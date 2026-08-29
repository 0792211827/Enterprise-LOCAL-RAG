"""Unit tests for the generic platform domain model."""
from src.db.interfaces.postgresql import Base
from src.models import (
    Document,
    DocumentChunk,
    IngestionJob,
    IngestionStatus,
    KnowledgeBase,
    ModelConfiguration,
    ModelProvider,
    RAGApplication,
    RAGStrategy,
    RetrievalConfiguration,
    RetrievalMode,
)


def test_all_platform_tables_registered():
    tables = set(Base.metadata.tables.keys())
    expected = {
        "knowledge_bases",
        "documents",
        "document_chunks",
        "rag_applications",
        "application_knowledge_bases",
        "model_providers",
        "model_configurations",
        "retrieval_configurations",
        "ingestion_jobs",
    }
    assert expected.issubset(tables)


def test_paper_table_removed():
    assert "papers" not in Base.metadata.tables


def test_knowledge_base_defaults_are_local():
    kb = KnowledgeBase(name="HR Policies", slug="hr-policies", index_name="kb-hr")
    # Defaults resolve at flush time; verify column defaults are local-first.
    assert KnowledgeBase.__table__.c.embedding_provider.default.arg == "huggingface"
    assert KnowledgeBase.__table__.c.embedding_model.default.arg == "BAAI/bge-m3"
    assert KnowledgeBase.__table__.c.retrieval_mode.default.arg == RetrievalMode.HYBRID.value
    assert kb.name == "HR Policies"


def test_document_lifecycle_status_default():
    doc = Document(knowledge_base_id=None, title="Handbook.pdf")
    assert Document.__table__.c.status.default.arg == IngestionStatus.QUEUED.value
    assert doc.title == "Handbook.pdf"


def test_rag_application_strategy_default():
    assert RAGApplication.__table__.c.rag_strategy.default.arg == RAGStrategy.TRADITIONAL.value


def test_entities_instantiate():
    assert DocumentChunk(document_id=None, knowledge_base_id=None, chunk_index=0, text="x")
    assert ModelProvider(name="local-ollama", kind="llm", provider_type="ollama", model="llama3.2:1b")
    assert ModelConfiguration(name="default")
    assert RetrievalConfiguration(name="default")
    assert IngestionJob(document_id=None, knowledge_base_id=None)
