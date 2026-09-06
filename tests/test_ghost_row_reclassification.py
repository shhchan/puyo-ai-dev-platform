import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from agents.deep_chain_builder import load_deep_chain_builder_config
from agents.deep_chain_native_transition import NativeCompactBatchClient
from eval.deep_chain_builder_benchmark import (
    CANONICAL_TARGET_CHAIN_COUNT,
    RUN_SCHEMA_VERSION,
    TICKET,
    _load_completed_runs,
    _protect_historical_output,
)
from eval.ghost_row_reclassification import SOURCE_DIR, audit_run


class TestHistoricalParityIsolation(unittest.TestCase):
    def test_old_artifact_directories_and_children_are_read_only(self):
        for name in (
            "puyo-189-deep-chain-builder-baseline",
            "puyo-204-deep-chain-native-baseline",
        ):
            for suffix in ("", "/nested"):
                with self.assertRaisesRegex(ValueError, "read-only"):
                    _protect_historical_output(
                        Path("docs/benchmarks") / (name + suffix)
                    )

    def test_new_runner_cannot_resume_legacy_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "runs").mkdir()
            (target / "runs/seed-123-repeat-01.json").write_text(
                json.dumps(
                    {
                        "schema_version": RUN_SCHEMA_VERSION,
                        "ticket": TICKET,
                        "run_id": "seed-123-repeat-01",
                        "target_chain_count": CANONICAL_TARGET_CHAIN_COUNT,
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "parity contract mismatch"):
                _load_completed_runs(target, load_deep_chain_builder_config().benchmark)


@unittest.skipIf(
    importlib.util.find_spec("_puyo_deep_chain_native") is None,
    "release native extension is not installed",
)
class TestGhostRowReclassification(unittest.TestCase):
    def test_raw_fingerprints_reconstruct_all_eight_seed136_failures(self):
        result = audit_run(
            SOURCE_DIR / "seed-136-repeat-01.json", NativeCompactBatchClient()
        )
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["decisions"], 40)
        self.assertEqual(
            [item["turn"] for item in result["mismatches"]], list(range(32, 40))
        )
        for record in result["mismatches"]:
            self.assertEqual(
                record["classification"], "ghost_row_lost_at_observation_boundary"
            )
            self.assertFalse(record["historical_full_parity_passed"])
            self.assertEqual(
                record["cell_differences"],
                [{"x": 1, "y": 13, "predicted": "EMPTY", "actual": "BLUE"}],
            )
            self.assertTrue(record["corrected_python_authoritative_passed"])

    def test_unknown_fingerprint_and_lower_board_difference_are_never_explained(self):
        original = json.loads((SOURCE_DIR / "seed-136-repeat-01.json").read_text())
        for mutation in ("fingerprint", "public_board", "verdict"):
            with (
                self.subTest(mutation=mutation),
                tempfile.TemporaryDirectory() as directory,
            ):
                run = copy.deepcopy(original)
                record = run["records"][32]
                if mutation == "fingerprint":
                    record["plan"]["steps"][0]["state_fingerprint"] = "unexplained"
                elif mutation == "public_board":
                    record["board_after"][0][0] = "BLUE"
                else:
                    record["parity"]["passed"] = True
                path = Path(directory) / "run.json"
                path.write_text(json.dumps(run))
                result = audit_run(path, NativeCompactBatchClient())
                self.assertTrue(result["errors"])
                if mutation != "verdict":
                    self.assertEqual(
                        result["mismatches"][0]["classification"], "unexplained"
                    )


if __name__ == "__main__":
    unittest.main()
