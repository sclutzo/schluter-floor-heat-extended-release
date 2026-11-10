"""Offline safeguards for the staged Schluter config-entry UI migration."""

from __future__ import annotations

from importlib import import_module
import json
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

policy = import_module("schluter_heat_extended.config_flow_policy")
auth = import_module("schluter_heat_extended.config_flow_auth")
rules = import_module("schluter_heat_extended.timezone_rules")


class ConfigFlowPolicyTest(unittest.TestCase):
    def test_normalized_account_id_does_not_change_password(self):
        self.assertEqual(policy.account_unique_id("  TEST.User@Example.COM  "),
                         "test.user@example.com")
        for value in ("", "no-at-sign", "x@", "@example.com",
                      "test @example.com", None, "x" * 300 + "@a.com"):
            with self.assertRaises(ValueError):
                policy.account_unique_id(value)

    def test_explicit_per_device_zones_do_not_mutate_other_devices(self):
        discovered = {"0001": "Bathroom", "0002": "Vacation home"}
        first = policy.timezone_options_for_device({}, "0001", "America/New_York",
                                                    discovered)
        second = policy.timezone_options_for_device(first, "0002", "America/Chicago",
                                                     discovered)
        self.assertEqual(first, {"0001": "America/New_York"})
        self.assertEqual(second, {"0001": "America/New_York",
                                  "0002": "America/Chicago"})
        self.assertNotIn("0002", first)

    def test_unknown_devices_and_bad_zones_never_save(self):
        good = {"0001": "America/New_York"}
        discovered = {"0001": "Bathroom"}
        for serial, zone in (("0002", "America/New_York"),
                             ("0001", "-0500"),
                             ("0001", "America/Invalid"),
                             ("0001", " America/New_York")):
            with self.assertRaises(rules.TimezoneUnverified):
                policy.timezone_options_for_device(good, serial, zone, discovered)
        self.assertEqual(good, {"0001": "America/New_York"})

    def test_login_status_never_leaks_password_or_session(self):
        secret = "PASSWORD-SHOULD-NOT-APPEAR"
        session = "SESSION-SHOULD-NOT-APPEAR"
        def fake(response, raises=None):
            client = MagicMock()
            client.get_session.return_value = response
            if raises is not None:
                client.get_session.side_effect = raises
            with patch.object(auth, "Api", return_value=client):
                return auth.check_login("test@example.com", secret), client

        result, client = fake(types.SimpleNamespace(
            status_code=200, json=lambda: {"ErrorCode": 0, "SessionId": session}))
        self.assertEqual(result, "ok")
        client._http_session.close.assert_called_once()
        self.assertEqual(client.call_args if hasattr(client, "call_args") else None, None)

        for response, expected in (
            (types.SimpleNamespace(status_code=401), "invalid_auth"),
            (types.SimpleNamespace(status_code=404), "cannot_connect"),
            (types.SimpleNamespace(status_code=200, json=lambda: {"ErrorCode": 9, "SessionId": session}), "invalid_auth"),
            (types.SimpleNamespace(status_code=200, json=lambda: {"ErrorCode": 0}), "invalid_auth"),
            (None, "cannot_connect"),
        ):
            status, _ = fake(response)
            self.assertEqual(status, expected)
            self.assertNotIn(secret, status)
            self.assertNotIn(session, status)
        status, _ = fake(None, RuntimeError(secret + session))
        self.assertEqual(status, "cannot_connect")

    def test_ui_is_staged_and_cannot_replace_yaml_or_recorder_yet(self):
        flow = (COMPONENT / "config_flow.py").read_text()
        self.assertIn('reason="yaml_active"', flow)
        self.assertIn('reason="single_instance_allowed"', flow)
        self.assertIn('async_step_reauth_confirm', flow)
        self.assertIn('async_step_timezone', flow)
        self.assertIn("OptionsFlowWithReload", flow)
        self.assertIn("check_login,", flow)
        self.assertNotIn("async_get_energy_report", flow)
        self.assertNotIn("async_add_external_statistics", flow)
        manifest = json.loads((COMPONENT / "manifest.json").read_text())
        self.assertTrue(manifest.get("config_flow", False))
        self.assertEqual(manifest["version"], "1.1.0b1")

    def test_ui_translation_keys_exist_and_match(self):
        strings = json.loads((COMPONENT / "strings.json").read_text())
        translations = json.loads((COMPONENT / "translations" / "en.json").read_text())
        self.assertEqual(strings, translations)
        self.assertIn("reauth_confirm", strings["config"]["step"])
        self.assertIn("timezone", strings["options"]["step"])


if __name__ == "__main__":
    unittest.main()
