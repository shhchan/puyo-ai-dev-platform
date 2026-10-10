"""Small runtime and adversarial evidence tests; never the formal cohort."""

import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from agents.deep_chain_builder import DeepChainBuilderPolicy, DeepChainBuilderProfile
from eval import nextgen_single_quality_gate as gate
from eval.nextgen_single_quality_runtime import (
    POLICIES,
    SingleQualityMatch,
    classify_small_clear,
    make_policy,
    measure,
    public_state,
    reference_input,
    replay_audit,
    semantic_payload,
)
from src.core.constants import PuyoColor
from src.core.puyo import Puyo


class SingleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {}
        for kind in POLICIES:
            if kind == "deep_chain_builder":
                policy = DeepChainBuilderPolicy(backend="python", profile=DeepChainBuilderProfile(
                    name="unit", version="1", purpose="bounded integration", depth=1,
                    width=2, scenarios=1, max_expanded_nodes=44))
            else:
                policy = make_policy(kind, backend="python", smoke=True)
            raw = measure(SingleQualityMatch(seed=55), policy, kind, placements=2, max_ticks=1000)
            gate.complete_evidence(raw, SingleQualityMatch(seed=55))
            cls.runs[kind] = raw

    def test_both_policies_resolve_and_replay_actual_locks(self):
        for kind, raw in self.runs.items():
            with self.subTest(policy=kind):
                self.assertEqual(len(raw["chains"]), 2)
                self.assertEqual(raw["audit"]["lock_mismatches"], [])
                self.assertEqual(raw["audit"]["unlocked_requests"], [])
                self.assertEqual(raw["errors"], [])
                self.assertEqual(raw["integrity_issues"], ["incomplete_window"])
                self.assertIsNone(raw["checkpoint40"])
                self.assertEqual(len(raw["receipts"]), 2)

    def test_corrupt_hash_input_actual_lock_and_checkpoint_are_rejected(self):
        baseline = self.runs["deep_chain_builder"]
        for mutate in (
            lambda r: r["ticks"][0].update(state_hash="bad"),
            lambda r: r["events"][0].update(axis_x=99),
            lambda r: r["rows"][0]["public"].update(score_carry=66),
        ):
            value = copy.deepcopy(baseline)
            mutate(value)
            with self.assertRaises(ValueError):
                replay_audit(value, SingleQualityMatch(seed=55))
        value = copy.deepcopy(baseline)
        value["checkpoint40"] = {"placements": 40}
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            gate.integrity_issues(value)

    def test_private_hidden_and_future_counterfactual_cannot_enter_reference(self):
        match = SingleQualityMatch(seed=55)
        while match.public_snapshot().own.phase != "control":
            match.step()
        public, inference = match.public_snapshot().own, match.public_board_inference()
        kwargs = {"tick": match.tick, "score": 0, "last_chain_end_score": 0, "last_chain_score_delta": 0}
        before = reference_input(public, inference, (True,) * 22, **kwargs)
        game = match.player_states["player_0"].simulator.game
        game.field.grid[12][0] = Puyo(PuyoColor.PURPLE)
        game.field.grid[13][2] = Puyo(PuyoColor.RED)
        game.puyo_sequence = object()
        game.next_puyo_queue.extend([(Puyo(PuyoColor.YELLOW), Puyo(PuyoColor.YELLOW))] * 5)
        after = reference_input(match.public_snapshot().own, match.public_board_inference(),
                                (True,) * 22, **kwargs)
        for key in ("board", "ghost_row", "next_pairs"):
            np.testing.assert_array_equal(before[0][key], after[0][key])
        self.assertEqual(before[1], after[1])
        self.assertEqual(set(before[0]), {"board", "ghost_row", "next_pairs", "schema_version"})
        self.assertNotIn("simulator", before[1])
        self.assertFalse(before[0]["ghost_row"].any())
        with self.assertRaisesRegex(ValueError, "unavailable"):
            reference_input(public, replace(inference, status="unknown", hidden_rows=((None,) * 6,) * 2),
                            (True,) * 22, **kwargs)

    def test_public_inferred_hidden_is_not_silently_erased(self):
        from agents import nextgen_contracts as c
        row = self.runs["deep_chain_builder"]["rows"][0]
        public = c.PublicPlayerState.from_dict(row["public"])
        inference = c.PublicBoardInference.from_dict(row["inference"])
        inferred = replace(inference, hidden_rows=((1, 0, 0, 0, 0, 0), (0, 2, 0, 0, 0, 0)))
        obs, _ = reference_input(public, inferred, row["reachable_mask"], tick=row["tick"],
                                 score=0, last_chain_end_score=0, last_chain_score_delta=0)
        self.assertEqual(obs["board"][0, 0, 0], 1)
        self.assertEqual(obs["ghost_row"][1, 1], 1)
        self.assertEqual(public_state(public, inferred).cell_count, 2)

    def test_wall_time_excluded_and_actual_decision_included_in_repeat_digest(self):
        raw = copy.deepcopy(self.runs["deep_chain_builder"])
        initial = gate.digest(semantic_payload(raw))
        raw["rows"][0]["seconds"] = 1000
        raw["receipts"][0]["policy_elapsed_seconds"] = 1000
        self.assertEqual(initial, gate.digest(semantic_payload(raw)))
        raw["rows"][0]["action"] += 1
        self.assertNotEqual(initial, gate.digest(semantic_payload(raw)))

    def test_unknown_public_small_clear_never_becomes_survival_exception(self):
        row = copy.deepcopy(self.runs["deep_chain_builder"]["rows"][0])
        row["inference"].update(status="unknown", hidden_rows=[[None] * 6] * 2)
        self.assertEqual(classify_small_clear(row, 1)["status"], "unknown")

    def test_public_quiet_witness_marks_an_unnecessary_single_clear(self):
        from agents import nextgen_contracts as c
        row = copy.deepcopy(self.runs["deep_chain_builder"]["rows"][0])
        public = c.PublicPlayerState.from_dict(row["public"])
        board = [list(v) for v in public.visible_board]
        board[-1][:3] = [1, 1, 1]
        public = replace(public, visible_board=tuple(map(tuple, board)), known_pieces=((1, 1),) * 3)
        row.update(public=public.to_dict(), action=0, reachable_mask=[True] * 22)
        row["inference"]["visible_digest"] = c.semantic_digest(public.visible_board)
        result = classify_small_clear(row, 1)
        self.assertEqual(result["status"], "unjustified")
        self.assertEqual(len(result["witness"]), 3)
        self.assertIsInstance(result["terminal_action"], int)

    def test_quota_adoption_and_certainty_fail_closed(self):
        baseline = self.runs["nextgen_tactic_manager"]
        for mutate, expected in (
            (lambda r: r["rows"][0]["counters"].update(shared_nodes=600001), "quota_exceeded"),
            (lambda r: r["receipts"][0].update(executed_action=99), "receipt_adoption_mismatch"),
            (lambda r: r["rows"][0]["inference"].update(status="unknown"), "public_inference_unknown"),
        ):
            value = copy.deepcopy(baseline)
            mutate(value)
            self.assertIn(expected, gate.integrity_issues(value))

    def test_resolution_window_reaches_46_and_preserves_40_checkpoint(self):
        # Exercise the actual loop/termination contract without expensive search.
        base = SingleQualityMatch(seed=55)
        match = SimpleNamespace(
            tick=0, ending=False, finished=False, player_states=base.player_states,
            public_snapshot=base.public_snapshot, public_board_inference=base.public_board_inference,
            replay_rules=base.replay_rules, state_hash=lambda: str(match.tick),
        )

        def step(_):
            tick = match.tick
            match.tick += 1
            event = SimpleNamespace(type="resolution_complete", tick=tick,
                                    data={"chain_count": 10 if tick == 44 else 0})
            return SimpleNamespace(tick=tick, player_results={"player_0": SimpleNamespace(events=[event])})

        match.step = step
        controller = SimpleNamespace(
            nextgen_scheduler=None, next_input=lambda *a, **k: SimpleNamespace(to_json=dict),
            diagnostics=SimpleNamespace(last_decision=None, to_dict=dict),
        )
        checkpoints = []
        with patch("eval.nextgen_single_quality_runtime.RealtimePolicyController", return_value=controller):
            raw = measure(match, SimpleNamespace(last_context=None), POLICIES[0],
                          progress=lambda r: checkpoints.append(r["final"]["placements"]))
        self.assertEqual(raw["final"]["placements"], 46)
        self.assertEqual(raw["checkpoint40"]["max_chain"], 0)
        self.assertEqual(raw["checkpoint40"]["tick"], 40)
        self.assertEqual(raw["final"]["max_chain"], 10)
        self.assertEqual(checkpoints, list(range(47)))


class SingleSummaryTests(unittest.TestCase):
    def test_resume_preserves_completed_failed_and_interrupted_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            complete = output / gate.artifact_name(POLICIES[0], 0, 1)
            gate.write_new(complete, {"saved": True})
            interrupted = output / (gate.artifact_name(POLICIES[0], 0, 2).removesuffix(".json.gz") + ".progress.json.gz")
            gate.write_new(interrupted, {"partial": True})
            failure = output / (gate.artifact_name(POLICIES[1], 0, 1).removesuffix(".json.gz") + ".failure.json")
            gate.write_new(failure, {"error": "original"})
            with (patch.object(gate, "PATTERN_IDS", (0,)),
                  patch.object(gate, "load_manifest", return_value={"sha256": "fixed"}),
                  patch.object(gate, "validated_row") as validate,
                  patch.object(gate.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
                  patch("builtins.print")):
                gate.run_all(output)
            validate.assert_called_once()
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][-1], "2")
            self.assertEqual(gate.read(failure), {"error": "original"})
            self.assertEqual(gate.read(interrupted), {"partial": True})
            self.assertEqual(gate.read(interrupted.with_name(interrupted.name.replace(".progress.json.gz", ".failure.json")))["error_type"], "InterruptedWorker")

    def rows(self):
        return [{"policy": p, "pattern_id": i, "repeat": r, "termination": "placements",
                 "final": {"max_chain": 10, "placements": 46}, "checkpoint40": {"max_chain": 10},
                 "game_over": False, "small_clears": {"necessary_survival": 0, "unjustified": 0, "unknown": 0},
                 "suffocation": "none", "semantic_digest": str(i), "integrity_issues": [],
                 "decision_seconds": [.1]} for p in POLICIES for i in gate.PATTERN_IDS for r in gate.REPEATS]

    def test_frozen_cohort_has_120_identities_and_cannot_enable_training(self):
        self.assertEqual(gate.declaration()["pattern_ids"], list(gate.PATTERN_IDS))
        rows = self.rows()
        self.assertEqual(len(rows), 120)
        report = gate.summarize(rows)
        self.assertEqual(report["single_quality_status"], "PASS")
        self.assertEqual(report["G2"], "BLOCKED")
        self.assertFalse(report["long_training_allowed"])
        # A weaker reference is a comparison result, not a new nextgen threshold.
        for r in rows:
            if r["policy"] == POLICIES[1]:
                r["final"]["max_chain"] = 8
        self.assertEqual(gate.summarize(rows)["single_quality_status"], "PASS")

    def test_missing_reference_repeat_unknown_or_bad_integrity_blocks(self):
        for mutate in (
            lambda r: r.pop(),
            lambda r: r[1].update(semantic_digest="different"),
            lambda r: r[0]["small_clears"].update(unknown=1),
            lambda r: r[-1]["integrity_issues"].append("quota_exceeded"),
        ):
            rows = self.rows()
            mutate(rows)
            self.assertEqual(gate.summarize(rows)["single_quality_status"], "BLOCKED")

    def test_failed_runs_not_excluded_from_mean_and_zero_thresholds(self):
        rows = self.rows()
        rows[0].update(termination="game_over", game_over=True, suffocation="avoidable")
        rows[0]["final"]["max_chain"] = 0
        rows[0]["small_clears"]["unjustified"] = 1
        report = gate.summarize(rows)
        quality = report["policies"][POLICIES[0]]
        self.assertAlmostEqual(quality["mean_maximum_actual_chain"], 290 / 30)
        self.assertEqual(len(quality["failed_conditions"]), 3)
        self.assertEqual(report["single_quality_status"], "FAIL")
        rows[0]["small_clears"] = {"unjustified": 0, "necessary_survival": 1, "unknown": 0}
        self.assertEqual(gate.summarize(rows)["policies"][POLICIES[0]]["small_clears"]["necessary_survival"], 1)

    def test_duplicate_or_outside_identity_rejected(self):
        rows = self.rows()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            gate.summarize(rows + [rows[0]])
        rows[0]["pattern_id"] = -1
        with self.assertRaisesRegex(ValueError, "unexpected"):
            gate.summarize(rows)

    def test_manifest_checksum_threshold_and_source_mismatch_rejected(self):
        config = {"preregistration": gate.declaration(), "pattern_ids": list(gate.PATTERN_IDS),
                  "repeats": list(gate.REPEATS), "policies": list(POLICIES),
                  "total_resolved_placements": 46, "thresholds": gate.declaration()["thresholds"]}
        value = {"schema": gate.SCHEMA, "source": {}, "build": {}, "config": config,
                 "config_sha256": gate.digest(config)}
        value["sha256"] = gate.digest(value)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            gate.write_new(output / "manifest.json", value)
            self.assertEqual(gate.load_manifest(output), value)
            with self.assertRaises(FileExistsError):
                gate.write_new(output / "manifest.json", value)
            with (patch.object(gate, "require_clean"),
                  patch.object(gate, "current_source", return_value={"other": 1}),
                  self.assertRaisesRegex(ValueError, "changed")):
                gate.load_manifest(output, execution=True)
            value["config"]["thresholds"]["mean_maximum_actual_chain"] = 9
            (output / "manifest.json").write_text(__import__("json").dumps(value))
            with self.assertRaisesRegex(ValueError, "checksum"):
                gate.load_manifest(output)


if __name__ == "__main__":
    unittest.main()
