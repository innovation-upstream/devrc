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
import { ACCOUNT_LABELS_KEY, accountLabel, creditsLine, formatPct } from "./format.js";
import { CRIT_PCT, WARN_PCT, percentTone, toneForRecord } from "./severity.js";
import { FREE, MEASURED, availability, nextFreeAt, orderForSwitch } from "./availability.js";

// Re-exported so the widget's own callers and tests keep addressing them here
// while there is ONE implementation, in lib/severity.js, shared with the
// badge. They were local to this file until the round-0 audit showed the badge
// and the widget were deciding severity by two different rules.
export { CRIT_PCT, WARN_PCT };

// The storage key the labels live under, re-exported for the same reason:
// content_widget.js reads it to build its `storage.local.get` list, and one
// misspelling there is a silently unlabelled widget.
export { ACCOUNT_LABELS_KEY };

/** How many other-account rows the card will draw before collapsing the rest
 * into "+N more".
 *
 * 🔴 THE CARD SITS OVER HIS CHAT. An unbounded list is a UI hazard, not a
 * feature: five accounts is already 232px x ~180px of claude.ai covered, and
 * the rows past the first few are by construction the LEAST available ones. */
export const OTHERS_MAX = 4;

/** The host element's id. Also the handle content_widget.js uses to detect an
 * existing mount, so a double-injected content script cannot stack widgets. */
export const WIDGET_HOST_ID = "claude-usage-tracker-widget";

/** chrome.storage.local key holding the collapsed/expanded preference. */
export const COLLAPSE_KEY = "widgetCollapsed";

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
 * The widget's colour band -- delegated to lib/severity.js, which the toolbar
 * badge reads too, so the two surfaces cannot disagree about one record. This
 * wrapper exists only to supply `isStale` (timefmt's, so the staleness line is
 * also decided once) and to keep the widget's own callers addressing a
 * widget-shaped name.
 */
export function toneFor(record, now) {
  return toneForRecord(record, now, isStale);
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
 *
 * `ctx` is optional and carries everything the OTHER-ACCOUNTS section needs:
 *   { accounts, lastActiveOrg, labels }
 * Omit it and the model is exactly what it was before that section existed
 * (`others: []`, `othersMore: 0`, `nextFree: null`), so a caller that has only
 * one record -- and every pre-existing test -- keeps working unchanged.
 */
export function widgetModel(record, now, ctx) {
  const labels = ctx && typeof ctx === "object" ? ctx.labels : null;
  if (!record || typeof record !== "object") {
    return {
      empty: true,
      name: "Claude usage",
      note: "Waiting for the first snapshot…",
      // Must match what toneFor(null) says, and what the BADGE shows for the
      // same absence of data (badgeFor with no record paints grey). It read
      // "unknown"/amber here while both of those said "stale"/grey, so a
      // first-ever page load had the toolbar reporting "no data" and the page
      // reporting "warning" about the identical state -- with this file
      // containing both answers.
      tone: toneFor(null, now),
      stale: false,
      asOf: "",
      rows: [],
      pill: "?",
      locked: null,
      credits: null,
      // There is no record at all, so there are no OTHER records either:
      // pickRecord() returns the freshest of whatever is stored and only
      // yields null when the account map is empty.
      others: [],
      othersMore: 0,
      nextFree: null,
    };
  }
  const stale = isStale(record.asOf, now) || record.staleSince !== null;
  const sessPct = record.session && record.session.utilization;
  const wkPct = record.weekly && record.weekly.utilization;
  const cd = formatCountdown(record.session && record.session.resetsAt, now);

  // 🔴 EACH BAR IS COLOURED BY ITS OWN WINDOW, not by the record's tone. The
  // record tone is the WORSE of session and weekly, which is right for the
  // card's overall signal and the collapsed pill -- but painting every bar
  // with it made a 5%-wide Session bar render RED whenever the weekly window
  // was critical, i.e. the bar misreported the very window it measures.
  // Staleness still greys everything, since no individual number is
  // trustworthy once the snapshot is old.
  const rowTone = (v) => (stale ? "stale" : (percentTone(v, null) || "unknown"));

  const rows = [
    {
      key: "session",
      label: "Session",
      value: formatPct(sessPct),
      bar: clampPct(sessPct),
      tone: rowTone(sessPct),
      // "resets soon" already carries its verb; a bare countdown gets one.
      meta: cd === "resets soon" ? cd : `resets ${cd}`,
    },
    {
      key: "weekly",
      label: "Weekly",
      value: formatPct(wkPct),
      bar: clampPct(wkPct),
      tone: rowTone(wkPct),
      meta: weeklyMeta(record, now),
    },
  ];

  if (typeof record.codeWeeklyPercent === "number" && Number.isFinite(record.codeWeeklyPercent)) {
    rows.push({
      key: "code",
      label: "Claude Code",
      value: formatPct(record.codeWeeklyPercent),
      bar: clampPct(record.codeWeeklyPercent),
      // This row is a SHARE of the weekly window, not a quota of its own, so
      // it is never alarming on its own terms -- 100% of your weekly usage
      // being Claude Code says nothing about how close to a limit you are.
      tone: stale ? "stale" : "ok",
      meta: "of weekly",
    });
  }

  // A locked window is the one state where a percentage is not the story.
  // Session lock is reported ahead of weekly: it is the one blocking you now.
  const locked = (record.session && record.session.lockedReason)
    || (record.weekly && record.weekly.lockedReason)
    || null;

  const others = buildOthers(record, now, ctx);

  return {
    empty: false,
    name: accountLabel(record, labels),
    note: null,
    tone: toneFor(record, now),
    stale,
    asOf: stale ? `${stalenessLabel(record.asOf, now)} · stale` : stalenessLabel(record.asOf, now),
    rows,
    pill: pillText(record),
    locked,
    credits: creditsLine(record.credits),
    ...others,
  };
}

/**
 * The "other accounts" section: the switch-to candidates, most available
 * first, capped, plus the one-line "next free" footer.
 *
 * Returns `{ others, othersMore, nextFree }` and never throws over garbage.
 */
function buildOthers(record, now, ctx) {
  const none = { others: [], othersMore: 0, nextFree: null };
  const c = ctx && typeof ctx === "object" ? ctx : null;
  if (!c) return none;
  const accounts = c.accounts && typeof c.accounts === "object" ? c.accounts : null;
  if (!accounts) return none;
  const labels = c.labels;
  const activeOrg = typeof c.lastActiveOrg === "string" && c.lastActiveOrg ? c.lastActiveOrg : null;

  // orderForSwitch pins the active record at index 0; drop it here AND drop
  // whatever record the card is already showing. Those are usually the same
  // object, but not always: pickRecord() falls back to the freshest record
  // when no active org is known yet, and listing the account already on
  // screen a second time under "other accounts" would be a plain lie.
  const activeRec = activeOrg ? accounts[activeOrg] : null;
  const rest = orderForSwitch(accounts, activeOrg, now)
    .filter((r) => r !== record && r !== activeRec);

  const shown = rest.slice(0, OTHERS_MAX);
  const next = nextFreeAt(accounts, activeOrg, now);
  return {
    others: shown.map((r) => otherRow(r, labels, now)),
    othersMore: rest.length - shown.length,
    nextFree: next
      ? `next free: ${accountLabel(next.record, labels)} in ${formatCountdown(next.at, now)}`
      : null,
  };
}

/** "measured 6h ago", or an honest admission when the snapshot carries no
 * timestamp at all. Never the bare word "unknown", which in this position
 * reads as "the percentage is unknown" rather than "its age is". */
function measuredPart(asOf, now) {
  return typeof asOf === "number" && Number.isFinite(asOf)
    ? `measured ${stalenessLabel(asOf, now)}`
    : "never measured";
}

/**
 * One other-account row.
 *
 * 🔴 A `free` ROW IS AN INFERENCE, AND IT SAYS SO. The state is derived from a
 * reset time that has passed since the last snapshot, NOT from a fresh
 * reading -- a stored account cannot be re-measured without logging into it.
 * So the row shows the verdict, when the reset landed, AND the last measured
 * percentage with its age: "AVAILABLE — reset 2h ago (was 92%, measured 6h
 * ago)". It must never be reducible to a bare "0%", which would be a
 * fabricated measurement.
 *
 * 🔴 STALENESS DOES NOT GREY A `free` ROW. The existing 6h staleness rule
 * greys everything, which washes out precisely the most actionable row on the
 * card -- and it does so BY CONSTRUCTION, since the account worth switching to
 * is the one measured longest ago. The availability verdict outranks
 * staleness for styling; `measured` and `unknown` rows still grey out, because
 * for those the stored percentage IS the claim being made.
 */
function otherRow(rec, labels, now) {
  const v = availability(rec, now);
  const name = accountLabel(rec, labels);
  const key = rec && typeof rec.orgUuid === "string" && rec.orgUuid ? rec.orgUuid : name;

  if (v.state === FREE) {
    return {
      key,
      name,
      state: FREE,
      value: "AVAILABLE",
      meta: `reset ${stalenessLabel(v.resetsAt, now)}`
        + ` (was ${formatPct(v.sessionPct)}, ${measuredPart(v.asOf, now)})`,
      bar: clampPct(v.sessionPct),
      tone: "ok",
      stale: false,
    };
  }

  const stale = isStale(rec && rec.asOf, now)
    || !(rec && rec.staleSince === null);
  if (v.state === MEASURED) {
    return {
      key,
      name,
      state: MEASURED,
      value: formatPct(v.sessionPct),
      meta: `resets ${formatCountdown(v.resetsAt, now)} · ${measuredPart(v.asOf, now)}`,
      bar: clampPct(v.sessionPct),
      tone: stale ? "stale" : (percentTone(v.sessionPct, null) || "unknown"),
      stale,
    };
  }
  return {
    key,
    name,
    state: "unknown",
    value: formatPct(v.sessionPct),
    meta: `reset time unknown · ${measuredPart(v.asOf, now)}`,
    bar: clampPct(v.sessionPct),
    tone: stale ? "stale" : "unknown",
    stale,
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
