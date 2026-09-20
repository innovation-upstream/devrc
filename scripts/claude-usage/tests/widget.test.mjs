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
const { NAME_A, NAME_B, NOW, ORG_A, ORG_B, fullUsage, nullUsage } =
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
  }
});

test("the widget's warn threshold matches the service worker's toast threshold", async () => {
  // A widget that turns amber at a different number than the one that toasts
  // would make the two surfaces disagree about what "high" means.
  globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;
  const SW = await import("../extension/service_worker.js");
  assert.equal(W.WARN_PCT, SW.ALERT_THRESHOLD_PCT);
});
