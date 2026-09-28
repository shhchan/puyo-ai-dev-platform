"""Parent-local immutable decoding and cancellation during result handoff."""

import copy
from concurrent.futures import Future
from dataclasses import FrozenInstanceError
import queue
import threading
import unittest
from unittest.mock import patch

from agents import nextgen_contracts as c
from puyo_env.nextgen_scheduler import decode_nextgen_payload
from puyo_env.realtime_ai import PolicyProcessExecutor, RealtimePolicyController
from puyo_env.realtime_versus import RealtimeVersusMatch
from tests.test_nextgen_tactic_manager import policy


class ReaderDecodeTests(unittest.TestCase):
    def prepare(self):
        p = policy()
        match = RealtimeVersusMatch(seed=55)
        controller = RealtimePolicyController(p)
        scheduler = controller.nextgen_scheduler
        observation, info = scheduler.prepare(match, "player_0", controller.config)
        action = p.select_action(observation, info)
        payload = copy.deepcopy(p.tactical_diagnostics)
        return scheduler, action, payload

    def test_decoded_payload_skips_only_schema_reconstruction(self):
        scheduler, action, payload = self.prepare()
        decoded = decode_nextgen_payload(payload)
        with patch.object(c.Diagnostics, "from_dict", side_effect=AssertionError("UI parse")):
            self.assertIsNone(scheduler.accept(decoded, action))
        self.assertIs(type(scheduler.last_payload), dict)
        self.assertEqual(scheduler.result.to_dict(), payload["nextgen"])
        with self.assertRaises(FrozenInstanceError):
            scheduler.result.request.identity.request_id = "mutation"
        payload["nextgen"]["request"]["identity"]["request_id"] = "changed"
        self.assertNotEqual(scheduler.result.request.identity.request_id, "changed")

    def test_nested_mutation_invalidates_proof_and_keeps_original_error(self):
        scheduler, action, payload = self.prepare()
        decoded = decode_nextgen_payload(payload)
        decoded["nextgen"]["selection"]["batch_digest"] = "f" * 64
        self.assertIsNone(decoded.decoded())
        self.assertEqual(scheduler.accept(decoded, action), scheduler.accept(dict(decoded), action))
        self.assertTrue(scheduler.errors)

    def test_validated_old_request_still_fails_authoritative_request_comparison(self):
        scheduler, action, payload = self.prepare()
        decoded = decode_nextgen_payload(payload)
        scheduler.data["identity"] = c.DecisionIdentity(
            "next-episode", 0, 1, "request-1", scheduler.data["public"].digest
        )
        self.assertIn("worker request mismatch", scheduler.accept(decoded, action))

    def test_plain_and_invalid_payloads_keep_existing_path(self):
        scheduler, action, payload = self.prepare()
        with patch.object(c.Diagnostics, "from_dict", wraps=c.Diagnostics.from_dict) as parse:
            self.assertIsNone(scheduler.accept(payload, action))
            self.assertEqual(parse.call_count, 1)
        invalid = {"nextgen": {"bad": "value"}}
        self.assertIs(decode_nextgen_payload(invalid), invalid)
        self.assertEqual(scheduler.accept(invalid, action), scheduler.accept(decode_nextgen_payload(invalid), action))

    def test_cancel_during_parse_does_not_publish_or_stop_reader(self):
        executor = object.__new__(PolicyProcessExecutor)
        executor._result_queue = queue.Queue()
        first, second = Future(), Future()
        executor._futures = {0: first, 1: second}
        entered, release = threading.Event(), threading.Event()

        def decode(value):
            if value == "first":
                entered.set()
                self.assertTrue(release.wait(2))
            return value

        executor._result_queue.put((0, True, 1, .1, "first"))
        executor._result_queue.put((1, True, 2, .2, "second"))
        executor._result_queue.put(None)
        with patch("puyo_env.nextgen_scheduler.decode_nextgen_payload", side_effect=decode):
            reader = threading.Thread(target=executor._read_results)
            reader.start()
            try:
                self.assertTrue(entered.wait(2))
                # Shutdown can still find the decoding Future in its registry.
                self.assertIs(executor._futures[0], first)
                first.cancel()
            finally:
                release.set()
                reader.join(2)
        self.assertFalse(reader.is_alive())
        self.assertTrue(first.cancelled())
        self.assertEqual(second.result(timeout=1), (2, .2, "second"))
        self.assertEqual(executor._futures, {})

    def test_spawned_reader_and_process_are_cleaned_up(self):
        scheduler, action, _ = self.prepare()
        executor = PolicyProcessExecutor(scheduler.policy)
        try:
            future = executor.submit_policy({}, {
                "nextgen": scheduler.data,
                "action_mask": scheduler.data["execution"].reachable_mask,
                "action_mask_source": "reachable_planner",
            })
            selected, _, decoded = future.result(timeout=15)
            self.assertEqual(selected, action)
            self.assertIsNotNone(decoded.decoded())
            self.assertIsNone(scheduler.accept(decoded, selected))
        finally:
            executor.shutdown(wait=True)
        self.assertFalse(executor._process.is_alive())
        self.assertFalse(executor._reader.is_alive())


if __name__ == "__main__":
    unittest.main()
