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

const saveSource = readFileSync(
  path.join(here, "..", "extension", "save_button.js"), "utf8");
const saveGlobal = { DEE_NO_AUTOSTART: true };
new Function("globalThis", "document", "location", saveSource)(
  saveGlobal, undefined, undefined);
const SAVE = saveGlobal.__DEE_SAVE__;

function freshDoc() {
  SAVE.forget();
  return makeDiscordDoc('<html><body>' +
    '<div class="message-abc">' +
    '<div style="max-width:400px"><img src="https://cdn.discordapp.com/attachments/1/2/test.png" /></div>' +
    '</div></body></html>');
}

test("the module exposes its pure functions", () => {
  assert.ok(SAVE);
  for (const fn of ["checkSidecar", "mountSaveButton", "unmountAll", "forget"]) {
    assert.equal(typeof SAVE[fn], "function", fn);
  }
});

test("checkSidecar is a function returning a promise", () => {
  const result = SAVE.checkSidecar();
  assert.ok(result && typeof result.then === "function");
  return result.catch(function () {});
});

test("mountSaveButton creates a button on the container", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  const container = img.parentElement;
  SAVE.mountSaveButton(container, img, "123456789", doc);
  const btn = container.querySelector(".dee-save-btn");
  assert.ok(btn);
  assert.equal(btn.textContent, "Save");
});

test("mountSaveButton button has correct class", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  const container = img.parentElement;
  SAVE.mountSaveButton(container, img, "123456789", doc);
  const btn = container.querySelector(".dee-save-btn");
  assert.ok(btn.classList.contains("dee-save-btn"));
});

test("mountSaveButton is idempotent (no duplicate buttons)", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  const container = img.parentElement;
  SAVE.mountSaveButton(container, img, "123456789", doc);
  SAVE.mountSaveButton(container, img, "123456789", doc);
  const btns = container.querySelectorAll(".dee-save-btn");
  assert.equal(btns.length, 1);
});

test("unmountAll removes all save buttons", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  const container = img.parentElement;
  SAVE.mountSaveButton(container, img, "123456789", doc);
  SAVE.unmountAll(doc);
  assert.equal(container.querySelector(".dee-save-btn"), null);
});

test("unmountAll on empty doc is safe", () => {
  const doc = makeDiscordDoc("<html><body></body></html>");
  SAVE.unmountAll(doc);
  assert.ok(true);
});

test("forget resets state and removes buttons", () => {
  const doc = freshDoc();
  const img = doc.querySelector("img");
  const container = img.parentElement;
  SAVE.mountSaveButton(container, img, "123456789", doc);
  SAVE.forget(doc);
  assert.equal(container.querySelector(".dee-save-btn"), null);
});

test("the manifest grants host_permissions for the sidecar only", () => {
  const manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"
  ));
  assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1:8791/*"]);
});

test("the manifest has zero permissions", () => {
  const manifest = JSON.parse(readFileSync(
    new URL("../extension/manifest.json", import.meta.url), "utf8"
  ));
  assert.deepEqual(manifest.permissions, []);
});

test("the save button script reimplements none of embed_enlarge", () => {
  const src = readFileSync(new URL("../extension/save_button.js", import.meta.url), "utf8");
  for (const forbidden of ["isMediaElement", "findContainer", "applyOverride"]) {
    assert.equal(src.includes(forbidden), false, "save_button.js must not contain " + forbidden);
  }
});
