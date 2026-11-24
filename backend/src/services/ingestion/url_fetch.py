"""Fetching one web page for a URL Document, safely.

The server is asked to fetch an address an admin typed, so this is where it must not become a way to probe the
network it runs in:

* deny by default: only hosts on the operator's allowlist (``INGESTION__URL_ALLOWED_HOSTS``) are fetched, and an empty
  list refuses everything;
* loopback, link-local and cloud-metadata addresses are reached only by a host the operator listed *exactly*; a
  wildcard entry (``*.example.com``) never reaches them, so a DNS record pointing a subdomain at ``169.254.169.254`` is
  refused;
* the name is resolved once, the check is made on the addresses it gave, and the connection goes to one of those very
  addresses (pinned), so the name cannot resolve somewhere else between the check and the connection;
* every redirect is a new request that is checked the same way, at most ``MAX_REDIRECTS`` of them;
* only http and https, one page (nothing is crawled), the upload size cap, and ``TIMEOUT_SECONDS`` for the whole fetch.

The type of what comes back is not taken from the response: the caller sniffs the bytes.
"""

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

import httpx

MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 30
MAX_URL_LENGTH = 2048
USER_AGENT = "Enterprise-LOCAL-RAG-url-fetcher/1.0"
DEFAULT_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
METADATA_ADDRESSES = frozenset(
    ipaddress.ip_address(a) for a in ("169.254.169.254", "169.254.170.2", "100.100.100.200", "fd00:ec2::254")
)
# Where a site sends someone who has to sign in first.
LOGIN_REDIRECT = re.compile(r"(?<![a-z])(log-?in|sign-?in|sign-?on|sso|saml2?|oauth2?|authorize|auth|session/new)(?![a-z])", re.IGNORECASE)
PASSWORD_FIELD = re.compile(rb"<input[^>]+type\s*=\s*[\"']?password", re.IGNORECASE)

Resolver = Callable[[str, int], Awaitable[List[str]]]


class UrlFetchError(Exception):
    """A page could not be fetched. ``message`` is for the admin."""

    status_code = 502

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class InvalidUrl(UrlFetchError):
    status_code = 400


class UrlRefused(UrlFetchError):
    """The address is not one the operator allows the server to fetch."""

    status_code = 403


class NeedsSignIn(UrlFetchError):
    status_code = 422

    def __init__(self, detail: str = "The page answered that sign-in is required"):
        super().__init__(f"{detail}. This page needs sign-in, which a URL Document cannot do; add the file itself instead.")


@dataclass(frozen=True)
class FetchedPage:
    url: str  # where the content finally came from, after redirects
    raw: bytes


def normalise_url(url: str) -> str:
    """The identity of a page within a Knowledge Base: lower-case scheme and host, no fragment, no default port.

    :raises InvalidUrl: not an http(s) address, or one that carries credentials.
    """
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidUrl(f"Give a web address of at most {MAX_URL_LENGTH} characters.")
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as exc:
        raise InvalidUrl(f"'{url[:80]}' is not a valid web address.") from exc
    if parts.scheme.lower() not in DEFAULT_PORTS:
        raise InvalidUrl("Only http and https addresses can be added.")
    if not host:
        raise InvalidUrl("The web address has no host.")
    if parts.username is not None or parts.password is not None:
        raise InvalidUrl("A web address with a user name or password in it is not accepted.")
    try:
        host = host if ":" in host else host.encode("idna").decode("ascii")  # an IPv6 literal is not a name
    except UnicodeError as exc:
        raise InvalidUrl("The host name is not valid.") from exc
    scheme = parts.scheme.lower()
    shown = f"[{host}]" if ":" in host else host
    netloc = shown if port in (None, DEFAULT_PORTS[scheme]) else f"{shown}:{port}"
    return urlunsplit(SplitResult(scheme, netloc, parts.path or "/", parts.query, ""))


def audit_form(url: str) -> str:
    """The address as it may be written to the audit log: no credentials and no query string (it may carry a token)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        shown = f"[{host}]" if ":" in host else host
        return urlunsplit(SplitResult(parts.scheme, f"{shown}:{parts.port}" if parts.port else shown, parts.path, "", ""))
    except ValueError:
        return "(invalid address)"


def looks_like_login_page(raw: bytes) -> bool:
    """An HTML page with a password field is a sign-in page, however it was reached."""
    return bool(PASSWORD_FIELD.search(raw[:300_000]))


def is_special_address(address: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    """Loopback, link-local, cloud metadata, and the unspecified, multicast and reserved ranges."""
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return (
        address.is_loopback
        or address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
        or address in METADATA_ADDRESSES
    )


async def resolve(host: str, port: int) -> List[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


@dataclass(frozen=True)
class _Entry:
    host: str
    wildcard: bool
    port: Optional[int]

    @classmethod
    def parse(cls, value: str) -> "_Entry":
        value = value.strip().lower()
        port: Optional[int] = None
        if value.startswith("["):  # [::1]:8080
            host, _, rest = value[1:].partition("]")
            port = cls._port(rest[1:]) if rest.startswith(":") else None
        elif value.count(":") == 1:
            value, _, tail = value.partition(":")
            host, port = value, cls._port(tail)
        else:
            host = value
        wildcard = host.startswith("*.")
        host = host[2:] if wildcard else host
        if host and ":" not in host and not host.isascii():
            try:
                host = host.encode("idna").decode("ascii")  # a name is compared as the address is normalised
            except UnicodeError:
                pass
        return cls(host, wildcard, port)

    @staticmethod
    def _port(text: str) -> int:
        """The port an entry names. One that is not a number matches no port, rather than silently allowing any."""
        return int(text) if text.isdigit() else -1

    def matches(self, host: str, port: int) -> bool:
        if self.port is not None and self.port != port:
            return False
        return host.endswith("." + self.host) if self.wildcard else host == self.host


class UrlFetcher:
    """Fetches one page. ``resolver`` is injectable so tests (and nothing else) can decide what a name resolves to."""

    def __init__(
        self,
        allowed_hosts: Sequence[str],
        max_bytes: int,
        resolver: Optional[Resolver] = None,
        timeout_seconds: float = TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self.entries = [_Entry.parse(host) for host in allowed_hosts if host.strip()]
        self.max_bytes = max_bytes
        self.resolver = resolver or resolve
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects

    @property
    def enabled(self) -> bool:
        return bool(self.entries)

    # ------------------------------------------------------------------------------------------------------ policy
    def _host_entry(self, url: str) -> Optional[_Entry]:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or DEFAULT_PORTS[parts.scheme]
        exact = next((e for e in self.entries if not e.wildcard and e.matches(host, port)), None)
        return exact or next((e for e in self.entries if e.matches(host, port)), None)

    def check(self, url: str) -> str:
        """Is this address one the server may fetch (before any network)? Returns its normal form.

        :raises InvalidUrl: not a usable address.
        :raises UrlRefused: URL fetching is off, or the host is not on the allowlist.
        """
        normal = normalise_url(url)
        if not self.entries:
            raise UrlRefused("Adding a URL is turned off: no host is on the allowlist (INGESTION__URL_ALLOWED_HOSTS).")
        if self._host_entry(normal) is None:
            host = urlsplit(normal).netloc
            raise UrlRefused(f"{host} is not on the allowlist of hosts a URL may be fetched from (INGESTION__URL_ALLOWED_HOSTS).")
        return normal

    async def _addresses(self, url: str) -> List[str]:
        """The addresses the connection may go to: resolved once, and checked."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or DEFAULT_PORTS[parts.scheme]
        exact = any(not e.wildcard and e.matches(host.lower(), port) for e in self.entries)
        try:
            ipaddress.ip_address(host)
            candidates = [host]
        except ValueError:
            try:
                candidates = await self.resolver(host, port)
            except OSError as exc:
                raise UrlFetchError(f"The host {host} could not be found.") from exc
        usable = []
        for candidate in candidates:
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if exact or not is_special_address(address):
                usable.append(candidate)
        if not usable:
            if candidates:
                raise UrlRefused(
                    f"{host} points at a loopback, link-local or metadata address, which is only reached by a host listed "
                    "exactly in INGESTION__URL_ALLOWED_HOSTS (not by a wildcard)."
                )
            raise UrlFetchError(f"The host {host} could not be found.")
        return usable

    # ------------------------------------------------------------------------------------------------------ fetch
    async def fetch(self, url: str) -> FetchedPage:
        """Fetch the one page at ``url`` and return its bytes.

        :raises UrlFetchError: it could not be fetched; subclasses say why (refused, needs sign-in, ...).
        """
        current = self.check(url)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                for hop in range(self.max_redirects + 1):
                    outcome = await self._get(current)
                    if isinstance(outcome, bytes):
                        return FetchedPage(url=current, raw=outcome)
                    if hop == self.max_redirects:
                        raise UrlRefused(f"The page redirects more than {self.max_redirects} times.")
                    current = self._redirect_target(current, outcome)
        except TimeoutError as exc:
            raise UrlFetchError(f"The page did not answer within {int(self.timeout_seconds)} seconds.") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def _redirect_target(self, current: str, location: str) -> str:
        target = urljoin(current, location)
        try:
            normal = normalise_url(target)
        except InvalidUrl as exc:
            raise UrlRefused(f"The page redirects somewhere that is not allowed: {exc.message}") from exc
        parts = urlsplit(normal)
        if LOGIN_REDIRECT.search(f"{parts.hostname or ''}{parts.path}") or (parts.hostname or "").startswith(("login.", "sso.", "accounts.")):
            raise NeedsSignIn("The page redirects to a sign-in page")
        if self._host_entry(normal) is None:
            raise UrlRefused(f"The page redirects to {parts.netloc}, which is not on the allowlist (INGESTION__URL_ALLOWED_HOSTS).")
        return normal

    async def _get(self, url: str):
        """One request, with the connection pinned to the address the host resolved to. Returns the body or a redirect's Location."""
        parts = urlsplit(url)
        host = parts.hostname or ""
        addresses = await self._addresses(url)
        headers = {"Host": parts.netloc, "User-Agent": USER_AGENT, "Accept": "text/html,application/pdf,application/*;q=0.9,*/*;q=0.5"}
        for number, address in enumerate(addresses):
            try:
                return await self._request(parts, host, address, headers)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if number == len(addresses) - 1:  # only addresses that passed the check are ever tried
                    raise UrlFetchError(f"The page could not be fetched: {exc.__class__.__name__}: {exc}"[:300]) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _request(self, parts: SplitResult, host: str, address: str, headers: dict):
        shown = f"[{address}]" if ":" in address else address
        pinned = urlunsplit(SplitResult(parts.scheme, f"{shown}:{parts.port}" if parts.port else shown, parts.path or "/", parts.query, ""))
        try:
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False, timeout=self.timeout_seconds) as client:
                async with client.stream("GET", pinned, headers=headers, extensions={"sni_hostname": host}) as response:
                    status = response.status_code
                    if status in REDIRECT_STATUSES and response.headers.get("location"):
                        return response.headers["location"]
                    if status in (401, 403):
                        raise NeedsSignIn(f"The page answered HTTP {status}")
                    if not 200 <= status < 300:
                        raise UrlFetchError(f"The page answered HTTP {status}.")
                    declared = response.headers.get("content-length", "")
                    if declared.isdigit() and int(declared) > self.max_bytes:
                        raise UrlFetchError(self._too_large())
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise UrlFetchError(self._too_large())
                    return bytes(body)
        except (UrlFetchError, httpx.ConnectError, httpx.ConnectTimeout):
            raise  # the last two: the caller tries the next checked address
        except httpx.HTTPError as exc:
            raise UrlFetchError(f"The page could not be fetched: {exc.__class__.__name__}: {exc}"[:300]) from exc

    def _too_large(self) -> str:
        return f"The page is larger than the {self.max_bytes // (1024 * 1024)} MB limit (INGESTION__MAX_UPLOAD_MB)."


def make_url_fetcher(settings) -> UrlFetcher:
    """The fetcher the platform's settings describe: the operator's allowlist and the upload size cap."""
    return UrlFetcher(settings.ingestion.url_allowed_host_values, settings.ingestion.max_upload_mb * 1024 * 1024)
