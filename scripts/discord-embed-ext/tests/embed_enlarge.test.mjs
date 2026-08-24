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

test("isMediaElement rejects a non-media tag", () => {
  // Retitled: there is no background-image handling in this extension, so the
  // old title described a feature that does not exist.
  var el = new FakeElement("div", { class: "message" });
  assert.equal(DEE.isMediaElement(el).isMedia, false);
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

// 🔴 REWRITTEN. The original built the constrainer as a DESCENDANT of the img
// (instrumented: 2 parent hops, `is ancestor? false`), so it returned null for a
// reason unrelated to depth — both MAX_WALK_DEPTH 8->3 and 8->20 survived it.
// These two build a real ancestor CHAIN and pin the boundary from both sides.
function chainAbove(img, depth, capIndexFromImg) {
  var node = img;
  for (var i = 1; i <= depth; i++) {
    var wrapper = new FakeElement("div", { class: "w" + i });
    if (i === capIndexFromImg) wrapper.style.setProperty("max-width", "400px");
    wrapper.appendChild(node);
    node = wrapper;
  }
  return node;
}

test("findContainer REACHES a constrainer at the last in-range ancestor (depth 8)", () => {
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  chainAbove(img, 12, 8);
  var container = DEE.findContainer(img);
  assert.ok(container, "the 8th ancestor is the last one MAX_WALK_DEPTH can see");
  assert.equal(container.getAttribute("class"), "w8");
});

test("findContainer STOPS one past the limit (depth 9 is unreachable)", () => {
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  chainAbove(img, 12, 9);
  assert.equal(DEE.findContainer(img), null,
    "the 9th ancestor is out of range — this is what pins the constant");
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
  // The fixture MUST have a capped parent. Without one `removed` is false whether
  // or not the guard runs, so inverting it to `already === "0"` survived.
  var container = new FakeElement("div", { class: "embed" });
  container.style.setProperty("max-width", "400px", "important");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  container.appendChild(img);
  img.setAttribute("data-dee-enlarged", "1");
  var result = DEE.applyOverride(img);
  assert.equal(result.removed, false,
    "an already-enlarged element must not touch its container a second time");
  assert.equal(container.style.getPropertyValue("max-width"), "400px",
    "the container cap is untouched");
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
  var count1 = DEE.scan(doc.body);
  var count2 = DEE.scan(doc.body);
  assert.ok(count1 > 0, "the first scan found media (positive control)");
  assert.equal(count2, count1,
    "scan() counts what it MATCHED, not what it changed — `count2 >= 0` was true " +
    "of every possible number and pinned nothing");
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

// ===========================================================================
// Added after an adversarial audit. Each test names the mutant it kills.
// ===========================================================================

test("REGRESSION: a PERCENTAGE max-width is not a pixel cap", () => {
  // parseFloat("100%") === 100, which is <= WIDTH_THRESHOLD (500). The walk used
  // to latch the first ancestor with max-width:100% — ubiquitous, often shared
  // layout — and write !important overrides onto it, with no undo.
  assert.ok(Number.isNaN(DEE.cssPx("100%")), "a percentage is not a px length");
  assert.ok(Number.isNaN(DEE.cssPx("none")));
  assert.ok(Number.isNaN(DEE.cssPx("calc(100% - 10px)")));
  assert.ok(Number.isNaN(DEE.cssPx("")));
  assert.equal(DEE.cssPx("400px"), 400);
  assert.equal(DEE.cssPx(" 350px "), 350);
});

test("REGRESSION: an ancestor capped in PERCENT is not treated as a constrainer", () => {
  var pct = new FakeElement("div", { class: "layout" });
  pct.style.setProperty("max-width", "100%");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  pct.appendChild(img);
  assert.equal(DEE.findContainer(img), null,
    "a 100% ancestor must be walked PAST, not overridden");
});

test("a px-capped ancestor beyond a percent one is still found", () => {
  var outer = new FakeElement("div", { class: "real-cap" });
  outer.style.setProperty("max-width", "400px");
  var pct = new FakeElement("div", { class: "layout" });
  pct.style.setProperty("max-width", "100%");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  pct.appendChild(img); outer.appendChild(pct);
  assert.equal(DEE.findContainer(img), outer,
    "positive control: the walk continues past the percent and finds the real cap");
});

test("REGRESSION: scan() is scoped to its root, not a whole-document rescan", () => {
  DEE.forget();
  var doc = makeDiscordDoc(
    "<div id='a'><img src='https://cdn.discordapp.com/attachments/1/1/a.png' /></div>" +
    "<div id='b'><img src='https://cdn.discordapp.com/attachments/2/2/b.png' /></div>");
  var a = doc.getElementById("a");
  assert.equal(DEE.scan(a), 1, "`var doc = root;` -> whole-document was a surviving mutant");
  assert.equal(doc.querySelector("#a img").getAttribute("data-dee-enlarged"), "1");
  assert.equal(doc.querySelector("#b img").getAttribute("data-dee-enlarged"), null,
    "the other subtree must be untouched");
});

test("scan() handles a root that IS the media element", () => {
  DEE.forget();
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  // querySelectorAll never matches the element it is called on, so an observer
  // handing us the <img> itself would otherwise scan nothing.
  assert.equal(DEE.scan(img), 1);
  assert.equal(img.getAttribute("data-dee-enlarged"), "1");
});

// --- the MutationObserver: the only production path after document_idle -----

function withFakeObserverAndTimers(fn) {
  var realMO = globalThis.MutationObserver;
  var realST = globalThis.setTimeout;
  var realCT = globalThis.clearTimeout;
  var timers = [];
  var captured = { cb: null, observedWith: null };
  globalThis.MutationObserver = function (cb) {
    captured.cb = cb;
    return {
      observe: function (target, opts) { captured.observedWith = { target: target, opts: opts }; },
      disconnect: function () { captured.disconnected = true; },
    };
  };
  globalThis.setTimeout = function (fn2) { timers.push(fn2); return timers.length; };
  globalThis.clearTimeout = function (id) { if (id) timers[id - 1] = null; };
  try {
    return fn(captured, function flush() {
      var pending = timers.slice();
      timers.length = 0;
      pending.forEach(function (t) { if (t) t(); });
    });
  } finally {
    globalThis.MutationObserver = realMO;
    globalThis.setTimeout = realST;
    globalThis.clearTimeout = realCT;
  }
}

test("REGRESSION: observe() actually subscribes to the document body", () => {
  DEE.forget();
  var doc = makeDiscordDoc("<div class='message'></div>");
  withFakeObserverAndTimers(function (cap) {
    DEE.observe(doc);
    assert.ok(cap.observedWith, "replacing observer.observe with a no-op used to survive " +
      "a green 60/60 suite — and this is the ONLY path that sees Discord's late render");
    assert.equal(cap.observedWith.target, doc.body);
    assert.deepEqual(cap.observedWith.opts, { childList: true, subtree: true });
  });
});

test("REGRESSION: batches arriving inside the debounce window are NOT discarded", () => {
  DEE.forget();
  var doc = makeDiscordDoc("<div class='message'></div>");
  withFakeObserverAndTimers(function (cap, flush) {
    DEE.observe(doc);
    function batch(id, src) {
      var wrap = new FakeElement("div", { id: id });
      var img = new FakeElement("img", { src: src });
      wrap.appendChild(img);
      return { addedNodes: [wrap], img: img };
    }
    var first = batch("b1", "https://cdn.discordapp.com/attachments/1/1/one.png");
    var second = batch("b2", "https://cdn.discordapp.com/attachments/2/2/two.png");
    cap.cb([{ addedNodes: first.addedNodes }]);
    cap.cb([{ addedNodes: second.addedNodes }]);   // arrives before the debounce fires
    flush();
    assert.equal(first.img.getAttribute("data-dee-enlarged"), "1",
      "the FIRST batch used to be silently dropped: the callback closed over only " +
      "the newest `mutations` and clearTimeout'd the pending run");
    assert.equal(second.img.getAttribute("data-dee-enlarged"), "1");
  });
});

test("REGRESSION: forget() cancels a debounce already in flight", () => {
  DEE.forget();
  var doc = makeDiscordDoc("<div class='message'></div>");
  withFakeObserverAndTimers(function (cap, flush) {
    DEE.observe(doc);
    var wrap = new FakeElement("div", {});
    var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/1/x.png" });
    wrap.appendChild(img);
    cap.cb([{ addedNodes: [wrap] }]);
    DEE.forget(doc);
    flush();
    assert.equal(img.getAttribute("data-dee-enlarged"), null,
      "disconnect() stops NEW batches; a pending timer still fired and re-marked " +
      "elements after forget()");
  });
});
