"""Unit tests for scripts/i3status-agent-ops — the LIVE agent-ops count block.

Exercises the pure `count_live_sessions` wrapper + the `render` formatter. The
wrapper delegates to agent-ops's real pure detector (parse_panes +
classify_claude_sessions), so it is driven here with INJECTED fixture fetchers
(a mock tmux-pane raw dump + a mock /proc index + an own-pid chain) — nothing
touches tmux or /proc. Mirrors test_agent_ops.py's fixtures + fail-safe stance.
"""
import importlib.machinery
import importlib.util
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.join(_HERE, "..")


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(
        modname, os.path.join(_SCRIPTS, name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


iao = _load("i3status-agent-ops", "i3status_agent_ops")
ao = _load("agent-ops", "agent_ops_for_count")


# A raw `tmux list-panes -a` dump matching agent-ops's pipe format:
#   pane_id|pane_pid|session|window_index|window_id|window_name|path|command|
#   server_start|title
# Pinned against the real PANE_FIELDS by
# test_the_fixture_dump_matches_agent_ops_current_pane_format — these lines are a
# hand-written copy of a format that lives in another file, and nothing else here
# reads `title`, so drift is otherwise silent.
_RAW_PANES = "\n".join([
    "%0|16060|main|1|@1|devrc ●|/home/zach/workspace/devrc|zsh|1700000000|⠐ Ship the block",
    "%1|16095|svc|2|@2|svc-b|/home/zach/ws/svc-b|zsh|1700000000|",  # plain zsh → not claude
    "%9|500|main|9|@9|self|/home/zach/workspace/devrc|python3|1700000000|agent-ops",  # own pane
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
    n = iao.count_live_sessions(
        ao,
        list_panes=lambda: _RAW_PANES,
        build_index=lambda: _PROC,
        own_chain=lambda: {999},
    )
    assert n == 1


def test_the_fixture_dump_matches_agent_ops_current_pane_format():
    """🔴 SEAM. These fixtures hand-copy a format defined in agent-ops, and every
    assertion in this file counts SESSIONS — none reads `title`. So a format
    change shifts every field one place left and the counts stay green.

    MEASURED: adding `#{start_time}` did exactly that. The 9-field lines still
    parsed, every pane_title landed in `server_start`, `title` went empty, and
    all eight tests here passed. Pin the field COUNT against the real
    PANE_FIELDS and pin the parse, so the next format change is loud here.
    """
    for line in _RAW_PANES.splitlines():
        assert len(line.split("|")) == len(ao.PANE_FIELDS), line
    panes = ao.parse_panes(_RAW_PANES)
    assert [p["title"] for p in panes] == ["⠐ Ship the block", "", "agent-ops"]
    assert [p["server_start"] for p in panes] == ["1700000000"] * 3
    assert [p["command"] for p in panes] == ["zsh", "zsh", "python3"]


def test_count_zero_when_no_claude_panes():
    raw = "%1|16095|svc|2|@2|svc-b|/home/zach/ws/svc-b|zsh|1700000000|"
    n = iao.count_live_sessions(
        ao, list_panes=lambda: raw, build_index=lambda: _PROC,
        own_chain=lambda: set())
    assert n == 0


def test_count_two_live_claude_sessions():
    raw = "\n".join([
        "%0|16060|main|1|@1|a|/r1|claude|1700000000|⠐ one",
        "%1|16095|main|2|@2|b|/r2|claude|1700000000|✳ two",
    ])
    n = iao.count_live_sessions(
        ao, list_panes=lambda: raw, build_index=lambda: {},
        own_chain=lambda: set())
    assert n == 2          # detected via foreground command == 'claude'


def test_count_failsafe_none_module_and_raising_fetchers():
    assert iao.count_live_sessions(None) == 0    # no agent-ops module → 0

    def boom():
        raise RuntimeError("tmux gone")

    # any fetcher raising degrades to 0, never propagates
    assert iao.count_live_sessions(
        ao, list_panes=boom, build_index=lambda: {},
        own_chain=lambda: set()) == 0


def test_render_bare_glyph_at_zero():
    out = iao.render(0)
    assert out["state"] == "Idle"
    assert out["text"] == iao.GLYPH        # glyph only, no count
    assert " " not in out["text"]


def test_render_glyph_and_count_when_positive():
    out = iao.render(3)
    assert out["state"] == "Idle"          # neutral even with runs active
    assert out["text"] == "%s 3" % iao.GLYPH


def test_render_failsafe_non_int():
    out = iao.render(None)
    assert out["text"] == iao.GLYPH and out["state"] == "Idle"


def test_render_emits_valid_json():
    line = json.dumps(iao.render(2))
    parsed = json.loads(line)
    assert parsed["state"] == "Idle" and "2" in parsed["text"]
