import hashlib
import math
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BRONZE_SUPPORTER_DIR = PROJECT_ROOT / "data" / "bronze" / "supporter"
SILVER_DIR = PROJECT_ROOT / "data" / "silver"
MATCHES_INPUT = SILVER_DIR / "matches.parquet"
FANS_OUTPUT = SILVER_DIR / "silver_fans.parquet"
FAN_IDENTITIES_OUTPUT = SILVER_DIR / "silver_fan_identities.parquet"
TICKET_SALES_OUTPUT = SILVER_DIR / "silver_ticket_sales.parquet"

TICKET_CUSTOMER_COLUMNS = (
	"ticket_customer_id",
	"email",
	"name",
	"created_at",
)
APP_USER_COLUMNS = (
	"app_user_id",
	"email",
	"display_name",
	"registered_at",
)
RAW_TICKET_SALE_COLUMNS = (
	"ticket_sale_id",
	"ticket_customer_id",
	"match_id",
	"match_type",
	"purchased_at",
	"ticket_type",
	"quantity",
	"unit_price_nok",
	"sales_channel",
	"status",
)
FAN_COLUMNS = [
	"fan_id",
	"primary_email",
	"display_name",
	"first_seen_at",
	"source_count",
]
FAN_IDENTITY_COLUMNS = [
	"fan_id",
	"source",
	"source_id",
	"normalized_email",
	"match_method",
]
SILVER_TICKET_SALE_COLUMNS = [
	"ticket_sale_id",
	"fan_id",
	"ticket_customer_id",
	"match_id",
	"match_type",
	"purchased_at",
	"ticket_type",
	"quantity",
	"unit_price_nok",
	"sales_channel",
	"status",
]


class FanSilverBuildError(RuntimeError):
	pass


def _read_csv(path: Path, required_columns) -> pd.DataFrame:
	if not path.exists():
		raise FanSilverBuildError(f"Missing Bronze supporter input {path}.")
	try:
		frame = pd.read_csv(path, dtype="string")
	except (OSError, UnicodeError, pd.errors.ParserError) as error:
		raise FanSilverBuildError(f"Could not read Bronze supporter input {path}: {error}") from error
	missing = [column for column in required_columns if column not in frame.columns]
	if missing:
		raise FanSilverBuildError(f"Bronze supporter input {path} is missing columns {missing}.")
	return frame


def load_supporter_data(
	bronze_dir: Path = BRONZE_SUPPORTER_DIR,
	matches_path: Path = MATCHES_INPUT,
) -> tuple:
	customers = _read_csv(
		bronze_dir / "ticket_system" / "ticket_customers.csv",
		TICKET_CUSTOMER_COLUMNS,
	)
	app_users = _read_csv(
		bronze_dir / "app" / "app_users.csv",
		APP_USER_COLUMNS,
	)
	ticket_sales = _read_csv(
		bronze_dir / "ticket_system" / "ticket_sales.csv",
		RAW_TICKET_SALE_COLUMNS,
	)
	if not matches_path.exists():
		raise FanSilverBuildError(f"Missing Silver match input {matches_path}.")
	try:
		matches = pd.read_parquet(matches_path, columns=["match_id"], engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise FanSilverBuildError(f"Could not read Silver match input {matches_path}: {error}") from error
	return customers, app_users, ticket_sales, matches


def normalize_email(value):
	if pd.isna(value) or not str(value).strip():
		return None
	email = str(value).strip().casefold()
	if email.count("@") != 1:
		raise FanSilverBuildError(f"Invalid supporter email address {value!r}.")
	local_part, domain = email.split("@")
	local_part = local_part.split("+", 1)[0]
	if not local_part or not domain:
		raise FanSilverBuildError(f"Invalid supporter email address {value!r}.")
	return f"{local_part}@{domain}"


def _validate_source(frame: pd.DataFrame, id_column: str, source: str) -> None:
	if frame[id_column].isna().any() or frame[id_column].duplicated().any():
		raise FanSilverBuildError(
			f"Silver fan validation failed: {source} {id_column} values must be unique and non-null."
		)


def _typed_sources(
	ticket_customers: pd.DataFrame,
	app_users: pd.DataFrame,
) -> tuple:
	tickets = ticket_customers.copy()
	apps = app_users.copy()
	_validate_source(tickets, "ticket_customer_id", "ticketing")
	_validate_source(apps, "app_user_id", "app")
	for frame, email_column in ((tickets, "email"), (apps, "email")):
		frame["normalized_email"] = frame[email_column].map(normalize_email).astype("string")
	tickets["created_at"] = pd.to_datetime(tickets["created_at"], utc=True, errors="coerce")
	apps["registered_at"] = pd.to_datetime(apps["registered_at"], utc=True, errors="coerce")
	if tickets["created_at"].isna().any():
		raise FanSilverBuildError("Silver fan validation failed: ticketing created_at is invalid.")
	if apps["registered_at"].isna().any():
		raise FanSilverBuildError("Silver fan validation failed: app registered_at is invalid.")
	return (
		tickets.sort_values("ticket_customer_id", kind="stable").reset_index(drop=True),
		apps.sort_values("app_user_id", kind="stable").reset_index(drop=True),
	)


def stable_fan_id(ticket=None, app=None) -> str:
	if ticket is not None:
		identity = f"ticketing:{ticket.ticket_customer_id}"
	elif app is not None:
		identity = f"app:{app.app_user_id}"
	else:
		raise FanSilverBuildError("Cannot create fan_id without a source identity.")
	digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16].upper()
	return f"FAN-{digest}"


def build_fans_and_identities(
	ticket_customers: pd.DataFrame,
	app_users: pd.DataFrame,
) -> tuple:
	tickets, apps = _typed_sources(ticket_customers, app_users)
	ticket_email_counts = tickets["normalized_email"].dropna().value_counts()
	app_email_counts = apps["normalized_email"].dropna().value_counts()
	linkable_emails = {
		email
		for email, count in ticket_email_counts.items()
		if count == 1 and app_email_counts.get(email, 0) == 1
	}
	apps_by_email = {
		row.normalized_email: row
		for row in apps.itertuples(index=False)
		if row.normalized_email in linkable_emails
	}
	matched_app_ids = set()
	groups = []
	for ticket in tickets.itertuples(index=False):
		app = apps_by_email.get(ticket.normalized_email)
		if app is not None:
			matched_app_ids.add(app.app_user_id)
		groups.append((ticket, app))
	for app in apps.itertuples(index=False):
		if app.app_user_id not in matched_app_ids:
			groups.append((None, app))

	fan_rows = []
	identity_rows = []
	for ticket, app in groups:
		fan_id = stable_fan_id(ticket, app)
		is_linked = ticket is not None and app is not None
		primary_email = ticket.normalized_email if ticket is not None else app.normalized_email
		display_name = ticket.name if ticket is not None and not pd.isna(ticket.name) else None
		if not display_name and app is not None and not pd.isna(app.display_name):
			display_name = app.display_name
		timestamps = []
		if ticket is not None:
			timestamps.append(ticket.created_at)
		if app is not None:
			timestamps.append(app.registered_at)
		fan_rows.append(
			{
				"fan_id": fan_id,
				"primary_email": primary_email,
				"display_name": display_name,
				"first_seen_at": min(timestamps),
				"source_count": len(timestamps),
			}
		)
		for source, source_id, normalized_email in (
			(
				"ticketing",
				ticket.ticket_customer_id if ticket is not None else None,
				ticket.normalized_email if ticket is not None else None,
			),
			(
				"app",
				app.app_user_id if app is not None else None,
				app.normalized_email if app is not None else None,
			),
		):
			if source_id is not None:
				identity_rows.append(
					{
						"fan_id": fan_id,
						"source": source,
						"source_id": source_id,
						"normalized_email": normalized_email,
						"match_method": "normalized_email" if is_linked else "source_only",
					}
				)

	fans = pd.DataFrame(fan_rows, columns=FAN_COLUMNS)
	identities = pd.DataFrame(identity_rows, columns=FAN_IDENTITY_COLUMNS)
	for column in ("fan_id", "primary_email", "display_name"):
		fans[column] = fans[column].astype("string")
	fans["first_seen_at"] = pd.to_datetime(fans["first_seen_at"], utc=True)
	fans["source_count"] = pd.to_numeric(fans["source_count"]).astype("Int64")
	for column in FAN_IDENTITY_COLUMNS:
		identities[column] = identities[column].astype("string")
	validate_fans(fans, identities, tickets, apps)
	return fans, identities


def validate_fans(
	fans: pd.DataFrame,
	identities: pd.DataFrame,
	ticket_customers: pd.DataFrame,
	app_users: pd.DataFrame,
) -> None:
	if fans["fan_id"].isna().any() or fans["fan_id"].duplicated().any():
		raise FanSilverBuildError("Silver fan validation failed: fan_id must be unique and non-null.")
	if identities.duplicated(["source", "source_id"]).any():
		raise FanSilverBuildError("Silver fan validation failed: a source identity maps more than once.")
	if not set(identities["fan_id"]).issubset(set(fans["fan_id"])):
		raise FanSilverBuildError("Silver fan validation failed: identity references an unknown fan_id.")
	if set(identities["source"]) != {"ticketing", "app"}:
		raise FanSilverBuildError("Silver fan validation failed: unexpected or missing identity source.")
	expected_identities = {
		*(('ticketing', source_id) for source_id in ticket_customers["ticket_customer_id"]),
		*(('app', source_id) for source_id in app_users["app_user_id"]),
	}
	actual_identities = set(zip(identities["source"], identities["source_id"]))
	if actual_identities != expected_identities:
		raise FanSilverBuildError("Silver fan validation failed: source identities are incomplete.")
	if identities.groupby(["fan_id", "source"]).size().gt(1).any():
		raise FanSilverBuildError("Silver fan validation failed: a fan has multiple IDs from one source.")
	identity_counts = identities.groupby("fan_id").size()
	fan_counts = fans.set_index("fan_id")["source_count"]
	if not fan_counts.equals(identity_counts.reindex(fan_counts.index).astype("Int64")):
		raise FanSilverBuildError("Silver fan validation failed: source_count is inconsistent.")


def build_ticket_sales(
	ticket_sales: pd.DataFrame,
	identities: pd.DataFrame,
	matches: pd.DataFrame,
) -> pd.DataFrame:
	missing = [column for column in RAW_TICKET_SALE_COLUMNS if column not in ticket_sales.columns]
	if missing:
		raise FanSilverBuildError(f"Bronze ticket sales are missing columns {missing}.")
	if "match_id" not in matches.columns:
		raise FanSilverBuildError("Silver matches are missing match_id.")
	frame = ticket_sales.copy()
	ticket_identities = identities.loc[
		identities["source"].eq("ticketing"), ["fan_id", "source_id"]
	].rename(columns={"source_id": "ticket_customer_id"})
	try:
		frame = frame.merge(
			ticket_identities,
			on="ticket_customer_id",
			how="left",
			validate="many_to_one",
		)
	except pd.errors.MergeError as error:
		raise FanSilverBuildError(f"Silver ticket sale identity join failed: {error}") from error
	frame = frame.reindex(columns=SILVER_TICKET_SALE_COLUMNS)
	for column in ("ticket_sale_id", "fan_id", "ticket_customer_id", "match_type", "ticket_type", "sales_channel", "status"):
		frame[column] = frame[column].astype("string")
	frame["match_id"] = pd.to_numeric(frame["match_id"], errors="coerce").astype("Int64")
	quantity = pd.to_numeric(frame["quantity"], errors="coerce")
	unit_price = pd.to_numeric(frame["unit_price_nok"], errors="coerce")
	if (
		quantity.isna().any()
		or unit_price.isna().any()
		or not quantity.map(lambda value: math.isfinite(float(value))).all()
		or not unit_price.map(lambda value: math.isfinite(float(value))).all()
		or (quantity % 1 != 0).any()
	):
		raise FanSilverBuildError(
			"Silver ticket sales validation failed: quantity and price must be valid finite numbers."
		)
	frame["quantity"] = quantity.astype("Int64")
	frame["unit_price_nok"] = unit_price.astype("Float64")
	frame["purchased_at"] = pd.to_datetime(frame["purchased_at"], utc=True, errors="coerce")
	frame = frame.sort_values("ticket_sale_id", kind="stable").reset_index(drop=True)
	validate_ticket_sales(frame, matches)
	return frame


def validate_ticket_sales(frame: pd.DataFrame, matches: pd.DataFrame) -> None:
	for column in (
		"ticket_sale_id",
		"fan_id",
		"ticket_customer_id",
		"match_id",
		"purchased_at",
		"quantity",
		"unit_price_nok",
	):
		if frame[column].isna().any():
			raise FanSilverBuildError(f"Silver ticket sales validation failed: {column} is null or invalid.")
	if frame["ticket_sale_id"].duplicated().any():
		raise FanSilverBuildError("Silver ticket sales validation failed: ticket_sale_id is duplicated.")
	if not set(frame["match_id"]).issubset(set(matches["match_id"])):
		raise FanSilverBuildError("Silver ticket sales validation failed: unknown match_id.")
	if not frame["unit_price_nok"].map(lambda value: math.isfinite(float(value))).all():
		raise FanSilverBuildError("Silver ticket sales validation failed: unit_price_nok is not finite.")
	if (frame["quantity"] <= 0).any() or (frame["unit_price_nok"] <= 0).any():
		raise FanSilverBuildError("Silver ticket sales validation failed: quantity and price must be positive.")
	if not set(frame["match_type"]).issubset({"home", "away"}):
		raise FanSilverBuildError("Silver ticket sales validation failed: invalid match_type.")
	if not set(frame["status"]).issubset({"completed", "cancelled", "refunded"}):
		raise FanSilverBuildError("Silver ticket sales validation failed: invalid status.")


def build_supporter_silver(
	ticket_customers: pd.DataFrame,
	app_users: pd.DataFrame,
	ticket_sales: pd.DataFrame,
	matches: pd.DataFrame,
) -> tuple:
	fans, identities = build_fans_and_identities(ticket_customers, app_users)
	sales = build_ticket_sales(ticket_sales, identities, matches)
	return fans, identities, sales


def write_supporter_silver(
	fans: pd.DataFrame,
	identities: pd.DataFrame,
	ticket_sales: pd.DataFrame,
	output_dir: Path = SILVER_DIR,
) -> dict:
	output_dir.mkdir(parents=True, exist_ok=True)
	paths = {
		"fans": output_dir / FANS_OUTPUT.name,
		"fan_identities": output_dir / FAN_IDENTITIES_OUTPUT.name,
		"ticket_sales": output_dir / TICKET_SALES_OUTPUT.name,
	}
	try:
		fans.to_parquet(paths["fans"], index=False, engine="pyarrow")
		identities.to_parquet(paths["fan_identities"], index=False, engine="pyarrow")
		ticket_sales.to_parquet(paths["ticket_sales"], index=False, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise FanSilverBuildError(f"Could not write supporter Silver Parquet files: {error}") from error
	return paths


def main() -> int:
	print("Building supporter Silver layer...")
	try:
		customers, app_users, raw_sales, matches = load_supporter_data()
		fans, identities, ticket_sales = build_supporter_silver(
			customers, app_users, raw_sales, matches
		)
		paths = write_supporter_silver(fans, identities, ticket_sales)
	except FanSilverBuildError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	linked_fans = int(fans["source_count"].eq(2).sum())
	print(f"\nFans: {len(fans)} ({linked_fans} linked across sources)")
	print(f"Fan identities: {len(identities)}")
	print(f"Ticket sales: {len(ticket_sales)}")
	print("\nWritten:")
	for path in paths.values():
		print(path.relative_to(PROJECT_ROOT))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())