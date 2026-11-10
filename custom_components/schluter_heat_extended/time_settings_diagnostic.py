"""Read-only, metadata-only inspection of cached thermostat clock fields.

Never return values, identifiers, or arbitrary provider payloads. This module
does not make network requests or change thermostat settings.
"""

from __future__ import annotations

import re

_TIME_TERMS = ("time", "zone", "offset", "dst", "daylight", "clock", "sync", "ntp", "summer")
_PRIVATE_TERMS = (
    "serial", "token", "session", "password", "secret", "cookie", "email",
    "address", "postal", "zipcode", "latitude", "longitude", "account",
    "username", "userid", "phone", "network", "ssid", "auth", "credential",
)
_SAFE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,47}\Z")
_GROUPS = {
    "settings", "configuration", "config", "timesettings",
    "timeconfiguration", "clocksettings", "clock", "time",
    "thermostat", "device", "options", "system", "systemsettings",
    "usersettings", "preferences", "location", "locationinfo",
}


def _value_type(value):
    """Return a controlled type name; never invoke provider-defined repr."""
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    return "other"


def inspect_time_settings(raw):
    """Return only relevant JSON field paths/types from one cached device.

    Nested traversal is restricted to known generic configuration containers.
    Array contents, identity fields, location values, and all other values
    are never inspected or returned. Results are capped to 32 fields.
    """
    fields = []

    def walk(mapping, prefix=(), depth=0):
        if type(mapping) is not dict or depth > 3:
            return
        # Provider payloads are expected to use string keys; ignore malformed ones.
        for name in sorted(key for key in mapping if type(key) is str):
            if len(fields) >= 32:
                break
            if type(name) is not str or not _SAFE_KEY.fullmatch(name):
                continue
            lower = name.lower()
            if any(term in lower for term in _PRIVATE_TERMS):
                continue
            value = mapping[name]
            if any(term in lower for term in _TIME_TERMS):
                fields.append({"field": ".".join((*prefix, name)),
                               "type": _value_type(value)})
            if type(value) is dict and lower in _GROUPS and depth < 3:
                walk(value, (*prefix, name), depth + 1)

    walk(raw)
    return {
        "source": "cached_thermostat_data",
        "time_related_fields": fields,
        "values_included": False,
        "read_only": True,
        "note": "Field names/types are clues only; they do not prove actual clock or DST behavior.",
    }


def inspect_locations_time_settings(raw):
    """Inspect location payload field names/types, never any provider values.

    Accept the known list-of-locations response or a Locations/locations
    envelope. Traverse only bounded, recognized configuration containers and
    nested thermostat/device/group lists. Never output names, identifiers,
    counts of devices, authentication details, or arbitrary data.
    """
    if type(raw) is list:
        locations = raw
    elif type(raw) is dict:
        locations = raw.get("locations", raw.get("Locations"))
    else:
        locations = None
    if type(locations) is not list:
        return {"source": "locations_api", "status": "unavailable",
                "time_related_fields": [], "values_included": False,
                "read_only": True}

    fields = {}
    list_groups = {"thermostats", "devices", "groups"}
    def scan(mapping, path=(), depth=0):
        if type(mapping) is not dict or depth > 3:
            return
        for name in sorted(key for key in mapping if type(key) is str):
            if len(fields) >= 48:
                break
            if not _SAFE_KEY.fullmatch(name):
                continue
            lower = name.lower()
            if any(term in lower for term in _PRIVATE_TERMS):
                continue
            val = mapping[name]
            this_path = (*path, name)
            if any(term in lower for term in _TIME_TERMS):
                fields[(".".join(this_path), _value_type(val))] = None
            if type(val) is dict and lower in _GROUPS and depth < 3:
                scan(val, this_path, depth + 1)
            elif type(val) is list and lower in list_groups and depth < 3:
                for sub in val[:8]:
                    scan(sub, (*path, name + "[]"), depth + 1)
                    if len(fields) >= 48:
                        break

    for location in locations[:16]:
        scan(location, ("location[]",))
        if len(fields) >= 48:
            break
    return {
        "source": "locations_api",
        "status": "inspected",
        "time_related_fields": [
            {"field": field, "type": field_type}
            for field, field_type in sorted(fields)
        ],
        "values_included": False,
        "read_only": True,
        "note": "Names/types across locations only; cannot establish a zone for a specific thermostat.",
    }
