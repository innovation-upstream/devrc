// widget.test.mjs -- the pure display model behind the injected in-page widget.
//
// The DOM mount (content_widget.js) paints this model one-to-one and computes
// nothing of its own, so everything the operator SEES on claude.ai is pinned
// here: which account is shown, the colour band, the bar geometry, the
// countdowns, and the states that must NOT render as a number (unknown,
// stale, locked, waiting).
//
// The fixtures are synthetic (see fixtures.mjs); nothing here is a capture.
import test from "node:test";
import assert from "node:assert/strict";

const W = await import("../extension/lib/widget.js");
const { TONE_ORDER: TONES } = await import("../extension/lib/severity.js");
const { NAME_A, NAME_B, NAME_C, NAME_D, NOW, ORG_A, ORG_B, ORG_C, ORG_D, fullUsage, nullUsage } =
  await import("./fixtures.mjs");
const { normalizeUsage } = await import("../extension/lib/normalize.js");

function rec(orgUuid, orgName, asOf = NOW, raw = fullUsage()) {
  return normalizeUsage(raw, orgUuid, orgName, asOf);
}

// --- which account -------------------------------------------------------------- //

test("pickRecord shows the account claude.ai is operating as", () => {
  const accounts = { [ORG_A]: rec(ORG_A, NAME_A), [ORG_B]: rec(ORG_B, NAME_B) };
  assert.equal(W.pickRecord(accounts, ORG_B).orgUuid, ORG_B);
  assert.equal(W.pickRecord(accounts, ORG_A).orgUuid, ORG_A);
});

test("pickRecord falls back to the freshest record before any active org is known", () => {
  // The first ever probe writes lastActiveOrg AFTER the first render, so this
  // path decides whether a brand-new user sees data or a waiting state.
  const accounts = {
    [ORG_A]: rec(ORG_A, NAME_A, NOW - 60 * 60 * 1000),
    [ORG_B]: rec(ORG_B, NAME_B, NOW),
  };
  assert.equal(W.pickRecord(accounts, null).orgUuid, ORG_B, "freshest wins");
  assert.equal(W.pickRecord(accounts, "no-such-org").orgUuid, ORG_B,
    "an active org with no stored record falls back too");
});

test("pickRecord is total over garbage and empty input", () => {
  assert.equal(W.pickRecord(null, ORG_A), null);
  assert.equal(W.pickRecord({}, ORG_A), null);
  assert.equal(W.pickRecord("nonsense", null), null);
  assert.equal(W.pickRecord([], null), null);
});

// --- the colour band ------------------------------------------------------------- //

// 🔴 These two set `severity = null` on purpose, to isolate the PERCENT half
// of the rule. They did not, at first, and both failed the moment toneFor
// started consulting the API severity as well: fullUsage() carries a
// `weekly_all` limit row with severity "medium", so the fixture's API signal
// says "warn" while its percentages say "ok". That disagreement is exactly
// what the consolidation exists to surface -- it was previously invisible
// because the widget read one side and the badge read the other. The
// interaction between the two halves is pinned separately below.
test("toneFor: the WORSE of session and weekly decides the band", () => {
  // The binding constraint is whichever window runs out first. A green widget
  // while the weekly window sits at 97% would be a lie by omission.
  const r = rec(ORG_A, NAME_A);
  r.severity = null;
  r.session.utilization = 5;
  r.weekly.utilization = 97;
  assert.equal(W.toneFor(r, NOW), "crit");
  r.weekly.utilization = 85;
  assert.equal(W.toneFor(r, NOW), "warn");
  r.weekly.utilization = 40;
  assert.equal(W.toneFor(r, NOW), "ok");
});

test("toneFor: thresholds are inclusive at exactly 80 and 95", () => {
  const r = rec(ORG_A, NAME_A);
  r.severity = null;
  r.weekly.utilization = null;
  r.session.utilization = 79.4;
  assert.equal(W.toneFor(r, NOW), "ok", "79.4 is below the warn line");
  r.session.utilization = W.WARN_PCT;
  assert.equal(W.toneFor(r, NOW), "warn");
  r.session.utilization = 94.9;
  assert.equal(W.toneFor(r, NOW), "warn");
  r.session.utilization = W.CRIT_PCT;
  assert.equal(W.toneFor(r, NOW), "crit");
});

test("toneFor: the API severity and the percent band, worst of the two", () => {
  const r = rec(ORG_A, NAME_A);
  r.session.utilization = 5;
  r.weekly.utilization = 5;

  r.severity = "critical";
  assert.equal(W.toneFor(r, NOW), "crit", "a loud API severity escalates a calm percentage");
  r.severity = "low";
  assert.equal(W.toneFor(r, NOW), "ok");

  r.session.utilization = 99;
  r.weekly.utilization = 99;
  assert.equal(W.toneFor(r, NOW), "crit",
    "a 'low' severity must NOT calm a 99% percentage");
  r.severity = null;
  assert.equal(W.toneFor(r, NOW), "crit",
    "and neither may an ABSENT one -- the badge's old green-at-99% hole");
});

test("toneFor: stale outranks every percentage", () => {
  // A green widget showing a six-hour-old number is dishonest; grey is not.
  const r = rec(ORG_A, NAME_A, NOW - 7 * 60 * 60 * 1000);
  r.session.utilization = 2;
  assert.equal(W.toneFor(r, NOW), "stale", "age alone");

  const auth = rec(ORG_A, NAME_A, NOW);
  auth.session.utilization = 2;
  auth.staleSince = NOW - 1000;
  assert.equal(W.toneFor(auth, NOW), "stale", "a 401/403 is stale even when fresh");

  const crit = rec(ORG_A, NAME_A, NOW - 7 * 60 * 60 * 1000);
  crit.session.utilization = 99;
  assert.equal(W.toneFor(crit, NOW), "stale", "stale beats crit, not the other way round");
});

test("toneFor: no numbers at all is 'unknown', never 'ok'", () => {
  const r = rec(ORG_A, NAME_A, NOW, nullUsage());
  assert.equal(W.toneFor(r, NOW), "unknown");
  assert.equal(W.toneFor(null, NOW), "stale");
  assert.equal(W.toneFor("nonsense", NOW), "stale");
});

// --- bar geometry ----------------------------------------------------------------- //

test("clampPct keeps a bar renderable without inventing a reading", () => {
  assert.equal(W.clampPct(0), 0);
  assert.equal(W.clampPct(47), 47);
  assert.equal(W.clampPct(100), 100);
  assert.equal(W.clampPct(140), 100, "over-100 clamps rather than overflowing the track");
  assert.equal(W.clampPct(-3), 0);
  assert.equal(W.clampPct(null), null, "unknown stays null -- an EMPTY track, not a zero one");
  assert.equal(W.clampPct("47"), null, "a string is not a measurement");
  assert.equal(W.clampPct(NaN), null);
  assert.equal(W.clampPct(Infinity), null);
});

test("an out-of-range value still shows its RAW number in the label", () => {
  // Clamping is a rendering concern. Hiding the real figure would make an API
  // regression invisible exactly when it matters.
  const r = rec(ORG_A, NAME_A);
  r.session.utilization = 140;
  const m = W.widgetModel(r, NOW);
  const row = m.rows.find((x) => x.key === "session");
  assert.equal(row.bar, 100, "bar clamps");
  assert.equal(row.value, "140%", "label does not");
});

// --- the model --------------------------------------------------------------------- //

test("widgetModel renders the headline rows with live countdowns", () => {
  const r = rec(ORG_A, NAME_A);
  r.session.resetsAt = new Date(NOW + 4 * 3600 * 1000 + 46 * 60 * 1000).toISOString();
  const m = W.widgetModel(r, NOW);
  assert.equal(m.name, NAME_A);
  assert.equal(m.empty, false);

  const sess = m.rows.find((x) => x.key === "session");
  assert.equal(sess.label, "Session");
  assert.equal(sess.value, "9%");
  assert.equal(sess.bar, 9);
  assert.equal(sess.meta, "resets 4h46m");

  const wk = m.rows.find((x) => x.key === "weekly");
  assert.equal(wk.value, "47%");
  assert.equal(wk.bar, 47);
});

test("widgetModel includes the Claude Code share only when the API gave one", () => {
  const r = rec(ORG_A, NAME_A);
  assert.equal(W.widgetModel(r, NOW).rows.find((x) => x.key === "code").value, "31%");
  r.codeWeeklyPercent = null;
  assert.equal(W.widgetModel(r, NOW).rows.find((x) => x.key === "code"), undefined,
    "no row rather than a '?' row");
});

test("an expired reset says 'resets soon' without doubling the verb", () => {
  const r = rec(ORG_A, NAME_A);
  r.session.resetsAt = new Date(NOW - 1000).toISOString();
  const sess = W.widgetModel(r, NOW).rows.find((x) => x.key === "session");
  assert.equal(sess.meta, "resets soon");
  assert.ok(!sess.meta.includes("resets resets"));
});

test("an unknown weekly reset renders NOTHING, not the word 'unknown' twice", () => {
  const weeklyMeta = (at) => {
    const r = rec(ORG_A, NAME_A);
    r.weekly.resetsAt = at;
    return W.widgetModel(r, NOW).rows.find((x) => x.key === "weekly").meta;
  };
  // Two DIFFERENT routes to "no countdown", and they hit different branches.
  // An earlier version of this test only passed null, which the `if (!at)`
  // guard catches first -- so the unparseable-string branch below was never
  // executed and a mutant that made it emit "unknown" SURVIVED a green suite.
  assert.equal(weeklyMeta(null), "", "absent");
  assert.equal(weeklyMeta("not-a-date"), "", "present but unparseable");
  // And the branch still renders a real countdown when there IS one, so the
  // assertions above cannot be satisfied by returning "" unconditionally.
  assert.equal(weeklyMeta(new Date(NOW + 2 * 3600 * 1000).toISOString()), "resets 2h0m");
});

test("🔴 each bar is coloured by ITS OWN window, not the record's worst", () => {
  // The record tone is the worse of session and weekly -- right for the card
  // and the pill, wrong for a per-window bar. Painting every bar with it made
  // a 5%-wide Session bar render RED whenever the weekly window was critical:
  // the bar misreporting the very window it measures.
  const r = rec(ORG_A, NAME_A);
  r.severity = null;
  r.session.utilization = 5;
  r.weekly.utilization = 97;
  const m = W.widgetModel(r, NOW);
  assert.equal(m.tone, "crit", "the record as a whole IS critical");
  assert.equal(m.rows.find((x) => x.key === "session").tone, "ok",
    "...but a 5% session window is not");
  assert.equal(m.rows.find((x) => x.key === "weekly").tone, "crit");
});

test("the Claude Code row is a SHARE, so it never alarms on its own", () => {
  // 100% of your weekly usage being Claude Code says nothing about how close
  // to a limit you are, so this row must not borrow the crit band.
  const r = rec(ORG_A, NAME_A);
  r.severity = null;
  r.codeWeeklyPercent = 99;
  assert.equal(W.widgetModel(r, NOW).rows.find((x) => x.key === "code").tone, "ok");
});

test("staleness greys every bar, not just the card", () => {
  const r = rec(ORG_A, NAME_A, NOW - 7 * 3600 * 1000);
  r.session.utilization = 99;
  for (const row of W.widgetModel(r, NOW).rows) {
    assert.equal(row.tone, "stale", `${row.key} kept a live colour on a stale record`);
  }
});

test("the waiting state agrees with toneFor AND with the badge", () => {
  // widgetModel hardcoded "unknown" here while toneFor(null) returned
  // "stale", so a first-ever page load had this file giving two answers for
  // one state -- and the toolbar (grey) disagreeing with the page (amber).
  assert.equal(W.widgetModel(null, NOW).tone, W.toneFor(null, NOW));
});

test("the waiting state is an instruction, never a row of zeroes", () => {
  const m = W.widgetModel(null, NOW);
  assert.equal(m.empty, true);
  assert.equal(m.rows.length, 0, "no fabricated 0% bars before the first snapshot");
  assert.equal(m.pill, "?");
  assert.ok(m.note && m.note.length > 0);
  assert.equal(W.widgetModel("nonsense", NOW).empty, true);
});

test("unknown utilisation shows '?' and an EMPTY bar, never 0%", () => {
  const r = rec(ORG_A, NAME_A, NOW, nullUsage());
  const m = W.widgetModel(r, NOW);
  const sess = m.rows.find((x) => x.key === "session");
  assert.equal(sess.value, "?");
  assert.equal(sess.bar, null, "null bar is an empty track; 0 would read as 'none used'");
  assert.equal(m.pill, "?");
});

test("staleness is both dimmed and LABELLED", () => {
  const r = rec(ORG_A, NAME_A, NOW - 7 * 3600 * 1000);
  const m = W.widgetModel(r, NOW);
  assert.equal(m.stale, true);
  assert.equal(m.asOf, "7h ago · stale");

  const fresh = W.widgetModel(rec(ORG_A, NAME_A, NOW), NOW);
  assert.equal(fresh.stale, false);
  assert.equal(fresh.asOf, "just now");
});

test("a locked window surfaces its reason, session taking priority", () => {
  const r = rec(ORG_A, NAME_A);
  r.weekly.lockedReason = "Weekly limit reached.";
  assert.equal(W.widgetModel(r, NOW).locked, "Weekly limit reached.");
  r.session.lockedReason = "Session limit reached.";
  assert.equal(W.widgetModel(r, NOW).locked, "Session limit reached.",
    "the window blocking you NOW is the one named");
  r.session.lockedReason = null;
  r.weekly.lockedReason = null;
  assert.equal(W.widgetModel(r, NOW).locked, null);
});

test("credits ride the shared formatter, not a second copy", () => {
  const r = rec(ORG_A, NAME_A);
  assert.equal(W.widgetModel(r, NOW).credits, "$75.00 credits left");
  r.credits = { enabled: false, disabledReason: "Monthly credit limit reached." };
  assert.equal(W.widgetModel(r, NOW).credits, "Credits disabled: Monthly credit limit reached.");
});

// --- the collapsed pill ------------------------------------------------------------ //

test("the pill carries the session number -- the one that gates the next message", () => {
  const r = rec(ORG_A, NAME_A);
  assert.equal(W.pillText(r), "9%");
  r.session.utilization = 47.5;
  assert.equal(W.pillText(r), "48%");
  r.session.utilization = null;
  assert.equal(W.pillText(r), "?");
  assert.equal(W.pillText(null), "?");
});

test("the model's pill and the pill helper never disagree", () => {
  // Two code paths render the same number; if they drift, the widget shows one
  // figure collapsed and another expanded.
  const r = rec(ORG_A, NAME_A);
  for (const v of [0, 9, 47.5, 99, 100, null]) {
    r.session.utilization = v;
    assert.equal(W.widgetModel(r, NOW).pill, W.pillText(r), `disagreed at ${v}`);
  }
});

// --- contracts the DOM depends on --------------------------------------------------- //

test("the host id and storage key are stable identifiers", () => {
  // content_widget.js finds an existing mount by this id; a change that is not
  // co-ordinated leaves two widgets stacked on the page.
  assert.equal(W.WIDGET_HOST_ID, "claude-usage-tracker-widget");
  assert.equal(W.COLLAPSE_KEY, "widgetCollapsed");
});

test("every row the painter receives carries the full field set", () => {
  // paintExpanded reads label/value/bar/meta off each row without guarding.
  const r = rec(ORG_A, NAME_A);
  for (const row of W.widgetModel(r, NOW).rows) {
    assert.equal(typeof row.key, "string");
    assert.equal(typeof row.label, "string");
    assert.equal(typeof row.value, "string");
    assert.equal(typeof row.meta, "string");
    assert.ok(row.bar === null || (typeof row.bar === "number" && row.bar >= 0 && row.bar <= 100));
    // `tone` was added to every row and paintExpanded reads it, but this pin
    // was not extended with it -- a row shipped without a tone passed a test
    // whose whole purpose is that the painter's fields are present.
    assert.ok(TONES.includes(row.tone), `${row.key} tone=${row.tone}`);
  }
});

test("the MODEL keeps a record tone distinct from the per-window tones", () => {
  // ⚠ THIS IS AN INVARIANT GUARD ON THE MODEL, NOT A REGRESSION TEST, and the
  // distinction cost a round to learn. Round 2's finding was that the PAINTER
  // rendered model.tone nowhere; the model was already correct, so this
  // assertion was GREEN at the broken tip — watched, not assumed. Titled
  // "the RECORD tone survives on the card" at first, which claimed coverage
  // of a surface it never touches.
  //
  // The regression guard for that defect lives where the defect lives:
  // tests/content_widget.test.mjs, "the expanded card RENDERS the record
  // tone", which IS watched red at d2c68d20. This test pins the model's half
  // of the contract so the painter has something correct to render.
  const r = rec(ORG_A, NAME_A);
  r.severity = "critical";
  r.session.utilization = 5;
  r.weekly.utilization = 5;
  const m = W.widgetModel(r, NOW);
  assert.equal(m.tone, "crit", "the record is critical because the API says so");
  assert.deepEqual(m.rows.map((x) => x.tone), ["ok", "ok", "ok"],
    "...while each individual WINDOW is genuinely fine");

  // And the fixture's own disagreement, which is the everyday case.
  const f = rec(ORG_A, NAME_A);            // severity "medium", 9% / 47%
  assert.equal(W.widgetModel(f, NOW).tone, "warn",
    "an API severity the percentages do not justify must still reach the card");
});

// --- the OTHER-ACCOUNTS section ------------------------------------------------- //
//
// 🔴 WHY THIS SECTION EXISTS. He runs several accounts and switches when one
// hits its session cap. A stored account can only be re-measured while logged
// INTO it (content_probe.js fetches with the current session cookie), so the
// account worth switching to is by construction the one with the STALEST
// snapshot -- and the old widget showed one account, the active one, with
// `formatCountdown()` reporting an elapsed reset as "resets soon". The freest
// account read as still-at-92%, forever.

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const iso = (ms) => new Date(ms).toISOString();

/**
 * A record whose session window resets at `resetsAt`, last measured `ageH`
 * hours ago at `pct` session / `wk` weekly. `over` gets the record for the
 * fields a single test cares about.
 *
 * 🔴 THE WEEKLY PERCENTAGE IS A PARAMETER, AND IT HAD TO BECOME ONE. Every
 * fixture in this section used to leave `weekly.utilization` at the constant
 * 47 that `fullUsage()` supplies, on EVERY record -- so the suite was
 * structurally blind to the weekly dimension for other-account rows, which is
 * exactly the dimension `otherRow` was ignoring (it passed `percentTone(pct,
 * null)`). A suite whose fixtures pin a dimension cannot see that dimension's
 * bugs. Values are pairwise distinct within every fixture set, distinct from
 * the session percentage beside them, and distinct from every constant the
 * assertions name (WARN_PCT 80, CRIT_PCT 95, WEEKLY_EXHAUSTED_PCT 100, 0), so
 * a mutant hardcoding a literal cannot survive by landing on a fixture value.
 *
 * `severity` is nulled so the PERCENT half of the tone rule is isolated --
 * `fullUsage()` carries a "medium" severity row that would otherwise decide
 * every tone below. A dedicated test covers severity reaching an other-row.
 */
function acct(uuid, name, pct, wk, resetsAt, ageH, over) {
  const r = rec(uuid, name, NOW - ageH * HOUR);
  r.severity = null;
  r.session.utilization = pct;
  r.session.resetsAt = resetsAt;
  r.weekly.utilization = wk;
  r.weekly.resetsAt = iso(NOW + 4 * DAY);
  if (over) over(r);
  return r;
}

test("🔴 REGRESSION: a stored account whose reset has ELAPSED reads as AVAILABLE, not 92%", () => {
  // The defect, at the model level. Account B was at 91% six hours ago and
  // its five-hour window closed two hours ago. Before this change the widget
  // had no `others` at all, and the only surface that showed B said
  // "Session 91% · resets soon" -- the one state his workflow depends on,
  // inverted.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const freed = acct(ORG_B, NAME_B, 91, 14, iso(NOW - 2 * HOUR), 6);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: freed },
    lastActiveOrg: ORG_A,
  });

  assert.equal(m.others.length, 1, "the other account is not on the card at all");
  const row = m.others[0];
  assert.equal(row.state, "free");
  assert.equal(row.value, "AVAILABLE");
  assert.ok(!/resets soon/.test(row.meta),
    `the freed account still reads "${row.meta}"`);
  // The inference is STATED, and the last measured reading stays visible so
  // it can never be mistaken for a fresh one.
  assert.match(row.meta, /reset 2h ago/, `meta was "${row.meta}"`);
  assert.match(row.meta, /was 91%/, "the last MEASURED value must remain visible");
  assert.match(row.meta, /measured 6h ago/, "...with its age");
  assert.ok(!/\b0%/.test(row.value + row.meta), "never a fabricated 0%");
});

// --- 🔴 F1: the row verdict is not a SESSION-window verdict ------------------ //
//
// 🔴 THE DEFECT. `otherRow` hardcoded `tone: "ok", stale: false` on the free
// branch and called `percentTone(v.sessionPct, null)` -- weekly literally
// null -- on the measured one, i.e. it ran a SECOND, narrower rule than the
// card above it and the badge beside it, in the PR whose commit is titled
// "one rule, one place". MEASURED at b97190c8: an account whose five-hour
// window had reset three hours ago, with its SEVEN-DAY window at 100% and
// `lockedReason: "Weekly limit reached."` resetting in four days, rendered
//
//     AVAILABLE — reset 3h ago (was 95%, measured 8h ago)     tone ok, sorted #1
//
// and logging into it is immediately weekly-blocked. Because the weekly
// window is seven days against the session's five hours, that state persists
// for DAYS. Adding `session.lockedReason` and a `staleSince` (a 403) produced
// a BYTE-IDENTICAL row.

/** The trap: session window long since reset, weekly window spent. */
const weeklyBlocked = (uuid, name, pct, wkPct, lockedReason) =>
  acct(uuid, name, pct, wkPct, iso(NOW - 3 * HOUR), 8, (r) => {
    r.weekly.lockedReason = lockedReason === undefined ? "Weekly limit reached." : lockedReason;
  });

test("🔴 REGRESSION: a weekly-BLOCKED account does not read AVAILABLE on the card", () => {
  // Watched RED at b97190c8: value "AVAILABLE", tone "ok", state "free".
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const trap = weeklyBlocked(ORG_B, NAME_B, 95, 100);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: trap }, lastActiveOrg: ORG_A,
  });
  const row = m.others[0];
  assert.equal(row.state, "blocked", `the row is "${row.state}"`);
  assert.notEqual(row.value, "AVAILABLE");
  assert.equal(row.value, "BLOCKED");
  assert.equal(row.tone, "crit", "an unusable account painted as a good switch target");

  // 🔴 THE DECISION, WRITTEN DOWN AS A CONTRACT rather than left to prose:
  // the row names the BINDING CONSTRAINT and says WHEN it frees up, because
  // that is the question "should I switch here" actually turns on -- and it
  // keeps the measured-value-visible, no-fabricated-0% rule the free branch
  // established.
  assert.match(row.meta, /Weekly limit reached\./,
    `the lock the ACTIVE card renders is dropped on an other row: "${row.meta}"`);
  assert.match(row.meta, /frees up in 4d0h/, `no wait time stated: "${row.meta}"`);
  assert.match(row.meta, /session was 95%/, "the last MEASURED value must remain visible");
  assert.match(row.meta, /measured 8h ago/, "...with its age");
  assert.ok(!/\b0%/.test(row.value + row.meta), "never a fabricated 0%");
  assert.ok(!/AVAILABLE|resets soon/.test(row.meta), `meta reads "${row.meta}"`);
});

test("🔴 REGRESSION: an EXHAUSTED weekly window with no lock string still blocks, and says why", () => {
  // Watched RED at b97190c8. The API need not send a reason; 100% of the
  // allowance is out on its own terms, and the row states the reading it
  // blocked on rather than an invented sentence.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const trap = weeklyBlocked(ORG_B, NAME_B, 62, 100, null);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: trap }, lastActiveOrg: ORG_A,
  });
  assert.equal(m.others[0].state, "blocked");
  assert.match(m.others[0].meta, /weekly 100%/, `meta reads "${m.others[0].meta}"`);
  assert.match(m.others[0].meta, /frees up in 4d0h/);
});

test("🔴 REGRESSION: a blocked row and a free row no longer render IDENTICALLY", () => {
  // MEASURED at b97190c8: a weekly-locked account and a genuinely free one
  // produced the same row modulo the name -- "AVAILABLE — reset 3h ago (was
  // 95%, measured 8h ago)", tone ok -- two opposite stored states, one
  // output, and the wrong one of the two was being recommended. Watched RED
  // there on both assertions below.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const lockedWeekly = weeklyBlocked(ORG_B, NAME_B, 95, 100);
  const genuinelyFree = acct(ORG_D, NAME_D, 95, 33, iso(NOW - 3 * HOUR), 8);

  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: lockedWeekly, [ORG_D]: genuinelyFree },
    lastActiveOrg: ORG_A,
  });
  const byName = Object.fromEntries(m.others.map((r) => [r.name, r]));
  const shape = (r) => `${r.state}|${r.value}|${r.meta}|${r.tone}`;

  assert.equal(byName[NAME_D].value, "AVAILABLE", "the genuinely free one is still free");
  assert.equal(byName[NAME_B].value, "BLOCKED");
  assert.notEqual(shape(byName[NAME_B]), shape(byName[NAME_D]),
    "a weekly-blocked account and a free one still render identically");
});

test("a LIVE session lock is named ahead of a weekly one; a SPENT one is not named at all", () => {
  // 🔴 A DELIBERATE COINCIDENCE, PINNED SO IT READS AS A DECISION. The round-1
  // audit noted that adding `session.lockedReason: "account_suspended"` to a
  // weekly-blocked record produced a byte-identical row, and it still does
  // when that record's SESSION WINDOW HAS SINCE RESET -- because a session
  // lock is evidence about the session window and is spent by that window's
  // own reset, exactly as the percentage beside it is. Naming a spent lock
  // would recreate this module's founding defect one field over: an account
  // marked BLOCKED forever off a five-hour lock it can never be re-measured
  // out of.
  //
  // ⚠ AND THERE IS A REAL RESIDUE, NAMED RATHER THAN PAPERED OVER: an
  // ACCOUNT-level suspension that the API happens to surface through
  // `five_hour.locked_reason` becomes invisible once the session window turns
  // over. Distinguishing it from an ordinary "Session limit reached." would
  // mean string-matching an uncontracted field, which is the prose-heuristic
  // fix RULES.md says to offer rather than reach for. The state is still
  // BLOCKED in the case that matters here (the weekly window), and a
  // suspension with NO weekly block would read as free. Not closed.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);

  // Session window still OPEN -> the lock is live, and it is the one blocking
  // you now, so it outranks the weekly reason.
  const liveLock = acct(ORG_B, NAME_B, 95, 100, iso(NOW + 2 * HOUR), 8, (r) => {
    r.session.lockedReason = "account_suspended";
    r.weekly.lockedReason = "Weekly limit reached.";
  });
  const live = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: liveLock }, lastActiveOrg: ORG_A,
  }).others[0];
  assert.equal(live.state, "blocked");
  assert.match(live.meta, /account_suspended/,
    "the session lock is the one blocking you NOW and must be the one named");
  assert.ok(!/Weekly limit reached/.test(live.meta),
    `both reasons on one row reads as two problems: "${live.meta}"`);

  // Session window RESET -> the lock is spent, and the weekly reason is the
  // one that still binds. Identical to the same record without the session
  // lock, and that is the contract.
  const spent = weeklyBlocked(ORG_C, NAME_C, 95, 100);
  spent.session.lockedReason = "account_suspended";
  const plain = weeklyBlocked(ORG_D, NAME_D, 95, 100);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_C]: spent, [ORG_D]: plain }, lastActiveOrg: ORG_A,
  });
  const byName = Object.fromEntries(m.others.map((r) => [r.name, r]));
  assert.ok(!/account_suspended/.test(byName[NAME_C].meta),
    `a lock spent by its own window's reset was reported as live: "${byName[NAME_C].meta}"`);
  assert.equal(byName[NAME_C].meta.replace(NAME_C, ""), byName[NAME_D].meta.replace(NAME_D, ""),
    "a spent session lock must change nothing -- if it does, this decision has drifted");
});

/** The card the widget renders when `lastActiveOrg` names an org with no
 * stored record: `pickRecord` falls back to the freshest one, so the card is
 * showing a record that has NOT earned the active-account exemption. This is
 * a real stored state -- service_worker.js writes `lastActiveOrg` whether or
 * not the /usage fetch produced a record. */
const GHOST_ORG = "99999999-9999-4999-8999-999999999999";
const ghostCard = (r, orgUuid) => {
  const accounts = { [orgUuid]: r };
  return W.widgetModel(W.pickRecord(accounts, GHOST_ORG), NOW,
    { accounts, lastActiveOrg: GHOST_ORG });
};

test("🔴 REGRESSION: the exemption is withdrawn for EVERY state -- a non-exempt card says BLOCKED", () => {
  // Watched RED at 38bbc1f1: `shownFree = !exempt && verdict.state === FREE`
  // withdrew the exemption for `free` alone, so the state that most needed
  // saying was the one it missed. MEASURED there, both columns:
  //   with a lock string     card session row `95% · resets soon`
  //   with NO lock string    card session row `95% · resets soon`, and a
  //                          NULL lock banner -- the card said nothing at all
  //                          about being blocked
  // while popup.js, same record and same `now`, read
  // `BLOCKED · Weekly limit reached. · frees up in 4d0h` and
  // `BLOCKED · weekly 100% · frees up in 4d0h`. "resets soon" is the string
  // lib/availability.js's own header calls "the bug", on a record that will
  // never be re-measured.
  for (const lock of ["Weekly limit reached.", null]) {
    const trap = weeklyBlocked(ORG_B, NAME_B, 95, 100, lock);
    const row = ghostCard(trap, ORG_B).rows[0];
    assert.equal(row.value, "BLOCKED", `lock=${lock}: the card read "${row.value}"`);
    assert.ok(!/resets soon/.test(row.meta), `lock=${lock}: "${row.meta}"`);
    assert.match(row.meta, /frees up in 4d0h/, `lock=${lock}: "${row.meta}"`);
    assert.match(row.meta, /session was 95%/,
      "the last MEASURED percentage must stay visible");
    assert.equal(row.tone, "crit", `lock=${lock}`);
    assert.equal(row.bar, null,
      "a track under a BLOCKED value is a percentage claim with no label");
  }
  // The reason comes from the record when there is one and from the weekly
  // number when there is not -- never an invented sentence, and worded by the
  // SAME helpers the other-account rows use.
  assert.match(ghostCard(weeklyBlocked(ORG_B, NAME_B, 95, 100), ORG_B).rows[0].meta,
    /^Weekly limit reached\. · /);
  assert.match(ghostCard(weeklyBlocked(ORG_B, NAME_B, 95, 100, null), ORG_B).rows[0].meta,
    /^weekly 100% · /);

  // 🔴 AND THE EXEMPTION ITSELF SURVIVES, or this is a deletion rather than a
  // fix. The ACTIVE account keeps its live countdown over an elapsed reset,
  // because content_probe.js will re-measure it within seconds.
  const active = weeklyBlocked(ORG_A, NAME_A, 95, 100);
  const exempt = W.widgetModel(active, NOW,
    { accounts: { [ORG_A]: active }, lastActiveOrg: ORG_A }).rows[0];
  assert.equal(exempt.value, "95%", "the active card lost its own reading");
  assert.equal(exempt.meta, "resets soon", "the earned exemption was deleted with the defect");
});

test("🔴 REGRESSION: a lock SPENT by its own reset is not painted over an AVAILABLE card", () => {
  // The mirror half of the same defect, MEASURED at 38bbc1f1: the lock BANNER
  // read the raw record while the session row read the verdict, so a session
  // lock whose five-hour window had since closed produced
  //   session row  {"value":"AVAILABLE","tone":"ok",
  //                 "meta":"reset 2h ago (was 95%, measured 8h ago)"}
  //   banner       "Session limit reached."
  // -- green and red on one card, disagreeing about the same record.
  const spentLock = acct(ORG_B, NAME_B, 95, 33, iso(NOW - 2 * HOUR), 8, (r) => {
    r.session.lockedReason = "Session limit reached.";
  });
  const card = ghostCard(spentLock, ORG_B);
  assert.equal(card.rows[0].value, "AVAILABLE", "precondition: the verdict is free");
  assert.equal(card.locked, null, `the card still banners "${card.locked}"`);

  // The exemption survives here too: for the ACTIVE account the banner keeps
  // its raw read, because the next probe is seconds away.
  const active = acct(ORG_A, NAME_A, 95, 33, iso(NOW - 2 * HOUR), 8, (r) => {
    r.session.lockedReason = "Session limit reached.";
  });
  assert.equal(
    W.widgetModel(active, NOW, { accounts: { [ORG_A]: active }, lastActiveOrg: ORG_A }).locked,
    "Session limit reached.");
  // ...and a LIVE lock on a non-exempt card is still bannered, so the branch
  // above cannot be satisfied by dropping the banner for every other record.
  const liveLock = acct(ORG_C, NAME_C, 95, 33, iso(NOW + 2 * HOUR), 8, (r) => {
    r.session.lockedReason = "Session limit reached.";
  });
  assert.equal(ghostCard(liveLock, ORG_C).locked, "Session limit reached.");
});

test("🔴 REGRESSION: an other-account row is not reddened by a weekly window that has RESET", () => {
  // The model-level half of availability.test.mjs's spent-weekly regression.
  // MEASURED at 38bbc1f1 -- tone "crit" on a row reading
  // `AVAILABLE — reset 3h ago (was 50%, measured 8d ago)`, with nothing on
  // the row that could explain the colour.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const freed = acct(ORG_B, NAME_B, 50, 100, iso(NOW - 3 * HOUR), 8 * 24, (r) => {
    r.weekly.resetsAt = iso(NOW - HOUR);       // the seven-day boundary crossed
  });
  const row = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: freed }, lastActiveOrg: ORG_A,
  }).others[0];
  assert.equal(row.state, "free", "precondition: the weekly block is spent too");
  assert.equal(row.tone, "ok", `an AVAILABLE row painted "${row.tone}": ${row.meta}`);
});

test("🔴 REGRESSION: the CARD's headline tone is not reddened by evidence its own verdict calls SPENT", () => {
  // Watched RED at 3f0a5506, on the fixture below:
  //   card    tone "crit"  -- a red collapsed pill and a red header dot
  //   others  tone "ok"    -- the SAME record, same storage, same `now`
  // `widgetModel` returned `tone: toneFor(record, now)`, which is
  // severity.js's `toneForRecord` reading `record.session.utilization` and
  // `record.weekly.utilization` RAW. Both windows here have turned over, so
  // both readings are spent evidence and `availability()` already says so --
  // the verdict is `free` with `weeklyBindingPct: null`. The card was the one
  // surface still banding the raw record, and it is the surface the operator
  // sees first.
  //
  // 🔴 THE ASSERTION IS THE COLOUR, and the pair of colours specifically. A
  // single-sided check ("the card is ok") would be satisfied by painting
  // every non-exempt card ok, which the weekly-still-binds case below forbids.
  const spentBoth = acct(ORG_B, NAME_B, 93, 97, iso(NOW - 2 * HOUR), 1, (r) => {
    r.weekly.resetsAt = iso(NOW - 30 * 60 * 1000);   // the weekly window turned over too
  });
  const card = ghostCard(spentBoth, ORG_B);
  assert.equal(card.rows[0].value, "AVAILABLE", "precondition: the verdict is free");
  assert.equal(card.tone, "ok",
    `the card headline painted "${card.tone}" off a session and a weekly reading `
    + "its own verdict calls spent");

  // The relationship, not just the value: one record at one instant may not
  // get two colours from two surfaces of one widget.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const asOther = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: spentBoth }, lastActiveOrg: ORG_A,
  }).others[0];
  assert.equal(asOther.name, NAME_B, "precondition: the row under test is the same record");
  assert.equal(card.tone, asOther.tone,
    `card "${card.tone}" vs other-account row "${asOther.tone}" for one record at one now`);

  // The WEEKLY ROW's own colour, which was the second raw read on this card.
  // "unknown" and not "ok": a window that has turned over leaves NO current
  // reading, and the row still displays the stored number beside the colour.
  assert.equal(card.rows[1].key, "weekly");
  assert.equal(card.rows[1].tone, "unknown",
    `the weekly row painted "${card.rows[1].tone}" off a 97% that reset 30 minutes ago`);
  // ⚠ INVARIANT GUARD (green at 3f0a5506): the VALUE was never the defect.
  assert.equal(card.rows[1].value, "97%",
    "the last MEASURED weekly reading must stay on screen; only the colour is withdrawn");

  // Each half separately, so a fix that reads only one of the two windows
  // through the verdict cannot pass. Session spent, weekly UNSPENT and calm:
  // red at 3f0a5506 as "warn" off the spent 93%.
  const spentSession = acct(ORG_C, NAME_C, 93, 12, iso(NOW - 2 * HOUR), 1);
  assert.equal(ghostCard(spentSession, ORG_C).tone, "ok",
    "a spent session percentage still decides the card headline");

  // ⚠ INVARIANT GUARD (green at 3f0a5506, and it must stay green): a weekly
  // window that has NOT reset is live evidence and must keep reddening the
  // card. This is what stops the fix from being "paint every non-exempt card
  // ok" -- 97% here is the same number as the spent fixture above, so only
  // the reset time distinguishes them.
  const weeklyStillBinds = acct(ORG_D, NAME_D, 93, 97, iso(NOW - 2 * HOUR), 1);
  assert.equal(ghostCard(weeklyStillBinds, ORG_D).tone, "crit",
    "a weekly window that has not reset stopped colouring the card");

  // ⚠ INVARIANT GUARD (green at 3f0a5506): the ACTIVE account keeps the raw
  // read. It is the one account content_probe.js re-measures within seconds,
  // and the toolbar badge shares that rule -- changing it here would split
  // the two surfaces the other way.
  const activeSpent = acct(ORG_A, NAME_A, 93, 97, iso(NOW - 2 * HOUR), 1, (r) => {
    r.weekly.resetsAt = iso(NOW - 30 * 60 * 1000);
  });
  assert.equal(
    W.widgetModel(activeSpent, NOW,
      { accounts: { [ORG_A]: activeSpent }, lastActiveOrg: ORG_A }).tone,
    "crit", "the earned exemption was deleted along with the defect");

  // ⚠ INVARIANT GUARD (green at 3f0a5506): this fix is the COLOUR only. The
  // weekly row's "resets soon" is a separately-named residue (see
  // `weeklyMeta`), and round 3 measured this row as the one remaining place
  // that string renders. If this assertion moves, that sweep needs re-running.
  assert.equal(card.rows[1].meta, "resets soon",
    "the weekly row's string residue changed; re-derive the sweep rather than editing it");
});

test("🔴 REGRESSION: a weekly-blocked account is sorted BELOW a usable one", () => {
  // Watched RED at b97190c8: the blocked account read "free" and took row 1,
  // above an account at 12% that actually works.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const trap = weeklyBlocked(ORG_B, NAME_B, 95, 100);
  const usable = acct(ORG_C, NAME_C, 12, 19, iso(NOW + 2 * HOUR), 1);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: trap, [ORG_C]: usable }, lastActiveOrg: ORG_A,
  });
  assert.deepEqual(m.others.map((r) => r.name), [NAME_C, NAME_B],
    "the account that rejects you at the login screen is being recommended first");
});

test("🔴 the next-free footer counts a blocked account -- four days is still an answer", () => {
  // Watched RED at b97190c8: with every other account weekly-blocked the
  // footer was null, so the card said nothing at all about the wait.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 30 * 60 * 1000), 0);
  const trap = weeklyBlocked(ORG_B, NAME_B, 95, 100);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: trap }, lastActiveOrg: ORG_A,
  });
  assert.equal(m.nextFree, `next free: ${NAME_B} in 4d0h`);
});

test("🔴 a fresh measured row is coloured by BOTH windows, not the session alone", () => {
  // Watched RED at b97190c8: `percentTone(v.sessionPct, null)` could not see
  // the weekly window, so a 23% session row beside a 97% weekly window
  // painted green -- "showing 'ok' while the weekly window sits at 97% would
  // be a lie by omission", which is severity.js's own sentence.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const calmSession = acct(ORG_B, NAME_B, 23, 97, iso(NOW + HOUR), 1);
  const alsoCalm = acct(ORG_C, NAME_C, 23, 84, iso(NOW + HOUR), 1);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: calmSession, [ORG_C]: alsoCalm },
    lastActiveOrg: ORG_A,
  });
  const byName = Object.fromEntries(m.others.map((r) => [r.name, r]));
  assert.equal(byName[NAME_B].tone, "crit", "a 97% weekly window did not reach the row");
  assert.equal(byName[NAME_C].tone, "warn", "...and neither did an 84% one");
  assert.equal(byName[NAME_B].value, "23%", "the session reading is still what is SHOWN");
});

test("🔴 the API severity reaches an other-account row, as it does the card", () => {
  // The other half of the consolidated rule. A severity the percentages do
  // not justify must not be silently dropped on the way to a row -- that is
  // the badge-vs-page disagreement lib/severity.js exists to eliminate.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const loud = acct(ORG_B, NAME_B, 23, 14, iso(NOW + HOUR), 1,
    (r) => { r.severity = "critical"; });
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: loud }, lastActiveOrg: ORG_A,
  });
  assert.equal(m.others[0].tone, "crit");
  // ...and it does not INVENT alarm: the same row with no severity is calm.
  const quiet = acct(ORG_B, NAME_B, 23, 14, iso(NOW + HOUR), 1);
  assert.equal(W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: quiet }, lastActiveOrg: ORG_A,
  }).others[0].tone, "ok");
});

test("🔴 staleness does NOT grey a presumed-free row (it greys the others)", () => {
  // The 6h staleness rule washes out precisely the most actionable row, and
  // does so BY CONSTRUCTION: an account you are not logged into cannot be
  // re-measured, so every good switch target is stale.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const freed = acct(ORG_B, NAME_B, 91, 14, iso(NOW - 2 * HOUR), 9);     // 9h old
  const waiting = acct(ORG_C, NAME_C, 62, 19, iso(NOW + 4 * HOUR), 9);   // also 9h old
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: freed, [ORG_C]: waiting },
    lastActiveOrg: ORG_A,
  });
  // Rows are addressed by NAME: an other-row carries no `key`, because
  // `paintOthers` rebuilds the whole section every render and diffs nothing.
  const byName = Object.fromEntries(m.others.map((r) => [r.name, r]));

  assert.equal(byName[NAME_B].stale, false, "the free row was greyed out");
  assert.equal(byName[NAME_B].tone, "ok", "...and lost its colour with it");
  assert.equal(byName[NAME_C].stale, true,
    "a stale MEASURED row must still grey -- there the percentage IS the claim");
  assert.equal(byName[NAME_C].tone, "stale");
});

test("a fresh measured row shows its band when the SESSION window is the worst constraint", () => {
  // 🔴 REWRITTEN FROM AN IMPLEMENTATION ASSERTION INTO A CONTRACT. This
  // used to expect "ok" for a 23% row whose fixture also carried a 47%
  // weekly window and a "medium" API severity -- an expectation that was
  // only correct because `otherRow` consulted neither. It therefore
  // asserted the one-window rule the implementation happened to use, and
  // would have stayed green through the whole of F1. The contract is
  // "the worst binding constraint decides"; this pins the case where the
  // session window IS that constraint, with the other two explicitly
  // held below it, and the test above pins the cases where they are not.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const hot = acct(ORG_B, NAME_B, 97, 14, iso(NOW + HOUR), 1);
  const calm = acct(ORG_C, NAME_C, 23, 19, iso(NOW + HOUR), 1);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: hot, [ORG_C]: calm },
    lastActiveOrg: ORG_A,
  });
  const byName = Object.fromEntries(m.others.map((r) => [r.name, r]));
  assert.equal(byName[NAME_B].tone, "crit");
  assert.equal(byName[NAME_C].tone, "ok");
  assert.equal(byName[NAME_B].value, "97%");
  assert.match(byName[NAME_B].meta, /^resets 1h0m · measured 1h ago$/);
});

test("an other-account row with no reset time says so, and shows no countdown", () => {
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const murky = acct(ORG_B, NAME_B, 62, 14, null, 1);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: murky }, lastActiveOrg: ORG_A,
  });
  assert.equal(m.others[0].state, "unknown");
  assert.equal(m.others[0].value, "62%", "the percentage is still known");
  assert.match(m.others[0].meta, /reset time unknown/);
  assert.ok(!/resets soon|unknown · /.test(m.others[0].meta.replace("reset time unknown", "")),
    `no second "unknown" in "${m.others[0].meta}"`);
});

test("every other-row carries the full field set the painter reads", () => {
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const accounts = {
    [ORG_A]: active,
    [ORG_B]: acct(ORG_B, NAME_B, 91, 14, iso(NOW - 2 * HOUR), 6),
    [ORG_C]: acct(ORG_C, NAME_C, 62, 19, iso(NOW + HOUR), 1),
    [ORG_D]: acct(ORG_D, NAME_D, 23, 26, null, 1),
    blocked_key: weeklyBlocked("55555555-5555-4555-8555-555555555555", "fifth", 44, 100),
  };
  const m = W.widgetModel(active, NOW, { accounts, lastActiveOrg: ORG_A });
  assert.equal(m.others.length, 4);
  assert.deepEqual([...new Set(m.others.map((r) => r.state))].sort(),
    ["blocked", "free", "measured", "unknown"],
    "precondition: all four branches are exercised");
  for (const row of m.others) {
    assert.equal(typeof row.name, "string");
    assert.equal(typeof row.value, "string");
    assert.equal(typeof row.meta, "string");
    assert.equal(typeof row.stale, "boolean");
    assert.ok(TONES.includes(row.tone), `${row.name} tone=${row.tone}`);
  }
});

test("an other-row carries NOTHING the painter does not read", () => {
  // The inverse of the test above, and the one that would have caught the
  // dead fields: `paintOthers` draws a dot, a name, a value and a meta line,
  // and reads `state`/`tone`/`stale` for the classes. It never reads a `bar`
  // (only the main card's rows are barred) and never reads a `key` (it
  // rebuilds the whole section each render, so there is no list to diff).
  // Both were computed on all three branches for a round with no reader.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const accounts = {
    [ORG_A]: active,
    [ORG_B]: acct(ORG_B, NAME_B, 91, 14, iso(NOW - 2 * HOUR), 6),    // free
    [ORG_C]: acct(ORG_C, NAME_C, 62, 19, iso(NOW + HOUR), 1),        // measured
    [ORG_D]: acct(ORG_D, NAME_D, 23, 26, null, 1),                   // unknown
    blocked_key: weeklyBlocked("55555555-5555-4555-8555-555555555555", "fifth", 44, 100),
  };
  const m = W.widgetModel(active, NOW, { accounts, lastActiveOrg: ORG_A });
  assert.equal(m.others.length, 4, "precondition: all four branches are exercised");
  const EXPECTED = ["name", "state", "value", "meta", "tone", "stale"];
  for (const row of m.others) {
    assert.deepEqual(Object.keys(row).sort(), [...EXPECTED].sort(),
      `the ${row.state} branch carries a field nothing paints`);
  }
});

test("🔴 EVERY other account reaches the card -- no row is counted-but-unreachable", () => {
  // This replaces a test that pinned OTHERS_MAX = 4 and `othersMore = 2`.
  // The "+N more" line that cap produced was TERMINAL: no click anywhere in
  // the card could reveal those rows. A count of rows you cannot see is
  // worse than either showing them or not mentioning them -- and that is
  // true whether or not the cap fires on any particular profile, which was
  // NOT measured (see lib/widget.js for what was).
  // The height bound moved into CSS (`.otherlist` scrolls), which keeps the
  // "card over his chat" constraint without hiding anything; the painter's
  // half is pinned in content_widget.test.mjs.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const accounts = { [ORG_A]: active };
  // Six others, all measured, with distinct session AND weekly percentages
  // so the order is the rule's and not the map's.
  const pcts = [23, 31, 42, 53, 62, 71];
  const wks = [14, 19, 26, 33, 44, 51];
  pcts.forEach((p, i) => {
    const id = `9${i}999999-9999-4999-8999-999999999999`;
    accounts[id] = acct(id, `acct ${i}`, p, wks[i], iso(NOW + (i + 1) * HOUR), 1);
  });
  const m = W.widgetModel(active, NOW, { accounts, lastActiveOrg: ORG_A });

  assert.equal(m.others.length, 6, "an account was dropped from the card");
  assert.deepEqual(m.others.map((r) => r.value), ["23%", "31%", "42%", "53%", "62%", "71%"],
    "still most-available-first; the tail is appended, not truncated");
  assert.equal(W.OTHERS_MAX, undefined,
    "a row cap came back -- see lib/widget.js for why the next one needs an expand affordance");
  assert.ok(!("othersMore" in m),
    "the model still reports a hidden-row count, so something is hiding rows");
});

test("the next-free footer names the soonest account -- which is NOT the first row", () => {
  // 🔴 WHY THE FOOTER IS NOT REDUNDANT WITH THE LIST. The list ranks by how
  // good a switch target an account is (lowest percentage first); the footer
  // ranks by TIME. Here the first row is the 23% account resetting in 5h and
  // the footer names the 62% one resetting in 1h12m -- two different
  // questions with two different answers, on one card. Round 0 measured the
  // footer null on a two-account fixture and read that as redundancy; it is
  // null only when there is genuinely nothing to wait for.
  const active = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 30 * 60 * 1000), 0);
  const soon = acct(ORG_B, NAME_B, 62, 14, iso(NOW + HOUR + 12 * 60 * 1000), 1);
  const later = acct(ORG_C, NAME_C, 23, 19, iso(NOW + 5 * HOUR), 1);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: soon, [ORG_C]: later }, lastActiveOrg: ORG_A,
  });
  assert.equal(m.nextFree, `next free: ${NAME_B} in 1h12m`,
    "the ACTIVE account's own sooner reset is not the answer");
  assert.equal(m.others[0].name, NAME_C,
    "precondition: the list's own first answer is a DIFFERENT account");

  // Nothing pending -> no line at all, rather than an empty one.
  const allFree = {
    [ORG_A]: active, [ORG_B]: acct(ORG_B, NAME_B, 91, 14, iso(NOW - HOUR), 6),
  };
  assert.equal(W.widgetModel(active, NOW, { accounts: allFree, lastActiveOrg: ORG_A }).nextFree, null);
});

test("the account already ON the card never appears again under 'other accounts'", () => {
  const a = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const b = acct(ORG_B, NAME_B, 62, 14, iso(NOW + HOUR), 1);
  const accounts = { [ORG_A]: a, [ORG_B]: b };

  assert.deepEqual(
    W.widgetModel(a, NOW, { accounts, lastActiveOrg: ORG_A }).others.map((r) => r.name), [NAME_B]);
  // pickRecord() falls back to the freshest record before any active org is
  // known; the shown record must still be excluded even though it is not the
  // "active" one.
  assert.deepEqual(
    W.widgetModel(a, NOW, { accounts, lastActiveOrg: null }).others.map((r) => r.name), [NAME_B]);
});

// --- 🔴 F2: ONE answer for "which record is active" -------------------------- //
//
// 🔴 THE SEAM. service_worker.js writes `lastActiveOrg = activeUuid`
// unconditionally, while a non-401/403 failure on a first-seen org (network,
// 5xx, a non-JSON 200) leaves `accounts[activeUuid]` absent. The widget then
// identified "active" by MAP KEY and the popup by the record's own `orgUuid`
// FIELD, and pickRecord() promoted the freshest record onto the card and
// handed it the ACTIVE-ACCOUNT EXEMPTION it is not entitled to. MEASURED at
// b97190c8 with `{B: 92%, reset elapsed 2h, freshest; C: 40% pending}` and
// `lastActiveOrg` naming a ghost org:
//
//     WIDGET card   : B | Session 92% · resets soon     <- the pre-PR string
//     WIDGET others : C = 40%                            <- no AVAILABLE row
//     POPUP         : B: AVAILABLE · reset 2h ago (was 92%)
//
// The popup half is pinned in popup.test.mjs's cross-surface guard.

const GHOST = "99999999-9999-4999-8999-999999999999";

test("🔴 REGRESSION: the card does not hand the active-account exemption to a FALLBACK record", () => {
  // Watched RED at b97190c8: the session row's meta was "resets soon".
  const b = acct(ORG_B, NAME_B, 92, 14, iso(NOW - 2 * HOUR), 0);
  const c = acct(ORG_C, NAME_C, 40, 19, iso(NOW + 2 * HOUR), 1);
  const accounts = { [ORG_B]: b, [ORG_C]: c };
  assert.equal(W.pickRecord(accounts, GHOST), b, "precondition: the card falls back to B");

  const m = W.widgetModel(b, NOW, { accounts, lastActiveOrg: GHOST });
  const sess = m.rows.find((x) => x.key === "session");
  assert.ok(!/resets soon/.test(sess.meta),
    `the card still reads "${sess.value} · ${sess.meta}" for a record that will NEVER be re-measured`);
  assert.equal(sess.value, "AVAILABLE");
  assert.equal(sess.bar, null, "an empty track: the stored % describes a window that is gone");
  assert.match(sess.meta, /reset 2h ago/, sess.meta);
  assert.match(sess.meta, /was 92%/, "the last MEASURED value must remain visible");
  assert.ok(!/\b0%/.test(sess.value + sess.meta), "never a fabricated 0%");
});

test("INVARIANT GUARD: the ACTIVE account keeps its countdown -- the exemption is still earned", () => {
  // ⚠ LABELLED: watched at b97190c8 and it PASSED there, because the active
  // account's card never changed. It pins that the F2 fix did not OVER-reach,
  // which is a different claim from pinning that the F2 fix happened -- the
  // regression pair for that is the two tests around it, both watched red.
  //
  // The reason the exemption exists at all: content_probe.js fetches with the
  // CURRENT session cookie, so this is the one account "the next snapshot
  // will correct it" is true of.
  const a = acct(ORG_A, NAME_A, 92, 8, iso(NOW - 2 * HOUR), 0);
  const accounts = { [ORG_A]: a };
  const m = W.widgetModel(a, NOW, { accounts, lastActiveOrg: ORG_A });
  const sess = m.rows.find((x) => x.key === "session");
  assert.equal(sess.meta, "resets soon");
  assert.equal(sess.value, "92%");

  // ...and with NO account map the caller has one record and it is by
  // construction the one in front of you, so it keeps the exemption too.
  assert.equal(W.widgetModel(a, NOW).rows.find((x) => x.key === "session").meta, "resets soon");
  assert.equal(W.widgetModel(a, NOW, { labels: {} }).rows
    .find((x) => x.key === "session").meta, "resets soon");
});

test("🔴 REGRESSION: a JUNK value under the active key does not blank the whole widget", () => {
  // Watched RED at b97190c8: `accounts[lastActiveOrg]` returned the string
  // "junk", pickRecord handed it back as the record, and widgetModel rendered
  // `empty: true` with `others: []` -- the entire widget gone, with real data
  // stored. widget.js's comment asserted this could only happen on an EMPTY
  // map, which is the claim that stopped anyone looking.
  const b = acct(ORG_B, NAME_B, 62, 14, iso(NOW + HOUR), 1);
  const accounts = { [ORG_A]: "junk", [ORG_B]: b };
  assert.equal(W.pickRecord(accounts, ORG_A), b, "a string is not a record");

  const m = W.widgetModel(W.pickRecord(accounts, ORG_A), NOW,
    { accounts, lastActiveOrg: ORG_A });
  assert.equal(m.empty, false, "the widget rendered its waiting state over real data");
  assert.equal(m.name, NAME_B);
  assert.deepEqual(m.others.map((r) => r.name), [],
    "the junk entry must not be painted as an account either");
});

test("labels rename both the card's own header and the other rows", () => {
  const a = acct(ORG_A, NAME_A, 37, 8, iso(NOW + 3 * HOUR), 0);
  const b = acct(ORG_B, NAME_B, 62, 14, iso(NOW + HOUR), 1);
  const m = W.widgetModel(a, NOW, {
    accounts: { [ORG_A]: a, [ORG_B]: b },
    lastActiveOrg: ORG_A,
    labels: { [ORG_A]: "personal", [ORG_B]: "  work  " },
  });
  assert.equal(m.name, "personal");
  assert.equal(m.others[0].name, "work", "trimmed");

  // A blank override is a REMOVE, not a nameless row.
  const blank = W.widgetModel(a, NOW, {
    accounts: { [ORG_A]: a, [ORG_B]: b }, lastActiveOrg: ORG_A,
    labels: { [ORG_A]: "   ", [ORG_B]: "" },
  });
  assert.equal(blank.name, NAME_A);
  assert.equal(blank.others[0].name, NAME_B);
});

test("without a ctx the model is exactly what it was before this section existed", () => {
  const r = rec(ORG_A, NAME_A);
  for (const m of [W.widgetModel(r, NOW), W.widgetModel(r, NOW, undefined)]) {
    assert.deepEqual(m.others, []);
    assert.equal(m.nextFree, null);
    assert.equal(m.name, NAME_A);
  }
  const empty = W.widgetModel(null, NOW, { accounts: { [ORG_A]: rec(ORG_A, NAME_A) } });
  assert.deepEqual(empty.others, []);
  assert.equal(empty.nextFree, null);
});

test("the others section is TOTAL over garbage ctx -- it runs in his real tab", () => {
  const r = rec(ORG_A, NAME_A);
  const ctxs = [
    "nonsense", 42, [], null,
    { accounts: "nonsense" }, { accounts: null }, { accounts: [] },
    { accounts: { [ORG_B]: null }, lastActiveOrg: 5 },
    { accounts: { [ORG_B]: "nope" }, lastActiveOrg: ORG_A, labels: "nope" },
    { accounts: { [ORG_B]: rec(ORG_B, NAME_B) }, labels: 7 },
    { accounts: { [ORG_A]: r }, lastActiveOrg: "constructor" },
  ];
  for (const ctx of ctxs) {
    const m = W.widgetModel(r, NOW, ctx);
    assert.ok(Array.isArray(m.others), `ctx=${JSON.stringify(ctx)}`);
    assert.ok(m.nextFree === null || typeof m.nextFree === "string");
    for (const row of m.rows) {
      assert.equal(typeof row.value, "string", `ctx=${JSON.stringify(ctx)}`);
      assert.equal(typeof row.meta, "string");
    }
  }
});

test("the widget's warn threshold matches the service worker's toast threshold", async () => {
  // A widget that turns amber at a different number than the one that toasts
  // would make the two surfaces disagree about what "high" means.
  globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;
  const SW = await import("../extension/service_worker.js");
  assert.equal(W.WARN_PCT, SW.ALERT_THRESHOLD_PCT);
});
