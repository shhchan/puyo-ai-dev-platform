"""Production catalog outcomes, including source-shape and resolution regressions."""

import unittest

from eval.nextgen_template_fixtures import run_cases


class ProductionTemplateCatalogTest(unittest.TestCase):
    def test_fixed_public_streams_replay_to_declared_outcomes(self):
        report = run_cases()
        completed = [case for case in report["cases"] if case["complete"]]
        self.assertEqual({case["selection"][0] for case in completed}, {"gtr", "daa", "persian"})
        self.assertTrue(all(case["decisions"] <= 14 for case in completed))
        self.assertEqual(len([case for case in report["cases"] if not case["complete"]]), 2)


if __name__ == "__main__":
    unittest.main()
