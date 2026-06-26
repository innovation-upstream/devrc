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

import * as AT from "./active_time.js";

const ENDPOINT = "http://127.0.0.1:8787/event";
const STORE_KEY = "active_state";
// Per-tab scroll engagement, keyed by tab id, persisted across worker
// suspension. Shape: { [tabId]: { scroll_pct, scroll_ms } }. Reported by the
// content_scroll.js content script and folded into the tab's nav event on leave.
const SCROLL_KEY = "scroll_by_tab";

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

// Stored shape: { tabId, url, title, accum } where `accum` is the pure
// active_time accumulator state ({ banked, since }). A fresh worker assumes the
// browser is active (it just ran an event) — the next idle/blur will correct.
async function getState() {
  const o = await chrome.storage.session.get(STORE_KEY);
  const s = o[STORE_KEY];
  if (!s) {
    return { tabId: null, url: "", title: "", accum: AT.freshState(Date.now(), true) };
  }
  s.accum = AT.fromStored(s.accum);
  return s;
}

async function setState(s) {
  await chrome.storage.session.set({ [STORE_KEY]: s });
}

// --- per-tab scroll engagement (from content_scroll.js) -------------------- //
// Read/write the persisted {tabId: {scroll_pct, scroll_ms}} map. Always returns
// a plain object — never throws on an empty / missing / corrupt blob.
async function getScrollMap() {
  try {
    const o = await chrome.storage.session.get(SCROLL_KEY);
    const m = o[SCROLL_KEY];
    return m && typeof m === "object" ? m : {};
  } catch {
    return {};
  }
}

// Store the latest metrics for a tab. content_scroll reports the running
// (monotonic) snapshot, so last-writer-wins is correct.
async function putScroll(tabId, scroll_pct, scroll_ms) {
  if (!Number.isFinite(tabId)) return;
  const m = await getScrollMap();
  m[tabId] = {
    scroll_pct: Number.isFinite(scroll_pct) ? Math.max(0, Math.min(100, Math.round(scroll_pct))) : 0,
    scroll_ms: Number.isFinite(scroll_ms) ? Math.max(0, Math.round(scroll_ms)) : 0,
  };
  await chrome.storage.session.set({ [SCROLL_KEY]: m });
}

// Pop (read + clear) a tab's scroll metrics; defaults to zeros when absent.
async function takeScroll(tabId) {
  const m = await getScrollMap();
  const v = (Number.isFinite(tabId) && m[tabId]) || { scroll_pct: 0, scroll_ms: 0 };
  if (Number.isFinite(tabId) && tabId in m) {
    delete m[tabId];
    await chrome.storage.session.set({ [SCROLL_KEY]: m });
  }
  return { scroll_pct: v.scroll_pct || 0, scroll_ms: v.scroll_ms || 0 };
}

// Apply an accumulator transition (onActive/onBlur/onIdle) to the persisted
// state in one read-modify-write. Keeps the active-time bookkeeping in sync
// with focus/idle signals without emitting a nav event.
async function applyAccum(fn, now) {
  const cur = await getState();
  fn(cur.accum, now);
  await setState(cur);
}

// A tab change / navigation: bank the active time for the OUTGOING tab, emit a
// nav event carrying that (capped, idle/blur-excluded) active_ms, then reset
// the accumulator for the new tab. take() re-anchors the in-progress span to
// `now` if still active, so continuous engagement across a switch keeps
// accruing.
async function onTabChange({ url, title, tabId }) {
  const prev = await getState();
  const now = Date.now();
  const active_ms = AT.take(prev.accum, now);
  // Fold in (and clear) the LEAVING tab's reading-engagement metrics. Robust to
  // an empty map: a tab with no scroll data reports scroll_pct:0, scroll_ms:0.
  const { scroll_pct, scroll_ms } = await takeScroll(prev.tabId);
  await post(buildEvent("nav", { url, title, active_ms, scroll_pct, scroll_ms }));
  await setState({ tabId, url, title, accum: prev.accum });
}

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
  await onTabChange(await tabInfo(tabId));
});

// Scroll-engagement updates from content_scroll.js. Store the latest running
// {scroll_pct, scroll_ms} per sender tab; it's folded into that tab's nav event
// when the tab is left. Best-effort — never throws back into the page.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (!msg || msg.kind !== "scroll") return;
  const tabId = sender && sender.tab ? sender.tab.id : undefined;
  putScroll(tabId, msg.scroll_pct, msg.scroll_ms).catch(() => {});
  // No async response; return nothing so the channel closes immediately.
});

// A closed tab can never emit a nav event, so its stored scroll metrics would
// leak in the map forever — drop them on tab removal.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  await takeScroll(tabId);
});

// Navigation within the active tab (committed top-frame loads).
chrome.webNavigation.onCommitted.addListener(async (d) => {
  if (d.frameId !== 0) return; // top frame only
  const info = await tabInfo(d.tabId);
  const cur = await getState();
  if (cur.tabId !== null && cur.tabId !== d.tabId) return; // not the active tab
  await onTabChange(info);
});

// Title can arrive after commit; refresh stored title without a duration reset.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (!changeInfo.title) return;
  const cur = await getState();
  if (cur.tabId === tabId) {
    cur.title = changeInfo.title;
    await setState(cur);
  }
});

// Window focus changes (browser gained/lost OS focus). Blur PAUSES the active-
// time accumulator (banks the elapsed span); refocus RESUMES it.
chrome.windows.onFocusChanged.addListener(async (windowId) => {
  const focused = windowId !== chrome.windows.WINDOW_ID_NONE;
  const now = Date.now();
  await applyAccum(focused ? AT.onActive : AT.onBlur, now);
  await post(buildEvent("focus", { state: focused ? "focused" : "blurred" }));
});

// Idle / active / locked transitions. "active" resumes accrual; "idle"/"locked"
// pause it (banking the active span) so away-time isn't counted as engagement.
chrome.idle.setDetectionInterval(60);
chrome.idle.onStateChanged.addListener(async (state) => {
  const now = Date.now();
  await applyAccum(state === "active" ? AT.onActive : AT.onIdle, now);
  await post(buildEvent("focus", { state }));
});
