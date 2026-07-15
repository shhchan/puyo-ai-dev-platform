import copy
import unittest

from agents.chain_styles import (
    CHAIN_STYLE_SCHEMA_VERSION,
    ChainStyleProviderResult,
    ChainStyleRegistry,
    ChainStyleSelection,
    load_chain_style_registry,
)
from agents.state_analyzer import StateAnalyzer
from agents.strategy_workers import StrategyOrchestrator, smoke_worker_profiles
from agents.v1_7_planner import build_planner_request
from agents.v1_7_strategy_manager import build_v1_7_checkpoint_metadata
from agents.v1_7_tactics import (
    LEGACY_TACTIC_SCHEMA_VERSION,
    TACTIC_SCHEMA_VERSION,
    TacticRegistry,
    build_tactic_registry_artifact,
    load_tactic_registry,
    migrate_tactic_registry_payload,
)
from eval.analyzer_scenarios import load_scenarios, scenario_input
from puyo_env.actions import legal_action_mask
from puyo_env.obs import encode_observation
from src.core.headless import HeadlessPuyoSimulator


class _ConstantStyleProvider:
    def evaluate(self, simulator, definition):
        _ = simulator, definition
        return ChainStyleProviderResult(
            applicable=True,
            adherence_score=0.5,
            hard_constraint_satisfied=True,
            diagnostics={"fixture": "constant"},
        )


class TestChainStyles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tactics = load_tactic_registry()
        cls.analyzer_input = scenario_input(load_scenarios()[0])
        cls.analyzer_diagnostics = StateAnalyzer().analyze(cls.analyzer_input)

    def test_default_unconstrained_round_trips_without_changing_search(self):
        tactic = self.tactics.tactic("build_main")
        implicit = build_planner_request(
            tactic,
            self.analyzer_input,
            self.analyzer_diagnostics,
            parameter_overrides={"planner": {"beam_depth": 1, "beam_width": 8}},
        )
        explicit = build_planner_request(
            tactic,
            self.analyzer_input,
            self.analyzer_diagnostics,
            parameter_overrides={"planner": {"beam_depth": 1, "beam_width": 8}},
            chain_style=ChainStyleSelection(),
        )

        proposals = []
        for request in (implicit, explicit):
            simulator = HeadlessPuyoSimulator(seed=168)
            proposals.append(
                StrategyOrchestrator(smoke_worker_profiles()).propose(
                    0,
                    encode_observation(simulator, step_count=0, max_steps=40),
                    {"simulator": simulator, "action_mask": legal_action_mask(simulator)},
                    planner_request=request,
                )
            )

        self.assertEqual(implicit.to_dict(), explicit.to_dict())
        self.assertEqual(proposals[0].action, proposals[1].action)
        self.assertEqual(proposals[0].candidate_value, proposals[1].candidate_value)
        self.assertEqual(proposals[0].value_breakdown, proposals[1].value_breakdown)
        self.assertIsNone(proposals[0].chain_style_evaluation)

    def test_named_provider_contribution_is_separate_from_generic_metrics(self):
        registry = ChainStyleRegistry.from_dict(
            {
                "schema_version": "puyo.chain_style_registry.v1",
                "registry_version": "test-v1",
                "styles": [
                    {
                        "style_id": "unconstrained",
                        "style_version": "1.0",
                        "provider_id": "builtin.unconstrained.v1",
                    },
                    {
                        "style_id": "fixture-style",
                        "style_version": "1.0",
                        "provider_id": "test.constant.v1",
                    },
                ],
            }
        )
        providers = {"test.constant.v1": _ConstantStyleProvider()}
        selection = ChainStyleSelection(
            style_id="fixture-style",
            style_version="1.0",
            constraint_mode="soft_preference",
            weight=0.5,
        )
        request = build_planner_request(
            self.tactics.tactic("build_main"),
            self.analyzer_input,
            self.analyzer_diagnostics,
            parameter_overrides={"planner": {"beam_depth": 1, "beam_width": 8}},
            chain_style=selection,
            chain_style_registry=registry,
            chain_style_providers=providers,
        )
        simulator = HeadlessPuyoSimulator(seed=168)
        proposal = StrategyOrchestrator(
            smoke_worker_profiles(),
            chain_style_registry=registry,
            chain_style_providers=providers,
        ).propose(
            0,
            encode_observation(simulator, step_count=0, max_steps=40),
            {"simulator": simulator, "action_mask": legal_action_mask(simulator)},
            planner_request=request,
        )

        self.assertGreater(proposal.value_breakdown["style_adherence"], 0.0)
        namespaces = proposal.build_potential_dict["metric_namespaces"]
        self.assertIn("build_potential", namespaces["generic_capability"])
        self.assertGreater(namespaces["style_adherence"]["score_contribution"], 0.0)
        self.assertEqual(
            namespaces["style_adherence"]["metric_namespace"],
            "style_adherence",
        )

    def test_unknown_deprecated_and_missing_provider_fall_back_deterministically(self):
        registry = load_chain_style_registry()
        unknown = registry.resolve(
            ChainStyleSelection("does-not-exist", "1.0", "soft_preference", 1.0)
        )
        missing_registry = ChainStyleRegistry.from_dict(
            {
                "schema_version": "puyo.chain_style_registry.v1",
                "registry_version": "missing-v1",
                "styles": [
                    {
                        "style_id": "unconstrained",
                        "style_version": "1.0",
                        "provider_id": "builtin.unconstrained.v1",
                    },
                    {
                        "style_id": "missing",
                        "style_version": "1.0",
                        "provider_id": "not-installed",
                    },
                ],
            }
        )
        missing = missing_registry.resolve(
            ChainStyleSelection("missing", "1.0", "hard_constraint", 1.0)
        )
        deprecated_registry = ChainStyleRegistry.from_dict(
            {
                "schema_version": "puyo.chain_style_registry.v1",
                "registry_version": "deprecated-v1",
                "styles": [
                    {
                        "style_id": "unconstrained",
                        "style_version": "1.0",
                        "provider_id": "builtin.unconstrained.v1",
                    },
                    {
                        "style_id": "old-style",
                        "style_version": "1.0",
                        "provider_id": "fixture.named-style-stub.v1",
                        "deprecated": True,
                    },
                ],
            }
        )
        deprecated = deprecated_registry.resolve(
            ChainStyleSelection("old-style", "1.0", "soft_preference", 1.0)
        )

        self.assertEqual(unknown.selected.style_id, "unconstrained")
        self.assertEqual(unknown.diagnostic_code, "unknown_style")
        self.assertEqual(missing.selected.style_id, "unconstrained")
        self.assertEqual(missing.diagnostic_code, "missing_provider")
        self.assertEqual(deprecated.selected.style_id, "unconstrained")
        self.assertEqual(deprecated.diagnostic_code, "deprecated_style")
        self.assertTrue(unknown.to_dict()["fallback_applied"])

    def test_tactic_planner_checkpoint_and_artifact_preserve_style_identity(self):
        registry = load_chain_style_registry()
        style = ChainStyleSelection("gtr", "1.0", "soft_preference", 0.75)
        request = build_planner_request(
            self.tactics.tactic("build_main"),
            self.analyzer_input,
            self.analyzer_diagnostics,
            chain_style=style,
            chain_style_registry=registry,
        )
        tactic_round_trip = TacticRegistry.from_dict(self.tactics.to_dict())
        checkpoint = build_v1_7_checkpoint_metadata(
            self.tactics,
            run_id="style-round-trip",
            chain_style=style,
        )
        artifact = build_tactic_registry_artifact(self.tactics)

        self.assertEqual(tactic_round_trip.tactic("build_main").chain_style.style_id, "unconstrained")
        self.assertEqual(request.to_dict()["chain_style"]["selected"]["style_id"], "gtr")
        self.assertEqual(request.to_dict()["chain_style"]["selected"]["style_version"], "1.0")
        self.assertEqual(checkpoint["chain_style"], style.to_dict())
        self.assertEqual(checkpoint["schemas"]["chain_style"], CHAIN_STYLE_SCHEMA_VERSION)
        self.assertEqual(artifact["chain_style_schema_version"], CHAIN_STYLE_SCHEMA_VERSION)

    def test_tactic_schema_v2_migration_adds_unconstrained_style(self):
        legacy = copy.deepcopy(self.tactics.to_dict())
        legacy["schema_version"] = LEGACY_TACTIC_SCHEMA_VERSION
        for tactic in legacy["tactics"]:
            tactic.pop("chain_style", None)

        migrated = migrate_tactic_registry_payload(legacy)

        self.assertEqual(migrated["schema_version"], TACTIC_SCHEMA_VERSION)
        self.assertTrue(
            all(item["chain_style"]["style_id"] == "unconstrained" for item in migrated["tactics"])
        )
        self.assertEqual(
            migrated["schema_migration"]["source_schema_version"],
            LEGACY_TACTIC_SCHEMA_VERSION,
        )


if __name__ == "__main__":
    unittest.main()
