// emulation.test.mjs — the `emulate` op: device emulation for real mobile testing.
//
// TWO halves, deliberately separated:
//   * PURE (protocol.js): the preset table's integrity, the validation refusals,
//     and the ORDERED CDP step list. No chrome, no mock, no I/O.
//   * GLUE (service_worker.js against a mocked chrome): the properties that only
//     exist once the pieces are wired — sticky re-application inside EVERY later
//     op's CDP session, `nav` applying emulation BEFORE navigating, the screenshot
//     fast-path being disabled, `click` switching to touch, per-tab isolation, and
//     the state lifecycle (reset / close / vanished tab).
//
// ⚠ WHAT THESE TESTS DO NOT PROVE. Every CDP assertion here is against a MOCK
// `chrome.debugger.sendCommand` that records method names and params. That proves
// the bridge SENDS the right protocol calls in the right order — it does NOT prove
// Chromium honours them, that the emulated viewport actually renders at 393px, or
// that a real site's UA sniffing is fooled. Those need a live browser and are
// listed as open items in the PR. Green here is a PREREQUISITE, not verification.

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  DEVICE_PRESETS, PRESET_NAMES, PRESET_REQUIRED_KEYS, EMULATION_LIMITS,
  normalizeEmulation, emulationCdpSteps, applyEmulationSteps, emulationSummary,
  isTouchEmulated, touchTapEvents, ALLOWED_OPS, EMULATION_MAX_STEPS,
  CDP_OP_BUDGET_MS, EXEC_OP_BUDGET_MS,
} from "../extension/protocol.js";

// --------------------------------------------------------------------------- //
// PURE: the preset table
// --------------------------------------------------------------------------- //

test("preset table: every preset carries every required key", () => {
  assert.ok(PRESET_NAMES.length >= 5, "the curated table must not be empty");
  for (const name of PRESET_NAMES) {
    const p = DEVICE_PRESETS[name];
    for (const key of PRESET_REQUIRED_KEYS) {
      assert.ok(Object.prototype.hasOwnProperty.call(p, key),
        `preset ${name} is missing '${key}' — a half-filled preset silently `
        + "emulates a phone-sized DESKTOP browser");
    }
  }
});

test("preset table: metrics are inside the raw-override limits and plausible", () => {
  for (const name of PRESET_NAMES) {
    const p = DEVICE_PRESETS[name];
    assert.ok(Number.isInteger(p.width) && p.width >= EMULATION_LIMITS.minDimension
      && p.width <= EMULATION_LIMITS.maxDimension, `${name}: width`);
    assert.ok(Number.isInteger(p.height) && p.height >= EMULATION_LIMITS.minDimension
      && p.height <= EMULATION_LIMITS.maxDimension, `${name}: height`);
    // A device preset is a REAL handheld: narrower than 1024 CSS px and taller
    // than it is wide. A preset that fails this is almost certainly a physical
    // resolution pasted in where a logical one belongs — the classic error.
    assert.ok(p.width <= 1024, `${name}: width ${p.width} is desktop-sized — did a `
      + "PHYSICAL resolution get pasted in place of the logical one?");
    assert.ok(p.height > p.width, `${name}: presets are defined portrait`);
    assert.ok(p.deviceScaleFactor >= EMULATION_LIMITS.minScaleFactor
      && p.deviceScaleFactor <= EMULATION_LIMITS.maxScaleFactor,
      `${name}: deviceScaleFactor`);
    assert.ok(p.deviceScaleFactor >= 1.5,
      `${name}: every modern handheld is HiDPI — dsf < 1.5 is a typo`);
    assert.equal(p.mobile, true, `${name}: mobile must be true`);
    assert.ok(p.maxTouchPoints >= 1
      && p.maxTouchPoints <= EMULATION_LIMITS.maxTouchPoints, `${name}: touch points`);
  }
});

test("preset table: physical pixels / dsf reproduces the CSS viewport", () => {
  // The internal-consistency check that catches a transcription error in either
  // number. Tolerance 1px: vendors round the logical resolution.
  for (const name of PRESET_NAMES) {
    const p = DEVICE_PRESETS[name];
    if (!p.physical) continue;
    for (const dim of ["width", "height"]) {
      const derived = p.physical[dim] / p.deviceScaleFactor;
      assert.ok(Math.abs(derived - p[dim]) <= 1,
        `${name}: ${p.physical[dim]}px physical / dsf ${p.deviceScaleFactor} = `
        + `${derived.toFixed(2)}, but the preset says ${p[dim]}`);
    }
  }
});

test("preset table: every preset sets a UA *and* UA-Client-Hints metadata", () => {
  // THE most commonly missed half. A preset with a mobile UA string and no
  // userAgentMetadata leaves navigator.userAgentData reporting the operator's real
  // desktop Chrome to any site that asks — which is most of them now.
  for (const name of PRESET_NAMES) {
    const p = DEVICE_PRESETS[name];
    assert.ok(typeof p.userAgent === "string" && p.userAgent.length > 20,
      `${name}: userAgent`);
    assert.ok(/mobile/i.test(p.userAgent) || /iPad/.test(p.userAgent),
      `${name}: a device preset's UA must read as a handheld`);
    const m = p.userAgentMetadata;
    assert.ok(m && typeof m === "object", `${name}: userAgentMetadata missing`);
    assert.equal(m.mobile, true,
      `${name}: userAgentMetadata.mobile must agree with the UA string`);
    assert.ok(typeof m.platform === "string" && m.platform.length > 0,
      `${name}: userAgentMetadata.platform`);
    assert.ok(Array.isArray(m.brands), `${name}: brands must be an array`);
    // Apple devices legitimately have NO brands (Safari sends no Sec-CH-UA);
    // Android must carry them or client-hint sniffing sees nothing.
    if (m.platform === "Android") {
      assert.ok(m.brands.length >= 2,
        `${name}: an Android preset must carry Chrome's brand list`);
    }
    assert.ok(typeof p.source === "string" && p.source.length > 20,
      `${name}: 'source' must state where the metrics came from`);
  }
});

test("preset table: the `browser` CLI's duplicated name list has not drifted", () => {
  // bash cannot import DEVICE_PRESETS, so the CLI repeats the names in its usage
  // text and in `--list`. This is the guard that makes that duplication safe.
  const cliPath = fileURLToPath(new URL("../browser", import.meta.url));
  const cli = readFileSync(cliPath, "utf8");
  const marker = cli.match(/# PRESET_NAMES: (.+)/);
  assert.ok(marker, "the `browser` CLI must carry a '# PRESET_NAMES:' marker line");
  const listed = marker[1].trim().split(/\s+/);
  assert.deepEqual(listed.slice().sort(), PRESET_NAMES.slice().sort(),
    "the CLI's preset list disagrees with DEVICE_PRESETS");
  // …and the `--list` output block prints exactly those names too.
  const listBlock = cli.match(/printf '  %s\\n' ([^\n]+)/);
  assert.ok(listBlock, "`browser emulate --list` must print the preset names");
  assert.deepEqual(listBlock[1].trim().split(/\s+/).sort(), PRESET_NAMES.slice().sort());
});

test("`emulate` is in the extension's ALLOWED_OPS", () => {
  assert.ok(ALLOWED_OPS.includes("emulate"));
});

// --------------------------------------------------------------------------- //
// PURE: validation refusals
// --------------------------------------------------------------------------- //

function refusal(cmd) {
  try {
    normalizeEmulation(cmd);
  } catch (e) {
    return e.message;
  }
  throw new Error(`normalizeEmulation(${JSON.stringify(cmd)}) did NOT throw`);
}

test("unknown preset is refused by NAME (so the caller can see what they typed)", () => {
  assert.equal(refusal({ device: "iphone-42" }), "unknown_preset:iphone-42");
  assert.equal(refusal({ device: "IPHONE-15" }), "unknown_preset:IPHONE-15");
});

test("out-of-range raw params are refused, per field", () => {
  assert.equal(refusal({ width: 0, height: 800 }), "invalid_emulation:width");
  assert.equal(refusal({ width: -5, height: 800 }), "invalid_emulation:width");
  assert.equal(refusal({ width: 400, height: 0 }), "invalid_emulation:height");
  assert.equal(refusal({ width: EMULATION_LIMITS.maxDimension + 1, height: 800 }),
    "invalid_emulation:width");
  assert.equal(refusal({ width: 400.5, height: 800 }), "invalid_emulation:width");
  assert.equal(refusal({ width: "wide", height: 800 }), "invalid_emulation:width");
  assert.equal(refusal({ width: 400, height: 800, dsf: 0 }), "invalid_emulation:dsf");
  assert.equal(refusal({ width: 400, height: 800, dsf: 99 }), "invalid_emulation:dsf");
  assert.equal(refusal({ width: 400, height: 800, dsf: -2 }), "invalid_emulation:dsf");
  assert.equal(refusal({ device: "pixel-8", maxTouchPoints: 0 }),
    "invalid_emulation:maxTouchPoints");
  assert.equal(refusal({ device: "pixel-8",
                         maxTouchPoints: EMULATION_LIMITS.maxTouchPoints + 1 }),
    "invalid_emulation:maxTouchPoints");
});

test("contradictory params are refused rather than silently resolved", () => {
  // Half a viewport is not a partial override you can complete: guessing the other
  // dimension from the real window would make the result depend on the operator's
  // window size, i.e. not reproducible.
  assert.equal(refusal({ width: 400 }), "invalid_emulation:width_and_height_together");
  assert.equal(refusal({ height: 800 }), "invalid_emulation:width_and_height_together");
  // --reset means "stop"; combining it with a device description is incoherent.
  assert.equal(refusal({ reset: true, device: "iphone-15" }),
    "invalid_emulation:reset_with_params");
  assert.equal(refusal({ reset: true, width: 400, height: 800 }),
    "invalid_emulation:reset_with_params");
  // maxTouchPoints while explicitly disabling touch.
  assert.equal(refusal({ device: "pixel-8", touch: false, maxTouchPoints: 5 }),
    "invalid_emulation:max_touch_points_without_touch");
});

test("an `emulate` with nothing to do is refused, not treated as a no-op", () => {
  assert.equal(refusal({}), "emulate_needs_device_or_params");
  assert.equal(refusal({ device: "", width: "", ua: "" }),
    "emulate_needs_device_or_params");
});

test("a UA carrying CR/LF is REFUSED, never sanitized", () => {
  // The UA is echoed into a request header by Chromium; CR/LF is the classic
  // header-injection primitive. Refusing beats stripping — silently rewriting
  // input leaves you unsure what went on the wire.
  assert.equal(refusal({ ua: "Mozilla/5.0\r\nX-Evil: 1" }), "invalid_emulation:ua");
  assert.equal(refusal({ ua: "Mozilla/5.0\nX-Evil: 1" }), "invalid_emulation:ua");
  assert.equal(refusal({ ua: "Mozilla /5.0" }), "invalid_emulation:ua");
  assert.equal(refusal({ ua: "x".repeat(EMULATION_LIMITS.maxUserAgentChars + 1) }),
    "invalid_emulation:ua");
  assert.equal(refusal({ ua: 12345 }), "invalid_emulation:ua");
});

test("timezone / colour-scheme / orientation / geo are validated", () => {
  assert.equal(refusal({ tz: "Europe/London; rm -rf" }), "invalid_emulation:tz");
  assert.equal(refusal({ tz: "x".repeat(EMULATION_LIMITS.maxTimezoneChars + 1) }),
    "invalid_emulation:tz");
  assert.equal(refusal({ colorScheme: "sepia" }), "invalid_emulation:colorScheme");
  assert.equal(refusal({ device: "iphone-15", orientation: "sideways" }),
    "invalid_emulation:orientation");
  assert.equal(refusal({ geo: { latitude: 91, longitude: 0 } }),
    "invalid_emulation:geo.latitude");
  assert.equal(refusal({ geo: { latitude: 0, longitude: 181 } }),
    "invalid_emulation:geo.longitude");
  assert.equal(refusal({ geo: [1, 2] }), "invalid_emulation:geo");
});

// --------------------------------------------------------------------------- //
// PURE: the normalized state + the ordered CDP steps
// --------------------------------------------------------------------------- //

test("a preset normalizes to the preset's metrics, touch and UA", () => {
  const s = normalizeEmulation({ device: "iphone-15" });
  assert.equal(s.preset, "iphone-15");
  assert.deepEqual(s.metrics, {
    width: 393, height: 852, deviceScaleFactor: 3, mobile: true,
    screenOrientation: { type: "portraitPrimary", angle: 0 },
  });
  assert.deepEqual(s.touch, { enabled: true, maxTouchPoints: 5 });
  assert.equal(s.ua.userAgent, DEVICE_PRESETS["iphone-15"].userAgent);
  assert.equal(s.ua.userAgentMetadata, DEVICE_PRESETS["iphone-15"].userAgentMetadata);
});

test("raw params override a preset's fields", () => {
  const s = normalizeEmulation({ device: "iphone-15", width: 320, height: 480,
                                 dsf: 1, touch: false });
  assert.equal(s.metrics.width, 320);
  assert.equal(s.metrics.height, 480);
  assert.equal(s.metrics.deviceScaleFactor, 1);
  assert.equal(s.metrics.mobile, true, "the preset's mobile flag survives");
  assert.deepEqual(s.touch, { enabled: false, maxTouchPoints: 1 });
  assert.equal(isTouchEmulated(s), false);
});

test("--orientation landscape swaps the viewport, not just the angle", () => {
  const s = normalizeEmulation({ device: "iphone-15", orientation: "landscape" });
  assert.equal(s.metrics.width, 852);
  assert.equal(s.metrics.height, 393);
  assert.deepEqual(s.metrics.screenOrientation, { type: "landscapePrimary", angle: 90 });
});

test("a RAW --ua still gets userAgentMetadata (never the operator's real brands)", () => {
  const s = normalizeEmulation({ width: 390, height: 844, mobile: true,
                                 ua: "Mozilla/5.0 (custom) Mobile" });
  assert.ok(s.ua.userAgentMetadata, "raw --ua must not leave metadata unset");
  assert.equal(s.ua.userAgentMetadata.mobile, true);
  assert.deepEqual(s.ua.userAgentMetadata.brands, []);
});

test("the CDP step list is ORDERED, and every step is required", () => {
  const s = normalizeEmulation({
    device: "pixel-8", colorScheme: "dark", tz: "Europe/London",
    geo: { latitude: 51.5, longitude: -0.12 },
  });
  const steps = emulationCdpSteps(s);
  assert.deepEqual(steps.map((x) => x.method), [
    "Emulation.setDeviceMetricsOverride",
    "Emulation.setTouchEmulationEnabled",
    "Emulation.setUserAgentOverride",
    "Emulation.setEmulatedMedia",
    "Emulation.setTimezoneOverride",
    "Emulation.setGeolocationOverride",
  ]);
  // Metrics FIRST is load-bearing: anything that measures layout afterwards
  // (a --fullpage clip, a click's element rect) must measure the emulated page.
  assert.equal(steps[0].method, "Emulation.setDeviceMetricsOverride");
  // No step is optional — a half-applied emulation returns a plausible screenshot
  // of the wrong thing, which is worse than a refusal.
  assert.ok(steps.every((x) => !x.optional));
});

test("the UA step carries BOTH userAgent and userAgentMetadata", () => {
  const s = normalizeEmulation({ device: "pixel-8" });
  const ua = emulationCdpSteps(s)
    .find((x) => x.method === "Emulation.setUserAgentOverride");
  assert.ok(ua, "a preset must produce a setUserAgentOverride step");
  assert.ok(ua.params.userAgent.includes("Android"));
  assert.ok(ua.params.userAgentMetadata, "userAgentMetadata is the missed half");
  assert.equal(ua.params.userAgentMetadata.platform, "Android");
  assert.equal(ua.params.userAgentMetadata.mobile, true);
  assert.ok(ua.params.userAgentMetadata.brands.length >= 2);
});

test("media / timezone / geolocation each produce their exact CDP params", () => {
  const s = normalizeEmulation({
    colorScheme: "dark", tz: "America/Los_Angeles",
    geo: { latitude: 37.77, longitude: -122.41, accuracy: 25 },
  });
  const by = Object.fromEntries(emulationCdpSteps(s).map((x) => [x.method, x.params]));
  assert.deepEqual(by["Emulation.setEmulatedMedia"], {
    media: "", features: [{ name: "prefers-color-scheme", value: "dark" }],
  });
  assert.deepEqual(by["Emulation.setTimezoneOverride"],
    { timezoneId: "America/Los_Angeles" });
  assert.deepEqual(by["Emulation.setGeolocationOverride"],
    { latitude: 37.77, longitude: -122.41, accuracy: 25 });
  // …and NO device metrics were invented for a media-only emulation.
  assert.equal(by["Emulation.setDeviceMetricsOverride"], undefined);
});

test("`reset` yields no steps at all", () => {
  const s = normalizeEmulation({ reset: true });
  assert.deepEqual(s, { reset: true });
  assert.deepEqual(emulationCdpSteps(s), []);
  assert.equal(emulationSummary(s), null);
  assert.equal(isTouchEmulated(s), false);
});

test("applyEmulationSteps propagates the FIRST failure (no optional swallowing)", () => {
  const seen = [];
  const send = async (method) => {
    seen.push(method);
    if (method === "Emulation.setUserAgentOverride") throw new Error("nope");
    return {};
  };
  const s = normalizeEmulation({ device: "pixel-8", tz: "UTC" });
  return assert.rejects(() => applyEmulationSteps(send, emulationCdpSteps(s)),
    /nope/).then(() => {
      // It stopped AT the failure — the later steps were never attempted, so the
      // caller cannot be told "applied" about something that never ran.
      assert.deepEqual(seen, [
        "Emulation.setDeviceMetricsOverride",
        "Emulation.setTouchEmulationEnabled",
        "Emulation.setUserAgentOverride",
      ]);
    });
});

test("BUDGET: the step count is bounded by a CONSTANT, not by caller input", () => {
  // The added per-op cost has to be analysable against EXEC_OP_BUDGET_MS. It is,
  // because nothing a caller sends can make the apply longer — the step list is
  // one entry per emulation FACET, and there are a fixed number of facets.
  const maximal = normalizeEmulation({
    device: "pixel-8", width: 400, height: 900, dsf: 3, mobile: true,
    touch: true, maxTouchPoints: 10, ua: "Mozilla/5.0 (custom) Mobile",
    orientation: "portrait", colorScheme: "dark", tz: "Europe/London",
    geo: { latitude: 51.5, longitude: -0.12, accuracy: 5 },
  });
  assert.equal(emulationCdpSteps(maximal).length, EMULATION_MAX_STEPS,
    "the maximal emulation must hit exactly the documented ceiling");
  for (const name of PRESET_NAMES) {
    assert.ok(emulationCdpSteps(normalizeEmulation({ device: name })).length
      <= EMULATION_MAX_STEPS);
  }
  // And the budgets it composes against are the ones the comment names, so a
  // change to either fails here rather than silently invalidating the analysis.
  assert.equal(CDP_OP_BUDGET_MS, 15000);
  assert.equal(EXEC_OP_BUDGET_MS, 18000);
});

test("touchTapEvents is the DevTools-shaped touchStart/touchEnd pair", () => {
  const evs = touchTapEvents(10, 20);
  assert.deepEqual(evs.map((e) => e.method),
    ["Input.dispatchTouchEvent", "Input.dispatchTouchEvent"]);
  assert.equal(evs[0].params.type, "touchStart");
  assert.deepEqual(evs[0].params.touchPoints[0].x, 10);
  assert.deepEqual(evs[0].params.touchPoints[0].y, 20);
  assert.equal(evs[1].params.type, "touchEnd");
  assert.deepEqual(evs[1].params.touchPoints, [],
    "touchEnd carries no remaining points");
});

test("emulationSummary is METADATA ONLY — no UA string, no coordinates", () => {
  const s = normalizeEmulation({
    device: "iphone-15", colorScheme: "dark", tz: "UTC",
    geo: { latitude: 51.5, longitude: -0.12 },
  });
  const sum = emulationSummary(s);
  const blob = JSON.stringify(sum);
  assert.ok(!blob.includes("AppleWebKit"), "the UA string must not be in the summary");
  assert.ok(!blob.includes("51.5"), "coordinates must not be in the summary");
  assert.ok(!blob.includes("-0.12"));
  assert.equal(sum.geolocation, true, "…but its PRESENCE is reported");
  assert.equal(sum.userAgentOverridden, true);
  assert.equal(sum.preset, "iphone-15");
  assert.equal(sum.width, 393);
});

// --------------------------------------------------------------------------- //
// GLUE: service_worker.js against a mocked chrome
// --------------------------------------------------------------------------- //

const TAB_A = 11;
const TAB_B = 22;

const sw = {
  cdp: [],                 // ordered [{method, params}] of every CDP send
  events: [],              // ordered "attach"/"detach" + method names
  tabs: new Map(),
  gone: new Set(),         // tabIds chrome.tabs.get must reject for
  removedListeners: [],
  tabsUpdate: [],
  tabsRemove: [],
  layoutMetrics: { cssContentSize: { width: 393, height: 4200 } },
  elementRect: { x: 10, y: 20, width: 100, height: 40 },
  failMethod: null,        // make one CDP method reject (the failure path)
  hangMethod: null,        // make one CDP method never settle (the no-wedge path)
};

function resetSw() {
  sw.cdp = [];
  sw.events = [];
  sw.gone = new Set();
  sw.tabsUpdate = [];
  sw.tabsRemove = [];
  sw.failMethod = null;
  sw.hangMethod = null;
  sw.tabs = new Map([
    [TAB_A, { id: TAB_A, url: "https://example.com/a", title: "A",
              active: true, status: "complete", windowId: 1 }],
    [TAB_B, { id: TAB_B, url: "https://example.com/b", title: "B",
              active: false, status: "complete", windowId: 1 }],
  ]);
}
resetSw();

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.BROWSER_BRIDGE_ACTIVATE_TIMING = { settleMs: 0, pollMs: 1 };
globalThis.chrome = {
  webNavigation: { async getAllFrames() { return []; } },
  scripting: {
    async executeScript() { return [{ result: { visibilityState: "visible" } }]; },
  },
  tabs: {
    async get(id) {
      if (sw.gone.has(id)) throw new Error("No tab with id");
      const t = sw.tabs.get(id);
      if (!t) throw new Error("No tab with id");
      return { ...t };
    },
    async query() { return [sw.tabs.get(TAB_A)]; },
    async captureVisibleTab() {
      sw.events.push("captureVisibleTab");
      return "data:image/png;base64,AAAA";
    },
    async update(id, props) { sw.tabsUpdate.push({ id, props }); return sw.tabs.get(id); },
    async remove(id) { sw.tabsRemove.push(id); sw.gone.add(id); sw.tabs.delete(id); },
    onRemoved: { addListener(fn) { sw.removedListeners.push(fn); } },
  },
  windows: { async update() {} },
  debugger: {
    async attach() { sw.events.push("attach"); },
    async detach() { sw.events.push("detach"); },
    async sendCommand(_t, method, params) {
      sw.cdp.push({ method, params });
      sw.events.push(method);
      // A promise that NEVER settles — the real "hung chrome.debugger call" shape.
      // Deliberately not a long timer: the point is that promiseWithTimeout settles
      // the awaiter and abandons this, so nothing here may keep the loop alive.
      if (sw.hangMethod === method) return new Promise(() => {});
      if (sw.failMethod === method) throw new Error(`cdp_failed:${method}`);
      if (method === "Page.captureScreenshot") return { data: "QkJCQg==" };
      if (method === "Page.getLayoutMetrics") return sw.layoutMetrics;
      if (method === "Runtime.evaluate") {
        return { result: { value: sw.elementRect } };
      }
      return {};
    },
    onDetach: { addListener() {} },
    onEvent: { addListener() {}, removeListener() {} },
  },
  storage: { local: { async get() { return {}; }, async set() {} } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} },
             getManifest: () => ({ version: "0.5.0" }), id: "test-ext-id" },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS, execute, emulationState } =
  await import("../extension/service_worker.js");

function methods() { return sw.cdp.map((c) => c.method); }
function paramsOf(method) {
  const hit = sw.cdp.find((c) => c.method === method);
  return hit ? hit.params : undefined;
}
function fresh() { resetSw(); emulationState.clear(); }

// The exact ordered prefix an emulated tab's CDP session must open with.
const IPHONE_PREFIX = [
  "Emulation.setDeviceMetricsOverride",
  "Emulation.setTouchEmulationEnabled",
  "Emulation.setUserAgentOverride",
];

test("emulate: stores the state and applies the overrides in order, then detaches", async () => {
  fresh();
  const out = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(out.tabId, TAB_A);
  assert.equal(out.emulation.preset, "iphone-15");
  assert.equal(out.emulation.width, 393);
  assert.deepEqual(methods(), IPHONE_PREFIX);
  // Attach → the overrides → detach. The session did not stay open.
  assert.deepEqual(sw.events, ["attach", ...IPHONE_PREFIX, "detach"]);
  assert.ok(emulationState.has(TAB_A));
});

test("STICKY: a SECOND, separate op re-applies the overrides — exact methods AND order", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];

  // A completely independent later command. Nothing about `screenshot` knows
  // emulation exists — the re-application happens at withCdp's choke point.
  await OPS.screenshot({ tabId: TAB_A });

  assert.deepEqual(methods(), [...IPHONE_PREFIX, "Page.captureScreenshot"],
    "the overrides must be re-applied BEFORE the op's own work, in order");
  assert.deepEqual(sw.events,
    ["attach", ...IPHONE_PREFIX, "Page.captureScreenshot", "detach"]);

  // …and a THIRD op re-applies again (it is not a one-shot).
  sw.cdp = [];
  await OPS.click({ tabId: TAB_A, selector: "#go" });
  assert.deepEqual(methods().slice(0, 3), IPHONE_PREFIX);
});

test("STICKY: the re-applied params are the SAME params, not a re-derived guess", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "galaxy-s24", colorScheme: "dark",
                      tz: "Europe/London" });
  const first = JSON.parse(JSON.stringify(sw.cdp));
  sw.cdp = [];
  await OPS.screenshot({ tabId: TAB_A });
  const second = sw.cdp.filter((c) => c.method.startsWith("Emulation."));
  assert.deepEqual(second, first,
    "a later op must send byte-identical emulation params");
});

test("nav on an emulated tab applies emulation BEFORE navigating (via CDP)", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];

  const out = await OPS.nav({ tabId: TAB_A, url: "https://example.com/next" });

  assert.deepEqual(methods(), [...IPHONE_PREFIX, "Page.navigate"],
    "the UA/viewport must be live BEFORE the navigation a page sniffs at load");
  const navIdx = methods().indexOf("Page.navigate");
  const uaIdx = methods().indexOf("Emulation.setUserAgentOverride");
  assert.ok(uaIdx >= 0 && uaIdx < navIdx,
    "setUserAgentOverride must precede Page.navigate");
  assert.equal(paramsOf("Page.navigate").url, "https://example.com/next");
  assert.equal(out.via, "cdp");
  // The plain chrome.tabs.update path — which would navigate OUTSIDE the emulated
  // session — must not have been taken.
  assert.deepEqual(sw.tabsUpdate, []);
});

test("nav on a NON-emulated tab is unchanged: chrome.tabs.update, no debugger", async () => {
  fresh();
  const out = await OPS.nav({ tabId: TAB_A, url: "https://example.com/plain" });
  assert.deepEqual(sw.cdp, []);
  assert.deepEqual(sw.events, []);
  assert.deepEqual(sw.tabsUpdate, [{ id: TAB_A, props: { url: "https://example.com/plain" } }]);
  assert.equal(out.via, "tabs.update");
});

test("SCREENSHOT: the captureVisibleTab FAST PATH is refused on an emulated tab", async () => {
  fresh();
  // Precondition — the fast path IS taken for this tab when it is NOT emulated
  // (tab.active === true, no --fullpage). Without this the next assertion could
  // pass for the wrong reason.
  const before = await OPS.screenshot({ tabId: TAB_A });
  assert.equal(before.via, "captureVisibleTab");
  assert.ok(sw.events.includes("captureVisibleTab"));
  assert.deepEqual(sw.cdp, [], "the fast path attaches no debugger");

  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];
  const after = await OPS.screenshot({ tabId: TAB_A });

  // THE BUG THIS GUARDS: captureVisibleTab never attaches the debugger, so under
  // emulation it would return a valid PNG of the UN-emulated desktop layout — a
  // confident wrong answer on the one op whose job is showing what the device sees.
  assert.equal(after.via, "cdp");
  assert.ok(!sw.events.includes("captureVisibleTab"),
    "the fast path MUST NOT be taken while the tab is emulated");
  assert.deepEqual(methods(), [...IPHONE_PREFIX, "Page.captureScreenshot"]);
  assert.equal(after.emulation.preset, "iphone-15");
});

test("SCREENSHOT --fullpage: the clip is measured AFTER the emulated metrics land", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = [];
  // The mock reports an emulated-width document; the point of the test is the
  // ORDER, which is what makes the real Page.getLayoutMetrics emulated at all.
  sw.layoutMetrics = { cssContentSize: { width: 393, height: 4200 } };
  await OPS.screenshot({ tabId: TAB_A, fullpage: true });

  const m = methods();
  const metricsIdx = m.indexOf("Emulation.setDeviceMetricsOverride");
  const layoutIdx = m.indexOf("Page.getLayoutMetrics");
  assert.ok(metricsIdx >= 0 && layoutIdx > metricsIdx,
    "Page.getLayoutMetrics must run AFTER setDeviceMetricsOverride, or the clip "
    + "is the size of the operator's real window");
  const clip = paramsOf("Page.captureScreenshot").clip;
  assert.equal(clip.width, 393, "the clip uses the EMULATED width");
  assert.equal(clip.height, 4200);
});

test("CLICK dispatches TOUCH under touch emulation, and MOUSE otherwise", async () => {
  // Mouse is the control. A mobile UI whose handler is `touchstart` never fires
  // under a mouse click — Chromium synthesizes mouse-from-touch, never the reverse.
  fresh();
  const plain = await OPS.click({ tabId: TAB_A, selector: "#go" });
  assert.equal(plain.via, "mouse");
  assert.deepEqual(methods().filter((x) => x.startsWith("Input.")),
    ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent"]);

  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = [];
  const tapped = await OPS.click({ tabId: TAB_A, selector: "#go" });
  assert.equal(tapped.via, "touch");
  assert.deepEqual(methods().filter((x) => x.startsWith("Input.")),
    ["Input.dispatchTouchEvent", "Input.dispatchTouchEvent"]);
  assert.ok(!methods().includes("Input.dispatchMouseEvent"),
    "no mouse event may be dispatched on a touch-emulated tab");
  const touchParams = sw.cdp.filter((c) => c.method === "Input.dispatchTouchEvent");
  assert.equal(touchParams[0].params.type, "touchStart");
  assert.equal(touchParams[1].params.type, "touchEnd");
  // The tap lands at the element's centre, exactly like the mouse path.
  assert.equal(touchParams[0].params.touchPoints[0].x, plain.x);
  assert.equal(touchParams[0].params.touchPoints[0].y, plain.y);
});

test("CLICK stays on MOUSE when emulation is on but touch is explicitly off", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15", touch: false });
  sw.cdp = [];
  const out = await OPS.click({ tabId: TAB_A, selector: "#go" });
  assert.equal(out.via, "mouse");
  assert.deepEqual(methods().filter((x) => x.startsWith("Input.")),
    ["Input.dispatchMouseEvent", "Input.dispatchMouseEvent"]);
});

test("--reset STOPS re-application (and reports what it stopped)", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  const out = await OPS.emulate({ tabId: TAB_A, reset: true });
  assert.equal(out.reset, true);
  assert.equal(out.wasEmulating.preset, "iphone-15");
  assert.equal(emulationState.has(TAB_A), false);

  sw.cdp = []; sw.events = [];
  await OPS.screenshot({ tabId: TAB_A });
  assert.ok(!methods().some((m) => m.startsWith("Emulation.")),
    "no emulation override may be re-applied after --reset");
  // …and the fast path is available again.
  assert.ok(sw.events.includes("captureVisibleTab"));
});

test("--reset on a tab that was never emulated is a clean no-op", async () => {
  fresh();
  const out = await OPS.emulate({ tabId: TAB_A, reset: true });
  assert.equal(out.reset, true);
  assert.equal(out.wasEmulating, null);
  assert.deepEqual(sw.cdp, [], "reset touches no CDP at all");
});

test("`close` clears the tab's emulation state", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.ok(emulationState.has(TAB_A));
  await OPS.close({ tabId: TAB_A });
  assert.equal(emulationState.has(TAB_A), false);
  assert.deepEqual(sw.tabsRemove, [TAB_A]);
});

test("a VANISHED tab drops its emulation state (a recycled tabId can't inherit it)", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.ok(emulationState.has(TAB_A));
  // The tab goes away out-of-band (operator closed it, crash, …).
  sw.gone.add(TAB_A);
  await assert.rejects(() => OPS.screenshot({ tabId: TAB_A }), /owned_tab_gone/);
  assert.equal(emulationState.has(TAB_A), false,
    "the state must not survive the tab it describes");
});

test("chrome.tabs.onRemoved drops the state even with no `close` op", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.ok(sw.removedListeners.length >= 1,
    "the SW must register an onRemoved listener at module scope");
  for (const fn of sw.removedListeners) fn(TAB_A);
  assert.equal(emulationState.has(TAB_A), false);
});

test("ISOLATION: emulating tab A never touches tab B", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];

  await OPS.screenshot({ tabId: TAB_B, fullpage: true });
  assert.ok(!methods().some((m) => m.startsWith("Emulation.")),
    "tab B must see NO emulation overrides");
  assert.equal(emulationState.has(TAB_B), false);

  // And B's own emulation does not disturb A's.
  await OPS.emulate({ tabId: TAB_B, device: "ipad-mini" });
  sw.cdp = [];
  await OPS.screenshot({ tabId: TAB_A, fullpage: true });
  assert.equal(paramsOf("Emulation.setDeviceMetricsOverride").width, 393,
    "tab A must still be the iPhone, not the iPad");
  sw.cdp = [];
  await OPS.screenshot({ tabId: TAB_B, fullpage: true });
  assert.equal(paramsOf("Emulation.setDeviceMetricsOverride").width, 768);
});

test("`tabs` SURFACES emulated tabs (a stuck override is visible, not mysterious)", async () => {
  fresh();
  let out = await OPS.tabs();
  assert.deepEqual(out.emulatedTabs, []);
  assert.ok(out.tabs.every((t) => t.emulation === undefined),
    "a normal listing is unchanged");

  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  out = await OPS.tabs();
  assert.deepEqual(out.emulatedTabs, [TAB_A]);
  const entry = out.tabs.find((t) => t.id === TAB_A);
  assert.equal(entry.emulation.preset, "iphone-15");
  assert.equal(entry.emulation.width, 393);
});

test("WAKE + EMULATE: neither clobbers the other", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];

  await OPS.wake({ tabId: TAB_A, waitMs: 0 });
  const m = methods();

  // The emulation is re-applied inside the wake's own session, BEFORE the wake
  // steps — so a woken tab is still an emulated tab.
  assert.deepEqual(m.slice(0, 3), IPHONE_PREFIX);
  assert.ok(m.includes("Emulation.setFocusEmulationEnabled"),
    "wake still does its own job");
  const metricsIdx = m.indexOf("Emulation.setDeviceMetricsOverride");
  const focusIdx = m.indexOf("Emulation.setFocusEmulationEnabled");
  assert.ok(metricsIdx < focusIdx);

  // wake's teardown disables FOCUS emulation only — it must not reach for
  // setDeviceMetricsOverride/clearDeviceMetricsOverride and undo the device.
  assert.ok(!m.includes("Emulation.clearDeviceMetricsOverride"),
    "wake's teardown must not clear the device emulation");
  const focusCalls = sw.cdp.filter(
    (c) => c.method === "Emulation.setFocusEmulationEnabled");
  assert.deepEqual(focusCalls.map((c) => c.params.enabled), [true, false],
    "focus emulation is turned on then explicitly off — unchanged");
  // The emulation state itself survives the wake.
  assert.ok(emulationState.has(TAB_A));

  // And the NEXT op after the wake is still emulated.
  sw.cdp = [];
  await OPS.screenshot({ tabId: TAB_A });
  assert.deepEqual(methods().slice(0, 3), IPHONE_PREFIX);
});

test("FAILURE PATH: a failing emulation apply is a normal error envelope, and detaches", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];
  sw.failMethod = "Emulation.setUserAgentOverride";

  // Through execute() — the poll loop's real entry point — so this exercises the
  // envelope the loop actually posts.
  const env = await execute({ id: "c1", op: "screenshot", tabId: TAB_A });
  assert.equal(env.ok, false);
  assert.equal(env.id, "c1");
  assert.match(env.error, /cdp_failed:Emulation\.setUserAgentOverride/);
  // Nothing threw past execute (the loop keeps going), and the debugger was
  // released — a failed emulation cannot leak an attachment or wedge the loop.
  assert.equal(sw.events[sw.events.length - 1], "detach");
  // The op's own work never ran on a half-emulated page.
  assert.ok(!methods().includes("Page.captureScreenshot"));
});

test("FAILURE PATH: a failing `emulate` does not leave the state stored", async () => {
  fresh();
  sw.failMethod = "Emulation.setDeviceMetricsOverride";
  const env = await execute({ id: "c2", op: "emulate", tabId: TAB_A,
                             device: "iphone-15" });
  assert.equal(env.ok, false);
  assert.match(env.error, /cdp_failed/);
  assert.equal(emulationState.has(TAB_A), false,
    "a rolled-back emulate must not have every later op retry an emulation the "
    + "caller was told had failed");
});

test("NO-WEDGE: a HUNG emulation step is bounded like any other CDP command", async () => {
  // The re-application runs inside withCdpSession's `run`, and is handed the
  // WRAPPED send — so a hung Emulation.* override settles at CDP_COMMAND_TIMEOUT_MS
  // with `cdp_timeout:<method>` instead of parking the poll loop forever. That is
  // the property #249 exists to protect, and this feature adds CDP calls to every
  // op, so it has to be re-asserted here rather than assumed to be inherited.
  //
  // Shrunk budgets via the same TEST-ONLY hook the wake/loop tests use, so this
  // settles in milliseconds rather than 8 real seconds.
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = []; sw.events = [];
  globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS = { attachMs: 200, commandMs: 40,
                                             budgetMs: 400 };
  sw.hangMethod = "Emulation.setDeviceMetricsOverride";
  try {
    const t0 = Date.now();
    const env = await execute({ id: "hung", op: "screenshot", tabId: TAB_A });
    const elapsed = Date.now() - t0;
    assert.equal(env.ok, false);
    assert.equal(env.error, "cdp_timeout:Emulation.setDeviceMetricsOverride",
      "the hung phase must be NAMED — a generic op_timeout sends the next "
      + "diagnosis to the wrong place");
    assert.ok(elapsed < 350,
      `settled in ${elapsed}ms — it must be bounded by commandMs, not budgetMs`);
    // The debugger was still released: a hung override cannot leak an attachment.
    assert.equal(sw.events[sw.events.length - 1], "detach");
    assert.ok(!methods().includes("Page.captureScreenshot"),
      "the op's own work must not run after a failed apply");
  } finally {
    delete globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS;
    sw.hangMethod = null;
  }
});

test("a bad `emulate` is refused with its NAMED error through execute()", async () => {
  fresh();
  const env = await execute({ id: "c3", op: "emulate", tabId: TAB_A,
                             device: "nokia-3310" });
  assert.equal(env.ok, false);
  assert.equal(env.error, "unknown_preset:nokia-3310");
  assert.deepEqual(sw.cdp, [], "a refused emulate touches no CDP");
  assert.equal(emulationState.has(TAB_A), false);
});
