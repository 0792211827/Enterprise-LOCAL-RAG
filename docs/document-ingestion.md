# Document ingestion: types and locators

## Supported types

One registry (`backend/src/services/ingestion/types.py`) decides what is accepted. Upload and the Connector both use it, so they cannot disagree.

| Type | How it is read | A Citation names |
|---|---|---|
| PDF | Docling | the heading and page (`Warranty, page 14`) |
| DOCX | Docling | the heading path (`Policy > Leave`) |
| PPTX | Docling for the slides, python-pptx for speaker notes | the slide (`slide 7`); a slide's notes are searchable with it |
| XLSX | openpyxl | the sheet and rows (`sheet Withdraw Codes, rows 12-30`) |
| CSV | the standard `csv` module | the rows (`rows 2-30`) |
| HTML | Docling | the heading path |
| TXT, MD | kept as text | nothing (no locator) |

A file's type is decided from its content (a PDF header, the parts inside an Office archive, an opening `<html>` tag), not from its name or declared type: a deck renamed `.txt` is read as a deck, an HTML page named `.xlsx` as HTML. TXT, MD and CSV have no signature, so their name (or declared type, if the name has no usable extension) decides. A file of no accepted type is refused with `415` and a message naming the accepted types. A file whose name says PDF, DOCX, PPTX or XLSX but whose content is empty or a broken archive is still stored as a Document and fails with a readable reason, so it can be retried or deleted. A file with no extractable text fails with "No text found".

## Passages and locators

* A slide is one passage where it fits (`CHUNKING__CHUNK_SIZE` words), otherwise it is split between paragraphs; every part says which slide it is.
* A spreadsheet or CSV is cut into runs of rows; every passage repeats the header row, and its locator is the rows it holds (as numbered in the workbook).
* A PDF, Word or HTML section (or the part of it on one page) is one passage that opens with its heading path.

A **locator** is stored in two places: in a new `document_chunk_locators` table keyed by chunk id (ADR-0003: no existing table is altered), and as a `locator` object on the chunk in the OpenSearch index. Citations (the chat Citations panel, the application playground sources and search hits) read it from the index and carry `locator_text`; the excerpts handed to the model are labelled with it too, so an answer can say where to look. The legacy `/ask` and OpenAI-compatible `citations` lists are title-only by contract and are unchanged. A chunk without a locator is shown as it always was.

## Adopting locators on an existing install

Nothing breaks on upgrade: the `locator` field is added to an existing index in place when the API starts (`ensure_additive_fields`), and chunks indexed earlier keep answering without a position.

To get locators for existing content:

1. Run `make reindex` so the rebuilt index carries the full current mapping (see `docs/decisions/0004-opensearch-and-knn-engine.md`).
2. Reprocess each Document that should gain locators (`POST /api/v1/documents/{id}/reprocess`, or **Reprocess** in the admin Documents tab). Reprocessing re-reads the stored file; if the file has since been removed by upload cleanup, the Document keeps answering without locators until it is added again. A Connector re-reads a file only when it changes, so to refresh a Connector's Documents reprocess them, or re-upload.

There is no back-fill: locators cannot be derived from text that was already flattened.

## Replace and delete

* **Replace** (`POST /api/v1/documents/{id}/replace`, **Replace** in the Documents tab) keeps the Document's id, title, Knowledge Base and restrictions. The new file is ingested as a new *revision*: its chunks go into the index hidden (`staged: true`, excluded from every search) beside the old ones. When it is ready the revision becomes current (the staged flag is cleared, then every other chunk of the Document is removed, then the chunk rows, locators and stored file are swapped in one database commit). If it fails or times out, what it indexed is removed, the old content keeps answering, and the Document shows the error (`replacement.state = failed`); **Retry** re-runs it from the stored file.
* A file whose **name** matches an existing Document is refused with `409` pointing to Replace; **identical bytes** still answer "already exists". A Document being ingested cannot be replaced until it finishes.
* **Delete** removes the Document's index entries for every revision and *verifies the index holds none*, then its stored file(s), chunks, locators, restrictions and jobs. If the index cannot be emptied nothing is changed (`502`). Deleting a Knowledge Base does the same for all its Documents. The Connector replaces changed files and removes vanished ones through the same paths.
* Add, replace and delete write a `document.change` audit event (ids, file name, type, size, digest; never document text).

Chunks indexed before revisions existed carry no `revision` field and are simply treated as the old revision at the first replacement. The additive fields `revision` and `staged` are added to an existing index when the API starts.

## Bulk upload, concurrency and recovery

* The API takes one file per request; the Documents tab sends a batch two at a time and shows each Document's own status (Queued, Processing, Completed, Failed with the reason). One failure never fails or blocks the others; failed Documents offer **Retry** (`POST /api/v1/documents/{id}/reprocess`) and **Delete**.
* Ingestion runs in the API process, `INGESTION__CONCURRENCY` Documents at a time (default 2); the rest wait as Queued. Each Document has `INGESTION__DOCUMENT_TIMEOUT_SECONDS` (default 900, 0 = no limit); past it the Document fails with that reason and whatever it indexed is removed.
* A restart loses what was in flight, so at startup Documents left Queued or Processing (and replacements left unfinished) are queued again, after removing what the interrupted attempt had indexed. Documents created by a Connector run are left to that run, which retries them itself.

The time limit stops waiting for a Document and fails it, but a parse already running in a worker thread cannot be interrupted, so it may use CPU until it ends on its own. If the old content cannot be removed from the index after a replacement commits (the index is failing), the new content stays current and the Document shows a warning to reprocess it.
