# =============================================================================
# support.py — the fakes every test uses in place of the network.
#
# Part of: google-maps-harness test suite. Used by: every test module.
# No test in this suite opens a socket to the internet or needs a real key. The
# fake transport records what a request would have been — host, path, query,
# headers, body — which is what lets a test assert on the wire rather than on a
# mock's call count.
# =============================================================================
"""Fakes standing in for Google Maps Platform."""

from typing import Any

from google_maps_harness.budget import charge
from google_maps_harness.config import MapsConfig
from google_maps_harness.http_client import HttpResponse
from google_maps_harness.maps_api import MapsClient
from google_maps_harness.redaction import SecretRegistry
from google_maps_harness.runtime import Runtime, configure

# Self-evidently fake and low-entropy, so gitleaks' generic-api-key rule does
# not fire on it and nothing needs suppressing. It exists precisely so the suite
# can prove it never escapes into an error message or a log line.
FAKE_API_KEY = "EXAMPLEmapsKEYnotARealCredential"


class RecordedRequest:
    """One request the fake transport was asked to make.

    Attributes:
        host: The host it would have called.
        path: The path.
        method: The HTTP method.
        params: The query parameters, before the key was added.
        json_body: The request body, or None.
        field_mask: The X-Goog-FieldMask value, or None.
        safe_label: The credential-free label the caller passed.
    """

    def __init__(self, host: str, path: str, kwargs: dict[str, Any]) -> None:
        """Record one request.

        Args:
            host: The host.
            path: The path.
            kwargs: Everything else the transport was given.
        """
        self.host = host
        self.path = path
        self.method = kwargs.get("method", "GET")
        self.params = kwargs.get("params")
        self.json_body = kwargs.get("json_body")
        self.field_mask = kwargs.get("field_mask")
        self.safe_label = kwargs.get("safe_label", "")


class FakeTransport:
    """Stands in for BoundedHttpClient, answering from a queue of replies."""

    def __init__(self, replies: list[HttpResponse] | None = None) -> None:
        """Build a transport that answers with the given replies in order.

        Args:
            replies: The responses to return, one per request. When the queue
                empties, an empty 200 is returned so a test that only cares
                about the request does not have to supply one.
        """
        self.replies = list(replies or [])
        self.requests: list[RecordedRequest] = []

    def request(self, host: str, path: str, **kwargs: Any) -> HttpResponse:
        """Record the request and return the next queued reply.

        Args:
            host: The host.
            path: The path.
            **kwargs: Everything else the caller passed.

        Returns:
            The next queued response, or an empty 200.

        Raises:
            BudgetExceededError: The tool call has spent its allowance.
        """
        # The real transport charges the per-call budget immediately before the
        # socket opens, so this one does too. Without it a test would exercise
        # every tool with the budget switched off, and the reported request
        # count in each wrapped result would always be zero — a fake that is
        # cheaper than the real thing in exactly the dimension being measured.
        charge(kwargs.get("safe_label", "a request"))
        self.requests.append(RecordedRequest(host, path, kwargs))
        if self.replies:
            return self.replies.pop(0)
        return HttpResponse(200, {}, None)

    @property
    def last(self) -> RecordedRequest:
        """Return the most recent request.

        Returns:
            The last recorded request.
        """
        return self.requests[-1]


def ok(body: Any) -> HttpResponse:
    """Build a successful response.

    Args:
        body: The parsed body to return.

    Returns:
        A 200 response carrying it.
    """
    return HttpResponse(200, body, None)


def failure(status: int, body: Any) -> HttpResponse:
    """Build a failed response.

    Args:
        status: The HTTP status.
        body: The parsed body to return.

    Returns:
        The response.
    """
    return HttpResponse(status, body, None)


def make_config(**overrides: Any) -> MapsConfig:
    """Build a validated configuration for a test.

    Args:
        **overrides: Fields to change from the defaults.

    Returns:
        The configuration.
    """
    defaults: dict[str, Any] = {
        "api_key": FAKE_API_KEY,
        "timeout_seconds": 10.0,
        "max_requests_per_call": 25,
        "max_seconds_per_call": 30.0,
        "region_code": None,
        "language_code": "en",
        "allow_atmosphere_fields": False,
    }
    defaults.update(overrides)
    return MapsConfig(**defaults)


def install_runtime(
    transport: FakeTransport | None = None, **config_overrides: Any
) -> FakeTransport:
    """Install a runtime backed by a fake transport, as the tools expect.

    Args:
        transport: The transport to use, or None to build an empty one.
        **config_overrides: Fields to change in the configuration.

    Returns:
        The transport, so a test can queue replies and read requests.
    """
    fake = transport or FakeTransport()
    config = make_config(**config_overrides)
    secrets = SecretRegistry()
    secrets.add(config.api_key)
    configure(
        Runtime(
            config=config,
            # The client's only dependency is something with a `request`
            # method, so the fake goes straight in where the transport would.
            maps=MapsClient(fake, config.language_code, config.region_code),  # type: ignore[arg-type]
            secrets=secrets,
        )
    )
    return fake
