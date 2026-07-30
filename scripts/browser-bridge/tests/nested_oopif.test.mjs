// Nested-OOPIF `eval --frame` / `upload --frame` (#211).
//
// THE DEFECT (confirmed live against a 3-domain loopback rig — see
// tests/fixtures/oopif-rig/README.md): `Target.setAutoAttach({flatten})` is NOT
// recursive — sent on the TAB's top session it auto-attaches only DIRECT child targets.
// So a GRANDCHILD cross-origin iframe never produced an `attachedToTarget` on the top
// session and `eval --frame <grandchild>` failed
// `frame_not_found:http://127.0.0.1.nip.io:8901/leaf.html` (fails SAFE — but the
// capability was missing; `text --frame` reached the same frame fine via
// chrome.scripting).
//
// THE FIX: `resolveOopifSession` (pure, protocol.js) re-arms `Target.setAutoAttach` ON
// each attached CHILD session so the cascade walks DOWN the frame tree — HARD-BOUNDED by
// a depth cap, a total-target cap, a quiet-window settle and a hard wait ceiling, with
// AMBIGUITY failing loud. It is the ONE resolver both `eval --frame` and the OOPIF branch
// of `upload --frame` call.
//
// Two layers here:
//   * PURE — resolveOopifSession / pickOopifSessionId / matchOopifSessions with injected
//     send + listener + clock (no chrome, no browser);
//   * GLUE — the real OPS.eval / OPS.upload against a mocked chrome.debugger whose
//     setAutoAttach announces children PER SESSION (top→mid, mid→leaf), i.e. exactly the
//     non-recursive CDP behaviour that caused the bug.

import test from "node:test";
import assert from "node:assert/strict";

import {
  resolveOopifSession, pickOopifSessionId, matchOopifSessions,
  OOPIF_MAX_DEPTH, OOPIF_MAX_TARGETS, OOPIF_AUTO_ATTACH_PARAMS,
} from "../extension/protocol.js";

// The live rig's three registrable sites (three renderer processes / three targets).
const TOP_URL = "http://127.0.0.1:8901/top.html";
const MID_URL = "http://lvh.me:8901/mid.html";
const LEAF_URL = "http://127.0.0.1.nip.io:8901/leaf.html";

// Tiny bounds so the cap/timeout cases settle in milliseconds, not seconds.
const FAST = { settleMs: 30, waitMs: 300, pollMs: 5 };

// --------------------------------------------------------------------------- //
// A fake CDP cascade. `tree` maps a session key ("top" for the tab's top session,
// else a sessionId) → the child targets Chrome would announce when setAutoAttach is
// sent ON that session. Announcements are per-session — which is precisely why the old
// single-level code could not see a grandchild.
function makeCascade(tree, opts = {}) {
  const listeners = new Set();
  const sent = [];
  const announce = (parentSessionId) => {
    const kids = tree[parentSessionId == null ? "top" : parentSessionId] || [];
    for (const k of kids) {
      for (const fn of [...listeners]) {
        fn({ tabId: 5, sessionId: parentSessionId },
           "Target.attachedToTarget",
           { sessionId: k.sessionId, targetInfo: { url: k.url, type: "iframe" } });
      }
    }
  };
  const send = async (method, params, sessionId) => {
    sent.push({ method, params, sessionId });
    if (method !== "Target.setAutoAttach") return {};
    if (opts.deliverAfterMs) setTimeout(() => announce(sessionId), opts.deliverAfterMs);
    else announce(sessionId);
    return {};
  };
  return {
    sent, listeners, send,
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
  };
}
const resolve = (rig, targetUrl, limits) => resolveOopifSession({
  send: rig.send, targetUrl,
  addListener: rig.addListener, removeListener: rig.removeListener,
  limits: { ...FAST, ...(limits || {}) },
});
const autoAttachSessions = (rig) =>
  rig.sent.filter((c) => c.method === "Target.setAutoAttach").map((c) => c.sessionId);

// --------------------------------------------------------------------------- //
// PURE: the recursive resolver
// --------------------------------------------------------------------------- //

test("REGRESSION — a DIRECT child OOPIF still resolves at level 1 (no needless descend)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }],
                            S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL }] });
  assert.equal(await resolve(rig, MID_URL), "S_MID");
  // Exactly ONE setAutoAttach, on the TOP session — the pre-existing single-level path
  // is unchanged for a direct child; we never descend once the match is in hand.
  assert.deepEqual(autoAttachSessions(rig), [undefined]);
  assert.equal(rig.listeners.size, 0, "listener always removed");
});

test("GRANDCHILD OOPIF resolves through TWO attach levels (the #211 fix)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }],
                            S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL }] });
  assert.equal(await resolve(rig, LEAF_URL), "S_LEAF");
  // The SECOND setAutoAttach MUST be sent ON the child's sessionId — that is the whole
  // fix (setAutoAttach is not recursive; flat mode forwards the sessionId).
  assert.deepEqual(autoAttachSessions(rig), [undefined, "S_MID"]);
  assert.equal(rig.listeners.size, 0);
});

test("great-grandchild (depth 3) resolves; the cascade descends level by level", async () => {
  const rig = makeCascade({
    top: [{ sessionId: "S1", url: "https://a.test/" }],
    S1: [{ sessionId: "S2", url: "https://b.test/" }],
    S2: [{ sessionId: "S3", url: "https://c.test/" }],
  });
  assert.equal(await resolve(rig, "https://c.test/"), "S3");
  assert.deepEqual(autoAttachSessions(rig), [undefined, "S1", "S2"]);
});

test("auto-attach params are flat + never waitForDebuggerOnStart (would PAUSE the page)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }] });
  await resolve(rig, MID_URL);
  assert.deepEqual(OOPIF_AUTO_ATTACH_PARAMS,
    { autoAttach: true, flatten: true, waitForDebuggerOnStart: false });
  for (const c of rig.sent) assert.deepEqual(c.params, OOPIF_AUTO_ATTACH_PARAMS);
});

test("DEPTH CAP: descending stops at maxDepth and fails LOUD (oopif_depth_cap), no loop", async () => {
  const rig = makeCascade({
    top: [{ sessionId: "S1", url: "https://a.test/" }],
    S1: [{ sessionId: "S2", url: "https://b.test/" }],
    S2: [{ sessionId: "S3", url: LEAF_URL }],
  });
  await assert.rejects(() => resolve(rig, LEAF_URL, { maxDepth: 2 }), /oopif_depth_cap:2/);
  // We attached the top + the ONE session at depth 1; the depth-2 session was NOT
  // descended into (that is the cap doing its job).
  assert.deepEqual(autoAttachSessions(rig), [undefined, "S1"]);
  assert.equal(rig.listeners.size, 0, "listener removed even when a cap threw");
});

test("TARGET CAP: a frame-spamming page is cut off with oopif_target_cap (bounded work)", async () => {
  // A hostile top page announcing many sibling targets, none of them the wanted frame.
  const many = Array.from({ length: 12 }, (_, i) =>
    ({ sessionId: `S${i}`, url: `https://spam${i}.test/` }));
  const rig = makeCascade({ top: many });
  await assert.rejects(() => resolve(rig, LEAF_URL, { maxTargets: 5 }), /oopif_target_cap:5/);
  assert.equal(rig.listeners.size, 0);
});

test("a match already in hand WINS over a cap hit in the same batch (no false cap error)", async () => {
  const rig = makeCascade({
    top: [{ sessionId: "S_WANT", url: LEAF_URL },
          ...Array.from({ length: 9 }, (_, i) => ({ sessionId: `X${i}`, url: `https://s${i}.test/` }))],
  });
  assert.equal(await resolve(rig, LEAF_URL, { maxTargets: 3 }), "S_WANT");
});

test("AMBIGUITY fails loud: two attached sessions with the SAME url → ambiguous_frame", async () => {
  // Realistic under nesting: the same widget/pixel embedded at two places in the tree.
  const rig = makeCascade({
    top: [{ sessionId: "S_A", url: MID_URL }, { sessionId: "S_B", url: MID_URL }],
  });
  await assert.rejects(() => resolve(rig, MID_URL),
    /ambiguous_frame:2 \[S_A:http:\/\/lvh\.me:8901\/mid\.html, S_B:http:\/\/lvh\.me:8901\/mid\.html\]/);
  assert.equal(rig.listeners.size, 0);
});

test("PROPAGATION TIMEOUT: events that never arrive → null (caller → frame_not_found), bounded", async () => {
  const rig = makeCascade({});   // setAutoAttach resolves; NOTHING is ever announced
  const t0 = Date.now();
  assert.equal(await resolve(rig, LEAF_URL, { settleMs: 40, waitMs: 200, pollMs: 5 }), null);
  assert.ok(Date.now() - t0 < 3000, "must settle by the tiny bound, not hang");
  assert.equal(rig.listeners.size, 0);
});

test("ASYNC propagation: an attachedToTarget arriving AFTER setAutoAttach resolved is still caught", async () => {
  // The real CDP contract: setAutoAttach's REPLY does not mean its events have landed.
  // The single-level code assumed it had; the resolver waits (bounded) instead.
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }],
                            S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL }] },
                          { deliverAfterMs: 40 });
  assert.equal(await resolve(rig, LEAF_URL, { settleMs: 120, waitMs: 2000, pollMs: 5 }),
    "S_LEAF");
});

test("a hard-capped cascade never exceeds the wait ceiling even while events keep arriving", async () => {
  // A page that keeps announcing NEW nested targets forever — the ceiling must win.
  const listeners = new Set();
  let n = 0;
  const rig = {
    listeners,
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
    sent: [],
    send: async (method, params, sessionId) => {
      rig.sent.push({ method, params, sessionId });
      const id = `N${n++}`;
      for (const fn of [...listeners]) {
        fn({ tabId: 5, sessionId }, "Target.attachedToTarget",
           { sessionId: id, targetInfo: { url: `https://noise${id}.test/` } });
      }
      return {};
    },
  };
  const t0 = Date.now();
  // Whichever cap trips first, the descend MUST stop and fail loud — never spin.
  await assert.rejects(() => resolve(rig, LEAF_URL, { maxTargets: 8, waitMs: 500 }),
    /oopif_(depth|target)_cap:\d+/);
  assert.ok(Date.now() - t0 < 3000, "bounded — never an unbounded descend loop");
  // The work done is bounded by the caps, not by how much the page spams.
  assert.ok(rig.sent.length <= 9, `bounded auto-attach count, got ${rig.sent.length}`);
  assert.equal(listeners.size, 0);
});

test("production caps are the documented, deliberately small bounds", () => {
  assert.equal(OOPIF_MAX_DEPTH, 5);
  assert.equal(OOPIF_MAX_TARGETS, 50);
});

// --------------------------------------------------------------------------- //
// PURE: matching / ambiguity helpers
// --------------------------------------------------------------------------- //

test("matchOopifSessions: exact tier wins; trailing-slash tier only as a fallback; dedup by sessionId", () => {
  const attached = [
    { sessionId: "S1", url: "https://x.test/a" },
    { sessionId: "S1", url: "https://x.test/a" },     // duplicate announcement
    { sessionId: "S2", url: "https://x.test/a/" },
  ];
  // An exact match exists → the tolerant candidate is NOT mixed in (so no false ambiguity).
  assert.deepEqual(matchOopifSessions(attached, "https://x.test/a").map((a) => a.sessionId), ["S1"]);
  assert.deepEqual(matchOopifSessions(attached, "https://x.test/a/").map((a) => a.sessionId), ["S2"]);
  // No exact match → the trailing-slash-tolerant tier picks BOTH (→ ambiguous, loud).
  assert.deepEqual(
    matchOopifSessions([{ sessionId: "S1", url: "https://y.test/" }], "https://y.test").map((a) => a.sessionId),
    ["S1"]);
  assert.deepEqual(matchOopifSessions([], "https://y.test/"), []);
  assert.deepEqual(matchOopifSessions(null, "https://y.test/"), []);
  assert.deepEqual(matchOopifSessions([{ sessionId: "S", url: "x" }], ""), []);
});

test("pickOopifSessionId: single match → id, none → null, MULTIPLE → ambiguous_frame (never first-match)", () => {
  const one = [{ sessionId: "S_bench", url: "https://model-benchmarking.civit.ai/" }];
  assert.equal(pickOopifSessionId(one, "https://model-benchmarking.civit.ai/"), "S_bench");
  assert.equal(pickOopifSessionId(one, "https://nope.test/"), null);
  assert.throws(() => pickOopifSessionId(
    [{ sessionId: "A", url: "https://dup.test/" }, { sessionId: "B", url: "https://dup.test/" }],
    "https://dup.test/"), /ambiguous_frame:2 \[A:https:\/\/dup\.test\/, B:https:\/\/dup\.test\/\]/);
});

// --------------------------------------------------------------------------- //
// GLUE: OPS.eval / OPS.upload through the real service worker, mocked chrome
// --------------------------------------------------------------------------- //

const TAB_ID = 5;
// The TOP session's Page.getFrameTree — same-process frames only. Neither the mid nor
// the leaf OOPIF is here (that is why the CDP target path exists at all).
const FRAME_TREE = { frame: { id: "CDP_MAIN", url: TOP_URL, name: "" }, childFrames: [] };

// Per-session announcements: top → mid, mid → leaf. This models the NON-RECURSIVE
// setAutoAttach that the old code tripped over.
const CASCADE = {
  top: [{ sessionId: "SID_MID", url: MID_URL }],
  SID_MID: [{ sessionId: "SID_LEAF", url: LEAF_URL }],
};

const state = {
  frames: [
    { frameId: 0, parentFrameId: -1, url: TOP_URL },
    { frameId: 2140, parentFrameId: 0, url: MID_URL },
    { frameId: 2141, parentFrameId: 2140, url: LEAF_URL },
  ],
  tab: { id: TAB_ID, url: TOP_URL, title: "rig", active: false, status: "complete", windowId: 1 },
  cascade: CASCADE,
  evalReply: { result: { value: null } },
  calls: { cdp: [], attach: [], detach: [], executeScript: [] },
};
function reset() {
  state.calls = { cdp: [], attach: [], detach: [], executeScript: [] };
  state.cascade = CASCADE;
  state.evalReply = { result: { value: null } };
  globalThis.BROWSER_BRIDGE_OOPIF_LIMITS = { ...FAST };
}

const evtListeners = new Set();
globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  webNavigation: { async getAllFrames() { return state.frames; } },
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
        const kids = state.cascade[target.sessionId == null ? "top" : target.sessionId] || [];
        for (const k of kids) {
          for (const fn of evtListeners) {
            fn({ tabId: target.tabId, sessionId: target.sessionId }, "Target.attachedToTarget",
               { sessionId: k.sessionId, targetInfo: { url: k.url, type: "iframe" } });
          }
        }
        return {};
      }
      if (method === "Runtime.evaluate") return state.evalReply;
      if (method === "Runtime.callFunctionOn") return { result: { value: true } };
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
const cdpCalls = (m) => state.calls.cdp.filter((c) => c.method === m);
const lastEval = () => [...state.calls.cdp].reverse().find((c) => c.method === "Runtime.evaluate");

test("eval --frame <GRANDCHILD>: resolves via the two-level cascade and evaluates in the LEAF session", async () => {
  reset();
  state.evalReply = { result: { value: "grandchild-reached" } };   // window.RIG_SECRET
  const out = await OPS.eval({ tabId: TAB_ID, frame: "2141", js: "window.RIG_SECRET" });
  // The second setAutoAttach went ON the mid session — the fix, end to end.
  assert.deepEqual(cdpCalls("Target.setAutoAttach").map((c) => c.sessionId), [undefined, "SID_MID"]);
  assert.equal(lastEval().sessionId, "SID_LEAF", "evaluated in the GRANDCHILD's flat session");
  assert.equal(lastEval().params.contextId, undefined);
  assert.equal(out.value, "grandchild-reached");
  assert.equal(out.url, LEAF_URL, "reports the frame's OWN url");
  assert.equal(state.calls.detach.length, 1, "always detaches");
  assert.equal(evtListeners.size, 0, "onEvent listener always removed");
});

test("eval --frame <DIRECT child>: unchanged single-level resolution (regression guard)", async () => {
  reset();
  state.evalReply = { result: { value: MID_URL } };
  const out = await OPS.eval({ tabId: TAB_ID, frame: "2140", js: "location.href" });
  assert.deepEqual(cdpCalls("Target.setAutoAttach").map((c) => c.sessionId), [undefined],
    "a direct child needs exactly ONE top-session auto-attach — no extra descend");
  assert.equal(lastEval().sessionId, "SID_MID");
  assert.equal(out.value, MID_URL);
  assert.equal(state.calls.detach.length, 1);
});

test("eval --frame: a frame that never attaches → frame_not_found:<url> (the live error shape)", async () => {
  reset();
  state.cascade = {};    // nothing is ever announced
  await assert.rejects(() => OPS.eval({ tabId: TAB_ID, frame: "2141", js: "1" }),
    /^Error: frame_not_found:http:\/\/127\.0\.0\.1\.nip\.io:8901\/leaf\.html$/);
  assert.equal(cdpCalls("Runtime.evaluate").length, 0, "never evaluates when unresolved");
  assert.equal(state.calls.detach.length, 1, "still detaches");
  assert.equal(evtListeners.size, 0);
});

test("eval --frame: the DEPTH CAP surfaces as a clear op error and STILL detaches", async () => {
  reset();
  globalThis.BROWSER_BRIDGE_OOPIF_LIMITS = { ...FAST, maxDepth: 1 };
  await assert.rejects(() => OPS.eval({ tabId: TAB_ID, frame: "2141", js: "1" }),
    /oopif_depth_cap:1/);
  assert.equal(state.calls.detach.length, 1);
  assert.equal(evtListeners.size, 0);
});

test("upload --frame <GRANDCHILD>: the SAME shared resolver routes setFileInputFiles into the leaf", async () => {
  reset();
  state.evalReply = { result: { objectId: "OBJ_1" } };
  const out = await OPS.upload({ tabId: TAB_ID, frame: "2141", selector: "input[type=file]",
                                 path: "/home/zach/x.png" });
  assert.deepEqual(cdpCalls("Target.setAutoAttach").map((c) => c.sessionId), [undefined, "SID_MID"]);
  const setFiles = cdpCalls("DOM.setFileInputFiles");
  assert.equal(setFiles.length, 1);
  assert.equal(setFiles[0].sessionId, "SID_LEAF", "the file is set in the GRANDCHILD's session");
  assert.deepEqual(setFiles[0].params.files, ["/home/zach/x.png"]);
  assert.deepEqual(out.files, ["x.png"], "only the basename returns");
  assert.equal(state.calls.detach.length, 1);
  assert.equal(evtListeners.size, 0);
});

test("SECURITY: the nested cascade adds NO new CDP surface and stays own-tab only", async () => {
  reset();
  state.evalReply = { result: { value: 1 } };
  await OPS.eval({ tabId: TAB_ID, frame: "2141", js: "1" });
  const allowed = new Set(["Page.getFrameTree", "Page.createIsolatedWorld",
                           "Target.setAutoAttach", "Runtime.evaluate"]);
  for (const c of state.calls.cdp) assert.ok(allowed.has(c.method), `unexpected CDP method: ${c.method}`);
  assert.ok(state.calls.attach.every((t) => t.tabId === TAB_ID), "own-tab attach only");
  assert.ok(state.calls.detach.every((t) => t.tabId === TAB_ID || t.tabId === undefined));
  // The eval NEVER goes through chrome.scripting frame injection (only the top-frame
  // visibility probe uses executeScript).
  assert.ok(state.calls.executeScript.every((c) => !(c.target && c.target.frameIds)));
});
