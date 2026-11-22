"""Step 3a: deterministic extraction of numeric facts (+ auditor) from the parsed pages.

No LLM here: financial statement rows are parsed with regex, which is exact and
auditable. Each fact keeps the PDF page and the row text it came from.

  1. classify pages as income / balance / cashflow statements (heading + number density)
  2. find the column of the current fiscal year from the header row ("2025 2024 2023")
  3. match each row's normalised label against the metric ontology (config/metrics.yaml)
  4. scale by the page's "(in millions)" note; per-share values are never scaled

Output: data/facts/<TICKER>.json
"""
from __future__ import annotations

import json
import re

from . import settings as S
from .parse_pdf import load_pages

# A page is a primary statement only if one of its first lines IS the statement title
# (e.g. "Consolidated Balance Sheets"), not merely a line that mentions those words.
# This avoids MD&A pages such as "Consolidated balance sheets analysis" (common in banks).
TITLE_PATTERNS = {
    "income": r"(income statements?|statements? of (consolidated )?(operations|income|earnings)"
              r"( and comprehensive (income|loss|\(loss\) income|income \(loss\)))?)",
    "balance": r"(balance sheets?|statements? of (consolidated )?(financial position|financial condition|condition))",
    "cashflow": r"(cash flows? statements?|statements? of (consolidated )?cash flows?)",
}
NUM_TOKEN = re.compile(r"^\(?-?\$?[\d,]*\d(\.\d+)?\)?%?$")
DASH_TOKEN = re.compile(r"^[—–-]+$")
YEAR = re.compile(r"\b(20\d\d)\b")


# ----------------------------------------------------------------- helpers
def normalise_label(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("$", " ")
    s = re.sub(r"\s*[–—]+\s*|\s+-{1,2}\s+|-{2,}", " - ", s)  # 'share––diluted' -> 'share - diluted'
    s = re.sub(r"\((see )?notes?\b[^)]*\)?", " ", s)           # '(note 7)' and unclosed '(notes 1 and'
    s = re.sub(r"\((deficit|investment)\)", " ", s)
    s = re.sub(r"\((includ\w*|excluding|net of allowance)[^)]*\)?", " ", s)  # '(included $134,559 at fair value)'
    s = re.sub(r"\((\w)\),", " ", s)                                       # Pfizer 'Cost of sales (a), (b)'

    s = re.sub(r"\(\d\)|\[\d\]|\([a-z]\)|\*+", " ", s)   # footnote markers (1) [1] (a) *
    s = re.sub(r"\s+", " ", s).strip(" :.,")
    return s


def parse_number(tok: str) -> float | None:
    if DASH_TOKEN.match(tok):
        return 0.0
    neg = tok.startswith("(") and tok.endswith(")")
    t = tok.strip("()$%").replace(",", "")
    if t in ("", "-"):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def split_row(line: str) -> tuple[str, list[float]]:
    """'Total net sales $ 391,035 $ 383,285' -> ('Total net sales', [391035, 383285])."""
    line = re.sub(r"\(?-?[\d,]*\.?\d+\)?\s*%", " ", line)          # Lowe's '% of sales' columns
    toks = [t for t in line.split() if t != "$" and not re.fullmatch(r"\([1-9]\)", t)]   # footnote '(4)' 
    vals = []
    while toks and (NUM_TOKEN.match(toks[-1]) or DASH_TOKEN.match(toks[-1])):
        v = parse_number(toks.pop())
        if v is None:
            break
        vals.append(v)
    return " ".join(toks), list(reversed(vals))


def page_scale(text: str) -> tuple[float, str]:
    # 'in millions', '$ in millions', '(Millions, except per share)', '(Dollars in millions)'
    m = re.search(r"(?:\bin\s+|\(\s*(?:\$\s*)?(?:dollars\s+|amounts\s+)?(?:in\s+)?)(millions|thousands|billions)\b",
                  text, re.I)
    if not m:
        return 1.0, "units"
    word = m.group(1).lower()
    return {"thousands": 1e3, "millions": 1e6, "billions": 1e9}[word], word


def year_columns(lines: list[str]) -> list[int]:
    """Years of the value columns, from a header line such as '2025 2024 2023' or
    'September 27, 2025 September 28, 2024'. Prose lines that mention years are skipped
    (Merck's statement page starts with a sentence citing 2025, 2024 and 2026)."""
    for ln in lines[:40]:
        ys = [int(y) for y in YEAR.findall(ln)]
        words = [w for w in re.findall(r"[A-Za-z]+", ln) if w.lower() not in MONTHS]
        if len(ys) >= 2 and len(set(ys)) == len(ys) and len(words) <= 8:
            return ys
    return []


MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december", "year", "years", "ended", "fiscal", "and", "as", "of",
          "at", "in", "millions", "dec", "jan", "sept", "sep"}


_TITLE_SUFFIX = r"(\s+(image\d+\.jpg|[a-z&.,' ]*?(inc\.?|corporation|company|co\.?)( and subsidiaries)?))?"


def _title_kind(text: str) -> str | None:
    """Which statement this line is the TITLE of, if any. Allowed around the title:
    a leading '<Company> and subsidiaries' (J&J), a trailing company name (Citi) or a stray
    image file name (Morgan Stanley). Prose such as '...our Consolidated Statements of Cash
    Flows.' or 'Selected income statement' does not count."""
    t = re.sub(r"\(continued\)", "", normalise_label(text)).strip(" :.,-")
    t = re.sub(r"^.*?\band subsidiaries\s+", "", t)
    for kind, pat in TITLE_PATTERNS.items():
        if re.fullmatch(r"(consolidated )?" + pat + _TITLE_SUFFIX, t):
            return kind
    return None


def is_title(line: str, pattern: str) -> bool:
    k = _title_kind(line)
    return k is not None and TITLE_PATTERNS[k] == pattern


def _nnum(page: dict) -> int:
    return sum(1 for ln in page["lines"] for t in ln.split() if NUM_TOKEN.match(t))


def title_kinds(page: dict) -> set[str]:
    head = page["lines"][:20]
    # also try each line joined with the next: J&J splits "consolidated statements of" / "cash flows"
    texts = head + [a + " " + b for a, b in zip(head, head[1:]) if normalise_label(a).endswith(" of")]
    kinds = {k for k in map(_title_kind, texts) if k}
    return set() if len(kinds) > 1 else kinds   # several statement titles on one page = an index page


def classify(page: dict) -> dict[str, float]:
    n = _nnum(page)
    if n < 25:                         # table-of-contents / index pages carry few numbers
        return {}
    return {k: n for k in title_kinds(page)}


def _other_statement(page: dict) -> bool:
    """Page starts another statement (equity, comprehensive income...), so it isn't a continuation."""
    return any(re.search(r"(consolidated )?statements? of (changes in )?(.*equity|comprehensive|shareholders|stockholders)",
                         normalise_label(l)) for l in page["lines"][:8])


def statement_pages(pages: list[dict]) -> dict[str, list[tuple[int, list[dict]]]]:
    """kind -> [(score, [title page, continuation page?])]. A statement often starts near the
    bottom of a page (title + a few rows) and continues on the next untitled page (Meta, Goldman)."""
    out: dict[str, list] = {"income": [], "balance": [], "cashflow": []}
    for i, p in enumerate(pages):
        kinds = title_kinds(p)
        if len(kinds) != 1:
            continue
        kind = kinds.pop()
        group = [p]
        nxt = pages[i + 1] if i + 1 < len(pages) else None
        if nxt is not None and not title_kinds(nxt) and not _other_statement(nxt) and _nnum(nxt) >= 25:
            group.append(nxt)
        score = sum(_nnum(g) for g in group)
        consolidated = any(_title_kind(l) and "consolidated" in normalise_label(l) for l in p["lines"][:20])
        if score >= 25:
            out[kind].append((score, group, consolidated))
    # if the filing has a 'Consolidated ...' statement, ignore other pages with the same plain title
    # (Bank of America's MD&A has a 'Balance Sheet' table for every business segment)
    for kind, lst in out.items():
        if any(c for _, _, c in lst):
            lst[:] = [x for x in lst if x[2]]
        lst[:] = [(sc, g) for sc, g, _ in lst]
    return out


def compile_metrics(metrics_cfg: dict):
    out = {}
    for name, m in metrics_cfg["metrics"].items():
        if m["statement"] == "text":
            continue
        out[name] = {
            **m,
            "_labels": [re.compile(p) for p in m["labels"]],
            "_exclude": [re.compile(p) for p in m.get("exclude", [])],
        }
    return out


def label_rank(label: str, m: dict) -> int | None:
    """Index of the first label pattern that matches (lower = preferred), or None.
    Patterns are listed most-specific first, so 'Total revenues' beats 'Net sales'."""
    if any(p.fullmatch(label) for p in m["_exclude"]):
        return None
    for i, p in enumerate(m["_labels"]):
        if p.fullmatch(label):
            return i
    return None


def label_matches(label: str, m: dict) -> bool:
    return label_rank(label, m) is not None


# ----------------------------------------------------------------- extractors
EPS_HEADER = re.compile(r"(diluted (net )?(earnings|income)( \(loss\))? per (common )?share|"
                        r"(net )?(earnings|income)( \(loss\))? per (common )?share - diluted|"
                        r"earnings per common share - diluted)")
EPS_UNDER_HEADER = re.compile(r"(net (income|earnings)( \(loss\))?( attributable to .+)?|class a common stock|"
                              r"total net earnings per share)")


def _logical_rows(lines: list[str]):
    """Yield (row_text, header_context). Joins a label that wraps onto the next line
    (Citi: 'Long-term debt (including ... respectively,' / 'at fair value) 316,000 287,000')."""
    pending, header, since = "", None, 99
    for i, line in enumerate(lines):
        text = (pending + " " + line).strip() if pending else line
        label, vals = split_row(text)
        pending = ""
        if vals and not label.strip():
            # an unlabeled total directly above 'Liabilities and ...' is total assets (Merck)
            nxt = next((l for l in lines[i + 1:] if l.strip()), "")
            if normalise_label(nxt).startswith("liabilities"):
                text = "total assets " + text.strip()
        if not vals:
            nl = normalise_label(label)
            if (label.count("(") > label.count(")") or re.search(r"\b(common|attributable to|and)$", nl)) \
                    and len(label) < 200:
                pending = text                      # label continues on the next line
                continue
            if EPS_HEADER.fullmatch(nl):
                header, since = "eps", 0            # e.g. 'Diluted earnings per share (4)'
            since += 1
            continue
        since += 1
        yield text, (header if since <= 6 else None)


def extract_statement_metrics(pages, meta, metrics) -> list[dict]:
    fy = meta["fiscal_year"]
    cands_by_metric: dict[str, list[dict]] = {}
    for kind, groups in statement_pages(pages).items():
        # statements usually sit together in Item 8; best-scoring groups first, max 4
        groups = [g for _, g in sorted(groups, key=lambda x: -x[0])[:4]]
        groups.sort(key=lambda g: g[0]["page"])
        for group in groups:
            years = year_columns(group[0]["lines"])
            col = years.index(max(years)) if years else 0
            scale, scale_word = page_scale("\n".join(g["text"] for g in group))
            done = False
            for p in group:
                if done:
                    break
                for line, ctx in _logical_rows(p["lines"]):
                    raw_label, vals = split_row(line)
                    label = normalise_label(raw_label)
                    if kind == "balance" and re.fullmatch(r"total liabilities,? (and|.* and) .*(equity|deficit|investment)", label):
                        done = True               # end of the balance sheet; skip footnote tables below (VIEs)
                        break
                    if years and len(vals) == 2 * len(years) and all(
                            abs(v) < 100 and v != int(v) for v in vals[1::2]):
                        vals = vals[0::2]         # Home Depot / Lowe's: '% of sales' column after each value
                    for name, m in metrics.items():
                        if m["statement"] != kind:
                            continue
                        rank = label_rank(label, m)
                        if rank is None and m.get("per_share") and ctx == "eps" and EPS_UNDER_HEADER.fullmatch(label):
                            rank = 50                   # row under a 'Diluted EPS' sub-heading (Citi, Pfizer, Visa)
                        if rank is None:
                            continue
                        vv = vals[-len(years):] if years and len(vals) > len(years) else vals
                        raw = vv[col] if col < len(vv) else vv[0]   # drops note refs before the values
                        per_share = m.get("per_share", False)
                        if per_share and abs(raw) >= 100:
                            continue                  # 'Diluted' share-count row, not EPS
                        if not per_share and m["unit"] == "USD" and scale > 1 and all(
                                abs(v) < 100 and v != int(v) for v in vv):
                            continue                  # a per-share row (Citi '$ 7.11'), not a dollar amount
                        cands_by_metric.setdefault(name, []).append({
                            "ticker": meta["ticker"], "kind": "metric", "predicate": name,
                            "value": raw if per_share else raw * scale,
                            "raw_value": raw, "scale": 1 if per_share else scale_word,
                            "unit": m["unit"], "period": f"FY{fy}", "period_end": meta["reportDate"],
                            "doc": meta["pdf"], "page": p["page"], "evidence": line.strip()[:300],
                            "method": f"table_regex:{kind}", "_rank": rank,
                        })
    # best-ranked label wins; among equal ranks: first match by default,
    # `prefer: last` for rows that appear twice, current then non-current
    # (Apple lists "Term debt" under current liabilities and again under non-current)
    facts = []
    for n, lst in cands_by_metric.items():
        best = min(c["_rank"] for c in lst)
        lst = [c for c in lst if c["_rank"] == best]
        f = lst[-1] if metrics[n].get("prefer") == "last" else lst[0]
        facts.append(f)
    for f in facts:
        f.pop("_rank", None)
    return facts + derive_liabilities(facts)


def derive_liabilities(facts) -> list[dict]:
    """Some filers (e.g. Walmart) print no 'Total liabilities' line. Derive it from the
    accounting identity and say so in the method and evidence, so it stays traceable."""
    have = {f["predicate"]: f for f in facts}
    if "TotalLiabilities" in have or not {"TotalAssets", "TotalEquity"} <= have.keys():
        return []
    a, e = have["TotalAssets"], have["TotalEquity"]
    return [{**a, "predicate": "TotalLiabilities", "value": a["value"] - e["value"], "raw_value": None,
             "evidence": f"derived = [{a['evidence']}] minus [{e['evidence']}]",
             "method": "derived:TotalAssets-TotalEquity"}]


EMPLOYEES = re.compile(
    r"(?:approximately|about|over|more than|roughly|had|employed|workforce of)\s+([\d,]+(?:\.\d+)?)\s*(million|thousand)?\s+"
    r"(?:full-?\s?time(?:\s+equivalent)?\s+|full-time and part-time\s+|total\s+)?"
    r"(?:employees|associates|team members|people|colleagues|persons)", re.I)


EMP_EXCLUDE = re.compile(r"covered by|collective|union|in (pharmaceutical )?research|health ?care|insurance|"
                         r"partnering|divest|contractors", re.I)


def extract_employees(pages, meta) -> list[dict]:
    """Total headcount: the LARGEST employee count stated in Item 1, skipping sentences about a
    subset (union-covered, R&D staff, a divested unit) or about people who aren't employees
    (insured Americans). The first match is often a subset, so 'first' is the wrong rule."""
    limit = max(10, int(len(pages) * 0.4))           # Item 1 (Business) is early in the filing
    early = pages[:limit]
    for scope in [early]:
        found = []
        for p in scope:
            flat = re.sub(r"(?<=\w)-\s+(?=\w)", "-", " ".join(p["lines"]))   # 'full- time' line breaks
            for m in EMPLOYEES.finditer(flat):
                v = float(m.group(1).replace(",", ""))
                mult = {"million": 1e6, "thousand": 1e3}.get((m.group(2) or "").lower(), 1)
                s_, e_ = max(0, m.start() - 80), min(len(flat), m.end() + 60)
                ctx = flat[s_:e_]
                near = flat[max(0, m.start() - 45): m.end() + 25]     # narrow window for exclusions
                if v * mult < 1000 or EMP_EXCLUDE.search(near):
                    continue
                found.append((v * mult, v, m.group(2), p["page"], ctx))
        if found:
            val, v, unit, page, ctx = max(found)
            return [{"ticker": meta["ticker"], "kind": "metric", "predicate": "Employees",
                     "value": val, "raw_value": v, "scale": unit or "units",
                     "unit": "count", "period": f"FY{meta['fiscal_year']}",
                     "period_end": meta["reportDate"], "doc": meta["pdf"], "page": page,
                     "evidence": ctx, "method": "text_regex"}]
    return []


AUDITOR_SIG = re.compile(r"/s/\s*([A-Z][A-Za-z&.,' -]{2,60}?LLP)", re.I)
AUDITOR_ALIASES = {
    "ernst & young": "Ernst & Young LLP", "pricewaterhousecoopers": "PricewaterhouseCoopers LLP",
    "deloitte": "Deloitte & Touche LLP", "kpmg": "KPMG LLP", "grant thornton": "Grant Thornton LLP",
    "bdo": "BDO USA, LLP",
}


def canonical_auditor(name: str) -> str:
    low = name.lower()
    for k, v in AUDITOR_ALIASES.items():
        if k in low:
            return v
    return name.strip()


FIRM_NAME = re.compile(r"(Ernst & Young LLP|PricewaterhouseCoopers LLP|Deloitte & Touche LLP|KPMG LLP|"
                       r"Grant Thornton LLP|BDO USA,? (?:LLP|P\.C\.))")


def _auditor_fact(meta, page, name, evidence, method):
    return [{"ticker": meta["ticker"], "kind": "attribute", "predicate": "AUDITED_BY",
             "value": canonical_auditor(name), "unit": None, "period": None,
             "doc": meta["pdf"], "page": page, "evidence": evidence, "method": method}]


def extract_auditor(pages, meta) -> list[dict]:
    # whitespace-normalised: the heading is often split across lines (Goldman Sachs)
    reports = [i for i, p in enumerate(pages)
               if "report of independent registered public accounting firm"
               in re.sub(r"\s+", " ", p["text"].lower())]
    # 1) a typed signature line "/s/ Ernst & Young LLP" - on the report page or up to 5 pages after
    #    (multi-page reports put the signature at the end)
    for i in reports:
        for p in pages[i:i + 6]:
            m = AUDITOR_SIG.search(p["text"])
            if m:
                return _auditor_fact(meta, p["page"], m.group(1), m.group(0), "text_regex:signature")
    # 2) some filers (JPMorgan) sign with a logo image; fall back to a known firm name printed
    #    on the report page or the page right after it (e.g. the firm's address footer)
    for i in reports:
        for p in pages[i:i + 6]:
            m = FIRM_NAME.search(p["text"])
            if m:
                s, e = max(0, m.start() - 30), m.end() + 40
                return _auditor_fact(meta, p["page"], m.group(1), p["text"][s:e].replace("\n", " "),
                                     "text_regex:firm_name")
    return []


def balance_check(facts) -> dict | None:
    """Assets should equal liabilities + equity. A cheap self-test of the extraction."""
    if any(f["predicate"] == "TotalLiabilities" and str(f["method"]).startswith("derived") for f in facts):
        return None
    v = {f["predicate"]: f["value"] for f in facts if f["kind"] == "metric"}
    if {"TotalAssets", "TotalLiabilities", "TotalEquity"} <= v.keys():
        gap = v["TotalAssets"] - v["TotalLiabilities"] - v["TotalEquity"]
        return {"assets_minus_liab_equity": gap, "relative_gap": gap / v["TotalAssets"]}
    return None


def run(tickers: list[str] | None = None) -> None:
    meta_all = json.loads(S.META.read_text())
    metrics = compile_metrics(S.load_metrics())
    report = {}
    for t, meta in meta_all.items():
        if tickers and t not in tickers:
            continue
        meta = {**meta, "ticker": t}
        pages = load_pages(t)
        facts = (extract_statement_metrics(pages, meta, metrics)
                 + extract_employees(pages, meta) + extract_auditor(pages, meta))
        (S.FACTS / f"{t}.json").write_text(json.dumps(facts, indent=1))
        got = sorted({f["predicate"] for f in facts})
        missing = sorted(set(S.load_metrics()["metrics"]) - set(got))
        report[t] = {"n_facts": len(facts), "missing": missing, "balance_check": balance_check(facts)}
        print(f"[ok] {t}: {len(facts)} facts; missing={missing}")
    (S.RESULTS / "extraction_report.json").write_text(json.dumps(report, indent=1))
