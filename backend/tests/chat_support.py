"""Shared fixtures for the employee chat tests: real grants over in-memory SQLite, the chat routers wired like main.py
wires them, a switchable caller, and fakes for the search index, the model and Langfuse. Offline.

Test modules import the fixtures they use (``world``, ``stack``) from here.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import src.models  # noqa: F401  (register models on Base.metadata)
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from src.auth import Principal, Role, get_principal, require_role
from src.config import AuthSettings, Settings
from src.dependencies import (
    get_db_session,
    get_embeddings_service,
    get_langfuse_tracer,
    get_llm_provider,
    get_opensearch_client,
    get_settings,
)
from src.models import KnowledgeBase
from src.models.application import RAGApplication
from src.models.document import Document
from src.models.provider import ModelConfiguration
from src.models.retrieval import RetrievalConfiguration
from src.repositories import GrantRepository
from src.routers import chat, chat_admin
from src.services.audit import DatabaseAuditRecorder

from tests.audit_support import opener, sqlite_session_factory

DOC_TEXT = "Employees get 25 days of annual leave. Unused days carry over."


def caller(subject, *groups, roles=(Role.QUERIER,)):
    return Principal(subject=subject, roles=frozenset(roles), auth_method="jwt", groups=frozenset(groups))


class FakeSpan:
    def __init__(self, sink, kwargs):
        self.sink = sink
        sink.append(("start", kwargs))

    def update(self, **kwargs):
        self.sink.append(("update", kwargs))

    def update_trace(self, **kwargs):
        self.sink.append(("trace", kwargs))

    def end(self, **kwargs):
        self.sink.append(("end", kwargs))


class FakeTracer:
    """What Langfuse would be sent: every span call is recorded."""

    def __init__(self):
        self.sent = []
        self.client = SimpleNamespace(start_span=lambda **kwargs: FakeSpan(self.sent, kwargs))

    def flush(self):
        pass

    def everything_sent(self) -> str:
        return json.dumps(self.sent, default=str)


@pytest.fixture
def world():
    factory = sqlite_session_factory()
    ids = SimpleNamespace(apps={}, kbs={}, docs={})
    with factory() as session:
        for slug in ("hr", "fin"):
            kb = KnowledgeBase(name=slug, slug=slug, index_name=f"{slug}-idx")
            session.add(kb)
            session.commit()
            ids.kbs[slug] = kb
        grants = GrantRepository(session)
        grants.replace_for_knowledge_base(ids.kbs["hr"].id, [("group", "staff")])
        grants.replace_for_knowledge_base(ids.kbs["fin"].id, [("group", "finance")])

        def application(name, kbs, citations=True):
            config = ModelConfiguration(name=f"{name}-m", llm_model="served")
            retrieval = RetrievalConfiguration(name=f"{name}-r", mode="bm25", top_k=3)
            session.add_all([config, retrieval])
            session.commit()
            app = RAGApplication(
                name=name,
                slug=name.lower(),
                description=f"{name} assistant",
                system_prompt="SECRET PROMPT",
                model_configuration_id=config.id,
                retrieval_configuration_id=retrieval.id,
                citations_enabled=citations,
            )
            app.knowledge_bases = [ids.kbs[k] for k in kbs]
            session.add(app)
            session.commit()
            ids.apps[name] = app

        application("Handbook", ["hr"])
        application("Finance", ["fin"])
        application("Both", ["hr", "fin"])
        application("Empty", [])
        application("Quiet", ["hr"], citations=False)
        for slug in ("hr", "fin"):
            doc = Document(knowledge_base_id=ids.kbs[slug].id, title=f"{slug} doc", raw_text=DOC_TEXT, status="COMPLETED")
            session.add(doc)
            session.commit()
            ids.docs[slug] = str(doc.id)
        ids.apps = {name: str(app.id) for name, app in ids.apps.items()}
        ids.kbs = {slug: str(kb.id) for slug, kb in ids.kbs.items()}
    ids.factory = factory
    return ids


@pytest.fixture
def stack(world):
    """The chat routers wired like main.py wires them, with a switchable caller."""
    index = MagicMock()
    index.search_unified = MagicMock(
        return_value={
            "hits": [
                {
                    "chunk_id": "c1",
                    "chunk_text": "Employees get 25 days of annual leave.",
                    "title": "hr doc",
                    "document_id": world.docs["hr"],
                    "knowledge_base_id": world.kbs["hr"],
                    "section_title": "Leave",
                    "chunk_index": 0,
                    "score": 0.9,
                }
            ]
        }
    )
    llm = AsyncMock()
    llm.stream_calls = []

    async def stream(**kwargs):
        llm.stream_calls.append(kwargs)
        for piece in ["25 ", "days."]:
            yield {"response": piece}

    llm.generate_rag_answer_stream = stream
    embeddings = AsyncMock()
    embeddings.embed_query = AsyncMock(return_value=[0.1])
    settings = {"value": Settings(auth=AuthSettings(mode="jwt"))}
    current = {"principal": caller("alice", "staff")}
    tracer = FakeTracer()

    app = FastAPI()
    app.include_router(chat.router, dependencies=[Depends(require_role(Role.QUERIER))])
    app.include_router(chat_admin.router, dependencies=[Depends(require_role(Role.ADMIN))])

    def _session():
        session = world.factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings["value"]
    app.dependency_overrides[get_principal] = lambda: current["principal"]
    app.dependency_overrides[get_opensearch_client] = lambda: index
    app.dependency_overrides[get_embeddings_service] = lambda: embeddings
    app.dependency_overrides[get_llm_provider] = lambda: llm
    app.dependency_overrides[get_langfuse_tracer] = lambda: tracer
    app.state.audit_recorder = DatabaseAuditRecorder(opener(world.factory), level="full")

    return SimpleNamespace(
        http=TestClient(app),
        index=index,
        llm=llm,
        tracer=tracer,
        act_as=lambda principal: current.update(principal=principal),
        use_settings=lambda value: settings.update(value=value),
    )


def frames(response):
    return [json.loads(line[len("data: ") :]) for line in response.text.splitlines() if line.startswith("data: ")]


def start_conversation(stack, world, application="Handbook", **body):
    response = stack.http.post("/api/v1/chat/conversations", json={"application_id": world.apps[application], **body})
    assert response.status_code == 201, response.text
    return response.json()


def ask(stack, conversation_id, query="How much leave?"):
    """Send a message and return its decoded stream frames."""
    response = stack.http.post(f"/api/v1/chat/conversations/{conversation_id}/messages", json={"query": query})
    assert response.status_code == 200, response.text
    return frames(response)


def exchange(stack, world, query="How much leave?", application="Handbook"):
    """One exchange in a fresh conversation: returns ``(conversation_id, frames)``."""
    conversation = start_conversation(stack, world, application)
    return conversation["id"], ask(stack, conversation["id"], query)
