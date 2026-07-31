// service_worker.js -- chrome.* glue for the media download router.
//
// All decision logic lives in route_core.js / sanitize.js (pure, node-tested).
// This file owns the browser side: config, the cached /dirs snapshot, the
// capture buffer, the downloads listeners, the toast/picker windows and the
// context menus.
//
// Profile scoping (decision D2): routing is OFF until `enabled` is ticked on
// this profile's options page. Extension storage is per-profile, so a profile
// where it was never enabled behaves exactly like stock Brave.
//
// MV3 lifetime: this worker is torn down after ~30s idle and restarted by the
// next event. Listener registration therefore has to happen in the FIRST TURN
// of the script -- see `registerListeners()` at the bottom -- and everything
// asynchronous hangs off `ready`, which the handlers await internally.
//
// Test hook: set `globalThis.DL_ROUTER_NO_AUTOSTART = true` before importing to
// suppress listener registration and networking.

import {
  buildMatchPayload, carryReferrer, correlateCapture, formatDup,
  handleDetermining, localContext, localDecide,
} from "./route_core.js";
import { isHttpUrl, relPathFromAbsolute, sanitizeDirName } from "./sanitize.js";
import { DEFAULT_PORT, manifestPort as readManifestPort } from "./port.js";

const CAPTURE_LIMIT = 40;
const SNAPSHOT_REFRESH_MINUTES = 5;
const TOAST_W = 420;
const TOAST_H = 190;
const PICKER_W = 460;
const PICKER_H = 420;

// How long the service worker waits for an injected overlay to prove it booted
// before giving up and opening the popup window instead. The content script's
// work is synchronous DOM building and the frame is a local extension page, so
// a healthy path answers in single-digit milliseconds; this is the budget for a
// page that is blocking the frame, not for a slow one.
const OVERLAY_READY_MS = 1500;

// How long onDeterminingFilename will wait for the cold-start config read
// before giving up and declining to route. A chrome.storage.local read is
// sub-millisecond; if it has not landed by now something is badly wrong and
// declining (Chrome's default filename) is the conservative answer.
const READY_TIMEOUT_MS = 250;

// Module-global so `onDeterminingFilename` can read it SYNCHRONOUSLY -- the
// listener has no time to await chrome.storage. It is repopulated on every
// service-worker start and by the refresh alarm.
export const state = {
  snapshot: null,       // last /dirs payload
  etag: null,
  captures: [],         // recent page-context captures (newest last)
  pending: new Map(),   // downloadId -> {dir, filename, decision, ts}
  pendingFetch: new Map(),  // fetchKey -> {url, payload, decision}
  config: { port: DEFAULT_PORT, token: "", enabled: false },
  configLoaded: false,  // has loadConfig() completed at least once?
  ownWindowIds: new Set(),  // popup windows WE created (toast/picker)
  overlays: new Map(),  // overlayId -> {tabId, downloadId} for in-page pickers
};

/**
 * Resolves once the worker has read its config and restored its snapshot.
 * Handlers registered synchronously await this instead of the module doing so.
 */
let readyPromise = null;
export function ready() {
  return readyPromise || Promise.resolve();
}

// --- config + transport ---------------------------------------------------- //
/** The sidecar port. See port.js -- the manifest is the only source of truth. */
export function manifestPort() {
  return readManifestPort(chrome);
}

export async function loadConfig() {
  const got = await chrome.storage.local.get(["token", "enabled"]);
  state.config = {
    port: manifestPort(),
    token: got.token || "",
    enabled: Boolean(got.enabled),
  };
  state.configLoaded = true;
  return state.config;
}

function base() {
  return `http://127.0.0.1:${state.config.port || DEFAULT_PORT}`;
}

/**
 * Turn a non-OK sidecar response into an Error that CARRIES ITS MESSAGE.
 *
 * This used to be `throw new Error(\`sidecar ${resp.status}\`)`, discarding the
 * body unread -- while the sidecar puts its explanation in `{"detail": ...}`
 * with a 400. So the careful "no routing decision is on record for this
 * download; either the sidecar was unreachable when it started..." text was
 * only ever reachable via `dl-route log` or curl, and the picker rendered
 * `Could not file into "Jane Doe": Error: sidecar 400`. Two whole findings
 * were about making that refusal honest and visible; neither was true at the
 * point the user actually reads it.
 */
async function sidecarError(resp) {
  let detail = "";
  try {
    const body = await resp.json();
    // Only a STRING is a message. Anything else stringifies to
    // "[object Object]", which is worse than the bare status line it replaces.
    for (const field of [body?.detail, body?.error]) {
      if (typeof field === "string" && field) { detail = field; break; }
    }
  } catch { /* not JSON, or already consumed -- fall back to the status */ }
  const err = new Error(
    detail ? `sidecar ${resp.status}: ${detail}` : `sidecar ${resp.status}`);
  err.status = resp.status;
  err.detail = detail;
  return err;
}

/**
 * The human-readable half of an error, for a toast or the picker.
 * Prefers the sidecar's own words over "Error: sidecar 400".
 */
export function errorMessage(err) {
  if (err && typeof err === "object") {
    if (err.detail) return String(err.detail);
    if (err.message) return String(err.message);
  }
  return String(err);
}

export async function api(method, path, body, { timeoutMs, headers } = {}) {
  const controller = new AbortController();
  const timer = timeoutMs
    ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const resp = await fetch(`${base()}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        Authorization: `Bearer ${state.config.token}`,
        "Content-Type": "application/json",
        ...(headers || {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (resp.status === 304) return { notModified: true };
    if (!resp.ok) throw await sidecarError(resp);
    return await resp.json();
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// --- snapshot -------------------------------------------------------------- //
/**
 * Fetch the /dirs snapshot.
 *
 * `revalidate` is what makes the picker's per-directory counts fresh.
 *
 * The sidecar deliberately leaves those counts OUT of the etag (see
 * App.dirs_snapshot): they change on every download and would turn a cache
 * validator for the ROUTING CONFIGURATION into a per-download counter. The
 * price of that choice is that a 304 carries no body, so a revalidating client
 * keeps whatever counts it already had -- and since the routing configuration
 * changes about once a month, they would be frozen for about a month.
 *
 * So the ONE request a human is waiting on -- the picker asking for the list it
 * is about to render -- skips If-None-Match and takes the full few-KB loopback
 * body. Everything else (the five-minute alarm, startup, the post-/mkdir
 * refresh) revalidates as before, so the steady-state traffic is unchanged.
 */
export async function refreshSnapshot({ revalidate = true } = {}) {
  const headers = (revalidate && state.etag)
    ? { "If-None-Match": state.etag } : undefined;
  const out = await api("GET", "/dirs", undefined, { timeoutMs: 4000, headers });
  if (out && out.notModified) return state.snapshot;
  state.snapshot = out;
  state.etag = out && out.etag ? `"${out.etag}"` : null;
  try {
    await chrome.storage.session.set({ snapshot: out });
  } catch {
    // storage.session is unavailable in some contexts; the in-memory copy is
    // the one that matters for the synchronous path.
  }
  return out;
}

async function restoreSnapshot() {
  try {
    const { snapshot } = await chrome.storage.session.get("snapshot");
    if (snapshot && !state.snapshot) state.snapshot = snapshot;
  } catch { /* best effort */ }
}

function knownDirs() {
  // Always a Set, even for a hostile or half-loaded snapshot: handleDetermining
  // fails closed on a non-Set, and a `.map` on a non-array would throw inside
  // the download listener.
  const dirs = state.snapshot?.dirs;
  if (!Array.isArray(dirs)) return new Set();
  return new Set(dirs.map((d) => d && d.name).filter(
    (n) => typeof n === "string" && n));
}

function otherDir() {
  return state.snapshot?.otherDir || "other";
}

/**
 * The library root, from the /dirs snapshot. Without it the extension cannot
 * prove a completed download landed inside the library, so every relocate is
 * refused rather than guessed (relPathFromAbsolute returns null on "").
 */
function libraryRoot() {
  const root = state.snapshot?.root;
  return typeof root === "string" ? root : "";
}

/**
 * The relative path to hand /relocate, or null when it cannot be proven.
 * Central so both correction paths (immediate and deferred-to-onChanged) get
 * the same containment check.
 */
function relPathFor(item) {
  if (!item || typeof item.filename !== "string") return null;
  return relPathFromAbsolute(item.filename, libraryRoot());
}

// --- capture buffer -------------------------------------------------------- //
export function recordCapture(payload, sender) {
  if (!payload || typeof payload !== "object") return;
  const capture = {
    href: typeof payload.href === "string" ? payload.href : "",
    mediaSrc: typeof payload.mediaSrc === "string" ? payload.mediaSrc : "",
    linkText: String(payload.linkText || "").slice(0, 300),
    alt: String(payload.alt || "").slice(0, 300),
    pageUrl: typeof payload.pageUrl === "string" ? payload.pageUrl : "",
    pageTitle: String(payload.pageTitle || "").slice(0, 300),
    site: String(payload.site || "").slice(0, 200),
    tags: Array.isArray(payload.tags)
      ? payload.tags.filter((t) => typeof t === "string").slice(0, 64) : [],
    og: payload.og && typeof payload.og === "object" ? payload.og : {},
    tabId: sender?.tab?.id,
    // The tab this one was opened from. It NARROWS the referrer carry's href
    // proof (see carryReferrer) -- it is not itself a proof, and the branch
    // that treated it as one was deleted. It comes free on the sender; the
    // content script has no way to know it.
    openerTabId: sender?.tab?.openerTabId,
    ts: Date.now(),
  };
  state.captures.push(capture);
  if (state.captures.length > CAPTURE_LIMIT) {
    state.captures.splice(0, state.captures.length - CAPTURE_LIMIT);
  }
}

// --- the download path ----------------------------------------------------- //
/**
 * The listener Chrome calls. Registered SYNCHRONOUSLY at module scope.
 *
 * The bug this shape exists to prevent: `start()` used to `await loadConfig()`
 * and `await restoreSnapshot()` BEFORE calling addListener. MV3 requires
 * registration in the first turn of the script, so after the ~30s idle
 * teardown a download that woke the worker could be dispatched before the
 * listener existed -- Chrome then used the default filename and the file
 * landed loose in the library root, unrouted and mixed in with the seeding
 * payloads. The listener is now always there; the readiness wait moved inside.
 *
 * Three cases:
 *   * config already read and routing disabled -> return false, stock Brave;
 *   * config already read and enabled          -> route synchronously;
 *   * config NOT read yet (a cold wake)        -> return true, wait for
 *     `ready()`, then route. If readiness does not land in READY_TIMEOUT_MS we
 *     cannot prove routing is even enabled for this profile, so we call
 *     `suggest()` with no argument, which tells Chrome to use its default.
 *     suggest() is still called EXACTLY ONCE on every one of these paths.
 */
export function onDeterminingFilename(item, suggest) {
  if (state.configLoaded) {
    if (!state.config.enabled) return false;  // profile opted out
    return routeDownload(item, suggest);
  }
  void (async () => {
    let settled = false;
    try {
      await Promise.race([
        ready(),
        new Promise((r) => setTimeout(r, READY_TIMEOUT_MS)),
      ]);
      settled = state.configLoaded;
    } catch { /* fall through to the conservative answer */ }
    if (!settled || !state.config.enabled) {
      suggest();   // no argument = Chrome's default filename
      return;
    }
    routeDownload(item, suggest);
  })();
  return true;
}

function routeDownload(item, suggest) {
  const { capture, tier } = correlateCapture(item, state.captures, {
    now: Date.now(),
    windowMs: (state.snapshot?.captureWindowS ?? 15) * 1000,
    activeTabId: state.activeTabId,
  });
  const payload = buildMatchPayload(
    item, capture, carryReferrer(item, capture, state.captures));

  return handleDetermining(item, suggest, {
    knownDirs: knownDirs(),
    otherDir: otherDir(),
    timeoutMs: state.snapshot?.matchTimeoutMs ?? 400,
    localDecision: () => localDecide(localContext(payload), state.snapshot),
    requestMatch: () => api("POST", "/match", payload,
      { timeoutMs: state.snapshot?.matchTimeoutMs ?? 400 }),
    onDecision: (info) => {
      state.pending.set(item.id, {
        dir: info.dir,
        filename: info.filename,
        decision: info.decision,
        payload,
        tier,
        // Where to put an in-page picker: the tab the click came from. It is
        // the capture's, not the DownloadItem's -- a DownloadItem carries no
        // tabId, which is the whole reason the capture buffer exists. Absent
        // when nothing correlated, and openOverlayPicker then falls back to the
        // active tab and finally to a window.
        tabId: capture?.tabId,
        ts: Date.now(),
      });
      // Fire-and-forget: the download is already answered.
      void afterDecision(item, info);
    },
  });
}

async function afterDecision(item, info) {
  const auto = info.auto;
  const reason = info.decision?.reason || "no match";
  const dup = formatDup(info.decision?.dup);
  if (auto) {
    await showToast({
      downloadId: item.id, dir: info.dir, reason, dup,
      source: info.source, filename: info.filename,
    });
  } else {
    await openPicker({
      downloadId: item.id, dir: info.dir, reason, dup,
      suggestNew: info.decision?.suggestNew || "",
      candidates: info.decision?.candidates || [],
    });
  }
}

// --- toast / picker windows ------------------------------------------------ //
/**
 * Remember a popup we created, so tab activation inside it is ignored.
 *
 * Creating a popup window ACTIVATES its tab, which fired chrome.tabs.onActivated
 * and clobbered `state.activeTabId` with the toast's own tab. After the very
 * first toast, tier-3 context correlation ("most recent capture from the active
 * tab") therefore matched nothing for the rest of the session and routing
 * silently degraded to the catch-all -- with no error anywhere, just steadily
 * worse matching.
 */
function rememberOwnWindow(win) {
  if (win && typeof win.id === "number") state.ownWindowIds.add(win.id);
}

export function onWindowRemoved(windowId) {
  state.ownWindowIds.delete(windowId);
}

/**
 * chrome.tabs.onActivated handler. Ignores our own popups.
 *
 * `windowId` (not the tab's URL) is the check because it is available
 * synchronously in the event itself -- no chrome.tabs.get round-trip that
 * could resolve after the next download has already been correlated.
 */
export function onTabActivated(info) {
  if (!info || typeof info.tabId !== "number") return;
  if (info.windowId !== undefined && state.ownWindowIds.has(info.windowId)) {
    return;
  }
  state.activeTabId = info.tabId;
}

function popupUrl(page, params) {
  const qs = new URLSearchParams(params).toString();
  return chrome.runtime.getURL(`${page}?${qs}`);
}

export async function showToast(info) {
  // A popup WINDOW, not an in-page overlay: injection fails on the PDF viewer,
  // chrome:// pages and sandboxed frames, which is exactly where a download
  // often starts.
  try {
    const win = await chrome.windows.create({
      url: popupUrl("toast.html", {
        id: String(info.downloadId ?? ""),
        dir: info.dir || "",
        reason: info.reason || "",
        dup: info.dup || "",
        source: info.source || "",
        ms: String(state.snapshot?.toastMs ?? 8000),
      }),
      type: "popup",
      width: TOAST_W,
      height: TOAST_H,
      focused: false,
    });
    rememberOwnWindow(win);
    return true;
  } catch {
    try {
      await chrome.notifications.create({
        type: "basic",
        iconUrl: chrome.runtime.getURL("icons/icon-48.png"),
        title: `Filed to ${info.dir}`,
        message: [info.reason, info.dup].filter(Boolean).join("\n"),
      });
    } catch { /* nothing left to try */ }
    return false;
  }
}

/** The query string both picker delivery paths are built from. ONE source. */
function pickerParams(info) {
  return {
    id: String(info.downloadId ?? ""),
    dir: info.dir || "",
    reason: info.reason || "",
    dup: info.dup || "",
    suggestNew: info.suggestNew || "",
  };
}

/**
 * The picker as a dedicated popup window. THE FALLBACK, and it stays.
 *
 * Content scripts do not run on `brave://`/`chrome://`, the PDF viewer, the Web
 * Store, `view-source:` or `file://`, and a download whose tab has already
 * closed -- the self-closing file-host tab -- has nothing to inject into at
 * all. Every one of those is a real case here, so this path is not legacy: it
 * is the answer whenever the page cannot host the overlay.
 */
export async function openPickerWindow(info) {
  try {
    const win = await chrome.windows.create({
      url: popupUrl("picker.html", pickerParams(info)),
      type: "popup",
      width: PICKER_W,
      height: PICKER_H,
      focused: true,
    });
    rememberOwnWindow(win);
    return true;
  } catch {
    return showToast({ ...info, source: "picker-failed" });
  }
}

/**
 * Cheap pre-check on a tab's URL. NOT the real test.
 *
 * It rejects the schemes a content script provably never sees, so the common
 * `brave://newtab` case costs no round-trip. It cannot cover the PDF viewer
 * (an ordinary https URL whose document is a plugin), a tab still loading, or a
 * discarded tab -- for those the probe below is the authority. Anything this
 * misses falls back correctly; anything it rejects wrongly only costs a window.
 */
export function overlayCapableUrl(url) {
  if (typeof url !== "string" || !/^https?:\/\//i.test(url)) return false;
  // The Web Store is blocked to extensions by the browser itself.
  if (/^https?:\/\/chromewebstore\.google\.com\//i.test(url)) return false;
  if (/^https?:\/\/chrome\.google\.com\/webstore(\/|$)/i.test(url)) return false;
  return true;
}

/** Which tab should host the overlay. */
function overlayTabId(info) {
  if (typeof info.tabId === "number") return info.tabId;
  const entry = state.pending.get(info.downloadId);
  if (entry && typeof entry.tabId === "number") return entry.tabId;
  // Last resort: wherever the user is actually looking. A download's own tab
  // often closes itself, and asking on the page in front of them beats a popup
  // window. `state.activeTabId` already excludes our own popups.
  return typeof state.activeTabId === "number" ? state.activeTabId : null;
}

function newOverlayId() {
  try {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return globalThis.crypto.randomUUID();
    }
  } catch { /* fall through */ }
  return `ov-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

// --- the overlay registry, and why it is PERSISTED -------------------------- //
//
// `state.overlays` is in-memory, and MV3 tears this worker down after ~30 s
// idle. The picker routinely outlives that: the user is CHOOSING, which is the
// slowest thing they do here. Everything that consults the registry on the far
// side of a teardown therefore has to read a durable copy first, or it reasons
// from an empty map:
//
//   * `dlr:choose` -> the anti-clickjack guard would refuse a LEGITIMATE pick
//     and discard it, precisely when the user had been deliberating longest;
//   * `tabs.onRemoved`/`onUpdated` -> the tab closing would find no overlay to
//     rescue, and the download would be left with no picker at all.
//
// `chrome.storage.session` has exactly the right lifetime: it survives the idle
// teardown and dies with the browser, which is also when every overlay dies.
// This is the same lesson as `Store.record_screened` -- a fact that must
// outlive a teardown does not live in service-worker memory.
async function persistOverlays() {
  try {
    await chrome.storage.session.set({ overlays: [...state.overlays] });
  } catch { /* storage.session unavailable; the in-memory copy still works */ }
}

// Overlays already being converted back into a window. Single-flight, and a
// TOMBSTONE: `persistOverlays` is fire-and-forget, so a restore that runs
// before that write lands would otherwise RESURRECT an overlay that has just
// been re-delivered -- and the user would get a second window for one download.
// Ids are per-open and the worker is torn down every ~30 s idle, so this set
// cannot grow unbounded.
const redelivering = new Set();

/** Merge the durable copy in. NEVER clobbers an overlay opened this turn. */
export async function restoreOverlays() {
  try {
    const { overlays } = await chrome.storage.session.get("overlays");
    if (!Array.isArray(overlays)) return;
    for (const entry of overlays) {
      if (!Array.isArray(entry) || typeof entry[0] !== "string") continue;
      if (!entry[1] || typeof entry[1] !== "object") continue;
      if (redelivering.has(entry[0])) continue;   // already asked in a window
      if (!state.overlays.has(entry[0])) state.overlays.set(entry[0], entry[1]);
    }
  } catch { /* best effort */ }
}

function rememberOverlay(id, record) {
  state.overlays.set(id, record);
  void persistOverlays();
}

function forgetOverlay(id) {
  const had = state.overlays.delete(id);
  if (had) void persistOverlays();
  return had;
}

/**
 * Is this one of OUR overlays? Consults the durable copy before saying no.
 *
 * The lazy restore is here rather than only in `start()` so the answer does not
 * depend on when readiness happened to resolve. Refusing a real pick is not a
 * recoverable error: the user's choice is gone.
 */
async function overlayIsOurs(id) {
  if (typeof id !== "string" || !id) return false;
  if (state.overlays.has(id)) return true;
  await restoreOverlays();
  return state.overlays.has(id);
}

// Resolvers for overlays whose `dlr:picker-ready` has not landed yet. Module
// scope, not `state`: purely in-flight, and the wait is under two seconds.
const overlayWaiters = new Map();

function waitForOverlayReady(id, ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      overlayWaiters.delete(id);
      resolve(false);
    }, ms);
    // `unref` exists only under node (the test runner); without it this timer
    // holds the event loop open and hangs the suite.
    if (timer && typeof timer.unref === "function") timer.unref();
    overlayWaiters.set(id, (ok) => {
      clearTimeout(timer);
      overlayWaiters.delete(id);
      resolve(ok !== false);
    });
  });
}

/** Abandon a readiness wait, so its timer does not outlive a failed open. */
function cancelOverlayWait(id) {
  const done = overlayWaiters.get(id);
  if (done) done(false);
}

async function tellOverlayToClose(tabId, id) {
  try {
    await chrome.tabs.sendMessage(tabId,
      { type: "dlr:close-overlay", overlay: id }, { frameId: 0 });
  } catch { /* tab gone or navigated -- the overlay went with the document */ }
}

/** The live overlay in a tab, if any. */
function overlayInTab(tabId) {
  for (const [id, record] of state.overlays) {
    if (record.tabId === tabId) return id;
  }
  return null;
}

/**
 * AN OVERLAY THAT STOPS EXISTING MUST BECOME A WINDOW.
 *
 * Gate 2 proves the frame booted. It proves nothing one millisecond later, and
 * an overlay is a node in a document the extension does not own: the tab can
 * close, navigate, or reload; the page can remove the node; and a second
 * download into the same tab evicts the first (the content script keeps exactly
 * one). In every one of those the picker vanishes, `openPicker` has already
 * returned true, and the download would be left with NO picker at all -- the
 * one outcome that is not allowed.
 *
 * So the worker keeps enough of the request (`info`) to re-ask, and re-asks in
 * a popup window, which nothing on the page can touch. Re-delivery is
 * idempotent: the record is removed first, so two triggers for the same overlay
 * produce one window.
 */
async function redeliverAsWindow(id) {
  // SINGLE-FLIGHT, CHECKED SYNCHRONOUSLY. onRemoved and onUpdated both fire for
  // a closing tab, and the page can report the loss at the same moment -- and
  // each of those now awaits `restoreOverlays()` first, so without this claim
  // all three could pass the "is there an overlay?" test before any of them
  // removed it. Two windows for one download is worse than none.
  if (redelivering.has(id)) return false;
  const record = state.overlays.get(id);
  if (!record) return false;
  redelivering.add(id);
  forgetOverlay(id);
  // Best effort: if anything IS still on screen, take it down first, so the
  // user never faces two pickers for one download.
  await tellOverlayToClose(record.tabId, id);
  return openPickerWindow(record.info);
}

/**
 * chrome.tabs.onRemoved: the tab holding the overlay is gone.
 *
 * `await restoreOverlays()` first, for the same reason the choose guard does:
 * this event is exactly the kind that WAKES a torn-down worker, and an empty
 * in-memory registry would find nothing to rescue and leave the download
 * unasked.
 */
export async function onTabRemoved(tabId) {
  await restoreOverlays();
  const id = overlayInTab(tabId);
  if (id) await redeliverAsWindow(id);
}

/**
 * chrome.tabs.onUpdated: the tab is navigating, so the overlay's document --
 * and the overlay with it -- is about to be destroyed.
 *
 * Keyed on `status === "loading"` and NOT on `changeInfo.url`: a single-page
 * app's pushState fires a url change without replacing the document, and
 * yanking the picker into a window every time a SPA changes route would be a
 * regression rather than a rescue.
 */
export async function onTabUpdated(tabId, changeInfo) {
  if (!changeInfo || changeInfo.status !== "loading") return;
  await restoreOverlays();
  const id = overlayInTab(tabId);
  if (id) await redeliverAsWindow(id);
}

/**
 * The picker as an in-page overlay. Returns false -- never throws -- whenever
 * the page cannot host it, so the caller falls back to the window.
 *
 * `chrome.action.openPopup()` is deliberately not an option here: the manifest
 * declares no `default_popup`, and an action popup dismisses on focus loss,
 * which would silently discard a pick and leave the file in the catch-all.
 *
 * TWO gates, and both are needed:
 *
 *   1. the content script answers `dlr:overlay-open`. `chrome.tabs.sendMessage`
 *      rejects with "receiving end does not exist" when there is no content
 *      script -- which is one check covering `brave://`, the PDF viewer, the
 *      Web Store, `view-source:`, `file://`, a closed or discarded tab, and a
 *      page that has not reached `document_idle` yet.
 *   2. the FRAME reports itself ready. A content-script-injected iframe is
 *      subject to the page's CSP, so a strict `frame-src` blocks the load while
 *      every DOM call in gate 1 still succeeds. Only an extension context that
 *      actually booted can send `dlr:picker-ready`.
 *
 * Without gate 2 a CSP-restrictive site would leave an empty overlay and no
 * window -- a download with no picker at all, which is the one outcome that is
 * not allowed.
 */
export async function openOverlayPicker(info) {
  const tabId = overlayTabId(info);
  if (typeof tabId !== "number") return false;
  let tab = null;
  try {
    tab = await chrome.tabs.get(tabId);
  } catch {
    return false;   // the tab has gone: the self-closing file-host tab
  }
  if (!tab || tab.discarded) return false;
  if (!overlayCapableUrl(tab.url || tab.pendingUrl)) return false;
  // ONE OVERLAY PER TAB, decided HERE rather than discovered later. The content
  // script keeps exactly one and evicts the incumbent, which for two downloads
  // racing into the same tab means the first one's picker disappears while the
  // worker still believes it was delivered. Refusing the second overlay sends
  // it to a window instead: two questions, both asked, neither lost.
  if (overlayInTab(tabId)) return false;

  const id = newOverlayId();
  const url = popupUrl("picker.html",
    { ...pickerParams(info), embed: "1", overlay: id });
  // ARM THE READINESS WAIT BEFORE THE PROBE. The content script returns as soon
  // as the DOM nodes exist; the frame's own boot is a SEPARATE round trip and
  // may land first. Registering the waiter after the send would drop a `ready`
  // that won the race and fall back to a popup window for no reason at all --
  // and, worse, intermittently.
  const ready = waitForOverlayReady(id, OVERLAY_READY_MS);
  let created = null;
  try {
    created = await chrome.tabs.sendMessage(tabId,
      { type: "dlr:overlay-open", id, url }, { frameId: 0 });
  } catch {
    cancelOverlayWait(id);
    return false;   // gate 1: no content script in this tab
  }
  if (!created || created.ok !== true) {
    cancelOverlayWait(id);
    return false;
  }

  // `info` is kept so the question can be RE-ASKED in a window if the overlay
  // stops existing -- see redeliverAsWindow.
  rememberOverlay(id, { tabId, downloadId: info.downloadId, info });
  if (!(await ready)) {
    // gate 2 failed: tear the husk down before opening the window, so the user
    // is never looking at two pickers for one download.
    forgetOverlay(id);
    await tellOverlayToClose(tabId, id);
    return false;
  }
  // BRING THE QUESTION TO THE USER. The popup window this replaces was created
  // `focused: true`; an overlay painted into a background tab is a question
  // nobody sees. The toast's `change` makes this concrete -- the user is looking
  // at the toast's own window, and the overlay goes to the download's tab.
  // Best effort: failing to raise a tab must not lose an overlay that works.
  try {
    await chrome.tabs.update(tabId, { active: true });
    if (typeof tab.windowId === "number") {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  } catch { /* the overlay is up either way */ }
  return true;
}

/**
 * Ask the user which directory. Overlay first, popup window as the automatic
 * fallback -- and `openOverlayPicker` is written so that every failure mode
 * reaches this fallback rather than throwing.
 */
export async function openPicker(info) {
  let shown = false;
  try {
    shown = await openOverlayPicker(info);
  } catch {
    shown = false;
  }
  if (shown) return true;
  return openPickerWindow(info);
}

// --- corrections ----------------------------------------------------------- //
/**
 * Apply the user's choice. If the download already completed, the file is
 * relocated (a same-filesystem rename, instant); otherwise the target is
 * remembered and applied when onChanged reports completion.
 */
export async function applyChoice(downloadId, chosenDir,
  { createdNew, kind } = {}) {
  // A cold-woken worker has no config and no snapshot yet, so knownDirs() is
  // empty and EVERY pick would be thrown away as "unsafe". The picker is a
  // separate popup window the user can leave open past the ~30s idle teardown,
  // so this is the normal case, not an edge one.
  await ready();
  const entry = state.pending.get(downloadId);
  const known = new Set([...knownDirs(), otherDir()]);
  if (createdNew) {
    // `kind` is what the picker asked for. Creating a directory WITHOUT one
    // leaves it unclassified, and an unclassified directory never auto-files
    // -- so the new directory would silently interrupt every future download
    // into it. That is why the picker asks rather than assuming.
    await api("POST", "/mkdir", { name: chosenDir, kind: kind || undefined },
      { timeoutMs: 5000 });
    await refreshSnapshot();
  }
  const safe = sanitizeDirName(chosenDir, createdNew ? null : known);
  if (!safe) throw new Error(`refusing unsafe directory: ${chosenDir}`);

  // A yt-dlp job, not a browser download: there is no DownloadItem to search
  // for and nothing to relocate -- the job simply has not been submitted yet.
  if (state.pendingFetch.has(downloadId)) {
    return submitFetch(downloadId, safe, { createdNew });
  }

  const items = await chrome.downloads.search({ id: downloadId });
  const item = items && items[0];
  if (item && item.state === "complete" && item.filename) {
    // null = the download did not land at <libraryRoot>/<dir>/<file>, so there
    // is nothing here this router may move. Refuse rather than guess -- and
    // SAY SO. This branch used to fall through to a `/learn` and an `{ok:true}`
    // while moving nothing, the same swallow-and-learn pair that was fixed in
    // onDownloadChanged but not here. This is the branch the picker uses.
    const rel = relPathFor(item);
    if (!rel) {
      const detail = "the file did not land inside the library root";
      await reportFailure(`Could not move the download to ${safe}`, detail);
      return { ok: false, dir: safe, error: detail };
    }
    if (rel.split("/")[0] !== safe) {
      // Deliberately NOT caught: a refused relocate must reach the caller (and
      // the user) rather than being reported as a success, and the alias below
      // must not be learned from a move that did not happen.
      await relocate(rel, safe, downloadId);
    }
  } else if (entry) {
    // Applied by onChanged when the download completes -- along with the
    // learn, so neither happens unless the move did.
    entry.wanted = safe;
    entry.wantedCreatedNew = Boolean(createdNew);
    state.pending.set(downloadId, entry);
    return { ok: true, dir: safe, deferred: true };
  }
  await learn(entry, safe, createdNew);
  return { ok: true, dir: safe };
}

/**
 * Ask the sidecar to move a completed download.
 *
 * The sidecar proves ownership from its OWN record of this `downloadId` -- the
 * file's name must match what that routing decision recorded, and the file must
 * be no older than it. Nothing the extension could assert here would add
 * evidence, so nothing is asserted: with no record the move is refused, and the
 * sidecar's own explanation is carried back through `sidecarError` so the user
 * reads THAT rather than a bare status code.
 */
async function relocate(rel, toDir, downloadId) {
  return api("POST", "/relocate", {
    fromRelPath: rel,
    toDir,
    downloadId,
  }, { timeoutMs: 10000 });
}

async function learn(entry, chosenDir, createdNew) {
  if (!entry) return;
  const out = await api("POST", "/learn", {
    context: entry.payload,
    chosenDir,
    autoDir: entry.dir,
    createdNew: Boolean(createdNew),
    // EXPLICIT CONFIRMATION. Every call here follows the user choosing a
    // directory in the picker (or confirming the one the router chose), and
    // the sidecar will not learn a tag -> directory alias without it. Stated
    // as a flag rather than inferred from the endpoint so the rule is legible
    // on both sides, and so a future automatic caller fails closed.
    confirmed: true,
  }, { timeoutMs: 5000 }).catch(() => null);
  reportNothingLearned(out);
}

/** Sources that carry a full-confidence identity (see matcher.identity_signals). */
const IDENTITY_SOURCES = new Set(["discord-channel", "thread-slug"]);

/**
 * Tell the user ONCE when a correction taught the router something less than
 * it looked like it did.
 *
 * The sidecar screens every candidate alias and returns what it refused, and
 * nothing consumed that -- which is how an over-strict screen stayed invisible
 * outside the sidecar journal. Two rules, and both matter:
 *
 *   * A SCREENED IDENTITY always notifies, even when something else was
 *     written. A screened identity writes no row, so the re-point bypass never
 *     engages for it either -- that channel or thread will never auto-file, and
 *     reporting "learned" because an unrelated tag landed hides it forever.
 *   * Otherwise, notify only when NOTHING was written. A category page whose
 *     junk tags were screened while a good one was learned is working as
 *     designed and must not nag.
 *
 * The catch-all is excluded outright: `/learn` returns a `skipped` entry for it
 * BY DESIGN ("the absence of a subject, not one"), and filing to the catch-all
 * is routine -- notifying there would train the operator to dismiss the very
 * notification the rest of this exists for. The `"catch-all"` source string is
 * a CROSS-LANGUAGE CONTRACT with server.App.learn and is pinned on both sides.
 *
 * Everything here is once-per-fact, never once-per-download. `dl-route alias
 * review` is the permanent surface; this is only the pointer at it.
 */
export function reportNothingLearned(out) {
  if (!out || !Array.isArray(out.skipped)) return false;
  // `first` is the sidecar telling us it has not refused this exact
  // (key, site, dir) before. WITHOUT IT the identity rule below notifies on
  // EVERY correction, because a permanent refusal (site branding, shared
  // vocabulary, a spread that only ever grows) recurs on every one -- and a
  // screened identity writes no row, so nothing else ever changes either.
  //
  // The flag lives in the SIDECAR, not in a suppression map here: MV3 tears
  // this worker down after ~30s idle, so a map would empty and the
  // notifications would resume. See Store.record_screened.
  const real = out.skipped.filter(
    (s) => s && s.source !== "catch-all" && s.first !== false);
  if (!real.length) return false;
  const identity = real.find((s) => IDENTITY_SOURCES.has(s.source));
  const wroteSomething = Array.isArray(out.written) && out.written.length > 0;
  if (!identity && wroteSomething) return false;
  // Report the IDENTITY refusal when there is one: it is the consequential
  // one, and `find(s => s.why)` would have reported whichever came first.
  const chosen = identity || real.find((s) => s.why) || real[0];
  void reportFailure(
    identity
      ? `Filed into ${out.dir}, but it will not learn this source`
      : `Filed into ${out.dir}, but learned nothing from it`,
    (chosen && chosen.why) ? String(chosen.why)
      : "every candidate was screened out");
  return true;
}

export async function onDownloadChanged(delta) {
  if (!delta || delta.state?.current !== "complete") return;
  await ready();
  const entry = state.pending.get(delta.id);
  if (!entry) return;
  if (entry.wanted) {
    const items = await chrome.downloads.search({ id: delta.id });
    const item = items && items[0];
    // KEY ON WHERE THE FILE ACTUALLY IS, not on what the router intended.
    //
    // This used to branch on `entry.wanted !== entry.dir` first, which asked
    // the wrong question. When the pick EQUALLED the router's own answer but
    // the file was somewhere else, nothing happened at all: no move, no
    // report, and the picker had already returned {ok:true, deferred:true} and
    // closed -- so the user believed their pick had been applied. The sibling
    // branch, given the identical physical situation, reported it.
    const rel = relPathFor(item);
    if (!rel) {
      // Outside the library root entirely (a Save-As elsewhere). Nothing this
      // router may move, and nothing it may learn from.
      await reportFailure(
        `Could not move the download to ${entry.wanted}`,
        "the file did not land inside the library root");
    } else if (rel.split("/")[0] === entry.wanted) {
      // Already where the user asked. Nothing to move, so "only learn if the
      // move happened" is trivially satisfied -- and this is the user
      // CONFIRMING a directory, which is a positive signal for the matcher
      // whether or not the router had proposed it.
      await learn(entry, entry.wanted, entry.wantedCreatedNew);
    } else {
      // Inside the library but in the wrong directory: move it, regardless of
      // what the router originally intended.
      try {
        await relocate(rel, entry.wanted, delta.id);
        await learn(entry, entry.wanted, entry.wantedCreatedNew);
      } catch (err) {
        // A `.catch(() => {})` here is how a completely dead correction path
        // stayed invisible: the move was refused, the alias was written
        // anyway, and the UI reported success. Surface it, and do not learn
        // from a move that never happened.
        await reportFailure(
          `Could not move the download to ${entry.wanted}`,
          errorMessage(err));
      }
    }
  }
  // Keep the entry briefly so a late "change" click can still relocate.
  // `unref` exists only under node (the test runner) -- without it this timer
  // would hold the event loop open for five minutes and hang the suite.
  const sweep = setTimeout(() => state.pending.delete(delta.id), 5 * 60 * 1000);
  if (sweep && typeof sweep.unref === "function") sweep.unref();
}

// --- context menus --------------------------------------------------------- //
export const MENU_ID = "dl-router-save";

export async function installMenus() {
  try {
    await chrome.contextMenus.removeAll();
    chrome.contextMenus.create({
      id: MENU_ID,
      title: "Save to library\u2026",
      contexts: ["link", "image", "video"],
    });
  } catch { /* menus are a nicety, never fatal */ }
}

export async function onMenuClicked(info, tab) {
  await ready();
  if (info.menuItemId !== MENU_ID || !state.config.enabled) return;
  const target = info.srcUrl || info.linkUrl || info.pageUrl;
  if (!isHttpUrl(target)) return;
  const streaming = /\.m3u8(\?|$)|\.mpd(\?|$)/i.test(target)
    || info.mediaType === "video";
  if (streaming) {
    // HLS/DASH has no single file to save -- hand the PAGE url to yt-dlp.
    const url = isHttpUrl(info.pageUrl) ? info.pageUrl : target;
    await startFetch(url, info, tab);
    return;
  }
  await chrome.downloads.download({ url: target });
}

// --- the yt-dlp path ------------------------------------------------------- //
let fetchSeq = 0;

/** Fetch jobs get a string key so applyChoice can tell them from a download. */
export function fetchKey() {
  fetchSeq += 1;
  return `fetch:${fetchSeq}`;
}

/**
 * Route a stream the SAME way a browser download is routed.
 *
 * It used to be `api("POST", "/fetch", { url, dir: otherDir() })
 * .catch(() => {})`: hardcoded to the catch-all, so a yt-dlp capture never
 * landed in the matched directory, never showed a toast, never offered the
 * picker, and swallowed every error -- clicking "Save to library" on a stream
 * looked identical whether it worked, was refused by the allowlist, or never
 * reached the sidecar at all.
 *
 * Now: /match (with the same cached-snapshot fallback as the download path),
 * auto-file above the threshold with a toast, picker below it, and any failure
 * is surfaced rather than swallowed.
 */
export async function startFetch(url, info, tab) {
  const capture = correlateCapture(
    { url, referrer: info?.pageUrl || "" }, state.captures,
    { now: Date.now(),
      windowMs: (state.snapshot?.captureWindowS ?? 15) * 1000,
      activeTabId: tab?.id ?? state.activeTabId }).capture;
  const item = { url, filename: "", referrer: info?.pageUrl || "" };
  const payload = buildMatchPayload(
    item, capture, carryReferrer(item, capture, state.captures));

  let decision = null;
  try {
    decision = await api("POST", "/match", payload, { timeoutMs: 4000 });
  } catch {
    decision = localDecide(localContext(payload), state.snapshot);
  }

  const known = new Set([...knownDirs(), otherDir()]);
  const dir = decision && decision.auto !== false
    ? sanitizeDirName(decision.dir, known) : null;
  const key = fetchKey();
  state.pendingFetch.set(key, { url, payload, decision });

  if (dir) return submitFetch(key, dir, {});
  await openPicker({
    downloadId: key,
    dir: otherDir(),
    reason: decision?.reason || "no match",
    dup: formatDup(decision?.dup) || "",
    suggestNew: decision?.suggestNew || "",
    candidates: decision?.candidates || [],
    // A yt-dlp job has no entry in `state.pending`, so the tab has to be
    // carried explicitly -- it is the tab the context menu was used in.
    tabId: tab?.id ?? capture?.tabId,
  });
  return { ok: true, queued: true, dir: null };
}

/** Submit the job and report the outcome. Never silently swallows a failure. */
export async function submitFetch(key, dir, { createdNew } = {}) {
  const job = state.pendingFetch.get(key);
  state.pendingFetch.delete(key);
  const url = job?.url;
  if (!isHttpUrl(url)) throw new Error(`refusing a non-http URL: ${url}`);
  try {
    const out = await api("POST", "/fetch", { url, dir }, { timeoutMs: 8000 });
    await showToast({
      downloadId: key, dir,
      reason: job?.decision?.reason || "stream via yt-dlp",
      source: "yt-dlp",
    });
    if (job && createdNew !== undefined) {
      await api("POST", "/learn", {
        context: job.payload, chosenDir: dir, autoDir: null,
        createdNew: Boolean(createdNew), confirmed: true,
      }, { timeoutMs: 5000 }).catch(() => {});
    }
    return { ok: true, dir, jobId: out?.jobId };
  } catch (err) {
    // The whole point: a refused or unreachable /fetch has to be visible.
    await reportFailure(`Could not start the download for ${dir}`,
      errorMessage(err));
    throw err;
  }
}

/** Surface an error to the user. Toast window first, notification second. */
export async function reportFailure(title, detail) {
  try {
    await chrome.notifications.create({
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon-48.png"),
      title,
      message: detail || "",
    });
    return true;
  } catch {
    return false;
  }
}

// --- messaging ------------------------------------------------------------- //
/**
 * EVERY branch that reads config or the snapshot must await `ready()` first.
 *
 * It did not, and the consequences were not theoretical: the toast and the
 * picker are separate popup WINDOWS the user can leave open past the ~30 s MV3
 * idle teardown, so a message routinely arrives at a cold-woken worker where
 * `state.config.token` is `""` and `state.snapshot` is `null`. Then:
 *
 *   * `dlr:choose` -> applyChoice computed knownDirs() from a null snapshot
 *     and threw the user's pick away as "refusing unsafe directory";
 *   * `dlr:snapshot` -> `Bearer ` -> 401 -> the picker cleared its loading
 *     state with an EMPTY directory list, so typing a name and pressing Enter
 *     created a new directory instead of selecting the existing match. That is
 *     finding 16 restored by a different route.
 *
 * `dlr:rules` answers asynchronously for the same reason; returning `true`
 * keeps the message channel open for the late `sendResponse`.
 */
export function onMessage(msg, sender, sendResponse) {
  if (!msg || typeof msg !== "object") return false;
  if (msg.type === "dlr:capture") {
    // Pure bookkeeping into a module-global buffer: no config, no snapshot,
    // and it must not be delayed or a fast click is lost.
    recordCapture(msg.payload, sender);
    return false;
  }
  if (msg.type === "dlr:choose") {
    // A SUBFRAME MUST PRESENT A NONCE WE ISSUED.
    //
    // `picker.html` is web-accessible (it has to be, to be framed), so ANY page
    // can embed it, point it at a recent download id and a chosen name, and
    // clickjack two clicks out of the user: one to take the "+ new dir" row,
    // one to answer the kind prompt. That is a /mkdir and a /relocate driven by
    // a hostile page. Path traversal is already impossible, but "create a
    // directory in the library and misfile a download into it" is not nothing,
    // and click-to-select is what made two blind clicks sufficient.
    //
    // Our own overlay is a subframe too, so the discriminator is the per-open
    // id: unguessable, issued by this worker, and never visible to the page
    // (the shadow root holding the frame is closed). The popup-window picker is
    // the TOP frame of its own tab and carries no nonce, which is why the test
    // is on `frameId` rather than on the nonce being present.
    // THE CHECK IS ASYNC, and it has to be. The registry it consults must
    // survive the ~30 s MV3 idle teardown -- the picker outlives that all the
    // time, because choosing is the slowest thing the user does here -- so
    // `overlayIsOurs` falls back to the durable copy in chrome.storage.session
    // rather than reading an empty in-memory Map and refusing a REAL pick.
    // Refusing a real pick is not recoverable: the choice is simply gone.
    const fromSubframe = typeof sender?.frameId === "number"
      && sender.frameId > 0;
    ready()
      .then(async () => {
        if (fromSubframe && !(await overlayIsOurs(msg.overlay))) {
          return { ok: false,
            error: "refusing a pick from an unrecognised frame" };
        }
        return applyChoice(msg.downloadId, msg.dir,
          { createdNew: msg.createdNew, kind: msg.kind });
      })
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: errorMessage(e) }));
    return true;   // async response
  }
  if (msg.type === "dlr:snapshot") {
    // `revalidate: false` -- this is the picker asking for the list it is
    // about to render, and the per-directory counts are not covered by the
    // etag, so a 304 would hand it a stale tally. See refreshSnapshot.
    ready()
      .then(() => refreshSnapshot({ revalidate: false }))
      .then(() => sendResponse({ ok: Boolean(state.snapshot),
        snapshot: state.snapshot }))
      .catch(() => sendResponse({ ok: Boolean(state.snapshot),
        snapshot: state.snapshot }));
    return true;
  }
  if (msg.type === "dlr:rules") {
    // Content scripts ask for the per-site capture rules on load. Answer from
    // the cached snapshot -- but only once there IS one; never block on the
    // network here.
    ready()
      .then(() => sendResponse({ siteRules: state.snapshot?.siteRules || {} }))
      .catch(() => sendResponse({ siteRules: {} }));
    return true;
  }
  if (msg.type === "dlr:overlay-lost") {
    // The content script noticed its host node was detached -- a page that
    // rewrote document.body, a sanitiser, an SPA re-render, or a page
    // deliberately removing it. Re-ask in a window, which the page cannot
    // touch.
    void redeliverAsWindow(msg.overlay);
    return false;
  }
  if (msg.type === "dlr:picker-ready") {
    // Gate 2 of the overlay handshake -- see openOverlayPicker. Pure
    // bookkeeping against an in-flight promise: it must NOT await ready(),
    // because the whole point is to answer inside the readiness budget.
    const resolve = overlayWaiters.get(msg.overlay);
    if (resolve) resolve();
    return false;
  }
  if (msg.type === "dlr:picker-closed") {
    // The embedded picker finished (accepted, cancelled, or refused then
    // escaped) and cannot close its own frame. Tear the overlay down.
    const record = state.overlays.get(msg.overlay);
    forgetOverlay(msg.overlay);
    // `sender.tab.id` is what survives an MV3 teardown. The picker can sit
    // open well past the ~30 s idle timeout, and a restarted worker has an
    // empty `state.overlays` -- keying only on that map would leave the
    // overlay on screen forever with nothing able to remove it.
    const tabId = record ? record.tabId : sender?.tab?.id;
    if (typeof tabId === "number") void tellOverlayToClose(tabId, msg.overlay);
    return false;
  }
  if (msg.type === "dlr:repick") {
    void ready().then(() => {
      const entry = state.pending.get(msg.downloadId);
      return openPicker({
        downloadId: msg.downloadId,
        dir: entry?.dir || otherDir(),
        reason: entry?.decision?.reason || "",
        dup: formatDup(entry?.decision?.dup) || "",
        suggestNew: entry?.decision?.suggestNew || "",
      });
    });
    return false;
  }
  return false;
}

// --- startup --------------------------------------------------------------- //
/**
 * Register every chrome.* listener. MUST be callable synchronously, and MUST
 * be reached in the FIRST TURN of the module -- no `await` before it, ever.
 *
 * THE BUG: `start()` used to `await loadConfig()` and `await restoreSnapshot()`
 * before calling `chrome.downloads.onDeterminingFilename.addListener`. MV3
 * tears this worker down after ~30s idle and restarts it on the next event, so
 * a download that WOKE the worker could be dispatched during those awaits,
 * before the listener existed. Chrome then used the default filename and the
 * file landed loose in the library root -- unrouted, and mixed in with the
 * qBittorrent seeding payloads that the backfill then has to disentangle.
 *
 * Nothing in here may await. The handlers do their own waiting on `ready()`.
 */
export function registerListeners() {
  chrome.downloads.onDeterminingFilename.addListener(onDeterminingFilename);
  chrome.downloads.onChanged.addListener((d) => { void onDownloadChanged(d); });
  chrome.runtime.onMessage.addListener(onMessage);
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    void onMenuClicked(info, tab);
  });
  // Ignores activation inside our own toast/picker popups -- see onTabActivated.
  chrome.tabs.onActivated.addListener(onTabActivated);
  // An overlay lives in a document the extension does not own. When that
  // document goes away the picker has to come back as a window, or the
  // download is left unasked -- see redeliverAsWindow.
  try {
    chrome.tabs.onRemoved.addListener(onTabRemoved);
    chrome.tabs.onUpdated.addListener(onTabUpdated);
  } catch { /* older shapes without these events */ }
  try {
    chrome.windows.onRemoved.addListener(onWindowRemoved);
  } catch { /* older shapes without windows.onRemoved */ }
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local") void loadConfig();
  });
  chrome.alarms.create("dlr-refresh", { periodInMinutes: SNAPSHOT_REFRESH_MINUTES });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "dlr-refresh") void refreshSnapshot().catch(() => {});
  });
}

/**
 * Everything asynchronous. Listeners are ALREADY registered by the time this
 * runs; anything that needs its results awaits `ready()`.
 */
export async function start() {
  await loadConfig();
  await restoreSnapshot();
  // Overlays opened before the last idle teardown. Without this a woken worker
  // has an empty registry, and the tab watchers would find no overlay to rescue
  // when its tab closes.
  await restoreOverlays();
  await installMenus();
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab && !state.ownWindowIds.has(tab.windowId)) state.activeTabId = tab.id;
  } catch { /* no window yet */ }
  await refreshSnapshot().catch(() => {});
  return state.config;
}

/** Test hook: re-arm the module as if the worker had just been woken. */
export function bootstrap() {
  registerListeners();          // synchronous, first turn -- never move this
  readyPromise = start().catch(() => state.config);
  return readyPromise;
}

if (!(typeof globalThis !== "undefined" && globalThis.DL_ROUTER_NO_AUTOSTART)) {
  bootstrap();
}
