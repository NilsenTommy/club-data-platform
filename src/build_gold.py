import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = PROJECT_ROOT / "data" / "silver"
GOLD_DIR = PROJECT_ROOT / "data" / "gold"
MATCH_INSIGHTS_OUTPUT = GOLD_DIR / "match_insights.parquet"

FOCUS_TEAM_ID = 293
FINISHED_STATUSES = ("complete", "finished")
RESULT_VALUES = ("win", "draw", "loss")
WEATHER_TOLERANCE = pd.Timedelta(hours=3)
WEATHER_ELEMENT_COLUMNS = {
	"air_temperature": "temperature_c",
	"sum(precipitation_amount PT1H)": "precipitation_mm",
	"wind_speed": "wind_speed_ms",
}

MATCH_INSIGHT_COLUMNS = [
	"match_id",
	"kickoff_at",
	"competition",
	"season",
	"home_team_name",
	"away_team_name",
	"home_score",
	"away_score",
	"result",
	"venue_id",
	"stadium_name",
	"country",
	"latitude",
	"longitude",
	"weather_observed_at",
	"temperature_c",
	"precipitation_mm",
	"wind_speed_ms",
]
SNAPSHOT_COLUMNS = [
	"match_id",
	"weather_observed_at",
	"temperature_c",
	"precipitation_mm",
	"wind_speed_ms",
]

REQUIRED_MATCH_COLUMNS = (
	"match_id",
	"kickoff_at",
	"competition",
	"season",
	"home_team_id",
	"home_team_name",
	"away_team_id",
	"away_team_name",
	"home_score",
	"away_score",
	"status",
	"venue_id",
)
REQUIRED_VENUE_COLUMNS = (
	"venue_id",
	"stadium_name",
	"country",
	"latitude",
	"longitude",
)
REQUIRED_WEATHER_COLUMNS = (
	"venue_id",
	"weather_station_id",
	"observed_at",
	"element",
	"value",
	"distance_to_venue_km",
)


class GoldBuildError(RuntimeError):
	pass


def read_silver_frame(path: Path, required_columns) -> pd.DataFrame:
	if not path.exists():
		raise GoldBuildError(f"Missing Silver input {path}. Run build_silver first.")
	try:
		frame = pd.read_parquet(path, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise GoldBuildError(f"Could not read Silver Parquet file {path}: {error}") from error
	missing = [column for column in required_columns if column not in frame.columns]
	if missing:
		raise GoldBuildError(f"Silver input {path} is missing columns {missing}.")
	return frame


def load_silver_data(silver_dir: Path = SILVER_DIR) -> tuple:
	match_frame = read_silver_frame(silver_dir / "matches.parquet", REQUIRED_MATCH_COLUMNS)
	venue_frame = read_silver_frame(silver_dir / "venues.parquet", REQUIRED_VENUE_COLUMNS)
	weather_frame = read_silver_frame(
		silver_dir / "weather_observations.parquet", REQUIRED_WEATHER_COLUMNS
	)
	return match_frame, venue_frame, weather_frame


def match_result(match, team_id: int = FOCUS_TEAM_ID):
	if pd.isna(match.status) or match.status not in FINISHED_STATUSES:
		return None
	if pd.isna(match.home_score) or pd.isna(match.away_score):
		return None
	if not pd.isna(match.home_team_id) and match.home_team_id == team_id:
		scored, conceded = match.home_score, match.away_score
	elif not pd.isna(match.away_team_id) and match.away_team_id == team_id:
		scored, conceded = match.away_score, match.home_score
	else:
		return None
	if scored > conceded:
		return "win"
	if scored < conceded:
		return "loss"
	return "draw"


def select_weather_snapshots(
	match_frame: pd.DataFrame,
	weather_frame: pd.DataFrame,
	tolerance: pd.Timedelta = WEATHER_TOLERANCE,
) -> pd.DataFrame:
	observations = weather_frame[
		weather_frame["element"].isin(WEATHER_ELEMENT_COLUMNS)
	]
	observations_by_venue = dict(tuple(observations.groupby("venue_id", sort=False)))

	rows = []
	for match in match_frame.itertuples(index=False):
		candidates = observations_by_venue.get(match.venue_id)
		if pd.isna(match.venue_id) or pd.isna(match.kickoff_at) or candidates is None:
			continue
		offset = candidates["observed_at"] - match.kickoff_at
		candidates = candidates.loc[offset.abs() <= tolerance].copy()
		if candidates.empty:
			continue
		offset = candidates["observed_at"] - match.kickoff_at
		candidates["time_distance"] = offset.abs()
		candidates["is_after_kickoff"] = (offset > pd.Timedelta(0)).astype(int)
		candidates = candidates.sort_values(
			[
				"time_distance",
				"is_after_kickoff",
				"observed_at",
				"distance_to_venue_km",
				"weather_station_id",
			],
			kind="stable",
		)
		selected = candidates.iloc[0]
		snapshot = candidates[
			(candidates["observed_at"] == selected["observed_at"])
			& (candidates["weather_station_id"] == selected["weather_station_id"])
		]
		row = dict.fromkeys(WEATHER_ELEMENT_COLUMNS.values())
		row["match_id"] = match.match_id
		row["weather_observed_at"] = selected["observed_at"]
		for measurement in snapshot.itertuples(index=False):
			row[WEATHER_ELEMENT_COLUMNS[measurement.element]] = measurement.value
		rows.append(row)

	frame = pd.DataFrame(rows, columns=SNAPSHOT_COLUMNS)
	frame["match_id"] = pd.to_numeric(frame["match_id"], errors="coerce").astype("Int64")
	frame["weather_observed_at"] = pd.to_datetime(
		frame["weather_observed_at"], utc=True, errors="coerce"
	)
	for column in WEATHER_ELEMENT_COLUMNS.values():
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
	return frame


def build_match_insights(
	match_frame: pd.DataFrame,
	venue_frame: pd.DataFrame,
	weather_frame: pd.DataFrame,
	team_id: int = FOCUS_TEAM_ID,
) -> pd.DataFrame:
	frame = match_frame.copy()
	frame["result"] = [match_result(match, team_id) for match in frame.itertuples(index=False)]
	try:
		frame = frame.merge(
			venue_frame[list(REQUIRED_VENUE_COLUMNS)],
			on="venue_id",
			how="left",
			validate="many_to_one",
		)
		frame = frame.merge(
			select_weather_snapshots(match_frame, weather_frame),
			on="match_id",
			how="left",
			validate="one_to_one",
		)
	except pd.errors.MergeError as error:
		raise GoldBuildError(f"Gold join would duplicate matches: {error}") from error

	frame = frame.reindex(columns=MATCH_INSIGHT_COLUMNS)
	for column in ("match_id", "season", "home_score", "away_score"):
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
	for column in (
		"latitude",
		"longitude",
		"temperature_c",
		"precipitation_mm",
		"wind_speed_ms",
	):
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
	for column in (
		"competition",
		"home_team_name",
		"away_team_name",
		"result",
		"venue_id",
		"stadium_name",
		"country",
	):
		frame[column] = frame[column].astype("string")
	for column in ("kickoff_at", "weather_observed_at"):
		frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")

	frame = frame.sort_values(["kickoff_at", "match_id"], kind="stable").reset_index(drop=True)
	validate_match_insights(frame, match_frame)
	return frame


def validate_match_insights(frame: pd.DataFrame, match_frame: pd.DataFrame = None) -> None:
	for column in ("match_id", "kickoff_at"):
		if frame[column].isna().any():
			raise GoldBuildError(f"Gold match_insights validation failed: {column} is null.")
	if frame["match_id"].duplicated().any():
		duplicates = frame.loc[frame["match_id"].duplicated(), "match_id"].tolist()
		raise GoldBuildError(
			f"Gold match_insights validation failed: duplicate match_id values {duplicates}."
		)
	invalid_results = frame["result"].dropna()
	invalid_results = invalid_results[~invalid_results.isin(RESULT_VALUES)]
	if not invalid_results.empty:
		raise GoldBuildError(
			"Gold match_insights validation failed: unsupported result values "
			f"{sorted(set(invalid_results))}."
		)
	observed = frame["weather_observed_at"].notna()
	if observed.any():
		offset = (frame.loc[observed, "weather_observed_at"] - frame.loc[observed, "kickoff_at"]).abs()
		if (offset > WEATHER_TOLERANCE).any():
			raise GoldBuildError(
				"Gold match_insights validation failed: weather observed more than three "
				"hours from kickoff."
			)
	if match_frame is None:
		return
	if len(frame) != len(match_frame):
		raise GoldBuildError(
			f"Gold match_insights validation failed: {len(frame)} rows built from "
			f"{len(match_frame)} Silver matches."
		)
	if set(frame["match_id"]) != set(match_frame["match_id"]):
		raise GoldBuildError(
			"Gold match_insights validation failed: match_id set differs from Silver matches."
		)


def write_gold(frame: pd.DataFrame, output_dir: Path = GOLD_DIR) -> Path:
	output_dir.mkdir(parents=True, exist_ok=True)
	path = output_dir / MATCH_INSIGHTS_OUTPUT.name
	try:
		frame.to_parquet(path, index=False, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise GoldBuildError(f"Could not write Gold Parquet file: {error}") from error
	return path


def main() -> int:
	print("Building Gold layer...")
	try:
		match_frame, venue_frame, weather_frame = load_silver_data()
		insights = build_match_insights(match_frame, venue_frame, weather_frame)
		path = write_gold(insights)
	except GoldBuildError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	geocoded = int(insights["latitude"].notna().sum())
	with_weather = int(insights["weather_observed_at"].notna().sum())
	print(f"\nMatch insights:\n{len(insights)} rows")
	print(f"{geocoded} with venue coordinates\n{with_weather} with weather at kickoff")
	print(f"\nResults:\n{insights['result'].value_counts(dropna=False).to_string()}")
	print("\nWritten:")
	try:
		print(path.relative_to(PROJECT_ROOT))
	except ValueError:
		print(path)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
