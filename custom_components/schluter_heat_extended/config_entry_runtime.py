"""Config-entry runtime account bootstrap, separate from legacy YAML setup.

Run blocking functions in Home Assistant's executor. Never log credentials,
tokens, raw provider exceptions, serial numbers, or API payloads.
Energy workers may start only for explicitly configured per-device timezones;
current provider offsets and ledger provenance are validated before import.
"""

from __future__ import annotations

from dataclasses import dataclass

from .v_lib.api import Api
from .v_lib.authenticator import Authenticator, AuthenticationState


@dataclass
class ConnectedAccount:
    """Transient connection data; belongs in config_entry.runtime_data."""

    api: Api
    device_names_by_serial: dict[str, str]


def connect_account(email: str, password: str) -> tuple[str, ConnectedAccount | None]:
    """Connect without ever reading/writing the legacy YAML session cache.

    Returns a safe status and connection; no raw exception escapes to HA logs.
    Session refresh goes through the existing Authenticator callback, in memory.
    """
    api = None
    connected = False
    try:
        api = Api(email=email, password=password)
        authenticator = Authenticator(api, email, password, session_id_cache_file=None)
        auth_result = authenticator.authenticate()
        if auth_result is None:
            return "cannot_connect", None
        if auth_result.state in (
            AuthenticationState.BAD_EMAIL, AuthenticationState.BAD_PASSWORD
        ):
            return "invalid_auth", None
        if auth_result.state != AuthenticationState.AUTHENTICATED:
            return "cannot_connect", None
        thermostats = api.get_thermostats()
        if not thermostats:
            return "no_thermostats", None
        names: dict[str, str] = {}
        for device in thermostats:
            serial = str(getattr(device, "serial_number", ""))
            name = getattr(device, "name", None)
            if serial.isdigit() and serial not in names:
                names[serial] = (
                    str(name)[:80] if isinstance(name, str) and name.strip()
                    else "Schluter Thermostat"
                )
        if not names:
            return "no_thermostats", None
        connected = True
        return "ok", ConnectedAccount(api, names)
    except Exception:
        return "cannot_connect", None
    finally:
        if api is not None and not connected:
            close_account(api)


def refresh_account(api: Api) -> bool:
    """Refresh an in-memory Config Entry session, without cache writes."""
    try:
        response = api.get_session(force=True)
        if response is None or not 200 <= response.status_code < 300:
            return False
        result = response.json()
        return (
            type(result) is dict
            and result.get("ErrorCode") in (0, "0")
            and type(result.get("SessionId")) is str
            and bool(result["SessionId"].strip())
        )
    except Exception:
        return False


def close_account(api: Api) -> None:
    """Close session resources on unload or connection failure."""
    try:
        api._http_session.close()
    except Exception:
        pass
