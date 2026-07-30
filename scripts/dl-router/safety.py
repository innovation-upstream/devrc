"""Path/name sanitisation — the security core of dl-router.

Everything that can become a filesystem path comes from an untrusted-ish source:
a web page's markup (subject tags), a download's server-supplied filename, or a
JSON body on the loopback socket. A single unvalidated component is a write
outside the library root, so validation lives HERE and every writer calls it.

The rules mirror the extension's `sanitizeDirName`/`sanitizeFileName`
(extension/sanitize.js) one-for-one; `tests/test_security.py` and
`tests/sanitize.test.mjs` assert the two implementations agree.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

# A directory name is ONE path component. No separators, no NUL, no control
# chars, 1..120 chars. `.`/`..` are rejected separately.
MAX_DIR_NAME = 120
MAX_FILE_NAME = 200

# Unicode bidi/format characters. A right-to-left override can make
# `subject<RLO>gnp.exe` render as `subject exe.png` — classic spoofing, and
# there is no legitimate reason for one in a directory name.
_BIDI_CONTROLS = frozenset(chr(c) for c in (
    0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069, 0x061C,
))


class UnsafeName(ValueError):
    """Raised when a name cannot be made safe (caller must fall back)."""


def _has_control(s: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in s)


def is_safe_dir_name(name) -> bool:
    """True iff `name` is a single, safe directory component.

    Rejects: non-str, empty, >120 chars, `.`/`..`, any `/` or `\\`, NUL and
    control characters, bidi/format overrides, leading/trailing whitespace or
    dots (which Windows-ish tooling and humans both mis-read), and any name
    that is not already NFC-normalised (so two visually identical dirs cannot
    both exist).
    """
    if not isinstance(name, str):
        return False
    if not name or len(name) > MAX_DIR_NAME:
        return False
    if name in (".", ".."):
        return False
    if "/" in name or "\\" in name:
        return False
    if "\x00" in name or _has_control(name):
        return False
    if any(ch in _BIDI_CONTROLS for ch in name):
        return False
    if name != name.strip() or name.endswith("."):
        return False
    if unicodedata.normalize("NFC", name) != name:
        return False
    return True


def safe_dir_name(name, *, known=None, allow_new: bool = False,
                  fallback: str | None = None):
    """Validate a directory name, optionally against the known-dir allowlist.

    `known` is the set of directory names that exist under the library root.
    With `allow_new=False` (the default, and what the download path uses) a name
    outside that set is refused — the extension may only file into a directory
    that already exists or one explicitly created via `/mkdir`.

    Returns the name, or `fallback` when supplied, else raises `UnsafeName`.
    """
    ok = is_safe_dir_name(name)
    if ok and known is not None and not allow_new and name not in known:
        ok = False
    if ok:
        return name
    if fallback is not None:
        return fallback
    raise UnsafeName(f"unsafe directory name: {name!r}")


def safe_file_name(name, *, fallback: str = "download") -> str:
    """Reduce an arbitrary string to one safe filename component.

    Never raises — a download always needs *some* name. Strips any directory
    part, control/bidi characters and separators, collapses whitespace, and
    truncates while preserving the extension.
    """
    if not isinstance(name, str) or not name:
        return fallback
    # Take the last component under BOTH separators: a server-supplied
    # "..\\..\\evil.exe" must not survive as a relative path on posix.
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    cleaned = "".join(
        ch for ch in base
        if ord(ch) >= 0x20 and ord(ch) != 0x7F and ch not in _BIDI_CONTROLS
    )
    cleaned = " ".join(cleaned.split()).strip().strip(".")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    if len(cleaned) <= MAX_FILE_NAME:
        return cleaned
    stem, dot, ext = cleaned.rpartition(".")
    if dot and 0 < len(ext) <= 12:
        keep = MAX_FILE_NAME - len(ext) - 1
        return (stem[:keep] + "." + ext) if keep > 0 else cleaned[:MAX_FILE_NAME]
    return cleaned[:MAX_FILE_NAME]


def safe_rel_path(rel, *, root: Path) -> Path:
    """Resolve `rel` under `root`, refusing anything that escapes it.

    Absolute inputs, `..` traversal, and symlinks pointing outside `root` are
    all rejected. Returns the resolved absolute path.
    """
    if not isinstance(rel, str) or not rel:
        raise UnsafeName("empty relative path")
    if "\x00" in rel or _has_control(rel):
        raise UnsafeName("control character in path")
    candidate = Path(rel)
    if candidate.is_absolute():
        raise UnsafeName(f"absolute path not allowed: {rel!r}")
    root_res = Path(root).resolve()
    target = (root_res / candidate).resolve()
    # `is_relative_to` compares the RESOLVED paths, so a symlink inside the
    # library that points at /etc is caught here too.
    if target != root_res and not target.is_relative_to(root_res):
        raise UnsafeName(f"path escapes the library root: {rel!r}")
    return target


def join_dir_file(dir_name: str, file_name: str) -> str:
    """The relative path handed to Chrome's `suggest()`. Both parts validated."""
    if not is_safe_dir_name(dir_name):
        raise UnsafeName(f"unsafe directory name: {dir_name!r}")
    return f"{dir_name}/{safe_file_name(file_name)}"


def is_http_url(url) -> bool:
    """True iff `url` is a plain http(s) URL safe to hand to yt-dlp.

    Rejects other schemes (`file:`, `javascript:`, `data:`), anything with a
    control character or whitespace (argv smuggling / header injection), and a
    leading `-` (which an argv parser would read as a flag).
    """
    if not isinstance(url, str) or not url or len(url) > 4096:
        return False
    if url.startswith("-"):
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    if any(ch.isspace() for ch in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if not parts.netloc:
        return False
    return True


def same_filesystem(a: Path, b: Path) -> bool:
    """True iff both paths live on one device (a rename is then atomic)."""
    try:
        return os.stat(a).st_dev == os.stat(b).st_dev
    except OSError:
        return False
