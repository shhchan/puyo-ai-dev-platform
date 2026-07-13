import json
import tempfile
import unittest
from pathlib import Path

from eval.arena import ArenaResult, MatchResult, summarize_result
from eval.lifecycle_audit import audit_realtime_lifecycle
from eval.v1_7_bootstrap_benchmark import (
    _lineage_manifest,
    build_gates,
    enrich_summary,
)
from eval.v1_7_teacher_replays import compact_teacher_replay
from train.lineage import build_registry, validate_registry


def _match(**overrides):
    values = {
        "seed": 123,
        "winner": "player_0",
        "steps": 4,
        "score_player_0": 40,
        "score_player_1": 0,
        "sent_ojama_player_0": 0,
        "sent_ojama_player_1": 0,
        "received_ojama_player_0": 0,
        "received_ojama_player_1": 0,
        "generated_ojama_player_0": 0,
        "generated_ojama_player_1": 0,
        "canceled_ojama_player_0": 0,
        "canceled_ojama_player_1": 0,
        "max_chain_player_0": 1,
        "max_chain_player_1": 0,
    }
    values.update(overrides)
    return MatchResult(**values)


class TestV17BootstrapBenchmark(unittest.TestCase):
    def test_teacher_replay_compaction_keeps_inputs_and_changed_plan_only(self):
        tick = {
            "tick": 1,
            "inputs": {"player_0": {"press": [], "release": []}},
            "policy_diagnostics": {"player_0": {"plan_id": "plan-1", "schema_version": "schema"}},
            "all_clear_diagnostics": {"players": {}},
            "attack_diagnostics": {"player_0": {}},
            "snapshot_hash": "hash-1",
        }
        replay = {
            "format": "puyo-realtime-match-v1",
            "seed": 1,
            "ticks": [tick, {**tick, "tick": 2, "snapshot_hash": "hash-2"}],
            "expected_final_hash": "hash-2",
        }

        compact = compact_teacher_replay(replay, teacher_agent="player_0")

        self.assertEqual(compact["ticks"][0]["policy_diagnostics"]["player_0"]["plan_id"], "plan-1")
        self.assertEqual(compact["ticks"][1]["policy_diagnostics"], {})
        self.assertEqual(compact["ticks"][1]["inputs"], tick["inputs"])

    def test_lifecycle_audit_detects_initial_false_positive_and_double_consumption(self):
        initial = {
            "players": {
                "player_0": {
                    "board_empty": True,
                    "all_clear_achieved": True,
                    "all_clear_bonus_pending": False,
                },
                "player_1": {
                    "board_empty": True,
                    "all_clear_achieved": False,
                    "all_clear_bonus_pending": False,
                },
            }
        }
        ticks = [
            {
                "all_clear_diagnostics": {
                    "players": {
                        "player_0": {
                            "all_clear_achieved": False,
                            "all_clear_bonus_pending": False,
                            "all_clear_bonus_consumed": True,
                        },
                        "player_1": {},
                    }
                },
                "attack_diagnostics": {
                    "player_0": {
                        "all_clear_bonus_consumed": True,
                        "generated": 31,
                        "canceled": 5,
                        "outgoing": 26,
                    },
                    "player_1": {},
                },
            }
        ]

        report = audit_realtime_lifecycle(
            initial_all_clear_diagnostics=initial,
            ticks=ticks,
        )

        player = report["players"]["player_0"]
        self.assertEqual(player["initial_empty_false_positives"], 1)
        self.assertEqual(player["double_consumptions"], 1)
        self.assertEqual(player["bonus_attack_outgoing"], 26)

    def test_policy_relative_summary_and_hard_gates_pass(self):
        result = ArenaResult(
            matches=(
                _match(),
                _match(
                    winner="player_1",
                    policy_a_side="player_1",
                    max_chain_player_0=0,
                    max_chain_player_1=1,
                ),
            )
        )
        summary = summarize_result(
            result,
            label="direct",
            policy_a="v1_7_bootstrap_manager",
            policy_b="v1_7_analyzer_manager",
            checkpoint_a="checkpoint.pt",
            checkpoint_b=None,
            games=2,
            seed=123,
            max_steps=40,
        )
        enrich_summary(summary, result)
        gui_lifecycle = {
            "player_0": {"initial_empty_false_positives": 0, "double_consumptions": 0},
            "player_1": {"initial_empty_false_positives": 0, "double_consumptions": 0},
        }

        gates = build_gates(
            checkpoint_evidence={"validation_errors": []},
            scenario_report={"summary": {"scenarios": 24, "passed": 24, "failed": 0}},
            summaries=[summary],
            gui_qa={
                "result": {"completed": True, "execution_completed": True},
                "quality_gate": {"enabled": True, "passed": True},
                "diagnostics": {"controller": {"player_0": {"decisions_activated": 1}}},
            },
            gui_verification={
                "verified": True,
                "final_hash": "abc",
                "lifecycle": {"players": gui_lifecycle},
            },
            lineage_issues=[],
        )

        self.assertTrue(all(gate["passed"] for gate in gates.values()))
        self.assertEqual(summary["max_chain_policy_a"], 1)
        self.assertEqual(summary["self_choke_rate_policy_a"], 0.0)

    def test_lineage_manifest_explicitly_records_feature_schema_difference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "benchmark_summary.json").write_text("{}", encoding="utf-8")
            evidence = {
                "sha256": "a" * 64,
                "run_id": "bootstrap-run",
                "git_commit": "abc123",
                "seed": 128,
                "global_step": 60,
                "path": "runs/bootstrap.pt",
                "feature_contract": {"context_dim": 77, "tactic_dim": 56, "preview_dim": 23},
                "checkpoint_metadata": {"schemas": {}},
                "dataset": {
                    "dataset_id": "dataset-1",
                    "manifest_sha256": "b" * 64,
                    "compatibility": {"status": "native"},
                },
            }
            manifest = _lineage_manifest(
                output_dir=root,
                checkpoint_evidence=evidence,
                checkpoint_payload={"checkpoint_schema": {"trainer_name": "v1_7_manager_bootstrap"}},
                promotion_passed=True,
            )
            manifest_path = root / "lineage_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            registry = build_registry([manifest_path])

            self.assertEqual(validate_registry(registry), [])
            schema = registry.nodes["feature_schema:v1.7.1"]
            self.assertIn("ordered context/tactic/preview", schema.metadata["difference_from_v1_7_0"])


if __name__ == "__main__":
    unittest.main()
