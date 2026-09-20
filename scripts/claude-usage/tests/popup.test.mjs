// popup.test.mjs -- the pure render functions behind the dashboard.
//
// The DOM paint is glue; everything the popup SHOWS is computed by the
// exported pure functions, so the pins live here: ordering (active first,
// then freshest), the "as of" staleness display, the metric lines, credits
// math from minor units, the sparkline points, and the empty state.
import test from "node:test";
import assert from "node:assert/strict";

const P = await import("../extension/popup.js");
const { formatCountdown } = await import("../extension/lib/timefmt.js");
const { accountLabel } = await import("../extension/lib/format.js");
const { NAME_A, NAME_B, NOW, ORG_A, ORG_B, ORG_C, fullUsage } =
  await import("./fixtures.mjs");
const { normalizeUsage } = await import("../extension/lib/normalize.js");

function rec(orgUuid, orgName, sessionUtil, asOf) {
  const r = normalizeUsage(fullUsage(), orgUuid, orgName, asOf);
  r.session.utilization = sessionUtil;
  return r;
}

// --- ordering ------------------------------------------------------------------ //

test("ordering: the active account first, then freshest-first", () => {
  const accounts = {
    [ORG_A]: rec(ORG_A, NAME_A, 10, NOW - 3600 * 1000),   // active, but staler
    [ORG_B]: rec(ORG_B, NAME_B, 20, NOW),                 // freshest
  };
  const ordered = P.orderAccounts(accounts, ORG_A);
  assert.deepEqual(ordered.map((r) => r.orgUuid), [ORG_A, ORG_B],
    "active first even when staler");
  assert.equal(P.orderAccounts(accounts, ORG_B).map((r) => r.orgUuid)[0], ORG_B);

  // No active account: pure freshness order.
  const fresh = P.orderAccounts(accounts, null);
  assert.deepEqual(fresh.map((r) => r.orgUuid), [ORG_B, ORG_A]);
  assert.equal(P.orderAccounts({}, null).length, 0);
  assert.equal(P.orderAccounts(null, ORG_A).length, 0);
});

test("ordering: an active org with no stored record orders the rest by freshness", () => {
  const accounts = { [ORG_B]: rec(ORG_B, NAME_B, 20, NOW) };
  assert.deepEqual(P.orderAccounts(accounts, ORG_A).map((r) => r.orgUuid), [ORG_B]);
});

// --- the metric lines ------------------------------------------------------------ //

test("sessionLine renders the proposal's format", () => {
  const r = rec(ORG_A, NAME_A, 9, NOW);
  r.session.resetsAt = new Date(NOW + 4 * 3600 * 1000 + 46 * 60 * 1000).toISOString();
  assert.equal(P.sessionLine(r, NOW), "Session 9% · resets 4h46m");
});

test("sessionLine tolerates unknown and expired resets", () => {
  const r = rec(ORG_A, NAME_A, null, NOW);
  r.session.resetsAt = null;
  assert.equal(P.sessionLine(r, NOW), "Session ? · resets unknown");
  r.session.utilization = 9;
  r.session.resetsAt = new Date(NOW - 1000).toISOString();
  assert.equal(P.sessionLine(r, NOW), "Session 9% · resets soon");
});

test("weeklyLine and the claude-code share", () => {
  const r = rec(ORG_A, NAME_A, 10, NOW);
  assert.equal(P.weeklyLine(r), "Weekly 47%");
  r.codeWeeklyPercent = 31.6;
  assert.equal(
    r.codeWeeklyPercent && `Claude Code ${P.formatPct(r.codeWeeklyPercent)}`,
    "Claude Code 32%");
  r.codeWeeklyPercent = null;
  assert.equal(P.formatPct(null), "?");
});

test("formatPct rounds, never hallucinates precision", () => {
  assert.equal(P.formatPct(9), "9%");
  assert.equal(P.formatPct(9.4), "9%");
  assert.equal(P.formatPct(47.5), "48%");
  assert.equal(P.formatPct(undefined), "?");
});

test("creditsLine converts minor units and shows the disabled reason", () => {
  assert.equal(
    P.creditsLine({ enabled: true, limit: 10000, used: 2500, currency: "USD" }),
    "$75.00 credits left");
  assert.equal(
    P.creditsLine({ enabled: true, limit: 10000, used: null, currency: "EUR" }),
    "\u20ac100.00 credits left");
  assert.equal(
    P.creditsLine({ enabled: true, limit: 500, used: 0, currency: "XYZ" }),
    "XYZ 5.00 credits left");
  assert.equal(
    P.creditsLine({ enabled: false, used: 0, limit: 0, currency: "USD",
      disabledReason: "Monthly credit limit reached." }),
    "Credits disabled: Monthly credit limit reached.");
  assert.equal(P.creditsLine({ enabled: false }), "Credits disabled");
  assert.equal(P.creditsLine({ enabled: true, limit: null, used: 0 }), null,
    "no limit -> no made-up number");
  assert.equal(P.creditsLine(null), null);
});

// --- staleness ------------------------------------------------------------------ //

test("a fresh record says 'just now' and is not stale", () => {
  const row = P.renderRow(rec(ORG_A, NAME_A, 10, NOW), NOW, true);
  assert.equal(row.asOf, "just now");
  assert.equal(row.stale, false);
  assert.equal(row.isActive, true);
});

test("an old snapshot is labeled AND flagged (the 6h staleness line)", () => {
  const row = P.renderRow(rec(ORG_A, NAME_A, 10, NOW - 7 * 3600 * 1000), NOW, false);
  assert.equal(row.asOf, "7h ago · stale");
  assert.equal(row.stale, true);
  // Below the 6h line: labeled but not flagged.
  const fresh = P.renderRow(rec(ORG_A, NAME_A, 10, NOW - 3 * 3600 * 1000), NOW, false);
  assert.equal(fresh.asOf, "3h ago");
  assert.equal(fresh.stale, false);
});

test("an auth-stale record is stale even with a fresh asOf", () => {
  const r = rec(ORG_A, NAME_A, 10, NOW);
  r.staleSince = NOW - 1000;
  const row = P.renderRow(r, NOW, false);
  assert.equal(row.stale, true);
});

// --- sparkline ------------------------------------------------------------------ //

test("sparkline: under 2 usable samples -> null", () => {
  assert.equal(P.sparkline(null), null);
  assert.equal(P.sparkline([]), null);
  assert.equal(P.sparkline([[NOW, 10, 5]]), null);
  assert.equal(P.sparkline([[NOW, null, null], [NOW + 1, null, null]]), null,
    "null utilizations are not usable samples");
});

test("sparkline maps history to a 100x24 box, y inverted, nulls skipped", () => {
  const pts = P.sparkline([
    [NOW, 0, 5],
    [NOW + 1000, null, 6],
    [NOW + 2000, 100, 7],
  ]);
  assert.equal(pts, "0.0,24.0 100.0,0.0");
});

// --- per-account labels ------------------------------------------------------------ //
//
// The API's org names are long and near-identical ("user@example.com's
// Organization"), which is useless for deciding which account to switch to.
// The POPUP is the only writer: the in-page widget reads this map and never
// edits it, because a text input in a card floating over claude.ai's composer
// is the wrong affordance and a direct route back to round 1's
// pointer-events bug.

test("renderRow shows the operator's label, falling back to the org name", () => {
  const r = rec(ORG_A, NAME_A, 10, NOW);
  assert.equal(P.renderRow(r, NOW, false, { [ORG_A]: "personal" }).name, "personal");
  assert.equal(P.renderRow(r, NOW, false, { [ORG_B]: "work" }).name, NAME_A,
    "another account's label is not mine");
  assert.equal(P.renderRow(r, NOW, false).name, NAME_A, "no labels at all");
  assert.equal(P.renderRow(r, NOW, false, { [ORG_A]: "   " }).name, NAME_A,
    "a whitespace label is a REMOVE, not a nameless row");
  assert.equal(P.renderRow(r, NOW, false, { [ORG_A]: "  work  " }).name, "work", "trimmed");
});

test("renderRow carries the orgUuid the editor needs to address the account", () => {
  // The rename button writes labels[orgUuid]; a row without one gets no
  // button rather than an edit that lands nowhere.
  assert.equal(P.renderRow(rec(ORG_A, NAME_A, 10, NOW), NOW, false).orgUuid, ORG_A);
  const anon = rec(ORG_A, NAME_A, 10, NOW);
  anon.orgUuid = null;
  assert.equal(P.renderRow(anon, NOW, false).orgUuid, null);
});

test("nextLabels: an edit sets one account's label and touches no other", () => {
  const before = { [ORG_A]: "personal", [ORG_B]: "work" };
  const after = P.nextLabels(before, ORG_B, "client");
  assert.deepEqual(after, { [ORG_A]: "personal", [ORG_B]: "client" });
  assert.deepEqual(before, { [ORG_A]: "personal", [ORG_B]: "work" },
    "the stored map was mutated in place");
});

test("nextLabels: clearing the box REMOVES the override rather than storing ''", () => {
  // An empty string would render a nameless account everywhere it is read.
  for (const blank of ["", "   ", "\t\n", null, undefined, 42]) {
    assert.deepEqual(P.nextLabels({ [ORG_A]: "personal" }, ORG_A, blank), {},
      `text=${JSON.stringify(blank)}`);
  }
  assert.deepEqual(P.nextLabels({ [ORG_A]: "personal", [ORG_B]: "work" }, ORG_A, ""),
    { [ORG_B]: "work" }, "the other account survives the clear");
});

test("nextLabels trims, and drops junk it finds in the stored map", () => {
  assert.deepEqual(P.nextLabels({}, ORG_A, "  personal  "), { [ORG_A]: "personal" });
  assert.deepEqual(
    P.nextLabels({ [ORG_A]: 7, [ORG_B]: "  ", [ORG_C]: "keep" }, ORG_A, "personal"),
    { [ORG_C]: "keep", [ORG_A]: "personal" },
    "a non-string and a blank left by an older write are not carried forward");
});

test("nextLabels is total over garbage -- it feeds chrome.storage directly", () => {
  assert.deepEqual(P.nextLabels(null, ORG_A, "personal"), { [ORG_A]: "personal" });
  assert.deepEqual(P.nextLabels("nonsense", ORG_A, "personal"), { [ORG_A]: "personal" });
  assert.deepEqual(P.nextLabels(undefined, undefined, undefined), {});
  assert.deepEqual(P.nextLabels({ [ORG_A]: "personal" }, null, "x"), { [ORG_A]: "personal" },
    "no account named -> the map comes back unchanged");
  assert.deepEqual(P.nextLabels({ [ORG_A]: "personal" }, "", "x"), { [ORG_A]: "personal" });
});

test("the popup and the widget resolve a label through ONE implementation", () => {
  // popup.js re-exports lib/format.js's accountLabel rather than owning a
  // second copy; two copies is how the same fallback bug gets fixed once.
  assert.equal(P.accountLabel, accountLabel);
  assert.equal(P.ACCOUNT_LABELS_KEY, "accountLabels");
});

// --- empty state ------------------------------------------------------------------ //

test("the empty state is a real instruction, not a blank div", () => {
  assert.equal(typeof P.EMPTY_STATE_TEXT, "string");
  assert.ok(P.EMPTY_STATE_TEXT.includes("claude.ai"));
  assert.equal(P.orderAccounts({}, null).length, 0,
    "no accounts -> the renderer paints the empty state");
});
