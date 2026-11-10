"""Support for Schluter thermostats."""

# Modified from Home Assistant Core's Apache-2.0 Schluter climate platform.
# Changes: schedule services, temporary overrides, energy queries, and extended attributes.
# Original: https://github.com/home-assistant/core/tree/dev/homeassistant/components/schluter


from __future__ import annotations

import asyncio
import logging
import math
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from requests import RequestException
import voluptuous as vol
import probatio

from homeassistant.components.climate import (
    PLATFORM_SCHEMA as CLIMATE_PLATFORM_SCHEMA,
    SCAN_INTERVAL,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE, CONF_SCAN_INTERVAL, UnitOfTemperature
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import TemperatureConverter
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

# Use relative imports to ensure we use your local v_lib
from . import DATA_SCHLUTER_API, DOMAIN
from .energy import async_get_energy_report
from .time_settings_diagnostic import inspect_time_settings, inspect_locations_time_settings
from .timezone_rules import (
    TimezoneUnverified, configured_device_zone, check_thermostat_offset,
)
from .v_lib.api import (
    API_GET_LOCATIONS_URL, ReturnToScheduleError, TemperatureSetError, comfort_end_time,
    ScheduleWriteError, _schedule_structure, compare_schedules, validate_schedule_update,
)

_LOGGER = logging.getLogger(__name__)

def _temporary_temperature(value):
    """Validate a finite temperature in Home Assistant display units."""
    try:
        if isinstance(value, bool):
            raise ValueError
        temperature = float(value)
        if not math.isfinite(temperature):
            raise ValueError
        return temperature
    except (TypeError, ValueError, OverflowError):
        raise probatio.Invalid("Temperature must be a finite number") from None


def _temporary_expiration(value):
    """Require an explicit timezone, future instant, and whole-minute precision."""
    try:
        expiry = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(expiry, datetime) or expiry.tzinfo is None:
            raise ValueError
        if expiry.utcoffset() is None:
            raise ValueError
        utc_expiry = expiry.astimezone(UTC)
        # Reject nonexistent local datetimes supplied with an IANA tzinfo.
        if (
            utc_expiry.astimezone(expiry.tzinfo).replace(tzinfo=None)
            != expiry.replace(tzinfo=None)
        ):
            raise ValueError
        if utc_expiry <= dt_util.utcnow() or utc_expiry.second or utc_expiry.microsecond:
            raise ValueError
        return utc_expiry
    except (TypeError, ValueError, OverflowError):
        raise probatio.Invalid(
            "expires_at must be a future ISO 8601 timestamp with an explicit "
            "UTC offset or Z, aligned to a whole minute"
        ) from None


def _single_temporary_target(data):
    """Allow one explicit climate entity, without group or area expansion."""
    entity_ids = data.get(ATTR_ENTITY_ID)
    if (
        not isinstance(entity_ids, list) or len(entity_ids) != 1
        or not isinstance(entity_ids[0], str)
        or not entity_ids[0].startswith("climate.")
        or any(key in data for key in ("device_id", "area_id", "floor_id", "label_id"))
    ):
        raise probatio.Invalid("Target exactly one Schluter climate entity by entity_id")
    return data


def _register_temporary_temperature_service(hass):
    """Use HA's platform dispatcher for target permissions and entity routing."""
    if hass.services.has_service(DOMAIN, "set_temporary_temperature"):
        return
    schema = probatio.All(
        cv.make_entity_service_schema({
            probatio.Required(ATTR_TEMPERATURE): _temporary_temperature,
            probatio.Required("expires_at"): _temporary_expiration,
        }),
        _single_temporary_target,
    )
    service.async_register_platform_entity_service(
        hass, DOMAIN, "set_temporary_temperature",
        entity_domain="climate",
        schema=schema,
        func="async_set_temporary_temperature",
        required_features=[ClimateEntityFeature.TARGET_TEMPERATURE],
    )


def _register_return_to_schedule_service(hass):
    """Register one-entity Return to Schedule through HA's platform dispatcher."""
    if hass.services.has_service(DOMAIN, "return_to_schedule"):
        return
    service.async_register_platform_entity_service(
        hass, DOMAIN, "return_to_schedule",
        entity_domain="climate",
        schema=probatio.All(
            cv.make_entity_service_schema({}), _single_temporary_target
        ),
        func="async_return_to_schedule",
        required_features=[ClimateEntityFeature.TARGET_TEMPERATURE],
    )


def _reported_override_expiration(value):
    """Read Schluter's day-first server timestamp; absent offsets mean UTC."""
    if not isinstance(value, str):
        return None
    for fmt in (
        "%d/%m/%Y %H:%M:%S %z", "%d/%m/%Y %H:%M %z",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
    ):
        try:
            expiry = datetime.strptime(value, fmt)
            return expiry.replace(tzinfo=UTC) if expiry.tzinfo is None else expiry.astimezone(UTC)
        except (ValueError, OverflowError):
            continue
    return None


def _register_get_schedule_service(hass):
    """Return one entity's complete weekly schedule through HA's dispatcher."""
    if hass.services.has_service(DOMAIN, "get_schedule"):
        return
    service.async_register_platform_entity_service(
        hass, DOMAIN, "get_schedule",
        entity_domain="climate",
        schema=probatio.All(
            cv.make_entity_service_schema({}), _single_temporary_target
        ),
        func="async_get_schedule",
        supports_response=SupportsResponse.ONLY,
    )


def _schedule_argument(value):
    if not isinstance(value, list):
        raise probatio.Invalid("Provide a complete weekly schedule array")
    return deepcopy(value)


def _register_save_schedule_service(hass):
    """Register the validated writer for one selected thermostat."""
    if hass.services.has_service(DOMAIN, "save_schedule"):
        return
    service.async_register_platform_entity_service(
        hass, DOMAIN, "save_schedule", entity_domain="climate",
        schema=probatio.All(cv.make_entity_service_schema({
            probatio.Required("schedules"): _schedule_argument,
            probatio.Required("original_schedules"): _schedule_argument,
        }), _single_temporary_target),
        func="async_save_schedule", supports_response=SupportsResponse.ONLY,
    )


def _register_get_energy_report_service(hass):
    """Read-only energy reports through the existing one-entity dispatcher."""
    if hass.services.has_service(DOMAIN, "get_energy_report"):
        return
    service.async_register_platform_entity_service(
        hass, DOMAIN, "get_energy_report", entity_domain="climate",
        schema=probatio.All(cv.make_entity_service_schema({
            probatio.Required("view"): probatio.In(("day", "week", "month", "year")),
            probatio.Required("date"): cv.string,
            probatio.Optional("refresh", default=False): cv.boolean,
        }), _single_temporary_target),
        func="async_get_energy_report", supports_response=SupportsResponse.ONLY,
    )


def _register_inspect_time_settings_service(hass):
    """Expose only cached clock-field names/types for a selected device."""
    if hass.services.has_service(DOMAIN, "inspect_time_settings"):
        return
    service.async_register_platform_entity_service(
        hass, DOMAIN, "inspect_time_settings", entity_domain="climate",
        schema=probatio.All(
            cv.make_entity_service_schema({
                probatio.Optional("include_locations", default=False): cv.boolean,
            }), _single_temporary_target
        ),
        func="async_inspect_time_settings", supports_response=SupportsResponse.ONLY,
    )


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Schluter thermostats."""
    # Note: Removed DATA_SCHLUTER_SESSION as the api object now manages it
    api = hass.data[DOMAIN][DATA_SCHLUTER_API]
    _register_temporary_temperature_service(hass)
    _register_return_to_schedule_service(hass)
    _register_get_schedule_service(hass)
    _register_save_schedule_service(hass)
    _register_get_energy_report_service(hass)
    _register_inspect_time_settings_service(hass)

    async def async_update_data():
        try:
            # UPDATED: We no longer pass session_id here
            thermostats = await hass.async_add_executor_job(api.get_thermostats)
        except RequestException as err:
            raise UpdateFailed(f"Error communicating with Schluter API: {err}") from err

        if thermostats is None:
            _LOGGER.debug("No thermostats found in Schluter response")
            return {}

        # Assumes the API returns a list of thermostat objects with a .serial_number attribute
        return {thermo.serial_number: thermo for thermo in thermostats}

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="schluter_heat_extended",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_refresh()

    if not coordinator.data:
        _LOGGER.error("No Schluter thermostats were discovered")
        if discovery_info and discovery_info.get("config_entry"):
            # A UI entry must not be marked loaded without its thermostats.
            from homeassistant.exceptions import ConfigEntryNotReady
            raise ConfigEntryNotReady("No Schluter thermostats are currently available.")
        return

    async_add_entities(
        SchluterThermostat(coordinator, serial_number, api)
        for serial_number in coordinator.data
    )


async def async_setup_entry(hass, entry, async_add_entities):
    """Use the same stable entity IDs and platform actions as legacy YAML."""
    await async_setup_platform(
        hass, {}, async_add_entities, discovery_info={"config_entry": True}
    )


class SchluterThermostat(CoordinatorEntity, ClimateEntity):
    """Representation of a Schluter Thermostat."""

    def __init__(self, coordinator, serial_number, api):
        """Initialize the thermostat."""
        super().__init__(coordinator)
        self._serial_number = serial_number
        self._api = api
        
        # Pull initial name safely
        data = self.coordinator.data.get(self._serial_number)
        self._attr_name = data.name if data else f"Schluter {serial_number}"
        self._attr_unique_id = f"schluter_heat_extended_{serial_number}"

    @property
    def thermostat_data(self):
        """Helper to get the current data for this specific serial."""
        return self.coordinator.data.get(self._serial_number)

    def _device_timezone(self):
        """Use explicit UI zone or preserve legacy YAML schedule behavior."""
        runtime = self.hass.data.get(DOMAIN, {})
        if type(runtime) is dict and runtime.get("config_entry_id"):
            try:
                return configured_device_zone(
                    runtime.get("time_zones", {}), str(self._serial_number)
                )
            except TimezoneUnverified:
                raise HomeAssistantError(
                    "Select a timezone for this thermostat in Configure before using time-dependent actions."
                ) from None
        return self.hass.config.time_zone

    async def async_get_energy_report(self, view, date, refresh=False):
        """Read cached energy without refreshing or controlling the thermostat."""
        zone = self._device_timezone()
        runtime = self.hass.data.get(DOMAIN, {})
        if type(runtime) is dict and runtime.get("config_entry_id"):
            # Hourly display is timezone-sensitive. Validate against current
            # cached device data; Recorder imports additionally require a
            # fresh network offset check every poll.
            try:
                raw = getattr(self.thermostat_data, "data", None)
                check_thermostat_offset(zone, raw.get("TZOffset")
                                        if type(raw) is dict else None, datetime.now(UTC))
            except TimezoneUnverified:
                raise HomeAssistantError(
                    "Thermostat UTC offset does not match the configured timezone; energy report not shown."
                ) from None
        return await async_get_energy_report(
            self.hass, self._api, self._serial_number, view, date, refresh,
            zone=zone if type(runtime) is dict and runtime.get("config_entry_id") else None,
        )

    async def async_inspect_time_settings(self, include_locations=False):
        """Read-only clock field inspection; optional sanitized locations GET."""
        raw = getattr(self.thermostat_data, "data", None)
        if not self.coordinator.last_update_success or not isinstance(raw, dict):
            raise HomeAssistantError(
                "Current Schluter thermostat data is unavailable; no diagnostic was run."
            )
        result = inspect_time_settings(raw)
        if not include_locations:
            return result
        # Location response is sanitized immediately; raw account data is never
        # returned, stored, logged, or attached to the climate entity.
        # Report only bounded transport/envelope diagnostics, never a response
        # body, exception string, token, location ID, or provider data value.
        try:
            response = await self.hass.async_add_executor_job(
                self._api._call_api, "get", API_GET_LOCATIONS_URL
            )
        except Exception:
            result["locations"] = {
                **inspect_locations_time_settings(None),
                "request_result": "transport_error",
            }
            return result
        if response is None:
            result["locations"] = {
                **inspect_locations_time_settings(None),
                "request_result": "no_response",
            }
            return result
        code = getattr(response, "status_code", None)
        if type(code) is not int or not 100 <= code <= 599:
            result["locations"] = {
                **inspect_locations_time_settings(None),
                "request_result": "invalid_http_status",
            }
            return result
        if not 200 <= code < 300:
            result["locations"] = {
                **inspect_locations_time_settings(None),
                "request_result": "http_non_success",
                "http_status": code,
            }
            return result
        try:
            payload = response.json()
        except Exception:
            result["locations"] = {
                **inspect_locations_time_settings(None),
                "request_result": "non_json_response",
                "http_status": code,
            }
            return result
        inspected = inspect_locations_time_settings(payload)
        if inspected["status"] == "unavailable":
            inspected["request_result"] = "unexpected_json_envelope"
            inspected["payload_type"] = (
                "object" if type(payload) is dict
                else "array" if type(payload) is list
                else "other"
            )
        else:
            inspected["request_result"] = "ok"
        inspected["http_status"] = code
        result["locations"] = inspected
        return result

    async def async_get_schedule(self):
        """Read the refreshed schedule without using any schedule-writing API."""
        try:
            await self.coordinator.async_request_refresh()
        except Exception:
            raise HomeAssistantError("Could not refresh the Schluter weekly schedule.") from None
        if not self.coordinator.last_update_success or not self.coordinator.data:
            raise HomeAssistantError("The Schluter weekly schedule is unavailable.")
        data = getattr(self.thermostat_data, "data", None)
        schedules = data.get("Schedules") if isinstance(data, dict) else None
        valid = isinstance(schedules, list) and len(schedules) == 7
        if valid:
            days = []
            for day in schedules:
                if not isinstance(day, dict) or type(day.get("WeekDayGrpNo")) is not int:
                    valid = False
                    break
                days.append(day["WeekDayGrpNo"])
                events = day.get("Events")
                if not isinstance(events, list) or len(events) != 6:
                    valid = False
                    break
                identifiers = []
                for event in events:
                    if not isinstance(event, dict):
                        valid = False
                        break
                    value = event.get("TempFloor")
                    clock_value = event.get("Clock")
                    try:
                        valid_temperature = type(value) in (int, float) and math.isfinite(value)
                    except OverflowError:
                        valid_temperature = False
                    if (
                        type(event.get("ScheduleType")) is not int
                        or type(event.get("Active")) is not bool
                        or not valid_temperature
                        or not isinstance(clock_value, str)
                    ):
                        valid = False
                        break
                    try:
                        parsed = datetime.strptime(clock_value, "%H:%M:%S")
                        parts = clock_value.split(":")
                        if (len(parts) != 3 or not 1 <= len(parts[0]) <= 2
                                or len(parts[1]) != 2 or len(parts[2]) != 2 or parsed.microsecond):
                            valid = False
                    except ValueError:
                        valid = False
                    identifiers.append(event["ScheduleType"])
                if not valid or sorted(identifiers) != list(range(6)):
                    valid = False
                    break
            valid = valid and sorted(days) == list(range(1, 8))
        if not valid:
            raise HomeAssistantError("Schluter did not return a complete seven-day, six-event weekly schedule.")
        limits = None
        try:
            minimum, maximum = data.get("MinTemp"), data.get("MaxTemp")
            if (type(minimum) not in (int, float) or type(maximum) not in (int, float)
                    or not math.isfinite(minimum) or not math.isfinite(maximum)
                    or minimum >= maximum):
                raise ValueError
            time_limits = await self.hass.async_add_executor_job(
                self._api.get_schedule_limits, self._serial_number
            )
            limits = {"MinTemp": minimum, "MaxTemp": maximum, **time_limits}
        except Exception:
            # Keep the viewer usable, but fail closed for editing without limits.
            _LOGGER.warning("Schluter schedule editing limits are unavailable; showing read-only data.")
        # Export only schedule data/limits; full thermostat objects contain account details.
        return {"Schedules": deepcopy(schedules), "time_zone": str(self._device_timezone()),
                "limits": limits}

    async def _async_schedule_save_refresh(self):
        """Force a server read instead of accepting a debounced cached refresh."""
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success or not self.coordinator.data:
            raise HomeAssistantError("Could not read the latest Schluter schedule. No schedule was sent.")
        data = getattr(self.thermostat_data, "data", None)
        if not isinstance(data, dict):
            raise HomeAssistantError("Could not read the latest Schluter schedule. No schedule was sent.")
        snapshot = deepcopy(data.get("Schedules"))
        _schedule_structure(snapshot)
        return snapshot, data

    async def async_save_schedule(self, schedules, original_schedules):
        """Compare, validate, POST once, then independently verify persistence."""
        if not self._serial_number:
            raise ServiceValidationError(
                "Cannot save schedule: thermostat serial number is missing."
            )
        if getattr(self, "_schedule_save_in_progress", False):
            raise HomeAssistantError("A Schluter schedule save is already in progress.")
        self._schedule_save_in_progress = True
        try:
            proposed, original = deepcopy(schedules), deepcopy(original_schedules)
            _schedule_structure(original)
            _schedule_structure(proposed)
            try:
                async with asyncio.timeout(30):
                    time_limits = await self.hass.async_add_executor_job(
                        self._api.get_schedule_limits, self._serial_number
                    )
                    latest, data = await self._async_schedule_save_refresh()
            except Exception:
                raise HomeAssistantError("Could not read the latest Schluter schedule and limits. No schedule was sent.") from None
            if not compare_schedules(original, latest):
                raise HomeAssistantError("The schedule changed elsewhere. Close and reopen the editor before saving; your draft has been retained.")
            limits = {"MinTemp": data.get("MinTemp"), "MaxTemp": data.get("MaxTemp"), **time_limits}
            validate_schedule_update(original, proposed, limits)
            if compare_schedules(proposed, latest):
                return {"status": "unchanged", "acknowledged": False, "normalized": False,
                        "Schedules": latest}
            try:
                async with asyncio.timeout(25):
                    result = await self.hass.async_add_executor_job(
                        self._api.save_weekly_schedule, self._serial_number, proposed
                    )
            except TimeoutError:
                result = {"acknowledged": False, "delivery_uncertain": True}
            acknowledged = isinstance(result, dict) and result.get("acknowledged") is True
            reason = "readback_failed"
            try:
                async with asyncio.timeout(60):
                    # Read-only retries handle cloud lag; the POST is never retried.
                    for delay in (0, 3, 7, 10):
                        if delay:
                            await asyncio.sleep(delay)
                        try:
                            reported, _ = await self._async_schedule_save_refresh()
                        except Exception:
                            continue
                        match = compare_schedules(proposed, reported)
                        if match:
                            return {"status": "confirmed", "acknowledged": acknowledged,
                                    "normalized": match == "normalized", "Schedules": reported}
                        reason = "pending" if compare_schedules(original, reported) else "different_values"
            except TimeoutError:
                pass
            return {"status": "unconfirmed", "acknowledged": acknowledged,
                    "reason": reason, "Schedules": None}
        except ScheduleWriteError as err:
            raise HomeAssistantError(str(err)) from None
        except HomeAssistantError:
            raise
        except Exception:
            raise HomeAssistantError("The schedule save could not be completed. Your draft is retained.") from None
        finally:
            self._schedule_save_in_progress = False

    def _get_val(self, prop_name, dict_key, default_val):
        """Find data by checking properties first, then the raw data dict."""
        if not self.thermostat_data:
            return default_val
        
        # 1. Try to get it as an attribute (e.g., thermostat.temperature)
        val = getattr(self.thermostat_data, prop_name, None)
        if val is not None:
            return val
            
        # 2. Try to get it from the internal .data dictionary
        # This matches the "CurrentTemp" key from your web portal trace
        if hasattr(self.thermostat_data, "data"):
            val = self.thermostat_data.data.get(dict_key)
            if val is not None:
                return val
                
        return default_val

    @property
    def extra_state_attributes(self):
        """Describe the reported override and next transition from refreshed data."""
        attributes = {
            "override_active": None,
            "override_until": None,
            "next_scheduled_change": None,
            "next_scheduled_temperature": None,
            "next_scheduled_temperature_unit": UnitOfTemperature.CELSIUS,
        }
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return attributes
        data = getattr(self.thermostat_data, "data", None)
        if not isinstance(data, dict):
            return attributes
        mode = data.get("RegulationMode")
        if type(mode) is not int or mode not in (1, 2, 3, 4):
            return attributes
        try:
            transition = comfort_end_time(
                thermostat=data, time_zone=str(self._device_timezone()),
                return_transition=True,
            )
            next_change = _reported_override_expiration(transition["end_time"])
            if next_change is not None:
                attributes["next_scheduled_change"] = next_change.isoformat()
                attributes["next_scheduled_temperature"] = transition["temperature_celsius"]
        except (TemperatureSetError, AttributeError, KeyError, TypeError, HomeAssistantError):
            # A display calculation must not hide the existing override state.
            pass
        if mode != 2:
            attributes["override_active"] = False
            return attributes
        expiry = _reported_override_expiration(data.get("ComfortEndTime"))
        if expiry is None:
            return attributes
        attributes["override_active"] = expiry > dt_util.utcnow()
        attributes["override_until"] = expiry.isoformat()
        return attributes

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        if not self.thermostat_data:
            return None
        
        # Get 'temperature' from thermostat.py or 'CurrentTemp' from raw data
        temp = getattr(self.thermostat_data, "temperature", None)
        if temp is None and hasattr(self.thermostat_data, "data"):
            temp = self.thermostat_data.data.get("CurrentTemp")

        if temp is not None:
            # 2250 / 100 = 22.5
            return float(temp) / 100
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        if not self.thermostat_data:
            return None
            
        temp = getattr(self.thermostat_data, "set_point_temp", None)
        if temp is None and hasattr(self.thermostat_data, "data"):
            temp = self.thermostat_data.data.get("SetPointTemp")
        
        if temp is not None:
            return float(temp) / 100
        return None

    @property
    def min_temp(self) -> float:
        """Return 5.0 (from 500)."""
        # We use 500 as the default if the attribute is missing
        temp = getattr(self.thermostat_data, "min_temp", 500)
        return float(temp) / 100

    @property
    def max_temp(self) -> float:
        """Return 40.0 (from 4000)."""
        # We use 4000 as the default if the attribute is missing
        temp = getattr(self.thermostat_data, "max_temp", 4000)
        return float(temp) / 100    
        
    @property
    def hvac_mode(self):
        """Return hvac operation mode."""
        is_on = self._get_val("is_online", "Online", True)
        return HVACMode.HEAT if is_on else HVACMode.OFF

    @property
    def hvac_modes(self):
        """Return the list of available hvac operation modes."""
        return [HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_action(self):
        """Return current operation (heating or idle)."""
        is_heating = self._get_val("is_heating", "Heating", False)
        return HVACAction.HEATING if is_heating else HVACAction.IDLE

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return ClimateEntityFeature.TARGET_TEMPERATURE

    @property
    def temperature_unit(self) -> str:
        """Return Celsius because the API sends 2250 (22.5C)."""
        # Home Assistant will auto-convert this to F on your dashboard
        return UnitOfTemperature.CELSIUS

    async def async_return_to_schedule(self):
        """Resume schedule and let refreshed server data describe the result."""
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                "Cannot return to schedule: current thermostat data is unavailable."
            )
        data = getattr(self.thermostat_data, "data", None)
        mode = data.get("RegulationMode") if isinstance(data, dict) else None
        if type(mode) is not int or mode not in (1, 2, 3, 4):
            raise HomeAssistantError(
                "Cannot return to schedule: the thermostat operating mode is unknown."
            )
        vacation_enabled = False if mode == 4 else data.get("VacationEnabled")
        if type(vacation_enabled) is not bool:
            raise HomeAssistantError(
                "Cannot return to schedule: the thermostat vacation setting is unknown."
            )
        try:
            result = await self.hass.async_add_executor_job(
                self._api.return_to_schedule, self._serial_number, vacation_enabled
            )
        except ReturnToScheduleError as err:
            raise HomeAssistantError(str(err)) from None
        except Exception:
            raise HomeAssistantError(
                "The Schluter return-to-schedule request could not be completed."
            ) from None

        acknowledged = isinstance(result, dict) and result.get("Success") is True
        refresh_ok = False
        try:
            await self.coordinator.async_request_refresh()
            refresh_ok = self.coordinator.last_update_success
        except Exception:
            _LOGGER.warning("Could not refresh Schluter data after returning to schedule")

        refreshed = getattr(self.thermostat_data, "data", None) if refresh_ok else None
        scheduled = (
            isinstance(refreshed, dict)
            and type(refreshed.get("RegulationMode")) is int
            and refreshed["RegulationMode"] == 1
        )
        if not scheduled:
            if acknowledged:
                _LOGGER.warning(
                    "Schluter acknowledged Return to Schedule, but refreshed "
                    "schedule mode is not yet confirmed; cloud updates may be delayed"
                )
            else:
                raise HomeAssistantError(
                    "Return to Schedule was delivered, but neither an API "
                    "acknowledgement nor refreshed schedule mode confirmed it. "
                    "The result is unconfirmed; check the thermostat before retrying."
                )

    async def async_set_temporary_temperature(self, temperature, expires_at):
        """Convert display units once, then reuse the ordinary native setter."""
        try:
            display_temperature = _temporary_temperature(temperature)
            expiry = _temporary_expiration(expires_at)
        except probatio.Invalid as err:
            raise ServiceValidationError(str(err)) from None
        native_temperature = TemperatureConverter.convert(
            display_temperature,
            self.hass.config.units.temperature_unit,
            self.temperature_unit,
        )
        await self.async_set_temperature(
            temperature=native_temperature, end_time=expiry
        )

    async def async_set_temperature(self, **kwargs):
        """Set new target temperature."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        # Support optional duration or explicit end_time if callers provide them
        duration = kwargs.get('duration_minutes')
        end_time = kwargs.get('end_time')

        if temp is None:
            return

        try:
            # Home Assistant supplies temperatures in the entity's native Celsius units.
            temp_c = float(temp)

            # Clamp to device-reported min/max (these are in Celsius).
            min_c = self.min_temp
            max_c = self.max_temp
            clamped_c = max(min_c, min(max_c, temp_c))

            if abs(clamped_c - temp_c) > 1e-6:
                _LOGGER.warning(
                    'Requested temperature %s C out of range (%s-%s C); clamping to %s C',
                    temp_c,
                    min_c,
                    max_c,
                    clamped_c,
                )
            if end_time is None and duration is None:
                end_time = comfort_end_time(
                    thermostat=getattr(self.thermostat_data, "data", None),
                    time_zone=str(self._device_timezone()),
                )
            result = await self.hass.async_add_executor_job(
                self._api.set_temperature, self._serial_number, float(clamped_c), duration, end_time
            )
            acknowledged = (
                isinstance(result, dict) and result.get("Success") is True
            )
        except TemperatureSetError as err:
            raise HomeAssistantError(str(err)) from None
        except HomeAssistantError:
            raise
        except Exception:
            raise HomeAssistantError(
                "Failed to set the Schluter target temperature. "
                "Check connectivity, authentication, and the thermostat's availability."
            ) from None
        finally:
            refresh_ok = False
            try:
                await self.coordinator.async_request_refresh()
                refresh_ok = self.coordinator.last_update_success
            except Exception:
                _LOGGER.warning(
                    "Could not refresh Schluter data after the temperature-setting attempt."
                )

        matches = False
        if refresh_ok and self.thermostat_data is not None:
            try:
                actual_c = self.target_temperature
                expected_c = round(clamped_c * 100) / 100
                matches = (
                    actual_c is not None
                    and abs(actual_c - expected_c) <= 0.011
                )
            except (TypeError, ValueError):
                pass

        if acknowledged:
            if not refresh_ok or self.thermostat_data is None:
                _LOGGER.warning(
                    "Schluter acknowledged the temperature request, but the "
                    "resulting setpoint could not be refreshed."
                )
            elif not matches:
                _LOGGER.warning(
                    "Schluter acknowledged the temperature request, but the "
                    "refreshed target does not yet match; cloud updates may be delayed."
                )
        elif not matches:
            raise HomeAssistantError(
                "The temperature request was delivered, but Schluter did not "
                "acknowledge success and a refreshed matching setpoint could "
                "not be confirmed. The result is unconfirmed, not a confirmed "
                "rejection; check the thermostat before retrying."
            )
        else:
            _LOGGER.warning(
                "Schluter returned an unrecognized temperature acknowledgement; "
                "the refreshed target matches the requested temperature."
            )

    async def async_set_hvac_mode(self, hvac_mode):
        """Set new target hvac mode."""
        pass


