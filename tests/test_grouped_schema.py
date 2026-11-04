import unittest

from Core.grouped_schema import (
    expand_catalyst_choices,
    remove_matching_input_items,
    remove_variants_where_all_inputs_match,
)


def item(key, name=None):
    return {"key": key, "originalName": name or key, "amount": 1}


class GroupedSchemaTests(unittest.TestCase):
    def test_catalyst_choices_expand_whole_cartesian_group_without_mutating(self):
        recipe = {
            "catalystGroups": [
                {
                    "catalysts": [item("fixed")],
                    "catalystChoices": [
                        {"members": [item("a"), item("b")]},
                        {"members": [item("c")]},
                    ],
                    "circuit": {"key": "circuit"},
                }
            ]
        }

        expanded = expand_catalyst_choices(recipe)
        keys = [
            [entry["key"] for entry in group["catalysts"]]
            for group in expanded["catalystGroups"]
        ]

        self.assertEqual(keys, [["a", "c", "fixed"], ["b", "c", "fixed"]])
        self.assertIn("catalystChoices", recipe["catalystGroups"][0])
        for group in expanded["catalystGroups"]:
            self.assertEqual(set(group), {"catalysts", "circuit"})

    def test_contains_rule_prunes_one_group_member(self):
        recipe = {
            "inputItems": [item("keep")],
            "inputGroups": [
                {"kind": "item", "members": [item("bad"), item("alt")]}
            ],
            "inputFluids": [],
        }

        filtered = remove_matching_input_items(
            recipe, lambda entry: entry["key"] == "bad"
        )

        self.assertEqual(
            [entry["key"] for entry in filtered["inputItems"]],
            ["keep", "alt"],
        )
        self.assertEqual(filtered["inputGroups"], [])

    def test_fixed_matching_input_deletes_whole_recipe(self):
        recipe = {
            "inputItems": [item("bad")],
            "inputGroups": [],
            "inputFluids": [],
        }

        filtered = remove_matching_input_items(
            recipe, lambda entry: entry["key"] == "bad"
        )

        self.assertIsNone(filtered)

    def test_all_match_rule_keeps_non_matching_variant(self):
        recipe = {
            "inputItems": [],
            "inputGroups": [
                {"kind": "item", "members": [item("vf"), item("alt")]}
            ],
            "inputFluids": [],
        }

        filtered = remove_variants_where_all_inputs_match(
            recipe, lambda entry: entry["key"] == "vf"
        )

        self.assertEqual([entry["key"] for entry in filtered["inputItems"]], ["alt"])
        self.assertEqual(filtered["inputGroups"], [])


if __name__ == "__main__":
    unittest.main()
