(function () {
  "use strict";

  var MEDIA_URL_RE = /^https?:\/\/(cdn\.discordapp\.com|media\.discordapp\.net)\/.*$/i;
  var ATTR_ENLARGED = "data-dee-enlarged";
  var MAX_WALK_DEPTH = 8;
  var WIDTH_THRESHOLD = 500;
  var HEIGHT_THRESHOLD = 400;
  var DEBOUNCE_MS = 100;

  var observer = null;
  var debounceTimer = null;

  function getComputedStyleFn() {
    if (typeof globalThis !== "undefined" && globalThis.__DEE_GET_COMPUTED_STYLE__) {
      return globalThis.__DEE_GET_COMPUTED_STYLE__;
    }
    if (typeof getComputedStyle === "function") return getComputedStyle;
    return null;
  }

  function parsePx(val) {
    if (typeof val !== "string") return NaN;
    var m = val.match(/([\d.]+)\s*px/);
    return m ? parseFloat(m[1]) : NaN;
  }

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
    return { isMedia: false, element: null, naturalWidth: 0, naturalHeight: 0 };
  }

  function findContainer(el) {
    var getCS = getComputedStyleFn();
    if (!getCS) return null;
    var node = el;
    for (var depth = 0; depth < MAX_WALK_DEPTH; depth++) {
      node = node.parentElement;
      if (!node) return null;
      var cs = getCS(node);
      var mw = parsePx(cs.getPropertyValue("max-width"));
      if (!isNaN(mw) && mw <= WIDTH_THRESHOLD) return node;
      var mh = parsePx(cs.getPropertyValue("max-height"));
      if (!isNaN(mh) && mh <= HEIGHT_THRESHOLD) return node;
    }
    return null;
  }

  function applyOverride(el) {
    if (!el || !el.getAttribute) return { ok: false, removed: false };
    if (el.getAttribute(ATTR_ENLARGED) === "1") return { ok: true, removed: false };
    var container = findContainer(el);
    var removed = false;
    if (container && container.style) {
      var cs = container.style;
      var hadConstraint = false;
      if (cs.getPropertyValue("max-width") && cs.getPropertyValue("max-width") !== "none") hadConstraint = true;
      if (cs.getPropertyValue("max-height") && cs.getPropertyValue("max-height") !== "none") hadConstraint = true;
      cs.setProperty("max-width", "none", "important");
      cs.setProperty("max-height", "none", "important");
      cs.setProperty("width", "auto", "important");
      cs.setProperty("height", "auto", "important");
      removed = hadConstraint;
    }
    if (el.style) {
      el.style.setProperty("max-width", "100%", "important");
      el.style.setProperty("max-height", "none", "important");
      el.style.setProperty("width", "auto", "important");
      el.style.setProperty("height", "auto", "important");
      el.style.setProperty("object-fit", "contain", "important");
      el.style.setProperty("cursor", "zoom-in", "important");
    }
    el.setAttribute(ATTR_ENLARGED, "1");
    return { ok: true, removed: removed };
  }

  function scanMedia(root) {
    if (!root || !root.querySelectorAll) return 0;
    var imgs = root.querySelectorAll("img");
    var vids = root.querySelectorAll("video");
    var all = [];
    for (var i = 0; i < imgs.length; i++) all.push(imgs[i]);
    for (var j = 0; j < vids.length; j++) all.push(vids[j]);
    var count = 0;
    for (var k = 0; k < all.length; k++) {
      var info = isMediaElement(all[k]);
      if (info.isMedia) {
        applyOverride(info.element);
        count++;
      }
    }
    return count;
  }

  function extractChannelId(url) {
    if (typeof url !== "string") return null;
    var m = url.match(/discord\.com\/channels\/(?:@me|(\d+))\/(\d+)/);
    return m ? (m[1] || m[2]) : null;
  }

  function observe(doc) {
    if (!doc || !doc.body) return null;
    observer = new MutationObserver(function (mutations) {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        for (var i = 0; i < mutations.length; i++) {
          var added = mutations[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var node = added[j];
            if (node.nodeType === 1) scanMedia(node);
          }
        }
      }, DEBOUNCE_MS);
    });
    observer.observe(doc.body, { childList: true, subtree: true });
    return observer;
  }

  function forget(doc) {
    if (observer) { observer.disconnect(); observer = null; }
    if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
    var d = doc || (typeof document !== "undefined" ? document : null);
    if (!d) return;
    var all = d.querySelectorAll("[" + ATTR_ENLARGED + "]");
    for (var i = 0; i < all.length; i++) all[i].removeAttribute(ATTR_ENLARGED);
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE__ = {
      MEDIA_URL_RE: MEDIA_URL_RE,
      isMediaElement: isMediaElement,
      findContainer: findContainer,
      applyOverride: applyOverride,
      scan: scanMedia,
      extractChannelId: extractChannelId,
      observe: observe,
      forget: forget
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) return;
  if (typeof document === "undefined") return;
  scanMedia(document);
  observe(document);
}());
