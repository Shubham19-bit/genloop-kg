
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
