import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

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
    return expandOverflowShorthand(cs);
  }
  // 🔴 A COPY, NEVER el.style ITSELF. getComputedStyle is a READ; expanding
  // shorthands into the element's own inline declaration would let a read
  // mutate the thing unclipAncestors later records as the "prior" value, and
  // the undo would restore longhands the page never had.
  return expandOverflowShorthand(copyOf(el.style));
};

function copyOf(style) {
  var cs = new FakeComputedStyle();
  if (style && style._props) {
    for (var e of style._props) cs.setProperty(e[0], e[1].value, e[1].important ? "important" : "");
  }
  return cs;
}

// 🔴 A REAL getComputedStyle RESOLVES SHORTHANDS TO LONGHANDS. `overflow:
// hidden` computes overflow-x AND overflow-y to "hidden"; the shorthand is
// never the only thing readable. clipsOverflow() reads the longhands because
// that is what the browser guarantees and because the two axes can differ —
// so a fixture that stores only the shorthand would report NOT-CLIPPING for
// the exact markup Discord ships, and every unclip test would pass vacuously
// on a code path that never fires in Brave.
//
// CSS also forces a `visible` axis to `auto` when its partner is hidden/clip.
// Modelling that is what makes the "both axes, always" claim in
// unclipAncestors testable rather than asserted.
function expandOverflowShorthand(cs) {
  if (!cs || typeof cs.getPropertyValue !== "function") return cs;
  var short = String(cs.getPropertyValue("overflow") || "").trim();
  if (short) {
    var axes = short.split(/\s+/);
    var x = axes[0];
    var y = axes.length > 1 ? axes[1] : axes[0];
    if (!cs.getPropertyValue("overflow-x")) cs.setProperty("overflow-x", x);
    if (!cs.getPropertyValue("overflow-y")) cs.setProperty("overflow-y", y);
  }
  var vx = String(cs.getPropertyValue("overflow-x") || "").trim();
  var vy = String(cs.getPropertyValue("overflow-y") || "").trim();
  var clips = function (v) { return v === "hidden" || v === "clip" || v === "auto" || v === "scroll"; };
  if (vx === "visible" && clips(vy)) cs.setProperty("overflow-x", "auto");
  if (vy === "visible" && clips(vx)) cs.setProperty("overflow-y", "auto");
  return cs;
}
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
  // A FRACTIONAL px is a real computed value (calc(), flex layout) and must
  // still count. Tightening the regex to integers-only used to survive, so a
  // later "tidy-up" could silently stop treating a real cap as a cap.
  assert.equal(DEE.cssPx("399.5px"), 399.5);
  assert.equal(DEE.cssPx("0px"), 0);
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
  var cleared = [];
  var captured = { cb: null, observedWith: null, disconnected: false };
  globalThis.MutationObserver = function (cb) {
    captured.cb = cb;
    return {
      observe: function (target, opts) { captured.observedWith = { target: target, opts: opts }; },
      disconnect: function () { captured.disconnected = true; },
    };
  };
  var scheduled = [];
  globalThis.setTimeout = function (fn2, delay) {
    timers.push(fn2);
    scheduled.push({ delay: delay });
    return timers.length;
  };
  captured.scheduled = scheduled;
  globalThis.clearTimeout = function (id) { if (id) { cleared.push(id); timers[id - 1] = null; } };
  captured.cleared = cleared;
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

// 🔴 THE NODES MUST BE ATTACHED TO A DOCUMENT. With detached fixtures,
// `node.ownerDocument` is null, so the mutant `scan(node.ownerDocument || node)`
// — i.e. reverting the observer's call site to a whole-document rescan — was
// INERT and survived. The `outsider` below is media that is in the document but
// in no batch: a whole-document rescan marks it, a correctly scoped scan does not.
function observerFixture() {
  DEE.forget();
  var doc = makeDiscordDoc(
    "<div id='outside'><img src='https://cdn.discordapp.com/attachments/9/9/out.png' /></div>" +
    "<div id='live'></div>");
  return { doc: doc, outsider: doc.querySelector("#outside img"), live: doc.getElementById("live") };
}

function batchInto(fx, id, src) {
  var wrap = fx.doc.createElement("div");
  wrap.setAttribute("id", id);
  var img = fx.doc.createElement("img");
  img.setAttribute("src", src);
  wrap.appendChild(img);
  fx.live.appendChild(wrap);
  return { addedNodes: [wrap], img: img };
}

test("REGRESSION: observe() actually subscribes to the document body", () => {
  var fx = observerFixture();
  withFakeObserverAndTimers(function (cap) {
    DEE.observe(fx.doc);
    assert.ok(cap.observedWith, "replacing observer.observe with a no-op used to survive a " +
      "green suite — and this is the ONLY path that sees Discord's late render");
    assert.equal(cap.observedWith.target, fx.doc.body);
    assert.equal(cap.observedWith.opts.childList, true);
    assert.equal(cap.observedWith.opts.subtree, true);
    assert.equal(cap.observedWith.opts.attributes, true);
    assert.deepEqual(cap.observedWith.opts.attributeFilter, ["src"],
      "an <img> whose src is set AFTER insertion is invisible to childList alone");
  });
});

test("REGRESSION: batches inside the debounce window are NOT discarded, and stay SCOPED", () => {
  var fx = observerFixture();
  withFakeObserverAndTimers(function (cap, flush) {
    var first = batchInto(fx, "b1", "https://cdn.discordapp.com/attachments/1/1/one.png");
    var second = batchInto(fx, "b2", "https://cdn.discordapp.com/attachments/2/2/two.png");
    DEE.observe(fx.doc);
    cap.cb([{ addedNodes: first.addedNodes }]);
    cap.cb([{ addedNodes: second.addedNodes }]);   // arrives before the debounce fires
    flush();
    assert.equal(first.img.getAttribute("data-dee-enlarged"), "1",
      "the FIRST batch used to be dropped: the callback closed over only the newest " +
      "`mutations` and clearTimeout'd the pending run");
    assert.equal(second.img.getAttribute("data-dee-enlarged"), "1");
    assert.equal(fx.outsider.getAttribute("data-dee-enlarged"), null,
      "and the scan stays inside the batch — a whole-document rescan would mark this");
  });
});

test("REGRESSION: the pending list DRAINS after a flush", () => {
  var fx = observerFixture();
  withFakeObserverAndTimers(function (cap, flush) {
    var first = batchInto(fx, "b1", "https://cdn.discordapp.com/attachments/1/1/one.png");
    DEE.observe(fx.doc);
    cap.cb([{ addedNodes: first.addedNodes }]);
    flush();
    assert.equal(first.img.getAttribute("data-dee-enlarged"), "1");
    first.img.removeAttribute("data-dee-enlarged");     // prove it is not re-scanned
    var second = batchInto(fx, "b2", "https://cdn.discordapp.com/attachments/2/2/two.png");
    cap.cb([{ addedNodes: second.addedNodes }]);
    flush();
    assert.equal(second.img.getAttribute("data-dee-enlarged"), "1", "the new batch ran");
    assert.equal(first.img.getAttribute("data-dee-enlarged"), null,
      "dropping `pendingNodes = []` used to survive: the list would grow without " +
      "bound on a long-lived tab and every debounce would rescan everything ever added");
  });
});

test("REGRESSION: an <img> whose src is set AFTER insertion is still picked up", () => {
  var fx = observerFixture();
  withFakeObserverAndTimers(function (cap, flush) {
    var img = fx.doc.createElement("img");        // inserted with NO src
    fx.live.appendChild(img);
    DEE.observe(fx.doc);
    cap.cb([{ addedNodes: [img] }]);
    flush();
    assert.equal(img.getAttribute("data-dee-enlarged"), null, "nothing to match yet");
    img.setAttribute("src", "https://cdn.discordapp.com/attachments/3/3/late.png");
    cap.cb([{ type: "attributes", attributeName: "src", target: img, addedNodes: [] }]);
    flush();
    assert.equal(img.getAttribute("data-dee-enlarged"), "1",
      "the scoped scan gave up the old whole-document rescan's accidental " +
      "self-healing; the attributeFilter restores it deliberately");
  });
});

// 🔴 forget() HAS TWO HALVES AND THEY MASK EACH OTHER. Dropping either one alone
// leaves behaviour unchanged, so a single outcome-shaped test pins neither. These
// two isolate them: one reads the timer directly, the other proves the list is
// empty by sending a LATER batch and checking the old node is not swept in.

test("REGRESSION: forget() clears the pending debounce TIMER", () => {
  var fx = observerFixture();
  withFakeObserverAndTimers(function (cap) {
    var first = batchInto(fx, "b1", "https://cdn.discordapp.com/attachments/1/1/one.png");
    DEE.observe(fx.doc);
    cap.cb([{ addedNodes: first.addedNodes }]);
    var clearedBefore = cap.cleared.length;
    DEE.forget(fx.doc);
    assert.ok(cap.cleared.length > clearedBefore,
      "forget() must clearTimeout the in-flight debounce, not merely disconnect");
    assert.equal(cap.disconnected, true, "and disconnect the observer");
  });
});

test("REGRESSION: forget() empties the pending NODE LIST", () => {
  var fx = observerFixture();
  withFakeObserverAndTimers(function (cap, flush) {
    var first = batchInto(fx, "b1", "https://cdn.discordapp.com/attachments/1/1/one.png");
    DEE.observe(fx.doc);
    cap.cb([{ addedNodes: first.addedNodes }]);
    DEE.forget(fx.doc);
    var second = batchInto(fx, "b2", "https://cdn.discordapp.com/attachments/2/2/two.png");
    cap.cb([{ addedNodes: second.addedNodes }]);   // a LATER batch drains the list
    flush();
    assert.equal(second.img.getAttribute("data-dee-enlarged"), "1");
    assert.equal(first.img.getAttribute("data-dee-enlarged"), null,
      "a node queued before forget() must not ride along on the next flush");
  });
});


test("the size thresholds are pinned from BOTH sides", () => {
  // Lowering them died already; RAISING them did not, and that direction is the
  // hazard: a bigger threshold means overriding ancestors that were never a
  // media cap at all — the same class of bug as reading "100%" as 100px.
  function capped(px) {
    var wrap = new FakeElement("div", { class: "c" });
    wrap.style.setProperty("max-width", px);
    var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
    wrap.appendChild(img);
    return DEE.findContainer(img);
  }
  assert.ok(capped("500px"), "500px is exactly at the limit and IS a cap");
  assert.equal(capped("501px"), null, "just past the limit is NOT a cap");
  assert.equal(capped("900px"), null,
    "a wide ancestor must never be treated as a media cap (threshold 500 -> 900 used to survive)");

  function cappedH(px) {
    var wrap = new FakeElement("div", { class: "c" });
    wrap.style.setProperty("max-height", px);
    var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
    wrap.appendChild(img);
    return DEE.findContainer(img);
  }
  assert.ok(cappedH("400px"), "400px height is at the limit and IS a cap");
  assert.equal(cappedH("401px"), null, "just past it is NOT");
  assert.equal(cappedH("900px"), null, "and a tall ancestor is never a cap");
});

// ===========================================================================
// Round 3 — findings from an independent 163-mutant battery.
// ===========================================================================

test("REGRESSION: a negative px value is NOT a cap", () => {
  // CSS forbids a negative max-width/max-height and clamps calc() to >= 0, so
  // this is not a value a real cap can hold. Accepting it made findContainer
  // LATCH on such an ancestor and write !important overrides onto it. An earlier
  // README claimed the negative branch was deliberate — it was unreachable,
  // unpinned and wrong. Dropping `-?` used to survive the whole suite.
  assert.ok(Number.isNaN(DEE.cssPx("-10px")), "a negative length is not a cap");
  assert.ok(Number.isNaN(DEE.cssPx("-0.5px")));
  var wrap = new FakeElement("div", { class: "bad" });
  wrap.style.setProperty("max-width", "-10px");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  wrap.appendChild(img);
  assert.equal(DEE.findContainer(img), null,
    "and it must not be latched on to as a constrainer");
});

test("REGRESSION: a <source> whose src is set after insertion resolves to its <video>", () => {
  DEE.forget();
  // The observer hands us the <source>; scan() used to accept only img/video as
  // a root and threw it away, so the observation cost was paid and the result
  // discarded — while the source comment claimed the case was closed.
  var doc = makeDiscordDoc("<div class='message'><video></video></div>");
  var video = doc.querySelector("video");
  var source = doc.createElement("source");
  source.setAttribute("src", "https://media.discordapp.net/attachments/1/2/late.mp4");
  video.appendChild(source);
  assert.equal(DEE.scan(source), 1, "scanning the <source> must find media");
  assert.equal(video.getAttribute("data-dee-enlarged"), "1",
    "and it is the parent <video> that gets marked");
});

test("REGRESSION: a non-element node never triggers a whole-document rescan", () => {
  DEE.forget();
  // Discord inserts text nodes constantly. Dropping the `nodeType === 1` filter
  // survived the suite, and a text node reaching scan() falls through to
  // `scope = document` — turning every text mutation into a full-page sweep,
  // which is the bug class the scoped scan exists to prevent.
  var doc = makeDiscordDoc(
    "<div id='far'><img src='https://cdn.discordapp.com/attachments/9/9/far.png' /></div>" +
    "<div id='live'></div>");
  var far = doc.querySelector("#far img");
  // 🔴 THE GLOBAL `document` MUST EXIST FOR THIS TEST TO BE ABLE TO FAIL. Node
  // has none, so the "fall back to the whole document" branch is unreachable
  // here and every mutant against it passes vacuously — which is exactly how
  // this hazard survived a 163-mutant battery. Simulate the browser condition.
  var realDoc = globalThis.document;
  globalThis.document = doc;
  try {
    assert.equal(DEE.scan({ nodeType: 3, textContent: "hello" }), 0,
      "a node with no querySelectorAll scans nothing");
    assert.equal(far.getAttribute("data-dee-enlarged"), null,
      "and above all does NOT trigger a whole-document sweep");
    withFakeObserverAndTimers(function (cap, flush) {
      DEE.observe(doc);
      cap.cb([{ addedNodes: [{ nodeType: 3, textContent: "hello" }] }]);
      flush();
      assert.equal(far.getAttribute("data-dee-enlarged"), null,
        "and the observer filters it out before it ever gets there");
    });
  } finally {
    if (realDoc === undefined) delete globalThis.document;
    else globalThis.document = realDoc;
  }
});

test("REGRESSION: the container override is !important, or Discord's own CSS wins", () => {
  var container = new FakeElement("div", { class: "embed" });
  container.style.setProperty("max-width", "400px", "important");
  container.style.setProperty("max-height", "300px", "important");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  container.appendChild(img);
  DEE.applyOverride(img);
  // The VALUE was asserted; the PRIORITY never was — and the whole point is to
  // beat the stylesheet that set the cap in the first place. Dropping
  // `"important"` used to survive.
  var css = container.style.cssText;
  assert.match(css, /max-width: none !important/, "container max-width must win");
  assert.match(css, /max-height: none !important/, "container max-height must win");
  assert.match(img.style.cssText, /max-width: 100% !important/,
    "and the element side too");
});

test("SEAM: autoStart both scans what is already there AND subscribes for later", () => {
  DEE.forget();
  // Deleting either half used to survive: everything rendered by document_idle
  // would go un-enlarged, or nothing rendered afterwards would ever be seen.
  var doc = makeDiscordDoc(
    "<div class='message'><div class='embed'>" +
    "<img src='https://cdn.discordapp.com/attachments/1/2/already.png' /></div></div>");
  var existing = doc.querySelector("img");
  withFakeObserverAndTimers(function (cap) {
    assert.equal(DEE.autoStart(doc), true);
    assert.equal(existing.getAttribute("data-dee-enlarged"), "1",
      "media present at load must be enlarged immediately");
    assert.ok(cap.observedWith, "and the observer must be subscribed for later renders");
    assert.equal(cap.observedWith.target, doc.body);
  });
});

// ===========================================================================
// Round 4 — the 47th mutant, and the entry point that still was not pinned.
// ===========================================================================

test("REGRESSION: a <video> arriving as the scan root is marked (the 47th mutant)", () => {
  DEE.forget();
  // Exact sibling of the <source> bug fixed last round: a <video> whose OWN src
  // is set after insertion reaches scan() as the ROOT, via the same
  // attributeFilter:["src"] branch. The img and source arms were pinned; this
  // one was not, and dropping it left the suite fully green.
  var doc = makeDiscordDoc("<div class='message'><video></video></div>");
  var video = doc.querySelector("video");
  video.setAttribute("src", "https://media.discordapp.net/attachments/1/2/late.mp4");
  assert.equal(DEE.scan(video), 1, "scanning the <video> itself must find it");
  assert.equal(video.getAttribute("data-dee-enlarged"), "1");
});

test("REGRESSION: tagName is matched case-insensitively, as a real document reports it", () => {
  // A real HTML document returns UPPERCASE tagName. The fake used to lowercase,
  // which made every `.toLowerCase()` in the extension vacuous: SEVEN guards
  // across both content scripts could each be deleted with the suite green, and
  // each deletion makes the extension completely inert in Brave. The fixture is now faithful; this asserts it.
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  assert.equal(img.tagName, "IMG", "the fixture must report what the browser does");
  assert.equal(DEE.isMediaElement(img).isMedia, true, "and detection must still work");

  var video = new FakeElement("video", {});
  var source = new FakeElement("source",
    { src: "https://media.discordapp.net/attachments/1/2/v.mp4" });
  video.appendChild(source);
  assert.equal(video.tagName, "VIDEO");
  assert.equal(source.tagName, "SOURCE");
  assert.equal(DEE.isMediaElement(video).element, video, "video via its <source> child");
  assert.equal(DEE.isMediaElement(source).element, video, "and a <source> resolves upward");
  DEE.forget();
  assert.equal(DEE.scan(video), 1, "and scan()'s own rootTag check is case-folded too");
});

test("SEAM: importing the script WITHOUT the autostart flag actually starts it", () => {
  // 🔴 THE DEFECT MOVED ONE LEVEL UP LAST ROUND AND WAS NOT CLOSED. autoStart()'s
  // two halves are pinned, but deleting the `autoStart();` CALL at module scope
  // left the suite green — every test sets DEE_NO_AUTOSTART before importing, so
  // module scope never reaches that line. The extension would do nothing at all
  // in Brave. A source-text grep would be a spelled guard (a comment-out still
  // matches), so this imports the real module in a SUBPROCESS with no flag set
  // and asserts observable behaviour.
  var extDir = new URL("../extension/", import.meta.url).pathname;
  var fakeDom = new URL("./fake_discord_dom.mjs", import.meta.url).pathname;
  var probe = [
    'import { makeDiscordDoc } from ' + JSON.stringify(fakeDom) + ';',
    'const doc = makeDiscordDoc(',
    '  "<div class=\'message\'><div class=\'embed\'>" +',
    '  "<img src=\'https://cdn.discordapp.com/attachments/1/2/auto.png\' /></div></div>");',
    'globalThis.document = doc;',
    'globalThis.MutationObserver = function (cb) {',
    '  return { observe: function () { globalThis.__OBSERVED__ = true; }, disconnect: function () {} };',
    '};',
    // deliberately NOT setting DEE_NO_AUTOSTART
    'await import(' + JSON.stringify(extDir + "embed_enlarge.js") + ');',
    'const img = doc.querySelector("img");',
    'process.stdout.write(JSON.stringify({',
    '  scanned: img.getAttribute("data-dee-enlarged"),',
    '  observed: globalThis.__OBSERVED__ === true,',
    '}));',
  ].join("\n");
  var out = execFileSync(process.execPath,
    ["--input-type=module", "-e", probe], { encoding: "utf8", timeout: 30000 });
  var res = JSON.parse(out);
  assert.equal(res.scanned, "1",
    "importing the content script must enlarge what is already on the page");
  assert.equal(res.observed, true,
    "and must subscribe the observer for everything rendered later");
});

test("MEDIA_URL_RE is ANCHORED — a matching substring inside another url is not media", () => {
  // Losing the `^` survived. The anchor is what makes "message media only" a
  // PREFIX test rather than a containment test; without it any url that merely
  // embeds the pattern would match.
  assert.equal(DEE.MEDIA_URL_RE.test(
    "https://evil.example.com/?x=https://cdn.discordapp.com/attachments/1/2/p.png"), false);
  assert.equal(DEE.MEDIA_URL_RE.test(
    "  https://cdn.discordapp.com/attachments/1/2/p.png"), false, "leading space too");
});

test("MEDIA_URL_RE is case-insensitive, as a real url may be", () => {
  // The `/i` flag survived. Same case-fold class as the tagName fixture bug,
  // one level out — a host is case-insensitive per RFC 3986.
  assert.equal(DEE.MEDIA_URL_RE.test(
    "HTTPS://CDN.DISCORDAPP.COM/ATTACHMENTS/1/2/P.PNG"), true);
  assert.equal(DEE.MEDIA_URL_RE.test(
    "https://CDN.discordapp.com/Attachments/1/2/p.png"), true);
});

// ===========================================================================
// Round 7.
// ===========================================================================

test("REGRESSION: cssPx returns NaN for a non-string, never 0", () => {
  // `return NaN` -> `return 0` survived, and 0 <= WIDTH_THRESHOLD, so
  // findContainer would latch the FIRST ancestor and write four !important
  // overrides onto it with no undo — the exact hazard cssPx's own comment
  // describes. Nothing fed it a non-string.
  for (var v of [null, undefined, 400, {}, [], NaN]) {
    assert.ok(Number.isNaN(DEE.cssPx(v)), "cssPx(" + String(v) + ") must be NaN");
  }
  // and the consequence, not just the return value
  var wrap = new FakeElement("div", { class: "no-cap" });
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  wrap.appendChild(img);
  var realGet = globalThis.__DEE_GET_COMPUTED_STYLE__;
  globalThis.__DEE_GET_COMPUTED_STYLE__ = function () {
    return { getPropertyValue: function () { return undefined; } };   // non-string
  };
  try {
    assert.equal(DEE.findContainer(img), null,
      "an ancestor whose computed value is not a string is not a cap");
  } finally {
    globalThis.__DEE_GET_COMPUTED_STYLE__ = realGet;
  }
});

test("REGRESSION: the debounce actually debounces, at the stated delay", () => {
  DEE.forget();
  var doc = makeDiscordDoc("<div class='message'></div>");
  withFakeObserverAndTimers(function (cap, flush) {
    DEE.observe(doc);
    var w1 = new FakeElement("div", {});
    var w2 = new FakeElement("div", {});
    cap.cb([{ addedNodes: [w1] }]);
    cap.cb([{ addedNodes: [w2] }]);
    // Two batches inside the window must leave ONE pending timer, not two —
    // deleting the clearTimeout survived, and the debounce stopped debouncing:
    // every batch scheduled its own full scan. The comment calls this "what
    // makes the debounce cheap", so it is a claim that needs a guard.
    assert.equal(cap.cleared.length, 1, "the first timer must be cancelled");
    assert.equal(cap.scheduled.length, 2, "two scheduled, one of them cancelled");
    // and the delay is the stated constant, pinned in BOTH directions
    assert.equal(cap.scheduled[cap.scheduled.length - 1].delay, 100,
      "DEBOUNCE_MS moved either way with the suite green before this");
    flush();
  });
});

test("REGRESSION: applyOverride writes its FULL declaration set, not just the caps", () => {
  // max-width/max-height/cursor were pinned; width, height and object-fit were
  // not — each deletable with the suite green. A cap removed while `width` stays
  // at Discord's computed value is a half-enlarged embed.
  var container = new FakeElement("div", { class: "embed" });
  container.style.setProperty("max-width", "400px", "important");
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  container.appendChild(img);
  DEE.applyOverride(img);

  var el = img.style.cssText;
  for (var decl of ["max-width: 100% !important", "max-height: none !important",
                    "width: auto !important", "height: auto !important",
                    "object-fit: contain !important", "cursor: zoom-in !important"]) {
    assert.ok(el.includes(decl), "element must declare " + decl + " — got: " + el);
  }
  var cs = container.style.cssText;
  for (var cdecl of ["max-width: none !important", "max-height: none !important",
                     "width: auto !important", "height: auto !important"]) {
    assert.ok(cs.includes(cdecl), "container must declare " + cdecl + " — got: " + cs);
  }
});

// ---------------------------------------------------------------------------
// UNCLIPPING — the half #804 did not have, and the bug v0.2.0-0.2.3 chased.
//
// 🔴 EVERY TEST BELOW WAS RUN AGAINST eaf68c96 (the restore point, before
// unclipAncestors existed) AND AGAINST 0e1db9e6 (the shipped v0.2.3). The
// matrix is in the commit message. A test that only ever ran green here would
// be pinning nothing.
// ---------------------------------------------------------------------------

// A message row + nested containers, shaped like Discord's: the media sits
// several levels below the row, and the row sits below a scroller that must
// NEVER be touched.
// 🔴 `chrome` IS LOad-BEARING, NOT SCENERY. Without an ancestor ABOVE the
// message row that clips with `overflow: hidden`, the boundary break is
// UNREACHABLE: the only thing above the row would be the `auto` scroller, which
// the clipsOverflow predicate already rejects, so deleting the break changed
// nothing and the mutant SURVIVED a green suite. Discord really does wrap the
// message list in hidden-overflow chrome, so this is the realistic shape as
// well as the observable one.
function clipRig(opts) {
  var o = opts || {};
  var chrome = new FakeElement("div", {
    class: "chrome",
    style: o.chromeStyle === undefined ? "overflow: hidden" : o.chromeStyle,
  });
  var scroller = new FakeElement("div", {
    id: o.scrollerId === undefined ? "chat-messages-111-222" : o.scrollerId,
    style: "overflow: auto",
  });
  chrome.appendChild(scroller);
  var row = new FakeElement("li", {
    class: o.rowClass === undefined ? "message__74e4d" : o.rowClass,
    style: o.rowStyle === undefined ? "overflow: hidden" : o.rowStyle,
  });
  var outer = new FakeElement("div", { style: o.outerStyle === undefined ? "overflow: hidden" : o.outerStyle });
  var inner = new FakeElement("div", { style: o.innerStyle === undefined ? "overflow: hidden" : o.innerStyle });
  var img = new FakeElement("img", { src: "https://cdn.discordapp.com/attachments/1/2/p.png" });
  scroller.appendChild(row);
  row.appendChild(outer);
  outer.appendChild(inner);
  inner.appendChild(img);
  return { chrome: chrome, scroller: scroller, row: row, outer: outer, inner: inner, img: img };
}

function overflowOf(el) {
  return el.style.getPropertyValue("overflow") + "/" + el.style.getPropertyPriority("overflow");
}

test("clipsOverflow: hidden and clip are clips, on EITHER axis", () => {
  var cases = [
    ["overflow-x", "hidden"], ["overflow-y", "hidden"],
    ["overflow-x", "clip"], ["overflow-y", "clip"],
  ];
  for (var c of cases) {
    var cs = new FakeComputedStyle();
    cs.setProperty(c[0], c[1]);
    assert.equal(DEE.clipsOverflow(cs), true, c[0] + ":" + c[1] + " must read as a clip");
  }
});

test("🔴 clipsOverflow: auto and scroll are NOT clips — they are SCROLL CONTAINERS", () => {
  // This is the whole safety property. v0.2.1-0.2.3 forced `overflow: visible`
  // onto every ancestor regardless, which is what broke Discord's scroller and
  // produced the "scroll loop cascade" in the handoff's dead-ends list.
  for (var v of ["auto", "scroll"]) {
    for (var axis of ["overflow-x", "overflow-y"]) {
      var cs = new FakeComputedStyle();
      cs.setProperty(axis, v);
      assert.equal(DEE.clipsOverflow(cs), false,
        axis + ":" + v + " is a scroller — clearing it breaks scrolling");
    }
  }
});

test("clipsOverflow: visible, garbage and a missing style object are not clips", () => {
  var vis = new FakeComputedStyle();
  vis.setProperty("overflow-x", "visible");
  vis.setProperty("overflow-y", "visible");
  assert.equal(DEE.clipsOverflow(vis), false, "visible needs no clearing");
  var junk = new FakeComputedStyle();
  junk.setProperty("overflow-x", "inherit");
  assert.equal(DEE.clipsOverflow(junk), false, "an unrecognised value is not a clip");
  assert.equal(DEE.clipsOverflow(null), false, "null is not a clip");
  assert.equal(DEE.clipsOverflow(undefined), false, "undefined is not a clip");
  assert.equal(DEE.clipsOverflow({}), false, "an object with no getPropertyValue is not a clip");
});

test("unclipAncestors clears EVERY clipping ancestor in the walk, not just the first", () => {
  // findContainer stops at the first ancestor with a px cap. If unclipAncestors
  // copied that shape it would clear `inner` and leave `outer` cropping — which
  // is indistinguishable from the original bug.
  var rig = clipRig();
  var n = DEE.unclipAncestors(rig.img);
  assert.equal(n, 3, "inner, outer and the row all clip and must all be cleared");
  assert.equal(overflowOf(rig.inner), "visible/important", "inner cleared");
  assert.equal(overflowOf(rig.outer), "visible/important", "outer cleared");
  assert.equal(overflowOf(rig.row), "visible/important", "the message row itself cleared");
});

test("🔴 unclipAncestors STOPS AT THE MESSAGE ROW — the scroller above is never touched", () => {
  // The structural bound. Depth numbers were tried (8 in v0.2.1-0.2.3) and are
  // the reason the walk could reach Discord's chrome; the message row makes the
  // scroller unreachable BY CONSTRUCTION rather than by hoping a number lands.
  var rig = clipRig();
  DEE.unclipAncestors(rig.img);
  // TWO INDEPENDENT CLAIMS, deliberately separated. The scroller is protected
  // by the auto/scroll PREDICATE; `chrome` clips with `overflow: hidden` and is
  // protected ONLY by the boundary break. Asserting the scroller alone left the
  // boundary guard unreachable — the mutant that deletes it SURVIVED.
  assert.equal(rig.scroller.style.getPropertyValue("overflow"), "",
    "the scroller must carry NO inline overflow — clearing it breaks scrolling");
  assert.equal(rig.scroller.getAttribute("data-dee-unclipped"), null,
    "and must not even be marked");
  assert.equal(rig.chrome.style.getPropertyValue("overflow"), "",
    "chrome ABOVE the message row clips with `hidden` and must survive — only " +
    "the message boundary can stop the walk reaching it");
  assert.equal(rig.chrome.getAttribute("data-dee-unclipped"), null,
    "nor may it be marked");
});

test("🔴 unclipAncestors leaves a SCROLLING ancestor inside the message alone", () => {
  // A code block inside a message legitimately scrolls. It is below the
  // boundary, so only the auto/scroll predicate protects it.
  var rig = clipRig({ outerStyle: "overflow: auto" });
  DEE.unclipAncestors(rig.img);
  assert.equal(rig.outer.style.getPropertyValue("overflow"), "",
    "an auto ancestor inside the message must survive untouched");
  assert.equal(overflowOf(rig.inner), "visible/important", "while a hidden sibling ancestor is cleared");
});

test("unclipAncestors falls back to the bounded walk when no message row is found", () => {
  // A Discord CSS reshuffle that rots findMessageContainer must degrade to the
  // old bounded walk, NOT to an unbounded climb to <html>.
  var rig = clipRig({ rowClass: "row", rowStyle: "overflow: hidden", scrollerId: "scroller" });
  assert.equal(DEE.findMessageContainer(rig.img), null, "precondition: no message row matches");
  var n = DEE.unclipAncestors(rig.img);
  // inner, outer, row and chrome clip and are cleared; the scroller is `auto`
  // and is skipped even here — the auto/scroll predicate is the LAST line of
  // defence once the structural boundary is gone, and it must still hold.
  assert.equal(n, 4, "clears every reachable clipping ancestor, got " + n);
  assert.equal(rig.scroller.style.getPropertyValue("overflow"), "",
    "the scroller survives on the predicate alone once the boundary is lost");
  // The chain above the img is 5 deep, under MAX_WALK_DEPTH — the claim being
  // pinned is that the walk TERMINATED rather than climbing to <html>.
  assert.ok(n <= 8, "and never more than MAX_WALK_DEPTH ancestors, got " + n);
});

test("unclipAncestors records the PRIOR inline overflow so it can be undone", () => {
  var rig = clipRig();
  rig.inner.style.setProperty("overflow", "hidden", "important");
  DEE.unclipAncestors(rig.img);
  assert.equal(rig.inner.getAttribute("data-dee-unclipped"), "hidden;important",
    "value AND priority are both recorded");
  assert.equal(rig.outer.getAttribute("data-dee-unclipped"), ";",
    "an ancestor with no inline overflow records an empty pair, not nothing");
});

test("unclipAncestors is once-only — a second pass must not record its own override", () => {
  // Without the ATTR_UNCLIPPED guard the second walk reads `visible !important`
  // (which we wrote) as the prior value, and forget() then "restores" the
  // override forever. Green suite, permanently welded style.
  var rig = clipRig();
  rig.inner.style.setProperty("overflow", "hidden", "");
  DEE.unclipAncestors(rig.img);
  var afterFirst = rig.inner.getAttribute("data-dee-unclipped");
  var n2 = DEE.unclipAncestors(rig.img);
  assert.equal(n2, 0, "a second pass clears nothing new");
  assert.equal(rig.inner.getAttribute("data-dee-unclipped"), afterFirst,
    "and must not overwrite the recorded prior value with our own");
  assert.equal(afterFirst, "hidden;", "which is still Discord's, not ours");
});

test("reclipAncestors restores the exact prior declaration, priority included", () => {
  var rig = clipRig();
  rig.inner.style.setProperty("overflow", "hidden", "important");
  rig.outer.style.setProperty("overflow", "clip", "");
  DEE.unclipAncestors(rig.img);
  assert.equal(overflowOf(rig.inner), "visible/important", "precondition: overridden");

  var doc = { querySelectorAll: function () { return [rig.inner, rig.outer, rig.row]; } };
  DEE.reclipAncestors(doc);
  assert.equal(overflowOf(rig.inner), "hidden/important", "value and priority both restored");
  assert.equal(overflowOf(rig.outer), "clip/", "a non-important prior stays non-important");
  assert.equal(rig.inner.getAttribute("data-dee-unclipped"), null, "marker removed");
});

test("🔴 reclipAncestors REMOVES the property when there was no prior inline overflow", () => {
  // Restoring "" as a value would leave `overflow: ` in the declaration; the
  // ancestor must come back with no inline overflow at all, the way Discord had
  // it, so the page stylesheet governs again.
  var rig = clipRig();
  DEE.unclipAncestors(rig.img);
  assert.equal(rig.outer.style.getPropertyValue("overflow"), "visible", "precondition");
  var doc = { querySelectorAll: function () { return [rig.outer]; } };
  DEE.reclipAncestors(doc);
  assert.equal(rig.outer.style.getPropertyValue("overflow"), "",
    "no inline overflow at all — not an empty-valued declaration");
  assert.equal(rig.outer.style.cssText.includes("overflow"), false,
    "and it is genuinely gone from the declaration list");
});

test("🔴 SEAM: forget() undoes the ANCESTOR styles, not only the media markers", () => {
  // The two are marked with DIFFERENT attributes, so a forget() that queries
  // only [data-dee-enlarged] leaves every cleared container overridden. This is
  // the gap #804's own header comment called out as "there is no undo".
  var rig = clipRig();
  rig.inner.style.setProperty("overflow", "hidden", "");
  DEE.applyOverride(rig.img);
  assert.equal(rig.img.getAttribute("data-dee-enlarged"), "1", "precondition: marked");
  assert.equal(overflowOf(rig.inner), "visible/important", "precondition: ancestor overridden");

  var marked = [rig.img, rig.inner, rig.outer, rig.row];
  var doc = {
    querySelectorAll: function (sel) {
      if (sel.indexOf("data-dee-enlarged") !== -1) return [rig.img];
      if (sel.indexOf("data-dee-unclipped") !== -1) {
        return marked.filter(function (e) { return e.getAttribute("data-dee-unclipped") !== null; });
      }
      return [];
    },
  };
  DEE.forget(doc);
  assert.equal(rig.img.getAttribute("data-dee-enlarged"), null, "media marker cleared");
  assert.equal(overflowOf(rig.inner), "hidden/", "AND the ancestor override undone");
  assert.equal(rig.outer.style.cssText.includes("overflow"), false, "for every cleared ancestor");
});

test("🔴 BOTH HALVES: a clipping ancestor with NO px cap is invisible to findContainer", () => {
  // The precise shape that survived v0.2.0-0.2.3 and #804 alike. findContainer
  // looks for a px max-width/max-height; this container has neither, so
  // uncapping alone cannot reach it and the enlarged image stays cropped.
  var rig = clipRig({ innerStyle: "overflow: hidden", outerStyle: "overflow: hidden" });
  assert.equal(DEE.findContainer(rig.img), null,
    "precondition: no ancestor advertises a px cap, so the uncap half finds nothing");
  var res = DEE.applyOverride(rig.img);
  assert.equal(res.ok, true);
  assert.ok(res.unclipped >= 2,
    "the unclip half must still reach them — got " + res.unclipped);
  assert.equal(overflowOf(rig.inner), "visible/important", "and actually clear the crop");
});

test("applyOverride reports the unclip count alongside the uncap result", () => {
  // The px cap goes in the style ATTRIBUTE: the computed-style seam reads that
  // in preference to el.style, so a setProperty here would be invisible to
  // findContainer and the test would assert on a container it never found.
  var rig = clipRig({ innerStyle: "overflow: hidden; max-width: 400px" });
  var res = DEE.applyOverride(rig.img);
  assert.equal(res.removed, true, "the uncap half still reports its container");
  assert.equal(res.unclipped, 3, "and the unclip half its own count");
});

test("unclipAncestors is inert with no computed-style source, and does not throw", () => {
  var rig = clipRig();
  var realGet = globalThis.__DEE_GET_COMPUTED_STYLE__;
  globalThis.__DEE_GET_COMPUTED_STYLE__ = null;
  try {
    // In a real page getComputedStyle always exists; this is the test-env and
    // document-less path, which must degrade to 0 rather than crash the script
    // and take the whole extension down with it.
    var n = DEE.unclipAncestors(rig.img);
    assert.equal(typeof n, "number", "it returns a count either way");
  } finally {
    globalThis.__DEE_GET_COMPUTED_STYLE__ = realGet;
  }
});

test("SEAM: findMessageContainer has ONE definition, and lightbox reads it", () => {
  // It moved out of lightbox.js so unclipAncestors could share the exact
  // boundary. A copy coming back is how one caller gets fixed on a Discord
  // reshuffle and the other keeps the rotted heuristic.
  assert.equal(typeof DEE.findMessageContainer, "function",
    "embed_enlarge owns the definition");
  var lb = readFileSync(new URL("../extension/lightbox.js", import.meta.url), "utf8");
  assert.ok(!/function\s+findMessageContainer/.test(lb),
    "lightbox.js must not redefine it");
  assert.ok(!/MESSAGE_WALK_DEPTH\s*=/.test(lb),
    "nor keep its own copy of the walk depth");
  assert.ok(lb.includes("dee.findMessageContainer"),
    "it must consume the shared one");
});
