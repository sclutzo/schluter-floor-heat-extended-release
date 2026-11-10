"""Exercise real Home Assistant 2026.11 config-flow methods offline.

All Schluter authentication requests, HA state and platform forwards are
mocked. Never contact the provider or use genuine credentials.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))
from schluter_heat_extended import config_flow as flow_mod
from schluter_heat_extended import config_entry_runtime as runtime_mod
from schluter_heat_extended import const
from schluter_heat_extended import async_setup_entry


class ConfigFlowBehaviourTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_ui_form_and_yaml_conflict(self):
        flow = flow_mod.SchluterHeatExtendedConfigFlow()
        flow.hass = SimpleNamespace(data={})
        with patch.object(flow, "_async_current_entries", return_value=[]):
            result = await flow.async_step_user()
        self.assertEqual(result["step_id"], "user")
        self.assertEqual(result["type"].value, "form")

        flow.hass = SimpleNamespace(data={const.DOMAIN: {"legacy": True}})
        with patch.object(flow, "_async_current_entries", return_value=[]):
            result = await flow.async_step_user()
        self.assertEqual(result["reason"], "yaml_active")

    async def test_login_form_validates_without_leaking_credentials(self):
        flow = flow_mod.SchluterHeatExtendedConfigFlow()
        fake_executor = AsyncMock(return_value="ok")
        flow.hass = SimpleNamespace(data={}, async_add_executor_job=fake_executor)
        user_input = {
            "username": "Name@Example.ORG", "password": "test-PRIVATE-credential",
        }
        with (
            patch.object(flow, "_async_current_entries", return_value=[]),
            patch.object(flow, "async_set_unique_id", new_callable=AsyncMock) as set_id,
            patch.object(flow, "_abort_if_unique_id_configured"),
        ):
            result = await flow.async_step_user(user_input)
        self.assertEqual(result["type"].value, "create_entry")
        self.assertEqual(result["data"]["username"], user_input["username"])
        self.assertEqual(result["data"]["password"], user_input["password"])
        set_id.assert_awaited_once_with("name@example.org")
        fake_executor.assert_awaited_once()

    async def test_no_login_on_bad_email(self):
        flow = flow_mod.SchluterHeatExtendedConfigFlow()
        fake_executor = AsyncMock()
        flow.hass = SimpleNamespace(data={}, async_add_executor_job=fake_executor)
        with patch.object(flow, "_async_current_entries", return_value=[]):
            result = await flow.async_step_user(
                {"username": "bad-at-all", "password": "do-not-send"}
            )
        self.assertEqual(result["errors"]["base"], "invalid_account")
        fake_executor.assert_not_awaited()

    async def test_options_flow_selects_only_discovered_device(self):
        selected_entry = SimpleNamespace(
            options={"time_zones": {"000222": "America/Chicago"}}
        )
        options = flow_mod.SchluterOptionsFlow()
        options.handler = "entry-id"
        options.hass = SimpleNamespace(
            data={const.DOMAIN: {
                "device_names_by_serial": {
                    "000111": "Bathroom", "000222": "Out of town"
                }
            }},
            config=SimpleNamespace(time_zone="America/New_York"),
            config_entries=SimpleNamespace(
                async_get_known_entry=Mock(return_value=selected_entry)
            ),
        )
        start = await options.async_step_init()
        self.assertEqual(start["step_id"], "init")
        bad = await options.async_step_init({"thermostat": "000333"})
        self.assertEqual(bad["errors"]["base"], "unknown_device")
        selection = await options.async_step_init({"thermostat": "000111"})
        self.assertEqual(selection["step_id"], "timezone")
        saved = await options.async_step_timezone(
            {"time_zone": "America/New_York"}
        )
        self.assertEqual(saved["type"].value, "create_entry")
        self.assertEqual(saved["data"]["time_zones"], {
            "000111": "America/New_York",
            "000222": "America/Chicago",
        })

    async def test_ui_entry_setup_propagates_failure_and_closes_session(self):
        api = Mock()
        account = runtime_mod.ConnectedAccount(
            api=api, device_names_by_serial={"000111": "Bathroom"}
        )
        entry = SimpleNamespace(
            data={"username": "a@b.test", "password": "fake"},
            options={"time_zones": {"000111": "America/New_York"}},
            entry_id="entry-test",
            runtime_data=None,
            async_on_unload=Mock(),
        )
        async def executor(fn, *args):
            return fn(*args)
        hass = SimpleNamespace(
            data={}, async_add_executor_job=executor,
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms_or_raise=AsyncMock(),
            ),
            services=SimpleNamespace(
                has_service=Mock(return_value=False),
                async_remove=Mock(),
            ),
        )
        with (
            patch("schluter_heat_extended.connect_account",
                  return_value=("ok", account)),
            patch("schluter_heat_extended.async_setup_energy_statistics_entry",
                  new_callable=AsyncMock) as energy_start,
            patch("schluter_heat_extended.async_stop_energy_statistics_entry",
                  new_callable=AsyncMock) as energy_stop,
            patch("schluter_heat_extended.close_account") as close,
            patch("schluter_heat_extended.async_track_time_interval",
                  return_value=lambda: None),
        ):
            energy_start.side_effect = RuntimeError("test-only-failure")
            with self.assertRaisesRegex(RuntimeError, "test-only-failure"):
                await async_setup_entry(hass, entry)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        hass.config_entries.async_unload_platforms_or_raise.assert_awaited_once()
        energy_stop.assert_awaited_once()
        close.assert_called_once_with(api)
        self.assertNotIn(const.DOMAIN, hass.data)


if __name__ == "__main__":
    unittest.main()
