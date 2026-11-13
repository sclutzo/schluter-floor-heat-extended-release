"""Offline checks for the standalone compact Lovelace layout."""

from pathlib import Path
import unittest
import yaml


ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "dashboard" / "cards"


class DashboardSizingTest(unittest.TestCase):
    def test_resizable_grid_wraps_thermostat_without_changing_entities(self):
        for filename, expected_count in (
            ("bathroom-heat-compact.yaml", 1),
        ):
            with self.subTest(filename=filename):
                data = yaml.safe_load((CARDS / filename).read_text())
                self.assertEqual(data["type"], "grid")
                self.assertEqual(data["columns"], 2)
                self.assertIs(data["square"], False)
                self.assertEqual(len(data["cards"]), expected_count)
                thermostat = data["cards"][0]
                self.assertEqual(thermostat["type"], "picture-elements")
                self.assertEqual(thermostat["image"], "/local/Thermostat.svg")
                elements = thermostat["elements"]
                scalers = [e for e in elements
                           if e["type"] == "custom:bathroom-heat-scaler"]
                self.assertEqual(len(scalers), 1)
                self.assertEqual(scalers[0]["font_scale"], 1.0)
                self.assertEqual(scalers[0]["style"]["pointer-events"], "none")

                controls = [
                    item for item in elements
                    if item["type"].startswith("custom:")
                    and item["type"] != "custom:bathroom-heat-scaler"
                ]
                self.assertTrue(controls)
                self.assertTrue(all(
                    item["entity"] == "climate.bath_heat"
                    and item["service_domain"] == "schluter_heat_extended"
                    for item in controls
                ))
                by_field = {item["field"]: item for item in controls
                            if item["type"] == "custom:bathroom-heat-status"}
                for name in ("clock", "current", "expiration"):
                    self.assertIs(by_field[name]["compact"], True)
                    self.assertIn("var(--bath-heat-", by_field[name]["style"]["font-size"])
                target = next(e for e in elements
                              if e["type"] == "custom:bathroom-heat-target")
                self.assertIn("var(--bath-heat-target", target["style"]["font-size"])
                arrow = [e for e in elements
                         if e["type"] == "icon"
                         and e["icon"] in ("mdi:arrow-up-bold", "mdi:arrow-down-bold")]
                self.assertEqual(len(arrow), 2)
                self.assertTrue(all(
                    "var(--bath-heat-arrow-width" in e["style"]["width"]
                    and "var(--bath-heat-arrow-height" in e["style"]["height"]
                    for e in arrow
                ))
                arrow_up = next(e for e in arrow if e["icon"] == "mdi:arrow-up-bold")
                self.assertEqual(arrow_up["style"]["top"], "39%")
                schedule_button = next(
                    e for e in elements
                    if e["type"] == "image"
                    and e.get("image") == "/local/EditSchedule.svg"
                )
                adjust_button = next(
                    e for e in elements
                    if e["type"] == "image"
                    and e.get("image") == "/local/Adjust.svg"
                )
                self.assertEqual(schedule_button["style"]["width"],
                                 adjust_button["style"]["width"])
                self.assertEqual(schedule_button["style"]["width"], "50%")
                self.assertEqual(schedule_button["style"]["top"], "85%")
                self.assertEqual(
                    schedule_button["tap_action"]["bathroom_heat"],
                    {"entity": "climate.bath_heat", "action": "schedule"}
                )
                self.assertEqual(schedule_button["tap_action"]["action"],
                                 "fire-dom-event")
                # Only the original full-size card retains a native button.
                self.assertFalse(any(
                    e["type"] == "custom:bathroom-heat-schedule-button"
                    for e in elements
                ))

                self.assertFalse((CARDS / "bathroom-heat-compact-with-energy.yaml").exists())


    def test_full_size_card_keeps_original_fixed_sizes(self):
        data = yaml.safe_load((CARDS / "bathroom-heat-extended.yaml").read_text())
        self.assertEqual(data["type"], "picture-elements")
        self.assertFalse(any(
            e["type"] == "custom:bathroom-heat-scaler"
            for e in data["elements"]
        ))
        full_size_button = next(
            e for e in data["elements"]
            if e["type"] == "custom:bathroom-heat-schedule-button"
        )
        self.assertNotIn("compact", full_size_button)
        self.assertEqual(full_size_button["style"]["width"], "50%")

if __name__ == "__main__":
    unittest.main()
