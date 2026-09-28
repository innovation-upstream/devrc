#!/usr/bin/env python3
"""`scripts/opencode/plugin/base-clone-freshness.js` — the plugin that keeps the
primary clone's context files fresh for opencode sessions.

REGRESSION COVERAGE vs INVARIANT GUARDS: every test in this file has a measured
red/green matrix against its base commit, stated inline.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "scripts" / "opencode" / "plugin" / "base-clone-freshness.js"
STALENESS_SH = ROOT / "scripts" / "claude-hooks" / "base-clone-staleness.sh"
HOME_NIX = ROOT / "nix" / "home.nix"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="the plugin is JS and is RUN, not grepped")


def _node(code: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "--input-type=module", "-e", code],
                          capture_output=True, text=True, timeout=20, env=env)


def _run_hook(input_js: str, output_js: str = '{ env: {} }',
              env: dict | None = None) -> dict:
    """Instantiate the plugin, call its `shell.env` hook with the given input and
    output objects, and return {ok, env, error} as JSON.

    The environment is built from scratch (no inherited DEVRC_* seams) unless
    the caller overrides.

    🔴 The built environment MUST actually reach the node driver. An earlier
    version of this harness built the `child` dict and then dropped it —
    `_node(code)` inherited the caller's env, so `HOME` was never redirected
    and the plugin resolved the script at the REAL home (absent pre-deploy);
    the spawn failed silently and the opt-out sentinel test passed VACUOUSLY
    ("no sentinel" — because no spawn ever happens at all). Caught by the
    cwd test, which asserts a sentinel EXISTS.
    """
    child = {k: v for k, v in __import__("os").environ.items()
             if not k.startswith("DEVRC_")}
    if env:
        child.update(env)

    code = (
        f'const m = await import({json.dumps(PLUGIN.as_uri())});\n'
        'const hooks = await m.BaseCloneFreshnessPlugin({ client: {}, project: {}, '
        'directory: "/tmp", worktree: "/tmp", serverUrl: "", $: {} });\n'
        f'const output = {output_js};\n'
        'let ok = true, error = "";\n'
        f'try {{ await hooks["shell.env"]({input_js}, output); }}\n'
        'catch (e) { ok = false; error = String(e); }\n'
        'console.log(JSON.stringify({ ok, error, '
        'env: (output && output.env) || null }));\n'
    )
    rc = _node(code, child)
    assert rc.returncode == 0, f"driver failed: {rc.stderr}"
    return json.loads(rc.stdout.strip())


# --------------------------------------------------------------------------- #
# 1. 🔴 THE LOADER CONTRACT — exactly one named function export.
#    RED on a plugin with two exports (like the #298 telemetry bug).
# --------------------------------------------------------------------------- #

def test_plugin_can_be_imported_and_invoked():
    """🔴 Import the plugin and call its factory. A module with no export, or
    with a non-function export, would throw here — which is the #298 shape."""
    code = (
        f'const m = await import({json.dumps(PLUGIN.as_uri())});\n'
        'const names = Object.keys(m).filter(k => k !== "default");\n'
        'console.log(JSON.stringify({ names, '
        'type: typeof m.BaseCloneFreshnessPlugin }));\n'
    )
    rc = _node(code)
    assert rc.returncode == 0, rc.stderr
    got = json.loads(rc.stdout.strip())
    assert got["names"] == ["BaseCloneFreshnessPlugin"], (
        f"expected exactly one named export 'BaseCloneFreshnessPlugin', "
        f"got {got['names']}")
    assert got["type"] == "function", (
        "BaseCloneFreshnessPlugin must be a factory function")


# --------------------------------------------------------------------------- #
# 2. 🔴 THE HOOK NAME — must register "shell.env", not a silent-noop key.
#    RED on a plugin whose hook key is a bus event type or a typo.
# --------------------------------------------------------------------------- #

def test_plugin_registers_shell_env():
    """🔴 Verify the hook object returned by the factory has a "shell.env" key
    that is a function. A typo like "shellEnv" would be a silent no-op."""
    code = (
        f'const m = await import({json.dumps(PLUGIN.as_uri())});\n'
        'const hooks = await m.BaseCloneFreshnessPlugin({});\n'
        'const keys = Object.keys(hooks);\n'
        'console.log(JSON.stringify({ keys, '
        'type: typeof hooks["shell.env"] }));\n'
    )
    rc = _node(code)
    assert rc.returncode == 0, rc.stderr
    got = json.loads(rc.stdout.strip())
    assert "shell.env" in got["keys"], (
        f"plugin must register 'shell.env', got keys: {got['keys']}")
    assert got["type"] == "function"


# --------------------------------------------------------------------------- #
# 3. 🔴 THE PLUGIN DOES NOT USE spawnSync — it uses spawn + unref.
#    RED on a plugin that blocks the bash tool on the pre-spawn critical path.
# --------------------------------------------------------------------------- #

def test_plugin_uses_non_sync_spawn():
    """The task spec requires `spawn` (async), not `spawnSync`. Pin that import
    in the source code.
    RED at base if the file used spawnSync.
    GREEN at HEAD only if the import says `spawn`."""
    src = PLUGIN.read_text()
    assert "spawnSync" not in src, (
        "base-clone-freshness.js must NOT use spawnSync — it sits in the bash "
        "tool's pre-spawn critical path and must never block")
    assert "spawn" in src, (
        "base-clone-freshness.js must import spawn from node:child_process")
    assert "import { spawn } from" in src, (
        "the async spawn import must be explicit, not a re-export")


# --------------------------------------------------------------------------- #
# 4. SANITY: the hook does not throw on valid or missing input.
#    NOT regression coverage (the loader-contract test covers the broken case);
#    these are invariant guards for the shell.env critical path.
# --------------------------------------------------------------------------- #

def test_hook_does_not_throw_on_valid_input():
    """A `shell.env` hook that throws breaks every bash call. The hook must
    tolerate a valid input with sessionID."""
    got = _run_hook('{ cwd: "/tmp", sessionID: "test-session-1", callID: "1" }')
    assert got["ok"] is True, f"hook threw on valid input: {got['error']}"


def test_hook_does_not_throw_on_pty_path_input():
    """The pty path fires this hook with `{cwd}` only — no sessionID. Must not
    throw."""
    got = _run_hook('{ cwd: "/tmp" }')
    assert got["ok"] is True, f"hook threw on pty input: {got['error']}"


def test_hook_does_not_throw_on_null_input():
    """Worst case: null input. Must not throw."""
    got = _run_hook("null")
    assert got["ok"] is True, f"hook threw on null input: {got['error']}"


# --------------------------------------------------------------------------- #
# 5. 🔴 HONOURS BASE_CLONE_NO_REFRESH — the environment variable opt-out.
#    RED on a plugin that spawns the script despite the opt-out.
#
#    🔴 The unref'd-spawn positive control is SOURCE-LEVEL, not sentinel-based:
#    the hook is explicitly fire-and-forget (detached + unref), so a sentinel
#    may never complete before the test exits. The opt-out test uses a sentinel
#    because NO spawn means the sentinel must never even be attempted.
# --------------------------------------------------------------------------- #

def test_base_clone_no_refresh_1_skips_spawning(tmp_path):
    """When BASE_CLONE_NO_REFRESH=1, the hook must not spawn the script — even
    with a valid sessionID. We detect this by placing a sentinel script at the
    deployed path that would WRITE a marker file if spawned; the marker must
    NOT exist after the hook runs."""
    sentinel = tmp_path / "sentinel-fired"
    fake_script = tmp_path / ".config" / "opencode" / "base-clone-staleness.sh"
    fake_script.parent.mkdir(parents=True)
    fake_script.write_text(
        "#!/usr/bin/env bash\n"
        f"touch {sentinel}\n"
    )
    fake_script.chmod(0o755)

    env = {"HOME": str(tmp_path), "BASE_CLONE_NO_REFRESH": "1"}
    got = _run_hook(
        '{ cwd: "/tmp", sessionID: "test-no-refresh", callID: "1" }',
        env=env,
    )
    assert got["ok"] is True, f"hook threw: {got['error']}"
    assert not sentinel.exists(), (
        "BASE_CLONE_NO_REFRESH=1 was set, but the script still spawned")


def test_base_clone_no_refresh_not_set_spawns():
    """Positive control: structurally verify that, without BASE_CLONE_NO_REFRESH,
    the plugin calls spawn + unref. The source must contain both calls since the
    hook is explicitly fire-and-forget (detached+unref)."""
    src = PLUGIN.read_text()
    assert "spawn(" in src, "plugin must call spawn() to run the script"
    assert ".unref()" in src, (
        "plugin must call .unref() on the child — it is fire-and-forget")
    assert 'BASE_CLONE_NO_REFRESH' in src
    # 🔴 Async spawn failures surface as an 'error' EVENT, which the hook's
    # try/catch cannot catch; with no listener Node raises it as an uncaught
    # exception and the whole opencode process dies in the bash pre-spawn
    # path. The no-op listener is the fix — pin it so it cannot be removed
    # as "dead code".
    assert 'child.on("error"' in src, (
        "the spawn child MUST have an 'error' listener — without one, a "
        "spawn failure (ENOENT bash) is an uncaught exception that crashes "
        "opencode's bash tool")


def test_spawn_passes_the_session_cwd_to_the_child(tmp_path):
    """🔴 The staleness script resolves its target repo from its OWN cwd
    (`git rev-parse --show-toplevel`), so a child spawned without the `cwd`
    option would inspect the directory opencode was LAUNCHED from, not the
    repo the session is working in. The hook must forward `input.cwd`.

    Sentinel: a fake script that records ITS cwd; the hook is called with a
    distinctive session cwd and the recorded value must equal it. RED against
    a plugin that spawns without `cwd: input.cwd` (the child would record the
    node driver's cwd instead)."""
    import os
    import time

    session_dir = tmp_path / "session-repo"
    session_dir.mkdir()
    recorded = tmp_path / "recorded-cwd"
    fake_script = tmp_path / ".config" / "opencode" / "base-clone-staleness.sh"
    fake_script.parent.mkdir(parents=True)
    fake_script.write_text(
        "#!/usr/bin/env bash\n"
        f"pwd > {recorded}\n"
    )
    fake_script.chmod(0o755)

    env = {"HOME": str(tmp_path), "BASE_CLONE_NO_REFRESH": ""}
    got = _run_hook(
        '{ cwd: ' + json.dumps(str(session_dir)) +
        ', sessionID: "test-cwd-passed", callID: "1" }',
        env=env,
    )
    assert got["ok"] is True, f"hook threw: {got['error']}"
    deadline = time.monotonic() + 10
    while not recorded.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert recorded.exists(), "the sentinel script never ran"
    # Resolve both sides: the session dir may be a symlinked temp path.
    assert recorded.read_text().strip() == os.path.realpath(session_dir), (
        "the child ran in the wrong cwd — input.cwd was not forwarded")


# --------------------------------------------------------------------------- #
# 6. Deployment pins (nix wiring)
# --------------------------------------------------------------------------- #

def test_home_nix_deploys_the_plugin_into_the_singular_plugin_dir():
    """The glob is `{plugin,plugins}/*.{ts,js}` — NON-RECURSIVE, `.js`/`.ts`
    only. A copy in both `plugin/` and `plugins/` loads it twice."""
    nix = HOME_NIX.read_text()
    assert (
        'home.file.".config/opencode/plugin/base-clone-freshness.js".source'
        in nix
    ), "nix/home.nix must deploy the plugin to plugin/base-clone-freshness.js"
    assert "../scripts/opencode/plugin/base-clone-freshness.js" in nix
    assert '.config/opencode/plugins/base-clone-freshness.js' not in nix, (
        "a copy in the PLURAL dir would load the plugin twice")


def test_home_nix_deploys_the_staleness_script():
    """The staleness script must be deployed to ~/.config/opencode/ so the
    plugin can find it at $HOME/.config/opencode/base-clone-staleness.sh."""
    nix = HOME_NIX.read_text()
    assert (
        'home.file.".config/opencode/base-clone-staleness.sh".source'
        in nix
    ), "nix/home.nix must deploy the staleness script"
    assert "../scripts/claude-hooks/base-clone-staleness.sh" in nix


def test_the_source_path_home_nix_claims_exists():
    """A `source = ../…` pointing at a missing file fails the SWITCH, not the
    tests — unless something asserts it here."""
    assert (ROOT / "nix" / ".." / "scripts" / "opencode" / "plugin" /
            "base-clone-freshness.js").resolve().is_file()
    assert STALENESS_SH.is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_plugin_is_tracked_by_git():
    """🔴 A NEW FILE THE FLAKE CANNOT SEE. Same reasoning as the
    session-env-plugin test: home-manager builds from the git tree, so an
    untracked file makes the switch succeed with the plugin simply absent.
    """
    if not (ROOT / ".git").exists():
        return
    rc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--error-unmatch",
         "scripts/opencode/plugin/base-clone-freshness.js"],
        capture_output=True, text=True, timeout=30,
    )
    assert rc.returncode == 0, (
        "scripts/opencode/plugin/base-clone-freshness.js is not tracked by "
        "git — the flake will silently omit it from the deploy")


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_staleness_script_is_tracked_by_git():
    """Same as above but for the staleness script (which exists but must be
    confirmed tracked)."""
    if not (ROOT / ".git").exists():
        return
    rc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--error-unmatch",
         "scripts/claude-hooks/base-clone-staleness.sh"],
        capture_output=True, text=True, timeout=30,
    )
    assert rc.returncode == 0, (
        "scripts/claude-hooks/base-clone-staleness.sh is not tracked by git")


def test_no_plugin_copy_under_plural_plugins_dir():
    """🔴 The glob reads BOTH `plugin/` and `plugins/`, so a file in each loads
    the plugin TWICE. Verify home.nix has no plural-dir copy of this plugin.
    Also verify no hand-placed copy exists in the repo."""
    nix = HOME_NIX.read_text()
    assert '.config/opencode/plugins/base-clone-freshness.js' not in nix, (
        "a copy in the PLURAL dir would load the plugin twice")
    # Also check no file lives at the plural path in the source tree
    plural = ROOT / "scripts" / "opencode" / "plugins" / "base-clone-freshness.js"
    assert not plural.exists(), (
        f"unexpected file at {plural} — would be loaded as a second copy")


def test_plugin_uses_homedir_not_import_meta_url():
    """🔴 The plugin must resolve the staleness script from $HOME, not from its
    own module location — same hazard guard.js fixed: `import.meta.url` resolves
    through the store symlink to a flat path, so `../` would be wrong."""
    src = PLUGIN.read_text()
    assert "homedir()" in src, (
        "base-clone-freshness.js must resolve the script from $HOME, "
        "independently of where the module itself sits")
    assert ".config" in src and "opencode" in src
    assert "base-clone-staleness.sh" in src