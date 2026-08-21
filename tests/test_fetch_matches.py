import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src import fetch_matches


class FetchMatchesTests(unittest.TestCase):
    @staticmethod
    def make_response(
        *,
        status_code=200,
        reason="OK",
        content=b'{"data":{"matches":[{"id":1}]}}',
        payload=None,
    ):
        response = Mock()
        response.status_code = status_code
        response.reason = reason
        response.content = content
        response.json.return_value = (
            {"data": {"matches": [{"id": 1}]}}
            if payload is None
            else payload
        )
        return response

    def test_fetches_and_saves_raw_response(self):
        raw_response = b'{\n  "data": {"matches": [ { "id": 293 } ]}\n}\n'
        response = self.make_response(content=raw_response)

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "bronze" / "football"

            with (
                patch.object(
                    fetch_matches.requests, "get", return_value=response
                ) as request_get,
                patch.object(fetch_matches, "datetime") as datetime_mock,
            ):
                datetime_mock.now.return_value = datetime(2026, 8, 21, 23, 59)
                output_path = fetch_matches.fetch_matches(
                    "test-api-key", output_dir
                )

            self.assertEqual(
                output_path, output_dir / "matches_2026-08-21.json"
            )
            self.assertEqual(output_path.read_bytes(), raw_response)
            request_get.assert_called_once_with(
                fetch_matches.API_URL,
                headers={"Authorization": "Bearer test-api-key"},
                timeout=fetch_matches.TIMEOUT_SECONDS,
            )
            datetime_mock.now.assert_called_once_with(fetch_matches.timezone.utc)

    def test_existing_daily_file_is_overwritten(self):
        response = self.make_response(
            content=b'{"data":{"matches":[{"id":2}]}}'
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory)
            output_path = output_dir / "matches_2026-08-21.json"
            output_path.write_bytes(b"old response")

            with (
                patch.object(fetch_matches.requests, "get", return_value=response),
                patch.object(fetch_matches, "datetime") as datetime_mock,
            ):
                datetime_mock.now.return_value = datetime(2026, 8, 21)
                result = fetch_matches.fetch_matches("test-api-key", output_dir)

            self.assertEqual(result, output_path)
            self.assertEqual(output_path.read_bytes(), response.content)

    def test_empty_matches_are_reported_and_saved(self):
        raw_response = b'{"data":{"matches":[]}}'
        response = self.make_response(
            content=raw_response, payload={"data": {"matches": []}}
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "football"
            standard_output = io.StringIO()

            with (
                patch.object(fetch_matches.requests, "get", return_value=response),
                patch.object(fetch_matches, "datetime") as datetime_mock,
                redirect_stdout(standard_output),
            ):
                datetime_mock.now.return_value = datetime(2026, 8, 21)
                output_path = fetch_matches.fetch_matches(
                    "test-api-key", output_dir
                )

            self.assertIn("No matches were returned", standard_output.getvalue())
            self.assertEqual(output_path.read_bytes(), raw_response)

    def test_authentication_failures_have_clear_error(self):
        for status_code in (401, 403):
            with self.subTest(status_code=status_code):
                response = self.make_response(status_code=status_code)

                with (
                    tempfile.TemporaryDirectory() as temporary_directory,
                    patch.object(
                        fetch_matches.requests, "get", return_value=response
                    ),
                    self.assertRaisesRegex(
                        fetch_matches.MatchIngestionError,
                        "Authentication failed.*FOOTBALLDATA_API_KEY",
                    ),
                ):
                    fetch_matches.fetch_matches(
                        "invalid-api-key", Path(temporary_directory) / "football"
                    )

    def test_other_http_errors_include_status_and_reason(self):
        response = self.make_response(
            status_code=503, reason="Service Unavailable"
        )

        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(fetch_matches.requests, "get", return_value=response),
            self.assertRaisesRegex(
                fetch_matches.MatchIngestionError,
                "HTTP 503: Service Unavailable",
            ),
        ):
            fetch_matches.fetch_matches(
                "test-api-key", Path(temporary_directory) / "football"
            )

    def test_timeout_has_clear_error(self):
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(
                fetch_matches.requests,
                "get",
                side_effect=requests.Timeout("request timed out"),
            ),
            self.assertRaisesRegex(
                fetch_matches.MatchIngestionError,
                "timed out after 30 seconds",
            ),
        ):
            fetch_matches.fetch_matches(
                "test-api-key", Path(temporary_directory) / "football"
            )

    def test_network_failure_has_clear_error(self):
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(
                fetch_matches.requests,
                "get",
                side_effect=requests.RequestException("network unavailable"),
            ),
            self.assertRaisesRegex(
                fetch_matches.MatchIngestionError,
                "Could not retrieve football matches: network unavailable",
            ),
        ):
            fetch_matches.fetch_matches(
                "test-api-key", Path(temporary_directory) / "football"
            )

    def test_invalid_json_is_not_saved(self):
        response = self.make_response(content=b"not json")
        response.json.side_effect = ValueError("invalid json")

        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "football"

            with (
                patch.object(fetch_matches.requests, "get", return_value=response),
                self.assertRaisesRegex(
                    fetch_matches.MatchIngestionError, "HTTP 200 with invalid JSON"
                ),
            ):
                fetch_matches.fetch_matches("test-api-key", output_dir)

            self.assertFalse(output_dir.exists())

    def test_main_rejects_missing_or_blank_api_key(self):
        for environment in ({}, {"FOOTBALLDATA_API_KEY": "   "}):
            with self.subTest(environment=environment):
                standard_error = io.StringIO()

                with (
                    patch.dict(fetch_matches.os.environ, environment, clear=True),
                    patch.object(fetch_matches, "load_dotenv"),
                    patch.object(fetch_matches, "fetch_matches") as ingest,
                    redirect_stderr(standard_error),
                ):
                    exit_code = fetch_matches.main()

                self.assertEqual(exit_code, 1)
                self.assertIn(
                    "FOOTBALLDATA_API_KEY is not set or is blank",
                    standard_error.getvalue(),
                )
                ingest.assert_not_called()

    def test_main_loads_dotenv_and_runs_ingestion(self):
        expected_path = Path("matches_2026-08-21.json")
        standard_output = io.StringIO()

        with (
            patch.dict(
                fetch_matches.os.environ,
                {"FOOTBALLDATA_API_KEY": " test-api-key "},
                clear=True,
            ),
            patch.object(fetch_matches, "load_dotenv") as load_dotenv,
            patch.object(
                fetch_matches, "fetch_matches", return_value=expected_path
            ) as ingest,
            redirect_stdout(standard_output),
        ):
            exit_code = fetch_matches.main()

        self.assertEqual(exit_code, 0)
        load_dotenv.assert_called_once_with(
            fetch_matches.PROJECT_ROOT / ".env", override=False
        )
        ingest.assert_called_once_with("test-api-key")
        self.assertIn(str(expected_path), standard_output.getvalue())


if __name__ == "__main__":
    unittest.main()