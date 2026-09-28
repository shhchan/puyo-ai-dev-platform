"""Check the paired PUYO-271 evidence without rerunning native search."""

import unittest

from eval.puyo_271_regression import ROOT, compare_saved_runs, verify


class Puyo271IntegratedEvidenceTest(unittest.TestCase):
    def test_after_raw_and_replayed_scores(self):
        before = ROOT / "docs/benchmarks/puyo-271-regression/before"
        after = ROOT / "docs/benchmarks/puyo-271-regression/after"
        verify(before)
        verify(after)
        report = compare_saved_runs(before, after)
        self.assertEqual(len(report["paired"]), 6)
        for pair in report["paired"]:
            for phase in ("before", "after"):
                run = pair[phase]
                self.assertEqual(len(run["chains"]), run["placements"])
                self.assertGreaterEqual(run["actual_score_replayed"], 0)
                self.assertEqual(run["decision_seconds"]["n"], len(run["decisions"]))
                if pair["policy"] == "nextgen":
                    self.assertEqual(run["receipt_nonactivated_count"], 0)
                    if phase == "after":
                        self.assertEqual(run["candidate_witness_gap_count"], 0)
                    else:
                        self.assertIsNone(run["candidate_witness_gap_count"])
        seed55 = next(row["after"] for row in report["paired"]
                      if row["policy"] == "nextgen" and row["seed"] == 55)
        self.assertEqual(seed55["template_completed_at_decision"], 13)
        self.assertEqual(seed55["decisions"][-1]["probe_status"], "unknown")
        self.assertFalse(seed55["decisions"][-1]["witness_actions"])


if __name__ == "__main__":
    unittest.main()
