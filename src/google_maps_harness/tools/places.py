# =============================================================================
# places.py — finding businesses and points of interest, and reading what
# Google knows about one.
#
# Part of: google-maps-harness, tool group "Places". Called by: the MCP client.
# Calls: runtime.maps() for the Places API (New).
# Security: this is the highest-risk group in the server, because its responses
# carry prose that members of the public wrote — place names, editorial
# summaries, and reviews. Three controls apply. The detail tier is a name from a
# fixed table, never a caller-supplied field mask, so no model output reaches an
# HTTP header and no call can silently ask for the most expensive fields. The
# `atmosphere` tier, which is the one that returns reviews, is behind an
# operator flag. And every string returned is stripped of control characters and
# labelled as data by wrap().
# =============================================================================
"""Search for places and read their details."""

from typing import Any

from mcp.types import ToolAnnotations

from ..maps_api import (
    validate_choice,
    validate_latitude,
    validate_longitude,
    validate_page_size,
    validate_place_id,
    validate_radius,
    validate_text,
)
from ..runtime import (
    DETAIL_TIER_NAMES,
    guarded,
    maps,
    mcp,
    places_field_mask,
    require_atmosphere,
    wrap,
)

TOOL_NAMES = (
    "search_places_by_text",
    "search_places_nearby",
    "get_place_details",
    "autocomplete_places",
)

# Google's own ranking options on each endpoint. Text search ranks by relevance
# or distance; nearby search ranks by popularity or distance.
_TEXT_RANKING = ("RELEVANCE", "DISTANCE")
_NEARBY_RANKING = ("POPULARITY", "DISTANCE")

# Google's page-token strings are opaque and long. Bounded and character-checked
# like any other value that reaches a request body.
_MAX_PAGE_TOKEN_CHARS = 2048

# Nearby search takes up to five types. The cap is Google's; it is enforced here
# so the refusal names the limit instead of arriving as an opaque 400.
_MAX_INCLUDED_TYPES = 5
_MAX_TYPE_CHARS = 64


@mcp.tool(
    description=(
        "Search for places by describing them in words — 'ramen near Union "
        "Square', 'hardware stores in Asheville NC', 'EV charging on I-81'. "
        "Returns up to 20 places per page and a token for the next page, to a "
        "maximum of 60 places. Set detail to control how much you get back and "
        "what it costs: essentials (address and coordinates), pro (adds names "
        "and business status), enterprise (adds ratings, hours, phone, and "
        "website), atmosphere (adds reviews and summaries; may be disabled)."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def search_places_by_text(
    query: str,
    detail: str = "pro",
    page_size: int = 10,
    page_token: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_metres: float | None = None,
    open_now: bool = False,
    min_rating: float | None = None,
    rank_by: str = "RELEVANCE",
) -> dict[str, Any]:
    """Search places by text.

    Args:
        query: What to look for, in plain words.
        detail: One of essentials, pro, enterprise, atmosphere.
        page_size: Places per page, 1 to 20.
        page_token: The `nextPageToken` from a previous call, to page further.
        latitude: Centre of the area to bias toward. Pass with longitude and
            radius_metres, or omit all three.
        longitude: Centre of the area to bias toward.
        radius_metres: Radius of that area, up to 50000.
        open_now: Return only places open at the moment of the call.
        min_rating: Keep only places rated at least this, 0 to 5.
        rank_by: RELEVANCE or DISTANCE. DISTANCE needs a bias centre.

    Returns:
        The wrapped search response.
    """
    tier = validate_choice(detail, DETAIL_TIER_NAMES, "detail")
    require_atmosphere("search_places_by_text", tier)

    body: dict[str, Any] = {
        "textQuery": validate_text(query, "query"),
        "pageSize": validate_page_size(page_size),
        "rankPreference": validate_choice(rank_by, _TEXT_RANKING, "rank_by"),
    }
    if page_token is not None:
        body["pageToken"] = validate_text(page_token, "page_token", _MAX_PAGE_TOKEN_CHARS)
    circle = _circle(latitude, longitude, radius_metres)
    if circle is not None:
        body["locationBias"] = {"circle": circle}
    if open_now:
        body["openNow"] = True
    if min_rating is not None:
        # Google accepts 0 to 5 in half-star steps and rejects anything else, so
        # the step is enforced here rather than surfacing as an opaque 400.
        rating = validate_choice(f"{float(min_rating):.1f}", _RATING_STEPS, "min_rating")
        body["minRating"] = float(rating)

    # The page token is a top-level response field, so it has to be named in the
    # mask alongside the per-place fields or paging silently stops after one page.
    mask = places_field_mask(tier, prefix="places.") + ",nextPageToken"
    return wrap(maps().search_text(body, mask), detail=tier)


@mcp.tool(
    description=(
        "List places inside a circle, ranked by popularity or by distance from "
        "its centre. Use this when you have a point and want what is around it; "
        "use search_places_by_text when you can describe what you want. Up to "
        "20 places, no paging. Filter by type with values such as restaurant, "
        "pharmacy, gas_station, hospital, or park."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def search_places_nearby(
    latitude: float,
    longitude: float,
    radius_metres: float = 1000.0,
    included_types: list[str] | None = None,
    detail: str = "pro",
    max_results: int = 10,
    rank_by: str = "POPULARITY",
) -> dict[str, Any]:
    """Search places within a circle.

    Args:
        latitude: Centre of the circle.
        longitude: Centre of the circle.
        radius_metres: Radius, above 0 and up to 50000.
        included_types: Up to five Google place types to keep.
        detail: One of essentials, pro, enterprise, atmosphere.
        max_results: Places to return, 1 to 20.
        rank_by: POPULARITY or DISTANCE.

    Returns:
        The wrapped search response.
    """
    tier = validate_choice(detail, DETAIL_TIER_NAMES, "detail")
    require_atmosphere("search_places_nearby", tier)

    circle = _circle(latitude, longitude, radius_metres)
    body: dict[str, Any] = {
        "locationRestriction": {"circle": circle},
        "maxResultCount": validate_page_size(max_results, "max_results"),
        "rankPreference": validate_choice(rank_by, _NEARBY_RANKING, "rank_by"),
    }
    if included_types:
        if len(included_types) > _MAX_INCLUDED_TYPES:
            raise ValueError(f"included_types takes at most {_MAX_INCLUDED_TYPES} types.")
        body["includedTypes"] = [
            validate_text(entry, f"included_types[{index}]", _MAX_TYPE_CHARS)
            for index, entry in enumerate(included_types)
        ]

    return wrap(maps().search_nearby(body, places_field_mask(tier, prefix="places.")), detail=tier)


@mcp.tool(
    description=(
        "Read everything Google holds about one place: address, coordinates, "
        "opening hours, rating, price level, phone, and website. Takes a "
        "place id from a search or autocomplete. Set detail to control cost — "
        "enterprise is the tier that carries hours, ratings, and contact "
        "details; atmosphere adds reviews and may be disabled."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def get_place_details(place_id: str, detail: str = "enterprise") -> dict[str, Any]:
    """Read one place's details.

    Args:
        place_id: A place id from a search or autocomplete result.
        detail: One of essentials, pro, enterprise, atmosphere.

    Returns:
        The wrapped details response.
    """
    tier = validate_choice(detail, DETAIL_TIER_NAMES, "detail")
    require_atmosphere("get_place_details", tier)
    return wrap(
        maps().place_details(validate_place_id(place_id), places_field_mask(tier)),
        detail=tier,
    )


@mcp.tool(
    description=(
        "Complete a partial place name or address into up to five candidates, "
        "each with a place id. Use this to pin down what the user meant before "
        "spending a search or a details lookup, and to turn a vague name into "
        "an id the other tools can use. The cheapest way to resolve a place."
    ),
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
@guarded
def autocomplete_places(
    partial_text: str,
    latitude: float | None = None,
    longitude: float | None = None,
    radius_metres: float | None = None,
) -> dict[str, Any]:
    """Complete a partial place query.

    Args:
        partial_text: What the user has typed so far.
        latitude: Centre of the area to bias toward, with longitude and
            radius_metres.
        longitude: Centre of the area to bias toward.
        radius_metres: Radius of that area, up to 50000.

    Returns:
        The wrapped autocomplete response.
    """
    body: dict[str, Any] = {"input": validate_text(partial_text, "partial_text")}
    circle = _circle(latitude, longitude, radius_metres)
    if circle is not None:
        body["locationBias"] = {"circle": circle}
    return wrap(maps().autocomplete(body))


# Google accepts a minimum rating in half-star steps only. Held as strings so
# the comparison is exact rather than a float equality test.
_RATING_STEPS = ("0.0", "0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0")


def _circle(
    latitude: float | None, longitude: float | None, radius_metres: float | None
) -> dict[str, Any] | None:
    """Build a validated circle, or None when no area was given.

    Args:
        latitude: Centre latitude, or None.
        longitude: Centre longitude, or None.
        radius_metres: Radius in metres, or None.

    Returns:
        The circle object Places takes, or None when all three were omitted.

    Raises:
        MapsApiError: A coordinate or the radius is out of range.
        ValueError: Some but not all three were given. A partial circle is a
            mistake worth naming rather than a default worth inventing — a
            radius with no centre would otherwise silently search the wrong
            place.
    """
    if latitude is None and longitude is None and radius_metres is None:
        return None
    if latitude is None or longitude is None or radius_metres is None:
        raise ValueError(
            "latitude, longitude, and radius_metres go together: pass all three "
            "to bias or restrict the search, or none of them."
        )
    return {
        "center": {
            "latitude": validate_latitude(latitude),
            "longitude": validate_longitude(longitude),
        },
        "radius": validate_radius(radius_metres),
    }
