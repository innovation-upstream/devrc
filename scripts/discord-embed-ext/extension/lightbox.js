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
    "@media (prefers-reduced-motion: reduce) { .media-container img, .media-container video { transition: none; } }",
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

  function findMessageContainer(el) {
    var node = el;
    for (var i = 0; i < 15; i++) {
      node = node.parentElement;
      if (!node) return el;
      var tag = (node.tagName || "").toLowerCase();
      if (tag === "div" && node.getAttribute && node.getAttribute("class") &&
          node.getAttribute("class").indexOf("message") !== -1) return node;
    }
    return el.parentElement || el;
  }

  function cloneMediaInto(mediaEl, container, doc) {
    var tag = (mediaEl.tagName || "").toLowerCase();
    var clone;
    if (tag === "video") {
      clone = doc.createElement("video");
      clone.setAttribute("controls", "");
      var src = mediaEl.getAttribute("src");
      if (src) clone.setAttribute("src", src);
      var children = mediaEl.children || [];
      for (var i = 0; i < children.length; i++) {
        var child = children[i];
        if (child.tagName && child.tagName.toLowerCase() === "source") {
          var s = doc.createElement("source");
          var ssrc = child.getAttribute("src");
          if (ssrc) s.setAttribute("src", ssrc);
          clone.appendChild(s);
        }
      }
    } else {
      clone = doc.createElement("img");
      var imgSrc = mediaEl.getAttribute("src");
      if (imgSrc) clone.setAttribute("src", imgSrc);
    }
    clone.style.maxWidth = "100%";
    clone.style.maxHeight = "95vh";
    clone.style.objectFit = "contain";
    clone.style.cursor = "grab";
    clone.style.userSelect = "none";
    container.appendChild(clone);
    return clone;
  }

  function open(doc, mediaEl) {
    if (!doc || !mediaEl) return { ok: false };
    if (state) close(doc);
    state = {
      zoomLevel: 1,
      panX: 0,
      panY: 0,
      dragging: false,
      dragStartX: 0,
      dragStartY: 0,
      mediaEl: mediaEl,
      mediaClone: null,
      siblings: [],
      currentIndex: 0,
    };
    state.siblings = getMediaSiblings(mediaEl);
    state.currentIndex = state.siblings.indexOf(mediaEl);
    if (state.currentIndex === -1) state.currentIndex = 0;

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
    state.mediaClone = cloneMediaInto(mediaEl, mediaContainer, doc);

    var controls = doc.createElement("div");
    controls.className = "controls";
    var zoomIn = doc.createElement("button");
    zoomIn.textContent = "+";
    var zoomOut = doc.createElement("button");
    zoomOut.textContent = "\u2212";
    var zoomLabel = doc.createElement("span");
    zoomLabel.textContent = "100%";
    controls.appendChild(zoomOut);
    controls.appendChild(zoomLabel);
    controls.appendChild(zoomIn);

    if (state.siblings.length > 1) {
      var prevBtn = doc.createElement("button");
      prevBtn.className = "nav-arrow nav-prev";
      prevBtn.textContent = "\u25C0";
      var nextBtn = doc.createElement("button");
      nextBtn.className = "nav-arrow nav-next";
      nextBtn.textContent = "\u25B6";
      shadow.appendChild(prevBtn);
      shadow.appendChild(nextBtn);
      state.prevBtn = prevBtn;
      state.nextBtn = nextBtn;
      state.zoomLabel = zoomLabel;
      state.mediaContainer = mediaContainer;
    }

    backdrop.appendChild(mediaContainer);
    mediaContainer.appendChild(controls);
    shadow.appendChild(backdrop);

    var mountPoint = doc.body || doc.documentElement;
    if (!mountPoint) return { ok: false };
    mountPoint.appendChild(host);

    state.host = host;
    state.shadow = shadow;
    state.backdrop = backdrop;
    state.zoomLabel = zoomLabel;
    state.mediaContainer = mediaContainer;

    function onKey(e) { handleKey(doc, e); }
    function onWheel(e) { handleWheel(doc, e); }
    function onMouseDown(e) { handleMouseDown(doc, e); }
    function onMouseMove(e) { handleMouseMove(doc, e); }
    function onMouseUp(e) { handleMouseUp(doc, e); }
    state.keyHandler = onKey;
    state.wheelHandler = onWheel;
    state.mouseDownHandler = onMouseDown;
    state.mouseMoveHandler = onMouseMove;
    state.mouseUpHandler = onMouseUp;

    if (typeof doc.addEventListener === "function") {
      doc.addEventListener("keydown", onKey);
      backdrop.addEventListener("wheel", onWheel);
      backdrop.addEventListener("mousedown", onMouseDown);
      doc.addEventListener("mousemove", onMouseMove);
      doc.addEventListener("mouseup", onMouseUp);
    }

    return { ok: true };
  }

  function close(doc) {
    if (!state) return { ok: false };
    if (typeof doc.removeEventListener === "function" && state.keyHandler) {
      doc.removeEventListener("keydown", state.keyHandler);
      if (state.backdrop) {
        state.backdrop.removeEventListener("wheel", state.wheelHandler);
        state.backdrop.removeEventListener("mousedown", state.mouseDownHandler);
      }
      doc.removeEventListener("mousemove", state.mouseMoveHandler);
      doc.removeEventListener("mouseup", state.mouseUpHandler);
    }
    if (state.host && state.host.parentElement) {
      state.host.parentElement.removeChild(state.host);
    }
    state = null;
    return { ok: true };
  }

  function isOpen() {
    return state !== null;
  }

  function handleKey(doc, event) {
    if (!state) return false;
    var key = event.key || event.code;
    if (key === "Escape") {
      close(doc);
      if (typeof event.preventDefault === "function") event.preventDefault();
      return true;
    }
    if (key === "ArrowLeft") {
      navigate(-1);
      if (typeof event.preventDefault === "function") event.preventDefault();
      return true;
    }
    if (key === "ArrowRight") {
      navigate(1);
      if (typeof event.preventDefault === "function") event.preventDefault();
      return true;
    }
    if (key === "+" || key === "=") {
      setZoom(state.zoomLevel + 0.25);
      if (typeof event.preventDefault === "function") event.preventDefault();
      return true;
    }
    if (key === "-" || key === "_") {
      setZoom(state.zoomLevel - 0.25);
      if (typeof event.preventDefault === "function") event.preventDefault();
      return true;
    }
    return false;
  }

  function handleWheel(doc, event) {
    if (!state) return false;
    var delta = event.deltaY || 0;
    if (delta < 0) setZoom(state.zoomLevel + 0.25);
    else if (delta > 0) setZoom(state.zoomLevel - 0.25);
    if (typeof event.preventDefault === "function") event.preventDefault();
    return true;
  }

  function handleMouseDown(doc, event) {
    if (!state) return false;
    state.dragging = true;
    state.dragStartX = event.clientX || 0;
    state.dragStartY = event.clientY || 0;
    return false;
  }

  function handleMouseMove(doc, event) {
    if (!state || !state.dragging) return false;
    var dx = (event.clientX || 0) - state.dragStartX;
    var dy = (event.clientY || 0) - state.dragStartY;
    state.panX += dx / state.zoomLevel;
    state.panY += dy / state.zoomLevel;
    state.dragStartX = event.clientX || 0;
    state.dragStartY = event.clientY || 0;
    applyTransform();
    return true;
  }

  function handleMouseUp(doc, event) {
    if (!state) return false;
    state.dragging = false;
    return false;
  }

  function setZoom(level) {
    if (!state) return;
    state.zoomLevel = Math.max(0.5, Math.min(5, level));
    state.panX = 0;
    state.panY = 0;
    applyTransform();
    if (state.zoomLabel) {
      state.zoomLabel.textContent = Math.round(state.zoomLevel * 100) + "%";
    }
  }

  function applyTransform() {
    if (!state || !state.mediaClone) return;
    state.mediaClone.style.transform = "scale(" + state.zoomLevel + ") translate(" + state.panX + "px, " + state.panY + "px)";
  }

  function navigate(direction) {
    if (!state || state.siblings.length <= 1) return;
    state.currentIndex = (state.currentIndex + direction + state.siblings.length) % state.siblings.length;
    var newMedia = state.siblings[state.currentIndex];
    if (!state.mediaContainer) return;
    var oldClone = state.mediaClone;
    if (oldClone && oldClone.parentElement) oldClone.parentElement.removeChild(oldClone);
    state.mediaClone = cloneMediaInto(newMedia, state.mediaContainer, doc_ref());
    state.zoomLevel = 1;
    state.panX = 0;
    state.panY = 0;
  }

  function doc_ref() {
    return state && state.host && state.host.ownerDocument || (typeof document !== "undefined" ? document : null);
  }

  function forget() {
    state = null;
  }

  function installAutoStart(doc) {
    if (typeof doc.addEventListener !== "function") return;
    doc.addEventListener("click", function (e) {
      var target = e.target;
      while (target) {
        if (target.getAttribute && target.getAttribute(ATTR_ENLARGED) === "1") {
          open(doc, target);
          if (typeof e.preventDefault === "function") e.preventDefault();
          return;
        }
        target = target.parentElement;
      }
    });
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
      forget: forget,
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) {
    return;
  }

  if (typeof document !== "undefined") {
    installAutoStart(document);
  }
}());
