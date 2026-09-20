// availability.js -- "which account can I switch to right now?"
//
// 🔴 THE DEFECT THIS MODULE EXISTS FOR. `formatCountdown()` returns
// "resets soon" for an already-elapsed reset, and its comment justifies that
// with "the next snapshot will correct it". That is true of the ACTIVE account
// and FALSE of every other one: content_probe.js fetches /api/organizations
// with the CURRENT session cookie, so a stored account can only ever be
// re-measured while you are logged into it. A second account therefore sits
// forever displaying `Session 92% · resets soon` at precisely the moment it has
// actually freed up -- the UI gets the one state the switch-accounts workflow
// depends on exactly backwards.
//
// So the reset time is read as EVIDENCE rather than as a pending event: once
// `session.resetsAt` is in the past, the five-hour window it described has
// closed, and the account is presumed FREE. That inference is safe in one
// direction only -- a reset cannot un-happen -- and it is never dressed up as
// a fresh reading: the caller is required to keep showing the last MEASURED
// percentage and its age beside the verdict (see widget.js's other-account
// rows). Nothing here fabricates a 0%.
//
// Pure, total and NEVER THROWS: every function below runs, through
// lib/widget.js, inside the operator's real claude.ai tab, over records that
// came out of chrome.storage.local and may predate any field.

import { parseWhen } from "./timefmt.js";

/** The reset has elapsed since the snapshot -- presumed available. */
export const FREE = "free";
/** The reset is still in the future -- the stored percentage is the estimate. */
export const MEASURED = "measured";
/** No parseable reset time, or no usable record. */
export const UNKNOWN = "unknown";

/** Most-available first. The ORDER is the comparison; `orderForSwitch` indexes
 * this, so a new state must be inserted at its true rank, not appended. */
export const STATE_ORDER = [FREE, MEASURED, UNKNOWN];

function finite(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** An object we are willing to read fields off. Rejects null (a property read
 * would THROW) and every primitive AND function (a callable carrying a
 * `.session` is not a stored record, and must not be read as a measurement). */
function isRecord(v) {
  return v !== null && typeof v === "object";
}

/**
 * The availability verdict for ONE stored record.
 *
 *   free      session.resetsAt <= now -- the window closed after the snapshot.
 *   measured  session.resetsAt  > now -- the stored percentage is the estimate.
 *   unknown   no parseable resetsAt, no usable record, or no usable `now`.
 *
 * 🔴 BOUNDARY, PINNED BY TEST: `resetsAt === now` is **free**. The stored value
 * is the instant the window ENDS, so at that instant it has ended; and the
 * asymmetry is deliberate -- calling a just-reset account "measured" leaves the
 * operator staring at a stale 92% one millisecond too long, while calling it
 * free one millisecond early costs nothing (the percentage and its age are
 * shown beside the verdict either way).
 *
 * Always returns the full field set, so a caller never has to guard:
 *   state, resetsAt, resetElapsedMs, msUntilReset, sessionPct, asOf, ageMs
 * Fields that do not apply to the state are null.
 */
export function availability(record, now) {
  const out = {
    state: UNKNOWN,
    resetsAt: null,
    resetElapsedMs: null,
    msUntilReset: null,
    sessionPct: null,
    asOf: null,
    ageMs: null,
  };
  if (!isRecord(record)) return out;

  const t = finite(now);
  const session = isRecord(record.session) ? record.session : null;
  out.sessionPct = finite(session && session.utilization);
  out.asOf = finite(record.asOf);
  out.ageMs = t !== null && out.asOf !== null ? t - out.asOf : null;

  const at = parseWhen(session && session.resetsAt);
  if (at === null || t === null) return out;

  out.resetsAt = at;
  if (at <= t) {
    out.state = FREE;
    out.resetElapsedMs = t - at;
    return out;
  }
  out.state = MEASURED;
  out.msUntilReset = at - t;
  return out;
}

/**
 * The accounts ordered MOST-AVAILABLE FIRST, for the in-page widget's
 * "other accounts" section.
 *
 * 🔴 THE ACTIVE ACCOUNT IS PINNED AT INDEX 0 and takes no part in the sort. It
 * is the one the operator is looking at; a row that reorders itself under them
 * as a countdown crosses a threshold is worse than a row in a boring place.
 * (popup.js's `orderAccounts` sorts by snapshot FRESHNESS, which is the exact
 * inverse of usefulness here -- the account worth switching to is by
 * construction the one measured longest ago.)
 *
 * The rest: free before measured before unknown; within `measured`, lowest
 * session percentage first, then soonest reset; within `free`, longest-elapsed
 * reset first (it has been available longest, so its last measurement is the
 * least likely to still bind). Every comparison ends in the record's index in
 * the account map, so the order is TOTAL and DETERMINISTIC -- two records that
 * tie on every visible field still have exactly one legal order.
 *
 * Total over garbage: a non-object map, a null record, a missing active org.
 */
export function orderForSwitch(accounts, lastActiveOrg, now) {
  const map = isRecord(accounts) ? accounts : {};
  const entries = Object.keys(map)
    .map((key, index) => ({ key, index, rec: map[key] }))
    .filter((e) => isRecord(e.rec));

  const activeKey = typeof lastActiveOrg === "string" && lastActiveOrg ? lastActiveOrg : null;
  const active = activeKey ? entries.find((e) => e.key === activeKey) : undefined;
  const rest = entries.filter((e) => e !== active);

  const verdicts = new Map();
  const verdict = (e) => {
    if (!verdicts.has(e)) verdicts.set(e, availability(e.rec, now));
    return verdicts.get(e);
  };

  rest.sort((a, b) => {
    const va = verdict(a);
    const vb = verdict(b);
    const byState = STATE_ORDER.indexOf(va.state) - STATE_ORDER.indexOf(vb.state);
    if (byState !== 0) return byState;

    if (va.state === FREE) {
      const ea = va.resetElapsedMs === null ? 0 : va.resetElapsedMs;
      const eb = vb.resetElapsedMs === null ? 0 : vb.resetElapsedMs;
      if (ea !== eb) return eb - ea;                    // longest free first
    } else if (va.state === MEASURED) {
      // A measured record with no usable percentage sorts LAST among the
      // measured ones: "?" is a worse switch target than a known low number.
      const pa = va.sessionPct === null ? Infinity : va.sessionPct;
      const pb = vb.sessionPct === null ? Infinity : vb.sessionPct;
      if (pa !== pb) return pa - pb;
      if (va.resetsAt !== vb.resetsAt) return va.resetsAt - vb.resetsAt;
    }
    return a.index - b.index;                           // total, deterministic
  });

  return [...(active ? [active.rec] : []), ...rest.map((e) => e.rec)];
}

/**
 * The soonest FUTURE reset among the non-active accounts -- the widget's
 * "next free: <label> in 1h12m" line. Null when nothing is pending: every
 * other account is already free, or unknown, or there are none.
 *
 * Returns `{ record, at, inMs }` rather than a string, because the label is
 * the caller's to resolve (a per-account override may rename it).
 */
export function nextFreeAt(accounts, lastActiveOrg, now) {
  const t = finite(now);
  if (t === null) return null;
  const map = isRecord(accounts) ? accounts : {};
  const activeKey = typeof lastActiveOrg === "string" && lastActiveOrg ? lastActiveOrg : null;

  let best = null;
  for (const key of Object.keys(map)) {
    if (activeKey !== null && key === activeKey) continue;
    const rec = map[key];
    if (!isRecord(rec)) continue;
    const v = availability(rec, t);
    if (v.state !== MEASURED) continue;
    // Strict `<`: on an exact tie the first in map order wins, so the answer
    // does not depend on comparison order.
    if (best === null || v.resetsAt < best.at) {
      best = { record: rec, at: v.resetsAt, inMs: v.resetsAt - t };
    }
  }
  return best;
}
