import importlib
import json
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from agents.chain_structure import load_chain_structure_config
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import (
    NATIVE_MODULE_NAME,
    InvalidNativeInputError,
    NativeDecisionRequest,
    NativeDeepChainBackend,
    NativeResourceExhaustedError,
    decode_request,
)
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.long_horizon_search import (
    LongHorizonSearchConfig,
    run_compact_long_horizon_search,
)
from eval.puyo242_request_migration import migrate_frozen_request
from src.core.constants import PuyoColor

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "eval" / "deep_chain_native_corpus.json"


def _state(payload):
    return CompactSearchState(
        planes=tuple(int(value, 16) for value in payload["planes_hex"]),
        all_clear_bonus_pending=bool(payload["all_clear_bonus_pending"]),
        game_over=bool(payload["game_over"]),
        score=int(payload["score"]),
        last_chain_end_score=int(payload["last_chain_end_score"]),
    )


def _pairs(payload):
    return tuple(tuple(PuyoColor[name] for name in pair) for pair in payload)


try:
    NATIVE_MODULE = importlib.import_module(NATIVE_MODULE_NAME)
except (ImportError, OSError):
    NATIVE_MODULE = None


@unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
class TestDeepChainNativeSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        cls.evaluator_config = load_chain_structure_config()
        cls.backend = NativeDeepChainBackend(NATIVE_MODULE)
        cls.default_state = _state(cls.corpus["cases"][0]["state"])
        cls.default_pairs = _pairs(cls.corpus["search_case"]["known_pairs"])

    def request(
        self,
        *,
        state=None,
        pairs=None,
        config=None,
        execution_mode="oracle-1",
        request_id=1,
    ):
        return NativeDecisionRequest(
            state=state or self.default_state,
            known_pairs=pairs or self.default_pairs,
            search_config=config
            or LongHorizonSearchConfig(
                depth=3,
                width=4,
                scenarios=2,
                minimum_chain_count=6,
                max_expanded_nodes=512,
                decision_seed=123,
                future_sampling_mode="legacy-fixed-six",
            ),
            evaluator_config=self.evaluator_config,
            config_digest=self.corpus["config_sha256"],
            profile_name="native-search-qa",
            profile_version="1.0",
            config_version="puyo-202",
            request_id=request_id,
            execution_mode=execution_mode,
            max_response_bytes=16 * 1024 * 1024,
        )

    def test_safe_quiet_target_and_safety_ranking_match_python(self):
        from tests.test_long_horizon_search import _fire_fixture_simulator

        cases = json.loads(
            Path("tests/fixtures/build_main_fire_cases.json").read_text()
        )["cases"]
        for case in cases:
            simulator = _fire_fixture_simulator(case)
            state = CompactSearchState.from_simulator(simulator)
            pairs = _pairs([case["current_pair"], *case["next_pairs"]])
            config = LongHorizonSearchConfig(
                depth=1,
                width=24,
                scenarios=1,
                minimum_chain_count=case["target_chain_count"],
                max_expanded_nodes=100,
                fire_context=case["fire_context"],
            )
            expected = run_compact_long_horizon_search(state, pairs, config)
            for mode in ("oracle-1", "scenario-6"):
                with self.subTest(case=case["id"], mode=mode):
                    request = self.request(
                        state=state, pairs=pairs, config=config, execution_mode=mode
                    )
                    actual = materialize_native_long_horizon_result(
                        self.backend.decide(request), request
                    )
                    self.assertEqual(actual.ranked_roots, expected.ranked_roots)
                    self.assertEqual(
                        actual.evidence_by_action[case["action"]].fire_class,
                        case["expected_fire_class"],
                    )
                    if case["id"] == "premature-fire":
                        quiet = next(
                            e
                            for e in actual.root_evidence
                            if e.fire_class == "quiet_continuation"
                        )
                        self.assertGreater(
                            quiet.ranking_key,
                            actual.evidence_by_action[case["action"]].ranking_key,
                        )

    def test_short_public_prefix_matches_python_through_completed_suffix(self):
        from tests.test_long_horizon_search import _fire_fixture_simulator

        case = json.loads((ROOT / "tests/fixtures/build_main_fire_cases.json").read_text())["cases"][0]
        state = CompactSearchState.from_simulator(_fire_fixture_simulator(case))
        visible = _pairs([case["current_pair"], *case["next_pairs"]])
        for known in (1, 2, 3):
            for depth in (known, known + 1):
                config = LongHorizonSearchConfig(
                    depth=depth, width=4, scenarios=2, minimum_chain_count=1,
                    max_expanded_nodes=200, decision_seed=123,
                )
                expected = run_compact_long_horizon_search(state, visible[:known], config)
                for mode in ("oracle-1", "scenario-6"):
                    with self.subTest(known=known, depth=depth, mode=mode):
                        request = self.request(state=state, pairs=visible[:known], config=config, execution_mode=mode)
                        actual = materialize_native_long_horizon_result(self.backend.decide(request), request)
                        self.assertEqual(actual.deterministic_digest, expected.deterministic_digest)
                        self.assertEqual(actual.ranked_roots, expected.ranked_roots)
                        for root in actual.root_evidence:
                            if root.best_fire is not None:
                                self.assertEqual(root.best_fire.known_pair_count, known)
                                self.assertEqual(root.best_fire.known_prefix_target,
                                                 root.best_fire.fire_class == "target_fire" and root.best_fire.depth <= known)

    def test_seed146_decision22_materializes_in_both_execution_modes(self):
        request = decode_request(migrate_frozen_request(bytes.fromhex(
            (ROOT / "tests/fixtures/evaluator_candidate_alias_request.hex").read_text()
        ))[0])
        reference = None
        for mode in ("oracle-1", "scenario-6"):
            current = replace(request, execution_mode=mode)
            first = self.backend.decide(current)
            second = self.backend.decide(current)
            self.assertEqual(first.deterministic_digest, second.deterministic_digest)
            actual = materialize_native_long_horizon_result(first, current)
            repeated = materialize_native_long_horizon_result(second, current)
            self.assertEqual(actual.deterministic_digest, repeated.deterministic_digest)
            self.assertEqual(actual.ranked_roots[0].root_action, first.selected_action)
            if reference is None:
                reference = actual
            else:
                self.assertEqual(actual.deterministic_digest, reference.deterministic_digest)
                self.assertEqual(actual.ranked_roots, reference.ranked_roots)

    def test_frozen_corpus_matches_python_oracle_in_both_execution_modes(self):
        search_payload = self.corpus["search_case"]["config"]
        for case in self.corpus["cases"]:
            with self.subTest(case=case["case_id"]):
                state = _state(case["state"])
                pairs = _pairs(case["scenario"]["known_pairs"])
                config = LongHorizonSearchConfig(
                    depth=int(search_payload["depth"]),
                    width=int(search_payload["width"]),
                    scenarios=int(search_payload["scenarios"]),
                    minimum_chain_count=int(search_payload["minimum_chain_count"]),
                    max_expanded_nodes=int(search_payload["max_expanded_nodes"]),
                    decision_seed=int(case["scenario"]["decision_seed"]),
                    future_sampling_mode=str(case["scenario"]["future_sampling_mode"]),
                )
                oracle_request = self.request(
                    state=state,
                    pairs=pairs,
                    config=config,
                    execution_mode="oracle-1",
                )
                parallel_request = self.request(
                    state=state,
                    pairs=pairs,
                    config=config,
                    execution_mode="scenario-6",
                )
                native_oracle = self.backend.decide(oracle_request)
                native_parallel = self.backend.decide(parallel_request)
                materialized = materialize_native_long_horizon_result(
                    native_oracle, oracle_request
                )
                parallel_materialized = materialize_native_long_horizon_result(
                    native_parallel, parallel_request
                )
                python = run_compact_long_horizon_search(state, pairs, config)

                self.assertEqual(native_oracle.counters, native_parallel.counters)
                self.assertEqual(
                    native_oracle.deterministic_digest,
                    native_parallel.deterministic_digest,
                )
                self.assertEqual(
                    native_oracle.ranked_root_actions,
                    native_parallel.ranked_root_actions,
                )
                self.assertEqual(
                    materialized.deterministic_digest,
                    python.deterministic_digest,
                )
                self.assertEqual(
                    parallel_materialized.deterministic_digest,
                    python.deterministic_digest,
                )
                self.assertEqual(
                    materialized.counters.to_dict(), python.counters.to_dict()
                )
                self.assertEqual(materialized.root_diagnostics, python.root_diagnostics)
                self.assertEqual(
                    {
                        action: (node.path, node.state.to_bytes())
                        for action, node in materialized.representatives.items()
                    },
                    {
                        action: (node.path, node.state.to_bytes())
                        for action, node in python.representatives.items()
                    },
                )

    def test_aggregate_ranking_failure_requests_match_all_roots_in_both_modes(self):
        fixture = json.loads(
            (ROOT / "tests/fixtures/aggregate_ranking_requests.json").read_text()
        )
        for case in fixture["cases"]:
            original = decode_request(migrate_frozen_request(bytes.fromhex(case["request_hex"]))[0])
            targets = (6, 8, 10, 12) if case["seed"] == 151 else (case["target"],)
            for target in targets:
                previous = None
                for mode in ("oracle-1", "scenario-6"):
                    with self.subTest(seed=case["seed"], target=target, mode=mode):
                        request = replace(
                            original,
                            search_config=replace(
                                original.search_config, minimum_chain_count=target
                            ),
                            execution_mode=mode,
                        )
                        native = self.backend.decide(request)
                        python = materialize_native_long_horizon_result(native, request)
                        ranking = tuple(e.root_action for e in python.ranked_roots)
                        self.assertEqual(ranking, native.ranked_root_actions)
                        self.assertEqual(ranking[0], native.selected_action)
                        if target == case["target"]:
                            self.assertEqual(list(ranking), case["native_ranking"])
                            self.assertEqual(ranking[0], case["native_selected_action"])
                        evidence = (
                            [e.to_dict() for e in python.ranked_roots],
                            native.deterministic_digest,
                            dict(native.counters),
                        )
                        if previous is not None:
                            self.assertEqual(evidence, previous)
                        previous = evidence
                        # A wrong non-selected rank must still fail strict validation.
                        corrupt = list(native.ranked_root_actions)
                        corrupt[-2:] = reversed(corrupt[-2:])
                        with self.assertRaisesRegex(
                            InvalidNativeInputError, "aggregate ranking differs"
                        ):
                            materialize_native_long_horizon_result(
                                replace(native, ranked_root_actions=tuple(corrupt)),
                                request,
                            )

    def test_parallel_coordinator_reruns_only_budget_crossing_scenario(self):
        config = LongHorizonSearchConfig(
            depth=3,
            width=5,
            scenarios=3,
            minimum_chain_count=6,
            max_expanded_nodes=300,
            decision_seed=5,
            future_sampling_mode="legacy-fixed-six",
        )
        oracle_request = self.request(config=config, execution_mode="oracle-1")
        parallel_request = self.request(config=config, execution_mode="scenario-6")

        oracle = self.backend.decide(oracle_request)
        parallel = self.backend.decide(parallel_request)

        self.assertEqual(parallel.counters["expanded_nodes"], 300)
        self.assertTrue(parallel.budget_exhausted)
        self.assertEqual(parallel.counters, oracle.counters)
        self.assertEqual(parallel.root_evidence, oracle.root_evidence)
        self.assertEqual(parallel.representatives, oracle.representatives)
        self.assertEqual(parallel.diagnostics, oracle.diagnostics)
        self.assertEqual(parallel.telemetry["scenario_reruns"], 1)

    def test_thirty_seed_future_isolation_and_repeatability(self):
        observed = set()
        for seed in range(30):
            config = LongHorizonSearchConfig(
                depth=4,
                width=2,
                scenarios=6,
                minimum_chain_count=6,
                max_expanded_nodes=2_000,
                decision_seed=seed,
                future_sampling_mode="seeded-authoritative",
            )
            request = self.request(
                config=config,
                execution_mode="scenario-6",
                request_id=seed,
            )
            first = self.backend.decide(request)
            second = self.backend.decide(request)
            self.assertEqual(first.deterministic_digest, second.deterministic_digest)
            self.assertEqual(first.counters, second.counters)
            result = materialize_native_long_horizon_result(first, request)
            observed.add(
                tuple(sequence.queue_digest for sequence in result.scenario_sequences)
            )
        self.assertEqual(len(observed), 30)

    def test_tt_toggle_matches_python_and_reports_exact_hits(self):
        common = {
            "depth": 4,
            "width": 8,
            "scenarios": 3,
            "minimum_chain_count": 6,
            "max_expanded_nodes": 2_000,
            "decision_seed": 987,
            "future_sampling_mode": "seeded-authoritative",
        }
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                config = LongHorizonSearchConfig(
                    **common,
                    use_transposition_table=enabled,
                )
                request = self.request(config=config)
                native = materialize_native_long_horizon_result(
                    self.backend.decide(request), request
                )
                python = run_compact_long_horizon_search(
                    self.default_state,
                    self.default_pairs,
                    config,
                )
                self.assertEqual(
                    native.deterministic_digest, python.deterministic_digest
                )
                self.assertEqual(native.counters.to_dict(), python.counters.to_dict())
                if enabled:
                    self.assertGreater(native.counters.transposition_hits, 0)
                else:
                    self.assertEqual(native.counters.transposition_hits, 0)

    def test_real_decision_releases_gil_and_reuses_bounded_worker_pool(self):
        config = LongHorizonSearchConfig(
            depth=12,
            width=128,
            scenarios=6,
            minimum_chain_count=6,
            max_expanded_nodes=200_000,
            decision_seed=321,
            future_sampling_mode="seeded-authoritative",
        )
        request = self.request(config=config, execution_mode="scenario-6")
        stop = threading.Event()
        ready = threading.Event()
        counter = [0]

        def advance():
            ready.set()
            while not stop.is_set():
                counter[0] += 1

        worker = threading.Thread(target=advance)
        worker.start()
        self.assertTrue(ready.wait(timeout=1.0))
        before = counter[0]
        first = self.backend.decide(request)
        after = counter[0]
        second = self.backend.decide(request)
        stop.set()
        worker.join(timeout=1.0)

        self.assertGreater(after, before)
        self.assertFalse(worker.is_alive())
        self.assertEqual(first.deterministic_digest, second.deterministic_digest)
        self.assertEqual(second.telemetry["pool_reuses"], 1)
        self.assertLessEqual(
            second.telemetry["peak_live_nodes"],
            second.telemetry["arena_capacity_nodes"],
        )
        self.assertGreater(second.telemetry["tt_capacity_slots"], 0)

    def test_response_limit_fails_closed_with_typed_resource_error(self):
        request = replace(self.request(), max_response_bytes=128)

        with self.assertRaises(NativeResourceExhaustedError) as captured:
            self.backend.decide(request)

        self.assertTrue(captured.exception.retry_safe)
        self.assertIn("response exceeds", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
