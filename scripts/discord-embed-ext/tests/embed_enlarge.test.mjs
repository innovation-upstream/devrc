import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { FakeElement, FakeComputedStyle, makeDiscordDoc } from "./fake_discord_dom.mjs";

globalThis.DEE_NO_AUTOSTART = true;
globalThis.__DEE_GET_COMPUTED_STYLE__ = function (el) {
  var styleAttr = el.getAttribute("style");
  if (styleAttr && typeof styleAttr === "string") {
    var cs = new FakeComputedStyle();
    var parts = styleAttr.split(";");
    for (var i = 0; i < parts.length; i++) {
      var trimmed = parts[i].trim();
      if (!trimmed) continue;
      var colonIdx = trimmed.indexOf(":");
      if (colonIdx < 0) continue;
      var prop = trimmed.slice(0, colonIdx).trim();
      var val = trimmed.slice(colonIdx + 1).trim();
      if (prop && val) cs.setProperty(prop, val);
    }
    return cs;
  }
  return el.style;
};
await import("../extension/embed_enlarge.js");
const DEE = globalThis.__DEE__;

const fixture = readFileSync(new URL("./discord_embeds.html", import.meta.url), "utf8");

function freshDoc() {
  DEE.forget();
  return makeDiscordDoc(fixture);
}

test("the module exposes its pure functions and starts nothing when autostart suppressed", () => {
  assert.equal(typeof DEE.isMediaElement, "function");
  assert.equal(typeof DEE.findContainer, "function");
  assert.equal(typeof DEE.applyOverride, "function");
  assert.equal(typeof DEE.scan, "function");
  assert.equal(typeof DEE.forget, "function");
  assert.equal(typeof DEE.observe, "function");
  assert.ok(DEE.MEDIA_URL_RE instanceof RegExp);
});

test("MEDIA_URL_RE matches cdn.discordapp.com URLs (https, with query params)", () => {
  assert.ok(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/attachments/123/456/photo.png?v=1"));
  assert.ok(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/attachments/100/200/a.jpg"));
});

test("MEDIA_URL_RE matches cdn.discordapp.com URLs (http)", () => {
  assert.ok(DEE.MEDIA_URL_RE.test("http://cdn.discordapp.com/attachments/123/456/photo.png"));
});

test("MEDIA_URL_RE matches media.discordapp.net URLs", () => {
  assert.ok(DEE.MEDIA_URL_RE.test("https://media.discordapp.net/attachments/123/789/clip.mp4"));
  assert.ok(DEE.MEDIA_URL_RE.test("http://media.discordapp.net/attachments/100/500/vid.webm"));
});

test("MEDIA_URL_RE rejects non-Discord URLs", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test("https://example.com/image.png"));
  assert.ok(!DEE.MEDIA_URL_RE.test("https://pbs.twimg.com/media/foo.jpg"));
});

test("MEDIA_URL_RE rejects empty strings and null", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test(""));
  assert.ok(!DEE.MEDIA_URL_RE.test(null));
  assert.ok(!DEE.MEDIA_URL_RE.test(undefined));
});

test("isMediaElement identifies Discord images", () => {
  var el = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  var result = DEE.isMediaElement(el);
  assert.equal(result.isMedia, true);
  assert.equal(result.element, el);
});

test("isMediaElement identifies Discord videos", () => {
  var el = new FakeElement("video", { src: "https://media.discordapp.net/attachments/1/2/v.mp4" });
  var result = DEE.isMediaElement(el);
  assert.equal(result.isMedia, true);
  assert.equal(result.element, el);
});

test("isMediaElement identifies video with source child", () => {
  var video = new FakeElement("video", {});
  var source = new FakeElement("source", { src: "https://media.discordapp.net/attachments/1/2/v.mp4" });
  video.appendChild(source);
  var result = DEE.isMediaElement(video);
  assert.equal(result.isMedia, true);
  assert.equal(result.element, video);
});

test("isMediaElement rejects non-Discord images", () => {
  var el = new FakeElement("img", { src: "https://example.com/image.png" });
  var result = DEE.isMediaElement(el);
  assert.equal(result.isMedia, false);
});

test("isMediaElement rejects divs without background image", () => {
  var el = new FakeElement("div", { class: "message" });
  var result = DEE.isMediaElement(el);
  assert.equal(result.isMedia, false);
});

test("findContainer walks up to find max-width constraint", () => {
  var doc = freshDoc();
  var img = doc.querySelector("#msg-img img");
  assert.ok(img, "fixture has img");
  var container = DEE.findContainer(img);
  assert.ok(container, "found a constrainer");
  assert.equal(container.getAttribute("class"), "embed");
});

test("findContainer walks up to find max-height constraint", () => {
  var wrapper = new FakeElement("div", { style: "" });
  wrapper.style.setProperty("max-height", "300px");
  var inner = new FakeElement("div", {});
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  inner.appendChild(img);
  wrapper.appendChild(inner);
  var container = DEE.findContainer(img);
  assert.ok(container, "found a constrainer");
  assert.equal(container, wrapper);
});

test("findContainer returns null when no constrainer exists", () => {
  var wrapper = new FakeElement("div", {});
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  wrapper.appendChild(img);
  var container = DEE.findContainer(img);
  assert.equal(container, null);
});

test("findContainer caps walk depth at MAX_WALK_DEPTH", () => {
  var deep = new FakeElement("div", { class: "embed" });
  deep.style.setProperty("max-width", "400px");
  var node = deep;
  for (var i = 0; i < 10; i++) {
    var wrapper = new FakeElement("div", {});
    wrapper.appendChild(node);
    node = wrapper;
  }
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  node.appendChild(img);
  var container = DEE.findContainer(img);
  assert.equal(container, null, "constrainer is too deep to reach");
});

test("findContainer stops at first constrainer (nearest wins)", () => {
  var outer = new FakeElement("div", { class: "outer" });
  outer.style.setProperty("max-width", "300px");
  var inner = new FakeElement("div", { class: "inner" });
  inner.style.setProperty("max-width", "200px");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  inner.appendChild(img);
  outer.appendChild(inner);
  var container = DEE.findContainer(img);
  assert.equal(container, inner, "nearest constrainer wins");
});

test("applyOverride removes max-width and max-height constraints", () => {
  var container = new FakeElement("div", { class: "embed" });
  container.style.setProperty("max-width", "400px", "important");
  container.style.setProperty("max-height", "300px", "important");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  container.appendChild(img);
  var result = DEE.applyOverride(img);
  assert.equal(result.ok, true);
  assert.equal(result.removed, true);
  assert.equal(container.style.getPropertyValue("max-width"), "none");
  assert.equal(container.style.getPropertyValue("max-height"), "none");
});

test("applyOverride sets cursor: zoom-in on media", () => {
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  DEE.applyOverride(img);
  assert.equal(img.style.getPropertyValue("cursor"), "zoom-in");
  assert.equal(img.getAttribute("data-dee-enlarged"), "1");
});

test("applyOverride is idempotent (skips already enlarged)", () => {
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  img.setAttribute("data-dee-enlarged", "1");
  var result = DEE.applyOverride(img);
  assert.equal(result.ok, true);
  assert.equal(result.removed, false);
});

test("applyOverride returns removed:false when no constraints present", () => {
  var wrapper = new FakeElement("div", {});
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  wrapper.appendChild(img);
  var result = DEE.applyOverride(img);
  assert.equal(result.ok, true);
  assert.equal(result.removed, false);
});

test("scan finds and overrides all Discord media in a fixture", () => {
  var doc = freshDoc();
  var count = DEE.scan(doc.body);
  assert.ok(count > 0, "scan found media");
  var img1 = doc.querySelector("#msg-img img");
  assert.equal(img1.getAttribute("data-dee-enlarged"), "1", "first image enlarged");
  var vid = doc.querySelector("#msg-video video");
  assert.equal(vid.getAttribute("data-dee-enlarged"), "1", "video enlarged");
});

test("scan is idempotent (second scan processes 0 new)", () => {
  var doc = freshDoc();
  DEE.scan(doc.body);
  var count2 = DEE.scan(doc.body);
  assert.ok(count2 >= 0, "second scan completes");
  var img = doc.querySelector("#msg-img img");
  assert.equal(img.getAttribute("data-dee-enlarged"), "1");
});

test("scan skips non-Discord images", () => {
  var doc = freshDoc();
  DEE.scan(doc.body);
  var ext = doc.querySelector("#msg-external img");
  assert.equal(ext.getAttribute("data-dee-enlarged"), null, "external image not enlarged");
});

test("forget resets all data-dee-enlarged attributes", () => {
  var doc = freshDoc();
  DEE.scan(doc.body);
  var img = doc.querySelector("#msg-img img");
  assert.equal(img.getAttribute("data-dee-enlarged"), "1");
  DEE.forget(doc);
  assert.equal(img.getAttribute("data-dee-enlarged"), null);
});

test("the manifest targets only discord.com domains", () => {
  var manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(manifest.content_scripts[0].matches,
    ["https://discord.com/*", "https://*.discord.com/*"]);
  assert.deepEqual(manifest.permissions, []);
  assert.equal(manifest.host_permissions, undefined,
    "the sidecar save button was removed, so the extension reaches no host at all");
});

test("the script exposes pure functions and starts nothing when autostart suppressed", () => {
  assert.equal(typeof DEE.isMediaElement, "function");
  assert.equal(typeof DEE.findContainer, "function");
  assert.equal(typeof DEE.applyOverride, "function");
  assert.equal(typeof DEE.scan, "function");
  assert.equal(typeof DEE.forget, "function");
  assert.equal(typeof DEE.observe, "function");
});

// 🔴 MEASURED ON THE REAL CLIENT, NOT IMAGINED. A logged-in channel on 2026-08-24
// served 59 host-matching <img>/<video>: avatars 24, icons 35, attachments 0 — and
// 10 of those avatars sat in a 196px-capped container the old host-only pattern
// would have overridden. The fixture could never surface this: every URL in it is
// already an attachment. These are the paths the CDN serves that are NOT message
// media. Ids here are fabricated.
const NON_MEDIA_PATHS = [
  "https://cdn.discordapp.com/avatars/111/222/avatar.png",
  "https://cdn.discordapp.com/icons/111/222/icon.png",
  "https://cdn.discordapp.com/emojis/333.png",
  "https://cdn.discordapp.com/stickers/444.png",
  "https://cdn.discordapp.com/banners/111/555.png",
  "https://cdn.discordapp.com/role-icons/111/666.png",
  // These two were NOT guessed — a second real channel served them, and both are
  // chrome: the /media/ items rendered 48x48 and the clan badges 14x14.
  "https://cdn.discordapp.com/media/ab/abcdefghijklmnopq/1234567890123456789/x.webp",
  "https://cdn.discordapp.com/clan-badges/1234567890123456789/badge.png",
];

test("REGRESSION: avatars, icons, emojis, stickers and banners are NOT message media", () => {
  for (var u of NON_MEDIA_PATHS) {
    assert.equal(DEE.MEDIA_URL_RE.test(u), false, u + " must not match");
  }
});

test("REGRESSION: an avatar in a capped container is left alone by scan", () => {
  DEE.forget();
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed' style='max-width:196px;'>" +
    "<img src='https://cdn.discordapp.com/avatars/111/222/avatar.png' /></div></div>");
  DEE.scan(doc.body);
  var av = doc.querySelector("img");
  assert.equal(av.getAttribute("data-dee-enlarged"), null,
    "a 196px-capped avatar is chrome, not an embed — 10 of these were on screen");
});

test("attachments and the external media proxy DO match", () => {
  assert.equal(DEE.MEDIA_URL_RE.test(
    "https://cdn.discordapp.com/attachments/1/2/photo.png?ex=abc"), true);
  assert.equal(DEE.MEDIA_URL_RE.test(
    "https://media.discordapp.net/attachments/1/2/clip.mp4"), true);
  assert.equal(DEE.MEDIA_URL_RE.test(
    "https://media.discordapp.net/external/hash/https/example.com/i.png"), true);
});

test("a bare CDN host with no path does not match", () => {
  assert.equal(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/"), false);
  assert.equal(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/attachments/"), false);
});
