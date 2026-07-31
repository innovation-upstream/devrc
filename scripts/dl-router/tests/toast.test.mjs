// Toast rendering + its params contract. The window-creation FALLBACK itself
// lives in the service worker and is covered by service_worker.test.mjs
// ("toast falls back to a notification..."); here we cover the popup page.
import test from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_TOAST_MS, isDuplicateMode, mount, parseParams, render, sourceLabel,
} from "../extension/toast.js";

// --- params ------------------------------------------------------------------ //
test("parseParams reads the full model from the query string", () => {
  const model = parseParams(
    "?id=7&dir=Jane%20Doe&reason=tag%20match&dup=dupe%20line&source=sidecar&ms=3000");
  assert.deepEqual(model, {
    downloadId: 7, dir: "Jane Doe", reason: "tag match", dup: "dupe line",
    source: "sidecar", ms: 3000, mode: "", relPath: "", dupRelPath: "",
  });
});

test("parseParams falls back to sane defaults", () => {
  const model = parseParams("");
  assert.deepEqual(model, {
    downloadId: null, dir: "", reason: "", dup: "", source: "",
    ms: DEFAULT_TOAST_MS, mode: "", relPath: "", dupRelPath: "",
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
  for (const id of ["dir", "reason", "badge", "dup", "error", "change", "keep",
    "discard"]) {
    nodes[id] = { id, textContent: "", hidden: false, disabled: false,
      listeners: {},
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

// --- the duplicate question --------------------------------------------------- //
//
// The file is ALWAYS kept and filed. This toast only offers to remove the copy
// that just landed, and it is the front end of the only destructive path in the
// subsystem -- so the pins here are about what must NOT happen as much as what
// must.
const DUP_SEARCH = "?id=9&dir=Jane%20Doe&mode=dup&rel=Jane%20Doe%2Fnew.mp4"
  + "&dupRel=john-smith%2F75936.mov&dup=Duplicate%20of%20john-smith%2F75936.mov";

// A sentinel, because `reply: undefined` would hit the default parameter and
// so could never express "the worker answered with nothing".
const NO_REPLY = Symbol("no-reply");

function mountDup({ search = DUP_SEARCH, reply = { ok: true } } = {}) {
  if (reply === NO_REPLY) reply = undefined;
  const doc = fakeDoc({ search });
  const win = fakeWin();
  const sent = [];
  const chromeApi = {
    runtime: {
      sendMessage: async (m) => {
        sent.push(m);
        if (m.type === "dlr:discard") {
          if (reply instanceof Error) throw reply;
          return reply;
        }
        return { ok: true };
      },
    },
  };
  const model = mount(doc, chromeApi, win);
  return { doc, win, sent, model };
}

test("duplicate mode reveals delete and keep, and names the file", () => {
  const { doc, model } = mountDup();
  assert.equal(isDuplicateMode(model), true);
  assert.equal(doc.nodes.discard.hidden, false);
  assert.equal(doc.nodes.keep.hidden, false);
  assert.match(doc.nodes.dup.textContent, /Duplicate of john-smith\/75936\.mov/);
});

test("the ordinary toast shows neither delete nor keep", () => {
  const doc = fakeDoc({ search: "?id=9&dir=Jane%20Doe&dup=Possible%20duplicate" });
  mount(doc, { runtime: { sendMessage: async () => {} } }, fakeWin());
  assert.equal(doc.nodes.discard.hidden, true);
  assert.equal(doc.nodes.keep.hidden, true);
});

test("a delete button is never offered without BOTH paths to prove it", () => {
  // Without `rel` there is nothing to delete; without `dupRel` there is no
  // proof the bytes exist anywhere else. Either way the sidecar refuses, so
  // the button would be one that can only fail.
  for (const search of ["?id=9&mode=dup&rel=Jane%20Doe%2Fa.mp4",
    "?id=9&mode=dup&dupRel=john-smith%2Fb.mov", "?id=9&mode=dup"]) {
    const doc = fakeDoc({ search });
    mount(doc, { runtime: { sendMessage: async () => {} } }, fakeWin());
    assert.equal(doc.nodes.discard.hidden, true, search);
  }
});

test("the duplicate question does NOT auto-close", () => {
  // A question that disappears on its own is answered by whichever button the
  // timer favours -- and one of these buttons deletes a file.
  const { win } = mountDup();
  assert.equal(win.timers.length, 0);
});

test("delete sends dlr:discard and closes only on success", async () => {
  const { doc, win, sent } = mountDup();
  doc.nodes.discard.listeners.click();
  await tick();
  assert.deepEqual(sent[0], {
    type: "dlr:discard", downloadId: 9,
    relPath: "Jane Doe/new.mp4", dupRelPath: "john-smith/75936.mov",
  });
  assert.equal(win.closed, true);
});

test("a REFUSED delete keeps the toast open and shows the reason", async () => {
  const { doc, win } = mountDup({
    reply: { ok: false, error: "refusing to move a file this router cannot "
      + "prove it created" } });
  doc.nodes.discard.listeners.click();
  await tick();
  assert.equal(win.closed, false, "a refusal must stay visible");
  assert.match(doc.nodes.error.textContent, /Not deleted: refusing to move/);
  assert.equal(doc.nodes.discard.disabled, false, "retry must be possible");
  // THE REFUSAL MUST NOT ERASE WHAT THE DUPLICATE WAS OF. Writing it over
  // `#dup` removed exactly the context needed to judge whether to retry.
  assert.match(doc.nodes.dup.textContent, /Duplicate of john-smith\/75936\.mov/);
});

test("a MISSING answer counts as a refusal, not a success", async () => {
  const { doc, win } = mountDup({ reply: NO_REPLY });
  doc.nodes.discard.listeners.click();
  await tick();
  assert.equal(win.closed, false);
  assert.match(doc.nodes.error.textContent, /no answer from the extension/);
  assert.match(doc.nodes.dup.textContent, /Duplicate of/);
});

test("a thrown sendMessage counts as a refusal", async () => {
  const { doc, win } = mountDup({ reply: new Error("worker gone") });
  doc.nodes.discard.listeners.click();
  await tick();
  assert.equal(win.closed, false);
  assert.match(doc.nodes.error.textContent, /Not deleted: worker gone/);
  assert.match(doc.nodes.dup.textContent, /Duplicate of/);
});

test("delete is single-flight while in flight", async () => {
  const { doc } = mountDup();
  doc.nodes.discard.listeners.click();
  assert.equal(doc.nodes.discard.disabled, true);
  await tick();
});

test("keep closes without asking the worker to delete anything", () => {
  const { doc, win, sent } = mountDup();
  doc.nodes.keep.listeners.click();
  assert.equal(win.closed, true);
  assert.equal(sent.filter((m) => m.type === "dlr:discard").length, 0);
});

test("Escape is KEEP -- the reflex key never deletes", () => {
  const { doc, win, sent } = mountDup();
  doc.listeners.keydown({ key: "Escape" });
  assert.equal(win.closed, true);
  assert.equal(sent.filter((m) => m.type === "dlr:discard").length, 0);
});

function tick() {
  return new Promise((r) => setTimeout(r, 0));
}
