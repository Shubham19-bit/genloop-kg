"""Step 2: PDF -> per-page text and reconstructed table rows.

Printed HTML tables often come out of plain text extraction with one cell per line,
which breaks "label  value  value" parsing. So we rebuild rows from word positions:
words whose vertical centres are within a small tolerance form one line, sorted
left-to-right. Output: data/pages/<TICKER>.jsonl, one record per PDF page (1-based).

Uses PyMuPDF (fast) if installed, else pdfplumber.
"""
from __future__ import annotations

import json

from . import settings as S


def _words_pymupdf(path):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf  # older PyMuPDF
    with pymupdf.open(path) as doc:
        for page in doc:
            # (x0, y0, x1, y1, word, block, line, word_no)
            yield [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]


def _words_pdfplumber(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            ws = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            yield [(w["x0"], w["top"], w["x1"], w["bottom"], w["text"]) for w in ws]


def words_by_page(path):
    try:
        try:
            import pymupdf  # noqa: F401
        except ImportError:
            import fitz  # noqa: F401
        return _words_pymupdf(path)
    except ImportError:
        return _words_pdfplumber(path)


def rows_from_words(words, y_tol: float = 3.0) -> list[str]:
    """Group words into visual rows by vertical centre, then join left-to-right."""
    items = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows, current, cy = [], [], None
    for w in items:
        y = (w[1] + w[3]) / 2
        if cy is None or abs(y - cy) <= y_tol:
            current.append(w)
            cy = y if cy is None else (cy + y) / 2
        else:
            rows.append(current)
            current, cy = [w], y
    if current:
        rows.append(current)
    return [" ".join(w[4] for w in sorted(r, key=lambda w: w[0])) for r in rows]


def parse_one(pdf_path) -> list[dict]:
    pages = []
    for i, words in enumerate(words_by_page(pdf_path), start=1):
        lines = rows_from_words(words)
        pages.append({"page": i, "lines": lines, "text": "\n".join(lines)})
    return pages


def run(tickers: list[str] | None = None) -> None:
    meta = json.loads(S.META.read_text())
    for t, m in meta.items():
        if tickers and t not in tickers:
            continue
        out = S.PAGES / f"{t}.jsonl"
        pages = parse_one(S.PDFS / m["pdf"])
        with out.open("w", encoding="utf-8") as f:
            for p in pages:
                f.write(json.dumps(p) + "\n")
        print(f"[ok] {t}: {len(pages)} pages -> {out.name}")


def load_pages(ticker: str) -> list[dict]:
    with (S.PAGES / f"{ticker}.jsonl").open(encoding="utf-8") as f:
        return [json.loads(l) for l in f]
