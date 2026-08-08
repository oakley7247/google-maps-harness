# =============================================================================
# test_http_client.py — the transport's controls, asserted on the wire.
#
# Part of: google-maps-harness test suite.
# The opener is replaced with a recorder rather than mocked at the method level,
# so these tests read the Request object urllib would actually have sent — which
# is where the API key either is or is not.
# =============================================================================
"""Host allowlist, key placement, redirects, proxies, and the size cap."""

import io
import os
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from google_maps_harness.budget import BudgetExceededError, begin_call, end_call
from google_maps_harness.http_client import (
    MAX_RESPONSE_BYTES,
    BoundedHttpClient,
    HttpError,
    HttpUnavailableError,
    _NoRedirectHandler,
)

from .support import FAKE_API_KEY


class _FakeResponse(io.BytesIO):
    """A response body with the attributes urllib's context manager needs."""

    def __init__(self, payload: bytes) -> None:
        """Build a 200 response carrying the payload.

        Args:
            payload: The body bytes.
        """
        super().__init__(payload)
        self.status = 200
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "_FakeResponse":
        """Return self, so `with opener.open(...)` works."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the buffer."""
        self.close()

    def read1(self, size: int = -1) -> bytes:
        """Read what is available, which for a buffer is what was asked for.

        Args:
            size: Bytes to read.

        Returns:
            The bytes.
        """
        return self.read(size)


class _RecordingOpener:
    """Stands in for urllib's opener, recording the Request it was handed."""

    def __init__(self, payload: bytes = b"{}") -> None:
        """Build an opener that always answers with the same body.

        Args:
            payload: The body every call returns.
        """
        self.payload = payload
        self.request: urllib.request.Request | None = None

    def open(self, request: urllib.request.Request, timeout: float) -> _FakeResponse:
        """Record the request and answer it.

        Args:
            request: The prepared request.
            timeout: The timeout the client chose.

        Returns:
            The canned response.
        """
        self.request = request
        self.timeout = timeout
        return _FakeResponse(self.payload)


class TransportTestCase(unittest.TestCase):
    """Base that opens a budget, because the transport charges one."""

    def setUp(self) -> None:
        """Open a generous budget so nothing here is refused for cost."""
        begin_call(max_requests=100, max_seconds=60.0)
        self.addCleanup(end_call)
        self.client = BoundedHttpClient(FAKE_API_KEY, 10.0)


class TestHostAllowlist(TransportTestCase):
    """Where this client will and will not open a socket."""

    def test_an_allowlisted_host_is_called(self) -> None:
        """The positive case: a host on the list is reached.

        Without it, an allowlist that refused everything would pass the
        negative test below (ledger LL-4).
        """
        opener = _RecordingOpener()
        self.client._opener = opener
        self.client.request("places.googleapis.com", "/v1/places:searchText", safe_label="a search")
        self.assertIsNotNone(opener.request)

    def test_any_other_host_is_refused(self) -> None:
        """A host nobody put on the list never gets the key."""
        with self.assertRaises(HttpError) as caught:
            self.client.request("maps.evil.example", "/v1/anything", safe_label="a search")
        self.assertIn("may not call", str(caught.exception))

    def test_the_refusal_names_no_url(self) -> None:
        """On three of these hosts the URL carries the key, so no URL is quoted."""
        with self.assertRaises(HttpError) as caught:
            self.client.request("maps.evil.example", "/v1/anything", safe_label="a search")
        self.assertNotIn(FAKE_API_KEY, str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

    def test_the_host_is_refused_before_the_budget_is_charged(self) -> None:
        """A request to an unlisted host must not cost the caller anything."""
        end_call()
        begin_call(max_requests=1, max_seconds=60.0)
        with self.assertRaises(HttpError):
            self.client.request("maps.evil.example", "/v1/anything", safe_label="a search")
        # The one permitted request is still available.
        opener = _RecordingOpener()
        self.client._opener = opener
        self.client.request("geocode.googleapis.com", "/v4/geocode/address", safe_label="geocoding")
        self.assertIsNotNone(opener.request)


class TestKeyPlacement(TransportTestCase):
    """Which mechanism carries the key on which host."""

    def _sent(self, host: str, path: str) -> urllib.request.Request:
        """Perform one request and return what urllib would have sent.

        Args:
            host: The host to call.
            path: The path.

        Returns:
            The prepared Request.
        """
        opener = _RecordingOpener()
        self.client._opener = opener
        self.client.request(host, path, safe_label="a request")
        assert opener.request is not None
        return opener.request

    def test_header_hosts_carry_the_key_in_a_header_and_not_the_url(self) -> None:
        """Places, Routes, and Geocoding v4 take X-Goog-Api-Key."""
        for host, path in (
            ("places.googleapis.com", "/v1/places:searchText"),
            ("routes.googleapis.com", "/directions/v2:computeRoutes"),
            ("geocode.googleapis.com", "/v4/geocode/address"),
        ):
            with self.subTest(host=host):
                sent = self._sent(host, path)
                self.assertEqual(sent.get_header("X-goog-api-key"), FAKE_API_KEY)
                self.assertNotIn(FAKE_API_KEY, sent.full_url)

    def test_query_hosts_carry_the_key_in_the_query_and_not_a_header(self) -> None:
        """The legacy web services accept no header form, so the URL holds it."""
        for host, path in (
            ("maps.googleapis.com", "/maps/api/timezone/json"),
            ("airquality.googleapis.com", "/v1/currentConditions:lookup"),
            ("addressvalidation.googleapis.com", "/v1:validateAddress"),
        ):
            with self.subTest(host=host):
                sent = self._sent(host, path)
                self.assertIsNone(sent.get_header("X-goog-api-key"))
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(sent.full_url).query)
                self.assertEqual(query["key"], [FAKE_API_KEY])

    def test_the_field_mask_reaches_the_header_verbatim(self) -> None:
        """The mask is a server constant, and it is sent as one."""
        opener = _RecordingOpener()
        self.client._opener = opener
        self.client.request(
            "places.googleapis.com",
            "/v1/places:searchText",
            safe_label="a search",
            field_mask="places.id,places.displayName",
        )
        assert opener.request is not None
        self.assertEqual(
            opener.request.get_header("X-goog-fieldmask"), "places.id,places.displayName"
        )


class TestRedirects(unittest.TestCase):
    """A machine-to-machine call has no legitimate redirect."""

    def test_the_handler_refuses_rather_than_following(self) -> None:
        """Returning None is what makes urllib raise instead of re-sending."""
        self.assertIsNone(_NoRedirectHandler().redirect_request())

    def test_a_3xx_status_becomes_an_error_naming_the_reason(self) -> None:
        """A redirect surfaces as a refusal, not as a followed request."""
        begin_call(max_requests=5, max_seconds=60.0)
        self.addCleanup(end_call)
        client = BoundedHttpClient(FAKE_API_KEY, 10.0)

        class _RedirectingOpener:
            """Raises the HTTPError urllib raises when a redirect is refused."""

            def open(self, request: Any, timeout: float) -> None:
                """Raise a 302.

                Args:
                    request: Ignored.
                    timeout: Ignored.
                """
                raise urllib.error.HTTPError(
                    "https://places.googleapis.com/v1/places:searchText",
                    302,
                    "Found",
                    {},  # type: ignore[arg-type]
                    io.BytesIO(b""),
                )

        client._opener = _RedirectingOpener()
        with self.assertRaises(HttpUnavailableError) as caught:
            client.request("places.googleapis.com", "/v1/places:searchText", safe_label="a search")
        self.assertIn("redirected", str(caught.exception))
        self.assertNotIn(FAKE_API_KEY, str(caught.exception))


class TestProxySuppression(unittest.TestCase):
    """One environment variable must not be able to reroute a credential."""

    def setUp(self) -> None:
        """Point a proxy variable at a port nothing listens on."""
        self._saved = os.environ.get("HTTPS_PROXY")
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:9"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        """Put the variable back."""
        if self._saved is None:
            os.environ.pop("HTTPS_PROXY", None)
        else:
            os.environ["HTTPS_PROXY"] = self._saved

    def test_a_default_opener_would_have_used_the_proxy(self) -> None:
        """The control. Without it, the test below passes on a machine where no
        proxy variable was ever readable, proving nothing (ledger LL-4)."""
        default = urllib.request.build_opener()
        proxies = [
            handler.proxies
            for handler in default.handlers
            if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertTrue(any("https" in entry for entry in proxies))

    def test_this_client_does_not(self) -> None:
        """Passing an empty ProxyHandler removes urllib's default, which is what
        seeds itself from the environment (ledger LL-28). urllib registers a
        handler under a scheme only when it has something to do for it, so the
        assertion is that no handler claims `https` for proxying at all."""
        client = BoundedHttpClient(FAKE_API_KEY, 10.0)
        for handler in client._opener.handlers:
            if isinstance(handler, urllib.request.ProxyHandler):
                self.assertEqual(handler.proxies, {})
        openers = client._opener.handle_open.get("https", [])
        self.assertFalse(
            any(isinstance(handler, urllib.request.ProxyHandler) for handler in openers)
        )


class TestResponseBounds(TransportTestCase):
    """What may come back off the socket."""

    def test_an_ordinary_body_is_parsed(self) -> None:
        """The positive case: a normal response survives the bound."""
        opener = _RecordingOpener(b'{"places": []}')
        self.client._opener = opener
        response = self.client.request(
            "places.googleapis.com", "/v1/places:searchText", safe_label="a search"
        )
        self.assertEqual(response.body, {"places": []})

    def test_an_oversized_body_is_refused(self) -> None:
        """A body past the cap is refused rather than silently truncated."""
        opener = _RecordingOpener(b"x" * (MAX_RESPONSE_BYTES + 10))
        self.client._opener = opener
        with self.assertRaises(HttpError) as caught:
            self.client.request(
                "places.googleapis.com", "/v1/places:searchText", safe_label="a search"
            )
        self.assertIn("exceeded", str(caught.exception))

    def test_a_body_that_is_not_json_is_refused(self) -> None:
        """An HTML error page from an intermediary is not a result."""
        opener = _RecordingOpener(b"<html>gateway timeout</html>")
        self.client._opener = opener
        with self.assertRaises(HttpUnavailableError):
            self.client.request(
                "places.googleapis.com", "/v1/places:searchText", safe_label="a search"
            )


class TestBudgetIsCharged(TransportTestCase):
    """The transport is where the per-call budget is actually spent."""

    def test_the_request_after_the_budget_is_refused(self) -> None:
        """No socket opens once the call has spent its allowance."""
        end_call()
        begin_call(max_requests=1, max_seconds=60.0)
        opener = _RecordingOpener()
        self.client._opener = opener
        self.client.request("places.googleapis.com", "/v1/places:searchText", safe_label="one")
        with self.assertRaises(BudgetExceededError):
            self.client.request("places.googleapis.com", "/v1/places:searchText", safe_label="two")

    def test_the_timeout_never_exceeds_what_the_call_has_left(self) -> None:
        """The per-request timeout is the smaller of the configured one and the
        budget's remainder, which is what makes the wall clock a real bound."""
        end_call()
        begin_call(max_requests=5, max_seconds=2.0)
        opener = _RecordingOpener()
        self.client._opener = opener
        self.client.request("places.googleapis.com", "/v1/places:searchText", safe_label="a search")
        self.assertLessEqual(opener.timeout, 2.0)


if __name__ == "__main__":
    unittest.main()
