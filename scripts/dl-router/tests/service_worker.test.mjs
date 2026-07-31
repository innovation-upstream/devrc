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
  fetches: [], menus: [], tabsGet: [], tabMessages: [],
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
let overlayReports = "ready";       // "ready" | "silent"

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
      return { id, url: tabUrl, discarded: tabDiscarded };
    },
    sendMessage: async (id, msg, opts) => {
      calls.tabMessages.push({ id, msg, opts });
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
