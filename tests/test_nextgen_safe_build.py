"""Safe-build profile and completed-template execution regressions."""

import unittest
from dataclasses import replace
from pathlib import Path

from agents import nextgen_contracts as c
from agents.deep_chain_builder import load_deep_chain_builder_config
from agents.nextgen_profiles import DEFAULT_NEXTGEN_PROFILE, nextgen_search_settings
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from src.core.puyo import Puyo
from src.ui.launcher_settings import LauncherSettings, resolve_nextgen_catalog


class SafeBuildTests(unittest.TestCase):
    def smoke_policy(self):
        catalog, _ = resolve_nextgen_catalog(
            catalog_path="train/config/nextgen_templates.yaml", templates="gtr",
            mode="argmax", temperature=.1, commit_turns=14,
            repo_root=Path(__file__).resolve().parents[1],
        )
        profile, search = nextgen_search_settings("nextgen_smoke", seed=55)
        return NextgenTacticManagerPolicy(catalog=catalog, seed=55, profile=profile,
                                          search_config=search, backend="python")

    def test_default_profile_matches_deep_reference_budget_and_target(self):
        from eval.realtime_versus_ui import parse_config

        policy = NextgenTacticManagerPolicy(backend="python")
        reference = load_deep_chain_builder_config().profile("reference")
        self.assertEqual(policy.profile.profile_id, DEFAULT_NEXTGEN_PROFILE)
        for field in ("depth", "width", "scenarios", "max_expanded_nodes"):
            self.assertEqual(getattr(policy.search_config, field), getattr(reference, field))
        self.assertEqual(policy.search_config.minimum_chain_count, 10)
        self.assertEqual(policy.selector.config.saturated_chain_count, 10)
        self.assertEqual(LauncherSettings().nextgen_profile, DEFAULT_NEXTGEN_PROFILE)
        self.assertEqual(parse_config([]).nextgen_profile, DEFAULT_NEXTGEN_PROFILE)
        smoke, config = nextgen_search_settings("nextgen_smoke", seed=55)
        self.assertEqual((smoke.shared_quota, config.minimum_chain_count), (256, 10))

    def test_completed_gtr_immediately_builds_without_clearing_or_reselecting(self):
        policy = self.smoke_policy()
        match = SafeNoThreatMatch(55)
        game = match.player_states["player_0"].simulator.game
        for y, row in enumerate(("221000", "112000", "122000")):
            for x, cell in enumerate(row):
                if int(cell):
                    game.field.grid[y][x] = Puyo(c.PUBLIC_CELL_TO_COLOR[int(cell)])
        controller = RealtimePolicyController(policy, config=RealtimeDecisionConfig(latency_mode="configured"))
        chains = []
        for _ in range(500):
            result = match.step({"player_0": controller.next_input(match, "player_0")})
            chains.extend(e.data["chain_count"] for e in result.player_results["player_0"].events if e.type == "resolution_complete")
            if len(chains) >= 2:
                break
        runtime = controller.nextgen_scheduler
        self.assertEqual(chains, [0, 0])
        self.assertEqual(runtime.errors, [])
        self.assertEqual(runtime.phase.exit_reason, "completed")
        self.assertEqual(runtime.phase.phase.phase_id, "template-phase-1")
        self.assertEqual(runtime.phase.phase.consumed_decisions, 0)
        self.assertIsNone(runtime.phase.pending_resolution)
        for diagnostic in runtime.ledger:
            self.assertEqual(diagnostic.selection.selected_tactic_id, "build_main")
            self.assertFalse(diagnostic.request.control.phase.active)
            self.assertEqual(diagnostic.receipt.outcome, "activated")
            self.assertEqual(diagnostic.receipt.requested_action, diagnostic.receipt.executed_action)
            c.Diagnostics.from_dict(diagnostic.to_dict())

    def test_unfinished_gtr_fourteenth_receipt_closes_limit_before_fifteenth(self):
        policy = self.smoke_policy()
        match = SafeNoThreatMatch(55)
        controller = RealtimePolicyController(policy, config=RealtimeDecisionConfig(latency_mode="configured"))
        runtime = controller.nextgen_scheduler
        observation, info = runtime.prepare(match, "player_0", controller.config)
        policy.select_action(observation, info)
        # Start the boundary fixture with an unfinished GTR and 13 previously
        # consumed pairs. The next two decisions still use real receipts.
        runtime.phase = policy.last_context.require("phase")
        runtime.phase.phase = replace(runtime.phase.phase, consumed_decisions=13)
        self.assertFalse(runtime.phase.candidate.complete)
        for _ in range(500):
            match.step({"player_0": controller.next_input(match, "player_0")})
            if len(runtime.ledger) >= 2:
                break
        first, second = runtime.ledger
        self.assertEqual(first.selection.selected_tactic_id, "build_template")
        self.assertEqual(first.request.control.phase.remaining_decisions, 1)
        self.assertEqual(second.selection.selected_tactic_id, "build_main")
        self.assertFalse(second.request.control.phase.active)
        self.assertEqual(runtime.phase.exit_reason, "limit")
        self.assertEqual(runtime.phase.phase.consumed_decisions, 14)
        self.assertTrue(all(d.receipt.outcome == "activated" for d in runtime.ledger))


if __name__ == "__main__":
    unittest.main()
