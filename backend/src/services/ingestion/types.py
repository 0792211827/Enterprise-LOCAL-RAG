"""The one list of document types the platform ingests, and how a file's type is decided.

Upload, replace and the Connector all consult this module, so they cannot disagree about what is accepted. A file is
trusted for nothing it says about itself: its type comes from its content where the content has a signature (a PDF
header, the parts inside an Office archive, an HTML document), and from its name or declared type only for plain
text formats that have no signature (TXT, MD, CSV).
"""

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Dict, Optional, Tuple

from src.config import Settings

from .uploads import decode_text, stored_path

MAX_ZIP_ENTRIES = 10_000
MAX_UNCOMPRESSED_MB = 500  # a guard against zip bombs; the upload cap bounds the compressed size
HTML_MARKERS = (b"<!doctype html", b"<html")


@dataclass(frozen=True)
class DocumentType:
    key: str
    label: str
    extension: str  # the one the file is stored under, whatever it was called
    aliases: Tuple[str, ...]  # other extensions that mean the same
    content_type: str
    office_part: Optional[str] = None  # a part that exists inside this kind of Office archive
    inline: bool = False  # plain text, kept on the Document row rather than as a file


DOCUMENT_TYPES: Dict[str, DocumentType] = {
    t.key: t
    for t in (
        DocumentType("pdf", "PDF", ".pdf", (), "application/pdf"),
        DocumentType(
            "docx", "DOCX", ".docx", (), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word/document.xml"
        ),
        DocumentType(
            "pptx", "PPTX", ".pptx", (), "application/vnd.openxmlformats-officedocument.presentationml.presentation", "ppt/presentation.xml"
        ),
        DocumentType("xlsx", "XLSX", ".xlsx", (), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xl/workbook.xml"),
        DocumentType("csv", "CSV", ".csv", (), "text/csv"),
        DocumentType("html", "HTML", ".html", (".htm",), "text/html"),
        DocumentType("txt", "TXT", ".txt", (), "text/plain", inline=True),
        DocumentType("md", "MD", ".md", (".markdown",), "text/markdown", inline=True),
    )
}
ACCEPTED_TYPES = ", ".join(t.label for t in DOCUMENT_TYPES.values())

_BY_EXTENSION = {ext: t for t in DOCUMENT_TYPES.values() for ext in (t.extension, *t.aliases)}
_BY_CONTENT_TYPE = {t.content_type: t for t in DOCUMENT_TYPES.values()}
TEXT_FORMATS = ("txt", "md", "csv", "html")
_BINARY_FORMATS = ("pdf", "docx", "pptx", "xlsx")  # their name alone makes them a Document, to fail with a reason if corrupt


class UnsupportedDocument(Exception):
    """A file that is not an accepted type. ``message`` is for the admin."""

    status_code = 415

    def __init__(self, reason: str):
        self.message = f"{reason} Accepted file types: {ACCEPTED_TYPES}."
        super().__init__(self.message)


def type_for_content_type(content_type: Optional[str]) -> Optional[DocumentType]:
    return _BY_CONTENT_TYPE.get((content_type or "").split(";")[0].strip().lower())


def _office_key(raw: bytes) -> Optional[str]:
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_MB * 1024 * 1024:
            raise UnsupportedDocument("The file is a compressed archive that is too large to open.")
        names = {i.filename for i in infos}
    return next((t.key for t in DOCUMENT_TYPES.values() if t.office_part and t.office_part in names), None)


def _extension_of(filename: Optional[str]) -> str:
    return PurePosixPath((filename or "").replace("\\", "/")).suffix.lower()


def type_for_extension(filename: Optional[str]) -> Optional[DocumentType]:
    """The type a file name's extension says it is (used to skip files a Connector could never ingest)."""
    return _BY_EXTENSION.get(_extension_of(filename))


def sniff_document_type(filename: Optional[str], raw: bytes, declared_content_type: Optional[str] = None) -> DocumentType:
    """What a file really is.

    A PDF header, an Office archive or an HTML document decides it whatever the file is called. A file that names a binary
    format but whose content is empty, truncated or corrupt (no readable text, or a broken archive header) is still that
    format: it becomes a failed Document with a readable reason instead of being turned away. Plain text has no signature,
    so TXT, MD and CSV are decided by the name or, failing that, the declared type.

    :raises UnsupportedDocument: the content and the name are of no accepted type.
    """
    by_name = type_for_extension(filename)
    names_binary = by_name is not None and by_name.key in _BINARY_FORMATS
    if raw.startswith(b"%PDF-"):
        return DOCUMENT_TYPES["pdf"]
    if raw.startswith(b"PK") and zipfile.is_zipfile(BytesIO(raw)):
        key = _office_key(raw)
        if key is None:
            if names_binary:
                return by_name
            raise UnsupportedDocument("The file is an archive, not an Office document.")
        return DOCUMENT_TYPES[key]
    if names_binary and (not raw or raw.startswith(b"PK") or not _is_utf8_text(raw)):
        return by_name  # corrupt or empty: it fails with a reason when read

    if b"\x00" in raw:
        raise UnsupportedDocument("The file is binary, not text.")
    if by_name is not None and by_name.key in TEXT_FORMATS:
        pass  # legacy 8-bit text has always been accepted for the text formats; it is read as Latin-1
    elif not _is_utf8_text(raw):
        raise UnsupportedDocument("The file is not valid UTF-8 text.")
    if raw.lstrip()[:15].lower().startswith(HTML_MARKERS):
        return DOCUMENT_TYPES["html"]
    if by_name is not None and not names_binary:
        return by_name
    declared = type_for_content_type(declared_content_type)
    if declared is not None and declared.key in TEXT_FORMATS:
        return declared
    extension = _extension_of(filename)
    if names_binary:
        raise UnsupportedDocument(f"The file is called '{extension}' but holds plain text, not a {by_name.label} file (an error message saved under that name?).")
    raise UnsupportedDocument(f"The file is plain text but its name ends in '{extension or 'nothing'}'.")


def _is_utf8_text(raw: bytes) -> bool:
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    return True


@dataclass(frozen=True)
class StoredFile:
    """Where a file's content lives once accepted: inline text (TXT, MD) or a file on disk that the parser reads."""

    document_type: DocumentType
    raw_text: Optional[str]
    file_path: Optional[str]

    @property
    def content_type(self) -> str:
        return self.document_type.content_type


def store_file(settings: Settings, knowledge_base_id: object, checksum: str, document_type: DocumentType, raw: bytes) -> StoredFile:
    """Keep a file the way its type needs, under a name nothing in the upload can influence."""
    if document_type.inline:
        return StoredFile(document_type, decode_text(raw), None)
    path = stored_path(settings, knowledge_base_id, checksum, document_type.extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return StoredFile(document_type, None, str(path))
