"""Activated-piece phase budget and authoritative public resolution tests."""

from dataclasses import replace
from pathlib import Path
import unittest

from agents.nextgen_contracts import PublicAttackPacket, PublicEvent
from agents.template_catalog import MatchResult, TemplateCandidate, load_template_catalog
from agents.template_phase import TemplatePhaseController
from puyo_env.nextgen_public_snapshot import PublicPacketEvent, PublicResolutionEvent, PublicTimingHistory
from puyo_env.realtime_ai import RealtimeDecisionRecord, advance_template_phase_runtime
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import Direction, PuyoColor
from src.core.puyo import Puyo
from src.core.realtime import RealtimeHeadlessSimulator


CATALOG = Path(__file__).parent / "fixtures/nextgen/template_catalog.yaml"


def candidate(*, fit="fit", complete=False, progress=0.1):
    return TemplateCandidate(
        "relation_fixture", "base", "identity", (("A", 1),), progress,
        complete, progress, fit, "fixture", "witness", (0,), 1, False,
        "searched", 1,
    )


def result(*candidates, cutoff=False):
    return MatchResult(tuple(candidates), 1, cutoff, 1, False)


def receipt(outcome="activated", fallback=False):
    return RealtimeDecisionRecord(
        0, 0, 0, "up", True, 1, 0, False, False, fallback, "fixture", 0.0,
        "configured", 0, 0, 0, 0, None, outcome, None,
    )


def lock_chain(match):
    state = match.player_states["player_0"]
    game = state.simulator.game
    for y in (0, 1):
        game.field.place_puyo(1, y, Puyo(PuyoColor.RED))
    game.current_puyo_1 = Puyo(PuyoColor.RED)
    game.current_puyo_2 = Puyo(PuyoColor.RED)
    game.puyo_x, game.puyo_rot = 2, Direction.UP
    game.puyo_y = game.find_landing_y(2, Direction.UP)
    game.lock_puyo()
    state.simulator = RealtimeHeadlessSimulator(game_state=game, timing=match.timing)


class TemplatePhaseTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load_template_catalog(CATALOG)
        self.controller = TemplatePhaseController(self.catalog, seed=77)
        self.match = RealtimeVersusMatch(seed=1)
        self.snapshot = self.match.public_snapshot()
        self.history = self.match.public_timing_history()
        self.controller.start(result(candidate()), decision_id="d0")

    def activate(self, piece, tactic="build_template", *, request=None, adopted=True,
                 snapshot=None, history=None, targets=()):
        return self.controller.activate(
            piece_id=piece, request_id=request or f"request-{piece}", tactic=tactic,
            adopted=adopted, snapshot=snapshot or self.snapshot,
            history=history or self.history, player_id=0, target_packet_ids=targets,
        )

    def observe(self, *, snapshot=None, history=None, controllable=True,
                match_result=None, decision_id="next"):
        return self.controller.observe(
            snapshot or self.snapshot, history or self.history, player_id=0,
            controllable=controllable, match_result=match_result,
            decision_id=decision_id,
        )

    def test_fourteenth_adopted_pair_allowed_fifteenth_disabled(self):
        initial_id = self.controller.phase.phase_id
        for n in range(14):
            self.assertTrue(self.activate(f"piece-{n}"))
            self.assertEqual(self.controller.phase.consumed_decisions, n + 1)
        self.assertEqual(self.controller.phase.exit_reason, "limit")
        self.assertFalse(self.controller.can_build_template)
        self.assertEqual(self.controller.phase_snapshot().remaining_decisions, 0)
        self.assertEqual(self.controller.diagnostics()["phase_id"], initial_id)
        with self.assertRaisesRegex(ValueError, "inactive"):
            self.activate("piece-15")

    def test_retries_replans_stale_timeout_and_request_ids(self):
        self.assertFalse(self.activate("pair-1", adopted=False, request="stale"))
        self.assertFalse(self.activate("pair-1", adopted=False, request="timeout"))
        self.assertTrue(self.activate("pair-1", request="adopted"))
        self.assertFalse(self.activate("pair-1", request="retry"))
        self.assertEqual(self.controller.phase.consumed_decisions, 1)
        self.assertEqual(self.controller.diagnostics()["request_count"], 4)
        self.assertEqual(self.controller.diagnostics()["activated_piece_count"], 1)

    def test_completion_and_conservative_loss_of_compatibility(self):
        self.observe(match_result=result(candidate(complete=True, progress=1)))
        self.assertEqual(self.controller.exit_reason, "completed")
        self.assertFalse(self.controller.can_build_template)
        other = TemplatePhaseController(self.catalog, seed=77)
        other.start(result(candidate()), decision_id="d0")
        other.reconcile(result(candidate(fit="unknown"), cutoff=True))
        self.assertEqual(other.exit_reason, "search_unknown")
        third = TemplatePhaseController(self.catalog, seed=77)
        third.start(result(candidate()), decision_id="d0")
        third.reconcile(result(candidate(fit="no_fit")))
        self.assertEqual(third.exit_reason, "no_compatible_candidate")

    def test_response_waits_for_target_cancel_and_reselects_once(self):
        packet = PublicAttackPacket("target", 5, 100, None)
        snapshot = replace(self.snapshot, own=replace(self.snapshot.own, attack_packets=(packet,)))
        self.activate("pair-1", "cancel", snapshot=snapshot, targets=("target",))
        self.assertEqual(self.controller.exit_reason, "response_switch")
        self.assertIsNone(self.observe(snapshot=snapshot, match_result=result(candidate())))
        partial = PublicTimingHistory((PublicPacketEvent("cancel-1", 0, 1, "cancel", "target", 3),), ())
        self.assertIsNone(self.observe(snapshot=snapshot, history=partial, match_result=result(candidate())))
        full = PublicTimingHistory(partial.packets + (PublicPacketEvent("cancel-2", 0, 2, "cancel", "target", 2),), ())
        self.assertIsNone(self.observe(snapshot=snapshot, history=full, controllable=False,
                                       match_result=result(candidate())))
        cleared = replace(snapshot, own=replace(snapshot.own, attack_packets=()))
        selection = self.observe(snapshot=cleared, history=full,
                                 match_result=result(candidate(progress=0.2)))
        self.assertEqual(selection.candidate.template_id, "relation_fixture")
        self.assertEqual(self.controller.phase.phase_id, "template-phase-2")
        self.assertIsNone(self.observe(snapshot=cleared, history=full,
                                       match_result=result(candidate(progress=0.2))))

    def test_first_drop_requires_later_counter_resolution(self):
        packet = PublicAttackPacket("target", 31, 2, None)
        snapshot = replace(self.snapshot, own=replace(self.snapshot.own, attack_packets=(packet,)))
        self.activate("pair-1", "counter", snapshot=snapshot, targets=("target",))
        dropped = PublicTimingHistory((PublicPacketEvent("drop", 0, 2, "drop", "target", 30),), ())
        self.assertIsNone(self.observe(snapshot=snapshot, history=dropped, match_result=result(candidate())))
        self.assertEqual(self.controller.pending_resolution, "response")
        zero = PublicResolutionEvent("zero", 0, 2, 0, 0, 0, 0)
        self.assertIsNone(self.observe(snapshot=snapshot, history=replace(dropped, resolutions=(zero,)),
                                       match_result=result(candidate())))
        fired = PublicResolutionEvent("fire", 0, 4, 1, 0, 0, 0)
        placed = PublicEvent("placed", 0, "placement", 3, 0, ())
        after = replace(snapshot, events=snapshot.events + (placed,),
                        own=replace(snapshot.own, attack_packets=()))
        selection = self.observe(snapshot=after, history=replace(dropped, resolutions=(zero, fired)),
                                 match_result=result(candidate()))
        self.assertIsNotNone(selection)

    def test_same_piece_switch_closes_phase_without_second_consumption(self):
        self.assertTrue(self.activate("pair", "build_template", request="r1"))
        self.assertFalse(self.activate("pair", "cancel", request="r2"))
        self.assertEqual(self.controller.phase.consumed_decisions, 1)
        self.assertEqual(self.controller.exit_reason, "response_switch")
        self.assertEqual(self.controller.pending_resolution, "response")
        self.assertEqual(self.controller.diagnostics()["activated_piece_count"], 1)

    def test_same_tick_resolution_before_drop_is_not_counter(self):
        packet = PublicAttackPacket("target", 31, 2, None)
        snapshot = replace(self.snapshot, own=replace(self.snapshot.own, attack_packets=(packet,)))
        self.activate("pair", "counter", snapshot=snapshot, targets=("target",))
        history = PublicTimingHistory(
            (PublicPacketEvent("drop", 0, 2, "drop", "target", 30),),
            (PublicResolutionEvent("prior-fire", 0, 2, 1, 4, 0, 4),),
        )
        same_tick_placement = PublicEvent("same-tick", 0, "placement", 2, 0, ())
        after = replace(snapshot, events=snapshot.events + (same_tick_placement,))
        self.assertIsNone(self.observe(snapshot=after, history=history, match_result=result(candidate())))
        self.assertEqual(self.controller.pending_resolution, "response")

    def test_only_first_post_drop_placement_can_complete_counter(self):
        packet = PublicAttackPacket("target", 31, 2, None)
        snapshot = replace(self.snapshot, own=replace(self.snapshot.own, attack_packets=(packet,)))
        self.activate("pair", "counter", snapshot=snapshot, targets=("target",))
        history = PublicTimingHistory(
            (PublicPacketEvent("drop", 0, 2, "drop", "target", 30),),
            (PublicResolutionEvent("first-zero", 0, 4, 0, 0, 0, 0),
             PublicResolutionEvent("later-fire", 0, 7, 1, 4, 0, 4)),
        )
        after = replace(snapshot, events=snapshot.events + (
            PublicEvent("first-placement", 0, "placement", 3, 0, ()),
            PublicEvent("second-placement", 0, "placement", 6, 0, ()),
        ), own=replace(snapshot.own, attack_packets=()))
        self.assertIsNone(self.observe(snapshot=after, history=history, match_result=result(candidate())))
        self.assertEqual(self.controller.pending_resolution, "response")

    def test_counter_with_remaining_packet_waits_until_threat_clears(self):
        packet = PublicAttackPacket("target", 31, 2, None)
        snapshot = replace(self.snapshot, own=replace(self.snapshot.own, attack_packets=(packet,)))
        self.activate("pair", "counter", snapshot=snapshot, targets=("target",))
        history = PublicTimingHistory(
            (PublicPacketEvent("drop", 0, 2, "drop", "target", 30),),
            (PublicResolutionEvent("fire", 0, 4, 1, 4, 0, 4),),
        )
        placed = PublicEvent("placed", 0, "placement", 3, 0, ())
        after = replace(snapshot, events=snapshot.events + (placed,),
                        own=replace(snapshot.own, attack_packets=(replace(packet, amount=1),)))
        self.assertIsNone(self.observe(snapshot=after, history=history, match_result=result(candidate())))
        self.assertTrue(self.controller.diagnostics()["reselect_ready"])
        self.assertEqual(self.controller.phase.phase_id, "template-phase-1")
        clear = replace(after, own=replace(after.own, attack_packets=()))
        self.assertIsNotNone(self.observe(snapshot=clear, history=history, match_result=result(candidate())))

    def test_fire_and_short_attack_require_authoritative_completion(self):
        for tactic in ("fire_main", "decisive_short_attack"):
            with self.subTest(tactic=tactic):
                controller = TemplatePhaseController(self.catalog, seed=1)
                controller.start(result(candidate()), decision_id="d0")
                controller.activate(piece_id="one", request_id="r1", tactic=tactic,
                                    adopted=True, snapshot=self.snapshot, history=self.history, player_id=0)
                self.assertIsNone(controller.observe(self.snapshot, self.history, player_id=0,
                                                      controllable=True, match_result=result(candidate()), decision_id="d1"))
                history = PublicTimingHistory((), (PublicResolutionEvent("other", 1, 1, 1, 4, 0, 4),))
                self.assertIsNone(controller.observe(self.snapshot, history, player_id=0,
                                                      controllable=True, match_result=result(candidate()), decision_id="d1"))
                history = PublicTimingHistory((), history.resolutions + (PublicResolutionEvent("own", 0, 2, 1, 4, 0, 4),))
                self.assertIsNotNone(controller.observe(self.snapshot, history, player_id=0,
                                                        controllable=True, match_result=result(candidate()), decision_id="d2"))

    def test_reselect_excludes_unknown_and_no_fit(self):
        self.activate("pair", "fire_main")
        history = PublicTimingHistory((), (PublicResolutionEvent("own", 0, 1, 1, 4, 0, 4),))
        selected = self.observe(history=history, match_result=result(candidate(fit="unknown")))
        self.assertIsNone(selected.candidate)
        self.assertEqual(selected.reason, "free_build_no_proven_fit")
        self.assertIsNone(self.controller.phase)
        self.assertEqual(self.controller.exit_reason, "free_build_no_proven_fit")

    def test_build_main_interrupts_without_reselection(self):
        self.activate("pair", "build_main")
        self.assertEqual(self.controller.exit_reason, "interrupted")
        self.assertIsNone(self.observe(match_result=result(candidate())))
        self.assertFalse(self.controller.can_build_template)

    def test_limit_does_not_prevent_later_fire_reselection(self):
        for n in range(14):
            self.activate(f"piece-{n}")
        self.activate("fire-piece", "fire_main")
        history = PublicTimingHistory((), (PublicResolutionEvent("fire", 0, 20, 1, 4, 0, 4),))
        selection = self.observe(history=history, match_result=result(candidate()))
        self.assertIsNotNone(selection)
        self.assertEqual(self.controller.phase.phase_id, "template-phase-2")

    def test_threat_does_not_repeatedly_open_phases(self):
        self.activate("pair-1", "cancel")
        self.activate("pair-2", "counter")
        self.assertEqual(self.controller.pending_resolution, "response")
        self.assertEqual(self.controller.phase.phase_id, "template-phase-1")
        self.assertIsNone(self.observe(match_result=result(candidate())))
        self.assertEqual(self.controller.phase.phase_id, "template-phase-1")

    def test_real_runtime_hook_waits_until_chain_resolution(self):
        # The actual match supplies the public history and control state.
        self.match.step()
        self.snapshot = self.match.public_snapshot()
        self.history = self.match.public_timing_history()
        advance_template_phase_runtime(
            self.match, 0, self.controller, activated_receipt=receipt(),
            piece_id="real-piece", request_id="real-request", tactic="fire_main",
            match_result=result(candidate()), decision_id="d1",
        )
        self.assertEqual(self.controller.pending_resolution, "fire")
        lock_chain(self.match)
        self.assertIsNone(advance_template_phase_runtime(
            self.match, 0, self.controller, match_result=result(candidate()), decision_id="d2"))
        for _ in range(200):
            self.match.step()
            if any(e.player_id == 0 and e.chain_count > 0
                   for e in self.match.public_timing_history().resolutions):
                break
        else:
            self.fail("chain resolution missing")
        # The observer can resolve during animation; reselect waits for control.
        selection = None
        for _ in range(200):
            selection = advance_template_phase_runtime(
                self.match, 0, self.controller, match_result=result(candidate()), decision_id="d3")
            if selection is not None:
                break
            self.match.step()
        self.assertIsNotNone(selection)
        self.assertEqual(self.controller.phase.phase_id, "template-phase-2")

    def test_runtime_bridge_ignores_fallback_receipt(self):
        advance_template_phase_runtime(
            self.match, 0, self.controller, activated_receipt=receipt("fallback", True),
            piece_id="same-pair", request_id="fallback-request", tactic="build_template",
        )
        self.assertEqual(self.controller.phase.consumed_decisions, 0)
        advance_template_phase_runtime(
            self.match, 0, self.controller, activated_receipt=receipt(),
            piece_id="same-pair", request_id="adopted-request", tactic="build_template",
        )
        self.assertEqual(self.controller.phase.consumed_decisions, 1)


if __name__ == "__main__":
    unittest.main()
