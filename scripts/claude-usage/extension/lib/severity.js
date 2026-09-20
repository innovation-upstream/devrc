// severity.js -- THE one rule for "how bad is this account's usage right now".
//
// Read by the toolbar badge, and by the injected in-page widget. Both used to
// decide it for themselves and they did not agree:
//
//   * the badge coloured from the API's own `limits[].severity` string;
//   * the widget banded the raw utilization percentages.
//
// One record could therefore be green on the toolbar and red in the page, and
// the widget's comment asserted the opposite ("can never disagree about
// severity") on the strength of the two sharing a PALETTE. A matching palette
// is not a matching predicate. RULES.md: "A predicate duplicated across call
// sites regenerates the same bug at every site... Consolidation is also a
// BUG-FINDING instrument" -- and consolidating these two is exactly what made
// the disagreement audible.
//
// 🔴 THE DISAGREEMENT WAS NOT SYMMETRIC -- the badge was the WRONG one, and
// silently so. `severityColor()` mapped a missing/empty severity to GREEN, so
// an account at 99% whose payload carried no severity row (recon saw
// `limits: null` as a real shape -- see fixtures' nullUsage) painted a calm
// green badge. The percent band had no such hole. So this module takes the
// WORSE of the two signals rather than picking a winner: a severity string can
// only ever escalate the percent band, never calm it.

/** Percent at which usage is "high", then "almost out".
 *
 * WARN_PCT is pinned by test to the service worker's ALERT_THRESHOLD_PCT: the
 * number that turns a surface amber and the number that fires a toast must be
 * the same, or the operator sees a colour change with no notification (or the
 * reverse) and has to work out which one is lying. CRIT_PCT has no toast --
 * the 80 crossing already fired one, and a second alert at 95 would be noise. */
export const WARN_PCT = 80;
export const CRIT_PCT = 95;

/** Tones, worst-first. The ORDER is the comparison: `worstTone` indexes this. */
export const TONE_ORDER = ["stale", "crit", "warn", "unknown", "ok"];

/** The API's severity strings -> our bands. Uncontracted and recon-observed,
 * so an unrecognised non-empty string is "unknown" (amber) rather than "ok":
 * a severity the API invented later must not read as calm. */
const SEVERITY_TONES = {
  critical: "crit",
  high: "crit",
  elevated: "warn",
  medium: "warn",
  low: "ok",
  none: "ok",
};

/** One colour per tone. The ONLY place these hexes are written down. */
const TONE_COLORS = {
  ok: "#31a73c",
  warn: "#f9ab00",
  crit: "#d93025",
  unknown: "#f9ab00",
  stale: "#80868b",
};

export function toneColor(tone) {
  return TONE_COLORS[tone] || TONE_COLORS.unknown;
}

/** The worse (earlier in TONE_ORDER) of two tones. */
export function worstTone(a, b) {
  const ia = TONE_ORDER.indexOf(a);
  const ib = TONE_ORDER.indexOf(b);
  if (ia < 0) return b;
  if (ib < 0) return a;
  return ia <= ib ? a : b;
}

/**
 * 🔴 NO SIGNAL AND AN UNREADABLE SIGNAL ARE DIFFERENT, and conflating them is
 * a bug in BOTH directions. Each of the two source functions below returns:
 *
 *   null        -- this source said nothing. It must not contribute a level;
 *                  the other source decides alone.
 *   "unknown"   -- this source said something we cannot interpret. That IS
 *                  information (amber), and must not be silently dropped.
 *
 * Getting this wrong the first way is the badge's original hole: absent
 * severity treated as "ok" (green at 99%). Getting it wrong the SECOND way is
 * the bug I wrote while fixing the first: absent severity treated as a level
 * of "unknown", which dragged a perfectly good 40% reading to amber because
 * one of its two inputs was quiet. A source with nothing to say gets no vote.
 */

/** The API severity string alone -> a tone, or null when the payload carried
 * no severity at all (`limits: null` is a shape recon observed). An
 * unrecognised NON-EMPTY string is "unknown", not null: a severity the API
 * invented later is a real signal we cannot read, and must not read as calm. */
export function severityTone(severity) {
  if (typeof severity !== "string" || !severity) return null;
  return SEVERITY_TONES[severity] || "unknown";
}

/** The higher of two utilizations -> a tone, or null when neither is usable.
 * The binding constraint is whichever window runs out first, so showing "ok"
 * while the weekly window sits at 97% would be a lie by omission. */
export function percentTone(session, weekly) {
  const s = num(session);
  const w = num(weekly);
  const worst = Math.max(s === null ? -1 : s, w === null ? -1 : w);
  if (worst < 0) return null;
  if (worst >= CRIT_PCT) return "crit";
  if (worst >= WARN_PCT) return "warn";
  return "ok";
}

function num(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/**
 * The tone for one stored record. THE entry point -- badge and widget both
 * call this and therefore cannot disagree.
 *
 * Stale outranks everything: a green surface showing a six-hour-old number is
 * dishonest where a grey one is not, and that is true of the toolbar exactly
 * as it is of the page. Otherwise: the WORSE of what the API said and what the
 * percentages say. Total over garbage -- a record this cannot read is "stale",
 * the one tone that claims nothing about usage.
 */
export function toneForRecord(record, now, isStaleFn) {
  if (!record || typeof record !== "object") return "stale";
  const stale = isStaleFn(record.asOf, now) || record.staleSince !== null;
  if (stale) return "stale";
  // Only sources that actually said something get a vote (see the note above
  // the two of them). Nothing said at all -> "unknown": we genuinely do not
  // know, which is amber, not green.
  const votes = [
    severityTone(record.severity),
    percentTone(
      record.session && record.session.utilization,
      record.weekly && record.weekly.utilization,
    ),
  ].filter((t) => t !== null);
  if (!votes.length) return "unknown";
  return votes.reduce(worstTone);
}
