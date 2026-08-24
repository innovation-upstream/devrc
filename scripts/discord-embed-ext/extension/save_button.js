(function () {
  "use strict";

  var SIDECAR_URL = "http://127.0.0.1:8791";
  var CACHE_TTL_MS = 30000;
  var ATTR_ENLARGED = "data-dee-enlarged";
  var BTN_CLASS = "dee-save-btn";

  var sidecarCache = null;
  var sidecarCacheTime = 0;
  var observer = null;

  function checkSidecar() {
    var now = Date.now();
    if (sidecarCache && (now - sidecarCacheTime) < CACHE_TTL_MS) {
      return Promise.resolve(sidecarCache);
    }
    return fetch(SIDECAR_URL + "/healthz", { signal: AbortSignal.timeout(500) })
      .then(function (resp) {
        sidecarCache = { available: resp.ok };
        sidecarCacheTime = Date.now();
        return sidecarCache;
      })
      .catch(function () {
        sidecarCache = { available: false };
        sidecarCacheTime = Date.now();
        return sidecarCache;
      });
  }

  function mountSaveButton(container, mediaEl, channelId) {
    if (!container || !mediaEl) return;
    var existing = container.querySelector ? container.querySelector("." + BTN_CLASS) : null;
    if (existing) return;
    var btn = container.ownerDocument.createElement("button");
    btn.className = BTN_CLASS;
    btn.textContent = "Save";
    btn.style.position = "absolute";
    btn.style.top = "4px";
    btn.style.right = "4px";
    btn.style.background = "rgba(0,0,0,0.6)";
    btn.style.color = "#fff";
    btn.style.border = "none";
    btn.style.borderRadius = "4px";
    btn.style.padding = "2px 8px";
    btn.style.fontSize = "11px";
    btn.style.zIndex = "10";
    btn.addEventListener("click", function () {
      fetch(SIDECAR_URL + "/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: mediaEl.getAttribute("src") || "",
          channelId: channelId || "",
          pageUrl: (typeof location !== "undefined" && location.href) || "",
        }),
      })
        .then(function (resp) {
          if (resp.ok) {
            btn.textContent = "Saved!";
          } else {
            btn.textContent = "Error";
          }
          setTimeout(function () { btn.textContent = "Save"; }, 2000);
        })
        .catch(function () {
          btn.textContent = "Error";
          setTimeout(function () { btn.textContent = "Save"; }, 2000);
        });
    });
    container.appendChild(btn);
  }

  function unmountAll(doc) {
    doc = doc || (typeof document !== "undefined" ? document : null);
    if (!doc) return;
    var btns = doc.querySelectorAll ? doc.querySelectorAll("." + BTN_CLASS) : [];
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].parentElement) btns[i].parentElement.removeChild(btns[i]);
    }
  }

  function forget() {
    sidecarCache = null;
    sidecarCacheTime = 0;
    if (observer) {
      observer.disconnect();
      observer = null;
    }
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE_SAVE__ = {
      checkSidecar: checkSidecar,
      mountSaveButton: mountSaveButton,
      unmountAll: unmountAll,
      forget: forget,
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) {
    return;
  }

  if (typeof document !== "undefined") {
    checkSidecar().then(function (resp) {
      if (!resp.available) return;
      if (typeof MutationObserver === "undefined") return;
      var body = document.body || document.documentElement;
      if (!body) return;
      observer = new MutationObserver(function (mutations) {
        for (var i = 0; i < mutations.length; i++) {
          var added = mutations[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            var node = added[j];
            if (node.nodeType !== 1) continue;
            if (node.getAttribute && node.getAttribute(ATTR_ENLARGED) === "1") {
              var container = node.parentElement;
              if (container) mountSaveButton(container, node, "");
            }
            var inner = node.querySelectorAll ? node.querySelectorAll("[" + ATTR_ENLARGED + "]") : [];
            for (var k = 0; k < inner.length; k++) {
              var el = inner[k];
              var p = el.parentElement;
              if (p) mountSaveButton(p, el, "");
            }
          }
        }
      });
      observer.observe(body, { childList: true, subtree: true });
    });
  }
}());
