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
const iso = (ms) => new Date(ms).toISOString();

/** A record whose session window resets at `resetsAt`, last measured `ageH`
 * hours ago at `pct`. Percentages are pairwise distinct and distinct from
 * WARN_PCT/CRIT_PCT so a mutant hardcoding a band cannot hide. */
function acct(uuid, name, pct, resetsAt, ageH) {
  const r = rec(uuid, name, NOW - ageH * HOUR);
  r.session.utilization = pct;
  r.session.resetsAt = resetsAt;
  return r;
}

test("🔴 REGRESSION: a stored account whose reset has ELAPSED reads as AVAILABLE, not 92%", () => {
  // The defect, at the model level. Account B was at 91% six hours ago and
  // its five-hour window closed two hours ago. Before this change the widget
  // had no `others` at all, and the only surface that showed B said
  // "Session 91% · resets soon" -- the one state his workflow depends on,
  // inverted.
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const freed = acct(ORG_B, NAME_B, 91, iso(NOW - 2 * HOUR), 6);
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

test("🔴 staleness does NOT grey a presumed-free row (it greys the others)", () => {
  // The 6h staleness rule washes out precisely the most actionable row, and
  // does so BY CONSTRUCTION: an account you are not logged into cannot be
  // re-measured, so every good switch target is stale.
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const freed = acct(ORG_B, NAME_B, 91, iso(NOW - 2 * HOUR), 9);     // 9h old
  const waiting = acct(ORG_C, NAME_C, 62, iso(NOW + 4 * HOUR), 9);   // also 9h old
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

test("a fresh measured row keeps its own percent band", () => {
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const hot = acct(ORG_B, NAME_B, 97, iso(NOW + HOUR), 1);
  const calm = acct(ORG_C, NAME_C, 23, iso(NOW + HOUR), 1);
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
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const murky = acct(ORG_B, NAME_B, 62, null, 1);
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
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const accounts = {
    [ORG_A]: active,
    [ORG_B]: acct(ORG_B, NAME_B, 91, iso(NOW - 2 * HOUR), 6),
    [ORG_C]: acct(ORG_C, NAME_C, 62, iso(NOW + HOUR), 1),
    [ORG_D]: acct(ORG_D, NAME_D, 23, null, 1),
  };
  const m = W.widgetModel(active, NOW, { accounts, lastActiveOrg: ORG_A });
  assert.equal(m.others.length, 3);
  for (const row of m.others) {
    assert.equal(typeof row.name, "string");
    assert.ok(["free", "measured", "unknown"].includes(row.state), row.state);
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
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const accounts = {
    [ORG_A]: active,
    [ORG_B]: acct(ORG_B, NAME_B, 91, iso(NOW - 2 * HOUR), 6),    // free
    [ORG_C]: acct(ORG_C, NAME_C, 62, iso(NOW + HOUR), 1),        // measured
    [ORG_D]: acct(ORG_D, NAME_D, 23, null, 1),                   // unknown
  };
  const m = W.widgetModel(active, NOW, { accounts, lastActiveOrg: ORG_A });
  assert.equal(m.others.length, 3, "precondition: all three branches are exercised");
  const EXPECTED = ["name", "state", "value", "meta", "tone", "stale"];
  for (const row of m.others) {
    assert.deepEqual(Object.keys(row).sort(), [...EXPECTED].sort(),
      `the ${row.state} branch carries a field nothing paints`);
  }
});

test("🔴 EVERY other account reaches the card -- no row is counted-but-unreachable", () => {
  // This replaces a test that pinned OTHERS_MAX = 4 and `othersMore = 2`.
  // The cap was real and it FIRED (the operator's live profile holds more
  // than four candidates), and the "+N more" line it produced was TERMINAL:
  // no click anywhere in the card could reveal those rows. A count of rows
  // you cannot see is worse than either showing them or not mentioning them.
  // The height bound moved into CSS (`.otherlist` scrolls), which keeps the
  // "card over his chat" constraint without hiding anything; the painter's
  // half is pinned in content_widget.test.mjs.
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const accounts = { [ORG_A]: active };
  // Six others, all measured, with distinct percentages so the order is the
  // rule's and not the map's.
  const pcts = [23, 31, 42, 53, 62, 71];
  pcts.forEach((p, i) => {
    const id = `9${i}999999-9999-4999-8999-999999999999`;
    accounts[id] = acct(id, `acct ${i}`, p, iso(NOW + (i + 1) * HOUR), 1);
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
  const active = acct(ORG_A, NAME_A, 37, iso(NOW + 30 * 60 * 1000), 0);
  const soon = acct(ORG_B, NAME_B, 62, iso(NOW + HOUR + 12 * 60 * 1000), 1);
  const later = acct(ORG_C, NAME_C, 23, iso(NOW + 5 * HOUR), 1);
  const m = W.widgetModel(active, NOW, {
    accounts: { [ORG_A]: active, [ORG_B]: soon, [ORG_C]: later }, lastActiveOrg: ORG_A,
  });
  assert.equal(m.nextFree, `next free: ${NAME_B} in 1h12m`,
    "the ACTIVE account's own sooner reset is not the answer");
  assert.equal(m.others[0].name, NAME_C,
    "precondition: the list's own first answer is a DIFFERENT account");

  // Nothing pending -> no line at all, rather than an empty one.
  const allFree = {
    [ORG_A]: active, [ORG_B]: acct(ORG_B, NAME_B, 91, iso(NOW - HOUR), 6),
  };
  assert.equal(W.widgetModel(active, NOW, { accounts: allFree, lastActiveOrg: ORG_A }).nextFree, null);
});

test("the account already ON the card never appears again under 'other accounts'", () => {
  const a = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const b = acct(ORG_B, NAME_B, 62, iso(NOW + HOUR), 1);
  const accounts = { [ORG_A]: a, [ORG_B]: b };

  assert.deepEqual(
    W.widgetModel(a, NOW, { accounts, lastActiveOrg: ORG_A }).others.map((r) => r.name), [NAME_B]);
  // pickRecord() falls back to the freshest record before any active org is
  // known; the shown record must still be excluded even though it is not the
  // "active" one.
  assert.deepEqual(
    W.widgetModel(a, NOW, { accounts, lastActiveOrg: null }).others.map((r) => r.name), [NAME_B]);
});

test("labels rename both the card's own header and the other rows", () => {
  const a = acct(ORG_A, NAME_A, 37, iso(NOW + 3 * HOUR), 0);
  const b = acct(ORG_B, NAME_B, 62, iso(NOW + HOUR), 1);
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
  ];
  for (const ctx of ctxs) {
    const m = W.widgetModel(r, NOW, ctx);
    assert.ok(Array.isArray(m.others), `ctx=${JSON.stringify(ctx)}`);
    assert.ok(m.nextFree === null || typeof m.nextFree === "string");
  }
});

test("the widget's warn threshold matches the service worker's toast threshold", async () => {
  // A widget that turns amber at a different number than the one that toasts
  // would make the two surfaces disagree about what "high" means.
  globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;
  const SW = await import("../extension/service_worker.js");
  assert.equal(W.WARN_PCT, SW.ALERT_THRESHOLD_PCT);
});
