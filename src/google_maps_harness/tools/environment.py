# =============================================================================
# environment.py — the physical facts about a point: what time it is there, how
# high it is, and what the air is like.
#
# Part of: google-maps-harness, tool group "Environment". Called by: the MCP
# client. Calls: runtime.maps() for the Time Zone, Elevation, and Air Quality
# APIs.
# Security: Time Zone and Elevation are Google's legacy web services, which
# report a quota refusal or a disabled API inside an HTTP 200 body. maps_api.py
# reads that body status, so a refusal cannot reach a tool here disguised as an
# empty result. Every coordinate is checked for finiteness before it is
# formatted into a query string, and the elevation point list is bounded before
# the URL is built rather than after.
# =============================================================================
"""Read time zone, elevation, and air quality for a point."""

from typing import Any

from mcp.types import ToolAnnotations

from ..maps_api import (
    MAX_ELEVATION_POINTS,
    MapsApiError,
    validate_latitude,
    validate_longitude,
    validate_timestamp,
)
from ..runtime import guarded, maps, mcp, wrap

TOOL_NAMES = ("get_time_zone", "get_elevation", "get_air_quality")

# What Google will compute beyond the raw index. Constants, not arguments: each
# one enlarges the response, and the set is chosen so a decision has what it
# needs without pulling the whole pollutant encyclopaedia into context.
_AIR_QUALITY_EXTRAS = ("HEALTH_RECOMMENDATIONS", "DOMINANT_POLLUTANT_CONCENTRATION", "LOCAL_AQI")


@mcp.tool(
    description=(
        "Get the time zone in force at a coordinate: its IANA id, its name, "
        "and its offset from UTC including daylight saving. Pass the moment "
        "you care about, because the offset changes across the year. Use this "
        "before reasoning about whether somewhere is open, or what local time "
        "a meeting lands at."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def get_time_zone(latitude: float, longitude: float, timestamp: int) -> dict[str, Any]:
    """Read the time zone at a point and an instant.

    Args:
        latitude: Degrees north, between -90 and 90.
        longitude: Degrees east, between -180 and 180.
        timestamp: The moment in question, in seconds since 1970-01-01 UTC.
            Daylight saving means the answer depends on it.

    Returns:
        The wrapped time zone response. Local time is the timestamp plus
        rawOffset plus dstOffset, both in seconds.
    """
    return wrap(
        maps().time_zone(
            validate_latitude(latitude),
            validate_longitude(longitude),
            validate_timestamp(timestamp),
        )
    )


@mcp.tool(
    description=(
        "Get the elevation in metres above sea level at one point or at "
        "several. Pass coordinates as a list of 'lat,lng' strings, at most 50. "
        "Use it to judge terrain — how much a route climbs, whether a site is "
        "on a floodplain, how exposed a location is."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def get_elevation(coordinates: list[str]) -> dict[str, Any]:
    """Read elevation at each of a list of points.

    Args:
        coordinates: Points as "lat,lng" strings, such as "39.0,-79.5". At most
            50, because the whole list is encoded into one URL.

    Returns:
        The wrapped elevation response, one result per point in the order given.
    """
    # SECURITY: the count is checked before any point is parsed and before the
    # URL is built, so an oversized list is refused rather than assembled and
    # then rejected (ledger LL-20).
    if not coordinates:
        raise MapsApiError("coordinates must hold at least one 'lat,lng' point.")
    if len(coordinates) > MAX_ELEVATION_POINTS:
        raise MapsApiError(
            f"coordinates takes at most {MAX_ELEVATION_POINTS} points in one call. Split the list."
        )
    points = [_point(entry, index) for index, entry in enumerate(coordinates)]
    return wrap(maps().elevation(points), points=len(points))


@mcp.tool(
    description=(
        "Get current air quality at a coordinate: the local and universal air "
        "quality index, the dominant pollutant and its concentration, and "
        "health guidance for the general population and for sensitive groups. "
        "Needs the Air Quality API enabled on the project, which is separate "
        "from the other Maps APIs."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def get_air_quality(latitude: float, longitude: float) -> dict[str, Any]:
    """Read current air quality at a point.

    Args:
        latitude: Degrees north, between -90 and 90.
        longitude: Degrees east, between -180 and 180.

    Returns:
        The wrapped air quality response.
    """
    body = {
        "location": {
            "latitude": validate_latitude(latitude),
            "longitude": validate_longitude(longitude),
        },
        "universalAqi": True,
        "extraComputations": list(_AIR_QUALITY_EXTRAS),
    }
    return wrap(maps().air_quality(body))


def _point(value: str, index: int) -> tuple[float, float]:
    """Parse and validate one "lat,lng" string.

    Args:
        value: The candidate point.
        index: Its position in the list, for the error message.

    Returns:
        The validated latitude and longitude.

    Raises:
        MapsApiError: The string is not two numbers separated by a comma, or a
            number is out of range or not finite.
    """
    field = f"coordinates[{index}]"
    latitude, separator, longitude = value.partition(",")
    if not separator:
        raise MapsApiError(f"{field} must be 'lat,lng', such as '39.0,-79.5'.")
    try:
        parsed_latitude = float(latitude)
        parsed_longitude = float(longitude)
    except ValueError as error:
        raise MapsApiError(f"{field} must be 'lat,lng', such as '39.0,-79.5'.") from error
    # The finiteness check lives in the validators, which reject the NaN and
    # Infinity that float() happily produces from "nan" and "1e999".
    return (
        validate_latitude(parsed_latitude, f"{field} latitude"),
        validate_longitude(parsed_longitude, f"{field} longitude"),
    )
