import importlib
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agents.chain_structure import load_chain_structure_config
from agents.compact_search import CompactSearchState
from agents.deep_chain_builder import (
    SCENARIO_SEARCH_RESULTS_ARTIFACT,
    DeepChainBuilderConfig,
    DeepChainBuilderPolicy,
    load_deep_chain_builder_config,
)
from agents.deep_chain_native import (
    NATIVE_MODULE_NAME,
    DeepChainNativeError,
    InvalidNativeInputError,
    NativeBackendUnavailableError,
    NativeDeepChainBackend,
)
from agents.deep_chain_search_backend import (
    LONG_HORIZON_BACKEND_CONFIG_SCHEMA_VERSION,
    AutoLongHorizonSearchBackend,
    LongHorizonBackendRequest,
    NativeLongHorizonSearchBackend,
    PythonLongHorizonSearchBackend,
    load_long_horizon_backend_config,
    make_long_horizon_search_backend,
)
from agents.long_horizon_search import LongHorizonSearchConfig
from puyo_env.actions import legal_action_mask
from puyo_env.obs import encode_observation
from puyo_env.realtime_ai import PolicyProcessExecutor
from src.core.constants import PuyoColor
from src.core.headless import HeadlessPuyoSimulator


def _request(
    *,
    canonical: bool = False,
    allow_auto_fallback: bool = False,
) -> LongHorizonBackendRequest:
    return LongHorizonBackendRequest(
        root_state=CompactSearchState(),
        known_pairs=(
            (PuyoColor.RED, PuyoColor.BLUE),
            (PuyoColor.GREEN, PuyoColor.YELLOW),
            (PuyoColor.BLUE, PuyoColor.GREEN),
        ),
        search_config=LongHorizonSearchConfig(
            depth=2,
            width=2,
            scenarios=2,
            minimum_chain_count=6,
            max_expanded_nodes=128,
            decision_seed=203,
            future_sampling_mode="legacy-fixed-six",
        ),
        evaluator_config=load_chain_structure_config(),
        profile_name="smoke",
        profile_version="1.0",
        search_config_version="test-v1",
        search_config_sha256="1" * 64,
        evaluator_config_version="test-v1",
        evaluator_config_sha256="3" * 64,
        backend_config_version="test-v1",
        backend_config_sha256="2" * 64,
        request_id=203,
        canonical=canonical,
        allow_auto_fallback=allow_auto_fallback,
    )


def _small_policy_config() -> DeepChainBuilderConfig:
    payload = load_deep_chain_builder_config().to_dict()
    payload["profiles"]["smoke"].update(
        {
            "depth": 3,
            "width": 4,
            "scenarios": 2,
            "max_expanded_nodes": 512,
        }
    )
    return DeepChainBuilderConfig.from_dict(payload)


def _observation(seed: int = 203):
    simulator = HeadlessPuyoSimulator(seed=seed)
    observation = encode_observation(simulator, step_count=0, max_steps=4)
    info = {
        "action_mask": legal_action_mask(simulator),
        "score": simulator.game.score,
        "step_count": 0,
        "max_steps": 4,
        "last_chain_end_score": simulator.game.last_chain_end_score,
    }
    return observation, info


class _Capabilities:
    def to_dict(self):
        return {
            "build_profile": "release",
            "gil_detach": True,
            "thread_modes": ["oracle-1", "scenario-6"],
        }


class _CountingNativeBoundary:
    def __init__(self, result=None, *, delegate=None):
        self.delegate = delegate
        self.capabilities = (
            delegate.capabilities if delegate is not None else _Capabilities()
        )
        self.result = result
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        if self.delegate is not None:
            return self.delegate.decide(request)
        return self.result


class _FailingNativeSearch:
    def __init__(self, error):
        self.error = error

    def describe(self):
        return {"backend": "native"}

    def search(self, request):
        _ = request
        raise self.error


try:
    importlib.import_module(NATIVE_MODULE_NAME)
    NativeDeepChainBackend(canonical=True)
except (ImportError, OSError, DeepChainNativeError):
    RELEASE_NATIVE_AVAILABLE = False
else:
    RELEASE_NATIVE_AVAILABLE = True


class TestDeepChainSearchBackend(unittest.TestCase):
    def test_versioned_routing_config_keeps_canonical_runs_fail_closed(self):
        config = load_long_horizon_backend_config()

        self.assertEqual(
            config.schema_version,
            LONG_HORIZON_BACKEND_CONFIG_SCHEMA_VERSION,
        )
        self.assertEqual(config.default_backend, "python")
        self.assertEqual(config.canonical_backend, "native")
        self.assertTrue(config.is_canonical_profile("reference"))
        self.assertFalse(config.allows_auto_fallback("reference"))
        self.assertTrue(config.allows_auto_fallback("smoke"))
        self.assertEqual(config.native_execution_mode, "scenario-6")

    def test_native_adapter_crosses_boundary_exactly_once_and_keeps_contract(self):
        request = _request(canonical=True)
        expected = PythonLongHorizonSearchBackend().search(request).result
        native_result = SimpleNamespace(
            telemetry={
                "search_ns": 10,
                "aggregation_ns": 2,
                "serialization_ns": 3,
            },
            provenance={"implementation": "native-test"},
            deterministic_digest="native-wire-digest",
            search_complete=True,
            budget_exhausted=False,
            record_counts={"root_evidence": 44},
        )
        boundary = _CountingNativeBoundary(native_result)
        backend = NativeLongHorizonSearchBackend(
            native_backend=boundary,
            canonical=True,
        )

        with patch(
            "agents.deep_chain_search_backend.materialize_native_long_horizon_result",
            return_value=expected,
        ) as materialize:
            execution = backend.search(request)

        self.assertIs(execution.result, expected)
        self.assertEqual(len(boundary.requests), 1)
        self.assertEqual(boundary.requests[0].execution_mode, "scenario-6")
        materialize.assert_called_once_with(native_result, boundary.requests[0])
        self.assertEqual(execution.diagnostics["backend"], "native")
        self.assertEqual(execution.diagnostics["request_id"], 203)
        self.assertEqual(
            execution.diagnostics["configuration"]["evaluator_config_sha256"],
            "3" * 64,
        )
        self.assertEqual(execution.diagnostics["boundary_call_count"], 1)
        self.assertEqual(execution.diagnostics["timing"]["native_compute_ns"], 12)
        json.dumps(execution.diagnostics, allow_nan=False)

    def test_auto_fallback_is_smoke_only_and_never_accepts_bad_input(self):
        unavailable = NativeBackendUnavailableError("missing", retry_safe=True)
        backend = AutoLongHorizonSearchBackend(_FailingNativeSearch(unavailable))

        execution = backend.search(_request(allow_auto_fallback=True))

        self.assertEqual(execution.diagnostics["requested_backend"], "auto")
        self.assertEqual(execution.diagnostics["backend"], "python")
        self.assertTrue(execution.diagnostics["fallback"]["used"])

        with self.assertRaises(NativeBackendUnavailableError):
            backend.search(_request(canonical=True, allow_auto_fallback=True))
        invalid = AutoLongHorizonSearchBackend(
            _FailingNativeSearch(InvalidNativeInputError("bad request"))
        )
        with self.assertRaises(InvalidNativeInputError):
            invalid.search(_request(allow_auto_fallback=True))

    def test_factory_requires_native_for_reference_auto_mode(self):
        config = load_long_horizon_backend_config()
        error = NativeBackendUnavailableError("missing", retry_safe=True)

        with patch(
            "agents.deep_chain_search_backend.NativeLongHorizonSearchBackend",
            side_effect=error,
        ):
            with self.assertRaises(NativeBackendUnavailableError):
                make_long_horizon_search_backend(
                    "native",
                    profile_name="smoke",
                    config=config,
                )
            with self.assertRaises(NativeBackendUnavailableError):
                make_long_horizon_search_backend(
                    "auto",
                    profile_name="reference",
                    config=config,
                )
            fallback = make_long_horizon_search_backend(
                "auto",
                profile_name="smoke",
                config=config,
            )

        self.assertIsInstance(fallback, AutoLongHorizonSearchBackend)
        self.assertTrue(fallback.describe()["fallback"]["used"])

    def test_explicit_native_runtime_error_is_not_policy_fallback(self):
        error = NativeBackendUnavailableError("runtime unavailable", retry_safe=True)
        policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=_small_policy_config(),
            backend="native",
            search_backend=_FailingNativeSearch(error),
        )
        observation, info = _observation()

        with self.assertRaises(NativeBackendUnavailableError):
            policy.select_action(observation, info)

        self.assertIsNone(policy.last_context)

    def test_explicit_native_adapter_contract_error_is_not_policy_fallback(self):
        error = RuntimeError("malformed adapter result")
        policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=_small_policy_config(),
            backend="native",
            search_backend=_FailingNativeSearch(error),
        )
        observation, info = _observation()

        with self.assertRaisesRegex(RuntimeError, "malformed adapter result"):
            policy.select_action(observation, info)

        self.assertIsNone(policy.last_context)

    @unittest.skipUnless(
        RELEASE_NATIVE_AVAILABLE,
        "release native extension is not installed",
    )
    def test_fixed_policy_corpus_native_matches_python_and_calls_native_once(self):
        config = _small_policy_config()
        python_policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=config,
            backend="python",
        )
        boundary = _CountingNativeBoundary(
            delegate=NativeDeepChainBackend(canonical=True)
        )
        native_backend = NativeLongHorizonSearchBackend(
            native_backend=boundary,
            canonical=True,
        )
        native_policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=config,
            backend="native",
            search_backend=native_backend,
        )
        fixed_seeds = (203, 204, 205, 206, 207, 208)
        for seed in fixed_seeds:
            with self.subTest(seed=seed):
                observation, info = _observation(seed=seed)
                python_policy.reset()
                native_policy.reset()

                python_action = python_policy.select_action(observation, info)
                native_action = native_policy.select_action(observation, info)
                python_diagnostics = python_policy.tactical_diagnostics
                native_diagnostics = native_policy.tactical_diagnostics

                self.assertEqual(native_action, python_action)
                self.assertEqual(
                    native_diagnostics["search"]["deterministic_digest"],
                    python_diagnostics["search"]["deterministic_digest"],
                )
                self.assertEqual(
                    native_diagnostics["search"]["counters"],
                    python_diagnostics["search"]["counters"],
                )
                self.assertEqual(
                    native_diagnostics["scenario_aggregation"],
                    python_diagnostics["scenario_aggregation"],
                )
                self.assertEqual(
                    native_diagnostics["plan"]["steps"],
                    python_diagnostics["plan"]["steps"],
                )
                self.assertEqual(
                    native_diagnostics["plan"]["plan_id"],
                    python_diagnostics["plan"]["plan_id"],
                )
                self.assertEqual(
                    native_diagnostics["selection_reason"],
                    python_diagnostics["selection_reason"],
                )
                self.assertEqual(
                    [
                        (step["step_id"], step["selection_reason"])
                        for step in native_diagnostics["decision_trace"]["steps"]
                    ],
                    [
                        (step["step_id"], step["selection_reason"])
                        for step in python_diagnostics["decision_trace"]["steps"]
                    ],
                )
                self.assertEqual(native_diagnostics["backend"]["backend"], "native")
                self.assertFalse(native_diagnostics["backend"]["fallback"]["used"])
                self.assertEqual(
                    native_diagnostics["decision_trace"]["backend"]["backend"],
                    "native",
                )
                self.assertEqual(
                    native_diagnostics["selection_evidence"]["backend"]["backend"],
                    "native",
                )
                self.assertEqual(
                    native_diagnostics["plan"]["search_control"]["backend"]["backend"],
                    "native",
                )
                self.assertTrue(
                    native_diagnostics["backend"]["capabilities"]["gil_detach"]
                )

        self.assertEqual(len(boundary.requests), len(fixed_seeds))

    @unittest.skipUnless(
        RELEASE_NATIVE_AVAILABLE,
        "release native extension is not installed",
    )
    def test_native_policy_round_trips_through_spawned_realtime_executor(self):
        policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=_small_policy_config(),
            backend="native",
        )
        observation, info = _observation(seed=204)
        executor = PolicyProcessExecutor(policy, start_method="spawn")
        try:
            future = executor.submit_policy(observation, info)
            action, elapsed, diagnostics = future.result(timeout=10.0)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        self.assertTrue(info["action_mask"][action])
        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(diagnostics["backend"]["backend"], "native")
        self.assertEqual(
            diagnostics["backend"]["execution_mode"],
            "scenario-6",
        )
        self.assertFalse(diagnostics["fallback"]["used"])

    @unittest.skipUnless(
        RELEASE_NATIVE_AVAILABLE,
        "release native extension is not installed",
    )
    def test_thirty_seed_private_future_markers_do_not_change_native_decisions(self):
        config = _small_policy_config()
        left_policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=config,
            backend="native",
        )
        right_policy = DeepChainBuilderPolicy(
            profile="smoke",
            config=config,
            backend="native",
        )
        records = []
        for seed in range(123, 153):
            observation, info = _observation(seed=seed)
            left_observation = dict(observation)
            right_observation = dict(observation)
            left_info = dict(info)
            right_info = dict(info)
            left_observation["private_future_queue"] = "private-left"
            right_observation["private_future_queue"] = "private-right"
            left_info["simulator"] = "private-left"
            right_info["simulator"] = "private-right"
            left_policy.reset()
            right_policy.reset()

            left_action = left_policy.select_action(left_observation, left_info)
            right_action = right_policy.select_action(right_observation, right_info)
            left_diagnostics = left_policy.tactical_diagnostics
            right_diagnostics = right_policy.tactical_diagnostics
            left_search = left_policy.last_context.require(
                SCENARIO_SEARCH_RESULTS_ARTIFACT
            )["result"]
            records.append(
                {
                    "seed": seed,
                    "action_matches": left_action == right_action,
                    "search_digest_matches": (
                        left_diagnostics["search"]["deterministic_digest"]
                        == right_diagnostics["search"]["deterministic_digest"]
                    ),
                    "plan_matches": (
                        left_diagnostics["plan"]["steps"]
                        == right_diagnostics["plan"]["steps"]
                    ),
                    "queue_digests": tuple(
                        sequence.queue_digest
                        for sequence in left_search.scenario_sequences
                    ),
                    "private_marker_absent": "private-"
                    not in json.dumps(left_diagnostics, sort_keys=True),
                }
            )

        self.assertEqual(len(records), 30)
        self.assertTrue(
            all(
                record["action_matches"]
                and record["search_digest_matches"]
                and record["plan_matches"]
                and record["private_marker_absent"]
                for record in records
            )
        )
        self.assertGreater(len({record["queue_digests"] for record in records}), 1)

    @unittest.skipUnless(
        RELEASE_NATIVE_AVAILABLE,
        "release native extension is not installed",
    )
    def test_spawned_native_reference_decision_can_be_cancelled_promptly(self):
        policy = DeepChainBuilderPolicy(profile="reference", backend="native")
        observation, info = _observation(seed=205)
        executor = PolicyProcessExecutor(policy, start_method="spawn")
        future = executor.submit_policy(observation, info)
        started = time.perf_counter()

        executor.shutdown(wait=False, cancel_futures=True)

        self.assertTrue(future.cancelled())
        self.assertLess(time.perf_counter() - started, 2.0)


if __name__ == "__main__":
    unittest.main()
