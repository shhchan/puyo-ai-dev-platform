"""Observed GTR progress and authoritative adoption, separate from gate PASS."""

import json
import unittest
from pathlib import Path

from agents import nextgen_contracts as c
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from agents.template_catalog import match_templates
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from eval.nextgen_realtime_diagnostic import template_observation
from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from src.core.puyo import Puyo
from src.ui.launcher_settings import resolve_nextgen_catalog


class GtrCapabilityTests(unittest.TestCase):
    def test_fixed_gtr_fixture_completes_then_rule_switches_to_build_main(self):
        fixture = json.loads(Path("tests/fixtures/nextgen_template_catalog_cases.json").read_text())
        case = next(v for v in fixture["cases"] if v["id"] == "gtr_left_supported_core")
        catalog, _ = resolve_nextgen_catalog(
            catalog_path="train/config/nextgen_templates.yaml", templates="gtr",
            mode="argmax", temperature=0.1, commit_turns=14, repo_root=".",
        )
        match = SafeNoThreatMatch(55)
        game = match.player_states["player_0"].simulator.game
        for y, row in enumerate(case["start_rows_bottom_up"]):
            for x, cell in enumerate(row):
                game.field.grid[y][x] = Puyo(c.PUBLIC_CELL_TO_COLOR[int(cell)])
        game.current_puyo_1, game.current_puyo_2 = (
            Puyo(c.PUBLIC_CELL_TO_COLOR[v]) for v in case["public_pieces"][0]
        )
        controller = RealtimePolicyController(
            NextgenTacticManagerPolicy(catalog=catalog, seed=55),
            config=RealtimeDecisionConfig(latency_mode="configured"),
        )
        first = controller.next_input(match, "player_0")
        runtime = controller.nextgen_scheduler
        key = runtime.phase.candidate.key
        before = template_observation(catalog, match.public_snapshot(), key)
        self.assertFalse(before["complete"])
        self.assertEqual(runtime.ledger[0].selection.selected_tactic_id, "build_template")
        self.assertEqual(runtime.ledger[0].receipt.outcome, "activated")
        self.assertEqual(runtime.ledger[0].request.control.search_profile.template_quota, 128)
        self.assertLessEqual(runtime.ledger[0].batch.counters.template_nodes, 128)
        request = runtime.ledger[0].request
        legacy = match_templates(
            catalog, request.public.own.visible_board, request.public.own.known_pieces,
            node_budget=128, binding_budget=4096,
            reachable_mask=request.execution.reachable_mask,
        )
        # Reproduce initial static fallback starvation without the nextgen
        # opt-in. Existing catalog consumers keep their enumeration contract.
        self.assertFalse(next(v for v in legacy.candidates if v.key == key).witness_actions)
        match.step({"player_0": first})
        for _ in range(200):
            match.step({"player_0": controller.next_input(match, "player_0")})
            if len(runtime.ledger) >= 2:
                break
        else:
            self.fail("second decision not reached")
        after = template_observation(catalog, runtime.ledger[1].request.public, key)
        self.assertGreater(after["progress"], before["progress"])
        self.assertEqual(after["progress"], 1.0)
        self.assertTrue(after["complete"])
        self.assertEqual(runtime.ledger[1].selection.selected_tactic_id, "build_main")
        self.assertEqual(runtime.ledger[1].receipt.outcome, "activated")
        self.assertFalse(runtime.ledger[1].request.control.phase.active)
        self.assertTrue(all(d.receipt.requested_action == d.receipt.executed_action for d in runtime.ledger))


if __name__ == "__main__":
    unittest.main()
