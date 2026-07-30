// Glue tests for the `upload` op (Gap 1) against a MOCKED chrome.debugger — proving
// the typed CDP file-upload is wired correctly WITHOUT a real Brave. `upload`
// resolves the file input to a CDP RemoteObject, VERIFIES it is a real
// <input type=file>, then hands the ABSOLUTE path to DOM.setFileInputFiles (Chrome
// reads the file itself — no bytes cross the bridge).
//
// These assert, against a mock chrome.debugger/webNavigation:
//   * TOP frame: Runtime.evaluate(querySelector) → objectId → Runtime.callFunctionOn
//     (is-file-input) → DOM.setFileInputFiles({objectId, files:[absPath]}) in the
//     tab's top session;
//   * cross-origin OOPIF (--frame): the SAME setAutoAttach flat-session path
//     `eval --frame` uses → setFileInputFiles issued in the matched SESSION;
//   * not_a_file_input / element_not_found error paths (nothing is set);
//   * SECURITY: own-tab-only attach (#187), a FIXED typed CDP method set (no raw
//     passthrough), and the full path never leaks into the result (basename only);
//   * #189: a hung Runtime.evaluate settles with cdp_timeout and STILL detaches.
//
// SW auto-start is suppressed (BROWSER_BRIDGE_NO_AUTOSTART) so importing does no I/O.

import test from "node:test";
import assert from "node:assert/strict";

const TAB_ID = 5;
const TOP_URL = "https://civitai.com/apps/run/model-benchmarking";
const OOPIF_URL = "https://model-benchmarking.civit.ai/";
const ABS_PATH = "/home/zach/pics/render.png";

const FRAME_TREE = {
  frame: { id: "CDP_MAIN", url: TOP_URL, name: "" },
  childFrames: [{ frame: { id: "CDP_SAME", url: "https://civitai.com/embed/w", name: "w" } }],
};

const state = {
  frames: [
    { frameId: 0, parentFrameId: -1, url: TOP_URL },
    { frameId: 3, parentFrameId: 0, url: "https://civitai.com/embed/w" }, // same-process child
    { frameId: 7, parentFrameId: 0, url: OOPIF_URL },
  ],
  tab: { id: TAB_ID, url: TOP_URL, title: "Bench", active: false, status: "complete", windowId: 1 },
  // Runtime.evaluate reply for the querySelector → RemoteObject: an objectId means
  // "found"; no objectId (subtype null) means element_not_found.
  evalResult: { result: { type: "object", subtype: "node", objectId: "OBJ_FILE" } },
  isFileInput: true,          // Runtime.callFunctionOn(is-file-input) → this value
  autoAttachTargets: [{ sessionId: "SID_OOPIF", url: OOPIF_URL }],
  hangEvaluate: false,
  calls: { cdp: [], attach: [], detach: [], getAllFrames: [], setFile: [] },
};
function reset() {
  state.calls = { cdp: [], attach: [], detach: [], getAllFrames: [], setFile: [] };
  state.evalResult = { result: { type: "object", subtype: "node", objectId: "OBJ_FILE" } };
  state.isFileInput = true;
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
    async executeScript() { throw new Error("upload must NOT use chrome.scripting"); },
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
        return state.evalResult;
      }
      if (method === "Runtime.callFunctionOn") return { result: { value: state.isFileInput } };
      if (method === "DOM.setFileInputFiles") {
        state.calls.setFile.push({ sessionId: target.sessionId, params });
        return {};
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

// --------------------------------------------------------------------------- //
test("upload TOP frame: DOM.setFileInputFiles with the ABS path on the resolved objectId", async () => {
  reset();
  const out = await OPS.upload({ tabId: TAB_ID, selector: "#file", path: ABS_PATH });
  assert.equal(state.calls.setFile.length, 1, "exactly one setFileInputFiles");
  const call = state.calls.setFile[0];
  assert.equal(call.sessionId, undefined, "top-frame upload uses the tab's top session");
  assert.deepEqual(call.params, { objectId: "OBJ_FILE", files: [ABS_PATH] },
    "the ABSOLUTE path is handed to Chrome on the resolved element objectId");
  // The RESULT carries the BASENAME only — never the full path.
  assert.deepEqual(out, { ok: true, selector: "#file", frame: null, url: TOP_URL,
    files: ["render.png"] });
  assert.ok(!JSON.stringify(out).includes("/home/zach"), "the full path must NOT be in the result");
  assert.equal(state.calls.attach.length, 1, "attaches once");
  assert.equal(state.calls.detach.length, 1, "always detaches");
});

test("upload --frame OOPIF: setFileInputFiles is issued in the auto-attached OOPIF session", async () => {
  reset();
  const out = await OPS.upload({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai",
    selector: "#drop input[type=file]", path: ABS_PATH });
  assert.ok(cdpMethods().includes("Target.setAutoAttach"), "reaches the OOPIF via auto-attach");
  assert.equal(state.calls.setFile.length, 1);
  assert.equal(state.calls.setFile[0].sessionId, "SID_OOPIF",
    "the file is set INSIDE the cross-origin OOPIF's flat session");
  assert.deepEqual(state.calls.setFile[0].params.files, [ABS_PATH]);
  assert.equal(out.frame, "model-benchmarking.civit.ai");
  assert.equal(out.url, OOPIF_URL, "reports the FRAME's own url (proves it targeted the OOPIF)");
  assert.deepEqual(out.files, ["render.png"]);
  assert.equal(state.calls.detach.length, 1);
});

test("upload --frame SAME-PROCESS: resolves via Page.createIsolatedWorld (contextId)", async () => {
  reset();
  // A same-process frame (in the top session's frame tree) → isolated-world context.
  const out = await OPS.upload({ tabId: TAB_ID, frame: "civitai.com/embed/w",
    selector: "#f", path: ABS_PATH });
  assert.ok(cdpMethods().includes("Page.createIsolatedWorld"), "same-process frame uses an isolated world");
  assert.ok(!cdpMethods().includes("Target.setAutoAttach"), "no OOPIF auto-attach for a same-process frame");
  // The querySelector eval ran in the isolated-world contextId (99).
  const ev = state.calls.cdp.find((c) => c.method === "Runtime.evaluate");
  assert.equal(ev.params.contextId, 99);
  assert.equal(state.calls.setFile.length, 1);
  assert.equal(out.url, "https://civitai.com/embed/w");
});

test("upload not_a_file_input: a non-file element is refused; NOTHING is set", async () => {
  reset();
  state.isFileInput = false;   // the resolved element is not an <input type=file>
  await assert.rejects(() => OPS.upload({ tabId: TAB_ID, selector: "#notfile", path: ABS_PATH }),
    /not_a_file_input:#notfile/);
  assert.equal(state.calls.setFile.length, 0, "no file is set for a non-file input");
  assert.equal(state.calls.detach.length, 1, "still detaches");
});

test("upload element_not_found: a selector matching nothing is a clear error; NOTHING is set", async () => {
  reset();
  state.evalResult = { result: { type: "object", subtype: "null", value: null } };  // no objectId
  await assert.rejects(() => OPS.upload({ tabId: TAB_ID, selector: "#missing", path: ABS_PATH }),
    /element_not_found:#missing/);
  assert.equal(state.calls.setFile.length, 0);
  assert.equal(state.calls.detach.length, 1);
});

test("upload --frame unresolvable OOPIF: frame_not_found, NOTHING set (never a silent success)", async () => {
  reset();
  // getAllFrames still has frame 7 (resolveFrame ok) but auto-attach announces a
  // DIFFERENT target → no session matches → frame_not_found before any setFile.
  state.autoAttachTargets = [{ sessionId: "SID_OTHER", url: "https://unrelated.example/" }];
  await assert.rejects(() => OPS.upload({ tabId: TAB_ID, frame: "7", selector: "#f", path: ABS_PATH }),
    /frame_not_found/);
  assert.equal(state.calls.setFile.length, 0);
  assert.equal(state.calls.detach.length, 1);
});

test("SECURITY: upload attaches ONLY to the op's own tab; a FIXED typed CDP method set", async () => {
  reset();
  await OPS.upload({ tabId: TAB_ID, frame: "model-benchmarking.civit.ai", selector: "#f", path: ABS_PATH });
  assert.ok(state.calls.attach.every((t) => t.tabId === TAB_ID), "own-tab-only attach (#187)");
  assert.ok(state.calls.detach.every((t) => t.tabId === TAB_ID || t.tabId === undefined));
  assert.ok(state.calls.getAllFrames.every((t) => t === TAB_ID), "frame enum scoped to the op's tab");
  // Only the fixed typed CDP methods — no arbitrary raw-CDP passthrough surface.
  const allowed = new Set(["Page.getFrameTree", "Page.createIsolatedWorld",
    "Target.setAutoAttach", "Runtime.evaluate", "Runtime.callFunctionOn",
    "DOM.setFileInputFiles"]);
  for (const m of cdpMethods()) assert.ok(allowed.has(m), `unexpected CDP method: ${m}`);
});

test("#189: a HUNG Runtime.evaluate settles with cdp_timeout and STILL detaches — no SW wedge", async () => {
  reset();
  globalThis.BROWSER_BRIDGE_CDP_TIMEOUTS = { attachMs: 60, commandMs: 20, budgetMs: 200 };
  state.hangEvaluate = true;
  const started = Date.now();
  await assert.rejects(() => OPS.upload({ tabId: TAB_ID, selector: "#f", path: ABS_PATH }),
    /cdp_timeout/);
  assert.ok(Date.now() - started < 4000, "settles by the tiny budget, not the 20s server timeout");
  assert.equal(state.calls.setFile.length, 0);
  assert.equal(state.calls.detach.length, 1, "a hung upload still best-effort detaches");
});
