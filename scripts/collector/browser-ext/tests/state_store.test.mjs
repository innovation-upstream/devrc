// Concurrency tests for state_store.js — the serialized (race-free) state store.
//
// These drive the store with a FAKE chrome.storage.session (a plain in-memory
// object with async get/set) and a fake `post`, so the concurrency-critical
// read-modify-write logic is exercised WITHOUT a real Chrome.
//
// The key tests reproduce the production bug and assert it's gone:
//   - no lost update under concurrency (an idle pause racing a tab switch is
//     banked, not clobbered; total emitted active_ms == the single real span,
//     NOT 2x it — i.e. no double-count).
//   - redundant-nav suppression (a second identical {tabId,url} emits no nav;
//     a same-tab DIFFERENT-url still emits).
//
// Run: nix-shell -p nodejs --run "node --test 'scripts/collector/browser-ext/tests/**/*.test.mjs'"
import test from "node:test";
import assert from "node:assert/strict";
import * as AT from "../active_time.js";
import { createStateStore, makeLock } from "../state_store.js";

// A fake chrome.storage.session: in-memory key/value with an awaitable get/set.
// `delay` lets us interleave two in-flight operations at their await points to
// reproduce the race a real chrome.storage.session exhibits.
function makeFakeStorage(delay = 0) {
  const data = {};
  const wait = () => (delay ? new Promise((r) => setTimeout(r, delay)) : Promise.resolve());
  return {
    data,
    async get(key) {
      await wait();
      // Return a deep-ish copy so callers mutating the returned object don't
      // alias our store — matches chrome.storage.session's structured-clone
      // semantics (the source of the lost-update race: each reader gets its own
      // copy and the last writer wins).
      return key in data ? { [key]: JSON.parse(JSON.stringify(data[key])) } : {};
    },
    async set(obj) {
      await wait();
      for (const k of Object.keys(obj)) data[k] = JSON.parse(JSON.stringify(obj[k]));
    },
  };
}

function makeStore(storage, now) {
  const posted = [];
  const store = createStateStore({
    storage,
    post: async (e) => { posted.push(e); },
    AT,
    buildEvent: (kind, p) => ({ kind, ...p }),
    now,
  });
  return { store, posted };
}

// --- the mutex itself ------------------------------------------------------ //
test("makeLock serializes async sections (no interleave)", async () => {
  const lock = makeLock();
  const order = [];
  const job = (id) => lock(async () => {
    order.push(`start${id}`);
    await new Promise((r) => setTimeout(r, 5));
    order.push(`end${id}`);
  });
  await Promise.all([job(1), job(2)]);
  // Without the lock the order would be start1,start2,end1,end2 (interleaved).
  assert.deepEqual(order, ["start1", "end1", "start2", "end2"]);
});

test("makeLock keeps the chain alive after a throw", async () => {
  const lock = makeLock();
  await assert.rejects(lock(async () => { throw new Error("boom"); }));
  // A subsequent job must still run despite the prior rejection.
  const r = await lock(async () => 42);
  assert.equal(r, 42);
});

// --- THE BUG: lost idle pause + double-count under concurrency ------------- //
// Scenario: tab is active and accruing. The user goes idle at the SAME instant
// the worker processes a tab switch — both handlers fire and interleave at
// their storage await points. Pre-fix, the second writer clobbered the first's
// mutation, so the idle pause was lost and BOTH take()s returned the full span.
test("idle racing a tab switch: pause is banked, NOT clobbered (no double-count)", async () => {
  const storage = makeFakeStorage(2); // nonzero delay forces interleave
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);

  // Seed an active tab (A) that has been focused for 10 min of real time.
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A", accum: AT.freshState(0, true) } });
  clock = 10 * 60_000; // 10 min later, two events fire "simultaneously":

  // (1) user goes idle -> should bank the 10-min span and PAUSE.
  // (2) tab switch A -> B -> should take() the active span as the nav active_ms.
  // Fire WITHOUT awaiting between them so they race.
  const pIdle = store.applyAccum(AT.onIdle, clock);
  const pSwitch = store.onTabChange({ tabId: 2, url: "b", title: "B" });
  const [, switchRes] = await Promise.all([pIdle, pSwitch]);

  // Exactly one nav event emitted (the A->B switch).
  const navs = posted.filter((e) => e.kind === "nav");
  assert.equal(navs.length, 1);

  // The total active_ms attributed to the switch must equal the SINGLE real
  // 10-min span — never 2x it. (Pre-fix this could be ~20 min: the idle write
  // and the switch write both saw since=0 and the clobber lost the reset.)
  const totalActive = navs.reduce((s, e) => s + e.active_ms, 0);
  assert.equal(totalActive, 10 * 60_000,
    `expected one 10-min span, got ${totalActive}ms across ${navs.length} nav(s)`);
  assert.ok(switchRes.emitted);

  // And whichever order they settled in, the accumulator must not have leaked a
  // second full span: advancing the clock with no further activity yields a
  // bounded, single-span take(), never a doubled value.
  clock += 60_000; // +1 min
  const after = await store.onTabChange({ tabId: 3, url: "c", title: "C" });
  assert.ok(after.active_ms <= 60_000 + 5,
    `follow-up active_ms ${after.active_ms} should be <= ~1 min, not a leaked span`);
});

// Two concurrent onTabChange for the SAME destination (onActivated + onCommitted
// both firing for one switch). Pre-fix: both take() the full span -> two nav
// events each carrying the full active_ms (the proven double-count). Post-fix:
// the second runs after the first reset since=now AND is suppressed as redundant.
test("double-fired switch (onActivated+onCommitted): no duplicate full-span nav", async () => {
  const storage = makeFakeStorage(2);
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A", accum: AT.freshState(0, true) } });
  clock = 30 * 60_000; // 30 min focused on A

  // Same destination B fired twice, concurrently.
  const p1 = store.onTabChange({ tabId: 2, url: "b", title: "B" });
  const p2 = store.onTabChange({ tabId: 2, url: "b", title: "B" });
  await Promise.all([p1, p2]);

  const navs = posted.filter((e) => e.kind === "nav");
  // Only ONE nav emitted; the second identical {tabId,url} is suppressed.
  assert.equal(navs.length, 1, `expected 1 nav, got ${navs.length}`);
  // And it carries the single real span, not 2x.
  assert.equal(navs[0].active_ms, 30 * 60_000);
});

// --- redundant-nav suppression --------------------------------------------- //
test("redundant onTabChange (same tabId+url) emits no nav", async () => {
  const storage = makeFakeStorage();
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A", accum: AT.freshState(0, true) } });

  clock = 1000;
  const r = await store.onTabChange({ tabId: 1, url: "a", title: "A (refreshed title)" });
  assert.equal(r.emitted, false);
  assert.equal(posted.filter((e) => e.kind === "nav").length, 0);
});

test("same-tab DIFFERENT-url IS a real navigation and still emits", async () => {
  const storage = makeFakeStorage();
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A", accum: AT.freshState(0, true) } });

  clock = 2000;
  const r = await store.onTabChange({ tabId: 1, url: "a2", title: "A2" });
  assert.equal(r.emitted, true);
  const navs = posted.filter((e) => e.kind === "nav");
  assert.equal(navs.length, 1);
  assert.equal(navs[0].url, "a2");
  assert.equal(navs[0].active_ms, 2000);
});

// --- scroll map serialization ---------------------------------------------- //
// takeScroll for tab X must not lose a concurrent putScroll for tab Y (the
// read-delete-write race on the shared map blob).
test("concurrent takeScroll(X) + putScroll(Y) does not lose Y", async () => {
  const storage = makeFakeStorage(2);
  const { store } = makeStore(storage, () => 0);
  await storage.set({ scroll_by_tab: { 1: { scroll_pct: 50, scroll_ms: 1000 } } });

  // Race: pop tab 1's metrics while writing tab 2's.
  const [taken] = await Promise.all([
    store.takeScroll(1),
    store.putScroll(2, 80, 2000),
  ]);
  assert.deepEqual(taken, { scroll_pct: 50, scroll_ms: 1000 });

  // Tab 2's write must have survived (not clobbered by takeScroll's map write).
  const remaining = await store.takeScroll(2);
  assert.deepEqual(remaining, { scroll_pct: 80, scroll_ms: 2000 });
});

// folds the leaving tab's scroll metrics into its nav event.
test("onTabChange folds the leaving tab's scroll metrics into the nav event", async () => {
  const storage = makeFakeStorage();
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 7, url: "p", title: "P", accum: AT.freshState(0, true) } });
  await store.putScroll(7, 88, 12500);

  clock = 3000;
  await store.onTabChange({ tabId: 8, url: "q", title: "Q" });
  const nav = posted.find((e) => e.kind === "nav");
  assert.equal(nav.scroll_pct, 88);
  assert.equal(nav.scroll_ms, 12500);
  assert.equal(nav.active_ms, 3000);
});
