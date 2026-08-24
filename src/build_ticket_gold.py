import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = PROJECT_ROOT / "data" / "silver"
GOLD_DIR = PROJECT_ROOT / "data" / "gold"
MATCH_TICKET_SALES_OUTPUT = GOLD_DIR / "match_ticket_sales.parquet"

COMPLETED_SALE_STATUS = "completed"
SALE_STATUS_VALUES = ("completed", "cancelled", "refunded")
REQUIRED_MATCH_COLUMNS = ("match_id",)
REQUIRED_TICKET_SALE_COLUMNS = (
	"ticket_sale_id",
	"match_id",
	"quantity",
	"unit_price_nok",
	"status",
)
MATCH_TICKET_SALES_COLUMNS = [
	"match_id",
	"completed_transactions",
	"tickets_sold",
	"gross_sales_nok",
]


class TicketGoldBuildError(RuntimeError):
	pass


def _read_frame(path: Path, required_columns) -> pd.DataFrame:
	if not path.exists():
		raise TicketGoldBuildError(f"Missing Silver input {path}.")
	try:
		frame = pd.read_parquet(path, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise TicketGoldBuildError(f"Could not read Silver input {path}: {error}") from error
	missing = [column for column in required_columns if column not in frame.columns]
	if missing:
		raise TicketGoldBuildError(f"Silver input {path} is missing columns {missing}.")
	return frame


def load_inputs(silver_dir: Path = SILVER_DIR) -> tuple:
	matches = _read_frame(silver_dir / "matches.parquet", REQUIRED_MATCH_COLUMNS)
	ticket_sales = _read_frame(
		silver_dir / "silver_ticket_sales.parquet", REQUIRED_TICKET_SALE_COLUMNS
	)
	return matches, ticket_sales


def build_match_ticket_sales(
	matches: pd.DataFrame,
	ticket_sales: pd.DataFrame,
) -> pd.DataFrame:
	match_frame = matches[list(REQUIRED_MATCH_COLUMNS)].copy()
	sale_frame = ticket_sales[list(REQUIRED_TICKET_SALE_COLUMNS)].copy()

	match_ids = pd.to_numeric(match_frame["match_id"], errors="coerce")
	sale_match_ids = pd.to_numeric(sale_frame["match_id"], errors="coerce")
	quantities = pd.to_numeric(sale_frame["quantity"], errors="coerce")
	unit_prices = pd.to_numeric(sale_frame["unit_price_nok"], errors="coerce")
	if (
		match_ids.isna().any()
		or sale_match_ids.isna().any()
		or quantities.isna().any()
		or unit_prices.isna().any()
		or (match_ids % 1 != 0).any()
		or (sale_match_ids % 1 != 0).any()
		or (quantities % 1 != 0).any()
		or not unit_prices.map(lambda value: math.isfinite(float(value))).all()
	):
		raise TicketGoldBuildError("Ticket Gold input contains an invalid numeric value.")

	match_frame["match_id"] = match_ids.astype("Int64")
	sale_frame["match_id"] = sale_match_ids.astype("Int64")
	sale_frame["quantity"] = quantities.astype("Int64")
	sale_frame["unit_price_nok"] = unit_prices.astype("Float64")
	sale_frame["ticket_sale_id"] = sale_frame["ticket_sale_id"].astype("string")
	sale_frame["status"] = sale_frame["status"].astype("string")

	if match_frame["match_id"].duplicated().any():
		raise TicketGoldBuildError("Ticket Gold match_id must be unique.")
	if sale_frame[["ticket_sale_id", "status"]].isna().any().any():
		raise TicketGoldBuildError("Ticket Gold sale identifier and status must be non-null.")
	if sale_frame["ticket_sale_id"].duplicated().any():
		raise TicketGoldBuildError("Ticket Gold ticket_sale_id must be unique.")
	if not set(sale_frame["match_id"]).issubset(set(match_frame["match_id"])):
		raise TicketGoldBuildError("Ticket Gold sale references an unknown match_id.")
	if not set(sale_frame["status"]).issubset(SALE_STATUS_VALUES):
		raise TicketGoldBuildError("Ticket Gold contains an unsupported sale status.")
	if (sale_frame["quantity"] <= 0).any() or (sale_frame["unit_price_nok"] <= 0).any():
		raise TicketGoldBuildError("Ticket Gold quantity and price must be positive.")

	completed = sale_frame[sale_frame["status"].eq(COMPLETED_SALE_STATUS)].copy()
	completed["gross_sales_nok"] = completed["quantity"] * completed["unit_price_nok"]
	aggregates = completed.groupby("match_id", sort=False).agg(
		completed_transactions=("ticket_sale_id", "size"),
		tickets_sold=("quantity", "sum"),
		gross_sales_nok=("gross_sales_nok", "sum"),
	)
	try:
		frame = match_frame.merge(
			aggregates,
			on="match_id",
			how="left",
			validate="one_to_one",
		)
	except pd.errors.MergeError as error:
		raise TicketGoldBuildError(f"Ticket Gold aggregation join failed: {error}") from error

	for column in ("completed_transactions", "tickets_sold"):
		frame[column] = frame[column].fillna(0).astype("Int64")
	frame["gross_sales_nok"] = frame["gross_sales_nok"].fillna(0).astype("Float64")
	frame = frame.reindex(columns=MATCH_TICKET_SALES_COLUMNS)
	frame = frame.sort_values("match_id", kind="stable").reset_index(drop=True)
	validate_match_ticket_sales(frame, expected_matches=len(match_frame))
	return frame


def validate_match_ticket_sales(frame: pd.DataFrame, expected_matches: int = None) -> None:
	if list(frame.columns) != MATCH_TICKET_SALES_COLUMNS:
		raise TicketGoldBuildError("Ticket Gold has an unexpected schema.")
	if frame["match_id"].isna().any() or frame["match_id"].duplicated().any():
		raise TicketGoldBuildError("Ticket Gold match_id must be unique and non-null.")
	metric_columns = ("completed_transactions", "tickets_sold", "gross_sales_nok")
	if frame[list(metric_columns)].isna().any().any() or (
		frame[list(metric_columns)] < 0
	).any().any():
		raise TicketGoldBuildError("Ticket Gold metrics must be non-negative.")
	if expected_matches is not None and len(frame) != expected_matches:
		raise TicketGoldBuildError(
			f"Ticket Gold contains {len(frame)} matches, expected {expected_matches}."
		)


def write_match_ticket_sales(
	frame: pd.DataFrame,
	output_dir: Path = GOLD_DIR,
) -> Path:
	output_dir.mkdir(parents=True, exist_ok=True)
	path = output_dir / MATCH_TICKET_SALES_OUTPUT.name
	try:
		frame.to_parquet(path, index=False, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise TicketGoldBuildError(f"Could not write match ticket sales Gold: {error}") from error
	return path


def main(argv=None) -> int:
	print("Building match ticket sales Gold...")
	try:
		matches, ticket_sales = load_inputs()
		frame = build_match_ticket_sales(matches, ticket_sales)
		path = write_match_ticket_sales(frame)
	except TicketGoldBuildError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	print(f"\nMatches: {len(frame)}")
	print(f"Tickets sold: {int(frame['tickets_sold'].sum())}")
	print("\nWritten:")
	print(path.relative_to(PROJECT_ROOT))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
