# Schluter Floor Heat Extended

<p align="center">
  <img src="assets/logo.svg" alt="Schluter Floor Heat Extended — unofficial Home Assistant integration" width="700">
</p>

> **Unreleased beta candidate — private staging.** This is an independent Home Assistant custom integration for Schluter DITRA-HEAT Wi-Fi thermostats. It is **not** made, endorsed, or supported by Schluter Systems or the Home Assistant project. The repository remains private until final review and explicit maintainer approval.

**Version:** `1.1.0b1`  
**Minimum tested Home Assistant:** `2026.11.0`  
**Integration domain:** `schluter_heat_extended`  
**Current testing:** One physical thermostat, with UI setup and per-device timezone selection. Multi-thermostat installations and other regional timezones have offline test coverage but have not been verified on additional physical devices.

## Features

- Home Assistant **UI account login** and reauthentication, without a `secrets.yaml` login requirement
- Thermostat climate state, target temperature, heating/idle status and temporary comfort overrides
- Read and edit full weekly thermostat schedules, with server readback and conflict detection
- Next scheduled transition and return-to-schedule controls
- On-demand provider energy reports and forward-only hourly Recorder energy statistics
- Independent per-thermostat statistic IDs and explicit time zone configuration
- Optional **full-size and compact thermostat cards**, plus a **separate energy card**, all in [dashboard/](dashboard/README.md)

## Install for a controlled beta test

**Do not replace a working integration without first backing up Home Assistant, its Recorder database and any integration-specific state.** This is a prerelease candidate.

For a manual installation, copy only `custom_components/schluter_heat_extended/` to `/config/custom_components/schluter_heat_extended/`, validate configuration and restart Home Assistant Core. Then use **Settings → Devices & services → Add integration → Schluter Floor Heat Extended** and enter your Schluter account credentials in Home Assistant's UI. Assign and verify an **IANA time zone for each thermostat** in the integration's options before relying on hourly energy statistics.

Do **not** simultaneously enable the built-in `schluter:` integration or an older YAML configuration of `schluter_heat_extended:`. For a migration, preserve `/config/.storage/`, Recorder history and the original per-device external statistic ID; do not delete and re-add the integration simply to upgrade.

**Marketplace custom-repository testing** will be performed only after this clean-history repository is explicitly approved for public visibility and has a tagged beta release. Being available as a custom repository is not the same as acceptance into the official Marketplace catalog.

## Optional thermostat dashboards

The optional [dashboard instructions](dashboard/README.md) explain how to install the SVG/JavaScript assets under `/config/www/`, add the frontend resources, and configure the full-size or compact cards. The energy card is a separate component. These are sample dashboard definitions and require mapping the example `climate` entity to the thermostat installed on your system.

The dashboard files are **not installed automatically** by the integration.

## Tested behavior and limitations

Live beta testing on Home Assistant 2026.11 confirmed UI account setup, both thermostat-card layouts, controlled schedule edits and readback, a real scheduled heating transition, energy-ledger preservation after Core restart, Recorder reconciliation, and clearing a stale historical-day flag.

- Only one physical thermostat has been tested. Multi-device and non-U.S.-Eastern device behavior is still experimental and intentionally fails closed when hourly time mapping cannot be verified.
- External hourly statistics track forward from a persisted anchor. **No historical backfill is promised.** Ambiguous or DST-transition hours are not invented.
- The integration depends on an undocumented third-party cloud interface, which could change.
- Do not post credentials, session cookies, device serials, trace files or private Home Assistant configuration in public issues.

## License, upstream code and support

This project contains modifications based on the Apache-2.0 Home Assistant Core Schluter integration and the MIT-licensed `py-schluter` project. See [LICENSE](LICENSE), [NOTICE](NOTICE), and [LICENSES/](LICENSES/). Original project contributions use Apache-2.0. The separate artwork is an unofficial project design.

Use the [Issues](../../issues) page for reproducible reports after the repository becomes public. Include Home Assistant and integration version, redacted relevant logs and reproduction steps. **Never include secrets or serial numbers.**

*There is currently no official catalog listing or stable public release. Publication will be authorized separately.*
