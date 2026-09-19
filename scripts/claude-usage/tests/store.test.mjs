// store.test.mjs -- the chrome.storage.local layer.
//
// Pins: per-org upsert (two accounts never bleed into each other), the
// history ring buffer's 15-min dedup and 7-day eviction AS OBSERVED THROUGH
// STORAGE, and migration/default shapes -- whatever junk sits in storage
// (extension update, partial write), readState hands back full-shape records
// instead of throwing.
import test from "node:test";
import assert from "node:assert/strict";

import { makeChromeMock } from "./sw-mock.mjs";

const { chrome, storage } = makeChromeMock();
globalThis.chrome = chrome;
globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;

const NOW = Date.parse("2026-09-19T13:00:00Z");
const MIN15 = 15 * 60 * 1000;
const ORG_A = "11111111-1111-4111-8111-111111111111";
const ORG_B = "22222222-2222-4222-8222-222222222222";
const NAME_A = "user@example.com's Organization";
const NAME_B = "Work Org <work@example.com>";

const SW = await import("../extension/service_worker.js");
const { handleReport, readState } = SW;
// HISTORY_MAX lives in the lib; the SW does not re-export it (a missing
// re-export read as `undefined + 28` here and silently seeded nothing).
const { HISTORY_MAX, HISTORY_TTL_MS } = await import("../extension/lib/normalize.js");

const report = (orgUuid, orgName, sessionUtil, overrides = {}) => ({
  type: "cu:usage-report",
  fetchedAt: NOW,
  orgs: [{ uuid: orgUuid, name: orgName }],
  activeUuid: orgUuid,
  results: [{
    orgUuid, ok: true,
    usage: { five_hour: { utilization: sessionUtil }, seven_day: { utilization: sessionUtil } },
  }],
  ...overrides,
});

test("upsert is per-org: two accounts store independently", async () => {
  await handleReport(report(ORG_A, NAME_A, 10));
  await handleReport(report(ORG_B, NAME_B, 20));
  const { accounts } = await readState();
  assert.equal(Object.keys(accounts).length, 2);
  assert.equal(accounts[ORG_A].session.utilization, 10);
  assert.equal(accounts[ORG_B].session.utilization, 20);
  assert.equal(accounts[ORG_A].orgName, NAME_A);
  assert.equal(accounts[ORG_B].orgName, NAME_B);
  assert.ok(accounts[ORG_A].history.length === 1, JSON.stringify(accounts[ORG_A].history));
});

test("the 15-min dedup survives the storage round-trip", async () => {
  storage.accounts = {};
  await handleReport(report(ORG_A, NAME_A, 10, { fetchedAt: NOW }));
  // 10 min later: still one sample.
  await handleReport(report(ORG_A, NAME_A, 12, { fetchedAt: NOW + 10 * 60 * 1000 }));
  let { accounts } = await readState();
  assert.equal(accounts[ORG_A].history.length, 1);
  // past the boundary: sampled.
  await handleReport(report(ORG_A, NAME_A, 14, { fetchedAt: NOW + MIN15 }));
  ({ accounts } = await readState());
  assert.equal(accounts[ORG_A].history.length, 2);
  // the sample values are the snapshots at those times
  assert.deepEqual(accounts[ORG_A].history[1], [NOW + MIN15, 14, 14]);
});

test("7-day eviction applies to history loaded FROM storage", async () => {
  const ancient = NOW - HISTORY_TTL_MS - 60 * 1000;
  storage.accounts = {
    [ORG_A]: {
      orgUuid: ORG_A, orgName: NAME_A, asOf: NOW - 16 * 60 * 1000,
      session: { utilization: 5, resetsAt: null, lockedReason: null },
      weekly: { utilization: 5, resetsAt: null },
      // 16 min old: past the 15-min dedup, so the fresh report appends.
      history: [[ancient, 1, 1], [NOW - 16 * 60 * 1000, 5, 5]],
    },
  };
  await handleReport(report(ORG_A, NAME_A, 6));
  const { accounts } = await readState();
  const hist = accounts[ORG_A].history;
  assert.equal(hist.some((s) => s[0] === ancient), false,
    "a 7-day-old sample survived a fresh upsert");
  assert.ok(hist.some((s) => s[0] === NOW), "the fresh sample is present");
});

test("the ring cap holds even when storage arrives pre-filled", async () => {
  // A migration can hand us denser-than-15-min history (700 samples at a
  // 10-min cadence, all inside the 7-day window). The cap must still bind
  // after the next append.
  const step = 10 * 60 * 1000;
  const history = [];
  for (let i = 0; i < HISTORY_MAX + 28; i++) {
    history.push([NOW - 16 * 60 * 1000 - (HISTORY_MAX + 27 - i) * step, i, i]);
  }
  storage.accounts = {
    [ORG_A]: {
      orgUuid: ORG_A, orgName: NAME_A, asOf: NOW, history,
      session: { utilization: 5, resetsAt: null, lockedReason: null },
      weekly: { utilization: 5, resetsAt: null },
    },
  };
  await handleReport(report(ORG_A, NAME_A, 6, { fetchedAt: NOW }));
  const { accounts } = await readState();
  assert.equal(accounts[ORG_A].history.length <= HISTORY_MAX, true,
    `cap exceeded: ${accounts[ORG_A].history.length}`);
  assert.deepEqual(accounts[ORG_A].history.at(-1), [NOW, 6, 6],
    "the fresh sample survived the cap");
});

test("migration/default shapes: garbage in storage never throws on read", async () => {
  storage.accounts = {
    [ORG_A]: null,
    [ORG_B]: { orgUuid: ORG_B },                       // partial record
    broken: "not an object at all",                    // junk key survives read...
  };
  const { accounts } = await readState();
  assert.ok(accounts[ORG_A], "null record normalizes to the default shape");
  assert.equal(accounts[ORG_A].session.utilization, null);
  assert.deepEqual(accounts[ORG_B].extraRows, []);
  assert.equal(accounts.broken.orgName, "unknown account");
});

test("readState defaults: missing or mistyped top-level keys", async () => {
  storage.accounts = { [ORG_A]: { orgUuid: ORG_A } };
  delete storage.lastActiveOrg;
  delete storage.lastToast;
  const s1 = await readState();
  assert.deepEqual(s1.lastToast, {});
  assert.equal(s1.lastActiveOrg, null);
  storage.lastToast = "garbage";
  storage.lastActiveOrg = 42;
  const s2 = await readState();
  assert.deepEqual(s2.lastToast, {});
  assert.equal(s2.lastActiveOrg, null);
});

test("handleReport ignores a report that is not a usage report", async () => {
  const res = await handleReport({ type: "something-else" });
  assert.equal(res.ok, false);
  assert.equal(res.error, "bad-message");
  const res2 = await handleReport(null);
  assert.equal(res2.ok, false);
});
