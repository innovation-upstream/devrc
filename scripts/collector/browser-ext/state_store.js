// state_store.js — serialized (race-free) state store for the browser collector.
//
// Why this exists: every event handler in service_worker.js used to do a
// NON-ATOMIC read-modify-write of the active-time accumulator persisted in
// chrome.storage.session:
//
//     const cur = await getState();   // read  (await point)
//     ...mutate cur.accum...
//     await setState(cur);            // write (await point)
//
// chrome.tabs.onActivated, webNavigation.onCommitted, tabs.onUpdated,
// windows.onFocusChanged and idle.onStateChanged all fire concurrently and
// interleave at those await points. Two handlers read the SAME `since`, both
// mutate, then both write — the second setState() clobbers the first's mutation.
// Observed in production: lost idle/blur pauses (active span runs unbroken to
// the 1h cap) and double-counted tab switches (onActivated + onCommitted for the
// same switch each take() the full span before either resets → duplicate nav
// events each carrying the full active_ms).
//
// Fix: a promise-chain async mutex so every read→mutate→write on the
// `active_state` key runs to completion before the next one starts (within the
// worker lifetime). The accumulator accounting itself lives in the PURE
// active_time.js and is unchanged.
//
// This module takes its dependencies (an async storage backend matching the
// chrome.storage.session get/set shape, a `post` fn, and the active_time module)
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

// Build a serialized state store over an injected storage backend.
//   storage: { get(key) -> Promise<obj>, set(obj) -> Promise<void> } matching the
//            chrome.storage.session shape (get returns { [key]: value }).
//   post:    async fn(event) -> emits an event.
//   AT:      the active_time module (freshState/fromStored/take/onActive/...).
//   buildEvent: pure payload builder.
//   now:     () => epoch ms (injectable for deterministic tests).
export function createStateStore({ storage, post, AT, buildEvent, now = () => Date.now() }) {
  const STORE_KEY = "active_state";
  const SCROLL_KEY = "scroll_by_tab";
  const lockState = makeLock();
  const lockScroll = makeLock();

  async function getState() {
    const o = await storage.get(STORE_KEY);
    const s = o[STORE_KEY];
    if (!s) {
      return { tabId: null, url: "", title: "", accum: AT.freshState(now(), true) };
    }
    s.accum = AT.fromStored(s.accum);
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

  // Apply an accumulator transition (onActive/onBlur/onIdle) to the persisted
  // state in one ATOMIC read-modify-write (serialized against onTabChange and
  // every other state mutation).
  async function applyAccum(fn, at) {
    return lockState(async () => {
      const cur = await getState();
      fn(cur.accum, at);
      await setState(cur);
    });
  }

  // A tab change / navigation. Serialized: bank the OUTGOING tab's active time,
  // emit a nav event carrying that (capped, idle/blur-excluded) active_ms, then
  // reset the accumulator for the new tab — all atomic w.r.t. concurrent
  // applyAccum / onTabChange. Because the second of two concurrent onTabChange
  // calls now runs AFTER the first has take()-reset since=now, it returns ~0
  // instead of a duplicate full span.
  //
  // Redundant-nav suppression: if the incoming {tabId,url} equals the CURRENT
  // stored {tabId,url}, nothing actually changed (e.g. onActivated AND
  // onCommitted both fired for the same destination switch) — bank the time but
  // do NOT emit a second nav event. A same-tab DIFFERENT-url load is a real
  // in-tab navigation and STILL emits.
  async function onTabChange({ url, title, tabId }) {
    // Pop the leaving tab's scroll metrics OUTSIDE the state lock (independent
    // lock); capture prev.tabId first inside the lock to know which tab leaves.
    return lockState(async () => {
      const prev = await getState();
      const at = now();
      const active_ms = AT.take(prev.accum, at);
      const redundant = prev.tabId === tabId && prev.url === url;
      if (redundant) {
        // Nothing changed — keep accruing for the SAME tab, no duplicate nav.
        await setState({ tabId, url: prev.url, title: prev.title || title, accum: prev.accum });
        return { emitted: false, active_ms };
      }
      const { scroll_pct, scroll_ms } = await takeScroll(prev.tabId);
      await post(buildEvent("nav", { url, title, active_ms, scroll_pct, scroll_ms }));
      await setState({ tabId, url, title, accum: prev.accum });
      return { emitted: true, active_ms };
    });
  }

  // Title can arrive after commit; refresh stored title without a duration reset.
  // Serialized so it doesn't clobber a concurrent accumulator mutation.
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
    applyAccum,
    updateTitle,
    putScroll,
    takeScroll,
    peekState,
    // exposed for tests
    _getState: getState,
    _setState: setState,
  };
}
