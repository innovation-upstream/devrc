// Per-player download buttons, driven over the two COMMITTED fixtures through
// the same in-test DOM the capture extractors use.
//
// player_buttons.js is a CLASSIC content script (it cannot be an ES module in
// MV3), so it is evaluated here with `globalThis`, `chrome` and `document`
// shadowed by parameters and DL_ROUTER_NO_AUTOSTART set. content_capture.js is
// evaluated into the SAME fake global first, because that is exactly how the
// two share an isolated world in the browser -- player_buttons.js reuses its
// `rulesForHost`/`extractContext` rather than restating them, and a test that
// loaded them separately would not be testing that.
//
// The fixtures are sanitised captures of the real structure: every host is
// *.example.test, every id/name/title is synthetic.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { playerDomFromHTML, domFromHTML } from "./fake_dom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const read = (...p) => fs.readFileSync(path.join(here, ...p), "utf8");

const EMBED_HTML = read("fixtures", "embed-player-frame.html");
const FORUM_HTML = read("fixtures", "forum-thread-page.html");

const fakeGlobal = { DL_ROUTER_NO_AUTOSTART: true };
for (const file of ["content_capture.js", "player_buttons.js"]) {
  // eslint-disable-next-line no-new-func
  new Function("globalThis", "chrome", "document",
    read("..", "extension", file))(fakeGlobal, undefined, undefined);
}
const P = fakeGlobal.__DLR_PLAYER__;
const CAP = fakeGlobal.__DLR_CAPTURE__;

const EMBED_HOST = "embedhost.example.test";
const FORUM_HOST = "forum.example.test";
const EMBED_URL = `https://${EMBED_HOST}/embed/SYNTH8563`;
const FORUM_URL = `https://${FORUM_HOST}/threads/sample-thread.90001/page-6`;

// The two-layer rule table, exactly as it would appear in config.toml.
const RULES = {
  [FORUM_HOST]: {
    context: { subject: ["h1.p-title-value"] },
  },
  [EMBED_HOST]: {
    player: {
      container: "#video-container",
      mount: "#video-wrapper",
      media: { element: "video#main-video", attr: "src" },
    },
  },
};

function embedDom() {
  return playerDomFromHTML(EMBED_HTML, { url: EMBED_URL });
}

/** A `deps` that records what the frame asked the worker for. */
function fakeDeps({ have = { ok: true, have: false }, download = { ok: true },
  pageUrl = EMBED_URL, fail = false } = {}) {
  const sent = [];
  return {
    sent,
    of: (type) => sent.filter((m) => m.type === type),
    pageUrl: () => pageUrl,
    send: (msg) => {
      sent.push(msg);
      if (fail) return Promise.reject(new Error("no worker"));
      if (msg.type === "dlr:have") return Promise.resolve(have);
      return Promise.resolve(download);
    },
  };
}

const rule = () => P.playerRuleFor(RULES, EMBED_HOST);

test("the content script exposes its pure functions and starts nothing", () => {
  assert.ok(P);
  for (const fn of ["isSupportedSelector", "normalisePlayerRule",
    "playerRuleFor", "readMediaUrl", "mountButton", "handleClick", "applyHave",
    "scan", "docAdapter", "pageContext", "isTopFrame"]) {
    assert.equal(typeof P[fn], "function", fn);
  }
});

// --- layer 1: the PLAYER rule, keyed on the EMBED host ---------------------- //
test("the player rule resolves against the embed host, from the fixture", () => {
  const r = rule();
  assert.ok(r, "the embed host must have a player rule");
  assert.equal(r.container, "#video-container");
  assert.equal(r.mount, "#video-wrapper");
  // A SINGLE-table config still normalises to a one-entry LIST -- that is the
  // back-compat contract, and it is asserted here rather than assumed.
  assert.deepEqual(r.media, [{ element: "video#main-video", attr: "src" }]);
  // Every selector must actually resolve in the real captured structure --
  // otherwise the rule is only asserting itself.
  const dom = embedDom();
  assert.equal(dom.queryAll(r.container).length, 1);
  const container = dom.queryAll(r.container)[0];
  assert.equal(dom.queryAll(r.mount, container).length, 1);
  assert.equal(dom.queryAll(r.media[0].element, container).length, 1);
});

test("THE PLAYER RULE IS NOT KEYED ON THE FORUM: the forum host has none", () => {
  // The forum's embed wrapper is host-agnostic, so a player rule keyed there
  // would match embeds from hosts we have no accessor for -- and would be
  // running in the wrong document to read anything anyway.
  assert.equal(P.playerRuleFor(RULES, FORUM_HOST), null);
});

test("an embed host with NO player rule renders no button at all", () => {
  const dom = embedDom();
  const before = JSON.stringify(dom.root.children.length);
  const mounted = new Map();
  const deps = fakeDeps();
  const got = P.scan(dom, P.playerRuleFor(RULES, "unknown-host.example.test"),
    deps, mounted);
  assert.deepEqual(got, []);
  assert.equal(mounted.size, 0);
  assert.equal(deps.sent.length, 0, "and nothing is asked of the worker");
  assert.equal(JSON.stringify(dom.root.children.length), before);
});

test("the top frame CANNOT reach a player: the forum DOM has no container", () => {
  // This is the whole reason the player rule lives in the embed frame. The
  // fixture's players are cross-origin iframes; from the forum document there
  // is no #video-container and no <video> anywhere.
  const dom = playerDomFromHTML(FORUM_HTML, { url: FORUM_URL });
  const r = rule();
  assert.equal(dom.queryAll(r.container).length, 0);
  assert.equal(dom.queryAll("video").length, 0);
  const frames = dom.queryAll("iframe");
  assert.ok(frames.length >= 3, `expected several embeds, got ${frames.length}`);
  for (const f of frames) {
    assert.equal(f.getAttribute("loading"), "lazy",
      "the fixture's players are lazy -- an off-screen frame has no document");
  }
  assert.deepEqual(P.scan(dom, r, fakeDeps(), new Map()), []);
});

// --- layer 2: the CONTEXT rule, keyed on the PAGE host ---------------------- //
test("the context rule resolves the subject from the forum fixture", () => {
  const entry = CAP.rulesForHost(RULES, FORUM_HOST);
  const sel = CAP.contextSelectors(entry);
  assert.deepEqual(sel.subject, ["h1.p-title-value"]);
  const dom = domFromHTML(FORUM_HTML, { url: FORUM_URL });
  const tags = CAP.extractSiteRules(dom, entry);
  assert.ok(tags.length, "the context selector must resolve in the fixture");
  assert.match(tags[0], /Subject Name/);
});

test("the explicit .context sub-table WINS over the flat legacy form", () => {
  // Both forms have to work: every existing config uses the flat one, and the
  // sub-table is what makes the two layers legible in the same table.
  const entry = { subject: [".legacy"], context: { subject: [".explicit"] } };
  assert.deepEqual(CAP.contextSelectors(entry).subject, [".explicit"]);
  assert.deepEqual(CAP.contextSelectors({ subject: [".legacy"] }).subject,
    [".legacy"]);
});

test("a context rule with junk selectors degrades to no tags, never a throw", () => {
  const dom = domFromHTML(FORUM_HTML, { url: FORUM_URL });
  assert.deepEqual(
    CAP.extractSiteRules(dom, { context: { subject: [42, null], tags: "no" } }),
    []);
});

test("pageContext answers with the TOP frame's own url and site", () => {
  const doc = domFromHTML(FORUM_HTML, { url: FORUM_URL });
  // `pageContext` builds its own adapter through CAP.domAdapter, so it is
  // handed a document-shaped object with querySelectorAll + title.
  const res = P.pageContext(
    { querySelectorAll: doc.querySelectorAll, title: doc.title,
      location: { href: FORUM_URL } },
    { location: { href: FORUM_URL } }, RULES);
  assert.equal(res.ok, true);
  assert.equal(res.context.pageUrl, FORUM_URL);
  assert.equal(res.context.site, FORUM_HOST);
  assert.ok(res.context.tags.some((t) => /Subject Name/.test(t)),
    "the context rule's subject must be in the tags it reports");
});

test("pageContext refuses rather than guesses when the extractor is absent", () => {
  const saved = fakeGlobal.__DLR_CAPTURE__;
  try {
    fakeGlobal.__DLR_CAPTURE__ = undefined;
    assert.deepEqual(P.pageContext({}, { location: { href: FORUM_URL } }, {}),
      { ok: false, error: "no_capture" });
  } finally {
    fakeGlobal.__DLR_CAPTURE__ = saved;
  }
});

// --- the media URL is read AT CLICK TIME ------------------------------------ //
test("readMediaUrl reads the signed src out of the fixture", () => {
  const dom = embedDom();
  const container = dom.queryAll("#video-container")[0];
  const url = P.readMediaUrl(dom, rule(), container);
  assert.match(url, /^https:\/\/cdn\.example\.test\/.+\?exp=\d+&token=/);
});

test("A ROTATED URL IS USED, A STALE ONE IS NEVER SENT", async () => {
  // The single most important behaviour here. The embed page re-signs the
  // media URL in place on the SAME element, ~60s before a ~2h expiry. A button
  // that captured the URL when it was drawn would send a dead link.
  const dom = embedDom();
  const deps = fakeDeps();
  const [handle] = P.scan(dom, rule(), deps, new Map());
  const video = dom.queryAll("video#main-video")[0];
  const stale = video.getAttribute("src");
  assert.ok(stale);

  const fresh = "https://cdn.example.test/media/data/sample-file.mp4"
    + "?exp=1999999999&token=ROTATED_TOKEN&fn=sample-file.mp4";
  video.setAttribute("src", fresh);

  await P.handleClick(dom, handle, deps);
  const [msg] = deps.of("dlr:player-download");
  assert.ok(msg, "the click must reach the worker");
  assert.equal(msg.mediaUrl, fresh);
  for (const sent of deps.sent) {
    assert.notEqual(sent.mediaUrl, stale,
      "the pre-rotation URL must never appear in anything sent");
  }

  // AND IT IS NOT CACHED AFTER THE FIRST READ EITHER. One read at click time
  // looks identical to a memoised one until the second click, which on a page
  // left open for a couple of hours is the one that matters.
  const later = fresh.replace("ROTATED_TOKEN", "ROTATED_AGAIN");
  video.setAttribute("src", later);
  await P.handleClick(dom, handle, deps);
  assert.deepEqual(deps.of("dlr:player-download").map((m) => m.mediaUrl),
    [fresh, later]);
});

test("the click carries the STABLE embed page url, not the signed media url", () => {
  const dom = embedDom();
  const deps = fakeDeps();
  const [handle] = P.scan(dom, rule(), deps, new Map());
  return P.handleClick(dom, handle, deps).then(() => {
    const [msg] = deps.of("dlr:player-download");
    assert.equal(msg.embedUrl, EMBED_URL);
    assert.notEqual(msg.embedUrl, msg.mediaUrl);
  });
});

test("clicking through the button element sends the same message", async () => {
  const dom = embedDom();
  const deps = fakeDeps();
  const [handle] = P.scan(dom, rule(), deps, new Map());
  handle.button.fire("click");
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(deps.of("dlr:player-download").length, 1);
});

test("a player whose frame has not assigned a src yet says so, and sends nothing",
  async () => {
    // `loading="lazy"` + `preload="none"`: the container can exist before the
    // media URL does. The button must render, survive, and resolve lazily --
    // never fire a download with an empty URL, never silently do nothing.
    const dom = embedDom();
    const video = dom.queryAll("video#main-video")[0];
    video.attrs.src = "";
    const deps = fakeDeps();
    const [handle] = P.scan(dom, rule(), deps, new Map());
    assert.ok(handle, "the button still renders over an unloaded player");
    const res = await P.handleClick(dom, handle, deps);
    assert.deepEqual(res, { ok: false, error: "no_media_url" });
    assert.deepEqual(deps.of("dlr:player-download"), []);
    assert.match(handle.button.textContent, /No media yet/);

    // ...and it resolves once the frame assigns one, with no remount.
    video.setAttribute("src", "https://cdn.example.test/media/x.mp4?exp=1&token=T");
    await P.handleClick(dom, handle, deps);
    assert.equal(deps.of("dlr:player-download").length, 1);
  });

test("a non-http media value is treated as no URL at all", () => {
  const dom = embedDom();
  dom.queryAll("video#main-video")[0].setAttribute("src", "blob:abcd-1234");
  assert.equal(P.readMediaUrl(dom, rule(), dom.queryAll("#video-container")[0]),
    "");
});

test("readMediaUrl falls back to the same-named PROPERTY", () => {
  // A host that only exposes `currentSrc` -- which is not an attribute at all.
  const dom = embedDom();
  const video = dom.queryAll("video#main-video")[0];
  video.attrs.src = "";
  video.src = "https://cdn.example.test/media/prop.mp4?exp=1&token=T";
  assert.equal(P.readMediaUrl(dom, rule(), dom.queryAll("#video-container")[0]),
    video.src);
});

test("the click reports a refusal from the worker instead of claiming success",
  async () => {
    const dom = embedDom();
    const deps = fakeDeps({ download: { ok: false, error: "nope" } });
    const [handle] = P.scan(dom, rule(), deps, new Map());
    const res = await P.handleClick(dom, handle, deps);
    assert.equal(res.ok, false);
    assert.match(handle.button.textContent, /Failed/);
  });

test("a worker that is not there does not wedge the button", async () => {
  const dom = embedDom();
  const deps = fakeDeps({ fail: true });
  const [handle] = P.scan(dom, rule(), deps, new Map());
  const res = await P.handleClick(dom, handle, deps);
  assert.deepEqual(res, { ok: false, error: "send_failed" });
  assert.equal(handle.button.disabled, false, "and it is clickable again");
});

// --- mounting --------------------------------------------------------------- //
test("the widget mounts inside the rule's mount point, in a CLOSED shadow root",
  () => {
    const dom = embedDom();
    const [handle] = P.scan(dom, rule(), fakeDeps(), new Map());
    const wrapper = dom.queryAll("#video-wrapper")[0];
    assert.equal(handle.host.parentElement, wrapper);
    assert.equal(handle.host.shadowRoot.mode, "closed");
    assert.equal(handle.button.textContent, "Save to library");
  });

test("with no mount selector the widget lands on the container itself", () => {
  const dom = embedDom();
  const r = { ...rule(), mount: "" };
  const [handle] = P.scan(dom, r, fakeDeps(), new Map());
  assert.equal(handle.host.parentElement,
    dom.queryAll("#video-container")[0]);
});

test("a mount selector that does not resolve falls back to the container", () => {
  const dom = embedDom();
  const r = { ...rule(), mount: "#nothing-here" };
  const [handle] = P.scan(dom, r, fakeDeps(), new Map());
  assert.equal(handle.host.parentElement,
    dom.queryAll("#video-container")[0]);
});

test("scanning twice does not stack two buttons on one player", () => {
  const dom = embedDom();
  const mounted = new Map();
  const deps = fakeDeps();
  assert.equal(P.scan(dom, rule(), deps, mounted).length, 1);
  assert.equal(P.scan(dom, rule(), deps, mounted).length, 0);
  assert.equal(deps.of("dlr:have").length, 1, "and the ledger is asked once");
});

test("A CONTAINER THAT APPEARS AFTER LOAD IS PICKED UP BY A LATER SCAN", () => {
  // The players are `loading="lazy"` and the embed page builds its own DOM, so
  // the container routinely does not exist when the content script first runs.
  // This is what the mutation observer drives.
  const dom = embedDom();
  const container = dom.queryAll("#video-container")[0];
  const parent = container.parentElement;
  container.remove();
  const mounted = new Map();
  const deps = fakeDeps();
  assert.deepEqual(P.scan(dom, rule(), deps, mounted), [],
    "nothing to mount before the player exists");

  parent.appendChild(container);
  const [handle] = P.scan(dom, rule(), deps, mounted);
  assert.ok(handle, "the late container gets its button");
  assert.equal(handle.host.isConnected, true);
});

test("a page that rips the widget out gets it back on the next scan", () => {
  const dom = embedDom();
  const mounted = new Map();
  const deps = fakeDeps();
  const [first] = P.scan(dom, rule(), deps, mounted);
  first.host.remove();
  const [second] = P.scan(dom, rule(), deps, mounted);
  assert.ok(second, "a framework re-render must not cost the button forever");
  assert.notEqual(second.host, first.host);
});

// --- the "already have this" badge ------------------------------------------ //
test("BADGE HIT: the ledger answer is asked by the EMBED URL and shown", () => {
  const dom = embedDom();
  const deps = fakeDeps({ have: { ok: true, have: true, dir: "Jane Doe" } });
  const [handle] = P.scan(dom, rule(), deps, new Map());
  const [ask] = deps.of("dlr:have");
  assert.equal(ask.embedUrl, EMBED_URL,
    "keyed on the stable embed url -- a signed media url would never hit");
  return Promise.resolve().then(() => {
    assert.ok(handle.badge, "a hit must be visible");
    assert.match(handle.badge.textContent, /In library: Jane Doe/);
  });
});

test("BADGE MISS: no record renders nothing at all", async () => {
  const dom = embedDom();
  const deps = fakeDeps({ have: { ok: true, have: false } });
  const [handle] = P.scan(dom, rule(), deps, new Map());
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(handle.badge, null);
  assert.equal(handle.row.children.length, 1, "the button, and only the button");
});

test("a ledger that cannot answer produces no badge, not a broken one", async () => {
  const dom = embedDom();
  const deps = fakeDeps({ have: { ok: false, have: false } });
  const [handle] = P.scan(dom, rule(), deps, new Map());
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(handle.badge, null);
});

test("applyHave is idempotent -- two answers do not stack two badges", () => {
  const dom = embedDom();
  const [handle] = P.scan(dom, rule(), fakeDeps(), new Map());
  const hit = { ok: true, have: true, dir: "Jane Doe" };
  assert.equal(P.applyHave(dom, handle, hit), true);
  assert.equal(P.applyHave(dom, handle, hit), false);
  assert.equal(handle.row.children.length, 2);
});

// --- config is DATA, never code --------------------------------------------- //
test("a player rule carrying code-shaped fields keeps only the known ones", () => {
  // The whole point of a declarative rule table: there is no path here that can
  // evaluate a string, so a hostile or careless config cannot get code into a
  // content script running on every page.
  const r = P.normalisePlayerRule({
    player: {
      container: "#c",
      media: { element: "video", attr: "src", getter: "() => 1" },
      js: "alert(1)",
      onclick: "alert(1)",
      code: "fetch('//x')",
      label: "Save",
    },
  });
  assert.deepEqual(Object.keys(r).sort(),
    ["container", "label", "media", "mount"]);
  assert.equal(Array.isArray(r.media), true, "media normalises to a list");
  assert.deepEqual(Object.keys(r.media[0]).sort(), ["attr", "element"]);
});

test("the media accessor must be an attribute NAME, not an expression", () => {
  const base = { container: "#c" };
  for (const attr of ["src()", "a.b", "0src", "", " src", "sr c", "__proto__"]) {
    assert.equal(
      P.normalisePlayerRule({ player: { ...base, media: { element: "video", attr } } }),
      null, `attr ${JSON.stringify(attr)} must be refused`);
  }
  assert.ok(P.normalisePlayerRule(
    { player: { ...base, media: { element: "video", attr: "data-src" } } }));
});

test("selectors are held to the subset the test DOM can parse", () => {
  // A richer selector would work in Brave and silently have no test, which is
  // how the two drift. Refusing it here is what keeps the suite honest.
  for (const bad of ["div:has(video)", "div > video", ".a:not(.b)", "a + b",
    "a ~ b", "video::shadow"]) {
    assert.equal(P.isSupportedSelector(bad), false, bad);
  }
  for (const good of ["#video-container", "video#main-video", ".plyr__video",
    'a[data-plyr="download"]', '[href^="https://x"]', "div.a .b, .c"]) {
    assert.equal(P.isSupportedSelector(good), true, good);
  }
});

test("an incomplete player rule yields NO rule, and therefore no button", () => {
  for (const raw of [
    null, {}, { player: {} }, { player: { container: "#c" } },
    { player: { container: "#c", media: {} } },
    { player: { container: "#c", media: { element: "video" } } },
    { player: { container: "div > x", media: { element: "v", attr: "src" } } },
    { player: { container: "#c", media: { element: "v:has(x)", attr: "src" } } },
    { player: "not-a-table" },
  ]) {
    assert.equal(P.normalisePlayerRule(raw), null, JSON.stringify(raw));
  }
});

test("a rule's label is bounded, and defaults", () => {
  const media = { element: "video", attr: "src" };
  assert.equal(
    P.normalisePlayerRule({ player: { container: "#c", media } }).label,
    "Save to library");
  assert.equal(P.normalisePlayerRule(
    { player: { container: "#c", media, label: "x".repeat(41) } }).label,
    "Save to library");
  assert.equal(P.normalisePlayerRule(
    { player: { container: "#c", media, label: "Grab" } }).label, "Grab");
});

// --- fixture hygiene -------------------------------------------------------- //
test("THE COMMITTED FIXTURES NAME NO REAL SITE", () => {
  // This repo is public. The forum, the embed host and the CDN must exist only
  // as *.example.test in anything committed; the real names live in the
  // operator's own config.toml and nowhere else.
  for (const [name, html] of [["embed", EMBED_HTML], ["forum", FORUM_HTML]]) {
    const hosts = [...html.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)]
      .map((m) => m[1].toLowerCase());
    assert.ok(hosts.length, `${name} fixture has no URLs to check`);
    for (const host of new Set(hosts)) {
      assert.ok(/(^|\.)(example\.test|schema\.org)$/.test(host),
        `${name} fixture references a real-looking host: ${host}`);
    }
  }
});

test("prefers-reduced-motion is respected by the widget's own stylesheet", () => {
  const src = read("..", "extension", "player_buttons.js");
  assert.match(src, /@media \(prefers-reduced-motion: reduce\)/);
});

test("the media URL is read in the click path and NOWHERE on the render path",
  () => {
    // A structural pin for the rotation rule above: if a future change caches
    // the URL at mount time this fails even if the click test is adjusted.
    const src = read("..", "extension", "player_buttons.js");
    const build = src.slice(src.indexOf("function build("),
      src.indexOf("function setLabel("));
    const mount = src.slice(src.indexOf("function mountButton("),
      src.indexOf("function isConnected("));
    for (const [name, body] of [["build", build], ["mountButton", mount]]) {
      assert.ok(!/readMediaUrl\s*\(/.test(body),
        `${name} must not read the media URL -- it would be stale by click time`);
    }
    assert.match(src.slice(src.indexOf("function handleClick(")),
      /var url = readMediaUrl\(/);
  });

// --- an ORDERED LIST of media accessors ------------------------------------ //
// WHY THIS EXISTS. `attr` is a single NAME, so one {element, attr} pair cannot
// say "the image's ANCHOR href, but the video's own src". A site that posts
// both kinds -- an <img> downscaled by a resizing proxy under an <a> pointing
// at the original, alongside a <video> whose src IS the original -- is not
// expressible with one pair: reading `src` saves the thumbnail, reading `href`
// is dead for video. The list is tried in ORDER, first http(s) hit wins.

const LIST_HOST = "twokinds.example.test";
const ORIGIN = "https://cdn.example-cdn.test/attachments/900/1/a.png";
const PROXY = "https://proxy.example-cdn.test/attachments/900/1/a.png?w=550";
const VIDEO = "https://cdn.example-cdn.test/attachments/900/2/clip.mp4?sig=1";
const LIST_RULES = {
  [LIST_HOST]: {
    player: {
      container: "#wrap",
      media: [
        { element: 'a[href^="https://cdn.example-cdn.test/"]', attr: "href" },
        { element: "video", attr: "src" },
      ],
    },
  },
};
const listRule = () => P.playerRuleFor(LIST_RULES, LIST_HOST);

test("a list of accessors normalises in order, and a single table still works",
  () => {
    const r = listRule();
    assert.ok(r);
    assert.deepEqual(r.media, [
      { element: 'a[href^="https://cdn.example-cdn.test/"]', attr: "href" },
      { element: "video", attr: "src" },
    ]);
    // back-compat, restated here so the two shapes are pinned side by side
    assert.deepEqual(rule().media,
      [{ element: "video#main-video", attr: "src" }]);
  });

test("AN IMAGE RESOLVES THROUGH ITS ANCHOR, not its downscaled src", () => {
  // The whole point. `src` is present and is an http URL, so a first-match
  // reader that ignored order would happily return the proxy copy.
  const dom = playerDomFromHTML(
    `<div id="wrap"><a href="${ORIGIN}"><img src="${PROXY}"></a></div>`);
  const container = dom.queryAll("#wrap")[0];
  assert.equal(P.readMediaUrl(dom, listRule(), container), ORIGIN);
});

test("A VIDEO RESOLVES THROUGH ITS OWN src -- the anchor accessor misses", () => {
  const dom = playerDomFromHTML(
    `<div id="wrap"><video src="${VIDEO}"></video></div>`);
  const container = dom.queryAll("#wrap")[0];
  assert.equal(P.readMediaUrl(dom, listRule(), container), VIDEO);
});

test("ORDER decides: swapping the accessors saves the thumbnail instead", () => {
  // The control for the two tests above. If order did not matter they would
  // both pass with the list reversed, and neither would be evidence.
  const reversed = P.normalisePlayerRule({
    player: {
      container: "#wrap",
      media: [
        { element: "img", attr: "src" },
        { element: 'a[href^="https://cdn.example-cdn.test/"]', attr: "href" },
      ],
    },
  });
  const dom = playerDomFromHTML(
    `<div id="wrap"><a href="${ORIGIN}"><img src="${PROXY}"></a></div>`);
  const container = dom.queryAll("#wrap")[0];
  assert.equal(P.readMediaUrl(dom, reversed, container), PROXY);
});

test("a later accessor is reached only when the earlier ones find nothing",
  () => {
    // Both accessors are valid; only the second can match this DOM.
    const dom = playerDomFromHTML(
      `<div id="wrap"><video src="${VIDEO}"></video></div>`);
    const container = dom.queryAll("#wrap")[0];
    assert.equal(P.readMediaUrl(dom, listRule(), container), VIDEO);
    // ...and with neither present, the reader reports nothing rather than
    // guessing, so the button says "No media yet" instead of firing.
    const bare = playerDomFromHTML(`<div id="wrap"></div>`);
    assert.equal(
      P.readMediaUrl(bare, listRule(), bare.queryAll("#wrap")[0]), "");
  });

test("ONE BAD ACCESSOR REJECTS THE WHOLE RULE -- no partial button", () => {
  // Dropping the bad entry and keeping the good one would render a button that
  // silently covers only some media on the page, which is the failure mode
  // normalisePlayerRule's docstring calls worse than no button.
  for (const bad of [{ element: "video", attr: "src()" },
    { element: "a:hover", attr: "href" },
    { element: "video", attr: "" },
    "not-a-table",
    null]) {
    const r = P.normalisePlayerRule({
      player: { container: "#wrap",
        media: [{ element: "img", attr: "src" }, bad] },
    });
    assert.equal(r, null, JSON.stringify(bad));
  }
});

test("an empty list and an over-long list are both refused", () => {
  assert.equal(P.normalisePlayerRule(
    { player: { container: "#wrap", media: [] } }), null);
  const many = [];
  for (let i = 0; i < 9; i += 1) many.push({ element: "video", attr: "src" });
  assert.equal(P.normalisePlayerRule(
    { player: { container: "#wrap", media: many } }), null);
  // ...and the boundary itself is accepted, so the cap is off-by-one-proof.
  const eight = many.slice(0, 8);
  assert.ok(P.normalisePlayerRule(
    { player: { container: "#wrap", media: eight } }));
});
