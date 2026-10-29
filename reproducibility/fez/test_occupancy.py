"""Small exhaustive checks of the finite-shot occupancy calculation."""
import itertools
import unittest

import numpy as np

from verify_shots import occupancy_tail


class OccupancyTests(unittest.TestCase):
    def test_matches_exhaustive_outcome_sequences(self):
        # Outcomes 0 and 1 are distinct accepted assignments; 2 is rejection.
        weights = (.2, .3, .5)
        actual = occupancy_tail(weights[:2], maximum=6, target=2)
        for shots in range(7):
            expected = 0.0
            for outcomes in itertools.product(range(3), repeat=shots):
                if len(set(outcomes) & {0, 1}) < 2:
                    continue
                probability = 1.0
                for outcome in outcomes:
                    probability *= weights[outcome]
                expected += probability
            self.assertAlmostEqual(float(actual[shots]), expected, places=12)

    def test_at_least_one_acceptance(self):
        actual = occupancy_tail([.2, .3], maximum=12, target=1)
        np.testing.assert_allclose(actual, 1 - .5 ** np.arange(13), atol=1e-12)

    def test_unreachable_unique_count(self):
        actual = occupancy_tail([.5, .5], maximum=12, target=3)
        np.testing.assert_allclose(actual, np.zeros(13), atol=1e-12)


if __name__ == "__main__":
    unittest.main()
