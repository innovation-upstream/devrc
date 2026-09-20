// availability.test.mjs -- "which account can I switch to right now?"
//
// 🔴 THE DEFECT THESE PIN. A stored account can only be re-measured while you
// are logged INTO it (content_probe.js fetches with the current session
// cookie), so the moment its five-hour reset passes, the extension's stored
// snapshot describes a window that no longer exists. The old display rule --
// formatCountdown()'s "resets soon", justified by "the next snapshot will
// correct it" -- is therefore exactly wrong for every account except the
// active one: it reports the freest account as the one still at 92%.
//
// lib/availability.js turns the elapsed reset into a verdict instead. These
// tests pin the verdict, the boundary, the ordering it drives, and the
// totality that lets it run inside the operator's real claude.ai tab.
//
// Fixture percentages are deliberately pairwise distinct AND distinct from
// every constant the assertions name (WARN_PCT 80, CRIT_PCT 95, 0, 100), so a
// mutant that hardcodes a literal cannot survive by coincidence.
import test from "node:test";
import assert from "node:assert/strict";

const A = await import("../extension/lib/availability.js");
const { accountLabel } = await import("../extension/lib/format.js");
const { NAME_A, NAME_B, NAME_C, NOW, ORG_A, ORG_B, ORG_C } =
  await import("./fixtures.mjs");

const H = 60 * 60 * 1000;

/** A minimal stored-shaped record. Written out rather than normalized, so the
 * fields the verdict reads are visible in the test. */
function rec(over) {
  return Object.assign({
    orgUuid: ORG_A,
    orgName: NAME_A,
    session: { utilization: 37, resetsAt: new Date(NOW + 3 * H).toISOString(), lockedReason: null },
    weekly: { utilization: 23, resetsAt: null, lockedReason: null },
    asOf: NOW - 2 * H,
    staleSince: null,
  }, over || {});
}

const at = (ms) => new Date(ms).toISOString();

// --- the verdict ------------------------------------------------------------- //

test("a reset still in the future is 'measured' -- the stored percentage stands", () => {
  const v = A.availability(rec({ session: { utilization: 62, resetsAt: at(NOW + 90 * 60 * 1000) } }), NOW);
  assert.equal(v.state, A.MEASURED);
  assert.equal(v.msUntilReset, 90 * 60 * 1000);
  assert.equal(v.resetElapsedMs, null, "a pending reset has not elapsed");
  assert.equal(v.sessionPct, 62);
});

test("🔴 a reset that has ELAPSED since the snapshot is 'free'", () => {
  // The whole point. The account was at 91% when last seen; its window closed
  // two hours ago; it is the one to switch to, and the old UI said "resets
  // soon" at 91% forever.
  const v = A.availability(rec({
    session: { utilization: 91, resetsAt: at(NOW - 2 * H) },
    asOf: NOW - 6 * H,
  }), NOW);
  assert.equal(v.state, A.FREE);
  assert.equal(v.resetElapsedMs, 2 * H, "how long ago it freed up");
  assert.equal(v.msUntilReset, null);
  assert.equal(v.sessionPct, 91, "the last MEASURED value is carried, not zeroed");
  assert.equal(v.asOf, NOW - 6 * H);
  assert.equal(v.ageMs, 6 * H, "...and so is its age");
});

test("🔴 BOUNDARY: resetsAt === now is FREE, one ms earlier is free, one later is measured", () => {
  // Pinned deliberately and documented in the module: the stored value is the
  // instant the window ENDS, so at that instant it has ended.
  assert.equal(A.availability(rec({ session: { utilization: 62, resetsAt: at(NOW) } }), NOW).state,
    A.FREE, "exactly at the reset instant");
  assert.equal(A.availability(rec({ session: { utilization: 62, resetsAt: at(NOW - 1) } }), NOW).state,
    A.FREE);
  assert.equal(A.availability(rec({ session: { utilization: 62, resetsAt: at(NOW + 1) } }), NOW).state,
    A.MEASURED);
  // resetElapsedMs is 0 at the boundary -- a real number, not a falsy hole.
  assert.equal(A.availability(rec({ session: { resetsAt: at(NOW) } }), NOW).resetElapsedMs, 0);
});

test("no parseable reset time is 'unknown', never free and never measured", () => {
  for (const bad of [null, undefined, "", "not-a-date", {}, [], true]) {
    const v = A.availability(rec({ session: { utilization: 62, resetsAt: bad } }), NOW);
    assert.equal(v.state, A.UNKNOWN, `resetsAt=${JSON.stringify(bad)}`);
    assert.equal(v.resetsAt, null);
  }
  // ...but the last measured percentage is still carried, because an unknown
  // reset time does not make the percentage unknown.
  assert.equal(A.availability(rec({ session: { utilization: 62, resetsAt: null } }), NOW).sessionPct, 62);
});

test("a record with no session window at all is 'unknown'", () => {
  for (const s of [null, undefined, "nonsense", 5]) {
    assert.equal(A.availability(rec({ session: s }), NOW).state, A.UNKNOWN);
  }
  assert.equal(A.availability({ orgUuid: ORG_A }, NOW).state, A.UNKNOWN);
});

test("availability is TOTAL over garbage records -- it runs in his real tab", () => {
  for (const bad of [null, undefined, "nonsense", 42, true, NaN]) {
    const v = A.availability(bad, NOW);
    assert.equal(v.state, A.UNKNOWN, `record=${String(bad)}`);
    assert.equal(v.sessionPct, null);
    assert.equal(v.resetsAt, null);
    assert.equal(v.ageMs, null);
  }
  // An array is object-shaped and simply has no fields to read.
  assert.equal(A.availability([], NOW).state, A.UNKNOWN);
});

test("a CALLABLE carrying a session is not read as a measurement", () => {
  // chrome.storage.local can never hand back a function -- it round-trips
  // JSON. This pins the type half of the record guard, which is otherwise
  // unobservable: without it, the property reads below succeed and a
  // non-record is reported as a live 62% measurement.
  const callable = function () {};
  callable.session = { utilization: 62, resetsAt: at(NOW + 3 * H) };
  callable.asOf = NOW;
  const v = A.availability(callable, NOW);
  assert.equal(v.state, A.UNKNOWN);
  assert.equal(v.sessionPct, null, "a function's fields are not a measurement");
});

test("an unusable `now` is 'unknown', not a comparison against NaN", () => {
  for (const bad of [undefined, null, NaN, "2026-09-19", Infinity]) {
    const v = A.availability(rec(), bad);
    assert.equal(v.state, A.UNKNOWN, `now=${String(bad)}`);
    assert.equal(v.ageMs, null);
    // The record's own fields are still reported -- only the time-relative
    // ones are withheld.
    assert.equal(v.sessionPct, 37);
    assert.equal(v.asOf, NOW - 2 * H);
  }
});

test("a record whose snapshot 401'd (staleSince set) still gets its verdict", () => {
  // staleSince is a STYLING input, not an availability one: an auth failure
  // does not un-elapse a reset, and the row still has to say so.
  const v = A.availability(rec({
    session: { utilization: 91, resetsAt: at(NOW - 45 * 60 * 1000) },
    staleSince: NOW - 1000,
  }), NOW);
  assert.equal(v.state, A.FREE);
  assert.equal(v.resetElapsedMs, 45 * 60 * 1000);
});

test("a missing asOf yields a null age rather than a nonsense one", () => {
  const v = A.availability(rec({ asOf: undefined }), NOW);
  assert.equal(v.asOf, null);
  assert.equal(v.ageMs, null);
  assert.equal(v.state, A.MEASURED, "the verdict does not depend on the snapshot time");
});

test("every verdict carries the full field set, whatever the state", () => {
  // The widget reads these without guarding.
  const keys = ["state", "resetsAt", "resetElapsedMs", "msUntilReset", "sessionPct", "asOf", "ageMs"];
  const cases = [
    A.availability(rec(), NOW),
    A.availability(rec({ session: { utilization: 91, resetsAt: at(NOW - H) } }), NOW),
    A.availability(rec({ session: { resetsAt: null } }), NOW),
    A.availability(null, NOW),
  ];
  for (const v of cases) {
    for (const k of keys) assert.ok(k in v, `${v.state} verdict is missing ${k}`);
  }
});

// --- the ordering ------------------------------------------------------------ //

const free = (uuid, name, pct, elapsedH, ageH) => ({
  orgUuid: uuid, orgName: name, staleSince: null,
  asOf: NOW - ageH * H,
  session: { utilization: pct, resetsAt: at(NOW - elapsedH * H) },
});
const pending = (uuid, name, pct, inH) => ({
  orgUuid: uuid, orgName: name, staleSince: null, asOf: NOW - H,
  session: { utilization: pct, resetsAt: at(NOW + inH * H) },
});
const murky = (uuid, name) => ({
  orgUuid: uuid, orgName: name, staleSince: null, asOf: NOW - H,
  session: { utilization: null, resetsAt: null },
});

test("🔴 orderForSwitch is most-available-first: free, then measured, then unknown", () => {
  const accounts = {
    [ORG_A]: murky(ORG_A, NAME_A),
    [ORG_B]: pending(ORG_B, NAME_B, 62, 2),
    [ORG_C]: free(ORG_C, NAME_C, 91, 2, 6),
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, null, NOW).map((r) => r.orgUuid),
    [ORG_C, ORG_B, ORG_A]);
});

test("🔴 the ACTIVE account is pinned at the top and takes no part in the sort", () => {
  // It is the account he is looking at. A row that moves under him as a
  // countdown crosses a threshold is worse than a row in a boring place.
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, 91, 4),      // active, and the worst candidate
    [ORG_B]: pending(ORG_B, NAME_B, 62, 2),
    [ORG_C]: free(ORG_C, NAME_C, 37, 2, 6),
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, ORG_A, NOW).map((r) => r.orgUuid),
    [ORG_A, ORG_C, ORG_B], "active first, then most-available");
  // And with a different account active, the pin moves with it.
  assert.equal(A.orderForSwitch(accounts, ORG_B, NOW)[0].orgUuid, ORG_B);
});

test("within 'measured': lowest session percentage first, then soonest reset", () => {
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, 91, 1),
    [ORG_B]: pending(ORG_B, NAME_B, 23, 4),
    [ORG_C]: pending(ORG_C, NAME_C, 62, 2),
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, null, NOW).map((r) => r.orgUuid),
    [ORG_B, ORG_C, ORG_A], "23 < 62 < 91 regardless of who resets soonest");

  // Equal percentages -> the one that frees up soonest.
  const tied = {
    [ORG_A]: pending(ORG_A, NAME_A, 62, 5),
    [ORG_B]: pending(ORG_B, NAME_B, 62, 1),
  };
  assert.deepEqual(A.orderForSwitch(tied, null, NOW).map((r) => r.orgUuid), [ORG_B, ORG_A]);
});

test("a measured account with no usable percentage sorts LAST among the measured", () => {
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, null, 1),
    [ORG_B]: pending(ORG_B, NAME_B, 91, 4),
  };
  assert.deepEqual(A.orderForSwitch(accounts, null, NOW).map((r) => r.orgUuid),
    [ORG_B, ORG_A], "'?' is a worse switch target than a known 91%");
});

test("within 'free': the one that has been free LONGEST comes first", () => {
  const accounts = {
    [ORG_A]: free(ORG_A, NAME_A, 37, 1, 3),
    [ORG_B]: free(ORG_B, NAME_B, 91, 5, 7),
  };
  assert.deepEqual(A.orderForSwitch(accounts, null, NOW).map((r) => r.orgUuid), [ORG_B, ORG_A]);
});

test("🔴 the order is TOTAL and DETERMINISTIC when every visible field ties", () => {
  // Two records that are indistinguishable to every rule above still have
  // exactly one legal order, and the same one every call -- a list that
  // reshuffles itself on a 30s re-render is unusable.
  const same = (uuid) => pending(uuid, `acct ${uuid.slice(0, 2)}`, 62, 2);
  const accounts = { [ORG_A]: same(ORG_A), [ORG_B]: same(ORG_B), [ORG_C]: same(ORG_C) };
  const first = A.orderForSwitch(accounts, null, NOW).map((r) => r.orgUuid);
  assert.deepEqual(first, [ORG_A, ORG_B, ORG_C], "map order breaks the tie");
  for (let i = 0; i < 5; i += 1) {
    assert.deepEqual(A.orderForSwitch(accounts, null, NOW).map((r) => r.orgUuid), first,
      "the order changed between identical calls");
  }
});

test("orderForSwitch is total over garbage and empty input", () => {
  assert.deepEqual(A.orderForSwitch(null, ORG_A, NOW), []);
  assert.deepEqual(A.orderForSwitch("nonsense", null, NOW), []);
  assert.deepEqual(A.orderForSwitch({}, ORG_A, NOW), []);
  assert.deepEqual(A.orderForSwitch(undefined, undefined, undefined), []);
  // Null records inside the map are dropped, not rendered as empty rows.
  const mixed = { [ORG_A]: null, [ORG_B]: pending(ORG_B, NAME_B, 62, 2), [ORG_C]: "nope" };
  assert.deepEqual(A.orderForSwitch(mixed, null, NOW).map((r) => r.orgUuid), [ORG_B]);
  // An active org with no stored record simply pins nothing.
  assert.deepEqual(A.orderForSwitch(mixed, "no-such-org", NOW).map((r) => r.orgUuid), [ORG_B]);
});

test("orderForSwitch with an unusable `now` still returns every record", () => {
  // Everything reads as unknown, so nothing is ranked -- but no account may
  // vanish from the card because the clock misbehaved.
  const accounts = { [ORG_A]: free(ORG_A, NAME_A, 37, 1, 3), [ORG_B]: pending(ORG_B, NAME_B, 62, 2) };
  assert.deepEqual(A.orderForSwitch(accounts, null, NaN).map((r) => r.orgUuid), [ORG_A, ORG_B]);
});

// --- next free ---------------------------------------------------------------- //

test("nextFreeAt is the SOONEST future reset among the non-active accounts", () => {
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, 37, 1),      // active -- excluded
    [ORG_B]: pending(ORG_B, NAME_B, 62, 4),
    [ORG_C]: pending(ORG_C, NAME_C, 91, 2),
  };
  const n = A.nextFreeAt(accounts, ORG_A, NOW);
  assert.equal(n.record.orgUuid, ORG_C, "the active account's own reset is not the answer");
  assert.equal(n.at, Date.parse(at(NOW + 2 * H)));
  assert.equal(n.inMs, 2 * H);
});

test("nextFreeAt ignores accounts that are already free or unknown", () => {
  const accounts = {
    [ORG_A]: free(ORG_A, NAME_A, 91, 2, 6),
    [ORG_B]: murky(ORG_B, NAME_B),
    [ORG_C]: pending(ORG_C, NAME_C, 62, 3),
  };
  assert.equal(A.nextFreeAt(accounts, null, NOW).record.orgUuid, ORG_C);
});

test("nextFreeAt is null when nothing is pending", () => {
  assert.equal(A.nextFreeAt({}, null, NOW), null);
  assert.equal(A.nextFreeAt(null, null, NOW), null);
  assert.equal(A.nextFreeAt("nonsense", ORG_A, NOW), null);
  assert.equal(A.nextFreeAt({ [ORG_A]: free(ORG_A, NAME_A, 91, 2, 6) }, null, NOW), null,
    "everyone already free -> nothing to wait for");
  assert.equal(A.nextFreeAt({ [ORG_A]: pending(ORG_A, NAME_A, 62, 2) }, ORG_A, NOW), null,
    "the only pending account is the active one");
  assert.equal(A.nextFreeAt({ [ORG_A]: pending(ORG_A, NAME_A, 62, 2) }, null, NaN), null,
    "an unusable clock answers nothing rather than guessing");
});

// --- labels -------------------------------------------------------------------- //

test("accountLabel prefers the operator's override", () => {
  const r = { orgUuid: ORG_A, orgName: NAME_A };
  assert.equal(accountLabel(r, { [ORG_A]: "personal" }), "personal");
  assert.equal(accountLabel(r, { [ORG_B]: "work" }), NAME_A, "another account's label is not mine");
});

test("a blank or whitespace override falls back -- clearing the box is a REMOVE", () => {
  const r = { orgUuid: ORG_A, orgName: NAME_A };
  assert.equal(accountLabel(r, { [ORG_A]: "" }), NAME_A);
  assert.equal(accountLabel(r, { [ORG_A]: "   " }), NAME_A);
  assert.equal(accountLabel(r, { [ORG_A]: "\t\n " }), NAME_A);
  assert.equal(accountLabel(r, { [ORG_A]: "  work  " }), "work", "...but a real label is trimmed");
});

test("accountLabel falls all the way through to a placeholder, never to blank", () => {
  assert.equal(accountLabel({ orgUuid: ORG_A, orgName: "" }, null), "unknown account");
  assert.equal(accountLabel({ orgUuid: ORG_A, orgName: "   " }, {}), "unknown account");
  assert.equal(accountLabel({ orgUuid: ORG_A }, {}), "unknown account");
  assert.equal(accountLabel(null, { [ORG_A]: "personal" }), "unknown account");
  assert.equal(accountLabel(undefined, undefined), "unknown account");
  assert.equal(accountLabel("nonsense", "nonsense"), "unknown account");
});

test("accountLabel is not fooled by an Object.prototype key", () => {
  // The same hole lib/severity.js's SEVERITY_TONES lookup documents: a bare
  // read hands back something truthy off the prototype.
  assert.equal(accountLabel({ orgUuid: "constructor", orgName: NAME_A }, {}), NAME_A);
  assert.equal(accountLabel({ orgUuid: "toString", orgName: NAME_A }, {}), NAME_A);
  assert.equal(accountLabel({ orgUuid: "hasOwnProperty", orgName: "" }, {}), "unknown account");
});
