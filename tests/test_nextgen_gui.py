"""GUI boundary checks for nextgen launch settings and adopted receipts."""

import json
import tempfile
import unittest
from pathlib import Path

from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from agents.nextgen_shared_search import scenario_provenance
from agents.template_catalog import MatchResult, TemplateCandidate
from eval.realtime_versus_ui import RealtimeVersusMatchController, RealtimeVersusUiConfig, parse_config, validate_config
from puyo_env.nextgen_scheduler import NextgenScheduler
from src.ui.launcher import LauncherService
from src.ui.nextgen_display import history_entries_for_tick, nextgen_receipt_summary
from src.ui.model_viewer import ModelViewerController, build_model_viewer_data, handle_model_viewer_navigation_key
from src.ui.keybindings import DEFAULT_BINDINGS

import pygame


ROOT = Path(__file__).resolve().parents[1]


def receipt_tick(request_id, tactic, *, tick=12, phase_id="phase-1", outcome="activated"):
    diagnostics = {
        "request": {
            "identity": {"episode_id": "episode-1", "request_id": request_id, "decision_id": tick},
            "control": {"phase": {"phase_id": phase_id, "template_id": "gtr", "active": True, "remaining_decisions": 13, "decision_limit": 14}},
        },
        "selection": {"selected_tactic_id": tactic, "reason": "rule_priority_" + tactic},
        "receipt": {"requested_action": 3, "executed_action": 5, "outcome": outcome, "reason": "fixture", "request_tick": 10, "activation_tick": tick},
    }
    return {
        "tick": tick,
        "nextgen_agents": ["player_0"],
        "policy_diagnostics": {"player_0": {
            "nextgen": {"selection": {"selected_tactic_id": "wrong_worker"}},
            "template_selection": {"candidate": {"template_id": "gtr", "variant_id": "left_supported_core", "score": 99}, "probability": 0.7},
            "template_phase": {"phase_id": phase_id, "decision_limit": 14, "consumed_decisions": 1},
        }},
        "controller_diagnostics": {"player_0": {"last_decision": {"nextgen_diagnostics": diagnostics, "outcome": outcome}}},
        "public_events": {"player_0": [{"type": "resolution_complete", "data": {"chain_count": 2}}]},
    }


class NextgenGuiTests(unittest.TestCase):
    def test_fn_free_history_keys_and_legacy_keys_keep_navigation(self):
        reserved = {"j", "k", "l", "u", "i"}
        self.assertFalse(reserved.intersection(key for keys in DEFAULT_BINDINGS.values() for key in keys))
        pygame.init()
        self.addCleanup(pygame.quit)
        with tempfile.TemporaryDirectory() as directory:
            controller = RealtimeVersusMatchController(RealtimeVersusUiConfig(
                policy_a="random", policy_b="random", keybindings_path=str(Path(directory) / "keys.json"),
            ))
            try:
                controller.tactic_history = [{"kind": "event", "tick": tick} for tick in range(25)]
                controller.handle_keydown(pygame.K_h)
                self.assertTrue(controller.history_open)
                for key, offset in (
                    (pygame.K_j, 8), (pygame.K_j, 16), (pygame.K_k, 8),
                    (pygame.K_l, 0), (pygame.K_PAGEUP, 8),
                    (pygame.K_PAGEDOWN, 0), (pygame.K_j, 8), (pygame.K_END, 0),
                ):
                    controller.handle_keydown(key)
                    self.assertEqual(controller.history_offset, offset)
            finally:
                controller.shutdown()

    def test_launcher_preset_and_cli_round_trip_into_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            service = LauncherService(repo_root=ROOT, preset_store_path=Path(directory) / "presets.json")
            for field, value in {
                "policy_a": "nextgen_tactic_manager",
                "nextgen_templates": "gtr,persian",
                "nextgen_selection_mode": "softmax",
                "nextgen_temperature": 0.5,
                "nextgen_seed": 17,
                "nextgen_commit_turns": 9,
                "nextgen_profile": "nextgen_diagnostic",
                "nextgen_trajectory_path": str(Path(directory) / "ledger.json"),
            }.items():
                service.update_setting("spectate", field, value)
            self.assertEqual(service.settings.validate("spectate"), [])
            service.settings.save_preset("spectate", "nextgen-test")
            restored = LauncherService(repo_root=ROOT, preset_store_path=Path(directory) / "presets.json")
            self.assertEqual(restored.settings.store.load_preset("spectate", "nextgen-test").nextgen_templates, "gtr,persian")
            config = parse_config(service.command_for("spectate")[3:])
            self.assertEqual(config.nextgen_seed, 17)
            self.assertEqual(config.nextgen_profile, "nextgen_diagnostic")
            self.assertEqual(config.nextgen_commit_turns, 9)

    def test_invalid_nextgen_config_rejected_before_start(self):
        valid = RealtimeVersusUiConfig(policy_a="nextgen_tactic_manager")
        for kwargs in (
            {"nextgen_templates": ""},
            {"nextgen_templates": "unknown"},
            {"nextgen_temperature": 0},
            {"nextgen_commit_turns": 0},
            {"nextgen_selector": "rl"},
            {"nextgen_profile": "unregistered"},
            {"checkpoint_a": "wrong-schema.pt"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                validate_config(RealtimeVersusUiConfig(**{**valid.__dict__, **kwargs}))

    def test_sidebar_and_history_use_adopted_receipt_without_fit_values(self):
        tick = receipt_tick("request-1", "build_template")
        summary = nextgen_receipt_summary(
            tick["policy_diagnostics"]["player_0"], tick["controller_diagnostics"]["player_0"]
        )
        self.assertEqual(summary["tactic"], "土台構築")
        self.assertEqual(summary["requested_action"], 3)
        self.assertEqual(summary["executed_action"], 5)
        self.assertEqual(summary["remaining"], 13)
        self.assertNotIn("score", json.dumps(summary))
        self.assertNotIn("probability", json.dumps(summary))
        seen = {}
        first = history_entries_for_tick(tick, seen)
        self.assertEqual(first[0]["tactic_id"], "build_template")
        self.assertEqual(first[1]["event"], "resolution_complete")
        self.assertEqual(history_entries_for_tick(tick, seen)[0]["kind"], "event")
        second = receipt_tick("request-2", "counter", tick=15, phase_id="phase-2")
        history = history_entries_for_tick(second, seen)
        self.assertEqual(history[0]["kind"], "gap")
        self.assertEqual(history[0]["to_tick"], 14)
        self.assertEqual(history[1]["previous_tactic"], "build_template")
        self.assertTrue(history[1]["reselected"])

    def test_template_seed_changes_selection_without_search_provenance(self):
        from src.ui.launcher_settings import resolve_nextgen_catalog

        catalog, _ = resolve_nextgen_catalog(
            catalog_path="train/config/nextgen_templates.yaml", templates="gtr,daa",
            mode="softmax", temperature=1000.0, commit_turns=14, repo_root=ROOT,
        )
        policies = [NextgenTacticManagerPolicy(catalog=catalog, seed=19, template_seed=seed) for seed in (0, 1)]
        candidates = tuple(
            TemplateCandidate(template_id, "base", "identity", (("A", 1),), 0.1, False, 0.0, "fit", "fixture", None, (), 0, False, "static", 0)
            for template_id in ("daa", "gtr")
        )
        result = MatchResult(candidates, 0, False, 0, False)
        picks = [NextgenScheduler(policy).phase.start(result, decision_id="d1").candidate.template_id for policy in policies]
        self.assertNotEqual(*picks)
        self.assertEqual(
            scenario_provenance(((1, 2),), policies[0].search_config),
            scenario_provenance(((1, 2),), policies[1].search_config),
        )

    def test_replay_history_seeks_to_same_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            decision_tick = receipt_tick("request-1", "build_template")
            history = history_entries_for_tick(decision_tick, {}) + [{"kind": "event", "tick": 13, "agent": "player_0", "event": "lock"}]
            replay = Path(directory) / "replay.json"
            replay.write_text(json.dumps({
                "format": "puyo-realtime-match-v1",
                "seed": 1,
                "policies": {"player_0": {"policy_type": "nextgen_tactic_manager"}},
                "ticks": [{"tick": 10}, decision_tick, {"tick": 13}],
                "tactic_history": history,
            }), encoding="utf-8")
            controller = ModelViewerController(build_model_viewer_data(replay_path=replay, model_registry_path=None))
            controller.seek_tactic_history(1)
            self.assertEqual(controller.selected_entry.tick, 12)
            self.assertTrue(handle_model_viewer_navigation_key(controller, pygame.K_k))
            self.assertEqual(controller.selected_entry.tick, 13)
            self.assertTrue(handle_model_viewer_navigation_key(controller, pygame.K_j))
            self.assertEqual(controller.selected_entry.tick, 12)
            self.assertTrue(handle_model_viewer_navigation_key(controller, pygame.K_h))
            self.assertEqual(controller.selected_entry.tick, 13)
            self.assertTrue(handle_model_viewer_navigation_key(controller, pygame.K_h, pygame.KMOD_SHIFT))
            self.assertEqual(controller.selected_entry.tick, 12)
            report = controller.report()["replay"]
            self.assertEqual(report["selected_entry"]["agents"]["player_0"]["nextgen"]["tactic_id"], "build_template")
            self.assertEqual(next(item for item in report["tactic_history"] if item["kind"] == "decision")["requested_action"], 3)
            self.assertTrue(any(item["kind"] == "gap" for item in report["tactic_history"]))


if __name__ == "__main__":
    unittest.main()
