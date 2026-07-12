import unittest

from src.core.ojama import convert_score_to_ojama


class TestOjamaScoring(unittest.TestCase):
    def test_score_boundaries_use_integer_units_and_carry(self):
        expected = {
            0: (0, 0),
            40: (0, 40),
            69: (0, 69),
            70: (1, 0),
            71: (1, 1),
        }

        for score_delta, (units, carry) in expected.items():
            with self.subTest(score_delta=score_delta):
                result = convert_score_to_ojama(score_delta)
                self.assertEqual((result.units, result.carry), (units, carry))

    def test_carry_is_combined_once_with_only_new_score(self):
        first = convert_score_to_ojama(40)
        second = convert_score_to_ojama(29, first.carry)
        third = convert_score_to_ojama(1, second.carry)

        self.assertEqual((first.units, first.carry), (0, 40))
        self.assertEqual((second.units, second.carry), (0, 69))
        self.assertEqual((third.units, third.carry), (1, 0))

    def test_target_score_must_be_positive(self):
        with self.assertRaises(ValueError):
            convert_score_to_ojama(40, target_score_per_ojama=0)


if __name__ == "__main__":
    unittest.main()
