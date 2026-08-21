import hashlib
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

if __package__:
	from . import geocode_venues
else:
	import geocode_venues


FROST_SOURCES_URL = "https://frost.met.no/sources/v0.jsonld"
FROST_OBSERVATIONS_URL = "https://frost.met.no/observations/v0.jsonld"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
GEOCODING_DIR = PROJECT_ROOT / "data" / "bronze" / "geocoding"
SOURCES_DIR = PROJECT_ROOT / "data" / "bronze" / "weather" / "sources"
OBSERVATIONS_DIR = PROJECT_ROOT / "data" / "bronze" / "weather" / "observations"
TIMEOUT_SECONDS = 30
MAX_STATION_DISTANCE_KM = 50
ELEMENTS = (
	"air_temperature",
	"sum(precipitation_amount PT1H)",
	"wind_speed",
)
QUALITY_CODES = "0,1,2,3,4"
FROST_CLIENT_ID_PLACEHOLDER = "your_client_id_here"


class WeatherIngestionError(RuntimeError):
	pass


def validate_frost_client_id(client_id: str) -> str:
	normalized_client_id = client_id.strip() if isinstance(client_id, str) else ""
	if (
		not normalized_client_id
		or normalized_client_id.casefold() == FROST_CLIENT_ID_PLACEHOLDER
	):
		raise WeatherIngestionError(
			"FROST_CLIENT_ID is not set or still contains the example value."
		)
	return normalized_client_id


def match_kickoff(match: dict) -> datetime:
	date_unix = match.get("date_unix") if isinstance(match, dict) else None
	if isinstance(date_unix, bool) or not isinstance(date_unix, (int, float)):
		raise ValueError("date_unix is missing or invalid")
	try:
		return datetime.fromtimestamp(date_unix, timezone.utc)
	except (OSError, OverflowError, ValueError) as error:
		raise ValueError("date_unix is outside the supported range") from error


def query_hash(params: dict) -> str:
	serialized_params = json.dumps(
		params, ensure_ascii=True, sort_keys=True, separators=(",", ":")
	)
	return hashlib.sha256(serialized_params.encode("utf-8")).hexdigest()[:12]


def read_geocode(match: dict, geocoding_dir: Path = GEOCODING_DIR):
	output_path = geocode_venues.geocode_output_path(match, geocoding_dir)
	if not output_path.exists():
		return None, "missing"

	try:
		results = json.loads(output_path.read_bytes())
	except OSError as error:
		raise WeatherIngestionError(
			f"Could not read geocode file {output_path}: {error}"
		) from error
	except (json.JSONDecodeError, UnicodeDecodeError) as error:
		raise WeatherIngestionError(
			f"Geocode file is not valid JSON: {output_path}."
		) from error

	if not isinstance(results, list):
		raise WeatherIngestionError(
			f"Geocode file must contain a JSON list: {output_path}."
		)
	if not results:
		return None, "empty"

	result = results[0]
	if not isinstance(result, dict):
		return None, "invalid"
	try:
		latitude = float(result["lat"])
		longitude = float(result["lon"])
	except (KeyError, TypeError, ValueError):
		return None, "invalid"
	if (
		not math.isfinite(latitude)
		or not math.isfinite(longitude)
		or not -90 <= latitude <= 90
		or not -180 <= longitude <= 180
	):
		return None, "invalid"
	return (f"{latitude:.4f}", f"{longitude:.4f}"), "available"


def source_params(latitude: str, longitude: str, kickoff: datetime) -> dict:
	return {
		"types": "SensorSystem",
		"geometry": f"nearest(POINT({longitude} {latitude}))",
		"nearestmaxcount": 1,
		"validtime": kickoff.date().isoformat(),
		"elements": ",".join(ELEMENTS),
	}


def source_output_path(
	match: dict,
	kickoff: datetime,
	params: dict,
	sources_dir: Path = SOURCES_DIR,
) -> Path:
	stadium_name, _ = geocode_venues.venue_fields(match) or (None, None)
	if stadium_name is None:
		raise WeatherIngestionError("Cannot create a source path without a stadium name.")
	return sources_dir / (
		f"source_{geocode_venues.slugify(stadium_name)}_"
		f"{kickoff:%Y-%m-%d}_{query_hash(params)}.json"
	)


def observation_params(station_id: str, kickoff: datetime) -> dict:
	start_time = kickoff - timedelta(hours=3)
	end_time = kickoff + timedelta(hours=3, seconds=1)
	return {
		"sources": station_id,
		"referencetime": (
			f"{start_time:%Y-%m-%dT%H:%M:%SZ}/"
			f"{end_time:%Y-%m-%dT%H:%M:%SZ}"
		),
		"elements": ",".join(ELEMENTS),
		"timeoffsets": "default",
		"levels": "default",
		"qualities": QUALITY_CODES,
	}


def observation_output_path(
	station_id: str,
	kickoff: datetime,
	params: dict,
	observations_dir: Path = OBSERVATIONS_DIR,
) -> Path:
	return observations_dir / (
		f"observations_{geocode_venues.slugify(station_id)}_"
		f"{kickoff:%Y-%m-%dT%H%M%SZ}_{query_hash(params)}.json"
	)


def parse_frost_data(response, context: str) -> list:
	try:
		payload = response.json()
	except ValueError as error:
		raise WeatherIngestionError(f"Frost returned invalid JSON for {context}.") from error
	data = payload.get("data") if isinstance(payload, dict) else None
	if not isinstance(data, list):
		raise WeatherIngestionError(
			f"Frost returned an unexpected response for {context}."
		)
	return data


def read_cached_frost_data(output_path: Path, context: str) -> list:
	try:
		payload = json.loads(output_path.read_bytes())
	except OSError as error:
		raise WeatherIngestionError(f"Could not read {output_path}: {error}") from error
	except (json.JSONDecodeError, UnicodeDecodeError) as error:
		raise WeatherIngestionError(f"Cached {context} file is invalid JSON: {output_path}.") from error
	data = payload.get("data") if isinstance(payload, dict) else None
	if not isinstance(data, list):
		raise WeatherIngestionError(
			f"Cached {context} file must contain a data list: {output_path}."
		)
	return data


def write_raw_response(output_path: Path, content: bytes) -> None:
	try:
		output_path.parent.mkdir(parents=True, exist_ok=True)
		output_path.write_bytes(content)
	except OSError as error:
		raise WeatherIngestionError(
			f"Could not write Bronze data to {output_path}: {error}"
		) from error


def frost_get(url: str, params: dict, client_id: str, user_agent: str):
	try:
		return requests.get(
			url,
			params=params,
			headers={"User-Agent": user_agent},
			auth=(client_id, ""),
			timeout=TIMEOUT_SECONDS,
		)
	except requests.Timeout as error:
		raise WeatherIngestionError(
			f"Frost timed out after {TIMEOUT_SECONDS} seconds."
		) from error
	except requests.RequestException as error:
		raise WeatherIngestionError(f"Could not retrieve Frost data: {error}") from error


def validate_frost_status(response, context: str, no_data_statuses=()) -> bool:
	if response.status_code in no_data_statuses:
		return False
	if response.status_code == 401:
		raise WeatherIngestionError(
			"Frost authentication failed. Check FROST_CLIENT_ID."
		)
	if response.status_code == 403:
		raise WeatherIngestionError(
			f"Frost rejected the {context} request with HTTP 403."
		)
	if response.status_code in (429, 503):
		raise WeatherIngestionError(
			f"Frost returned HTTP {response.status_code} for {context}; retry later."
		)
	if response.status_code != 200:
		reason = response.reason or "Unknown error"
		raise WeatherIngestionError(
			f"Frost returned HTTP {response.status_code} for {context}: {reason}."
		)
	return True


def select_station(data: list):
	if not data:
		return None, "missing"
	source = data[0]
	if not isinstance(source, dict):
		return None, "invalid"
	station_id = source.get("id")
	try:
		distance = float(source["distance"])
	except (KeyError, TypeError, ValueError):
		return None, "invalid"
	if not isinstance(station_id, str) or not station_id.strip() or not math.isfinite(distance):
		return None, "invalid"
	if distance > MAX_STATION_DISTANCE_KM:
		return None, "too_far"
	return station_id.strip(), "available"


def observation_status(data: list) -> str:
	if not data:
		return "empty"

	observed_elements = set()
	for item in data:
		if not isinstance(item, dict):
			continue
		observations = item.get("observations")
		if not isinstance(observations, list):
			continue
		for observation in observations:
			if isinstance(observation, dict) and isinstance(
				observation.get("elementId"), str
			):
				observed_elements.add(observation["elementId"])
	return "available" if set(ELEMENTS) <= observed_elements else "partial"


def get_source(
	match: dict,
	kickoff: datetime,
	coordinates,
	client_id: str,
	user_agent: str,
	sources_dir: Path = SOURCES_DIR,
) -> dict:
	latitude, longitude = coordinates
	params = source_params(latitude, longitude, kickoff)
	output_path = source_output_path(match, kickoff, params, sources_dir)

	if output_path.exists():
		data = read_cached_frost_data(output_path, "Frost source")
		cache_state = "cached"
	else:
		response = frost_get(FROST_SOURCES_URL, params, client_id, user_agent)
		if not validate_frost_status(response, "source lookup", (404,)):
			return {"cache": "none", "status": "missing", "station_id": None}
		data = parse_frost_data(response, "source lookup")
		write_raw_response(output_path, response.content)
		cache_state = "fetched"

	station_id, station_status = select_station(data)
	return {
		"cache": cache_state,
		"status": station_status,
		"station_id": station_id,
	}


def get_observations(
	station_id: str,
	kickoff: datetime,
	client_id: str,
	user_agent: str,
	observations_dir: Path = OBSERVATIONS_DIR,
) -> dict:
	params = observation_params(station_id, kickoff)
	output_path = observation_output_path(
		station_id, kickoff, params, observations_dir
	)

	if output_path.exists():
		data = read_cached_frost_data(output_path, "Frost observation")
		return {
			"cache": "cached",
			"status": observation_status(data),
		}

	response = frost_get(FROST_OBSERVATIONS_URL, params, client_id, user_agent)
	if not validate_frost_status(response, "observation lookup", (404, 412)):
		return {"cache": "none", "status": "missing"}
	data = parse_frost_data(response, "observation lookup")
	write_raw_response(output_path, response.content)
	return {
		"cache": "fetched",
		"status": observation_status(data),
	}


def fetch_weather(
	matches: list,
	client_id: str,
	user_agent: str,
	geocoding_dir: Path = GEOCODING_DIR,
	sources_dir: Path = SOURCES_DIR,
	observations_dir: Path = OBSERVATIONS_DIR,
) -> dict:
	validated_client_id = validate_frost_client_id(client_id)
	validated_user_agent = geocode_venues.validate_user_agent(user_agent)
	unique_venues, _ = geocode_venues.unique_venue_matches(matches)
	summary = {
		"matches": len(matches),
		"unique_venues": len(unique_venues),
		"skipped_missing_venue": 0,
		"skipped_invalid_kickoff": 0,
		"geocode_missing": 0,
		"geocode_empty": 0,
		"geocode_invalid": 0,
		"source_fetched": 0,
		"source_cached": 0,
		"source_missing": 0,
		"source_too_far": 0,
		"source_invalid": 0,
		"observations_fetched": 0,
		"observations_cached": 0,
		"observations_missing": 0,
		"observations_empty": 0,
		"observations_partial": 0,
		"observations_available": 0,
	}

	for match in matches:
		if geocode_venues.venue_fields(match) is None:
			summary["skipped_missing_venue"] += 1
			continue
		try:
			kickoff = match_kickoff(match)
		except ValueError:
			summary["skipped_invalid_kickoff"] += 1
			continue

		coordinates, geocode_status = read_geocode(match, geocoding_dir)
		if geocode_status != "available":
			summary[f"geocode_{geocode_status}"] += 1
			continue

		source_result = get_source(
			match,
			kickoff,
			coordinates,
			validated_client_id,
			validated_user_agent,
			sources_dir,
		)
		if source_result["cache"] in ("fetched", "cached"):
			summary[f"source_{source_result['cache']}"] += 1
		if source_result["status"] != "available":
			summary[f"source_{source_result['status']}"] += 1
			continue

		observation_result = get_observations(
			source_result["station_id"],
			kickoff,
			validated_client_id,
			validated_user_agent,
			observations_dir,
		)
		if observation_result["cache"] in ("fetched", "cached"):
			summary[f"observations_{observation_result['cache']}"] += 1
		if observation_result["status"] == "available":
			summary["observations_available"] += 1
		else:
			summary[f"observations_{observation_result['status']}"] += 1

	return summary


def main() -> int:
	load_dotenv(PROJECT_ROOT / ".env", override=False)
	client_id = os.getenv("FROST_CLIENT_ID", "")
	user_agent = os.getenv("PLATFORM_USER_AGENT", "")

	try:
		input_path = geocode_venues.latest_matches_file()
		matches = geocode_venues.load_matches(input_path)
		summary = fetch_weather(matches, client_id, user_agent)
	except (geocode_venues.GeocodingError, WeatherIngestionError) as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	print(
		"Weather ingestion complete: "
		f"{summary['matches']} matches, {summary['unique_venues']} unique venues, "
		f"{summary['observations_fetched']} observations fetched, "
		f"{summary['observations_cached']} cached, "
		f"{summary['observations_available']} available, "
		f"{summary['observations_missing']} without observations, "
		f"{summary['observations_empty']} empty observation responses, "
		f"{summary['observations_partial']} partial observation responses, "
		f"{summary['source_missing']} without a source, "
		f"{summary['source_too_far']} beyond {MAX_STATION_DISTANCE_KM} km, "
		f"{summary['geocode_missing']} missing and "
		f"{summary['geocode_empty']} empty geocodes."
	)
	return 0 if summary["observations_available"] else 1


if __name__ == "__main__":
	raise SystemExit(main())
