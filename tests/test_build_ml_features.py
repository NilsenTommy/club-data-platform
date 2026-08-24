import tempfile
import unittest
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src import build_ml_features


AS_OF = "2026-08-22T12:00:00Z"
WINDOW_START = "2025-08-22T00:00:00Z"


class BuildMLFeaturesTests(unittest.TestCase):
	@staticmethod
	def row(fan_id="FAN-1", matches=2, engagement="2026-08-20T23:00:00Z", segment="OCCASIONAL"):
		return {
			"fan_id": fan_id,
			"as_of_at": AS_OF,
			"window_start_at": WINDOW_START,
			"last_engagement_date": engagement,
			"matches_purchased_12m": matches,
			"purchase_transactions_12m": max(matches, 2),
			"tickets_purchased_12m": max(matches, 2),
			"total_spend_12m": 400.0,
			"cancelled_transactions_12m": 0,
			"refunded_transactions_12m": 0,
			"engagement_segment": segment,
		}

	@classmethod
	def make_frame(cls):
		return pd.DataFrame(
			[
				cls.row(),
				cls.row(
					fan_id="FAN-2",
					matches=0,
					engagement=None,
					segment="INACTIVE",
				),
			]
		)

	def test_builds_exact_schema_and_nullable_dtypes(self):
		features = build_ml_features.build_ml_features(self.make_frame())

		self.assertEqual(list(features.columns), build_ml_features.ML_FEATURE_COLUMNS)
		self.assertEqual(str(features["fan_id"].dtype), "string")
		self.assertEqual(str(features["as_of_at"].dtype), "datetime64[ns, UTC]")
		self.assertEqual(str(features["window_start_at"].dtype), "datetime64[ns, UTC]")
		self.assertEqual(str(features["recency_days"].dtype), "Int64")
		for column in build_ml_features.COUNT_COLUMNS:
			self.assertEqual(str(features[column].dtype), "Int64")
		self.assertEqual(str(features["total_spend_12m"].dtype), "Float64")
		self.assertEqual(str(features["rule_segment"].dtype), "string")
		self.assertFalse(features.isna().any().any())

	def test_calculates_active_recency_in_utc_calendar_days(self):
		features = build_ml_features.build_ml_features(self.make_frame()).set_index("fan_id")

		self.assertEqual(features.loc["FAN-1", "recency_days"], 2)

	def test_calculates_no_activity_recency_as_window_length_plus_one(self):
		features = build_ml_features.build_ml_features(self.make_frame()).set_index("fan_id")

		self.assertEqual(features.loc["FAN-2", "recency_days"], 366)

	def test_does_not_require_or_export_pii_and_consent(self):
		minimal = self.make_frame()
		features = build_ml_features.build_ml_features(minimal)
		with_sensitive_fields = minimal.assign(
			primary_email=["one@example.com", "two@example.com"],
			display_name=["One", "Two"],
			marketing_consent=[True, False],
			consent_updated_at=[AS_OF, AS_OF],
			marketing_allowed=[True, False],
		)

		pd.testing.assert_frame_equal(
			features,
			build_ml_features.build_ml_features(with_sensitive_fields),
		)
		self.assertFalse(
			set(with_sensitive_fields.columns).difference(minimal.columns)
			& set(features.columns)
		)

	def test_rejects_invalid_or_duplicate_fan_ids(self):
		for fan_ids in ([None, "FAN-2"], ["", "FAN-2"], ["FAN-1", "FAN-1"]):
			with self.subTest(fan_ids=fan_ids):
				frame = self.make_frame()
				frame["fan_id"] = fan_ids
				with self.assertRaises(build_ml_features.MLFeatureBuildError):
					build_ml_features.build_ml_features(frame)

	def test_rejects_invalid_count_values(self):
		for value in (-1, 1.5, float("inf"), float("nan"), "invalid"):
			with self.subTest(value=value):
				frame = self.make_frame()
				frame["cancelled_transactions_12m"] = frame[
					"cancelled_transactions_12m"
				].astype("object")
				frame.loc[0, "cancelled_transactions_12m"] = value
				with self.assertRaisesRegex(
					build_ml_features.MLFeatureBuildError, "finite, non-negative integers"
				):
					build_ml_features.build_ml_features(frame)

	def test_rejects_invalid_spend_values(self):
		for value in (-1, float("inf"), float("nan"), "invalid"):
			with self.subTest(value=value):
				frame = self.make_frame()
				frame["total_spend_12m"] = frame["total_spend_12m"].astype("object")
				frame.loc[0, "total_spend_12m"] = value
				with self.assertRaisesRegex(
					build_ml_features.MLFeatureBuildError, "finite and non-negative"
				):
					build_ml_features.build_ml_features(frame)

	def test_rejects_impossible_purchase_counts(self):
		cases = (
			("matches_purchased_12m", 3, "cannot exceed"),
			("tickets_purchased_12m", 1, "cannot be below"),
		)
		for column, value, message in cases:
			with self.subTest(column=column):
				frame = self.make_frame()
				frame.loc[0, column] = value
				with self.assertRaisesRegex(build_ml_features.MLFeatureBuildError, message):
					build_ml_features.build_ml_features(frame)

	def test_rejects_invalid_dates_and_activity_outside_window(self):
		cases = (
			("as_of_at", "invalid"),
			("window_start_at", AS_OF),
			("last_engagement_date", "2026-08-23T00:00:00Z"),
			("last_engagement_date", "2025-08-21T23:59:59Z"),
		)
		for column, value in cases:
			with self.subTest(column=column, value=value):
				frame = self.make_frame()
				frame.loc[0, column] = value
				if column == "window_start_at":
					frame.loc[1, column] = value
				with self.assertRaises(build_ml_features.MLFeatureBuildError):
					build_ml_features.build_ml_features(frame)

	def test_rejects_mixed_snapshots(self):
		for column, value in (
			("as_of_at", "2026-08-23T12:00:00Z"),
			("window_start_at", "2025-08-23T00:00:00Z"),
		):
			with self.subTest(column=column):
				frame = self.make_frame()
				frame.loc[1, column] = value
				with self.assertRaisesRegex(
					build_ml_features.MLFeatureBuildError, "one consistent snapshot"
				):
					build_ml_features.build_ml_features(frame)

	def test_rejects_invalid_or_inconsistent_segment(self):
		for segment in ("UNKNOWN", "ENGAGED", None):
			with self.subTest(segment=segment):
				frame = self.make_frame()
				frame.loc[0, "engagement_segment"] = segment
				with self.assertRaises(build_ml_features.MLFeatureBuildError):
					build_ml_features.build_ml_features(frame)

	def test_sorts_stably_by_fan_id(self):
		frame = self.make_frame().iloc[::-1].reset_index(drop=True)

		features = build_ml_features.build_ml_features(frame)

		self.assertEqual(features["fan_id"].tolist(), ["FAN-1", "FAN-2"])

	def test_read_requires_only_feature_source_columns(self):
		with tempfile.TemporaryDirectory() as temporary_directory:
			path = Path(temporary_directory) / "activation.parquet"
			self.make_frame().to_parquet(path, index=False, engine="pyarrow")

			actual = build_ml_features.read_fan_activation(path)

		pd.testing.assert_frame_equal(actual, self.make_frame())

	def test_writes_utc_timestamps_as_parquet_microseconds(self):
		features = build_ml_features.build_ml_features(self.make_frame())
		with tempfile.TemporaryDirectory() as temporary_directory:
			path = build_ml_features.write_ml_features(
				features, Path(temporary_directory) / "fan_features.parquet"
			)
			arrow_schema = pq.read_schema(path)
			parquet_schema = pq.ParquetFile(path).schema

			for column in ("as_of_at", "window_start_at"):
				with self.subTest(column=column):
					self.assertEqual(
						arrow_schema.field(column).type,
						pa.timestamp("us", tz="UTC"),
					)
					parquet_column = parquet_schema.column(
						parquet_schema.names.index(column)
					)
					self.assertEqual(parquet_column.physical_type, "INT64")
					self.assertEqual(parquet_column.converted_type, "TIMESTAMP_MICROS")

	def test_two_writes_are_byte_identical(self):
		features = build_ml_features.build_ml_features(self.make_frame())
		with tempfile.TemporaryDirectory() as first_directory, tempfile.TemporaryDirectory() as second_directory:
			first_path = build_ml_features.write_ml_features(
				features, Path(first_directory) / "fan_features.parquet"
			)
			second_path = build_ml_features.write_ml_features(
				features, Path(second_directory) / "fan_features.parquet"
			)

			self.assertEqual(first_path.read_bytes(), second_path.read_bytes())


if __name__ == "__main__":
	unittest.main()