# Schluter Floor Heat Extended 1.1.0b2 — Beta 2

**Status:** Unofficial GitHub pre-release (`1.1.0b2`), available for testing through Home Assistant Marketplace as a custom repository. Not listed in the official Marketplace catalog.

## New since 1.1.0b1

- **First-time setup wizard:** after account login, discover available
  thermostats and ask users to explicitly confirm an IANA timezone for each
  device, before the configuration entry is created.
- Suggest the Home Assistant timezone without silently assigning it.
  Existing installations can still change device zones using **Configure**.
- If the cloud login succeeds but discovery fails, show an actionable
  connection/no-thermostats error rather than an incorrect success status.
- Added isolated wizard regression tests for one device, multiple devices,
  invalid timezone, login failure, and device-discovery outage.
- Corrected the public README's outdated pre-release status message.

## Important constraints

- **Not yet an official Marketplace catalog listing.** Install via the
  Home Assistant Marketplace custom-repository feature and select
  **Include Pre-releases** from the integration's three-dot menu.
- Only the integration is installed automatically. Dashboard JS/SVG assets,
  custom cards, and the separate energy viewer remain manual/optional.
- Take a full Home Assistant backup before changing an existing integration.
  Do not concurrently enable the legacy `schluter:` integration and
  `schluter_heat_extended` against one thermostat.
- Hourly external Recorder energy data begins from a persisted anchor and
  does not promise historical backfill. Existing Recorder and `.storage`
  state must be preserved during upgrades.
- Real-device, clean Marketplace installation and outage/restart checks are
  required before promoting from beta.
- Cloud API unofficial. This project is not affiliated with Schluter.

## Clean-VM verification checklist

1. Restore the known-good clean HAOS development snapshot.
2. Install 1.1.0b2 via Marketplace custom repository and include pre-releases.
3. During Add integration, sign in and confirm **America/New_York** for a
   Maryland thermostat; confirm the flow completes without using Configure.
4. Verify thermostat state, seven-day schedule readback, and independent
   energy statistic creation; test restart continuity.
5. Review logs, no secret/device serial exposure, and Home Assistant CI.
