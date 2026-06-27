// Concurrency tests for state_store.js — the serialized (race-free) tab+scroll
// store.
//
// These drive the store with a FAKE chrome.storage.session (a plain in-memory
// object with async get/set) and a fake `post`, so the concurrency-critical
// read-modify-write logic is exercised WITHOUT a real Chrome.
//
// The key tests:
//   - redundant-nav suppression (a second identical {tabId,url} emits no nav;
//     a same-tab DIFFERENT-url still emits) — relies on the lock for consistent
//     reads.
//   - scroll-map serialization (a take for tab X must not lose a concurrent put
//     for tab Y).
//   - tab tracking (the leaving tab's scroll metrics fold into the nav event).
//   - the nav payload no longer carries the retired active_ms/state fields.
//
// (The per-page active_ms accumulator + focus/idle handling this store used to
// integrate have been RETIRED — see service_worker.js.)
//
// Run: nix-shell -p nodejs --run "node --test 'scripts/collector/browser-ext/tests/**/*.test.mjs'"
import test from "node:test";
import assert from "node:assert/strict";
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

// --- double-fired switch (onActivated+onCommitted) ------------------------- //
// Two concurrent onTabChange for the SAME destination (onActivated + onCommitted
// both firing for one switch). The lock + redundant-nav suppression mean the
// second runs after the first recorded the new tab and is suppressed.
test("double-fired switch (onActivated+onCommitted): exactly one nav", async () => {
  const storage = makeFakeStorage(2); // nonzero delay forces interleave
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A" } });
  clock = 30 * 60_000;

  // Same destination B fired twice, concurrently.
  const p1 = store.onTabChange({ tabId: 2, url: "b", title: "B" });
  const p2 = store.onTabChange({ tabId: 2, url: "b", title: "B" });
  await Promise.all([p1, p2]);

  const navs = posted.filter((e) => e.kind === "nav");
  // Only ONE nav emitted; the second identical {tabId,url} is suppressed.
  assert.equal(navs.length, 1, `expected 1 nav, got ${navs.length}`);
  assert.equal(navs[0].url, "b");
});

// --- the nav payload no longer carries retired fields ---------------------- //
test("nav event carries url/title/scroll but NOT active_ms/state", async () => {
  const storage = makeFakeStorage();
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A" } });
  await store.putScroll(1, 64, 3200);

  clock = 5000;
  await store.onTabChange({ tabId: 2, url: "b", title: "B" });
  const nav = posted.find((e) => e.kind === "nav");
  assert.ok(nav, "a nav event should have been emitted");
  assert.equal(nav.url, "b");
  assert.equal(nav.title, "B");
  assert.equal(nav.scroll_pct, 64);
  assert.equal(nav.scroll_ms, 3200);
  // Retired fields must be absent.
  assert.ok(!("active_ms" in nav), "nav must NOT carry active_ms");
  assert.ok(!("state" in nav), "nav must NOT carry state");
});

// --- redundant-nav suppression --------------------------------------------- //
test("redundant onTabChange (same tabId+url) emits no nav", async () => {
  const storage = makeFakeStorage();
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A" } });

  clock = 1000;
  const r = await store.onTabChange({ tabId: 1, url: "a", title: "A (refreshed title)" });
  assert.equal(r.emitted, false);
  assert.equal(posted.filter((e) => e.kind === "nav").length, 0);
});

test("same-tab DIFFERENT-url IS a real navigation and still emits", async () => {
  const storage = makeFakeStorage();
  let clock = 0;
  const { store, posted } = makeStore(storage, () => clock);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A" } });

  clock = 2000;
  const r = await store.onTabChange({ tabId: 1, url: "a2", title: "A2" });
  assert.equal(r.emitted, true);
  const navs = posted.filter((e) => e.kind === "nav");
  assert.equal(navs.length, 1);
  assert.equal(navs[0].url, "a2");
});

// --- tab tracking ---------------------------------------------------------- //
test("updateTitle refreshes the stored title for the current tab only", async () => {
  const storage = makeFakeStorage();
  const { store } = makeStore(storage, () => 0);
  await storage.set({ active_state: { tabId: 1, url: "a", title: "A" } });

  await store.updateTitle(2, "Wrong tab"); // not the current tab → ignored
  assert.equal((await store._getState()).title, "A");

  await store.updateTitle(1, "New Title"); // current tab → applied
  assert.equal((await store._getState()).title, "New Title");
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
  await storage.set({ active_state: { tabId: 7, url: "p", title: "P" } });
  await store.putScroll(7, 88, 12500);

  clock = 3000;
  await store.onTabChange({ tabId: 8, url: "q", title: "Q" });
  const nav = posted.find((e) => e.kind === "nav");
  assert.equal(nav.scroll_pct, 88);
  assert.equal(nav.scroll_ms, 12500);
});
