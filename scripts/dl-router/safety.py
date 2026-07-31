"""Path/name sanitisation — the security core of dl-router.

Everything that can become a filesystem path comes from an untrusted-ish source:
a web page's markup (subject tags), a download's server-supplied filename, or a
JSON body on the loopback socket. A single unvalidated component is a write
outside the library root, so validation lives HERE and every writer calls it.

The rules mirror the extension's `sanitizeDirName`/`sanitizeFileName`
(extension/sanitize.js) one-for-one, and BOTH suites are driven from the same
fixture file (`tests/fixtures/name_cases.json`) so the two tables cannot drift
apart the way two hand-copied literal lists did.

Where the two languages' primitives disagree, this module is written to the
STRICTER reading, because the sidecar is the side that creates real
directories: if `/mkdir` accepted a name the extension will never route into,
the result is a directory nothing can file into, and the NFC anti-homoglyph
check is defeated (two visually identical directories, one reachable). The
concrete divergences that had to be closed:

  * JS `trim()` strips U+FEFF, Python `strip()` does not  -> U+FEFF (and the
    other zero-width format characters) are rejected outright by both;
  * Python treats U+0085 and the other C1 controls as whitespace/controls, JS
    does not  -> `_has_control` rejects the whole C1 range in both;
  * `". foo"` reduced to `" foo"` in Python and `"foo"` in JS  -> both now run
    collapse-whitespace, strip dots, strip whitespace in that order;
  * `is_http_url` accepted an out-of-range port in Python and rejected it in
    JS  -> the port is parsed and range-checked here too.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

# A directory name is ONE path component. No separators, no NUL, no control
# chars, 1..120 chars. `.`/`..` are rejected separately.
MAX_DIR_NAME = 120
MAX_FILE_NAME = 200

# Unicode bidi/format and zero-width characters. A right-to-left override can
# make `subject<RLO>gnp.exe` render as `subject exe.png` — classic spoofing —
# and a zero-width character makes two directory names that render identically
# compare unequal, which is exactly what the NFC check exists to prevent. There
# is no legitimate reason for one of these in a directory name.
#
# U+FEFF is in this set for a second reason: JS `trim()` strips it and Python
# `str.strip()` does not, so leaving it merely "allowed" guaranteed the two
# implementations disagreed on every name that ended with one.
_FORMAT_CONTROLS = frozenset(chr(c) for c in (
    0x200E, 0x200F, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069, 0x061C,
    0x200B, 0x200C, 0x200D, 0xFEFF,
))

# Back-compat name for the narrower set this used to be.
_BIDI_CONTROLS = _FORMAT_CONTROLS

# Characters that are syntactically legal in a POSIX filename but break a tool
# dl-router actually invokes. `:` is the separator in yt-dlp's
# `--paths TYPE:PATH`, so a directory containing one is silently mis-parsed
# into a type selector plus a truncated path (fetcher.py builds `--paths`).
_HOSTILE_PUNCTUATION = frozenset({":"})


class UnsafeName(ValueError):
    """Raised when a name cannot be made safe (caller must fall back)."""


def _has_control(s: str) -> bool:
    """C0 controls, DEL, and the C1 range (0x80-0x9F).

    C1 matters for parity: Python's `str.strip()`/`str.split()` treat U+0085
    (NEL) as whitespace and JS's `trim()`/`\\s` do not, so anything that merely
    *trimmed* them diverged. Rejecting the range outright makes both sides
    agree and loses nothing — a C1 control in a filename is never legitimate.
    """
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F or 0x80 <= ord(ch) <= 0x9F
               for ch in s)


def is_safe_dir_name(name) -> bool:
    """True iff `name` is a single, safe directory component.

    Rejects: non-str, empty, >120 chars, `.`/`..`, any `/` or `\\`, `:`, NUL
    and control characters (C0, DEL, C1), bidi/format/zero-width characters,
    leading/trailing whitespace or dots (which Windows-ish tooling and humans
    both mis-read), and any name that is not already NFC-normalised (so two
    visually identical dirs cannot both exist).
    """
    if not isinstance(name, str):
        return False
    if not name or len(name) > MAX_DIR_NAME:
        return False
    if name in (".", ".."):
        return False
    if "/" in name or "\\" in name:
        return False
    if any(ch in _HOSTILE_PUNCTUATION for ch in name):
        return False
    if "\x00" in name or _has_control(name):
        return False
    if any(ch in _FORMAT_CONTROLS for ch in name):
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
        if not _has_control(ch) and ch not in _FORMAT_CONTROLS
        and ch not in _HOSTILE_PUNCTUATION
    )
    # ORDER MATTERS and must match sanitize.js exactly: collapse whitespace,
    # then strip dots at both ends, then strip whatever whitespace that
    # exposed. Doing the dot-strip last left Python returning `" foo"` for
    # `". foo"` where JS returned `"foo"`.
    cleaned = " ".join(cleaned.split()).strip(".").strip()
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


# `_` is invalid per RFC 1123 but browsers and curl accept it and it does
# occur in the wild, so it is allowed here too -- what matters is that BOTH
# implementations allow exactly the same set.
_HOST_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_IPV6_CHARS = frozenset("0123456789abcdefABCDEF:.")
_DIGITS = frozenset("0123456789")


def _valid_host(host: str) -> bool:
    """Strict, parser-independent host rule. Mirrors sanitize.js exactly."""
    if not host or len(host) > 253:
        return False
    if host.startswith("["):
        if not host.endswith("]"):
            return False
        inner = host[1:-1]
        return bool(inner) and all(ch in _IPV6_CHARS for ch in inner)
    if "]" in host:
        return False
    for label in host.split("."):
        if not label or len(label) > 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        if any(ch not in _HOST_CHARS for ch in label):
            return False
    return True


def is_http_url(url) -> bool:
    """True iff `url` is a plain http(s) URL safe to hand to yt-dlp.

    Rejects other schemes (`file:`, `javascript:`, `data:`), anything with a
    control character or whitespace (argv smuggling / header injection), and a
    leading `-` (which an argv parser would read as a flag).

    The authority is split and validated BY HAND rather than trusting
    `urlsplit`. Differential fuzzing against sanitize.js found 50 inputs where
    the two standard parsers disagreed about what a host is -- `urlsplit`
    accepts `http://%2f` and `http://<ZWNBSP>`, while WHATWG's `new URL`
    accepts `http://////..` by collapsing the extra slashes. Neither answer is
    wrong for its own spec, which is exactly why neither can be the contract
    here: an explicit rule shared by both implementations is the only way the
    sidecar and the extension provably agree on what yt-dlp will be handed.
    """
    if not isinstance(url, str) or not url or len(url) > 4096:
        return False
    if url.startswith("-"):
        return False
    if _has_control(url):
        return False
    if any(ch.isspace() for ch in url):
        return False
    if any(ch in _FORMAT_CONTROLS for ch in url):
        return False

    lowered = url.lower()
    if lowered.startswith("http://"):
        rest = url[len("http://"):]
    elif lowered.startswith("https://"):
        rest = url[len("https://"):]
    else:
        return False

    end = len(rest)
    for i, ch in enumerate(rest):
        if ch in "/?#":
            end = i
            break
    authority = rest[:end]
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]

    port = None
    host = authority
    if host.startswith("["):
        close = host.find("]")
        if close < 0:
            return False
        tail = host[close + 1:]
        host = host[:close + 1]
        if tail:
            if not tail.startswith(":"):
                return False
            port = tail[1:]
    elif ":" in host:
        host, _, port = host.partition(":")

    if port is not None:
        if not port or any(ch not in _DIGITS for ch in port):
            return False
        if not 1 <= int(port) <= 65535:
            return False
    return _valid_host(host)
