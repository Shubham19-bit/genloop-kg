"""Independent check of extracted numbers against the filing's own inline-XBRL tags.

EDGAR 10-K HTML files are inline XBRL: every number in the financial statements is wrapped
in a tag such as <ix:nonFraction name="us-gaap:Assets" contextRef="c-5" scale="6">359,241</...>.
Those tags are a structured, official source that is independent of our PDF text extraction,
so we use them to (a) verify extracted facts and (b) verify benchmark gold answers.
They are NEVER used to answer questions.

Standard library only.
  python scripts/xbrl_check.py              -> results/xbrl_check.csv + summary
  python scripts/xbrl_check.py AAPL Revenue -> print the XBRL value(s) for one fact
"""
from __future__ import annotations

import csv
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, FACTS, RESULTS = ROOT / "data" / "raw_html", ROOT / "data" / "facts", ROOT / "results"

# Our metric -> candidate us-gaap tags, most specific first.
TAGS = {
    "Revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenuesNetOfInterestExpense", "SalesRevenueNet"],
    "OperatingIncome": ["OperatingIncomeLoss"],
    "NetIncome": ["NetIncomeLoss", "ProfitLoss"],
    "DilutedEPS": ["EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"],
    "ResearchAndDevelopment": ["ResearchAndDevelopmentExpense",
                               "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
    "TotalAssets": ["Assets"],
    "TotalLiabilities": ["Liabilities"],
    "TotalEquity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                    "StockholdersEquity"],
    "CashAndEquivalents": ["CashAndCashEquivalentsAtCarryingValue", "CashAndDueFromBanks"],
    "LongTermDebt": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
                     "UnsecuredLongTermDebt"],
    "OperatingCashFlow": ["NetCashProvidedByUsedInOperatingActivities",
                          "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
}
DURATION = {"Revenue", "OperatingIncome", "NetIncome", "DilutedEPS", "ResearchAndDevelopment",
            "OperatingCashFlow"}

CTX = re.compile(r"<xbrli:context\b[^>]*\bid=\"([^\"]+)\"(.*?)</xbrli:context>", re.S)
NONFRAC = re.compile(r"<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>", re.S)
ATTR = re.compile(r"(\w[\w:-]*)=\"([^\"]*)\"")


def contexts(doc: str) -> dict:
    out = {}
    for cid, body in CTX.findall(doc):
        if "<xbrldi:explicitMember" in body or "<xbrldi:typedMember" in body:
            continue                                   # dimensional (segment) value, skip
        inst = re.search(r"<xbrli:instant>([\d-]+)<", body)
        st = re.search(r"<xbrli:startDate>([\d-]+)<", body)
        en = re.search(r"<xbrli:endDate>([\d-]+)<", body)
        if inst:
            out[cid] = ("instant", None, inst.group(1))
        elif st and en:
            out[cid] = ("duration", st.group(1), en.group(1))
    return out


def values(doc: str, ctx: dict, report_date: str) -> dict[str, list[float]]:
    """us-gaap tag -> distinct values for the fiscal year ending report_date (no dimensions)."""
    end = date.fromisoformat(report_date)
    out: dict[str, set] = {}
    for attrs, inner in NONFRAC.findall(doc):
        a = dict(ATTR.findall(attrs))
        name = a.get("name", "")
        if not name.startswith("us-gaap:") or a.get("contextRef") not in ctx:
            continue
        kind, st, en = ctx[a["contextRef"]]
        if abs((date.fromisoformat(en) - end).days) > 7:
            continue
        if kind == "duration" and not (300 < (date.fromisoformat(en) - date.fromisoformat(st)).days < 400):
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", inner)).strip().replace(",", "")
        if text in ("", "-", "—") or "zerodash" in a.get("format", ""):
            v = 0.0
        else:
            try:
                v = float(text)
            except ValueError:
                continue
        v *= 10 ** int(a.get("scale", "0") or 0)
        if a.get("sign") == "-":
            v = -v
        out.setdefault(name[8:], set()).add(round(v, 4))
    return {k: sorted(v) for k, v in out.items()}


def xbrl_for(ticker: str, meta: dict) -> dict[str, list[float]]:
    # main 10-K plus any Exhibit 13 (Wells Fargo keeps its statements there)
    doc = "\n".join(f.read_text(encoding="utf-8", errors="ignore")
                    for f in sorted(RAW.glob(f"{ticker}_10K_FY2025*.htm")))
    return values(doc, contexts(doc), meta[ticker]["reportDate"])


def main() -> None:
    meta = json.loads((ROOT / "data" / "filings.json").read_text())
    if len(sys.argv) == 3:
        vals = xbrl_for(sys.argv[1], meta)
        for tag in TAGS[sys.argv[2]]:
            print(tag, vals.get(tag))
        return
    rows, summary = [], {"match": 0, "mismatch": 0, "no_xbrl": 0, "not_extracted": 0}
    for t in sorted(meta):
        vals = xbrl_for(t, meta)
        ours = {f["predicate"]: f for f in json.loads((FACTS / f"{t}.json").read_text())
                if f["kind"] == "metric"}
        for m, tags in TAGS.items():
            cands = [(tag, v) for tag in tags for v in vals.get(tag, [])]
            o = ours.get(m)
            if o is None:
                status = "not_extracted"
            elif not cands:
                status = "no_xbrl"
            elif any(abs(o["value"] - v) <= max(0.006, 0.005 * abs(v)) for _, v in cands):
                status = "match"
            else:
                status = "mismatch"
            summary[status] += 1
            rows.append([t, m, status, o and o["value"], o and o["page"],
                         "; ".join(f"{tag}={v:g}" for tag, v in cands[:4]),
                         o and str(o.get("evidence", ""))[:90]])
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "xbrl_check.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "metric", "status", "our_value", "page", "xbrl_values", "evidence"])
        w.writerows(rows)
    print(summary)
    for r in rows:
        if r[2] == "mismatch":
            print("MISMATCH", r[0], r[1], r[3], "| xbrl:", r[5], "| row:", r[6])


if __name__ == "__main__":
    main()
