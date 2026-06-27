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
// PURE by design: no window/document/chrome, no
// Date.now(); every method takes `now` (ms) from the caller, and state is plain
// so content_scroll.js stays a thin DOM/chrome wrapper that's trivially
// unit-testable in plain Node.
//
// LOADING MODEL: this is a CLASSIC (non-ESM) script. It is injected as the FIRST
// content script (before content_scroll.js) and publishes its factory on the
// shared isolated-world global as `globalThis.__activityScrollTracker`. There is
// NO top-level `export`/`import` — dynamic import of a web-accessible resource
// from a content script is CSP-fragile and was failing silently (the tracker was
// never created → scroll_pct stuck at 0). content_scroll.js reads the global
// directly. The test loads this file for its side effect and reads the global.

(function () {
  "use strict";

  // Gap (ms) at/above which two scroll samples are treated as separate bursts.
  const GAP_MS = 1000;

  // Build a fresh per-page-view scroll tracker. `gapMs` overridable for tests.
  function createScrollTracker(gapMs = GAP_MS) {
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

  // Minimum overflow ratio (scrollHeight / clientHeight) for an inner container
  // to count as a real scroller. Below this it's a trivial overflow — a tiny
  // dropdown / autocomplete menu that would otherwise report ~100% depth the
  // instant it scrolls a few pixels. Kept here as the single source of truth.
  const MIN_SCROLLABLE_RATIO = 1.3;

  // PURE: derive (pos, viewport, total) for whatever element actually scrolled.
  // `target` is a scroll event's target; `doc`/`win` are the document and window
  // (injected so this stays testable without a real DOM). Returns null when the
  // scroller is the *document* (handled by the documentEl branch) is unreadable,
  // or when an inner container is a trivial overflow that shouldn't be sampled.
  //
  //   - Document scroll: target is the document / <html> / <body>. Use the
  //     scrolling element's scrollTop/scrollHeight and the window's innerHeight.
  //   - Inner-container scroll: read the element's own scrollTop / clientHeight /
  //     scrollHeight, after the trivial-scroller guard.
  function deriveScrollGeometry(target, doc, win, minRatio = MIN_SCROLLABLE_RATIO) {
    if (!doc) return null;
    const documentEl = doc.documentElement || null;
    const bodyEl = doc.body || null;

    if (target === doc || target === documentEl || target === bodyEl) {
      const el = doc.scrollingElement || documentEl || {};
      return {
        pos: el.scrollTop || 0,
        viewport: (win && win.innerHeight) || 0,
        total: el.scrollHeight || 0,
      };
    }

    if (target && typeof target.scrollTop === "number") {
      const viewport = target.clientHeight || 0;
      const total = target.scrollHeight || 0;
      // Trivial-scroller guard: require the element to overflow its own
      // viewport by at least `minRatio`× before it counts as deep reading.
      if (viewport <= 0 || total < viewport * minRatio) return null;
      return { pos: target.scrollTop || 0, viewport, total };
    }

    return null;
  }

  // Expose the GAP_MS constant + the threshold + the pure geometry helper on the
  // factory so callers/tests can read them without separate ESM named exports.
  createScrollTracker.GAP_MS = GAP_MS;
  createScrollTracker.MIN_SCROLLABLE_RATIO = MIN_SCROLLABLE_RATIO;
  createScrollTracker.deriveScrollGeometry = deriveScrollGeometry;

  // Publish on the shared isolated-world global. Idempotent: if both content
  // scripts somehow run twice we just re-point at the same factory.
  globalThis.__activityScrollTracker = createScrollTracker;
})();
