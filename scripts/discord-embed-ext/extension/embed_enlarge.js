(function () {
  "use strict";

  var MEDIA_URL_RE = /^https?:\/\/(cdn\.discordapp\.com|media\.discordapp\.net)\/(attachments|embeds)\/.*$/i;
  var EMOJI_RE = /^https?:\/\/cdn\.discordapp\.com\/emojis\//i;
  var STICKER_RE = /^https?:\/\/cdn\.discordapp\.com\/stickers\//i;
  var ATTR_ENLARGED = "data-dee-enlarged";
  var ATTR_CLEARED = "data-dee-cleared";
  var STYLE_ID = "dee-enlarge-css";
  var DEBOUNCE_MS = 100;

  var observer = null;
  var debounceTimer = null;

  function isDiscordMedia(src) {
    if (!src || typeof src !== "string") return false;
    return MEDIA_URL_RE.test(src) && !EMOJI_RE.test(src) && !STICKER_RE.test(src);
  }

  function isMediaElement(el) {
    if (!el || !el.tagName) return { isMedia: false, element: null };
    var tag = el.tagName.toLowerCase();
    if (tag === "img") {
      var src = el.getAttribute("src") || "";
      if (isDiscordMedia(src)) return { isMedia: true, element: el };
    }
    if (tag === "video") {
      var vsrc = el.getAttribute("src") || "";
      if (isDiscordMedia(vsrc)) return { isMedia: true, element: el };
      var sources = el.children || [];
      for (var i = 0; i < sources.length; i++) {
        var child = sources[i];
        if (child && child.tagName && child.tagName.toLowerCase() === "source") {
          var ssrc = child.getAttribute("src") || "";
          if (isDiscordMedia(ssrc)) return { isMedia: true, element: el };
        }
      }
    }
    return { isMedia: false, element: null };
  }

  var ENLARGE_CSS = [
    "img[src*='cdn.discordapp.com/attachments/'],",
    "img[src*='media.discordapp.net/attachments/'],",
    "video[src*='cdn.discordapp.com/attachments/'],",
    "video[src*='media.discordapp.net/attachments/'] {",
    "  max-width: none !important;",
    "  max-height: none !important;",
    "  width: auto !important;",
    "  height: auto !important;",
    "  object-fit: contain !important;",
    "}",
    ":has(> img[src*='cdn.discordapp.com/attachments/']),",
    ":has(> img[src*='media.discordapp.net/attachments/']),",
    ":has(> video[src*='cdn.discordapp.com/attachments/']),",
    ":has(> video[src*='media.discordapp.net/attachments/']),",
    ":has(> source[src*='cdn.discordapp.com/attachments/']),",
    ":has(> source[src*='media.discordapp.net/attachments/']) {",
    "  max-width: none !important;",
    "  max-height: none !important;",
    "  height: auto !important;",
    "  overflow: visible !important;",
    "}",
    "[class*='imageWrapper'],",
    "[class*='mosaicItem'],",
    "[class*='attachment'],",
    "[class*='wrapper-'],",
    "[class*='wrapper_'],",
    "[class*='imageContainer'] {",
    "  max-width: none !important;",
    "  max-height: none !important;",
    "  height: auto !important;",
    "  overflow: visible !important;",
    "}"
  ].join("\n");

  function injectStylesheet(doc) {
    if (!doc || !doc.head) return;
    if (doc.getElementById(STYLE_ID)) return;
    var style = doc.createElement("style");
    style.id = STYLE_ID;
    style.textContent = ENLARGE_CSS;
    doc.head.appendChild(style);
  }

  function removeStylesheet(doc) {
    if (!doc) return;
    var el = doc.getElementById(STYLE_ID);
    if (el && el.parentElement) el.parentElement.removeChild(el);
  }

  function clearParentConstraints(el) {
    if (typeof getComputedStyle === "function") {
      var parent = el.parentElement;
      var depth = 0;
      while (parent && parent !== document.body && depth < 8) {
        if (parent.nodeType !== 1) { parent = parent.parentElement; depth++; continue; }
        if (parent.getAttribute(ATTR_CLEARED)) break;
        parent.setAttribute(ATTR_CLEARED, "1");
        var cs = getComputedStyle(parent);
        if (cs.overflow === "hidden" || cs.overflowX === "hidden" || cs.overflowY === "hidden") {
          parent.style.setProperty("overflow", "visible", "important");
        }
        if (cs.maxHeight && cs.maxHeight !== "none") {
          parent.style.setProperty("max-height", "none", "important");
        }
        if (cs.maxWidth && cs.maxWidth !== "none") {
          parent.style.setProperty("max-width", "none", "important");
        }
        if (cs.height && cs.height !== "auto") {
          parent.style.setProperty("height", "auto", "important");
        }
        parent = parent.parentElement;
        depth++;
      }
    }
  }

  function markMediaElements(root) {
    if (!root || !root.querySelectorAll) return 0;
    var imgs = root.querySelectorAll("img");
    var vids = root.querySelectorAll("video");
    var all = [];
    for (var i = 0; i < imgs.length; i++) all.push(imgs[i]);
    for (var j = 0; j < vids.length; j++) all.push(vids[j]);
    var count = 0;
    for (var k = 0; k < all.length; k++) {
      var info = isMediaElement(all[k]);
      if (info.isMedia && !info.element.getAttribute(ATTR_ENLARGED)) {
        info.element.setAttribute(ATTR_ENLARGED, "1");
        info.element.style.setProperty("cursor", "zoom-in", "important");
        clearParentConstraints(info.element);
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
        if (observer) observer.disconnect();
        var found = 0;
        for (var i = 0; i < mutations.length; i++) {
          var added = mutations[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var node = added[j];
            if (node.nodeType === 1) found += markMediaElements(node);
          }
        }
        if (found > 0 && observer && doc.body) {
          observer.observe(doc.body, { childList: true, subtree: true });
        }
      }, DEBOUNCE_MS);
    });
    observer.observe(doc.body, { childList: true, subtree: true });
    return observer;
  }

  function restoreParentConstraints(el) {
    if (typeof document !== "undefined" && document.body) {
      var parent = el.parentElement;
      var depth = 0;
      while (parent && parent !== document.body && depth < 8) {
        if (parent.nodeType !== 1) { parent = parent.parentElement; depth++; continue; }
        if (!parent.getAttribute(ATTR_CLEARED)) break;
        parent.removeAttribute(ATTR_CLEARED);
        parent.style.removeProperty("overflow");
        parent.style.removeProperty("max-height");
        parent.style.removeProperty("max-width");
        parent.style.removeProperty("height");
        parent = parent.parentElement;
        depth++;
      }
    }
  }

  function forget(doc) {
    if (observer) { observer.disconnect(); observer = null; }
    if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
    var d = doc || (typeof document !== "undefined" ? document : null);
    if (!d) return;
    removeStylesheet(d);
    var all = d.querySelectorAll("[" + ATTR_ENLARGED + "]");
    for (var i = 0; i < all.length; i++) {
      restoreParentConstraints(all[i]);
      all[i].removeAttribute(ATTR_ENLARGED);
      all[i].style.removeProperty("cursor");
    }
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE__ = {
      MEDIA_URL_RE: MEDIA_URL_RE,
      EMOJI_RE: EMOJI_RE,
      STICKER_RE: STICKER_RE,
      isDiscordMedia: isDiscordMedia,
      isMediaElement: isMediaElement,
      ENLARGE_CSS: ENLARGE_CSS,
      injectStylesheet: injectStylesheet,
      markMediaElements: markMediaElements,
      extractChannelId: extractChannelId,
      observe: observe,
      forget: forget
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) return;
  if (typeof document === "undefined") return;
  injectStylesheet(document);
  markMediaElements(document);
  observe(document);
}());
