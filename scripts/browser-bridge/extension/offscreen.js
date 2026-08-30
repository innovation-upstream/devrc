// offscreen.js — write one string to the clipboard on behalf of the service
// worker, which cannot reach the clipboard itself (no `document` in MV3).
//
// 🔴 `document.execCommand("copy")`, NOT `navigator.clipboard.writeText()`.
// The async Clipboard API requires the calling document to be FOCUSED; an
// offscreen document is never focused, so writeText() rejects with
// "Document is not focused" every single time. execCommand on a selected
// textarea has no such requirement and is the path Chrome's own offscreen
// clipboard sample uses. It is deprecated-but-supported, and there is no
// replacement reachable from this context.
//
// 🔴 AND IT NEEDS THE `clipboardWrite` PERMISSION — the manifest declares it, and
// dropping it makes this file silently inert. `execCommand("copy")` is allowed
// without that permission ONLY inside a short-lived event handler for a user
// action. This copy is neither: by the time it runs, the worker has awaited
// config(), the active tab, a NETWORK /whoami round trip and the creation of
// this very document — and this document never had transient activation at all.
// Without the permission `execCommand` returns FALSE (it does not throw), which
// this listener reports honestly as {ok:false}, so the symptom is a ✗ badge and
// an untouched clipboard on EVERY click, forever. Chrome's own
// cookbook.offscreen-clipboard-write sample declares `clipboardWrite` for
// exactly this reason. tests/offscreen_clipboard.test.mjs pins both permissions
// against the manifest.
//
// The listener answers ONLY its own `target`, because chrome.runtime.sendMessage
// fans out to every extension context except the sender — an options page that
// grew a listener must not be able to answer for the clipboard.
const TARGET = "offscreen-clipboard";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== TARGET) return false;   // not ours: stay silent
  if (msg.type !== "copy") {
    sendResponse({ ok: false, error: `unknown type: ${String(msg.type)}` });
    return false;
  }
  try {
    const sink = document.getElementById("sink");
    sink.value = String(msg.text == null ? "" : msg.text);
    sink.select();
    // execCommand REPORTS failure by returning false rather than throwing, so
    // the boolean is the whole result — treating a completed call as success is
    // how a ✓ badge ends up over an empty clipboard.
    const ok = document.execCommand("copy");
    sendResponse(ok ? { ok: true } : { ok: false, error: "execCommand refused" });
  } catch (e) {
    sendResponse({ ok: false, error: String((e && e.message) || e) });
  }
  return false;                                      // answered synchronously
});
