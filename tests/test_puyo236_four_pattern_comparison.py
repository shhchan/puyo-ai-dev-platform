"""Protocol failures must stay failures; no quality threshold tuning here."""

import copy
import json
import math
import unittest
from pathlib import Path

from agents.deep_chain_native import REQUEST_SCHEMA_IDENTITIES_TAG, decode_envelope
from agents.deep_chain_search_backend import semantic_sha256
from eval import puyo236_four_pattern_trial as trial
from eval.puyo236_fixture_migration import migrate_frozen_request
from eval.puyo236_run_comparison import balanced_counts, schedule


class TestFourPatternComparison(unittest.TestCase):
    def test_schedule_has_240_unique_runs_and_exactly_balanced_positions(self):
        entries = schedule()
        self.assertEqual(len(entries), 240)
        self.assertEqual(len({(e["arm"], e["seed"], e["repeat"]) for e in entries}), 240)
        self.assertEqual(balanced_counts(entries), {a: {p: 15 for p in range(4)} for a in trial.ARMS})
        for start in range(0, 240, 4):
            group = entries[start:start + 4]
            self.assertEqual({e["arm"] for e in group}, set(trial.ARMS))
            self.assertEqual(len({(e["seed"], e["repeat"]) for e in group}), 1)

    def test_strict_json_preserves_signed_rank_sentinels_and_rejects_nan(self):
        value = {"roots": [(1, -math.inf), (2, math.inf)]}
        self.assertEqual(json.loads(json.dumps(trial.strict_json_value(value), allow_nan=False)),
                         {"roots": [[1, "-Infinity"], [2, "Infinity"]]})
        with self.assertRaisesRegex(ValueError, "NaN"):
            trial.strict_json_value({"rank": [math.nan]})

    def test_fixture_migration_preserves_every_nonidentity_byte_and_is_reversible(self):
        raw = bytes.fromhex(Path("tests/fixtures/evaluator_candidate_alias_request.hex").read_text())
        before = decode_envelope(raw)
        for identity in ("puyo.expected_chain_ranking.v2", "puyo.expected_chain_ranking.v3", "puyo.expected_chain_ranking.v4"):
            migrated, receipt = migrate_frozen_request(raw, identity)
            after = decode_envelope(migrated)
            self.assertEqual(before.request_id, after.request_id)
            self.assertTrue(receipt["only_ranking_identity_changed"])
            for a, b in zip(before.sections, after.sections, strict=True):
                if a.tag != REQUEST_SCHEMA_IDENTITIES_TAG:
                    self.assertEqual(a, b)
            restored, _ = migrate_frozen_request(migrated, receipt["from_identity"])
            self.assertEqual(restored, raw)

    def receipt_fixture(self):
        evaluator = {"fatal_score": -1000}
        manifest = {"effective_evaluator_config": evaluator, "common_configuration": {
            "configuration_sha256": {"train/config/v1_7_chain_structure.yaml": "yaml-sha"}}}
        receipt = {
            "evaluator_yaml_sha256": "yaml-sha", "evaluator_semantic_sha256": semantic_sha256(evaluator),
            "strict_all_root_parity_passed": True, "candidate_representatives_match": True,
            "ranked_root_actions": [3, 2], "search_digest": "search",
            "representatives": {"3": {"path": [3, 7]}},
        }
        record = {"action": 3, "search": {"backend": {"configuration": {"evaluator_config_sha256": "yaml-sha"}},
                                           "deterministic_digest": "search"},
                  "selection": {"candidate_count": 2}, "plan": {"steps": [{"action": 3}, {"action": 7}]},
                  "scenario_accounting": {"failure_count": 0}, "parity": {"passed": True},
                  "actual_result": {"valid": True, "game_over": False}, "fallback": {"used": False}}
        run = {"records": [record], "request_receipts": [receipt], "simulator_parity_mismatch_count": 0,
               "fallback_count": 0, "termination_reason": "turn_limit", "game_over": False}
        return run, manifest

    def test_receipts_distinguish_yaml_from_semantics_and_bind_candidate_plan(self):
        run, manifest = self.receipt_fixture()
        trial.validate_receipts(run, manifest)
        for mutation in ("yaml", "plan", "ranking", "parity", "accounting", "fallback", "gameover"):
            broken = copy.deepcopy(run)
            record, receipt = broken["records"][0], broken["request_receipts"][0]
            if mutation == "yaml":
                receipt["evaluator_yaml_sha256"] = receipt["evaluator_semantic_sha256"]
            elif mutation == "plan":
                record["plan"]["steps"][1]["action"] = 8
            elif mutation == "ranking":
                receipt["ranked_root_actions"] = [2, 3]
            elif mutation == "parity":
                record["parity"]["passed"] = False
            elif mutation == "accounting":
                record["scenario_accounting"]["failure_count"] = 1
            elif mutation == "fallback":
                record["fallback"]["used"] = True
            else:
                broken["termination_reason"] = "game_over"
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                trial.validate_receipts(broken, manifest)


if __name__ == "__main__":
    unittest.main()
