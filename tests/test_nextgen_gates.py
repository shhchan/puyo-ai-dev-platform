"""Gate truth tables, historical failures, missing runs and public evidence isolation."""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from agents import nextgen_contracts as c
from eval.nextgen_gates import (
    G0_CHECKS,
    G1_CHECKS,
    REPEATS,
    SEEDS,
    THRESHOLDS,
    diagnose_selection,
    distribution,
    evaluate,
    safe_build_summary,
    throughput_estimate,
)


def contract():
    return {
        "seeds": list(SEEDS),
        "repeats": list(REPEATS),
        "thresholds": dict(THRESHOLDS),
        "runtime_information": "public_only",
        "reference_profile_calibrated": True,
    }


def rows():
    return [
        {
            "seed": s,
            "repeat": r,
            "termination": "placements",
            "placements": 40,
            "game_over": False,
            "max_chain": 10,
            "premature": 0,
            "semantic_digest": str(s),
            "decision_seconds": [0.5],
        }
        for s in SEEDS
        for r in REPEATS
    ]


def report(values=None, declaration=None, **kwargs):
    return evaluate(
        rows=rows() if values is None else values,
        contract=declaration or contract(),
        g0={k: True for k in G0_CHECKS},
        g1=kwargs.pop("g1", {k: True for k in G1_CHECKS}),
        threats={
            "complete": True,
            "failed": 0,
            "reference_source": "public_reference",
            "reference_corpus_predeclared": True,
            "candidate_denominator": 8,
            "candidate_gap": 0,
        },
        **kwargs,
    )


class GateTests(unittest.TestCase):
    def test_complete_quality_and_performance_are_separate_from_training(self):
        result = report()
        self.assertEqual(result["gates"]["G2"]["status"], "PASS")
        self.assertEqual(result["gates"]["G3"]["status"], "BLOCKED")
        self.assertFalse(result["long_training_allowed"])
        self.assertEqual(result["observed_quality"]["status"], "PASS")
        values = rows()
        for value in values:
            value["decision_seconds"] = [2]
        result = report(values)
        self.assertEqual(result["gates"]["G2"]["status"], "PASS")
        self.assertEqual(result["safe_build_performance"]["status"], "FAIL")

    def test_only240_adoption_never_overrides_historical_failure(self):
        values = rows()
        for value in values:
            value["max_chain"] = 9.7
            if value["seed"] == 126:
                value["premature"] = 1
            if value["seed"] == 133:
                value.update(termination="game_over", placements=39, game_over=True)
        result = report(values)
        self.assertEqual(result["gates"]["G2"]["status"], "BLOCKED")
        self.assertFalse(result["gates"]["G2"]["checks"]["premature_zero"])
        self.assertEqual(result["observed_quality"]["status"], "FAIL")
        self.assertFalse(result["gates"]["G2"]["checks"]["game_over_zero"])

    def test_missing_truncated_and_repeat_mismatch_are_not_dropped(self):
        values = rows()[:-1]
        values[0].update(termination="tick_limit", placements=3)
        values[2]["semantic_digest"] = "changed"
        result = report(values)
        summary = result["safe_build"]
        self.assertEqual(summary["observed_runs"], 59)
        self.assertEqual(summary["completed_runs"], 58)
        self.assertEqual(summary["missing"], [(152, 2)])
        self.assertEqual(summary["unfinished"], [[123, 1]])
        self.assertEqual(summary["repeat_mismatch_seeds"], [124])
        self.assertEqual(result["gates"]["G2"]["status"], "BLOCKED")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            safe_build_summary(values + [values[1]])

    def test_smoke_gui_missing_and_oracle_cannot_pass(self):
        declaration = contract()
        declaration["reference_profile_calibrated"] = False
        result = report(
            declaration=declaration,
            g1={k: True for k in G1_CHECKS if k != "gui_ledger"},
        )
        self.assertEqual(result["gates"]["G1"]["status"], "BLOCKED")
        self.assertEqual(result["gates"]["G2"]["status"], "BLOCKED")
        declaration["runtime_information"] = "hidden_future_oracle"
        self.assertFalse(
            report(declaration=declaration)["gates"]["G2"]["checks"]["public_runtime"]
        )

    def test_frozen_thresholds_and_explicit_boolean_evidence(self):
        declaration = contract()
        declaration["thresholds"]["mean_max_chain"] = 9
        with self.assertRaisesRegex(ValueError, "frozen"):
            report(declaration=declaration)
        with self.assertRaisesRegex(ValueError, "true, false or null"):
            report(g1={"gui_ledger": "PASS"})

    def test_measured_throughput_uses_mean_wall_time_not_p95(self):
        result = throughput_estimate(10, 12, 20)
        self.assertEqual(result["self_decisions_per_second"], 0.5)
        self.assertEqual(result["opponent_per_self"], 1.2)
        self.assertAlmostEqual(result["estimated_hours"]["wiring_2000"], 4000 / 3600)
        self.assertEqual(throughput_estimate(0, 0, 0)["status"], "BLOCKED")
        self.assertEqual(distribution([])["p95"], None)


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.d = c.Diagnostics.from_dict(
            json.loads(
                (Path(__file__).parent / "fixtures/nextgen/cancel.json").read_text()
            )
        )
        self.ref = {
            "snapshot_digest": self.d.request.public.digest,
            "tactic": "cancel",
            "good_root_actions": [4],
            "source": "public_reference",
        }

    def classify(self, diagnostic=None):
        return diagnose_selection((diagnostic or self.d).to_dict(), self.ref)

    def test_gap_and_selection_have_distinct_opportunity_denominators(self):
        self.ref["good_root_actions"] = [5]
        result = self.classify()
        self.assertEqual(
            (
                result["candidate_gap"],
                result["candidate_ranking_failure"],
                result["tactic_selection_failure"],
            ),
            (1, 0, 0),
        )
        self.assertEqual(result["ranking_denominator"], 0)
        self.ref["good_root_actions"] = [4]
        changed = replace(
            self.d,
            selection=replace(self.d.selection, selected_tactic_id="build_main"),
            receipt=None,
        )
        result = self.classify(changed)
        self.assertEqual(
            (
                result["candidate_gap"],
                result["candidate_ranking_failure"],
                result["tactic_selection_failure"],
            ),
            (0, 0, 1),
        )

    def test_rank_failure_does_not_get_counted_as_tactic_failure(self):
        first = self.d.batch.candidates[0]
        plan = (replace(first.plan[0], action=5), *first.plan[1:])
        second = replace(
            first,
            root_action=5,
            plan=plan,
            rank=1,
            candidate_id=c.candidate_id(first.identity, plan, first.assumptions),
        )
        tactics = tuple(
            replace(t, candidate_ids=(*t.candidate_ids, second.candidate_id))
            if t.available
            else t
            for t in self.d.batch.tactics
        )
        batch = replace(self.d.batch, candidates=(first, second), tactics=tactics)
        d = replace(
            self.d,
            batch=batch,
            selection=replace(self.d.selection, batch_digest=batch.digest),
            receipt=None,
        )
        self.ref["good_root_actions"] = [5]
        result = self.classify(d)
        self.assertEqual(
            (
                result["candidate_gap"],
                result["candidate_ranking_failure"],
                result["tactic_selection_failure"],
            ),
            (0, 1, 0),
        )
        self.assertEqual(result["selection_denominator"], 0)

    def test_oracle_stays_sidecar_and_private_runtime_injection_is_rejected(self):
        self.ref["source"] = "hidden_future_oracle"
        self.assertEqual(self.classify()["source"], "hidden_future_oracle")
        raw = copy.deepcopy(self.d.to_dict())
        raw["oracle"] = self.ref
        with self.assertRaises(ValueError):
            diagnose_selection(raw, self.ref)
        self.ref["snapshot_digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.classify()


class RealtimeMeasurementTests(unittest.TestCase):
    def test_async_measurement_advances_ticks_while_worker_is_running(self):
        import time
        from unittest.mock import patch

        from eval.nextgen_gate_benchmark import measure
        from tests.test_nextgen_tactic_manager import policy

        def make(**kwargs):
            instance = policy()
            original = instance.select_action

            def slow(observation, info):
                time.sleep(0.03)
                return original(observation, info)

            instance.select_action = slow
            return instance

        with patch(
            "eval.nextgen_gate_benchmark.NextgenTacticManagerPolicy", side_effect=make
        ):
            result = measure(
                seed=123,
                max_ticks=60,
                safe=False,
                latency_mode="measured",
                realtime_clock=True,
            )
        receipts = [d["receipt"] for ledger in result["ledgers"] for d in ledger]
        self.assertTrue(receipts)
        self.assertTrue(any(r["completion_tick"] > r["request_tick"] for r in receipts))
        self.assertEqual(result["execution_mode"], "single_worker_realtime_clock")
        self.assertEqual(result["ticks"], 60)


if __name__ == "__main__":
    unittest.main()
