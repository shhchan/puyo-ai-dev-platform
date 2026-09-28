"""Native cohort integrity without running the 60-identity experiment."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.nextgen_gates import REPEATS, SEEDS, THRESHOLDS, digest
from eval.nextgen_safe_build_gate import (
    SCHEMA,
    cohort_row,
    finalize,
    load_manifest,
    write_new,
)


class NativeGateTests(unittest.TestCase):
    def manifest(self):
        config = {"seeds": list(SEEDS), "repeats": list(REPEATS), "thresholds": THRESHOLDS,
                  "runtime_information": "public_only", "reference_profile_calibrated": True}
        value = {"schema": SCHEMA, "source": {}, "build": {}, "config": config,
                 "config_sha256": digest(config)}
        value["sha256"] = digest(value)
        return value

    def row(self, manifest, seed=123, repeat=1):
        return {"manifest_sha256": manifest["sha256"], "seed": seed, "repeat": repeat,
                "termination": "placements", "placements": 40, "max_chain": 10,
                "premature": 0, "game_over": False, "semantic": {"chains": [0] * 39 + [10]},
                "semantic_digest": digest({"chains": [0] * 39 + [10]}), "ledger": [], "rows": [],
                "chains": [0] * 39 + [10], "errors": []}

    def test_checksum_source_and_overwrite_are_not_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = self.manifest()
            write_new(output / "manifest.json", manifest)
            self.assertEqual(load_manifest(output), manifest)
            with self.assertRaises(FileExistsError):
                write_new(output / "manifest.json", manifest)
            with patch("eval.nextgen_safe_build_gate.source_identity", return_value={"changed": True}), self.assertRaisesRegex(ValueError, "changed"):
                load_manifest(output, execution=True)
            manifest["config"]["placements"] = 1
            (output / "manifest.json").unlink()
            write_new(output / "manifest.json", manifest)
            with self.assertRaisesRegex(ValueError, "checksum"):
                load_manifest(output)

    def test_cross_manifest_or_corrupt_trajectory_rejected(self):
        manifest = self.manifest()
        row = self.row(manifest)
        cohort_row(row, manifest)
        row["manifest_sha256"] = "other"
        with self.assertRaisesRegex(ValueError, "another"):
            cohort_row(row, manifest)
        row = self.row(manifest)
        row["semantic"]["chains"] = [1]
        with self.assertRaisesRegex(ValueError, "checksum"):
            cohort_row(row, manifest)

    def test_batch_difference_changes_repeat_digest(self):
        manifest = self.manifest()
        first = self.row(manifest)
        second = copy.deepcopy(first)
        first["ledger"] = [{"selection": {"batch_digest": "a"}}]
        second["ledger"] = [{"selection": {"batch_digest": "b"}}]
        self.assertNotEqual(cohort_row(first, manifest)["semantic_digest"],
                            cohort_row(second, manifest)["semantic_digest"])

    def test_summary_cannot_replace_actual_trajectory_chains(self):
        manifest = self.manifest()
        row = self.row(manifest)
        row["chains"][-1] = 11
        row["max_chain"] = 11
        with self.assertRaisesRegex(ValueError, "recorded trajectory"):
            cohort_row(row, manifest)

    def test_all_sixty_safe_runs_cannot_fabricate_other_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            manifest = self.manifest()
            write_new(output / "manifest.json", manifest)
            for seed in SEEDS:
                for repeat in REPEATS:
                    write_new(output / f"seed-{seed}-repeat-{repeat}.json.gz", self.row(manifest, seed, repeat))
            with patch("eval.nextgen_safe_build_gate.source_identity", return_value={}), patch("builtins.print"):
                report = finalize(output)
            self.assertEqual(report["safe_build"]["completed_runs"], 60)
            self.assertEqual(report["gates"]["G2"]["status"], "BLOCKED")
            self.assertIn("G1_passed", report["gates"]["G2"]["failed_conditions"])
            self.assertIn("required_threat_fixtures", report["gates"]["G2"]["failed_conditions"])
            self.assertFalse(report["long_training_allowed"])


if __name__ == "__main__":
    unittest.main()
