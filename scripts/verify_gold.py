"""Cross-check a golden answer against SEC's structured XBRL data (independent of our PDF pipeline).

  python scripts/verify_gold.py AAPL Revenue
  python scripts/verify_gold.py --all            # every ticker x metric -> results/xbrl_check.csv

Reads the company's XBRL "companyfacts" and keeps only values reported in the SAME
10-K accession we downloaded, taking the one for the latest period end. Use this to
verify each number you put in benchmark/benchmark.jsonl (you still read the PDF page
yourself to record the page number).
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import settings as S          # noqa: E402
from src.download import _get           # noqa: E402

UNITS = {"USD": "USD", "USD_per_share": "USD/shares", "count": "pure"}


def xbrl_value(ticker: str, metric: str):
    meta = json.loads(S.META.read_text())[ticker]
    m = S.load_metrics()["metrics"][metric]
    facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{meta['cik']:010d}.json").json()
    gaap = facts["facts"].get("us-gaap", {})
    for tag in m.get("xbrl", []):
        entries = gaap.get(tag, {}).get("units", {}).get(UNITS[m["unit"]], [])
        entries = [e for e in entries if e.get("accn") == meta["accessionNumber"]]
        if m["statement"] in ("income", "cashflow"):   # annual duration facts only
            entries = [e for e in entries if "start" in e and
                       300 < (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days < 400]
        if entries:
            best = max(entries, key=lambda e: e["end"])
            return {"tag": tag, "value": best["val"], "end": best["end"], "accn": best["accn"]}
    return None


def main():
    if sys.argv[1:] == ["--all"]:
        meta = json.loads(S.META.read_text())
        mets = [k for k, v in S.load_metrics()["metrics"].items() if v.get("xbrl")]
        ours = {}
        for p in S.FACTS.glob("*.json"):
            for f in json.loads(p.read_text()):
                if f["kind"] == "metric":
                    ours[(f["ticker"], f["predicate"])] = f["value"]
        out = S.RESULTS / "xbrl_check.csv"
        with out.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "metric", "xbrl_tag", "xbrl_value", "our_value", "rel_diff"])
            for t in meta:
                for mt in mets:
                    x = xbrl_value(t, mt)
                    o = ours.get((t, mt))
                    diff = (abs(o - x["value"]) / abs(x["value"])
                            if x and o is not None and x["value"] else "")
                    w.writerow([t, mt, x and x["tag"], x and x["value"], o, diff])
                    print(t, mt, x and x["value"], o, diff)
        print(f"wrote {out}")
    else:
        print(xbrl_value(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
