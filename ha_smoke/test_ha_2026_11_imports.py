"""Import-time/API smoke tests against installed Home Assistant 2026.11.

These never contact Schluter, start Home Assistant, or touch live Recorder data.
"""

from __future__ import annotations

from importlib import import_module
import inspect
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))


class HomeAssistant202611SmokeTest(unittest.TestCase):
    def test_all_ui_modules_import_against_actual_home_assistant(self):
        modules = {}
        for name in (
            "__init__", "config_flow", "config_entry_runtime",
            "config_flow_auth", "config_flow_policy", "climate",
            "energy", "energy_statistics", "timezone_rules",
        ):
            fullname = ("schluter_heat_extended" if name == "__init__"
                        else f"schluter_heat_extended.{name}")
            with self.subTest(module=fullname):
                modules[name] = import_module(fullname)
        from homeassistant.config_entries import (
            ConfigFlow, ConfigEntry, OptionsFlowWithReload,
        )
        self.assertTrue(issubclass(
            modules["config_flow"].SchluterHeatExtendedConfigFlow, ConfigFlow
        ))
        self.assertTrue(issubclass(
            modules["config_flow"].SchluterOptionsFlow, OptionsFlowWithReload
        ))
        self.assertTrue(inspect.iscoroutinefunction(
            modules["__init__"].async_setup_entry
        ))
        self.assertTrue(inspect.iscoroutinefunction(
            modules["__init__"].async_unload_entry
        ))
        self.assertTrue(inspect.iscoroutinefunction(
            modules["climate"].async_setup_entry
        ))

    def test_ha_2026_11_lifecycle_hooks_exist(self):
        from homeassistant.config_entries import ConfigEntry, ConfigEntries
        self.assertTrue(hasattr(ConfigEntry, "async_on_unload"))
        self.assertTrue(hasattr(ConfigEntries, "async_forward_entry_setups"))
        self.assertTrue(hasattr(ConfigEntries, "async_unload_platforms_or_raise"))

    def test_flow_selectors_and_models_can_be_instantiated(self):
        flow = import_module("schluter_heat_extended.config_flow")
        self.assertIsNotNone(flow.ACCOUNT_SCHEMA)
        self.assertIsNotNone(flow.PASSWORD_SCHEMA)
        self.assertTrue(flow.SchluterHeatExtendedConfigFlow.VERSION >= 1)
        self.assertTrue(callable(flow.SchluterHeatExtendedConfigFlow.async_get_options_flow))

    def test_energy_metadata_contract_remains_per_serial(self):
        from schluter_heat_extended.energy_statistics import EnergyStatistics
        self.assertIn("serial", inspect.signature(EnergyStatistics).parameters)
        self.assertIn("verify_offset", inspect.signature(EnergyStatistics).parameters)


if __name__ == "__main__":
    unittest.main()
