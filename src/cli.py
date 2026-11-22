"""Single entry point.

  python -m src.cli download   [--tickers AAPL MSFT]   # EDGAR -> HTML -> PDF
  python -m src.cli parse      [--tickers ...]         # PDF -> pages
  python -m src.cli extract    [--tickers ...]         # numeric + auditor facts (regex)
  python -m src.cli extract-llm [--tickers ...]        # HQ, CEO, segments (LLM + quote check)
  python -m src.cli build                              # facts -> graph.pkl
  python -m src.cli ask "Which bank had the most total assets?"
  python -m src.cli eval [--limit 5]
  python -m src.cli debug JPM                          # why were metrics missing for a company?
  python -m src.cli all                                # download..build in one go
"""
from __future__ import annotations

import argparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download", "parse", "extract", "extract-llm", "build",
                                    "ask", "eval", "debug", "all"])
    ap.add_argument("question", nargs="?")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="download again even if the PDF exists")
    a = ap.parse_args()

    if a.cmd in ("download", "all"):
        from . import download; download.run(a.tickers, a.force)
    if a.cmd in ("parse", "all"):
        from . import parse_pdf; parse_pdf.run(a.tickers)
    if a.cmd in ("extract", "all"):
        from . import extract_numeric; extract_numeric.run(a.tickers)
    if a.cmd in ("extract-llm", "all"):
        from . import extract_llm; extract_llm.run(a.tickers)
    if a.cmd in ("build", "all"):
        from . import graph_build; graph_build.run()
    if a.cmd == "ask":
        from .ask import answer, pretty
        print(pretty(answer(a.question)))
    if a.cmd == "debug":
        from . import debug; debug.run(a.question)
    if a.cmd == "eval":
        from . import evaluate; evaluate.run(a.limit)


if __name__ == "__main__":
    main()
