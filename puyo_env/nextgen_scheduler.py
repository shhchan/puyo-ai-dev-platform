"""Authoritative scheduler bridge for public-only nextgen workers."""

from __future__ import annotations

import copy
import pickle
from dataclasses import replace

from agents import nextgen_contracts as c
from agents.nextgen_tactic_manager import match_result_from_dict, reconcile_phase
from agents.template_phase import TemplatePhaseController
from puyo_env.nextgen_public_snapshot import TickInterval, TimingProfile


class _DecodedNextgenPayload(dict):
    """Parent-local proof of pure schema validation, never a wire contract.

    Contracts are frozen and recursively tuple-valued. Keep an exact wire
    serialization: a consumer changing even a nested wire field or its type
    must not retain the proof for the previous value. This avoids constructing
    a second full dictionary tree and walking it in Python on the UI thread.
    Pickle is used only to compare local bytes; no bytes are loaded here.
    """

    def __init__(self, payload, diagnostics):
        super().__init__(payload)
        self._diagnostics = diagnostics
        self._validated_wire = pickle.dumps(payload["nextgen"], protocol=5)

    def decoded(self):
        try:
            current = pickle.dumps(self.get("nextgen"), protocol=5)
        except Exception:
            # A mutated value can define a failing __reduce__. Treat any local
            # serialization failure as lost proof; the schema path rejects it.
            return None
        if current == self._validated_wire:
            return self._diagnostics
        return None


def decode_nextgen_payload(payload):
    """Validate only immutable worker data on the existing result reader.

    Malformed values retain the old UI-side error/outcome path. Request identity,
    current public state, phase and authoritative reachability are NOT accepted
    here; those remain owned by the simulation thread.
    """
    if not isinstance(payload, dict) or "nextgen" not in payload:
        return payload
    try:
        return _DecodedNextgenPayload(payload, c.Diagnostics.from_dict(payload["nextgen"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return payload


class NextgenScheduler:
    def __init__(self, policy):
        self.policy = policy
        self.reset()

    def reset(self):
        self.phase = TemplatePhaseController(self.policy.catalog, seed=self.policy.template_seed)
        self.sequence = 0
        self.episode_index = getattr(self, "episode_index", 0) + 1
        self.episode_id = f"nextgen-episode-{self.episode_index}"
        self.data = None
        self.result = None
        self.result_candidate = None
        self.result_phase = None
        self.ledger = []
        self.ledger_metadata = []
        self.errors = []
        self.last_payload = {}

    def prepare(self, match, agent, config):
        from puyo_env.realtime_ai import nextgen_authoritative_action_mask

        player = int(agent.rsplit("_", 1)[1])
        public = match.public_snapshot(player)
        history = match.public_timing_history()
        self.phase.observe(public, history, player_id=player, controllable=False)
        mask = nextgen_authoritative_action_mask(
            match.player_states[agent].simulator,
            timing=match.timing,
            max_expanded_states=config.max_plan_expanded_states,
        )
        if not any(mask):
            return None
        self.sequence += 1
        identity = c.DecisionIdentity(
            self.episode_id,
            player,
            self.sequence,
            f"request-{self.sequence}",
            public.digest,
        )
        # Explicit smoke estimate, never promoted to a lock/landing guarantee.
        timing = TimingProfile.from_match(
            match,
            latency_mode=config.latency_mode,
            inference_latency_ticks=config.inference_latency_ticks,
            timeout_ticks=config.timeout_ticks
            if config.timeout_ticks is not None
            else 2**31 - 1,
            operation_cadence=TickInterval(
                1, 120, "public_estimate", "smoke_operation_estimate"
            ),
        )
        self.data = {
            "identity": identity,
            "public": public,
            "history": history,
            "timing": timing,
            "execution": timing.execution_context(mask, match.tick),
            "phase": copy.deepcopy(self.phase),
            "piece_id": f"piece-{sum(e.kind == 'placement' and e.player_id == player for e in public.events)}",
        }
        inference = match.public_board_inference(player)
        if (inference.episode_id != match.public_inference_episode_id or
                inference.origin_episode_id != match.public_inference_episode_id):
            inference = replace(inference, status="unknown", hidden_rows=((None,) * 6,) * 2,
                                reason="origin_episode_mismatch")
        self.data["inference"] = replace(
            inference, episode_id=identity.episode_id,
            request_digest=c.inference_request_digest(identity, self.data["execution"]),
        )
        self.result = None
        self.result_candidate = None
        self.result_phase = None
        self.last_payload = {}
        # No simulator, queue, RNG, observation board or privileged runtime info
        # crosses this boundary, including when using the spawned executor.
        return {}, {
            "nextgen": self.data,
            "action_mask": mask,
            "action_mask_source": "reachable_planner",
        }

    def accept(self, payload, selected_action):
        try:
            diagnostics = payload.decoded() if isinstance(payload, _DecodedNextgenPayload) else None
            if diagnostics is None:
                diagnostics = c.Diagnostics.from_dict(payload["nextgen"])
            request = diagnostics.request
            if (
                request.identity != self.data["identity"]
                or request.public != self.data["public"]
                or request.execution != self.data["execution"]
                or request.inference != self.data["inference"]
                or request.control.search_profile != self.policy.profile
                or request.control.template_config_hash
                != self.policy.catalog.semantic_digest
            ):
                raise ValueError("worker request mismatch")
            candidate = diagnostics.selection.validate_batch(diagnostics.batch)
            if candidate.root_action != selected_action:
                raise ValueError("worker action/selection mismatch")
            phase = copy.deepcopy(self.phase)
            reconcile_phase(
                phase,
                match_result_from_dict(payload["template_match"]),
                request.public,
                self.data["history"],
                request.identity,
            )
            if phase.phase_snapshot() != request.control.phase:
                raise ValueError("worker phase mismatch")
            self.result, self.result_phase = diagnostics, phase
            self.result_candidate = candidate
            # Initial selection/reconciliation is control state, not a tactic
            # activation. Keep it across timeout/retry; consumption is below.
            self.phase = phase
            # The executor hands off an owned result. Only top-level keys are
            # replaced below; the controller still takes its detached copy.
            self.last_payload = dict(payload)
            return None
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            reason = f"nextgen_policy_error: {exc}"
            self.errors.append(
                {"request_id": self.data["identity"].request_id, "reason": reason}
            )
            return reason

    def stale(self, match):
        return (
            match.public_snapshot(self.data["identity"].player_id).digest
            != self.data["public"].digest
        )

    def finish(self, match, record, *, outcome=None):
        outcome = outcome or (
            "timeout"
            if record.timeout
            else "fallback"
            if record.fallback
            else "activated"
        )
        if self.result is None:
            return replace(record, outcome=outcome)
        result = self.result
        # accept validated the selected candidate against the complete batch.
        candidate = self.result_candidate
        player = result.request.identity.player_id
        public, history = match.public_snapshot(player), match.public_timing_history()
        reason = record.reason
        if result.selection.reason in ("legitimate_survival_exception", "survival_safe_nonfire"):
            adoption = "survival_adopted" if outcome == "activated" else "survival_not_adopted_" + outcome
            reason = (adoption + ":" + result.selection.reason + ":" + reason)[:256]
        if result.selection.selected_tactic_id == "build_template":
            adoption = "template_adopted" if outcome == "activated" else "template_not_adopted_" + outcome
            reason = (adoption + ":" + reason)[:256]
        receipt = c.ExecutionReceipt(
            candidate.candidate_id,
            candidate.root_action,
            record.action_index if record.activation_tick is not None else None,
            outcome,
            result.request.execution.request_tick,
            record.completion_tick,
            record.activation_tick,
            result.request.execution.timeout_tick,
            public.digest,
            reason,
        )
        diagnostics = replace(result, receipt=receipt)
        self.phase = self.result_phase
        self.phase.activate(
            piece_id=self.data["piece_id"],
            request_id=result.request.identity.request_id,
            tactic=result.selection.selected_tactic_id,
            adopted=outcome == "activated",
            snapshot=public,
            history=history,
            player_id=player,
            target_packet_ids=tuple(p.packet_id for p in public.own.attack_packets)
            if result.selection.selected_tactic_id in ("cancel", "counter")
            else (),
        )
        self.ledger.append(diagnostics)
        template_selection = self.phase.selection
        self.ledger_metadata.append(
            {
                "selected_template": copy.deepcopy(self.last_payload.get("search", {}).get("selected_template")),
                "phase_after": self.phase.phase_snapshot().to_dict(),
                "switch_reason": self.phase.exit_reason,
                "template_score": template_selection.candidate.score
                if template_selection and template_selection.candidate
                else None,
                "template_probability": template_selection.probability
                if template_selection
                else None,
                "template_rng_position": template_selection.rng_position
                if template_selection
                else None,
            }
        )
        diagnostics_dict = diagnostics.to_dict()
        self.last_payload["nextgen"] = diagnostics_dict
        self.last_payload["template_phase"] = self.phase.diagnostics()
        return replace(
            record,
            nextgen_diagnostics=diagnostics_dict,
            requested_action=candidate.root_action,
            executed_action=receipt.executed_action,
            outcome=outcome,
        )

    def decision_record(
        self,
        index,
        *,
        reward_components,
        elapsed_match_ticks,
        next_decision_id,
        **kwargs,
    ):
        """Bind caller-observed rewards/terminal data to the exact emitted receipt."""
        from train.nextgen_trajectory import DecisionRecord

        return DecisionRecord(
            self.ledger[index],
            reward_components,
            elapsed_match_ticks,
            next_decision_id,
            # Extended search traces stay in the replay/ledger metadata; the
            # trajectory decision schema keeps its existing fields.
            **{**{k: v for k, v in self.ledger_metadata[index].items()
                  if k != "selected_template"}, **kwargs},
        )
