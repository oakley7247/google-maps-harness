# =============================================================================
# config.py — reads and validates every setting this server takes from the
# environment.
#
# Part of: google-maps-harness MCP server. Called by: server.py at startup;
# env_file.py reads SETTING_NAMES. Calls: nothing.
# Security: the Google Maps API key is a credential and is read from the
# environment only — never from argv, where it would land in shell history and
# in `ps` output. A settings file can reach the environment, but only through
# the explicit --env-file argument env_file.py parses, and only for the names
# listed in SETTING_NAMES. No error this module raises echoes the key: the
# validation failures name the variable and describe the expected shape, never
# the value that was supplied.
# =============================================================================
"""Load and validate the Google Maps Platform settings from the environment."""

import os
import re
from dataclasses import dataclass

# Google Maps API keys are 39 characters beginning `AIza` today, drawn from the
# URL-safe base64 alphabet. The pattern is deliberately wider than that —
# length and alphabet, not an exact shape — so a future format change does not
# lock the server out, while a value carrying a space, a quote, a newline, or
# any other control character is still refused before it can reach a URL, a
# header, or a log line (ledger LL-2).
_KEY_PATTERN = re.compile(r"\A[A-Za-z0-9_\-]{20,128}\Z")

# CLDR region codes are two letters ("US", "GB"). Language codes are an ISO 639
# code with an optional subtag ("en", "en-GB", "pt-BR"). Both are interpolated
# into request bodies and query strings, so both are matched exactly rather than
# merely length-checked.
_REGION_PATTERN = re.compile(r"\A[A-Za-z]{2}\Z")
_LANGUAGE_PATTERN = re.compile(r"\A[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?\Z")

_DEFAULT_TIMEOUT_SECONDS = 10.0
_MIN_TIMEOUT_SECONDS = 1.0
_MAX_TIMEOUT_SECONDS = 60.0

# The per-tool-call upstream budget. Every loop in this server is bounded on its
# own, but bounded loops still multiply: one route matrix plus one paged search
# plus a details lookup per result is a product nobody sizes by reading any
# single loop (ledger LL-33). These two numbers bound the tool call, which is
# the unit the caller actually invokes and the unit Google actually bills.
#
# The arithmetic behind the default of 25: the largest legitimate single call in
# this server is a text search that pages to Google's 60-result ceiling (3
# requests) followed by a details lookup for each of the 20 results on one page
# (20 requests), which is 23. Twenty-five leaves headroom above that and refuses
# anything an order of magnitude larger.
_DEFAULT_MAX_REQUESTS_PER_CALL = 25
_MIN_MAX_REQUESTS_PER_CALL = 1
_MAX_MAX_REQUESTS_PER_CALL = 200

# Wall clock for one tool call, across every upstream request it makes. Sized as
# roughly three times the per-request timeout, so a call that is merely slow
# survives while a call that has stalled in a retry-like pattern does not.
_DEFAULT_MAX_SECONDS_PER_CALL = 30.0
_MIN_MAX_SECONDS_PER_CALL = 5.0
_MAX_MAX_SECONDS_PER_CALL = 300.0


# Every variable this project reads, and the only names `--env-file` will apply.
# A settings file naming anything else is refused rather than partly ignored, so
# a misspelling fails loudly instead of leaving the operator believing a setting
# took effect (ledger LL-12), and a settings file cannot reach any other part of
# the process environment. A test holds this set and .env.example in step.
SETTING_NAMES: frozenset[str] = frozenset(
    {
        "GOOGLE_MAPS_API_KEY",
        "GOOGLE_MAPS_TIMEOUT_SECONDS",
        "GOOGLE_MAPS_MAX_REQUESTS_PER_CALL",
        "GOOGLE_MAPS_MAX_SECONDS_PER_CALL",
        "GOOGLE_MAPS_REGION_CODE",
        "GOOGLE_MAPS_LANGUAGE_CODE",
        "GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS",
    }
)


class ConfigError(Exception):
    """Raised when the environment does not describe a usable configuration."""


@dataclass(frozen=True)
class MapsConfig:
    """Validated settings for one Google Maps Platform project.

    Attributes:
        api_key: The Maps Platform API key. Never log this.
        timeout_seconds: Per-request connect and read timeout.
        max_requests_per_call: Upstream requests one tool call may issue.
        max_seconds_per_call: Wall clock one tool call may spend upstream.
        region_code: Default CLDR region used to bias ambiguous queries, or
            None to let Google infer it from the request.
        language_code: Default language for place names and instructions.
        allow_atmosphere_fields: Whether the highest Places detail tier —
            reviews, editorial summaries, and the rest of the Atmosphere SKU —
            may be requested.
    """

    api_key: str
    timeout_seconds: float
    max_requests_per_call: int
    max_seconds_per_call: float
    region_code: str | None
    language_code: str
    allow_atmosphere_fields: bool


def load_config() -> MapsConfig:
    """Read every setting from the environment and validate it.

    Returns:
        The validated settings.

    Raises:
        ConfigError: A required setting is missing, or one is malformed. The
            message names the variable and the expected shape, never the value.
    """
    return MapsConfig(
        api_key=_required_key("GOOGLE_MAPS_API_KEY"),
        timeout_seconds=_bounded_float(
            "GOOGLE_MAPS_TIMEOUT_SECONDS",
            _DEFAULT_TIMEOUT_SECONDS,
            _MIN_TIMEOUT_SECONDS,
            _MAX_TIMEOUT_SECONDS,
        ),
        max_requests_per_call=_bounded_int(
            "GOOGLE_MAPS_MAX_REQUESTS_PER_CALL",
            _DEFAULT_MAX_REQUESTS_PER_CALL,
            _MIN_MAX_REQUESTS_PER_CALL,
            _MAX_MAX_REQUESTS_PER_CALL,
        ),
        max_seconds_per_call=_bounded_float(
            "GOOGLE_MAPS_MAX_SECONDS_PER_CALL",
            _DEFAULT_MAX_SECONDS_PER_CALL,
            _MIN_MAX_SECONDS_PER_CALL,
            _MAX_MAX_SECONDS_PER_CALL,
        ),
        region_code=_optional_pattern("GOOGLE_MAPS_REGION_CODE", _REGION_PATTERN, "two letters"),
        language_code=_optional_pattern(
            "GOOGLE_MAPS_LANGUAGE_CODE", _LANGUAGE_PATTERN, "a language code such as en or pt-BR"
        )
        or "en",
        allow_atmosphere_fields=_flag("GOOGLE_MAPS_ALLOW_ATMOSPHERE_FIELDS"),
    )


def _required_key(name: str) -> str:
    """Read a credential from the environment and check its shape.

    Args:
        name: The environment variable holding it.

    Returns:
        The credential, stripped of surrounding whitespace.

    Raises:
        ConfigError: The variable is absent, empty, or malformed.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        raise ConfigError(
            f"{name} is not set. Create an API key in the Google Cloud console, "
            "restrict it to the Maps Platform APIs this server calls, and put it "
            "in a 0600 file passed with --env-file."
        )
    if not _KEY_PATTERN.fullmatch(raw):
        # SECURITY: the message describes the expected shape and never echoes
        # the supplied value, because a malformed key is still a credential and
        # this text reaches stderr and the operator's terminal.
        raise ConfigError(
            f"{name} is not a plausible Google Maps API key: expected 20 to 128 "
            "characters of letters, digits, hyphen, and underscore."
        )
    return raw


def _bounded_float(name: str, default: float, low: float, high: float) -> float:
    """Read an optional float setting and hold it inside its range.

    Args:
        name: The environment variable.
        default: Used when the variable is absent or empty.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        The validated value.

    Raises:
        ConfigError: The value is not a number, is not finite, or is outside
            the range.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    # SECURITY: `float()` and `int()` are Unicode-aware and convert non-ASCII
    # digits silently — `float("٣")` is 3.0, and nothing downstream would ever
    # know the operator did not type a 3. The ASCII gate runs before the parse
    # so the value that is validated is the value that was written (ledger LL-3,
    # LL-2).
    if not raw.isascii():
        raise ConfigError(f"{name} must be a number between {low} and {high}, in ASCII digits.")
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a number between {low} and {high}.") from error
    # SECURITY: float() accepts "nan", "inf", and "1e999". A non-finite timeout
    # is an unbounded wait and a non-finite budget is no budget at all, so the
    # range check below is written to reject them: every comparison against NaN
    # is False, which makes `not (low <= value <= high)` true (ledger LL-15).
    if not low <= value <= high:
        raise ConfigError(f"{name} must be a number between {low} and {high}.")
    return value


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    """Read an optional integer setting and hold it inside its range.

    Args:
        name: The environment variable.
        default: Used when the variable is absent or empty.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        The validated value.

    Raises:
        ConfigError: The value is not an integer or is outside the range.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    # SECURITY: same gate as _bounded_float, and for the same reason. `int("٣")`
    # returns 3 without complaint, so a try/except around the parse catches
    # nothing — the ASCII check is the control, not the exception handler
    # (ledger LL-3).
    if not raw.isascii():
        raise ConfigError(
            f"{name} must be a whole number between {low} and {high}, in ASCII digits."
        )
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a whole number between {low} and {high}.") from error
    if not low <= value <= high:
        raise ConfigError(f"{name} must be a whole number between {low} and {high}.")
    return value


def _optional_pattern(name: str, pattern: re.Pattern[str], shape: str) -> str | None:
    """Read an optional string setting that must match a pattern.

    Args:
        name: The environment variable.
        pattern: The shape the value must match exactly.
        shape: A description of that shape, for the error message.

    Returns:
        The validated value, or None when the variable is absent or empty.

    Raises:
        ConfigError: The value does not match.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    if not pattern.fullmatch(raw):
        raise ConfigError(f"{name} must be {shape}.")
    # The matched string is what is returned, so what was validated is what is
    # stored (ledger LL-2).
    return raw


def _flag(name: str) -> bool:
    """Read a boolean setting.

    Args:
        name: The environment variable.

    Returns:
        True only for an explicit affirmative. Anything else, including an
        unset variable and a typo, reads as False.

    Raises:
        ConfigError: The value is present but is neither affirmative nor
            negative — a flag nobody can read is worse than an absent one.
    """
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    # SECURITY: fails closed and loudly rather than quietly reading as False.
    # A permission flag the operator believes is set, and that this server has
    # silently ignored, is the failure mode worth an exit (ledger LL-12).
    raise ConfigError(f"{name} must be true or false.")
