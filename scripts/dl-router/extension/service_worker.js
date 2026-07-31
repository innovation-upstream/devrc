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
// Test hook: set `globalThis.DL_ROUTER_NO_AUTOSTART = true` before importing to
// suppress listener registration and networking.

import {
  buildMatchPayload, correlateCapture, formatDup, handleDetermining,
  localDecide,
} from "./route_core.js";
import { isHttpUrl, relPathFromAbsolute, sanitizeDirName } from "./sanitize.js";

const DEFAULT_PORT = 8791;
const CAPTURE_LIMIT = 40;
const SNAPSHOT_REFRESH_MINUTES = 5;
const TOAST_W = 420;
const TOAST_H = 190;
const PICKER_W = 460;
const PICKER_H = 420;

// Module-global so `onDeterminingFilename` can read it SYNCHRONOUSLY -- the
// listener has no time to await chrome.storage. It is repopulated on every
// service-worker start and by the refresh alarm.
export const state = {
  snapshot: null,       // last /dirs payload
  etag: null,
  captures: [],         // recent page-context captures (newest last)
  pending: new Map(),   // downloadId -> {dir, filename, decision, ts}
  config: { port: DEFAULT_PORT, token: "", enabled: false },
};

// --- config + transport ---------------------------------------------------- //
export async function loadConfig() {
  const got = await chrome.storage.local.get(["port", "token", "enabled"]);
  state.config = {
    port: got.port || DEFAULT_PORT,
    token: got.token || "",
    enabled: Boolean(got.enabled),
  };
  return state.config;
}

function base() {
  return `http://127.0.0.1:${state.config.port || DEFAULT_PORT}`;
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
    if (!resp.ok) throw new Error(`sidecar ${resp.status}`);
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
export function onDeterminingFilename(item, suggest) {
  if (!state.config.enabled) return false;   // profile opted out -> stock Brave
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
function popupUrl(page, params) {
  const qs = new URLSearchParams(params).toString();
  return chrome.runtime.getURL(`${page}?${qs}`);
}

export async function showToast(info) {
  // A popup WINDOW, not an in-page overlay: injection fails on the PDF viewer,
  // chrome:// pages and sandboxed frames, which is exactly where a download
  // often starts.
  try {
    await chrome.windows.create({
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
    await chrome.windows.create({
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
  const entry = state.pending.get(downloadId);
  const known = new Set([...knownDirs(), otherDir()]);
  if (createdNew) {
    await api("POST", "/mkdir", { name: chosenDir }, { timeoutMs: 5000 });
    await refreshSnapshot();
  }
  const safe = sanitizeDirName(chosenDir, createdNew ? null : known);
  if (!safe) throw new Error(`refusing unsafe directory: ${chosenDir}`);

  const items = await chrome.downloads.search({ id: downloadId });
  const item = items && items[0];
  if (item && item.state === "complete" && item.filename) {
    // null = the download did not land at <libraryRoot>/<dir>/<file>, so there
    // is nothing here this router may move. Refuse rather than guess.
    const rel = relPathFor(item);
    if (rel && rel.split("/")[0] !== safe) {
      await api("POST", "/relocate",
        { fromRelPath: rel, toDir: safe, downloadId },
        { timeoutMs: 10000 });
    }
  } else if (entry) {
    entry.wanted = safe;   // applied by onChanged when the download completes
    state.pending.set(downloadId, entry);
  }
  if (entry) {
    await api("POST", "/learn", {
      context: entry.payload,
      chosenDir: safe,
      autoDir: entry.dir,
      createdNew: Boolean(createdNew),
    }, { timeoutMs: 5000 }).catch(() => {});
  }
  return { ok: true, dir: safe };
}

export async function onDownloadChanged(delta) {
  if (!delta || delta.state?.current !== "complete") return;
  const entry = state.pending.get(delta.id);
  if (!entry) return;
  if (entry.wanted && entry.wanted !== entry.dir) {
    const items = await chrome.downloads.search({ id: delta.id });
    const item = items && items[0];
    const rel = relPathFor(item);
    if (rel) {
      await api("POST", "/relocate",
        { fromRelPath: rel, toDir: entry.wanted, downloadId: delta.id },
        { timeoutMs: 10000 })
        .catch(() => {});
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
  if (info.menuItemId !== MENU_ID || !state.config.enabled) return;
  const target = info.srcUrl || info.linkUrl || info.pageUrl;
  if (!isHttpUrl(target)) return;
  const streaming = /\.m3u8(\?|$)|\.mpd(\?|$)/i.test(target)
    || info.mediaType === "video";
  if (streaming) {
    // HLS/DASH has no single file to save -- hand the PAGE url to yt-dlp.
    const url = isHttpUrl(info.pageUrl) ? info.pageUrl : target;
    await api("POST", "/fetch", { url, dir: otherDir() }, { timeoutMs: 8000 })
      .catch(() => {});
    return;
  }
  await chrome.downloads.download({ url: target });
}

// --- messaging ------------------------------------------------------------- //
export function onMessage(msg, sender, sendResponse) {
  if (!msg || typeof msg !== "object") return false;
  if (msg.type === "dlr:capture") {
    recordCapture(msg.payload, sender);
    return false;
  }
  if (msg.type === "dlr:choose") {
    applyChoice(msg.downloadId, msg.dir, { createdNew: msg.createdNew })
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e) }));
    return true;   // async response
  }
  if (msg.type === "dlr:snapshot") {
    refreshSnapshot()
      .then(() => sendResponse({ ok: true, snapshot: state.snapshot }))
      .catch(() => sendResponse({ ok: Boolean(state.snapshot),
        snapshot: state.snapshot }));
    return true;
  }
  if (msg.type === "dlr:rules") {
    // Content scripts ask for the per-site capture rules on load. Answer from
    // the cached snapshot; never block on the network here.
    sendResponse({ siteRules: state.snapshot?.siteRules || {} });
    return false;
  }
  if (msg.type === "dlr:repick") {
    const entry = state.pending.get(msg.downloadId);
    void openPicker({
      downloadId: msg.downloadId,
      dir: entry?.dir || otherDir(),
      reason: entry?.decision?.reason || "",
      dup: formatDup(entry?.decision?.dup) || "",
      suggestNew: entry?.decision?.suggestNew || "",
    });
    return false;
  }
  return false;
}

// --- startup --------------------------------------------------------------- //
export async function start() {
  await loadConfig();
  await restoreSnapshot();
  chrome.downloads.onDeterminingFilename.addListener(onDeterminingFilename);
  chrome.downloads.onChanged.addListener((d) => { void onDownloadChanged(d); });
  chrome.runtime.onMessage.addListener(onMessage);
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    void onMenuClicked(info, tab);
  });
  chrome.tabs.onActivated.addListener(({ tabId }) => { state.activeTabId = tabId; });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local") void loadConfig();
  });
  chrome.alarms.create("dlr-refresh", { periodInMinutes: SNAPSHOT_REFRESH_MINUTES });
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === "dlr-refresh") void refreshSnapshot().catch(() => {});
  });
  await installMenus();
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab) state.activeTabId = tab.id;
  } catch { /* no window yet */ }
  await refreshSnapshot().catch(() => {});
}

if (!(typeof globalThis !== "undefined" && globalThis.DL_ROUTER_NO_AUTOSTART)) {
  void start();
}
