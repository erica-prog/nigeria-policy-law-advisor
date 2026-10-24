# data/us — provenance

United States federal authority for the `us-library` collection (docs/23 slice
P2-B, decision D8). Every file is a **US government work in the public domain**
(17 U.S.C. § 105). Nothing here is a client document.

The files are derived text, not the raw downloads: `scripts/fetch_us_corpus.py`
fetches each source, converts it to the plain shape the parsers in
`policy_advisor/ingestion/parse_us.py` read, and writes `manifest.json` with the
URL, retrieval time and SHA-256 of the raw bytes. Each file repeats its own
provenance in `# key: value` header lines. To refresh, run the script and commit
the diff together with the new manifest.

| File | Source | Form fetched | Locators |
|---|---|---|---|
| `8-cfr-part-245.txt` | eCFR versioner API, title 8 part 245, version date `2026-10-01` | XML, one `§` section per `DIV8` | `8 C.F.R. § 245.1(a)` (paragraph level; numbered sub-paragraphs when a paragraph is long) |
| `uscis-pm-vol7-part-b-ch1.txt`, `…ch2.txt`, `…ch3.txt` | USCIS Policy Manual, Volume 7 Part B chapters 1 to 3, "current as of" date in each header | HTML, the Guidance tab | `USCIS-PM Vol. 7, Pt. B, Ch. 2.A.1` (one chunk per deepest heading; the Footnotes block is kept in the file but not chunked) |
| `zadvydas-v-davis-533-us-678.txt` | Library of Congress scan of the bound United States Reports, volume 533 | PDF, text layer via `pdftotext` | `533 U.S. 678, 687` (one chunk per reporter page) |

## The reporter page derivation

The bound-volume text layer carries the running heads ("Cite as: 533 U. S. 678
(2001)", "Opinion of the Court") but not the page numbers. The fetch script
therefore numbers pages by position: PDF page *n* is reporter page
`first_page + n - 1`, with `first_page` taken from the citation. This was checked
against the syllabus, which cites "Pp. 687-688" for the habeas holding: Part II
of the opinion, where that holding begins, falls on the page the derivation
labels 687. A re-fetch of a different opinion should repeat that check.

## What is deliberately not here

- The raw XML, HTML and PDF (large; reproducible from the manifest).
- Any text whose registry row says it may not be stored (`docs/24`). All three
  sources are public domain and their rows say `may_store_text: true`.
- Later chapters of Part B and the rest of Title 8: enough for the golden set,
  not a complete library. Connectors (slices P2-C, P2-D) are the route to the
  rest, not more committed text.
