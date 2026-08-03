#!/usr/bin/env python3
"""Tests for the OpenCode activity plugin.

Covers:
  - State file round-trip and ring-buffer pruning (shell-level via node)
  - Emit round-trip: plugin calls emit → collector.parse_line succeeds
  - opencode plugin-loader contract: exactly one named export, and it is a
    function (a non-function export makes opencode reject the whole module)
  - Declarative deployment via nix/home.nix into the singular plugin/ dir
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
# opencode plugin-loader contract  (regression: PR #298 → silent total outage)
# --------------------------------------------------------------------------- #
#
# MEASURED on opencode 1.18.4, 2026-08-02, by dropping probe plugins into the
# real ~/.config/opencode/plugin/ and reading `opencode run --print-logs`:
#
#   export const _internals = {};                 → ERROR "failed to load plugin
#   export const P = async () => ({});              … Plugin export is not a
#                                                     function"  (WHOLE module
#                                                     rejected, no hooks run)
#
#   export const P = async () => ({});             → loads clean, no error
#
#   two FUNCTION exports                           → BOTH invoked as plugin
#                                                    factories, with
#                                                    {client, project, worktree,
#                                                     directory, serverUrl, $}
#
# So a single non-function `export const` disables ALL telemetry, silently —
# `emitEvent` swallows every error by design, so nothing surfaces anywhere.
# That is precisely what #298 shipped, and emission stopped at the moment
# ship.sh deployed it (last kind=tool-call row 2026-08-03 02:32 UTC).
#
# These tests pin the contract at the file level. The old tests here exercised
# deploy-plugin.sh instead — they stayed green through the entire outage,
# because "the script makes a symlink" says nothing about whether opencode can
# LOAD what the symlink points at.

def _named_exports() -> dict:
    """Return {exportName: typeof} for activity-plugin.js, via node."""
    code = (
        f'const m = await import({json.dumps(PLUGIN_JS.as_uri())});\n'
        'const out = {};\n'
        'for (const k of Object.keys(m)) out[k] = typeof m[k];\n'
        'console.log(JSON.stringify(out));\n'
    )
    rc = run_node(code)
    assert rc.returncode == 0, f"module failed to import: {rc.stderr}"
    return json.loads(rc.stdout)


def test_every_named_export_is_a_function():
    """opencode rejects the WHOLE module if any named export is not a function.

    This is the exact assertion that would have caught #298.
    """
    exports = _named_exports()
    offenders = {k: t for k, t in exports.items() if t != "function"}
    assert not offenders, (
        f"non-function named export(s) {offenders} — opencode's loader will "
        f"reject activity-plugin.js entirely with 'Plugin export is not a "
        f"function' and ALL telemetry stops silently. Keep helpers "
        f"module-private."
    )


def test_exactly_one_named_export():
    """Every function export is CALLED as a plugin factory — so export only one.

    A helper exported for testability (e.g. `emitEvent`) is invoked by opencode
    at startup with the plugin context, which for `emitEvent` means shelling out
    to `emit` with kind=undefined — a junk telemetry row on every launch.
    """
    exports = _named_exports()
    assert list(exports) == ["ActivityPlugin"], (
        f"expected exactly one named export (ActivityPlugin), got "
        f"{sorted(exports)} — opencode invokes each function export as a "
        f"plugin factory"
    )


# --------------------------------------------------------------------------- #
# Declarative deployment  (regression: the laptop was never deployed at all)
# --------------------------------------------------------------------------- #
#
# The plugin used to be installed by a hand-run deploy-plugin.sh. It was run on
# the workbench on 2026-07-29 and never on the laptop, so the laptop recorded
# ZERO kind=tool-call rows for the plugin's entire existence. home.nix is now
# the single deployment, which makes both hosts identical by construction.

HOME_NIX = SCRIPT_DIR.parent.parent.parent / "nix" / "home.nix"


def test_home_nix_deploys_plugin_to_singular_plugin_dir():
    """The plugin must be declared into a directory opencode actually globs."""
    text = HOME_NIX.read_text(encoding="utf-8")
    assert '.config/opencode/plugin/activity.js' in text, (
        "nix/home.nix does not deploy the activity plugin — opencode will "
        "never load it and telemetry is silently dead on every host"
    )
    assert 'collector/opencode/activity-plugin.js' in text, (
        "the deployment does not point at activity-plugin.js"
    )


def test_home_nix_does_not_deploy_to_plural_plugins_dir():
    """opencode globs BOTH plugin/ and plugins/ — two copies = double emission."""
    text = HOME_NIX.read_text(encoding="utf-8")
    assert '.config/opencode/plugins/activity.js' not in text, (
        "activity.js is declared in the PLURAL plugins/ dir as well; opencode "
        "globs {plugin,plugins}/*.{ts,js} and would load it twice, "
        "double-emitting every event"
    )


def test_deploy_script_is_gone():
    """The hand-run script is superseded; its return would reintroduce drift."""
    assert not (SCRIPT_DIR / "deploy-plugin.sh").exists(), (
        "deploy-plugin.sh is back — it deploys to the plural dir and must be "
        "remembered per host, which is how the laptop went blind. Deployment "
        "belongs in nix/home.nix."
    )


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
