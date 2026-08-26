import importlib
import unittest

from agents.compact_search import CompactSearchState
from agents.deep_chain_native_transition import (
    NativeCompactTransitionInput,
    encode_native_compact_batch,
)
from eval.deep_chain_native_transition_profile import (
    _PROFILE_RESPONSE,
    COMBINED_TRANSITION_EVALUATOR_BUDGET_MS,
    DEFAULT_OUTPUT_DIR,
    PROFILE_MODES,
    QUIET_TARGET_NS,
    TRANSITION_TARGET_NS,
    NativeCompactProfiler,
    _percentile,
    decode_profile_measurement,
    derive_budget_decision,
    measure_call_count_model,
    parse_args,
    verify_benchmark,
)
from src.core.constants import PuyoColor


class TestDeepChainNativeTransitionProfile(unittest.TestCase):
    def test_defaults_lock_samples_and_follow_up_targets(self):
        args = parse_args(["run"])

        self.assertEqual(args.ticket, "PUYO-205")
        self.assertEqual(args.mixed_samples, 120)
        self.assertEqual(args.outcome_samples, 40)
        self.assertEqual(args.stage_samples, 30)
        self.assertEqual(args.warmup, 5)
        self.assertEqual(TRANSITION_TARGET_NS, 100.0)
        self.assertEqual(QUIET_TARGET_NS, 50.0)
        self.assertEqual(COMBINED_TRANSITION_EVALUATOR_BUDGET_MS, 820.625)

    def test_optimization_ticket_can_use_its_own_evidence_directory(self):
        args = parse_args(["run", "--ticket", "PUYO-206"])

        self.assertEqual(args.ticket, "PUYO-206")
        self.assertIsNone(args.output_dir)

    def test_nearest_rank_percentile_keeps_all_samples(self):
        values = list(range(1, 101))

        self.assertEqual(_percentile(values, 50), 50.0)
        self.assertEqual(_percentile(values, 95), 95.0)

    def test_profile_response_decoder_preserves_counter_and_size_metadata(self):
        payload = _PROFILE_RESPONSE.pack(
            b"PCPS",
            1,
            0,
            PROFILE_MODES["result_minimal_hot"],
            1,
            10,
            2,
            20,
            1_000,
            3_000,
            42,
            0,
            80,
            24,
            104,
            0,
            0,
            0,
        )

        result = decode_profile_measurement(payload)

        self.assertEqual(result["measured_records"], 20)
        self.assertEqual(result["per_record_ns"], 50.0)
        self.assertEqual(result["per_record_cycles"], 150.0)
        self.assertEqual(result["state_bytes"], 80)
        self.assertEqual(result["result_bytes"], 24)
        self.assertEqual(result["cycle_source"], "rdtsc-lfence")

    def test_budget_decision_preserves_outer_gates(self):
        call_count = {
            "planned_native_search": {
                "canonical_transition_call_ceiling": 600_000,
            }
        }
        decomposition = {
            "quiet_stage_p50_cycles_per_record": {
                "inserted_connectivity": 80.0,
            },
            "largest_stage_by_cycles": "inserted_connectivity",
            "quiet_full_p50_cycles_per_record": 160.0,
        }

        result = derive_budget_decision(130.0, call_count, decomposition)

        self.assertEqual(result["locked_constraints"]["end_to_end_p95_ms"], 1_000.0)
        self.assertEqual(result["locked_constraints"]["native_total_p95_ms"], 900.0)
        self.assertEqual(result["decision"]["combined_p95_budget_ms"], 820.625)
        self.assertEqual(
            result["decision"]["transition_p95_target_ns_per_call"], 100.0
        )

    def test_frozen_search_measures_one_transition_per_expanded_node(self):
        result = measure_call_count_model()

        self.assertTrue(result["expected_search_matches"])
        self.assertTrue(result["assumption_600k_is_one_transition_each"])
        self.assertEqual(result["python_search"]["transition_calls"], 396)
        self.assertEqual(
            result["planned_native_search"]["canonical_transition_call_ceiling"],
            600_000,
        )

    @unittest.skipUnless(DEFAULT_OUTPUT_DIR.exists(), "PUYO-205 evidence is not checked in")
    def test_checked_in_profile_evidence_is_integral(self):
        result = verify_benchmark()

        self.assertTrue(result["passed"], result["issues"])


try:
    NATIVE_MODULE = importlib.import_module("_puyo_deep_chain_native")
except (ImportError, OSError):
    NATIVE_MODULE = None


@unittest.skipUnless(
    NATIVE_MODULE is not None
    and callable(getattr(NATIVE_MODULE, "_compact_transition_profile", None)),
    "PUYO-205 release native extension is not installed",
)
class TestNativeCompactProfileBoundary(unittest.TestCase):
    def test_stage_layout_and_result_modes_report_zero_mismatches(self):
        request = encode_native_compact_batch(
            [
                NativeCompactTransitionInput(
                    CompactSearchState.empty(),
                    (PuyoColor.RED, PuyoColor.BLUE),
                    0,
                )
            ]
        )
        profiler = NativeCompactProfiler(NATIVE_MODULE)

        for name in (
            "full_transition",
            "direct_placement",
            "color_plane_extraction",
            "inserted_connectivity",
            "state_result_materialization",
            "layout_three_bit_slices",
            "layout_six_color_planes",
            "layout_column_local",
            "layout_local_metadata_cache",
            "result_full_summary",
            "result_minimal_hot",
            "result_hot_with_metadata",
        ):
            with self.subTest(mode=name):
                result = profiler.measure(request, mode=PROFILE_MODES[name], repeats=3)
                self.assertEqual(result["mismatch_count"], 0)
                self.assertEqual(result["record_count"], 1)
                self.assertGreater(result["cycles"], 0)


if __name__ == "__main__":
    unittest.main()
