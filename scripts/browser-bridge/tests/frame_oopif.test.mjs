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
  normalizeWebNavFrames, resolveWebNavFrameId,
  frameReadHtmlFn, frameReadTextFn, frameEvalFn,
  frameClickFn, frameTypeFn, frameKeyFn, keyEventParams,
} from "../extension/protocol.js";

// A getAllFrames() result for civitai.com/apps/run/model-benchmarking: the top frame
// PLUS a CROSS-ORIGIN child (model-benchmarking.civit.ai) that runs OUT-OF-PROCESS —
// the frame the old CDP getFrameTree could NOT see.
const GET_ALL_FRAMES = [
  { frameId: 0, parentFrameId: -1, url: "https://civitai.com/apps/run/model-benchmarking", documentId: "d0" },
  { frameId: 7, parentFrameId: 0, url: "https://model-benchmarking.civit.ai/", documentId: "d7" },
  { frameId: 12, parentFrameId: 7, url: "https://ads.example/pixel", documentId: "d12" },
];

// --- enumeration ----------------------------------------------------------- //
test("normalizeWebNavFrames maps getAllFrames → {frameId,url,parentFrameId} incl. the OOPIF", () => {
  const out = normalizeWebNavFrames(GET_ALL_FRAMES);
  assert.deepEqual(out, [
    { frameId: 0, url: "https://civitai.com/apps/run/model-benchmarking", parentFrameId: -1 },
    { frameId: 7, url: "https://model-benchmarking.civit.ai/", parentFrameId: 0 },
    { frameId: 12, url: "https://ads.example/pixel", parentFrameId: 7 },
  ]);
  // THE REGRESSION: the cross-origin child frame IS present (getFrameTree missed it).
  assert.ok(out.some((f) => f.url.includes("model-benchmarking.civit.ai")),
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
  assert.equal(resolveWebNavFrameId(frames, "model-benchmarking.civit.ai"), 7);
  assert.equal(resolveWebNavFrameId(frames, "MODEL-BENCHMARKING.CIVIT.AI"), 7);
  // first (list-order) substring match wins deterministically.
  assert.equal(resolveWebNavFrameId(frames, "https://"), 0);
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

test("frameClickFn: scrolls, dispatches mousedown→mouseup→click AND el.click(); returns center", () => {
  const el = fakeEl();
  fakeDoc({ map: { "#go": el } });
  const res = frameClickFn("#go");
  assert.deepEqual(res, { ok: true, x: 60, y: 40 });  // center of 100x40 @ (10,20)
  assert.equal(el.scrolled, true);
  assert.deepEqual(el.events.map((e) => e.type), ["mousedown", "mouseup", "click"]);
  assert.equal(el.clickCalls, 1, "also calls el.click() for click-only listeners");
  assert.equal(el.events[0].clientX, 60);
  assert.equal(el.events[0].clientY, 40);
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

test("frameTypeFn: empty selector types into the frame's activeElement", () => {
  const el = fakeEl();
  fakeDoc({ active: el });
  const res = frameTypeFn("", "abc");
  assert.deepEqual(res, { ok: true, typed: 3 });
  assert.equal(el.value, "abc");
});

test("frameTypeFn: a given selector matching nothing → element_not_found", () => {
  fakeDoc({ map: {} });
  assert.deepEqual(frameTypeFn("#nope", "x"), { ok: false, error: "element_not_found" });
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
