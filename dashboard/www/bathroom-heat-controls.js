/* Bathroom floor heat: browser-local drafts. No storage, helpers, or API client. */
(() => {
  "use strict";
  if (window.bathroomHeatControls) return;

  const VERSION = "1.0.23";
  // Browser Mod wraps each entry in :host. These variables belong only to our
  // two popup instances, including their adaptive bottom sheets and pickers.
  // The same card can target the original or extended integration.
  // Existing production cards without service_domain keep the old default.
  function requireHeatConfig(config) {
    if (!config || typeof config.entity !== "string" ||
        !config.entity.startsWith("climate.") ||
        !/^[a-z0-9_]+$/.test(config.entity.slice(8)))
      throw new Error("Select a valid climate entity for bathroom heat.");
    const domain = config.service_domain ?? "schluter";
    if (!["schluter", "schluter_heat_extended"].includes(domain))
      throw new Error("Choose a supported Schluter service_domain.");
    return {...config, service_domain: domain};
  }
  const POPUP_LIGHT_SURFACE = `
    color-scheme: light;
    color: #202124;
    --ha-dialog-surface-background: #f5f5f5 !important;
    --ha-dialog-surface-backdrop-filter: none !important;
    --ha-bottom-sheet-surface-background: #f5f5f5 !important;
    --ha-bottom-sheet-surface-backdrop-filter: none !important;
    --ha-card-background: #f5f5f5;
    --card-background-color: #f5f5f5;
    --popup-background-color: #f5f5f5;
    --popup-header-background-color: #f5f5f5;
    --primary-background-color: #f5f5f5;
    --secondary-background-color: #ffffff;
    --primary-text-color: #202124;
    --secondary-text-color: #50555a;
    --text-primary-color: #ffffff;
    --disabled-text-color: #5f6368;
    --disabled-color: #e2e4e7;
    --divider-color: #c5c7ca;
    --primary-color: #0069b4;
    --success-color: #246b30;
    --warning-color: #8a4b00;
    --info-color: #005b96;
    --error-color: #b3261e;
    --ha-color-surface-default: #f5f5f5;
    --ha-color-surface-raised: #ffffff;
    --ha-color-form-background: #ffffff;
    --ha-color-form-background-disabled: #e2e4e7;
    --ha-color-text-primary: #202124;
    --ha-color-text-secondary: #50555a;
    --ha-color-on-neutral-normal: #202124;
    --ha-color-on-primary-quiet: #005b96;
    --ha-color-border-neutral-loud: #9aa0a6;
    --ha-color-fill-primary-quiet-resting: #dcecff;
    --ha-color-fill-primary-quiet-hover: #c8e2ff;
    --ha-color-fill-primary-loud-resting: #0069b4;
    --ha-color-fill-primary-loud-hover: #005a9b;
    --ha-color-fill-primary-loud-active: #004c83;
    --ha-color-on-primary-loud: #ffffff;
    --ha-color-fill-neutral-normal-hover: #d5d8dc;
    --ha-color-fill-neutral-normal-active: #c5c7ca;
    --ha-color-fill-disabled-normal-resting: #e2e4e7;
    --ha-color-fill-disabled-loud-resting: #e2e4e7;
    --ha-color-on-disabled-normal: #5f6368;
    --ha-color-on-disabled-loud: #5f6368;
    --wa-color-surface-default: #f5f5f5;
    --wa-color-surface-raised: #ffffff;
    --wa-color-text-normal: #202124;
    --wa-color-text-quiet: #50555a;
    --wa-color-brand-fill-loud: #0069b4;
    --wa-color-brand-on-loud: #ffffff;
    --wa-color-neutral-fill-normal: #e5e7eb;
    --wa-color-neutral-on-normal: #202124;
    --wa-color-neutral-on-quiet: #202124;
    --wa-form-control-background-color: #ffffff;
    --wa-form-control-border-color: #9aa0a6;
    --wa-form-control-value-color: #202124;
    --wa-form-control-label-color: #50555a;
    --input-fill-color: #ffffff;
    --input-ink-color: #202124;
    --input-label-ink-color: #50555a;
    --input-dropdown-icon-color: #50555a;
    --ha-button-height: 44px;
  `;
  const popupStyles = () => [{style: "all", styles: POPUP_LIGHT_SURFACE}];

  function scopePickerDialog(event, parent) {
    const detail = event.detail;
    if (!parent?.shadowRoot || !detail || typeof detail.dialogTag !== "string" || detail.parentElement) return;
    // HA's supported parentElement option keeps picker dialogs in this popup's
    // theme scope. Leave import, callbacks, history and event propagation intact.
    try { detail.parentElement = parent; } catch (_) { /* An immutable request is left alone. */ }
  }
  const CONFIRMATION_TIMEOUT_MS = 120000;
  const CONFIRMATION_TIMEOUT_MESSAGE = "The adjustment was not confirmed before its confirmation deadline. Showing the reported target; check the thermostat before retrying.";
  const TARGET_TOLERANCE_F = 0.06;
  const TAG = "bath-heat-adjust";
  const controllers = new Map();
  const submitting = new Set();
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const pad = (value) => String(value).padStart(2, "0");
  const dateText = (p) => `${p.year}-${p.month}-${p.day}`;
  const timeText = (p) => `${p.hour}:${p.minute}`;
  const wallUTC = (p) => Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);

  function clock(zone) {
    const formatter = new Intl.DateTimeFormat("en-GB", {
      timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "short",
    });
    return {
      formatter,
      parts: (ms) => Object.fromEntries(formatter.formatToParts(new Date(ms))
        .filter((part) => part.type !== "literal").map((part) => [part.type, part.value])),
    };
  }

  function calendar(date, time) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || "") ||
        !/^(?:[01]\d|2[0-3]):[0-5]\d(?::00)?$/.test(time || ""))
      throw new Error("Choose a valid date and time, with no seconds.");
    const [y, m, d] = date.split("-").map(Number);
    const [h, n] = time.split(":").map(Number);
    const ms = Date.UTC(y, m - 1, d, h, n);
    const check = new Date(ms);
    if (y < 2000 || check.getUTCFullYear() !== y ||
        check.getUTCMonth() !== m - 1 || check.getUTCDate() !== d)
      throw new Error("The selected date is invalid.");
    return ms;
  }

  function resolveLocal(date, time, zone) {
    const wall = calendar(date, time);
    const {parts} = clock(zone);
    const matches = new Set();
    // Sample both sides of a timezone transition, then round-trip candidates.
    for (let hours = -36; hours <= 36; hours += 6) {
      const sample = wall + hours * 3600000;
      const candidate = wall - (wallUTC(parts(sample)) - sample);
      const p = parts(candidate);
      if (dateText(p) === date && timeText(p) === time.slice(0, 5)) matches.add(candidate);
    }
    if (matches.size !== 1) throw new Error(matches.size
      ? "That local time occurs twice during a clock change. Choose another time."
      : "That local time does not exist during a clock change. Choose another time.");
    return [...matches][0];
  }

  function reportedExpiry(value) {
    if (typeof value !== "string") return NaN;
    const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::00(?:\.0{1,6})?)?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match) return NaN;
    try { calendar(match[1], match[2]); } catch (_) { return NaN; }
    const ms = Date.parse(value);
    return Number.isFinite(ms) && ms % 60000 === 0 ? ms : NaN;
  }

  class DraftController {
    constructor(entity, serviceDomain) {
      this.serviceDomain = serviceDomain;
      this.entity = entity;
      this.views = new Set();
      this.hass = null;
      this.draft = null;
      this.fingerprint = null;
      this.session = null;
      this.generation = 0;
      this.pending = false;
      this.confirmation = null;
      this.opening = false;
      this.closing = Promise.resolve();
      this.bm = null;
      this.connection = null;
      this.onClosed = (event) => {
        if (event.detail?.popup?.tag === TAG && this.session) this.reset(false);
      };
      this.onOpened = (event) => {
        const popup = event.detail?.popup;
        if (popup?.tag !== TAG || !this.session) return;
        clearTimeout(this.openTimer);
        this.session.popup = popup;
        this.session.form = popup.content;
        const session = this.session;
        session.onValues = (ev) => {
          if (this.session !== session || this.pending) return;
          const value = ev.detail?.value;
          if (!value || typeof value !== "object") return;
          session.values = {...value};
          const target = value.target === "" || value.target == null ? NaN : Number(value.target);
          const state = this.state();
          if (finite(target) && target >= state.min && target <= state.max) {
            this.draft = target;
            this.render();
          }
        };
        session.form?.addEventListener("value-changed", session.onValues);
        session.onPickerDialog = (ev) => {
          if (this.session === session) scopePickerDialog(ev, session.popup);
        };
        session.form?.addEventListener("show-dialog", session.onPickerDialog);
      };
      this.onDisconnected = () => {
        this.clearConfirmation("Connection lost before the adjustment was confirmed. Showing the reported thermostat state.");
        this.reset(true);
      };
    }

    state() {
      const entity = this.hass?.states?.[this.entity];
      const a = entity?.attributes || {};
      const unit = this.hass?.config?.unit_system?.temperature;
      const zone = this.hass?.config?.time_zone;
      return {
        available: !!entity && !["unknown", "unavailable"].includes(entity.state) &&
          this.hass?.connected !== false && this.connection?.connected !== false &&
          finite(a.temperature) && finite(a.min_temp) && finite(a.max_temp) && a.min_temp <= a.max_temp &&
          unit === "°F" && typeof zone === "string" && !!zone,
        target: a.temperature, min: a.min_temp, max: a.max_temp,
        active: a.override_active, until: a.override_until, unit, zone,
      };
    }

    update(hass) {
      this.hass = hass;
      if (this.connection !== hass.connection) {
        this.connection?.removeEventListener?.("disconnected", this.onDisconnected);
        this.connection = hass.connection;
        this.connection?.addEventListener?.("disconnected", this.onDisconnected);
      }
      this.bindBrowserMod();
      const state = this.state();
      const next = JSON.stringify([state.available, state.target, state.active,
        state.until, state.unit, state.zone]);
      const changed = this.fingerprint !== null && this.fingerprint !== next;
      this.fingerprint = next;
      const confirmationStatus = this.reconcileConfirmation();
      if ((!state.available || changed) && !this.pending && !this.confirmation &&
          confirmationStatus !== "confirmed" &&
          (this.draft !== null || this.session)) {
        this.reset(true);
        if (state.available) this.notify("Thermostat settings changed. The draft was discarded.");
      }
      this.render();
    }

    bindBrowserMod() {
      const bm = window.browser_mod;
      if (this.bm === bm) return;
      this.bm?.removeEventListener("browser-mod-popup-opened", this.onOpened);
      this.bm?.removeEventListener("browser-mod-popup-closed", this.onClosed);
      this.bm = bm;
      bm?.addEventListener("browser-mod-popup-opened", this.onOpened);
      bm?.addEventListener("browser-mod-popup-closed", this.onClosed);
    }

    notify(message) {
      if (typeof this.bm?.service === "function")
        this.bm.service("browser_mod.notification", {message, duration: 10000});
      else this.views.values().next().value?.dispatchEvent(new CustomEvent("hass-notification", {
        bubbles: true, composed: true, detail: {message},
      }));
    }

    requireAvailable() {
      const state = this.state();
      if (!state.available) throw new Error(
        "The thermostat is unavailable, or this Fahrenheit dashboard's units/timezone are missing."
      );
      return state;
    }

    clearConfirmation(message) {
      if (!this.confirmation) return;
      clearTimeout(this.confirmationTimer);
      this.confirmation = null;
      if (message) this.notify(message);
    }

    reconcileConfirmation(force = false) {
      const confirmation = this.confirmation;
      if (!confirmation || (this.pending && !force)) return;
      const state = this.state();
      const matches = (value) => finite(value) &&
        Math.abs(state.target - value) <= TARGET_TOLERANCE_F;
      if (!state.available || state.unit !== confirmation.unit || state.zone !== confirmation.zone) {
        this.clearConfirmation("Thermostat confirmation became unavailable. Showing the reported thermostat state.");
        return "failed";
      }
      if (matches(confirmation.target)) {
        this.clearConfirmation();
        return "confirmed";
      }
      if (Date.now() >= confirmation.deadline) {
        this.clearConfirmation(CONFIRMATION_TIMEOUT_MESSAGE);
        return "failed";
      }
      if (!confirmation.staleTargets.some(matches)) {
        this.clearConfirmation("The thermostat reported a different target before confirmation. Showing its reported target.");
        return "failed";
      }
      return "waiting";
    }

    awaitConfirmation(target, expiresAt, state, staleTargets) {
      this.clearConfirmation();
      this.confirmation = {
        target, unit: state.unit, zone: state.zone, staleTargets,
        deadline: Math.min(Date.now() + CONFIRMATION_TIMEOUT_MS,
          expiresAt === null ? Infinity : Date.parse(expiresAt)),
      };
      // The setter already refreshed HA; it may have confirmed before returning.
      this.reconcileConfirmation(true);
      if (this.confirmation) {
        const confirmation = this.confirmation;
        this.confirmationTimer = setTimeout(() => {
          if (this.confirmation !== confirmation) return;
          this.reconcileConfirmation(true);
          // A backwards system-clock adjustment must not prolong optimism forever.
          if (this.confirmation === confirmation) this.clearConfirmation(CONFIRMATION_TIMEOUT_MESSAGE);
          this.render();
        }, Math.max(0, confirmation.deadline - Date.now()));
      }
    }

    step(amount) {
      try {
        if (submitting.has(this.entity)) throw new Error("An adjustment is still being submitted. Please wait.");
        if (amount !== 1 && amount !== -1) return;
        this.reconcileConfirmation();
        const state = this.requireAvailable();
        const value = this.draft === null ? (this.confirmation?.target ?? state.target) : this.draft;
        this.draft = Math.max(state.min, Math.min(state.max,
          Math.round((value + amount) * 1000000) / 1000000));
        // Update only the target field; never replace the chosen expiration.
        const form = this.session?.form;
        if (form) {
          const values = {...form.data, target: this.draft};
          form.data = values;
          this.session.values = values;
          form.dispatchEvent(new CustomEvent("value-changed", {detail: {value: values}}));
        }
        this.render();
      } catch (error) { this.notify(error.message); }
    }

    async open() {
      if (this.session || this.opening) return;
      try {
        if (submitting.has(this.entity)) throw new Error("An adjustment is still being submitted. Please wait.");
        this.opening = true;
        const openingGeneration = this.generation;
        await this.closing;
        if (!this.views.size || openingGeneration !== this.generation) return;
        this.reconcileConfirmation();
        const state = this.requireAvailable();
        this.bindBrowserMod();
        if (typeof this.bm?.service !== "function")
          throw new Error("Browser Mod must be loaded in this browser to open Adjust.");
        const now = Date.now();
        const {formatter, parts} = clock(state.zone);
        const expiry = state.active === true ? reportedExpiry(state.until) : NaN;
        const keep = finite(expiry) && expiry > now;
        const initial = parts(keep ? expiry : now + 3600000);
        // Initialize once per popup; option switches retain the form's clock value.
        const defaultClock = clock("America/New_York").parts(
          Math.ceil((now + 30 * 60000) / (15 * 60000)) * (15 * 60000));
        const times = Array.from({length: 96}, (_, i) => {
          const hour = Math.floor(i / 4), minute = pad(i % 4 * 15);
          const value = `${pad(hour)}:${minute}`;
          return {value, label: `${hour % 12 || 12}:${minute} ${hour < 12 ? "AM" : "PM"}`};
        });
        const options = [
          ...(keep ? [{value: "keep", label: "Keep current expiration: " +
            formatter.format(new Date(expiry)) + ` (${state.zone})`}] : []),
          {value: "next", label: "Until next scheduled event"},
          {value: "clock", label: "Until clock time (elapsed times mean tomorrow)"},
          {value: "date", label: "Until a specific date and time"},
        ];
        const values = {
          target: this.draft === null ? (this.confirmation?.target ?? state.target) : this.draft,
          ending: "clock",
          clock: timeText(defaultClock),
          date: dateText(initial), time: timeText(initial),
        };
        const token = ++this.generation;
        this.session = {token, state, values, popup: null, form: null};
        const field = (name, label, selector, required = false) => ({
          name, label, selector, required, default: values[name],
        });
        this.bm.service("browser_mod.popup", {
          tag: TAG, title: "Adjust bathroom floor heat", adaptive: true,
          popup_styles: popupStyles(),
          right_button: "Apply", right_button_close: false,
          right_button_variant: "brand", right_button_appearance: "accent",
          left_button: "Cancel", left_button_close: true,
          left_button_variant: "neutral", left_button_appearance: "filled",
          dismissable: true,
          content: [
            field("target", `Target temperature (${state.unit})`, {
              number: {min: state.min, max: state.max, step: 1, mode: "box"},
            }, true),
            field("ending", "End adjustment", {select: {mode: "dropdown", options}}, true),
            {
              ...field("clock", "Ending clock time", {
                select: {mode: "dropdown", options: times},
              }),
              visible: {field: "ending", operator: "eq", value: "clock"},
            },
            {
              ...field("date", "Ending date", {date: {}}),
              visible: {field: "ending", operator: "eq", value: "date"},
            },
            {
              ...field("time", "Ending time", {time: {no_second: true}}),
              visible: {field: "ending", operator: "eq", value: "date"},
            },
          ],
          right_button_action: {
            service: "browser_mod.javascript",
            data: {draft_entity: this.entity, draft_token: token,
              code: "window.bathroomHeatControls.apply(data.draft_entity, data.draft_token, data, hass);"},
          },
        });
        this.openTimer = setTimeout(() => {
          if (this.session?.token === token && !this.session.popup) {
            this.reset(true);
            this.notify("The Adjust popup did not open. Check Browser Mod in this browser.");
          }
        }, 10000);
      } catch (error) {
        this.reset(true);
        this.notify(error.message || "The Adjust popup could not be opened.");
      } finally { this.opening = false; }
    }

    expiration(data, session) {
      const now = Date.now();
      let expiry;
      if (data.ending === "keep") {
        expiry = reportedExpiry(session.state.until);
        if (session.state.active !== true) expiry = NaN;
      } else if (data.ending === "clock") {
        if (!/^(?:[01]\d|2[0-3]):(?:00|15|30|45)$/.test(data.clock || ""))
          throw new Error("Choose an ending clock time in 15-minute increments.");
        const today = clock(session.state.zone).parts(now);
        let date = dateText(today);
        if (data.clock <= timeText(today)) date = new Date(Date.UTC(
          +today.year, +today.month - 1, +today.day + 1)).toISOString().slice(0, 10);
        expiry = resolveLocal(date, data.clock, session.state.zone);
      } else if (data.ending === "date") {
        expiry = resolveLocal(data.date, data.time, session.state.zone);
      } else if (data.ending === "next") return null;
      else throw new Error("Choose when the adjustment should end.");
      if (!finite(expiry) || expiry <= now)
        throw new Error("The expiration must be in the future. Choose a new ending time.");
      return new Date(expiry).toISOString();
    }

    async apply(token, data, hass) {
      if (submitting.has(this.entity) || !this.session || this.session.token !== token) return;
      try {
        // A callback can carry newer HA state than the card's most recent render.
        if (hass) this.update(hass);
        if (!this.session || this.session.token !== token) return;
        const state = this.requireAvailable();
        if (data.target == null || data.target === "" || typeof data.target === "boolean")
          throw new Error("Enter a target temperature.");
        const temperature = Number(data.target);
        if (!finite(temperature) || temperature < state.min || temperature > state.max)
          throw new Error(`Target must be between ${state.min} and ${state.max} °F.`);
        const expiresAt = this.expiration(data, this.session);
        const appliedSession = this.session;
        const staleTargets = [state.target, this.confirmation?.target].filter(finite);
        this.pending = true;
        submitting.add(this.entity);
        this.render();
        // Only Apply and Return to Schedule call a thermostat action.
        if (expiresAt === null) await this.hass.callService("climate", "set_temperature",
          {temperature}, {entity_id: this.entity});
        else await this.hass.callService(this.serviceDomain, "set_temporary_temperature",
          {temperature, expires_at: expiresAt}, {entity_id: this.entity});
        // Closing/navigating away during submission must not recreate a draft display.
        if (this.session !== appliedSession || controllers.get(this.entity) !== this) return;
        this.awaitConfirmation(temperature, expiresAt, state, staleTargets);
        this.reset(true);
        const actual = this.state();
        const reportedEnd = reportedExpiry(actual.until);
        if (this.confirmation)
          this.notify("Request acknowledged. Thermostat confirmation is still pending; the underlined target is the requested temperature.");
        else if (actual.available && Math.abs(actual.target - temperature) <= TARGET_TOLERANCE_F &&
            expiresAt !== null && (actual.active !== true || reportedEnd !== Date.parse(expiresAt)))
          this.notify("Temperature confirmed; override expiration is still awaiting cloud confirmation.");
      } catch (error) {
        this.notify(error.message || "The adjustment could not be applied. Check the thermostat before retrying.");
      } finally {
        // Validation errors must not release another in-flight command's lock.
        if (this.pending) submitting.delete(this.entity);
        this.pending = false;
        this.render();
        if (controllers.get(this.entity) !== this) controllers.get(this.entity)?.render();
      }
    }

    async returnToSchedule() {
      if (submitting.has(this.entity)) {
        this.notify("An adjustment is still being submitted. Please wait.");
        return;
      }
      try {
        const state = this.requireAvailable();
        this.reset(true, true);
        if (state.active !== true) return;
        this.pending = true;
        submitting.add(this.entity);
        this.render();
        await this.hass.callService(this.serviceDomain, "return_to_schedule", {}, {entity_id: this.entity});
      } catch (error) { this.notify(error.message || "Return to Schedule could not be completed."); }
      finally {
        if (this.pending) submitting.delete(this.entity);
        this.pending = false;
        this.render();
        if (controllers.get(this.entity) !== this) controllers.get(this.entity)?.render();
      }
    }

    reset(closePopup, discardConfirmation = false) {
      const session = this.session;
      this.generation++;
      this.session = null;
      this.draft = null;
      if (discardConfirmation) this.clearConfirmation();
      clearTimeout(this.openTimer);
      if (session?.form && session.onValues)
        session.form.removeEventListener("value-changed", session.onValues);
      if (session?.form && session.onPickerDialog)
        session.form.removeEventListener("show-dialog", session.onPickerDialog);
      if (closePopup && session && typeof this.bm?.closePopup === "function") {
        this.closing = Promise.resolve(this.bm.closePopup({tag: TAG})).catch(() => {
          this.notify("The draft was discarded. Close the Adjust popup if it remains visible.");
        });
      }
      this.render();
    }

    render() {
      this.reconcileConfirmation();
      const state = this.state();
      const value = state.available ?
        (this.draft === null ? (this.confirmation?.target ?? state.target) : this.draft) : null;
      for (const view of this.views) view.show(value, this.draft !== null,
        submitting.has(this.entity), this.draft === null && this.confirmation !== null);
    }

    destroy() {
      this.reset(true, true);
      this.connection?.removeEventListener?.("disconnected", this.onDisconnected);
      this.bm?.removeEventListener("browser-mod-popup-opened", this.onOpened);
      this.bm?.removeEventListener("browser-mod-popup-closed", this.onClosed);
      controllers.delete(this.entity);
    }
  }

  // Optional display-only helper for Picture Elements cards. The Home
  // Assistant picture-elements card renders overlays inside #root. Observing
  // that root means font sizes are percentages of the faceplate's displayed
  // width, NOT the viewport or the browser's base font size.
  //
  // The ResizeObserver is disconnected when the card is removed, and this
  // helper never calls thermostat services or touches a controller draft.
  class BathroomHeatScaler extends HTMLElement {
    setConfig(config = {}) {
      const factor = config.font_scale === undefined ? 1 : config.font_scale;
      if (!finite(factor) || factor < 0.75 || factor > 1.35) {
        throw new Error("font_scale must be between 0.75 and 1.35.");
      }
      this.fontScale = factor;
      if (this.isConnected) this.resize();
    }
    connectedCallback() {
      this.style.display = "none";
      // Picture Elements has its OWN #root inside the card. Selecting the
      // first #root in the document can accidentally choose HA's outer app
      // container, leaving compact text enormous in a two-column grid.
      // Always start with the containing/nearest Picture Elements root.
      const ancestor = this.closest("#root");
      const rootCandidate = ancestor || this.getRootNode()?.querySelector?.("#root");
      this.faceplate = rootCandidate?.contains?.(this) ? rootCandidate : null;
      if (!this.faceplate) return;
      if (typeof ResizeObserver === "function") {
        this.resizeObserver = new ResizeObserver(() => this.resize());
        this.resizeObserver.observe(this.faceplate);
      } else {
        this.onWindowResize = () => this.resize();
        window.addEventListener("resize", this.onWindowResize);
      }
      this.resize();
    }
    disconnectedCallback() {
      this.resizeObserver?.disconnect();
      this.resizeObserver = null;
      if (this.onWindowResize) {
        window.removeEventListener("resize", this.onWindowResize);
        this.onWindowResize = null;
      }
      this.faceplate = null;
    }
    resize() {
      const width = this.faceplate?.getBoundingClientRect?.().width;
      if (!finite(width) || width <= 0) return;
      const fontScale = this.fontScale ?? 1;
      const pixels = (proportion, minimum, maximum, isFont = true) =>
        Math.min(maximum, Math.max(minimum, width * proportion * (isFont ? fontScale : 1)));
      const sizes = {
        "clock": [0.029, 9, 18],
        "current": [0.029, 9, 18],
        "target": [0.076, 21, 50],
        "mode": [0.027, 9, 17],
        "action": [0.026, 9, 16],
        "next": [0.023, 9, 15],
        "button": [0.027, 9, 18],
        "schedule-height": [0.086, 26, 44, false],
        "arrow-width": [0.087, 28, 54, false],
        "arrow-height": [0.048, 18, 30, false],
        "arrow-icon": [0.038, 13, 24, false],
        "arrow-padding": [0.007, 2, 5, false],
      };
      for (const [name, spec] of Object.entries(sizes)) {
        this.faceplate.style.setProperty(
          `--bath-heat-${name}`, `${pixels(...spec).toFixed(2)}px`,
        );
      }
    }
  }

  class BathroomHeatTarget extends HTMLElement {
    setConfig(config) {
      if (!config || typeof config.entity !== "string" || !config.entity.startsWith("climate.") || !/^[a-z0-9_]+$/.test(config.entity.slice(8)))
        throw new Error("Bathroom heat controls require a valid configured climate entity.");
      this.config = requireHeatConfig(config);
      if (this.isConnected) this.attach();
    }
    set hass(value) {
      this.latestHass = value;
      if (this.isConnected && this.config) this.attach();
      this.controller?.update(value);
    }
    connectedCallback() { if (this.config) this.attach(); }
    attach() {
      if (this.controller) return;
      const entity = this.config.entity;
      if (!controllers.has(entity)) controllers.set(entity, new DraftController(entity, this.config.service_domain));
      else if (controllers.get(entity).serviceDomain !== this.config.service_domain)
        throw new Error("This climate entity is configured with conflicting Schluter service domains.");
      this.controller = controllers.get(entity);
      this.controller.views.add(this);
      this.style.whiteSpace = "nowrap";
      if (this.latestHass) this.controller.update(this.latestHass);
      else this.controller.render();
    }
    disconnectedCallback() {
      if (!this.controller) return;
      const controller = this.controller;
      this.controller = null;
      controller.views.delete(this);
      if (!controller.views.size) controller.destroy();
    }
    show(value, draft, pending, confirming) {
      this.textContent = value === null ? "—" : new Intl.NumberFormat(
        this.latestHass?.locale?.language || "en-US", {maximumFractionDigits: 1}).format(value);
      this.title = pending ? "Submitting adjustment; waiting for thermostat state" :
        draft ? "Draft target — press Adjust to apply" : confirming ?
          "Requested target — awaiting thermostat confirmation (up to 2 minutes)" : "Reported thermostat target";
      this.style.textDecoration = confirming ? "underline dotted" : "";
      this.style.textUnderlineOffset = confirming ? "0.12em" : "";
      this.style.textDecorationThickness = confirming ? "1px" : "";
      this.setAttribute("aria-label", this.title + ": " + this.textContent + " °F");
      this.setAttribute("data-draft", String(draft));
      this.setAttribute("data-confirmation-pending", String(confirming));
    }
    getCardSize() { return 1; }
  }

  function statusDateTime(value) {
    if (typeof value !== "string") return null;
    const match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})(?::[0-5]\d(?:\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if (!match) return null;
    try {
      calendar(match[1], match[2]);
      const ms = Date.parse(value);
      if (!finite(ms)) return null;
      const formatter = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York", year: "numeric", month: "long", day: "numeric",
        hour: "2-digit", minute: "2-digit", hour12: true,
      });
      const p = Object.fromEntries(formatter.formatToParts(new Date(ms))
        .filter((part) => part.type !== "literal").map((part) => [part.type, part.value]));
      return `${p.month} ${p.day}, ${p.year} at ${p.hour}:${p.minute} ${p.dayPeriod}`;
    } catch (_) { return null; }
  }

  function nextChangeText(attributes, now = Date.now()) {
    const value = attributes.next_scheduled_change;
    if (!statusDateTime(value)) return null;
    const ms = Date.parse(value);
    if (!finite(now) || ms <= now) return null;
    let fahrenheit = attributes.next_scheduled_temperature;
    if (finite(fahrenheit) && attributes.next_scheduled_temperature_unit === "°C") fahrenheit = fahrenheit * 9 / 5 + 32;
    else if (attributes.next_scheduled_temperature_unit !== "°F") fahrenheit = null;
    const zone = "America/New_York";
    const {parts} = clock(zone);
    const eventDate = parts(ms), today = parts(now);
    // Compare calendar dates, not elapsed hours: DST days can be 23 or 25 hours.
    const dayNumber = (p) => Date.UTC(+p.year, +p.month - 1, +p.day) / 86400000;
    const days = dayNumber(eventDate) - dayNumber(today);
    const time = new Intl.DateTimeFormat("en-US", {
      timeZone: zone, hour: "numeric", minute: "2-digit", hour12: true,
    }).format(new Date(ms)).replace(/\s+/g, "");
    let when = "";
    if (days === 1) when = " Tomorrow";
    else if (days > 1) {
      const date = new Intl.DateTimeFormat("en-US", {
        timeZone: zone, weekday: "short", month: "short", day: "numeric",
        ...(eventDate.year !== today.year ? {year: "numeric"} : {}),
      }).format(new Date(ms));
      when = ` ${date}`;
    }
    const target = finite(fahrenheit) ? `${Math.round(fahrenheit)}°F` : "temperature unavailable";
    return `Next Change: ${target}${when} at ${time}`;
  }

  function currentDateTime(now = Date.now(), compact = false) {
    const p = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric",
      hour: "numeric", minute: "2-digit", hour12: true,
    }).formatToParts(new Date(now)).filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]));
    return compact ? `${p.month} ${p.day} · ${p.hour}:${p.minute}${p.dayPeriod}` :
      `${p.month} ${p.day}, ${p.year} - ${p.hour}:${p.minute}${p.dayPeriod}`;
  }

  // Find the schedule in effect AT the override expiry, not the first event after it.
  // Use reported schedule data only, with the setter's sixth-slot overnight rules.
  function temperatureAtExpiry(schedules, expiry) {
    try {
      if (!Array.isArray(schedules) || schedules.length !== 7 ||
          new Set(schedules.map((day) => day?.WeekDayGrpNo)).size !== 7 ||
          schedules.some((day) => !Number.isInteger(day?.WeekDayGrpNo) ||
            day.WeekDayGrpNo < 1 || day.WeekDayGrpNo > 7 ||
            !Array.isArray(day.Events) || day.Events.length !== 6 ||
            day.Events.some((event, index) => event?.ScheduleType !== index ||
              typeof event.Active !== "boolean" || !finite(event.TempFloor)))) return null;
      const {parts} = clock("America/New_York");
      const local = parts(expiry);
      const localWall = wallUTC(local);
      const midnight = Date.UTC(+local.year, +local.month - 1, +local.day);
      const candidates = [], gaps = [];
      // Two previous days cover the prior day's carried-over sixth event and
      // the last active event before today's first event.
      for (let offset = -2; offset <= 0; offset++) {
        const day = new Date(midnight + offset * 86400000);
        const weekday = day.getUTCDay() || 7;
        const events = schedules.find((record) => record.WeekDayGrpNo === weekday).Events;
        let previous = -Infinity;
        for (const [index, event] of events.entries()) {
          if (!event.Active) continue;
          const match = /^(\d{1,2}):([0-5]\d):00$/.exec(event.Clock);
          if (!match || +match[1] > 23) return null;
          const minutes = +match[1] * 60 + +match[2];
          // Same explicitly unsupported website edge as comfort_end_time().
          if (index === 5 && minutes > 180 && minutes < 240) return null;
          const wall = day.getTime() + minutes * 60000 +
            (index === 5 && minutes <= 180 ? 86400000 : 0);
          if (wall <= previous) return null;
          previous = wall;
          const instants = new Set();
          // Keep both fold possibilities. A later wall-clock event can already
          // have occurred when expiry is in the second repeated DST hour.
          for (let hours = -36; hours <= 36; hours += 6) {
            const sample = wall + hours * 3600000;
            const candidate = wall - (wallUTC(parts(sample)) - sample);
            if (wallUTC(parts(candidate)) === wall) instants.add(candidate);
          }
          if (!instants.size && wall <= localWall) gaps.push(wall);
          for (const at of instants) if (at <= expiry)
            candidates.push({at, wall, raw: instants.size === 1 ? event.TempFloor : null});
        }
      }
      candidates.sort((a, b) => b.at - a.at);
      if (!candidates.length || candidates[1]?.at === candidates[0].at) return null;
      const latest = candidates[0];
      // Ambiguous/nonexistent DST schedule events stay unknown instead of guessing.
      if (!finite(latest.raw) || gaps.some((wall) => wall >= latest.wall)) return null;
      return latest.raw / 100 * 9 / 5 + 32;
    } catch (_) { return null; }
  }

  // Read-only labels deliberately use reported HA attributes, never draft/confirmation state.
  class BathroomHeatStatus extends HTMLElement {
    setConfig(config) {
      if (!config || typeof config.entity !== "string" || !config.entity.startsWith("climate.") || !/^[a-z0-9_]+$/.test(config.entity.slice(8)) || !["mode", "expiration", "clock", "current"].includes(config.field))
        throw new Error("Bathroom heat status requires a valid configured climate entity and a supported display field.");
      this.config = requireHeatConfig(config);
      this.render();
    }
    set hass(value) { this.latestHass = value; this.render(); }
    connectedCallback() {
      this.style.whiteSpace = "nowrap"; this.statusAttached = true;
      this.onScheduleSaved = () => { this.scheduleCache = null; this.render(); };
      window.addEventListener("bathroom-heat-schedule-save-finished", this.onScheduleSaved);
      this.render(); this.refreshClock();
    }
    disconnectedCallback() {
      this.statusAttached = false; clearTimeout(this.statusTimer);
      this.scheduleCache = null;
      window.removeEventListener("bathroom-heat-schedule-save-finished", this.onScheduleSaved);
    }
    refreshClock() {
      clearTimeout(this.statusTimer);
      if (this.statusAttached) this.statusTimer = setTimeout(() => {
        this.render(); this.refreshClock();
      }, 60000 - Date.now() % 60000);
    }
    readSchedule(key) {
      if (!this.statusAttached || this.scheduleRequest || typeof this.latestHass?.callWS !== "function") return;
      const request = {};
      this.scheduleRequest = request;
      // Cache errors too, so a failed read cannot loop on coordinator updates.
      this.scheduleCache = {key, until: Date.now() + 60000, schedules: null};
      Promise.resolve().then(() => this.latestHass.callWS({
        type: "call_service", domain: this.config.service_domain, service: "get_schedule",
        target: {entity_id: this.config.entity}, service_data: {}, return_response: true,
      })).then((result) => {
        if (!this.statusAttached || this.scheduleCache?.key !== key) return;
        const body = result?.response?.[this.config.entity];
        this.scheduleCache = {key, until: Date.now() + 60000,
          schedules: body?.time_zone === (this.config.time_zone || "America/New_York") ? body.Schedules : null};
      }).catch(() => {
        // No raw exceptions: display the known expiry with temperature unavailable.
      }).finally(() => {
        if (this.scheduleRequest === request) this.scheduleRequest = null;
        if (this.statusAttached) this.render();
      });
    }
    overrideText(attributes) {
      const expiry = reportedExpiry(attributes.override_until);
      if (!finite(expiry) || expiry <= Date.now()) return "Next Change: unavailable";
      const target = {...attributes, next_scheduled_change: attributes.override_until};
      if (expiry !== Date.parse(attributes.next_scheduled_change)) {
        const key = JSON.stringify([attributes.override_until, attributes.next_scheduled_change,
          attributes.next_scheduled_temperature, attributes.next_scheduled_temperature_unit]);
        if (this.scheduleCache?.key !== key || this.scheduleCache.until <= Date.now()) this.readSchedule(key);
        target.next_scheduled_temperature = this.scheduleCache?.key === key &&
          this.scheduleCache.until > Date.now() ? temperatureAtExpiry(this.scheduleCache.schedules, expiry) : null;
        target.next_scheduled_temperature_unit = "°F";
      }
      return nextChangeText(target) || "Next Change: unavailable";
    }
    render() {
      if (!this.config) return;
      const entity = this.latestHass?.states?.[this.config.entity];
      const a = entity?.attributes || {};
      if (this.config.field === "clock") {
        this.textContent = currentDateTime(Date.now(), this.config.compact === true);
        this.title = "Current date and time (America/New_York)";
        return;
      }
      if (this.config.field === "current") {
        const available = entity && !["unknown", "unavailable"].includes(entity.state) && this.latestHass?.connected !== false;
        let value = available && finite(a.current_temperature) ? a.current_temperature : null;
        const unit = this.latestHass?.config?.unit_system?.temperature;
        if (value !== null && unit === "°C") value = value * 9 / 5 + 32;
        else if (unit !== "°F") value = null;
        this.textContent = `${this.config.compact === true ? "Floor" : "Current Temp"}: ${value === null ? "—" : new Intl.NumberFormat("en-US", {maximumFractionDigits: 1}).format(value)}°F`;
        this.title = "Reported current floor temperature";
        return;
      }
      let mode = "Status unknown", detail = "Date/time unavailable";
      if (!entity || ["unknown", "unavailable"].includes(entity.state) || this.latestHass?.connected === false) {
        mode = "Status unavailable";
      } else if (a.override_active === true) {
        mode = "Manual Override";
        detail = this.config.field === "expiration" ? this.overrideText(a) : "";
      } else {
        mode = "On Schedule";
        detail = nextChangeText(a) || "Next Change: unavailable";
      }
      // Small picture-elements cards cannot fit the original, no-wrap label.
      // Compact mode changes *display only*: full details remain in the tooltip.
      this.textContent = this.config.field === "mode" ? mode :
        this.config.compact === true && this.config.field === "expiration"
          ? detail.replace(/^Next Change:/, "Next:").replace(/ at /, " ")
          : detail;
      this.title = this.config.field === "mode" ? mode : `${mode}: ${detail} (America/New_York)`;
      this.setAttribute("aria-label", this.title);
    }
    getCardSize() { return 1; }
  }

  const SCHEDULE_TAG = "bath-heat-schedule";
  const scheduleSubmissions = new Set();

  function scheduleComparison(expected, reported) {
    try {
      const canonical = (value) => Array.isArray(value) ? value.map(canonical)
        : value && typeof value === "object" ? Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])])) : value;
      const copies = [expected, reported].map((week) => {
        if (!Array.isArray(week) || week.length !== 7 ||
            new Set(week.map((day) => day?.WeekDayGrpNo)).size !== 7) throw Error();
        const copy = JSON.parse(JSON.stringify(week));
        for (const day of copy) {
          if (!Number.isInteger(day?.WeekDayGrpNo) || day.WeekDayGrpNo < 1 || day.WeekDayGrpNo > 7 ||
              !Array.isArray(day.Events) || day.Events.length !== 6 ||
              new Set(day.Events.map((event) => event?.ScheduleType)).size !== 6) throw Error();
          for (const event of day.Events) {
            if (!Number.isInteger(event?.ScheduleType) || event.ScheduleType < 0 || event.ScheduleType > 5 ||
                !finite(event.TempFloor) || typeof event.Active !== "boolean") throw Error();
            scheduleSeconds(event.Clock);
          }
        }
        return copy;
      });
      if (JSON.stringify(canonical(copies[0])) === JSON.stringify(canonical(copies[1]))) return "exact";
      for (const week of copies) for (const day of week) for (const event of day.Events)
        event.Clock = scheduleSeconds(event.Clock);
      return JSON.stringify(canonical(copies[0])) === JSON.stringify(canonical(copies[1])) ? "normalized" : null;
    } catch (_) { return null; }
  }

  function scheduleSaveError(error) {
    // Display only our own fixed safe messages; never arbitrary websocket details.
    const safe = [
      "The schedule changed elsewhere. Close and reopen the editor before saving; your draft has been retained.",
      "A Schluter schedule save is already in progress.",
      "Could not read the latest Schluter schedule and limits. No schedule was sent.",
      "A complete seven-day, six-event schedule with valid values is required.",
      "Schluter schedule validation limits are unavailable. No schedule was sent.",
      "Schedule day identifiers, ordering and unknown fields must be preserved.",
      "Schedule event identifiers, ordering and unknown fields must be preserved.",
      "Edited schedule times must use 15-minute increments.",
      "A schedule temperature is outside the thermostat limits.",
      "Event 1 must remain active on every day.",
      "An active event time is outside Schluter's limits for that event.",
      "Active schedule events must be at least 15 minutes apart.",
      "No active Schluter session is available for saving the schedule.",
      "The Schluter schedule request could not be sent.",
      "Schluter rejected the schedule save.",
    ];
    if (safe.includes(error?.message)) return `${error.message} Your draft is retained.`;
    const status = /^Schluter schedule request returned HTTP (\d{3})\.$/.exec(error?.message || "");
    if (status) return `Schluter schedule request failed (HTTP ${status[1]}). Your draft is retained.`;
    return "The save result is unavailable; the request may have been delivered. Your draft is retained. Check confirmation before retrying.";
  }

  const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const SUNDAY_FIRST = [7, 1, 2, 3, 4, 5, 6];

  function currentScheduleDay(now = Date.now()) {
    const local = clock("America/New_York").parts(now);
    const day = new Date(Date.UTC(+local.year, +local.month - 1, +local.day)).getUTCDay();
    return day || 7; // Schluter: Monday=1, ..., Sunday=7.
  }


  function scheduleClock(value) {
    const match = /^(\d{1,2}):([0-5]\d):([0-5]\d)$/.exec(value || "");
    if (!match || +match[1] > 23) throw new Error("Invalid schedule time");
    const hour = +match[1];
    return `${hour % 12 || 12}:${match[2]}${match[3] === "00" ? "" : ":" + match[3]} ${hour < 12 ? "AM" : "PM"}`;
  }

  function scheduleSeconds(clock) {
    scheduleClock(clock);
    const [hour, minute, second] = clock.split(":").map(Number);
    return hour * 3600 + minute * 60 + second;
  }

  function scheduleLimits(value) {
    if (!value || !finite(value.MinTemp) || !finite(value.MaxTemp) || value.MinTemp >= value.MaxTemp)
      return null;
    const result = {MinTemp: value.MinTemp, MaxTemp: value.MaxTemp};
    for (const key of ["MinTimeLimits", "MaxTimeLimits"]) {
      const entries = value[key];
      if (!Array.isArray(entries) || entries.length !== 6 ||
          new Set(entries.map((v) => v?.ScheduleType)).size !== 6 ||
          entries.some((v) => !Number.isInteger(v?.ScheduleType) || v.ScheduleType < 0 ||
            v.ScheduleType > 5 || !Number.isSafeInteger(v.Clock) || v.Clock < 0)) return null;
      result[key] = Object.fromEntries(entries.map((v) => [v.ScheduleType, v.Clock]));
    }
    if (Object.keys(result.MinTimeLimits).some((slot) =>
        result.MinTimeLimits[slot] > result.MaxTimeLimits[slot])) return null;
    // There must be at least one whole-Fahrenheit choice within the native limits.
    if (Math.ceil(value.MinTemp / 100 * 9 / 5 + 32) >
        Math.floor(value.MaxTemp / 100 * 9 / 5 + 32)) return null;
    return result;
  }

  class BathroomHeatSchedule extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({mode: "open"});
      this.selectedDay = 1;
      this.ticket = 0;
      this.active = false;
      this.attempted = false;
      this.loading = false;
      this.schedules = null;
      this.draftSchedules = null;
      this.limits = null;
      this.editError = null;
      this.error = null;
      this.saving = false;
      this.saveMessage = null;
      this.saveOutcome = "idle";
      this.saveStatusLabel = null;
      this.dismissButton = null;
      this.pendingSnapshot = null;
      this.pendingAcknowledged = false;
      this.dirtyLabel = null;
      this.errorList = null;
      this.saveButton = null;
      this.copyState = null;
      this.copyMessage = null;
      this.copyDialog = null;
      this.copyControls = null;
      this.copyButton = null;
      this.onSaveFinished = () => { if (this.active) this.render(); };
      this.onPopupClosed = (event) => {
        if (event.detail?.popup?.tag === SCHEDULE_TAG) this.stop();
      };
    }
    setConfig(config) {
      if (!config || typeof config.entity !== "string" || !config.entity.startsWith("climate.") || !/^[a-z0-9_]+$/.test(config.entity.slice(8)))
        throw new Error("The bathroom schedule viewer requires a valid configured climate entity.");
      this.config = requireHeatConfig(config);
      this.render();
      void this.load();
    }
    set hass(value) { this.latestHass = value; void this.load(); }
    connectedCallback() {
      if (!this.active) this.selectedDay = currentScheduleDay();
      this.active = true;
      window.addEventListener("bathroom-heat-schedule-save-finished", this.onSaveFinished);
      this.browserMod = window.browser_mod;
      this.browserMod?.addEventListener("browser-mod-popup-closed", this.onPopupClosed);
      this.render();
      void this.load();
    }
    disconnectedCallback() { this.stop(); }
    stop() {
      this.active = false;
      window.removeEventListener("bathroom-heat-schedule-save-finished", this.onSaveFinished);
      this.browserMod?.removeEventListener("browser-mod-popup-closed", this.onPopupClosed);
      this.ticket++;
      this.attempted = false;
      this.loading = false;
      this.schedules = null;
      this.draftSchedules = null;
      this.limits = null;
      this.editError = null;
      this.error = null;
      this.saving = false;
      this.saveMessage = null;
      this.saveOutcome = "idle";
      this.saveStatusLabel = null;
      this.dismissButton = null;
      this.pendingSnapshot = null;
      this.pendingAcknowledged = false;
      this.dirtyLabel = null;
      this.errorList = null;
      this.saveButton = null;
      this.copyState = null;
      this.copyMessage = null;
      this.copyDialog = null;
      this.copyControls = null;
      this.copyButton = null;
      this.shadowRoot.replaceChildren();
    }
    async load() {
      if (!this.active || !this.config || !this.latestHass || this.attempted) return;
      this.attempted = true;
      this.loading = true;
      const ticket = ++this.ticket;
      this.render();
      try {
        const result = await this.latestHass.callWS({
          type: "call_service", domain: this.config.service_domain, service: "get_schedule",
          target: {entity_id: this.config.entity}, service_data: {}, return_response: true,
        });
        if (!this.active || ticket !== this.ticket) return;
        const body = result?.response?.[this.config.entity];
        const schedules = body?.Schedules;
        if (!Array.isArray(schedules) || schedules.length !== 7 ||
            new Set(schedules.map((d) => d?.WeekDayGrpNo)).size !== 7 ||
            schedules.some((day) => !Number.isInteger(day?.WeekDayGrpNo) ||
              day.WeekDayGrpNo < 1 || day.WeekDayGrpNo > 7 ||
              !Array.isArray(day.Events) || day.Events.length !== 6))
          throw new Error("Incomplete weekly schedule");
        for (const day of schedules) {
          if (new Set(day.Events.map((e) => e?.ScheduleType)).size !== 6 ||
              day.Events.some((event) => !Number.isInteger(event?.ScheduleType) ||
                event.ScheduleType < 0 || event.ScheduleType > 5 ||
                !finite(event.TempFloor) || typeof event.Active !== "boolean"))
            throw new Error("Invalid event data");
          for (const event of day.Events) scheduleClock(event.Clock);
        }
        // Keep the raw centi-Celsius values/identifiers; conversion is display-only.
        this.schedules = JSON.parse(JSON.stringify(schedules));
        this.draftSchedules = JSON.parse(JSON.stringify(schedules));
        this.limits = scheduleLimits(body.limits);
        this.timeZone = typeof body.time_zone === "string" ? body.time_zone : "thermostat local time";
      } catch (_) {
        if (!this.active || ticket !== this.ticket) return;
        this.error = "Unable to load the complete weekly schedule. Close and reopen this viewer to retry.";
      } finally {
        if (this.active && ticket === this.ticket) { this.loading = false; this.render(); }
      }
    }
    get dirty() { return !!this.draftSchedules &&
      JSON.stringify(this.draftSchedules) !== JSON.stringify(this.schedules); }
    event(day, slot, original = false) {
      return (original ? this.schedules : this.draftSchedules)
        ?.find((d) => d.WeekDayGrpNo === day)?.Events.find((e) => e.ScheduleType === slot);
    }
    edit(day, slot, field, value, rerender = true) {
      if (!this.active || this.saving || this.copyState || !this.limits || !["Clock", "TempFloor", "Active"].includes(field)) return;
      const event = this.event(day, slot), original = this.event(day, slot, true);
      if (!event || !original) return;
      this.editError = null;
      if (field === "TempFloor") {
        const fahrenheit = Number(value);
        const minimum = Math.ceil(this.limits.MinTemp / 100 * 9 / 5 + 32);
        const maximum = Math.floor(this.limits.MaxTemp / 100 * 9 / 5 + 32);
        if (String(value).trim() === "" || !Number.isInteger(fahrenheit) ||
            fahrenheit < minimum || fahrenheit > maximum) {
          this.editError = `Enter a whole temperature from ${minimum} to ${maximum} °F.`;
        } else {
          // Website conversion truncates to hundredths of Celsius. Untouched values
          // and values returned to their original displayed choice retain exact raw data.
          event.TempFloor = fahrenheit === Math.round(original.TempFloor / 100 * 9 / 5 + 32)
            ? original.TempFloor : Math.trunc((fahrenheit - 32) * (5 / 9) * 100);
        }
      } else if (field === "Clock") {
        if (typeof value !== "string" || !/^([01]\d|2[0-3]):[0-5]\d:00$/.test(value) ||
            scheduleSeconds(value) % 900) this.editError = "Choose a time in 15-minute increments.";
        else event.Clock = scheduleSeconds(value) === scheduleSeconds(original.Clock) ? original.Clock : value;
      } else if (slot !== 0 && typeof value === "boolean") event.Active = value;
      if (rerender) this.render(); else this.updateFeedback();
    }
    get canCopy() {
      return this.active && !!this.limits && !!this.draftSchedules && !this.editError &&
        !this.saving && !this.copyState && !scheduleSubmissions.has(this.config?.entity);
    }
    openCopy() {
      if (!this.canCopy) return;
      this.copyState = {source: this.selectedDay, destinations: new Set(), confirmed: false};
      this.render();
    }
    copyDestinations() {
      return this.copyState ? SUNDAY_FIRST.filter((day) =>
        day !== this.copyState.source && this.copyState.destinations.has(day)) : [];
    }
    copyConflicts() {
      return this.copyDestinations().filter((day) => {
        const current = this.draftSchedules.find((d) => d.WeekDayGrpNo === day);
        const original = this.schedules.find((d) => d.WeekDayGrpNo === day);
        return JSON.stringify(current) !== JSON.stringify(original);
      });
    }
    setCopyDestination(day, checked) {
      if (!this.copyState || !SUNDAY_FIRST.includes(day) || day === this.copyState.source) return;
      if (checked) this.copyState.destinations.add(day); else this.copyState.destinations.delete(day);
      this.copyState.confirmed = false;
      this.updateCopyFeedback();
    }
    setCopyPreset(preset) {
      if (!this.copyState) return;
      const days = preset === "weekdays" ? [1, 2, 3, 4, 5]
        : preset === "weekend" ? [7, 6] : preset === "all" ? SUNDAY_FIRST : [];
      this.copyState.destinations = new Set(days.filter((day) => day !== this.copyState.source));
      this.copyState.confirmed = false;
      this.updateCopyFeedback();
    }
    updateCopyFeedback() {
      if (!this.copyState || !this.copyControls) return;
      const conflicts = this.copyConflicts();
      for (const [day, checkbox] of this.copyControls.checkboxes)
        checkbox.checked = this.copyState.destinations.has(day);
      this.copyControls.warning.hidden = !conflicts.length;
      this.copyControls.warning.textContent = conflicts.length
        ? `Unsaved edits on ${conflicts.map((day) => WEEKDAYS[day - 1]).join(", ")} will be replaced.` : "";
      this.copyControls.confirmLabel.hidden = !conflicts.length;
      this.copyControls.confirm.checked = this.copyState.confirmed;
      this.copyControls.apply.disabled = !this.active || !this.limits || this.saving || !!this.editError ||
        scheduleSubmissions.has(this.config?.entity) || !this.copyDestinations().length ||
        (conflicts.length > 0 && !this.copyState.confirmed);
    }
    cancelCopy() {
      this.copyState = null;
      if (this.copyDialog?.open) this.copyDialog.close();
      this.copyDialog = null; this.copyControls = null;
      if (this.active) { this.render(); this.copyButton?.focus(); }
    }
    applyCopy() {
      if (!this.copyState || !this.active || !this.limits || this.saving || this.editError ||
          scheduleSubmissions.has(this.config?.entity)) return;
      const destinations = this.copyDestinations(), sourceDay = this.copyState.source;
      if (!destinations.length || (this.copyConflicts().length && !this.copyState.confirmed)) return;
      try {
        const source = this.draftSchedules.find((day) => day.WeekDayGrpNo === sourceDay);
        if (!source || source.Events.length !== 6 ||
            new Set(source.Events.map((event) => event.ScheduleType)).size !== 6 ||
            source.Events.some((event) => !Number.isInteger(event.ScheduleType) ||
              event.ScheduleType < 0 || event.ScheduleType > 5 || !finite(event.TempFloor) ||
              typeof event.Active !== "boolean")) throw new Error("Invalid copy source");
        const slots = new Map(source.Events.map((event) => [event.ScheduleType, event]));
        for (const event of source.Events) scheduleClock(event.Clock);
        // Prepare all replacements before changing anything. Preserve destination-specific
        // metadata and original array order; copy all six event settings by slot identity.
        const replacements = destinations.map((day) => {
          const target = this.draftSchedules.find((d) => d.WeekDayGrpNo === day);
          if (!target || target.Events.length !== 6 ||
              new Set(target.Events.map((event) => event.ScheduleType)).size !== 6)
            throw new Error("Invalid copy destination");
          return {target, events: target.Events.map((event) => {
            const selected = slots.get(event.ScheduleType);
            if (!selected) throw new Error("Missing copy slot");
            const copied = JSON.parse(JSON.stringify(event));
            for (const key of ["Clock", "TempFloor", "Active", "ScheduleType"]) copied[key] = selected[key];
            return copied;
          })};
        });
        for (const {target, events} of replacements) target.Events = events;
        this.copyMessage = `Copied ${WEEKDAYS[sourceDay - 1]} to ${destinations.map((day) => WEEKDAYS[day - 1]).join(", ")} in the local draft. Review changes before Save Schedule.`;
        this.cancelCopy();
      } catch (_) {
        if (this.copyControls) this.copyControls.error.textContent = "Copy could not be applied. Your draft is unchanged.";
      }
    }
    renderCopyDialog(node) {
      const dialog = node("dialog", undefined, "copy-dialog");
      dialog.setAttribute("aria-labelledby", "copy-day-title");
      const title = node("h2", `Copy ${WEEKDAYS[this.copyState.source - 1]}`);
      title.id = "copy-day-title"; dialog.append(title);
      dialog.append(node("p", "Copy all six events to the selected days in your local draft. Nothing is sent to Schluter until Save Schedule.", "note"));
      const presets = node("div", undefined, "copy-presets");
      for (const [preset, label] of [["weekdays", "Weekdays"], ["weekend", "Weekend"], ["all", "All other days"]]) {
        const button = node("button", label); button.type = "button";
        button.addEventListener("click", () => this.setCopyPreset(preset)); presets.append(button);
      }
      dialog.append(presets);
      const days = node("div", undefined, "copy-destinations"), checkboxes = new Map();
      for (const day of SUNDAY_FIRST.filter((value) => value !== this.copyState.source)) {
        const label = node("label", undefined, "active-toggle"), checkbox = node("input");
        checkbox.type = "checkbox"; checkbox.value = String(day);
        checkbox.setAttribute("aria-label", `Copy to ${WEEKDAYS[day - 1]}`);
        checkbox.addEventListener("change", () => this.setCopyDestination(day, checkbox.checked));
        label.append(checkbox, node("span", WEEKDAYS[day - 1])); days.append(label); checkboxes.set(day, checkbox);
      }
      dialog.append(days);
      const warning = node("p", "", "error"); warning.setAttribute("role", "alert"); dialog.append(warning);
      const confirmLabel = node("label", undefined, "active-toggle"), confirm = node("input");
      confirm.type = "checkbox";
      confirm.addEventListener("change", () => {
        if (this.copyState) { this.copyState.confirmed = confirm.checked; this.updateCopyFeedback(); }
      });
      confirmLabel.append(confirm, node("span", "Replace unsaved destination edits")); dialog.append(confirmLabel);
      const error = node("p", "", "error"); error.setAttribute("role", "alert"); dialog.append(error);
      const actions = node("div", undefined, "copy-dialog-actions");
      const cancel = node("button", "Cancel copy"), apply = node("button", "Copy to selected days", "copy-apply");
      cancel.type = apply.type = "button";
      cancel.addEventListener("click", () => this.cancelCopy());
      apply.addEventListener("click", () => this.applyCopy());
      actions.append(cancel, apply); dialog.append(actions);
      dialog.addEventListener("keydown", (event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.cancelCopy(); }
      });
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault(); event.stopPropagation(); this.cancelCopy();
      });
      dialog.addEventListener("click", (event) => {
        event.stopPropagation(); if (event.target === dialog) this.cancelCopy();
      });
      dialog.addEventListener("close", () => { if (this.copyDialog === dialog && this.copyState) this.cancelCopy(); });
      this.copyControls = {checkboxes, warning, confirmLabel, confirm, apply, error};
      this.updateCopyFeedback();
      return dialog;
    }
    validationErrors() {
      if (!this.limits || !this.draftSchedules) return [];
      const errors = [];
      for (const day of this.draftSchedules) {
        let previous = null;
        for (const event of [...day.Events].sort((a, b) => a.ScheduleType - b.ScheduleType)) {
          const slot = event.ScheduleType, prefix = `${WEEKDAYS[day.WeekDayGrpNo - 1]}, Event ${slot + 1}`;
          if (slot === 0 && !event.Active) errors.push(`${prefix}: Event 1 must remain active.`);
          if (event.TempFloor < this.limits.MinTemp || event.TempFloor > this.limits.MaxTemp)
            errors.push(`${prefix}: temperature is outside the thermostat limits.`);
          const original = this.event(day.WeekDayGrpNo, slot, true);
          if (original && scheduleSeconds(event.Clock) !== scheduleSeconds(original.Clock) &&
              scheduleSeconds(event.Clock) % 900)
            errors.push(`${prefix}: edited times must use 15-minute increments.`);
          if (!event.Active) continue;
          let seconds = scheduleSeconds(event.Clock);
          // Match the website's clock rollover using server-provided slot bounds.
          // These are recurring local clock values; never apply UTC or DST offsets.
          if (previous !== null && seconds < previous) seconds += 86400;
          if (seconds < this.limits.MinTimeLimits[slot] || seconds > this.limits.MaxTimeLimits[slot])
            errors.push(`${prefix}: time is outside Schluter's limits for this event.`);
          if (previous !== null && seconds < previous + 900)
            errors.push(`${prefix}: active events must be at least 15 minutes apart.`);
          previous = seconds;
        }
      }
      return errors;
    }
    get canSave() {
      return this.active && !!this.limits && this.dirty && !this.editError &&
        !this.saving && !scheduleSubmissions.has(this.config?.entity) && !this.validationErrors().length;
    }
    get saveStatusTone() {
      if (this.saving || this.saveOutcome === "pending") return "info";
      if (this.saveOutcome === "error") return "error";
      if (this.saveOutcome === "confirmed") return this.dirty || this.editError ? "warning" : "success";
      return this.dirty ? "warning" : "neutral";
    }
    paintStatus(element, text, tone) {
      const icons = {success: "mdi:check-circle-outline", warning: "mdi:pencil-outline",
        info: "mdi:progress-clock", error: "mdi:alert-circle-outline", neutral: "mdi:information-outline"};
      element.className = `status-line status-${tone}`;
      element.setAttribute("data-status", tone);
      const icon = document.createElement("ha-icon");
      icon.setAttribute("icon", icons[tone]); icon.setAttribute("aria-hidden", "true");
      const label = document.createElement("span"); label.textContent = text;
      element.replaceChildren(icon, label);
    }
    updateFeedback() {
      if (this.dirtyLabel) this.paintStatus(this.dirtyLabel,
        this.dirty ? "Unsaved changes" : "No unsaved changes", this.dirty ? "warning" : "neutral");
      if (this.saveStatusLabel && this.saveMessage)
        this.paintStatus(this.saveStatusLabel, this.saveMessage, this.saveStatusTone);
      if (this.dismissButton) this.dismissButton.textContent = this.dirty ? "Cancel" : "Close";
      if (this.errorList) {
        this.errorList.replaceChildren();
        for (const message of [...(this.editError ? [this.editError] : []), ...this.validationErrors()]) {
          const item = document.createElement("li"); item.textContent = message; this.errorList.append(item);
        }
      }
      if (this.saveButton) this.saveButton.disabled = !this.canSave;
      if (this.copyButton) this.copyButton.disabled = !this.canCopy;
    }
    async scheduleCall(service, serviceData) {
      let timer;
      try {
        return await Promise.race([
          this.latestHass.callWS({type: "call_service", domain: this.config.service_domain, service,
            target: {entity_id: this.config.entity}, service_data: serviceData, return_response: true}),
          new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Schedule response timeout")), 150000); }),
        ]);
      } finally { clearTimeout(timer); }
    }
    acceptConfirmation(reported, requested, acknowledged, normalized) {
      if (!scheduleComparison(requested, reported)) throw new Error("Schedule confirmation mismatch");
      const stillRequested = scheduleComparison(this.draftSchedules, requested);
      this.schedules = JSON.parse(JSON.stringify(reported));
      if (stillRequested) this.draftSchedules = JSON.parse(JSON.stringify(reported));
      this.pendingSnapshot = null;
      this.saveOutcome = "confirmed";
      this.saveMessage = normalized ? "Schedule confirmed from the thermostat (equivalent clock/number formatting normalized)."
        : acknowledged ? "Schedule saved and confirmed from the thermostat."
        : "Schedule confirmed from the thermostat; API acknowledgement was unavailable.";
    }
    async save() {
      if (!this.canSave) return;
      const entity = this.config.entity, ticket = this.ticket;
      const requested = JSON.parse(JSON.stringify(this.draftSchedules));
      const original = JSON.parse(JSON.stringify(this.schedules));
      this.pendingSnapshot = requested;
      this.pendingAcknowledged = false;
      scheduleSubmissions.add(entity);
      this.saving = true;
      this.saveOutcome = "pending";
      this.saveMessage = "Saving and verifying the schedule… Closing this popup cannot cancel a submitted save.";
      this.render();
      try {
        const result = await this.scheduleCall("save_schedule", {schedules: requested, original_schedules: original});
        if (!this.active || ticket !== this.ticket) return;
        const body = result?.response?.[entity];
        this.pendingAcknowledged = body?.acknowledged === true;
        if (body?.status === "confirmed" || body?.status === "unchanged") {
          this.acceptConfirmation(body.Schedules, requested, body.acknowledged === true,
            body.normalized === true || scheduleComparison(requested, body.Schedules) === "normalized");
          if (body.status === "unchanged") this.saveMessage = "The thermostat already reports this schedule; no write was needed.";
        } else if (body?.status === "unconfirmed") {
          this.saveOutcome = ["readback_failed", "different_values"].includes(body.reason) ? "error" : "pending";
          const detail = body.reason === "different_values" ? "The server reports different event values."
            : body.reason === "readback_failed" ? "Thermostat readback failed." : "The cloud still reports the earlier schedule.";
          this.saveMessage = `${body.acknowledged === true ? "Schluter acknowledged the request, but persistence is unconfirmed." : "Schedule delivery and persistence are unconfirmed."} ${detail} Your draft is retained. Check confirmation before retrying.`;
        } else throw new Error("Missing schedule response");
      } catch (error) {
        if (!this.active || ticket !== this.ticket) return;
        this.saveOutcome = "error";
        this.saveMessage = scheduleSaveError(error);
      } finally {
        scheduleSubmissions.delete(entity);
        if (this.active && ticket === this.ticket) { this.saving = false; this.render(); }
        window.dispatchEvent(new CustomEvent("bathroom-heat-schedule-save-finished"));
      }
    }
    async checkConfirmation() {
      if (!this.active || !this.pendingSnapshot || this.saving || scheduleSubmissions.has(this.config?.entity)) return;
      const entity = this.config.entity, ticket = this.ticket;
      const requested = JSON.parse(JSON.stringify(this.pendingSnapshot));
      scheduleSubmissions.add(entity); this.saving = true;
      this.saveOutcome = "pending";
      this.saveMessage = "Checking thermostat confirmation (read only)…"; this.render();
      try {
        const result = await this.scheduleCall("get_schedule", {});
        if (!this.active || ticket !== this.ticket) return;
        const reported = result?.response?.[entity]?.Schedules;
        const match = scheduleComparison(requested, reported);
        if (match) this.acceptConfirmation(reported, requested, this.pendingAcknowledged, match === "normalized");
        else this.saveMessage = "The requested schedule is still unconfirmed. Your draft is retained. Check again or review before retrying Save Schedule.";
      } catch (_) {
        if (this.active && ticket === this.ticket) {
          this.saveOutcome = "error";
          this.saveMessage = "Could not read thermostat confirmation. Your draft is retained; no additional save was sent.";
        }
      } finally {
        scheduleSubmissions.delete(entity);
        if (this.active && ticket === this.ticket) { this.saving = false; this.render(); }
        window.dispatchEvent(new CustomEvent("bathroom-heat-schedule-save-finished"));
      }
    }
    render() {
      if (!this.shadowRoot) return;
      const node = (tag, text, className) => {
        const el = document.createElement(tag);
        if (text !== undefined) el.textContent = text;
        if (className) el.className = className;
        return el;
      };
      const style = node("style", `
        :host { ${POPUP_LIGHT_SURFACE}
          display: block; background: #f5f5f5; }
        .schedule { padding: 4px 12px 12px; background: #f5f5f5; }
        .note, .zone { color: var(--secondary-text-color); font-size: 13px; }
        .day-row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 12px; margin: 16px 0; }
        .weekday { display: inline-flex; align-items: center; gap: 10px; margin: 0; }
        .day-row select { margin-left: 0; min-height: 44px; }
        .copy-day { display: inline-flex; align-items: center; gap: 8px;
          color: var(--primary-color); border-color: var(--primary-color); background: #ffffff; }
        .copy-day ha-icon { --mdc-icon-size: 20px; width: 20px; height: 20px; }
        .copy-dialog { box-sizing: border-box; width: 420px; max-width: calc(100vw - 32px);
          max-height: calc(100dvh - 32px); overflow: auto; padding: 20px;
          border: 1px solid var(--divider-color); border-radius: 16px;
          color: #202124; background: #f5f5f5; color-scheme: light;
          backdrop-filter: none; -webkit-backdrop-filter: none;
          box-shadow: 0 8px 28px rgba(0,0,0,.3); }
        .copy-dialog::backdrop { background: rgba(0,0,0,.45); }
        .copy-dialog [hidden] { display: none !important; }
        .copy-dialog h2 { margin: 0 0 8px; font-size: 20px; }
        .copy-presets { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0; }
        .copy-presets button { padding: 8px 12px; }
        .copy-destinations { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 10px; }
        .copy-dialog-actions { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 18px; }
        .copy-apply { color: var(--primary-color); border-color: var(--primary-color); background: #ffffff; }
        .active-toggle { display: inline-flex; align-items: center; gap: 6px; }
        .table-scroll { overflow-x: auto; }
        input[type=number] { box-sizing: border-box; width: 4em; min-height: 44px; padding: 8px; font: inherit; }
        input, select, option { color-scheme: light; color: #202124; background: #ffffff; }
        input[type=number] { border: 1px solid var(--divider-color); border-radius: 6px; }
        input[type=checkbox] { accent-color: #0069b4; }
        input:disabled, select:disabled { opacity: 1; color: #5f6368; background: #e2e4e7; }
        input:focus-visible, select:focus-visible { outline: 2px solid #0069b4; outline-offset: 2px; }
        td select { margin: 0; min-height: 44px; }
        .dirty { font-weight: 600; }
        .status-line { display: flex; align-items: flex-start; gap: 7px;
          font-size: 13px; font-weight: 600; line-height: 1.5; }
        .status-line ha-icon { --mdc-icon-size: 18px; width: 18px; height: 18px;
          flex: 0 0 18px; margin-top: 1px; color: inherit; }
        .status-line span { min-width: 0; }
        .status-success { color: var(--success-color, var(--green-color, #2e7d32)); }
        .status-warning { color: var(--warning-color, var(--orange-color, #b26a00)); }
        .status-info { color: var(--info-color, var(--primary-color, #039be5)); }
        .status-error { color: var(--error-color, #db4437); }
        .status-neutral { color: var(--secondary-text-color); }
        button { font: inherit; font-weight: 500; min-height: 44px; padding: 10px 18px;
          border: 1px solid var(--divider-color); border-radius: var(--ha-border-radius-lg, 12px);
          background: var(--secondary-background-color, white); color: var(--primary-text-color);
          cursor: pointer; }
        button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        button:disabled { cursor: default; opacity: 1; color: #5f6368; background: #e2e4e7; }
        .actions { display: flex; justify-content: space-between; align-items: center;
          gap: 12px; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--divider-color); }
        .actions .primary { margin-left: auto; border-color: transparent;
          background: var(--primary-color); color: var(--text-primary-color, white); }
        .actions .primary:disabled { background: var(--disabled-color, #bdbdbd); color: #5f6368; }
        .check-confirmation { margin-top: 12px; }
        @media (max-width: 400px) {
          .schedule { padding-left: 4px; padding-right: 4px; }
          input[type=number], select { font-size: 16px; }
          th, td { padding-left: 4px; padding-right: 4px; }
          .actions { gap: 8px; }
          .actions button { min-width: 0; padding: 10px 12px; }
        }
        select { margin-left: 12px; padding: 8px; font: inherit; color: inherit;
          background: var(--card-background-color, white); border: 1px solid var(--divider-color); border-radius: 6px; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { text-align: left; padding: 12px 6px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
        th { font-weight: 500; }
        .inactive { color: var(--secondary-text-color); }
        .error { color: var(--error-color); }
      `);
      const content = node("div", undefined, "schedule");
      content.append(node("p", "Edit the weekly schedule, then Save Schedule to send all seven days.", "note"));
      this.saveStatusLabel = null;
      if (this.saveMessage) {
        this.saveStatusLabel = node("p");
        this.saveStatusLabel.setAttribute("role", "status");
        this.paintStatus(this.saveStatusLabel, this.saveMessage, this.saveStatusTone);
        content.append(this.saveStatusLabel);
      }
      if (!this.dirty) this.copyMessage = null;
      if (this.copyMessage) {
        const message = node("p"); this.paintStatus(message, this.copyMessage, "warning");
        message.setAttribute("role", "status"); content.append(message);
      }
      if (this.loading || !this.attempted) content.append(node("p", "Loading schedule…"));
      else if (this.error || !this.schedules) content.append(node("p", this.error || "Schedule unavailable", "error"));
      else {
        if (!this.limits) content.append(node("p",
          "Editing unavailable: thermostat validation limits could not be loaded. Read-only schedule shown.", "note"));
        else {
          this.dirtyLabel = node("p", "", "dirty");
          this.dirtyLabel.setAttribute("aria-live", "polite"); content.append(this.dirtyLabel);
          this.errorList = node("ul", undefined, "error");
          this.errorList.setAttribute("aria-live", "polite"); content.append(this.errorList);
        }
        const label = node("label", "Day", "weekday");
        const select = node("select");
        select.setAttribute("aria-label", "Schedule weekday");
        SUNDAY_FIRST.forEach((day) => {
          const option = node("option", WEEKDAYS[day - 1]);
          option.value = String(day);
          option.selected = day === this.selectedDay;
          select.append(option);
        });
        select.value = String(this.selectedDay);
        select.disabled = this.saving || !!this.copyState;
        select.addEventListener("change", () => {
          if (this.saving || this.copyState) return;
          const day = Number(select.value);
          if (Number.isInteger(day) && day >= 1 && day <= 7) { this.selectedDay = day; this.render(); }
        });
        label.append(select);
        const dayRow = node("div", undefined, "day-row"); dayRow.append(label);
        if (this.limits) {
          this.copyButton = node("button", undefined, "copy-day"); this.copyButton.type = "button";
          this.copyButton.disabled = !this.canCopy;
          const icon = node("ha-icon"); icon.setAttribute("icon", "mdi:content-copy"); icon.setAttribute("aria-hidden", "true");
          this.copyButton.append(icon, node("span", "Copy Day…"));
          this.copyButton.addEventListener("click", () => this.openCopy()); dayRow.append(this.copyButton);
        }
        content.append(dayRow);
        const table = node("table");
        table.setAttribute("aria-label", `${WEEKDAYS[this.selectedDay - 1]} heating schedule`);
        const head = node("thead"), header = node("tr");
        for (const title of ["Event", "Time", "Target", "Status"]) header.append(node("th", title));
        head.append(header); table.append(head);
        const body = node("tbody");
        const day = this.draftSchedules.find((d) => d.WeekDayGrpNo === this.selectedDay);
        for (const event of day.Events) {
          const row = node("tr", undefined, event.Active ? "active" : "inactive");
          const fahrenheit = new Intl.NumberFormat("en-US", {maximumFractionDigits: 1})
            .format(event.TempFloor / 100 * 9 / 5 + 32);
          if (!this.limits) {
            for (const text of [`Event ${event.ScheduleType + 1}`, scheduleClock(event.Clock),
              `${fahrenheit} °F`, event.Active ? "Active" : "Inactive"]) row.append(node("td", text));
          } else {
            const slot = event.ScheduleType, dayNumber = this.selectedDay;
            row.append(node("td", `Event ${slot + 1}`));
            const timeCell = node("td"), clock = node("select");
            clock.setAttribute("aria-label", `Event ${slot + 1} time`);
            // Keep unusual existing clocks intact until the user explicitly edits them.
            const normalized = event.Clock.split(":").map((v) => v.padStart(2, "0")).join(":");
            if (scheduleSeconds(event.Clock) % 900) {
              const existing = node("option", `${scheduleClock(event.Clock)} (existing)`);
              existing.value = normalized; clock.append(existing);
            }
            for (let quarter = 0; quarter < 96; quarter++) {
              const value = `${String(Math.floor(quarter / 4)).padStart(2, "0")}:${String(quarter % 4 * 15).padStart(2, "0")}:00`;
              const option = node("option", scheduleClock(value));
              option.value = value; clock.append(option);
            }
            clock.value = normalized; clock.disabled = this.saving || !!this.copyState;
            clock.addEventListener("change", () => this.edit(dayNumber, slot, "Clock", clock.value));
            timeCell.append(clock); row.append(timeCell);
            const temperatureCell = node("td"), temperature = node("input");
            temperature.type = "number"; temperature.step = "1"; temperature.disabled = this.saving || !!this.copyState;
            temperature.min = String(Math.ceil(this.limits.MinTemp / 100 * 9 / 5 + 32));
            temperature.max = String(Math.floor(this.limits.MaxTemp / 100 * 9 / 5 + 32));
            temperature.value = String(Math.round(event.TempFloor / 100 * 9 / 5 + 32));
            temperature.setAttribute("aria-label", `Event ${slot + 1} target in Fahrenheit`);
            temperature.addEventListener("input", () => this.edit(dayNumber, slot, "TempFloor", temperature.value, false));
            temperature.addEventListener("change", () => this.edit(dayNumber, slot, "TempFloor", temperature.value, false));
            temperatureCell.append(temperature, node("span", " °F")); row.append(temperatureCell);
            const statusCell = node("td"), activeLabel = node("label", undefined, "active-toggle");
            const active = node("input"); active.type = "checkbox";
            active.checked = event.Active; active.disabled = slot === 0 || this.saving || !!this.copyState;
            active.setAttribute("aria-label", `Event ${slot + 1} active`);
            active.addEventListener("change", () => this.edit(dayNumber, slot, "Active", active.checked));
            activeLabel.append(active, node("span", event.Active ? "Active" : "Inactive"));
            statusCell.append(activeLabel); row.append(statusCell);
          }
          body.append(row);
        }
        table.append(body);
        const scroll = node("div", undefined, "table-scroll"); scroll.append(table); content.append(scroll);
        content.append(node("p", `Times are local to the thermostat (${this.timeZone}).`, "zone"));
        if (this.limits) {
          this.saveButton = node("button", this.saving ? "Saving / checking…" : "Save Schedule", "primary");
          this.saveButton.type = "button";
          this.saveButton.addEventListener("click", () => { void this.save(); });
          if (this.pendingSnapshot && !this.saving) {
            const check = node("button", "Check confirmation", "check-confirmation"); check.type = "button";
            check.disabled = scheduleSubmissions.has(this.config?.entity);
            check.addEventListener("click", () => { void this.checkConfirmation(); }); content.append(check);
          }
        }
        this.updateFeedback();
      }
      const actions = node("div", undefined, "actions");
      actions.setAttribute("role", "group"); actions.setAttribute("aria-label", "Schedule actions");
      const cancel = node("button", this.dirty ? "Cancel" : "Close"); cancel.type = "button";
      this.dismissButton = cancel;
      cancel.addEventListener("click", async () => {
        try { await this.browserMod.closePopup({tag: SCHEDULE_TAG}); }
        catch (_) {
          this.dispatchEvent(new CustomEvent("hass-notification", {
            bubbles: true, composed: true,
            detail: {message: "Could not close the schedule popup. Use X to cancel."},
          }));
        }
      });
      actions.append(cancel);
      if (this.saveButton) actions.append(this.saveButton);
      content.append(actions);
      this.copyDialog = this.copyState ? this.renderCopyDialog(node) : null;
      if (this.copyDialog) content.append(this.copyDialog);
      this.shadowRoot.replaceChildren(style, content);
      if (this.copyDialog && this.active) {
        try { this.copyDialog.showModal(); }
        catch (_) {
          this.copyState = null; this.copyDialog = null; this.copyControls = null;
          this.dispatchEvent(new CustomEvent("hass-notification", {
            bubbles: true, composed: true,
            detail: {message: "Could not open Copy Day. Your schedule draft is unchanged."},
          }));
          this.render();
        }
      }
    }
    getCardSize() { return 5; }
  }

  // Shared schedule opener: the original native button and the new compact
  // SVG picture-element both reach this same Browser Mod dialog.
  function openWeeklySchedule(entity, serviceDomain, notificationTarget) {
    const bm = window.browser_mod;
    if (typeof bm?.service !== "function") {
      notificationTarget?.dispatchEvent?.(new CustomEvent("hass-notification", {
        bubbles: true, composed: true,
        detail: {message: "Browser Mod must be loaded to view the schedule."},
      }));
      return;
    }
    if (bm.openPopups?.includes(SCHEDULE_TAG)) return;
    bm.service("browser_mod.popup", {
      tag: SCHEDULE_TAG, title: "Weekly heating schedule", adaptive: true,
      popup_styles: popupStyles(),
      content: {type: "custom:bathroom-heat-schedule", entity,
        service_domain: serviceDomain},
      dismissable: true,
    });
  }

  class BathroomHeatScheduleButton extends HTMLElement {
    setConfig(config) {
      if (!config || typeof config.entity !== "string" || !config.entity.startsWith("climate.") || !/^[a-z0-9_]+$/.test(config.entity.slice(8)))
        throw new Error("The bathroom schedule button requires a valid configured climate entity.");
      this.config = requireHeatConfig(config);
    }
    set hass(value) { this.latestHass = value; }
    connectedCallback() {
      const button = document.createElement("button");
      button.textContent = "Edit Schedule";
      button.type = "button";
      Object.assign(button.style, {
        width: "100%", padding: "6px", borderRadius: "6px", cursor: "pointer",
        border: "1px solid rgba(0,0,0,0.25)", background: "#d9d9d9",
        font: "inherit", color: "inherit",
      });
      // Only the compact card uses the height set from the rendered faceplate.
      // The original Picture Elements card keeps its exact prior button style.
      if (this.config?.compact === true) {
        Object.assign(button.style, {
          boxSizing: "border-box",
          padding: "0 6px",
          lineHeight: "1.1",
          whiteSpace: "nowrap",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        });
        // Some HA themes enforce a minimum native button height, overriding
        // our previous ordinary minHeight and making compact buttons too tall.
        // Constrain the actual native button only in compact mode.
        const compactHeight = "var(--bath-heat-schedule-height, 38px)";
        button.style.setProperty("height", compactHeight, "important");
        button.style.setProperty("min-height", "0px", "important");
        button.style.setProperty("max-height", compactHeight, "important");
      }
      button.addEventListener("click", () => {
        if (!this.config) return;
        openWeeklySchedule(this.config.entity, this.config.service_domain, this);
      });
      this.replaceChildren(button);
    }
    getCardSize() { return 1; }
  }

  window.bathroomHeatControls = Object.freeze({
    version: VERSION,
    apply: (entity, token, data, hass) => controllers.get(entity)?.apply(token, data, hass),
  });
  if (!customElements.get("bathroom-heat-scaler"))
    customElements.define("bathroom-heat-scaler", BathroomHeatScaler);
  if (!customElements.get("bathroom-heat-target"))
    customElements.define("bathroom-heat-target", BathroomHeatTarget);
  if (!customElements.get("bathroom-heat-status"))
    customElements.define("bathroom-heat-status", BathroomHeatStatus);
  if (!customElements.get("bathroom-heat-schedule"))
    customElements.define("bathroom-heat-schedule", BathroomHeatSchedule);
  if (!customElements.get("bathroom-heat-schedule-button"))
    customElements.define("bathroom-heat-schedule-button", BathroomHeatScheduleButton);

  document.body.addEventListener("ll-custom", (event) => {
    const data = event.detail?.bathroom_heat;
    const controller = data && controllers.get(data.entity);
    if (!controller) return;
    if (data.action === "step") controller.step(data.step);
    else if (data.action === "adjust") void controller.open();
    else if (data.action === "schedule") openWeeklySchedule(
      controller.entity, controller.serviceDomain, event.target);
    else if (data.action === "return_to_schedule") void controller.returnToSchedule();
  });
  const discard = () => { for (const controller of controllers.values()) controller.reset(true, true); };
  let location = window.location.href;
  const navigate = () => {
    const next = window.location.href;
    // Dialog/history notifications at the same URL are not view navigation.
    if (next !== location) { location = next; discard(); }
  };
  for (const event of ["location-changed", "popstate", "hashchange"])
    window.addEventListener(event, navigate);
  for (const event of ["pagehide", "beforeunload"]) window.addEventListener(event, discard);
})();