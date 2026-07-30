// Glue tests for `eval --frame` against a MOCKED chrome — proving the #190 fix is wired
// correctly WITHOUT a real Brave. The bug: `eval --frame` was routed through
// chrome.scripting.executeScript, which runs a serialized FUNC, not an arbitrary JS
// STRING → it evaluated nothing meaningful and returned value:null-as-success. The fix
// routes `eval --frame` through CDP Runtime.evaluate in the target frame's execution
// context (same-process isolated world OR cross-origin OOPIF flat session).
//
// These assert, against a mock chrome.debugger/webNavigation:
//   * the CDP path is TAKEN (chrome.debugger.attach), NOT chrome.scripting;
//   * SAME-PROCESS frame: Page.getFrameTree → Page.createIsolatedWorld →
//     Runtime.evaluate(contextId) returns the eval VALUE (NOT null — the regression);
//   * cross-origin OOPIF: Target.setAutoAttach flat → the target session matched by url
//     → Runtime.evaluate(sessionId) returns the value (OOPIF context resolution path);
//   * NEVER-SILENT-NULL: an exceptionDetails → frame_eval_failed; an unresolvable OOPIF
//     → frame_not_found (never value:null);
//   * a SyntaxError on the expression form retries the statement form (once);
//   * #189 timeouts still bound the CDP eval path: a hung Runtime.evaluate settles with
//     cdp_timeout and STILL detaches — no SW wedge;
//   * security/telemetry: own-tab attach, typed op only, no eval source echoed.
//
// SW auto-start is suppressed (BROWSER_BRIDGE_NO_AUTOSTART) so importing does no I/O.

import test from "node:test";
import assert from "node:assert/strict";

const TAB_ID = 5;
const TOP_URL = "https://civitai.com/apps/run/model-benchmarking";
const OOPIF_URL = "https://model-benchmarking.civit.ai/";

// A same-process frame tree from the TOP session's Page.getFrameTree: the top frame +
// a same-process child. The cross-origin OOPIF is DELIBERATELY ABSENT (getFrameTree
// from the top target omits OOPIFs — the reason the auto-attach path exists).
const FRAME_TREE = {
  frame: { id: "CDP_MAIN", url: TOP_URL, name: "" },
  childFrames: [{ frame: { id: "CDP_SAME", url: "https://civitai.com/embed/w", name: "w" } }],
};

const state = {
  frames: [
    { frameId: 0, parentFrameId: -1, url: TOP_URL },
    { frameId: 7, parentFrameId: 0, url: OOPIF_URL },
  ],
  tab: { id: TAB_ID, url: TOP_URL, title: "Bench", active: false, status: "complete", windowId: 1 },
  // The reply(ies) the mock returns for Runtime.evaluate, in order (falls back to the
  // single `evalReply` when the queue is empty).
  evalReplies: [],
  evalReply: { result: { value: null } },
  // Auto-attach targets the mock announces when Target.setAutoAttach is called.
  autoAttachTargets: [{ sessionId: "SID_OOPIF", url: OOPIF_URL }],
  hangEvaluate: false,   // make Runtime.evaluate never resolve (timeout test)
  calls: { cdp: [], executeScript: [], attach: [], detach: [], getAllFrames: [] },
};
function reset() {
  state.calls = { cdp: [], executeScript: [], attach: [], detach: [], getAllFrames: [] };
  state.evalReplies = [];
  state.evalReply = { result: { value: null } };
  state.autoAttachTargets = [{ sessionId: "SID_OOPIF", url: OOPIF_URL }];
  state.hangEvaluate = false;
  delete globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS;
}

const evtListeners = new Set();

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  webNavigation: {
    async getAllFrames({ tabId }) { state.calls.getAllFrames.push(tabId); return state.frames; },
  },
  scripting: {
    async executeScript(params) { state.calls.executeScript.push(params); return [{ result: null }]; },
  },
  tabs: {
    async get(id) { return { ...state.tab, id }; },
    async query() { return [state.tab]; },
    async update() {},
  },
  debugger: {
    async attach(target) { state.calls.attach.push(target); },
    async detach(target) { state.calls.detach.push(target); },
    async sendCommand(target, method, params) {
      state.calls.cdp.push({ method, sessionId: target.sessionId, params });
      if (method === "Page.getFrameTree") return { frameTree: FRAME_TREE };
      if (method === "Page.createIsolatedWorld") return { executionContextId: 99 };
      if (method === "Target.setAutoAttach") {
        // Announce the auto-attached OOPIF target(s) to every onEvent listener, exactly
        // as Chrome fires Target.attachedToTarget for existing OOPIFs on setAutoAttach.
        for (const t of state.autoAttachTargets) {
          for (const fn of evtListeners) {
            fn(target, "Target.attachedToTarget",
              { sessionId: t.sessionId, targetInfo: { url: t.url, type: "iframe", targetId: "T_" + t.sessionId } });
          }
        }
        return {};
      }
      if (method === "Runtime.evaluate") {
        if (state.hangEvaluate) return new Promise(() => {});   // never settles → timeout
        return state.evalReplies.length ? state.evalReplies.shift() : state.evalReply;
      }
      return {};
    },
    onDetach: { addListener() {} },
    onEvent: {
      addListener(fn) { evtListeners.add(fn); },
      removeListener(fn) { evtListeners.delete(fn); },
    },
  },
  storage: { local: { async get() { return {}; }, async set() {} } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} } },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS } = await import("../extension/service_worker.js");

const cdpMethods = () => state.calls.cdp.map((c) => c.method);
const lastEval = () => [...state.calls.cdp].reverse().find((c) => c.method === "Runtime.evaluate");

// --------------------------------------------------------------------------- //
test("eval --frame 0 (SAME-PROCESS/top): CDP Runtime.evaluate returns the VALUE, not null", async () => {
  reset();
  state.evalReply = { result: { value: TOP_URL } };   // e.g. location.href in the top frame
  const out = await OPS.eval({ tabId: TAB_ID, frame: "0", js: "location.href" });
  // The CDP path was taken — attach happened, chrome.scripting did NOT.
  assert.equal(state.calls.attach.length, 1, "eval --frame must attach chrome.debugger (CDP path)");
  assert.equal(state.calls.executeScript.length, 0, "eval --frame must NOT use chrome.scripting");
  // Same-process → frame tree lookup + isolated world + evaluate(contextId).
  assert.ok(cdpMethods().includes("Page.getFrameTree"));
  assert.ok(cdpMethods().includes("Page.createIsolatedWorld"));
  assert.equal(lastEval().params.contextId, 99, "evaluated in the frame's isolated-world context");
  assert.equal(lastEval().params.returnByValue, true);
  assert.ok(String(lastEval().params.expression).includes("location.href"));
  // THE REGRESSION: the value is the eval result, NOT null.
  assert.equal(out.value, TOP_URL);
  assert.equal(out.frame, "0");
  assert.equal(out.url, TOP_URL, "reports the frame's own url");
  assert.equal(state.calls.detach.length, 1, "always detaches");
});

test("eval --frame <oopif>: OOPIF target resolved via setAutoAttach flat session → Runtime.evaluate returns the value", async () => {
  reset();
  state.evalReply = { result: { value: OOPIF_URL } };   // location.href INSIDE the OOPIF
  const out = await OPS.eval({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai", js: "location.href" });
  assert.equal(state.calls.executeScript.length, 0, "still not chrome.scripting");
  // OOPIF path: NOT in the frame tree → auto-attach → evaluate in the matched SESSION.
  assert.ok(cdpMethods().includes("Target.setAutoAttach"), "auto-attaches to reach the OOPIF target");
  assert.equal(lastEval().sessionId, "SID_OOPIF", "evaluate is issued in the OOPIF's flat session");
  assert.equal(lastEval().params.contextId, undefined, "OOPIF eval uses the session default context");
  // PROOF eval ran inside the OOPIF (its own url), not null.
  assert.equal(out.value, OOPIF_URL);
  assert.equal(out.url, OOPIF_URL);
  assert.equal(state.calls.detach.length, 1);
});

test("eval --frame numeric OOPIF id also resolves the OOPIF session (numeric id → url → CDP target)", async () => {
  reset();
  state.evalReply = { result: { value: 1234 } };
  const out = await OPS.eval({ tabId: TAB_ID, frame: "7", js: "6*206 + 2" });
  assert.equal(lastEval().sessionId, "SID_OOPIF");
  assert.equal(out.value, 1234, "not null — the numeric frameId mapped to the OOPIF's url→session");
});

test("NEVER-SILENT-NULL: a Runtime.evaluate exceptionDetails → frame_eval_failed (not value:null)", async () => {
  reset();
  state.evalReply = { result: { type: "object" },
    exceptionDetails: { exception: { description: "TypeError: foo is not a function" } } };
  await assert.rejects(() => OPS.eval({ tabId: TAB_ID, frame: "0", js: "foo()" }),
    /frame_eval_failed:TypeError: foo is not a function/);
  assert.equal(state.calls.detach.length, 1, "detaches even when the eval threw");
});

test("NEVER-SILENT-NULL: an unresolvable OOPIF frame → frame_not_found (not value:null)", async () => {
  reset();
  // getAllFrames still HAS frame 7 (resolveFrame succeeds), but auto-attach announces a
  // DIFFERENT target → no session matches its url → frame_not_found, never a silent null.
  state.autoAttachTargets = [{ sessionId: "SID_OTHER", url: "https://unrelated.example/" }];
  await assert.rejects(() => OPS.eval({ tabId: TAB_ID, frame: "7", js: "location.href" }),
    /frame_not_found:https:\/\/model-benchmarking\.civit\.ai\//);
  // Runtime.evaluate was NEVER issued (we failed to resolve the context first).
  assert.equal(cdpMethods().includes("Runtime.evaluate"), false);
  assert.equal(state.calls.detach.length, 1, "still detaches on the frame_not_found path");
});

test("a genuine null result is returned AS a value (distinct from a failure to execute)", async () => {
  reset();
  state.evalReply = { result: { value: null } };
  const out = await OPS.eval({ tabId: TAB_ID, frame: "0", js: "window.__nothing" });
  assert.equal(out.value, null, "a real null eval result is a legitimate value, not an error");
});

test("SyntaxError on the expression form retries the statement form ONCE", async () => {
  reset();
  // First evaluate (expression form) → CDP SyntaxError; second (statement fallback) → value.
  state.evalReplies = [
    { exceptionDetails: { exception: { className: "SyntaxError" }, text: "Uncaught SyntaxError" } },
    { result: { value: 7 } },
  ];
  const out = await OPS.eval({ tabId: TAB_ID, frame: "0", js: "var x = 7; x" });
  assert.equal(out.value, 7);
  const evals = state.calls.cdp.filter((c) => c.method === "Runtime.evaluate");
  assert.equal(evals.length, 2, "exactly two evaluate calls: expression then statement fallback");
});

test("#189: a HUNG Runtime.evaluate settles with cdp_timeout and STILL detaches — no SW wedge", async () => {
  reset();
  // Shrink the CDP budgets (test-only hook) so this settles in ms, not the 8s default.
  globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS = { attachMs: 60, commandMs: 20, budgetMs: 200 };
  state.hangEvaluate = true;
  const started = Date.now();
  await assert.rejects(() => OPS.eval({ tabId: TAB_ID, frame: "0", js: "while(1){}" }),
    /cdp_timeout/);
  assert.ok(Date.now() - started < 4000, "must settle by the tiny budget, not the 20s server timeout");
  assert.equal(state.calls.detach.length, 1, "a hung eval must still best-effort detach (no leaked session)");
});

test("SECURITY: eval --frame attaches ONLY to the op's own tab; typed op — no raw CDP passthrough", async () => {
  reset();
  state.evalReply = { result: { value: 1 } };
  await OPS.eval({ tabId: TAB_ID, frame: "0", js: "1" });
  // Every attach/detach targeted THIS tab (own-tab-only, #187).
  assert.ok(state.calls.attach.every((t) => t.tabId === TAB_ID));
  assert.ok(state.calls.detach.every((t) => t.tabId === TAB_ID || t.tabId === undefined));
  // Frame enumeration was scoped to this tab too.
  assert.ok(state.calls.getAllFrames.every((t) => t === TAB_ID));
  // The only CDP methods used are the FIXED typed set — no arbitrary CDP surface.
  const allowed = new Set(["Page.getFrameTree", "Page.createIsolatedWorld",
                           "Target.setAutoAttach", "Runtime.evaluate"]);
  for (const m of cdpMethods()) assert.ok(allowed.has(m), `unexpected CDP method: ${m}`);
});
