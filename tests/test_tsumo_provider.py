import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval.realtime_arena import match_from_replay
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.constants import PuyoColor
from src.core.tsumo import EsportsTsuSource, PuyoSequence


def colors(pair):
    return tuple(puyo.color for puyo in pair)


class TestTsumoProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "synthetic-haipuyo.txt"
        first = b"rgyb" * 64
        alternate = first[:6] + b"yrbg" * 62 + b"yr"
        second = b"pbgy" * 64
        cls.path.write_bytes(first + b"\n" + alternate + b"\n" + (first + b"\n") * 65533 + second + b"\n")
        cls.checksum = hashlib.sha256(cls.path.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def source(self):
        with patch("src.core.tsumo.ESPORTS_SOURCE_SHA256", self.checksum):
            return EsportsTsuSource(self.path)

    def test_source_checksum_format_and_id_bounds(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            EsportsTsuSource(self.path)
        source = self.source()
        self.assertEqual(len(source.rows), 65536)
        for bad in (-1, 65536, True, "0"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                source.sequence(bad)

    def test_axis_child_color_mapping_and_128_pair_repeat(self):
        sequence = self.source().sequence(65535)
        self.assertEqual(colors(sequence.next_pair()), (PuyoColor.RED, PuyoColor.BLUE))
        self.assertEqual(colors(sequence.next_pair()), (PuyoColor.GREEN, PuyoColor.YELLOW))
        sequence.next_pairs(125)
        self.assertEqual(colors(sequence.next_pair()), (PuyoColor.GREEN, PuyoColor.YELLOW))
        self.assertEqual(colors(sequence.next_pair()), (PuyoColor.RED, PuyoColor.BLUE))

    def test_deepcopy_shares_corpus_but_keeps_independent_cursor(self):
        source = self.source()
        sequence = source.sequence(0)
        clone = copy.deepcopy(sequence)
        self.assertIs(clone.source, source)
        self.assertIsNot(clone, sequence)
        clone.next_pair()
        self.assertEqual(sequence.cursor, 0)
        self.assertEqual(clone.cursor, 1)
        from src.core.game import GameState
        game = GameState(puyo_sequence=sequence)
        game_clone = copy.deepcopy(game)
        self.assertIs(game_clone.puyo_sequence.source, source)
        self.assertIsNot(game_clone.puyo_sequence, sequence)

    def test_versus_shared_and_independent_distribution(self):
        with patch("src.core.tsumo.ESPORTS_SOURCE_SHA256", self.checksum):
            common = {"tsumo_mode": "esports_tsu", "tsumo_source": str(self.path), "tsumo_pattern_id": 0}
            shared = RealtimeVersusMatch(seed=7, **common)
            independent = RealtimeVersusMatch(seed=7, tsumo_player_1_pattern_id=65535, **common)
            self.assertEqual(shared.player_states["player_0"].simulator.game.current_puyo_1.color,
                             shared.player_states["player_1"].simulator.game.current_puyo_1.color)
            self.assertNotEqual(independent.player_states["player_0"].simulator.game.current_puyo_2.color,
                                independent.player_states["player_1"].simulator.game.current_puyo_2.color)
            self.assertEqual(independent.replay_rules()["tsumo"]["color_mapping"]["player_1"]["p"], "RED")
            replay = {"seed": 7, "match_rules": independent.replay_rules()}
            # The replay identity gate deliberately only accepts the published source.
            replay["match_rules"]["tsumo"]["source_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "source identity"):
                match_from_replay(replay)

    def test_hidden_future_and_pattern_id_do_not_change_public_snapshot(self):
        with patch("src.core.tsumo.ESPORTS_SOURCE_SHA256", self.checksum):
            common = {"seed": 7, "tsumo_mode": "esports_tsu", "tsumo_source": str(self.path)}
            first = RealtimeVersusMatch(tsumo_pattern_id=0, **common)
            alternate = RealtimeVersusMatch(tsumo_pattern_id=1, **common)
            self.assertNotEqual(first.player_states["player_0"].simulator.game.puyo_sequence.source.rows[0],
                                alternate.player_states["player_0"].simulator.game.puyo_sequence.source.rows[1])
            self.assertEqual(first.public_snapshot().digest, alternate.public_snapshot().digest)

    def test_policy_infos_expose_visible_pairs_without_private_provider(self):
        try:
            from puyo_env.single_env import SinglePuyoEnv
            from puyo_env.versus_env import VersusPuyoEnv
            from puyo_env.realtime_ai import build_realtime_info
        except ImportError:
            self.skipTest("gymnasium/numpy unavailable")
        with patch("src.core.tsumo.ESPORTS_SOURCE_SHA256", self.checksum):
            kwargs = {"tsumo_mode": "esports_tsu", "tsumo_source": str(self.path), "tsumo_pattern_id": 0}
            single = SinglePuyoEnv(**kwargs)
            _, info = single.reset()
            self.assert_public_simulator(info["simulator"], single.simulator)
            versus = VersusPuyoEnv(**kwargs)
            _, infos = versus.reset()
            self.assert_public_simulator(infos["player_0"]["simulator"], versus.player_states["player_0"].simulator)
            self.assert_public_simulator(infos["player_0"]["opponent_simulator"], versus.player_states["player_1"].simulator)
            match = RealtimeVersusMatch(seed=7, **kwargs)
            realtime_info = build_realtime_info(match, "player_0", use_reachable_action_mask=False)
            for key, agent in (("simulator", "player_0"), ("realtime_simulator", "player_0"),
                               ("opponent_simulator", "player_1"), ("opponent_realtime_simulator", "player_1")):
                self.assert_public_simulator(realtime_info[key], match.player_states[agent].simulator)
            legacy = RealtimeVersusMatch(seed=7)
            legacy_info = build_realtime_info(legacy, "player_0", use_reachable_action_mask=False)
            self.assertIs(legacy_info["realtime_simulator"], legacy.player_states["player_0"].simulator)

    def assert_public_simulator(self, public, private):
        self.assertIsNot(public, private)
        self.assertIsInstance(public.game.puyo_sequence, PuyoSequence)
        self.assertFalse(hasattr(public.game.puyo_sequence, "pattern_id"))
        self.assertFalse(hasattr(public.game.puyo_sequence, "source"))
        self.assertEqual(colors((public.game.current_puyo_1, public.game.current_puyo_2)),
                         colors((private.game.current_puyo_1, private.game.current_puyo_2)))
        self.assertEqual([colors(pair) for pair in public.game.next_puyo_queue],
                         [colors(pair) for pair in private.game.next_puyo_queue])

    def test_random_seed_preserves_pair_stream(self):
        from src.core.headless import HeadlessPuyoSimulator
        expected = PuyoSequence(seed=123)
        actual = HeadlessPuyoSimulator(seed=123).game
        self.assertEqual(colors((actual.current_puyo_1, actual.current_puyo_2)), colors(expected.next_pair()))
        self.assertEqual(colors(actual.next_puyo_queue[0]), colors(expected.next_pair()))
        self.assertEqual(colors(actual.next_puyo_queue[1]), colors(expected.next_pair()))

    @unittest.skipUnless(os.environ.get("PUYO_TSUMO_SOURCE"), "original source is external")
    def test_original_source_roundtrip_and_loop(self):
        source = EsportsTsuSource(os.environ["PUYO_TSUMO_SOURCE"])
        for pattern_id in (0, 34066, 65535):
            with self.subTest(pattern_id=pattern_id):
                sequence = source.sequence(pattern_id)
                reverse = {color: letter for letter, color in sequence.color_mapping.items()}
                observed = "".join(reverse[color].lower() for pair in sequence.next_pairs(128) for color in colors(pair))
                self.assertEqual(observed.encode(), source.rows[pattern_id])
                self.assertEqual(colors(sequence.next_pair()),
                                 colors(source.sequence(pattern_id).next_pair()))
