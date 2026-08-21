import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src import build_silver, fetch_weather, geocode_venues


class BuildSilverTests(unittest.TestCase):
	@staticmethod
	def make_match(
		match_id=1,
		stadium_name="Aspmyra Stadion",
		stadium_location="Håloglandsgata 30, Bodø",
		country="Norway",
	):
		return {
			"match_id": match_id,
			"date_unix": 1787166000,
			"league": {"competition_name": "UEFA Champions League", "country": country},
			"season": {"year": 20262027},
			"home_team": {"team_id": 293, "team_name": "FK Bodo - Glimt"},
			"away_team": {"team_id": 331, "team_name": "NEC"},
			"score": {"home": 3, "away": 1},
			"status": "complete",
			"venue": {
				"stadium_name": stadium_name,
				"stadium_location": stadium_location,
			},
		}

	@staticmethod
	def write_geocode(match, directory, payload):
		path = geocode_venues.geocode_output_path(match, directory)
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps(payload))
		return path

	def test_build_matches_has_typed_canonical_schema(self):
		frame = build_silver.build_matches([self.make_match()])

		self.assertEqual(list(frame.columns), build_silver.MATCH_COLUMNS)
		self.assertEqual(frame.loc[0, "match_id"], 1)
		self.assertEqual(frame.loc[0, "kickoff_at"], pd.Timestamp(1787166000, unit="s", tz="UTC"))
		self.assertEqual(frame.loc[0, "home_score"], 3)
		self.assertTrue(pd.isna(frame.loc[0, "attendance"]))
		self.assertEqual(str(frame["match_id"].dtype), "Int64")
		self.assertEqual(str(frame["kickoff_at"].dtype), "datetime64[ns, UTC]")
		self.assertEqual(frame.loc[0, "source"], "FootballData")

	def test_build_matches_rejects_duplicates_and_missing_required_fields(self):
		match = self.make_match()
		with self.assertRaisesRegex(build_silver.SilverBuildError, "duplicate match_id"):
			build_silver.build_matches([match, match])

		missing_home_team = self.make_match()
		missing_home_team["home_team"]["team_name"] = None
		with self.assertRaisesRegex(build_silver.SilverBuildError, "home_team_name is null"):
			build_silver.build_matches([missing_home_team])

	def test_build_matches_deduplicates_logical_fixture_using_richer_record(self):
		richer = self.make_match(match_id=200399)
		richer["venue"]["stadium_location"] = "Håloglandsgata 30, Bodø"
		poorer = self.make_match(match_id=197228, stadium_name="", stadium_location="")
		poorer["score"] = {"home": 3, "away": 1}

		frame = build_silver.build_matches([poorer, richer])

		self.assertEqual(len(frame), 1)
		self.assertEqual(frame.loc[0, "match_id"], 200399)
		self.assertEqual(frame.loc[0, "venue_name"], "Aspmyra Stadion")

	def test_stable_venue_id_ignores_case_and_whitespace(self):
		first = build_silver.stable_venue_id(
			"Aspmyra Stadion", "Håloglandsgata 30, Bodø"
		)
		second = build_silver.stable_venue_id(
			"  ASPMYRA   STADION ", " Håloglandsgata 30, Bodø "
		)

		self.assertEqual(first, second)
		self.assertTrue(first.startswith("VENUE-"))

	def test_build_venues_uses_first_successful_query_variant(self):
		domestic = self.make_match(country="Norway")
		european = self.make_match(match_id=2, country="Europe")

		with tempfile.TemporaryDirectory() as temporary_directory:
			geocoding_dir = Path(temporary_directory)
			self.write_geocode(domestic, geocoding_dir, [])
			self.write_geocode(
				european,
				geocoding_dir,
				[
					{
						"lat": "67.2827",
						"lon": "14.4049",
						"display_name": "Aspmyra stadion, Bodø, Norway",
						"importance": 0.8,
						"address": {"country": "Norway"},
					}
				],
			)

			frame = build_silver.build_venues(
				[domestic, european], geocoding_dir
			)

		self.assertEqual(len(frame), 1)
		self.assertEqual(frame.loc[0, "latitude"], 67.2827)
		self.assertEqual(frame.loc[0, "longitude"], 14.4049)
		self.assertEqual(frame.loc[0, "geocoding_query"], "Aspmyra Stadion, Bodø")
		self.assertTrue(pd.isna(frame.loc[0, "geocoding_confidence"]))
		self.assertEqual(frame.loc[0, "country"], "Norway")

	def test_build_venues_collapses_raw_aliases_with_same_osm_identity(self):
		full = self.make_match()
		alias = self.make_match(
			match_id=2,
			stadium_name="Aspmyra Stadion (Bodø)",
			stadium_location="",
		)
		result = {
			"lat": "67.2766478",
			"lon": "14.3844344",
			"osm_type": "way",
			"osm_id": 24292284,
			"display_name": "Aspmyra stadion, Bodø, Norway",
			"address": {"country": "Norway"},
		}

		with tempfile.TemporaryDirectory() as temporary_directory:
			geocoding_dir = Path(temporary_directory)
			self.write_geocode(full, geocoding_dir, [result])
			self.write_geocode(alias, geocoding_dir, [result])
			frame = build_silver.build_venues([alias, full], geocoding_dir)

		self.assertEqual(len(frame), 1)
		self.assertEqual(frame.loc[0, "stadium_name"], "Aspmyra Stadion")
		self.assertEqual(frame.loc[0, "stadium_location_raw"], "Håloglandsgata 30, Bodø")
		self.assertEqual(
			frame.loc[0, "venue_id"],
			build_silver.stable_venue_id("ignored", "ignored", result),
		)

	def test_build_venues_keeps_ungeocoded_venue(self):
		match = self.make_match()
		with tempfile.TemporaryDirectory() as temporary_directory:
			frame = build_silver.build_venues([match], Path(temporary_directory))

		self.assertEqual(len(frame), 1)
		self.assertTrue(pd.isna(frame.loc[0, "latitude"]))

	def test_validate_venues_rejects_invalid_coordinates(self):
		frame = pd.DataFrame(
			[
				{
					"venue_id": "VENUE-1",
					"latitude": 91.0,
					"longitude": 14.0,
				}
			]
		)
		with self.assertRaisesRegex(build_silver.SilverBuildError, "invalid latitude"):
			build_silver.validate_venues(frame)

	def write_weather_bronze(self, match, root, observation_value=8.2):
		geocoding_dir = root / "geocoding"
		sources_dir = root / "sources"
		observations_dir = root / "observations"
		self.write_geocode(
			match,
			geocoding_dir,
			[{"lat": "67.2827", "lon": "14.4049"}],
		)
		kickoff = fetch_weather.match_kickoff(match)
		source_params = fetch_weather.source_params("67.2827", "14.4049", kickoff)
		source_path = fetch_weather.source_output_path(
			match, kickoff, source_params, sources_dir
		)
		source_path.parent.mkdir(parents=True, exist_ok=True)
		source_path.write_text(
			json.dumps(
				{
					"data": [
						{
							"id": "SN82290",
							"name": "BODØ VI",
							"distance": 0.45,
							"geometry": {
								"coordinates": [14.3826, 67.2726]
							},
						}
					]
				}
			)
		)
		observation_params = fetch_weather.observation_params("SN82290", kickoff)
		observation_path = fetch_weather.observation_output_path(
			"SN82290", kickoff, observation_params, observations_dir
		)
		observation_path.parent.mkdir(parents=True, exist_ok=True)
		observation_path.write_text(
			json.dumps(
				{
					"data": [
						{
							"referenceTime": "2026-08-19T18:00:00Z",
							"observations": [
								{
									"elementId": "air_temperature",
									"value": observation_value,
									"unit": "degC",
									"timeOffset": "PT1H",
								},
								{
									"elementId": "wind_speed",
									"value": 5.1,
									"unit": "m/s",
									"timeOffset": "PT0H",
								},
							],
						}
					]
				}
			)
		)
		return geocoding_dir, sources_dir, observations_dir

	def test_build_weather_observations_is_typed_long_format(self):
		match = self.make_match()
		with tempfile.TemporaryDirectory() as temporary_directory:
			root = Path(temporary_directory)
			directories = self.write_weather_bronze(match, root)
			frame = build_silver.build_weather_observations([match], *directories)

		self.assertEqual(list(frame.columns), build_silver.WEATHER_COLUMNS)
		self.assertEqual(len(frame), 2)
		self.assertEqual(set(frame["element"]), {"air_temperature", "wind_speed"})
		temperature = frame.loc[frame["element"] == "air_temperature"].iloc[0]
		self.assertEqual(temperature["observed_at"], pd.Timestamp("2026-08-19T19:00:00Z"))
		self.assertEqual(temperature["weather_station_id"], "SN82290")
		self.assertEqual(temperature["weather_station_name"], "BODØ VI")
		self.assertEqual(temperature["source"], "Frost")
		self.assertGreater(temperature["distance_to_venue_km"], 0)
		self.assertLess(temperature["distance_to_venue_km"], 2)

	def test_build_weather_selects_one_canonical_series(self):
		match = self.make_match()
		with tempfile.TemporaryDirectory() as temporary_directory:
			root = Path(temporary_directory)
			geocoding_dir, sources_dir, observations_dir = self.write_weather_bronze(
				match, root
			)
			kickoff = fetch_weather.match_kickoff(match)
			params = fetch_weather.observation_params("SN82290", kickoff)
			path = fetch_weather.observation_output_path(
				"SN82290", kickoff, params, observations_dir
			)
			path.write_text(
				json.dumps(
					{
						"data": [
							{
								"referenceTime": "2026-08-19T18:00:00Z",
								"observations": [
									{
										"elementId": "air_temperature",
										"value": 8.2,
										"unit": "degC",
										"timeOffset": "PT0H",
										"timeResolution": "PT10M",
										"qualityCode": 2,
										"timeSeriesId": 0,
									},
									{
										"elementId": "air_temperature",
										"value": 8.5,
										"unit": "degC",
										"timeOffset": "PT0H",
										"timeResolution": "PT1H",
										"qualityCode": 0,
										"timeSeriesId": 0,
									},
								],
							}
						]
					}
				)
			)
			frame = build_silver.build_weather_observations(
				[match], geocoding_dir, sources_dir, observations_dir
			)

		self.assertEqual(len(frame), 1)
		self.assertEqual(frame.loc[0, "value"], 8.5)
		self.assertFalse(frame.duplicated(build_silver.WEATHER_SERIES_KEY).any())

	def test_main_handles_malformed_weather_geocode(self):
		with patch.object(
			build_silver.fetch_weather,
			"read_geocode",
			side_effect=fetch_weather.WeatherIngestionError("corrupt geocode"),
		), patch.object(
			build_silver.geocode_venues,
			"latest_matches_file",
			return_value=Path("matches.json"),
		), patch.object(
			build_silver.geocode_venues,
			"load_matches",
			return_value=[self.make_match()],
		), patch.object(
			build_silver,
			"build_venues",
			return_value=pd.DataFrame(),
		), patch("sys.stderr"):
			exit_code = build_silver.main()

		self.assertEqual(exit_code, 1)

	def test_build_weather_rejects_non_numeric_values(self):
		match = self.make_match()
		with tempfile.TemporaryDirectory() as temporary_directory:
			root = Path(temporary_directory)
			directories = self.write_weather_bronze(match, root, "warm")
			with self.assertRaisesRegex(build_silver.SilverBuildError, "value is not numeric"):
				build_silver.build_weather_observations([match], *directories)

	def test_write_silver_creates_readable_parquet_files(self):
		match_frame = build_silver.build_matches([self.make_match()])
		with tempfile.TemporaryDirectory() as temporary_directory:
			root = Path(temporary_directory)
			venue_frame = build_silver.build_venues([self.make_match()], root / "geocoding")
			weather_frame = pd.DataFrame(columns=build_silver.WEATHER_COLUMNS)
			weather_frame["observed_at"] = pd.to_datetime(weather_frame["observed_at"], utc=True)
			for column in ("value", "station_latitude", "station_longitude", "distance_to_venue_km"):
				weather_frame[column] = weather_frame[column].astype("Float64")
			for column in set(build_silver.WEATHER_COLUMNS) - {
				"observed_at", "value", "station_latitude", "station_longitude", "distance_to_venue_km"
			}:
				weather_frame[column] = weather_frame[column].astype("string")

			paths = build_silver.write_silver(
				match_frame, venue_frame, weather_frame, root / "silver"
			)

			self.assertEqual(len(pd.read_parquet(paths["matches"])), 1)
			self.assertEqual(len(pd.read_parquet(paths["venues"])), 1)
			self.assertEqual(len(pd.read_parquet(paths["weather"])), 0)


if __name__ == "__main__":
	unittest.main()