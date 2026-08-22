import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import build_gold


KICKOFF = pd.Timestamp("2026-03-11T20:00:00Z")
VENUE_ID = "VENUE-TEST0000000001"


class BuildGoldTests(unittest.TestCase):
	@staticmethod
	def make_matches(records) -> pd.DataFrame:
		defaults = {
			"match_id": 1,
			"kickoff_at": KICKOFF,
			"competition": "Eliteserien",
			"season": 20262027,
			"home_team_id": 293,
			"home_team_name": "FK Bodo - Glimt",
			"away_team_id": 331,
			"away_team_name": "NEC",
			"home_score": 3,
			"away_score": 1,
			"status": "complete",
			"venue_id": VENUE_ID,
		}
		rows = [{**defaults, **record} for record in records]
		frame = pd.DataFrame(rows, columns=list(defaults))
		frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True)
		for column in (
			"match_id",
			"season",
			"home_team_id",
			"away_team_id",
			"home_score",
			"away_score",
		):
			frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
		for column in (
			"competition",
			"home_team_name",
			"away_team_name",
			"status",
			"venue_id",
		):
			frame[column] = frame[column].astype("string")
		return frame

	@staticmethod
	def make_venues(records=None) -> pd.DataFrame:
		defaults = {
			"venue_id": VENUE_ID,
			"stadium_name": "Aspmyra Stadion",
			"country": "Norway",
			"latitude": 67.2766,
			"longitude": 14.3844,
		}
		records = [{}] if records is None else records
		frame = pd.DataFrame(
			[{**defaults, **record} for record in records], columns=list(defaults)
		)
		for column in ("latitude", "longitude"):
			frame[column] = frame[column].astype("Float64")
		for column in ("venue_id", "stadium_name", "country"):
			frame[column] = frame[column].astype("string")
		return frame

	@staticmethod
	def make_weather(records) -> pd.DataFrame:
		defaults = {
			"venue_id": VENUE_ID,
			"weather_station_id": "SN82290",
			"observed_at": KICKOFF,
			"element": "air_temperature",
			"value": 7.8,
			"distance_to_venue_km": 0.45,
		}
		frame = pd.DataFrame(
			[{**defaults, **record} for record in records], columns=list(defaults)
		)
		frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
		for column in ("value", "distance_to_venue_km"):
			frame[column] = frame[column].astype("Float64")
		for column in ("venue_id", "weather_station_id", "element"):
			frame[column] = frame[column].astype("string")
		return frame

	@classmethod
	def full_snapshot(cls, observed_at=KICKOFF, station="SN82290", distance=0.45):
		return cls.make_weather(
			[
				{"observed_at": observed_at, "weather_station_id": station,
					"distance_to_venue_km": distance,
					"element": "air_temperature", "value": 7.8},
				{"observed_at": observed_at, "weather_station_id": station,
					"distance_to_venue_km": distance,
					"element": "sum(precipitation_amount PT1H)", "value": 0.4},
				{"observed_at": observed_at, "weather_station_id": station,
					"distance_to_venue_km": distance,
					"element": "wind_speed", "value": 4.1},
			]
		)

	def build(self, match_frame, venue_frame=None, weather_frame=None) -> pd.DataFrame:
		return build_gold.build_match_insights(
			match_frame,
			self.make_venues() if venue_frame is None else venue_frame,
			self.make_weather([]) if weather_frame is None else weather_frame,
		)

	def test_result_uses_focus_team_perspective(self):
		matches = self.make_matches(
			[
				{"match_id": 1, "home_team_id": 293, "away_team_id": 331,
					"home_score": 3, "away_score": 1},
				{"match_id": 2, "home_team_id": 331, "away_team_id": 293,
					"home_score": 1, "away_score": 3,
					"kickoff_at": KICKOFF + pd.Timedelta(days=1)},
				{"match_id": 3, "home_team_id": 293, "away_team_id": 331,
					"home_score": 2, "away_score": 2,
					"kickoff_at": KICKOFF + pd.Timedelta(days=2)},
				{"match_id": 4, "home_team_id": 331, "away_team_id": 293,
					"home_score": 2, "away_score": 0,
					"kickoff_at": KICKOFF + pd.Timedelta(days=3)},
			]
		)

		frame = self.build(matches).set_index("match_id")

		self.assertEqual(frame.loc[1, "result"], "win")
		self.assertEqual(frame.loc[2, "result"], "win")
		self.assertEqual(frame.loc[3, "result"], "draw")
		self.assertEqual(frame.loc[4, "result"], "loss")

	def test_result_is_null_without_finished_result(self):
		matches = self.make_matches(
			[
				{"match_id": 1, "status": "incomplete", "home_score": None,
					"away_score": None},
				{"match_id": 2, "status": "complete", "home_score": 1,
					"away_score": None, "kickoff_at": KICKOFF + pd.Timedelta(days=1)},
			]
		)

		frame = self.build(matches)

		self.assertTrue(frame["result"].isna().all())
		self.assertEqual(len(frame), 2)

	def test_result_is_null_for_missing_status_or_team_ids(self):
		matches = self.make_matches(
			[
				{"match_id": 1, "status": None},
				{"match_id": 2, "home_team_id": None, "away_team_id": None,
					"kickoff_at": KICKOFF + pd.Timedelta(days=1)},
				{"match_id": 3, "home_team_id": 1068, "away_team_id": 331,
					"kickoff_at": KICKOFF + pd.Timedelta(days=2)},
			]
		)

		frame = self.build(matches)

		self.assertTrue(frame["result"].isna().all())
		self.assertEqual(len(frame), 3)

	def test_selects_weather_closest_to_kickoff(self):
		matches = self.make_matches([{"match_id": 1}])
		weather = pd.concat(
			[
				self.full_snapshot(observed_at=KICKOFF - pd.Timedelta(hours=2)),
				self.full_snapshot(observed_at=KICKOFF + pd.Timedelta(minutes=20)),
			],
			ignore_index=True,
		)

		frame = self.build(matches, weather_frame=weather)

		self.assertEqual(
			frame.loc[0, "weather_observed_at"], KICKOFF + pd.Timedelta(minutes=20)
		)
		self.assertEqual(frame.loc[0, "temperature_c"], 7.8)
		self.assertEqual(frame.loc[0, "precipitation_mm"], 0.4)
		self.assertEqual(frame.loc[0, "wind_speed_ms"], 4.1)

	def test_prefers_observation_before_kickoff_on_equal_distance(self):
		matches = self.make_matches([{"match_id": 1}])
		before = self.full_snapshot(observed_at=KICKOFF - pd.Timedelta(hours=1))
		after = self.full_snapshot(observed_at=KICKOFF + pd.Timedelta(hours=1))
		after["value"] = after["value"] + 10

		frame = self.build(
			matches, weather_frame=pd.concat([after, before], ignore_index=True)
		)

		self.assertEqual(
			frame.loc[0, "weather_observed_at"], KICKOFF - pd.Timedelta(hours=1)
		)
		self.assertEqual(frame.loc[0, "temperature_c"], 7.8)

	def test_accepts_three_hour_boundary_and_rejects_beyond(self):
		matches = self.make_matches([{"match_id": 1}])

		inside = self.build(
			matches,
			weather_frame=self.full_snapshot(observed_at=KICKOFF - pd.Timedelta(hours=3)),
		)
		outside = self.build(
			matches,
			weather_frame=self.full_snapshot(
				observed_at=KICKOFF + pd.Timedelta(hours=3, seconds=1)
			),
		)

		self.assertEqual(
			inside.loc[0, "weather_observed_at"], KICKOFF - pd.Timedelta(hours=3)
		)
		self.assertTrue(pd.isna(outside.loc[0, "weather_observed_at"]))
		self.assertTrue(pd.isna(outside.loc[0, "temperature_c"]))

	def test_keeps_match_without_weather(self):
		matches = self.make_matches([{"match_id": 1}])

		frame = self.build(matches, weather_frame=self.full_snapshot(
			observed_at=KICKOFF - pd.Timedelta(days=4)
		))

		self.assertEqual(len(frame), 1)
		self.assertEqual(frame.loc[0, "stadium_name"], "Aspmyra Stadion")
		for column in ("weather_observed_at", "temperature_c", "precipitation_mm", "wind_speed_ms"):
			self.assertTrue(pd.isna(frame.loc[0, column]))

	def test_keeps_match_without_venue(self):
		matches = self.make_matches([{"match_id": 1, "venue_id": None}])

		frame = self.build(matches, weather_frame=self.full_snapshot())

		self.assertEqual(len(frame), 1)
		self.assertEqual(frame.loc[0, "result"], "win")
		for column in ("venue_id", "stadium_name", "country", "latitude", "longitude"):
			self.assertTrue(pd.isna(frame.loc[0, column]))
		self.assertTrue(pd.isna(frame.loc[0, "weather_observed_at"]))

	def test_joins_do_not_duplicate_matches(self):
		matches = self.make_matches([{"match_id": 1}, {"match_id": 2}])
		weather = pd.concat(
			[
				self.full_snapshot(observed_at=KICKOFF - pd.Timedelta(hours=1)),
				self.full_snapshot(observed_at=KICKOFF),
				self.full_snapshot(observed_at=KICKOFF + pd.Timedelta(hours=1)),
			],
			ignore_index=True,
		)

		frame = self.build(matches, weather_frame=weather)

		self.assertEqual(len(frame), 2)
		self.assertEqual(list(frame.columns), build_gold.MATCH_INSIGHT_COLUMNS)
		self.assertFalse(frame["match_id"].duplicated().any())

	def test_duplicate_venue_rows_are_rejected(self):
		matches = self.make_matches([{"match_id": 1}])
		venues = self.make_venues([{}, {"stadium_name": "Aspmyra Stadion (Bodø)"}])

		with self.assertRaisesRegex(build_gold.GoldBuildError, "duplicate matches"):
			self.build(matches, venue_frame=venues)

	def test_validation_rejects_invalid_gold_rows(self):
		frame = self.build(self.make_matches([{"match_id": 1}]))

		duplicated = pd.concat([frame, frame], ignore_index=True)
		with self.assertRaisesRegex(build_gold.GoldBuildError, "duplicate match_id"):
			build_gold.validate_match_insights(duplicated)

		missing_kickoff = frame.copy()
		missing_kickoff.loc[0, "kickoff_at"] = pd.NaT
		with self.assertRaisesRegex(build_gold.GoldBuildError, "kickoff_at is null"):
			build_gold.validate_match_insights(missing_kickoff)

		invalid_result = frame.copy()
		invalid_result.loc[0, "result"] = "victory"
		with self.assertRaisesRegex(build_gold.GoldBuildError, "unsupported result"):
			build_gold.validate_match_insights(invalid_result)

		stale_weather = frame.copy()
		stale_weather.loc[0, "weather_observed_at"] = KICKOFF + pd.Timedelta(hours=4)
		with self.assertRaisesRegex(build_gold.GoldBuildError, "three"):
			build_gold.validate_match_insights(stale_weather)

		with self.assertRaisesRegex(build_gold.GoldBuildError, "Silver matches"):
			build_gold.validate_match_insights(
				frame, self.make_matches([{"match_id": 1}, {"match_id": 2}])
			)

	def test_load_silver_data_reports_missing_input(self):
		with tempfile.TemporaryDirectory() as temporary_directory:
			with self.assertRaisesRegex(build_gold.GoldBuildError, "Missing Silver input"):
				build_gold.load_silver_data(Path(temporary_directory))

	def test_output_is_deterministic(self):
		matches = self.make_matches(
			[
				{"match_id": 2, "kickoff_at": KICKOFF + pd.Timedelta(days=1)},
				{"match_id": 1},
			]
		)
		weather = self.full_snapshot()

		first = self.build(matches, weather_frame=weather)
		second = self.build(matches.iloc[::-1].reset_index(drop=True), weather_frame=weather)

		self.assertEqual(list(first["match_id"]), [1, 2])
		pd.testing.assert_frame_equal(first, second)

		with tempfile.TemporaryDirectory() as temporary_directory:
			root = Path(temporary_directory)
			first_path = build_gold.write_gold(first, root / "first")
			second_path = build_gold.write_gold(second, root / "second")

			self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
			self.assertEqual(len(pd.read_parquet(first_path)), 2)


if __name__ == "__main__":
	unittest.main()
