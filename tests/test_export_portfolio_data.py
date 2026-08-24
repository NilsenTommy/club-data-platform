import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src import export_portfolio_data as portfolio_exporter


CLUB = portfolio_exporter.CLUB_TEAM_NAME
AS_OF = "2026-08-22T00:00:00Z"
WINDOW_START = "2025-08-22T00:00:00Z"


def make_matches():
	return pd.DataFrame(
		[
			{
				"match_id": 2,
				"kickoff_at": "2026-03-11T18:00:00Z",
				"competition": "UEFA Champions League",
				"season": 20252026,
				"home_team_name": CLUB,
				"away_team_name": "Sporting CP",
				"home_score": 2,
				"away_score": 1,
				"result": "win",
				"stadium_name": "Aspmyra Stadion",
				"country": "Norge",
				"latitude": 67.28,
				"longitude": 14.39,
				"weather_observed_at": "2026-03-11T18:00:00Z",
				"temperature_c": 7.8,
				"precipitation_mm": 0.0,
				"wind_speed_ms": 3.1,
			},
			{
				"match_id": 1,
				"kickoff_at": "2026-03-17T20:00:00Z",
				"competition": "UEFA Champions League",
				"season": 20252026,
				"home_team_name": "Sporting CP",
				"away_team_name": CLUB,
				"home_score": 0,
				"away_score": 0,
				"result": "draw",
				"stadium_name": None,
				"country": None,
				"latitude": None,
				"longitude": None,
				"weather_observed_at": None,
				"temperature_c": None,
				"precipitation_mm": None,
				"wind_speed_ms": None,
			},
		]
	)


def make_match_ticket_sales():
	return pd.DataFrame(
		[
			{
				"match_id": 2,
				"completed_transactions": 2,
				"tickets_sold": 400,
				"gross_sales_nok": 120000.0,
			},
			{
				"match_id": 1,
				"completed_transactions": 1,
				"tickets_sold": 100,
				"gross_sales_nok": 30000.0,
			},
		]
	)


def make_fan_segment_summary():
	return pd.DataFrame(
		[
			{
				"engagement_segment": "INACTIVE",
				"as_of_at": AS_OF,
				"window_start_at": WINDOW_START,
				"fan_count": 1,
				"consent_granted_count": 0,
				"consent_declined_count": 0,
				"consent_unknown_count": 1,
				"activatable_count": 0,
				"matches_purchased_median": 0.0,
				"purchase_transactions_median": 0.0,
				"tickets_purchased_median": 0.0,
				"total_spend_median": 0.0,
			},
			{
				"engagement_segment": "OCCASIONAL",
				"as_of_at": AS_OF,
				"window_start_at": WINDOW_START,
				"fan_count": 1,
				"consent_granted_count": 1,
				"consent_declined_count": 0,
				"consent_unknown_count": 0,
				"activatable_count": 1,
				"matches_purchased_median": 2.0,
				"purchase_transactions_median": 2.0,
				"tickets_purchased_median": 3.0,
				"total_spend_median": 740.0,
			},
			{
				"engagement_segment": "ENGAGED",
				"as_of_at": AS_OF,
				"window_start_at": WINDOW_START,
				"fan_count": 2,
				"consent_granted_count": 1,
				"consent_declined_count": 1,
				"consent_unknown_count": 0,
				"activatable_count": 1,
				"matches_purchased_median": 4.0,
				"purchase_transactions_median": 4.0,
				"tickets_purchased_median": 7.0,
				"total_spend_median": 2335.0,
			},
			{
				"engagement_segment": "HIGHLY_ENGAGED",
				"as_of_at": AS_OF,
				"window_start_at": WINDOW_START,
				"fan_count": 1,
				"consent_granted_count": 1,
				"consent_declined_count": 0,
				"consent_unknown_count": 0,
				"activatable_count": 1,
				"matches_purchased_median": 7.0,
				"purchase_transactions_median": 7.0,
				"tickets_purchased_median": 12.0,
				"total_spend_median": 3815.0,
			},
		]
	)


def make_ml_snapshot():
	return {
		"schemaVersion": portfolio_exporter.SCHEMA_VERSION,
		"experiment": {
			"notebook": "07_ml_fan_segmentation",
			"selectionRunId": None,
			"snapshotAt": "2026-08-22T00:00:00Z",
			"windowStartAt": "2025-08-22T00:00:00Z",
			"rowCount": 10,
			"randomState": 42,
			"nInit": 20,
			"features": ["recency_days", "total_spend_12m"],
			"stabilitySeeds": [7, 19],
		},
		"promotion": {
			"promotedAt": "2026-08-22",
			"source": "local_reproduction",
			"note": "Test",
		},
		"selection": {
			"selectedK": 2,
			"baselineK": 2,
			"selectedSilhouette": 0.64,
			"baselineSilhouette": 0.6388,
			"silhouetteDelta": 0.0012,
			"rule": "Høyeste silhouette vinner.",
			"candidates": [
				{
					"k": 2,
					"valid": True,
					"silhouetteScore": 0.64,
					"inertia": 12.5,
					"minimumClusterSize": 4,
					"minimumClusterShare": 40.0,
					"stabilityAriMean": 1.0,
					"stabilityAriMinimum": 1.0,
					"selected": True,
				}
			],
		},
		"clusterProfiles": [
			{
				"segment": "ML_01",
				"label": "Inaktivt segment",
				"interpretation": "Ingen kjøp.",
				"count": 4,
				"share": 40.0,
				"medians": {"recency_days": 366.0, "total_spend_12m": 0.0},
				"means": {"recency_days": 366.0, "total_spend_12m": 0.0},
			},
			{
				"segment": "ML_02",
				"label": "Aktivt segment",
				"interpretation": "Jevnlige kjøp.",
				"count": 6,
				"share": 60.0,
				"medians": {"recency_days": 40.0, "total_spend_12m": 2500.0},
				"means": {"recency_days": 45.0, "total_spend_12m": 2600.0},
			},
		],
		"ruleComparison": {
			"adjustedRandIndex": 0.3133,
			"ruleSegments": list(portfolio_exporter.SEGMENT_ORDER),
			"crossTab": [
				{
					"segment": "ML_01",
					"counts": {
						"INACTIVE": 4,
						"OCCASIONAL": 0,
						"ENGAGED": 0,
						"HIGHLY_ENGAGED": 0,
					},
				},
				{
					"segment": "ML_02",
					"counts": {
						"INACTIVE": 0,
						"OCCASIONAL": 1,
						"ENGAGED": 3,
						"HIGHLY_ENGAGED": 2,
					},
				},
			],
			"note": "Det finnes ingen fasit her.",
		},
		"guardrails": [
			{"check": "Ingen PII eller samtykke i trening", "passed": True},
			{"check": "Assignment-output har bare tre tillatte kolonner", "passed": True},
		],
		"decision": {
			"verdict": "Eksperiment godkjent – produksjon avvist",
			"reasons": ["Syntetiske data."],
			"notRegistered": ["modellregistrering"],
		},
	}


def build():
	return portfolio_exporter.build_portfolio(
		make_matches(),
		make_match_ticket_sales(),
		make_fan_segment_summary(),
		make_ml_snapshot(),
	)


class MatchAggregationTests(unittest.TestCase):
	def test_counts_only_completed_ticket_sales(self):
		rows = {row["matchId"]: row for row in build()["matches"]["rows"]}

		self.assertEqual(rows[2]["ticketsSold"], 400)
		self.assertEqual(rows[1]["ticketsSold"], 100)

	def test_classifies_home_and_away_from_the_club_team(self):
		home_away = build()["matches"]["homeAway"]

		self.assertEqual(home_away["home"]["matchCount"], 1)
		self.assertEqual(home_away["away"]["matchCount"], 1)
		self.assertEqual(home_away["home"]["averageTickets"], 400.0)
		self.assertEqual(home_away["away"]["averageTickets"], 100.0)
		self.assertEqual(home_away["ratio"], 4.0)

	def test_zero_home_sales_are_a_valid_average(self):
		tickets = make_match_ticket_sales()
		tickets.loc[tickets["match_id"].eq(2), "tickets_sold"] = 0

		matches = portfolio_exporter.build_matches_view(make_matches(), tickets)
		finding = portfolio_exporter.build_overview(
			matches,
			portfolio_exporter.build_supporters_view(make_fan_segment_summary()),
			portfolio_exporter.build_ml_view(make_ml_snapshot()),
		)["findings"][0]

		self.assertEqual(matches["homeAway"]["ratio"], 0.0)
		self.assertEqual(finding["title"], "Bortekamper selger flere billetter")
		self.assertEqual(finding["value"], "100")
		self.assertNotIn("Hjemmekamper selger langt flere", finding["title"])

	def test_all_zero_sales_are_reported_as_equal(self):
		tickets = make_match_ticket_sales()
		tickets["tickets_sold"] = 0

		matches = portfolio_exporter.build_matches_view(make_matches(), tickets)
		finding = portfolio_exporter.build_overview(
			matches,
			portfolio_exporter.build_supporters_view(make_fan_segment_summary()),
			portfolio_exporter.build_ml_view(make_ml_snapshot()),
		)["findings"][0]

		self.assertIsNone(matches["homeAway"]["ratio"])
		self.assertEqual(finding["title"], "Billettsalget er likt hjemme og borte")
		self.assertEqual(finding["value"], "0")

	def test_keeps_missing_weather_null_and_reports_coverage(self):
		matches = build()["matches"]
		rows = {row["matchId"]: row for row in matches["rows"]}

		self.assertIsNone(rows[1]["weather"]["temperatureC"])
		self.assertFalse(rows[1]["coverage"]["hasTemperature"])
		self.assertFalse(rows[1]["coverage"]["hasCoordinates"])
		self.assertTrue(rows[2]["coverage"]["hasCompleteWeather"])
		self.assertEqual(matches["weatherCoverage"]["withTemperature"], 1)
		self.assertEqual(matches["weatherCoverage"]["withCompleteWeather"], 1)

	def test_requires_complete_weather_to_include_wind(self):
		matches = make_matches()
		matches.loc[matches["match_id"].eq(2), "wind_speed_ms"] = None

		view = portfolio_exporter.build_matches_view(matches, make_match_ticket_sales())

		self.assertEqual(view["weatherCoverage"]["withTemperature"], 1)
		self.assertEqual(view["weatherCoverage"]["withCompleteWeather"], 0)

	def test_sorts_rows_by_kickoff(self):
		rows = build()["matches"]["rows"]

		self.assertEqual([row["matchId"] for row in rows], [2, 1])

	def test_rejects_duplicate_match_ids(self):
		matches = pd.concat([make_matches(), make_matches().head(1)], ignore_index=True)

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "unique"):
			portfolio_exporter.build_matches_view(matches, make_match_ticket_sales())

	def test_rejects_matches_without_the_club(self):
		matches = make_matches()
		matches.loc[0, "home_team_name"] = "Rosenborg"

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "club team name"):
			portfolio_exporter.build_matches_view(matches, make_match_ticket_sales())

	def test_rejects_invalid_ticket_totals(self):
		for tickets_sold in (-1, None, 1.5):
			with self.subTest(tickets_sold=tickets_sold):
				sales = make_match_ticket_sales()
				sales["tickets_sold"] = sales["tickets_sold"].astype("object")
				sales.loc[0, "tickets_sold"] = tickets_sold
				with self.assertRaisesRegex(
					portfolio_exporter.PortfolioBuildError, "non-negative integer"
				):
					portfolio_exporter.build_matches_view(make_matches(), sales)


class SupporterAggregationTests(unittest.TestCase):
	def test_reports_three_consent_states(self):
		supporters = build()["supporters"]

		self.assertEqual(supporters["consent"]["granted"], 3)
		self.assertEqual(supporters["consent"]["declined"], 1)
		self.assertEqual(supporters["consent"]["unknown"], 1)
		self.assertEqual(supporters["activatable"], 3)

	def test_reports_every_segment_in_rule_order(self):
		segments = build()["supporters"]["segments"]

		self.assertEqual(
			[segment["segment"] for segment in segments],
			list(portfolio_exporter.SEGMENT_ORDER),
		)
		by_name = {segment["segment"]: segment for segment in segments}
		self.assertEqual(by_name["ENGAGED"]["count"], 2)
		self.assertEqual(by_name["ENGAGED"]["activatable"], 1)
		self.assertEqual(by_name["ENGAGED"]["medians"]["totalSpend"], 2335.0)
		self.assertEqual(by_name["INACTIVE"]["activatable"], 0)

	def test_funnel_never_grows_between_stages(self):
		funnel = build()["supporters"]["funnel"]

		counts = [stage["count"] for stage in funnel]
		self.assertEqual(counts, sorted(counts, reverse=True))

	def test_rejects_duplicate_fan_ids(self):
		fans = pd.concat(
			[make_fan_segment_summary(), make_fan_segment_summary().head(1)],
			ignore_index=True,
		)

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "one row"):
			portfolio_exporter.build_supporters_view(fans)

	def test_rejects_unsupported_segment(self):
		fans = make_fan_segment_summary()
		fans.loc[0, "engagement_segment"] = "SUPERFAN"

		with self.assertRaisesRegex(
			portfolio_exporter.PortfolioBuildError, "engagement_segment"
		):
			portfolio_exporter.build_supporters_view(fans)


class PublishablePayloadTests(unittest.TestCase):
	def test_payload_excludes_person_level_fields(self):
		payload = build()

		serialized = json.dumps(payload, ensure_ascii=False)
		for field in portfolio_exporter.FORBIDDEN_OUTPUT_KEYS:
			with self.subTest(field=field):
				self.assertNotIn(f'"{field}"', serialized)
		self.assertNotIn("@example.com", serialized)

	def test_rejects_payload_with_a_forbidden_key(self):
		payload = build()
		payload["supporters"]["segments"][0]["fanId"] = "FAN-0"

		with self.assertRaisesRegex(
			portfolio_exporter.PortfolioBuildError, "must not contain field"
		):
			portfolio_exporter.assert_publishable(payload)

	def test_rejects_payload_with_an_email_address(self):
		payload = build()
		payload["supporters"]["governance"]["contact"] = "fan@example.com"

		with self.assertRaisesRegex(
			portfolio_exporter.PortfolioBuildError, "email address"
		):
			portfolio_exporter.assert_publishable(payload)

	def test_exposes_only_aggregates_for_supporters(self):
		supporters = build()["supporters"]

		self.assertEqual(
			set(supporters),
			{
				"totalFans",
				"asOfAt",
				"windowStartAt",
				"consent",
				"activatable",
				"activatableShare",
				"funnel",
				"segments",
				"governance",
			},
		)


class SerializationTests(unittest.TestCase):
	def test_two_builds_are_byte_identical(self):
		first = portfolio_exporter.serialize(build())
		second = portfolio_exporter.serialize(build())

		self.assertEqual(first, second)

	def test_serialized_output_ends_with_one_newline(self):
		serialized = portfolio_exporter.serialize(build())

		self.assertTrue(serialized.endswith("}\n"))
		self.assertFalse(serialized.endswith("\n\n"))

	def test_check_detects_stale_and_matching_files(self):
		payload = build()
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "portfolio.json"

			self.assertFalse(portfolio_exporter.check_portfolio(payload, path))

			portfolio_exporter.write_portfolio(payload, path)
			self.assertTrue(portfolio_exporter.check_portfolio(payload, path))

			path.write_text("{}\n", encoding="utf-8")
			self.assertFalse(portfolio_exporter.check_portfolio(payload, path))

	def test_metadata_declares_schema_and_snapshot_boundaries(self):
		metadata = build()["metadata"]

		self.assertEqual(metadata["schemaVersion"], portfolio_exporter.SCHEMA_VERSION)
		self.assertEqual(metadata["sources"]["supporters"]["asOfAt"], AS_OF)
		self.assertEqual(metadata["sources"]["matches"]["dataThroughAt"], "2026-03-17")


class OverviewTests(unittest.TestCase):
	def test_findings_reference_existing_views(self):
		payload = build()

		available = set(payload) - {"metadata", "overview"}
		for finding in payload["overview"]["findings"]:
			with self.subTest(finding=finding["id"]):
				self.assertIn(finding["view"], available)
				self.assertEqual(set(finding["tabs"]), {"see", "missing", "decision"})

	def test_findings_use_computed_values(self):
		payload = build()

		finding = next(
			item for item in payload["overview"]["findings"] if item["id"] == "home-advantage"
		)
		self.assertEqual(finding["value"], "4,00×")

	def test_questions_cover_every_view(self):
		payload = build()

		available = set(payload) - {"metadata", "overview"}
		self.assertEqual(
			{question["view"] for question in payload["overview"]["questions"]},
			available,
		)


class MlViewTests(unittest.TestCase):
	def test_extra_snapshot_fields_are_not_published(self):
		snapshot = make_ml_snapshot()
		for node in (
			snapshot["promotion"],
			snapshot["selection"]["candidates"][0],
			snapshot["clusterProfiles"][0]["medians"],
			snapshot["guardrails"][0],
			snapshot["decision"],
		):
			node["fanIds"] = ["FAN-000001"]

		serialized = json.dumps(
			portfolio_exporter.build_ml_view(snapshot),
			ensure_ascii=False,
		)

		self.assertNotIn("fanIds", serialized)
		self.assertNotIn("FAN-000001", serialized)

	def test_cross_tab_is_expanded_with_labels_and_shares(self):
		view = portfolio_exporter.build_ml_view(make_ml_snapshot())

		row = next(
			item for item in view["ruleComparison"]["crossTab"] if item["segment"] == "ML_02"
		)
		self.assertEqual(row["total"], 6)
		self.assertEqual([cell["ruleSegment"] for cell in row["cells"]], list(portfolio_exporter.SEGMENT_ORDER))
		engaged = next(cell for cell in row["cells"] if cell["ruleSegment"] == "ENGAGED")
		self.assertEqual(engaged["count"], 3)
		self.assertEqual(engaged["share"], 50.0)
		self.assertEqual(engaged["label"], "Engasjert")

	def test_ari_is_never_presented_as_accuracy(self):
		view = portfolio_exporter.build_ml_view(make_ml_snapshot())

		self.assertIn("ingen fasit", view["ruleComparison"]["note"])
		self.assertNotIn("accuracy", view["selection"]["caveat"].lower())

	def test_marginal_delta_is_stated_in_the_caveat(self):
		view = portfolio_exporter.build_ml_view(make_ml_snapshot())

		self.assertIn("0,00120", view["selection"]["caveat"])
		self.assertIn("antakelse", view["selection"]["caveat"])

	def test_profile_counts_must_match_row_count(self):
		snapshot = make_ml_snapshot()
		snapshot["clusterProfiles"][0]["count"] = 3

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "expected 10"):
			portfolio_exporter.build_ml_view(snapshot)

	def test_cross_tab_totals_must_match_profile_counts(self):
		snapshot = make_ml_snapshot()
		snapshot["ruleComparison"]["crossTab"][1]["counts"]["ENGAGED"] = 9

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "cross tab row"):
			portfolio_exporter.build_ml_view(snapshot)

	def test_failed_guardrail_blocks_the_build(self):
		snapshot = make_ml_snapshot()
		snapshot["guardrails"][0]["passed"] = False

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "guardrails"):
			portfolio_exporter.build_ml_view(snapshot)

	def test_mlflow_promotion_requires_run_id(self):
		snapshot = make_ml_snapshot()
		snapshot["promotion"]["source"] = "mlflow_artifact"

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "selectionRunId"):
			portfolio_exporter.build_ml_view(snapshot)

	def test_selected_k_must_match_profile_count(self):
		snapshot = make_ml_snapshot()
		snapshot["selection"]["selectedK"] = 4

		with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "selectedK"):
			portfolio_exporter.build_ml_view(snapshot)


class SnapshotLoadingTests(unittest.TestCase):
	def test_missing_snapshot_is_reported(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "absent.json"
			with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "Missing snapshot"):
				portfolio_exporter._read_snapshot(path, ("schemaVersion",))

	def test_wrong_schema_version_is_rejected(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "snapshot.json"
			path.write_text(json.dumps({"schemaVersion": 99}), encoding="utf-8")
			with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "schemaVersion 99"):
				portfolio_exporter._read_snapshot(path, ("schemaVersion",))

	def test_missing_keys_are_reported(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "snapshot.json"
			path.write_text(json.dumps({"schemaVersion": 1}), encoding="utf-8")
			with self.assertRaisesRegex(portfolio_exporter.PortfolioBuildError, "missing keys"):
				portfolio_exporter._read_snapshot(path, ("schemaVersion", "decision"))

	def test_committed_snapshots_load_and_build(self):
		ml_snapshot = portfolio_exporter.load_snapshots()

		self.assertEqual(
			len(portfolio_exporter.build_ml_view(ml_snapshot)["profiles"]),
			ml_snapshot["selection"]["selectedK"],
		)


if __name__ == "__main__":
	unittest.main()
