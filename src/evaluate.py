"""Run the benchmark and report accuracy / precision / recall, overall and by type.

Definitions (as in the task):
  accuracy  = correct answers / total questions
  precision = |retrieved facts ∩ gold facts| / |retrieved facts|   (per question, then averaged)
  recall    = |retrieved facts ∩ gold facts| / |gold facts|        (per question, then averaged)
Facts are compared by key, e.g. "JPM|TotalAssets|FY2025" or "JPM|AUDITED_BY".
A question whose plan or execution fails counts as wrong with precision = recall = 0.

Outputs: results/run.jsonl (every answer, plan, facts) and results/summary.md
"""
from __future__ import annotations

import json
import re
from collections import defaultdict

from . import settings as S
from .ask import answer, get_executor

NUM_TOL = 0.01      # 1% relative tolerance for numbers
RATIO_TOL = 0.0005  # absolute tolerance for ratios (0.05 percentage points)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _entity_names(ticker_or_name: str) -> set[str]:
    """A ticker also matches the company's name and aliases."""
    api = get_executor().api
    names = {_norm(ticker_or_name)}
    try:
        t = api.resolve_company(ticker_or_name)
        d = api.G.nodes[f"company:{t}"]
        names |= {_norm(t), _norm(d["name"]), *(_norm(a) for a in d.get("aliases", []))}
    except Exception:  # noqa: BLE001 - not a company (e.g. an auditor name)
        pass
    return names


def _same_entity(a: str, b: str) -> bool:
    na, nb = _entity_names(a), _entity_names(b)
    return bool(na & nb) or any(x in y or y in x for x in na for y in nb if len(x) > 3 and len(y) > 3)


def is_correct(q: dict, res: dict) -> bool:
    t = q["answer_type"]
    if t == "number":
        v, g = res.get("value"), q["gold_value"]
        if v is None:
            return False
        if q.get("unit") == "ratio":
            return abs(v - g) <= RATIO_TOL
        return abs(v - g) <= NUM_TOL * abs(g)
    if t in ("entity", "list"):
        sys_e, gold_e = res.get("entities", []), q["gold_entities"]
        return (len(sys_e) == len(gold_e)
                and all(any(_same_entity(g, s) for s in sys_e) for g in gold_e))
    if t == "text":
        return _norm(q["gold_answer"]) in _norm(res.get("text", ""))
    raise ValueError(f"unknown answer_type {t}")


def prf(retrieved: list[str], gold: list[str]) -> tuple[float, float]:
    R, G = set(retrieved), set(gold)
    hit = len(R & G)
    return (hit / len(R) if R else 0.0), (hit / len(G) if G else 0.0)


def run(limit: int | None = None) -> None:
    qs = [json.loads(l) for l in S.BENCHMARK.read_text(encoding="utf-8").splitlines() if l.strip()]
    qs = [q for q in qs if not str(q.get("gold_answer", "")).startswith("TODO")][:limit]
    rows = []
    with (S.RESULTS / "run.jsonl").open("w", encoding="utf-8") as fh:
        for q in qs:
            res = answer(q["question"])
            ok = is_correct(q, res)
            p, r = prf(res.get("facts", []), q["gold_facts"])
            row = {"id": q["id"], "type": q["type"], "correct": ok, "precision": p, "recall": r,
                   "gold_answer": q["gold_answer"], "system_answer": res.get("text"),
                   "error": res.get("error"), "retrieved": res.get("facts"),
                   "gold_facts": q["gold_facts"], "plan": res.get("plan"),
                   "citations": res.get("citations")}
            rows.append(row)
            fh.write(json.dumps(row) + "\n")
            print(f"{q['id']:>4} {q['type']:<12} {'OK ' if ok else 'XX '} P={p:.2f} R={r:.2f}  {res.get('text','')[:70]}")

    groups = defaultdict(list)
    for r in rows:
        groups[r["type"]].append(r)
        groups["overall"].append(r)
    order = ["single_fact", "multi_hop", "comparison", "overall"]
    md = ["| Type | N | Accuracy | Precision | Recall |", "|---|---|---|---|---|"]
    for k in order:
        g = groups.get(k, [])
        if not g:
            continue
        n = len(g)
        md.append(f"| {k} | {n} | {sum(r['correct'] for r in g) / n:.2%} | "
                  f"{sum(r['precision'] for r in g) / n:.2%} | {sum(r['recall'] for r in g) / n:.2%} |")
    fails = [r for r in rows if not r["correct"]]
    md += ["", f"Failures: {len(fails)}"] + [
        f"- {r['id']} ({r['type']}): gold=`{r['gold_answer']}` system=`{r['system_answer']}`"
        + (f" error=`{r['error']}`" if r["error"] else "") for r in fails]
    (S.RESULTS / "summary.md").write_text("\n".join(md), encoding="utf-8")
    print("\n" + "\n".join(md[: len(order) + 2]))
