"""Artificial pattern fixtures only; production style shapes belong to PUYO-246."""

import copy
import string
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agents.chain_styles import (
    ChainStyleEvaluator,
    ChainStyleRegistry,
    ChainStyleSelection,
    PublicTemplateCatalogProvider,
    PublicTemplateStyleInput,
)
from agents.nextgen_contracts import PublicPlayerState, PublicSnapshot
from agents.template_catalog import (
    TemplateCatalog,
    TemplateSelector,
    load_template_catalog,
    match_templates,
)
from puyo_env.actions import NUM_ACTIONS
from src.core.constants import GRID_WIDTH, NORMAL_PUYO_COLORS


def catalog(
    *,
    rows=("A.",),
    same=(),
    different=(),
    empty=(),
    occupied=(),
    origin=(0, 0),
    transforms=("identity",),
    mode="argmax",
    templates=None,
):
    variant = {
        "id": "base",
        "origin": {"x": origin[0], "y_from_bottom": origin[1]},
        "pattern_rows_bottom_up": list(rows),
        "same": [list(v) for v in same],
        "different": [list(v) for v in different],
        "empty_cells": [list(v) for v in empty],
        "occupied_cells": [list(v) for v in occupied],
        "weight": 1.0,
        "transforms": list(transforms),
    }
    return {
        "schema_version": "puyo.template_catalog.v1",
        "enabled": True,
        "selection": {"mode": mode, "temperature": 0.2, "seed_stream": "template"},
        "default_commit_turns": 14,
        "templates": templates
        if templates is not None
        else [
            {
                "id": "fixture",
                "version": "1",
                "enabled": True,
                "commit_turns": None,
                "variants": [variant],
            }
        ],
    }


def board(*, hidden=False):
    rows = [[0] * 6 for _ in range(14)]
    if hidden:
        rows[:2] = [[None] * 6 for _ in range(2)]
    return rows


class TemplateCatalogTest(unittest.TestCase):
    def test_loader_rejects_unknown_duplicate_undefined_contradiction_and_invalid_cells(
        self,
    ):
        cases = []
        value = catalog()
        value["unexpected"] = 1
        cases.append(value)
        value = catalog()
        value["templates"].append(copy.deepcopy(value["templates"][0]))
        cases.append(value)
        value = catalog()
        value["templates"][0]["variants"][0]["same"] = [["A", "Z"]]
        cases.append(value)
        value = catalog(rows=("AB",), same=(("A", "B"),), different=(("A", "B"),))
        cases.append(value)
        value = catalog(rows=("AB",), empty=((0, 0),))
        cases.append(value)
        value = catalog(origin=(5, 0))
        cases.append(value)
        value = catalog(origin=(0, 13), rows=("A", "B"))
        cases.append(value)
        value = catalog()
        value["selection"]["temperature"] = 0
        cases.append(value)
        value = catalog()
        value["default_commit_turns"] = -1
        cases.append(value)
        value = catalog()
        value["templates"] = []
        cases.append(value)
        value = catalog(origin=(0, 1), occupied=())
        cases.append(value)
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                TemplateCatalog.from_dict(item)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.yaml"
            path.write_text(
                "schema_version: one\nschema_version: two\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                load_template_catalog(path)

    def test_checked_in_yaml_and_semantic_digest(self):
        path = Path(__file__).parent / "fixtures" / "nextgen" / "template_catalog.yaml"
        loaded = load_template_catalog(path)
        self.assertEqual(loaded.templates[0].id, "relation_fixture")
        self.assertEqual(loaded.templates[0].commit_turns, 14)
        value = catalog(rows=("AB",), same=(("A", "B"),))
        alternate = copy.deepcopy(value)
        alternate["templates"][0]["variants"][0]["same"] = [["B", "A"]]
        self.assertEqual(
            TemplateCatalog.from_dict(value).semantic_digest,
            TemplateCatalog.from_dict(alternate).semantic_digest,
        )

    def test_noninjective_binding_and_wildcards_are_not_empty(self):
        config = TemplateCatalog.from_dict(
            catalog(rows=("A.C*",), different=(("A", "C"),))
        )
        b = board()
        b[-1][:4] = [1, 3, 2, 4]
        result = match_templates(config, b, (), node_budget=0, binding_budget=16)
        candidate = max(result.candidates, key=lambda c: c.score)
        self.assertEqual(candidate.score, 1.0)
        self.assertEqual(dict(candidate.binding)["A"], 1)
        self.assertEqual(dict(candidate.binding)["C"], 2)
        config = TemplateCatalog.from_dict(catalog(rows=("AC",)))
        b[-1][:2] = [1, 1]
        result = match_templates(config, b, (), node_budget=0, binding_budget=16)
        self.assertEqual(max(c.score for c in result.candidates), 1.0)

    def test_mirror_dedup_and_explicit_raised_support(self):
        config = TemplateCatalog.from_dict(
            catalog(rows=("A",), transforms=("identity", "mirror_x"))
        )
        result = match_templates(config, board(), (), node_budget=0, binding_budget=0)
        self.assertEqual(len(result.candidates), 1)
        raised = TemplateCatalog.from_dict(
            catalog(rows=("A",), origin=(0, 1), occupied=((0, 0),))
        )
        b = board()
        b[-1][0] = 5
        b[-2][0] = 1
        result = match_templates(raised, b, (), node_budget=0, binding_budget=4)
        self.assertEqual(max(c.score for c in result.candidates), 1.0)

    def test_public_visible_root_fit_has_replayable_witness(self):
        config = TemplateCatalog.from_dict(catalog())
        b = board(hidden=True)
        result = match_templates(
            config,
            b,
            ((1, 2),),
            node_budget=100,
            binding_budget=4,
            reachable_mask=(True,) * NUM_ACTIONS,
        )
        fits = [
            candidate
            for candidate in result.candidates
            if candidate.fit_status == "fit"
        ]
        self.assertTrue(fits)
        selected = TemplateSelector(config, 7).select_initial(result)
        self.assertIsNotNone(selected.candidate)
        witness = fits[0]
        self.assertEqual(witness.known_prefix_length, 1)
        self.assertEqual(len(witness.witness_actions), 1)
        self.assertIsNotNone(witness.witness_candidate_id)
        self.assertEqual(witness.score_source, "searched")

    def test_full_known_prefix_can_use_intermediate_nonprogress_move(self):
        config = TemplateCatalog.from_dict(catalog())
        result = match_templates(
            config, board(), ((2, 2), (1, 1)), node_budget=900, binding_budget=4
        )
        fit = next(c for c in result.candidates if c.fit_status == "fit")
        self.assertEqual(fit.known_prefix_length, 2)
        self.assertEqual(len(fit.witness_actions), 2)
        self.assertEqual(fit.binding, (("A", 1),))

    def test_static_fallback_scores_all_templates_before_search_quota(self):
        first = catalog()["templates"][0]
        second = copy.deepcopy(first)
        second["id"] = "other"
        second["variants"][0]["origin"]["x"] = 1
        config = TemplateCatalog.from_dict(catalog(templates=[first, second]))
        b = board()
        b[-1][1] = 2
        result = match_templates(config, b, ((1, 2),), node_budget=0, binding_budget=0)
        self.assertEqual(
            TemplateSelector(config, 0).select_initial(result).candidate.template_id,
            "other",
        )
        self.assertTrue(
            all(c.score_source == "static_fallback" for c in result.candidates)
        )
        self.assertGreaterEqual(result.static_bindings, 8)
        self.assertTrue(result.cutoff)

    def test_clique_larger_than_engine_color_set_is_rejected_at_load(self):
        symbols = string.ascii_uppercase[: len(NORMAL_PUYO_COLORS) + 1]
        different = [
            (a, b) for index, a in enumerate(symbols) for b in symbols[index + 1 :]
        ]
        with self.assertRaisesRegex(ValueError, "no valid binding"):
            TemplateCatalog.from_dict(
                catalog(
                    rows=tuple(
                        symbols[index : index + GRID_WIDTH].ljust(GRID_WIDTH, ".")
                        for index in range(0, len(symbols), GRID_WIDTH)
                    ),
                    different=different,
                )
            )

    def test_validation_cutoff_is_reported_separately_from_impossible_graph(self):
        with patch("agents.template_catalog._VALIDATION_COLOR_TRIAL_CAP", 1):
            with self.assertRaisesRegex(ValueError, "complexity cap"):
                TemplateCatalog.from_dict(catalog())

    def test_larger_constraint_graph_accounts_for_rejected_color_checks(self):
        symbols = "ABCDEFGHIJKL"
        graph = [(symbols[i], symbols[i + 1]) for i in range(len(symbols) - 1)]
        config = TemplateCatalog.from_dict(
            catalog(rows=(symbols[:6], symbols[6:]), different=graph)
        )
        result = match_templates(
            config,
            board(),
            ((1, 2),),
            node_budget=100,
            binding_budget=8,
            static_binding_cap=8,
        )
        self.assertEqual(result.binding_trials, 8)
        self.assertEqual(result.static_trials, 8)
        self.assertTrue(result.cutoff)
        self.assertTrue(result.static_cutoff)
        self.assertTrue(result.candidates)
        self.assertTrue(all(c.fit_status == "unknown" for c in result.candidates))

    def test_partial_binding_coverage_does_not_claim_no_fit(self):
        config = TemplateCatalog.from_dict(
            catalog(rows=("AB",), different=(("A", "B"),))
        )
        b = board()
        b[-1][:2] = [5, 5]
        result = match_templates(
            config, b, ((1, 2),), node_budget=100, binding_budget=12
        )
        self.assertTrue(result.cutoff)
        self.assertEqual(result.binding_trials, 12)
        self.assertTrue(result.candidates)
        self.assertTrue(all(c.fit_status == "unknown" for c in result.candidates))
        self.assertTrue(
            all(c.reason == "binding_budget_exhausted" for c in result.candidates)
        )

    def test_occupied_only_progress_cannot_prove_fit_and_budget_is_unknown(self):
        config = TemplateCatalog.from_dict(catalog(rows=("A.",), occupied=((1, 0),)))
        b = board(hidden=True)
        zero = match_templates(
            config,
            b,
            ((1, 2),),
            node_budget=0,
            binding_budget=0,
            reachable_mask=(True,) * NUM_ACTIONS,
        )
        self.assertEqual(zero.candidates[0].fit_status, "unknown")
        self.assertEqual(zero.candidates[0].score_source, "static_fallback")
        self.assertTrue(zero.cutoff)
        b[-1][0] = 5  # A can never become a normal color by placing a pair.
        result = match_templates(
            config,
            b,
            ((1, 2),),
            node_budget=100,
            binding_budget=4,
            reachable_mask=(True,) * NUM_ACTIONS,
        )
        self.assertFalse(any(c.fit_status == "fit" for c in result.candidates))

    def test_unstable_visible_board_does_not_prove_hidden_cell_fit(self):
        config = TemplateCatalog.from_dict(catalog())
        b = board(hidden=True)
        b[-2][1] = 2
        result = match_templates(
            config,
            b,
            ((1, 2),),
            node_budget=100,
            binding_budget=4,
            reachable_mask=(True,) * NUM_ACTIONS,
        )
        self.assertTrue(all(c.fit_status == "unknown" for c in result.candidates))

    def test_selection_is_deterministic_and_reselection_requires_fit(self):
        first = catalog()["templates"][0]
        second = copy.deepcopy(first)
        second["id"] = "fixture_b"
        config = TemplateCatalog.from_dict(
            catalog(templates=[second, first], mode="argmax")
        )
        result = match_templates(config, board(), (), node_budget=0, binding_budget=0)
        self.assertEqual(
            TemplateSelector(config, 1).select_initial(result).candidate.template_id,
            "fixture",
        )
        self.assertEqual(
            TemplateSelector(config, 1).reselect(result).reason,
            "free_build_no_proven_fit",
        )
        softmax = TemplateCatalog.from_dict(
            catalog(templates=[second, first], mode="softmax")
        )
        result = match_templates(softmax, board(), (), node_budget=0, binding_budget=0)
        one = TemplateSelector(softmax, 42)
        two = TemplateSelector(softmax, 42)
        picks = [one.select_initial(result) for _ in range(3)]
        self.assertEqual(picks, [two.select_initial(result) for _ in range(3)])
        self.assertEqual([pick.rng_position for pick in picks], [1, 2, 3])
        self.assertTrue(all(0 < pick.probability < 1 for pick in picks))

    def test_existing_provider_injection_uses_only_explicit_public_input(self):
        config = TemplateCatalog.from_dict(catalog())
        own = PublicPlayerState(
            tuple(tuple(row) for row in board(hidden=True)),
            ((1, 2),),
            "control",
            (),
            0,
            False,
            False,
            False,
        )
        opponent = PublicPlayerState(
            tuple(tuple(row) for row in board(hidden=True)),
            (),
            "control",
            (),
            0,
            False,
            False,
            False,
        )
        snapshot = PublicSnapshot(own, opponent, ())
        provider = PublicTemplateCatalogProvider(config)
        registry = ChainStyleRegistry.from_dict(
            {
                "schema_version": "puyo.chain_style_registry.v1",
                "registry_version": "fixture",
                "styles": [
                    {
                        "style_id": "unconstrained",
                        "style_version": "1.0",
                        "provider_id": "builtin.unconstrained.v1",
                    },
                    {
                        "style_id": "fixture",
                        "style_version": "1",
                        "provider_id": provider.provider_id,
                    },
                ],
            }
        )
        evaluator = ChainStyleEvaluator(
            registry,
            ChainStyleSelection("fixture", "1", "soft_preference", 1.0),
            providers={provider.provider_id: provider},
        )
        input_value = PublicTemplateStyleInput(
            snapshot,
            snapshot.digest,
            config.semantic_digest,
            (True,) * NUM_ACTIONS,
            100,
            4,
        )
        result = evaluator.evaluate(input_value)
        self.assertTrue(result.applicable)
        self.assertEqual(result.diagnostics["fit_status"], "fit")
        self.assertTrue(result.diagnostics["witness_actions"])
        with self.assertRaisesRegex(TypeError, "explicit public"):
            evaluator.evaluate(object())
        with self.assertRaisesRegex(ValueError, "stale public"):
            evaluator.evaluate(
                PublicTemplateStyleInput(
                    snapshot,
                    "0" * 64,
                    config.semantic_digest,
                    (True,) * NUM_ACTIONS,
                    100,
                    4,
                )
            )


if __name__ == "__main__":
    unittest.main()
