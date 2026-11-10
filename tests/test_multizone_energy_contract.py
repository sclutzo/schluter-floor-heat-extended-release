"""Offline regression checks for guarded Schluter multi-zone energy migration.

These checks cover source contracts and pure time/ledger policy only. They do
NOT replace a Home Assistant runtime/Recorder migration test.
"""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMP = ROOT / "custom_components" / "schluter_heat_extended"
spec = importlib.util.spec_from_file_location(
    "timezone_rules_multizone_under_test", COMP / "timezone_rules.py"
)
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


class MultiZoneEnergyContractTest(unittest.TestCase):
    def test_device_zones_produce_distinct_utc_hours(self):
        zones = {"100": "America/New_York", "200": "America/Los_Angeles",
                 "300": "America/Phoenix"}
        day = date(2026, 11, 4)
        stamps = [
            rules.safe_regular_hour_starts(
                day, rules.configured_device_zone(zones, serial)
            )[0].isoformat()
            for serial in ("100", "200", "300")
        ]
        self.assertEqual(stamps, [
            "2026-11-04T05:00:00+00:00",
            "2026-11-04T08:00:00+00:00",
            "2026-11-04T07:00:00+00:00",
        ])

    def test_dst_boundaries_and_invalid_zone_fail_closed(self):
        zones = {"100": "America/New_York"}
        selected = rules.configured_device_zone(zones, "100")
        for change_day in (date(2026, 3, 8), date(2026, 11, 1)):
            with self.assertRaises(rules.TimezoneUnverified):
                rules.safe_regular_hour_starts(change_day, selected)
        with self.assertRaises(rules.TimezoneUnverified):
            rules.configured_device_zone(zones, "200")
        with self.assertRaises(rules.TimezoneUnverified):
            rules.check_thermostat_offset(
                selected, "-0400", datetime(2026, 11, 8, 12, tzinfo=UTC)
            )
        rules.check_thermostat_offset(
            selected, "-0500", datetime(2026, 11, 8, 12, tzinfo=UTC)
        )

    def test_legacy_history_keeps_original_timezone_and_utc_timestamps(self):
        saved = {
            "serial": "100", "statistic_id": "schluter_heat_extended:energy_100",
            "anchor": "2026-10-31T04:00:00+00:00",
            "hours": {"2026-11-02T05:00:00+00:00": 0.34},
            "checks": {}, "issues": {"2026-11-01": "dst_unmapped"},
        }
        before = repr(saved)
        self.assertEqual(
            rules.verify_ledger_zone(saved, "America/New_York"),
            "legacy_new_york",
        )
        self.assertEqual(repr(saved), before)
        with self.assertRaises(rules.TimezoneUnverified):
            rules.verify_ledger_zone(saved, "America/Chicago")
        self.assertEqual(repr(saved), before)

    def test_collector_persists_zone_before_any_publication(self):
        code = (COMP / "energy_statistics.py").read_text()
        self.assertIn('"time_zone": str(self.zone)', code)
        self.assertIn('verify_ledger_zone(data, str(zone))', code)
        self.assertIn("safe_regular_hour_starts(day, self.zone)", code)
        self.assertIn('report.get("time_zone") != str(self.zone)', code)
        self.assertIn('self.zone = require_iana_zone(str(zone))', code)
        self.assertIn('check_thermostat_offset(self.zone, raw.get("TZOffset"), now)', code)
        self.assertIn("verify_offset=True", code)
        update = code.split("async def async_update(self, _now=None):", 1)[1]
        self.assertLess(update.index("self.check_current_offset"),
                        update.index("await self._initialize("))
        self.assertLess(update.index("await self._initialize("),
                        update.index("await self._publish("))

    def test_ui_options_do_not_default_to_legacy_zone(self):
        code = (COMP / "energy_statistics.py").read_text()
        setup = code.split("async def async_setup_energy_statistics_entry(", 1)[1]
        self.assertIn("configured_device_zone(time_zones, serial)", setup)
        self.assertIn('reason": "Choose a valid timezone', setup)
        self.assertIn("async_stop_energy_statistics_entry", setup)
        self.assertNotIn("zone=ZONE", setup)

    def test_reports_are_keyed_by_serial_but_labeled_in_selected_zone(self):
        code = (COMP / "energy.py").read_text()
        self.assertIn('key = f"{serial}|{view}|{value}"', code)
        self.assertIn('today = now.astimezone(zone).date()', code)
        self.assertIn('safe_regular_hour_starts(start, zone)', code)
        self.assertIn('"time_zone": str(zone)', code)
        self.assertIn('zone=None', code)
        climate = (COMP / "climate.py").read_text()
        self.assertIn("configured_device_zone(", climate)
        self.assertIn("check_thermostat_offset(zone,", climate)
        self.assertIn("zone=zone if", climate)

    def test_ui_runtime_stops_workers_on_unload(self):
        init = (COMP / "__init__.py").read_text()
        self.assertIn("async_setup_energy_statistics_entry(", init)
        self.assertIn("async_stop_energy_statistics_entry(hass)", init)
        unload = init.split("async def async_unload_entry(", 1)[1]
        self.assertLess(unload.index("async_stop_energy_statistics_entry"),
                        unload.index("async_unload_platforms_or_raise"))


if __name__ == "__main__":
    unittest.main()
