import argparse
import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = PROJECT_ROOT / "data" / "silver"
GOLD_DIR = PROJECT_ROOT / "data" / "gold"
FAN_ACTIVATION_OUTPUT = GOLD_DIR / "fan_activation.parquet"
FAN_SEGMENT_SUMMARY_OUTPUT = GOLD_DIR / "fan_segment_summary.parquet"

SEGMENT_VALUES = ("INACTIVE", "OCCASIONAL", "ENGAGED", "HIGHLY_ENGAGED")
SALE_STATUS_VALUES = ("completed", "cancelled", "refunded")

REQUIRED_FAN_COLUMNS = (
	"fan_id",
	"primary_email",
	"display_name",
	"marketing_consent",
	"consent_updated_at",
	"activation_eligible",
)
REQUIRED_TICKET_SALE_COLUMNS = (
	"ticket_sale_id",
	"fan_id",
	"match_id",
	"purchased_at",
	"quantity",
	"unit_price_nok",
	"status",
)
FAN_ACTIVATION_COLUMNS = [
	"fan_id",
	"primary_email",
	"display_name",
	"as_of_at",
	"window_start_at",
	"matches_purchased_12m",
	"purchase_transactions_12m",
	"tickets_purchased_12m",
	"total_spend_12m",
	"last_engagement_date",
	"cancelled_transactions_12m",
	"refunded_transactions_12m",
	"engagement_segment",
	"marketing_consent",
	"consent_updated_at",
	"marketing_allowed",
]
FAN_SEGMENT_SUMMARY_COLUMNS = [
	"engagement_segment",
	"as_of_at",
	"window_start_at",
	"fan_count",
	"consent_granted_count",
	"consent_declined_count",
	"consent_unknown_count",
	"activatable_count",
	"matches_purchased_median",
	"purchase_transactions_median",
	"tickets_purchased_median",
	"total_spend_median",
]
ADDITIVE_COLUMNS = (
	"matches_purchased_12m",
	"purchase_transactions_12m",
	"tickets_purchased_12m",
	"total_spend_12m",
	"cancelled_transactions_12m",
	"refunded_transactions_12m",
)


class FanGoldBuildError(RuntimeError):
	pass


def parse_as_of(value: str) -> pd.Timestamp:
	try:
		as_of = pd.Timestamp(value)
	except (TypeError, ValueError) as error:
		raise FanGoldBuildError(f"Invalid --as-of value {value!r}.") from error
	if pd.isna(as_of):
		raise FanGoldBuildError(f"Invalid --as-of value {value!r}.")
	if as_of.tzinfo is None:
		as_of = as_of.tz_localize("UTC")
	else:
		as_of = as_of.tz_convert("UTC")
	return as_of


def read_silver_frame(path: Path, required_columns) -> pd.DataFrame:
	if not path.exists():
		raise FanGoldBuildError(
			f"Missing Silver input {path}. Run build_fan_silver first."
		)
	try:
		frame = pd.read_parquet(path, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise FanGoldBuildError(f"Could not read Silver Parquet file {path}: {error}") from error
	missing = [column for column in required_columns if column not in frame.columns]
	if missing:
		raise FanGoldBuildError(f"Silver input {path} is missing columns {missing}.")
	return frame


def load_fan_silver(silver_dir: Path = SILVER_DIR) -> tuple:
	fans = read_silver_frame(
		silver_dir / "silver_fans.parquet", REQUIRED_FAN_COLUMNS
	)
	ticket_sales = read_silver_frame(
		silver_dir / "silver_ticket_sales.parquet",
		REQUIRED_TICKET_SALE_COLUMNS,
	)
	return fans, ticket_sales


def _typed_inputs(fans: pd.DataFrame, ticket_sales: pd.DataFrame) -> tuple:
	fan_frame = fans.copy()
	sale_frame = ticket_sales.copy()
	if fan_frame["fan_id"].isna().any() or fan_frame["fan_id"].duplicated().any():
		raise FanGoldBuildError(
			"Fan activation input validation failed: fan_id must be unique and non-null."
		)
	for column in ("fan_id", "primary_email", "display_name"):
		fan_frame[column] = fan_frame[column].astype("string")
	try:
		fan_frame["marketing_consent"] = fan_frame["marketing_consent"].astype("boolean")
		fan_frame["activation_eligible"] = fan_frame["activation_eligible"].astype("boolean")
	except (TypeError, ValueError) as error:
		raise FanGoldBuildError(
			"Fan activation input validation failed: consent or eligibility is invalid."
		) from error
	raw_consent_timestamps = fan_frame["consent_updated_at"].astype("string").str.strip()
	raw_consent_present = raw_consent_timestamps.notna() & raw_consent_timestamps.ne("")
	fan_frame["consent_updated_at"] = pd.to_datetime(
		fan_frame["consent_updated_at"], utc=True, errors="coerce", format="mixed"
	)
	if (raw_consent_present & fan_frame["consent_updated_at"].isna()).any():
		raise FanGoldBuildError(
			"Fan activation input validation failed: consent_updated_at is invalid."
		)
	if not fan_frame["marketing_consent"].notna().equals(
		fan_frame["consent_updated_at"].notna()
	):
		raise FanGoldBuildError(
			"Fan activation input validation failed: consent value and timestamp must occur together."
		)

	for column in ("ticket_sale_id", "fan_id", "status"):
		sale_frame[column] = sale_frame[column].astype("string")
	match_ids = pd.to_numeric(sale_frame["match_id"], errors="coerce")
	quantities = pd.to_numeric(sale_frame["quantity"], errors="coerce")
	unit_prices = pd.to_numeric(sale_frame["unit_price_nok"], errors="coerce")
	if (
		match_ids.isna().any()
		or quantities.isna().any()
		or unit_prices.isna().any()
		or (match_ids % 1 != 0).any()
		or (quantities % 1 != 0).any()
		or not unit_prices.map(lambda value: math.isfinite(float(value))).all()
	):
		raise FanGoldBuildError(
			"Fan activation input validation failed: ticket sale numeric value is invalid."
		)
	sale_frame["match_id"] = match_ids.astype("Int64")
	sale_frame["quantity"] = quantities.astype("Int64")
	sale_frame["unit_price_nok"] = unit_prices.astype("Float64")
	sale_frame["purchased_at"] = pd.to_datetime(
		sale_frame["purchased_at"], utc=True, errors="coerce", format="mixed"
	)

	for column in ("ticket_sale_id", "fan_id", "match_id", "purchased_at", "quantity", "unit_price_nok", "status"):
		if sale_frame[column].isna().any():
			raise FanGoldBuildError(
				f"Fan activation input validation failed: ticket sale {column} is null or invalid."
			)
	if sale_frame["ticket_sale_id"].duplicated().any():
		raise FanGoldBuildError(
			"Fan activation input validation failed: ticket_sale_id is duplicated."
		)
	if not set(sale_frame["fan_id"]).issubset(set(fan_frame["fan_id"])):
		raise FanGoldBuildError(
			"Fan activation input validation failed: ticket sale references an unknown fan_id."
		)
	if not set(sale_frame["status"]).issubset(SALE_STATUS_VALUES):
		raise FanGoldBuildError(
			"Fan activation input validation failed: unsupported ticket sale status."
		)
	if (sale_frame["quantity"] <= 0).any() or (sale_frame["unit_price_nok"] <= 0).any():
		raise FanGoldBuildError(
			"Fan activation input validation failed: quantity and price must be positive."
		)
	return fan_frame, sale_frame


def engagement_segment(matches_purchased: int) -> str:
	if matches_purchased == 0:
		return "INACTIVE"
	if matches_purchased <= 2:
		return "OCCASIONAL"
	if matches_purchased <= 5:
		return "ENGAGED"
	return "HIGHLY_ENGAGED"


def _window_metrics(sales: pd.DataFrame) -> pd.DataFrame:
	completed = sales[sales["status"].eq("completed")].copy()
	completed["spend_nok"] = completed["quantity"] * completed["unit_price_nok"]
	metrics = completed.groupby("fan_id", sort=False).agg(
		matches_purchased_12m=("match_id", "nunique"),
		purchase_transactions_12m=("ticket_sale_id", "size"),
		tickets_purchased_12m=("quantity", "sum"),
		total_spend_12m=("spend_nok", "sum"),
	)
	for status in ("cancelled", "refunded"):
		column = f"{status}_transactions_12m"
		metrics = metrics.join(
			sales[sales["status"].eq(status)].groupby("fan_id").size().rename(column),
			how="outer",
		)
	return metrics.reset_index()


def build_fan_activation(
	fans: pd.DataFrame,
	ticket_sales: pd.DataFrame,
	as_of: pd.Timestamp,
) -> pd.DataFrame:
	as_of = parse_as_of(str(as_of))
	window_start = as_of - pd.DateOffset(months=12)
	fan_frame, sale_frame = _typed_inputs(fans, ticket_sales)
	future_consent = fan_frame["consent_updated_at"].notna() & fan_frame[
		"consent_updated_at"
	].gt(as_of)
	if future_consent.any():
		raise FanGoldBuildError(
			"Fan activation validation failed: consent_updated_at is after as_of."
		)

	historical_completed = sale_frame[
		sale_frame["status"].eq("completed") & sale_frame["purchased_at"].lt(as_of)
	]
	last_engagement = historical_completed.groupby("fan_id")["purchased_at"].max()
	window_sales = sale_frame[
		sale_frame["purchased_at"].ge(window_start)
		& sale_frame["purchased_at"].lt(as_of)
	]
	metrics = _window_metrics(window_sales)
	try:
		frame = fan_frame[list(REQUIRED_FAN_COLUMNS)].merge(
			metrics, on="fan_id", how="left", validate="one_to_one"
		)
	except pd.errors.MergeError as error:
		raise FanGoldBuildError(f"Fan activation join failed: {error}") from error
	frame["last_engagement_date"] = frame["fan_id"].map(last_engagement)
	frame["as_of_at"] = as_of
	frame["window_start_at"] = window_start
	for column in ADDITIVE_COLUMNS:
		frame[column] = frame[column].fillna(0)
	for column in (
		"matches_purchased_12m",
		"purchase_transactions_12m",
		"tickets_purchased_12m",
		"cancelled_transactions_12m",
		"refunded_transactions_12m",
	):
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
	frame["total_spend_12m"] = pd.to_numeric(
		frame["total_spend_12m"], errors="coerce"
	).astype("Float64")
	frame["engagement_segment"] = frame["matches_purchased_12m"].map(
		engagement_segment
	).astype("string")
	frame["marketing_allowed"] = (
		frame["marketing_consent"].fillna(False) & frame["primary_email"].notna()
	).astype("boolean")
	frame = frame.reindex(columns=FAN_ACTIVATION_COLUMNS)
	frame = frame.sort_values("fan_id", kind="stable").reset_index(drop=True)
	validate_fan_activation(frame, fan_frame, as_of, window_start)
	return frame


def validate_fan_activation(
	frame: pd.DataFrame,
	fans: pd.DataFrame = None,
	as_of: pd.Timestamp = None,
	window_start: pd.Timestamp = None,
) -> None:
	if frame["fan_id"].isna().any() or frame["fan_id"].duplicated().any():
		raise FanGoldBuildError(
			"Fan activation validation failed: fan_id must be unique and non-null."
		)
	for column in ADDITIVE_COLUMNS:
		if frame[column].isna().any() or (frame[column] < 0).any():
			raise FanGoldBuildError(
				f"Fan activation validation failed: {column} must be non-negative."
			)
	if not frame["total_spend_12m"].map(
		lambda value: math.isfinite(float(value))
	).all():
		raise FanGoldBuildError(
			"Fan activation validation failed: total_spend_12m must be finite."
		)
	if not set(frame["engagement_segment"]).issubset(SEGMENT_VALUES):
		raise FanGoldBuildError(
			"Fan activation validation failed: unsupported engagement_segment."
		)
	expected_segments = frame["matches_purchased_12m"].map(engagement_segment).astype("string")
	if not frame["engagement_segment"].equals(expected_segments):
		raise FanGoldBuildError(
			"Fan activation validation failed: engagement_segment is inconsistent."
		)
	expected_allowed = (
		frame["marketing_consent"].fillna(False) & frame["primary_email"].notna()
	).astype("boolean")
	if not frame["marketing_allowed"].equals(expected_allowed):
		raise FanGoldBuildError(
			"Fan activation validation failed: marketing_allowed is inconsistent."
		)
	if as_of is not None and not frame["as_of_at"].eq(as_of).all():
		raise FanGoldBuildError("Fan activation validation failed: as_of_at is inconsistent.")
	if window_start is not None and not frame["window_start_at"].eq(window_start).all():
		raise FanGoldBuildError(
			"Fan activation validation failed: window_start_at is inconsistent."
		)
	if fans is not None:
		if len(frame) != len(fans) or set(frame["fan_id"]) != set(fans["fan_id"]):
			raise FanGoldBuildError(
				"Fan activation validation failed: fan set differs from Silver fans."
			)
		eligible = fans.set_index("fan_id")["activation_eligible"].astype("boolean")
		allowed = frame.set_index("fan_id")["marketing_allowed"]
		if not allowed.equals(eligible.reindex(allowed.index)):
			raise FanGoldBuildError(
				"Fan activation validation failed: marketing_allowed differs from Silver eligibility."
			)


def build_fan_segment_summary(frame: pd.DataFrame) -> pd.DataFrame:
	validate_fan_activation(frame)
	if frame.empty:
		raise FanGoldBuildError("Fan segment summary requires at least one fan.")

	rows = []
	for segment in SEGMENT_VALUES:
		selection = frame[frame["engagement_segment"].eq(segment)]
		consent = selection["marketing_consent"].astype("boolean")
		rows.append(
			{
				"engagement_segment": segment,
				"as_of_at": frame["as_of_at"].iloc[0],
				"window_start_at": frame["window_start_at"].iloc[0],
				"fan_count": len(selection),
				"consent_granted_count": int(consent.eq(True).sum()),
				"consent_declined_count": int(consent.eq(False).sum()),
				"consent_unknown_count": int(consent.isna().sum()),
				"activatable_count": int(
					selection["marketing_allowed"].astype("boolean").eq(True).sum()
				),
				"matches_purchased_median": selection["matches_purchased_12m"].median(),
				"purchase_transactions_median": selection[
					"purchase_transactions_12m"
				].median(),
				"tickets_purchased_median": selection["tickets_purchased_12m"].median(),
				"total_spend_median": selection["total_spend_12m"].median(),
			}
		)

	summary = pd.DataFrame(rows, columns=FAN_SEGMENT_SUMMARY_COLUMNS)
	for column in (
		"fan_count",
		"consent_granted_count",
		"consent_declined_count",
		"consent_unknown_count",
		"activatable_count",
	):
		summary[column] = summary[column].astype("Int64")
	for column in (
		"matches_purchased_median",
		"purchase_transactions_median",
		"tickets_purchased_median",
		"total_spend_median",
	):
		summary[column] = summary[column].astype("Float64")
	validate_fan_segment_summary(summary, expected_fans=len(frame))
	return summary


def validate_fan_segment_summary(frame: pd.DataFrame, expected_fans: int = None) -> None:
	if list(frame.columns) != FAN_SEGMENT_SUMMARY_COLUMNS:
		raise FanGoldBuildError("Fan segment summary has an unexpected schema.")
	if frame["engagement_segment"].duplicated().any() or set(
		frame["engagement_segment"]
	) != set(SEGMENT_VALUES):
		raise FanGoldBuildError(
			"Fan segment summary must contain one row for every engagement segment."
		)
	count_columns = (
		"fan_count",
		"consent_granted_count",
		"consent_declined_count",
		"consent_unknown_count",
		"activatable_count",
	)
	if frame[list(count_columns)].isna().any().any() or (
		frame[list(count_columns)] < 0
	).any().any():
		raise FanGoldBuildError("Fan segment summary counts must be non-negative.")
	consent_total = frame[
		["consent_granted_count", "consent_declined_count", "consent_unknown_count"]
	].sum(axis=1)
	if not consent_total.equals(frame["fan_count"]):
		raise FanGoldBuildError("Fan segment summary consent counts do not reconcile.")
	if (frame["activatable_count"] > frame["consent_granted_count"]).any():
		raise FanGoldBuildError(
			"Fan segment summary activatable count exceeds granted consent."
		)
	if expected_fans is not None and int(frame["fan_count"].sum()) != expected_fans:
		raise FanGoldBuildError(
			f"Fan segment summary covers {int(frame['fan_count'].sum())} fans, "
			f"expected {expected_fans}."
		)
	for column in ("as_of_at", "window_start_at"):
		if frame[column].isna().any() or frame[column].nunique() != 1:
			raise FanGoldBuildError(
				f"Fan segment summary {column} must contain one shared timestamp."
			)


def write_fan_activation(
	frame: pd.DataFrame,
	output_dir: Path = GOLD_DIR,
) -> Path:
	output_dir.mkdir(parents=True, exist_ok=True)
	path = output_dir / FAN_ACTIVATION_OUTPUT.name
	try:
		frame.to_parquet(path, index=False, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise FanGoldBuildError(f"Could not write fan activation Gold: {error}") from error
	return path


def write_fan_segment_summary(
	frame: pd.DataFrame,
	output_dir: Path = GOLD_DIR,
) -> Path:
	output_dir.mkdir(parents=True, exist_ok=True)
	path = output_dir / FAN_SEGMENT_SUMMARY_OUTPUT.name
	try:
		frame.to_parquet(path, index=False, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise FanGoldBuildError(f"Could not write fan segment summary Gold: {error}") from error
	return path


def _argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Build consent-aware fan activation Gold.")
	parser.add_argument(
		"--as-of",
		required=True,
		help="Exclusive UTC snapshot boundary, for example 2026-08-22.",
	)
	return parser


def main(argv=None) -> int:
	arguments = _argument_parser().parse_args(argv)
	print("Building fan activation Gold...")
	try:
		as_of = parse_as_of(arguments.as_of)
		fans, ticket_sales = load_fan_silver()
		activation = build_fan_activation(fans, ticket_sales, as_of)
		summary = build_fan_segment_summary(activation)
		activation_path = write_fan_activation(activation)
		summary_path = write_fan_segment_summary(summary)
	except FanGoldBuildError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	print(f"\nFans: {len(activation)}")
	print(f"Marketing allowed: {int(activation['marketing_allowed'].sum())}")
	print(f"\nSegments:\n{activation['engagement_segment'].value_counts().to_string()}")
	print("\nWritten:")
	print(activation_path.relative_to(PROJECT_ROOT))
	print(summary_path.relative_to(PROJECT_ROOT))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())