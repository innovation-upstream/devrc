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
  assertCdpAttachable, withCdpSession, flattenFrameTree, resolveFrame,
  keyEventParams, KEY_EVENTS, clickPoint, boxModelOrigin, frameEvalExpressions,
  isCdpSyntaxError, cdpExceptionText, frameHtmlExpression, frameTextExpression,
  elementRectExpression, focusExpression, fullPageClip,
} from "../extension/protocol.js";

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

test("resolveFrame: exact frameId wins; else a url substring; else not_found", () => {
  const frames = [
    { frameId: "MAIN", url: "https://civitai.com/", name: "" },
    { frameId: "F1", url: "https://model-benchmarking.civit.ai/app", name: "bench" },
  ];
  assert.equal(resolveFrame(frames, "F1").frameId, "F1");                 // exact id
  assert.equal(resolveFrame(frames, "model-benchmarking").frameId, "F1"); // substring
  assert.equal(resolveFrame(frames, "CIVIT.AI").frameId, "F1");           // case-insensitive
  assert.throws(() => resolveFrame(frames, "nope"), /frame_not_found:nope/);
  assert.throws(() => resolveFrame(frames, ""), /frame_not_specified/);
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
