#!/usr/bin/env python3
"""Unit tests for scripts/session-write — the four tmux WRITE verbs.

🔴 THESE ARE INVARIANT GUARDS, NOT REGRESSION TESTS
----------------------------------------------------
`scripts/session-write` DID NOT EXIST at this branch's base. There is no
"red at origin/main, green at HEAD" matrix to report and none is claimed: every
test here pins an invariant of a brand-new file, and the honest statement is
"these guards were mutation-tested against deliberate breakage of the code they
guard", which is what the COMMITTED sweep at
`scripts/session-write-harness/mutation_sweep.py` records — reproducibly, which
the first version's uncommitted one did not. Calling them regression coverage
would be a false claim about what has been observed to fail.

🔴 AND A GREEN RUN HERE IS STRUCTURALLY BLIND TO ONE WHOLE CLASS. Every test in
this file stubs the tmux seam, so no test here can disagree with a PREMISE about
what tmux and readline DO with a payload — and that is exactly where the audited
`type` bypass lived: 117 tests green while `session-write type` executed
arbitrary commands in any shell pane. The companion
`scripts/session-write-harness/real_pane_check.py` is what covers it, driving
the real CLI against a real bash pane in a private `-L` server and reading the
answer off the FILESYSTEM. Run it after any change to `validate_text`.


🔴 HERMETIC BY CONSTRUCTION, AND PROVEN SO
-------------------------------------------
This suite runs on the live workbench, whose real tmux holds the operator's
sessions. A test that reached the real seam would DETACH A REAL CLIENT or TYPE
INTO A REAL PANE. Four autouse fixtures make that impossible rather than
unlikely, and `test_hermeticity_fixtures_are_actually_installed` is the POSITIVE
CONTROL on all four — a guard nobody has watched work is not a guard:

  1. `sw._default_runner` and `sr._default_runner` are both replaced with
     raisers. Two modules, two seams; patching one and assuming the other is how
     session-resolve's own suite once shelled out to real git.
  2. `subprocess.run` is patched in BOTH module namespaces. No binding — a
     dataclass field default, a captured symbol, a re-import — outruns that.
  3. `$SESSION_WRITE_LOG` is repointed under tmp_path, so a bare
     `WriteSources()` cannot append to the operator's real
     `~/.claude/session-write.log`.
  4. session-resolve's `DEFAULT_REGISTRY_DIR` / `DEFAULT_SLOT_TABLE` are
     repointed at paths that do not exist.


🔴 EVERY FIXTURE RESOLUTION GOES THROUGH THE REAL `session-resolve`
--------------------------------------------------------------------
Not a canned dict. RULES.md: "verified in isolation is the new vacuous green —
the defect lives in the SEAM nobody owns." session-write reads ~15 keys off a
resolved target, and a suite built on a hand-written target dict would pass
forever while session-resolve renamed one of them. So `make_sources()` feeds RAW
tmux format strings (built from session-resolve's OWN `PANE_FORMAT` /
`WINDOW_FORMAT` / `CLIENT_FORMAT` constants, never a hand-copied spelling)
through the real `sr.resolve`, and `test_every_target_key_session_write_reads_
is_emitted_by_session_resolve` closes the seam structurally: it AST-scans
session-write for the keys it pulls off a target and asserts each one is
actually present on a really-resolved target.

That guard is not hypothetical. It is how this branch found that session-resolve
declares `claude` in `SM_PASSTHROUGH_FIELDS` and never emits it.


🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT, AND DISTINCT FROM EVERY CONSTANT
--------------------------------------------------------------------------
This repo has been bitten five times by a fixture whose value equals the
constant under test, so a mutant that hardcodes the literal SURVIVES a fully
green suite. Concretely here:

  * `LOCAL_HOST` and `REMOTE_HOST` are NEITHER of session-resolve's real
    `HOST_NAMES`. A mutant that hardcodes `"workbench"` as "local" therefore
    fails the local case, not just the remote one.
  * window ids (`@713`/`@714`/`@716`) and window indexes (`"31"`/`"12"`/`"44"`)
    are drawn from different number spaces, so an argv built from the wrong one
    is visible.
  * THE FOCUSED BASE TERMINAL IS NOT FIRST IN THE CLIENT LIST. That is the whole
    point of `test_focus_picks_the_focused_base_terminal_not_the_first_one`:
    `compute_visibility` publishes `base_terminal = base_all[0]`, which on the
    live host happens to be the focused one, so a fixture in list order could
    not tell a correct implementation from one reading that field.
  * the focused base terminal sits on a DIFFERENT session from the target, so
    `switch-client` is exercised on the default path rather than as an edge
    case — measured on the workbench, that is the common case.
  * the target's previous active window is a THIRD window, so a restore that
    put the session back to the target itself would be visible.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import unicodedata

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SW_PATH = os.path.normpath(os.path.join(_HERE, "..", "session-write"))
_SR_PATH = os.path.normpath(os.path.join(_HERE, "..", "session-resolve"))


def _load(name, path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, path))
    module = importlib.util.module_from_spec(spec)
    # 🔴 Register BEFORE exec_module — `@dataclass` resolves its own class's
    # module through `sys.modules[cls.__module__]`.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sw = _load("session_write", _SW_PATH)
#: session-write imports session-resolve itself, under this exact name. Reading
#: it back out (rather than loading a SECOND copy) is what keeps `sr.Sources`
#: one class — two module objects would give two `Sources` types and
#: `isinstance` checks inside session-resolve would start disagreeing.
sr = sw.sr


# =========================================================================== #
# Hermeticity harness
# =========================================================================== #
class _Forbidden(RuntimeError):
    """Raised when a test reaches for the real world."""


@pytest.fixture(autouse=True)
def _no_real_runners(monkeypatch):
    def _boom_sw(argv, timeout=None):
        raise _Forbidden(f"test reached session-write's real seam: {argv!r}")

    def _boom_sr(argv, timeout=None):
        raise _Forbidden(f"test reached session-resolve's real seam: {argv!r}")

    monkeypatch.setattr(sw, "_default_runner", _boom_sw)
    monkeypatch.setattr(sr, "_default_runner", _boom_sr)


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """THE BACKSTOP. Patches `subprocess.run` in BOTH module namespaces, which
    no captured binding can outrun."""
    def _boom(*a, **k):
        raise _Forbidden(f"test reached subprocess.run: {a[:1]!r}")
    monkeypatch.setattr(sw.subprocess, "run", _boom)
    monkeypatch.setattr(sr.subprocess, "run", _boom)


@pytest.fixture(autouse=True)
def _no_real_log(monkeypatch, tmp_path):
    """🔴 BOTH halves, and the ROOT is the load-bearing one. G14 confines every
    log write under `sw.LOG_ROOT`; repointing only the env var would leave the
    suite's `tmp_path` outside the root and every test would refuse with
    EXIT_BAD_LOG_PATH — a suite that is red for a reason unrelated to what it
    tests. Repointing the root is also what keeps the confinement honest here:
    the tests exercise the real predicate against a real root, just not the
    operator's."""
    monkeypatch.setattr(sw, "LOG_ROOT", str(tmp_path))
    monkeypatch.setenv(sw.LOG_PATH_ENV, str(tmp_path / "session-write.log"))


@pytest.fixture(autouse=True)
def _no_real_registry(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "DEFAULT_REGISTRY_DIR",
                        str(tmp_path / "no-such-registry"))
    monkeypatch.setattr(sr, "DEFAULT_SLOT_TABLE",
                        str(tmp_path / "no-such-slots.sh"))


def test_hermeticity_fixtures_are_actually_installed(tmp_path):
    """POSITIVE CONTROL on all four fixtures, asserting the RESOLVED values a
    default-constructed object actually yields rather than the module globals —
    session-resolve has a scar from a control that asserted a global nothing on
    the live path ever read."""
    with pytest.raises(_Forbidden):
        sw._default_runner(["tmux", "list-panes"], timeout=1)
    with pytest.raises(_Forbidden):
        sr._default_runner(["tmux", "list-panes"], timeout=1)
    with pytest.raises(_Forbidden):
        sw.subprocess.run(["true"])
    with pytest.raises(_Forbidden):
        sr.subprocess.run(["true"])

    bare = sw.WriteSources()
    assert bare.resolved_runner() is sw._default_runner
    log = bare.resolved_log_path()
    assert log == str(tmp_path / "session-write.log")
    assert log != sw.DEFAULT_LOG_PATH
    # G14's root is repointed too, or the line above would be REFUSED rather
    # than written — and the suite would be red for the wrong reason.
    assert bare.resolved_log_root() == str(tmp_path)
    assert sw.LOG_ROOT == str(tmp_path) != os.path.join(
        os.path.expanduser("~"), ".claude")
    assert sr.Sources().resolved_registry_dir() == str(
        tmp_path / "no-such-registry")


# =========================================================================== #
# Fixtures — raw tmux output, fed through the REAL session-resolve
# =========================================================================== #
SEP = sr.FIELD_SEP

#: 🔴 NEITHER is a production host name, so a mutant that hardcodes "workbench"
#: fails the LOCAL case too, not only the remote one.
LOCAL_HOST = "rig-alpha"
REMOTE_HOST = "outpost-7"
assert LOCAL_HOST not in sr.HOST_NAMES and REMOTE_HOST not in sr.HOST_NAMES
assert LOCAL_HOST != sr.HOST_ALL and REMOTE_HOST != sr.HOST_ALL

SESSION_TARGET = "workshop4"      # holds the write target
SESSION_CLIENT = "annex9"         # where the focused base terminal is sitting
SESSION_REMOTE = "vault2"         # only exists on REMOTE_HOST

WIN_TARGET_ID, WIN_TARGET_INDEX = "@713", "31"
WIN_PREV_ID, WIN_PREV_INDEX = "@716", "12"     # the ACTIVE window of SESSION_TARGET
WIN_CLIENT_ID, WIN_CLIENT_INDEX = "@714", "44"
WIN_SHELL_ID, WIN_SHELL_INDEX = "@717", "58"
WIN_REMOTE_ID, WIN_REMOTE_INDEX = "@715", "63"

PANE_TARGET = "%9301"
PANE_PREV = "%9302"
PANE_CLIENT = "%9303"
PANE_SHELL = "%9304"

CWD_TARGET = "/w/atelier"
CWD_PREV = "/w/foundry"
CWD_CLIENT = "/w/annexe"
CWD_SHELL = "/w/scullery"

TITLE_TARGET = "pane title target"

#: 🔴 The UNFOCUSED base terminal is FIRST. `compute_visibility` publishes
#: `base_all[0]`; if the fixture put the focused one first, a reader of that
#: field and a correct implementation would be indistinguishable.
BASE_UNFOCUSED_TTY = "/dev/pts/41"
BASE_FOCUSED_TTY = "/dev/pts/42"
POPUP_ONE_TTY = "/dev/pts/43"
POPUP_TWO_TTY = "/dev/pts/44"
UNATTACHED_TTY = "/dev/pts/45"

BASE_TERM = "alacritty"
POPUP_TERM = sr.POPUP_TERM_PREFIX + "-256color"

# --------------------------------------------------------------------------- #
# 🔴 LITERAL client summaries. RULES.md: "Never derive a test's expectation from
# the implementation it tests."
#
# These were `sw._client_summary(...)` calls — the function under test computing
# its own expected value, so every assertion using them read
# `f(x) == f(x)` and was TRUE FOR ANY f. An independent 21-mutant sweep found
# three survivors of a fully green 117-test suite, all here and all invisible
# for that reason:
#
#     tty and session swapped                 -> both sides swap, still equal
#     the popup/base label inverted           -> both sides invert, still equal
#     every client rendered as the string
#       "none" (the empty-list fallback)      -> both sides collapse, still equal
#
# Spelled out, each of those three is a diff. The cost is that changing
# `clients_raw` now breaks these — which is the trade: a fixture change should
# be a deliberate act, and the whole-string discipline this file claims is only
# real if the string is written down somewhere the implementation cannot reach.
# --------------------------------------------------------------------------- #
SUMMARY_TWO_POPUPS_ONLY = (
    "/dev/pts/43 (annex9, popup), /dev/pts/44 (workshop4, popup)")

SUMMARY_ALL_FOUR_CLIENTS = (
    "/dev/pts/41 (workshop4, base), /dev/pts/42 (annex9, base), "
    "/dev/pts/43 (annex9, popup), /dev/pts/44 (workshop4, popup)")

#: With `base=0` the whole attached list IS the two popups.
SUMMARY_NO_BASE_TERMINAL = SUMMARY_TWO_POPUPS_ONLY

#: SYNTHETIC. This repo is PUBLIC and forbids captured text; this stands in for
#: text the operator typed and never sent. Its LENGTH is what the refusal
#: reports, and it is deliberately not a round number.
UNSENT_TEXT = "SYNTHETIC-UNSENT-NEVER-RENDER-THIS-42"
UNSENT_LENGTH = len(UNSENT_TEXT)

#: The payload the write verbs carry. Distinct from every other string here, and
#: it contains a `;` in the MIDDLE (legal — only a TRAILING one is eaten) so the
#: seam's segmenter is exercised by a realistic payload on the happy path.
WRITE_TEXT = "run the audit; then report"

STUB_BRANCH = "fixture-branch-sw-7"


def _pane_line(session, index, wid, pid, cwd, title):
    return SEP.join([LOCAL_HOST, session, index, wid, pid, cwd, title])


def panes_raw() -> str:
    return "\n".join([
        _pane_line(SESSION_TARGET, WIN_TARGET_INDEX, WIN_TARGET_ID,
                   PANE_TARGET, CWD_TARGET, TITLE_TARGET),
        _pane_line(SESSION_TARGET, WIN_PREV_INDEX, WIN_PREV_ID,
                   PANE_PREV, CWD_PREV, "pane title prev"),
        _pane_line(SESSION_CLIENT, WIN_CLIENT_INDEX, WIN_CLIENT_ID,
                   PANE_CLIENT, CWD_CLIENT, "pane title client"),
        _pane_line(SESSION_CLIENT, WIN_SHELL_INDEX, WIN_SHELL_ID,
                   PANE_SHELL, CWD_SHELL, "pane title shell"),
    ])


def windows_raw(active_in_target=WIN_PREV_ID, drop=()) -> str:
    """`drop` removes windows, which is how the write-time re-verify is made to
    fire: resolution sees the window, the fresh read does not."""
    rows = [
        (SESSION_TARGET, WIN_TARGET_ID),
        (SESSION_TARGET, WIN_PREV_ID),
        (SESSION_CLIENT, WIN_CLIENT_ID),
        (SESSION_CLIENT, WIN_SHELL_ID),
    ]
    return "\n".join(
        SEP.join([s, w, "1" if w == active_in_target or s == SESSION_CLIENT
                  and w == WIN_CLIENT_ID else "0"])
        for s, w in rows if w not in drop)


def _client_line(tty, term, w, h, session, flags):
    return SEP.join([tty, term, w, h, session, flags])


def clients_raw(popups=2, base=2, focused_base=True) -> str:
    rows = []
    if base >= 1:
        # UNFOCUSED and FIRST — deliberately.
        rows.append(_client_line(BASE_UNFOCUSED_TTY, BASE_TERM, "104", "31",
                                 SESSION_TARGET, "attached,UTF-8"))
    if base >= 2:
        rows.append(_client_line(
            BASE_FOCUSED_TTY, BASE_TERM, "311", "62", SESSION_CLIENT,
            "attached,focused,UTF-8" if focused_base else "attached,UTF-8"))
    if popups >= 1:
        rows.append(_client_line(POPUP_ONE_TTY, POPUP_TERM, "246", "47",
                                 SESSION_CLIENT, "attached,focused,UTF-8"))
    if popups >= 2:
        rows.append(_client_line(POPUP_TWO_TTY, POPUP_TERM, "194", "35",
                                 SESSION_TARGET, "attached,focused,UTF-8"))
    return "\n".join(rows)


def _sm_row(session, window_id, window_index, **over):
    row = {"session": session, "window_id": window_id,
           "window_index": window_index, "status": None,
           "waiting_probable": False, "waiting_signals": [],
           "waiting_status": "ok", "unsent_prompt": None,
           "unsent_prompt_status": "ok", "age_secs": None, "age_source": None,
           "runtime": None, "claude": True, "busy": False, "task": None,
           "claude_session_id": None, "label": None, "label_source": None,
           "window_name": None}
    row.update(over)
    return row


def sm_payload(target_runtime="claude", target_unsent=None,
               local_host=LOCAL_HOST, with_remote=True):
    hosts = {
        LOCAL_HOST: {"reachable": True, "windows": [
            _sm_row(SESSION_TARGET, WIN_TARGET_ID, WIN_TARGET_INDEX,
                    runtime=target_runtime, unsent_prompt=target_unsent),
            _sm_row(SESSION_TARGET, WIN_PREV_ID, WIN_PREV_INDEX,
                    runtime="claude"),
            _sm_row(SESSION_CLIENT, WIN_CLIENT_ID, WIN_CLIENT_INDEX,
                    runtime="opencode"),
            # 🔴 runtime None — the THIRD state, 36 of 92 targets on the live
            # host. NOT "shell": it means the ledger has no record.
            _sm_row(SESSION_CLIENT, WIN_SHELL_ID, WIN_SHELL_INDEX,
                    runtime=None),
        ]},
    }
    if with_remote:
        hosts[REMOTE_HOST] = {"reachable": True, "windows": [
            _sm_row(SESSION_REMOTE, WIN_REMOTE_ID, WIN_REMOTE_INDEX,
                    runtime="claude"),
        ]}
    payload = {"hosts": hosts}
    if local_host is not None:
        payload["local_host"] = local_host
    return payload


def _sr_runner(argv, timeout=None):
    """session-resolve's runner. Answers `git` and refuses everything else — all
    three tmux reads are supplied as raw strings, so a tmux call here is a bug."""
    if argv and argv[0] == "git":
        return 0, STUB_BRANCH + "\n", ""
    raise _Forbidden(f"session-resolve asked for an unexpected argv: {argv!r}")


def make_sources(**over) -> "sr.Sources":
    kwargs = dict(
        host=LOCAL_HOST, local_host=LOCAL_HOST, runner=_sr_runner,
        panes_raw=panes_raw(), windows_raw=windows_raw(),
        clients_raw=clients_raw(), slot_table_text="",
        registry_records=[], sm_payload=sm_payload(),
    )
    kwargs.update(over)
    return sr.Sources(**kwargs)


class TmuxStub:
    """session-write's tmux seam.

    Answers the three re-verify reads from ITS OWN raw strings — deliberately
    separate from the ones session-resolve saw, so a test can make the world
    change BETWEEN resolution and the write, which is the only way G3 can be
    observed to fire. Records every argv; write verbs return `rc_for` or 0.
    """

    def __init__(self, panes=None, windows=None, clients=None, rc_for=None):
        self.panes = panes_raw() if panes is None else panes
        self.windows = windows_raw() if windows is None else windows
        self.clients = clients_raw() if clients is None else clients
        #: {subcommand: (rc, stderr)} — how a given write verb should fail.
        self.rc_for = dict(rc_for or {})
        self.calls = []

    def __call__(self, argv, timeout=None):
        argv = list(argv)
        self.calls.append(argv)
        assert argv and argv[0] == "tmux", f"non-tmux argv reached seam: {argv!r}"
        sub = argv[1]
        if sub == "list-panes":
            return 0, self.panes, ""
        if sub == "list-windows":
            return 0, self.windows, ""
        if sub == "list-clients":
            return 0, self.clients, ""
        if sub in self.rc_for:
            rc, err = self.rc_for[sub]
            return rc, "", err
        return 0, "", ""

    @property
    def writes(self):
        return [a for a in self.calls
                if a[1] in sw.TMUX_WRITE_SUBCOMMANDS]

    @property
    def subcommands(self):
        return [a[1] for a in self.calls]


def make_ws(stub=None, src_over=None, **over) -> "sw.WriteSources":
    stub = stub if stub is not None else TmuxStub()
    kwargs = dict(runner=stub, resolver_src=make_sources(**(src_over or {})))
    kwargs.update(over)
    ws = sw.WriteSources(**kwargs)
    ws.stub = stub          # test-side handle; session-write never reads it
    return ws


def parse_args(*argv):
    return sw.build_parser().parse_args(list(argv))


def run_verb(*argv, ws=None):
    ws = ws if ws is not None else make_ws()
    return sw.run(parse_args(*argv), ws), ws


#: 🔴 G5's acknowledgement, spelled ONCE. Both screen-changing verbs need it, so
#: every test below that is exercising something OTHER than G5 has to pass it —
#: and a literal repeated twenty times is a literal that gets half-updated.
AT_KB = "--i-am-at-the-keyboard"
assert AT_KB in sw.build_parser().format_help()


ADDR_TARGET = f"{SESSION_TARGET}:{WIN_TARGET_ID}"
ADDR_SHELL = f"{SESSION_CLIENT}:{WIN_SHELL_ID}"
ADDR_REMOTE = f"{SESSION_REMOTE}:{WIN_REMOTE_ID}"


def _norm(text: str) -> str:
    """Collapse whitespace so a WHOLE-STRING assertion survives source rewrap
    but NOT a reword. Rewrapping a message is cosmetic; changing a word is not."""
    return re.sub(r"\s+", " ", text).strip()


def assert_message_is(outcome, template, **slots):
    """🔴 WHOLE NORMALISED STRING, never a keyword. A guard on a word is walkable
    by rewording, and this repo has watched a two-word check be satisfied by a
    sentence's own static prose while neither computed slot was ever read."""
    expected = _norm(template.format(**slots))
    actual = _norm("\n".join(outcome.lines))
    assert actual == expected, f"\n  expected: {expected}\n  actual:   {actual}"


# =========================================================================== #
# The fixture itself is a claim — prove it resolves before trusting any test
# =========================================================================== #
def test_the_fixture_resolves_through_the_real_session_resolve():
    """POSITIVE CONTROL on every other test in this file. If the raw strings
    stop resolving, every guard test below would refuse for the WRONG reason and
    still look like it was testing its guard."""
    res = sr.resolve(ADDR_TARGET, make_sources())
    assert res["status"] == sr.STATUS_RESOLVED, res.get("reason")
    t = res["target"]
    assert t["host"] == LOCAL_HOST
    assert t["session"] == SESSION_TARGET
    assert t["window_id"] == WIN_TARGET_ID
    assert t["pane_id"] == PANE_TARGET
    assert t["runtime"] == "claude"
    assert res["coverage"]["registry"]["local_host"] == LOCAL_HOST
    # and the remote one really is built, or G2 would be untestable
    rem = sr.resolve(ADDR_REMOTE, make_sources())
    assert rem["status"] == sr.STATUS_RESOLVED
    assert rem["target"]["host"] == REMOTE_HOST


# =========================================================================== #
# session-resolve MUST STAY READ-ONLY
# =========================================================================== #
def test_session_resolve_is_still_read_only():
    """🔴 The contract this whole PR is built on. session-write exists BECAUSE
    session-resolve does not write; if that stops being true the separation is
    decorative."""
    assert sr.TMUX_READ_ONLY_SUBCOMMANDS == (
        "list-panes", "list-windows", "list-clients")
    for verb in sw.TMUX_WRITE_SUBCOMMANDS:
        assert verb not in sr.TMUX_READ_ONLY_SUBCOMMANDS
        with pytest.raises(sr.ReadOnlyViolation):
            sr._assert_read_only(["tmux", verb, "-t", PANE_TARGET])


def test_session_write_never_calls_session_resolves_runner():
    """session-write owns its OWN seam. If it borrowed session-resolve's, every
    write would raise `ReadOnlyViolation` — or worse, someone would widen
    session-resolve's allowlist to make it work."""
    src = open(_SW_PATH, encoding="utf-8").read()
    assert "TMUX_READ_ONLY_SUBCOMMANDS" in src, (
        "expected session-write to DERIVE its read list from session-resolve")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "_assert_read_only":
            raise AssertionError(
                "session-write must not call session-resolve's read-only "
                "assertion — it has its own, with a different allowlist")


# =========================================================================== #
# G4 — the write seam
# =========================================================================== #
DESTRUCTIVE_VERBS = ("kill-session", "kill-pane", "kill-window", "kill-server",
                     "respawn-pane", "respawn-window", "rename-session",
                     "new-session", "run-shell", "source-file")


@pytest.mark.parametrize("verb", DESTRUCTIVE_VERBS)
def test_the_seam_refuses_a_destructive_verb(verb):
    assert verb not in sw.TMUX_ALLOWED_SUBCOMMANDS
    with pytest.raises(sw.WriteVerbViolation) as exc:
        sw._assert_allowed(["tmux", verb, "-t", PANE_TARGET])
    assert verb in str(exc.value)


@pytest.mark.parametrize("verb", sw.TMUX_ALLOWED_SUBCOMMANDS)
def test_the_seam_allows_each_allowlisted_verb(verb):
    """POSITIVE CONTROL. An allowlist that rejects everything is not a guard,
    it is a broken tool — and it would pass every test above."""
    sw._assert_allowed(["tmux", verb, "-t", PANE_TARGET])


def test_the_seam_checks_every_command_not_just_the_first():
    """🔴 tmux separates commands on a `;` WELDED to the previous token
    (measured on 3.7b — session-resolve's own constant records it). A guard that
    stops after the first subcommand accepts a smuggled second one."""
    with pytest.raises(sw.WriteVerbViolation) as exc:
        sw._assert_allowed(["tmux", "list-panes", "-F", "#{window_id};",
                            "kill-pane", "-t", PANE_TARGET])
    assert "kill-pane" in str(exc.value)

    with pytest.raises(sw.WriteVerbViolation):
        sw._assert_allowed(["tmux", "send-keys", "-t", PANE_TARGET, ";",
                            "kill-session"])


def test_the_seam_ignores_non_tmux_argv():
    sw._assert_allowed(["git", "kill-session"])
    sw._assert_allowed([])


def test_run_tmux_asserts_the_allowlist_even_on_a_dry_run():
    """A dry run must not be a way to build an argv nobody checked — the
    allowlist is a property of the argv, not of whether it executed."""
    ws = make_ws()
    with pytest.raises(sw.WriteVerbViolation):
        sw.run_tmux(ws, ["tmux", "kill-pane", "-t", PANE_TARGET], dry_run=True)


def test_the_reverify_read_list_is_derived_from_session_resolve():
    """ONE RULE, ONE PLACE. A read verb added to session-resolve must not leave
    the two modules disagreeing about what counts as a read."""
    assert sw.TMUX_REVERIFY_SUBCOMMANDS == tuple(sr.TMUX_READ_ONLY_SUBCOMMANDS)
    assert set(sw.TMUX_ALLOWED_SUBCOMMANDS) == (
        set(sw.TMUX_WRITE_SUBCOMMANDS) | set(sw.TMUX_REVERIFY_SUBCOMMANDS))


def test_write_and_read_subcommand_sets_are_disjoint():
    assert not (set(sw.TMUX_WRITE_SUBCOMMANDS)
                & set(sw.TMUX_REVERIFY_SUBCOMMANDS))


# =========================================================================== #
# Exit codes
# =========================================================================== #
def test_exit_codes_are_pairwise_distinct_and_reuse_session_resolves():
    """🔴 EVERY CODE IS PINNED TO ITS LITERAL, not just to itself.

    Every refusal test asserts `outcome.code == sw.EXIT_<X>`, which is a
    fixture-equals-the-constant comparison: renumber the constant and both sides
    move together. This is the one place that feeds a value the constant CANNOT
    equal — the literal an operator scripts against.
    """
    assert len(set(sw.ALL_EXIT_CODES)) == len(sw.ALL_EXIT_CODES)
    assert sw.EXIT_OK == sr.EXIT_RESOLVED == 0
    assert sw.EXIT_AMBIGUOUS == sr.EXIT_AMBIGUOUS == 2
    assert sw.EXIT_UNMATCHED == sr.EXIT_UNMATCHED == 3
    assert sw.EXIT_REMOTE_HOST == 4
    assert sw.EXIT_SCREEN_NOT_YOURS == 5
    assert sw.EXIT_SHELL_EXEC == 6
    assert sw.EXIT_UNSENT_PROMPT == 7
    assert sw.EXIT_TARGET_VANISHED == 8
    assert sw.EXIT_AMBIGUOUS_CLIENT == 9
    assert sw.EXIT_TMUX_FAILED == 10
    assert sw.EXIT_BAD_TEXT == 11
    assert sw.EXIT_RESTORE_FAILED == 12
    assert sw.EXIT_BAD_LOG_PATH == 13
    # 1 is left free for an uncaught interpreter error, as everywhere else.
    assert 1 not in sw.ALL_EXIT_CODES


def test_an_exit_code_names_a_CLASS_and_the_outcome_token_names_the_GATE():
    """🔴 THE CLAIM THIS FILE USED TO MAKE WAS FALSE. The exit-code comment read
    "every refusal has its own code so a caller can branch on WHICH gate stopped
    it". It never did: `EXIT_AMBIGUOUS_CLIENT` covers five distinct refusals and
    `EXIT_REMOTE_HOST` two.

    Rather than renumber into one-code-per-refusal (a table every caller would
    have to keep), the per-gate discriminator is the `outcome` TOKEN, which is
    emitted on every run into the audit log and into `--json`. This test pins
    the corrected claim so the false one cannot come back as prose.
    """
    # The collisions are real, and are named rather than denied.
    per_code = {}
    for token in sw.OUTCOME_TOKENS:
        per_code.setdefault(token.split(":", 1)[0], []).append(token)
    assert len(sw.OUTCOME_TOKENS) > len(sw.ALL_EXIT_CODES), (
        "if tokens ever became as coarse as codes they would add nothing")
    # Tokens are unique — they are the thing a caller branches on.
    assert len(set(sw.OUTCOME_TOKENS)) == len(sw.OUTCOME_TOKENS)
    assert set(per_code) == {"refused", "failed", "noop", "written"}


def test_the_outcome_token_ledger_matches_what_the_SOURCE_actually_emits():
    """🔴 A LEDGER PINNED TWO WAYS, failing when the set GROWS or SHRINKS.

    AST-scan every `outcome=` keyword and every `"outcome": <literal>` in
    session-write and compare the set against `OUTCOME_TOKENS`. A new gate
    cannot arrive without a name, and a name cannot linger after its gate is
    deleted — either direction is a red.

    The `dry-run:` prefix is excluded: `build_record` REWRITES a `written:*`
    token when `--dry-run` is set, so those are derived rather than emitted.
    """
    src = open(_SW_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "outcome" and isinstance(kw.value, ast.Constant):
                    emitted.add(kw.value.value)
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "outcome"
                        and isinstance(v, ast.Constant)):
                    emitted.add(v.value)
        # `record["outcome"] = "..."` rebinds
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for t in node.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value == "outcome"):
                    emitted.add(node.value.value)
    raw = {t for t in emitted if isinstance(t, str)}
    emitted = {t for t in raw if ":" in t}
    # 🔴 THE FILTER MUST REMOVE NOTHING. It exists only to drop a hypothetical
    # bare token, and a bare token is precisely what this ledger cannot see —
    # so if the filter ever starts doing work, the ledger has a blind spot.
    # `_refuse` now REQUIRES its token (see the test below); this is the
    # measurement that says the requirement is holding.
    assert raw == emitted, (
        f"colon-less outcome token(s) the ledger cannot see: "
        f"{sorted(raw - emitted)}")
    # POSITIVE CONTROL: the scan must actually have found something.
    assert len(emitted) > 20, f"the AST scan found only {sorted(emitted)}"
    assert emitted == set(sw.OUTCOME_TOKENS), (
        f"\n  emitted but unledgered: {sorted(emitted - set(sw.OUTCOME_TOKENS))}"
        f"\n  ledgered but unemitted: "
        f"{sorted(set(sw.OUTCOME_TOKENS) - emitted)}")


def test_every_refusal_NAMES_its_gate():
    """🔴 THE LEDGER'S OWN BLIND SPOT, CLOSED.

    `_refuse` used to take `outcome` as an ordinary keyword defaulting to the
    bare token `"refused"` — and the ledger test above filters the scanned
    tokens on `":" in t`, so that fallback was the ONE token the ledger was
    structurally unable to see. Every call site passed a token anyway; nothing
    pinned that they must, so a new gate that forgot one would have emitted an
    unbranchable name past a fully green suite and an intact ledger.

    Two assertions, because either alone is walkable:

      * `outcome` is a REQUIRED, KEYWORD-ONLY parameter, so a missing one is a
        TypeError rather than a silent downgrade — but a TypeError only fires
        on a path some test actually walks;
      * every `_refuse(...)` call in the SOURCE really passes it, which covers
        the paths no test walks. That is the structural half.
    """
    import inspect
    param = inspect.signature(sw._refuse).parameters["outcome"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, param.kind
    assert param.default is inspect.Parameter.empty, (
        "a default here is exactly the fallback the OUTCOME_TOKENS ledger "
        "cannot see")

    tree = ast.parse(open(_SW_PATH, encoding="utf-8").read())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_refuse"]
    # POSITIVE CONTROL on the scan: a scanner wired to nothing finds no
    # offenders and passes vacuously.
    assert len(calls) > 20, f"the scan found only {len(calls)} _refuse calls"
    without = sorted(c.lineno for c in calls
                     if not any(kw.arg == "outcome" for kw in c.keywords))
    assert without == [], (
        f"_refuse called with no outcome token at line(s) {without}")


# --------------------------------------------------------------------------- #
# 🔴 THE PAYLOAD-SITE LEDGER — every place a `send-keys` argv is built
#
# Nothing asserted that a payload site is preceded by validation. There are
# exactly two literal-payload sites today and both validate, but a THIRD — a
# fifth verb, a retry path, a "resend" convenience — would have passed the
# entire suite unvalidated, which is the wrapper's whole reason to exist
# rebuilt one function later.
#
# The ledger fails when the set GROWS or SHRINKS. It is a sorted TUPLE and not
# a set, deliberately: a DUPLICATED site is a growth too, and a set would
# collapse it into the row that was already there.
# --------------------------------------------------------------------------- #
SEND_KEYS_SITES = (
    ("verb_send", "keys", "'Enter'"),
    ("verb_send", "literal", "args.text"),
    ("verb_type", "literal", "args.text"),
)


def _send_keys_sites(source=None):
    """AST-scan for every `["tmux", "send-keys", ...]` argv list.

    -> a sorted tuple of (enclosing function, kind, source of the LAST argv
    element). `kind` is "literal" when the argv carries `-l` (a caller-shaped
    payload) and "keys" otherwise (tmux KEY NAMES, e.g. a bare Enter).
    """
    src = source if source is not None else open(_SW_PATH,
                                                 encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List) or len(node.elts) < 2:
            continue
        head = [e.value if isinstance(e, ast.Constant) else None
                for e in node.elts[:2]]
        if head != ["tmux", "send-keys"]:
            continue
        consts = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        owners = [f for f in funcs
                  if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)]
        owner = min(owners, key=lambda f: (f.end_lineno or f.lineno) - f.lineno,
                    default=None)
        rows.append((owner.name if owner is not None else "(module level)",
                     "literal" if "-l" in consts else "keys",
                     ast.unparse(node.elts[-1])))
    return tuple(sorted(rows))


def test_the_send_keys_scanner_can_actually_see_a_site():
    """🔴 BOTH CONTROLS ON THE SCANNER, before its verdict is read anywhere.

    POSITIVE: fed a synthetic source that HAS a site, it must find exactly that
    row — otherwise a scanner wired to nothing returns `()` and the ledger test
    below passes vacuously against an empty set, which is the reassuring zero
    this repo's rules single out.

    NEGATIVE: fed a source with no `send-keys` at all, it must find nothing —
    a scanner that matches everything would also make the ledger meaningless.
    """
    positive = (
        "def some_new_verb(ws, args, pane):\n"
        "    _issue(ws, ['tmux', 'send-keys', '-t', pane, '-l', '--',\n"
        "                args.text], args.dry_run)\n"
        "    _issue(ws, ['tmux', 'send-keys', '-t', pane, 'Enter'],\n"
        "           args.dry_run)\n")
    assert _send_keys_sites(positive) == (
        ("some_new_verb", "keys", "'Enter'"),
        ("some_new_verb", "literal", "args.text"))

    negative = ("def f(ws, args):\n"
                "    return ['tmux', 'list-panes', '-a']\n")
    assert _send_keys_sites(negative) == ()


def test_every_send_keys_payload_site_is_LEDGERED():
    """🔴 THE STRUCTURAL LEDGER, failing in BOTH directions.

    A new payload site cannot arrive unreviewed, and a site cannot quietly
    disappear (which would mean a verb stopped writing and every behavioural
    test still passed because it asserts a refusal).
    """
    sites = _send_keys_sites()
    assert sites == SEND_KEYS_SITES, (
        f"\n  new/changed site(s): "
        f"{sorted(set(sites) - set(SEND_KEYS_SITES))}"
        f"\n  ledgered but gone:   "
        f"{sorted(set(SEND_KEYS_SITES) - set(sites))}"
        f"\n  full scan:           {sites}")


def test_every_LITERAL_payload_site_is_preceded_by_validate_text():
    """🔴 THE RELATIONSHIP, not the components. RULES.md: a seam guard must pin
    a RELATIONSHIP, and every component here is individually tested already.

    For each ledgered site that carries `-l` (a caller-shaped payload), the
    enclosing function must call `validate_text` at a line STRICTLY BEFORE the
    argv is built. A site that validated afterwards would still refuse — and
    would already have typed the payload.
    """
    tree = ast.parse(open(_SW_PATH, encoding="utf-8").read())
    funcs = {n.name: n for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)}
    literal_owners = sorted({f for f, kind, _ in SEND_KEYS_SITES
                             if kind == "literal"})
    assert literal_owners, "the ledger names no literal-payload site at all"

    for name in literal_owners:
        fn = funcs[name]
        validations = [n.lineno for n in ast.walk(fn)
                       if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name)
                       and n.func.id == "validate_text"]
        assert validations, (
            f"{name} builds a literal `send-keys` payload and never calls "
            f"validate_text — that is the Bash-classifier bypass rebuilt")
        sites = [n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.List) and len(n.elts) >= 2
                 and [e.value if isinstance(e, ast.Constant) else None
                      for e in n.elts[:2]] == ["tmux", "send-keys"]
                 and "-l" in [e.value for e in n.elts
                              if isinstance(e, ast.Constant)]]
        assert sites, f"the ledger claims {name} has a literal site; it has none"
        assert min(validations) < min(sites), (
            f"{name} validates at line {min(validations)} but builds its "
            f"payload at line {min(sites)} — validation must come FIRST")


@pytest.mark.parametrize("verb", sorted({f.replace("verb_", "")
                                         for f, kind, _ in SEND_KEYS_SITES
                                         if kind == "literal"}))
def test_every_ledgered_payload_verb_REFUSES_an_unvalidatable_payload(verb):
    """🔴 THE BEHAVIOURAL HALF. A structural check type-checks past a wrong
    argument: `validate_text(something_else)` satisfies every assertion above
    while validating nothing the verb is about to send.

    So each ledgered literal-payload verb is driven with the audited execution
    character and must refuse with nothing reaching tmux. Derived from the
    ledger rather than hardcoded, so a fifth verb added to the ledger is
    exercised here automatically.
    """
    assert verb in sw.ALL_VERBS, verb
    outcome, ws = run_verb(verb, ADDR_TARGET, "--text",
                           "audit" + EXECUTING_CHAR)
    assert outcome.code == sw.EXIT_BAD_TEXT, outcome.lines
    assert ws.stub.writes == [], "nothing may reach tmux"


def test_every_verb_is_classified_pane_or_client():
    """Two-way pin against the parser's OWN choices, so a fifth verb cannot
    arrive unclassified and silently skip the pane half of the re-verify."""
    choices = None
    for action in sw.build_parser()._actions:
        if action.dest == "verb":
            choices = tuple(action.choices)
    assert choices == sw.ALL_VERBS
    assert set(sw.ALL_VERBS) == set(sw.PANE_VERBS) | set(sw.CLIENT_VERBS)
    assert not (set(sw.PANE_VERBS) & set(sw.CLIENT_VERBS))
    assert set(sw._VERB_FUNCS) == set(sw.ALL_VERBS)


# =========================================================================== #
# G1 — re-resolve at write time
# =========================================================================== #
def test_a_raw_address_is_re_resolved_not_passed_through_to_tmux():
    """🔴 THE POINT OF THE WRAPPER. argv carries a SELECTOR; what reaches tmux is
    the RESOLVED pane id. A pass-through implementation would put the selector
    string itself in `-t`, and the fixture makes those two different strings."""
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    (write,) = ws.stub.writes
    assert write == ["tmux", "send-keys", "-t", PANE_TARGET, "-l", "--",
                     WRITE_TEXT]
    assert ADDR_TARGET not in write, "the selector must never reach tmux"


def test_a_codename_selector_resolves_to_the_same_pane():
    """The selector vocabulary is session-resolve's, not a second one."""
    outcome, ws = run_verb("type", WIN_TARGET_ID, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK
    assert ws.stub.writes[0][3] == PANE_TARGET


def test_an_ambiguous_selector_is_refused_with_session_resolves_exit_code():
    # SESSION_TARGET alone matches BOTH of its windows.
    outcome, ws = run_verb("type", SESSION_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_AMBIGUOUS == 2
    assert ws.stub.writes == [], "an ambiguous selector must write nothing"
    res = sr.resolve(SESSION_TARGET, make_sources())
    assert_message_is(
        outcome, sw.MSG_AMBIGUOUS, selector=SESSION_TARGET,
        count=res["candidate_count"],
        candidates=", ".join(str(c["address"]) for c in res["candidates"]))


def test_an_unmatched_selector_is_refused():
    missing = "no-such-window-anywhere-9x"
    outcome, ws = run_verb("type", missing, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_UNMATCHED == 3
    assert ws.stub.calls == [], "an unmatched selector must not even re-verify"
    res = sr.resolve(missing, make_sources())
    assert_message_is(outcome, sw.MSG_UNMATCHED, selector=missing,
                      reason=res["reason"])


# =========================================================================== #
# G2 — LOCAL HOST ONLY (the highest-consequence guard in the PR)
# =========================================================================== #
def test_a_remote_target_is_refused_by_name():
    """🔴 A laptop-resolved address written to LOCAL tmux does not fail — it
    hits whatever local window shares that address. Measured, 30 of 92 targets
    are remote, so this state is reachable every single run."""
    outcome, ws = run_verb("type", ADDR_REMOTE, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_REMOTE_HOST
    assert ws.stub.calls == [], "a remote target must not even re-verify"
    assert_message_is(outcome, sw.MSG_REMOTE_HOST, selector=ADDR_REMOTE,
                      target_host=REMOTE_HOST, local_host=LOCAL_HOST)


@pytest.mark.parametrize("verb,extra", [
    ("type", ["--text", WRITE_TEXT]),
    ("send", ["--text", WRITE_TEXT]),
    ("focus", [AT_KB]),
    ("dismiss", [AT_KB]),
])
def test_every_verb_refuses_a_remote_target(verb, extra):
    """The guard belongs to the pipeline, not to one verb. A per-verb copy is
    how N-1 of N call sites end up wrong in the same direction."""
    outcome, ws = run_verb(verb, ADDR_REMOTE, *extra)
    assert outcome.code == sw.EXIT_REMOTE_HOST
    assert ws.stub.writes == []


def test_a_local_target_is_not_refused():
    """POSITIVE CONTROL on G2 — a guard that refuses everything is not a guard.
    LOCAL_HOST is not a production host name, so this also kills a mutant that
    hardcodes `"workbench"`."""
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK
    assert len(ws.stub.writes) == 1


def test_an_unknown_local_host_is_refused_rather_than_assumed():
    """Locality that cannot be PROVEN is not locality. With no `local_host` in
    the payload and `--host all`, session-resolve reports LOCAL_HOST_UNKNOWN and
    builds only local-tmux targets — which LOOK local and are unverified."""
    src_over = {"local_host": None, "host": sr.HOST_ALL,
                "sm_payload": sm_payload(local_host=None, with_remote=False)}
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                           ws=make_ws(src_over=src_over))
    assert outcome.code == sw.EXIT_REMOTE_HOST
    assert ws.stub.calls == []
    assert_message_is(outcome, sw.MSG_LOCAL_HOST_UNKNOWN,
                      selector=ADDR_TARGET,
                      target_host=sr.LOCAL_HOST_UNKNOWN,
                      local_host=sr.LOCAL_HOST_UNKNOWN)


# =========================================================================== #
# G3 — re-verify IMMEDIATELY before the write
# =========================================================================== #
def test_the_reverify_runs_before_any_write():
    """Ordering is the guard. A check that runs after the write is decoration."""
    _outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT)
    subs = ws.stub.subcommands
    first_write = min(i for i, s in enumerate(subs)
                      if s in sw.TMUX_WRITE_SUBCOMMANDS)
    for read in sw.TMUX_REVERIFY_SUBCOMMANDS:
        assert read in subs[:first_write], (
            f"{read} must run BEFORE the first write; got {subs}")


def test_a_window_that_vanished_between_resolve_and_write_is_refused():
    """🔴 THE STALE-OBSERVATION CLASS. Resolution consults four sources — one of
    them shells out to session-manager, which scans both hosts — so seconds pass.
    Here the window exists at resolution and is GONE from the fresh read."""
    stub = TmuxStub(windows=windows_raw(drop=(WIN_TARGET_ID,)))
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_TARGET_VANISHED
    assert ws.stub.writes == [], "nothing may be written to a vanished window"
    assert_message_is(
        outcome, sw.MSG_VANISHED, address=ADDR_TARGET,
        detail=f"tmux no longer lists window {WIN_TARGET_ID} in session "
               f"{SESSION_TARGET!r}")


def test_a_pane_that_vanished_between_resolve_and_write_is_refused():
    """A window can survive while the PANE the write targets does not."""
    surviving = "\n".join(l for l in panes_raw().splitlines()
                          if PANE_TARGET not in l)
    surviving += "\n" + _pane_line(SESSION_TARGET, WIN_TARGET_INDEX,
                                   WIN_TARGET_ID, "%9999", CWD_TARGET, "other")
    stub = TmuxStub(panes=surviving)
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_TARGET_VANISHED
    assert ws.stub.writes == []
    assert_message_is(
        outcome, sw.MSG_VANISHED, address=ADDR_TARGET,
        detail=f"pane {PANE_TARGET!r} is not among the panes tmux now lists "
               f"for that window (%9999)")


def test_a_client_verb_does_not_require_the_pane_to_survive():
    """POSITIVE CONTROL that the pane check is SCOPED, not universal — dismiss
    addresses a client, so a vanished pane must not refuse it. Without this,
    a mutant making `need_pane` always-True passes every other test."""
    surviving = "\n".join(l for l in panes_raw().splitlines()
                          if PANE_TARGET not in l)
    stub = TmuxStub(panes=surviving, clients=clients_raw(popups=1))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "detach-client", "-t", POPUP_ONE_TTY]]


def test_a_failed_reverify_read_is_refused_not_assumed_fine():
    """An unreadable tmux is UNMEASURED, never 'the window is fine'."""
    class Failing(TmuxStub):
        def __call__(self, argv, timeout=None):
            self.calls.append(list(argv))
            if argv[1] == "list-windows":
                return 3, "", "server not found"
            return 0, "", ""

    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                           ws=make_ws(stub=Failing()))
    assert outcome.code == sw.EXIT_TARGET_VANISHED
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_VANISHED, address=ADDR_TARGET,
                      detail="tmux list-windows exited 3: server not found")


# =========================================================================== #
# G5 — focus is a pkill-class action
# =========================================================================== #
def test_focus_refuses_without_the_flag_and_touches_nothing():
    """🔴 THE OPERATOR'S SCREEN. The refusal is checked BEFORE resolution, so a
    refused focus does not even read the world."""
    outcome, ws = run_verb("focus", ADDR_TARGET)
    assert outcome.code == sw.EXIT_SCREEN_NOT_YOURS
    assert ws.stub.calls == []
    assert_message_is(outcome, sw.MSG_FOCUS_NEEDS_FLAG)


def test_the_focus_refusal_is_exactly_the_agreed_wording():
    """Pinned as a WHOLE string including the two-space continuation indent —
    this text is the operator-facing contract, not an implementation detail."""
    assert sw.MSG_FOCUS_NEEDS_FLAG == (
        "REFUSED: focus changes your screen.\n"
        "  re-run with --i-am-at-the-keyboard")


def test_dismiss_refuses_without_the_flag_and_touches_nothing():
    """🔴 G5 NOW COVERS `dismiss`, AND IT DID NOT. The first version gated only
    `focus`, on the reading that taking an overlay DOWN is not the same act as
    raising a window. Two things are wrong with that:

      * RULES.md names the hazard as "changes what a human is looking at RIGHT
        NOW", and an overlay vanishing does exactly that;
      * per G6 this tool cannot PROVE the client it detaches is an overlay —
        "popup" is a statement about the client's TERM. Measured, tmux refuses
        an ordinary same-server nested attach ("unset $TMUX to force"), so the
        misclassification needs a second server or a deliberate force — narrow,
        but the consequence is detaching a REAL session, not closing an overlay.

    Checked BEFORE resolution, like focus: a refused dismiss does not read the
    world either.
    """
    stub = TmuxStub(clients=clients_raw(popups=1))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_SCREEN_NOT_YOURS
    assert ws.stub.calls == [], "not even a read before the gate"
    assert_message_is(outcome, sw.MSG_DISMISS_NEEDS_FLAG)


def test_the_two_screen_refusals_are_DIFFERENT_STRINGS():
    """🔴 One shared message would let a mutant move the gate between the two
    verbs with every whole-string assertion still green. They are different
    actions with different consequences and are spelled separately."""
    assert sw.MSG_FOCUS_NEEDS_FLAG != sw.MSG_DISMISS_NEEDS_FLAG
    assert set(sw.SCREEN_VERBS) == {"focus", "dismiss"}
    assert len(set(sw.SCREEN_VERBS.values())) == 2
    # Both must actually name the flag, or the refusal is a dead end.
    for msg in sw.SCREEN_VERBS.values():
        assert AT_KB in msg


def test_the_screen_verbs_are_exactly_the_client_directed_verbs():
    """🔴 A LEDGER, failing when the set GROWS or SHRINKS. Every client-directed
    verb issues `detach-client`, `select-window` or `switch-client`, and all
    three change the screen — so a fifth verb that addresses a client cannot
    arrive without either taking the gate or forcing this line to be edited."""
    assert set(sw.SCREEN_VERBS) == set(sw.CLIENT_VERBS)
    assert set(sw.SCREEN_VERBS).isdisjoint(sw.PANE_VERBS)


@pytest.mark.parametrize("verb,extra", [
    ("type", ["--text", WRITE_TEXT]),
    ("send", ["--text", WRITE_TEXT]),
    ("dismiss", [AT_KB]),
])
def test_no_other_verb_ever_changes_the_screen_THE_WAY_FOCUS_DOES(verb, extra):
    """🔴 focus must never fire as a SIDE EFFECT. No other verb may emit
    select-window or switch-client, whatever else it does.

    `dismiss` carries the keyboard flag here DELIBERATELY: without it the run is
    refused at G5 before issuing anything, and this assertion would pass over an
    empty list — vacuously green, and green for a reason that has nothing to do
    with what it claims. The `calls` assertion below is the control that makes
    the pass mean something.
    """
    stub = TmuxStub(clients=clients_raw(popups=1))
    _outcome, ws = run_verb(verb, ADDR_TARGET, *extra, ws=make_ws(stub=stub))
    assert ws.stub.calls, "the verb must have RUN, or this proves nothing"
    for argv in ws.stub.calls:
        assert argv[1] not in ("select-window", "switch-client"), argv


def test_focus_with_the_flag_proceeds():
    """POSITIVE CONTROL on G5."""
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard")
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert any(a[1] == "select-window" for a in ws.stub.writes)


# =========================================================================== #
# focus — client selection, record, raise, restore
# =========================================================================== #
def test_focus_picks_the_focused_base_terminal_not_the_first_one():
    """🔴 REFUTES THE BRIEF (and `visibility.base_terminal`). TWO base terminals
    were measured on the live host, and `compute_visibility` publishes
    `base_all[0]`. The fixture puts the UNFOCUSED one first, so an
    implementation reading that field targets the wrong screen and this fails."""
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           "--stay")
    assert outcome.code == sw.EXIT_OK, outcome.lines
    switches = [a for a in ws.stub.writes if a[1] == "switch-client"]
    assert switches, "the focused base terminal is on another session"
    assert switches[0][3] == BASE_FOCUSED_TTY
    assert BASE_UNFOCUSED_TTY not in switches[0]


def test_focus_records_and_restores_both_tmux_dimensions():
    """RULES.md names TWO dimensions and this covers both: the CLIENT's session
    and the SESSION's active window. The fixture makes them differ from the
    target on purpose, so a restore that skipped either is visible."""
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard")
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [
        ["tmux", "select-window", "-t", ADDR_TARGET],
        ["tmux", "switch-client", "-c", BASE_FOCUSED_TTY, "-t", SESSION_TARGET],
        ["tmux", "select-window", "-t", f"{SESSION_TARGET}:{WIN_PREV_ID}"],
        ["tmux", "switch-client", "-c", BASE_FOCUSED_TTY, "-t", SESSION_CLIENT],
    ]
    joined = _norm(" ".join(outcome.lines))
    assert f"recorded: client {BASE_FOCUSED_TTY} was on session " \
           f"'{SESSION_CLIENT}'" in joined
    assert WIN_PREV_ID in joined


def test_focus_says_the_i3_workspace_is_out_of_scope():
    """🔴 A restore rule about one dimension must not read as covering another.
    This tool is tmux-only and never touches i3; every focus result says so."""
    outcome, _ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard")
    assert sw.MSG_FOCUS_SCOPE in outcome.lines
    assert "i3" in sw.MSG_FOCUS_SCOPE and "NOT changed" in sw.MSG_FOCUS_SCOPE


def test_focus_stay_does_not_restore():
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           "--stay")
    assert outcome.code == sw.EXIT_OK
    assert ws.stub.writes == [
        ["tmux", "select-window", "-t", ADDR_TARGET],
        ["tmux", "switch-client", "-c", BASE_FOCUSED_TTY, "-t", SESSION_TARGET],
    ]
    assert outcome.record["outcome"] == "written:focus-stay"


def test_a_failed_restore_is_reported_and_changes_the_exit_code():
    """🔴 The screen is then somewhere the operator did not put it. That is the
    one outcome a caller must never learn about by silence."""
    stub = TmuxStub(rc_for={})

    calls = {"switch": 0}
    real = stub.__call__

    def failing(argv, timeout=None):
        if argv[1] == "switch-client":
            calls["switch"] += 1
            if calls["switch"] == 2:        # the RESTORE switch, not the raise
                stub.calls.append(list(argv))
                return 1, "", "can't find client"
        return real(argv, timeout)

    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           ws=make_ws(stub=stub, runner=failing))
    assert outcome.code == sw.EXIT_RESTORE_FAILED
    assert outcome.code != sw.EXIT_OK
    detail = (f"could not return client {BASE_FOCUSED_TTY} to session "
              f"{SESSION_CLIENT!r} (tmux switch-client exited 1: "
              f"can't find client)")
    assert _norm(sw.MSG_RESTORE_FAILED.format(detail=detail)) in \
        _norm(" ".join(outcome.lines))


def test_a_half_completed_raise_still_restores_what_it_moved():
    """🔴 RULES.md: restore "including on failure". The dangerous gap is BETWEEN
    the two raise commands — select-window lands, switch-client errors, and an
    early return leaves the operator's session pointing at a window this tool
    chose."""
    stub = TmuxStub()
    real = stub.__call__
    seen = {"switch": 0}

    def failing(argv, timeout=None):
        if argv[1] == "switch-client":
            seen["switch"] += 1
            if seen["switch"] == 1:         # the RAISE switch fails
                stub.calls.append(list(argv))
                return 1, "", "no such session"
        return real(argv, timeout)

    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           ws=make_ws(stub=stub, runner=failing))
    assert outcome.code == sw.EXIT_TMUX_FAILED
    # select-window moved the session's active window; that MUST be put back,
    # and the switch-client that never succeeded must NOT be "restored".
    assert ws.stub.writes == [
        ["tmux", "select-window", "-t", ADDR_TARGET],
        ["tmux", "switch-client", "-c", BASE_FOCUSED_TTY, "-t", SESSION_TARGET],
        ["tmux", "select-window", "-t", f"{SESSION_TARGET}:{WIN_PREV_ID}"],
    ]
    assert outcome.record["restored"] is True
    assert outcome.record["moved_client"] is False


def test_focus_makes_at_most_one_raise():
    """No ping-pong. The raise/restore pair is two switches by construction; a
    third raise would be the alternation the rule forbids."""
    _outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard")
    raises = [a for a in ws.stub.writes
              if a == ["tmux", "select-window", "-t", ADDR_TARGET]]
    assert len(raises) == 1


def test_focus_refuses_when_two_base_terminals_are_both_focused():
    """Ambiguity is refused, never guessed — matching session-resolve's stance
    on multiple matches."""
    stub = TmuxStub(clients=clients_raw(popups=0, base=2).replace(
        "attached,UTF-8", "attached,focused,UTF-8"))
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == []
    assert "--client TTY" in " ".join(outcome.lines)


def test_focus_refuses_when_no_base_terminal_is_attached():
    stub = TmuxStub(clients=clients_raw(popups=2, base=0))
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_NO_BASE_TERMINAL,
                      attached=SUMMARY_NO_BASE_TERMINAL)


def test_focus_accepts_an_explicit_client():
    stub = TmuxStub(clients=clients_raw(popups=0, base=2).replace(
        "attached,UTF-8", "attached,focused,UTF-8"))
    outcome, ws = run_verb("focus", ADDR_TARGET, "--i-am-at-the-keyboard",
                           "--stay", "--client", BASE_UNFOCUSED_TTY,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    # 🔴 The named client is already attached to SESSION_TARGET, so NO
    # switch-client is needed — only the window is raised. That is the second
    # claim here: the raise is conditional on the client's session differing,
    # not issued unconditionally.
    assert ws.stub.writes == [["tmux", "select-window", "-t", ADDR_TARGET]]
    assert not any(a[1] == "switch-client" for a in ws.stub.writes)


# =========================================================================== #
# dismiss
# =========================================================================== #
def test_dismiss_with_no_popups_is_a_noop_that_writes_nothing():
    """Exit 0 and NO write. The requested end state already holds; a caller that
    retried on non-zero would loop forever against a correct machine."""
    stub = TmuxStub(clients=clients_raw(popups=0, base=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_NO_POPUPS, address=ADDR_TARGET)


def test_dismiss_with_exactly_one_popup_detaches_it():
    stub = TmuxStub(clients=clients_raw(popups=1))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "detach-client", "-t", POPUP_ONE_TTY]]


def test_dismiss_with_two_popups_refuses_rather_than_guessing():
    """MEASURED LIVE: there really are two popups attached on this host, so this
    is the default state, not an edge case. Picking one is a coin flip on which
    of the operator's overlays disappears."""
    stub = TmuxStub(clients=clients_raw(popups=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_AMBIGUOUS_POPUP, count=2,
                      candidates=SUMMARY_TWO_POPUPS_ONLY)


def test_the_dismiss_RESULT_states_how_the_popup_was_CLASSIFIED():
    """🔴 A SAFETY CLAIM, pinned so it cannot be quietly dropped as verbosity.

    Per G6, "popup" is decided on the client's TERM and is NOT proof of a
    `display-popup` overlay. An operator whose session just vanished has to be
    able to read, from the line this tool printed, that it may have been
    DETACHED rather than closed — and which client it was. That sentence is the
    only thing standing between "the overlay closed" and a silent wrong model,
    so it is asserted rather than left to review.
    """
    stub = TmuxStub(clients=clients_raw(popups=1))
    outcome, _ws = run_verb("dismiss", ADDR_TARGET, AT_KB,
                            ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    line = _norm(" ".join(outcome.lines))
    assert POPUP_ONE_TTY in line, "the client detached must be named"
    assert POPUP_TERM in line, "the TERM the decision was made on must be shown"
    assert "classified as a popup on its TERM" in line
    assert "not proof of a display-popup overlay" in line


def test_dismiss_with_two_popups_and_an_explicit_client_detaches_that_one():
    stub = TmuxStub(clients=clients_raw(popups=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, "--client", POPUP_TWO_TTY,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "detach-client", "-t", POPUP_TWO_TTY]]


@pytest.mark.parametrize("tty", [BASE_UNFOCUSED_TTY, BASE_FOCUSED_TTY])
def test_dismiss_refuses_to_detach_a_base_terminal(tty):
    """🔴 G6. Detaching the base terminal drops the operator out of tmux
    entirely. `--client` searches ALL attached clients precisely so this guard
    is REACHABLE — a chooser that only ever looked at popups would make it
    unreachable, and an unreachable guard is one no test can honestly pin."""
    stub = TmuxStub(clients=clients_raw(popups=2, base=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, "--client", tty,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == [], "no detach may be issued for a base terminal"
    session = (SESSION_TARGET if tty == BASE_UNFOCUSED_TTY else SESSION_CLIENT)
    assert_message_is(outcome, sw.MSG_BASE_TERMINAL, tty=tty, session=session)


def test_focus_refuses_an_explicit_client_that_is_a_POPUP():
    """🔴 THE MIRROR OF G6, AND IT WAS OPEN. `pick_base_terminal` returned
    `pick_named_client(...)` unconditionally when `--client` was given, so the
    base-terminal derivation — the only thing that made the chosen client a
    real screen — was skipped entirely whenever the caller named one. Result:
    `focus --client <popup tty>` was ACCEPTED and `switch-client -c <popup>`
    steered an overlay, moving a transient client while the screen the operator
    is actually looking at never moved.

    Exactly the shape G6 was built to avoid in the other direction, which is why
    `pick_named_client` deliberately searches ALL clients: so both assertions
    are REACHABLE from the explicit path.
    """
    stub = TmuxStub(clients=clients_raw(popups=2, base=2))
    outcome, ws = run_verb("focus", ADDR_TARGET, AT_KB,
                           "--client", POPUP_ONE_TTY, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == [], "no switch-client may steer an overlay"
    assert not any(a[1] == "switch-client" for a in ws.stub.writes)
    assert_message_is(outcome, sw.MSG_NOT_BASE_TERMINAL, tty=POPUP_ONE_TTY,
                      session=SESSION_CLIENT)


def test_the_two_client_assertions_are_mirrors_and_neither_accepts_the_others_client():
    """🔴 A RELATIONSHIP, not two components. Each verb's assertion must refuse
    exactly the client kind the OTHER verb requires — a structural check that
    stays true even if `popup`/`base_terminal` stop being complements.

    Pinned as a table so a third client kind cannot arrive unclassified.
    """
    popup = {"tty": POPUP_ONE_TTY, "session": SESSION_CLIENT,
             "popup": True, "base_terminal": False}
    base = {"tty": BASE_FOCUSED_TTY, "session": SESSION_CLIENT,
            "popup": False, "base_terminal": True}

    # dismiss's assertion: refuses the base terminal, admits the popup.
    assert sw._assert_not_base_terminal(popup) is None
    assert sw._assert_not_base_terminal(base) is not None
    # focus's assertion: exactly the other way round.
    assert sw._assert_is_base_terminal(base) is None
    assert sw._assert_is_base_terminal(popup) is not None

    # ...and they refuse with DIFFERENT messages, or a mutant could swap them.
    a = sw._assert_not_base_terminal(base)
    b = sw._assert_is_base_terminal(popup)
    assert a.lines[0] != b.lines[0]
    assert a.record["outcome"] != b.record["outcome"]


def test_the_base_terminal_guard_is_the_last_word_on_any_chosen_client():
    """Unit-level proof that the guard is a property of the CLIENT, not of the
    path that chose it — so a future chooser cannot route around it."""
    assert sw._assert_not_base_terminal({"tty": "x", "session": "y",
                                         "base_terminal": False}) is None
    refusal = sw._assert_not_base_terminal(
        {"tty": UNATTACHED_TTY, "session": SESSION_REMOTE,
         "base_terminal": True})
    assert refusal is not None
    assert refusal.code == sw.EXIT_AMBIGUOUS_CLIENT


def test_dismiss_refuses_an_unattached_client():
    stub = TmuxStub(clients=clients_raw(popups=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB, "--client", UNATTACHED_TTY,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_NO_SUCH_CLIENT, tty=UNATTACHED_TTY,
                      attached=SUMMARY_ALL_FOUR_CLIENTS)


def test_dismiss_uses_the_FRESH_client_list_not_the_resolution_time_one():
    """🔴 "Re-verify at the moment you ACT" applies to the popup about to be
    detached, not only to the window about to be written. Resolution saw two
    popups; by write time only one remains, and dismiss must proceed (not
    refuse as ambiguous) and detach the survivor."""
    src_over = {"clients_raw": clients_raw(popups=2)}
    stub = TmuxStub(clients=clients_raw(popups=1))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, AT_KB,
                           ws=make_ws(stub=stub, src_over=src_over))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "detach-client", "-t", POPUP_ONE_TTY]]


def test_dismiss_does_not_require_the_target_to_be_covered():
    """🔴 REFUTES THE BRIEF. Measured on the live host, the brief's own target is
    `visible=False, covered=False` WITH two popups attached — `covered` is only
    true when the base terminal is showing the target. Gating dismiss on it
    would refuse in exactly the state the operator is in."""
    res = sr.resolve(ADDR_TARGET, make_sources(clients_raw=clients_raw(popups=1,
                                                                       base=0)))
    assert res["target"]["visibility"]["covered"] is False
    stub = TmuxStub(clients=clients_raw(popups=1, base=0))
    outcome, _ws = run_verb("dismiss", ADDR_TARGET, AT_KB, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines


# =========================================================================== #
# type / send mechanics
# =========================================================================== #
def test_type_sends_literal_text_and_no_enter():
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK
    assert ws.stub.writes == [
        ["tmux", "send-keys", "-t", PANE_TARGET, "-l", "--", WRITE_TEXT]]
    assert outcome.record["submitted"] is False


def test_send_with_text_types_then_presses_enter_as_two_commands():
    """Two separate argv, so the SUBMIT is an auditable act rather than a
    character buried inside a payload."""
    outcome, ws = run_verb("send", ADDR_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [
        ["tmux", "send-keys", "-t", PANE_TARGET, "-l", "--", WRITE_TEXT],
        ["tmux", "send-keys", "-t", PANE_TARGET, "Enter"]]
    assert outcome.record["submitted"] is True


def test_send_without_text_presses_enter_only():
    outcome, ws = run_verb("send", ADDR_TARGET)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "send-keys", "-t", PANE_TARGET, "Enter"]]


def test_type_requires_text():
    outcome, ws = run_verb("type", ADDR_TARGET)
    assert outcome.code == sw.EXIT_BAD_TEXT
    assert ws.stub.calls == []
    assert_message_is(outcome, sw.MSG_TYPE_NEEDS_TEXT)


def test_a_tmux_write_that_fails_is_reported_not_swallowed():
    stub = TmuxStub(rc_for={"send-keys": (1, "can't find pane: %9301")})
    outcome, _ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                            ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_TMUX_FAILED
    assert_message_is(outcome, sw.MSG_TMUX_FAILED, subcommand="send-keys",
                      rc=1, stderr="can't find pane: %9301")


# =========================================================================== #
# The two MEASURED tmux payload hazards
# =========================================================================== #
@pytest.mark.parametrize("verb", ["type", "send"])
@pytest.mark.parametrize("ch,name", [("\n", "a newline"),
                                     ("\r", "a carriage return")])
def test_text_containing_a_submitting_character_is_refused(verb, ch, name):
    """🔴 MEASURED on tmux 3.7b in a private -L server:

           tmux send-keys -t %0 -l -- $'X\\nY'  ->  pane received X, ENTER, Y

    So a `\\n` inside `type`'s payload SUBMITS, silently breaking the "literal,
    no Enter" contract — and on a shell pane it EXECUTES. The offset in the
    refusal is COMPUTED, so a mutant that drops the scan and hardcodes the
    message cannot pass: the fixture puts the character at a non-zero offset."""
    prefix = "audit the queue"
    text = prefix + ch + "then stop"
    assert text.find(ch) == len(prefix) != 0
    outcome, ws = run_verb(verb, ADDR_TARGET, "--text", text)
    assert outcome.code == sw.EXIT_BAD_TEXT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_TEXT_SUBMITS, name=name,
                      offset=len(prefix))


@pytest.mark.parametrize("verb", ["type", "send"])
def test_text_ending_in_the_tmux_separator_is_refused(verb):
    """🔴 MEASURED: `tmux send-keys -t %0 -l -- 'abc;'` returns rc 0 and the pane
    receives "abc". tmux eats a `;` welded to the end of an argv token. Silent
    truncation of the payload, reported as success — refused rather than
    silently mutated behind the caller's back."""
    outcome, ws = run_verb(verb, ADDR_TARGET, "--text",
                           "restart the poller" + sw.TMUX_ARGV_SEPARATOR)
    assert outcome.code == sw.EXIT_BAD_TEXT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_TEXT_TRAILING_SEP,
                      sep=sw.TMUX_ARGV_SEPARATOR)


def test_a_separator_in_the_MIDDLE_of_the_text_is_allowed():
    """POSITIVE CONTROL — the refusal is about a TRAILING separator only. A
    mutant banning every `;` would break a legitimate shell payload, and every
    other test in this pair would still pass."""
    assert sw.TMUX_ARGV_SEPARATOR in WRITE_TEXT
    assert not WRITE_TEXT.endswith(sw.TMUX_ARGV_SEPARATOR)
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK
    assert ws.stub.writes[0][-1] == WRITE_TEXT


def test_text_containing_a_nul_byte_is_refused():
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", "ab\x00cd")
    assert outcome.code == sw.EXIT_BAD_TEXT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_TEXT_NUL, offset=2)


def test_validate_text_accepts_ordinary_payloads():
    """POSITIVE CONTROL on the validator itself."""
    for good in ("", "plain", "-l --dash-leading", "a;b", "tabs\there",
                 "unicode ✓ ok"):
        assert sw.validate_text(good) is None, good


# =========================================================================== #
# G13 — the --text ALLOWLIST (the audited bypass, and why it is not a denylist)
# =========================================================================== #
#: 🔴 THE MEASURED EXECUTION EVENT. Ctrl-O is readline `operate-and-get-next`, a
#: BASH DEFAULT. Reproduced twice independently in a private `-L` tmux server:
#:
#:   send-keys -t %0 -l -- "touch <d>/EXECUTED\x0f"   (no Enter) -> file CREATED
#:   the same payload without it            (no Enter) -> nothing ran
#:
#: The second line is the negative control: the rig can tell the two apart, so
#: the first line is a measurement and not an artefact.
EXECUTING_CHAR = "\x0f"
assert not EXECUTING_CHAR.isprintable()
assert EXECUTING_CHAR not in sw.SUBMITTING_CHARS, (
    "the whole point is that this character was NOT on the old denylist")


#: Codepoints that must be refused, and WHY each is in the list. Every one of
#: these was ACCEPTED by the denylist this allowlist replaced.
@pytest.mark.parametrize("ch,why", [
    ("\x0f", "Ctrl-O — readline operate-and-get-next, measured to EXECUTE"),
    ("\x1b", "ESC — starts every terminal escape sequence"),
    ("\x1ba", "ESC a — zsh emacs accept-and-hold, another execution event"),
    ("\x16", "Ctrl-V — readline quoted-insert, smuggles the next byte"),
    ("\x03", "Ctrl-C — sends SIGINT to the foreground job"),
    ("\x04", "Ctrl-D — EOF, can close the operator's shell"),
    ("\x07", "BEL"),
    ("\x7f", "DEL"),
    ("\x1a", "Ctrl-Z — suspends the foreground job"),
    ("", "U+E000 private use — renders INVISIBLE in a diff or a grep"),
])
def test_a_character_outside_the_allowlist_is_refused_by_codepoint(ch, why):
    """🔴 THE AUDITED BYPASS, and the class it belongs to.

    None of these is on any denylist this file ever had; all of them are refused
    now because the question asked is "is this character ALLOWED", not "is it
    one of the ones we thought of". `why` is carried so a future reader can see
    that the list is a sample of a class rather than a new denylist — adding
    `\\x0f` alone was the fix explicitly rejected.
    """
    text = "audit the queue" + ch
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", text)
    assert outcome.code == sw.EXIT_BAD_TEXT, why
    assert ws.stub.writes == [], "nothing may reach tmux"
    # The offset is COMPUTED — a mutant hardcoding 0 cannot pass, because the
    # offending character is never first.
    idx = min(i for i, c in enumerate(text) if not sw.TEXT_IS_ALLOWED(c))
    assert idx != 0
    assert_message_is(outcome, sw.MSG_TEXT_NOT_PRINTABLE,
                      codepoint=f"{ord(text[idx]):04X}",
                      category=unicodedata.category(text[idx]), offset=idx)


# --------------------------------------------------------------------------- #
# 🔴 OFFSET 0 — the one position every other fixture in this file avoids
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ch,template,slots,why", [
    (EXECUTING_CHAR, "MSG_TEXT_NOT_PRINTABLE",
     {"codepoint": "000F", "category": "Cc", "offset": 0},
     "the audited execution event, at the position the scan could not see"),
    ("\x1b", "MSG_TEXT_NOT_PRINTABLE",
     {"codepoint": "001B", "category": "Cc", "offset": 0},
     "ESC — the other control character with an accept-line binding"),
    ("\x00", "MSG_TEXT_NUL", {"offset": 0},
     "the NUL diagnosis is reachable from offset 0 too"),
    ("\n", "MSG_TEXT_SUBMITS", {"name": "a newline", "offset": 0},
     "a LEADING newline submits the pane's existing buffer"),
    ("\xa0", "MSG_TEXT_INVISIBLE",
     {"name": "NO-BREAK SPACE", "codepoint": "00A0", "category": "Zs",
      "offset": 0},
     "the invisible diagnosis, likewise"),
])
def test_a_disallowed_character_at_offset_ZERO_is_refused(ch, template, slots,
                                                          why):
    """🔴 THE HOLE THE REST OF THIS SECTION LEAVES OPEN — AND IT IS THE
    EXPLOIT'S OWN POSITION.

    Every other disallowed-character fixture here puts the character after a
    printable prefix, and two of them deliberately ASSERT `offset != 0` in order
    to kill a mutant that hardcodes the reported offset. The price of that
    choice was that the FIRST codepoint of `--text` was never scanned by any
    test: MEASURED on this branch against the shipped file,

        for idx, ch in enumerate(text):  ->  enumerate(text[1:], 1)

    SURVIVED all 161 tests AND the committed 52-mutant sweep, leaving `type`
    able to deliver a LEADING Ctrl-O — the exact deploy blocker #582 was
    reopened for, one character earlier in the string.

    🔴 BOTH ASSERTIONS ARE KEPT. This one covers offset 0; the `offset != 0`
    ones still kill the hardcoded-0 mutant. They are different mutants and they
    need different fixtures — weakening either to serve the other trades one
    blind spot for the other.

    All four diagnosis branches are exercised here, because "the scan starts at
    1" is invisible in whichever branch happens to be untested.
    """
    if slots.get("sep") is None and template == "MSG_TEXT_INVISIBLE":
        slots = dict(slots, sep=sw.TMUX_ARGV_SEPARATOR)
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", ch + "audit")
    assert outcome.code == sw.EXIT_BAD_TEXT, why
    assert ws.stub.writes == [], "nothing may reach tmux"
    assert_message_is(outcome, getattr(sw, template), **slots)


def test_the_offset_zero_and_nonzero_fixtures_are_BOTH_load_bearing():
    """🔴 The two fixtures kill DIFFERENT mutants, and this says which.

    A future reader looking at `assert idx != 0` above and at the offset-0 test
    here will reasonably ask why both exist. Because:

      * a mutant that hardcodes `offset=0` passes any fixture whose character
        IS at 0, and is caught only by a non-zero fixture;
      * a mutant that starts the scan at 1 passes any fixture whose character
        is NOT at 0, and is caught only by a zero fixture.

    Asserted here rather than left as a comment, because a comment is a claim
    too and this one is the reason a later cleanup must not merge the two.
    """
    text_at_zero = EXECUTING_CHAR + "audit"
    text_at_nonzero = "audit" + EXECUTING_CHAR
    assert not sw.TEXT_IS_ALLOWED(EXECUTING_CHAR)

    # Fixture 1 lands at 0; fixture 2 does not. Both are refused today.
    assert min(i for i, c in enumerate(text_at_zero)
               if not sw.TEXT_IS_ALLOWED(c)) == 0
    assert min(i for i, c in enumerate(text_at_nonzero)
               if not sw.TEXT_IS_ALLOWED(c)) != 0
    assert sw.validate_text(text_at_zero) is not None
    assert sw.validate_text(text_at_nonzero) is not None

    # And the offsets they REPORT differ, which is what makes a hardcoded
    # constant visible to exactly one of them.
    assert (sw.validate_text(text_at_zero).record["message"] !=
            sw.validate_text(text_at_nonzero).record["message"])


# --------------------------------------------------------------------------- #
# 🔴 THE INVISIBLE / LOOK-ALIKE CLASS — refused, and now with a REMEDY
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("ch,name,category,why", [
    ("\xa0", "NO-BREAK SPACE", "Zs",
     "what a web page or a model's answer leaves in text the operator pastes"),
    ("\u202f", "NARROW NO-BREAK SPACE", "Zs", "the French-typography one"),
    ("\u2003", "EM SPACE", "Zs", "a rendered-width space from a copied doc"),
    ("\u00ad", "SOFT HYPHEN", "Cf", "invisible until the line wraps"),
    ("\u200b", "ZERO WIDTH SPACE", "Cf", "invisible always"),
    ("\u200f", "RIGHT-TO-LEFT MARK", "Cf", "a bidi control"),
    ("\u200d", "ZERO WIDTH JOINER", "Cf",
     "the character every ZWJ emoji sequence is built from"),
    ("\ufeff", "ZERO WIDTH NO-BREAK SPACE", "Cf", "the BOM"),
    ("\u2028", "LINE SEPARATOR", "Zl", "a Unicode line break"),
    ("\u2029", "PARAGRAPH SEPARATOR", "Zp", "a Unicode paragraph break"),
])
def test_an_invisible_or_lookalike_character_is_refused_WITH_A_REMEDY(
        ch, name, category, why):
    """🔴 THE DECISION, PINNED — and it is a decision, not an oversight.

    Measured against the shipped predicate: every character above is REFUSED,
    and the refusal used to be headlined "the non-printable character", which
    for a SPACE reads as nonsense and — unlike NUL, the submitting characters
    and the trailing separator — offered no next step.

    The allowlist is NOT widened. Measured in a real bash pane on a private
    `-L` server, `touch<U+00A0>/tmp/f` delivered by `send-keys -l` produced

        bash: touch\\xa0/tmp/f: No such file or directory

    — the payload collapsed into ONE word and failed later, wearing a
    diagnosis about a PATH. That is the same silent-corruption class as the
    trailing `;` this file already refuses, so refusing is right; normalising
    it to U+0020 behind the caller's back is the payload mutation this file
    refuses to do anywhere else. What was missing was the REMEDY, and the
    whole-string assertion below is what pins it.
    """
    assert unicodedata.category(ch) == category, ch
    assert unicodedata.name(ch) == name, ch
    text = "audit" + ch + "queue"
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", text)
    assert outcome.code == sw.EXIT_BAD_TEXT, why
    assert ws.stub.writes == [], "nothing may reach tmux"
    assert_message_is(outcome, sw.MSG_TEXT_INVISIBLE, name=name,
                      codepoint=f"{ord(ch):04X}", category=category,
                      offset=len("audit"), sep=sw.TMUX_ARGV_SEPARATOR)


def test_the_invisible_diagnosis_is_a_DIAGNOSIS_not_a_gate():
    """🔴 The same structural claim the three older messages carry, extended to
    the new one: deleting this branch must weaken the WORDS and not the guard.

    The boundary is measured at BOTH points, and it is the interesting one:
    U+0020 and U+00A0 are BOTH category Zs, so a branch that decided anything
    on the category would refuse the ordinary space and break every payload.
    It decides nothing — `str.isprintable()` special-cases U+0020, so the
    ordinary space never reaches the diagnosis at all.
    """
    assert unicodedata.category(" ") == "Zs"
    assert "Zs" in sw.TEXT_INVISIBLE_CATEGORIES
    assert sw.TEXT_IS_ALLOWED(" "), "the ASCII space must stay allowed"
    assert sw.validate_text("a b") is None

    for cat in sw.TEXT_INVISIBLE_CATEGORIES:
        assert cat in ("Cf", "Zl", "Zp", "Zs"), cat
    for ch in ("\xa0", "\u200b", "\u2028", "\u2029", "\u200d"):
        assert not sw.TEXT_IS_ALLOWED(ch), (
            f"{ch!r} must be refused by the ALLOWLIST, not by its message "
            f"branch — otherwise deleting the branch reopens the hole")


def test_a_ZWJ_emoji_sequence_is_refused_and_the_message_names_the_JOINER():
    """A measured consequence of the decision above, stated here rather than
    left to be discovered in production: the family and flag emoji — any
    sequence built with a ZERO WIDTH JOINER — are REFUSED, because the joiner
    between the glyphs is U+200D, category Cf.

    The bare non-BMP emoji and a VS16 variation selector are still ACCEPTED. A
    mutant that refused those too would be a different bug, so this pins both
    directions rather than only the refusal.
    """
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert sw.validate_text(family) is not None
    outcome, _ws = run_verb("type", ADDR_TARGET, "--text", family)
    assert outcome.code == sw.EXIT_BAD_TEXT
    assert_message_is(outcome, sw.MSG_TEXT_INVISIBLE,
                      name="ZERO WIDTH JOINER", codepoint="200D",
                      category="Cf", offset=1, sep=sw.TMUX_ARGV_SEPARATOR)
    # BOTH directions: the unjoined emoji, and VS16, are still ACCEPTED.
    for good in ("\U0001f389", "❤️", "\U0001f468 and \U0001f469"):
        assert sw.validate_text(good) is None, good


def test_the_refusal_names_the_COMPUTED_codepoint_not_the_one_in_its_own_prose():
    """A refusal that printed the character itself would print nothing visible
    for exactly the characters this guard exists for, so it prints the NUMBER.

    🔴 THIS TEST WAS WALKABLE BY THE MESSAGE'S OWN STATIC PROSE, and the
    mutation sweep is what found it. The first version asserted `"U+000F" in
    lines` after sending a Ctrl-O — but `MSG_TEXT_NOT_PRINTABLE` NAMES U+000F in
    its explanatory sentence, so the assertion was satisfied by the template
    regardless of what the computed slot rendered. A mutant hardcoding
    `codepoint="0000"` passed it. Exactly the failure RULES.md describes: "a
    two-word check satisfied by the sentence's own static prose while neither
    computed slot was ever read."

    Fixed two ways at once: probe with a character the prose does NOT mention,
    and assert the WHOLE normalised string rather than a substring.
    """
    probe = "\x16"                       # Ctrl-V — U+0016
    assert f"U+{ord(probe):04X}" not in sw.MSG_TEXT_NOT_PRINTABLE, (
        "the probe must be a codepoint the static prose does not already spell")
    assert "U+000F" in sw.MSG_TEXT_NOT_PRINTABLE, (
        "...and the prose really does spell U+000F, which is what made the "
        "original assertion vacuous")

    outcome, _ws = run_verb("type", ADDR_TARGET, "--text", "xy" + probe)
    assert_message_is(outcome, sw.MSG_TEXT_NOT_PRINTABLE, codepoint="0016",
                      category="Cc", offset=2)


def test_the_named_text_messages_are_DIAGNOSES_not_the_gate():
    """🔴 THE STRUCTURAL CLAIM, and the one that says this is not a denylist
    wearing an allowlist's name.

    NUL, `\\n`, `\\r` and the invisible/look-alike class still get their own
    sentences — a specific diagnosis beats a generic one. But they are refused
    BY THE ALLOWLIST; the named branches only choose better words. Proven by
    removing the branches from the question entirely and asking the predicate
    directly: if any of them were allowed, deleting its message branch would
    silently reopen it.
    """
    for ch in ("\x00", "\xa0", "​") + tuple(sw.SUBMITTING_CHARS):
        assert not sw.TEXT_IS_ALLOWED(ch), (
            f"{ch!r} must be refused by the ALLOWLIST, not only by its message "
            f"branch — otherwise deleting the branch reopens the hole")


def test_the_allowlist_is_the_predicate_every_character_actually_goes_through():
    """🔴 ONE RULE, ONE PLACE. `validate_text` must agree with `TEXT_IS_ALLOWED`
    on every codepoint, or there are two rules and one of them is wrong.

    Swept over the whole BMP plus the ASCII range explicitly. A disagreement
    means either the scan skips characters the predicate rejects (a hole) or
    refuses ones it permits (a false refusal).
    """
    probe = [chr(c) for c in range(0x00, 0x100)]
    probe += [chr(c) for c in (0x200b, 0x2028, 0x2029, 0xe000, 0xfeff,
                               0x1f600, 0x0301, 0x3042)]
    disagreed = []
    for ch in probe:
        if ch == "\x00":
            continue        # cannot ride in an argv at all; covered separately
        allowed = sw.TEXT_IS_ALLOWED(ch)
        # `X` around it so a trailing-separator refusal cannot be confused for
        # an allowlist one, and so the character is never at offset 0.
        refused = sw.validate_text("X" + ch + "X") is not None
        if allowed == refused:
            disagreed.append((hex(ord(ch)), allowed, refused))
    assert disagreed == [], f"scan and predicate disagree: {disagreed[:10]}"


def test_the_allowlist_admits_the_payloads_an_agent_actually_sends():
    """🔴 POSITIVE CONTROL, and the one that stops this guard from being
    "refuse everything" — which would pass every test above.

    An allowlist that rejected ordinary prose would be discovered in production,
    not here. These are the shapes a real caller sends.
    """
    for good in [
        "restart the poller and report back",
        "run: git -C /home/x log --oneline -3 | head",
        "answer 'yes' to the prompt; then stop",
        "path/with-dashes_and.dots:8080",
        "tab\tseparated\tfields",
        "unicode ✓ ± é 漢字 🎉",
        '{"json": ["payload", 1, null]}',
        "--flag=value --other='quoted string'",
    ]:
        assert sw.validate_text(good) is None, good
        outcome, ws = run_verb("type", ADDR_TARGET, "--text", good)
        assert outcome.code == sw.EXIT_OK, (good, outcome.lines)
        assert ws.stub.writes[0][-1] == good


def test_tab_is_the_ONE_control_character_re_permitted_and_it_is_explicit():
    """Tab is category Cc, so the printable test alone would refuse it. It is
    re-permitted by name because it has an ordinary typographic use and cannot
    accept a line — and a mutant that widens `TEXT_EXTRA_ALLOWED` to admit a
    second control character has to say so here."""
    assert sw.TEXT_EXTRA_ALLOWED == ("\t",)
    assert unicodedata.category("\t") == "Cc"
    assert not "\t".isprintable()
    assert sw.TEXT_IS_ALLOWED("\t")
    for other in ("\n", "\r", "\x0b", "\x0c", "\x0f", "\x1b"):
        assert not sw.TEXT_IS_ALLOWED(other), other


# =========================================================================== #
# G8 — the shell-execution gate
# =========================================================================== #
def test_the_agent_runtime_allowlist_is_exactly_the_two_measured_runtimes():
    """🔴 An ALLOWLIST, not a denylist — see the module docstring's refutation
    (1). `runtime` has THREE states and the third (None, 36 of 92 targets on the
    live host) means UNRECORDED, not "shell"."""
    assert sw.AGENT_RUNTIMES == ("claude", "opencode")


def test_send_to_an_unrecorded_runtime_is_refused():
    """The shell pane in the fixture has `runtime: None`. Enter there would
    EXECUTE rather than submit a prompt."""
    target = sr.resolve(ADDR_SHELL, make_sources())["target"]
    assert target["runtime"] is None, "fixture must exercise the third state"
    outcome, ws = run_verb("send", ADDR_SHELL, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_SHELL_EXEC
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_SHELL_EXEC, address=ADDR_SHELL,
                      runtime=repr(None),
                      agents=", ".join(sw.AGENT_RUNTIMES))


def test_send_to_an_unrecorded_runtime_is_allowed_with_the_flag():
    outcome, ws = run_verb("send", ADDR_SHELL, "--text", WRITE_TEXT,
                           "--allow-shell-exec")
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes[-1] == ["tmux", "send-keys", "-t", PANE_SHELL,
                                  "Enter"]


def test_bare_send_to_an_unrecorded_runtime_is_also_refused():
    """`send` with no --text still presses Enter, which still executes whatever
    is sitting in the pane. A gate applied only to the --text path would miss
    the more dangerous shape."""
    outcome, ws = run_verb("send", ADDR_SHELL)
    assert outcome.code == sw.EXIT_SHELL_EXEC
    assert ws.stub.writes == []


@pytest.mark.parametrize("runtime,addr,pane", [
    ("claude", ADDR_TARGET, PANE_TARGET),
    ("opencode", f"{SESSION_CLIENT}:{WIN_CLIENT_ID}", PANE_CLIENT),
])
def test_send_to_an_agent_runtime_is_not_gated(runtime, addr, pane):
    """POSITIVE CONTROL on G8, at BOTH allowlisted runtimes — a mutant that
    hardcodes only "claude" survives a single-runtime test."""
    target = sr.resolve(addr, make_sources())["target"]
    assert target["runtime"] == runtime
    outcome, ws = run_verb("send", addr, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes[-1] == ["tmux", "send-keys", "-t", pane, "Enter"]


def test_type_is_ungated_ONLY_because_the_allowlist_makes_it_safe():
    """🔴 REPLACES `test_type_is_NOT_shell_gated`, which pinned a FALSE premise
    and would have defended the hole this test now closes.

    That test's docstring read: "Enter is the execution event; `type` never
    sends one." Measured on tmux 3.7b in a private -L server, that is wrong — a
    payload ending in U+000F (Ctrl-O, readline operate-and-get-next, a bash
    default) EXECUTED the pane's buffer with no Enter sent, while the same
    payload without it did not (the negative control). So `type` on a shell pane
    was an arbitrary-command executor, and the old test asserted that as
    intended behaviour.

    🔴 THIS IS A SEAM GUARD, NOT A SCOPE CONTROL. It pins the RELATIONSHIP
    between the two halves rather than either alone, because either alone is
    green while the pair is broken:

        half A  — type on a shell runtime is NOT gated by G8
        half B  — and the G13 allowlist is what makes that safe

    A future edit that keeps A and weakens B reopens exactly the audited hole,
    and a test asserting only A would stay green through it.
    """
    # --- half A: no shell gate on `type`, on a runtime `send` WOULD refuse. ---
    target = sr.resolve(ADDR_SHELL, make_sources())["target"]
    assert target["runtime"] is None, "fixture must be a non-agent runtime"
    assert sw.gate_shell_exec(target, False) is not None, (
        "the fixture must be one `send` refuses, or half A is vacuous")

    outcome, ws = run_verb("type", ADDR_SHELL, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [
        ["tmux", "send-keys", "-t", PANE_SHELL, "-l", "--", WRITE_TEXT]]

    # --- half B: and the payload that made A dangerous is REFUSED. -----------
    outcome, ws = run_verb("type", ADDR_SHELL, "--text",
                           WRITE_TEXT + EXECUTING_CHAR)
    assert outcome.code == sw.EXIT_BAD_TEXT, outcome.lines
    assert ws.stub.writes == [], "the executing payload must not reach tmux"


def test_gate_shell_exec_reads_the_runtime_and_nothing_else():
    """Unit-level, with values that cannot coincide with any fixture."""
    assert sw.gate_shell_exec({"runtime": "claude"}, False) is None
    assert sw.gate_shell_exec({"runtime": "opencode"}, False) is None
    assert sw.gate_shell_exec({"runtime": None}, True) is None
    for bad in (None, "", "bash", "zsh", "Claude", "claude-code"):
        refusal = sw.gate_shell_exec({"runtime": bad, "address": "z:@0"}, False)
        assert refusal is not None and refusal.code == sw.EXIT_SHELL_EXEC, bad


# =========================================================================== #
# G9 — the unsent-prompt gate
# =========================================================================== #
def _unsent_ws(stub=None):
    return make_ws(stub=stub,
                   src_over={"sm_payload": sm_payload(target_unsent=UNSENT_TEXT)})


@pytest.mark.parametrize("verb", ["type", "send"])
def test_writing_onto_unsent_operator_text_is_refused(verb):
    """The agent's text would CONCATENATE onto a human's half-finished sentence
    and, on `send`, submit the pair."""
    outcome, ws = run_verb(verb, ADDR_TARGET, "--text", WRITE_TEXT,
                           ws=_unsent_ws())
    assert outcome.code == sw.EXIT_UNSENT_PROMPT
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_UNSENT_PROMPT, address=ADDR_TARGET,
                      length=UNSENT_LENGTH)


def test_the_unsent_prompt_CONTENT_never_reaches_the_output():
    """🔴 THIS REPO IS PUBLIC. `unsent_prompt` is text captured off the
    operator's screen; only its presence and length may leave the tool."""
    outcome, _ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                            ws=_unsent_ws())
    blob = json.dumps(outcome.record, default=str) + " ".join(outcome.lines)
    assert UNSENT_TEXT not in blob
    assert str(UNSENT_LENGTH) in " ".join(outcome.lines)


def test_append_allows_writing_onto_unsent_text():
    """POSITIVE CONTROL on G9."""
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                           "--append", ws=_unsent_ws())
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert len(ws.stub.writes) == 1


def test_bare_send_IS_unsent_gated_and_says_it_would_SUBMIT():
    """🔴 REPLACES `test_bare_send_is_not_unsent_gated`, which pinned the gap as
    intended behaviour and would have defended it against this fix.

    That test's reasoning was: bare `send` exists to submit text that is already
    in the pane, so gating it on that text existing refuses its only purpose.
    The error is in "text that is already there" — `unsent_prompt` is not
    neutral leftover text, it is specifically what a HUMAN typed and chose not
    to send. Bare send dispatches it verbatim, unread, with no agent text
    involved. That is G9's hazard by a shorter route, and the exemption made the
    shorter route the unguarded one.

    The refusal must also be the SUBMIT wording, not the concatenate wording:
    nothing is appended here, and telling the operator otherwise misdescribes
    what they are about to authorise.
    """
    outcome, ws = run_verb("send", ADDR_TARGET, ws=_unsent_ws())
    assert outcome.code == sw.EXIT_UNSENT_PROMPT
    assert ws.stub.writes == [], "no Enter may be issued"
    assert_message_is(outcome, sw.MSG_UNSENT_PROMPT_SUBMIT,
                      address=ADDR_TARGET, length=UNSENT_LENGTH)


def test_the_two_unsent_refusals_are_DIFFERENT_STRINGS():
    """🔴 A shared message would make a mutant that moves the gate between the
    --text and bare shapes invisible to every whole-string assertion — both
    would read the same sentence and both would pass. They describe different
    consequences (CONCATENATE vs SUBMIT) and must stay separately spelled."""
    assert sw.MSG_UNSENT_PROMPT != sw.MSG_UNSENT_PROMPT_SUBMIT
    a = _norm(sw.MSG_UNSENT_PROMPT.format(address="z:@0", length=1))
    b = _norm(sw.MSG_UNSENT_PROMPT_SUBMIT.format(address="z:@0", length=1))
    assert a != b


def test_bare_send_with_append_submits_the_operators_unsent_text():
    """POSITIVE CONTROL on the gate above — it must be passable, or bare send
    has simply been deleted rather than gated."""
    outcome, ws = run_verb("send", ADDR_TARGET, "--append", ws=_unsent_ws())
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "send-keys", "-t", PANE_TARGET, "Enter"]]


def test_bare_send_on_a_pane_with_NO_unsent_text_needs_no_flag():
    """The gate keys on the unsent text EXISTING, not on the verb shape. With an
    empty prompt bare send is unimpeded — a mutant that refuses every bare send
    would pass the gate test above and fail here."""
    outcome, ws = run_verb("send", ADDR_TARGET)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "send-keys", "-t", PANE_TARGET, "Enter"]]


def test_an_empty_unsent_prompt_is_not_a_refusal():
    """A measured-empty prompt is a real zero, not a reason to refuse."""
    for empty in (None, ""):
        assert sw.gate_unsent_prompt({"unsent_prompt": empty}, False) is None


# =========================================================================== #
# G10 — the audit log
# =========================================================================== #
def test_the_log_path_is_pinned():
    """🔴 An on-disk artifact NAME is invisible to every behavioural test — the
    repo has a whole file (`test_on_disk_artifact_names.py`) about the fifteen
    names that survived a rename sweep. Pin it here."""
    assert sw.DEFAULT_LOG_PATH == os.path.join(
        os.path.expanduser("~"), ".claude", "session-write.log")
    assert sw.LOG_PATH_ENV == "SESSION_WRITE_LOG"


def test_the_log_path_resolves_at_call_time_not_as_a_field_default(monkeypatch,
                                                                   tmp_path):
    """🔴 session-resolve has a scar here: a dataclass FIELD DEFAULT bound at
    class-creation time sailed past a monkeypatch and read 48 live records
    inside a suite that believed itself hermetic."""
    later = tmp_path / "chosen-later.log"
    monkeypatch.setenv(sw.LOG_PATH_ENV, str(later))
    assert sw.WriteSources().resolved_log_path() == str(later)
    explicit = tmp_path / "explicit.log"
    assert sw.WriteSources(log_path=str(explicit)).resolved_log_path() == str(
        explicit)


# =========================================================================== #
# G14 — the log path is CONFINED, because it is a filesystem write primitive
# =========================================================================== #
#: 🔴 What `--log-path` actually is, and why an audit called it deploy-blocking:
#: `log_record` calls `os.makedirs(dirname, exist_ok=True)` and then APPENDS a
#: JSON line whose contents the caller shapes (`selector`, `text`, the argv).
#: Reachable from the ONE allowlisted Bash entry, that is "create nested
#: directories anywhere and append attacker-chosen bytes" — granted on the
#: premise that this file constrains what it does. `$SESSION_WRITE_LOG` is the
#: same surface by another route.
def test_a_log_path_outside_the_root_is_REFUSED_before_anything_resolves(
        tmp_path):
    outside = tmp_path.parent / "escaped" / "nested" / "deep" / "evil.log"
    # 🔴 Through `make_ws`, not a bare `--log-path` on `run_verb`: `run` reads
    # the path off `ws`, and it is `main` that copies argv onto it. A test that
    # passed the flag to `run_verb` alone would exercise the ENV default and
    # pass while proving nothing — it did, before this comment existed.
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT,
                           ws=make_ws(log_path=str(outside)))
    assert outcome.code == sw.EXIT_BAD_LOG_PATH
    assert ws.stub.calls == [], "nothing may even be READ before this gate"
    assert ws.stub.writes == []
    assert not outside.parent.exists(), (
        "the refused path's directories must not have been created")
    assert_message_is(outcome, sw.MSG_BAD_LOG_PATH, path=str(outside),
                      resolved=os.path.realpath(str(outside)),
                      root=os.path.realpath(str(tmp_path)))


def test_the_env_var_is_the_SAME_surface_and_is_confined_too(monkeypatch,
                                                             tmp_path):
    """🔴 Two routes to one hazard. Constraining only the flag would move the
    bypass, not close it — and the env route is the quieter of the two."""
    outside = tmp_path.parent / "via-env" / "evil.log"
    monkeypatch.setenv(sw.LOG_PATH_ENV, str(outside))
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_BAD_LOG_PATH
    assert ws.stub.writes == []
    assert not outside.parent.exists()


def test_a_refused_log_path_is_still_JOURNALLED_but_never_to_that_path(
        tmp_path):
    """🔴 The refusal is the interesting record (G10), so it must still be
    written — and the one place it must NOT be written is the path just
    refused. `resolved_log_path` falls back inside the root for exactly this."""
    outside = tmp_path.parent / "escaped2" / "evil.log"
    ws = make_ws(log_path=str(outside))
    code = sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT], ws=ws)
    assert code == sw.EXIT_BAD_LOG_PATH
    assert not outside.exists() and not outside.parent.exists()
    landed = tmp_path / os.path.basename(sw.DEFAULT_LOG_PATH)
    assert landed.exists(), "the refusal must still be audited"
    rec = json.loads(landed.read_text().strip())
    assert rec["outcome"] == "refused:bad-log-path"
    assert rec["exit_code"] == sw.EXIT_BAD_LOG_PATH


@pytest.mark.parametrize("relative", [
    "../escape.log",
    "sub/../../escape.log",
    "./sub/./../../escape.log",
])
def test_dot_dot_cannot_climb_out_of_the_root(tmp_path, relative):
    """🔴 `realpath` BEFORE comparing, never a prefix test on the raw string.
    A `..` inside an otherwise-legal-looking path is the oldest way out."""
    candidate = str(tmp_path / relative)
    refusal = sw.validate_log_path(candidate, str(tmp_path))
    assert refusal is not None, candidate
    assert refusal.code == sw.EXIT_BAD_LOG_PATH


def test_a_symlink_planted_inside_the_root_cannot_point_out_of_it(tmp_path):
    """🔴 The reason the check resolves rather than normalises. A caller who can
    create one file inside the root could otherwise redirect every subsequent
    append anywhere on the filesystem."""
    outside_dir = tmp_path.parent / "symlink-target-dir"
    outside_dir.mkdir(exist_ok=True)
    link = tmp_path / "innocent"
    if not link.exists():
        link.symlink_to(outside_dir, target_is_directory=True)
    refusal = sw.validate_log_path(str(link / "evil.log"), str(tmp_path))
    assert refusal is not None
    assert refusal.code == sw.EXIT_BAD_LOG_PATH


def test_the_ROOT_is_realpathed_too_not_only_the_candidate_path(tmp_path):
    """🔴 THE OTHER HALF OF `realpath`, AND IT WAS ASSERTED IN PROSE ONLY.

    `validate_log_path`'s docstring states this exact case — "comparing a
    resolved path against an unresolved root fails whenever the root itself
    contains a symlink (`~` behind one is ordinary)" — and the committed sweep
    mutated only the PATH side (`realpath` -> `abspath`). MEASURED on this
    branch: `real_root = os.path.realpath(root)` -> `real_root = root`
    SURVIVED all 161 tests and the whole 52-mutant sweep.

    🔴 THE FAILURE DIRECTION IS OVER-REFUSAL, NOT A HOLE. On a host whose
    `$HOME` sits behind a symlink, every single invocation would refuse with
    EXIT_BAD_LOG_PATH before resolving anything — an availability failure of
    the entire tool, arriving as a confident security refusal. That is why it
    is worth a guard even though it cannot leak a write.

    The fixture is a symlinked ROOT with a real directory behind it, which is
    the shape `~` behind a link produces. Both points are measured: the path
    under the link must be ACCEPTED, and a path genuinely outside must still be
    REFUSED — a mutant that accepted everything would pass the first alone.
    """
    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "linked-root"
    link.symlink_to(real, target_is_directory=True)
    assert os.path.realpath(str(link)) == os.path.realpath(str(real)) != str(
        link), "the fixture must really be a symlink, or it proves nothing"

    # A path INSIDE the symlinked root: accepted, because both sides resolve.
    inside = link / "sub" / "audit.log"
    assert sw.validate_log_path(str(inside), str(link)) is None, (
        "a log path under a SYMLINKED root must be accepted — refusing it "
        "would take the whole tool down on a host with a symlinked $HOME")

    # ...and the confinement still holds through the link.
    outside = tmp_path / "not-the-root" / "audit.log"
    refusal = sw.validate_log_path(str(outside), str(link))
    assert refusal is not None and refusal.code == sw.EXIT_BAD_LOG_PATH


def test_a_sibling_directory_sharing_the_roots_PREFIX_is_outside_it(tmp_path):
    """🔴 `commonpath`, never `startswith`: `~/.claudeX` starts with
    `~/.claude` and is a different directory."""
    sibling = str(tmp_path) + "X"
    refusal = sw.validate_log_path(os.path.join(sibling, "a.log"),
                                   str(tmp_path))
    assert refusal is not None
    assert refusal.code == sw.EXIT_BAD_LOG_PATH


def test_the_root_ITSELF_is_not_a_legal_log_path(tmp_path):
    """Appending to the directory would fail anyway, but it must be a REFUSAL
    with a reason rather than an OSError swallowed by `log_record`."""
    assert sw.validate_log_path(str(tmp_path), str(tmp_path)) is not None


@pytest.mark.parametrize("inside", [
    "session-write.log",
    "nested/deeper/audit.log",
    "./also-fine.log",
])
def test_paths_INSIDE_the_root_are_accepted_and_actually_written(tmp_path,
                                                                 inside):
    """🔴 POSITIVE CONTROL on G14 — a check that refused every path would pass
    every test above while breaking the audit log entirely, which is the failure
    mode this repo names "a permanently-red gate"."""
    path = tmp_path / inside
    assert sw.validate_log_path(str(path), str(tmp_path)) is None
    ws = make_ws(log_path=str(path))
    code = sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT], ws=ws)
    assert code == sw.EXIT_OK
    assert path.exists() and json.loads(path.read_text().strip())["outcome"] \
        == "written:type"


def test_the_SHIPPED_default_log_path_is_inside_the_SHIPPED_root():
    """🔴 The shipped default must satisfy its own guard, or the tool refuses
    every invocation on a real machine — a permanently-red gate.

    Read from `DEFAULT_LOG_PATH`, which is computed at IMPORT time and so still
    carries the real values; `sw.LOG_ROOT` is monkeypatched to `tmp_path` by the
    hermeticity fixture, and asserting against it here would only prove the
    fixture is installed."""
    real_root = os.path.join(os.path.expanduser("~"), ".claude")
    assert sw.DEFAULT_LOG_PATH == os.path.join(real_root, "session-write.log")
    assert sw.validate_log_path(sw.DEFAULT_LOG_PATH, real_root) is None


def test_the_root_is_NOT_reachable_from_argv_or_the_environment():
    """🔴 A confinement a caller can relocate is not a confinement. `log_root`
    is an in-process seam only: no CLI flag sets it, and no environment variable
    is read for it."""
    help_text = sw.build_parser().format_help()
    assert "--log-root" not in help_text
    dests = {a.dest for a in sw.build_parser()._actions}
    assert "log_root" not in dests
    src = open(_SW_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    # The only environment key this module reads for logging is the path one.
    env_keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"):
            for a in node.args[:1]:
                if isinstance(a, ast.Name):
                    env_keys.add(a.id)
                elif isinstance(a, ast.Constant):
                    env_keys.add(a.value)
    assert env_keys <= {"LOG_PATH_ENV"}, env_keys


@pytest.mark.parametrize("argv,expected_code", [
    (["type", ADDR_TARGET, "--text", WRITE_TEXT], 0),
    (["send", ADDR_TARGET, "--text", WRITE_TEXT], 0),
    (["dismiss", ADDR_TARGET], None),
    (["focus", ADDR_TARGET], 5),
    (["type", ADDR_REMOTE, "--text", WRITE_TEXT], 4),
])
def test_every_run_writes_exactly_one_log_line(tmp_path, argv, expected_code):
    """Refusals are logged too — a refusal is the interesting record."""
    log = tmp_path / "writes.log"
    ws = make_ws()
    rc = sw.main(argv + ["--log-path", str(log)], ws=ws)
    if expected_code is not None:
        assert rc == expected_code, log.read_text()
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["verb"] == argv[0]
    assert record["selector"] == argv[1]
    assert record["exit_code"] == rc
    assert "outcome" in record and record["outcome"]
    assert isinstance(record["tmux_argv_issued"], list)


def test_the_log_records_the_argv_that_was_actually_issued(tmp_path):
    log = tmp_path / "writes.log"
    ws = make_ws()
    sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--log-path",
             str(log)], ws=ws)
    record = json.loads(log.read_text(encoding="utf-8").strip())
    assert ["tmux", "send-keys", "-t", PANE_TARGET, "-l", "--",
            WRITE_TEXT] in record["tmux_argv_issued"]
    assert record["target"]["pane_id"] == PANE_TARGET
    assert record["target"]["host"] == LOCAL_HOST


def test_the_log_never_contains_unsent_prompt_content(tmp_path):
    """🔴 PUBLIC REPO. Presence and length only."""
    log = tmp_path / "writes.log"
    ws = _unsent_ws()
    sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--log-path",
             str(log)], ws=ws)
    raw = log.read_text(encoding="utf-8")
    assert UNSENT_TEXT not in raw
    record = json.loads(raw.strip())
    assert record["unsent_prompt_present"] is True
    assert record["unsent_prompt_length"] == UNSENT_LENGTH


def test_a_log_that_cannot_be_written_does_not_change_the_exit_code(tmp_path,
                                                                    capsys):
    """A wrapper that refuses to work because it cannot journal is a worse
    failure than the missing line."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    ws = make_ws()
    rc = sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--log-path",
                  str(blocker / "sub" / "writes.log")], ws=ws)
    assert rc == sw.EXIT_OK
    assert "could not write log" in capsys.readouterr().err


# =========================================================================== #
# --dry-run
# =========================================================================== #
def test_dry_run_issues_no_write_but_reports_the_argv(capsys):
    ws = make_ws()
    rc = sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--dry-run"],
                 ws=ws)
    assert rc == sw.EXIT_OK
    # the READS really ran (the re-verify is not skipped) ...
    # compared as a SET: the order reverify happens to read them in is not the
    # contract, that all three ran before the write is.
    assert sorted(a[1] for a in ws.stub.calls) == sorted(
        sw.TMUX_REVERIFY_SUBCOMMANDS)
    # ... and the WRITE did not reach the runner at all.
    assert not any(a[1] in sw.TMUX_WRITE_SUBCOMMANDS for a in ws.stub.calls)
    out = capsys.readouterr().out
    assert "NOTHING was written" in out
    assert f"send-keys -t {PANE_TARGET} -l -- {WRITE_TEXT}" in out


def test_dry_run_still_runs_every_guard():
    """A dry run is not a way around the gates."""
    outcome, _ws = run_verb("type", ADDR_REMOTE, "--text", WRITE_TEXT,
                            "--dry-run")
    assert outcome.code == sw.EXIT_REMOTE_HOST


def test_a_dry_run_is_never_logged_as_a_write(tmp_path):
    """🔴 `outcome` is what a reader greps the audit log for. `written:type`
    sitting beside `dry_run: true` is two fields that must be read together —
    i.e. one field that can be read wrong."""
    log = tmp_path / "writes.log"
    sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--dry-run",
             "--log-path", str(log)], ws=make_ws())
    record = json.loads(log.read_text(encoding="utf-8").strip())
    assert record["dry_run"] is True
    assert record["outcome"] == "dry-run:type"
    assert not record["outcome"].startswith("written:")

    # POSITIVE CONTROL: the very same verb, not dry, DOES log as a write.
    log2 = tmp_path / "real.log"
    sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--log-path",
             str(log2)], ws=make_ws())
    assert json.loads(log2.read_text(encoding="utf-8").strip())["outcome"] == \
        "written:type"


# =========================================================================== #
# 🔴 THE SEAM WITH session-resolve
# =========================================================================== #
#: Keys session-write is ALLOWED to read off a resolved target but which are not
#: guaranteed present on every one. `pane_id` is None for a remote target — and
#: a remote target is refused by G2 long before any pane is used.
TARGET_KEYS_MAY_BE_NULL = ("pane_id",)


def _target_keys_read_by_session_write():
    """AST-scan session-write for the target keys it pulls out.

    Structural, not a hand-maintained list: a key added to the code is added to
    this set automatically, so the seam test below cannot go stale.
    """
    tree = ast.parse(open(_SW_PATH, encoding="utf-8").read())
    keys = set()
    for node in ast.walk(tree):
        # target.get("x") / (target or {}).get("x")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            base = node.func.value
            if isinstance(base, ast.Name) and base.id == "target":
                keys.add(node.args[0].value)
            if (isinstance(base, ast.BoolOp)
                    and any(isinstance(v, ast.Name) and v.id == "target"
                            for v in base.values)):
                keys.add(node.args[0].value)
        # target["x"]
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "target"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            keys.add(node.slice.value)
    return keys


def test_the_ast_scanner_can_actually_see_a_key():
    """POSITIVE CONTROL on the scanner. A scanner wired to nothing returns an
    empty set and the seam test below passes vacuously — the exact
    reassuring-zero this repo's rules single out."""
    keys = _target_keys_read_by_session_write()
    expected = {"host", "session", "window_id", "pane_id", "runtime",
                "unsent_prompt", "address"}
    assert expected <= keys, (
        f"scanner missed {sorted(expected - keys)}: found {sorted(keys)}")
    #: Pinned as a LEDGER, both directions. It fails when session-write starts
    #: reading a new target key (go check session-resolve emits it) AND when it
    #: stops reading one (the seam test below just got weaker).
    assert keys == expected, (
        f"the set of target keys session-write reads changed: "
        f"added {sorted(keys - expected)}, removed {sorted(expected - keys)}")


def test_every_target_key_session_write_reads_is_emitted_by_session_resolve():
    """🔴 THE SEAM GUARD. RULES.md: "verified in isolation is the new vacuous
    green — the defect lives in the SEAM nobody owns."

    This is how this branch discovered that session-resolve declares `claude` in
    `SM_PASSTHROUGH_FIELDS` and `build_targets` never emits it. Had session-write
    keyed its shell gate on `claude` (as the brief implied), every hermetic test
    built on a hand-written target dict would have passed and the gate would
    have read `None` for EVERY window in production.
    """
    # 🔴 A FULLY-POPULATED target: the default fixture leaves `unsent_prompt`
    # null, and a null cannot prove a key carries anything. This assertion
    # caught that on its first run.
    target = sr.resolve(ADDR_TARGET, make_sources(
        sm_payload=sm_payload(target_unsent=UNSENT_TEXT)))["target"]
    missing = sorted(k for k in _target_keys_read_by_session_write()
                     if k not in target)
    assert not missing, (
        f"session-write reads target key(s) session-resolve does not emit: "
        f"{missing}. Add them to build_targets, or stop reading them.")
    for key in _target_keys_read_by_session_write():
        if key not in TARGET_KEYS_MAY_BE_NULL:
            assert target[key] is not None, (
                f"target[{key!r}] is None on a fully-populated fixture — the "
                f"fixture cannot prove this key carries anything")


def test_the_record_only_names_target_keys_that_exist():
    """The audit record projects a fixed key list; a typo there would log a
    silent null forever."""
    target = sr.resolve(ADDR_TARGET, make_sources())["target"]
    ws = make_ws()
    outcome, ws = run_verb("type", ADDR_TARGET, "--text", WRITE_TEXT, ws=ws)
    record = sw.build_record(ws, parse_args("type", ADDR_TARGET, "--text",
                                            WRITE_TEXT),
                             target, None, outcome)
    for key, value in record["target"].items():
        assert key in target, key
        assert value == target[key]


# =========================================================================== #
# The CLI surface
# =========================================================================== #
def test_the_permission_surface_is_one_executable():
    """The whole design rests on ONE allowlist entry, `Bash(scripts/session-write:*)`.
    A second entry point (a shell wrapper, an alternate name) would need its own,
    and the classifier bypass would be back."""
    assert os.path.isfile(_SW_PATH)
    assert os.access(_SW_PATH, os.X_OK), "session-write must be executable"

    # 🔴 The interpreter line is pinned by AGREEMENT WITH ITS SIBLING, not by a
    # literal. Two reasons, and the second is the load-bearing one:
    #   * the two halves of this subsystem must not disagree about how they are
    #     launched — a stronger claim than either file's line in isolation;
    #   * `test_runtime_shebangs.py` is a repo-wide TEXT scan over every
    #     `test_*.py`, and shape 1 of `testlib.shebang_scan.line_is_offender` is
    #     "a quote immediately followed by the two shebang characters". It
    #     cannot tell an assertion ABOUT a shebang from a test WRITING one, so
    #     spelling either the full interpreter path OR a bare quoted marker here
    #     puts that guard RED — both measured, not predicted.
    #
    # The marker is therefore assembled from CHARACTER CODES, exactly as
    # `shebang_scan` assembles its own needles and for exactly the same
    # self-match reason ("no literal quoted marker anywhere in this file").
    # This is not a reword to dodge the scanner: the three assertions below are
    # strictly stronger than the single literal they replaced.
    hashbang = chr(35) + chr(33)
    first = open(_SW_PATH, encoding="utf-8").readline().rstrip("\n")
    sibling = open(_SR_PATH, encoding="utf-8").readline().rstrip("\n")
    assert first == sibling, (
        f"session-write and session-resolve disagree about their interpreter: "
        f"{first!r} vs {sibling!r}")
    assert first.startswith(hashbang)
    assert first.endswith("python3")


def test_the_parser_rejects_an_unknown_verb():
    with pytest.raises(SystemExit):
        parse_args("kill", ADDR_TARGET)


def test_json_output_is_the_same_record_that_is_logged(tmp_path, capsys):
    log = tmp_path / "writes.log"
    ws = make_ws()
    sw.main(["type", ADDR_TARGET, "--text", WRITE_TEXT, "--json",
             "--log-path", str(log)], ws=ws)
    printed = json.loads(capsys.readouterr().out)
    logged = json.loads(log.read_text(encoding="utf-8").strip())
    assert printed == logged


# =========================================================================== #
# 🔴 THE MUTATION MATRIX
# =========================================================================== #
#: 🔴 THE SWEEP LIVES IN THE REPO NOW — DO NOT RESTATE ITS RESULTS HERE.
#:
#: This constant used to hold a hand-maintained matrix of 36 mutations. Two
#: things were wrong with that, and an audit found both:
#:
#:   * the harness that produced it was never committed, so the claim was not
#:     reproducible. An auditor's INDEPENDENT 21-mutant sweep then found seven
#:     survivors of a fully green 117-test suite — including the deploy-blocking
#:     `type` shell-exec bypass. A sweep nobody else can run is not evidence;
#:   * the matrix skipped number 11 with no explanation while the prose claimed
#:     36, so the list and the count disagreed and neither could be checked.
#:
#: Both are closed by `scripts/session-write-harness/mutation_sweep.py`: the
#: table IS the matrix, the numbering is derived from position so it cannot
#: develop a gap, and the run prints its own results. Read the numbers THERE,
#: never from a copy here — a copy is exactly what went stale.
#:
#:     python3 scripts/session-write-harness/mutation_sweep.py
#:
#: It refuses to score anything unless the unmutated baseline is green, kills a
#: mutant only when the NAMED test fails, and carries TWO controls: a mutation
#: known to be caught (must be KILLED) and a semantically null edit (must
#: SURVIVE). The second is the one that distinguishes a working harness from
#: one that reports everything as killed.
#:
#: 🔴 IT MUTATES A DISPOSABLE COPY OF `scripts/`, never the live allowlisted
#: script — for the duration of a run the in-place version left
#: `scripts/session-write` carrying deliberately disabled guards at exactly the
#: path `Bash(scripts/session-write:*)` names. It re-asserts the live file's
#: SHA-256 at the end, and
#: `test_the_mutation_sweep_never_WRITES_the_LIVE_allowlisted_script` pins the
#: property structurally.
#:
#: The real-pane check is its companion, and covers what no stub can:
#:
#:     python3 scripts/session-write-harness/real_pane_check.py
#:
MUTATION_HARNESS = "scripts/session-write-harness/mutation_sweep.py"
REAL_PANE_HARNESS = "scripts/session-write-harness/real_pane_check.py"


def test_the_committed_harnesses_exist_and_are_runnable():
    """🔴 A pointer that rots is worse than no pointer — it reads as a claim
    that the sweep is reproducible while naming a file that is gone.

    Both paths are checked to EXIST and to PARSE. Deliberately not "and to
    run": each spawns a real tmux server or a full pytest sweep, which is not
    something a unit suite may do. Saying so, rather than letting the docstring
    imply a stronger check than the assertions make — a comment is a claim too.
    """
    root = os.path.normpath(os.path.join(_HERE, "..", ".."))
    for rel in (MUTATION_HARNESS, REAL_PANE_HARNESS):
        path = os.path.join(root, rel)
        assert os.path.exists(path), f"{rel} is referenced but missing"
        ast.parse(open(path, encoding="utf-8").read())


def test_the_mutation_sweep_never_WRITES_the_LIVE_allowlisted_script():
    """🔴 THE SWEEP MUTATES A COPY, AND THIS IS WHAT SAYS SO.

    It patches the file it tests — deliberately disabling `TEXT_IS_ALLOWED`,
    `validate_log_path` and the screen gate in turn, for one full suite run
    each. The first version did that IN PLACE, in the checkout it was run
    from, and its usage line told you to run it from the repo root: for
    minutes at a time the exact path `Bash(scripts/session-write:*)` names
    carried disabled guards, and a crash between apply and the `finally`
    restore left it that way silently.

    The fix is structural, so this pin is structural too: every `open(..., "w")`
    in the sweep must name `_SRC` — the path inside the disposable copy — and
    `_LIVE_SRC` must never be a write target. A comment saying so is a claim; an
    AST scan is a measurement.
    """
    root = os.path.normpath(os.path.join(_HERE, "..", ".."))
    tree = ast.parse(open(os.path.join(root, MUTATION_HARNESS),
                          encoding="utf-8").read())
    write_targets = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open" and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and "w" in str(node.args[1].value)):
            arg = node.args[0]
            write_targets.append(
                arg.id if isinstance(arg, ast.Name) else ast.unparse(arg))
    # POSITIVE CONTROL: a scan that found no writes would pass vacuously, and
    # the sweep certainly writes.
    assert write_targets, "the scan found no write at all in the sweep"
    assert set(write_targets) == {"_SRC"}, (
        f"the sweep writes to {sorted(set(write_targets))}; only the copy "
        f"(_SRC) may ever be written")

    src = open(os.path.join(root, MUTATION_HARNESS), encoding="utf-8").read()
    assert "_LIVE_SRC" in src, "the sweep must still know the live path exists"
    assert "def _prepare_tree(" in src, (
        "the copy step is what makes the assertion above meaningful")


def test_the_mutation_table_names_only_tests_that_EXIST_in_this_file():
    """🔴 A mutant whose killer name is a typo can never be KILLED, and the
    sweep would report it as a survivor forever — or, worse, the name could
    match nothing and a future rename would silently turn a real kill into an
    unexplained red. Cross-check the table against this file's own test names.

    This is the ledger direction that matters: the sweep proves the tests catch
    the mutants; this proves the sweep is pointing at tests that are really here.
    """
    root = os.path.normpath(os.path.join(_HERE, "..", ".."))
    tree = ast.parse(open(os.path.join(root, MUTATION_HARNESS),
                          encoding="utf-8").read())
    named = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "M"):
            # M(guard, cls, desc, find, repl, killer)
            if len(node.args) >= 6 and isinstance(node.args[5], ast.Constant):
                if isinstance(node.args[5].value, str):
                    named.add(node.args[5].value)
    assert len(named) > 30, f"the scan found only {len(named)} killer names"

    here = {n.name for n in ast.walk(ast.parse(open(__file__,
                                                    encoding="utf-8").read()))
            if isinstance(n, ast.FunctionDef)}
    missing = sorted(k for k in named if k not in here)
    assert not missing, (
        f"the mutation table names tests that do not exist here: {missing}")
