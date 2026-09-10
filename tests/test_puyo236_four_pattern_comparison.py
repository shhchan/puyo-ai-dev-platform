"""Protocol failures must stay failures; no quality threshold tuning here."""

import copy
import json
import math
import unittest
from dataclasses import replace
from pathlib import Path

from agents.deep_chain_native import (
    REQUEST_SCHEMA_IDENTITIES_TAG,
    decode_envelope,
    decode_request,
)
from agents.deep_chain_search_backend import semantic_sha256
from eval import puyo236_four_pattern_trial as trial
from eval.puyo236_fixture_migration import migrate_frozen_request
from eval.puyo236_run_comparison import balanced_counts, schedule
from eval.puyo236_summarize import quality


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

    def test_quality_count_and_clean_success_use_the_supplied_unique_seed_denominator(self):
        runs = [{"maximum_actual_fire_chain_count": c, "premature_fire_count": p,
                 "game_over": g, "actual_fire_chain_counts": fires}
                for c, p, g, fires in ((10, 0, False, [10]), (12, 1, False, [2, 12]), (0, 0, True, []))]
        result = quality(runs)
        self.assertEqual(result["unique_seed_count"], 3)
        self.assertEqual(result["target10_seed_count"], 2)
        self.assertEqual(result["clean_target10_seed_count"], 1)
        self.assertAlmostEqual(result["target10_rate_percent"], 200 / 3)
        self.assertEqual(result["maximum_actual_chain_distribution"], {0: 1, 10: 1, 12: 1})

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

    def test_native_fixture_adapter_keeps_search_and_evaluator_file_hashes_separate(self):
        raw = bytes.fromhex(Path("tests/fixtures/evaluator_candidate_alias_request.hex").read_text())
        request = decode_request(raw)
        # This historical fixture declares an older search YAML; it must be rejected.
        with self.assertRaises(AssertionError):
            trial.request_receipt_context(request)
        request = replace(request, config_digest=trial.baseline.file_sha256(Path("train/config/deep_chain_builder.yaml")),
                          evaluator_config=trial.load_chain_structure_config())
        context = trial.request_receipt_context(request)
        self.assertEqual(context["evaluator_yaml_sha256"], trial.baseline.file_sha256(Path("train/config/v1_7_chain_structure.yaml")))
        self.assertNotEqual(context["evaluator_yaml_sha256"], request.config_digest)
        self.assertNotEqual(context["evaluator_yaml_sha256"], context["evaluator_semantic_sha256"])
        backend = trial.LongHorizonBackendRequest(
            root_state=request.state, known_pairs=request.known_pairs, search_config=request.search_config,
            evaluator_config=request.evaluator_config, profile_name="reference", profile_version="1.0",
            search_config_version="1.0", search_config_sha256=request.config_digest,
            evaluator_config_version="1.0", evaluator_config_sha256=context["evaluator_yaml_sha256"],
            backend_config_version="1.0", backend_config_sha256=trial.baseline.file_sha256(Path("train/config/deep_chain_backend.yaml")),
            request_id=request.request_id, canonical=True, allow_auto_fallback=False,
        )
        self.assertEqual(trial.request_receipt_context(backend), context)
        with self.assertRaises(TypeError):
            trial.request_receipt_context(object())

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
