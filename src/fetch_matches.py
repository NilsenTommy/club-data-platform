import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


API_URL = "https://footballdata.io/api/v1/teams/293/matches"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "bronze" / "football"
TIMEOUT_SECONDS = 30


class MatchIngestionError(RuntimeError):
	pass


def fetch_matches(api_key: str, output_dir: Path = OUTPUT_DIR) -> Path:
	try:
		response = requests.get(
			API_URL,
			headers={"Authorization": f"Bearer {api_key}"},
			timeout=TIMEOUT_SECONDS,
		)
	except requests.Timeout as error:
		raise MatchIngestionError(
			f"The football match API timed out after {TIMEOUT_SECONDS} seconds."
		) from error
	except requests.RequestException as error:
		raise MatchIngestionError(
			f"Could not retrieve football matches: {error}"
		) from error

	if response.status_code in (401, 403):
		raise MatchIngestionError(
			"Authentication failed. Check FOOTBALLDATA_API_KEY."
		)

	if response.status_code != 200:
		reason = response.reason or "Unknown error"
		raise MatchIngestionError(
			f"Football match API returned HTTP {response.status_code}: {reason}."
		)

	try:
		payload = response.json()
	except ValueError as error:
		raise MatchIngestionError(
			"Football match API returned HTTP 200 with invalid JSON."
		) from error

	response_data = payload.get("data") if isinstance(payload, dict) else None
	if isinstance(response_data, dict) and response_data.get("matches") == []:
		print("No matches were returned for team 293; saving the raw response.")

	output_path = output_dir / f"matches_{datetime.now(timezone.utc):%Y-%m-%d}.json"

	try:
		output_dir.mkdir(parents=True, exist_ok=True)
		output_path.write_bytes(response.content)
	except OSError as error:
		raise MatchIngestionError(
			f"Could not write Bronze data to {output_path}: {error}"
		) from error

	return output_path


def main() -> int:
	load_dotenv(PROJECT_ROOT / ".env", override=False)
	api_key = os.getenv("FOOTBALLDATA_API_KEY", "").strip()

	if not api_key:
		print(
			"Error: FOOTBALLDATA_API_KEY is not set or is blank.",
			file=sys.stderr,
		)
		return 1

	try:
		output_path = fetch_matches(api_key)
	except MatchIngestionError as error:
		print(f"Error: {error}", file=sys.stderr)
		return 1

	print(f"Saved raw match response to {output_path}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
