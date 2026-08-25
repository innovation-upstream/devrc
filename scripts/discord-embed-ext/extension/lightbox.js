(function () {
  "use strict";

  var HOST_ID = "dee-lightbox-host";
  var ATTR_ENLARGED = "data-dee-enlarged";
  var MEDIA_URL_RE = /^https?:\/\/(cdn\.discordapp\.com|media\.discordapp\.net)\/.*$/i;

  var SHADOW_CSS = [
    ":host { contain: layout style; position: fixed; inset: 0; z-index: 2147483647; }",
    ".backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: flex; align-items: center; justify-content: center; cursor: zoom-out; }",
    ".media-container { position: relative; max-width: 95vw; max-height: 95vh; display: flex; align-items: center; justify-content: center; }",
    ".media-container img, .media-container video { max-width: 100%; max-height: 95vh; object-fit: contain; cursor: grab; user-select: none; }",
    ".media-container img:active, .media-container video:active { cursor: grabbing; }",
    ".controls { position: absolute; bottom: 16px; left: 50%; transform: translateX(-50%); display: flex; gap: 8px; align-items: center; background: rgba(0,0,0,0.6); border-radius: 8px; padding: 6px 12px; font: 12px/1 system-ui, sans-serif; color: #fff; user-select: none; }",
    ".controls button { background: none; border: none; color: #fff; cursor: pointer; font-size: 16px; padding: 4px 8px; border-radius: 4px; }",
    ".controls button:hover { background: rgba(255,255,255,0.15); }",
    ".nav-arrow { position: absolute; top: 50%; transform: translateY(-50%); background: rgba(0,0,0,0.4); border: none; color: #fff; font-size: 32px; padding: 16px 12px; cursor: pointer; border-radius: 8px; user-select: none; z-index: 1; }",
    ".nav-arrow:hover { background: rgba(0,0,0,0.7); }",
    ".nav-prev { left: 8px; }",
    ".nav-next { right: 8px; }",
    "@media (prefers-reduced-motion: reduce) { .media-container img, .media-container video { transition: none; } }"
  ].join("\n");

  var state = null;

  function getMediaSiblings(mediaEl) {
    var node = mediaEl;
    for (var i = 0; i < 10; i++) {
      node = node.parentElement;
      if (!node) break;
    }
    if (!node) return [mediaEl];
    var all = [];
    var els = node.querySelectorAll ? node.querySelectorAll("img, video") : [];
    for (var j = 0; j < els.length; j++) {
      var el = els[j];
      var src = el.getAttribute("src") || "";
      if (MEDIA_URL_RE.test(src)) all.push(el);
    }
    return all.length > 0 ? all : [mediaEl];
  }

  function cloneMediaInto(mediaEl, container, doc) {
    var tag = (mediaEl.tagName || "").toLowerCase();
    var clone;
    if (tag === "video") {
      clone = doc.createElement("video");
      clone.setAttribute("controls", "");
      var src = mediaEl.getAttribute("src") || "";
      if (src) clone.setAttribute("src", src);
      var sources = mediaEl.querySelectorAll ? mediaEl.querySelectorAll("source") : [];
      for (var i = 0; i < sources.length; i++) {
        var s = doc.createElement("source");
        s.setAttribute("src", sources[i].getAttribute("src") || "");
        s.setAttribute("type", sources[i].getAttribute("type") || "");
        clone.appendChild(s);
      }
    } else {
      clone = doc.createElement("img");
      clone.setAttribute("src", mediaEl.getAttribute("src") || "");
      clone.setAttribute("alt", mediaEl.getAttribute("alt") || "");
    }
    clone.style.setProperty("max-width", "100%", "important");
    clone.style.setProperty("max-height", "95vh", "important");
    clone.style.setProperty("object-fit", "contain", "important");
    container.appendChild(clone);
    return clone;
  }

  function build(doc) {
    var host = doc.createElement("div");
    host.setAttribute("id", HOST_ID);
    var shadow = host.attachShadow({ mode: "closed" });
    var style = doc.createElement("style");
    style.textContent = SHADOW_CSS;
    shadow.appendChild(style);

    var backdrop = doc.createElement("div");
    backdrop.className = "backdrop";

    var mediaContainer = doc.createElement("div");
    mediaContainer.className = "media-container";

    var controls = doc.createElement("div");
    controls.className = "controls";

    var zoomIn = doc.createElement("button");
    zoomIn.textContent = "+";
    zoomIn.className = "zoom-in";

    var zoomOut = doc.createElement("button");
    zoomOut.textContent = "\u2212";
    zoomOut.className = "zoom-out";

    var reset = doc.createElement("button");
    reset.textContent = "Reset";
    reset.className = "zoom-reset";

    controls.appendChild(zoomOut);
    controls.appendChild(reset);
    controls.appendChild(zoomIn);

    var navPrev = doc.createElement("button");
    navPrev.className = "nav-arrow nav-prev";
    navPrev.textContent = "\u2039";
    navPrev.style.setProperty("display", "none", "important");

    var navNext = doc.createElement("button");
    navNext.className = "nav-arrow nav-next";
    navNext.textContent = "\u203a";
    navNext.style.setProperty("display", "none", "important");

    backdrop.appendChild(mediaContainer);
    backdrop.appendChild(controls);
    backdrop.appendChild(navPrev);
    backdrop.appendChild(navNext);
    shadow.appendChild(backdrop);
    doc.body.appendChild(host);

    return { host: host, shadow: shadow, backdrop: backdrop, mediaContainer: mediaContainer, navPrev: navPrev, navNext: navNext };
  }

  function open(doc, mediaEl) {
    if (!doc || !mediaEl) return { ok: false };
    if (state) close(doc);

    var siblings = getMediaSiblings(mediaEl);
    var idx = siblings.indexOf(mediaEl);
    if (idx === -1) idx = 0;

    var parts = build(doc);
    var displayedMedia = cloneMediaInto(mediaEl, parts.mediaContainer, doc);

    state = {
      doc: doc,
      host: parts.host,
      shadow: parts.shadow,
      backdrop: parts.backdrop,
      mediaContainer: parts.mediaContainer,
      navPrev: parts.navPrev,
      navNext: parts.navNext,
      displayedMedia: displayedMedia,
      siblings: siblings,
      index: idx,
      zoom: 1,
      panX: 0,
      panY: 0,
      dragging: false,
      dragStartX: 0,
      dragStartY: 0,
      listeners: []
    };

    if (siblings.length > 1) {
      parts.navPrev.style.setProperty("display", "", "important");
      parts.navNext.style.setProperty("display", "", "important");
    }

    function onKey(e) { return handleKey(doc, e); }
    function onWheel(e) { return handleWheel(doc, e); }
    function onMouseDown(e) { return handleMouseDown(doc, e); }
    function onMouseMove(e) { return handleMouseMove(doc, e); }
    function onMouseUp(e) { return handleMouseUp(doc, e); }
    function onBackdropClick(e) {
      if (e.target === parts.backdrop) close(doc);
    }

    doc.addEventListener("keydown", onKey, true);
    doc.addEventListener("wheel", onWheel, { capture: true, passive: false });
    doc.addEventListener("mousedown", onMouseDown, true);
    doc.addEventListener("mousemove", onMouseMove, true);
    doc.addEventListener("mouseup", onMouseUp, true);
    parts.backdrop.addEventListener("click", onBackdropClick, true);

    state.listeners = [
      ["keydown", onKey, true],
      ["wheel", onWheel, { capture: true, passive: false }],
      ["mousedown", onMouseDown, true],
      ["mousemove", onMouseMove, true],
      ["mouseup", onMouseUp, true]
    ];
    state.backdropListener = ["click", onBackdropClick, true];

    if (parts.backdrop.focus) parts.backdrop.focus();
    return { ok: true };
  }

  function close(doc) {
    if (!state) return { ok: false, error: "no_lightbox" };
    if (state.host && state.host.parentElement) state.host.parentElement.removeChild(state.host);
    if (doc && state.listeners) {
      for (var i = 0; i < state.listeners.length; i++) {
        doc.removeEventListener(state.listeners[i][0], state.listeners[i][1], state.listeners[i][2]);
      }
    }
    if (state.backdrop && state.backdropListener) {
      state.backdrop.removeEventListener(state.backdropListener[0], state.backdropListener[1], state.backdropListener[2]);
    }
    state = null;
    return { ok: true };
  }

  function isOpen() { return state !== null; }

  function applyTransform() {
    if (!state || !state.displayedMedia) return;
    state.displayedMedia.style.setProperty("transform",
      "scale(" + state.zoom + ") translate(" + state.panX + "px, " + state.panY + "px)", "important");
  }

  function navigateTo(idx) {
    if (!state || !state.siblings) return;
    var len = state.siblings.length;
    state.index = ((idx % len) + len) % len;
    var mediaEl = state.siblings[state.index];
    state.mediaContainer.removeChild(state.displayedMedia);
    state.displayedMedia = cloneMediaInto(mediaEl, state.mediaContainer, state.doc);
    state.zoom = 1;
    state.panX = 0;
    state.panY = 0;
    applyTransform();
  }

  function handleKey(doc, e) {
    if (!state) return false;
    var key = e.key;
    if (key === "Escape") { close(doc); if (e.preventDefault) e.preventDefault(); return true; }
    if (key === "ArrowLeft") { navigateTo(state.index - 1); if (e.preventDefault) e.preventDefault(); return true; }
    if (key === "ArrowRight") { navigateTo(state.index + 1); if (e.preventDefault) e.preventDefault(); return true; }
    if (key === "+" || key === "=") { state.zoom = Math.min(5, state.zoom + 0.25); applyTransform(); if (e.preventDefault) e.preventDefault(); return true; }
    if (key === "-" || key === "_") { state.zoom = Math.max(0.5, state.zoom - 0.25); applyTransform(); if (e.preventDefault) e.preventDefault(); return true; }
    if (key === "0") { state.zoom = 1; state.panX = 0; state.panY = 0; applyTransform(); if (e.preventDefault) e.preventDefault(); return true; }
    return false;
  }

  function handleWheel(doc, e) {
    if (!state) return false;
    if (e.deltaY < 0) state.zoom = Math.min(5, state.zoom + 0.1);
    else state.zoom = Math.max(0.5, state.zoom - 0.1);
    applyTransform();
    if (e.preventDefault) e.preventDefault();
    return true;
  }

  function handleMouseDown(doc, e) {
    if (!state) return false;
    state.dragging = true;
    state.dragStartX = e.clientX;
    state.dragStartY = e.clientY;
    if (e.preventDefault) e.preventDefault();
    return true;
  }

  function handleMouseMove(doc, e) {
    if (!state || !state.dragging) return false;
    var dx = e.clientX - state.dragStartX;
    var dy = e.clientY - state.dragStartY;
    state.panX += dx / state.zoom;
    state.panY += dy / state.zoom;
    state.dragStartX = e.clientX;
    state.dragStartY = e.clientY;
    applyTransform();
    return true;
  }

  function handleMouseUp(doc, e) {
    if (!state) return false;
    state.dragging = false;
    return true;
  }

  function forget() {
    state = null;
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE_LIGHTBOX__ = {
      open: open,
      close: close,
      isOpen: isOpen,
      handleKey: handleKey,
      handleWheel: handleWheel,
      handleMouseDown: handleMouseDown,
      handleMouseMove: handleMouseMove,
      handleMouseUp: handleMouseUp,
      forget: forget
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) return;
  if (typeof document === "undefined") return;

  document.addEventListener("click", function (e) {
    var target = e.target;
    if (target && target.getAttribute && target.getAttribute(ATTR_ENLARGED)) {
      e.preventDefault();
      e.stopPropagation();
      open(document, target);
    }
  }, true);
}());
