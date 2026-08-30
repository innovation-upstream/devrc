// Service-worker glue against a MOCKED chrome + fetch. No Brave, no sidecar.
//
// Covers the wiring the pure core cannot: the profile-scoped enable gate, the
// dedupe surfacing into the toast, the toast -> notification fallback, the
// picker for a below-threshold match, relocate-after-complete, the context
// menus, and the message router.
//
// The SW's auto-start (listener registration + networking) is suppressed via
// DL_ROUTER_NO_AUTOSTART so importing it here does nothing on its own.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

globalThis.DL_ROUTER_NO_AUTOSTART = true;

// --- chrome mock ------------------------------------------------------------ //
const calls = {
  windowsCreate: [], notifications: [], downloads: [], search: [],
  fetches: [], menus: [], tabsGet: [], tabMessages: [],
  tabsUpdate: [], windowsUpdate: [], tabListeners: [],
};
let storageLocal = {};
let storageSession = {};
let searchResult = [];
let windowsCreateFails = false;
let notificationsFail = false;

// --- the in-page overlay path ----------------------------------------------- //
// Defaults reproduce "this tab cannot host an overlay", so every pre-existing
// picker test keeps asserting the popup-window fallback it was written for.
// The overlay tests opt in explicitly.
let tabUrl = "https://example-site.test/v/1";
let tabDiscarded = false;
let tabsGetFails = false;
let contentScriptMissing = true;    // sendMessage rejects: no receiving end
let overlayOpenResult = { ok: true };
let overlayReports = "ready";       // "ready" | "ready-late" | "silent"
let tabsUpdateFails = false;

// --- the player-button path -------------------------------------------------- //
// What the TOP frame answers when the worker asks it to describe itself. `null`
// reproduces "there is no content script in the top frame", which is the
// correlation failure the picker degradation depends on.
let pageContextResult = null;
let pageContextThrows = false;

// chrome.storage STRUCTURED-CLONES on the way in and on the way out. Storing
// the reference instead was a real fidelity bug in this mock: a live object
// mutated after being "persisted" appeared to have been persisted with the
// mutation, so `state.pending`'s dedupe latch tested as durable while the
// production code had not written it. Clone, or the persistence tests lie.
const clone = (v) => (v === undefined ? undefined
  : JSON.parse(JSON.stringify(v)));

globalThis.chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const k of [].concat(keys)) out[k] = clone(storageLocal[k]);
        return out;
      },
      set: async (obj) => Object.assign(storageLocal, clone(obj)),
    },
    session: {
      get: async (k) => ({ [k]: clone(storageSession[k]) }),
      set: async (obj) => Object.assign(storageSession, clone(obj)),
    },
    onChanged: { addListener() {} },
  },
  runtime: {
    getURL: (p) => `chrome-extension://test/${p}`,
    onMessage: { addListener() {} },
    getManifest: () => ({ host_permissions: ["http://127.0.0.1:8791/*"] }),
  },
  windows: {
    create: async (opts) => {
      calls.windowsCreate.push(opts);
      if (windowsCreateFails) throw new Error("no window");
      return { id: 900 + calls.windowsCreate.length };
    },
    update: async (id, info) => {
      calls.windowsUpdate.push({ id, info });
      return { id };
    },
    onRemoved: { addListener() {} },
  },
  notifications: {
    create: async (opts) => {
      calls.notifications.push(opts);
      if (notificationsFail) throw new Error("no notifications");
      return "n1";
    },
  },
  downloads: {
    search: async (q) => { calls.search.push(q); return searchResult; },
    download: async (o) => { calls.downloads.push(o); return 99; },
    onDeterminingFilename: { addListener() {} },
    onChanged: { addListener() {} },
  },
  contextMenus: {
    removeAll: async () => { calls.menus.push("removeAll"); },
    create: (o) => calls.menus.push(o),
    onClicked: { addListener() {} },
  },
  tabs: {
    query: async () => [],
    get: async (id) => {
      calls.tabsGet.push(id);
      // Chrome rejects for a tab that no longer exists -- the self-closing
      // file-host tab, which is a real case in this workflow.
      if (tabsGetFails) throw new Error("No tab with id: " + id);
      return { id, url: tabUrl, discarded: tabDiscarded, windowId: 5 };
    },
    update: async (id, info) => {
      calls.tabsUpdate.push({ id, info });
      if (tabsUpdateFails) throw new Error("no such tab");
      return { id };
    },
    sendMessage: async (id, msg, opts) => {
      calls.tabMessages.push({ id, msg, opts });
      if (msg && msg.type === "dlr:page-context") {
        if (pageContextThrows) {
          throw new Error("Could not establish connection. "
            + "Receiving end does not exist.");
        }
        return pageContextResult;
      }
      if (msg && msg.type === "dlr:overlay-open") {
        if (contentScriptMissing) {
          throw new Error("Could not establish connection. "
            + "Receiving end does not exist.");
        }
        // The frame booted and announced itself, via the same message the real
        // picker page sends. WHEN it announces is a genuine race: the content
        // script returns as soon as the DOM nodes exist, and the frame's boot
        // is a separate round trip that can land on either side of that.
        const announce = () => SW.onMessage(
          { type: "dlr:picker-ready", overlay: msg.id }, {}, () => {});
        if (overlayReports === "ready") queueMicrotask(announce);   // early
        if (overlayReports === "ready-late") setTimeout(announce, 5);
        return overlayOpenResult;
      }
      return { ok: true };
    },
    onActivated: { addListener() {} },
    // Registered by registerListeners so an overlay whose tab dies becomes a
    // window again. Present here so that registration is real, not a silent
    // no-op swallowed by its try/catch.
    onRemoved: { addListener: (fn) => calls.tabListeners.push(["removed", fn]) },
    onUpdated: { addListener: (fn) => calls.tabListeners.push(["updated", fn]) },
  },
  alarms: { create() {}, onAlarm: { addListener() {} } },
};

// --- fetch mock ------------------------------------------------------------- //
let fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({}) });
globalThis.fetch = async (url, opts) => {
  calls.fetches.push({ url, opts });
  return fetchHandler(url, opts);
};

const SW = await import("../extension/service_worker.js");

const LIB_ROOT = "/home/u/library";

const SNAPSHOT = {
  etag: "abc123",
  otherDir: "other",
  // The extension needs the library root to prove a completed download landed
  // INSIDE the library before it asks /relocate to move anything.
  root: LIB_ROOT,
  threshold: 0.75,
  matchTimeoutMs: 400,
  captureWindowS: 15,
  toastMs: 8000,
  dirs: [
    // `kind` gates auto-filing in the cached fallback the same way it does in
    // the sidecar; unclassified never auto-files.
    { name: "Jane Doe", key: "janedoe", tokens: ["jane", "doe"],
      kind: "performer" },
    { name: "john-smith", key: "johnsmith", tokens: ["john", "smith"],
      kind: "performer" },
  ],
  aliases: [],
  siteRules: { "example-site.test": { tags: [".tag a"] } },
};

function reset({ enabled = true, snapshot = SNAPSHOT } = {}) {
  for (const k of Object.keys(calls)) calls[k].length = 0;
  storageLocal = { token: "tok", enabled };
  storageSession = {};
  searchResult = [];
  windowsCreateFails = false;
  notificationsFail = false;
  tabUrl = "https://example-site.test/v/1";
  tabDiscarded = false;
  tabsGetFails = false;
  contentScriptMissing = true;
  overlayOpenResult = { ok: true };
  overlayReports = "ready";
  tabsUpdateFails = false;
  pageContextResult = null;
  pageContextThrows = false;
  SW.state.overlays.clear();
  SW.state.config = { port: 8791, token: "tok", enabled };
  SW.state.configLoaded = true;
  SW.state.pendingFetch.clear();
  SW.state.ownWindowIds.clear();
  SW.state.snapshot = snapshot;
  SW.state.etag = snapshot ? `"${snapshot.etag}"` : null;
  SW.state.captures = [];
  SW.state.pending.clear();
  SW.state.activeTabId = 7;
  fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({}) });
}

const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function spy() {
  const c = [];
  const fn = (a) => c.push(a);
  fn.calls = c;
  return fn;
}

// --- config ----------------------------------------------------------------- //
test("loadConfig reads the per-profile settings", async () => {
  reset({ enabled: true });
  const cfg = await SW.loadConfig();
  assert.deepEqual(cfg, { port: 8791, token: "tok", enabled: true });
});

test("the port comes from the manifest, not from storage", () => {
  // host_permissions is a hard pin the options page cannot change, so a
  // configurable port setting silently bricked every fetch.
  reset();
  storageLocal.port = 9999;
  assert.equal(SW.manifestPort(), 8791);
});

test("routing is off in a profile where it was never enabled", async () => {
  reset({ enabled: false });
  const suggest = spy();
  const ret = SW.onDeterminingFilename({ id: 1, filename: "clip.mp4" }, suggest);
  assert.equal(ret, false, "must decline so Brave behaves normally");
  await settle();
  assert.equal(suggest.calls.length, 0);
});

// --- snapshot --------------------------------------------------------------- //
test("refreshSnapshot sends the bearer token and stores the payload", async () => {
  reset();
  SW.state.snapshot = null;
  SW.state.etag = null;
  fetchHandler = async () => ({ ok: true, status: 200, json: async () => SNAPSHOT });
  const out = await SW.refreshSnapshot();
  assert.equal(out.etag, "abc123");
  assert.equal(calls.fetches[0].opts.headers.Authorization, "Bearer tok");
  assert.match(calls.fetches[0].url, /^http:\/\/127\.0\.0\.1:8791\/dirs$/);
  assert.equal(storageSession.snapshot.etag, "abc123");
});

test("refreshSnapshot keeps the cached snapshot on 304", async () => {
  reset();
  fetchHandler = async () => ({ ok: false, status: 304, json: async () => ({}) });
  const out = await SW.refreshSnapshot();
  assert.equal(out.etag, "abc123");
  assert.equal(calls.fetches[0].opts.headers["If-None-Match"], '"abc123"');
});

test("a sidecar error propagates out of refreshSnapshot", async () => {
  reset();
  fetchHandler = async () => ({ ok: false, status: 500, json: async () => ({}) });
  await assert.rejects(() => SW.refreshSnapshot(), /sidecar 500/);
});

// --- captures --------------------------------------------------------------- //
test("recordCapture normalises, clamps and tags the tab", () => {
  reset();
  SW.recordCapture({
    href: "https://example-site.test/f", linkText: "x".repeat(1000),
    tags: ["Jane Doe", 42, "ok"], og: { title: "t" }, pageUrl: "p",
  }, { tab: { id: 12 } });
  const c = SW.state.captures[0];
  assert.equal(c.tabId, 12);
  assert.equal(c.linkText.length, 300);
  assert.deepEqual(c.tags, ["Jane Doe", "ok"]);
  assert.ok(typeof c.ts === "number");
});

test("the capture buffer is bounded", () => {
  reset();
  for (let i = 0; i < 200; i += 1) SW.recordCapture({ pageUrl: `p${i}` }, {});
  assert.ok(SW.state.captures.length <= 40);
  assert.equal(SW.state.captures.at(-1).pageUrl, "p199");
});

test("hostile capture payloads are ignored or coerced", () => {
  reset();
  SW.recordCapture(null, {});
  SW.recordCapture("string", {});
  SW.recordCapture({ tags: "nope", og: "nope", href: 42 }, {});
  assert.equal(SW.state.captures.length, 1);
  assert.deepEqual(SW.state.captures[0].tags, []);
  assert.equal(SW.state.captures[0].href, "");
});

// --- the download path ------------------------------------------------------ //
test("an auto-filed download suggests the matched dir and shows a toast", async () => {
  reset();
  SW.recordCapture({ href: "https://example-site.test/f.mp4",
    pageUrl: "https://example-site.test/v", tags: ["Jane Doe"] },
  { tab: { id: 7 } });
  fetchHandler = async () => ({
    ok: true, status: 200,
    json: async () => ({ dir: "Jane Doe", auto: true, confidence: 0.85,
      reason: "tag=='Jane Doe'", dup: null }),
  });
  const suggest = spy();
  SW.onDeterminingFilename(
    { id: 5, filename: "f.mp4", url: "https://example-site.test/f.mp4" }, suggest);
  await settle(10);
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "Jane Doe/f.mp4");
  assert.equal(calls.windowsCreate.length, 1);
  assert.match(calls.windowsCreate[0].url, /toast\.html/);
  assert.equal(calls.windowsCreate[0].type, "popup");
  assert.equal(calls.windowsCreate[0].focused, false);
});

test("a duplicate is surfaced in the toast", async () => {
  reset();
  fetchHandler = async () => ({
    ok: true, status: 200,
    json: async () => ({ dir: "Jane Doe", auto: true, reason: "r",
      dup: { where: "target-dir", relpath: "Jane Doe/f.mp4", kind: "name+size" } }),
  });
  SW.onDeterminingFilename({ id: 6, filename: "f.mp4" }, spy());
  await settle(10);
  const url = new URL(calls.windowsCreate[0].url);
  assert.match(url.searchParams.get("dup"), /already in this folder/);
});

test("a below-threshold match opens the picker instead of the toast", async () => {
  reset();
  fetchHandler = async () => ({
    ok: true, status: 200,
    json: async () => ({ dir: "Jane Doe", auto: false, reason: "tie",
      suggestNew: "Aster Vale", candidates: [] }),
  });
  const suggest = spy();
  SW.onDeterminingFilename({ id: 7, filename: "f.mp4" }, suggest);
  await settle(10);
  assert.equal(suggest.calls[0].filename, "other/f.mp4");
  assert.match(calls.windowsCreate[0].url, /picker\.html/);
  assert.equal(calls.windowsCreate[0].focused, true);
  const url = new URL(calls.windowsCreate[0].url);
  assert.equal(url.searchParams.get("suggestNew"), "Aster Vale");
});

test("toast falls back to a notification when the window cannot be created", async () => {
  reset();
  windowsCreateFails = true;
  const ok = await SW.showToast({ downloadId: 1, dir: "Jane Doe", reason: "r",
    dup: "", source: "sidecar" });
  assert.equal(ok, false);
  assert.equal(calls.notifications.length, 1);
  assert.equal(calls.notifications[0].title, "Filed to Jane Doe");
});

test("a failing notification fallback is still swallowed", async () => {
  reset();
  windowsCreateFails = true;
  notificationsFail = true;
  await SW.showToast({ downloadId: 1, dir: "Jane Doe" });   // must not throw
});

test("the picker falls back to a toast when its window cannot be created", async () => {
  reset();
  windowsCreateFails = true;
  await SW.openPicker({ downloadId: 1, dir: "other", reason: "r" });
  assert.equal(calls.notifications.length, 1);
});

test("a sidecar outage still suggests, from the cached snapshot", async () => {
  reset();
  SW.recordCapture({ pageUrl: "p", tags: ["john-smith"] }, { tab: { id: 7 } });
  fetchHandler = async () => { throw new Error("ECONNREFUSED"); };
  const suggest = spy();
  SW.onDeterminingFilename({ id: 8, filename: "f.mp4" }, suggest);
  await settle(10);
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "john-smith/f.mp4");
});

test("no snapshot and no sidecar still suggests exactly once", async () => {
  reset({ snapshot: null });
  fetchHandler = async () => { throw new Error("down"); };
  const suggest = spy();
  SW.onDeterminingFilename({ id: 9, filename: "f.mp4" }, suggest);
  await settle(10);
  assert.equal(suggest.calls.length, 1);
  assert.equal(suggest.calls[0].filename, "other/f.mp4");
});

// --- corrections ------------------------------------------------------------ //
test("applyChoice relocates a completed download and learns", async () => {
  reset();
  SW.state.pending.set(11, { dir: "other", filename: "f.mp4",
    payload: { page: {} }, decision: null });
  searchResult = [{ id: 11, state: "complete",
    filename: "/home/u/library/other/f.mp4" }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const out = await SW.applyChoice(11, "Jane Doe");
  assert.deepEqual(out, { ok: true, dir: "Jane Doe" });
  const relocate = posted.find((p) => p.url.endsWith("/relocate"));
  // The sidecar proves ownership from its OWN record of this downloadId --
  // name + write time. Nothing the extension could assert here would add
  // evidence, so nothing is asserted.
  assert.deepEqual(relocate.body,
    { fromRelPath: "other/f.mp4", toDir: "Jane Doe", downloadId: 11 });
  const learn = posted.find((p) => p.url.endsWith("/learn"));
  assert.equal(learn.body.chosenDir, "Jane Doe");
  assert.equal(learn.body.autoDir, "other");
});

test("applyChoice does not relocate when the directory is unchanged", async () => {
  reset();
  SW.state.pending.set(12, { dir: "Jane Doe", payload: {} });
  searchResult = [{ id: 12, state: "complete",
    filename: "/home/u/library/Jane Doe/f.mp4" }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push(url);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.applyChoice(12, "Jane Doe");
  assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0);
});

test("applyChoice on an in-flight download defers to onChanged", async () => {
  reset();
  SW.state.pending.set(13, { dir: "other", payload: {} });
  searchResult = [{ id: 13, state: "in_progress" }];
  fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({}) });
  await SW.applyChoice(13, "Jane Doe");
  assert.equal(SW.state.pending.get(13).wanted, "Jane Doe");

  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  searchResult = [{ id: 13, state: "complete",
    filename: "/home/u/library/other/f.mp4" }];
  await SW.onDownloadChanged({ id: 13, state: { current: "complete" } });
  const relocate = posted.find((p) => p.url.endsWith("/relocate"));
  assert.equal(relocate.body.toDir, "Jane Doe");
});

test("applyChoice creates a new directory first when asked", async () => {
  reset();
  SW.state.pending.set(14, { dir: "other", payload: {} });
  searchResult = [{ id: 14, state: "complete",
    filename: "/home/u/library/other/f.mp4" }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push(url);
    if (url.endsWith("/dirs")) {
      return { ok: true, status: 200,
        json: async () => ({ ...SNAPSHOT, etag: "new",
          dirs: [...SNAPSHOT.dirs, { name: "Aster Vale", key: "astervale",
            tokens: ["aster", "vale"], kind: "performer" }] }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.applyChoice(14, "Aster Vale", { createdNew: true });
  assert.ok(posted.some((u) => u.endsWith("/mkdir")));
  assert.ok(posted.some((u) => u.endsWith("/dirs")));
  assert.ok(posted.some((u) => u.endsWith("/relocate")));
});

test("applyChoice refuses an unsafe or unknown directory", async () => {
  reset();
  for (const bad of ["../escape", "a/b", "..", "Not In Snapshot", ""]) {
    await assert.rejects(() => SW.applyChoice(15, bad), /unsafe|refusing/i, bad);
  }
});

test("onDownloadChanged ignores non-completion and unknown downloads", async () => {
  reset();
  const before = calls.fetches.length;
  await SW.onDownloadChanged({ id: 1, state: { current: "in_progress" } });
  await SW.onDownloadChanged({ id: 999, state: { current: "complete" } });
  await SW.onDownloadChanged(null);
  assert.equal(calls.fetches.length, before);
});

// --- context menus ---------------------------------------------------------- //
test("installMenus registers one entry for link/image/video", async () => {
  reset();
  await SW.installMenus();
  const created = calls.menus.find((m) => typeof m === "object");
  assert.equal(created.id, SW.MENU_ID);
  assert.deepEqual(created.contexts, ["link", "image", "video"]);
});

test("the menu downloads a plain link", async () => {
  reset();
  await SW.onMenuClicked({ menuItemId: SW.MENU_ID,
    linkUrl: "https://example-site.test/f.mp4" }, {});
  assert.deepEqual(calls.downloads[0], { url: "https://example-site.test/f.mp4" });
});

test("the menu saves the original, not the proxy thumbnail in the src", async () => {
  // Discord puts a downscaled webp from its resizing proxy in the <img src>
  // and the posted file on the wrapping <a href>. Taking `srcUrl` because it
  // is listed first saves the thumbnail and silently calls it the download.
  reset();
  const ch = "119283746551234567";
  const original
    = `https://cdn.discordapp.com/attachments/${ch}/998877665544332211/a.png`;
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    mediaType: "image",
    srcUrl: `https://media.discordapp.net/attachments/${ch}`
      + "/998877665544332211/a.png?format=webp&width=550",
    linkUrl: `${original}?ex=1&is=2&hm=3`,
    pageUrl: "https://discord.com/channels/1/2",
  }, {});
  assert.deepEqual(calls.downloads[0], { url: `${original}?ex=1&is=2&hm=3` });
});

test("a Discord video is downloaded directly, never handed to yt-dlp", async () => {
  // `mediaType === "video"` exists for players whose src is a `blob:`. A CDN
  // attachment is a direct file, so that clause would send yt-dlp at
  // `discord.com/channels/<guild>/<channel>` while the .mp4 sat in `srcUrl`.
  reset();
  const clip = "https://cdn.discordapp.com/attachments"
    + "/119283746551234567/998877665544332211/clip.mp4?ex=1&is=2&hm=3";
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    mediaType: "video",
    srcUrl: clip,
    pageUrl: "https://discord.com/channels/1/2",
  }, {});
  assert.deepEqual(calls.downloads[0], { url: clip });
  assert.equal(calls.fetches.length, 0);
});

test("a NON-Discord video still takes the yt-dlp path", async () => {
  // The control for the test above: the direct-file bypass must be the
  // narrow exception, not a removal of the blob:-player branch.
  reset();
  // `auto: true` DELIBERATELY. A below-threshold match queues the picker and
  // POSTs nothing -- correct behaviour, but then the positive assertion below
  // would be asserting the picker path instead of the yt-dlp one.
  fetchHandler = async (url) => {
    if (url.endsWith("/match")) {
      return { ok: true, status: 200,
        json: async () => ({ dir: "Jane Doe", auto: true,
          reason: "tag=='Jane Doe'" }) };
    }
    return { ok: true, status: 200, json: async () => ({ jobId: "j9" }) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    mediaType: "video",
    srcUrl: "https://cdn.example-site.test/v.mp4",
    pageUrl: "https://example-site.test/v/1",
  }, {});
  assert.equal(calls.downloads.length, 0,
    "an embedded player must still go through yt-dlp");
  // 🔴 THE ABSENCE ALONE IS NOT THE ASSERTION. Asserting only
  // `downloads.length === 0` left this guard green when `startFetch` was
  // stubbed to a no-op — it passed whether or not the yt-dlp path did
  // anything, i.e. green for a reason other than the one it names. Pin the
  // POSITIVE half too.
  const fetchCall = calls.fetches.find((f) => f.url.endsWith("/fetch"));
  assert.ok(fetchCall, "the yt-dlp path must actually POST /fetch");
  assert.equal(JSON.parse(fetchCall.opts.body).url,
    "https://example-site.test/v/1",
    "yt-dlp gets the PAGE url for an embedded player");
});

test("a Discord .m3u8 is still treated as a stream, not saved as a file", async () => {
  // The bypass must not reach a manifest: downloading a ~200-byte playlist and
  // calling it the media is a silent wrong answer where the old path failed
  // loudly.
  // 🔴 `auto: true` and a POSITIVE assertion, because the absence alone is the
  // F4 defect wearing a different hat. MEASURED: with only
  // `downloads.length === 0`, a mutant that made a Discord manifest do NOTHING
  // AT ALL survived the whole 527-test suite. An absence cannot tell "handed
  // to the stream path" from "silently dropped".
  reset();
  const manifestUrl = "https://cdn.discordapp.com/attachments"
    + "/119283746551234567/998877665544332211/live.m3u8?ex=1";
  fetchHandler = async (url) => {
    if (url.endsWith("/match")) {
      return { ok: true, status: 200,
        json: async () => ({ dir: "Jane Doe", auto: true,
          reason: "tag=='Jane Doe'" }) };
    }
    return { ok: true, status: 200, json: async () => ({ jobId: "j10" }) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    mediaType: "video",
    srcUrl: manifestUrl,
    pageUrl: "https://discord.com/channels/1/2",
  }, {});
  assert.equal(calls.downloads.length, 0,
    "a manifest must never be handed to chrome.downloads");
  const fetchCall = calls.fetches.find((f) => f.url.endsWith("/fetch"));
  assert.ok(fetchCall, "the manifest must actually reach the yt-dlp path");
  // 🔴 And it must get the MANIFEST, not the Discord page. `discord.com/
  // channels/<guild>/<channel>` is an authenticated SPA route yt-dlp cannot
  // resolve, and the job's failure is unobservable -- nothing polls `jobId`,
  // so the user would get a "filed" toast and no file.
  assert.equal(JSON.parse(fetchCall.opts.body).url, manifestUrl,
    "a Discord manifest must be handed over as itself, not as the page url");
});

test("the menu routes a stream through the matched dir, not the catch-all", () => {
  // It used to be hardcoded to `dir: otherDir()` with `.catch(() => {})`: a
  // yt-dlp capture never reached the matched directory, never toasted, never
  // offered the picker, and every failure was swallowed.
  return (async () => {
    reset();
    const posted = [];
    fetchHandler = async (url, opts) => {
      posted.push({ url, body: JSON.parse(opts.body || "{}") });
      if (url.endsWith("/match")) {
        return { ok: true, status: 200,
          json: async () => ({ dir: "Jane Doe", auto: true,
            reason: "tag=='Jane Doe'" }) };
      }
      return { ok: true, status: 200, json: async () => ({ jobId: "j1" }) };
    };
    await SW.onMenuClicked({
      menuItemId: SW.MENU_ID,
      srcUrl: "https://cdn.example-site.test/master.m3u8",
      pageUrl: "https://example-site.test/v/1",
    }, {});
    assert.equal(calls.downloads.length, 0);
    const matchCall = posted.find((p) => p.url.endsWith("/match"));
    assert.ok(matchCall, "a stream must be matched like any other download");
    const fetchCall = posted.find((p) => p.url.endsWith("/fetch"));
    assert.equal(fetchCall.body.url, "https://example-site.test/v/1");
    assert.equal(fetchCall.body.dir, "Jane Doe");
    assert.match(calls.windowsCreate[0].url, /toast\.html/);
  })();
});

test("a below-threshold stream opens the picker instead of guessing", async () => {
  reset();
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push(url);
    if (url.endsWith("/match")) {
      return { ok: true, status: 200,
        json: async () => ({ dir: "Jane Doe", auto: false, reason: "tie",
          suggestNew: "Aster Vale" }) };
    }
    return { ok: true, status: 200, json: async () => ({ jobId: "j1" }) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    srcUrl: "https://cdn.example-site.test/master.m3u8",
    pageUrl: "https://example-site.test/v/1",
  }, {});
  assert.equal(posted.filter((u) => u.endsWith("/fetch")).length, 0,
    "nothing may be fetched before the user picks a directory");
  assert.match(calls.windowsCreate[0].url, /picker\.html/);
  assert.equal(SW.state.pendingFetch.size, 1);
});

test("choosing a directory for a queued stream submits the job there", async () => {
  reset();
  fetchHandler = async (url) => {
    if (url.endsWith("/match")) {
      return { ok: true, status: 200,
        json: async () => ({ dir: "other", auto: false, reason: "no match" }) };
    }
    return { ok: true, status: 200, json: async () => ({ jobId: "j2" }) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    srcUrl: "https://cdn.example-site.test/master.m3u8",
    pageUrl: "https://example-site.test/v/1",
  }, {});
  const [key] = [...SW.state.pendingFetch.keys()];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ jobId: "j2" }) };
  };
  const out = await SW.applyChoice(key, "john-smith");
  assert.equal(out.dir, "john-smith");
  const fetchCall = posted.find((p) => p.url.endsWith("/fetch"));
  assert.equal(fetchCall.body.dir, "john-smith");
  assert.equal(SW.state.pendingFetch.size, 0);
  assert.equal(calls.search.length, 0,
    "a fetch job has no DownloadItem to search for");
});

test("a failing /fetch is SURFACED, not swallowed", async () => {
  reset();
  fetchHandler = async (url) => {
    if (url.endsWith("/match")) {
      return { ok: true, status: 200,
        json: async () => ({ dir: "Jane Doe", auto: true, reason: "r" }) };
    }
    return { ok: false, status: 400, json: async () => ({}) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    srcUrl: "https://cdn.example-site.test/master.m3u8",
    pageUrl: "https://example-site.test/v/1",
  }, {}).catch(() => {});
  assert.equal(calls.notifications.length, 1,
    "clicking Save must not look identical whether it worked or not");
  assert.match(calls.notifications[0].title, /Could not start/);
});

test("an unreachable sidecar still routes a stream from the cache", async () => {
  reset();
  SW.recordCapture({ pageUrl: "https://example-site.test/v/1",
    tags: ["john-smith"] }, { tab: { id: 7 } });
  const posted = [];
  fetchHandler = async (url, opts) => {
    if (url.endsWith("/match")) throw new Error("ECONNREFUSED");
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ jobId: "j3" }) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    srcUrl: "https://cdn.example-site.test/master.m3u8",
    pageUrl: "https://example-site.test/v/1",
  }, { id: 7 });
  const fetchCall = posted.find((p) => p.url.endsWith("/fetch"));
  assert.equal(fetchCall.body.dir, "john-smith");
});

test("the menu ignores non-http targets", async () => {
  reset();
  const before = calls.downloads.length + calls.fetches.length;
  await SW.onMenuClicked({ menuItemId: SW.MENU_ID,
    linkUrl: "javascript:alert(1)" }, {});
  await SW.onMenuClicked({ menuItemId: SW.MENU_ID,
    linkUrl: "file:///etc/passwd" }, {});
  assert.equal(calls.downloads.length + calls.fetches.length, before);
});

test("the menu does nothing when routing is disabled", async () => {
  reset({ enabled: false });
  await SW.onMenuClicked({ menuItemId: SW.MENU_ID,
    linkUrl: "https://example-site.test/f.mp4" }, {});
  assert.equal(calls.downloads.length, 0);
});

// --- messaging -------------------------------------------------------------- //
test("onMessage routes captures, choices, snapshots and rules", async () => {
  reset();
  assert.equal(SW.onMessage({ type: "dlr:capture", payload: { pageUrl: "p" } },
    { tab: { id: 3 } }, () => {}), false);
  assert.equal(SW.state.captures.length, 1);

  const rules = [];
  // dlr:rules now answers ASYNCHRONOUSLY (it has to await readiness before it
  // can read the snapshot), so it returns true to keep the channel open.
  assert.equal(SW.onMessage({ type: "dlr:rules" }, {}, (r) => rules.push(r)),
    true);
  await settle(0);
  assert.deepEqual(rules[0].siteRules, SNAPSHOT.siteRules);

  assert.equal(SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "x" },
    {}, () => {}), true, "async responses must return true");
  assert.equal(SW.onMessage({ type: "dlr:snapshot" }, {}, () => {}), true);
  assert.equal(SW.onMessage(null, {}, () => {}), false);
  assert.equal(SW.onMessage({ type: "unknown" }, {}, () => {}), false);
});

test("a failing choice answers with an error rather than throwing", async () => {
  reset();
  const responses = [];
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "../escape" }, {},
    (r) => responses.push(r));
  await settle(10);
  assert.equal(responses[0].ok, false);
  assert.match(responses[0].error, /unsafe|refusing/i);
});

test("dlr:repick reopens the picker for a known download", async () => {
  reset();
  SW.state.pending.set(21, { dir: "Jane Doe",
    decision: { reason: "r", suggestNew: "Aster Vale", dup: null } });
  SW.onMessage({ type: "dlr:repick", downloadId: 21 }, {}, () => {});
  await settle(10);
  assert.match(calls.windowsCreate[0].url, /picker\.html/);
});


// --- relocate is refused unless the file is provably inside the library ----- //
//
// relPathFromAbsolute used to take the last two components of whatever
// absolute path Chrome reported. A download saved outside the library root
// therefore produced a plausible "<dir>/<file>" that named a DIFFERENT, real
// file inside the library -- and /relocate would move it. The library root is
// a live qBittorrent seeding target.
test("a download that landed OUTSIDE the library is never relocated", async () => {
  reset();
  SW.state.pending.set(31, { dir: "other", payload: {} });
  searchResult = [{ id: 31, state: "complete",
    filename: "/home/u/Downloads/other/f.mp4" }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push(url);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const out = await SW.applyChoice(31, "Jane Doe");
  assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0,
    "the last two components of an outside path must not become a relPath");
  // ...and it must SAY it did nothing. This branch used to return {ok:true}
  // and learn an alias for a move that never happened -- the same
  // swallow-and-learn pair that was fixed in onDownloadChanged but not here,
  // on the branch the picker actually uses.
  assert.equal(out.ok, false);
  assert.equal(calls.notifications.length, 1);
  assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0,
    "no alias may be learned from a move that did not happen");
});

test("a download loose at the library root is never relocated", async () => {
  reset();
  SW.state.pending.set(32, { dir: "other", payload: {} });
  searchResult = [{ id: 32, state: "complete",
    filename: `${LIB_ROOT}/f.mp4` }];
  const posted = [];
  fetchHandler = async (url) => {
    posted.push(url);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.applyChoice(32, "Jane Doe");
  assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0);
});

test("no snapshot means no library root means no relocate", async () => {
  reset({ snapshot: null });
  SW.state.pending.set(33, { dir: "other", payload: {} });
  searchResult = [{ id: 33, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  const posted = [];
  fetchHandler = async (url) => {
    posted.push(url);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.applyChoice(33, "other").catch(() => {});
  assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0);
});

test("the deferred onChanged relocate carries the download id too", async () => {
  reset();
  SW.state.pending.set(34, { dir: "other", payload: {}, wanted: "Jane Doe" });
  searchResult = [{ id: 34, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  await SW.onDownloadChanged({ id: 34, state: { current: "complete" } });
  const relocate = posted.find((p) => p.url.endsWith("/relocate"));
  assert.deepEqual(relocate.body,
    { fromRelPath: "other/f.mp4", toDir: "Jane Doe", downloadId: 34 });
});

test("the /match payload carries the download id", async () => {
  reset();
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200,
      json: async () => ({ dir: "Jane Doe", auto: true, reason: "r" }) };
  };
  SW.onDeterminingFilename({ id: 77, filename: "f.mp4" }, spy());
  await settle(10);
  const match = posted.find((p) => p.url.endsWith("/match"));
  assert.equal(match.body.downloadId, 77,
    "without it the sidecar cannot prove who created the file");
});

// --- the toast must not steal the active tab -------------------------------- //
//
// chrome.windows.create ACTIVATES the new window's tab, which fired
// chrome.tabs.onActivated and clobbered state.activeTabId with the toast's own
// tab. After the very first toast, tier-3 correlation ("most recent capture
// from the active tab") matched nothing for the rest of the session and
// routing silently degraded to the catch-all -- no error, just worse matching.
test("a real tab activation updates activeTabId", () => {
  reset();
  SW.onTabActivated({ tabId: 42, windowId: 5 });
  assert.equal(SW.state.activeTabId, 42);
});

test("activation inside OUR toast window is ignored", async () => {
  reset();
  SW.state.activeTabId = 7;
  await SW.showToast({ downloadId: 1, dir: "Jane Doe", reason: "r" });
  const ownWindowId = [...SW.state.ownWindowIds][0];
  assert.ok(typeof ownWindowId === "number", "the popup must be remembered");
  SW.onTabActivated({ tabId: 999, windowId: ownWindowId });
  assert.equal(SW.state.activeTabId, 7, "the toast stole the active tab");
});

test("activation inside OUR picker window is ignored", async () => {
  reset();
  SW.state.activeTabId = 7;
  await SW.openPicker({ downloadId: 1, dir: "other", reason: "r" });
  const ownWindowId = [...SW.state.ownWindowIds][0];
  SW.onTabActivated({ tabId: 998, windowId: ownWindowId });
  assert.equal(SW.state.activeTabId, 7);
});

test("tier-3 correlation still works after a toast has been shown", async () => {
  reset();
  SW.state.activeTabId = 7;
  SW.recordCapture({ pageUrl: "https://example-site.test/v/1",
    tags: ["john-smith"] }, { tab: { id: 7 } });
  await SW.showToast({ downloadId: 1, dir: "Jane Doe", reason: "r" });
  SW.onTabActivated({ tabId: 1234, windowId: [...SW.state.ownWindowIds][0] });

  fetchHandler = async () => { throw new Error("down"); };
  const suggest = spy();
  SW.onDeterminingFilename({ id: 60, filename: "f.mp4" }, suggest);
  await settle(10);
  assert.equal(suggest.calls[0].filename, "john-smith/f.mp4",
    "the capture on the real tab must still correlate");
});

test("a closed popup stops being tracked", async () => {
  reset();
  await SW.showToast({ downloadId: 1, dir: "Jane Doe", reason: "r" });
  const id = [...SW.state.ownWindowIds][0];
  SW.onWindowRemoved(id);
  assert.equal(SW.state.ownWindowIds.has(id), false);
  SW.onTabActivated({ tabId: 555, windowId: id });
  assert.equal(SW.state.activeTabId, 555);
});

test("a malformed activation event is ignored", () => {
  reset();
  SW.state.activeTabId = 7;
  for (const e of [null, undefined, {}, { tabId: "nope" }]) SW.onTabActivated(e);
  assert.equal(SW.state.activeTabId, 7);
});


// --- a failed correction must be VISIBLE, and must not be learned from ------ //
//
// The deferred path did `.catch(() => {})` and wrote the alias anyway. That is
// HOW a completely dead correction path stayed invisible for a whole audit
// round: every relocate was being refused, the UI reported success, and the
// alias table filled up with corrections that had never been applied.
test("a refused deferred relocate is surfaced and NOT learned from", async () => {
  reset();
  SW.state.pending.set(80, { dir: "other", payload: { page: {} },
    wanted: "Jane Doe" });
  searchResult = [{ id: 80, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  const posted = [];
  fetchHandler = async (url) => {
    posted.push(url);
    if (url.endsWith("/relocate")) {
      return { ok: false, status: 400, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.onDownloadChanged({ id: 80, state: { current: "complete" } });
  assert.equal(calls.notifications.length, 1, "the failure must be visible");
  assert.match(calls.notifications[0].title, /Could not move/);
  assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0,
    "an alias must not be learned from a move that never happened");
});

test("a SUCCESSFUL deferred relocate does learn", async () => {
  reset();
  SW.state.pending.set(81, { dir: "other", payload: { page: {} },
    wanted: "Jane Doe" });
  searchResult = [{ id: 81, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.onDownloadChanged({ id: 81, state: { current: "complete" } });
  assert.equal(calls.notifications.length, 0);
  const learn = posted.find((p) => p.url.endsWith("/learn"));
  assert.equal(learn.body.chosenDir, "Jane Doe");
});

test("a file that landed outside the library is reported, not silently dropped",
  async () => {
    reset();
    SW.state.pending.set(82, { dir: "other", payload: {}, wanted: "Jane Doe" });
    searchResult = [{ id: 82, state: "complete",
      filename: "/home/u/Downloads/other/f.mp4" }];
    const posted = [];
    fetchHandler = async (url) => {
      posted.push(url);
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    };
    await SW.onDownloadChanged({ id: 82, state: { current: "complete" } });
    assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0);
    assert.equal(calls.notifications.length, 1);
    assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0);
  });

test("a refused IMMEDIATE relocate rejects rather than reporting success",
  async () => {
    reset();
    SW.state.pending.set(83, { dir: "other", payload: { page: {} } });
    searchResult = [{ id: 83, state: "complete",
      filename: `${LIB_ROOT}/other/f.mp4` }];
    const posted = [];
    fetchHandler = async (url) => {
      posted.push(url);
      if (url.endsWith("/relocate")) {
        return { ok: false, status: 400, json: async () => ({}) };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    };
    await assert.rejects(() => SW.applyChoice(83, "Jane Doe"), /sidecar 400/);
    assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0);
  });

test("applyChoice on an in-flight download reports that it deferred", async () => {
  reset();
  SW.state.pending.set(84, { dir: "other", payload: {} });
  searchResult = [{ id: 84, state: "in_progress" }];
  const posted = [];
  fetchHandler = async (url) => {
    posted.push(url);
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const out = await SW.applyChoice(84, "Jane Doe");
  assert.equal(out.deferred, true);
  assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0,
    "the learn is deferred too -- neither happens unless the move does");
});


test("a deferred pick EQUAL to the auto dir still learns", async () => {
  // Moving /learn behind `moved` silently dropped this: applyChoice returns
  // {deferred:true} without learning and onDownloadChanged only learned when
  // wanted !== dir. It is the user CONFIRMING the router's own answer, which
  // is a real positive signal the matcher was getting before.
  reset();
  SW.state.pending.set(90, { dir: "Jane Doe", payload: { page: {} } });
  searchResult = [{ id: 90, state: "in_progress" }];
  fetchHandler = async () => ({ ok: true, status: 200, json: async () => ({}) });
  const out = await SW.applyChoice(90, "Jane Doe");
  assert.equal(out.deferred, true);

  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({}) };
  };
  searchResult = [{ id: 90, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  await SW.onDownloadChanged({ id: 90, state: { current: "complete" } });
  assert.equal(posted.filter((p) => p.url.endsWith("/relocate")).length, 0,
    "nothing to move");
  const learn = posted.find((p) => p.url.endsWith("/learn"));
  assert.ok(learn, "the confirmation must still be learned");
  assert.equal(learn.body.chosenDir, "Jane Doe");
});


// --- the sidecar's own words must reach the user ---------------------------- //
//
// api() threw `new Error(`sidecar ${status}`)` and discarded the JSON body
// unread, while the sidecar puts its explanation in {"detail": ...} with a 400.
// Two whole findings were about making that refusal honest and making it
// visible; the honest text was reachable only via `dl-route log` or curl.
const REFUSAL = "no routing decision is on record for this download. Either "
  + "the sidecar was unreachable when the download started";

test("a 400 detail is carried into the thrown error", async () => {
  reset();
  fetchHandler = async () => ({
    ok: false, status: 400,
    json: async () => ({ ok: false, error: "unsafe", detail: REFUSAL }),
  });
  await assert.rejects(() => SW.api("POST", "/relocate", {}), (err) => {
    assert.equal(err.status, 400);
    assert.equal(err.detail, REFUSAL);
    assert.match(String(err.message), /no routing decision is on record/);
    return true;
  });
});

test("errorMessage prefers the sidecar's words over the status line", () => {
  assert.equal(SW.errorMessage({ detail: REFUSAL, message: "sidecar 400" }),
    REFUSAL);
  assert.equal(SW.errorMessage(new Error("boom")), "boom");
  assert.equal(SW.errorMessage("plain"), "plain");
});

test("a body that is not JSON still yields a usable error", async () => {
  reset();
  fetchHandler = async () => ({
    ok: false, status: 502,
    json: async () => { throw new Error("not json"); },
  });
  await assert.rejects(() => SW.api("GET", "/dirs"), /sidecar 502/);
});

test("the picker is told WHY, not just that it failed", async () => {
  reset();
  SW.state.pending.set(95, { dir: "other", payload: { page: {} } });
  searchResult = [{ id: 95, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  fetchHandler = async (url) => {
    if (url.endsWith("/relocate")) {
      return { ok: false, status: 400,
        json: async () => ({ detail: REFUSAL }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const responses = [];
  SW.onMessage({ type: "dlr:choose", downloadId: 95, dir: "Jane Doe" }, {},
    (r) => responses.push(r));
  await settle(10);
  assert.equal(responses[0].ok, false);
  assert.match(responses[0].error, /no routing decision is on record/,
    "the picker rendered `Error: sidecar 400` before this");
  assert.ok(!responses[0].error.startsWith("Error:"));
});

test("the desktop notification carries the reason too", async () => {
  reset();
  SW.state.pending.set(96, { dir: "other", payload: { page: {} },
    wanted: "Jane Doe" });
  searchResult = [{ id: 96, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  fetchHandler = async (url) => {
    if (url.endsWith("/relocate")) {
      return { ok: false, status: 400,
        json: async () => ({ detail: REFUSAL }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.onDownloadChanged({ id: 96, state: { current: "complete" } });
  assert.equal(calls.notifications.length, 1);
  assert.match(calls.notifications[0].message, /no routing decision is on record/);
});

// --- the deferred-equal learn must VERIFY, not assert ----------------------- //
test("a deferred-equal pick does not learn when the file left the library",
  async () => {
    // "there is nothing to move" was asserted, not checked: with a Save-As to
    // a non-library folder this posted /learn with a subject directory the
    // file never went near.
    reset();
    SW.state.pending.set(97, { dir: "Jane Doe", payload: { page: {} },
      wanted: "Jane Doe" });
    searchResult = [{ id: 97, state: "complete",
      filename: "/home/u/Downloads/f.mp4" }];
    const posted = [];
    fetchHandler = async (url) => {
      posted.push(url);
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    };
    await SW.onDownloadChanged({ id: 97, state: { current: "complete" } });
    assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0);
    assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0);
  });

// NOTE: the previous round asserted here that a deferred-equal pick with the
// file in ANOTHER library directory must not learn. That rule is SUPERSEDED:
// keying on the router's intent rather than the file's location meant nothing
// happened at all in that case -- no move, no report -- while the picker had
// already reported success. It is now moved and then learned; see
// "deferred pick == router answer, file in the WRONG dir -> it is moved".


// --- the deferred branch keys on WHERE THE FILE IS, not on router intent ---- //
//
// It used to branch on `entry.wanted !== entry.dir` first, which asks the
// wrong question. When the pick EQUALLED the router's own answer but the file
// was somewhere else, nothing happened at all: no move, no report -- while the
// picker had already returned {ok:true, deferred:true} and closed, so the user
// believed their pick had been applied. The sibling branch, given the
// identical physical situation, reported it.
function deferred(id, { dir, wanted, filename }) {
  reset();
  SW.state.pending.set(id, { dir, wanted, payload: { page: {} } });
  searchResult = [{ id, state: "complete", filename }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  return posted;
}

test("deferred pick == router answer, file in the WRONG dir -> it is moved",
  async () => {
    const posted = deferred(100, { dir: "Jane Doe", wanted: "Jane Doe",
      filename: `${LIB_ROOT}/john-smith/f.mp4` });
    await SW.onDownloadChanged({ id: 100, state: { current: "complete" } });
    const relocate = posted.find((p) => p.url.endsWith("/relocate"));
    assert.ok(relocate, "the file is in the library, just in the wrong place");
    assert.equal(relocate.body.toDir, "Jane Doe");
    assert.equal(relocate.body.fromRelPath, "john-smith/f.mp4");
    assert.ok(posted.some((p) => p.url.endsWith("/learn")));
  });

test("deferred pick == router answer, file OUTSIDE the root -> reported",
  async () => {
    const posted = deferred(101, { dir: "Jane Doe", wanted: "Jane Doe",
      filename: "/home/u/Downloads/f.mp4" });
    await SW.onDownloadChanged({ id: 101, state: { current: "complete" } });
    assert.equal(posted.filter((p) => p.url.endsWith("/relocate")).length, 0);
    assert.equal(posted.filter((p) => p.url.endsWith("/learn")).length, 0);
    assert.equal(calls.notifications.length, 1,
      "silently doing nothing is how the user came to believe it was applied");
    assert.match(calls.notifications[0].message, /did not land inside/);
  });

test("deferred pick == router answer, file where it says -> learn only",
  async () => {
    const posted = deferred(102, { dir: "Jane Doe", wanted: "Jane Doe",
      filename: `${LIB_ROOT}/Jane Doe/f.mp4` });
    await SW.onDownloadChanged({ id: 102, state: { current: "complete" } });
    assert.equal(posted.filter((p) => p.url.endsWith("/relocate")).length, 0);
    assert.ok(posted.some((p) => p.url.endsWith("/learn")));
    assert.equal(calls.notifications.length, 0);
  });

test("deferred pick != router answer still behaves as before", async () => {
  const posted = deferred(103, { dir: "other", wanted: "Jane Doe",
    filename: `${LIB_ROOT}/other/f.mp4` });
  await SW.onDownloadChanged({ id: 103, state: { current: "complete" } });
  const relocate = posted.find((p) => p.url.endsWith("/relocate"));
  assert.equal(relocate.body.toDir, "Jane Doe");
  assert.ok(posted.some((p) => p.url.endsWith("/learn")));
});

test("deferred pick != router answer, already in the right place -> no move",
  async () => {
    const posted = deferred(104, { dir: "other", wanted: "Jane Doe",
      filename: `${LIB_ROOT}/Jane Doe/f.mp4` });
    await SW.onDownloadChanged({ id: 104, state: { current: "complete" } });
    assert.equal(posted.filter((p) => p.url.endsWith("/relocate")).length, 0);
    assert.ok(posted.some((p) => p.url.endsWith("/learn")));
  });

test("a refused deferred move is still reported and not learned from",
  async () => {
    reset();
    SW.state.pending.set(105, { dir: "Jane Doe", wanted: "Jane Doe",
      payload: { page: {} } });
    searchResult = [{ id: 105, state: "complete",
      filename: `${LIB_ROOT}/john-smith/f.mp4` }];
    const posted = [];
    fetchHandler = async (url) => {
      posted.push(url);
      if (url.endsWith("/relocate")) {
        return { ok: false, status: 400,
          json: async () => ({ detail: "cannot prove it created this file" }) };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    };
    await SW.onDownloadChanged({ id: 105, state: { current: "complete" } });
    assert.equal(posted.filter((u) => u.endsWith("/learn")).length, 0);
    assert.equal(calls.notifications.length, 1);
    assert.match(calls.notifications[0].message, /cannot prove/);
  });

test("a non-string detail does not become [object Object]", async () => {
  reset();
  fetchHandler = async () => ({
    ok: false, status: 400,
    json: async () => ({ detail: { nested: "shape" } }),
  });
  await assert.rejects(() => SW.api("POST", "/relocate", {}), (err) => {
    assert.ok(!String(err.message).includes("[object Object]"), err.message);
    assert.match(String(err.message), /sidecar 400/);
    return true;
  });
});

// --- identity signals, kinds and the referrer carry, through the real glue --- //
test("recordCapture remembers the opener tab", () => {
  // The ONLY provable link between a forum thread and the file host it sent
  // the user to. The content script cannot know it; the sender does.
  reset();
  SW.recordCapture({ pageUrl: "https://filehost.test/f/AbCdEf" },
    { tab: { id: 12, openerTabId: 4 } });
  const c = SW.state.captures.at(-1);
  assert.equal(c.tabId, 12);
  assert.equal(c.openerTabId, 4);
});

test("a Discord download posts its URL, so the sidecar can read the channel", async () => {
  // Six of nine real downloads had NO page context at all -- correlation finds
  // nothing, and the URL is the entire signal.
  reset();
  const url = "https://cdn.discordapp.com/attachments/119283746551234567"
    + "/998877665544332211/clip.mp4";
  let posted = null;
  fetchHandler = async (u, opts) => {
    if (u.endsWith("/match")) posted = JSON.parse(opts.body);
    return { ok: true, status: 200,
      json: async () => ({ dir: "other", auto: false, confidence: 0 }) };
  };
  SW.onDeterminingFilename({ id: 40, url, filename: "clip.mp4" }, spy());
  await settle(5);
  assert.equal(posted.url, url);
});

test("a proven referrer travels to the sidecar; an unprovable one does not", async () => {
  reset();
  const thread = "https://someforum.test/threads/aster-vale.481920/";
  const hostPage = "https://filehost.test/f/AbCdEf";
  SW.recordCapture({ pageUrl: thread, pageTitle: "Aster Vale | Some Forum",
    href: hostPage, tags: [] }, { tab: { id: 4 } });
  SW.recordCapture({ pageUrl: hostPage, pageTitle: "Download", tags: [] },
    { tab: { id: 5, openerTabId: 4 } });

  let posted = null;
  const capturePost = () => {
    fetchHandler = async (u, opts) => {
      if (u.endsWith("/match")) posted = JSON.parse(opts.body);
      return { ok: true, status: 200,
        json: async () => ({ dir: "other", auto: false, confidence: 0 }) };
    };
  };
  capturePost();
  SW.onDeterminingFilename(
    { id: 41, url: "https://filehost.test/d/AbCdEf", referrer: hostPage,
      filename: "f.mp4" }, spy());
  await settle(5);
  assert.equal(posted.page.referrerUrl, thread);

  // Now the same download with the chain broken: no href, no opener.
  reset();                       // ...which also restores the default handler
  SW.recordCapture({ pageUrl: thread, pageTitle: "Aster Vale", tags: [] },
    { tab: { id: 4 } });
  SW.recordCapture({ pageUrl: hostPage, tags: [] }, { tab: { id: 5 } });
  posted = null;
  capturePost();
  SW.onDeterminingFilename(
    { id: 42, url: "https://filehost.test/d/AbCdEf", referrer: hostPage,
      filename: "f.mp4" }, spy());
  await settle(5);
  assert.equal(posted.page.referrerUrl, "",
    "an unprovable thread is never carried");
});

test("creating a directory sends the kind the picker asked for", async () => {
  reset();
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
    if (url.endsWith("/dirs")) {
      return { ok: true, status: 200, json: async () => SNAPSHOT };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.applyChoice(50, "Aster Vale",
    { createdNew: true, kind: "performer" });
  const mkdir = posted.find((p) => p.url.endsWith("/mkdir"));
  assert.deepEqual(mkdir.body, { name: "Aster Vale", kind: "performer" });
});

test("a correction is marked as an explicit confirmation", async () => {
  // The sidecar refuses to learn a tag -> directory alias without it, and the
  // flag is stated rather than inferred so a future automatic caller fails
  // closed.
  reset();
  SW.state.pending.set(60, { dir: "other", filename: "f.mp4",
    payload: { page: {} }, ts: Date.now() });
  searchResult = [{ id: 60, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.applyChoice(60, "Jane Doe", {});
  const learn = posted.find((p) => p.url.endsWith("/learn"));
  assert.equal(learn.body.confirmed, true);
});

// --- reportNothingLearned: the notification that makes the screen visible --- //
// It had no test at all, and it is the only consumer of `skipped` -- so the
// mechanism the screen's visibility depends on was itself unpinned.
test("filing to the catch-all does NOT notify", () => {
  // /learn returns a `skipped` entry for the catch-all BY DESIGN ("the absence
  // of a subject, not one"), and filing there is routine. Notifying would
  // train the operator to dismiss the exact notification this exists for.
  reset();
  const fired = SW.reportNothingLearned({
    dir: "other", written: [],
    skipped: [{ key: "", source: "catch-all", why: "the catch-all is..." }],
  });
  assert.equal(fired, false);
  assert.equal(calls.notifications.length, 0);
});

test("a screened IDENTITY notifies even when something else was written", () => {
  // A screened identity writes no row, so the re-point bypass never engages
  // for it either: that thread will never auto-file, forever. Reporting
  // success because an unrelated tag landed hides it permanently.
  reset();
  const fired = SW.reportNothingLearned({
    dir: "Jane Doe",
    written: [{ key: "fieldrecordings", site: "s.test", source: "tag" }],
    skipped: [{ key: "thread:x", source: "thread-slug", why: "seen on 2..." }],
  });
  assert.equal(fired, true);
  assert.match(calls.notifications[0].title, /will not learn this source/);
  assert.match(calls.notifications[0].message, /seen on 2/);
});

test("a screened tag with something else written stays quiet", () => {
  reset();
  assert.equal(SW.reportNothingLearned({
    dir: "acme-studio",
    written: [{ key: "fieldrecordings", site: "s.test", source: "tag" }],
    skipped: [{ key: "jd", source: "tag", why: "too short" }],
  }), false);
  assert.equal(calls.notifications.length, 0);
});

test("a correction that learned nothing at all notifies", () => {
  reset();
  assert.equal(SW.reportNothingLearned({
    dir: "acme-studio", written: [],
    skipped: [{ key: "jd", source: "tag", why: "too short" }],
  }), true);
  assert.match(calls.notifications[0].title, /learned nothing/);
});

test("nothing skipped, nothing said", () => {
  reset();
  assert.equal(SW.reportNothingLearned({ dir: "Jane Doe",
    written: [{ key: "k", site: "s", source: "thread-slug" }], skipped: [] }),
  false);
  assert.equal(SW.reportNothingLearned(null), false);
  assert.equal(calls.notifications.length, 0);
});

test("the identity refusal is the one reported, not merely the first", () => {
  reset();
  SW.reportNothingLearned({
    dir: "Jane Doe", written: [],
    skipped: [
      { key: "jd", source: "tag", why: "FIRST but unimportant" },
      { key: "thread:x", source: "thread-slug", why: "THE consequential one" },
    ],
  });
  assert.match(calls.notifications[0].message, /consequential/);
});

test("a PERMANENTLY refused identity notifies once, not once per download", () => {
  // The screen refuses a permanent reason (site branding, shared vocabulary, a
  // spread that only ever grows) on EVERY correction, and a screened identity
  // writes no row so nothing else changes either. Without the sidecar's
  // `first` flag this fired forever -- the same "train them to dismiss it"
  // failure as the catch-all bug, narrower in trigger but unbounded.
  reset();
  const learned = {
    dir: "acme-studio",
    written: [{ key: "fieldrecordings", site: "s.test", source: "tag" }],
    skipped: [{ key: "thread:x", source: "thread-slug",
      why: "it is part of the site name", first: true }],
  };
  assert.equal(SW.reportNothingLearned(learned), true);
  assert.equal(calls.notifications.length, 1);

  // ...every subsequent correction for the same key says `first: false`.
  const again = { ...learned,
    skipped: [{ ...learned.skipped[0], first: false }] };
  assert.equal(SW.reportNothingLearned(again), false);
  assert.equal(SW.reportNothingLearned(again), false);
  assert.equal(calls.notifications.length, 1, "one fact, one notification");
});

test("a refusal with no `first` field at all still notifies", () => {
  // Forward compatibility with an older sidecar: absent is not `false`.
  reset();
  assert.equal(SW.reportNothingLearned({
    dir: "Jane Doe", written: [],
    skipped: [{ key: "jd", source: "tag", why: "too short" }],
  }), true);
});

// --- the picker as an in-page overlay, with the window as the fallback ------- //
//
// The overlay and the window run the SAME picker page and therefore the same
// reducer -- there is no second implementation to keep in step. What is tested
// here is only the delivery decision and, above all, that EVERY failure mode
// reaches the window rather than leaving a download with no picker.

const overlayOpen = () =>
  calls.tabMessages.find((m) => m.msg.type === "dlr:overlay-open");
const overlayClose = () =>
  calls.tabMessages.find((m) => m.msg.type === "dlr:close-overlay");

test("a page that can host it gets the picker in-page, with no popup window",
  async () => {
    reset();
    contentScriptMissing = false;
    SW.state.activeTabId = 42;
    assert.equal(await SW.openPicker({ downloadId: 1, dir: "other",
      reason: "no match", suggestNew: "Aster Vale" }), true);
    assert.equal(calls.windowsCreate.length, 0, "no popup window");
    const open = overlayOpen();
    assert.equal(open.id, 42);
    // Top frame only: `all_frames` is for the capture script.
    assert.deepEqual(open.opts, { frameId: 0 });
  });

test("the overlay's URL is the picker page, marked embedded, with a nonce",
  async () => {
    reset();
    contentScriptMissing = false;
    SW.state.activeTabId = 42;
    await SW.openPicker({ downloadId: 7, dir: "other", reason: "r",
      dup: "d", suggestNew: "Aster Vale" });
    const url = new URL(overlayOpen().msg.url);
    assert.match(url.pathname, /picker\.html$/);
    assert.equal(url.searchParams.get("id"), "7");
    assert.equal(url.searchParams.get("suggestNew"), "Aster Vale");
    assert.equal(url.searchParams.get("embed"), "1");
    const nonce = url.searchParams.get("overlay");
    assert.ok(nonce && nonce.length >= 8, "a per-open id the page cannot guess");
    assert.equal(overlayOpen().msg.id, nonce);
  });

test("the popup window carries no embed marker", async () => {
  // It CAN close its own window, and must not ask the worker to tear down an
  // overlay that does not exist.
  reset();
  await SW.openPicker({ downloadId: 7, dir: "other", reason: "r" });
  const url = new URL(calls.windowsCreate[0].url);
  assert.equal(url.searchParams.get("embed"), null);
  assert.equal(url.searchParams.get("overlay"), null);
});

test("the overlay goes to the tab the download came from, not the active tab",
  async () => {
    reset();
    contentScriptMissing = false;
    SW.state.activeTabId = 42;
    SW.state.pending.set(5, { dir: "other", tabId: 11 });
    await SW.openPicker({ downloadId: 5, dir: "other", reason: "r" });
    assert.equal(overlayOpen().id, 11);
  });

// --- every fallback route --------------------------------------------------- //
test("a tab that has already closed falls back to a window", async () => {
  // The self-closing file-host tab: a real case in this workflow.
  reset();
  contentScriptMissing = false;
  tabsGetFails = true;
  SW.state.activeTabId = 42;
  assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
  assert.equal(calls.windowsCreate.length, 1);
  assert.equal(overlayOpen(), undefined, "not even probed");
});

test("a discarded tab falls back to a window", async () => {
  reset();
  contentScriptMissing = false;
  tabDiscarded = true;
  SW.state.activeTabId = 42;
  await SW.openPicker({ downloadId: 1, dir: "other" });
  assert.equal(calls.windowsCreate.length, 1);
});

test("with no tab to inject into at all, a window", async () => {
  reset();
  contentScriptMissing = false;
  SW.state.activeTabId = undefined;
  SW.state.pending.clear();
  await SW.openPicker({ downloadId: 1, dir: "other" });
  assert.equal(calls.windowsCreate.length, 1);
  assert.equal(calls.tabsGet.length, 0);
});

test("no content script in the tab falls back to a window", async () => {
  // ONE check covering brave://, the PDF viewer (an https URL whose document is
  // a plugin), view-source:, file://, and a page that has not reached
  // document_idle: chrome.tabs.sendMessage rejects with "receiving end does not
  // exist". That is why the URL test below is only a shortcut, never the proof.
  reset();
  contentScriptMissing = true;
  SW.state.activeTabId = 42;
  assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
  assert.ok(overlayOpen(), "it was probed");
  assert.equal(calls.windowsCreate.length, 1, "and fell back");
});

test("a content script that refuses to build falls back to a window", async () => {
  reset();
  contentScriptMissing = false;
  overlayOpenResult = { ok: false, error: "create_failed" };
  SW.state.activeTabId = 42;
  await SW.openPicker({ downloadId: 1, dir: "other" });
  assert.equal(calls.windowsCreate.length, 1);
});

test("GATE 2: a frame that never reports ready falls back, husk torn down first",
  async () => {
    // A content-script-injected iframe is subject to the PAGE's CSP, so a site
    // with a strict `frame-src` blocks the load while every DOM call in the
    // content script still succeeds. Without this gate the user would be left
    // looking at an empty overlay and no picker at all.
    reset();
    contentScriptMissing = false;
    overlayReports = "silent";
    SW.state.activeTabId = 42;
    assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
    assert.ok(overlayClose(), "the empty overlay is removed");
    assert.equal(calls.windowsCreate.length, 1, "and a window opens");
    assert.equal(SW.state.overlays.size, 0);
  });

test("NOTHING leaves a download without a picker", async () => {
  // The last rung: no overlay, no window -- a notification.
  reset();
  contentScriptMissing = true;
  windowsCreateFails = true;
  SW.state.activeTabId = 42;
  await SW.openPicker({ downloadId: 1, dir: "other", reason: "no match" });
  assert.equal(calls.notifications.length, 1);
});

test("an overlay that throws unexpectedly still reaches the window", async () => {
  // openPicker's contract: openOverlayPicker may fail in any way at all.
  reset();
  const realGet = chrome.tabs.get;
  chrome.tabs.get = async () => { throw { nope: true }; };  // not an Error
  SW.state.activeTabId = 42;
  try {
    assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
    assert.equal(calls.windowsCreate.length, 1);
  } finally {
    chrome.tabs.get = realGet;
  }
});

// --- closing ---------------------------------------------------------------- //
test("dlr:picker-closed tears the overlay down in the tab it was opened in",
  async () => {
    reset();
    contentScriptMissing = false;
    SW.state.activeTabId = 42;
    await SW.openPicker({ downloadId: 1, dir: "other" });
    const nonce = overlayOpen().msg.id;
    assert.equal(SW.state.overlays.size, 1);
    SW.onMessage({ type: "dlr:picker-closed", overlay: nonce }, {}, () => {});
    await settle();
    const close = overlayClose();
    assert.equal(close.id, 42);
    assert.equal(close.msg.overlay, nonce);
    assert.equal(SW.state.overlays.size, 0);
  });

test("dlr:picker-closed survives an MV3 teardown via sender.tab.id", async () => {
  // The picker can sit open well past the ~30 s idle timeout. A restarted
  // worker has an empty `state.overlays`, so keying only on that map would
  // leave the overlay on screen forever with nothing able to remove it.
  reset();
  SW.state.overlays.clear();
  SW.onMessage({ type: "dlr:picker-closed", overlay: "ov-gone" },
    { tab: { id: 77 } }, () => {});
  await settle();
  const close = overlayClose();
  assert.equal(close.id, 77);
  assert.equal(close.msg.overlay, "ov-gone");
});

test("dlr:picker-closed with nothing to go on is a silent no-op", async () => {
  reset();
  SW.state.overlays.clear();
  assert.equal(
    SW.onMessage({ type: "dlr:picker-closed", overlay: "ov-gone" }, {},
      () => {}), false);
  await settle();
  assert.equal(overlayClose(), undefined);
});

test("dlr:picker-ready answers synchronously and holds no channel open", () => {
  // It must not await ready(): the whole point is to answer inside the
  // readiness budget the open path is waiting on.
  reset();
  assert.equal(
    SW.onMessage({ type: "dlr:picker-ready", overlay: "ov-x" }, {}, () => {}),
    false);
});

// --- the URL shortcut ------------------------------------------------------- //
test("overlayCapableUrl rejects what a content script provably never sees", () => {
  for (const url of ["https://example-site.test/v/1",
    "http://example-site.test/v/1"]) {
    assert.equal(SW.overlayCapableUrl(url), true, url);
  }
  for (const url of ["brave://newtab/", "chrome://extensions/",
    "chrome-extension://abc/picker.html", "view-source:https://x.test/",
    "file:///home/u/a.pdf", "about:blank", "",
    "https://chromewebstore.google.com/detail/x",
    "https://chrome.google.com/webstore/detail/x", null, undefined, 7]) {
    assert.equal(SW.overlayCapableUrl(url), false, String(url));
  }
});

test("a brave:// tab is not even probed", async () => {
  reset();
  contentScriptMissing = false;
  tabUrl = "brave://settings/downloads";
  SW.state.activeTabId = 42;
  await SW.openPicker({ downloadId: 1, dir: "other" });
  assert.equal(overlayOpen(), undefined);
  assert.equal(calls.windowsCreate.length, 1);
});

// --- the snapshot freshness flag -------------------------------------------- //
test("THE PICKER'S SNAPSHOT REQUEST DOES NOT REVALIDATE", async () => {
  // The pin on the other half of the ETag decision. The sidecar keeps the
  // per-directory counts OUT of the etag, so a 304 would hand the picker a
  // stale tally -- frozen until the routing configuration next changed. This
  // one request skips If-None-Match; add it back and this fails.
  reset();
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => SNAPSHOT });
  SW.onMessage({ type: "dlr:snapshot" }, {}, () => {});
  await settle();
  const dirsFetch = calls.fetches.find((f) => f.url.endsWith("/dirs"));
  assert.equal(dirsFetch.opts.headers["If-None-Match"], undefined);
});

test("but everything else still revalidates", async () => {
  // The counterweight: `revalidate: false` must not have leaked into the
  // five-minute alarm, startup, or the post-/mkdir refresh, or the steady-state
  // traffic would become a full refetch every five minutes.
  reset();
  fetchHandler = async () => ({ ok: true, status: 304, json: async () => ({}) });
  await SW.refreshSnapshot();
  assert.equal(calls.fetches[0].opts.headers["If-None-Match"], '"abc123"');
});

test("the counts ride through to the picker", async () => {
  reset();
  const withCounts = { ...SNAPSHOT, counts: { "Jane Doe": 12 } };
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => withCounts });
  let answer = null;
  SW.onMessage({ type: "dlr:snapshot" }, {}, (r) => { answer = r; });
  await settle();
  assert.deepEqual(answer.snapshot.counts, { "Jane Doe": 12 });
});

test("a `ready` that beats the probe's own answer is not lost", async () => {
  // The readiness wait is armed BEFORE the open message goes out, precisely so
  // this ordering works. Arming it afterwards drops the signal and falls back
  // to a popup window for no reason -- intermittently, which is the worst kind.
  reset();
  contentScriptMissing = false;
  overlayReports = "ready";      // announces in a microtask, before the send
  SW.state.activeTabId = 42;                                   // has resolved
  assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
  assert.equal(calls.windowsCreate.length, 0);
});

test("...and so is one that arrives after it", async () => {
  reset();
  contentScriptMissing = false;
  overlayReports = "ready-late";
  SW.state.activeTabId = 42;
  assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
  assert.equal(calls.windowsCreate.length, 0);
});

// --- AN OVERLAY THAT STOPS EXISTING BECOMES A WINDOW ------------------------ //
//
// PINS ON THE PERMISSIVE PATH. Gate 2 proves the frame booted; it proves
// nothing a millisecond later, and `openPicker` has already returned true. The
// overlay lives in a document the extension does not own, so every way it can
// vanish needs a route back to a window -- or the download is left unasked,
// which is the one outcome that is not allowed.

async function liveOverlay({ downloadId = 1, tabId = 42 } = {}) {
  reset();
  contentScriptMissing = false;
  SW.state.activeTabId = tabId;
  await SW.openPicker({ downloadId, dir: "other", reason: "no match",
    suggestNew: "Aster Vale" });
  assert.equal(calls.windowsCreate.length, 0, "precondition: overlay is up");
  return overlayOpen().msg.id;
}

test("the tab holding the overlay CLOSES -> the picker comes back as a window",
  async () => {
    // The self-closing file-host tab, arriving one beat later than the version
    // chrome.tabs.get catches.
    const id = await liveOverlay();
    SW.onTabRemoved(42);
    await settle();
    assert.equal(calls.windowsCreate.length, 1, "the question is re-asked");
    assert.equal(SW.state.overlays.size, 0);
    // ...as the SAME question: same download, same proposal.
    const url = new URL(calls.windowsCreate[0].url);
    assert.equal(url.searchParams.get("id"), "1");
    assert.equal(url.searchParams.get("suggestNew"), "Aster Vale");
    assert.equal(url.searchParams.get("embed"), null, "and as a real window");
    assert.equal(SW.state.overlays.has(id), false);
  });

test("the tab NAVIGATES -> the picker comes back as a window", async () => {
  await liveOverlay();
  SW.onTabUpdated(42, { status: "loading" });
  await settle();
  assert.equal(calls.windowsCreate.length, 1);
  assert.equal(SW.state.overlays.size, 0);
});

test("a SPA route change does NOT yank the picker into a window", async () => {
  // pushState fires onUpdated with a url but no `status`, and the document is
  // not replaced -- the overlay is still there and still usable. Re-delivering
  // on every route change would be a regression, not a rescue.
  await liveOverlay();
  SW.onTabUpdated(42, { url: "https://example-site.test/v/2" });
  SW.onTabUpdated(42, { status: "complete" });
  SW.onTabUpdated(42, { title: "something" });
  await settle();
  assert.equal(calls.windowsCreate.length, 0);
  assert.equal(SW.state.overlays.size, 1);
});

test("the PAGE removes the overlay -> the picker comes back as a window",
  async () => {
    // `document.body.innerHTML = ...`, a DOM sanitiser, a framework re-render,
    // or hostile script. The content script reports it; this is the other half.
    const id = await liveOverlay();
    SW.onMessage({ type: "dlr:overlay-lost", overlay: id }, {}, () => {});
    await settle();
    assert.equal(calls.windowsCreate.length, 1);
    assert.equal(SW.state.overlays.size, 0);
  });

test("events for OTHER tabs leave the overlay alone", async () => {
  await liveOverlay({ tabId: 42 });
  SW.onTabRemoved(99);
  SW.onTabUpdated(99, { status: "loading" });
  await settle();
  assert.equal(calls.windowsCreate.length, 0);
  assert.equal(SW.state.overlays.size, 1);
});

test("re-delivery is idempotent: two triggers, ONE window", async () => {
  // onRemoved and onUpdated can both fire for a closing tab, and the page can
  // report the loss at the same moment. Two pickers for one download would be
  // worse than none.
  const id = await liveOverlay();
  SW.onTabUpdated(42, { status: "loading" });
  SW.onTabRemoved(42);
  SW.onMessage({ type: "dlr:overlay-lost", overlay: id }, {}, () => {});
  await settle();
  assert.equal(calls.windowsCreate.length, 1);
});

test("a loss reported for an unknown overlay does nothing", async () => {
  reset();
  SW.onMessage({ type: "dlr:overlay-lost", overlay: "ov-nope" }, {}, () => {});
  await settle();
  assert.equal(calls.windowsCreate.length, 0);
});

test("a SECOND download into the same tab gets a window, not an eviction",
  async () => {
    // The content script keeps exactly ONE overlay and evicts the incumbent, so
    // overlaying the second download would silently destroy the first one's
    // picker while the worker still believed it was delivered. Both questions
    // must be asked.
    await liveOverlay({ downloadId: 1, tabId: 42 });
    calls.windowsCreate.length = 0;
    assert.equal(await SW.openPicker({ downloadId: 2, dir: "other" }), true);
    assert.equal(calls.windowsCreate.length, 1, "the second gets a window");
    assert.equal(SW.state.overlays.size, 1, "the first keeps its overlay");
    const url = new URL(calls.windowsCreate[0].url);
    assert.equal(url.searchParams.get("id"), "2");
  });

test("once the first overlay closes, the tab can host another", async () => {
  const id = await liveOverlay({ downloadId: 1, tabId: 42 });
  SW.onMessage({ type: "dlr:picker-closed", overlay: id }, {}, () => {});
  await settle();
  calls.windowsCreate.length = 0;
  assert.equal(await SW.openPicker({ downloadId: 2, dir: "other" }), true);
  assert.equal(calls.windowsCreate.length, 0, "overlaid again");
});

test("the overlay's tab is raised, or the question is asked where nobody looks",
  async () => {
    // The popup window this replaces was created `focused: true`. The toast's
    // `change` makes it concrete: the user is looking at the toast's own
    // window, and the overlay goes to the download's tab.
    reset();
    contentScriptMissing = false;
    SW.state.activeTabId = 42;
    await SW.openPicker({ downloadId: 1, dir: "other" });
    assert.deepEqual(calls.tabsUpdate, [{ id: 42, info: { active: true } }]);
    assert.deepEqual(calls.windowsUpdate, [{ id: 5, info: { focused: true } }]);
  });

test("a tab that refuses to be raised still keeps its working overlay",
  async () => {
    reset();
    contentScriptMissing = false;
    tabsUpdateFails = true;
    SW.state.activeTabId = 42;
    assert.equal(await SW.openPicker({ downloadId: 1, dir: "other" }), true);
    assert.equal(calls.windowsCreate.length, 0, "no pointless second picker");
  });

// --- a hostile page may frame picker.html ----------------------------------- //
test("A PICK FROM AN UNRECOGNISED SUBFRAME IS REFUSED", async () => {
  // `picker.html` is web-accessible -- it has to be, to be framed at all -- so
  // any page can embed it, point it at a recent download id, and clickjack two
  // clicks: one to take the "+ new dir" row, one to answer the kind prompt.
  // That is a /mkdir and a /relocate driven by a page. Click-to-select is what
  // made two blind clicks enough.
  reset();
  let answer = null;
  SW.onMessage(
    { type: "dlr:choose", downloadId: 1, dir: "Evil", createdNew: true,
      kind: "performer" },
    { frameId: 3, tab: { id: 42 } }, (r) => { answer = r; });
  await settle(20);
  assert.equal(answer.ok, false);
  assert.match(answer.error, /unrecognised frame/);
  assert.equal(calls.fetches.length, 0, "no /mkdir, no /relocate, no /learn");
});

test("...and a guessed nonce does not help", async () => {
  reset();
  let answer = null;
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "Evil",
    overlay: "ov-guessed" }, { frameId: 3, tab: { id: 42 } },
  (r) => { answer = r; });
  await settle();
  assert.equal(answer.ok, false);
  assert.equal(calls.fetches.length, 0);
});

test("OUR overlay's pick, carrying the id we issued, goes through", async () => {
  // The counterweight: the guard must not break the path it protects.
  const id = await liveOverlay();
  searchResult = [];
  let answer = null;
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "Jane Doe",
    overlay: id }, { frameId: 7, tab: { id: 42 } }, (r) => { answer = r; });
  await settle(20);
  assert.equal(answer.ok, true);
  assert.equal(answer.dir, "Jane Doe");
});

test("the popup window's pick is untouched by the guard", async () => {
  // It is the TOP frame of its own tab and carries no nonce, which is exactly
  // why the test is on frameId rather than on a nonce being present.
  reset();
  searchResult = [];
  let answer = null;
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "Jane Doe" },
    { frameId: 0, tab: { id: 42 } }, (r) => { answer = r; });
  await settle(20);
  assert.equal(answer.ok, true);
});

test("a sender with no frame information at all is still served", async () => {
  reset();
  searchResult = [];
  let answer = null;
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "Jane Doe" }, {},
    (r) => { answer = r; });
  await settle(20);
  assert.equal(answer.ok, true);
});

// --- the registry must outlive the ~30s MV3 idle teardown ------------------- //
//
// Choosing a directory is the SLOWEST thing the user does here, so the picker
// routinely outlives the worker that opened it. Every consumer of the overlay
// registry therefore has to read the durable copy, or it reasons from an empty
// Map on exactly the path that matters most.

/** The worker being torn down and woken: memory gone, storage.session kept. */
const tearDownWorker = () => SW.state.overlays.clear();

test("A LEGITIMATE PICK SURVIVES AN MV3 TEARDOWN", async () => {
  // THE regression. `state.overlays` is in-memory and nothing repopulated it on
  // wake, so after ~30 s the anti-clickjack guard saw an empty registry and
  // refused a real pick -- discarding the user's choice precisely when they had
  // been deliberating longest. Same shape as the screened-refusal suppression
  // map: a fact that must outlive a teardown does not live in worker memory.
  const id = await liveOverlay();
  await settle();
  tearDownWorker();
  assert.equal(SW.state.overlays.size, 0, "the worker really did lose it");

  searchResult = [];
  let answer = null;
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "Jane Doe",
    overlay: id }, { frameId: 7, tab: { id: 42 } }, (r) => { answer = r; });
  await settle(20);
  assert.equal(answer.ok, true, answer && answer.error);
  assert.equal(answer.dir, "Jane Doe");
});

test("...but a forged id still cannot get through after one", async () => {
  // The counterweight: restoring the durable copy must not degrade into a
  // blanket allow. Delete the guard entirely and the test above still passes;
  // this is the one that notices.
  await liveOverlay();
  await settle();
  tearDownWorker();
  let answer = null;
  SW.onMessage({ type: "dlr:choose", downloadId: 1, dir: "Evil",
    overlay: "ov-guessed" }, { frameId: 7, tab: { id: 42 } },
  (r) => { answer = r; });
  await settle(20);
  assert.equal(answer.ok, false);
  assert.match(answer.error, /unrecognised frame/);
  assert.equal(calls.fetches.length, 0);
});

test("the tab watchers survive a teardown too", async () => {
  // Otherwise a tab closing after the worker slept finds no overlay to rescue,
  // and the download is left with no picker at all -- the same invariant, one
  // wake later.
  await liveOverlay();
  await settle();
  tearDownWorker();
  await SW.onTabRemoved(42);
  await settle();
  assert.equal(calls.windowsCreate.length, 1, "the question is re-asked");
});

test("a navigation after a teardown re-asks too", async () => {
  await liveOverlay();
  await settle();
  tearDownWorker();
  await SW.onTabUpdated(42, { status: "loading" });
  await settle();
  assert.equal(calls.windowsCreate.length, 1);
});

test("restoreOverlays never clobbers an overlay opened this turn", async () => {
  const live = await liveOverlay({ downloadId: 2, tabId: 43 });
  await settle();
  // A durable copy that disagrees about the live overlay, plus one the worker
  // has genuinely forgotten. The live record must win; the forgotten one must
  // come back.
  storageSession.overlays = [
    [live, { tabId: 999, downloadId: 999, info: { downloadId: 999 } }],
    ["ov-older", { tabId: 42, downloadId: 1, info: { downloadId: 1 } }],
  ];
  await SW.restoreOverlays();
  assert.equal(SW.state.overlays.get(live).tabId, 43, "the live one wins");
  assert.equal(SW.state.overlays.has("ov-older"), true, "the other is restored");
});

test("a corrupt durable copy is ignored rather than trusted", async () => {
  reset();
  storageSession.overlays = ["nope", null, [1, 2], ["ov-x", "not-an-object"]];
  await SW.restoreOverlays();
  assert.equal(SW.state.overlays.size, 0);
  storageSession.overlays = { not: "an array" };
  await SW.restoreOverlays();
  assert.equal(SW.state.overlays.size, 0);
});

test("registerListeners really registers the tab watchers", () => {
  // They sit inside a try/catch for older shapes, which is exactly how a
  // registration can silently become a no-op.
  reset();
  calls.tabListeners.length = 0;
  SW.registerListeners();
  const kinds = calls.tabListeners.map(([k]) => k);
  assert.ok(kinds.includes("removed"), "tabs.onRemoved");
  assert.ok(kinds.includes("updated"), "tabs.onUpdated");
});

// --- the duplicate flow: confirm after completion, warn, never destroy ------ //
//
// THE DUPLICATE CHECK CANNOT HAPPEN ON THE /match PATH. At
// onDeterminingFilename time Chrome has written nothing, so there is no file to
// hash, and `totalBytes` is frequently 0 so even the size is unreliable. These
// pin that it happens on COMPLETION, that it only ever warns, and that the one
// destructive message refuses everything it cannot get an answer for.

function dedupeResponder(dedupe, { onPost } = {}) {
  return async (url, opts) => {
    if (onPost) onPost(url, JSON.parse(opts?.body || "{}"));
    if (url.endsWith("/dedupe")) {
      return { ok: true, status: 200, json: async () => dedupe };
    }
    if (url.endsWith("/relocate")) {
      return { ok: true, status: 200,
        json: async () => ({ ok: true, relPath: "Jane Doe/f (1).mp4" }) };
    }
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
}

test("a completed auto-filed download is checked for duplicates", async () => {
  reset();
  SW.state.pending.set(200, { dir: "Jane Doe", payload: { page: {} } });
  searchResult = [{ id: 200, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  const posted = [];
  fetchHandler = dedupeResponder(
    { ok: true, duplicate: false },
    { onPost: (url, body) => posted.push({ url, body }) });
  await SW.onDownloadChanged({ id: 200, state: { current: "complete" } });
  const check = posted.find((p) => p.url.endsWith("/dedupe"));
  assert.ok(check, "no duplicate check ran on the auto-file path");
  assert.deepEqual(check.body, { relPath: "Jane Doe/f.mp4", downloadId: 200 });
  assert.equal(calls.windowsCreate.length, 0, "nothing to warn about");
});

test("a CONFIRMED duplicate opens a toast offering delete and keep", async () => {
  reset();
  SW.state.pending.set(201, { dir: "Jane Doe", payload: { page: {} } });
  searchResult = [{ id: 201, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  fetchHandler = dedupeResponder({
    ok: true, duplicate: true, relPath: "Jane Doe/f.mp4",
    dupRelPath: "john-smith/75936.mov", kind: "size+hash" });
  await SW.onDownloadChanged({ id: 201, state: { current: "complete" } });
  assert.equal(calls.windowsCreate.length, 1);
  const url = calls.windowsCreate[0].url;
  assert.match(url, /toast\.html\?/);
  const q = new URLSearchParams(url.split("?")[1]);
  assert.equal(q.get("mode"), "dup");
  assert.equal(q.get("rel"), "Jane Doe/f.mp4");
  assert.equal(q.get("dupRel"), "john-smith/75936.mov");
  assert.match(q.get("dup"), /Duplicate of john-smith\/75936\.mov/);
  assert.equal(calls.windowsCreate[0].focused, true,
    "a question with no auto-close must not open behind the browser");
});

test("the duplicate check uses the path AFTER a correction moved the file",
  async () => {
    // The sidecar uniquifies on a name collision, so its answer is the
    // authority on where the file is. Checking the pre-move path would ask
    // about a file that is no longer there.
    reset();
    SW.state.pending.set(202, { dir: "other", payload: { page: {} },
      wanted: "Jane Doe" });
    searchResult = [{ id: 202, state: "complete",
      filename: `${LIB_ROOT}/other/f.mp4` }];
    const posted = [];
    fetchHandler = dedupeResponder({ ok: true, duplicate: false },
      { onPost: (url, body) => posted.push({ url, body }) });
    await SW.onDownloadChanged({ id: 202, state: { current: "complete" } });
    const check = posted.find((p) => p.url.endsWith("/dedupe"));
    assert.equal(check.body.relPath, "Jane Doe/f (1).mp4");
  });

test("a failing duplicate check never breaks the completion path", async () => {
  reset();
  SW.state.pending.set(203, { dir: "other", payload: { page: {} },
    wanted: "Jane Doe" });
  searchResult = [{ id: 203, state: "complete",
    filename: `${LIB_ROOT}/other/f.mp4` }];
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push(url);
    if (url.endsWith("/dedupe")) throw new Error("sidecar went away");
    void opts;
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  await SW.onDownloadChanged({ id: 203, state: { current: "complete" } });
  assert.ok(posted.some((u) => u.endsWith("/learn")),
    "the correction must still have completed");
  assert.equal(calls.notifications.length, 0,
    "a dedupe failure is not worth interrupting anyone for");
});

test("a duplicate answer with no counterpart path is ignored", async () => {
  // Without `dupRelPath` there is nothing proving the bytes exist elsewhere,
  // so there is nothing to offer a delete against.
  reset();
  SW.state.pending.set(204, { dir: "Jane Doe", payload: { page: {} } });
  searchResult = [{ id: 204, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  fetchHandler = dedupeResponder({ ok: true, duplicate: true });
  await SW.onDownloadChanged({ id: 204, state: { current: "complete" } });
  assert.equal(calls.windowsCreate.length, 0);
});

test("the duplicate toast falls back to a notification with NO delete",
  async () => {
    reset();
    windowsCreateFails = true;
    await SW.showDuplicateToast({ downloadId: 205, dir: "Jane Doe",
      relPath: "Jane Doe/f.mp4", dupRelPath: "john-smith/75936.mov" });
    assert.equal(calls.notifications.length, 1);
    assert.match(calls.notifications[0].message, /The file was kept/);
    assert.equal(calls.notifications[0].buttons, undefined,
      "a notification cannot show a refusal, so it must not offer a delete");
  });

// --- dlr:discard: the one destructive message ------------------------------- //
test("dlr:discard forwards all three fields to /discard", async () => {
  reset();
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200,
      json: async () => ({ ok: true, discarded: true }) };
  };
  const answer = await new Promise((resolve) => {
    SW.onMessage({ type: "dlr:discard", downloadId: 7,
      relPath: "Jane Doe/f.mp4", dupRelPath: "john-smith/75936.mov" },
    { frameId: 0 }, resolve);
  });
  assert.deepEqual(answer, { ok: true, discarded: true });
  const call = posted.find((p) => p.url.endsWith("/discard"));
  assert.deepEqual(call.body, { relPath: "Jane Doe/f.mp4",
    dupRelPath: "john-smith/75936.mov", downloadId: 7 });
});

test("a REFUSED discard is reported, twice, and never claimed as done",
  async () => {
    reset();
    fetchHandler = async (url) => {
      if (url.endsWith("/discard")) {
        return { ok: false, status: 400,
          json: async () => ({ detail: "refusing to move a file this router "
            + "cannot prove it created" }) };
      }
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    };
    const answer = await new Promise((resolve) => {
      SW.onMessage({ type: "dlr:discard", downloadId: 7,
        relPath: "Jane Doe/f.mp4", dupRelPath: "john-smith/75936.mov" },
      { frameId: 0 }, resolve);
    });
    assert.equal(answer.ok, false);
    assert.match(answer.error, /cannot prove it created/);
    assert.equal(calls.notifications.length, 1,
      "a refused DELETE is the one refusal the user must not miss");
    assert.match(calls.notifications[0].title, /Did not delete/);
  });

test("a discard from an embedded FRAME is refused outright", async () => {
  // toast.html is web-accessible, so a page could frame it. There is no
  // legitimate embedded duplicate toast, so there is nothing to weigh up.
  reset();
  const posted = [];
  fetchHandler = async (url) => {
    posted.push(url);
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  const answer = await new Promise((resolve) => {
    SW.onMessage({ type: "dlr:discard", downloadId: 7,
      relPath: "Jane Doe/f.mp4", dupRelPath: "john-smith/75936.mov" },
    { frameId: 3 }, resolve);
  });
  assert.equal(answer.ok, false);
  assert.match(answer.error, /embedded frame/);
  assert.equal(posted.filter((u) => u.endsWith("/discard")).length, 0,
    "nothing may reach the sidecar from a framed toast");
});

// --- the overlay picker takes the keyboard focus back ----------------------- //
test("the overlay is re-focused AFTER the tab and window are raised", async () => {
  // Raising a tab focuses the PAGE's document, which strips focus off the
  // frame the content script had just given it. This message is what wins.
  reset();
  contentScriptMissing = false;
  const ok = await SW.openOverlayPicker({ downloadId: 300, dir: "other",
    tabId: 42, reason: "r" });
  assert.equal(ok, true);
  const kinds = calls.tabMessages.map((m) => m.msg.type);
  const opened = kinds.indexOf("dlr:overlay-open");
  const focused = kinds.indexOf("dlr:focus-overlay");
  assert.ok(focused > opened, "no focus request was sent");
  const raiseIndex = calls.tabsUpdate.length;
  assert.ok(raiseIndex >= 1, "the tab was never raised");
  const focusMsg = calls.tabMessages[focused];
  assert.equal(focusMsg.opts.frameId, 0);
  assert.equal(typeof focusMsg.msg.overlay, "string");
});

test("a failed focus request does not lose a working overlay", async () => {
  reset();
  contentScriptMissing = false;
  const realSend = chrome.tabs.sendMessage;
  chrome.tabs.sendMessage = async (id, msg, opts) => {
    if (msg && msg.type === "dlr:focus-overlay") throw new Error("gone");
    return realSend(id, msg, opts);
  };
  try {
    assert.equal(await SW.openOverlayPicker({ downloadId: 301, dir: "other",
      tabId: 42 }), true);
  } finally {
    chrome.tabs.sendMessage = realSend;
  }
});

test("a repeated completion delta does NOT ask or warn twice", async () => {
  // `state.pending` is kept for five minutes after completion so a late
  // "change" click can still relocate, and nothing stops Chrome delivering a
  // second `complete` delta in that window. Without the latch that is a second
  // /dedupe and a second FOCUSED, never-auto-closing duplicate toast for one
  // download -- they would stack up in front of the user.
  reset();
  SW.state.pending.set(206, { dir: "Jane Doe", payload: { page: {} } });
  searchResult = [{ id: 206, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  const posted = [];
  fetchHandler = dedupeResponder({
    ok: true, duplicate: true, relPath: "Jane Doe/f.mp4",
    dupRelPath: "john-smith/75936.mov", kind: "size+hash" },
  { onPost: (url) => posted.push(url) });
  await SW.onDownloadChanged({ id: 206, state: { current: "complete" } });
  await SW.onDownloadChanged({ id: 206, state: { current: "complete" } });
  assert.equal(posted.filter((u) => u.endsWith("/dedupe")).length, 1,
    "the duplicate check ran twice for one download");
  assert.equal(calls.windowsCreate.length, 1,
    "two duplicate toasts for one download");
});

test("confirmDuplicate without an entry still answers (the latch is optional)",
  async () => {
    reset();
    fetchHandler = dedupeResponder({ ok: true, duplicate: false });
    assert.equal(await SW.confirmDuplicate(207, "Jane Doe/f.mp4", "Jane Doe"),
      null);
  });

// --- the player-button path -------------------------------------------------- //
//
// A click on an injected button inside a CROSS-ORIGIN embed frame. The frame
// has the media URL and nothing else; the top frame has the subject and cannot
// see the media; only the worker can reach both.
const EMBED_URL = "https://embedhost.example.test/embed/SYNTH8563";
const MEDIA_URL = "https://cdn.example.test/media/data/f.mp4"
  + "?exp=1900000000&token=SIGNED_A&fn=f.mp4";
const FORUM_URL = "https://forum.example.test/threads/jane-doe-set.90001/page-6";

/** What the top frame reports when a context rule matched. */
function topContext(overrides = {}) {
  return {
    ok: true,
    context: {
      href: "", mediaSrc: "", linkText: "", alt: "",
      pageUrl: FORUM_URL,
      pageTitle: "Jane Doe | Example Forums",
      site: "forum.example.test",
      tags: ["Jane Doe"],
      og: {},
      ...overrides,
    },
  };
}

/** The frame sending `dlr:player-download`; `frameId > 0` -- a subframe. */
const embedSender = (tabId = 7) => ({ tab: { id: tabId }, frameId: 4 });

test("a player click downloads the URL read at click time", async () => {
  reset();
  pageContextResult = topContext();
  const res = await SW.playerDownload(
    { mediaUrl: MEDIA_URL, embedUrl: EMBED_URL }, embedSender());
  assert.equal(res.ok, true);
  assert.equal(res.context, true);
  assert.deepEqual(calls.downloads, [{ url: MEDIA_URL }]);
});

test("frame -> top-frame correlation asks FRAME 0 OF THE SAME TAB", async () => {
  // The only proof available: `document.referrer` inside the embed is the forum
  // ORIGIN with the path stripped, and ancestorOrigins carries no paths either.
  reset();
  pageContextResult = topContext();
  await SW.playerDownload({ mediaUrl: MEDIA_URL, embedUrl: EMBED_URL },
    embedSender(11));
  const ask = calls.tabMessages.find((m) => m.msg.type === "dlr:page-context");
  assert.ok(ask, "the worker must ask the top frame");
  assert.equal(ask.id, 11, "the tab the click came from");
  assert.deepEqual(ask.opts, { frameId: 0 }, "the TOP frame, never any other");
});

test("a correlated click carries the thread's subject into /match", async () => {
  reset();
  pageContextResult = topContext();
  const posted = [];
  fetchHandler = async (url, opts) => {
    if (url.endsWith("/match")) {
      posted.push(JSON.parse(opts.body));
      return { ok: true, status: 200,
        json: async () => ({ dir: "Jane Doe", confidence: 1, auto: true,
          reason: "alias(thread-slug)" }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  await SW.playerDownload({ mediaUrl: MEDIA_URL, embedUrl: EMBED_URL },
    embedSender());
  // Now Chrome dispatches the download the click started.
  SW.onDeterminingFilename({ id: 401, url: MEDIA_URL, filename: "f.mp4" },
    () => {});
  await settle(5);
  assert.equal(posted.length, 1);
  const p = posted[0];
  // Tier 1 bound the synthesised capture to the DownloadItem by exact URL --
  // no time window, no active-tab guess.
  assert.equal(p.page.url, FORUM_URL);
  assert.deepEqual(p.page.tags, ["Jane Doe"]);
  assert.equal(p.page.site, "forum.example.test");
});

test("THE LEDGER KEY IS THE EMBED URL, NOT THE SIGNED MEDIA URL", async () => {
  // The media URL is re-signed in place roughly hourly. Keyed on it, the ledger
  // would mint a new row per rotation and the badge would never light.
  reset();
  pageContextResult = topContext();
  const posted = [];
  fetchHandler = async (url, opts) => {
    if (url.endsWith("/match")) posted.push(JSON.parse(opts.body));
    return { ok: true, status: 200,
      json: async () => ({ dir: "Jane Doe", confidence: 1, auto: true }) };
  };
  await SW.playerDownload({ mediaUrl: MEDIA_URL, embedUrl: `${EMBED_URL}?autoplay=1#t=3` },
    embedSender());
  SW.onDeterminingFilename({ id: 402, url: MEDIA_URL, filename: "f.mp4" },
    () => {});
  await settle(5);
  assert.equal(posted[0].sourceKey, EMBED_URL,
    "normalised to scheme+host+path: playback params are not an identity");
  assert.notEqual(posted[0].sourceKey, MEDIA_URL);
});

test("the player WRITE folds a Discord embed the same way the read does",
  async () => {
    // 🔴 THE SECOND WRITER. There are TWO sites that turn one URL into a
    // ledger key -- this one and haveUrl -- and the Discord fold originally
    // landed on the reader and NOT here, so a lookup asked for a string this
    // writer never stored. Both go through `ledgerSourceKey` now; this pins
    // that they agree, which is the relationship, not either half.
    reset();
    const path = "/attachments/119283746551234567/998877665544332211/a.mp4";
    const posted = [];
    fetchHandler = async (url, opts) => {
      if (url.endsWith("/match")) posted.push(JSON.parse(opts.body));
      return { ok: true, status: 200,
        json: async () => ({ dir: "Jane Doe", confidence: 1, auto: true }) };
    };
    await SW.playerDownload(
      { mediaUrl: "https://cdn.example-cdn.test/v/abc.mp4?sig=1",
        embedUrl: `https://media.discordapp.net${path}?width=550` },
      embedSender());
    SW.onDeterminingFilename(
      { id: 403, url: "https://cdn.example-cdn.test/v/abc.mp4?sig=1",
        filename: "f.mp4" }, () => {});
    await settle(5);
    // The literal, hand-spelled -- not computed from the function under test.
    assert.equal(posted[0].sourceKey, `https://cdn.discordapp.com${path}`);
  });

test("CORRELATION FAILURE DEGRADES TO THE PICKER, never to a wrong subject",
  async () => {
    // The top frame has no content script (a CSP-sandboxed host, a page still
    // loading, a tab that navigated). The tempting fallback -- the newest
    // capture from this tab -- is the branch carryReferrer already deleted for
    // learning "the last thread I saw" as a 1.00 alias.
    reset();
    pageContextThrows = true;
    // A capture from a DIFFERENT thread, live in the same tab. If any fallback
    // existed, this is the wrong subject it would import.
    SW.state.captures = [{
      href: "https://forum.example.test/other", mediaSrc: "",
      pageUrl: "https://forum.example.test/threads/someone-else.777/",
      pageTitle: "Someone Else", site: "forum.example.test",
      tags: ["Someone Else"], og: {}, tabId: 7, ts: Date.now(),
    }];
    const posted = [];
    fetchHandler = async (url, opts) => {
      if (url.endsWith("/match")) {
        posted.push(JSON.parse(opts.body));
        return { ok: true, status: 200,
          json: async () => ({ dir: "other", confidence: 0.1, auto: false,
            reason: "no match" }) };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    };
    const res = await SW.playerDownload(
      { mediaUrl: MEDIA_URL, embedUrl: EMBED_URL }, embedSender());
    assert.equal(res.ok, true);
    assert.equal(res.context, false, "the worker must admit it proved nothing");

    SW.onDeterminingFilename({ id: 403, url: MEDIA_URL, filename: "f.mp4" },
      () => {});
    await settle(5);
    assert.equal(posted.length, 1);
    assert.equal(posted[0].page.url, "", "no page URL was proven");
    assert.deepEqual(posted[0].page.tags, [], "and no subject was invented");
    assert.equal(posted[0].page.title, "");
    for (const value of Object.values(posted[0].page)) {
      assert.notEqual(value, "Someone Else");
    }
    // ...and the user is asked.
    const picker = calls.windowsCreate.find((w) => /picker\.html/.test(w.url));
    assert.ok(picker, "an unproven subject must reach the picker");
  });

test("a top frame that answers without a usable page URL counts as a failure",
  async () => {
    reset();
    pageContextResult = topContext({ pageUrl: "about:blank" });
    const res = await SW.playerDownload(
      { mediaUrl: MEDIA_URL, embedUrl: EMBED_URL }, embedSender());
    assert.equal(res.context, false);
    assert.equal(SW.state.captures[0].tags.length, 0);
  });

test("a non-http media URL is refused before any download starts", async () => {
  reset();
  pageContextResult = topContext();
  for (const bad of ["blob:abcd", "javascript:alert(1)", "", null,
    "file:///etc/passwd"]) {
    const res = await SW.playerDownload(
      { mediaUrl: bad, embedUrl: EMBED_URL }, embedSender());
    assert.equal(res.ok, false, JSON.stringify(bad));
  }
  assert.deepEqual(calls.downloads, []);
});

test("a player click in a profile where routing is off does nothing", async () => {
  reset({ enabled: false });
  const res = await SW.playerDownload(
    { mediaUrl: MEDIA_URL, embedUrl: EMBED_URL }, embedSender());
  assert.equal(res.ok, false);
  assert.deepEqual(calls.downloads, []);
});

test("a message with no tab cannot start a download", async () => {
  reset();
  const res = await SW.playerDownload(
    { mediaUrl: MEDIA_URL, embedUrl: EMBED_URL }, { frameId: 4 });
  assert.equal(res.ok, false);
  assert.deepEqual(calls.downloads, []);
});

test("dlr:player-download is routed, and answers asynchronously", async () => {
  reset();
  pageContextResult = topContext();
  let answer = null;
  const ret = SW.onMessage({ type: "dlr:player-download", mediaUrl: MEDIA_URL,
    embedUrl: EMBED_URL }, embedSender(), (r) => { answer = r; });
  assert.equal(ret, true, "the channel must stay open for the late response");
  await settle(5);
  assert.equal(answer.ok, true);
});

// --- the "already have this" badge ------------------------------------------- //
test("dlr:have asks the ledger by the NORMALISED embed url", async () => {
  reset();
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => ({ ok: true, have: true, dir: "Jane Doe" }) });
  const res = await SW.haveUrl(`${EMBED_URL}?autoplay=1`);
  assert.deepEqual(res, { ok: true, have: true, dir: "Jane Doe" });
  assert.equal(calls.fetches[0].url,
    `http://127.0.0.1:8791/have?url=${encodeURIComponent(EMBED_URL)}`);
});

test("dlr:have folds a Discord attachment the SAME WAY the ledger write does",
  async () => {
    // 🔴 A SEAM, not a component. The write side folds a Discord attachment's
    // authority to the origin before the ledger records it; if the read side
    // asked with the bare proxy key it would look up a string the writer never
    // stores, and the badge could only ever miss -- "a badge that never lights
    // actively asserts you do not have this". Neither side is wrong alone,
    // which is exactly why this has to be pinned as a RELATIONSHIP.
    reset();
    fetchHandler = async () => ({ ok: true, status: 200,
      json: async () => ({ ok: true, have: true, dir: "Jane Doe" }) });
    const path = "/attachments/119283746551234567/998877665544332211/a.png";
    await SW.haveUrl(`https://media.discordapp.net${path}?format=webp&width=550`);
    const asked = decodeURIComponent(calls.fetches[0].url.split("url=")[1]);
    // The literal the WRITE side produces, spelled out rather than computed
    // from the function under test.
    assert.equal(asked, `https://cdn.discordapp.com${path}`);
  });

test("a ledger miss is a miss, and a dead sidecar is also a miss", async () => {
  reset();
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => ({ ok: true, have: false }) });
  assert.deepEqual(await SW.haveUrl(EMBED_URL), { ok: true, have: false,
    dir: "" });
  fetchHandler = async () => { throw new Error("connection refused"); };
  // A badge is a hint on someone else's page: a sidecar that is down must
  // produce NO badge, never a broken one.
  assert.deepEqual(await SW.haveUrl(EMBED_URL), { ok: false, have: false });
});

test("an unusable embed url is not asked about at all", async () => {
  reset();
  for (const bad of ["", "blob:x", "about:blank", null]) {
    assert.deepEqual(await SW.haveUrl(bad), { ok: false, have: false });
  }
  assert.deepEqual(calls.fetches, []);
});

test("dlr:have is routed and answers asynchronously", async () => {
  reset();
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => ({ ok: true, have: true, dir: "Jane Doe" }) });
  let answer = null;
  const ret = SW.onMessage({ type: "dlr:have", embedUrl: EMBED_URL },
    embedSender(), (r) => { answer = r; });
  assert.equal(ret, true);
  await settle(5);
  assert.equal(answer.have, true);
});

// --- state.pending SURVIVES THE ~30s MV3 IDLE TEARDOWN ----------------------- //
//
// Every test below simulates the teardown the same way Chrome does it: the
// module globals go, `chrome.storage.local` stays. `state.pending.clear()` is
// therefore not a shortcut -- it IS the teardown.
function teardown() {
  SW.state.pending.clear();
  SW.state.overlays.clear();
  SW.state.captures = [];
}

test("A DOWNLOAD THAT OUTLIVES THE TEARDOWN KEEPS ITS TOAST", async () => {
  // The bug: `pending` was an in-memory Map and `onDownloadChanged` returns
  // early without an entry -- so a download taking longer than half a minute
  // lost its toast, its pending relocate AND its learning. Silently, and for
  // essentially every video.
  reset();
  SW.state.snapshot.root = LIB_ROOT;
  const suggest = spy();
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => ({ dir: "Jane Doe", confidence: 1, auto: true,
      reason: "alias" }) });
  SW.onDeterminingFilename({ id: 501, url: MEDIA_URL, filename: "f.mp4" },
    suggest);
  await settle(5);
  assert.ok(SW.state.pending.has(501));
  assert.ok(Array.isArray(storageLocal.pending),
    "the routing decision must be written durably, not only to memory");

  teardown();
  searchResult = [{ id: 501, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => ({ ok: true, duplicate: false }) });
  await SW.onDownloadChanged({ id: 501, state: { current: "complete" } });
  assert.ok(SW.state.pending.has(501),
    "the woken worker must find the entry it wrote before the teardown");
});

test("A CORRECTION MADE BEFORE THE TEARDOWN IS STILL APPLIED AFTER IT",
  async () => {
    // The picker is a separate window the user can leave open past the idle
    // timeout -- choosing is the slowest thing they do here -- so this is the
    // normal case, not an edge one.
    reset();
    SW.state.snapshot.root = LIB_ROOT;
    SW.state.pending.set(502, { dir: "other", filename: "f.mp4",
      payload: { page: {} }, ts: Date.now() });
    searchResult = [];   // still downloading: the choice is deferred
    const out = await SW.applyChoice(502, "Jane Doe", {});
    assert.deepEqual(out, { ok: true, dir: "Jane Doe", deferred: true });

    teardown();
    searchResult = [{ id: 502, state: "complete",
      filename: `${LIB_ROOT}/other/f.mp4` }];
    const posted = [];
    fetchHandler = async (url, opts) => {
      posted.push({ url, body: opts.body ? JSON.parse(opts.body) : null });
      return { ok: true, status: 200,
        json: async () => ({ ok: true, relPath: "Jane Doe/f.mp4" }) };
    };
    await SW.onDownloadChanged({ id: 502, state: { current: "complete" } });
    const moved = posted.find((p) => p.url.endsWith("/relocate"));
    assert.ok(moved, "the pick must survive the teardown and be applied");
    assert.equal(moved.body.toDir, "Jane Doe");
    assert.ok(posted.some((p) => p.url.endsWith("/learn")),
      "and it must still be learned from");
  });

test("the dedupe latch survives the teardown too", async () => {
  // Persisting the entry without persisting its latch would open a SECOND
  // focused, never-auto-closing duplicate toast for one download.
  reset();
  SW.state.snapshot.root = LIB_ROOT;
  // Routed through the real path, so the entry is genuinely durable -- setting
  // `state.pending` by hand would leave nothing in storage and the second delta
  // would find no entry at all, which passes for the wrong reason.
  fetchHandler = async () => ({ ok: true, status: 200,
    json: async () => ({ dir: "Jane Doe", confidence: 1, auto: true }) });
  SW.onDeterminingFilename({ id: 503, url: MEDIA_URL, filename: "f.mp4" },
    () => {});
  await settle(5);
  assert.ok(storageLocal.pending.some((r) => r[0] === 503));

  searchResult = [{ id: 503, state: "complete",
    filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
  const posted = [];
  fetchHandler = dedupeResponder({
    ok: true, duplicate: true, relPath: "Jane Doe/f.mp4",
    dupRelPath: "john-smith/x.mov", kind: "size+hash" },
  { onPost: (url) => posted.push(url) });
  await SW.onDownloadChanged({ id: 503, state: { current: "complete" } });
  teardown();
  await SW.onDownloadChanged({ id: 503, state: { current: "complete" } });
  assert.equal(posted.filter((u) => u.endsWith("/dedupe")).length, 1,
    "the duplicate check ran twice for one download");
  const dupToasts = calls.windowsCreate.filter((w) => /mode=dup/.test(w.url));
  assert.equal(dupToasts.length, 1, "two duplicate toasts for one download");
});

test("the restored registry is TTL- and size-bounded", async () => {
  // `storage.local` outlives the browser, so without these an abandoned
  // download would be resurrected weeks later, and the registry would grow
  // without limit.
  reset();
  const day = 24 * 60 * 60 * 1000;
  storageLocal.pending = [
    [601, { dir: "Jane Doe", ts: Date.now() }],
    [602, { dir: "Jane Doe", ts: Date.now() - day - 1000 }],
    ["not-a-pair"],
    [603, null],
    [{ bad: "key" }, { dir: "x", ts: Date.now() }],
  ];
  await SW.restorePending();
  assert.deepEqual([...SW.state.pending.keys()], [601]);

  reset();
  for (let i = 0; i < 100; i += 1) {
    SW.state.pending.set(700 + i, { dir: "Jane Doe", ts: Date.now() });
  }
  await SW.applyChoice(700, "Jane Doe", {});
  assert.ok(storageLocal.pending.length <= 64,
    `stored ${storageLocal.pending.length} entries`);
});

test("restorePending NEVER clobbers an entry created this turn", async () => {
  reset();
  storageLocal.pending = [[801, { dir: "stale", ts: Date.now() }]];
  SW.state.pending.set(801, { dir: "fresh", ts: Date.now() });
  await SW.restorePending();
  assert.equal(SW.state.pending.get(801).dir, "fresh");
});

test("the five-minute sweep clears the DURABLE copy, not just the memory one",
  async () => {
    // Otherwise the entry would be gone from memory and immediately restored
    // from storage by the next `pendingEntry`, and nothing would ever expire.
    reset();
    SW.state.pending.set(802, { dir: "Jane Doe", payload: { page: {} },
      ts: Date.now() });
    searchResult = [{ id: 802, state: "complete",
      filename: `${LIB_ROOT}/Jane Doe/f.mp4` }];
    fetchHandler = async () => ({ ok: true, status: 200,
      json: async () => ({ ok: true, duplicate: false }) });
    const realSetTimeout = globalThis.setTimeout;
    let sweep = null;
    globalThis.setTimeout = (fn, ms) => {
      if (ms === 5 * 60 * 1000) { sweep = fn; return { unref() {} }; }
      return realSetTimeout(fn, ms);
    };
    try {
      await SW.onDownloadChanged({ id: 802, state: { current: "complete" } });
    } finally {
      globalThis.setTimeout = realSetTimeout;
    }
    assert.ok(sweep, "a sweep must be armed for a completed download");
    assert.ok(storageLocal.pending.some((r) => r[0] === 802));
    sweep();
    await settle(0);
    assert.ok(!SW.state.pending.has(802));
    assert.ok(!storageLocal.pending.some((r) => r[0] === 802),
      "the durable copy would otherwise resurrect it on the next read");
  });

test("an entry past its TTL is dropped on WRITE, not only on read", async () => {
  reset();
  SW.state.pending.set(804, { dir: "Jane Doe",
    ts: Date.now() - 25 * 60 * 60 * 1000 });
  SW.state.pending.set(805, { dir: "Jane Doe", ts: Date.now() });
  await SW.applyChoice(805, "Jane Doe", {});
  assert.deepEqual(storageLocal.pending.map((r) => r[0]), [805]);
});

test("a routing decision no longer re-reads the config on every download",
  async () => {
    // `pending` now lives in chrome.storage.local alongside the token, so an
    // unfiltered storage.onChanged handler would call loadConfig() on every
    // single download event.
    reset();
    const src = readFileSync(
      new URL("../extension/service_worker.js", import.meta.url), "utf8");
    const handler = src.slice(src.indexOf("chrome.storage.onChanged"));
    assert.match(handler.slice(0, 600), /"token" in changes/);
    assert.match(handler.slice(0, 600), /"enabled" in changes/);
  });

test("after a teardown the picker still goes to the DOWNLOAD's tab", async () => {
  // `overlayTabId` reads `pending`, so before the durable read it sent every
  // post-teardown picker to a popup window (or worse, the wrong tab) --
  // precisely when a picker is most likely to be needed, because the user was
  // slow enough to trigger the teardown in the first place.
  reset();
  contentScriptMissing = false;
  tabUrl = "https://forum.example.test/threads/jane-doe.1/";
  SW.state.pending.set(901, { dir: "other", filename: "f.mp4", tabId: 42,
    payload: { page: {} }, ts: Date.now() });
  await SW.applyChoice(901, "other", {}).catch(() => {});
  teardown();
  await SW.openPicker({ downloadId: 901, dir: "other", reason: "" });
  const open = calls.tabMessages.find((m) => m.msg.type === "dlr:overlay-open");
  assert.ok(open, "the overlay must still be offered");
  assert.equal(open.id, 42, "in the tab the download came from");
});
