#!/usr/bin/env python3
"""Tests for the OpenCode ledger plugin — writer 2 of the agent activity ledger.

Mirrors `scripts/collector/opencode/tests/test_plugin.py`, which is the repo's
established way to test an opencode plugin: a Python test driving real `node`.
There is no node suite for `scripts/opencode/plugin/` and this does not add one.

WHAT THIS FILE IS FOR

  1. 🔴 THE SEAM, which is the whole risk here. The record shape lives in Python
     and the writer is JavaScript, so "the plugin works" and "the record is one
     `session-manager` can read" are different claims. The round-trip test makes
     both: node drives the hook, the CLI writes, and the record is read back
     through `agent_ledger.parse_ledger` — the same function the reader uses.

  2. 🔴 IT MUST NEVER THROW. opencode carries a plugin exception up, and a
     ledger write is not worth a failed tool call. Every failure mode — no
     module, no session id, a python that exits non-zero, a missing interpreter
     — is driven through real node and asserted silent.

  3. THE HOOK CHOICE. `tool.execute.after`, never `.before`: `.before` is where
     `guard.js` THROWS to block a command, and a second handler there that can
     fail is a way to break the guard's gate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# parents[3]: tests/ -> opencode/ -> scripts/ -> the repo root.
ROOT = Path(__file__).resolve().parents[3]
PLUGIN_JS = ROOT / "scripts" / "opencode" / "plugin" / "ledger.js"
MODULE_PY = ROOT / "scripts" / "lib" / "agent_ledger.py"
HOME_NIX = ROOT / "nix" / "home.nix"

sys.path.insert(0, str(ROOT / "scripts"))
from testlib import mockbin as M  # noqa: E402


def run_node(code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = dict(os.environ)
    base.pop("TMUX_PANE", None)
    base.update(env or {})
    return subprocess.run(["node", "--input-type=module", "-e", code],
                          capture_output=True, text=True, env=base, timeout=30)


def fire(session="oc-sess", env=None, tool="bash"):
    """Drive `tool.execute.after` exactly as opencode does."""
    code = (
        'import { LedgerPlugin } from %s;\n'
        'const p = await LedgerPlugin();\n'
        'await p["tool.execute.after"]('
        '  { tool: %s, sessionID: %s, callID: "c1" }, {});\n'
        'console.log("OK");\n'
    ) % (json.dumps(str(PLUGIN_JS)), json.dumps(tool), json.dumps(session))
    return run_node(code, env=env)


def ledger_env(tmp_path, **extra):
    e = {"DEVRC_LEDGER_MODULE": str(MODULE_PY),
         "HOME": str(tmp_path)}
    e.update(extra)
    return e


def read_back(tmp_path):
    """Read what the plugin wrote through the SHIPPING read path."""
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("_al_probe", str(MODULE_PY))
    spec = importlib.util.spec_from_file_location("_al_probe", str(MODULE_PY),
                                                  loader=loader)
    al = importlib.util.module_from_spec(spec)
    loader.exec_module(al)
    d = Path(tmp_path) / ".cache" / "agent-ledger"
    proc = subprocess.run(list(al.read_argv(abs_dir=str(d))),
                          capture_output=True, text=True, timeout=10)
    return al.parse_ledger(proc.stdout)


# =========================================================================== #
# the loader contract — opencode rejects a module that breaks it
# =========================================================================== #
def test_exactly_one_named_export_and_it_is_a_function():
    """opencode rejects the whole module if a named export is not a function,
    and a rejected module is a writer that silently never runs."""
    code = (
        'const m = await import(%s);\n'
        'const names = Object.keys(m);\n'
        'console.log(JSON.stringify({names, kinds: names.map(n => typeof m[n])}));\n'
    ) % json.dumps(str(PLUGIN_JS))
    proc = run_node(code)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["names"] == ["LedgerPlugin"]
    assert out["kinds"] == ["function"]


def test_it_registers_ONLY_tool_execute_after():
    """🔴 `.before` is where `guard.js` throws to BLOCK a command. A second
    handler on that event that can fail is a way to break the guard's gate, so
    this writer must not be on it. Both directions: `.after` present,
    `.before` absent."""
    code = (
        'import { LedgerPlugin } from %s;\n'
        'const p = await LedgerPlugin();\n'
        'console.log(JSON.stringify(Object.keys(p)));\n'
    ) % json.dumps(str(PLUGIN_JS))
    proc = run_node(code)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == ["tool.execute.after"]


# =========================================================================== #
# the round trip — node writes, the READER reads
# =========================================================================== #
def test_the_plugin_writes_a_record_the_READER_can_parse(tmp_path):
    """🔴 THE SEAM. The record shape is Python and the writer is JavaScript, so
    "the plugin ran" and "the record is one session-manager can read" are
    different claims. This makes both, through the shipping read path."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    M.write_exec(bindir / "tmux", "printf '@41|4025325\\n'\n")
    env = ledger_env(tmp_path, TMUX_PANE="%77",
                     PATH="%s:%s" % (bindir, os.environ["PATH"]))
    proc = fire(session="oc-roundtrip", env=env)
    assert proc.returncode == 0 and "OK" in proc.stdout, proc.stderr

    parsed = read_back(tmp_path)
    assert parsed["measured"] is True
    assert len(parsed["records"]) == 1
    rec = parsed["records"][0]
    assert rec["runtime"] == "opencode"
    assert rec["session_id"] == "oc-roundtrip"
    assert rec["window_id"] == "@41" and rec["pane_id"] == "%77"
    assert rec["tmux_pid"] == "4025325"


def test_the_record_is_namespaced_so_it_cannot_clobber_writer_1(tmp_path):
    """🔴 The guard built before this writer existed: a pane that ran Claude and
    later opencode holds ONE record each, not one overwriting the other."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    M.write_exec(bindir / "tmux", "printf '@41|4025325\\n'\n")
    env = ledger_env(tmp_path, TMUX_PANE="%77",
                     PATH="%s:%s" % (bindir, os.environ["PATH"]))
    fire(session="oc-ns", env=env)
    names = sorted(p.name for p in
                   (tmp_path / ".cache" / "agent-ledger").iterdir())
    assert names == ["opencode-p77.json"], names


# =========================================================================== #
# 🔴 fail-open — every path, through real node
# =========================================================================== #
@pytest.mark.parametrize("session", ["", None, 42])
def test_a_missing_or_non_string_session_writes_NOTHING_and_is_silent(
        session, tmp_path):
    """No session id means no ClickHouse join and no row identity — the hollow
    record `build_record` refuses. Skip, silently."""
    proc = fire(session=session, env=ledger_env(tmp_path))
    assert proc.returncode == 0 and proc.stderr == ""
    assert not (tmp_path / ".cache" / "agent-ledger").exists()


def test_an_ABSENT_module_is_survived_silently(tmp_path):
    """🔴 Unlike the guard — which refuses the command rather than run it
    unchecked — a missing ledger writer is not a safety question. The honest
    outcome is a row with no age, which the reader already renders as "no writer
    has recorded this window"."""
    env = ledger_env(tmp_path, DEVRC_LEDGER_MODULE=str(tmp_path / "nope.py"))
    proc = fire(env=env)
    assert proc.returncode == 0 and proc.stderr == ""


def test_a_FAILING_python_does_not_fail_the_tool_call(tmp_path):
    """The CLI exits non-zero on a bad record. opencode carries a plugin
    exception up, so that must not become a failed tool call."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    M.write_exec(bindir / "python3", "exit 9\n")
    env = ledger_env(tmp_path,
                     DEVRC_LEDGER_PYTHON=str(bindir / "python3"))
    proc = fire(env=env)
    assert proc.returncode == 0 and proc.stderr == ""


def test_a_MISSING_interpreter_does_not_fail_the_tool_call(tmp_path):
    env = ledger_env(tmp_path,
                     DEVRC_LEDGER_PYTHON="/nonexistent/python-that-is-not-here")
    proc = fire(env=env)
    assert proc.returncode == 0 and proc.stderr == ""


def test_the_kill_switch_writes_nothing(tmp_path):
    env = ledger_env(tmp_path, DEVRC_LEDGER_DISABLE="1", TMUX_PANE="%77")
    proc = fire(env=env)
    assert proc.returncode == 0
    assert not (tmp_path / ".cache" / "agent-ledger").exists()


# =========================================================================== #
# deployment — a writer that is not deployed writes nothing
# =========================================================================== #
def test_home_nix_deploys_it_to_the_SINGULAR_plugin_dir():
    """🔴 opencode's glob is `{plugin,plugins}/*.{ts,js}` and reads BOTH, so a
    file in each loads the plugin twice and writes twice. Singular only."""
    nix = HOME_NIX.read_text()
    assert '".config/opencode/plugin/ledger.js"' in nix
    assert '".config/opencode/plugins/ledger.js"' not in nix


def test_home_nix_deploys_the_MODULE_beside_it():
    """The plugin holds no schema — it shells out to `agent_ledger.py`, which
    must therefore be deployed where it looks first. Same arrangement guard.js
    has with guard_core.py."""
    nix = HOME_NIX.read_text()
    assert '".config/opencode/agent_ledger.py"' in nix
    assert "../scripts/lib/agent_ledger.py" in nix


def test_the_plugin_is_tracked_by_git():
    """🔴 A new file that is not `git add`ed is silently omitted from the flake:
    the switch succeeds and opencode simply never writes a record.

    🔴 ASSERTED IN BOTH TIERS AND NEVER SKIPPED, following the shape
    `test_handoff_doc.py::test_the_tool_is_tracked_by_git` already established.
    In the nix sandbox there is no `.git` at all, so the file's PRESENCE in the
    flake source is the evidence; on the dev host `git ls-files` is asked
    directly. The first draft ran `git` unconditionally and passed on the host
    while failing in the sandbox with `not a git repository` — a two-tier split
    this suite would have carried into CI had the target not been registered in
    the same change.
    """
    rel = "scripts/opencode/plugin/ledger.js"
    assert PLUGIN_JS.exists(), f"{PLUGIN_JS} is missing from this tree"
    if not (ROOT / ".git").exists():
        return
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", rel],
                         capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == rel, (
        f"{rel} is not tracked by git, so the flake will omit it and "
        "`home-manager switch` will succeed with opencode never writing a "
        "record.")


def test_the_plugin_carries_NO_record_schema_of_its_own():
    """🔴 The reason it spawns Python. A JavaScript re-implementation of the
    record is how writer 2 drifts from writer 1 and from the reader while all
    three look correct. Asserted structurally: the field names that make up a
    record must not appear in the JS."""
    js = PLUGIN_JS.read_text()
    for field in ("last_activity_ts", "tmux_pid", "window_id", "schema"):
        assert field not in js, field
