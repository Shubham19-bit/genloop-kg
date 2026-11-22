# Gold-answer extraction prompt (for Gemini chat)

**How to use:** open a new Gemini chat, paste everything in the "PROMPT" box below, then upload ONE
10-K PDF from `data\pdfs\` (e.g. `AAPL_10K_FY2025.pdf`). Copy Gemini's JSON reply into
`benchmark\gold_raw.jsonl` as ONE line. Repeat for each company (start a new chat every ~5 PDFs so
it doesn't mix companies up).

Then run `python scripts/check_gold.py` (or ask Claude to): it compares every value with the filing's
XBRL tags and flags anything that doesn't match. Fix flagged values by reading the PDF yourself.

Order to do them in (the benchmark needs all 35, most-used first):
JPM BAC WFC C GS MS V MA AXP · JNJ PFE MRK ABBV LLY UNH TMO AMGN · WMT COST TGT HD LOW NKE MCD KO PEP ·
AAPL MSFT GOOGL AMZN META NVDA ORCL ADBE CRM

---

## PROMPT (copy everything between the lines)

---
You are helping build a verified benchmark from an SEC 10-K filing. I will upload one 10-K PDF.
Read it and return ONLY one line of JSON (no markdown, no code fences) with exactly these keys.

Rules:
1. Use ONLY the uploaded PDF. Never use outside knowledge or memory of the company.
2. Take numbers from the primary consolidated financial statements in Item 8 (Consolidated Statement
   of Income / Operations / Earnings, Consolidated Balance Sheet / Statement of Financial Condition,
   Consolidated Statement of Cash Flows), NOT from MD&A summaries or segment tables.
3. Use the MOST RECENT fiscal year column only.
4. Report each number exactly as printed (e.g. "416,161") plus the scale stated on that statement
   ("millions" / "thousands"). Negative numbers in parentheses -> write with a minus sign.
5. "page" = the PDF page number as shown by a PDF viewer (1 = the first page of the file), NOT the
   page number printed in the footer of the page.
6. "quote" = the exact row or sentence from the page (max 25 words) that contains the value.
7. If a fact is not reported in this filing, write null for value, page and quote. Do NOT guess.
   (Examples: banks usually have no "operating income" line; many companies report no R&D line.)
8. Definitions:
   - revenue: the TOTAL revenue line (e.g. "Total net sales", "Total revenues", "Total net revenue",
     "Total revenue, net of interest expense"), not a sub-line like "Products".
   - operating_income: the line literally called operating income / income from operations /
     operating profit / operating earnings. null if no such line.
   - net_income: net income attributable to the company (after noncontrolling interests).
   - diluted_eps: diluted earnings per share for net income (total, not continuing operations only
     unless that is the only one shown).
   - total_liabilities: the "Total liabilities" line; null if the balance sheet has no such line.
   - total_equity: "Total equity" including noncontrolling interests if shown; otherwise total
     stockholders'/shareholders' equity.
   - long_term_debt: the NON-current long-term debt line on the balance sheet.
   - operating_cash_flow: net cash provided by (used in) operating activities.
   - employees: total headcount stated in Item 1 (Human Capital); full number, e.g. 166000.
   - auditor: firm that signed the "Report of Independent Registered Public Accounting Firm".
   - headquarters: city and state from the cover page "Address of principal executive offices",
     formatted "City, State".
   - ceo: name of the person signing as Chief Executive Officer / Principal Executive Officer on the
     Signatures page.
   - segments: list of the company's REPORTABLE segments as named in the segment note.

Output format (one line, this exact shape):
{"ticker": "<ticker>", "fiscal_year_end": "<YYYY-MM-DD>",
 "revenue": {"value": "...", "scale": "...", "page": 0, "quote": "..."},
 "operating_income": {...}, "net_income": {...}, "diluted_eps": {...},
 "research_and_development": {...}, "total_assets": {...}, "total_liabilities": {...},
 "total_equity": {...}, "cash_and_equivalents": {...}, "long_term_debt": {...},
 "operating_cash_flow": {...}, "employees": {"value": "...", "page": 0, "quote": "..."},
 "auditor": {"value": "...", "page": 0, "quote": "..."},
 "headquarters": {"value": "...", "page": 0, "quote": "..."},
 "ceo": {"value": "...", "page": 0, "quote": "..."},
 "segments": {"value": ["..."], "page": 0, "quote": "..."}}

The ticker of the uploaded filing is: <TYPE THE TICKER HERE, e.g. AAPL>
---

---

## Additional rules used for the final gold set (pasted after the prompt above)

These were added after the first 10 filings, which showed where the original prompt was ambiguous. All 35 companies were then re-extracted under the same rules.

```
A. cash_and_equivalents: use the line literally called "Cash and cash equivalents" or "Cash and
   equivalents". If there is no such line (common at banks), use "Cash and due from banks".
B. segments: if the company has ONE segment and the filing gives it no name, set "value": null
   (do NOT invent a description). Keep abbreviations as printed. Exclude "All Other" / "Corporate".
C. headquarters: always "City, State" with the state written in full.
D. auditor: standard firm name; "page" = the audit report page. If the signature is an image, take
   the firm name from that report or the page after it and add "note": "signature is an image".
E. Column order: check the year header; some companies put the oldest year first.
F. Nulls are correct answers. Do not add up other lines or substitute a different line (except A).
G. If you made a judgment call, add "note": "<one short sentence>" inside that field's object.
H. "page" = the PDF page index, never the number printed in the footer.
I. Take the ticker from the uploaded file name.
J. (Key-page files for long filings) Each page is stamped "ORIGINAL PDF PAGE n": use that n as
   "page". If a fact is not on the included pages, return null with "note": "not in key pages".
```
