"""Pure safety rules for a future Schluter config-entry migration.

This module deliberately does not enable config entries or alter existing YAML,
statistics, Recorder, or thermostat API state.
"""

from __future__ import annotations

from .timezone_rules import TimezoneUnverified, require_iana_zone


def account_unique_id(username: str) -> str:
    """Normalize a Schluter login name for duplicate-entry prevention.

    Never include the password, access session, or device serial in unique IDs.
    """
    if type(username) is not str:
        raise ValueError("A Schluter account email is required.")
    email = username.strip().casefold()
    if not email or len(email) > 254 or email.count("@") != 1:
        raise ValueError("A valid Schluter account email is required.")
    local, domain = email.split("@", 1)
    if not local or not domain or "." not in domain or any(char.isspace() for char in email):
        raise ValueError("A valid Schluter account email is required.")
    return email


def timezone_options_for_device(current: dict, serial: str, zone_name: str,
                                discovered: dict) -> dict:
    """Return a new per-device timezone map after explicit user selection.

    Never silently set all devices to Home Assistant's host timezone. Existing
    choices for other devices remain untouched. No writes are made here.
    """
    if type(current) is not dict or type(discovered) is not dict:
        raise TimezoneUnverified("Time zone options are unavailable.")
    if type(serial) is not str or not serial.isdigit() or serial not in discovered:
        raise TimezoneUnverified("The selected thermostat was not discovered.")
    require_iana_zone(zone_name)
    if any(type(key) is not str or type(value) is not str
           for key, value in current.items()):
        raise TimezoneUnverified("Saved thermostat time zones are invalid.")
    return {**current, serial: zone_name}
