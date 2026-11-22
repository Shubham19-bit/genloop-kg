"""Why were some metrics missing for a company?   python -m src.cli debug JPM

Prints (1) which pages were recognised as the income / balance / cash-flow statement and
(2) for every metric that has no fact, the table rows anywhere in the filing whose label
contains a keyword, with the PDF page. Read those rows to see how the filing really words it,
then add that wording to config/metrics.yaml (labels are regexes on the normalised label).
"""
from __future__ import annotations

import json

from . import settings as S
from .extract_numeric import classify, normalise_label, split_row
from .parse_pdf import load_pages

HINTS = {
    "Revenue": ["revenue", "net sales"],
    "OperatingIncome": ["operating income", "income from operations", "operating profit"],
    "NetIncome": ["net income", "net earnings"],
    "DilutedEPS": ["diluted"],
    "ResearchAndDevelopment": ["research and development", "technology and development"],
    "TotalAssets": ["total assets"],
    "TotalLiabilities": ["total liabilities"],
    "TotalEquity": ["total stockholders", "total shareholders", "total equity", "total shareowners"],
    "CashAndEquivalents": ["cash and"],
    "LongTermDebt": ["long-term debt", "term debt", "long-term borrowings"],
    "OperatingCashFlow": ["operating activities"],
}


def run(ticker: str | None) -> None:
    if not ticker:
        raise SystemExit("usage: python -m src.cli debug TICKER")
    t = ticker.upper()
    pages = load_pages(t)
    print(f"{t}: {len(pages)} pages\n")

    print("Pages recognised as primary statements (page: number-density):")
    for kind in ("income", "balance", "cashflow"):
        hits = [(p["page"], c[kind]) for p in pages for c in [classify(p)] if kind in c]
        print(f"  {kind:<9} {hits[:12] or 'NONE FOUND'}")

    got = {f["predicate"] for f in json.loads((S.FACTS / f"{t}.json").read_text())}
    missing = [m for m in HINTS if m not in got]
    print(f"\nMissing metrics: {missing or 'none'}")
    for m in missing:
        print(f"\n--- {m}: rows containing {HINTS[m]} (page: label | numbers)")
        shown = 0
        for p in pages:
            for line in p["lines"]:
                label, vals = split_row(line)
                nl = normalise_label(label)
                if vals and any(h in nl for h in HINTS[m]):
                    print(f"  p{p['page']}: {nl[:70]} | {vals[:3]}")
                    shown += 1
                    if shown >= 8:
                        break
            if shown >= 8:
                break
        if shown == 0:
            print("  (no matching rows anywhere)")
    if "Employees" not in got:
        print("\n--- Employees: lines containing 'employees' with a number")
        n = 0
        for p in pages[: max(10, int(len(pages) * 0.4))]:
            for line in p["lines"]:
                if "employees" in line.lower() and any(ch.isdigit() for ch in line):
                    print(f"  p{p['page']}: {line[:110]}")
                    n += 1
            if n >= 6:
                break
