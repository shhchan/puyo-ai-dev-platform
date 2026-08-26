import importlib
import json
import struct
import threading
import time
import unittest
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import agents.deep_chain_native as native_boundary
from agents.chain_structure import load_chain_structure_config
from agents.compact_search import CompactSearchState
from agents.deep_chain_native import (
    EnvelopeKind,
    EnvelopeSection,
    IncompatibleSchemaError,
    InvalidNativeInputError,
    NativeBackendUnavailableError,
    NativeDecisionRequest,
    NativeDeepChainBackend,
    UnsupportedNativeConfigError,
    decode_envelope,
    decode_request,
    decode_response,
    encode_envelope,
    encode_request,
    native_fallback_allowed,
    request_sha256,
)
from agents.long_horizon_search import LongHorizonSearchConfig
from src.core.constants import PuyoColor

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "eval" / "deep_chain_native_corpus.json"
MANIFEST_PATH = (
    ROOT
    / "docs"
    / "benchmarks"
    / "puyo-199-native-extension-contract"
    / "round_trip_manifest.json"
)


def _state_from_payload(payload):
    return CompactSearchState(
        planes=tuple(int(value, 16) for value in payload["planes_hex"]),
        all_clear_bonus_pending=bool(payload["all_clear_bonus_pending"]),
        game_over=bool(payload["game_over"]),
        score=int(payload["score"]),
        last_chain_end_score=int(payload["last_chain_end_score"]),
    )


def _pairs_from_payload(payload):
    return tuple(tuple(PuyoColor[name] for name in pair) for pair in payload)


class NativeRequestFixture:
    @classmethod
    def setUpClass(cls):
        cls.corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
        cls.evaluator_config = load_chain_structure_config()

    def make_request(self, case, *, request_id=123):
        search_payload = self.corpus["search_case"]["config"]
        scenario = case["scenario"]
        return NativeDecisionRequest(
            state=_state_from_payload(case["state"]),
            known_pairs=_pairs_from_payload(scenario["known_pairs"]),
            search_config=LongHorizonSearchConfig(
                depth=int(search_payload["depth"]),
                width=int(search_payload["width"]),
                scenarios=int(search_payload["scenarios"]),
                minimum_chain_count=int(search_payload["minimum_chain_count"]),
                max_expanded_nodes=int(search_payload["max_expanded_nodes"]),
                decision_seed=int(scenario["decision_seed"]),
                future_sampling_mode=str(scenario["future_sampling_mode"]),
            ),
            evaluator_config=self.evaluator_config,
            config_digest=self.corpus["config_sha256"],
            profile_name="smoke",
            profile_version="1.0",
            config_version="v1.0",
            request_id=request_id,
        )


class TestDeepChainNativePythonContract(NativeRequestFixture, unittest.TestCase):
    def test_locked_schema_identities_track_python_contract_sources(self):
        from agents.chain_structure import (
            CHAIN_STRUCTURE_FEATURE_VERSION,
            CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION,
            ChainStructureWeights,
        )
        from agents.compact_search import COMPACT_SEARCH_SCHEMA_VERSION
        from agents.deep_chain_builder import DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION
        from agents.long_horizon_search import (
            EXPECTED_CHAIN_RANKING_RULE_VERSION,
            FUTURE_SAMPLING_SCHEMA_VERSION,
            TERMINAL_FIRE_SCORE_VERSION,
        )
        from puyo_env.actions import NUM_ACTIONS

        self.assertEqual(
            native_boundary.COMPACT_SEARCH_SCHEMA_VERSION,
            COMPACT_SEARCH_SCHEMA_VERSION,
        )
        self.assertEqual(
            native_boundary.FUTURE_SAMPLING_SCHEMA_VERSION,
            FUTURE_SAMPLING_SCHEMA_VERSION,
        )
        self.assertEqual(
            native_boundary.EXPECTED_CHAIN_RANKING_RULE_VERSION,
            EXPECTED_CHAIN_RANKING_RULE_VERSION,
        )
        self.assertEqual(
            native_boundary.TERMINAL_FIRE_SCORE_VERSION,
            TERMINAL_FIRE_SCORE_VERSION,
        )
        self.assertEqual(
            native_boundary.DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION,
            DEEP_CHAIN_DIAGNOSTICS_SCHEMA_VERSION,
        )
        self.assertEqual(
            native_boundary.CHAIN_STRUCTURE_FEATURE_VERSION,
            CHAIN_STRUCTURE_FEATURE_VERSION,
        )
        self.assertEqual(
            native_boundary.CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION,
            CHAIN_STRUCTURE_WEIGHT_SCHEMA_VERSION,
        )
        self.assertEqual(
            native_boundary._WEIGHT_NAMES,
            tuple(item.name for item in fields(ChainStructureWeights)),
        )
        self.assertEqual(native_boundary.NUM_ACTIONS, NUM_ACTIONS)

    def test_python_codec_round_trips_all_frozen_corpus_inputs_losslessly(self):
        for index, case in enumerate(self.corpus["cases"], start=1):
            with self.subTest(case=case["case_id"]):
                request = self.make_request(case, request_id=index)
                decoded = decode_request(encode_request(request))

                self.assertEqual(decoded.state.to_bytes(), request.state.to_bytes())
                self.assertEqual(decoded.known_pairs, request.known_pairs)
                self.assertEqual(decoded.search_config, request.search_config)
                self.assertEqual(decoded.evaluator_config, request.evaluator_config)
                self.assertEqual(decoded.config_digest, request.config_digest)
                self.assertEqual(decoded.request_id, index)

    def test_boundary_preserves_hidden_rows_ojama_flags_and_large_scores(self):
        state = CompactSearchState(
            planes=(1, 0, 0, 0, 1 << 78, 1 << 83),
            all_clear_bonus_pending=True,
            game_over=True,
            score=(1 << 63) + 17,
            last_chain_end_score=(1 << 63) + 9,
        )
        request = self.make_request(self.corpus["cases"][0], request_id=99)
        request = NativeDecisionRequest(
            state=state,
            known_pairs=request.known_pairs,
            search_config=request.search_config,
            evaluator_config=request.evaluator_config,
            config_digest=request.config_digest,
            profile_name=request.profile_name,
            profile_version=request.profile_version,
            config_version=request.config_version,
            request_id=request.request_id,
        )

        decoded = decode_request(encode_request(request))

        self.assertEqual(decoded.state, state)
        self.assertEqual(decoded.state.planes[5], 1 << 83)
        self.assertTrue(decoded.state.all_clear_bonus_pending)
        self.assertTrue(decoded.state.game_over)

    def test_score_overflow_is_a_stable_invalid_input(self):
        request = self.make_request(self.corpus["cases"][0])
        oversized = CompactSearchState(score=1 << 64)
        request = NativeDecisionRequest(
            state=oversized,
            known_pairs=request.known_pairs,
            search_config=request.search_config,
            evaluator_config=request.evaluator_config,
            config_digest=request.config_digest,
            profile_name=request.profile_name,
            profile_version=request.profile_version,
            config_version=request.config_version,
        )

        with self.assertRaisesRegex(InvalidNativeInputError, "wire range"):
            encode_request(request)

    def test_framing_rejects_unknown_required_section_and_nonzero_padding(self):
        unknown = encode_envelope(
            EnvelopeKind.REQUEST,
            1,
            (EnvelopeSection(0x8FFF, 1, b"x"),),
        )
        with self.assertRaisesRegex(IncompatibleSchemaError, "unknown required"):
            decode_envelope(unknown, known_tags=frozenset())

        malformed = bytearray(unknown)
        malformed[-1] = 1
        with self.assertRaisesRegex(InvalidNativeInputError, "padding"):
            decode_envelope(malformed)

    def test_invalid_action_in_success_envelope_fails_closed(self):
        decision = bytearray((22, 1, 0, 0))
        decision.extend(struct.pack("<HH", 1, 0))
        digest = b"digest-v1"
        decision.extend(struct.pack("<H", len(digest)))
        decision.extend(digest)
        decision.append(0)
        counters = struct.pack("<" + "Q" * 9, *([0] * 9))
        provenance = bytearray()
        for value in (
            "native",
            "0.1.0",
            "revision",
            "rustc",
            "release",
            "x86_64-unknown-linux-gnu",
            "external",
            "oracle-1",
            "scalar",
        ):
            encoded = value.encode("utf-8")
            provenance.extend(struct.pack("<H", len(encoded)))
            provenance.extend(encoded)
        provenance.extend(struct.pack("<H", 1))
        empty_record_section = struct.pack("<HHII", 1, 0, 0, 0)
        payload = encode_envelope(
            EnvelopeKind.SUCCESS,
            1,
            (
                EnvelopeSection(native_boundary.RESULT_DECISION_TAG, 1, decision),
                EnvelopeSection(native_boundary.RESULT_COUNTERS_TAG, 1, counters),
                EnvelopeSection(
                    native_boundary.RESULT_ROOT_EVIDENCE_TAG,
                    1,
                    empty_record_section,
                ),
                EnvelopeSection(
                    native_boundary.RESULT_REPRESENTATIVES_TAG,
                    1,
                    empty_record_section,
                ),
                EnvelopeSection(
                    native_boundary.RESULT_DIAGNOSTICS_TAG,
                    1,
                    empty_record_section,
                ),
                EnvelopeSection(
                    native_boundary.RESULT_PROVENANCE_TAG,
                    1,
                    provenance,
                ),
            ),
        )

        with self.assertRaisesRegex(InvalidNativeInputError, "invalid action"):
            decode_response(payload)

    def test_canonical_mode_never_allows_silent_fallback(self):
        error = NativeBackendUnavailableError("missing", retry_safe=True)

        self.assertFalse(native_fallback_allowed("auto", error, canonical=True))
        self.assertTrue(native_fallback_allowed("auto", error, canonical=False))
        self.assertFalse(native_fallback_allowed("native", error, canonical=False))
        self.assertFalse(
            native_fallback_allowed(
                "auto",
                InvalidNativeInputError("bad request"),
                canonical=False,
            )
        )

    def test_missing_extension_is_mapped_to_backend_unavailable(self):
        with (
            mock.patch.object(
                importlib,
                "import_module",
                side_effect=ImportError("not installed"),
            ),
            self.assertRaisesRegex(
                NativeBackendUnavailableError,
                "unavailable",
            ),
        ):
            NativeDeepChainBackend()

    def test_production_builder_does_not_import_or_select_native_backend(self):
        import agents.deep_chain_builder as builder

        self.assertNotIn("NativeDeepChainBackend", builder.__dict__)
        self.assertNotIn("native", builder.DeepChainBuilderConfig.__dataclass_fields__)

    def test_checked_in_round_trip_manifest_matches_frozen_request(self):
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        actual_cases = []
        for request_id, case in enumerate(self.corpus["cases"], start=1):
            request = self.make_request(case, request_id=request_id)
            actual_cases.append(
                {
                    "case_id": case["case_id"],
                    "request_id": request_id,
                    "request_bytes": len(encode_request(request)),
                    "request_sha256": request_sha256(request),
                }
            )

        self.assertEqual(manifest["source_corpus_digest"], self.corpus["corpus_digest"])
        self.assertEqual(manifest["cases"], actual_cases)


try:
    NATIVE_MODULE = importlib.import_module(native_boundary.NATIVE_MODULE_NAME)
except (ImportError, OSError):
    NATIVE_MODULE = None


@unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
class TestDeepChainNativeExtension(NativeRequestFixture, unittest.TestCase):
    def setUp(self):
        self.backend = NativeDeepChainBackend(NATIVE_MODULE)

    def test_release_capabilities_are_machine_readable_and_compatible(self):
        capabilities = self.backend.capabilities
        payload = capabilities.to_dict()

        self.assertEqual(capabilities.build_profile, "release")
        self.assertEqual(capabilities.python_abi, "cp312")
        self.assertEqual(capabilities.target, "x86_64-unknown-linux-gnu")
        self.assertEqual(capabilities.simd_path, "scalar")
        self.assertIn("scalar", capabilities.cpu_features)
        self.assertEqual(capabilities.thread_modes, ("oracle-1",))
        self.assertTrue(payload["gil_detach"])
        self.assertFalse(payload["parallel"])
        self.assertTrue(payload["wheel_hash_hook"])
        self.assertEqual(
            payload["compact_hot_result"],
            {
                "abi_version": 1,
                "schema": "puyo.native_compact_hot_result.v1",
                "child_state_bytes": 80,
                "result_bytes": 24,
                "flags_mask": 0x0F,
            },
        )

    def test_native_codec_round_trips_the_full_frozen_corpus(self):
        for index, case in enumerate(self.corpus["cases"], start=1):
            with self.subTest(case=case["case_id"]):
                request = self.make_request(case, request_id=index)
                returned = self.backend.round_trip_request(request)

                self.assertEqual(returned.state.to_bytes(), request.state.to_bytes())
                self.assertEqual(returned.known_pairs, request.known_pairs)
                self.assertEqual(returned.search_config, request.search_config)
                self.assertEqual(returned.evaluator_config, request.evaluator_config)
                self.assertEqual(request_sha256(returned), request_sha256(request))

    def test_decide_reserves_one_call_contract_with_typed_unimplemented_error(self):
        request = self.make_request(self.corpus["cases"][0], request_id=777)

        with self.assertRaises(UnsupportedNativeConfigError) as captured:
            self.backend.decide(request)

        self.assertEqual(captured.exception.failing_tag, 0)
        self.assertTrue(captured.exception.retry_safe)
        self.assertEqual(
            captured.exception.provenance["build_profile"],
            "release",
        )

    def test_native_rejects_abi_mismatch_and_overlapping_planes(self):
        request = self.make_request(self.corpus["cases"][0], request_id=88)
        encoded = encode_request(request)
        mismatched = bytearray(encoded)
        mismatched[4:6] = struct.pack("<H", 2)

        with self.assertRaises(IncompatibleSchemaError):
            decode_response(NATIVE_MODULE.decide(bytes(mismatched)))

        envelope = decode_envelope(
            encoded,
            known_tags=native_boundary._REQUEST_TAGS,
        )
        sections = []
        for section in envelope.sections:
            payload = bytearray(section.payload)
            if section.tag == native_boundary.REQUEST_ROOT_STATE_TAG:
                payload[4] = 1
                payload[15] = 1
            sections.append(EnvelopeSection(section.tag, section.version, payload))
        malformed = encode_envelope(EnvelopeKind.REQUEST, 88, sections)

        with self.assertRaises(InvalidNativeInputError) as captured:
            decode_response(NATIVE_MODULE.decide(malformed))
        self.assertEqual(
            captured.exception.failing_tag,
            native_boundary.REQUEST_ROOT_STATE_TAG,
        )

    def test_long_running_native_probe_releases_the_gil(self):
        stop = threading.Event()
        ready = threading.Event()
        counter = [0]

        def advance():
            ready.set()
            while not stop.is_set():
                counter[0] += 1

        worker = threading.Thread(target=advance)
        worker.start()
        self.assertTrue(ready.wait(timeout=1.0))
        time.sleep(0.01)
        before = counter[0]
        iterations = NATIVE_MODULE._gil_probe(150)
        after = counter[0]
        stop.set()
        worker.join(timeout=1.0)

        self.assertGreater(iterations, 0)
        self.assertGreater(after, before)
        self.assertFalse(worker.is_alive())

    def test_canonical_adapter_rejects_debug_capability_provenance(self):
        capabilities = bytes(NATIVE_MODULE.capabilities())
        self.assertIn(b"\x07\x00release", capabilities)
        debug_capabilities = capabilities.replace(
            b"\x07\x00release",
            b"\x07\x00debugxx",
            1,
        )
        fake_module = SimpleNamespace(
            capabilities=lambda: debug_capabilities,
            decide=NATIVE_MODULE.decide,
            _round_trip_request=NATIVE_MODULE._round_trip_request,
        )

        with self.assertRaisesRegex(
            NativeBackendUnavailableError,
            "release build",
        ):
            NativeDeepChainBackend(fake_module, canonical=True)

        noncanonical = NativeDeepChainBackend(fake_module, canonical=False)
        self.assertEqual(noncanonical.capabilities.build_profile, "debugxx")


if __name__ == "__main__":
    unittest.main()
