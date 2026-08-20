#!/usr/bin/env python3
"""Static analysis of an opencode dispatch BRIEF — the two measured failure
modes that make a dispatch exit 0 having done nothing.

FAILURE 1 — `external_directory: "ask"` -> headless auto-reject -> exit 0.
    MEASURED twice, in both directions (a read, then a write). The brief named a
    path outside `--dir`; opencode asked for external-directory access; `opencode
    run` AUTO-REJECTS an `ask` rather than prompting; the run ended successfully
    having done nothing. Its fingerprint in opencode's own store: 10 of 321
    sessions with `model IS NULL` and 0 tokens.

    `scan_paths` closes it EXACTLY: a path is under `--dir` or it is not. There
    is no keyword heuristic here and no scoring. `opencode-dispatch` HARD-BLOCKS
    on a non-empty result, because the failure it prevents is a silent exit 0 —
    the one failure shape a human never notices.

FAILURE 3 — `permission.bash` `ask` -> auto-reject MID-RUN.
    MEASURED: opencode needed `kubectl exec … psql`, was auto-rejected, and the
    dispatch was abandoned half-done. `scan_commands` extracts the brief's fenced
    command blocks and resolves each command node against the same
    `permission.bash` block the engine uses.

    🔴 This one WARNS and never blocks. It runs through a parser that
    deliberately OVER-MATCHES (guard_core's splitter plus opencode's own
    `*`-crosses-everything globs), so blocking on it would create a
    permanently-red gate — and RULES.md is explicit that a gate people learn to
    click through is worse than no gate.

ONE PARSER, NOT TWO
-------------------
🔴 Command splitting is `scripts/claude-hooks/guard_core.py`'s `split_commands`,
imported, not reimplemented. It already splits on `;`/`&&`/`||`/`|`/`&`, strips
`VAR=` prefixes and `sudo`/`env`/`timeout` wrappers, recurses into `bash -c`,
and lifts heredoc bodies. A second splitter is how the two disagree, and the
disagreement would be invisible: both would still print warnings.
"""
from __future__ import annotations

import os
import re
import sys
from collections import namedtuple
from pathlib import Path

_LIB = Path(__file__).resolve().parent
_HOOKS = _LIB.parent.parent / "claude-hooks"
# Both, so this module imports cleanly however it is reached — the CLI, the test
# suite, or a bare `python -c "import brief_scan"` from anywhere.
for _p in (_LIB, _HOOKS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import guard_core  # noqa: E402  — the repo's ONE command splitter + guard policy

from oc_permissions import base_bash_rules, resolve_with_rule  # noqa: E402

# --------------------------------------------------------------------------- #
# Path containment
# --------------------------------------------------------------------------- #

# 🔴 AN EXPLICIT ENUMERATION, NOT A PATTERN — same shape as drift-check's
# settings.json key allowlist, and for the same reason: an unlisted prefix is an
# offender BY DEFAULT. Each entry is a read-only system location that is never a
# work target, so naming one in a brief cannot be the "my brief lives outside
# --dir" mistake this scanner exists for. It is NOT env-overridable.
#
# 🔴 `/tmp` is DELIBERATELY ABSENT. Claude Code's per-session scratchpad lives
# under /tmp, and "the brief lived in the scratchpad" is failure 1's exact
# vector — allowing /tmp here would exempt the only path the incident actually
# involved.
# 🔴 `/etc` and `/var` are likewise absent: `/etc/nixos` is a real edit target on
# these hosts, and a brief naming it genuinely needs a wider `--dir`.
SYSTEM_ALLOW = {
    "/nix/store": "immutable nix store — read-only by construction",
    "/usr": "distro-provided binaries/headers; never an edit target here",
    "/bin": "distro-provided binaries",
    "/sbin": "distro-provided binaries",
    "/lib": "distro-provided libraries",
    "/lib64": "distro-provided libraries",
    "/proc": "kernel pseudo-filesystem",
    "/sys": "kernel pseudo-filesystem",
    "/dev": "device nodes; /dev/null and friends appear in every shell snippet",
    "/run": "runtime state (sockets, /run/wrappers on NixOS)",
}

# A URL's `//host/path` is not a filesystem path. Strip URLs BEFORE scanning or
# every `https://example.invalid/x` contributes a bogus `/x`.
_URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.\-]*://\S+")

# An absolute POSIX path or a `~/`-relative one.
#
# The lookbehind is what keeps prose out: in `and/or`, `20/80` and `**/*.py` the
# slash is preceded by a character in the excluded class, so none of them yields
# a candidate. At least one segment character is REQUIRED after the leading
# slash, so a bare `/` in prose ("read/write") is not a path.
_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.~*/@+-])"
    r"(~/[A-Za-z0-9_./~+@-]*|/[A-Za-z0-9_.~+@-][A-Za-z0-9_./~+@-]*)"
)

# Sentence punctuation that sticks to the end of a path in prose. A `/` is NOT
# stripped — a trailing slash is part of the path.
_TRAILING_PUNCT = ".,;:!?)]}'\"`"

Offender = namedtuple("Offender", "text resolved")


def canon(path) -> str:
    """One canonicalisation rule for both sides of the comparison.

    `os.path.realpath` on a path that does not exist resolves the symlinks in
    whatever prefix DOES exist and leaves the rest lexically — which is the
    behaviour wanted here, since a brief routinely names files the dispatch is
    about to create.
    """
    return os.path.realpath(os.path.expanduser(str(path)))


def extract_paths(text: str) -> list[str]:
    """Every absolute (or `~/`) path-shaped token in `text`, in order, deduped.

    Pure and independently testable — the positive control in the test suite
    calls this directly, so a scanner wired to nothing cannot report a
    reassuring zero.
    """
    stripped = _URL_RE.sub(" ", text)
    out, seen = [], set()
    for m in _PATH_RE.finditer(stripped):
        cand = m.group(1).rstrip(_TRAILING_PUNCT)
        if not cand or cand in ("~/", "/"):
            continue
        if cand not in seen:
            seen.add(cand)
            out.append(cand)
    return out


def is_allowlisted(resolved: str) -> str | None:
    """The SYSTEM_ALLOW prefix covering `resolved`, or None."""
    for prefix, reason in SYSTEM_ALLOW.items():
        if resolved == prefix or resolved.startswith(prefix + os.sep):
            return reason
    return None


def is_under(resolved: str, root: str) -> bool:
    """🔴 The whole predicate. Exact: a path is under `root` or it is not.

    `os.sep` matters — a bare `startswith` would call `/home/zach/devrc-other`
    a child of `/home/zach/devrc`.
    """
    return resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep)


def scan_paths(text: str, directory) -> list[Offender]:
    """Paths in `text` that are NOT under `directory` and not system-allowlisted.

    An empty list means the brief is containable. A non-empty one is failure 1
    waiting to happen, and `opencode-dispatch` refuses on it.
    """
    root = canon(directory)
    offenders = []
    for cand in extract_paths(text):
        resolved = canon(cand)
        if is_under(resolved, root):
            continue
        if is_allowlisted(resolved):
            continue
        offenders.append(Offender(cand, resolved))
    return offenders


# --------------------------------------------------------------------------- #
# Fenced command blocks -> permission verdicts
# --------------------------------------------------------------------------- #

# ``` or ~~~ fences, with the closing fence at line start. Non-greedy body.
_FENCE_RE = re.compile(
    r"^(?P<fence>`{3,}|~{3,})[ \t]*(?P<info>[^\n]*)\n(?P<body>.*?)^(?P=fence)[ \t]*$",
    re.M | re.S,
)

# Info strings that mean "this block is shell". An EMPTY info string counts:
# a bare ``` block in a brief is overwhelmingly a command block, and missing a
# real command is the failure mode that matters on a warn-only channel.
SHELL_INFO = {
    "", "bash", "sh", "shell", "zsh", "console", "shell-session",
    "shellsession", "terminal", "command", "commands", "text",
}

# Bound on how many command nodes one brief contributes. A brief is prose; this
# only exists so a pathological paste cannot spin the resolver.
_MAX_COMMANDS = 400

Verdict = namedtuple("Verdict", "command action pattern guard_reason")


def shell_blocks(text: str) -> list[str]:
    """The bodies of every fenced block whose info string reads as shell."""
    out = []
    for m in _FENCE_RE.finditer(text):
        # `bash {.line-numbers}` and friends: the LANGUAGE is the first word.
        words = m.group("info").strip().lower().split()
        info = words[0] if words else ""
        if info in SHELL_INFO:
            out.append(m.group("body"))
    return out


def block_commands(body: str) -> list[str]:
    """Command nodes in one fenced block, via guard_core's splitter.

    Prompt markers (`$ `, `# ` at line start when followed by a space) and
    whole-line comments are dropped FIRST — `split_commands` is a shell splitter,
    not a transcript parser, and would otherwise emit `#` as argv[0] for every
    comment line.
    """
    lines = []
    for raw in body.split("\n"):
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("$ "):
            line = stripped[2:]
        lines.append(line)
    if not lines:
        return []
    return guard_core.split_commands("\n".join(lines))


def scan_commands(text: str, directory=None, config: dict | None = None) -> list[Verdict]:
    """Every command node in the brief's shell blocks that would NOT run clean.

    Returns only the nodes whose resolved action is `ask` or `deny`, plus any the
    guard's "opencode" policy hard-denies. An `ask` is the one that matters most:
    `opencode run` auto-rejects it mid-run, so the dispatch stalls half-done
    rather than erroring.
    """
    rules = base_bash_rules(config)
    out, seen = [], set()
    for body in shell_blocks(text):
        for cmd in block_commands(body):
            cmd = cmd.strip()
            if not cmd or cmd in seen:
                continue
            seen.add(cmd)
            if len(seen) > _MAX_COMMANDS:
                return out
            action, pattern = resolve_with_rule(rules, cmd)
            try:
                guard_reason = guard_core.evaluate(cmd, policy="opencode",
                                                   cwd=str(directory) if directory else None)
            except Exception:  # noqa: BLE001 — a warn-only channel never raises
                guard_reason = None
            if action in ("ask", "deny") or guard_reason:
                out.append(Verdict(cmd, action, pattern, guard_reason))
    return out
