// state_store.js — serialized (race-free) tab + scroll store for the browser
// collector.
//
// Why this exists: the chrome event handlers in service_worker.js each do a
// NON-ATOMIC read-modify-write of state persisted in chrome.storage.session:
//
//     const cur = await getState();   // read  (await point)
//     ...mutate cur...
//     await setState(cur);            // write (await point)
//
// chrome.tabs.onActivated, webNavigation.onCommitted and tabs.onUpdated all fire
// concurrently and interleave at those await points. Two handlers read the SAME
// state, both mutate, then both write — the second setState() clobbers the
// first's mutation. Without a lock, a double-fired switch (onActivated AND
// onCommitted for one destination) would each emit a nav event, and the scroll
// map's read-delete-write would lose a concurrent put for a different tab.
//
// Fix: a promise-chain async mutex so every read→mutate→write on the tab state
// runs to completion before the next one starts (within the worker lifetime),
// plus a SEPARATE mutex serializing the scroll map. This also makes the
// redundant-nav suppression (same {tabId,url} → no duplicate nav) reliable by
// guaranteeing consistent reads.
//
// (The per-page `active_ms` accumulator this store used to integrate has been
// RETIRED — see service_worker.js. The store is now a plain serialized tab+scroll
// store: no active-time accounting, no `accum` field, no focus/idle handling.)
//
// This module takes its dependencies (an async storage backend matching the
// chrome.storage.session get/set shape, a `post` fn, and the buildEvent builder)
// by injection so the concurrency-critical logic is unit-testable WITHOUT a real
// Chrome — see tests/state_store.test.mjs.

// --- generic async mutex (promise chain) ----------------------------------- //
// Each scheduled `fn` runs only after the previous one settles. We attach `fn`
// as BOTH the fulfil and reject handler (`.then(fn, fn)`) so a prior rejection
// does not skip this run, and keep the chain alive on throw by swallowing the
// settle of the tail.
export function makeLock() {
  let chain = Promise.resolve();
  return function withLock(fn) {
    const run = chain.then(fn, fn);
    chain = run.then(() => {}, () => {});
    return run;
  };
}

// Build a serialized tab + scroll store over an injected storage backend.
//   storage: { get(key) -> Promise<obj>, set(obj) -> Promise<void> } matching the
//            chrome.storage.session shape (get returns { [key]: value }).
//   post:    async fn(event) -> emits an event.
//   buildEvent: pure payload builder.
//   now:     () => epoch ms (injectable for deterministic tests).
export function createStateStore({ storage, post, buildEvent, now = () => Date.now() }) {
  const STORE_KEY = "active_state";
  const SCROLL_KEY = "scroll_by_tab";
  const lockState = makeLock();
  const lockScroll = makeLock();

  async function getState() {
    const o = await storage.get(STORE_KEY);
    const s = o[STORE_KEY];
    if (!s) {
      return { tabId: null, url: "", title: "" };
    }
    return s;
  }

  async function setState(s) {
    await storage.set({ [STORE_KEY]: s });
  }

  // --- per-tab scroll engagement (separate, independent lock) --------------- //
  async function getScrollMap() {
    try {
      const o = await storage.get(SCROLL_KEY);
      const m = o[SCROLL_KEY];
      return m && typeof m === "object" ? m : {};
    } catch {
      return {};
    }
  }

  // Store the latest running (monotonic) snapshot for a tab. Serialized so a
  // concurrent take/put cannot clobber the map blob.
  async function putScroll(tabId, scroll_pct, scroll_ms) {
    if (!Number.isFinite(tabId)) return;
    return lockScroll(async () => {
      const m = await getScrollMap();
      m[tabId] = {
        scroll_pct: Number.isFinite(scroll_pct) ? Math.max(0, Math.min(100, Math.round(scroll_pct))) : 0,
        scroll_ms: Number.isFinite(scroll_ms) ? Math.max(0, Math.round(scroll_ms)) : 0,
      };
      await storage.set({ [SCROLL_KEY]: m });
    });
  }

  // Pop (read + clear) a tab's scroll metrics. Serialized so the read-delete-
  // write cannot lose a concurrent putScroll for a DIFFERENT tab.
  async function takeScroll(tabId) {
    return lockScroll(async () => {
      const m = await getScrollMap();
      const v = (Number.isFinite(tabId) && m[tabId]) || { scroll_pct: 0, scroll_ms: 0 };
      if (Number.isFinite(tabId) && tabId in m) {
        delete m[tabId];
        await storage.set({ [SCROLL_KEY]: m });
      }
      return { scroll_pct: v.scroll_pct || 0, scroll_ms: v.scroll_ms || 0 };
    });
  }

  // A tab change / navigation. Serialized: emit a nav event for the new tab
  // carrying the LEAVING tab's scroll metrics, then record the new tab as
  // current — all atomic w.r.t. concurrent onTabChange.
  //
  // Redundant-nav suppression: if the incoming {tabId,url} equals the CURRENT
  // stored {tabId,url}, nothing actually changed (e.g. onActivated AND
  // onCommitted both fired for the same destination switch) — do NOT emit a
  // second nav event. A same-tab DIFFERENT-url load is a real in-tab navigation
  // and STILL emits.
  async function onTabChange({ url, title, tabId }) {
    return lockState(async () => {
      const prev = await getState();
      const redundant = prev.tabId === tabId && prev.url === url;
      if (redundant) {
        // Nothing changed — refresh the title only, no duplicate nav.
        await setState({ tabId, url: prev.url, title: prev.title || title });
        return { emitted: false };
      }
      const { scroll_pct, scroll_ms } = await takeScroll(prev.tabId);
      await post(buildEvent("nav", { url, title, scroll_pct, scroll_ms }));
      await setState({ tabId, url, title });
      return { emitted: true };
    });
  }

  // Title can arrive after commit; refresh stored title without re-emitting.
  // Serialized so it doesn't clobber a concurrent state mutation.
  async function updateTitle(tabId, title) {
    return lockState(async () => {
      const cur = await getState();
      if (cur.tabId === tabId) {
        cur.title = title;
        await setState(cur);
      }
    });
  }

  // Read the current stored state WITHOUT mutating — for the onCommitted
  // "is this the active tab?" guard. Not itself a critical section.
  async function peekState() {
    return getState();
  }

  return {
    onTabChange,
    updateTitle,
    putScroll,
    takeScroll,
    peekState,
    // exposed for tests
    _getState: getState,
    _setState: setState,
  };
}
