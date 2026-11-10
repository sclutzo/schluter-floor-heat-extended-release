"""Read-only, offline contract tests for the Schluter clock metadata diagnostic."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "schluter_heat_extended" / "time_settings_diagnostic.py"
spec = importlib.util.spec_from_file_location("time_settings_diagnostic_under_test", SOURCE)
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class TimeSettingsDiagnosticTest(unittest.TestCase):
    def test_find_basic_timezone_and_dst_keys_without_values(self):
        private = "sensitive-value-do-not-export"
        payload = {
            "SerialNumber": "123456", "SessionId": private,
            "TZOffset": "-0500", "DSTEnabled": True,
            "TimeSyncMode": "server", "CurrentTime": "12:30",
            "Password": private, "Room": private,
        }
        result = diagnostic.inspect_time_settings(payload)
        names = {entry["field"]: entry["type"] for entry in result["time_related_fields"]}
        self.assertEqual(names["TZOffset"], "string")
        self.assertEqual(names["DSTEnabled"], "boolean")
        self.assertEqual(names["TimeSyncMode"], "string")
        self.assertEqual(names["CurrentTime"], "string")
        self.assertTrue(result["read_only"])
        self.assertFalse(result["values_included"])
        self.assertNotIn(private, str(result))
        self.assertNotIn("123456", str(result))
        self.assertNotIn("-0500", str(result))
        self.assertNotIn("server", str(result))

    def test_nested_settings_without_location_values_or_array_contents(self):
        raw = {
            "Settings": {
                "Clock": {"DaylightSavingTime": False, "AutomaticTime": True},
                "Location": {"TimeZone": "America/New_York",
                             "PostalCode": "private"},
                "Schedules": [{"Clock": "very-private"}],
            },
            "TimeZone_SecretKey": "not-safe",
        }
        fields = {entry["field"]: entry["type"]
                  for entry in diagnostic.inspect_time_settings(raw)["time_related_fields"]}
        self.assertEqual(fields["Settings.Clock.DaylightSavingTime"], "boolean")
        self.assertEqual(fields["Settings.Clock.AutomaticTime"], "boolean")
        self.assertEqual(fields["Settings.Location.TimeZone"], "string")
        self.assertNotIn("Settings.Location.PostalCode", fields)
        self.assertNotIn("Settings.Schedules[0].Clock", fields)
        self.assertNotIn("TimeZone_SecretKey", fields)

    def test_payload_data_is_never_mutated(self):
        raw = {"TZOffset": "-0400", "Settings": {"TimeSync": True}}
        original = {"TZOffset": "-0400", "Settings": {"TimeSync": True}}
        diagnostic.inspect_time_settings(raw)
        self.assertEqual(raw, original)

    def test_reject_keys_with_embedded_personal_identifiers(self):
        raw = {"TimeZone": "UTC", "TimeUserName": "secret",
               "TimePostalCode": "secret", "ClockAccount": "secret",
               "DSTSession": "secret", "TZOffset": "+0100"}
        result = diagnostic.inspect_time_settings(raw)
        keys = [f["field"] for f in result["time_related_fields"]]
        self.assertEqual(keys, ["TZOffset", "TimeZone"])

    def test_absent_or_invalid_mapping_is_safe(self):
        for raw in (None, [], "clock", 12, {"NotTimeRelated": {"TZOffset": "x"}}):
            result = diagnostic.inspect_time_settings(raw)
            if raw is None or not isinstance(raw, dict):
                self.assertEqual(result["time_related_fields"], [])
            self.assertTrue(result["read_only"])

    def test_fields_limit_and_no_arbitrary_nested_descent(self):
        data = {f"TimeField{i:03d}": i for i in range(100)}
        data["PersonalRoom"] = {"TimeSync": False}
        result = diagnostic.inspect_time_settings(data)
        self.assertEqual(len(result["time_related_fields"]), 32)
        self.assertNotIn("PersonalRoom.TimeSync",
                         [v["field"] for v in result["time_related_fields"]])

    def test_locations_metadata_only_extracts_time_fields(self):
        private = "never-expose-this-value"
        raw = [
            {"LocationName": "my house", "PostalCode": "21076",
             "Timezone": "America/New_York", "DSTEnabled": True,
             "Config": {"SyncMode": "server", "Password": private},
             "thermostats": [
                 {"SerialNumber": "777", "TZOffset": "-0500",
                  "ClockSettings": {"DaylightSaving": False,
                                    "EmailAddress": private}},
             ]},
            {"TimeZone": "America/Los_Angeles", "Phone": private},
        ]
        result = diagnostic.inspect_locations_time_settings(raw)
        self.assertEqual(result["source"], "locations_api")
        self.assertEqual(result["status"], "inspected")
        fields = {x["field"]: x["type"] for x in result["time_related_fields"]}
        self.assertEqual(fields["location[].Timezone"], "string")
        self.assertEqual(fields["location[].DSTEnabled"], "boolean")
        self.assertEqual(fields["location[].Config.SyncMode"], "string")
        self.assertEqual(fields["location[].thermostats[].TZOffset"], "string")
        self.assertEqual(fields["location[].thermostats[].ClockSettings.DaylightSaving"], "boolean")
        self.assertNotIn("location[].PostalCode", fields)
        self.assertNotIn("location[].thermostats[].SerialNumber", fields)
        self.assertNotIn("location[].thermostats[].ClockSettings.EmailAddress", fields)
        serialized = repr(result)
        for val in (private, "21076", "-0500", "America/New_York", "777"):
            self.assertNotIn(val, serialized)
        self.assertFalse(result["values_included"])
        self.assertTrue(result["read_only"])

    def test_locations_envelope_and_unsupported_payload(self):
        for wrapped in ({"locations": [{"TimeZone": "UTC"}]},
                        {"Locations": [{"TimeZone": "UTC"}]}):
            out = diagnostic.inspect_locations_time_settings(wrapped)
            self.assertEqual(out["status"], "inspected")
            self.assertEqual(out["time_related_fields"],
                             [{"field": "location[].TimeZone", "type": "string"}])
        for bad in (None, "hello", {"Groups": []}, {"locations": {}}, 0):
            self.assertEqual(diagnostic.inspect_locations_time_settings(bad)["status"], "unavailable")

    def test_locations_are_bounded_and_malformed_keys_ignored(self):
        raw = [{f"ClockField{i:03d}": i for i in range(150)} for _ in range(20)]
        raw[0][123] = "malformed"
        result = diagnostic.inspect_locations_time_settings(raw)
        self.assertLessEqual(len(result["time_related_fields"]), 48)
        self.assertTrue(result["time_related_fields"])
        # Even if an unknown object holds clock-looking keys, never traverse it.
        result = diagnostic.inspect_locations_time_settings(
            [{"UserProfile": {"TimeSync": "secret"}, "Config": {"TimeSync": True}}]
        )
        names = {x["field"] for x in result["time_related_fields"]}
        self.assertNotIn("location[].UserProfile.TimeSync", names)
        self.assertIn("location[].Config.TimeSync", names)

    def test_locations_transport_diagnostics_exclude_response_values(self):
        climate = (ROOT / "custom_components" / "schluter_heat_extended" / "climate.py").read_text()
        method = climate.split("    async def async_inspect_time_settings(self, include_locations=False):", 1)[1].split("\n    async def ", 1)[0]
        self.assertIn('"request_result": "http_non_success"', method)
        self.assertIn('"request_result": "non_json_response"', method)
        self.assertIn('"request_result": "no_response"', method)
        self.assertIn('"request_result": "transport_error"', method)
        self.assertIn('"request_result"] = "unexpected_json_envelope"', method)
        self.assertIn('"http_status": code', method)
        self.assertIn('"payload_type"', method)
        for unsafe in ("response.text", "response.content", "str(response)",
                       "response.url", "response.headers", "response.cookies",
                       "payload.keys()", "self._api._session_id"):
            self.assertNotIn(unsafe, method)

    def test_locations_api_is_opt_in_read_only_and_response_only(self):
        climate = (ROOT / "custom_components" / "schluter_heat_extended" / "climate.py").read_text()
        self.assertIn('func="async_inspect_time_settings", supports_response=SupportsResponse.ONLY', climate)
        self.assertIn('probatio.Optional("include_locations", default=False): cv.boolean', climate)
        method = climate.split("    async def async_inspect_time_settings(self, include_locations=False):", 1)[1].split("\n    async def ", 1)[0]
        self.assertIn("self.thermostat_data", method)
        self.assertIn("if not include_locations:", method)
        self.assertIn("return result", method)
        self.assertIn('self._api._call_api, "get", API_GET_LOCATIONS_URL', method)
        self.assertNotIn('"post"', method)
        self.assertNotIn('"put"', method)
        self.assertNotIn("get_thermostats(", method)
        self.assertNotIn("save_", method)


if __name__ == "__main__":
    unittest.main()
