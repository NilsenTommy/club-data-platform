import csv
import random
import re
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATCHES_INPUT = PROJECT_ROOT / "data" / "silver" / "matches.parquet"
MATCH_INSIGHTS_INPUT = PROJECT_ROOT / "data" / "gold" / "match_insights.parquet"
OUTPUT_DIR = PROJECT_ROOT / "data" / "bronze" / "supporter"

RANDOM_SEED = 2932026
FOCUS_TEAM_ID = 293
SUPPORTER_COUNT = 500
TICKET_CUSTOMER_COUNT = 425
APP_USER_COUNT = 375
OVERLAP_COUNT = 300
RESERVED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}

TICKET_CUSTOMER_COLUMNS = [
	"ticket_customer_id",
	"email",
	"name",
	"created_at",
	"postal_code",
	"country",
	"marketing_consent",
	"consent_updated_at",
]
APP_USER_COLUMNS = [
	"app_user_id",
	"email",
	"display_name",
	"registered_at",
	"push_opt_in",
	"locale",
]
TICKET_SALE_COLUMNS = [
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
]
REQUIRED_MATCH_COLUMNS = (
	"match_id",
	"kickoff_at",
	"competition",
	"home_team_id",
	"home_team_name",
	"away_team_id",
	"away_team_name",
	"home_score",
	"away_score",
	"status",
)

FIRST_NAMES = (
	"Ada", "Aksel", "Amalie", "Anders", "Astrid", "Elias", "Emil", "Emma",
	"Erik", "Frida", "Hanna", "Henrik", "Ida", "Ingrid", "Isak", "Jakob",
	"Julie", "Kari", "Kristian", "Lea", "Lina", "Magnus", "Maja", "Marius",
	"Nora", "Oda", "Ola", "Pernille", "Sara", "Sigrid", "Sofie", "Thea",
)
LAST_NAMES = (
	"Berg", "Dahl", "Eide", "Fjell", "Gran", "Hagen", "Haugen", "Holm",
	"Johansen", "Knutsen", "Kristiansen", "Larsen", "Lund", "Moen", "Nilsen",
	"Nordli", "Olsen", "Pedersen", "Solberg", "Strand", "Sund", "Vik",
)
NICKNAMES = ("Glimtvenn", "Nordlys", "Aspmyra", "Gul", "Bodoe", "1916")
FRAGMENTATION_COUNTS = {
	"exact": 180,
	"case": 30,
	"whitespace": 25,
	"alias": 25,
	"different": 25,
	"missing_ticket": 8,
	"missing_app": 7,
}


class FanDataError(RuntimeError):
	pass


def _timestamp(value) -> str:
	return pd.Timestamp(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_parquet(path: Path, description: str) -> pd.DataFrame:
	if not path.exists():
		raise FanDataError(f"Missing {description} {path}.")
	try:
		return pd.read_parquet(path, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise FanDataError(f"Could not read {description} {path}: {error}") from error


def validate_match_data(matches: pd.DataFrame) -> pd.DataFrame:
	missing = [column for column in REQUIRED_MATCH_COLUMNS if column not in matches.columns]
	if missing:
		raise FanDataError(f"Silver matches are missing columns {missing}.")
	frame = matches.copy()
	frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True, errors="coerce")
	if frame.empty:
		raise FanDataError("Silver matches contain no rows.")
	if frame["match_id"].isna().any() or frame["match_id"].duplicated().any():
		raise FanDataError("Silver matches must have unique, non-null match_id values.")
	if frame["kickoff_at"].isna().any():
		raise FanDataError("Silver matches contain an invalid or missing kickoff_at.")
	if frame[["home_team_id", "away_team_id"]].isna().any().any():
		raise FanDataError("Silver matches contain a missing home_team_id or away_team_id.")
	is_home = frame["home_team_id"].eq(FOCUS_TEAM_ID)
	is_away = frame["away_team_id"].eq(FOCUS_TEAM_ID)
	if (~(is_home ^ is_away)).any():
		raise FanDataError(
			f"Every Silver match must contain team {FOCUS_TEAM_ID} exactly once."
		)
	return frame.sort_values(["kickoff_at", "match_id"], kind="stable").reset_index(drop=True)


def load_match_data(
	matches_path: Path = MATCHES_INPUT,
	insights_path: Path = MATCH_INSIGHTS_INPUT,
) -> tuple:
	matches = validate_match_data(_read_parquet(matches_path, "Silver match input"))
	insights = None
	if insights_path.exists():
		insights = _read_parquet(insights_path, "Gold match insights input")
		if "match_id" not in insights.columns or insights["match_id"].duplicated().any():
			raise FanDataError("Gold match insights must have a unique match_id column.")
	return matches, insights


def create_supporter_profiles(seed: int = RANDOM_SEED) -> pd.DataFrame:
	rng = random.Random(seed)
	fragmentation = [
		kind
		for kind, count in FRAGMENTATION_COUNTS.items()
		for _ in range(count)
	]
	rng.shuffle(fragmentation)
	rows = []
	for index in range(SUPPORTER_COUNT):
		first_name = FIRST_NAMES[(index * 7 + rng.randrange(len(FIRST_NAMES))) % len(FIRST_NAMES)]
		last_name = LAST_NAMES[(index * 11 + rng.randrange(len(LAST_NAMES))) % len(LAST_NAMES)]
		domain = sorted(RESERVED_EMAIL_DOMAINS)[index % len(RESERVED_EMAIL_DOMAINS)]
		local_part = f"{first_name}.{last_name}.{index + 1:03d}".lower()
		rows.append(
			{
				"canonical_supporter_id": f"FAN-{index + 1:04d}",
				"first_name": first_name,
				"last_name": last_name,
				"base_email": f"{local_part}@{domain}",
				"in_ticket_system": index < TICKET_CUSTOMER_COUNT,
				"in_app": index < OVERLAP_COUNT or index >= TICKET_CUSTOMER_COUNT,
				"fragmentation_type": fragmentation[index] if index < OVERLAP_COUNT else "source_only",
			}
		)
	return pd.DataFrame(rows)


def _fragmented_emails(profile) -> tuple:
	base_email = profile.base_email
	kind = profile.fragmentation_type
	if kind == "case":
		return base_email, base_email.upper()
	if kind == "whitespace":
		return base_email, f"  {base_email} "
	if kind == "alias":
		local_part, domain = base_email.split("@")
		return base_email, f"{local_part}+app@{domain}"
	if kind == "different":
		return base_email, f"supporter.{profile.Index + 1:03d}@example.net"
	if kind == "missing_ticket":
		return None, base_email
	if kind == "missing_app":
		return base_email, None
	return base_email, base_email


def project_ticket_customers(
	profiles: pd.DataFrame,
	matches: pd.DataFrame,
	seed: int = RANDOM_SEED,
) -> pd.DataFrame:
	rng = random.Random(seed + 1)
	earliest_kickoff = matches["kickoff_at"].min()
	rows = []
	for profile in profiles[profiles["in_ticket_system"]].itertuples():
		email, _ = _fragmented_emails(profile)
		if profile.Index >= OVERLAP_COUNT and profile.Index % 47 == 0:
			email = rows[-1]["email"]
		created_at = earliest_kickoff - pd.Timedelta(days=rng.randint(240, 2200))
		consent_updated_at = created_at + pd.Timedelta(days=30 + profile.Index % 181)
		rows.append(
			{
				"ticket_customer_id": str(71000000 + profile.Index * 17),
				"email": email,
				"name": None if profile.Index % 89 == 0 else f"{profile.first_name} {profile.last_name}",
				"created_at": _timestamp(created_at),
				"postal_code": None if profile.Index % 31 == 0 else f"{8000 + (profile.Index * 37) % 1999:04d}",
				"country": None if profile.Index % 97 == 0 else "NO",
				"marketing_consent": profile.Index % 3 != 0,
				"consent_updated_at": _timestamp(consent_updated_at),
			}
		)
	return pd.DataFrame(rows, columns=TICKET_CUSTOMER_COLUMNS)


def _display_name(profile) -> str:
	mode = profile.Index % 5
	if mode == 0:
		return profile.first_name
	if mode == 1:
		return f"{profile.first_name} {profile.last_name[0]}."
	if mode == 2:
		return f"{profile.first_name[0]}. {profile.last_name}"
	if mode == 3:
		return f"{NICKNAMES[profile.Index % len(NICKNAMES)]}{profile.Index + 1}"
	return f"{profile.first_name} {profile.last_name}"


def project_app_users(
	profiles: pd.DataFrame,
	matches: pd.DataFrame,
	seed: int = RANDOM_SEED,
) -> pd.DataFrame:
	rng = random.Random(seed + 2)
	earliest_kickoff = matches["kickoff_at"].min()
	rows = []
	for profile in profiles[profiles["in_app"]].itertuples():
		_, email = _fragmented_emails(profile)
		if profile.Index >= OVERLAP_COUNT and profile.Index % 19 == 0:
			email = None
		registered_at = earliest_kickoff - pd.Timedelta(days=rng.randint(30, 1500))
		rows.append(
			{
				"app_user_id": f"APP-{992 + profile.Index * 13:05d}",
				"email": email,
				"display_name": None if profile.Index % 43 == 0 else _display_name(profile),
				"registered_at": _timestamp(registered_at),
				"push_opt_in": profile.Index % 4 != 0,
				"locale": ("nb-NO", "en-GB", "nn-NO")[profile.Index % 3],
			}
		)
	return pd.DataFrame(rows, columns=APP_USER_COLUMNS)


def _match_demand(match, previous_results: list) -> int:
	is_home = match.home_team_id == FOCUS_TEAM_ID
	base = 235 if is_home else 90
	competition = str(match.competition).lower()
	if "champions" in competition:
		base *= 1.24
	elif "europa" in competition:
		base *= 1.13
	elif "friendly" in competition:
		base *= 0.75
	opponent = match.away_team_name if is_home else match.home_team_name
	if any(name in str(opponent).lower() for name in ("roma", "madrid", "dortmund", "molde", "rosenborg")):
		base *= 1.12
	if match.kickoff_at.weekday() >= 5:
		base *= 1.08
	if previous_results:
		base *= 0.94 + 0.06 * sum(previous_results[-3:]) / len(previous_results[-3:])
	return max(35, round(base))


def _focus_result(match):
	if str(match.status).lower() not in {"complete", "finished"}:
		return None
	if pd.isna(match.home_score) or pd.isna(match.away_score):
		return None
	if match.home_team_id == FOCUS_TEAM_ID:
		scored, conceded = match.home_score, match.away_score
	else:
		scored, conceded = match.away_score, match.home_score
	return 1.0 if scored > conceded else 0.5 if scored == conceded else 0.0


def _bad_weather(match_id, weather_by_match: dict) -> bool:
	weather = weather_by_match.get(match_id)
	if weather is None:
		return False
	precipitation = weather.get("precipitation_mm")
	wind = weather.get("wind_speed_ms")
	temperature = weather.get("temperature_c")
	return (
		(not pd.isna(precipitation) and precipitation >= 2.0)
		or (not pd.isna(wind) and wind >= 10.0)
		or (not pd.isna(temperature) and temperature <= -5.0)
	)


def generate_ticket_sales(
	matches: pd.DataFrame,
	customers: pd.DataFrame,
	insights: pd.DataFrame = None,
	seed: int = RANDOM_SEED,
) -> pd.DataFrame:
	rng = random.Random(seed + 3)
	weather_by_match = {}
	if insights is not None:
		weather_columns = {"temperature_c", "precipitation_mm", "wind_speed_ms"}
		if weather_columns.issubset(insights.columns):
			weather_by_match = insights.set_index("match_id")[list(weather_columns)].to_dict("index")
	previous_results = []
	rows = []
	customer_ids = customers["ticket_customer_id"].tolist()
	for match in matches.itertuples(index=False):
		transaction_count = min(len(customer_ids), _match_demand(match, previous_results))
		selected_customers = rng.sample(customer_ids, transaction_count)
		bad_weather = _bad_weather(match.match_id, weather_by_match)
		for customer_id in selected_customers:
			late_probability = 0.12 if bad_weather else 0.27
			if rng.random() < late_probability:
				lead_hours = rng.randint(3, 72)
			else:
				lead_hours = rng.randint(73, 24 * 120)
			purchased_at = match.kickoff_at - pd.Timedelta(hours=lead_hours, minutes=rng.randint(0, 59))
			ticket_type = rng.choices(
				("adult", "youth", "student", "child"), weights=(55, 18, 17, 10), k=1
			)[0]
			base_price = {"adult": 420, "youth": 240, "student": 290, "child": 160}[ticket_type]
			if match.home_team_id != FOCUS_TEAM_ID:
				base_price = round(base_price * 0.85 / 10) * 10
			status = rng.choices(
				("completed", "cancelled", "refunded"), weights=(94, 3, 3), k=1
			)[0]
			rows.append(
				{
					"ticket_sale_id": f"TS-{len(rows) + 1:07d}",
					"ticket_customer_id": customer_id,
					"match_id": match.match_id,
					"match_type": "home" if match.home_team_id == FOCUS_TEAM_ID else "away",
					"purchased_at": _timestamp(purchased_at),
					"ticket_type": ticket_type,
					"quantity": rng.choices((1, 2, 3, 4), weights=(50, 32, 12, 6), k=1)[0],
					"unit_price_nok": base_price,
					"sales_channel": rng.choices(
						("web", "app", "box_office"), weights=(55, 37, 8), k=1
					)[0],
					"status": status,
				}
			)
		result = _focus_result(match)
		if result is not None:
			previous_results.append(result)
	return pd.DataFrame(rows, columns=TICKET_SALE_COLUMNS)


def _validate_email_domains(frame: pd.DataFrame, column: str) -> None:
	for email in frame[column].dropna():
		value = str(email).strip()
		if not re.fullmatch(r"[^@\s]+@[^@\s]+", value):
			raise FanDataError(f"Invalid synthetic email address {email!r}.")
		if value.rsplit("@", 1)[1].lower() not in RESERVED_EMAIL_DOMAINS:
			raise FanDataError(f"Non-reserved email domain in {email!r}.")


def validate_datasets(
	profiles: pd.DataFrame,
	matches: pd.DataFrame,
	customers: pd.DataFrame,
	app_users: pd.DataFrame,
	sales: pd.DataFrame,
) -> None:
	if len(profiles) != SUPPORTER_COUNT or profiles["canonical_supporter_id"].nunique() != SUPPORTER_COUNT:
		raise FanDataError("Exactly 500 unique underlying supporters are required.")
	if not (profiles["in_ticket_system"] | profiles["in_app"]).all():
		raise FanDataError("Every underlying supporter must occur in at least one source.")
	if len(customers) != TICKET_CUSTOMER_COUNT or len(app_users) != APP_USER_COUNT:
		raise FanDataError("Unexpected ticket customer or app user population size.")
	if int((profiles["in_ticket_system"] & profiles["in_app"]).sum()) != OVERLAP_COUNT:
		raise FanDataError("Unexpected source-system overlap.")
	for frame, id_column in (
		(customers, "ticket_customer_id"),
		(app_users, "app_user_id"),
		(sales, "ticket_sale_id"),
	):
		if frame[id_column].isna().any() or frame[id_column].duplicated().any():
			raise FanDataError(f"{id_column} values must be unique and non-null.")
	for frame in (customers, app_users, sales):
		if "canonical_supporter_id" in frame.columns:
			raise FanDataError("Internal canonical supporter IDs must not leak to raw data.")
	_validate_email_domains(customers, "email")
	_validate_email_domains(app_users, "email")
	if not set(sales["ticket_customer_id"]).issubset(set(customers["ticket_customer_id"])):
		raise FanDataError("Ticket sales contain an unknown ticket_customer_id.")
	if set(sales["match_id"]) != set(matches["match_id"]):
		raise FanDataError("Every sale must reference a match and every match must have sales.")
	expected_match_types = {
		"home" if match.home_team_id == FOCUS_TEAM_ID else "away"
		for match in matches.itertuples(index=False)
	}
	if set(sales["match_type"]) != expected_match_types:
		raise FanDataError("Ticket sale types must match the supplied matches.")
	match_lookup = matches.set_index("match_id")
	expected_type = sales["match_id"].map(
		lambda match_id: "home" if match_lookup.loc[match_id, "home_team_id"] == FOCUS_TEAM_ID else "away"
	)
	if not expected_type.equals(sales["match_type"]):
		raise FanDataError("A ticket sale has an incorrect match_type.")
	purchased_at = pd.to_datetime(sales["purchased_at"], utc=True, errors="coerce")
	kickoff_at = sales["match_id"].map(match_lookup["kickoff_at"])
	if purchased_at.isna().any() or not (purchased_at < kickoff_at).all():
		raise FanDataError("Every ticket purchase must occur before kickoff.")
	if (sales["quantity"] <= 0).any() or (sales["unit_price_nok"] <= 0).any():
		raise FanDataError("Ticket quantities and prices must be positive.")
	if not set(sales["status"]).issubset({"completed", "cancelled", "refunded"}):
		raise FanDataError("Ticket sales contain an unsupported status.")
	if sales.duplicated(["ticket_customer_id", "match_id"]).any():
		raise FanDataError("A customer has multiple ticket transactions for one match.")
	first_purchase = sales.groupby("ticket_customer_id")["purchased_at"].min()
	created_at = customers.set_index("ticket_customer_id")["created_at"]
	for customer_id, purchase in first_purchase.items():
		if pd.Timestamp(created_at[customer_id]) >= pd.Timestamp(purchase):
			raise FanDataError("A customer was created after their first ticket purchase.")
	consent_updated_at = pd.to_datetime(
		customers["consent_updated_at"], utc=True, errors="coerce"
	)
	customer_created_at = pd.to_datetime(customers["created_at"], utc=True, errors="coerce")
	if consent_updated_at.isna().any() or (consent_updated_at < customer_created_at).any():
		raise FanDataError("Consent timestamps must be valid and not precede customer creation.")
	fragmentation = profiles.loc[
		profiles["in_ticket_system"] & profiles["in_app"], "fragmentation_type"
	]
	if fragmentation.value_counts().to_dict() != FRAGMENTATION_COUNTS:
		raise FanDataError("Expected identity fragmentation is missing.")


def generate_datasets(
	matches: pd.DataFrame,
	insights: pd.DataFrame = None,
	seed: int = RANDOM_SEED,
) -> tuple:
	matches = validate_match_data(matches)
	profiles = create_supporter_profiles(seed)
	customers = project_ticket_customers(profiles, matches, seed)
	app_users = project_app_users(profiles, matches, seed)
	sales = generate_ticket_sales(matches, customers, insights, seed)
	validate_datasets(profiles, matches, customers, app_users, sales)
	return profiles, customers, app_users, sales


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	frame.to_csv(
		path,
		index=False,
		encoding="utf-8",
		lineterminator="\n",
		quoting=csv.QUOTE_MINIMAL,
	)


def write_datasets(
	customers: pd.DataFrame,
	app_users: pd.DataFrame,
	sales: pd.DataFrame,
	output_dir: Path = OUTPUT_DIR,
) -> tuple:
	paths = (
		output_dir / "ticket_system" / "ticket_customers.csv",
		output_dir / "ticket_system" / "ticket_sales.csv",
		output_dir / "app" / "app_users.csv",
	)
	_write_csv(customers, paths[0])
	_write_csv(sales, paths[1])
	_write_csv(app_users, paths[2])
	return paths


def main() -> int:
	print("Generating synthetic fragmented supporter data...")
	try:
		matches, insights = load_match_data()
		_, customers, app_users, sales = generate_datasets(matches, insights)
		paths = write_datasets(customers, app_users, sales)
	except FanDataError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	counts = sales["match_type"].value_counts()
	print(f"\nTicket customers: {len(customers)}")
	print(f"App users: {len(app_users)}")
	print(f"Ticket transactions: {len(sales)}")
	print(f"Home transactions: {counts.get('home', 0)}")
	print(f"Away transactions: {counts.get('away', 0)}")
	print("\nWritten:")
	for path in paths:
		print(path.relative_to(PROJECT_ROOT))
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
