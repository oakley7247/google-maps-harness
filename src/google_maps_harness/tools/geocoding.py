# =============================================================================
# geocoding.py — turning text into coordinates, coordinates into text, and
# checking whether an address is real.
#
# Part of: google-maps-harness, tool group "Geocoding". Called by: the MCP
# client. Calls: runtime.maps() for the Geocoding and Address Validation APIs.
# Security: every coordinate is checked for finiteness before it reaches a URL,
# every place id is matched against a character class and then percent-encoded
# into the path, and every free-text argument is length-capped with control
# characters refused. Everything returned is written by Google's data partners
# and is labelled as data by wrap().
# =============================================================================
"""Convert between addresses, coordinates, and place ids."""

from typing import Any

from mcp.types import ToolAnnotations

from ..maps_api import (
    MapsApiError,
    validate_address_lines,
    validate_latitude,
    validate_longitude,
    validate_place_id,
    validate_text,
)
from ..runtime import guarded, maps, mcp, wrap

# Names every tool this module registers. `tests/test_tools.py` reconciles the
# union of these lists against what the server actually exposes, so a tool added
# here and forgotten there fails the suite.
TOOL_NAMES = (
    "geocode_address",
    "reverse_geocode",
    "geocode_place_id",
    "validate_address",
)

# A CLDR region code is two letters. Checked here rather than passed through,
# because it lands in a query string.
_REGION_LENGTH = 2


@mcp.tool(
    description=(
        "Turn an address, a landmark name, or a plus code into coordinates, a "
        "normalized address, and its component parts (street, city, postal "
        "code, country). Use this before any tool that needs a latitude and "
        "longitude. Returns several candidates when the text is ambiguous."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def geocode_address(address: str, region_code: str | None = None) -> dict[str, Any]:
    """Geocode an address.

    Args:
        address: The address, landmark, or plus code to resolve.
        region_code: Two-letter CLDR region to bias toward, such as "US" or
            "GB". Disambiguates names that exist in several countries.

    Returns:
        The wrapped geocoding response.
    """
    return wrap(maps().geocode_address(validate_text(address, "address"), _region(region_code)))


@mcp.tool(
    description=(
        "Turn a latitude and longitude into the addresses at that point, from "
        "the exact street address outward to the neighbourhood, city, and "
        "country. Use this to describe where a coordinate is."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def reverse_geocode(latitude: float, longitude: float) -> dict[str, Any]:
    """Reverse geocode a coordinate.

    Args:
        latitude: Degrees north, between -90 and 90.
        longitude: Degrees east, between -180 and 180.

    Returns:
        The wrapped geocoding response.
    """
    return wrap(maps().reverse_geocode(validate_latitude(latitude), validate_longitude(longitude)))


@mcp.tool(
    description=(
        "Look up the coordinates, normalized address, and address components "
        "of a place id returned by a place search. More precise than "
        "geocoding the place's name, and cheaper than a full details lookup "
        "when location is all you need."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def geocode_place_id(place_id: str) -> dict[str, Any]:
    """Geocode a place id.

    Args:
        place_id: A place id from search_places_by_text, search_places_nearby,
            or autocomplete_places.

    Returns:
        The wrapped geocoding response.
    """
    return wrap(maps().geocode_place(validate_place_id(place_id)))


@mcp.tool(
    description=(
        "Check whether a postal address is real and deliverable, and get back "
        "the corrected, standardized version plus a note of anything that was "
        "missing, unconfirmed, or inferred. Use this before relying on an "
        "address a user typed; use geocode_address instead when you only need "
        "coordinates."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def validate_address(address_lines: list[str], region_code: str) -> dict[str, Any]:
    """Validate a postal address.

    Args:
        address_lines: The address as it would be written, one line per line,
            most specific first. At most five lines.
        region_code: Two-letter CLDR region the address is in, such as "US".
            Required: validation rules are national, so Google needs to know
            which country's rules to apply.

    Returns:
        The wrapped validation response.
    """
    body = {
        "address": {
            "regionCode": _region(region_code),
            "addressLines": validate_address_lines(address_lines),
        }
    }
    return wrap(maps().validate_address(body))


def _region(value: str | None) -> str | None:
    """Validate an optional CLDR region code.

    Args:
        value: The candidate code, or None.

    Returns:
        The uppercased code, or None when nothing was supplied. The uppercased
        form is what is returned and therefore what is sent, so what was
        checked is what is used (ledger LL-2).

    Raises:
        MapsApiError: The value is not two letters.
    """
    if value is None:
        return None
    text = validate_text(value, "region_code", _REGION_LENGTH)
    if not text.isascii() or not text.isalpha():
        # SECURITY: `isalpha` alone is Unicode-aware and admits letters from
        # any script; pairing it with `isascii` keeps this to the two ASCII
        # letters a CLDR region code actually is (ledger LL-3).
        raise MapsApiError("region_code must be two ASCII letters, such as US or GB.")
    return text.upper()
