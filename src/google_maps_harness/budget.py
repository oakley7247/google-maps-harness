# =============================================================================
# budget.py — the per-tool-call ceiling on upstream requests and wall clock.
#
# Part of: google-maps-harness. Called by: runtime.py (which opens a budget at
# the start of every tool call) and http_client.py (which charges it
# immediately before each socket opens). Calls: nothing.
# Security: every loop in this server is bounded on its own, and bounded loops
# still multiply — a paged search whose every result is then looked up in detail
# is a product no single loop's bound describes (ledger LL-33). Google Maps
# Platform bills per request, so an unbounded fan-out is money as well as
# latency. This module puts the budget on the unit the caller actually invokes.
#
# Concurrency: the MCP SDK dispatches each request into a task group and runs
# synchronous tools on the anyio worker-thread pool, so two tool calls can be in
# flight at once and a worker thread is reused across calls. The counters are
# therefore thread-local — a shared counter would let two concurrent calls reset
# and spend each other's budget — and every tool call resets them on entry
# rather than trusting whatever the previous occupant of the thread left behind
# (ledger LL-32).
# =============================================================================
"""Bound the upstream requests and wall clock one tool call may spend."""

import threading
import time
from dataclasses import dataclass

# The smallest per-request timeout worth attempting. When the wall-clock budget
# has less than this left, the call is refused rather than started: a request
# given a fraction of a second will time out anyway, and refusing says why.
_MIN_USEFUL_SECONDS = 0.5


class BudgetExceededError(Exception):
    """Raised when a tool call has spent its upstream request or time budget."""


@dataclass
class _CallBudget:
    """One tool call's remaining allowance.

    Attributes:
        requests_left: Upstream requests this call may still make.
        deadline: Monotonic time after which no further request may start.
        requests_made: Requests already charged, for the refusal message.
    """

    requests_left: int
    deadline: float
    requests_made: int


_local = threading.local()


def begin_call(max_requests: int, max_seconds: float) -> None:
    """Open a fresh budget for the tool call now starting on this thread.

    Args:
        max_requests: Upstream requests this call may make.
        max_seconds: Wall clock this call may spend on upstream requests.
    """
    # Assignment, not a top-up: a worker thread is reused between tool calls, so
    # anything the previous occupant left is discarded rather than inherited.
    _local.budget = _CallBudget(
        requests_left=max_requests,
        deadline=time.monotonic() + max_seconds,
        requests_made=0,
    )


def end_call() -> None:
    """Close the budget, so nothing outside a tool call can spend on this thread."""
    _local.budget = None


def charge(safe_label: str) -> float:
    """Charge one upstream request and return the seconds it may take.

    Args:
        safe_label: A credential-free description of the request about to be
            made, used in the refusal message.

    Returns:
        The wall clock remaining for this tool call. The caller uses it as the
        request's own timeout, which is what makes the deadline a real bound:
        a check between requests cannot fire while a request is in progress, so
        the remaining budget has to be handed to the operation itself (ledger
        LL-30).

    Raises:
        BudgetExceededError: The call has spent its request allowance or its
            time, or no budget is open on this thread.
    """
    budget: _CallBudget | None = getattr(_local, "budget", None)
    if budget is None:
        # SECURITY: fails closed. A request reaching the transport with no
        # budget open is a request nothing is metering, which is the state this
        # module exists to prevent — so it is refused rather than waved through
        # as "unlimited" (ledger LL-24). The only callers are tools, and every
        # tool passes through the decorator that opens a budget.
        raise BudgetExceededError(
            f"Refused: {safe_label} was attempted outside a tool call, where "
            "nothing bounds how many upstream requests it can make. This is a "
            "defect in the server; report it rather than trying another route."
        )

    # SECURITY: the charge happens before the request, not after it, and the
    # refusals sit above the charge — so a refused attempt spends nothing and a
    # spent attempt is one that actually reached Google (ledger LL-20, LL-27).
    if budget.requests_left <= 0:
        raise BudgetExceededError(
            f"Refused: {safe_label} would be upstream request "
            f"{budget.requests_made + 1} of this tool call, past the "
            f"{budget.requests_made} it is allowed. Ask for fewer results, or "
            "split the work across several calls."
        )
    remaining = budget.deadline - time.monotonic()
    if remaining < _MIN_USEFUL_SECONDS:
        raise BudgetExceededError(
            f"Refused: this tool call has used its whole time budget, so "
            f"{safe_label} was not started. Ask for fewer results, or split the "
            "work across several calls."
        )

    budget.requests_left -= 1
    budget.requests_made += 1
    return remaining


def requests_made() -> int:
    """Return how many upstream requests the current tool call has charged.

    Returns:
        The count, or 0 when no budget is open. Reported alongside every tool
        result so the cost of a call is visible to whoever reads it.
    """
    budget: _CallBudget | None = getattr(_local, "budget", None)
    return budget.requests_made if budget else 0
