"""Diagnostic clocks must retain nesting and cross-frame reader attribution."""
import unittest
from unittest.mock import patch

from eval.puyo_269_frame_diagnostic import Recorder


class FrameDiagnosticTests(unittest.TestCase):
    def test_wall_and_current_thread_cpu_are_separate(self):
        recorder = Recorder()
        recorder.frame = 4
        with patch('eval.puyo_269_frame_diagnostic.time.perf_counter_ns', side_effect=[1_000_000, 9_000_000]), patch(
            'eval.puyo_269_frame_diagnostic.time.thread_time_ns', side_effect=[2_000_000, 5_000_000]
        ):
            token = recorder.start('reader_decode')
            recorder.frame = 5
            recorder.end(token)
        row = recorder.rows[0]
        self.assertEqual((row['frame_start'], row['frame_end']), (4, 5))
        self.assertEqual((row['wall_ms'], row['thread_cpu_ms']), (8, 3))

    def test_nested_scope_restores_after_failure(self):
        recorder = Recorder()
        def fail():
            raise ValueError('fixture')
        inner = recorder.scoped('deepcopy', fail)
        with self.assertRaises(ValueError):
            recorder.wrap('activation', inner)()
        self.assertEqual([(r['label'], r['parent']) for r in recorder.rows],
                         [('deepcopy', 'activation'), ('activation', None)])
        self.assertEqual(recorder.local.stack, ())
        count = len(recorder.rows)
        with self.assertRaises(ValueError):
            inner()
        self.assertEqual(len(recorder.rows), count)

    def test_gc_records_generation_and_collection_count(self):
        recorder = Recorder()
        recorder.gc_callback('start', {'generation': 2})
        recorder.gc_callback('stop', {'generation': 2, 'collected': 0, 'uncollectable': 0})
        row = recorder.rows[0]
        self.assertEqual((row['label'], row['generation'], row['collected']), ('gc_collect', 2, 0))
        self.assertEqual(recorder.gc_started, {})


if __name__ == '__main__':
    unittest.main()
