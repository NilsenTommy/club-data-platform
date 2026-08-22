import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import build_fan_silver


class BuildFanSilverTests(unittest.TestCase):
	@staticmethod
	def make_customers() -> pd.DataFrame:
		return pd.DataFrame(
			[
				{"ticket_customer_id": "T-100", "email": " ADA@EXAMPLE.COM ", "name": "Ada Berg", "created_at": "2024-01-01T10:00:00Z", "marketing_consent": True, "consent_updated_at": "2024-01-10T10:00:00Z"},
				{"ticket_customer_id": "T-200", "email": "bob@example.org", "name": "Bob Dahl", "created_at": "2024-01-02T10:00:00Z", "marketing_consent": False, "consent_updated_at": "2024-01-11T10:00:00Z"},
				{"ticket_customer_id": "T-300", "email": "duplicate@example.net", "name": "Cecilie Eide", "created_at": "2024-01-03T10:00:00Z", "marketing_consent": True, "consent_updated_at": "2024-01-12T10:00:00Z"},
				{"ticket_customer_id": "T-400", "email": "duplicate@example.net", "name": "Daniel Fjell", "created_at": "2024-01-04T10:00:00Z", "marketing_consent": False, "consent_updated_at": "2024-01-13T10:00:00Z"},
				{"ticket_customer_id": "T-500", "email": None, "name": "Eva Gran", "created_at": "2024-01-05T10:00:00Z", "marketing_consent": True, "consent_updated_at": "2024-01-14T10:00:00Z"},
			]
		)

	@staticmethod
	def make_app_users() -> pd.DataFrame:
		return pd.DataFrame(
			[
				{"app_user_id": "A-100", "email": "ada@example.com", "display_name": "Ada", "registered_at": "2024-02-01T10:00:00Z"},
				{"app_user_id": "A-200", "email": "bob+app@example.org", "display_name": "Bobby", "registered_at": "2024-02-02T10:00:00Z"},
				{"app_user_id": "A-300", "email": "duplicate@example.net", "display_name": "Duplicate", "registered_at": "2024-02-03T10:00:00Z"},
				{"app_user_id": "A-400", "email": "app-only@example.com", "display_name": "App Only", "registered_at": "2024-02-04T10:00:00Z"},
				{"app_user_id": "A-500", "email": None, "display_name": "No Email", "registered_at": "2024-02-05T10:00:00Z"},
			]
		)

	@staticmethod
	def make_sales() -> pd.DataFrame:
		return pd.DataFrame(
			[
				{
					"ticket_sale_id": "S-1",
					"ticket_customer_id": "T-100",
					"match_id": "10",
					"match_type": "home",
					"purchased_at": "2025-01-01T10:00:00Z",
					"ticket_type": "adult",
					"quantity": "2",
					"unit_price_nok": "420",
					"sales_channel": "web",
					"status": "completed",
				},
				{
					"ticket_sale_id": "S-2",
					"ticket_customer_id": "T-500",
					"match_id": "11",
					"match_type": "away",
					"purchased_at": "2025-01-02T10:00:00Z",
					"ticket_type": "youth",
					"quantity": "1",
					"unit_price_nok": "200",
					"sales_channel": "app",
					"status": "refunded",
				},
			]
		)

	@staticmethod
	def make_matches() -> pd.DataFrame:
		return pd.DataFrame({"match_id": pd.Series([10, 11], dtype="Int64")})

	def build(self):
		return build_fan_silver.build_supporter_silver(
			self.make_customers(),
			self.make_app_users(),
			self.make_sales(),
			self.make_matches(),
		)

	def test_normalize_email_handles_case_spaces_and_aliases(self):
		self.assertEqual(build_fan_silver.normalize_email(" ADA+news@EXAMPLE.COM "), "ada@example.com")
		self.assertIsNone(build_fan_silver.normalize_email(None))
		with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, "Invalid supporter email"):
			build_fan_silver.normalize_email("not-an-email")

	def test_builds_canonical_fans_and_identity_bridge(self):
		fans, identities, _ = self.build()

		self.assertEqual(list(fans.columns), build_fan_silver.FAN_COLUMNS)
		self.assertEqual(list(identities.columns), build_fan_silver.FAN_IDENTITY_COLUMNS)
		self.assertEqual(len(fans), 8)
		self.assertEqual(len(identities), 10)
		self.assertEqual(fans["fan_id"].nunique(), 8)
		self.assertEqual(fans["source_count"].value_counts().to_dict(), {1: 6, 2: 2})
		self.assertFalse(identities.duplicated(["source", "source_id"]).any())
		self.assertEqual(str(fans["marketing_consent"].dtype), "boolean")
		self.assertEqual(str(fans["consent_updated_at"].dtype), "datetime64[ns, UTC]")
		self.assertEqual(str(fans["activation_eligible"].dtype), "boolean")

	def test_exact_and_alias_emails_link_across_sources(self):
		_, identities, _ = self.build()
		lookup = identities.set_index(["source", "source_id"])["fan_id"]

		self.assertEqual(lookup[("ticketing", "T-100")], lookup[("app", "A-100")])
		self.assertEqual(lookup[("ticketing", "T-200")], lookup[("app", "A-200")])
		self.assertEqual(
			identities.loc[identities["source_id"].isin(["T-100", "A-100"]), "match_method"].unique().tolist(),
			["normalized_email"],
		)

	def test_ambiguous_and_missing_emails_remain_separate(self):
		_, identities, _ = self.build()
		lookup = identities.set_index(["source", "source_id"])["fan_id"]

		duplicate_fans = {
			lookup[("ticketing", "T-300")],
			lookup[("ticketing", "T-400")],
			lookup[("app", "A-300")],
		}
		self.assertEqual(len(duplicate_fans), 3)
		self.assertNotEqual(lookup[("ticketing", "T-500")], lookup[("app", "A-500")])

	def test_activation_requires_explicit_consent_and_contactable_identity(self):
		fans, identities, _ = self.build()
		lookup = identities.set_index(["source", "source_id"])["fan_id"]
		fans_by_id = fans.set_index("fan_id")

		self.assertTrue(fans_by_id.loc[lookup[("ticketing", "T-100")], "activation_eligible"])
		self.assertFalse(fans_by_id.loc[lookup[("ticketing", "T-200")], "activation_eligible"])
		self.assertFalse(fans_by_id.loc[lookup[("ticketing", "T-500")], "activation_eligible"])
		app_only = fans_by_id.loc[lookup[("app", "A-400")]]
		self.assertTrue(pd.isna(app_only["marketing_consent"]))
		self.assertFalse(app_only["activation_eligible"])

	def test_fan_ids_are_stable_when_an_earlier_source_identity_is_added(self):
		_, before = build_fan_silver.build_fans_and_identities(
			self.make_customers(), self.make_app_users()
		)
		customers = pd.concat(
			[
				pd.DataFrame(
					[
						{
							"ticket_customer_id": "T-050",
							"email": "new@example.com",
							"name": "New Fan",
							"created_at": "2023-01-01T10:00:00Z",
							"marketing_consent": True,
							"consent_updated_at": "2023-01-02T10:00:00Z",
						}
					]
				),
				self.make_customers(),
			],
			ignore_index=True,
		)
		_, after = build_fan_silver.build_fans_and_identities(customers, self.make_app_users())
		before_lookup = before.set_index(["source", "source_id"])["fan_id"]
		after_lookup = after.set_index(["source", "source_id"])["fan_id"]

		pd.testing.assert_series_equal(
			before_lookup.sort_index(),
			after_lookup.loc[before_lookup.index].sort_index(),
		)
		self.assertTrue(before_lookup.str.fullmatch(r"FAN-[0-9A-F]{16}").all())

	def test_ticket_sales_are_typed_and_linked_to_fans(self):
		_, identities, sales = self.build()
		lookup = identities.loc[identities["source"].eq("ticketing")].set_index("source_id")["fan_id"]

		self.assertEqual(list(sales.columns), build_fan_silver.SILVER_TICKET_SALE_COLUMNS)
		self.assertEqual(sales.loc[0, "fan_id"], lookup["T-100"])
		self.assertEqual(sales.loc[1, "fan_id"], lookup["T-500"])
		self.assertEqual(str(sales["match_id"].dtype), "Int64")
		self.assertEqual(str(sales["purchased_at"].dtype), "datetime64[ns, UTC]")
		self.assertEqual(str(sales["unit_price_nok"].dtype), "Float64")

	def test_rejects_duplicate_source_ids_and_unknown_sale_references(self):
		customers = self.make_customers()
		customers.loc[1, "ticket_customer_id"] = "T-100"
		with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, "unique and non-null"):
			build_fan_silver.build_fans_and_identities(customers, self.make_app_users())

		_, identities = build_fan_silver.build_fans_and_identities(
			self.make_customers(), self.make_app_users()
		)
		unknown_customer = self.make_sales()
		unknown_customer.loc[0, "ticket_customer_id"] = "T-999"
		with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, "fan_id is null"):
			build_fan_silver.build_ticket_sales(unknown_customer, identities, self.make_matches())

		unknown_match = self.make_sales()
		unknown_match.loc[0, "match_id"] = "999"
		with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, "unknown match_id"):
			build_fan_silver.build_ticket_sales(unknown_match, identities, self.make_matches())

	def test_rejects_invalid_or_incomplete_consent(self):
		for column, value, message in (
			("marketing_consent", "maybe", "marketing_consent is invalid"),
			("consent_updated_at", None, "must occur together"),
			("consent_updated_at", "2023-01-01T10:00:00Z", "precedes customer creation"),
		):
			with self.subTest(column=column, value=value):
				customers = self.make_customers()
				customers[column] = customers[column].astype("object")
				customers.loc[0, column] = value
				with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, message):
					build_fan_silver.build_fans_and_identities(
						customers, self.make_app_users()
					)

		customers = self.make_customers()
		customers["marketing_consent"] = customers["marketing_consent"].astype("object")
		customers.loc[0, "marketing_consent"] = None
		customers.loc[0, "consent_updated_at"] = "not-a-timestamp"
		with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, "consent_updated_at is invalid"):
			build_fan_silver.build_fans_and_identities(
				customers, self.make_app_users()
			)

	def test_rejects_invalid_or_non_finite_quantities_and_prices(self):
		_, identities = build_fan_silver.build_fans_and_identities(
			self.make_customers(), self.make_app_users()
		)
		for column, value in (
			("quantity", "not-a-number"),
			("quantity", "1.5"),
			("unit_price_nok", "not-a-number"),
			("unit_price_nok", "inf"),
		):
			with self.subTest(column=column, value=value):
				sales = self.make_sales()
				sales.loc[0, column] = value
				with self.assertRaisesRegex(
					build_fan_silver.FanSilverBuildError, "valid finite numbers"
				):
					build_fan_silver.build_ticket_sales(
						sales, identities, self.make_matches()
					)

	def test_missing_input_has_clear_error(self):
		with tempfile.TemporaryDirectory() as temporary_directory:
			with self.assertRaisesRegex(build_fan_silver.FanSilverBuildError, "Missing Bronze supporter input"):
				build_fan_silver.load_supporter_data(
					Path(temporary_directory), Path(temporary_directory) / "matches.parquet"
				)

	def test_output_is_deterministic_and_readable(self):
		first = self.build()
		second = self.build()
		for first_frame, second_frame in zip(first, second):
			pd.testing.assert_frame_equal(first_frame, second_frame)

		with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
			first_paths = build_fan_silver.write_supporter_silver(*first, Path(first_directory))
			second_paths = build_fan_silver.write_supporter_silver(*second, Path(second_directory))
			for key in first_paths:
				self.assertEqual(first_paths[key].read_bytes(), second_paths[key].read_bytes())
				self.assertFalse(pd.read_parquet(first_paths[key]).empty)


if __name__ == "__main__":
	unittest.main()