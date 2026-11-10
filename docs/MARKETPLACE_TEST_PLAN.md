# Marketplace custom-repository test plan — beta 1.1.0b1

> **Do not perform until the repository is explicitly approved for public
> visibility, a versioned beta GitHub release exists, and HACS validation passes.**
> The official Marketplace catalog does **not** need to list this integration
> for a custom-repository test.

## 1. Use a disposable Home Assistant test VM

The cleanest proof is a **new or cloned test VM**, not a working Home Assistant
installation that already contains a manually copied Schluter integration.

- Use a supported Home Assistant release (minimum `2026.11.0`).
- Take a VMware/HA backup and verify rollback before changing an existing VM.
- Do not replace or delete another installation's `.storage`, Recorder database
  or existing per-device energy-statistics ledgers.
- Do not enable the built-in `schluter:` component or legacy YAML login for the
  same account alongside `schluter_heat_extended`.
- Keep all credentials, session files, device identifiers and HAR traces private.

## 2. Install through Marketplace

1. Confirm the published repository URL is
   `https://github.com/sclutzo/schluter-floor-heat-extended-release`.
2. In **Settings → Marketplace**, use the menu for **Add from link** or
   **Custom repositories** (wording may vary with the installed Marketplace).
   Enter the GitHub repository URL and select the **Integration** type if asked.
3. The published GitHub **pre-release tag must be exactly `1.1.0b1`**, matching
   `custom_components/schluter_heat_extended/manifest.json` (no leading `v`).
   On the repository's **Marketplace device page**, enable the normally disabled
   **Pre-release** entity, then switch **Pre-release** on. Marketplace shows
   stable releases by default, so the beta otherwise may not appear.
4. Select the available **`1.1.0b1` pre-release**, then install. If missing,
   confirm the repository is public, the GitHub release was *published*
   (a tag or release draft alone is insufficient), the pre-release switch is
   on, and the HACS validation workflow passed.
5. Restart Home Assistant Core when prompted.

This step installs the **backend integration only**. The optional thermostat
and energy cards are **not** installed automatically.

## 3. Configure and validate without changing schedules

1. Open **Settings → Devices & services → Add integration**, select
   **Schluter Floor Heat Extended**, and enter the account credentials through
   Home Assistant's UI. Do not paste credentials into `configuration.yaml`.
2. From the integration's options, explicitly assign the correct **IANA time
   zone** to each discovered thermostat.
3. Verify the correct thermostat climate entity appears and responds to
   read-only status updates.
4. In **Developer Tools → Actions**, call
   `schluter_heat_extended.get_energy_statistics_status`. Confirm state,
   per-device statistic IDs, missing hours, pending Recorder writes and errors.
   A new installation starts tracking forward, not by inventing backfill.
5. Open the read-only seven-day schedule before testing any write. If a
   controlled, intentional change is made, verify server readback and observed
   behavior. Never use a test that inadvertently alters a household schedule.

## 4. Optional dashboard test

Follow [dashboard/README.md](../dashboard/README.md). Copy bundled SVG and
JavaScript assets to `/config/www/`, register JavaScript module resources,
then use either full-size **or** compact card, with the separate energy card.

Replace the sample climate entity ID in **all** sample card elements with the
actual entity created for your test thermostat. The popup dialogs require
Browser Mod; dashboard installation is independent of the core integration.

## 5. Record results before approval

Confirm (without posting raw identifiers or screenshots of credentials):

- Marketplace download and UI setup/reauthentication work.
- Integration loads after a Core restart.
- Climate state, schedule readback and temporary-override controls work.
- Recorder/energy-statistics IDs and tracking anchor remain stable on an
  **upgrade test**, with no unexplained reset or duplicate hourly sums.
- The test does not require changes to the existing production installation.
- Browser/phone card appearance remains unchanged; do not revise the approved
  thermostat layout just for packaging.

If anything fails, preserve only sanitized logs and roll the **test VM** back
to its snapshot. Do not delete Recorder/`.storage` to solve a Marketplace
installation issue. Report a minimal redacted reproducer through GitHub Issues.
