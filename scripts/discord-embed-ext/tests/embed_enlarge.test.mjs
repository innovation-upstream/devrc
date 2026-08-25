import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { makeDiscordDoc, FakeElement } from "./fake_discord_dom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(
  path.join(here, "..", "extension", "embed_enlarge.js"), "utf8");

const fakeGlobal = { DEE_NO_AUTOSTART: true };
new Function("globalThis", "document", source)(fakeGlobal, undefined);

const DEE = fakeGlobal.__DEE__;

function makeDoc(html) {
  return makeDiscordDoc(html || '<html><body></body></html>');
}

function makeEl(tag, attrs, children) {
  return new FakeElement(tag, attrs || {}, children || []);
}

test("the module exposes its pure functions and starts nothing", () => {
  assert.ok(DEE);
  for (const fn of ["isDiscordMedia", "isMediaElement", "injectStylesheet",
    "markMediaElements", "extractChannelId", "observe", "forget"]) {
    assert.equal(typeof DEE[fn], "function", fn);
  }
});

test("MEDIA_URL_RE matches attachment URLs", () => {
  assert.ok(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/attachments/123/a/b.png"));
  assert.ok(DEE.MEDIA_URL_RE.test("https://media.discordapp.net/attachments/123/a/b.png"));
  assert.ok(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/embeds/123/a/b.png"));
});

test("MEDIA_URL_RE rejects emoji URLs", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/emojis/12345.png"));
  assert.ok(!DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/emojis/12345.gif"));
});

test("MEDIA_URL_RE rejects sticker URLs", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/stickers/12345.png"));
});

test("MEDIA_URL_RE rejects non-Discord URLs", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test("https://example.com/photo.jpg"));
  assert.ok(!DEE.MEDIA_URL_RE.test(""));
  assert.ok(!DEE.MEDIA_URL_RE.test(null));
});

test("EMOJI_RE matches emoji CDN paths", () => {
  assert.ok(DEE.EMOJI_RE.test("https://cdn.discordapp.com/emojis/123.png"));
  assert.ok(!DEE.EMOJI_RE.test("https://cdn.discordapp.com/attachments/123.png"));
});

test("STICKER_RE matches sticker CDN paths", () => {
  assert.ok(DEE.STICKER_RE.test("https://cdn.discordapp.com/stickers/123.png"));
  assert.ok(!DEE.STICKER_RE.test("https://cdn.discordapp.com/attachments/123.png"));
});

test("isDiscordMedia returns true for attachments", () => {
  assert.ok(DEE.isDiscordMedia("https://cdn.discordapp.com/attachments/123/a/b.png"));
  assert.ok(DEE.isDiscordMedia("https://media.discordapp.net/attachments/123/a/b.mp4"));
});

test("isDiscordMedia returns false for emojis", () => {
  assert.ok(!DEE.isDiscordMedia("https://cdn.discordapp.com/emojis/123.png"));
});

test("isDiscordMedia returns false for stickers", () => {
  assert.ok(!DEE.isDiscordMedia("https://cdn.discordapp.com/stickers/123.png"));
});

test("isDiscordMedia returns false for non-Discord", () => {
  assert.ok(!DEE.isDiscordMedia("https://example.com/photo.jpg"));
  assert.ok(!DEE.isDiscordMedia(null));
  assert.ok(!DEE.isDiscordMedia(""));
});

test("isMediaElement identifies Discord images", () => {
  const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
  const result = DEE.isMediaElement(img);
  assert.equal(result.isMedia, true);
  assert.equal(result.element, img);
});

test("isMediaElement identifies Discord videos", () => {
  const vid = makeEl("video", { src: "https://media.discordapp.net/attachments/123/a/b.mp4" });
  const result = DEE.isMediaElement(vid);
  assert.equal(result.isMedia, true);
  assert.equal(result.element, vid);
});

test("isMediaElement identifies video with source child", () => {
  const source = makeEl("source", { src: "https://cdn.discordapp.com/attachments/123/a/b.mp4" });
  const vid = makeEl("video", {}, [source]);
  const result = DEE.isMediaElement(vid);
  assert.equal(result.isMedia, true);
  assert.equal(result.element, vid);
});

test("isMediaElement rejects emoji images", () => {
  const img = makeEl("img", { src: "https://cdn.discordapp.com/emojis/123.png" });
  const result = DEE.isMediaElement(img);
  assert.equal(result.isMedia, false);
});

test("isMediaElement rejects non-Discord images", () => {
  const img = makeEl("img", { src: "https://example.com/photo.jpg" });
  const result = DEE.isMediaElement(img);
  assert.equal(result.isMedia, false);
});

test("isMediaElement rejects divs", () => {
  const div = makeEl("div", {});
  const result = DEE.isMediaElement(div);
  assert.equal(result.isMedia, false);
});

test("isMediaElement handles null", () => {
  assert.equal(DEE.isMediaElement(null).isMedia, false);
});

test("ENLARGE_CSS contains selectors for attachments", () => {
  assert.match(DEE.ENLARGE_CSS, /cdn\.discordapp\.com\/attachments\//);
  assert.match(DEE.ENLARGE_CSS, /media\.discordapp\.net\/attachments\//);
});

test("ENLARGE_CSS contains !important overrides", () => {
  assert.match(DEE.ENLARGE_CSS, /max-width:\s*none\s*!important/);
  assert.match(DEE.ENLARGE_CSS, /max-height:\s*none\s*!important/);
  assert.match(DEE.ENLARGE_CSS, /object-fit:\s*contain\s*!important/);
});

test("ENLARGE_CSS targets embed container wrappers", () => {
  assert.match(DEE.ENLARGE_CSS, /\[class\*='imageWrapper'\]/);
  assert.match(DEE.ENLARGE_CSS, /\[class\*='mosaicItem'\]/);
});

test("ENLARGE_CSS does not target emojis", () => {
  assert.ok(!DEE.ENLARGE_CSS.includes("/emojis/"));
  assert.ok(!DEE.ENLARGE_CSS.includes("/stickers/"));
});

test("injectStylesheet creates a style element", () => {
  const doc = makeDoc("<html><head></head><body></body></html>");
  DEE.injectStylesheet(doc);
  const style = doc.querySelector("#dee-enlarge-css");
  assert.ok(style);
  assert.equal(style.tagName.toLowerCase(), "style");
});

test("injectStylesheet is idempotent", () => {
  const doc = makeDoc("<html><head></head><body></body></html>");
  DEE.injectStylesheet(doc);
  DEE.injectStylesheet(doc);
  assert.equal(doc.querySelectorAll("#dee-enlarge-css").length, 1);
});

test("markMediaElements marks Discord images", () => {
  const doc = makeDoc('<html><body>' +
    '<img src="https://cdn.discordapp.com/attachments/123/a/b.png" />' +
    '</body></html>');
  const count = DEE.markMediaElements(doc);
  assert.equal(count, 1);
  const img = doc.querySelector("img");
  assert.equal(img.getAttribute("data-dee-enlarged"), "1");
});

test("markMediaElements sets cursor: zoom-in", () => {
  const doc = makeDoc('<html><body>' +
    '<img src="https://cdn.discordapp.com/attachments/123/a/b.png" />' +
    '</body></html>');
  DEE.markMediaElements(doc);
  const img = doc.querySelector("img");
  assert.match(img.style.getPropertyValue("cursor"), /zoom-in/);
});

test("markMediaElements skips emojis", () => {
  const doc = makeDoc('<html><body>' +
    '<img src="https://cdn.discordapp.com/emojis/123.png" />' +
    '</body></html>');
  const count = DEE.markMediaElements(doc);
  assert.equal(count, 0);
});

test("markMediaElements is idempotent", () => {
  const doc = makeDoc('<html><body>' +
    '<img src="https://cdn.discordapp.com/attachments/123/a/b.png" />' +
    '</body></html>');
  const count1 = DEE.markMediaElements(doc);
  const count2 = DEE.markMediaElements(doc);
  assert.equal(count1, 1);
  assert.equal(count2, 0);
});

test("markMediaElements counts multiple images", () => {
  const doc = makeDoc('<html><body>' +
    '<img src="https://cdn.discordapp.com/attachments/1/2/a.png" />' +
    '<img src="https://cdn.discordapp.com/attachments/3/4/b.png" />' +
    '<img src="https://cdn.discordapp.com/emojis/5.png" />' +
    '</body></html>');
  const count = DEE.markMediaElements(doc);
  assert.equal(count, 2);
});

test("extractChannelId parses @me channel", () => {
  assert.equal(DEE.extractChannelId("https://discord.com/channels/@me/123456789/987654321"), "123456789");
});

test("extractChannelId parses guild channel", () => {
  assert.equal(DEE.extractChannelId("https://discord.com/channels/987654321/123456789/987654321"), "987654321");
});

test("extractChannelId returns null for non-channel URLs", () => {
  assert.equal(DEE.extractChannelId("https://discord.com/channels/@me"), null);
  assert.equal(DEE.extractChannelId("https://discord.com/app"), null);
});

test("extractChannelId returns null for non-strings", () => {
  assert.equal(DEE.extractChannelId(null), null);
  assert.equal(DEE.extractChannelId(undefined), null);
});

test("forget removes stylesheet and attributes", () => {
  const doc = makeDoc("<html><head></head><body>" +
    '<img src="https://cdn.discordapp.com/attachments/123/a/b.png" />' +
    "</body></html>");
  DEE.injectStylesheet(doc);
  DEE.markMediaElements(doc);
  assert.ok(doc.querySelector("#dee-enlarge-css"));
  assert.ok(doc.querySelector("[data-dee-enlarged]"));
  DEE.forget(doc);
  assert.equal(doc.querySelector("#dee-enlarge-css"), null);
  assert.equal(doc.querySelector("[data-dee-enlarged]"), null);
});

test("the manifest targets only discord.com domains", () => {
  const manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"
  ));
  const [cs] = manifest.content_scripts;
  assert.deepEqual(cs.matches, ["https://discord.com/*", "https://*.discord.com/*"]);
  assert.deepEqual(cs.js, ["embed_enlarge.js", "lightbox.js", "save_button.js"]);
  assert.equal(cs.run_at, "document_idle");
});

test("the manifest has zero permissions", () => {
  const manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"
  ));
  assert.deepEqual(manifest.permissions, []);
});
