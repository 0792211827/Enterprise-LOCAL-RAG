"""Replacing and deleting Documents through the HTTP API, over the real pipeline and a word-matching in-memory index.

The invariants are asserted by searching the index directly (not through the Document row): after a delete nothing of the
Document is found under any query, after a failed replacement the old content still answers, after a successful one only
the new does. Fixtures and helpers come from the document ingestion tests.
"""

from pathlib import Path

from tests.api.routers.test_document_ingestion import (  # noqa: F401
    ADMIN,
    OFFICE_MIME,
    document,
    library,
    make_pptx,
    search,
    upload,
)
from tests.chat_support import stack, world  # noqa: F401

TEXT = "text/plain"
STAFF = {"restrictions": [{"principal_type": "group", "principal_value": "staff"}]}


def replace(library, document_id, name, data, mime=TEXT):
    return library.http.post(f"/api/v1/documents/{document_id}/replace", files={"file": (name, data, mime)})


def chunks_in_index(library, document_id):
    return [c for c in library.search_index.docs.values() if c["document_id"] == document_id]


def added(library, name="guide.txt", data=b"The old guide mentions pelicans", mime=TEXT):
    response = upload(library, name, data, mime)
    assert response.status_code == 202, response.text
    return response.json()["id"]


# --- replace ---------------------------------------------------------------------------------------------------- #
def test_a_replacement_takes_over_keeping_the_documents_identity_and_restrictions(library):
    doc_id = added(library)
    library.http.put(f"/api/v1/documents/{doc_id}/restrictions", json=STAFF)
    before = document(library, doc_id)

    replaced = replace(library, doc_id, "guide-v2.txt", b"The new guide mentions albatrosses")

    assert replaced.status_code == 202, replaced.text
    after = document(library, doc_id)
    assert (after["id"], after["title"], after["knowledge_base_id"]) == (doc_id, before["title"], before["knowledge_base_id"])
    assert (after["status"], after["error"]) == ("COMPLETED", None)
    assert search(library, "pelicans") == []
    assert [hit["document_id"] for hit in search(library, "albatrosses")] == [doc_id]
    kept = chunks_in_index(library, doc_id)
    assert len(kept) == 1 and "staged" not in kept[0] and kept[0]["restriction_principals"] == ["group:staff"]
    restrictions = library.http.get(f"/api/v1/documents/{doc_id}/restrictions").json()["restrictions"]
    assert [(r["principal_type"], r["principal_value"]) for r in restrictions] == [("group", "staff")]


def test_a_replacement_removes_the_old_stored_file_and_chunk_rows(library, world):
    from sqlalchemy import select
    from src.models.document import Document, DocumentChunk

    doc_id = added(library, "deck.pptx", make_pptx([("Old", "pelicans nest here", None)]), OFFICE_MIME)
    with world.factory() as session:
        old_file = session.get(Document, doc_id).file_path
    assert Path(old_file).exists()

    replace(library, doc_id, "deck.pptx", make_pptx([("New", "albatrosses glide here", None)]), OFFICE_MIME)

    with world.factory() as session:
        stored = session.get(Document, doc_id)
        texts = [c.text for c in session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc_id))]
        assert Path(stored.file_path).exists() and stored.file_path != old_file
    assert not Path(old_file).exists()
    assert len(texts) == 1 and "albatrosses" in texts[0]


def test_a_failed_replacement_leaves_the_old_content_answering_and_shows_the_error(library):
    doc_id = added(library)

    replaced = replace(library, doc_id, "broken.pptx", b"PK\x03\x04 broken", OFFICE_MIME)

    assert replaced.status_code == 202, replaced.text
    after = document(library, doc_id)
    assert after["status"] == "COMPLETED"
    assert "PPTX" in after["error"] and after["replacement"]["state"] == "failed"
    assert [hit["document_id"] for hit in search(library, "pelicans")] == [doc_id]
    assert len(chunks_in_index(library, doc_id)) == 1  # the failed attempt left nothing behind


def test_a_failed_replacement_can_be_replaced_again_and_the_error_clears(library):
    doc_id = added(library)
    replace(library, doc_id, "broken.pptx", b"PK\x03\x04 broken", OFFICE_MIME)

    replace(library, doc_id, "guide-v2.txt", b"The new guide mentions albatrosses")

    after = document(library, doc_id)
    assert (after["status"], after["error"], after["replacement"]) == ("COMPLETED", None, None)
    assert [hit["document_id"] for hit in search(library, "albatrosses")] == [doc_id]
    assert search(library, "pelicans") == []


def test_replacing_with_the_same_bytes_or_bytes_held_elsewhere_in_the_knowledge_base_is_refused(library):
    doc_id = added(library)
    other = added(library, "other.txt", b"Something else entirely")

    same = replace(library, doc_id, "again.txt", b"The old guide mentions pelicans")
    elsewhere = replace(library, doc_id, "copy.txt", b"Something else entirely")

    assert same.status_code == elsewhere.status_code == 409
    assert "already exists" in same.json()["detail"] and "already exists" in elsewhere.json()["detail"]
    assert document(library, other)["status"] == "COMPLETED"


def test_a_file_whose_name_matches_a_document_is_a_conflict_pointing_to_replace(library):
    added(library)
    listed = library.http.get(f"/api/v1/knowledge-bases/{library.kb}/documents").json()

    clash = upload(library, "guide.txt", b"A different guide with the same name", TEXT)
    identical = upload(library, "other-name.txt", b"The old guide mentions pelicans", TEXT)

    assert clash.status_code == 409 and "Replace" in clash.json()["detail"] and "guide.txt" in clash.json()["detail"]
    assert identical.status_code == 409 and "already exists" in identical.json()["detail"]
    assert library.http.get(f"/api/v1/knowledge-bases/{library.kb}/documents").json() == listed


def test_a_replacement_that_is_still_being_built_is_never_answered_from(library):
    doc_id = added(library)
    library.search_index.add(
        {"document_id": doc_id, "knowledge_base_id": library.kb, "chunk_text": "half built albatrosses", "revision": "r2", "staged": True}
    )

    assert search(library, "albatrosses") == []
    assert [hit["document_id"] for hit in search(library, "pelicans")] == [doc_id]


# --- delete ----------------------------------------------------------------------------------------------------- #
def test_deleting_a_document_leaves_nothing_of_it_in_the_index_files_or_database(library, world):
    from sqlalchemy import func, select
    from src.models.access import DocumentRestriction
    from src.models.document import Document, DocumentChunk, DocumentChunkLocator

    doc_id = added(library, "deck.pptx", make_pptx([("Old", "pelicans nest here", None)]), OFFICE_MIME)
    library.http.put(f"/api/v1/documents/{doc_id}/restrictions", json=STAFF)
    replace(library, doc_id, "deck2.pptx", make_pptx([("New", "albatrosses glide here", None)]), OFFICE_MIME)
    library.search_index.add({"document_id": doc_id, "knowledge_base_id": library.kb, "chunk_text": "stray revision", "revision": "x", "staged": True})
    with world.factory() as session:
        stored = session.get(Document, doc_id).file_path

    deleted = library.http.delete(f"/api/v1/documents/{doc_id}")

    assert deleted.status_code == 204
    assert chunks_in_index(library, doc_id) == []
    assert search(library, "albatrosses") == [] and search(library, "stray") == []
    assert not Path(stored).exists()
    with world.factory() as session:
        for model in (Document, DocumentChunk, DocumentChunkLocator, DocumentRestriction):
            column = model.id if model is Document else model.document_id
            assert session.scalar(select(func.count()).select_from(model).where(column == doc_id)) == 0, model


def test_a_delete_the_index_cannot_carry_out_changes_nothing(library):
    doc_id = added(library)
    library.search_index.fail_deletes = True

    refused = library.http.delete(f"/api/v1/documents/{doc_id}")

    assert refused.status_code == 502
    assert document(library, doc_id)["status"] == "COMPLETED"
    assert [hit["document_id"] for hit in search(library, "pelicans")] == [doc_id]


def test_deleting_a_knowledge_base_removes_all_its_documents_the_same_way(library, world):
    from src.models.document import Document
    from src.routers import knowledge_bases

    library.app.include_router(knowledge_bases.router)
    first = added(library)
    second = added(library, "second.pptx", make_pptx([("Second", "narwhals swim", None)]), OFFICE_MIME)
    replace(library, second, "second2.pptx", b"PK\x03\x04 broken", OFFICE_MIME)  # leaves a waiting file behind
    with world.factory() as session:
        files = [session.get(Document, d).file_path for d in (first, second)]
        waiting = session.get(Document, second).extra_metadata["replacement"]["file_path"]
    other_kb_chunks = [c for c in library.search_index.docs.values() if c["knowledge_base_id"] != library.kb]

    deleted = library.http.delete(f"/api/v1/knowledge-bases/{library.kb}")

    assert deleted.status_code == 204
    assert [c for c in library.search_index.docs.values() if c["knowledge_base_id"] == library.kb] == []
    assert search(library, "pelicans") == [] and search(library, "narwhals") == []
    assert not any(Path(p).exists() for p in (*(f for f in files if f), waiting))
    assert [c for c in library.search_index.docs.values() if c["knowledge_base_id"] != library.kb] == other_kb_chunks


# --- audit ------------------------------------------------------------------------------------------------------ #
def test_add_replace_and_delete_are_audited_without_document_text(library, world):
    import json

    from sqlalchemy import select
    from src.models import AuditEvent

    doc_id = added(library, "guide.txt", b"CONFIDENTIAL pelicans text")
    replace(library, doc_id, "guide2.txt", b"CONFIDENTIAL albatrosses text")
    library.http.delete(f"/api/v1/documents/{doc_id}")

    with world.factory() as session:
        events = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "document.change").order_by(AuditEvent.occurred_at)))
    assert [e.details["operation"] for e in events] == ["add", "replace", "delete"]
    assert {e.details["document_id"] for e in events} == {doc_id}
    assert events[0].actor_subject == "root" and all(e.outcome == "ok" for e in events)
    assert "CONFIDENTIAL" not in json.dumps([[e.details, e.query_text, e.answer_text] for e in events], default=str)


def test_a_document_cannot_be_retried_or_replaced_again_while_a_replacement_is_in_flight(library, world):
    from src.models.document import Document

    doc_id = added(library)
    with world.factory() as session:
        stored = session.get(Document, doc_id)
        stored.extra_metadata = {"replacement": {"revision": "r2", "state": "processing", "filename": "v2.txt"}}
        session.commit()

    retried = library.http.post(f"/api/v1/documents/{doc_id}/reprocess")
    again = replace(library, doc_id, "v3.txt", b"Yet another version")

    assert retried.status_code == again.status_code == 409
    assert [hit["document_id"] for hit in search(library, "pelicans")] == [doc_id]  # the old content never stopped answering
