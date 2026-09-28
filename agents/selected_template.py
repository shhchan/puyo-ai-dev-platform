"""Immutable, compiled selected-template constraints for both search engines.

Coordinates are zero based, bottom up. Colors are compact IDs 1..5; forbidden
color 0 means any occupied cell. Unlisted cells are unconstrained. No catalog
matching or rebinding occurs here. Satisfied required cells must survive every
subsequent resolved placement, including after completion.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct
import time

from agents.compact_search import CompactSearchState

SCHEMA = "puyo.selected_template.v1"
ABI_VERSION = 1


@dataclass(frozen=True, slots=True)
class SelectedTemplate:
    template_id: str
    variant_id: str
    transform: str
    binding: tuple[tuple[str, int], ...]
    required_cells: tuple[tuple[int, int, int], ...]
    forbidden_cells: tuple[tuple[int, int, int], ...] = ()
    schema_version: str = SCHEMA

    def __post_init__(self):
        if self.schema_version != SCHEMA:
            raise ValueError("unsupported selected-template schema")
        for name in (self.template_id, self.variant_id, self.transform):
            if not isinstance(name, str) or not name or len(name.encode()) > 4096:
                raise ValueError("selected-template identity must be a bounded string")
        binding = tuple(sorted(tuple(item) for item in self.binding))
        if not 1 <= len(binding) <= 5 or len({v[0] for v in binding}) != len(binding):
            raise ValueError("selected-template binding requires unique labels")
        if len({v[1] for v in binding}) != len(binding):
            raise ValueError("selected-template binding must be injective")
        for label, color in binding:
            if (
                not isinstance(label, str)
                or not label
                or len(label.encode()) > 4096
                or type(color) is not int
                or not 1 <= color <= 5
            ):
                raise ValueError("invalid selected-template binding")
        object.__setattr__(self, "binding", binding)
        colors = {c for _, c in binding}
        for name in ("required_cells", "forbidden_cells"):
            cells = tuple(sorted(tuple(cell) for cell in getattr(self, name)))
            if len(cells) > 84 * 6 or len(set(cells)) != len(cells):
                raise ValueError("duplicate or excessive selected-template cells")
            for x, y, color in cells:
                if (
                    any(type(v) is not int for v in (x, y, color))
                    or not 0 <= x < 6
                    or not 0 <= y < 14
                    or color
                    not in colors | ({0} if name == "forbidden_cells" else set())
                ):
                    raise ValueError("invalid selected-template cell")
            if name == "required_cells" and (
                not cells or len({(x, y) for x, y, _ in cells}) != len(cells)
            ):
                raise ValueError("required cells must have unique coordinates")
            object.__setattr__(self, name, cells)
        for x, y, color in self.required_cells:
            if (x, y, color) in self.forbidden_cells or (
                x,
                y,
                0,
            ) in self.forbidden_cells:
                raise ValueError("contradictory selected-template cells")

    def to_bytes(self) -> bytes:
        result = bytearray(struct.pack("<H", ABI_VERSION))
        for value in (
            self.schema_version,
            self.template_id,
            self.variant_id,
            self.transform,
        ):
            encoded = value.encode()
            result.extend(struct.pack("<H", len(encoded)) + encoded)
        result.append(len(self.binding))
        for label, color in self.binding:
            encoded = label.encode()
            result.extend(struct.pack("<H", len(encoded)) + encoded + bytes([color]))
        for cells in (self.required_cells, self.forbidden_cells):
            result.extend(struct.pack("<H", len(cells)))
            for cell in cells:
                result.extend(bytes(cell))
        return bytes(result)

    @property
    def semantic_digest(self) -> str:
        return hashlib.sha256(self.to_bytes()).hexdigest()

    @classmethod
    def from_bytes(cls, payload: bytes):
        from agents.deep_chain_native import _Reader, IncompatibleSchemaError

        r = _Reader(payload, failing_tag=0x8007)
        if r.u16("selected-template ABI") != ABI_VERSION:
            raise IncompatibleSchemaError("unsupported selected-template ABI")
        schema, identity, variant, transform = (
            r.string("template identity") for _ in range(4)
        )
        if schema != SCHEMA:
            raise IncompatibleSchemaError("unsupported selected-template schema")
        binding = tuple(
            (r.string("binding label"), r.u8("binding color"))
            for _ in range(r.u8("binding count"))
        )
        required, forbidden = (
            tuple(
                tuple(r.u8("cell") for _ in range(3))
                for _ in range(r.u16("cell count"))
            )
            for _ in range(2)
        )
        r.finish()
        value = cls(identity, variant, transform, binding, required, forbidden, schema)
        if value.to_bytes() != payload:
            raise ValueError("selected-template encoding is not canonical")
        return value

    def evaluate(
        self, state: CompactSearchState, parent: CompactSearchState
    ) -> tuple[bool, bool]:
        complete = True
        for x, y, color in self.required_cells:
            bit = 1 << (y * 6 + x)
            matches = bool(state.planes[color - 1] & bit)
            if not matches:
                complete = False
                if state.occupied_mask & bit or parent.planes[color - 1] & bit:
                    return False, False
        for x, y, color in self.forbidden_cells:
            plane = state.occupied_mask if color == 0 else state.planes[color - 1]
            if plane & (1 << (y * 6 + x)):
                return False, False
        return True, complete


def validate_public_template_input(template, known_pairs, config):
    if template is not None:
        if not isinstance(template, SelectedTemplate):
            raise ValueError("selected_template must be an immutable SelectedTemplate")
        if not 1 <= len(known_pairs) <= 3:
            raise ValueError("selected-template accepts public current/NEXT/NEXT2 only")
        if config.fire_context != "safe_build":
            raise ValueError(
                "selected-template must not constrain the survival safety path"
            )


def new_template_record():
    return {
        "checks": 0,
        "rejected": 0,
        "_check_ns": 0,
        "root_violation": False,
        "known_witness": (),
        "sampled_witness": (),
        "sampled_scenario_id": None,
    }


def check_template(
    template,
    records,
    action,
    state,
    parent,
    path,
    known_count,
    initial_valid,
    scenario_id,
):
    record = records[action]
    record["checks"] += 1
    started = time.perf_counter_ns()
    valid, complete = template.evaluate(state, parent)
    record["_check_ns"] += time.perf_counter_ns() - started
    if not initial_valid or not valid:
        record["rejected"] += 1
        if len(path) == 1:
            record["root_violation"] = True
        return False
    if complete and not state.game_over:
        name = "known_witness" if len(path) <= known_count else "sampled_witness"
        previous = record[name]
        if (
            not previous
            or (len(path), path) < (len(previous), previous)
            or (
                name == "sampled_witness"
                and path == previous
                and scenario_id < record["sampled_scenario_id"]
            )
        ):
            record[name] = path
            if name == "sampled_witness":
                record["sampled_scenario_id"] = scenario_id
    return True


def template_result(template, records, counters, representatives):
    if template is None:
        return None
    roots = {}
    for action, value in sorted(records.items()):
        record = {key: item for key, item in value.items() if not key.startswith("_")}
        record["status"] = (
            "violated"
            if record["root_violation"]
            else "known_complete"
            if record["known_witness"]
            else "cutoff"
            if counters.budget_exhausted
            else "unknown"
        )
        record["reason"] = (
            "resolved_constraint_violation"
            if record["root_violation"]
            else "public_prefix_completion"
            if record["known_witness"]
            else "request_node_quota"
            if counters.budget_exhausted
            else "sampled_completion_only"
            if record["sampled_witness"]
            else "no_completion_witness"
        )
        record["known_scenario_id"] = None  # Public prefix is scenario-independent.
        record["completion_source"] = (
            "public"
            if record["known_witness"]
            else "sampled"
            if record["sampled_witness"]
            else None
        )
        record["compatible"] = (
            action in representatives and not record["root_violation"]
        )
        roots[action] = record
    return {
        "schema_version": SCHEMA,
        "semantic_digest": template.semantic_digest,
        "roots": roots,
    }


def decode_template_result(
    payload,
    template,
    counters,
    representatives,
    legal_roots,
    known_count,
    depth,
    legal_scenarios,
):
    from agents.deep_chain_native import _Reader, InvalidNativeInputError

    if template is None:
        if payload is not None:
            raise InvalidNativeInputError("unexpected selected-template result")
        return None
    if payload is None:
        raise InvalidNativeInputError("missing selected-template result")
    r = _Reader(payload, failing_tag=0x8307)
    if (
        r.u16("template result ABI") != ABI_VERSION
        or r.take(32, "template digest").hex() != template.semantic_digest
    ):
        raise InvalidNativeInputError("selected-template result identity mismatch")
    records = {}
    for _ in range(r.u16("template root count")):
        action = r.u8("template root action")
        checks, rejected = r.u64("checks"), r.u64("rejected")
        violation = r.u8("root violation")
        known, sampled = (
            tuple(r.u8("witness action") for _ in range(r.u8("witness length")))
            for _ in range(2)
        )
        sampled_scenario = r.u8("sampled witness scenario")
        if (
            (not sampled and sampled_scenario != 255)
            or (sampled and sampled_scenario not in legal_scenarios)
            or action in records
            or action not in legal_roots
            or rejected > checks
            or violation > 1
            or (violation and (not rejected or known or sampled))
            or any(a >= 22 for a in known + sampled)
            or any(path and path[0] != action for path in (known, sampled))
            or len(known) > min(known_count, depth)
            or (sampled and not known_count < len(sampled) <= depth)
        ):
            raise InvalidNativeInputError("invalid selected-template root evidence")
        records[action] = dict(
            checks=checks,
            rejected=rejected,
            root_violation=bool(violation),
            known_witness=known,
            sampled_witness=sampled,
            sampled_scenario_id=None if sampled_scenario == 255 else sampled_scenario,
        )
    r.u64("template check nanoseconds")
    r.finish()
    if (
        set(records) != set(legal_roots)
        or sum(v["checks"] for v in records.values()) != counters.generated_nodes
    ):
        raise InvalidNativeInputError("incomplete selected-template accounting")
    return template_result(template, records, counters, representatives)
