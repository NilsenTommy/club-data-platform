import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = PROJECT_ROOT / "data" / "gold"
ML_SNAPSHOT = PROJECT_ROOT / "data" / "ml" / "fan_segmentation_summary.json"
PORTFOLIO_OUTPUT = PROJECT_ROOT / "docs" / "data" / "portfolio.json"

SCHEMA_VERSION = 1
CLUB_TEAM_NAME = "FK Bodo - Glimt"
CLUB_DISPLAY_NAME = "FK Bodø/Glimt"

REQUIRED_MATCH_COLUMNS = (
	"match_id",
	"kickoff_at",
	"competition",
	"season",
	"home_team_name",
	"away_team_name",
	"home_score",
	"away_score",
	"result",
	"stadium_name",
	"country",
	"latitude",
	"longitude",
	"weather_observed_at",
	"temperature_c",
	"precipitation_mm",
	"wind_speed_ms",
)
REQUIRED_MATCH_TICKET_SALES_COLUMNS = (
	"match_id",
	"completed_transactions",
	"tickets_sold",
	"gross_sales_nok",
)
REQUIRED_FAN_SEGMENT_SUMMARY_COLUMNS = (
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
)

SEGMENT_ORDER = ("INACTIVE", "OCCASIONAL", "ENGAGED", "HIGHLY_ENGAGED")
SEGMENT_LABELS = {
	"INACTIVE": "Inaktiv",
	"OCCASIONAL": "Sporadisk",
	"ENGAGED": "Engasjert",
	"HIGHLY_ENGAGED": "Svært engasjert",
}
SEGMENT_RULES = {
	"INACTIVE": "Har ikke kjøpt billett de siste 12 månedene",
	"OCCASIONAL": "Har kjøpt billett til 1–2 kamper",
	"ENGAGED": "Har kjøpt billett til 3–5 kamper",
	"HIGHLY_ENGAGED": "Har kjøpt billett til 6 kamper eller flere",
}

REQUIRED_ML_KEYS = (
	"schemaVersion",
	"experiment",
	"promotion",
	"selection",
	"clusterProfiles",
	"ruleComparison",
	"guardrails",
	"decision",
)
PROMOTION_SOURCES = ("local_reproduction", "mlflow_artifact")
ALLOWED_ML_FEATURES = (
	"recency_days",
	"matches_purchased_12m",
	"purchase_transactions_12m",
	"tickets_purchased_12m",
	"total_spend_12m",
	"cancelled_transactions_12m",
	"refunded_transactions_12m",
)
ARI_NOTE = (
	"Tallet viser hvor likt modellen og reglene deler inn supporterne. 1 betyr helt "
	"likt, 0 betyr ingen sammenheng. Det finnes ingen fasit her – reglene er bare en "
	"annen måte å dele inn på."
)

# Publisering skjer til et offentlig GitHub Pages-nettsted; disse feltene skal aldri forlate repoet.
FORBIDDEN_OUTPUT_KEYS = (
	"fanId",
	"fan_id",
	"primaryEmail",
	"primary_email",
	"displayName",
	"display_name",
	"email",
	"consentUpdatedAt",
	"consent_updated_at",
	"ticketSaleId",
	"ticket_sale_id",
	"ticketCustomerId",
	"ticket_customer_id",
)
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class PortfolioBuildError(RuntimeError):
	pass


def _read_frame(path: Path, required_columns) -> pd.DataFrame:
	if not path.exists():
		raise PortfolioBuildError(f"Missing input {path}. Run the pipeline first.")
	try:
		frame = pd.read_parquet(path, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise PortfolioBuildError(f"Could not read {path}: {error}") from error
	missing = [column for column in required_columns if column not in frame.columns]
	if missing:
		raise PortfolioBuildError(f"Input {path} is missing columns {missing}.")
	return frame


def load_inputs(gold_dir: Path = GOLD_DIR) -> tuple:
	matches = _read_frame(gold_dir / "match_insights.parquet", REQUIRED_MATCH_COLUMNS)
	match_ticket_sales = _read_frame(
		gold_dir / "match_ticket_sales.parquet", REQUIRED_MATCH_TICKET_SALES_COLUMNS
	)
	fan_segment_summary = _read_frame(
		gold_dir / "fan_segment_summary.parquet", REQUIRED_FAN_SEGMENT_SUMMARY_COLUMNS
	)
	return matches, match_ticket_sales, fan_segment_summary


def _read_snapshot(path: Path, required_keys) -> dict:
	if not path.exists():
		raise PortfolioBuildError(f"Missing snapshot {path}. Promote it before building.")
	try:
		snapshot = json.loads(path.read_text(encoding="utf-8"))
	except (OSError, ValueError) as error:
		raise PortfolioBuildError(f"Could not read {path}: {error}") from error
	if not isinstance(snapshot, dict):
		raise PortfolioBuildError(f"Snapshot {path} must be a JSON object.")
	missing = [key for key in required_keys if key not in snapshot]
	if missing:
		raise PortfolioBuildError(f"Snapshot {path} is missing keys {missing}.")
	if snapshot["schemaVersion"] != SCHEMA_VERSION:
		raise PortfolioBuildError(
			f"Snapshot {path} has schemaVersion {snapshot['schemaVersion']}, "
			f"expected {SCHEMA_VERSION}."
		)
	return snapshot


def load_snapshots(ml_snapshot: Path = ML_SNAPSHOT) -> dict:
	return _read_snapshot(Path(ml_snapshot), REQUIRED_ML_KEYS)


def _optional_number(value, decimals=None):
	if value is None or pd.isna(value):
		return None
	number = float(value)
	if decimals is not None:
		number = round(number, decimals)
	return number + 0.0


def _optional_int(value):
	if value is None or pd.isna(value):
		return None
	return int(value)


def _optional_text(value):
	if value is None or pd.isna(value):
		return None
	text = str(value).strip()
	return text or None


def _instant(value):
	if value is None or pd.isna(value):
		return None
	timestamp = pd.Timestamp(value)
	timestamp = (
		timestamp.tz_localize("UTC")
		if timestamp.tzinfo is None
		else timestamp.tz_convert("UTC")
	)
	return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _nb_number(value, decimals=0) -> str:
	if value is None:
		return "ukjent"
	text = f"{float(value):,.{decimals}f}"
	return text.replace(",", " ").replace(".", ",")


def _match_rows(matches: pd.DataFrame, match_ticket_sales: pd.DataFrame) -> list:
	frame = matches.copy()
	if frame["match_id"].isna().any() or frame["match_id"].duplicated().any():
		raise PortfolioBuildError("match_id must be unique and non-null.")
	tickets = match_ticket_sales.copy()
	if tickets["match_id"].isna().any() or tickets["match_id"].duplicated().any():
		raise PortfolioBuildError("Ticket Gold match_id must be unique and non-null.")
	ticket_values = pd.to_numeric(tickets["tickets_sold"], errors="coerce")
	if ticket_values.isna().any() or (ticket_values < 0).any() or (
		ticket_values % 1 != 0
	).any():
		raise PortfolioBuildError("Ticket Gold tickets_sold must be a non-negative integer.")
	tickets["tickets_sold"] = ticket_values.astype("Int64")
	if set(tickets["match_id"]) != set(frame["match_id"]):
		raise PortfolioBuildError("Ticket Gold must contain exactly the match insight IDs.")
	frame["kickoff_at"] = pd.to_datetime(frame["kickoff_at"], utc=True, errors="coerce")
	if frame["kickoff_at"].isna().any():
		raise PortfolioBuildError("kickoff_at must contain valid UTC timestamps.")
	try:
		frame = frame.merge(
			tickets[["match_id", "tickets_sold"]],
			on="match_id",
			how="left",
			validate="one_to_one",
		)
	except pd.errors.MergeError as error:
		raise PortfolioBuildError(f"Ticket aggregation join failed: {error}") from error

	is_home = frame["home_team_name"].eq(CLUB_TEAM_NAME)
	is_away = frame["away_team_name"].eq(CLUB_TEAM_NAME)
	if not bool((is_home | is_away).all()):
		raise PortfolioBuildError(
			f"Every match must involve {CLUB_TEAM_NAME!r}; check the club team name."
		)
	frame["is_home"] = is_home
	frame["opponent"] = frame["away_team_name"].where(is_home, frame["home_team_name"])
	frame = frame.sort_values(["kickoff_at", "match_id"], kind="stable")

	rows = []
	for match in frame.itertuples(index=False):
		temperature = _optional_number(match.temperature_c, 1)
		precipitation = _optional_number(match.precipitation_mm, 1)
		wind = _optional_number(match.wind_speed_ms, 1)
		rows.append(
			{
				"matchId": int(match.match_id),
				"date": _instant(match.kickoff_at)[:10],
				"kickoffAt": _instant(match.kickoff_at),
				"competition": _optional_text(match.competition),
				"season": _optional_int(match.season),
				"isHome": bool(match.is_home),
				"opponent": _optional_text(match.opponent),
				"ticketsSold": int(match.tickets_sold),
				"result": _optional_text(match.result),
				"homeScore": _optional_int(match.home_score),
				"awayScore": _optional_int(match.away_score),
				"stadiumName": _optional_text(match.stadium_name),
				"country": _optional_text(match.country),
				"weather": {
					"observedAt": _instant(match.weather_observed_at),
					"temperatureC": temperature,
					"precipitationMm": precipitation,
					"windSpeedMs": wind,
				},
				"coverage": {
					"hasCoordinates": bool(
						not pd.isna(match.latitude) and not pd.isna(match.longitude)
					),
					"hasTemperature": temperature is not None,
					"hasCompleteWeather": None not in (temperature, precipitation, wind),
				},
			}
		)
	return rows


def _side_summary(rows: list, home: bool) -> dict:
	values = sorted(row["ticketsSold"] for row in rows if row["isHome"] is home)
	if not values:
		return {"matchCount": 0, "totalTickets": 0, "averageTickets": None, "medianTickets": None}
	series = pd.Series(values, dtype="Int64")
	return {
		"matchCount": len(values),
		"totalTickets": int(series.sum()),
		"averageTickets": _optional_number(series.mean(), 1),
		"medianTickets": _optional_number(series.median(), 1),
	}


def build_matches_view(matches: pd.DataFrame, match_ticket_sales: pd.DataFrame) -> dict:
	rows = _match_rows(matches, match_ticket_sales)
	home = _side_summary(rows, home=True)
	away = _side_summary(rows, home=False)
	ratio = None
	if (
		home["averageTickets"] is not None
		and away["averageTickets"] is not None
		and away["averageTickets"] != 0
	):
		ratio = round(home["averageTickets"] / away["averageTickets"], 2)
	return {
		"club": CLUB_DISPLAY_NAME,
		"totals": {
			"matchCount": len(rows),
			"totalTickets": sum(row["ticketsSold"] for row in rows),
			"firstDate": rows[0]["date"] if rows else None,
			"lastDate": rows[-1]["date"] if rows else None,
		},
		"homeAway": {"home": home, "away": away, "ratio": ratio},
		"weatherCoverage": {
			"matchCount": len(rows),
			"withTemperature": sum(1 for row in rows if row["coverage"]["hasTemperature"]),
			"withCompleteWeather": sum(
				1 for row in rows if row["coverage"]["hasCompleteWeather"]
			),
			"withCoordinates": sum(1 for row in rows if row["coverage"]["hasCoordinates"]),
			"definition": "Komplett vær betyr temperatur, nedbør og vind fra samme observasjon.",
		},
		"opponents": sorted({row["opponent"] for row in rows if row["opponent"]}),
		"competitions": sorted({row["competition"] for row in rows if row["competition"]}),
		"rows": rows,
	}


def build_supporters_view(fan_segment_summary: pd.DataFrame) -> dict:
	frame = fan_segment_summary.copy()
	segments = frame["engagement_segment"].astype("string")
	if segments.isna().any() or segments.duplicated().any() or set(segments) != set(
		SEGMENT_ORDER
	):
		raise PortfolioBuildError(
			"Fan segment summary must contain one row for every engagement_segment."
		)
	count_columns = (
		"fan_count",
		"consent_granted_count",
		"consent_declined_count",
		"consent_unknown_count",
		"activatable_count",
	)
	for column in count_columns:
		frame[column] = pd.to_numeric(frame[column], errors="coerce")
	if frame[list(count_columns)].isna().any().any() or (
		frame[list(count_columns)] < 0
	).any().any():
		raise PortfolioBuildError("Fan segment summary counts must be non-negative.")
	consent_totals = frame[
		["consent_granted_count", "consent_declined_count", "consent_unknown_count"]
	].sum(axis=1)
	if not consent_totals.equals(frame["fan_count"]):
		raise PortfolioBuildError("Fan segment summary consent counts do not reconcile.")
	if (frame["activatable_count"] > frame["consent_granted_count"]).any():
		raise PortfolioBuildError(
			"Fan segment summary activatable count exceeds granted consent."
		)
	for column in ("as_of_at", "window_start_at"):
		if frame[column].isna().any() or frame[column].nunique() != 1:
			raise PortfolioBuildError(
				f"Fan segment summary {column} must contain one shared timestamp."
			)

	total = int(frame["fan_count"].sum())
	granted = int(frame["consent_granted_count"].sum())
	declined = int(frame["consent_declined_count"].sum())
	unknown = int(frame["consent_unknown_count"].sum())
	activatable = int(frame["activatable_count"].sum())

	segment_rows = []
	for name in SEGMENT_ORDER:
		selection = frame[segments.eq(name)].iloc[0]
		count = int(selection["fan_count"])
		segment_rows.append(
			{
				"segment": name,
				"label": SEGMENT_LABELS[name],
				"rule": SEGMENT_RULES[name],
				"count": count,
				"share": round(count / total * 100, 1) if total else 0.0,
				"activatable": int(selection["activatable_count"]),
				"medians": {
					"matchesPurchased": _optional_number(
						selection["matches_purchased_median"], 1
					),
					"purchaseTransactions": _optional_number(
						selection["purchase_transactions_median"], 1
					),
					"ticketsPurchased": _optional_number(
						selection["tickets_purchased_median"], 1
					),
					"totalSpend": _optional_number(selection["total_spend_median"], 0),
				},
			}
		)

	return {
		"totalFans": total,
		"asOfAt": _instant(frame["as_of_at"].iloc[0]),
		"windowStartAt": _instant(frame["window_start_at"].iloc[0]),
		"consent": {
			"granted": granted,
			"declined": declined,
			"unknown": unknown,
			"grantedShare": round(granted / total * 100, 1) if total else 0.0,
		},
		"activatable": activatable,
		"activatableShare": round(activatable / total * 100, 1) if total else 0.0,
		"funnel": [
			{
				"id": "canonical",
				"label": "Alle supportere",
				"count": total,
				"note": "Slått sammen fra billettsystemet og supporterappen.",
			},
			{
				"id": "consent",
				"label": "Har sagt ja",
				"count": granted,
				"note": f"{declined} har sagt nei. For {unknown} er det ukjent, fordi de ikke er koblet til billettsystemet.",
			},
			{
				"id": "activatable",
				"label": "Kan kontaktes",
				"count": activatable,
				"note": "Har både sagt ja og oppgitt en e-postadresse.",
			},
		],
		"segments": segment_rows,
		"governance": {
			"consentSource": "Billettsystemet er kilden til hvem som har sagt ja.",
			"activationRule": "Det kreves både et ja og en e-postadresse.",
			"separation": (
				"Hvor aktiv en supporter er, sier ingenting om klubben har lov til å "
				"kontakte vedkommende."
			),
			"synthetic": (
				"Supporterdataene er laget for demonstrasjon og tilhører ikke ekte personer."
			),
		},
	}


def build_ml_view(snapshot: dict) -> dict:
	experiment = snapshot["experiment"]
	promotion = snapshot["promotion"]
	selection = snapshot["selection"]
	profiles = snapshot["clusterProfiles"]
	comparison = snapshot["ruleComparison"]
	features = list(experiment["features"])
	unknown_features = [feature for feature in features if feature not in ALLOWED_ML_FEATURES]
	if unknown_features or len(features) != len(set(features)):
		raise PortfolioBuildError(
			f"ML snapshot contains unsupported or duplicate features {unknown_features}."
		)
	promotion_source = promotion.get("source")
	if promotion_source not in PROMOTION_SOURCES:
		raise PortfolioBuildError(
			f"ML promotion source must be one of {PROMOTION_SOURCES}."
		)
	if promotion_source == "mlflow_artifact" and not experiment.get("selectionRunId"):
		raise PortfolioBuildError(
			"MLflow artifact promotion requires experiment.selectionRunId."
		)

	row_count = experiment["rowCount"]
	profile_total = sum(profile["count"] for profile in profiles)
	if profile_total != row_count:
		raise PortfolioBuildError(
			f"ML cluster profiles cover {profile_total} rows, expected {row_count}."
		)
	if len(profiles) != selection["selectedK"]:
		raise PortfolioBuildError(
			f"ML snapshot has {len(profiles)} profiles but selectedK is "
			f"{selection['selectedK']}."
		)
	failed = [entry["check"] for entry in snapshot["guardrails"] if not entry["passed"]]
	if failed:
		raise PortfolioBuildError(f"ML guardrails did not pass: {failed}.")

	rule_segments = comparison["ruleSegments"]
	counts_by_segment = {profile["segment"]: profile["count"] for profile in profiles}
	cross_tab = []
	for row in comparison["crossTab"]:
		counts = row["counts"]
		unknown = [segment for segment in counts if segment not in rule_segments]
		if unknown:
			raise PortfolioBuildError(f"ML cross tab has unknown rule segments {unknown}.")
		total = sum(counts.values())
		if total != counts_by_segment.get(row["segment"]):
			raise PortfolioBuildError(
				f"ML cross tab row {row['segment']} sums to {total}, expected "
				f"{counts_by_segment.get(row['segment'])}."
			)
		cross_tab.append(
			{
				"segment": row["segment"],
				"total": total,
				"cells": [
					{
						"ruleSegment": segment,
						"label": SEGMENT_LABELS[segment],
						"count": counts.get(segment, 0),
						"share": round(counts.get(segment, 0) / total * 100, 1),
					}
					for segment in rule_segments
				],
			}
		)

	return {
		"snapshotAt": experiment["snapshotAt"],
		"windowStartAt": experiment["windowStartAt"],
		"notebook": experiment["notebook"],
		"selectionRunId": experiment["selectionRunId"],
		"promotion": {
			"promotedAt": promotion["promotedAt"],
			"source": promotion_source,
			"note": promotion["note"],
		},
		"setup": {
			"rowCount": row_count,
			"randomState": experiment["randomState"],
			"nInit": experiment["nInit"],
			"features": features,
			"stabilitySeeds": list(experiment["stabilitySeeds"]),
			"pipeline": "log1p → standardisering → K-means",
		},
		"selection": {
			"selectedK": selection["selectedK"],
			"baselineK": selection["baselineK"],
			"selectedSilhouette": selection["selectedSilhouette"],
			"baselineSilhouette": selection["baselineSilhouette"],
			"silhouetteDelta": selection["silhouetteDelta"],
			"deltaLabel": _nb_number(selection["silhouetteDelta"], 5),
			"rule": selection["rule"],
			"caveat": (
				f"Forskjellen mellom {selection['selectedK']} og "
				f"{selection['baselineK']} grupper er bare "
				f"{_nb_number(selection['silhouetteDelta'], 5)} poeng. "
				f"{selection['selectedK']} grupper er altså knapt bedre enn "
				f"{selection['baselineK']}, så inndelingen bør regnes som en antakelse."
			),
			"candidates": [
				{
					"k": candidate["k"],
					"valid": candidate["valid"],
					"silhouetteScore": candidate["silhouetteScore"],
					"inertia": candidate["inertia"],
					"minimumClusterSize": candidate["minimumClusterSize"],
					"minimumClusterShare": candidate["minimumClusterShare"],
					"stabilityAriMean": candidate["stabilityAriMean"],
					"stabilityAriMinimum": candidate["stabilityAriMinimum"],
					"selected": candidate["selected"],
				}
				for candidate in selection["candidates"]
			],
		},
		"profiles": [
			{
				"segment": profile["segment"],
				"label": profile["label"],
				"interpretation": profile["interpretation"],
				"count": profile["count"],
				"share": profile["share"],
				"medians": {
					feature: profile["medians"][feature]
					for feature in features
				},
			}
			for profile in profiles
		],
		"ruleComparison": {
			"adjustedRandIndex": comparison["adjustedRandIndex"],
			"ruleSegments": [
				{"segment": segment, "label": SEGMENT_LABELS[segment]}
				for segment in rule_segments
			],
			"crossTab": cross_tab,
			"note": ARI_NOTE,
		},
		"guardrails": [
			{"check": entry["check"], "passed": entry["passed"]}
			for entry in snapshot["guardrails"]
		],
		"decision": {
			"verdict": snapshot["decision"]["verdict"],
			"reasons": list(snapshot["decision"]["reasons"]),
			"notRegistered": list(snapshot["decision"]["notRegistered"]),
		},
	}


def _home_away_finding(home: dict, away: dict, ratio) -> dict:
	home_average = home["averageTickets"]
	away_average = away["averageTickets"]
	if home_average is None or away_average is None:
		title = "For få kamper til å sammenligne billettsalg"
		value = "–"
		unit = "mangler sammenligningsgrunnlag"
		see = (
			f"Tallene dekker {home['matchCount']} hjemmekamper og "
			f"{away['matchCount']} bortekamper. Begge typer må finnes for å kunne "
			"sammenligne billettsalget."
		)
	elif home_average > away_average:
		title = "Hjemmekamper selger langt flere billetter"
		if ratio is None:
			value = _nb_number(home_average, 0)
			unit = "billetter i snitt hjemme, mot 0 borte"
		else:
			value = f"{_nb_number(ratio, 2)}×"
			unit = "så mange billetter hjemme som borte"
		see = (
			f"Hjemmekampene solgte i snitt {_nb_number(home_average, 0)} "
			f"billetter, mot {_nb_number(away_average, 0)} på bortekamp. "
			f"Tallene dekker {home['matchCount']} hjemmekamper og "
			f"{away['matchCount']} bortekamper."
		)
	elif away_average > home_average:
		title = "Bortekamper selger flere billetter"
		if home_average == 0:
			value = _nb_number(away_average, 0)
			unit = "billetter i snitt borte, mot 0 hjemme"
		else:
			value = f"{_nb_number(away_average / home_average, 2)}×"
			unit = "så mange billetter borte som hjemme"
		see = (
			f"Bortekampene solgte i snitt {_nb_number(away_average, 0)} "
			f"billetter, mot {_nb_number(home_average, 0)} på hjemmekamp. "
			f"Tallene dekker {away['matchCount']} bortekamper og "
			f"{home['matchCount']} hjemmekamper."
		)
	else:
		title = "Billettsalget er likt hjemme og borte"
		value = _nb_number(home_average, 0)
		unit = "billetter i snitt"
		see = (
			f"Både hjemme- og bortekampene solgte i snitt "
			f"{_nb_number(home_average, 0)} billetter. Tallene dekker "
			f"{home['matchCount']} hjemmekamper og {away['matchCount']} bortekamper."
		)

	return {
		"id": "home-advantage",
		"view": "matches",
		"title": title,
		"value": value,
		"unit": unit,
		"tabs": {
			"see": see,
			"missing": (
				"Dette er solgte billetter, ikke oppmøte. Det finnes ingen data på hvem "
				"som faktisk kom. Billettdataene er dessuten laget for demonstrasjon."
			),
			"decision": (
				"Tallet omtales alltid som solgte billetter, aldri som publikumstall."
			),
		},
	}


def build_overview(
	matches_view: dict,
	supporters_view: dict,
	ml_view: dict,
) -> dict:
	home = matches_view["homeAway"]["home"]
	away = matches_view["homeAway"]["away"]
	ratio = matches_view["homeAway"]["ratio"]
	coverage = matches_view["weatherCoverage"]
	consent = supporters_view["consent"]

	findings = [
		_home_away_finding(home, away, ratio),
		{
			"id": "weather-coverage",
			"view": "matches",
			"title": "Værdata mangler for de fleste kampene",
			"value": f"{coverage['withTemperature']}/{coverage['matchCount']}",
			"unit": "kamper har temperatur",
			"tabs": {
				"see": (
					f"{coverage['withTemperature']} av {coverage['matchCount']} kamper har "
					f"temperatur. Bare {coverage['withCompleteWeather']} har både "
					"temperatur, nedbør og vind."
				),
				"missing": (
					"Værtjenesten dekker norske målestasjoner. For bortekamper i "
					"Europa finnes ingen stasjon nær nok, og feltene står tomme."
				),
				"decision": (
					"Kamper uten måling står uten vær, i stedet for et gjettet tall. "
					"Ingen konklusjon trekkes om hvordan været påvirker salget."
				),
			},
		},
		{
			"id": "activation-funnel",
			"view": "supporters",
			"title": "Halvparten av supporterne kan klubben kontakte",
			"value": _nb_number(supporters_view["activatable"], 0),
			"unit": f"av {_nb_number(supporters_view['totalFans'], 0)} kan kontaktes",
			"tabs": {
				"see": (
					f"Av {_nb_number(supporters_view['totalFans'], 0)} supportere har "
					f"{_nb_number(consent['granted'], 0)} sagt ja til å bli kontaktet. "
					f"{_nb_number(supporters_view['activatable'], 0)} av dem har også "
					"oppgitt en e-postadresse."
				),
				"missing": (
					f"{consent['declined']} har sagt nei. For {consent['unknown']} er "
					"det ukjent, fordi de ikke er koblet til billettsystemet. Det er en "
					"mangel i dataene, ikke et nei fra dem."
				),
				"decision": (
					"En supporter må ha sagt ja og ha en e-postadresse. Hvor aktiv "
					"vedkommende er, spiller ingen rolle."
				),
			},
		},
		{
			"id": "ml-marginal",
			"view": "ml",
			"title": "Maskinlæringen ga for liten gevinst",
			"value": _nb_number(ml_view["selection"]["silhouetteDelta"], 5),
			"unit": "poeng bedre enn den enkleste inndelingen",
			"tabs": {
				"see": (
					f"Modellen delte supporterne i "
					f"{ml_view['selection']['selectedK']} grupper, men ble bare "
					f"{_nb_number(ml_view['selection']['silhouetteDelta'], 5)} poeng bedre "
					f"enn med {ml_view['selection']['baselineK']} grupper."
				),
				"missing": (
					"Dataene er laget for demonstrasjon, og ingen har vist at inndelingen "
					"faktisk ville hjulpet klubben."
				),
				"decision": (
					"Forsøket er dokumentert, men modellen er ikke tatt i bruk."
				),
			},
		},
	]

	return {
		"questions": [
			{
				"id": "demand",
				"view": "matches",
				"title": "Kamper og billettsalg",
				"summary": (
					f"Alle {matches_view['totals']['matchCount']} kampene med resultat, "
					"antall solgte billetter og været ved avspark."
				),
			},
			{
				"id": "activation",
				"view": "supporters",
				"title": "Supportere og samtykke",
				"summary": (
					f"{_nb_number(supporters_view['totalFans'], 0)} supportere fordelt "
					"etter hvor mye de kjøper, og hvem klubben har lov til å kontakte."
				),
			},
			{
				"id": "segmentation",
				"view": "ml",
				"title": "Forsøk med maskinlæring",
				"summary": (
					"Kan en modell finne grupper i supporterdataene som de vanlige "
					"reglene ikke fanger opp?"
				),
			},
		],
		"findings": findings,
	}


def build_portfolio(
	matches: pd.DataFrame,
	match_ticket_sales: pd.DataFrame,
	fan_segment_summary: pd.DataFrame,
	ml_snapshot: dict,
) -> dict:
	matches_view = build_matches_view(matches, match_ticket_sales)
	supporters_view = build_supporters_view(fan_segment_summary)
	ml_view = build_ml_view(ml_snapshot)
	payload = {
		"metadata": {
			"schemaVersion": SCHEMA_VERSION,
			"club": CLUB_DISPLAY_NAME,
			"title": "Klubbdata",
			"ticketsProxyNote": (
				"Tallene viser solgte billetter, ikke hvor mange som møtte opp. "
				"Det finnes ingen data fra innslippet."
			),
			"syntheticNote": (
				"Kamp-, stadion- og værdata er ekte. Supporterdataene er laget for "
				"demonstrasjon."
			),
			"sources": {
				"matches": {
					"dataset": "data/gold/match_insights.parquet",
					"grain": "Én rad per kamp",
					"dataThroughAt": matches_view["totals"]["lastDate"],
				},
				"tickets": {
					"dataset": "data/gold/match_ticket_sales.parquet",
					"grain": "Én rad per kamp",
				},
				"supporters": {
					"dataset": "data/gold/fan_segment_summary.parquet",
					"grain": "Én rad per engasjementssegment",
					"asOfAt": supporters_view["asOfAt"],
					"windowStartAt": supporters_view["windowStartAt"],
				},
				"ml": {
					"dataset": "data/ml/fan_segmentation_summary.json",
					"grain": "Aggregerte målinger per kandidat og per ML-segment",
					"snapshotAt": ml_view["snapshotAt"],
				},
			},
		},
		"overview": build_overview(matches_view, supporters_view, ml_view),
		"matches": matches_view,
		"supporters": supporters_view,
		"ml": ml_view,
	}
	assert_publishable(payload)
	return payload


def assert_publishable(payload) -> None:
	def walk(node, path):
		if isinstance(node, dict):
			for key, value in node.items():
				if key in FORBIDDEN_OUTPUT_KEYS:
					raise PortfolioBuildError(
						f"Publishable payload must not contain field {key!r} at {path}."
					)
				walk(value, f"{path}.{key}")
		elif isinstance(node, list):
			for index, value in enumerate(node):
				walk(value, f"{path}[{index}]")
		elif isinstance(node, str) and EMAIL_PATTERN.search(node):
			raise PortfolioBuildError(
				f"Publishable payload must not contain an email address at {path}."
			)

	walk(payload, "$")


def serialize(payload: dict) -> str:
	return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def write_portfolio(payload: dict, path: Path = PORTFOLIO_OUTPUT) -> Path:
	path = Path(path)
	path.parent.mkdir(parents=True, exist_ok=True)
	try:
		path.write_text(serialize(payload), encoding="utf-8")
	except OSError as error:
		raise PortfolioBuildError(f"Could not write {path}: {error}") from error
	return path


def check_portfolio(payload: dict, path: Path = PORTFOLIO_OUTPUT) -> bool:
	path = Path(path)
	if not path.exists():
		return False
	return path.read_text(encoding="utf-8") == serialize(payload)


def _argument_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Build the aggregated, PII-free portfolio dataset for GitHub Pages."
	)
	parser.add_argument("--output", type=Path, default=PORTFOLIO_OUTPUT)
	parser.add_argument(
		"--check",
		action="store_true",
		help="Fail instead of writing when the committed file is out of date.",
	)
	return parser


def main(argv=None) -> int:
	arguments = _argument_parser().parse_args(argv)
	try:
		matches, match_ticket_sales, fan_segment_summary = load_inputs()
		ml_snapshot = load_snapshots()
		payload = build_portfolio(
			matches,
			match_ticket_sales,
			fan_segment_summary,
			ml_snapshot,
		)
		if arguments.check:
			if not check_portfolio(payload, arguments.output):
				print(
					f"Error: {arguments.output} is out of date. "
					"Run python3 -m src.export_portfolio_data.",
					file=sys.stderr,
				)
				return 1
			print(f"Up to date: {arguments.output}")
			return 0
		path = write_portfolio(payload, arguments.output)
	except PortfolioBuildError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	print(f"Matches: {payload['matches']['totals']['matchCount']}")
	print(f"Fans: {payload['supporters']['totalFans']}")
	print(f"ML segments: {len(payload['ml']['profiles'])}")
	print(f"Written: {path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
