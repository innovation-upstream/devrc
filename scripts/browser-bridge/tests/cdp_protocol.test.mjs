// Tests for the CDP (chrome.debugger) pure decision layer in extension/protocol.js.
// These prove the security-relevant invariants WITHOUT a real browser (no chrome.*):
// strict attach scope (refuse BEFORE attach), always-detach, and the frame/key/coord
// math + typed read-expression builders the thin service_worker.js glue relies on.
//
// The chrome.debugger side effects themselves genuinely need a real Brave and are
// verified manually (see the PR body / SKILL.md live-check steps).

import test from "node:test";
import assert from "node:assert/strict";
import {
  CDP_VERSION, CDP_ATTACHABLE_SCHEMES, cdpSchemeOf, isCdpAttachableUrl,
  assertCdpAttachable, withCdpSession, flattenFrameTree,
  keyEventParams, KEY_EVENTS, clickPoint, boxModelOrigin, frameEvalExpressions,
  isCdpSyntaxError, cdpExceptionText, frameHtmlExpression, frameTextExpression,
  elementRectExpression, focusExpression, fullPageClip,
  promiseWithTimeout, assertTabCdpReady, TAB_DISCARDED_MESSAGE,
  CDP_ATTACH_TIMEOUT_MS, CDP_COMMAND_TIMEOUT_MS, CDP_OP_BUDGET_MS,
  matchCdpFrameId, pickOopifSessionId, evalValueOrThrow,
} from "../extension/protocol.js";

// A promise that NEVER settles — models a hung chrome.debugger call (the wedge).
const NEVER = () => new Promise(() => {});
// Tiny, injected budgets so the timeout tests settle in milliseconds and PROVE
// they bound by the (tiny) budget, NOT the 20s server cmd_timeout.
const FAST = { attachMs: 20, commandMs: 20, budgetMs: 60 };

test("CDP_VERSION is the stable 1.3 protocol channel", () => {
  assert.equal(CDP_VERSION, "1.3");
  assert.deepEqual([...CDP_ATTACHABLE_SCHEMES], ["http:", "https:"]);
});

test("cdpSchemeOf extracts a lowercased scheme; junk → ''", () => {
  assert.equal(cdpSchemeOf("HTTPS://X.test/y"), "https:");
  assert.equal(cdpSchemeOf("chrome://newtab"), "chrome:");
  assert.equal(cdpSchemeOf("not a url"), "");
});

test("isCdpAttachableUrl: only real web pages (http/https) are attachable", () => {
  assert.equal(isCdpAttachableUrl("https://civitai.com/x"), true);
  assert.equal(isCdpAttachableUrl("http://127.0.0.1:3000"), true);
  // Privileged / non-web surfaces the debugger must NEVER touch.
  for (const u of ["chrome://newtab", "brave://settings", "about:blank",
                   "chrome-extension://abc/opt.html", "devtools://devtools/x",
                   "file:///etc/passwd", "view-source:https://x", "data:text/html,x",
                   "", "not a url", undefined]) {
    assert.equal(isCdpAttachableUrl(u), false, `must refuse ${String(u)}`);
  }
});

test("assertCdpAttachable throws (naming the scheme) for a privileged tab", () => {
  assert.doesNotThrow(() => assertCdpAttachable("https://civitai.com"));
  assert.throws(() => assertCdpAttachable("chrome://newtab"), /cdp_attach_refused:chrome:/);
  assert.throws(() => assertCdpAttachable("file:///x"), /cdp_attach_refused:file:/);
  assert.throws(() => assertCdpAttachable(""), /cdp_attach_refused:<no-scheme>/);
});

// --- withCdpSession lifecycle (the always-detach + refuse-before-attach core) -- //
test("withCdpSession: a privileged url is REFUSED before any attach", async () => {
  let attached = false, detached = false;
  await assert.rejects(
    withCdpSession({
      url: "chrome://newtab",
      attach: async () => { attached = true; },
      detach: async () => { detached = true; },
      run: async () => "should-not-run",
    }),
    /cdp_attach_refused/);
  assert.equal(attached, false, "attach must NEVER be called for a refused url");
  assert.equal(detached, false, "detach must not run when we never attached");
});

test("withCdpSession: attaches then ALWAYS detaches on success", async () => {
  const seq = [];
  const out = await withCdpSession({
    url: "https://x.test",
    attach: async () => seq.push("attach"),
    detach: async () => seq.push("detach"),
    run: async () => { seq.push("run"); return "RESULT"; },
  });
  assert.equal(out, "RESULT");
  assert.deepEqual(seq, ["attach", "run", "detach"]);
});

test("withCdpSession: ALWAYS detaches even when the op throws (no leaked attach)", async () => {
  const seq = [];
  await assert.rejects(
    withCdpSession({
      url: "https://x.test",
      attach: async () => seq.push("attach"),
      detach: async () => seq.push("detach"),
      run: async () => { seq.push("run"); throw new Error("op_boom"); },
    }),
    /op_boom/);
  assert.deepEqual(seq, ["attach", "run", "detach"],
    "detach must run on the error path so no attach leaks");
});

test("withCdpSession: a detach failure is swallowed (never masks the result)", async () => {
  const out = await withCdpSession({
    url: "https://x.test",
    attach: async () => {},
    detach: async () => { throw new Error("already_detached"); },
    run: async () => "OK",
  });
  assert.equal(out, "OK", "a detach error must not mask a successful op result");
});

// --- frame enumeration + resolution ---------------------------------------- //
test("flattenFrameTree flattens depth-first, main-frame first, metadata only", () => {
  const tree = {
    frame: { id: "MAIN", url: "https://civitai.com/", name: "" },
    childFrames: [
      { frame: { id: "F1", url: "https://model-benchmarking.civit.ai/app", name: "bench" },
        childFrames: [
          { frame: { id: "F1a", url: "https://ads.example/x", name: "ad" } },
        ] },
      { frame: { id: "F2", url: "https://other.civit.ai/y", name: "" } },
    ],
  };
  assert.deepEqual(flattenFrameTree(tree), [
    { frameId: "MAIN", url: "https://civitai.com/", name: "", parentId: null },
    { frameId: "F1", url: "https://model-benchmarking.civit.ai/app", name: "bench", parentId: "MAIN" },
    { frameId: "F1a", url: "https://ads.example/x", name: "ad", parentId: "F1" },
    { frameId: "F2", url: "https://other.civit.ai/y", name: "", parentId: "MAIN" },
  ]);
});

// --- trusted input math ---------------------------------------------------- //
test("keyEventParams maps known keys + aliases; refuses unknown (bounded set)", () => {
  assert.equal(keyEventParams("Enter").keyCode, 13);
  assert.equal(keyEventParams("Enter").text, "\r");
  assert.equal(keyEventParams("Tab").keyCode, 9);
  assert.equal(keyEventParams("esc").key, "Escape");          // alias
  assert.equal(keyEventParams("RETURN").key, "Enter");        // alias, case-insensitive
  assert.equal(keyEventParams("arrowdown").key, "ArrowDown"); // ci canonical
  assert.equal(KEY_EVENTS.Tab.text, undefined, "a non-printable key carries no text");
  assert.throws(() => keyEventParams("F13"), /unknown_key:F13/);
  assert.throws(() => keyEventParams(""), /unknown_key:<none>/);
});

test("clickPoint centers the rect + offsets by the frame origin", () => {
  // Top frame (no offset): center of a 100x40 box at (10,20) → (60,40).
  assert.deepEqual(clickPoint({ x: 10, y: 20, width: 100, height: 40 }), { x: 60, y: 40 });
  // Sub-frame: add the iframe's on-page origin (200,300).
  assert.deepEqual(
    clickPoint({ x: 10, y: 20, width: 100, height: 40 }, { x: 200, y: 300 }),
    { x: 260, y: 340 });
});

test("boxModelOrigin extracts the content quad's top-left; junk → 0,0", () => {
  assert.deepEqual(boxModelOrigin({ content: [200, 300, 500, 300, 500, 700, 200, 700] }),
    { x: 200, y: 300 });
  assert.deepEqual(boxModelOrigin({}), { x: 0, y: 0 });
  assert.deepEqual(boxModelOrigin(null), { x: 0, y: 0 });
});

// --- frame eval + read/probe expression builders --------------------------- //
test("frameEvalExpressions builds expression + statement fallback forms", () => {
  const { expression, fallback } = frameEvalExpressions("document.title");
  assert.equal(expression, "(function(){ return (document.title) })()");
  assert.equal(fallback, "(function(){ document.title })()");
});

test("isCdpSyntaxError detects a SyntaxError exceptionDetails (className or text)", () => {
  assert.equal(isCdpSyntaxError({ exception: { className: "SyntaxError" } }), true);
  assert.equal(isCdpSyntaxError({ text: "Uncaught SyntaxError: bad" }), true);
  assert.equal(isCdpSyntaxError({ exception: { className: "TypeError" } }), false);
  assert.equal(isCdpSyntaxError(null), false);
});

test("cdpExceptionText pulls a human message from exceptionDetails", () => {
  assert.equal(cdpExceptionText({ exception: { description: "TypeError: x is undefined" } }),
    "TypeError: x is undefined");
  assert.equal(cdpExceptionText({ text: "Uncaught" }), "Uncaught");
  assert.equal(cdpExceptionText(null), "eval_failed");
});

test("frame read/probe expressions select DOM state as expected", () => {
  assert.equal(frameHtmlExpression(), "document.documentElement.outerHTML");
  // text passes the selector as the IIFE arg (JSON-escaped) — not inlined.
  assert.match(frameTextExpression("main #x"), /\}\)\("main #x"\)$/);
  assert.match(frameTextExpression("main #x"), /s\?document\.querySelector\(s\)/);
  assert.match(frameTextExpression(""), /\}\)\(""\)$/);
  const rectExpr = elementRectExpression("#go");
  assert.match(rectExpr, /getBoundingClientRect/);
  assert.match(rectExpr, /scrollIntoView/);
  assert.match(rectExpr, /\}\)\("#go"\)$/);       // selector is the IIFE arg
  assert.match(focusExpression("#f"), /\.focus\(\)/);
  assert.match(focusExpression("#f"), /\}\)\("#f"\)$/);
});

test("frame expression builders are injection-safe (JSON-escaped selector)", () => {
  const expr = elementRectExpression('a[href="x"]');
  assert.ok(expr.includes('a[href=\\"x\\"]'), "the selector quote must be escaped");
  // The built expression must remain valid JS (the quote can't break out early).
  assert.doesNotThrow(() => new Function(`return ${expr}`));
});

test("fullPageClip uses the css content size for a full-document capture", () => {
  const clip = fullPageClip({ cssContentSize: { width: 1200, height: 5400.6 } });
  assert.deepEqual(clip, { x: 0, y: 0, width: 1200, height: 5401, scale: 1 });
  assert.equal(fullPageClip({ contentSize: { width: 800, height: 600 } }).width, 800);
  assert.deepEqual(fullPageClip({}), { x: 0, y: 0, width: 0, height: 0, scale: 1 });
});

// --- SW-side CDP timeouts: a hung chrome.debugger call must NOT wedge the SW --- //
// Root cause fixed here: a chrome.debugger.attach / sendCommand / detach that never
// resolves (a discarded background tab has no renderer) left the SW's command
// handler blocked on an unresolved await forever → the /poll loop never resumed →
// the instance dropped. Every CDP call is now raced against a bounded timeout so
// the op SETTLES and control returns to the poll loop.

test("timeout budgets are chosen well under the 20s server cmd_timeout", () => {
  for (const ms of [CDP_ATTACH_TIMEOUT_MS, CDP_COMMAND_TIMEOUT_MS, CDP_OP_BUDGET_MS]) {
    assert.ok(ms > 0 && ms < 20000, `budget ${ms} must be >0 and < the 20s server timeout`);
  }
});

test("promiseWithTimeout: a hung promise rejects with cdp_timeout:<label> (settles, not hangs)", async () => {
  await assert.rejects(promiseWithTimeout(NEVER(), 10, "attach"), /^Error: cdp_timeout:attach$/);
  // A promise that settles on its own wins the race unchanged (value + error).
  assert.equal(await promiseWithTimeout(Promise.resolve(7), 1000, "x"), 7);
  await assert.rejects(promiseWithTimeout(Promise.reject(new Error("boom")), 1000, "x"), /boom/);
  // ms<=0 disables the bound (returns the value as-is, no timer).
  assert.equal(await promiseWithTimeout(Promise.resolve(3), 0, "x"), 3);
});

test("withCdpSession: a HUNG attach rejects with cdp_timeout within the attach budget — no hang, best-effort detach", async () => {
  let ran = false, detached = false;
  const started = Date.now();
  await assert.rejects(withCdpSession({
    url: "https://x.test",
    timeouts: FAST,
    attach: NEVER,                              // attach never resolves
    detach: async () => { detached = true; },
    send: async () => ({}),
    run: (send) => { ran = true; return send("Page.enable"); },
  }), /cdp_timeout:attach/);
  assert.equal(ran, false, "run must NOT start when attach never resolved");
  assert.equal(detached, true, "a timed-out attach must still best-effort detach (no leaked session)");
  assert.ok(Date.now() - started < 5000, "must settle by the tiny budget, not the 20s server timeout");
});

test("withCdpSession: a HUNG CDP command (e.g. Page.getFrameTree) rejects bounded + always detaches", async () => {
  let detached = false;
  await assert.rejects(withCdpSession({
    url: "https://x.test",
    timeouts: FAST,
    attach: async () => {},
    detach: async () => { detached = true; },
    send: NEVER,                                // Page.getFrameTree never resolves
    run: (send) => send("Page.getFrameTree"),
  }), /cdp_timeout:Page\.getFrameTree/);
  assert.equal(detached, true, "detach must run so no debugger session leaks after a hung command");
});

test("NO-WEDGE: a hung CDP op settles so the very NEXT op is processed (the core regression)", async () => {
  // Op 1 hangs in a command → it must SETTLE (reject) and detach, returning control.
  let detach1 = false;
  await assert.rejects(withCdpSession({
    url: "https://x.test", timeouts: FAST,
    attach: async () => {}, detach: async () => { detach1 = true; },
    send: NEVER, run: (send) => send("Page.getFrameTree"),
  }), /cdp_timeout/);
  assert.equal(detach1, true);
  // Op 2, issued immediately after, runs normally — proving op 1 did NOT block the
  // handler (in production this is the /poll loop continuing to the next command).
  const out = await withCdpSession({
    url: "https://x.test", timeouts: FAST,
    attach: async () => {}, detach: async () => {},
    send: async (m) => ({ ran: m }), run: (send) => send("Page.enable"),
  });
  assert.deepEqual(out, { ran: "Page.enable" }, "the next op must be processed after a hung one");
});

test("withCdpSession: a HUNG detach cannot re-wedge the finally — the op still settles", async () => {
  const started = Date.now();
  const out = await withCdpSession({
    url: "https://x.test",
    timeouts: FAST,
    attach: async () => {},
    detach: NEVER,                              // detach itself hangs
    send: async () => "OK",
    run: (send) => send("Page.captureScreenshot"),
  });
  assert.equal(out, "OK", "a hung detach must not block the op's already-computed result");
  assert.ok(Date.now() - started < 5000, "the bounded detach lets the handler return quickly");
});

test("withCdpSession: the overall op BUDGET backstops a run that never settles", async () => {
  await assert.rejects(withCdpSession({
    url: "https://x.test",
    timeouts: FAST,
    attach: async () => {},
    detach: async () => {},
    send: async () => ({}),
    run: NEVER,                                 // run resolves to a never-settling promise
  }), /cdp_timeout:op/);
});

test("withCdpSession: per-command timeout wraps the send handed to run", async () => {
  // The `send` given to run is the WRAPPED one — a slow command trips commandMs.
  await assert.rejects(withCdpSession({
    url: "https://x.test",
    timeouts: { attachMs: 50, commandMs: 15, budgetMs: 500 },
    attach: async () => {}, detach: async () => {},
    send: NEVER, run: (send) => send("DOM.getBoxModel"),
  }), /cdp_timeout:DOM\.getBoxModel/);
});

// --- discarded / unloaded tab (the probable root cause) --------------------- //
test("assertTabCdpReady: a discarded/unloaded tab fails FAST (no attach on a dead renderer)", () => {
  assert.throws(() => assertTabCdpReady({ discarded: true }),
    new RegExp("tab_discarded"));
  assert.throws(() => assertTabCdpReady({ status: "unloaded" }), /tab_discarded/);
  assert.throws(() => assertTabCdpReady(null), /owned_tab_gone/);
  // A live tab passes (any non-discarded status incl. loading/complete/undefined).
  assert.doesNotThrow(() => assertTabCdpReady({ discarded: false, status: "complete" }));
  assert.doesNotThrow(() => assertTabCdpReady({ status: "loading" }));
  assert.doesNotThrow(() => assertTabCdpReady({}));
  assert.ok(/reload the tab|foreground/.test(TAB_DISCARDED_MESSAGE),
    "the message must tell the caller how to recover");
});

test("withCdpSession still enforces security: a privileged url is REFUSED before attach (unchanged)", async () => {
  let attached = false;
  await assert.rejects(withCdpSession({
    url: "chrome://newtab", timeouts: FAST,
    attach: async () => { attached = true; },
    detach: async () => {}, send: async () => ({}), run: (s) => s("Page.enable"),
  }), /cdp_attach_refused/);
  assert.equal(attached, false, "the timeout wiring must not weaken the attach-scope invariant");
});

// --- CDP `eval --frame` frame-context resolution (the #190 null fix) --------- //
// The decision layer for running an arbitrary JS STRING in a target frame via CDP
// Runtime.evaluate: locate a SAME-PROCESS frame in the top session's frame tree by url,
// or an OOPIF flat session by url, and interpret the evaluate reply under the
// never-silent-null contract. All pure — the SW supplies the chrome.debugger effects.

test("matchCdpFrameId: finds a SAME-PROCESS frame's CDP id by url; an OOPIF (absent) → null", () => {
  const tree = {
    frame: { id: "MAIN", url: "https://civitai.com/apps/run/model-benchmarking", name: "" },
    childFrames: [
      { frame: { id: "SAME1", url: "https://civitai.com/embed/widget", name: "w" } },
    ],
  };
  // top frame (frameId 0 maps to the top url) → the main CDP frame id.
  assert.equal(matchCdpFrameId(tree, "https://civitai.com/apps/run/model-benchmarking"), "MAIN");
  // a same-process child by exact url.
  assert.equal(matchCdpFrameId(tree, "https://civitai.com/embed/widget"), "SAME1");
  // the cross-origin OOPIF is NOT in the top session's frame tree → null (take the
  // auto-attach path). This is the whole reason getFrameTree can't reach it.
  assert.equal(matchCdpFrameId(tree, "https://model-benchmarking.civit.ai/"), null);
  assert.equal(matchCdpFrameId(tree, ""), null);
  assert.equal(matchCdpFrameId(null, "https://x/"), null);
});

test("pickOopifSessionId: matches the auto-attached OOPIF target session by url (trailing-slash tolerant)", () => {
  const attached = [
    { sessionId: "S_ad", url: "https://ads.example/pixel" },
    { sessionId: "S_bench", url: "https://model-benchmarking.civit.ai/" },
  ];
  assert.equal(pickOopifSessionId(attached, "https://model-benchmarking.civit.ai/"), "S_bench");
  // frame url without the trailing slash still matches the target url that has one.
  assert.equal(pickOopifSessionId(attached, "https://model-benchmarking.civit.ai"), "S_bench");
  assert.equal(pickOopifSessionId(attached, "https://ads.example/pixel"), "S_ad");
  assert.equal(pickOopifSessionId(attached, "https://not-attached/"), null);
  assert.equal(pickOopifSessionId([], "https://x/"), null);
  assert.equal(pickOopifSessionId(null, "https://x/"), null);
});

test("evalValueOrThrow: NEVER-SILENT-NULL — exception → frame_eval_failed; real null/undefined pass AS values", () => {
  // A real value passes through.
  assert.equal(evalValueOrThrow({ result: { value: 1234 } }), 1234);
  assert.equal(evalValueOrThrow({ result: { value: "https://model-benchmarking.civit.ai/" } }),
    "https://model-benchmarking.civit.ai/");
  // A GENUINE null/undefined result is a legitimate value (distinct from a failure).
  assert.equal(evalValueOrThrow({ result: { value: null } }), null);
  assert.equal(evalValueOrThrow({ result: { value: undefined } }), undefined);
  assert.equal(evalValueOrThrow({}), undefined);                 // bare handle → undefined
  // An exceptionDetails is a FAILURE TO EXECUTE → a clear op error, NOT value:null.
  assert.throws(() => evalValueOrThrow({
    result: { type: "object" },
    exceptionDetails: { exception: { description: "TypeError: x is not a function" } },
  }), /frame_eval_failed:TypeError: x is not a function/);
  assert.throws(() => evalValueOrThrow({ exceptionDetails: { text: "Uncaught" } }),
    /frame_eval_failed:Uncaught/);
});
