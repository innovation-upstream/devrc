// build_id.js — the browser-bridge extension's BUILD MARKER (#324).
//
// GENERATED. Do not hand-edit; regenerate with:
//     python3 scripts/browser-bridge/gen-build-marker.py
// `scripts/browser-bridge/tests/test_server.py` recomputes this value from the
// extension source on every CI run and fails if it is stale, naming that
// command.
//
// 🔴 WHY THIS IS A LITERAL, AND WHY EVERY RUNTIME-READ ALTERNATIVE IS WRONG.
// The question this answers is "is the code executing right now the code I
// deployed?". A value that a running service worker COMPUTES by reading disk
// cannot answer it, because the disk is the thing that was updated — a stale
// worker reads the NEW file and reports the NEW value. That is precisely the
// bug in #324: `chrome.runtime.getManifest().version` describes the manifest of
// the extension that was LOADED, and `chrome.runtime.id` is derived from the
// load PATH, so neither describes the running code. Measured 2026-08-04:
// two Brave profiles on ONE directory reported the same id, the same 0.7.3 and
// `extension_stale: false` while executing different code.
//
// So do NOT "simplify" this to any of:
//   * fetch(chrome.runtime.getURL("build_id.json"))  — reads disk at runtime
//   * chrome.runtime.getManifest().version            — describes the LOAD
//   * a value written into chrome.storage.local       — survives a code swap
//   * a hash computed in the worker over its own source — same disk read
// The marker must be a LITERAL in a module the worker imported, so that it was
// frozen into the loaded module graph at load time and travels with the code.
export const BUILD_MARKER = "e1ee86a50a811d40";
