"""URL Documents: a web page, or a file a link points to, fetched by the server and ingested like an upload.

A URL Document's identity within its Knowledge Base is its normalised address (the fragment removed), kept as the
Document's ``source_uri`` and in ``extra_metadata["source"]``. Adding the same address twice is refused with a pointer
to Refresh; a refresh re-fetches and replaces the content only if it changed, through the same safe swap as Replace.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from src.models.document import Document

from .types import DOCUMENT_TYPES, DocumentType, UnsupportedDocument, sniff_document_type, store_file
from .url_fetch import FetchedPage, NeedsSignIn, UrlFetcher, UrlFetchError, looks_like_login_page

HTML_START = re.compile(rb"<(html|body|head)[\s>]", re.IGNORECASE)


def url_source(url: str) -> dict:
    return {"kind": "url", "url": url}


def is_url_document(document: Document) -> bool:
    return ((document.extra_metadata or {}).get("source") or {}).get("kind") == "url"


@dataclass(frozen=True)
class FetchedContent:
    page: FetchedPage
    document_type: DocumentType
    checksum: str


async def fetch_content(fetcher: UrlFetcher, url: str) -> FetchedContent:
    """Fetch a page and decide, from its bytes and not its headers, what it is. A sign-in page is refused, never indexed.

    :raises UrlFetchError: it could not be fetched, is of no accepted type, or needs sign-in.
    """
    page = await fetcher.fetch(url)
    name = PurePosixPath(urlsplit(page.url).path).name
    try:
        document_type = sniff_document_type(name, page.raw)
    except UnsupportedDocument as exc:
        if not HTML_START.search(page.raw[:4096]):
            raise UrlFetchError(f"The address does not serve a page or file that can be ingested. {exc.message}") from exc
        document_type = DOCUMENT_TYPES["html"]
    if document_type.key == "html" and looks_like_login_page(page.raw):
        raise NeedsSignIn("The page is a sign-in page")
    return FetchedContent(page, document_type, hashlib.sha256(page.raw).hexdigest())


def keep_content(settings: Any, document: Document, content: FetchedContent) -> None:
    """Store what was fetched as the Document's own content, the way an upload's is."""
    stored = store_file(settings, document.knowledge_base_id, content.checksum, content.document_type, content.page.raw)
    document.content_type = stored.content_type
    document.file_path = stored.file_path
    document.raw_text = stored.raw_text
    document.file_size_bytes = len(content.page.raw)
    document.checksum = content.checksum
