"""Offline tests for a timezone mapping prototype (not a live collector)."""

import importlib.util
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'custom_components' / 'schluter_heat_extended' / 'timezone_rules.py'
spec = importlib.util.spec_from_file_location('timezone_rules_under_test', SOURCE)
zone_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zone_rules)


class TimezoneRulesTest(unittest.TestCase):
    def zone(self, name):
        return zone_rules.require_iana_zone(name)

    def test_new_york_normal_winter_and_summer_days(self):
        for day, hour in [(date(2026, 1, 10), 5), (date(2026, 7, 10), 4)]:
            hours = zone_rules.safe_regular_hour_starts(day, self.zone('America/New_York'))
            self.assertEqual(len(hours), 24)
            self.assertEqual(hours[0], datetime(day.year, day.month, day.day, hour, tzinfo=UTC))
            self.assertEqual(hours[-1] - hours[0], timedelta(hours=23))

    def test_us_dst_transition_days_fail_closed(self):
        for day in [date(2026, 3, 8), date(2026, 11, 1)]:
            with self.assertRaises(zone_rules.TimezoneUnverified):
                zone_rules.safe_regular_hour_starts(day, self.zone('America/New_York'))

    def test_other_us_zones(self):
        for name, day, utc_hour in [
            ('America/Chicago', date(2026, 1, 10), 6),
            ('America/Los_Angeles', date(2026, 7, 10), 7),
            ('America/Phoenix', date(2026, 1, 10), 7),
            ('America/Phoenix', date(2026, 7, 10), 7),
        ]:
            starts = zone_rules.safe_regular_hour_starts(day, self.zone(name))
            self.assertEqual(starts[0].hour, utc_hour)

    def test_europe_berlin_and_dst(self):
        zone = self.zone('Europe/Berlin')
        self.assertEqual(zone_rules.safe_regular_hour_starts(date(2026, 1, 10), zone)[0].hour, 23)
        self.assertEqual(zone_rules.safe_regular_hour_starts(date(2026, 7, 10), zone)[0].hour, 22)
        for day in [date(2026, 3, 29), date(2026, 10, 25)]:
            with self.assertRaises(zone_rules.TimezoneUnverified):
                zone_rules.safe_regular_hour_starts(day, zone)

    def test_half_hour_and_quarter_hour_offsets_do_not_get_fabricated_utc_hours(self):
        for name in ('Australia/Adelaide', 'Asia/Kathmandu'):
            with self.assertRaises(zone_rules.TimezoneUnverified):
                zone_rules.safe_regular_hour_starts(date(2026, 7, 10), self.zone(name))

    def test_offset_check_is_a_consistency_guard_only(self):
        zone = self.zone('America/New_York')
        at = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)
        zone_rules.check_thermostat_offset(zone, '-0500', at)
        with self.assertRaises(zone_rules.TimezoneUnverified):
            zone_rules.check_thermostat_offset(zone, '-0400', at)
        with self.assertRaises(zone_rules.TimezoneUnverified):
            zone_rules.check_thermostat_offset(zone, 'Eastern', at)

    def test_reject_unknown_or_offset_only_zone(self):
        for name in (None, '', '-0500', 'America/Does_Not_Exist', ' America/New_York'):
            with self.assertRaises(zone_rules.TimezoneUnverified):
                self.zone(name)

    def test_two_devices_can_have_independent_zone_maps(self):
        day = date(2026, 11, 4)
        a = zone_rules.safe_regular_hour_starts(day, self.zone('America/New_York'))
        b = zone_rules.safe_regular_hour_starts(day, self.zone('America/Los_Angeles'))
        self.assertNotEqual(a, b)
        self.assertEqual(a[0].hour, 5)
        self.assertEqual(b[0].hour, 8)

    def test_requires_explicit_time_zone_per_thermostat(self):
        zones = {"0001": "America/New_York", "0002": "America/Chicago"}
        self.assertEqual(zone_rules.configured_device_zone(zones, "0001").key, "America/New_York")
        self.assertEqual(zone_rules.configured_device_zone(zones, "0002").key, "America/Chicago")
        for configured, serial in ((zones, "0003"), ({}, "0001"),
                                   (None, "0001"), (zones, "bad-serial")):
            with self.assertRaises(zone_rules.TimezoneUnverified):
                zone_rules.configured_device_zone(configured, serial)

    def test_ledger_zone_policy_preserves_legacy_new_york(self):
        old = {"statistic_id": "schluter_heat_extended:energy_123",
               "anchor": "2026-11-01T00:00:00+00:00",
               "hours": {"2026-11-02T05:00:00+00:00": 0.5}}
        snapshot = repr(old)
        self.assertEqual(zone_rules.verify_ledger_zone(old, "America/New_York"),
                         "legacy_new_york")
        self.assertEqual(repr(old), snapshot)
        self.assertEqual(zone_rules.verify_ledger_zone(None, "America/Phoenix"), "new")
        with self.assertRaises(zone_rules.TimezoneUnverified):
            zone_rules.verify_ledger_zone(old, "America/Los_Angeles")
        self.assertEqual(repr(old), snapshot)

    def test_zone_change_must_pause_without_rewriting_history(self):
        saved = {"time_zone": "America/Chicago", "hours": {"2026-11-02T06:00:00+00:00": 0.4}}
        self.assertEqual(zone_rules.verify_ledger_zone(saved, "America/Chicago"),
                         "unchanged")
        with self.assertRaises(zone_rules.TimezoneUnverified):
            zone_rules.verify_ledger_zone(saved, "America/New_York")
        self.assertEqual(saved["time_zone"], "America/Chicago")
        self.assertEqual(saved["hours"], {"2026-11-02T06:00:00+00:00": 0.4})
        for invalid in ([], "bad", {"time_zone": "-0500"}):
            with self.assertRaises(zone_rules.TimezoneUnverified):
                zone_rules.verify_ledger_zone(invalid, "America/New_York")

    def test_requires_calendar_date(self):
        with self.assertRaises(zone_rules.TimezoneUnverified):
            zone_rules.local_day_bounds_utc(datetime(2026, 1, 10), self.zone('UTC'))

if __name__ == '__main__':
    unittest.main()
