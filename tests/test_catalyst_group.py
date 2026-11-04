import unittest

from Core.catalyst_group import (
    effective_analysis_groups,
    normalize_catalyst_groups,
    normalize_recipe_catalyst_groups,
)


def item(key):
    return {"key": key, "amount": 1}


class CatalystGroupSchemaTests(unittest.TestCase):
    def test_recipe_may_have_zero_groups(self):
        recipe = {"catalystGroups": []}
        normalized = normalize_recipe_catalyst_groups(recipe)
        self.assertEqual(normalized["catalystGroups"], [])
        self.assertEqual(len(effective_analysis_groups(normalized)), 1)
        self.assertEqual(
            effective_analysis_groups(normalized),
            [{"catalysts": [], "circuit": None}],
        )

    def test_empty_group_is_not_stored(self):
        self.assertEqual(
            normalize_catalyst_groups(
                [{"catalysts": [], "circuit": None}]
            ),
            [],
        )

    def test_multiple_concrete_groups_are_preserved(self):
        groups = [
            {
                "catalysts": [item("a"), item("b")],
                "circuit": item("circuit_1"),
            },
            {
                "catalysts": [item("c")],
                "circuit": None,
            },
        ]
        normalized = normalize_catalyst_groups(groups)
        self.assertEqual(len(normalized), 2)
        self.assertEqual(
            [entry["key"] for entry in normalized[0]["catalysts"]],
            ["a", "b"],
        )
        self.assertEqual(normalized[1]["circuit"], None)

    def test_choices_expand_to_cartesian_concrete_groups(self):
        groups = [
            {
                "catalysts": [item("fixed")],
                "circuit": item("circuit"),
                "catalystChoices": [
                    {"members": [item("a"), item("b")]},
                    {"members": [item("c")]},
                ],
            }
        ]
        normalized = normalize_catalyst_groups(groups)
        self.assertEqual(
            [[entry["key"] for entry in group["catalysts"]] for group in normalized],
            [["a", "c", "fixed"], ["b", "c", "fixed"]],
        )
        for group in normalized:
            self.assertEqual(set(group), {"catalysts", "circuit"})


if __name__ == "__main__":
    unittest.main()
