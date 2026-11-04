import itertools
import random
import unittest

from Core.recipe_checker import (
    _recipe_input_slots,
    _slots_can_match,
    build_conflict_graph,
    recipe_can_trigger,
)


def item(key, amount=1):
    return {"key": key, "amount": amount}


def catalyst_group(key):
    return {
        "catalysts": [{"key": key, "amount": 1}],
        "circuit": None,
    }


def recipe(inputs=(), groups=(), output="out", catalyst="cat", orphan=False):
    return {
        "inputItems": [item(key) for key in inputs],
        "inputFluids": [],
        "inputGroups": [
            {"kind": "item", "members": [item(key) for key in members]}
            for members in groups
        ],
        "outputItems": [item(output)],
        "outputFluids": [],
        "catalystGroups": [catalyst_group(catalyst)] if catalyst else [],
        "orphan": {"hasOrphanInput": orphan, "onlyOrphanInputs": orphan},
    }


def concrete_input_variants(recipe_value):
    """Expand each slot to one selected key, preserving slot multiplicity."""

    choices = [tuple(slot) for slot in _recipe_input_slots(recipe_value)]
    return list(itertools.product(*choices) if choices else [()])


def variants_can_match(source_variant, target_variant):
    """Brute-force injective target-slot to source-slot matching."""

    if len(target_variant) > len(source_variant):
        return False
    for source_indexes in itertools.permutations(
        range(len(source_variant)), len(target_variant)
    ):
        if all(
            source_variant[source_index] == target_slot
            for source_index, target_slot in zip(
                source_indexes, target_variant
            )
        ):
            return True
    return False


class ConflictGraphTests(unittest.TestCase):
    def test_fixed_input_match_matches_key_subset_semantics(self):
        left = recipe(inputs=("a", "b"))
        right = recipe(inputs=("a",))
        self.assertTrue(recipe_can_trigger(left, right))
        self.assertFalse(recipe_can_trigger(right, left))

    def test_group_slot_member_can_satisfy_fixed_input(self):
        left = recipe(groups=(("a", "b"),))
        right = recipe(inputs=("b",))
        self.assertTrue(recipe_can_trigger(left, right))

    def test_distinct_target_slots_cannot_share_one_source_group(self):
        source = recipe(groups=(("x", "z"),))
        target = recipe(groups=(("x",), ("z",)))
        self.assertFalse(
            _slots_can_match(
                _recipe_input_slots(source), _recipe_input_slots(target)
            )
        )

    def test_slot_matching_matches_explicit_variant_expansion(self):
        rng = random.Random(20261104)
        keys = tuple("abcdef")
        for case in range(120):
            def random_recipe():
                fixed = rng.sample(keys, rng.randint(0, 2))
                groups = []
                for _ in range(rng.randint(0, 3)):
                    members = rng.sample(keys, rng.randint(1, 3))
                    groups.append(tuple(members))
                return recipe(inputs=fixed, groups=groups)

            source = random_recipe()
            target = random_recipe()
            expected = any(
                variants_can_match(source_variant, target_variant)
                for target_variant in concrete_input_variants(target)
                for source_variant in concrete_input_variants(source)
            )
            with self.subTest(case=case):
                self.assertEqual(
                    recipe_can_trigger(source, target), expected
                )

    def test_grouped_recipe_creates_group_edge(self):
        left = recipe(
            groups=(("x", "y"),),
            output="left",
            catalyst="cat_left",
        )
        right = recipe(inputs=("x",), output="right", catalyst="cat_right")

        graph = build_conflict_graph("machine", [left, right])

        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["adjacency_matrix"][1][0], 1)

    def test_same_output_exemption_removes_edge(self):
        left = recipe(inputs=("x", "y"), output="same", catalyst="cat_left")
        right = recipe(inputs=("x",), output="same", catalyst="cat_right")

        graph = build_conflict_graph("machine", [left, right])

        self.assertEqual(graph["edges"], [])

    def test_random_graph_matches_pairwise_oracle(self):
        rng = random.Random(424242)
        keys = tuple("abcdef")
        recipes = []
        for index in range(24):
            fixed = rng.sample(keys, rng.randint(0, 2))
            groups = [
                tuple(rng.sample(keys, rng.randint(1, 3)))
                for _ in range(rng.randint(0, 2))
            ]
            recipes.append(
                recipe(
                    inputs=fixed,
                    groups=groups,
                    output=f"out_{index}",
                    catalyst=f"cat_{index}",
                )
            )

        graph = build_conflict_graph("machine", recipes)
        actual = {
            frozenset((edge["source"], edge["target"]))
            for edge in graph["edges"]
        }
        expected = set()
        for left_index in range(len(recipes)):
            for right_index in range(left_index + 1, len(recipes)):
                left = recipes[left_index]
                right = recipes[right_index]
                if recipe_can_trigger(left, right) or recipe_can_trigger(
                    right, left
                ):
                    expected.add(
                        frozenset(
                            (
                                f"group_{left_index:04d}",
                                f"group_{right_index:04d}",
                            )
                        )
                    )
        self.assertEqual(actual, expected)

    def test_empty_group_can_be_triggered_by_other_recipe(self):
        empty_recipe = recipe(inputs=("x",), output="empty", catalyst=None)
        empty_recipe["catalystGroups"] = [
            {"catalysts": [], "circuit": None}
        ]
        other_recipe = recipe(
            inputs=("x", "y"), output="other", catalyst="cat_other"
        )
        empty_report = {}

        graph = build_conflict_graph(
            "machine",
            [empty_recipe, other_recipe],
            empty_conflict_report=empty_report,
        )

        self.assertEqual(
            graph["empty_group_conflict_counts"][0]["pair_count"], 1
        )
        self.assertEqual(empty_report["pair_count"], 1)

    def test_orphan_empty_group_is_not_triggered_by_other(self):
        empty_recipe = recipe(
            inputs=("x", "unique"), output="empty", catalyst=None, orphan=True
        )
        empty_recipe["catalystGroups"] = [
            {"catalysts": [], "circuit": None}
        ]
        other_recipe = recipe(
            inputs=("x",), output="other", catalyst="cat_other"
        )

        graph = build_conflict_graph(
            "machine", [empty_recipe, other_recipe]
        )

        self.assertEqual(graph["empty_group_conflict_counts"], [])

    def test_two_implicit_circuit_zero_recipes_can_self_conflict(self):
        superset = recipe(
            inputs=("wood_plank", "gunpowder", "string"),
            output="powderbarrel",
            catalyst=None,
        )
        subset = recipe(
            inputs=("gunpowder", "string"),
            output="fuse",
            catalyst=None,
        )

        graph = build_conflict_graph("machine", [superset, subset])

        self.assertEqual(len(graph["nodes"]), 1)
        self.assertEqual(graph["build_stats"]["self_conflict_count"], 1)
        self.assertEqual(graph["edges"], [])

    def test_orphan_target_is_pruned(self):
        source = recipe(inputs=("a",), orphan=False, catalyst="cat_source")
        target = recipe(inputs=("a", "orphan"), orphan=True, catalyst="cat_target")

        graph = build_conflict_graph("machine", [source, target])

        self.assertEqual(graph["build_stats"]["orphan_targets_pruned"], 1)
        self.assertEqual(graph["build_stats"]["valid_target_count"], 1)


if __name__ == "__main__":
    unittest.main()
