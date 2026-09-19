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
const { badgeFor, formatBadgePct, severityColor } = SW;
const { normalizeUsage } = await import("../extension/lib/normalize.js");

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

test("severity colors: known strings map, unknown strings are amber, absence is green", () => {
  assert.equal(severityColor("high"), "#d93025");
  assert.equal(severityColor("critical"), "#d93025");
  assert.equal(severityColor("medium"), "#f9ab00");
  assert.equal(severityColor("elevated"), "#f9ab00");
  assert.equal(severityColor("low"), "#31a73c");
  assert.equal(severityColor("none"), "#31a73c");
  assert.equal(severityColor(null), "#31a73c");
  assert.equal(severityColor(""),
    "#31a73c", "empty string is absence, not an unknown severity");
  assert.equal(severityColor("sev_from_a_future_release"), "#f9ab00",
    "an unknown severity must not render as OK");
});

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
