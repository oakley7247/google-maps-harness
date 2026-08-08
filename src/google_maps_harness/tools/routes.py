# =============================================================================
# routes.py — how long it takes to get from one place to another, and the
# many-to-many version of the same question.
#
# Part of: google-maps-harness, tool group "Routes". Called by: the MCP client.
# Calls: runtime.maps() for the Routes API.
# Security: the matrix is the expensive tool in this server — Google bills it
# per origin-destination element, so its size is bounded here before the request
# is built rather than left to Google's own ceiling, which is six times larger.
# Waypoints are parsed into exactly one of a place id, a coordinate pair, or an
# address, each validated in its own terms. Field masks are constants, so no
# model output reaches an HTTP header.
# =============================================================================
"""Compute routes and travel-time matrices."""

from datetime import UTC, datetime
from typing import Any

from mcp.types import ToolAnnotations

from ..maps_api import (
    MAX_INTERMEDIATES,
    MAX_MATRIX_ELEMENTS,
    MapsApiError,
    parse_waypoint,
    validate_choice,
    validate_text,
)
from ..runtime import (
    ROUTE_FIELD_MASK,
    ROUTE_MATRIX_FIELD_MASK,
    ROUTE_STEPS_FIELD_MASK,
    guarded,
    maps,
    mcp,
    wrap,
)

TOOL_NAMES = ("compute_route", "compute_route_matrix")

TRAVEL_MODES = ("DRIVE", "BICYCLE", "WALK", "TWO_WHEELER", "TRANSIT")

# NOTE: Google accepts routingPreference on road-vehicle modes only, and
# rejects the whole request with a 400 when it is sent alongside WALK,
# BICYCLE, or TRANSIT. That is why the preference is attached conditionally
# below rather than always — read off the Routes API reference, not inferred
# from the parameter's name (ledger LL-26).
_TRAFFIC_MODES = frozenset({"DRIVE", "TWO_WHEELER"})
_ROUTING_PREFERENCES = ("TRAFFIC_AWARE", "TRAFFIC_UNAWARE", "TRAFFIC_AWARE_OPTIMAL")

UNITS = ("METRIC", "IMPERIAL")

# An RFC 3339 timestamp is at most a few dozen characters. Bounded before it is
# parsed so a long string cannot reach the parser at all.
_MAX_TIME_CHARS = 40


@mcp.tool(
    description=(
        "Get the driving, walking, cycling, or transit route between two "
        "places, with total time, distance, and any warnings. Each endpoint is "
        "written one of three ways: 'place_id:ChIJ...' from a place search "
        "(most precise), '40.7580,-73.9855' as a coordinate pair, or plain "
        "text for Google to resolve as an address. Traffic is included on "
        "DRIVE and TWO_WHEELER. Set include_steps only when you need "
        "turn-by-turn text; it makes the response much larger."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def compute_route(
    origin: str,
    destination: str,
    travel_mode: str = "DRIVE",
    departure_time: str | None = None,
    intermediates: list[str] | None = None,
    avoid_tolls: bool = False,
    avoid_highways: bool = False,
    avoid_ferries: bool = False,
    alternatives: bool = False,
    units: str = "IMPERIAL",
    include_steps: bool = False,
) -> dict[str, Any]:
    """Compute one route.

    Args:
        origin: Where the route starts, in one of the three accepted forms.
        destination: Where it ends, in one of the three accepted forms.
        travel_mode: DRIVE, BICYCLE, WALK, TWO_WHEELER, or TRANSIT.
        departure_time: When to leave, as an RFC 3339 UTC timestamp such as
            "2026-08-08T17:30:00Z". Must be in the future; omit for now.
        intermediates: Waypoints to pass through, in order, at most ten.
        avoid_tolls: Prefer routes without toll roads.
        avoid_highways: Prefer routes off motorways.
        avoid_ferries: Prefer routes without ferries.
        alternatives: Ask for more than one route when Google has them.
        units: METRIC or IMPERIAL, for the distances in the instructions.
        include_steps: Return turn-by-turn instructions.

    Returns:
        The wrapped route response.
    """
    mode = validate_choice(travel_mode, TRAVEL_MODES, "travel_mode")
    body: dict[str, Any] = {
        "origin": parse_waypoint(origin, "origin"),
        "destination": parse_waypoint(destination, "destination"),
        "travelMode": mode,
        "computeAlternativeRoutes": bool(alternatives),
        "units": validate_choice(units, UNITS, "units"),
    }
    if mode in _TRAFFIC_MODES:
        body["routingPreference"] = _ROUTING_PREFERENCES[0]
    if departure_time is not None:
        body["departureTime"] = _rfc3339(departure_time)
    if intermediates:
        if len(intermediates) > MAX_INTERMEDIATES:
            raise MapsApiError(
                f"intermediates takes at most {MAX_INTERMEDIATES} waypoints. "
                "Split a longer trip into several routes."
            )
        body["intermediates"] = [
            parse_waypoint(entry, f"intermediates[{index}]")
            for index, entry in enumerate(intermediates)
        ]

    modifiers = _modifiers(avoid_tolls, avoid_highways, avoid_ferries)
    if modifiers:
        body["routeModifiers"] = modifiers

    mask = ROUTE_STEPS_FIELD_MASK if include_steps else ROUTE_FIELD_MASK
    return wrap(maps().compute_routes(body, mask))


@mcp.tool(
    description=(
        "Get travel time and distance for every origin against every "
        "destination in one call — the comparison tool. Use it to pick the "
        "nearest branch, rank candidate sites by drive time, or check which "
        "of several suppliers is reachable inside a window. Each endpoint uses "
        "the same three forms as compute_route. Google bills this per pair, so "
        "origins times destinations is capped at 100."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def compute_route_matrix(
    origins: list[str],
    destinations: list[str],
    travel_mode: str = "DRIVE",
    departure_time: str | None = None,
    avoid_tolls: bool = False,
    avoid_highways: bool = False,
    avoid_ferries: bool = False,
) -> dict[str, Any]:
    """Compute travel time and distance for every origin-destination pair.

    Args:
        origins: Where each journey starts, in the accepted forms.
        destinations: Where each journey ends, in the accepted forms.
        travel_mode: DRIVE, BICYCLE, WALK, TWO_WHEELER, or TRANSIT.
        departure_time: When to leave, as an RFC 3339 UTC timestamp. Must be in
            the future; omit for now.
        avoid_tolls: Prefer routes without toll roads.
        avoid_highways: Prefer routes off motorways.
        avoid_ferries: Prefer routes without ferries.

    Returns:
        The wrapped matrix response, one entry per pair, each carrying the
        zero-based index of its origin and destination.
    """
    if not origins or not destinations:
        raise MapsApiError("origins and destinations must each hold at least one entry.")
    # SECURITY: the size check runs before any waypoint is parsed and before the
    # request is built, so the expensive work is never started by a call that
    # will be refused (ledger LL-20). len() on a list is O(1), which makes the
    # guarded form free.
    elements = len(origins) * len(destinations)
    if elements > MAX_MATRIX_ELEMENTS:
        raise MapsApiError(
            f"{len(origins)} origins against {len(destinations)} destinations is "
            f"{elements} pairs, past the {MAX_MATRIX_ELEMENTS} this server "
            "allows in one call. Google bills every pair. Narrow the lists, or "
            "run the comparison in batches."
        )

    modifiers = _modifiers(avoid_tolls, avoid_highways, avoid_ferries)
    body: dict[str, Any] = {
        "origins": [
            _matrix_waypoint(entry, f"origins[{index}]", modifiers)
            for index, entry in enumerate(origins)
        ],
        "destinations": [
            _matrix_waypoint(entry, f"destinations[{index}]", None)
            for index, entry in enumerate(destinations)
        ],
        "travelMode": validate_choice(travel_mode, TRAVEL_MODES, "travel_mode"),
    }
    if body["travelMode"] in _TRAFFIC_MODES:
        body["routingPreference"] = _ROUTING_PREFERENCES[0]
    if departure_time is not None:
        body["departureTime"] = _rfc3339(departure_time)

    return wrap(
        maps().compute_route_matrix(body, ROUTE_MATRIX_FIELD_MASK),
        pairs=elements,
    )


def _matrix_waypoint(value: str, field: str, modifiers: dict[str, bool] | None) -> dict[str, Any]:
    """Wrap a parsed waypoint in the shape the matrix endpoint takes.

    Args:
        value: The endpoint as written by the caller.
        field: The argument's name, for the error message.
        modifiers: Route modifiers to attach, or None. The matrix endpoint
            takes them per origin rather than once for the request.

    Returns:
        The origin or destination object.
    """
    entry: dict[str, Any] = {"waypoint": parse_waypoint(value, field)}
    if modifiers:
        entry["routeModifiers"] = modifiers
    return entry


def _modifiers(tolls: bool, highways: bool, ferries: bool) -> dict[str, bool]:
    """Build the route modifiers, omitting the ones nobody asked for.

    Args:
        tolls: Avoid toll roads.
        highways: Avoid motorways.
        ferries: Avoid ferries.

    Returns:
        The modifiers Google takes. Empty when all three are off, so the key is
        left out of the request entirely rather than sent as three falses.
    """
    modifiers: dict[str, bool] = {}
    if tolls:
        modifiers["avoidTolls"] = True
    if highways:
        modifiers["avoidHighways"] = True
    if ferries:
        modifiers["avoidFerries"] = True
    return modifiers


def _rfc3339(value: str) -> str:
    """Check a departure time and return it in the exact form Google takes.

    Args:
        value: An RFC 3339 timestamp, such as "2026-08-08T17:30:00Z".

    Returns:
        The timestamp normalized to UTC with a trailing `Z`.

    Raises:
        MapsApiError: The value does not parse as a timestamp, or carries no
            time zone.
    """
    text = validate_text(value, "departure_time", _MAX_TIME_CHARS)
    try:
        # NOTE: Python 3.11's fromisoformat accepts the trailing `Z` that
        # earlier versions rejected, which is why this project's floor is 3.11.
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise MapsApiError(
            "departure_time must be an RFC 3339 timestamp such as 2026-08-08T17:30:00Z."
        ) from error
    if parsed.tzinfo is None:
        # A naive timestamp would be read as some machine's local time, and
        # which machine is not something either side has agreed on.
        raise MapsApiError("departure_time must name its time zone, such as 2026-08-08T17:30:00Z.")
    # What is sent is what was parsed, not the text that arrived (ledger LL-2):
    # an equivalent spelling in another offset becomes one canonical UTC form.
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
