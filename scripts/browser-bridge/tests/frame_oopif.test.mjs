// Tests for the OOPIF-capable frame layer in extension/protocol.js — the fix for
// the cross-origin (out-of-process) iframe gap. Two pure, browser-independent parts:
//
//  1. Enumeration/resolution over a chrome.webNavigation.getAllFrames() result
//     (numeric frameIds, INCLUDING cross-origin OOPIFs — the exact frames CDP
//     Page.getFrameTree omitted).
//  2. The SELF-CONTAINED page functions injected via chrome.scripting.executeScript
//     into the resolved frame. They reference only their args + page globals, so we
//     exercise them directly against a tiny hand-rolled DOM (no jsdom dep).
//
// The chrome.* side effects (getAllFrames / executeScript) are covered by
// tests/service_worker.test.mjs against a mocked chrome; the real cross-origin
// injection is verified manually (see the PR body / SKILL.md live-check).

import test from "node:test";
import assert from "node:assert/strict";
import {
  normalizeWebNavFrames, resolveWebNavFrameId, resolveWebNavFrame,
  frameReadHtmlFn, frameReadTextFn, frameEvalFn,
  frameClickFn, frameTypeFn, frameKeyFn, keyEventParams,
} from "../extension/protocol.js";

// A getAllFrames() result for civitai.com/apps/run/model-benchmarking: the top frame
// PLUS a CROSS-ORIGIN child (model-benchmarking.example.test) that runs OUT-OF-PROCESS —
// the frame the old CDP getFrameTree could NOT see.
const GET_ALL_FRAMES = [
  { frameId: 0, parentFrameId: -1, url: "https://civitai.com/apps/run/model-benchmarking", documentId: "d0" },
  { frameId: 7, parentFrameId: 0, url: "https://model-benchmarking.example.test/", documentId: "d7" },
  { frameId: 12, parentFrameId: 7, url: "https://ads.example/pixel", documentId: "d12" },
];

// --- enumeration ----------------------------------------------------------- //
test("normalizeWebNavFrames maps getAllFrames → {frameId,url,parentFrameId} incl. the OOPIF", () => {
  const out = normalizeWebNavFrames(GET_ALL_FRAMES);
  assert.deepEqual(out, [
    { frameId: 0, url: "https://civitai.com/apps/run/model-benchmarking", parentFrameId: -1 },
    { frameId: 7, url: "https://model-benchmarking.example.test/", parentFrameId: 0 },
    { frameId: 12, url: "https://ads.example/pixel", parentFrameId: 7 },
  ]);
  // THE REGRESSION: the cross-origin child frame IS present (getFrameTree missed it).
  assert.ok(out.some((f) => f.url.includes("model-benchmarking.example.test")),
    "the cross-origin OOPIF must appear in the enumeration");
});

test("normalizeWebNavFrames is metadata-only + drops junk entries", () => {
  const out = normalizeWebNavFrames([
    { frameId: 0, url: "https://a/", parentFrameId: -1, documentId: "x", errorOccurred: false },
    { url: "https://no-id/" },          // no numeric frameId → dropped
    null,                                // junk → dropped
    { frameId: 3 },                      // missing url/parent → defaulted
  ]);
  assert.deepEqual(out, [
    { frameId: 0, url: "https://a/", parentFrameId: -1 },
    { frameId: 3, url: "", parentFrameId: -1 },
  ]);
  // Never any content/document fields — id/url/parent only.
  for (const f of out) {
    assert.deepEqual(Object.keys(f).sort(), ["frameId", "parentFrameId", "url"]);
  }
  assert.deepEqual(normalizeWebNavFrames(null), []);
});

// --- resolution ------------------------------------------------------------ //
test("resolveWebNavFrameId: exact numeric frameId wins; url substring; case-insensitive", () => {
  const frames = normalizeWebNavFrames(GET_ALL_FRAMES);
  // numeric frameId (string or number), incl. the top frame 0.
  assert.equal(resolveWebNavFrameId(frames, 7), 7);
  assert.equal(resolveWebNavFrameId(frames, "7"), 7);
  assert.equal(resolveWebNavFrameId(frames, "0"), 0);
  // url substring → the numeric id of the matching frame.
  assert.equal(resolveWebNavFrameId(frames, "model-benchmarking.example.test"), 7);
  assert.equal(resolveWebNavFrameId(frames, "MODEL-BENCHMARKING.EXAMPLE.TEST"), 7);
});

// --- Fix 3: host-preference + ambiguity (no silent wrong-frame) ------------- //
test("resolveWebNavFrame: a url substring prefers a HOST match over a top-frame PATH match", () => {
  const frames = normalizeWebNavFrames(GET_ALL_FRAMES);
  // THE civitai self-shadow: `model-benchmarking` appears in the TOP frame's PATH
  // (civitai.com/apps/run/model-benchmarking) AND the OOPIF's HOST
  // (model-benchmarking.example.test). The HOST match (the OOPIF, frame 7) is intended —
  // never the top path (frame 0), which the old first-match returned.
  assert.equal(resolveWebNavFrame(frames, "model-benchmarking").frameId, 7);
  assert.equal(resolveWebNavFrameId(frames, "model-benchmarking"), 7);
});

test("resolveWebNavFrame: a genuinely ambiguous substring → ambiguous_frame (not first-match)", () => {
  const frames = normalizeWebNavFrames(GET_ALL_FRAMES);
  // "https://" is in every frame's url and no frame's HOST → 3 path candidates,
  // ambiguous. Must error (listing the candidates) so the caller picks a numeric id,
  // NOT silently return the first frame.
  assert.throws(() => resolveWebNavFrame(frames, "https://"),
    /ambiguous_frame:3 \[/);
  // Two frames sharing a HOST substring are ambiguous too → force a numeric id.
  const dup = normalizeWebNavFrames([
    { frameId: 3, parentFrameId: 0, url: "https://dup.example/a" },
    { frameId: 4, parentFrameId: 0, url: "https://dup.example/b" },
  ]);
  assert.throws(() => resolveWebNavFrame(dup, "dup.example"), /ambiguous_frame:2 \[/);
  // …but the exact NUMERIC frameId still disambiguates cleanly.
  assert.equal(resolveWebNavFrame(dup, "4").frameId, 4);
});

test("resolveWebNavFrame: returns the FRAME OBJECT (id+url+parent) so the SW can report/locate the frame", () => {
  const frames = normalizeWebNavFrames(GET_ALL_FRAMES);
  // exact numeric id → the whole object (url is what the SW reports + matches in CDP).
  assert.deepEqual(resolveWebNavFrame(frames, "7"),
    { frameId: 7, url: "https://model-benchmarking.example.test/", parentFrameId: 0 });
  // url substring → the same object.
  assert.deepEqual(resolveWebNavFrame(frames, "model-benchmarking.example.test"),
    { frameId: 7, url: "https://model-benchmarking.example.test/", parentFrameId: 0 });
  // the top frame.
  assert.equal(resolveWebNavFrame(frames, "0").frameId, 0);
  // resolveWebNavFrameId delegates → same numeric id, never diverges.
  assert.equal(resolveWebNavFrameId(frames, "model-benchmarking.example.test"),
    resolveWebNavFrame(frames, "model-benchmarking.example.test").frameId);
  assert.throws(() => resolveWebNavFrame(frames, "nope"), /frame_not_found:nope/);
  assert.throws(() => resolveWebNavFrame(frames, ""), /frame_not_specified/);
});

test("resolveWebNavFrameId: unknown → frame_not_found; empty → frame_not_specified", () => {
  const frames = normalizeWebNavFrames(GET_ALL_FRAMES);
  assert.throws(() => resolveWebNavFrameId(frames, "nope"), /frame_not_found:nope/);
  assert.throws(() => resolveWebNavFrameId(frames, "999"), /frame_not_found:999/);
  assert.throws(() => resolveWebNavFrameId(frames, ""), /frame_not_specified/);
  assert.throws(() => resolveWebNavFrameId(frames, null), /frame_not_specified/);
  // Tab-scoped by construction: a frameId that only exists in ANOTHER tab's list is
  // simply absent here → frame_not_found (can't reach another tab's frame).
  assert.throws(() => resolveWebNavFrameId([], "7"), /frame_not_found:7/);
});

// --- injected page functions (self-contained; run inside the frame) --------- //
// A tiny DOM/event shim so the injected functions run under node exactly as they
// would in the page — proving the synthetic dispatch logic without a browser.
class FakeEvt { constructor(type, opts = {}) { this.type = type; Object.assign(this, opts); } }

function installDom() {
  globalThis.MouseEvent = class extends FakeEvt {};
  globalThis.KeyboardEvent = class extends FakeEvt {};
  globalThis.Event = class extends FakeEvt {};
  globalThis.window = {};
}
installDom();

function fakeEl(over = {}) {
  const el = {
    events: [], focusCalls: 0, clickCalls: 0, scrolled: false,
    value: "", isContentEditable: false,
    focus() { this.focusCalls++; },
    click() { this.clickCalls++; },
    scrollIntoView() { this.scrolled = true; },
    getBoundingClientRect() { return { left: 10, top: 20, width: 100, height: 40 }; },
    dispatchEvent(e) { this.events.push(e); return true; },
    ...over,
  };
  return el;
}

function fakeDoc({ map = {}, active = null, body = null } = {}) {
  globalThis.document = {
    documentElement: { outerHTML: "<html><body>frame</body></html>" },
    body: body || { innerText: "BODY TEXT", events: [], dispatchEvent() {} },
    activeElement: active,
    querySelector(sel) { return map[sel] || null; },
  };
  return globalThis.document;
}

test("frameReadHtmlFn returns the frame document's outerHTML", () => {
  fakeDoc();
  assert.equal(frameReadHtmlFn(), "<html><body>frame</body></html>");
});

test("frameReadTextFn reads a selector's innerText, else the body's", () => {
  fakeDoc({ map: { "#main": { innerText: "HELLO OOPIF" } } });
  assert.equal(frameReadTextFn("#main"), "HELLO OOPIF");
  assert.equal(frameReadTextFn(""), "BODY TEXT");
  assert.equal(frameReadTextFn("#missing"), "");   // selector matches nothing
});

test("frameEvalFn evaluates an expression (once), falls back to statements, propagates throws", () => {
  fakeDoc();
  assert.equal(frameEvalFn("2 * 21"), 42);
  assert.equal(frameEvalFn("[1,2,3].length"), 3);
  // statement form (unparseable as an expression) runs without a return value.
  assert.equal(frameEvalFn("var x = 1;"), undefined);
  // a runtime throw propagates (executeScript would reject → the op errors).
  assert.throws(() => frameEvalFn("(function(){ throw new Error('boom'); })()"), /boom/);
});

test("frameClickFn: scrolls, dispatches pointerdown/mousedown→pointerup/mouseup→ONE click; returns center", () => {
  const el = fakeEl();
  fakeDoc({ map: { "#go": el } });
  const res = frameClickFn("#go");
  assert.deepEqual(res, { ok: true, x: 60, y: 40 });  // center of 100x40 @ (10,20)
  assert.equal(el.scrolled, true);
  assert.deepEqual(el.events.map((e) => e.type),
    ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
  // exactly ONE dispatched `click` event…
  assert.equal(el.events.filter((e) => e.type === "click").length, 1);
  // …and el.click() is NOT also called (that was the 0→2 double-fire).
  assert.equal(el.clickCalls, 0, "must NOT also call el.click() — that double-fired the handler");
  assert.equal(el.events[0].clientX, 60);
  assert.equal(el.events[0].clientY, 40);
});

test("frameClickFn: one click op fires the target's click handler EXACTLY ONCE (0→1, not 0→2)", () => {
  // Reproduces the live-observed double-fire: a real element whose onclick increments a
  // counter. Model the DOM's click semantics: a dispatched `click` event AND el.click()
  // BOTH invoke the onclick handler (el.click() dispatches a click under the hood). With
  // the fix (one click event, no el.click()) the counter goes 0→1, not 0→2.
  let counter = 0;
  const onclick = () => { counter++; };
  const el = {
    scrolled: false, clickCalls: 0,
    scrollIntoView() { this.scrolled = true; },
    getBoundingClientRect() { return { left: 10, top: 20, width: 100, height: 40 }; },
    dispatchEvent(e) { if (e.type === "click") onclick(); return true; },
    click() { this.clickCalls++; onclick(); },   // el.click() ALSO runs the handler
  };
  fakeDoc({ map: { "#btn": el } });
  frameClickFn("#btn");
  assert.equal(counter, 1, "one click op must fire the onclick handler exactly once");
});

test("frameClickFn: a missing selector → {ok:false,error:element_not_found} (no throw)", () => {
  fakeDoc({ map: {} });
  assert.deepEqual(frameClickFn("#nope"), { ok: false, error: "element_not_found" });
});

test("frameTypeFn: focuses, sets .value, dispatches input+change; returns only the LENGTH", () => {
  const el = fakeEl();
  fakeDoc({ map: { "#in": el } });
  const res = frameTypeFn("#in", "hello");
  assert.deepEqual(res, { ok: true, typed: 5 });
  assert.equal(el.value, "hello");
  assert.equal(el.focusCalls, 1);
  assert.deepEqual(el.events.map((e) => e.type), ["input", "change"]);
  assert.ok(!("text" in res), "the typed text must never be returned");
});

test("frameTypeFn: empty selector types into the frame's (editable) activeElement", () => {
  const el = fakeEl();   // fakeEl has a settable `value` → editable
  fakeDoc({ active: el });
  const res = frameTypeFn("", "abc");
  assert.deepEqual(res, { ok: true, typed: 3 });
  assert.equal(el.value, "abc");
});

test("frameTypeFn: sets textContent for a contenteditable (no `value`)", () => {
  const el = {
    isContentEditable: true, textContent: "", focusCalls: 0, events: [],
    focus() { this.focusCalls++; },
    dispatchEvent(e) { this.events.push(e); },
  };
  fakeDoc({ map: { "#rte": el } });
  const res = frameTypeFn("#rte", "rich");
  assert.deepEqual(res, { ok: true, typed: 4 });
  assert.equal(el.textContent, "rich");
});

test("frameTypeFn: a given selector matching nothing → element_not_found", () => {
  fakeDoc({ map: {} });
  assert.deepEqual(frameTypeFn("#nope", "x"), { ok: false, error: "element_not_found" });
});

test("frameTypeFn: NO editable target (empty sel, activeElement is <body>) → no_editable_target, NOT false success", () => {
  // The #190 audit bug: empty selector + nothing focused → activeElement defaults to
  // <body> (no `value`, not contenteditable). The OLD code set nothing yet returned
  // {ok:true,typed:N} — a FALSE success. Now it's a clear error with NO `typed`.
  const body = { innerText: "BODY", events: [], dispatchEvent(e) { this.events.push(e); } };
  fakeDoc({ active: body, body });   // activeElement is the non-editable body
  const res = frameTypeFn("", "hello");
  assert.deepEqual(res, { ok: false, error: "no_editable_target" });
  assert.ok(!("typed" in res), "must NOT claim typed:N when nothing was written");
  assert.deepEqual(body.events, [], "no input/change dispatched on a non-editable target");
  // A selector resolving to a non-editable element is likewise refused.
  const div = { events: [], dispatchEvent(e) { this.events.push(e); } };
  fakeDoc({ map: { "#label": div } });
  assert.deepEqual(frameTypeFn("#label", "x"), { ok: false, error: "no_editable_target" });
});

test("frameKeyFn: dispatches keydown→keypress→keyup for a printable key with the mapped params", () => {
  const el = fakeEl();
  fakeDoc({ map: { "#f": el } });
  const res = frameKeyFn("#f", keyEventParams("Enter"));
  assert.deepEqual(res, { ok: true, key: "Enter" });
  assert.equal(el.focusCalls, 1);
  // Enter carries text ("\r") → keypress is included between down and up.
  assert.deepEqual(el.events.map((e) => e.type), ["keydown", "keypress", "keyup"]);
  assert.equal(el.events[0].key, "Enter");
  assert.equal(el.events[0].keyCode, 13);
});

test("frameKeyFn: a non-printable key (Tab) omits keypress; missing selector → element_not_found", () => {
  const el = fakeEl();
  fakeDoc({ map: { "#f": el } });
  const res = frameKeyFn("#f", keyEventParams("Tab"));
  assert.deepEqual(res, { ok: true, key: "Tab" });
  assert.deepEqual(el.events.map((e) => e.type), ["keydown", "keyup"]);  // no keypress
  fakeDoc({ map: {} });
  assert.deepEqual(frameKeyFn("#nope", keyEventParams("Enter")),
    { ok: false, error: "element_not_found" });
});
