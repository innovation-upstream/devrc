// Pure, deterministic tests for scroll_track.js (the scroll-engagement
// accumulator). No window/document/chrome, no Date.now() — all scroll geometry
// and timestamps are passed in.
//
// Run: nix-shell -p nodejs --run "node --test scripts/collector/browser-ext/tests/"
import test from "node:test";
import assert from "node:assert/strict";
import { createScrollTracker, GAP_MS } from "../scroll_track.js";

// (a) scroll_pct is the MAX depth reached — it goes UP, never back down when you
// scroll back toward the top.
test("scroll_pct tracks max depth and never decreases", () => {
  const t = createScrollTracker();
  // 1000px doc, 500px viewport. At top: (0+500)/1000 = 50%.
  assert.equal(t.onScroll(0, 500, 1000, 100).scroll_pct, 50);
  // Scroll to bottom: (500+500)/1000 = 100%.
  assert.equal(t.onScroll(500, 500, 1000, 200).scroll_pct, 100);
  // Scroll back up to top: depth READ stays at the max (100), not 50.
  assert.equal(t.onScroll(0, 500, 1000, 300).scroll_pct, 100);
});

// scroll_pct rounds: (200+500)/1000 = 70%.
test("scroll_pct rounds the depth percent", () => {
  const t = createScrollTracker();
  assert.equal(t.onScroll(200, 500, 1000, 0).scroll_pct, 70);
  // 333/1000 viewport+offset → (250+333)/1000 = 58.3 → 58.
  assert.equal(t.onScroll(250, 333, 1000, 250).scroll_pct, 70); // max stays 70
  assert.equal(t.onScroll(750, 333, 1000, 500).scroll_pct, 100); // 1083 → clamp
});

// (b) depth clamps to 100 even when content+viewport overshoot scrollHeight.
test("scroll_pct clamps to 100", () => {
  const t = createScrollTracker();
  // Overshoot: (900+500)/1000 = 140% → clamp to 100.
  assert.equal(t.onScroll(900, 500, 1000, 0).scroll_pct, 100);
  // A zero / tiny scrollHeight uses max(1, …) and still clamps sanely.
  const t2 = createScrollTracker();
  assert.equal(t2.onScroll(0, 0, 0, 0).scroll_pct, 0);
  assert.equal(t2.onScroll(10, 5, 0, 1).scroll_pct, 100);
});

// (c) scroll_ms accumulates WITHIN a burst but NOT across a >=1s gap.
test("scroll_ms accumulates within a burst, not across a gap", () => {
  const t = createScrollTracker();
  // Burst: samples 250ms apart → deltas count.
  t.onScroll(0, 500, 2000, 0);      // first sample: no prior, +0
  t.onScroll(50, 500, 2000, 250);   // +250
  t.onScroll(100, 500, 2000, 500);  // +250  → 500 so far
  assert.equal(t.snapshot().scroll_ms, 500);
  // Gap >= GAP_MS ends the burst: this delta must NOT be added.
  t.onScroll(150, 500, 2000, 500 + GAP_MS + 10); // gap 1010ms → +0
  assert.equal(t.snapshot().scroll_ms, 500);
  // New burst resumes accruing from the next in-window sample.
  t.onScroll(200, 500, 2000, 500 + GAP_MS + 10 + 200); // +200
  assert.equal(t.snapshot().scroll_ms, 700);
});

// A delta exactly at GAP_MS is treated as a gap (boundary is exclusive).
test("scroll_ms treats a delta == GAP_MS as a new burst", () => {
  const t = createScrollTracker();
  t.onScroll(0, 500, 2000, 0);
  t.onScroll(10, 500, 2000, GAP_MS); // delta == GAP_MS → not counted
  assert.equal(t.snapshot().scroll_ms, 0);
});

// (d) snapshot() is stable/idempotent and reset() clears all state.
test("snapshot is stable and reset clears state", () => {
  const t = createScrollTracker();
  t.onScroll(500, 500, 1000, 0);   // 100%
  t.onScroll(500, 500, 1000, 250); // +250 ms
  const a = t.snapshot();
  const b = t.snapshot();
  assert.deepEqual(a, b);                       // stable
  assert.deepEqual(a, { scroll_pct: 100, scroll_ms: 250 });
  t.reset();
  assert.deepEqual(t.snapshot(), { scroll_pct: 0, scroll_ms: 0 });
  // After reset, the next sample has no prior anchor (no carried-over delta).
  t.onScroll(0, 500, 1000, 9999);
  assert.deepEqual(t.snapshot(), { scroll_pct: 50, scroll_ms: 0 });
});

// A backwards / non-advancing clock never subtracts or adds negative time.
test("scroll_ms ignores non-advancing timestamps", () => {
  const t = createScrollTracker();
  t.onScroll(0, 500, 2000, 1000);
  t.onScroll(50, 500, 2000, 900);  // backwards → delta < 0, +0
  t.onScroll(60, 500, 2000, 900);  // same ts → delta 0, +0
  assert.equal(t.snapshot().scroll_ms, 0);
});
