"""Real-runtime public boundary fixtures plus timing/contract integration."""
from collections import deque
from dataclasses import replace
import random
import unittest

from agents.nextgen_contracts import (
    FEATURE_NAMES, NumericEvidence, PUBLIC_CELL_TO_COLOR, PublicSnapshot,
    build_features,
)
from puyo_env.actions import NUM_ACTIONS
from puyo_env.nextgen_public_snapshot import (
    ResponseWitness, TickInterval, TimingProfile, derive_timing_summary,
)
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import Action, Direction, PuyoColor, VISIBLE_HEIGHT
from src.core.puyo import Puyo
from src.core.realtime import RealtimeHeadlessSimulator, TickInput


def interval(lower, upper=None, source="public_estimate", provenance="fixture_public_witness"):
    return TickInterval(lower, lower if upper is None else upper, source, provenance)


def witness(start=10, complete=70, generated=4, required=4, known=True):
    return ResponseWitness(interval(start), interval(complete),
                           NumericEvidence(generated, "evaluated", "visible_exact"),
                           NumericEvidence(required, "evaluated", "visible_exact"), known)


def lock(match, player=0, colors=(PuyoColor.RED, PuyoColor.BLUE), x=0):
    state = match.player_states[f"player_{player}"]
    game = state.simulator.game
    game.current_puyo_1, game.current_puyo_2 = map(Puyo, colors)
    game.puyo_x, game.puyo_rot = x, Direction.UP
    game.puyo_y = game.find_landing_y(x, Direction.UP)
    game.lock_puyo()
    state.simulator = RealtimeHeadlessSimulator(game_state=game, timing=match.timing)


def resolve(match):
    for _ in range(200):
        result = match.step()
        if any(e.type == "resolution_complete" for r in result.player_results.values() for e in r.events):
            return result
    raise AssertionError("fixture failed to resolve")


class PublicBoundaryTests(unittest.TestCase):
    def test_private_perturbations_leave_snapshot_digest_and_features_equal(self):
        match = RealtimeVersusMatch(seed=123)
        match.public_snapshot()
        match.step()
        before = match.public_snapshot()
        profile = TimingProfile.from_match(match, operation_cadence=interval(20, 30))
        summary = derive_timing_summary(before, profile, request_tick=match.tick,
                                        opponent_attack_candidate=False)
        mask = (True, False, False, False, False, False)
        features = build_features(summary.feature_summaries(), mask)
        match.seed = 999
        match._ojama_rngs = {a: random.Random(999) for a in match.possible_agents}
        for state in match.player_states.values():
            game = state.simulator.game
            game.puyo_sequence = object()  # Raises if producer attempts future draws.
            game.next_puyo_queue = deque(list(game.next_puyo_queue)[:2] + [
                (Puyo(PuyoColor.PURPLE), Puyo(PuyoColor.PURPLE))] * 50)
            for y in range(VISIBLE_HEIGHT, 14):
                game.field.place_puyo(0, y, Puyo(PuyoColor.YELLOW))
            original_get = game.field.get_puyo
            def visible_only(x, y, original=original_get):
                if y >= VISIBLE_HEIGHT:
                    raise AssertionError("private row read")
                return original(x, y)
            game.field.get_puyo = visible_only
        after = match.public_snapshot()
        self.assertEqual(before, after)
        self.assertEqual(before.digest, after.digest)
        after_summary = derive_timing_summary(after, profile, request_tick=match.tick,
                                              opponent_attack_candidate=False)
        self.assertEqual(summary, after_summary)
        self.assertEqual(features, build_features(after_summary.feature_summaries(), mask))
        self.assertEqual(after.own.visible_board[:2], ((None,) * 6,) * 2)
        self.assertEqual(PublicSnapshot.from_dict(after.to_dict()), after)

    def test_wire_colors_copied_values_and_public_queue_limit(self):
        match = RealtimeVersusMatch(seed=1)
        game = match.player_states["player_0"].simulator.game
        for x, color in enumerate(PUBLIC_CELL_TO_COLOR):
            game.field.place_puyo(x, 0, Puyo(color))
        snapshot = match.public_snapshot()
        self.assertEqual(snapshot.own.visible_board[-1][:len(PUBLIC_CELL_TO_COLOR)],
                         tuple(range(len(PUBLIC_CELL_TO_COLOR))))
        self.assertEqual(len(snapshot.own.known_pieces), 3)
        game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        self.assertEqual(snapshot.own.visible_board[-1][0], 0)
        self.assertEqual(match.public_snapshot(1).opponent, match.public_snapshot().own)

    def test_carry_validates_runtime_threshold_not_hardcoded_70(self):
        match = RealtimeVersusMatch(target_score_per_ojama=100)
        match.player_states["player_0"].score_carry = 99
        self.assertEqual(match.public_snapshot().own.score_carry, 99)
        match.player_states["player_0"].score_carry = 100
        with self.assertRaisesRegex(ValueError, "carry"):
            match.public_snapshot()

    def test_hidden_landing_destinations_suppressed_during_animation(self):
        match = RealtimeVersusMatch()
        game = match.player_states["player_0"].simulator.game
        for phase, animation in (("animate", "drop_tween"), ("garbage", "resolve")):
            game.state, game.animation_state = phase, animation
            before = match.public_snapshot()
            game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
            self.assertEqual(before, match.public_snapshot())
            self.assertTrue(all(cell is None for row in before.own.visible_board for cell in row))

    def test_arrival_waits_for_drop_and_overflow_keeps_packet_identity(self):
        for amount in (31, 60, 61):
            with self.subTest(amount=amount):
                match = RealtimeVersusMatch(seed=1)
                match.public_snapshot()
                match.schedule_attack("player_1", amount)
                match.step()
                arrived = match.public_snapshot()
                packet = arrived.own.attack_packets[0]
                self.assertEqual(packet.arrival_tick, 0)
                self.assertIsNone(packet.landed_tick)
                self.assertTrue(any(e.kind == "arrival" for e in arrived.events))
                self.assertFalse(any(e.kind == "drop" for e in arrived.events))
                lock(match)
                result = resolve(match)
                dropped = match.public_snapshot()
                self.assertEqual(result.dropped_ojama["player_0"], 30)
                self.assertEqual(dropped.own.attack_packets[0].amount, amount - 30)
                self.assertEqual(dropped.own.attack_packets[0].packet_id, packet.packet_id)
                drop = [e for e in dropped.events if e.kind == "drop"][-1]
                self.assertGreater(drop.tick, packet.arrival_tick)
                match.step()
                self.assertEqual(len([e for e in match.public_snapshot().events if e.kind == "drop"]), 1)

    def test_same_tick_cancellation_and_bonus_are_observed_once(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=100)
        match.public_snapshot()
        for player in (0, 1):
            state = match.player_states[f"player_{player}"]
            game = state.simulator.game
            for y in (0, 1):
                game.field.place_puyo(1, y, Puyo(PuyoColor.RED))
            game.field.place_puyo(5, 0, Puyo(PuyoColor.BLUE))  # prevent new all clear
            game.all_clear_bonus_pending = True
            state.score_carry = 69
            lock(match, player, (PuyoColor.RED, PuyoColor.RED), 2)
        first = match.public_snapshot()
        self.assertEqual(first.own.score_carry, 69)
        result = resolve(match)
        after = match.public_snapshot()
        self.assertEqual(result.generated_attacks, {"player_0": 31, "player_1": 31})
        self.assertEqual(result.attack_diagnostics["player_0"]["canceled"], 31)
        self.assertFalse(after.own.attack_packets)
        self.assertFalse(after.opponent.attack_packets)
        self.assertEqual(after.own.score_carry, 39)
        self.assertFalse(after.own.all_clear_bonus_pending)
        self.assertTrue(after.own.all_clear_bonus_consumed)
        for _ in range(3):
            match.public_snapshot()  # observer reads must never convert score twice
        match.step()
        self.assertEqual(match.public_snapshot().own.score_carry, 39)
        self.assertEqual(len([e for e in match.public_snapshot().events if e.kind == "attack"]), 2)
        history = match.public_timing_history()
        self.assertEqual(len(history.resolutions), 2)
        self.assertEqual(history.resolutions[0].canceled, 31)
        self.assertEqual(history.resolutions[0].outgoing, 0)

    def test_same_tick_arrival_and_full_or_partial_drop_have_linked_events(self):
        for amount in (3, 31):
            match = RealtimeVersusMatch(seed=1)
            match.public_snapshot()
            # Resolve a normal placement first to discover the public boundary
            # in an identical deterministic fixture, then deliver exactly there.
            probe = RealtimeVersusMatch(seed=1)
            lock(probe)
            boundary_tick = resolve(probe).tick
            match.schedule_attack("player_1", amount, delay_ticks=boundary_tick)
            packet_id = match.public_snapshot().own.attack_packets[0].packet_id
            lock(match)
            result = resolve(match)
            self.assertEqual(result.tick, boundary_tick)
            events = match.public_timing_history().packets
            self.assertEqual([(e.kind, e.packet_id, e.tick, e.amount) for e in events],
                             [("arrival", packet_id, boundary_tick, amount),
                              ("drop", packet_id, boundary_tick, min(amount, 30))])

    def test_packet_cancel_sidecar_identifies_response_target(self):
        match = RealtimeVersusMatch(seed=1)
        match.public_snapshot()
        match.schedule_attack("player_1", 5, delay_ticks=100)
        target = match.public_snapshot().own.attack_packets[0].packet_id
        game = match.player_states["player_0"].simulator.game
        game.all_clear_bonus_pending = True
        for y in (0, 1):
            game.field.place_puyo(1, y, Puyo(PuyoColor.RED))
        lock(match, colors=(PuyoColor.RED, PuyoColor.RED), x=2)
        result = resolve(match)
        history = match.public_timing_history()
        cancel = [e for e in history.packets if e.kind == "cancel"]
        self.assertEqual([(e.packet_id, e.amount, e.tick) for e in cancel], [(target, 5, result.tick)])
        self.assertEqual(history.resolutions[0].event_id, f"{result.tick}:0:resolution")
        self.assertEqual(type(history).from_dict(history.to_dict()), history)

    def test_opponent_advances_during_chain_and_fire_completion_is_later(self):
        match = RealtimeVersusMatch(seed=123)
        match.public_snapshot()
        game = match.player_states["player_0"].simulator.game
        for y in (0, 1):
            game.field.place_puyo(1, y, Puyo(PuyoColor.RED))
        lock(match, colors=(PuyoColor.RED, PuyoColor.RED), x=2)
        start = match.tick
        opponent = match.player_states["player_1"].simulator.game
        before_y = opponent.puyo_y
        match.step({"player_1": TickInput(press=(Action.DOWN,))})
        self.assertEqual(game.state, "animate")
        self.assertLess(opponent.puyo_y, before_y)
        self.assertFalse(any(e.kind == "clear" for e in match.public_snapshot().events))
        result = resolve(match)
        self.assertGreater(result.tick, start)
        self.assertTrue(any(e.kind == "clear" and e.tick == result.tick for e in match.public_snapshot().events))

    def test_tick_input_records_actual_lock_before_chain_completion(self):
        match = RealtimeVersusMatch(seed=1)
        match.public_snapshot()
        game = match.player_states["player_0"].simulator.game
        game.current_puyo_1 = Puyo(PuyoColor.RED)
        game.current_puyo_2 = Puyo(PuyoColor.RED)
        for y in (0, 1):
            game.field.place_puyo(1, y, Puyo(PuyoColor.RED))
        match.step({"player_0": TickInput(press=(Action.DOWN,))})
        for _ in range(180):
            match.step()
            if match.public_timing_history().resolutions:
                break
        events = match.public_snapshot().events
        placement = next(e for e in events if e.player_id == 0 and e.kind == "placement")
        clear = next(e for e in events if e.player_id == 0 and e.kind == "clear")
        self.assertLess(placement.tick, clear.tick)
        self.assertEqual(match.public_timing_history().resolutions[0].tick, clear.tick)

    def test_reset_and_lazy_install_have_no_stale_or_fabricated_history(self):
        match = RealtimeVersusMatch()
        match.step()
        self.assertEqual(match.public_snapshot().events, ())
        self.assertEqual(match.public_timing_history().packets, ())
        match.schedule_attack("player_1", 1)
        match.step()
        self.assertTrue(match.public_snapshot().events)
        match.reset(seed=3)
        self.assertEqual(match.public_snapshot().events, ())
        self.assertEqual(match.public_snapshot(), RealtimeVersusMatch(seed=3).public_snapshot())


class TimingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.match = RealtimeVersusMatch()
        self.profile = TimingProfile.from_match(self.match, operation_cadence=interval(20, 30))

    def summary(self, **kwargs):
        return derive_timing_summary(self.match.public_snapshot(), self.profile,
                                     request_tick=10, **kwargs)

    def test_profile_hash_modes_rules_and_strict_roundtrip(self):
        self.assertEqual(self.profile, TimingProfile.from_dict(self.profile.to_dict()))
        for profile in (replace(self.profile, latency_mode="measured"),
                        replace(self.profile, garbage_drop_ticks=22),
                        replace(self.profile, operation_cadence=interval(10, 30))):
            self.assertNotEqual(self.profile.digest, profile.digest)
        execution = self.profile.execution_context([True] * NUM_ACTIONS, 8)
        self.assertEqual(execution.timing_digest, self.profile.digest)
        self.assertEqual(execution.timeout_tick, 128)
        with self.assertRaises(ValueError):
            replace(self.profile, operation_cadence=interval(0))

    def test_arrival_is_only_lower_bound_no_fake_landing(self):
        self.match.schedule_attack("player_1", 60, delay_ticks=20)
        summary = self.summary(response_witness=witness(10, 100))
        self.assertEqual(summary.deadline.lower, 20)
        self.assertIsNone(summary.deadline.upper)
        self.assertEqual(summary.response, "marginal")
        self.assertEqual(summary.threat, "immediate")
        with self.assertRaisesRegex(ValueError, "precedes"):
            self.summary(landing_deadline=interval(19))

    def test_threat_bins_and_unknown_are_not_zero_safety(self):
        self.assertIsNone(self.summary().threat)
        self.assertEqual(self.summary(opponent_attack_candidate=False).threat, "none")
        self.assertEqual(self.summary(opponent_attack_candidate=True).threat, "potential")
        self.match.schedule_attack("player_1", 1)
        for deadline, expected in ((40, "immediate"), (70, "pressing"), (100, "potential")):
            self.assertEqual(self.summary(landing_deadline=interval(deadline)).threat, expected)
        self.profile = replace(self.profile, operation_cadence=TickInterval(None, None, "public_estimate", "unavailable"))
        self.assertIsNone(self.summary().threat)

    def test_response_uses_completion_bounds_power_and_provenance(self):
        cases = [
            (witness(10, 70), "possible"),
            (replace(witness(), fire_complete=interval(60, 90)), "marginal"),
            (witness(10, 81), "impossible"),
            (witness(generated=3), "impossible"),
            (witness(known=False), "marginal"),
            (replace(witness(), generated_ojama=NumericEvidence(4, "evaluated", "sampled_future")), "marginal"),
            (replace(witness(), generated_ojama=NumericEvidence(None, "not_evaluated", "public_estimate")), "unknown"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected, witness=response):
                self.assertEqual(self.summary(landing_deadline=interval(70, 80), response_witness=response).response, expected)
        self.assertEqual(self.summary().response, "unknown")

    def test_first_fire_counts_independent_opponent_operations_and_clips_once(self):
        self.profile = replace(self.profile, operation_cadence=interval(20))
        summary = self.summary(response_witness=witness(10, 110), opponent_next_operation=interval(20))
        self.assertEqual((summary.first_fire_lower.value, summary.first_fire_upper.value), (5, 5))
        self.assertEqual(type(summary).from_dict(summary.to_dict()), summary)
        features = build_features(summary.feature_summaries(), (True, False, False, False, False, False))
        for name in ("first_fire_loss.lower", "first_fire_loss.upper"):
            self.assertEqual(features.values[FEATURE_NAMES.index(name)], 1.0)
        busy = self.summary(response_witness=witness(10, 110), opponent_next_operation=interval(120))
        self.assertEqual(busy.first_fire_upper.value, 0)
        uncertain = self.summary(response_witness=witness(10, 70), opponent_next_operation=interval(20, 40))
        self.assertEqual((uncertain.first_fire_lower.value, uncertain.first_fire_upper.value), (2, 3))

    def test_feature_allowlist_has_no_ticks_identifiers_runtime_or_false_zeros(self):
        summary = self.summary()
        names = summary.feature_summaries()
        self.assertTrue(set(names) <= set(FEATURE_NAMES))
        features = build_features(names, (True, False, False, False, False, False))
        self.assertTrue(features.missing[FEATURE_NAMES.index("first_fire_loss.lower")])
        self.assertTrue(features.missing[FEATURE_NAMES.index("threat.none")])
        self.assertEqual(features.values[FEATURE_NAMES.index("response.unknown")], 1)
        with self.assertRaises(ValueError):
            build_features({"request_tick": 10}, (True,) * 6)


if __name__ == "__main__":
    unittest.main()
