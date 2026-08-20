#!/usr/bin/env python3
"""Unit tests for scripts/session-write — the four tmux WRITE verbs.

🔴 THESE ARE INVARIANT GUARDS, NOT REGRESSION TESTS
----------------------------------------------------
`scripts/session-write` DID NOT EXIST at this branch's base. There is no
"red at origin/main, green at HEAD" matrix to report and none is claimed: every
test here pins an invariant of a brand-new file, and the honest statement is
"these guards were mutation-tested against deliberate breakage of the code they
guard", which is what MUTATION_MATRIX at the bottom records. Calling them
regression coverage would be a false claim about what has been observed to fail.


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
    # 1 is left free for an uncaught interpreter error, as everywhere else.
    assert 1 not in sw.ALL_EXIT_CODES


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
    ("focus", ["--i-am-at-the-keyboard"]),
    ("dismiss", []),
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
    outcome, ws = run_verb("dismiss", ADDR_TARGET, ws=make_ws(stub=stub))
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


@pytest.mark.parametrize("verb,extra", [
    ("type", ["--text", WRITE_TEXT]),
    ("send", ["--text", WRITE_TEXT]),
    ("dismiss", []),
])
def test_no_other_verb_ever_changes_the_screen(verb, extra):
    """🔴 focus must never fire as a SIDE EFFECT. No other verb may emit
    select-window or switch-client, whatever else it does."""
    _outcome, ws = run_verb(verb, ADDR_TARGET, *extra)
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
    clients, _ = sr.parse_clients(clients_raw(popups=2, base=0))
    assert_message_is(outcome, sw.MSG_NO_BASE_TERMINAL,
                      attached=sw._client_summary(clients))


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
    outcome, ws = run_verb("dismiss", ADDR_TARGET, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK
    assert ws.stub.writes == []
    assert_message_is(outcome, sw.MSG_NO_POPUPS, address=ADDR_TARGET)


def test_dismiss_with_exactly_one_popup_detaches_it():
    stub = TmuxStub(clients=clients_raw(popups=1))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [["tmux", "detach-client", "-t", POPUP_ONE_TTY]]


def test_dismiss_with_two_popups_refuses_rather_than_guessing():
    """MEASURED LIVE: there really are two popups attached on this host, so this
    is the default state, not an edge case. Picking one is a coin flip on which
    of the operator's overlays disappears."""
    stub = TmuxStub(clients=clients_raw(popups=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == []
    clients, _ = sr.parse_clients(clients_raw(popups=2))
    popups = [c for c in clients if c["popup"]]
    assert len(popups) == 2
    assert_message_is(outcome, sw.MSG_AMBIGUOUS_POPUP, count=2,
                      candidates=sw._client_summary(popups))


def test_dismiss_with_two_popups_and_an_explicit_client_detaches_that_one():
    stub = TmuxStub(clients=clients_raw(popups=2))
    outcome, ws = run_verb("dismiss", ADDR_TARGET, "--client", POPUP_TWO_TTY,
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
    outcome, ws = run_verb("dismiss", ADDR_TARGET, "--client", tty,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == [], "no detach may be issued for a base terminal"
    session = (SESSION_TARGET if tty == BASE_UNFOCUSED_TTY else SESSION_CLIENT)
    assert_message_is(outcome, sw.MSG_BASE_TERMINAL, tty=tty, session=session)


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
    outcome, ws = run_verb("dismiss", ADDR_TARGET, "--client", UNATTACHED_TTY,
                           ws=make_ws(stub=stub))
    assert outcome.code == sw.EXIT_AMBIGUOUS_CLIENT
    assert ws.stub.writes == []
    clients, _ = sr.parse_clients(clients_raw(popups=2))
    assert_message_is(outcome, sw.MSG_NO_SUCH_CLIENT, tty=UNATTACHED_TTY,
                      attached=sw._client_summary(clients))


def test_dismiss_uses_the_FRESH_client_list_not_the_resolution_time_one():
    """🔴 "Re-verify at the moment you ACT" applies to the popup about to be
    detached, not only to the window about to be written. Resolution saw two
    popups; by write time only one remains, and dismiss must proceed (not
    refuse as ambiguous) and detach the survivor."""
    src_over = {"clients_raw": clients_raw(popups=2)}
    stub = TmuxStub(clients=clients_raw(popups=1))
    outcome, ws = run_verb("dismiss", ADDR_TARGET,
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
    outcome, _ws = run_verb("dismiss", ADDR_TARGET, ws=make_ws(stub=stub))
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


def test_type_is_NOT_shell_gated():
    """🔴 THE GATE IS ON `send` ONLY, and this is the control that pins the
    scope. Enter is the execution event; `type` never sends one (which is what
    the `\\n` refusal above makes true). A mutant that moved the gate into the
    shared pipeline would be invisible without this."""
    outcome, ws = run_verb("type", ADDR_SHELL, "--text", WRITE_TEXT)
    assert outcome.code == sw.EXIT_OK, outcome.lines
    assert ws.stub.writes == [
        ["tmux", "send-keys", "-t", PANE_SHELL, "-l", "--", WRITE_TEXT]]


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


def test_bare_send_is_not_unsent_gated():
    """`send` with no --text is how you submit text that is ALREADY there —
    gating it on that text existing would refuse its only purpose."""
    outcome, ws = run_verb("send", ADDR_TARGET, ws=_unsent_ws())
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
    first = open(_SW_PATH, encoding="utf-8").readline().rstrip("\n")
    assert first == "#!/usr/bin/env python3"


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
MUTATION_MATRIX = """
Each row: a deliberate break of ONE guard in scripts/session-write, and the test
that killed it. MEASURED 2026-08-19 — 36 mutants, 36 KILLED, and every one of
the 36 was killed BY THE TEST NAMED HERE (checked mechanically: the harness
matched the expected test id against the FAILED set, so "something went red" was
never accepted as a kill).

🔴 THESE ARE INVARIANT GUARDS, NOT REGRESSION TESTS. session-write did not exist
at origin/main, so "red at base, green at HEAD" is meaningless for it and is not
claimed. What WAS observed is that each guard, broken on purpose, produces a red
that names that guard.

🔴 HOW THE SWEEP WAS RUN, because a sweep is an instrument and an instrument is
a claim:
  * `__pycache__` purged before EVERY mutant, and `PYTHONDONTWRITEBYTECODE=1`
    on every spawned process. CPython validates cached bytecode on whole-second
    mtime + size, so a same-length edit landing in the same second is invisible:
    the test imports the ORIGINAL bytecode and the mutant scores SURVIVED
    without ever executing.
  * The original bytes are restored in a `finally` and SHA-256 byte-identity is
    asserted after every mutant, and again at the end.
  * Each mutant asserts its pattern occurs EXACTLY ONCE before applying, so a
    `count=1` replace cannot hit an occurrence nobody pictured.
  * The FULL test file runs for each mutant — no `-k` filter, which is how a
    sweep reports SURVIVED because it excluded the killing test.
  * PC rides along as the POSITIVE CONTROL: a mutation known to be caught. If
    the harness ever reports it SURVIVED, the harness is broken, not the code.

  #   mutation                                              killed by
  --  ----------------------------------------------------  --------------------
  PC  POSITIVE CONTROL: focus refusal reworded              test_the_focus_refusal_is_exactly_the_agreed_wording
  1   G2: `if target_host != local_host` -> `if False`      test_a_remote_target_is_refused_by_name
  2   G2: local_host hardcoded to "workbench"               test_a_local_target_is_not_refused
  3   G2: LOCAL_HOST_UNKNOWN branch removed                 test_an_unknown_local_host_is_refused_rather_than_assumed
  4   G3: window-existence check removed                    test_a_window_that_vanished_between_resolve_and_write_is_refused
  5   G3: need_pane forced True for every verb              test_a_client_verb_does_not_require_the_pane_to_survive
  6   G3: reverify moved AFTER the verb runs                test_the_reverify_runs_before_any_write
  7   G4: "kill-pane" added to the write allowlist          test_the_seam_refuses_a_destructive_verb[kill-pane]
  8   G4: seam `break` -> `return` (first command only)     test_the_seam_checks_every_command_not_just_the_first
  9   G4: run_tmux skips _assert_allowed                    test_run_tmux_asserts_the_allowlist_even_on_a_dry_run
  10  G5: `not args.at_keyboard` -> `False`                 test_focus_refuses_without_the_flag_and_touches_nothing
  12  G6: _assert_not_base_terminal always returns None     test_dismiss_refuses_to_detach_a_base_terminal[/dev/pts/41]
  13  G6 REACHABILITY: pick_named_client searches popups    test_dismiss_refuses_to_detach_a_base_terminal[/dev/pts/41]
        only, making the guard unreachable. 🔴 The exit CODE is unchanged
        (both refusals are EXIT_AMBIGUOUS_CLIENT) — only the WHOLE-STRING
        message assertion can see it. That is the argument for pinning whole
        strings rather than codes or keywords.
  14  G7: `elif len(popups) > 1` -> `elif False`            test_dismiss_with_two_popups_refuses_rather_than_guessing
  15  G8: AGENT_RUNTIMES -> ("claude",)                     test_send_to_an_agent_runtime_is_not_gated[opencode-...]
  16  G8: gate keyed on `runtime is not None`               test_gate_shell_exec_reads_the_runtime_and_nothing_else
        🔴 The end-to-end test SURVIVES this one (the fixture's shell pane has
        runtime None, which both spellings refuse). It is killed only by the
        unit test that feeds "bash" — a value the constant CANNOT equal. A
        fixture that can only produce the constant's own value cannot see this
        mutant.
  17  G8: shell gate also applied to `type`                 test_type_is_NOT_shell_gated
  18  G8: shell gate skipped when --text is absent          test_bare_send_to_an_unrecorded_runtime_is_also_refused
  19  G9: unsent-prompt gate disabled                       test_writing_onto_unsent_operator_text_is_refused[type]
  20  G9: refusal reports the CONTENT, not the length       test_the_unsent_prompt_CONTENT_never_reaches_the_output
  21  focus: pick_base_terminal returns base[0]             test_focus_picks_the_focused_base_terminal_not_the_first_one
  22  focus: restore never runs                             test_focus_records_and_restores_both_tmux_dimensions
  23  focus: restore covers the window but not the session  test_focus_records_and_restores_both_tmux_dimensions
  24  focus: a failed restore returns EXIT_OK               test_a_failed_restore_is_reported_and_changes_the_exit_code
  25  focus: early return on a half-completed raise         test_a_half_completed_raise_still_restores_what_it_moved
  26  text: SUBMITTING_CHARS -> ()                          test_text_containing_a_submitting_character_is_refused[\\n-a newline-type]
  27  text: `endswith(sep)` -> `sep in text`                test_a_separator_in_the_MIDDLE_of_the_text_is_allowed
  28  text: the reported offset hardcoded to 0              test_text_containing_a_submitting_character_is_refused[\\n-a newline-type]
        (the fixture puts the character at offset 15, never 0 — a fixture that
        placed it first could not see this)
  29  type: the `-l` literal flag dropped                   test_type_sends_literal_text_and_no_enter
  30  type: an Enter appended after the literal text        test_type_sends_literal_text_and_no_enter
  31  G1: the raw selector passed straight to tmux -t       test_a_raw_address_is_re_resolved_not_passed_through_to_tmux
  32  G1: ambiguity treated as a normal resolution          test_an_ambiguous_selector_is_refused_with_session_resolves_exit_code
  33  log: log_record made a no-op                          test_every_run_writes_exactly_one_log_line[argv0-0]
  34  log: the on-disk artifact name renamed                test_the_log_path_is_pinned
  35  seam: session-write starts reading target["claude"]   test_every_target_key_session_write_reads_is_emitted_by_session_resolve
  36  EXIT_REMOTE_HOST renumbered 4 -> 40                   test_exit_codes_are_pairwise_distinct_and_reuse_session_resolves
        🔴 Every OTHER test compares `outcome.code == sw.EXIT_REMOTE_HOST` —
        both sides move together, so none of them can see a renumber. Only the
        test that pins the LITERAL can.
"""
