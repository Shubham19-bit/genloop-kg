"""Offline end-to-end test on two FAKE mini filings (no network, no LLM).

HTML -> PDF (headless Chrome) -> page rows -> regex facts -> graph -> hand-written plans.
Run:  python -m pytest -q   (or: python tests/test_pipeline.py)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAKE = """<html><body style="font-family:Arial;font-size:11px">
<h3>{name}</h3><p>We had approximately {emp} full-time employees as of year end.</p>
<div style="page-break-after:always"></div>
<h3>CONSOLIDATED STATEMENTS OF OPERATIONS</h3><p>(In millions, except per-share amounts)</p>
<table>{inc}</table>
<div style="page-break-after:always"></div>
<h3>CONSOLIDATED BALANCE SHEETS</h3><p>(In millions)</p><table>{bal}</table>
<div style="page-break-after:always"></div>
<h3>Report of Independent Registered Public Accounting Firm</h3>
<p>We have audited the consolidated balance sheets...</p><p>/s/ {aud}</p>
</body></html>"""


def _table(rows, years=("2025", "2024", "2023")):
    head = "<tr><td></td>" + "".join(f"<td>{y}</td>" for y in years) + "</tr>"
    pad = "".join(f"<tr><td>Other line {i}</td>" + "<td>1,000</td>" * len(years) + "</tr>"
                  for i in range(10))                      # make the page number-dense
    body = "".join("<tr><td>" + r[0] + "</td>" + "".join(f"<td>{v}</td>" for v in r[1:]) + "</tr>"
                   for r in rows)
    return head + body + pad


def make_fake(tmp: Path, t: str, name: str, rev: str, opi: str, ni: str, ta: str, tl: str, te: str,
              aud: str, emp: str):
    inc = _table([("Total net sales", f"$ {rev}", "$ 90,000", "$ 80,000"),
                  ("Operating income", opi, "20,000", "18,000"),
                  ("Net income", f"$ {ni}", "$ 15,000", "$ 14,000"),
                  ("Diluted", "6.11", "5.50", "5.00"),
                  ("Diluted", "15,400", "15,800", "16,100")])
    bal = _table([("Total assets", f"$ {ta}", "$ 300,000"),
                  ("Total liabilities", tl, "200,000"),
                  ("Total shareholders’ equity", te, "100,000")], years=("2025", "2024"))
    html = tmp / f"{t}.htm"
    html.write_text(FAKE.format(name=name, emp=emp, inc=inc, bal=bal, aud=aud), encoding="utf-8")
    return html


def test_end_to_end():
    from src import settings as S
    tmp = Path(tempfile.mkdtemp())
    # redirect all data paths into a temp dir
    for attr in ("PDFS", "PAGES", "FACTS"):
        p = tmp / attr.lower(); p.mkdir(); setattr(S, attr, p)
    S.META = tmp / "filings.json"
    from src import download, extract_numeric, graph_build, parse_pdf
    for mod in (parse_pdf, extract_numeric, graph_build):
        mod.S = S

    meta = {}
    specs = [("AAPL", "Apple Inc.", "100,000", "30,000", "25,000", "350,000", "250,000", "100,000",
              "Ernst & Young LLP", "164,000"),
             ("MSFT", "Microsoft Corporation", "200,000", "90,000", "70,000", "500,000", "240,000",
              "260,000", "Deloitte & Touche LLP", "228,000")]
    for t, name, *vals in specs:
        html = make_fake(tmp, t, name, *vals)
        pdf = S.PDFS / f"{t}_10K_FY2025.pdf"
        download.html_to_pdf(html, pdf)
        meta[t] = {"pdf": pdf.name, "fiscal_year": 2025, "reportDate": "2025-12-31",
                   "url": "file://fake", "filingDate": "2026-02-01", "name": name}
    S.META.write_text(json.dumps(meta))
    parse_pdf.run()
    extract_numeric.run()

    facts = {f["predicate"]: f for f in json.loads((S.FACTS / "AAPL.json").read_text())}
    assert facts["Revenue"]["value"] == 100_000e6, facts["Revenue"]
    assert facts["OperatingIncome"]["value"] == 30_000e6
    assert facts["NetIncome"]["value"] == 25_000e6
    assert facts["DilutedEPS"]["value"] == 6.11            # not the share-count 'Diluted' row
    assert facts["TotalEquity"]["value"] == 100_000e6
    assert facts["Employees"]["value"] == 164_000
    assert facts["AUDITED_BY"]["value"] == "Ernst & Young LLP"
    assert facts["Revenue"]["page"] == 2

    # graph + deterministic plans (what the LLM planner would produce)
    G = graph_build.build()
    from src.query.executor import Executor
    ex = Executor(G)
    r = ex.run({"steps": [{"id": "s1", "op": "metric", "input": ["Apple"], "metric": "Revenue"}]})
    assert r["value"] == 100_000e6 and r["facts"] == ["AAPL|Revenue|FY2025"]
    assert r["citations"] == [{"doc": "AAPL_10K_FY2025.pdf", "page": 2}]

    r = ex.run({"steps": [{"id": "s1", "op": "ratio", "input": ["AAPL", "MSFT"], "metric": "OperatingMargin"},
                          {"id": "s2", "op": "rank", "input": "s1", "order": "desc", "k": 1}]})
    assert r["entities"] == ["MSFT"] and abs(r["value"] - 0.45) < 1e-9
    assert set(r["facts"]) == {"AAPL|OperatingIncome|FY2025", "AAPL|Revenue|FY2025",
                               "MSFT|OperatingIncome|FY2025", "MSFT|Revenue|FY2025"}

    r = ex.run({"steps": [{"id": "s1", "op": "companies", "sector": "Technology"},
                          {"id": "s2", "op": "metric", "input": "s1", "metric": "TotalAssets"},
                          {"id": "s3", "op": "rank", "input": "s2", "order": "desc", "k": 1},
                          {"id": "s4", "op": "attribute", "input": "s3", "relation": "AUDITED_BY"}]})
    assert r["entities"] == ["Deloitte & Touche LLP"], r
    assert "MSFT|AUDITED_BY" in r["facts"] and "AAPL|TotalAssets|FY2025" in r["facts"]

    r = ex.run({"steps": [{"id": "s1", "op": "companies", "where": {"relation": "AUDITED_BY", "value": "EY"}}]})
    assert r["entities"] == ["AAPL"], r     # short auditor name resolves via alias map
    shutil.rmtree(tmp)
    print("end-to-end test passed")


if __name__ == "__main__":
    test_end_to_end()
