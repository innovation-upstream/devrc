#!/usr/bin/env python3
"""Regenerate `extension/build_id.js` — the build marker that travels WITH the
extension code.

    python3 scripts/browser-bridge/gen-build-marker.py           # rewrite the file
    python3 scripts/browser-bridge/gen-build-marker.py --check   # verify only

WHY THIS EXISTS (#324). `ping` used to answer "is the build I just deployed the
one Brave loaded?" with `chrome.runtime.getManifest().version` and
`chrome.runtime.id`. Both describe the DIRECTORY the extension was loaded from,
not the code that is executing: the id is derived from the load path, and the
version describes the manifest of the extension that was LOADED. Measured 2026-08-04 —
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

🔴 SECOND EXCLUSION, AND WHY IT IS NOT OPTIONAL: `manifest.json`'s `version`
VALUE is normalised away before hashing (`normalised_manifest_bytes`). The
manifest is still hashed — every other field of it — but the version string
is not, and removing that exclusion makes this module non-terminating.

The reason is a cycle. The version's 4th component is DERIVED from the marker
(`derive_version`, below), so version = f(marker). If the marker also hashed the
version, then marker = g(version) = g(f(marker)) — writing the derived version
back into the manifest would change the marker, which would derive a different
version, forever. There is no fixpoint to iterate to; the normalisation is what
makes `version = f(marker)` a definition rather than a recurrence.

What this costs, stated honestly: a change to ONLY the version string no longer
moves the marker. That is intended — such a change is not a code change, and
the version is now a function of the code rather than an input to it. Every
change that alters BEHAVIOUR still moves the marker, which is the property
`extension_stale` depends on.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parent / "extension"
BUILD_ID_FILE = EXT_DIR / "build_id.js"
MANIFEST_FILE = EXT_DIR / "manifest.json"
MARKER_HEX_CHARS = 16

# How many hex chars of the marker become the version's build component.
# FOUR, and that is a hard constraint, not a taste: Chrome caps each dotted
# component at 65535, and 0xFFFF == 65535 exactly. Five would silently produce
# manifests Chrome REFUSES TO LOAD for ~94% of markers — an extension that
# fails to load looks identical to a bridge that is down.
VERSION_HEX_CHARS = 4
VERSION_COMPONENT_MAX = 65535

# The human-owned part of the version: everything before the build component.
# Bump it by hand for a real release; the generator preserves it.
VERSION_BASE_COMPONENTS = 3

# Matches the manifest's `version` KEY only. `"manifest_version"` cannot match:
# the pattern requires a literal opening quote immediately before `version`,
# and there the preceding character is `_`. Its value is unquoted anyway.
MANIFEST_VERSION_RE = re.compile(r'"version"\s*:\s*"([^"]*)"')

# What the version value is replaced BY when hashing. Any fixed string works;
# this one is self-describing if it ever surfaces in a debug dump.
VERSION_PLACEHOLDER = '"version": "<NORMALISED-FOR-MARKER>"'

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
        # The manifest contributes its NORMALISED bytes — see the module
        # docstring for why hashing its version would make this non-terminating.
        # The length is taken from the normalised body too: taking it from the
        # raw one would leak the version's LENGTH back into the digest, so
        # `0.8.1.7` and `0.8.1.4242` would still disagree and the cycle would
        # come back in a form that only bites on some builds.
        body = (normalised_manifest_bytes(p) if p.name == "manifest.json"
                else p.read_bytes())
        h.update(p.name.encode("utf-8"))
        h.update(b"\0")
        h.update(str(len(body)).encode("ascii"))
        h.update(b"\0")
        h.update(body)
    return h.hexdigest()[:MARKER_HEX_CHARS]


def normalised_manifest_bytes(path=None) -> bytes:
    """`manifest.json`'s bytes with the `version` VALUE replaced by a constant.

    Every other byte of the manifest is preserved, so a permissions change, a
    renamed service worker or an edited description all still move the marker.
    Only the version — which is derived FROM the marker — is neutralised.

    Raises if the manifest carries no version key: silently hashing the raw
    bytes there would reintroduce the cycle on exactly the manifest that is
    malformed, and a marker that is right except in the broken case is worse
    than a loud failure."""
    path = Path(path) if path is not None else MANIFEST_FILE
    text = path.read_text(encoding="utf-8")
    normalised, n = MANIFEST_VERSION_RE.subn(VERSION_PLACEHOLDER, text)
    if n != 1:
        raise AssertionError(
            f"HARNESS: expected exactly ONE `\"version\": \"...\"` in {path}, "
            f"found {n}. The marker derivation cannot neutralise a version it "
            f"cannot locate. Fix this function or the manifest; do NOT fall "
            f"back to hashing the raw bytes — that reintroduces the "
            f"version<->marker cycle described in this module's docstring.")
    return normalised.encode("utf-8")


def read_manifest_version(path=None):
    """The `version` string declared in a manifest, or None if absent."""
    path = Path(path) if path is not None else MANIFEST_FILE
    try:
        m = MANIFEST_VERSION_RE.search(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — best-effort, mirrors read_marker.
        return None
    return m.group(1) if m else None


def version_base(version: str) -> str:
    """The human-owned prefix of a version: its first VERSION_BASE_COMPONENTS
    components.

    So `0.8.1` -> `0.8.1` and `0.8.1.47127` -> `0.8.1`. This is what makes the
    scheme idempotent: re-deriving from an already-derived version must not
    keep appending components.

    🔴 STRICT, and the strictness is the point. An earlier version of this
    function sliced `[:3]` off whatever it was given and returned it verbatim,
    which is wrong in two directions that both reach a host:

      * a base that is not Chrome-legal (`0.8.1-beta`, `v0.8.1`, `0.08.1`,
        `0.8.1 `, ``) was concatenated with the build component and WRITTEN,
        and `--check` then certified the result — producing a manifest Chrome
        REFUSES TO LOAD, which is indistinguishable from a dead bridge. That is
        the exact failure VERSION_HEX_CHARS exists to prevent, reached by
        another door.
      * a base with FEWER than VERSION_BASE_COMPONENTS components (`0.9`) has
        no fixpoint: `0.9` -> `0.9.43738`, whose own base is then `0.9.43738`
        -> `0.9.43738.43738`. It converges only after freezing one build's id
        into the middle of the release, where it never updates again.

    Slicing cannot distinguish "a 3-component base" from "a 2-component base
    that already has a build id appended", so the shape is REQUIRED rather than
    inferred. Fail loudly and name the fix instead."""
    parts = version.split(".")
    if len(parts) < VERSION_BASE_COMPONENTS:
        raise AssertionError(
            f"manifest version {version!r} has {len(parts)} component(s); the "
            f"release base needs at least {VERSION_BASE_COMPONENTS} "
            f"(e.g. '0.9.0', not '0.9'). More is fine — a 4th component is the "
            f"generated build id and is truncated away here. A SHORTER base has "
            f"no fixpoint: the "
            f"generated build id would be re-read as part of the base on the "
            f"next run and frozen there permanently.")
    base_parts = parts[:VERSION_BASE_COMPONENTS]
    for p in base_parts:
        _require_legal_component(p, version)
    return ".".join(base_parts)


def _require_legal_component(part: str, version: str) -> None:
    """Raise unless `part` is a component Chrome will accept.

    Chrome's rule: 1-4 dot-separated integers, each 0..65535, no leading zeros.
    A manifest breaking it does not warn — the extension simply fails to LOAD,
    which presents as a bridge that is down rather than as a bad version.

    `isascii()` guards `isdigit()`, and the order matters: `'²'.isdigit()` is
    True while `int('²')` RAISES, so testing digit-ness alone let a ValueError
    escape from the next clause — losing the message that names Chrome's rule
    and the file to edit, in favour of a bare
    `invalid literal for int() with base 10`."""
    if not (part.isascii() and part.isdigit()) or part != str(int(part)) or \
            int(part) > VERSION_COMPONENT_MAX:
        raise AssertionError(
            f"manifest version {version!r} contains component {part!r}, which "
            f"Chrome will not accept: every component must be a plain integer "
            f"0..{VERSION_COMPONENT_MAX} with no leading zeros, sign, suffix "
            f"or whitespace. An extension whose manifest breaks this fails to "
            f"LOAD, and a bridge that never connects looks nothing like a "
            f"version problem. Fix the version in extension/manifest.json.")


def build_component(marker) -> int:
    """The version's build component: the marker's first VERSION_HEX_CHARS hex
    chars as an integer, so it moves whenever the CODE moves.

    Validates its input rather than indexing it blind. `read_marker` returns
    None by contract when build_id.js is missing or unreadable, and a bare
    `marker[:4]` on that raises TypeError from whatever line happens to call it
    — including, for a module-level caller, at IMPORT time, where it becomes a
    collection error that hides every other test in the file."""
    if not isinstance(marker, str) or len(marker) < VERSION_HEX_CHARS:
        raise AssertionError(
            f"HARNESS: need a build marker of at least {VERSION_HEX_CHARS} hex "
            f"chars to derive a version component, got {marker!r}. A None here "
            f"means extension/build_id.js is missing or unreadable — "
            f"regenerate it: {REGEN_CMD}")
    value = int(marker[:VERSION_HEX_CHARS], 16)
    if not 0 <= value <= VERSION_COMPONENT_MAX:
        raise AssertionError(  # unreachable while VERSION_HEX_CHARS == 4
            f"HARNESS: build component {value} outside Chrome's 0.."
            f"{VERSION_COMPONENT_MAX} — a manifest carrying it would not load.")
    return value


def derive_version(ext_dir=None, marker: str | None = None) -> str:
    """The version `ext_dir`'s manifest SHOULD declare: its human-owned base
    with the marker-derived build component appended."""
    ext_dir = Path(ext_dir) if ext_dir is not None else EXT_DIR
    current = read_manifest_version(ext_dir / "manifest.json")
    if current is None:
        raise AssertionError(
            f"HARNESS: no version in {ext_dir / 'manifest.json'} — nothing to "
            f"derive a base from.")
    if marker is None:
        marker = compute_marker(ext_dir)
    # No re-validation of the concatenation here. `version_base` returns exactly
    # VERSION_BASE_COMPONENTS already-validated components and `build_component`
    # returns an int in 0..VERSION_COMPONENT_MAX, so the result is always 4 legal
    # components — a check here CANNOT FIRE. An earlier revision had one, with a
    # comment calling it "the last point before it is written to a manifest",
    # which was worse than nothing: it read as defence-in-depth while being
    # unreachable, and it pointed away from the entry point that IS unguarded.
    # That check now lives in `write_manifest_version`, where an externally
    # supplied `version=` can genuinely be illegal.
    return f"{version_base(current)}.{build_component(marker)}"


def write_manifest_version(ext_dir=None, version: str | None = None) -> str:
    """Rewrite the manifest's version in place, touching nothing else.

    A targeted substitution rather than a JSON round-trip on purpose: reparsing
    and re-dumping would reformat the whole file, so every build would produce a
    diff far larger than the one byte-range that actually changed.

    🔴 THIS is where the written string is validated, and it is the only place
    that can be. A caller-supplied `version=` is the one entry point that
    reaches the manifest without passing through `derive_version` — so
    `write_manifest_version(d, "0.8.1-beta")` used to write a manifest Chrome
    refuses to load, with no error. `derive_version`'s own output is legal by
    construction, so validating it there could never fire."""
    ext_dir = Path(ext_dir) if ext_dir is not None else EXT_DIR
    path = ext_dir / "manifest.json"
    version = version or derive_version(ext_dir)
    parts = version.split(".")
    if not 1 <= len(parts) <= 4:
        raise AssertionError(
            f"refusing to write version {version!r}: it has {len(parts)} "
            f"components and Chrome accepts 1-4. An extension whose manifest "
            f"breaks this fails to LOAD, which presents as a dead bridge.")
    for p in parts:
        _require_legal_component(p, version)
    text = path.read_text(encoding="utf-8")
    new, n = MANIFEST_VERSION_RE.subn(f'"version": "{version}"', text)
    if n != 1:
        raise AssertionError(
            f"HARNESS: expected exactly ONE version key in {path}, found {n}.")
    path.write_text(new, encoding="utf-8")
    return version


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
export const BUILD_MARKER = "{marker}";
'''


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed marker is stale")
    args = ap.parse_args(argv)

    want = compute_marker()
    have = read_marker()
    want_version = derive_version(marker=want)
    have_version = read_manifest_version()

    if args.check:
        rc = 0
        if have == want:
            print(f"build marker OK: {want}")
        else:
            print(f"build marker STALE: build_id.js has {have!r}, extension "
                  f"source hashes to {want!r}. Run: {REGEN_CMD}",
                  file=sys.stderr)
            rc = 1
        # Reported independently of the marker, and both verdicts always print:
        # collapsing them would let a correct marker mask a stale version, and
        # the version is the half a HUMAN reads in brave://extensions.
        if have_version == want_version:
            print(f"manifest version OK: {want_version}")
        else:
            print(f"manifest version STALE: manifest.json declares "
                  f"{have_version!r}, the marker derives {want_version!r}. "
                  f"Run: {REGEN_CMD}", file=sys.stderr)
            rc = 1
        return rc

    written_version = write_manifest_version(version=want_version)
    BUILD_ID_FILE.write_text(render(want), encoding="utf-8")
    print(f"wrote {BUILD_ID_FILE} ({have!r} -> {want!r})")
    print(f"wrote {MANIFEST_FILE} version ({have_version!r} -> "
          f"{written_version!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
