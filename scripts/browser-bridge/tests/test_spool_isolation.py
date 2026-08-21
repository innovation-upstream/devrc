"""THE regression guard for the production-telemetry leak.

WHY THIS IS ITS OWN FILE
------------------------
The bug was not a missing fixture — the fixture existed, was `autouse`, and its
docstring said it covered "EVERY test". It was declared inside `test_server.py`,
so pytest scoped it to that module while the other EIGHT modules in this
directory ran unisolated, appending to `<ACTIVITY_SPOOL_DIR>/current.log`, which
the activity collector ships to the production ClickHouse `activity.events`.

🔴 MEASURED, because the count that first circulated was wrong and inflated.
Running each module alone at `origin/main`: `test_site_notes.py` wrote **17**
rows per run, `test_server.py` 0-1 (the teardown race), and the other seven **0**
— unisolated but LATENT, since they never reach an emit. Exactly one sibling was
leaking, not five. The five came from grepping for files that touch `server`,
and a grep counts declarations, never instances.

A guard for that living in `test_server.py` would be worthless: it would pass
for exactly the reason the bug existed. THE VALUE OF THE FIRST TWO TESTS IS
THEIR LOCATION — they can only pass if `conftest.py`'s `_isolate_activity_spool`
reaches a module other than `test_server.py`. Do not move them into another
file. (The third test targets `_session_spool_backstop` instead and carries no
such argument; this file does declare one fixture of its own, `session_backstop_
value`, purely to read that backstop's value — it isolates nothing.)

WHAT IS ASSERTED, AND WHY IT IS THE RELATIONSHIP AND NOT THE SPELLING
--------------------------------------------------------------------
"`ACTIVITY_SPOOL_DIR` is set" is walkable: a developer (or CI, or the
`scripts/gate.sh` wrapper) with the variable exported in the ambient shell
satisfies it while every test in the run still shares ONE directory outside the
tmp tree. So the structural test asserts the EFFECTIVE dir — what
`spool_emit.default_spool_dir()` actually returns — is under *this test's own*
`tmp_path`. An ambient value cannot satisfy that; only the per-test fixture can.

And a structural check type-checks past a wrong argument, so the second test is
behavioural: drive the real `server.emit_cmd_event()` writer, prove the row
landed in the temp spool, and prove the same row is NOT in the production spool.
🔴 Both halves call `_decoded_lines` with the SAME `only_source` filter, and that
is load-bearing rather than tidy. With the filter applied on the production side
only, a change to `emit_cmd_event`'s `rec["source"]` (server.py:1309) would make
the production match set permanently empty — the leak assertion would pass
vacuously while the tmp assertion, unfiltered, stayed green. Filtering both makes
the tmp assertion a positive control for the exact predicate the production
assertion depends on: get the source token wrong and the tmp half reds first.

🔴 WHAT THESE TWO TESTS STRUCTURALLY CANNOT SEE, so nobody reads them as wider
than they are: the LATE EMIT. `emit_cmd_event()` runs after the HTTP response is
sent, so a lingering handler thread can write its row after the test body — and
therefore after every assertion here — has finished. That row escapes through
the per-test fixture's TEARDOWN, not through anything either test inspects. It
is closed by `conftest.py`'s session-scoped `_session_spool_backstop`, which
makes the value teardown restores to safe. The evidence for THAT is a
deterministic probe recorded in the conftest docstring — explicitly not a
repeat-run leak count, which proved unable to tell a fix from a race that simply
did not fire (0/20 leaks on known-broken code), and not an assertion here.
"""
from __future__ import annotations

import base64
import importlib.util
import os
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

# The in-repo emitter — same path `test_server.py` pins, and the same reason:
# `server._SPOOL_EMIT_PATH` otherwise points at the operator's ~/workspace
# checkout, which does not exist in the nix check sandbox (telemetry would
# silently disable itself and the behavioural test would assert nothing).
SPOOL_EMIT_PY = (Path(__file__).resolve().parent.parent.parent
                 / "collector" / "keylog" / "spool_emit.py")


def _spool_emit_module():
    """Load the real `spool_emit` fresh, by path (never from sys.modules)."""
    spec = importlib.util.spec_from_file_location(
        "spool_emit_under_isolation_test", str(SPOOL_EMIT_PY))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _production_spool_dir() -> Path:
    """Where `default_spool_dir()` lands with ACTIVITY_SPOOL_DIR UNSET — i.e. the
    real spool the collector ships. Computed by calling the production function
    with the variable removed, not by re-spelling `~/.local/state/...` here, so
    the guard follows the fallback (XDG_STATE_HOME included) rather than a copy
    of it that could drift."""
    with mock.patch.dict(os.environ):
        os.environ.pop("ACTIVITY_SPOOL_DIR", None)
        return _spool_emit_module().default_spool_dir()


def _decoded_lines(path: Path, only_source: str | None = None) -> list[str]:
    """Spool lines with their `b64:` fields decoded, so a plaintext marker can be
    found. `only_source` pre-filters on the PLAIN `source=` field before any
    decoding — the production spool also carries keylog rows, and this guard has
    no business decoding those."""
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        if only_source is not None and f"source={only_source}" not in raw:
            continue
        parts = []
        for field in raw.split("\t"):
            if field.startswith("b64:") and "=" in field:
                key, _, val = field[4:].partition("=")
                try:
                    parts.append(
                        key + "=" + base64.b64decode(val).decode("utf-8",
                                                                 "replace"))
                    continue
                except Exception:  # noqa: BLE001 — undecodable stays verbatim.
                    pass
            parts.append(field)
        out.append("\t".join(parts))
    return out


@pytest.fixture(scope="session")
def session_backstop_value(_session_spool_backstop):
    """The ACTIVITY_SPOOL_DIR the SESSION backstop installed — i.e. the value a
    per-test teardown restores to. Requested by NAME so it is ordered after the
    conftest fixture that sets it."""
    return os.environ.get("ACTIVITY_SPOOL_DIR")


def test_the_value_a_per_test_teardown_restores_to_is_not_production(
        session_backstop_value):
    """The session backstop's invariant, as far as it is checkable in-suite.

    🔴 SCOPE, stated so nobody reads this as wider than it is: this catches the
    backstop being DELETED or pointed somewhere unsafe. It does NOT catch the
    regression that actually bit — a `yield` + `mp.undo()` in the backstop,
    which restores the ambient value at SESSION teardown and so leaks a
    post-session late emit to production while this assertion, running mid-
    session, stays perfectly green. That one is held by the deterministic probe
    recorded in the conftest docstring, not by any assertion here.
    """
    assert session_backstop_value is not None, (
        "no ACTIVITY_SPOOL_DIR at session scope — conftest.py's "
        "`_session_spool_backstop` did not run, so a late emit arriving between "
        "tests or after the last one falls through to the production spool.")
    restored = Path(session_backstop_value).resolve()
    assert restored != _production_spool_dir().resolve(), (
        f"the session backstop points AT the production spool ({restored}).")


def test_the_effective_spool_dir_is_under_this_test_s_own_tmp_path(tmp_path):
    """STRUCTURAL. Not "the env var is set" — an ambient export satisfies that."""
    effective = _spool_emit_module().default_spool_dir().resolve()
    root = tmp_path.resolve()
    assert effective.is_relative_to(root), (
        "ACTIVITY SPOOL IS NOT ISOLATED TO THIS TEST.\n"
        f"  spool_emit.default_spool_dir() -> {effective}\n"
        f"  this test's tmp_path           -> {root}\n"
        f"  the production spool           -> {_production_spool_dir()}\n"
        "The autouse `_isolate_activity_spool` fixture in "
        "scripts/browser-bridge/tests/conftest.py did not run for THIS file. "
        "If it was moved back into a test module, pytest scoped it to that "
        "module and every other file in this directory is writing real rows "
        "into the production activity pipeline.")


def test_a_real_emit_lands_in_tmp_and_never_in_the_production_spool(
        tmp_path, monkeypatch):
    """BEHAVIOURAL. Drives the actual production writer, `emit_cmd_event()`."""
    production = _production_spool_dir()
    production_existed = production.exists()
    marker = "spool-isolation-guard-" + uuid.uuid4().hex

    monkeypatch.setattr(S, "_SPOOL_EMIT_PATH", SPOOL_EMIT_PY)
    monkeypatch.setattr(S, "_spool_emit_mod", None)
    monkeypatch.setattr(S, "_spool_emit_tried", False)
    S.emit_cmd_event("read", marker, "ok", 1, domain="")

    # 🔴 THIS IS THE ASSERTION THAT CARRIES THE TEST. Same `only_source` filter
    # as the production check below — see the module docstring: it makes this
    # line the positive control for that predicate.
    tmp_log = tmp_path / "activity-spool" / "current.log"
    written = _decoded_lines(tmp_log, only_source="browser-bridge")
    assert any(marker in ln for ln in written), (
        f"emit_cmd_event() did not write into the per-test spool {tmp_log} "
        f"({len(written)} browser-bridge line(s) found there). Either the "
        "isolation fixture did not run for this file, the emit path is broken, "
        "or the event's `source` is no longer 'browser-bridge' — in the first "
        "case the row went to the PRODUCTION spool instead.")

    # 🔴 BELT-AND-BRACES, NOT COVERAGE. Both checks below are effectively
    # unreachable: every failure mode that would put the row in the production
    # spool leaves the per-test spool empty, so the assertion above fires first
    # and this code never runs. They are kept because they name the actual
    # hazard at the place a reader looks for it, and because "unreachable today"
    # is a property of the current failure ordering, not a guarantee. Do not
    # count either one as regression coverage.
    production_log = production / _spool_emit_module().CURRENT_NAME
    leaked = _decoded_lines(production_log, only_source="browser-bridge")
    # Marker only — the failure message must never quote a production row.
    assert not any(marker in ln for ln in leaked), (
        f"A TEST EMIT REACHED THE PRODUCTION ACTIVITY SPOOL ({production_log}). "
        f"The marker {marker} written by this test is present there; the "
        "collector ships that spool to ClickHouse activity.events.")
    if not production_existed:
        assert not production.exists(), (
            f"A TEST EMIT CREATED THE PRODUCTION ACTIVITY SPOOL {production}. "
            "It did not exist before this test ran, so the emitter's "
            "mkdir(parents=True) resolved to the real path, not tmp_path.")
