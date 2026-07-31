// Messages arriving at a COLD-WOKEN service worker.
//
// Its own file because it needs the module WITHOUT DL_ROUTER_NO_AUTOSTART and
// with the startup storage read held open, and `node --test` gives each file
// its own process.
//
// Why this is the realistic case, not an edge one: the toast and the picker are
// separate popup WINDOWS. MV3 tears the worker down after ~30 s idle, so a user
// who leaves the picker open for half a minute -- reading the reason string,
// deciding -- and then presses Enter is messaging a worker that has just been
// restarted and has not read its config yet.
//
// `onDeterminingFilename` and `onMenuClicked` awaited readiness; `onMessage`
// did not. Reproduced consequences:
//
//   * dlr:choose -> applyChoice computed knownDirs() from a null snapshot and
//     threw the user's pick away as "refusing unsafe directory";
//   * dlr:snapshot -> `Bearer ` -> 401 -> the picker cleared its loading state
//     with an EMPTY directory list, so typing a name and pressing Enter created
//     a new directory instead of selecting the existing match. Finding 16,
//     restored by a different route.
import test from "node:test";
import assert from "node:assert/strict";

const listeners = {};
const calls = { fetches: [] };

let releaseStorage;
const storageGate = new Promise((r) => { releaseStorage = r; });
const storageLocal = { token: "tok", enabled: true };
let storageSession = {};

const record = (name) => ({
  addListener: (fn) => { listeners[name] = fn; },
});

const SNAPSHOT = {
  etag: "abc123",
  otherDir: "other",
  root: "/home/u/library",
  threshold: 0.75,
  matchTimeoutMs: 50,
  captureWindowS: 15,
  dirs: [
    { name: "Jane Doe", key: "janedoe", tokens: ["jane", "doe"] },
    { name: "john-smith", key: "johnsmith", tokens: ["john", "smith"] },
  ],
  aliases: [],
  siteRules: { "example-site.test": { tags: [".tag a"] } },
};

globalThis.chrome = {
  storage: {
    local: {
      get: async (keys) => {
        await storageGate;                 // held until the test releases it
        const out = {};
        for (const k of [].concat(keys)) out[k] = storageLocal[k];
        return out;
      },
      set: async () => {},
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
    search: async () => [{ id: 70, state: "complete",
      filename: "/home/u/library/other/f.mp4" }],
    download: async () => 1,
  },
  contextMenus: {
    removeAll: async () => {},
    create: () => {},
    onClicked: record("contextMenus.onClicked"),
  },
  tabs: { query: async () => [], onActivated: record("tabs.onActivated") },
  windows: { create: async () => ({ id: 900 }), onRemoved: record("windows.onRemoved") },
  alarms: { create: () => {}, onAlarm: record("alarms.onAlarm") },
  notifications: { create: async () => "n1" },
};

// The token is `""` until loadConfig lands, so a request sent too early is a
// 401 -- exactly what the real sidecar answers.
globalThis.fetch = async (url, opts) => {
  calls.fetches.push({ url, auth: opts?.headers?.Authorization });
  if (opts?.headers?.Authorization === "Bearer ") {
    return { ok: false, status: 401, json: async () => ({}) };
  }
  if (url.endsWith("/dirs")) {
    return { ok: true, status: 200, json: async () => SNAPSHOT };
  }
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};

const SW = await import("../extension/service_worker.js");
const settle = (ms = 0) => new Promise((r) => setTimeout(r, ms));

test("precondition: the worker really is cold", () => {
  assert.equal(SW.state.configLoaded, false);
  assert.equal(SW.state.snapshot, null);
});

test("dlr:choose from a cold worker does NOT discard the user's pick", async () => {
  const responses = [];
  const returned = SW.onMessage(
    { type: "dlr:choose", downloadId: 70, dir: "Jane Doe" }, {},
    (r) => responses.push(r));
  assert.equal(returned, true, "must keep the channel open");

  // Nothing may be answered while the worker is still cold.
  await settle(5);
  assert.equal(responses.length, 0);

  releaseStorage();
  await SW.ready();
  await settle(20);

  assert.equal(responses.length, 1);
  assert.equal(responses[0].ok, true,
    `the pick was discarded: ${responses[0].error}`);
  assert.equal(responses[0].dir, "Jane Doe");
});

test("dlr:snapshot from a cold worker answers with a USABLE snapshot", async () => {
  await SW.ready();
  const responses = [];
  SW.onMessage({ type: "dlr:snapshot" }, {}, (r) => responses.push(r));
  await settle(20);
  assert.equal(responses[0].ok, true);
  assert.ok(responses[0].snapshot);
  assert.equal(responses[0].snapshot.dirs.length, 2,
    "an empty list here is what re-opened finding 16 in the picker");
});

test("no request was ever sent with an empty bearer token", () => {
  const unauthed = calls.fetches.filter((f) => f.auth === "Bearer ");
  assert.deepEqual(unauthed, [],
    "a request sent before readiness 401s, and the picker reads that as "
    + "'there are no directories'");
});

// NOTE: warm by this point -- the storage gate was released above and cannot
// be re-closed in-process. This pins the ASYNC ANSWER SHAPE (returning true and
// responding later), which is what the cold path depends on; the cold-path
// behaviour itself is covered by the three tests above.
test("dlr:rules answers asynchronously (warm -- shape only)", async () => {
  await SW.ready();
  const responses = [];
  const returned = SW.onMessage({ type: "dlr:rules" }, {},
    (r) => responses.push(r));
  assert.equal(returned, true, "async answer -> the channel stays open");
  await settle(5);
  assert.deepEqual(responses[0].siteRules, SNAPSHOT.siteRules);
});
