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
  hasCommittedDocument, emulationCreateTimeSignature, documentPredatesEmulation,
  annotateDocumentPredates, DOCUMENT_PREDATES_EMULATION_NOTE,
  NOT_EMULATED_READ_NOTE,
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
  frames: [],              // chrome.webNavigation.getAllFrames result
  frameTree: null,         // CDP Page.getFrameTree result (same-process --frame)
  removedListeners: [],
  replacedListeners: [],
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
  sw.frames = [];
  sw.frameTree = null;
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
  webNavigation: { async getAllFrames() { return sw.frames; } },
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
    onReplaced: { addListener(fn) { sw.replacedListeners.push(fn); } },
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
      if (method === "Page.getFrameTree") return { frameTree: sw.frameTree };
      if (method === "Page.createIsolatedWorld") return { executionContextId: 42 };
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

const { OPS, execute, emulationState, documentEmulation } =
  await import("../extension/service_worker.js");

function methods() { return sw.cdp.map((c) => c.method); }
function paramsOf(method) {
  const hit = sw.cdp.find((c) => c.method === method);
  return hit ? hit.params : undefined;
}
function fresh() { resetSw(); emulationState.clear(); documentEmulation.clear(); }

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
  assert.equal(paramsOf("Emulation.setDeviceMetricsOverride").width,
    DEVICE_PRESETS["ipad-mini"].width);
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

// --------------------------------------------------------------------------- //
// F1: the NON-CDP read path is NOT emulated — and must say so
//
// `text`/`html`/`js` take the chrome.scripting path, which never attaches the
// debugger, so withCdp's re-application choke point never runs. The DOM they read
// is the tab's real, un-emulated one. That is correct-by-design (between ops the
// tab genuinely is not emulated) but it is a TRAP: an agent screenshots a phone
// layout, then reads `text` and reasons about the DESKTOP DOM. So the envelope has
// to say which one it got.
// --------------------------------------------------------------------------- //

test("F1 REPRODUCTION: a default text/html/js read of an emulated tab issues ZERO CDP calls", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  for (const op of ["text", "getHtml", "eval"]) {
    sw.cdp = []; sw.events = [];
    await OPS[op]({ tabId: TAB_A, js: "1" });
    assert.deepEqual(sw.cdp, [],
      `${op} took the chrome.scripting path — no CDP, so NO emulation was applied`);
    assert.ok(!sw.events.includes("attach"), `${op} must not attach the debugger`);
  }
});

test("F1: a NON-emulated read of an emulated tab is annotated `emulated:false` + a note", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  for (const [op, cmd] of [["text", {}], ["getHtml", {}], ["eval", { js: "1" }]]) {
    const out = await OPS[op]({ tabId: TAB_A, ...cmd });
    assert.equal(out.emulated, false,
      `${op} read the un-emulated DOM and must say so`);
    assert.equal(out.notEmulatedRead, true);
    assert.match(out.emulationNote, /--wake/,
      "the note must name the remedy, like HIDDEN_TAB_NOTE does");
    assert.match(out.emulationNote, /real|un-emulated/i);
  }
});

test("F1: a CDP read (--wake) of an emulated tab is annotated `emulated:true`", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  for (const [op, cmd] of [["text", {}], ["getHtml", {}], ["eval", { js: "1" }]]) {
    const out = await OPS[op]({ tabId: TAB_A, wake: true, waitMs: 0, ...cmd });
    assert.equal(out.emulated, true, `${op} --wake reads inside an emulated session`);
    assert.equal(out.notEmulatedRead, undefined);
    assert.equal(out.emulation.preset, "iphone-15");
  }
});

test("F1: `eval --frame` goes through CDP, so it IS emulated", async () => {
  // A SAME-PROCESS frame (resolved via Page.getFrameTree → createIsolatedWorld) —
  // the cheapest path that still proves `eval --frame` routes through withCdp. The
  // cross-origin OOPIF cascade is exercised in frame_oopif.test.mjs; replicating
  // its auto-attach event machinery here would test the mock, not the annotation.
  fresh();
  const FRAME_URL = "https://example.com/a/inner";
  sw.frames = [{ frameId: 0, parentFrameId: -1, url: "https://example.com/a" },
               { frameId: 3, parentFrameId: 0, url: FRAME_URL }];
  sw.frameTree = {
    frame: { id: "TOPF", url: "https://example.com/a" },
    childFrames: [{ frame: { id: "SUBF", url: FRAME_URL } }],
  };
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.cdp = [];
  const out = await OPS.eval({ tabId: TAB_A, js: "1", frame: FRAME_URL });
  assert.equal(out.emulated, true);
  assert.equal(out.emulation.preset, "iphone-15");
  // …and it really did apply the overrides before evaluating in the frame.
  const m = methods();
  assert.deepEqual(m.slice(0, 3), IPHONE_PREFIX);
  assert.ok(m.indexOf("Runtime.evaluate") > m.indexOf("Emulation.setUserAgentOverride"));
});

test("F1: a NON-emulated tab gets NO emulation annotation at all (envelope unchanged)", async () => {
  fresh();
  for (const [op, cmd] of [["text", {}], ["getHtml", {}], ["eval", { js: "1" }]]) {
    const out = await OPS[op]({ tabId: TAB_A, ...cmd });
    assert.equal(out.emulated, undefined,
      `${op} on a plain tab must not grow a field`);
    assert.equal(out.notEmulatedRead, undefined);
    assert.equal(out.emulationNote, undefined);
  }
});

test("F3: a raw --ua derives platform FROM THE UA STRING, never from --mobile", () => {
  // The bug this pins: keying platform off the `mobile` flag alone produced
  // `platform:"Android"` for an iPhone UA — exactly the "combination no real client
  // ever produces" that the preset metadata exists to avoid. An inconsistent pair
  // is a STRONGER bot-detection signal than a blank platform.
  const iphone = normalizeEmulation({
    width: 393, height: 852, mobile: true,
    ua: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148",
  });
  assert.equal(iphone.ua.userAgentMetadata.platform, "iOS");
  assert.equal(iphone.ua.userAgentMetadata.model, "iPhone");
  assert.equal(iphone.ua.userAgentMetadata.mobile, true);

  const android = normalizeEmulation({
    width: 412, height: 915, mobile: true,
    ua: "Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/126.0.0.0 Mobile",
  });
  assert.equal(android.ua.userAgentMetadata.platform, "Android");

  const ipad = normalizeEmulation({
    width: 744, height: 1133, mobile: true,
    ua: "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Mobile/15E148",
  });
  assert.equal(ipad.ua.userAgentMetadata.platform, "iOS");
  assert.equal(ipad.ua.userAgentMetadata.model, "iPad");

  // An unrecognised UA leaves platform EMPTY rather than guessing one.
  const weird = normalizeEmulation({ width: 300, height: 600, mobile: true,
                                     ua: "TotallyCustomAgent/1.0" });
  assert.equal(weird.ua.userAgentMetadata.platform, "");
  assert.equal(weird.ua.userAgentMetadata.model, "");

  // brands stays EMPTY for every raw UA — a wrong brand list is worse than none.
  for (const s of [iphone, android, ipad, weird]) {
    assert.deepEqual(s.ua.userAgentMetadata.brands, []);
  }
});

test("F4: both iPad Minis exist and are distinct (a stable name was not re-pointed)", () => {
  assert.equal(DEVICE_PRESETS["ipad-mini"].width, 744,
    "the unqualified name tracks the CURRENT shipping 6th-gen");
  assert.equal(DEVICE_PRESETS["ipad-mini"].height, 1133);
  assert.equal(DEVICE_PRESETS["ipad-mini-2019"].width, 768,
    "the 2019 model keeps a name of its own rather than being silently replaced");
  assert.equal(DEVICE_PRESETS["ipad-mini-2019"].height, 1024);
  // The integrity + provenance tests above cover both entries automatically.
});

test("onReplaced (prerender/instant swap) drops BOTH tab ids' emulation state", async () => {
  // A swapped-out tabId is retired WITHOUT onRemoved firing, which would strand a
  // Map entry on a recyclable id. The emulation deliberately does NOT follow the
  // tab: the swapped-in document was rendered WITHOUT the overrides, so carrying
  // the state across would claim an emulation that was never applied.
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  await OPS.emulate({ tabId: TAB_B, device: "pixel-8" });
  assert.ok(sw.replacedListeners.length >= 1,
    "the SW must register an onReplaced listener at module scope");
  for (const fn of sw.replacedListeners) fn(TAB_B, TAB_A);   // (added, removed)
  assert.equal(emulationState.has(TAB_A), false, "the retired id is dropped");
  assert.equal(emulationState.has(TAB_B), false,
    "the swapped-IN id is dropped too — its document was never emulated");
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

// --------------------------------------------------------------------------- //
// documentPredatesEmulation — the CREATE-TIME hint (manifest 0.6.0)
//
// The defect it guards (MEASURED live, laptop, extension 0.5.0, example.com —
// see protocol.js DOCUMENT_PREDATES_EMULATION_NOTE): `emulate` on an
// ALREADY-LOADED page leaves `"ontouchstart" in window === false` and
// `typeof TouchEvent === "undefined"`, while metrics/media/UA-CH all apply. A
// `nav` UNDER emulation fixes it. PR #251 wrote that down; this makes the
// ENVELOPE say it, the way the F1 read annotation does.
//
// ⚠ NOTHING BELOW OBSERVES A BROWSER. These tests prove the hint fires and clears
// on the intended state transitions against a MOCKED chrome.debugger. That the
// remedy (`nav` under emulation) actually installs TouchEvent is the LIVE
// measurement above, not something this file can re-prove.
// --------------------------------------------------------------------------- //

test("PURE hasCommittedDocument: an empty/new tab is NOT a committed document", () => {
  for (const u of ["", "about:blank", "about:blank#x", "about:blank?q=1",
                   "about:newtab", "chrome://newtab/", "brave://newtab/"]) {
    assert.equal(hasCommittedDocument(u), false, `${JSON.stringify(u)} must not warn`);
  }
  for (const u of ["https://example.com", "http://127.0.0.1:8788/x",
                   "https://example.com/a#frag"]) {
    assert.equal(hasCommittedDocument(u), true, `${u} IS a committed document`);
  }
  // Defensive: a missing/odd url must not throw and must not warn.
  for (const u of [undefined, null, 42, {}]) assert.equal(hasCommittedDocument(u), false);
});

test("PURE createTimeSignature: 'none' for no/reset state, and it SEPARATES devices", () => {
  assert.equal(emulationCreateTimeSignature(null), "none");
  assert.equal(emulationCreateTimeSignature({ reset: true }), "none");

  const iphone = normalizeEmulation({ device: "iphone-15" });
  const sameAgain = normalizeEmulation({ device: "iphone-15" });
  assert.equal(emulationCreateTimeSignature(iphone),
               emulationCreateTimeSignature(sameAgain),
               "the same device must produce the SAME signature — otherwise a "
               + "re-emulate after nav cries wolf on every call");
  assert.notEqual(emulationCreateTimeSignature(iphone), "none");

  // Touch off is a DIFFERENT create-time world from touch on: that is the whole
  // measured property. Same device, touch:false → different signature.
  const noTouch = normalizeEmulation({ device: "iphone-15", touch: false });
  assert.notEqual(emulationCreateTimeSignature(noTouch),
                  emulationCreateTimeSignature(iphone));
  // …and so is a different touch-point count.
  const fewer = normalizeEmulation({ device: "iphone-15", maxTouchPoints: 2 });
  assert.notEqual(emulationCreateTimeSignature(fewer),
                  emulationCreateTimeSignature(iphone));

  // EVERY component of the signature must be pinned, not just the measured one.
  // NARROWING the signature is the dangerous direction — it produces false
  // SILENCE, the failure mode this whole hint exists to prevent — and a narrowing
  // edit is exactly the kind that ships green if only touch is guarded.
  const raw = { width: 393, height: 852, dsf: 3, touch: true, maxTouchPoints: 5 };
  // (a) `mobile` — same viewport and touch, different mobile flag.
  assert.notEqual(
    emulationCreateTimeSignature(normalizeEmulation({ ...raw, mobile: true })),
    emulationCreateTimeSignature(normalizeEmulation({ ...raw, mobile: false })),
    "dropping `mobile` from the signature would silently accept a document built "
    + "with the opposite mobile flag");
  // (b) the UA-override bit — same everything, one with a raw --ua and one without.
  // (Conservative padding, not a soundness claim: userAgentData was measured to
  // apply LIVE. Pinned anyway so the padding cannot be removed unnoticed.)
  const IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1";
  assert.notEqual(
    emulationCreateTimeSignature(normalizeEmulation({ ...raw, mobile: true })),
    emulationCreateTimeSignature(normalizeEmulation({ ...raw, mobile: true,
                                                      ua: IPHONE_UA })),
    "dropping the UA-override bit would silently accept a document built without "
    + "the UA override");
});

test("PURE documentPredatesEmulation: the full decision matrix", () => {
  const sig = emulationCreateTimeSignature(normalizeEmulation({ device: "iphone-15" }));
  const other = emulationCreateTimeSignature(
    normalizeEmulation({ device: "iphone-15", touch: false }));

  // No committed document → never warn, whatever the record says. This is THE
  // correct workflow (open at about:blank, then emulate) and warning on it would
  // train the caller to ignore the hint.
  assert.equal(documentPredatesEmulation("about:blank", sig, undefined), false);
  assert.equal(documentPredatesEmulation("about:blank", sig, "none"), false);

  // Committed document, no record of us building it under emulation → WARN.
  assert.equal(documentPredatesEmulation("https://example.com", sig, undefined), true);
  assert.equal(documentPredatesEmulation("https://example.com", sig, "none"), true);
  // Built under a DIFFERENT create-time state → still WARN.
  assert.equal(documentPredatesEmulation("https://example.com", sig, other), true);
  // Built under THIS exact create-time state → silent.
  assert.equal(documentPredatesEmulation("https://example.com", sig, sig), false);
});

test("PURE annotateDocumentPredates: same idiom as the read annotation, absent when silent", () => {
  const fires = annotateDocumentPredates({ tabId: 1 }, true);
  assert.equal(fires.documentPredatesEmulation, true);
  assert.equal(fires.emulationNote, DOCUMENT_PREDATES_EMULATION_NOTE);

  const silent = annotateDocumentPredates({ tabId: 1 }, false);
  assert.deepEqual(Object.keys(silent), ["tabId"],
    "a silent hint must not grow the envelope a single field");
  // Non-objects pass through untouched rather than throwing.
  assert.equal(annotateDocumentPredates(null, true), null);
  assert.equal(annotateDocumentPredates("x", true), "x");
});

test("THE NOTE IS LOAD-BEARING: it names the measured properties, the remedy, and its own limits", () => {
  const n = DOCUMENT_PREDATES_EMULATION_NOTE;
  // What is broken — concretely. "something may be wrong" teaches no reflex.
  assert.ok(n.includes("ontouchstart"), "the note must name ontouchstart");
  assert.ok(n.includes("TouchEvent"), "the note must name TouchEvent");
  // What to do about it.
  assert.ok(/browser nav/.test(n), "the note must give the `nav` remedy");
  // The accuracy standard: exactly TWO properties were measured. A future edit
  // that quietly promotes the list to exhaustive fails here.
  assert.ok(/only properties measured|assume there are others/i.test(n),
    "the note must NOT present the create-time list as exhaustive");
  // ONE annotation idiom across the feature: both notes ride `emulationNote`.
  assert.notEqual(n, NOT_EMULATED_READ_NOTE);
});

test("GLUE: emulating a tab that ALREADY has a document fires the hint", async () => {
  fresh();   // TAB_A sits at https://example.com/a — a committed document
  const out = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(out.documentPredatesEmulation, true);
  assert.equal(out.emulationNote, DOCUMENT_PREDATES_EMULATION_NOTE);
  // The hint is ADDITIVE — the op's normal result is untouched.
  assert.equal(out.emulation.preset, "iphone-15");
  assert.deepEqual(out.applied, IPHONE_PREFIX);
  assert.ok(/sticky per tab/.test(out.note));
});

test("GLUE: after a `nav` UNDER emulation, re-emulating the SAME device is SILENT", async () => {
  fresh();
  const first = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(first.documentPredatesEmulation, true, "precondition: it fired once");

  await OPS.nav({ tabId: TAB_A, url: "https://example.com/a" });   // built under emulation

  const again = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(again.documentPredatesEmulation, undefined,
    "the document WAS created under this emulation — warning again is crying wolf");
  assert.equal(again.emulationNote, undefined);
  assert.equal(again.emulation.preset, "iphone-15");
});

test("GLUE: after a nav under emulation, emulating a DIFFERENT create-time state fires again", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  await OPS.nav({ tabId: TAB_A, url: "https://example.com/a" });

  // touch OFF is a different create-time world — the loaded document has touch
  // installed, the requested state says it should not.
  const out = await OPS.emulate({ tabId: TAB_A, device: "iphone-15", touch: false });
  assert.equal(out.documentPredatesEmulation, true);
});

test("GLUE: the WRONG ORDER — nav on a plain tab, THEN emulate — fires the hint", async () => {
  fresh();
  await OPS.nav({ tabId: TAB_A, url: "https://example.com/loaded-first" });
  const out = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(out.documentPredatesEmulation, true,
    "a document built with NO overrides is exactly the measured defect");
});

test("GLUE: a FAILED nav under emulation records no document (the old one is still loaded)", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  sw.failMethod = "Page.navigate";
  await assert.rejects(() => OPS.nav({ tabId: TAB_A, url: "https://example.com/x" }));
  sw.failMethod = null;
  const out = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(out.documentPredatesEmulation, true,
    "the navigation never committed, so the OLD document is still loaded");
});

test("GLUE: --reset carries NO hint, and does not forget what the document was built under", async () => {
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  await OPS.nav({ tabId: TAB_A, url: "https://example.com/a" });

  const reset = await OPS.emulate({ tabId: TAB_A, reset: true });
  assert.equal(reset.documentPredatesEmulation, undefined,
    "--reset applies nothing, so there is nothing to warn about");
  assert.equal(reset.emulationNote, undefined);
  assert.equal(reset.reset, true);

  // The DOCUMENT is unchanged by a reset — it still has touch installed — so
  // re-emulating the same device stays silent. (`clearEmulation` must not have
  // dropped the document record.)
  const again = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(again.documentPredatesEmulation, undefined,
    "--reset must not make a later identical emulate cry wolf");
});

test("GLUE: a tab with NO emulation state is completely unaffected", async () => {
  fresh();
  // The plain nav envelope has not grown a field…
  const nav = await OPS.nav({ tabId: TAB_B, url: "https://example.com/plain" });
  assert.deepEqual(Object.keys(nav).sort(), ["tabId", "url", "via"]);
  assert.equal(nav.via, "tabs.update");
  // …and a plain read is still un-annotated (the F1 guarantee, unchanged).
  const read = await OPS.text({ tabId: TAB_B });
  assert.equal(read.documentPredatesEmulation, undefined);
  assert.equal(read.emulated, undefined);
  assert.equal(read.emulationNote, undefined);
});

test("GLUE: the hint rides ONLY on `emulate` — a read envelope never grows it", async () => {
  fresh();
  const emu = await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  assert.equal(emu.documentPredatesEmulation, true, "precondition");
  const read = await OPS.text({ tabId: TAB_A });
  assert.equal(read.documentPredatesEmulation, undefined,
    "the read path has its OWN annotation (notEmulatedRead); two hints on one "
    + "envelope is two idioms");
  assert.equal(read.notEmulatedRead, true);
});

test("GLUE: a vanished / closed / swapped tab drops the document record too", async () => {
  // A recycled tabId inheriting a stale record would SUPPRESS the hint on a
  // brand-new tab — the failure mode that matters, since silence is the unsafe
  // direction here.
  fresh();
  await OPS.emulate({ tabId: TAB_A, device: "iphone-15" });
  await OPS.nav({ tabId: TAB_A, url: "https://example.com/a" });
  assert.ok(documentEmulation.has(TAB_A), "precondition: the record exists");
  await OPS.close({ tabId: TAB_A });
  assert.equal(documentEmulation.has(TAB_A), false, "`close` drops it");

  fresh();
  await OPS.nav({ tabId: TAB_A, url: "https://example.com/a" });
  assert.ok(documentEmulation.has(TAB_A));
  sw.gone.add(TAB_A);
  await assert.rejects(() => OPS.screenshot({ tabId: TAB_A }), /owned_tab_gone/);
  assert.equal(documentEmulation.has(TAB_A), false, "a vanished tab drops it");

  fresh();
  await OPS.nav({ tabId: TAB_A, url: "https://example.com/a" });
  await OPS.nav({ tabId: TAB_B, url: "https://example.com/b" });
  for (const fn of sw.removedListeners) fn(TAB_A);
  assert.equal(documentEmulation.has(TAB_A), false, "onRemoved drops it");
  for (const fn of sw.replacedListeners) fn(TAB_B, TAB_A);
  assert.equal(documentEmulation.has(TAB_B), false, "onReplaced drops both ids");
});

test("TODAY'S CONTRACT: `emulate` on an about:blank tab is REFUSED before it can warn", async () => {
  // Pinning the CURRENT behaviour, not endorsing it: chrome.debugger may only
  // attach to http/https (CDP_ATTACHABLE_SCHEMES), so the documented
  // `open` → `emulate` → `nav` recipe cannot run as written — `emulate` on the
  // about:blank tab `open` creates is refused. Flagged in the PR; not fixed here.
  //
  // It also means the "a freshly opened tab must not warn" property is exercised
  // at the PURE level above (documentPredatesEmulation("about:blank", …) is
  // false), which is where it will still hold if this refusal is ever relaxed.
  fresh();
  sw.tabs.set(TAB_A, { id: TAB_A, url: "about:blank", title: "", active: true,
                       status: "complete", windowId: 1 });
  await assert.rejects(() => OPS.emulate({ tabId: TAB_A, device: "iphone-15" }),
    /cdp_attach_refused:about:/);
});
