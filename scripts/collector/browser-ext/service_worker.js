// service_worker.js — MV3 background worker for the activity browser collector.
//
// Tracks the active tab (URL + title) and per-page scroll engagement, then POSTs
// a `nav` event to the localhost receiver (http://127.0.0.1:8787/event) on each
// tab change / navigation. The receiver bridges these into the existing
// activity-collector spool in the v1 emit format. FULL URL capture — consistent
// with the full-content self-instrumentation choice; nothing leaves localhost
// here (the daemon decides shipping).
//
// Event kinds:
//   nav — the active tab navigated / changed; carries url, title, scroll_pct,
//         scroll_ms (the scroll engagement on the page being LEFT).
//
// RETIRED: per-page `active_ms` (active-engagement time) and `focus`/idle events.
// The extension's `active_ms` was STRUCTURALLY WRONG on the i3 WM — chrome.idle
// measures system-wide input and chrome.windows.onFocusChanged blur is unreliable
// on i3, so it counted time spent in OTHER apps as browser engagement. Browser
// attention is now DERIVED DOWNSTREAM by intersecting i3 "Brave-focused" intervals
// with the active-tab domain timeline (see the validation harness + the dashboard
// panel); the focus/idle wiring + accumulator that drove `active_ms` are gone.
//
// MV3 service workers are ephemeral (the browser may suspend them). We persist
// the "current active tab + per-tab scroll" in chrome.storage.session so tab
// tracking + scroll attribution survive a worker restart.
//
// All read-modify-write access to the persisted tab state + scroll map is
// SERIALIZED through an async mutex in state_store.js — the chrome event handlers
// fire concurrently and used to interleave at their await points and clobber each
// other's writes (duplicate nav events from a double-fired switch; lost scroll
// metrics from a racing read-delete-write). See state_store.js for the writeup.

import { createStateStore } from "./state_store.js";

const ENDPOINT = "http://127.0.0.1:8787/event";

// --- pure payload builder (kept tiny + mirrored by the receiver test) ----- //
export function buildEvent(kind, { url, title, scroll_pct, scroll_ms }) {
  return {
    kind, // "nav"
    url: url || "",
    title: title || "",
    scroll_pct: Number.isFinite(scroll_pct) ? Math.max(0, Math.min(100, Math.round(scroll_pct))) : 0,
    scroll_ms: Number.isFinite(scroll_ms) ? Math.max(0, Math.round(scroll_ms)) : 0,
    ts: Date.now(),
  };
}

async function post(event) {
  try {
    await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(event),
    });
  } catch (e) {
    // Receiver down (service not running) — drop silently; telemetry is
    // best-effort and must never disrupt browsing.
  }
}

// The serialized (race-free) tab + scroll store. Stored shape:
//   { tabId, url, title }  persisted in chrome.storage.session so tab tracking
// survives MV3 worker suspension, plus a separate per-tab scroll map. All
// reads/writes go through the mutex inside the store; the chrome.storage.session
// backend is injected so the store is unit-testable without a real browser.
const store = createStateStore({
  storage: chrome.storage.session,
  post,
  buildEvent,
  now: () => Date.now(),
});

async function tabInfo(tabId) {
  try {
    const t = await chrome.tabs.get(tabId);
    return { url: t.url || t.pendingUrl || "", title: t.title || "", tabId };
  } catch {
    return { url: "", title: "", tabId };
  }
}

// --- chrome event wiring --------------------------------------------------- //
chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  await store.onTabChange(await tabInfo(tabId));
});

// Scroll-engagement updates from content_scroll.js. Store the latest running
// {scroll_pct, scroll_ms} per sender tab; it's folded into that tab's nav event
// when the tab is left. Best-effort — never throws back into the page.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.kind !== "scroll") return;
  const tabId = sender && sender.tab ? sender.tab.id : undefined;
  store.putScroll(tabId, msg.scroll_pct, msg.scroll_ms).catch(() => {});
  // No async response; return nothing so the channel closes immediately.
});

// A closed tab can never emit a nav event, so its stored scroll metrics would
// leak in the map forever — drop them on tab removal.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  await store.takeScroll(tabId);
});

// Navigation within the active tab (committed top-frame loads).
chrome.webNavigation.onCommitted.addListener(async (d) => {
  if (d.frameId !== 0) return; // top frame only
  const info = await tabInfo(d.tabId);
  // Cheap pre-filter: skip if this isn't the active tab. The authoritative
  // (race-free) redundant-nav check happens inside store.onTabChange under the
  // lock — peekState here is just to avoid scheduling obviously-irrelevant work.
  const cur = await store.peekState();
  if (cur.tabId !== null && cur.tabId !== d.tabId) return; // not the active tab
  await store.onTabChange(info);
});

// Title can arrive after commit; refresh stored title without re-emitting a nav.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (!changeInfo.title) return;
  await store.updateTitle(tabId, changeInfo.title);
});
