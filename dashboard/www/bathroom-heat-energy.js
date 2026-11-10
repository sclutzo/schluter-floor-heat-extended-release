/* Read-only Schluter provider reports. No thermostat controls or browser credentials. */
(() => {
  "use strict";
  const VERSION = "1.0.5";
  const TAG = "bathroom-heat-energy";
  const ZONE = "America/New_York";
  const POLL_MS = 15 * 60 * 1000;
  const VIEWS = ["day", "week", "month", "year"];
  const pad = (n) => String(n).padStart(2, "0");
  const isoDay = (date) => `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}`;
  function parseDay(text) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(text || "")) throw Error("Invalid date");
    const [year, month, day] = text.split("-").map(Number);
    const date = new Date(Date.UTC(year, month - 1, day));
    if (year < 1000 || year > 9998 || isoDay(date) !== text) throw Error("Invalid date");
    return date;
  }
  function today() {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date(Date.now())).filter((p) => p.type !== "literal").map((p) => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  }
  function period(view, anchor) {
    const start = parseDay(anchor), end = new Date(start);
    if (view === "week") start.setUTCDate(start.getUTCDate() - start.getUTCDay());
    else if (view === "month") start.setUTCDate(1);
    else if (view === "year") start.setUTCMonth(0, 1);
    end.setTime(start.getTime());
    if (view === "year") end.setUTCFullYear(end.getUTCFullYear() + 1);
    else if (view === "month") end.setUTCMonth(end.getUTCMonth() + 1);
    else end.setUTCDate(end.getUTCDate() + (view === "week" ? 7 : 1));
    return {start: isoDay(start), end: isoDay(end)};
  }
  function shift(view, anchor, direction) {
    const date = parseDay(period(view, anchor).start);
    if (view === "year") date.setUTCFullYear(date.getUTCFullYear() + direction);
    else if (view === "month") date.setUTCMonth(date.getUTCMonth() + direction);
    else date.setUTCDate(date.getUTCDate() + direction * (view === "week" ? 7 : 1));
    return isoDay(date);
  }
  const number = (value, digits = 3) => new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits, minimumFractionDigits: 0,
  }).format(value);
  const exactEnergy = (value) => new Intl.NumberFormat("en-US", {
    maximumSignificantDigits: 21,
  }).format(value);
  function displayEnergy(value) {
    if (value === 0) return "0";
    if (value > 0 && value < .0001) return "<0.0001";
    const digits = value >= 1 ? 2 : 4;
    return new Intl.NumberFormat("en-US", {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(value);
  }
  function energyScale(buckets) {
    const maximum = Math.max(0, ...buckets.filter((b) =>
      b.availability === "reported" && typeof b.kwh === "number" && Number.isFinite(b.kwh)
    ).map((b) => b.kwh));
    const target = maximum || 1, raw = target / 4 || target;
    const magnitude = 10 ** Math.floor(Math.log10(raw));
    const fraction = raw / magnitude;
    const step = [1, 2, 2.5, 5, 10].find((n) => n >= fraction) * magnitude;
    const count = Math.ceil(target / step), top = count * step;
    if (Number.isFinite(top) && top >= target && Number.isFinite(step) && step > 0 && count <= 8)
      return {maximum, top, step, ticks: Array.from({length: count + 1}, (_, i) => i * step)};
    // Keep even extreme finite values bounded without overflowing tick arithmetic.
    return {maximum, top: target, step: raw, ticks: [...new Set([0, .25, .5, .75, 1].map((n) => target * n))]};
  }
  function axisNumber(value, step) {
    if (!value) return "0";
    if (Math.abs(value) < 1e-8 || Math.abs(value) >= 1e6)
      return new Intl.NumberFormat("en-US", {notation: "scientific", maximumSignificantDigits: 5}).format(value);
    return number(value, Math.min(12, Math.max(0, 1 - Math.floor(Math.log10(step)))));
  }
  function dateLabel(text) {
    return new Intl.DateTimeFormat("en-US", {timeZone: "UTC", month: "long", day: "numeric", year: "numeric"}).format(parseDay(text));
  }
  function bucketLabel(view, report, bucket) {
    try {
      if (view === "day") return `${dateLabel(report.start)}${report.hour_labels_known === false
        ? ` — ${bucket.label} (hour mapping unverified)` : ` at ${bucket.label}`}`;
      if (view === "year") return new Intl.DateTimeFormat("en-US", {
        timeZone: "UTC", month: "long", year: "numeric",
      }).format(parseDay(bucket.date));
      return dateLabel(bucket.date);
    } catch (_) { return bucket.label; }
  }
  function futureZeroHours(view, report) {
    const empty = new Set();
    if (view !== "day" || report.hour_labels_known !== true || report.time_zone !== ZONE
        || report.buckets.length !== 24) return empty;
    const ordered = report.buckets.every((bucket, index) => {
      const match = /^(1[0-2]|[1-9])(?::00)?\s*(AM|PM)$/i.exec(bucket.label.trim());
      return match && (+match[1] % 12 + (match[2].toUpperCase() === "PM" ? 12 : 0)) === index;
    });
    if (!ordered) return empty;
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(Date.now())).filter((p) => p.type !== "literal").map((p) => [p.type, p.value]));
    if (report.start !== `${parts.year}-${parts.month}-${parts.day}`) return empty;
    // Presentation only: retain raw buckets, totals, unknown flags, and nonzero values.
    return new Set(report.buckets.filter((bucket, index) => index > +parts.hour && bucket.kwh === 0
      && ["reported", "zero_or_unavailable"].includes(bucket.availability)));
  }
  function title(view, anchor) {
    const {start, end} = period(view, anchor);
    if (view === "year") return start.slice(0, 4);
    if (view === "month") return new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC", month: "long", year: "numeric",
    }).format(parseDay(start));
    if (view === "week") {
      const last = parseDay(end); last.setUTCDate(last.getUTCDate() - 1);
      return `${dateLabel(start)} – ${dateLabel(isoDay(last))}`;
    }
    return dateLabel(start);
  }
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }

  class BathroomHeatEnergy extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({mode: "open"});
      this.view = "day";
      this.anchor = today();
      this.followCurrent = true;
      this.active = false;
      this.loading = false;
      this.ticket = 0;
      this.requested = null;
      this.report = null;
      this.error = null;
      this._daySelection = null;
      this._dayScroll = null;
      this._dayChart = null;
      this._dayFrame = null;
      this._dayObserver = null;
      this._recenterDay = false;
      this.onVisible = () => {
        if (document.visibilityState !== "hidden") void this.load(false, true);
      };
    }
    setConfig(config) {
      const entity = config.entity || "climate.bath_heat";
      if (!entity.startsWith("climate.") || !/^[a-z0-9_]+$/.test(entity.slice(8)))
        throw Error("The bathroom energy card needs a valid climate entity.");
      const domain = config.service_domain || "schluter";
      if (!["schluter", "schluter_heat_extended"].includes(domain))
        throw Error("Choose a supported Schluter service_domain.");
      if (config.default_view && !VIEWS.includes(config.default_view)) throw Error("Choose Day, Week, Month, or Year.");
      const first = !this.config;
      this.config = {...config, entity, service_domain: domain};
      if (first) this.view = config.default_view || "day";
      this.render();
      void this.load();
    }
    set hass(value) { this.latestHass = value; void this.load(); }
    connectedCallback() {
      this.active = true;
      document.addEventListener("visibilitychange", this.onVisible);
      this.render();
      void this.load();
    }
    disconnectedCallback() {
      this.active = false;
      this._stopDayLayout();
      this._daySelection = null;
      this._dayScroll = null;
      this._recenterDay = false;
      this.ticket++;
      this.loading = false;
      this.requested = null;
      clearTimeout(this.timer);
      document.removeEventListener("visibilitychange", this.onVisible);
    }
    scheduleRefresh() {
      clearTimeout(this.timer);
      if (this.active) this.timer = setTimeout(() => {
        if (document.visibilityState !== "hidden") void this.load(false, true);
        else this.scheduleRefresh();
      }, POLL_MS);
    }
    select(view, anchor) {
      try {
        if (!VIEWS.includes(view) || period(view, anchor).start > today()) throw Error("Future period");
        this._recenterDay = view === "day" && (this.view !== "day"
          || period("day", anchor).start !== period("day", this.anchor).start);
        this.view = view; this.anchor = anchor;
        this.followCurrent = period(view, anchor).start === period(view, today()).start;
        this.error = null;
        this.render();
        void this.load();
      } catch (_) {
        this.error = "Choose a valid current or previous reporting period.";
        this.render();
      }
    }
    async load(refresh = false, recheck = false) {
      if (!this.active || !this.config || !this.latestHass) return;
      if (this.followCurrent) this.anchor = today();
      const bounds = period(this.view, this.anchor);
      const key = `${this.view}|${bounds.start}`;
      if (this.loading && this.requested === key) return;
      if (this.requested === key && !refresh && !recheck) return;
      clearTimeout(this.timer);
      this.requested = key;
      this.loading = true;
      this.error = null;
      this.report = null;
      const ticket = ++this.ticket, view = this.view;
      this.render();
      try {
        const result = await this.latestHass.callWS({
          type: "call_service", domain: this.config.service_domain, service: "get_energy_report",
          target: {entity_id: this.config.entity}, return_response: true,
          service_data: {view, date: view === "year" ? bounds.start.slice(0, 4) : bounds.start, refresh},
        });
        if (!this.active || ticket !== this.ticket) return;
        const body = result?.response?.[this.config.entity];
        if (!body || body.view !== view || body.start !== bounds.start ||
            !Array.isArray(body.buckets) || !Array.isArray(body.warnings) ||
            !["reported", "partial", "unavailable", "zero_or_unavailable"].includes(body.status) ||
            (body.total_kwh !== null && (typeof body.total_kwh !== "number" || !Number.isFinite(body.total_kwh) || body.total_kwh < 0)))
          throw Error("Unrecognized energy report");
        for (const bucket of body.buckets) {
          if (typeof bucket.label !== "string" ||
              !["future", "reported", "unavailable", "zero_or_unavailable"].includes(bucket.availability) ||
              (bucket.kwh !== null && (typeof bucket.kwh !== "number" || !Number.isFinite(bucket.kwh) || bucket.kwh < 0)))
            throw Error("Invalid energy bucket");
        }
        this.report = body;
      } catch (_) {
        if (!this.active || ticket !== this.ticket) return;
        // Arbitrary websocket/transport errors may contain sensitive request details.
        this.error = "Energy report could not be loaded. Check that Schluter is available and the new action is installed, then Refresh to retry.";
      } finally {
        if (this.active && ticket === this.ticket) {
          this.loading = false;
          this.render();
          this.scheduleRefresh();
        }
      }
    }
    _captureDayScroll(ref = this._dayChart) {
      if (ref !== this._dayChart || !ref || ref.scroll.clientWidth <= 0) return;
      // A rebuilt chart starts at zero until restoration; do not lose saved scroll.
      if (ref.applied || ref.scroll.scrollLeft > 0) {
        ref.state.left = ref.scroll.scrollLeft;
        ref.state.positioned = true;
        ref.applied = true;
      }
    }
    _stopDayLayout() {
      if (this._dayFrame !== null) window.cancelAnimationFrame?.(this._dayFrame);
      this._dayFrame = null;
      this._dayObserver?.disconnect();
      this._dayObserver = null;
      this._dayChart = null;
    }
    _beginDayRender() {
      this._captureDayScroll();
      this._stopDayLayout();
      const selection = `${this.view}|${period(this.view, this.anchor).start}`;
      if (selection !== this._daySelection) {
        const previous = this._daySelection?.startsWith("day|") && !this._recenterDay ? this._dayScroll : null;
        this._daySelection = selection;
        this._dayScroll = this.view === "day" ? previous || {left: 0, positioned: false} : null;
      }
      this._recenterDay = false;
    }
    _mountDayScroll(chart) {
      if (!chart || !this.active || !this._dayScroll) return;
      const ref = this._dayChart = {...chart, state: this._dayScroll, applied: false};
      ref.scroll.addEventListener("scroll", () => this._captureDayScroll(ref), {passive: true});
      if (typeof window.requestAnimationFrame !== "function") return;
      const schedule = () => {
        if (ref !== this._dayChart || !this.active) return;
        if (this._dayFrame !== null) window.cancelAnimationFrame?.(this._dayFrame);
        // Let card sizing and CSS layout settle before measuring the noon column.
        this._dayFrame = window.requestAnimationFrame(() => {
          this._dayFrame = window.requestAnimationFrame(() => {
            this._dayFrame = null;
            this._positionDayScroll(ref);
          });
        });
      };
      if (typeof window.ResizeObserver === "function") {
        this._dayObserver = new window.ResizeObserver(schedule);
        this._dayObserver.observe(ref.scroll);
        if (ref.noon) this._dayObserver.observe(ref.noon);
      }
      schedule();
    }
    _positionDayScroll(ref) {
      if (!this.active || ref !== this._dayChart || ref.scroll.clientWidth <= 0) return;
      const {scroll, state, noon} = ref;
      const maximum = Math.max(0, scroll.scrollWidth - scroll.clientWidth);
      let left = state.left;
      if (!state.positioned) {
        if (!noon) return; // No guessed noon on unverified DST/hour mappings.
        const bar = noon.getBoundingClientRect();
        if (bar.width <= 0) return;
        if (maximum > 0) {
          const viewport = scroll.getBoundingClientRect();
          left = bar.left + bar.width / 2 - viewport.left - scroll.clientLeft
            + scroll.scrollLeft - scroll.clientWidth / 2;
        } else left = 0;
      }
      state.positioned = true;
      ref.applied = true;
      const target = Math.max(0, Math.min(maximum, left));
      if (scroll.scrollLeft !== target) scroll.scrollLeft = target;
      state.left = scroll.scrollLeft;
    }
    render() {
      if (!this.shadowRoot || !this.config) return;
      this._beginDayRender();
      const style = node("style", `
        :host { display:block; color:var(--primary-text-color); }
        ha-card { padding:16px; overflow:hidden; }
        h2 { margin:0 0 12px; font-size:20px; font-weight:500; }
        .tabs,.navigation { display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-bottom:12px; }
        button,input { font:inherit; color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color,#aaa); border-radius:20px; padding:8px 12px; }
        button { cursor:pointer; min-height:40px; }
        button[aria-pressed="true"] { background:var(--primary-color); color:var(--text-primary-color,#fff); border-color:var(--primary-color); }
        button:disabled { opacity:.45; cursor:default; }
        button:focus-visible,input:focus-visible { outline:2px solid var(--primary-color); outline-offset:2px; }
        .period { font-weight:600; margin:8px 0; }
        .total { font-size:24px; font-weight:600; margin:12px 0 4px; }
        .note,.updated { color:var(--secondary-text-color); font-size:12px; line-height:1.5; }
        .warning { color:var(--warning-color,#b26a00); font-size:13px; line-height:1.5; }
        .error { color:var(--error-color,#db4437); }
        .chart-frame { display:grid; grid-template-columns:max-content minmax(0,1fr); gap:8px; padding-top:8px; margin:16px 0 8px; }
        .y-axis { position:relative; height:180px; min-width:2em; padding-right:8px; border-right:1px solid var(--divider-color,#aaa); color:var(--secondary-text-color); font-size:11px; }
        .axis-size { visibility:hidden; white-space:nowrap; }
        .axis-tick { position:absolute; right:8px; transform:translateY(-50%); line-height:1; white-space:nowrap; }
        .scroll { overflow-x:auto; min-width:0; }
        .chart { position:relative; display:flex; gap:5px; height:212px; align-items:stretch; }
        .gridlines { position:absolute; top:0; left:0; right:0; height:180px; pointer-events:none; }
        .gridline { position:absolute; left:0; right:0; border-top:1px solid var(--divider-color,#aaa); opacity:.6; }
        .column { position:relative; flex:1; min-width:0; display:flex; flex-direction:column; align-items:center; }
        .plot { box-sizing:border-box; flex:0 0 180px; height:180px; min-height:0; width:100%; padding:0; border:0; border-radius:0; background:transparent; display:flex; align-items:flex-end; justify-content:center; }
        .plot:hover .bar,.plot:focus-visible .bar { filter:brightness(.85); }
        .plot:focus-visible { outline-offset:-2px; }
        .bar { width:75%; background:var(--primary-color); border-radius:4px 4px 0 0; pointer-events:none; }
        .provisional .bar { opacity:.65; }
        .missing { color:var(--secondary-text-color); font-size:12px; }
        .label { min-height:28px; font-size:10px; text-align:center; padding-top:4px; white-space:nowrap; }
        .chart-readout { min-height:2.5em; margin:4px 0; font-size:13px; line-height:1.5; overflow-wrap:anywhere; }
        details { margin-top:12px; } summary { cursor:pointer; }
        table { width:100%; border-collapse:collapse; margin-top:8px; font-size:13px; }
        th,td { text-align:left; border-bottom:1px solid var(--divider-color); padding:6px; }
        .value { text-align:right; } .hidden { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
        @media(max-width:450px) { ha-card {padding:12px;} button,input {padding:8px 10px;} }
      `);
      const card = node("ha-card");
      card.append(node("h2", this.config.title || "Bathroom Floor Heat Energy"));
      const tabs = node("div", undefined, "tabs"); tabs.setAttribute("aria-label", "Energy reporting view");
      for (const view of VIEWS) {
        const button = node("button", view[0].toUpperCase() + view.slice(1)); button.type = "button";
        button.setAttribute("aria-pressed", String(view === this.view));
        button.addEventListener("click", () => this.select(view, this.anchor)); tabs.append(button);
      }
      card.append(tabs);
      const navigation = node("div", undefined, "navigation");
      for (const [text, direction] of [["Previous", -1], ["Next", 1]]) {
        const button = node("button", text); button.type = "button";
        let next;
        try { next = shift(this.view, this.anchor, direction); button.disabled = period(this.view, next).start > today(); }
        catch (_) { button.disabled = true; }
        button.addEventListener("click", () => this.select(this.view, next)); navigation.append(button);
      }
      const picker = node("input");
      picker.setAttribute("aria-label", "Reporting period");
      picker.type = this.view === "year" ? "number" : this.view === "month" ? "month" : "date";
      picker.value = this.view === "year" ? this.anchor.slice(0, 4) : this.view === "month" ? this.anchor.slice(0, 7) : this.anchor;
      picker.min = this.view === "year" ? "1000" : this.view === "month" ? "1000-01" : "1000-01-01";
      picker.max = this.view === "year" ? today().slice(0, 4) : this.view === "month" ? today().slice(0, 7) : today();
      picker.step = "1";
      picker.addEventListener("change", () => {
        const value = this.view === "year" ? `${picker.value}-01-01` : this.view === "month" ? `${picker.value}-01` : picker.value;
        this.select(this.view, value);
      });
      navigation.append(picker);
      const current = node("button", "Current"); current.type = "button";
      current.addEventListener("click", () => this.select(this.view, today())); navigation.append(current);
      const refresh = node("button", "Refresh"); refresh.type = "button"; refresh.disabled = this.loading;
      refresh.addEventListener("click", () => { void this.load(true); }); navigation.append(refresh);
      card.append(navigation, node("p", title(this.view, this.anchor), "period"));
      const live = node("div"); live.setAttribute("role", "status"); live.setAttribute("aria-live", "polite");
      if (this.loading) live.append(node("p", "Loading energy reports…"));
      if (this.error) live.append(node("p", this.error, "error"));
      card.append(live);
      const report = this.report;
      let dayChart = null;
      if (report) {
        const uncertain = report.status === "zero_or_unavailable";
        const totalLabel = report.total_kwh === null ? "Energy unavailable" :
          `${displayEnergy(report.total_kwh)} kWh${report.status === "partial" || uncertain ? " reported subtotal" : " total"}`;
        card.append(node("div", totalLabel, "total"));
        if (report.incomplete) card.append(node("p", report.start === period(this.view, today()).start
          ? {day: "Today so far", week: "Week so far", month: "Month so far", year: "Year so far"}[this.view]
          : "Partial period", "note"));
        if (report.stale) card.append(node("p", "Cached historical values — refresh was unsuccessful or returned ambiguous empty data.", "warning"));
        for (const warning of report.warnings) card.append(node("p", warning, "warning"));
        const futureZeros = futureZeroHours(this.view, report);
        const scale = energyScale(report.buckets);
        card.append(node("p", "Consumption (kWh)", "note"));
        const frame = node("div", undefined, "chart-frame");
        const axis = node("div", undefined, "y-axis"); axis.setAttribute("aria-label", "Energy consumption in kWh");
        const tickLabels = scale.ticks.map((value) => axisNumber(value, scale.step));
        axis.append(node("span", tickLabels.reduce((a, b) => a.length > b.length ? a : b, "0"), "axis-size"));
        const chart = node("div", undefined, "chart");
        chart.setAttribute("role", "group"); chart.setAttribute("aria-label", `${this.view} consumption bars in kWh`);
        const width = Math.max(36, ...report.buckets.map((b) => b.label.length * 6));
        chart.style.minWidth = `${Math.max(0, report.buckets.length * (width + 5) - 5)}px`;
        const grid = node("div", undefined, "gridlines"); grid.setAttribute("aria-hidden", "true");
        scale.ticks.forEach((value, index) => {
          const top = `${100 * (1 - value / scale.top)}%`;
          const tick = node("span", tickLabels[index], "axis-tick"); tick.style.top = top;
          tick.setAttribute("aria-label", `${tickLabels[index]} kWh`); axis.append(tick);
          const line = node("div", undefined, "gridline"); line.style.top = top; grid.append(line);
        });
        chart.append(grid);
        const readout = node("p", "Hover, focus, or tap a bar to see its reported consumption.", "chart-readout");
        readout.id = "energy-readout"; readout.setAttribute("role", "status"); readout.setAttribute("aria-live", "polite");
        let noon = null;
        for (const bucket of report.buckets) {
          const column = node("div", undefined, `column${bucket.provisional ? " provisional" : ""}`);
          if (this.view === "day" && report.hour_labels_known === true && /^12(?::00)?\s*PM$/i.test(bucket.label.trim()))
            noon = column;
          const plot = node("button", undefined, "plot"); plot.type = "button";
          const futureZero = futureZeros.has(bucket);
          const detail = futureZero ? `Future hour — provider reports zero${bucket.availability === "zero_or_unavailable" ? "; availability unverified" : ""}` :
            bucket.availability === "future" ? "Future — no consumption value" :
            bucket.availability === "unavailable" || bucket.kwh === null ? "Unavailable — no consumption value" :
            `${displayEnergy(bucket.kwh)} kWh${bucket.availability === "zero_or_unavailable" ? " reported; availability unverified" : ""}`;
          const description = `${bucketLabel(this.view, report, bucket)}: ${detail}${bucket.provisional && !futureZero ? " · provisional" : ""}${bucket.stale ? " · cached" : ""}`;
          column.title = description; plot.title = description;
          plot.setAttribute("aria-label", description); plot.setAttribute("aria-describedby", "energy-readout");
          for (const event of ["pointerenter", "focus", "click"])
            plot.addEventListener(event, () => { readout.textContent = description; });
          if (bucket.kwh !== null && bucket.availability === "reported" && !futureZero) {
            const bar = node("div", undefined, "bar");
            bar.style.height = `${Math.max(0, bucket.kwh / scale.top * 100)}%`; plot.append(bar);
          } else plot.append(node("span", bucket.availability === "future" || (futureZero && bucket.availability !== "zero_or_unavailable") ? "—" : "?", "missing"));
          column.append(plot, node("div", bucket.label, "label")); chart.append(column);
        }
        const scroll = node("div", undefined, "scroll"); scroll.append(chart);
        if (this.view === "day") dayChart = {scroll, noon};
        frame.append(axis, scroll); card.append(frame, readout);
        card.append(node("p", "Open Consumption details for exact reported values.", "note"));
        const details = node("details"); details.append(node("summary", "Consumption details"));
        const table = node("table"), head = node("tr");
        for (const text of ["Interval", "kWh", "Availability"]) head.append(node("th", text));
        table.append(head);
        for (const bucket of report.buckets) {
          const row = node("tr");
          const status = futureZeros.has(bucket) ? `Future hour${bucket.availability === "zero_or_unavailable" ? "; availability unverified" : " (provider zero)"}` :
            bucket.availability === "future" ? "Future" : bucket.availability === "unavailable" ? "Unavailable" :
            bucket.availability === "zero_or_unavailable" ? "Zero or unavailable" : bucket.provisional ? "Provisional" : "Reported";
          row.append(node("td", bucket.date || bucket.label), node("td", bucket.kwh === null ? "—" : exactEnergy(bucket.kwh), "value"),
            node("td", `${status}${bucket.stale ? " (cached)" : ""}`)); table.append(row);
        }
        details.append(table);
        if (this.view === "day" && report.incomplete) details.append(node("p",
          "Future-hour labels apply only to later zero-valued slots with a verified New York clock mapping. Provider values are preserved above; zeros do not establish that an interval is complete. Unverified DST hours are not inferred.", "note"));
        if (report.oldest_report_at) {
          const updated = new Date(report.oldest_report_at);
          if (Number.isFinite(updated.getTime())) details.append(node("p", `Oldest source report: ${new Intl.DateTimeFormat("en-US", {
            timeZone: ZONE, dateStyle: "medium", timeStyle: "short",
          }).format(updated)} (${ZONE}).`, "updated"));
        }
        details.append(node("p", "Provider-reported kWh. History observed: monthly ≈ Oct 2021 onward; daily verified Jan 2024 onward; hourly retention unverified. Older periods may be available. Monthly totals never fill missing daily/hourly bars.", "note"));
        details.append(node("p", `Calendar dates use ${ZONE}. Read-only viewer · ${VERSION}`, "note"));
        card.append(details);
      }
      this.shadowRoot.replaceChildren(style, card);
      this._mountDayScroll(dayChart);
    }
    getCardSize() { return 7; }
  }
  if (!customElements.get(TAG)) customElements.define(TAG, BathroomHeatEnergy);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((c) => c.type === TAG)) window.customCards.push({
    type: TAG, name: "Bathroom Floor Heat Energy", description: "Read-only Schluter Day / Week / Month / Year reports",
  });
})();