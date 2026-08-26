import cProfile
import json
import tempfile
import unittest
from pathlib import Path

from agents.compact_search import CompactSearchState, transition
from eval.deep_chain_native_profile import (
    CORPUS_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    SUMMARY_SCHEMA_VERSION,
    _stable_digest,
    _write_json,
    classify_hotspot,
    derive_performance_budgets,
    summarize_cprofile,
    summarize_samples,
    verify_frozen_corpus,
)
from src.core.constants import PuyoColor


class TestDeepChainNativeProfile(unittest.TestCase):
    def test_hotspot_classification_covers_native_boundary_units(self):
        cases = {
            ("agents/compact_search.py", "transition"): "transition",
            ("agents/chain_structure.py", "extract_components"): "component_extraction",
            ("agents/chain_structure.py", "bounded_quiescence"): "bounded_quiescence",
            ("agents/chain_structure.py", "_score"): "evaluator_score",
            ("agents/long_horizon_search.py", "_new_node"): "candidate_generation",
            ("agents/long_horizon_search.py", "_prune_survivors"): "beam_prune",
            ("agents/long_horizon_search.py", "aggregate_expected_chain_evidence"): "scenario_aggregation",
            ("agents/long_horizon_search.py", "_root_build_diagnostics"): "diagnostics",
            ("agents/long_horizon_search.py", "compact_state_fingerprint"): "digest",
            ("eval/deep_chain_native_profile.py", "_sample_stack"): "profiler_instrumentation",
        }
        for (path, function), expected in cases.items():
            with self.subTest(path=path, function=function):
                self.assertEqual(classify_hotspot(path, function), expected)

    def test_deterministic_profiler_reports_calls_inclusive_exclusive_and_node_cost(self):
        profiler = cProfile.Profile()
        state = CompactSearchState.empty()
        profiler.enable()
        for _ in range(4):
            transition(state, (PuyoColor.RED, PuyoColor.BLUE), 0)
        profiler.disable()

        summary = summarize_cprofile(
            profiler,
            expanded_nodes=4,
            evaluated_nodes=4,
        )

        transition_rows = [
            row for row in summary["functions"] if row["function"].endswith(":transition")
        ]
        self.assertEqual(len(transition_rows), 1)
        row = transition_rows[0]
        self.assertEqual(row["total_calls"], 4)
        self.assertGreater(row["inclusive_seconds"], 0.0)
        self.assertGreater(row["exclusive_seconds"], 0.0)
        self.assertIsNotNone(row["exclusive_per_expanded_node_us"])

    def test_sampling_summary_is_statistical_and_grouped(self):
        samples = [
            (
                "agents/deep_chain_builder.py:1:decide",
                "agents/chain_structure.py:900:bounded_quiescence",
            ),
            (
                "agents/deep_chain_builder.py:1:decide",
                "agents/chain_structure.py:900:bounded_quiescence",
            ),
            (
                "agents/deep_chain_builder.py:1:decide",
                "agents/compact_search.py:547:transition",
            ),
        ]

        result = summarize_samples(samples, interval_seconds=0.01)

        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["groups"][0]["group"], "bounded_quiescence")
        self.assertAlmostEqual(result["groups"][0]["share"], 2.0 / 3.0)

    def test_frozen_corpus_static_contract_and_fast_differential_checks(self):
        corpus = Path("eval/deep_chain_native_corpus.json")
        payload = json.loads(corpus.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], CORPUS_SCHEMA_VERSION)
        self.assertEqual(len(payload["cases"]), 3)
        self.assertIn("expected_action_id", payload["search_case"])
        self.assertEqual(verify_frozen_corpus(corpus, execute_search=False), [])

    def test_corpus_tamper_is_detected_without_executing_search(self):
        source = Path("eval/deep_chain_native_corpus.json")
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["cases"][0]["action_id"] += 1
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "corpus.json"
            _write_json(target, payload)

            issues = verify_frozen_corpus(target, execute_search=False)

        self.assertTrue(any("corpus_digest mismatch" in issue for issue in issues))
        self.assertTrue(any("transition digest mismatch" in issue for issue in issues))

    def test_performance_budgets_are_numeric_and_keep_ten_percent_margin(self):
        groups = [
            {"group": "transition", "exclusive_seconds": 2.0},
            {"group": "bounded_quiescence", "exclusive_seconds": 6.0},
            {"group": "search_control", "exclusive_seconds": 1.0},
            {"group": "aggregation_serialization", "exclusive_seconds": 0.1},
            {"group": "decision_orchestration", "exclusive_seconds": 0.1},
        ]
        profile = {"deterministic_profile": {"groups": groups}}

        result = derive_performance_budgets(
            profile,
            gate_seconds=1.0,
            canonical_max_expanded_nodes=600_000,
        )

        self.assertEqual(result["safety_margin_seconds"], 0.1)
        self.assertAlmostEqual(
            sum(row["decision_budget_seconds"] for row in result["categories"]),
            0.9,
        )
        self.assertTrue(
            all(row["decision_budget_seconds"] > 0.0 for row in result["categories"])
        )

    def test_evidence_schema_constants_are_versioned(self):
        self.assertEqual(SUMMARY_SCHEMA_VERSION, "puyo.deep_chain_native.profile_summary.v1")
        self.assertEqual(MANIFEST_SCHEMA_VERSION, "puyo.deep_chain_native.profile_manifest.v1")
        self.assertEqual(
            len(_stable_digest({"schema": CORPUS_SCHEMA_VERSION}, prefix="test")),
            64,
        )


if __name__ == "__main__":
    unittest.main()
