"""Per-thermostat forward-only energy statistics at original interval times.

This is an external statistic, not an automatically recorded sensor as well.
Only completed, unambiguously mapped hours since the persisted start are used.
No rows are created for missing data; daily/monthly reports are never expanded.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
import logging
import math

import probatio

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, UnitOfEnergy
from homeassistant.core import SupportsResponse
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .energy import ZONE, async_get_energy_report
from .timezone_rules import (
    TimezoneUnverified, check_thermostat_offset, configured_device_zone,
    require_iana_zone, safe_regular_hour_starts, verify_ledger_zone,
)

_LOGGER = logging.getLogger(__name__)
DATA_STATISTICS = "energy_statistics"
POLL_INTERVAL = timedelta(minutes=15)
AUDIT_INTERVAL = timedelta(days=7)
# Revisit stale historical days without repeatedly hammering the cloud API.
STALE_RECHECK_INTERVAL = timedelta(hours=6)
HOUR = timedelta(hours=1)


class StatisticsStateError(Exception):
    """State could not be trusted; never silently start a replacement counter."""


def instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


def stamp(value):
    return value.astimezone(UTC).isoformat()


def hour_floor(value):
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def valid_energy(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def day_bounds(day, zone=ZONE):
    start = datetime.combine(day, time.min, zone)
    end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def regular_day(day, zone=ZONE):
    start, end = day_bounds(day, zone)
    return end - start == timedelta(hours=24)


class IntervalLedger:
    """Sparse interval snapshots. Replacements are corrections, never additions."""

    def __init__(self, anchor, serial, statistic_id, zone=ZONE):
        self.anchor = anchor
        self.zone = zone
        self.serial = str(serial)
        self.statistic_id = statistic_id
        self.hours = {}
        self.checks = {}
        self.issues = {}

    @classmethod
    def restore(cls, data, serial, statistic_id, zone=ZONE):
        try:
            # Legacy 1.0.2 UTC intervals were mapped in America/New_York.
            # Never reinterpret a different selected zone as the old history.
            verify_ledger_zone(data, str(zone))
            if (
                not isinstance(data, dict)
                or data.get("statistic_id") != statistic_id
                or data.get("serial") != serial
            ):
                raise ValueError
            anchor = instant(data["anchor"])
            if hour_floor(anchor) != anchor:
                raise ValueError
            ledger = cls(anchor, serial, statistic_id, zone)
            if not all(isinstance(data.get(key), dict) for key in ("hours", "checks", "issues")):
                raise ValueError
            for key, value in data["hours"].items():
                start = instant(key)
                if start < anchor or hour_floor(start) != start or not valid_energy(value) or stamp(start) in ledger.hours:
                    raise ValueError
                ledger.hours[stamp(start)] = value
            for key, value in data["checks"].items():
                if date.fromisoformat(key).isoformat() != key:
                    raise ValueError
                ledger.checks[key] = stamp(instant(value))
            allowed = {"dst_unmapped", "unavailable", "stale", "unverified_hours", "partial", "ambiguous_zero"}
            for key, value in data["issues"].items():
                if date.fromisoformat(key).isoformat() != key or value not in allowed:
                    raise ValueError
                ledger.issues[key] = value
            return ledger
        except Exception:
            raise StatisticsStateError("Saved native energy tracking state is invalid; imports are paused to avoid resetting or duplicating consumption.") from None

    def snapshot(self):
        return {"statistic_id": self.statistic_id, "serial": self.serial, "anchor": stamp(self.anchor),
                "time_zone": str(self.zone),
                "hours": deepcopy(self.hours), "checks": deepcopy(self.checks), "issues": deepcopy(self.issues)}

    def missing_on(self, day, now):
        start, end = day_bounds(day, self.zone)
        cursor, end = max(start, self.anchor), min(end, hour_floor(now))
        while cursor < end:
            if stamp(cursor) not in self.hours:
                return True
            cursor += HOUR
        return False

    def ingest(self, day, report, now):
        """Never accept a zero unless the existing report marks it reported."""
        key = day.isoformat()
        self.checks[key] = stamp(now)
        if not regular_day(day, self.zone):
            self.issues[key] = "dst_unmapped"
            return None
        if not isinstance(report, dict) or report.get("stale"):
            self.issues[key] = "stale" if isinstance(report, dict) else "unavailable"
            return None
        buckets = report.get("buckets")
        if (report.get("view") != "day" or report.get("start") != key
                or report.get("time_zone") != str(self.zone) or report.get("hour_labels_known") is not True
                or not isinstance(buckets, list) or len(buckets) != 24):
            self.issues[key] = "unverified_hours"
            return None
        try:
            fetched = instant(report["oldest_report_at"])
            expected = [f"{i % 12 or 12} {'AM' if i < 12 else 'PM'}" for i in range(24)]
            if [b.get("label") for b in buckets] != expected:
                raise ValueError
        except Exception:
            self.issues[key] = "unverified_hours"
            return None
        try:
            hour_starts = safe_regular_hour_starts(day, self.zone)
        except TimezoneUnverified:
            self.issues[key] = "unverified_hours"
            return None
        changed = None
        ambiguous = False
        for index, bucket in enumerate(buckets):
            start = hour_starts[index]
            if start < self.anchor or start + HOUR > min(now, fetched):
                continue
            availability, value = bucket.get("availability"), bucket.get("kwh")
            if availability != "reported" or not valid_energy(value):
                ambiguous |= availability == "zero_or_unavailable"
                continue
            slot = stamp(start)
            if self.hours.get(slot) != value or slot not in self.hours:
                self.hours[slot] = value
                changed = min(start, changed) if changed is not None else start
        if self.missing_on(day, now):
            self.issues[key] = "ambiguous_zero" if ambiguous else "partial"
        else:
            self.issues.pop(key, None)
        return changed

    def records(self, since=None):
        """Rebuild cumulative prefixes so corrections never become today's spikes."""
        total = compensation = 0.0
        rows = []
        for key in sorted(self.hours):
            value = self.hours[key]
            adjusted = value - compensation
            updated = total + adjusted
            compensation = (updated - total) - adjusted
            total = updated
            if not math.isfinite(total):
                raise StatisticsStateError("Native energy total is invalid; publication is paused.")
            start = instant(key)
            if since is None or start >= since:
                rows.append(StatisticData(start=start, state=value, sum=total))
        return rows


class EnergyStatistics:
    """Independent, fail-soft polling with durable-before-publish checkpoints."""

    def __init__(self, hass, api, serial, zone=ZONE, verify_offset=False):
        self.hass, self.api = hass, api
        self.serial = str(serial)
        self.zone = require_iana_zone(str(zone))
        self.verify_offset = verify_offset
        self.statistic_id = f"{DOMAIN}:energy_{self.serial}"
        self.store = Store(
            hass, 1, f"{DOMAIN}_energy_statistics_{self.serial}"
        )
        self.ledger = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self._now = lambda: datetime.now(UTC)
        self._dirty_since = None
        self._pending_since = None
        self._metadata_queued = False
        self._unsubscribe = None
        self._stopping = False
        self._task = None
        self._error = None
        self._last_poll = None
        self._confirmed_end = None

    def metadata(self):
        # External statistics use energy unit_class/has_sum, not entity state_class.
        return {"statistic_id": self.statistic_id, "source": DOMAIN,
                "name": f"Schluter Floor Heat {self.serial} Energy", "unit_class": EnergyConverter.UNIT_CLASS,
                "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
                "has_sum": True, "mean_type": StatisticMeanType.NONE}

    async def _initialize(self, recorder, now):
        if self._initialized:
            return
        try:
            saved = await self.store.async_load()
        except Exception:
            raise StatisticsStateError("Native energy tracking state could not be loaded; imports are paused rather than resetting the source.") from None
        if saved is not None:
            self.ledger = IntervalLedger.restore(
                saved, self.serial, self.statistic_id, self.zone
            )
        elif self.ledger is None:
            previous = await recorder.async_add_executor_job(
                get_last_statistics, self.hass, 1, self.statistic_id, True, {"sum"}
            )
            if previous.get(self.statistic_id):
                raise StatisticsStateError("Native energy statistics exist without their tracking ledger; restore the ledger before resuming imports.")
            anchor = hour_floor(now)
            if anchor < now:
                anchor += HOUR
            self.ledger = IntervalLedger(
                anchor, self.serial, self.statistic_id, self.zone
            )
        # Persist the fixed anchor before creating metadata or publishing any data.
        await self.store.async_save(self.ledger.snapshot())
        self._dirty_since = self.ledger.anchor
        self._initialized = True

    def _days(self, now):
        if hour_floor(now) <= self.ledger.anchor:
            return []
        first = self.ledger.anchor.astimezone(self.zone).date()
        today = now.astimezone(self.zone).date()
        days, audits = [], []
        cursor = first
        while cursor <= today:
            key = cursor.isoformat()
            if not regular_day(cursor, self.zone):
                self.ledger.issues[key] = "dst_unmapped"
            elif cursor >= today - timedelta(days=1):
                days.append((cursor, False))
            else:
                checked = instant(self.ledger.checks[key]) if key in self.ledger.checks else None
                if self.ledger.missing_on(cursor, now):
                    due = POLL_INTERVAL
                elif self.ledger.issues.get(key) == "stale":
                    # A complete day may still have an obsolete stale flag.
                    # A successful refreshed report clears it in ingest().
                    due = STALE_RECHECK_INTERVAL
                else:
                    due = AUDIT_INTERVAL
                if checked is None or now - checked >= due:
                    audits.append((checked or datetime.min.replace(tzinfo=UTC), cursor))
            cursor += timedelta(days=1)
        days.extend((day, True) for _, day in sorted(audits)[:2])
        return days

    async def _recorded(self, recorder, rows):
        # Flush earlier queued imports before deciding that readback matches.
        # Otherwise a delayed older import could overwrite a newer correction.
        pending = recorder.async_get_commit_future()
        if pending is not None:
            await asyncio.wait_for(asyncio.shield(pending), timeout=30)
        existing = await recorder.async_add_executor_job(
            statistics_during_period, self.hass, rows[0]["start"], rows[-1]["start"] + HOUR,
            {self.statistic_id}, "hour", {"energy": "kWh"}, {"state", "sum"},
        )
        return {float(row["start"]): row for row in existing.get(self.statistic_id, [])}

    @staticmethod
    def _difference(rows, reported):
        for row in rows:
            previous = reported.get(row["start"].timestamp())
            if previous is None or any(
                type(previous.get(field)) not in (int, float)
                or not math.isclose(previous[field], row[field], rel_tol=1e-12, abs_tol=1e-10)
                for field in ("state", "sum")
            ):
                return row["start"]
        return None

    async def _publish(self, recorder):
        start = min((v for v in (self._dirty_since, self._pending_since) if v is not None), default=None)
        rows = self.ledger.records(start) if start is not None else []
        if not self._metadata_queued:
            async_add_external_statistics(self.hass, self.metadata(), [])
            self._metadata_queued = True
        if not rows:
            self._dirty_since = None
            return
        different = self._difference(rows, await self._recorded(recorder, rows))
        self._dirty_since = None
        if different is not None:
            # This is an idempotent upsert at original hours, never an additive delta.
            self._pending_since = different
            tail = [r for r in rows if r["start"] >= different]
            async_add_external_statistics(self.hass, self.metadata(), tail)
            if self._difference(tail, await self._recorded(recorder, tail)) is not None:
                return
        self._pending_since = None
        self._confirmed_end = rows[-1]["start"] + HOUR

    def check_current_offset(self, now):
        """Require a fresh matching reported offset before any UI import.

        Authentication/discovery and provider payloads remain internal.
        """
        try:
            thermostats = self.api.get_thermostats()
            for thermostat in thermostats or ():
                if str(getattr(thermostat, "serial_number", "")) == self.serial:
                    raw = getattr(thermostat, "data", None)
                    if type(raw) is not dict:
                        break
                    check_thermostat_offset(self.zone, raw.get("TZOffset"), now)
                    return
        except TimezoneUnverified:
            raise StatisticsStateError(
                "Thermostat timezone or current UTC offset could not be verified; imports are paused."
            ) from None
        except Exception:
            raise StatisticsStateError(
                "Thermostat timezone offset could not be refreshed; imports are paused."
            ) from None
        raise StatisticsStateError(
            "Thermostat offset is unavailable; imports are paused."
        )

    async def async_update(self, _now=None):
        if self._stopping or self._lock.locked():
            return
        async with self._lock:
            self._task = asyncio.current_task()
            now = self._now().astimezone(UTC)
            try:
                recorder = get_instance(self.hass)
                if not await asyncio.wait_for(asyncio.shield(recorder.async_db_ready), timeout=30):
                    raise StatisticsStateError("Recorder is not ready for native energy statistics.")
                if self.verify_offset:
                    await self.hass.async_add_executor_job(self.check_current_offset, now)
                await self._initialize(recorder, now)
                for day, refresh in self._days(now):
                    try:
                        report = await async_get_energy_report(
                            self.hass, self.api, self.serial, "day",
                            day.isoformat(), refresh, zone=self.zone,
                        )
                    except Exception:
                        report = None
                    changed = self.ledger.ingest(day, report, now)
                    if changed is not None:
                        self._dirty_since = min(self._dirty_since, changed) if self._dirty_since is not None else changed
                # If this save fails, keep the in-memory intervals but publish nothing.
                await self.store.async_save(self.ledger.snapshot())
                await self._publish(recorder)
                self._error = None
                self._last_poll = now
            except StatisticsStateError as err:
                self._error = str(err)
                _LOGGER.warning("Schluter native energy tracking paused: %s", self._error)
            except Exception:
                self._error = "Native energy statistics could not be updated; existing data is retained and the next poll will retry."
                _LOGGER.warning("Schluter native energy statistics update failed; existing data retained")
            finally:
                self._task = None

    def status(self):
        now = self._now().astimezone(UTC)
        ledger = self.ledger
        elapsed = max(0, int((hour_floor(now) - ledger.anchor) / HOUR)) if ledger else 0
        known = sum(instant(key) < hour_floor(now) for key in ledger.hours) if ledger else 0
        missing = max(0, elapsed - known)
        try:
            total = math.fsum(ledger.hours.values()) if ledger and ledger.hours else None
        except (ValueError, OverflowError):
            total = None
        state = ("paused" if self._error else "initializing" if not self._initialized else
                 "degraded" if missing or ledger.issues else "waiting_for_completed_hour" if not ledger.hours else
                 "publishing" if self._pending_since is not None or self._dirty_since is not None else "tracking")
        return {"statistic_id": self.statistic_id, "serial": self.serial, "name": f"Schluter Floor Heat {self.serial} Energy", "unit_of_measurement": "kWh",
                "state": state, "tracking_start": stamp(ledger.anchor) if ledger else None,
                "known_intervals": len(ledger.hours) if ledger else 0, "missing_completed_hours": missing,
                "known_kwh": total,
                "days_requiring_attention": deepcopy(ledger.issues) if ledger else {},
                "last_completed_poll": stamp(self._last_poll) if self._last_poll else None,
                "recorder_pending": self._pending_since is not None or self._dirty_since is not None,
                "recorder_confirmed_latest_interval_end": stamp(self._confirmed_end) if self._confirmed_end else None,
                "error": self._error, "historical_backfill": False}

    async def async_start(self, _hass):
        if self._unsubscribe is not None or self._stopping:
            return
        self._unsubscribe = async_track_time_interval(self.hass, self.async_update, POLL_INTERVAL)
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.async_stop)
        await self.async_update()

    async def async_stop(self, _event):
        self._stopping = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


async def async_setup_energy_statistics(hass, api):
    """Set up independent energy statistics for each thermostat."""
    data = hass.data.setdefault(DOMAIN, {})

    if DATA_STATISTICS in data:
        return

    # Reserve setup before the first await to prevent duplicate discovery.
    data[DATA_STATISTICS] = {}

    try:
        thermostats = await hass.async_add_executor_job(
            api.get_thermostats
        )
    except Exception:
        data.pop(DATA_STATISTICS, None)
        _LOGGER.warning(
            "Schluter energy statistics: thermostat discovery failed"
        )
        return

    workers = {}
    for thermostat in thermostats:
        serial = str(thermostat.serial_number)

        if not serial or serial == "None" or serial in workers:
            continue

        workers[serial] = EnergyStatistics(hass, api, serial)

    if not workers:
        data.pop(DATA_STATISTICS, None)
        _LOGGER.warning(
            "Schluter energy statistics: no thermostats discovered"
        )
        return

    data[DATA_STATISTICS] = workers

    async def read_status(_call):
        return {
            "thermostats": {
                serial: worker.status()
                for serial, worker in workers.items()
            }
        }

    if not hass.services.has_service(
        DOMAIN, "get_energy_statistics_status"
    ):
        hass.services.async_register(
            DOMAIN,
            "get_energy_statistics_status",
            read_status,
            schema=probatio.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )

    for worker in workers.values():
        async_at_started(hass, worker.async_start)


async def async_setup_energy_statistics_entry(hass, api, discovered, time_zones):
    """Start only timezone-configured UI workers; others remain visibly paused.

    No default timezone is inferred. Every active worker independently checks
    fresh device TZOffset and persisted timezone provenance before any writes.
    """
    data = hass.data.get(DOMAIN)
    if type(data) is not dict or DATA_STATISTICS in data:
        raise StatisticsStateError("The Schluter energy runtime is already active.")
    if type(discovered) is not dict or type(time_zones) is not dict:
        raise StatisticsStateError("Thermostat timezone settings are unavailable.")

    workers, paused = {}, {}
    for serial in sorted(discovered):
        try:
            zone = configured_device_zone(time_zones, serial)
        except TimezoneUnverified:
            paused[serial] = {
                "state": "paused",
                "reason": "Choose a valid timezone for this thermostat in Configure.",
            }
            continue
        workers[serial] = EnergyStatistics(
            hass, api, serial, zone=zone, verify_offset=True,
        )

    # Reserve the key before subscribing/starting, so a second setup can't
    # publish duplicates. No Store/Recorder writes take place during setup.
    data[DATA_STATISTICS] = workers
    data["energy_timezone_paused"] = paused

    async def read_status(_call):
        return {
            "thermostats": {
                **{serial: worker.status() for serial, worker in workers.items()},
                **paused,
            },
        }

    if not hass.services.has_service(DOMAIN, "get_energy_statistics_status"):
        hass.services.async_register(
            DOMAIN, "get_energy_statistics_status", read_status,
            schema=probatio.Schema({}), supports_response=SupportsResponse.ONLY,
        )
    for worker in workers.values():
        async_at_started(hass, worker.async_start)


async def async_stop_energy_statistics_entry(hass):
    """Stop polling and drain in-flight worker tasks before unloading the UI."""
    data = hass.data.get(DOMAIN)
    if type(data) is not dict:
        return
    workers = data.get(DATA_STATISTICS, {})
    if type(workers) is dict:
        for worker in workers.values():
            await worker.async_stop(None)
    if hass.services.has_service(DOMAIN, "get_energy_statistics_status"):
        hass.services.async_remove(DOMAIN, "get_energy_statistics_status")
    data.pop(DATA_STATISTICS, None)
    data.pop("energy_timezone_paused", None)
