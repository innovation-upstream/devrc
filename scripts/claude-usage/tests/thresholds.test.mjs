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
const { fullUsage } = await import("./fixtures.mjs");

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

// --- an alert and the summary are ONE event, not two ------------------------- //
//
// 🔴 OPERATOR-REPORTED: "2 native system notifications when i opened
// claude.ai", and when asked what they SAID, they were DIFFERENT — a
// threshold alert and the account summary. No concurrency involved: ONE
// report, one handler, two toasts, because handleReport appends the summary
// independently of whatever evaluateAlerts returned.
//
// The two carry the same numbers; the alert just adds a reason. Two
// notifications landing together for one event is the symptom.
//
// The codebase already had this idea — the `switch` kind records the
// `summary` key alongside its own so a page-open cannot re-toast the same
// content — it simply was never applied to the alert path.

test("🔴 crossing a threshold fires the ALERT ONLY, not alert + summary", async () => {
  await chrome.storage.local.set({ accounts: {}, lastActiveOrg: null, lastToast: {} });
  calls.notifications.length = 0;

  const u = fullUsage();
  u.five_hour.utilization = 85;          // over ALERT_THRESHOLD_PCT
  await SW.enqueueReport({
    type: "cu:usage-report", fetchedAt: NOW,
    orgs: [{ uuid: ORG, name: NAME }], activeUuid: ORG,
    results: [{ orgUuid: ORG, ok: true, usage: u }],
  });

  assert.equal(calls.notifications.length, 1,
    `${calls.notifications.length} notifications for ONE event — `
    + calls.notifications.map((n) => n.title).join(" + "));
  assert.match(calls.notifications[0].title, /threshold/,
    "the ALERT is the one that survives — it carries the same numbers plus a reason");
});

test("the summary is SUPPRESSED, not merely delayed into the next report", async () => {
  // If the alert did not consume the summary's dedup slot, the next report
  // inside the window would deliver it — a stray duplicate arriving minutes
  // after the alert, which is the same complaint with a gap in the middle.
  await chrome.storage.local.set({ accounts: {}, lastActiveOrg: null, lastToast: {} });
  calls.notifications.length = 0;

  const u = fullUsage();
  u.five_hour.utilization = 85;
  const send = (at) => SW.enqueueReport({
    type: "cu:usage-report", fetchedAt: at,
    orgs: [{ uuid: ORG, name: NAME }], activeUuid: ORG,
    results: [{ orgUuid: ORG, ok: true, usage: u }],
  });
  await send(NOW);
  await send(NOW + 60 * 1000);           // a minute later, well inside TOAST_DEDUP_MS
  assert.equal(calls.notifications.length, 1,
    "the summary arrived late instead of being suppressed");
});

test("with NO alert firing, the summary still toasts", async () => {
  // The suppression must not silence the ordinary page-open summary, which is
  // the feature working as intended.
  await chrome.storage.local.set({ accounts: {}, lastActiveOrg: null, lastToast: {} });
  calls.notifications.length = 0;

  await SW.enqueueReport({
    type: "cu:usage-report", fetchedAt: NOW,
    orgs: [{ uuid: ORG, name: NAME }], activeUuid: ORG,
    results: [{ orgUuid: ORG, ok: true, usage: fullUsage() }],   // 9% — calm
  });
  assert.equal(calls.notifications.length, 1);
  assert.doesNotMatch(calls.notifications[0].title, /threshold/);
});

test("an alert on a NON-active account does not suppress the active one's summary", async () => {
  // The suppression is per-account. A threshold crossing on a background org
  // must not silence the summary for the org the operator is looking at.
  const ORG_B = "22222222-2222-4222-8222-222222222222";
  await chrome.storage.local.set({ accounts: {}, lastActiveOrg: null, lastToast: {} });
  calls.notifications.length = 0;

  const hot = fullUsage(); hot.five_hour.utilization = 85;
  await SW.enqueueReport({
    type: "cu:usage-report", fetchedAt: NOW,
    orgs: [{ uuid: ORG, name: NAME }, { uuid: ORG_B, name: "Second Org" }],
    activeUuid: ORG,
    results: [
      { orgUuid: ORG, ok: true, usage: fullUsage() },   // active, calm
      { orgUuid: ORG_B, ok: true, usage: hot },           // background, hot
    ],
  });
  const titles = calls.notifications.map((n) => n.title);
  assert.equal(titles.length, 2, `expected the B alert AND the A summary, got ${titles.join(" + ")}`);
  assert.ok(titles.some((t) => /threshold/.test(t)), "B's alert is missing");
  assert.ok(titles.some((t) => t === NAME), "A's summary was wrongly suppressed");
});
