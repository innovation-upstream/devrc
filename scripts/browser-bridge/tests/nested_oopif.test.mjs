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
  OOPIF_AUTO_ATTACH_PARAMS_NOFILTER, OOPIF_TARGET_TYPES, formatCascadeTrace,
} from "../extension/protocol.js";

// The live rig's three registrable sites (three renderer processes / three targets).
const TOP_URL = "http://127.0.0.1:8901/top.html";
const MID_URL = "http://127.0.0.1.sslip.io:8901/mid.html";
const LEAF_URL = "http://127.0.0.1.nip.io:8901/leaf.html";

// Tiny bounds so the cap/timeout cases settle in milliseconds, not seconds.
const FAST = { settleMs: 30, waitMs: 300, pollMs: 5 };
const TAB = 5;
const OTHER_TAB = 99;

// --------------------------------------------------------------------------- //
// A fake CDP cascade. `tree` maps a session key ("top" for the tab's top session,
// else a sessionId) → the child targets Chrome would announce when setAutoAttach is
// sent ON that session. Announcements are per-session — which is precisely why the old
// single-level code could not see a grandchild.
//
// A child entry is {sessionId, url} plus OPTIONAL adversarial overrides: `type` (default
// "iframe") and `tabId` (default this op's tab) — so a test can model a page minting a
// worker target, a privileged-scheme child, or an event from another tab.
function makeCascade(tree, opts = {}) {
  const listeners = new Set();
  const sent = [];
  const announce = (parentSessionId) => {
    const kids = tree[parentSessionId == null ? "top" : parentSessionId] || [];
    for (const k of kids) {
      // Source shape. `subNoTabId` models the SUSPECTED live Chrome behaviour: an event
      // for a SUB-session carries only `sessionId`, no `tabId` — which is what would
      // make an over-strict tabId check silently kill the whole cascade at level 2.
      const src = {};
      if (!(opts.subNoTabId && parentSessionId != null)) {
        src.tabId = k.tabId === undefined ? TAB : k.tabId;
      }
      if (parentSessionId != null) src.sessionId = parentSessionId;
      for (const fn of [...listeners]) {
        fn(src,
           "Target.attachedToTarget",
           { sessionId: k.sessionId,
             targetInfo: { url: k.url, type: k.type === undefined ? "iframe" : k.type,
                           targetId: `T_${k.sessionId}` } });
      }
    }
  };
  const send = async (method, params, sessionId) => {
    sent.push({ method, params, sessionId });
    if (method !== "Target.setAutoAttach") return {};
    if (opts.rejectFilter && params && params.filter) throw new Error("Invalid parameters");
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
const resolve = (rig, targetUrl, limits, over) => resolveOopifSession({
  send: rig.send, targetUrl, tabId: TAB,
  addListener: rig.addListener, removeListener: rig.removeListener,
  limits: { ...FAST, ...(limits || {}) },
  ...(over || {}),
});
const autoAttachSessions = (rig) =>
  rig.sent.filter((c) => c.method === "Target.setAutoAttach").map((c) => c.sessionId);

// The resolver THROWS `frame_not_found:<label> cascade[…]` rather than returning null,
// so every failure carries the bounded diagnostic. Returns the message for inspection.
async function notFound(rig, targetUrl, limits, over) {
  let msg = null;
  try { await resolve(rig, targetUrl, limits, over); }
  catch (e) { msg = String(e.message); }
  assert.ok(msg, "must reject — the resolver never returns a silent null");
  assert.match(msg, /^frame_not_found:/);
  return msg;
}

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

test("auto-attach params are flat, iframe-FILTERED, never waitForDebuggerOnStart (would PAUSE the page)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }] });
  await resolve(rig, MID_URL);
  assert.deepEqual(OOPIF_AUTO_ATTACH_PARAMS, {
    autoAttach: true, flatten: true, waitForDebuggerOnStart: false,
    filter: [{ type: "iframe" }],
  });
  assert.deepEqual(OOPIF_AUTO_ATTACH_PARAMS_NOFILTER,
    { autoAttach: true, flatten: true, waitForDebuggerOnStart: false });
  for (const c of rig.sent) assert.deepEqual(c.params, OOPIF_AUTO_ATTACH_PARAMS);
});

test("the EXPERIMENTAL `filter` param FAILS SOFT: rejected → retried without it, same result", async () => {
  // Target.setAutoAttach's `filter` is experimental and may be rejected on the pinned
  // 1.3 channel. It must never be able to break the whole OOPIF path — the listener-side
  // type check is the authoritative control.
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }],
                            S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL }] },
                          { rejectFilter: true });
  assert.equal(await resolve(rig, LEAF_URL), "S_LEAF");
  const attempts = rig.sent.filter((c) => c.method === "Target.setAutoAttach");
  assert.ok(attempts[0].params.filter, "first attempt carries the filter");
  assert.equal(attempts[1].params.filter, undefined, "retried WITHOUT the filter");
  // Once it has fallen back it stays fallen back for the rest of the op (no re-probing).
  for (const a of attempts.slice(1)) assert.equal(a.params.filter, undefined);
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
    /ambiguous_frame:2 \[S_A:http:\/\/127\.0\.0\.1\.sslip\.io:8901\/mid\.html, S_B:http:\/\/127\.0\.0\.1\.sslip\.io:8901\/mid\.html\]/);
  assert.equal(rig.listeners.size, 0);
});

test("PROPAGATION TIMEOUT: events that never arrive → null (caller → frame_not_found), bounded", async () => {
  const rig = makeCascade({});   // setAutoAttach resolves; NOTHING is ever announced
  const t0 = Date.now();
  const msg = await notFound(rig, LEAF_URL, { settleMs: 40, waitMs: 200, pollMs: 5 });
  assert.match(msg, /exit=settle/);
  assert.match(msg, /\(no events observed\)/, "the diagnostic says plainly that NOTHING arrived");
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

test("THE HARD CEILING is a real wall: a page keeping the descend queue non-empty still stops at waitMs", async () => {
  // The Fix-5 regression. An earlier revision checked the deadline ONLY when `pending`
  // was empty, so a page that always had another child to descend into never reached it
  // and the real wall became CDP_OP_BUDGET_MS (surfacing as cdp_timeout:op, not the
  // clean op error the docs promise). Here: every setAutoAttach announces ONE new
  // never-matching child, so `pending` is NEVER empty and the settle window is NEVER
  // reached — only the ceiling can end this. maxTargets is set high so it cannot be the
  // thing that stops us, which is what distinguishes ceiling from cap.
  const listeners = new Set();
  let n = 0;
  const rig = {
    listeners, sent: [],
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
    send: async (method, params, sessionId) => {
      rig.sent.push({ method, sessionId });
      const id = `C${n++}`;
      for (const fn of [...listeners]) {
        fn({ tabId: TAB, sessionId }, "Target.attachedToTarget",
           { sessionId: id, targetInfo: { url: `https://chain${id}.test/`, type: "iframe" } });
      }
      await new Promise((r) => setTimeout(r, 2));   // each descend costs a little time
      return {};
    },
  };
  const t0 = Date.now();
  // maxDepth/maxTargets are BOTH generous — neither cap can trip before the ceiling.
  const msg = await notFound(rig, LEAF_URL,
    { maxDepth: 1000, maxTargets: 100000, settleMs: 10_000, waitMs: 120, pollMs: 5 });
  const elapsed = Date.now() - t0;
  assert.match(msg, /exit=deadline/, "the ceiling — NOT a cap and NOT the settle window");
  assert.ok(elapsed >= 100, `must actually reach the ceiling, took ${elapsed}ms`);
  assert.ok(elapsed < 2000, `must STOP at the ceiling, took ${elapsed}ms`);
  assert.equal(listeners.size, 0);
});

test("a hard-capped cascade never spins even while events keep arriving (target cap trips)", async () => {
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
        fn({ tabId: TAB, sessionId }, "Target.attachedToTarget",
           { sessionId: id, targetInfo: { url: `https://noise${id}.test/`, type: "iframe" } });
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
  assert.deepEqual([...OOPIF_TARGET_TYPES], ["iframe"]);
});

// --------------------------------------------------------------------------- //
// ADVERSARIAL: the page is HOSTILE. The cascade removed the old implicit
// "one level down from a URL-validated tab" boundary, so these prove the explicit
// replacement (own-tab / target-type / attachable-scheme) actually holds.
// --------------------------------------------------------------------------- //

test("HOSTILE: non-iframe targets (worker/service_worker/page/browser) are IGNORED entirely", async () => {
  const rig = makeCascade({ top: [
    { sessionId: "S_W", url: "https://a.test/w.js", type: "worker" },
    { sessionId: "S_SW", url: "https://a.test/sw.js", type: "service_worker" },
    { sessionId: "S_P", url: "https://a.test/popup", type: "page" },
    { sessionId: "S_B", url: "https://a.test/b", type: "browser" },
    { sessionId: "S_SHARED", url: "https://a.test/shared.js", type: "shared_worker" },
  ] });
  // None of them is selectable...
  const msg = await notFound(rig, "https://a.test/w.js");
  assert.match(msg, /drop:type type=worker/, "the diagnostic names WHY each was dropped");
  // ...and none of them is DESCENDED into (only the top-session auto-attach happened).
  assert.deepEqual(rig.sent.filter((c) => c.method === "Target.setAutoAttach")
    .map((c) => c.sessionId), [undefined]);
});

test("HOSTILE: `new Worker(location.href)` — a worker target whose url EQUALS the frame's must not shadow it", async () => {
  // The DoS + wrong-context vector: one line of JS in any cross-origin frame mints a
  // target with an IDENTICAL url. Unfiltered that makes the frame permanently
  // un-eval-able (forced ambiguous_frame) and, after a navigation race, could route the
  // operator's JS into a WORKER global.
  const rig = makeCascade({ top: [
    { sessionId: "S_FRAME", url: LEAF_URL, type: "iframe" },
    { sessionId: "S_WORKER", url: LEAF_URL, type: "worker" },
  ] });
  // Resolves cleanly to the REAL FRAME — no ambiguity, no worker.
  assert.equal(await resolve(rig, LEAF_URL), "S_FRAME");
});

test("HOSTILE: a worker impersonating the frame CANNOT deny service by forcing ambiguous_frame", async () => {
  const rig = makeCascade({ top: [
    { sessionId: "S_FRAME", url: MID_URL, type: "iframe" },
    ...Array.from({ length: 5 }, (_, i) =>
      ({ sessionId: `S_W${i}`, url: MID_URL, type: "worker" })),
  ] });
  assert.equal(await resolve(rig, MID_URL), "S_FRAME", "workers never enter the match set");
});

test("HOSTILE: a privileged-scheme child is REFUSED — the top-tab scheme gate is not bypassable one level down", async () => {
  // A hostile page embeds another extension's web_accessible_resource (or file:/
  // devtools:). getAllFrames lists it, so a prompt-injected agent CAN pass it to
  // `eval --frame` — running operator JS inside ANOTHER EXTENSION'S ORIGIN. Refused.
  for (const url of ["chrome-extension://abcdefghijklmnop/page.html",
                     "file:///home/zach/.ssh/id_ed25519",
                     "devtools://devtools/bundled/x.html",
                     "chrome://settings",
                     "about:blank",
                     "data:text/html,<script>1</script>",
                     "javascript:alert(1)"]) {
    const rig = makeCascade({ top: [{ sessionId: "S_PRIV", url, type: "iframe" }] });
    assert.match(await notFound(rig, url), /drop:scheme/, `must refuse ${url}`);
    // …and never descended into it either.
    assert.deepEqual(rig.sent.filter((c) => c.method === "Target.setAutoAttach")
      .map((c) => c.sessionId), [undefined], `must not descend into ${url}`);
  }
});

test("HOSTILE: a privileged child does not poison an otherwise-good cascade", async () => {
  const rig = makeCascade({
    top: [{ sessionId: "S_EXT", url: "chrome-extension://aaaa/x.html" },
          { sessionId: "S_MID", url: MID_URL }],
    S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL }],
  });
  assert.equal(await resolve(rig, LEAF_URL), "S_LEAF");
  assert.equal(rig.sent.filter((c) => c.method === "Target.setAutoAttach")
    .some((c) => c.sessionId === "S_EXT"), false, "never descends into the extension target");
});

test("OWN TAB: an attachedToTarget for ANOTHER tab is ignored (onEvent is a GLOBAL listener)", async () => {
  const rig = makeCascade({ top: [
    { sessionId: "S_OTHER", url: LEAF_URL, tabId: OTHER_TAB },
  ] });
  assert.match(await notFound(rig, LEAF_URL), /drop:foreign-tab/,
    "another tab's frame must never resolve");
});

test("OWN TAB: the right tab still resolves alongside a same-url decoy from another tab", async () => {
  const rig = makeCascade({ top: [
    { sessionId: "S_OTHER", url: LEAF_URL, tabId: OTHER_TAB },
    { sessionId: "S_MINE", url: LEAF_URL, tabId: TAB },
  ] });
  // No ambiguity: the foreign one never entered the set.
  assert.equal(await resolve(rig, LEAF_URL), "S_MINE");
});

test("OWN TAB: an omitted deps.tabId FAILS CLOSED (a tagged event can't match undefined)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }] });
  await assert.rejects(() => resolveOopifSession({
    send: rig.send, targetUrl: MID_URL,          // tabId deliberately omitted
    addListener: rig.addListener, removeListener: rig.removeListener,
    limits: FAST,
  }), /frame_not_found/, "a caller that forgets tabId gets NOTHING, never everything");
});

// --------------------------------------------------------------------------- //
// OWNERSHIP FALLBACK — the fix for the FIRST LIVE FAILURE.
// Live (Brave, 2026-07-30, verified-fresh extension): depth 1 resolved, depth 2+ ALWAYS
// returned frame_not_found and NEVER oopif_depth_cap — i.e. no level-2 session was ever
// recorded, so the cascade was INERT, not capped. Prime suspect: sub-session
// attachedToTarget events carry no `source.tabId`, so the strict own-tab check dropped
// every one of them. Ownership now falls back to SESSION PARENTAGE, which preserves the
// invariant without depending on Chrome populating `tabId`.
// --------------------------------------------------------------------------- //

test("LIVE REPRO: sub-session events with NO tabId still resolve a GRANDCHILD (parentage fallback)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }],
                            S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL }] },
                          { subNoTabId: true });
  // Under the strict-tabId rule this returned frame_not_found with attach=[top,S_MID] —
  // exactly the live symptom. The level-2 event is now accepted because its
  // source.sessionId is a session THIS cascade attached.
  assert.equal(await resolve(rig, LEAF_URL), "S_LEAF");
  assert.deepEqual(autoAttachSessions(rig), [undefined, "S_MID"]);
});

test("LIVE REPRO: the 7-level deep rig shape resolves to the documented cap with no tabId on sub-events", async () => {
  const tree = { top: [{ sessionId: "S1", url: "https://d1.test/" }] };
  for (let i = 1; i <= 5; i++) {
    tree[`S${i}`] = [{ sessionId: `S${i + 1}`, url: `https://d${i + 1}.test/` }];
  }
  const rig = makeCascade(tree, { subNoTabId: true });
  // depth 5 is AT the cap and resolves…
  assert.equal(await resolve(rig, "https://d5.test/"), "S5");
  // …depth 6 is PAST it and is refused LOUDLY (not silently not-found) — the outcome
  // Check B of the live rig expects.
  const rig2 = makeCascade(tree, { subNoTabId: true });
  await assert.rejects(() => resolve(rig2, "https://d6.test/"), /oopif_depth_cap:5/);
});

test("OWNERSHIP: an event with NEITHER tabId nor a known parent sessionId is REJECTED", async () => {
  const listeners = new Set();
  const rig = {
    listeners, sent: [],
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
    send: async (method, params, sessionId) => {
      rig.sent.push({ method, sessionId });
      for (const fn of [...listeners]) {
        fn({}, "Target.attachedToTarget",     // no tabId, no sessionId — unprovable
           { sessionId: "S_GHOST", targetInfo: { url: LEAF_URL, type: "iframe" } });
      }
      return {};
    },
  };
  assert.match(await notFound(rig, LEAF_URL), /drop:unowned/);
});

test("OWNERSHIP: an UNKNOWN parent sessionId is rejected even without a tabId (no blanket trust)", async () => {
  // The fallback must not degrade into "anything with a sessionId is ours".
  const listeners = new Set();
  const rig = {
    listeners, sent: [],
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
    send: async (method, params, sessionId) => {
      rig.sent.push({ method, sessionId });
      for (const fn of [...listeners]) {
        fn({ sessionId: "S_SOMEONE_ELSES" }, "Target.attachedToTarget",
           { sessionId: "S_X", targetInfo: { url: LEAF_URL, type: "iframe" } });
      }
      return {};
    },
  };
  const msg = await notFound(rig, LEAF_URL);
  assert.match(msg, /drop:unowned/);
  assert.match(msg, /parent=unknown/);
});

test("OWNERSHIP: a present-but-FOREIGN tabId still loses, even on a sub-session event", async () => {
  // tabId, when present, stays AUTHORITATIVE — the fallback only covers its absence.
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }],
                            S_MID: [{ sessionId: "S_LEAF", url: LEAF_URL, tabId: OTHER_TAB }] });
  const msg = await notFound(rig, LEAF_URL);
  assert.match(msg, /drop:foreign-tab/);
});

// --------------------------------------------------------------------------- //
// DIAGNOSTICS — the readout that makes the next live run evidence, not guesswork.
// --------------------------------------------------------------------------- //

test("DIAGNOSTIC: a failure reports exit reason, attach chain, event provenance and depths", async () => {
  const rig = makeCascade({ top: [
    { sessionId: "S_MID", url: MID_URL },
    { sessionId: "S_W", url: LEAF_URL, type: "worker" },
    { sessionId: "S_EXT", url: "chrome-extension://aaaa/x.html" },
    { sessionId: "S_FOREIGN", url: LEAF_URL, tabId: OTHER_TAB },
  ] });
  const msg = await notFound(rig, "https://never.test/");
  // Header: which exit fired, which sessions we auto-attached, counts, filter mode, caps.
  assert.match(msg, /cascade\[exit=(settle|deadline) attach=top>S_MID events=4 accepted=1 filter=on caps=d5\/t50\]/);
  // Per-event provenance, including WHY each was dropped.
  assert.match(msg, /#1 accept type=iframe tab=match parent=absent d=1 http:\/\/127\.0\.0\.1\.sslip\.io:8901\/mid\.html/);
  assert.match(msg, /drop:type type=worker/);
  assert.match(msg, /drop:scheme/);
  assert.match(msg, /drop:foreign-tab type=iframe tab=foreign/);
});

test("DIAGNOSTIC: the trace is CAPPED so a frame-spamming page cannot blow up the error", async () => {
  const many = Array.from({ length: 60 }, (_, i) =>
    ({ sessionId: `S${i}`, url: `https://spam${i}.test/`, type: "worker" }));
  const rig = makeCascade({ top: many });
  const msg = await notFound(rig, "https://never.test/");
  const rows = msg.match(/#\d+ drop:/g) || [];
  assert.equal(rows.length, 20, "at most OOPIF_TRACE_MAX entries are rendered");
  assert.match(msg, /\(\+40 more events not shown\)/, "but the true count is still reported");
  assert.ok(msg.length < 4000, `error must stay compact, was ${msg.length}`);
});

test("DIAGNOSTIC: formatCascadeTrace is pure and handles the empty case", () => {
  assert.match(
    formatCascadeTrace({ exit: "settle", eventsSeen: 0, trace: [], attachSent: [],
                         filterMode: "on", accepted: 0, maxDepth: 5, maxTargets: 50 }),
    /^cascade\[exit=settle attach=- events=0 accepted=0 filter=on caps=d5\/t50\] \(no events observed\)$/);
});

test("DIAGNOSTIC: a filter rejection is recorded in the readout (so it can't hide)", async () => {
  const rig = makeCascade({ top: [{ sessionId: "S_MID", url: MID_URL }] },
                          { rejectFilter: true });
  assert.match(await notFound(rig, "https://never.test/"), /filter=rejected→off/);
});

test("DEGRADATION (Fix 6): an UNTAGGED source.sessionId loses the DEPTH bound — it does not fake a cap", async () => {
  // Depth attribution assumes Chrome tags a sub-session event's source with the parent
  // sessionId. If it does NOT, every target is attributed depth 1, so `depthCapHit` is
  // NEVER set and OOPIF_MAX_DEPTH stops binding — the descent is then bounded only by
  // maxTargets/waitMs. This pins the REAL behaviour (an earlier PR-body claim that it
  // degrades to a spurious depth cap was backwards).
  const listeners = new Set();
  const chain = { top: ["S1"], S1: ["S2"], S2: ["S3"], S3: ["S4"] };
  const urls = { S1: "https://l1.test/", S2: "https://l2.test/",
                 S3: "https://l3.test/", S4: LEAF_URL };
  const rig = {
    listeners, sent: [],
    addListener: (fn) => listeners.add(fn),
    removeListener: (fn) => listeners.delete(fn),
    send: async (method, params, sessionId) => {
      rig.sent.push({ method, sessionId });
      for (const id of chain[sessionId == null ? "top" : sessionId] || []) {
        for (const fn of [...listeners]) {
          // NOTE: source carries NO sessionId — the degraded case.
          fn({ tabId: TAB }, "Target.attachedToTarget",
             { sessionId: id, targetInfo: { url: urls[id], type: "iframe" } });
        }
      }
      return {};
    },
  };
  // maxDepth 2 would stop a CORRECTLY-attributed cascade before S4 (depth 4). Untagged,
  // every target reads as depth 1 → we descend the whole chain and RESOLVE past the cap.
  assert.equal(await resolve(rig, LEAF_URL, { maxDepth: 2 }), "S4",
    "untagged source ⇒ the depth cap silently stops binding (bounded only by maxTargets/waitMs)");
  // Selection is still by URL alone, so the frame reached is the one asked for — the
  // degradation costs the DEPTH BOUND, never frame correctness.
});

test("depth is attributed from source.sessionId when Chrome DOES tag it (the assumed case)", async () => {
  const rig = makeCascade({
    top: [{ sessionId: "S1", url: "https://l1.test/" }],
    S1: [{ sessionId: "S2", url: "https://l2.test/" }],
    S2: [{ sessionId: "S3", url: "https://l3.test/" }],
    S3: [{ sessionId: "S4", url: LEAF_URL }],
  });
  // The mirror of the test above: tagged ⇒ maxDepth 2 DOES bind and S4 is unreachable.
  await assert.rejects(() => resolve(rig, LEAF_URL, { maxDepth: 2 }), /oopif_depth_cap:2/);
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
  const one = [{ sessionId: "S_bench", url: "https://model-benchmarking.example.test/" }];
  assert.equal(pickOopifSessionId(one, "https://model-benchmarking.example.test/"), "S_bench");
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

const FRAMES = [
  { frameId: 0, parentFrameId: -1, url: TOP_URL },
  { frameId: 2140, parentFrameId: 0, url: MID_URL },
  { frameId: 2141, parentFrameId: 2140, url: LEAF_URL },
];

const state = {
  frames: FRAMES,
  tab: { id: TAB_ID, url: TOP_URL, title: "rig", active: false, status: "complete", windowId: 1 },
  cascade: CASCADE,
  noise: [],          // adversarial extra targets announced on the TOP session
  evalReply: { result: { value: null } },
  calls: { cdp: [], attach: [], detach: [], executeScript: [] },
};
function reset() {
  state.calls = { cdp: [], attach: [], detach: [], executeScript: [] };
  state.cascade = CASCADE;
  state.noise = [];
  state.frames = FRAMES;
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
        const kids = [...(state.cascade[target.sessionId == null ? "top" : target.sessionId] || []),
                      ...(target.sessionId == null ? state.noise : [])];
        for (const k of kids) {
          for (const fn of evtListeners) {
            fn({ tabId: k.tabId === undefined ? target.tabId : k.tabId,
                 sessionId: target.sessionId },
               "Target.attachedToTarget",
               { sessionId: k.sessionId,
                 targetInfo: { url: k.url, type: k.type === undefined ? "iframe" : k.type } });
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
    /^Error: frame_not_found:http:\/\/127\.0\.0\.1\.nip\.io:8901\/leaf\.html cascade\[/);
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

test("SECURITY (end-to-end, HOSTILE page): worker / foreign-tab / privileged decoys are all rejected", async () => {
  reset();
  // The page mints, all carrying the WANTED grandchild's url: a worker (the DoS +
  // wrong-context vector), a same-url target belonging to ANOTHER tab, and a
  // web-accessible resource of another extension. Every one must be ignored, and the op
  // must still land in the real grandchild frame.
  state.noise = [
    { sessionId: "SID_WORKER", url: LEAF_URL, type: "worker" },
    { sessionId: "SID_SW", url: LEAF_URL, type: "service_worker" },
    { sessionId: "SID_FOREIGN", url: LEAF_URL, tabId: 4242 },
    { sessionId: "SID_EXT", url: "chrome-extension://aaaabbbbcccc/page.html" },
  ];
  state.evalReply = { result: { value: "grandchild-reached" } };
  const out = await OPS.eval({ tabId: TAB_ID, frame: "2141", js: "window.RIG_SECRET" });
  assert.equal(out.value, "grandchild-reached");
  // NOT ambiguous (the decoys never entered the match set) and evaluated in the REAL frame.
  assert.equal(lastEval().sessionId, "SID_LEAF");
  // No decoy was ever descended into.
  const descended = cdpCalls("Target.setAutoAttach").map((c) => c.sessionId);
  for (const bad of ["SID_WORKER", "SID_SW", "SID_FOREIGN", "SID_EXT"]) {
    assert.equal(descended.includes(bad), false, `must never descend into ${bad}`);
  }
  assert.deepEqual(descended, [undefined, "SID_MID"]);
});

test("DOC-PINNED GAP (Fix 4): a numeric --frame id does NOT disambiguate duplicate-URL OOPIFs", async () => {
  reset();
  // Two genuinely distinct iframes with the SAME url (a duplicated ad/widget slot).
  // The caller supplies an unambiguous NUMERIC frameId — but the CDP resolver matches
  // purely by frame.url, so the numeric id cannot break the tie. This test EXISTS to pin
  // the documented limitation honestly (SKILL.md/README say the id does not help here),
  // not to bless it; the parent-chain follow-up is filed in the PR body.
  state.frames = [
    { frameId: 0, parentFrameId: -1, url: TOP_URL },
    { frameId: 3001, parentFrameId: 0, url: MID_URL },
    { frameId: 3002, parentFrameId: 0, url: MID_URL },
  ];
  state.cascade = { top: [{ sessionId: "SID_A", url: MID_URL },
                          { sessionId: "SID_B", url: MID_URL }] };
  await assert.rejects(() => OPS.eval({ tabId: TAB_ID, frame: "3002", js: "1" }),
    /ambiguous_frame:2 \[SID_A:.*, SID_B:.*\]/);
  assert.equal(cdpCalls("Runtime.evaluate").length, 0, "fails BEFORE running anything");
  assert.equal(state.calls.detach.length, 1);
  state.frames = FRAMES;   // restore for any later test
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
