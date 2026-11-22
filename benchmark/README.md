# Benchmark

`benchmark.jsonl` holds 30 questions, one JSON object per line: 10 `single_fact`, 10 `multi_hop`, 10 `comparison`.

## Fields

| Field | Meaning |
|---|---|
| `question` | Natural wording, as an analyst would ask it |
| `answer_type` | `number` (scored on `gold_value`), `entity` / `list` (scored on `gold_entities`), or `text` (scored on the key name in `gold_answer`) |
| `unit` | `USD` (plain dollars, not millions), `USD_per_share`, `count`, or `ratio` (a fraction) |
| `gold_value`, `gold_answer`, `gold_entities` | The gold answer in machine and human form |
| `gold_facts` | **Every** fact needed to justify the answer, as keys: `TICKER\|Metric\|FY2025` or `TICKER\|RELATION`. Precision and recall are computed against these. |
| `sources` | `{doc, page, fact}`, where `page` is the PDF page index (1 = the first page of the file) |
| `verified_with`, `notes` | How the value was checked, plus any caveat (fiscal-year differences, label choices) |

## How the questions were chosen

- They cover all 12 metrics, the 4 relations (auditor, HQ, CEO, segments), all 4 sectors and every query operation (lookup, filter, rank, ratio, sum, difference, hop).
- Comparisons span different fiscal years on purpose (e.g. Visa, which ends in September, against Mastercard, which ends in December), and the notes say so.
- Some questions are deliberately hard: Q10 (Goldman reports "headcount", not employees) and Q20, where the top revenue is a close race between Amazon ($716.9B) and Walmart ($713.2B).
- The question wording was drafted with an AI assistant and edited by hand. It was fixed **before** any QA run (the draft is in `drafts/`). Two questions were rewritten before the first run because their gold facts don't exist in the filings (Amazon has no total-liabilities line, and Morgan Stanley has no long-term-debt line).

## How gold answers were derived and verified

1. **Locate:** each filing was given to Gemini (chat), a different model from the pipeline's Qwen, together with the fixed rubric in `GOLD_EXTRACTION_PROMPT.md`. The rubric sets out which statement to use, which line counts as "revenue", how to treat nulls, and which page numbering to use. The raw output is `gold_raw.jsonl`. For the 15 longest filings, Gemini was given a subset of key pages with each page stamped with its original page number, because the full PDF was cut off.
2. **Verify numbers:** `python scripts/check_gold.py` compares every gold number with the filing's own inline-XBRL tag for the same fiscal year. 334 of 335 agree. The one difference is Bank of America's "cash and cash equivalents" (231,845), against the narrower tagged "cash and due from banks" (28,595). That is a definition choice, and no question depends on it.
3. **Verify text facts** (auditor, HQ, CEO, segments, employees) by reading the quoted page.
4. **Derive:** winners, sums and ratios were computed in code from the verified values. Gold facts for "highest X in sector S" are X for every company in S, plus the final hop.

No gold answer was edited after seeing system output.

## Scoring rules (`src/evaluate.py`)

- Numbers must be within 1%, and ratios within 0.05 percentage points.
- Entities are matched through company aliases (a ticker also matches the company's name).
- For text answers, the key name (city or surname) must appear in the system's answer.
- A question whose plan or execution fails counts as wrong, with precision = recall = 0.
