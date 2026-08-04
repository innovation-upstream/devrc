// emulation_reset.test.mjs — `emulate --reset` must UNDO the emulated viewport.
//
// Issue #319: device-metrics emulation was permanently sticky per tab. It survived
// the debugger detach, it survived `--reset`, and it survived a re-navigation. The
// only remedy was closing the tab.
//
// 🔴 WHY THIS FILE EXISTS AND WHY IT DOES NOT ASSERT `ok:true`.
// The broken behaviour ALREADY returned `{ok:true, reset:true, wasEmulating:{…}}`.
// A test that checks the envelope passes on the bug. So does a test that checks
// "clearDeviceMetricsOverride was sent" — PR #320 sent exactly that, it was
// deployed to a real profile, the clear was acknowledged, and the viewport did not
// come back. The ONLY assertion that discriminates is the VIEWPORT AFTER RESET.
//
// So this file models the browser's measured behaviour and asserts the width.
//
// --- THE MODEL, AND WHAT CALIBRATES IT --------------------------------------- //
//
// Measured 2026-08-04 against a throwaway Brave 147.0.7727.56 under Xvfb, driven
// over the raw DevTools websocket (no extension in the loop), with a never-emulated
// control tab read every round:
//
//   session A: setDeviceMetricsOverride{393x852,dsf3,mobile} → detach   innerWidth 394
//              (control tab, same window, untouched)                    innerWidth 1055
//   session B: clearDeviceMetricsOverride → detach                      innerWidth 394
//   session B: setDeviceMetricsOverride{…} then clear → detach          innerWidth 1055
//
// i.e. THREE rules, and rule 2 is the whole bug:
//   1. a device-metrics override resizes the browser-side widget, and that resize
//      SURVIVES the detach (devicePixelRatio, touch points, pointer:coarse, UA and
//      timezone do not — they revert on their own, measured);
//   2. clearDeviceMetricsOverride is a NO-OP unless the CALLING session has itself
//      set an override — a fresh session's clear silently does nothing;
//   3. any setDeviceMetricsOverride arms the session, and {width:0,height:0} arms
//      it WITHOUT resizing anything (measured: still 394 in-session after arming),
//      so the arm-then-clear pair restores with no intermediate flash.
//
// The model below implements exactly those three rules and nothing else. It is
// validated in BOTH directions before any product assertion is read (see the two
// HARNESS tests): it must be able to report a STUCK viewport, and it must be able
// to report a RESTORED one. A model that could only ever say "restored" would make
// every test here vacuous.

import test from "node:test";
import assert from "node:assert/strict";

const TAB_ID = 11;
const TAB_URL = "https://example.com/";
const REAL_WIDTH = 1055;      // the never-emulated control's innerWidth
const PHONE_WIDTH = 393;

// --- the browser model -------------------------------------------------------- //
const browser = {
  widgetWidth: REAL_WIDTH,    // what innerWidth reports; survives detach (rule 1)
  session: null,              // { armed } while the debugger is attached
  commands: [],               // [{ session, method, params }] for ordering assertions
  sessions: 0,
  attachThrows: null,
};

function resetBrowser() {
  browser.widgetWidth = REAL_WIDTH;
  browser.session = null;
  browser.commands = [];
  browser.sessions = 0;
  browser.attachThrows = null;
}

function cdp(method, params) {
  browser.commands.push({ session: browser.sessions, method, params });
  if (method === "Emulation.setDeviceMetricsOverride") {
    browser.session.armed = true;                       // rule 3
    if (params && params.width > 0) browser.widgetWidth = params.width;
  } else if (method === "Emulation.clearDeviceMetricsOverride") {
    if (browser.session.armed) {                        // rule 2
      browser.session.armed = false;
      browser.widgetWidth = REAL_WIDTH;
    }
  }
  if (method === "Page.captureScreenshot") return { data: "QkJCQg==" };
  return {};
}

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  webNavigation: { async getAllFrames() { return [{ frameId: 0, parentFrameId: -1, url: TAB_URL }]; } },
  scripting: { async executeScript() { return [{ result: { ok: true }, frameId: 0 }]; } },
  tabs: {
    async get(id) { return { id, url: TAB_URL, title: "t", active: false, status: "complete", windowId: 1 }; },
    async query() { return [{ id: TAB_ID, url: TAB_URL, active: false, status: "complete", windowId: 1 }]; },
    async update(id) { return { id, url: TAB_URL }; },
    async remove() {},
  },
  windows: { async update() {} },
  debugger: {
    async attach() {
      if (browser.attachThrows) throw new Error(browser.attachThrows);
      browser.sessions += 1;
      browser.session = { armed: false };
    },
    async detach() { browser.session = null; },          // rule 1: width unchanged
    async sendCommand(_t, method, params) { return cdp(method, params); },
    onDetach: { addListener() {} },
    onEvent: { addListener() {}, removeListener() {} },
  },
  storage: { local: { async get() { return {}; }, async set() {} } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} },
             getManifest: () => ({ version: "0.0.0" }), id: "test" },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS } = await import("../extension/service_worker.js");
const { EMULATION_RESET_CDP_STEPS } = await import("../extension/protocol.js");

// Drive the model directly, the way a raw CDP client would.
async function rawSession(fn) {
  await globalThis.chrome.debugger.attach();
  try { await fn((m, p) => cdp(m, p)); } finally { await globalThis.chrome.debugger.detach(); }
}
const PHONE_METRICS = { width: PHONE_WIDTH, height: 852, deviceScaleFactor: 3, mobile: true };

// --------------------------------------------------------------------------- //
// HARNESS VALIDATION — both directions, before any product assertion is read.
// --------------------------------------------------------------------------- //

test("HARNESS: the model reproduces #319 — a fresh session's bare clear is a NO-OP", async () => {
  resetBrowser();
  await rawSession(async (send) => send("Emulation.setDeviceMetricsOverride", PHONE_METRICS));
  assert.equal(browser.widgetWidth, PHONE_WIDTH,
    "rule 1: the widget resize must SURVIVE the detach, or this file cannot see the bug");
  await rawSession(async (send) => send("Emulation.clearDeviceMetricsOverride"));
  assert.equal(browser.widgetWidth, PHONE_WIDTH,
    "rule 2 (measured): clearDeviceMetricsOverride from an UNARMED session does " +
    "nothing — this is the negative control; if it restores, every assertion " +
    "below is vacuous and the model is wrong");
});

test("HARNESS: the model CAN restore — arm-then-clear in one session works", async () => {
  resetBrowser();
  await rawSession(async (send) => send("Emulation.setDeviceMetricsOverride", PHONE_METRICS));
  assert.equal(browser.widgetWidth, PHONE_WIDTH);
  await rawSession(async (send) => {
    await send("Emulation.setDeviceMetricsOverride", { width: 0, height: 0, deviceScaleFactor: 0, mobile: false });
    assert.equal(browser.widgetWidth, PHONE_WIDTH,
      "rule 3 (measured): arming with {0,0} must NOT itself resize — no flash");
    await send("Emulation.clearDeviceMetricsOverride");
  });
  assert.equal(browser.widgetWidth, REAL_WIDTH,
    "positive control: the model must be able to report a RESTORED viewport too");
});

// --------------------------------------------------------------------------- //
// THE REGRESSION TEST (#319).
// --------------------------------------------------------------------------- //

test("emulate --reset RESTORES the viewport (#319) — the width, not the envelope", async () => {
  resetBrowser();
  await OPS.emulate({ tabId: TAB_ID, width: PHONE_WIDTH, height: 852, dsf: 3, mobile: true });
  assert.equal(browser.widgetWidth, PHONE_WIDTH,
    "precondition: the emulate must actually have moved the viewport");

  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });

  // 🔴 THE assertion. `ok:true`/`reset:true`/`wasEmulating` were ALL true while
  // the bug shipped; only this line discriminates.
  assert.equal(browser.widgetWidth, REAL_WIDTH,
    "emulate --reset must leave the tab at its REAL viewport width — this is #319 " +
    "and it is the only assertion the broken behaviour fails");
  assert.equal(out.restored, true);
  assert.ok(out.wasEmulating, "the reset still reports the state it dropped");
});

test("emulate --reset survives a re-navigation in between (#319 step 6)", async () => {
  resetBrowser();
  await OPS.emulate({ tabId: TAB_ID, width: PHONE_WIDTH, height: 852, dsf: 3, mobile: true });
  // A navigation does not restore the width — that was measured, and it is why
  // "just re-nav" was ruled out as a remedy. Model it by leaving the width alone.
  assert.equal(browser.widgetWidth, PHONE_WIDTH);
  await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.equal(browser.widgetWidth, REAL_WIDTH);
});

test("emulate --reset: sends the ARM-then-CLEAR pair, in that order", async () => {
  resetBrowser();
  await OPS.emulate({ tabId: TAB_ID, width: PHONE_WIDTH, height: 852, dsf: 3, mobile: true });
  const before = browser.commands.length;
  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });

  assert.deepEqual(out.cleared,
    ["Emulation.setDeviceMetricsOverride", "Emulation.clearDeviceMetricsOverride"],
    "the reported step list IS the fix: the arming override first, then the clear");
  assert.deepEqual(EMULATION_RESET_CDP_STEPS.map((s) => s.method), out.cleared,
    "the op must report exactly the pure step list, not a hand-rolled copy");

  const sent = browser.commands.slice(before);
  assert.deepEqual(sent.map((c) => c.method), out.cleared,
    "and exactly those two commands reached the browser — nothing else");
  assert.equal(new Set(sent.map((c) => c.session)).size, 1,
    "both steps must ride ONE session: the clear is a no-op in a session that did " +
    "not arm itself, so splitting them reintroduces #319");
  assert.deepEqual(sent[0].params, { width: 0, height: 0, deviceScaleFactor: 0, mobile: false },
    "the arming override must be the neutral one — it must not resize the tab to " +
    "some other wrong size on the way out");
});

test("emulate --reset does NOT re-apply the emulation on its way in", async () => {
  resetBrowser();
  await OPS.emulate({ tabId: TAB_ID, width: PHONE_WIDTH, height: 852, dsf: 3, mobile: true });
  const before = browser.commands.length;
  await OPS.emulate({ tabId: TAB_ID, reset: true });
  const sent = browser.commands.slice(before);
  assert.equal(sent.some((c) => c.method === "Emulation.setDeviceMetricsOverride"
                             && c.params && c.params.width === PHONE_WIDTH), false,
    "the state must be dropped BEFORE the attach, or withCdp's re-application " +
    "choke point re-installs the phone metrics inside the reset's own session");
  assert.equal(sent.some((c) => c.method === "Emulation.setTouchEmulationEnabled"), false,
    "likewise for touch — reset re-applies nothing");
});

test("emulate --reset is BEST-EFFORT: a refused attach reports, never throws", async () => {
  resetBrowser();
  await OPS.emulate({ tabId: TAB_ID, width: PHONE_WIDTH, height: 852, dsf: 3, mobile: true });
  browser.attachThrows = "Cannot access a chrome:// URL";

  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.equal(out.reset, true, "the reset itself still succeeds");
  assert.equal(out.restored, false);
  assert.deepEqual(out.cleared, [], "nothing was acknowledged, so nothing is claimed");
  assert.match(out.restoreError, /chrome:\/\//);
  assert.match(out.note, /--reset --recreate/,
    "the failure note must name the remedy that needs no CDP");

  // …and the in-memory state is gone regardless, so nothing gets re-applied later.
  browser.attachThrows = null;
  const again = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.equal(again.wasEmulating, null);
});

test("emulate --reset on a never-emulated tab is a safe no-op on the viewport", async () => {
  resetBrowser();
  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.equal(browser.widgetWidth, REAL_WIDTH,
    "arm-then-clear on a tab that was never emulated must not move it (measured: " +
    "2/2 runs left a never-emulated tab at its true width)");
  assert.equal(out.wasEmulating, null);
  assert.equal(out.restored, true);
});
