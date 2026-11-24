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

## Images

Content that exists only inside a screenshot becomes findable when the optional vision model is on (`VLM__ENABLED=true`, see `.env.example`).

* **What is found.** The pictures embedded in a PDF (page), PPTX (slide) or DOCX (heading path) are read straight from the file with the text around them, not through Docling's layout models. Icons and rules (either side under `INGESTION__MIN_IMAGE_PX`, default 100) are skipped, and so is a picture that sits in three or more places of the Document (a logo, a background). At most `INGESTION__MAX_IMAGES_PER_DOCUMENT` (default 200) are described, in document order.
* **What it becomes.** Each description is its own passage, opening with where the picture sits (`Image on slide 7:`), embedded and indexed like any other passage. Its locator has `kind: "image"`, so a Citation says **image on slide 7**, **image on page 14** or **image in Policy > Leave**. The prompt asks for the text visible in the picture and what it shows, and carries the page's or slide's own text for context.
* **Pictures never fail a Document.** With the vision model off, or when it fails (one picture that fails is skipped; three in a row and the rest are given up on), the Document is ingested with its text and shows a warning: how many images were not described and why. A file made only of pictures fails with "No text found" (and says the vision model is off); with the model on, its descriptions are its text.
* **Adding the missing descriptions.** Turn the vision model on (restart the API) and **Reprocess** the Document: the warning goes and the descriptions are indexed. A Connector re-reads a file only when it changes, so reprocess its Documents from the admin UI.

## URL Documents

An admin adds a web page by address (`POST /api/v1/knowledge-bases/{id}/documents/url`, **Add URL** in the Documents tab). The server fetches it, and it is ingested like a file; the Document's identity within its Knowledge Base is its normalised address (lower-case host, no fragment, no default port).

* **Deny by default.** Only hosts in `INGESTION__URL_ALLOWED_HOSTS` are fetched (`docs.example.com`, `*.example.com` for subdomains, `host:port`); empty refuses every address (`403`). Only `http` and `https` addresses without credentials are accepted.
* **Internal addresses.** Loopback, link-local and cloud-metadata addresses (and the unspecified, multicast and reserved ranges) are reached only by a host listed *exactly*; a wildcard never reaches them, so a DNS record that points a subdomain at `169.254.169.254` is refused. Private ranges (10/8, 192.168/16) are reachable by any allowlisted host.
* **Pinned and re-checked.** The name is resolved once, every address it gave is checked, and the connection goes to one of those addresses (with the right `Host` header and TLS server name), so DNS cannot change its answer between the check and the connection. Every redirect (at most 3) is a new request, checked the same way.
* **One page.** No crawling, the upload size cap (`INGESTION__MAX_UPLOAD_MB`) and 30 seconds for the whole fetch. The type is decided from the bytes, not the `Content-Type` header, so a link to a PDF or Office file is ingested as that type. HTML is reduced to its main text: the `main` or `article` part if there is one, else the body, without navigation, headers, footers, side bars, forms, scripts and styles.
* **Sign-in.** A `401` or `403`, a redirect to a sign-in page, or a page with a password field fails the Document with "needs sign-in"; a login page is never indexed.
* **Failures are Documents.** The address is checked when it is added (`400` invalid, `403` not allowed); the fetch itself runs with the ingestion, so a page that is missing, too big, slow or behind a login is a failed Document with the reason. **Retry** fetches it again.
* **Duplicates and refresh.** Adding an address that is already in the Knowledge Base is refused (`409`) with a pointer to **Refresh** (`POST /api/v1/documents/{id}/refresh`). Refresh fetches the page and compares its content hash: unchanged answers `200 {"changed": false}` and does nothing; changed goes through the same safe swap as Replace (`202 {"changed": true}`), keeping id, title and restrictions. There is no scheduled refresh. A URL Document cannot be replaced with a file (`409`).
* **Citations.** Index entries carry `source_url` (a new additive mapping field, added in place when the API starts), and Citations carry it too: the chat panel and the playground link to the page. The audit log records `add` and `refresh` with the address minus its query string.

## Sample-data check

`make ingest-samples` ingests the real sample folder (`data/File Examples`) into a scratch Knowledge Base and prints per-file status, chunk counts, warnings and errors. It is local and opt-in, never part of CI. See the README (*Sample-data check*). It goes through the same type registry, replace swap and delete lifecycle as the API: changed files are replaced, unchanged ones skipped, failed ones retried; `CLEAN=1` removes the scratch Knowledge Base; `URLS=1` adds the links in `Links.docx` whose hosts are allowlisted; `ASK=1` asks `evals/sample-data-questions.yaml` (a Golden set) and prints the Citations.
