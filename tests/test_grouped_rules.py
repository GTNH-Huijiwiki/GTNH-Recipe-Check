import unittest

from Rules import assembler, fluidchemicaleactor, laserengraver


def item(key, name=None):
    return {"key": key, "originalName": name or key, "amount": 1}


def base_recipe():
    return {
        "inputItems": [],
        "inputFluids": [],
        "outputItems": [],
        "outputFluids": [],
        "catalystGroups": [],
        "inputGroups": [],
    }


class GroupedRuleTests(unittest.TestCase):
    def test_laser_removes_floppy_or_eeprom_only_input(self):
        for name in ("Floppy Disk", "EEPROM"):
            with self.subTest(name=name):
                recipe = base_recipe()
                recipe["inputItems"] = [item(name.lower(), name)]
                self.assertEqual(laserengraver.clean([recipe]), [])

    def test_laser_removes_unaligned_quark_catalyst_input(self):
        recipe = base_recipe()
        recipe["inputItems"] = [
            item("quark", "Unaligned Quark Releasing Catalyst")
        ]
        recipe["inputFluids"] = [item("baryonic", "Stabilised Baryonic Matter")]
        self.assertEqual(laserengraver.clean([recipe]), [])

    def test_laser_prunes_removed_catalyst_choice(self):
        recipe = base_recipe()
        recipe["catalystGroups"] = [
            {
                "catalysts": [],
                "catalystChoices": [
                    {
                        "members": [
                            item("bad", "Reinforced Glass Lens"),
                            item("good", "Sapphire Lens"),
                        ]
                    }
                ],
                "circuit": None,
            }
        ]

        filtered = laserengraver.clean([recipe])

        self.assertEqual(len(filtered), 1)
        group = filtered[0]["catalystGroups"][0]
        self.assertEqual([entry["key"] for entry in group["catalysts"]], ["good"])
        self.assertNotIn("catalystChoices", group)

    def test_fluid_reactor_clears_grouped_catalysts(self):
        recipe = base_recipe()
        recipe["catalystGroups"] = [
            {
                "catalysts": [item("fixed")],
                "catalystChoices": [{"members": [item("choice")]}],
                "circuit": item("circuit"),
            }
        ]

        filtered = fluidchemicaleactor.clean([recipe])

        group = filtered[0]["catalystGroups"][0]
        self.assertEqual(group["catalysts"], [])
        self.assertNotIn("catalystChoices", group)
        self.assertEqual(group["circuit"]["key"], "circuit")

    def test_assembler_removes_original_name_trophy_with_any_circuit(self):
        recipe = base_recipe()
        recipe["catalystGroups"] = [
            {
                "catalysts": [item("custom:special:1", "Mystery Trophy")],
                "circuit": item(
                    "gregtech:gt.integrated_circuit:42",
                    "Programmed Circuit 42",
                ),
            }
        ]
        self.assertEqual(assembler.clean([recipe]), [])

    def test_assembler_does_not_match_trophy_only_in_key(self):
        recipe = base_recipe()
        recipe["catalystGroups"] = [
            {
                "catalysts": [item("custom:item.trophy:99", "Mystery Token")],
                "circuit": item(
                    "gregtech:gt.integrated_circuit:42",
                    "Programmed Circuit 42",
                ),
            }
        ]
        self.assertEqual(len(assembler.clean([recipe])), 1)

    def test_assembler_prunes_only_volumetric_variant(self):
        recipe = base_recipe()
        recipe["inputGroups"] = [
            {
                "kind": "item",
                "members": [
                    item("vf", "Volumetric Flask"),
                    item("alt", "Alternative"),
                ],
            }
        ]

        filtered = assembler.clean([recipe])

        self.assertEqual(len(filtered), 1)
        self.assertEqual(
            [entry["key"] for entry in filtered[0]["inputItems"]], ["alt"]
        )
        self.assertEqual(filtered[0]["inputGroups"], [])


if __name__ == "__main__":
    unittest.main()
