import argparse
import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAN_ACTIVATION_INPUT = PROJECT_ROOT / "data" / "gold" / "fan_activation.parquet"
ML_FEATURES_OUTPUT = PROJECT_ROOT / "data" / "ml" / "fan_features.parquet"

SEGMENT_VALUES = ("INACTIVE", "OCCASIONAL", "ENGAGED", "HIGHLY_ENGAGED")
COUNT_COLUMNS = (
	"matches_purchased_12m",
	"purchase_transactions_12m",
	"tickets_purchased_12m",
	"cancelled_transactions_12m",
	"refunded_transactions_12m",
)
REQUIRED_INPUT_COLUMNS = (
	"fan_id",
	"as_of_at",
	"window_start_at",
	"last_engagement_date",
	*COUNT_COLUMNS,
	"total_spend_12m",
	"engagement_segment",
)
ML_FEATURE_COLUMNS = [
	"fan_id",
	"as_of_at",
	"window_start_at",
	"recency_days",
	"matches_purchased_12m",
	"purchase_transactions_12m",
	"tickets_purchased_12m",
	"total_spend_12m",
	"cancelled_transactions_12m",
	"refunded_transactions_12m",
	"rule_segment",
]


class MLFeatureBuildError(RuntimeError):
	pass


def _rule_segment(matches_purchased: int) -> str:
	if matches_purchased == 0:
		return "INACTIVE"
	if matches_purchased <= 2:
		return "OCCASIONAL"
	if matches_purchased <= 5:
		return "ENGAGED"
	return "HIGHLY_ENGAGED"


def _require_columns(frame: pd.DataFrame) -> None:
	missing = [column for column in REQUIRED_INPUT_COLUMNS if column not in frame.columns]
	if missing:
		raise MLFeatureBuildError(f"Fan activation input is missing columns {missing}.")


def _utc_timestamps(frame: pd.DataFrame, column: str, nullable: bool) -> pd.Series:
	raw_values = frame[column]
	timestamps = pd.to_datetime(raw_values, utc=True, errors="coerce", format="mixed")
	invalid = raw_values.notna() & timestamps.isna()
	if invalid.any() or (not nullable and timestamps.isna().any()):
		raise MLFeatureBuildError(f"{column} must contain valid UTC timestamps.")
	return timestamps.astype("datetime64[ns, UTC]")


def _integer_values(frame: pd.DataFrame, column: str) -> pd.Series:
	values = pd.to_numeric(frame[column], errors="coerce")
	finite = values.notna() & values.map(lambda value: math.isfinite(float(value)))
	if not finite.all() or (values % 1 != 0).any() or (values < 0).any():
		raise MLFeatureBuildError(
			f"{column} must contain finite, non-negative integers."
		)
	return values.astype("Int64")


def build_ml_features(frame: pd.DataFrame) -> pd.DataFrame:
	_require_columns(frame)
	features = frame[list(REQUIRED_INPUT_COLUMNS)].copy()

	if features["fan_id"].isna().any() or features["fan_id"].duplicated().any():
		raise MLFeatureBuildError("fan_id must be unique and non-null.")
	features["fan_id"] = features["fan_id"].astype("string")
	if features["fan_id"].str.strip().eq("").any():
		raise MLFeatureBuildError("fan_id must not be empty.")

	features["as_of_at"] = _utc_timestamps(features, "as_of_at", nullable=False)
	features["window_start_at"] = _utc_timestamps(
		features, "window_start_at", nullable=False
	)
	features["last_engagement_date"] = _utc_timestamps(
		features, "last_engagement_date", nullable=True
	)
	if features["as_of_at"].nunique() != 1 or features["window_start_at"].nunique() != 1:
		raise MLFeatureBuildError(
			"Fan activation input must represent one consistent snapshot."
		)
	as_of = features["as_of_at"].iloc[0]
	window_start = features["window_start_at"].iloc[0]
	if window_start >= as_of:
		raise MLFeatureBuildError("window_start_at must be before as_of_at.")
	present_engagement = features["last_engagement_date"].notna()
	outside_window = present_engagement & (
		features["last_engagement_date"].lt(window_start)
		| features["last_engagement_date"].gt(as_of)
	)
	if outside_window.any():
		raise MLFeatureBuildError(
			"last_engagement_date must be between window_start_at and as_of_at."
		)

	for column in COUNT_COLUMNS:
		features[column] = _integer_values(features, column)
	spend = pd.to_numeric(features["total_spend_12m"], errors="coerce")
	finite_spend = spend.notna() & spend.map(lambda value: math.isfinite(float(value)))
	if not finite_spend.all() or (spend < 0).any():
		raise MLFeatureBuildError("total_spend_12m must be finite and non-negative.")
	features["total_spend_12m"] = spend.astype("Float64")

	if (
		features["matches_purchased_12m"]
		> features["purchase_transactions_12m"]
	).any():
		raise MLFeatureBuildError(
			"matches_purchased_12m cannot exceed purchase_transactions_12m."
		)
	if (
		features["tickets_purchased_12m"]
		< features["purchase_transactions_12m"]
	).any():
		raise MLFeatureBuildError(
			"tickets_purchased_12m cannot be below purchase_transactions_12m."
		)

	segments = features["engagement_segment"].astype("string")
	if segments.isna().any() or not set(segments).issubset(SEGMENT_VALUES):
		raise MLFeatureBuildError("engagement_segment contains an unsupported value.")
	expected_segments = features["matches_purchased_12m"].map(_rule_segment).astype("string")
	if not segments.equals(expected_segments):
		raise MLFeatureBuildError(
			"engagement_segment is inconsistent with matches_purchased_12m."
		)

	calendar_as_of = features["as_of_at"].dt.normalize()
	calendar_window_start = features["window_start_at"].dt.normalize()
	calendar_engagement = features["last_engagement_date"].dt.normalize()
	features["recency_days"] = (
		(calendar_as_of - calendar_engagement).dt.days.where(
			present_engagement,
			(calendar_as_of - calendar_window_start).dt.days + 1,
		)
	).astype("Int64")
	features["rule_segment"] = segments
	features = features.reindex(columns=ML_FEATURE_COLUMNS)
	features = features.sort_values("fan_id", kind="stable").reset_index(drop=True)
	if features.isna().any().any():
		raise MLFeatureBuildError("ML feature output must not contain null values.")
	return features


def read_fan_activation(path: Path = FAN_ACTIVATION_INPUT) -> pd.DataFrame:
	path = Path(path)
	if not path.exists():
		raise MLFeatureBuildError(f"Missing fan activation input {path}.")
	try:
		frame = pd.read_parquet(path, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise MLFeatureBuildError(f"Could not read fan activation input {path}: {error}") from error
	_require_columns(frame)
	return frame


def write_ml_features(frame: pd.DataFrame, path: Path = ML_FEATURES_OUTPUT) -> Path:
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	try:
		frame.to_parquet(
			path,
			index=False,
			engine="pyarrow",
			version="2.4",
			coerce_timestamps="us",
			allow_truncated_timestamps=False,
		)
	except (OSError, ImportError, ValueError) as error:
		raise MLFeatureBuildError(f"Could not write ML features {path}: {error}") from error
	return path


def _argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Build deterministic fan ML features.")
	parser.add_argument("--input", type=Path, default=FAN_ACTIVATION_INPUT)
	parser.add_argument("--output", type=Path, default=ML_FEATURES_OUTPUT)
	return parser


def main(argv=None) -> int:
	arguments = _argument_parser().parse_args(argv)
	try:
		activation = read_fan_activation(arguments.input)
		features = build_ml_features(activation)
		path = write_ml_features(features, arguments.output)
	except MLFeatureBuildError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	try:
		display_path = path.relative_to(PROJECT_ROOT)
	except ValueError:
		display_path = path
	print(f"Rows: {len(features)}")
	print(f"Written: {display_path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())