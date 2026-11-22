"""Step 3b: LLM extraction of qualitative facts (HQ, incorporation, CEO, segments).

Design choices that matter for a bank-grade answer:
  * The LLM sees only a few candidate PAGES (picked by keywords), never the whole filing.
  * It must return an exact quote and the page number for every fact.
  * We VERIFY the quote is really on that page; facts that fail are dropped and logged.
    This is our guard against hallucinated facts entering the graph.

Output: data/facts/<TICKER>_llm.json   (+ results/llm_rejections.json)
"""
from __future__ import annotations

import json
import re

from . import settings as S
from .llm import complete_json
from .parse_pdf import load_pages

SYSTEM = """You extract facts from pages of a company's SEC 10-K filing.
Return ONLY JSON: {"facts": [{"predicate": str, "value": str | [str], "page": int, "quote": str}]}
Rules:
- Use only the pages given. Never use outside knowledge.
- "quote" must be copied EXACTLY from the page text (5-25 words) and must contain the value.
- "page" is the number shown in the [PAGE n] marker where the quote appears.
- If a fact is not on the pages, leave it out. Do not guess."""

TASKS = {
    "cover": ("HEADQUARTERED_IN: city and state/country of the principal executive offices, "
              "formatted 'City, State' (e.g. 'Cupertino, California').\n"
              "INCORPORATED_IN: state or country of incorporation (e.g. 'Delaware')."),
    "signatures": "CEO: full name of the person signing as Chief Executive Officer (or principal executive officer).",
    "segments": ("HAS_SEGMENT: list of the company's REPORTABLE segment names as the filing names them "
                 "(value is a JSON list of strings)."),
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


SEG_PAT = re.compile(r"reportable (business |operating )?segments?|(business|operating) segments|segment results|"
                     r"(one|single|a single) (reportable |operating |business )?segment|reportable segments? (is|are|consist)",
                     re.I)


def candidate_pages(pages: list[dict]) -> dict[str, list[dict]]:
    # Signatures: anywhere in the filing (Wells Fargo puts them on p36), a page with a SIGNATURES
    # heading plus an executive-officer signer; prefer the last such pages.
    sig = [p for p in pages
           if re.search(r"(?m)^\s*signatures?\s*$", p["text"], re.I)
           and re.search(r"(chief|principal) executive officer", p["text"], re.I)]
    if not sig:
        sig = [p for p in pages if "/s/" in p["text"].lower()
               and re.search(r"(chief|principal) executive officer", p["text"], re.I)]
    seg = sorted(pages, key=lambda p: -len(SEG_PAT.findall(p["text"])))
    return {"cover": pages[:3], "signatures": sig[-2:],
            "segments": [p for p in seg[:2] if SEG_PAT.search(p["text"])]}


def _prompt(task: str, pages: list[dict], name: str) -> str:
    body = "\n\n".join(f"[PAGE {p['page']}]\n{p['text'][:6000]}" for p in pages)
    return f"Company: {name}\nExtract:\n{TASKS[task]}\n\nPages:\n{body}"


PRED_ALIASES = {"CHIEF_EXECUTIVE_OFFICER": "CEO", "CEO": "CEO", "HEADQUARTERS": "HEADQUARTERED_IN",
                "HEADQUARTERED_IN": "HEADQUARTERED_IN", "INCORPORATED_IN": "INCORPORATED_IN",
                "HAS_SEGMENT": "HAS_SEGMENT", "SEGMENTS": "HAS_SEGMENT", "REPORTABLE_SEGMENTS": "HAS_SEGMENT"}


def verify(fact: dict, pages_by_no: dict[int, dict]) -> str | None:
    """Return None if OK, else a rejection reason."""
    page = pages_by_no.get(fact.get("page"))
    if page is None:
        return "page not among the pages given"
    quote, text = _norm(fact.get("quote", "")), _norm(page["text"])
    if not quote:
        return "no quote"
    if quote not in text:
        # Cover pages are tables, so the model may quote cells in a different order than the
        # text layer. Accept only if EVERY word of the quote is on the page (still no invented words).
        words = set(text.split())
        if not all(w in words for w in quote.split()):
            return "quote not found on page"
    vals = fact["value"] if isinstance(fact["value"], list) else [fact["value"]]
    for v in vals:
        first = _norm(str(v)).split(" ")[0] if v else ""
        if first and first not in _norm(page["text"]):
            return f"value '{v}' not found on page"
    return None


def run(tickers: list[str] | None = None) -> None:
    meta_all = json.loads(S.META.read_text())
    rejections = []
    for t, meta in meta_all.items():
        if tickers and t not in tickers:
            continue
        pages = load_pages(t)
        by_no = {p["page"]: p for p in pages}
        facts = []
        for task, cands in candidate_pages(pages).items():
            if not cands:
                rejections.append({"ticker": t, "task": task, "reason": "no candidate pages"})
                continue
            try:
                out = complete_json(SYSTEM, _prompt(task, cands, meta["name"]))
            except Exception as e:  # noqa: BLE001 - keep going; re-running retries only the failures
                rejections.append({"ticker": t, "task": task, "reason": f"llm error: {e}"})
                print(f"   [warn] {t} {task}: {e}")
                continue
            for f in out.get("facts", []):
                reason = verify(f, by_no)
                if reason:
                    rejections.append({"ticker": t, "task": task, "fact": f, "reason": reason})
                    continue
                facts.append({"ticker": t, "kind": "attribute", "predicate": PRED_ALIASES.get(
                                  str(f["predicate"]).strip().upper().replace(" ", "_"), f["predicate"]),
                              "value": f["value"], "unit": None, "period": None,
                              "doc": meta["pdf"], "page": f["page"], "evidence": f["quote"],
                              "method": "llm+quote_check"})
        (S.FACTS / f"{t}_llm.json").write_text(json.dumps(facts, indent=1))
        print(f"[ok] {t}: {len(facts)} LLM facts")
    (S.RESULTS / "llm_rejections.json").write_text(json.dumps(rejections, indent=1))
    print(f"{len(rejections)} rejected facts/tasks logged to results/llm_rejections.json")
