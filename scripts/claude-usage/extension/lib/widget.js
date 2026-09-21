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
import {
  BLOCKED, FREE, MEASURED, activeRecord, availability, isActiveRecord,
  nextFreeAt, orderForSwitch, toneForRow,
} from "./availability.js";

// Re-exported so the widget's own callers and tests keep addressing them here
// while there is ONE implementation, in lib/severity.js, shared with the
// badge. They were local to this file until the round-0 audit showed the badge
// and the widget were deciding severity by two different rules.
export { CRIT_PCT, WARN_PCT };

// The storage key the labels live under, re-exported for the same reason:
// content_widget.js reads it to build its `storage.local.get` list, and one
// misspelling there is a silently unlabelled widget.
export { ACCOUNT_LABELS_KEY };

// 🔴 THERE IS NO `OTHERS_MAX` CAP ANY MORE, AND ADDING ONE BACK NEEDS AN
// ANSWER TO THIS. The card showed at most 4 other rows and summarised the
// rest as a "+N more" line -- which was TERMINAL: nothing in the card could
// expand it, so any row past the fourth was unreachable by any click.
//
// ⚠ WHETHER IT FIRES TODAY IS NOT MEASURED, and the argument does not rest
// on it. What IS measured (2026-09-20, the operator's LAPTOP -- the widget is
// registered in no profile on the workbench, so "is it running" is
// host-dependent): the extension is live from this repo path, its store is
// ~645 KB, and it holds 266 `resetsAt` occurrences, i.e. many real snapshots.
// Six distinct UUID-shaped values appear in it, but that is an INDICATOR and
// NOT an account count -- some may be other identifiers, and nobody has
// counted the accounts. So: a cap that MIGHT already be hiding rows, with no
// way to see them if it is. A counted-but-unexpandable row is a dead end
// whenever it occurs; it does not have to occur today to be one.
//
// The two ways out were "make +N more reachable" and "drop the cap". Making
// it reachable means a BUTTON inside the other-accounts section, and
// content_widget.test.mjs pins that there is none: round 1 shipped a widget
// that swallowed clicks meant for claude.ai's composer, and "the only
// clickable things in the card are the collapse button and the pill" is a
// deliberate invariant, not an accident. So the cap went instead, and the
// "card over his chat" constraint it existed for is now held by CSS --
// `.otherlist` in content_widget.js scrolls past a fixed max-height. Scrolled
// content is reachable; counted content is not.
//
// ⚠ NOT VERIFIED ON SCREEN. The scroll bound is CSS in a shadow root inside
// claude.ai; the node harness has no layout and this box is not the host the
// extension runs on. What is pinned is that the rule exists and carries both
// declarations, and that every row is painted inside the bounded container.

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
 * when the map holds no USABLE record, which the widget renders as its
 * waiting state rather than as zeroes.
 *
 * 🔴 THE ACTIVE LOOKUP IS `activeRecord()`, NOT A BARE MAP READ. A bare
 * `accounts[lastActiveOrg]` returns whatever sits under that key, object or
 * not: MEASURED at b97190c8 with `{A: "junk", B: <a real record>}` and
 * `lastActiveOrg = A`, the string "junk" was handed back as the record and the
 * whole widget rendered its EMPTY state -- `empty: true`, `others: []`, the
 * entire section gone -- with real data stored. It is also the predicate
 * popup.js reads, so the two surfaces cannot name different active accounts.
 */
export function pickRecord(accounts, lastActiveOrg) {
  if (!accounts || typeof accounts !== "object") return null;
  const active = activeRecord(accounts, lastActiveOrg);
  if (active !== null) return active;
  // No usable record under the active key -- either none is recorded yet (the
  // very first probe writes it after the first render) or the key names an org
  // whose snapshot never landed. Fall back to the freshest record so the
  // widget shows a real number instead of "waiting" when data plainly exists.
  const list = Object.values(accounts).filter((r) => r && typeof r === "object");
  if (!list.length) return null;
  return list.reduce((best, r) => ((r.asOf || 0) > (best.asOf || 0) ? r : best));
}

/**
 * The widget's colour band for an EXEMPT (active) record -- delegated to
 * lib/severity.js, which the toolbar badge reads too, so the two surfaces
 * cannot disagree about the account claude.ai is operating as. This wrapper
 * exists only to supply `isStale` (timefmt's, so the staleness line is also
 * decided once) and to keep the widget's own callers addressing a
 * widget-shaped name.
 *
 * 🔴 IT READS THE RECORD RAW, WITH NO SPENT-EVIDENCE RULE, AND THAT IS WHY IT
 * IS NOT THE CARD'S TONE FOR A NON-EXEMPT RECORD. `toneForRecord` bands
 * `record.session.utilization` and `record.weekly.utilization` as stored --
 * the same raw read `toneForRow` exists to avoid. For the ACTIVE account that
 * is the earned exemption (the probe re-measures it in seconds, so a stored
 * percentage describing a window that just turned over is about to be
 * replaced); for the fallback record `pickRecord` shows when `lastActiveOrg`
 * names no stored record it is not earned. MEASURED at 3f0a5506 on
 * `{session 95%, reset 2h ago; weekly 100%, reset 30m ago; asOf 10m}` with
 * `lastActiveOrg` naming a ghost org: `widgetModel().tone` was `crit` -- a red
 * pill and a red header dot over a `free` verdict -- while the SAME record as
 * an other-account row, same storage and same `now`, was `ok`. One record, one
 * instant, two colours, one surface apart.
 *
 * 🔴 DO NOT "FIX" THAT INSIDE `toneForRecord`. The toolbar badge shares it
 * (service_worker.js's `badgeFor`, which reads `accounts[lastActiveOrg]` and
 * nothing else, so it is only ever about the active account and shows an empty
 * grey badge when there is none). Applying the non-exempt rule there would
 * change what the badge means for the one account that has the exemption.
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
 * (`others: []`, `nextFree: null`), so a caller that has only one record --
 * and every pre-existing test -- keeps working unchanged.
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
      // pickRecord() returns the freshest USABLE record and yields null only
      // when the map holds none. (It used to say "only when the map is
      // EMPTY", which was false -- a bare `accounts[lastActiveOrg]` handed
      // back a junk value under the active key and the whole section
      // vanished with real data stored. Both the lookup and this sentence
      // were wrong; see pickRecord.)
      others: [],
      nextFree: null,
    };
  }
  const stale = isStale(record.asOf, now) || record.staleSince !== null;
  const sessPct = record.session && record.session.utilization;
  const wkPct = record.weekly && record.weekly.utilization;
  const cd = formatCountdown(record.session && record.session.resetsAt, now);

  // 🔴 THE ACTIVE-ACCOUNT EXEMPTION IS A PREDICATE, NOT THE CARD'S POSITION.
  // The card keeps rendering a live countdown over an already-elapsed reset
  // ("resets soon") for ONE reason: content_probe.js fetches with the CURRENT
  // session cookie, so the active account really will be re-measured within
  // seconds. For any OTHER record that justification is simply absent -- and
  // the card shows another record more often than it looks: pickRecord()
  // falls back to the freshest one whenever the active key names no stored
  // record, which service_worker.js produces whenever a first-seen org's
  // /usage fetch fails for any reason other than 401/403.
  //
  // MEASURED at b97190c8 in exactly that state: the card read
  // `Session 92% · resets soon` -- the literal pre-fix defect string -- for a
  // record the POPUP, on the same storage and the same `now`, was calling
  // AVAILABLE. So the exemption goes to the record `isActiveRecord()` names
  // and to nothing else, which is the rule popup.js's `sessionLine` already
  // applied. When no account map is supplied at all the caller has one record
  // and it is by construction the one in front of you, so it keeps it; a
  // garbage ctx degrades to that same single-record reading rather than
  // silently losing the exemption.
  //
  // 🔴 AND IT IS WITHDRAWN FOR EVERY STATE, NOT JUST `free`. For a round this
  // was `!exempt && verdict.state === FREE`, so the sentence above was false
  // as implemented and the state it most needed to cover was the one it
  // missed. MEASURED at 38bbc1f1, `lastActiveOrg` naming a ghost org and
  // `pickRecord` falling back to a weekly-blocked record: the card's session
  // row read `95% · resets soon` -- again the literal defect string -- while
  // the POPUP, same record, same `now`, read `BLOCKED · Weekly limit
  // reached. · frees up in 4d0h`. With no lock STRING on the record the card
  // said nothing about being blocked at all: `resets soon`, and an absent
  // lock banner. The same measurement found the mirror case: a session lock
  // SPENT by its own reset left the card reading `AVAILABLE` in green with
  // the red `Session limit reached.` banner still under it, because the
  // banner read the raw record rather than the verdict.
  const cx = ctx && typeof ctx === "object" ? ctx : null;
  const cxAccounts = cx && cx.accounts && typeof cx.accounts === "object" ? cx.accounts : null;
  const exempt = cxAccounts === null
    || isActiveRecord(record, cxAccounts, cx.lastActiveOrg);
  const verdict = availability(record, now);
  // The state the CARD is allowed to act on: the verdict for a record that
  // has not earned the exemption, and null for one that has (the exempt card
  // keeps its live countdown, which is the whole point of the exemption).
  const shownState = exempt ? null : verdict.state;
  // A non-exempt record whose session window has closed states the verdict,
  // exactly as an other-account row does -- and shows an EMPTY track, because
  // the stored percentage describes a window that no longer exists and a bar
  // is a claim about a current one.
  const shownFree = shownState === FREE;
  // Likewise a blocked one: the row reports a STATE, not a percentage, so
  // there is no track under it either. The last measured number stays in the
  // meta, so nothing is hidden -- it is just no longer drawn as a live bar.
  const shownBlocked = shownState === BLOCKED;

  // 🔴 EACH BAR IS COLOURED BY ITS OWN WINDOW, not by the record's tone. The
  // record tone is the WORSE of session and weekly, which is right for the
  // card's overall signal and the collapsed pill -- but painting every bar
  // with it made a 5%-wide Session bar render RED whenever the weekly window
  // was critical, i.e. the bar misreported the very window it measures.
  // Staleness still greys everything, since no individual number is
  // trustworthy once the snapshot is old.
  //
  // 🔴 WHAT YOU PASS IN MUST ALREADY HAVE THE SPENT-EVIDENCE RULE APPLIED.
  // This bands whatever number it is given; it does not know which window the
  // number came from or whether that window has since turned over. The weekly
  // row passes `verdict.weeklyBindingPct` for a non-exempt record for exactly
  // that reason -- see the row below.
  const rowTone = (v) => (stale ? "stale" : (percentTone(v, null) || "unknown"));

  // The session row's three readings, spelled once each. `blockedBecause` and
  // `blockedUntil` are the SAME helpers the other-account rows use, so the
  // card and the list cannot word a block two ways.
  let sessionValue = formatPct(sessPct);
  // "resets soon" already carries its verb; a bare countdown gets one.
  let sessionMeta = cd === "resets soon" ? cd : `resets ${cd}`;
  if (shownFree) {
    sessionValue = "AVAILABLE";
    sessionMeta = `reset ${stalenessLabel(verdict.resetsAt, now)}`
      + ` (was ${formatPct(verdict.sessionPct)}, ${measuredPart(verdict.asOf, now)})`;
  } else if (shownBlocked) {
    sessionValue = "BLOCKED";
    sessionMeta = `${blockedBecause(verdict)} · ${blockedUntil(verdict, now)}`
      + ` (session was ${formatPct(verdict.sessionPct)}, ${measuredPart(verdict.asOf, now)})`;
  }

  const rows = [
    {
      key: "session",
      label: "Session",
      value: sessionValue,
      bar: (shownFree || shownBlocked) ? null : clampPct(sessPct),
      tone: (shownFree || shownBlocked)
        ? toneForRow(record, verdict, isStale, now) : rowTone(sessPct),
      meta: sessionMeta,
    },
    {
      key: "weekly",
      label: "Weekly",
      value: formatPct(wkPct),
      bar: clampPct(wkPct),
      // 🔴 THE COLOUR READS `weeklyBindingPct` FOR A NON-EXEMPT RECORD; THE
      // VALUE AND THE BAR STILL READ THE RAW NUMBER. Those are different
      // claims: the number is the last thing that was MEASURED and hiding it
      // would fabricate an absence, while the colour is a claim about now.
      // Once `weekly.resetsAt` has passed, the stored percentage describes a
      // window that no longer exists, so `availability()` nulls
      // `weeklyBindingPct` and this row bands nothing rather than banding
      // spent evidence. MEASURED at 3f0a5506 on `{weekly 100%, reset 30m
      // ago}`: this row was `crit` on a card whose session row read
      // `AVAILABLE` and whose verdict was `free`.
      //
      // ⚠ NULL HERE HAS TWO CAUSES AND THIS DELIBERATELY DOES NOT TELL THEM
      // APART: a weekly window shown to have reset (the case above), and a
      // record that carried no usable weekly reading at all. Both land on
      // "unknown" (amber) -- which is what the second case already rendered at
      // 3f0a5506, so that half is unchanged. Mapping null to "ok" instead
      // would paint an absent reading green, which is why it is not done.
      //
      // ⚠ RESIDUE, NOT A CLOSED ARGUMENT: the header dot for this same record
      // is `toneForRow`'s free-branch "ok", so a card can show a green header
      // over an amber weekly row. That is the header stating the VERDICT and
      // the row stating that it has no current reading. Nobody has looked at
      // it on screen.
      tone: rowTone(exempt ? wkPct : verdict.weeklyBindingPct),
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
  //
  // 🔴 THE BANNER IS PART OF THE EXEMPTION TOO. For an exempt (active) record
  // it reads the record raw, which is the earned exemption: the next probe is
  // seconds away and will confirm or clear it. For a NON-exempt record it
  // reads the verdict, which applies availability.js's spent-evidence rule --
  // a session lock whose own five-hour window has since reset is not a live
  // lock. The raw read on a non-exempt record put `AVAILABLE` in green and
  // `Session limit reached.` in red on the same card (MEASURED at 38bbc1f1),
  // which is this module's founding defect in the one field the session row's
  // fix did not cover. `verdict.lockedReason` is null unless the verdict is
  // BLOCKED, and it already orders session ahead of weekly for the same
  // reason this line does.
  const locked = exempt
    ? ((record.session && record.session.lockedReason)
      || (record.weekly && record.weekly.lockedReason)
      || null)
    : verdict.lockedReason;

  const others = buildOthers(record, now, ctx);

  return {
    empty: false,
    name: accountLabel(record, labels),
    note: null,
    // 🔴 THE HEADLINE TONE IS PART OF THE EXEMPTION TOO -- the lock banner and
    // the session row already were, and this field was the one left reading
    // the record raw. It paints the collapsed pill AND the card's header dot
    // (content_widget.js's `paintCollapsed` and `paintExpanded`), so a
    // non-exempt record reddened by its own SPENT session percentage showed a
    // red pill for an account the list underneath was calling AVAILABLE in
    // green -- one record, one `now`, two colours. `toneForRow` is the SAME
    // call `otherRow` makes, so the two cannot come apart by construction
    // rather than by agreement.
    //
    // ⚠ WHAT THIS DOES NOT COVER: `pill` below is still `pillText(record)`,
    // the RAW session percentage, so the collapsed pill of a non-exempt free
    // record now reads "95%" in green. The number is the last measurement and
    // showing it is deliberate (the same reason the rows keep their raw
    // values); whether a percentage is the right thing for the pill to show
    // when the verdict is `free` has not been decided and is not decided here.
    tone: exempt ? toneFor(record, now) : toneForRow(record, verdict, isStale, now),
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
 * The "other accounts" section: every switch-to candidate, most available
 * first, plus the one-line "next free" footer.
 *
 * 🔴 THE FOOTER IS NOT REDUNDANT WITH THE FIRST MEASURED ROW, and the two
 * disagree BY CONSTRUCTION. The list is ordered by how good a switch target
 * an account is -- lowest session percentage first -- while the footer
 * answers a different question, "how long until ANY of them frees up", which
 * is ordered by TIME. An account at 23% resetting in 5h sorts above one at
 * 62% resetting in 1h12m, so the first measured row is routinely not the next
 * one to free up.
 *
 * ⚠ WHEN IT IS NULL, STATED FROM THE CODE RATHER THAN FROM THE INTENT. This
 * said "null exactly when there is nothing to wait for (everything else is
 * already free, or unknown)", and that was incomplete in BOTH directions once
 * `nextFreeAt` started reading `freesAt` instead of `resetsAt`. It is null
 * when every other account's `freesAt` is null -- free, unknown, or BLOCKED
 * WITH NO KNOWABLE END -- when there are no other accounts, and when `now`
 * itself is unusable (nothing time-relative is knowable then, including
 * this). And it is
 * non-null for a blocked account with a knowable end, which is emphatically
 * something to wait for: `next free: <label> in 4d0h` is pinned for exactly
 * that record. `the next-free line names the soonest account` pins the
 * ordering half.
 *
 * Returns `{ others, nextFree }` and never throws over garbage.
 */
function buildOthers(record, now, ctx) {
  const none = { others: [], nextFree: null };
  const c = ctx && typeof ctx === "object" ? ctx : null;
  if (!c) return none;
  const accounts = c.accounts && typeof c.accounts === "object" ? c.accounts : null;
  if (!accounts) return none;
  const labels = c.labels;
  const activeOrg = typeof c.lastActiveOrg === "string" && c.lastActiveOrg ? c.lastActiveOrg : null;

  // orderForSwitch ranks every record it is given, active one included; drop
  // the active record here AND drop whatever record the card is already
  // showing. Those are usually the same object, but not always: pickRecord()
  // falls back to the freshest record when no active org is known yet, and
  // listing the account already on screen a second time under "other
  // accounts" would be a plain lie.
  //
  // 🔴 THE ACTIVE ACCOUNT IS EXCLUDED HERE, and that exclusion is also what
  // applies availability()'s documented active-account exemption on this
  // surface: the widget's own card keeps rendering the active record's
  // countdown (formatCountdown, above), because that is the one account the
  // probe will re-measure in seconds. popup.js applies the same exemption
  // through `sessionLine`'s `isActive`.
  // ⚠ `activeRecord()` HERE IS CONSISTENCY, NOT A FIX, AND THIS COMMENT USED
  // TO CLAIM OTHERWISE. It said the bare `accounts[activeOrg]` "is the second
  // of the two spellings that made this surface and the popup name different
  // active accounts" -- but reverting THIS call site to the bare read was
  // MEASURED to SURVIVE all 251 tests, and it is observationally identical
  // here: `activeRec` is only ever used as a `!==` filter against a list
  // `orderForSwitch` has already stripped of everything `isRecord` rejects,
  // so a junk value under the active key can match nothing either way. The
  // seam that really did split the two surfaces was `pickRecord` (widget) vs
  // the popup's `orgUuid` FIELD spelling, and both of those are mutant-killed
  // by their own tests. The call stays because one predicate for "which
  // record is active" is worth having whether or not each site can observe
  // the difference; what it does NOT do is close a gap.
  const activeRec = activeRecord(accounts, activeOrg);
  const rest = orderForSwitch(accounts, now)
    .filter((r) => r !== record && r !== activeRec);

  const next = nextFreeAt(accounts, activeOrg, now);
  return {
    others: rest.map((r) => otherRow(r, labels, now)),
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
 * 🔴 AND A `blocked` ROW IS WHY THAT IS NOT ENOUGH. For one round this
 * function hardcoded `tone: "ok", stale: false` on the free branch and passed
 * `percentTone(v.sessionPct, null)` -- weekly literally `null` -- on the
 * measured one, so it ran a SECOND, narrower rule than the card above it and
 * the badge beside it. An account whose session window had reset while its
 * weekly window sat at 100% and locked therefore rendered as the single best
 * switch target, in green. The colour now comes from `toneForRow()`, which
 * composes severity.js's primitives over the constraints the verdict says
 * still bind, and the lock the ACTIVE card has always rendered is no longer
 * dropped on the way to an other-account row.
 *
 * 🔴 STALENESS DOES NOT GREY A `free` ROW -- NOR A `blocked` ONE. The existing
 * 6h staleness rule greys everything, which washes out precisely the most
 * actionable rows on the card -- and it does so BY CONSTRUCTION, since an
 * account you are not logged into cannot be re-measured at all. Both of those
 * verdicts are live inferences from a reset time rather than claims about a
 * stored percentage, so the verdict outranks staleness for styling;
 * `measured` and `unknown` rows still grey out, because for those the stored
 * percentage IS the claim being made.
 *
 * ⚠ AN OTHER-ROW HAS NO `bar` AND NO `key`, and neither is an oversight.
 * `paintOthers` draws these rows as a dot, a name and a value -- no progress
 * track -- so a `bar: clampPct(...)` on all three branches was computed for
 * nobody but the test that asserted it; only the main card's rows are barred,
 * and those still are. `key` was a list-diff id for a painter that rebuilds
 * the whole section on every render and therefore diffs nothing. Add either
 * back only with the reader that needs it in the same change.
 */
function otherRow(rec, labels, now) {
  const v = availability(rec, now);
  const name = accountLabel(rec, labels);
  // ONE call, for every branch. `toneForRow` decides which constraints bind
  // per state -- including that a free row is not reddened by its own spent
  // session percentage, and that neither a free nor a blocked row greys.
  const tone = toneForRow(rec, v, isStale, now);

  if (v.state === BLOCKED) {
    return {
      name,
      state: BLOCKED,
      // NOT "AVAILABLE", and not a percentage either: the percentage is not
      // the story when the window is shut.
      value: "BLOCKED",
      meta: `${blockedBecause(v)} · ${blockedUntil(v, now)}`
        + ` (session was ${formatPct(v.sessionPct)}, ${measuredPart(v.asOf, now)})`,
      tone,
      stale: false,
    };
  }

  if (v.state === FREE) {
    return {
      name,
      state: FREE,
      value: "AVAILABLE",
      meta: `reset ${stalenessLabel(v.resetsAt, now)}`
        + ` (was ${formatPct(v.sessionPct)}, ${measuredPart(v.asOf, now)})`,
      tone,
      stale: false,
    };
  }

  const stale = isStale(rec && rec.asOf, now)
    || !(rec && rec.staleSince === null);
  if (v.state === MEASURED) {
    return {
      name,
      state: MEASURED,
      value: formatPct(v.sessionPct),
      meta: `resets ${formatCountdown(v.resetsAt, now)} · ${measuredPart(v.asOf, now)}`,
      tone,
      stale,
    };
  }
  return {
    name,
    state: "unknown",
    value: formatPct(v.sessionPct),
    meta: `reset time unknown · ${measuredPart(v.asOf, now)}`,
    tone,
    stale,
  };
}

/** WHY a row is blocked: the API's own lock text when there is one, otherwise
 * the weekly reading that says the window is spent. Never an invented reason
 * -- an exhausted-but-unlocked weekly window states its number instead. */
function blockedBecause(v) {
  if (v.lockedReason) return v.lockedReason;
  return `weekly ${formatPct(v.weeklyPct)}`;
}

/** WHEN it frees up -- the binding constraint's own reset, which for a weekly
 * block is days out and is the whole reason the operator needs it stated. */
function blockedUntil(v, now) {
  if (v.freesAt === null) return "frees up: unknown";
  return `frees up in ${formatCountdown(v.freesAt, now)}`;
}

/** The weekly row's sub-label: its own reset countdown when the API gave one,
 * otherwise nothing (the session row already shows a countdown, and repeating
 * "unknown" twice reads as a bug).
 *
 * ⚠ THIS STILL SAYS "resets soon" FOR AN ELAPSED WEEKLY RESET, and it is now
 * the ONLY place the string this whole change exists to remove survives on a
 * non-exempt surface -- the session row above cannot produce it in any state
 * once the exemption is withdrawn (free and blocked state the verdict; a
 * measured window has a future reset by definition; an unknown one renders
 * "unknown"). MEASURED 2026-09-20 on a record whose weekly reset passed 30
 * minutes ago: the SESSION row of a non-exempt card correctly reads "resets
 * 2h0m" while this row reads "resets soon". For the ACTIVE account that is
 * the earned exemption and it is fine; for the FALLBACK record the card shows
 * when `lastActiveOrg` names no stored record it is not earned.
 *
 * 🔴 THIS RESIDUE IS THE STRING ONLY. It used to be the string AND the
 * COLOUR, and its closing condition did not say so -- "`weeklyMeta` takes the
 * same `exempt` predicate the session row does" would have replaced
 * "resets soon" with a countdown and left the row RED, because the row's tone
 * was a second, separate raw read. That half is closed: the tone above now
 * bands `verdict.weeklyBindingPct` for a non-exempt record, so a spent weekly
 * percentage no longer colours a NON-EXEMPT row. The exempt branch of that
 * same `rowTone` call still bands `wkPct` raw. Reading this note as covering
 * the colour is what made it look shut; it never did.
 *
 * NOT FIXED HERE, deliberately. A previous round left the weekly window's
 * elapsed-reset shape alone reasoning that "a weekly countdown renders only
 * on the active card"; that premise is now half-expired -- `weekly.resetsAt`
 * additionally decides whether a weekly block is spent (availability.js), and
 * now also whether this row is coloured -- but the DISPLAY claim it rests on
 * is still true, and closing it needs the weekly reset threaded through the
 * verdict with a consumer, which is a change to `availability()`'s field set
 * rather than a tweak here. It is narrow: a seven-day boundary must have
 * crossed AND the card must be showing a non-active record. Closing
 * condition, STRING ONLY: `weeklyMeta` takes the same `exempt` predicate the
 * session row does, so this row states the weekly verdict instead of
 * "resets soon", with a test watched red on that two-condition fixture. Round
 * 3 measured this row as the one remaining place the string renders (3600 of
 * 9000 swept shapes); closing it should re-derive that sweep, not edit the
 * number. */
function weeklyMeta(record, now) {
  const at = record.weekly && record.weekly.resetsAt;
  if (!at) return "";
  const cd = formatCountdown(at, now);
  if (cd === "unknown") return "";
  return cd === "resets soon" ? cd : `resets ${cd}`;
}
