// content_scroll.js — thin DOM/chrome wiring around the pure scroll_track.js.
//
// Injected (as a CLASSIC content script — MV3 declarative content scripts are
// not ES modules) into all http/https pages. It dynamically imports the pure
// tracker from the extension package (scroll_track.js is listed in
// web_accessible_resources so the page-world import URL resolves), watches
// `scroll`, feeds throttled samples into the tracker, and reports the running
// {scroll_pct, scroll_ms} to the service worker so the SW can fold those
// reading-engagement metrics into the page view's `nav` event.
//
// We deliberately DO NOT emit a per-scroll event stream — only a low-rate
// running update plus a final flush when the page is hidden/unloaded.
//
// Best-effort throughout: the dynamic import or chrome.runtime.sendMessage may
// fail because the MV3 worker is asleep or the page/extension context is being
// torn down — we swallow every error; the SW picks up a later update or the
// leaving tab simply reports 0.

(() => {
  const SCROLL_THROTTLE_MS = 250; // min spacing between processed scroll samples
  const REPORT_THROTTLE_MS = 2000; // min spacing between sendMessage updates

  let tracker = null;
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

  function onScroll() {
    if (!tracker) return;
    const t = now();
    if (t - lastSampleTs < SCROLL_THROTTLE_MS) return;
    lastSampleTs = t;

    const doc = document.documentElement || document.body || {};
    tracker.onScroll(
      window.scrollY || window.pageYOffset || 0,
      window.innerHeight || 0,
      doc.scrollHeight || 0,
      t,
    );

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

  // Load the pure tracker, then wire up DOM listeners. If the import fails the
  // page is simply un-instrumented (telemetry is best-effort).
  import(chrome.runtime.getURL("scroll_track.js"))
    .then(({ createScrollTracker }) => {
      tracker = createScrollTracker();
      window.addEventListener("scroll", onScroll, { passive: true });
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") flush();
      });
      window.addEventListener("pagehide", flush);
    })
    .catch(() => {
      // Could not load the tracker module — leave the page un-instrumented.
    });
})();
