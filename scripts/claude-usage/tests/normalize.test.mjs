// normalize.test.mjs -- raw usage JSON -> stored record.
//
// Pins the defensive-normalization contract: the recon'd schema's golden
// shape, EVERY null variant tolerated, unknown fields dropped, the claude_code
// row extracted, severity/is_active mapping -- and never throwing, whatever
// drift arrives.
import test from "node:test";
import assert from "node:assert/strict";

import {
  HISTORY_MAX,
  MIN_SAMPLE_GAP_MS,
  mergeStored,
  normalizeStoredRecord,
  normalizeUsage,
} from "../extension/lib/normalize.js";
import { NAME_A, NOW, ORG_A, fullUsage, nullUsage } from "./fixtures.mjs";

test("golden full-schema payload normalizes to the stored record shape", () => {
  const rec = normalizeUsage(fullUsage(), ORG_A, NAME_A, NOW);
  assert.equal(rec.orgUuid, ORG_A);
  assert.equal(rec.orgName, NAME_A);
  assert.deepEqual(rec.session, { utilization: 9, resetsAt: "2026-09-19T18:00:00Z", lockedReason: null });
  assert.deepEqual(rec.weekly, { utilization: 47, resetsAt: "2026-09-24T00:00:00Z", lockedReason: null });
  // weekly_scoped limit + the named seven_day_opus window; session and
  // weekly_all limits do NOT duplicate into extra rows.
  assert.deepEqual(rec.extraRows, [
    {
      kind: "weekly_scoped", label: "Opus", percent: 12, severity: "low",
      resetsAt: "2026-09-24T00:00:00Z", isActive: false,
    },
    { label: "Opus (7-day)", percent: 12, resetsAt: "2026-09-24T00:00:00Z" },
  ]);
  assert.deepEqual(rec.credits, {
    enabled: true, used: 2500, limit: 10000, currency: "USD", disabledReason: null,
  });
  assert.equal(rec.codeWeeklyPercent, 31, "the namesake claude_code row");
  assert.equal(rec.severity, "medium", "most severe ACTIVE limit");
  assert.equal(rec.isActiveLimit, true);
  assert.equal(rec.asOf, NOW);
  assert.deepEqual(rec.history, []);
});

test("unknown fields are dropped, not passed through", () => {
  const raw = fullUsage();
  raw.some_future_field = { deep: { junk: [1, 2, 3] } };
  raw.five_hour.extra_junk = "x";
  const rec = normalizeUsage(raw, ORG_A, NAME_A, NOW);
  const allowed = new Set([
    "orgUuid", "orgName", "session", "weekly", "extraRows", "credits",
    "codeWeeklyPercent", "severity", "isActiveLimit", "asOf", "staleSince", "history",
  ]);
  for (const key of Object.keys(rec)) {
    assert.ok(allowed.has(key), `unexpected key ${key} survived normalization`);
  }
  assert.equal("some_future_field" in rec, false);
});

test("every recon-observed null variant is tolerated", () => {
  const rec = normalizeUsage(nullUsage(), ORG_A, NAME_A, NOW);
  assert.deepEqual(rec.session, { utilization: null, resetsAt: null, lockedReason: null });
  assert.deepEqual(rec.weekly, { utilization: null, resetsAt: null, lockedReason: null });
  assert.deepEqual(rec.extraRows, []);
  assert.deepEqual(rec.credits, {
    enabled: null, used: null, limit: null, currency: null, disabledReason: null,
  });
  assert.equal(rec.codeWeeklyPercent, null);
  assert.equal(rec.severity, null);
  assert.equal(rec.isActiveLimit, false);
});

test("never throws, whatever the drift", () => {
  const hostile = [
    null, undefined, 42, "usage", true, [], {},
    { five_hour: "junk", seven_day: 7 },
    { limits: [null, 5, "x", {}] },
    { extra_usage: "disabled" },
    { seven_day_breakdown: { rows: "nope" } },
    { seven_day_breakdown: { rows: [null, { key: "claude_code", percent: "junk" }] } },
    { nimbus_quill: { utilization: "junk", resets_at: 42 } },
    { five_hour: { utilization: NaN } },
  ];
  for (const raw of hostile) {
    assert.doesNotThrow(() => normalizeUsage(raw, ORG_A, NAME_A, NOW), JSON.stringify(raw));
  }
  const rec = normalizeUsage("total garbage", ORG_A, NAME_A, NOW);
  assert.equal(rec.session.utilization, null);
  assert.deepEqual(rec.extraRows, []);
});

test("codename windows become opaque extra rows when non-null", () => {
  const rec = normalizeUsage(
    { nimbus_quill: { utilization: 3, resets_at: "2026-09-20T00:00:00Z" } },
    ORG_A, NAME_A, NOW);
  assert.deepEqual(rec.extraRows, [
    { label: "nimbus_quill", percent: 3, resetsAt: "2026-09-20T00:00:00Z" },
  ]);
});

test("severity comes from the most severe ACTIVE limit only", () => {
  const rec = normalizeUsage(fullUsage({
    limits: [
      { kind: "session", percent: 5, severity: "high", resets_at: null, is_active: false },
      { kind: "weekly_all", percent: 50, severity: "low", resets_at: null, is_active: true },
    ],
  }), ORG_A, NAME_A, NOW);
  assert.equal(rec.severity, "low", "inactive high-severity rows do not win");
  assert.equal(rec.isActiveLimit, true);

  const none = normalizeUsage(fullUsage({ limits: [] }), ORG_A, NAME_A, NOW);
  assert.equal(none.severity, null);
  assert.equal(none.isActiveLimit, false);
});

test("string numbers are accepted; junk is not", () => {
  const rec = normalizeUsage(
    { five_hour: { utilization: "9.5" }, seven_day: { utilization: true } },
    ORG_A, NAME_A, NOW);
  assert.equal(rec.session.utilization, 9.5);
  assert.equal(rec.weekly.utilization, null);
});

test("resets_at persists RAW -- a non-ISO string is stored verbatim or dropped", () => {
  const iso = normalizeUsage({ seven_day: { utilization: 1, resets_at: "2026-09-24T00:00:00Z" } },
    ORG_A, NAME_A, NOW);
  assert.equal(iso.weekly.resetsAt, "2026-09-24T00:00:00Z");
  const junk = normalizeUsage({ seven_day: { utilization: 1, resets_at: 42 } },
    ORG_A, NAME_A, NOW);
  assert.equal(junk.weekly.resetsAt, null);
});

// --- mergeStored: the ring buffer --------------------------------------------- //

function recordWith(util, ts) {
  const rec = normalizeUsage(fullUsage(), ORG_A, NAME_A, ts);
  rec.session.utilization = util;
  rec.weekly.utilization = util / 2;
  return rec;
}

test("history samples are 15-min deduped", () => {
  let rec = recordWith(10, NOW);
  let stored = mergeStored(null, rec, NOW);
  assert.equal(stored.history.length, 1);
  // 14:59 later: no second sample.
  stored = mergeStored(stored, recordWith(11, NOW + MIN_SAMPLE_GAP_MS - 1), NOW + MIN_SAMPLE_GAP_MS - 1);
  assert.equal(stored.history.length, 1);
  // at the 15-min boundary: sampled.
  stored = mergeStored(stored, recordWith(12, NOW + MIN_SAMPLE_GAP_MS), NOW + MIN_SAMPLE_GAP_MS);
  assert.equal(stored.history.length, 2);
  assert.deepEqual(stored.history[1], [NOW + MIN_SAMPLE_GAP_MS, 12, 6]);
});

test("history is capped at the 7-day ring size and evicts 7-day-old samples", () => {
  let stored = null;
  const step = MIN_SAMPLE_GAP_MS;
  for (let i = 0; i < HISTORY_MAX + 50; i++) {
    stored = mergeStored(stored, recordWith(i, NOW + i * step), NOW + i * step);
  }
  assert.ok(stored.history.length <= HISTORY_MAX, `cap exceeded: ${stored.history.length}`);
  const cutoff = NOW + (HISTORY_MAX + 49) * step - 7 * 24 * 60 * 60 * 1000;
  for (const sample of stored.history) {
    assert.ok(sample[0] >= cutoff, "a sample older than 7 days survived eviction");
  }
  assert.equal(stored.history[stored.history.length - 1][1], HISTORY_MAX + 49,
    "the newest sample survived the cap");
});

test("mergeStored is a default-shapes migration: broken stored records do not throw", () => {
  const fresh = recordWith(10, NOW);
  for (const broken of [null, undefined, 42, {}, { history: "nope" }, { history: [null, [1], ["x", "y", "z"]] }]) {
    const merged = mergeStored(broken, fresh, NOW);
    assert.ok(Array.isArray(merged.history));
    assert.equal(merged.history.length >= 1, true);
    assert.equal(merged.staleSince, null, "a fresh snapshot clears staleness");
    assert.equal(merged.asOf, NOW, "a merge never backdates freshness");
  }
});

test("normalizeStoredRecord fills every missing field of a partial stored record", () => {
  const rec = normalizeStoredRecord({ orgUuid: ORG_A, orgName: NAME_A });
  assert.deepEqual(rec.session, { utilization: null, resetsAt: null, lockedReason: null });
  assert.deepEqual(rec.weekly, { utilization: null, resetsAt: null, lockedReason: null });
  assert.deepEqual(rec.extraRows, []);
  assert.deepEqual(rec.credits, {
    enabled: null, used: null, limit: null, currency: null, disabledReason: null,
  });
  assert.equal(rec.codeWeeklyPercent, null);
  assert.equal(rec.severity, null);
  assert.equal(rec.isActiveLimit, false);
  assert.equal(rec.asOf, 0);
  assert.equal(rec.staleSince, null);
  assert.deepEqual(rec.history, []);
});

test("normalizeStoredRecord keeps history of the wrong shape out", () => {
  const rec = normalizeStoredRecord({
    orgUuid: ORG_A,
    history: [[NOW, 10, 5], null, [1, 2], ["a", "b", "c"], [NOW, 20, 8]],
  });
  assert.deepEqual(rec.history, [[NOW, 10, 5], [NOW, 20, 8]]);
});
