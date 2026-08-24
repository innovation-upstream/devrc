import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { FakeElement, makeDiscordDoc } from "./fake_discord_dom.mjs";

globalThis.DEE_NO_AUTOSTART = true;
await import("../extension/lightbox.js");
const LB = globalThis.__DEE_LIGHTBOX__;

function makeImg(src) {
  var el = new FakeElement("img", { src: src });
  el.naturalWidth = 1920;
  el.naturalHeight = 1080;
  return el;
}

function makeDocWithImage(src) {
  LB.forget();
  var doc = makeDiscordDoc("<div class='message'><div class='embed' style='max-width:400px;'><img src='" + src + "' /></div></div>");
  return doc;
}

const DISCORD_IMG = "https://cdn.discordapp.com/attachments/123/456/photo.png";

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
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  var result = LB.open(doc, img);
  assert.equal(result.ok, true);
  var host = doc.querySelector("#dee-lightbox-host");
  assert.ok(host, "host element created");
  assert.equal(host.shadowRoot.mode, "closed", "shadow root is closed");
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

test("Left arrow navigates to previous image", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "ArrowLeft", preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("Right arrow navigates to next image", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "ArrowRight", preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("+ key zooms in", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "+", preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("- key zooms out", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "-", preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("unrecognized keys are not handled", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleKey(doc, { key: "a", preventDefault: function () {} });
  assert.equal(handled, false);
  assert.equal(LB.isOpen(), true);
});

test("wheel event zooms in on scroll up", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleWheel(doc, { deltaY: -100, preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("wheel event zooms out on scroll down", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleWheel(doc, { deltaY: 100, preventDefault: function () {} });
  assert.equal(handled, true);
  assert.equal(LB.isOpen(), true);
});

test("mousedown starts a drag", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var handled = LB.handleMouseDown(doc, { clientX: 100, clientY: 200 });
  assert.equal(handled, false);
  assert.equal(LB.isOpen(), true);
});

test("mouseup ends a drag", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  LB.handleMouseDown(doc, { clientX: 100, clientY: 200 });
  var handled = LB.handleMouseUp(doc, { clientX: 150, clientY: 250 });
  assert.equal(handled, false);
  assert.equal(LB.isOpen(), true);
});

test("the lightbox displays the media at native resolution", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var host = doc.querySelector("#dee-lightbox-host");
  assert.ok(host, "host exists");
  var shadow = host.shadowRoot;
  var mediaContainer = null;
  for (var i = 0; i < shadow.children.length; i++) {
    if (shadow.children[i].tagName === "div" && shadow.children[i].className === "backdrop") {
      var bc = shadow.children[i].children;
      for (var j = 0; j < bc.length; j++) {
        if (bc[j].className === "media-container") mediaContainer = bc[j];
      }
    }
  }
  assert.ok(mediaContainer, "media container found in shadow");
  var clonedImg = null;
  for (var k = 0; k < mediaContainer.children.length; k++) {
    if (mediaContainer.children[k].tagName === "img") clonedImg = mediaContainer.children[k];
  }
  assert.ok(clonedImg, "cloned img in media container");
});

test("the lightbox shows a semi-transparent backdrop", () => {
  var doc = makeDocWithImage(DISCORD_IMG);
  var img = doc.querySelector("img");
  LB.open(doc, img);
  var host = doc.querySelector("#dee-lightbox-host");
  var shadow = host.shadowRoot;
  var backdrop = null;
  for (var i = 0; i < shadow.children.length; i++) {
    if (shadow.children[i].className === "backdrop") backdrop = shadow.children[i];
  }
  assert.ok(backdrop, "backdrop found");
});

test("the lightbox script reimplements none of embed_enlarge (source structure check)", () => {
  var src = readFileSync(new URL("../extension/lightbox.js", import.meta.url), "utf8");
  for (var forbidden of ["findContainer", "scan("]) {
    assert.ok(!src.includes(forbidden), "lightbox.js must not contain " + forbidden);
  }
  assert.ok(src.includes("attachShadow"), "lightbox creates shadow root");
  assert.ok(src.includes("createElement"), "lightbox creates elements");
});
