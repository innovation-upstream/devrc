// content_capture.js — page-context capture (classic content script, all frames).
//
// Downloaded filenames from file-sharing/tube sites are usually opaque, so the
// routing signal has to come from the PAGE. On a capture-phase mousedown/click/
// contextmenu over an <a href>, <img> or <video> we snapshot what the page says
// about its subject and post it to the service worker, which correlates it back
// to the DownloadItem later (a DownloadItem carries no tabId).
//
// Extraction is DATA-DRIVEN: generic selectors (Open Graph, JSON-LD
// Person/VideoObject, [itemprop=name], meta keywords) plus a per-site rules
// table fetched from the sidecar's config. Adding a site is CONFIG, not code.
//
// Everything below is written against a small DOM ADAPTER rather than `document`
// directly, so `node --test` can drive it over fixture HTML with no browser.
// Test hooks: `globalThis.DL_ROUTER_NO_AUTOSTART` suppresses listener install;
// `globalThis.__DLR_CAPTURE__` exposes the pure functions.

(function () {
  "use strict";

  var MAX_TAGS = 64;
  var MAX_TAG_LEN = 120;
  var MAX_TEXT = 300;
  var MAX_JSONLD_BYTES = 200000;
  var MAX_DEPTH = 6;

  function clampText(value) {
    if (typeof value !== "string") return "";
    var trimmed = value.replace(/\s+/g, " ").trim();
    return trimmed.length > MAX_TEXT ? trimmed.slice(0, MAX_TEXT) : trimmed;
  }

  function clampTag(value) {
    if (typeof value !== "string") return "";
    var trimmed = value.replace(/\s+/g, " ").trim();
    if (!trimmed || trimmed.length > MAX_TAG_LEN) return "";
    return trimmed;
  }

  /** Wrap a real Document in the adapter shape the extractors expect. */
  function domAdapter(doc, url, title) {
    return {
      querySelectorAll: function (sel) {
        try {
          return Array.prototype.slice.call(doc.querySelectorAll(sel));
        } catch (e) {
          return [];   // a malformed per-site selector must not break capture
        }
      },
      url: url !== undefined ? url : (doc.location ? doc.location.href : ""),
      title: title !== undefined ? title : (doc.title || ""),
    };
  }

  function attr(node, name) {
    if (!node || typeof node.getAttribute !== "function") return "";
    var v = node.getAttribute(name);
    return typeof v === "string" ? v : "";
  }

  function text(node) {
    if (!node) return "";
    return clampText(typeof node.textContent === "string" ? node.textContent : "");
  }

  /** Open Graph + Twitter card metadata as a flat map. */
  function extractOg(dom) {
    var og = {};
    var nodes = dom.querySelectorAll('meta[property^="og:"], meta[property^="video:"], meta[property^="article:"], meta[name^="twitter:"]');
    for (var i = 0; i < nodes.length; i += 1) {
      var key = attr(nodes[i], "property") || attr(nodes[i], "name");
      var val = clampText(attr(nodes[i], "content"));
      if (!key || !val) continue;
      og[key.replace(/^og:/, "")] = val;
      og[key] = val;
    }
    return og;
  }

  /** Walk a parsed JSON-LD value for names/actors/keywords. Depth-bounded. */
  function harvestJsonLd(value, out, depth) {
    if (depth > MAX_DEPTH || out.length >= MAX_TAGS) return;
    if (Array.isArray(value)) {
      for (var i = 0; i < value.length && out.length < MAX_TAGS; i += 1) {
        harvestJsonLd(value[i], out, depth + 1);
      }
      return;
    }
    if (!value || typeof value !== "object") return;
    var type = value["@type"];
    var types = Array.isArray(type) ? type : [type];
    var isSubject = types.indexOf("Person") >= 0
      || types.indexOf("PerformingGroup") >= 0
      || types.indexOf("Organization") >= 0;
    if (isSubject && typeof value.name === "string") {
      var n = clampTag(value.name);
      if (n) out.push(n);
    }
    if (types.indexOf("VideoObject") >= 0 || types.indexOf("Movie") >= 0) {
      harvestJsonLd(value.actor, out, depth + 1);
      harvestJsonLd(value.director, out, depth + 1);
      if (typeof value.keywords === "string") {
        var parts = value.keywords.split(",");
        for (var k = 0; k < parts.length && out.length < MAX_TAGS; k += 1) {
          var t = clampTag(parts[k]);
          if (t) out.push(t);
        }
      }
    }
    // Common containers.
    if (value["@graph"]) harvestJsonLd(value["@graph"], out, depth + 1);
    if (value.mainEntity) harvestJsonLd(value.mainEntity, out, depth + 1);
  }

  function extractJsonLd(dom) {
    var out = [];
    var nodes = dom.querySelectorAll('script[type="application/ld+json"]');
    for (var i = 0; i < nodes.length; i += 1) {
      var raw = typeof nodes[i].textContent === "string" ? nodes[i].textContent : "";
      if (!raw || raw.length > MAX_JSONLD_BYTES) continue;
      var parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (e) {
        continue;   // hostile/broken JSON-LD is simply ignored
      }
      harvestJsonLd(parsed, out, 0);
    }
    return out;
  }

  function extractGeneric(dom) {
    var out = [];
    var nodes = dom.querySelectorAll('[itemprop="name"]');
    for (var i = 0; i < nodes.length && out.length < MAX_TAGS; i += 1) {
      var t = clampTag(text(nodes[i]));
      if (t) out.push(t);
    }
    var kw = dom.querySelectorAll('meta[name="keywords"]');
    for (var j = 0; j < kw.length; j += 1) {
      var parts = attr(kw[j], "content").split(",");
      for (var p = 0; p < parts.length && out.length < MAX_TAGS; p += 1) {
        var tag = clampTag(parts[p]);
        if (tag) out.push(tag);
      }
    }
    return out;
  }

  /** Per-site rules: { subject: [selector], tags: [selector] }. */
  function extractSiteRules(dom, rules) {
    var out = [];
    if (!rules) return out;
    var groups = [].concat(rules.subject || [], rules.tags || []);
    for (var i = 0; i < groups.length; i += 1) {
      if (typeof groups[i] !== "string") continue;
      var nodes = dom.querySelectorAll(groups[i]);
      for (var j = 0; j < nodes.length && out.length < MAX_TAGS; j += 1) {
        var t = clampTag(text(nodes[j]) || attr(nodes[j], "content")
          || attr(nodes[j], "title"));
        if (t) out.push(t);
      }
    }
    return out;
  }

  function hostOf(url) {
    try {
      return new URL(url).hostname.toLowerCase();
    } catch (e) {
      return "";
    }
  }

  /**
   * Build the capture payload.
   * `dom`      adapter (see domAdapter)
   * `el`       the clicked element descriptor {href, mediaSrc, linkText, alt}
   * `siteRules` full rules table keyed by hostname
   */
  function extractContext(dom, el, siteRules) {
    el = el || {};
    var site = hostOf(dom.url);
    var rules = siteRules && (siteRules[site]
      || siteRules[site.replace(/^www\./, "")]);
    var tags = [];
    var seen = Object.create(null);
    var push = function (list) {
      for (var i = 0; i < list.length && tags.length < MAX_TAGS; i += 1) {
        var key = list[i].toLowerCase();
        if (seen[key]) continue;
        seen[key] = true;
        tags.push(list[i]);
      }
    };
    // Site rules first: they are the most specific signal available.
    push(extractSiteRules(dom, rules));
    push(extractJsonLd(dom));
    push(extractGeneric(dom));

    return {
      href: typeof el.href === "string" ? el.href : "",
      mediaSrc: typeof el.mediaSrc === "string" ? el.mediaSrc : "",
      linkText: clampText(el.linkText || ""),
      alt: clampText(el.alt || ""),
      pageUrl: dom.url || "",
      pageTitle: clampText(dom.title || ""),
      site: site,
      tags: tags,
      og: extractOg(dom),
    };
  }

  /** Nearest interesting ancestor of the event target, as a descriptor. */
  function describeTarget(node) {
    var depth = 0;
    while (node && depth < 12) {
      var tag = (node.tagName || "").toLowerCase();
      if (tag === "a" && attr(node, "href")) {
        return { href: node.href || attr(node, "href"), linkText: text(node) };
      }
      if (tag === "img") {
        return {
          href: node.currentSrc || node.src || attr(node, "src"),
          mediaSrc: node.currentSrc || node.src || attr(node, "src"),
          alt: attr(node, "alt"),
        };
      }
      if (tag === "video" || tag === "source") {
        return {
          mediaSrc: node.currentSrc || node.src || attr(node, "src"),
          alt: attr(node, "title"),
        };
      }
      node = node.parentElement || node.parentNode;
      depth += 1;
    }
    return null;
  }

  function installListeners(doc, send, getRules) {
    var handler = function (event) {
      var el = describeTarget(event.target);
      if (!el) return;
      var rules = getRules ? getRules() : null;
      try {
        send(extractContext(domAdapter(doc), el, rules));
      } catch (e) {
        // Capture is best-effort; a page that breaks it must not break clicks.
      }
    };
    // Capture phase, so a page that stops propagation cannot hide the click.
    ["mousedown", "click", "contextmenu"].forEach(function (name) {
      doc.addEventListener(name, handler, true);
    });
    return handler;
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DLR_CAPTURE__ = {
      extractContext: extractContext,
      extractOg: extractOg,
      extractJsonLd: extractJsonLd,
      extractGeneric: extractGeneric,
      extractSiteRules: extractSiteRules,
      describeTarget: describeTarget,
      domAdapter: domAdapter,
      installListeners: installListeners,
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DL_ROUTER_NO_AUTOSTART) {
    return;
  }
  if (typeof chrome === "undefined" || !chrome.runtime) return;

  var siteRules = null;
  try {
    chrome.runtime.sendMessage({ type: "dlr:rules" }, function (resp) {
      if (resp && resp.siteRules) siteRules = resp.siteRules;
    });
  } catch (e) { /* the SW may be asleep; generic extraction still works */ }

  installListeners(document, function (payload) {
    try {
      chrome.runtime.sendMessage({ type: "dlr:capture", payload: payload });
    } catch (e) { /* SW restarting */ }
  }, function () { return siteRules; });
}());
