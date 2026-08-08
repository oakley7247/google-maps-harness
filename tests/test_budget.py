# =============================================================================
# test_budget.py — the per-tool-call ceiling on upstream requests and time.
#
# Part of: google-maps-harness test suite.
# Each test names one property and is shaped so nothing else can satisfy it: a
# test of the request ceiling is given a generous clock, and a test of the clock
# is given a generous request ceiling, so whichever limit fires is the one under
# test (ledger LL-21).
# =============================================================================
"""The budget refuses, resets, and does not leak between threads."""

import threading
import unittest

from google_maps_harness.budget import (
    BudgetExceededError,
    begin_call,
    charge,
    end_call,
    requests_made,
)


class TestRequestCeiling(unittest.TestCase):
    """How many upstream requests one tool call may make."""

    def tearDown(self) -> None:
        """Leave no budget open for the next test on this thread."""
        end_call()

    def test_an_ordinary_call_is_not_refused(self) -> None:
        """The positive case: the budget permits the work it exists to bound.

        Without this, a budget that refused everything would pass every other
        test in this class (ledger LL-4).
        """
        begin_call(max_requests=3, max_seconds=60.0)
        for _ in range(3):
            self.assertGreater(charge("a request"), 0.0)
        self.assertEqual(requests_made(), 3)

    def test_the_request_after_the_last_is_refused(self) -> None:
        """The ceiling holds, and the refusal names the count."""
        begin_call(max_requests=2, max_seconds=60.0)
        charge("first")
        charge("second")
        with self.assertRaises(BudgetExceededError) as caught:
            charge("third")
        self.assertIn("upstream request 3", str(caught.exception))

    def test_a_refused_request_spends_nothing(self) -> None:
        """A rejected attempt must not advance the counter it was refused by.

        Charging on the way past the check would make every retry cost the
        caller a request it never got (ledger LL-27).
        """
        begin_call(max_requests=1, max_seconds=60.0)
        charge("first")
        for _ in range(5):
            with self.assertRaises(BudgetExceededError):
                charge("refused")
        self.assertEqual(requests_made(), 1)

    def test_the_budget_resets_between_calls(self) -> None:
        """A worker thread reused by the next tool call starts fresh."""
        begin_call(max_requests=1, max_seconds=60.0)
        charge("first call")
        with self.assertRaises(BudgetExceededError):
            charge("first call again")

        begin_call(max_requests=1, max_seconds=60.0)
        self.assertGreater(charge("second call"), 0.0)
        self.assertEqual(requests_made(), 1)


class TestTimeCeiling(unittest.TestCase):
    """The wall clock one tool call may spend upstream."""

    def tearDown(self) -> None:
        """Leave no budget open for the next test on this thread."""
        end_call()

    def test_the_remaining_time_is_handed_to_the_caller(self) -> None:
        """charge returns the budget left, which becomes the request's timeout.

        A deadline checked only between requests cannot fire during one, so the
        remaining budget has to reach the operation itself (ledger LL-30).
        """
        begin_call(max_requests=10, max_seconds=12.0)
        remaining = charge("a request")
        self.assertGreater(remaining, 11.0)
        self.assertLessEqual(remaining, 12.0)

    def test_an_expired_clock_refuses_before_the_request_starts(self) -> None:
        """A call past its deadline is refused with plenty of requests left."""
        begin_call(max_requests=100, max_seconds=5.0)
        # Move the deadline into the past rather than sleeping: the property
        # under test is that an expired deadline refuses, not how long a test
        # is willing to wait for one.
        from google_maps_harness.budget import _local

        _local.budget.deadline -= 10.0
        with self.assertRaises(BudgetExceededError) as caught:
            charge("a request")
        self.assertIn("time budget", str(caught.exception))


class TestClosedByDefault(unittest.TestCase):
    """No budget open means no request, on any thread."""

    def test_a_request_outside_a_tool_call_is_refused(self) -> None:
        """An unmetered request is the state this module exists to prevent."""
        end_call()
        with self.assertRaises(BudgetExceededError) as caught:
            charge("a request")
        self.assertIn("outside a tool call", str(caught.exception))

    def test_one_thread_cannot_spend_another_thread_s_budget(self) -> None:
        """The MCP SDK runs synchronous tools on a worker pool, so two calls
        can be in flight at once; a shared counter would let them reset and
        spend each other's allowance (ledger LL-32)."""
        begin_call(max_requests=5, max_seconds=60.0)
        self.addCleanup(end_call)

        seen: list[object] = []

        def other_thread() -> None:
            """Try to charge without opening a budget on this thread."""
            try:
                charge("a request from another thread")
                seen.append("allowed")
            except BudgetExceededError:
                seen.append("refused")

        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()

        self.assertEqual(seen, ["refused"])
        # And the first thread's budget is untouched by the second's attempt.
        self.assertEqual(requests_made(), 0)


if __name__ == "__main__":
    unittest.main()
