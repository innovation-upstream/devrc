// The in-page overlay content script: what it builds, and what it refuses.
//
// It deliberately contains NO picker logic -- it frames `picker.html`, which
// runs the same `picker.js` and the same reducer. So the tests here are about
// the delivery mechanism only: the closed shadow root, the frame's URL, one
// overlay at a time, and the close protocol.
//
// Run: nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.DL_ROUTER_NO_AUTOSTART = true;
await import("../extension/picker_overlay.js");

const OV = globalThis.__DLR_OVERLAY__;

import { makeDoc } from "./fake_page.mjs";

const URL_A = "chrome-extension://test/picker.html?id=7&embed=1&overlay=ov-a";

function freshDoc() {
  OV.forget();
  return makeDoc([]);
}

function frameOf(doc) {
  const host = doc.body.children[0];
  return { host, shadow: host.shadowRoot,
    frame: host.shadowRoot.children.find((c) => c.tagName === "iframe") };
}

test("the module exposes its pure functions without installing listeners", () => {
  assert.equal(typeof OV.openOverlay, "function");
  assert.equal(typeof OV.closeOverlay, "function");
  assert.equal(typeof OV.handleMessage, "function");
});

test("opening builds a host with a frame pointing at the picker page", () => {
  const doc = freshDoc();
  const out = OV.openOverlay(doc, { type: "dlr:overlay-open", id: "ov-a",
    url: URL_A });
  assert.deepEqual(out, { ok: true, id: "ov-a" });
  const { host, frame } = frameOf(doc);
  assert.equal(host.getAttribute("id"), OV.HOST_ID);
  assert.equal(frame.getAttribute("src"), URL_A);
  assert.ok(OV.hasOverlay());
});

test("THE SHADOW ROOT IS CLOSED", () => {
  // Not cosmetic. The frame's URL carries the per-open nonce the service worker
  // matches on, and a page that could read it out of an open shadow root could
  // forge `dlr:picker-closed` and quietly discard the pick.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  assert.equal(frameOf(doc).shadow.mode, "closed");
});

test("the host is positioned inline and !important", () => {
  // The host is the ONE node the page's stylesheets can see and match on, so
  // its geometry cannot live in the shadow stylesheet.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const style = frameOf(doc).host.getAttribute("style");
  assert.match(style, /position: fixed !important/);
  assert.match(style, /z-index: 2147483647 !important/);
});

test("the shadow stylesheet honours prefers-reduced-motion", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const css = frameOf(doc).shadow.children
    .find((c) => c.tagName === "style").textContent;
  assert.match(css, /animation: dlr-overlay-in/);
  assert.match(css, /prefers-reduced-motion/);
});

test("opening twice REPLACES rather than stacks", () => {
  // Two pickers for one download would make it ambiguous which one an Enter
  // answers -- and the reducer has no notion of "which download", because the
  // id is baked into the frame's URL.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  OV.openOverlay(doc, { id: "ov-b", url: "chrome-extension://test/picker.html?x" });
  assert.equal(doc.body.children.length, 1);
  assert.equal(frameOf(doc).frame.getAttribute("src"),
    "chrome-extension://test/picker.html?x");
});

test("a malformed open is refused rather than half-built", () => {
  const doc = freshDoc();
  for (const msg of [null, {}, { id: "ov-a" }, { url: URL_A },
    { id: "", url: URL_A }, { id: "ov-a", url: "" },
    { id: 7, url: URL_A }]) {
    assert.equal(OV.openOverlay(doc, msg).ok, false, JSON.stringify(msg));
  }
  assert.equal(doc.body.children.length, 0);
  assert.equal(OV.hasOverlay(), false);
});

test("a DOM that cannot host the overlay is a clean false, not a throw", () => {
  // The service worker's whole fallback depends on this never throwing.
  OV.forget();
  const broken = { createElement: () => { throw new Error("no"); }, body: null };
  assert.deepEqual(OV.openOverlay(broken, { id: "ov-a", url: URL_A }),
    { ok: false, error: "create_failed" });
  assert.equal(OV.hasOverlay(), false);
});

test("closing removes the host", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  assert.deepEqual(OV.closeOverlay("ov-a"), { ok: true });
  assert.equal(doc.body.children.length, 0);
  assert.equal(OV.hasOverlay(), false);
});

test("a STALE close is a no-op, not a blind teardown", () => {
  // A close for a previous overlay must not remove a picker that a later
  // download legitimately opened.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-b", url: URL_A });
  assert.deepEqual(OV.closeOverlay("ov-a"), { ok: false, error: "stale" });
  assert.equal(doc.body.children.length, 1);
});

test("closing when there is nothing open says so", () => {
  OV.forget();
  assert.deepEqual(OV.closeOverlay("ov-a"), { ok: false, error: "no_overlay" });
});

test("handleMessage routes exactly the two overlay messages", () => {
  const doc = freshDoc();
  const replies = [];
  const reply = (r) => replies.push(r);

  OV.handleMessage(doc, { type: "dlr:overlay-open", id: "ov-a", url: URL_A },
    reply);
  assert.deepEqual(replies[0], { ok: true, id: "ov-a" });

  OV.handleMessage(doc, { type: "dlr:close-overlay", overlay: "ov-a" }, reply);
  assert.deepEqual(replies[1], { ok: true });

  // Anything else is left alone -- content_capture.js and the picker share this
  // message bus.
  for (const msg of [null, "nope", { type: "dlr:capture" },
    { type: "dlr:choose" }]) {
    assert.equal(OV.handleMessage(doc, msg, reply), false);
  }
  assert.equal(replies.length, 2, "no stray replies");
});

test("handleMessage never returns true (it answers synchronously)", () => {
  // Returning true would hold the message channel open waiting for a response
  // that already went out, and Chrome would log a dangling-port error.
  const doc = freshDoc();
  assert.equal(
    OV.handleMessage(doc, { type: "dlr:overlay-open", id: "ov-a", url: URL_A },
      () => {}), false);
});

test("only the top frame paints an overlay", () => {
  // `all_frames: true` is for the capture script; a picker in every iframe of a
  // page would be absurd.
  const top = {};
  top.top = top;
  top.self = top;
  assert.equal(OV.isTopFrame(top), true);
  assert.equal(OV.isTopFrame({ top: {}, self: {} }), false);
  assert.equal(OV.isTopFrame(null), false);
  assert.equal(OV.isTopFrame({ get top() { throw new Error("cross-origin"); } }),
    false);
});

test("the overlay script reimplements none of the picker", () => {
  // The architectural line this file exists to hold: the overlay and the
  // window must share ONE reducer, and the way that is guaranteed is that the
  // overlay frames the picker page rather than rendering a list itself.
  // safety.py/sanitize.js already paid for a second implementation once.
  const src = readFileSync(new URL("../extension/picker_overlay.js",
    import.meta.url), "utf8");
  for (const forbidden of ["filterEntries", "reduce(", "ArrowDown",
    "dlr:choose", "titleCase", "ENTRY_NEW"]) {
    assert.equal(src.includes(forbidden), false,
      `picker_overlay.js must not contain ${forbidden}`);
  }
  // What it DOES do: frame whatever URL the worker hands it.
  assert.match(src, /createElement\("iframe"\)/);
});

test("the manifest delivers the overlay through the EXISTING content script", () => {
  // No new permissions: the overlay rides the declaration content_capture.js
  // already has. If that ever changes, this is where it becomes visible.
  const manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"));
  const [cs] = manifest.content_scripts;
  assert.deepEqual(cs.matches, ["http://*/*", "https://*/*"]);
  // ORDER IS LOAD-BEARING, not cosmetic: the files in one declaration run in
  // this order in one isolated world, and player_buttons.js reuses
  // content_capture.js's extractor through `globalThis.__DLR_CAPTURE__` rather
  // than restating it. Put content_capture.js second and the page-context
  // responder answers `{ok:false, error:"no_capture"}` on the first click.
  assert.deepEqual(cs.js,
    ["content_capture.js", "picker_overlay.js", "player_buttons.js"]);
  assert.deepEqual(manifest.permissions.slice().sort(),
    ["alarms", "contextMenus", "downloads", "notifications", "storage", "tabs"]);
  // Framing an extension page from a web page needs it web-accessible.
  const [war] = manifest.web_accessible_resources;
  assert.deepEqual(manifest.web_accessible_resources.length, 1);
  assert.deepEqual(war.matches, ["http://*/*", "https://*/*"]);
  // THE PRIMARY MITIGATION, ahead of the per-open id rather than instead of it.
  // A static chrome-extension://<id>/picker.html is guessable, so any page
  // could frame the picker and try to clickjack two clicks into a /mkdir and a
  // /relocate. A dynamic URL rotates per session, so the page cannot construct
  // the surface to load at all; the id stays as the authorisation check for
  // anything that does get loaded.
  assert.equal(war.use_dynamic_url, true);
  // The framed page's OWN module graph has to be listed too: under a rotating
  // origin its relative imports resolve against that origin, and an unlisted
  // import would 404 -- which fails safe (gate 2 never fires, the worker opens
  // a window) but would mean the overlay never worked at all.
  assert.deepEqual(war.resources.slice().sort(),
    ["picker.html", "picker.js", "route_core.js", "sanitize.js"]);
});

// --- the page can take the overlay away ------------------------------------- //
//
// PINS ON A PERMISSIVE PATH. Gate 2 proves the frame booted; it proves nothing
// a millisecond later. An overlay is a node in a document the extension does
// not own, and the worker has ALREADY been told the picker was delivered. If
// losing it were silent, the download would end up with no picker at all.

test("a detached host is REPORTED, so the worker can re-ask in a window", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const { host } = frameOf(doc);
  assert.equal(OV.stillAttached(), true);

  host.remove();                       // `body.innerHTML = ...`, a sanitiser,
  assert.equal(OV.stillAttached(), false);   // an SPA re-render, or hostile JS

  const lost = [];
  assert.equal(OV.reportLostIfDetached((id) => lost.push(id)), true);
  assert.deepEqual(lost, ["ov-a"]);
  assert.equal(OV.hasOverlay(), false, "and it stops claiming to have one");
});

test("an attached overlay is NOT reported lost", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const lost = [];
  assert.equal(OV.reportLostIfDetached((id) => lost.push(id)), false);
  assert.deepEqual(lost, []);
  assert.equal(OV.hasOverlay(), true);
});

test("a detached host is reported exactly once", () => {
  // Re-delivery must not produce two windows for one download.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  frameOf(doc).host.remove();
  const lost = [];
  const notify = (id) => lost.push(id);
  OV.reportLostIfDetached(notify);
  OV.reportLostIfDetached(notify);
  OV.reportLostIfDetached(notify);
  assert.deepEqual(lost, ["ov-a"]);
});

test("a notify that throws does not wedge the content script", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  frameOf(doc).host.remove();
  assert.equal(OV.reportLostIfDetached(() => { throw new Error("no worker"); }),
    true);
  assert.equal(OV.hasOverlay(), false);
});

test("the watcher is only armed while an overlay is live", () => {
  // An always-on MutationObserver over every page the user visits is a real
  // cost for no extra coverage; node has no MutationObserver, so the guard is
  // what is asserted here.
  const doc = freshDoc();
  assert.equal(OV.watchAttachment(doc, () => {}), null,
    "no MutationObserver, no watcher -- and no throw");
  assert.equal(OV.watchAttachment(null, () => {}), null);
});

test("closing SWEEPS a stray host left by a previous injection", () => {
  // The content script is re-injected on every navigation, so a re-injected
  // copy has no record while a host element from before may still be in the
  // document. Without the sweep the user faces a modal with no close button
  // and no working Esc, and the only way out is a page reload.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  OV.forget();                                   // as if freshly re-injected
  assert.equal(OV.hasOverlay(), false);
  // The fake's getElementById is id-keyed, so register the host under its id
  // the way a real document would.
  doc.els.set(OV.HOST_ID, doc.body.children[0]);
  assert.deepEqual(OV.closeOverlay("ov-a", doc), { ok: true, swept: true });
  assert.equal(doc.body.children.length, 0);
});

test("the sweep does not invent an overlay that was never there", () => {
  const doc = freshDoc();
  assert.deepEqual(OV.closeOverlay("ov-a", doc),
    { ok: false, error: "no_overlay" });
});

test("the frame is focused, and again once it has loaded", () => {
  // The picker is keyboard-first. `input.focus()` inside the framed document
  // only moves focus WITHIN that document -- the frame element has to hold
  // focus for keystrokes to reach it. Without this the user would have to click
  // the overlay before typing, which the popup window never required.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const { frame } = frameOf(doc);
  assert.equal(frame.focused, true);
  assert.ok(frame.listeners.has("load"), "and re-focused when it loads");
  frame.focused = false;
  frame.fire("load");
  assert.equal(frame.focused, true);
});

// --- dlr:focus-overlay ------------------------------------------------------- //
//
// FOCUSING THE FRAME AT BUILD TIME IS NOT ENOUGH, and this is the half that was
// missing. The worker RAISES the overlay's tab and its window right after the
// frame reports ready -- and raising a tab puts focus on the PAGE's document,
// taking it straight back off the frame. Whichever lands last wins, so the
// worker asks again after it has finished raising things.

test("dlr:focus-overlay puts keyboard focus back on the live frame", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const { frame } = frameOf(doc);
  frame.focused = false;
  assert.deepEqual(OV.focusOverlay("ov-a"), { ok: true });
  assert.equal(frame.focused, true);
});

test("a focus request for another overlay is refused, not obeyed", () => {
  // A stale request must not yank the caret into a picker a LATER download
  // opened.
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const { frame } = frameOf(doc);
  frame.focused = false;
  assert.deepEqual(OV.focusOverlay("ov-b"), { ok: false, error: "stale" });
  assert.equal(frame.focused, false);
});

test("a focus request with no overlay is a no-op", () => {
  freshDoc();
  assert.deepEqual(OV.focusOverlay("ov-a"), { ok: false, error: "no_overlay" });
});

test("handleMessage routes dlr:focus-overlay", () => {
  const doc = freshDoc();
  OV.openOverlay(doc, { id: "ov-a", url: URL_A });
  const { frame } = frameOf(doc);
  frame.focused = false;
  let answer = null;
  OV.handleMessage(doc, { type: "dlr:focus-overlay", overlay: "ov-a" },
    (r) => { answer = r; });
  assert.deepEqual(answer, { ok: true });
  assert.equal(frame.focused, true);
});
