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
const { isStale } = await import("../extension/lib/timefmt.js");
const { accountLabel } = await import("../extension/lib/format.js");
const { NAME_A, NAME_B, NAME_C, NOW, ORG_A, ORG_B, ORG_C } =
  await import("./fixtures.mjs");

const H = 60 * 60 * 1000;
const DAY = 24 * H;

/** A minimal stored-shaped record. Written out rather than normalized, so the
 * fields the verdict reads are visible in the test.
 *
 * ⚠ THE WEEKLY WINDOW IS PART OF THE DEFAULT, and it did not used to be
 * enough: `weekly` carried a percentage but no reset time, which is the one
 * shape in which the weekly window can never be shown to have expired. Every
 * weekly value below is distinct from the session one beside it and from
 * every constant the assertions name (WARN_PCT 80, CRIT_PCT 95,
 * WEEKLY_EXHAUSTED_PCT 100, 0), so a mutant hardcoding a literal cannot
 * survive by landing on a fixture's own number. */
function rec(over) {
  return Object.assign({
    orgUuid: ORG_A,
    orgName: NAME_A,
    severity: null,
    session: { utilization: 37, resetsAt: new Date(NOW + 3 * H).toISOString(), lockedReason: null },
    weekly: { utilization: 26, resetsAt: new Date(NOW + 4 * DAY).toISOString(), lockedReason: null },
    asOf: NOW - 2 * H,
    staleSince: null,
  }, over || {});
}

const at = (ms) => new Date(ms).toISOString();

/** The weekly window, spelled out. Defaults are a healthy window four days
 * out, so a test naming only what it cares about gets a non-blocking rest. */
const wk = (over) => Object.assign(
  { utilization: 26, resetsAt: at(NOW + 4 * DAY), lockedReason: null }, over || {});

// --- the verdict ------------------------------------------------------------- //

test("a reset still in the future is 'measured' -- the stored percentage stands", () => {
  const v = A.availability(rec({ session: { utilization: 62, resetsAt: at(NOW + 90 * 60 * 1000) } }), NOW);
  assert.equal(v.state, A.MEASURED);
  assert.equal(v.resetsAt, NOW + 90 * 60 * 1000);
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
  assert.equal(v.sessionPct, 91, "the last MEASURED value is carried, not zeroed");
  assert.equal(v.asOf, NOW - 6 * H, "...and the snapshot time the caller ages it from");
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
  // 🔴 THIS TEST CARRIES BOTH HALVES OF `isRecord`, MEASURED one mutant at a
  // time with the runner filtered to this test and nothing else: `null` kills
  // `v !== null`, and `undefined` kills `typeof v === "object"`. Both die the
  // same way -- a TypeError out of availability.js, which is the 🔴 NEVER
  // THROW invariant, not a wrong verdict.
  //
  // ⚠ `"nonsense"`, `42`, `true` and `NaN` kill NEITHER half: a `.session`
  // read off them answers `undefined` without throwing, so they reach UNKNOWN
  // regardless. They are breadth. `undefined` is load-bearing and must not be
  // dropped from this list.
  //
  // A separate `a CALLABLE carrying a session ...` test used to claim the
  // typeof half; it killed no mutant this one does not, and
  // chrome.storage.local round-trips JSON so it pinned a shape that cannot
  // occur. It was deleted and this was re-measured afterwards.
  //
  // ⚠ THE FILTER IS PART OF THE INSTRUMENT. `node --test
  // --test-name-pattern` reports `tests 1 / pass 1` when the pattern matches
  // NOTHING, which is byte-indistinguishable from a real pass -- an anchored
  // pattern on a truncated copy of this test's name read "mutant SURVIVED"
  // before a non-matching control exposed it. Re-run that control first.
  for (const bad of [null, undefined, "nonsense", 42, true, NaN]) {
    const v = A.availability(bad, NOW);
    assert.equal(v.state, A.UNKNOWN, `record=${String(bad)}`);
    assert.equal(v.sessionPct, null);
    assert.equal(v.resetsAt, null);
    assert.equal(v.asOf, null);
  }
  // An array is object-shaped and simply has no fields to read.
  assert.equal(A.availability([], NOW).state, A.UNKNOWN);
});

test("an unusable `now` is 'unknown', not a comparison against NaN", () => {
  for (const bad of [undefined, null, NaN, "2026-09-19", Infinity]) {
    const v = A.availability(rec(), bad);
    assert.equal(v.state, A.UNKNOWN, `now=${String(bad)}`);
    assert.equal(v.resetsAt, null);
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

test("a missing asOf is null rather than a nonsense timestamp", () => {
  const v = A.availability(rec({ asOf: undefined }), NOW);
  assert.equal(v.asOf, null);
  assert.equal(v.state, A.MEASURED, "the verdict does not depend on the snapshot time");
});

test("every verdict carries the full field set, whatever the state", () => {
  // The widget and the popup read these without guarding. `msUntilReset` and
  // `ageMs` were in this list for a round with no reader anywhere outside
  // this module -- both callers recompute them from the raw record through
  // formatCountdown()/stalenessLabel() -- and were deleted rather than wired
  // up. Every name below has a named consumer in availability.js's header.
  const keys = ["state", "resetsAt", "resetElapsedMs", "sessionPct", "weeklyPct",
    "weeklyBindingPct", "asOf", "lockedReason", "freesAt"];
  const cases = [
    A.availability(rec(), NOW),
    A.availability(rec({ session: { utilization: 91, resetsAt: at(NOW - H) } }), NOW),
    A.availability(rec({ session: { resetsAt: null } }), NOW),
    A.availability(rec({ weekly: wk({ lockedReason: "Weekly limit reached." }) }), NOW),
    A.availability(null, NOW),
  ];
  const seen = new Set(cases.map((v) => v.state));
  assert.deepEqual([...seen].sort(), ["blocked", "free", "measured", "unknown"],
    "precondition: every state is exercised, or the field set is pinned for only some");
  for (const v of cases) {
    for (const k of keys) assert.ok(k in v, `${v.state} verdict is missing ${k}`);
    // ...and NOTHING ELSE. Pinned both ways deliberately: the one-way version
    // of this test is what let two consumer-less fields live in the verdict.
    assert.deepEqual(Object.keys(v).sort(), [...keys].sort(),
      `the ${v.state} verdict carries a field with no consumer`);
  }
});

test("INVARIANT GUARD: a FREE verdict always carries a NUMERIC resetElapsedMs", () => {
  // ⚠ LABELLED. This passed at b97190c8 too -- it is not regression coverage,
  // it is what makes a DELETION safe. `orderForSwitch`'s longest-free-first
  // comparison used to read `va.resetElapsedMs === null ? 0 : ...` on both
  // sides, and that ternary was dead: the FREE branch is the only writer of
  // the field and it always writes a number. Removing both ternaries was
  // MEASURED to survive all 217 tests, so nothing pinned the invariant they
  // stood in for. This does; the sort now reads the field bare.
  const cases = [
    rec({ session: { utilization: 91, resetsAt: at(NOW - 2 * H) } }),
    rec({ session: { utilization: 62, resetsAt: at(NOW) } }),              // boundary
    rec({ session: { utilization: null, resetsAt: at(NOW - 5 * DAY) } }),  // no percentage
    rec({ session: { utilization: 91, resetsAt: NOW - H }, asOf: undefined }),
  ];
  for (const r of cases) {
    const v = A.availability(r, NOW);
    assert.equal(v.state, A.FREE, "precondition: this fixture is free");
    assert.equal(typeof v.resetElapsedMs, "number",
      "a FREE verdict with a null resetElapsedMs would make the free sort compare against null");
    assert.ok(Number.isFinite(v.resetElapsedMs));
  }
});

// --- 🔴 F1: the verdict is not a SESSION-window verdict ---------------------- //
//
// 🔴 THE DEFECT. `availability()` read `session.resetsAt` and nothing else, so
// the one state a heavy operator lives in for days -- five-hour window reset,
// SEVEN-DAY window spent -- was reported as the best account to switch to.
// MEASURED at b97190c8: session 95% with the reset 3h elapsed, weekly 100%
// and `weekly.lockedReason: "Weekly limit reached."` resetting in 4 days
// rendered `AVAILABLE — reset 3h ago (was 95%, measured 8h ago)`, tone "ok",
// not stale, sorted FIRST. Adding `session.lockedReason: "account_suspended"`
// and a `staleSince` produced a BYTE-IDENTICAL row.

test("🔴 REGRESSION: a spent WEEKLY window blocks, however long ago the session reset", () => {
  // Watched RED at b97190c8: state was "free" for all three.
  const freed = { utilization: 95, resetsAt: at(NOW - 3 * H) };
  const cases = [
    ["a weekly LOCK", wk({ utilization: 62, lockedReason: "Weekly limit reached." })],
    ["an EXHAUSTED weekly window with no lock string", wk({ utilization: 100 })],
    ["both at once", wk({ utilization: 100, lockedReason: "Weekly limit reached." })],
  ];
  for (const [why, weekly] of cases) {
    const v = A.availability(rec({ session: freed, weekly, asOf: NOW - 8 * H }), NOW);
    assert.equal(v.state, A.BLOCKED, `${why} still read as ${v.state}`);
    // The evidence stays visible -- the verdict never replaces the readings.
    assert.equal(v.sessionPct, 95, "the last MEASURED session value is carried");
    assert.equal(v.weeklyPct, weekly.utilization, "...and the weekly one");
    assert.equal(v.freesAt, Date.parse(at(NOW + 4 * DAY)),
      "the operator has to be told WHEN it frees up, and that is the weekly reset");
    assert.equal(v.lockedReason, weekly.lockedReason,
      "the API's own words, and never an invented sentence for the unlocked case");
  }
});

test("🔴 REGRESSION: a SESSION lock blocks while its own window is open", () => {
  // Watched RED at b97190c8: "measured", rendered as a plain 62% row.
  const v = A.availability(rec({
    session: { utilization: 62, resetsAt: at(NOW + 90 * 60 * 1000),
      lockedReason: "Session limit reached." },
  }), NOW);
  assert.equal(v.state, A.BLOCKED);
  assert.equal(v.lockedReason, "Session limit reached.");
  assert.equal(v.freesAt, NOW + 90 * 60 * 1000, "a session lock ends at the session reset");
});

test("🔴 a SESSION lock is SPENT by its own reset -- it does not outlive the window", () => {
  // The mirror of the rule above, and the one that would have re-created this
  // module's founding defect one field over: a lock recorded during a window
  // that has since closed is stale evidence, exactly as the percentage is.
  const v = A.availability(rec({
    session: { utilization: 95, resetsAt: at(NOW - 2 * H), lockedReason: "Session limit reached." },
  }), NOW);
  assert.equal(v.state, A.FREE, "a five-hour lock survived its five-hour window");
  assert.equal(v.lockedReason, null);
});

test("🔴 a WEEKLY lock is spent by the WEEKLY reset, not by the session one", () => {
  // Both windows elapsed -> nothing binds.
  const v = A.availability(rec({
    session: { utilization: 95, resetsAt: at(NOW - 2 * H) },
    weekly: wk({ utilization: 100, resetsAt: at(NOW - 60 * 1000),
      lockedReason: "Weekly limit reached." }),
  }), NOW);
  assert.equal(v.state, A.FREE, "an expired weekly block still blocked");

  // ...and one minute the other way it still binds, so the assertion above
  // cannot be satisfied by ignoring the weekly window altogether.
  const still = A.availability(rec({
    session: { utilization: 95, resetsAt: at(NOW - 2 * H) },
    weekly: wk({ utilization: 100, resetsAt: at(NOW + 60 * 1000),
      lockedReason: "Weekly limit reached." }),
  }), NOW);
  assert.equal(still.state, A.BLOCKED);
  assert.equal(still.freesAt, NOW + 60 * 1000);
});

test("a lock whose reset cannot be parsed blocks with an UNKNOWN end, never optimistically", () => {
  // The inference is safe in one direction only: a reset we cannot read has
  // not been shown to have happened.
  const v = A.availability(rec({
    session: { utilization: 95, resetsAt: at(NOW - 2 * H) },
    weekly: wk({ utilization: 62, resetsAt: null, lockedReason: "Weekly limit reached." }),
  }), NOW);
  assert.equal(v.state, A.BLOCKED);
  assert.equal(v.freesAt, null, "a block with no knowable end must not name a time");

  // And when TWO windows block with different ends, the later one decides --
  // the account is usable only once every blocker has cleared.
  const both = A.availability(rec({
    session: { utilization: 95, resetsAt: at(NOW + 2 * H), lockedReason: "Session limit reached." },
    weekly: wk({ utilization: 100, resetsAt: at(NOW + 3 * DAY) }),
  }), NOW);
  assert.equal(both.freesAt, Date.parse(at(NOW + 3 * DAY)), "the SOONER reset was taken");
  assert.equal(both.lockedReason, "Session limit reached.",
    "session first -- it is the one named on screen when both apply");
});

test("the exhaustion boundary is WEEKLY_EXHAUSTED_PCT, inclusive, and below it nothing blocks", () => {
  const atPct = (p) => A.availability(rec({
    session: { utilization: 37, resetsAt: at(NOW - H) },
    weekly: wk({ utilization: p }),
  }), NOW).state;
  assert.equal(A.WEEKLY_EXHAUSTED_PCT, 100);
  assert.equal(atPct(99.4), A.FREE, "99.4% of your weekly allowance is not out");
  assert.equal(atPct(A.WEEKLY_EXHAUSTED_PCT), A.BLOCKED, "inclusive at the endpoint");
  assert.equal(atPct(140), A.BLOCKED, "an over-100 reading is not a reason to unblock");
  assert.equal(atPct(null), A.FREE, "an unknown weekly reading is not an exhausted one");
  // CRIT_PCT is a colour band and must NOT be a state boundary: a 95% weekly
  // window is alarming and still usable.
  assert.equal(atPct(95), A.FREE, "the crit BAND became a blocking threshold");
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
/** Session window long since reset, WEEKLY window spent -- the state the
 * verdict used to call the best switch target on the card. */
const blocked = (uuid, name, pct, weeklyInDays) => ({
  orgUuid: uuid, orgName: name, staleSince: null, asOf: NOW - 8 * H,
  session: { utilization: pct, resetsAt: at(NOW - 3 * H) },
  weekly: { utilization: 100, resetsAt: at(NOW + weeklyInDays * DAY),
    lockedReason: "Weekly limit reached." },
});

test("🔴 orderForSwitch is most-available-first: free, then measured, then unknown", () => {
  const accounts = {
    [ORG_A]: murky(ORG_A, NAME_A),
    [ORG_B]: pending(ORG_B, NAME_B, 62, 2),
    [ORG_C]: free(ORG_C, NAME_C, 91, 2, 6),
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid),
    [ORG_C, ORG_B, ORG_A]);
});

test("orderForSwitch does NOT know about the active account -- its caller excludes it", () => {
  // It used to take `lastActiveOrg` and pin that record at index 0, exempt
  // from the sort. That contract was UNOBSERVABLE: lib/widget.js's
  // buildOthers filters the active record out of the result on the next
  // line, because the card already shows it above. Removing the pin changes
  // no output -- dropping one element from a sorted list leaves the rest in
  // the same relative order -- which is what this pins: the active record
  // sorts on its merits like any other, and the order of the REST is
  // identical to the order the caller ends up painting.
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, 91, 4),      // would have been pinned first
    [ORG_B]: pending(ORG_B, NAME_B, 62, 2),
    [ORG_C]: free(ORG_C, NAME_C, 37, 2, 6),
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid),
    [ORG_C, ORG_B, ORG_A], "the 91% record sorts last on merit, not first on identity");
  assert.equal(A.orderForSwitch.length, 2,
    "orderForSwitch grew an argument back -- a stale 3-arg call would pass an "
    + "org id where `now` belongs and silently rank everything UNKNOWN");
});

test("within 'measured': lowest session percentage first, then soonest reset", () => {
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, 91, 1),
    [ORG_B]: pending(ORG_B, NAME_B, 23, 4),
    [ORG_C]: pending(ORG_C, NAME_C, 62, 2),
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid),
    [ORG_B, ORG_C, ORG_A], "23 < 62 < 91 regardless of who resets soonest");

  // Equal percentages -> the one that frees up soonest.
  const tied = {
    [ORG_A]: pending(ORG_A, NAME_A, 62, 5),
    [ORG_B]: pending(ORG_B, NAME_B, 62, 1),
  };
  assert.deepEqual(A.orderForSwitch(tied, NOW).map((r) => r.orgUuid), [ORG_B, ORG_A]);
});

test("a measured account with no usable percentage sorts LAST among the measured", () => {
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, null, 1),
    [ORG_B]: pending(ORG_B, NAME_B, 91, 4),
  };
  assert.deepEqual(A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid),
    [ORG_B, ORG_A], "'?' is a worse switch target than a known 91%");
});

test("within 'free': the one that has been free LONGEST comes first", () => {
  const accounts = {
    [ORG_A]: free(ORG_A, NAME_A, 37, 1, 3),
    [ORG_B]: free(ORG_B, NAME_B, 91, 5, 7),
  };
  assert.deepEqual(A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid), [ORG_B, ORG_A]);
});

test("🔴 REGRESSION: a weekly-BLOCKED account never outranks a usable one", () => {
  // Watched RED at b97190c8: the blocked account read "free" and therefore
  // sorted FIRST -- the single best switch target on the card was the one
  // that rejects you at the login screen.
  const accounts = {
    [ORG_A]: blocked(ORG_A, NAME_A, 95, 4),
    [ORG_B]: pending(ORG_B, NAME_B, 91, 4),      // usable, and nearly out
    [ORG_C]: murky(ORG_C, NAME_C),               // usable for all we know
  };
  assert.deepEqual(
    A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid),
    [ORG_B, ORG_C, ORG_A],
    "blocked must rank below even an UNKNOWN account: unknown might be usable, "
    + "blocked is known not to be");
  assert.deepEqual(A.STATE_ORDER, ["free", "measured", "unknown", "blocked"]);
});

test("within 'blocked': the one that frees up SOONEST comes first, unknown ends last", () => {
  const accounts = {
    [ORG_A]: blocked(ORG_A, NAME_A, 91, 5),
    [ORG_B]: blocked(ORG_B, NAME_B, 62, 1),
    [ORG_C]: Object.assign(blocked(ORG_C, NAME_C, 37, 2),
      { weekly: { utilization: 100, resetsAt: null, lockedReason: "Weekly limit reached." } }),
  };
  assert.deepEqual(A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid),
    [ORG_B, ORG_A, ORG_C],
    "'blocked until we cannot say' is the worst thing to be waiting on");
});

test("INVARIANT GUARD: the order is TOTAL and DETERMINISTIC when every visible field ties", () => {
  // ⚠ LABELLED, AND THE MECHANISM IS NOT WHAT THIS FILE USED TO CLAIM. The
  // comparator ended in `a.index - b.index`, documented as what made the
  // order total, and this test was cited as the pin for it. It is not, and
  // cannot be: `Array.prototype.sort` has been stable by spec since ES2019,
  // so `return 0` produces the identical order and was MEASURED to survive
  // all 217 tests. The tiebreak and the `index` field it read have therefore
  // been DELETED rather than re-justified.
  //
  // What remains is worth keeping and is exactly this: the OUTPUT property,
  // guarded against a comparator that is ever made non-deterministic. It is
  // an invariant guard on the engine's guarantee, not regression coverage for
  // any defect, and it passed at b97190c8 unchanged.
  const same = (uuid) => pending(uuid, `acct ${uuid.slice(0, 2)}`, 62, 2);
  const accounts = { [ORG_A]: same(ORG_A), [ORG_B]: same(ORG_B), [ORG_C]: same(ORG_C) };
  const first = A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid);
  assert.deepEqual(first, [ORG_A, ORG_B, ORG_C], "map order breaks the tie");
  for (let i = 0; i < 5; i += 1) {
    assert.deepEqual(A.orderForSwitch(accounts, NOW).map((r) => r.orgUuid), first,
      "the order changed between identical calls");
  }
});

test("orderForSwitch is total over garbage and empty input", () => {
  assert.deepEqual(A.orderForSwitch(null, NOW), []);
  assert.deepEqual(A.orderForSwitch("nonsense", NOW), []);
  assert.deepEqual(A.orderForSwitch({}, NOW), []);
  assert.deepEqual(A.orderForSwitch(undefined, undefined), []);
  // Null records inside the map are dropped, not rendered as empty rows.
  const mixed = { [ORG_A]: null, [ORG_B]: pending(ORG_B, NAME_B, 62, 2), [ORG_C]: "nope" };
  assert.deepEqual(A.orderForSwitch(mixed, NOW).map((r) => r.orgUuid), [ORG_B]);
});

test("orderForSwitch with an unusable `now` still returns every record", () => {
  // Everything reads as unknown, so nothing is ranked -- but no account may
  // vanish from the card because the clock misbehaved.
  const accounts = { [ORG_A]: free(ORG_A, NAME_A, 37, 1, 3), [ORG_B]: pending(ORG_B, NAME_B, 62, 2) };
  assert.deepEqual(A.orderForSwitch(accounts, NaN).map((r) => r.orgUuid), [ORG_A, ORG_B]);
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

test("INVARIANT GUARD: two accounts freeing at the SAME instant -- the first in map order wins", () => {
  // ⚠ LABELLED: this passed at b97190c8, so it is not regression coverage.
  // It closes a SURVIVED mutant instead. `nextFreeAt`'s comparison is
  // documented as load-bearing ("on an exact tie the first in map order
  // wins, so the answer does not depend on comparison order") and was
  // UNPINNED: relaxing the strict `<` to `<=` -- which hands the tie to the
  // LAST record instead -- was MEASURED to survive all 217 tests. A comment
  // claiming a property nothing checks is the shape this project keeps
  // finding, so the claim now has a guard rather than a better sentence.
  const tie = at(NOW + 2 * H);
  const accounts = {
    [ORG_A]: { orgUuid: ORG_A, orgName: NAME_A, staleSince: null, asOf: NOW - H,
      session: { utilization: 62, resetsAt: tie } },
    [ORG_B]: { orgUuid: ORG_B, orgName: NAME_B, staleSince: null, asOf: NOW - H,
      session: { utilization: 23, resetsAt: tie } },
  };
  assert.equal(A.nextFreeAt(accounts, null, NOW).record.orgUuid, ORG_A,
    "the tie went to the LAST record -- the comparison is no longer strict");
  // Reversed insertion order gives the other answer, so the assertion above
  // cannot be satisfied by always returning ORG_A.
  const flipped = { [ORG_B]: accounts[ORG_B], [ORG_A]: accounts[ORG_A] };
  assert.equal(A.nextFreeAt(flipped, null, NOW).record.orgUuid, ORG_B);
});

test("🔴 nextFreeAt counts a BLOCKED account -- its weekly reset is the wait", () => {
  // Watched RED at b97190c8: every other account blocked meant `state !==
  // MEASURED` for all of them, so the footer was null and the card said
  // nothing at all while the operator had four days to wait.
  const accounts = {
    [ORG_A]: pending(ORG_A, NAME_A, 37, 1),                   // active, excluded
    [ORG_B]: blocked(ORG_B, NAME_B, 95, 4),
  };
  const n = A.nextFreeAt(accounts, ORG_A, NOW);
  assert.ok(n, "no wait time offered for an account that is blocked, not free");
  assert.equal(n.record.orgUuid, ORG_B);
  assert.equal(n.at, Date.parse(at(NOW + 4 * DAY)), "the WEEKLY reset is what unblocks it");
  assert.equal(n.inMs, 4 * DAY);

  // A block with no knowable end offers nothing rather than guessing.
  const opaque = { [ORG_B]: Object.assign(blocked(ORG_B, NAME_B, 95, 4),
    { weekly: { utilization: 100, resetsAt: null, lockedReason: "Weekly limit reached." } }) };
  assert.equal(A.nextFreeAt(opaque, null, NOW), null);
});

// --- 🔴 F2: ONE predicate for "which record is active" ----------------------- //
//
// 🔴 THE SEAM. The widget identified the active account by MAP KEY
// (`accounts[lastActiveOrg]`) and the popup by the record's own `orgUuid`
// FIELD (`rec.orgUuid === lastActiveOrg`). service_worker.js writes
// `lastActiveOrg` whether or not the /usage fetch produced a record, so a
// non-401/403 failure on a first-seen org leaves the key naming nothing --
// and the two spellings then answered differently on one storage read.

test("🔴 REGRESSION: an active key naming NO stored record is not active at all", () => {
  // Watched RED at b97190c8 -- there was no predicate to be red, which is the
  // finding: both surfaces open-coded their own.
  const GHOST = "99999999-9999-4999-8999-999999999999";
  const b = pending(ORG_B, NAME_B, 92, 2);
  const accounts = { [ORG_B]: b };
  assert.equal(A.activeRecord(accounts, GHOST), null,
    "a key with no record must not promote SOME OTHER account to active");
  assert.equal(A.isActiveRecord(b, accounts, GHOST), false);
  // ...and it still names the real one when there is one.
  assert.equal(A.activeRecord(accounts, ORG_B), b);
  assert.equal(A.isActiveRecord(b, accounts, ORG_B), true);
});

test("activeRecord is the MAP KEY, not the record's own orgUuid field", () => {
  // The worker writes `accounts[uuid]` and `lastActiveOrg = uuid` from one
  // value in one writeState, so the key is the claim and the field is a copy
  // an older or partial write can disagree with.
  const stray = pending(ORG_B, NAME_B, 62, 2);
  stray.orgUuid = ORG_C;                              // the copy disagrees
  const accounts = { [ORG_B]: stray };
  assert.equal(A.activeRecord(accounts, ORG_B), stray);
  assert.equal(A.activeRecord(accounts, ORG_C), null,
    "the FIELD spelling would have matched here, and it is the wrong record");
});

test("activeRecord is total over garbage, junk records and prototype keys", () => {
  const b = pending(ORG_B, NAME_B, 62, 2);
  assert.equal(A.activeRecord({ [ORG_A]: "junk", [ORG_B]: b }, ORG_A), null,
    "a junk value under the active key is not a record");
  assert.equal(A.activeRecord({ [ORG_A]: null }, ORG_A), null);
  assert.equal(A.activeRecord(null, ORG_A), null);
  assert.equal(A.activeRecord("nonsense", ORG_A), null);
  assert.equal(A.activeRecord({}, ""), null);
  assert.equal(A.activeRecord({}, 5), null);
  assert.equal(A.activeRecord({}, null), null);
  // ⚠ THESE TWO DO NOT EXERCISE THE `hasOwnProperty` GUARD, and reading them
  // as though they did is why that guard went untested for a round. A bare
  // read of `accounts["constructor"]` answers a truthy FUNCTION, which
  // `isRecord` rejects two lines later -- so DELETING the guard leaves both
  // of these green (MEASURED at 38bbc1f1: the deletion SURVIVED all 251
  // tests). They are breadth over prototype-shaped keys, nothing more.
  assert.equal(A.activeRecord({}, "constructor"), null);
  assert.equal(A.activeRecord({}, "toString"), null);
  // 🔴 THIS is the key the guard decides, and the only one. `{}["__proto__"]`
  // answers Object.prototype -- an ordinary object `isRecord` ACCEPTS -- so
  // without the guard Object.prototype is handed back as the active record
  // and the widget renders the page's prototype as an account. MEASURED both
  // ways at 38bbc1f1: null with the guard, Object.prototype without it.
  // ⚠ NOT REACHABLE IN PRODUCTION -- `lastActiveOrg` is an API UUID written
  // by service_worker.js. Pinned because a guard whose only reachable case is
  // untested reads as coverage while providing none.
  assert.equal(A.activeRecord({}, "__proto__"), null,
    "Object.prototype was returned as the active record");
  assert.equal(A.isActiveRecord(Object.prototype, {}, "__proto__"), false);
  assert.equal(A.isActiveRecord(null, { [ORG_A]: null }, ORG_A), false,
    "null === null must not read as 'this record is the active one'");
});

// --- the row tone ------------------------------------------------------------- //

test("🔴 REGRESSION: a free row is coloured by the WEEKLY window, not by its spent session %", () => {
  // Watched RED at b97190c8: `otherRow` hardcoded `tone: "ok"` on this branch,
  // so a weekly window at 100% painted green. The mirror half matters just as
  // much -- the SESSION percentage is spent evidence and must not redden it.
  const v = (weeklyPct) => A.availability(rec({
    session: { utilization: 95, resetsAt: at(NOW - 3 * H) },
    weekly: wk({ utilization: weeklyPct }),
  }), NOW);
  const tone = (weeklyPct) => A.toneForRow(
    rec({ session: { utilization: 95, resetsAt: at(NOW - 3 * H) },
      weekly: wk({ utilization: weeklyPct }) }), v(weeklyPct), isStale, NOW);
  assert.equal(v(26).state, A.FREE, "precondition");
  assert.equal(tone(26), "ok", "a 95% session window that has RESET is not red");
  assert.equal(tone(84), "warn", "the weekly window is the one that still binds");
  assert.equal(tone(97), "crit");
  assert.equal(tone(null), "ok", "an unknown weekly reading leaves the verdict standing");
});

test("🔴 REGRESSION: the WEEKLY reading is SPENT by the weekly reset -- free rows and measured ones", () => {
  // Watched RED at 38bbc1f1. `toneForRow` read `verdict.weeklyPct`
  // unconditionally, so a weekly window that had ALREADY reset still coloured
  // the row -- the mirror of the defect the previous round fixed, one window
  // over. MEASURED there on {session 50% reset 3h ago, weekly 100% reset 1h
  // ago, asOf 8d}:
  //   {"state":"free","value":"AVAILABLE",
  //    "meta":"reset 3h ago (was 50%, measured 8d ago)","tone":"crit"}
  // AVAILABLE, painted red, with nothing on the row explaining the colour.
  // It needs only the seven-day boundary to have crossed on a non-active
  // account -- i.e. any account not logged into for over a week, which is the
  // population the other-accounts list exists for.
  const tone = (r) => A.toneForRow(r, A.availability(r, NOW), isStale, NOW);
  const old = { asOf: NOW - 8 * DAY };

  const freeSpent = rec(Object.assign({
    session: { utilization: 50, resetsAt: at(NOW - 3 * H) },
    weekly: wk({ utilization: 100, resetsAt: at(NOW - H) }),
  }, old));
  const fv = A.availability(freeSpent, NOW);
  assert.equal(fv.state, A.FREE, "precondition: the weekly block is spent, so nothing binds");
  // 🔴 THE BEHAVIOURAL ASSERTION COMES FIRST, ON PURPOSE. The two field
  // assertions under it name `weeklyBindingPct`, which does not EXIST at
  // 38bbc1f1 -- so had they run first this test would have gone red at base
  // for a missing field rather than for the colour, and its red would have
  // been evidence of nothing.
  assert.equal(tone(freeSpent), "ok", "an AVAILABLE row painted red off a reset window");
  assert.equal(fv.weeklyPct, 100,
    "the RAW reading stays reportable -- it is what a blocked row states");
  assert.equal(fv.weeklyBindingPct, null,
    "...and it no longer binds, which is the field the colour is allowed to read");

  // The mirror, so the assertion above cannot be satisfied by ignoring the
  // weekly window altogether: the same reading, one hour the other way, still
  // binds. 84% rather than 100%, because a LIVE 100% is BLOCKED and would
  // take the crit branch without consulting a percentage at all.
  const binds = rec(Object.assign({
    session: { utilization: 50, resetsAt: at(NOW - 3 * H) },
    weekly: wk({ utilization: 84, resetsAt: at(NOW + H) }),
  }, old));
  const spent = rec(Object.assign({
    session: { utilization: 50, resetsAt: at(NOW - 3 * H) },
    weekly: wk({ utilization: 84, resetsAt: at(NOW - H) }),
  }, old));
  assert.equal(A.availability(binds, NOW).state, A.FREE, "precondition");
  assert.equal(tone(binds), "warn", "a LIVE 84% weekly window still colours a free row");
  assert.equal(tone(spent), "ok", "the SAME 84% two hours earlier must not");

  // ...and the same rule on a MEASURED row, which a fix scoped to `free`
  // would have left one state short. The session window is open so its
  // percentage binds; a weekly reading whose own window turned over does not.
  const mkMeasured = (weeklyResetsAt) => rec({
    session: { utilization: 62, resetsAt: at(NOW + 2 * H) },
    weekly: wk({ utilization: 97, resetsAt: weeklyResetsAt }),
  });
  const mSpent = mkMeasured(at(NOW - H));
  assert.equal(A.availability(mSpent, NOW).state, A.MEASURED, "precondition");
  assert.equal(tone(mSpent), "ok", "a spent 97% weekly reading reddened a measured row");
  assert.equal(tone(mkMeasured(at(NOW + H))), "crit",
    "a LIVE 97% weekly window still reddens a measured row");

  // The unspent direction of the field itself, and the no-clock case: a reset
  // that cannot be TIMED has not been shown to have happened, so the reading
  // still binds. (The spent direction is the first assertion in this test.)
  assert.equal(A.availability(mkMeasured(at(NOW + H)), NOW).weeklyBindingPct, 97);
  assert.equal(A.availability(mkMeasured(null), NOW).weeklyBindingPct, 97,
    "an unparseable weekly reset must not silently un-bind the reading");
  assert.equal(A.availability(mkMeasured(at(NOW - H)), NaN).weeklyBindingPct, 97,
    "with no usable clock nothing has been shown to have reset");
});

test("MUTATION GUARD: an UNPARSEABLE reset leaves its OWN window open -- swept over BOTH windows", () => {
  // ⚠ LABELLED. This PASSES at 38bbc1f1 -- the behaviour was already right
  // and only the weekly half was pinned, so this is not regression coverage.
  // What it closes is a SURVIVED mutant: `session.at === null || session.at >
  // t` mutated to `session.at !== null && session.at > t` survived all 251
  // tests at 38bbc1f1. MEASURED there on {session 62%, resetsAt null,
  // lockedReason "Session limit reached.", weekly healthy}:
  //   HEAD    state=blocked  "Session limit reached."  frees up: unknown
  //   MUTANT  state=unknown  "62% · reset time unknown"
  // -- a live lock dismissed because its end could not be read, which is the
  // one direction this module's inference is NOT safe in.
  //
  // 🔴 SWEPT OVER BOTH WINDOWS RATHER THAN FIXED FOR ONE. The previous two
  // rounds each closed this same defect class a single window at a time, so
  // the rule is walked here instead of instantiated.
  const mk = {
    session: (over) => rec({
      session: Object.assign({ utilization: 62, resetsAt: null }, over),
      weekly: wk(),
    }),
    weekly: (over) => rec({
      session: { utilization: 62, resetsAt: at(NOW + 2 * H) },
      weekly: wk(Object.assign({ resetsAt: null }, over)),
    }),
  };
  for (const [name, build] of Object.entries(mk)) {
    const v = A.availability(build({ lockedReason: "Limit reached." }), NOW);
    assert.equal(v.state, A.BLOCKED,
      `a ${name} lock whose end cannot be read was dismissed as expired`);
    assert.equal(v.lockedReason, "Limit reached.", `${name}: the API's own words`);
    assert.equal(v.freesAt, null,
      `a ${name} block with no knowable end must not name a time`);
  }
  // The EXHAUSTION half exists for the weekly window only, and carries the
  // same rule: an unreadable weekly reset must not un-exhaust a spent
  // allowance.
  const out = A.availability(mk.weekly({ utilization: A.WEEKLY_EXHAUSTED_PCT }), NOW);
  assert.equal(out.state, A.BLOCKED);
  assert.equal(out.lockedReason, null, "no lock string -- the row states the number instead");
  assert.equal(out.freesAt, null);
  // ⚠ NOTHING HERE TOUCHES `weeklyBindingPct`, deliberately. Every assertion
  // above holds at 38bbc1f1 -- CONFIRMED by running this exact test against
  // that tree -- which is what makes the MUTATION GUARD label honest. One
  // assertion on a field that did not exist there would have turned it red
  // at base for a reason that has nothing to do with the rule it pins, and
  // it would then have read as regression coverage. The unspent-window half
  // of `weeklyBindingPct` is pinned in the spent-weekly regression above.
});

test("a free row does not grey, and a blocked row is crit and does not grey either", () => {
  const old = { asOf: NOW - 9 * H, staleSince: NOW - 1000 };     // stale twice over
  const freeRec = rec(Object.assign({
    session: { utilization: 91, resetsAt: at(NOW - 2 * H) }, weekly: wk({ utilization: 33 }),
  }, old));
  assert.equal(A.toneForRow(freeRec, A.availability(freeRec, NOW), isStale, NOW), "ok",
    "the account worth switching to is stale BY CONSTRUCTION; greying it hides it");

  const blockedRec = rec(Object.assign({
    session: { utilization: 91, resetsAt: at(NOW - 2 * H) },
    weekly: wk({ utilization: 100, lockedReason: "Weekly limit reached." }),
  }, old));
  assert.equal(A.toneForRow(blockedRec, A.availability(blockedRec, NOW), isStale, NOW), "crit");
});

test("a measured row takes the WORSE of session, weekly and the API severity", () => {
  // The rule severity.js already owned and `otherRow` was not applying: it
  // passed `percentTone(sessionPct, null)` -- weekly literally null -- so the
  // weekly window could not colour an other-account row at all.
  const tone = (over) => {
    const r = rec(Object.assign({
      session: { utilization: 23, resetsAt: at(NOW + 2 * H) }, asOf: NOW - 60 * 1000,
    }, over));
    return A.toneForRow(r, A.availability(r, NOW), isStale, NOW);
  };
  assert.equal(tone({}), "ok", "23% session / 26% weekly / no severity");
  assert.equal(tone({ weekly: wk({ utilization: 84 }) }), "warn", "the WEEKLY window binds");
  assert.equal(tone({ weekly: wk({ utilization: 97 }) }), "crit");
  assert.equal(tone({ severity: "critical" }), "crit", "the API severity still escalates");
  assert.equal(tone({ staleSince: NOW - 1000 }), "stale",
    "here the stored percentage IS the claim, so staleness greys it");
});

test("toneForRow is total over garbage -- it runs in his real tab", () => {
  for (const bad of [null, undefined, "nonsense", 42, true, NaN, []]) {
    const tone = A.toneForRow(bad, A.availability(bad, NOW), isStale, NOW);
    assert.equal(typeof tone, "string", `record=${String(bad)}`);
  }
  assert.equal(typeof A.toneForRow(rec(), null, isStale, NOW), "string", "no verdict at all");
  assert.equal(typeof A.toneForRow(rec(), undefined, isStale, NOW), "string");
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
