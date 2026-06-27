// service_worker.js — MV3 background worker for the activity browser collector.
//
// Tracks the active tab (URL + title), how long it stayed active, and
// focus/idle transitions, then POSTs each event to the localhost receiver
// (http://127.0.0.1:8787/event). The receiver bridges these into the existing
// activity-collector spool in the v1 emit format. FULL URL capture — consistent
// with the full-content self-instrumentation choice; nothing leaves localhost
// here (the daemon decides shipping).
//
// Event kinds:
//   nav   — the active tab navigated / changed; carries url, title, active_ms
//            (how long the PREVIOUS active tab was focused before this switch).
//   focus — window/idle focus transition (browser focused/blurred, idle/active).
//
// MV3 service workers are ephemeral (the browser may suspend them). We persist
// the "current active tab + active-time accumulator" in chrome.storage.session
// so active-duration accounting survives a worker restart.
//
// active_ms is now IDLE/BLUR-AWARE: the accumulator in active_time.js only
// accrues wall-clock while the browser is focused AND the user is not
// idle/locked, so a tab "focused" while the user walked away no longer logs
// that away-time as engagement. (Pure module — no chrome.* — unit-tested.)
//
// All read-modify-write access to the persisted accumulator + scroll map is
// SERIALIZED through an async mutex in state_store.js — the chrome event
// handlers fire concurrently and used to interleave at their await points and
// clobber each other's writes (lost idle/blur pauses → spans pinned at the 1h
// cap; double-counted tab switches). See state_store.js for the full writeup.

import * as AT from "./active_time.js";
import { createStateStore } from "./state_store.js";

const ENDPOINT = "http://127.0.0.1:8787/event";

// --- pure payload builder (kept tiny + mirrored by the receiver test) ----- //
export function buildEvent(kind, { url, title, active_ms, state, scroll_pct, scroll_ms }) {
  return {
    kind, // "nav" | "focus"
    url: url || "",
    title: title || "",
    active_ms: Number.isFinite(active_ms) ? Math.max(0, Math.round(active_ms)) : 0,
    state: state || "", // for focus events: focused|blurred|idle|active|locked
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

// The serialized (race-free) state store. Stored shape:
//   { tabId, url, title, accum }  where `accum` is the pure active_time
// accumulator ({ banked, since }), persisted in chrome.storage.session so the
// active-duration survives MV3 worker suspension. All reads/writes go through
// the mutex inside the store; the chrome.storage.session backend is injected so
// the store is unit-testable without a real browser.
const store = createStateStore({
  storage: chrome.storage.session,
  post,
  AT,
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

// Title can arrive after commit; refresh stored title without a duration reset.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (!changeInfo.title) return;
  await store.updateTitle(tabId, changeInfo.title);
});

// Window focus changes (browser gained/lost OS focus). Blur PAUSES the active-
// time accumulator (banks the elapsed span); refocus RESUMES it.
chrome.windows.onFocusChanged.addListener(async (windowId) => {
  const focused = windowId !== chrome.windows.WINDOW_ID_NONE;
  await store.applyAccum(focused ? AT.onActive : AT.onBlur, Date.now());
  await post(buildEvent("focus", { state: focused ? "focused" : "blurred" }));
});

// Idle / active / locked transitions. "active" resumes accrual; "idle"/"locked"
// pause it (banking the active span) so away-time isn't counted as engagement.
chrome.idle.setDetectionInterval(60);
chrome.idle.onStateChanged.addListener(async (state) => {
  await store.applyAccum(state === "active" ? AT.onActive : AT.onIdle, Date.now());
  await post(buildEvent("focus", { state }));
});
