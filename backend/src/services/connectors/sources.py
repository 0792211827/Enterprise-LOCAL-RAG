"""Where a Connector reads from. A source lists its files and reads one; everything else (what changed, what to
ingest, what to remove) is the sync's business, so every kind of source behaves identically."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Protocol

from src.config import Settings


class ConnectorError(Exception):
    """The source cannot be used as configured or reached; the whole run is abandoned (a single bad file is not this)."""


@dataclass(frozen=True)
class SourceFile:
    """One file in a source. ``key`` is its path below the source root, with ``/`` separators."""

    key: str
    size: int
    # A cheap change signal that costs no download: if it is unchanged the file is not read at all.
    fingerprint: str


class Source(Protocol):
    def list_files(self) -> List[SourceFile]: ...

    def read(self, key: str) -> bytes: ...


def check_mount_path(path: str, settings: Settings) -> str:
    """The normalised absolute path if it is at or below ``CONNECTOR__MOUNT_ROOT``; otherwise raises ``ConnectorError``.

    Lexical only (it needs no access to the folder, which may be mounted only where the job runs); the job also
    resolves the real path, so a symlink cannot lead out either.
    """
    root = settings.connector.mount_root.strip()
    if not root:
        raise ConnectorError(
            "Mounted-path Connectors are off: set CONNECTOR__MOUNT_ROOT to the directory your shares are mounted under."
        )
    if not os.path.isabs(path):
        raise ConnectorError("The path must be absolute, e.g. /mnt/sources/hr-share.")
    normal, normal_root = os.path.normpath(path), os.path.normpath(root)
    if normal != normal_root and not normal.startswith(normal_root.rstrip("/") + "/"):
        raise ConnectorError(f"The path must be at or below CONNECTOR__MOUNT_ROOT ({normal_root}).")
    return normal


class PathSource:
    """A folder mounted by the operating system. Read-only; symbolic links are never followed."""

    def __init__(self, path: str, settings: Settings):
        self.root = Path(check_mount_path(path, settings))
        # The real locations, so neither a link in the mount root nor one inside the share leads elsewhere.
        real_root = Path(os.path.realpath(settings.connector.mount_root))
        if not self.root.is_dir():
            raise ConnectorError(f"{self.root} is not a directory here: is the share mounted into the job's container?")
        self.real_root = Path(os.path.realpath(self.root))
        if self.real_root != real_root and real_root not in self.real_root.parents:
            raise ConnectorError(f"{self.root} resolves outside CONNECTOR__MOUNT_ROOT.")

    def list_files(self) -> List[SourceFile]:
        files: List[SourceFile] = []
        for directory, dirs, names in os.walk(self.real_root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(names):
                if name.startswith("."):
                    continue
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                files.append(
                    SourceFile(
                        key=path.relative_to(self.real_root).as_posix(),
                        size=stat.st_size,
                        fingerprint=f"{stat.st_size}:{stat.st_mtime_ns}",
                    )
                )
        return files

    def read(self, key: str) -> bytes:
        path = self.real_root / key
        real = Path(os.path.realpath(path))
        if path.is_symlink() or self.real_root not in real.parents:
            raise ConnectorError(f"{key} is not a regular file inside the source")
        return real.read_bytes()
