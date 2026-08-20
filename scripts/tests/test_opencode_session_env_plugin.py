#!/usr/bin/env python3
"""`scripts/opencode/plugin/session-env.js` — the hook that gives a nested
opencode run an identity of its own.

WHAT IT IS. A `shell.env` plugin hook that exports `OPENCODE_SESSION_ID` into
every bash-tool invocation. opencode builds the tool environment as
`{...process.env, ...pluginEnv}` — plugin wins — so the value a bash call sees
was written for THAT call by the session issuing it. `scripts/browser-bridge/
browser` reads it as tier 0 of `derive_session_id`, above the claude tiers,
because `CLAUDE_CODE_SESSION_ID` inside opencode may be an ancestor's.

WHY THE TESTS ARE SHAPED LIKE THIS. Three separate hazards, each with a real
incident behind it:

  1. THE LOADER CONTRACT. opencode iterates EVERY named export of a plugin
     module: a non-function export throws "Plugin export is not a function" and
     aborts the whole module, and a function export is CALLED as a plugin
     factory. PR #298 added `export const _internals = {...}` to the telemetry
     plugin and killed ALL opencode telemetry on both hosts for ~11 hours,
     silently. So: exactly one named export, and it must be a function.
  2. THE HOOK NAME. `session.created` / `message.updated` / `session.idle` are
     opencode BUS EVENT TYPES, not hook names; registering one as a hook key is
     a silent no-op that reads as live code. Three sat in activity-plugin.js for
     five days emitting zero rows.
  3. THE CRITICAL PATH. `shell.env` runs BEFORE every bash call opencode makes.
     A throw here does not lose an attribution, it breaks the tool. Every input
     shape the hook can be handed is exercised for "does not throw".

Plus the SEAM: the plugin WRITES a variable name and the browser CLI READS it,
in a different language, in a different directory, and nothing in either file
fails if they drift.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "scripts" / "opencode" / "plugin" / "session-env.js"
HOME_NIX = ROOT / "nix" / "home.nix"
BROWSER_CLI = ROOT / "scripts" / "browser-bridge" / "browser"

# The one variable this whole mechanism turns on. A LITERAL here on purpose: both
# real files are held against it, so neither can be renamed alone.
ENV_VAR = "OPENCODE_SESSION_ID"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="the plugin is JS and is RUN, not grepped")


def _node(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "--input-type=module", "-e", code],
                          capture_output=True, text=True, timeout=20)


def _run_hook(input_js: str, output_js: str = '{ env: {} }') -> dict:
    """Instantiate the plugin, call its `shell.env` hook with the given input and
    output objects, and return {ok, env, error} as JSON.

    The hook MUTATES `output.env` (it does not return a new object), so what is
    read back is the object we handed in — the same contract env.js relies on.
    """
    code = (
        f'const m = await import({json.dumps(PLUGIN.as_uri())});\n'
        'const hooks = await m.SessionEnvPlugin({ client: {}, project: {}, '
        'directory: "/tmp", worktree: "/tmp", serverUrl: "", $: {} });\n'
        f'const output = {output_js};\n'
        'let ok = true, error = "";\n'
        f'try {{ await hooks["shell.env"]({input_js}, output); }}\n'
        'catch (e) { ok = false; error = String(e); }\n'
        'console.log(JSON.stringify({ ok, error, '
        'env: (output && output.env) || null }));\n'
    )
    rc = _node(code)
    assert rc.returncode == 0, f"driver failed: {rc.stderr}"
    return json.loads(rc.stdout)


# --------------------------------------------------------------------------- #
# 1. The loader contract  (hazard 1)
# --------------------------------------------------------------------------- #

def test_exactly_one_named_export_and_it_is_a_function():
    """🔴 THE #298 SHAPE. A second export — of ANY kind — is called as a plugin
    factory or aborts the module. This is the file-level pin; the behavioural
    tests below cannot see it, because they import the one export by name.
    """
    code = (
        f'const m = await import({json.dumps(PLUGIN.as_uri())});\n'
        'console.log(JSON.stringify(Object.fromEntries('
        'Object.keys(m).map(k => [k, typeof m[k]]))));\n'
    )
    rc = _node(code)
    assert rc.returncode == 0, f"the module does not even import: {rc.stderr}"
    exports = json.loads(rc.stdout)
    assert list(exports) == ["SessionEnvPlugin"], exports
    assert exports["SessionEnvPlugin"] == "function", exports


# --------------------------------------------------------------------------- #
# 2. The registered hook keys  (hazard 2)
# --------------------------------------------------------------------------- #

# Registering any of these is a silent no-op — they are bus event types.
DEAD_HOOKS = ["session.created", "message.updated", "session.idle"]


def _hook_keys() -> list:
    code = (
        f'const m = await import({json.dumps(PLUGIN.as_uri())});\n'
        'const h = await m.SessionEnvPlugin({ client: {}, project: {}, '
        'directory: "/tmp", worktree: "/tmp", serverUrl: "", $: {} });\n'
        'console.log(JSON.stringify(Object.keys(h).sort()));\n'
    )
    rc = _node(code)
    assert rc.returncode == 0, f"plugin factory threw: {rc.stderr}"
    return json.loads(rc.stdout)


def test_it_registers_only_the_shell_env_hook():
    """One hook, and it is the real one. A LEDGER: it fails when the set grows as
    well as when it shrinks, because a second hook in this file would silently
    re-merge the failure domains the file exists to keep apart."""
    assert _hook_keys() == ["shell.env"]


def test_no_bus_event_type_is_registered_as_a_hook():
    registered = set(_hook_keys())
    assert not (registered & set(DEAD_HOOKS)), sorted(registered & set(DEAD_HOOKS))


# --------------------------------------------------------------------------- #
# 3. Behaviour
# --------------------------------------------------------------------------- #

def test_the_session_id_lands_in_the_bash_tool_environment():
    """THE POINT OF THE FILE. Mutated onto the object handed in, under the exact
    name the browser CLI reads."""
    res = _run_hook('{ sessionID: "ses_abc123", cwd: "/tmp", callID: "c1" }')
    assert res["ok"], res
    assert res["env"] == {ENV_VAR: "ses_abc123"}, res


def test_a_second_call_overwrites_a_parents_inherited_id():
    """NESTING. opencode merges `{...process.env, ...pluginEnv}` — plugin wins —
    so an opencode run inside another opencode run must end up credited to the
    INNER session. Modelled by pre-seeding the overlay with an ancestor's id;
    the two ids are distinct, so an implementation that skipped an already-set
    variable would be caught rather than looking correct."""
    res = _run_hook('{ sessionID: "ses_inner" }',
                    output_js='{ env: { ' + ENV_VAR + ': "ses_outer" } }')
    assert res["ok"], res
    assert res["env"][ENV_VAR] == "ses_inner", res


@pytest.mark.parametrize("inp,why", [
    ('{ cwd: "/tmp" }', "the PTY path fires with cwd only — no sessionID"),
    ('{}', "an empty input object"),
    ('{ sessionID: "" }', "an empty-string sessionID"),
    ('{ sessionID: null }', "an explicit null"),
    ('{ sessionID: 12345 }', "a non-string sessionID"),
    # 🔴 These two are what make the `input &&` guard REACHABLE. Without it,
    # reading `.sessionID` off undefined/null throws, the catch swallows it, and
    # the ancestor's id SURVIVES in the overlay — the one shape where "does not
    # throw" and "does not misattribute" are different claims.
    ("undefined", "no input object at all"),
    ("null", "a null input object"),
])
def test_an_unidentifiable_call_sets_the_variable_empty_not_stale(inp, why):
    """🔴 FAIL CLOSED, AND ACTIVELY. `output.env` is an OVERLAY, so "leave it
    alone" means whatever a parent process exported survives into the shell — a
    stale id, which is the misattribution this whole mechanism exists to prevent.
    Writing "" is what makes the consumer's `[ -n "${OPENCODE_SESSION_ID:-}" ]`
    test fall through to its documented fallback.

    Asserted with a PRE-SEEDED ancestor id, so a no-op implementation fails here
    instead of passing on an empty fixture: %s
    """ % why
    res = _run_hook(inp, output_js='{ env: { ' + ENV_VAR + ': "ses_ancestor" } }')
    assert res["ok"], res
    assert res["env"][ENV_VAR] == "", res


@pytest.mark.parametrize("inp,out", [
    ("undefined", "{ env: {} }"),
    ("null", "{ env: {} }"),
    ('{ sessionID: "ses_x" }', "undefined"),
    ('{ sessionID: "ses_x" }', "null"),
    ('{ sessionID: "ses_x" }', "{}"),
    ('{ sessionID: "ses_x" }', "{ env: null }"),
    ('{ get sessionID() { throw new Error("boom"); } }', "{ env: {} }"),
])
def test_the_hook_never_throws_whatever_it_is_handed(inp, out):
    """🔴 HAZARD 3. This runs in front of EVERY bash call opencode makes. A throw
    does not lose an attribution, it breaks the tool — which is why the body is
    wrapped, and why the wrapper is tested against shapes that would otherwise
    raise (a null output, a missing `env`, a property getter that throws).
    """
    res = _run_hook(inp, output_js=out)
    assert res["ok"], res


def test_the_hook_writes_nothing_else():
    """The env it contributes is exactly one variable. A `shell.env` hook can put
    anything into the bash tool's environment; this one has one job, and a second
    variable appearing here would be an unreviewed change to every command
    opencode runs."""
    res = _run_hook('{ sessionID: "ses_only" }')
    assert list(res["env"]) == [ENV_VAR], res


# --------------------------------------------------------------------------- #
# 4. The SEAM: the writer and the reader are in different files and languages
# --------------------------------------------------------------------------- #

def test_the_plugin_writes_the_variable_the_browser_cli_reads():
    """🔴 SEAM. The plugin sets a name; `derive_session_id` reads it. Nothing in
    either file fails if one is renamed — the tier simply never fires again and
    the `session` column silently re-empties, which is the exact failure mode the
    tier tags' two-way pin exists for.

    Both sides are PARSED, and each parse is asserted non-empty first: a regex
    that matched nothing would make this pass vacuously.
    """
    plugin_writes = set(re.findall(r"output\.env\.([A-Z_][A-Z0-9_]*)\s*=",
                                   PLUGIN.read_text()))
    assert plugin_writes, "the plugin-write parser matched nothing"
    assert plugin_writes == {ENV_VAR}, plugin_writes

    cli = BROWSER_CLI.read_text()
    cli_reads = set(re.findall(r"\$\{(OPENCODE_SESSION_ID|CLAUDE_CODE_SESSION_ID)"
                               r":-\}", cli))
    assert cli_reads, "the CLI-read parser matched nothing"
    assert ENV_VAR in cli_reads, cli_reads


def test_the_cli_reads_the_opencode_id_before_the_claude_ones():
    """🔴 THE ORDERING IS THE FIX, and it is a property of source ORDER that no
    unit test of either file alone can see. `CLAUDE_CODE_SESSION_ID` leaks into
    opencode's tool shells, so reading it first credits the ancestor.

    Structural, and deliberately so: the behavioural proof lives in
    test_browser_session_id.py (which runs the CLI in both environments). What is
    pinned here is that a future edit reordering the branches — a change that
    looks like tidying — cannot land silently.
    """
    src = BROWSER_CLI.read_text()
    oc = src.index('"${OPENCODE_SESSION_ID:-}"')
    claude = src.index('"${CLAUDE_CODE_SESSION_ID:-}"')
    assert oc < claude, (
        "derive_session_id must test OPENCODE_SESSION_ID before the claude "
        "variables — the claude value may be an inherited ancestor's")


# --------------------------------------------------------------------------- #
# 5. Deployment. A plugin that is not deployed is not a mechanism.
# --------------------------------------------------------------------------- #

def test_home_nix_deploys_the_plugin_into_the_singular_plugin_dir():
    """The glob is `{plugin,plugins}/*.{ts,js}` — NON-RECURSIVE, `.js`/`.ts` only.
    A dir symlink or a `.mjs` does not load, and a copy in BOTH `plugin/` and
    `plugins/` loads it twice."""
    nix = HOME_NIX.read_text()
    assert 'home.file.".config/opencode/plugin/session-env.js".source' in nix, nix[:0]
    assert "../scripts/opencode/plugin/session-env.js" in nix
    assert '.config/opencode/plugins/session-env.js' not in nix, (
        "a copy in the PLURAL dir would load the plugin twice")


def test_the_source_path_home_nix_claims_exists():
    """A `source = ../…` pointing at a missing file fails the SWITCH, not the
    tests — unless something asserts it here."""
    assert (ROOT / "nix" / ".." / "scripts" / "opencode" / "plugin" /
            "session-env.js").resolve().is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_plugin_is_tracked_by_git():
    """🔴 A NEW FILE THE FLAKE CANNOT SEE. home-manager builds from the git tree:
    an untracked `source = ../…` file makes the switch SUCCEED with the plugin
    simply absent, and the only symptom is a variable that is never set. The
    file existing on disk (the test above) is a different claim from the flake
    being able to read it.

    🔴 THE `.git` GUARD IS NOT OPTIONAL, and `shutil.which("git")` does not
    stand in for it. In the `checks.pytests` sandbox git IS on PATH
    (flake.nix nativeBuildInputs) but the source it runs against is a
    `/nix/store/…-source` copy with **no `.git` at all** — so
    `ls-files --error-unmatch` returns rc 128 ("not a git repository") and this
    test fails permanently, on a tree where the file is demonstrably present
    because the flake copied it. The assertion message would then be actively
    false and send a reader hunting an untracked file that is not untracked.
    It also cannot be skipped away: run-tests.sh's EXPECTED_SKIPS requires the
    skip total to EQUAL its one entry.

    So the two tiers get the check that means something in each, which is the
    same split scripts/tests/test_clawgate_predicate_single_source.py already
    uses: EXISTENCE (the test above) is the sandbox's evidence — the store copy
    is built from tracked files only, so an untracked file simply would not be
    there — and TRACKEDNESS is the dev host's, where the file exists whether or
    not git knows about it. Returning early rather than skipping keeps the skip
    ledger intact.
    """
    if not (ROOT / ".git").exists():
        return
    rc = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch",
                         "scripts/opencode/plugin/session-env.js"],
                        capture_output=True, text=True, timeout=30)
    assert rc.returncode == 0, (
        "scripts/opencode/plugin/session-env.js is not tracked by git — the "
        "flake will silently omit it from the deploy")


PLUGIN_REL = "scripts/opencode/plugin/session-env.js"


def _fake_tree(tmp_path, *, git: bool, tracked: bool) -> Path:
    """A minimal tree carrying the plugin at its real relative path."""
    root = tmp_path / "tree"
    (root / "scripts" / "opencode" / "plugin").mkdir(parents=True)
    (root / PLUGIN_REL).write_text("// stand-in\n")
    if git:
        subprocess.run(["git", "-C", str(root), "init", "-q"], check=True,
                       capture_output=True, timeout=30)
        if tracked:
            subprocess.run(["git", "-C", str(root), "add", PLUGIN_REL],
                           check=True, capture_output=True, timeout=30)
    return root


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("git,tracked,should_raise,why", [
    # 🔴 THE ROW THAT WOULD HAVE CAUGHT THE BUG. The `checks.pytests` sandbox
    # runs against a /nix/store/…-source copy with NO `.git`, while git IS on
    # PATH. The pre-fix guard (`skipif(which("git") is None)` only) therefore
    # RAN, got rc 128 "not a git repository", and turned the flake check
    # permanently red with a message claiming an untracked file.
    (False, False, False, "no .git at all — the nix sandbox's shape"),
    # The dev-host shape, both ways round. Present so the early return above
    # cannot quietly become an unconditional one: if the guard widened to
    # 'always return', this row goes green-when-it-should-fail and is caught.
    (True, True, False, "a real repo where the file is tracked"),
    (True, False, True, "a real repo where the file is NOT tracked"),
])
def test_the_tracked_by_git_guard_behaves_in_a_git_free_tree(
        tmp_path, monkeypatch, git, tracked, should_raise, why):
    """🔴 MUTATION-PROOF THE GUARD ITSELF, in the environment that broke it.

    The check above reads the module-level ROOT, so pointing ROOT at a
    fabricated tree exercises the real function body against each tier's actual
    shape — rather than asserting that it happens to pass in THIS checkout,
    which is the observation that missed the bug in the first place.

    Three rows, and the pair is the point: a guard that returned early
    unconditionally would satisfy the .git-free row and DIE on the untracked
    row, so neither direction can pass alone. Case: %s
    """ % why
    monkeypatch.setitem(globals(), "ROOT", _fake_tree(tmp_path, git=git,
                                                      tracked=tracked))
    if should_raise:
        with pytest.raises(AssertionError):
            test_the_plugin_is_tracked_by_git()
    else:
        test_the_plugin_is_tracked_by_git()
