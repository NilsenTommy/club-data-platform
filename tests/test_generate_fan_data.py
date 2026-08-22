import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import generate_fan_data


class GenerateFanDataTests(unittest.TestCase):
	@staticmethod
	def make_matches(count=21) -> pd.DataFrame:
		rows = []
		for index in range(count):
			is_home = index % 2 == 0
			rows.append(
				{
					"match_id": 1000 + index,
					"kickoff_at": pd.Timestamp("2025-01-01T18:00:00Z") + pd.Timedelta(days=index * 9),
					"competition": "UEFA Europa League" if index % 3 == 0 else "Eliteserien",
					"home_team_id": 293 if is_home else 500 + index,
					"home_team_name": "FK Bodo - Glimt" if is_home else f"Opponent {index}",
					"away_team_id": 500 + index if is_home else 293,
					"away_team_name": f"Opponent {index}" if is_home else "FK Bodo - Glimt",
					"home_score": 2 if is_home else 1,
					"away_score": 1 if is_home else 2,
					"status": "complete",
				}
			)
		return pd.DataFrame(rows)

	@classmethod
	def setUpClass(cls):
		cls.matches = cls.make_matches()
		(
			cls.profiles,
			cls.customers,
			cls.app_users,
			cls.sales,
		) = generate_fan_data.generate_datasets(cls.matches)

	def test_schemas_and_population_sizes(self):
		self.assertEqual(list(self.customers.columns), generate_fan_data.TICKET_CUSTOMER_COLUMNS)
		self.assertEqual(list(self.app_users.columns), generate_fan_data.APP_USER_COLUMNS)
		self.assertEqual(list(self.sales.columns), generate_fan_data.TICKET_SALE_COLUMNS)
		self.assertEqual(len(self.profiles), 500)
		self.assertEqual(len(self.customers), 425)
		self.assertEqual(len(self.app_users), 375)
		self.assertGreaterEqual(len(self.sales), 3000)
		self.assertLessEqual(len(self.sales), 5000)
		for frame in (self.customers, self.app_users, self.sales):
			self.assertNotIn("canonical_supporter_id", frame.columns)

	def test_expected_source_overlap_and_source_only_supporters(self):
		in_ticket = self.profiles["in_ticket_system"]
		in_app = self.profiles["in_app"]
		self.assertEqual(int((in_ticket & in_app).sum()), 300)
		self.assertEqual(int((in_ticket & ~in_app).sum()), 125)
		self.assertEqual(int((~in_ticket & in_app).sum()), 75)
		self.assertTrue((in_ticket | in_app).all())
		self.assertEqual(self.profiles["canonical_supporter_id"].nunique(), 500)

	def test_contains_exact_and_fragmented_identities(self):
		overlap = self.profiles.iloc[:300]
		counts = overlap["fragmentation_type"].value_counts().to_dict()
		self.assertEqual(counts, generate_fan_data.FRAGMENTATION_COUNTS)

		customer_email = self.customers.iloc[:300]["email"].reset_index(drop=True)
		app_email = self.app_users.iloc[:300]["email"].reset_index(drop=True)
		kind = overlap["fragmentation_type"].reset_index(drop=True)
		exact = kind.eq("exact")
		fragmented = ~exact
		self.assertTrue((customer_email[exact] == app_email[exact]).all())
		self.assertTrue((customer_email[fragmented].fillna("") != app_email[fragmented].fillna("")).all())
		self.assertTrue(app_email[kind.eq("alias")].str.contains("+app@", regex=False).all())
		self.assertTrue(app_email[kind.eq("whitespace")].str.startswith("  ").all())
		self.assertTrue(app_email[kind.eq("case")].str.isupper().all())
		self.assertTrue(customer_email[kind.eq("missing_ticket")].isna().all())
		self.assertTrue(app_email[kind.eq("missing_app")].isna().all())

	def test_source_ids_are_unique_and_use_different_formats(self):
		self.assertFalse(self.customers["ticket_customer_id"].duplicated().any())
		self.assertFalse(self.app_users["app_user_id"].duplicated().any())
		self.assertFalse(self.sales["ticket_sale_id"].duplicated().any())
		self.assertTrue(self.customers["ticket_customer_id"].str.fullmatch(r"\d+").all())
		self.assertTrue(self.app_users["app_user_id"].str.fullmatch(r"APP-\d{5}").all())
		self.assertTrue(set(self.customers["ticket_customer_id"]).isdisjoint(self.app_users["app_user_id"]))

	def test_sales_have_valid_foreign_keys_and_cover_every_match(self):
		self.assertTrue(set(self.sales["ticket_customer_id"]).issubset(self.customers["ticket_customer_id"]))
		self.assertEqual(set(self.sales["match_id"]), set(self.matches["match_id"]))
		self.assertFalse(self.sales.duplicated(["ticket_customer_id", "match_id"]).any())

	def test_sales_classify_home_and_away_and_precede_kickoff(self):
		match_lookup = self.matches.set_index("match_id")
		expected = self.sales["match_id"].map(
			lambda match_id: "home" if match_lookup.loc[match_id, "home_team_id"] == 293 else "away"
		)
		self.assertTrue(expected.equals(self.sales["match_type"]))
		self.assertEqual(set(self.sales["match_type"]), {"home", "away"})
		purchased = pd.to_datetime(self.sales["purchased_at"], utc=True)
		kickoff = self.sales["match_id"].map(match_lookup["kickoff_at"])
		self.assertTrue((purchased < kickoff).all())

	def test_generation_accepts_only_home_or_only_away_matches(self):
		for match_type in ("home", "away"):
			with self.subTest(match_type=match_type):
				matches = self.make_matches(3)
				if match_type == "home":
					matches["home_team_id"] = 293
					matches["home_team_name"] = "FK Bodo - Glimt"
					matches["away_team_id"] = range(500, 503)
					matches["away_team_name"] = [f"Opponent {index}" for index in range(3)]
				else:
					matches["home_team_id"] = range(500, 503)
					matches["home_team_name"] = [f"Opponent {index}" for index in range(3)]
					matches["away_team_id"] = 293
					matches["away_team_name"] = "FK Bodo - Glimt"

				_, _, _, sales = generate_fan_data.generate_datasets(matches)

				self.assertEqual(set(sales["match_type"]), {match_type})

	def test_sales_values_and_customer_dates_are_valid(self):
		self.assertTrue((self.sales["quantity"] > 0).all())
		self.assertTrue((self.sales["unit_price_nok"] > 0).all())
		self.assertEqual(set(self.sales["status"]), {"completed", "cancelled", "refunded"})
		self.assertTrue(set(self.sales["ticket_type"]).issubset({"adult", "youth", "student", "child"}))
		self.assertTrue(set(self.sales["sales_channel"]).issubset({"web", "app", "box_office"}))
		first_purchase = pd.to_datetime(
			self.sales.groupby("ticket_customer_id")["purchased_at"].min(), utc=True
		)
		created = pd.to_datetime(
			self.customers.set_index("ticket_customer_id")["created_at"], utc=True
		)
		self.assertTrue((created.loc[first_purchase.index] < first_purchase).all())

	def test_consent_has_deterministic_logical_timestamps(self):
		created = pd.to_datetime(self.customers["created_at"], utc=True)
		updated = pd.to_datetime(self.customers["consent_updated_at"], utc=True)

		self.assertFalse(updated.isna().any())
		self.assertTrue((updated >= created).all())
		self.assertEqual(set(self.customers["marketing_consent"]), {True, False})

	def test_all_emails_use_reserved_domains(self):
		domains = set()
		for frame in (self.customers, self.app_users):
			for email in frame["email"].dropna():
				domains.add(email.strip().lower().rsplit("@", 1)[1])
		self.assertTrue(domains)
		self.assertTrue(domains.issubset(generate_fan_data.RESERVED_EMAIL_DOMAINS))

	def test_generation_and_csv_bytes_are_deterministic(self):
		first = generate_fan_data.generate_datasets(self.matches)
		second = generate_fan_data.generate_datasets(self.matches)
		for first_frame, second_frame in zip(first, second):
			pd.testing.assert_frame_equal(first_frame, second_frame)

		with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
			first_paths = generate_fan_data.write_datasets(*first[1:], Path(first_directory))
			second_paths = generate_fan_data.write_datasets(*second[1:], Path(second_directory))
			for first_path, second_path in zip(first_paths, second_paths):
				self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

	def test_missing_or_invalid_match_data_has_clear_errors(self):
		with tempfile.TemporaryDirectory() as temporary_directory:
			missing = Path(temporary_directory) / "matches.parquet"
			with self.assertRaisesRegex(generate_fan_data.FanDataError, "Missing Silver match input"):
				generate_fan_data.load_match_data(missing, Path(temporary_directory) / "gold.parquet")

		with self.assertRaisesRegex(generate_fan_data.FanDataError, "missing columns"):
			generate_fan_data.validate_match_data(pd.DataFrame({"match_id": [1]}))

		invalid = self.make_matches(2)
		invalid.loc[0, "kickoff_at"] = None
		with self.assertRaisesRegex(generate_fan_data.FanDataError, "kickoff_at"):
			generate_fan_data.validate_match_data(invalid)

		wrong_team = self.make_matches(2)
		wrong_team.loc[0, "home_team_id"] = 999
		with self.assertRaisesRegex(generate_fan_data.FanDataError, "team 293"):
			generate_fan_data.validate_match_data(wrong_team)

		null_team = self.make_matches(2)
		null_team[["home_team_id", "away_team_id"]] = null_team[
			["home_team_id", "away_team_id"]
		].astype("Int64")
		null_team.loc[0, "away_team_id"] = pd.NA
		with self.assertRaisesRegex(generate_fan_data.FanDataError, "missing home_team_id or away_team_id"):
			generate_fan_data.validate_match_data(null_team)


if __name__ == "__main__":
	unittest.main()
