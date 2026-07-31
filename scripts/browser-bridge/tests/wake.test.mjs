// Tests for the `wake` op and the `--wake` reads — the NON-INTRUSIVE un-throttle
// that replaces reflexive `activate` (which steals the operator's screen).
//
// Two halves:
//   1. PURE (protocol.js): the clamp, and the FROZEN CDP step list + its
//      apply-with-optional-tolerance orchestration.
//   2. GLUE (service_worker.js against a MOCKED chrome.debugger): op routing,
//      own-tab scoping, attach→detach ALWAYS (incl. on throw), the pre-attach
//      privileged-scheme refusal, `--wake` reads running in the SAME session, the
//      --wake+--frame refusal, and — the regression guard that matters most — that
//      a DEFAULT text/html read still takes the NON-CDP chrome.scripting path with
//      ZERO debugger attaches. If someone later "simplifies" by routing all reads
//      through CDP, Brave would flash its debug banner on every single read; that
//      test fails first.
//
// SW auto-start is suppressed (BROWSER_BRIDGE_NO_AUTOSTART) so importing does no I/O.

import test from "node:test";
import assert from "node:assert/strict";
import {
  ALLOWED_OPS, validateCommand,
  WAKE_SETTLE_DEFAULT_MS, WAKE_SETTLE_MAX_MS, WAKE_CDP_STEPS,
  clampWakeMs, applyWakeSteps, WAKE_PROBE_EXPRESSION, HIDDEN_TAB_NOTE,
} from "../extension/protocol.js";

// --------------------------------------------------------------------------- //
// PURE
// --------------------------------------------------------------------------- //

test("wake is part of the shared op contract and validates", () => {
  assert.ok(ALLOWED_OPS.includes("wake"), "ALLOWED_OPS must carry `wake`");
  assert.deepEqual(validateCommand({ op: "wake" }), { ok: true });
  // No required fields: waitMs is optional.
  assert.deepEqual(validateCommand({ op: "wake", waitMs: 10 }), { ok: true });
  assert.deepEqual(validateCommand({ op: "wakeup" }), { ok: false, error: "unknown_op" });
});

test("clampWakeMs: default / zero / cap — bounded like clampActivateWaitMs", () => {
  assert.equal(clampWakeMs(undefined), WAKE_SETTLE_DEFAULT_MS);
  assert.equal(clampWakeMs(null), WAKE_SETTLE_DEFAULT_MS);
  assert.equal(clampWakeMs(""), WAKE_SETTLE_DEFAULT_MS);
  assert.equal(clampWakeMs(0), 0);
  assert.equal(clampWakeMs(-1), 0);
  assert.equal(clampWakeMs("nope"), 0);
  assert.equal(clampWakeMs(250), 250);
  assert.equal(clampWakeMs(250.9), 250);
  assert.equal(clampWakeMs(999999), WAKE_SETTLE_MAX_MS, "never exceeds the #189 cap");
  assert.ok(WAKE_SETTLE_MAX_MS <= 8000, "the settle stays well under CDP_OP_BUDGET_MS");
});

test("SECURITY: WAKE_CDP_STEPS is FROZEN data with no caller-influenced params", () => {
  assert.ok(Object.isFrozen(WAKE_CDP_STEPS));
  assert.deepEqual(WAKE_CDP_STEPS.map((s) => s.method),
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"],
    "the ORDER matters: thaw a frozen page first, then the step that un-throttles");
  for (const s of WAKE_CDP_STEPS) {
    assert.ok(Object.isFrozen(s) && Object.isFrozen(s.params));
  }
  // No raw-CDP passthrough: nothing here is a caller-supplied method or param.
  assert.deepEqual(WAKE_CDP_STEPS[0].params, { state: "active" });
  assert.deepEqual(WAKE_CDP_STEPS[1].params, { enabled: true });
  // The probe is metadata-only — it can never return page content.
  assert.match(WAKE_PROBE_EXPRESSION, /visibilityState/);
  assert.ok(!/innerText|outerHTML|documentElement/.test(WAKE_PROBE_EXPRESSION),
    "the wake probe must never read page content");
});

test("applyWakeSteps: runs the steps IN ORDER and reports what applied", async () => {
  const seen = [];
  const out = await applyWakeSteps(async (m, p) => { seen.push([m, p]); });
  assert.deepEqual(seen.map((x) => x[0]),
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"]);
  assert.deepEqual(out.applied,
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"]);
  assert.deepEqual(out.skipped, []);
});

test("applyWakeSteps: an OPTIONAL step's failure is tolerated (measured to be a no-op for a hidden tab)", async () => {
  // Page.setWebLifecycleState changed NOTHING for a merely-hidden tab in the live
  // measurement; it exists only for a FROZEN page. A Chromium that rejects it must
  // not fail the wake — the step that actually un-throttles still runs.
  const out = await applyWakeSteps(async (m) => {
    if (m === "Page.setWebLifecycleState") throw new Error("not supported");
  });
  assert.deepEqual(out.applied, ["Emulation.setFocusEmulationEnabled"]);
  assert.equal(out.skipped.length, 1);
  assert.equal(out.skipped[0].method, "Page.setWebLifecycleState");
});

test("applyWakeSteps: a REQUIRED step's failure PROPAGATES (never claim a wake that didn't happen)", async () => {
  await assert.rejects(
    () => applyWakeSteps(async (m) => {
      if (m === "Emulation.setFocusEmulationEnabled") throw new Error("boom");
    }),
    /boom/);
});

test("the hidden-tab note points at the NON-INTRUSIVE remedy, not `activate`", () => {
  // This string is emitted on EVERY read of a hidden tab, so whatever it names
  // becomes the reflex an agent learns. It used to say "run 'browser activate'",
  // which trained agents to steal the operator's screen 1-5x/minute.
  assert.match(HIDDEN_TAB_NOTE, /browser wake/);
  assert.match(HIDDEN_TAB_NOTE, /--wake/);
  assert.match(HIDDEN_TAB_NOTE, /STEALS/, "it must say plainly that activate takes the screen");
  assert.ok(!/run 'browser activate'/.test(HIDDEN_TAB_NOTE),
    "activate must NOT be the instruction the note gives");
});

// --------------------------------------------------------------------------- //
// GLUE (mocked chrome)
// --------------------------------------------------------------------------- //

const TAB_ID = 5;
const TOP_URL = "https://example.com/app";

const state = {
  tab: { id: TAB_ID, url: TOP_URL, title: "App", active: false, status: "complete", windowId: 1 },
  visibility: "hidden",        // what the CDP wake PROBE reports
  evalValue: "WOKEN-READ",     // what a CDP Runtime.evaluate read returns
  failStep: null,              // a CDP method name to reject
  calls: { cdp: [], attach: [], detach: [], scripting: [] },
};
function reset() {
  state.tab = { id: TAB_ID, url: TOP_URL, title: "App", active: false, status: "complete", windowId: 1 };
  state.visibility = "hidden";
  state.evalValue = "WOKEN-READ";
  state.failStep = null;
  state.calls = { cdp: [], attach: [], detach: [], scripting: [] };
}

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  webNavigation: {
    async getAllFrames() {
      return [{ frameId: 0, parentFrameId: -1, url: TOP_URL }];
    },
  },
  scripting: {
    async executeScript(params) {
      const src = params.func ? String(params.func) : "";
      state.calls.scripting.push(src.includes("visibilityState") ? "probe" : "read");
      if (src.includes("visibilityState")) return [{ result: state.visibility }];
      return [{ result: "PLAIN-READ" }];
    },
  },
  tabs: {
    async get(id) { return { ...state.tab, id }; },
    async query() { return [state.tab]; },
    async update() { throw new Error("wake must NEVER change tab focus"); },
  },
  windows: {
    async update() { throw new Error("wake must NEVER change window focus"); },
  },
  debugger: {
    async attach(target) { state.calls.attach.push(target); },
    async detach(target) { state.calls.detach.push(target); },
    async sendCommand(target, method, params) {
      state.calls.cdp.push({ method, params, sessionId: target.sessionId });
      if (state.failStep === method) throw new Error(`cdp_reject:${method}`);
      if (method === "Runtime.evaluate") {
        const expr = String(params.expression || "");
        if (expr.includes("visibilityState")) {
          return { result: { value: JSON.stringify({
            visibilityState: state.visibility, readyState: "complete", hasFocus: true }) } };
        }
        return { result: { value: state.evalValue } };
      }
      return {};
    },
    onDetach: { addListener() {} },
    onEvent: { addListener() {}, removeListener() {} },
  },
  storage: { local: { async get() { return {}; }, async set() {} } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} } },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS } = await import("../extension/service_worker.js");

const cdpMethods = () => state.calls.cdp.map((c) => c.method);

test("wake: applies the un-throttle to the ROUTED tab and reports it woke", async () => {
  reset();
  state.visibility = "visible";     // what the probe sees INSIDE the woken session
  const out = await OPS.wake({ tabId: TAB_ID, waitMs: 0 });

  assert.deepEqual(state.calls.attach, [{ tabId: TAB_ID }], "own-tab attach only");
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }], "always detaches");
  assert.deepEqual(cdpMethods(),
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled", "Runtime.evaluate"]);
  assert.equal(out.tabId, TAB_ID);
  assert.equal(out.woke, true);
  assert.equal(out.visibilityState, "visible");
  assert.equal(out.readyState, "complete");
  assert.equal(out.settleMs, 0);
  assert.deepEqual(out.applied,
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"]);
  assert.match(out.note, /does not survive|ends at detach/i);
});

test("wake: NEVER touches tab/window focus (the whole point)", async () => {
  reset();
  // chrome.tabs.update / chrome.windows.update THROW in this mock — reaching them
  // at all is the failure. This is the regression guard for focus theft.
  await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.ok(true);
});

test("wake: an honest false verdict when the tab stayed hidden", async () => {
  reset();
  state.visibility = "hidden";   // un-throttle didn't take
  const out = await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.equal(out.woke, false, "never claim a wake the probe didn't confirm");
  assert.equal(out.visibilityState, "hidden");
});

test("wake: a tolerated optional-step failure is REPORTED, not hidden", async () => {
  reset();
  state.failStep = "Page.setWebLifecycleState";
  const out = await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.deepEqual(out.applied, ["Emulation.setFocusEmulationEnabled"]);
  assert.equal(out.skipped.length, 1);
  assert.equal(out.skipped[0].method, "Page.setWebLifecycleState");
});

test("SECURITY: wake REFUSES a privileged scheme BEFORE attaching", async () => {
  reset();
  state.tab = { ...state.tab, url: "chrome://settings" };
  await assert.rejects(() => OPS.wake({ tabId: TAB_ID, waitMs: 0 }),
    /cdp_attach_refused:chrome/);
  assert.deepEqual(state.calls.attach, [], "no attach on a privileged surface");
  assert.deepEqual(state.calls.detach, [], "nothing to detach");
});

test("SECURITY: wake ALWAYS detaches, even when a required CDP step throws", async () => {
  reset();
  state.failStep = "Emulation.setFocusEmulationEnabled";
  await assert.rejects(() => OPS.wake({ tabId: TAB_ID, waitMs: 0 }), /cdp_reject/);
  assert.deepEqual(state.calls.attach, [{ tabId: TAB_ID }]);
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }], "detach happens in the finally");
});

// --- the load-bearing regression guard ------------------------------------- //

test("REGRESSION: a DEFAULT text/html/eval read takes the NON-CDP path — ZERO debugger attaches", async () => {
  reset();
  state.visibility = "hidden";
  const t = await OPS.text({ tabId: TAB_ID });
  const h = await OPS.getHtml({ tabId: TAB_ID });
  const e = await OPS.eval({ tabId: TAB_ID, js: "1" });

  assert.deepEqual(state.calls.attach, [],
    "routing ordinary reads through CDP would flash Brave's debug banner on EVERY read");
  assert.deepEqual(state.calls.cdp, [], "no CDP command on the default read path");
  assert.ok(state.calls.scripting.length >= 6, "the default path is chrome.scripting");
  // ...and the reads still self-announce the hidden tab, pointing at `wake`.
  for (const out of [t, h, e]) {
    assert.equal(out.hidden, true);
    assert.match(out.note, /browser wake/);
    assert.ok(!("woke" in out), "a non-woken read must not claim it woke anything");
  }
});

test("text --wake: un-throttles and reads INSIDE THE SAME CDP session", async () => {
  reset();
  state.visibility = "visible";
  const out = await OPS.text({ tabId: TAB_ID, wake: true, waitMs: 0 });

  assert.deepEqual(state.calls.attach, [{ tabId: TAB_ID }]);
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }]);
  // The un-throttle steps come BEFORE the read, in ONE attached session — the whole
  // reason --wake exists (the un-throttled state does NOT survive detach).
  const order = cdpMethods();
  assert.equal(order[0], "Page.setWebLifecycleState");
  assert.equal(order[1], "Emulation.setFocusEmulationEnabled");
  assert.ok(order.slice(2).every((m) => m === "Runtime.evaluate"));
  assert.equal(state.calls.attach.length, 1, "ONE attach covers both the wake and the read");

  assert.equal(out.text, "WOKEN-READ");
  assert.equal(out.woke, true);
  assert.equal(out.visibilityState, "visible");
  assert.ok(!("hidden" in out), "a woken read is not a hidden read");
  assert.deepEqual(out.wake.applied,
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"]);
});

test("html --wake / eval --wake take the same single-session path", async () => {
  reset();
  state.visibility = "visible";

  const h = await OPS.getHtml({ tabId: TAB_ID, wake: true, waitMs: 0 });
  assert.equal(h.html, "WOKEN-READ");
  assert.equal(h.woke, true);
  assert.equal(state.calls.attach.length, 1);

  reset();
  state.visibility = "visible";
  const e = await OPS.eval({ tabId: TAB_ID, js: "1+1", wake: true, waitMs: 0 });
  assert.equal(e.value, "WOKEN-READ");
  assert.equal(e.woke, true);
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }]);
});

test("a --wake read still reports honestly when the tab stayed hidden", async () => {
  reset();
  state.visibility = "hidden";
  const out = await OPS.text({ tabId: TAB_ID, wake: true, waitMs: 0 });
  assert.equal(out.woke, false);
  assert.equal(out.hidden, true, "still self-announces — the read IS a shell");
  assert.match(out.note, /browser wake/);
});

test("--wake + --frame is REFUSED loudly (never silently ignore one of the two)", async () => {
  for (const call of [
    () => OPS.text({ tabId: TAB_ID, wake: true, frame: "7" }),
    () => OPS.getHtml({ tabId: TAB_ID, wake: true, frame: "7" }),
    () => OPS.eval({ tabId: TAB_ID, wake: true, frame: "7", js: "1" }),
  ]) {
    reset();
    await assert.rejects(call, /wake_with_frame_unsupported/);
  }
});

test("SECURITY: a --wake read attaches ONLY to the routed tab, never the active tab", async () => {
  reset();
  state.visibility = "visible";
  await OPS.text({ tabId: 99, wake: true, waitMs: 0 });
  assert.deepEqual(state.calls.attach, [{ tabId: 99 }],
    "the server-injected tab is authoritative — same own-tab scope as every CDP op");
});
