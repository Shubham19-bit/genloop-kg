"""Answer one question: plan (LLM) -> execute on graph (deterministic) -> cited answer."""
from __future__ import annotations

import json

from .graph_build import load
from .query.executor import Executor, PlanError
from .query.planner import plan as make_plan

_EXEC = None


def get_executor() -> Executor:
    global _EXEC
    if _EXEC is None:
        _EXEC = Executor(load())
    return _EXEC


def answer(question: str) -> dict:
    ex = get_executor()
    try:
        p = make_plan(question, ex.api)
    except Exception as e:  # noqa: BLE001
        return {"question": question, "error": f"planning failed: {e}", "plan": None,
                "text": "", "value": None, "entities": [], "facts": [], "citations": []}
    try:
        out = ex.run(p)
    except Exception as e:  # noqa: BLE001 - a bad plan must fail one question, not the whole run
        return {"question": question, "error": f"execution failed: {e}", "plan": p,
                "text": "", "value": None, "entities": [], "facts": [], "citations": []}
    return {"question": question, "plan": p, **out}


def pretty(res: dict) -> str:
    lines = [f"Q: {res['question']}"]
    if res.get("error"):
        lines.append(f"ERROR: {res['error']}")
    lines.append(f"A: {res['text']}")
    for c in res.get("citations", []):
        lines.append(f"   source: {c['doc']} p.{c['page']}")
    if res.get("notes"):
        lines.append(f"   notes: {res['notes']}")
    lines.append("   plan: " + json.dumps(res.get("plan")))
    return "\n".join(lines)
