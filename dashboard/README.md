# Schluter Floor Heat Extended: dashboard cards

These are **optional** Home Assistant dashboard cards for the
`schluter_heat_extended` integration. You can use the full-size thermostat card
or the smaller compact version, and add the **energy chart as a separate card**.

**Current Beta 2 installation:** Marketplace installs the thermostat
**integration**, but does **not yet** copy or register these optional dashboard
files. The steps below are manual. Automatic dashboard-resource installation
is being considered for a later beta.

## What the cards look like

| Full-size thermostat | Optional compact thermostat |
| :---: | :---: |
| <img src="screenshots/thermostat-full-width.webp" width="280" alt="Full-width thermostat card in Home Assistant"> | <img src="screenshots/thermostat-compact.webp" width="280" alt="Compact thermostat card in Home Assistant"> |
| [Full-size card YAML](cards/bathroom-heat-extended.yaml) | [Compact card YAML](cards/bathroom-heat-compact.yaml) |

[Separate energy card YAML](cards/bathroom-heat-energy-extended.yaml)

## Before you begin

You need:

- Home Assistant **2026.11 or newer**, with **Schluter Floor Heat Extended**
  installed and a working climate entity.
- **Browser Mod** installed and loaded in the browser to open **Adjust** and
  **Edit Schedule** popups.
- A way to copy files into Home Assistant's `/config/www/` folder (for example,
  Studio Code Server or a file editor).
- A dashboard you can edit.

**Find your entity ID first:** In **Settings → Devices & services → Entities**,
find the thermostat supplied by **Schluter Floor Heat Extended**. On our clean
DEV VM it is `climate.bath_heat`. On another Home Assistant installation it
may be different. **Do not rename an entity just to match the example.**
Instead, replace the example entity ID in the card YAML with your own.

The provided controls currently use **°F** and display schedule times in
`America/New_York`. Other units/timezones have not yet been verified for the
custom cards.

## Install the cards: six steps

### Step 1 — Copy six dashboard files into Home Assistant

Open the repository's [dashboard/www folder](www/) and copy these **six files**
from GitHub into **`/config/www/` on your Home Assistant machine**:

| JavaScript files | Image files |
| --- | --- |
| `bathroom-heat-controls.js` | `Thermostat.svg` |
| `bathroom-heat-energy.js` | `Adjust.svg` |
| | `EditSchedule.svg` |
| | `ReturnToSchedule.svg` |

Use GitHub's **Raw / Download raw file** option to preserve each file's
contents and name. The final locations should look like this:

```text
/config/www/bathroom-heat-controls.js
/config/www/bathroom-heat-energy.js
/config/www/Thermostat.svg
/config/www/Adjust.svg
/config/www/EditSchedule.svg
/config/www/ReturnToSchedule.svg
```

**Important:** Put the files directly inside `www`, not in a new
`dashboard/www` subfolder or the `custom_components` directory. The URL
`/local/filename` is how Home Assistant serves a file from `/config/www/`.

If you **created the `www` folder for the first time**, restart Home Assistant
Core once so it can discover the folder. Otherwise, no Core restart is
normally required for copying these files.

### Step 2 — Register the two JavaScript files

This step is required **even when the Schluter integration is already working**.

1. Go to **Settings → Dashboards**, then open the **⋮ menu** in the top-right.
2. Choose **Resources**. If Resources is missing, enable **Advanced Mode** in
   your Home Assistant user profile and return to this page.
3. Click **Add resource**. Enter this **URL** exactly, choose **JavaScript
   module** as the type, and save it:

   `/local/bathroom-heat-controls.js?v=1.0.23`

4. Click **Add resource** again. Enter this second **URL** exactly, choose
   **JavaScript module** again, and save it:

   `/local/bathroom-heat-energy.js?v=1.0.5`

Your resource list should contain:

| Resource URL | Type |
| --- | --- |
| `/local/bathroom-heat-controls.js?v=1.0.23` | **JavaScript module** |
| `/local/bathroom-heat-energy.js?v=1.0.5` | **JavaScript module** |

**Use `/local/...` in the URL field**, not `/config/www/...`, a GitHub address,
or a filename by itself. If the resource was previously registered, **edit
its existing entry** instead of creating a duplicate.

**Reload your Home Assistant browser tab** after saving both resources.
On Safari, use **Command–R**; if old code persists, clear the frontend cache.
For a Home Assistant Companion App, close/reopen it.

### Step 3 — Add the full-size thermostat card

1. Open your chosen dashboard and select **Edit dashboard → Add card → Manual**.
2. Open [bathroom-heat-extended.yaml](cards/bathroom-heat-extended.yaml) on
   GitHub. Use **Raw** or **Copy raw file** to copy the **entire YAML file**.
3. Paste it into Home Assistant's Manual card editor.
4. Check every `entity: climate.bath_heat` in the pasted YAML. If **your**
   thermostat has a different entity ID, replace **all occurrences** with
   yours. Keep `service_domain: schluter_heat_extended` unchanged.
5. Save the card.

**Want the compact layout instead?** Use
[bathroom-heat-compact.yaml](cards/bathroom-heat-compact.yaml) in Step 3,
**instead of** the full-size file. Do not install both unless you actually want
two thermostat cards. See [Compact layout options](#compact-layout-options).

### Step 4 — Add the energy chart as a second card

1. Choose **Edit dashboard → Add card → Manual** again.
2. Copy the entire
   [bathroom-heat-energy-extended.yaml](cards/bathroom-heat-energy-extended.yaml)
   file into the new card editor.
3. Its example starts like this:

   ```yaml
   type: custom:bathroom-heat-energy
   service_domain: schluter_heat_extended
   entity: climate.bath_heat
   title: Bathroom Floor Heat Energy
   default_view: day
   ```

4. Change the `entity:` value **only if** your thermostat entity ID is
   different, and save.

The **thermostat and energy chart are deliberately two separate cards**.
There is no combined layout to install.

### Step 5 — Check the cards

- The thermostat should display its clock, current and target temperatures,
  and adjustment controls.
- **Edit Schedule** should open the seven-day schedule viewer. Verify the
  displayed events before trying to save changes.
- The energy card should show the **Day / Week / Year** controls. If this is a
  new install, energy data may take time to accumulate; a zero reading is not
  automatically an error.

**Do not change integration credentials, `.storage` data, Recorder files,
thermostat schedules, or existing climate entities to install dashboard cards.**

### Step 6 — Keep the files for future updates

The versions `v=1.0.23` and `v=1.0.5` are resource **cache-busting versions**,
not thermostat/integration versions. On a dashboard asset update, replace the
file under `/config/www/` and **edit** its existing resource URL to the new
version; don't register the same script twice.

## Compact layout options

The [compact thermostat YAML](cards/bathroom-heat-compact.yaml) is a standard
Home Assistant Grid card containing a smaller Picture Elements thermostat.
It uses **the same controls JavaScript resource** as the full-size version.

At the beginning of the compact example:

```yaml
type: grid
columns: 2
square: false
cards:
  - type: picture-elements
    image: /local/Thermostat.svg
```

- `columns: 1` makes the thermostat full width.
- `columns: 2` makes it approximately half width (recommended for a normal
  desktop dashboard section).
- `columns: 3` makes it approximately one-third width and may be too small.

You can also adjust the compact scaler element's `font_scale: 1.0` to `0.9`
for slightly smaller text or `1.1` for larger text (supported range
`0.75–1.35`).

The compact card uses `EditSchedule.svg` for the Edit Schedule control.
**Both the compact YAML and the SVG are required** for the matching button
dimensions. Narrow phone screens may benefit from the full-size card instead.
Keep the energy card separate in either layout.

To switch between layouts, back up your current card YAML, edit the existing
thermostat card, and replace the entire YAML with the other example. There is
no integration reinstall needed.

## If something doesn't work

| Symptom | What to do |
| --- | --- |
| `Custom element doesn't exist: bathroom-heat-target` or `bathroom-heat-status` | Check the **controls** JS resource URL and **JavaScript module** type in Step 2, then reload the browser. |
| `Custom element doesn't exist: bathroom-heat-energy` | Check the **energy** JS resource in Step 2; it is separate from the controls resource. |
| A `/local/` file URL returns **404** | Confirm the exact filename is directly in `/config/www/`; restart Home Assistant Core if you just created that folder. |
| Thermostat background appears but custom labels don't | The SVG can load even when JavaScript isn't registered. Check Step 2 first. |
| Controls show an unavailable thermostat or fail to load data | Check that your card's entity ID matches the one from **Schluter Floor Heat Extended**. Don't change the service domain to match an entity name. |
| Adjust or Edit Schedule doesn't open | Confirm **Browser Mod** is installed and loaded in that browser. |
| You still see older dashboard controls | Edit the existing resource URL to the current version, then reload or clear the frontend cache. |
| Resources isn't in the Dashboards ⋮ menu | Enable **Advanced Mode** in your user profile. If resources are intentionally configured in YAML, see below. |

**To verify the JavaScript file paths**, open the following relative URLs
against your Home Assistant web address; you should see JavaScript text rather
than a 404 or a dashboard page:

- `/local/bathroom-heat-controls.js?v=1.0.23`
- `/local/bathroom-heat-energy.js?v=1.0.5`

## Advanced: existing YAML-managed dashboard resources

**Use this only if** your Home Assistant installation already has
`lovelace.resource_mode: yaml`. Do **not** switch resource modes or add a
second `lovelace:` key merely for these cards.

Add these entries under your existing `lovelace.resources` list in
`configuration.yaml`, preserving all other settings:

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /local/bathroom-heat-controls.js?v=1.0.23
      type: module
    - url: /local/bathroom-heat-energy.js?v=1.0.5
      type: module
```

Merge this into existing YAML; don't replace your other Lovelace resources.
Use `lovelace.reload_resources` or restart Home Assistant and reload the
browser. Normal UI-managed dashboards do **not** need this YAML.

See Home Assistant's
[resource-registration documentation](https://contributors.home-assistant.io/frontend/custom-ui/resources/).

## Original Schluter integration / rollback

For the older, original `schluter` integration, use that integration's actual
climate entity and change the card's `service_domain:` entries to `schluter`.
The new integration's examples intentionally use
`service_domain: schluter_heat_extended`.

**Rollback:** Back up your original card YAML, `/config/www/` files, and
existing resource URLs before updating them. To revert a dashboard change,
restore those files and YAML and reset the resource version URLs. Dashboard
files do not control energy-statistics history or the thermostat login.

These dashboard files are source-tested but still require hands-on validation
of Adjust and Schedule Save operations on a non-production installation before
using them routinely.
