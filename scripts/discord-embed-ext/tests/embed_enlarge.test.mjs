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
new Function("globalThis", "document", "getComputedStyle", source)(
  fakeGlobal, undefined, undefined);

const DEE = fakeGlobal.__DEE__;

const FIXTURE_HTML = readFileSync(
  path.join(here, "fixtures", "discord_embeds.html"), "utf8");

function makeDoc(html) {
  return makeDiscordDoc(html || FIXTURE_HTML);
}

function makeEl(tag, attrs, children) {
  return new FakeElement(tag, attrs || {}, children || []);
}

function withMockCS(fn) {
  fakeGlobal.__DEE_GET_COMPUTED_STYLE__ = (el) => el.style;
  try { fn(); } finally { delete fakeGlobal.__DEE_GET_COMPUTED_STYLE__; }
}

test("the module exposes its pure functions and starts nothing", () => {
  assert.ok(DEE);
  for (const fn of ["isMediaElement", "findContainer", "applyOverride", "scan",
    "extractChannelId", "observe", "forget"]) {
    assert.equal(typeof DEE[fn], "function", fn);
  }
});

test("MEDIA_URL_RE matches cdn.discordapp.com URLs", () => {
  assert.ok(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/attachments/123/a/b.png"));
  assert.ok(DEE.MEDIA_URL_RE.test("https://cdn.discordapp.com/attachments/123/a/b.png?width=400"));
  assert.ok(DEE.MEDIA_URL_RE.test("http://cdn.discordapp.com/attachments/123/a/b.png"));
});

test("MEDIA_URL_RE matches media.discordapp.net URLs", () => {
  assert.ok(DEE.MEDIA_URL_RE.test("https://media.discordapp.net/attachments/123/a/b.png"));
  assert.ok(DEE.MEDIA_URL_RE.test("https://media.discordapp.net/attachments/123/a/b.mp4?width=400&height=300"));
});

test("MEDIA_URL_RE rejects non-Discord URLs", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test("https://example.com/photo.jpg"));
  assert.ok(!DEE.MEDIA_URL_RE.test("https://cdn.notdiscord.com/attachments/123/a/b.png"));
});

test("MEDIA_URL_RE rejects empty and null", () => {
  assert.ok(!DEE.MEDIA_URL_RE.test(""));
  assert.ok(!DEE.MEDIA_URL_RE.test(null));
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

test("isMediaElement rejects non-Discord images", () => {
  const img = makeEl("img", { src: "https://example.com/photo.jpg" });
  const result = DEE.isMediaElement(img);
  assert.equal(result.isMedia, false);
});

test("isMediaElement rejects divs without matching src", () => {
  const div = makeEl("div", {});
  const result = DEE.isMediaElement(div);
  assert.equal(result.isMedia, false);
});

test("isMediaElement handles null and elements without tagName", () => {
  assert.equal(DEE.isMediaElement(null).isMedia, false);
  assert.equal(DEE.isMediaElement({}).isMedia, false);
});

test("findContainer walks up to find max-width constraint", () => {
  withMockCS(() => {
    const grandparent = makeEl("div", {});
    grandparent.style.setProperty("max-width", "400px");
    const parent = makeEl("div", {});
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    parent.appendChild(img);
    grandparent.appendChild(parent);
    const found = DEE.findContainer(img);
    assert.equal(found, grandparent);
  });
});

test("findContainer walks up to find max-height constraint", () => {
  withMockCS(() => {
    const container = makeEl("div", {});
    container.style.setProperty("max-height", "300px");
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    container.appendChild(img);
    const found = DEE.findContainer(img);
    assert.equal(found, container);
  });
});

test("findContainer returns null when no constrainer exists", () => {
  withMockCS(() => {
    const parent = makeEl("div", {});
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    parent.appendChild(img);
    const found = DEE.findContainer(img);
    assert.equal(found, null);
  });
});

test("findContainer caps walk depth at MAX_WALK_DEPTH", () => {
  withMockCS(() => {
    let root = makeEl("div", {});
    root.style.setProperty("max-width", "400px");
    let current = root;
    for (let i = 0; i < 10; i++) {
      const child = makeEl("div", {});
      current.appendChild(child);
      current = child;
    }
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    current.appendChild(img);
    const found = DEE.findContainer(img);
    assert.equal(found, null);
  });
});

test("findContainer stops at first constrainer (nearest wins)", () => {
  withMockCS(() => {
    const outer = makeEl("div", {});
    outer.style.setProperty("max-width", "400px");
    const inner = makeEl("div", {});
    inner.style.setProperty("max-width", "350px");
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    inner.appendChild(img);
    outer.appendChild(inner);
    const found = DEE.findContainer(img);
    assert.equal(found, inner);
  });
});

test("findContainer returns null without getComputedStyle", () => {
  const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
  const found = DEE.findContainer(img);
  assert.equal(found, null);
});

test("applyOverride removes max-width and max-height constraints", () => {
  withMockCS(() => {
    const container = makeEl("div", {});
    container.style.setProperty("max-width", "400px");
    container.style.setProperty("max-height", "300px");
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    container.appendChild(img);
    const result = DEE.applyOverride(img);
    assert.equal(result.ok, true);
    assert.equal(result.removed, true);
    assert.match(container.style.getPropertyValue("max-width"), /none/);
    assert.match(container.style.getPropertyValue("max-height"), /none/);
    assert.equal(img.getAttribute("data-dee-enlarged"), "1");
  });
});

test("applyOverride sets cursor: zoom-in on media", () => {
  withMockCS(() => {
    const container = makeEl("div", {});
    container.style.setProperty("max-width", "400px");
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    container.appendChild(img);
    DEE.applyOverride(img);
    assert.match(img.style.getPropertyValue("cursor"), /zoom-in/);
  });
});

test("applyOverride is idempotent (skips already enlarged)", () => {
  withMockCS(() => {
    const container = makeEl("div", {});
    container.style.setProperty("max-width", "400px");
    const img = makeEl("img", {
      src: "https://cdn.discordapp.com/attachments/123/a/b.png",
      "data-dee-enlarged": "1"
    });
    container.appendChild(img);
    const result = DEE.applyOverride(img);
    assert.equal(result.ok, true);
    assert.equal(result.removed, false);
  });
});

test("applyOverride returns removed:false when no constraints present", () => {
  withMockCS(() => {
    const container = makeEl("div", {});
    const img = makeEl("img", { src: "https://cdn.discordapp.com/attachments/123/a/b.png" });
    container.appendChild(img);
    const result = DEE.applyOverride(img);
    assert.equal(result.ok, true);
    assert.equal(result.removed, false);
  });
});

test("scan finds and overrides all Discord media in a fixture", () => {
  withMockCS(() => {
    const doc = makeDoc();
    const count = DEE.scan(doc);
    assert.ok(count > 0);
    const nonDiscord = doc.querySelector("img[src='https://example.com/photo.jpg']");
    assert.ok(nonDiscord);
    assert.equal(nonDiscord.getAttribute("data-dee-enlarged"), null);
  });
});

test("scan is idempotent", () => {
  withMockCS(() => {
    const doc = makeDoc();
    const count1 = DEE.scan(doc);
    const count2 = DEE.scan(doc);
    assert.equal(count2, 0);
  });
});

test("scan counts Discord images and videos", () => {
  withMockCS(() => {
    const doc = makeDoc();
    const count = DEE.scan(doc);
    assert.ok(count >= 6);
  });
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

test("forget resets all data-dee-enlarged attributes", () => {
  withMockCS(() => {
    const doc = makeDoc();
    DEE.scan(doc);
    assert.ok(doc.querySelector("[data-dee-enlarged]"));
    DEE.forget(doc);
    assert.equal(doc.querySelector("[data-dee-enlarged]"), null);
  });
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
