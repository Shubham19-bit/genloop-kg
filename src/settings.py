"""Paths and config loading. Everything else imports from here."""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
RAW_HTML = DATA / "raw_html"        # EDGAR primary documents
PDFS = DATA / "pdfs"                # printed PDFs (the corpus we parse)
PAGES = DATA / "pages"              # per-page text + reconstructed lines
FACTS = DATA / "facts"              # extracted facts, one JSON per company
CACHE = DATA / "llm_cache"          # every LLM response, keyed by prompt hash
GRAPH_PATH = DATA / "graph.pkl"
META = DATA / "filings.json"        # which filing we used per company
BENCHMARK = ROOT / "benchmark" / "benchmark.jsonl"
RESULTS = ROOT / "results"

for p in (RAW_HTML, PDFS, PAGES, FACTS, CACHE, RESULTS):
    p.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = re.split(r"\s+#", v, maxsplit=1)[0]        # drop inline '# comment'
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# SEC asks every automated client to identify itself with a contact email.
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "Genloop take-home your.email@example.com")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")          # gemini | groq | ollama
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-4-hydrogen")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")


def load_companies() -> dict:
    return yaml.safe_load((CONFIG / "companies.yaml").read_text(encoding="utf-8"))


def load_metrics() -> dict:
    return yaml.safe_load((CONFIG / "metrics.yaml").read_text(encoding="utf-8"))
