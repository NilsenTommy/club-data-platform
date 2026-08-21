import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

from src import geocode_venues


class GeocodeVenuesTests(unittest.TestCase):
    @staticmethod
    def make_match(
        stadium_name="Aspmyra Stadion",
        stadium_location="Håloglandsgata 30, Bodø",
        home_team="FK Bodo - Glimt",
        country="Norway",
    ):
        return {
            "home_team": {"team_name": home_team},
            "league": {"country": country},
            "venue": {
                "stadium_name": stadium_name,
                "stadium_location": stadium_location,
            },
        }

    @staticmethod
    def make_response(content=b'[{"lat":"67.2827","lon":"14.4049"}]'):
        response = Mock()
        response.status_code = 200
        response.reason = "OK"
        response.content = content
        response.json.return_value = json.loads(content)
        return response

    def test_latest_matches_file_uses_latest_name(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            matches_dir = Path(temporary_directory)
            older = matches_dir / "matches_2026-08-20.json"
            latest = matches_dir / "matches_2026-08-21.json"
            older.write_text("{}")
            latest.write_text("{}")

            self.assertEqual(geocode_venues.latest_matches_file(matches_dir), latest)

    def test_load_matches_requires_data_matches_list(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "matches.json"
            input_path.write_text('{"data":{"matches":{}}}')

            with self.assertRaisesRegex(
                geocode_venues.GeocodingError, "data.matches list"
            ):
                geocode_venues.load_matches(input_path)

    def test_query_uses_context_and_excludes_europe(self):
        match = self.make_match(
            stadium_name=" Goffertstadion ",
            stadium_location=" Stadionplein 1 ",
            home_team="NEC",
            country="Europe",
        )

        self.assertEqual(
            geocode_venues.build_geocode_query(match),
            "Goffertstadion",
        )

    def test_query_allows_missing_address_and_removes_duplicates(self):
        match = self.make_match(
            stadium_name="Aspmyra Stadion",
            stadium_location="",
            home_team="Aspmyra Stadion",
            country="Norway",
        )

        self.assertEqual(
            geocode_venues.build_geocode_query(match),
            "Aspmyra Stadion, Norway",
        )

    def test_query_uses_city_hint_instead_of_full_street_address(self):
        match = self.make_match(
            stadium_location="Håloglandsgata 30, Bodø",
            country="Europe",
        )

        self.assertEqual(
            geocode_venues.build_geocode_query(match),
            "Aspmyra Stadion, Bodø",
        )

    def test_cache_key_changes_with_effective_query(self):
        norway = self.make_match(country="Norway")
        sweden = self.make_match(country="Sweden")

        self.assertNotEqual(
            geocode_venues.venue_key(norway),
            geocode_venues.venue_key(sweden),
        )

    def test_deduplication_uses_country_dependent_cache_identity(self):
        domestic = self.make_match(country="Norway")
        european = self.make_match(country="Europe")

        unique_matches, skipped = geocode_venues.unique_venue_matches(
            [domestic, european]
        )

        self.assertEqual(len(unique_matches), 2)
        self.assertEqual(skipped, 0)
        self.assertEqual(
            len(
                {
                    geocode_venues.geocode_output_path(match)
                    for match in unique_matches
                }
            ),
            2,
        )

    def test_geocodes_unique_venues_with_spacing_and_raw_bytes(self):
        first = self.make_match()
        duplicate = self.make_match(stadium_name=" Aspmyra   Stadion ")
        second = self.make_match(
            stadium_name="Goffertstadion",
            stadium_location="Stadionplein 1",
            home_team="NEC",
            country="Europe",
        )
        missing_name = self.make_match(stadium_name=" ")
        first_content = b'[ { "lat": "67.2827", "lon": "14.4049" } ]\n'
        second_content = b'[{"lat":"51.821","lon":"5.837"}]'
        responses = [
            self.make_response(first_content),
            self.make_response(second_content),
        ]

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "geocoding"
            with (
                patch.object(
                    geocode_venues.requests, "get", side_effect=responses
                ) as request_get,
                patch.object(geocode_venues.time, "sleep") as sleep,
            ):
                summary = geocode_venues.geocode_venues(
                    [first, duplicate, second, missing_name],
                    "football-club-data-platform/0.1 owner@example.org",
                    output_dir,
                )

            self.assertEqual(
                summary,
                {
                    "matches": 4,
                    "unique_venues": 2,
                    "skipped_missing_name": 1,
                    "fetched": 2,
                    "cached": 0,
                    "empty": 0,
                },
            )
            self.assertEqual(
                geocode_venues.geocode_output_path(first, output_dir).read_bytes(),
                first_content,
            )
            self.assertEqual(
                geocode_venues.geocode_output_path(second, output_dir).read_bytes(),
                second_content,
            )
            sleep.assert_called_once_with(geocode_venues.REQUEST_INTERVAL_SECONDS)
            self.assertEqual(request_get.call_count, 2)
            self.assertEqual(
                request_get.call_args_list[0],
                call(
                    geocode_venues.NOMINATIM_URL,
                    params={
                        "q": "Aspmyra Stadion, Bodø, Norway",
                        "format": "jsonv2",
                        "limit": 5,
                        "addressdetails": 1,
                        "accept-language": "en",
                    },
                    headers={
                        "User-Agent": "football-club-data-platform/0.1 owner@example.org"
                    },
                    timeout=geocode_venues.TIMEOUT_SECONDS,
                ),
            )

    def test_existing_file_is_used_as_cache(self):
        match = self.make_match()

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            output_path = geocode_venues.geocode_output_path(match, output_dir)
            output_path.write_bytes(b"[]")

            with patch.object(geocode_venues.requests, "get") as request_get:
                summary = geocode_venues.geocode_venues(
                    [match],
                    "football-club-data-platform/0.1 owner@example.org",
                    output_dir,
                )

            request_get.assert_not_called()
            self.assertEqual(summary["cached"], 1)
            self.assertEqual(summary["empty"], 1)

    def test_empty_response_is_saved(self):
        match = self.make_match()
        response = self.make_response(b"[]")

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            with patch.object(
                geocode_venues.requests, "get", return_value=response
            ):
                summary = geocode_venues.geocode_venues(
                    [match],
                    "football-club-data-platform/0.1 owner@example.org",
                    output_dir,
                )

            self.assertEqual(summary["empty"], 1)
            self.assertEqual(
                geocode_venues.geocode_output_path(match, output_dir).read_bytes(),
                b"[]",
            )

    def test_placeholder_user_agent_is_rejected(self):
        with self.assertRaisesRegex(
            geocode_venues.GeocodingError, "PLATFORM_USER_AGENT"
        ):
            geocode_venues.geocode_venues(
                [self.make_match()],
                "football-club-data-platform/0.1 contact@example.com",
            )

    def test_http_policy_errors_are_clear(self):
        for status_code, expected_message in (
            (403, "Check PLATFORM_USER_AGENT"),
            (429, "retry the batch later"),
            (503, "HTTP 503"),
        ):
            with self.subTest(status_code=status_code):
                response = self.make_response()
                response.status_code = status_code
                response.reason = "Service Unavailable"

                with (
                    tempfile.TemporaryDirectory() as temporary_directory,
                    patch.object(
                        geocode_venues.requests, "get", return_value=response
                    ),
                    self.assertRaisesRegex(
                        geocode_venues.GeocodingError, expected_message
                    ),
                ):
                    geocode_venues.geocode_venues(
                        [self.make_match()],
                        "football-club-data-platform/0.1 owner@example.org",
                        Path(temporary_directory),
                    )

    def test_timeout_is_clear(self):
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(
                geocode_venues.requests,
                "get",
                side_effect=requests.Timeout("timed out"),
            ),
            self.assertRaisesRegex(
                geocode_venues.GeocodingError, "timed out after 30 seconds"
            ),
        ):
            geocode_venues.geocode_venues(
                [self.make_match()],
                "football-club-data-platform/0.1 owner@example.org",
                Path(temporary_directory),
            )


if __name__ == "__main__":
    unittest.main()