# =============================================================================
# maps_api.py — the Google Maps Platform client, and every validator that
# stands between a tool argument and a request.
#
# Part of: google-maps-harness. Called by: every module under tools/. Calls:
# http_client.py, which is the only thing here that touches the API key.
# Security: two jobs. First, validation — every value a tool hands this module
# came out of a language model, so coordinates are checked for finiteness (a
# JSON parser will hand you NaN and Infinity if you let it), place ids are
# matched against a character class *and* percent-encoded before they reach a
# URL path, and every free-text field is length-capped with control characters
# refused. Second, error translation — three of these APIs report failure inside
# an HTTP 200 body, so a caller that checked only the HTTP status would read a
# quota refusal as a successful empty result.
# =============================================================================
"""Call Google Maps Platform, and validate everything on the way in."""

import math
import urllib.parse
from typing import Any

from .http_client import BoundedHttpClient, HttpResponse

# Hosts, as constants rather than strings at each call site, so the allowlist in
# http_client.py is checked against a value no tool argument can influence.
GEOCODE_HOST = "geocode.googleapis.com"
PLACES_HOST = "places.googleapis.com"
ROUTES_HOST = "routes.googleapis.com"
ADDRESS_VALIDATION_HOST = "addressvalidation.googleapis.com"
AIR_QUALITY_HOST = "airquality.googleapis.com"
LEGACY_HOST = "maps.googleapis.com"

# Google place ids are URL-safe base64-style strings. The class is matched
# exactly rather than merely length-checked, because this value becomes a URL
# path segment: a `/` or a `..` in it would address a different resource
# entirely. The percent-encoding in _place_path is the second layer — the class
# check makes the intent explicit and gives a readable refusal, the encoding
# makes it true even if the class is ever widened (ledger LL-9: neutralize the
# downstream grammar's delimiters, do not trust a general check to have done it).
_PLACE_ID_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_MAX_PLACE_ID_CHARS = 512

# Free-text fields are model-authored and end up in a URL or a JSON body.
_MAX_QUERY_CHARS = 500
_MAX_ADDRESS_LINE_CHARS = 200
_MAX_ADDRESS_LINES = 5

# Google's Places search ceiling. Both search endpoints cap a page at 20.
MAX_PAGE_SIZE = 20

# Google's own ceiling on a location-biasing or location-restricting circle.
MAX_RADIUS_METRES = 50_000.0

# Elevation accepts up to 512 coordinates. This server caps at 50: the whole
# list is percent-encoded into a query string, and 512 pairs is a URL long
# enough that an intermediary can truncate it silently. Fifty samples describe
# any route profile a decision needs, and a caller wanting more can page.
MAX_ELEVATION_POINTS = 50

# Routes accepts up to 625 matrix elements. This server caps at 100, because
# Google bills the matrix per element: a single tool call at Google's ceiling is
# 625 billed elements, and a model that mistypes a loop would spend that
# repeatedly. Ten origins against ten destinations covers the comparisons an
# agent actually makes.
MAX_MATRIX_ELEMENTS = 100

# Waypoints between an origin and a destination on one route. Each one lengthens
# the response's leg list, which is the part that reaches the model's context.
MAX_INTERMEDIATES = 10

# The Unix timestamps this server will ask the Time Zone API about: 1970 through
# roughly 2100. Bounded because the value is model-supplied and lands in a URL,
# and because a timestamp outside this range is a mistake rather than a query.
_MIN_TIMESTAMP = 0
_MAX_TIMESTAMP = 4_102_444_800

# Legacy web-service statuses that mean "the request was fine, there is simply
# nothing here". Treated as success with an empty result, because a model told
# "no elevation data exists for the middle of the ocean" can reason; a model
# told "the request failed" retries.
_EMPTY_STATUSES = frozenset({"ZERO_RESULTS", "DATA_NOT_AVAILABLE"})


class MapsApiError(Exception):
    """A request to Google Maps Platform was refused, or an argument was invalid."""


# --- Validators ---------------------------------------------------------------


def validate_latitude(value: float, field: str = "latitude") -> float:
    """Check a latitude and return it.

    Args:
        value: The candidate latitude in degrees.
        field: The argument's name, for the error message.

    Returns:
        The validated latitude.

    Raises:
        MapsApiError: The value is not a finite number in [-90, 90].
    """
    return _finite_in_range(value, field, -90.0, 90.0)


def validate_longitude(value: float, field: str = "longitude") -> float:
    """Check a longitude and return it.

    Args:
        value: The candidate longitude in degrees.
        field: The argument's name, for the error message.

    Returns:
        The validated longitude.

    Raises:
        MapsApiError: The value is not a finite number in [-180, 180].
    """
    return _finite_in_range(value, field, -180.0, 180.0)


def _finite_in_range(value: Any, field: str, low: float, high: float) -> float:
    """Check that a number is finite and inside a range.

    Args:
        value: The candidate.
        field: The argument's name, for the error message.
        low: Smallest accepted value.
        high: Largest accepted value.

    Returns:
        The value as a float.

    Raises:
        MapsApiError: The value is not a number, is not finite, or is outside
            the range.
    """
    # SECURITY: the finiteness check runs on the value the JSON layer produced,
    # not on the text it arrived as. `float("nan")` and `float("1e999")` both
    # succeed, JSON's own parsers accept the bare tokens `NaN` and `Infinity`,
    # and either would sail through a bare range comparison — every comparison
    # against NaN is False, so `not (low <= value <= high)` catches it, and
    # math.isfinite catches the infinities the range would otherwise clamp
    # (ledger LL-15, LL-16).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapsApiError(f"{field} must be a number between {low} and {high}.")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise MapsApiError(f"{field} must be a finite number between {low} and {high}.")
    return number


def validate_place_id(value: str) -> str:
    """Check a Google place id and return it.

    Args:
        value: The candidate place id.

    Returns:
        The validated id, unchanged — what was checked is what is used
        (ledger LL-2).

    Raises:
        MapsApiError: The id is empty, too long, or holds a character outside
            the URL-safe set Google uses.
    """
    if not value or len(value) > _MAX_PLACE_ID_CHARS:
        raise MapsApiError(
            f"place_id must be between 1 and {_MAX_PLACE_ID_CHARS} characters. "
            "Place ids come from a search or an autocomplete result."
        )
    if not set(value) <= _PLACE_ID_CHARACTERS:
        raise MapsApiError(
            "place_id may hold only letters, digits, hyphen, and underscore. "
            "Use search_places_by_text to get a valid id rather than composing one."
        )
    return value


def validate_text(value: str, field: str, max_chars: int = _MAX_QUERY_CHARS) -> str:
    """Check a free-text argument and return it.

    Args:
        value: The candidate text.
        field: The argument's name, for the error message.
        max_chars: Longest text accepted.

    Returns:
        The validated text, stripped of surrounding whitespace.

    Raises:
        MapsApiError: The text is empty, too long, or holds an ASCII control
            character.
    """
    text = value.strip()
    if not text:
        raise MapsApiError(f"{field} must not be empty.")
    if len(text) > max_chars:
        raise MapsApiError(f"{field} must be {max_chars} characters or fewer.")
    # SECURITY: control characters are refused at the boundary rather than
    # stripped, because this value reaches a URL, a JSON body, and a log line,
    # and all three are spoofable through them (ledger LL-2).
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise MapsApiError(f"{field} must not contain control characters.")
    return text


def validate_page_size(value: int, field: str = "page_size") -> int:
    """Check a page size against Google's ceiling.

    Args:
        value: The candidate size.
        field: The argument's name, for the error message.

    Returns:
        The validated size.

    Raises:
        MapsApiError: The value is not a whole number in 1..20.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MapsApiError(f"{field} must be a whole number between 1 and {MAX_PAGE_SIZE}.")
    if not 1 <= value <= MAX_PAGE_SIZE:
        raise MapsApiError(f"{field} must be between 1 and {MAX_PAGE_SIZE}.")
    return value


def validate_radius(value: float) -> float:
    """Check a search radius against Google's ceiling.

    Args:
        value: The candidate radius in metres.

    Returns:
        The validated radius.

    Raises:
        MapsApiError: The radius is not a finite number above 0 and at most
            50,000 metres.
    """
    radius = _finite_in_range(value, "radius_metres", 0.0, MAX_RADIUS_METRES)
    if radius <= 0.0:
        raise MapsApiError("radius_metres must be greater than 0.")
    return radius


def validate_timestamp(value: int) -> int:
    """Check a Unix timestamp.

    Args:
        value: Seconds since the Unix epoch.

    Returns:
        The validated timestamp.

    Raises:
        MapsApiError: The value is not a whole number in the accepted range.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MapsApiError("timestamp must be a whole number of seconds since 1970-01-01 UTC.")
    if not _MIN_TIMESTAMP <= value <= _MAX_TIMESTAMP:
        raise MapsApiError(
            f"timestamp must be between {_MIN_TIMESTAMP} and {_MAX_TIMESTAMP} "
            "seconds since 1970-01-01 UTC."
        )
    return value


def validate_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    """Check a value against a fixed set and return it.

    Args:
        value: The candidate.
        allowed: Every value this server accepts.
        field: The argument's name, for the error message.

    Returns:
        The validated value.

    Raises:
        MapsApiError: The value is not in the set.
    """
    # SECURITY: an allowlist, checked against the exact string. These values are
    # interpolated into request bodies Google parses as enums; anything else is
    # refused rather than passed on for Google to reject, so the refusal names
    # the options rather than returning an opaque 400.
    if value not in allowed:
        raise MapsApiError(f"{field} must be one of: {', '.join(allowed)}.")
    return value


def validate_address_lines(lines: list[str]) -> list[str]:
    """Check the address lines handed to the Address Validation API.

    Args:
        lines: The candidate lines, in the order they would be written.

    Returns:
        The validated lines.

    Raises:
        MapsApiError: The list is empty, too long, or a line is unusable.
    """
    if not lines or len(lines) > _MAX_ADDRESS_LINES:
        raise MapsApiError(f"address_lines must hold 1 to {_MAX_ADDRESS_LINES} lines.")
    return [
        validate_text(line, f"address_lines[{index}]", _MAX_ADDRESS_LINE_CHARS)
        for index, line in enumerate(lines)
    ]


def parse_waypoint(value: str, field: str) -> dict[str, Any]:
    """Turn one route endpoint into the waypoint shape the Routes API takes.

    Three forms are accepted, in this order:

    - `place_id:ChIJ...` — the most precise, and what a search result gives you.
    - `40.7580,-73.9855` — a latitude and longitude pair.
    - anything else — treated as an address for Google to resolve.

    Args:
        value: The endpoint as written by the caller.
        field: The argument's name, for the error message.

    Returns:
        A waypoint object with exactly one of `placeId`, `location`, or
        `address` set.

    Raises:
        MapsApiError: The value is empty, too long, holds a control character,
            or looks like a coordinate pair and is not one.
    """
    text = validate_text(value, field)

    prefix = "place_id:"
    if text.startswith(prefix):
        return {"placeId": validate_place_id(text[len(prefix) :])}

    latitude, separator, longitude = text.partition(",")
    if separator and _is_number(latitude) and _is_number(longitude):
        # Only read as coordinates when both halves parse as numbers. An address
        # such as "Berlin, Germany" partitions on its comma too, and neither
        # half parses, so it stays an address. The test is "did the caller write
        # numbers", not "are the numbers usable" — a caller who wrote "nan,nan"
        # meant a coordinate, so it is refused by the validators below rather
        # than quietly posted to Google as an address.
        return {
            "location": {
                "latLng": {
                    "latitude": validate_latitude(float(latitude), f"{field} latitude"),
                    "longitude": validate_longitude(float(longitude), f"{field} longitude"),
                }
            }
        }

    return {"address": text}


def _is_number(text: str) -> bool:
    """Return True when text was written as a decimal number.

    Args:
        text: One half of a candidate coordinate pair.

    Returns:
        True when `float()` accepts it, including the "nan" and "1e999"
        spellings it turns into non-finite values. Those are deliberately
        included: recognizing them here is what routes them to
        validate_latitude, which refuses them, instead of leaving them to be
        read as an address (ledger LL-15, LL-16).
    """
    candidate = text.strip()
    # ASCII first: float() converts non-ASCII digits silently, and a coordinate
    # spelled in Arabic-Indic digits is a typo, not a location (ledger LL-3).
    if not candidate.isascii():
        return False
    try:
        float(candidate)
    except ValueError:
        return False
    return True


def _place_path(prefix: str, place_id: str) -> str:
    """Build a URL path holding a validated, encoded place id.

    Args:
        prefix: The path up to and including the trailing slash.
        place_id: An id that has already passed validate_place_id.

    Returns:
        The path, with the id percent-encoded so no character in it can be read
        as a path delimiter.
    """
    return prefix + urllib.parse.quote(place_id, safe="")


def _coordinate(value: float) -> str:
    """Format a coordinate for a legacy query parameter.

    Args:
        value: A validated latitude or longitude.

    Returns:
        The value with seven decimal places, which is about a centimetre — more
        precision than any Maps Platform API resolves, and a fixed width that
        keeps a URL's length predictable.
    """
    return f"{value:.7f}"


# --- The client ---------------------------------------------------------------


class MapsClient:
    """Calls the six Google Maps Platform APIs this server exposes."""

    def __init__(
        self, http: BoundedHttpClient, language_code: str, region_code: str | None
    ) -> None:
        """Build the client.

        Args:
            http: The bounded transport, which holds the API key.
            language_code: Default language for names and instructions.
            region_code: Default CLDR region for biasing, or None.
        """
        self._http = http
        self._language = language_code
        self._region = region_code

    # --- Geocoding ------------------------------------------------------------

    def geocode_address(self, address: str, region_code: str | None) -> Any:
        """Turn an address or plus code into coordinates and address components.

        Args:
            address: The validated address text.
            region_code: A CLDR region to bias toward, or None for the default.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        params = {"languageCode": self._language}
        region = region_code or self._region
        if region:
            params["regionCode"] = region
        # NOTE: the address goes in the path, not in a query parameter. Geocoding
        # v4 reserves `?address=` for a *structured* address, which is a protobuf
        # message; sending the free-text form there is refused with "'address' is
        # a message type. Parameters can only be bound to primitive types."
        # Verified against the live API on 2026-08-09 — the reference page shows
        # both spellings and does not say which one takes free text (ledger
        # LL-26: a comment naming a mechanism is a claim, and this one was wrong
        # until a real request proved it).
        return self._call(
            GEOCODE_HOST,
            "/v4/geocode/address/" + urllib.parse.quote(address, safe=""),
            "geocoding an address",
            params=params,
        )

    def reverse_geocode(self, latitude: float, longitude: float) -> Any:
        """Turn coordinates into the addresses at that point.

        Args:
            latitude: A validated latitude.
            longitude: A validated longitude.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            GEOCODE_HOST,
            "/v4/geocode/location",
            "reverse geocoding a coordinate",
            params={
                "location.latitude": _coordinate(latitude),
                "location.longitude": _coordinate(longitude),
                "languageCode": self._language,
            },
        )

    def geocode_place(self, place_id: str) -> Any:
        """Look up the geometry and address of a known place id.

        Args:
            place_id: A validated place id.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            GEOCODE_HOST,
            _place_path("/v4/geocode/places/", place_id),
            "geocoding a place id",
            params={"languageCode": self._language},
        )

    # --- Places ---------------------------------------------------------------

    def search_text(self, body: dict[str, Any], field_mask: str) -> Any:
        """Run a Places text search.

        Args:
            body: The validated request body.
            field_mask: The server-chosen field mask.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            PLACES_HOST,
            "/v1/places:searchText",
            "searching places by text",
            method="POST",
            json_body=self._with_locale(body),
            field_mask=field_mask,
        )

    def search_nearby(self, body: dict[str, Any], field_mask: str) -> Any:
        """Run a Places nearby search.

        Args:
            body: The validated request body.
            field_mask: The server-chosen field mask.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            PLACES_HOST,
            "/v1/places:searchNearby",
            "searching places nearby",
            method="POST",
            json_body=self._with_locale(body),
            field_mask=field_mask,
        )

    def place_details(self, place_id: str, field_mask: str) -> Any:
        """Read one place's details.

        Args:
            place_id: A validated place id.
            field_mask: The server-chosen field mask.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        params = {"languageCode": self._language}
        if self._region:
            params["regionCode"] = self._region
        return self._call(
            PLACES_HOST,
            _place_path("/v1/places/", place_id),
            "reading a place's details",
            params=params,
            field_mask=field_mask,
        )

    def autocomplete(self, body: dict[str, Any]) -> Any:
        """Ask Places for completions of a partial query.

        Args:
            body: The validated request body.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        # NOTE: no field mask. Autocomplete's reference documents the header as
        # optional, and the default response — the predictions themselves — is
        # exactly what this tool returns.
        return self._call(
            PLACES_HOST,
            "/v1/places:autocomplete",
            "completing a place query",
            method="POST",
            json_body=self._with_locale(body),
        )

    # --- Routes ---------------------------------------------------------------

    def compute_routes(self, body: dict[str, Any], field_mask: str) -> Any:
        """Compute one or more routes between two points.

        Args:
            body: The validated request body.
            field_mask: The server-chosen field mask.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            ROUTES_HOST,
            "/directions/v2:computeRoutes",
            "computing a route",
            method="POST",
            json_body={**body, "languageCode": self._language},
            field_mask=field_mask,
        )

    def compute_route_matrix(self, body: dict[str, Any], field_mask: str) -> Any:
        """Compute travel time and distance for every origin-destination pair.

        Args:
            body: The validated request body.
            field_mask: The server-chosen field mask.

        Returns:
            The parsed response body, which is a JSON array rather than an
            object.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            ROUTES_HOST,
            "/distanceMatrix/v2:computeRouteMatrix",
            "computing a route matrix",
            method="POST",
            json_body={**body, "languageCode": self._language},
            field_mask=field_mask,
        )

    # --- Environment ----------------------------------------------------------

    def time_zone(self, latitude: float, longitude: float, timestamp: int) -> Any:
        """Read the time zone in force at a point and an instant.

        Args:
            latitude: A validated latitude.
            longitude: A validated longitude.
            timestamp: A validated Unix timestamp.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            LEGACY_HOST,
            "/maps/api/timezone/json",
            "reading a time zone",
            params={
                "location": f"{_coordinate(latitude)},{_coordinate(longitude)}",
                "timestamp": str(timestamp),
                "language": self._language,
            },
        )

    def elevation(self, points: list[tuple[float, float]]) -> Any:
        """Read the elevation at each of a list of points.

        Args:
            points: Validated latitude/longitude pairs, already bounded in
                count by the caller.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        joined = "|".join(
            f"{_coordinate(latitude)},{_coordinate(longitude)}" for latitude, longitude in points
        )
        return self._call(
            LEGACY_HOST,
            "/maps/api/elevation/json",
            "reading elevations",
            params={"locations": joined},
        )

    def air_quality(self, body: dict[str, Any]) -> Any:
        """Read current air quality at a point.

        Args:
            body: The validated request body.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            AIR_QUALITY_HOST,
            "/v1/currentConditions:lookup",
            "reading air quality",
            method="POST",
            json_body={**body, "languageCode": self._language},
        )

    def validate_address(self, body: dict[str, Any]) -> Any:
        """Validate and normalize a postal address.

        Args:
            body: The validated request body.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: Google refused the request.
        """
        return self._call(
            ADDRESS_VALIDATION_HOST,
            "/v1:validateAddress",
            "validating an address",
            method="POST",
            json_body={**body, "languageOptions": {"returnEnglishLatinAddress": False}},
        )

    # --- Plumbing -------------------------------------------------------------

    def _with_locale(self, body: dict[str, Any]) -> dict[str, Any]:
        """Add the configured language and region to a Places request body.

        Args:
            body: The request body built by a tool.

        Returns:
            The body with `languageCode`, and `regionCode` when one is
            configured. A value the tool already set wins, so a caller can ask
            for a specific locale.
        """
        merged = {"languageCode": self._language, **body}
        if self._region and "regionCode" not in merged:
            merged["regionCode"] = self._region
        return merged

    def _call(
        self,
        host: str,
        path: str,
        safe_label: str,
        *,
        method: str = "GET",
        params: dict[str, str] | None = None,
        json_body: Any = None,
        field_mask: str | None = None,
    ) -> Any:
        """Make one request and turn any refusal into a MapsApiError.

        Args:
            host: One of the module's host constants.
            path: The request path.
            safe_label: A credential-free description of the request.
            method: The HTTP method.
            params: Query parameters, before the key is added.
            json_body: A JSON-serializable body, or None.
            field_mask: The server-chosen field mask, or None.

        Returns:
            The parsed response body.

        Raises:
            MapsApiError: The HTTP status was not 2xx, or the body reported a
                failure status of its own.
        """
        response = self._http.request(
            host,
            path,
            safe_label=safe_label,
            method=method,
            params=params,
            json_body=json_body,
            field_mask=field_mask,
        )
        if response.status >= 400:
            raise MapsApiError(_http_failure(response, safe_label))
        _check_body_status(response.body, safe_label)
        return response.body


def _check_body_status(body: Any, safe_label: str) -> None:
    """Raise when a 200 response carries a failure status in its body.

    Args:
        body: The parsed response body.
        safe_label: A credential-free description of the request.

    Raises:
        MapsApiError: The body's own `status` reports a failure.

    Notes:
        SECURITY: the Time Zone and Elevation web services answer a quota
        refusal, a disabled API, and a rejected key with HTTP 200 and a
        `status` field. A caller that checked only the HTTP status would read
        `OVER_QUERY_LIMIT` as a successful result with no data — the same shape
        a genuine empty answer has. The two are distinguished here, and only
        here, so no tool has to remember.
    """
    if not isinstance(body, dict):
        return
    status = body.get("status")
    if not isinstance(status, str) or status in {"OK", *_EMPTY_STATUSES}:
        return
    detail = body.get("error_message")
    suffix = f" Google said: {str(detail)[:200]}" if isinstance(detail, str) else ""
    raise MapsApiError(f"{_status_advice(status, safe_label)}{suffix}")


def _status_advice(status: str, safe_label: str) -> str:
    """Turn a legacy status string into something a model can act on.

    Args:
        status: The `status` field from the response body.
        safe_label: A credential-free description of the request.

    Returns:
        A sentence naming what failed and what to do about it.
    """
    if status in {"OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"}:
        return (
            f"Google refused {safe_label}: the project is over its quota for "
            "this API. Tell the user rather than retrying."
        )
    if status == "REQUEST_DENIED":
        return (
            f"Google refused {safe_label}: the API key is not authorized for "
            "this API. The project owner has to enable it and allow this key to "
            "call it. Tell the user rather than trying another tool."
        )
    if status == "INVALID_REQUEST":
        return f"Google refused {safe_label}: the request was malformed."
    return f"Google refused {safe_label} with status {status[:64]}."


def _http_failure(response: HttpResponse, safe_label: str) -> str:
    """Turn a non-2xx response into something a model can act on.

    Args:
        response: The failed response.
        safe_label: A credential-free description of the request.

    Returns:
        A sentence naming what failed and what to do about it.
    """
    status, message = response.error_detail()
    # The remote message is already clipped by error_detail, and it is Google's
    # prose rather than a user's, but it is still text this server did not
    # write, so it is presented as a quotation rather than as this server's own
    # instruction.
    detail = f" Google said: {message}" if message else ""

    if response.status == 429:
        return (
            f"Google rate-limited {safe_label}. Wait before asking again, and "
            f"tell the user rather than retrying in a loop.{detail}"
        )
    if response.status == 403:
        return (
            f"Google refused {safe_label}: the API key is not authorized for "
            "this API, or the API is not enabled on the project. The project "
            f"owner has to fix that; no other tool will work around it.{detail}"
        )
    if response.status == 400:
        return f"Google rejected {safe_label} as malformed.{detail}"
    if response.status == 404:
        return f"Google found nothing for {safe_label}.{detail}"
    if response.status >= 500:
        return (
            f"Google's own service failed on {safe_label}. This is temporary; "
            f"one retry is reasonable.{detail}"
        )
    code = status or str(response.status)
    return f"Google refused {safe_label} ({code[:64]}).{detail}"


def build_client(
    api_key: str, timeout_seconds: float, language_code: str, region_code: str | None
) -> MapsClient:
    """Build the client every tool uses.

    Args:
        api_key: The Maps Platform API key.
        timeout_seconds: Per-request connect and read timeout.
        language_code: Default language for names and instructions.
        region_code: Default CLDR region for biasing, or None.

    Returns:
        The client, with its transport already built.
    """
    return MapsClient(BoundedHttpClient(api_key, timeout_seconds), language_code, region_code)
