"""Episode-scoped template phase lifecycle over public, authoritative events.

The scheduler owns request IDs and piece IDs.  Only an activated, adopted
piece is reported here; retries for the same piece never consume another turn.
The caller supplies a fresh matcher result at each controllable board.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from agents.nextgen_contracts import PhaseSnapshot, PublicSnapshot, TACTIC_IDS
from agents.template_catalog import MatchResult, TemplateCandidate, TemplateCatalog, TemplateSelection, TemplateSelector
from puyo_env.nextgen_public_snapshot import PublicTimingHistory


@dataclass(frozen=True)
class TemplatePhase:
    phase_id: str
    template_id: str
    variant_id: str
    transform: str
    binding: tuple[tuple[str, int], ...]
    started_decision: str
    consumed_decisions: int
    limit: int
    state: str
    exit_reason: str | None


@dataclass(frozen=True)
class _PendingResolution:
    kind: str
    target_packet_ids: tuple[str, ...]
    target_packets: tuple[tuple[str, int], ...]
    packet_event_ids: frozenset[str]
    resolution_event_ids: frozenset[str]
    public_event_ids: frozenset[str]
    first_drop_tick: int | None


class TemplatePhaseController:
    """One controller and fixed catalog/selector RNG per match and player."""

    def __init__(self, catalog: TemplateCatalog, *, seed: int):
        self.catalog = catalog
        self.selector = TemplateSelector(catalog, seed)
        self.phase: TemplatePhase | None = None
        self.candidate: TemplateCandidate | None = None
        self.selection: TemplateSelection | None = None
        self.exit_reason: str | None = None
        self._next_phase_number = 1
        self._initialized = False
        self._piece_tactics: dict[str, str] = {}
        self._request_ids: set[str] = set()
        self._pending: _PendingResolution | None = None
        self._reselect_ready = False
        self._previous_tactic: str | None = None
        self._tactic_continuation = 0

    @property
    def can_build_template(self) -> bool:
        return self.phase is not None and self.phase.state == "active" and self.phase.consumed_decisions < self.phase.limit

    @property
    def pending_resolution(self) -> str | None:
        return self._pending.kind if self._pending is not None else None

    def start(self, result: MatchResult, *, decision_id: str) -> TemplateSelection:
        if self._initialized:
            raise ValueError("initial template selection already made")
        self._initialized = True
        selection = self.selector.select_initial(result)
        self._open(selection, decision_id)
        return selection

    def _open(self, selection: TemplateSelection, decision_id: str) -> None:
        self.selection = selection
        self.candidate = selection.candidate
        if selection.candidate is None:
            self.phase = None
            self.exit_reason = selection.reason
            return
        candidate = selection.candidate
        template = next(t for t in self.catalog.templates if t.id == candidate.template_id)
        self.phase = TemplatePhase(
            phase_id=f"template-phase-{self._next_phase_number}",
            template_id=candidate.template_id,
            variant_id=candidate.variant_id,
            transform=candidate.transform,
            binding=candidate.binding,
            started_decision=decision_id,
            consumed_decisions=0,
            limit=template.commit_turns,
            state="active",
            exit_reason=None,
        )
        self._next_phase_number += 1
        self.exit_reason = None

    def _close(self, reason: str) -> None:
        if self.phase is not None and self.phase.state == "active":
            self.phase = replace(self.phase, state="closed", exit_reason=reason)
        self.exit_reason = reason

    def reconcile(self, result: MatchResult) -> None:
        """Reconcile the selected shape on the current controllable board."""
        if not self.can_build_template or self.candidate is None:
            return
        same = next((c for c in result.candidates if c.key == self.candidate.key), None)
        if same is None:
            self._close("search_unknown" if result.cutoff or result.static_cutoff else "no_compatible_candidate")
            return
        self.candidate = same
        if same.complete:
            self._close("completed")
        elif not same.compatible:
            self._close("no_compatible_candidate")

    def activate(
        self,
        *,
        piece_id: str,
        request_id: str,
        tactic: str,
        adopted: bool,
        snapshot: PublicSnapshot,
        history: PublicTimingHistory,
        player_id: int,
        target_packet_ids: tuple[str, ...] = (),
    ) -> bool:
        """Record a scheduler receipt. Return whether this piece was consumed.

        Request ID identifies an attempt; piece ID identifies the actual pair.
        A stale, timeout, rejected or fallback receipt has adopted=False.
        """
        if not piece_id or not request_id or tactic not in TACTIC_IDS or player_id not in (0, 1):
            raise ValueError("invalid activation identity")
        self._request_ids.add(request_id)
        if not adopted or self._piece_tactics.get(piece_id) == tactic:
            return False
        previously_adopted = piece_id in self._piece_tactics
        if tactic == "build_template" and (previously_adopted or not self.can_build_template):
            raise ValueError("template tactic is inactive")
        targets = tuple((p.packet_id, p.amount) for p in snapshot.own.attack_packets if p.packet_id in target_packet_ids)
        previously_dropped = {e.packet_id for e in history.packets if e.kind == "drop"}
        if len(targets) != len(set(target_packet_ids)) and not (
            tactic == "counter" and set(target_packet_ids) <= {p for p, _ in targets} | previously_dropped
        ):
            raise ValueError("target packet missing from public snapshot")
        self._piece_tactics[piece_id] = tactic
        if not previously_adopted:
            if tactic == self._previous_tactic:
                self._tactic_continuation += 1
            else:
                self._previous_tactic = tactic
                self._tactic_continuation = 1
        elif self._previous_tactic != tactic:
            self._previous_tactic = tactic
            self._tactic_continuation = 1
        if tactic == "build_template":
            assert self.phase is not None
            self.phase = replace(self.phase, consumed_decisions=self.phase.consumed_decisions + 1)
            if self.phase.consumed_decisions == self.phase.limit:
                self._close("limit")
            return True
        if tactic == "build_main" and self.can_build_template:
            self._close("interrupted")
        if tactic in ("cancel", "counter", "fire_main", "decisive_short_attack") and self._pending is None and not self._reselect_ready:
            if self.can_build_template:
                reason = "response_switch" if tactic in ("cancel", "counter") else "fire_switch" if tactic == "fire_main" else "short_attack_switch"
                self._close(reason)
            self._pending = _PendingResolution(
                "response" if tactic in ("cancel", "counter") else "fire",
                target_packet_ids,
                targets,
                frozenset(e.event_id for e in history.packets),
                frozenset(e.event_id for e in history.resolutions),
                frozenset(e.event_id for e in snapshot.events),
                min((e.tick for e in history.packets if e.kind == "drop" and e.packet_id in target_packet_ids), default=None) if tactic == "counter" else None,
            )
        elif tactic == "counter" and self._pending is not None and self._pending.kind == "response":
            response_targets = target_packet_ids or self._pending.target_packet_ids
            first_drop = min((e.tick for e in history.packets if e.kind == "drop" and e.packet_id in response_targets), default=None)
            if first_drop is not None:
                self._pending = replace(self._pending, first_drop_tick=first_drop)
        return not previously_adopted

    def observe(
        self,
        snapshot: PublicSnapshot,
        history: PublicTimingHistory,
        *,
        player_id: int,
        controllable: bool,
        match_result: MatchResult | None = None,
        decision_id: str | None = None,
    ) -> TemplateSelection | None:
        """Use authoritative resolution, then select once at the next control board."""
        if player_id not in (0, 1):
            raise ValueError("invalid player")
        pending = self._pending
        if pending is not None:
            new_packets = tuple(e for e in history.packets if e.event_id not in pending.packet_event_ids)
            new_resolutions = tuple(e for e in history.resolutions if e.event_id not in pending.resolution_event_ids and e.player_id == player_id)
            fired = any(e.chain_count > 0 for e in new_resolutions)
            if pending.kind == "fire":
                resolved = fired
            else:
                canceled = all(
                    sum(e.amount for e in new_packets if e.kind == "cancel" and e.packet_id == packet_id) >= amount
                    for packet_id, amount in pending.target_packets
                ) if pending.target_packets and len(pending.target_packets) == len(pending.target_packet_ids) else (
                    any(e.canceled > 0 for e in new_resolutions) if not pending.target_packet_ids else False
                )
                observed_drop = min((e.tick for e in new_packets if e.kind == "drop" and e.packet_id in pending.target_packet_ids), default=None)
                first_drop = pending.first_drop_tick if observed_drop is None else min(pending.first_drop_tick, observed_drop) if pending.first_drop_tick is not None else observed_drop
                placements = tuple(e for e in snapshot.events if e.event_id not in pending.public_event_ids and e.kind == "placement" and e.player_id == player_id and first_drop is not None and e.tick > first_drop)
                first_placement_tick = min((e.tick for e in placements), default=None)
                first_placement_resolution = min(
                    (e for e in new_resolutions if first_placement_tick is not None and e.tick >= first_placement_tick),
                    key=lambda e: (e.tick, e.event_id),
                    default=None,
                )
                counter = first_placement_resolution is not None and first_placement_resolution.chain_count > 0
                resolved = canceled or counter
            if resolved:
                self._pending = None
                self._reselect_ready = True
        if self._reselect_ready and controllable and not snapshot.own.attack_packets and match_result is not None and decision_id is not None:
            self._reselect_ready = False
            selection = self.selector.reselect(match_result)
            self._open(selection, decision_id)
            return selection
        if controllable and match_result is not None and self.can_build_template:
            self.reconcile(match_result)
        return None

    def phase_snapshot(self) -> PhaseSnapshot:
        phase = self.phase
        candidate = self.candidate
        return PhaseSnapshot(
            phase_id=phase.phase_id if phase else None,
            template_id=phase.template_id if phase else None,
            active=self.can_build_template,
            remaining_decisions=max(0, phase.limit - phase.consumed_decisions) if self.can_build_template else 0,
            decision_limit=phase.limit if phase else self.catalog.default_commit_turns,
            progress=candidate.progress if candidate else 0.0,
            fit_status=candidate.fit_status if candidate else "unknown",
            previous_tactic=self._previous_tactic,
            tactic_continuation=self._tactic_continuation,
        )

    def diagnostics(self) -> dict:
        return {
            "phase_id": self.phase.phase_id if self.phase else None,
            "selected_key": self.candidate.key if self.candidate else None,
            "constraint_retention": "active_phase" if self.can_build_template else "released",
            "constraint_release_reason": self.exit_reason if not self.can_build_template else None,
            "exit_reason": self.exit_reason,
            "consumed_decisions": self.phase.consumed_decisions if self.phase else 0,
            "decision_limit": self.phase.limit if self.phase else self.catalog.default_commit_turns,
            "pending_resolution": self.pending_resolution,
            "reselect_ready": self._reselect_ready,
            "request_count": len(self._request_ids),
            "activated_piece_count": len(self._piece_tactics),
        }
