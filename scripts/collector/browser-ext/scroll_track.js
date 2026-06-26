// scroll_track.js — PURE scroll-engagement accounting for the browser collector.
//
// Tracks, for a SINGLE page view, two reading-engagement metrics from throttled
// scroll samples:
//
//   scroll_pct — MAX reading depth reached, as a percent of the document:
//                round(100 * (scrollY + innerHeight) / max(1, scrollHeight)),
//                clamped 0–100. It only ever goes UP within a view — scrolling
//                back toward the top does NOT lower it.
//   scroll_ms  — accumulated ACTIVE-scroll time: the sum of "scrolling bursts".
//                Consecutive throttled samples < GAP_MS apart extend the current
//                burst (their inter-sample delta is added); a gap ≥ GAP_MS ends
//                the burst, so idle reading time between scrolls is NOT counted.
//
// Mirrors active_time.js's design: PURE — no window/document/chrome, no
// Date.now(); every method takes `now` (ms) from the caller, and state is plain
// so content_scroll.js stays a thin DOM/chrome wrapper that's trivially
// unit-testable in plain Node.

// Gap (ms) at/above which two scroll samples are treated as separate bursts.
export const GAP_MS = 1000;

// Build a fresh per-page-view scroll tracker. `gapMs` overridable for tests.
export function createScrollTracker(gapMs = GAP_MS) {
  let maxPct = 0;     // max depth reached this view (monotonic)
  let scrollMs = 0;   // accumulated active-scroll time across bursts
  let lastTs = null;  // timestamp of the previous in-burst sample, or null

  function clampPct(p) {
    if (!Number.isFinite(p)) return 0;
    if (p < 0) return 0;
    if (p > 100) return 100;
    return Math.round(p);
  }

  return {
    // Feed one (throttled) scroll sample. `now` is a monotonic-ish ms timestamp.
    // Returns the current snapshot for convenience.
    onScroll(scrollY, innerHeight, scrollHeight, now) {
      const denom = Math.max(1, Number(scrollHeight) || 0);
      const reached =
        ((Number(scrollY) || 0) + (Number(innerHeight) || 0)) / denom * 100;
      const pct = clampPct(reached);
      if (pct > maxPct) maxPct = pct;

      if (Number.isFinite(now)) {
        if (lastTs !== null) {
          const delta = now - lastTs;
          // Only accrue time for samples that continue the current burst; a gap
          // ≥ gapMs (or a non-advancing/backwards clock) starts a new burst.
          if (delta > 0 && delta < gapMs) scrollMs += delta;
        }
        lastTs = now;
      }
      return this.snapshot();
    },

    // Stable, non-mutating read of the current running metrics. Idempotent.
    snapshot() {
      return { scroll_pct: maxPct, scroll_ms: Math.round(scrollMs) };
    },

    // Clear all state for a fresh page view.
    reset() {
      maxPct = 0;
      scrollMs = 0;
      lastTs = null;
    },
  };
}
