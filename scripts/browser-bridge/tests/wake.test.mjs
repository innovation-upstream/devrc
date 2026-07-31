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
  WAKE_SETTLE_DEFAULT_MS, WAKE_SETTLE_MAX_MS, WAKE_CDP_STEPS, WAKE_CDP_TEARDOWN,
  clampWakeMs, applyWakeSteps, wakeProbeFn, HIDDEN_TAB_NOTE,
  CDP_OP_BUDGET_MS, CDP_COMMAND_TIMEOUT_MS,
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
});

test("WAKE_SETTLE_MAX_MS is DERIVED from the CDP budgets — settle + one command fits", () => {
  // The settle is only ONE PHASE of a CDP op that must also fit the probe and (for a
  // --wake read) the read, all inside CDP_OP_BUDGET_MS. An 8s cap let
  // `html --wake=8000` reach 8s settle + an 8s-bounded read = 16s > the 15s budget,
  // surfacing as an opaque `cdp_timeout:op`. Pin the relationship so changing a
  // budget without re-deriving the cap fails here rather than in production.
  assert.ok(WAKE_SETTLE_MAX_MS + CDP_COMMAND_TIMEOUT_MS < CDP_OP_BUDGET_MS,
    `settle cap ${WAKE_SETTLE_MAX_MS} + command ${CDP_COMMAND_TIMEOUT_MS} `
    + `must fit the ${CDP_OP_BUDGET_MS} op budget`);
  assert.ok(WAKE_SETTLE_DEFAULT_MS < WAKE_SETTLE_MAX_MS);
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
});

test("SECURITY: the teardown explicitly DISABLES focus emulation (never left to detach)", () => {
  // Relying on detach to revert focus emulation is relying on an Emulation-domain
  // implementation detail — and a hung/failed detach is bounded and SWALLOWED by
  // withCdpSession's safeDetach, so the attachment (and the emulated focus) can
  // outlive the op. It must be turned off explicitly.
  assert.equal(WAKE_CDP_TEARDOWN.method, "Emulation.setFocusEmulationEnabled");
  assert.deepEqual(WAKE_CDP_TEARDOWN.params, { enabled: false });
  assert.ok(Object.isFrozen(WAKE_CDP_TEARDOWN) && Object.isFrozen(WAKE_CDP_TEARDOWN.params));
  // It must undo exactly what the steps turned on.
  const on = WAKE_CDP_STEPS.find((x) => x.method === WAKE_CDP_TEARDOWN.method);
  assert.ok(on && on.params.enabled === true);
});

test("SECURITY: the wake probe is an isolated-world FUNCTION, not a main-world expression", () => {
  // A CDP Runtime.evaluate probe runs in the page's MAIN world, where a hostile page
  // can shadow document.visibilityState / document.hasFocus and make `woke` a
  // page-controlled claim. chrome.scripting's isolated world sees the real values.
  assert.equal(typeof wakeProbeFn, "function");
  const src = String(wakeProbeFn);
  assert.match(src, /visibilityState/);
  assert.ok(!/innerText|outerHTML|documentElement|querySelector/.test(src),
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

// The mock keeps the ISOLATED-world results (what chrome.scripting returns) and the
// MAIN-world results (what a CDP Runtime.evaluate would return) DELIBERATELY
// DIFFERENT, so a test can tell which world a read actually came from. The
// main-world values simulate a hostile page that has installed an `outerHTML`
// getter / shadowed `innerText` — content it authored that is not in the DOM.
const ISOLATED_HTML = "<html>REAL-DOM</html>";
const ISOLATED_TEXT = "REAL-TEXT";
const MAIN_WORLD_POISON = "ATTACKER-SHADOWED-CONTENT";

const state = {
  tab: { id: TAB_ID, url: TOP_URL, title: "App", active: false, status: "complete", windowId: 1 },
  visibility: "hidden",        // what the ISOLATED-world wake probe reports
  failStep: null,              // a CDP method name to reject
  failDetach: false,           // simulate a hung/failing chrome.debugger.detach
  throwInRead: false,          // make the --wake read throw (teardown must still run)
  calls: { cdp: [], attach: [], detach: [], scripting: [] },
};
function reset() {
  state.tab = { id: TAB_ID, url: TOP_URL, title: "App", active: false, status: "complete", windowId: 1 };
  state.visibility = "hidden";
  state.failStep = null;
  state.failDetach = false;
  state.throwInRead = false;
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
      // The WAKE probe (wakeProbeFn) returns an object and mentions readyState; the
      // ordinary read-path visibility probe returns the bare string. Both are
      // chrome.scripting, so distinguish them by shape, not by mechanism.
      if (src.includes("readyState")) {
        state.calls.scripting.push("probe");
        return [{ result: { visibilityState: state.visibility,
                            readyState: "complete", hasFocus: true } }];
      }
      if (src.includes("visibilityState")) {
        state.calls.scripting.push("vis");
        return [{ result: state.visibility }];
      }
      if (src.includes("outerHTML")) {
        state.calls.scripting.push("html");
        if (state.throwInRead) throw new Error("read_boom");
        return [{ result: ISOLATED_HTML }];
      }
      state.calls.scripting.push("text");
      if (state.throwInRead) throw new Error("read_boom");
      return [{ result: ISOLATED_TEXT }];
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
    async detach(target) {
      state.calls.detach.push(target);
      if (state.failDetach) throw new Error("detach_failed");
    },
    async sendCommand(target, method, params) {
      state.calls.cdp.push({ method, params, sessionId: target.sessionId });
      if (state.failStep === method) throw new Error(`cdp_reject:${method}`);
      if (method === "Runtime.evaluate") {
        // Anything reaching CDP evaluate is running in the page's MAIN world.
        return { result: { value: MAIN_WORLD_POISON } };
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

const { OPS, cdpAttached } = await import("../extension/service_worker.js");

const cdpMethods = () => state.calls.cdp.map((c) => c.method);
const focusEmuCalls = () => state.calls.cdp
  .filter((c) => c.method === "Emulation.setFocusEmulationEnabled")
  .map((c) => c.params && c.params.enabled);

test("wake: applies the un-throttle to the ROUTED tab and reports it woke", async () => {
  reset();
  state.visibility = "visible";     // what the ISOLATED probe sees during the wake
  const out = await OPS.wake({ tabId: TAB_ID, waitMs: 0 });

  assert.deepEqual(state.calls.attach, [{ tabId: TAB_ID }], "own-tab attach only");
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }], "always detaches");
  assert.deepEqual(cdpMethods(), [
    "Page.setWebLifecycleState",
    "Emulation.setFocusEmulationEnabled",   // on
    "Emulation.setFocusEmulationEnabled",   // off (explicit teardown)
  ]);
  assert.deepEqual(state.calls.scripting, ["probe"], "the probe is isolated-world");
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

// --- Fix A: explicit un-emulation + the tracking-set ordering --------------- //

test("SECURITY: focus emulation is explicitly DISABLED before detach", async () => {
  reset();
  state.visibility = "visible";
  await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.deepEqual(focusEmuCalls(), [true, false],
    "the emulated-focus window must be closed explicitly, not left to detach");
  // ...and it must happen BEFORE the detach, not after (or alongside) it.
  const offIdx = state.calls.cdp.findIndex(
    (c) => c.method === "Emulation.setFocusEmulationEnabled" && c.params.enabled === false);
  assert.ok(offIdx >= 0);
  assert.equal(state.calls.detach.length, 1);
});

test("SECURITY: focus emulation is disabled even when the --wake READ throws", async () => {
  reset();
  state.visibility = "visible";
  state.throwInRead = true;
  await assert.rejects(() => OPS.text({ tabId: TAB_ID, wake: true, waitMs: 0 }),
    /read_boom/);
  assert.deepEqual(focusEmuCalls(), [true, false],
    "a thrown read must not leave the tab focus-emulated");
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }]);
});

test("SECURITY: a teardown FAILURE never masks the op's real result", async () => {
  reset();
  state.visibility = "visible";
  // Both the on- and off- calls use the same method name, so failing it makes the
  // REQUIRED wake step throw first — the op must surface THAT, not a teardown error.
  state.failStep = "Emulation.setFocusEmulationEnabled";
  await assert.rejects(() => OPS.wake({ tabId: TAB_ID, waitMs: 0 }),
    /cdp_reject:Emulation.setFocusEmulationEnabled/);
});

test("REGRESSION: a failed/hung detach leaves the tab TRACKED in cdpAttached", async () => {
  reset();
  state.visibility = "visible";
  state.failDetach = true;
  // The op still succeeds — withCdpSession bounds and swallows a detach failure.
  const out = await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.equal(out.woke, true);
  assert.ok(cdpAttached.has(TAB_ID),
    "deleting from the tracking set BEFORE awaiting detach made a real leaked "
    + "attachment invisible; a failed detach must leave the tab tracked");
  cdpAttached.delete(TAB_ID);   // don't leak mock state into later tests
});

test("a SUCCESSFUL detach still clears the tracking set", async () => {
  reset();
  state.visibility = "visible";
  await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.ok(!cdpAttached.has(TAB_ID), "the normal path must not accumulate entries");
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
  assert.equal(state.calls.attach.length, 1, "ONE attach covers both the wake and the read");
  // The un-throttle comes BEFORE the read and is torn down after — the whole reason
  // --wake exists (the un-throttled state does NOT survive detach).
  assert.deepEqual(cdpMethods(), [
    "Page.setWebLifecycleState",
    "Emulation.setFocusEmulationEnabled",
    "Emulation.setFocusEmulationEnabled",
  ]);
  assert.deepEqual(state.calls.scripting, ["probe", "text"],
    "probe and read both go through chrome.scripting, inside the attached session");

  assert.equal(out.text, ISOLATED_TEXT);
  assert.equal(out.woke, true);
  assert.equal(out.visibilityState, "visible");
  assert.ok(!("hidden" in out), "a woken read is not a hidden read");
  assert.deepEqual(out.wake.applied,
    ["Page.setWebLifecycleState", "Emulation.setFocusEmulationEnabled"]);
});

// --- Fix B: --wake reads must come from the ISOLATED world ------------------ //

test("SECURITY: text/html --wake read the ISOLATED world — a main-world shadow is NOT observed", async () => {
  // A hostile page can install an `outerHTML` getter or shadow innerText/querySelector
  // and hand the reader content that is NOT in the DOM — a prompt-injection payload
  // delivered on exactly the path agents are told to use when a read "came back
  // empty". The mock returns MAIN_WORLD_POISON from every CDP Runtime.evaluate, so
  // seeing it here would mean the read ran in the page's main world.
  reset();
  state.visibility = "visible";
  const t = await OPS.text({ tabId: TAB_ID, wake: true, waitMs: 0 });
  assert.equal(t.text, ISOLATED_TEXT);
  assert.notEqual(t.text, MAIN_WORLD_POISON);

  reset();
  state.visibility = "visible";
  const h = await OPS.getHtml({ tabId: TAB_ID, wake: true, waitMs: 0 });
  assert.equal(h.html, ISOLATED_HTML);
  assert.notEqual(h.html, MAIN_WORLD_POISON);

  // No Runtime.evaluate at all on the html/text --wake path: the reads and the probe
  // are chrome.scripting, only the un-throttle is CDP.
  assert.ok(!cdpMethods().includes("Runtime.evaluate"),
    "a --wake html/text read must never evaluate in the page's main world");
});

test("SECURITY: the `woke` verdict is probed from the ISOLATED world, not main-world CDP", async () => {
  reset();
  state.visibility = "visible";
  const out = await OPS.wake({ tabId: TAB_ID, waitMs: 0 });
  assert.equal(out.woke, true);
  assert.ok(state.calls.scripting.includes("probe"));
  assert.ok(!cdpMethods().includes("Runtime.evaluate"),
    "a main-world probe could be shadowed, making `woke` a page-controlled claim");
});

test("eval --wake DOES use CDP main world — matching plain `eval` (world:MAIN) by design", async () => {
  // `eval` means "run my JS with the page's own globals", and the default eval path
  // is explicitly world:"MAIN". So --wake adds no exposure eval didn't already have.
  // It also CANNOT use chrome.scripting: that runs a serialized FUNC, never a
  // caller's JS STRING (#190). Pin the intent so the asymmetry with text/html is
  // deliberate and visible.
  reset();
  state.visibility = "visible";
  const e = await OPS.eval({ tabId: TAB_ID, js: "1+1", wake: true, waitMs: 0 });
  assert.equal(e.value, MAIN_WORLD_POISON, "eval --wake evaluates in the main world");
  assert.equal(e.woke, true);
  assert.ok(cdpMethods().includes("Runtime.evaluate"));
  assert.deepEqual(focusEmuCalls(), [true, false], "still torn down explicitly");
  assert.deepEqual(state.calls.detach, [{ tabId: TAB_ID }]);
});

test("html --wake takes the same single-session path", async () => {
  reset();
  state.visibility = "visible";
  const h = await OPS.getHtml({ tabId: TAB_ID, wake: true, waitMs: 0 });
  assert.equal(h.html, ISOLATED_HTML);
  assert.equal(h.woke, true);
  assert.equal(state.calls.attach.length, 1);
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

// --- Fix C: --frame is refused on every wake surface ------------------------ //

test("--wake + --frame is REFUSED loudly (never silently ignore one of the two)", async () => {
  for (const call of [
    () => OPS.text({ tabId: TAB_ID, wake: true, frame: "7" }),
    () => OPS.getHtml({ tabId: TAB_ID, wake: true, frame: "7" }),
    () => OPS.eval({ tabId: TAB_ID, wake: true, frame: "7", js: "1" }),
  ]) {
    reset();
    await assert.rejects(call, /wake_with_frame_unsupported/);
    assert.deepEqual(state.calls.attach, [], "refused before any attach");
  }
});

test("the `wake` OP with a --frame is refused too (global flag, no per-frame wake)", async () => {
  // `--frame` is a GLOBAL CLI flag parsed before the subcommand, so
  // `browser --frame X wake` puts a `frame` on the wire. Silently waking the whole
  // tab while the caller believes they scoped it is the same quiet mismatch the
  // --wake+--frame refusal exists to prevent.
  reset();
  await assert.rejects(() => OPS.wake({ tabId: TAB_ID, frame: "7", waitMs: 0 }),
    /wake_with_frame_unsupported/);
  assert.deepEqual(state.calls.attach, [], "refused before any attach");
  assert.match(
    await OPS.wake({ tabId: TAB_ID, frame: "7" }).catch((e) => e.message),
    /tab-level, not per-frame/,
    "the message must explain WHY, not just refuse");
});

test("SECURITY: a --wake read attaches ONLY to the routed tab, never the active tab", async () => {
  reset();
  state.visibility = "visible";
  await OPS.text({ tabId: 99, wake: true, waitMs: 0 });
  assert.deepEqual(state.calls.attach, [{ tabId: 99 }],
    "the server-injected tab is authoritative — same own-tab scope as every CDP op");
});
