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
const { NAME_A, NAME_B, NOW, ORG_A, ORG_B, fullUsage } =
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

// --- empty state ------------------------------------------------------------------ //

test("the empty state is a real instruction, not a blank div", () => {
  assert.equal(typeof P.EMPTY_STATE_TEXT, "string");
  assert.ok(P.EMPTY_STATE_TEXT.includes("claude.ai"));
  assert.equal(P.orderAccounts({}, null).length, 0,
    "no accounts -> the renderer paints the empty state");
});
