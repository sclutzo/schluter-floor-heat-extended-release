"""Offline lifecycle safety checks for staged Home Assistant config entry support."""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "schluter_heat_extended"
if "schluter_heat_extended" not in sys.modules:
    package = types.ModuleType("schluter_heat_extended")
    package.__path__ = [str(COMPONENT)]
    sys.modules["schluter_heat_extended"] = package
runtime = importlib.import_module("schluter_heat_extended.config_entry_runtime")
auth = importlib.import_module("schluter_heat_extended.v_lib.authenticator")


class ConfigEntryRuntimeTest(unittest.TestCase):
    def setup_mocks(self, state, thermostats):
        api = MagicMock()
        api.get_thermostats.return_value = thermostats
        authentication = MagicMock()
        authentication.state = state
        authenticator = MagicMock()
        authenticator.authenticate.return_value = authentication
        return api, authenticator

    def test_success_uses_no_legacy_yaml_cache_and_returns_only_safe_names(self):
        device = types.SimpleNamespace(serial_number="000123", name="Bathroom Floor Heat")
        api, authenticator = self.setup_mocks(auth.AuthenticationState.AUTHENTICATED, [device])
        with patch.object(runtime, "Api", return_value=api), patch.object(
            runtime, "Authenticator", return_value=authenticator
        ) as factory:
            status, account = runtime.connect_account("User@example.org", "private-password")
        self.assertEqual(status, "ok")
        self.assertIs(account.api, api)
        self.assertEqual(account.device_names_by_serial, {"000123": "Bathroom Floor Heat"})
        self.assertIsNone(factory.call_args.kwargs["session_id_cache_file"])
        api._http_session.close.assert_not_called()

    def test_auth_failure_closes_session_and_returns_no_sensitive_error(self):
        for state, expected in (
            (auth.AuthenticationState.BAD_EMAIL, "invalid_auth"),
            (auth.AuthenticationState.BAD_PASSWORD, "invalid_auth"),
            (auth.AuthenticationState.REQUIRES_AUTHENTICATION, "cannot_connect"),
        ):
            api, authenticator = self.setup_mocks(state, [])
            with patch.object(runtime, "Api", return_value=api), patch.object(
                runtime, "Authenticator", return_value=authenticator
            ):
                status, account = runtime.connect_account("user@example.org", "secret")
            self.assertEqual(status, expected)
            self.assertIsNone(account)
            self.assertNotIn("secret", status)
            api._http_session.close.assert_called_once()

    def test_no_thermostats_and_network_error_fail_closed(self):
        api, authenticator = self.setup_mocks(auth.AuthenticationState.AUTHENTICATED, [])
        with patch.object(runtime, "Api", return_value=api), patch.object(
            runtime, "Authenticator", return_value=authenticator
        ):
            self.assertEqual(runtime.connect_account("unit@example.invalid", "secret"),
                             ("no_thermostats", None))
        api._http_session.close.assert_called_once()
        api, authenticator = self.setup_mocks(auth.AuthenticationState.AUTHENTICATED, [])
        api.get_thermostats.side_effect = RuntimeError("secret-password-and-session")
        with patch.object(runtime, "Api", return_value=api), patch.object(
            runtime, "Authenticator", return_value=authenticator
        ):
            self.assertEqual(runtime.connect_account("unit@example.invalid", "secret"),
                             ("cannot_connect", None))
        api._http_session.close.assert_called_once()

    def test_refresh_returns_only_acknowledged_boolean(self):
        api = MagicMock()
        api.get_session.return_value = types.SimpleNamespace(
            status_code=200,
            json=lambda: {"ErrorCode": 0, "SessionId": "super-secret-session"}
        )
        self.assertTrue(runtime.refresh_account(api))
        api.get_session.assert_called_once_with(force=True)
        api.get_session.return_value = types.SimpleNamespace(
            status_code=403, json=lambda: {}
        )
        self.assertFalse(runtime.refresh_account(api))
        api.get_session.side_effect = RuntimeError("private")
        self.assertFalse(runtime.refresh_account(api))

    def test_entry_runtime_is_staged_and_never_starts_unverified_energy(self):
        init = (COMPONENT / "__init__.py").read_text()
        climate = (COMPONENT / "climate.py").read_text()
        self.assertIn("async def async_setup_entry(", init)
        self.assertIn("async def async_unload_entry(", init)
        self.assertIn("async_forward_entry_setups", init)
        self.assertIn("async_unload_platforms_or_raise", init)
        self.assertIn("entry.runtime_data = account", init)
        self.assertIn("entry.async_on_unload(", init)
        self.assertIn("config_entry_id", init)
        self.assertIn("vol.Optional(DOMAIN)", init)
        self.assertIn("if DOMAIN not in config:", init)
        self.assertIn("async def async_setup_entry(hass, entry, async_add_entities)", climate)
        self.assertIn('f"schluter_heat_extended_{serial_number}"', climate)
        # New UI path must never invoke legacy-zone energy import.
        setup_entry = init.split("async def async_setup_entry(", 1)[1].split(
            "\nasync def async_unload_entry(", 1
        )[0]
        self.assertNotIn("async_setup_energy_statistics(", setup_entry)
        self.assertNotIn("async_add_external_statistics(", setup_entry)
        self.assertNotIn("Store(", setup_entry)


if __name__ == "__main__":
    unittest.main()
