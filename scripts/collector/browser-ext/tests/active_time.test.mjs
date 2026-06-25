// Pure, deterministic tests for active_time.js (the idle/blur-aware active-time
// accumulator). No chrome stubs, no Date.now() — all timestamps are passed in.
//
// Run: nix-shell -p nodejs --run "node --test scripts/collector/browser-ext/tests/"
import test from "node:test";
import assert from "node:assert/strict";
import * as AT from "../active_time.js";

// (a) accrues while active
test("accrues wall-clock while active", () => {
  const s = AT.freshState(0, true); // active at t=0
  assert.equal(AT.activeMs(s, 1000), 1000);
  assert.equal(AT.activeMs(s, 5000), 5000);
});

// (b) does NOT accrue across an idle period: idle@t1 → active@t2 contributes 0
test("idle period contributes zero active time", () => {
  const s = AT.freshState(0, true);
  AT.onIdle(s, 1000);            // bank [0,1000] = 1000, pause
  AT.onActive(s, 21_000);       // resume after 20s idle (away)
  // The 20s of idle [1000, 21000] must NOT count.
  assert.equal(AT.take(s, 22_000), 1000 + 1000); // 1000 banked + [21000,22000]
});

// (c) does NOT accrue while blurred
test("blurred span contributes zero active time", () => {
  const s = AT.freshState(0, true);
  AT.onBlur(s, 2000);           // bank [0,2000] = 2000, pause
  // 10 minutes blurred...
  assert.equal(AT.activeMs(s, 602_000), 2000); // nothing accrued while paused
});

// (d) resumes correctly after idle→active
test("resumes accrual after idle then active", () => {
  const s = AT.freshState(0, true);
  AT.onIdle(s, 3000);           // banked 3000
  AT.onActive(s, 10_000);       // resume
  assert.equal(AT.activeMs(s, 12_000), 3000 + 2000); // 3000 + [10000,12000]
});

// (e) the 1h cap clamps an absurd value (edge-case missed transition)
test("take() clamps to the 1-hour cap", () => {
  const s = AT.freshState(0, true);
  // Two hours of "active" with no idle/blur signal at all (a missed transition).
  const got = AT.take(s, 7_200_000);
  assert.equal(got, AT.ACTIVE_CAP_MS);
  assert.equal(AT.ACTIVE_CAP_MS, 3_600_000);
});

// (f) take() resets state (banked cleared; re-anchored if still active)
test("take() resets the accumulator and re-anchors if active", () => {
  const s = AT.freshState(0, true);
  AT.onIdle(s, 1000);           // banked 1000
  AT.onActive(s, 5000);         // resume
  const first = AT.take(s, 6000); // 1000 + [5000,6000] = 2000
  assert.equal(first, 2000);
  assert.equal(s.banked, 0);
  // Still active, so re-anchored to 6000: the next span starts fresh.
  assert.equal(AT.activeMs(s, 6500), 500);
  const second = AT.take(s, 7000); // [6000,7000] = 1000
  assert.equal(second, 1000);
});

// take() while paused stays paused and resets banked
test("take() while paused returns banked and stays paused", () => {
  const s = AT.freshState(0, true);
  AT.onBlur(s, 4000);           // banked 4000, paused
  const got = AT.take(s, 9000); // paused → no live span
  assert.equal(got, 4000);
  assert.equal(s.banked, 0);
  assert.equal(AT.isActive(s), false);
  // Stays at 0 until a fresh onActive.
  assert.equal(AT.activeMs(s, 99_000), 0);
});

// onActive is idempotent — duplicate active/focus signals must not drop the
// in-progress span by re-anchoring.
test("onActive is idempotent (does not reset an in-progress span)", () => {
  const s = AT.freshState(0, true);
  AT.onActive(s, 5000);  // already active — should be a no-op, keep since=0
  assert.equal(AT.activeMs(s, 10_000), 10_000);
});

// onInactive is idempotent — a second idle/blur while paused banks nothing.
test("onInactive is idempotent while paused", () => {
  const s = AT.freshState(0, true);
  AT.onIdle(s, 1000);   // banked 1000
  AT.onIdle(s, 5000);   // already paused — no extra bank
  assert.equal(s.banked, 1000);
});

// fromStored normalizes garbage / partial blobs back to a valid state.
test("fromStored normalizes bad input", () => {
  assert.deepEqual(AT.fromStored(undefined), { banked: 0, since: null });
  assert.deepEqual(AT.fromStored({ banked: -5, since: "x" }), { banked: 0, since: null });
  assert.deepEqual(AT.fromStored({ banked: 100, since: 200 }), { banked: 100, since: 200 });
});

// A realistic scenario matching the harness finding: a tab "focused" 42 min of
// wall-clock but the user was idle/away for most of it → active_ms reflects
// only the genuinely-active spans, well under the window.
test("real-world: long-focused-but-idle tab logs only active spans", () => {
  const s = AT.freshState(0, true);
  AT.onActive(s, 0);
  // 5 min active, then idle for 30 min, then 7 min active.
  AT.onIdle(s, 5 * 60_000);            // banked 5 min
  AT.onActive(s, 35 * 60_000);         // back after 30 min away
  const active = AT.take(s, 42 * 60_000); // + 7 min
  assert.equal(active, (5 + 7) * 60_000); // 12 min, NOT 42
});
