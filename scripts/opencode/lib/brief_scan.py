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
#
# 🔴 MATCHED AGAINST THE PATH AS WRITTEN (lexically normalised), NOT its
# realpath — and that is a FIX, not an oversight. Matching the realpath made the
# two comments above FALSE on NixOS: `/etc/hosts` realpaths into
# `/nix/store/…-hosts`, hit the `/nix/store` entry, and passed. So every `/etc`
# path this list claims to block was silently allowed, invisibly, because the
# under-blocking looked identical to a clean brief. A symlink out of a listed
# prefix now blocks (fail-closed), which is the correct side to err on for an
# enumeration whose whole premise is "unlisted is an offender by default".
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
# a path in a QUERY or FRAGMENT (`?f=/etc/passwd`, `#/opt/thing`) is extracted as
# a real one — those are preceded by `=` or `#`, which the lookbehind does not
# exclude. A plain `https://host/a/b` needs no stripping; every `/` in it is
# already preceded by an excluded character.
_URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.\-]*://\S+")

# 🔴 A path built from a VARIABLE cannot be resolved statically, and must be
# reported as UNMEASURED rather than folded into either verdict.
#
# Both spellings were wrong before this pattern existed, in OPPOSITE directions,
# and both are the most likely content of a brief written in this repo:
#   `${pkgs.python312}/bin/python3`  -> FALSE BLOCK. `}` is not in the
#       lookbehind's excluded class, so `/bin/python3` was extracted as a real
#       absolute path and refused the dispatch. Same for a Python f-string's
#       `{base}/sub/file.py`, and for `${DEVRC}/scripts/tests`.
#   `$DEVRC/scripts/tests`          -> SILENT PASS. The `/` follows a letter,
#       which IS excluded, so nothing was extracted at all — and CLAUDE.md
#       actively tells agents to use those `$DEVRC`/`$HOMELAB` handles.
# With no override flag, a false block leaves the operator only "reword or
# abandon the tool", which is the gate-people-route-around outcome this design
# exists to avoid; a silent pass is the failure the gate exists to catch. So
# neither verdict is honest here: say UNMEASURED and let the operator decide.
_VAR_PATH_RE = re.compile(
    r"(?:\$\{[^}\s]*\}"           # ${DEVRC}, ${pkgs.python312}
    r"|\$[A-Za-z_][A-Za-z0-9_]*"  # $DEVRC
    r"|\{[A-Za-z_][A-Za-z0-9_.]*\})"  # {base} — a Python f-string slot
    r"(?:/[A-Za-z0-9_.~+@${}-]+)+/?"  # …followed by at least one path segment
)

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

# 🔴 A RELATIVE path can escape `--dir` too, and the docs invite it: SKILL.md
# tells the brief's author to name locations RELATIVE to `--dir`, and opencode
# runs with `cwd=--dir`, so `../other-repo/x` genuinely leaves the directory.
# Measured before this pattern existed: `"Read ../outside/extra.md and apply
# it."` reported `paths examined : 0` and an unconditional all-clear.
#
# Scoped to tokens containing a `..` SEGMENT, because those are the only
# relative tokens that can escape at all — `src/x.py` resolves under `--dir` by
# construction and matching it would drag in every prose slash. A bare `..` with
# no `/` is dropped (see `extract_paths`), which is what keeps `...`, `v1..v2`
# and `HEAD~2..HEAD` out; in each of those the `..` is preceded by an excluded
# character anyway.
_REL_ESCAPE_RE = re.compile(
    r"(?<![A-Za-z0-9_.~*/@+$}-])"
    r"((?:[A-Za-z0-9_.~+@-]+/)*\.\.(?:/[A-Za-z0-9_.~+@-]+)*/?)"
)

# Sentence punctuation that sticks to the end of a path in prose. A `/` is NOT
# stripped — a trailing slash is part of the path.
_TRAILING_PUNCT = ".,;:!?)]}'\"`"

# `kind` distinguishes a path NAMED IN THE BRIEF from one handed to opencode as
# an `--file` ATTACHMENT, because the operator's fix differs: reword the brief,
# versus drop or move the attachment.
Offender = namedtuple("Offender", "text resolved kind")


def canon(path) -> str:
    """Canonical form for the CONTAINMENT comparison — symlinks resolved.

    `os.path.realpath` on a path that does not exist resolves the symlinks in
    whatever prefix DOES exist and leaves the rest lexically — which is the
    behaviour wanted here, since a brief routinely names files the dispatch is
    about to create.
    """
    return os.path.realpath(os.path.expanduser(str(path)))


def lexical(path) -> str:
    """Canonical form for the ALLOWLIST comparison — symlinks NOT resolved.

    Deliberately different from `canon`: see the SYSTEM_ALLOW header. Resolving
    symlinks here made `/etc/hosts` land in `/nix/store` and pass.
    """
    return os.path.normpath(os.path.expanduser(str(path)))


def extract_unresolved_paths(text: str) -> list[str]:
    """Variable-interpolated path tokens — reported UNMEASURED, never clean."""
    out, seen = [], set()
    for m in _VAR_PATH_RE.finditer(_URL_RE.sub(" ", text)):
        tok = m.group(0).rstrip(_TRAILING_PUNCT)
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _mask(text: str) -> str:
    """Text with URLs and variable-interpolated paths blanked out.

    Both are handled BEFORE `_PATH_RE` runs, so a fragment of either cannot be
    mistaken for a real path. One masking step, so `extract_paths` and
    `extract_unresolved_paths` cannot disagree about which spans are which.
    """
    return _VAR_PATH_RE.sub(" ", _URL_RE.sub(" ", text))


def extract_paths(text: str) -> list[str]:
    """Every RESOLVABLE path-shaped token in `text`, in order, deduped.

    Three shapes: absolute (`/a/b`), home-relative (`~/a`), and a relative token
    carrying a `..` segment (`../a`, `a/../../b`). Variable-interpolated tokens
    are NOT here — they are `extract_unresolved_paths`.

    Pure and independently testable — the positive controls in the test suite
    call this directly, so a scanner wired to nothing cannot report a reassuring
    zero.
    """
    masked = _mask(text)
    out, seen = [], set()
    for rx in (_PATH_RE, _REL_ESCAPE_RE):
        for m in rx.finditer(masked):
            cand = m.group(1).rstrip(_TRAILING_PUNCT)
            # 🔴 A bare `..` (an ellipsis, `v1..v2`) is dropped HERE, by the
            # rstrip above emptying it — `_TRAILING_PUNCT` contains `.`, and
            # `_REL_ESCAPE_RE`'s only slashless token is exactly `..`.
            #
            # An explicit `if "/" not in cand: continue` guard USED to sit below
            # this line. It was DEAD: a mutation sweep removed it and the full
            # suite stayed green, because this check had already consumed every
            # input it could see. Its comment claimed to be what kept `...` out,
            # which was false. The coupling to `_TRAILING_PUNCT` is real and now
            # asserted by test_dispatch.py rather than left implicit.
            if not cand or cand in ("~/", "/"):
                continue
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def is_allowlisted(path_as_written: str) -> str | None:
    """The SYSTEM_ALLOW prefix covering `path_as_written`, or None.

    🔴 Takes the LEXICAL form, not the realpath — see the SYSTEM_ALLOW header.
    """
    p = lexical(path_as_written)
    for prefix, reason in SYSTEM_ALLOW.items():
        if p == prefix or p.startswith(prefix + os.sep):
            return reason
    return None


def is_under(resolved: str, root: str) -> bool:
    """🔴 The whole predicate. Exact: a path is under `root` or it is not.

    `os.sep` matters — a bare `startswith` would call `/home/zach/devrc-other`
    a child of `/home/zach/devrc`.
    """
    return resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep)


def resolve_candidate(cand: str, directory) -> str:
    """A candidate's absolute form. A RELATIVE one resolves against `--dir`,
    because that is the cwd opencode runs in."""
    if cand.startswith(("/", "~")):
        return canon(cand)
    return canon(os.path.join(str(directory), cand))


def _judge(cand: str, directory, root: str, kind: str):
    """`Offender` if `cand` escapes `--dir`, else None. ONE predicate, so the
    brief scan and the attachment scan cannot disagree."""
    resolved = resolve_candidate(cand, directory)
    if is_under(resolved, root):
        return None
    # Only an absolute/`~` token can be system-allowlisted; a relative one that
    # escaped `--dir` is an offender whatever it lands on.
    if cand.startswith(("/", "~")) and is_allowlisted(cand):
        return None
    return Offender(cand, resolved, kind)


def scan_paths(text: str, directory) -> list[Offender]:
    """Paths in `text` that are NOT under `directory` and not system-allowlisted.

    An empty list means every path the scanner COULD resolve is containable —
    which is not the same as "the brief is clean", and the report must not say
    otherwise when nothing was examined.
    """
    root = canon(directory)
    return [o for o in (_judge(c, directory, root, "brief")
                        for c in extract_paths(text)) if o]


def scan_attachments(attachments, directory) -> list[Offender]:
    """🔴 The same predicate, applied to `--file` ATTACHMENTS.

    Measured before this existed: `run --dir <proj> --file <outside>/extra.md`
    printed "external paths : none — every path is under --dir" and handed
    opencode the outside file. `--file` is the #3 most-used flag and SKILL.md
    advertises it, so this was the advertised path.

    Whether opencode itself raises an `external_directory` ask for an attachment
    is UNVERIFIED. It does not matter: the printed claim was false either way,
    and a report that overstates what it checked is the defect.
    """
    root = canon(directory)
    return [o for o in (_judge(str(a), directory, root, "attachment")
                        for a in attachments) if o]


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

# 🔴 The prefix a guard_core failure wears, so it is IMPOSSIBLE to confuse with
# a clean brief. The channel used to sit behind a bare `except: guard_reason =
# None` — a mutant making `evaluate` raise SURVIVED all 717 tests, because a
# broken channel and a brief with no dangerous commands both printed nothing.
# `evaluate` raises on an unknown policy name BY DESIGN ("a typo must not
# silently degrade to no checks"), so a rename of the "opencode" policy is
# exactly the shape that would have gone dark forever.
GUARD_UNMEASURED = "COULD NOT MEASURE"


def _guard_verdict(cmd: str, directory) -> str | None:
    """guard_core's "opencode"-policy verdict, or a loud `COULD NOT MEASURE`.

    Never raises — this is an advisory channel and must not break a preflight —
    but a failure is REPORTED, never swallowed into a reassuring None.
    """
    try:
        return guard_core.evaluate(
            cmd, policy="opencode",
            cwd=str(directory) if directory else None)
    except Exception as e:  # noqa: BLE001 — reported, not swallowed
        return f"{GUARD_UNMEASURED}: guard_core.evaluate raised {type(e).__name__}: {e}"


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
            guard_reason = _guard_verdict(cmd, directory)
            if action in ("ask", "deny") or guard_reason:
                out.append(Verdict(cmd, action, pattern, guard_reason))
    return out
