"""Home Assistant UI configuration and safe reauthentication for Schluter Extended.

Version 1.1.0b2 asks for per-device timezones during first-time setup.
The UI flow blocks legacy YAML duplicates and requires verified timezones.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from zoneinfo import available_timezones

import probatio

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .config_entry_runtime import close_account, connect_account
from .config_flow_auth import check_login
from .config_flow_policy import account_unique_id, timezone_options_for_device
from .const import DOMAIN
from .timezone_rules import TimezoneUnverified


ACCOUNT_SCHEMA = probatio.Schema({
    probatio.Required(CONF_USERNAME): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)
    ),
    probatio.Required(CONF_PASSWORD): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    ),
})

PASSWORD_SCHEMA = probatio.Schema({
    probatio.Required(CONF_PASSWORD): selector.TextSelector(
        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
    ),
})


class SchluterHeatExtendedConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Sign in to one Schluter account; one account may contain many devices."""

    VERSION = 1
    _pending_email: str | None = None
    _pending_password: str | None = None
    _pending_devices: dict[str, str] | None = None
    _pending_zones: dict[str, str] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a UI entry only when a legacy YAML runtime is not active."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if DOMAIN in self.hass.data:
            return self.async_abort(reason="yaml_active")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                login_email = user_input[CONF_USERNAME].strip()
                unique_id = account_unique_id(login_email)
            except (KeyError, AttributeError, ValueError):
                errors["base"] = "invalid_account"
            else:
                result = await self.hass.async_add_executor_job(
                    check_login, login_email, user_input[CONF_PASSWORD]
                )
                if result == "ok":
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                    # Discover devices before creating the entry so timezone
                    # confirmation cannot be skipped on first installation.
                    status, account = await self.hass.async_add_executor_job(
                        connect_account, login_email, user_input[CONF_PASSWORD]
                    )
                    if status == "ok" and account is not None:
                        self._pending_email = login_email
                        self._pending_password = user_input[CONF_PASSWORD]
                        self._pending_devices = dict(account.device_names_by_serial)
                        self._pending_zones = {}
                        await self.hass.async_add_executor_job(close_account, account.api)
                        return await self.async_step_initial_timezone()
                    errors["base"] = (
                        "devices_unavailable" if status == "no_thermostats"
                        else "cannot_connect"
                    )
                else:
                    errors["base"] = result

        return self.async_show_form(
            step_id="user", data_schema=ACCOUNT_SCHEMA, errors=errors
        )

    async def async_step_initial_timezone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require explicit timezone confirmation for every discovered device."""
        devices = self._pending_devices
        zones = self._pending_zones
        if not devices or zones is None or self._pending_email is None:
            return self.async_abort(reason="devices_unavailable")
        remaining = [serial for serial in sorted(devices) if serial not in zones]
        if not remaining:
            return self.async_abort(reason="devices_unavailable")
        serial = remaining[0]
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                updated = timezone_options_for_device(
                    zones, serial, user_input["time_zone"], devices
                )
            except (KeyError, TimezoneUnverified):
                errors["base"] = "invalid_timezone"
            else:
                self._pending_zones = updated
                if len(updated) == len(devices):
                    email, password = self._pending_email, self._pending_password
                    self._pending_password = None
                    self._pending_email = None
                    self._pending_devices = None
                    self._pending_zones = None
                    return self.async_create_entry(
                        title="Schluter Floor Heat Extended",
                        data={CONF_USERNAME: email, CONF_PASSWORD: password},
                        options={"time_zones": updated},
                    )
                return await self.async_step_initial_timezone()
        suggested = self.hass.config.time_zone
        schema = probatio.Schema({
            probatio.Required("time_zone", default=suggested):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(available_timezones()),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
        })
        return self.async_show_form(
            step_id="initial_timezone", data_schema=schema, errors=errors,
            description_placeholders={
                "device_name": devices[serial],
                "device_number": str(len(zones) + 1),
                "device_count": str(len(devices)),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """A fixed-account password reset cannot silently switch accounts."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Revalidate the configured account and update the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            login_email = entry.data[CONF_USERNAME]
            username = account_unique_id(login_email)
            result = await self.hass.async_add_executor_job(
                check_login, login_email, user_input[CONF_PASSWORD]
            )
            if result == "ok":
                await self.async_set_unique_id(username)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
            errors["base"] = result
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=PASSWORD_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SchluterOptionsFlow:
        """Expose per-device timezone options for the active UI config entry."""
        return SchluterOptionsFlow()


class SchluterOptionsFlow(OptionsFlowWithReload):
    """Select a device and explicitly assign its IANA timezone."""

    _serial: str | None = None
    _name: str = ""

    def _devices(self) -> dict[str, str]:
        """Runtime supplies discovered serials and labels; never invent them."""
        data = self.hass.data.get(DOMAIN)
        if type(data) is not dict:
            return {}
        names = data.get("device_names_by_serial")
        if type(names) is not dict:
            return {}
        return {
            serial: str(name)[:80]
            for serial, name in names.items()
            if type(serial) is str and serial.isdigit() and type(name) is str
        }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Present only actually discovered thermostats."""
        devices = self._devices()
        if not devices:
            return self.async_abort(reason="devices_unavailable")
        if user_input is not None:
            serial = user_input.get("thermostat")
            if serial not in devices:
                return self.async_show_form(
                    step_id="init", data_schema=self._picker_schema(devices),
                    errors={"base": "unknown_device"}
                )
            self._serial = serial
            self._name = devices[serial]
            return await self.async_step_timezone()
        return self.async_show_form(
            step_id="init", data_schema=self._picker_schema(devices)
        )

    @staticmethod
    def _picker_schema(devices: dict[str, str]) -> probatio.Schema:
        return probatio.Schema({
            probatio.Required("thermostat"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=serial, label=name)
                        for serial, name in sorted(devices.items())
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        })

    async def async_step_timezone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store exactly one explicitly selected zone; leave other devices alone."""
        devices = self._devices()
        if self._serial not in devices:
            return self.async_abort(reason="devices_unavailable")
        current = self.config_entry.options.get("time_zones", {})
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                zones = timezone_options_for_device(
                    current, self._serial, user_input["time_zone"], devices
                )
            except (KeyError, TimezoneUnverified):
                errors["base"] = "invalid_timezone"
            else:
                return self.async_create_entry(
                    data={**self.config_entry.options, "time_zones": zones}
                )
        # The HA zone is a *suggestion only*. Importing new hourly energy
        # requires an explicitly saved zone for each thermostat.
        suggested = current.get(self._serial, self.hass.config.time_zone)
        schema = probatio.Schema({
            probatio.Required("time_zone", default=suggested):
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(available_timezones()),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
        })
        return self.async_show_form(
            step_id="timezone", data_schema=schema, errors=errors,
            description_placeholders={"device_name": self._name}
        )
