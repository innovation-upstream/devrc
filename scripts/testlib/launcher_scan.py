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

import ast
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

# NOTE: the PATH-clobber scan is AST-based (see `path_clobbers`). It used to be
# a line regex, and every false negative a review found came from that: a line
# has a TAIL, so `{"PATH": "/usr/bin/false", "HOME": os.environ["HOME"]}` and
# even a trailing `# os.environ` comment read as "inherits". A syntax tree has
# neither tails nor comments.


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


def _inherits(node: ast.AST, module: ast.Module, depth: int = 0) -> bool:
    """True when this PATH value carries the AMBIENT PATH along with it.

    Direct signals, read from the SYNTAX TREE rather than the line text:
      * the expression mentions `os.environ` / `getenv` anywhere in its subtree;
      * it mentions the string "PATH" (i.e. `env["PATH"]`, `env.get("PATH")`),
        which means it is building on whatever the parent had.

    Plus TWO levels of indirection, because both occur in this suite and both
    would otherwise be reported as clobbers they are not:
      * a VARIABLE — `env={"PATH": path}` with `path` assigned from os.environ;
      * a LOCAL FUNCTION's return — `_run(store, PATH=_shim(tmp_path))`, where
        the helper returns `f"{bindir}:{os.environ['PATH']}"`.
    A deeper or cross-module chain is NOT resolved: it reports, and the pin
    table is where someone says why it is safe. Erring toward reporting is the
    right direction for a scan whose failure mode is a missed clobber.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in ("environ", "getenv"):
            return True
        if isinstance(sub, ast.Name) and sub.id in ("environ", "getenv"):
            return True
        if isinstance(sub, ast.Constant) and sub.value == "PATH":
            return True
    if depth >= 2:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            for other in ast.walk(module):
                if isinstance(other, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id == sub.id
                        for t in other.targets):
                    if _inherits(other.value, module, depth + 1):
                        return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            for other in ast.walk(module):
                if (isinstance(other, ast.FunctionDef)
                        and other.name == sub.func.id):
                    for ret in ast.walk(other):
                        if (isinstance(ret, ast.Return) and ret.value is not None
                                and _inherits(ret.value, module, depth + 1)):
                            return True
    return False


def _path_values(module: ast.Module):
    """Every expression assigned to a PATH key, in any of the shapes that occur.

    🔴 The line-based version this replaces missed FOUR shapes a review measured,
    all of which drop the ambient PATH silently:
        {"PATH": "/usr/bin/false", "HOME": os.environ["HOME"]}   (later key)
        env["PATH"] = "/x"  # os.environ is fine here            (a COMMENT)
        env=dict(PATH="/x")                                      (kwarg)
        env.setdefault("PATH", "/x") / env.update(PATH="/x")     (method)
    The first two defeated it because it tested the whole line TAIL for an
    inheritance token. An AST has no line tails.
    """
    for node in ast.walk(module):
        # env["PATH"] = <value>
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "PATH"):
                    yield node, node.value
        # {"PATH": <value>}
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "PATH":
                    yield node, value
        elif isinstance(node, ast.Call):
            # dict(PATH=…) / env.update(PATH=…) / anything(PATH=…)
            for kw in node.keywords:
                if kw.arg == "PATH":
                    yield node, kw.value
            # env.setdefault("PATH", <value>)
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "setdefault"
                    and len(node.args) == 2
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "PATH"):
                yield node, node.args[1]


def path_clobbers(tests_dir: Path) -> list[tuple[str, int, str]]:
    """`(file, lineno, source)` for every PATH assignment that DROPS the ambient PATH.

    These are the sites the fixture cannot reach, so each one must be pinned by
    the guard test with a reason it is safe.

    Scope: `test_*.py` AND `conftest.py` — the latter was outside the old glob,
    which is where the fixture that does the protecting actually lives.
    """
    out = []
    files = sorted(Path(tests_dir).glob("test_*.py"))
    conftest = Path(tests_dir) / "conftest.py"
    if conftest.exists():
        files.append(conftest)
    for py in files:
        text = py.read_text(encoding="utf-8")
        try:
            module = ast.parse(text)
        except SyntaxError:  # pragma: no cover — a broken test file fails elsewhere
            continue
        seen = set()
        for node, value in _path_values(module):
            if _inherits(value, module):
                continue
            lineno = getattr(value, "lineno", getattr(node, "lineno", 0))
            if lineno in seen:
                continue
            seen.add(lineno)
            src = ast.get_source_segment(text, node) or ""
            out.append((py.name, lineno, " ".join(src.split())[:160]))
    return sorted(out)
