import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { makeDiscordDoc, FakeElement } from "./fake_discord_dom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));

const enlargeSource = readFileSync(
  path.join(here, "..", "extension", "embed_enlarge.js"), "utf8");
const enlargeGlobal = { DEE_NO_AUTOSTART: true };
new Function("globalThis", "document", "getComputedStyle", enlargeSource)(
  enlargeGlobal, undefined, undefined);
const DEE = enlargeGlobal.__DEE__;

const lightboxSource = readFileSync(
  path.join(here, "..", "extension", "lightbox.js"), "utf8");
const lightboxGlobal = { DEE_NO_AUTOSTART: true };
new Function("globalThis", "document", lightboxSource)(
  lightboxGlobal, undefined);
const LB = lightboxGlobal.__DEE_LIGHTBOX__;

const SINGLE_IMAGE_HTML = '<html><body>' +
  '<div class="message-abc">' +
  '<div style="max-width:400px"><img src="https://cdn.discordapp.com/attachments/1/2/test.png" /></div>' +
  '</div></body></html>';

const MULTI_IMAGE_HTML = '<html><body>' +
  '<div class="message-abc">' +
  '<div style="max-width:400px"><img src="https://cdn.discordapp.com/attachments/1/2/a.png" /></div>' +
  '<div style="max-width:400px"><img src="https://cdn.discordapp.com/attachments/1/3/b.png" /></div>' +
  '</div></body></html>';

function makeDoc(html) { return makeDiscordDoc(html || SINGLE_IMAGE_HTML); }

function freshDoc(html) {
  LB.forget();
  return makeDoc(html);
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
  assert.equal(typeof LB.forget, "function");
});

test("opening creates a host with a closed shadow root", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  const result = LB.open(doc, img);
  assert.equal(result.ok, true);
  assert.equal(LB.isOpen(), true);
  const host = doc.querySelector("#dee-lightbox-host");
  assert.ok(host);
  assert.equal(host.shadowRoot.mode, "closed");
});

test("opening replaces an existing lightbox (no stacking)", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  LB.open(doc, img);
  assert.equal(doc.querySelectorAll("#dee-lightbox-host").length, 1);
});

test("closing removes the host element", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  assert.equal(LB.close(doc).ok, true);
  assert.equal(LB.isOpen(), false);
  assert.equal(doc.querySelector("#dee-lightbox-host"), null);
});

test("closing when nothing is open returns ok: false", () => {
  const doc = freshDoc();
  const result = LB.close(doc);
  assert.equal(result.ok, false);
  assert.equal(result.error, "no_lightbox");
});

test("Escape closes the lightbox", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const handled = LB.handleKey(doc, { key: "Escape", preventDefault() {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), false);
});

test("Left arrow navigates to previous image", () => {
  const doc = freshDoc(MULTI_IMAGE_HTML);
  const imgs = doc.querySelectorAll("img");
  LB.open(doc, imgs[0]);
  LB.handleKey(doc, { key: "ArrowLeft", preventDefault() {} });
  assert.equal(LB.isOpen(), true);
});

test("Right arrow navigates to next image", () => {
  const doc = freshDoc(MULTI_IMAGE_HTML);
  const imgs = doc.querySelectorAll("img");
  LB.open(doc, imgs[0]);
  LB.handleKey(doc, { key: "ArrowRight", preventDefault() {} });
  assert.equal(LB.isOpen(), true);
});

test("+ key zooms in", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const handled = LB.handleKey(doc, { key: "+", preventDefault() {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("- key zooms out", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const handled = LB.handleKey(doc, { key: "-", preventDefault() {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("0 key resets zoom", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleKey(doc, { key: "+", preventDefault() {} });
  LB.handleKey(doc, { key: "0", preventDefault() {} });
  assert.equal(LB.isOpen(), true);
});

test("unrecognized keys are not handled", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const handled = LB.handleKey(doc, { key: "a", preventDefault() {} });
  assert.equal(handled, false);
});

test("wheel event zooms in on scroll up", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const handled = LB.handleWheel(doc, { deltaY: -100, preventDefault() {} });
  assert.equal(handled, true);
});

test("wheel event zooms out on scroll down", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const handled = LB.handleWheel(doc, { deltaY: 100, preventDefault() {} });
  assert.equal(handled, true);
});

test("mousedown starts a drag", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleMouseDown(doc, { clientX: 100, clientY: 100, preventDefault() {} });
  assert.equal(LB.isOpen(), true);
});

test("mousemove during drag pans the image", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleMouseDown(doc, { clientX: 100, clientY: 100, preventDefault() {} });
  LB.handleMouseMove(doc, { clientX: 150, clientY: 150, preventDefault() {} });
  assert.equal(LB.isOpen(), true);
});

test("mouseup ends a drag", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleMouseDown(doc, { clientX: 100, clientY: 100, preventDefault() {} });
  LB.handleMouseUp(doc, {});
  assert.equal(LB.isOpen(), true);
});

test("the lightbox displays the media at native resolution", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const host = doc.querySelector("#dee-lightbox-host");
  const displayed = host.shadowRoot.querySelector("img");
  assert.ok(displayed);
  assert.equal(displayed.getAttribute("src"), img.getAttribute("src"));
});

test("the lightbox shows a semi-transparent backdrop", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  LB.open(doc, img);
  const host = doc.querySelector("#dee-lightbox-host");
  const backdrop = host.shadowRoot.querySelector(".backdrop");
  assert.ok(backdrop);
});

test("the lightbox shows nav arrows for multi-image messages", () => {
  const doc = freshDoc(MULTI_IMAGE_HTML);
  const imgs = doc.querySelectorAll("img");
  LB.open(doc, imgs[0]);
  const host = doc.querySelector("#dee-lightbox-host");
  const navPrev = host.shadowRoot.querySelector(".nav-prev");
  const navNext = host.shadowRoot.querySelector(".nav-next");
  assert.ok(navPrev);
  assert.ok(navNext);
});

test("the lightbox script reimplements none of embed_enlarge", () => {
  const src = readFileSync(new URL("../extension/lightbox.js", import.meta.url), "utf8");
  for (const forbidden of ["isMediaElement", "findContainer", "applyOverride"]) {
    assert.equal(src.includes(forbidden), false, "lightbox.js must not contain " + forbidden);
  }
});
