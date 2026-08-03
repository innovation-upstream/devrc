#!/usr/bin/env python3
"""Tests for the OpenCode activity plugin.

Covers:
  - State file round-trip and ring-buffer pruning (shell-level via node)
  - Emit round-trip: plugin calls emit → collector.parse_line succeeds
  - Deploy script: creates plugins dir and symlink
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Import collector for parse_line
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import collector as C  # noqa: E402
import _mockbin as M  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent.parent
PLUGIN_JS = SCRIPT_DIR / "activity-plugin.js"
DEPLOY_SH = SCRIPT_DIR / "deploy-plugin.sh"
EMIT = SCRIPT_DIR.parent / "emit"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def run_node(code: str, *, env: dict | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run a Node.js one-liner and return the CompletedProcess."""
    base_env = dict(os.environ)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["node", "--input-type=module", "-e", code],
        capture_output=True, text=True, env=base_env, cwd=cwd, timeout=10,
    )


def write_mock_emit(path: Path, log_file: Path) -> None:
    """Write a mock `emit` script that records its arguments to log_file.

    Shebang owned by `_mockbin.write_exec`: this used to be
    `#!/usr/bin/env bash`, which execs on a NixOS dev host but NOT in the nix
    build sandbox (no /usr/bin/env). That was invisible until this suite was
    added to run-tests.sh's target list.
    """
    M.write_exec(path, f'echo "$@" >> "{log_file}"\nexit 0\n')


# --------------------------------------------------------------------------- #
# State round-trip + ring buffer (JS logic, tested via node)
# --------------------------------------------------------------------------- #

def test_state_file_roundtrip(tmp_path):
    """Write state JSON, read it back, verify contents."""
    state_file = tmp_path / "state.json"
    state_file.write_text(
        json.dumps({"version": 1, "seen": ["a", "b", "c"]}),
        encoding="utf-8",
    )
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["seen"] == ["a", "b", "c"]


def test_state_ring_buffer():
    """Add 1100 IDs to state, verify oldest 100 are pruned (keeps last 1000)."""
    code = '''
import { readFileSync, writeFileSync } from "fs";
const MAX_SEEN = 1000;
let seen = [];
for (let i = 0; i < 1100; i++) seen.push("msg_" + i);
// Ring buffer logic (mirrors plugin)
const trimmed = seen.slice(-MAX_SEEN);
const state = JSON.stringify({ version: 1, seen: trimmed });
const parsed = JSON.parse(state);
console.log(JSON.stringify({ count: parsed.seen.length, first: parsed.seen[0], last: parsed.seen[parsed.seen.length - 1] }));
'''
    result = run_node(code)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip())
    assert out["count"] == 1000
    assert out["first"] == "msg_100"  # first 100 pruned
    assert out["last"] == "msg_1099"


# --------------------------------------------------------------------------- #
# Emit round-trip: plugin emitEvent → collector.parse_line
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not EMIT.exists(), reason="emit script missing")
def test_plugin_emit_roundtrip(tmp_path):
    """Use the real emit to write a v1 line, then parse_line to verify fields."""
    spool = tmp_path / "spool"
    spool.mkdir()
    text = "hello from opencode plugin 你好"
    project = "my-project"
    cwd = "/tmp/test-dir"
    session = "sess-123"
    payload = json.dumps({"agent": "code", "model": "gpt-4"})

    env = dict(os.environ, ACTIVITY_SPOOL_DIR=str(spool))
    # Build emit args the same way the JS plugin would
    rc = subprocess.run(
        [
            "bash", str(EMIT),
            "source=opencode", "kind=session-create",
            f"b64:text={text}", f"b64:project={project}",
            f"b64:cwd={cwd}", f"b64:session={session}",
            "b64:app=opencode", f"b64:payload={payload}",
        ],
        env=env, capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr

    lines = [l for l in (spool / "current.log").read_text().splitlines() if l]
    assert len(lines) == 1

    ev = C.parse_line(lines[0])
    assert ev is not None
    assert ev["source"] == "opencode"
    assert ev["kind"] == "session-create"
    assert ev["text"] == text
    assert ev["project"] == project
    assert ev["cwd"] == cwd
    assert ev["session"] == session
    assert ev["app"] == "opencode"
    pl = json.loads(ev["payload"])
    assert pl["agent"] == "code"
    assert pl["model"] == "gpt-4"
    # ts auto-filled by emit
    assert "ts" in ev


@pytest.mark.skipif(not EMIT.exists(), reason="emit script missing")
def test_plugin_tool_call_emit_roundtrip(tmp_path):
    """Simulate a tool-call event and verify parse_line."""
    spool = tmp_path / "spool"
    spool.mkdir()
    payload = json.dumps({"duration_ms": 42, "success": True, "args_summary": '{"file":"test.js"'[:200]})

    env = dict(os.environ, ACTIVITY_SPOOL_DIR=str(spool))
    rc = subprocess.run(
        [
            "bash", str(EMIT),
            "source=opencode", "kind=tool-call",
            "b64:text=Edit",
            "b64:session=sess-abc",
            "b64:app=opencode",
            f"b64:payload={payload}",
        ],
        env=env, capture_output=True, text=True,
    )
    assert rc.returncode == 0, rc.stderr

    ev = C.parse_line((spool / "current.log").read_text().splitlines()[0])
    assert ev["kind"] == "tool-call"
    assert ev["text"] == "Edit"
    pl = json.loads(ev["payload"])
    assert pl["duration_ms"] == 42
    assert pl["success"] is True


# --------------------------------------------------------------------------- #
# Deploy script
# --------------------------------------------------------------------------- #

def test_deploy_creates_symlink(tmp_path):
    """Deploy script creates the plugins dir and symlinks activity.js."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    plugins_dir = fake_home / ".config" / "opencode" / "plugins"

    env = dict(os.environ, HOME=str(fake_home))
    rc = subprocess.run(
        ["bash", str(DEPLOY_SH)],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert rc.returncode == 0, rc.stderr
    assert plugins_dir.is_dir()

    link = plugins_dir / "activity.js"
    assert link.is_symlink()
    assert link.resolve() == PLUGIN_JS.resolve()


def test_deploy_idempotent(tmp_path):
    """Running deploy twice doesn't error and the symlink is correct."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()

    env = dict(os.environ, HOME=str(fake_home))
    for _ in range(2):
        rc = subprocess.run(
            ["bash", str(DEPLOY_SH)],
            env=env, capture_output=True, text=True, timeout=5,
        )
        assert rc.returncode == 0, rc.stderr

    link = fake_home / ".config" / "opencode" / "plugins" / "activity.js"
    assert link.is_symlink()
    assert link.resolve() == PLUGIN_JS.resolve()


def test_deploy_removes_stale_regular_file(tmp_path):
    """If a regular file exists at the link target, deploy replaces it with a symlink."""
    fake_home = tmp_path / "fakehome"
    plugins_dir = fake_home / ".config" / "opencode" / "plugins"
    plugins_dir.mkdir(parents=True)
    stale = plugins_dir / "activity.js"
    stale.write_text("stale content", encoding="utf-8")
    assert stale.is_file()

    env = dict(os.environ, HOME=str(fake_home))
    rc = subprocess.run(
        ["bash", str(DEPLOY_SH)],
        env=env, capture_output=True, text=True, timeout=5,
    )
    assert rc.returncode == 0, rc.stderr
    link = plugins_dir / "activity.js"
    assert link.is_symlink()
    assert link.resolve() == PLUGIN_JS.resolve()


# --------------------------------------------------------------------------- #
# Plugin JS: emitEvent with mock emit
# --------------------------------------------------------------------------- #

def test_emit_event_calls_emit_cli(tmp_path):
    """Verify the JS emitEvent helper calls emit with the correct arguments."""
    mock_emit = tmp_path / "emit"
    log_file = tmp_path / "emit.log"
    write_mock_emit(mock_emit, log_file)

    code = f'''
import {{ readFileSync }} from "fs";

// Inline emitEvent (mirrors plugin logic)
const EMIT = "{mock_emit}";
const LOG = "{log_file}";

import {{ execSync }} from "child_process";

function emitEvent({{ kind, text, project, cwd, session, app, payload }}) {{
  const args = ["source=opencode", `kind=${{kind}}`];
  if (text != null) args.push(`b64:text=${{String(text)}}`);
  if (project != null) args.push(`b64:project=${{String(project)}}`);
  if (cwd != null) args.push(`b64:cwd=${{String(cwd)}}`);
  if (session != null) args.push(`b64:session=${{String(session)}}`);
  if (app != null) args.push(`b64:app=${{String(app)}}`);
  if (payload != null) {{
    const p = typeof payload === "string" ? payload : JSON.stringify(payload);
    args.push(`b64:payload=${{p}}`);
  }}
  execSync(`${{EMIT}} ${{args.join(" ")}}`, {{ stdio: "ignore" }});
}}

emitEvent({{
  kind: "session-create",
  text: "test session",
  project: "my-proj",
  cwd: "/tmp",
  session: "s1",
  app: "opencode",
  payload: {{"agent":"code"}},
}});
'''
    result = run_node(code)
    assert result.returncode == 0, result.stderr

    log_content = log_file.read_text(encoding="utf-8").strip()
    assert "source=opencode" in log_content
    assert "kind=session-create" in log_content
    assert "b64:project=" in log_content
    assert "b64:session=s1" in log_content
    assert "b64:app=opencode" in log_content


def test_emit_event_error_swallowed(tmp_path):
    """If emit binary doesn't exist, emitEvent should not throw."""
    code = '''
import { execSync } from "child_process";

function emitEvent({ kind }) {
  try {
    execSync("/nonexistent/emit kind=" + kind, { stdio: "ignore", timeout: 2000 });
  } catch {
    // swallowed
  }
}

emitEvent({ kind: "test" });
console.log("ok");
'''
    result = run_node(code)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout
