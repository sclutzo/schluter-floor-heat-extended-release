# Schluter Floor Heat Extended — v1.1.0b1 release candidate

**Unofficial beta Home Assistant custom integration.** This build was validated
on Home Assistant 2026.11. It is not affiliated with or supported by Schluter
Systems or Home Assistant.

## Installation and Home Assistant login

1. Back up Home Assistant and Recorder before testing upgrades. Install this
   directory at `/config/custom_components/schluter_heat_extended/`.
2. For a **new UI-configured installation**, open **Settings → Devices &
   services → Add integration → Schluter Floor Heat Extended**.
3. Enter your Schluter account email and password in Home Assistant's login
   dialog. **Do not add a `schluter_heat_extended:` username/password block in
   `configuration.yaml`** for the UI version. It does not require credentials
   in `secrets.yaml`; the integration also supports UI reauthentication.
4. In the integration's **Configure** options, select a verified **IANA time
   zone for each thermostat**. Unverified time zones deliberately pause hourly
   Recorder imports rather than manufacturing incorrectly timed measurements.
5. Verify the climate entity, schedule readback, and
   `schluter_heat_extended.get_energy_statistics_status`. Restart Home
   Assistant Core and recheck as part of a controlled upgrade.

## Upgrading from the legacy v1.0.2 YAML integration

Remove or comment out only the previous `schluter_heat_extended:` YAML account
block *after backing up the test Home Assistant instance*, then configure the
new integration through the UI. Do **not** simultaneously enable YAML and UI
versions or the upstream `schluter:` integration against the same thermostat.
Preserve `.storage` and Recorder; do not delete/recreate any energy-statistics
ledger or external statistic ID. Check the original statistic ID, tracking
start, and recorded intervals before and after an upgrade.

## Capabilities

- Discover supported Schluter Wi-Fi thermostats and expose climate entities.
- Retrieve and safely edit seven-day schedules with readback/conflict checks;
  temporarily override floor temperature and return to schedule.
- Report energy by day/week/month/year and publish per-device external Recorder
  energy statistics with IDs `schluter_heat_extended:energy_<serial>`.
- Configure an explicit time zone per discovered thermostat. Skip ambiguous
  or unmappable DST-transition hours.
- Inspect collector state via
  `schluter_heat_extended.get_energy_statistics_status`.

Optional full-size and compact dashboard cards and a **separate** energy card
are documented in the repository's `dashboard/README.md`. Dashboard JavaScript
and image assets are copied to `/config/www/` separately; installing the
integration does not overwrite existing dashboards.

## Release status and limitations

On November 10, 2026, the development VM showed functioning temperature
overrides, schedules, an observed scheduled heating transition, and Recorder
history preserved across restart (146 known hourly intervals, zero missing
completed hours, no pending Recorder writes or errors). A historical stale
warning cleared after the integration rechecked the report.

Broader multi-device/time-zone real-hardware testing, extended authentication
and Recorder soak tests, independent licensing/security review, and public HACS
validation remain outstanding. No automatic historical energy backfill is
claimed. Cloud service compatibility can change.

**Never commit account credentials, tokens, authentication caches, raw session
traces, or private thermostat identifiers.**

See the root `README.md`, `LICENSE`, and `NOTICE` for installation guidance,
limitations, license terms, and attributions.
