// service_worker.js -- orchestration for the Claude Usage Tracker.
//
// All pure logic lives in lib/normalize.js / lib/timefmt.js and the exported
// helpers below (node-tested). This file owns the browser side: triggers,
// storage, thresholds, toasts and the badge.
//
// MV3 lifetime: this worker is torn down after ~30s idle and restarted by the
// next event. EVERY listener is registered synchronously in the FIRST TURN of
// the script (registerListeners() at the bottom) -- a listener registered
// after an await is a listener that misses the wake-up event. There is no
// action.onClicked listener: the manifest's action.default_popup covers the
// toolbar click, and notifications are only ever CREATED here, never
// answered.
//
// Test hook: set `globalThis.CLAUDE_USAGE_NO_AUTOSTART = true` before
// importing to suppress listener registration and networking.

import {
  mergeStored,
  normalizeStoredRecord,
  normalizeUsage,
} from "./lib/normalize.js";
import {
  STALE_AFTER_MS,
  formatCountdown,
  isStale,
  stalenessLabel,
} from "./lib/timefmt.js";
import {
  WARN_PCT,
  toneColor,
  toneForRecord,
} from "./lib/severity.js";

export const ALARM_NAME = "cu-reprobe";
export const REPROBE_PERIOD_MIN = 15;
// Per-tab ask dedup: onUpdated(complete) and onActivated can both fire for
// the same tab within seconds; only one of them may trigger a probe.
export const PROBE_MIN_INTERVAL_MS = 30 * 1000;
export const TOAST_DEDUP_MS = 30 * 60 * 1000;
// The toast threshold IS the shared warn band -- one number, not two that a
// test asserts are equal. A test can only catch a drift that has already been
// written; this cannot drift.
export const ALERT_THRESHOLD_PCT = WARN_PCT;

const CLAUDE_AI_HOST = "claude.ai";
const CLAUDE_AI_MATCH = ["https://claude.ai/*"];

export const state = {
  // tabId -> epoch ms of the last probe ask. In-memory only: a worker wake
  // resets it, which loosens dedup across a teardown (worst case: one extra
  // snapshot request), never a missed one.
  lastAskedByTab: new Map(),
};

// --- storage ---------------------------------------------------------------- //
const KEY_ACCOUNTS = "accounts";
const KEY_LAST_ACTIVE = "lastActiveOrg";
const KEY_LAST_TOAST = "lastToast";

export async function readState() {
  const got = await chrome.storage.local.get([KEY_ACCOUNTS, KEY_LAST_ACTIVE, KEY_LAST_TOAST]);
  const rawAccounts = got[KEY_ACCOUNTS] || {};
  const accounts = {};
  for (const uuid of Object.keys(rawAccounts)) {
    accounts[uuid] = normalizeStoredRecord(rawAccounts[uuid]);
  }
  return {
    accounts,
    lastActiveOrg: typeof got[KEY_LAST_ACTIVE] === "string" ? got[KEY_LAST_ACTIVE] : null,
    lastToast: isObj(got[KEY_LAST_TOAST]) ? got[KEY_LAST_TOAST] : {},
  };
}

export async function writeState(patch) {
  await chrome.storage.local.set(patch);
}

function isObj(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// --- pure trigger gating ----------------------------------------------------- //

/** claude.ai itself only, over https -- the usage endpoints live there and
 * nowhere else. The protocol is pinned to match the manifest's
 * host_permissions / content_scripts match pattern. */
export function isClaudeAiUrl(url) {
  if (typeof url !== "string" || url === "") return false;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && u.hostname === CLAUDE_AI_HOST;
  } catch {
    return false;
  }
}

/**
 * Should this tab event trigger a probe? Pure. `tab` is {url, status};
 * `status` must be "complete" (the spec's onUpdated trigger) and the URL must
 * be claude.ai. The per-tab dedup collapses the onUpdated + onActivated
 * double-fire and repeated update events.
 */
export function shouldProbeTab(tab, lastAskedAt, now) {
  if (!isObj(tab)) return false;
  if (tab.status !== "complete") return false;
  if (!isClaudeAiUrl(tab.url)) return false;
  if (typeof lastAskedAt === "number" && now - lastAskedAt < PROBE_MIN_INTERVAL_MS) return false;
  return true;
}

/** An org uuid different from the last one we saw active = an account switch. */
export function detectSwitch(lastActiveOrg, activeUuid) {
  return Boolean(lastActiveOrg && activeUuid && activeUuid !== lastActiveOrg);
}

// --- alerts ------------------------------------------------------------------ //

function pct(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function toastKey(orgUuid, kind) {
  return `${orgUuid}:${kind}`;
}

function withinDedup(lastToast, orgUuid, kind, now) {
  const at = lastToast[toastKey(orgUuid, kind)];
  return typeof at === "number" && now - at < TOAST_DEDUP_MS;
}

/**
 * Threshold alerts for ONE freshly-updated record. Pure.
 *
 * Edge-triggered on the ALERT_THRESHOLD_PCT crossing (or the first sighting
 * already past it): holding a value >=80% does not re-fire, recovering below
 * it re-arms the next crossing. locked_reason and a credits disabled_reason
 * alert the same way. Kinds already toasted for this account inside
 * TOAST_DEDUP_MS are suppressed here, so the caller can fire exactly what
 * comes back.
 */
export function evaluateAlerts(record, prev, now, lastToast) {
  if (!isObj(record) || !record.orgUuid) return [];
  const out = [];
  const was = (sel) => (isObj(prev) ? sel(prev) : null);

  const sess = pct(record.session && record.session.utilization);
  const sessPrev = pct(was((p) => p.session && p.session.utilization));
  if (sess !== null && sess >= ALERT_THRESHOLD_PCT
    && !(sessPrev !== null && sessPrev >= ALERT_THRESHOLD_PCT)
    && !withinDedup(lastToast, record.orgUuid, "session-high", now)) {
    out.push({
      kind: "session-high",
      title: "Claude usage — session threshold",
      message: `${record.orgName}\nSession at ${Math.round(sess)}% · resets ${formatCountdown(record.session.resetsAt, now)}`,
    });
  }

  const wk = pct(record.weekly && record.weekly.utilization);
  const wkPrev = pct(was((p) => p.weekly && p.weekly.utilization));
  if (wk !== null && wk >= ALERT_THRESHOLD_PCT
    && !(wkPrev !== null && wkPrev >= ALERT_THRESHOLD_PCT)
    && !withinDedup(lastToast, record.orgUuid, "weekly-high", now)) {
    out.push({
      kind: "weekly-high",
      title: "Claude usage — weekly threshold",
      message: `${record.orgName}\nWeekly at ${Math.round(wk)}% · resets ${formatCountdown(record.weekly.resetsAt, now)}`,
    });
  }

  // locked_reason exists on EVERY window in the API (recon 2026-09-19: null
  // on all of them today); session and weekly both alert when it populates.
  const locked = record.session && record.session.lockedReason;
  const lockedPrev = was((p) => p.session && p.session.lockedReason);
  if (locked && !lockedPrev
    && !withinDedup(lastToast, record.orgUuid, "locked", now)) {
    out.push({
      kind: "locked",
      title: "Claude usage — session locked",
      message: `${record.orgName}\n${locked}`,
    });
  }

  const weeklyLocked = record.weekly && record.weekly.lockedReason;
  const weeklyLockedPrev = was((p) => p.weekly && p.weekly.lockedReason);
  if (weeklyLocked && !weeklyLockedPrev
    && !withinDedup(lastToast, record.orgUuid, "locked-weekly", now)) {
    out.push({
      kind: "locked-weekly",
      title: "Claude usage — weekly locked",
      message: `${record.orgName}\n${weeklyLocked}`,
    });
  }

  const creditsOff = record.credits && record.credits.enabled === false
    && record.credits.disabledReason;
  const creditsOffPrev = was((p) => p.credits && p.credits.enabled === false
    && p.credits.disabledReason);
  if (creditsOff && !creditsOffPrev
    && !withinDedup(lastToast, record.orgUuid, "credits", now)) {
    out.push({
      kind: "credits",
      title: "Claude usage — credits disabled",
      message: `${record.orgName}\n${record.credits.disabledReason}`,
    });
  }

  return out;
}

/** "Session 9% · resets 4h46m · Weekly 47%" — the page-open toast body. */
export function summaryToastText(record, now) {
  const s = pct(record.session && record.session.utilization);
  const w = pct(record.weekly && record.weekly.utilization);
  const sessPart = s === null ? "Session ?" : `Session ${Math.round(s)}%`;
  const resetPart = `resets ${formatCountdown(record.session && record.session.resetsAt, now)}`;
  const wkPart = w === null ? "Weekly ?" : `Weekly ${Math.round(w)}%`;
  return `${sessPart} · ${resetPart} · ${wkPart}`;
}

// --- badge ------------------------------------------------------------------- //

const COLOR_STALE = toneColor("stale");

export function formatBadgePct(p) {
  if (p === null || p === undefined) return "?";
  const n = Math.round(p);
  if (n >= 100) return "99+"; // badge clamp: >2 digits never shown
  return String(n);
}

/**
 * Badge for the ACTIVE account's session %: {text, color, title}, or null to
 * clear. Gray wins when the record is stale (>6h or 401/403'd) -- a gray badge
 * is the honest "this number is old" signal.
 *
 * Colour comes from lib/severity.js, the SAME predicate the in-page widget
 * uses, so the toolbar and the page cannot disagree about one record. They did
 * before: the badge read the API's severity string and the widget banded the
 * percentages.
 */
export function badgeFor(accounts, lastActiveOrg, now) {
  const rec = accounts && lastActiveOrg ? accounts[lastActiveOrg] : null;
  if (!rec) {
    return { text: "", color: COLOR_STALE, title: "Claude Usage Tracker — no active account yet" };
  }
  const stale = isStale(rec.asOf, now) || rec.staleSince !== null;
  const color = toneColor(toneForRecord(rec, now, isStale));
  const s = pct(rec.session && rec.session.utilization);
  const text = stale && s === null ? "" : formatBadgePct(s);
  const asOfLine = stalenessLabel(rec.asOf, now);
  return {
    text,
    color,
    title: `${rec.orgName} — as of ${asOfLine}\n${summaryToastText(rec, now)}`,
  };
}

// --- notifications ----------------------------------------------------------- //

async function notify(title, message) {
  try {
    await chrome.notifications.create({
      type: "basic",
      iconUrl: chrome.runtime.getURL("icons/icon-48.png"),
      title,
      message,
    });
  } catch { /* notifications unavailable or the worker is dying; non-fatal */ }
}

// --- report handling ---------------------------------------------------------- //

/**
 * A `cu:usage-report` from the content probe. The ONLY entry point for usage
 * data, whether the probe ran because a page opened or because the alarm
 * asked it to: both get the same normalization, storage, alert evaluation
 * and (deduped) toasts. A 401/403 marks accounts stale SILENTLY -- never a
 * toast, which is what would turn a logged-out week into notification spam.
 */
export async function handleReport(msg) {
  if (!isObj(msg) || msg.type !== "cu:usage-report") {
    return { ok: false, error: "bad-message" };
  }
  // The PROBE's clock is the snapshot time (the fetch happened there, at
  // fetchedAt); the worker's own clock is only a fallback.
  const now = typeof msg.fetchedAt === "number" && Number.isFinite(msg.fetchedAt)
    ? msg.fetchedAt
    : Date.now();
  const snap = await readState();
  const { accounts, lastToast } = snap;
  let lastActiveOrg = snap.lastActiveOrg;

  // The org list itself auth-failed: the whole cookie is gone (401) or the
  // account is not usable (403). Mark everything stale, silently.
  if (msg.orgsError && (msg.orgsError.status === 401 || msg.orgsError.status === 403)) {
    const mark = now;
    for (const uuid of Object.keys(accounts)) {
      accounts[uuid].staleSince = accounts[uuid].staleSince || mark;
    }
    await writeState({ [KEY_ACCOUNTS]: accounts });
    await updateBadge(accounts, lastActiveOrg, now);
    return { ok: true };
  }

  const nameOf = (uuid) => {
    const row = Array.isArray(msg.orgs) && msg.orgs.find((o) => o && o.uuid === uuid);
    return row && row.name ? row.name : null;
  };

  const updated = [];
  const results = Array.isArray(msg.results) ? msg.results : [];
  for (const res of results) {
    if (!isObj(res) || typeof res.orgUuid !== "string") continue;
    const prev = accounts[res.orgUuid] || null;
    if (res.ok) {
      const fresh = normalizeUsage(res.usage, res.orgUuid, nameOf(res.orgUuid)
        || (prev && prev.orgName), now);
      accounts[res.orgUuid] = mergeStored(prev, fresh, now);
      updated.push({ record: accounts[res.orgUuid], prev });
    } else if (res.status === 401 || res.status === 403) {
      // One org denied -- mark that account stale, silently.
      if (accounts[res.orgUuid]) {
        accounts[res.orgUuid].staleSince = accounts[res.orgUuid].staleSince || now;
        updated.push({ record: accounts[res.orgUuid], prev });
      }
    }
    // Any other per-org failure (network, 5xx, bad-schema): leave the stored
    // record untouched. A missed snapshot is not a state change.
  }

  const fired = [];
  for (const { record, prev } of updated) {
    for (const alert of evaluateAlerts(record, prev, now, lastToast)) {
      fired.push({ orgUuid: record.orgUuid, alert });
    }
  }

  // Summary toast for the active account (deduped like every other kind);
  // an account SWITCH gets its own kind so it always reads as news.
  const activeUuid = typeof msg.activeUuid === "string" ? msg.activeUuid : null;
  const activeRec = activeUuid ? accounts[activeUuid] : null;
  if (activeRec && activeRec.staleSince === null) {
    const switched = detectSwitch(lastActiveOrg, activeUuid);
    const kind = switched ? "switch" : "summary";
    if (!withinDedup(lastToast, activeUuid, kind, now)) {
      const prefix = switched ? "Account switched — " : "";
      fired.push({
        orgUuid: activeUuid,
        alert: {
          kind,
          title: `${prefix}${activeRec.orgName}`,
          message: summaryToastText(activeRec, now),
        },
      });
    }
  }

  const newLastToast = { ...lastToast };
  for (const { orgUuid, alert } of fired) {
    newLastToast[toastKey(orgUuid, alert.kind)] = now;
    await notify(alert.title, alert.message);
    // A switch toast IS the account summary in content: record both kinds,
    // so a page-open right after a switch cannot re-toast the same thing.
    if (alert.kind === "switch") newLastToast[toastKey(orgUuid, "summary")] = now;
  }

  if (activeUuid) lastActiveOrg = activeUuid;
  await writeState({
    [KEY_ACCOUNTS]: accounts,
    [KEY_LAST_ACTIVE]: lastActiveOrg,
    [KEY_LAST_TOAST]: newLastToast,
  });
  await updateBadge(accounts, lastActiveOrg, now);
  return { ok: true, updated: updated.length, toasts: fired.length };
}

async function updateBadge(accounts, lastActiveOrg, now) {
  try {
    const badge = badgeFor(accounts, lastActiveOrg, now);
    await chrome.action.setBadgeText({ text: badge.text });
    await chrome.action.setBadgeBackgroundColor({ color: badge.color });
    await chrome.action.setTitle({ title: badge.title });
  } catch { /* action API unavailable in some contexts */ }
}

// --- probing ------------------------------------------------------------------ //

async function askProbe(tabId) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "cu:probe" });
  } catch { // no content script in that tab (page loaded pre-install, chrome://
    // page, crashed renderer) -- its NEXT navigation auto-probes.
  }
}

export async function probeExistingTabs() {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: CLAUDE_AI_MATCH });
  } catch { return 0; }
  let asked = 0;
  for (const tab of tabs) {
    if (!tab || typeof tab.id !== "number") continue;
    // tabs.query already gates on claude.ai; ask regardless of status so an
    // alarm can use a long-lived tab.
    state.lastAskedByTab.set(tab.id, Date.now());
    await askProbe(tab.id);
    asked += 1;
  }
  return asked;
}

// --- message router ----------------------------------------------------------- //

/**
 * The single runtime.onMessage listener. Synchronous answers only (returns
 * false); anything long-running happens after the response.
 */
export function onMessage(msg, sender, sendResponse) {
  if (isObj(msg) && msg.type === "cu:usage-report") {
    void handleReport(msg).catch(() => {});
    sendResponse({ ok: true });
    return false;
  }
  if (isObj(msg) && msg.type === "cu:probe-request") {
    void probeExistingTabs().catch(() => {});
    sendResponse({ ok: true });
    return false;
  }
  return false;
}

function onTabUpdated(tabId, changeInfo, tab) {
  // status "complete" is the trigger; URL/other update events are noise.
  const status = (changeInfo && changeInfo.status) || (tab && tab.status);
  const url = (changeInfo && changeInfo.url) || (tab && tab.url);
  if (!shouldProbeTab({ url, status }, state.lastAskedByTab.get(tabId), Date.now())) return;
  state.lastAskedByTab.set(tabId, Date.now());
  void askProbe(tabId);
}

function onTabActivated(info) {
  if (!info || typeof info.tabId !== "number") return;
  chrome.tabs.get(info.tabId).then((tab) => {
    if (!shouldProbeTab({ url: tab && tab.url, status: "complete" },
      state.lastAskedByTab.get(info.tabId), Date.now())) return;
    state.lastAskedByTab.set(info.tabId, Date.now());
    return askProbe(info.tabId);
  }).catch(() => { /* tab gone */ });
}

function onAlarm(alarm) {
  if (!alarm || alarm.name !== ALARM_NAME) return;
  // The re-probe ONLY runs while a claude.ai tab exists -- tabs.query IS the
  // gate, not a pre-check followed by a broader fetch.
  void probeExistingTabs();
}

/**
 * Register EVERY listener synchronously, in the first turn of the worker.
 * Nothing in here may await. Mirrors dl-router's registerListeners().
 */
export function registerListeners() {
  chrome.tabs.onUpdated.addListener(onTabUpdated);
  chrome.tabs.onActivated.addListener(onTabActivated);
  chrome.runtime.onMessage.addListener(onMessage);
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: REPROBE_PERIOD_MIN });
  chrome.alarms.onAlarm.addListener(onAlarm);
}

/** Everything asynchronous; listeners are ALREADY registered by the time this runs. */
export async function start() {
  try {
    await probeExistingTabs();
  } catch { /* storage or tabs unavailable; the next event retries */ }
  try {
    const got = await readState();
    await updateBadge(got.accounts, got.lastActiveOrg, Date.now());
  } catch { /* badge is cosmetic */ }
}

/** Test hook / entry: re-arm the module as if the worker had just been woken. */
export function bootstrap() {
  registerListeners();          // synchronous, first turn -- never move this
  void start();
}

if (!(typeof globalThis !== "undefined" && globalThis.CLAUDE_USAGE_NO_AUTOSTART)) {
  bootstrap();
}
