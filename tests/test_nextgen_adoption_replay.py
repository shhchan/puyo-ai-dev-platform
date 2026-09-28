"""Reproduce the saved activation/lock boundary without rerunning search."""

import gzip
import json
import unittest
from pathlib import Path

from agents.template_catalog import load_template_catalog
from eval.nextgen_adoption_replay import replay_adoptions
from eval.nextgen_asset_diagnostic import audit_run

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/benchmarks/puyo-266-safe-build/asset-quality-20260927/native-g2"


class AdoptionReplayTests(unittest.TestCase):
    def raw(self, seed):
        return json.loads(gzip.decompress((CORPUS / f"seed-{seed}-repeat-1.json.gz").read_bytes()))

    def test_seed_137_activated_root_does_not_prove_realized_placement(self):
        raw = self.raw(137)
        result = replay_adoptions(raw)
        self.assertTrue(result["final_hash_matches"])
        self.assertEqual(result["lock_mismatch_decisions"], [34])
        decision = result["records"][33]
        self.assertEqual(decision["receipt"]["executed_action"], 12)
        self.assertEqual(decision["expected_pose"], [3, "RIGHT"])
        self.assertEqual((decision["lock"]["axis_x"], decision["lock"]["rotation"]), (2, "RIGHT"))
        self.assertEqual(decision["public_predicted_chain"], 12)
        self.assertEqual(decision["offline_full_board_predicted_chain"], 12)
        self.assertEqual(decision["resolution"]["chain_count"], 1)
        # Do not attribute the intended 12-chain's erased cells to this run.
        with self.assertRaisesRegex(ValueError, "actual resolution"):
            audit_run(raw, load_template_catalog(ROOT / "train/config/nextgen_templates.yaml"))

    def test_seed_148_natural_gravity_changes_input_path(self):
        result = replay_adoptions(self.raw(148))
        self.assertEqual(result["lock_mismatch_decisions"], [21, 25])
        decision = result["records"][20]
        self.assertEqual(decision["expected_pose"], [4, "LEFT"])
        self.assertEqual(decision["lock"]["axis_x"], 3)
        self.assertEqual(decision["public_predicted_chain"], 0)
        self.assertEqual(decision["offline_full_board_predicted_chain"], 0)
        self.assertEqual(decision["resolution"]["chain_count"], 8)
        edge = next(row for row in decision["path"] if row["tick"] == 1200)
        self.assertEqual((edge["before"][1], edge["after"][1]), (11, 9))
        self.assertEqual(edge["fired"], ["DOWN"])


if __name__ == "__main__":
    unittest.main()
