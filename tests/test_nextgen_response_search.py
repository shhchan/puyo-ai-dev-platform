"""Public response witness coverage, lifecycle parity and bounded accounting."""

import random
import unittest
from dataclasses import replace
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents import nextgen_response_search as response
from agents.nextgen_shared_search import scenario_provenance
from eval.nextgen_response_fixtures import (
    CONFIG,
    COUNTER,
    EMPTY,
    FIRE,
    FIXTURES,
    TIMING,
    build,
    make_request,
)
from puyo_env.nextgen_public_snapshot import TickInterval, TimingProfile
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import PuyoColor
from src.core.field import Field
from src.core.puyo import Puyo


def evidence(candidate):
    return {e.name: e.evidence for e in candidate.evidence}


class PublicResponseTests(unittest.TestCase):
    def test_required_public_fixture_coverage_and_fixed_cost(self):
        for name, values, tactic in FIXTURES:
            with self.subTest(fixture=name):
                req = make_request(**values)
                result = build(req)
                self.assertTrue(
                    result.batch.tactics[c.TACTIC_IDS.index(tactic)].available
                )
                self.assertLessEqual(
                    result.batch.counters.response_nodes,
                    req.control.search_profile.response_quota,
                )
                c.validate_request_batch(req, result.batch)
                self.assertEqual(c.from_json(result.batch.to_json()), result.batch)

    def test_counter_first_max30_drop_exactly_next_move_and_remaining_packets(self):
        for amount in (31, 61, 120):
            with self.subTest(incoming=amount):
                result = build(
                    make_request(
                        board=COUNTER, pieces=((3, 4), (1, 1)), incoming=amount
                    )
                )
                witnesses = [
                    t
                    for t in result.response_result.traces
                    if t.tactic == "counter" and t.first_drop_count == 30
                ]
                self.assertTrue(witnesses)
                witness = witnesses[0]
                self.assertEqual(len(witness.plan), 2)
                self.assertEqual(witness.first_drop_after_step, 1)
                self.assertEqual(witness.first_drop_columns, (5,) * 6)
                self.assertFalse(witness.conditional)
                candidate = next(
                    p
                    for p in result.response_result.proposals
                    if p.plan == witness.plan
                )
                values = evidence(candidate)
                self.assertEqual(values["counter_after_first_drop"].value, 1)
                self.assertEqual(values["fire_depth"].value, 2)
                self.assertEqual(
                    sum(p.amount for p in witness.remaining_packets),
                    amount - 30 - values["canceled"].value,
                )
                self.assertTrue(witness.following_drops)
                for dropped in witness.following_drops:
                    self.assertLessEqual(dropped.placed, 30)
                    self.assertEqual(
                        sum(p.amount for p in dropped.remaining_packets),
                        sum(p.amount for p in witness.remaining_packets)
                        - dropped.placed,
                    )
                self.assertGreater(witness.fire_start[0], TIMING.garbage_drop_ticks)

    def test_insufficient_firepower_does_not_mask_cancel_or_counter(self):
        for req, tactic in (
            (make_request(incoming=120), "cancel"),
            (
                make_request(board=COUNTER, pieces=((3, 4), (1, 1)), incoming=120),
                "counter",
            ),
        ):
            result = build(req)
            candidate = result.select(tactic)
            self.assertLess(evidence(candidate)["response_surplus"].value, 0)
            self.assertTrue(result.batch.tactics[c.TACTIC_IDS.index(tactic)].available)

    def test_arrival_is_not_fire_deadline_and_prep_respects_drop_boundary(self):
        result = build(make_request(incoming=1, arrival=0, carry=30, pieces=((1, 2),)))
        values = evidence(result.select("cancel"))
        self.assertGreater(values["fire_start_lower"].value, 0)
        self.assertGreater(
            values["fire_end_lower"].value, values["fire_start_lower"].value
        )
        self.assertEqual(values["canceled"].value, 1)
        future = build(
            make_request(
                board=EMPTY, pieces=((1, 1), (1, 1)), incoming=31, arrival=10000
            )
        )
        self.assertEqual(len(future.select("cancel").plan), 2)
        self.assertFalse(future.batch.action_mask[4])
        due = build(
            make_request(board=EMPTY, pieces=((1, 1), (1, 1)), incoming=31, arrival=0)
        )
        self.assertFalse(
            due.batch.action_mask[3]
        )  # Cannot skip the first drop to prepare.

    def test_unknown_remainder_is_conditional_and_never_known_counter_witness(self):
        result = build(
            make_request(
                board=COUNTER, pieces=((3, 4), (1, 1)), incoming=29, quota=4000
            )
        )
        self.assertTrue(result.batch.action_mask[4])
        self.assertFalse(result.batch.tactics[4].known_witness)
        for trace in result.response_result.traces:
            if trace.tactic == "counter":
                self.assertTrue(trace.conditional)
        self.assertEqual(
            evidence(result.select("counter"))["counter_after_first_drop"].source,
            "public_estimate",
        )

    def test_structural_blocked_is_distinct_from_budget_not_found(self):
        req = make_request(board=EMPTY, pieces=((1, 2), (3, 4)), incoming=61)
        complete = build(req)
        self.assertEqual(complete.batch.tactics[4].mask_reason, "trigger_blocked")
        partial = build(
            replace(
                req,
                control=replace(
                    req.control, search_profile=c.SearchProfile("tiny", 0, 0, 1)
                ),
            )
        )
        self.assertEqual(
            partial.batch.tactics[4].mask_reason, "not_found_within_budget"
        )
        self.assertEqual(partial.batch.counters.response_nodes, 1)
        self.assertEqual(partial.response_result.cutoff_reason, "response_quota")

    def test_known_configured_timeout_is_structural_measured_time_remains_estimate(
        self,
    ):
        late = replace(TIMING, inference_latency_ticks=121)
        result = build(make_request(timing=late), timing=late)
        self.assertEqual(result.batch.tactics[3].mask_reason, "deadline_unreachable")
        self.assertEqual(result.batch.counters.response_nodes, 0)
        measured = replace(TIMING, latency_mode="measured")
        result = build(make_request(timing=measured, pieces=((1, 2),)), timing=measured)
        self.assertIsNone(evidence(result.select("cancel"))["fire_end_upper"].value)
        self.assertEqual(
            evidence(result.select("cancel"))["fire_end_upper"].status, "not_evaluated"
        )

    def test_profile_digest_mismatch_rejected_before_expansion(self):
        with patch.object(response, "transition", wraps=response.transition) as kernel:
            with self.assertRaisesRegex(ValueError, "timing profile mismatch"):
                build(make_request(), timing=replace(TIMING, garbage_drop_ticks=22))
            kernel.assert_not_called()

    def test_score_carry_and_bonus_once_match_attack_conversion(self):
        for bonus, expected in ((False, 1), (True, 31)):
            result = build(
                make_request(incoming=120, carry=30, bonus=bonus, pieces=((1, 2),))
            )
            self.assertEqual(
                evidence(result.select("cancel"))["generated"].value, expected
            )
        result = build(
            make_request(
                board=EMPTY, pieces=((1, 1), (1, 1)), incoming=0, bonus=True, quota=1000
            )
        )
        # The all-clear bonus is consumed by the second placement's clear.
        depth2 = [p for p in result.response_result.proposals if len(p.plan) == 2]
        self.assertTrue(depth2)
        self.assertTrue(all(evidence(p)["generated"].value == 30 for p in depth2))

    def test_no_threat_short_attack_and_sidecar_not_in_candidate_wire(self):
        result = build(make_request(incoming=0, pieces=((1, 2),)))
        self.assertTrue(result.batch.action_mask[5])
        self.assertFalse(result.batch.action_mask[3])
        self.assertFalse(result.batch.action_mask[4])
        self.assertTrue(result.response_result.traces)
        payload = result.batch.to_json()
        self.assertNotIn("following_drops", payload)
        self.assertNotIn("first_drop_columns", payload)
        with patch.object(
            response, "transition", side_effect=AssertionError("search after select")
        ):
            result.select("decisive_short_attack")

    def test_budget_counts_every_new_placement_and_drop_before_expansion(self):
        for quota in (0, 1, 23, 200):
            req = make_request(board=COUNTER, incoming=61, quota=quota)
            with (
                patch.object(
                    response, "transition", wraps=response.transition
                ) as placements,
                patch.object(
                    response, "drop_public_garbage", wraps=response.drop_public_garbage
                ) as drops,
                patch.object(
                    response.PublicResponseProvider,
                    "_times",
                    autospec=True,
                    side_effect=response.PublicResponseProvider._times,
                ) as features,
            ):
                result = build(req)
            self.assertEqual(
                result.batch.counters.response_nodes,
                placements.call_count + drops.call_count,
            )
            self.assertEqual(
                result.batch.counters.feature_evaluations, features.call_count
            )
            self.assertLessEqual(result.batch.counters.response_nodes, quota)

    def test_hidden_rows_stay_conditional_and_unknown_cadence_is_missing(self):
        board = ((None,) * 6,) * 2 + FIRE[2:]
        result = build(make_request(board=board, pieces=((1, 2),)))
        self.assertFalse(result.batch.tactics[3].known_witness)
        self.assertEqual(
            evidence(result.select("cancel"))["generated"].status, "partial"
        )
        timing = replace(
            TIMING,
            operation_cadence=TickInterval(None, None, "public_estimate", "unknown"),
        )
        result = build(make_request(timing=timing, pieces=((1, 2),)), timing=timing)
        self.assertIsNone(evidence(result.select("cancel"))["fire_start_lower"].value)

    def test_only_public_prefix_is_used_and_repeated_results_are_identical(self):
        req = make_request(board=EMPTY, pieces=((1, 2),), incoming=0)
        first, second = build(req), build(req)
        self.assertFalse(first.batch.action_mask[5])
        self.assertEqual(first.deterministic_digest, second.deterministic_digest)
        self.assertEqual(first.batch.counters.response_nodes, c.NUM_ACTIONS)

    def test_runtime_private_rng_and_future_changes_leave_candidates_unchanged(self):
        match = RealtimeVersusMatch(seed=250)
        public = match.public_snapshot()
        timing = TimingProfile.from_match(
            match, operation_cadence=TIMING.operation_cadence
        )
        req = make_request(timing=timing, quota=80)
        req = replace(
            req,
            public=public,
            identity=replace(req.identity, snapshot_digest=public.digest),
            control=replace(
                req.control,
                scenario_provenance=scenario_provenance(
                    public.own.known_pieces, CONFIG
                ),
            ),
        )
        first = build(req, timing=timing)
        match.seed = 999
        match._ojama_rngs = {a: random.Random(999) for a in match.possible_agents}
        for state in match.player_states.values():
            state.simulator.game.puyo_sequence = object()
        changed = match.public_snapshot()
        self.assertEqual(changed, public)
        second = build(replace(req, public=changed), timing=timing)
        self.assertEqual(first.deterministic_digest, second.deterministic_digest)

    def test_drop_kernel_matches_authoritative_field_for_all_remainder_distributions(
        self,
    ):
        from agents.nextgen_shared_search import _public_state

        state, _ = _public_state(make_request(board=COUNTER))
        for count in (1, 5, 29, 30):
            for columns in response.drop_distributions(count):
                field = Field()
                for y, row in enumerate(state.to_color_grid()):
                    for x, color in enumerate(row):
                        field.place_puyo(x, y, Puyo(color))

                class PublicColumns:
                    def sample(self, population, k, columns=columns, count=count):
                        return [x for x, n in enumerate(columns) if n > count // 6]

                actual = field.drop_ojama(count, rng=PublicColumns())
                predicted, placed = response.drop_public_garbage(state, columns)
                self.assertEqual(placed, actual)
                self.assertEqual(
                    predicted.to_color_grid(),
                    tuple(tuple(cell.color for cell in row) for row in field.grid),
                )
                self.assertEqual(
                    predicted.game_over, field.get_puyo(2, 11).color != PuyoColor.EMPTY
                )


if __name__ == "__main__":
    unittest.main()
