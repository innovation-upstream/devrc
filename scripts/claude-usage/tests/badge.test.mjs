// badge.test.mjs -- the toolbar badge.
//
// Pins: % formatting (including the >2-digit clamp), severity colors, the
// stale gray that overrides everything, and the no-account state.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";

const { chrome } = makeChromeMock();
globalThis.chrome = chrome;
globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;

const NOW = Date.parse("2026-09-19T13:00:00Z");
const ORG = "11111111-1111-4111-8111-111111111111";
const NAME = "user@example.com's Organization";

const SW = await import("../extension/service_worker.js");
const { badgeFor, formatBadgePct } = SW;
const { normalizeUsage } = await import("../extension/lib/normalize.js");
// The widget's own entry points, so the badge/widget agreement test below
// compares the two SHIPPED paths rather than re-deriving either of them here.
const { toneColor: TONE_COLOR } = await import("../extension/lib/severity.js");
const { toneFor: WIDGET_TONE } = await import("../extension/lib/widget.js");

function rec(asOf = NOW) {
  return normalizeUsage({}, ORG, NAME, asOf);
}

test("% formatting rounds and stays an integer string", () => {
  assert.equal(formatBadgePct(9), "9");
  assert.equal(formatBadgePct(9.4), "9");
  assert.equal(formatBadgePct(9.5), "10");
  assert.equal(formatBadgePct(47), "47");
  assert.equal(formatBadgePct(0), "0");
});

test(">2-digit values clamp to '99+' -- the badge never truncates to a lie", () => {
  assert.equal(formatBadgePct(99.4), "99");
  assert.equal(formatBadgePct(99.5), "99+");
  assert.equal(formatBadgePct(100), "99+");
  assert.equal(formatBadgePct(250), "99+");
  assert.equal(formatBadgePct(null), "?");
  assert.equal(formatBadgePct(undefined), "?");
});

// The old `severityColor()` export is GONE, not merely deprecated. It had one
// caller, badgeFor, which now goes through lib/severity.js; keeping it would
// have left a function nothing calls, pinned by five assertions, whose body
// re-introduced the very null-vs-unknown conflation lib/severity.js warns
// about. Its severity-string mapping is covered by severityTone() in
// severity.test.mjs, where the single implementation now lives.

function accountWith(sessionUtil, asOf = NOW) {
  const r = rec(asOf);
  r.session.utilization = sessionUtil;
  return r;
}

test("badgeFor: the active account's session % in its severity color", () => {
  const r = accountWith(9);
  r.severity = "high";
  const badge = badgeFor({ [ORG]: r }, ORG, NOW);
  assert.equal(badge.text, "9");
  assert.equal(badge.color, "#d93025");
  assert.ok(badge.title.includes(NAME));
});

test("badgeFor: 99% with NO severity row is not green (the hole this closed)", () => {
  // REGRESSION. Before lib/severity.js the badge coloured from the severity
  // string alone and severityColor(null) returned green, so this exact record
  // -- which `limits: null` produces, a shape recon observed -- painted a calm
  // green badge while the account was one percent from its cap. Watched RED at
  // the pre-change tip: it returned '#31a73c'.
  const r = accountWith(99);
  r.severity = null;
  const badge = badgeFor({ [ORG]: r }, ORG, NOW);
  assert.equal(badge.color, "#d93025", "the percent band must supply the answer");
  assert.equal(badge.text, "99");
});

test("badgeFor: a severity string can ESCALATE the percent band, never calm it", () => {
  // The worst-of rule, in both directions, so a mutant that picks either
  // single source dies here.
  const calmPctLoudSev = accountWith(5);
  calmPctLoudSev.weekly.utilization = 5;
  calmPctLoudSev.severity = "critical";
  assert.equal(badgeFor({ [ORG]: calmPctLoudSev }, ORG, NOW).color, "#d93025",
    "API says critical at 5% -> the API wins");

  const loudPctCalmSev = accountWith(99);
  loudPctCalmSev.weekly.utilization = 99;
  loudPctCalmSev.severity = "low";
  assert.equal(badgeFor({ [ORG]: loudPctCalmSev }, ORG, NOW).color, "#d93025",
    "API says low at 99% -> the percentage wins; 'low' must not calm it");
});

test("badgeFor and the in-page widget cannot disagree about one record", () => {
  // The claim the widget's comment used to make on the strength of a shared
  // PALETTE. Now it rests on a shared PREDICATE, so assert it directly across
  // the cases where the two old rules diverged.
  const cases = [
    { pct: 99, sev: null }, { pct: 99, sev: "low" }, { pct: 5, sev: "critical" },
    { pct: 85, sev: null }, { pct: 50, sev: "medium" }, { pct: 10, sev: "none" },
  ];
  for (const c of cases) {
    const r = accountWith(c.pct);
    r.weekly.utilization = c.pct;
    r.severity = c.sev;
    const badgeColor = badgeFor({ [ORG]: r }, ORG, NOW).color;
    const widgetColor = TONE_COLOR(WIDGET_TONE(r, NOW));
    assert.equal(badgeColor, widgetColor,
      `disagreed at pct=${c.pct} sev=${c.sev}`);
  }
});

test("badgeFor: stale snapshot (>6h) goes gray, number still shown", () => {
  const r = accountWith(9, NOW - 7 * 3600 * 1000);
  r.severity = "high";
  const badge = badgeFor({ [ORG]: r }, ORG, NOW);
  assert.equal(badge.color, "#80868b");
  assert.equal(badge.text, "9");
});

test("badgeFor: an auth-stale account (401/403) goes gray immediately", () => {
  const r = accountWith(9);
  r.staleSince = NOW - 1000;
  const badge = badgeFor({ [ORG]: r }, ORG, NOW);
  assert.equal(badge.color, "#80868b");
});

test("badgeFor: stale with an unknown % clears the text instead of showing '?'", () => {
  const r = rec(NOW - 7 * 3600 * 1000);
  assert.equal(badgeFor({ [ORG]: r }, ORG, NOW).text, "");
});

test("badgeFor: no active account -> empty text, gray", () => {
  const badge = badgeFor({}, ORG, NOW);
  assert.equal(badge.text, "");
  assert.equal(badge.color, "#80868b");
  assert.equal(badgeFor({}, null, NOW).text, "");
  assert.equal(badgeFor(null, null, NOW).text, "");
  // accounts === null with a TRUTHY lastActiveOrg must also be safe (the
  // short-circuit used to evaluable accounts[lastActiveOrg] and throw).
  assert.equal(badgeFor(null, ORG, NOW).text, "");
});
