// Glue tests for the TOOLBAR CLICK path (service_worker.js handleActionClick)
// against a mocked chrome — no Brave, no server, no clipboard.
//
// WHY THIS FILE EXISTS. Every other thing the worker does has a wire surface: a
// command goes in, an envelope comes out, and the server, the CLI and half a
// dozen tests can all see it. The click path has none — it reads config, asks
// the bridge who it is, and puts a string on the system clipboard. If it copies
// the WRONG string, or copies nothing while flashing a ✓, there is no envelope
// anywhere that says so; the operator finds out when a later command drives
// someone else's tab. So the assertions here are about what LANDED on the
// clipboard and, just as much, about what did NOT.
//
// The mock is deliberately strict in three places, each pinning a mistake that
// would otherwise pass:
//   * `execCommand` failure is reported by RETURN VALUE, so "the call completed"
//     is not success — the offscreen reply carries {ok:false} and must be fatal.
//   * /whoami is a real fetch in production; here it is a stub whose response is
//     switched per test, including the unreachable and 401 cases.
//   * the offscreen document is created and CLOSED around each copy, and the
//     test records both so a regression that leaves it open is visible.

import test from "node:test";
import assert from "node:assert/strict";

const TAB_ID = 4242;

const state = {
  storage: { port: 8788, token: "t0ken", label: "main", instanceId: "auto-id-uuid" },
  tab: { id: TAB_ID, url: "https://example.test/x", title: "X", active: true },
  tabs: null,                        // null → query returns [state.tab]
  whoami: { status: 200, body: { ok: true, host: { label: "workbench" } } },
  whoamiThrows: false,
  clipboardReply: { ok: true },
  offscreenExists: false,
  offscreenCreateThrows: null,
  calls: { fetch: [], created: [], closed: [], messages: [], badge: [], title: [] },
};

function reset() {
  state.storage = { port: 8788, token: "t0ken", label: "main", instanceId: "auto-id-uuid" };
  state.tab = { id: TAB_ID, url: "https://example.test/x", title: "X", active: true };
  state.tabs = null;
  state.whoami = { status: 200, body: { ok: true, host: { label: "workbench" } } };
  state.whoamiThrows = false;
  state.clipboardReply = { ok: true };
  state.offscreenExists = false;
  state.offscreenCreateThrows = null;
  state.calls = { fetch: [], created: [], closed: [], messages: [], badge: [], title: [] };
}

globalThis.BROWSER_BRIDGE_NO_AUTOSTART = true;
globalThis.chrome = {
  storage: { local: {
    async get(keys) {
      const out = {};
      for (const k of [].concat(keys)) {
        if (state.storage[k] !== undefined) out[k] = state.storage[k];
      }
      return out;
    },
    async set() {},
  } },
  tabs: {
    async query() { return state.tabs === null ? [state.tab] : state.tabs; },
    async get(id) { return { ...state.tab, id }; },
  },
  action: {
    async setBadgeText(o) { state.calls.badge.push(o.text); },
    async setBadgeBackgroundColor() {},
    async setTitle(o) { state.calls.title.push(o.title); },
    onClicked: { addListener() {} },
  },
  offscreen: {
    async hasDocument() { return state.offscreenExists; },
    async createDocument(opts) {
      if (state.offscreenCreateThrows) throw new Error(state.offscreenCreateThrows);
      state.calls.created.push(opts);
      state.offscreenExists = true;
    },
    async closeDocument() { state.calls.closed.push(true); state.offscreenExists = false; },
  },
  runtime: {
    async sendMessage(msg) { state.calls.messages.push(msg); return state.clipboardReply; },
    getManifest() { return { version: "0.0.0.0" }; },
    id: "mockextensionid",
    onInstalled: { addListener() {} },
    onStartup: { addListener() {} },
  },
  alarms: { create() {}, onAlarm: { addListener() {} } },
  debugger: { onDetach: { addListener() {} } },
  webNavigation: { async getAllFrames() { return []; } },
  scripting: { async executeScript() { return [{ result: null }]; } },
};

globalThis.fetch = async (url, init) => {
  state.calls.fetch.push({ url, init });
  if (state.whoamiThrows) throw new TypeError("Failed to fetch");
  const { status, body } = state.whoami;
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; },
  };
};

const { handleActionClick } = await import("../extension/service_worker.js");

// The one string the whole feature exists to produce. Same literal as
// tests/tab_ref.test.mjs and tests/test_browser_tab_ref.py.
function copied() {
  const m = state.calls.messages[state.calls.messages.length - 1];
  return m && m.text;
}

// --------------------------------------------------------------------------- //
test("a click copies bw://<host>/<label>/<tabId> and badges a tick", async () => {
  reset();
  const out = await handleActionClick();
  assert.deepEqual(out, { ok: true, ref: `bw://workbench/main/${TAB_ID}` });
  assert.equal(copied(), `bw://workbench/main/${TAB_ID}`);
  assert.equal(state.calls.badge[0], "✓");
  assert.match(state.calls.title[0], /^Copied bw:\/\/workbench\/main\/4242$/);
});

test("the host comes from /whoami on EVERY click — it is never assumed or cached", async () => {
  reset();
  await handleActionClick();
  assert.equal(state.calls.fetch.length, 1);
  assert.match(state.calls.fetch[0].url, /^http:\/\/127\.0\.0\.1:8788\/whoami$/);
  assert.equal(state.calls.fetch[0].init.headers.Authorization, "Bearer t0ken");
  // A SECOND click must ask again: a cached label survives the profile being
  // carried to the other host, which is the one thing the host field catches.
  state.whoami = { status: 200, body: { ok: true, host: { label: "laptop" } } };
  const out = await handleActionClick();
  assert.equal(state.calls.fetch.length, 2);
  assert.equal(out.ref, `bw://laptop/main/${TAB_ID}`);
});

test("an UNLABELLED profile copies the full auto-id", async () => {
  reset();
  state.storage.label = "";
  const out = await handleActionClick();
  assert.equal(out.ref, `bw://workbench/auto-id-uuid/${TAB_ID}`);
});

test("the offscreen document is created for the copy and CLOSED again", async () => {
  reset();
  await handleActionClick();
  assert.equal(state.calls.created.length, 1);
  assert.deepEqual(state.calls.created[0].reasons, ["CLIPBOARD"]);
  assert.equal(state.calls.created[0].url, "offscreen.html");
  assert.equal(state.calls.closed.length, 1, "it must not be left alive after the copy");
  assert.equal(state.offscreenExists, false);
});

test("the copy message is namespaced so another context cannot answer for it", async () => {
  reset();
  await handleActionClick();
  const m = state.calls.messages[0];
  assert.equal(m.target, "offscreen-clipboard");
  assert.equal(m.type, "copy");
});

test("a pre-existing offscreen document is reused, not re-created", async () => {
  reset();
  state.offscreenExists = true;
  const out = await handleActionClick();
  assert.equal(out.ok, true);
  assert.equal(state.calls.created.length, 0);
});

test("losing the create RACE is not a failure — the document exists either way", async () => {
  reset();
  state.offscreenCreateThrows = "Only a single offscreen document may be created.";
  const out = await handleActionClick();
  assert.equal(out.ok, true, "the other click already created it; copy anyway");
  assert.equal(copied(), `bw://workbench/main/${TAB_ID}`);
});

test("a create failure that is NOT the race is fatal and copies nothing", async () => {
  reset();
  state.offscreenCreateThrows = "boom";
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.equal(state.calls.messages.length, 0);
  assert.equal(state.calls.badge[0], "✗");
});

// --- the refusals: nothing is copied, and the badge says so ----------------- //
test("a REFUSED clipboard write is a failure, not a completed call", async () => {
  reset();
  // execCommand("copy") returns false rather than throwing — the exact reason a
  // "the call returned" test would go green over an empty clipboard.
  state.clipboardReply = { ok: false, error: "execCommand refused" };
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /clipboard write refused: execCommand refused/);
  assert.equal(state.calls.badge[0], "✗");
});

test("an ABSENT reply is a failure too", async () => {
  reset();
  state.clipboardReply = undefined;
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /clipboard write refused/);
});

test("no active tab → a named refusal, no copy", async () => {
  reset();
  state.tabs = [];
  const out = await handleActionClick();
  assert.deepEqual(out, { ok: false, error: "no active tab to reference" });
  assert.equal(state.calls.messages.length, 0);
  assert.equal(state.calls.badge[0], "✗");
});

test("bridge unreachable → a named refusal, no copy", async () => {
  reset();
  state.whoamiThrows = true;
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /bridge not reachable/);
  assert.equal(state.calls.messages.length, 0);
});

test("a rejected token says to re-paste it, rather than reporting a generic HTTP error", async () => {
  reset();
  state.whoami = { status: 401, body: {} };
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /re-paste it in Options/);
});

test("any other HTTP status names the status", async () => {
  reset();
  state.whoami = { status: 503, body: {} };
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /HTTP 503/);
});

test("a bridge that cannot identify its host copies NOTHING", async () => {
  reset();
  // 🔴 The CLI proceeds-with-a-warning on `unknown` because it has a live bridge
  // to compare against and refusing there would strand a host with no
  // ACTIVITY_HOST. The EXTENSION has the opposite duty: a ref minted with an
  // unverifiable host is a token that will be pasted somewhere else later, and
  // there is no second chance to check it. It must not be minted at all.
  state.whoami = { status: 200, body: { ok: true, host: { label: "unknown" } } };
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /could not identify this host/);
  assert.equal(state.calls.messages.length, 0);
});

test("an unsafe LABEL is refused by name rather than mangled into a ref", async () => {
  reset();
  state.storage.label = "my profile";
  const out = await handleActionClick();
  assert.equal(out.ok, false);
  assert.match(out.error, /instance label is not ref-safe: my profile/);
  assert.equal(state.calls.messages.length, 0);
});

test("handleActionClick NEVER throws — it is an event-listener body", async () => {
  reset();
  // Make the very first await fail in a way nothing catches specifically.
  const realGet = chrome.storage.local.get;
  chrome.storage.local.get = async () => { throw new Error("storage exploded"); };
  try {
    const out = await handleActionClick();
    assert.equal(out.ok, false);
    assert.match(out.error, /storage exploded/);
  } finally {
    chrome.storage.local.get = realGet;
  }
});
