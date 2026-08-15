"""Unit tests for scripts/i3status-claude-runs — the LIVE Claude-runs count pill.

Exercises the pure `count_live_sessions` wrapper + the `render` formatter. The
wrapper delegates to the shared detector in `scripts/lib/claude_sessions.py`
(parse_panes + classify_claude_sessions), so it is driven here with INJECTED
fixture fetchers (a mock tmux-pane raw dump + a mock /proc index + an own-pid
chain) — nothing touches tmux or /proc.

🔴 THIS FILE AND test_claude_sessions.py TEST ONE DEFINITION, NOT TWO. They used
to mirror each other because the pill and the retired `agent-ops` TUI each
loaded the detector separately; the mirroring was the cost of that. Now there is
a single module and this file's job is only the WRAPPER around it — the counting
seam and the pill's text grammar. `test_detector_is_loaded_from_the_shared_module`
below is the guard that keeps it that way.
"""
import ast
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")


def _load(relpath, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, os.path.join(_SCRIPTS, relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


blk = _load("i3status-claude-runs", "i3status_claude_runs")
cs = _load(os.path.join("lib", "claude_sessions.py"), "claude_sessions_for_block")


# A raw `tmux list-panes -a` dump matching claude_sessions's pipe format:
#   pane_id|pane_pid|session|window_index|window_id|window_name|path|command|
#   server_start|title
# Pinned against the real PANE_FIELDS by
# test_the_fixture_dump_matches_the_current_pane_format — these lines are a
# hand-written copy of a format that lives in another file, and nothing else here
# reads `title`, so drift is otherwise silent.
_RAW_PANES = "\n".join([
    "%0|16060|main|1|@1|devrc ●|/home/zach/workspace/devrc|zsh|1700000000|⠐ Ship the block",
    "%1|16095|svc|2|@2|svc-b|/home/zach/ws/svc-b|zsh|1700000000|",  # plain zsh → not claude
    "%9|500|main|9|@9|self|/home/zach/workspace/devrc|python3|1700000000|bar block",  # own pane
])

# Mock /proc tree: two claude panes + a plain zsh + the block's own tree.
#   16060 zsh -> 108149 .claude-wrapped            [INCLUDE]
#   16095 zsh                                        [EXCLUDE — no claude]
#   500   zsh -> 999 (this block's pid)             [EXCLUDE — own tree]
_PROC = {
    16060: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 100,
            "children": [108149]},
    108149: {"comm": ".claude-wrapped", "ppid": 16060, "state": "R",
             "age_secs": 90, "children": []},
    16095: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 100,
            "children": []},
    500: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 5,
          "children": [999]},
    999: {"comm": "python3", "ppid": 500, "state": "R", "age_secs": 5,
          "children": []},
}


def test_count_uses_real_detector_excludes_plain_and_own():
    # Only the one real Claude pane (%0) counts; the plain zsh pane and this
    # block's own pane (containing pid 999) are excluded.
    n = blk.count_live_sessions(
        cs,
        list_panes=lambda: _RAW_PANES,
        build_index=lambda: _PROC,
        own_chain=lambda: {999},
    )
    assert n == 1


def test_the_fixture_dump_matches_the_current_pane_format():
    """🔴 SEAM. These fixtures hand-copy a format defined in claude_sessions.py,
    and every assertion in this file counts SESSIONS — none reads `title`. So a
    format change shifts every field one place left and the counts stay green.

    MEASURED: adding `#{start_time}` did exactly that. The 9-field lines still
    parsed, every pane_title landed in `server_start`, `title` went empty, and
    all eight tests here passed. Pin the field COUNT against the real
    PANE_FIELDS and pin the parse, so the next format change is loud here.
    """
    for line in _RAW_PANES.splitlines():
        assert len(line.split("|")) == len(cs.PANE_FIELDS), line
    panes = cs.parse_panes(_RAW_PANES)
    assert [p["title"] for p in panes] == ["⠐ Ship the block", "", "bar block"]
    assert [p["server_start"] for p in panes] == ["1700000000"] * 3
    assert [p["command"] for p in panes] == ["zsh", "zsh", "python3"]


def test_the_blocks_OWN_pane_is_not_counted_even_when_it_is_a_claude_session():
    """🔴 FOUND BY MUTATION. `_PROC` puts a plain `python3` in the block's own
    tree — a pane that would never have counted anyway — so the exclusion was
    never observed to exclude anything here either, and `any(...)` → `all(...)`
    in the shared detector survived this whole file.

    The pill is normally spawned by i3status-rs, outside tmux, where the
    exclusion is moot. It is not moot when a human or an agent runs it from a
    pane to check the bar — the pane they run it from is usually a Claude
    session, and counting it inflates the operator's live-agent count by one
    with no way to notice.
    """
    raw = "\n".join([
        "%own|700|main|1|@1|own|/r|zsh|1700000000|⠐ running the pill",
        "%other|800|main|2|@2|other|/r|zsh|1700000000|✳ real work",
    ])
    proc = {
        700: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 50,
              "children": [701]},
        701: {"comm": ".claude-wrapped", "ppid": 700, "state": "R",
              "age_secs": 40, "children": [702]},
        702: {"comm": "python3", "ppid": 701, "state": "R", "age_secs": 1,
              "children": []},          # ← this block, under a claude
        800: {"comm": "zsh", "ppid": 1, "state": "S", "age_secs": 50,
              "children": [801]},
        801: {"comm": ".claude-wrapped", "ppid": 800, "state": "S",
              "age_secs": 40, "children": []},
    }
    n = blk.count_live_sessions(cs, list_panes=lambda: raw,
                                build_index=lambda: proc,
                                own_chain=lambda: {702})
    assert n == 1, "the block counted its own pane"
    # POSITIVE CONTROL: the same fixture WITHOUT the exclusion counts both, so
    # the 1 above is the exclusion working and not an unmatchable fixture.
    assert blk.count_live_sessions(cs, list_panes=lambda: raw,
                                   build_index=lambda: proc,
                                   own_chain=set) == 2


def test_count_zero_when_no_claude_panes():
    raw = "%1|16095|svc|2|@2|svc-b|/home/zach/ws/svc-b|zsh|1700000000|"
    n = blk.count_live_sessions(
        cs, list_panes=lambda: raw, build_index=lambda: _PROC,
        own_chain=lambda: set())
    assert n == 0


def test_count_two_live_claude_sessions():
    raw = "\n".join([
        "%0|16060|main|1|@1|a|/r1|claude|1700000000|⠐ one",
        "%1|16095|main|2|@2|b|/r2|claude|1700000000|✳ two",
    ])
    n = blk.count_live_sessions(
        cs, list_panes=lambda: raw, build_index=lambda: {},
        own_chain=lambda: set())
    assert n == 2          # detected via foreground command == 'claude'


def test_count_returns_NONE_not_zero_when_it_cannot_measure():
    """🔴 THE DISCRIMINANT, at its source.

    0 is a reading this block takes constantly — an idle machine. Returning it
    for "I could not tell" is what made an unloadable detector render byte-
    identically to a quiet one. Both failure paths (no module, a raising
    fetcher) must answer None, and None must NOT be confusable with 0.
    """
    assert blk.count_live_sessions(None) is None      # no detector module

    def boom():
        raise RuntimeError("tmux gone")

    got = blk.count_live_sessions(
        cs, list_panes=boom, build_index=lambda: {}, own_chain=lambda: set())
    assert got is None
    assert got != 0 and got is not False              # 0 is a real reading


def test_render_bare_glyph_at_zero():
    out = blk.render(0)
    assert out["state"] == "Idle"
    assert out["text"] == blk.GLYPH        # glyph only, no count
    assert " " not in out["text"]
    assert "?" not in out["text"]          # a MEASURED zero carries no `?`


def test_render_glyph_and_count_when_positive():
    out = blk.render(3)
    assert out["state"] == "Idle"          # neutral even with runs active
    assert out["text"] == "%s 3" % blk.GLYPH


@pytest.mark.parametrize("bad", [None, "x", object()])
def test_render_unmeasured_is_VISIBLY_different_from_zero(bad):
    """🔴 The pill's half of the discriminant. `?` is the bar-wide grammar for
    "this is not a current measurement" (claude/skills/bar). The assertion that
    matters is the INEQUALITY: before this change every one of these rendered
    the bare glyph, i.e. the same pixels as a machine with nothing running."""
    out = blk.render(bad)
    assert out["text"] == blk.UNMEASURED
    assert out["text"].endswith("?")
    assert out["state"] == "Idle"          # unmeasured is not an alarm
    assert out["text"] != blk.render(0)["text"]


def test_render_emits_valid_json():
    line = json.dumps(blk.render(2))
    parsed = json.loads(line)
    assert parsed["state"] == "Idle" and "2" in parsed["text"]


def test_detector_is_loaded_from_the_shared_module_by_explicit_path():
    """🔴 SINGLE SOURCE. This block once loaded the detector out of the
    `agent-ops` TUI; that TUI is gone and the detector lives in
    scripts/lib/claude_sessions.py. Pin that the block names THAT module, loads
    it by explicit path (no sys.path insert — scripts/lib/ holds unrelated
    modules that would shadow), and keeps no fallback copy of its own.
    """
    src = open(os.path.join(_SCRIPTS, "i3status-claude-runs"),
               encoding="utf-8").read()
    assert "claude_sessions.py" in src
    assert "SourceFileLoader" in src
    assert "agent-ops" not in src and "agent_ops" not in src

    # 🔴 STRUCTURAL, not spelled. Asserting the STRING "sys.path" is absent
    # failed on this module's own docstring, which explains why it does not
    # insert one — a guard a comment can trip is a guard a comment can also
    # satisfy. Walk the AST for an actual mutation of sys.path instead.
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in (
                "insert", "append", "extend"):
            continue
        tgt = fn.value
        assert not (isinstance(tgt, ast.Attribute) and tgt.attr == "path"
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "sys"), \
            "the block mutates sys.path — scripts/lib/ would shadow stdlib names"

    # every detector symbol the wrapper uses must come off the loaded module,
    # never be re-implemented here
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)}
    for sym in ("list_tmux_panes_raw", "build_proc_index", "own_pid_chain",
                "parse_panes", "classify_claude_sessions"):
        assert "cs.%s" % sym in src, sym
        assert sym not in defined, "%s is re-implemented in the block" % sym


def test_the_deployed_sibling_leg_is_first():
    """🔴 On a live host this script is a lone nix-store symlink in
    ~/.config/i3status-rust/scripts and the module is symlinked BESIDE it. That
    leg must be tried first, or a stale $DEVRC_DIR checkout silently wins over
    the copy home-manager just deployed."""
    paths = blk._MODULE_PATHS
    assert len(paths) == 3, paths
    assert paths[0].endswith(os.path.join("scripts", "claude_sessions.py")) or \
        os.path.basename(paths[0]) == "claude_sessions.py"
    assert os.path.dirname(paths[0]) == os.path.dirname(
        os.path.abspath(os.path.join(_SCRIPTS, "i3status-claude-runs")))
    # the in-checkout leg resolves without $DEVRC_DIR being set correctly
    assert paths[1].endswith(os.path.join("lib", "claude_sessions.py"))
    assert os.path.exists(paths[1]), paths[1]


# --------------------------------------------------------------------------- #
# 🔴 THE LAST-RESORT PILL — the one path in this file nothing reached.
#
# `main()` is defensive all the way down (load_detector and count_live_sessions
# each swallow Exception, render never raises), so the `except Exception` around
# `sys.exit(main())` is the handler for what nobody predicted. It was named
# `_BARE`, from when it really did render the bare glyph — i.e. the same pixels
# as "nothing is running", the exact lie the rest of this script exists to
# prevent. The value had already been fixed to carry `?`; the NAME still claimed
# otherwise, and MEASURED, reverting the value to `GLYPH` survived all 4,712
# tests. So both a behavioural and a structural guard, because they fail on
# different things.
# --------------------------------------------------------------------------- #
_MAIN_FAILS_DRIVER = r'''
import json, runpy, sys
_real = json.dumps
_calls = []
def _dumps(obj, *a, **k):
    _calls.append(obj)
    if len(_calls) == 1:            # the write inside main() — the unpredicted
        raise RuntimeError("boom inside main()")
    return _real(obj, *a, **k)      # the last-resort handler's own write
json.dumps = _dumps
runpy.run_path(sys.argv[1], run_name="__main__")
'''


def test_an_exception_that_escapes_main_STILL_emits_the_unmeasured_pill(tmp_path):
    """🔴 BEHAVIOURAL, through the real `__main__` guard.

    The failure is injected where a real one would land — the first
    `json.dumps`, i.e. main()'s own write — so the handler runs for a genuine
    unpredicted error rather than one the test hand-delivered to it.
    """
    driver = tmp_path / "drive_main_failure.py"
    driver.write_text(_MAIN_FAILS_DRIVER, encoding="utf-8")
    script = os.path.join(_SCRIPTS, "i3status-claude-runs")
    r = subprocess.run([sys.executable, "-B", str(driver), script],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    out = json.loads(r.stdout.strip())
    assert out["text"] == blk.UNMEASURED, out
    assert out["text"].endswith("?"), out
    assert out["text"] != blk.render(0)["text"], "a crash rendered as an idle machine"
    assert out["state"] == "Idle"

    # 🔴 POSITIVE CONTROL for the injection: without it, the SAME driver runs
    # the script's normal path and must NOT produce the `?` pill — otherwise
    # this test would pass on a script that always renders `?`.
    plain = tmp_path / "drive_plain.py"
    plain.write_text('import runpy, sys\n'
                     'runpy.run_path(sys.argv[1], run_name="__main__")\n',
                     encoding="utf-8")
    r2 = subprocess.run([sys.executable, "-B", str(plain), script],
                        capture_output=True, text=True, timeout=120)
    assert r2.returncode == 0, (r2.returncode, r2.stdout, r2.stderr)
    ok = json.loads(r2.stdout.strip())
    assert ok["text"] != blk.UNMEASURED, (
        "the un-injected run also rendered `?` — this host cannot load the "
        "detector, so the injected run proves nothing: %r" % ok)


def test_the_last_resort_pill_IS_the_unmeasured_pill_not_a_second_literal():
    """STRUCTURAL, and it fails on the thing the behavioural test cannot see: a
    fallback that is spelled out again by hand instead of derived. Two literals
    for one discriminant is how the value drifts back to a bare glyph while
    every other path stays correct."""
    assert blk._UNMEASURED_PILL == blk.render(None)
    assert blk._UNMEASURED_PILL["text"] == blk.UNMEASURED
    assert blk._UNMEASURED_PILL["text"] != blk.GLYPH, (
        "the last-resort pill is the bare glyph again — indistinguishable "
        "from a measured zero")
    # …and the handler must actually USE it. The name is checked in source
    # because the guard body only runs under __main__.
    src = open(os.path.join(_SCRIPTS, "i3status-claude-runs"),
               encoding="utf-8").read()
    tail = src.split('if __name__ == "__main__":')[1]
    assert "_UNMEASURED_PILL" in tail, tail
    # …and the stale name has not come back. Scoped to CODE lines: the comment
    # above the constant names `_BARE` while explaining why it is gone, and a
    # whole-file substring check would trip on the prose recording the fix —
    # the same spelled-guard trap this PR hit with `sys.path`.
    code = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    assert not [ln for ln in code if "_BARE" in ln], (
        "the stale name is back in code; it asserts the old lie")
