"""Question -> query plan (JSON) with an LLM.

The planner's prompt is built FROM THE GRAPH (its companies, metrics, relations), so
the LLM can only ask for things the graph actually holds. It outputs a plan; it never
sees facts or numbers and never writes the answer.
"""
from __future__ import annotations

import json

from ..llm import complete_json
from .executor import RELATIONS, GraphAPI

SYSTEM = """You translate a question about SEC 10-K filings into a JSON query plan over a knowledge graph.
You do NOT answer the question. Output ONLY the plan JSON.

Ops (each step has a unique "id"; "input" refers to an earlier step id, or is a list of company names/tickers):
- companies: {"op":"companies", "tickers":[...]} or {"sector":"..."} or {"where":{"relation":R,"value":"..."}} (filters can be combined)
- metric:    {"op":"metric", "input":..., "metric":M}
- ratio:     {"op":"ratio", "input":..., "metric":DERIVED}  or  {"numerator":M, "denominator":M}
- rank:      {"op":"rank", "input":<values step>, "order":"desc"|"asc", "k":N}
- filter:    {"op":"filter", "input":<values step>, "cmp":">"|">="|"<"|"<=", "value":NUMBER_IN_BASE_UNITS}
- aggregate: {"op":"aggregate", "input":<values step>, "fn":"sum"|"avg"|"count"|"max"|"min"}
- diff:      {"op":"diff", "input":<values step with exactly 2 companies>}
- attribute: {"op":"attribute", "input":..., "relation":R}
Finish with "answer": <id of the step that answers the question>.
Monetary values in the graph are in plain USD (not millions); write filter thresholds in USD.
"""

EXAMPLES = """Examples:
Q: What was Visa's net income?
{"steps":[{"id":"s1","op":"metric","input":["V"],"metric":"NetIncome"}],"answer":"s1"}

Q: Which had the higher operating margin, Visa or Mastercard?
{"steps":[{"id":"s1","op":"ratio","input":["V","MA"],"metric":"OperatingMargin"},
          {"id":"s2","op":"rank","input":"s1","order":"desc","k":1}],"answer":"s2"}

Q: Who audits the Financials company with the largest total assets?
{"steps":[{"id":"s1","op":"companies","sector":"Financials"},
          {"id":"s2","op":"metric","input":"s1","metric":"TotalAssets"},
          {"id":"s3","op":"rank","input":"s2","order":"desc","k":1},
          {"id":"s4","op":"attribute","input":"s3","relation":"AUDITED_BY"}],"answer":"s4"}

Q: Among companies audited by KPMG, which reported the most revenue?
{"steps":[{"id":"s1","op":"companies","where":{"relation":"AUDITED_BY","value":"KPMG"}},
          {"id":"s2","op":"metric","input":"s1","metric":"Revenue"},
          {"id":"s3","op":"rank","input":"s2","order":"desc","k":1}],"answer":"s3"}
"""


def schema_card(api: GraphAPI) -> str:
    G = api.G
    comps = [f"{d['ticker']}: {d['name']} (aka {', '.join(d.get('aliases', []))})"
             for _, d in G.nodes(data=True) if d.get("type") == "Company"]
    sectors = sorted({d["name"] for n, d in G.nodes(data=True) if n.startswith("sector:")})
    derived = {k: f"{v['numerator']}/{v['denominator']}" for k, v in api.derived.items()}
    return (f"Companies:\n" + "\n".join(comps)
            + f"\n\nSectors: {sectors}\nMetrics: {api.metrics()}\nDerived metrics: {derived}"
            + f"\nRelations: {RELATIONS}  (HEADQUARTERED_IN values look like 'City, State'; "
              "a state name also matches)\n")


def plan(question: str, api: GraphAPI) -> dict:
    prompt = f"{schema_card(api)}\n{EXAMPLES}\nQ: {question}\n"
    out = complete_json(SYSTEM, prompt)
    if "steps" not in out:
        raise ValueError(f"planner returned no steps: {json.dumps(out)[:300]}")
    return out
