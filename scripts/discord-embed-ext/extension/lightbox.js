(function () {
  "use strict";

  var HOST_ID = "dee-lightbox-host";
  var ATTR_ENLARGED = "data-dee-enlarged";
  // ONE definition, in embed_enlarge.js, which the manifest loads first. This used
  // to be a second copy of the same literal — and when the host-only pattern turned
  // out to match avatars, that is exactly the shape that gets fixed at one site and
  // left wrong at the other.
  var MEDIA_URL_RE = (typeof globalThis !== "undefined" && globalThis.__DEE__ &&
                      globalThis.__DEE__.MEDIA_URL_RE) || null;

  // How far up to look for the row that bounds one message. Discord nests a
  // message body ~6-10 levels under its row; 15 is slack, not a measurement.
  var MESSAGE_WALK_DEPTH = 15;

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

  // The row that bounds ONE message. Discord ships hashed class names
  // (`message__74e4d`), so this matches on the substring, and on the row id
  // (`chat-messages-<channel>-<message>`) that the real client puts on the <li>.
  // Neither is a fixed class name, which is what keeps this from rotting on a
  // Discord CSS reshuffle.
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

  // 🔴 SCOPED TO THE MESSAGE, DELIBERATELY. This used to walk up a fixed 10
  // parents and collect every Discord image under whatever it landed on. Two
  // failure modes, in opposite directions: on a shallow tree the walk ran off
  // the top, `node` went null and it returned a single-element list, so
  // navigation silently did nothing; on the real client 10 levels overshoots one
  // message and lands on the scroller, so the arrows paged through every image
  // on screen instead of the ones in the message you clicked.
  function getMediaSiblings(mediaEl) {
    var container = findMessageContainer(mediaEl);
    if (!container || !container.querySelectorAll || !MEDIA_URL_RE) return [mediaEl];
    var all = [];
    var els = container.querySelectorAll("img, video");
    for (var j = 0; j < els.length; j++) {
      var el = els[j];
      var src = el.getAttribute("src") || "";
      if (MEDIA_URL_RE.test(src)) { all.push(el); continue; }
      // a <video> carries its url on a child <source>
      var kids = el.children || [];
      for (var k = 0; k < kids.length; k++) {
        var kid = kids[k];
        if (kid.tagName && kid.tagName.toLowerCase() === "source" &&
            MEDIA_URL_RE.test(kid.getAttribute("src") || "")) {
          all.push(el);
          break;
        }
      }
    }
    return all.length > 0 ? all : [mediaEl];
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
      didDrag: false,
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

    state.host = host;
    state.shadow = shadow;
    state.backdrop = backdrop;
    state.zoomLabel = zoomLabel;
    state.mediaContainer = mediaContainer;

    // Every on-screen control is wired here. They used to be created, styled and
    // appended with no listener at all — visible, hoverable and completely inert.
    zoomIn.addEventListener("click", function (e) { stop(e); setZoom(state ? state.zoomLevel + 0.25 : 1); });
    zoomOut.addEventListener("click", function (e) { stop(e); setZoom(state ? state.zoomLevel - 0.25 : 1); });

    if (state.siblings.length > 1) {
      var prevBtn = doc.createElement("button");
      prevBtn.className = "nav-arrow nav-prev";
      prevBtn.textContent = "\u25C0";
      var nextBtn = doc.createElement("button");
      nextBtn.className = "nav-arrow nav-next";
      nextBtn.textContent = "\u25B6";
      prevBtn.addEventListener("click", function (e) { stop(e); navigate(-1); });
      nextBtn.addEventListener("click", function (e) { stop(e); navigate(1); });
      shadow.appendChild(prevBtn);
      shadow.appendChild(nextBtn);
      state.prevBtn = prevBtn;
      state.nextBtn = nextBtn;
    }

    backdrop.appendChild(mediaContainer);
    mediaContainer.appendChild(controls);
    shadow.appendChild(backdrop);

    var mountPoint = doc.body || doc.documentElement;
    if (!mountPoint) return { ok: false };
    mountPoint.appendChild(host);

    function onKey(e) { handleKey(doc, e); }
    function onWheel(e) { handleWheel(doc, e); }
    function onMouseDown(e) { handleMouseDown(doc, e); }
    function onMouseMove(e) { handleMouseMove(doc, e); }
    function onMouseUp(e) { handleMouseUp(doc, e); }
    function onBackdropClick(e) { handleBackdropClick(doc, e); }
    state.keyHandler = onKey;
    state.wheelHandler = onWheel;
    state.mouseDownHandler = onMouseDown;
    state.mouseMoveHandler = onMouseMove;
    state.mouseUpHandler = onMouseUp;
    state.backdropClickHandler = onBackdropClick;

    if (typeof doc.addEventListener === "function") {
      doc.addEventListener("keydown", onKey);
      doc.addEventListener("mousemove", onMouseMove);
      doc.addEventListener("mouseup", onMouseUp);
    }
    if (typeof backdrop.addEventListener === "function") {
      backdrop.addEventListener("wheel", onWheel);
      backdrop.addEventListener("mousedown", onMouseDown);
      backdrop.addEventListener("click", onBackdropClick);
    }

    return { ok: true };
  }

  function stop(e) {
    if (e && typeof e.stopPropagation === "function") e.stopPropagation();
  }

  function close(doc) {
    if (!state) return { ok: false };
    if (doc && typeof doc.removeEventListener === "function" && state.keyHandler) {
      doc.removeEventListener("keydown", state.keyHandler);
      doc.removeEventListener("mousemove", state.mouseMoveHandler);
      doc.removeEventListener("mouseup", state.mouseUpHandler);
    }
    if (state.backdrop && typeof state.backdrop.removeEventListener === "function") {
      state.backdrop.removeEventListener("wheel", state.wheelHandler);
      state.backdrop.removeEventListener("mousedown", state.mouseDownHandler);
      state.backdrop.removeEventListener("click", state.backdropClickHandler);
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
    state.didDrag = false;
    state.dragStartX = event.clientX || 0;
    state.dragStartY = event.clientY || 0;
    return false;
  }

  function handleMouseMove(doc, event) {
    if (!state || !state.dragging) return false;
    var dx = (event.clientX || 0) - state.dragStartX;
    var dy = (event.clientY || 0) - state.dragStartY;
    if (dx !== 0 || dy !== 0) state.didDrag = true;
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

  // The backdrop is styled `cursor: zoom-out`, which promises a click closes it.
  // Nothing honoured that promise before. A pan that happens to finish over the
  // backdrop must NOT close, hence the didDrag latch.
  function handleBackdropClick(doc, event) {
    if (!state) return false;
    if (state.didDrag) { state.didDrag = false; return false; }
    if (event && event.target && state.backdrop && event.target !== state.backdrop) return false;
    close(doc);
    return true;
  }

  function setZoom(level) {
    if (!state) return;
    state.zoomLevel = Math.max(0.5, Math.min(5, level));
    state.panX = 0;
    state.panY = 0;
    applyTransform();
    updateZoomLabel();
  }

  function updateZoomLabel() {
    if (state && state.zoomLabel) {
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
    // Both of these were missing: the new media rendered untransformed while the
    // label still read the PREVIOUS zoom, so the number on screen was a lie.
    applyTransform();
    updateZoomLabel();
  }

  function currentSrc() {
    if (!state || !state.mediaClone) return null;
    return state.mediaClone.getAttribute("src");
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
      // A modified click is the user asking the BROWSER for something (open in a
      // new tab, save, the context menu). Hijacking it is a regression, not a
      // feature.
      if (e && (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey ||
                (typeof e.button === "number" && e.button !== 0))) return;
      var target = e.target;
      while (target) {
        if (target.getAttribute && target.getAttribute(ATTR_ENLARGED) === "1") {
          // 🔴 <video> IS DELIBERATELY EXCLUDED. It gets the size override like
          // any other media, but its own controls occupy the element: a click on
          // play/scrub/volume would open a lightbox instead of doing what the
          // user asked. Video plays inline, enlarged.
          var tag = (target.tagName || "").toLowerCase();
          if (tag === "video") return;
          open(doc, target);
          if (typeof e.preventDefault === "function") e.preventDefault();
          // Discord binds its OWN handler for opening its native image viewer.
          // Without this, both open and the user gets two stacked lightboxes.
          if (typeof e.stopPropagation === "function") e.stopPropagation();
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
      handleBackdropClick: handleBackdropClick,
      navigate: navigate,
      setZoom: setZoom,
      currentSrc: currentSrc,
      siblingCount: function () { return state ? state.siblings.length : 0; },
      zoomLevel: function () { return state ? state.zoomLevel : null; },
      installAutoStart: installAutoStart,
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
