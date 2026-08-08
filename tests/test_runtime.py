# =============================================================================
# test_runtime.py — the guards every tool passes through.
#
# Part of: google-maps-harness test suite.
# =============================================================================
"""Credential scrubbing, payload bounds, and the recursion ceiling."""

import unittest

from google_maps_harness.budget import BudgetExceededError, charge
from google_maps_harness.redaction import REDACTED, SecretRegistry
from google_maps_harness.runtime import (
    _MAX_SCRUB_DEPTH,
    guarded,
    strip_control,
    wrap,
)

from .support import FAKE_API_KEY, install_runtime


class TestScrubbing(unittest.TestCase):
    """The API key must not leave the process inside an exception."""

    def test_a_registered_secret_is_removed(self) -> None:
        """The registry's own behaviour, before the decorator uses it."""
        registry = SecretRegistry()
        registry.add(FAKE_API_KEY)
        self.assertEqual(
            registry.scrub(f"failed with key={FAKE_API_KEY}"), f"failed with key={REDACTED}"
        )

    def test_a_short_value_is_not_registered(self) -> None:
        """A two-character secret would redact half of every sentence."""
        registry = SecretRegistry()
        registry.add("ab")
        self.assertEqual(registry.scrub("ab cd ab"), "ab cd ab")

    def test_a_tool_exception_carrying_the_key_is_scrubbed(self) -> None:
        """The backstop, exercised through the decorator every tool wears."""
        install_runtime()

        @guarded
        def leaky() -> None:
            """Raise an error that names the key, as a library might."""
            raise RuntimeError(
                f"connection to https://maps.googleapis.com/?key={FAKE_API_KEY} failed"
            )

        with self.assertRaises(RuntimeError) as caught:
            leaky()
        self.assertNotIn(FAKE_API_KEY, str(caught.exception))
        self.assertIn(REDACTED, str(caught.exception))

    def test_an_ordinary_exception_passes_through_unchanged(self) -> None:
        """The positive case: scrubbing does not rewrite messages it should not.

        Without this, a decorator that replaced every exception with a redaction
        marker would pass the test above (ledger LL-4).
        """
        install_runtime()

        @guarded
        def ordinary() -> None:
            """Raise an error with nothing sensitive in it."""
            raise ValueError("radius_metres must be greater than 0.")

        with self.assertRaises(ValueError) as caught:
            ordinary()
        self.assertEqual(str(caught.exception), "radius_metres must be greater than 0.")


class TestBudgetLifecycle(unittest.TestCase):
    """The decorator opens the budget and closes it on every path."""

    def test_a_budget_is_open_inside_a_tool_and_closed_after(self) -> None:
        """Closing on exit is what keeps a pooled worker thread from carrying
        spendable budget into the next tool call (ledger LL-32)."""
        install_runtime()
        inside: list[float] = []

        @guarded
        def tool() -> None:
            """Charge once, from inside the call."""
            inside.append(charge("a request"))

        tool()
        self.assertEqual(len(inside), 1)
        with self.assertRaises(BudgetExceededError):
            charge("a request after the call")

    def test_the_budget_closes_even_when_the_tool_raises(self) -> None:
        """A failing tool must not leave an open budget behind it."""
        install_runtime()

        @guarded
        def failing() -> None:
            """Fail after doing nothing."""
            raise ValueError("no")

        with self.assertRaises(ValueError):
            failing()
        with self.assertRaises(BudgetExceededError):
            charge("a request after the call")


class TestPayloadBounds(unittest.TestCase):
    """What may reach the model's context."""

    def setUp(self) -> None:
        """Install a runtime, because wrap reports the call's request count."""
        install_runtime()

    def test_a_small_payload_survives_whole(self) -> None:
        """The positive case: the bound does not eat an ordinary response."""
        wrapped = wrap({"places": [{"id": "abc"}]})
        self.assertEqual(wrapped["data"], {"places": [{"id": "abc"}]})
        self.assertNotIn("truncated", wrapped)

    def test_a_large_list_is_cut_at_a_record_boundary(self) -> None:
        """Whole entries are kept, so what survives is still valid to reason about."""
        payload = {"places": [{"id": "x" * 1000} for _ in range(500)]}
        wrapped = wrap(payload)
        self.assertIn("truncated", wrapped)
        self.assertLess(len(wrapped["data"]["places"]), 500)
        self.assertTrue(all("id" in entry for entry in wrapped["data"]["places"]))

    def test_a_structure_nested_past_the_ceiling_is_replaced(self) -> None:
        """A defensive walk over remote structure is itself attack surface, so
        it is bounded, and past the bound it fails closed rather than passing
        an unchecked string through (ledger LL-19)."""
        deep: object = "leaf\x07"
        for _ in range(_MAX_SCRUB_DEPTH + 5):
            deep = {"next": deep}
        wrapped = wrap(deep)
        self.assertNotIn("\x07", repr(wrapped["data"]))

    def test_an_unmeasurable_payload_is_not_passed_on(self) -> None:
        """What cannot be measured cannot be bounded, so it does not go.

        A circular reference is the case that reaches this branch: the size
        pass serializes with `default=str`, which turns any ordinary object
        into its repr, so an unknown *type* is not what fails here — an
        unbounded *shape* is.
        """
        circular: dict[str, object] = {}
        circular["self"] = circular
        wrapped = wrap(circular)
        self.assertIn("truncated", wrapped)
        self.assertNotIsInstance(wrapped["data"], dict)


class TestControlStrip(unittest.TestCase):
    """Every string Google returns is stripped before it reaches the model."""

    def test_controls_go_and_ordinary_text_stays(self) -> None:
        """Both halves, so the strip is not simply deleting everything."""
        self.assertEqual(strip_control("Caf\u00e9\tdel\nMar\x7f"), "Caf\u00e9delMar")
        self.assertEqual(strip_control("Straße 12, München"), "Straße 12, München")


if __name__ == "__main__":
    unittest.main()
