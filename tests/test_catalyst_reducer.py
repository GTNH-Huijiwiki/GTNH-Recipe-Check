import unittest

from Core.catalyst_reducer import reduce_catalyst_groups


def group(*keys, circuit="c"):
    return {
        "catalysts": [{"key": key} for key in keys],
        "circuit": {"key": circuit},
    }


def recipe(*groups):
    return {"inputItems": [], "catalystGroups": list(groups)}


def selected_keys(metadata):
    return [
        [entry["key"] for entry in item["group"]["catalysts"]]
        for item in metadata["selected_groups"]
    ]


class CatalystReducerTests(unittest.TestCase):
    def test_multi_catalyst_group_covers_its_subsets(self):
        recipes = [
            recipe(group("a", "b")),
            recipe(group("a")),
            recipe(group("b")),
        ]

        reduced, metadata = reduce_catalyst_groups("machine", recipes)

        self.assertEqual(selected_keys(metadata), [["a", "b"]])
        self.assertTrue(all(item["catalystGroups"] for item in reduced))
        self.assertTrue(metadata["set_cover"]["optimal"])

    def test_exact_cover_beats_greedy(self):
        recipes = [
            recipe(group("a"), group("b")),
            recipe(group("a"), group("b")),
            recipe(group("a"), group("c")),
            recipe(group("a"), group("c")),
            recipe(group("b")),
            recipe(group("c")),
        ]

        _, metadata = reduce_catalyst_groups("machine", recipes)

        self.assertEqual(selected_keys(metadata), [["b"], ["c"]])
        self.assertEqual(metadata["set_cover"]["selected_count"], 2)

    def test_missing_catalyst_groups_stay_zero_groups(self):
        reduced, _ = reduce_catalyst_groups(
            "machine",
            [{"inputItems": [], "catalystGroups": []}],
        )
        self.assertEqual(reduced[0]["catalystGroups"], [])

    def test_equivalent_catalyst_choices_become_one_abstract_group(self):
        recipes = [
            {
                "inputItems": [],
                "catalystGroups": [
                    {
                        "catalysts": [],
                        "catalystChoices": [
                            {"members": [{"key": "a"}, {"key": "b"}]}
                        ],
                        "circuit": None,
                    }
                ],
            }
        ]

        _, metadata = reduce_catalyst_groups("machine", recipes)

        self.assertEqual(len(metadata["selected_groups"]), 1)
        selected = metadata["selected_groups"][0]["group"]
        self.assertTrue(selected["catalysts"][0]["key"].startswith("__abstract_"))


if __name__ == "__main__":
    unittest.main()
