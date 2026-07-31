// The service worker's STARTUP contract. Its own test file because it must run
// the module WITHOUT DL_ROUTER_NO_AUTOSTART, and `node --test` gives each file
// its own process (so the module registry, and `globalThis.chrome`, are clean).
//
// What is being pinned:
//
//   MV3 tears this worker down after ~30s idle and restarts it on the next
//   event. Chrome requires every listener to be registered in the FIRST TURN of
//   the service-worker script -- a listener added after an `await` may not
//   exist yet when the event that woke the worker is dispatched.
//
//   `start()` used to `await loadConfig()` and `await restoreSnapshot()` BEFORE
//   `chrome.downloads.onDeterminingFilename.addListener`. So a download that
//   woke the worker could be dispatched with no listener at all: Chrome used
//   the default filename and the file landed loose in the LIBRARY ROOT --
//   unrouted, and mixed in with the qBittorrent seeding payloads.
//
//   `start()` also had zero coverage.
//
// The trick that makes this a real test rather than an assertion on a mock: the
// chrome.storage.local read is held OPEN. If registration happened after the
// await, no listener exists at the moment we check.
import test from "node:test";
import assert from "node:assert/strict";

const calls = { addListener: [], menus: [], alarms: [], fetches: [] };
const listeners = {};

// A gate we control, standing in for the cold-start storage read.
let releaseStorage;
const storageGate = new Promise((r) => { releaseStorage = r; });
let storageLocal = { token: "tok", enabled: true };
let storageSession = {};

const record = (name) => ({
  addListener: (fn) => { calls.addListener.push(name); listeners[name] = fn; },
});

globalThis.chrome = {
  storage: {
    local: {
      get: async (keys) => {
        await storageGate;                   // <-- held until the test says so
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
    onChanged: record("storage.onChanged"),
  },
  runtime: {
    getURL: (p) => `chrome-extension://test/${p}`,
    onMessage: record("runtime.onMessage"),
    getManifest: () => ({ host_permissions: ["http://127.0.0.1:8791/*"] }),
  },
  downloads: {
    onDeterminingFilename: record("downloads.onDeterminingFilename"),
    onChanged: record("downloads.onChanged"),
    search: async () => [],
    download: async () => 1,
  },
  contextMenus: {
    removeAll: async () => { calls.menus.push("removeAll"); },
    create: (o) => calls.menus.push(o),
    onClicked: record("contextMenus.onClicked"),
  },
  tabs: { query: async () => [{ id: 3, windowId: 1 }], onActivated: record("tabs.onActivated") },
  windows: { create: async () => ({ id: 900 }), onRemoved: record("windows.onRemoved") },
  alarms: {
    create: (name, opts) => calls.alarms.push([name, opts]),
    onAlarm: record("alarms.onAlarm"),
  },
  notifications: { create: async () => "n1" },
};

const SNAPSHOT = {
  etag: "abc123",
  otherDir: "other",
  root: "/home/u/library",
  threshold: 0.75,
  matchTimeoutMs: 50,
  captureWindowS: 15,
  dirs: [{ name: "Jane Doe", key: "janedoe", tokens: ["jane", "doe"] }],
  aliases: [],
};

globalThis.fetch = async (url) => {
  calls.fetches.push(url);
  return { ok: true, status: 200, json: async () => SNAPSHOT };
};

// NOTE: no DL_ROUTER_NO_AUTOSTART. Importing runs the module's own bootstrap,
// exactly as Chrome would.
const SW = await import("../extension/service_worker.js");

const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));

function spy() {
  const c = [];
  const fn = (a) => c.push(a);
  fn.calls = c;
  return fn;
}

test("every listener is registered before startup can await anything", () => {
  // The storage read is STILL BLOCKED at this point -- `start()` cannot have
  // progressed past its first await. On the pre-fix module `listeners` is empty
  // here and this fails.
  assert.equal(SW.state.configLoaded, false, "precondition: config not read yet");
  for (const name of [
    "downloads.onDeterminingFilename",
    "downloads.onChanged",
    "runtime.onMessage",
    "contextMenus.onClicked",
    "tabs.onActivated",
    "storage.onChanged",
    "alarms.onAlarm",
  ]) {
    assert.ok(calls.addListener.includes(name), `${name} was not registered`);
  }
});

test("the refresh alarm is armed synchronously too", () => {
  assert.equal(calls.alarms.length, 1);
  assert.equal(calls.alarms[0][0], "dlr-refresh");
});

test("a download that wakes the worker is routed, not dropped in the root", async () => {
  // The whole point: dispatch BEFORE readiness, exactly as Chrome would when
  // the download is the event that restarted the worker.
  const suggest = spy();
  const ret = SW.state.configLoaded;
  assert.equal(ret, false, "precondition: still cold");

  const listener = listeners["downloads.onDeterminingFilename"];
  const returned = listener({ id: 1, filename: "clip.mp4",
    url: "https://example-site.test/f.mp4" }, suggest);
  assert.equal(returned, true, "must claim the async suggest slot");
  assert.equal(suggest.calls.length, 0, "nothing decided while still cold");

  releaseStorage();          // the worker finishes waking up
  await SW.ready();
  await settle(80);

  assert.equal(suggest.calls.length, 1, "suggest must be called exactly once");
  assert.equal(suggest.calls[0].conflictAction, "uniquify");
  assert.match(suggest.calls[0].filename, /\/clip\.mp4$/);
  assert.ok(!suggest.calls[0].filename.startsWith("/"),
    "the suggestion is relative to the download root");
});

test("start() populated the config, the snapshot and the menus", async () => {
  await SW.ready();
  assert.equal(SW.state.configLoaded, true);
  assert.deepEqual(SW.state.config, { port: 8791, token: "tok", enabled: true });
  assert.equal(SW.state.snapshot.etag, "abc123");
  assert.ok(calls.fetches.some((u) => u.endsWith("/dirs")));
  assert.ok(calls.menus.includes("removeAll"));
  assert.ok(calls.menus.some((m) => m && m.id === SW.MENU_ID));
});

test("start() seeds activeTabId from the focused window", async () => {
  await SW.ready();
  assert.equal(SW.state.activeTabId, 3);
});

test("once warm, a disabled profile declines synchronously", async () => {
  await SW.ready();
  const before = SW.state.config.enabled;
  SW.state.config = { ...SW.state.config, enabled: false };
  try {
    const suggest = spy();
    const listener = listeners["downloads.onDeterminingFilename"];
    assert.equal(listener({ id: 2, filename: "x.mp4" }, suggest), false,
      "a profile that never opted in must behave like stock Brave");
    await settle(10);
    assert.equal(suggest.calls.length, 0);
  } finally {
    SW.state.config = { ...SW.state.config, enabled: before };
  }
});

test("bootstrap() is idempotent enough to re-arm the module", async () => {
  const before = calls.addListener.length;
  await SW.bootstrap();
  assert.ok(calls.addListener.length > before, "listeners re-registered");
  assert.equal(SW.state.configLoaded, true);
});
