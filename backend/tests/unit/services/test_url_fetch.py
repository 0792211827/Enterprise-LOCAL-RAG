"""Fetching a page for a URL Document: what the server may and may not be made to contact. A local stub server stands in
for the web and a fake resolver decides what names resolve to; nothing reaches the network."""

import ipaddress

import pytest
from src.services.ingestion.url_fetch import (
    InvalidUrl,
    NeedsSignIn,
    UrlFetcher,
    UrlFetchError,
    UrlRefused,
    audit_form,
    is_special_address,
    normalise_url,
)
from tests.url_support import resolving, site  # noqa: F401

MB = 1024 * 1024


def fetcher(allowed, resolver=None, **kwargs):
    return UrlFetcher(allowed, kwargs.pop("max_bytes", 1 * MB), resolver=resolver, **kwargs)


# --- which addresses are accepted at all ------------------------------------------------------------------------ #
def test_an_empty_allowlist_refuses_every_address_before_any_connection(site):
    site.serve("/page", b"<html>hi</html>")

    with pytest.raises(UrlRefused, match="INGESTION__URL_ALLOWED_HOSTS"):
        fetcher([]).check(site.url("/page"))

    assert site.requests == []


@pytest.mark.parametrize(
    "url",
    ["ftp://docs.example.com/a", "file:///etc/passwd", "gopher://docs.example.com/", "https://user:secret@docs.example.com/", "http://", "not a url", ""],
)
def test_only_plain_http_and_https_addresses_without_credentials_are_accepted(url):
    with pytest.raises(InvalidUrl):
        normalise_url(url)


def test_a_host_off_the_allowlist_is_refused_and_an_exact_or_wildcard_entry_allows():
    guard = fetcher(["docs.example.com", "*.wiki.example.org", "intranet:8443"])

    assert guard.check("https://docs.example.com/a") and guard.check("https://x.wiki.example.org/") and guard.check("https://intranet:8443/")
    for refused in ("https://evil.example.com/", "https://example.org/", "https://wiki.example.org/", "https://intranet/", "https://docs.example.com.evil.net/"):
        with pytest.raises(UrlRefused):
            guard.check(refused)


def test_the_identity_of_a_page_ignores_its_fragment_case_and_default_port():
    assert normalise_url("HTTPS://Docs.Example.com:443/Guide#section-2") == "https://docs.example.com/Guide"
    assert normalise_url("http://docs.example.com") == "http://docs.example.com/"
    assert normalise_url("https://docs.example.com/a?x=1&y=2") == "https://docs.example.com/a?x=1&y=2"


def test_the_audit_form_of_an_address_carries_no_query_string_or_credentials():
    assert audit_form("https://docs.example.com/a/b?token=SECRET") == "https://docs.example.com/a/b"


@pytest.mark.parametrize(
    "address,special",
    [("127.0.0.1", True), ("::1", True), ("169.254.169.254", True), ("fe80::1", True), ("fd00:ec2::254", True), ("100.100.100.200", True),
     ("0.0.0.0", True), ("::ffff:127.0.0.1", True), ("224.0.0.1", True), ("93.184.216.34", False), ("10.1.2.3", False), ("192.168.0.9", False)],
)
def test_loopback_link_local_and_metadata_addresses_are_special(address, special):
    assert is_special_address(ipaddress.ip_address(address)) is special


# --- reaching internal addresses -------------------------------------------------------------------------------- #
async def test_a_wildcard_never_reaches_a_loopback_or_metadata_address(site):
    site.serve("/page", b"<html>secret</html>")
    resolver = resolving({"a.corp.test": ["127.0.0.1"], "b.corp.test": ["169.254.169.254"], "c.corp.test": ["::ffff:127.0.0.1"]})
    guard = fetcher(["*.corp.test"], resolver)

    for name in ("a", "b", "c"):
        with pytest.raises(UrlRefused, match="loopback, link-local or metadata"):
            await guard.fetch(f"http://{name}.corp.test:{site.port}/page")

    assert site.requests == []


async def test_a_host_listed_exactly_may_be_internal_and_is_reached_by_its_address_with_its_own_host_header(site):
    site.serve("/page", b"<html>internal wiki</html>")
    guard = fetcher(["wiki.corp.test"], resolving({"wiki.corp.test": ["127.0.0.1"]}))

    page = await guard.fetch(site.url("/page", host="wiki.corp.test"))

    assert page.raw == b"<html>internal wiki</html>"
    assert site.header_seen("/page", "Host") == f"wiki.corp.test:{site.port}"


async def test_an_address_listed_exactly_is_fetched(site):
    site.serve("/page", b"ok")

    assert (await fetcher(["127.0.0.1"]).fetch(site.url("/page"))).raw == b"ok"


async def test_the_connection_goes_to_the_address_that_was_checked_not_to_a_later_dns_answer(site):
    site.serve("/page", b"<html>pinned</html>")
    resolver = resolving({"wiki.corp.test": ["127.0.0.1"]}, then={"wiki.corp.test": ["10.255.255.1"]})  # rebinds on the second lookup

    page = await fetcher(["wiki.corp.test"], resolver).fetch(site.url("/page", host="wiki.corp.test"))

    assert page.raw == b"<html>pinned</html>" and resolver.calls == ["wiki.corp.test"]


async def test_a_name_that_does_not_resolve_is_a_readable_failure():
    with pytest.raises(UrlFetchError, match="could not be found"):
        await fetcher(["gone.corp.test"], resolving({})).fetch("http://gone.corp.test/")


# --- redirects -------------------------------------------------------------------------------------------------- #
async def test_a_redirect_between_allowed_hosts_is_followed_and_reported_at_its_final_address(site):
    site.redirect("/old", "/new")
    site.serve("/new", b"<html>moved</html>")

    page = await fetcher(["127.0.0.1"]).fetch(site.url("/old"))

    assert page.raw == b"<html>moved</html>" and page.url == site.url("/new")


async def test_every_redirect_hop_is_checked_against_the_allowlist(site):
    site.redirect("/old", "http://169.254.169.254/latest/meta-data/")

    with pytest.raises(UrlRefused, match="not on the allowlist"):
        await fetcher(["127.0.0.1"]).fetch(site.url("/old"))

    assert site.paths() == ["/old"]  # the second request was never made


async def test_a_redirect_to_an_allowed_name_that_resolves_somewhere_internal_is_refused(site):
    site.redirect("/old", f"http://sneaky.corp.test:{site.port}/x")
    guard = fetcher(["127.0.0.1", "*.corp.test"], resolving({"sneaky.corp.test": ["127.0.0.1"]}))

    with pytest.raises(UrlRefused, match="loopback, link-local or metadata"):
        await guard.fetch(site.url("/old"))

    assert site.paths() == ["/old"]


async def test_at_most_three_redirects_are_followed(site):
    for n in range(5):
        site.redirect(f"/r{n}", f"/r{n + 1}")
    site.serve("/r4", b"end")
    site.redirect("/ok3", "/ok2")
    site.redirect("/ok2", "/ok1")
    site.redirect("/ok1", "/ok0")
    site.serve("/ok0", b"landed")

    assert (await fetcher(["127.0.0.1"]).fetch(site.url("/ok3"))).raw == b"landed"
    with pytest.raises(UrlRefused, match="redirects more than 3"):
        await fetcher(["127.0.0.1"]).fetch(site.url("/r0"))


async def test_a_redirect_to_a_sign_in_page_needs_sign_in(site):
    site.redirect("/private", "/account/login?next=/private")

    with pytest.raises(NeedsSignIn, match="needs sign-in"):
        await fetcher(["127.0.0.1"]).fetch(site.url("/private"))

    assert site.paths() == ["/private"]


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_page_needs_sign_in(site, status):
    site.serve("/private", b"denied", status=status)

    with pytest.raises(NeedsSignIn, match=f"HTTP {status}"):
        await fetcher(["127.0.0.1"]).fetch(site.url("/private"))


async def test_other_errors_are_reported_with_their_status(site):
    with pytest.raises(UrlFetchError, match="HTTP 404"):
        await fetcher(["127.0.0.1"]).fetch(site.url("/missing"))


# --- limits ----------------------------------------------------------------------------------------------------- #
async def test_a_page_over_the_size_cap_is_refused_whether_it_declares_its_size_or_not(site):
    site.serve("/big", b"x" * 2000)
    site.serve("/liar", b"x" * 10, **{"Content-Length": "999999"})

    for path in ("/big", "/liar"):
        with pytest.raises(UrlFetchError, match="INGESTION__MAX_UPLOAD_MB"):
            await fetcher(["127.0.0.1"], max_bytes=1000).fetch(site.url(path))


async def test_a_page_that_does_not_answer_in_time_fails_instead_of_hanging(site):
    site.serve("/slow", b"late", delay=2)

    with pytest.raises(UrlFetchError, match="did not answer within"):
        await fetcher(["127.0.0.1"], timeout_seconds=0.3).fetch(site.url("/slow"))
