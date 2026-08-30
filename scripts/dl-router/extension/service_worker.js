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
  buildMatchPayload, carryReferrer, correlateCapture, discordSourceKey,
  formatDup, handleDetermining, localContext, localDecide, playerSourceKey,
  preferOriginalUrl,
} from "./route_core.js";
import { isHttpUrl, relPathFromAbsolute, sanitizeDirName } from "./sanitize.js";
import { DEFAULT_PORT, manifestPort as readManifestPort } from "./port.js";

const CAPTURE_LIMIT = 40;
const SNAPSHOT_REFRESH_MINUTES = 5;
const TOAST_W = 420;
const TOAST_H = 190;
// The duplicate toast carries one more line and two more buttons.
const TOAST_DUP_H = 240;
// /discard READS BOTH FILES IN FULL before deleting anything -- sampling
// cannot carry a destructive decision, so the sidecar proves it properly and
// that takes as long as the disk takes. This MUST exceed the sidecar's own
// `DISCARD_VERIFY_TIMEOUT_S` (180 s), or the toast reports "Not deleted" for a
// delete that then succeeds -- an active false claim about a destructive
// operation, which is the exact shape already fixed once here (the index walk
// on the response path).
const DISCARD_TIMEOUT_MS = 240000;
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

// --- `state.pending` outlives this worker, and has to -------------------------
//
// An entry in `state.pending` is what makes a download's toast, its relocate
// and its learning possible: `onDownloadChanged` reads it and returns early
// when it is absent. It was an in-memory Map, and MV3 tears this worker down
// after ~30 s idle -- so ANY download that took longer than half a minute lost
// all three, silently, today. That is not an edge case; it is every video.
//
// `chrome.storage.local`, NOT `chrome.storage.session`. The two are used for
// two different lifetimes in this file and conflating them would be wrong in
// both directions:
//
//   * the overlay registry uses `session` because an overlay is a node in a
//     page's document. Every overlay dies with the browser, so a record that
//     outlived the browser could only ever resurrect a picker for a download
//     nobody remembers.
//   * a download in flight is the opposite. Chrome RESUMES interrupted
//     downloads across a browser restart, and a multi-gigabyte file plausibly
//     spans one. `local` survives that, which is the correct lifetime here.
//
// The cost of the longer lifetime is that stale entries would accumulate
// forever, so a TTL and a cap are prerequisites rather than polish.
const PENDING_KEY = "pending";
const PENDING_MAX = 64;
const PENDING_TTL_MS = 24 * 60 * 60 * 1000;

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
    // The STABLE ledger key for a download whose own URL is not one (a signed,
    // rotating media URL). Empty for every ordinary capture.
    sourceKey: typeof payload.sourceKey === "string" ? payload.sourceKey : "",
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

// --- the pending registry, and why it is PERSISTED -------------------------- //
/**
 * Write the durable copy. Fire-and-forget everywhere: a failed write costs a
 * toast after a teardown, and blocking a routing decision on storage would
 * cost the download itself.
 *
 * Pruned on WRITE as well as on read, so the stored array cannot grow past the
 * cap even if nothing ever reads it back.
 */
async function persistPending() {
  try {
    const now = Date.now();
    const rows = [...state.pending]
      .filter(([, e]) => e && typeof e === "object"
        && now - (e.ts || 0) <= PENDING_TTL_MS)
      .slice(-PENDING_MAX);
    await chrome.storage.local.set({ [PENDING_KEY]: rows });
  } catch { /* storage unavailable; the in-memory copy still works */ }
}

/** Merge the durable copy in. NEVER clobbers an entry created this turn. */
export async function restorePending() {
  try {
    const got = await chrome.storage.local.get(PENDING_KEY);
    const rows = got && got[PENDING_KEY];
    if (!Array.isArray(rows)) return;
    const now = Date.now();
    for (const row of rows) {
      if (!Array.isArray(row) || row.length !== 2) continue;
      const [id, entry] = row;
      if (typeof id !== "number" && typeof id !== "string") continue;
      if (!entry || typeof entry !== "object") continue;
      // A download older than the TTL is not resumable in any useful sense and
      // its file has long since been dealt with by hand.
      if (now - (entry.ts || 0) > PENDING_TTL_MS) continue;
      if (!state.pending.has(id)) state.pending.set(id, entry);
    }
  } catch { /* best effort */ }
}

function rememberPending(id, entry) {
  state.pending.set(id, entry);
  void persistPending();
}

function forgetPending(id) {
  const had = state.pending.delete(id);
  if (had) void persistPending();
  return had;
}

/**
 * The entry for a download, consulting the durable copy before giving up.
 *
 * The lazy restore is here rather than only in `start()` for the same reason
 * `overlayIsOurs` does it: whether the answer is right must not depend on when
 * readiness happened to resolve. Every caller that would otherwise reason from
 * an empty Map goes through this.
 */
async function pendingEntry(id) {
  if (state.pending.has(id)) return state.pending.get(id);
  await restorePending();
  return state.pending.get(id);
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
      rememberPending(item.id, {
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

/**
 * The duplicate question. THE FILE IS ALREADY KEPT AND FILED -- this only
 * offers to remove the copy that just landed.
 *
 * A window, and a taller one, for the same reason the auto-file toast is a
 * window: a download often starts on a page no content script can reach. It is
 * shown only for a CONFIRMED duplicate (matching size AND bounded head+tail
 * digest, from POST /dedupe), never for the "possible duplicate" line that
 * rides along on the ordinary toast -- offering a delete button next to a
 * maybe is how a warning becomes a data-loss mechanism.
 */
export async function showDuplicateToast(info) {
  const dupLine = `Duplicate of ${info.dupRelPath}`;
  try {
    const win = await chrome.windows.create({
      url: popupUrl("toast.html", {
        id: String(info.downloadId ?? ""),
        dir: info.dir || "",
        reason: info.reason || "The file was kept and filed normally.",
        dup: dupLine,
        source: "duplicate",
        mode: "dup",
        rel: info.relPath || "",
        dupRel: info.dupRelPath || "",
      }),
      type: "popup",
      width: TOAST_W,
      height: TOAST_DUP_H,
      // FOCUSED, unlike the ordinary toast: this one is a question with no
      // auto-close, and an unfocused question sits behind the browser window
      // forever.
      focused: true,
    });
    rememberOwnWindow(win);
    return true;
  } catch {
    try {
      // The notification carries NO delete affordance on purpose. A
      // notification button cannot show the sidecar's refusal, and this path
      // exists because window creation already failed.
      await chrome.notifications.create({
        type: "basic",
        iconUrl: chrome.runtime.getURL("icons/icon-48.png"),
        title: `Filed to ${info.dir} (duplicate)`,
        message: `${dupLine}\nThe file was kept.`,
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

/**
 * Which tab should host the overlay.
 *
 * ASYNC because `pendingEntry` is: after an idle teardown the in-memory Map is
 * empty, and reading it directly would send every post-teardown picker to a
 * popup window (or the wrong tab) rather than to the tab the download came
 * from -- which is precisely the window in which a picker is most likely to be
 * opened, because the user was slow.
 */
async function overlayTabId(info) {
  if (typeof info.tabId === "number") return info.tabId;
  const entry = await pendingEntry(info.downloadId);
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
  const tabId = await overlayTabId(info);
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
  // AND THEN TAKE THE KEYBOARD FOCUS BACK. Raising a tab focuses the PAGE's
  // document, which strips the focus off the frame the content script had just
  // given it -- so the picker painted, and typing went to the page underneath
  // it. This message is sent AFTER the raising, so it is the one that wins;
  // the framed picker's own `focus` handler then puts the caret in the filter
  // input. Best effort, and last: an overlay you have to click once is far
  // better than no overlay.
  try {
    await chrome.tabs.sendMessage(tabId,
      { type: "dlr:focus-overlay", overlay: id }, { frameId: 0 });
  } catch { /* the overlay is up and usable either way */ }
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
  const entry = await pendingEntry(downloadId);
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
    // THE CORRECTION ITSELF HAS TO SURVIVE THE TEARDOWN. This is the branch a
    // picker takes while the download is still running, which is exactly the
    // case that outlives ~30 s of idle: without the durable write the user's
    // choice would be applied by a worker that no longer has it.
    rememberPending(downloadId, entry);
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

/**
 * Confirm (or refute) a duplicate, now that the file exists.
 *
 * WHY IT IS HERE AND NOT ON THE /match PATH: at `onDeterminingFilename` time
 * Chrome has not written a byte, so there is no file to hash -- and `totalBytes`
 * is frequently 0 there, so even the size is unreliable. This is the first
 * moment both files exist. It is also a moment where nothing is waiting: a slow
 * answer costs a late toast, not a misfiled download.
 *
 * Fire-and-forget by construction: every failure is swallowed. A dedupe warning
 * that broke the completion handler would take the correction path down with
 * it, and there is nothing to report -- the file is already filed correctly.
 */
export async function confirmDuplicate(downloadId, rel, dir, entry) {
  if (!rel) return null;
  // ONCE PER DOWNLOAD, LATCHED ON THE ENTRY.
  //
  // `state.pending` keeps the entry for five minutes after completion (so a
  // late "change" click can still relocate), and nothing stops Chrome
  // delivering a second `state: {current: "complete"}` delta in that window.
  // Without this latch that is a second /dedupe and a second duplicate toast
  // for ONE download -- and this toast is focused and never auto-closes, so
  // the duplicates would stack up in front of the user. The same lesson as
  // the picker's done-latch: the guard belongs on the transition, not on the
  // post-state.
  if (entry) {
    if (entry.dedupeChecked) return null;
    entry.dedupeChecked = true;
    // The latch has to be as durable as the entry that carries it. Now that
    // `pending` survives the teardown, a second `complete` delta delivered to a
    // freshly-woken worker would restore the entry WITHOUT the latch and open a
    // second focused, never-auto-closing duplicate toast for one download.
    void persistPending();
  }
  let out;
  try {
    out = await api("POST", "/dedupe", { relPath: rel, downloadId },
      { timeoutMs: 15000 });
  } catch {
    return null;
  }
  if (!out || out.duplicate !== true || !out.dupRelPath) return null;
  await showDuplicateToast({
    downloadId,
    dir: dir || rel.split("/")[0],
    relPath: out.relPath || rel,
    dupRelPath: out.dupRelPath,
    reason: `Same size and content as a file you already have (${out.kind}).`,
  });
  return out;
}

export async function onDownloadChanged(delta) {
  if (!delta || delta.state?.current !== "complete") return;
  await ready();
  // THE DURABLE READ, and the whole point of persisting `pending`. This used to
  // be `state.pending.get(delta.id)`, and the `if (!entry) return` below then
  // discarded the toast, the pending relocate and the learning for every
  // download that ran longer than the ~30 s MV3 idle teardown -- which is most
  // of them.
  const entry = await pendingEntry(delta.id);
  if (!entry) return;
  // Where the file ended up, following any correction applied below. The
  // dedupe check needs the FINAL path: checking the pre-move one would ask the
  // sidecar about a file that is no longer there.
  let finalRel = null;
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
      finalRel = rel;
      await learn(entry, entry.wanted, entry.wantedCreatedNew);
    } else {
      // Inside the library but in the wrong directory: move it, regardless of
      // what the router originally intended.
      try {
        const moved = await relocate(rel, entry.wanted, delta.id);
        // The sidecar uniquifies on a name collision, so its answer is the
        // authority on where the file is -- not the path we asked for.
        finalRel = (moved && typeof moved.relPath === "string")
          ? moved.relPath : rel;
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
  } else {
    // The auto-filed path: no correction was asked for, so the file is wherever
    // the router put it.
    const items = await chrome.downloads.search({ id: delta.id });
    finalRel = relPathFor(items && items[0]);
  }
  // THE DUPLICATE CHECK IS LAST, and it never throws (see confirmDuplicate).
  // It runs on both paths -- an auto-filed download is exactly as likely to be
  // a duplicate as a corrected one -- and only ever WARNS.
  await confirmDuplicate(delta.id, finalRel, entry.wanted || entry.dir, entry);
  // Keep the entry briefly so a late "change" click can still relocate.
  // `unref` exists only under node (the test runner) -- without it this timer
  // would hold the event loop open for five minutes and hang the suite.
  const sweep = setTimeout(() => forgetPending(delta.id), 5 * 60 * 1000);
  if (sweep && typeof sweep.unref === "function") sweep.unref();
}

// --- the player-button path ------------------------------------------------ //
/**
 * The top frame's own description of itself, or null.
 *
 * THE EMBED FRAME CANNOT IDENTIFY ITS THREAD, and this is the only path that
 * can. `document.referrer` inside the embed is the forum ORIGIN with the path
 * stripped (`strict-origin-when-cross-origin`), and
 * `location.ancestorOrigins` gives origins without paths. So the subject has to
 * come from the top frame, and only the worker can reach both frames.
 *
 * `{ frameId: 0 }` is the whole correlation, and it is a PROOF rather than a
 * heuristic: the message is delivered to the top frame OF THE SAME TAB the
 * click came from, and that frame answers with its own `location.href`.
 *
 * THERE IS DELIBERATELY NO FALLBACK. The obvious one -- the newest capture from
 * this tab -- is the branch `carryReferrer` had deleted for being "the last
 * thread I saw in that tab" wearing the word "provable"; on a forum, where the
 * user scrolls past several threads, it produces a CONFIDENT WRONG SUBJECT that
 * is then learned as a 1.00 identity alias. Returning null costs one picker.
 */
async function topFrameContext(tabId) {
  let res;
  try {
    res = await chrome.tabs.sendMessage(tabId, { type: "dlr:page-context" },
      { frameId: 0 });
  } catch {
    return null;   // no content script in the top frame, or the tab is gone
  }
  if (!res || res.ok !== true) return null;
  const ctx = res.context;
  if (!ctx || typeof ctx !== "object") return null;
  // The frame must have reported a real page URL. Without one there is no
  // thread slug, no site and no title -- i.e. nothing this was asked for.
  if (!isHttpUrl(ctx.pageUrl)) return null;
  return ctx;
}

/**
 * `dlr:player-download` -- a click on an injected player button.
 *
 * The media URL arrives from the frame READ AT CLICK TIME (see
 * player_buttons.readMediaUrl); it is signed and rotates, so it is used
 * immediately and never stored.
 *
 * The routing then rides the ordinary pipeline rather than a parallel one: a
 * capture is synthesised for the tab and pushed into the SAME buffer every
 * click capture goes into, and `chrome.downloads.download` is handed the exact
 * URL that capture records. `correlateCapture` tier 1 (exact URL match on
 * `item.url`) therefore binds them deterministically -- no time window, no
 * active-tab guess, and every downstream behaviour (auto-file, toast, picker,
 * relocate, learn, dedupe) is inherited unchanged.
 */
export async function playerDownload(msg, sender) {
  await ready();
  if (!state.config.enabled) {
    return { ok: false, error: "routing is not enabled in this profile" };
  }
  const mediaUrl = msg && msg.mediaUrl;
  if (!isHttpUrl(mediaUrl)) {
    return { ok: false, error: "refusing a non-http media URL" };
  }
  const tabId = sender?.tab?.id;
  if (typeof tabId !== "number") {
    return { ok: false, error: "no tab for this player" };
  }
  const context = await topFrameContext(tabId);
  const capture = {
    ...(context || {}),
    // The clicked "element" is the media itself. Both fields are set because
    // tier 1 tests either against the DownloadItem's url.
    href: mediaUrl,
    mediaSrc: mediaUrl,
    // The ledger's key. Stable across the signature rotation the media URL is
    // subject to -- see route_core.playerSourceKey.
    sourceKey: playerSourceKey(msg && msg.embedUrl),
  };
  // WITH NO PROVEN CONTEXT THE CAPTURE CARRIES NO SUBJECT AT ALL. Not the embed
  // page's title, not its URL: an embed page is a bare player, and anything
  // scraped from it would be the embed host's branding presented as the
  // subject. An empty context scores nothing, falls below the threshold and
  // reaches the picker, which is the correct degradation.
  if (!context) {
    capture.pageUrl = "";
    capture.pageTitle = "";
    capture.site = "";
    capture.tags = [];
    capture.og = {};
  }
  recordCapture(capture, { tab: { id: tabId } });
  try {
    const downloadId = await chrome.downloads.download({ url: mediaUrl });
    return { ok: true, downloadId, context: Boolean(context) };
  } catch (err) {
    return { ok: false, error: errorMessage(err) };
  }
}

/**
 * `dlr:have` -- the "already have this" badge's question.
 *
 * Asked BY THE EMBED PAGE URL, normalised through the same `playerSourceKey`
 * the write side uses. Keying this on the media URL instead would mean the
 * badge never lit: the signature rotates, so every lookup would miss, and a
 * badge that never lights actively asserts "you do not have this".
 *
 * Every failure is a MISS, never an error the user sees. This is a hint on
 * someone else's page; a sidecar that is down must produce no badge, not a
 * broken one.
 */
export async function haveUrl(embedUrl) {
  await ready();
  if (!state.config.enabled) return { ok: false, have: false };
  // THE READ MUST USE THE SAME FOLD AS THE WRITE. `discordSourceKey` folds
  // a Discord attachment's host to the origin before the ledger records it, so
  // asking with the bare `playerSourceKey` of a `media.discordapp.net` URL
  // looks up a key the write side never stores -- a badge that can only miss,
  // which is the failure this whole docstring is about. Not reachable today
  // (no Discord `[site_rules."<host>".player]` rule exists, so nothing asks
  // with a CDN URL), but nothing else pins it, and the first operator to add
  // one would hit it.
  const key = discordSourceKey(embedUrl) || playerSourceKey(embedUrl);
  if (!key) return { ok: false, have: false };
  try {
    const out = await api("GET", `/have?url=${encodeURIComponent(key)}`,
      undefined, { timeoutMs: 3000 });
    return { ok: true, have: out?.have === true, dir: out?.dir || "" };
  } catch {
    return { ok: false, have: false };
  }
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
  // `srcUrl` unless a link proves a better copy of the SAME asset exists --
  // on Discord an image's src is a downscaled webp from the resizing proxy.
  const target = preferOriginalUrl(info.srcUrl, info.linkUrl) || info.pageUrl;
  if (!isHttpUrl(target)) return;
  // A MANIFEST IS ALWAYS A STREAM, including one served from Discord. This
  // test stays OUTSIDE the bypass below on purpose: an `.m3u8` has no single
  // file to save, so letting the bypass reach it would download a ~200-byte
  // playlist and call it the media -- trading a loud failure for a silent
  // wrong answer.
  const manifest = /\.m3u8(\?|$)|\.mpd(\?|$)/i.test(target);
  // Otherwise a Discord attachment is a DIRECT FILE whatever element it was
  // clicked on, so it must not take the yt-dlp branch: that branch hands
  // `discord.com/channels/<guild>/<channel>` to yt-dlp while the .mp4 sits
  // right there in `srcUrl`.
  //
  // The `mediaType === "video"` clause it bypasses catches players whose media
  // URL this listener cannot use directly. Its exact reach is NOT verified
  // here -- `isHttpUrl` already returned above, so whether a `blob:`-src
  // <video> can reach this line depends on whether Chrome populates
  // `info.srcUrl` for one, which needs a browser to answer. The bypass is
  // scoped to CDN attachments either way, so it does not depend on that.
  const directFile = discordSourceKey(target) !== "";
  const streaming = manifest || (!directFile && info.mediaType === "video");
  if (streaming) {
    // HLS/DASH has no single file to save -- hand the PAGE url to yt-dlp,
    // because on the sites this was built for the page is what yt-dlp knows
    // how to resolve.
    //
    // A DISCORD MANIFEST IS THE ONE CASE WHERE THAT IS BACKWARDS, and the cell
    // only became reachable when the manifest test moved outside the bypass.
    // `info.pageUrl` there is `discord.com/channels/<guild>/<channel>` -- an
    // authenticated SPA route, not a media page -- while `target` IS the
    // manifest yt-dlp consumes natively. Handing over the page throws away the
    // only usable URL, and the failure is INVISIBLE: `/fetch` has no allowlist
    // so the job is accepted, `submitFetch` toasts "filed to <dir>" as soon as
    // the POST returns, and nothing ever polls the job -- `jobId` is returned
    // once and dropped. That is a success toast with no file, which is exactly
    // the silent wrong answer the manifest test was moved out here to prevent.
    const url = (manifest && directFile) ? target
      : (isHttpUrl(info.pageUrl) ? info.pageUrl : target);
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
  if (msg.type === "dlr:discard") {
    // THE ONLY DESTRUCTIVE MESSAGE IN THE EXTENSION.
    //
    // It asserts nothing and proves nothing: it forwards three strings and
    // lets the sidecar refuse. That is deliberate -- the sidecar holds the
    // route log, the file's mtime and the bytes of both files, and this worker
    // holds none of them, so any check here would be a check the caller could
    // also have made.
    //
    // The subframe refusal below is DEFENCE IN DEPTH, not a live hole being
    // closed: `toast.html` is NOT in `web_accessible_resources` (only
    // picker.html and its modules are), so today no page can frame it. It is
    // here because the reason picker.html had to become web-accessible --
    // being framed as an overlay -- could just as easily be wanted for the
    // toast later, and the person making that change should find the delete
    // path already refusing rather than have to notice it needs to. There is
    // no legitimate embedded duplicate toast, so unlike `dlr:choose` there is
    // no nonce to weigh up: a subframe is simply refused.
    const fromSubframe = typeof sender?.frameId === "number"
      && sender.frameId > 0;
    if (fromSubframe) {
      sendResponse({ ok: false,
        error: "refusing a delete from an embedded frame" });
      return false;
    }
    ready()
      .then(() => api("POST", "/discard", {
        relPath: msg.relPath,
        dupRelPath: msg.dupRelPath,
        downloadId: msg.downloadId,
      }, { timeoutMs: DISCARD_TIMEOUT_MS }))
      .then((r) => sendResponse(r))
      .catch((e) => {
        // Surface it twice: in the toast (which stays open) and as a
        // notification, because a refused DELETE is the one refusal the user
        // must not be able to miss.
        const detail = errorMessage(e);
        void reportFailure("Did not delete the duplicate", detail);
        sendResponse({ ok: false, error: detail });
      });
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
  if (msg.type === "dlr:player-download") {
    // FROM A SUBFRAME BY DESIGN, unlike `dlr:choose` and `dlr:discard`: the
    // media element only exists inside the cross-origin embed frame, so
    // refusing subframes here would refuse the entire feature. There is no
    // nonce to check and none is needed -- this message asserts nothing about
    // the library. Everything it can do, the page could already do: start a
    // download of a URL it chose. Where it lands is decided by /match from a
    // context proven through frameId 0, and an unproven one goes to the picker.
    playerDownload(msg, sender)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: errorMessage(e) }));
    return true;   // async response
  }
  if (msg.type === "dlr:have") {
    haveUrl(msg.embedUrl)
      .then((r) => sendResponse(r))
      .catch(() => sendResponse({ ok: false, have: false }));
    return true;   // async response
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
    void ready().then(async () => {
      const entry = await pendingEntry(msg.downloadId);
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
    // ONLY the two config keys. `chrome.storage.local` also holds the durable
    // `pending` registry now, which is written on every routing decision and
    // every correction -- so an unfiltered handler would re-read the config
    // (and allocate a promise inside a listener) on every download event, for
    // a change that provably cannot affect it.
    if (area !== "local" || !changes) return;
    if ("token" in changes || "enabled" in changes) void loadConfig();
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
  // Downloads still in flight from before the last teardown, or from before the
  // last browser restart. See the PENDING_KEY block at the top.
  await restorePending();
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
