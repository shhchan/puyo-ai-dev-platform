import importlib
import json
import unittest
from dataclasses import replace
from pathlib import Path

from agents.compact_search import CompactSearchState, transition
from agents.chain_structure import load_chain_structure_config
from agents.deep_chain_native import (
    NativeDecisionRequest,
    NativeDeepChainBackend,
    encode_request,
    decode_request,
    request_sha256,
    EnvelopeSection,
    EnvelopeKind,
    encode_envelope,
    decode_envelope,
    REQUEST_SELECTED_TEMPLATE_TAG,
    _REQUEST_TAGS,
    IncompatibleSchemaError,
    InvalidNativeInputError,
    decode_response,
)
from agents.deep_chain_native_search import materialize_native_long_horizon_result
from agents.long_horizon_search import (
    LongHorizonSearchConfig,
    run_compact_long_horizon_search,
)
from agents.selected_template import SelectedTemplate
from src.core.constants import PuyoColor


def state(rows):
    planes = [0] * 6
    for y, row in enumerate(rows):
        for x, color in enumerate(row):
            if int(color):
                planes[int(color) - 1] |= 1 << (y * 6 + x)
    return CompactSearchState(tuple(planes))


def template(required=((0, 0, 1), (1, 0, 1), (2, 0, 1)), forbidden=()):
    return SelectedTemplate(
        "synthetic-persian",
        "base",
        "identity",
        (("A", 1), ("B", 2), ("C", 3)),
        required,
        forbidden,
    )


def config(**kwargs):
    return LongHorizonSearchConfig(
        **dict(
            depth=2,
            width=24,
            scenarios=1,
            minimum_chain_count=10,
            max_expanded_nodes=600,
            decision_seed=123,
            future_sampling_mode="legacy-fixed-six",
            **kwargs,
        )
    )


def request(board, selected, cfg=None, pairs=None, mode="oracle-1"):
    return NativeDecisionRequest(
        board,
        pairs or ((PuyoColor.RED, PuyoColor.BLUE),),
        cfg or config(),
        load_chain_structure_config(),
        "0" * 64,
        "qa",
        "1",
        "272",
        selected_template=selected,
        execution_mode=mode,
    )


class SelectedTemplateTests(unittest.TestCase):
    def test_wire_round_trip_canonical_identity_and_cache_separation(self):
        r = request(state(["110000"]), template())
        self.assertEqual(decode_request(encode_request(r)), r)
        variants = [
            replace(r, selected_template=None),
            replace(r, selected_template=replace(template(), transform="mirror")),
            replace(
                r,
                selected_template=replace(
                    template(), binding=(("A", 2), ("B", 1), ("C", 3))
                ),
            ),
        ]
        self.assertEqual(
            len({request_sha256(r), *(request_sha256(x) for x in variants)}), 4
        )
        with self.assertRaisesRegex(ValueError, "schema"):
            replace(template(), schema_version="future")
        with self.assertRaisesRegex(ValueError, "public"):
            replace(r, known_pairs=r.known_pairs * 4)
        with self.assertRaisesRegex(ValueError, "survival"):
            replace(
                r, search_config=replace(r.search_config, fire_context="forced_safety")
            )

    def test_explicit_forbidden_color_and_empty_leave_other_cells_free(self):
        selected = template(forbidden=((0, 1, 1),))
        self.assertEqual(
            selected.evaluate(state(["110000", "200000"]), state(["110000"])),
            (True, False),
        )
        self.assertFalse(
            selected.evaluate(state(["110000", "100000"]), state(["110000"]))[0]
        )
        self.assertFalse(
            template(forbidden=((0, 1, 0),)).evaluate(
                state(["110000", "200000"]), state(["110000"])
            )[0]
        )
        self.assertTrue(
            template().evaluate(state(["110000", "100000"]), state(["110000"]))[0]
        )

    def test_foundation_is_monotonic_after_completion(self):
        selected = template()
        complete = state(["111000"])
        self.assertEqual(selected.evaluate(complete, state(["110000"])), (True, True))
        self.assertFalse(selected.evaluate(state(["110000"]), complete)[0])
        self.assertEqual(selected.evaluate(state(["111200"]), complete), (True, True))

    def test_corpus_l_is_not_banned_but_destructive_root_is(self):
        corpus = json.loads(
            Path("tests/fixtures/puyo_271_regression_cases.json").read_text()
        )
        results = []
        for case in corpus["persian_counterexamples"]:
            results.append(
                run_compact_long_horizon_search(
                    state(case["rows_bottom_up"]),
                    request(state([]), None).known_pairs,
                    replace(config(), depth=1),
                    selected_template=template(),
                )
            )
        flat, corner = results
        self.assertEqual(flat.selected_template["roots"][7]["status"], "known_complete")
        self.assertEqual(corner.selected_template["roots"][7]["status"], "violated")
        self.assertGreater(len(corner.compatible_ranked_roots), 1)
        self.assertTrue(
            any(r["compatible"] for r in corner.selected_template["roots"].values())
        )

    def test_constraint_enters_beam_before_pruning(self):
        pairs = ((PuyoColor.RED, PuyoColor.BLUE), (PuyoColor.GREEN, PuyoColor.YELLOW))
        cfg = replace(config(), width=1)
        plain = run_compact_long_horizon_search(state([]), pairs, cfg)
        selected = run_compact_long_horizon_search(
            state([]), pairs, cfg, selected_template=template(required=((0, 0, 3),))
        )
        self.assertEqual(set(plain.representatives), {0})
        self.assertEqual(set(selected.representatives), {3})
        self.assertEqual(
            selected.selected_template["roots"][3]["known_witness"], (3, 0)
        )

    def test_shared_cache_includes_constraint_and_binding(self):
        from agents.deep_chain_search_backend import (
            LongHorizonBackendRequest,
            PythonLongHorizonSearchBackend,
        )
        from agents.nextgen_shared_search import SharedSearchCache

        req = request(state(["110000"]), template(), replace(config(), depth=1))
        backend_request = LongHorizonBackendRequest(
            req.state,
            req.known_pairs,
            req.search_config,
            req.evaluator_config,
            "qa",
            "1",
            "1",
            "0" * 64,
            "1",
            "0" * 64,
            "1",
            "0" * 64,
            1,
            False,
            False,
            req.selected_template,
        )
        cache = SharedSearchCache()
        backend = PythonLongHorizonSearchBackend()
        first, hit = cache.search(backend, backend_request)
        self.assertIsNone(hit)
        same, hit = cache.search(backend, replace(backend_request, request_id=2))
        self.assertEqual(
            first.result.deterministic_digest, same.result.deterministic_digest
        )
        self.assertEqual(first.diagnostics, same.diagnostics)
        self.assertIsNotNone(hit)
        changed, hit = cache.search(
            backend,
            replace(
                backend_request,
                selected_template=replace(template(), transform="mirror"),
            ),
        )
        self.assertIsNone(hit)
        self.assertIsNot(first, changed)

    def test_cutoff_unknown_sampled_and_public_completion(self):
        selected = template(required=((0, 0, 3),))
        pairs = ((PuyoColor.RED, PuyoColor.BLUE), (PuyoColor.GREEN, PuyoColor.YELLOW))
        known = run_compact_long_horizon_search(
            state([]), pairs, config(), selected_template=selected
        )
        self.assertTrue(
            any(
                v["status"] == "known_complete"
                for v in known.selected_template["roots"].values()
            )
        )
        sampled = run_compact_long_horizon_search(
            state([]),
            pairs[:1],
            replace(config(), depth=3, max_expanded_nodes=1600),
            selected_template=selected,
        )
        self.assertTrue(
            any(
                v["completion_source"] == "sampled"
                for v in sampled.selected_template["roots"].values()
            )
        )
        self.assertFalse(
            any(
                v["status"] == "known_complete"
                for v in sampled.selected_template["roots"].values()
            )
        )
        for record in sampled.selected_template["roots"].values():
            self.assertIsNone(record["known_scenario_id"])
            if not record["sampled_witness"]:
                self.assertIsNone(record["sampled_scenario_id"])
                continue
            sequence = next(
                s
                for s in sampled.scenario_sequences
                if s.scenario_id == record["sampled_scenario_id"]
            )
            board = state([])
            for cursor, action in enumerate(record["sampled_witness"]):
                child = transition(board, sequence.pair_at(cursor), action).state
                valid, complete = selected.evaluate(child, board)
                self.assertTrue(valid)
                board = child
            self.assertTrue(complete)

        cutoff = run_compact_long_horizon_search(
            state([]),
            pairs,
            replace(config(), max_expanded_nodes=1),
            selected_template=selected,
        )
        self.assertTrue(
            any(
                v["status"] == "cutoff"
                for v in cutoff.selected_template["roots"].values()
            )
        )
        unknown = run_compact_long_horizon_search(
            state([]), pairs, replace(config(), depth=1), selected_template=selected
        )
        self.assertTrue(
            any(
                v["status"] == "unknown"
                for v in unknown.selected_template["roots"].values()
            )
        )


try:
    native_module = importlib.import_module("_puyo_deep_chain_native")
except ImportError:
    native_module = None


@unittest.skipIf(native_module is None, "native extension is not installed")
class NativeSelectedTemplateTests(unittest.TestCase):
    def check_parity(self, r):
        python = run_compact_long_horizon_search(
            r.state,
            r.known_pairs,
            r.search_config,
            selected_template=r.selected_template,
        )
        for mode in ("oracle-1", "scenario-6"):
            req = replace(r, execution_mode=mode)
            native = materialize_native_long_horizon_result(
                NativeDeepChainBackend(native_module).decide(req), req
            )
            self.assertEqual(python.selected_template, native.selected_template)
            self.assertEqual(python.counters.to_dict(), native.counters.to_dict())
            self.assertEqual(python.ranked_roots, native.ranked_roots)
            self.assertEqual(python.deterministic_digest, native.deterministic_digest)
            self.assertEqual(
                {a: n.path for a, n in python.representatives.items()},
                {a: n.path for a, n in native.representatives.items()},
            )
        return python

    def test_matrix_and_multiple_compatible_roots(self):
        for rows in (["110000"], ["110000", "100000"]):
            for selected in (None, template(), template(forbidden=((0, 1, 1),))):
                with self.subTest(rows=rows, template=selected):
                    value = self.check_parity(request(state(rows), selected))
                    if selected is not None and not selected.forbidden_cells:
                        self.assertGreater(len(value.compatible_ranked_roots), 1)

    def test_mirror_color_permutation_and_tt(self):
        for selected, board in (
            (
                replace(
                    template(),
                    transform="mirror",
                    required_cells=((3, 0, 1), (4, 0, 1), (5, 0, 1)),
                ),
                state(["000011"]),
            ),
            (
                replace(
                    template(),
                    binding=(("A", 2), ("B", 1), ("C", 3)),
                    required_cells=((0, 0, 2), (1, 0, 2), (2, 0, 2)),
                ),
                state(["220000"]),
            ),
        ):
            self.check_parity(request(board, selected))
        for enabled in (False, True):
            self.check_parity(
                request(
                    state(["110000"]),
                    template(),
                    replace(config(), use_transposition_table=enabled),
                )
            )

    def test_cutoff_public_and_sampled_parity(self):
        selected = template(required=((0, 0, 3),))
        pairs = ((PuyoColor.RED, PuyoColor.BLUE), (PuyoColor.GREEN, PuyoColor.YELLOW))
        self.check_parity(request(state([]), selected, pairs=pairs))
        self.check_parity(
            request(state([]), selected, replace(config(), width=1), pairs=pairs)
        )
        self.check_parity(
            request(
                state([]), selected, replace(config(), depth=3, max_expanded_nodes=1600)
            )
        )
        self.check_parity(
            request(
                state([]),
                selected,
                replace(config(), scenarios=6, max_expanded_nodes=1),
            )
        )

    def test_required_extension_fails_closed(self):
        r = request(state([]), template())
        sections = decode_envelope(
            encode_request(r),
            known_tags=_REQUEST_TAGS | {REQUEST_SELECTED_TEMPLATE_TAG},
        ).sections
        bad = tuple(
            replace(s, payload=b"\x02\x00" + s.payload[2:])
            if s.tag == REQUEST_SELECTED_TEMPLATE_TAG
            else s
            for s in sections
        )
        with self.assertRaises(IncompatibleSchemaError):
            decode_response(
                native_module.decide(
                    encode_envelope(EnvelopeKind.REQUEST, r.request_id, bad)
                )
            )
        # Old readers cannot silently discard the required constraint section.
        with self.assertRaises(IncompatibleSchemaError):
            decode_envelope(encode_request(r), known_tags=_REQUEST_TAGS)
        private = replace(r, selected_template=None, known_pairs=r.known_pairs * 4)
        private_sections = decode_envelope(
            encode_request(private), known_tags=_REQUEST_TAGS
        ).sections
        template_section = next(
            s for s in sections if s.tag == REQUEST_SELECTED_TEMPLATE_TAG
        )
        with self.assertRaises(InvalidNativeInputError):
            decode_response(
                native_module.decide(
                    encode_envelope(
                        EnvelopeKind.REQUEST,
                        r.request_id,
                        private_sections + (template_section,),
                    )
                )
            )
        result = NativeDeepChainBackend(native_module).decide(r)
        with self.assertRaisesRegex(
            InvalidNativeInputError, "missing selected-template"
        ):
            materialize_native_long_horizon_result(
                replace(result, selected_template=None), r
            )
        with self.assertRaisesRegex(InvalidNativeInputError, "identity"):
            materialize_native_long_horizon_result(
                result,
                replace(r, selected_template=replace(template(), transform="mirror")),
            )


if __name__ == "__main__":
    unittest.main()
