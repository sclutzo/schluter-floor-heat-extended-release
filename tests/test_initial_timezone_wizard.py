"""Offline behavioral checks for the initial per-thermostat timezone wizard.

Load the actual async flow methods from the integration's source with isolated
Home Assistant UI/test doubles: no network, credentials, or device access.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest


FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "schluter_heat_extended" / "config_flow.py"
)


class TimezoneUnverified(Exception):
    pass


def make_flow(*, login_status="ok", discover_status="ok", devices=None):
    if devices is None:
        devices = {"1001": "Test Bathroom"}
    devices = dict(devices)
    valid_zones = {"America/New_York", "America/Chicago"}

    def select_zone(zones, serial, zone, discovered):
        if serial not in discovered or zone not in valid_zones:
            raise TimezoneUnverified()
        return {**zones, serial: zone}

    account = SimpleNamespace(
        api=object(), device_names_by_serial=devices,
    )
    environment = {
        "Any": Any,
        "ConfigFlowResult": dict,
        "CONF_USERNAME": "username",
        "CONF_PASSWORD": "password",
        "account_unique_id": lambda email: email.strip().lower(),
        "check_login": lambda email, password: login_status,
        "connect_account": lambda email, password: (
            discover_status, account if discover_status == "ok" else None
        ),
        "close_account": lambda api: None,
        "timezone_options_for_device": select_zone,
        "TimezoneUnverified": TimezoneUnverified,
        "available_timezones": lambda: valid_zones,
        "probatio": SimpleNamespace(
            Schema=lambda schema: schema,
            Required=lambda name, default=None: name,
        ),
        "selector": SimpleNamespace(
            SelectSelector=lambda config: config,
            SelectSelectorConfig=lambda **kwargs: kwargs,
            SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"),
        ),
    }

    tree = ast.parse(FLOW_PATH.read_text(encoding="utf-8"), str(FLOW_PATH))
    cls = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "SchluterHeatExtendedConfigFlow"
    )
    cls.bases = []
    cls.keywords = []
    cls.decorator_list = []
    cls.body = [
        node for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"async_step_user", "async_step_initial_timezone"}
    ]
    namespace = ast.fix_missing_locations(
        ast.Module(body=[cls], type_ignores=[])
    )
    exec(compile(namespace, str(FLOW_PATH), "exec"), environment)
    FlowUnderTest = environment["SchluterHeatExtendedConfigFlow"]

    async def run_executor_job(function, *arguments):
        return function(*arguments)

    class TestFlow(FlowUnderTest):
        def __init__(self):
            self.hass = SimpleNamespace(
                data={},
                config=SimpleNamespace(time_zone="America/New_York"),
                async_add_executor_job=run_executor_job,
            )
            self.unique_id = None

        def _async_current_entries(self):
            return []

        async def async_set_unique_id(self, unique_id):
            self.unique_id = unique_id

        def _abort_if_unique_id_configured(self):
            return None

        def async_abort(self, **kwargs):
            return {"type": "abort", **kwargs}

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

    return TestFlow()


class InitialTimezoneWizardTest(unittest.TestCase):
    @staticmethod
    def login(flow):
        return asyncio.run(
            flow.async_step_user({
                "username": "test@example.invalid",
                "password": "not-a-real-password",
            })
        )

    def test_success_requires_explicit_timezone_before_creating_entry(self):
        flow = make_flow()
        form = self.login(flow)
        self.assertEqual(form["type"], "form")
        self.assertEqual(form["step_id"], "initial_timezone")
        self.assertNotIn("not-a-real-password", repr(form))
        entry = asyncio.run(flow.async_step_initial_timezone(
            {"time_zone": "America/New_York"}
        ))
        self.assertEqual(entry["type"], "create_entry")
        self.assertEqual(entry["options"], {
            "time_zones": {"1001": "America/New_York"}
        })
        self.assertIsNone(flow._pending_password)

    def test_multiple_devices_each_require_confirmation(self):
        flow = make_flow(devices={"1001": "Bathroom", "1002": "Another zone"})
        self.assertEqual(self.login(flow)["step_id"], "initial_timezone")
        next_step = asyncio.run(flow.async_step_initial_timezone(
            {"time_zone": "America/New_York"}
        ))
        self.assertEqual(next_step["type"], "form")
        self.assertEqual(next_step["description_placeholders"]["device_number"], "2")
        result = asyncio.run(flow.async_step_initial_timezone(
            {"time_zone": "America/Chicago"}
        ))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["options"]["time_zones"], {
            "1001": "America/New_York",
            "1002": "America/Chicago",
        })

    def test_invalid_timezone_stays_in_wizard(self):
        flow = make_flow()
        self.login(flow)
        response = asyncio.run(flow.async_step_initial_timezone(
            {"time_zone": "UTC+5"}
        ))
        self.assertEqual(response["type"], "form")
        self.assertEqual(response["errors"]["base"], "invalid_timezone")
        self.assertEqual(flow._pending_zones, {})

    def test_discovery_outage_preserves_actionable_error(self):
        for status, expected in (
            ("cannot_connect", "cannot_connect"),
            ("no_thermostats", "devices_unavailable"),
        ):
            with self.subTest(status=status):
                response = self.login(make_flow(discover_status=status))
                self.assertEqual(response["type"], "form")
                self.assertEqual(response["errors"]["base"], expected)

    def test_bad_login_never_starts_timezone_wizard(self):
        response = self.login(make_flow(login_status="invalid_auth"))
        self.assertEqual(response["type"], "form")
        self.assertEqual(response["step_id"], "user")
        self.assertEqual(response["errors"]["base"], "invalid_auth")


if __name__ == "__main__":
    unittest.main()
