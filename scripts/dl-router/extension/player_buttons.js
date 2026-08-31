// player_buttons.js -- a download button per embedded player (classic content
// script, same declaration as content_capture.js, so no new permissions).
//
// WHY THIS FILE EXISTS, AND WHY IT RUNS WHERE IT DOES.
//
// On the forums this routes for there are ZERO native <video> elements. Every
// video is a cross-origin iframe (a real OOPIF) pointing at an embed host. A
// content script on the FORUM origin cannot reach the media element at all --
// it can see the iframe's `src` and nothing inside it. The manifest already
// injects on `https://*/*` with `all_frames: true`, so a content script DOES
// run inside the embed frame, and that is the only place the media URL exists.
//
// Hence the two-layer rule table (see content_capture.contextSelectors):
//
//   * the PLAYER rule is keyed on the EMBED HOST and consumed here, in the
//     embed frame: container, media-URL accessor, mount point.
//   * the CONTEXT rule is keyed on the PAGE HOST and consumed by
//     content_capture.js, in the top frame: what is this page about.
//
// Keying the player rule on the forum instead would be wrong twice over: the
// forum's embed wrapper is host-AGNOSTIC (it wraps embeds from arbitrary
// hosts), so such a rule would match embeds we have no player rule for, and it
// would still be running in the wrong document to read anything.
//
// THE ONE RULE THAT MATTERS MOST: the media URL is SIGNED AND EXPIRES. The
// embed page assigns it at frame load and re-signs it in place before expiry
// and on error. So it is present without any user interaction -- but it must be
// READ AT CLICK TIME and never cached. A URL captured at render time is a URL
// that has already rotated by the time anyone clicks it.
//
// What this deliberately does NOT do: use the player's own download control.
// On the observed host that anchor points at an HTML interstitial (HEAD 405,
// GET text/html, no Content-Disposition); handing it to chrome.downloads saves
// a web page.
//
// Test hooks, matching content_capture.js and picker_overlay.js:
// `globalThis.DL_ROUTER_NO_AUTOSTART` suppresses listener install;
// `globalThis.__DLR_PLAYER__` exposes the pure functions.

(function () {
  "use strict";

  var HOST_ATTR = "data-dl-router-player";
  var LABEL = "Save to library";
  var BUSY_LABEL = "Saving...";
  var HAVE_LABEL = "In library";

  // The grammar a selector may use. Kept to EXACTLY the subset
  // tests/fake_dom.mjs parses -- tag, .class, #id, [attr], [attr="v"],
  // [attr^="v"], descendant combinators, comma groups -- so a rule that works
  // in Brave is a rule the suite can exercise. A richer selector would still
  // work in the browser and silently have no test, which is how the two drift.
  //
  // Bracketed sections are stripped first: `[href^="https://x"]` legitimately
  // contains a colon.
  var BRACKETS = /\[[^\]]*\]/g;
  var UNSUPPORTED = /[>+~():]/;
  // An attribute or property NAME, never an expression. See readMediaUrl.
  var ACCESSOR_NAME = /^[A-Za-z][A-Za-z0-9_-]*$/;
  var HTTP_URL = /^https?:\/\/[^\s]+$/i;
  // Mirrors config.MAX_MEDIA_ACCESSORS. A cap, not a design target: every
  // accessor is a selector query run inside the CLICK handler. Both sides must
  // agree -- a list the sidecar accepts and this rejects renders NO button,
  // which is silent.
  var MAX_MEDIA_ACCESSORS = 8;

  function isSupportedSelector(selector) {
    if (typeof selector !== "string") return false;
    var s = selector.trim();
    if (!s || s.length > 300) return false;
    return !UNSUPPORTED.test(s.replace(BRACKETS, ""));
  }

  /**
   * Normalise one host's `player` sub-table, or null.
   *
   * CONFIG IS DATA, NEVER CODE. Only the four fields below are read, each is
   * type- and grammar-checked, and anything else in the table is dropped on the
   * floor. A rule carrying `js`, `onclick` or `code` gets exactly the same
   * treatment as a rule carrying a typo: ignored. Config-supplied JavaScript
   * executing in a content script -- on every page, with the extension's
   * isolated world -- is a code-injection surface, and the way to not have one
   * is to have no path that could evaluate a string.
   *
   * A rule that fails validation yields null, which renders NO button. That is
   * the deliberate failure mode: a button that cannot resolve a media URL is
   * worse than no button, because it looks like the feature is working.
   */
  function normalisePlayerRule(raw) {
    if (!raw || typeof raw !== "object") return null;
    var player = (raw.player && typeof raw.player === "object")
      ? raw.player : null;
    if (!player) return null;
    var media = (player.media && typeof player.media === "object")
      ? player.media : null;
    if (!media) return null;
    if (!isSupportedSelector(player.container)) return null;
    // ONE accessor or an ORDERED LIST. `attr` is a single NAME, so one pair
    // cannot say "the image's ANCHOR href, but the video's own src" -- which is
    // exactly what a site serving both kinds needs. Normalised to a list here
    // so readMediaUrl has one shape to walk.
    var raws = Array.isArray(media) ? media : [media];
    if (!raws.length || raws.length > MAX_MEDIA_ACCESSORS) return null;
    var accessors = [];
    for (var a = 0; a < raws.length; a += 1) {
      var acc = raws[a];
      if (!acc || typeof acc !== "object") return null;
      if (!isSupportedSelector(acc.element)) return null;
      if (typeof acc.attr !== "string" || !ACCESSOR_NAME.test(acc.attr)) {
        // STRICT, and it must stay strict. Dropping the bad accessor and
        // keeping the good ones would render a button that silently covers
        // only some media on the page -- the failure this function's docstring
        // calls worse than no button, because it looks like it is working.
        return null;
      }
      accessors.push({ element: acc.element.trim(), attr: acc.attr });
    }
    var mount = isSupportedSelector(player.mount) ? player.mount.trim() : "";
    return {
      container: player.container.trim(),
      mount: mount,
      media: accessors,
      label: (typeof player.label === "string" && player.label
        && player.label.length <= 40) ? player.label : LABEL,
    };
  }

  /** The player rule for a host, or null when this host has none. */
  function playerRuleFor(siteRules, host) {
    var cap = globalThis.__DLR_CAPTURE__;
    var entry = (cap && typeof cap.rulesForHost === "function")
      ? cap.rulesForHost(siteRules, host) : null;
    return normalisePlayerRule(entry);
  }

  function safeQuery(dom, selector, root) {
    try {
      var out = dom.queryAll(selector, root);
      return out && typeof out.length === "number" ? out : [];
    } catch (e) {
      return [];
    }
  }

  /**
   * READ THE MEDIA URL. Called from the CLICK HANDLER AND NOWHERE ELSE.
   *
   * If you find a caller on the render path, that is the bug this comment
   * exists for: the URL is signed with a ~2h expiry and is re-signed in place
   * on the same element, so a value read when the button was drawn is stale by
   * the time it is used. `player_buttons.test.mjs` pins this by rotating the
   * attribute between render and click and asserting the NEW value is sent.
   *
   * `attr` is a NAME, not an expression (ACCESSOR_NAME). The content attribute
   * is tried first because that is what an assignment to `video.src` reflects
   * to; the same-named property is the fallback, which is how a host that only
   * exposes `currentSrc` (not an attribute at all) still works. Only a string
   * is ever accepted, and it is never called.
   */
  function readMediaUrl(dom, rule, container) {
    if (!rule) return "";
    // ORDER IS THE CONTRACT, not an implementation detail: the first accessor
    // that yields an http(s) URL wins, so a rule puts the accessor that
    // resolves the BEST copy first. On a site that serves one asset at two
    // resolutions -- a downscaled <img src> under an <a> pointing at the
    // original -- reading the anchor first is what saves the posted file
    // rather than the thumbnail, and it does so BY CONSTRUCTION: the anchor's
    // URL is already signed for its own host, so nothing has to be rewritten.
    var pairs = rule.media;
    var nodes = [];
    for (var p = 0; p < pairs.length; p += 1) {
      var attr = pairs[p].attr;
      nodes = safeQuery(dom, pairs[p].element, container);
      for (var i = 0; i < nodes.length; i += 1) {
        var node = nodes[i];
        var value = "";
        try {
          if (node && typeof node.getAttribute === "function") {
            value = node.getAttribute(attr) || "";
          }
          if (!value && node && typeof node[attr] === "string") {
            value = node[attr];
          }
        } catch (e) {
          value = "";
        }
        // The frame-side shape check is a nicety so the button can say "not
        // ready" instead of firing a doomed download. The AUTHORITATIVE gate is
        // sanitize.isHttpUrl in the service worker, which is the only side that
        // can refuse.
        if (typeof value === "string" && HTTP_URL.test(value.trim())) {
          return value.trim();
        }
      }
    }
    return "";
  }

  // --- the widget ----------------------------------------------------------- //
  // Inside a CLOSED shadow root, for the same reasons picker_overlay.js uses
  // one: the page cannot style it, cannot read it, and its own CSS cannot leak
  // out onto a player it is sitting on top of.
  var HOST_STYLE = [
    "all: initial !important",
    "position: absolute !important",
    "top: 8px !important",
    "left: 8px !important",
    "z-index: 2147483646 !important",
    "display: block !important",
    "visibility: visible !important",
    "opacity: 1 !important",
    "pointer-events: auto !important",
    "margin: 0 !important",
    "padding: 0 !important",
    "border: 0 !important",
  ].join("; ");

  var SHADOW_CSS = [
    ":host { contain: layout style; }",
    ".row { display: flex; gap: 6px; align-items: center;",
    "  font: 500 12px/1.2 system-ui, sans-serif; }",
    ".btn {",
    "  font: inherit; cursor: pointer; border-radius: 6px;",
    "  border: 1px solid rgba(255,255,255,.35); padding: 5px 9px;",
    "  background: rgba(20,20,20,.62); color: #f2f2f2;",
    // Unobtrusive: nearly invisible until the player is hovered or the button
    // is focused. A permanent opaque control over someone's video is a worse
    // trade than one extra pixel of discovery cost.
    "  opacity: .35; transition: opacity 120ms ease-out;",
    "}",
    ".btn:hover, .btn:focus-visible { opacity: 1; }",
    ".btn[disabled] { cursor: default; opacity: .35; }",
    ".badge {",
    "  border-radius: 6px; padding: 4px 7px; opacity: .9;",
    "  background: rgba(30,90,40,.85); color: #eaffea;",
    "  font: 500 11px/1.2 system-ui, sans-serif;",
    "}",
    "@media (prefers-reduced-motion: reduce) {",
    "  .btn { transition: none; opacity: .8; }",
    "}",
  ].join("\n");

  function build(dom, rule) {
    var host = dom.create("div");
    host.setAttribute(HOST_ATTR, "1");
    host.setAttribute("style", HOST_STYLE);
    var shadow = host.attachShadow({ mode: "closed" });
    var style = dom.create("style");
    style.textContent = SHADOW_CSS;
    var row = dom.create("div");
    row.className = "row";
    var button = dom.create("button");
    button.className = "btn";
    button.setAttribute("type", "button");
    button.setAttribute("part", "dlr-save");
    button.textContent = rule.label;
    row.appendChild(button);
    shadow.appendChild(style);
    shadow.appendChild(row);
    return { host: host, shadow: shadow, row: row, button: button, badge: null };
  }

  function setLabel(handle, text) {
    if (handle && handle.button) handle.button.textContent = text;
  }

  function setDisabled(handle, value) {
    if (!handle || !handle.button) return;
    handle.button.disabled = Boolean(value);
    if (value) handle.button.setAttribute("disabled", "");
  }

  /**
   * The "already have this" badge.
   *
   * `res.have` comes from the sidecar's source-URL ledger, asked BY THE EMBED
   * PAGE URL -- see route_core.playerSourceKey for why it cannot be the media
   * URL. A miss renders nothing at all rather than an "not in library" chip:
   * the ledger only started recording in #244, so a miss means "no record",
   * which is not the same claim as "you do not have it".
   */
  function applyHave(dom, handle, res) {
    if (!handle || !res || res.have !== true) return false;
    if (handle.badge) return false;
    var badge = dom.create("span");
    badge.className = "badge";
    badge.textContent = res.dir ? HAVE_LABEL + ": " + res.dir : HAVE_LABEL;
    badge.setAttribute("title", "This player's URL is already in the ledger");
    handle.row.appendChild(badge);
    handle.badge = badge;
    return true;
  }

  /**
   * The click. Reads the media URL NOW (see readMediaUrl) and hands it to the
   * service worker, which is where the download and every correlation happen.
   *
   * `deps.send(msg)` -> Promise. `deps.pageUrl()` -> this frame's location.
   */
  function handleClick(dom, handle, deps) {
    var url = readMediaUrl(dom, handle.rule, handle.container);
    if (!url) {
      // VISIBLE AND HARMLESS. A player whose src has not been assigned yet
      // (preload="none" on an off-screen frame) must not fire a download with
      // an empty URL and must not silently do nothing.
      setLabel(handle, "No media yet");
      return Promise.resolve({ ok: false, error: "no_media_url" });
    }
    setLabel(handle, BUSY_LABEL);
    setDisabled(handle, true);
    return Promise.resolve(deps.send({
      type: "dlr:player-download",
      mediaUrl: url,
      // The STABLE key (normalised worker-side). Never the media URL.
      embedUrl: deps.pageUrl(),
    })).then(function (res) {
      setDisabled(handle, false);
      if (res && res.ok) {
        setLabel(handle, "Saved");
        return res;
      }
      setLabel(handle, "Failed");
      return res || { ok: false, error: "no_response" };
    }, function () {
      setDisabled(handle, false);
      setLabel(handle, "Failed");
      return { ok: false, error: "send_failed" };
    });
  }

  /** Where inside a container the widget is mounted. */
  function mountPointFor(dom, rule, container) {
    if (rule.mount) {
      var found = safeQuery(dom, rule.mount, container)[0];
      if (found) return found;
    }
    return container;
  }

  /**
   * Mount the widget on one container. Returns the handle, or null when the
   * DOM refused it (a hostile or half-built document).
   */
  function mountButton(dom, rule, container, deps) {
    var handle;
    try {
      handle = build(dom, rule);
    } catch (e) {
      return null;
    }
    handle.rule = rule;
    handle.container = container;
    try {
      mountPointFor(dom, rule, container).appendChild(handle.host);
    } catch (e) {
      return null;
    }
    try {
      handle.button.addEventListener("click", function (ev) {
        if (ev && typeof ev.preventDefault === "function") ev.preventDefault();
        handleClick(dom, handle, deps);
      });
    } catch (e) { /* a button that cannot listen is still better than a throw */ }
    // The badge is asked for ONCE per mount, and never blocks the button.
    try {
      Promise.resolve(deps.send({
        type: "dlr:have", embedUrl: deps.pageUrl(),
      })).then(function (res) {
        applyHave(dom, handle, res);
      }, function () { /* no ledger answer: no badge, which is the honest one */ });
    } catch (e) { /* same */ }
    return handle;
  }

  function isConnected(node) {
    if (!node) return false;
    if (typeof node.isConnected === "boolean") return node.isConnected;
    return Boolean(node.parentNode);
  }

  /**
   * Mount on every container that does not already carry a live widget.
   *
   * IDEMPOTENT, and re-entrant on purpose: players are `loading="lazy"`, an
   * off-screen frame has no document at all yet, and a forum thread renders
   * more posts as you scroll. So this is called on load AND from a mutation
   * observer, and both must be able to run repeatedly without stacking
   * buttons. The liveness check is `isConnected` on OUR host rather than a
   * marker attribute on the page's node: a framework re-render that replaces
   * the container's subtree drops our host, and we have to notice and remount.
   */
  function scan(dom, rule, deps, mounted) {
    var out = [];
    if (!rule) return out;   // no player rule for this host: no buttons, ever
    var containers = safeQuery(dom, rule.container);
    for (var i = 0; i < containers.length; i += 1) {
      var container = containers[i];
      var existing = mounted.get(container);
      if (existing && isConnected(existing.host)) continue;
      var handle = mountButton(dom, rule, container, deps);
      if (!handle) continue;
      mounted.set(container, handle);
      out.push(handle);
    }
    return out;
  }

  /** The real-document adapter. Everything above is written against this. */
  function docAdapter(doc, win) {
    return {
      queryAll: function (selector, root) {
        var scope = root || doc;
        return Array.prototype.slice.call(scope.querySelectorAll(selector));
      },
      create: function (tag) { return doc.createElement(tag); },
      url: (win && win.location) ? win.location.href
        : (doc.location ? doc.location.href : ""),
    };
  }

  function isTopFrame(win) {
    try {
      return Boolean(win) && win.top === win.self;
    } catch (e) {
      return false;
    }
  }

  /**
   * Answer `dlr:page-context` -- the TOP frame describing itself.
   *
   * The embed frame cannot identify its own thread and no amount of effort in
   * the frame will change that: `document.referrer` is the forum ORIGIN only
   * (the path is stripped by `strict-origin-when-cross-origin`) and
   * `location.ancestorOrigins` gives origins without paths. So the subject has
   * to be carried from the top frame, and the service worker is the only place
   * that can reach both.
   *
   * It REUSES content_capture's extractor rather than restating it -- same
   * isolated world, same file list in the manifest. A second subject extractor
   * is exactly the drift picker_overlay.js exists to avoid.
   */
  function pageContext(doc, win, siteRules) {
    var cap = globalThis.__DLR_CAPTURE__;
    if (!cap || typeof cap.extractContext !== "function") {
      return { ok: false, error: "no_capture" };
    }
    var url = (win && win.location) ? win.location.href
      : (doc && doc.location ? doc.location.href : "");
    try {
      return { ok: true, context: cap.extractContext(
        cap.domAdapter(doc, url, doc ? doc.title : ""), {}, siteRules) };
    } catch (e) {
      return { ok: false, error: "extract_failed" };
    }
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DLR_PLAYER__ = {
      HOST_ATTR: HOST_ATTR,
      isSupportedSelector: isSupportedSelector,
      normalisePlayerRule: normalisePlayerRule,
      playerRuleFor: playerRuleFor,
      readMediaUrl: readMediaUrl,
      mountButton: mountButton,
      handleClick: handleClick,
      applyHave: applyHave,
      mountPointFor: mountPointFor,
      scan: scan,
      docAdapter: docAdapter,
      pageContext: pageContext,
      isTopFrame: isTopFrame,
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DL_ROUTER_NO_AUTOSTART) {
    return;
  }
  if (typeof chrome === "undefined" || !chrome.runtime) return;

  var siteRules = null;
  var rule = null;
  var mounted = new Map();
  var dom = docAdapter(document,
    typeof window !== "undefined" ? window : null);

  function send(msg) {
    try {
      return Promise.resolve(chrome.runtime.sendMessage(msg));
    } catch (e) {
      return Promise.reject(e);
    }
  }

  var deps = { send: send, pageUrl: function () { return dom.url; } };

  function rescan() {
    try {
      scan(dom, rule, deps, mounted);
    } catch (e) { /* a page that breaks this must not break the page */ }
  }

  // A CONTAINER CAN APPEAR LONG AFTER LOAD. The player is `loading="lazy"`, so
  // an off-screen frame has no document until it is scrolled near -- and when
  // this script does run in it, the embed page may still be building the
  // player. `subtree: true` here, unlike picker_overlay's body-only observer:
  // that one watches for ONE node it put there itself, this one is looking for
  // a node the page has not created yet, at an unknown depth.
  function watch() {
    if (typeof MutationObserver !== "function") return null;
    if (!document.body) return null;
    var obs = new MutationObserver(function () { rescan(); });
    try {
      obs.observe(document.body, { childList: true, subtree: true });
    } catch (e) {
      return null;
    }
    return obs;
  }

  chrome.runtime.onMessage.addListener(function (msg, _sender, sendResponse) {
    if (!msg || typeof msg !== "object") return false;
    if (msg.type === "dlr:page-context") {
      if (!isTopFrame(typeof window !== "undefined" ? window : null)) {
        return false;   // frameId 0 is targeted, but never assume the targeting
      }
      sendResponse(pageContext(document, window, siteRules));
      return false;
    }
    return false;
  });

  try {
    chrome.runtime.sendMessage({ type: "dlr:rules" }, function (resp) {
      if (!resp || !resp.siteRules) return;
      siteRules = resp.siteRules;
      var cap = globalThis.__DLR_CAPTURE__;
      var host = (cap && typeof cap.hostOf === "function")
        ? cap.hostOf(dom.url) : "";
      rule = playerRuleFor(siteRules, host);
      // NO RULE FOR THIS HOST: nothing is mounted and nothing is observed. The
      // forum's embed wrapper is host-agnostic, so this is the ORDINARY case
      // for an embed from a host we have never configured -- and the right
      // answer there is no button at all, not a button that cannot resolve.
      if (!rule) return;
      rescan();
      watch();
    });
  } catch (e) { /* the SW may be asleep; nothing to mount without rules */ }
}());
