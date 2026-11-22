"""Deterministic execution of a query plan against the graph.

The LLM never answers questions. It only writes a PLAN (see planner.py); this module
runs the plan as graph lookups/traversals and returns the answer, the exact fact
nodes it touched (used for precision/recall) and their source pages.

Plan format:
{"steps": [{"id": "s1", "op": "companies", "sector": "Financials"},
           {"id": "s2", "op": "metric", "input": "s1", "metric": "TotalAssets"},
           {"id": "s3", "op": "rank", "input": "s2", "order": "desc", "k": 1},
           {"id": "s4", "op": "attribute", "input": "s3", "relation": "AUDITED_BY"}],
 "answer": "s4"}

Ops
  companies  tickers=[..] | sector=.. | where={relation, value}       -> companies
  metric     input, metric, period?                                  -> values
  ratio      input, metric=<derived name> | numerator+denominator    -> values
  rank       input(values), order=desc|asc, k                        -> values (top k)
  filter     input(values), cmp=> >= < <=, value                     -> values
  aggregate  input(values), fn=sum|avg|count|max|min                 -> scalar
  diff       input(values) with exactly 2 companies (first - second) -> scalar
  attribute  input(companies|values), relation                       -> attributes
"""
from __future__ import annotations

import difflib
import operator
import re
from dataclasses import dataclass, field

import networkx as nx

from .. import settings as S

RELATIONS = ["AUDITED_BY", "HEADQUARTERED_IN", "INCORPORATED_IN", "CEO", "HAS_SEGMENT", "IN_SECTOR"]


SHORT_AUDITOR = {"ey": "Ernst & Young", "e y": "Ernst & Young", "pwc": "PricewaterhouseCoopers",
                 "deloitte": "Deloitte & Touche", "kpmg": "KPMG"}


class PlanError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


@dataclass
class StepResult:
    kind: str                       # companies | values | attributes | scalar
    data: object
    facts: set = field(default_factory=set)
    notes: list = field(default_factory=list)


class GraphAPI:
    """Small typed read API over the NetworkX graph."""

    def __init__(self, G: nx.MultiDiGraph):
        self.G = G
        self.derived = S.load_metrics().get("derived", {})
        self.alias = {}
        for n, d in G.nodes(data=True):
            if d.get("type") == "Company":
                t = d["ticker"]
                for a in [t, d["name"], *d.get("aliases", [])]:
                    self.alias[_norm(a)] = t

    # -- entities
    def tickers(self) -> list[str]:
        return [d["ticker"] for _, d in self.G.nodes(data=True) if d.get("type") == "Company"]

    def resolve_company(self, s: str) -> str:
        key = _norm(s)
        if key in self.alias:
            return self.alias[key]
        if s.upper() in self.tickers():
            return s.upper()
        m = difflib.get_close_matches(key, list(self.alias), n=1, cutoff=0.85)
        if m:
            return self.alias[m[0]]
        raise PlanError(f"unknown company '{s}'")

    def metrics(self) -> list[str]:
        return sorted(d["name"] for _, d in self.G.nodes(data=True) if d.get("type") == "Metric")

    def resolve_metric(self, m: str) -> str:
        names = self.metrics() + list(self.derived)
        if m in names:
            return m
        hit = difflib.get_close_matches(m, names, n=1, cutoff=0.7)
        if hit:
            return hit[0]
        raise PlanError(f"unknown metric '{m}'")

    # -- facts
    def metric_fact(self, ticker: str, metric: str, period: str | None = None) -> str | None:
        """Traverse Company -HAS_FACT-> Fact -OF_METRIC-> Metric (and FOR_PERIOD)."""
        best = None
        for _, fid, e in self.G.out_edges(f"company:{ticker}", data=True):
            if e["rel"] != "HAS_FACT":
                continue
            d = self.G.nodes[fid]
            if d.get("kind") != "metric" or d.get("predicate") != metric:
                continue
            if period and d.get("period") != period:
                continue
            if best is None or d.get("period", "") > self.G.nodes[best].get("period", ""):
                best = fid                       # default: latest period
        return best

    def attribute(self, ticker: str, relation: str) -> tuple[list[str], set]:
        vals, facts = [], set()
        for _, eid, e in self.G.out_edges(f"company:{ticker}", data=True):
            if e["rel"] == relation:
                vals.append(self.G.nodes[eid]["name"])
                facts.add(e["fact"])
        return vals, facts

    def companies_where(self, relation: str, value: str) -> tuple[list[str], set]:
        """Reverse traversal: Entity <-relation- Company (City -LOCATED_IN-> State also matches)."""
        want = _norm(value)
        out, facts = [], set()
        for t in self.tickers():
            for _, eid, e in self.G.out_edges(f"company:{t}", data=True):
                if e["rel"] != relation:
                    continue
                names = [self.G.nodes[eid]["name"]]
                names += [self.G.nodes[s]["name"] for _, s, r in self.G.out_edges(eid, data="rel")
                          if r == "LOCATED_IN"]
                if any(want == _norm(n) or want in _norm(n).split(" ") or want in _norm(n)
                       for n in names):
                    out.append(t)
                    facts.add(e["fact"])
                    break
        return out, facts

    def fact(self, fid: str) -> dict:
        return self.G.nodes[fid]


class Executor:
    def __init__(self, G: nx.MultiDiGraph):
        self.api = GraphAPI(G)

    # ---- input helpers
    def _companies_of(self, step: dict, env: dict) -> list[str]:
        src = step.get("input")
        if isinstance(src, list):
            out = []
            for x in src:
                if isinstance(x, dict):                       # {"ticker": "AAPL"} / {"name": ...}
                    x = x.get("ticker") or x.get("name") or x.get("company") or ""
                if isinstance(x, str) and x in env:          # a step id inside a list
                    out += self._companies_of({"id": step.get("id"), "input": x}, env)
                else:
                    out.append(self.api.resolve_company(str(x)))
            return list(dict.fromkeys(out))
        if src not in env:
            raise PlanError(f"step {step.get('id')}: unknown input '{src}'")
        r = env[src]
        if r.kind == "companies":
            return list(r.data)
        if r.kind == "values":
            return [row["ticker"] for row in r.data]
        if r.kind == "attributes":
            return list(r.data)
        raise PlanError(f"step {step.get('id')}: input '{src}' is not a set of companies")

    def _values_of(self, step: dict, env: dict) -> StepResult:
        r = env.get(step.get("input"))
        if r is None or r.kind != "values":
            raise PlanError(f"step {step.get('id')}: needs a 'values' input")
        return r

    # ---- ops
    def op_companies(self, s, env):
        sets, facts = [], set()
        if s.get("tickers"):
            sets.append([self.api.resolve_company(x) for x in s["tickers"]])
        if s.get("sector"):
            ts, f = self.api.companies_where("IN_SECTOR", s["sector"])
            sets.append(ts)
            facts |= f
        if s.get("where"):
            w = s["where"]
            value = w["value"]
            if w["relation"] == "AUDITED_BY":
                value = SHORT_AUDITOR.get(_norm(value), value)
            ts, f = self.api.companies_where(w["relation"], value)
            sets.append(ts)
            facts |= f
        if not sets:
            sets.append(self.api.tickers())
        keep = [t for t in sets[0] if all(t in other for other in sets[1:])]
        return StepResult("companies", keep, facts)

    def op_metric(self, s, env):
        metric = self.api.resolve_metric(s["metric"])
        if metric in self.api.derived:
            return self.op_ratio({**s, "metric": metric}, env)
        rows, facts, notes = [], set(), []
        for t in self._companies_of(s, env):
            fid = self.api.metric_fact(t, metric, s.get("period"))
            if fid is None:
                notes.append(f"no {metric} fact for {t}")
                continue
            rows.append({"ticker": t, "value": self.api.fact(fid)["value"], "facts": [fid],
                         "unit": self.api.fact(fid)["unit"]})
            facts.add(fid)
        return StepResult("values", rows, facts, notes)

    def op_ratio(self, s, env):
        num_ref, den_ref = s.get("numerator"), s.get("denominator")
        if isinstance(num_ref, str) and isinstance(den_ref, str) and num_ref in env and den_ref in env:
            a, b = env[num_ref], env[den_ref]
            if a.kind != "values" or b.kind != "values":
                raise PlanError("ratio of steps needs two 'values' steps")
            bmap = {r["ticker"]: r for r in b.data}
            rows, notes = [], []
            for r in a.data:
                d = bmap.get(r["ticker"])
                if d is None or not d["value"]:
                    notes.append(f"cannot compute ratio for {r['ticker']}")
                    continue
                rows.append({"ticker": r["ticker"], "value": r["value"] / d["value"],
                             "facts": r["facts"] + d["facts"], "unit": "ratio"})
            return StepResult("values", rows, set(a.facts) | set(b.facts), notes)
        if s.get("metric") in self.api.derived:
            d = self.api.derived[s["metric"]]
            num, den = d["numerator"], d["denominator"]
        else:
            num, den = self.api.resolve_metric(s["numerator"]), self.api.resolve_metric(s["denominator"])
        rows, facts, notes = [], set(), []
        for t in self._companies_of(s, env):
            a = self.api.metric_fact(t, num, s.get("period"))
            b = self.api.metric_fact(t, den, s.get("period"))
            if not a or not b or not self.api.fact(b)["value"]:
                notes.append(f"cannot compute {num}/{den} for {t}")
                continue
            v = self.api.fact(a)["value"] / self.api.fact(b)["value"]
            # derived value keeps DERIVED_FROM links to both inputs
            rows.append({"ticker": t, "value": v, "facts": [a, b], "unit": "ratio"})
            facts |= {a, b}
        return StepResult("values", rows, facts, notes)

    def op_rank(self, s, env):
        r = self._values_of(s, env)
        rows = sorted(r.data, key=lambda x: x["value"], reverse=s.get("order", "desc") == "desc")
        k = int(s.get("k", 1))
        # ranking needs every candidate's value, so all input facts count as retrieved
        return StepResult("values", rows[:k], set(r.facts), list(r.notes))

    def op_filter(self, s, env):
        r = self._values_of(s, env)
        cmp = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}[s["cmp"]]
        rows = [x for x in r.data if cmp(x["value"], float(s["value"]))]
        return StepResult("values", rows, set(r.facts), list(r.notes))

    def op_aggregate(self, s, env):
        r = self._values_of(s, env)
        vals = [x["value"] for x in r.data]
        fn = s.get("fn", "sum")
        out = {"sum": sum, "max": max, "min": min, "count": len,
               "avg": lambda v: sum(v) / len(v) if v else None}[fn](vals) if vals else None
        return StepResult("scalar", {"value": out, "fn": fn, "unit": r.data[0]["unit"] if r.data else None},
                          set(r.facts), list(r.notes))

    def op_diff(self, s, env):
        src = s.get("input")
        if isinstance(src, list) and len(src) == 2 and all(isinstance(x, str) and x in env for x in src):
            parts = [env[x] for x in src]
            if any(p.kind != "values" or len(p.data) != 1 for p in parts):
                raise PlanError("diff of steps needs two single-company 'values' steps")
            r = StepResult("values", [parts[0].data[0], parts[1].data[0]],
                           set(parts[0].facts) | set(parts[1].facts))
        else:
            r = self._values_of(s, env)
        if len(r.data) != 2:
            raise PlanError("diff needs exactly two companies")
        a, b = r.data
        return StepResult("scalar", {"value": a["value"] - b["value"], "fn": f"{a['ticker']} - {b['ticker']}",
                                     "unit": a["unit"]}, set(r.facts), list(r.notes))

    def op_attribute(self, s, env):
        rel = s["relation"]
        if rel not in RELATIONS:
            raise PlanError(f"unknown relation '{rel}'")
        data, facts, notes = {}, set(), []
        upstream = env.get(s.get("input")) if isinstance(s.get("input"), str) else None
        if upstream:
            facts |= upstream.facts
        for t in self._companies_of(s, env):
            vals, f = self.api.attribute(t, rel)
            if not vals:
                notes.append(f"no {rel} fact for {t}")
            data[t] = vals
            facts |= f
        return StepResult("attributes", data, facts, notes)

    # ---- driver
    def run(self, plan: dict) -> dict:
        env: dict[str, StepResult] = {}
        all_facts, notes = set(), []
        for s in plan.get("steps", []):
            fn = getattr(self, f"op_{s.get('op')}", None)
            if fn is None:
                raise PlanError(f"unknown op '{s.get('op')}'")
            r = fn(s, env)
            env[s["id"]] = r
            all_facts |= r.facts
            notes += r.notes
        final = env[plan.get("answer") or plan["steps"][-1]["id"]]
        return self._render(final, all_facts, notes)

    # ---- answer rendering
    @staticmethod
    def fmt(v, unit) -> str:
        if v is None:
            return "n/a"
        if unit == "USD":
            return f"${v / 1e6:,.0f} million"
        if unit == "USD_per_share":
            return f"${v:,.2f} per share"
        if unit == "ratio":
            return f"{v * 100:.2f}%"
        return f"{v:,.0f}"

    def _render(self, r: StepResult, facts: set, notes: list) -> dict:
        value, entities = None, []
        if r.kind == "values":
            entities = [x["ticker"] for x in r.data]
            value = r.data[0]["value"] if len(r.data) == 1 else None
            text = "; ".join(f"{x['ticker']}: {self.fmt(x['value'], x['unit'])}" for x in r.data)
        elif r.kind == "scalar":
            value = r.data["value"]
            text = self.fmt(value, r.data["unit"])
        elif r.kind == "attributes":
            entities = sorted({v for vals in r.data.values() for v in vals})
            text = "; ".join(f"{t}: {', '.join(v) or 'n/a'}" for t, v in r.data.items())
        else:
            entities = list(r.data)
            text = ", ".join(entities)
        # retrieval metrics count facts from FILINGS only (config facts like sector are excluded)
        filed = sorted(f for f in facts if self.api.fact(f).get("method") != "config")
        cites = sorted({(self.api.fact(f)["doc"], self.api.fact(f)["page"]) for f in filed})
        return {"text": text or "No answer found in the graph.", "value": value,
                "entities": entities, "facts": [self.api.fact(f)["key"] for f in filed],
                "citations": [{"doc": d, "page": p} for d, p in cites], "notes": notes}
