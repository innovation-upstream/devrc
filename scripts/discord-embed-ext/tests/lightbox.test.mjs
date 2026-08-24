import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { FakeElement, makeDiscordDoc, makeClickEvent, openShadow } from "./fake_discord_dom.mjs";

globalThis.DEE_NO_AUTOSTART = true;
// Load order MIRRORS manifest.json: embed_enlarge.js first, because it owns the
// single MEDIA_URL_RE definition that lightbox.js consumes. Importing lightbox
// alone would leave that null and silently reduce every message to one sibling.
await import("../extension/embed_enlarge.js");
await import("../extension/lightbox.js");
const LB = globalThis.__DEE_LIGHTBOX__;

function makeDocWithImage(src) {
  LB.forget();
  var doc = makeDiscordDoc("<div class='message'><div class='embed' style='max-width:400px;'><img src='" + src + "' /></div></div>");
  return doc;
}

const DISCORD_IMG = "https://cdn.discordapp.com/attachments/123/456/photo.png";
const A1 = "https://cdn.discordapp.com/attachments/100/1/a1.png";
const A2 = "https://cdn.discordapp.com/attachments/100/2/a2.png";
const B1 = "https://cdn.discordapp.com/attachments/200/1/b1.png";
const B2 = "https://cdn.discordapp.com/attachments/200/2/b2.png";

// 🔴 THE NESTING DEPTH IS THE WHOLE POINT OF THIS FIXTURE, NOT DECORATION.
// The bug being pinned is that getMediaSiblings walked up a FIXED 10 parents.
// On a shallow tree that walk runs off the top, `node` goes null and the old
// code returned a single-element list — so every scoping assertion would pass
// vacuously and the mutant would survive. Nine wrapper divs put the 10th
// ancestor at `w2`, which contains BOTH messages, so the old behaviour is
// observable: it collects all FOUR images instead of the clicked message's two.
function makeTwoMessageDoc() {
  LB.forget();
  var open9 = "";
  var close9 = "";
  for (var i = 1; i <= 9; i++) { open9 += "<div class='w" + i + "'>"; close9 += "</div>"; }
  var msgA = "<div class='message' id='msg-a'><div class='embed'>" +
    "<img src='" + A1 + "' /><img src='" + A2 + "' /></div></div>";
  var msgB = "<div class='message' id='msg-b'><div class='embed'>" +
    "<img src='" + B1 + "' /><img src='" + B2 + "' /></div></div>";
  return makeDiscordDoc(open9 + msgA + msgB + close9);
}

function shadowOf(doc) {
  // openShadow(), not host.shadowRoot: the root is mode:"closed", and the fake
  // now hides it exactly as a real browser does.
  return openShadow(doc.querySelector("#dee-lightbox-host"));
}

function buttonByText(shadow, text) {
  var btns = shadow.querySelectorAll("button");
  for (var i = 0; i < btns.length; i++) {
    if (btns[i].textContent === text) return btns[i];
  }
  return null;
}

function clickOn(el, target) {
  el.dispatchEvent({ type: "click", target: target || el, stopPropagation: function () {} });
}

test("the module exposes its pure functions", () => {
  assert.equal(typeof LB.open, "function");
  assert.equal(typeof LB.close, "function");
  assert.equal(typeof LB.isOpen, "function");
  assert.equal(typeof LB.handleKey, "function");
  assert.equal(typeof LB.handleWheel, "function");
  assert.equal(typeof LB.handleMouseDown, "function");
  assert.equal(typeof LB.handleMouseMove, "function");
  assert.equal(typeof LB.handleMouseUp, "function");
  assert.equal(typeof LB.handleBackdropClick, "function");
  assert.equal(typeof LB.navigate, "function");
  assert.equal(typeof LB.setZoom, "function");
  assert.equal(typeof LB.forget, "function");
});

test("opening creates a host with a closed shadow root", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  var result = LB.open(doc, img);
  assert.equal(result.ok, true);
  var host = doc.querySelector("#dee-lightbox-host");
  assert.ok(host, "host element created");
  assert.equal(host.shadowRoot, null,
    "a CLOSED root is unreachable from the host — the page cannot reach into it");
  assert.equal(openShadow(host).mode, "closed", "and it really is closed");
  assert.equal(LB.isOpen(), true);
});

test("opening replaces an existing lightbox (no stacking)", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  LB.open(doc, img);
  var hosts = doc.querySelectorAll("#dee-lightbox-host");
  assert.equal(hosts.length, 1, "only one host");
  assert.equal(LB.isOpen(), true);
});

test("closing removes the host element", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  assert.equal(LB.isOpen(), true);
  var result = LB.close(doc);
  assert.equal(result.ok, true);
  assert.equal(LB.isOpen(), false);
  var host = doc.querySelector("#dee-lightbox-host");
  assert.equal(host, null, "host removed");
});

test("closing when nothing is open returns ok:false", () => {
  LB.forget();
  var doc = makeDocWithImage(DISCORD_IMG);
  var result = LB.close(doc);
  assert.equal(result.ok, false);
});

test("Escape closes the lightbox", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "Escape", preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), false);
});

// --- sibling scoping -------------------------------------------------------
// REGRESSION for the fixed-10-parent walk. Red before the fix: siblingCount 4.

test("REGRESSION: siblings are scoped to the clicked message, not the ancestor 10 levels up", () => {
  var doc = makeTwoMessageDoc();
  var a1 = doc.querySelector("#msg-a img");
  LB.open(doc, a1);
  assert.equal(LB.siblingCount(), 2,
    "must collect only the 2 images in msg-a, not all 4 in the shared ancestor");
});

test("REGRESSION: arrow navigation wraps within the message and never reaches the next one", () => {
  var doc = makeTwoMessageDoc();
  var a1 = doc.querySelector("#msg-a img");
  LB.open(doc, a1);
  assert.equal(LB.currentSrc(), A1, "opens on the clicked image");
  LB.handleKey(doc, { key: "ArrowRight", preventDefault: function () {} });
  assert.equal(LB.currentSrc(), A2, "advances to the second image of msg-a");
  LB.handleKey(doc, { key: "ArrowRight", preventDefault: function () {} });
  assert.equal(LB.currentSrc(), A1, "wraps back to the first — B1 is a different message");
  LB.handleKey(doc, { key: "ArrowLeft", preventDefault: function () {} });
  assert.equal(LB.currentSrc(), A2, "left wraps backwards within the message");
});

test("a single-image message reports one sibling and navigation is a no-op", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  assert.equal(LB.siblingCount(), 1);
  LB.handleKey(doc, { key: "ArrowRight", preventDefault: function () {} });
  assert.equal(LB.currentSrc(), DISCORD_IMG, "nothing to navigate to");
  assert.equal(LB.isOpen(), true);
});

test("media with no message ancestor falls back to itself, not to an empty list", () => {
  LB.forget();
  var doc = makeDiscordDoc("<div class='wrap'><img src='" + A1 + "' /></div>");
  var img = doc.querySelector("img");
  LB.open(doc, img);
  assert.equal(LB.siblingCount(), 1);
  assert.equal(LB.currentSrc(), A1);
});

// --- the on-screen controls are wired -------------------------------------
// REGRESSION: every one of these was created, styled, appended — and inert.

test("REGRESSION: the next/prev arrow buttons are wired to navigate", () => {
  var doc = makeTwoMessageDoc();
  var a1 = doc.querySelector("#msg-a img");
  LB.open(doc, a1);
  var shadow = shadowOf(doc);
  var next = shadow.querySelector(".nav-next");
  var prev = shadow.querySelector(".nav-prev");
  assert.ok(next && prev, "both arrows rendered for a multi-image message");
  clickOn(next);
  assert.equal(LB.currentSrc(), A2, "clicking ▶ advanced the media");
  clickOn(prev);
  assert.equal(LB.currentSrc(), A1, "clicking ◀ went back");
});

test("the arrows are not rendered for a single-image message", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  assert.equal(shadow.querySelector(".nav-next"), null);
  assert.equal(shadow.querySelector(".nav-prev"), null);
});

test("REGRESSION: the +/- zoom buttons are wired and move the zoom level", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  var plus = buttonByText(shadow, "+");
  var minus = buttonByText(shadow, "−");
  assert.ok(plus && minus, "both zoom buttons rendered");
  assert.equal(LB.zoomLevel(), 1);
  clickOn(plus);
  assert.equal(LB.zoomLevel(), 1.25, "clicking + zoomed in");
  clickOn(minus);
  assert.equal(LB.zoomLevel(), 1, "clicking - zoomed back out");
});

// --- zoom state, asserted as STATE not as "handled: true" -----------------

test("+ key raises the zoom level and the label agrees", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "+", preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.zoomLevel(), 1.25);
  var shadow = shadowOf(doc);
  assert.equal(shadow.querySelector(".controls").children[1].textContent, "125%");
});

test("- key lowers the zoom level", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleKey(doc, { key: "-", preventDefault: function () {} });
  assert.equal(LB.zoomLevel(), 0.75);
});

test("zoom is clamped to [0.5, 5] at both ends", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  LB.setZoom(99);
  assert.equal(LB.zoomLevel(), 5, "upper clamp");
  LB.setZoom(-99);
  assert.equal(LB.zoomLevel(), 0.5, "lower clamp");
});

test("unrecognized keys are not handled and change nothing", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "a", preventDefault: function () {} });
  assert.equal(handled, false);
  assert.equal(LB.zoomLevel(), 1);
  assert.equal(LB.isOpen(), true);
});

test("wheel up zooms in, wheel down zooms out", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleWheel(doc, { deltaY: -100, preventDefault: function () {} });
  assert.equal(LB.zoomLevel(), 1.25);
  LB.handleWheel(doc, { deltaY: 100, preventDefault: function () {} });
  assert.equal(LB.zoomLevel(), 1);
});

test("REGRESSION: navigating resets the zoom AND the label that reports it", () => {
  var doc = makeTwoMessageDoc();
  var a1 = doc.querySelector("#msg-a img");
  LB.open(doc, a1);
  LB.setZoom(1.5);
  var shadow = shadowOf(doc);
  assert.equal(shadow.querySelector(".controls").children[1].textContent, "150%");
  LB.navigate(1);
  assert.equal(LB.zoomLevel(), 1, "zoom reset for the new media");
  assert.equal(shadow.querySelector(".controls").children[1].textContent, "100%",
    "the label must not keep reporting the PREVIOUS zoom");
});

// --- drag / backdrop ------------------------------------------------------

test("mousedown starts a drag, mouseup ends it", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  assert.equal(LB.handleMouseDown(doc, { clientX: 100, clientY: 200 }), false);
  assert.equal(LB.handleMouseMove(doc, { clientX: 150, clientY: 250 }), true, "move pans while dragging");
  assert.equal(LB.handleMouseUp(doc, { clientX: 150, clientY: 250 }), false);
  assert.equal(LB.handleMouseMove(doc, { clientX: 300, clientY: 300 }), false, "no pan after mouseup");
  assert.equal(LB.isOpen(), true);
});

test("REGRESSION: clicking the backdrop closes the lightbox", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  var backdrop = shadow.querySelector(".backdrop");
  assert.ok(backdrop, "backdrop present");
  clickOn(backdrop);
  assert.equal(LB.isOpen(), false, "the zoom-out cursor promises this");
});

test("REGRESSION: a pan that ends over the backdrop does NOT close the lightbox", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  var backdrop = shadow.querySelector(".backdrop");
  LB.handleMouseDown(doc, { clientX: 100, clientY: 100 });
  LB.handleMouseMove(doc, { clientX: 260, clientY: 180 });
  LB.handleMouseUp(doc, { clientX: 260, clientY: 180 });
  clickOn(backdrop);
  assert.equal(LB.isOpen(), true, "a drag is not a click");
});

test("a click on the media itself does not close the lightbox", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  var backdrop = shadow.querySelector(".backdrop");
  var media = shadow.querySelector(".media-container");
  clickOn(backdrop, media);
  assert.equal(LB.isOpen(), true, "only the bare backdrop closes");
});

// --- structure ------------------------------------------------------------

test("the lightbox displays the media at native resolution", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  var mediaContainer = shadow.querySelector(".media-container");
  assert.ok(mediaContainer, "media container found in shadow");
  var clonedImg = null;
  for (var k = 0; k < mediaContainer.children.length; k++) {
    if (mediaContainer.children[k].tagName === "img") clonedImg = mediaContainer.children[k];
  }
  assert.ok(clonedImg, "cloned img in media container");
  assert.equal(clonedImg.getAttribute("src"), DISCORD_IMG, "the clone shows the clicked media");
});

test("a video clone carries its <source> children across", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'><video>" +
    "<source src='https://media.discordapp.net/attachments/1/2/clip.mp4' />" +
    "</video></div></div>");
  var vid = doc.querySelector("video");
  LB.open(doc, vid);
  var shadow = shadowOf(doc);
  var clone = shadow.querySelector(".media-container video");
  assert.ok(clone, "video cloned");
  var srcs = clone.querySelectorAll("source");
  assert.equal(srcs.length, 1, "the <source> child came with it");
  assert.equal(srcs[0].getAttribute("src"),
    "https://media.discordapp.net/attachments/1/2/clip.mp4");
});

test("the lightbox shows a semi-transparent backdrop", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var shadow = shadowOf(doc);
  assert.ok(shadow.querySelector(".backdrop"), "backdrop found");
});

test("the lightbox script reimplements none of embed_enlarge (source structure check)", () => {
  var src = readFileSync(new URL("../extension/lightbox.js", import.meta.url), "utf8");
  for (var forbidden of ["findContainer", "scan("]) {
    assert.ok(!src.includes(forbidden), "lightbox.js must not contain " + forbidden);
  }
  assert.ok(src.includes("attachShadow"), "lightbox creates shadow root");
  assert.ok(src.includes("createElement"), "lightbox creates elements");
});

test("the extension ships no sidecar integration and no background worker", () => {
  var manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(manifest.permissions, [], "zero permissions");
  assert.equal(manifest.host_permissions, undefined,
    "no host_permissions: the dl-router save button was removed, so nothing " +
    "talks to 127.0.0.1:8791 any more");
  assert.equal(manifest.background, undefined,
    "no background worker: the declared service_worker.js was a 0-byte file");
  assert.deepEqual(manifest.content_scripts[0].js,
    ["embed_enlarge.js", "lightbox.js"], "exactly the two scripts that exist");
});

test("SEAM: lightbox holds no second copy of the media pattern — it uses embed_enlarge's", () => {
  var src = readFileSync(new URL("../extension/lightbox.js", import.meta.url), "utf8");
  assert.ok(!src.includes("discordapp"),
    "a second host pattern here is how one site gets fixed and the other left wrong");
  assert.ok(src.includes("__DEE__"), "it must read the shared definition");
  assert.ok(globalThis.__DEE__ && globalThis.__DEE__.MEDIA_URL_RE instanceof RegExp,
    "and that definition must actually be present at load time");
});

test("REGRESSION: an avatar is not treated as message media", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'>" +
    "<img src='https://cdn.discordapp.com/avatars/1/2/av.png' />" +
    "<img src='" + A1 + "' /></div></div>");
  var real = doc.querySelectorAll("img")[1];
  LB.open(doc, real);
  assert.equal(LB.siblingCount(), 1,
    "the avatar in the same message must not become a navigation sibling");
});

// ===========================================================================
// Coverage added after an adversarial audit found 34 of 43 semantic mutants
// surviving a green suite. Each test below was watched to FAIL against the
// pre-fix source with the mutant it names.
// ===========================================================================

test("REGRESSION: navigating APPLIES the transform, it does not merely reset the number", () => {
  var doc = makeTwoMessageDoc();
  var a1 = doc.querySelector("#msg-a img");
  LB.open(doc, a1);
  LB.setZoom(2);
  LB.navigate(1);
  var shadow = shadowOf(doc);
  var clone = shadow.querySelector(".media-container img");
  assert.equal(clone.style.transform, "scale(1) translate(0px, 0px)",
    "the NEW media must be rendered untransformed — `navigate() minus applyTransform()` " +
    "was a surviving mutant, because nothing ever asserted the transform string");
});

test("setZoom writes the transform, and the scale/translate order is pinned", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  LB.open(doc, doc.querySelector("img"));
  LB.setZoom(1.5);
  var clone = shadowOf(doc).querySelector(".media-container img");
  assert.equal(clone.style.transform, "scale(1.5) translate(0px, 0px)",
    "swapping scale/translate is a distinct transform and used to survive");
});

test("panning divides the delta by the zoom level (not multiplies)", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  LB.open(doc, doc.querySelector("img"));
  LB.setZoom(2);
  LB.handleMouseDown(doc, { clientX: 0, clientY: 0 });
  LB.handleMouseMove(doc, { clientX: 100, clientY: 40 });
  var clone = shadowOf(doc).querySelector(".media-container img");
  assert.equal(clone.style.transform, "scale(2) translate(50px, 20px)",
    "dx/zoom = 50, not dx*zoom = 200 — `/` -> `*` used to survive");
});

test("REGRESSION: opening on a NON-first image starts at that image", () => {
  var doc = makeTwoMessageDoc();
  var a2 = doc.querySelectorAll("#msg-a img")[1];
  LB.open(doc, a2);
  assert.equal(LB.currentSrc(), A2,
    "hardcoding currentIndex = 0 used to survive: every test opened on the first image");
  LB.handleKey(doc, { key: "ArrowRight", preventDefault: function () {} });
  assert.equal(LB.currentSrc(), A1, "and it wraps from there");
});

test("REGRESSION: the message match is a SUBSTRING, so Discord's hashed classes work", () => {
  LB.forget();
  // This is the anti-rot property the source comment and the README both claim.
  // `cls === "message"` used to survive, because no fixture shipped a hashed class.
  var doc = makeDiscordDoc(
    "<div class='message__74e4d cozy__1f3a'><div class='embed'>" +
    "<img src='" + A1 + "' /><img src='" + A2 + "' /></div></div>");
  LB.open(doc, doc.querySelector("img"));
  assert.equal(LB.siblingCount(), 2, "a hashed class must still bound the message");
});

test("REGRESSION: the chat-messages- row id bounds a message too", () => {
  LB.forget();
  // The real client puts this on the <li>; the branch had no coverage at all.
  var doc = makeDiscordDoc(
    "<li id='chat-messages-111-222'><div class='wrapper'>" +
    "<img src='" + A1 + "' /><img src='" + A2 + "' /></div></li>");
  LB.open(doc, doc.querySelector("img"));
  assert.equal(LB.siblingCount(), 2, "id-based bounding must work with no `message` class");
});

test("the message walk depth is bounded — a far-away message row does not bound", () => {
  LB.forget();
  var deep = "";
  var close = "";
  for (var i = 0; i < 20; i++) { deep += "<div class='w" + i + "'>"; close += "</div>"; }
  var doc = makeDiscordDoc(
    "<div class='message'>" + deep +
    "<img src='" + A1 + "' /><img src='" + A2 + "' />" + close + "</div>");
  LB.open(doc, doc.querySelector("img"));
  assert.equal(LB.siblingCount(), 1,
    "beyond MESSAGE_WALK_DEPTH it must fall back to the element itself, not keep climbing");
});

test("the message row binds at a REALISTIC nesting depth (~10 levels up)", () => {
  LB.forget();
  // Pins the LOWER side of MESSAGE_WALK_DEPTH. The 20-level test above pins only
  // the upper side — it falls back to 1 sibling at ANY small depth, so `15 -> 2`
  // survived it in the sweep. Discord nests a message body roughly 6-10 levels
  // under its row, which is the band this constant exists to cover.
  var open10 = "", close10 = "";
  for (var i = 1; i <= 10; i++) { open10 += "<div class='n" + i + "'>"; close10 += "</div>"; }
  var doc = makeDiscordDoc(
    "<div class='message'>" + open10 +
    "<img src='" + A1 + "' /><img src='" + A2 + "' />" + close10 + "</div>");
  LB.open(doc, doc.querySelector("img"));
  assert.equal(LB.siblingCount(), 2,
    "a message row ~11 ancestors up must still bound the group");
});

// --- installAutoStart: the production entry point --------------------------
// Every mutant here survived before, because the fake document's addEventListener
// was a no-op and nothing ever dispatched a real bubbling click.

test("REGRESSION: a click on enlarged media opens the lightbox (real bubbling dispatch)", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'><img src='" + A1 + "' /></div></div>");
  var img = doc.querySelector("img");
  img.setAttribute("data-dee-enlarged", "1");
  LB.installAutoStart(doc);
  var ev = makeClickEvent(img);
  doc.dispatchEvent(ev);
  assert.equal(LB.isOpen(), true, "the click handler must find the marked element");
  assert.equal(ev.defaultPrevented, true, "and suppress the default action");
  assert.equal(ev.__stopped, true,
    "and stop propagation, or Discord's own viewer opens a SECOND lightbox");
});

test("REGRESSION: a click on UNMARKED media does not open the lightbox", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'><img src='" + A1 + "' /></div></div>");
  LB.installAutoStart(doc);
  doc.dispatchEvent(makeClickEvent(doc.querySelector("img")));
  assert.equal(LB.isOpen(), false,
    "dropping the data-dee-enlarged check used to survive");
});

test("REGRESSION: a click on a <video> does NOT open the lightbox", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'><video src='" +
    "https://media.discordapp.net/attachments/1/2/c.mp4' controls></video></div></div>");
  var vid = doc.querySelector("video");
  vid.setAttribute("data-dee-enlarged", "1");
  LB.installAutoStart(doc);
  var ev = makeClickEvent(vid);
  doc.dispatchEvent(ev);
  assert.equal(LB.isOpen(), false,
    "a video's own controls occupy the element — play/scrub must not open a lightbox");
  assert.equal(ev.defaultPrevented, false, "and the click must reach the controls");
});

test("REGRESSION: a modified click is left to the browser", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'><img src='" + A1 + "' /></div></div>");
  var img = doc.querySelector("img");
  img.setAttribute("data-dee-enlarged", "1");
  LB.installAutoStart(doc);
  for (var mod of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { button: 1 }]) {
    LB.forget();
    var ev = makeClickEvent(img, mod);
    doc.dispatchEvent(ev);
    assert.equal(LB.isOpen(), false, "open-in-new-tab / save must still work: " + JSON.stringify(mod));
    assert.equal(ev.defaultPrevented, false);
  }
});

test("a click on a CHILD of enlarged media still opens it (the walk-up works)", () => {
  LB.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed' data-dee-enlarged='1'>" +
    "<img src='" + A1 + "' /></div></div>");
  LB.installAutoStart(doc);
  doc.dispatchEvent(makeClickEvent(doc.querySelector("img")));
  assert.equal(LB.isOpen(), true, "the handler walks up from e.target");
});

test("close() removes its document listeners", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var before = doc.listenerCount("keydown");
  LB.open(doc, doc.querySelector("img"));
  assert.equal(doc.listenerCount("keydown"), before + 1, "open registers one");
  LB.close(doc);
  assert.equal(doc.listenerCount("keydown"), before,
    "close must remove it — leaking one per open used to survive");
});
