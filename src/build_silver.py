import hashlib
import json
import math
import sys
from pathlib import Path

import pandas as pd

if __package__:
	from . import fetch_weather, geocode_venues
else:
	import fetch_weather
	import geocode_venues


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SILVER_DIR = PROJECT_ROOT / "data" / "silver"
MATCHES_OUTPUT = SILVER_DIR / "matches.parquet"
VENUES_OUTPUT = SILVER_DIR / "venues.parquet"
WEATHER_OUTPUT = SILVER_DIR / "weather_observations.parquet"

MATCH_COLUMNS = [
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
	"venue_name",
	"venue_location_raw",
	"attendance",
	"source",
]
VENUE_COLUMNS = [
	"venue_id",
	"stadium_name",
	"stadium_location_raw",
	"latitude",
	"longitude",
	"geocoding_provider",
	"geocoding_query",
	"geocoding_display_name",
	"geocoding_confidence",
	"country",
]
WEATHER_COLUMNS = [
	"venue_id",
	"weather_station_id",
	"weather_station_name",
	"observed_at",
	"element",
	"value",
	"unit",
	"station_latitude",
	"station_longitude",
	"distance_to_venue_km",
	"source",
]
WEATHER_SERIES_KEY = [
	"venue_id",
	"weather_station_id",
	"observed_at",
	"element",
]


class SilverBuildError(RuntimeError):
	pass


def nested_value(record: dict, *path):
	value = record
	for key in path:
		if not isinstance(value, dict):
			return None
		value = value.get(key)
	return value


def raw_venue_identity(stadium_name: str, stadium_location: str) -> str:
	return json.dumps(
		{
			"stadium_location": geocode_venues.normalize_text(stadium_location).casefold(),
			"stadium_name": geocode_venues.normalize_text(stadium_name).casefold(),
		},
		ensure_ascii=True,
		sort_keys=True,
		separators=(",", ":"),
	)


def geocoded_venue_identity(geocode_result: dict):
	osm_type = geocode_result.get("osm_type") if isinstance(geocode_result, dict) else None
	osm_id = geocode_result.get("osm_id") if isinstance(geocode_result, dict) else None
	if isinstance(osm_type, str) and isinstance(osm_id, (str, int)):
		return f"nominatim:osm:{osm_type.casefold()}:{osm_id}"
	try:
		latitude = float(geocode_result["lat"])
		longitude = float(geocode_result["lon"])
	except (KeyError, TypeError, ValueError):
		return None
	if not math.isfinite(latitude) or not math.isfinite(longitude):
		return None
	return f"nominatim:coordinates:{latitude:.6f}:{longitude:.6f}"


def stable_venue_id(
	stadium_name: str,
	stadium_location: str,
	geocode_result: dict = None,
) -> str:
	identity = geocoded_venue_identity(geocode_result) or raw_venue_identity(
		stadium_name, stadium_location
	)
	digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16].upper()
	return f"VENUE-{digest}"


def populated_leaf_count(value) -> int:
	if isinstance(value, dict):
		return sum(populated_leaf_count(item) for item in value.values())
	if isinstance(value, list):
		return sum(populated_leaf_count(item) for item in value)
	return int(value is not None and value != "")


def logical_fixture_key(match: dict):
	return (
		match.get("date_unix"),
		nested_value(match, "league", "competition_name"),
		nested_value(match, "season", "year"),
		nested_value(match, "home_team", "team_id"),
		nested_value(match, "away_team", "team_id"),
	)


def deduplicate_matches(matches: list) -> list:
	fixtures = {}
	for source_order, match in enumerate(matches):
		key = logical_fixture_key(match)
		match_id = match.get("match_id")
		rank = (
			populated_leaf_count(match),
			int(match.get("status") == "complete"),
			match_id if isinstance(match_id, int) else -1,
			-source_order,
		)
		current = fixtures.get(key)
		if current is None or rank > current[0]:
			fixtures[key] = (rank, match)
	return [item[1] for item in fixtures.values()]


def build_matches(
	matches: list,
	geocoding_dir: Path = geocode_venues.OUTPUT_DIR,
) -> pd.DataFrame:
	match_ids = [match.get("match_id") for match in matches]
	duplicate_ids = sorted(
		{match_id for match_id in match_ids if match_ids.count(match_id) > 1}
	)
	if duplicate_ids:
		raise SilverBuildError(
			f"Silver matches validation failed: duplicate match_id values {duplicate_ids}."
		)
	venue_ids_by_match = {
		id(resolution["match"]): resolution["venue_id"]
		for resolution in venue_resolutions(matches, geocoding_dir)
	}
	rows = []
	for match in deduplicate_matches(matches):
		venue = match.get("venue") if isinstance(match, dict) else None
		venue = venue if isinstance(venue, dict) else {}
		rows.append(
			{
				"match_id": match.get("match_id"),
				"kickoff_at": pd.to_datetime(
					match.get("date_unix"), unit="s", utc=True, errors="coerce"
				),
				"competition": nested_value(match, "league", "competition_name"),
				"season": nested_value(match, "season", "year"),
				"home_team_id": nested_value(match, "home_team", "team_id"),
				"home_team_name": nested_value(match, "home_team", "team_name"),
				"away_team_id": nested_value(match, "away_team", "team_id"),
				"away_team_name": nested_value(match, "away_team", "team_name"),
				"home_score": nested_value(match, "score", "home"),
				"away_score": nested_value(match, "score", "away"),
				"status": match.get("status"),
				"venue_id": venue_ids_by_match.get(id(match)),
				"venue_name": geocode_venues.normalize_text(
					venue.get("stadium_name")
				)
				or None,
				"venue_location_raw": geocode_venues.normalize_text(
					venue.get("stadium_location")
				)
				or None,
				"attendance": match.get("attendance"),
				"source": "FootballData",
			}
		)

	frame = pd.DataFrame(rows, columns=MATCH_COLUMNS)
	for column in (
		"match_id",
		"season",
		"home_team_id",
		"away_team_id",
		"home_score",
		"away_score",
		"attendance",
	):
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
	for column in (
		"competition",
		"home_team_name",
		"away_team_name",
		"status",
		"venue_id",
		"venue_name",
		"venue_location_raw",
		"source",
	):
		frame[column] = frame[column].astype("string")

	validate_matches(frame)
	return frame.sort_values(["kickoff_at", "match_id"], kind="stable").reset_index(
		drop=True
	)


def validate_matches(frame: pd.DataFrame) -> None:
	for column in ("match_id", "kickoff_at", "home_team_name", "away_team_name"):
		if frame[column].isna().any():
			raise SilverBuildError(f"Silver matches validation failed: {column} is null.")
	if frame["match_id"].duplicated().any():
		duplicates = frame.loc[frame["match_id"].duplicated(), "match_id"].tolist()
		raise SilverBuildError(
			f"Silver matches validation failed: duplicate match_id values {duplicates}."
		)


def geocode_candidates(matches: list, geocoding_dir: Path):
	for match in matches:
		if geocode_venues.venue_fields(match) is None:
			continue
		path = geocode_venues.geocode_output_path(match, geocoding_dir)
		if not path.exists():
			continue
		try:
			results = json.loads(path.read_bytes())
		except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
			raise SilverBuildError(f"Could not parse geocoding Bronze file {path}.") from error
		if not isinstance(results, list):
			raise SilverBuildError(f"Geocoding Bronze file must contain a list: {path}.")
		yield match, results


def venue_resolutions(matches: list, geocoding_dir: Path) -> list:
	geocodes_by_match = {}
	best_geocode_by_raw_identity = {}
	for match, results in geocode_candidates(matches, geocoding_dir):
		fields = geocode_venues.venue_fields(match)
		candidate = results[0] if results and isinstance(results[0], dict) else None
		geocodes_by_match[id(match)] = candidate
		if candidate is not None:
			best_geocode_by_raw_identity.setdefault(
				raw_venue_identity(*fields), (match, candidate)
			)

	resolutions = []
	for match in matches:
		fields = geocode_venues.venue_fields(match)
		if fields is None:
			continue
		result = geocodes_by_match.get(id(match))
		geocode_match = match
		if result is None:
			inherited = best_geocode_by_raw_identity.get(raw_venue_identity(*fields))
			if inherited is not None:
				geocode_match, result = inherited
		resolutions.append(
			{
				"match": match,
				"geocode_match": geocode_match,
				"fields": fields,
				"geocode_result": result,
				"venue_id": stable_venue_id(*fields, result),
			}
		)
	return resolutions


def representative_venue_rank(resolution: dict):
	stadium_name, stadium_location = resolution["fields"]
	return (
		int(resolution["geocode_result"] is not None),
		int(bool(stadium_location)),
		len(stadium_location),
		-int("(" in stadium_name),
		-len(stadium_name),
		stadium_name.casefold(),
	)


def build_venues(matches: list, geocoding_dir: Path = geocode_venues.OUTPUT_DIR) -> pd.DataFrame:
	canonical_venues = {}
	for resolution in venue_resolutions(matches, geocoding_dir):
		venue_id = resolution["venue_id"]
		current = canonical_venues.get(venue_id)
		if current is None or representative_venue_rank(
			resolution
		) > representative_venue_rank(current):
			canonical_venues[venue_id] = resolution

	rows = []
	for venue_id, resolution in canonical_venues.items():
		stadium_name, stadium_location = resolution["fields"]
		geocode_match = resolution["geocode_match"]
		result = resolution["geocode_result"]
		address = result.get("address") if isinstance(result, dict) else None
		address = address if isinstance(address, dict) else {}
		rows.append(
			{
				"venue_id": venue_id,
				"stadium_name": stadium_name,
				"stadium_location_raw": stadium_location or None,
				"latitude": result.get("lat") if result else None,
				"longitude": result.get("lon") if result else None,
				"geocoding_provider": "Nominatim",
				"geocoding_query": geocode_venues.build_geocode_query(geocode_match),
				"geocoding_display_name": result.get("display_name") if result else None,
				"geocoding_confidence": None,
				"country": address.get("country"),
			}
		)

	frame = pd.DataFrame(rows, columns=VENUE_COLUMNS)
	for column in ("latitude", "longitude", "geocoding_confidence"):
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
	for column in (
		"venue_id",
		"stadium_name",
		"stadium_location_raw",
		"geocoding_provider",
		"geocoding_query",
		"geocoding_display_name",
		"country",
	):
		frame[column] = frame[column].astype("string")

	validate_venues(frame)
	return frame.sort_values("venue_id", kind="stable").reset_index(drop=True)


def validate_venues(frame: pd.DataFrame) -> None:
	if frame["venue_id"].isna().any():
		raise SilverBuildError("Silver venues validation failed: venue_id is null.")
	if frame["venue_id"].duplicated().any():
		raise SilverBuildError("Silver venues validation failed: venue_id is not unique.")
	invalid_latitude = frame["latitude"].notna() & ~frame["latitude"].between(-90, 90)
	invalid_longitude = frame["longitude"].notna() & ~frame["longitude"].between(
		-180, 180
	)
	if invalid_latitude.any():
		raise SilverBuildError("Silver venues validation failed: invalid latitude.")
	if invalid_longitude.any():
		raise SilverBuildError("Silver venues validation failed: invalid longitude.")


def read_bronze_object(path: Path, source_name: str) -> dict:
	try:
		payload = json.loads(path.read_bytes())
	except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
		raise SilverBuildError(f"Could not parse {source_name} Bronze file {path}.") from error
	if not isinstance(payload, dict):
		raise SilverBuildError(f"{source_name} Bronze file must contain an object: {path}.")
	return payload


def haversine_km(
	latitude_a: float,
	longitude_a: float,
	latitude_b: float,
	longitude_b: float,
) -> float:
	earth_radius_km = 6371.0088
	latitude_a_rad = math.radians(latitude_a)
	latitude_b_rad = math.radians(latitude_b)
	latitude_delta = math.radians(latitude_b - latitude_a)
	longitude_delta = math.radians(longitude_b - longitude_a)
	a = (
		math.sin(latitude_delta / 2) ** 2
		+ math.cos(latitude_a_rad)
		* math.cos(latitude_b_rad)
		* math.sin(longitude_delta / 2) ** 2
	)
	return earth_radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def observed_timestamp(reference_time, time_offset, source_path: Path):
	reference = pd.to_datetime(reference_time, utc=True, errors="coerce")
	if pd.isna(reference):
		return pd.NaT
	try:
		offset = pd.Timedelta(time_offset or "PT0H")
	except (TypeError, ValueError) as error:
		raise SilverBuildError(
			f"Unsupported Frost timeOffset {time_offset!r} in {source_path}."
		) from error
	return reference + offset


def weather_link_for_match(
	match: dict,
	venue_id: str,
	geocoding_dir: Path,
	sources_dir: Path,
	observations_dir: Path,
):
	fields = geocode_venues.venue_fields(match)
	if fields is None:
		return None
	try:
		kickoff = fetch_weather.match_kickoff(match)
	except ValueError:
		return None
	coordinates, geocode_status = fetch_weather.read_geocode(match, geocoding_dir)
	if geocode_status != "available":
		return None

	latitude, longitude = coordinates
	source_params = fetch_weather.source_params(latitude, longitude, kickoff)
	source_path = fetch_weather.source_output_path(
		match, kickoff, source_params, sources_dir
	)
	if not source_path.exists():
		return None
	source_payload = read_bronze_object(source_path, "Frost source")
	source_data = source_payload.get("data")
	if not isinstance(source_data, list):
		raise SilverBuildError(f"Frost source Bronze file has no data list: {source_path}.")
	station_id, station_status = fetch_weather.select_station(source_data)
	if station_status != "available":
		return None

	observation_params = fetch_weather.observation_params(station_id, kickoff)
	observation_path = fetch_weather.observation_output_path(
		station_id, kickoff, observation_params, observations_dir
	)
	if not observation_path.exists():
		return None
	return {
		"coordinates": (float(latitude), float(longitude)),
		"observation_path": observation_path,
		"source": source_data[0],
		"venue_id": venue_id,
	}


def weather_resolution_rank(time_resolution) -> int:
	preferred = {"PT1H": 0, "PT30M": 1, "PT10M": 2}
	return preferred.get(time_resolution, 3)


def weather_quality_rank(quality_code) -> int:
	try:
		return int(quality_code)
	except (TypeError, ValueError):
		return 999


def weather_series_rank(observation: dict, source_order: int):
	time_series_id = observation.get("timeSeriesId")
	try:
		time_series_rank = int(time_series_id)
	except (TypeError, ValueError):
		time_series_rank = 999
	return (
		weather_quality_rank(observation.get("qualityCode")),
		weather_resolution_rank(observation.get("timeResolution")),
		time_series_rank,
		source_order,
	)


def build_weather_observations(
	matches: list,
	geocoding_dir: Path = geocode_venues.OUTPUT_DIR,
	sources_dir: Path = fetch_weather.SOURCES_DIR,
	observations_dir: Path = fetch_weather.OBSERVATIONS_DIR,
) -> pd.DataFrame:
	rows = []
	processed_links = set()
	venue_ids_by_match = {
		id(resolution["match"]): resolution["venue_id"]
		for resolution in venue_resolutions(matches, geocoding_dir)
	}
	for match in matches:
		venue_id = venue_ids_by_match.get(id(match))
		if venue_id is None:
			continue
		link = weather_link_for_match(
			match, venue_id, geocoding_dir, sources_dir, observations_dir
		)
		if link is None:
			continue
		link_key = (link["venue_id"], link["observation_path"].resolve())
		if link_key in processed_links:
			continue
		processed_links.add(link_key)

		station = link["source"]
		geometry = station.get("geometry") if isinstance(station, dict) else None
		coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
		if (
			not isinstance(coordinates, list)
			or len(coordinates) < 2
			or not all(isinstance(value, (int, float)) for value in coordinates[:2])
		):
			raise SilverBuildError(
				f"Frost source has invalid station coordinates: {link['observation_path']}."
			)
		station_longitude, station_latitude = coordinates[:2]
		venue_latitude, venue_longitude = link["coordinates"]
		distance = haversine_km(
			venue_latitude,
			venue_longitude,
			station_latitude,
			station_longitude,
		)

		observation_payload = read_bronze_object(
			link["observation_path"], "Frost observation"
		)
		observation_data = observation_payload.get("data")
		if not isinstance(observation_data, list):
			raise SilverBuildError(
				f"Frost observation Bronze file has no data list: {link['observation_path']}."
			)
		canonical_observations = {}
		source_order = 0
		for observation_set in observation_data:
			if not isinstance(observation_set, dict):
				continue
			observations = observation_set.get("observations")
			if not isinstance(observations, list):
				continue
			for observation in observations:
				if not isinstance(observation, dict):
					continue
				row = {
						"venue_id": link["venue_id"],
						"weather_station_id": station.get("id"),
						"weather_station_name": station.get("name"),
						"observed_at": observed_timestamp(
							observation_set.get("referenceTime"),
							observation.get("timeOffset"),
							link["observation_path"],
						),
						"element": observation.get("elementId"),
						"value": observation.get("value"),
						"unit": observation.get("unit"),
						"station_latitude": station_latitude,
						"station_longitude": station_longitude,
						"distance_to_venue_km": distance,
						"source": "Frost",
					}
				key = tuple(row[column] for column in WEATHER_SERIES_KEY)
				rank = weather_series_rank(observation, source_order)
				current = canonical_observations.get(key)
				if current is None or rank < current[0]:
					canonical_observations[key] = (rank, row)
				source_order += 1
		rows.extend(item[1] for item in canonical_observations.values())

	frame = pd.DataFrame(rows, columns=WEATHER_COLUMNS)
	frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True, errors="coerce")
	for column in (
		"value",
		"station_latitude",
		"station_longitude",
		"distance_to_venue_km",
	):
		frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Float64")
	for column in (
		"venue_id",
		"weather_station_id",
		"weather_station_name",
		"element",
		"unit",
		"source",
	):
		frame[column] = frame[column].astype("string")

	validate_weather_observations(frame)
	return frame.sort_values(
		["venue_id", "observed_at", "element"], kind="stable"
	).reset_index(drop=True)


def validate_weather_observations(frame: pd.DataFrame) -> None:
	for column in ("observed_at", "element"):
		if frame[column].isna().any():
			raise SilverBuildError(f"Silver weather validation failed: {column} is null.")
	if frame["value"].isna().any():
		raise SilverBuildError(
			"Silver weather validation failed: value is not numeric."
		)


def write_silver(
	match_frame: pd.DataFrame,
	venue_frame: pd.DataFrame,
	weather_frame: pd.DataFrame,
	output_dir: Path = SILVER_DIR,
) -> dict:
	output_dir.mkdir(parents=True, exist_ok=True)
	paths = {
		"matches": output_dir / MATCHES_OUTPUT.name,
		"venues": output_dir / VENUES_OUTPUT.name,
		"weather": output_dir / WEATHER_OUTPUT.name,
	}
	try:
		match_frame.to_parquet(paths["matches"], index=False, engine="pyarrow")
		venue_frame.to_parquet(paths["venues"], index=False, engine="pyarrow")
		weather_frame.to_parquet(paths["weather"], index=False, engine="pyarrow")
	except (OSError, ImportError, ValueError) as error:
		raise SilverBuildError(f"Could not write Silver Parquet files: {error}") from error
	return paths


def main() -> int:
	print("Building Silver layer...")
	try:
		matches_path = geocode_venues.latest_matches_file()
		matches = geocode_venues.load_matches(matches_path)
		match_frame = build_matches(matches)
		venue_frame = build_venues(matches)
		weather_frame = build_weather_observations(matches)
		paths = write_silver(match_frame, venue_frame, weather_frame)
	except (
		fetch_weather.WeatherIngestionError,
		geocode_venues.GeocodingError,
		SilverBuildError,
	) as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	geocoded_venues = int(venue_frame["latitude"].notna().sum())
	element_count = weather_frame["element"].nunique()
	print(f"\nMatches:\n{len(match_frame)} rows")
	print(f"\nVenues:\n{len(venue_frame)} unique venues\n{geocoded_venues} geocoded")
	print(
		f"\nWeather observations:\n{len(weather_frame)} rows\n"
		f"{element_count} elements"
	)
	print("\nWritten:")
	for path in paths.values():
		try:
			print(path.relative_to(PROJECT_ROOT))
		except ValueError:
			print(path)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
