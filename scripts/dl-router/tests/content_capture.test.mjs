// Page-context extraction, driven over fixture HTML through a tiny in-test DOM.
//
// content_capture.js is a CLASSIC content script (it cannot be an ES module in
// MV3), so it is evaluated here in a `vm` context with `chrome` and `document`
// absent and DL_ROUTER_NO_AUTOSTART set. It publishes its pure functions on
// globalThis.__DLR_CAPTURE__, which is what these tests drive.
//
// All fixtures are synthetic (`Jane Doe`, `acme-studio`, `example-site.test`).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { domFromHTML, element } from "./fake_dom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = fs.readFileSync(
  path.join(here, "..", "extension", "content_capture.js"), "utf8");

// Evaluate the classic script with `globalThis`, `chrome` and `document`
// SHADOWED by parameters. This keeps it in the host realm (so arrays it
// returns are real Arrays and deepEqual works) while still isolating it: it
// sees no chrome.* and cannot touch the test's globals.
const fakeGlobal = { DL_ROUTER_NO_AUTOSTART: true };
// eslint-disable-next-line no-new-func
new Function("globalThis", "chrome", "document", source)(
  fakeGlobal, undefined, undefined);

const CAP = fakeGlobal.__DLR_CAPTURE__;

test("the content script exposes its pure functions and starts nothing", () => {
  assert.ok(CAP);
  for (const fn of ["extractContext", "extractOg", "extractJsonLd",
    "extractGeneric", "extractSiteRules", "describeTarget", "domAdapter",
    "installListeners"]) {
    assert.equal(typeof CAP[fn], "function", fn);
  }
});

// --- Open Graph ------------------------------------------------------------- //
test("Open Graph metadata is extracted under both key forms", () => {
  const dom = domFromHTML(`
    <html><head>
      <meta property="og:title" content="Jane Doe at acme-studio">
      <meta property="og:type" content="video.other">
      <meta property="video:actor" content="Jane Doe">
      <meta name="twitter:title" content="Jane Doe">
    </head><body></body></html>`);
  const og = CAP.extractOg(dom);
  assert.equal(og["og:title"], "Jane Doe at acme-studio");
  assert.equal(og.title, "Jane Doe at acme-studio", "og: prefix also stripped");
  assert.equal(og["video:actor"], "Jane Doe");
  assert.equal(og["twitter:title"], "Jane Doe");
});

test("Open Graph tags with no content are skipped", () => {
  const dom = domFromHTML('<meta property="og:title" content="">');
  assert.deepEqual(CAP.extractOg(dom), {});
});

test("missing markup yields an empty Open Graph map", () => {
  assert.deepEqual(CAP.extractOg(domFromHTML("<html><body>hi</body></html>")), {});
});

// --- JSON-LD ---------------------------------------------------------------- //
test("JSON-LD Person names are harvested", () => {
  const dom = domFromHTML(`
    <script type="application/ld+json">
      {"@type": "Person", "name": "Jane Doe"}
    </script>`);
  assert.deepEqual(CAP.extractJsonLd(dom), ["Jane Doe"]);
});

test("JSON-LD VideoObject actors and keywords are harvested", () => {
  const dom = domFromHTML(`
    <script type="application/ld+json">
    {"@type":"VideoObject","name":"A clip",
     "actor":[{"@type":"Person","name":"Jane Doe"},
              {"@type":"Person","name":"John Smith"}],
     "keywords":"acme-studio, tag two"}
    </script>`);
  const tags = CAP.extractJsonLd(dom);
  assert.deepEqual(tags, ["Jane Doe", "John Smith", "acme-studio", "tag two"]);
});

test("JSON-LD inside @graph is followed", () => {
  const dom = domFromHTML(`
    <script type="application/ld+json">
    {"@graph":[{"@type":"Person","name":"Jane Doe"}]}
    </script>`);
  assert.deepEqual(CAP.extractJsonLd(dom), ["Jane Doe"]);
});

test("an array of JSON-LD documents is handled", () => {
  const dom = domFromHTML(`
    <script type="application/ld+json">
    [{"@type":"Person","name":"Jane Doe"},{"@type":"Person","name":"John Smith"}]
    </script>`);
  assert.deepEqual(CAP.extractJsonLd(dom), ["Jane Doe", "John Smith"]);
});

test("broken JSON-LD is ignored, not thrown", () => {
  const dom = domFromHTML(`
    <script type="application/ld+json">{ this is not json </script>
    <script type="application/ld+json">{"@type":"Person","name":"Jane Doe"}</script>`);
  assert.deepEqual(CAP.extractJsonLd(dom), ["Jane Doe"]);
});

test("deeply nested hostile JSON-LD terminates", () => {
  let payload = '{"@type":"Person","name":"deep"}';
  for (let i = 0; i < 200; i += 1) payload = `{"mainEntity":${payload}}`;
  const dom = domFromHTML(
    `<script type="application/ld+json">${payload}</script>`);
  const out = CAP.extractJsonLd(dom);   // depth-bounded: finds nothing, no hang
  assert.ok(Array.isArray(out));
});

test("an enormous JSON-LD block is skipped", () => {
  const huge = `{"@type":"Person","name":"${"x".repeat(300000)}"}`;
  const dom = domFromHTML(
    `<script type="application/ld+json">${huge}</script>`);
  assert.deepEqual(CAP.extractJsonLd(dom), []);
});

test("a JSON-LD name longer than the tag cap is dropped", () => {
  const dom = domFromHTML(
    `<script type="application/ld+json">{"@type":"Person","name":"${"x".repeat(200)}"}</script>`);
  assert.deepEqual(CAP.extractJsonLd(dom), []);
});

// --- generic selectors ------------------------------------------------------- //
test("itemprop=name and meta keywords are harvested", () => {
  const dom = domFromHTML(`
    <meta name="keywords" content="Jane Doe, acme-studio , , tag three">
    <span itemprop="name">John Smith</span>`);
  const tags = CAP.extractGeneric(dom);
  assert.ok(tags.includes("John Smith"));
  assert.ok(tags.includes("Jane Doe"));
  assert.ok(tags.includes("acme-studio"));
  assert.ok(!tags.includes(""));
});

// --- per-site rules ---------------------------------------------------------- //
test("per-site rules pull subject and tag text", () => {
  const dom = domFromHTML(`
    <div class="tag-list"><a href="/t/1">Jane Doe</a><a href="/t/2">acme-studio</a></div>
    <a class="performer">John Smith</a>`);
  const tags = CAP.extractSiteRules(dom, {
    subject: ["a.performer"], tags: [".tag-list a"],
  });
  assert.deepEqual(tags, ["John Smith", "Jane Doe", "acme-studio"]);
});

test("per-site rules fall back to content/title attributes", () => {
  const dom = domFromHTML('<meta class="subject" content="Jane Doe">');
  assert.deepEqual(CAP.extractSiteRules(dom, { subject: [".subject"] }),
    ["Jane Doe"]);
});

test("a malformed per-site selector does not break capture", () => {
  const dom = domFromHTML('<a class="x">Jane Doe</a>');
  const adapter = {
    ...dom,
    querySelectorAll: (sel) => {
      if (sel === "!!!broken") throw new Error("bad selector");
      return dom.querySelectorAll(sel);
    },
  };
  assert.deepEqual(CAP.extractSiteRules(adapter, { tags: ["!!!broken"] }), []);
});

test("no rules for the site yields nothing", () => {
  const dom = domFromHTML('<a class="x">Jane Doe</a>');
  assert.deepEqual(CAP.extractSiteRules(dom, null), []);
  assert.deepEqual(CAP.extractSiteRules(dom, {}), []);
});

// --- extractContext ---------------------------------------------------------- //
const PAGE = `
  <html><head>
    <title>Jane Doe at acme-studio</title>
    <meta property="og:title" content="Jane Doe at acme-studio">
    <meta name="keywords" content="acme-studio">
    <script type="application/ld+json">{"@type":"Person","name":"Jane Doe"}</script>
  </head><body>
    <div class="tag-list"><a href="/t/1">Aster Vale</a></div>
    <a id="dl" href="https://cdn.example-site.test/f.mp4">download</a>
  </body></html>`;

test("extractContext assembles the full capture payload", () => {
  const dom = domFromHTML(PAGE, { url: "https://example-site.test/v/1" });
  const ctx = CAP.extractContext(dom,
    { href: "https://cdn.example-site.test/f.mp4", linkText: "download" },
    { "example-site.test": { tags: [".tag-list a"] } });
  assert.equal(ctx.site, "example-site.test");
  assert.equal(ctx.pageUrl, "https://example-site.test/v/1");
  assert.equal(ctx.pageTitle, "Jane Doe at acme-studio");
  assert.equal(ctx.href, "https://cdn.example-site.test/f.mp4");
  assert.equal(ctx.linkText, "download");
  assert.equal(ctx.og.title, "Jane Doe at acme-studio");
  // site rules first, then JSON-LD, then generic
  assert.deepEqual(ctx.tags, ["Aster Vale", "Jane Doe", "acme-studio"]);
});

test("site rules are matched with and without the www prefix", () => {
  const dom = domFromHTML(PAGE, { url: "https://www.example-site.test/v/1" });
  const ctx = CAP.extractContext(dom, {},
    { "example-site.test": { tags: [".tag-list a"] } });
  assert.ok(ctx.tags.includes("Aster Vale"));
});

test("tags are deduplicated case-insensitively", () => {
  const dom = domFromHTML(`
    <span itemprop="name">Jane Doe</span>
    <script type="application/ld+json">{"@type":"Person","name":"JANE DOE"}</script>`);
  const ctx = CAP.extractContext(dom, {}, null);
  assert.deepEqual(ctx.tags, ["JANE DOE"]);
});

test("extractContext works on a page with no metadata at all", () => {
  const dom = domFromHTML("<html><body><p>nothing here</p></body></html>",
    { url: "https://example-site.test/x" });
  const ctx = CAP.extractContext(dom, { href: "https://example-site.test/f.mp4" });
  assert.deepEqual(ctx.tags, []);
  assert.deepEqual(ctx.og, {});
  assert.equal(ctx.site, "example-site.test");
});

test("an unparseable page URL yields an empty site rather than throwing", () => {
  const dom = domFromHTML("<html></html>", { url: "not a url" });
  assert.equal(CAP.extractContext(dom, {}).site, "");
});

test("long link text and alt are clamped", () => {
  const dom = domFromHTML("<html></html>");
  const ctx = CAP.extractContext(dom,
    { linkText: "x".repeat(1000), alt: "y".repeat(1000) });
  assert.equal(ctx.linkText.length, 300);
  assert.equal(ctx.alt.length, 300);
});

test("non-string element fields are coerced away", () => {
  const dom = domFromHTML("<html></html>");
  const ctx = CAP.extractContext(dom, { href: 42, mediaSrc: null, linkText: {} });
  assert.equal(ctx.href, "");
  assert.equal(ctx.mediaSrc, "");
  assert.equal(ctx.linkText, "");
});

// --- describeTarget ---------------------------------------------------------- //
test("describeTarget recognises a link, an image and a video", () => {
  const a = element("a", { href: "https://example-site.test/f.mp4" }, "download");
  assert.deepEqual(CAP.describeTarget(a),
    { href: "https://example-site.test/f.mp4", linkText: "download" });

  const img = element("img", { src: "https://example-site.test/i.jpg", alt: "Jane Doe" });
  const imgOut = CAP.describeTarget(img);
  assert.equal(imgOut.mediaSrc, "https://example-site.test/i.jpg");
  assert.equal(imgOut.alt, "Jane Doe");

  const video = element("video", { src: "https://example-site.test/v.m3u8" });
  assert.equal(CAP.describeTarget(video).mediaSrc,
    "https://example-site.test/v.m3u8");
});

test("describeTarget walks up to the nearest interesting ancestor", () => {
  const dom = domFromHTML(
    '<a href="https://example-site.test/f.mp4"><span>inner text</span></a>');
  const span = dom.querySelectorAll("span")[0];
  const out = CAP.describeTarget(span);
  assert.equal(out.href, "https://example-site.test/f.mp4");
});

test("describeTarget returns null for an uninteresting element", () => {
  assert.equal(CAP.describeTarget(element("p", {}, "text")), null);
  assert.equal(CAP.describeTarget(null), null);
});

test("an anchor with no href is not a target", () => {
  assert.equal(CAP.describeTarget(element("a", {}, "no href")), null);
});

// --- listener installation ---------------------------------------------------- //
test("listeners are installed on the capture phase for the three events", () => {
  const added = [];
  const doc = { addEventListener: (name, fn, capture) => added.push([name, capture]) };
  CAP.installListeners(doc, () => {}, () => null);
  assert.deepEqual(added,
    [["mousedown", true], ["click", true], ["contextmenu", true]]);
});

test("a click on a link sends a capture; a click on prose does not", () => {
  const sent = [];
  const listeners = {};
  const dom = domFromHTML(PAGE, { url: "https://example-site.test/v/1" });
  const doc = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    querySelectorAll: dom.querySelectorAll,
    location: { href: "https://example-site.test/v/1" },
    title: "Jane Doe at acme-studio",
  };
  CAP.installListeners(doc, (payload) => sent.push(payload), () => null);

  listeners.click({ target: element("a", { href: "https://example-site.test/f.mp4" }, "dl") });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].href, "https://example-site.test/f.mp4");

  listeners.click({ target: element("p", {}, "just text") });
  assert.equal(sent.length, 1, "prose clicks are ignored");
});

test("a throwing send never propagates out of the listener", () => {
  const listeners = {};
  const dom = domFromHTML(PAGE);
  const doc = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    querySelectorAll: dom.querySelectorAll,
    location: { href: "https://example-site.test/v/1" },
    title: "",
  };
  CAP.installListeners(doc, () => { throw new Error("SW asleep"); }, () => null);
  listeners.click({ target: element("a", { href: "https://example-site.test/f" }, "x") });
});
