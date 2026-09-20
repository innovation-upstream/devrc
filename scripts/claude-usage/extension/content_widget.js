// content_widget.js -- the in-page usage widget (classic content script).
//
// Shows the active account's usage on every claude.ai page load, so the
// numbers are visible without opening the popup. Data comes from
// chrome.storage.local, which the service worker writes after content_probe.js
// reports; this file NEVER fetches and never normalizes. If the probe has not
// run yet, it renders a waiting state and fills in when storage changes.
//
// 🔴 THIS RUNS INSIDE THE OPERATOR'S REAL CLAUDE.AI TAB. Three rules follow:
//
//   * SHADOW DOM, always. claude.ai's stylesheet must not reach our nodes and
//     ours must not reach theirs. Note that CSS isolation is not total --
//     INHERITED properties (font, colour, line-height) cross a shadow
//     boundary -- so the container sets those explicitly rather than assuming.
//
//   * NEVER THROW into the page. Every entry point is wrapped; a widget that
//     fails must disappear quietly, not surface an error in the operator's
//     chat. `import()` and every chrome.* call can reject outright after an
//     extension reload ("context invalidated"), which is a normal event here,
//     not an exception worth propagating.
//
//   * ATTACH TO documentElement, not body. claude.ai is a SPA; React owns
//     <body> and re-renders subtrees, which would take our host with it.
//     <html> is not React's to manage. The render path re-appends anyway if
//     the host has been detached, so a future change to that assumption
//     degrades to a re-mount rather than a vanished widget.
//
// Countdowns re-render on a 30s tick (computed from the raw stored resetsAt,
// never persisted pre-rendered), so a tab left open overnight still shows a
// live number rather than a frozen one.

(function () {
  "use strict";

  if (typeof document === "undefined") return;       // node: nothing to mount

  var TICK_MS = 30 * 1000;
  var mod = null;          // lib/widget.js, loaded once
  var shadow = null;
  var hostEl = null;
  var timer = null;

  // --- styles ------------------------------------------------------------------ //
  // Declared BEFORE its first use. An earlier draft put this at the bottom of
  // the IIFE, where it worked only because `var` hoists and the sole reader
  // ran in a later microtask -- correct by accident, and silently broken by
  // any change that made the first render synchronous.
  //
  // Inherited properties cross the shadow boundary, so .root restates font and
  // colour rather than inheriting claude.ai's. Light/dark follows the OS via
  // prefers-color-scheme: claude.ai's in-app theme toggle is not readable from
  // here without coupling to their DOM, and the two agree for anyone who has
  // not deliberately desynced them.
  var CSS = [
    ".root{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;",
    "font-size:12px;line-height:1.4;color:#1f2328;-webkit-font-smoothing:antialiased}",

    ".card{width:232px;background:#fffefb;border:1px solid rgba(0,0,0,.12);",
    "border-radius:12px;padding:10px 12px 8px;box-shadow:0 4px 16px rgba(0,0,0,.13)}",
    ".card.stale{opacity:.72}",

    ".head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}",
    ".name{font-weight:600;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
    ".collapse{all:unset;cursor:pointer;width:18px;height:18px;border-radius:5px;",
    "display:grid;place-items:center;font-size:14px;line-height:1;color:#6b7280;flex:0 0 auto}",
    ".collapse:hover{background:rgba(0,0,0,.07)}",

    ".row{margin-bottom:8px}",
    ".rowtop{display:flex;align-items:baseline;justify-content:space-between;gap:8px}",
    ".label{color:#6b7280}",
    ".value{font-weight:600;font-variant-numeric:tabular-nums}",
    ".track{height:5px;border-radius:3px;background:rgba(0,0,0,.09);overflow:hidden;margin-top:4px}",
    ".fill{height:100%;border-radius:3px;transition:width .3s ease}",
    ".meta{color:#8a9099;font-size:11px;margin-top:3px}",

    ".note{color:#6b7280;padding:2px 0 6px}",
    ".locked{margin:6px 0 2px;padding:5px 7px;border-radius:7px;font-size:11px;",
    "background:rgba(217,48,37,.10);color:#b3261e}",
    ".credits{margin-top:4px;color:#6b7280;font-size:11px}",
    ".foot{margin-top:6px;color:#9aa0a6;font-size:11px}",

    ".pill{all:unset;cursor:pointer;display:flex;align-items:center;gap:6px;",
    "background:#fffefb;border:1px solid rgba(0,0,0,.12);border-radius:999px;",
    "padding:5px 10px 5px 8px;box-shadow:0 3px 10px rgba(0,0,0,.13);",
    "font-family:inherit;font-size:12px;font-weight:600;color:#1f2328;font-variant-numeric:tabular-nums}",
    ".pill:hover{background:#fff}",
    ".dot{width:8px;height:8px;border-radius:50%;flex:0 0 auto}",

    // Tones match the service worker's badge palette exactly, so the toolbar
    // badge and the in-page widget can never disagree about severity.
    ".t-ok{background:#31a73c}",
    ".t-warn{background:#f9ab00}",
    ".t-crit{background:#d93025}",
    ".t-stale{background:#80868b}",
    ".t-unknown{background:#f9ab00}",
    ".pill.t-ok,.pill.t-warn,.pill.t-crit,.pill.t-stale,.pill.t-unknown{background:#fffefb}",

    "@media (prefers-color-scheme: dark){",
    ".root{color:#e8eaed}",
    ".card,.pill{background:#26262b;border-color:rgba(255,255,255,.14);",
    "box-shadow:0 4px 16px rgba(0,0,0,.45)}",
    ".pill{color:#e8eaed}",
    ".pill:hover{background:#2f2f35}",
    ".label,.note,.credits{color:#9aa0a6}",
    ".meta,.foot{color:#7e848c}",
    ".track{background:rgba(255,255,255,.13)}",
    ".collapse{color:#9aa0a6}",
    ".collapse:hover{background:rgba(255,255,255,.10)}",
    ".locked{background:rgba(217,48,37,.20);color:#f2b8b5}",
    ".pill.t-ok,.pill.t-warn,.pill.t-crit,.pill.t-stale,.pill.t-unknown{background:#26262b}",
    "}",
  ].join("");

  function ext() {
    // chrome.runtime goes away when the extension is reloaded/updated while a
    // page is open. Callers treat null as "stop quietly".
    try {
      return (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.id)
        ? chrome : null;
    } catch (e) { return null; }
  }

  // --- mount ----------------------------------------------------------------- //

  function ensureHost(id) {
    if (hostEl && hostEl.isConnected) return hostEl;
    var existing = document.getElementById(id);
    if (existing) { hostEl = existing; return hostEl; }
    hostEl = document.createElement("div");
    hostEl.id = id;
    // Inline, because the page's stylesheet cannot be trusted to leave a bare
    // div alone. `all: initial` would also reset position, so the layout
    // properties are set after it deliberately.
    hostEl.style.cssText = [
      "all: initial",
      "position: fixed",
      "right: 16px",
      "bottom: 16px",
      // Just under the 32-bit max: above claude.ai's overlays, still leaving
      // room for anything that deliberately wants to sit on top.
      "z-index: 2147483000",
      "pointer-events: auto",
    ].join(";");
    document.documentElement.appendChild(hostEl);
    shadow = null;                                   // new host needs a new root
    return hostEl;
  }

  function ensureShadow(id) {
    var host = ensureHost(id);
    if (shadow && shadow.host === host) return shadow;
    shadow = host.shadowRoot || host.attachShadow({ mode: "open" });
    shadow.textContent = "";
    var style = document.createElement("style");
    style.textContent = CSS;
    shadow.appendChild(style);
    var root = document.createElement("div");
    root.className = "root";
    shadow.appendChild(root);
    return shadow;
  }

  // --- paint ----------------------------------------------------------------- //

  function bar(pct, tone) {
    var track = document.createElement("div");
    track.className = "track";
    var fill = document.createElement("div");
    fill.className = "fill t-" + tone;
    // null (unknown) renders an empty track rather than a full or zero bar:
    // "we don't know" must not look like "you've used none of it".
    fill.style.width = (pct === null ? 0 : pct) + "%";
    track.appendChild(fill);
    return track;
  }

  function paintCollapsed(root, model) {
    var pill = document.createElement("button");
    pill.className = "pill t-" + model.tone;
    pill.type = "button";
    pill.title = model.name + " — click to expand";
    var dot = document.createElement("span");
    dot.className = "dot t-" + model.tone;
    var txt = document.createElement("span");
    txt.className = "pilltext";
    txt.textContent = model.pill;
    pill.append(dot, txt);
    pill.addEventListener("click", function () { setCollapsed(false); });
    root.appendChild(pill);
  }

  function paintExpanded(root, model) {
    var card = document.createElement("div");
    card.className = "card" + (model.stale ? " stale" : "");

    var head = document.createElement("div");
    head.className = "head";
    var name = document.createElement("span");
    name.className = "name";
    name.textContent = model.name;
    var collapse = document.createElement("button");
    collapse.className = "collapse";
    collapse.type = "button";
    collapse.title = "Collapse";
    collapse.setAttribute("aria-label", "Collapse");
    collapse.textContent = "–";
    collapse.addEventListener("click", function () { setCollapsed(true); });
    head.append(name, collapse);
    card.appendChild(head);

    if (model.empty) {
      var note = document.createElement("div");
      note.className = "note";
      note.textContent = model.note;
      card.appendChild(note);
    } else {
      model.rows.forEach(function (row) {
        var r = document.createElement("div");
        r.className = "row";
        var top = document.createElement("div");
        top.className = "rowtop";
        var lab = document.createElement("span");
        lab.className = "label";
        lab.textContent = row.label;
        var val = document.createElement("span");
        val.className = "value";
        val.textContent = row.value;
        top.append(lab, val);
        r.append(top, bar(row.bar, model.tone));
        if (row.meta) {
          var meta = document.createElement("div");
          meta.className = "meta";
          meta.textContent = row.meta;
          r.appendChild(meta);
        }
        card.appendChild(r);
      });

      if (model.locked) {
        var lock = document.createElement("div");
        lock.className = "locked";
        lock.textContent = model.locked;
        card.appendChild(lock);
      }
      if (model.credits) {
        var cr = document.createElement("div");
        cr.className = "credits";
        cr.textContent = model.credits;
        card.appendChild(cr);
      }
      var foot = document.createElement("div");
      foot.className = "foot";
      foot.textContent = model.asOf;
      card.appendChild(foot);
    }
    root.appendChild(card);
  }

  // --- state ----------------------------------------------------------------- //

  function setCollapsed(v) {
    var c = ext();
    if (!c) return;
    try {
      var p = {};
      p[mod.COLLAPSE_KEY] = Boolean(v);
      var setting = c.storage.local.set(p);
      if (setting && typeof setting.catch === "function") setting.catch(function () {});
    } catch (e) { /* storage gone; the onChanged re-render simply won't fire */ }
    render();                                        // don't wait for the round-trip
  }

  function render() {
    var c = ext();
    if (!c || !mod) return;
    var getting;
    try {
      getting = c.storage.local.get(["accounts", "lastActiveOrg", mod.COLLAPSE_KEY]);
    } catch (e) { return; }
    if (!getting || typeof getting.then !== "function") return;
    getting.then(function (got) {
      var now = Date.now();
      var rec = mod.pickRecord(got.accounts, got.lastActiveOrg);
      var model = mod.widgetModel(rec, now);
      var collapsed = got[mod.COLLAPSE_KEY] === true;
      var sh = ensureShadow(mod.WIDGET_HOST_ID);
      var root = sh.querySelector(".root");
      if (!root) return;
      root.textContent = "";
      if (collapsed) paintCollapsed(root, model);
      else paintExpanded(root, model);
    }).catch(function () { /* storage unreadable; leave whatever is on screen */ });
  }

  // --- boot ------------------------------------------------------------------- //

  function boot() {
    var c = ext();
    if (!c) return;
    var url;
    try { url = c.runtime.getURL("lib/widget.js"); } catch (e) { return; }
    import(url).then(function (m) {
      mod = m;
      render();
      try {
        c.storage.onChanged.addListener(function (changes, area) {
          if (area !== "local") return;
          if (changes.accounts || changes.lastActiveOrg || changes[mod.COLLAPSE_KEY]) render();
        });
      } catch (e) { /* no live updates; the tick still refreshes */ }
      if (timer === null) timer = setInterval(render, TICK_MS);
    }).catch(function () { /* module unavailable (reload mid-load): no widget */ });
  }

  boot();
})();
