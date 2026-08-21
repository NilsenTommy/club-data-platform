import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests
from dotenv import load_dotenv


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATCHES_DIR = PROJECT_ROOT / "data" / "bronze" / "football"
OUTPUT_DIR = PROJECT_ROOT / "data" / "bronze" / "geocoding"
TIMEOUT_SECONDS = 30
REQUEST_INTERVAL_SECONDS = 1
PLACEHOLDER_CONTACTS = ("contact@example.com", "your-email@example.com")


class GeocodingError(RuntimeError):
    pass


def latest_matches_file(matches_dir: Path = MATCHES_DIR) -> Path:
    matches_files = sorted(matches_dir.glob("matches_*.json"))
    if not matches_files:
        raise GeocodingError(f"No Football Bronze files found in {matches_dir}.")
    return matches_files[-1]


def load_matches(input_path: Path) -> list:
    try:
        payload = json.loads(input_path.read_bytes())
    except OSError as error:
        raise GeocodingError(f"Could not read {input_path}: {error}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GeocodingError(f"Football Bronze file is not valid JSON: {input_path}.") from error

    response_data = payload.get("data") if isinstance(payload, dict) else None
    matches = response_data.get("matches") if isinstance(response_data, dict) else None
    if not isinstance(matches, list):
        raise GeocodingError(
            f"Football Bronze file must contain a data.matches list: {input_path}."
        )
    return matches


def normalize_text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def venue_fields(match: dict):
    venue = match.get("venue") if isinstance(match, dict) else None
    if not isinstance(venue, dict):
        return None

    stadium_name = normalize_text(venue.get("stadium_name"))
    if not stadium_name:
        return None

    return stadium_name, normalize_text(venue.get("stadium_location"))


def venue_identity(match: dict) -> str:
    fields = venue_fields(match)
    if fields is None:
        raise GeocodingError("Cannot create a venue identity without a stadium name.")

    stadium_name, stadium_location = fields
    return json.dumps(
        {
            "stadium_location": stadium_location.casefold(),
            "stadium_name": stadium_name.casefold(),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def geocode_identity(match: dict) -> str:
    return json.dumps(
        {
            "query": build_geocode_query(match).casefold(),
            "venue": venue_identity(match),
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def venue_key(match: dict) -> str:
    return hashlib.sha256(geocode_identity(match).encode("utf-8")).hexdigest()[:12]


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.casefold()).strip("-")
    return (slug or "venue")[:48]


def geocode_output_path(match: dict, output_dir: Path = OUTPUT_DIR) -> Path:
    stadium_name, _ = venue_fields(match) or (None, None)
    if stadium_name is None:
        raise GeocodingError("Cannot create a geocode path without a stadium name.")
    return output_dir / f"venue_{slugify(stadium_name)}_{venue_key(match)}.json"


def build_geocode_query(match: dict) -> str:
    fields = venue_fields(match)
    if fields is None:
        raise GeocodingError("Cannot geocode a match without a stadium name.")

    stadium_name, stadium_location = fields
    league = match.get("league")
    country = normalize_text(league.get("country") if isinstance(league, dict) else None)
    location_parts = [
        normalize_text(part) for part in stadium_location.split(",") if normalize_text(part)
    ]
    city_hint = location_parts[-1] if len(location_parts) > 1 else ""

    components = [stadium_name, city_hint]
    if country.casefold() != "europe":
        components.append(country)

    query_parts = []
    seen = set()
    for component in components:
        normalized_component = normalize_text(component)
        component_key = normalized_component.casefold()
        if normalized_component and component_key not in seen:
            query_parts.append(normalized_component)
            seen.add(component_key)
    return ", ".join(query_parts)


def unique_venue_matches(matches: list):
    unique_matches = {}
    skipped_missing_name = 0

    for match in matches:
        if venue_fields(match) is None:
            skipped_missing_name += 1
            continue
        unique_matches.setdefault(geocode_identity(match), match)

    return list(unique_matches.values()), skipped_missing_name


def validate_user_agent(user_agent: str) -> str:
    normalized_user_agent = normalize_text(user_agent)
    lowered_user_agent = normalized_user_agent.casefold()
    if not normalized_user_agent or any(
        placeholder in lowered_user_agent for placeholder in PLACEHOLDER_CONTACTS
    ):
        raise GeocodingError(
            "PLATFORM_USER_AGENT must identify the application with a real contact "
            "email or website."
        )
    return normalized_user_agent


def parse_geocode_response(response, source: str) -> list:
    try:
        results = response.json()
    except ValueError as error:
        raise GeocodingError(f"Nominatim returned invalid JSON for {source}.") from error
    if not isinstance(results, list):
        raise GeocodingError(f"Nominatim returned an unexpected response for {source}.")
    return results


def load_cached_geocode(output_path: Path) -> list:
    try:
        results = json.loads(output_path.read_bytes())
    except OSError as error:
        raise GeocodingError(f"Could not read cached geocode file {output_path}: {error}") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GeocodingError(f"Cached geocode file is invalid JSON: {output_path}.") from error
    if not isinstance(results, list):
        raise GeocodingError(f"Cached geocode file must contain a JSON list: {output_path}.")
    return results


def fetch_geocode(match: dict, user_agent: str, output_path: Path):
    query = build_geocode_query(match)
    try:
        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 5,
                "addressdetails": 1,
                "accept-language": "en",
            },
            headers={"User-Agent": user_agent},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.Timeout as error:
        raise GeocodingError(
            f"Nominatim timed out after {TIMEOUT_SECONDS} seconds for {query}."
        ) from error
    except requests.RequestException as error:
        raise GeocodingError(f"Could not geocode {query}: {error}") from error

    if response.status_code == 403:
        raise GeocodingError(
            "Nominatim returned HTTP 403. Check PLATFORM_USER_AGENT and the usage policy."
        )
    if response.status_code == 429:
        raise GeocodingError("Nominatim returned HTTP 429. Stop and retry the batch later.")
    if response.status_code != 200:
        reason = response.reason or "Unknown error"
        raise GeocodingError(
            f"Nominatim returned HTTP {response.status_code}: {reason}."
        )

    results = parse_geocode_response(response, query)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
    except OSError as error:
        raise GeocodingError(f"Could not write Bronze data to {output_path}: {error}") from error
    return results


def geocode_venues(matches: list, user_agent: str, output_dir: Path = OUTPUT_DIR) -> dict:
    validated_user_agent = validate_user_agent(user_agent)
    venues, skipped_missing_name = unique_venue_matches(matches)
    summary = {
        "matches": len(matches),
        "unique_venues": len(venues),
        "skipped_missing_name": skipped_missing_name,
        "fetched": 0,
        "cached": 0,
        "empty": 0,
    }
    network_requests = 0

    for match in venues:
        output_path = geocode_output_path(match, output_dir)
        if output_path.exists():
            results = load_cached_geocode(output_path)
            summary["cached"] += 1
        else:
            if network_requests:
                time.sleep(REQUEST_INTERVAL_SECONDS)
            results = fetch_geocode(match, validated_user_agent, output_path)
            network_requests += 1
            summary["fetched"] += 1
        if not results:
            summary["empty"] += 1

    return summary


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    user_agent = os.getenv("PLATFORM_USER_AGENT", "")

    try:
        input_path = latest_matches_file()
        matches = load_matches(input_path)
        summary = geocode_venues(matches, user_agent)
    except GeocodingError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(
        "Geocoding complete: "
        f"{summary['matches']} matches, {summary['unique_venues']} unique venues, "
        f"{summary['fetched']} fetched, {summary['cached']} cached, "
        f"{summary['empty']} empty, "
        f"{summary['skipped_missing_name']} matches skipped without a stadium name."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())