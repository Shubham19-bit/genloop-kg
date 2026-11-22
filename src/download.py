"""Step 1: find each company's 10-K on EDGAR, download it, print it to PDF.

EDGAR serves 10-Ks as (inline-XBRL) HTML. The task asks us to work from PDFs, so we
print each HTML filing to PDF with headless Chrome (via Playwright). Everything
downstream reads ONLY the PDFs, and citations use the PDF page number.

Usage:  python -m src.cli download [--tickers AAPL MSFT]
"""
from __future__ import annotations

import json
import re
import time

import requests

from . import settings as S

HEADERS = {"User-Agent": S.SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"}


def _get(url: str) -> requests.Response:
    time.sleep(0.15)  # SEC fair-access limit is 10 requests/sec
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r


def ticker_to_cik() -> dict[str, int]:
    data = _get("https://www.sec.gov/files/company_tickers.json").json()
    return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}


def _iter_filings(cik: int):
    """Yield filing dicts from the submissions API, including older pages."""
    sub = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    blocks = [sub["filings"]["recent"]]
    for f in sub["filings"].get("files", []):
        blocks.append(_get(f"https://data.sec.gov/submissions/{f['name']}").json())
    for b in blocks:
        for i in range(len(b["form"])):
            yield {k: b[k][i] for k in ("form", "accessionNumber", "reportDate",
                                        "filingDate", "primaryDocument")}, sub


def find_10k(cik: int, start: str, end: str) -> dict | None:
    """The latest original 10-K (not 10-K/A) whose period of report ends in [start, end].

    A date window, not a fiscal-year label, because labels differ by company
    (Walmart calls the year ending Jan 2026 'fiscal 2026'; Apple's FY ends in September).
    """
    best = None
    for f, sub in _iter_filings(cik):
        if f["form"] == "10-K" and start <= f["reportDate"] <= end:
            if best is None or f["reportDate"] > best["reportDate"]:
                best = {**f, "cik": cik, "entityName": sub.get("name"),
                        "sicDescription": sub.get("sicDescription"),
                        "fiscalYearEnd": sub.get("fiscalYearEnd")}
    return best


def html_to_pdf(html_path, pdf_path) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            browser = p.chromium.launch(channel="chrome")  # fall back to installed Chrome
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=180_000)
        page.pdf(path=str(pdf_path), format="Letter", print_background=False,
                 margin={"top": "0.5in", "bottom": "0.5in", "left": "0.5in", "right": "0.5in"})
        browser.close()


def exhibit_13_urls(cik: int, accession: str) -> list[str]:
    """Some banks (Wells Fargo) put the financial statements in Exhibit 13, the Annual Report,
    and file a short 10-K that only points to it. Find EX-13 documents in the filing index."""
    acc = accession.replace("-", "")
    idx = _get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{accession}-index.htm").text
    urls = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", idx, re.S | re.I):
        if re.search(r">\s*EX-13(\.\d+)?\s*<", row):
            m = re.search(r'href="([^"]+\.htm)"', row, re.I)
            if m:
                href = m.group(1).replace("/ix?doc=", "")
                urls.append(href if href.startswith("http") else "https://www.sec.gov" + href)
    return urls


def merge_pdfs(paths, out_path) -> None:
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    out = pymupdf.open()
    for pth in paths:
        with pymupdf.open(pth) as d:
            out.insert_pdf(d)
    out.save(str(out_path))
    out.close()


def run(tickers: list[str] | None = None, force: bool = False) -> None:
    cfg = S.load_companies()
    fy = cfg["target_fiscal_year"]                      # label only, e.g. FY2025
    start, end = cfg["period_start"], cfg["period_end"]
    companies = [c for c in cfg["companies"] if not tickers or c["ticker"] in tickers]
    meta = json.loads(S.META.read_text()) if S.META.exists() else {}
    ciks = ticker_to_cik()

    for c in companies:
        t = c["ticker"]
        pdf_path = S.PDFS / f"{t}_10K_FY{fy}.pdf"
        if pdf_path.exists() and t in meta and not force:
            print(f"[skip] {t} already downloaded")
            continue
        cik = ciks.get(t)
        if cik is None:
            print(f"[warn] {t}: ticker not found in SEC list")
            continue
        f = find_10k(cik, start, end)
        if f is None:
            print(f"[warn] {t}: no 10-K with period ending {start}..{end}")
            continue
        acc = f["accessionNumber"].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{f['primaryDocument']}"
        html_path = S.RAW_HTML / f"{t}_10K_FY{fy}.htm"
        html_path.write_bytes(_get(url).content)
        print(f"[ok] {t}: downloaded {url}")
        html_to_pdf(html_path, pdf_path)
        print(f"[ok] {t}: printed {pdf_path.name}")
        exhibits = exhibit_13_urls(cik, f["accessionNumber"]) if c.get("exhibit_13") else []
        for i, ex_url in enumerate(exhibits, start=1):
            ex_html = S.RAW_HTML / f"{t}_10K_FY{fy}_ex13_{i}.htm"
            ex_html.write_bytes(_get(ex_url).content)
            ex_pdf = S.PDFS / f"_{t}_ex13_{i}.pdf"
            html_to_pdf(ex_html, ex_pdf)
            merge_pdfs([pdf_path, ex_pdf], pdf_path.with_suffix(".merged.pdf"))
            pdf_path.with_suffix(".merged.pdf").replace(pdf_path)
            ex_pdf.unlink()
            print(f"[ok] {t}: appended Exhibit 13 ({ex_url}) to {pdf_path.name}")
        meta[t] = {**f, "url": url, "exhibit_13": exhibits, "pdf": pdf_path.name, "fiscal_year": fy,
                   "name": c["name"], "sector": c["sector"]}
        S.META.write_text(json.dumps(meta, indent=2))
