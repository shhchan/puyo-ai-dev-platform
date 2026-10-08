"""Exact wire proofs retain canonical diagnostics and strict schema boundaries."""
import copy
import json
import unittest
from unittest.mock import patch

from agents import nextgen_contracts as c
from puyo_env.nextgen_scheduler import decode_nextgen_payload
from tests import test_nextgen_reader_decode as fixtures


class ExactWireProofTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scheduler, cls.action, cls.payload = fixtures.ReaderDecodeTests().prepare()

    def test_valid_proof_preserves_canonical_bytes_without_second_wire_tree(self):
        original = c.Diagnostics.from_dict(self.payload['nextgen'])
        with patch.object(c.Diagnostics, 'to_dict', side_effect=AssertionError('extra tree')):
            decoded = decode_nextgen_payload(copy.deepcopy(self.payload))
            self.assertIsNotNone(decoded.decoded())
        def canonical(value):
            return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(canonical(original.to_dict()), canonical(decoded.decoded().to_dict()))
        self.assertIsNone(self.scheduler.accept(decoded, self.action))
        self.assertEqual(canonical(original.to_dict()), canonical(self.scheduler.result.to_dict()))

    def test_serialization_exceptions_in_mutated_values_lose_proof(self):
        class BrokenWire:
            def __init__(self, error):
                self.error = error

            def __reduce__(self):
                raise self.error("invalid mutated wire")

        for error in (ValueError, RuntimeError, TypeError):
            with self.subTest(error=error):
                decoded = decode_nextgen_payload(copy.deepcopy(self.payload))
                decoded['nextgen']['request']['identity']['request_id'] = BrokenWire(error)
                self.assertIsNone(decoded.decoded())
                self.assertIsNotNone(self.scheduler.accept(decoded, self.action))
                self.assertEqual(self.scheduler.accept(decoded, self.action),
                                 self.scheduler.accept(dict(decoded), self.action))

    def test_mutations_never_reuse_a_different_wire_proof(self):
        for mutation in ('missing', 'extra', 'list_to_tuple', 'bool_to_int', 'int_to_float', 'selection', 'unpicklable'):
            with self.subTest(mutation=mutation):
                decoded = decode_nextgen_payload(copy.deepcopy(self.payload))
                request = decoded['nextgen']['request']
                if mutation == 'missing':
                    del request['identity']['request_id']
                elif mutation == 'extra':
                    request['identity']['unknown'] = 1
                elif mutation == 'list_to_tuple':
                    request['execution']['reachable_mask'] = tuple(request['execution']['reachable_mask'])
                elif mutation == 'bool_to_int':
                    request['execution']['reachable_mask'][0] = int(request['execution']['reachable_mask'][0])
                elif mutation == 'int_to_float':
                    request['identity']['player_id'] = float(request['identity']['player_id'])
                elif mutation == 'selection':
                    decoded['nextgen']['selection']['batch_digest'] = 'f' * 64
                else:
                    request['identity']['request_id'] = lambda: None
                self.assertIsNone(decoded.decoded())
                self.assertEqual(self.scheduler.accept(decoded, self.action),
                                 self.scheduler.accept(dict(decoded), self.action))
                if mutation != 'list_to_tuple':
                    self.assertIsNotNone(self.scheduler.accept(decoded, self.action))


if __name__ == '__main__':
    unittest.main()
