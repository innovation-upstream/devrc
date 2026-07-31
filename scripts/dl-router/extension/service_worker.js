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
  buildMatchPayload, correlateCapture, formatDup, handleDetermining,
  localDecide,
} from "./route_core.js";
import { isHttpUrl, relPathFromAbsolute, sanitizeDirName } from "./sanitize.js";
import { DEFAULT_PORT, manifestPort as readManifestPort } from "./port.js";

const CAPTURE_LIMIT = 40;
const SNAPSHOT_REFRESH_MINUTES = 5;
const TOAST_W = 420;
const TOAST_H = 190;
const PICKER_W = 460;
const PICKER_H = 420;

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
export async function refreshSnapshot() {
  const headers = state.etag ? { "If-None-Match": state.etag } : undefined;
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
  const payload = buildMatchPayload(item, capture);

  return handleDetermining(item, suggest, {
    knownDirs: knownDirs(),
    otherDir: otherDir(),
    timeoutMs: state.snapshot?.matchTimeoutMs ?? 400,
    localDecision: () => localDecide({
      tags: payload.page.tags,
      og: payload.page.og,
      linkText: payload.page.linkText,
      alt: payload.page.alt,
      pageTitle: payload.page.title,
      site: payload.page.site,
    }, state.snapshot),
    requestMatch: () => api("POST", "/match", payload,
      { timeoutMs: state.snapshot?.matchTimeoutMs ?? 400 }),
    onDecision: (info) => {
      state.pending.set(item.id, {
        dir: info.dir,
        filename: info.filename,
        decision: info.decision,
        payload,
        tier,
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

export async function openPicker(info) {
  try {
    const win = await chrome.windows.create({
      url: popupUrl("picker.html", {
        id: String(info.downloadId ?? ""),
        dir: info.dir || "",
        reason: info.reason || "",
        dup: info.dup || "",
        suggestNew: info.suggestNew || "",
      }),
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

// --- corrections ----------------------------------------------------------- //
/**
 * Apply the user's choice. If the download already completed, the file is
 * relocated (a same-filesystem rename, instant); otherwise the target is
 * remembered and applied when onChanged reports completion.
 */
export async function applyChoice(downloadId, chosenDir, { createdNew } = {}) {
  // A cold-woken worker has no config and no snapshot yet, so knownDirs() is
  // empty and EVERY pick would be thrown away as "unsafe". The picker is a
  // separate popup window the user can leave open past the ~30s idle teardown,
  // so this is the normal case, not an edge one.
  await ready();
  const entry = state.pending.get(downloadId);
  const known = new Set([...knownDirs(), otherDir()]);
  if (createdNew) {
    await api("POST", "/mkdir", { name: chosenDir }, { timeoutMs: 5000 });
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
  await api("POST", "/learn", {
    context: entry.payload,
    chosenDir,
    autoDir: entry.dir,
    createdNew: Boolean(createdNew),
  }, { timeoutMs: 5000 }).catch(() => {});
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
  const payload = buildMatchPayload({ url, filename: "" }, capture);

  let decision = null;
  try {
    decision = await api("POST", "/match", payload, { timeoutMs: 4000 });
  } catch {
    decision = localDecide({
      tags: payload.page.tags, og: payload.page.og,
      linkText: payload.page.linkText, alt: payload.page.alt,
      pageTitle: payload.page.title, site: payload.page.site,
    }, state.snapshot);
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
        createdNew: Boolean(createdNew),
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
    ready()
      .then(() => applyChoice(msg.downloadId, msg.dir,
        { createdNew: msg.createdNew }))
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: errorMessage(e) }));
    return true;   // async response
  }
  if (msg.type === "dlr:snapshot") {
    ready()
      .then(() => refreshSnapshot())
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
