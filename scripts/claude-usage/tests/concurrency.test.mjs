// concurrency.test.mjs -- two reports in flight at once.
//
// 🔴 OPERATOR-REPORTED, not hypothesised: opening claude.ai produced TWO
// identical system notifications. One page load genuinely produces two
// reports — content_probe.js auto-runs at `document_idle`, and independently
// the worker's `onTabUpdated` fires at status "complete" and asks the probe to
// run again — and `onMessage` dispatched them with `void handleReport(...)`,
// fire-and-forget, with no ordering.
//
// `handleReport` is a read-modify-write over chrome.storage: it awaits
// readState(), decides which toasts fall outside their dedup window, then
// awaits writeState(). Both invocations read the OLD `lastToast`, both find
// nothing recorded, both fire.
//
// 🔴 THE SEQUENTIAL CASE PASSES, AND THAT IS WHY THIS SHIPPED. Every existing
// test awaits one report before sending the next, which is the one ordering
// the bug cannot occur in. A test that only ever drives a handler serially
// cannot see a concurrency defect in it — measured: sequential 1 toast,
// concurrent 2, same inputs.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";

const { chrome, calls } = makeChromeMock();
globalThis.chrome = chrome;
globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;

const SW = await import("../extension/service_worker.js");
const { NAME_A, ORG_A, fullUsage } = await import("./fixtures.mjs");

const NOW = Date.parse("2026-09-19T13:00:00Z");

const report = (over = {}) => ({
  type: "cu:usage-report",
  fetchedAt: NOW,
  orgs: [{ uuid: ORG_A, name: NAME_A }],
  activeUuid: ORG_A,
  results: [{ orgUuid: ORG_A, ok: true, usage: fullUsage() }],
  ...over,
});

async function fresh() {
  calls.notifications.length = 0;
  await chrome.storage.local.set({ accounts: {}, lastActiveOrg: null, lastToast: {} });
}

test("🔴 two concurrent reports toast ONCE, not twice", async () => {
  await fresh();
  // Exactly how onMessage dispatches them: both started, neither awaited.
  await Promise.all([SW.enqueueReport(report()), SW.enqueueReport(report())]);
  await SW.reportsSettled();
  assert.equal(calls.notifications.length, 1,
    `${calls.notifications.length} notifications — the operator sees each one`);
});

test("the sequential case still toasts once (the control that always passed)", async () => {
  await fresh();
  await SW.enqueueReport(report());
  await SW.enqueueReport(report());
  await SW.reportsSettled();
  assert.equal(calls.notifications.length, 1);
});

test("a burst of five concurrent reports still toasts once", async () => {
  // The two-report case is what one page load produces today; a wake with a
  // queued alarm plus two tab events can produce more.
  await fresh();
  await Promise.all(Array.from({ length: 5 }, () => SW.enqueueReport(report())));
  await SW.reportsSettled();
  assert.equal(calls.notifications.length, 1);
});

test("🔴 concurrent reports do not LOSE an account write", async () => {
  // The silent half of the same window: each invocation builds `accounts`
  // from its own snapshot, so the later write erases what the earlier added.
  // Two DIFFERENT accounts reported concurrently must both survive.
  await fresh();
  const ORG_B = "22222222-2222-4222-8222-222222222222";
  await Promise.all([
    SW.enqueueReport(report()),
    SW.enqueueReport(report({
      orgs: [{ uuid: ORG_B, name: "Second Org" }],
      activeUuid: ORG_B,
      results: [{ orgUuid: ORG_B, ok: true, usage: fullUsage() }],
    })),
  ]);
  await SW.reportsSettled();
  const got = await chrome.storage.local.get(["accounts"]);
  assert.deepEqual(Object.keys(got.accounts).sort(), [ORG_A, ORG_B].sort(),
    "an account write was lost to the read-modify-write window");
});

test("one failing report does not poison the queue for every later one", async () => {
  // The `.catch` lives INSIDE the chain for this reason: a rejection left on
  // `reportQueue` would make every subsequent report a silent no-op, which is
  // strictly worse than the bug being fixed.
  await fresh();
  await SW.enqueueReport({ type: "cu:usage-report", fetchedAt: NOW, results: "not-an-array" });
  await SW.enqueueReport(report());
  await SW.reportsSettled();
  assert.equal(calls.notifications.length, 1, "a later report was dropped");
});

test("enqueueReport returns a promise that settles with the queue", async () => {
  await fresh();
  const p = SW.enqueueReport(report());
  assert.ok(p && typeof p.then === "function");
  await p;
  assert.equal(calls.notifications.length, 1);
});
