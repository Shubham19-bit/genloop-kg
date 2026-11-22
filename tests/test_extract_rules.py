"""Offline unit tests for the extraction rules, using small synthetic pages that copy the
real-world problems found on the first run (Apple, JPMorgan, Walmart)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import settings as S                                   # noqa: E402
from src.extract_numeric import (classify, compile_metrics, extract_employees,   # noqa: E402
                                 extract_statement_metrics)

META = {"ticker": "TST", "fiscal_year": 2025, "reportDate": "2026-01-31", "pdf": "TST.pdf"}


def page(n, lines):
    return {"page": n, "lines": lines, "text": "\n".join(lines)}


PAD = [f"Other line {i} 1,000 1,100 1,200" for i in range(12)]


def test_walmart_like_statements_and_derived_liabilities():
    income = page(20, ["Walmart Inc.", "Consolidated Statements of Income",
                       "(Amounts in millions, except per share data)",
                       "Fiscal Years Ended January 31, 2026 2025 2024",
                       "Total revenues 700,000 650,000 600,000",
                       "Operating income 30,000 29,000 27,000",
                       "Consolidated net income 20,000 19,000 18,000",
                       "Consolidated net income attributable to noncontrolling interest (500) (400) (300)",
                       "Consolidated net income attributable to Walmart 19,500 18,600 17,700",
                       "Diluted net income per common share attributable to Walmart $ 2.41 2.30 2.20",
                       "Weighted-average common shares outstanding: Diluted 8,100 8,200 8,300"] + PAD)
    mdna = page(10, ["Consolidated balance sheets analysis", "Total assets 999,999 888,888",
                     "Long-term debt 1 2"] + PAD)           # must NOT be treated as the statement
    bal = page(21, ["Consolidated Balance Sheets", "(Amounts in millions)",
                    "As of January 31, 2026 2025",
                    "Cash and cash equivalents 9,000 8,000",
                    "Total assets 300,000 290,000",
                    "Long-term debt due within one year 3,000 2,500",
                    "Long-term debt 40,000 41,000",
                    "Total Walmart shareholders' equity 100,000 95,000",
                    "Total equity 105,000 100,000"] + PAD)
    facts = {f["predicate"]: f for f in
             extract_statement_metrics([mdna, income, bal], META, compile_metrics(S.load_metrics()))}
    assert classify(mdna) == {}                                  # analysis page is not a statement
    assert facts["Revenue"]["value"] == 700_000e6
    assert facts["NetIncome"]["value"] == 19_500e6               # attributable to Walmart, not NCI/consolidated
    assert facts["DilutedEPS"]["value"] == 2.41
    assert facts["TotalAssets"]["value"] == 300_000e6 and facts["TotalAssets"]["page"] == 21
    assert facts["TotalEquity"]["value"] == 105_000e6            # prefer last: total equity incl. NCI
    assert facts["LongTermDebt"]["value"] == 40_000e6
    assert facts["TotalLiabilities"]["value"] == 195_000e6       # derived: no such line in the filing
    assert facts["TotalLiabilities"]["method"].startswith("derived")


def test_apple_term_debt_appears_twice_and_title_with_company_prefix():
    bal = page(43, ["Apple Inc.", "CONSOLIDATED BALANCE SHEETS", "(In millions)",
                    "September 27, 2025 September 28, 2024",
                    "Total assets $ 359,241 $ 364,980",
                    "Term debt 12,380 10,912",
                    "Total current liabilities 165,631 176,392",
                    "Term debt 78,328 85,750",
                    "Total liabilities 285,508 308,030",
                    "Total shareholders' equity 73,733 56,950",
                    "Total liabilities and shareholders' equity $ 359,241 $ 364,980"] + PAD)
    facts = {f["predicate"]: f for f in
             extract_statement_metrics([bal], META, compile_metrics(S.load_metrics()))}
    assert facts["LongTermDebt"]["value"] == 78_328e6            # the non-current row, not 12,380
    assert facts["TotalLiabilities"]["value"] == 285_508e6
    assert not facts["TotalLiabilities"]["method"].startswith("derived")
    assert facts["TotalEquity"]["value"] == 73_733e6


def test_employees_across_line_break_and_full_time_equivalent():
    p = page(8, ["culture of collaboration.",
                 "As of September 27, 2025, the Company had approximately 166,000 full-",
                 "time equivalent employees.", "The Company believes that compensation"])
    f = extract_employees([p], META)
    assert f and f[0]["value"] == 166_000


if __name__ == "__main__":
    test_walmart_like_statements_and_derived_liabilities()
    test_apple_term_debt_appears_twice_and_title_with_company_prefix()
    test_employees_across_line_break_and_full_time_equivalent()
    print("extraction rule tests passed")
