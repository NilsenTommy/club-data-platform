import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch

import requests

from src import fetch_weather, geocode_venues


class FetchWeatherTests(unittest.TestCase):
    USER_AGENT = "football-club-data-platform/0.1 owner@example.org"
    CLIENT_ID = "test-frost-client-id"

    @staticmethod
    def make_match(
        *,
        match_id=1,
        kickoff=datetime(2026, 8, 19, 19, tzinfo=timezone.utc),
        stadium_name="Aspmyra Stadion",
        stadium_location="Håloglandsgata 30, Bodø",
    ):
        return {
            "match_id": match_id,
            "date_unix": int(kickoff.timestamp()),
            "home_team": {"team_name": "FK Bodo - Glimt"},
            "league": {"country": "Norway"},
            "venue": {
                "stadium_name": stadium_name,
                "stadium_location": stadium_location,
            },
        }

    @staticmethod
    def make_response(payload, *, content=None, status_code=200, reason="OK"):
        response = Mock()
        response.status_code = status_code
        response.reason = reason
        response.content = (
            json.dumps(payload, separators=(",", ":")).encode()
            if content is None
            else content
        )
        response.json.return_value = payload
        return response

    @staticmethod
    def observation_data(elements=None):
        requested_elements = fetch_weather.ELEMENTS if elements is None else elements
        return [
            {
                "sourceId": "SN123:0",
                "observations": [
                    {"elementId": element, "value": 1}
                    for element in requested_elements
                ],
            }
        ]

    @staticmethod
    def write_geocode(match, geocoding_dir, payload=None):
        if payload is None:
            payload = [{"lat": "67.28274", "lon": "14.40494"}]
        output_path = geocode_venues.geocode_output_path(match, geocoding_dir)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload))
        return output_path

    def test_fetches_source_and_observations_as_raw_bytes(self):
        match = self.make_match()
        source_content = b'{ "data": [ { "id": "SN123", "distance": 12.5 } ] }\n'
        observation_payload = {"data": self.observation_data()}
        observation_content = json.dumps(observation_payload).encode()
        source_response = self.make_response(
            {"data": [{"id": "SN123", "distance": 12.5}]},
            content=source_content,
        )
        observation_response = self.make_response(
            observation_payload, content=observation_content
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            sources_dir = root / "sources"
            observations_dir = root / "observations"
            self.write_geocode(match, geocoding_dir)

            with patch.object(
                fetch_weather.requests,
                "get",
                side_effect=[source_response, observation_response],
            ) as request_get:
                summary = fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    sources_dir,
                    observations_dir,
                )

            kickoff = fetch_weather.match_kickoff(match)
            source_params = fetch_weather.source_params(
                "67.2827", "14.4049", kickoff
            )
            observation_params = fetch_weather.observation_params("SN123", kickoff)
            source_path = fetch_weather.source_output_path(
                match, kickoff, source_params, sources_dir
            )
            observation_path = fetch_weather.observation_output_path(
                "SN123", kickoff, observation_params, observations_dir
            )

            self.assertEqual(source_path.read_bytes(), source_content)
            self.assertEqual(observation_path.read_bytes(), observation_content)
            self.assertEqual(summary["source_fetched"], 1)
            self.assertEqual(summary["observations_fetched"], 1)
            self.assertEqual(summary["observations_available"], 1)
            self.assertEqual(
                request_get.call_args_list,
                [
                    call(
                        fetch_weather.FROST_SOURCES_URL,
                        params={
                            "types": "SensorSystem",
                            "geometry": "nearest(POINT(14.4049 67.2827))",
                            "nearestmaxcount": 1,
                            "validtime": "2026-08-19",
                            "elements": ",".join(fetch_weather.ELEMENTS),
                        },
                        headers={"User-Agent": self.USER_AGENT},
                        auth=(self.CLIENT_ID, ""),
                        timeout=fetch_weather.TIMEOUT_SECONDS,
                    ),
                    call(
                        fetch_weather.FROST_OBSERVATIONS_URL,
                        params={
                            "sources": "SN123",
                            "referencetime": (
                                "2026-08-19T16:00:00Z/"
                                "2026-08-19T22:00:01Z"
                            ),
                            "elements": ",".join(fetch_weather.ELEMENTS),
                            "timeoffsets": "default",
                            "levels": "default",
                            "qualities": fetch_weather.QUALITY_CODES,
                        },
                        headers={"User-Agent": self.USER_AGENT},
                        auth=(self.CLIENT_ID, ""),
                        timeout=fetch_weather.TIMEOUT_SECONDS,
                    ),
                ],
            )

    def test_existing_source_and_observation_files_are_cached(self):
        match = self.make_match()
        kickoff = fetch_weather.match_kickoff(match)

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            sources_dir = root / "sources"
            observations_dir = root / "observations"
            self.write_geocode(match, geocoding_dir)
            source_params = fetch_weather.source_params(
                "67.2827", "14.4049", kickoff
            )
            source_path = fetch_weather.source_output_path(
                match, kickoff, source_params, sources_dir
            )
            source_path.parent.mkdir(parents=True)
            source_path.write_text(
                '{"data":[{"id":"SN123","distance":12.5}]}'
            )
            observation_params = fetch_weather.observation_params("SN123", kickoff)
            observation_path = fetch_weather.observation_output_path(
                "SN123", kickoff, observation_params, observations_dir
            )
            observation_path.parent.mkdir(parents=True)
            observation_path.write_text(
                json.dumps({"data": self.observation_data()})
            )

            with patch.object(fetch_weather.requests, "get") as request_get:
                summary = fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    sources_dir,
                    observations_dir,
                )

            request_get.assert_not_called()
            self.assertEqual(summary["source_cached"], 1)
            self.assertEqual(summary["observations_cached"], 1)
            self.assertEqual(summary["observations_available"], 1)

    def test_observation_status_requires_all_nested_elements(self):
        temperature, precipitation, wind = fetch_weather.ELEMENTS
        complete_across_entries = [
            {
                "observations": [
                    {"elementId": temperature},
                    {"elementId": precipitation},
                ]
            },
            {"observations": [{"elementId": wind}]},
        ]
        partial = self.observation_data((temperature, wind))

        self.assertEqual(
            fetch_weather.observation_status(complete_across_entries),
            "available",
        )
        self.assertEqual(fetch_weather.observation_status(partial), "partial")
        self.assertEqual(
            fetch_weather.observation_status([{"observations": None}]),
            "partial",
        )
        self.assertEqual(fetch_weather.observation_status([]), "empty")

    def test_partial_observation_response_is_cached_but_not_available(self):
        match = self.make_match()
        source_response = self.make_response(
            {"data": [{"id": "SN123", "distance": 12.5}]}
        )
        partial_data = self.observation_data(
            ("air_temperature", "wind_speed")
        )
        observation_response = self.make_response({"data": partial_data})

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            observations_dir = root / "observations"
            self.write_geocode(match, geocoding_dir)

            with patch.object(
                fetch_weather.requests,
                "get",
                side_effect=[source_response, observation_response],
            ):
                summary = fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    root / "sources",
                    observations_dir,
                )

            self.assertEqual(summary["observations_partial"], 1)
            self.assertEqual(summary["observations_available"], 0)
            self.assertEqual(len(list(observations_dir.glob("*.json"))), 1)

    def test_missing_empty_and_invalid_geocodes_are_skipped(self):
        missing = self.make_match(match_id=1, stadium_name="Missing")
        empty = self.make_match(match_id=2, stadium_name="Empty")
        invalid = self.make_match(match_id=3, stadium_name="Invalid")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            self.write_geocode(empty, geocoding_dir, [])
            self.write_geocode(invalid, geocoding_dir, [{"lat": "north"}])

            with patch.object(fetch_weather.requests, "get") as request_get:
                summary = fetch_weather.fetch_weather(
                    [missing, empty, invalid],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    root / "sources",
                    root / "observations",
                )

            request_get.assert_not_called()
            self.assertEqual(summary["geocode_missing"], 1)
            self.assertEqual(summary["geocode_empty"], 1)
            self.assertEqual(summary["geocode_invalid"], 1)

    def test_source_beyond_50_km_is_saved_but_observations_are_skipped(self):
        match = self.make_match()
        source_content = b'{"data":[{"id":"SN999","distance":50.1}]}'
        response = self.make_response(
            {"data": [{"id": "SN999", "distance": 50.1}]},
            content=source_content,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            sources_dir = root / "sources"
            self.write_geocode(match, geocoding_dir)

            with patch.object(
                fetch_weather.requests, "get", return_value=response
            ) as request_get:
                summary = fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    sources_dir,
                    root / "observations",
                )

            self.assertEqual(request_get.call_count, 1)
            self.assertEqual(summary["source_too_far"], 1)
            self.assertEqual(summary["observations_available"], 0)
            self.assertEqual(len(list(sources_dir.glob("*.json"))), 1)
            self.assertEqual(next(sources_dir.glob("*.json")).read_bytes(), source_content)

    def test_no_data_statuses_continue_across_matches(self):
        no_source = self.make_match(match_id=1, stadium_name="No Source")
        no_observations = self.make_match(
            match_id=2, stadium_name="No Observations"
        )
        source_404 = self.make_response({}, status_code=404, reason="Not Found")
        source_ok = self.make_response(
            {"data": [{"id": "SN123", "distance": 8.0}]}
        )
        observations_412 = self.make_response(
            {}, status_code=412, reason="No time series"
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            self.write_geocode(no_source, geocoding_dir)
            self.write_geocode(no_observations, geocoding_dir)

            with patch.object(
                fetch_weather.requests,
                "get",
                side_effect=[source_404, source_ok, observations_412],
            ):
                summary = fetch_weather.fetch_weather(
                    [no_source, no_observations],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    root / "sources",
                    root / "observations",
                )

            self.assertEqual(summary["source_missing"], 1)
            self.assertEqual(summary["observations_missing"], 1)
            self.assertEqual(summary["observations_available"], 0)

    def test_invalid_match_fields_are_skipped(self):
        missing_venue = self.make_match(stadium_name="")
        invalid_kickoff = self.make_match()
        invalid_kickoff["date_unix"] = "yesterday"

        summary = fetch_weather.fetch_weather(
            [missing_venue, invalid_kickoff],
            self.CLIENT_ID,
            self.USER_AGENT,
        )

        self.assertEqual(summary["skipped_missing_venue"], 1)
        self.assertEqual(summary["skipped_invalid_kickoff"], 1)

    def test_configuration_placeholders_are_rejected(self):
        with self.assertRaisesRegex(
            fetch_weather.WeatherIngestionError, "FROST_CLIENT_ID"
        ):
            fetch_weather.fetch_weather(
                [], "your_client_id_here", self.USER_AGENT
            )
        with self.assertRaisesRegex(
            geocode_venues.GeocodingError, "PLATFORM_USER_AGENT"
        ):
            fetch_weather.fetch_weather(
                [], self.CLIENT_ID, "app/0.1 contact@example.com"
            )

    def test_frost_authentication_and_timeout_errors_are_clear(self):
        match = self.make_match()

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            self.write_geocode(match, geocoding_dir)

            auth_response = self.make_response({}, status_code=401)
            with (
                patch.object(
                    fetch_weather.requests, "get", return_value=auth_response
                ),
                self.assertRaisesRegex(
                    fetch_weather.WeatherIngestionError,
                    "authentication failed.*FROST_CLIENT_ID",
                ),
            ):
                fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    root / "sources",
                    root / "observations",
                )

            with (
                patch.object(
                    fetch_weather.requests,
                    "get",
                    side_effect=requests.Timeout("timed out"),
                ),
                self.assertRaisesRegex(
                    fetch_weather.WeatherIngestionError,
                    "timed out after 30 seconds",
                ),
            ):
                fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    root / "other-sources",
                    root / "observations",
                )

    def test_invalid_frost_json_is_not_saved(self):
        match = self.make_match()
        response = self.make_response({}, content=b"not json")
        response.json.side_effect = ValueError("invalid json")

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            geocoding_dir = root / "geocoding"
            sources_dir = root / "sources"
            self.write_geocode(match, geocoding_dir)

            with (
                patch.object(fetch_weather.requests, "get", return_value=response),
                self.assertRaisesRegex(
                    fetch_weather.WeatherIngestionError, "invalid JSON"
                ),
            ):
                fetch_weather.fetch_weather(
                    [match],
                    self.CLIENT_ID,
                    self.USER_AGENT,
                    geocoding_dir,
                    sources_dir,
                    root / "observations",
                )

            self.assertFalse(sources_dir.exists())


if __name__ == "__main__":
    unittest.main()