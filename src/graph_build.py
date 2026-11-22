"""Step 4: build the knowledge graph (NetworkX MultiDiGraph) from extracted facts.

Schema (node type: id pattern)
  Company   company:AAPL            Sector   sector:Technology
  Filing    filing:<pdf>            Page     page:<pdf>#<n>
  Metric    metric:Revenue          Period   period:FY2025
  Fact      fact:AAPL|Revenue|FY2025   (metric fact: value, unit, scale, evidence)
            fact:AAPL|AUDITED_BY       (attribute fact)
  Entity    entity:Auditor:KPMG LLP, entity:City:..., entity:State:..., entity:Person:...,
            entity:Segment:AAPL:iPhone

Edges
  Company -HAS_FACT-> Fact -OF_METRIC-> Metric ; Fact -FOR_PERIOD-> Period
  Fact -SOURCED_FROM-> Page <-HAS_PAGE- Filing <-FILED- Company
  Fact -OBJECT-> Entity            (attribute facts)
  Company -AUDITED_BY|HEADQUARTERED_IN|INCORPORATED_IN|CEO|HAS_SEGMENT|IN_SECTOR-> Entity
      shortcut edges for traversal; each carries fact=<fact id> so provenance is never lost
  City -LOCATED_IN-> State

Facts are nodes (reified), not plain edges, because a financial number needs
attributes of its own: period, unit, scale, source page and the evidence row.
"""
from __future__ import annotations

import json
import pickle

import networkx as nx

from . import settings as S

ENTITY_TYPE = {"AUDITED_BY": "Auditor", "HEADQUARTERED_IN": "City", "INCORPORATED_IN": "Jurisdiction",
               "CEO": "Person", "HAS_SEGMENT": "Segment", "IN_SECTOR": "Sector"}


US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
             "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
             "FL": "Florida", "GA": "Georgia", "IL": "Illinois", "IN": "Indiana", "MA": "Massachusetts",
             "MD": "Maryland", "MI": "Michigan", "MN": "Minnesota", "NC": "North Carolina", "NJ": "New Jersey",
             "NY": "New York", "OH": "Ohio", "OR": "Oregon", "PA": "Pennsylvania", "TN": "Tennessee",
             "TX": "Texas", "VA": "Virginia", "WA": "Washington", "WI": "Wisconsin"}


def normalise_place(v) -> str:
    """'Purchase, NY' -> 'Purchase, New York' so state filters match however the filing wrote it."""
    v = str(v).strip()
    if "," in v:
        city, st = v.rsplit(",", 1)
        st = US_STATES.get(st.strip().upper().replace(".", ""), st.strip())
        return f"{city.strip()}, {st}"
    return v


def fact_key(f: dict) -> str:
    if f["kind"] == "metric":
        return f"{f['ticker']}|{f['predicate']}|{f['period']}"
    return f"{f['ticker']}|{f['predicate']}"


def _add_page(G, doc: str, page: int | None) -> str | None:
    if page is None:
        return None
    pid = f"page:{doc}#{page}"
    if pid not in G:
        G.add_node(pid, type="Page", doc=doc, page=page)
        G.add_edge(f"filing:{doc}", pid, rel="HAS_PAGE")
    return pid


def _entity_id(pred: str, value: str, ticker: str) -> str:
    etype = ENTITY_TYPE.get(pred, "Entity")
    if etype == "Segment":                       # segment names are company-specific
        return f"entity:Segment:{ticker}:{value}"
    if etype == "Sector":
        return f"sector:{value}"
    return f"entity:{etype}:{value}"


def add_fact(G, f: dict) -> str:
    fid = "fact:" + fact_key(f)
    t = f["ticker"]
    G.add_node(fid, type="Fact", key=fact_key(f),
               **{k: f.get(k) for k in ("kind", "predicate", "value", "raw_value", "scale", "unit",
                                        "period", "period_end", "doc", "page", "evidence", "method")})
    G.add_edge(f"company:{t}", fid, rel="HAS_FACT")
    pid = _add_page(G, f["doc"], f.get("page")) if f["doc"] in G.graph["docs"] else None
    if pid:
        G.add_edge(fid, pid, rel="SOURCED_FROM")

    if f["kind"] == "metric":
        G.add_node(f"metric:{f['predicate']}", type="Metric", name=f["predicate"])
        G.add_edge(fid, f"metric:{f['predicate']}", rel="OF_METRIC")
        G.add_node(f"period:{f['period']}", type="Period", name=f["period"])
        G.add_edge(fid, f"period:{f['period']}", rel="FOR_PERIOD")
        return fid

    values = f["value"] if isinstance(f["value"], list) else [f["value"]]
    if f["predicate"] == "HEADQUARTERED_IN":
        values = [normalise_place(v) for v in values]
    for v in values:
        v = str(v).strip()
        eid = _entity_id(f["predicate"], v, t)
        G.add_node(eid, type=ENTITY_TYPE.get(f["predicate"], "Entity"), name=v)
        G.add_edge(fid, eid, rel="OBJECT")
        G.add_edge(f"company:{t}", eid, rel=f["predicate"], fact=fid)
        if f["predicate"] == "HEADQUARTERED_IN" and "," in v:
            state = v.rsplit(",", 1)[1].strip()
            sid = f"entity:State:{state}"
            G.add_node(sid, type="State", name=state)
            G.add_edge(eid, sid, rel="LOCATED_IN")
    return fid


def build() -> nx.MultiDiGraph:
    cfg = S.load_companies()
    meta = json.loads(S.META.read_text()) if S.META.exists() else {}
    G = nx.MultiDiGraph()
    G.graph["docs"] = set()
    for c in cfg["companies"]:
        t = c["ticker"]
        G.add_node(f"company:{t}", type="Company", ticker=t, name=c["name"],
                   aliases=c.get("aliases", []))
        if t in meta:
            doc = meta[t]["pdf"]
            G.graph["docs"].add(doc)
            G.add_node(f"filing:{doc}", type="Filing", doc=doc, url=meta[t]["url"],
                       form="10-K", period_end=meta[t]["reportDate"], filed=meta[t]["filingDate"])
            G.add_edge(f"company:{t}", f"filing:{doc}", rel="FILED")
        # sector membership is OUR grouping; provenance says so honestly
        add_fact(G, {"ticker": t, "kind": "attribute", "predicate": "IN_SECTOR",
                     "value": c["sector"], "doc": "config/companies.yaml", "page": None,
                     "evidence": "sector assigned in config", "method": "config"})

    for path in sorted(S.FACTS.glob("*.json")):
        for f in json.loads(path.read_text()):
            add_fact(G, f)
    return G


def run() -> None:
    G = build()
    with S.GRAPH_PATH.open("wb") as fh:
        pickle.dump(G, fh)
    types = {}
    for _, d in G.nodes(data=True):
        types[d.get("type")] = types.get(d.get("type"), 0) + 1
    print(f"[ok] graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    print("     node types:", dict(sorted(types.items())))


def load() -> nx.MultiDiGraph:
    with S.GRAPH_PATH.open("rb") as fh:
        return pickle.load(fh)
