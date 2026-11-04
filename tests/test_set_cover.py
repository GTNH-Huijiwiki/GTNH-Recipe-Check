import itertools
import random
import unittest

from Core.set_cover import minimum_set_cover


class SetCoverTests(unittest.TestCase):
    def test_exact_solver_matches_bruteforce_on_small_random_cases(self):
        rng = random.Random(20261104)
        for case in range(80):
            universe = set(range(1, rng.randint(2, 7) + 1))
            candidates = []
            for index in range(rng.randint(1, 9)):
                coverage = {
                    element for element in universe if rng.random() < 0.42
                }
                if coverage:
                    candidates.append({"key": index, "coverage": coverage})
            for element in universe:
                candidates.append(
                    {"key": f"singleton-{element}", "coverage": {element}}
                )

            selected, stats = minimum_set_cover(universe, candidates)

            brute_force_size = len(candidates)
            for size in range(1, len(candidates) + 1):
                for combination in itertools.combinations(candidates, size):
                    covered = set().union(
                        *(candidate["coverage"] for candidate in combination)
                    )
                    if covered >= universe:
                        brute_force_size = size
                        break
                if brute_force_size == size:
                    break

            with self.subTest(case=case):
                self.assertEqual(len(selected), brute_force_size)
                self.assertTrue(stats["optimal"])

    def test_selected_cover_actually_covers_universe(self):
        universe = {1, 2, 3, 4, 5, 6}
        candidates = [
            {"key": "a", "coverage": {1, 2, 3, 4}},
            {"key": "b", "coverage": {1, 2, 5}},
            {"key": "c", "coverage": {3, 4, 6}},
        ]
        selected, _ = minimum_set_cover(universe, candidates)
        covered = set().union(
            *(
                candidate["coverage"]
                for candidate in candidates
                if candidate["key"] in selected
            )
        )
        self.assertGreaterEqual(covered, universe)
        self.assertEqual(len(selected), 2)


if __name__ == "__main__":
    unittest.main()
