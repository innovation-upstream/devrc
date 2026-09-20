// widget.js -- the display model for the in-page usage widget.
//
// Everything the widget SHOWS is computed here, pure and node-tested; the DOM
// (content_widget.js) only paints what this returns. Same split as popup.js,
// for the same reason: a render bug should be catchable without a browser.
//
// 🔴 NEVER THROW. This code runs inside the operator's real claude.ai tab. A
// stored record can predate any field (extension update, partial write) and
// the usage API's schema is recon-observed, not contracted -- so every
// function here is total over garbage input. An exception escaping into the
// page is a worse failure than showing "?".
//
// Countdowns are computed at RENDER time from the raw persisted `resetsAt`,
// never stored pre-rendered, so a tab open for six hours still counts down
// correctly once the 30s tick re-renders it.

import { formatCountdown, isStale, stalenessLabel } from "./timefmt.js";
import { creditsLine, formatPct } from "./format.js";

/** The host element's id. Also the handle content_widget.js uses to detect an
 * existing mount, so a double-injected content script cannot stack widgets. */
export const WIDGET_HOST_ID = "claude-usage-tracker-widget";

/** chrome.storage.local key holding the collapsed/expanded preference. */
export const COLLAPSE_KEY = "widgetCollapsed";

/** Percent at which the widget turns amber, then red. These mirror the
 * service worker's ALERT_THRESHOLD_PCT (80) for the warn step so the widget
 * and the toast agree about what "high" means; 95 is the widget-only "almost
 * out" step, which has no toast because the 80 crossing already fired one. */
export const WARN_PCT = 80;
export const CRIT_PCT = 95;

/** 0..100, or null when unknown. Values outside the range are clamped rather
 * than dropped: a bar cannot render -3% or 140%, but the LABEL still shows the
 * raw rounded number, so a nonsense API value stays visible instead of being
 * silently normalized away. */
export function clampPct(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return null;
  return Math.max(0, Math.min(100, v));
}

/**
 * Which account the widget shows: the one claude.ai is currently operating as.
 * The widget is deliberately SINGLE-account -- it sits in the page for the
 * session you are using, and the popup is the all-accounts view. Returns null
 * when there is no record yet (first ever page load), which the widget renders
 * as its waiting state rather than as zeroes.
 */
export function pickRecord(accounts, lastActiveOrg) {
  if (!accounts || typeof accounts !== "object") return null;
  if (lastActiveOrg && accounts[lastActiveOrg]) return accounts[lastActiveOrg];
  // No active org recorded yet (the very first probe writes it after the first
  // render): fall back to the freshest record so the widget shows a real
  // number instead of "waiting" when data plainly exists.
  const list = Object.values(accounts).filter((r) => r && typeof r === "object");
  if (!list.length) return null;
  return list.reduce((best, r) => ((r.asOf || 0) > (best.asOf || 0) ? r : best));
}

/**
 * The widget's colour band. Stale wins over everything: a grey widget showing
 * an old number is honest, whereas a green one showing an old number is not.
 * Otherwise the HIGHER of session/weekly decides -- the binding constraint is
 * whichever runs out first, and showing "ok" while the weekly window is at 97%
 * would be the widget lying by omission.
 */
export function toneFor(record, now) {
  if (!record || typeof record !== "object") return "stale";
  if (isStale(record.asOf, now) || record.staleSince !== null) return "stale";
  const s = clampPct(record.session && record.session.utilization);
  const w = clampPct(record.weekly && record.weekly.utilization);
  const worst = Math.max(s === null ? -1 : s, w === null ? -1 : w);
  if (worst < 0) return "unknown";
  if (worst >= CRIT_PCT) return "crit";
  if (worst >= WARN_PCT) return "warn";
  return "ok";
}

/**
 * The collapsed pill's text: the single number worth one glance. Session
 * percent, because that is the window that gates the next message; the weekly
 * figure is one expand away. "?" when unknown rather than a fabricated 0.
 */
export function pillText(record) {
  if (!record || typeof record !== "object") return "?";
  return formatPct(record.session && record.session.utilization);
}

/**
 * Full display model for the expanded card. Pure; content_widget.js maps this
 * to nodes one-to-one and computes nothing of its own.
 */
export function widgetModel(record, now) {
  if (!record || typeof record !== "object") {
    return {
      empty: true,
      name: "Claude usage",
      note: "Waiting for the first snapshot…",
      tone: "unknown",
      stale: false,
      asOf: "",
      rows: [],
      pill: "?",
      locked: null,
      credits: null,
    };
  }
  const stale = isStale(record.asOf, now) || record.staleSince !== null;
  const sessPct = record.session && record.session.utilization;
  const wkPct = record.weekly && record.weekly.utilization;
  const cd = formatCountdown(record.session && record.session.resetsAt, now);

  const rows = [
    {
      key: "session",
      label: "Session",
      value: formatPct(sessPct),
      bar: clampPct(sessPct),
      // "resets soon" already carries its verb; a bare countdown gets one.
      meta: cd === "resets soon" ? cd : `resets ${cd}`,
    },
    {
      key: "weekly",
      label: "Weekly",
      value: formatPct(wkPct),
      bar: clampPct(wkPct),
      meta: weeklyMeta(record, now),
    },
  ];

  if (typeof record.codeWeeklyPercent === "number" && Number.isFinite(record.codeWeeklyPercent)) {
    rows.push({
      key: "code",
      label: "Claude Code",
      value: formatPct(record.codeWeeklyPercent),
      bar: clampPct(record.codeWeeklyPercent),
      meta: "of weekly",
    });
  }

  // A locked window is the one state where a percentage is not the story.
  // Session lock is reported ahead of weekly: it is the one blocking you now.
  const locked = (record.session && record.session.lockedReason)
    || (record.weekly && record.weekly.lockedReason)
    || null;

  return {
    empty: false,
    name: record.orgName || "unknown account",
    note: null,
    tone: toneFor(record, now),
    stale,
    asOf: stale ? `${stalenessLabel(record.asOf, now)} · stale` : stalenessLabel(record.asOf, now),
    rows,
    pill: pillText(record),
    locked,
    credits: creditsLine(record.credits),
  };
}

/** The weekly row's sub-label: its own reset countdown when the API gave one,
 * otherwise nothing (the session row already shows a countdown, and repeating
 * "unknown" twice reads as a bug). */
function weeklyMeta(record, now) {
  const at = record.weekly && record.weekly.resetsAt;
  if (!at) return "";
  const cd = formatCountdown(at, now);
  if (cd === "unknown") return "";
  return cd === "resets soon" ? cd : `resets ${cd}`;
}
