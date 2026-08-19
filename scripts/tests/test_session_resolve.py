#!/usr/bin/env python3
"""Unit tests for scripts/session-resolve — the session identity resolver.

🔴 HERMETIC BY CONSTRUCTION, AND PROVEN SO
------------------------------------------
This suite runs on a live workbench holding the operator's real tmux sessions,
a real `~/.claude/sessions` registry and a real git checkout. NOTHING here may
reach any of it. Two autouse fixtures enforce that rather than asserting it:

  1. `_no_real_subprocess` replaces `sr._default_runner` — the ONLY subprocess
     seam in the module — with a function that RAISES. A test that forgets to
     inject a runner fails loudly instead of shelling out to tmux, git or gh.
  2. `_no_real_registry` repoints `sr.DEFAULT_REGISTRY_DIR` and
     `sr.DEFAULT_SLOT_TABLE` at paths under tmp_path that do not exist, so a
     `Sources()` built with no arguments cannot read the operator's real files.

`test_hermeticity_fixtures_are_actually_installed` is the POSITIVE CONTROL on
both — a guard nobody has watched work is not a guard.

🔴 FIXTURE VALUES ARE PAIRWISE DISTINCT, AND DISTINCT FROM EVERY ASSERTED
CONSTANT. This repo has been bitten repeatedly by a fixture whose value happens
to equal the constant under test, so a mutant that hardcodes the literal SURVIVES
a fully green suite. Concretely here:

  * window ids (@501/@502/@503/@504) and window indexes ("502"/"501"/"9"/"4")
    are drawn from DIFFERENT number spaces and are deliberately SWAPPED between
    the first two windows — see `test_the_join_is_on_window_id_not_window_index`,
    which is the only test in this file that can tell the two joins apart.
  * every timestamp differs from every other, and their expected ISO renderings
    are LITERALS computed independently, never re-derived from `parse_epoch_ms`.
  * pids, session ids, harness names, codenames, hotkeys and cwds are all unique
    across fixtures, so an assertion can only pass by reading the right field.

THE MUTATION MATRIX this suite was checked against is recorded in
`MUTATION_MATRIX` at the bottom of this file — each mutant, and the test that
kills it WITH ITS OWN ASSERTION.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPT = os.path.normpath(os.path.join(_HERE, "..", "session-resolve"))

# session-resolve has no .py extension -> load it by explicit path.
_spec = importlib.util.spec_from_loader(
    "session_resolve",
    importlib.machinery.SourceFileLoader("session_resolve", _SCRIPT))
sr = importlib.util.module_from_spec(_spec)
# 🔴 Register BEFORE exec_module. `@dataclass` resolves its own class's module
# through `sys.modules[cls.__module__]`, so a module loaded by explicit path and
# never registered raises AttributeError on the first dataclass — a failure that
# looks like a bug in the module under test and is not one.
sys.modules["session_resolve"] = sr
_spec.loader.exec_module(sr)


# =========================================================================== #
# Hermeticity harness
# =========================================================================== #
class _Forbidden(RuntimeError):
    """Raised when a test reaches for the real world."""


@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    def _boom(argv, timeout=None):
        raise _Forbidden(f"test tried to run a real subprocess: {argv!r}")
    monkeypatch.setattr(sr, "_default_runner", _boom)


@pytest.fixture(autouse=True)
def _no_real_registry(monkeypatch, tmp_path):
    """A bare `Sources()` must not see the operator's real files."""
    monkeypatch.setattr(sr, "DEFAULT_REGISTRY_DIR",
                        str(tmp_path / "no-such-registry"))
    monkeypatch.setattr(sr, "DEFAULT_SLOT_TABLE",
                        str(tmp_path / "no-such-slots.sh"))


def test_hermeticity_fixtures_are_actually_installed(tmp_path):
    """POSITIVE CONTROL: the raisers are in place AND they really raise.

    An autouse fixture that silently failed to apply would leave every other
    test in this file free to shell out, and the suite would still be green.
    """
    with pytest.raises(_Forbidden):
        sr._default_runner(["tmux", "list-panes"], timeout=1)
    assert not os.path.exists(sr.DEFAULT_REGISTRY_DIR)
    assert not os.path.exists(sr.DEFAULT_SLOT_TABLE)


# =========================================================================== #
# Fixtures — all synthetic. No captured text, no real repo paths, no real hosts.
# =========================================================================== #
HOST = "benchhost"

# --- the slot table ------------------------------------------------------- #
# Codenames/hotkeys deliberately UNLIKE the real table's (Gold/G, grove/g) so a
# test cannot pass by accidentally agreeing with the production file.
SLOT_TEXT = """
# a comment mentioning alpha1 and $mod+Shift+Q as PROSE, not data —
# a line-oriented grep for "alpha1" would wrongly pick this up.
SCRATCH_SLOTS=(
    "alpha1:Q:#111111:Quartz"
    "beta2:q:#222222:quill"
    "gamma3:Z:#333333:Zephyr"
)
"""
CODENAME_A = "Quartz"     # alpha1, hotkey Q (upper)
CODENAME_B = "quill"      # beta2,  hotkey q (lower)
CODENAME_C = "Zephyr"     # gamma3, hotkey Z
# `delta4` has NO slot entry on purpose — the no-hotkey fallback case.
SESSION_NO_SLOT = "delta4"

# --- tmux ------------------------------------------------------------------ #
# 🔴 THE JOIN DISCRIMINATOR. alpha1's two windows have their ids and indexes
# CROSSED: window @501 sits at index "502", and window @502 sits at index "501".
# A join on window_index therefore still MATCHES — it just matches the WRONG
# window — which is the only fixture shape that can distinguish a correct join
# from an index join. A fixture with matching ids and indexes would let the
# index join pass every assertion in this file.
WIN_A_ID, WIN_A_INDEX = "@501", "502"
WIN_B_ID, WIN_B_INDEX = "@502", "501"
WIN_C_ID, WIN_C_INDEX = "@503", "9"
WIN_D_ID, WIN_D_INDEX = "@504", "4"

CWD_A = "/w/repo-alpha"
CWD_B = "/w/repo-beta"
CWD_C = "/w/repo-gamma"
CWD_D = "/w/repo-delta"

PANE_A, PANE_B, PANE_C, PANE_D = "%601", "%602", "%603", "%604"

TITLE_A = "pane title alpha"
TITLE_B = "pane title beta"
TITLE_C = "pane title gamma"
TITLE_D = "pane title delta"

SEP = sr.FIELD_SEP


def _pane_line(session, index, wid, pid, cwd, title):
    return SEP.join([HOST, session, index, wid, pid, cwd, title])


PANES_RAW = "\n".join([
    _pane_line("alpha1", WIN_A_INDEX, WIN_A_ID, PANE_A, CWD_A, TITLE_A),
    _pane_line("alpha1", WIN_B_INDEX, WIN_B_ID, PANE_B, CWD_B, TITLE_B),
    _pane_line("gamma3", WIN_C_INDEX, WIN_C_ID, PANE_C, CWD_C, TITLE_C),
    _pane_line(SESSION_NO_SLOT, WIN_D_INDEX, WIN_D_ID, PANE_D, CWD_D, TITLE_D),
])

# Window @501 is the ACTIVE window of alpha1; the rest are not.
WINDOWS_RAW = "\n".join([
    SEP.join(["alpha1", WIN_A_ID, "1"]),
    SEP.join(["alpha1", WIN_B_ID, "0"]),
    SEP.join(["gamma3", WIN_C_ID, "1"]),
    SEP.join([SESSION_NO_SLOT, WIN_D_ID, "1"]),
])

BASE_TTY, BASE_TERM, BASE_W, BASE_H = "/dev/pts/7", "alacritty", "300", "60"
POPUP_TTY, POPUP_TERM, POPUP_W, POPUP_H = "/dev/pts/8", "tmux-256color", "240", "44"
POPUP2_TTY, POPUP2_W, POPUP2_H = "/dev/pts/9", "190", "30"

# Base terminal shows alpha1 (so @501, its active window, is VISIBLE) and two
# popups are attached elsewhere -> alpha1's active window is COVERED.
CLIENTS_RAW = "\n".join([
    SEP.join([BASE_TTY, BASE_TERM, BASE_W, BASE_H, "alpha1", "attached,focused"]),
    SEP.join([POPUP_TTY, POPUP_TERM, POPUP_W, POPUP_H, "gamma3", "attached"]),
    SEP.join([POPUP2_TTY, POPUP_TERM, POPUP2_W, POPUP2_H, SESSION_NO_SLOT,
              "attached"]),
])

# --- the harness registry -------------------------------------------------- #
# 🔴 The record addresses window @502 — the window sitting at INDEX "501".
PID_B = 60202
SESSION_ID_B = "aaaaaaaa-1111-4222-8333-444444444444"
HARNESS_NAME_B = "harness-beta-77"
HARNESS_STATUS_B = "waiting"

# Timestamps: epoch MILLISECOND integers, pairwise distinct. The expected ISO
# strings are LITERALS computed independently of `parse_epoch_ms`.
TS_STARTED = 1787117530032
TS_STARTED_ISO = "2026-08-19T05:32:10.032000Z"
TS_UPDATED = 1787118509172
TS_UPDATED_ISO = "2026-08-19T05:48:29.172000Z"
TS_STATUS = 1787119000123
TS_STATUS_ISO = "2026-08-19T05:56:40.123000Z"
TS_NAMESINCE = 1787117600456
TS_NAMESINCE_ISO = "2026-08-19T05:33:20.456000Z"

REGISTRY_RECORD_B = {
    "pid": PID_B,
    "sessionId": SESSION_ID_B,
    "cwd": CWD_B,
    "startedAt": TS_STARTED,
    "updatedAt": TS_UPDATED,
    "statusUpdatedAt": TS_STATUS,
    "nameSince": TS_NAMESINCE,
    "kind": "interactive",
    "entrypoint": "cli",
    "tmux": f"alpha1:{WIN_B_ID}.{PANE_B}",
    "name": HARNESS_NAME_B,
    "nameSource": "derived",
    "status": HARNESS_STATUS_B,
}

# --- session-manager ------------------------------------------------------- #
SM_STATUS_A = "busy"
SM_TASK_A = "task text alpha"
SM_AGE_A = 1234.5
SM_AGE_SOURCE_A = "ledger"
SM_RUNTIME_A = "claude"
SM_CLAUDE_SESSION_A = "bbbbbbbb-5555-4666-8777-888888888888"
SM_UNSENT_A = "half typed thing"
SM_SIGNAL_NAME = "selection_menu"
# 🔴 Captured pane text is what `SIGNAL_CAPTURED_TEXT_KEY` must never render.
# This value is SYNTHETIC — the repo is public and forbids captured text.
SM_SIGNAL_LINE = "SYNTHETIC-NEVER-RENDER-THIS-LINE"


def _sm_row(session, window_id, window_index, **over):
    row = {
        "session": session,
        "window_id": window_id,
        "window_index": window_index,
        "status": None,
        "waiting_probable": False,
        "waiting_signals": [],
        "waiting_status": "ok",
        "unsent_prompt": None,
        "unsent_prompt_status": "ok",
        "age_secs": None,
        "age_source": None,
        "runtime": None,
        "claude": True,
        "busy": False,
        "task": None,
        "claude_session_id": None,
        "label": None,
        "label_source": None,
        "window_name": None,
    }
    row.update(over)
    return row


def sm_payload():
    return {
        "hosts": {
            HOST: {
                "reachable": True,
                "windows": [
                    _sm_row("alpha1", WIN_A_ID, WIN_A_INDEX,
                            status=SM_STATUS_A, waiting_probable=True,
                            waiting_signals=[{"signal": SM_SIGNAL_NAME,
                                              "line": SM_SIGNAL_LINE}],
                            task=SM_TASK_A, age_secs=SM_AGE_A,
                            age_source=SM_AGE_SOURCE_A, runtime=SM_RUNTIME_A,
                            unsent_prompt=SM_UNSENT_A,
                            claude_session_id=SM_CLAUDE_SESSION_A),
                    _sm_row("alpha1", WIN_B_ID, WIN_B_INDEX),
                    _sm_row("gamma3", WIN_C_ID, WIN_C_INDEX),
                    _sm_row(SESSION_NO_SLOT, WIN_D_ID, WIN_D_INDEX),
                ],
            }
        }
    }


def base_sources(**over):
    kwargs = dict(
        host=HOST,
        panes_raw=PANES_RAW,
        windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW,
        slot_table_text=SLOT_TEXT,
        registry_records=[dict(REGISTRY_RECORD_B)],
        sm_payload=sm_payload(),
    )
    kwargs.update(over)
    return sr.Sources(**kwargs)


def resolve(selector, **over):
    return sr.resolve(selector, base_sources(**over))


def target_for(selector, **over):
    res = resolve(selector, **over)
    assert res["status"] == sr.STATUS_RESOLVED, res.get("reason")
    return res["target"]


# =========================================================================== #
# §1  The join key
# =========================================================================== #
def test_the_join_is_on_window_id_not_window_index():
    """🔴 THE load-bearing guard. The registry record addresses `@502`, and the
    window at INDEX "502" is a DIFFERENT window (`@501`). A join on window_index
    still finds a match — the wrong one — so only this crossed fixture can tell
    the two joins apart.
    """
    by_id = target_for(WIN_B_ID)          # @502, the addressed window
    assert by_id["window_id"] == WIN_B_ID
    assert by_id["window_index"] == WIN_B_INDEX
    assert by_id["harness_presence"] == sr.PRESENCE_PRESENT
    assert by_id["harness"]["pid"] == PID_B
    assert by_id["harness"]["name"] == HARNESS_NAME_B
    assert by_id["harness_status"] == HARNESS_STATUS_B

    # And the window whose INDEX equals the addressed id's number must NOT have
    # picked the record up.
    decoy = target_for(WIN_A_ID)          # @501, sitting at index "502"
    assert decoy["window_index"] == WIN_A_INDEX == "502"
    assert decoy["harness_presence"] == sr.PRESENCE_ABSENT
    assert decoy["harness"] is None


def test_parse_tmux_address_splits_session_window_and_pane():
    key, pane = sr.parse_tmux_address(f"alpha1:{WIN_B_ID}.{PANE_B}")
    assert key == ("alpha1", WIN_B_ID)
    assert pane == PANE_B


@pytest.mark.parametrize("bad", [
    None, "", "no-colon", 17, "alpha1:501.%602", "alpha1:", ":@502.%602",
])
def test_parse_tmux_address_rejects_anything_not_session_at_window(bad):
    """A window id MUST start with `@`. `alpha1:501` is an INDEX address and is
    refused outright rather than joined."""
    assert sr.parse_tmux_address(bad) == (None, None)


def test_a_session_manager_row_without_window_id_is_never_joined_on_index():
    """🔴 The `--lean` shape. Its rows carry window_index only. They must be
    counted UNJOINABLE and named, never joined on the index."""
    payload = sm_payload()
    for row in payload["hosts"][HOST]["windows"]:
        del row["window_id"]
    res = resolve(WIN_B_ID, sm_payload=payload)
    cov = res["coverage"]
    assert cov["session_manager_rows"] == 4
    assert cov["session_manager_rows_unjoinable"] == 4
    assert cov["windows_with_session_manager_row"] == 0
    assert any("window_id" in d and "NOT joined" in d for d in res["dropped"])
    t = res["target"]
    assert t["session_manager_presence"] == sr.PRESENCE_ABSENT
    assert t["waiting_probable"] is None


def test_sm_argv_does_not_request_the_lean_view():
    """🔴 Pins the refutation in the module docstring: `--lean` drops window_id,
    so asking for it would silently make every row unjoinable."""
    assert "--lean" not in sr.SM_ARGV
    assert "--json" in sr.SM_ARGV


# =========================================================================== #
# §2  Selector kinds
# =========================================================================== #
def test_selector_codename():
    t = target_for(CODENAME_C)
    assert t["session"] == "gamma3"
    assert t["window_id"] == WIN_C_ID
    assert t["codename"] == CODENAME_C


def test_selector_codename_is_case_insensitive():
    assert target_for(CODENAME_C.upper())["window_id"] == WIN_C_ID
    assert target_for(CODENAME_C.lower())["window_id"] == WIN_C_ID


def test_selector_hotkey():
    t = target_for("Z")
    assert t["window_id"] == WIN_C_ID
    assert t["hotkey"] == "Z"


def test_selector_hotkey_tolerates_modifier_prefixes():
    for spelling in ("Alt+Shift+Z", "$mod+Shift+Z", "Mod4+Z"):
        assert target_for(spelling)["window_id"] == WIN_C_ID, spelling


def test_hotkey_case_is_significant():
    """🔴 `Q` is Quartz (alpha1) and `q` is quill (beta2). Lower-casing the key
    would merge ten pairs of real slots. beta2 has no windows in this fixture,
    so `q` must find NOTHING while `Q` finds alpha1's windows."""
    assert sr.normalise_hotkey("Alt+Shift+Q") == "Q"
    assert sr.normalise_hotkey("Alt+q") == "q"
    upper = resolve("Q")
    assert upper["status"] == sr.STATUS_AMBIGUOUS      # alpha1 has 2 windows
    assert {c["session"] for c in upper["candidates"]} == {"alpha1"}
    assert resolve("q")["status"] == sr.STATUS_UNMATCHED


@pytest.mark.parametrize("multi", ["Quartz", "ab", "Alt+Shift+ab", ""])
def test_normalise_hotkey_returns_none_for_anything_but_a_single_key(multi):
    assert sr.normalise_hotkey(multi) is None


def test_selector_session_index():
    """`alpha1:501` addresses the window at INDEX 501, which is @502."""
    t = target_for(f"alpha1:{WIN_B_INDEX}")
    assert t["window_id"] == WIN_B_ID
    assert sr.SELECTOR_SESSION_INDEX in resolve(
        f"alpha1:{WIN_B_INDEX}")["matched_kinds"]


def test_selector_address_session_at_window_id():
    res = resolve(f"alpha1:{WIN_A_ID}")
    assert res["status"] == sr.STATUS_RESOLVED
    assert res["target"]["window_id"] == WIN_A_ID
    assert sr.SELECTOR_ADDRESS in res["matched_kinds"]


def test_selector_window_id():
    res = resolve(WIN_C_ID)
    assert res["target"]["session"] == "gamma3"
    assert sr.SELECTOR_WINDOW_ID in res["matched_kinds"]


def test_selector_harness_name():
    res = resolve(HARNESS_NAME_B)
    assert res["status"] == sr.STATUS_RESOLVED
    assert res["target"]["window_id"] == WIN_B_ID
    assert sr.SELECTOR_HARNESS_NAME in res["matched_kinds"]


def test_selector_cwd_full_path_and_repo_basename():
    by_path = resolve(CWD_C)
    by_repo = resolve(os.path.basename(CWD_C))
    assert by_path["target"]["window_id"] == WIN_C_ID
    assert by_repo["target"]["window_id"] == WIN_C_ID
    assert sr.SELECTOR_CWD in by_path["matched_kinds"]
    assert sr.SELECTOR_CWD in by_repo["matched_kinds"]


def test_selector_session_name_matches_every_window_in_it():
    res = resolve("alpha1")
    assert res["status"] == sr.STATUS_AMBIGUOUS
    assert {c["window_id"] for c in res["candidates"]} == {WIN_A_ID, WIN_B_ID}
    for c in res["candidates"]:
        assert sr.SELECTOR_SESSION in c["matched_kinds"]


def test_every_supported_selector_kind_is_reachable():
    """A ledger pinned two ways: the advertised kinds and the kinds this suite
    has actually exercised must be the SAME SET. A kind added to the module
    without a test, or a test for a kind the module no longer advertises, fails
    here rather than being discovered later."""
    exercised = set()
    for sel in (CODENAME_C, "Z", f"alpha1:{WIN_B_INDEX}", f"alpha1:{WIN_A_ID}",
                WIN_C_ID, HARNESS_NAME_B, CWD_C, "alpha1"):
        res = sr.resolve(sel, base_sources())
        if res["status"] == sr.STATUS_RESOLVED:
            exercised.update(res["matched_kinds"])
        else:
            for cand in res["candidates"]:
                exercised.update(cand["matched_kinds"])
    assert exercised == set(sr.ALL_SELECTOR_KINDS)


# =========================================================================== #
# §3  Ambiguity and no-match
# =========================================================================== #
def test_ambiguous_selector_lists_candidates_and_refuses():
    """🔴 NEVER silently picks. The refusal must carry every candidate and the
    kinds each matched under, or it is not actionable."""
    res = resolve("alpha1")
    assert res["status"] == sr.STATUS_AMBIGUOUS
    assert res["target"] is None
    assert res["candidate_count"] == 2
    addrs = {c["address"] for c in res["candidates"]}
    assert addrs == {f"alpha1:{WIN_A_ID}", f"alpha1:{WIN_B_ID}"}
    assert "refusing to pick" in res["reason"]


def test_ambiguity_across_two_different_selector_kinds_still_refuses():
    """A selector that names one target by cwd and another by codename is
    ambiguous even though no single kind is."""
    panes = PANES_RAW + "\n" + _pane_line(
        "gamma3", "12", "@505", "%605", "/w/" + CODENAME_C, "t")
    res = resolve(CODENAME_C, panes_raw=panes)
    assert res["status"] == sr.STATUS_AMBIGUOUS
    kinds = {k for c in res["candidates"] for k in c["matched_kinds"]}
    assert kinds == {sr.SELECTOR_CODENAME, sr.SELECTOR_CWD}


def test_unmatched_selector_names_the_sources_consulted():
    res = resolve("no-such-target-anywhere")
    assert res["status"] == sr.STATUS_UNMATCHED
    assert res["target"] is None
    assert res["candidates"] == []
    assert res["sources_consulted"] == list(sr.ALL_SOURCES)
    for source in ("tmux", "slot-table", "harness-registry", "session-manager"):
        assert source in res["reason"] or source in res["sources_consulted"]
    assert res["coverage"]["windows_total"] == 4


def test_unmatched_render_prints_every_source_and_its_status():
    text = sr.render(resolve("nothing-matches-this"))
    assert "UNMATCHED" in text
    for label in ("tmux", "slot table", "registry", "session-manager"):
        assert label in text


def test_resolution_exit_codes_are_distinct():
    assert len({sr.EXIT_RESOLVED, sr.EXIT_AMBIGUOUS, sr.EXIT_UNMATCHED}) == 3


# =========================================================================== #
# §4  The slot table
# =========================================================================== #
def test_slot_table_is_parsed_not_hardcoded():
    slots = sr.parse_slot_table(SLOT_TEXT)
    assert slots["alpha1"] == {"session": "alpha1", "hotkey": "Q",
                               "colour": "#111111", "codename": CODENAME_A}
    assert slots["beta2"]["codename"] == CODENAME_B
    assert set(slots) == {"alpha1", "beta2", "gamma3"}


def test_slot_table_prose_mentioning_a_session_is_not_parsed_as_data():
    """The real table's comments name `scratch4` and `$mod+Shift+V`. Only the
    SCRATCH_SLOTS array is data."""
    slots = sr.parse_slot_table(SLOT_TEXT)
    assert SESSION_NO_SLOT not in slots
    assert len(slots) == 3


def test_a_session_with_no_slot_entry_has_no_hotkey_and_falls_back_to_cwd():
    """🔴 No hotkey is INVENTED for a session the table does not list."""
    t = target_for(WIN_D_ID)
    assert t["session"] == SESSION_NO_SLOT
    assert t["hotkey"] is None
    assert t["codename"] is None
    assert t["colour"] is None
    assert t["label"] == os.path.basename(CWD_D)
    assert t["label_source"] == sr.LABEL_SOURCE_CWD


def test_a_slotted_session_labels_from_the_slot_not_the_cwd():
    t = target_for(WIN_C_ID)
    assert t["label"] == CODENAME_C
    assert t["label_source"] == sr.LABEL_SOURCE_SLOT
    assert t["label"] != os.path.basename(CWD_C)


def test_label_falls_back_to_the_session_name_when_there_is_no_cwd_either():
    panes = _pane_line("epsilon5", "3", "@777", "%777", "", "")
    t = target_for("@777", panes_raw=panes,
                   windows_raw=SEP.join(["epsilon5", "@777", "0"]))
    assert t["label"] == "epsilon5"
    assert t["label_source"] == sr.LABEL_SOURCE_SESSION


def test_absent_slot_table_is_unmeasured_not_an_empty_mapping(tmp_path):
    """🔴 A missing slot table must not read as 'every session has no slot'."""
    missing = str(tmp_path / "gone.sh")
    res = sr.resolve(WIN_C_ID, sr.Sources(
        host=HOST, panes_raw=PANES_RAW, windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW, slot_table_text=None, slot_table_path=missing,
        registry_records=[], sm_payload=sm_payload()))
    t = res["target"]
    assert sr.is_unmeasured(t["slot_status"])
    assert "slot-table-absent" in t["slot_status"]
    assert t["codename"] is None
    assert res["coverage"]["windows_with_slot"] == 0


def test_slot_table_is_read_from_disk_when_no_text_is_injected(tmp_path):
    path = tmp_path / "slots.sh"
    path.write_text(SLOT_TEXT, encoding="utf-8")
    res = sr.resolve(WIN_C_ID, sr.Sources(
        host=HOST, panes_raw=PANES_RAW, windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW, slot_table_text=None,
        slot_table_path=str(path), registry_records=[],
        sm_payload=sm_payload()))
    assert res["target"]["codename"] == CODENAME_C
    assert res["target"]["slot_status"] == sr.MEASURED_OK


# =========================================================================== #
# §5  The registry — epoch-ms, three states, malformed input
# =========================================================================== #
def test_epoch_ms_integers_are_parsed_to_iso():
    """🔴 The registry's timestamps are epoch MILLISECOND INTEGERS. A parser
    that handles only ISO strings returns None for every record and is
    indistinguishable from the field being absent."""
    assert sr.parse_epoch_ms(TS_STARTED) == (TS_STARTED_ISO, None)
    assert sr.parse_epoch_ms(TS_UPDATED) == (TS_UPDATED_ISO, None)
    assert sr.parse_epoch_ms(TS_STATUS) == (TS_STATUS_ISO, None)
    assert sr.parse_epoch_ms(TS_NAMESINCE) == (TS_NAMESINCE_ISO, None)


def test_the_four_timestamps_reach_the_target_under_their_own_names():
    """Each field carries its OWN value — pairwise distinct, so a mutant that
    reads the wrong key cannot pass."""
    times = target_for(WIN_B_ID)["harness"]["times"]
    assert times["startedAt"] == TS_STARTED
    assert times["startedAt_iso"] == TS_STARTED_ISO
    assert times["updatedAt_iso"] == TS_UPDATED_ISO
    assert times["statusUpdatedAt_iso"] == TS_STATUS_ISO
    assert times["nameSince_iso"] == TS_NAMESINCE_ISO
    assert len({times["startedAt_iso"], times["updatedAt_iso"],
                times["statusUpdatedAt_iso"], times["nameSince_iso"]}) == 4


def test_a_string_timestamp_is_unmeasured_with_a_reason_never_zero():
    rec = dict(REGISTRY_RECORD_B, statusUpdatedAt="2026-08-19T05:56:40Z")
    times = target_for(WIN_B_ID, registry_records=[rec])["harness"]["times"]
    assert times["statusUpdatedAt_iso"] is None
    assert sr.is_unmeasured(times["statusUpdatedAt_status"])
    assert "not-epoch-ms:str" in times["statusUpdatedAt_status"]
    # the OTHER timestamps still parse — one bad field does not poison the rest
    assert times["startedAt_iso"] == TS_STARTED_ISO


def test_a_missing_timestamp_is_unmeasured_absent_not_epoch_zero():
    rec = dict(REGISTRY_RECORD_B)
    del rec["updatedAt"]
    times = target_for(WIN_B_ID, registry_records=[rec])["harness"]["times"]
    assert times["updatedAt_iso"] is None
    assert times["updatedAt_status"] == sr.unmeasured("updatedAt:absent")
    assert "1970" not in str(times["updatedAt_iso"])


def test_a_boolean_timestamp_is_rejected_rather_than_read_as_one_millisecond():
    """`isinstance(True, int)` is True in Python, so without an explicit bool
    check `true` would silently become 1970-01-01T00:00:00.001Z."""
    iso, reason = sr.parse_epoch_ms(True)
    assert iso is None
    assert "bool" in reason


def test_malformed_registry_json_is_counted_and_named_not_silently_skipped(
        tmp_path):
    reg = tmp_path / "sessions"
    reg.mkdir()
    (reg / "1111.json").write_text(json.dumps(REGISTRY_RECORD_B),
                                   encoding="utf-8")
    (reg / "2222.json").write_text("{not valid json", encoding="utf-8")
    (reg / "3333.json").write_text('["a list, not an object"]',
                                   encoding="utf-8")
    res = sr.resolve(WIN_B_ID, sr.Sources(
        host=HOST, panes_raw=PANES_RAW, windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW, slot_table_text=SLOT_TEXT,
        registry_records=None, registry_dir=str(reg), sm_payload=sm_payload()))
    cov = res["coverage"]
    assert cov["registry_unparseable_files"] == 2
    assert cov["registry_records"] == 1
    assert any("unparseable registry file" in d for d in res["dropped"])
    assert any("2222.json" in d for d in res["dropped"])
    # the VALID record still joined — a bad neighbour does not lose it
    assert res["target"]["harness"]["pid"] == PID_B


def test_an_absent_registry_directory_is_unmeasured_for_every_window(tmp_path):
    res = sr.resolve(WIN_B_ID, sr.Sources(
        host=HOST, panes_raw=PANES_RAW, windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW, slot_table_text=SLOT_TEXT,
        registry_records=None, registry_dir=str(tmp_path / "nope"),
        sm_payload=sm_payload()))
    t = res["target"]
    assert t["harness_presence"] == sr.PRESENCE_UNMEASURED
    assert "registry-absent" in t["harness_status_measurement"]
    assert res["coverage"]["windows_with_registry_match"] == 0


# =========================================================================== #
# §6  🔴 THE PRESENCE TRICHOTOMY — three states pinned APART, per source
# =========================================================================== #
def test_presence_tokens_are_four_distinct_values():
    """🔴 FOUR, not three. `structurally_excluded` was the state with no name:
    a REMOTE window can never carry a harness record, because the registry is
    read off the LOCAL filesystem. Collapsing it into `absent` tells a caller
    "none (normal)" and invites it to re-check for a clock that never arrives.
    """
    assert len(set(sr.ALL_PRESENCE_STATES)) == 4
    assert sr.PRESENCE_EXCLUDED in sr.ALL_PRESENCE_STATES
    assert sr.PRESENCE_EXCLUDED != sr.PRESENCE_ABSENT
    assert sr.PRESENCE_EXCLUDED != sr.PRESENCE_UNMEASURED
    assert sr.PRESENCE_PRESENT != sr.PRESENCE_ABSENT != sr.PRESENCE_UNMEASURED
    assert sr.PRESENCE_PRESENT != sr.PRESENCE_UNMEASURED


def test_harness_presence_present_absent_and_unmeasured_are_all_distinct(
        tmp_path):
    """🔴 Per-source trichotomy #1. The ABSENT fixture cannot be satisfied by
    the UNMEASURED path: it supplies a registry that WAS read successfully (a
    real, non-empty record list) and simply has no record for that window."""
    present = target_for(WIN_B_ID)
    assert present["harness_presence"] == sr.PRESENCE_PRESENT

    absent = target_for(WIN_A_ID)                # registry read; no record here
    assert absent["harness_presence"] == sr.PRESENCE_ABSENT
    assert absent["harness_status_measurement"] == sr.MEASURED_OK
    assert not sr.is_unmeasured(absent["harness_status_measurement"])

    unmeasured = sr.resolve(WIN_A_ID, sr.Sources(
        host=HOST, panes_raw=PANES_RAW, windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW, slot_table_text=SLOT_TEXT,
        registry_records=None, registry_dir=str(tmp_path / "gone"),
        sm_payload=sm_payload()))["target"]
    assert unmeasured["harness_presence"] == sr.PRESENCE_UNMEASURED
    assert sr.is_unmeasured(unmeasured["harness_status_measurement"])

    assert len({present["harness_presence"], absent["harness_presence"],
                unmeasured["harness_presence"]}) == 3


def test_harness_absent_and_unmeasured_render_as_different_strings(tmp_path):
    """🔴 The render is where the two collapse most easily — both have a None
    status value. Whole-string assertions, so a reword cannot walk them."""
    absent = sr.render_harness_status(target_for(WIN_A_ID))
    assert absent == sr.HARNESS_NO_RECORD
    assert "UNMEASURED" not in absent

    t = sr.resolve(WIN_A_ID, sr.Sources(
        host=HOST, panes_raw=PANES_RAW, windows_raw=WINDOWS_RAW,
        clients_raw=CLIENTS_RAW, slot_table_text=SLOT_TEXT,
        registry_records=None, registry_dir=str(tmp_path / "gone"),
        sm_payload=sm_payload()))["target"]
    unmeasured_text = sr.render_harness_status(t)
    assert "UNMEASURED" in unmeasured_text
    assert unmeasured_text != absent


def test_session_manager_presence_present_absent_and_unmeasured_are_distinct():
    """🔴 Per-source trichotomy #2. ABSENT = session-manager ran and had no row
    for this window (built from a payload that DOES carry other rows, so the
    unmeasured path cannot satisfy it). UNMEASURED = it could not be read."""
    present = target_for(WIN_A_ID)
    assert present["session_manager_presence"] == sr.PRESENCE_PRESENT
    assert present["waiting_probable"] is True

    payload = sm_payload()
    payload["hosts"][HOST]["windows"] = [
        r for r in payload["hosts"][HOST]["windows"]
        if r["window_id"] != WIN_A_ID]
    assert payload["hosts"][HOST]["windows"], "fixture must keep other rows"
    absent = target_for(WIN_A_ID, sm_payload=payload)
    assert absent["session_manager_presence"] == sr.PRESENCE_ABSENT
    assert absent["waiting_probable"] is None      # UNKNOWN, never False

    unmeasured = target_for(WIN_A_ID, sm_payload={"no_hosts_key": True})
    assert unmeasured["session_manager_presence"] == sr.PRESENCE_UNMEASURED
    assert unmeasured["waiting_probable"] is None

    assert len({present["session_manager_presence"],
                absent["session_manager_presence"],
                unmeasured["session_manager_presence"]}) == 3


def test_pr_presence_present_absent_and_unmeasured_are_all_distinct():
    """🔴 Per-source trichotomy #3, the one that matters most. "gh answered and
    this branch has NO open PR" (work is done) and "gh failed / never ran"
    (unknown) are OPPOSITE answers to session-manager's documented blind spot.
    """
    branch = "feat/some-branch"
    pr_number = 4242

    def git_ok(argv, timeout=None):
        return 0, branch + "\n", ""

    def gh_with_pr(argv, timeout=None):
        return 0, json.dumps([{"number": pr_number, "title": "t",
                               "state": "OPEN", "isDraft": False,
                               "url": "u", "mergeStateStatus": "BLOCKED"}]), ""

    def gh_empty(argv, timeout=None):
        return 0, "[]", ""

    def gh_broken(argv, timeout=None):
        return 1, "", "gh: could not authenticate"

    present = target_for(WIN_C_ID, want_pr=True, git_runner=git_ok,
                         gh_runner=gh_with_pr)["git"]
    assert present["pr_presence"] == sr.PRESENCE_PRESENT
    assert present["pr"][0]["number"] == pr_number
    assert present["pr_status"] == sr.MEASURED_OK

    absent = target_for(WIN_C_ID, want_pr=True, git_runner=git_ok,
                        gh_runner=gh_empty)["git"]
    assert absent["pr_presence"] == sr.PRESENCE_ABSENT
    assert absent["pr"] == []
    assert absent["pr_status"] == sr.MEASURED_OK
    assert not sr.is_unmeasured(absent["pr_status"])

    unmeasured = target_for(WIN_C_ID, want_pr=True, git_runner=git_ok,
                            gh_runner=gh_broken)["git"]
    assert unmeasured["pr_presence"] == sr.PRESENCE_UNMEASURED
    assert unmeasured["pr"] is None
    assert "gh-failed" in unmeasured["pr_status"]

    assert len({present["pr_presence"], absent["pr_presence"],
                unmeasured["pr_presence"]}) == 3


def test_pr_absent_and_unmeasured_render_as_different_strings():
    def git_ok(argv, timeout=None):
        return 0, "feat/x\n", ""

    def gh_empty(argv, timeout=None):
        return 0, "[]", ""

    def gh_broken(argv, timeout=None):
        return 3, "", "boom"

    absent_text = sr.render(sr.resolve(WIN_C_ID, base_sources(
        want_pr=True, git_runner=git_ok, gh_runner=gh_empty)))
    broken_text = sr.render(sr.resolve(WIN_C_ID, base_sources(
        want_pr=True, git_runner=git_ok, gh_runner=gh_broken)))
    assert sr.PR_NONE_MEASURED in absent_text
    assert sr.PR_NONE_MEASURED not in broken_text
    assert "UNMEASURED" in broken_text.split("open PR")[1]


def test_a_pr_lookup_that_was_never_requested_is_unmeasured_not_no_pr():
    """🔴 `--pr` off must NOT read as 'this branch has no PR'."""
    def git_ok(argv, timeout=None):
        return 0, "feat/y\n", ""

    g = target_for(WIN_C_ID, want_pr=False, git_runner=git_ok)["git"]
    assert g["pr_presence"] == sr.PRESENCE_UNMEASURED
    assert g["pr_status"] == sr.unmeasured("pr-lookup-not-requested")
    assert g["pr"] is None


def test_branch_presence_present_absent_and_unmeasured_are_all_distinct():
    """🔴 Per-source trichotomy #4. A DETACHED HEAD is a measured absence of a
    branch (git answered); a failed git is unmeasured."""
    def git_named(argv, timeout=None):
        return 0, "release/9.9\n", ""

    def git_detached(argv, timeout=None):
        return 0, "\n", ""

    def git_broken(argv, timeout=None):
        return 128, "", "fatal: not a git repository"

    present = target_for(WIN_C_ID, git_runner=git_named)["git"]
    assert present["branch_presence"] == sr.PRESENCE_PRESENT
    assert present["branch"] == "release/9.9"
    assert present["branch_status"] == sr.MEASURED_OK

    absent = target_for(WIN_C_ID, git_runner=git_detached)["git"]
    assert absent["branch_presence"] == sr.PRESENCE_ABSENT
    assert absent["branch"] is None
    assert absent["branch_status"] == sr.MEASURED_OK

    unmeasured = target_for(WIN_C_ID, git_runner=git_broken)["git"]
    assert unmeasured["branch_presence"] == sr.PRESENCE_UNMEASURED
    assert "git-branch-failed" in unmeasured["branch_status"]

    assert len({present["branch_presence"], absent["branch_presence"],
                unmeasured["branch_presence"]}) == 3


def test_gh_bad_json_degrades_to_unmeasured_not_to_no_pr():
    def git_ok(argv, timeout=None):
        return 0, "feat/z\n", ""

    def gh_garbage(argv, timeout=None):
        return 0, "not json at all", ""

    g = target_for(WIN_C_ID, want_pr=True, git_runner=git_ok,
                   gh_runner=gh_garbage)["git"]
    assert g["pr_presence"] == sr.PRESENCE_UNMEASURED
    assert "gh-bad-json" in g["pr_status"]
    assert g["pr"] is None


def test_the_pr_lookup_is_cached_per_branch():
    """The `gh` call is the slow one; it must run ONCE per (cwd, branch) even
    though several windows share a cwd."""
    calls = []

    def git_ok(argv, timeout=None):
        return 0, "feat/cached\n", ""

    def gh_counting(argv, timeout=None):
        calls.append(argv)
        return 0, "[]", ""

    src = base_sources(want_pr=True, git_runner=git_ok, gh_runner=gh_counting)
    built = sr.build_targets(src)
    assert len(built["targets"]) == 4
    # Four windows across four DISTINCT cwds -> one lookup each.
    assert len(calls) == 4
    # 🔴 The caching claim itself: a second pass over the SAME Sources must add
    # ZERO calls. Without this the test above would pass with no cache at all.
    sr.build_targets(src)
    assert len(calls) == 4

    # And the cache is keyed per (cwd, branch), not globally: a fresh Sources
    # re-queries. Otherwise "cached" would mean "queried once, ever".
    src2 = base_sources(want_pr=True, git_runner=git_ok, gh_runner=gh_counting)
    sr.build_targets(src2)
    assert len(calls) == 8


# =========================================================================== #
# §7  Two statuses, never collapsed
# =========================================================================== #
def test_harness_status_and_waiting_probable_are_carried_separately():
    """🔴 They are different measurements and they DISAGREE. Window @502 is
    `waiting` per the registry and NOT waiting per session-manager; window @501
    is the mirror image. Neither may be derived from the other."""
    b = target_for(WIN_B_ID)
    assert b["harness_status"] == HARNESS_STATUS_B      # "waiting"
    assert b["waiting_probable"] is False               # session-manager says no

    a = target_for(WIN_A_ID)
    assert a["harness_presence"] == sr.PRESENCE_ABSENT  # registry: no record
    assert a["waiting_probable"] is True                # session-manager: yes

    assert b["harness_status"] != b["waiting_probable"]


def test_no_field_merges_or_sums_the_two_statuses():
    """A structural guard on the SCHEMA: no key may exist whose name suggests a
    combined verdict. Inventing one repeats the `blocked_on_me` mistake."""
    t = target_for(WIN_B_ID)
    forbidden = ("blocked_on_me", "is_waiting", "waiting", "needs_attention",
                 "attention", "combined_status", "overall_status")
    for key in forbidden:
        assert key not in t, f"{key!r} collapses two distinct measurements"
    assert "harness_status" in t and "waiting_probable" in t


def test_session_manager_fields_are_passed_through_verbatim():
    """🔴 CONSUMED, NEVER RE-DERIVED. Each value must be session-manager's own."""
    t = target_for(WIN_A_ID)
    assert t["session_manager_status"] == SM_STATUS_A
    assert t["task"] == SM_TASK_A
    assert t["age_secs"] == SM_AGE_A
    assert t["age_source"] == SM_AGE_SOURCE_A
    assert t["runtime"] == SM_RUNTIME_A
    assert t["claude_session_id"] == SM_CLAUDE_SESSION_A
    assert t["unsent_prompt"] == SM_UNSENT_A
    assert t["waiting_signals"] == [{"signal": SM_SIGNAL_NAME,
                                     "line": SM_SIGNAL_LINE}]


def test_waiting_probable_is_never_synthesised_when_session_manager_gave_none():
    """`--no-capture` makes waiting_probable null for every row. Null must stay
    null and render as UNMEASURED — never as False."""
    payload = sm_payload()
    for row in payload["hosts"][HOST]["windows"]:
        row["waiting_probable"] = None
        row["waiting_status"] = "capture-skipped"
    t = target_for(WIN_A_ID, sm_payload=payload)
    assert t["waiting_probable"] is None
    text = sr.render_waiting_probable(t)
    assert sr.WAITING_NOT_MEASURED in text
    assert "capture-skipped" in text
    assert text != "False"


def test_rendered_signals_never_include_the_captured_pane_line():
    """🔴 This repo is PUBLIC and forbids captured text. The signal NAME is
    rendered; the scraped `line` is not."""
    assert sr.signal_names([{"signal": SM_SIGNAL_NAME, "line": SM_SIGNAL_LINE}]) \
        == [SM_SIGNAL_NAME]
    text = sr.render(resolve(WIN_A_ID))
    assert SM_SIGNAL_NAME in text
    assert SM_SIGNAL_LINE not in text


def test_signal_names_tolerates_bare_strings_and_unnamed_dicts():
    assert sr.signal_names(["plain"]) == ["plain"]
    assert sr.signal_names([{"line": SM_SIGNAL_LINE}]) == ["unnamed-signal"]
    assert sr.signal_names(None) == []


# =========================================================================== #
# §8  Real terminal vs popup; visible vs covered
# =========================================================================== #
def test_popup_clients_are_classified_by_term_name_not_by_size():
    clients, malformed = sr.parse_clients(CLIENTS_RAW)
    assert malformed == 0
    base = [c for c in clients if c["base_terminal"]]
    popups = [c for c in clients if c["popup"]]
    assert len(base) == 1 and len(popups) == 2
    assert base[0]["term"] == BASE_TERM and base[0]["tty"] == BASE_TTY
    assert {c["tty"] for c in popups} == {POPUP_TTY, POPUP2_TTY}


def test_a_resized_base_terminal_is_still_a_base_terminal():
    """🔴 Size is EVIDENCE, not the discriminator. A base terminal shrunk to a
    popup's dimensions must not be reclassified."""
    shrunk = SEP.join([BASE_TTY, BASE_TERM, POPUP_W, POPUP_H, "alpha1",
                       "attached"])
    clients, _ = sr.parse_clients(shrunk)
    assert clients[0]["base_terminal"] is True
    assert clients[0]["popup"] is False
    assert clients[0]["width"] == int(POPUP_W)


def test_a_tmux_client_at_a_large_size_is_still_a_popup():
    """The mirror image: the term name decides, even at base-terminal size."""
    big = SEP.join([POPUP_TTY, POPUP_TERM, BASE_W, BASE_H, "alpha1", "attached"])
    clients, _ = sr.parse_clients(big)
    assert clients[0]["popup"] is True
    assert clients[0]["base_terminal"] is False


def test_the_active_window_on_the_base_terminal_is_visible_but_covered():
    """🔴 THE CASE THIS TOOL EXISTS FOR. @501 is alpha1's ACTIVE window, the
    base terminal is attached to alpha1, and two popups are attached elsewhere:
    the target is on screen AND underneath an overlay. 'Open it' is the wrong
    action, and no existing tool could say so."""
    vis = target_for(WIN_A_ID)["visibility"]
    assert vis["status"] == sr.MEASURED_OK
    assert vis["window_active"] is True
    assert vis["visible"] is True
    assert vis["covered"] is True
    assert vis["base_terminal"]["tty"] == BASE_TTY
    assert len(vis["popups"]) == 2
    assert [c["tty"] for c in vis["attached_clients"]] == [BASE_TTY]


def test_a_non_active_window_is_not_visible_and_not_covered():
    vis = target_for(WIN_B_ID)["visibility"]
    assert vis["window_active"] is False
    assert vis["visible"] is False
    assert vis["covered"] is False


def test_a_window_shown_by_a_popup_is_visible_and_not_covered():
    """A target displayed BY the popup is on top, not underneath one."""
    vis = target_for(WIN_C_ID)["visibility"]      # gamma3, shown by POPUP_TTY
    assert vis["window_active"] is True
    assert vis["visible"] is True
    assert vis["covered"] is False


def test_covered_requires_a_popup_to_actually_exist():
    """With the popups gone, the same active window on the same base terminal is
    visible and NOT covered — so `covered` is not just an alias for `visible`."""
    only_base = SEP.join([BASE_TTY, BASE_TERM, BASE_W, BASE_H, "alpha1",
                          "attached,focused"])
    vis = target_for(WIN_A_ID, clients_raw=only_base)["visibility"]
    assert vis["visible"] is True
    assert vis["covered"] is False


def test_no_attached_clients_is_a_measured_zero_not_unmeasured():
    """🔴 tmux answered and nothing is attached. That is a real zero."""
    vis = target_for(WIN_A_ID, clients_raw="")["visibility"]
    assert vis["status"] == sr.MEASURED_OK
    assert vis["clients"] == []
    assert vis["visible"] is False
    assert not sr.is_unmeasured(vis["status"])


def test_visibility_is_unmeasured_when_the_clients_call_failed():
    """🔴 tmux could NOT be asked -> None with a reason, never False."""
    def runner(argv, timeout=None):
        if "list-clients" in argv:
            return 1, "", "no server running"
        if argv and argv[0] == "git":
            return 0, "some-branch\n", ""
        raise AssertionError(f"unexpected {argv!r}")

    t = target_for(WIN_A_ID, clients_raw=None, runner=runner)
    vis = t["visibility"]
    assert sr.is_unmeasured(vis["status"])
    assert vis["visible"] is None
    assert vis["covered"] is None
    assert vis["clients"] is None


def test_a_window_missing_from_list_windows_is_unmeasured_not_inactive():
    vis = target_for(WIN_A_ID, windows_raw=SEP.join(["alpha1", WIN_B_ID, "0"])
                     )["visibility"]
    assert sr.is_unmeasured(vis["status"])
    assert "window-not-in-list-windows" in vis["status"]
    assert vis["visible"] is None


# =========================================================================== #
# §9  Failure modes — unreachable host, dropped rows, null-not-zero
# =========================================================================== #
def test_an_unreachable_host_is_reported_not_counted_as_zero_windows():
    payload = {"hosts": {
        HOST: sm_payload()["hosts"][HOST],
        "laptop": {"reachable": False, "error": "ssh: connect timed out",
                   "windows": []},
    }}
    res = resolve(WIN_A_ID, sm_payload=payload)
    assert any("laptop" in d and "unreachable" in d for d in res["dropped"])
    assert any("connect timed out" in d for d in res["dropped"])
    # the reachable host's rows are unaffected
    assert res["target"]["session_manager_presence"] == sr.PRESENCE_PRESENT


def test_a_failed_tmux_panes_call_yields_no_targets_and_says_why():
    def runner(argv, timeout=None):
        return 1, "", "no server running on /tmp/tmux-1000/default"

    res = sr.resolve("anything", sr.Sources(
        host=HOST, panes_raw=None, windows_raw=None, clients_raw=None,
        slot_table_text=SLOT_TEXT, registry_records=[],
        sm_payload=sm_payload(), runner=runner))
    assert res["status"] == sr.STATUS_UNMATCHED
    cov = res["coverage"]
    assert cov["windows_total"] == 0
    assert sr.is_unmeasured(cov["tmux_panes_status"])
    assert "no server running" in cov["tmux_panes_status"]


def test_malformed_tmux_lines_are_dropped_and_counted_never_padded():
    """🔴 NO SILENT CAPS. A short row would join against the wrong window if
    padded, so it is dropped — and the drop is reported."""
    panes = PANES_RAW + "\n" + SEP.join(["only", "three", "fields"])
    res = resolve(WIN_A_ID, panes_raw=panes)
    assert res["coverage"]["windows_total"] == 4
    assert any("malformed tmux list-panes" in d for d in res["dropped"])


def test_unmeasured_requires_a_reason():
    with pytest.raises(ValueError):
        sr.unmeasured("")


def test_is_unmeasured_distinguishes_ok_from_every_unmeasured_reason():
    assert not sr.is_unmeasured(sr.MEASURED_OK)
    assert not sr.is_unmeasured(None)
    assert sr.is_unmeasured(sr.unmeasured("any-reason"))


def test_coverage_reports_both_halves_of_every_ratio():
    """🔴 'Registry matched 1 of 4 windows' is the honest statement. A bare
    count hides the denominator, and a silent 0 for the rest invents an
    absence."""
    cov = resolve(WIN_A_ID)["coverage"]
    assert cov["windows_total"] == 4
    assert cov["windows_with_registry_match"] == 1
    assert cov["windows_without_registry_match"] == 3
    assert cov["windows_with_slot"] == 3          # alpha1 x2 + gamma3
    assert cov["windows_without_slot"] == 1       # delta4
    assert cov["windows_with_session_manager_row"] == 4
    assert cov["windows_without_session_manager_row"] == 0
    assert cov["registry_records"] == 1
    assert cov["registry_records_with_tmux"] == 1
    for with_key, without_key in (
            ("windows_with_registry_match", "windows_without_registry_match"),
            ("windows_with_slot", "windows_without_slot"),
            ("windows_with_session_manager_row",
             "windows_without_session_manager_row")):
        assert cov[with_key] + cov[without_key] == cov["windows_total"]


def test_duplicate_registry_records_for_one_window_are_reported():
    dup = dict(REGISTRY_RECORD_B, pid=70303, name="harness-dup-88")
    res = resolve(WIN_B_ID, registry_records=[dict(REGISTRY_RECORD_B), dup])
    assert any("registry records claim" in d for d in res["dropped"])
    assert res["target"]["harness"]["pid"] == PID_B


# =========================================================================== #
# §10  READ-ONLY enforcement, artifacts, and the CLI
# =========================================================================== #
@pytest.mark.parametrize("verb", [
    "select-window", "send-keys", "switch-client", "detach-client",
    "kill-window", "kill-session", "new-window", "rename-window",
    "swap-window", "respawn-pane",
])
def test_every_tmux_write_verb_is_refused_at_the_seam(verb):
    """🔴 An ALLOWLIST, not a denylist: a denylist is walkable by any verb
    nobody thought to ban, and this module's whole promise is that none exists.
    """
    with pytest.raises(sr.ReadOnlyViolation) as exc:
        sr._assert_read_only(["tmux", verb, "-t", "alpha1"])
    assert verb in str(exc.value)


@pytest.mark.parametrize("verb", list(sr.TMUX_READ_ONLY_SUBCOMMANDS))
def test_the_three_read_verbs_are_permitted(verb):
    """POSITIVE CONTROL on the allowlist: a guard that refuses EVERYTHING would
    pass the test above while breaking the tool."""
    sr._assert_read_only(["tmux", verb, "-F", "x"])


def test_the_allowlist_holds_exactly_the_three_read_commands():
    assert set(sr.TMUX_READ_ONLY_SUBCOMMANDS) == {
        "list-panes", "list-windows", "list-clients"}


def test_no_tmux_write_verb_appears_anywhere_in_the_source():
    src_text = open(_SCRIPT, encoding="utf-8").read()
    body = src_text.split('"""', 2)[-1]     # skip the module docstring
    for verb in ("select-window", "send-keys", "switch-client",
                 "detach-client", "kill-server"):
        assert f'"{verb}"' not in body, f"{verb} must not be a live literal"


def test_module_creates_no_on_disk_artifacts(tmp_path, monkeypatch):
    """🔴 The COMPLETE artifact set is EMPTY, pinned as a literal. A cache file
    would be invisible to every behavioural test here — see
    scripts/claude-hooks/tests/test_on_disk_artifact_names.py for the class.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    before = set(str(p) for p in tmp_path.rglob("*"))

    def git_ok(argv, timeout=None):
        return 0, "feat/artifact-check\n", ""

    def gh_ok(argv, timeout=None):
        return 0, "[]", ""

    sr.render(sr.resolve(WIN_A_ID, base_sources(
        want_pr=True, git_runner=git_ok, gh_runner=gh_ok)))

    after = set(str(p) for p in tmp_path.rglob("*"))
    assert after - before == set(), f"unexpected artifacts: {after - before}"


def test_the_cache_lives_in_memory_on_the_sources_object():
    src = base_sources(want_pr=True,
                       git_runner=lambda argv, timeout=None: (0, "b\n", ""),
                       gh_runner=lambda argv, timeout=None: (0, "[]", ""))
    sr.build_targets(src)
    assert src.pr_cache and src.branch_cache
    assert isinstance(src.pr_cache, dict)


def test_two_sources_objects_do_not_share_a_cache():
    """A mutable default would leak one run's PR state into the next."""
    a, b = sr.Sources(), sr.Sources()
    a.pr_cache[("x", "y")] = ("leak", sr.MEASURED_OK, sr.PRESENCE_PRESENT)
    assert b.pr_cache == {}


def test_json_output_is_serialisable_and_carries_the_whole_result():
    res = resolve(WIN_B_ID)
    blob = json.loads(json.dumps(res, sort_keys=True, default=str))
    assert blob["status"] == sr.STATUS_RESOLVED
    assert blob["target"]["harness"]["pid"] == PID_B
    assert blob["coverage"]["windows_total"] == 4
    assert blob["selector"] == WIN_B_ID


def test_cli_parser_accepts_show_and_resolve_and_rejects_other_verbs():
    parser = sr.build_parser()
    assert parser.parse_args(["show", "Gold"]).command == "show"
    assert parser.parse_args(["resolve", "Gold"]).command == "resolve"
    with pytest.raises(SystemExit):
        parser.parse_args(["select", "Gold"])


def test_render_of_a_resolved_target_names_both_statuses_and_the_coverage():
    text = sr.render(resolve(WIN_B_ID))
    assert "harness_status" in text
    assert "waiting_probable" in text
    assert "two statuses, never collapsed" in text
    assert "coverage" in text
    assert f"{WIN_B_ID}" in text


def test_render_of_an_unslotted_session_says_none_rather_than_inventing_a_key():
    text = sr.render(resolve(WIN_D_ID))
    assert "NONE (no slot entry)" in text


# =========================================================================== #
# 🔴 THE MUTATION MATRIX
# =========================================================================== #
#
# Every guard below was broken ON PURPOSE and the named test watched go RED with
# ITS OWN assertion. Run under PYTHONDONTWRITEBYTECODE=1 with __pycache__ purged
# between mutants: CPython validates cached bytecode on whole-second mtime plus
# size, so a same-length edit landing in the same second is served from stale
# bytecode and scores SURVIVED without ever executing.
#
#  # | mutation                                          | killed by
# ---+---------------------------------------------------+---------------------
#  1 | build_targets keys by window_index, and the        | test_the_join_is_on_
#    | registry address parse returns the index           |   window_id_not_
#    | -> the record lands on the window at index "502"   |   window_index
#    |    (@501) instead of the window @502               |   (harness pid claim)
#  2 | parse_epoch_ms accepts only str (the ISO-only bug) | test_epoch_ms_
#    |                                                    |   integers_are_
#    |                                                    |   parsed_to_iso
#  3 | resolve() returns matches[0] instead of refusing   | test_ambiguous_
#    |                                                    |   selector_lists_
#    |                                                    |   candidates_and_
#    |                                                    |   refuses
#  4 | parse_clients classifies popups by width < 260     | test_a_resized_base_
#    |    instead of by TERM name                         |   terminal_is_still_a_
#    |                                                    |   base_terminal
#  5 | compute_visibility drops the `popups_all` term     | test_the_active_
#    |    from `covered` (covered := visible)             |   window_on_the_base_
#    |                                                    |   terminal_is_visible_
#    |                                                    |   but_covered / _covered_
#    |                                                    |   requires_a_popup
#  6 | the no-registry-record path sets PRESENCE_         | test_harness_presence_
#    |    UNMEASURED instead of PRESENCE_ABSENT           |   present_absent_and_
#    |                                                    |   unmeasured_are_all_
#    |                                                    |   distinct
#  7 | gh returning [] sets PRESENCE_UNMEASURED           | test_pr_presence_
#    |    (the "no PR" == "didn't look" collapse)         |   present_absent_and_
#    |                                                    |   unmeasured_are_all_
#    |                                                    |   distinct
#  8 | a failed gh returns [] with MEASURED_OK            | test_pr_presence_… and
#    |                                                    |   test_gh_bad_json_
#    |                                                    |   degrades_to_unmeasured
#  9 | waiting_probable defaults to False when session-   | test_waiting_probable_
#    |    manager gave None                               |   is_never_synthesised
# 10 | a session-manager row without window_id falls back | test_a_session_manager_
#    |    to joining on window_index                      |   row_without_window_id_
#    |                                                    |   is_never_joined_on_index
# 11 | normalise_hotkey lower-cases the key               | test_hotkey_case_is_
#    |                                                    |   significant
# 12 | _assert_read_only becomes a denylist of 3 verbs    | test_every_tmux_write_
#    |                                                    |   verb_is_refused (the
#    |                                                    |   unlisted verbs)
# 13 | malformed tmux rows are padded instead of dropped  | test_malformed_tmux_
#    |                                                    |   lines_are_dropped
# 14 | an empty clients list is reported as UNMEASURED    | test_no_attached_clients_
#    |                                                    |   is_a_measured_zero
# 15 | a missing timestamp yields epoch 0 instead of None | test_a_missing_timestamp_
#    |                                                    |   is_unmeasured_absent
#
# 16 | a REMOTE window reports ABSENT instead of      | test_a_remote_window_is_
#    |    EXCLUDED (the collapse this state exists for)| structurally_excluded_
#    |                                                 | not_measured_absent
# 17 | the session-manager join key drops its HOST      | test_the_join_key_carries_
#    |                                                 | the_host_so_two_machines_
#    |                                                 | cannot_collide
# 18 | git runs against a REMOTE cwd locally           | test_a_remote_target_never_
#    |                                                 | runs_git_against_the_local_
#    |                                                 | filesystem
# 19 | hosts_not_covered stops excluding the local host | test_coverage_names_the_
#    |                                                 | hosts_the_registry_cannot_
#    |                                                 | cover
# 20 | EXCLUDED spelled with unmeasured()               | test_a_remote_window_is_
#    |    ("cannot ever" reads as "did not this time")  | structurally_excluded_...
# 21 | remote visibility reports measured instead of    | test_a_remote_target_
#    |    UNMEASURED                                    | reports_visibility_as_
#    |                                                 | unmeasured
#
MUTATION_MATRIX_MUTANTS = 20


# =========================================================================== #
# §11  🔴 THE FOURTH STATE — a remote window is EXCLUDED, not ABSENT
# =========================================================================== #
REMOTE_HOST = "laptophost"
REMOTE_SESSION = "alpha1"          # SAME session name as the local host has
REMOTE_WINDOW_ID = WIN_A_ID        # 🔴 SAME window id as a LOCAL window (@501)
REMOTE_WINDOW_INDEX = "3"
REMOTE_CWD = "/w/repo-remote"
REMOTE_TASK = "task text on the other machine"
REMOTE_STATUS = "stale"


def sm_payload_two_hosts():
    """Local rows plus a remote host whose (session, window_id) COLLIDES.

    🔴 The collision is the point. Measured across the real pair at one instant:
    14 session NAMES and 11 window_ids exist on BOTH machines, and laptop's id
    range (@4–@31) sits inside workbench's — so a full-pair collision is one
    window-open away. A host-less join key would attach this remote row to the
    LOCAL @501 window.
    """
    payload = sm_payload()
    payload["hosts"][REMOTE_HOST] = {
        "reachable": True,
        "windows": [_sm_row(REMOTE_SESSION, REMOTE_WINDOW_ID,
                            REMOTE_WINDOW_INDEX,
                            status=REMOTE_STATUS, task=REMOTE_TASK,
                            path=REMOTE_CWD, waiting_probable=False)],
    }
    return payload


def two_host_sources(**over):
    kwargs = dict(sm_payload=sm_payload_two_hosts(), local_host=HOST)
    kwargs.update(over)
    return base_sources(**kwargs)


def remote_target(**over):
    built = sr.build_targets(two_host_sources(**over))
    hits = [t for t in built["targets"] if t["host"] == REMOTE_HOST]
    assert len(hits) == 1, [t["address"] for t in built["targets"]]
    return hits[0], built


def test_a_remote_window_is_structurally_excluded_not_measured_absent():
    """🔴 The fixture CANNOT be satisfied by the measured-absence path: the
    registry is read successfully in this very same call (the LOCAL @502 window
    joins a record, and the LOCAL @501 window reports a genuine ABSENT). Only
    the host dimension can produce EXCLUDED here.
    """
    remote, built = remote_target()
    assert remote["harness_presence"] == sr.PRESENCE_EXCLUDED
    assert remote["harness"] is None
    assert remote["harness_status"] is None
    reason = remote["harness_status_measurement"]
    assert "registry-is-local-host-only" in reason
    assert REMOTE_HOST in reason and HOST in reason
    # 🔴 EXCLUDED is NOT spelled with unmeasured() — "cannot look, ever" must
    # not read as "did not look this time".
    assert not sr.is_unmeasured(reason)

    locals_ = {t["window_id"]: t for t in built["targets"]
               if t["host"] == HOST}
    assert locals_[WIN_B_ID]["harness_presence"] == sr.PRESENCE_PRESENT
    assert locals_[WIN_A_ID]["harness_presence"] == sr.PRESENCE_ABSENT


def test_all_four_harness_presence_states_occur_in_one_measurement(tmp_path):
    remote, built = remote_target()
    seen = {t["harness_presence"] for t in built["targets"]}
    assert sr.PRESENCE_PRESENT in seen
    assert sr.PRESENCE_ABSENT in seen
    assert sr.PRESENCE_EXCLUDED in seen

    unmeasured = sr.build_targets(two_host_sources(
        registry_records=None, registry_dir=str(tmp_path / "gone")))
    local_states = {t["harness_presence"] for t in unmeasured["targets"]
                    if t["host"] == HOST}
    assert local_states == {sr.PRESENCE_UNMEASURED}
    # …and the REMOTE one stays EXCLUDED even when the registry is unreadable:
    # a broken local registry does not make a remote window merely unmeasured.
    remote_states = {t["harness_presence"] for t in unmeasured["targets"]
                     if t["host"] == REMOTE_HOST}
    assert remote_states == {sr.PRESENCE_EXCLUDED}


def test_excluded_and_absent_render_as_different_strings():
    """🔴 Whole-string pins. ABSENT invites a re-check; EXCLUDED must not."""
    remote, built = remote_target()
    local_absent = [t for t in built["targets"]
                    if t["host"] == HOST and t["window_id"] == WIN_A_ID][0]
    excluded_text = sr.render_harness_status(remote)
    absent_text = sr.render_harness_status(local_absent)
    assert excluded_text == sr.HARNESS_EXCLUDED
    assert absent_text == sr.HARNESS_NO_RECORD
    assert excluded_text != absent_text
    assert "never" in excluded_text
    assert "UNMEASURED" not in excluded_text


def test_coverage_names_the_hosts_the_registry_cannot_cover():
    """🔴 Spelled `coverage.registry.hosts_not_covered` to MATCH the
    waiting-windows report. Two PRs disagreeing on the vocabulary for one fact
    is its own defect."""
    _, built = remote_target()
    reg = built["coverage"]["registry"]
    assert reg["hosts_not_covered"] == [REMOTE_HOST]
    assert reg["local_host"] == HOST
    assert set(reg["hosts_seen"]) == {HOST, REMOTE_HOST}
    assert reg["windows_structurally_excluded"] == 1
    assert reg["windows_covered"] == 1


def test_hosts_not_covered_is_empty_when_everything_is_local():
    """The POSITIVE CONTROL on the field: it must be able to be empty, or it is
    just a constant that happens to look right in the interesting case."""
    built = sr.build_targets(base_sources())
    reg = built["coverage"]["registry"]
    assert reg["hosts_not_covered"] == []
    assert reg["windows_structurally_excluded"] == 0
    assert not any(t["harness_presence"] == sr.PRESENCE_EXCLUDED
                   for t in built["targets"])


def test_the_render_flags_a_remote_target_and_names_the_excluded_hosts():
    res = sr.resolve(REMOTE_CWD, two_host_sources())
    assert res["status"] == sr.STATUS_RESOLVED
    text = sr.render(res)
    assert "REMOTE" in text
    assert "registry CANNOT cover" in text
    assert REMOTE_HOST in text


# --- the host must be part of the join key -------------------------------- #
def test_the_join_key_carries_the_host_so_two_machines_cannot_collide():
    """🔴 Local @501 and remote @501 are the SAME (session, window_id). Only the
    host separates them; without it one machine's row lands on the other's
    window."""
    _, built = remote_target()
    by_host = {t["host"]: t for t in built["targets"]
               if t["window_id"] == WIN_A_ID}
    assert set(by_host) == {HOST, REMOTE_HOST}
    # each kept ITS OWN session-manager row — pairwise-distinct values, so a
    # crossed join cannot pass
    assert by_host[HOST]["task"] == SM_TASK_A
    assert by_host[HOST]["waiting_probable"] is True
    assert by_host[REMOTE_HOST]["task"] == REMOTE_TASK
    assert by_host[REMOTE_HOST]["waiting_probable"] is False
    assert by_host[REMOTE_HOST]["session_manager_status"] == REMOTE_STATUS
    assert by_host[HOST]["task"] != by_host[REMOTE_HOST]["task"]


def test_a_remote_target_never_runs_git_against_the_local_filesystem():
    """🔴 A remote window's cwd is a path on the OTHER machine. Running `git -C`
    on it locally could SUCCEED against a same-named local checkout and report a
    branch belonging to something else entirely."""
    calls = []

    def git_spy(argv, timeout=None):
        calls.append(argv)
        return 0, "should-never-be-used\n", ""

    remote, built = remote_target(git_runner=git_spy, want_pr=True,
                                  gh_runner=git_spy)
    assert remote["git"]["branch"] is None
    assert remote["git"]["branch_presence"] == sr.PRESENCE_EXCLUDED
    assert remote["git"]["pr_presence"] == sr.PRESENCE_EXCLUDED
    assert "cwd-is-on-remote-host" in remote["git"]["branch_status"]
    assert not any(REMOTE_CWD in " ".join(c) for c in calls), calls
    # POSITIVE CONTROL: the spy IS wired up — local targets did call it.
    assert any(CWD_A in " ".join(c) for c in calls), calls


def test_a_remote_target_reports_visibility_as_unmeasured():
    """tmux clients were read from THIS machine; they say nothing about what is
    on screen over there. Never False, which would assert 'not visible'."""
    remote, _ = remote_target()
    vis = remote["visibility"]
    assert sr.is_unmeasured(vis["status"])
    assert "tmux-read-is-local-only" in vis["status"]
    assert vis["visible"] is None
    assert vis["covered"] is None


def test_a_remote_window_is_still_selectable_by_every_shared_selector():
    """The remote target is a first-class target, not a stub."""
    built = sr.build_targets(two_host_sources())
    for selector in (REMOTE_CWD, "repo-remote"):
        res = sr.resolve(selector, two_host_sources(), built=built)
        assert res["status"] == sr.STATUS_RESOLVED, selector
        assert res["target"]["host"] == REMOTE_HOST


# --- the registry's own waitingFor ---------------------------------------- #
WAITING_FOR_VALUE = "input needed"


def test_the_registry_waiting_for_reason_is_carried_verbatim():
    """Measured: the registry has THREE status values (idle/busy/waiting) and
    `waitingFor` rides along on the waiting ones. Carried beside the status,
    never used to derive waiting_probable."""
    rec = dict(REGISTRY_RECORD_B, status="waiting",
               waitingFor=WAITING_FOR_VALUE)
    t = target_for(WIN_B_ID, registry_records=[rec])
    assert t["harness"]["waiting_for"] == WAITING_FOR_VALUE
    assert t["harness_status"] == "waiting"
    # 🔴 STILL not merged into session-manager's verdict, which disagrees.
    assert t["waiting_probable"] is False


def test_a_record_without_waiting_for_carries_none_not_a_placeholder():
    t = target_for(WIN_B_ID)
    assert t["harness"]["waiting_for"] is None


def test_no_registry_status_value_is_branched_on_in_the_source():
    """🔴 The registry's status vocabulary is NOT stable — two snapshots hours
    apart differed (`shell` present, then gone; `waiting` throughout, and
    `waitingFor` riding along). So the module must pass `status` through and
    never BRANCH on its value.

    Structural, via the syntax tree, not a substring scan: "busy" is also a
    legitimate session-manager FIELD NAME in SM_PASSTHROUGH_FIELDS, and a text
    search cannot tell a dict key from a comparison. What must not exist is a
    COMPARISON against one of these literals.
    """
    tree = ast.parse(open(_SCRIPT, encoding="utf-8").read())
    vocabulary = {"idle", "busy", "shell", "waiting"}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for operand in operands:
                for sub in ast.walk(operand):
                    if isinstance(sub, ast.Constant) and sub.value in vocabulary:
                        offenders.append((node.lineno, sub.value))
    assert not offenders, (
        f"the source BRANCHES on registry status value(s) {offenders}; that "
        "vocabulary is unstable and must be passed through, not matched")


def test_the_status_branch_guard_can_actually_fire():
    """POSITIVE CONTROL on the guard above — a scan that can never find
    anything is indistinguishable from a clean tree."""
    tree = ast.parse('x = 1\nif s == "idle":\n    pass\n')
    hits = [c for n in ast.walk(tree) if isinstance(n, ast.Compare)
            for o in [n.left, *n.comparators]
            for c in ast.walk(o)
            if isinstance(c, ast.Constant) and c.value == "idle"]
    assert len(hits) == 1


def test_local_host_comes_from_session_manager_not_the_host_argument():
    """🔴 `--host all` is not a host NAME. Inferring the local host from the
    argument would label every local window "all" and mark BOTH real machines
    remote — silently excluding the local registry from its own windows.
    session-manager states which box it ran on; that is authoritative.
    """
    payload = sm_payload_two_hosts()
    payload["local_host"] = HOST
    built = sr.build_targets(base_sources(host="all", local_host=None,
                                          sm_payload=payload))
    reg = built["coverage"]["registry"]
    assert reg["local_host"] == HOST
    assert reg["hosts_not_covered"] == [REMOTE_HOST]
    local = [t for t in built["targets"] if t["host"] == HOST]
    assert local and all(
        t["harness_presence"] != sr.PRESENCE_EXCLUDED for t in local)


def test_an_explicit_local_host_overrides_session_managers_answer():
    payload = sm_payload_two_hosts()
    payload["local_host"] = REMOTE_HOST      # deliberately disagrees
    built = sr.build_targets(base_sources(local_host=HOST,
                                          sm_payload=payload))
    assert built["coverage"]["registry"]["local_host"] == HOST


def test_local_host_falls_back_to_the_host_argument_when_nothing_states_it():
    payload = sm_payload_two_hosts()
    payload.pop("local_host", None)
    built = sr.build_targets(base_sources(host=HOST, local_host=None,
                                          sm_payload=payload))
    assert built["coverage"]["registry"]["local_host"] == HOST


def test_a_missing_age_never_renders_a_unit_suffix():
    """A unit welded to a missing value ("UNMEASUREDs") reads like a
    measurement. Pinned in both directions so the numeric path still works."""
    remote, _ = remote_target()
    assert remote["age_secs"] is None
    text = sr.render(sr.resolve(REMOTE_CWD, two_host_sources()))
    assert "UNMEASUREDs" not in text
    assert "age            : UNMEASURED" in text

    local_text = sr.render(resolve(WIN_A_ID))
    assert f"{SM_AGE_A:.0f}s" in local_text
