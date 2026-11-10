"""Schluter Floor Heat Extended integration setup."""

# Modified from Home Assistant Core's Apache-2.0 Schluter integration.
# Changes: separate domain/session cache, bundled API, periodic refresh, and energy setup.
# Original: https://github.com/home-assistant/core/tree/dev/homeassistant/components/schluter


from __future__ import annotations

from datetime import timedelta
import logging

from requests import RequestException, Session
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .config_entry_runtime import close_account, connect_account, refresh_account
from .energy_statistics import (
    async_setup_energy_statistics_entry, async_stop_energy_statistics_entry,
)
from .const import DOMAIN
from .v_lib.api import Api
from .v_lib.authenticator import Authenticator, AuthenticationState

_LOGGER = logging.getLogger(__name__)
DATA_SCHLUTER_SESSION = "schluter_session"
DATA_SCHLUTER_API = "schluter_api"
SCHLUTER_CONFIG_FILE = ".schluter_heat_extended.conf"
API_TIMEOUT = 10
SESSION_REFRESH_INTERVAL = timedelta(minutes=15)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN): vol.Schema(
            {
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


def setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Schluter thermostats with a separate, cached authentication session."""
    if DOMAIN not in config:
        # UI config entries are handled separately by async_setup_entry.
        return True
    conf = config[DOMAIN]
    api = Api(
        timeout=API_TIMEOUT,
        http_session=Session(),
        email=conf[CONF_USERNAME],
        password=conf[CONF_PASSWORD],
    )
    authenticator = Authenticator(
        api,
        conf[CONF_USERNAME],
        conf[CONF_PASSWORD],
        session_id_cache_file=hass.config.path(SCHLUTER_CONFIG_FILE),
    )

    try:
        authentication = authenticator.authenticate()
    except RequestException:
        _LOGGER.error("Could not connect to Schluter for authentication")
        return False

    if authentication is None:
        _LOGGER.error("Schluter authentication did not return a result")
        return False
    if authentication.state != AuthenticationState.AUTHENTICATED:
        _LOGGER.error("Schluter authentication failed: %s", authentication.state.value)
        return False

    hass.data[DOMAIN] = {
        DATA_SCHLUTER_API: api,
        DATA_SCHLUTER_SESSION: authentication.session_id,
    }

    async def _refresh_session(_now):
        """Reauthenticate in an executor; the authenticator updates its cache."""
        try:
            response = await hass.async_add_executor_job(api.get_session, None, None, True)
            acknowledged = False
            if response is not None and 200 <= response.status_code < 300:
                try:
                    payload = response.json()
                    acknowledged = (
                        isinstance(payload, dict)
                        and payload.get("ErrorCode") in (0, "0")
                        and isinstance(payload.get("SessionId"), str)
                        and bool(payload["SessionId"].strip())
                    )
                except Exception:
                    pass
            if acknowledged:
                hass.data[DOMAIN][DATA_SCHLUTER_SESSION] = api._session_id
            else:
                _LOGGER.warning("Periodic Schluter session refresh did not succeed")
        except Exception:
            _LOGGER.warning("Periodic Schluter session refresh could not be completed")

    async_track_time_interval(hass, _refresh_session, SESSION_REFRESH_INTERVAL)
    discovery.load_platform(hass, Platform.CLIMATE, DOMAIN, {}, config)
    try:
        from .energy_statistics import async_setup_energy_statistics
        hass.add_job(async_setup_energy_statistics, hass, api)
    except Exception:
        _LOGGER.warning(
            "Schluter energy statistics could not start; thermostat controls are unaffected"
        )
    return True


_UI_SERVICES = (
    "set_temporary_temperature", "return_to_schedule", "get_schedule",
    "save_schedule", "get_energy_report", "inspect_time_settings",
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start the UI entry, protecting the existing Recorder history.

    The UI and legacy YAML setups cannot run simultaneously. Verified per-device
    zones and current offsets gate hourly imports; ambiguous data is never
    automatically remapped onto previously recorded history.
    """
    if DOMAIN in hass.data:
        raise ConfigEntryError(
            "Schluter is already running via YAML or another entry; disable the duplicate setup."
        )
    email, password = entry.data.get(CONF_USERNAME), entry.data.get(CONF_PASSWORD)
    if not isinstance(email, str) or not isinstance(password, str):
        raise ConfigEntryAuthFailed("Schluter account credentials are missing.")

    status, account = await hass.async_add_executor_job(
        connect_account, email, password
    )
    if status == "invalid_auth":
        raise ConfigEntryAuthFailed("Schluter credentials were rejected.")
    if status != "ok" or account is None:
        raise ConfigEntryNotReady("Schluter account connection is not available.")

    entry.runtime_data = account
    hass.data[DOMAIN] = {
        DATA_SCHLUTER_API: account.api,
        "device_names_by_serial": account.device_names_by_serial,
        "time_zones": dict(entry.options.get("time_zones", {})),
        "config_entry_id": entry.entry_id,
    }
    forwarded = False
    try:
        await hass.config_entries.async_forward_entry_setups(entry, [Platform.CLIMATE])
        forwarded = True
        await async_setup_energy_statistics_entry(
            hass, account.api, account.device_names_by_serial,
            dict(entry.options.get("time_zones", {})),
        )
    except Exception:
        await async_stop_energy_statistics_entry(hass)
        # A failed energy worker startup must not leave a live climate platform
        # attached to a Config Entry in the retry state.
        if forwarded:
            try:
                await hass.config_entries.async_unload_platforms_or_raise(
                    entry, [Platform.CLIMATE]
                )
            except Exception:
                _LOGGER.warning(
                    "Schluter UI platform cleanup could not complete after setup failure"
                )
        if hass.data.get(DOMAIN, {}).get("config_entry_id") == entry.entry_id:
            for service_name in _UI_SERVICES:
                if hass.services.has_service(DOMAIN, service_name):
                    hass.services.async_remove(DOMAIN, service_name)
            hass.data.pop(DOMAIN, None)
        await hass.async_add_executor_job(close_account, account.api)
        raise

    async def _refresh_ui_session(_now):
        if not await hass.async_add_executor_job(refresh_account, account.api):
            _LOGGER.warning("Schluter UI session refresh was not acknowledged")

    entry.async_on_unload(
        async_track_time_interval(hass, _refresh_ui_session, SESSION_REFRESH_INTERVAL)
    )
    _LOGGER.info(
        "Schluter UI energy statistics will import only for thermostats "
        "with verified timezone, current offset, and compatible history"
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the UI climate platform, services and private API session."""
    await async_stop_energy_statistics_entry(hass)
    await hass.config_entries.async_unload_platforms_or_raise(
        entry, [Platform.CLIMATE]
    )
    runtime = entry.runtime_data
    data = hass.data.get(DOMAIN)
    if isinstance(data, dict) and data.get("config_entry_id") == entry.entry_id:
        # Workers have been stopped above before shared services are removed.
        for service_name in _UI_SERVICES:
            if hass.services.has_service(DOMAIN, service_name):
                hass.services.async_remove(DOMAIN, service_name)
        hass.data.pop(DOMAIN, None)
    await hass.async_add_executor_job(close_account, runtime.api)
    return True
