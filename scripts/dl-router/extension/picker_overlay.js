// picker_overlay.js -- the picker delivered IN THE PAGE (classic content
// script, same declaration as content_capture.js, so no new permissions).
//
// WHAT IT DOES NOT DO: it does not reimplement the picker. It frames
// `picker.html` -- the same document, running the same `picker.js`, driven by
// the same reducer -- inside a CLOSED shadow root on the page. A second copy of
// the picker's state machine is precisely the drift that safety.py and
// sanitize.js paid for, and this file exists to make sure there is only ever
// one. Everything the reducer has learned (the `done` latch, single-flight
// finish, choose-failed above the guard, a failed snapshot counting as still
// loading, never creating a directory on Enter before the list lands) is
// inherited rather than restated, because it is literally the same code.
//
// Why a frame and not a rendered list:
//   * the picker needs `chrome.runtime.sendMessage` with an EXTENSION identity,
//     and a content script's isolated world cannot import an ES module without
//     making it web-accessible anyway;
//   * an iframe is a separate document, so page CSS cannot reach the picker and
//     the picker's CSS cannot reach the page -- stronger than a shadow root
//     alone. The shadow root is still there, and is what stops the page styling
//     or discovering the HOST element.
//
// It is NOT dismissed by an outside click or by losing focus. That is the exact
// behaviour that ruled out `chrome.action.openPopup()`: a picker that vanishes
// on blur silently discards the pick and leaves the file in the catch-all.
//
// Test hooks, matching content_capture.js: `globalThis.DL_ROUTER_NO_AUTOSTART`
// suppresses listener install; `globalThis.__DLR_OVERLAY__` exposes the pure
// functions.

(function () {
  "use strict";

  var HOST_ID = "dl-router-picker-host";
  var WIDTH = 460;
  var HEIGHT = 420;

  // Positioning lives on the HOST, inline and !important, because the host is
  // the one node the page's stylesheets can see and match on.
  var HOST_STYLE = [
    "all: initial !important",
    "position: fixed !important",
    "top: 16px !important",
    "right: 16px !important",
    "width: " + WIDTH + "px !important",
    "height: " + HEIGHT + "px !important",
    // Above anything a page is likely to have parked at 2147483647-ish; this
    // is a modal question about a download that already started.
    "z-index: 2147483647 !important",
    "display: block !important",
    "visibility: visible !important",
    "opacity: 1 !important",
    "pointer-events: auto !important",
    "margin: 0 !important",
    "padding: 0 !important",
    "border: 0 !important",
  ].join("; ");

  // Inside the shadow root, so none of it can leak onto the page.
  var SHADOW_CSS = [
    ":host { contain: layout style; }",
    ".frame {",
    "  display: block; width: 100%; height: 100%;",
    "  border: 1px solid rgba(128,128,128,.45); border-radius: 8px;",
    "  box-shadow: 0 8px 28px rgba(0,0,0,.28);",
    "  background: Canvas; color-scheme: light dark;",
    // Subtle entrance: the same 110-260ms register the picker's own rows use,
    // so the overlay and its contents read as one piece of UI.
    "  animation: dlr-overlay-in 140ms ease-out;",
    "}",
    "@keyframes dlr-overlay-in {",
    "  from { opacity: 0; transform: translateY(-6px); }",
    "  to   { opacity: 1; transform: translateY(0); }",
    "}",
    "@media (prefers-reduced-motion: reduce) {",
    "  .frame { animation: none; }",
    "}",
  ].join("\n");

  // The one live overlay, or null. Exactly one at a time: a second picker
  // stacked on the first would make it ambiguous which download an Enter
  // answers, and the reducer has no notion of "which download" -- the id is
  // baked into the frame's URL.
  var current = null;

  function detach(overlay) {
    if (!overlay || !overlay.host) return false;
    try {
      if (typeof overlay.host.remove === "function") {
        overlay.host.remove();
      } else if (overlay.host.parentNode) {
        overlay.host.parentNode.removeChild(overlay.host);
      }
    } catch (e) {
      return false;
    }
    return true;
  }

  /** Build the host + closed shadow root + frame. Throws on a hostile DOM. */
  function build(doc, url, id) {
    var host = doc.createElement("div");
    host.setAttribute("id", HOST_ID);
    host.setAttribute("style", HOST_STYLE);
    // CLOSED, not open: the page must not be able to reach into the shadow to
    // read the frame's URL. That URL carries the per-open nonce the service
    // worker uses to match the overlay, and a page that could read it could
    // forge a close and quietly discard the pick.
    var shadow = host.attachShadow({ mode: "closed" });
    var style = doc.createElement("style");
    style.textContent = SHADOW_CSS;
    var frame = doc.createElement("iframe");
    frame.className = "frame";
    frame.setAttribute("title", "Save to library");
    frame.setAttribute("src", url);
    shadow.appendChild(style);
    shadow.appendChild(frame);
    // FOCUS THE FRAME, AND AGAIN ON LOAD. The picker is keyboard-first, and
    // `input.focus()` inside the framed document only moves focus WITHIN that
    // document -- the frame element itself has to hold focus for keystrokes to
    // reach it at all. The immediate call may land before the frame exists to
    // focus, so the load handler is the one that usually does the work. Without
    // this the user would have to click the overlay before they could type,
    // which the popup window never required.
    //
    // THIS IS NOT SUFFICIENT ON ITS OWN, and that is why `dlr:focus-overlay`
    // exists. The worker RAISES the tab and its window after the frame reports
    // ready (see openOverlayPicker) -- and raising a tab puts focus on the
    // page's own document, taking it straight back off a frame that had just
    // been given it. Whichever of the two lands last wins, so the worker asks
    // for the focus again once it has finished raising things.
    frame.addEventListener("load", function () {
      try {
        frame.focus();
      } catch (e) { /* the page may have moved on */ }
    });
    var mountPoint = doc.body || doc.documentElement;
    if (!mountPoint) throw new Error("no document to mount into");
    mountPoint.appendChild(host);
    try {
      frame.focus();
    } catch (e) { /* the load handler above is the reliable one */ }
    return { id: id, host: host, shadow: shadow, frame: frame };
  }

  /**
   * Handle `dlr:overlay-open`.
   *
   * `{ok: true}` here means ONLY "the host element and frame were created". It
   * is deliberately not the delivery signal: a content-script-injected iframe
   * is subject to the PAGE's Content-Security-Policy, so a site with a strict
   * `frame-src` blocks the load while every DOM call here still succeeds. The
   * service worker waits for `dlr:picker-ready`, which can only be sent from
   * inside a booted extension context, and falls back to a popup window if it
   * never arrives.
   */
  function openOverlay(doc, msg) {
    if (!doc || !msg || typeof msg.url !== "string" || !msg.url
        || typeof msg.id !== "string" || !msg.id) {
      return { ok: false, error: "bad_request" };
    }
    if (current) detach(current);
    current = null;
    try {
      current = build(doc, msg.url, msg.id);
    } catch (e) {
      current = null;
      return { ok: false, error: "create_failed" };
    }
    return { ok: true, id: msg.id };
  }

  /** Is the live overlay still in the document? */
  function stillAttached() {
    if (!current || !current.host) return false;
    if (typeof current.host.isConnected === "boolean") {
      return current.host.isConnected;
    }
    return Boolean(current.host.parentNode);
  }

  /**
   * THE OVERLAY CAN BE TAKEN AWAY BY THE PAGE, and that must not silently cost
   * the user the question.
   *
   * `document.body.innerHTML = ...`, a DOM sanitiser, a framework re-render, or
   * nine lines of hostile script all detach the host. The worker has already
   * been told the overlay was delivered, so without this the download is left
   * with no picker and nothing notices. Reporting it makes the worker re-ask in
   * a popup window, which the page cannot reach.
   *
   * The closed shadow root is NOT what protects the pick here -- a page does
   * not need the frame's URL to remove the element next to it. This is.
   */
  function reportLostIfDetached(notify) {
    if (!current || stillAttached()) return false;
    var lost = current.id;
    current = null;
    try {
      if (typeof notify === "function") notify(lost);
    } catch (e) { /* the worker will not learn; nothing else to try */ }
    return true;
  }

  /**
   * Handle `dlr:close-overlay`. An id that does not match the live overlay is
   * a no-op rather than a blind teardown: the worker retries and times out on
   * its own schedule, and a stale close must not remove a picker that a later
   * download legitimately opened.
   */
  function closeOverlay(id, doc) {
    if (!current) {
      // NO RECORD IS NOT "NOTHING TO DO". This content script is re-injected on
      // every navigation, so a re-injected copy has `current === null` while a
      // host element from before may still be sitting in the document. Without
      // this sweep the user would face a 460x420 modal with no close button and
      // no working Esc, and the only way out would be a page reload.
      var stray = (doc && typeof doc.getElementById === "function")
        ? doc.getElementById(HOST_ID) : null;
      if (stray) {
        detach({ host: stray });
        return { ok: true, swept: true };
      }
      return { ok: false, error: "no_overlay" };
    }
    if (id && current.id !== id) return { ok: false, error: "stale" };
    var removed = detach(current);
    current = null;
    return { ok: removed };
  }

  /**
   * Handle `dlr:focus-overlay`. Put keyboard focus back on the live frame.
   *
   * The id must match: a stale focus request must not yank the caret into a
   * picker a LATER download opened, and a request for an overlay we do not
   * have is a no-op rather than a blind `document.activeElement` grab.
   */
  function focusOverlay(id) {
    if (!current || !current.frame) return { ok: false, error: "no_overlay" };
    if (id && current.id !== id) return { ok: false, error: "stale" };
    if (typeof current.frame.focus !== "function") {
      return { ok: false, error: "not_focusable" };
    }
    try {
      current.frame.focus();
    } catch (e) {
      return { ok: false, error: "focus_failed" };
    }
    return { ok: true };
  }

  function hasOverlay() {
    return Boolean(current);
  }

  /** Reset hook for tests; also the honest answer after a navigation. */
  function forget() {
    current = null;
  }

  function handleMessage(doc, msg, sendResponse) {
    if (!msg || typeof msg !== "object") return false;
    if (msg.type === "dlr:overlay-open") {
      sendResponse(openOverlay(doc, msg));
      return false;
    }
    if (msg.type === "dlr:close-overlay") {
      sendResponse(closeOverlay(msg.overlay, doc));
      return false;
    }
    if (msg.type === "dlr:focus-overlay") {
      sendResponse(focusOverlay(msg.overlay));
      return false;
    }
    return false;
  }

  /**
   * Watch for the host being detached. `childList` on `body` only -- the host
   * is a direct child of it, and `innerHTML =` or a `removeChild` both show up
   * there. A `subtree: true` observer over every page the user visits would be
   * a real cost for no extra coverage.
   */
  function watchAttachment(doc, notify) {
    if (typeof MutationObserver !== "function") return null;
    if (!doc || !doc.body) return null;
    var obs = new MutationObserver(function () {
      if (reportLostIfDetached(notify)) obs.disconnect();
    });
    try {
      obs.observe(doc.body, { childList: true });
    } catch (e) {
      return null;
    }
    return obs;
  }

  /** Top frame only: `all_frames` is for capture, not for painting a picker. */
  function isTopFrame(win) {
    try {
      return Boolean(win) && win.top === win.self;
    } catch (e) {
      return false;
    }
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DLR_OVERLAY__ = {
      HOST_ID: HOST_ID,
      openOverlay: openOverlay,
      closeOverlay: closeOverlay,
      focusOverlay: focusOverlay,
      handleMessage: handleMessage,
      hasOverlay: hasOverlay,
      stillAttached: stillAttached,
      reportLostIfDetached: reportLostIfDetached,
      watchAttachment: watchAttachment,
      forget: forget,
      isTopFrame: isTopFrame,
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DL_ROUTER_NO_AUTOSTART) {
    return;
  }
  if (typeof chrome === "undefined" || !chrome.runtime) return;
  if (!isTopFrame(typeof window !== "undefined" ? window : null)) return;

  /** Tell the worker the overlay is gone, so it can re-ask in a window. */
  function reportLost(lostId) {
    try {
      var sent = chrome.runtime.sendMessage(
        { type: "dlr:overlay-lost", overlay: lostId });
      if (sent && typeof sent.catch === "function") sent.catch(function () {});
    } catch (e) { /* worker restarting */ }
  }

  var watcher = null;

  // `_sender` is unused: this listener only ever hears from OUR service worker,
  // and the frame the message is about is identified by the overlay id in the
  // payload. Named rather than dropped because sendResponse is the third arg.
  chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
    var out = handleMessage(document, msg, sendResponse);
    // The watcher is armed only while an overlay is live: an always-on
    // MutationObserver on every page the user visits would be a real cost.
    if (msg && msg.type === "dlr:overlay-open" && hasOverlay()) {
      if (watcher) watcher.disconnect();
      watcher = watchAttachment(document, reportLost);
    } else if (msg && msg.type === "dlr:close-overlay" && !hasOverlay()) {
      if (watcher) watcher.disconnect();
      watcher = null;
    }
    return out;
  });
}());
