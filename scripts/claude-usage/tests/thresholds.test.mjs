// thresholds.test.mjs -- the alert engine.
//
// Pins: the >=80 crossing fires ONCE, holding the value does not re-fire,
// recovery re-arms the next crossing (rate-limited by the dedup window), a
// fresh lastToast suppresses a re-fire even after state loss, locked_reason
// and credits-disabled alert the same way -- and the alarm-only path (a
// report that arrives with no page open at all) goes through the SAME
// evaluate-and-notify machinery, while a 401 produces silence.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";

const { chrome, storage, calls } = makeChromeMock();
globalThis.chrome = chrome;
globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;

const NOW = Date.parse("2026-09-19T13:00:00Z");
const ORG = "11111111-1111-4111-8111-111111111111";
const NAME = "user@example.com's Organization";

const SW = await import("../extension/service_worker.js");
const { evaluateAlerts, handleReport, TOAST_DEDUP_MS } = SW;
const { normalizeUsage } = await import("../extension/lib/normalize.js");

function record(util, ts = NOW) {
  const rec = normalizeUsage({}, ORG, NAME, ts);
  rec.session.utilization = util;
  rec.weekly.utilization = Math.floor(util / 2);
  return rec;
}

test("the >=80 crossing fires once", () => {
  const prev = record(79);
  const now = record(80);
  const alerts = evaluateAlerts(now, prev, NOW, {});
  assert.equal(alerts.filter((a) => a.kind === "session-high").length, 1);
  // First sighting already past the threshold (no prev) fires too.
  assert.equal(evaluateAlerts(record(95), null, NOW, {})
    .filter((a) => a.kind === "session-high").length, 1);
});

test("holding >=80 does not re-fire", () => {
  const prev = record(81);
  const alerts = evaluateAlerts(record(85), prev, NOW, {});
  assert.deepEqual(alerts, []);
});

test("recovery re-arms: dip below 80, then cross again", () => {
  const recovered = record(70);
  assert.deepEqual(evaluateAlerts(recovered, record(85), NOW, {}),
    [], "recovery itself is silent");
  const alerts = evaluateAlerts(record(82), recovered, NOW, {});
  assert.equal(alerts.filter((a) => a.kind === "session-high").length, 1,
    "the re-crossing fires");
});

test("a fresh lastToast suppresses a re-fire even with no prev (state loss)", () => {
  const lastToast = { [`${ORG}:session-high`]: NOW - 1000 };
  assert.deepEqual(evaluateAlerts(record(90), null, NOW, lastToast), []);
  // ...but the same edge after the dedup window fires.
  const staleToast = { [`${ORG}:session-high`]: NOW - TOAST_DEDUP_MS - 1 };
  assert.equal(evaluateAlerts(record(90), null, NOW, staleToast)
    .filter((a) => a.kind === "session-high").length, 1);
});

test("weekly has its own kind and its own threshold", () => {
  const rec = record(10);
  rec.weekly.utilization = 80;
  const alerts = evaluateAlerts(rec, record(10), NOW, {});
  assert.deepEqual(alerts.map((a) => a.kind), ["weekly-high"]);
});

test("locked_reason fires on becoming set and re-fires only after clearing", () => {
  const locked = record(50);
  locked.session.lockedReason = "Usage limit reached. Your Claude Code session will reset at 6pm.";
  assert.equal(evaluateAlerts(locked, record(50), NOW, {})
    .filter((a) => a.kind === "locked").length, 1);
  // Already locked -> silence.
  assert.deepEqual(evaluateAlerts(locked, locked, NOW, {}), []);
  // Cleared, then set again -> fires.
  assert.equal(evaluateAlerts(locked, record(50), NOW, {})
    .filter((a) => a.kind === "locked").length, 1);
});

test("a WEEKLY locked_reason fires on becoming set, under its own dedup kind", () => {
  // locked_reason exists on every window in the API; the weekly one must be
  // consumed too, and must not collide with the session "locked" kind.
  const locked = record(50);
  locked.weekly.lockedReason = "Weekly limit reached. Resets Wed 7:00 PM.";
  assert.equal(evaluateAlerts(locked, record(50), NOW, {})
    .filter((a) => a.kind === "locked-weekly").length, 1);
  // Already locked -> silence.
  assert.deepEqual(evaluateAlerts(locked, locked, NOW, {}), []);
  // Both locked at once -> two distinct kinds, not one swallowed by the other.
  const both = record(50);
  both.session.lockedReason = "Session locked.";
  both.weekly.lockedReason = "Weekly locked.";
  const kinds = evaluateAlerts(both, record(50), NOW, {}).map((a) => a.kind);
  assert.deepEqual(kinds.sort(), ["locked", "locked-weekly"]);
});

test("a credits disabled_reason fires once until credits re-enable", () => {
  const off = record(50);
  off.credits = { enabled: false, used: 0, limit: 0, currency: "USD",
    disabledReason: "Monthly credit limit reached." };
  assert.equal(evaluateAlerts(off, record(50), NOW, {})
    .filter((a) => a.kind === "credits").length, 1);
  assert.deepEqual(evaluateAlerts(off, off, NOW, {}), []);
  const reEnabled = record(50);
  reEnabled.credits = { enabled: true, used: 0, limit: 100, currency: "USD", disabledReason: null };
  assert.deepEqual(evaluateAlerts(reEnabled, off, NOW, {}), [],
    "re-enabling is silent");
  assert.equal(evaluateAlerts(off, reEnabled, NOW, {})
    .filter((a) => a.kind === "credits").length, 1, "disabling again re-arms");
});

test("degenerate inputs never throw and never crash the caller", () => {
  assert.deepEqual(evaluateAlerts(null, null, NOW, {}), []);
  assert.deepEqual(evaluateAlerts({}, null, NOW, {}), []);
  // A garbage prev is treated as ABSENT: the first sighting past the
  // threshold fires (the same rule a freshly installed extension obeys).
  assert.equal(evaluateAlerts(record(90), "garbage", NOW, {})
    .filter((a) => a.kind === "session-high").length, 1);
});

// --- the alarm-only path: handleReport with no page open ----------------------- //

const REPORT = (sessionUtil, overrides = {}) => ({
  type: "cu:usage-report",
  fetchedAt: NOW,
  orgs: [{ uuid: ORG, name: NAME }],
  activeUuid: ORG,
  results: [{ orgUuid: ORG, ok: true, usage: { five_hour: { utilization: sessionUtil } } }],
  ...overrides,
});

test("alarm-only path: a threshold report with no page open still notifies", async () => {
  const res = await handleReport(REPORT(85));
  assert.equal(res.ok, true);
  const kinds = calls.notifications.map((n) => `${n.title}`);
  assert.ok(kinds.some((t) => t.includes("session threshold")), JSON.stringify(kinds));
  // The alert was recorded for dedup.
  assert.ok(storage.lastToast[`${ORG}:session-high`]);
});

test("alarm-only path: a report below the threshold gives no alert notification", async () => {
  calls.notifications.length = 0;
  delete storage.lastToast;
  const res = await handleReport(REPORT(10));
  assert.equal(res.ok, true);
  // Only the summary toast (title = the account name) may exist.
  assert.ok(calls.notifications.every((n) => n.title.includes(NAME)),
    JSON.stringify(calls.notifications));
});

test("a 401 is SILENT: staleness is recorded, no notification exists", async () => {
  calls.notifications.length = 0;
  const res = await handleReport({
    type: "cu:usage-report", fetchedAt: NOW, orgs: [], activeUuid: null,
    results: [], orgsError: { status: 401, kind: "unauthorized" },
  });
  assert.equal(res.ok, true);
  assert.deepEqual(calls.notifications, [], "401 must never toast");
  const stored = storage.accounts[ORG];
  assert.ok(stored.staleSince, "the account is marked stale");
});

test("threshold firing is recorded in lastToast for the right (account, kind)", async () => {
  delete storage.lastToast;
  await handleReport(REPORT(88));
  const lt = storage.lastToast;
  assert.ok(lt[`${ORG}:session-high`], JSON.stringify(lt));
  assert.equal(lt[`${ORG}:summary`] !== undefined, true, "the summary kind is deduped separately");
});
