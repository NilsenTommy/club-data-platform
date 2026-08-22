import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import build_fan_gold


AS_OF = pd.Timestamp("2026-08-22T00:00:00Z")
WINDOW_START = pd.Timestamp("2025-08-22T00:00:00Z")


class BuildFanGoldTests(unittest.TestCase):
	@staticmethod
	def make_fans() -> pd.DataFrame:
		return pd.DataFrame(
			[
				{
					"fan_id": "FAN-1",
					"primary_email": "one@example.com",
					"display_name": "One",
					"marketing_consent": True,
					"consent_updated_at": "2025-01-01T00:00:00Z",
					"activation_eligible": True,
				},
				{
					"fan_id": "FAN-2",
					"primary_email": "two@example.com",
					"display_name": "Two",
					"marketing_consent": False,
					"consent_updated_at": "2025-01-02T00:00:00Z",
					"activation_eligible": False,
				},
				{
					"fan_id": "FAN-3",
					"primary_email": "three@example.com",
					"display_name": "Three",
					"marketing_consent": None,
					"consent_updated_at": None,
					"activation_eligible": False,
				},
				{
					"fan_id": "FAN-4",
					"primary_email": None,
					"display_name": "Four",
					"marketing_consent": True,
					"consent_updated_at": "2025-01-04T00:00:00Z",
					"activation_eligible": False,
				},
			]
		)

	@staticmethod
	def sale(
		sale_id,
		fan_id="FAN-1",
		match_id=1,
		purchased_at="2026-01-01T00:00:00Z",
		quantity=1,
		unit_price=100,
		status="completed",
	):
		return {
			"ticket_sale_id": sale_id,
			"fan_id": fan_id,
			"match_id": match_id,
			"purchased_at": purchased_at,
			"quantity": quantity,
			"unit_price_nok": unit_price,
			"status": status,
		}

	@classmethod
	def make_sales(cls) -> pd.DataFrame:
		return pd.DataFrame(
			[
				cls.sale("S-1", match_id=10, purchased_at=WINDOW_START, quantity=2),
				cls.sale("S-2", match_id=11, purchased_at="2026-02-01T00:00:00Z", unit_price=200),
				cls.sale("S-3", match_id=12, purchased_at="2026-03-01T00:00:00Z", status="cancelled"),
				cls.sale("S-4", match_id=13, purchased_at="2026-04-01T00:00:00Z", status="refunded"),
				cls.sale("S-5", match_id=14, purchased_at=AS_OF, quantity=4),
				cls.sale("S-6", fan_id="FAN-3", match_id=15, purchased_at="2025-01-01T00:00:00Z"),
			]
		)

	def build(self, fans=None, sales=None):
		return build_fan_gold.build_fan_activation(
			self.make_fans() if fans is None else fans,
			self.make_sales() if sales is None else sales,
			AS_OF,
		)

	def test_builds_canonical_schema_and_preserves_all_fans(self):
		frame = self.build()

		self.assertEqual(list(frame.columns), build_fan_gold.FAN_ACTIVATION_COLUMNS)
		self.assertEqual(len(frame), 4)
		self.assertEqual(set(frame["fan_id"]), set(self.make_fans()["fan_id"]))
		self.assertFalse(frame["fan_id"].duplicated().any())
		self.assertEqual(str(frame["matches_purchased_12m"].dtype), "Int64")
		self.assertEqual(str(frame["total_spend_12m"].dtype), "Float64")
		self.assertEqual(str(frame["marketing_consent"].dtype), "boolean")
		self.assertEqual(str(frame["marketing_allowed"].dtype), "boolean")

	def test_window_is_start_inclusive_and_as_of_exclusive(self):
		frame = self.build().set_index("fan_id")
		fan = frame.loc["FAN-1"]

		self.assertEqual(fan["matches_purchased_12m"], 2)
		self.assertEqual(fan["purchase_transactions_12m"], 2)
		self.assertEqual(fan["tickets_purchased_12m"], 3)
		self.assertEqual(fan["total_spend_12m"], 400.0)
		self.assertEqual(fan["window_start_at"], WINDOW_START)
		self.assertEqual(fan["as_of_at"], AS_OF)

	def test_statuses_are_counted_without_affecting_engagement_or_spend(self):
		fan = self.build().set_index("fan_id").loc["FAN-1"]

		self.assertEqual(fan["cancelled_transactions_12m"], 1)
		self.assertEqual(fan["refunded_transactions_12m"], 1)
		self.assertEqual(fan["matches_purchased_12m"], 2)
		self.assertEqual(fan["total_spend_12m"], 400.0)

	def test_last_engagement_is_all_time_completed_purchase_before_as_of(self):
		frame = self.build().set_index("fan_id")

		self.assertEqual(
			frame.loc["FAN-1", "last_engagement_date"],
			pd.Timestamp("2026-02-01T00:00:00Z"),
		)
		self.assertEqual(
			frame.loc["FAN-3", "last_engagement_date"],
			pd.Timestamp("2025-01-01T00:00:00Z"),
		)
		self.assertTrue(pd.isna(frame.loc["FAN-2", "last_engagement_date"]))

	def test_segment_boundaries(self):
		expected = {
			0: "INACTIVE",
			1: "OCCASIONAL",
			2: "OCCASIONAL",
			3: "ENGAGED",
			5: "ENGAGED",
			6: "HIGHLY_ENGAGED",
		}
		for match_count, segment in expected.items():
			with self.subTest(match_count=match_count):
				self.assertEqual(build_fan_gold.engagement_segment(match_count), segment)

	def test_no_purchase_fans_have_zero_metrics_and_inactive_segment(self):
		fan = self.build().set_index("fan_id").loc["FAN-2"]

		for column in build_fan_gold.ADDITIVE_COLUMNS:
			self.assertEqual(fan[column], 0)
		self.assertEqual(fan["engagement_segment"], "INACTIVE")
		self.assertTrue(pd.isna(fan["last_engagement_date"]))

	def test_marketing_allowed_requires_consent_and_email(self):
		frame = self.build().set_index("fan_id")

		self.assertTrue(frame.loc["FAN-1", "marketing_allowed"])
		self.assertFalse(frame.loc["FAN-2", "marketing_allowed"])
		self.assertFalse(frame.loc["FAN-3", "marketing_allowed"])
		self.assertFalse(frame.loc["FAN-4", "marketing_allowed"])

	def test_rejects_consent_after_as_of(self):
		fans = self.make_fans()
		fans.loc[0, "consent_updated_at"] = "2026-08-23T00:00:00Z"

		with self.assertRaisesRegex(build_fan_gold.FanGoldBuildError, "after as_of"):
			self.build(fans=fans)

	def test_rejects_invalid_inputs(self):
		cases = []
		unknown_fan = self.make_sales()
		unknown_fan.loc[0, "fan_id"] = "FAN-UNKNOWN"
		cases.append((self.make_fans(), unknown_fan, "unknown fan_id"))
		invalid_time = self.make_sales()
		invalid_time.loc[0, "purchased_at"] = "not-a-timestamp"
		cases.append((self.make_fans(), invalid_time, "purchased_at is null or invalid"))
		invalid_price = self.make_sales()
		invalid_price["unit_price_nok"] = invalid_price["unit_price_nok"].astype("object")
		invalid_price.loc[0, "unit_price_nok"] = float("inf")
		cases.append((self.make_fans(), invalid_price, "numeric value is invalid"))
		invalid_quantity = self.make_sales()
		invalid_quantity["quantity"] = invalid_quantity["quantity"].astype("object")
		invalid_quantity.loc[0, "quantity"] = 1.5
		cases.append((self.make_fans(), invalid_quantity, "numeric value is invalid"))
		invalid_consent = self.make_fans()
		invalid_consent.loc[0, "consent_updated_at"] = "not-a-timestamp"
		cases.append((invalid_consent, self.make_sales(), "consent_updated_at is invalid"))

		for fans, sales, message in cases:
			with self.subTest(message=message):
				with self.assertRaisesRegex(build_fan_gold.FanGoldBuildError, message):
					self.build(fans=fans, sales=sales)

	def test_parse_as_of_normalizes_utc_and_rejects_invalid_values(self):
		self.assertEqual(
			build_fan_gold.parse_as_of("2026-08-22"),
			pd.Timestamp("2026-08-22T00:00:00Z"),
		)
		self.assertEqual(
			build_fan_gold.parse_as_of("2026-08-22T02:00:00+02:00"),
			pd.Timestamp("2026-08-22T00:00:00Z"),
		)
		with self.assertRaisesRegex(build_fan_gold.FanGoldBuildError, "Invalid --as-of"):
			build_fan_gold.parse_as_of("not-a-date")

	def test_load_reports_missing_or_incomplete_input(self):
		with tempfile.TemporaryDirectory() as temporary_directory:
			directory = Path(temporary_directory)
			with self.assertRaisesRegex(build_fan_gold.FanGoldBuildError, "Missing Silver input"):
				build_fan_gold.load_fan_silver(directory)

			pd.DataFrame({"fan_id": ["FAN-1"]}).to_parquet(
				directory / "silver_fans.parquet", index=False
			)
			with self.assertRaisesRegex(build_fan_gold.FanGoldBuildError, "missing columns"):
				build_fan_gold.load_fan_silver(directory)

	def test_output_is_deterministic_and_byte_identical(self):
		first = self.build()
		second = self.build()
		pd.testing.assert_frame_equal(first, second)

		with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
			first_path = build_fan_gold.write_fan_activation(first, Path(first_directory))
			second_path = build_fan_gold.write_fan_activation(second, Path(second_directory))
			self.assertEqual(first_path.read_bytes(), second_path.read_bytes())


if __name__ == "__main__":
	unittest.main()