"""The sample-data check: ingest a folder of real files into a scratch Knowledge Base and report how each one went.

An admin or developer runs it locally (``make ingest-samples``) to judge ingestion quality on real documents; it is never
part of CI, which uses small synthetic files. It goes through the same paths as the API and the Connector -- the shared
type registry, the safe replace swap, the delete lifecycle -- so what it reports is what an upload would do:

* a file not seen before is added; one whose content changed is replaced; an unchanged one is skipped (a failed one is
  retried); a file that fails is reported with its reason and never stops the run;
* the web addresses in a ``Links`` document can be added as URL Documents, when their hosts are on the allowlist;
* a few questions can be asked of the scratch Knowledge Base, printing the Citations that come back;
* the scratch Knowledge Base can be removed cleanly.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ContextManager, List, Optional

from sqlalchemy.orm import Session
from src.config import Settings
from src.models.document import Document
from src.models.enums import IngestionStatus
from src.models.ingestion import IngestionJob
from src.models.knowledge_base import KnowledgeBase
from src.repositories import DocumentRepository, IngestionJobRepository, KnowledgeBaseRepository
from src.schemas.api.domain import KnowledgeBaseCreate
from src.schemas.indexing.elements import locator_label
from src.services.retrieval import retrieve
from src.services.slug import slugify

from .lifecycle import delete_knowledge_base, stage_replacement
from .types import UnsupportedDocument, sniff_document_type, store_file, type_for_extension
from .uploads import display_name
from .url_documents import fetch_content, is_url_document, url_source
from .url_fetch import UrlFetchError

SCRATCH_NAME = "Sample data (scratch)"
LINKS_FILE = re.compile(r"^links?\.docx$", re.IGNORECASE)
URL_IN_TEXT = re.compile(r"https?://[^\s<>\"')\]]+")
SNIPPET_CHARS = 160


@dataclass
class FileResult:
    name: str
    action: str  # added | replaced | unchanged | retried | skipped | failed
    status: str = ""  # the Document's status afterwards
    chunks: int = 0
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class QuestionResult:
    question: str
    citations: List[dict]


def links_in(path: Path) -> List[str]:
    """The web addresses in a Word file: its hyperlinks, and any written out in its text. In order, once each."""
    import docx
    from docx.opc.constants import RELATIONSHIP_TYPE

    document = docx.Document(str(path))
    found = [rel.target_ref for rel in document.part.rels.values() if rel.reltype == RELATIONSHIP_TYPE.HYPERLINK and rel.is_external]
    for paragraph in document.paragraphs:
        found.extend(match.rstrip(".,;:") for match in URL_IN_TEXT.findall(paragraph.text))
    return list(dict.fromkeys(url for url in found if url.lower().startswith(("http://", "https://"))))


class SampleIngest:
    """Collaborators are injected, so the same code runs in the command and in tests."""

    def __init__(
        self,
        open_session: Callable[[], ContextManager[Session]],
        ingestion: Any,
        opensearch: Any,
        embeddings: Any,
        settings: Settings,
        url_fetcher: Any = None,
    ) -> None:
        self.open_session = open_session
        self.ingestion = ingestion
        self.opensearch = opensearch
        self.embeddings = embeddings
        self.settings = settings
        self.url_fetcher = url_fetcher

    # ------------------------------------------------------------------------------------------------------------ run
    async def run(self, folder: Path, name: str = SCRATCH_NAME, urls: bool = False) -> List[FileResult]:
        """Ingest every supported file in ``folder`` (and, with ``urls``, the addresses in its Links document)."""
        if not folder.is_dir():
            raise FileNotFoundError(f"{folder} is not a folder.")
        results: List[FileResult] = []
        with self.open_session() as session:
            kb = self._scratch_kb(session, name)
            files = sorted(p for p in folder.rglob("*") if p.is_file() and not p.name.startswith("."))
            for path in files:
                if type_for_extension(path.name) is None:
                    results.append(FileResult(path.name, "skipped", error="not a supported file type"))
                else:
                    results.append(await self._file(session, kb, path))
            if urls:
                for link in dict.fromkeys(u for p in files if LINKS_FILE.match(p.name) for u in self._links(p)):
                    results.append(await self._url(session, kb, link))
            docs = DocumentRepository(session)
            kb.document_count, kb.chunk_count = docs.count(kb.id), docs.count_chunks(kb.id)
            KnowledgeBaseRepository(session).update(kb, {})
        return results

    @staticmethod
    def _links(path: Path) -> List[str]:
        try:
            return links_in(path)
        except Exception:  # noqa: BLE001 - a Links file that cannot be read is not a reason to stop
            return []

    def _scratch_kb(self, session: Session, name: str) -> KnowledgeBase:
        repo = KnowledgeBaseRepository(session)
        slug = slugify(name)
        existing = repo.get_by_slug(slug)
        if existing is not None:
            return existing
        defaults = KnowledgeBaseCreate(name=name, description="Scratch Knowledge Base for `make ingest-samples`. Safe to delete.")
        return repo.create(
            KnowledgeBase(
                name=name,
                slug=slug,
                description=defaults.description,
                index_name=f"{self.settings.opensearch.index_name}-{slug}-{self.settings.opensearch.chunk_index_suffix}",
                embedding_provider=defaults.embedding_provider,
                embedding_model=defaults.embedding_model,
                embedding_dimension=defaults.embedding_dimension,
                retrieval_mode=defaults.retrieval_mode.value,
                default_top_k=defaults.default_top_k,
            )
        )

    # ---------------------------------------------------------------------------------------------------------- files
    async def _file(self, session: Session, kb: KnowledgeBase, path: Path) -> FileResult:
        name = display_name(path.name)
        try:
            raw = path.read_bytes()
            cap = self.settings.ingestion.max_upload_mb
            if len(raw) > cap * 1024 * 1024:
                return FileResult(name, "failed", error=f"larger than the {cap} MB limit (INGESTION__MAX_UPLOAD_MB)")
            document_type = sniff_document_type(path.name, raw)
            checksum = hashlib.sha256(raw).hexdigest()
            repo = DocumentRepository(session)
            existing = repo.get_by_source_name(kb.id, name)
            if existing is None:
                stored = store_file(self.settings, kb.id, checksum, document_type, raw)
                document = repo.create(
                    Document(
                        knowledge_base_id=kb.id,
                        title=name,
                        source_uri=name,
                        content_type=stored.content_type,
                        file_path=stored.file_path,
                        file_size_bytes=len(raw),
                        checksum=checksum,
                        raw_text=stored.raw_text,
                        status=IngestionStatus.QUEUED.value,
                    )
                )
                return await self._ingest(session, kb, document, "added", replacing=False)
            if existing.checksum == checksum and existing.status == IngestionStatus.COMPLETED.value:
                return self._result(name, "unchanged", existing)
            if existing.status == IngestionStatus.COMPLETED.value:
                stage_replacement(self.settings, repo, existing, name, raw, document_type)
                return await self._ingest(session, kb, existing, "replaced", replacing=True)
            action = "retried" if existing.checksum == checksum else "replaced"
            stored = store_file(self.settings, kb.id, checksum, document_type, raw)
            self._start_over(repo, existing, stored, len(raw), checksum)
            return await self._ingest(session, kb, existing, action, replacing=False)
        except UnsupportedDocument as exc:
            return FileResult(name, "failed", error=exc.message)
        except Exception as exc:  # noqa: BLE001 - one file never stops the run
            session.rollback()
            return FileResult(name, "failed", error=str(exc) or exc.__class__.__name__)

    def _start_over(self, repo: DocumentRepository, document: Document, stored: Any, size: int, checksum: str) -> None:
        """A Document nothing of which is answering (an earlier attempt failed) is rebuilt as the same Document."""
        self.opensearch.purge_document_chunks(str(document.id))
        repo.delete_chunks_for_document(document.id)
        document.content_type, document.file_path, document.raw_text = stored.content_type, stored.file_path, stored.raw_text
        document.file_size_bytes, document.checksum = size, checksum
        document.status, document.error, document.chunk_count = IngestionStatus.QUEUED.value, None, 0
        repo.save(document)

    async def _ingest(self, session: Session, kb: KnowledgeBase, document: Document, action: str, replacing: bool) -> FileResult:
        jobs = IngestionJobRepository(session)
        job = jobs.create(IngestionJob(id=uuid.uuid4(), document_id=document.id, knowledge_base_id=kb.id, status=IngestionStatus.QUEUED.value))
        run = self.ingestion.replace_document if replacing else self.ingestion.ingest_document
        await run(document=document, job=job, document_repo=DocumentRepository(session), ingestion_repo=jobs, knowledge_base=kb)
        return self._result(document.source_uri or document.title, action, document)

    @staticmethod
    def _result(name: str, action: str, document: Document) -> FileResult:
        failed_replacement = (document.replacement or {}).get("state") == "failed"
        failed = document.status == IngestionStatus.FAILED.value or failed_replacement
        error = (document.replacement or {}).get("error") if failed_replacement else document.error
        return FileResult(
            name,
            "failed" if failed else action,
            status="COMPLETED (old content kept)" if failed_replacement else document.status,
            chunks=document.chunk_count or 0,
            warnings=document.warnings,
            error=error if failed else None,
        )

    # ----------------------------------------------------------------------------------------------------------- URLs
    async def _url(self, session: Session, kb: KnowledgeBase, link: str) -> FileResult:
        if self.url_fetcher is None:
            return FileResult(link, "skipped", error="URL fetching is not available")
        try:
            url = self.url_fetcher.check(link)
        except UrlFetchError as exc:
            return FileResult(link, "skipped", error=exc.message)
        try:
            repo = DocumentRepository(session)
            existing = repo.get_by_source_name(kb.id, url)
            if existing is None:
                document = repo.create(
                    Document(
                        knowledge_base_id=kb.id,
                        title=url,
                        source_uri=url,
                        extra_metadata={"source": url_source(url)},
                        status=IngestionStatus.QUEUED.value,
                    )
                )
                return await self._ingest(session, kb, document, "added", replacing=False)
            if not is_url_document(existing):
                return FileResult(url, "failed", error="a file of that name already exists")
            if existing.status != IngestionStatus.COMPLETED.value:
                self._start_over_url(repo, existing)
                return await self._ingest(session, kb, existing, "retried", replacing=False)
            content = await fetch_content(self.url_fetcher, url)
            if content.checksum == existing.checksum:
                return self._result(url, "unchanged", existing)
            stage_replacement(self.settings, repo, existing, url, content.page.raw, content.document_type)
            return await self._ingest(session, kb, existing, "replaced", replacing=True)
        except UrlFetchError as exc:
            return FileResult(url, "failed", error=exc.message)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            return FileResult(url, "failed", error=str(exc) or exc.__class__.__name__)

    def _start_over_url(self, repo: DocumentRepository, document: Document) -> None:
        self.opensearch.purge_document_chunks(str(document.id))
        repo.delete_chunks_for_document(document.id)
        document.file_path = document.raw_text = document.checksum = None
        document.status, document.error, document.chunk_count = IngestionStatus.QUEUED.value, None, 0
        repo.save(document)

    # --------------------------------------------------------------------------------------------------- ask / clean
    async def ask(self, questions: List[str], name: str = SCRATCH_NAME, size: int = 3) -> List[QuestionResult]:
        """Search the scratch Knowledge Base for each question and return the Citations that come back."""
        with self.open_session() as session:
            kb = KnowledgeBaseRepository(session).get_by_slug(slugify(name))
            if kb is None:
                raise LookupError(f"There is no scratch Knowledge Base '{name}'. Run the check first.")
            kb_id = str(kb.id)
        results = []
        for question in questions:
            # The operator's own check, so no caller's scope applies; the Knowledge Base is still the only one searched.
            found = await retrieve(
                self.opensearch, self.embeddings, question, scope=None, requested_kb_ids=[kb_id], size=size, fall_back_to_bm25=True
            )
            results.append(QuestionResult(question, [self._citation(hit) for hit in found.hits]))
        return results

    @staticmethod
    def _citation(hit: dict) -> dict:
        text = " ".join((hit.get("chunk_text") or "").split())
        return {
            "title": hit.get("title") or "",
            "where": locator_label(hit.get("locator")) or hit.get("section_title") or "",
            "url": hit.get("source_url"),
            "passage": text[:SNIPPET_CHARS] + ("..." if len(text) > SNIPPET_CHARS else ""),
        }

    def clean(self, name: str = SCRATCH_NAME) -> Optional[int]:
        """Remove the scratch Knowledge Base and everything in it. Returns how many Documents it held, or ``None`` if absent.

        :raises IndexNotUpdated: its chunks could not be removed from the index; nothing was removed.
        """
        with self.open_session() as session:
            repo = KnowledgeBaseRepository(session)
            kb = repo.get_by_slug(slugify(name))
            if kb is None:
                return None
            held = DocumentRepository(session).count(kb.id)
            delete_knowledge_base(session, self.opensearch, kb, repo.delete)
            return held
