"use strict";
// No HA or Schluter API required. Exercise actual browser-facing scaler logic.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const registered = new Map();
const styles = new Map();
const bodyListeners = new Map();
const popupCalls = [];
let width = 450;
let observer;
let listenerCount = 0;
const root = {
  style: {setProperty(key, value) {styles.set(key, value);}},
  getBoundingClientRect() {return {width};},
  contains() {return true;},
};
// Deliberately expose a WRONG page-level #root to catch a reversion to
// selecting document.querySelector("#root") before closest("#root").
const unrelatedRoot = {
  getBoundingClientRect() {return {width: 1700};},
  contains() {return false;},
};
const shadowRoot = {
  querySelector(selector) {
    assert.equal(selector, "#root");
    return unrelatedRoot;
  },
};
class FakeHTMLElement {
  constructor() {
    this.style = {};
    this.isConnected = false;
  }
  setAttribute() {}
  getRootNode() {return shadowRoot;}
  closest(selector) {assert.equal(selector, "#root"); return root;}
  replaceChildren(...children) {this.children = children;}
}
class FakeResizeObserver {
  constructor(callback) {
    this.callback = callback;
    this.disconnected = false;
    observer = this;
  }
  observe(el) {assert.equal(el, root);}
  disconnect() {this.disconnected = true;}
  trigger() {this.callback();}
}
const context = {
  HTMLElement: FakeHTMLElement,
  ResizeObserver: FakeResizeObserver,
  customElements: {
    get(key) {return registered.get(key);},
    define(key, value) {registered.set(key, value);},
  },
  window: {
    location: {href: "http://test"},
    addEventListener() {listenerCount++;},
    removeEventListener() {listenerCount--;},
  },
  document: {
    body: {addEventListener(eventName, listener) {
      bodyListeners.set(eventName, listener);
    }},
    createElement(tag) {
      assert.equal(tag, "button");
      const style = {
        priorities: {},
        setProperty(key, value, priority) {
          this[key] = value;
          this.priorities[key] = priority;
        },
      };
      return {style, listeners: {}, addEventListener(eventName, listener) {
        this.listeners[eventName] = listener;
      }};
    },
  },
  Intl, Date, Math, Number, Map, Set, Object, Error, String,
  setTimeout, clearTimeout, console,
};
const filename = path.join(__dirname, "../www/bathroom-heat-controls.js");
vm.runInNewContext(fs.readFileSync(filename, "utf8"), context, {
  filename, timeout: 2000,
});
assert.equal(context.window.bathroomHeatControls.version, "1.0.23");
const ScheduleButton = registered.get("bathroom-heat-schedule-button");
assert.equal(typeof ScheduleButton, "function");
const scalerModule = fs.readFileSync(filename, "utf8");
assert.ok(scalerModule.includes('if (this.config?.compact === true)'));
assert.ok(scalerModule.includes('button.style.setProperty("height", compactHeight, "important")'));
assert.ok(scalerModule.includes('button.style.setProperty("min-height", "0px", "important")'));
assert.ok(scalerModule.includes('button.style.setProperty("max-height", compactHeight, "important")'));
// Compact and original buttons share the same JavaScript without
// accidentally changing the original Picture Elements button.
const compactButton = new ScheduleButton();
compactButton.setConfig({
  entity: "climate.bath_heat_2",
  service_domain: "schluter_heat_extended",
  compact: true,
});
compactButton.connectedCallback();
const compactNative = compactButton.children[0];
assert.equal(compactNative.style.height, "var(--bath-heat-schedule-height, 38px)");
assert.equal(compactNative.style.priorities.height, "important");
assert.equal(compactNative.style.priorities["min-height"], "important");
assert.equal(compactNative.style["min-height"], "0px");
assert.equal(compactNative.style.whiteSpace, "nowrap");

const originalButton = new ScheduleButton();
originalButton.setConfig({
  entity: "climate.bath_heat_2",
  service_domain: "schluter_heat_extended",
});
originalButton.connectedCallback();
const originalNative = originalButton.children[0];
assert.equal(originalNative.style.priorities.height, undefined);
assert.equal(originalNative.style.height, undefined);

// Existing full-size native button still opens the normal Browser Mod popup.
context.window.browser_mod = {
  openPopups: [],
  service(service, config) {popupCalls.push({service, config});},
};
originalNative.listeners.click();
assert.equal(popupCalls.length, 1);
assert.equal(popupCalls[0].service, "browser_mod.popup");
assert.equal(popupCalls[0].config.content.type, "custom:bathroom-heat-schedule");
assert.equal(popupCalls[0].config.content.entity, "climate.bath_heat_2");

// The compact card uses the Picture Elements image's fire-dom-event action.
// It must open the SAME popup, routed to the same extended domain.
const Target = registered.get("bathroom-heat-target");
const target = new Target();
target.isConnected = true;
target.setConfig({
  entity: "climate.bath_heat_2", service_domain: "schluter_heat_extended",
});
assert.ok(bodyListeners.has("ll-custom"));
bodyListeners.get("ll-custom")({
  target,
  detail: {bathroom_heat: {
    entity: "climate.bath_heat_2", action: "schedule",
  }},
});
assert.equal(popupCalls.length, 2);
assert.equal(popupCalls[1].service, "browser_mod.popup");
assert.equal(popupCalls[1].config.content.type, "custom:bathroom-heat-schedule");
assert.equal(popupCalls[1].config.content.entity, "climate.bath_heat_2");
assert.equal(popupCalls[1].config.content.service_domain, "schluter_heat_extended");

const Scaler = registered.get("bathroom-heat-scaler");
assert.equal(typeof Scaler, "function");
for (const name of ["bathroom-heat-target", "bathroom-heat-status",
  "bathroom-heat-schedule-button", "bathroom-heat-schedule"]) {
  assert.ok(registered.has(name), `missing ${name}`);
}
const element = new Scaler();
element.setConfig({font_scale: 1});
element.isConnected = true;
element.connectedCallback();
assert.ok(observer);
const px = (key) => Number.parseFloat(styles.get(`--bath-heat-${key}`));
assert.ok(Math.abs(px("clock") - width * .029) < .02);
assert.ok(Math.abs(px("target") - width * .076) < .02);
assert.ok(Math.abs(px("next") - width * .023) < .02);
assert.ok(Math.abs(px("arrow-width") - width * .087) < .02);
assert.ok(Math.abs(px("schedule-height") - width * .086) < .02);
const fullClock = px("clock");
width = 300; // 2-column tablet/dashboard layout
observer.trigger();
assert.ok(px("clock") < fullClock, "clock must shrink when card shrinks");
assert.ok(Math.abs(px("clock") - Math.max(9, width * .029)) < .02);
assert.ok(Math.abs(px("target") - width * .076) < .02);
assert.ok(Math.abs(px("schedule-height") - 26) < .02,
  "Edit Schedule button should be short at 2 columns");
element.setConfig({font_scale: 1.2});
assert.ok(px("clock") > width * .029, "font_scale must enlarge text");
assert.ok(Math.abs(px("arrow-width") - Math.max(28, width * .087)) < .02,
  "font_scale must not enlarge arrow hit target");
assert.throws(() => element.setConfig({font_scale: 10}), /font_scale/);
element.disconnectedCallback();
assert.equal(observer.disconnected, true);

const noWrites = fs.readFileSync(filename, "utf8").split(
  "class BathroomHeatScaler extends HTMLElement", 2)[1].split(
    "class BathroomHeatTarget extends HTMLElement", 1)[0];
assert.ok(!noWrites.includes("callService"));
assert.ok(!noWrites.includes("callWS"));
console.log("Responsive card tests passed: measured width, scale, resize, cleanup.");
