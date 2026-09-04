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

  // 🔴 THE FOUR BELOW ARE A VIEWPORT CAP, NOT THE TWO DETECTION THRESHOLDS
  // ABOVE. WIDTH_THRESHOLD/HEIGHT_THRESHOLD are px numbers findContainer
  // compares an ANCESTOR'S COMPUTED cap against, to RECOGNISE Discord's 400x300
  // box so it can be removed. ENLARGED_* is the bound WE then impose on the
  // media. Same shape, opposite direction: one reads, one writes. Do not reuse,
  // extend or fold one into the other.
  //
  // 🔴 EXPRESSED IN CSS VIEWPORT UNITS, WITH NO resize LISTENER, DELIBERATELY.
  // The engine re-evaluates vw/vh on every window resize itself, so there is no
  // listener to leak, no cached px value to go stale and no rAF thrash. A
  // JS-computed px cap would need one, and would need the fake DOM to grow a
  // viewport it does not model. This is the whole design: keep it declarative.
  //
  // 🔴 UNDER 100 ON PURPOSE — NOT A ROUNDING CHOICE, AND NOT SIMPLIFIABLE.
  // `100vw` is the width of the initial containing block, which INCLUDES the
  // gutter a classic vertical scrollbar sits in, so a `100vw` element overflows
  // horizontally and grows a HORIZONTAL scrollbar on any page that has a classic
  // vertical one. MEASURED 2026-09-04 in the engine this actually ships against
  // (Brave/Chromium 144, headless, 1000px window, `overflow-y: scroll` document):
  // innerWidth 1000, documentElement.clientWidth 985 — a 15px gutter. A `100vw`
  // box rendered 1000.00px against 985px of usable width and pushed
  // scrollWidth past clientWidth; the 96vw box rendered 960.00px and did not.
  // The remaining margin is also the only breathing room between the media and
  // the window edge, because unclipAncestors() has just stripped every ancestor
  // clip that would otherwise have supplied one.
  var ENLARGED_MAX_VW = 96;
  // 🔴 `vh`, NOT `dvh`, AS A DECISION RATHER THAN AN OMISSION. `dvh` tracks a
  // retracting browser toolbar, so it RELAYOUTS media mid-scroll; `vh` is the
  // LARGE viewport — stable, and numerically identical to `dvh` on desktop
  // Brave, which is the only place this content script runs. Revisit only if
  // this manifest ever matches a mobile browser.
  // ⚠ `vh` IS THE WINDOW, NOT THE VISIBLE MESSAGE AREA. Discord insets the
  // scroller by a channel header and a composer, so a 92vh image is still
  // taller than what you can see and you will scroll to its bottom. That is a
  // large improvement on `max-height: none` (2000px in a 640px viewport,
  // measured) but it is NOT "now fits on screen", and the README must not say
  // so. MEASURED on a synthetic Discord-shaped layout, 1200x800 with a 48px
  // header and a 68px composer: scroller visible height 684px, a 1000x3000
  // image capped to 736px. NOT measured against the live client. If fitting
  // matters more than size, this constant is the single lever — ~85 rather
  // than 92.
  var ENLARGED_MAX_VH = 92;
  // 🔴 BOTH HALVES, OR THE MEDIA ESCAPES SIDEWAYS. `100%` alone was the bug: it
  // resolves against the container, whose own max-width applyOverride sets to
  // `none` just above the write site, so the effective horizontal bound became an
  // ancestor chain that need not be viewport-bounded. But `96vw` alone is a
  // DIFFERENT bug — Discord's message column is much narrower than the window,
  // so a viewport-only cap lets media spill out of the column and across the
  // sidebar. min() takes whichever is smaller: the column when the column is
  // narrow, the viewport when it is not. MEASURED in Brave/Chromium 144 at a
  // 1000px window: computed max-width resolves to `min(100%, 960px)`, and the
  // SAME declaration rendered 400.00px inside a 400px column and 960.00px inside
  // a 2000px one — both halves demonstrably load-bearing, at two points.
  //
  // Built by concatenation so the number appears exactly ONCE, above. A second
  // hardcoded `96vw`/`92vh` literal anywhere in this file is what lets the
  // constant and its use drift apart, and the suite forbids it.
  var ENLARGED_MAX_WIDTH = "min(100%, " + ENLARGED_MAX_VW + "vw)";
  var ENLARGED_MAX_HEIGHT = ENLARGED_MAX_VH + "vh";

  var DEBOUNCE_MS = 100;
  var ATTR_ENLARGED = "data-dee-enlarged";
  // Holds the ancestor's PRIOR inline overflow so forget() can put it back —
  // see unclipAncestors. Its presence is also the once-only guard.
  var ATTR_UNCLIPPED = "data-dee-unclipped";
  // How far up to look for the row that bounds one message. Discord nests a
  // message body ~6-10 levels under its row; 15 is slack, not a measurement.
  var MESSAGE_WALK_DEPTH = 15;

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
  // NEGATIVE IS NOT A CAP. CSS forbids a negative max-width/max-height and
  // clamps a calc() result to >= 0, so `-10px` is not a value a real cap can
  // hold — and accepting it made findContainer LATCH on such an ancestor and
  // write !important overrides onto it. An earlier README claimed the negative
  // branch was deliberate; it was unreachable, unpinned, and wrong.
  function cssPx(v) {
    if (typeof v !== "string") return NaN;
    var m = /^\s*([0-9]*\.?[0-9]+)px\s*$/.exec(v);
    return m ? parseFloat(m[1]) : NaN;
  }

  // ONE resolver for the computed-style source. Both walks below need it and
  // both must honour the test seam, so it is not open-coded twice.
  function computedStyleFn() {
    return (typeof globalThis !== "undefined" && globalThis.__DEE_GET_COMPUTED_STYLE__) ||
           (typeof getComputedStyle !== "undefined" ? getComputedStyle : null);
  }

  // The row that bounds ONE message. Discord ships hashed class names
  // (`message__74e4d`), so this matches on the substring, and on the row id
  // (`chat-messages-<channel>-<message>`) that the real client puts on the <li>.
  // Neither is a fixed class name, which is what keeps this from rotting on a
  // Discord CSS reshuffle.
  //
  // 🔴 LIVES HERE, NOT IN lightbox.js. Two callers need this boundary — the
  // lightbox's sibling collection and unclipAncestors below — and the lightbox
  // is already the consumer of this module's exports. A second copy there is
  // the exact shape the MEDIA_URL_RE header warns about: one gets fixed on a
  // Discord reshuffle and the other silently keeps the old heuristic.
  function findMessageContainer(el) {
    var node = el;
    for (var i = 0; i < MESSAGE_WALK_DEPTH; i++) {
      node = node.parentElement;
      if (!node) return null;
      var cls = (node.getAttribute && node.getAttribute("class")) || "";
      if (cls.indexOf("message") !== -1) return node;
      var id = (node.getAttribute && node.getAttribute("id")) || "";
      if (id.indexOf("chat-messages-") === 0) return node;
    }
    return null;
  }

  // 🔴 ONLY `hidden` AND `clip` ARE A CLIP WE MAY REMOVE. `auto` and `scroll`
  // are SCROLL CONTAINERS: forcing those to `visible` is how a previous attempt
  // (v0.2.1-0.2.3) broke Discord's message scroller while chasing this same
  // bug. `visible` needs no clearing, and an unset/garbage value is not a clip.
  function clipsOverflow(cs) {
    if (!cs) return false;
    var get = cs.getPropertyValue
      ? function (p) { return cs.getPropertyValue(p) || ""; }
      : function () { return ""; };
    var vals = [get("overflow-x"), get("overflow-y")];
    for (var i = 0; i < vals.length; i++) {
      var v = String(vals[i]).trim().toLowerCase();
      if (v === "hidden" || v === "clip") return true;
    }
    return false;
  }

  // 🔴 THE ENLARGEMENT'S OTHER HALF. findContainer uncaps the first ancestor
  // with a px SIZE cap; an ancestor can also clip with `overflow: hidden` and
  // NO px cap at all, and that one is what actually cut the enlarged media off.
  // Both are needed: uncapping without unclipping grows the image inside a box
  // that still crops it, which is the bug this extension shipped for four
  // versions.
  //
  // 🔴 BOUNDED BY THE MESSAGE, NOT BY A DEPTH GUESS. Stopping at (and
  // including) the message row is what keeps this off Discord's chrome — the
  // scroller lives ABOVE the row, so it is unreachable by construction rather
  // than by hoping a depth number lands right. MAX_WALK_DEPTH remains as a
  // backstop for the case findMessageContainer finds nothing (a fixture, or a
  // Discord reshuffle that rots the heuristic), so a null boundary degrades to
  // the old bounded walk instead of running to <html>.
  //
  // 🔴 BOTH AXES, ALWAYS. CSS forces a `visible` axis to `auto` when the other
  // axis is `hidden`, so clearing one axis alone cannot uncrop anything. The
  // shorthand is the only write that works.
  function unclipAncestors(el) {
    var getCS = computedStyleFn();
    if (!getCS) return 0;
    var boundary = findMessageContainer(el);
    // 🔴 ALL READS, THEN ALL WRITES. Interleaving getComputedStyle with a
    // style write forces a layout recalc per ancestor — up to 8 per media
    // element, on a page that can hold dozens. Splitting the phases is safe
    // because overflow is not inherited and a descendant's overflow cannot
    // change an ancestor's computed value, so what the second phase writes
    // could not have altered what the first phase read.
    var targets = [];
    var node = el;
    for (var depth = 0; depth < MAX_WALK_DEPTH; depth++) {
      node = node.parentElement;
      if (!node || node.nodeType !== 1) break;
      // Once-only. Without this the SECOND pass would record our own
      // "visible" as the element's prior value and forget() would restore the
      // override instead of undoing it.
      if ((!node.getAttribute || !node.getAttribute(ATTR_UNCLIPPED)) &&
          node.style && clipsOverflow(getCS(node))) {
        targets.push(node);
      }
      if (boundary && node === boundary) break;
    }
    for (var i = 0; i < targets.length; i++) {
      var t = targets[i];
      var prior = t.style.getPropertyValue
        ? t.style.getPropertyValue("overflow") || "" : "";
      var priority = t.style.getPropertyPriority
        ? t.style.getPropertyPriority("overflow") || "" : "";
      // `;` cannot appear inside a single declaration's value, so it is a
      // safe delimiter for the (value, priority) pair we must restore.
      if (t.setAttribute) t.setAttribute(ATTR_UNCLIPPED, prior + ";" + priority);
      t.style.setProperty("overflow", "visible", "important");
    }
    return targets.length;
  }

  // The exact inverse of the write above. #804's forget() cleared the marker
  // attribute and left the inline overrides in place, which its own header
  // comment flagged as "there is no undo"; restoring the recorded prior value
  // is what closes that.
  function reclipAncestors(doc) {
    var els = doc.querySelectorAll("[" + ATTR_UNCLIPPED + "]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var raw = el.getAttribute(ATTR_UNCLIPPED) || ";";
      var cut = raw.lastIndexOf(";");
      var value = cut === -1 ? "" : raw.slice(0, cut);
      var priority = cut === -1 ? "" : raw.slice(cut + 1);
      if (el.style) {
        if (value) el.style.setProperty("overflow", value, priority);
        else if (el.style.removeProperty) el.style.removeProperty("overflow");
      }
      el.removeAttribute(ATTR_UNCLIPPED);
    }
    return els.length;
  }

  function findContainer(el) {
    var node = el;
    var getCS = computedStyleFn();
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
    // Unclip BEFORE the size overrides below. Order is not cosmetic: the walk
    // reads each ancestor's COMPUTED overflow, and uncapping a container first
    // can let content reflow and change what the browser reports.
    var unclipped = unclipAncestors(el);
    var removed = false;
    if (container) {
      // 🔴 THE CONTAINER IS UNCAPPED AND STAYS UNCAPPED — the viewport cap goes
      // on the ELEMENT only, and that is a decision, not an oversight. With
      // width/height forced to `auto` the container is sized by its content, so
      // once the media inside is bounded the container is bounded with it and
      // no empty box is left behind; a second cap here would be redundant at
      // best. At worst it is harmful: this container is Discord's own layout
      // box, and re-imposing a max-height on a flex parent can squeeze the
      // media the uncapping just freed. MEASURED, not reasoned: rendering this
      // exact four-declaration set on a wrapper around a 92vh-capped 200x2000
      // image in Brave/Chromium 144 gave wrapper height 588.80px against image
      // height 588.80px — the container tracks the media exactly, no open box —
      // and 588.80 <= the 640px viewport. There is nothing here for a container
      // cap to fix.
      //
      // (An earlier version of this comment said 592.80 against 588.80 and
      // called the 4px an inline baseline gap. That figure does not reproduce:
      // re-measured at 0px in three line-height contexts. The decision it
      // supports is unchanged and in fact stronger.)
      var ccs = container.style;
      ccs.setProperty("max-width", "none", "important");
      ccs.setProperty("max-height", "none", "important");
      ccs.setProperty("width", "auto", "important");
      ccs.setProperty("height", "auto", "important");
      removed = true;
    }
    // 🔴 THE VIEWPORT CAP. `max-height: none` here was the vertical overflow by
    // construction: any tall image ran off the bottom of the window, resize or
    // no resize. `max-width: 100%` was the horizontal half — see the
    // ENLARGED_MAX_WIDTH header. MEASURED: a 200x2000 image under these
    // declarations rendered 58.88 x 588.80 in Brave/Chromium 144 — the 1:10
    // ratio intact — where the shipped `max-height: none` rendered it 2000px
    // tall against a 640px viewport.
    //
    // 🔴 `object-fit: contain` IS NOT WHAT PRESERVES THAT RATIO — do not cite
    // it as the reason, and do not delete it as useless either. In the ordinary
    // BLOCK case the box ratio already equals the intrinsic ratio (58.88 x 10 =
    // 588.80), so the replaced-element auto/max-constraint algorithm does the
    // work: MEASURED identical at 58.88 x 588.80 with `contain`, with `fill`,
    // and with the declaration deleted outright. It becomes load-bearing where
    // the box ratio DIFFERS from the content's — same engine, same image,
    // inside `display:flex; flex-direction:column` the box is 614.39 x 588.80,
    // and there `fill` squashes while `contain` letterboxes. Kept for that
    // case; no behavioural test in this suite can observe it, so only the
    // structural declaration-set assertion holds the line.
    el.style.setProperty("max-width", ENLARGED_MAX_WIDTH, "important");
    el.style.setProperty("max-height", ENLARGED_MAX_HEIGHT, "important");
    el.style.setProperty("width", "auto", "important");
    el.style.setProperty("height", "auto", "important");
    el.style.setProperty("object-fit", "contain", "important");
    el.style.setProperty("cursor", "zoom-in", "important");
    if (el.setAttribute) {
      el.setAttribute(ATTR_ENLARGED, "1");
    }
    return { ok: true, removed: removed, unclipped: unclipped };
  }

  // 🔴 SCOPED TO `root`. This used to do `root.ownerDocument || root` and then
  // query THAT, i.e. every call was a full-document rescan and the parameter was
  // dead — `var doc = root;` was a surviving mutant. It also masked the observer
  // dropping batches below, since a whole-document sweep re-found everything.
  // A root that IS the media element is handled explicitly: querySelectorAll
  // never matches the element it is called on.
  function scan(root) {
    var scope;
    if (root === undefined || root === null) {
      scope = (typeof document !== "undefined") ? document : null;
    } else if (root.querySelectorAll) {
      scope = root;
    } else {
      // 🔴 A NODE WE CANNOT QUERY IS NOT AN INVITATION TO SWEEP THE PAGE.
      // Falling back to `document` for a BAD root turned any non-element that
      // reached here — Discord inserts text nodes constantly — into a
      // whole-document rescan, which is the exact cost the scoped scan exists to
      // avoid. The observer's `nodeType === 1` filter is now defence in depth
      // rather than the only thing standing between a text node and a full sweep.
      return 0;
    }
    if (!scope) return 0;
    var count = 0;
    var all = [];
    var rootTag = (root && root.tagName) ? root.tagName.toLowerCase() : "";
    // `source` included: the observer hands us the <source> element when ITS
    // src is set after insertion, and isMediaElement() resolves it to the
    // parent <video>. Without this the observation cost is paid and the
    // result thrown away — the comment below claimed the case was closed
    // while scan() still dropped it on the floor.
    if (rootTag === "img" || rootTag === "video" || rootTag === "source") all.push(root);
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
    // Ancestors are marked on a DIFFERENT attribute and are NOT in the set
    // above — forgetting the media elements alone would leave every cleared
    // container with `overflow: visible !important` welded on.
    reclipAncestors(doc);
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
      ENLARGED_MAX_VW: ENLARGED_MAX_VW,
      ENLARGED_MAX_VH: ENLARGED_MAX_VH,
      ENLARGED_MAX_WIDTH: ENLARGED_MAX_WIDTH,
      ENLARGED_MAX_HEIGHT: ENLARGED_MAX_HEIGHT,
      isMediaElement: isMediaElement,
      findContainer: findContainer,
      findMessageContainer: findMessageContainer,
      clipsOverflow: clipsOverflow,
      unclipAncestors: unclipAncestors,
      reclipAncestors: reclipAncestors,
      applyOverride: applyOverride,
      scan: scan,
      cssPx: cssPx,
      forget: forget,
      observe: observe,
    };
  }

  // The production entry point, as a NAMED function so it can be pinned. As a
  // bare pair of statements at module scope, deleting either one left the whole
  // suite green: everything already rendered at document_idle would silently go
  // un-enlarged, or nothing rendered later would ever be seen.
  function autoStart(doc) {
    doc = doc || (typeof document !== "undefined" ? document : null);
    if (!doc) return false;
    scan(doc);
    observe(doc);
    return true;
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE__.autoStart = autoStart;
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) {
    return;
  }

  autoStart();
}());
