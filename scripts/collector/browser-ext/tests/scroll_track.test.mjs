// Pure, deterministic tests for scroll_track.js (the scroll-engagement
// accumulator). No window/document/chrome, no Date.now() — all scroll geometry
// and timestamps are passed in.
//
// Run: nix-shell -p nodejs --run "node --test scripts/collector/browser-ext/tests/"
import test from "node:test";
import assert from "node:assert/strict";

// scroll_track.js is now a CLASSIC (non-ESM) content script: it assigns its
// factory to globalThis.__activityScrollTracker as a side effect (no exports).
// Load it for that side effect, then read the global — exactly how the sibling
// content_scroll.js content script consumes it in the shared isolated world.
await import("../scroll_track.js");
const createScrollTracker = globalThis.__activityScrollTracker;
const GAP_MS = createScrollTracker.GAP_MS;

// The classic script must have published the factory on the global.
test("scroll_track publishes its factory on the global", () => {
  assert.equal(typeof globalThis.__activityScrollTracker, "function");
  assert.equal(typeof GAP_MS, "number");
});

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

// ---------------------------------------------------------------------------
// deriveScrollGeometry — pure element→geometry derivation used by the content
// script's capture-phase document scroll listener (root cause #2). Tested with
// minimal mock document/window objects (no real DOM).
// ---------------------------------------------------------------------------
const deriveScrollGeometry = createScrollTracker.deriveScrollGeometry;
const MIN_RATIO = createScrollTracker.MIN_SCROLLABLE_RATIO;

// Build a fake document whose scrollingElement carries the given geometry.
function mockDoc(scrollTop, scrollHeight) {
  const documentElement = { scrollTop: 0, scrollHeight: 0, clientHeight: 0 };
  const body = {};
  const doc = {
    documentElement,
    body,
    scrollingElement: { scrollTop, scrollHeight },
  };
  return doc;
}

test("deriveScrollGeometry reads document scroll via scrollingElement + innerHeight", () => {
  const doc = mockDoc(800, 5000);
  const win = { innerHeight: 900 };
  // target === document → document-scroll branch.
  assert.deepEqual(deriveScrollGeometry(doc, doc, win), {
    pos: 800,
    viewport: 900,
    total: 5000,
  });
  // target === <html> and === <body> resolve through the same branch.
  assert.deepEqual(deriveScrollGeometry(doc.documentElement, doc, win), {
    pos: 800,
    viewport: 900,
    total: 5000,
  });
  assert.deepEqual(deriveScrollGeometry(doc.body, doc, win), {
    pos: 800,
    viewport: 900,
    total: 5000,
  });
});

test("deriveScrollGeometry reads an inner container's own geometry", () => {
  const doc = mockDoc(0, 0);
  const win = { innerHeight: 900 };
  // A real SPA inner scroller (e.g. Discord message list): tall content in a
  // short viewport. 300px viewport / 3000px content → qualifies (ratio 10).
  const el = { scrollTop: 1200, clientHeight: 300, scrollHeight: 3000 };
  assert.deepEqual(deriveScrollGeometry(el, doc, win), {
    pos: 1200,
    viewport: 300,
    total: 3000,
  });
});

test("deriveScrollGeometry guards against trivial scrollers (tiny dropdowns)", () => {
  const doc = mockDoc(0, 0);
  const win = { innerHeight: 900 };
  // Just below the ratio threshold → ignored (returns null).
  const tiny = {
    scrollTop: 5,
    clientHeight: 100,
    scrollHeight: Math.floor(100 * MIN_RATIO) - 1, // < 1.3× → trivial
  };
  assert.equal(deriveScrollGeometry(tiny, doc, win), null);

  // At/above the threshold → qualifies.
  const ok = {
    scrollTop: 5,
    clientHeight: 100,
    scrollHeight: Math.ceil(100 * MIN_RATIO) + 1, // ≥ 1.3× → real scroller
  };
  assert.deepEqual(deriveScrollGeometry(ok, doc, win), {
    pos: 5,
    viewport: 100,
    total: Math.ceil(100 * MIN_RATIO) + 1,
  });

  // A zero-height element can never qualify (avoids divide-by-trivial).
  const zero = { scrollTop: 0, clientHeight: 0, scrollHeight: 1000 };
  assert.equal(deriveScrollGeometry(zero, doc, win), null);
});

test("deriveScrollGeometry returns null for an unknown / non-scrollable target", () => {
  const doc = mockDoc(0, 0);
  const win = { innerHeight: 900 };
  assert.equal(deriveScrollGeometry({}, doc, win), null); // no scrollTop number
  assert.equal(deriveScrollGeometry(null, doc, win), null);
  assert.equal(deriveScrollGeometry(doc, doc, undefined).viewport, 0); // no win → 0 viewport
});
