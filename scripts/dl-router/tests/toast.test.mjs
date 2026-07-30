// Toast rendering + its params contract. The window-creation FALLBACK itself
// lives in the service worker and is covered by service_worker.test.mjs
// ("toast falls back to a notification..."); here we cover the popup page.
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_TOAST_MS, mount, parseParams, render, sourceLabel,
} from "../extension/toast.js";

// --- params ------------------------------------------------------------------ //
test("parseParams reads the full model from the query string", () => {
  const model = parseParams(
    "?id=7&dir=Jane%20Doe&reason=tag%20match&dup=dupe%20line&source=sidecar&ms=3000");
  assert.deepEqual(model, {
    downloadId: 7, dir: "Jane Doe", reason: "tag match", dup: "dupe line",
    source: "sidecar", ms: 3000,
  });
});

test("parseParams falls back to sane defaults", () => {
  const model = parseParams("");
  assert.deepEqual(model, {
    downloadId: null, dir: "", reason: "", dup: "", source: "",
    ms: DEFAULT_TOAST_MS,
  });
});

test("a nonsensical duration falls back to the default", () => {
  for (const q of ["?ms=abc", "?ms=-5", "?ms=0", "?ms="]) {
    assert.equal(parseParams(q).ms, DEFAULT_TOAST_MS, q);
  }
});

// --- source labels ------------------------------------------------------------ //
test("each decision source gets a human label", () => {
  assert.equal(sourceLabel("sidecar"), "matched");
  assert.equal(sourceLabel("cache"), "cached (sidecar slow)");
  assert.equal(sourceLabel("cache-timeout"), "cached (sidecar slow)");
  assert.equal(sourceLabel("other"), "no match");
  assert.equal(sourceLabel("other-timeout"), "no match");
  assert.equal(sourceLabel(""), "");
  assert.equal(sourceLabel("something-else"), "something-else");
});

// --- rendering ---------------------------------------------------------------- //
function fakeDoc({ search = "" } = {}) {
  const nodes = {};
  for (const id of ["dir", "reason", "badge", "dup", "change"]) {
    nodes[id] = { id, textContent: "", hidden: false, listeners: {},
      addEventListener(name, fn) { this.listeners[name] = fn; } };
  }
  return {
    location: { search },
    getElementById: (id) => nodes[id],
    addEventListener(name, fn) { this.listeners = { [name]: fn }; },
    nodes,
  };
}

test("render fills the directory, reason and badge", () => {
  const doc = fakeDoc();
  render(doc, { dir: "Jane Doe", reason: "tag=='Jane Doe'", dup: "",
    source: "sidecar" });
  assert.equal(doc.nodes.dir.textContent, "Jane Doe");
  assert.equal(doc.nodes.reason.textContent, "tag=='Jane Doe'");
  assert.equal(doc.nodes.badge.textContent, "matched");
});

test("the duplicate line is hidden when there is no duplicate", () => {
  const doc = fakeDoc();
  render(doc, { dir: "Jane Doe", reason: "r", dup: "", source: "sidecar" });
  assert.equal(doc.nodes.dup.hidden, true);
});

test("the duplicate line is shown when there is one", () => {
  const doc = fakeDoc();
  render(doc, { dir: "Jane Doe", reason: "r",
    dup: "Possible duplicate (name): already in this folder — Jane Doe/f.mp4",
    source: "sidecar" });
  assert.equal(doc.nodes.dup.hidden, false);
  assert.match(doc.nodes.dup.textContent, /Possible duplicate/);
});

test("a wrong match is always diagnosable — the reason is surfaced verbatim", () => {
  const doc = fakeDoc();
  const reason = "contains 'jane doe' (2/4 tokens) +host-prior";
  render(doc, { dir: "Jane Doe", reason, source: "cache" });
  assert.equal(doc.nodes.reason.textContent, reason);
  assert.equal(doc.nodes.badge.textContent, "cached (sidecar slow)");
});

// --- mount -------------------------------------------------------------------- //
function fakeWin() {
  return {
    closed: false,
    timers: [],
    close() { this.closed = true; },
    setTimeout(fn, ms) { this.timers.push({ fn, ms }); return this.timers.length; },
    clearTimeout(id) { this.timers[id - 1] = null; },
  };
}

test("mount renders, arms the auto-close timer and wires `change`", () => {
  const doc = fakeDoc({ search: "?id=9&dir=Jane%20Doe&reason=r&ms=1234" });
  const win = fakeWin();
  const sent = [];
  const chromeApi = { runtime: { sendMessage: async (m) => sent.push(m) } };

  const model = mount(doc, chromeApi, win);
  assert.equal(model.dir, "Jane Doe");
  assert.equal(doc.nodes.dir.textContent, "Jane Doe");
  assert.equal(win.timers[0].ms, 1234);

  doc.nodes.change.listeners.click();
  assert.deepEqual(sent[0], { type: "dlr:repick", downloadId: 9 });
  assert.equal(win.closed, true);
});

test("Escape closes the toast and cancels the auto-close timer", () => {
  const doc = fakeDoc({ search: "?id=9&dir=Jane%20Doe" });
  const win = fakeWin();
  mount(doc, { runtime: { sendMessage: async () => {} } }, win);
  doc.listeners.keydown({ key: "Escape" });
  assert.equal(win.closed, true);
  assert.equal(win.timers[0], null, "the timer must be cleared");
});

test("other keys do not close the toast", () => {
  const doc = fakeDoc({ search: "?id=9" });
  const win = fakeWin();
  mount(doc, { runtime: { sendMessage: async () => {} } }, win);
  doc.listeners.keydown({ key: "a" });
  assert.equal(win.closed, false);
});
