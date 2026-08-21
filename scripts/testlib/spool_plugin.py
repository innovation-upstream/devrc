#!/usr/bin/env python3
"""The ONE place that makes "a test cannot write to the REAL activity spool"
true — for EVERY target, not for one directory.

🔴 WHAT LEAKS, AND WHERE IT ENDS UP
------------------------------------
`scripts/collector/invocation.py` emits one `source=tool kind=invocation` row
per tool run, and several suites drive tools that call it. The row is appended
to `<ACTIVITY_SPOOL_DIR>/current.log`; `spool_emit.default_spool_dir()` resolves
that env var **at call time** and otherwise falls back to
`${XDG_STATE_HOME:-~/.local/state}/activity/spool` — which the activity
collector daemon ships to the production ClickHouse `activity.events`. A test
run therefore writes real rows into a real dataset, where they are
indistinguishable from the operator's own activity.

🔴 WHY THIS IS A PLUGIN AND NOT N CONFTESTS
--------------------------------------------
#614 fixed `scripts/browser-bridge/tests` with a `conftest.py`. That closed one
directory. A full `scripts/gate.sh --tier pytest` run still leaked, because
`run-tests.sh` runs ONE pytest process per target and there are two dozen of
them — thirteen test directories under `scripts/` have `.py` tests and no
conftest at all, so the class recurs at every one of them and at every target
added next month. `claude/RULES.md` → "a count of enforcement DECLARATIONS is
not a count of protected INSTANCES", and `run-tests.sh`'s GUARD 7 header records
the same lesson from #399, which protected 1 target of 17.

So the isolation is installed at the single place every target is invoked — the
env exports in `run-tests.sh` — which covers the NON-pytest targets
(`HOOK_TESTS`, `SHELL_TESTS`) that no conftest can ever reach. This module is
the pytest-side half: it is what makes the protection OBSERVABLE per target,
and it re-installs the isolation for a session that is not under the runner.

🔴 THE POSITIVE CONTROL, WHICH IS THE POINT OF THE FILE
--------------------------------------------------------
"0 rows leaked" is the observable that a working guard and a guard wired to
nothing SHARE. Two per-target records make them distinguishable, and
`run-tests.sh` REQUIRES both:

  * the SESSION MARKER (`spool(session)`) — one line per pytest session,
    recording the `ACTIVITY_SPOOL_DIR` this process actually saw. A target with
    no marker is a target the guard never loaded in, and the runner fails it
    rather than reading its zero as protection.

  * the FALLBACK CONTROL — one row deliberately emitted down the REAL fallback
    path (`ACTIVITY_SPOOL_DIR` absent, resolved by `spool_emit` itself, not by a
    restatement of its rule). It must land in the trap the runner set up. That
    is "feed it a case that MUST produce a non-zero count and watch the number
    move", per target: it proves the leak counter for THIS target can move at
    all, so its zero for real rows means something.

The control is FAIL-CLOSED: if the resolved fallback is not inside the guard
directory, nothing is emitted (it would land in production) and the marker
records `control=WITHHELD-UNCONTAINED`, which the runner turns into a loud
failure. A guard that writes to production to prove it does not write to
production would be worse than no guard.

🔴 IT DELIBERATELY NEVER UNDOES ITSELF — the same lesson as
`scripts/browser-bridge/tests/conftest.py`'s session backstop. `emit_cmd_event`
and friends run OFF the critical path, so a thread can still be on its way to an
emit when a fixture tears down; restoring the ambient value at teardown reopens
the hole for exactly the rows that are hardest to see. The variable is
process-local and the process is exiting.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

# The handle `run-tests.sh` exports so a session under the runner can find the
# ONE guard directory and write into the ONE ledger the runner reads. Same shape
# as nolaunch_plugin.STUB_DIR_ENV, and for the same reason: per-target
# attribution needs one log, not one per session.
GUARD_DIR_ENV = "DEVRC_TEST_SPOOL_GUARD_DIR"

# The two variables that decide where a spool write lands. BOTH are levers:
# ACTIVITY_SPOOL_DIR is the direct one, XDG_STATE_HOME governs the FALLBACK that
# a test taking the variable away (monkeypatch.delenv, a hand-built subprocess
# env) drops through.
SPOOL_ENV = "ACTIVITY_SPOOL_DIR"
STATE_ENV = "XDG_STATE_HOME"

SESSIONS_LOG = "sessions.log"
SESSION_MARKER = "spool(session)"

# The control row's `source`. `spool_emit` writes `source=` as a PLAIN scalar
# (it is in its _PLAIN_KEYS), so this string appears verbatim in the line and
# the runner can separate controls from leaks with a fixed-string grep. It is
# deliberately not any real source name in `activity.events`.
CONTROL_SOURCE = "devrc-spool-guard"
CONTROL_KIND = "fallback-control"

CONTROL_EMITTED = "emitted"
CONTROL_WITHHELD = "WITHHELD-UNCONTAINED"

# `scripts/collector/keylog/spool_emit.py` is the single source of truth for
# both the fallback RULE and the line format. It is loaded by path rather than
# by `sys.path` insertion so this plugin cannot shadow, or be shadowed by, a
# test's own `import spool_emit` (several suites do exactly that).
SPOOL_EMIT_PATH = (Path(__file__).resolve().parents[1]
                   / "collector" / "keylog" / "spool_emit.py")


def load_spool_emit():
    """Import `spool_emit` from its path, or return None if that is impossible.

    None is a real outcome, not a swallowed error: the runner is told about it
    through the marker's `control=` field and fails the target, because a
    session that could not load the emitter also could not run its own control.
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "_devrc_spool_guard_emit", SPOOL_EMIT_PATH)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001 — an unimportable emitter must not crash pytest
        return None


def resolve_fallback(se) -> Path:
    """Where a write lands when `ACTIVITY_SPOOL_DIR` is ABSENT.

    Asked of `spool_emit` itself rather than restated here — a second copy of
    the rule would agree with the first right up until the rule changed, and the
    trap would then be pointed at a directory nothing writes to while still
    reporting a reassuring zero.

    The variable is removed for the duration of one function call and restored
    in a `finally`. That window is microseconds wide and opens at session start,
    before any test body or server thread exists; it is named here rather than
    hidden because it is the one moment this file is not protected by itself.
    """
    prev = os.environ.pop(SPOOL_ENV, None)
    try:
        return se.default_spool_dir()
    finally:
        if prev is not None:
            os.environ[SPOOL_ENV] = prev


def _contained(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or lives under it, symlinks resolved."""
    try:
        c = Path(child).resolve()
        p = Path(parent).resolve()
    except OSError:  # pragma: no cover — an unresolvable path is not contained
        return False
    return c == p or p in c.parents


def _write_marker(guard_dir: Path, fields: dict[str, str]) -> None:
    """Append one TAB-separated session marker to `<guard_dir>/sessions.log`.

    TAB-separated because the values are filesystem paths: a space-separated
    record would be mis-parsed under a TMPDIR containing a space, and the
    mis-parse would read as "this target ran with the wrong spool dir".
    """
    line = SESSION_MARKER + "".join(f"\t{k}={v}" for k, v in fields.items())
    try:
        guard_dir.mkdir(parents=True, exist_ok=True)
        with (guard_dir / SESSIONS_LOG).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:  # pragma: no cover — an unwritable guard dir fails the run
        pass          # via the missing marker, which is the louder signal


def _inherited_guard_dir() -> Path | None:
    """The guard dir `run-tests.sh` already installed, if this session is under it.

    Verified, not trusted — the same check `nolaunch_plugin` makes: an env var
    naming a directory that does not exist would silently downgrade this session
    to no accounting at all.
    """
    raw = os.environ.get(GUARD_DIR_ENV)
    if not raw:
        return None
    d = Path(raw)
    return d if d.is_dir() else None


def install(guard_dir: Path) -> None:
    """Point BOTH levers inside `guard_dir`. Never undone — see the header.

    Unconditional, including over an ambient value. A test runner that honoured
    an inherited `ACTIVITY_SPOOL_DIR` would write real rows the moment someone's
    shell had the collector's env sourced, and that is a likelier accident than
    a deliberate intent.
    """
    os.environ[SPOOL_ENV] = str(guard_dir / "isolated")
    os.environ[STATE_ENV] = str(guard_dir / "xdg")


@pytest.fixture(scope="session", autouse=True)
def no_real_activity_spool(tmp_path_factory):
    """Isolate the activity spool for the whole session, and record that it was.

    AUTOUSE and session-scoped: protection a test has to ask for is protection
    the next test forgets, and per-test isolation was measured (#614) to have a
    teardown-shaped hole that only a session-wide floor closes.
    """
    guard_dir = _inherited_guard_dir()
    if guard_dir is None:
        # Not under the runner (a bare `pytest <dir>`). The protection must not
        # be conditional on the runner, so install our own — the accounting is
        # what the runner adds, not the isolation.
        guard_dir = Path(tmp_path_factory.mktemp("spool-guard"))
        install(guard_dir)

    note = " ".join(sys.argv[1:]) or "(no args)"
    se = load_spool_emit()
    if se is None:
        fallback, control = "(spool_emit-unimportable)", CONTROL_WITHHELD
    else:
        fb = resolve_fallback(se)
        fallback = str(fb)
        if _contained(fb, guard_dir):
            se.emit({"source": CONTROL_SOURCE, "kind": CONTROL_KIND,
                     "text": note}, spool_dir=fb)
            control = CONTROL_EMITTED
        else:
            # FAIL-CLOSED. Emitting here would put the guard's own row in the
            # operator's production dataset.
            control = CONTROL_WITHHELD

    _write_marker(guard_dir, {
        "isolated": os.environ.get(SPOOL_ENV, "(unset)"),
        "fallback": fallback,
        "control": control,
        "args": note,
    })
    yield guard_dir
