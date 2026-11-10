# Modified from Adam Michaleski's MIT-licensed py-schluter API implementation.
# Changes: extended cloud API, energy, schedule, authentication, and error handling.
# Original: https://github.com/prairieapps/py-schluter

import logging
import math
import json
import datetime
import re
import threading
from copy import deepcopy
from typing import Optional
from zoneinfo import ZoneInfo

# requests is commonly available in Home Assistant, but in some dev
# environments or test runners it may be missing. Guard the import so the
# integration module can still be imported; runtime network calls will
# produce a clear error if requests is not present.
try:
    import requests
    from requests.exceptions import RequestException
    _HAS_REQUESTS = True
except Exception:  # pragma: no cover - environments vary
    requests = None
    RequestException = Exception
    _HAS_REQUESTS = False
from .thermostat import Thermostat

API_BASE_URL = "https://ditra-heat-e-wifi.schluter.com"
API_GET_THERMOSTATS_URL = API_BASE_URL + "/api/thermostats"
API_GET_DEFAULTS_URL = API_BASE_URL + "/api/defaults"
API_GET_LOCATIONS_URL = API_BASE_URL + "/api/user/locations"
API_AUTH_URL = API_BASE_URL + "/api/authenticate/user"
API_APPLICATION_ID = 7
API_GET_ENERGY_URL = API_BASE_URL + "/api/energyusage"

_LOGGER = logging.getLogger(__name__)




class ReturnToScheduleError(Exception):
    """A return-to-schedule request failed or could not be sent."""


class TemperatureSetError(Exception):
    """A temperature-setting request failed."""


class ScheduleReadError(Exception):
    """Read-only schedule validation metadata could not be retrieved."""


class EnergyReadError(Exception):
    """An energy report could not be read; messages contain no request details."""


class ScheduleWriteError(Exception):
    """A schedule save failed validation or was explicitly rejected."""


def _schedule_structure(schedules):
    """Validate a complete JSON schedule without normalizing its original values."""
    try:
        json.dumps(schedules, allow_nan=False)
        if not isinstance(schedules, list) or len(schedules) != 7:
            raise ValueError
        days = []
        for day in schedules:
            if not isinstance(day, dict) or type(day.get("WeekDayGrpNo")) is not int:
                raise ValueError
            days.append(day["WeekDayGrpNo"])
            events = day.get("Events")
            if not isinstance(events, list) or len(events) != 6:
                raise ValueError
            slots = []
            for event in events:
                if (not isinstance(event, dict) or type(event.get("ScheduleType")) is not int
                        or type(event.get("Active")) is not bool
                        or type(event.get("TempFloor")) not in (int, float)
                        or not math.isfinite(event["TempFloor"])):
                    raise ValueError
                slots.append(event["ScheduleType"])
                _schedule_clock(event.get("Clock"))
            if sorted(slots) != list(range(6)):
                raise ValueError
        if sorted(days) != list(range(1, 8)):
            raise ValueError
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ScheduleWriteError("A complete seven-day, six-event schedule with valid values is required.") from None


def _schedule_clock(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:[0-5]\d:[0-5]\d", value):
        raise ValueError
    hour, minute, second = map(int, value.split(":"))
    if hour > 23:
        raise ValueError
    return hour * 3600 + minute * 60 + second


def compare_schedules(expected, reported):
    """Return exact/normalized only for equivalent complete schedules; never round."""
    try:
        _schedule_structure(expected)
        _schedule_structure(reported)
        if json.dumps(expected, sort_keys=True) == json.dumps(reported, sort_keys=True):
            return "exact"
        copies = [deepcopy(expected), deepcopy(reported)]
        for schedules in copies:
            for day in schedules:
                for event in day["Events"]:
                    event["Clock"] = _schedule_clock(event["Clock"])
        for expected_day, reported_day in zip(*copies):
            for expected_event, reported_event in zip(expected_day["Events"], reported_day["Events"]):
                if expected_event["TempFloor"] != reported_event["TempFloor"]:
                    return None
                expected_event["TempFloor"] = reported_event["TempFloor"] = 0
        # Padding and JSON numeric representation may normalize, never values/order.
        return "normalized" if json.dumps(copies[0], sort_keys=True) == json.dumps(copies[1], sort_keys=True) else None
    except (ScheduleWriteError, TypeError, ValueError, OverflowError):
        return None


def validate_schedule_update(original, proposed, limits):
    """Validate the complete draft against actual device bounds and preserve extras."""
    _schedule_structure(original)
    _schedule_structure(proposed)
    try:
        minimum, maximum = limits["MinTemp"], limits["MaxTemp"]
        if (type(minimum) not in (int, float) or type(maximum) not in (int, float)
                or not math.isfinite(minimum) or not math.isfinite(maximum) or minimum >= maximum):
            raise ValueError
        bounds = {}
        for key in ("MinTimeLimits", "MaxTimeLimits"):
            values = limits[key]
            if (not isinstance(values, list) or len(values) != 6
                    or any(not isinstance(v, dict) or type(v.get("ScheduleType")) is not int
                           or type(v.get("Clock")) is not int or v["Clock"] < 0 for v in values)
                    or sorted(v["ScheduleType"] for v in values) != list(range(6))):
                raise ValueError
            bounds[key] = {v["ScheduleType"]: v["Clock"] for v in values}
        if any(bounds["MinTimeLimits"][i] > bounds["MaxTimeLimits"][i] for i in range(6)):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ScheduleWriteError("Schluter schedule validation limits are unavailable. No schedule was sent.") from None
    for old_day, day in zip(original, proposed):
        if (json.dumps({k: v for k, v in old_day.items() if k != "Events"}, sort_keys=True)
                != json.dumps({k: v for k, v in day.items() if k != "Events"}, sort_keys=True)):
            raise ScheduleWriteError("Schedule day identifiers, ordering and unknown fields must be preserved.")
        for old, event in zip(old_day["Events"], day["Events"]):
            editable = {"Clock", "TempFloor", "Active"}
            if (json.dumps({k: v for k, v in old.items() if k not in editable}, sort_keys=True)
                    != json.dumps({k: v for k, v in event.items() if k not in editable}, sort_keys=True)):
                raise ScheduleWriteError("Schedule event identifiers, ordering and unknown fields must be preserved.")
            if _schedule_clock(old["Clock"]) != _schedule_clock(event["Clock"]) and _schedule_clock(event["Clock"]) % 900:
                raise ScheduleWriteError("Edited schedule times must use 15-minute increments.")
            if not minimum <= event["TempFloor"] <= maximum:
                raise ScheduleWriteError("A schedule temperature is outside the thermostat limits.")
        previous = None
        for event in sorted(day["Events"], key=lambda e: e["ScheduleType"]):
            slot = event["ScheduleType"]
            if slot == 0 and not event["Active"]:
                raise ScheduleWriteError("Event 1 must remain active on every day.")
            if not event["Active"]:
                continue
            seconds = _schedule_clock(event["Clock"])
            if previous is not None and seconds < previous:
                seconds += 86400
            if not bounds["MinTimeLimits"][slot] <= seconds <= bounds["MaxTimeLimits"][slot]:
                raise ScheduleWriteError("An active event time is outside Schluter's limits for that event.")
            if previous is not None and seconds < previous + 900:
                raise ScheduleWriteError("Active schedule events must be at least 15 minutes apart.")
            previous = seconds


def comfort_end_time(
    *, thermostat=None, time_zone=None, end_time=None, duration_minutes=None, now=None,
    return_transition=False,
):
    """Return the UTC expiry, optionally paired with its schedule event's Celsius target."""
    utc = datetime.timezone.utc
    now = now or datetime.datetime.now(utc)
    scheduled_temperature = None
    try:
        if now.tzinfo is None:
            raise ValueError
        now = now.astimezone(utc)
        if end_time is not None:
            expiry = end_time
            if isinstance(expiry, str):
                for fmt in (
                    "%d/%m/%Y %H:%M:%S %z", "%d/%m/%Y %H:%M %z",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                ):
                    try:
                        expiry = datetime.datetime.strptime(end_time, fmt)
                        break
                    except ValueError:
                        pass
                else:
                    expiry = datetime.datetime.fromisoformat(end_time)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=utc)
        elif duration_minutes is not None:
            minutes = int(duration_minutes)
            if isinstance(duration_minutes, bool) or minutes <= 0:
                raise ValueError
            expiry = (now + datetime.timedelta(minutes=minutes)).replace(
                second=0, microsecond=0
            )
        else:
            zone = ZoneInfo(time_zone)
            local_now = now.astimezone(zone)
            reported_offset = datetime.datetime.strptime(
                thermostat["TZOffset"], "%z"
            ).utcoffset()
            if local_now.utcoffset() != reported_offset:
                raise TemperatureSetError(
                    "Thermostat offset does not match Home Assistant's timezone; "
                    "cannot determine a safe comfort expiration."
                )
            schedules = thermostat["Schedules"]
            if len(schedules) != 7 or any(
                day["WeekDayGrpNo"] != index + 1
                or not isinstance(day["Events"], list) or len(day["Events"]) != 6
                for index, day in enumerate(schedules)
            ):
                raise ValueError
            walls = {}
            for offset in range(-1, 8):
                day = local_now.date() + datetime.timedelta(days=offset)
                previous = None
                for index, event in enumerate(schedules[day.weekday()]["Events"]):
                    if event["Active"] is not True:
                        continue
                    clock = datetime.time.fromisoformat(event["Clock"])
                    if clock.tzinfo is not None or clock.second or clock.microsecond:
                        raise ValueError
                    if index == 5 and clock.hour == 3 and clock.minute:
                        raise TemperatureSetError(
                            "The website handles sixth events between 03:01 and "
                            "03:59 inconsistently; cannot determine comfort expiration."
                        )
                    event_day = day
                    if index == 5 and clock <= datetime.time(3):
                        event_day += datetime.timedelta(days=1)
                    wall = datetime.datetime.combine(event_day, clock)
                    if previous is not None and wall <= previous:
                        raise ValueError
                    previous = wall
                    walls.setdefault(wall, []).append(event)
            expiry = None
            for wall in sorted(walls):
                instants = {
                    wall.replace(tzinfo=zone, fold=fold).astimezone(utc)
                    for fold in (0, 1)
                    if wall.replace(tzinfo=zone, fold=fold).astimezone(utc)
                    .astimezone(zone).replace(tzinfo=None) == wall
                }
                future = sorted(instant for instant in instants if instant > now)
                if (len(instants) == 2 and future) or (
                    not instants and wall > local_now.replace(tzinfo=None)
                ):
                    raise TemperatureSetError(
                        "The next schedule time is ambiguous or nonexistent during "
                        "a daylight-saving change; supply an explicit UTC expiration."
                    )
                if future:
                    expiry = future[0]
                    if return_transition and len(walls[wall]) == 1:
                        raw_temperature = walls[wall][0].get("TempFloor")
                        try:
                            if type(raw_temperature) in (int, float) and math.isfinite(raw_temperature):
                                scheduled_temperature = raw_temperature / 100
                        except OverflowError:
                            pass
                    break
            if expiry is None:
                raise TemperatureSetError(
                    "No active future schedule transition was found; "
                    "supply an explicit expiration."
                )
        expiry = expiry.astimezone(utc)
        if expiry <= now or expiry.second or expiry.microsecond:
            raise TemperatureSetError(
                "Comfort expiration must be in the future and aligned to a whole minute."
            )
        formatted = expiry.strftime("%d/%m/%Y %H:%M:00")
        if return_transition:
            return {"end_time": formatted, "temperature_celsius": scheduled_temperature}
        return formatted
    except TemperatureSetError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise TemperatureSetError(
            "Cannot determine a valid comfort expiration from the supplied "
            "schedule, timezone, or override time."
        ) from None


class Api:
    """Schluter client using the website's Application 7 authentication."""

    def __init__(self, timeout=10, http_session=None, email=None, password=None):
        self._timeout = timeout
        self._http_session = http_session if http_session is not None else requests.Session()
        self._session_id = None
        self._session_lock = threading.RLock()
        self._on_authenticated = None
        self._email = email
        self._password = password

    def get_thermostats(self):
        """Discover thermostats with the authenticated browser-style request.

        The site expects a sessionid query parameter, not a session cookie.
        On 401 or empty Groups, refresh the session and retry once.
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            )
        }

        def unpack(payload):
            if isinstance(payload, dict):
                if isinstance(payload.get("Groups"), list):
                    result = []
                    for group in payload["Groups"]:
                        if isinstance(group, dict) and isinstance(group.get("Thermostats"), list):
                            result.extend(group["Thermostats"])
                    return result
                if isinstance(payload.get("Thermostats"), list):
                    return payload["Thermostats"]
                # Keep the legacy handling for an unnamed list envelope.
                for value in payload.values():
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        return value
            return payload if isinstance(payload, list) else []

        thermostats = []
        for attempt in range(2):
            params = {"sessionid": self._session_id} if self._session_id else None
            try:
                response = requests.get(
                    API_GET_THERMOSTATS_URL, params=params,
                    headers=headers, timeout=self._timeout, cookies={},
                    allow_redirects=False,
                )
                if response.status_code == 401 and attempt == 0:
                    self.get_session(force=True)
                    continue
                payload = response.json() if 200 <= response.status_code < 300 else None
                thermostats = unpack(payload)
                if thermostats:
                    break
                groups_empty = (
                    isinstance(payload, dict)
                    and isinstance(payload.get("Groups"), list)
                    and not thermostats
                )
                if groups_empty and attempt == 0:
                    self.get_session(force=True)
                    continue
                break
            except RequestException:
                _LOGGER.warning("Schluter thermostat discovery request failed")
                raise RequestException("Schluter thermostat discovery request failed") from None
            except Exception:
                _LOGGER.warning("Schluter thermostat discovery returned an invalid response")
                break

        if not thermostats:
            try:
                response = self._call_api("get", API_GET_LOCATIONS_URL)
                locations = response.json() if response is not None else None
                if isinstance(locations, list):
                    for location in locations:
                        if isinstance(location, dict):
                            thermostats.extend(location.get("thermostats", []) or [])
            except Exception:
                _LOGGER.warning("Schluter locations-based thermostat discovery failed")

        thermostats = [t for t in thermostats if isinstance(t, dict)]
        _LOGGER.debug("Schluter discovery found %d thermostat(s)", len(thermostats))
        return [Thermostat(t) for t in thermostats]

    def get_session(self, email=None, password=None, force=False, app_id=None):
        """Obtain an Application 7 session and publish it through one owner.

        The existing signature is preserved for the bundled authenticator.
        """
        with self._session_lock:
            try:
                if not _HAS_REQUESTS:
                    _LOGGER.warning("Schluter authentication unavailable")
                    return None
                with requests.Session() as transport:
                    transport.trust_env = False
                    transport.mount(
                        "https://", requests.adapters.HTTPAdapter(max_retries=0)
                    )
                    transport.headers.update({"Accept": "application/json"})
                    response = transport.post(
                        API_AUTH_URL,
                        json={
                            "Email": email or self._email,
                            "Password": password or self._password,
                            "Application": API_APPLICATION_ID,
                        },
                        timeout=self._timeout,
                        allow_redirects=False,
                    )
                    if not 200 <= response.status_code < 300:
                        _LOGGER.warning(
                            "Schluter authentication failed: http_status=%s",
                            response.status_code,
                        )
                        return response
                    data = response.json()
                    code = data.get("ErrorCode") if isinstance(data, dict) else None
                    session_id = data.get("SessionId") if isinstance(data, dict) else None
                    if not (
                        type(code) in (int, str) and code in (0, "0")
                        and isinstance(session_id, str) and session_id.strip()
                    ):
                        _LOGGER.warning("Schluter authentication was not acknowledged")
                        return response

                if self._on_authenticated is not None:
                    self._on_authenticated(session_id)
                else:
                    self._session_id = session_id
                _LOGGER.info("Schluter Application 7 authentication succeeded")
                return response
            except Exception:
                # Do not expose response bodies or credential-bearing URLs.
                _LOGGER.warning("Schluter authentication could not be completed")
                return None


    def _call_api(self, method, url, params=None, json=None, headers=None):
        """Perform an API request and retry once after an unauthorized response."""
        method = method.lower()
        params = dict(params or {})
        headers = dict(headers or {})
        top_level = url == API_GET_THERMOSTATS_URL
        for attempt in range(2):
            if top_level and self._session_id:
                params["sessionid"] = self._session_id
            try:
                transport = requests if top_level else self._http_session
                response = transport.request(
                    method, url, params=params, json=json,
                    headers=headers, timeout=self._timeout,
                    allow_redirects=False,
                )
                if response is not None and response.status_code == 401 and attempt == 0:
                    self.get_session(force=True)
                    continue
                return response
            except RequestException:
                raise RequestException("Schluter API request failed") from None
            except Exception:
                _LOGGER.warning("Schluter API request could not be completed")
                return None
        return None

    def get_energy_usage(self, serial_number, view, report_date):
        """Read one website energy report with the existing active session only."""
        try:
            if view not in ("day", "week", "year") or not isinstance(report_date, str):
                raise ValueError
            if view == "year":
                if not re.fullmatch(r"[1-9]\d{3}", report_date):
                    raise ValueError
            elif datetime.date.fromisoformat(report_date).isoformat() != report_date:
                raise ValueError
        except (TypeError, ValueError):
            raise EnergyReadError("Choose a valid energy view and reporting date.") from None
        session_id = self._session_id
        if not _HAS_REQUESTS or not session_id or not serial_number:
            raise EnergyReadError("No active Schluter session is available for energy reports.")
        try:
            response = requests.get(
                API_GET_ENERGY_URL,
                params={"sessionid": session_id, "serialnumber": serial_number,
                        "view": view, "date": report_date, "history": 0,
                        "calc": "yes", "weekstart": "sunday"},
                headers={"Accept": "application/json"},
                timeout=self._timeout, allow_redirects=False,
            )
            if response.status_code in (401, 403):
                raise EnergyReadError("Schluter did not authorize the energy report. Check integration authentication.")
            if not 200 <= response.status_code < 300:
                raise EnergyReadError(f"Schluter energy request returned HTTP {response.status_code}.")
            body = response.json()
            if not isinstance(body, dict) or body.get("Success") is False:
                raise EnergyReadError("Schluter did not return a usable energy report.")
            return body
        except EnergyReadError:
            raise
        except Exception:
            # Transport/JSON exceptions may include credential-bearing query strings.
            raise EnergyReadError("Could not read the Schluter energy report. No thermostat settings were changed.") from None

    # --- Schedule helpers
    def save_weekly_schedule(self, serial_number, schedules):
        """One verified website POST; never retry a possibly delivered save."""
        _schedule_structure(schedules)
        session_id = self._session_id
        if not _HAS_REQUESTS or not session_id or not serial_number:
            raise ScheduleWriteError("No active Schluter session is available for saving the schedule.")
        try:
            response = requests.request(
                "post", API_BASE_URL + "/api/thermostat",
                params={"sessionid": session_id, "serialnumber": serial_number},
                json={"Schedules": deepcopy(schedules)},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self._timeout, allow_redirects=False,
            )
        except RequestException:
            # A timeout can occur after delivery. Readback must determine persistence.
            return {"acknowledged": False, "delivery_uncertain": True}
        except Exception:
            raise ScheduleWriteError("The Schluter schedule request could not be sent.") from None
        if not 200 <= response.status_code < 300:
            raise ScheduleWriteError(f"Schluter schedule request returned HTTP {response.status_code}.")
        try:
            body = response.json()
        except Exception:
            body = None
        if isinstance(body, dict) and body.get("Success") is False:
            raise ScheduleWriteError("Schluter rejected the schedule save.")
        return {"acknowledged": isinstance(body, dict) and body.get("Success") is True,
                "delivery_uncertain": False}

    def get_schedule_limits(self, serial_number: str) -> dict:
        """Read this thermostat's website time bounds using the active session."""
        session_id = self._session_id
        if not session_id:
            raise ScheduleReadError("No active Schluter session for schedule limits.")
        try:
            response = requests.get(
                API_GET_DEFAULTS_URL,
                params={"sessionid": session_id},
                headers={"Accept": "application/json"},
                timeout=self._timeout,
                allow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                raise ScheduleReadError("Schluter could not provide schedule limits.")
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("Success") is False:
                raise ValueError
            thermostats = payload.get("Thermostats")
            if not isinstance(thermostats, list):
                raise ValueError
            matches = [item for item in thermostats if isinstance(item, dict)
                       and str(item.get("SerialNumber")) == str(serial_number)]
            if len(matches) != 1:
                raise ValueError
            limits = {}
            for key in ("MinTimeLimits", "MaxTimeLimits"):
                values = matches[0].get(key)
                if not isinstance(values, list) or len(values) != 6:
                    raise ValueError
                identifiers = []
                for value in values:
                    if (not isinstance(value, dict)
                            or type(value.get("ScheduleType")) is not int
                            or type(value.get("Clock")) is not int
                            or value["Clock"] < 0):
                        raise ValueError
                    identifiers.append(value["ScheduleType"])
                if sorted(identifiers) != list(range(6)):
                    raise ValueError
                # Export only validation fields, never the full defaults object.
                limits[key] = [{"ScheduleType": v["ScheduleType"], "Clock": v["Clock"]}
                               for v in values]
            minimum = {v["ScheduleType"]: v["Clock"] for v in limits["MinTimeLimits"]}
            if any(minimum[v["ScheduleType"]] > v["Clock"] for v in limits["MaxTimeLimits"]):
                raise ValueError
            return deepcopy(limits)
        except ScheduleReadError:
            raise
        except Exception:
            # Transport/JSON exceptions can contain session-bearing URLs.
            raise ScheduleReadError("Schluter schedule limits are unavailable.") from None




    def return_to_schedule(self, serial_number, vacation_enabled):
        """Resume the programmed schedule using the existing active session."""
        session_id = self._session_id
        if not serial_number or not isinstance(session_id, str) or not session_id.strip():
            raise ReturnToScheduleError(
                "Cannot return to schedule: the thermostat or active session is missing."
            )
        if type(vacation_enabled) is not bool:
            raise ReturnToScheduleError(
                "Cannot return to schedule: the vacation setting is unknown."
            )
        headers = {'Content-Type': 'application/json; charset=UTF-8'}
        try:
            if not _HAS_REQUESTS:
                raise ReturnToScheduleError(
                    "Cannot return to schedule: the requests library is unavailable."
                )
            response = requests.request(
                'post', API_BASE_URL + '/api/thermostat',
                params={'sessionid': session_id, 'serialnumber': serial_number},
                json={'RegulationMode': 1, 'VacationEnabled': vacation_enabled},
                headers=headers, timeout=self._timeout, allow_redirects=False,
            )
            if response is None:
                raise ReturnToScheduleError(
                    "Schluter returned no response; return to schedule is unconfirmed."
                )
            if not 200 <= response.status_code < 300:
                raise ReturnToScheduleError(
                    f"Schluter return-to-schedule request failed with HTTP {response.status_code}."
                )
            try:
                result = response.json()
            except ValueError:
                result = None
            if isinstance(result, dict) and result.get('Success') is False:
                raise ReturnToScheduleError(
                    "Schluter rejected the return-to-schedule request."
                )
            return result
        except ReturnToScheduleError:
            raise
        except RequestException:
            raise ReturnToScheduleError(
                "Could not communicate with Schluter; return to schedule is unconfirmed."
            ) from None
        except Exception:
            raise ReturnToScheduleError(
                "The Schluter return-to-schedule request could not be completed."
            ) from None

    def set_temperature(self, serial_number: str, temperature_celsius: float, duration_minutes: Optional[int] = None, end_time: Optional[str] = None) -> Optional[dict]:
        """Set a comfort temperature with a required future UTC expiration.

        Uses the thermostat website's /api/thermostat endpoint.
        - Celsius is converted to the server's centidegrees.
        - An explicit end_time takes precedence over duration_minutes.
        - Expirations use DD/MM/YYYY HH:MM:00 in UTC.
        - Ordinary climate adjustments supply the next scheduled transition.

        Return parsed JSON after successful HTTP delivery.

        Captured acknowledgements use {"Success": true}. HTTP success alone
        does not confirm thermostat acceptance. Raise TemperatureSetError
        on transport errors or explicit rejection.
        """
        if not serial_number or temperature_celsius is None:
            raise TemperatureSetError(
                "A thermostat serial number and temperature are required."
            )

        # Convert to centidegrees (2390 == 23.90 C).
        try:
            temp_value = int(round(float(temperature_celsius) * 100))
        except Exception:
            raise TemperatureSetError(
                "The requested Schluter temperature is invalid."
            ) from None

        if end_time is None and duration_minutes is None:
            raise TemperatureSetError("A future comfort expiration is required.")
        ct_str = comfort_end_time(
            end_time=end_time, duration_minutes=duration_minutes
        )

        payload = {
            'ComfortTemperature': str(temp_value),
            'RegulationMode': 2,
            'VacationEnabled': False,
        }
        payload['ComfortEndTime'] = ct_str

        url = API_BASE_URL + '/api/thermostat'

        # Match the browser JSON request without replayed cookies or credentials.
        headers = {'Content-Type': 'application/json; charset=UTF-8'}

        params = {}
        if self._session_id:
            params['sessionid'] = self._session_id
        params['serialnumber'] = serial_number

        try:
            # Ensure requests is available
            if not _HAS_REQUESTS:
                raise TemperatureSetError(
                    "Cannot set temperature: the requests library is unavailable."
                )

            # Use top-level requests.request to mimic XHR (no session cookiejar)
            resp = requests.request(
                'post', url, params=params, json=payload, headers=headers,
                timeout=self._timeout, allow_redirects=False,
            )

            if resp is None:
                raise TemperatureSetError(
                    "Schluter returned no response; the temperature change is unconfirmed."
                )

            if not 200 <= resp.status_code < 300:
                raise TemperatureSetError(
                    f"Schluter temperature request failed with HTTP {resp.status_code}."
                )

            try:
                out = resp.json()
            except ValueError:
                out = None

            if isinstance(out, dict) and out.get("Success") is False:
                raise TemperatureSetError(
                    "Schluter rejected the temperature-setting request."
                )

            return out
        except TemperatureSetError:
            raise
        except RequestException:
            raise TemperatureSetError(
                "Could not communicate with Schluter; "
                "the temperature change is unconfirmed."
            ) from None
        except Exception:
            raise TemperatureSetError(
                "An unexpected error occurred while setting the Schluter "
                "temperature; the change is unconfirmed."
            ) from None




