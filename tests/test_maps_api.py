# =============================================================================
# test_maps_api.py — the validators, and the error shapes Google hides in a 200.
#
# Part of: google-maps-harness test suite.
# =============================================================================
"""Argument validation, waypoint parsing, and failure translation."""

import unittest

from google_maps_harness.budget import begin_call, end_call
from google_maps_harness.maps_api import (
    MAX_RADIUS_METRES,
    MapsApiError,
    MapsClient,
    parse_waypoint,
    validate_address_lines,
    validate_choice,
    validate_latitude,
    validate_longitude,
    validate_page_size,
    validate_place_id,
    validate_radius,
    validate_text,
    validate_timestamp,
)

from .support import FakeTransport, failure, ok


class TestCoordinates(unittest.TestCase):
    """Coordinates that are numbers, and values that only look like numbers."""

    def test_ordinary_coordinates_pass(self) -> None:
        """The positive case, so the checks below are not simply refusing all."""
        self.assertEqual(validate_latitude(40.7580), 40.7580)
        self.assertEqual(validate_longitude(-73.9855), -73.9855)
        self.assertEqual(validate_latitude(-90.0), -90.0)
        self.assertEqual(validate_longitude(180.0), 180.0)

    def test_nan_is_refused(self) -> None:
        """A JSON parser will hand you NaN; every range comparison with it is
        False, which is what the check relies on (ledger LL-15)."""
        with self.assertRaises(MapsApiError):
            validate_latitude(float("nan"))

    def test_infinity_is_refused(self) -> None:
        """Infinity would otherwise be clamped rather than rejected."""
        with self.assertRaises(MapsApiError):
            validate_longitude(float("inf"))
        with self.assertRaises(MapsApiError):
            validate_longitude(float("-inf"))

    def test_out_of_range_is_refused(self) -> None:
        """A finite number outside the globe is still not a coordinate."""
        with self.assertRaises(MapsApiError):
            validate_latitude(91.0)
        with self.assertRaises(MapsApiError):
            validate_longitude(180.1)

    def test_a_bool_is_not_a_coordinate(self) -> None:
        """Python says True is 1, which would geocode the Gulf of Guinea."""
        with self.assertRaises(MapsApiError):
            validate_latitude(True)  # type: ignore[arg-type]

    def test_a_string_is_not_a_coordinate(self) -> None:
        """The value is checked after parsing, not coerced into one."""
        with self.assertRaises(MapsApiError):
            validate_latitude("40.7")  # type: ignore[arg-type]


class TestPlaceIds(unittest.TestCase):
    """A place id becomes a URL path segment, so it is checked like one."""

    def test_a_real_shaped_id_passes(self) -> None:
        """The positive case: ids Google actually issues are accepted."""
        self.assertEqual(
            validate_place_id("ChIJj61dQgK6j4AR4GeTYWZsKWw"), "ChIJj61dQgK6j4AR4GeTYWZsKWw"
        )

    def test_a_path_traversal_is_refused(self) -> None:
        """A slash in a path segment addresses a different resource entirely."""
        with self.assertRaises(MapsApiError):
            validate_place_id("../../v1/places:searchText")

    def test_a_query_delimiter_is_refused(self) -> None:
        """A `?` or `&` would rewrite the request's parameters."""
        for candidate in ("abc?key=x", "abc&key=x", "abc#frag"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(MapsApiError):
                    validate_place_id(candidate)

    def test_an_empty_id_is_refused(self) -> None:
        """An empty segment would address the collection rather than a place."""
        with self.assertRaises(MapsApiError):
            validate_place_id("")


class TestFreeText(unittest.TestCase):
    """Model-authored text that lands in a URL or a JSON body."""

    def test_ordinary_text_passes(self) -> None:
        """The positive case, with surrounding whitespace removed."""
        self.assertEqual(validate_text("  ramen near me  ", "query"), "ramen near me")

    def test_control_characters_are_refused(self) -> None:
        """A newline in a query can forge a line in a log and a header."""
        with self.assertRaises(MapsApiError):
            validate_text("ramen\nX-Injected: yes", "query")

    def test_empty_text_is_refused(self) -> None:
        """An empty query is a mistake, not a search for everything."""
        with self.assertRaises(MapsApiError):
            validate_text("   ", "query")

    def test_long_text_is_refused(self) -> None:
        """Bounded before it becomes a URL rather than after Google rejects it."""
        with self.assertRaises(MapsApiError):
            validate_text("x" * 5000, "query")


class TestBoundedNumbers(unittest.TestCase):
    """Page sizes, radii, and timestamps."""

    def test_page_size_range(self) -> None:
        """The positive case and both edges."""
        self.assertEqual(validate_page_size(20), 20)
        with self.assertRaises(MapsApiError):
            validate_page_size(21)
        with self.assertRaises(MapsApiError):
            validate_page_size(0)
        with self.assertRaises(MapsApiError):
            validate_page_size(True)  # type: ignore[arg-type]

    def test_radius_range(self) -> None:
        """Zero is not a circle, and Google's ceiling is 50 km."""
        self.assertEqual(validate_radius(1500.0), 1500.0)
        self.assertEqual(validate_radius(MAX_RADIUS_METRES), MAX_RADIUS_METRES)
        with self.assertRaises(MapsApiError):
            validate_radius(0.0)
        with self.assertRaises(MapsApiError):
            validate_radius(MAX_RADIUS_METRES + 1)
        with self.assertRaises(MapsApiError):
            validate_radius(float("nan"))

    def test_timestamp_range(self) -> None:
        """A bounded whole number of seconds, not a float and not a bool."""
        self.assertEqual(validate_timestamp(1_760_000_000), 1_760_000_000)
        with self.assertRaises(MapsApiError):
            validate_timestamp(-1)
        with self.assertRaises(MapsApiError):
            validate_timestamp(99_999_999_999)

    def test_choice_is_an_allowlist(self) -> None:
        """A value outside the set is refused, and the refusal names the set."""
        self.assertEqual(validate_choice("DRIVE", ("DRIVE", "WALK"), "travel_mode"), "DRIVE")
        with self.assertRaises(MapsApiError) as caught:
            validate_choice("TELEPORT", ("DRIVE", "WALK"), "travel_mode")
        self.assertIn("DRIVE", str(caught.exception))

    def test_address_lines_are_bounded_and_checked(self) -> None:
        """The positive case, then the empty list and the control character."""
        self.assertEqual(
            validate_address_lines(["1600 Amphitheatre Pkwy"]), ["1600 Amphitheatre Pkwy"]
        )
        with self.assertRaises(MapsApiError):
            validate_address_lines([])
        with self.assertRaises(MapsApiError):
            validate_address_lines(["1600 Amphitheatre\nPkwy"])


class TestWaypoints(unittest.TestCase):
    """Three forms, told apart deterministically."""

    def test_a_place_id_form(self) -> None:
        """The prefix wins, and the id behind it is validated."""
        self.assertEqual(
            parse_waypoint("place_id:ChIJj61dQgK6j4AR4GeTYWZsKWw", "origin"),
            {"placeId": "ChIJj61dQgK6j4AR4GeTYWZsKWw"},
        )
        with self.assertRaises(MapsApiError):
            parse_waypoint("place_id:../elsewhere", "origin")

    def test_a_coordinate_pair(self) -> None:
        """Two numbers with a comma become a location."""
        self.assertEqual(
            parse_waypoint("40.7580,-73.9855", "origin"),
            {"location": {"latLng": {"latitude": 40.7580, "longitude": -73.9855}}},
        )

    def test_an_address_with_a_comma_stays_an_address(self) -> None:
        """ "Berlin, Germany" partitions on its comma too; the numeric test is
        what keeps it from being read as a failed coordinate pair."""
        self.assertEqual(
            parse_waypoint("Berlin, Germany", "origin"), {"address": "Berlin, Germany"}
        )

    def test_a_coordinate_pair_of_nan_is_refused(self) -> None:
        """float("nan") parses; the finiteness test is what refuses it."""
        with self.assertRaises(MapsApiError):
            parse_waypoint("nan,nan", "origin")

    def test_an_out_of_range_pair_is_refused(self) -> None:
        """Numeric and comma-separated is not enough to be a coordinate."""
        with self.assertRaises(MapsApiError):
            parse_waypoint("999,999", "origin")


class TestFailureTranslation(unittest.TestCase):
    """Google reports failure in two places; both reach the caller as an error."""

    def setUp(self) -> None:
        """Open a budget, because every request charges one.

        These tests call the client directly rather than through a tool, so
        nothing has opened the budget the transport insists on — which is the
        fail-closed behaviour under test in tests/test_budget.py.
        """
        begin_call(max_requests=10, max_seconds=60.0)
        self.addCleanup(end_call)

    def _client(self, reply: object) -> MapsClient:
        """Build a client whose transport answers with one canned reply.

        Args:
            reply: The HttpResponse to return.

        Returns:
            The client.
        """
        transport = FakeTransport([reply])  # type: ignore[list-item]
        return MapsClient(transport, "en", None)  # type: ignore[arg-type]

    def test_a_200_with_a_failure_status_is_an_error(self) -> None:
        """The Time Zone and Elevation services answer a refused key with 200.

        A caller that read only the HTTP status would see this as a successful
        empty result — the same shape a genuine empty answer has.
        """
        client = self._client(ok({"status": "REQUEST_DENIED", "error_message": "not authorized"}))
        with self.assertRaises(MapsApiError) as caught:
            client.time_zone(40.0, -73.0, 1_760_000_000)
        self.assertIn("not authorized", str(caught.exception))

    def test_a_200_with_an_over_limit_status_says_not_to_retry(self) -> None:
        """A quota refusal has to reach the model as a stop, not a hiccup."""
        client = self._client(ok({"status": "OVER_QUERY_LIMIT"}))
        with self.assertRaises(MapsApiError) as caught:
            client.time_zone(40.0, -73.0, 1_760_000_000)
        self.assertIn("quota", str(caught.exception))

    def test_zero_results_is_a_success(self) -> None:
        """Nothing there is an answer a model can reason about; an error is not."""
        client = self._client(ok({"status": "ZERO_RESULTS", "results": []}))
        self.assertEqual(
            client.time_zone(40.0, -73.0, 1_760_000_000), {"status": "ZERO_RESULTS", "results": []}
        )

    def test_a_403_names_the_project_owner_as_the_fix(self) -> None:
        """A disabled API is not something another tool can work around."""
        client = self._client(
            failure(403, {"error": {"status": "PERMISSION_DENIED", "message": "API not enabled"}})
        )
        with self.assertRaises(MapsApiError) as caught:
            client.search_text({"textQuery": "x"}, "places.id")
        self.assertIn("not enabled", str(caught.exception))

    def test_a_429_says_to_wait_rather_than_loop(self) -> None:
        """A retry loop against a rate limit is how a key gets suspended."""
        client = self._client(failure(429, {"error": {"message": "quota exceeded"}}))
        with self.assertRaises(MapsApiError) as caught:
            client.search_text({"textQuery": "x"}, "places.id")
        self.assertIn("rate-limited", str(caught.exception))

    def test_a_successful_body_is_returned_unchanged(self) -> None:
        """The positive case: nothing about this translation eats a good result."""
        client = self._client(ok({"places": [{"id": "abc"}]}))
        self.assertEqual(
            client.search_text({"textQuery": "x"}, "places.id"), {"places": [{"id": "abc"}]}
        )


if __name__ == "__main__":
    unittest.main()
