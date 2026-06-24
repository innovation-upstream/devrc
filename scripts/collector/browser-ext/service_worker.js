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
// the "current active tab + since-timestamp" in chrome.storage.session so an
// active-duration can still be computed across a worker restart.

const ENDPOINT = "http://127.0.0.1:8787/event";
const STORE_KEY = "active_state";

// --- pure payload builder (kept tiny + mirrored by the receiver test) ----- //
export function buildEvent(kind, { url, title, active_ms, state }) {
  return {
    kind, // "nav" | "focus"
    url: url || "",
    title: title || "",
    active_ms: Number.isFinite(active_ms) ? Math.max(0, Math.round(active_ms)) : 0,
    state: state || "", // for focus events: focused|blurred|idle|active|locked
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

async function getState() {
  const o = await chrome.storage.session.get(STORE_KEY);
  return o[STORE_KEY] || { tabId: null, url: "", title: "", since: Date.now() };
}

async function setState(s) {
  await chrome.storage.session.set({ [STORE_KEY]: s });
}

// Compute active_ms for the outgoing (previous) tab, emit a nav event for the
// NEW active tab, and record the new active state.
async function onActive({ url, title, tabId }) {
  const prev = await getState();
  const now = Date.now();
  const active_ms = prev.since ? now - prev.since : 0;
  await post(buildEvent("nav", { url, title, active_ms }));
  await setState({ tabId, url, title, since: now });
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
  await onActive(await tabInfo(tabId));
});

// Navigation within the active tab (committed top-frame loads).
chrome.webNavigation.onCommitted.addListener(async (d) => {
  if (d.frameId !== 0) return; // top frame only
  const info = await tabInfo(d.tabId);
  const cur = await getState();
  if (cur.tabId !== null && cur.tabId !== d.tabId) return; // not the active tab
  await onActive(info);
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

// Window focus changes (browser gained/lost OS focus).
chrome.windows.onFocusChanged.addListener(async (windowId) => {
  const focused = windowId !== chrome.windows.WINDOW_ID_NONE;
  await post(buildEvent("focus", { state: focused ? "focused" : "blurred" }));
});

// Idle / active / locked transitions.
chrome.idle.setDetectionInterval(60);
chrome.idle.onStateChanged.addListener(async (state) => {
  await post(buildEvent("focus", { state }));
});
