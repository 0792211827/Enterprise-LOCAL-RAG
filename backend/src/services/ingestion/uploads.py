"""Safe handling of uploaded files: size cap, stored names and the upload area.

A file name is attacker-controlled input. Nothing from it reaches the file
system: stored files are named by content hash plus an extension that passed an
allowlist-shaped check, and the original name is kept only as a display string
with path components and control characters removed.
"""

import re
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from src.config import Settings

READ_CHUNK = 1024 * 1024
MAX_NAME_LENGTH = 150
_EXTENSION = re.compile(r"^\.[a-z0-9]{1,10}$")
_DISALLOWED = re.compile(r"[^\w .,()+@#&'\-]", re.UNICODE)


def display_name(filename: Optional[str], fallback: str = "document") -> str:
    """The upload's name made safe to store and show: no directories, control characters or markup."""
    # Both separators, whatever the client's OS: the name is not a path we trust.
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _DISALLOWED.sub("_", base).strip(" .")
    return cleaned[:MAX_NAME_LENGTH] or fallback


def safe_extension(filename: Optional[str]) -> str:
    """The file's extension if it looks like one (``.pdf``), else an empty string."""
    suffix = Path((filename or "").replace("\\", "/").rsplit("/", 1)[-1]).suffix.lower()
    return suffix if _EXTENSION.match(suffix) else ""


def upload_root(settings: Settings) -> Path:
    return Path(settings.ingestion.upload_dir) if settings.ingestion.upload_dir else Path(tempfile.gettempdir()) / "rag-uploads"


def stored_path(settings: Settings, knowledge_base_id: object, checksum: str, filename: Optional[str]) -> Path:
    """Where an upload is kept: ``<root>/<kb id>/<checksum><safe extension>``."""
    return upload_root(settings) / str(knowledge_base_id) / f"{checksum}{safe_extension(filename)}"


async def read_capped(file: UploadFile, max_mb: int) -> bytes:
    """Read an upload, refusing with 413 as soon as it exceeds ``max_mb`` (never buffering past the cap)."""
    limit = max_mb * 1024 * 1024
    chunks, size = [], 0
    while True:
        chunk = await file.read(READ_CHUNK)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status_code=413,
                detail=f"The file is larger than the {max_mb} MB upload limit (INGESTION__MAX_UPLOAD_MB).",
            )
        chunks.append(chunk)
