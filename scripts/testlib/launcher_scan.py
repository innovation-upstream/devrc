#!/usr/bin/env python3
"""Deterministic scans behind the no-real-launchers fixture.

Two hand-written lists were found wrong by a human review, in the same round:

  * the LEDGER (`nolaunch.HOST_LAUNCHERS`) was pinned against itself — it
    asserted "these are the entries" and nothing asserted "these are the
    launchers the scripts actually reach". `dunstctl`, `rofi` and `yad` were all
    reachable and none was listed. A list that only agrees with itself is a
    tautology; `claude/RULES.md` → "a count of DECLARATIONS is not a count of
    INSTANCES".
  * the PATH-CLOBBER sites (`env={"PATH": "/usr/bin/false"}`) are the one shape
    the fixture cannot cover: they replace the ambient PATH wholesale, so the
    stub dir is not in the child's PATH at all. Two exist today and both are
    harmless — nothing they can reach — but the third one added tomorrow is
    unprotected and nothing goes red.

🔴 STATED LIMIT, because this scan can be believed too much: `HAZARD_VOCABULARY`
is a list of NAMES. It cannot see a launcher nobody has thought of, and it
matches the name anywhere on a line — a mention in a comment counts. That
over-approximation is deliberate and FAIL-CLOSED: an unacknowledged name is a
failure, and the fix is one line in an ACKNOWLEDGED table saying why it is safe.
What this closes is the case that actually happened (a real invocation nobody
put in the ledger), not the general problem.

SCOPE: the TOP LEVEL of `scripts/` only — the desktop/session layer that
`scripts/tests` executes as subprocesses (rig-control.sh, monitor-blackout.sh,
notif-center, the i3status-* pills, the menus). Subdirectories are their own
suites with their own conftests and are deliberately out of scope; see the
"not verified" list in the PR that added this.
"""
from __future__ import annotations

import re
from pathlib import Path

# Binaries that act on the operator's live session, machine or configuration.
# Wider than the ledger on purpose: a name here that is NOT stubbed must be
# acknowledged in the test's ACKNOWLEDGED table with a reason.
HAZARD_VOCABULARY = (
    "systemd-run", "systemctl", "loginctl",
    "notify-send", "dunstify", "dunstctl",
    "rofi", "yad", "zenity", "wmctrl", "xdotool", "i3-msg",
    "xdg-open", "openrgb", "ddcutil", "brightnessctl", "xset",
    "pactl", "playerctl", "espanso",
    "home-manager", "nixos-rebuild",
)

# A PATH value that mentions any of these is DERIVED from the ambient PATH, so
# the stub dir travels with it. Anything else replaces PATH wholesale.
_INHERITS = (
    "os.environ", "environ[", "PATH_ENV", "$PATH", "getenv",
    # `env["PATH"] = f"{d}:{env['PATH']}"` — the ambient value is carried by a
    # dict copied from os.environ earlier, so the RHS naming PATH at all means
    # the child keeps whatever the parent had, stub dir included.
    '"PATH"', "'PATH'",
)

# Both shapes that occur: a dict entry (`{"PATH": …}`) and a subscript
# assignment (`env["PATH"] = …`). The optional `]` is what makes the second one
# visible — without it the scan silently missed test_claude_log_rotate.py.
_PATH_ASSIGN = re.compile(r"""["']PATH["']\s*\]?\s*[:=]\s*(?P<value>.+)$""")
# The value as written, up to the end of that dict entry / statement.
_VALUE_HEAD = re.compile(r"^\s*(?P<expr>[^,}]+)")
_BARE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def top_level_scripts(scripts_root: Path) -> list[Path]:
    """Every readable file directly under `scripts/` (no subdirectories)."""
    out = []
    for p in sorted(Path(scripts_root).iterdir()):
        if p.is_file() and p.suffix not in (".md", ".json", ".lock"):
            out.append(p)
    return out


def hazard_hits(scripts_root: Path) -> dict[str, list[str]]:
    """`{binary: [script names…]}` for every vocabulary name found."""
    hits: dict[str, list[str]] = {}
    for path in top_level_scripts(scripts_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover — unreadable file
            continue
        for name in HAZARD_VOCABULARY:
            if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", text):
                hits.setdefault(name, []).append(path.name)
    return hits


def path_clobbers(tests_dir: Path) -> list[tuple[str, int, str]]:
    """`(file, lineno, line)` for every PATH assignment that DROPS the ambient PATH.

    These are the sites the fixture cannot reach, so each one must be pinned by
    the guard test with a reason it is safe.
    """
    out = []
    for py in sorted(Path(tests_dir).glob("test_*.py")):
        text = py.read_text(encoding="utf-8")
        lines = text.splitlines()
        for lineno, line in enumerate(lines, 1):
            m = _PATH_ASSIGN.search(line)
            if not m:
                continue
            value = m.group("value")
            # A RHS can continue onto the next lines — `clean_env["PATH"] = (\n
            # os.pathsep.join(…os.environ…))`. Reading only the first line calls
            # that a clobber (it happened, in this repo's own guard file). Keep
            # taking lines while brackets are unbalanced, bounded so a syntax
            # error cannot make this walk the file.
            depth = value.count("(") + value.count("[") + value.count("{") \
                - value.count(")") - value.count("]") - value.count("}")
            look = lineno
            while depth > 0 and look < len(lines) and look - lineno < 10:
                nxt = lines[look]
                value += " " + nxt
                depth += nxt.count("(") + nxt.count("[") + nxt.count("{") \
                    - nxt.count(")") - nxt.count("]") - nxt.count("}")
                look += 1
            if any(tok in value for tok in _INHERITS):
                continue
            # `env={"PATH": path, …}` where `path` was built from os.environ two
            # lines up is NOT a clobber. Resolve that one indirection — it is the
            # dominant shape in this suite (12 of 14 raw hits), and reporting it
            # would drown the two real ones.
            head = _VALUE_HEAD.match(value)
            expr = head.group("expr").strip() if head else value.strip()
            if _BARE_NAME.match(expr):
                assign = re.compile(
                    r"^\s*" + re.escape(expr) + r"\s*=\s*(?P<rhs>.+)$", re.M)
                if any(tok in mm.group("rhs")
                       for mm in assign.finditer(text) for tok in _INHERITS):
                    continue
            out.append((py.name, lineno, line.strip()))
    return out
