import unittest

from Core.catalyst_relations import (
    RELATION_SCHEMA,
    classify_relation,
    containment_markdown,
    machine_relation_report,
    pure_containment_chains,
)


class CatalystRelationTests(unittest.TestCase):
    def test_all_relation_types(self):
        cases = [
            ({1, 2}, {1, 2}, "equivalent"),
            ({1}, {1, 2}, "strict_subset"),
            ({1, 2}, {1}, "strict_superset"),
            ({1, 2}, {2, 3}, "intersect"),
            ({1}, {2, 3}, "disjoint"),
        ]
        for left, right, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(classify_relation(left, right), expected)

    def test_relation_schema_covers_classifier_outputs(self):
        outputs = {
            "equivalent",
            "strict_subset",
            "strict_superset",
            "intersect",
            "disjoint",
        }
        self.assertEqual(set(RELATION_SCHEMA), outputs)

    def test_pure_containment_chain_ignores_intersections(self):
        metadata = {
            "equivalence_classes": [
                {
                    "id": "ec0001",
                    "top_level": True,
                    "coverage_size": 10,
                    "member_labels": ["root"],
                },
                {
                    "id": "ec0002",
                    "top_level": False,
                    "coverage_size": 5,
                    "member_labels": ["middle"],
                },
                {
                    "id": "ec0003",
                    "top_level": False,
                    "coverage_size": 2,
                    "member_labels": ["bottom"],
                },
                {
                    "id": "ec0004",
                    "top_level": False,
                    "coverage_size": 3,
                    "member_labels": ["other"],
                },
            ],
            "relations": [
                {
                    "left": "ec0002",
                    "right": "ec0001",
                    "relation": "strict_subset",
                },
                {
                    "left": "ec0003",
                    "right": "ec0002",
                    "relation": "strict_subset",
                },
                {
                    "left": "ec0003",
                    "right": "ec0004",
                    "relation": "intersect",
                },
            ],
        }

        chains = pure_containment_chains(metadata)

        self.assertEqual(len(chains), 1)
        self.assertEqual(
            chains[0]["chain"], ["ec0001", "ec0002", "ec0003"]
        )

        report = machine_relation_report("machine", metadata)
        markdown = containment_markdown([report])
        self.assertIn("ec0001 ⊃ ec0002 ⊃ ec0003", markdown)

    def test_machine_relation_report_preserves_schema(self):
        metadata = {
            "group_nodes": [{"id": "g0001"}],
            "equivalence_classes": [{"id": "ec0001"}],
            "relations": [{"relation": "intersect"}],
        }
        report = machine_relation_report("machine", metadata)
        self.assertEqual(report["relation_schema"], RELATION_SCHEMA)
        self.assertEqual(report["groups"], metadata["group_nodes"])
        self.assertEqual(report["relations"], metadata["relations"])


if __name__ == "__main__":
    unittest.main()
