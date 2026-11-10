"""Read-only provider reports. Resolutions are never expanded into invented detail."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime, time, timedelta
import logging
import math
import re
from zoneinfo import ZoneInfo

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store

from .timezone_rules import TimezoneUnverified, safe_regular_hour_starts
from .v_lib.api import EnergyReadError

_LOGGER = logging.getLogger(__name__)
ZONE = ZoneInfo("America/New_York")
DATA_ENERGY = "energy_reports"
CURRENT_TTL = 15 * 60
HISTORICAL_TTL = 7 * 86400
_CACHE_SAVE_WARNING = "Energy cache could not be saved; retrieved data remains available in memory."
# Evidence boundaries, not API limits: earlier nonzero data is still accepted.
# Zero buckets before these observations cannot establish historical availability.
_OBSERVED_FROM = {"monthly": date(2021, 10, 1), "daily": date(2024, 1, 7),
                  "hourly": date(2026, 10, 25)}


def period_bounds(view, value):
    """Calendar boundaries are local dates, never elapsed 24-hour durations."""
    try:
        if not isinstance(value, str):
            raise ValueError
        if view == "year":
            if not re.fullmatch(r"[1-9]\d{3}", value):
                raise ValueError
            start = date(int(value), 1, 1)
            return start, date(start.year + 1, 1, 1)
        start = date.fromisoformat(value)
        if start.isoformat() != value:
            raise ValueError
        if view == "day":
            return start, start + timedelta(days=1)
        if view == "week":
            start -= timedelta(days=(start.weekday() + 1) % 7)
            return start, start + timedelta(days=7)
        if view == "month":
            start = start.replace(day=1)
            return start, date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    except (TypeError, ValueError, OverflowError):
        pass
    raise HomeAssistantError("Choose Day, Week, Month, or Year and a valid reporting date.") from None


def normalize_report(view, body):
    """Whitelist consumption fields only; reject unknown ordering/envelopes."""
    if not isinstance(body, dict) or body.get("Success") is False:
        raise EnergyReadError("Schluter returned an unrecognized energy report.")
    rows = body.get("EnergyUsage")
    if not isinstance(rows, list) or len(rows) > 1:
        raise EnergyReadError("Schluter returned an unrecognized energy report.")
    if not rows:
        return []
    row = rows[0]
    values = row.get("Usage") if isinstance(row, dict) else None
    if not isinstance(values, list):
        raise EnergyReadError("Schluter returned an unrecognized energy report.")
    if not values:
        return []
    if view == "week" and body.get("MondayIsFirstDay") is not False:
        raise EnergyReadError("The energy report's weekday ordering could not be verified.")
    if ((view == "week" and len(values) != 7)
            or (view == "year" and len(values) != 12)
            or (view == "day" and len(values) not in (23, 24, 25))):
        raise EnergyReadError("Schluter returned an incomplete or unrecognized energy breakdown.")
    clean = []
    try:
        for item in reversed(values):
            if not isinstance(item, dict):
                raise ValueError
            energy, charge = item.get("EnergyKWattHour"), item.get("ChargeKWattHour")
            if type(energy) not in (int, float) or not math.isfinite(energy) or energy < 0:
                raise ValueError
            if charge is not None and (type(charge) not in (int, float) or not math.isfinite(charge)):
                raise ValueError
            # Minutes is not usable: captured nonzero consumption has Minutes=0.
            clean.append({"kwh": energy, "charge": charge})
    except (TypeError, ValueError, OverflowError):
        raise EnergyReadError("Schluter returned invalid energy values.") from None
    return clean


def _stamp(value):
    return value.astimezone(UTC).isoformat()


def _instant(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


class EnergyReports:
    """On-demand, deduplicated reports; no background thermostat polling/writes."""

    def __init__(self, hass, api):
        self.hass, self.api = hass, api
        self.store = Store(hass, 1, "schluter_heat_extended_energy_reports")
        self.reports = {}
        self._loaded = False
        self._dirty = False
        self._lock = asyncio.Lock()
        self._network = asyncio.Semaphore(2)
        self._now = lambda: datetime.now(UTC)
        self._cache_warning = None

    async def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            data = await self.store.async_load()
            entries = data.get("reports", {}) if isinstance(data, dict) else {}
            if not isinstance(entries, dict):
                raise ValueError
            for key, entry in entries.items():
                try:
                    serial, view, value = key.split("|")
                    if not serial.isdigit() or view not in ("day", "week", "year"):
                        raise ValueError
                    period_bounds(view, value)
                    fetched = _stamp(_instant(entry["fetched_at"]))
                    checked = _stamp(_instant(entry.get("checked_at", fetched)))
                    # Revalidate persisted data with the same strict response parser.
                    usage = entry["usage"]
                    body = {"MondayIsFirstDay": False, "EnergyUsage": [{"Usage": [
                        {"EnergyKWattHour": item["kwh"], "ChargeKWattHour": item["charge"]}
                        for item in reversed(usage)
                    ]}]}
                    clean = normalize_report(view, body)
                    self.reports[key] = {"usage": clean, "fetched_at": fetched,
                                         "checked_at": checked,
                                         "retained": entry.get("retained") is True}
                except Exception:
                    self._cache_warning = "Some saved energy reports were invalid and were skipped."
        except Exception:
            self._cache_warning = "Saved energy reports could not be loaded; available reports will be retrieved again."
            _LOGGER.warning("Schluter energy cache could not be loaded. No cache contents or exception details logged.")

    async def _fetch(self, serial, view, value, now, refresh, zone):
        key = f"{serial}|{view}|{value}"
        old = self.reports.get(key)
        _, end = period_bounds(view, value)
        today = now.astimezone(zone).date()
        ttl = CURRENT_TTL if end >= today - timedelta(days=1) else HISTORICAL_TTL
        if old and not refresh:
            age = (now - _instant(old["checked_at"])).total_seconds()
            if 0 <= age < ttl:
                return old, bool(old["retained"]), None
        try:
            async with self._network:
                body = await self.hass.async_add_executor_job(
                    self.api.get_energy_usage, serial, view, value
                )
            usage = normalize_report(view, body)
            entry = {"usage": usage, "fetched_at": _stamp(now),
                     "checked_at": _stamp(now), "retained": False}
            if old and any(item["kwh"] > 0 for item in old["usage"]) and not any(item["kwh"] > 0 for item in usage):
                # An empty/all-zero response cannot prove historical consumption vanished.
                entry = {**old, "checked_at": _stamp(now), "retained": True}
            self.reports[key] = entry
            self._dirty = True
            return entry, entry["retained"], None
        except EnergyReadError as err:
            message = "One or more energy reports could not be refreshed."
            if re.fullmatch(r"Schluter energy request returned HTTP \d{3}\.", str(err)):
                message = str(err)
            elif str(err) == "Schluter did not authorize the energy report. Check integration authentication.":
                message = str(err)
            return old, bool(old), message
        except Exception:
            # Never propagate arbitrary exception text: it may contain session URLs.
            return old, bool(old), "One or more energy reports could not be refreshed."

    async def _save(self):
        if not self._dirty:
            return
        try:
            await self.store.async_save({"reports": deepcopy(self.reports)})
            self._dirty = False
            if self._cache_warning == _CACHE_SAVE_WARNING:
                self._cache_warning = None
        except Exception:
            self._cache_warning = _CACHE_SAVE_WARNING
            _LOGGER.warning("Schluter energy cache could not be saved. Keeping reports in memory.")

    async def get(self, serial, view, value, refresh=False, zone=None):
        # Legacy YAML reports retain the v1.0.2 New York interpretation.
        # Config entries must pass a verified per-device ZoneInfo explicitly.
        zone = ZONE if zone is None else zone
        if not isinstance(zone, ZoneInfo):
            raise HomeAssistantError("Thermostat timezone has not been verified.")
        start, end = period_bounds(view, value)
        async with self._lock:
            now = self._now().astimezone(UTC)
            today = now.astimezone(zone).date()
            if start > today:
                raise HomeAssistantError("Choose a current or previous energy reporting period.")
            await self._load()
            if view == "month":
                cursor = start - timedelta(days=(start.weekday() + 1) % 7)
                requests = []
                while cursor < end and cursor <= today:
                    requests.append(("week", cursor.isoformat()))
                    cursor += timedelta(days=7)
            else:
                requests = [(view, str(start.year) if view == "year" else start.isoformat())]
            results = await asyncio.gather(*(
                self._fetch(str(serial), source, anchor, now, refresh, zone)
                for source, anchor in requests
            ))
            await self._save()
            warnings = [item[2] for item in results if item[2]]
            if self._cache_warning:
                warnings.append(self._cache_warning)
            if any(stale for _, stale, _ in results):
                warnings.append("Showing older cached values where current data could not be established.")
            buckets = []
            fetched = []
            if view == "month":
                by_date = {}
                for (_, anchor), (entry, stale, _) in zip(requests, results):
                    if entry:
                        fetched.append(entry["fetched_at"])
                        for i, item in enumerate(entry["usage"]):
                            day = date.fromisoformat(anchor) + timedelta(days=i)
                            by_date[day] = (item, entry, stale)
                cursor = start
                while cursor < end:
                    item, entry, stale = by_date.get(cursor, (None, None, False))
                    buckets.append(self._bucket(cursor.isoformat(), str(cursor.day), cursor,
                                                cursor + timedelta(days=1), item, entry, stale, today, "daily"))
                    cursor += timedelta(days=1)
                hour_labels_known = None
            else:
                entry, stale, _ = results[0]
                if entry:
                    fetched.append(entry["fetched_at"])
                usage = entry["usage"] if entry else []
                hour_labels_known = None
                if view == "day":
                    try:
                        safe_regular_hour_starts(start, zone)
                        hour_labels_known = len(usage) == 24
                    except TimezoneUnverified:
                        hour_labels_known = False
                    if not hour_labels_known:
                        warnings.append("Hourly clock mapping is unverified for this date. Buckets are shown in provider order without hourly timestamps.")
                    for i, item in enumerate(usage):
                        label = f"{i % 12 or 12} {'AM' if i < 12 else 'PM'}" if hour_labels_known else f"Bucket {i + 1}"
                        bucket = self._bucket(str(i), label, start, end, item, entry, stale, today, "hourly")
                        # Never assign UTC instants or infer which current-day hours are future.
                        bucket.pop("date", None)
                        buckets.append(bucket)
                else:
                    for i in range(7 if view == "week" else 12):
                        day = start + timedelta(days=i) if view == "week" else date(start.year, i + 1, 1)
                        next_day = (day + timedelta(days=1) if view == "week" else
                                    date(day.year + (day.month == 12), day.month % 12 + 1, 1))
                        label = day.strftime("%a") if view == "week" else day.strftime("%b")
                        buckets.append(self._bucket(day.isoformat(), label, day, next_day,
                                                    usage[i] if usage else None, entry, stale, today,
                                                    "daily" if view == "week" else "monthly"))
            relevant = [b for b in buckets if b["availability"] != "future"]
            known = [b for b in relevant if b["kwh"] is not None]
            ambiguous = any(b["availability"] == "zero_or_unavailable" for b in relevant)
            if not known:
                status = "unavailable"
            elif len(known) != len(relevant):
                status = "partial"
            elif ambiguous:
                status = "zero_or_unavailable"
            else:
                status = "reported"
            if ambiguous:
                warnings.append("Reported zeros in all-zero reports or before verified history cannot distinguish actual zero consumption from unavailable data. No missing detail has been reconstructed.")
            return {"view": view, "start": start.isoformat(), "end_exclusive": end.isoformat(),
                    "time_zone": str(zone), "buckets": buckets, "status": status,
                    "total_kwh": math.fsum(b["kwh"] for b in known) if known else None,
                    "incomplete": end > today, "stale": any(b["stale"] for b in buckets),
                    "hour_labels_known": hour_labels_known,
                    "oldest_report_at": min(fetched) if fetched else None,
                    "latest_report_at": max(fetched) if fetched else None,
                    "warnings": list(dict.fromkeys(warnings)),
                    "history_evidence": {"monthly": "Observed from approximately October 2021",
                                         "daily": "Verified at least as far back as January 2024",
                                         "hourly": "Historical retention unverified"}}

    @staticmethod
    def _bucket(key, label, start, end, item, entry, stale, today, resolution):
        future = start > today
        ambiguous = bool(entry and entry["usage"] and not any(v["kwh"] > 0 for v in entry["usage"]))
        if item is not None and item["kwh"] == 0 and start < _OBSERVED_FROM[resolution]:
            ambiguous = True
        availability = ("future" if future else "unavailable" if item is None else
                        "zero_or_unavailable" if ambiguous else "reported")
        return {"key": key, "label": label, "date": start.isoformat(),
                "kwh": item["kwh"] if item is not None and not future else None,
                "charge": item["charge"] if item is not None and not future else None,
                "availability": availability, "provisional": start <= today < end,
                "stale": bool(stale) and not future}


async def async_get_energy_report(hass, api, serial, view, value, refresh=False, zone=None):
    """Reuse the active client; no authentication, discovery, or control calls."""
    data = hass.data.setdefault("schluter_heat_extended", {})
    manager = data.get(DATA_ENERGY)
    if manager is None or manager.api is not api:
        manager = data[DATA_ENERGY] = EnergyReports(hass, api)
    return await manager.get(serial, view, value, refresh, zone=zone)
