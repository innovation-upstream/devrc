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

globalThis.DL_ROUTER_NO_AUTOSTART = true;

// --- chrome mock ------------------------------------------------------------ //
const calls = {
  windowsCreate: [], notifications: [], downloads: [], search: [],
  fetches: [], menus: [],
};
let storageLocal = {};
let storageSession = {};
let searchResult = [];
let windowsCreateFails = false;
let notificationsFail = false;

globalThis.chrome = {
  storage: {
    local: {
      get: async (keys) => {
        const out = {};
        for (const k of [].concat(keys)) out[k] = storageLocal[k];
        return out;
      },
      set: async (obj) => Object.assign(storageLocal, obj),
    },
    session: {
      get: async (k) => ({ [k]: storageSession[k] }),
      set: async (obj) => Object.assign(storageSession, obj),
    },
    onChanged: { addListener() {} },
  },
  runtime: { getURL: (p) => `chrome-extension://test/${p}`, onMessage: { addListener() {} } },
  windows: {
    create: async (opts) => {
      calls.windowsCreate.push(opts);
      if (windowsCreateFails) throw new Error("no window");
      return { id: 1 };
    },
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
  tabs: { query: async () => [], onActivated: { addListener() {} } },
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
    { name: "Jane Doe", key: "janedoe", tokens: ["jane", "doe"] },
    { name: "john-smith", key: "johnsmith", tokens: ["john", "smith"] },
  ],
  aliases: [],
  siteRules: { "example-site.test": { tags: [".tag a"] } },
};

function reset({ enabled = true, snapshot = SNAPSHOT } = {}) {
  for (const k of Object.keys(calls)) calls[k].length = 0;
  storageLocal = { port: 8791, token: "tok", enabled };
  storageSession = {};
  searchResult = [];
  windowsCreateFails = false;
  notificationsFail = false;
  SW.state.config = { port: 8791, token: "tok", enabled };
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
            tokens: ["aster", "vale"] }] }) };
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

test("the menu routes a stream through the yt-dlp endpoint", async () => {
  reset();
  const posted = [];
  fetchHandler = async (url, opts) => {
    posted.push({ url, body: JSON.parse(opts.body || "{}") });
    return { ok: true, status: 200, json: async () => ({ jobId: "j1" }) };
  };
  await SW.onMenuClicked({
    menuItemId: SW.MENU_ID,
    srcUrl: "https://cdn.example-site.test/master.m3u8",
    pageUrl: "https://example-site.test/v/1",
  }, {});
  assert.equal(calls.downloads.length, 0);
  const fetchCall = posted.find((p) => p.url.endsWith("/fetch"));
  assert.equal(fetchCall.body.url, "https://example-site.test/v/1");
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
  assert.equal(SW.onMessage({ type: "dlr:rules" }, {}, (r) => rules.push(r)),
    false);
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
  await SW.applyChoice(31, "Jane Doe");
  assert.equal(posted.filter((u) => u.endsWith("/relocate")).length, 0,
    "the last two components of an outside path must not become a relPath");
  assert.ok(posted.some((u) => u.endsWith("/learn")),
    "the correction is still learned");
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
