// trigger.test.mjs -- MV3 cold-start + the snapshot triggers.
//
// This file deliberately does NOT set CLAUDE_USAGE_NO_AUTOSTART: it imports
// the worker exactly as a woken service worker arrives -- cold -- and pins
// that every listener (tabs.onUpdated, tabs.onActivated, runtime.onMessage,
// alarms.onAlarm) is registered SYNCHRONOUSLY in the first turn, before any
// await. A listener registered after an await misses the wake-up event; that
// is the defect class this file exists for.
//
// It also pins the trigger semantics on the registered handlers:
// claude.ai-only gating, the onUpdated/onActivated dedup window, the
// 15-minute alarm re-probe that only runs while a claude.ai tab exists -- and
// that the service worker NEVER fetches claude.ai itself.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";

const { chrome, calls, listeners } = makeChromeMock();
globalThis.chrome = chrome;

// The service worker must never fetch -- not even by accident. Any call
// lands here and the final test fails.
const fetchCalls = [];
globalThis.fetch = async (...args) => { fetchCalls.push(args); throw new Error("SW must not fetch"); };

const SW = await import("../extension/service_worker.js");

const CLAUDE_TAB = { id: 1, url: "https://claude.ai/new", status: "complete" };

test("cold-start: every listener is registered synchronously in the first turn", () => {
  assert.equal(typeof listeners["tabs.onUpdated"], "function",
    "tabs.onUpdated was not registered by import time");
  assert.equal(typeof listeners["tabs.onActivated"], "function",
    "tabs.onActivated was not registered by import time");
  assert.equal(typeof listeners["runtime.onMessage"], "function",
    "runtime.onMessage was not registered by import time");
  assert.equal(typeof listeners["alarms.onAlarm"], "function",
    "alarms.onAlarm was not registered by import time");
  // The re-probe alarm is created at registration, not on first use.
  assert.deepEqual(calls.alarmsCreated, [{ name: "cu-reprobe", periodInMinutes: 15 }]);
});

test("onUpdated(complete) on a claude.ai tab asks the tab's probe", () => {
  listeners["tabs.onUpdated"](1, { status: "complete" }, CLAUDE_TAB);
  assert.deepEqual(calls.tabMessages, [{ tabId: 1, msg: { type: "cu:probe" } }]);
});

test("non-claude.ai tabs never trigger", () => {
  calls.tabMessages.length = 0;
  listeners["tabs.onUpdated"](2, { status: "complete" }, { id: 2, url: "https://example.com/x" });
  listeners["tabs.onUpdated"](3, { status: "complete" }, { id: 3, url: "https://claude.ai.evil.test/" });
  assert.deepEqual(calls.tabMessages, []);
});

test("update events that are not 'complete' never trigger", () => {
  calls.tabMessages.length = 0;
  listeners["tabs.onUpdated"](1, { status: "loading" }, CLAUDE_TAB);
  listeners["tabs.onUpdated"](1, { title: "x" }, CLAUDE_TAB);
  assert.deepEqual(calls.tabMessages, []);
});

test("onUpdated + onActivated dedup within the ask window", () => {
  calls.tabMessages.length = 0;
  // The tab was just asked by the previous tests' events; a fresh
  // complete + activation burst inside the window asks nothing further.
  listeners["tabs.onUpdated"](1, { status: "complete" }, CLAUDE_TAB);
  chrome.tabs.get = async () => CLAUDE_TAB;
  listeners["tabs.onActivated"]({ tabId: 1, windowId: 9 });
  assert.deepEqual(calls.tabMessages, []);
  assert.deepEqual(SW.shouldProbeTab({ url: CLAUDE_TAB.url, status: "complete" },
    Date.now(), Date.now()), false, "pure gate agrees with the handler");
});

test("shouldProbeTab: claude.ai-only, complete-only, dedup window", () => {
  const now = 1_000_000_000_000;
  const ok = { url: "https://claude.ai/new", status: "complete" };
  assert.equal(SW.shouldProbeTab(ok, undefined, now), true);
  assert.equal(SW.shouldProbeTab(ok, now - 29_000, now), false);
  assert.equal(SW.shouldProbeTab(ok, now - 31_000, now), true, "the window does expire");
  assert.equal(SW.shouldProbeTab({ ...ok, url: "http://claude.ai/" }, undefined, now), false,
    "https only");
  assert.equal(SW.shouldProbeTab({ ...ok, url: "https://evil.test/claude.ai" }, undefined, now), false);
  assert.equal(SW.shouldProbeTab({ ...ok, status: "loading" }, undefined, now), false);
  assert.equal(SW.shouldProbeTab(null, undefined, now), false);
  assert.equal(SW.shouldProbeTab({ status: "complete" }, undefined, now), false);
});

test("the alarm re-probe runs only while a claude.ai tab exists", async () => {
  calls.tabMessages.length = 0;
  listeners["alarms.onAlarm"]({ name: "something-else" });
  listeners["alarms.onAlarm"]({ name: "cu-reprobe" });
  assert.deepEqual(calls.tabMessages, [], "no claude.ai tab -> no probe");

  chrome.tabs.query = async () => [{ id: 7, url: "https://claude.ai/new" }];
  listeners["alarms.onAlarm"]({ name: "cu-reprobe" });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(calls.tabMessages, [{ tabId: 7, msg: { type: "cu:probe" } }],
    "a claude.ai tab exists -> exactly the re-probe ask");
  // And a wrong-named alarm still does nothing.
  calls.tabMessages.length = 0;
  listeners["alarms.onAlarm"]({ name: "cu-reprobe-lookalike" });
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(calls.tabMessages, []);
});

test("onMessage keeps cold answers synchronous", () => {
  let answered = null;
  const keepOpen = SW.onMessage({ type: "cu:usage-report", results: [] }, {}, (r) => { answered = r; });
  assert.equal(keepOpen, false);
  assert.deepEqual(answered, { ok: true });
});

test("the service worker NEVER fetched anything", () => {
  assert.deepEqual(fetchCalls, [],
    "all usage fetches belong to the page-context content probe");
});
