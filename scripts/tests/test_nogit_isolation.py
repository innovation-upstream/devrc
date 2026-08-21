"""🔴 No target may touch the operator's REAL git config or reach a REAL remote —
and the guard PROVES it covered every target, rather than asserting that a
variable was set once.

WHY THIS FILE EXISTS
--------------------
MEASURED 2026-08-21. A test ran `githooks/install.sh` for real; line 39 of that
script is `git config --global core.hooksPath "$DIR"`, so it rewrote the
operator's global git config to point at a pytest tmpdir. In the same window ~63
fixture commits were pushed to the REAL `origin/main`, whose tree became a single
file named `f`, and the base clone ended up `core.bare = true` on a populated
working tree. All repaired; nothing lost. What was missing was a FLOOR.

GUARD 7 (#399) and GUARD 8 (#614) are the same guard shape and both began as a
conftest fixture, which protected 1 target of 17 and 1 directory of 13
respectively — `scripts/run-tests.sh` runs ONE pytest process per target. So
GUARD 9 is one module (`scripts/testlib/nogit_plugin.py`) registered at two entry
points: `-p testlib.nogit_plugin` on the single pytest line, and an import in
`scripts/tests/conftest.py`.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT (RULES.md asks for the label):

  * `test_the_real_installer_cannot_reach_the_operators_git_config` is THE
    regression test for the measured incident, run with the REAL
    `githooks/install.sh`. Its positive control
    `test_the_positive_control_that_installer_DOES_rewrite_an_unguarded_home`
    runs the identical command with the guard removed and a SACRIFICIAL home,
    and watches the write land in `<home>/.gitconfig` — which is what makes the
    "byte-identical" half above evidence rather than a reassuring zero.
  * `test_an_https_push_is_refused_by_the_guard_not_by_the_network` is the
    regression test for the pushes. Its discriminator
    `test_the_positive_control_that_the_same_push_fails_DIFFERENTLY_unguarded`
    is what separates "the allowlist refused it" from "there was no network" —
    an empty-result confusion that would otherwise score a completely
    unprotected run as protected.
  * `test_the_allowlist_still_permits_every_local_remote_shape` is the
    NEGATIVE control on breadth: `file` must not be so narrow that fixture
    remotes stop working. Both shapes in this tree are exercised.
  * `test_the_missing_plugin_is_named_and_red`,
    `test_an_ungoverned_redirect_is_named_and_red`,
    `test_an_absent_protocol_allowlist_is_named_and_red` and
    `test_a_dead_tripwire_comparator_is_named_and_red` are the MUTATION tests for
    the four mutants that leave a green suite. Each asserts THIS guard's own
    message, not merely that the run went red.
  * `test_a_target_that_rewrites_the_home_config_is_named_and_red` is REGRESSION
    coverage for the residual hazard the exports cannot close — code that REMOVES
    `GIT_CONFIG_GLOBAL` from its own environment and drops back to `$HOME`. It is
    measured against a scratch HOME, never the operator's.
  * `test_the_control_of_those_mutants_is_green` is the positive control for all
    of them, and pins the accounting line the others read.
  * the plugin-flag set pin, the lever pins, the two-way token pin and the
    accounting-site count are INVARIANT GUARDS. The bug never violated them; they
    exist so the enforcement point cannot be narrowed to nothing by an edit that
    still looks like a guard.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib import nogit_plugin  # noqa: E402
from testlib.runner_patch import runner_with_targets, write_pytest_suite  # noqa: E402

RUN_TESTS = SCRIPTS / "run-tests.sh"
RUNNER_SRC = RUN_TESTS.read_text(encoding="utf-8")
# 🔴 CODE ONLY. run-tests.sh is 40% commentary and GUARD 9's header quotes every
# token these pins look for — the variable names, the plugin flag, the marker
# string. A pin read off the raw text answers a question about the PROSE.
RUNNER_CODE = "\n".join(ln for ln in RUNNER_SRC.splitlines()
                        if not ln.lstrip().startswith("#"))

INSTALLER = REPO_ROOT / "githooks" / "install.sh"

# Assembled, not spelled, so this file's own text cannot satisfy a pin that
# greps the runner for it.
_NOGIT_FLAG = "-p testlib.nogit" + "_plugin"

# The complete set of guard plugins the single pytest invocation must carry,
# pinned BOTH ways: a guard dropped from that line covers nothing, and a guard
# added without updating this pin is a coverage claim nobody reviewed.
EXPECTED_PLUGINS = {"testlib.nolaunch" + "_plugin",
                    "testlib.spool" + "_plugin",
                    "testlib.nogit" + "_plugin"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _pytest_invocations(src: str) -> list[str]:
    """The lines that actually RUN pytest — not the ones that talk about it."""
    return [ln for ln in src.splitlines()
            if ln.strip().startswith("python -m pytest")
            and "--version" not in ln]


def _run(args: list[str], env: dict | None = None,
         timeout: int = 600) -> subprocess.CompletedProcess:
    full = {**os.environ, **(env or {})}
    for k, v in list(full.items()):
        if v is None:
            del full[k]
    return subprocess.run(["bash", *args], capture_output=True, text=True,
                          timeout=timeout, cwd=str(REPO_ROOT), env=full)


def _unguarded_home(tmp_path: Path) -> dict:
    """A scratch HOME with every GUARD 9 lever REMOVED from the child env.

    This is the measurement method for the runner-level tests, and it is sound
    for one reason: it moves the root of the SAME lookup git performs, without
    changing the code path at all. A write that would go to the operator's global
    config goes to `<tmp>/.gitconfig` instead — so "did this leak" becomes a
    question about a directory this test owns, and the operator's file is never
    written, only (in the in-process tests below) hashed.

    XDG_CONFIG_HOME must be REMOVED rather than set: it takes precedence for the
    XDG candidate path, so leaving the outer gate run's value in place would
    point the probe at the wrong file and every measurement here would read zero
    for a reason unrelated to the tree under test.
    """
    home = tmp_path / "scratch-home"
    home.mkdir(parents=True, exist_ok=True)
    return {"HOME": str(home), "XDG_CONFIG_HOME": None,
            "GIT_CONFIG_GLOBAL": None, "GIT_CONFIG_SYSTEM": None,
            "GIT_CONFIG_NOSYSTEM": None, "GIT_ALLOW_PROTOCOL": None,
            "DEVRC_TEST_GIT_GUARD_DIR": None, "DEVRC_TEST_GIT_IN_SESSION": None}


def _home_config_paths(tmp_path: Path) -> list[Path]:
    home = tmp_path / "scratch-home"
    return [home / ".gitconfig", home / ".config" / "git" / "config"]


def _digest(p: Path) -> str:
    """A file's content digest, or the literal 'ABSENT'.

    ABSENT is a value, not a skip: a guard that CREATES the operator's global
    config where none existed is the same finding as one that edits it.
    """
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return "ABSENT"


def _real_global_config_paths() -> list[Path]:
    """The operator's REAL global config file(s), asked of git itself.

    Read-only, and it deliberately does not restate git's lookup rule: the
    origins come from `git config --list --show-origin` with the guard's redirect
    removed from the CHILD environment only (never from `os.environ`), unioned
    with the two documented candidate paths so a file that does not exist yet is
    still watched.
    """
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                        "GIT_CONFIG_NOSYSTEM")}
    out: list[Path] = []
    git = shutil.which("git")
    if git is not None:
        proc = subprocess.run(
            [git, "config", "--global", "--list", "--show-origin"],
            capture_output=True, text=True, env=env, timeout=30)
        for line in proc.stdout.splitlines():
            if line.startswith("file:"):
                out.append(Path(line[len("file:"):].split("\t", 1)[0]))
    home = Path(env.get("HOME", "/nonexistent"))
    out.append(home / ".gitconfig")
    out.append(Path(env.get("XDG_CONFIG_HOME", str(home / ".config"))) / "git" / "config")
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _guard_config() -> Path:
    """This session's isolated global config — the file the guard redirects to."""
    raw = os.environ.get(nogit_plugin.CONFIG_ENV)
    assert raw, (
        "GIT_CONFIG_GLOBAL is not set in this pytest session. The GUARD 9 "
        "fixture is autouse and session-scoped, so its absence means the plugin "
        "did not load — every assertion in this file would be vacuous.")
    return Path(raw)


# --------------------------------------------------------------------------- #
# INVARIANT GUARDS — the enforcement point cannot be narrowed to nothing
# --------------------------------------------------------------------------- #
def test_the_single_pytest_invocation_loads_every_guard_plugin():
    """🔴 The whole design in one assertion: ONE `python -m pytest` line, and it
    carries the COMPLETE set of guard plugins.

    That is what makes "every target" true BY CONSTRUCTION rather than by two
    dozen conftests that drift — and what protects the target added next month,
    which is the half a per-directory copy can never do.

    The set is pinned BOTH ways on purpose. Dropping a plugin makes that guard
    cover nothing while the suite stays green (the mutant `test_the_missing_
    plugin_is_named_and_red` exercises end to end); ADDING one without touching
    this line is a coverage claim nobody reviewed.
    """
    invocations = _pytest_invocations(RUNNER_CODE)
    assert len(invocations) == 1, (
        "run-tests.sh must invoke pytest from exactly ONE place — all three "
        f"guards are attached to that line. Found {len(invocations)}: {invocations}")
    found = set(re.findall(r"-p\s+(testlib\.\w+)", invocations[0]))
    assert found == EXPECTED_PLUGINS, (
        "the set of guard plugins on the single pytest invocation changed.\n"
        f"  expected: {sorted(EXPECTED_PLUGINS)}\n  found:    {sorted(found)}\n"
        f"  line:     {invocations[0].strip()}")


def test_all_four_levers_are_exported_by_the_runner():
    """GIT_CONFIG_GLOBAL alone is not the guard.

    It closes `git config --global`; the system file is the same hazard one level
    up (two variables, because they cover different git versions and neither
    substitutes for the other); and the protocol allowlist is what stops a
    fixture repo pushing to a real remote. An export dropped here silently
    re-opens exactly one of the three surfaces.
    """
    for var in (nogit_plugin.CONFIG_ENV, nogit_plugin.SYSTEM_ENV,
                nogit_plugin.NOSYSTEM_ENV, nogit_plugin.PROTOCOL_ENV):
        assert re.search(rf"^export {var}=", RUNNER_CODE, re.M), (
            f"run-tests.sh no longer exports {var}; every target then runs with "
            "whatever the ambient environment says, which on the operator's "
            "machine is their real git configuration")


def test_the_guard_deliberately_does_not_reassign_home():
    """🔴 A PINNED DECISION, not an omission.

    Several suites legitimately read `~/.claude/...` (the analyze-service index
    store among them), so a blanket HOME rewrite would break real tests and be
    reverted — trading a durable guard for a temporary one. GIT_CONFIG_GLOBAL is
    the narrow lever that closes the surface that was actually poisoned.

    If HOME isolation is ever genuinely needed, it needs an argument in the open;
    this pin is what makes adding it a visible edit rather than a quiet one.
    """
    src = (SCRIPTS / "testlib" / "nogit_plugin.py").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert 'os.environ["HOME"]' not in code and "os.environ['HOME']" not in code, (
        "nogit_plugin now assigns HOME. That breaks the suites which read "
        "~/.claude/... — see this test's docstring before proceeding.")


def test_the_live_session_carries_every_lever():
    """🔴 Read the levers off the SESSION THAT IS RUNNING, not off the source.

    The pin above reads `run-tests.sh`; this one asks the process. They are
    different claims — the runner's exports say what a gate run does, and this
    says what THIS pytest process actually has, which is also the only statement
    that covers a bare `pytest <dir>` going through the conftest entry point.
    A lever dropped from `nogit_plugin.install()` is invisible to the source pin
    and lands here.
    """
    cfg = _guard_config()
    assert cfg.exists(), f"the isolated global config {cfg} does not exist"
    assert os.environ.get(nogit_plugin.SYSTEM_ENV) == os.devnull
    assert os.environ.get(nogit_plugin.NOSYSTEM_ENV) == "1"
    assert os.environ.get(nogit_plugin.PROTOCOL_ENV) == nogit_plugin.ALLOWED_PROTOCOLS
    # And the redirect is not merely SET — it is where git actually looks.
    origin = subprocess.run(
        ["git", "config", "--global", "--list", "--show-origin"],
        capture_output=True, text=True, timeout=30)
    assert str(cfg) in origin.stdout or origin.stdout.strip() == "", (
        "git reports a global config origin that is NOT this session's isolated "
        f"file:\n{origin.stdout}")


def test_the_config_control_is_fail_closed_when_the_write_is_not_contained(
        tmp_path, monkeypatch):
    """The containment check is not decoration — and it is REACHABLE.

    `config_control` reports `emitted` only when the write is present in the
    guard's OWN file. Reading the value back through `--global` proves git wrote
    somewhere; only the file check proves it wrote HERE, which is the whole
    claim. The branch fires on any git that ignores `GIT_CONFIG_GLOBAL` (it is
    2.32+), a state this suite cannot otherwise produce — so it is exercised
    directly, by asking the control about a directory git is not writing into.

    The redirect is monkeypatched to a scratch file so this probe cannot add a
    second control key to the runner's per-target ledger, where the count is
    pinned at exactly one.
    """
    monkeypatch.setenv(nogit_plugin.CONFIG_ENV,
                       str(nogit_plugin.guard_config_path(tmp_path)))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    status, detail = nogit_plugin.config_control(elsewhere)
    assert status == nogit_plugin.CONTROL_UNCONTAINED, (status, detail)
    # The positive control for that: asked about the directory git IS writing
    # into, the same call reports success. Without this, UNCONTAINED is
    # satisfied by a function that can only ever fail.
    status, detail = nogit_plugin.config_control(tmp_path)
    assert status == nogit_plugin.CONTROL_OK, (status, detail)


def test_the_runner_and_the_plugin_agree_on_the_shared_tokens():
    """The four strings crossing the process boundary, pinned BOTH ways.

    The runner greps for them in bash; the plugin writes them in Python.
    Renaming either side alone leaves the accounting matching nothing — and a
    grep that matches nothing prints a clean run, which is the failure that looks
    most like success.
    """
    for var, value in (
            ("NOGIT_SESSION_MARKER", nogit_plugin.SESSION_MARKER),
            ("NOGIT_CONTROL_SECTION", nogit_plugin.CONTROL_SECTION),
            ("NOGIT_CONTROL_OK", nogit_plugin.CONTROL_OK),
            ("NOGIT_PROTOCOL_OK", nogit_plugin.PROTOCOL_REFUSED)):
        assert f'{var}="{value}"' in RUNNER_CODE, (
            f"run-tests.sh's {var} does not match nogit_plugin's '{value}'. "
            "The accounting would silently match nothing.")


def test_every_target_kind_feeds_the_accounting():
    """Three call sites: pytest targets, hook scripts, shell scripts.

    A loop that stopped accounting reports no leaks for the most reassuring
    possible reason. (`TARGETS` membership additionally fails any pytest target
    that produced no record at all.)
    """
    assert RUNNER_CODE.count('_nogit_account "') == 3, (
        "expected exactly three sites to feed NOGIT_SEEN (pytest, hook scripts, "
        "shell scripts)")
    assert RUNNER_CODE.count("_nogit_mark_before") == 4, (
        "each accounting site needs its matching before-mark (plus the "
        "definition); an unpaired one attributes a change to the wrong target")


# --------------------------------------------------------------------------- #
# 🔴 THE REGRESSION TEST — the measured incident, with the REAL installer
# --------------------------------------------------------------------------- #
def test_the_real_installer_cannot_reach_the_operators_git_config(tmp_path):
    """🔴 RUN `githooks/install.sh` FOR REAL AND FOLLOW THE WRITE.

    This is the command that did the damage: line 39 is
    `git config --global core.hooksPath "$DIR"`. Under the guard it lands in this
    session's isolated file and the operator's real config is byte-identical.

    HOME is redirected for the CHILD ONLY, because install.sh also seeds
    `$HOME/.claude/audit-on-push.env` — a separate surface, owned by
    `test_hook_suites_do_not_touch_the_inherited_home.py`, and not something a
    test may create on someone's machine.

    `hooksPath` landing in the guard file is this test's positive control:
    without it, "the real config is unchanged" would be satisfied by an installer
    that never ran.
    """
    assert INSTALLER.is_file(), f"{INSTALLER} is gone — this test now proves nothing"
    guard_cfg = _guard_config()
    real = _real_global_config_paths()
    before = {p: _digest(p) for p in real}

    child_home = tmp_path / "installer-home"
    child_home.mkdir()
    try:
        proc = subprocess.run(["bash", str(INSTALLER)], capture_output=True,
                              text=True, timeout=120, cwd=str(REPO_ROOT),
                              env={**os.environ, "HOME": str(child_home)})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "hooksPath" in guard_cfg.read_text(encoding="utf-8"), (
            "the installer ran but its global write is NOT in this session's "
            f"isolated config ({guard_cfg}) — so the assertion below would be "
            "measuring an installer that wrote nowhere:\n"
            + proc.stdout + proc.stderr)
        # 🔴 MEASURED BEFORE THE CLEANUP, and that ordering is load-bearing. The
        # `finally` below UNSETS the key; taking the after-digest outside the try
        # would compare a file the cleanup had already restored, so a redirect
        # pointing straight at the operator's config would score CLEAN. Measured:
        # with the digest taken after cleanup, the "redirect points at $HOME"
        # mutant SURVIVED this test.
        after = {p: _digest(p) for p in real}
    finally:
        # 🔴 MANDATORY, not tidiness. `core.hooksPath` in the SESSION-WIDE
        # isolated config would make githooks/pre-push fire for every later
        # fixture `git push` in this process — and that hook runs the whole test
        # suite. A guard that recursively invoked the gate would be a worse bug
        # than the one it closes.
        subprocess.run(["git", "config", "--global", "--unset", "core.hooksPath"],
                       capture_output=True, text=True, timeout=30)

    changed = [str(p) for p in real if before[p] != after[p]]
    assert not changed, (
        "the installer reached the operator's REAL git config despite the "
        f"guard: {changed}")
    # And it did not create one where none existed.
    for p in real:
        if before[p] == "ABSENT":
            assert after[p] == "ABSENT", f"the installer CREATED {p}"


def test_the_positive_control_that_installer_DOES_rewrite_an_unguarded_home(tmp_path):
    """🔴 THE OTHER HALF OF THE PAIR — without it the test above is a zero.

    The identical command, with the guard's redirect REMOVED from the child
    environment and HOME pointed at a sacrificial directory. The write lands in
    `<home>/.gitconfig`, which is precisely what happened to the operator's file
    on 2026-08-21. Never run against the real config or a real remote: the whole
    demonstration is contained in `tmp_path`.
    """
    home = tmp_path / "sacrificial-home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                        "GIT_CONFIG_NOSYSTEM", "XDG_CONFIG_HOME")}
    env["HOME"] = str(home)
    proc = subprocess.run(["bash", str(INSTALLER)], capture_output=True, text=True,
                          timeout=120, cwd=str(REPO_ROOT), env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    poisoned = home / ".gitconfig"
    assert poisoned.exists(), (
        "with the redirect removed the installer wrote NOWHERE — the mechanism "
        "this guard exists to stop cannot be demonstrated, so the negative "
        f"control proves nothing:\n{proc.stdout}{proc.stderr}")
    assert "hooksPath" in poisoned.read_text(encoding="utf-8")


def test_a_global_config_write_is_captured_and_the_real_file_is_untouched():
    """The narrowest possible statement of the guarantee, hashed both sides.

    `git config --global user.name X` — the plainest form of the write — must
    leave every real global config file byte-identical while the value is
    readable back through `--global` from the isolated file.
    """
    guard_cfg = _guard_config()
    real = _real_global_config_paths()
    before = {p: _digest(p) for p in real}

    try:
        subprocess.run(["git", "config", "--global", "user.name",
                        "devrc-nogit-guard-probe"], check=True, timeout=30,
                       capture_output=True, text=True)
        got = subprocess.run(["git", "config", "--global", "--get", "user.name"],
                             capture_output=True, text=True, timeout=30)
        assert got.stdout.strip() == "devrc-nogit-guard-probe", (
            "the write did not read back — the probe never happened, so "
            "'unchanged' below would be vacuous")
        assert "devrc-nogit-guard-probe" in guard_cfg.read_text(encoding="utf-8")
        # Inside the try, BEFORE the cleanup — see the ordering note in
        # `test_the_real_installer_cannot_reach_the_operators_git_config`.
        after = {p: _digest(p) for p in real}
    finally:
        # A global `user.name` would hand every LATER test in this process a git
        # identity the nix-sandbox tier does not have — the two tiers must not
        # diverge because a probe left something behind.
        subprocess.run(["git", "config", "--global", "--unset", "user.name"],
                       capture_output=True, text=True, timeout=30)

    assert [str(p) for p in real if before[p] != after[p]] == []


# --------------------------------------------------------------------------- #
# 🔴 THE PROTOCOL HALF — refused by GIT, not by the network
# --------------------------------------------------------------------------- #
def test_an_https_push_is_refused_by_the_guard_not_by_the_network(tmp_path):
    """🔴 A fixture repo with an `https://` remote cannot push.

    The refusal must come from the ALLOWLIST — `fatal: transport 'https' not
    allowed`, emitted before any name resolution — and not from a DNS failure or
    a missing credential. Those are all non-zero exits, so keying on "it failed"
    would score a completely unprotected run as protected; this keys on git's own
    refusal and its discriminator lives in the next test.

    🔴 The host is `.invalid` (reserved by RFC 2606, can never resolve) so the
    probe is OFFLINE in both states: with the guard git refuses before any name
    resolution, and without it the attempt dies at DNS. A real hostname would
    make this suite send traffic to a third party every run and would behave
    differently on a networkless gate tier.
    """
    repo = tmp_path / "fixture"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                    "-m", "base"], check=True, timeout=60)
    proc = subprocess.run(
        ["git", "-C", str(repo), "push",
         "https://devrc-nogit-guard.invalid/nope.git", "HEAD:refs/heads/main"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode != 0, "an https push SUCCEEDED from the test suite"
    assert nogit_plugin.REFUSAL_TOKEN in (proc.stderr + proc.stdout).lower(), (
        "the push failed, but NOT because the protocol allowlist refused it. "
        "That is the empty-result confusion this guard exists to avoid — a "
        f"network-less sandbox fails the same way:\n{proc.stderr}{proc.stdout}")


def test_the_positive_control_that_the_same_push_fails_DIFFERENTLY_unguarded(tmp_path):
    """🔴 THE DISCRIMINATOR. Remove the allowlist from the CHILD env only.

    The same command must then fail for some OTHER reason — DNS, TLS, or a
    credential — with no "not allowed" anywhere in it. That is what proves the
    refusal above is the guard's doing and not a property of the URL, and it
    holds with or without a network (`.invalid` can never resolve).

    Offline by construction: nothing here can reach a real host.
    """
    env = {k: v for k, v in os.environ.items()
           if k != nogit_plugin.PROTOCOL_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    repo = tmp_path / "fixture"
    subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=60)
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-remote",
         "https://devrc-nogit-guard.invalid/nope.git"],
        capture_output=True, text=True, timeout=120, env=env)
    blob = (proc.stderr + proc.stdout).lower()
    assert proc.returncode != 0, blob
    assert nogit_plugin.REFUSAL_TOKEN not in blob, (
        "with GIT_ALLOW_PROTOCOL removed the transport was STILL refused, so "
        "the refusal in the sibling test is not attributable to the allowlist "
        f"— something else is blocking https here:\n{blob}")


def test_the_allowlist_still_permits_every_local_remote_shape(tmp_path):
    """🔴 THE BREADTH CHECK: `file` must not be so narrow that fixtures break.

    Both shapes that appear in this tree's fixtures — a plain filesystem path and
    an explicit `file://` URL — must clone AND push with the guard active. If
    this ever goes red the allowlist is too narrow and the answer is to widen it
    deliberately, not to delete the guard.
    """
    src = tmp_path / "src"
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", str(src)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(src), "-c", "user.name=t",
                    "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
                    "-m", "base"], check=True, timeout=60)
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, timeout=60)

    for remote in (str(bare), f"file://{bare}"):
        pushed = subprocess.run(
            ["git", "-C", str(src), "push", remote, "HEAD:refs/heads/main", "-f"],
            capture_output=True, text=True, timeout=120)
        assert pushed.returncode == 0, (
            f"the allowlist refused a LOCAL remote ({remote}) — it is too "
            f"narrow and fixture repos are broken:\n{pushed.stderr}")
    for i, remote in enumerate((str(bare), f"file://{bare}")):
        cloned = subprocess.run(
            ["git", "clone", "-q", remote, str(tmp_path / f"clone{i}")],
            capture_output=True, text=True, timeout=120)
        assert cloned.returncode == 0, (
            f"the allowlist refused a LOCAL clone ({remote}):\n{cloned.stderr}")


# --------------------------------------------------------------------------- #
# 🔴 MUTATION TESTS — the mutants that leave a green suite
# --------------------------------------------------------------------------- #
def _clean_runner(tmp_path: Path, extra_files: dict[str, str] | None = None):
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 2, prefix="test_plain")
    for name, body in (extra_files or {}).items():
        (target / name).write_text(body, encoding="utf-8")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[])
    return target, runner


def test_the_control_of_those_mutants_is_green(tmp_path):
    """The positive control for every runner-level test below.

    Unmutated, a plain target PASSES — and prints the accounting line the others
    read, with both controls beside the zero. Without this, "the run went red" is
    satisfied by the runner being red about anything at all.
    """
    target, runner = _clean_runner(tmp_path)
    proc = _run([str(runner), str(REPO_ROOT)], env=_unguarded_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"the clean control run was not green:\n{out}"
    assert re.search(
        rf"{re.escape(str(target))}  real-config-changed=0/\d+  "
        r"config-control=1  protocol=refused  plugin=1", out), (
        "the per-target GUARD 9 line is missing or its shape changed — the "
        f"accounting the other tests read is not actually printed:\n{out}")


def test_the_missing_plugin_is_named_and_red(tmp_path):
    """🔴 MUTANT 1: the guard loading in NO target, silently.

    Strip `-p testlib.nogit_plugin` — the shape of every "narrow the glob /
    comment it out" mutation, whose whole danger is that the suite stays green.
    The session marker is what makes it observable: without the marker
    requirement this target's `real-config-changed=0` is indistinguishable from
    real protection.
    """
    _, runner = _clean_runner(tmp_path)
    src = runner.read_text(encoding="utf-8")
    # 🔴 The CODE line, not the prose. GUARD 9's header names the flag, so a
    # blanket replace would edit a comment and leave the invocation untouched —
    # a mutation that never happened, scored SURVIVED.
    hits = _pytest_invocations(src)
    assert len(hits) == 1 and _NOGIT_FLAG in hits[0], (
        f"expected exactly one live site to mutate, found {hits}")
    runner.write_text(src.replace(hits[0], hits[0].replace(" " + _NOGIT_FLAG, "")),
                      encoding="utf-8")

    proc = _run([str(runner), str(REPO_ROOT)], env=_unguarded_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"a target that ran with NO git guard produced a PASSING run:\n{out}")
    assert "session marker" in out, (
        f"the run failed, but not because the plugin was missing:\n{out}")


def test_an_ungoverned_redirect_is_named_and_red(tmp_path):
    """🔴 MUTANT 2: the redirect exported but not governing git.

    Neuter `export GIT_CONFIG_GLOBAL` and every `git config --global` in the run
    goes back to the operator's real file. The runner must refuse to start rather
    than proceed with an unarmed guard — which is the state the whole suite was
    in on 2026-08-21.
    """
    _, runner = _clean_runner(tmp_path)
    src = runner.read_text(encoding="utf-8")
    mutated, n = re.subn(r'^export GIT_CONFIG_GLOBAL="\$NOGIT_CONFIG"$',
                         ': "$NOGIT_CONFIG"', src, count=1, flags=re.M)
    assert n == 1, "the GIT_CONFIG_GLOBAL export was not found to mutate"
    runner.write_text(mutated, encoding="utf-8")

    proc = _run([str(runner), str(REPO_ROOT)], env=_unguarded_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"an ungoverned redirect produced a PASSING run:\n{out}"
    assert "does NOT write to this run's" in out, (
        f"the run failed, but not because the redirect was dead:\n{out}")
    # 🔴 The positive control for the mutation itself: with the export neutered
    # the runner's own probe landed in the (SACRIFICIAL) home config, which is
    # exactly where the operator's write went on 2026-08-21. Without this the
    # red above is satisfied by the mutant failing for any other reason.
    landed = [p for p in _home_config_paths(tmp_path)
              if p.exists() and "runner-probe" in p.read_text(encoding="utf-8")]
    assert landed, (
        "the mutant did not actually become unguarded — the probe wrote "
        "somewhere else, so this mutation is not the one the test claims")


def test_an_absent_protocol_allowlist_is_named_and_red(tmp_path):
    """🔴 MUTANT 3: the transport allowlist exported but not governing git.

    Neuter `export GIT_ALLOW_PROTOCOL` and a fixture repo can push to a real
    remote again. The runner's up-front probe must catch it by NAME — the probe
    keys on git's refusal, so a run with no network still fails here rather than
    reading the DNS error as protection.
    """
    _, runner = _clean_runner(tmp_path)
    src = runner.read_text(encoding="utf-8")
    mutated, n = re.subn(r"^export GIT_ALLOW_PROTOCOL=file$", ": file",
                         src, count=1, flags=re.M)
    assert n == 1, "the GIT_ALLOW_PROTOCOL export was not found to mutate"
    runner.write_text(mutated, encoding="utf-8")

    proc = _run([str(runner), str(REPO_ROOT)], env=_unguarded_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"an absent allowlist produced a PASSING run:\n{out}"
    assert "NOT refused by the" in out, (
        f"the run failed, but not because https was reachable:\n{out}")


def test_a_dead_tripwire_comparator_is_named_and_red(tmp_path):
    """🔴 MUTANT 4: the tripwire that can never report a change.

    Make the fingerprint a constant. Every `real-config-changed=0` then becomes
    the reassuring zero of a detector wired to nothing — the exact shape
    claude/RULES.md calls a positive-control failure. The canary the runner runs
    before anything else is what kills this mutant.
    """
    _, runner = _clean_runner(tmp_path)
    src = runner.read_text(encoding="utf-8")
    mutated, n = re.subn(r"^_nogit_fingerprint_of\(\) \{.*?^\}",
                         "_nogit_fingerprint_of() {\n  echo constant\n}",
                         src, count=1, flags=re.S | re.M)
    assert n == 1, "the fingerprint function was not found to mutate"
    runner.write_text(mutated, encoding="utf-8")

    proc = _run([str(runner), str(REPO_ROOT)], env=_unguarded_home(tmp_path))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a dead tripwire produced a PASSING run:\n{out}"
    assert "cannot detect a file changing" in out, (
        f"the run failed, but not because the comparator was dead:\n{out}")


def test_a_target_that_rewrites_the_home_config_is_named_and_red(tmp_path):
    """🔴 THE RESIDUAL HAZARD the exports cannot close, and its detector.

    Code that REMOVES `GIT_CONFIG_GLOBAL` from its own environment drops back to
    `$HOME`. The redirect cannot stop that — but the tripwire sees it, names the
    target and names the file. Measured against a scratch HOME, never the
    operator's.
    """
    probe = (
        "import os, subprocess\n"
        "\n"
        "\n"
        "def test_drops_the_redirect_and_writes_home():\n"
        "    env = {k: v for k, v in os.environ.items()\n"
        "           if k not in ('GIT_CONFIG_GLOBAL', 'GIT_CONFIG_SYSTEM',\n"
        "                        'GIT_CONFIG_NOSYSTEM')}\n"
        "    subprocess.run(['git', 'config', '--global', 'core.hooksPath',\n"
        "                    '/tmp/planted-by-a-test'], check=True, env=env)\n"
    )
    home = tmp_path / "scratch-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text("[user]\n\tname = pre-existing\n",
                                     encoding="utf-8")
    target, runner = _clean_runner(tmp_path, {"test_drop.py": probe})
    proc = _run([str(runner), str(REPO_ROOT)], env=_unguarded_home(tmp_path))
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        f"a planted write to the home git config produced a PASSING run:\n{out}")
    assert "GUARD 9 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    assert str(target) in out, f"the failure did not NAME the target:\n{out}"
    assert str(home / ".gitconfig") in out, (
        f"the failure did not NAME the file that changed:\n{out}")
    # The positive control for the assertion above: the write really happened.
    assert "planted-by-a-test" in (home / ".gitconfig").read_text(encoding="utf-8")
