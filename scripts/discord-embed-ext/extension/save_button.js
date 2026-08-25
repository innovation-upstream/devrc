(function () {
  "use strict";

  var SIDECAR_URL = "http://127.0.0.1:8791";
  var CHECK_INTERVAL_MS = 30000;
  var ATTR_ENLARGED = "data-dee-enlarged";
  var BTN_CLASS = "dee-save-btn";

  var sidecarAvailable = false;
  var checkTimer = null;
  var observer = null;

  function checkSidecar() {
    var timeout = typeof AbortSignal !== "undefined" && AbortSignal.timeout
      ? AbortSignal.timeout(500) : undefined;
    var opts = { method: "GET" };
    if (timeout) opts.signal = timeout;
    return fetch(SIDECAR_URL + "/healthz", opts)
      .then(function (r) { return r.ok; })
      .catch(function () { return false; })
      .then(function (ok) {
        sidecarAvailable = ok;
        return { available: ok };
      });
  }

  function extractChannelId() {
    if (typeof location === "undefined" || !location.href) return null;
    var m = location.href.match(/discord\.com\/channels\/(?:@me|(\d+))\/(\d+)/);
    return m ? (m[1] || m[2]) : null;
  }

  function mountSaveButton(container, mediaEl, channelId, doc) {
    if (!container || !mediaEl) return;
    var existing = container.querySelector ? container.querySelector("." + BTN_CLASS) : null;
    if (existing) return;
    var d = doc || (typeof document !== "undefined" ? document : null);
    if (!d) return;
    var btn = d.createElement("button");
    btn.className = BTN_CLASS;
    btn.textContent = "Save";
    btn.setAttribute("style",
      "position:absolute;top:4px;right:4px;background:rgba(0,0,0,0.6);color:#fff;" +
      "border:none;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;z-index:10;");
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var payload = JSON.stringify({
        url: mediaEl.getAttribute("src") || "",
        channelId: channelId || extractChannelId() || "",
        pageUrl: typeof location !== "undefined" ? location.href : ""
      });
      fetch(SIDECAR_URL + "/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload
      }).then(function (r) {
        btn.textContent = r.ok ? "Saved!" : "Error";
        setTimeout(function () { btn.textContent = "Save"; }, 2000);
      }).catch(function () {
        btn.textContent = "Error";
        setTimeout(function () { btn.textContent = "Save"; }, 2000);
      });
    }, true);
    if (container.style && typeof container.style.setProperty === "function") {
      container.style.setProperty("position", "relative", "important");
    }
    container.appendChild(btn);
  }

  function unmountAll(doc) {
    var d = doc || (typeof document !== "undefined" ? document : null);
    var btns = d ? d.querySelectorAll("." + BTN_CLASS) : [];
    for (var i = 0; i < btns.length; i++) {
      if (btns[i].parentElement) btns[i].parentElement.removeChild(btns[i]);
    }
  }

  function observe(doc) {
    if (!doc || !doc.body) return;
    observer = new MutationObserver(function (mutations) {
      if (!sidecarAvailable) return;
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.getAttribute && node.getAttribute(ATTR_ENLARGED)) {
            mountSaveButton(node.parentElement || node, node, extractChannelId());
          }
          var children = node.querySelectorAll ? node.querySelectorAll("[" + ATTR_ENLARGED + "]") : [];
          for (var k = 0; k < children.length; k++) {
            mountSaveButton(children[k].parentElement || children[k], children[k], extractChannelId());
          }
        }
      }
    });
    observer.observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: [ATTR_ENLARGED] });
  }

  function forget(doc) {
    sidecarAvailable = false;
    if (checkTimer) { clearInterval(checkTimer); checkTimer = null; }
    if (observer) { observer.disconnect(); observer = null; }
    unmountAll(doc);
  }

  if (typeof globalThis !== "undefined") {
    globalThis.__DEE_SAVE__ = {
      checkSidecar: checkSidecar,
      mountSaveButton: mountSaveButton,
      unmountAll: unmountAll,
      forget: forget
    };
  }

  if (typeof globalThis !== "undefined" && globalThis.DEE_NO_AUTOSTART) return;
  if (typeof document === "undefined") return;

  checkSidecar().then(function (resp) {
    if (resp.available) observe(document);
  });
}());
