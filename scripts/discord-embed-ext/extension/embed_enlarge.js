(function () {
  "use strict";

  // 🔴 MESSAGE MEDIA ONLY — matching the bare CDN HOST is wrong, and measurably so.
  // cdn.discordapp.com also serves avatars, server icons, emojis, stickers and
  // banners. Measured against a real logged-in channel on 2026-08-24: 59 of 60
  // <img>/<video> matched the host, and the breakdown was avatars 24 / icons 35 /
  // attachments 0 — with 10 avatars sitting in a 196px-capped container this code
  // would have happily "enlarged". The path prefix is what separates a message
  // attachment from a piece of chrome. `external` is Discord's proxy for linked
  // media in an embed, which IS message content.
  var MEDIA_URL_RE = /^https?:\/\/(cdn\.discordapp\.com|media\.discordapp\.net)\/(attachments|external)\/.+/i;
  var MAX_WALK_DEPTH = 8;
  var WIDTH_THRESHOLD = 500;
  var HEIGHT_THRESHOLD = 400;
  var DEBOUNCE_MS = 100;
  var ATTR_ENLARGED = "data-dee-enlarged";

  var observer = null;
  var debounceTimer = null;
  var pendingNodes = [];

  function isMediaElement(el) {
    if (!el || !el.tagName) return { isMedia: false, element: null, naturalWidth: 0, naturalHeight: 0 };
    var tag = el.tagName.toLowerCase();
    if (tag === "img") {
      var src = el.getAttribute("src") || "";
      if (MEDIA_URL_RE.test(src)) {
        return { isMedia: true, element: el, naturalWidth: el.naturalWidth || 0, naturalHeight: el.naturalHeight || 0 };
      }
    }
    if (tag === "video") {
      var vsrc = el.getAttribute("src") || "";
      if (MEDIA_URL_RE.test(vsrc)) {
        return { isMedia: true, element: el, naturalWidth: el.naturalWidth || 0, naturalHeight: el.naturalHeight || 0 };
      }
      var sources = el.children || [];
      for (var i = 0; i < sources.length; i++) {
        var child = sources[i];
        if (child && child.tagName && child.tagName.toLowerCase() === "source") {
          var ssrc = child.getAttribute("src") || "";
          if (MEDIA_URL_RE.test(ssrc)) {
            return { isMedia: true, element: el, naturalWidth: el.naturalWidth || 0, naturalHeight: el.naturalHeight || 0 };
          }
        }
      }
    }
    if (tag === "source") {
      var parent = el.parentElement;
      if (parent && parent.tagName && parent.tagName.toLowerCase() === "video") {
        var psrc = el.getAttribute("src") || "";
        if (MEDIA_URL_RE.test(psrc)) {
          return { isMedia: true, element: parent, naturalWidth: parent.naturalWidth || 0, naturalHeight: parent.naturalHeight || 0 };
        }
      }
    }
    return { isMedia: false, element: null, naturalWidth: 0, naturalHeight: 0 };
  }

  // 🔴 ONLY A px LENGTH IS A PIXEL CAP. A computed max-width/max-height can be
  // `none`, `400px`, `100%` or a calc(), and `parseFloat("100%")` is `100` —
  // which is <= WIDTH_THRESHOLD. So a PERCENTAGE cap, which is ubiquitous, used
  // to read as a 100px cap: the walk latched the first such ancestor and
  // applyOverride wrote max-width/max-height/width/height `!important` onto it.
  // That ancestor can be shared layout, and there is no undo — forget() clears
  // the marker attribute, not the inline styles. Measured in Brave via CDP:
  // getComputedStyle(el).maxWidth returns the string "100%" for max-width:100%.
  function cssPx(v) {
    if (typeof v !== "string") return NaN;
    var m = /^\s*(-?[0-9]*\.?[0-9]+)px\s*$/.exec(v);
    return m ? parseFloat(m[1]) : NaN;
  }

  function findContainer(el) {
    var node = el;
    var getCS = (typeof globalThis !== "undefined" && globalThis.__DEE_GET_COMPUTED_STYLE__) ||
                (typeof getComputedStyle !== "undefined" ? getComputedStyle : null);
    for (var depth = 0; depth < MAX_WALK_DEPTH; depth++) {
      node = node.parentElement;
      if (!node) return null;
      if (!getCS) return null;
      var cs = getCS(node);
      var mw = cssPx(cs.getPropertyValue("max-width"));
      var mh = cssPx(cs.getPropertyValue("max-height"));
      if (!isNaN(mw) && mw <= WIDTH_THRESHOLD) return node;
      if (!isNaN(mh) && mh <= HEIGHT_THRESHOLD) return node;
    }
    return null;
  }

  function applyOverride(el) {
    var already = el.getAttribute && el.getAttribute(ATTR_ENLARGED);
    if (already === "1") return { ok: true, removed: false };
    var container = findContainer(el);
    var removed = false;
    if (container) {
      var ccs = container.style;
      ccs.setProperty("max-width", "none", "important");
      ccs.setProperty("max-height", "none", "important");
      ccs.setProperty("width", "auto", "important");
      ccs.setProperty("height", "auto", "important");
      removed = true;
    }
    el.style.setProperty("max-width", "100%", "important");
    el.style.setProperty("max-height", "none", "important");
    el.style.setProperty("width", "auto", "important");
    el.style.setProperty("height", "auto", "important");
    el.style.setProperty("object-fit", "contain", "important");
    el.style.setProperty("cursor", "zoom-in", "important");
    if (el.setAttribute) {
      el.setAttribute(ATTR_ENLARGED, "1");
    }
    return { ok: true, removed: removed };
  }

  // 🔴 SCOPED TO `root`. This used to do `root.ownerDocument || root` and then
  // query THAT, i.e. every call was a full-document rescan and the parameter was
  // dead — `var doc = root;` was a surviving mutant. It also masked the observer
  // dropping batches below, since a whole-document sweep re-found everything.
  // A root that IS the media element is handled explicitly: querySelectorAll
  // never matches the element it is called on.
  function scan(root) {
    var scope = (root && root.querySelectorAll) ? root
              : (typeof document !== "undefined" ? document : null);
    if (!scope) return 0;
    var count = 0;
    var all = [];
    var rootTag = (root && root.tagName) ? root.tagName.toLowerCase() : "";
    if (rootTag === "img" || rootTag === "video") all.push(root);
    var imgs = scope.querySelectorAll("img");
    var videos = scope.querySelectorAll("video");
    for (var i = 0; i < imgs.length; i++) all.push(imgs[i]);
    for (var j = 0; j < videos.length; j++) all.push(videos[j]);
    for (var k = 0; k < all.length; k++) {
      var info = isMediaElement(all[k]);
      if (info.isMedia) {
        applyOverride(info.element);
        count++;
      }
    }
    return count;
  }

  function forget(doc) {
    doc = doc || (typeof document !== "undefined" ? document : null);
    if (!doc) return;
    var els = doc.querySelectorAll("[" + ATTR_ENLARGED + "]");
    for (var i = 0; i < els.length; i++) {
      els[i].removeAttribute(ATTR_ENLARGED);
    }
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    // Disconnecting stops NEW batches; a debounce already in flight would still
    // fire and re-mark elements after "forget".
    if (debounceTimer) {
      clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    pendingNodes = [];
  }

  function observe(doc) {
    if (typeof MutationObserver === "undefined") return null;
    var body = doc.body || doc.documentElement;
    if (!body) return null;
    // 🔴 ACCUMULATE ACROSS BATCHES. The debounce used to close over only the
    // NEWEST `mutations` and clearTimeout the pending run, so every batch that
    // arrived within DEBOUNCE_MS of another was silently DISCARDED. Discord
    // renders well after document_idle, so this observer — not the initial
    // scan(document) — is the production path, and it was dropping work.
    observer = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var m = mutations[i];
        // 🔴 AN `src` SET AFTER INSERTION MUST STILL BE SEEN. Scoping scan() to
        // the mutated subtree (correct, and what makes the debounce cheap) gave
        // up a property the old whole-document rescan had by accident: an <img>
        // inserted BEFORE its CDN src is assigned matched nothing on insertion,
        // and used to be picked up by the next unrelated mutation. With a
        // childList-only observer it would be missed permanently. Watching the
        // attribute restores it deliberately instead of by accident.
        if (m.type === "attributes") {
          if (m.target && m.target.nodeType === 1) pendingNodes.push(m.target);
          continue;
        }
        var added = m.addedNodes || [];
        for (var j = 0; j < added.length; j++) {
          if (added[j] && added[j].nodeType === 1) pendingNodes.push(added[j]);
        }
      }
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        debounceTimer = null;
        var batch = pendingNodes;
        pendingNodes = [];
        for (var k = 0; k < batch.length; k++) scan(batch[k]);
      }, DEBOUNCE_MS);
    });
    observer.observe(body, { childList: true, subtree: true,
                            attributes: true, attributeFilter: ["src"] });
    return observer;
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE__ = {
      MEDIA_URL_RE: MEDIA_URL_RE,
      isMediaElement: isMediaElement,
      findContainer: findContainer,
      applyOverride: applyOverride,
      scan: scan,
      cssPx: cssPx,
      forget: forget,
      observe: observe,
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) {
    return;
  }

  if (typeof document !== "undefined") {
    scan(document);
    observe(document);
  }
}());
