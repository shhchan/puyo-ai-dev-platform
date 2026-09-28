"""PUYO-271 frozen public counterexamples and before-run evidence."""

import unittest

from eval.puyo_271_regression import ROOT, replay_fixtures, verify


class Puyo271RegressionTest(unittest.TestCase):
    def test_synthetic_counterexamples_and_visibility_boundaries(self):
        result = replay_fixtures()
        persian = {row["id"]: row for row in result["persian_counterexamples"]}
        self.assertEqual(
            [persian[name]["chain_count"] for name in ("persian_flat_three", "persian_l_corner")],
            [0, 1],
        )
        self.assertGreater(persian["persian_l_corner"]["quiet_legal_alternatives"], 0)
        self.assertEqual(persian["persian_l_corner"]["shape_preserving_quiet_alternatives"], 0)
        self.assertEqual(persian["persian_l_corner"]["before_static_conflicts"], 0)
        self.assertEqual(persian["persian_l_corner"]["static_conflicts"], 1)
        self.assertFalse(persian["persian_l_corner"]["selected_binding_compatible"])
        survival = result["survival_counterexamples"][0]
        self.assertEqual(survival["safe"], {"chain_count": 1, "game_over": False})
        self.assertEqual(survival["fatal"], {"chain_count": 0, "game_over": True})
        self.assertEqual(
            {row["template_id"] for row in result["template_rollouts"] if row["completed"]},
            {"gtr", "daa", "persian"},
        )
        probes = {row["id"]: row for row in result["visibility_probes"]}
        self.assertTrue(probes["known_current"]["has_fit"])
        self.assertFalse(probes["unknown_current"]["has_fit"])
        self.assertTrue(probes["zero_node_cutoff"]["cutoff"])

    def test_saved_before_run_is_complete_and_untampered(self):
        verify(ROOT / "docs/benchmarks/puyo-271-regression/before")


if __name__ == "__main__":
    unittest.main()
