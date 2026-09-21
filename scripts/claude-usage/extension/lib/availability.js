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
// 🔴 ONE RULE, ONE PLACE. `availability()` is the ONLY place this verdict is
// decided, and BOTH surfaces read it: the in-page widget's other-account rows
// (lib/widget.js's `otherRow`) and the popup's per-account rows
// (popup.js's `sessionLine`). It shipped fixed in the widget and still broken
// in the popup for one round -- same storage, same `now`, two surfaces giving
// opposite answers about one account -- which is exactly the failure mode a
// predicate duplicated across call sites produces. Nothing may re-derive the
// verdict from `formatCountdown()`'s "resets soon"; that string is the bug.
//
// 🔴 AND THE VERDICT IS NOT A SESSION-WINDOW VERDICT. For one round it was:
// `availability()` read `session.resetsAt` and nothing else, so an account
// whose five-hour window had reset while its SEVEN-DAY window sat at 100% and
// locked rendered `AVAILABLE — reset 3h ago (was 95%, measured 8h ago)`, in
// green, sorted FIRST -- and logging into it is immediately weekly-blocked.
// MEASURED at b97190c8; adding `session.lockedReason: "account_suspended"`
// and a `staleSince` produced a BYTE-IDENTICAL row. The weekly window is
// SEVEN DAYS against the session's five hours, so that state is not a corner
// case, it is where a heavy operator lives for days at a time.
//
// severity.js already owned the rule this file was missing: "The binding
// constraint is whichever window runs out first, so showing 'ok' while the
// weekly window sits at 97% would be a lie by omission." So the verdict now
// answers "can I switch to this account", not "did its session window reset".
//
// 🔴 WHICH EVIDENCE SURVIVES WHICH RESET -- the rule that decides all of it.
// Every stored field is evidence about the WINDOW it was measured in, and a
// window's own reset is what spends that evidence:
//
//   * a SESSION lock or percentage is spent the moment `session.resetsAt`
//     passes. That is this module's founding inference and it is why an
//     elapsed session reset reads FREE rather than "still 92%".
//   * a WEEKLY lock or exhaustion is NOT spent by a session reset -- it is
//     spent by `weekly.resetsAt`, up to seven days later. A session reset
//     cannot clear it, so it outranks the session verdict.
//   * a reset we cannot parse has NOT been shown to have happened, so a lock
//     with an unknown end still blocks. The inference is safe in one
//     direction only.
//
// ⚠ ONE RESIDUE, NAMED RATHER THAN PAPERED OVER. The session rule is right
// for `five_hour.locked_reason` values that ARE the five-hour cap ("Session
// limit reached."), and wrong for an ACCOUNT-level state the API happens to
// surface through the same field -- a suspension does not clear at the next
// five-hour boundary, but this treats it as spent once the window turns over.
// Telling the two apart means string-matching an uncontracted field, which is
// the prose-heuristic fix RULES.md says to OFFER rather than reach for, so it
// is not done here. In practice a suspended account is almost always weekly-
// blocked too and still reads BLOCKED for that reason; a suspension with a
// healthy weekly window reads free. Pinned as a decision in widget.test.mjs's
// "a LIVE session lock is named ahead of a weekly one". Not closed.
//
// The ACTIVE account is the one documented exemption, and it is a fact about
// MEASUREMENT rather than a styling choice: it is the one account the probe
// WILL re-measure within seconds, so for it "the next snapshot will correct
// it" is true. 🔴 WHICH RECORD THAT IS IS ALSO ONE PREDICATE, `activeRecord()`
// below, and it has to be: the widget identified the active account by MAP KEY
// and the popup by the record's own `orgUuid` FIELD, and with a `lastActiveOrg`
// naming an org that has no stored record at all (service_worker.js writes it
// whether or not the /usage fetch produced one) the two answers came apart on
// screen -- the widget's card rendering the literal pre-fix string
// `Session 92% · resets soon` for a record the popup was calling AVAILABLE.
//
// Pure, total and NEVER THROWS: every function below runs, through
// lib/widget.js, inside the operator's real claude.ai tab, over records that
// came out of chrome.storage.local and may predate any field.

import { parseWhen } from "./timefmt.js";
import { percentTone, severityTone, worstTone } from "./severity.js";

/** The session reset has elapsed since the snapshot and nothing else blocks
 * -- presumed available. */
export const FREE = "free";
/** The session window is still open -- the stored percentage is the estimate. */
export const MEASURED = "measured";
/** A window is LOCKED or EXHAUSTED and its own reset has not been shown to
 * have passed. Switching here does not work, whatever the session window says. */
export const BLOCKED = "blocked";
/** No parseable reset time, or no usable record. */
export const UNKNOWN = "unknown";

/** Most-available first. The ORDER is the comparison; `orderForSwitch` indexes
 * this, so a new state must be inserted at its true rank, not appended.
 *
 * BLOCKED is last, and that is its TRUE rank rather than an append: `unknown`
 * means we cannot tell whether the account is usable, `blocked` means we know
 * it is not. A weekly-blocked account must never outrank a usable one, which
 * is what it did for a round -- it was reported as the single best switch
 * target on the card. */
export const STATE_ORDER = [FREE, MEASURED, UNKNOWN, BLOCKED];

/** Weekly utilization at or above this is EXHAUSTED.
 *
 * ⚠ NOT A BAND, and deliberately not kept beside severity.js's WARN_PCT /
 * CRIT_PCT. Those are tuned thresholds that decide a COLOUR; this is the
 * endpoint of the scale the API reports in -- 100% of your allowance used is
 * out, by definition of the unit -- and it decides a STATE. Moving it is a
 * different kind of act from re-tuning a band, so it lives here, where the
 * state is decided. */
export const WEEKLY_EXHAUSTED_PCT = 100;

function finite(v) {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** An object we are willing to read fields off.
 *
 * 🔴 BOTH HALVES EXIST TO STOP A THROW, and this file must never throw -- it
 * runs, through lib/widget.js, inside the operator's real claude.ai tab.
 * MEASURED, one mutant at a time against `availability is TOTAL over garbage
 * records` filtered to that test alone: drop `v !== null` and `null.session`
 * throws; drop `typeof v === "object"` and `undefined.session` throws, since
 * `undefined !== null` is true and the first half does not cover it.
 *
 * ⚠ THE OTHER PRIMITIVES DO NOT CARRY THIS GUARD, and an earlier draft of
 * this comment said they did. `"nonsense"`, `42`, `true` and `NaN` all answer
 * `undefined` to a `.session` read without throwing, so they reach UNKNOWN
 * with or without the typeof half -- they are breadth in the fixture list,
 * not the kill. `undefined` is the single input that carries it, which is why
 * it may not be dropped from that list.
 *
 * ⚠ It also rejects a FUNCTION, but that is a side effect and not a reason:
 * `chrome.storage.local` round-trips JSON and can never hand one back. A
 * separate test asserting the callable case was deleted in round 3 -- it
 * killed no mutant the garbage-record test does not already kill on its own
 * (re-measured after the deletion), and it read as coverage of a hazard that
 * cannot occur. */
function isRecord(v) {
  return v !== null && typeof v === "object";
}

/** One stored window (`session` / `weekly`) read defensively. */
function windowOf(record, key) {
  const w = isRecord(record[key]) ? record[key] : null;
  const reason = w && w.lockedReason;
  return {
    pct: finite(w && w.utilization),
    at: parseWhen(w && w.resetsAt),
    locked: typeof reason === "string" && reason ? reason : null,
  };
}

/**
 * The availability verdict for ONE stored record.
 *
 *   free      the session window closed after the snapshot, and nothing else
 *             blocks -- presumed available.
 *   measured  the session window is still open; the stored percentage is the
 *             estimate.
 *   blocked   a window is LOCKED or EXHAUSTED and its own reset has not been
 *             shown to have passed.
 *   unknown   no parseable session resetsAt, no usable record, or no usable
 *             `now`.
 *
 * 🔴 BLOCKED OUTRANKS THE SESSION VERDICT, per the "which evidence survives
 * which reset" rule in this file's header. A weekly lock or a weekly window at
 * WEEKLY_EXHAUSTED_PCT is spent by `weekly.resetsAt` -- up to seven days out --
 * so an elapsed FIVE-HOUR reset cannot clear it. A SESSION lock is spent by the
 * session's own reset, so it blocks only while that window is still open;
 * treating it as permanent would resurrect the very defect this module exists
 * for, one field over.
 *
 * 🔴 BOUNDARY, PINNED BY TEST: `resetsAt === now` is **free**. The stored value
 * is the instant the window ENDS, so at that instant it has ended; and the
 * asymmetry is deliberate -- calling a just-reset account "measured" leaves the
 * operator staring at a stale 92% one millisecond too long, while calling it
 * free one millisecond early costs nothing (the percentage and its age are
 * shown beside the verdict either way).
 *
 * Always returns the full field set, so a caller never has to guard:
 *   state, resetsAt, resetElapsedMs, sessionPct, weeklyPct, asOf,
 *   lockedReason, freesAt
 * Fields that do not apply to the state are null.
 *
 * ⚠ EVERY FIELD HERE HAS A CONSUMER, and that is the rule for adding one.
 * `msUntilReset` and `ageMs` were returned for a round with zero references
 * outside this file -- the caller recomputed both from the raw record through
 * `formatCountdown()` and `stalenessLabel()` -- so they were deleted rather
 * than wired up. Today: `state` (widget.js's `otherRow` + card, `toneForRow`,
 * `orderForSwitch`, `nextFreeAt`, popup.js's `sessionLine`), `resetsAt`
 * (`otherRow`'s "reset 2h ago", the measured tiebreak), `resetElapsedMs` (the
 * longest-free-first sort), `sessionPct` (`otherRow`, `sessionLine`,
 * `toneForRow`, the measured sort), `weeklyPct` (`toneForRow`, and
 * `otherRow`'s "weekly 100%" when nothing was locked but the window is spent),
 * `asOf` (`otherRow`'s "measured 6h ago"), `lockedReason` (the blocked row's
 * and the blocked popup line's text -- the ACTIVE card already rendered a lock
 * and every other row dropped it), `freesAt` (`nextFreeAt`, the blocked
 * ordering, and the blocked row's "frees up in 4d0h").
 */
export function availability(record, now) {
  const out = {
    state: UNKNOWN,
    resetsAt: null,
    resetElapsedMs: null,
    sessionPct: null,
    weeklyPct: null,
    asOf: null,
    lockedReason: null,
    freesAt: null,
  };
  if (!isRecord(record)) return out;

  const session = windowOf(record, "session");
  const weekly = windowOf(record, "weekly");
  out.sessionPct = session.pct;
  out.weeklyPct = weekly.pct;
  out.asOf = finite(record.asOf);

  const t = finite(now);
  // No usable clock: nothing time-relative is knowable, and that includes
  // whether a lock has expired. The record's own fields stay reported.
  if (t === null) return out;

  if (session.at !== null) out.resetsAt = session.at;

  // A window is OPEN when its reset has not been SHOWN to have passed -- an
  // unparseable reset time leaves it open, because a lock we cannot prove
  // expired must not be dismissed.
  const sessionOpen = session.at === null || session.at > t;
  const weeklyOpen = weekly.at === null || weekly.at > t;
  const sessionLock = sessionOpen ? session.locked : null;
  const weeklyLock = weeklyOpen ? weekly.locked : null;
  const weeklyExhausted = weeklyOpen
    && weekly.pct !== null && weekly.pct >= WEEKLY_EXHAUSTED_PCT;

  if (sessionLock || weeklyLock || weeklyExhausted) {
    out.state = BLOCKED;
    // The API's OWN words, session first (it is the one blocking you now),
    // and never a sentence of ours: an exhausted-but-unlocked weekly window
    // has no reason string, and the row says "weekly 100%" from `weeklyPct`
    // rather than being handed an invented one.
    out.lockedReason = sessionLock || weeklyLock;
    // Usable again once EVERY blocking window has reset; any blocker with no
    // knowable end makes the whole answer unknown rather than optimistic.
    let at = null;
    let unknownEnd = false;
    const needs = (reset) => {
      if (reset === null) unknownEnd = true;
      else if (at === null || reset > at) at = reset;
    };
    if (sessionLock) needs(session.at);
    if (weeklyLock || weeklyExhausted) needs(weekly.at);
    out.freesAt = unknownEnd ? null : at;
    return out;
  }

  if (session.at === null) return out;
  if (session.at <= t) {
    out.state = FREE;
    // 🔴 ALWAYS a number on a FREE verdict, never null -- `orderForSwitch`'s
    // longest-free-first comparison reads it without a guard, and the
    // null-coalescing ternaries that used to stand in for this invariant were
    // dead code that no mutation could kill. `every verdict carries the full
    // field set` pins it.
    out.resetElapsedMs = t - session.at;
    return out;
  }
  out.state = MEASURED;
  out.freesAt = session.at;
  return out;
}

/**
 * THE active record, or null. One predicate, because two spellings of it is
 * how the widget and the popup came to disagree on screen.
 *
 * 🔴 THE MAP KEY IS AUTHORITATIVE, not the record's own `orgUuid` field. The
 * service worker writes `accounts[uuid]` and `lastActiveOrg = uuid` from the
 * same value in one `writeState`, so the key is the claim; the field is a copy
 * that a partial or older write can disagree with.
 *
 * 🔴 A KEY NAMING NO USABLE RECORD IS NOT ACTIVE -- it is null, and callers
 * must fall back rather than treat some other account as active.
 * service_worker.js sets `lastActiveOrg` whether or not the /usage fetch
 * produced a record (a network error, a 5xx, or a non-JSON 200 on a
 * first-seen org leaves it absent), so this is a real stored state and not a
 * defensive flourish. `hasOwnProperty`, for the reason severity.js's
 * SEVERITY_TONES lookup documents: `accounts["constructor"]` answers something
 * truthy off Object.prototype.
 */
export function activeRecord(accounts, lastActiveOrg) {
  const map = isRecord(accounts) ? accounts : {};
  const key = typeof lastActiveOrg === "string" && lastActiveOrg ? lastActiveOrg : null;
  if (key === null) return null;
  const rec = Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null;
  return isRecord(rec) ? rec : null;
}

/** Is THIS record the active one? Identity against `activeRecord()`, so both
 * surfaces ask one question and get one answer. */
export function isActiveRecord(record, accounts, lastActiveOrg) {
  const active = activeRecord(accounts, lastActiveOrg);
  return active !== null && record === active;
}

/**
 * The tone for one NON-active account row.
 *
 * 🔴 THE SAME RULE severity.js's `toneForRecord` APPLIES -- the WORST of the
 * constraints -- over the constraints that still BIND. Which ones bind is what
 * the verdict decides, and it is not the same set for every state, which is
 * why this cannot simply be `toneForRecord`:
 *
 *   blocked   crit, unconditionally. A locked or exhausted window is not a
 *             degree of "high usage"; the account cannot be used at all.
 *   free      the session window RESET, so its percentage is SPENT evidence
 *             and must not colour anything -- a 91%-then-reset account is not
 *             red. The weekly window is the one constraint provably unspent by
 *             a session reset, so it alone decides. The API's `severity`
 *             string is deliberately NOT consulted here: normalize.js collapses
 *             it to one value across windows, so it cannot be shown to have
 *             survived the session reset, and letting it in would re-redden the
 *             row on the same spent evidence. Staleness does not grey a free
 *             row (widget.js records why: the best switch target is stale BY
 *             CONSTRUCTION).
 *   measured  every constraint binds, staleness included -- here the stored
 *             session percentage IS the claim being made.
 *   unknown   as measured.
 *
 * ⚠ A free row with no usable weekly reading is "ok", not "unknown". The
 * verdict itself is the affirmative claim being made; amber would report doubt
 * about the one row the operator is meant to act on.
 */
export function toneForRow(record, verdict, isStaleFn, now) {
  const state = verdict && verdict.state;
  if (state === BLOCKED) return "crit";
  if (state === FREE) return percentTone(null, verdict.weeklyPct) || "ok";
  const rec = isRecord(record) ? record : null;
  const stale = isStaleFn(rec && rec.asOf, now) || !(rec && rec.staleSince === null);
  if (stale) return "stale";
  const votes = [
    severityTone(rec && rec.severity),
    percentTone(verdict && verdict.sessionPct, verdict && verdict.weeklyPct),
  ].filter((tone) => tone !== null);
  if (!votes.length) return "unknown";
  return votes.reduce(worstTone);
}

/**
 * The accounts ordered MOST-AVAILABLE FIRST, for the in-page widget's
 * "other accounts" section.
 *
 * Free before measured before unknown before blocked; within `measured`,
 * lowest session percentage first, then soonest reset; within `free`,
 * longest-elapsed reset first (it has been available longest, so its last
 * measurement is the least likely to still bind); within `blocked`, the one
 * that frees up soonest.
 *
 * ⚠ THE TOTAL ORDER COMES FROM THE SORT'S STABILITY, NOT FROM A TIEBREAK, and
 * saying otherwise was a false claim this file carried for a round. It ended
 * every comparison in `a.index - b.index` and called that what made the order
 * "TOTAL and DETERMINISTIC" -- but `Array.prototype.sort` has been stable by
 * spec since ES2019, so replacing that expression with `return 0` was
 * MEASURED to change no output and to survive all 217 tests. The expression,
 * the `index` field it read and the entry wrapper that carried it were
 * therefore deleted rather than re-justified: two records that tie on every
 * rule keep their map-insertion order because the sort leaves them there.
 * That output property is still worth pinning, and is -- as an invariant
 * guard on the engine's guarantee, labelled as one in the test.
 *
 * ⚠ IT DOES NOT KNOW ABOUT THE ACTIVE ACCOUNT, deliberately. It used to pin
 * the active record at index 0 and exempt it from the sort, and that pinning
 * was UNOBSERVABLE in the shipped UI: the sole caller (lib/widget.js's
 * `buildOthers`) filters the active record out of the result on the very next
 * line, because a widget whose card already shows that account must not list
 * it again underneath. A contract nothing can observe is a contract nobody can
 * get right, so it was removed rather than wired up. Removing it changes no
 * output -- dropping one element from a sorted list leaves the others in the
 * same relative order.
 *
 * (popup.js sorts its own rows by snapshot FRESHNESS, which is the exact
 * inverse of usefulness here -- the account worth switching to is by
 * construction the one measured longest ago. That is a deliberate difference
 * of LAYOUT between the two surfaces, not of verdict; the verdict is
 * `availability()`, which both read.)
 *
 * Total over garbage: a non-object map, a null record, an unusable `now`.
 */
export function orderForSwitch(accounts, now) {
  const map = isRecord(accounts) ? accounts : {};
  const list = Object.keys(map).map((key) => map[key]).filter(isRecord);

  const verdicts = new Map();
  const verdict = (rec) => {
    if (!verdicts.has(rec)) verdicts.set(rec, availability(rec, now));
    return verdicts.get(rec);
  };

  list.sort((a, b) => {
    const va = verdict(a);
    const vb = verdict(b);
    const byState = STATE_ORDER.indexOf(va.state) - STATE_ORDER.indexOf(vb.state);
    if (byState !== 0) return byState;

    if (va.state === FREE) {
      // Both sides are FREE here, and a FREE verdict always carries a numeric
      // resetElapsedMs (availability() sets it on that branch and the field-set
      // test pins it), so neither read needs a null guard.
      if (va.resetElapsedMs !== vb.resetElapsedMs) {
        return vb.resetElapsedMs - va.resetElapsedMs;   // longest free first
      }
    } else if (va.state === MEASURED) {
      // A measured record with no usable percentage sorts LAST among the
      // measured ones: "?" is a worse switch target than a known low number.
      const pa = va.sessionPct === null ? Infinity : va.sessionPct;
      const pb = vb.sessionPct === null ? Infinity : vb.sessionPct;
      if (pa !== pb) return pa - pb;
      if (va.resetsAt !== vb.resetsAt) return va.resetsAt - vb.resetsAt;
    } else if (va.state === BLOCKED) {
      // Soonest to free up first; a block with no knowable end sorts last,
      // because "blocked until we cannot say" is the worst thing to wait on.
      const fa = va.freesAt === null ? Infinity : va.freesAt;
      const fb = vb.freesAt === null ? Infinity : vb.freesAt;
      if (fa !== fb) return fa - fb;
    }
    return 0;      // tie -> map-insertion order, by the sort's own stability
  });

  return list;
}

/**
 * The soonest instant at which SOME non-active account becomes usable -- the
 * widget's "next free: <label> in 1h12m" line. Null when nothing is pending:
 * every other account is already free, or blocked with no knowable end, or
 * unknown, or there are none.
 *
 * 🔴 IT READS `freesAt`, NOT `resetsAt`, AND THAT IS WHY A BLOCKED ACCOUNT
 * COUNTS. A weekly-blocked account is exactly the case where the operator
 * needs a wait time, and answering "nothing to wait for" while every other
 * account is locked for four days is a lie the caller renders as an absent
 * line. `availability()` owns what unblocks a record; this only takes the
 * minimum, so the blocking rule is not spelled a second time here.
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
    if (v.freesAt === null) continue;
    // 🔴 STRICT `<`, and it is load-bearing: on an exact tie the FIRST in map
    // order wins, so the answer does not depend on comparison order. Pinned by
    // `two accounts freeing at the SAME instant` -- it was documented as
    // load-bearing and unpinned for a round, and `<=` (which hands the tie to
    // the LAST record instead) survived the whole suite.
    if (best === null || v.freesAt < best.at) {
      best = { record: rec, at: v.freesAt, inMs: v.freesAt - t };
    }
  }
  return best;
}
