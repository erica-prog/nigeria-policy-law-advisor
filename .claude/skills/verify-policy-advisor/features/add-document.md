# Add a document

Add a document lets a person attach a PDF or DOCX to a case they own. The page shows the file as a chip and polls until it is ready or failed.

## Sub-features

- `upload-pick` opens the file chooser from `+`.
- `upload-queue` accepts a PDF or DOCX and returns a processing chip.
- `upload-ready` reaches `ready` or a curated `failed` message without spending the server's Claude key.

## How to get to it (user POV)

- Log in and have a writable case open.
- Choose `+`, whose accessible name is `Add documents (PDF or DOCX)`.
- The file input is `#file-input` and accepts `.pdf,.docx`.

## Driving it with pa-verify

Preconditions:

- `pa-verify doctor` printed `pa-verify doctor ok`.
- Logged in as `verify`, with `CASE` set to a writable matter id.
- A synthetic DOCX, not a client document, is available. The unit-test helper `tests/api/helpers.py` `synthetic_docx_bytes` builds one for Foxglove Trading Ltd.
- `OUT` is `.claude/skills/verify-policy-advisor/evidence/add-document`.

- **Upload.** Run `curl -sS -b "$JAR" -o "$OUT/upload.json" -w '%{http_code}' -F 'file=@supply.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document' "$URL/api/matters/$CASE/documents"`. The status is `202`. The body `name` is `supply.docx` and `status` is `queued` or `processing`.
- **Poll.** Run `curl -sS -b "$JAR" "$URL/api/matters/$CASE/documents"` until `supply.docx` has `status` `ready` or `failed`. Record the body as `$OUT/documents.json`.
- **Confirm the chip survived a fresh read.** The same GET is the second view. `ready` includes a positive `chunk_count`. `failed` includes a short `error` string and no stack trace.

## Gotchas

- This path loads the embedding model and writes an index under the disposable data directory. If the model cannot load, report that failure. Do not skip the poll and call the upload verified.
- Web ingestion does not translate, so a missing Claude key is not by itself a failed upload. Do not call `/chat` to prove the upload.
- Scanned PDFs fail with the poppler message when poppler is not installed. That is a real result, not a harness error.
- The shared library rejects uploads with `403` `matter_read_only`.
