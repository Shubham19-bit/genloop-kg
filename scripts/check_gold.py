"""Verify gold values (found with Gemini chat, see benchmark/GOLD_EXTRACTION_PROMPT.md) against
the filing's own inline-XBRL tags. Standard library only.

  python scripts/check_gold.py   -> results/gold_check.csv and a printed summary

Input: benchmark/gold_raw.jsonl, one JSON object per company as returned by Gemini.
Statuses:
  verified       number matches an XBRL tag for the same fiscal year (within 0.5%)
  mismatch       XBRL has a value and it differs -> read the PDF page yourself and fix it
  no_xbrl        no comparable XBRL tag (e.g. employees, text facts) -> check the page by hand
  not_reported   Gemini said the filing doesn't report it (null) -> confirm by looking
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from xbrl_check import TAGS, xbrl_for  # noqa: E402

KEYS = {"revenue": "Revenue", "operating_income": "OperatingIncome", "net_income": "NetIncome",
        "diluted_eps": "DilutedEPS", "research_and_development": "ResearchAndDevelopment",
        "total_assets": "TotalAssets", "total_liabilities": "TotalLiabilities",
        "total_equity": "TotalEquity", "cash_and_equivalents": "CashAndEquivalents",
        "long_term_debt": "LongTermDebt", "operating_cash_flow": "OperatingCashFlow"}
TEXT_KEYS = ["employees", "auditor", "headquarters", "ceo", "segments"]
SCALE = {"thousands": 1e3, "millions": 1e6, "billions": 1e9}


def to_number(entry: dict, per_share: bool) -> float | None:
    v = entry.get("value")
    if v in (None, "", "null"):
        return None
    s = str(v).replace(",", "").replace("$", "").strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = s.strip("()-")
    try:
        x = float(s)
    except ValueError:
        return None
    if not per_share:
        x *= SCALE.get(str(entry.get("scale") or "").lower().strip(), 1)
    return -x if neg else x


def main() -> None:
    meta = json.loads((ROOT / "data" / "filings.json").read_text())
    rows, summary = [], {}
    lines = (ROOT / "benchmark" / "gold_raw.jsonl").read_text(encoding="utf-8").splitlines()
    for ln in lines:
        ln = ln.strip().strip("`")
        if not ln or ln.startswith("json"):
            continue
        g = json.loads(ln)
        t = g["ticker"].upper()
        xv = xbrl_for(t, meta)
        for key, metric in KEYS.items():
            e = g.get(key) or {}
            val = to_number(e, metric == "DilutedEPS")
            cands = [(tag, v) for tag in TAGS[metric] for v in xv.get(tag, [])]
            if val is None:
                status = "not_reported"
            elif not cands:
                status = "no_xbrl"
            elif any(abs(val - v) <= max(0.006, 0.005 * abs(v)) for _, v in cands):
                status = "verified"
            else:
                status = "mismatch"
            summary[status] = summary.get(status, 0) + 1
            rows.append([t, metric, status, val, e.get("page"), e.get("quote"),
                         "; ".join(f"{tag}={v:g}" for tag, v in cands[:3])])
        for key in TEXT_KEYS:
            e = g.get(key) or {}
            status = "not_reported" if e.get("value") in (None, "", []) else "no_xbrl"
            summary[status] = summary.get(status, 0) + 1
            rows.append([t, key, status, e.get("value"), e.get("page"), e.get("quote"), ""])
    out = ROOT / "results" / "gold_check.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "fact", "status", "gold_value", "page", "quote", "xbrl"])
        w.writerows(rows)
    print(summary)
    for r in rows:
        if r[2] == "mismatch":
            print("MISMATCH", r[0], r[1], r[3], "| xbrl:", r[6], "| page", r[4])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
