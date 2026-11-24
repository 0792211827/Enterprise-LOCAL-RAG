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
