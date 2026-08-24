import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { FakeElement, makeDiscordDoc } from "./fake_discord_dom.mjs";

globalThis.DEE_NO_AUTOSTART = true;
await import("../extension/save_button.js");
const SAVE = globalThis.__DEE_SAVE__;

test("the module exposes its pure functions", () => {
  assert.equal(typeof SAVE.checkSidecar, "function");
  assert.equal(typeof SAVE.mountSaveButton, "function");
  assert.equal(typeof SAVE.unmountAll, "function");
  assert.equal(typeof SAVE.forget, "function");
});

test("checkSidecar is a function", () => {
  assert.equal(typeof SAVE.checkSidecar, "function");
  var result = SAVE.checkSidecar();
  assert.ok(result && typeof result.then === "function", "returns a promise");
});

test("mountSaveButton creates a button on the container", () => {
  var doc = makeDiscordDoc("<div class='msg'><img src='https://cdn.discordapp.com/attachments/1/2/p.png' /></div>");
  var container = doc.querySelector(".msg");
  var mediaEl = doc.querySelector("img");
  SAVE.mountSaveButton(container, mediaEl, "12345");
  var btn = container.querySelector(".dee-save-btn");
  assert.ok(btn, "button created");
  assert.equal(btn.tagName, "button");
});

test("mountSaveButton button has correct text and class", () => {
  var doc = makeDiscordDoc("<div class='msg'><img src='https://cdn.discordapp.com/attachments/1/2/p.png' /></div>");
  var container = doc.querySelector(".msg");
  var mediaEl = doc.querySelector("img");
  SAVE.mountSaveButton(container, mediaEl, "12345");
  var btn = container.querySelector(".dee-save-btn");
  assert.equal(btn.textContent, "Save");
  assert.equal(btn.getAttribute("class"), "dee-save-btn");
});

test("unmountAll removes all save buttons", () => {
  var doc = makeDiscordDoc("<div class='msg1'><img src='https://cdn.discordapp.com/attachments/1/2/p.png' /></div><div class='msg2'><img src='https://cdn.discordapp.com/attachments/3/4/q.png' /></div>");
  var c1 = doc.querySelector(".msg1");
  var c2 = doc.querySelector(".msg2");
  SAVE.mountSaveButton(c1, doc.querySelector(".msg1 img"), "111");
  SAVE.mountSaveButton(c2, doc.querySelector(".msg2 img"), "222");
  assert.ok(c1.querySelector(".dee-save-btn"), "btn1 exists before");
  assert.ok(c2.querySelector(".dee-save-btn"), "btn2 exists before");
  SAVE.unmountAll(doc);
  assert.equal(c1.querySelector(".dee-save-btn"), null, "btn1 removed");
  assert.equal(c2.querySelector(".dee-save-btn"), null, "btn2 removed");
});

test("unmountAll on empty doc is safe", () => {
  var doc = makeDiscordDoc("<div></div>");
  SAVE.unmountAll(doc);
  assert.ok(true, "no throw");
});

test("the manifest grants host_permissions for the sidecar only", () => {
  var manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1:8791/*"]);
});

test("the manifest has zero permissions", () => {
  var manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(manifest.permissions, []);
});
