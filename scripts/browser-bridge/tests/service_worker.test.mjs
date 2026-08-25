// Glue tests for service_worker.js OPS against a MOCKED chrome — proving the OOPIF
// fix is wired correctly WITHOUT a real Brave:
//   * `frames` calls chrome.webNavigation.getAllFrames({tabId}) and returns the list
//     INCLUDING the cross-origin OOPIF child (the exact regression);
//   * `--frame` reads/inputs resolve a NUMERIC frameId and inject via
//     chrome.scripting.executeScript({target:{tabId, frameIds:[id]}}) — never CDP;
//   * an unknown/absent frame → a clear error, before any injection;
//   * SECURITY: frame resolution is confined to the op's OWN tab (getAllFrames is
//     tab-scoped) and an unknown `key` is refused before touching the tab;
//   * `screenshot` STILL uses the chrome.debugger (CDP) path — not regressed.
//
// The SW auto-start (poll loop + listeners) is suppressed via BROWSER_BRIDGE_NO_AUTOSTART
// so importing it here does not start networking.

import test from "node:test";
import assert from "node:assert/strict";

// --- a single mutable chrome mock the SW closes over -------------------------- //
const TAB_ID = 5;
// Distinct from TAB_ID on purpose: `open`'s reuse tests turn on WHICH tab came
// back, so a shared id would let a reused tab and a freshly created one satisfy
// the same assertion (the collapsed-fixture trap).
const FRESH_TAB_ID = 91;
const OOPIF_URL = "https://model-benchmarking.example.test/";
const state = {
  frames: [
    { frameId: 0, parentFrameId: -1, url: "https://civitai.com/apps/run/model-benchmarking" },
    { frameId: 7, parentFrameId: 0, url: OOPIF_URL },
  ],
  execResult: { ok: true },
  tab: { id: TAB_ID, url: "https://civitai.com/apps/run/model-benchmarking",
         title: "Model Benchmarking", active: false, status: "complete", windowId: 1 },
  calls: { getAllFrames: [], executeScript: [], debugger: [], tabsGet: [],
           tabsUpdate: [], windowsUpdate: [], tabsCreate: [] },
  crumbs: [],
};
function resetCalls() {
  state.calls = { getAllFrames: [], executeScript: [], debugger: [], tabsGet: [],
                  tabsUpdate: [], windowsUpdate: [], tabsCreate: [] };
  state.crumbs = [];
  state.execResult = { ok: true };
}
// Keep the `activate` wait fast + deterministic in these wiring tests (the wait
// LOGIC itself is unit-tested in protocol.test.mjs): no paint settle, 1ms polls.
globalThis.BROWSER_BRIDGE_ACTIVATE_TIMING = { settleMs: 0, pollMs: 1 };

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  webNavigation: {
    async getAllFrames({ tabId }) { state.calls.getAllFrames.push(tabId); return state.frames; },
  },
  scripting: {
    async executeScript(params) {
      state.calls.executeScript.push(params);
      return [{ result: state.execResult, frameId: (params.target.frameIds || [0])[0] }];
    },
  },
  tabs: {
    async get(id) { state.calls.tabsGet.push(id); return { ...state.tab, id }; },
    async query() { return [state.tab]; },
    async captureVisibleTab() { return "data:image/png;base64,AAAA"; },
    async create(props) {
      state.calls.tabsCreate.push(props);
      return { id: FRESH_TAB_ID, url: (props && props.url) || "about:blank" };
    },
    async update(id, props) {
      state.calls.tabsUpdate.push({ id, props });
      if (props) Object.assign(state.tab, props);   // e.g. {active:true}
      return { ...state.tab, id };
    },
  },
  windows: {
    async update(windowId, props) {
      state.calls.windowsUpdate.push({ windowId, props });
    },
  },
  debugger: {
    async attach() { state.calls.debugger.push("attach"); },
    async detach() { state.calls.debugger.push("detach"); },
    async sendCommand(_t, method) {
      state.calls.debugger.push(method);
      if (method === "Page.captureScreenshot") return { data: "QkJCQg==" };
      return {};
    },
    onDetach: { addListener() {} },
    onEvent: { addListener() {}, removeListener() {} },
  },
  storage: { local: {
    async get() { return {}; },
    async set(v) { if (v && v.lastExec) state.crumbs.push(v.lastExec); },
  } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} } },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS, loopTiming } = await import("../extension/service_worker.js");
const { FAST_CAPTURE_BUDGET_MS, EXEC_OP_BUDGET_MS, POLL_BUDGET_MS,
        RESULT_BUDGET_MS, LOOP_STALL_MS, STORAGE_BUDGET_MS, REUSE_TAB_BUDGET_MS }
  = await import("../extension/protocol.js");

function lastExec() { return state.calls.executeScript[state.calls.executeScript.length - 1]; }

// --------------------------------------------------------------------------- //
test("frames op: enumerates via webNavigation.getAllFrames — the OOPIF child IS present", async () => {
  resetCalls();
  const out = await OPS.frames({ tabId: TAB_ID });
  // getAllFrames was called for THIS tab.
  assert.deepEqual(state.calls.getAllFrames, [TAB_ID]);
  // No CDP/debugger was used to enumerate frames anymore.
  assert.deepEqual(state.calls.debugger, []);
  // THE REGRESSION: the cross-origin OOPIF appears in the returned list.
  assert.ok(out.frames.some((f) => f.url === OOPIF_URL),
    "the cross-origin frame must be enumerated (getFrameTree missed it)");
  assert.deepEqual(out.frames.find((f) => f.url === OOPIF_URL),
    { frameId: 7, url: OOPIF_URL, parentFrameId: 0 });
});

test("--frame text: resolves the numeric frameId + injects via executeScript into that frame", async () => {
  resetCalls();
  state.execResult = "INNER OOPIF TEXT";
  const out = await OPS.text({ tabId: TAB_ID, frame: "model-benchmarking.example.test" });
  const call = lastExec();
  assert.deepEqual(call.target, { tabId: TAB_ID, frameIds: [7] },
    "the resolved OOPIF frameId is the executeScript target");
  assert.equal(typeof call.func, "function");
  assert.equal(out.text, "INNER OOPIF TEXT");
  assert.equal(out.frame, "model-benchmarking.example.test");
  assert.equal(out.url, OOPIF_URL,
    "a frame read reports the FRAME's own url (proves it targeted the OOPIF, not the top)");
  assert.deepEqual(state.calls.debugger, [], "a frame read must NOT use the debugger");
});

test("--frame html: inject into the resolved OOPIF frame via executeScript; reports the FRAME url", async () => {
  resetCalls();
  state.execResult = "<html>oopif</html>";
  const h = await OPS.getHtml({ tabId: TAB_ID, frame: "7" });   // numeric frame id
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] });
  assert.equal(h.html, "<html>oopif</html>");
  assert.equal(h.url, OOPIF_URL, "a frame read reports the FRAME's own url, not the top url");
  assert.deepEqual(state.calls.debugger, [], "a fixed-func frame read must NOT use the debugger");
});
// REGRESSION (v0.7.1): `--annotated` + `--frame` used to throw
// `annotated_with_frame_unsupported` before any injection. It now injects
// annotatedTextFn into the resolved OOPIF and returns the element payload.
// RED on origin/main (a0a5d73): the old code threw, so no executeScript ran.
test("--frame text --annotated: injects annotatedTextFn into the OOPIF; no longer refused", async () => {
  resetCalls();
  // Frame-RELATIVE CSS paths — the annotated payload is relative to the FRAME's
  // own document, not the top page (there is no `iframe > …` prefix).
  state.execResult = {
    elements: [{ tag: "button", text: "Run", cssPath: "#panel > button" }],
    count: 1,
  };
  const out = await OPS.text({ tabId: TAB_ID, frame: "7", annotated: true });
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] },
    "annotated-in-frame must inject into the RESOLVED frame, not the top page");
  assert.equal(typeof lastExec().func, "function");
  assert.equal(out.count, 1);
  assert.deepEqual(out.elements,
    [{ tag: "button", text: "Run", cssPath: "#panel > button" }]);
  assert.equal(out.url, OOPIF_URL, "reports the FRAME's own url");
  assert.equal(out.frame, "7");
  assert.equal(out.truncated, 0);
  assert.equal(out.text, undefined, "annotated replaces flat text, it does not add to it");
  assert.deepEqual(state.calls.debugger, [], "an annotated frame read must NOT use the debugger");
});

test("--frame text --annotated: byte-cap applies inside the frame path too", async () => {
  resetCalls();
  state.execResult = {
    elements: Array.from({ length: 20 },
      (_, i) => ({ tag: "p", text: `element-${i}`.padEnd(60, "."), cssPath: `p:nth-of-type(${i + 1})` })),
    count: 20,
  };
  const out = await OPS.text({ tabId: TAB_ID, frame: "7", annotated: true, maxBytes: 300 });
  assert.ok(out.elements.length < 20,
    `byte-cap must drop elements in the frame path; kept ${out.elements.length}`);
  assert.equal(out.count, out.elements.length);
  assert.ok(out.truncated > 0, "truncated byte count must be reported");
});

// NOTE: `eval --frame` no longer uses executeScript (chrome.scripting can only run a
// serialized FUNC, never an arbitrary JS STRING → the #190 value:null bug). It now runs
// via CDP Runtime.evaluate — see tests/frame_eval_cdp.test.mjs.

test("--frame click/type/key: injected into the OOPIF frame; report trusted:false", async () => {
  resetCalls();
  state.execResult = { ok: true, x: 60, y: 40 };
  const c = await OPS.click({ tabId: TAB_ID, frame: "model-benchmarking.example.test", selector: "#run" });
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] });
  assert.deepEqual(lastExec().args, ["#run"]);
  assert.deepEqual(c, { url: OOPIF_URL, clicked: "#run", x: 60, y: 40,
                        frame: "model-benchmarking.example.test", trusted: false });

  resetCalls();
  state.execResult = { ok: true, typed: 5 };
  const t = await OPS.type({ tabId: TAB_ID, frame: "model-benchmarking.example.test", text: "hello", selector: "#q" });
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] });
  assert.deepEqual(lastExec().args, ["#q", "hello"]);
  assert.equal(t.typed, 5);
  assert.equal(t.trusted, false);
  assert.ok(!("text" in t), "type must never echo the text");

  resetCalls();
  state.execResult = { ok: true, key: "Enter" };
  const k = await OPS.key({ tabId: TAB_ID, frame: "model-benchmarking.example.test", key: "Enter" });
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] });
  // The bounded key params (resolved in the SW) are passed to the injected fn.
  assert.equal(lastExec().args[0], "");            // no --selector
  assert.equal(lastExec().args[1].key, "Enter");
  assert.equal(lastExec().args[1].keyCode, 13);
  assert.equal(k.trusted, false);
  assert.deepEqual(state.calls.debugger, [], "in-frame input must NOT use the debugger");
});

test("--frame input: an element_not_found in the frame surfaces a clear op error", async () => {
  resetCalls();
  state.execResult = { ok: false, error: "element_not_found" };
  await assert.rejects(() => OPS.click({ tabId: TAB_ID, frame: "7", selector: "#missing" }),
    /element_not_found:#missing/);
  await assert.rejects(() => OPS.type({ tabId: TAB_ID, frame: "7", text: "x", selector: "#missing" }),
    /element_not_found:#missing/);
});

test("--frame unknown: frame_not_found is raised BEFORE any injection", async () => {
  resetCalls();
  await assert.rejects(() => OPS.text({ tabId: TAB_ID, frame: "does-not-exist" }),
    /frame_not_found:does-not-exist/);
  assert.equal(state.calls.executeScript.length, 0, "no injection for an unresolved frame");
});

test("SECURITY: frame resolution is confined to the op's OWN tab (getAllFrames is tab-scoped)", async () => {
  resetCalls();
  // Even though the model supplies frame "7", it is resolved ONLY against getAllFrames
  // for the op's tab (TAB_ID) — never another tab. Assert every getAllFrames call used
  // THIS tab id, and the executeScript target tabId is THIS tab.
  state.execResult = "x";
  await OPS.text({ tabId: TAB_ID, frame: "7" });
  assert.ok(state.calls.getAllFrames.every((t) => t === TAB_ID),
    "frame enumeration must be scoped to the op's own tab");
  assert.equal(lastExec().target.tabId, TAB_ID,
    "executeScript must target the op's own tab — a frameId can't escape it");
});

test("SECURITY: an unknown key is refused before ANY frame enumeration or injection", async () => {
  resetCalls();
  await assert.rejects(() => OPS.key({ tabId: TAB_ID, frame: "7", key: "F13" }), /unknown_key:F13/);
  assert.equal(state.calls.getAllFrames.length, 0, "no frame lookup for a refused key");
  assert.equal(state.calls.executeScript.length, 0, "no injection for a refused key");
});

test("screenshot STILL uses the chrome.debugger (CDP) path — not regressed to scripting", async () => {
  resetCalls();
  const out = await OPS.screenshot({ tabId: TAB_ID });   // tab.active:false → CDP path
  assert.ok(state.calls.debugger.includes("attach"));
  assert.ok(state.calls.debugger.includes("Page.captureScreenshot"));
  assert.ok(state.calls.debugger.includes("detach"), "always detaches");
  assert.equal(state.calls.executeScript.length, 0, "screenshot must not use executeScript");
  assert.equal(state.calls.getAllFrames.length, 0, "screenshot must not enumerate frames");
  assert.equal(out.via, "cdp");
  assert.match(out.dataUrl, /^data:image\/png;base64,/);
});

// --------------------------------------------------------------------------- //
// 🔴 THE FAST PATH MUST SURVIVE A HANG, NOT ONLY A REJECTION.
//
// `chrome.tabs.captureVisibleTab` can simply never settle. Before the fast-path
// bound, the `catch` that is supposed to fall through to CDP could not see that:
// the await never returned, and the whole op died at EXEC_OP_BUDGET_MS (18s) —
// on a tab CDP would have captured in well under a second. Measured 2026-08-24,
// 3/3 `op_timeout:screenshot` at 18.07-18.11s vs 3/3 CDP successes 381-3084ms.
//
// RED WITHOUT THE FIX — and it must be red as a FAILURE, never as a hang.
//
// 🔴 THE `{ timeout }` IS LOAD-BEARING, NOT HYGIENE. With the bound reverted and
// no per-test timeout, this file does not fail — it WEDGES: measured, the runner
// never exits (still running at 200s), prints NO summary, reports `not ok` = 0
// and `# fail` = 0, and the 12 tests declared after this one NEVER RUN. So the
// regression this test exists to catch would read as CLEAN to any gate that
// counts failures, while silently blinding the back half of the file. A hang is
// the one failure shape a green-suite check cannot distinguish from success.
// With the timeout it fails with an attributable message and the file completes.
// 🔴 CLEANUP IS `t.after`, NOT `finally`, AND THAT IS THE SECOND HALF OF THE FIX.
// A `finally` around the await does NOT run when the test times out — the await
// never settles, so the block is never reached. Measured with the bound reverted:
// the hung stub and `tab.active = true` LEAKED into the following tests, hanging
// the healthy-fast-path control too, and the file still died at the runner's own
// timeout with `fail=0, cancelled=2` and 10 tests never run — i.e. still the
// count-blind shape this timeout was added to remove. `t.after` runs even on a
// timed-out test, so exactly ONE test fails and the other 22 still execute.
test("screenshot fast path: a HUNG captureVisibleTab falls through to CDP",
     { timeout: 2000 }, async (t) => {
  resetCalls();
  const realCapture = chrome.tabs.captureVisibleTab;
  const realTiming = globalThis.BROWSER_BRIDGE_LOOP_TIMING;
  t.after(() => {
    chrome.tabs.captureVisibleTab = realCapture;
    globalThis.BROWSER_BRIDGE_LOOP_TIMING = realTiming;
    state.tab.active = false;
  });
  // 20ms bound instead of the real 1500ms, via the same injection point the loop
  // budgets already use. The production VALUE is pinned separately, in
  // cdp_protocol.test.mjs — this test covers the mechanism, not the number.
  globalThis.BROWSER_BRIDGE_LOOP_TIMING = { ...(realTiming || {}), fastCaptureMs: 20 };
  chrome.tabs.captureVisibleTab = () => new Promise(() => {});   // never settles
  state.tab.active = true;                                        // → fast path
  // 🔴 RACE IT HERE rather than leaning on `{ timeout }` alone, so a regression is
  // counted as a FAILURE and not as a CANCELLATION. node scores a timed-out test
  // `cancelled`, leaving `fail` at 0 — so a gate that greps the fail count reads
  // a regression as clean, which is the same count-blindness in a smaller shape.
  // Racing it makes the regression an ordinary assertion failure with a message
  // that names the cause. The `{ timeout: 2000 }` above stays as the backstop for
  // anything that hangs OUTSIDE this race.
  let hangTimer;
  const out = await Promise.race([
    OPS.screenshot({ tabId: TAB_ID }),
    new Promise((_, reject) => {
      hangTimer = setTimeout(
        () => reject(new Error("screenshot did not settle within 1s: the fast path "
                               + "is unbounded, so a hung captureVisibleTab never "
                               + "reaches the catch that falls through to CDP")),
        1000);
    }),
  ]).finally(() => clearTimeout(hangTimer));
  assert.equal(out.via, "cdp", "a hung fast path must fall through to CDP");
  assert.match(out.dataUrl, /^data:image\/png;base64,/);
  assert.ok(state.calls.debugger.includes("Page.captureScreenshot"),
            "the CDP path actually ran");
  assert.ok(state.calls.debugger.includes("detach"), "always detaches");
  // (No elapsed-time assertion here: the Promise.race above already rejects at
  // 1000ms, so any run reaching this point is necessarily under it. An earlier
  // `Date.now() - started < 1000` could therefore only ever fire as a 1-2ms
  // boundary flake — a guard that cannot fail for its stated reason is worse than
  // none, because it reads as coverage. The bound's magnitude is not pinned
  // anywhere — it is BOUNDED, from both sides: `> 1365` and the `FAST + 16s <=
  // 18s` sum invariant, both in cdp_protocol.test.mjs. The wiring test below pins
  // the WIRE, not the value. Saying "pinned" of either would be a fourth loose
  // claim in a PR whose whole history is loose claims.)
});

// 🔴 PINNING THE CONSTANT IS NOT PINNING THAT PRODUCTION USES IT.
//
// The test above injects `fastCaptureMs: 20`, and cdp_protocol.test.mjs asserts
// the CONSTANT's value — so between them, nothing checked the wire connecting the
// two. Measured by a re-audit: mutating loopTiming()'s default from
// FAST_CAPTURE_BUDGET_MS to a literal 600000 left ALL 501 tests green, i.e. the
// original surviving mutant was still alive, just spelled one level down. This is
// the seam assertion; it must run with NO override active.
// 🔴 ALL SIX WIRES, not just the one this PR touched. Pinning only fastCaptureMs
// left the SAME seam open on every other budget — and on the most important one:
// mutating `execMs`'s default from EXEC_OP_BUDGET_MS to a literal 999999 left the
// whole 502-test suite GREEN, i.e. the choke-point op budget could be silently
// disconnected from its constant while every test that pins the CONSTANT's value
// still passed. A per-field loop makes adding a 7th budget fail here until it is
// wired, rather than silently going unpinned like these five did.
test("loop budgets are wired to their constants, not to literals", async () => {
  const realTiming = globalThis.BROWSER_BRIDGE_LOOP_TIMING;
  delete globalThis.BROWSER_BRIDGE_LOOP_TIMING;
  try {
    const wired = {
      execMs: EXEC_OP_BUDGET_MS,
      pollMs: POLL_BUDGET_MS,
      resultMs: RESULT_BUDGET_MS,
      stallMs: LOOP_STALL_MS,
      storageMs: STORAGE_BUDGET_MS,
      fastCaptureMs: FAST_CAPTURE_BUDGET_MS,
      reuseTabMs: REUSE_TAB_BUDGET_MS,
    };
    // 🔴 THE GUARD IS ONLY AS GOOD AS THE VALUES BEING DISTINCT. If two budgets
    // ever hold the SAME number, swapping their wires passes every assertion
    // below — the collapsed-fixture trap, where a fixture cannot express the
    // difference it exists to detect. They are pairwise distinct today
    // (1500/2000/5000/10000/18000/40000/180000); this keeps it that way rather than
    // leaving it to luck, and fails loudly on the day someone picks a duplicate.
    assert.equal(new Set(Object.values(wired)).size, Object.keys(wired).length,
                 "two budgets share a value — a swapped wire would be undetectable; "
                 + "give them distinct values or assert the swap directly");
    const actual = loopTiming();
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(wired).sort(),
                     "a budget was added or removed — wire it to a constant here");
    for (const [field, constant] of Object.entries(wired)) {
      assert.equal(actual[field], constant,
                   `loopTiming().${field} must read its protocol.js constant, or the `
                   + `value assertions elsewhere do not govern runtime`);
    }
  } finally {
    if (realTiming === undefined) delete globalThis.BROWSER_BRIDGE_LOOP_TIMING;
    else globalThis.BROWSER_BRIDGE_LOOP_TIMING = realTiming;
  }
});

// ATTRIBUTION CONTROL for the test above: the bound must not simply push every
// capture onto CDP. A healthy fast path is still the fast path — otherwise the
// test above would pass just as well with the fast path deleted outright, and
// would be recording coverage it does not have.
test("screenshot fast path: a HEALTHY captureVisibleTab is still used", async () => {
  resetCalls();
  state.tab.active = true;
  try {
    const out = await OPS.screenshot({ tabId: TAB_ID });
    assert.equal(out.via, "captureVisibleTab", "healthy fast path must NOT go to CDP");
    assert.equal(state.calls.debugger.length, 0, "no debugger attach on the fast path");
  } finally {
    state.tab.active = false;
  }
});

// --------------------------------------------------------------------------- //
// 🔴 `open`'s REUSE PROBE MUST SURVIVE A HANG, NOT ONLY A REJECTION.
//
// THE PREDICTED REGENERATION. When the fast-path bound above shipped (#797), the
// README named this exact call as "same class, not yet fixed": `open`'s idempotent
// re-open path awaits `chrome.tabs.get(cmd.reuseTabId)` inside a `try` whose
// `catch` promises "owned tab gone → open a fresh one below". That promise is only
// ever true of a REJECTION. Unbounded, a `tabs.get` that never settles never
// reaches the catch, so the fall-through never happens and the op dies at
// EXEC_OP_BUDGET_MS (18s) — on `open`, the first op of every session.
//
// RED WITHOUT THE FIX — and red as a FAILURE, never as a hang. Both halves of the
// #797 lesson apply verbatim and are NOT hygiene:
//   * `t.after`, NOT `finally`. A `finally` wrapped around a hung await never runs
//     (the await never settles, so the block is never reached), leaking the hung
//     stub into every following test and hanging them too — which is exactly the
//     count-blind shape this test exists to remove.
//   * RACE THE CALL HERE rather than leaning on `{ timeout }` alone. node scores a
//     timed-out test `cancelled`, leaving `fail` at 0, so a gate that greps the
//     fail count reads a live regression as clean. Racing it makes the regression
//     an ordinary assertion failure that names its own cause; the `{ timeout }`
//     stays only as a backstop for anything hanging OUTSIDE the race.
test("open reuse probe: a HUNG chrome.tabs.get falls through to a fresh tab",
     { timeout: 2000 }, async (t) => {
  resetCalls();
  const realGet = chrome.tabs.get;
  const realTiming = globalThis.BROWSER_BRIDGE_LOOP_TIMING;
  t.after(() => {
    chrome.tabs.get = realGet;
    globalThis.BROWSER_BRIDGE_LOOP_TIMING = realTiming;
  });
  // 20ms bound instead of the real 2000ms, through the same injection point the
  // loop budgets use. The production VALUE is pinned separately in
  // cdp_protocol.test.mjs — this covers the mechanism, not the number.
  globalThis.BROWSER_BRIDGE_LOOP_TIMING = { ...(realTiming || {}), reuseTabMs: 20 };
  chrome.tabs.get = () => new Promise(() => {});   // never settles
  let hangTimer;
  const out = await Promise.race([
    OPS.open({ reuseTabId: TAB_ID, url: "https://civitai.com/" }),
    new Promise((_, reject) => {
      hangTimer = setTimeout(
        () => reject(new Error("open did not settle within 1s: the reuse probe is "
                               + "unbounded, so a hung chrome.tabs.get never reaches "
                               + "the catch that opens a fresh tab")),
        1000);
    }),
  ]).finally(() => clearTimeout(hangTimer));
  assert.equal(out.tabId, FRESH_TAB_ID,
               "a hung reuse probe must fall through and create a NEW tab");
  assert.ok(!out.reused, "a hung probe must not be reported as a reuse");
  assert.equal(state.calls.tabsCreate.length, 1, "the fall-through actually created a tab");
  assert.deepEqual(state.calls.tabsCreate[0],
                   { url: "https://civitai.com/", active: false },
                   "the fresh tab keeps open's normal shape (background, requested url)");
});

// ATTRIBUTION CONTROL for the test above: the bound must not simply turn every
// reuse into a fresh tab. A healthy `tabs.get` must still reuse — otherwise the
// test above would pass just as well with the reuse path deleted outright, and
// would be recording coverage it does not have. (Tab reuse is not cosmetic: a
// missed reuse orphans the previous tab, which nothing owns and nothing closes.)
test("open reuse probe: a HEALTHY chrome.tabs.get still reuses the owned tab", async () => {
  resetCalls();
  const out = await OPS.open({ reuseTabId: TAB_ID, url: "https://civitai.com/" });
  assert.equal(out.reused, true, "a healthy probe must reuse, not create");
  assert.equal(out.tabId, TAB_ID, "reuse must return the OWNED tab, not a fresh one");
  assert.deepEqual(state.calls.tabsGet, [TAB_ID], "the probe ran against the owned tab");
  assert.equal(state.calls.tabsCreate.length, 0, "no second tab on the reuse path");
});

// The REJECTION arm — the case the catch always handled. Kept next to the hang arm
// so a change that "fixes" one by breaking the other cannot pass quietly.
test("open reuse probe: a REJECTING chrome.tabs.get still falls through (unchanged)", async (t) => {
  resetCalls();
  const realGet = chrome.tabs.get;
  t.after(() => { chrome.tabs.get = realGet; });
  chrome.tabs.get = async () => { throw new Error("No tab with id: 5."); };
  const out = await OPS.open({ reuseTabId: TAB_ID, url: "https://civitai.com/" });
  assert.equal(out.tabId, FRESH_TAB_ID, "a gone tab still yields a fresh one");
  assert.ok(!out.reused);
  assert.equal(state.calls.tabsCreate.length, 1);
});

// 🔴 THE CRUMB MUST SEPARATE THE TWO CAUSES, WHICH THE RESULT ENVELOPE CANNOT.
//
// A hung probe and a genuinely-gone tab both return the SAME shape — a fresh
// `{tabId, url}` with no `reused` flag. So the only thing that can ever tell
// "correct, the tab was gone" from "we timed out and just orphaned a LIVE tab"
// (the bound's known cost) is the breadcrumb phase. A crumb that spelled both
// cases the same way would provide nothing while reading as observability, so
// the DIFFERENCE is what is asserted here, not the mere presence of a crumb.
//
// 🔴 ITS HANG ARM NEEDS THE SAME RACE + `{ timeout }` AS THE TEST ABOVE, AND THIS
// COMMENT EXISTS BECAUSE THE FIRST DRAFT OMITTED THEM. Any test that drives a
// never-settling `chrome.tabs.get` is a hang test whether or not that is its
// subject: with the bound reverted, the bare `await OPS.open(...)` below wedged the
// whole FILE. Measured on the reverted tree — `fail 1, cancelled 1, tests 18` of
// 30, i.e. 12 tests never ran and node scored the wedge `cancelled`, not `fail`.
// That is precisely the count-blind shape #797's timeout was added to remove,
// reintroduced one test later by a crumb assertion that looked unrelated to it.
test("open reuse probe: the breadcrumb distinguishes a HANG from a genuinely-gone tab",
     { timeout: 2000 }, async (t) => {
  const realGet = chrome.tabs.get;
  const realTiming = globalThis.BROWSER_BRIDGE_LOOP_TIMING;
  t.after(() => {
    chrome.tabs.get = realGet;
    globalThis.BROWSER_BRIDGE_LOOP_TIMING = realTiming;
  });

  resetCalls();
  chrome.tabs.get = async () => { throw new Error("No tab with id: 5."); };
  await OPS.open({ id: "cmd-gone", reuseTabId: TAB_ID });
  const gone = state.crumbs.filter((c) => c.op === "open");
  assert.equal(gone.length, 1, "the gone path leaves exactly one crumb");
  assert.equal(gone[0].phase, "open_reuse_gone");
  assert.equal(gone[0].id, "cmd-gone", "the crumb carries the command id");

  resetCalls();
  globalThis.BROWSER_BRIDGE_LOOP_TIMING = { ...(realTiming || {}), reuseTabMs: 20 };
  chrome.tabs.get = () => new Promise(() => {});   // never settles
  let hangTimer;
  await Promise.race([
    OPS.open({ id: "cmd-hang", reuseTabId: TAB_ID }),
    new Promise((_, reject) => {
      hangTimer = setTimeout(
        () => reject(new Error("open did not settle within 1s: the reuse probe is "
                               + "unbounded, so no crumb is ever written")),
        1000);
    }),
  ]).finally(() => clearTimeout(hangTimer));
  const hung = state.crumbs.filter((c) => c.op === "open");
  assert.equal(hung.length, 1, "the hang path leaves exactly one crumb");
  assert.equal(hung[0].phase, "open_reuse_timeout");
  assert.notEqual(hung[0].phase, gone[0].phase,
                  "a crumb that spells both causes the same way distinguishes nothing");
});

// --------------------------------------------------------------------------- //
// `activate` op: foreground the tab (tabs.update{active} + windows.update{focused})
// then bounded wait-for-load. Wiring only — the wait LOGIC is unit-tested in
// protocol.test.mjs. No CDP/debugger, no executeScript, no new permission.
// --------------------------------------------------------------------------- //
test("activate: makes the tab active + requests its window focus; returns tab info", async () => {
  resetCalls();
  state.tab = { id: TAB_ID, url: "https://model-benchmarking.example.test/",
    title: "Bench", active: false, status: "complete", windowId: 3 };
  const out = await OPS.activate({ tabId: TAB_ID });
  // chrome.tabs.update(tabId, {active:true}) — make it the active tab of its window.
  assert.deepEqual(state.calls.tabsUpdate, [{ id: TAB_ID, props: { active: true } }]);
  // chrome.windows.update(windowId, {focused:true}) — request the window's focus.
  assert.deepEqual(state.calls.windowsUpdate, [{ windowId: 3, props: { focused: true } }]);
  // Returns the resolved tab's info so the caller can confirm it foregrounded.
  assert.equal(out.tabId, TAB_ID);
  assert.equal(out.windowId, 3);
  assert.equal(out.status, "complete");
  assert.equal(out.active, true);
  assert.equal(out.url, "https://model-benchmarking.example.test/");
  // Intrusive-but-bounded: NO debugger attach, NO page injection.
  assert.deepEqual(state.calls.debugger, [], "activate must not use the debugger");
  assert.equal(state.calls.executeScript.length, 0, "activate must not inject a script");
});

test("activate: an already-complete tab short-circuits the wait (returns promptly)", async () => {
  resetCalls();
  state.tab = { id: TAB_ID, url: "https://x.test/", title: "X",
    active: false, status: "complete", windowId: 1 };
  const out = await OPS.activate({ tabId: TAB_ID, waitMs: 5000 });
  assert.equal(out.status, "complete");
  // A complete tab needs at most a couple of tabs.get reads (windowId + the wait's
  // first read) — it must NOT poll in a loop.
  assert.ok(state.calls.tabsGet.length <= 2, "no polling loop for a complete tab");
});

test("activate: a DISCARDED/unloaded tab foregrounds then returns promptly (NO wedge #189)", async () => {
  resetCalls();
  state.tab = { id: TAB_ID, url: "https://x.test/", title: "X",
    active: false, status: "unloaded", discarded: true, windowId: 1 };
  // Even with a large waitMs, an unloaded tab (no live renderer) must fail-fast,
  // never hang waiting for a "complete" that can't come.
  const out = await OPS.activate({ tabId: TAB_ID, waitMs: 8000 });
  assert.equal(state.calls.tabsUpdate.length, 1, "still foregrounds the tab");
  assert.equal(out.status, "unloaded");
  assert.ok(state.calls.tabsGet.length <= 2, "no polling loop for a discarded tab");
});

test("activate: windows.update failure is swallowed (best-effort i3 focus)", async () => {
  resetCalls();
  state.tab = { id: TAB_ID, url: "https://x.test/", title: "X",
    active: false, status: "complete", windowId: 9 };
  const origWindows = globalThis.chrome.windows;
  globalThis.chrome.windows = { async update() { throw new Error("i3 refused to raise"); } };
  try {
    // A WM that refuses the focus request must NOT fail the op — the tab is still
    // set active within its window (the reliable part), focus is best-effort.
    const out = await OPS.activate({ tabId: TAB_ID });
    assert.equal(out.status, "complete");
    assert.deepEqual(state.calls.tabsUpdate, [{ id: TAB_ID, props: { active: true } }]);
  } finally {
    globalThis.chrome.windows = origWindows;
  }
});

// --------------------------------------------------------------------------- //
// `ping` — the build-freshness tell. It must be INERT: no tab lookup, no page
// injection, no CDP. Its only job is to prove WHICH build answered.
// --------------------------------------------------------------------------- //
test("ping: reports the loaded manifest version + id + op set, touching no tab", async () => {
  resetCalls();
  const origRuntime = globalThis.chrome.runtime;
  globalThis.chrome.runtime = { ...origRuntime, id: "abcdefghijklmnop",
    getManifest: () => ({ version: "0.3.1" }) };
  try {
    const out = await OPS.ping({});
    assert.equal(out.pong, true);
    assert.equal(out.extensionVersion, "0.3.1",
      "must report the LOADED build's own manifest, not the repo's");
    // The path-derived id — the ONLY field that says which DIRECTORY Brave
    // loaded. Two builds at the same version but different paths differ here.
    assert.equal(out.id, "abcdefghijklmnop");
    assert.ok(out.ops.includes("ping"), "the op set is self-describing");
    // Inert: a freshness probe must never be able to disturb the operator's tabs.
    assert.deepEqual(state.calls.tabsGet, []);
    assert.deepEqual(state.calls.tabsUpdate, []);
    assert.deepEqual(state.calls.executeScript, []);
    assert.deepEqual(state.calls.debugger, []);
    assert.deepEqual(state.calls.getAllFrames, []);
  } finally {
    globalThis.chrome.runtime = origRuntime;
  }
});

test("ping: degrades to empty version + id when runtime is bare", async () => {
  // The bare mock has neither getManifest nor id — must not throw (best-effort,
  // like the /poll identity headers).
  const out = await OPS.ping({});
  assert.equal(out.pong, true);
  assert.equal(out.extensionVersion, "");
  assert.equal(out.id, "");
});

test("ping: reports the BUILD MARKER, and it is INDEPENDENT of chrome.* (#324)", async () => {
  // 🔴 The property that makes the marker worth having: it comes from the
  // module graph, NOT from anything the browser can be asked at call time.
  // extensionVersion and id both come from chrome.runtime — stub those to a
  // different build entirely and the marker must not move, because it describes
  // the CODE that is executing rather than the directory it was loaded from.
  const { BUILD_MARKER } = await import("../extension/build_id.js");
  assert.match(BUILD_MARKER, /^[0-9a-f]{8,}$/);

  const origRuntime = globalThis.chrome.runtime;
  globalThis.chrome.runtime = { ...origRuntime, id: "zzzzzzzzzzzzzzzz",
    getManifest: () => ({ version: "99.99.99" }) };
  try {
    const out = await OPS.ping({});
    assert.equal(out.buildMarker, BUILD_MARKER);
    assert.equal(out.extensionVersion, "99.99.99");   // control: chrome.* DID move
    assert.equal(out.id, "zzzzzzzzzzzzzzzz");
  } finally {
    globalThis.chrome.runtime = origRuntime;
  }
  // …and with a bare runtime (no getManifest, no id) the marker is STILL there:
  // it never depended on chrome.* in the first place.
  const bare = await OPS.ping({});
  assert.equal(bare.buildMarker, BUILD_MARKER);
  assert.equal(bare.extensionVersion, "");
});

// --------------------------------------------------------------------------- //
// `emulate --reset` — THE RUNTIME NOTE IS PINNED CHARACTER FOR CHARACTER.
//
// WHY A LITERAL, and why the WHOLE string. This note is read by a human operator
// AND forwarded verbatim to the autonomous LLM agent (browser_tool_impl.mjs's
// emulate summarizer carries `note` through on purpose), so a false sentence in
// it is acted on by both. It has already shipped false once: the note claimed
// "the CDP clears were sent" while the reset branch is a single Map delete that
// sends nothing — and NOTHING in the tree asserted the text, which is exactly
// why it shipped. Pinning a FEATURE of the string ("mentions #319", "contains
// the word viewport") would have passed on that false note too. So: the whole
// normalised string, or this test is theatre.
//
// The two tests below are COMPLEMENTS and both are required:
//   * the text says an arm-then-clear pair was sent;
//   * `state.calls.debugger` shows exactly that pair, which is the code-side fact
//     that makes the sentence true. Either alone can go green while the other rots.
//
// ⚠ The note was rewritten in 0.8.1 (#319): reset now DOES undo the viewport. The
// old literal asserted "NOTHING WAS SENT TO THE BROWSER" and a matching empty
// debugger list — both were true of the broken behaviour, and pinning them is
// exactly what kept the false safety claim honest until the fix landed. The
// VIEWPORT assertion itself lives in tests/emulation_reset.test.mjs, against a
// browser model; this file pins the wording and the wire traffic.
// --------------------------------------------------------------------------- //
const RESET_NOTE =
  "emulation stopped AND the emulated viewport was restored: a CDP " +
  "session was attached and Emulation.setDeviceMetricsOverride " +
  "{width:0,height:0} then Emulation.clearDeviceMetricsOverride were " +
  "sent (see `cleared`). Both steps are required — a bare " +
  "clearDeviceMetricsOverride from a session that did not itself set " +
  "an override is a measured no-op, which is why earlier attempts to " +
  "undo this changed nothing (#319). The UA, timezone, " +
  "devicePixelRatio, touch points and prefers-color-scheme were NOT " +
  "cleared and did not need to be: they die with the debugger session " +
  "that set them. If you still see the emulated size, `browser " +
  "emulate --reset --recreate` opens a fresh tab at the same url and " +
  "closes this one (the tab id changes).";

test("emulate --reset: the runtime note is pinned VERBATIM", async () => {
  resetCalls();
  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.equal(out.reset, true);
  assert.equal(out.tabId, TAB_ID);
  // Normalise whitespace runs ONLY — the source is `+`-concatenated across lines,
  // so a re-wrap must not fail, but a WORD change must.
  const norm = (s) => String(s).replace(/\s+/g, " ").trim();
  assert.equal(norm(out.note), norm(RESET_NOTE),
    "the emulate --reset note is a CONTRACT read by the operator AND forwarded " +
    "to the LLM agent — update this literal in the same commit as the string");
});

test("emulate --reset: sends the arm-then-clear pair (the fact the note asserts)", async () => {
  resetCalls();
  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.deepEqual(state.calls.debugger,
    ["attach",
     "Emulation.setDeviceMetricsOverride",
     "Emulation.clearDeviceMetricsOverride",
     "detach"],
    "reset attaches ONE session and sends the arming override then the clear — " +
    "in that order, nothing else. A bare clear is a measured no-op (#319), so " +
    "dropping the first command silently reinstates the bug");
  assert.deepEqual(state.calls.executeScript, [],
    "reset must not inject into the page");
  assert.deepEqual(out.cleared,
    ["Emulation.setDeviceMetricsOverride", "Emulation.clearDeviceMetricsOverride"],
    "`cleared` reports exactly what was acknowledged — the note points at it");
  assert.equal(out.restored, true);
  assert.equal("restoreError" in out, false, "no error key on the success path");
});

test("emulate --reset: reports what WAS in force, then forgets it", async () => {
  resetCalls();
  // Seed real state through the normal apply path (this one DOES use CDP), then
  // reset it — so the "no debugger" assertion above is about reset specifically
  // and not about a mock that never calls the debugger at all.
  await OPS.emulate({ tabId: TAB_ID, width: 390, height: 844 });
  assert.ok(state.calls.debugger.length > 0,
    "HARNESS: applying emulation MUST use the debugger, else the reset test's " +
    "empty-debugger assertion proves nothing about reset");
  resetCalls();
  const out = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.ok(out.wasEmulating, "the reset reports the state it dropped");
  const again = await OPS.emulate({ tabId: TAB_ID, reset: true });
  assert.equal(again.wasEmulating, null,
    "a second reset has nothing left to report — the state really was dropped");
});
