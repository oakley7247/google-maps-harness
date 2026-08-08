# =============================================================================
# http_client.py — the HTTPS transport every outbound call in this project
# shares, and the only place the API key is ever attached to a request.
#
# Part of: google-maps-harness. Called by: maps_api.py. Calls: the six Google
# Maps Platform hosts named in HOST_AUTH_STYLE.
# Security: this module holds the properties that must not drift between its
# callers — TLS verification on with a 1.2 floor, proxies suppressed, redirects
# refused, a host allowlist, response bodies bounded by both size and wall
# clock, the per-tool-call budget charged before the socket opens, and no
# exception carrying a request URL or header. The last one is load-bearing here
# in a way it is not in a bearer-token project: three of these APIs take the key
# as a query parameter, so on those hosts the URL *is* a credential. Nothing in
# this module ever places a URL into a message; callers pass a `safe_label`
# instead, and runtime.py scrubs the key out of anything that escapes anyway.
# =============================================================================
"""Bounded, proxy-free, redirect-free HTTPS to Google Maps Platform."""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .budget import charge

# How Google wants the key on each host. Two mechanisms exist because Google
# ships two generations of API: the newer resource-oriented services accept
# `X-Goog-Api-Key`, while the older web services documented under
# maps.googleapis.com accept only a `key` query parameter. Each entry below was
# read off that API's own reference page rather than assumed from the host
# name, because assuming is how a credential ends up somewhere nobody checked
# (ledger LL-26).
AUTH_HEADER = "header"
AUTH_QUERY = "query"

HOST_AUTH_STYLE: dict[str, str] = {
    # Geocoding API v4 — documents `X-Goog-Api-Key` alongside the query form.
    "geocode.googleapis.com": AUTH_HEADER,
    # Places API (New) — header, and a field mask header is mandatory.
    "places.googleapis.com": AUTH_HEADER,
    # Routes API — header, and a field mask header is mandatory.
    "routes.googleapis.com": AUTH_HEADER,
    # Address Validation API — its reference documents the query form only.
    "addressvalidation.googleapis.com": AUTH_QUERY,
    # Air Quality API — its reference documents the query form only.
    "airquality.googleapis.com": AUTH_QUERY,
    # Time Zone and Elevation, the two legacy web services this server uses.
    # Their references document no header form.
    "maps.googleapis.com": AUTH_QUERY,
}

# The largest answer any of these APIs gives is a route with a full step list or
# a 20-place search with every Atmosphere field, both comfortably under 1 MiB.
# The cap bounds the bytes read from the socket, which in turn bounds the work
# json.loads can be asked to do — a byte limit is not an object limit, so the
# parse below is the layer this one protects, and nothing beyond it re-expands
# the payload (reliability standard, bounded resources rule 8).
MAX_RESPONSE_BYTES = 4 * 1024 * 1024

# Read granularity. An upper bound per iteration, not a demand: the loop uses
# read1, which returns whatever has arrived rather than waiting for the full
# amount.
_READ_CHUNK_BYTES = 64 * 1024

# Longest error text taken from a remote response. Google chooses this string,
# so it is untrusted length as well as untrusted content.
_MAX_REMOTE_MESSAGE_CHARS = 200


class HttpError(Exception):
    """An HTTPS call did not produce a usable result."""


class HttpUnavailableError(HttpError):
    """The host could not be reached, redirected, or answered too slowly."""


class HttpResponse:
    """One completed HTTP response: its status, parsed body, and Retry-After."""

    def __init__(self, status: int, body: Any, retry_after: str | None) -> None:
        """Build a response record.

        Args:
            status: The HTTP status code.
            body: The parsed JSON body, or None when the response carried none.
            retry_after: The raw `Retry-After` header, or None when absent.
        """
        self.status = status
        self.body = body
        self.retry_after = retry_after

    def error_detail(self) -> tuple[str | None, str | None]:
        """Return the remote error's status and message, if the body carries them.

        Google answers an error on the newer services with
        `{"error": {"status": ..., "message": ...}}`, and on the legacy web
        services with a top-level `{"status": ..., "error_message": ...}` — the
        latter inside an HTTP 200, which is why maps_api.py checks the body's
        own status rather than only the HTTP one. Both shapes are read here so
        callers do not each re-derive them.

        Returns:
            A tuple of the machine-readable status and the human message. Either
            may be None. Both are truncated, because the remote host chooses
            their length.
        """
        body = self.body
        if not isinstance(body, dict):
            return None, None
        inner = body.get("error")
        if isinstance(inner, dict):
            return _clip(inner.get("status")), _clip(inner.get("message"))
        return _clip(body.get("status")), _clip(body.get("error_message") or _clip(inner))


def _clip(value: Any) -> str | None:
    """Return a remote-supplied string bounded in length, or None.

    Args:
        value: A value read out of a remote JSON body.

    Returns:
        The value as a truncated string, or None when it was absent.
    """
    if value is None:
        return None
    return str(value)[:_MAX_REMOTE_MESSAGE_CHARS]


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect instead of following it."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Return None so urllib raises rather than following the redirect."""
        # SECURITY: every request this project makes carries the API key, in a
        # header on three hosts and in the query string on the other three.
        # urllib re-sends both to whatever host a 3xx Location names, so
        # following one would hand a billable credential to a stranger after a
        # DNS takeover or a compromised endpoint. A machine-to-machine API call
        # has no legitimate reason to redirect (ledger LL-14).
        return None


def _build_tls_context() -> ssl.SSLContext:
    """Return the TLS context every request in this project uses.

    Returns:
        A context that verifies certificates and hostnames, with TLS 1.2 as the
        floor.
    """
    context = ssl.create_default_context()
    # SECURITY: verification is on by default here; these two lines assert it
    # rather than assume it, so a future edit that swaps the constructor cannot
    # silently disable the check. TLS 1.0 and 1.1 are set aside explicitly
    # because Google requires 1.2 or better anyway, which makes the floor free.
    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # NOTE: the trust store is the platform's, loaded by create_default_context.
    # `SSL_CERT_FILE` and `SSL_CERT_DIR` in the inherited environment can point
    # it elsewhere. That is not defended against here: anyone able to set an
    # environment variable for this process can already read the settings file
    # holding the key, so a CA override buys them nothing they do not already
    # have. Proxy variables are different and *are* suppressed below — those
    # redirect traffic rather than merely re-pointing trust.
    return context


class BoundedHttpClient:
    """Calls Google Maps Platform under hard bounds, holding the only copy of the key."""

    def __init__(self, api_key: str, timeout_seconds: float) -> None:
        """Build the transport.

        Args:
            api_key: The Maps Platform API key. Attached to every request by
                this class and by nothing else, so no caller ever handles it.
            timeout_seconds: Ceiling on the connect and read time for one
                request. The effective timeout is the smaller of this and
                whatever the tool call's wall-clock budget has left.
        """
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        # SECURITY: the empty ProxyHandler suppresses urllib's default, which
        # seeds itself from http_proxy/HTTPS_PROXY/ALL_PROXY in the inherited
        # environment. Without it one environment variable routes every request
        # through an arbitrary host, bypassing the allowlist below entirely
        # (ledger LL-28).
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler,
            urllib.request.HTTPSHandler(context=_build_tls_context()),
        )

    def request(
        self,
        host: str,
        path: str,
        *,
        safe_label: str,
        method: str = "GET",
        params: dict[str, str] | None = None,
        json_body: Any = None,
        field_mask: str | None = None,
    ) -> HttpResponse:
        """Perform one request and return its status, parsed body, and Retry-After.

        Args:
            host: One of the hosts in HOST_AUTH_STYLE. Callers pass a constant.
            path: The absolute path, already percent-encoded by the caller for
                any segment that came from input.
            safe_label: A credential-free description of the request, used in
                every error message this method raises. Never the URL: on three
                of these hosts the URL carries the key.
            method: The HTTP method.
            params: Query parameters, before the key is added.
            json_body: A JSON-serializable body, or None.
            field_mask: The value for `X-Goog-FieldMask`. Always a constant
                chosen by this server — see the SECURITY note below.

        Returns:
            The response. A non-2xx status is returned rather than raised, so
            callers can read Google's own error status and decide.

        Raises:
            BudgetExceededError: This tool call has spent its request or time
                allowance.
            HttpUnavailableError: The host was unreachable, timed out,
                redirected, or answered with something that was not JSON.
            HttpError: The host is not one this client may call, or the
                response exceeded the size cap.
        """
        auth_style = self._auth_style(host, safe_label)

        query = dict(params or {})
        if auth_style == AUTH_QUERY:
            query["key"] = self._api_key
        url = f"https://{host}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)

        encoded = None if json_body is None else json.dumps(json_body).encode("utf-8")
        request = urllib.request.Request(url, data=encoded, method=method)
        if auth_style == AUTH_HEADER:
            request.add_header("X-Goog-Api-Key", self._api_key)
        if field_mask is not None:
            # SECURITY: the mask is always one of the constants in
            # runtime.py — no model-supplied text reaches a request header.
            # A header whose value came from a tool argument is a header
            # injection waiting for the first newline, and here it would also
            # be a way to ask Google for the most expensive fields on every
            # call.
            request.add_header("X-Goog-FieldMask", field_mask)
        if encoded is not None:
            request.add_header("Content-Type", "application/json")

        # SECURITY: charged immediately before the socket opens, never after the
        # response returns. A budget tested a step later authorizes the work it
        # was meant to prevent (ledger LL-20), and a budget charged on success
        # only never advances under sustained failure (ledger LL-27).
        timeout = min(self._timeout_seconds, charge(safe_label))

        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = self._read_bounded(response, safe_label, timeout)
                return HttpResponse(
                    int(response.status),
                    self._parse(raw, safe_label),
                    response.headers.get("Retry-After"),
                )
        except urllib.error.HTTPError as error:
            return self._handle_http_error(error, safe_label, timeout)
        except urllib.error.URLError as error:
            # NOTE: `error.reason` is an OSError or an ssl.SSLError, never the
            # request URL or headers, so it is safe to surface. It is what
            # distinguishes "no network" from "certificate rejected", which the
            # operator needs.
            raise HttpUnavailableError(f"Could not reach {safe_label}: {error.reason}") from error
        except TimeoutError as error:
            raise HttpUnavailableError(
                f"{safe_label} did not answer within {timeout:.1f} seconds."
            ) from error

    def _auth_style(self, host: str, safe_label: str) -> str:
        """Return how the key is attached on this host, refusing any other host.

        Args:
            host: The host about to be called.
            safe_label: A credential-free description of the request.

        Returns:
            AUTH_HEADER or AUTH_QUERY.

        Raises:
            HttpError: The host is not one this client may call.
        """
        # SECURITY: the last gate before a credential-bearing request opens a
        # socket, and an allowlist rather than a check for something bad. Every
        # caller passes a module-level constant, so this can only fire on a
        # programming error — which is exactly when it matters, since the
        # mistake it catches is sending the API key somewhere new. The lookup
        # is on the lowercased host with no port, so `Places.GoogleAPIs.com:443`
        # cannot slip past a case-sensitive match.
        style = HOST_AUTH_STYLE.get(host.lower())
        if style is None:
            raise HttpError(f"Refused: {safe_label} names a host this client may not call.")
        return style

    def _handle_http_error(
        self, error: urllib.error.HTTPError, safe_label: str, timeout: float
    ) -> HttpResponse:
        """Turn an HTTPError into a returned response, or raise on a redirect.

        Args:
            error: The raised HTTPError, which is itself an open response.
            safe_label: A credential-free description of the request.
            timeout: The wall clock this request was granted.

        Returns:
            The status, parsed error body, and Retry-After header.

        Raises:
            HttpUnavailableError: The host redirected, or the error body was
                not usable JSON.
        """
        try:
            if error.code in (301, 302, 303, 307, 308):
                raise HttpUnavailableError(
                    f"{safe_label} redirected. Refused: following it would send "
                    "the API key to another host."
                )
            raw = self._read_bounded(error, safe_label, timeout)
            return HttpResponse(
                error.code, self._parse(raw, safe_label), error.headers.get("Retry-After")
            )
        finally:
            # HTTPError holds an open socket; closing it here rather than
            # leaving it to the garbage collector keeps the connection from
            # lingering on every error path.
            error.close()

    @staticmethod
    def _parse(raw: bytes, safe_label: str) -> Any:
        """Parse a response body as JSON.

        Args:
            raw: The body bytes.
            safe_label: A credential-free description of the request.

        Returns:
            The parsed body, or None when the body was empty.

        Raises:
            HttpUnavailableError: The body was not valid UTF-8 JSON.
        """
        if not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise HttpUnavailableError(
                f"{safe_label} answered with something that was not JSON."
            ) from error

    def _read_bounded(self, response: Any, safe_label: str, timeout: float) -> bytes:
        """Read a response body under both a size cap and a wall-clock deadline.

        Args:
            response: The open HTTP response.
            safe_label: A credential-free description of the request.
            timeout: The wall clock this request was granted.

        Returns:
            The body bytes.

        Raises:
            HttpUnavailableError: The host was still sending after the timeout
                had elapsed across the whole read.
            HttpError: The body exceeded the size cap.
        """
        # SECURITY: the socket timeout applies per read *operation* and resets
        # on every byte received, so a check between reads is not by itself a
        # bound: `read(n)` blocks until all n bytes arrive, and a peer trickling
        # one byte just inside each window keeps a single call blocked far past
        # the deadline, which is never re-evaluated (ledger LL-30). Two things
        # make the bound real. read1 returns as soon as any data is available,
        # which is what lets the deadline check below actually fire; and the
        # socket's own timeout is tightened to the remaining budget before each
        # read, so a stalled read raises rather than waiting.
        read = getattr(response, "read1", None) or response.read

        deadline = time.monotonic() + timeout
        # One byte past the cap, so an over-large body is detectable rather
        # than silently truncated into JSON that fails to parse for the wrong
        # reason.
        remaining = MAX_RESPONSE_BYTES + 1
        chunks: list[bytes] = []
        while remaining > 0:
            left = deadline - time.monotonic()
            if left <= 0:
                raise HttpUnavailableError(
                    f"{safe_label} was still sending after {timeout:.1f} seconds."
                )
            self._tighten_socket_timeout(response, left)
            try:
                chunk = read(min(_READ_CHUNK_BYTES, remaining))
            except TimeoutError as error:
                raise HttpUnavailableError(
                    f"{safe_label} stalled partway through its response."
                ) from error
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)

        body = b"".join(chunks)
        if len(body) > MAX_RESPONSE_BYTES:
            raise HttpError(
                f"The response from {safe_label} exceeded {MAX_RESPONSE_BYTES} "
                "bytes and was refused. Ask for fewer results per page."
            )
        return body

    @staticmethod
    def _tighten_socket_timeout(response: Any, seconds: float) -> None:
        """Lower the underlying socket's timeout to the time budget left.

        Args:
            response: The open HTTP response.
            seconds: Remaining budget; the socket will not wait longer.
        """
        # NOTE: urllib exposes no public way to adjust the timeout mid-response,
        # so this reaches through http.client's buffered reader to the socket.
        # It is best effort by design — the private chain can be absent on a
        # wrapped or mocked response, and the small chunk size above is what
        # keeps the bound meaningful when that happens.
        sock = getattr(getattr(response, "fp", None), "raw", None)
        sock = getattr(sock, "_sock", None)
        if sock is None:
            return
        try:
            sock.settimeout(max(0.05, seconds))
        except OSError:
            # A closed or detached socket needs no timeout; the read that
            # follows fails on its own and is reported by the caller.
            return
