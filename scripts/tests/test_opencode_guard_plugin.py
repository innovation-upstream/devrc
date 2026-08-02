r"""Tests for opencode's guard plugin AS DEPLOYED — executed, not read.

🔴 WHY THIS FILE EXISTS — the bug that shipped, and why the suite was green.

`scripts/opencode/plugin/guard.js` located its python core with

    fileURLToPath(new URL("../guard_core.py", import.meta.url))

reasoning that it lives at ~/.config/opencode/plugin/guard.js, so `..` is
~/.config/opencode/ — exactly where nix/home.nix links the core. That reasoning
is about the DEPLOY path, and the deploy path is a SYMLINK. home-manager's
`home.file` links the plugin into the nix store; node resolves `import.meta.url`
THROUGH the symlink to the real store path; and the store is FLAT — the file
lands as a single `/nix/store/<hash>-hm_guard.js` with no `plugin/` directory
above it. MEASURED on the live host, 2026-08-02:

    readlink -f ~/.config/opencode/plugin/guard.js
      -> /nix/store/5m6y63cj512ksn783j5nddlrchkca92p-hm_guard.js
    import.meta.url dir : /nix/store
    ../guard_core.py    -> /nix/guard_core.py          ← does not exist

The guard FAILS CLOSED, so this was not a silent hole — it was a total outage:
every bash call in opencode was refused with

    bash guard failed (status=2, stderr=python3: can't open file
    '/nix/guard_core.py': [Errno 2] No such file or directory).

🔴 AND THE SUITE WAS GREEN. test_opencode_config.py asserted, verbatim:

    assert 'new URL("../guard_core.py", import.meta.url)' in GUARD_PLUGIN.read_text()

i.e. it pinned the BROKEN LINE as if it were the contract, and its docstring
restated the (wrong) `~/.config/opencode/plugin/` reasoning as justification. The
rest of the coverage was the same shape: substring checks over guard.js's text,
existence checks on home.nix `source =` paths, and python tests that imported
`guard_core` DIRECTLY. Every one of them passes with the plugin 100% inert.
Nothing anywhere ever LOADED guard.js and called its hook.

That is the finding, and it is what this file fixes. These tests build the
store-symlink layout on disk and run the plugin through its real entry point
(scripts/tests/fixtures/guard_plugin_driver.mjs), so a path that does not resolve
in the deployed shape fails here.

🔴 RED/GREEN MATRIX — measured, so this file carries its own scope.
Run at origin/main (88eb6d0, pre-fix): **5 failed, 15 passed**. The five REGRESSION
tests, red at base and green at HEAD, are:

    test_plugin_finds_its_core_in_the_deployed_store_symlink_layout
    test_plugin_still_blocks_an_irreversible_command_in_the_deployed_layout
    test_plugin_refuses_when_the_core_cannot_be_found_anywhere
    test_the_home_candidate_beats_the_module_relative_one
    test_guard_js_does_not_rely_solely_on_its_own_module_url

Everything else in this file PASSED at base and is therefore an INVARIANT GUARD,
not regression coverage — it pins behaviour the bug never violated (the harness
controls, the python-missing limb, the override seam, the repo-checkout layout,
the non-bash passthrough). Counted honestly, this PR adds 5 regression tests and
15 guards.

MUTATION SWEEP (5/5 killed, each by its NAMED test; control green before and
after, and the restore re-verified):
    M1 drop the $HOME candidate            -> ..._deployed_store_symlink_layout
    M2 skip the existsSync check           -> ..._plain_repo_checkout_layout_still_works
    M3 module-relative first (the bug)     -> ..._home_candidate_beats_the_module_relative_one
    M4 DEVRC_GUARD_CORE as hint not override -> ..._override_is_used_verbatim...
    M5 FAIL OPEN when no core is found     -> ..._refuses_when_the_core_cannot_be_found_anywhere

    run:  python -m pytest scripts/tests/test_opencode_guard_plugin.py -q

`node` and `python3` are hard REQUIRED_TOOLS in scripts/run-tests.sh, so their
absence is a runner FATAL rather than a skip here — a skip in this file would
restore exactly the vacuous green it exists to end.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD_JS = ROOT / "scripts" / "opencode" / "plugin" / "guard.js"
GUARD_CORE = ROOT / "scripts" / "claude-hooks" / "guard_core.py"
DRIVER = Path(__file__).resolve().parent / "fixtures" / "guard_plugin_driver.mjs"

# A plausible store basename. The two things that matter are that it is FLAT
# (no `plugin/` parent) and that the deployed name is `hm_<basename>`, which is
# what home-manager produces.
STORE_GUARD = "5m6y63cj512ksn783j5nddlrchkca92p-hm_guard.js"
STORE_CORE = "y1g22dzhwkqcmqx89cb6j79pkr92961v-hm_guard_core.py"

# Representative commands. `mkswap /dev/…` is deliberately one of the GLOB BLIND
# SPOTS listed in test_opencode_config.py — a command the glob layer never
# caught, so only the guard can refuse it. `ls -la` is on that file's MUST_ALLOW
# list.
BLOCKED_COMMAND = "mkswap /dev/zzz-nonexistent-device"
BENIGN_COMMAND = "ls -la"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def run_plugin(plugin_path: Path, command: str, *, home: Path, env: dict | None = None):
    """Load `plugin_path` in node and invoke its `tool.execute.before` hook.

    Returns the driver's decoded JSON: {"outcome": "allow"|"throw"|…}.

    The environment is built from scratch rather than inherited: every
    DEVRC_GUARD_* seam is stripped unless the caller asks for it, so a variable
    set in the developer's shell cannot decide a test's outcome.
    """
    child = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("DEVRC_GUARD_") and k != "HOME"
    }
    child["HOME"] = str(home)
    if env:
        child.update(env)

    proc = subprocess.run(
        ["node", str(DRIVER), str(plugin_path), command],
        capture_output=True,
        text=True,
        timeout=120,
        env=child,
    )
    assert proc.returncode == 0, (
        f"the DRIVER itself failed (rc={proc.returncode}). That is a harness "
        f"fault, not a guard verdict.\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip(), f"driver produced no stdout; stderr: {proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def build_deployed_layout(tmp_path: Path, *, with_core: bool = True) -> tuple[Path, Path]:
    """Reproduce the home-manager deployment on disk.

        <tmp>/store/<hash>-hm_guard.js            (FLAT regular file)
        <tmp>/store/<hash>-hm_guard_core.py       (FLAT regular file)
        <tmp>/home/.config/opencode/plugin/guard.js  -> the store guard
        <tmp>/home/.config/opencode/guard_core.py    -> the store core

    Returns (home, plugin_symlink). The caller imports the SYMLINK, which is
    what opencode does, so node performs the same resolution it performs live.
    """
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    home = tmp_path / "home"
    cfg = home / ".config" / "opencode"
    (cfg / "plugin").mkdir(parents=True, exist_ok=True)

    store_guard = store / STORE_GUARD
    shutil.copy2(GUARD_JS, store_guard)
    link = cfg / "plugin" / "guard.js"
    link.symlink_to(store_guard)

    if with_core:
        store_core = store / STORE_CORE
        shutil.copy2(GUARD_CORE, store_core)
        (cfg / "guard_core.py").symlink_to(store_core)

    return home, link


def build_repo_layout(tmp_path: Path) -> tuple[Path, Path]:
    """A plain (non-home-manager) checkout: the two files in their repo shape.

        <tmp>/checkout/plugin/guard.js
        <tmp>/checkout/guard_core.py

    Returns (empty_home, plugin_path).
    """
    checkout = tmp_path / "checkout"
    (checkout / "plugin").mkdir(parents=True, exist_ok=True)
    plugin = checkout / "plugin" / "guard.js"
    shutil.copy2(GUARD_JS, plugin)
    shutil.copy2(GUARD_CORE, checkout / "guard_core.py")

    home = tmp_path / "empty-home"
    home.mkdir(exist_ok=True)
    return home, plugin


# --------------------------------------------------------------------------- #
# 🔴 harness self-validation (negative controls)
#
# A harness that reports green while testing nothing is worse than no test.
# Before reading any verdict below, prove that this layout actually reproduces
# the mechanism, and that the driver can actually report a failure.
# --------------------------------------------------------------------------- #
def test_the_layout_really_resolves_import_meta_url_into_the_flat_store(tmp_path):
    """🔴 THE load-bearing harness control.

    If node did NOT resolve the symlink — or if the store dir were not flat —
    the "deployed layout" below would be a fiction and every test in this file
    would be green for the wrong reason.

    A standalone probe (NOT guard.js, so this control is independent of the code
    under test) is placed in the store dir and imported via a symlink from the
    plugin dir. It must report that `../guard_core.py` off its own module URL
    lands at <tmp>/guard_core.py — i.e. one level ABOVE the store, missing the
    real core entirely. That is the bug, reproduced.
    """
    store = tmp_path / "store"
    store.mkdir()
    probe = store / "deadbeef-hm_probe.mjs"
    probe.write_text(
        'import { fileURLToPath } from "node:url";\n'
        'console.log(JSON.stringify({\n'
        '  self: fileURLToPath(import.meta.url),\n'
        '  parentRelative: fileURLToPath(new URL("../guard_core.py", import.meta.url)),\n'
        "}));\n"
    )
    plugin_dir = tmp_path / "home" / ".config" / "opencode" / "plugin"
    plugin_dir.mkdir(parents=True)
    real_core = tmp_path / "home" / ".config" / "opencode" / "guard_core.py"
    real_core.write_text("# the core lives here\n")
    link = plugin_dir / "probe.mjs"
    link.symlink_to(probe)

    proc = subprocess.run(
        ["node", str(link)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip())

    assert got["self"] == str(probe), (
        f"node did NOT resolve the symlink — import.meta.url is {got['self']!r}, "
        f"expected the store path {str(probe)!r}. Without that resolution this "
        f"whole file tests a layout that does not occur in production."
    )
    assert got["parentRelative"] == str(tmp_path / "guard_core.py"), (
        f"`../guard_core.py` resolved to {got['parentRelative']!r}; the layout is "
        f"not reproducing the flat-store shape"
    )
    assert got["parentRelative"] != str(real_core), (
        "the module-relative path found the real core — the bug is NOT reproduced"
    )
    assert not Path(got["parentRelative"]).exists()


def test_the_driver_reports_a_throw_rather_than_swallowing_it(tmp_path):
    """Negative control for the driver: a hook that throws must come back as
    `outcome: throw` with the message, not as a silent allow and not as a
    non-zero exit. Without this, a broken driver would report every command
    allowed and the block assertions below would be untestable."""
    fake = tmp_path / "throwing-plugin.js"
    fake.write_text(
        "export const GuardPlugin = async () => ({\n"
        '  "tool.execute.before": async () => { throw new Error("SENTINEL-BOOM"); },\n'
        "});\n"
    )
    got = run_plugin(fake, "anything", home=tmp_path)
    assert got["outcome"] == "throw"
    assert "SENTINEL-BOOM" in got["message"]


def test_the_driver_reports_an_allow_when_the_hook_returns(tmp_path):
    """The other half of the control: the driver must be able to say `allow`.
    A driver that reported `throw` unconditionally would make every block
    assertion below pass vacuously."""
    fake = tmp_path / "permissive-plugin.js"
    fake.write_text(
        "export const GuardPlugin = async () => ({\n"
        '  "tool.execute.before": async () => {},\n'
        "});\n"
    )
    assert run_plugin(fake, "anything", home=tmp_path)["outcome"] == "allow"


def test_the_real_core_is_the_file_home_nix_deploys():
    """The copy this file makes must be the same source home.nix links to both
    ~/.claude/hooks/ and ~/.config/opencode/. If it drifts, these tests exercise
    a core nobody deploys."""
    nix = (ROOT / "nix" / "home.nix").read_text()
    assert nix.count("../scripts/claude-hooks/guard_core.py") == 2
    assert GUARD_CORE.is_file()
    assert DRIVER.is_file()


# --------------------------------------------------------------------------- #
# 1. 🔴 THE REGRESSION TEST — the deployed store-symlink layout
#
# RED on origin/main (88eb6d0): the plugin resolved /nix-style `../guard_core.py`
# off the flat store dir, found nothing, and threw
# "bash guard failed (status=2, stderr=python3: can't open file …)".
# --------------------------------------------------------------------------- #
def test_plugin_finds_its_core_in_the_deployed_store_symlink_layout(tmp_path):
    """🔴 A benign command must RUN when the plugin is deployed as a store symlink.

    This is the live breakage, verbatim: on origin/main every bash call in
    opencode was refused because the core could not be found. The assertion is
    on the OUTCOME (`allow`), so it cannot be satisfied by the plugin throwing
    for a different reason.
    """
    home, plugin = build_deployed_layout(tmp_path)
    got = run_plugin(plugin, BENIGN_COMMAND, home=home)
    assert got["outcome"] == "allow", (
        f"a benign command was refused in the DEPLOYED layout: {got}. The plugin "
        f"cannot locate guard_core.py when it is a store symlink — every bash "
        f"call in opencode is blocked."
    )


def test_plugin_still_blocks_an_irreversible_command_in_the_deployed_layout(tmp_path):
    """The guard must still DENY, and deny for its OWN reason.

    🔴 Asserting merely "it threw" would be green on origin/main too — the broken
    build threw on every command, including this one. The discriminating claim is
    WHICH error: "BLOCKED by the devrc bash guard" (a verdict) versus "bash guard
    failed"/"cannot find guard_core.py" (an outage). So the message is pinned,
    and the outage strings are asserted ABSENT.
    """
    home, plugin = build_deployed_layout(tmp_path)
    got = run_plugin(plugin, BLOCKED_COMMAND, home=home)
    assert got["outcome"] == "throw", f"{BLOCKED_COMMAND!r} was not blocked: {got}"
    assert "BLOCKED by the devrc bash guard" in got["message"], (
        f"blocked, but not by a guard VERDICT: {got['message']!r}"
    )
    for outage in ("cannot find guard_core.py", "bash guard failed", "could not run"):
        assert outage not in got["message"], (
            f"the throw is an OUTAGE, not a verdict: {got['message']!r}"
        )


def test_plugin_leaves_non_bash_tools_alone_in_the_deployed_layout(tmp_path):
    """A deployed-layout pin that the hook does not throw for tools it does not
    guard — a core-resolution error raised unconditionally would break `edit`,
    `read` and `write` too."""
    home, plugin = build_deployed_layout(tmp_path)
    child = {k: v for k, v in os.environ.items() if not k.startswith("DEVRC_GUARD_")}
    child["HOME"] = str(home)
    probe = tmp_path / "non-bash-driver.mjs"
    probe.write_text(
        'const { pathToFileURL } = await import("node:url");\n'
        "const mod = await import(pathToFileURL(process.argv[2]).href);\n"
        "const hook = (await mod.GuardPlugin({}))['tool.execute.before'];\n"
        "try { await hook({ tool: 'read' }, { args: { command: 'mkswap /dev/x' } });\n"
        '  console.log("allow"); } catch (e) { console.log("throw:" + e.message); }\n'
    )
    proc = subprocess.run(
        ["node", str(probe), str(plugin)],
        capture_output=True, text=True, timeout=60, env=child,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "allow", proc.stdout


# --------------------------------------------------------------------------- #
# 2. fail-closed
# --------------------------------------------------------------------------- #
def test_plugin_refuses_when_the_core_cannot_be_found_anywhere(tmp_path):
    """🔴 FAIL CLOSED, and say which paths were tried.

    Deployed layout with the core NOT deployed. There is no candidate anywhere,
    so the command must be REFUSED — never allowed through unchecked — and the
    message must name the path it looked at, or the operator has an outage with
    no lead.
    """
    home, plugin = build_deployed_layout(tmp_path, with_core=False)
    got = run_plugin(plugin, BENIGN_COMMAND, home=home)
    assert got["outcome"] == "throw", (
        f"the core is ABSENT and the command was allowed through unchecked: {got}. "
        f"A guard that degrades to allow reports safety it is not providing."
    )
    assert "cannot find guard_core.py" in got["message"]
    expected = str(home / ".config" / "opencode" / "guard_core.py")
    assert expected in got["message"], (
        f"the refusal does not name the path it tried. Wanted {expected!r} in "
        f"{got['message']!r}"
    )


def test_plugin_refuses_a_destructive_command_when_the_core_is_absent(tmp_path):
    """The dangerous direction of the same case: no core must never mean the
    irreversible command runs."""
    home, plugin = build_deployed_layout(tmp_path, with_core=False)
    got = run_plugin(plugin, BLOCKED_COMMAND, home=home)
    assert got["outcome"] == "throw"


def test_plugin_refuses_when_python_is_missing(tmp_path):
    """The other fail-closed limb: the core is found but the interpreter is not.
    Pinned because the resolution rewrite moved the throw sites around."""
    home, plugin = build_deployed_layout(tmp_path)
    got = run_plugin(
        plugin, BENIGN_COMMAND, home=home,
        env={"DEVRC_GUARD_PYTHON": str(tmp_path / "no-such-python")},
    )
    assert got["outcome"] == "throw", f"no interpreter, yet allowed: {got}"


# --------------------------------------------------------------------------- #
# 3. the DEVRC_GUARD_CORE override, and the last-resort repo layout
# --------------------------------------------------------------------------- #
def test_explicit_core_override_is_used_verbatim_with_no_silent_fallback(tmp_path):
    """🔴 An override is an override.

    A correctly-deployed core sits at $HOME/.config/opencode/guard_core.py, but
    DEVRC_GUARD_CORE points somewhere that does not exist. Falling back to the
    working core would be the WRONG kindness: the operator believes the guard is
    checking the file they named. Refuse, and name what they named.
    """
    home, plugin = build_deployed_layout(tmp_path)
    bogus = tmp_path / "not-a-real-core.py"
    got = run_plugin(
        plugin, BENIGN_COMMAND, home=home, env={"DEVRC_GUARD_CORE": str(bogus)}
    )
    assert got["outcome"] == "throw", (
        f"an explicit DEVRC_GUARD_CORE override was silently ignored in favour "
        f"of the deployed core: {got}"
    )
    assert str(bogus) in got["message"]
    assert str(home / ".config") not in got["message"], (
        "the override must be the ONLY candidate — the message shows the "
        "resolver also considered $HOME"
    )


def test_explicit_core_override_is_honoured_when_it_does_exist(tmp_path):
    """The positive half: a valid override must actually be used. Without this,
    the test above would pass with the override handling deleted entirely (no
    candidate would ever be found in a bare temp home)."""
    home = tmp_path / "empty-home"
    home.mkdir()
    plugin = tmp_path / "flat-hm_guard.js"
    shutil.copy2(GUARD_JS, plugin)
    core = tmp_path / "elsewhere" / "guard_core.py"
    core.parent.mkdir()
    shutil.copy2(GUARD_CORE, core)

    assert run_plugin(
        plugin, BENIGN_COMMAND, home=home, env={"DEVRC_GUARD_CORE": str(core)}
    )["outcome"] == "allow"
    got = run_plugin(
        plugin, BLOCKED_COMMAND, home=home, env={"DEVRC_GUARD_CORE": str(core)}
    )
    assert got["outcome"] == "throw"
    assert "BLOCKED by the devrc bash guard" in got["message"]


def test_plain_repo_checkout_layout_still_works(tmp_path):
    """The LAST-RESORT candidate: a non-home-manager checkout with the two files
    in their repo shape, and nothing at $HOME. This is the case the original
    module-relative resolution served, and it must not regress.

    It cannot weaken the fix: it is reached only when $HOME has no core, and a
    candidate is used only if it EXISTS.
    """
    home, plugin = build_repo_layout(tmp_path)
    assert run_plugin(plugin, BENIGN_COMMAND, home=home)["outcome"] == "allow"
    got = run_plugin(plugin, BLOCKED_COMMAND, home=home)
    assert got["outcome"] == "throw"
    assert "BLOCKED by the devrc bash guard" in got["message"]


def test_the_home_candidate_beats_the_module_relative_one(tmp_path):
    """🔴 ORDER, pinned by behaviour rather than by reading the source.

    Both candidates exist, and the module-relative one is a decoy that DENIES
    everything. If the resolver consulted it first, the benign command would be
    refused. This is what stops a later "simplification" from reinstating
    module-relative-first, which is the exact shape of the shipped bug.
    """
    home, plugin = build_deployed_layout(tmp_path)
    # <tmp>/guard_core.py — where `../guard_core.py` off the flat store dir lands.
    decoy = tmp_path / "guard_core.py"
    decoy.write_text(
        "import json, sys\n"
        "sys.stdin.read()\n"
        'print(json.dumps({"decision": "deny", "reason": "DECOY-CORE"}))\n'
    )
    got = run_plugin(plugin, BENIGN_COMMAND, home=home)
    assert got["outcome"] == "allow", (
        f"the module-relative candidate won over $HOME: {got}. In the real "
        f"deployment that path is /nix/<something>, which is how the outage "
        f"happened."
    )


# --------------------------------------------------------------------------- #
# 4. source-level pins
#
# These are INVARIANT GUARDS, not regression coverage — they are cheap and they
# localise a failure, but the tests above are the protection. They replace
# test_opencode_config.py::test_plugin_resolves_the_core_next_to_the_config_dir,
# which asserted the BROKEN line was present.
# --------------------------------------------------------------------------- #
def test_guard_js_does_not_rely_solely_on_its_own_module_url():
    src = GUARD_JS.read_text()
    assert "homedir()" in src, (
        "guard.js must resolve the core from $HOME, independently of where the "
        "module itself sits — `import.meta.url` resolves into the flat nix store"
    )
    assert ".config" in src and "opencode" in src


def test_home_nix_still_deploys_the_core_where_the_plugin_looks():
    """The plugin now hard-codes `$HOME/.config/opencode/guard_core.py`, so that
    attrpath in home.nix is load-bearing. Moving it breaks the guard."""
    nix = (ROOT / "nix" / "home.nix").read_text()
    assert 'home.file.".config/opencode/guard_core.py".source' in nix


@pytest.mark.parametrize("seam", ["DEVRC_GUARD_CORE", "DEVRC_GUARD_PYTHON",
                                  "DEVRC_GUARD_POLICY", "DEVRC_GUARD_DISABLE"])
def test_documented_test_seams_survive(seam):
    assert seam in GUARD_JS.read_text()
