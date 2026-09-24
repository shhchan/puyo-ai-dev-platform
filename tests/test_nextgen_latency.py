"""Default native routing keeps fixed-budget public policy semantics."""
import importlib.util
import unittest
from unittest.mock import patch

from agents.deep_chain_native import NativeBackendUnavailableError, IncompatibleSchemaError
from agents.deep_chain_search_backend import NativeLongHorizonSearchBackend, PythonLongHorizonSearchBackend
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from eval.nextgen_latency_benchmark import make_case
from puyo_env.realtime_ai import PolicyProcessExecutor


class BackendRoutingTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("_puyo_deep_chain_native"), "native release extension unavailable")
    def test_owned_native_spawn_revalidates_client_then_reuses_only_result(self):
        fixture, observation, info = make_case("empty", 55, (), "native")
        policy = NextgenTacticManagerPolicy(catalog=fixture.catalog, seed=55,
            profile=fixture.profile, search_config=fixture.search_config, backend="native")
        expected = policy.select_action(observation, info)
        executor = PolicyProcessExecutor(policy)
        try:
            first, _, payload = executor.submit_policy(observation, info).result(timeout=30)
            second, _, repeated = executor.submit_policy(observation, info).result(timeout=30)
        finally:
            executor.shutdown(wait=True)
        self.assertEqual((first, second), (expected, expected))
        self.assertFalse(payload["search"]["shared_reuse"]["hit"])
        self.assertTrue(repeated["search"]["shared_reuse"]["hit"])
        self.assertEqual(payload["nextgen"]["selection"], repeated["nextgen"]["selection"])

    @unittest.skipUnless(importlib.util.find_spec("_puyo_deep_chain_native"), "native release extension unavailable")
    def test_owned_native_retries_rebuild_public_responses_and_adopt_once(self):
        from tests.test_nextgen_tactic_manager import SchedulerTests

        for regression in ("test_repeated_opponent_updates_retry_without_placement_then_adopt_once",
                           "test_retry_recomputes_response_when_new_own_threat_arrives"):
            case = SchedulerTests()
            case.setUp()
            original = case.policy
            case.policy = NextgenTacticManagerPolicy(catalog=original.catalog,
                search_config=original.search_config, profile=original.profile, seed=19, backend="native")
            getattr(case, regression)()

    @unittest.skipUnless(importlib.util.find_spec("_puyo_deep_chain_native"), "native release extension unavailable")
    def test_owned_native_cache_invalidates_adapter_module_client_and_callable(self):
        from dataclasses import replace
        from types import SimpleNamespace
        from agents.deep_chain_native import NativeDeepChainBackend
        from agents.nextgen_shared_search import SharedSearchBatchBuilder, SharedSearchCache
        from tests.test_nextgen_shared_search import config, request

        native = NativeLongHorizonSearchBackend()
        cache = SharedSearchCache(owned_native_backend=native)
        builder = SharedSearchBatchBuilder(native, config(), shared_cache=cache)
        req = request(quota=(4, 0, 0))
        first = builder.build(req)
        repeat = builder.build(req)
        self.assertTrue(repeat.diagnostics["shared_reuse"]["hit"])
        self.assertEqual(repeat.diagnostics["shared_reuse"]["current_boundary_calls"], 0)
        self.assertEqual(first.batch.digest, repeat.batch.digest)
        self.assertEqual(first.batch.counters.shared_nodes, repeat.batch.counters.shared_nodes)
        public = replace(req.public, opponent=replace(req.public.opponent, score_carry=1))
        retry = replace(req, public=public,
            identity=replace(req.identity, decision_id=2, request_id="native-retry", snapshot_digest=public.digest),
            execution=replace(req.execution, reachable_mask=(True,) * 21 + (False,)))
        cached = builder.build(retry)
        fresh = SharedSearchBatchBuilder(native, config()).build(retry)
        self.assertTrue(cached.diagnostics["shared_reuse"]["hit"])
        self.assertEqual(cached.deterministic_digest, fresh.deterministic_digest)
        self.assertNotEqual(cached.batch.identity, first.batch.identity)
        self.assertTrue(all(not v.root_reachable for v in cached.batch.candidates if v.root_action == 21))
        for change in (
            lambda: setattr(native, "execution_mode", "oracle-1"),
            lambda: setattr(native, "max_response_bytes", native.max_response_bytes - 1),
            lambda: setattr(native, "_native_backend", NativeDeepChainBackend()),
            lambda: setattr(native._client(), "_module", SimpleNamespace(decide=native._client()._module.decide)),
        ):
            change()
            self.assertFalse(builder.build(req).diagnostics["shared_reuse"]["hit"])
            self.assertTrue(builder.build(req).diagnostics["shared_reuse"]["hit"])
        client = native._client()
        with patch.object(client, "decide", side_effect=IncompatibleSchemaError("runtime ABI mismatch")):
            with self.assertRaises(IncompatibleSchemaError):
                builder.build(req)
        _, key, _, _ = cache.entry
        for change in (
            {"root_state": replace(key.root_state, all_clear_bonus_pending=True)},
            {"known_pairs": tuple(reversed(key.known_pairs))},
            {"search_config": replace(key.search_config, decision_seed=999)},
            {"search_config": replace(key.search_config, max_expanded_nodes=3)},
            {"evaluator_config": replace(key.evaluator_config, weight_version="other")},
        ):
            _, source = cache.search(native, replace(key, **change))
            self.assertIsNone(source)
        # Supplying even the standard native adapter explicitly is an injection,
        # so callers retain uncached execution unless they own the construction.
        policy = NextgenTacticManagerPolicy(backend=native)
        self.assertIsNone(policy.shared_cache.owned_native_backend)
        _, source = cache.search(NativeLongHorizonSearchBackend(), key)
        self.assertIsNone(source)
        self.assertIsNone(cache.entry)

    def test_default_native_initialization_fails_closed(self):
        for error in (NativeBackendUnavailableError("missing", retry_safe=True), IncompatibleSchemaError("wrong ABI")):
            with self.subTest(error=type(error).__name__), patch(
                "agents.nextgen_tactic_manager.NativeLongHorizonSearchBackend", side_effect=error,
            ), patch("agents.nextgen_tactic_manager.PythonLongHorizonSearchBackend") as python:
                with self.assertRaises(type(error)):
                    NextgenTacticManagerPolicy()
                python.assert_not_called()

    def test_python_is_explicit_and_auto_is_rejected(self):
        self.assertIsInstance(NextgenTacticManagerPolicy(backend="python").backend, PythonLongHorizonSearchBackend)
        with self.assertRaises(ValueError):
            NextgenTacticManagerPolicy(backend="auto")

    def test_gui_cli_backend_is_explicit_and_recorded(self):
        from eval.realtime_versus_ui import parse_config, validate_config
        from dataclasses import replace

        config = parse_config(["--policy-a", "nextgen_tactic_manager", "--nextgen-backend", "python"])
        self.assertEqual(config.nextgen_backend, "python")
        self.assertEqual(parse_config([]).nextgen_backend, "native")
        with self.assertRaises(ValueError):
            validate_config(replace(config, nextgen_backend="auto"))

    @unittest.skipUnless(importlib.util.find_spec("_puyo_deep_chain_native"), "native release extension unavailable")
    def test_native_private_future_isolation_and_runtime_failure(self):
        from tests.test_nextgen_tactic_manager import SchedulerTests

        # Exercise the same authoritative public-boundary regression with the
        # new default backend, including hidden row and unseen queue changes.
        case = SchedulerTests()
        case.setUp()
        case.policy.backend = NativeLongHorizonSearchBackend()
        case.test_private_future_changes_do_not_change_candidate_features_or_selection()
        observation, info = case.controller.nextgen_scheduler.prepare(case.match, "player_0", case.controller.config)
        with patch.object(case.policy.backend, "search", side_effect=IncompatibleSchemaError("runtime ABI mismatch")), patch(
            "agents.nextgen_tactic_manager.PythonLongHorizonSearchBackend"
        ) as python:
            with self.assertRaises(IncompatibleSchemaError):
                case.policy.select_action(observation, info)
            python.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec("_puyo_deep_chain_native"), "native release extension unavailable")
    def test_default_native_and_public_candidate_parity_through_spawn(self):
        self.assertIsInstance(NextgenTacticManagerPolicy().backend, NativeLongHorizonSearchBackend)
        python, observation, info = make_case("gtr_partial", 55, ("221000", "112000", "020000"), "python")
        native, _, native_info = make_case("gtr_partial", 55, ("221000", "112000", "020000"), "native")
        self.assertEqual(info["nextgen"]["public"], native_info["nextgen"]["public"])
        expected_action = python.select_action(observation, info)
        expected = python.tactical_diagnostics
        executor = PolicyProcessExecutor(native)
        try:
            action, _, actual = executor.submit_policy(observation, info).result(timeout=30)
        finally:
            executor.shutdown(wait=True)
        self.assertEqual(action, expected_action)
        self.assertEqual(actual["nextgen"]["selection"], expected["nextgen"]["selection"])
        self.assertEqual(actual["nextgen"]["batch"]["candidates"], expected["nextgen"]["batch"]["candidates"])
        self.assertEqual(actual["search"]["backend"]["backend"], "native")
        self.assertFalse(actual["search"]["backend"]["fallback"]["used"])
        self.assertEqual(actual["search"]["backend"]["boundary_call_count"], 1)
        for key in ("shared_nodes", "template_nodes", "response_nodes", "feature_evaluations"):
            self.assertEqual(actual["nextgen"]["batch"]["counters"][key], expected["nextgen"]["batch"]["counters"][key])


if __name__ == "__main__":
    unittest.main()
