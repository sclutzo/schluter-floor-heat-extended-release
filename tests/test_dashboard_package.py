"""Source/layout contracts for optional Lovelace dashboard packaging."""

from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
WWW = DASH / "www"


class DashboardPackageTest(unittest.TestCase):
    def test_thermostat_card_routing_and_files(self):
        cfg = yaml.safe_load((DASH / "cards/bathroom-heat-extended.yaml").read_text())
        self.assertEqual(cfg["type"], "picture-elements")
        self.assertEqual(cfg["image"], "/local/Thermostat.svg")
        controls = [x for x in cfg["elements"] if x["type"].startswith("custom:")]
        self.assertGreaterEqual(len(controls), 5)
        for item in controls:
            self.assertEqual(item["entity"], "climate.bath_heat")
            self.assertEqual(item["service_domain"], "schluter_heat_extended")
        for item in cfg["elements"]:
            if item["type"] == "conditional":
                for child in item["elements"]:
                    self.assertEqual(
                        child.get("tap_action", {}).get("bathroom_heat", {}).get("entity"),
                        "climate.bath_heat"
                    )


    def test_svg_assets_exist_and_are_usable_by_picture_elements(self):
        import xml.etree.ElementTree as ET
        card = yaml.safe_load((DASH / "cards/bathroom-heat-extended.yaml").read_text())
        images = [card["image"]]
        images += [
            item["image"] for item in card["elements"] if item.get("type") == "image"
        ]
        for item in card["elements"]:
            if item.get("type") == "conditional":
                images += [
                    child["image"] for child in item["elements"]
                    if child.get("type") == "image"
                ]
        self.assertEqual(set(images), {
            "/local/Thermostat.svg", "/local/Adjust.svg", "/local/ReturnToSchedule.svg"
        })
        for image in images:
            asset = WWW / image.split("/")[-1]
            self.assertTrue(asset.exists())
            root = ET.fromstring(asset.read_text())
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertIn("viewBox", root.attrib)

    def test_compact_schedule_svg_matches_adjust_exact_geometry(self):
        import xml.etree.ElementTree as ET

        adjust = ET.fromstring((WWW / "Adjust.svg").read_text())
        schedule = ET.fromstring((WWW / "EditSchedule.svg").read_text())
        self.assertEqual(schedule.attrib["viewBox"], adjust.attrib["viewBox"])
        self.assertEqual(schedule.attrib["width"], adjust.attrib["width"])
        self.assertEqual(schedule.attrib["height"], adjust.attrib["height"])
        self.assertIn("Edit Schedule", (WWW / "EditSchedule.svg").read_text())

    def test_read_only_energy_card(self):
        cfg = yaml.safe_load((DASH / "cards/bathroom-heat-energy-extended.yaml").read_text())
        self.assertEqual(cfg["type"], "custom:bathroom-heat-energy")
        self.assertEqual(cfg["entity"], "climate.bath_heat")
        self.assertEqual(cfg["service_domain"], "schluter_heat_extended")

    def test_controller_domain_is_entity_specific_and_default_remains_original(self):
        src = (WWW / "bathroom-heat-controls.js").read_text()
        self.assertIn('const VERSION = "1.0.23"', src)
        self.assertIn('const domain = config.service_domain ?? "schluter"', src)
        self.assertIn("new DraftController(entity, this.config.service_domain)", src)
        self.assertIn("this.hass.callService(this.serviceDomain", src)
        self.assertIn("domain: this.config.service_domain", src)
        self.assertIn('content: {type: "custom:bathroom-heat-schedule", entity,', src)
        self.assertIn("service_domain: serviceDomain", src)
        self.assertIn("openWeeklySchedule(this.config.entity, this.config.service_domain, this)", src)
        self.assertIn('else if (data.action === "schedule") openWeeklySchedule(', src)
        self.assertNotIn('config.entity !== "climate.bath_heat"', src)
        self.assertNotIn('domain: "schluter"', src)

    def test_energy_service_is_entity_specific(self):
        src = (WWW / "bathroom-heat-energy.js").read_text()
        self.assertIn('const VERSION = "1.0.5"', src)
        self.assertIn('const domain = config.service_domain || "schluter"', src)
        self.assertIn('domain: this.config.service_domain', src)
        self.assertNotIn('domain: "schluter"', src)

    def test_dashboard_preview_images_and_separate_energy_recommendation(self):
        doc = (DASH / "README.md").read_text()
        for name in ("thermostat-full-width.webp", "thermostat-compact.webp"):
            path = DASH / "screenshots" / name
            image = path.read_bytes()
            self.assertGreater(len(image), 1000, name)
            self.assertEqual(image[:4], b"RIFF", name)
            self.assertEqual(image[8:12], b"WEBP", name)
            self.assertIn(f"screenshots/{name}", doc)

        self.assertFalse((DASH / "cards/bathroom-heat-compact-with-energy.yaml").exists())
        self.assertNotIn("cards/bathroom-heat-compact-with-energy.yaml", doc)
        self.assertIn("separate card", doc.lower())

    def test_documentation_includes_requirements_and_backup(self):
        doc = (DASH / "README.md").read_text()
        for phrase in ("Browser Mod", "Resources", "climate.bath_heat",
                       "service_domain: schluter_heat_extended", "rollback"):
            self.assertIn(phrase.lower(), doc.lower())


if __name__ == "__main__":
    unittest.main()
