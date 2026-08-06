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
           tabsUpdate: [], windowsUpdate: [] },
};
function resetCalls() {
  state.calls = { getAllFrames: [], executeScript: [], debugger: [], tabsGet: [],
                  tabsUpdate: [], windowsUpdate: [] };
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
  storage: { local: { async get() { return {}; }, async set() {} } },
  runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} } },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

const { OPS } = await import("../extension/service_worker.js");

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
