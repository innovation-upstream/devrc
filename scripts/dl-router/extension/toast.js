// toast.js -- the auto-file confirmation popup (decision D3).
//
// Shows WHERE the download went, WHY (the matcher's reason, so a wrong match is
// diagnosable rather than mysterious), any duplicate warning, and a `change`
// button that opens the picker. Auto-closes after ~8s.
//
// It is a popup WINDOW rather than an in-page overlay because injection fails
// exactly where downloads often start: the PDF viewer, chrome:// pages and
// sandboxed frames. The service worker falls back to chrome.notifications when
// even window creation fails.

export const DEFAULT_TOAST_MS = 8000;

/** Parse the popup's query string into a render model. */
export function parseParams(search) {
  const p = new URLSearchParams(search || "");
  const ms = Number(p.get("ms"));
  return {
    downloadId: p.get("id") ? Number(p.get("id")) : null,
    dir: p.get("dir") || "",
    reason: p.get("reason") || "",
    dup: p.get("dup") || "",
    source: p.get("source") || "",
    ms: Number.isFinite(ms) && ms > 0 ? ms : DEFAULT_TOAST_MS,
  };
}

/** A short badge explaining which decision path answered. */
export function sourceLabel(source) {
  switch (source) {
    case "sidecar": return "matched";
    case "cache":
    case "cache-timeout": return "cached (sidecar slow)";
    case "other":
    case "other-timeout": return "no match";
    default: return source || "";
  }
}

/** Fill the toast DOM. Pure w.r.t. chrome.* -- takes the document. */
export function render(doc, model) {
  doc.getElementById("dir").textContent = model.dir;
  doc.getElementById("reason").textContent = model.reason;
  const badge = doc.getElementById("badge");
  badge.textContent = sourceLabel(model.source);
  const dup = doc.getElementById("dup");
  if (model.dup) {
    dup.textContent = model.dup;
    dup.hidden = false;
  } else {
    dup.hidden = true;
  }
  return model;
}

export function mount(doc, chromeApi, win) {
  const model = parseParams(doc.location.search);
  render(doc, model);
  doc.getElementById("change").addEventListener("click", () => {
    void chromeApi.runtime.sendMessage({
      type: "dlr:repick",
      downloadId: model.downloadId,
    });
    win.close();
  });
  const timer = win.setTimeout(() => win.close(), model.ms);
  doc.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { win.clearTimeout(timer); win.close(); }
  });
  return model;
}

if (typeof document !== "undefined" && typeof chrome !== "undefined"
    && !globalThis.DL_ROUTER_NO_AUTOSTART) {
  mount(document, chrome, window);
}
