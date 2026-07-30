// Glue tests for the hidden-tab SELF-ANNOUNCE (Gap 2) against a MOCKED chrome —
// proving each read executor reports the tab's visibilityState and, when the tab
// is HIDDEN (a background tab Chromium throttles → a shell-only DOM), flags
// data.hidden=true + a note so a read can't masquerade as a real outage.
//
// The mock distinguishes the top-frame visibilityState PROBE (its injected func
// references document.visibilityState) from the actual read, so a read returns its
// content while the probe returns a controllable visibilityState.
//
// SW auto-start is suppressed (BROWSER_BRIDGE_NO_AUTOSTART) so importing does no I/O.

import test from "node:test";
import assert from "node:assert/strict";
import { HIDDEN_TAB_NOTE } from "../extension/protocol.js";

const TAB_ID = 5;
const TOP_URL = "https://civitai.com/apps/run/model-benchmarking";
const OOPIF_URL = "https://model-benchmarking.civit.ai/";

const state = {
  frames: [
    { frameId: 0, parentFrameId: -1, url: TOP_URL },
    { frameId: 7, parentFrameId: 0, url: OOPIF_URL },
  ],
  tab: { id: TAB_ID, url: TOP_URL, title: "Bench", active: false, status: "complete", windowId: 1 },
  visibility: "visible",          // what the top-frame visibilityState PROBE returns
  readResult: "TOP READ",          // what a top-frame read returns
  frameReadResult: "FRAME READ",   // what a --frame injection returns
};

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  webNavigation: {
    async getAllFrames() { return state.frames; },
  },
  scripting: {
    async executeScript(params) {
      const src = params.func ? String(params.func) : "";
      // The visibilityState probe is the ONLY injected func that reads it.
      if (src.includes("visibilityState")) return [{ result: state.visibility }];
      if (params.target && params.target.frameIds) return [{ result: state.frameReadResult }];
      return [{ result: state.readResult }];
    },
  },
  tabs: {
    async get(id) { return { ...state.tab, id }; },
    async query() { return [state.tab]; },
  },
  debugger: { onDetach: { addListener() {} }, onEvent: { addListener() {}, removeListener() {} } },
  storage: { local: { async get() { return {}; }, async set() {} } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} } },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS } = await import("../extension/service_worker.js");

// --------------------------------------------------------------------------- //
test("visible tab: every read reports visibilityState:'visible' and does NOT flag hidden", async () => {
  state.visibility = "visible";
  for (const call of [
    () => OPS.text({ tabId: TAB_ID }),
    () => OPS.getHtml({ tabId: TAB_ID }),
    () => OPS.eval({ tabId: TAB_ID, js: "1" }),
    () => OPS.frames({ tabId: TAB_ID }),
  ]) {
    const out = await call();
    assert.equal(out.visibilityState, "visible");
    assert.ok(!("hidden" in out), "a visible tab must NOT set hidden");
    assert.ok(!("note" in out), "a visible tab must NOT carry the throttle note");
  }
});

test("hidden tab: text self-announces (hidden:true + the throttle note)", async () => {
  state.visibility = "hidden";
  const out = await OPS.text({ tabId: TAB_ID });
  assert.equal(out.text, "TOP READ", "the read still returns its (shell) content");
  assert.equal(out.visibilityState, "hidden");
  assert.equal(out.hidden, true);
  assert.equal(out.note, HIDDEN_TAB_NOTE);
  assert.match(out.note, /background tabs are throttled/);
  assert.match(out.note, /browser activate/);
});

test("hidden tab: html / eval / frames each self-announce too", async () => {
  state.visibility = "hidden";
  for (const call of [
    () => OPS.getHtml({ tabId: TAB_ID }),
    () => OPS.eval({ tabId: TAB_ID, js: "document.title" }),
    () => OPS.frames({ tabId: TAB_ID }),
  ]) {
    const out = await call();
    assert.equal(out.hidden, true);
    assert.equal(out.note, HIDDEN_TAB_NOTE);
    assert.equal(out.visibilityState, "hidden");
  }
});

test("hidden tab: a --frame read reports the tab's hidden state the same way", async () => {
  state.visibility = "hidden";
  const out = await OPS.text({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai" });
  assert.equal(out.text, "FRAME READ", "the OOPIF read still returns its content");
  assert.equal(out.frame, "model-benchmarking.civit.ai");
  assert.equal(out.hidden, true, "an OOPIF's document follows the tab → hidden reported the same way");
  assert.equal(out.note, HIDDEN_TAB_NOTE);
});

test("visibility probe failure is swallowed: the read still returns (no visibility fields)", async () => {
  state.visibility = null;   // probe returns null (e.g. discarded tab / no permission)
  const out = await OPS.text({ tabId: TAB_ID });
  assert.equal(out.text, "TOP READ");
  assert.ok(!("visibilityState" in out), "a null probe adds nothing — the read never fails");
  assert.ok(!("hidden" in out));
});
