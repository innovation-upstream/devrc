// content_scroll.js — thin DOM/chrome wiring around the pure scroll_track.js.
//
// Injected (as a CLASSIC content script — MV3 declarative content scripts are
// not ES modules) into all http/https pages. The pure tracker is injected as a
// SIBLING content script (scroll_track.js, listed FIRST in manifest's `js` array)
// which publishes its factory on the shared isolated-world global as
// `globalThis.__activityScrollTracker`. Content scripts of the same extension
// share one isolated-world global, so we read the factory directly — NO dynamic
// import / web_accessible_resources (that path was CSP-fragile and failed
// silently, leaving scroll_pct stuck at 0).
//
// We listen at the DOCUMENT level in the CAPTURE phase so scroll from ANY
// element bubbles to us — including SPA inner containers (Discord/Gmail/Grafana)
// that scroll a div rather than the document. We feed throttled samples into the
// tracker and report the running {scroll_pct, scroll_ms} to the service worker
// so the SW can fold those reading-engagement metrics into the page view's `nav`
// event.
//
// We deliberately DO NOT emit a per-scroll event stream — only a low-rate
// running update plus a final flush when the page is hidden/unloaded.
//
// Best-effort throughout: chrome.runtime.sendMessage may fail because the MV3
// worker is asleep or the page/extension context is being torn down — we swallow
// every error; the SW picks up a later update or the leaving tab simply reports 0.

(() => {
  const SCROLL_THROTTLE_MS = 250; // min spacing between processed scroll samples
  const REPORT_THROTTLE_MS = 2000; // min spacing between sendMessage updates

  let tracker = null;
  let deriveScrollGeometry = null; // pure geometry helper from the sibling module
  let lastSampleTs = 0;
  let lastReportTs = 0;
  let pendingReport = false;

  function now() {
    return Date.now();
  }

  // Best-effort: tell the SW the current running metrics. Swallows the
  // "receiving end does not exist" / "Extension context invalidated"
  // rejections that happen when the worker is suspended or the page unloads.
  function report() {
    if (!tracker) return;
    const snap = tracker.snapshot();
    try {
      const p = chrome.runtime.sendMessage({
        kind: "scroll",
        scroll_pct: snap.scroll_pct,
        scroll_ms: snap.scroll_ms,
      });
      if (p && typeof p.then === "function") p.then(() => {}, () => {});
    } catch (_e) {
      // chrome.runtime gone (context invalidated) — nothing to do.
    }
  }

  function onScroll(event) {
    if (!tracker) return;
    const t = now();
    if (t - lastSampleTs < SCROLL_THROTTLE_MS) return;

    // Derive (pos, viewport, total) from whatever element actually scrolled —
    // document root OR an SPA inner container — using the pure helper the
    // sibling tracker module published (single source of the trivial-scroller
    // threshold). null = trivial/unknown scroller → don't sample or throttle.
    const geo = deriveScrollGeometry(event && event.target, document, window);
    if (!geo) return;
    lastSampleTs = t;

    // tracker keeps the MAX depth across all qualifying scrollers this view.
    tracker.onScroll(geo.pos, geo.viewport, geo.total, t);

    pendingReport = true;
    // Low-rate running update while the user keeps scrolling.
    if (t - lastReportTs >= REPORT_THROTTLE_MS) {
      lastReportTs = t;
      pendingReport = false;
      report();
    }
  }

  // Final flush when the page becomes hidden or is being unloaded, so the last
  // burst of scrolling isn't lost between the throttle window and tab-leave.
  function flush() {
    if (!tracker) return;
    pendingReport = false;
    lastReportTs = now();
    report();
  }

  // Build the tracker synchronously from the shared global the sibling content
  // script (scroll_track.js) published. If it's missing (load order broke, or
  // the page blocked injection), no-op — the page is simply un-instrumented.
  const createScrollTracker = globalThis.__activityScrollTracker;
  const derive =
    createScrollTracker && createScrollTracker.deriveScrollGeometry;
  if (
    typeof createScrollTracker === "function" &&
    typeof derive === "function"
  ) {
    tracker = createScrollTracker();
    deriveScrollGeometry = derive;
    // Capture-phase, document-level: catches scroll from ANY element, including
    // SPA inner containers that don't bubble a window-level scroll event.
    document.addEventListener("scroll", onScroll, {
      capture: true,
      passive: true,
    });
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flush();
    });
    window.addEventListener("pagehide", flush);
  }
})();
