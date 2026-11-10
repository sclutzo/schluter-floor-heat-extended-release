"""Conservative timezone helpers for Schluter energy time mapping.

This module has no Home Assistant imports and is not wired into the runtime
collector. A thermostat's current UTC offset is an optional consistency check,
NOT evidence that the configured IANA time zone is authoritative. The caller
must obtain or explicitly configure the zone separately.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TimezoneUnverified(ValueError):
    """Timezone or hourly mapping could not be verified safely."""


def require_iana_zone(zone_name: str) -> ZoneInfo:
    """Require a named, loadable IANA zone, not a bare numeric UTC offset."""
    if not isinstance(zone_name, str) or not zone_name or zone_name.strip() != zone_name:
        raise TimezoneUnverified("A valid IANA timezone name is required.")
    # UTC is an unambiguous named IANA zone. Names such as America/New_York
    # are preferred; do not accept a numeric offset as the timezone identity.
    if zone_name != "UTC" and "/" not in zone_name:
        raise TimezoneUnverified("Use a named IANA timezone, not a UTC offset.")
    try:
        return ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise TimezoneUnverified("The IANA timezone name is not available.") from None


def check_thermostat_offset(zone: ZoneInfo, reported_offset: str, at_utc: datetime) -> None:
    """Fail if a device-reported UTC offset disagrees at an observed instant.

    Passing this check cannot prove that ``zone`` matches the device's
    historical DST rules. The caller must verify/select the IANA zone first.
    """
    if at_utc.tzinfo is None or at_utc.utcoffset() is None:
        raise TimezoneUnverified("An aware UTC observation time is required.")
    if not isinstance(reported_offset, str):
        raise TimezoneUnverified("A thermostat UTC offset is required.")
    try:
        observed = datetime.strptime(reported_offset, "%z").utcoffset()
    except ValueError:
        raise TimezoneUnverified("Thermostat UTC offset is unrecognized.") from None
    expected = at_utc.astimezone(zone).utcoffset()
    if observed != expected:
        raise TimezoneUnverified("Thermostat offset disagrees with the selected timezone.")


def local_day_bounds_utc(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Return UTC instants bounding one local calendar day."""
    if not isinstance(day, date) or isinstance(day, datetime):
        raise TimezoneUnverified("A local calendar date is required.")
    local_start = datetime.combine(day, time.min, zone)
    local_end = datetime.combine(day + timedelta(days=1), time.min, zone)
    return local_start.astimezone(UTC), local_end.astimezone(UTC)


def safe_regular_hour_starts(day: date, zone: ZoneInfo) -> tuple[datetime, ...]:
    """UTC starts for exactly 24 locally ordered, UTC-aligned hourly buckets.

    Refuse DST changes (23/25-hour days), unusual transitions, and fractional
    offsets whose local hour boundaries land between UTC hour boundaries.
    This validates *clock mapping*, not the provider's bucket ordering.
    Only call it for provider reports after their ordering is independently
    verified; do not manufacture hourly data from daily aggregates.
    """
    start, end = local_day_bounds_utc(day, zone)
    if end - start != timedelta(hours=24):
        raise TimezoneUnverified("A DST or offset-change day has ambiguous hour buckets.")
    starts = tuple(start + timedelta(hours=i) for i in range(24))
    for hour, utc_start in enumerate(starts):
        local = utc_start.astimezone(zone)
        if (utc_start.minute or utc_start.second or utc_start.microsecond or
                local.date() != day or local.hour != hour or local.minute != 0 or
                local.second != 0 or local.fold != 0):
            raise TimezoneUnverified("Hour buckets cannot be aligned safely to UTC hours.")
    return starts


def configured_device_zone(time_zones: dict, serial: str) -> ZoneInfo:
    """Require an explicitly selected IANA timezone for this exact thermostat.

    Never infer from the Home Assistant host timezone or from a UTC offset.
    Serial numbers are used as dictionary keys only, not embedded in errors.
    """
    if type(time_zones) is not dict or type(serial) is not str or not serial.isdigit():
        raise TimezoneUnverified("Per-thermostat timezone configuration is invalid.")
    return require_iana_zone(time_zones.get(serial))


def verify_ledger_zone(saved: dict | None, selected_zone: str) -> str:
    """Check immutable mapping provenance before reading/publishing intervals.

    Existing v1.0.2 ledgers were explicitly mapped with America/New_York.
    A legacy ledger lacking a timezone may be migrated *only* when that exact
    zone was explicitly selected; never re-date stored UTC hourly intervals.
    A returned status is information for the future migration code, not a write.
    """
    require_iana_zone(selected_zone)
    if saved is None:
        return "new"
    if type(saved) is not dict:
        raise TimezoneUnverified("Saved energy tracking state is invalid; imports must pause.")
    old_zone = saved.get("time_zone")
    if old_zone is None:
        if selected_zone != "America/New_York":
            raise TimezoneUnverified(
                "Legacy energy history uses America/New_York; timezone migration requires review."
            )
        return "legacy_new_york"
    if type(old_zone) is not str or old_zone != selected_zone:
        raise TimezoneUnverified(
            "The thermostat timezone changed since energy tracking began; imports must pause."
        )
    return "unchanged"
