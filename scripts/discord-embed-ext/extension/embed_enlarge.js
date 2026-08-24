(function () {
  "use strict";

  var MEDIA_URL_RE = /^https?:\/\/(cdn\.discordapp\.com|media\.discordapp\.net)\/.*$/i;
  var MAX_WALK_DEPTH = 8;
  var WIDTH_THRESHOLD = 500;
  var HEIGHT_THRESHOLD = 400;
  var DEBOUNCE_MS = 100;
  var ATTR_ENLARGED = "data-dee-enlarged";

  var observer = null;
  var debounceTimer = null;

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

  function findContainer(el) {
    var node = el;
    var getCS = (typeof globalThis !== "undefined" && globalThis.__DEE_GET_COMPUTED_STYLE__) ||
                (typeof getComputedStyle !== "undefined" ? getComputedStyle : null);
    for (var depth = 0; depth < MAX_WALK_DEPTH; depth++) {
      node = node.parentElement;
      if (!node) return null;
      if (!getCS) return null;
      var cs = getCS(node);
      var mw = parseFloat(cs.getPropertyValue("max-width"));
      var mh = parseFloat(cs.getPropertyValue("max-height"));
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

  function scan(root) {
    var doc = root.ownerDocument || root;
    var imgs = (doc.querySelectorAll ? doc.querySelectorAll("img") : []);
    var videos = (doc.querySelectorAll ? doc.querySelectorAll("video") : []);
    var count = 0;
    var all = [];
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

  function extractChannelId(url) {
    if (!url || typeof url !== "string") return null;
    var m = url.match(/discord\.com\/channels\/@me\/(\d+)\/\d+/);
    return m ? m[1] : null;
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
  }

  function observe(doc) {
    if (typeof MutationObserver === "undefined") return null;
    var body = doc.body || doc.documentElement;
    if (!body) return null;
    observer = new MutationObserver(function (mutations) {
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        for (var i = 0; i < mutations.length; i++) {
          var added = mutations[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var node = added[j];
            if (node.nodeType === 1) scan(node);
          }
        }
      }, DEBOUNCE_MS);
    });
    observer.observe(body, { childList: true, subtree: true });
    return observer;
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE__ = {
      MEDIA_URL_RE: MEDIA_URL_RE,
      isMediaElement: isMediaElement,
      findContainer: findContainer,
      applyOverride: applyOverride,
      scan: scan,
      extractChannelId: extractChannelId,
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
