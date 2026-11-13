"""Where a Connector reads from. A source lists its files and reads one; everything else (what changed, what to
ingest, what to remove) is the sync's business, so every kind of source behaves identically."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol
from urllib.parse import urlsplit

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


ENV_REFERENCE_PREFIX = "env:"


def resolve_env_reference(reference: str, what: str) -> str:
    """The value an ``env:NAME`` reference stands for, read from the environment now.

    Only references are accepted -- a literal secret is refused -- so nothing secret is ever stored in the database.
    """
    if not isinstance(reference, str) or not reference.startswith(ENV_REFERENCE_PREFIX) or not reference[len(ENV_REFERENCE_PREFIX) :].strip():
        raise ConnectorError(f"The {what} must be an environment reference like env:MINIO_ACCESS_KEY, never the value itself.")
    name = reference[len(ENV_REFERENCE_PREFIX) :].strip()
    value = os.environ.get(name)
    if not value:
        raise ConnectorError(f"The {what} is set to '{reference}', but the environment variable {name} is not set where the job runs.")
    return value


def check_s3_endpoint(endpoint: str, settings: Settings) -> str:
    """The endpoint URL if it is a plain http(s) address on the operator's allowlist; otherwise raises ``ConnectorError``."""
    parts = urlsplit((endpoint or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConnectorError("The endpoint must be a URL such as https://minio.internal:9000.")
    allowed = [host.lower() for host in settings.connector.allowed_s3_host_values]
    if allowed:
        try:
            port = parts.port
        except ValueError:
            port = None
        host = parts.hostname.lower()
        if host not in allowed and not (port is not None and f"{host}:{port}" in allowed):
            raise ConnectorError(
                f"The endpoint host '{host}' is not allowed. The operator restricts S3 endpoints with "
                f"CONNECTOR__ALLOWED_S3_HOSTS (allowed: {', '.join(sorted(allowed))})."
            )
    return endpoint.strip().rstrip("/")


def normalise_prefix(prefix: Optional[str]) -> str:
    """``hr/policies`` -> ``hr/policies/``; empty stays empty (the whole bucket)."""
    cleaned = (prefix or "").strip().lstrip("/")
    return cleaned if not cleaned or cleaned.endswith("/") else cleaned + "/"


class S3Source:
    """An S3-compatible bucket (AWS S3 and MinIO speak the same API), read-only.

    It contacts only the configured endpoint: the address is required (there is no default AWS endpoint), credentials
    are always given explicitly from ``env:`` references (so the SDK never falls back to looking for them elsewhere,
    such as an instance-metadata service), and objects are addressed path-style (never ``<bucket>.<host>``).
    """

    def __init__(self, config: Dict[str, Any], settings: Settings, client: Any = None):
        self.bucket = (config.get("bucket") or "").strip()
        if not self.bucket:
            raise ConnectorError("An S3 Connector needs a bucket.")
        self.prefix = normalise_prefix(config.get("prefix"))
        self.endpoint = check_s3_endpoint(config.get("endpoint_url", ""), settings)
        if client is None:
            access_key = resolve_env_reference(config.get("access_key_ref", ""), "access key")
            secret_key = resolve_env_reference(config.get("secret_key_ref", ""), "secret key")
            client = self._make_client(config, access_key, secret_key)
        self.client = client

    def _make_client(self, config: Dict[str, Any], access_key: str, secret_key: str) -> Any:
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=config.get("region") or "us-east-1",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=60,
            ),
        )

    def list_files(self) -> List[SourceFile]:
        files: List[SourceFile] = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                key = item["Key"][len(self.prefix) :]
                # Folder placeholders and hidden names are skipped, as they are for a mounted folder.
                if not key or key.endswith("/") or any(part.startswith(".") for part in key.split("/")):
                    continue
                etag = str(item.get("ETag", "")).strip('"')
                files.append(SourceFile(key=key, size=item["Size"], fingerprint=f"{etag}:{item['Size']}"))
        return sorted(files, key=lambda f: f.key)

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self.prefix + key)["Body"].read()
