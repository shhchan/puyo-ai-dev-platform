"""Versioned inference binding preserves old data and public backend inputs."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from agents import nextgen_contracts as c
from agents.nextgen_shared_search import SharedSearchBatchBuilder
from tests.test_nextgen_shared_search import CountingBackend, config, request
from tests.test_nextgen_contracts import make_diagnostics
from agents.nextgen_tactic_manager import feature_summaries


def bind(req, *, hidden=((0,) * 6,) * 2):
    return c.PublicBoardInference(
        "public-episode", req.identity.player_id, req.execution.request_tick,
        c.semantic_digest(req.public.own.visible_board), "0:0:lock", "known", hidden,
        "public_actual_lock", c.inference_request_digest(req.identity, req.execution),
    )


class InferenceWireTests(unittest.TestCase):
    def test_legacy_wire_roundtrip_and_nested_digest_are_exact(self):
        path = Path(__file__).parent / "fixtures" / "puyo_266_continuation_walls.json"
        for wire in json.loads(path.read_text()).values():
            req = c.NextgenRequest.from_dict(wire)
            self.assertEqual(req.to_dict(), wire)
            self.assertEqual(c.semantic_digest(req), c.semantic_digest(wire))
            self.assertEqual(c.from_json(req.to_json()), req)
            self.assertIsNone(req.known_inference())
            self.assertEqual(c.semantic_digest({"request": req}),
                             c.semantic_digest({"request": wire}))

    def test_v2_roundtrip_explicit_binding_and_legacy_rejection(self):
        req = request()
        req = replace(req, inference=bind(req))
        self.assertEqual(c.from_json(req.to_json()), req)
        self.assertEqual(req.known_inference(), req.inference)
        with self.assertRaises(ValueError):
            replace(req, schema_version=c.LEGACY_REQUEST_SCHEMA_VERSION)
        wire = req.to_dict()
        del wire["inference"]
        with self.assertRaises(ValueError):
            c.NextgenRequest.from_dict(wire)

    def test_missing_unknown_or_misbound_inference_is_unusable(self):
        req = request()
        good = bind(req)
        values = [None, replace(good, observed_tick=1), replace(good, player_id=1),
                  replace(good, visible_digest=c.semantic_digest("different")),
                  replace(good, request_digest=None),
                  replace(good, status="unknown", hidden_rows=((None,) * 6,) * 2)]
        for value in values:
            self.assertIsNone(replace(req, inference=value).known_inference())

    def test_hidden_deduction_is_not_native_search_input(self):
        cfg = config()
        req = request(cfg, board=((None,) * 6,) * 2 + ((0,) * 6,) * 12)
        backend = CountingBackend()
        provider = SharedSearchBatchBuilder(backend, cfg)
        provider.build(req)
        provider.build(replace(req, inference=bind(req, hidden=((1, 0, 0, 0, 0, 0), (0,) * 6))))
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(backend.calls[0], backend.calls[1])
        self.assertFalse(backend.calls[1].root_state.occupied_mask)

    def test_actor_features_do_not_encode_raw_hidden_sidecar(self):
        diagnostic = make_diagnostics()
        req = replace(diagnostic.request, schema_version=c.REQUEST_SCHEMA_VERSION)
        inferred = replace(req, inference=bind(req, hidden=((1,) * 6, (2,) * 6)))
        timing = SimpleNamespace(feature_summaries=lambda: {})
        features = c.build_features(feature_summaries(req, diagnostic.batch, timing),
                                    diagnostic.batch.action_mask)
        self.assertEqual(features, c.build_features(
            feature_summaries(inferred, diagnostic.batch, timing), diagnostic.batch.action_mask))


if __name__ == "__main__":
    unittest.main()
