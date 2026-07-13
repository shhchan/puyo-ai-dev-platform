import copy
import json
import tempfile
import unittest
from pathlib import Path

from eval.analyzer_scenarios import SCENARIO_SCHEMA_VERSION
from puyo_env.actions import NUM_ACTIONS
from train.v1_7_bootstrap_dataset import (
    MANIFEST_SCHEMA_VERSION,
    SAMPLE_SCHEMA_VERSION,
    build_bootstrap_dataset,
    load_bootstrap_split,
    validate_bootstrap_dataset,
)


ROOT = Path(__file__).resolve().parents[1]
GUI_REPLAY = ROOT / "docs/benchmarks/puyo-v1-7-0-smoke/gui_qa_replay.json"


def _first_diagnostics():
    replay = json.loads(GUI_REPLAY.read_text(encoding="utf-8"))
    for tick in replay["ticks"]:
        diagnostics = tick.get("policy_diagnostics", {}).get("player_0", {})
        if diagnostics.get("schema_version"):
            return copy.deepcopy(diagnostics)
    raise AssertionError("tracked GUI replay has no v1.7 diagnostics")


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


class TestV17BootstrapDataset(unittest.TestCase):
    def test_replay_decisions_are_deduplicated_and_build_is_reproducible(self):
        diagnostics = _first_diagnostics()
        replay = {
            "format": "puyo-realtime-match-v1",
            "ticks": [
                {
                    "tick": 1,
                    "policy_diagnostics": {"player_0": diagnostics},
                    "attack_diagnostics": {},
                },
                {
                    "tick": 2,
                    "policy_diagnostics": {"player_0": diagnostics},
                    "attack_diagnostics": {},
                },
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "replay.json"
            source.write_text(json.dumps(replay), encoding="utf-8")
            first = build_bootstrap_dataset(
                [source],
                root / "first",
                validation_ratio=0.0,
                include_default_scenarios=False,
            )
            second = build_bootstrap_dataset(
                [source],
                root / "second",
                validation_ratio=0.0,
                include_default_scenarios=False,
            )

            self.assertEqual(first, second)
            self.assertEqual(first["schema_version"], MANIFEST_SCHEMA_VERSION)
            self.assertEqual(first["sample_schema_version"], SAMPLE_SCHEMA_VERSION)
            self.assertEqual(first["counts"]["train"], 1)
            self.assertEqual(first["counts"]["duplicates_removed"], 1)
            self.assertEqual(validate_bootstrap_dataset(root / "first"), [])
            samples = load_bootstrap_split(root / "first", "train")
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["compatibility"]["status"], "current")
            self.assertTrue(samples[0]["compatibility"]["training_eligible"])
            self.assertTrue(all(samples[0]["compatibility"]["feature_presence"].values()))

            train_path = root / "first" / "train.jsonl"
            train_path.write_text(train_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.assertIn(
                "train split checksum mismatch",
                validate_bootstrap_dataset(root / "first"),
            )

    def test_migration_legacy_and_invalid_actions_are_audited(self):
        current = _first_diagnostics()
        migrated = copy.deepcopy(current)
        migrated_input = migrated["analyzer"]["input"]
        migrated["lifecycle_features"] = {
            side: {
                field: migrated_input[side][field]
                for field in (
                    "score_carry",
                    "all_clear_achieved",
                    "all_clear_bonus_pending",
                    "all_clear_bonus_consumed",
                )
            }
            for side in ("own", "opponent")
        }
        del migrated_input["own"]["score_carry"]

        legacy = copy.deepcopy(current)
        for side in ("own", "opponent"):
            for field in (
                "score_carry",
                "all_clear_achieved",
                "all_clear_bonus_pending",
                "all_clear_bonus_consumed",
            ):
                legacy["analyzer"]["input"][side].pop(field)
        old_schema = copy.deepcopy(current)
        old_schema["analyzer"]["input"]["schema_version"] = "puyo.state_analyzer.input.legacy"

        rejected = copy.deepcopy(current)
        rejected["worker"]["result"]["action"] = NUM_ACTIONS
        illegal = copy.deepcopy(current)
        for row_index, row in enumerate(illegal["analyzer"]["input"]["own"]["board"]):
            row[0] = "RED" if row_index % 2 == 0 else "BLUE"
        illegal["worker"]["result"]["action"] = 0
        old_teacher = {
            "scenario": "legacy-safe-build",
            "category": "safe_build",
            "board": [],
            "next_pairs": [],
            "manager_features": [0.0],
            "selected_profile_id": 0,
            "selected_profile_name": "build_large",
            "selected_action_id": 0,
            "counterfactuals": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "mixed.json"
            source.write_text(
                json.dumps(
                    [current, migrated, legacy, old_schema, rejected, illegal, old_teacher]
                ),
                encoding="utf-8",
            )
            manifest = build_bootstrap_dataset(
                [source],
                root / "dataset",
                validation_ratio=0.0,
                include_default_scenarios=False,
            )

            self.assertEqual(manifest["counts"]["current"], 1)
            self.assertEqual(manifest["counts"]["migrated"], 1)
            self.assertEqual(manifest["counts"]["legacy"], 3)
            self.assertEqual(manifest["counts"]["rejected"], 2)
            self.assertIn(
                "lifecycle_features.own.score_carry",
                " ".join(manifest["compatibility"]["migration_sources"]),
            )
            self.assertEqual(
                manifest["compatibility"]["legacy_reasons"]["legacy_manager_feature_schema"],
                1,
            )
            self.assertEqual(
                manifest["compatibility"]["legacy_reasons"][
                    "unsupported_analyzer_input_schema:puyo.state_analyzer.input.legacy"
                ],
                1,
            )
            self.assertTrue(
                any(
                    reason.startswith("validation_error:teacher action")
                    for reason in manifest["compatibility"]["rejection_reasons"]
                )
            )
            self.assertTrue(
                any(
                    "illegal for the analyzer snapshot" in reason
                    for reason in manifest["compatibility"]["rejection_reasons"]
                )
            )
            migrated_sample = next(
                sample
                for sample in _read_jsonl(root / "dataset" / "train.jsonl")
                if sample["compatibility"]["status"] == "migrated"
            )
            self.assertFalse(
                migrated_sample["compatibility"]["source_feature_presence"]["own.score_carry"]
            )
            self.assertEqual(
                migrated_sample["analyzer"]["input"]["own"]["score_carry"],
                current["analyzer"]["input"]["own"]["score_carry"],
            )

    def test_all_puyo_153_scenarios_are_versioned_validation_samples(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "dataset"
            manifest = build_bootstrap_dataset([], root)
            samples = _read_jsonl(root / "validation.jsonl")
            by_name = {
                sample["outcomes"]["scenario"]["name"]: sample
                for sample in samples
            }

            self.assertEqual(manifest["validation_scenarios"]["schema_version"], SCENARIO_SCHEMA_VERSION)
            self.assertEqual(manifest["validation_scenarios"]["count"], 24)
            self.assertEqual(len(by_name), 24)
            initial = by_name["initial_empty_board_has_no_all_clear_event"]
            pending = by_name["pending_bonus_survives_non_clearing_turn"]
            consumed = by_name["consumed_bonus_is_not_applied_again"]
            boundary = by_name["score_total_71_retains_one"]
            self.assertFalse(initial["analyzer"]["input"]["own"]["all_clear_achieved"])
            self.assertTrue(pending["analyzer"]["input"]["own"]["all_clear_bonus_pending"])
            self.assertTrue(consumed["analyzer"]["input"]["own"]["all_clear_bonus_consumed"])
            self.assertEqual(boundary["analyzer"]["input"]["own"]["score_carry"], 31)
            self.assertTrue(all(initial["compatibility"]["feature_presence"].values()))
            self.assertEqual(validate_bootstrap_dataset(root), [])


if __name__ == "__main__":
    unittest.main()
