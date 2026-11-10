"""Offline collector scheduling regressions for historical stale reports.

Exercise the production _days method in isolation so tests do not require a
running Home Assistant installation or touch the Recorder database.
"""

import ast
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "schluter_heat_extended"
    / "energy_statistics.py"
)


def load_days():
    """Compile just the unmodified production method with deterministic stubs."""
    module = ast.parse(SOURCE.read_text())
    cls = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "EnergyStatistics"
    )
    method = next(
        node for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "_days"
    )
    scope = {
        "datetime": datetime,
        "UTC": UTC,
        "date": date,
        "timedelta": timedelta,
        "POLL_INTERVAL": timedelta(minutes=15),
        "AUDIT_INTERVAL": timedelta(days=7),
        "STALE_RECHECK_INTERVAL": timedelta(hours=6),
        "hour_floor": lambda value: value.astimezone(UTC).replace(
            minute=0, second=0, microsecond=0
        ),
        "regular_day": lambda _day, _zone: True,
        "instant": datetime.fromisoformat,
    }
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), scope)
    return scope["_days"]


class HistoricalStaleAuditTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 20, tzinfo=UTC)
        self.zone = ZoneInfo("America/New_York")
        self.anchor = datetime(2026, 11, 5, 5, tzinfo=UTC)
        self.checks = {
            date(2026, 11, day).isoformat(): (
                self.now - timedelta(days=1)
            ).isoformat()
            for day in range(5, 9)
        }
        self.checks["2026-11-07"] = (
            self.now - timedelta(hours=1)
        ).isoformat()
        self.checks["2026-11-08"] = self.now.isoformat()
        self.missing_days = set()

    def run_days(self, issues):
        ledger = SimpleNamespace(
            anchor=self.anchor,
            checks=self.checks,
            issues=dict(issues),
            missing_on=lambda day, _now: day in self.missing_days,
        )
        worker = SimpleNamespace(zone=self.zone, ledger=ledger)
        return load_days()(worker, self.now)

    def test_stale_complete_day_is_retried_before_weekly_audit(self):
        days = self.run_days({"2026-11-05": "stale"})
        self.assertIn((date(2026, 11, 5), True), days)

    def test_healthy_day_stays_on_weekly_audit(self):
        days = self.run_days({})
        self.assertNotIn((date(2026, 11, 5), True), days)

    def test_recently_checked_stale_day_waits_for_recheck_interval(self):
        days = self.run_days({"2026-11-07": "stale"})
        self.assertNotIn((date(2026, 11, 7), True), days)

    def test_missing_hours_take_priority_over_stale_retry(self):
        self.missing_days = {date(2026, 11, 6)}
        days = self.run_days({})
        self.assertIn((date(2026, 11, 6), True), days)


if __name__ == "__main__":
    unittest.main()
