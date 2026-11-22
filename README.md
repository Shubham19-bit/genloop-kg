# Knowledge-graph QA over SEC 10-K filings

This project builds a knowledge graph from the 10-K PDFs of 35 companies. Questions are answered by turning them into a **graph-traversal plan** that is executed deterministically, not by searching text chunks. Every answer comes with the facts it used and the PDF page each one came from. A 30-question benchmark, written and verified by hand, measures how well it works.

| Question type | N | Accuracy | Precision | Recall |
|---|---|---|---|---|
| Single-fact | 10 | 90.0% | 90.0% | 90.0% |
| Multi-hop | 10 | 90.0% | 98.9% | 96.0% |
| Cross-filing comparison | 10 | 90.0% | 90.0% | 90.0% |
| **Overall** | **30** | **90.0%** | **93.0%** | **92.0%** |

The full write-up (methodology, benchmark construction, failure analysis, improvements) is in **[`docs/results.pdf`](docs/results.pdf)**. Every answer, plan and retrieved fact is in `results/run.jsonl`. The first, pre-fix run is kept in `results/run_v1_first_attempt.jsonl`.

## Why a graph, and what kind

A text-chunk retriever returns passages that *sound like* the question. It can't rank all nine banks by total assets, chain "biggest bank, then its auditor", or notice that a fact is missing. Here the graph's structure does that work:

- **Facts are nodes, with their sources.** `Company -> Fact{value, unit, scale, period, evidence row} -> Metric`, plus `Fact -> Period` and `Fact -> Page <- Filing`. A number never travels without its page.
- **A metric dictionary.** About 60 filing labels ("Total net sales", "Total revenue, net of interest expense", "Sales to customers"…) map to 12 canonical metrics (`config/metrics.yaml`), so cross-company comparison is a single traversal.
- **Shared entity nodes** (Auditor, City -> State, Sector, Person/CEO, Segment) connect the 35 filings for multi-hop questions.
- **Derived values are computed with citations.** Margins, debt/equity, ROA and R&D intensity are computed at query time and cite both input facts.
- **The LLM plans; the graph answers.** The LLM turns a question into a JSON plan over 8 operations (`companies, metric, ratio, rank, filter, aggregate, diff, attribute`). Python runs that plan on the graph. The LLM never sees numbers and never writes the answer. A missing fact gives "no answer", never a guess.
- **Extraction guards.** Numbers come from regex and table parsing, never from the LLM. Text facts from the LLM must quote the page exactly, and the code checks that the quote really is on that page.

```
question --LLM--> JSON plan --executor--> graph traversal --> answer + fact keys + PDF pages
PDF --word positions--> table rows --regex + metric dictionary--> numeric facts (382) + auditor (34/35)
PDF --2-3 candidate pages--> local LLM --exact-quote check--> HQ, CEO, incorporation, segments
                                                             \--> NetworkX graph: 1,126 nodes / 2,784 edges
```

## Setup (Windows PowerShell; the same commands work on macOS/Linux)

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env        # defaults: local Ollama + qwen3.5:9b (the model used for the reported run)
python -m pytest -q                # offline tests (the end-to-end test also needs: python -m playwright install chromium)
```

### Reproduce the reported numbers (no model or API needed)

The extracted facts and **every LLM response** (`data/llm_cache/`) are committed. With the default `.env`, this replays the exact run:

```powershell
python -m src.cli build      # facts -> data/graph.pkl
python -m src.cli eval       # -> results/summary.md, results/run.jsonl
python -m src.cli ask "Who audits the Financials company with the largest total assets?"
```

A new question needs a running model: install [Ollama](https://ollama.com) and run `ollama pull qwen3.5:9b`. To use the Gemini or Groq free tier instead, set `LLM_PROVIDER` in `.env`.

### Rebuild everything from scratch

```powershell
python -m playwright install chromium
# set SEC_USER_AGENT in .env to "Your Name your@email" (SEC requires it)
python -m src.cli download     # EDGAR -> HTML -> PDF (data/pdfs); Wells Fargo's Exhibit 13 is merged in
python -m src.cli parse        # PDF -> per-page rows rebuilt from word positions
python -m src.cli extract      # numeric + auditor facts (regex), results/extraction_report.json
python -m src.cli extract-llm  # HQ, CEO, incorporation, segments (LLM + quote check)
python -m src.cli build
python -m src.cli eval
```

## Benchmark

`benchmark/benchmark.jsonl` has 30 questions (10 single-fact, 10 multi-hop, 10 comparison). Each question has a gold answer, its source documents and pages, and the full list of **gold supporting facts** used for precision and recall. How it was built:

1. The questions were written **before** any QA run, to cover every metric and relation type and all four sectors.
2. Gold values were located in the PDFs with a different model from the pipeline's (Gemini chat), one filing at a time, following a fixed written rubric (`benchmark/GOLD_EXTRACTION_PROMPT.md`). Its raw output is `benchmark/gold_raw.jsonl`.
3. **Every gold number was checked against the filing's own inline-XBRL tag** (`python scripts/check_gold.py`): 334 of 335 agree, and the one difference is a definition choice. Text facts were checked on their quoted page.
4. Winners, sums and ratios were computed in code from the verified values. No gold answer was changed after seeing system output.

Details are in `benchmark/README.md`.

## Verification tools

- `python scripts/xbrl_check.py` compares every **extracted** value with the filing's inline-XBRL tags (334/335 match). This is a check only. XBRL is never used to answer.
- `python scripts/check_gold.py` does the same for the **gold** values.
- `python -m src.cli debug TICKER` shows which pages were read as statements and, for each missing metric, how the filing actually words that row.

## Repo map

| Path | What it holds |
|---|---|
| `config/companies.yaml` | 35 companies, sectors, aliases, fiscal-period window |
| `config/metrics.yaml` | Metric dictionary: labels in priority order -> canonical metric; derived ratios |
| `src/download.py` | EDGAR lookup, 10-K download, HTML -> PDF, Exhibit 13 merge |
| `src/parse_pdf.py` | Words -> visual rows (rebuilds table lines from word positions) |
| `src/extract_numeric.py` | Statement-page detection, year column, scale, label ranking, auditor, employees |
| `src/extract_llm.py` | Candidate pages -> LLM JSON -> exact-quote check |
| `src/graph_build.py` | Graph schema and construction |
| `src/query/planner.py` | Question -> JSON plan (prompt built from the graph's own schema) |
| `src/query/executor.py` | Plan -> traversal -> answer, fact keys, citations |
| `src/evaluate.py` | Accuracy, precision, recall, overall and by type |
| `benchmark/` | Benchmark, gold extraction rubric and raw gold values |
| `results/` | `run.jsonl`, `summary.md`, `run_v1_first_attempt.jsonl`, `xbrl_check.csv`, `gold_check.csv` |
| `docs/results.pdf` | The 2-page results write-up |

## How AI tools were used

- **Claude (Anthropic)** was my pair-programmer and tutor. It helped plan the approach, explained SEC filings and knowledge-graph design as I studied them, wrote most of the code with me, and diagnosed extraction failures on the real filings. It also drafted the benchmark question wording and the first draft of this README and the results PDF. I reviewed, edited and ran everything, and I can walk through every design decision.
- **Gemini (chat)** was used only to *locate* gold values in the PDFs, following the rubric above. Every number it returned was verified against XBRL, and the text facts were checked on the page.
- **Qwen 3.5 9B (local, via Ollama)** runs inside the pipeline for two jobs: extracting text facts (with an exact-quote check) and turning questions into plans. It never extracts numbers or writes answers. No paid services were used.

## Known limitations

Short version (details in `docs/results.pdf` §3):
- Aggregates over a partly-extracted set can be silently wrong. Q18 missed Pfizer's HQ, so its New York total was too low.
- The planner sometimes invents plan syntax (Q24, which failed safely) or narrows a set (Q19 read "bank" as 6 companies).
- Regex misses unusual wording (Goldman's "headcount"). The LLM listed Morgan Stanley's CFO as a second CEO.
- Fiscal years end in different months, and some metrics are defined differently across sectors.
