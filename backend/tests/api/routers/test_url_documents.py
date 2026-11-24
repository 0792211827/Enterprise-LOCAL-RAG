"""URL Documents through the HTTP API: a web page added by address, fetched by the server under the operator's
allowlist, ingested like a file, cited with a link to the page, refreshed and replaced only if it changed. A local stub
server stands in for the web; nothing reaches the network. Fixtures come from the document ingestion tests."""

import json

from sqlalchemy import select
from src.dependencies import get_url_fetcher
from src.models import AuditEvent
from src.services.ingestion.url_fetch import UrlFetcher
from tests.api.routers.test_document_ingestion import document, library, search  # noqa: F401
from tests.chat_support import stack, world  # noqa: F401
from tests.document_files import make_docx
from tests.url_support import resolving, site  # noqa: F401

PAGE = b"""<html><head><title>Support portal</title><script>var secretscript = 1;</script><style>.x{color:red}</style></head>
<body><nav>Home Products Careers</nav><main><h1>Support portal</h1><p>Reset a password from the kiosk screen.</p></main>
<footer>Copyright megacorp</footer></body></html>"""


def allow(library, *hosts, resolver=None):
    fetcher = UrlFetcher(list(hosts), 1024 * 1024, resolver=resolver)
    library.app.dependency_overrides[get_url_fetcher] = lambda: fetcher
    return fetcher


def add_url(library, url, **body):
    return library.http.post(f"/api/v1/knowledge-bases/{library.kb}/documents/url", json={"url": url, **body})


def documents(library):
    return library.http.get(f"/api/v1/knowledge-bases/{library.kb}/documents").json()


def refresh(library, document_id):
    return library.http.post(f"/api/v1/documents/{document_id}/refresh")


def audit_events(world):
    with world.factory() as session:
        return list(session.scalars(select(AuditEvent).where(AuditEvent.action == "document.change").order_by(AuditEvent.occurred_at)))


# --- adding ----------------------------------------------------------------------------------------------------- #
def test_a_page_is_fetched_reduced_to_its_main_text_and_cited_with_a_link_to_it(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")

    added = add_url(library, site.url("/support#reset"))

    assert added.status_code == 202, added.text
    stored = document(library, added.json()["id"])
    assert (stored["status"], stored["error"]) == ("COMPLETED", None)
    assert (stored["source_kind"], stored["source_url"], stored["title"]) == ("url", site.url("/support"), site.url("/support"))
    [hit] = search(library, "kiosk")
    assert hit["source_url"] == site.url("/support")
    for noise in ("Careers", "secretscript", "megacorp", "color"):
        assert search(library, noise) == [], noise


def test_a_title_may_be_given(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")

    added = add_url(library, site.url("/support"), title="Kiosk guide")

    assert document(library, added.json()["id"])["title"] == "Kiosk guide"


def test_a_page_that_links_a_word_file_is_ingested_as_that_file_whatever_the_header_says(library, site):
    site.serve("/files/policy", make_docx([("h1", "Leave"), ("p", "Employees receive twentyfive vacation days")]), content_type="text/plain")
    allow(library, "127.0.0.1")

    added = add_url(library, site.url("/files/policy"))

    stored = document(library, added.json()["id"])
    assert stored["status"] == "COMPLETED" and stored["content_type"].endswith("wordprocessingml.document")
    assert "Leave" in search(library, "twentyfive")[0]["locator_text"]


def test_a_linked_pdf_is_ingested_as_a_pdf(library, site, monkeypatch):
    from src.schemas.indexing.elements import DocumentElement, Locator
    from src.schemas.pdf_parser.models import ParserType, PdfContent

    async def parse_pdf(path):
        elements = [DocumentElement(text="Warranty lasts eighteen months", locator=Locator(kind="text", page=3))]
        return PdfContent(raw_text=elements[0].text, elements=elements, parser_used=ParserType.DOCLING)

    monkeypatch.setattr(library.parser_service.docling_parser, "parse_pdf", parse_pdf)
    site.serve("/manual", b"%PDF-1.4 stub", content_type="text/html")
    allow(library, "127.0.0.1")

    added = add_url(library, site.url("/manual"))

    assert document(library, added.json()["id"])["content_type"] == "application/pdf"
    assert search(library, "eighteen")[0]["locator_text"] == "page 3"


# --- refused ---------------------------------------------------------------------------------------------------- #
def test_nothing_is_fetched_when_the_operator_has_listed_no_host(library, site):
    site.serve("/support", PAGE)
    allow(library)  # the default: an empty allowlist
    before = documents(library)

    refused = add_url(library, site.url("/support"))

    assert refused.status_code == 403 and "INGESTION__URL_ALLOWED_HOSTS" in refused.json()["detail"]
    assert site.requests == [] and documents(library) == before


def test_a_host_off_the_allowlist_and_a_bad_address_are_refused_without_creating_a_document(library, site):
    allow(library, "docs.example.com")
    before = documents(library)

    off_list = add_url(library, site.url("/support"))
    not_http = add_url(library, "file:///etc/passwd")
    with_password = add_url(library, "https://user:pw@docs.example.com/")

    assert (off_list.status_code, not_http.status_code, with_password.status_code) == (403, 400, 400)
    assert documents(library) == before


def test_a_name_that_leads_to_an_internal_address_becomes_a_failed_document_and_is_never_contacted(library, site):
    site.serve("/support", PAGE)
    allow(library, "*.corp.test", resolver=resolving({"wiki.corp.test": ["127.0.0.1"]}))

    added = add_url(library, site.url("/support", host="wiki.corp.test"))

    stored = document(library, added.json()["id"])
    assert stored["status"] == "FAILED" and "loopback, link-local or metadata" in stored["error"]
    assert site.requests == []


def test_adding_the_same_address_twice_points_to_refresh(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")
    first = add_url(library, site.url("/support")).json()["id"]
    before = documents(library)

    again = add_url(library, site.url("/support#other-part"))

    assert again.status_code == 409
    assert "Refresh" in again.json()["detail"] and first in again.json()["detail"]
    assert documents(library) == before


def test_a_page_that_needs_sign_in_fails_the_document_with_a_clear_message(library, site):
    site.serve("/private", b"denied", status=401)
    site.redirect("/members", "/user/login?back=/members")
    site.serve("/portal", b'<html><body><h1>Welcome</h1><form><input type="password" name="p"></form></body></html>')
    allow(library, "127.0.0.1")

    stored = [document(library, add_url(library, site.url(path)).json()["id"]) for path in ("/private", "/members", "/portal")]

    for failed in stored:
        assert failed["status"] == "FAILED" and "needs sign-in" in failed["error"]
    assert search(library, "Welcome") == []  # a login page is never indexed as content


def test_a_page_that_is_missing_or_not_a_document_fails_with_the_reason(library, site):
    site.serve("/photo", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, content_type="image/png")
    allow(library, "127.0.0.1")

    missing = document(library, add_url(library, site.url("/missing")).json()["id"])
    photo = document(library, add_url(library, site.url("/photo")).json()["id"])

    assert missing["status"] == "FAILED" and "HTTP 404" in missing["error"]
    assert photo["status"] == "FAILED" and "PDF" in photo["error"]


def test_a_failed_fetch_is_retried_with_reprocess_once_the_page_is_there(library, site):
    allow(library, "127.0.0.1")
    failed = add_url(library, site.url("/later")).json()["id"]
    assert document(library, failed)["status"] == "FAILED"
    site.serve("/later", PAGE)

    retried = library.http.post(f"/api/v1/documents/{failed}/reprocess")

    assert retried.status_code == 202, retried.text
    assert document(library, failed)["status"] == "COMPLETED" and len(search(library, "kiosk")) == 1


# --- refresh ---------------------------------------------------------------------------------------------------- #
def test_a_refresh_of_an_unchanged_page_changes_nothing(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")
    doc_id = add_url(library, site.url("/support")).json()["id"]
    chunks = [c["chunk_text"] for c in library.search_index.docs.values()]

    refreshed = refresh(library, doc_id)

    assert refreshed.status_code == 200 and refreshed.json()["changed"] is False
    assert [c["chunk_text"] for c in library.search_index.docs.values()] == chunks
    assert document(library, doc_id)["replacement"] is None


def test_a_refresh_of_a_changed_page_replaces_the_content_keeping_identity_and_restrictions(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")
    doc_id = add_url(library, site.url("/support")).json()["id"]
    library.http.put(
        f"/api/v1/documents/{doc_id}/restrictions", json={"restrictions": [{"principal_type": "group", "principal_value": "staff"}]}
    )
    site.serve("/support", PAGE.replace(b"Reset a password from the kiosk", b"Unlock an account from the terminal"))

    refreshed = refresh(library, doc_id)

    assert refreshed.status_code == 202 and refreshed.json()["changed"] is True
    stored = document(library, doc_id)
    assert (stored["status"], stored["error"], stored["source_url"]) == ("COMPLETED", None, site.url("/support"))
    assert search(library, "kiosk") == []
    [hit] = search(library, "terminal")
    assert (hit["document_id"], hit["source_url"]) == (doc_id, site.url("/support"))
    assert [c["restriction_principals"] for c in library.search_index.docs.values()] == [["group:staff"]]


def test_a_failed_refresh_leaves_the_old_content_answering(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")
    doc_id = add_url(library, site.url("/support")).json()["id"]
    site.serve("/support", b"gone", status=500)

    failed = refresh(library, doc_id)

    assert failed.status_code == 502 and "HTTP 500" in failed.json()["detail"]
    assert document(library, doc_id)["status"] == "COMPLETED" and len(search(library, "kiosk")) == 1


def test_only_url_documents_can_be_refreshed_and_they_are_not_replaced_by_a_file(library, site):
    site.serve("/support", PAGE)
    allow(library, "127.0.0.1")
    page = add_url(library, site.url("/support")).json()["id"]
    uploaded = library.http.post(
        f"/api/v1/knowledge-bases/{library.kb}/documents", files={"file": ("notes.txt", b"Remember the lighthouse", "text/plain")}
    ).json()["id"]

    assert refresh(library, uploaded).status_code == 409
    replaced = library.http.post(f"/api/v1/documents/{page}/replace", files={"file": ("x.txt", b"other", "text/plain")})
    assert replaced.status_code == 409 and "Refresh" in replaced.json()["detail"]


# --- audit ------------------------------------------------------------------------------------------------------ #
def test_a_page_wrapped_in_a_form_keeps_its_text(library, site):
    site.serve("/webforms", b"<html><body><form action='/x'><h1>Tickets</h1><p>Open a ticket from the kiosk screen</p><input name='q'></form></body></html>")
    allow(library, "127.0.0.1")

    stored = document(library, add_url(library, site.url("/webforms")).json()["id"])

    assert stored["status"] == "COMPLETED" and len(search(library, "kiosk")) == 1


def test_deleting_a_page_is_audited_without_its_query_string(library, site, world):
    site.serve("/support?token=SECRET", PAGE)
    allow(library, "127.0.0.1")
    doc_id = add_url(library, site.url("/support?token=SECRET")).json()["id"]

    library.http.delete(f"/api/v1/documents/{doc_id}")

    assert "SECRET" not in json.dumps([e.details for e in audit_events(world)])


def test_add_and_refresh_are_audited_without_page_text_or_query_strings(library, site, world):
    site.serve("/support?token=SECRET", PAGE)
    allow(library, "127.0.0.1")
    doc_id = add_url(library, site.url("/support?token=SECRET")).json()["id"]
    site.serve("/support?token=SECRET", PAGE + b"<!-- changed -->")
    refresh(library, doc_id)
    refresh(library, doc_id)

    events = audit_events(world)

    assert [e.details["operation"] for e in events] == ["add", "refresh", "refresh"]
    assert [e.details.get("changed") for e in events] == [None, True, False]
    assert all(e.details["url"] == site.url("/support") for e in events)
    text = json.dumps([[e.details, e.query_text, e.answer_text] for e in events], default=str)
    assert "SECRET" not in text and "kiosk" not in text
