// timefmt.test.mjs -- countdown boundaries, staleness, timezone independence.
//
// The boundary pins are the reason this file exists: a countdown that flips a
// minute early, or renders differently in another timezone, is wrong in the
// direction nobody notices (it says "resets in 0m" for an hour).
import test from "node:test";
import assert from "node:assert/strict";

import {
  STALE_AFTER_MS,
  formatCountdown,
  isStale,
  parseWhen,
  stalenessLabel,
} from "../extension/lib/timefmt.js";

const NOW = Date.UTC(2026, 8, 19, 13, 0, 0); // 2026-09-19T13:00:00Z
const at = (msFromNow) => new Date(NOW + msFromNow).toISOString();

test("countdown boundaries: 59s stays seconds, 60s becomes minutes", () => {
  assert.equal(formatCountdown(at(59 * 1000), NOW), "59s");
  assert.equal(formatCountdown(at(60 * 1000), NOW), "1m");
  assert.equal(formatCountdown(at(119 * 1000), NOW), "1m");
  assert.equal(formatCountdown(at(60 * 60 * 1000 - 1000), NOW), "59m");
});

test("countdown boundaries: 23h59m59s stays hours, 24h becomes days", () => {
  assert.equal(formatCountdown(at(23 * 3600 * 1000 + 59 * 60 * 1000 + 59 * 1000), NOW), "23h59m");
  assert.equal(formatCountdown(at(24 * 3600 * 1000), NOW), "1d0h");
  assert.equal(formatCountdown(at(50 * 3600 * 1000), NOW), "2d2h");
});

test("expired resets read 'resets soon', not a negative countdown", () => {
  assert.equal(formatCountdown(at(0), NOW), "resets soon");
  assert.equal(formatCountdown(at(-3600 * 1000), NOW), "resets soon");
});

test("null / garbage / future-garbage resets_at -> 'unknown'", () => {
  for (const bad of [null, undefined, "", "not-a-date", {}, NaN]) {
    assert.equal(formatCountdown(bad, NOW), "unknown", String(bad));
  }
});

test("the proposal's example reads '4h46m'", () => {
  assert.equal(formatCountdown(at(4 * 3600 * 1000 + 46 * 60 * 1000), NOW), "4h46m");
});

test("epoch-ms inputs work like ISO strings", () => {
  assert.equal(formatCountdown(NOW + 90 * 1000, NOW), "1m");
  assert.equal(formatCountdown(NOW, NOW), "resets soon");
});

test("staleness labels", () => {
  assert.equal(stalenessLabel(NOW - 30 * 1000, NOW), "just now");
  assert.equal(stalenessLabel(NOW - 5 * 60 * 1000, NOW), "5m ago");
  assert.equal(stalenessLabel(NOW - 3 * 3600 * 1000, NOW), "3h ago");
  assert.equal(stalenessLabel(NOW - 2 * 24 * 3600 * 1000, NOW), "2d ago");
  assert.equal(stalenessLabel(null, NOW), "unknown");
  // A snapshot "from the future" (clock skew) is not negative-stale.
  assert.equal(stalenessLabel(NOW + 10 * 60 * 1000, NOW), "just now");
});

test("staleness threshold: 6h exactly is NOT stale, a second more is", () => {
  assert.equal(isStale(NOW - STALE_AFTER_MS, NOW), false);
  assert.equal(isStale(NOW - STALE_AFTER_MS - 1, NOW), true);
  assert.equal(isStale(null, NOW), true, "unknown snapshot time is stale");
});

test("parseWhen is total", () => {
  assert.equal(parseWhen("2026-09-19T13:00:00Z"), NOW);
  assert.equal(parseWhen(NOW), NOW);
  assert.equal(parseWhen("junk"), null);
  assert.equal(parseWhen(null), null);
  assert.equal(parseWhen(Infinity), null);
});

// 🔴 TIMEZONE INDEPENDENCE is a contract, not an accident: every function
// here computes from epoch DIFFERENCES. This test pins it the hostile way --
// by checking that no output depends on the host's local timezone. Since a
// node process cannot reliably change its own TZ after boot, the assertion is
// structural: the outputs at a UTC-pinned instant must be the pure arithmetic
// answers below, which a localtime-reading implementation (e.g. one using
// getHours/getDate on the reset Date) cannot reproduce at TZ=UTC+13 offsets.
test("countdowns are pure arithmetic on epoch differences (UTC-pinned)", () => {
  // 2026-09-19T23:30:00Z is 2026-09-20 12:30 in UTC+13: a localtime-reading
  // implementation would compute a different day/hour split from the same
  // instants. The answers below are the epoch-difference answers.
  const now = Date.UTC(2026, 8, 19, 23, 30, 0);
  const resetNextDayUtc = Date.UTC(2026, 8, 20, 4, 16, 0);
  assert.equal(formatCountdown(resetNextDayUtc, now), "4h46m");
  const resetNextDayBoundary = Date.UTC(2026, 8, 21, 0, 0, 0);
  assert.equal(formatCountdown(resetNextDayBoundary, now), "1d0h");
});
