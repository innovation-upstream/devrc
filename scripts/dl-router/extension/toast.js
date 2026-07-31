// toast.js -- the auto-file confirmation popup (decision D3), and the
// duplicate warning that reuses the same page.
//
// Shows WHERE the download went, WHY (the matcher's reason, so a wrong match is
// diagnosable rather than mysterious), any duplicate warning, and a `change`
// button that opens the picker. Auto-closes after ~8s.
//
// It is a popup WINDOW rather than an in-page overlay because injection fails
// exactly where downloads often start: the PDF viewer, chrome:// pages and
// sandboxed frames. The service worker falls back to chrome.notifications when
// even window creation fails.
//
// DUPLICATE MODE (`mode=dup`). The file is ALWAYS kept and filed normally --
// nothing here destroys anything on its own. The toast reports which library
// file it duplicates and offers `delete` and `keep`, and in this mode:
//
//   * the auto-close timer is NOT armed. A question that disappears on its own
//     is answered by whichever button the timer happened to favour, and here
//     one of those buttons deletes a file;
//   * a REFUSED delete keeps the window open and shows the sidecar's reason,
//     the same rule the picker learned ("a refusal must be VISIBLE") -- the
//     sidecar refuses a delete it cannot prove, and a toast that closed anyway
//     would report destruction that never happened.

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
    // Duplicate mode. BOTH paths are required: without `rel` there is nothing
    // to delete and without `dupRel` there is no proof anything else holds
    // these bytes, and the sidecar refuses either way. Offering the button
    // without them would be a button that can only fail.
    mode: p.get("mode") || "",
    relPath: p.get("rel") || "",
    dupRelPath: p.get("dupRel") || "",
  };
}

/** Is this the duplicate question, with everything a delete would need? */
export function isDuplicateMode(model) {
  return Boolean(model && model.mode === "dup" && model.relPath
    && model.dupRelPath);
}

/** A short badge explaining which decision path answered. */
export function sourceLabel(source) {
  switch (source) {
    case "sidecar": return "matched";
    case "cache":
    case "cache-timeout": return "cached (sidecar slow)";
    case "other":
    case "other-timeout": return "no match";
    case "duplicate": return "duplicate";
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
  const duplicate = isDuplicateMode(model);
  for (const id of ["keep", "discard"]) {
    const el = doc.getElementById(id);
    if (el) el.hidden = !duplicate;
  }
  const err = doc.getElementById("error");
  if (err) err.hidden = true;
  return model;
}

export function mount(doc, chromeApi, win) {
  const model = parseParams(doc.location.search);
  render(doc, model);
  const duplicate = isDuplicateMode(model);

  doc.getElementById("change").addEventListener("click", () => {
    void chromeApi.runtime.sendMessage({
      type: "dlr:repick",
      downloadId: model.downloadId,
    });
    win.close();
  });

  // NO AUTO-CLOSE ON THE DUPLICATE QUESTION -- see the header.
  const timer = duplicate ? null : win.setTimeout(() => win.close(), model.ms);
  const stop = () => { if (timer !== null) win.clearTimeout(timer); };

  const keep = doc.getElementById("keep");
  if (keep) {
    keep.addEventListener("click", () => { stop(); win.close(); });
  }
  const discard = doc.getElementById("discard");
  if (discard) {
    discard.addEventListener("click", () => {
      stop();
      if (!duplicate) return;
      // Disabled while in flight: a second click is a second delete request,
      // and the first one may already have moved the file.
      discard.disabled = true;
      void (async () => {
        let resp;
        try {
          resp = await chromeApi.runtime.sendMessage({
            type: "dlr:discard",
            downloadId: model.downloadId,
            relPath: model.relPath,
            dupRelPath: model.dupRelPath,
          });
        } catch (err) {
          resp = { ok: false, error: (err && err.message) || String(err) };
        }
        // A missing answer counts as a refusal, exactly as in the picker: the
        // worker may have been torn down mid-request and "probably worked" is
        // not a thing to report about a delete.
        if (!resp || resp.ok === false) {
          // The refusal goes in its OWN line. Writing it over `#dup` erased
          // the "Duplicate of <path>" text, so after any refusal the user
          // could no longer see WHICH file it was supposed to duplicate --
          // exactly the context needed to judge whether to retry.
          const err = doc.getElementById("error");
          err.textContent = `Not deleted: ${(resp && resp.error)
            || "no answer from the extension (it may have been restarted)"}`;
          err.hidden = false;
          discard.disabled = false;
          return;
        }
        win.close();
      })();
    });
  }

  doc.addEventListener("keydown", (e) => {
    // Escape is KEEP. It is the reflex key, so it must never be the one that
    // deletes something.
    if (e.key === "Escape") { stop(); win.close(); }
  });
  return model;
}

if (typeof document !== "undefined" && typeof chrome !== "undefined"
    && !globalThis.DL_ROUTER_NO_AUTOSTART) {
  mount(document, chrome, window);
}
