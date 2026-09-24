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
