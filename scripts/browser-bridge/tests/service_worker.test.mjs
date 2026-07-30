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
const OOPIF_URL = "https://model-benchmarking.civit.ai/";
const state = {
  frames: [
    { frameId: 0, parentFrameId: -1, url: "https://civitai.com/apps/run/model-benchmarking" },
    { frameId: 7, parentFrameId: 0, url: OOPIF_URL },
  ],
  execResult: { ok: true },
  tab: { id: TAB_ID, url: "https://civitai.com/apps/run/model-benchmarking",
         title: "Model Benchmarking", active: false, status: "complete", windowId: 1 },
  calls: { getAllFrames: [], executeScript: [], debugger: [], tabsGet: [] },
};
function resetCalls() {
  state.calls = { getAllFrames: [], executeScript: [], debugger: [], tabsGet: [] };
  state.execResult = { ok: true };
}

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
    async update() {},
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
  const out = await OPS.text({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai" });
  const call = lastExec();
  assert.deepEqual(call.target, { tabId: TAB_ID, frameIds: [7] },
    "the resolved OOPIF frameId is the executeScript target");
  assert.equal(typeof call.func, "function");
  assert.equal(out.text, "INNER OOPIF TEXT");
  assert.equal(out.frame, "model-benchmarking.civit.ai");
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
// NOTE: `eval --frame` no longer uses executeScript (chrome.scripting can only run a
// serialized FUNC, never an arbitrary JS STRING → the #190 value:null bug). It now runs
// via CDP Runtime.evaluate — see tests/frame_eval_cdp.test.mjs.

test("--frame click/type/key: injected into the OOPIF frame; report trusted:false", async () => {
  resetCalls();
  state.execResult = { ok: true, x: 60, y: 40 };
  const c = await OPS.click({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai", selector: "#run" });
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] });
  assert.deepEqual(lastExec().args, ["#run"]);
  assert.deepEqual(c, { url: OOPIF_URL, clicked: "#run", x: 60, y: 40,
                        frame: "model-benchmarking.civit.ai", trusted: false });

  resetCalls();
  state.execResult = { ok: true, typed: 5 };
  const t = await OPS.type({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai", text: "hello", selector: "#q" });
  assert.deepEqual(lastExec().target, { tabId: TAB_ID, frameIds: [7] });
  assert.deepEqual(lastExec().args, ["#q", "hello"]);
  assert.equal(t.typed, 5);
  assert.equal(t.trusted, false);
  assert.ok(!("text" in t), "type must never echo the text");

  resetCalls();
  state.execResult = { ok: true, key: "Enter" };
  const k = await OPS.key({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai", key: "Enter" });
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
