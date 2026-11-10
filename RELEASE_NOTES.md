# 1.1.0b1 — beta release notes (draft)

**Status:** Pre-release candidate, prepared for manual Marketplace custom-repository testing; this is not an official Marketplace catalog listing.

**Tag:** `1.1.0b1` — exactly matches the integration's `manifest.json` version (no leading `v`).

## New in this beta

- Home Assistant UI login, reauthentication and per-device IANA timezone configuration.
- Climate integration for Schluter Wi-Fi floor-heat thermostats.
- Temporary comfort temperature with end time, Return to Schedule and full seven-day schedule editing with confirmation/readback.
- Day/week/month/year energy reports and forward-only external Recorder hourly energy statistics, keyed independently to each thermostat.
- Safeguards against ambiguous times, partially reported energy hours, Recorder resumption and stale historical reports.
- Optional full-size or compact thermostat dashboard, with a separate energy card.
- Original unofficial icon and logo, code attribution, and open-source licenses.

## Evidence and requirements

Home Assistant **2026.11.0** and Python 3.14 compatibility were checked with automated import/API smoke tests. Offline Python/JS tests and Home Assistant hassfest validation ran on the staged development candidate. One physical thermostat was used for live testing of scheduling, setpoint changes, restart continuity and energy reporting.

**Known limits:** Multi-device live operation, multiple geographic time zones, and long-running cloud session refresh have not been tested broadly. For unverified timezone and DST data, hourly imports are deliberately paused rather than guessing. Historical backfill is not promised. The cloud API is unofficial and can change.

Only the Home Assistant custom integration under `custom_components/schluter_heat_extended/` is installed by Marketplace/HACS; `dashboard/` assets require separate manual setup, including Browser Mod for popup dialogs.

## Upgrade caution

Take a Home Assistant backup and preserve Recorder, `.storage` and per-device statistic IDs. Do not run a legacy YAML instance, the built-in Schluter integration, and this UI integration against the same thermostat simultaneously. Compare energy status and schedules before and after an upgrade.

**Distribution:** A public, signed-off tagged beta release and custom-repository installation test will be created only after the maintainer approves publication. This note is not proof of Marketplace catalog approval.
