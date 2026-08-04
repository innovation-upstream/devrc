#!/usr/bin/env python3
"""Regenerate `extension/build_id.js` — the build marker that travels WITH the
extension code.

    python3 scripts/browser-bridge/gen-build-marker.py           # rewrite the file
    python3 scripts/browser-bridge/gen-build-marker.py --check   # verify only

WHY THIS EXISTS (#324). `ping` used to answer "is the build I just deployed the
one Brave loaded?" with `chrome.runtime.getManifest().version` and
`chrome.runtime.id`. Both describe the DIRECTORY the extension was loaded from,
not the code that is executing: the id is derived from the load path, and the
version is read off the manifest on disk at call time. Measured 2026-08-04 —
two Brave profiles loading the SAME directory reported an identical id, an
identical `0.7.3`, and `extension_stale: false`, while one was executing `main`
and the other an unmerged 0.7.2 build whose source exists on no disk. No
version-shaped signal can separate those two rows.

The marker fixes that by being a **literal constant in the extension's own
loaded module graph**: whatever code is running carries its own marker, so a
stale worker necessarily reports the stale value.

THE DERIVATION (`compute_marker`) is deliberately the ONE place that defines the
marker, and both the generator and the CI drift test call it — a second copy is
how a gate like this drifts into vacuousness.

  sha256 over every `*.js` file in `extension/` (EXCEPT `build_id.js` itself —
  it must not hash its own output) plus `manifest.json`, sorted by relative
  path, each contributing `path\\0<len>\\0<bytes>` so a rename or a byte move
  between files cannot collide. First MARKER_HEX_CHARS hex chars.

`build_id.js` is excluded to avoid the self-reference; that is safe because the
file contains nothing but the marker, so it has no behaviour of its own to go
untracked.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parent / "extension"
BUILD_ID_FILE = EXT_DIR / "build_id.js"
MARKER_HEX_CHARS = 16

# The regeneration command, quoted verbatim in every failure message so a red
# gate tells you what to run instead of tempting you to delete it.
REGEN_CMD = "python3 scripts/browser-bridge/gen-build-marker.py"

# Matches the literal in build_id.js. Kept loose on whitespace/quote style but
# strict on the value: lowercase hex only.
MARKER_RE = re.compile(
    r"""BUILD_MARKER\s*=\s*["']([0-9a-f]+)["']""")


def marker_inputs(ext_dir=None):
    """The files the marker hashes, sorted by relative path.

    Every `*.js` under `extension/` except `build_id.js`, plus `manifest.json`.
    Sorted so the digest is order-independent of the filesystem."""
    ext_dir = Path(ext_dir) if ext_dir is not None else EXT_DIR
    files = [p for p in sorted(ext_dir.glob("*.js")) if p.name != "build_id.js"]
    manifest = ext_dir / "manifest.json"
    if manifest.is_file():
        files.append(manifest)
    return sorted(files, key=lambda p: p.name)


def compute_marker(ext_dir=None) -> str:
    """The marker the extension source in `ext_dir` SHOULD carry.

    Raises rather than returning a digest of nothing: an empty input set would
    make every caller vacuous (a "matching" marker over zero files), which is
    the exact harness failure this gate exists to prevent."""
    files = marker_inputs(ext_dir)
    if not files:
        raise AssertionError(
            f"HARNESS: hashed ZERO files out of {ext_dir or EXT_DIR} — the "
            f"extension layout changed. Fix this function; do NOT read the "
            f"empty result as 'nothing to check'.")
    h = hashlib.sha256()
    for p in files:
        body = p.read_bytes()
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(str(len(body)).encode("ascii"))
        h.update(b"\0")
        h.update(body)
    return h.hexdigest()[:MARKER_HEX_CHARS]


def read_marker(path=None):
    """The BUILD_MARKER literal declared in a build_id.js, or None (missing /
    unreadable / no literal). Best-effort — never raises."""
    path = Path(path) if path is not None else BUILD_ID_FILE
    try:
        m = MARKER_RE.search(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — best-effort.
        return None
    return m.group(1) if m else None


def render(marker: str) -> str:
    return f'''// build_id.js — the browser-bridge extension's BUILD MARKER (#324).
//
// GENERATED. Do not hand-edit; regenerate with:
//     {REGEN_CMD}
// `scripts/browser-bridge/tests/test_server.py` recomputes this value from the
// extension source on every CI run and fails if it is stale, naming that
// command.
//
// 🔴 WHY THIS IS A LITERAL, AND WHY EVERY RUNTIME-READ ALTERNATIVE IS WRONG.
// The question this answers is "is the code executing right now the code I
// deployed?". A value that a running service worker COMPUTES by reading disk
// cannot answer it, because the disk is the thing that was updated — a stale
// worker reads the NEW file and reports the NEW value. That is precisely the
// bug in #324: `chrome.runtime.getManifest().version` reads the on-disk
// manifest, and `chrome.runtime.id` is derived from the load PATH, so both
// describe the DIRECTORY rather than the running code. Measured 2026-08-04:
// two Brave profiles on ONE directory reported the same id, the same 0.7.3 and
// `extension_stale: false` while executing different code.
//
// So do NOT "simplify" this to any of:
//   * fetch(chrome.runtime.getURL("build_id.json"))  — reads disk at runtime
//   * chrome.runtime.getManifest().version            — reads disk at runtime
//   * a value written into chrome.storage.local       — survives a code swap
//   * a hash computed in the worker over its own source — same disk read
// The marker must be a LITERAL in a module the worker imported, so that it was
// frozen into the loaded module graph at load time and travels with the code.
export const BUILD_MARKER = "{marker}";
'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed marker is stale")
    args = ap.parse_args(argv)

    want = compute_marker()
    have = read_marker()
    if args.check:
        if have == want:
            print(f"build marker OK: {want}")
            return 0
        print(f"build marker STALE: build_id.js has {have!r}, extension source "
              f"hashes to {want!r}. Run: {REGEN_CMD}", file=sys.stderr)
        return 1
    BUILD_ID_FILE.write_text(render(want), encoding="utf-8")
    print(f"wrote {BUILD_ID_FILE} ({have!r} -> {want!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
