#!/usr/bin/env python3
"""Unit tests for `scripts/peer-host` — the cross-host peer -> machine lookup.

🔴 HERMETIC BY CONSTRUCTION, AND PROVEN SO
------------------------------------------
This suite runs on a live workbench holding the operator's real
`~/.claude/sessions` registry and real SSH access to the laptop. NOTHING here
may reach either. Three autouse fixtures enforce it rather than asserting it:

  1. `_no_real_subprocess` replaces `ph._default_runner` — the ONLY subprocess
     seam in the module — with a function that RAISES, so a test that forgets to
     inject a runner cannot open an SSH connection to the operator's laptop.
  2. `_no_real_registry` repoints `ph.DEFAULT_REGISTRY_DIR` at a path under
     `tmp_path` that does not exist, so a `lookup()` called with no directory
     cannot read the operator's real sessions.
  3. `_no_real_host_label_file` repoints `host_label`'s env file, so
     `local_host_label()` cannot read this machine's real collector config and
     make an assertion pass for an environmental reason.

`test_the_hermeticity_fixtures_are_actually_installed` is the POSITIVE CONTROL
on all three — a guard nobody has watched work is not a guard.

🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT, AND DISTINCT FROM EVERY ASSERTED
CONSTANT. A fixture that happens to equal the constant under test cannot see a
mutant that hardcodes the literal. Concretely here:

  * pids are 910001 / 910002 / 910003 / 910004 — six digits, no overlap with any
    exit code (0/2/3/4/64) or with `MIN_SESSION_ID_PREFIX` (6).
  * session ids are drawn from four disjoint hex prefixes (aaa.../bbb.../
    ccc.../ddd...) so a prefix assertion can only pass by reading the right one.
  * peer names are `alpha-11` / `beta-22` / `gamma-33`, none of which is a
    substring of another and none of which contains a host label — so a mutant
    that answered by matching the SELECTOR against the HOST NAME cannot pass.
  * the ambiguous pair deliberately spans TWO HOSTS as well as being duplicated
    on one, because "ambiguous" and "the hosts disagree" are different facts and
    a test that only ever saw the one-host case could not tell them apart.

🔴 WHICH TESTS ARE REGRESSION COVERAGE, AND AGAINST WHICH BASE. Everything in
this file is red at `origin/main` for the trivial reason that `scripts/peer-host`
does not exist there — so NONE of it is evidence that a pre-existing bug was
fixed, and it is not claimed as such. `RED_AT_BASE_FOR_A_REAL_DEFECT` names the
two that pin behaviour which was measured WRONG during this branch's own
development, i.e. the only two with a defect behind them:

  * `test_a_usage_error_exits_64_not_2` — argparse's own usage exit is 2, which
    is this tool's AMBIGUOUS code. Before the remap, a mistyped flag was
    indistinguishable from a real "this name matches two peers" answer.
  * `test_the_remote_program_puts_from_future_first` — splicing `host_label.py`
    into a larger program left its `from __future__` line mid-file, which is a
    hard SyntaxError. The leg then failed as "unreachable", i.e. the laptop
    looked DOWN when the bug was entirely local. Measured during development.

The MUTATION MATRIX this suite was checked against is `MUTATION_MATRIX` at the
bottom of this file — each mutant, and the test that kills it WITH ITS OWN
assertion.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent
_SCRIPT = _SCRIPTS / "peer-host"

sys.path.insert(0, str(_SCRIPTS / "lib"))
import host_label as hl  # noqa: E402

# peer-host has no .py extension -> load it by explicit path.
_spec = importlib.util.spec_from_loader(
    "peer_host", importlib.machinery.SourceFileLoader("peer_host", str(_SCRIPT)))
ph = importlib.util.module_from_spec(_spec)
# Register BEFORE exec_module, the same reason test_session_resolve.py does.
sys.modules["peer_host"] = ph
_spec.loader.exec_module(ph)


RED_AT_BASE_FOR_A_REAL_DEFECT = {
    "test_a_usage_error_exits_64_not_2",
    "test_the_remote_program_puts_from_future_first",
}


# =========================================================================== #
# Hermeticity harness
# =========================================================================== #
class _Forbidden(RuntimeError):
    """Raised when a test reaches for the real world."""


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    def _boom(argv, *, input_text=None, timeout=None):
        raise _Forbidden("test tried to run a real subprocess: %r" % (argv,))
    monkeypatch.setattr(ph, "_default_runner", _boom)


@pytest.fixture(autouse=True)
def _no_real_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(ph, "DEFAULT_REGISTRY_DIR",
                        str(tmp_path / "absent-registry"))


@pytest.fixture(autouse=True)
def _no_real_host_label_file(monkeypatch, tmp_path):
    monkeypatch.setattr(hl, "ACTIVITY_ENV", str(tmp_path / "absent-env"))
    monkeypatch.delenv("ACTIVITY_HOST", raising=False)


def test_the_hermeticity_fixtures_are_actually_installed(tmp_path):
    """POSITIVE CONTROL on all three autouse fixtures."""
    with pytest.raises(_Forbidden):
        ph._default_runner(["ssh", "somewhere"])
    assert not os.path.exists(ph.DEFAULT_REGISTRY_DIR)
    assert "absent-registry" in ph.DEFAULT_REGISTRY_DIR
    assert not os.path.exists(hl.ACTIVITY_ENV)
    # …and with no env file and no env var, the label falls back to the default
    # rather than reading this machine's real collector config.
    assert hl.local_host_label() == hl.DEFAULT_LOCAL_HOST


# =========================================================================== #
# Fixtures
# =========================================================================== #
WB_ALPHA = {"pid": 910001, "sessionId": "aaa1c4f0-0000-4000-8000-000000000001",
            "name": "alpha-11", "cwd": "/w/one", "kind": "interactive",
            "status": "idle", "tmux": "scratchA:@71.%71"}
WB_DUPE = {"pid": 910002, "sessionId": "bbb2c4f0-0000-4000-8000-000000000002",
           "name": "gamma-33", "cwd": "/w/two", "kind": "interactive",
           "status": "busy", "tmux": "scratchB:@72.%72"}
#: 🔴 NO `tmux` KEY AT ALL — not `None`, ABSENT. Every one of the 9 laptop
#: records measured on 2026-09-12 was shaped like this, and a reader that
#: assumed the key would have raised and reported the whole LAPTOP as
#: unreadable — i.e. one missing field would have cost the entire remote half of
#: the feature, which is the feature.
LT_BETA = {"pid": 910003, "sessionId": "ccc3c4f0-0000-4000-8000-000000000003",
           "name": "beta-22", "cwd": "/l/one", "kind": "interactive",
           "status": "idle"}
LT_DUPE = {"pid": 910004, "sessionId": "ddd4c4f0-0000-4000-8000-000000000004",
           "name": "gamma-33", "cwd": "/l/two", "kind": "interactive",
           "status": "idle"}
#: 🔴 THE SAME MISSING-`tmux` SHAPE, but on the LOCAL side so it goes through the
#: REAL READER. Found by the mutation sweep: `raw.get("tmux")` -> `raw["tmux"]`
#: SURVIVED, because the only tmux-less fixture reached the assertions through
#: the stubbed SSH leg as an already-shaped dict — the reader never saw it, so
#: the test's docstring claimed coverage the test did not provide.
WB_NOTMUX = {"pid": 910006, "sessionId": "fff6c4f0-0000-4000-8000-000000000006",
             "name": "delta-44", "cwd": "/w/three", "kind": "interactive",
             "status": "idle"}


def _registry(tmp_path, name, records):
    """Write `records` as `<pid>.json` files, plus a fake /proc for liveness."""
    reg = tmp_path / name / "sessions"
    reg.mkdir(parents=True)
    proc = tmp_path / name / "proc"
    proc.mkdir(parents=True)
    for rec in records:
        (reg / ("%d.json" % rec["pid"])).write_text(json.dumps(rec))
        (proc / str(rec["pid"])).mkdir()
    return str(reg), str(proc)


def _leg_stdout(label, records, *, stale=0, unreadable=0, error=None):
    """A well-formed remote answer: sentinel line, then the JSON payload."""
    payload = {"records": records, "stale_dropped": stale,
               "unreadable": unreadable, "dir": "/remote/sessions",
               "error": error}
    return "%s %s\n%s\n" % (ph.SENTINEL, label, json.dumps(payload))


def _shaped(rec):
    """A registry dict as the READER emits it (the reader renames the keys)."""
    return {"name": rec.get("name"), "session_id": rec.get("sessionId"),
            "pid": rec.get("pid"), "tmux": rec.get("tmux"),
            "cwd": rec.get("cwd"), "kind": rec.get("kind"),
            "status": rec.get("status")}


@pytest.fixture
def world(tmp_path):
    """Local = workbench (alpha-11, gamma-33); remote = laptop (beta-22).

    Returns a `lookup(**kw)` bound to that world, with the SSH leg answering
    from `laptop_records` — mutable per test, which is how the unreachable and
    mismatch cases are built without a second harness.
    """
    reg, proc = _registry(tmp_path, "wb", [WB_ALPHA, WB_DUPE])
    state = {"records": [_shaped(LT_BETA)], "label": "laptop",
             "rc": 0, "stdout": None, "raise": None, "calls": []}

    def runner(argv, *, input_text=None, timeout=None):
        state["calls"].append({"argv": list(argv), "input": input_text,
                               "timeout": timeout})
        if state["raise"] is not None:
            raise state["raise"]
        out = state["stdout"]
        if out is None:
            out = _leg_stdout(state["label"], state["records"])
        return state["rc"], out, "stub stderr"

    def _lookup(selector, **kw):
        kw.setdefault("local_host", "workbench")
        kw.setdefault("registry_dir", reg)
        kw.setdefault("proc_dir", proc)
        kw.setdefault("runner", runner)
        kw.setdefault("host_label_source", "def local_host_label(): pass\n")
        return ph.lookup(selector, **kw)

    _lookup.state = state
    _lookup.registry = reg
    _lookup.proc = proc
    return _lookup


# =========================================================================== #
# CRITERION 1 + 2 — a name resolves, on EITHER host
# =========================================================================== #
def test_a_local_peer_name_resolves_to_the_local_host(world):
    r = world("alpha-11")
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["host"] == "workbench"
    assert [m["pid"] for m in r["matches"]] == [WB_ALPHA["pid"]]
    assert r["matches"][0]["matched_by"] == [ph.KIND_NAME]


def test_a_remote_peer_name_resolves_to_the_REMOTE_host(world):
    """🔴 THE test that a single-host check cannot substitute for.

    A lookup hardcoded to the local machine passes
    `test_a_local_peer_name_resolves_to_the_local_host` perfectly. Only a name
    that exists ONLY on the other host can tell the two apart, so this asserts
    the answer is `laptop` — a value the local leg can never produce, because
    `local_host` is pinned to `workbench` in this world.
    """
    r = world("beta-22")
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["host"] == "laptop"
    assert r["host"] != r["local_host"]
    assert [m["pid"] for m in r["matches"]] == [LT_BETA["pid"]]


def test_a_record_with_NO_tmux_key_still_resolves(world):
    """LT_BETA carries no `tmux` field at all — the shape every laptop record
    had when measured. It must match by name and report `tmux: None`, not raise
    and not be dropped.

    ⚠ This exercises the MATCHING half only: LT_BETA arrives through the stubbed
    SSH leg as an already-shaped dict, so the READER never parses it. The reader
    is covered by `test_the_READER_parses_a_record_with_no_tmux_key`, which is
    the one a `raw["tmux"]` mutant dies on.
    """
    assert "tmux" not in LT_BETA
    r = world("beta-22")
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["matches"][0]["tmux"] is None


def test_the_READER_parses_a_record_with_no_tmux_key(world, tmp_path):
    """🔴 THE HALF THE TEST ABOVE CANNOT SEE, and the reason it exists.

    A `raw["tmux"]` instead of `raw.get("tmux")` raises KeyError on EVERY laptop
    record measured — i.e. one missing field costs the whole remote host, which
    is the whole feature. This drives a tmux-less record through the REAL
    reader, off a real directory, so the mutant actually executes.
    """
    assert "tmux" not in WB_NOTMUX
    reg, proc = _registry(tmp_path, "notmux", [WB_NOTMUX, WB_ALPHA])
    r = world("delta-44", registry_dir=reg, proc_dir=proc)
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["host"] == "workbench"
    assert r["matches"][0]["tmux"] is None
    assert r["coverage"]["workbench"]["status"] == ph.READ_OK
    assert r["coverage"]["workbench"]["unreadable"] == 0
    # …and the neighbour in the same directory is unharmed, so this is not a
    # reader that silently swallowed the whole read.
    assert r["records_seen"]["workbench"] == 2


# =========================================================================== #
# CRITERION 3 — no-match and ambiguity both refuse
# =========================================================================== #
def test_an_unknown_name_is_UNMATCHED_and_never_the_local_host(world):
    r = world("nothing-like-this")
    assert r["status"] == ph.STATUS_UNMATCHED
    assert r["host"] is None
    # Both hosts really were read — that is what makes this "not found" rather
    # than "could not look".
    assert r["hosts_searched"] == ["workbench", "laptop"]
    assert r["hosts_unsearched"] == []


def test_a_name_on_TWO_hosts_is_AMBIGUOUS(world):
    """`gamma-33` is live on the workbench AND the laptop. The tool must refuse
    rather than pick one — and must name both candidates."""
    world.state["records"] = [_shaped(LT_BETA), _shaped(LT_DUPE)]
    r = world("gamma-33")
    assert r["status"] == ph.STATUS_AMBIGUOUS
    assert r["host"] is None
    assert sorted(m["host"] for m in r["matches"]) == ["laptop", "workbench"]
    diag = ph.render_diagnostics(r)
    assert "refusing to guess" in diag
    assert str(WB_DUPE["pid"]) in diag and str(LT_DUPE["pid"]) in diag


def test_a_name_duplicated_on_ONE_host_is_also_AMBIGUOUS(world, tmp_path):
    """The live case: `datapacket-talos-d3` was two peers on ONE machine.

    The HOST is arguably unambiguous there, but the PEER is not, and a caller
    routing work to a peer needs to know it did not identify one. Refusing is
    the specified behaviour.
    """
    twin = dict(WB_ALPHA, pid=910005, name="gamma-33",
                sessionId="eee5c4f0-0000-4000-8000-000000000005",
                tmux="scratchC:@73.%73")
    reg, proc = _registry(tmp_path, "wb2", [WB_DUPE, twin])
    r = world("gamma-33", registry_dir=reg, proc_dir=proc)
    assert r["status"] == ph.STATUS_AMBIGUOUS
    assert r["host"] is None
    assert {m["host"] for m in r["matches"]} == {"workbench"}
    assert len(r["matches"]) == 2


# =========================================================================== #
# The laptop-unreachable behaviour — the failure criterion 3 forbids
# =========================================================================== #
def test_an_unreachable_laptop_yields_INCOMPLETE_not_the_local_host(world):
    """🔴 THE DANGEROUS CASE. A laptop-only name, with the laptop unreachable,
    must NOT come back as `workbench`."""
    world.state["raise"] = subprocess.TimeoutExpired(cmd="ssh", timeout=1)
    r = world("beta-22")
    assert r["status"] == ph.STATUS_INCOMPLETE
    assert r["host"] is None
    assert r["hosts_unsearched"] == ["laptop"]
    assert r["coverage"]["laptop"]["status"] == ph.READ_UNREACHABLE


def test_INCOMPLETE_and_UNMATCHED_are_different_answers(world):
    """Both find zero matches. Only one of them means "it is not running"."""
    reachable = world("nothing-like-this")
    world.state["raise"] = OSError("no route to host")
    unreachable = world("nothing-like-this")
    assert reachable["status"] == ph.STATUS_UNMATCHED
    assert unreachable["status"] == ph.STATUS_INCOMPLETE
    assert ph._EXIT_FOR[reachable["status"]] != ph._EXIT_FOR[unreachable["status"]]


def test_a_unique_LOCAL_match_still_answers_when_the_laptop_is_down(world):
    """A positive measurement is not a default — but it is warned about."""
    world.state["raise"] = OSError("no route to host")
    r = world("alpha-11")
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["host"] == "workbench"
    assert r["hosts_unsearched"] == ["laptop"]
    assert "WARNING" in ph.render_diagnostics(r)


def test_the_local_leg_reports_NO_self_label(world):
    """🔴 THE ASYMMETRY IS THE POINT, and it must not be tidied away.

    The REMOTE leg's `reported_label` is an INDEPENDENT measurement — the far
    end computes it for itself — which is what lets a wrong SSH address be
    caught. The LOCAL leg has no second source: `local_host` was itself derived
    from `local_host_label()`, so echoing it back would be a tautology wearing
    the costume of a check, and a reader comparing the two columns would
    conclude the local leg is guarded when it is not.

    This asserts the honest `None`. A future edit that "fills in" the local
    label to make the two columns look uniform fails here.
    """
    r = world("alpha-11")
    assert r["coverage"]["workbench"]["reported_label"] is None
    assert r["coverage"]["laptop"]["reported_label"] == "laptop"


def test_a_nonzero_ssh_exit_is_UNREACHABLE_and_carries_the_stderr(world):
    world.state["rc"] = 255
    world.state["stdout"] = ""
    r = world("beta-22")
    assert r["coverage"]["laptop"]["status"] == ph.READ_UNREACHABLE
    assert "255" in r["coverage"]["laptop"]["error"]
    assert "stub stderr" in r["coverage"]["laptop"]["error"]


# =========================================================================== #
# The remote leg's own instrument — sentinel and self-identification
# =========================================================================== #
def test_output_without_the_sentinel_is_NO_SENTINEL_not_an_empty_success(world):
    """A swallowed command and a host with no sessions must not look alike."""
    world.state["stdout"] = "some unrelated chatter\n"
    r = world("beta-22")
    assert r["coverage"]["laptop"]["status"] == ph.READ_NO_SENTINEL
    assert r["status"] == ph.STATUS_INCOMPLETE


def test_a_host_that_identifies_as_someone_else_is_REFUSED(world):
    """🔴 Dialling `10.42.0.10` (the homelab gateway) instead of `10.42.0.100`
    (the laptop) SUCCEEDS against a real host. Only the self-reported label can
    catch it, and the leg must be refused rather than recorded."""
    world.state["label"] = "workbench"
    r = world("beta-22")
    cov = r["coverage"]["laptop"]
    assert cov["status"] == ph.READ_LABEL_MISMATCH
    assert cov["reported_label"] == "workbench"
    assert "refusing to label" in cov["error"]
    assert r["status"] == ph.STATUS_INCOMPLETE
    assert r["host"] is None


def test_a_label_mismatch_KEEPS_ITS_NAME_even_when_ssh_also_exits_nonzero(world):
    """🔴 An `unreachable` verdict invites a retry against the very address that
    is wrong. Only `no_sentinel` may be overridden by a non-zero rc."""
    world.state["label"] = "workbench"
    world.state["rc"] = 1
    r = world("beta-22")
    assert r["coverage"]["laptop"]["status"] == ph.READ_LABEL_MISMATCH


def test_a_missing_host_label_source_is_a_leg_ERROR_not_a_traceback(world, monkeypatch):
    """`read_host` promises it never raises for a host problem. An unreadable
    `host_label.py` is a LOCAL problem, but letting it escape still exits 1 —
    outside the documented vocabulary — so it becomes this leg's error instead."""
    class _Boom:
        __file__ = "/nonexistent/host_label.py"
    monkeypatch.setattr(ph, "_hl", _Boom, raising=False)
    monkeypatch.setattr(ph._hl, "ssh_target", lambda h: "zach@example", raising=False)
    leg = ph.read_host("laptop", local_host="workbench", registry_dir="/x",
                       runner=lambda *a, **k: (0, "", ""))
    assert leg["status"] == ph.READ_ERROR
    assert "host_label source" in leg["error"]


def test_a_registry_error_on_the_remote_is_ERROR_not_an_empty_read(world):
    world.state["stdout"] = _leg_stdout("laptop", [],
                                        error="FileNotFoundError: /nope")
    r = world("beta-22")
    assert r["coverage"]["laptop"]["status"] == ph.READ_ERROR
    assert r["status"] == ph.STATUS_INCOMPLETE


def test_parse_leg_output_rejects_an_unparseable_payload():
    leg = ph.parse_leg_output("%s laptop\nnot json\n" % ph.SENTINEL, "laptop")
    assert leg["status"] == ph.READ_NO_SENTINEL
    assert "unparseable" in leg["error"]


def test_parse_leg_output_tolerates_noise_before_the_sentinel():
    """Framing is by SENTINEL SCAN, not by line number, so a stray banner from
    a login shell cannot make a good answer unreadable."""
    good = _leg_stdout("laptop", [_shaped(LT_BETA)])
    leg = ph.parse_leg_output("motd line\nanother\n" + good, "laptop")
    assert leg["status"] == ph.READ_OK
    assert [r["name"] for r in leg["records"]] == ["beta-22"]


# =========================================================================== #
# The shipped remote program
# =========================================================================== #
def test_the_remote_program_puts_from_future_first():
    """🔴 RED FOR A REAL DEFECT. `from __future__` must be the first statement
    of the compiled unit; splicing host_label.py in with its own copy left one
    mid-file, which is a hard SyntaxError — and the leg then reports as an
    UNREACHABLE LAPTOP, i.e. a purely local bug that reads as a dead host."""
    src = (_SCRIPTS / "lib" / "host_label.py").read_text(encoding="utf-8")
    program = ph.remote_program(src)
    assert program.splitlines()[0] == "from __future__ import annotations"
    assert program.count("from __future__ import annotations") == 1
    # The real control: it must COMPILE.
    compile(program, "<remote>", "exec")


def test_the_remote_program_does_not_let_host_label_print_its_own_main(monkeypatch):
    """`python3 -` runs the program as `__main__`, which defeats host_label's
    own `if __name__ == "__main__"` guard and would put a bare label line on
    stdout ahead of our payload."""
    src = (_SCRIPTS / "lib" / "host_label.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in src, "the hazard this guards is gone"
    program = ph.remote_program(src)
    idx_guard = program.index("__name__ = '_peerhost_hostlabel'")
    idx_main = program.index('if __name__ == "__main__":')
    idx_restore = program.index("__name__ = '__main__'")
    assert idx_guard < idx_main < idx_restore


def test_the_remote_program_actually_runs_and_emits_the_protocol(tmp_path):
    """END-TO-END on the SHIPPED source, in a subprocess, against a fixture
    registry — the positive control that the thing we send over SSH works at
    all. It runs `python3 -` exactly as the SSH leg does, but locally."""
    reg, _unused_proc = _registry(tmp_path, "e2e", [WB_ALPHA, LT_BETA])
    env_file = tmp_path / "collector-env"
    env_file.write_text("ACTIVITY_HOST=laptop\n")
    src = (_SCRIPTS / "lib" / "host_label.py").read_text(encoding="utf-8")
    program = ph.remote_program(src)
    # `_unused_proc`: this leg deliberately runs against the real /proc (the
    # reader's default), because what is under test is the shipped PROTOCOL, not
    # the liveness filter. The fixture /proc is built by the helper and not used.
    out = subprocess.run(
        [sys.executable, "-", reg], input=program, capture_output=True,
        text=True, timeout=60,
        env={**os.environ, "HOST_LABEL_ENV_FILE": str(env_file),
             "ACTIVITY_HOST": "laptop"})
    assert out.returncode == 0, out.stderr
    leg = ph.parse_leg_output(out.stdout, "laptop")
    assert leg["status"] == ph.READ_OK
    assert leg["reported_label"] == "laptop"
    # 🔴 THE ASSERTION IS A SUM, NOT A ZERO, AND THAT IS THE WHOLE POINT.
    # This leg uses the reader's DEFAULT `proc_dir` — the machine's real /proc —
    # so whether pids 910001/910003 are live is a property of the host, not of
    # the code. `pid_max` here is 4194304 and current pids run near it, so those
    # values are ordinary allocatable ones: an equality-to-zero assertion passes
    # by accident of the environment and can flake on the dev host while never
    # flaking in the sandbox. The two tiers would then disagree structurally —
    # the same trap the shebang pin fell into.
    #
    # The sum keeps the positive control intact (both records were SEEN and
    # accounted for, so the protocol really carried them) without depending on
    # which side of the liveness filter they landed on.
    assert leg["stale_dropped"] + len(leg["records"]) == 2
    assert leg["unreadable"] == 0


def test_host_label_stays_SHIPPABLE_over_the_wire():
    """🔴 THE CONSTRAINT IS STATED ABOUT THE WRONG MODULE WITHOUT THIS.

    `_READER_SOURCE`'s comment says "stdlib-only and Python-3.9-compatible", but
    the source actually spliced into the remote program is `host_label.py`, which
    carried no such pin. A future third-party import there breaks the remote leg
    as `unreachable` — a purely local bug that reads as a DEAD LAPTOP, which is
    exactly the failure class `test_the_remote_program_puts_from_future_first`
    exists for. The end-to-end test cannot catch it either: the package would be
    installed on the dev host, so the splice would run fine there.

    Imports only; a stdlib-only module is what "shippable" means here.

    ⚠ THERE IS NO RELATIVE-IMPORT BRANCH, AND THAT IS DELIBERATE. One was written
    and then DELETED after a mutation sweep showed it could never execute: a
    `from . import x` in `host_label.py` breaks the flat `import host_label` that
    this very file does at module scope, so the mutant kills COLLECTION and the
    assertion is never reached. The hazard is real but it is caught earlier and
    far louder than any guard here could manage — by every importer at once — so
    a branch for it would be dead code claiming coverage it cannot provide.
    """
    src = (_SCRIPTS / "lib" / "host_label.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    stdlib = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    assert stdlib, "no stdlib_module_names on this interpreter — cannot judge"
    non_stdlib = sorted(r for r in roots if r not in stdlib)
    assert not non_stdlib, (
        "host_label.py imports %s, which will not exist on the remote host — "
        "the peer-host SSH leg would report the laptop as UNREACHABLE"
        % non_stdlib)


def test_the_shipped_reader_is_the_one_the_local_leg_runs():
    """One implementation, both legs. If these ever diverge, a difference
    between the hosts' answers stops being evidence about the hosts."""
    ns = ph._reader_namespace()
    assert "_peerhost_read_registry" in ns and "_peerhost_main" in ns
    assert "_peerhost_read_registry" in ph.remote_program("x = 1\n")


# =========================================================================== #
# Selector kinds
# =========================================================================== #
@pytest.mark.parametrize("selector,expected", [
    ("alpha-11", [ph.KIND_NAME]),
    (WB_ALPHA["sessionId"], [ph.KIND_SESSION_ID]),
    (WB_ALPHA["sessionId"][:6], [ph.KIND_SESSION_ID]),
    (WB_ALPHA["sessionId"][:6].upper(), [ph.KIND_SESSION_ID]),
    ("910001", [ph.KIND_PID]),
    ("scratchA:@71.%71", [ph.KIND_TMUX]),
    ("scratchA:@71", [ph.KIND_TMUX]),
])
def test_every_selector_kind_matches_the_right_record(selector, expected):
    assert ph.match_kinds(selector, _shaped(WB_ALPHA)) == expected


@pytest.mark.parametrize("selector", [
    "", "   ",
    "aaa1c",                       # one char short of MIN_SESSION_ID_PREFIX
    "alpha-1",                     # a PREFIX of the name is not the name
    "alpha-111",                   # nor a superstring
    "91000",                       # a prefix of the pid is not the pid
    "9100010",
    "scratchA:@7",                 # a partial window id must not match @71
    "scratchA",
])
def test_a_near_miss_matches_nothing(selector):
    assert ph.match_kinds(selector, _shaped(WB_ALPHA)) == []


def test_a_six_char_prefix_matches_and_a_five_char_one_does_not():
    """Pins MIN_SESSION_ID_PREFIX to its BOUNDARY on both sides, so a mutant
    that moves it in either direction dies here."""
    sid = WB_ALPHA["sessionId"]
    assert ph.MIN_SESSION_ID_PREFIX == 6
    assert ph.match_kinds(sid[:ph.MIN_SESSION_ID_PREFIX], _shaped(WB_ALPHA))
    assert not ph.match_kinds(sid[:ph.MIN_SESSION_ID_PREFIX - 1], _shaped(WB_ALPHA))


def test_a_non_hex_string_of_sufficient_length_is_not_a_session_id():
    rec = _shaped(dict(WB_ALPHA, sessionId="zzzzzzzz-0000-4000-8000-00000000000f"))
    assert ph.match_kinds("zzzzzz", rec) == []


def test_a_record_matched_by_two_kinds_is_still_ONE_match(world, tmp_path):
    """A selector that is both a valid name and a valid pid must not double the
    match and turn a resolvable lookup into a false AMBIGUOUS."""
    rec = dict(WB_ALPHA, name="910001")
    reg, proc = _registry(tmp_path, "twokind", [rec])
    r = world("910001", registry_dir=reg, proc_dir=proc)
    assert r["status"] == ph.STATUS_RESOLVED
    assert len(r["matches"]) == 1
    assert set(r["matches"][0]["matched_by"]) == {ph.KIND_NAME, ph.KIND_PID}


@pytest.mark.parametrize("selector", ["\u00b2", "\u0661\u0662\u0663", "\uff11\uff12\uff13"])
def test_a_non_ascii_digit_is_not_a_pid(selector):
    """🔴 TWO DISTINCT DEFECTS, AND `isdecimal()` FIXES ONLY ONE.

    `str.isdigit()` is True for characters `int()` refuses and for characters
    `int()` ACCEPTS AS A DIFFERENT SPELLING:

      * `"\u00b2"` (superscript two) — isdigit, NOT isdecimal, `int()` raises.
        It escaped `match_kinds` as a ValueError and exited **1**, a code outside
        this tool's entire documented vocabulary (0/2/3/4/64), with a traceback
        on stderr — and it happened AFTER the SSH round trip.
      * `"\u0661\u0662\u0663"` (Arabic-Indic) — isdigit AND isdecimal, and
        `int()` maps it to 123. So a second spelling of a pid silently RESOLVED
        a peer. Only the ASCII test rejects this one.

    Both must be non-matches, not crashes and not matches.
    """
    rec = _shaped(dict(WB_ALPHA, pid=123))
    assert ph.match_kinds(selector, rec) == []


def test_the_exit_code_for_a_weird_selector_stays_IN_the_vocabulary(world):
    """The consequence the unit test above cannot see: an unclassifiable exit."""
    r = world("\u00b2")
    assert r["status"] == ph.STATUS_UNMATCHED
    assert ph._EXIT_FOR[r["status"]] in (0, 2, 3, 4)


def test_a_bool_pid_is_not_an_integer_pid():
    """`isinstance(True, int)` is True in Python; a JSON `true` in the pid field
    must not match the selector "1"."""
    assert ph.match_kinds("1", _shaped(dict(WB_ALPHA, pid=True))) == []


# =========================================================================== #
# The local reader
# =========================================================================== #
def test_a_record_whose_pid_is_gone_is_DROPPED_and_COUNTED(world, tmp_path):
    """A stale file describes a session that exited. Counting it could invent an
    ambiguity, or answer with a host the peer has left."""
    reg, proc = _registry(tmp_path, "stale", [WB_ALPHA, WB_DUPE])
    os.rmdir(os.path.join(proc, str(WB_DUPE["pid"])))
    r = world("gamma-33", registry_dir=reg, proc_dir=proc)
    assert r["status"] == ph.STATUS_UNMATCHED
    assert r["coverage"]["workbench"]["stale_dropped"] == 1
    # …and the live one in the SAME directory is still found, so this is a
    # filter and not a broken read.
    assert world("alpha-11", registry_dir=reg,
                 proc_dir=proc)["status"] == ph.STATUS_RESOLVED


def test_an_unparseable_json_file_is_COUNTED_not_fatal(world, tmp_path):
    reg, proc = _registry(tmp_path, "bad", [WB_ALPHA])
    Path(reg, "910099.json").write_text("{not json")
    r = world("alpha-11", registry_dir=reg, proc_dir=proc)
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["coverage"]["workbench"]["unreadable"] == 1


def test_a_missing_registry_directory_is_ERROR_not_an_empty_read(world, tmp_path):
    r = world("alpha-11", registry_dir=str(tmp_path / "nope"),
              proc_dir=str(tmp_path))
    assert r["coverage"]["workbench"]["status"] == ph.READ_ERROR
    assert r["status"] == ph.STATUS_INCOMPLETE
    assert r["host"] is None


def test_non_json_files_in_the_registry_are_ignored(world, tmp_path):
    reg, proc = _registry(tmp_path, "mixed", [WB_ALPHA])
    Path(reg, "README.txt").write_text("hello")
    r = world("alpha-11", registry_dir=reg, proc_dir=proc)
    assert r["status"] == ph.STATUS_RESOLVED
    assert r["coverage"]["workbench"]["unreadable"] == 0


# =========================================================================== #
# --host scoping
# =========================================================================== #
def test_host_scoping_excludes_rather_than_silently_missing(world):
    """🔴 A DELIBERATE narrowing yields UNMATCHED, not INCOMPLETE — and the two
    must not be confused in either direction.

    `INCOMPLETE` means "a host I MEANT to search would not answer", which is a
    fault. `--host workbench` is not a fault: the caller asked a narrower
    question and gets a truthful answer to the question they asked. Blaming
    `--host` for a host that is genuinely out of scope is the failure mode
    session-resolve's `SCOPE_BLAMING_STATUSES` exists to prevent.

    What the caller must NOT get is a SILENT narrowing, so the diagnostic names
    the scope it actually searched.
    """
    r = world("beta-22", hosts=("workbench",))
    assert r["coverage"]["laptop"]["status"] == ph.READ_OUT_OF_SCOPE
    assert r["hosts_in_scope"] == ["workbench"]
    assert r["status"] == ph.STATUS_UNMATCHED
    assert r["hosts_unsearched"] == []
    diag = ph.render_diagnostics(r)
    assert "workbench" in diag and "laptop" not in diag
    assert world.state["calls"] == [], "the excluded host was still dialled"


def test_an_out_of_scope_host_is_not_reported_as_a_failed_search(world):
    """The mirror image: a host that WAS in scope and failed must still be
    INCOMPLETE, so the two cannot be collapsed into one branch."""
    world.state["raise"] = OSError("down")
    r = world("beta-22", hosts=("workbench", "laptop"))
    assert r["status"] == ph.STATUS_INCOMPLETE
    assert r["hosts_unsearched"] == ["laptop"]


def test_an_EMPTY_host_scope_RAISES_rather_than_answering_unmatched(world):
    """🔴 UNMATCHED (exit 3) IS THE STRONGEST NEGATIVE ANSWER THIS TOOL GIVES.

    `hosts` is membership-tested, so a string (`"all"`), a mis-cased label or
    `()` selects NO host — and every downstream branch then reads "zero matches,
    nothing unsearched" and returns UNMATCHED from a search that dialled nothing.
    A routing caller is entitled to trust exit 3; it must never come from an
    empty scope. Not reachable through the CLI (argparse `choices` guards
    `--host`), but `lookup()` is the documented API.
    """
    for hosts in ("all", ("Workbench",), (), ("homelab",)):
        with pytest.raises(ValueError, match="empty scope"):
            world("alpha-11", hosts=hosts)


def test_scoping_to_the_local_host_makes_no_ssh_call(world):
    world("alpha-11", hosts=("workbench",))
    assert world.state["calls"] == []


# =========================================================================== #
# The SSH argv
# =========================================================================== #
def test_the_ssh_leg_dials_the_shared_peer_table_address(world):
    world("beta-22")
    argv = world.state["calls"][0]["argv"]
    assert argv[0] == "ssh"
    assert hl.ssh_target("laptop") in argv
    assert argv[-1].startswith("python3 - ")
    assert "-o" in argv and "BatchMode=yes" in argv


def test_the_program_is_piped_on_STDIN_never_embedded_in_the_argv(world):
    """A multi-kilobyte program in an argv is an ARG_MAX and a quoting hazard;
    stdin has neither."""
    world("beta-22")
    call = world.state["calls"][0]
    assert "_peerhost_main" in call["input"]
    assert not any("_peerhost_main" in a for a in call["argv"])


def test_the_registry_dir_is_shell_quoted_into_the_remote_command(world):
    world("beta-22", registry_dir="/tmp/has space/sessions")
    assert "'/tmp/has space/sessions'" in world.state["calls"][0]["argv"][-1]


# =========================================================================== #
# CLI surface
# =========================================================================== #
def test_stdout_carries_ONLY_the_label(world, capsys, monkeypatch):
    """🔴 THE RESOLVED CASE THAT ALSO EMITS DIAGNOSTICS, deliberately.

    Found by the mutation sweep: with a clean resolve there is nothing to print,
    so `print(diag, file=sys.stderr)` -> `print(diag)` was UNREACHABLE here and
    the mutant survived this test. A resolve with an unsearched host emits a
    WARNING, so the mutant now executes — and the assertion is that even then
    stdout carries the label and NOTHING else.
    """
    world.state["raise"] = OSError("laptop down")
    result = world("alpha-11")
    assert result["status"] == ph.STATUS_RESOLVED
    monkeypatch.setattr(ph, "lookup", lambda *a, **k: result)
    rc = ph.main(["alpha-11"])
    out = capsys.readouterr()
    assert rc == ph.EXIT_RESOLVED
    assert out.out == "workbench\n"
    assert "WARNING" in out.err


def test_a_refusal_puts_NOTHING_on_stdout(world, capsys, monkeypatch):
    world.state["records"] = [_shaped(LT_BETA), _shaped(LT_DUPE)]
    result = world("gamma-33")
    monkeypatch.setattr(ph, "lookup", lambda *a, **k: result)
    rc = ph.main(["gamma-33"])
    out = capsys.readouterr()
    assert rc == ph.EXIT_AMBIGUOUS
    assert out.out == ""
    assert "refusing to guess" in out.err


def test_the_status_vocabulary_is_pinned_to_LITERALS():
    """🔴 Found by the sweep's own NEGATIVE CONTROL failing to go red.

    Every other test compares `r["status"]` against `ph.STATUS_*`, so renaming a
    constant's VALUE changes both sides of the comparison and no test can see
    it. `--json` publishes these strings to callers, so they are a contract like
    the exit codes — and a contract asserted only against itself is not pinned
    at all.
    """
    assert ph.STATUS_RESOLVED == "resolved"
    assert ph.STATUS_AMBIGUOUS == "ambiguous"
    assert ph.STATUS_UNMATCHED == "unmatched"
    assert ph.STATUS_INCOMPLETE == "incomplete"
    assert ph.READ_OK == "read"
    assert ph.READ_UNREACHABLE == "unreachable"
    assert ph.READ_NO_SENTINEL == "no_sentinel"
    assert ph.READ_LABEL_MISMATCH == "label_mismatch"
    assert ph.READ_ERROR == "error"
    assert ph.READ_OUT_OF_SCOPE == "out-of-scope"
    assert ph.SENTINEL == "PEERHOST1"


def test_the_exit_code_vocabulary_is_pinned_to_LITERALS():
    """Callers branch on these numbers; they are a contract, not an
    implementation detail. Literals, never re-derived from the module."""
    assert ph.EXIT_RESOLVED == 0
    assert ph.EXIT_AMBIGUOUS == 2
    assert ph.EXIT_UNMATCHED == 3
    assert ph.EXIT_INCOMPLETE == 4
    assert ph.EXIT_USAGE == 64
    assert sorted(ph._EXIT_FOR.values()) == [0, 2, 3, 4]
    assert len(set(ph._EXIT_FOR.values())) == 4, "two outcomes share a code"


def test_a_usage_error_exits_64_not_2():
    """🔴 RED FOR A REAL DEFECT. argparse's own usage exit is 2, which is this
    tool's AMBIGUOUS code — so before the remap a mistyped flag was
    indistinguishable from a real "this name matches two peers" answer."""
    assert ph.main(["--no-such-flag", "x"]) == ph.EXIT_USAGE
    assert ph.EXIT_USAGE != ph.EXIT_AMBIGUOUS


def test_help_still_exits_zero(capsys):
    assert ph.main(["--help"]) == 0


def test_a_bad_host_name_is_a_usage_error_not_a_silent_empty_search():
    assert ph.main(["--host", "mars", "alpha-11"]) == ph.EXIT_USAGE


def test_json_output_is_parseable_and_carries_the_coverage(world, capsys,
                                                           monkeypatch):
    result = world("beta-22")
    monkeypatch.setattr(ph, "lookup", lambda *a, **k: result)
    ph.main(["--json", "beta-22"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["host"] == "laptop"
    assert set(doc["coverage"]) == set(ph.HOST_NAMES)
    assert doc["coverage"]["laptop"]["status"] == ph.READ_OK
    # The full record list must NOT be echoed under coverage — only the census.
    assert "records" not in doc["coverage"]["laptop"]
    assert doc["records_seen"]["laptop"] == 1


# =========================================================================== #
# 🔴 THE SEAM: one spelling of the peer addresses
# =========================================================================== #
def test_opencode_search_and_peer_host_share_ONE_peer_table():
    """Not two tables that happen to agree — the same object."""
    sys.path.insert(0, str(_SCRIPTS / "lib"))
    import opencode_search as ocs
    assert ocs.PEERS is hl.PEER_SSH


def test_session_manager_DERIVES_its_laptop_target_from_the_peer_table():
    """🔴 THE FOLD-IN, PINNED STRUCTURALLY — not just "the two values agree".

    `session-manager` used to declare its own `LAPTOP_SSH_TARGET = "zach@…"`.
    An equality guard between two literals is nearly worthless: it only fires
    for an edit that changes ONE of them, and both were already pinned to the
    same literal by their own tests. So the constant was folded in instead, and
    what is asserted here is that it is DERIVED — an `ast.Call`, not an
    `ast.Constant`. A future edit that re-declares the literal fails here even
    if it happens to type the correct address.
    """
    src = (_SCRIPTS / "session-manager").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assigns = [n for n in ast.walk(tree)
               if isinstance(n, ast.Assign)
               and any(isinstance(x, ast.Name) and x.id == "LAPTOP_SSH_TARGET"
                       for x in n.targets)]
    assert len(assigns) == 1, "expected exactly one LAPTOP_SSH_TARGET assignment"
    value = assigns[0].value
    assert not isinstance(value, ast.Constant), (
        "session-manager re-declared the laptop address as a literal; it must "
        "derive it from host_label.ssh_target()")
    assert isinstance(value, ast.Call)
    assert ast.unparse(value).endswith('ssh_target(\'laptop\')'), ast.unparse(value)


def _scan_for_address_literals(root):
    """(scanned_files, offenders) for a scripts/ tree. PURE apart from reads.

    🔴 EXTRACTED so the guard's two assertions are INDEPENDENTLY REACHABLE. When
    both lived in one test body the file-count control ran first, so in the
    negative control's small fake tree it always won and the offender assertion
    never executed — the "an earlier check always wins so the guard never runs"
    shape exactly, caught by that control on its first run.

    Reads the SYNTAX TREE, not the text: the first cut flagged a COMMENT that
    quoted the literal while explaining why the literal had been removed, i.e.
    it fired on the documentation of its own fix. Comments may name the address;
    a string constant may not. Non-Python files fall back to a text scan.
    """
    targets = {"zach@%s" % addr for _, addr, _ in hl.PEER_SSH}
    owner = (root / "lib" / "host_label.py").resolve()
    offenders, scanned = [], 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.resolve() == owner:
            continue
        parts = set(path.parts)
        if "tests" in parts or "__pycache__" in parts or path.suffix == ".md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            for t_ in targets:
                if ('"%s"' % t_) in text or ("'%s'" % t_) in text:
                    offenders.append("%s: %s" % (path.relative_to(root), t_))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value in targets:
                offenders.append("%s:%s: %s" % (path.relative_to(root),
                                                node.lineno, node.value))
    return scanned, offenders


def test_no_module_redeclares_a_peer_address_literal():
    """🔴 THE LEDGER GUARD — AND ITS DOCSTRING IS DELIBERATELY NARROW.

    It enforces exactly ONE property: no `user@addr` STRING CONSTANT for a peer
    outside `host_label.py`, under `scripts/`. A literal in a TEST is expected —
    that is the pin, not a copy.

    🔴 WHAT IT CANNOT SEE, stated here because an earlier draft of this docstring
    (and of `host_label.py`'s ledger comment) claimed "every place these
    addresses are spelled" and was FALSE when written:
      * a target COMPOSED at runtime — `scripts/lib/host-role.sh` builds
        `zach@10.42.0.100` from two shell variables and never spells it whole;
      * a BARE IP — `scripts/browser-bridge/server.py:653-654`;
      * anything outside `scripts/` — a literal in `nix/` is invisible.
    Both known cases are named in `host_label.PEER_SSH`'s comment. The values
    agree today; this is a duplication hazard, not a live defect.

    On its FIRST run it found a copy nobody had listed:
    `scripts/session-analysis/espanso-usage.py:90`, a hardcoded
    `DEFAULT_REMOTE`. It is derived now.
    """
    scanned, offenders = _scan_for_address_literals(_SCRIPTS)
    assert not offenders, (
        "these modules re-declare a peer SSH target instead of deriving it from "
        "host_label.PEER_SSH: %s" % offenders)
    # 🔴 POSITIVE CONTROL, asserted AFTER the offender check so it can never
    # mask it: a zero is only meaningful if the walk reached files at all.
    assert scanned > 100, "the scan reached only %d files — it is not looking" % scanned


def test_that_ledger_guard_can_actually_fire(tmp_path):
    """🔴 NEGATIVE CONTROL. The test above passes when the tree is clean AND
    when it is wired to nothing. This proves the scan can go red — and that it
    discriminates a STRING CONSTANT from the same text inside a comment."""
    fake = tmp_path / "scripts"
    (fake / "lib").mkdir(parents=True)
    (fake / "lib" / "host_label.py").write_text('OWNER = "zach@10.42.0.100"\n')
    (fake / "offender.py").write_text('TARGET = "zach@10.42.0.100"\n')
    (fake / "innocent.py").write_text('# was "zach@10.42.0.100" once\nY = 2\n')
    (fake / "tests").mkdir()
    (fake / "tests" / "test_x.py").write_text('PIN = "zach@10.42.0.100"\n')
    scanned, offenders = _scan_for_address_literals(fake)
    joined = " ".join(offenders)
    assert "offender.py" in joined, offenders
    assert "innocent.py" not in joined, "a comment must not trip the guard"
    assert "host_label.py" not in joined, "the owner must be exempt"
    assert "test_x.py" not in joined, "a test pin must not trip the guard"
    # 2, not 4: the owner and anything under tests/ are skipped BEFORE the
    # counter, so `scanned` means "files actually inspected".
    assert scanned == 2, scanned


def test_every_host_name_has_an_ssh_target():
    for host in hl.HOST_NAMES:
        assert hl.ssh_target(host).startswith("zach@")
    assert len({hl.ssh_target(h) for h in hl.HOST_NAMES}) == len(hl.HOST_NAMES)


def test_the_laptop_target_is_the_laptop_not_the_homelab_gateway():
    """Equality, NOT `"10.42.0.10" not in target` — that substring test passes
    for the correct value too and would be a guard that cannot fail."""
    user, _, addr = hl.ssh_target("laptop").partition("@")
    assert user == "zach"
    assert addr == "10.42.0.100"


def test_an_unknown_host_label_RAISES_rather_than_returning_a_falsy_target():
    """A None or "" spliced into an ssh argv connects to the LOCAL machine and
    reports its answer under the wrong label. There is no safe fallback."""
    with pytest.raises(KeyError):
        hl.ssh_target("homelab")


# =========================================================================== #
# Structural guards
# =========================================================================== #
def test_the_module_never_writes_to_disk():
    """No cache, no state file. An on-disk name is a liability no behavioural
    test can see move — the same reason session-resolve pins this."""
    src = _SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {"makedirs", "mkdir", "write_text", "unlink", "remove", "rename"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            raise AssertionError("peer-host reaches for %s()" % node.attr)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "open":
            raise AssertionError("peer-host calls bare open()")


def test_the_module_is_executable_and_has_a_python_shebang():
    """🔴 THE SHEBANG IS CHECKED BY SHAPE, NOT BY ITS LITERAL TEXT.

    This pinned the env-form interpreter line exactly and went RED in the
    nix-build tier while passing on the dev host — `flake.nix` runs nixpkgs'
    `patchShebangs src/scripts` over the copied tree, so the env form becomes an
    absolute store path there. Pinning the exact string asserts something about
    ONE tree, which is the config-blind-suite failure in miniature: a test whose
    environment decides its answer. What is load-bearing is the `#!` prefix plus
    a python3 interpreter, and that holds in both tiers.

    🔴 THE DOCSTRING DELIBERATELY DOES NOT SPELL THE ENV FORM either —
    `test_runtime_shebangs.py` is a repo-wide TEXT scan and flagged this prose on
    its first run after the fix, correctly by its own rules. The resolution
    `test_handoff_index.py` prescribes for exactly this is to stop carrying the
    literal, not to pin an exemption for it.

    Same reasoning and the same shape as
    `test_handoff_index.py::test_a_module_documenting_bare_invocation_is_executable`.
    """
    first = _SCRIPT.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!"), first
    assert "python3" in first, first
    assert os.stat(_SCRIPT).st_mode & 0o111, "peer-host is not executable"


def test_no_ref_selector_is_implemented():
    """The bracketed `[ref]` from the peer listing is deliberately unsupported.

    ⚠ LABELLED HONESTLY: this is an INVARIANT GUARD, not regression coverage and
    not a discriminating check. It cannot tell a CORRECT ref derivation from a
    wrong one — nothing here can, because no derivation is known. What it pins is
    the deliberate ABSENCE, so that adding a ref kind is a conscious act with a
    test to delete rather than a quiet afternoon's guess.

    Why the absence: the ref survived two independent sweeps (11 registry inputs
    x 6 hash algorithms x 3 offsets x 48 records; then 9 inputs including the raw
    UUID bytes in both orders and `bridgeSessionId`, x 11 algorithms). The
    tempting shortcut is "the ref's first two characters look like the name's
    suffix" — n=1, 1-in-256 by chance, and if wrong it answers the WRONG HOST
    silently, the one outcome this tool exists to prevent.
    """
    assert "ref" not in ph.ALL_KINDS
    # The second assertion IS discriminating: it fails if a name-suffix rule is
    # ever implemented, which is the specific wrong guess this guards against.
    assert ph.match_kinds("ef79ae", _shaped(dict(WB_ALPHA, name="repo-ef"))) == []


def test_peer_host_is_on_PATH_as_a_bare_command():
    """🔴 THE DELIVERABLE IS THE ANSWER BEING OBTAINABLE, NOT THE SCRIPT EXISTING.

    The caller this tool is for is a session in ANOTHER REPO deciding where to
    route work, and such a session cannot resolve an absolute devrc path — the
    rationale `nix/home.nix` already states for `claim-work` and `cairn`. Without
    a PATH entry the tool answers correctly and its intended caller cannot reach
    it. Found by the round-0 requirements pass, not by any correctness axis.
    """
    home_nix = (_SCRIPTS.parent / "nix" / "home.nix").read_text(encoding="utf-8")
    assert '.local/bin/peer-host' in home_nix, (
        "peer-host has no PATH entry — a cross-repo caller cannot invoke it")
    entry = home_nix[home_nix.index('.local/bin/peer-host'):]
    entry = entry[:entry.index(";") + 1]
    # 🔴 mkOutOfStoreSymlink is REQUIRED, not stylistic: peer-host reaches
    # `lib/host_label.py` through `Path(__file__).resolve().parent / "lib"`, and
    # `.resolve()` follows symlinks — so the directory holding the REAL file must
    # also hold `lib/`. A `home.file` store copy resolves into /nix/store and
    # dies on import, which is a runtime failure no unit test would see.
    assert "mkOutOfStoreSymlink" in entry, entry
    assert "scripts/peer-host" in entry, entry


def test_the_module_resolves_its_lib_relative_to_the_RESOLVED_file():
    """The property the line above depends on, asserted against the source: the
    sibling lookup must use `resolve()`, or a PATH symlink would look for `lib/`
    next to the symlink instead of next to the real file."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert 'Path(__file__).resolve().parent / "lib"' in src


def test_read_statuses_are_all_distinct_and_only_READ_counts_as_searched():
    assert len(set(ph.ALL_READ_STATUSES)) == len(ph.ALL_READ_STATUSES)
    assert ph.SEARCHED_STATUSES == (ph.READ_OK,)
    for status in ph.ALL_READ_STATUSES:
        if status != ph.READ_OK:
            assert status not in ph.SEARCHED_STATUSES


# =========================================================================== #
# MUTATION MATRIX — each mutant, and the test that kills it with its OWN
# assertion. Swept under PYTHONDONTWRITEBYTECODE=1 (a same-length edit inside
# one second of the last import is invisible to CPython's mtime-in-seconds
# bytecode cache, and would be scored SURVIVED without ever executing).
# =========================================================================== #
MUTATION_MATRIX = {
    # lookup(): the host answer
    "resolved returns local_host instead of matches[0]['host']":
        "test_a_remote_peer_name_resolves_to_the_REMOTE_host",
    "len(matches) > 1 -> len(matches) > 2 (ambiguity not refused)":
        "test_a_name_on_TWO_hosts_is_AMBIGUOUS",
    "the one-host duplicate collapses to resolved":
        "test_a_name_duplicated_on_ONE_host_is_also_AMBIGUOUS",
    "unsearched hosts ignored -> zero matches becomes UNMATCHED":
        "test_an_unreachable_laptop_yields_INCOMPLETE_not_the_local_host",
    "STATUS_INCOMPLETE aliased to STATUS_UNMATCHED":
        "test_INCOMPLETE_and_UNMATCHED_are_different_answers",
    # find_matches(): which legs contribute
    "SEARCHED_STATUSES widened to include every status":
        "test_a_host_that_identifies_as_someone_else_is_REFUSED",
    # parse_leg_output(): the protocol
    "missing sentinel treated as an empty success":
        "test_output_without_the_sentinel_is_NO_SENTINEL_not_an_empty_success",
    "reported_label != expected check dropped":
        "test_a_host_that_identifies_as_someone_else_is_REFUSED",
    "sentinel scan replaced by lines[0]":
        "test_parse_leg_output_tolerates_noise_before_the_sentinel",
    "payload 'error' key ignored":
        "test_a_registry_error_on_the_remote_is_ERROR_not_an_empty_read",
    # read_host(): the rc override
    "rc != 0 overrides ANY status (label_mismatch -> unreachable)":
        "test_a_label_mismatch_KEEPS_ITS_NAME_even_when_ssh_also_exits_nonzero",
    # match_kinds()
    "name match becomes startswith":
        "test_a_near_miss_matches_nothing",
    "MIN_SESSION_ID_PREFIX 6 -> 5":
        "test_a_six_char_prefix_matches_and_a_five_char_one_does_not",
    "MIN_SESSION_ID_PREFIX 6 -> 7":
        "test_a_six_char_prefix_matches_and_a_five_char_one_does_not",
    "the _is_hex() check on a session-id prefix is dropped":
        "test_a_non_hex_string_of_sufficient_length_is_not_a_session_id",
    "tmux prefix match drops the '.' boundary (scratchA:@7 matches @71)":
        "test_a_near_miss_matches_nothing",
    "the isinstance(pid, bool) exclusion is dropped":
        "test_a_bool_pid_is_not_an_integer_pid",
    "kinds appended per-kind as separate matches":
        "test_a_record_matched_by_two_kinds_is_still_ONE_match",
    # the reader
    "the pid-liveness filter is dropped":
        "test_a_record_whose_pid_is_gone_is_DROPPED_and_COUNTED",
    "an unparseable file raises instead of counting":
        "test_an_unparseable_json_file_is_COUNTED_not_fatal",
    "a missing directory returns an empty success":
        "test_a_missing_registry_directory_is_ERROR_not_an_empty_read",
    "raw.get('tmux') -> raw['tmux'] (KeyError on every laptop record)":
        "test_the_READER_parses_a_record_with_no_tmux_key",
    "the .json suffix filter is dropped":
        "test_non_json_files_in_the_registry_are_ignored",
    # remote_program()
    "the __future__ strip is dropped (SyntaxError -> looks like a dead host)":
        "test_the_remote_program_puts_from_future_first",
    "the __name__ rebind is dropped":
        "test_the_remote_program_does_not_let_host_label_print_its_own_main",
    "the shipped program stops emitting the protocol":
        "test_the_remote_program_actually_runs_and_emits_the_protocol",
    # scoping / argv
    "an out-of-scope host is dialled anyway":
        "test_host_scoping_excludes_rather_than_silently_missing",
    "out-of-scope collapsed into the unsearched set (UNMATCHED -> INCOMPLETE)":
        "test_host_scoping_excludes_rather_than_silently_missing",
    "a genuinely failed in-scope host collapsed into out-of-scope":
        "test_an_out_of_scope_host_is_not_reported_as_a_failed_search",
    "shlex.quote dropped from the remote command":
        "test_the_registry_dir_is_shell_quoted_into_the_remote_command",
    "ssh_target('laptop') -> the workbench address":
        "test_the_laptop_target_is_the_laptop_not_the_homelab_gateway",
    "ssh_target returns None for an unknown label":
        "test_an_unknown_host_label_RAISES_rather_than_returning_a_falsy_target",
    "opencode_search re-declares its own PEERS literal":
        "test_opencode_search_and_peer_host_share_ONE_peer_table",
    "session-manager re-declares the laptop address as a literal":
        "test_session_manager_DERIVES_its_laptop_target_from_the_peer_table",
    "a new module hardcodes zach@<peer address>":
        "test_no_module_redeclares_a_peer_address_literal",
    "the ledger scan is wired to nothing / stops discriminating comments":
        "test_that_ledger_guard_can_actually_fire",
    "a non-ASCII digit selector crashes out of match_kinds (exit 1)":
        "test_a_non_ascii_digit_is_not_a_pid",
    "an empty/bogus host scope answers UNMATCHED instead of raising":
        "test_an_EMPTY_host_scope_RAISES_rather_than_answering_unmatched",
    "host_label.py grows a third-party import (remote leg dies as unreachable)":
        "test_host_label_stays_SHIPPABLE_over_the_wire",
    "an unreadable host_label.py escapes read_host as a traceback (exit 1)":
        "test_a_missing_host_label_source_is_a_leg_ERROR_not_a_traceback",
    # CLI
    "diagnostics printed to stdout":
        "test_stdout_carries_ONLY_the_label",
    "the local leg echoes local_host as a fake self-report":
        "test_the_local_leg_reports_NO_self_label",
    "any STATUS_* or READ_* string value renamed":
        "test_the_status_vocabulary_is_pinned_to_LITERALS",
    "the .local/bin PATH entry is dropped (tool unreachable cross-repo)":
        "test_peer_host_is_on_PATH_as_a_bare_command",
    "the PATH entry becomes a home.file store copy":
        "test_peer_host_is_on_PATH_as_a_bare_command",
    "resolve() dropped from the lib lookup (breaks the PATH symlink)":
        "test_the_module_resolves_its_lib_relative_to_the_RESOLVED_file",
    "the host is printed even on a refusal":
        "test_a_refusal_puts_NOTHING_on_stdout",
    "EXIT_USAGE 64 -> 2":
        "test_a_usage_error_exits_64_not_2",
    "any two exit codes made equal":
        "test_the_exit_code_vocabulary_is_pinned_to_LITERALS",
    "--help remapped to EXIT_USAGE":
        "test_help_still_exits_zero",
}


def test_every_mutation_matrix_entry_names_a_real_test():
    """The matrix is a CLAIM about this file; this makes it a checkable one."""
    here = set(globals())
    missing = sorted({t for t in MUTATION_MATRIX.values() if t not in here})
    assert not missing, "MUTATION_MATRIX names tests that do not exist: %s" % missing
