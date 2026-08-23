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
GUARD 10 is one module (`scripts/testlib/nogit_plugin.py`) registered at two entry
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
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from testlib import nogit_plugin  # noqa: E402
# 🔴 The SAME helper the runner's evidence probe calls — imported, not restated.
# A second copy of "is another process sitting in this repo?" would agree with
# the first until the day one of them changed, and the control below would then
# be asserting its own opinion instead of the guard's.
from testlib.gitenv import live_cotenants  # noqa: E402
from testlib.runner_patch import runner_with_targets, write_pytest_suite  # noqa: E402

RUN_TESTS = SCRIPTS / "run-tests.sh"
RUNNER_SRC = RUN_TESTS.read_text(encoding="utf-8")
# 🔴 CODE ONLY. run-tests.sh is 40% commentary and GUARD 10's header quotes every
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
                    "testlib.gitenv" + "_plugin",
                    "testlib.nogit" + "_plugin"}
# 🔴 `gitenv_plugin` (GUARD 9, #683) was added 2026-08-22 when the two git
# guards landed a day apart. The ledger did exactly what it is for: it went RED
# on the merged tree because the set GREW, which is the review this comment is.
# GUARD 9 strips the repo POINTERS so a fixture cannot reach this checkout by
# accident; GUARD 10 (`nogit_plugin`) refuses a WRITE to any repo outside the
# session tmp roots. Two guards, not one done twice — see each header.


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _guard10_failure_block(out: str) -> str:
    """Just GUARD 10's `ERROR:` block, so a path assertion means what it says.

    🔴 WHY THIS EXISTS, MEASURED. `assert str(path) in out` looks like "the
    failure named the file" and is not: `run-tests.sh` prints
    `present <path>  [<class>]` for EVERY protected file at startup,
    unconditionally, whether or not anything fails. An audit mutant that
    suppressed the path in GUARD 10's failure message left the negative controls
    at `9 passed` — SURVIVED. The same shape as the reason assertion inside the
    downgrade block, which had already been scoped for exactly this reason.

    Returns "" when the block is absent, so a caller asserting into it fails
    rather than silently searching an empty string it mistook for the run.
    """
    head = "ERROR:"
    tail = "---- end GUARD 10 problems ----"
    if "GUARD 10 problem(s):" not in out or tail not in out:
        return ""
    after = out.split("GUARD 10 problem(s):", 1)[1]
    return head + after.split(tail, 1)[0]


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
    """A scratch HOME with every GUARD 10 lever REMOVED from the child env.

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
        "GIT_CONFIG_GLOBAL is not set in this pytest session. The GUARD 10 "
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
        "the per-target GUARD 10 line is missing or its shape changed — the "
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
    # 🔴 The CODE line, not the prose. GUARD 10's header names the flag, so a
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
    assert "GUARD 10 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    # 🔴 Read out of GUARD 10's FAILURE block, not the whole run — the startup
    # `present <path>` listing satisfies a bare `in out` even when the failure
    # names nothing (see `_guard10_failure_block`).
    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert str(target) in block, (
        f"the failure did not NAME the target:\n{block}\n---\n{out}")
    assert str(home / ".gitconfig") in block, (
        f"the failure did not NAME the file that changed:\n{block}\n---\n{out}")
    # The positive control for the assertion above: the write really happened.
    assert "planted-by-a-test" in (home / ".gitconfig").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# 🔴 #730 — THE REPO-LOCAL CLASS, AND THE THREE DIRECTIONS THAT DEFINE IT
# --------------------------------------------------------------------------- #
# `NOGIT_PROTECTED` holds two classes of file and they are NOT the same claim:
#
#   GLOBAL      `~/.gitconfig`, `$XDG_CONFIG_HOME/git/config`, everything
#               `git config --global --list --show-origin` names. Nothing a
#               concurrent worktree operation does writes these, so a change is
#               still attributable to whatever was running. Always enforcing.
#   REPO-LOCAL  `<git-common-dir>/config`. Shared by EVERY worktree of the
#               clone: `git worktree add`, `git branch --track` and any
#               `git config` in any of them rewrite it. On the operator's box
#               that is ~90 worktrees and ~15 concurrent sessions, so it moves
#               continuously and GUARD 10 blamed whichever target was running.
#
# MEASURED, one commit, two environments (#730):
#   isolated clone -> real-config-changed=0/3 on every target, PASS
#   shared clone   -> .git/config CHANGED on 2 targets, FAIL, with failed=0
# In that same shared run GUARD 9 PROVED co-tenancy and downgraded itself.
#
# WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT. All four are RED at
# `a0fb39c7`, but only ONE of them is red for a BEHAVIOURAL reason, and the
# difference is the label:
#   * `test_a_repo_local_change_is_REPORTED_when_a_cotenant_is_PROVEN` is THE
#     regression test. At base, MEASURED: `real-config-changed=1/3`,
#     `RESULT: FAIL (exit=1)` with `failed=0` — #730's shape exactly. GREEN at
#     HEAD. This is the only one that may be counted as regression coverage.
#   * `test_a_repo_local_change_still_FAILS_when_no_cotenant_is_proven` and
#     `test_a_global_change_FAILS_even_with_a_cotenant_PROVEN` are NEGATIVE
#     CONTROLS. The BEHAVIOUR they pin (the run must go red) is what base
#     already did; they are red at base only because they also assert the new
#     class WORDING. Do not read their base-red as regression coverage. Without
#     them, "#730 is fixed" is satisfied by a patch that simply stops watching
#     the file, or by one that lets co-tenancy suppress `~/.gitconfig` too.
#   * `test_a_broken_evidence_probe_fails_TOWARD_enforcing` is a FAIL-SAFE guard
#     on code that did not exist at base, so its base-red is structural (its
#     mutation anchor is absent), not behavioural. Its branch is proved
#     REACHABLE by the mutation that flips the probe's failure path to `proven`,
#     which turns it red at HEAD.
#
# THE MEASUREMENT METHOD, and why it is sound: the runner is driven against a
# SCRATCH ROOT — a directory whose top-level entries are symlinks to this repo
# (so `$ROOT/scripts/...` resolves and the runner's own preconditions hold) but
# which owns a FRESH `.git` of its own. The operator's real clone is never
# written. Co-tenancy is then flipped with REAL evidence rather than a stub: a
# live process whose cwd sits in that scratch root, which is exactly what
# `testlib.gitenv.live_cotenants` looks for.
_G10_PROBE_IMPORT = "from testlib.gitenv import attribution_evidence, protected_git_dirs"


def _scratch_root(tmp_path: Path) -> Path:
    """A repository of its own whose content is this repo, by symlink.

    🔴 The point is the `.git`: `git init` here gives the runner a
    `<git-common-dir>/config` that this test OWNS, so a planted repo-local write
    is measured against a directory pytest created and never against the
    operator's shared clone.
    """
    scratch = tmp_path / "scratch-root"
    scratch.mkdir(parents=True, exist_ok=True)
    for entry in REPO_ROOT.iterdir():
        if entry.name == ".git":
            continue
        (scratch / entry.name).symlink_to(entry)
    init = subprocess.run(["git", "init", "-q", str(scratch)],
                          capture_output=True, text=True, timeout=120)
    assert init.returncode == 0, f"could not init the scratch root:\n{init.stderr}"
    assert (scratch / ".git" / "config").is_file(), (
        "the scratch root has no repo-local config — the whole measurement "
        "below would be about a file that does not exist")
    return scratch


def _runner_over(tmp_path: Path, scratch: Path, shell_tests: list[str]) -> Path:
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 2, prefix="test_plain")
    return runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                               hook_tests=[], shell_tests=shell_tests)


def _run_at(runner: Path, scratch: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, **_unguarded_home(tmp_path)}
    for k, v in list(env.items()):
        if v is None:
            del env[k]
    return subprocess.run(["bash", str(runner), str(scratch)], capture_output=True,
                          text=True, timeout=900, cwd=str(REPO_ROOT), env=env)


class _cotenant:
    """A REAL live process sitting in `root`, which is the evidence itself.

    Not a stub and not a monkeypatch: `live_cotenants` scans `/proc` for a
    process whose cwd is inside a protected repository and which is not one of
    our own ancestors. A `sleep` started here is a sibling of the runner, so it
    is precisely the thing that function exists to find.
    """

    def __init__(self, root: Path):
        self.root = root
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        self.proc = subprocess.Popen(["sleep", "900"], cwd=str(self.root),
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        # The positive control for the evidence itself: assert the helper the
        # runner will call ACTUALLY sees it, before the run whose verdict
        # depends on it. Without this a run that passed for some unrelated
        # reason would be scored as "the downgrade worked".
        for _ in range(50):
            if live_cotenants([self.root / ".git"]):
                return self
            time.sleep(0.1)
        raise AssertionError(
            f"no co-tenant was visible in {self.root} after starting one — the "
            "evidence this test depends on was never established")

    def __exit__(self, *exc):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:      # pragma: no cover - defensive
                self.proc.kill()
        return False


# 🔴 NO SHEBANG, DELIBERATELY, AND NOT AN OVERSIGHT. `run-tests.sh`'s
# SHELL_TESTS loop invokes each entry as `bash "$SHELL_TEST"`, so the shebang is
# never consulted — and writing `#!/usr/bin/env bash` here would plant the exact
# defect `test_runtime_shebangs.py` exists to catch: `/usr/bin/env` is present on
# the dev host and ABSENT in the nix build sandbox, which is the tier that gates
# merges. Measured: this file's first full-suite run was RED on that guard.
def _plant_repo_local_write(scratch: Path) -> str:
    name = "g10-write-repo-local.sh"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        f'git -C "{scratch}" config devrc-g10.planted yes\n',
        encoding="utf-8")
    return name


def _plant_global_write(scratch: Path, home: Path) -> str:
    name = "g10-write-global.sh"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        "env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CONFIG_NOSYSTEM "
        f'HOME="{home}" git config --global core.hooksPath /tmp/planted-by-a-test\n',
        encoding="utf-8")
    return name


def test_a_repo_local_change_is_REPORTED_when_a_cotenant_is_PROVEN(tmp_path):
    """🔴 THE REGRESSION TEST FOR #730. RED at a0fb39c7, GREEN at HEAD.

    A target changes `<git-common-dir>/config` while another live process sits
    in that repository. The change is real and is NOT dropped — it is counted,
    named, and its reason is printed — but it does not fail the run, because
    nothing in this environment can attribute it to the target.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_write(scratch)
    runner = _runner_over(tmp_path, scratch, [name])

    with _cotenant(scratch):
        proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr

    assert proc.returncode == 0, (
        "a repo-local config change with a PROVEN external writer still failed "
        f"the run — this is #730:\n{out}")
    # The write really happened: without this the pass above is satisfied by
    # nothing having changed at all.
    assert "planted" in (scratch / ".git" / "config").read_text(encoding="utf-8"), (
        "the planted repo-local write never landed, so this run proves nothing")
    assert f"{name}  real-config-changed=0/" in out, (
        f"the accounting did not record the change as non-enforcing:\n{out}")
    # 🔴 TRAILING BOUNDARY PINNED. A bare `"repo-local-reported=1" in out` is a
    # PREFIX match: it is equally satisfied by `=10` … `=19`, so a count that
    # ran away by an order of magnitude reads as the expected 1.
    assert re.search(r"repo-local-reported=1(?![0-9])", out), (
        f"the downgraded change was not COUNTED as exactly 1 on the target's "
        f"line:\n{out}")
    assert re.search(r"repo-local-reported-total=1(?![0-9])", out), (
        f"the downgraded change is missing from the run's summary, or the total "
        f"is not exactly 1:\n{out}")

    # 🔴 SCOPED TO GUARD 10's OWN BLOCK, and the mutation sweep is why.
    # `cannot attribute: live processes are sitting inside …` is also printed by
    # GUARD 9's per-target `gitenv(session)` lines, which fire here because that
    # detector watches the repo `testlib` lives in — the operator's co-tenanted
    # clone. Asserted against the whole output, the mutant that DELETES GUARD
    # 10's reason logging SURVIVED a fully green suite: the neighbour's sentence
    # satisfied the pin. The same applies to the file path, which the startup
    # `protected files:` listing also prints. Both are read out of the reported
    # block only.
    head = "---- repo-local git config: REPORTED, not enforced (#730) ----"
    tail = "This file is the git COMMON dir's config"
    assert head in out, f"the downgrade block was never printed:\n{out}"
    assert tail in out, f"the downgrade block is not delimited as expected:\n{out}"
    block = out.split(head, 1)[1].split(tail, 1)[0]
    assert name in block, (
        f"the downgrade block did not NAME the target:\n{block}\n---\n{out}")
    assert str(scratch / ".git" / "config") in block, (
        f"the downgrade block did not NAME the file:\n{block}\n---\n{out}")
    assert "cannot attribute: live processes are sitting inside" in block, (
        f"the downgrade block did not state the REASON, as GUARD 9 does:"
        f"\n{block}\n---\n{out}")


def test_a_repo_local_change_still_FAILS_when_no_cotenant_is_proven(tmp_path):
    """🔴 NEGATIVE CONTROL — NOT regression coverage.

    The behaviour it pins (the run goes red) is what base already did; it is red
    at base only over the new class wording. Counting that as regression
    coverage would be a coverage claim nobody measured.

    This is the half that matters. Without it, "#730 is fixed" is satisfied by a
    patch that simply stops watching the repo-local file, and the `core.bare =
    true` casualty that motivated protecting it would be uncovered on a clean
    machine and in CI — which is exactly where it happened.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_write(scratch)
    runner = _runner_over(tmp_path, scratch, [name])

    assert not live_cotenants([scratch / ".git"]), (
        "something is already sitting in this scratch root, so the 'no proven "
        "writer' arm of this control does not hold")
    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        f"an ATTRIBUTABLE repo-local config change produced a PASSING run:\n{out}")
    assert "GUARD 10 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    assert "repo-local-enforced" in out, (
        f"the failure did not classify the change as repo-local-ENFORCED:\n{out}")
    assert "repo-local-reported-total=0" in out, (
        f"nothing should have been downgraded in this run:\n{out}")
    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert "FOUND NO PROOF of another writer" in block, (
        f"the failure did not say WHY it was attributable:\n{block}")
    # 🔴 The message must not overstate the probe: it checks cwd against the git
    # dir and its parent, so a SIBLING worktree is invisible. Saying "no other
    # writer exists" would be the #730 misdiagnosis in a new place.
    assert "SIBLING worktree" in block, (
        f"the failure claimed more than the probe measured — the sibling-worktree "
        f"blind spot is not named:\n{block}")
    assert str(scratch / ".git" / "config") in block, (
        f"the failure did not NAME the file that changed:\n{block}\n---\n{out}")


# --------------------------------------------------------------------------- #
# 🔴 THE ATTRIBUTION MESSAGE — WHAT MOVED, AND WHICH HYPOTHESIS IT SUPPORTS
# --------------------------------------------------------------------------- #
# MEASURED 2026-08-23 on the operator's box: GUARD 10 flagged `<devrc>/.git/config`
# FOUR separate times in one day and each time the message named whichever target
# was in teardown ("…so the change is attributed to this target"). Every one of
# those writes was a concurrent `git branch` / `worktree add` in the shared clone.
# Cost: a four-run experiment by one agent and a diagnosis pass by the operator,
# all of it auditing tests that had done nothing.
#
# The DETECTION is deliberately unchanged — an unattributable repo-local delta
# still fails the run, and the controls below pin that in both directions. What
# these tests pin is the MESSAGE: the key names that moved, and a ranking that
# follows them rather than always landing on the target.
#
# WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT:
#   * `test_an_ORDINARY_git_delta_does_not_blame_the_target` is THE regression
#     test. Base prints the accusatory sentence and no key delta at all.
#   * the HAZARD, VALUE and NOT-VISIBLE tests are NEGATIVE CONTROLS on the
#     ranking, the redaction and the empty list. They are red at base only
#     because base emits none of this; the behaviour they defend is "the fix did
#     not overshoot into always excusing the target", "a config VALUE is never
#     printed", and "an unobservable delta says so instead of rendering blank".

def _plant_repo_local_keys(scratch: Path, *pairs: tuple[str, str]) -> str:
    """A shell target that writes specific KEYS into the scratch clone's config.

    Same no-shebang convention as `_plant_repo_local_write` above, and for the
    same reason: `run-tests.sh` invokes SHELL_TESTS as `bash "$SHELL_TEST"`.

    🔴 BOTH SIDES GO THROUGH `shlex.quote`. They were interpolated bare, which
    was correct for the values in use and silently wrong for anything carrying
    a space, a glob or a `!` — the planter would then write something other
    than what its caller asked for and the test would pass or fail for a reason
    nobody could see. `submodule.<n>.update = !cmd` is exactly such a value.
    """
    name = "g10-write-keys.sh"
    body = "set -euo pipefail\n" + "".join(
        f'git -C {shlex.quote(str(scratch))} config {shlex.quote(k)} '
        f'{shlex.quote(v)}\n' for k, v in pairs)
    (scratch / name).write_text(body, encoding="utf-8")
    return name


def test_an_ORDINARY_git_delta_does_not_blame_the_target(tmp_path):
    """🔴 THE REGRESSION TEST for the four misattributions of 2026-08-23.

    `branch.<name>.remote` / `.merge` appearing in a clone's SHARED config is
    what `git branch --track`, `checkout -b --track` and `push -u` write. The
    run must still FAIL — that is the guard's job and it is unchanged — but the
    message must name the keys and rank the concurrent writer FIRST, instead of
    telling the reader the target did it.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_keys(
        scratch,
        ("branch.topic-x.remote", "origin"),
        ("branch.topic-x.merge", "refs/heads/topic-x"))
    runner = _runner_over(tmp_path, scratch, [name])

    assert not live_cotenants([scratch / ".git"]), (
        "something is already sitting in this scratch root, so this run would "
        "take the DOWNGRADE arm and measure a different message")
    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr

    # 🔴 THE DETECTION IS UNCHANGED. Without this the whole fix is satisfied by
    # a patch that simply stops failing, which is the outcome the message was
    # annoying enough to tempt someone into.
    assert proc.returncode != 0, (
        f"the run PASSED — the message fix silently disarmed the guard:\n{out}")

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    # The sentence that was wrong four times. Its absence is the fix.
    assert "attributed to this target" not in block, (
        f"the failure still hands the reader a VERDICT it cannot support:\n{block}")
    assert "WINDOW, NOT A CULPRIT" in block, (
        f"the failure did not say the target is the window rather than the "
        f"writer:\n{block}")
    # 🔴 The keys themselves, in GUARD 10's block — not merely somewhere in the
    # run. Same reason `_guard10_failure_block` exists.
    assert "+ branch.topic-x.remote" in block, (
        f"the failure did not name the key that moved:\n{block}")
    assert "+ branch.topic-x.merge" in block, (
        f"the failure named only one of the two keys that moved:\n{block}")
    assert "SHAPE: ORDINARY GIT" in block, (
        f"the failure did not classify the delta's shape:\n{block}")
    assert "LEADING hypothesis is a concurrent git command" in block, (
        f"the failure did not rank the concurrent writer first:\n{block}")
    # A ranking, never a clearance — the probe proved nothing either way.
    assert "RANKING, not a verdict" in block, (
        f"the failure presented its ranking as a finding:\n{block}")
    assert "worktree list" in block, (
        f"the failure did not hand over the discriminator for the blind spot it "
        f"just admitted to:\n{block}")


def test_a_HAZARD_shaped_delta_still_points_at_the_target(tmp_path):
    """🔴 NEGATIVE CONTROL ON THE RANKING — the fix must not overshoot.

    `core.hooksPath` written into a clone's config is the 2026-08-21 incident
    itself. If the new wording led with "the target is the window, not a
    culprit" here it would have traded one confident misdiagnosis for its
    mirror image, and the guard's whole reason for existing reads as noise.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_keys(
        scratch, ("core.hooksPath", "/tmp/planted-by-a-test"))
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"a core.hooksPath write into the clone's config PASSED:\n{out}")

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    # git lower-cases key names in `--list`; pin what is actually printed.
    assert "+ core.hookspath" in block, (
        f"the failure did not name the key that moved:\n{block}")
    assert "SHAPE: HAZARD" in block, (
        f"the incident's own key shape was not classified as a hazard:\n{block}")
    assert "AUDIT THIS TARGET FIRST" in block, (
        f"the failure did not send the reader to the target:\n{block}")
    assert "WINDOW, NOT A CULPRIT" not in block, (
        f"the failure led with the concurrent-writer excuse on a delta that is "
        f"the 2026-08-21 incident's own shape:\n{block}")


def test_the_key_delta_never_prints_a_config_VALUE(tmp_path):
    """🔴 NEGATIVE CONTROL ON REDACTION, with its positive control beside it.

    `<git-common-dir>/config` holds `remote.<name>.url`, which on some clones
    carries a token, and this output lands in CI logs. The rendering prints key
    NAMES only — so a value-only change must still surface (positive control:
    the key name appears, marked `~`) while the value must not appear ANYWHERE
    in the run's output, not merely outside GUARD 10's block.
    """
    scratch = _scratch_root(tmp_path)
    secret = "s3cr3t-token-that-must-never-be-printed"
    seed = subprocess.run(
        ["git", "-C", str(scratch), "config", "remote.origin.url",
         "https://example.invalid/before"],
        capture_output=True, text=True, timeout=120)
    assert seed.returncode == 0, f"could not seed the remote URL:\n{seed.stderr}"
    name = _plant_repo_local_keys(
        scratch, ("remote.origin.url", f"https://example.invalid/{secret}"))
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr

    # Positive control: the write really landed, so a clean output below is
    # about the RENDERING and not about a write that never happened.
    assert secret in (scratch / ".git" / "config").read_text(encoding="utf-8"), (
        "the planted value never reached the config — this run proves nothing")
    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert "~ remote.origin.url" in block, (
        f"a VALUE-only change did not surface as a moved key:\n{block}")
    assert secret not in out, (
        "a git config VALUE was printed into the run's output")


def test_bytes_that_move_with_no_key_delta_SAY_SO(tmp_path):
    """🔴 AN EMPTY LIST WOULD BE A CLAIM ABOUT THE FILE, NOT THE PARSE.

    A comment appended to `.git/config` moves the bytes the tripwire hashes and
    changes nothing `git config --list` reports. Rendering that as an empty
    "keys that moved" list reads as "nothing identifiable changed" — which is
    the same shape of confident silence this whole fix exists to remove.
    """
    scratch = _scratch_root(tmp_path)
    name = "g10-comment.sh"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        f'printf "\\t# planted by a test\\n" >> "{scratch}/.git/config"\n',
        encoding="utf-8")
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"a byte-level change to the clone's config PASSED:\n{out}")

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert "NOT VISIBLE" in block, (
        f"a delta with no visible keys rendered as an empty list:\n{block}")
    assert "no key-level delta was visible" in block, (
        f"the failure did not say WHY the key list is empty:\n{block}")
    assert "SHAPE: UNRECOGNISED" in block, (
        f"an unobservable delta was ranked instead of declared unrankable:"
        f"\n{block}")


def test_the_DOWNGRADED_block_also_carries_the_key_delta(tmp_path):
    """The reader's question is the same whether the run failed or not.

    A downgrade answers "this will not fail you"; it does not answer "who wrote
    it". The rows are already recorded, so withholding them here would leave the
    #730 arm — the common one on the operator's box — as uninformative as the
    message this change replaced.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_keys(scratch, ("branch.topic-y.remote", "origin"))
    runner = _runner_over(tmp_path, scratch, [name])

    with _cotenant(scratch):
        proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (
        f"a repo-local change with a PROVEN external writer failed the run — "
        f"this test is measuring the wrong arm:\n{out}")

    head = "---- repo-local git config: REPORTED, not enforced (#730) ----"
    tail = "This file is the git COMMON dir's config"
    assert head in out and tail in out, (
        f"the downgrade block was not printed as expected:\n{out}")
    block = out.split(head, 1)[1].split(tail, 1)[0]
    assert "+ branch.topic-y.remote" in block, (
        f"the downgrade block did not say WHICH key moved:\n{block}")
    assert "SHAPE: ORDINARY GIT" in block, (
        f"the downgrade block did not classify the delta's shape:\n{block}")


# --------------------------------------------------------------------------- #
# 🔴 THE #773 AUDIT ROUND — the reassuring lead was reachable from three states
# --------------------------------------------------------------------------- #
# The first cut of the lead selection branched on `hazard` alone, so BOTH other
# states fell through to "THE TARGET NAMED HERE IS THE WINDOW, NOT A CULPRIT":
#
#   * a MIXED run, where a GLOBAL file also changed. `_nogit_shape_for` returns
#     `none` for a global file — there are no key rows for one — so the shape
#     aggregation structurally could not see it, and the one class that is
#     ALWAYS attributable got the excuse. The comment above the loop claimed the
#     opposite of what the code did.
#   * an UNRECOGNISED delta, which the classifier's own header says must be
#     "ranked as NEITHER". It fired on `devrc-g10.planted` — this file's own
#     fixture for a test escaping isolation — printing "NOT A CULPRIT" directly
#     above "this run cannot rank the two hypotheses".
#
# The audit's mutation sweep also found two advertised arms unpinned: deleting
# the whole `remote.*.url|pushurl` hazard clause SURVIVED, and flipping the
# `unrecognised` fall-through to `ordinary` SURVIVED. Both are covered below.

def test_a_MIXED_global_and_repo_local_delta_leads_with_the_GLOBAL_class(tmp_path):
    """🔴 REGRESSION for #773's first audit finding. RED at a7499d67.

    A target that writes `core.hooksPath` into a scratch `~/.gitconfig` AND one
    `branch.*` key into the clone. The global write is attributable by
    construction — no concurrent worktree operation touches the operator's
    global config — so the lead must send the reader at the target, not excuse
    it. Before this round the very same run led with NOT A CULPRIT.
    """
    scratch = _scratch_root(tmp_path)
    home = tmp_path / "scratch-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text("[user]\n\tname = before\n", encoding="utf-8")
    name = "g10-mixed.sh"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        f'git -C "{scratch}" config branch.topic-m.remote origin\n'
        "env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CONFIG_NOSYSTEM "
        f'HOME="{home}" git config --global core.hooksPath /tmp/planted-by-a-test\n',
        encoding="utf-8")
    runner = _runner_over(tmp_path, scratch, [name])

    env = {**os.environ, **_unguarded_home(tmp_path), "HOME": str(home)}
    for k, v in list(env.items()):
        if v is None:
            del env[k]
    proc = subprocess.run(["bash", str(runner), str(scratch)], capture_output=True,
                          text=True, timeout=900, cwd=str(REPO_ROOT), env=env)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a mixed global + repo-local run PASSED:\n{out}"

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    # Positive control: BOTH classes really are in this failure. Without it a
    # green here could just mean the global write never landed.
    assert "global-enforced" in block, (
        f"the global write never reached the failure — this run is not the "
        f"mixed case it claims to measure:\n{block}")
    assert "repo-local-enforced" in block, (
        f"the repo-local write never reached the failure:\n{block}")
    assert "GLOBAL ONE, WHICH *IS* ATTRIBUTABLE" in block, (
        f"the mixed run did not lead with the attributable class:\n{block}")
    assert "AUDIT THIS TARGET FIRST" in block, (
        f"the mixed run did not send the reader at the target:\n{block}")
    assert "WINDOW, NOT A CULPRIT" not in block, (
        f"the mixed run excused the target while the operator's GLOBAL config "
        f"was being rewritten — this is #773's first audit finding:\n{block}")


def test_an_UNRECOGNISED_delta_is_ranked_as_NEITHER_in_the_HEADLINE(tmp_path):
    """🔴 REGRESSION for #773's second audit finding. RED at a7499d67.

    `_nogit_delta_shape`'s header says an unknown key "must not be laundered
    into 'probably concurrent'". The per-file line honoured that; the HEADLINE
    did not, so the two contradicted each other on the same screen — and the
    key that triggers it here is `devrc-g10.planted`, this file's own model of a
    fixture write escaping isolation.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_write(scratch)          # writes devrc-g10.planted
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"an unrecognised repo-local delta PASSED:\n{out}"

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert "+ devrc-g10.planted" in block, (
        f"the failure did not name the key that moved:\n{block}")
    assert "SHAPE: UNRECOGNISED" in block, (
        f"an unknown key was classified rather than declined:\n{block}")
    assert "CANNOT RANK THE TWO HYPOTHESES" in block, (
        f"the headline ranked a delta the classifier declined to rank:\n{block}")
    assert "WINDOW, NOT A CULPRIT" not in block, (
        f"the headline handed the reader the reassuring lead over an "
        f"unrecognised key — this is #773's second audit finding:\n{block}")


@pytest.mark.parametrize("key,value", [
    ("remote.origin.url", "https://example.invalid/x"),
    ("remote.origin.pushurl", "https://example.invalid/y"),
    ("remote.origin.uploadpack", "/tmp/planted-uploadpack"),
    ("remote.origin.receivepack", "/tmp/planted-receivepack"),
    ("submodule.mod.update", "!/tmp/planted-update"),
])
def test_remote_and_submodule_COMMAND_and_URL_keys_rank_HAZARD(tmp_path, key, value):
    """🔴 THE ARM THE AUDIT'S SWEEP FOUND UNPINNED — one case per key.

    🔴 THE PARAMS SPLIT INTO TWO DIFFERENT CLAIMS AND THE LABEL MATTERS.
    MEASURED at a7499d67: `url` and `pushurl` PASS there, the other three FAIL.

      * `uploadpack`, `receivepack`, `submodule.<n>.update` are REGRESSION
        coverage. They ranked ORDINARY at a7499d67 — the reassuring arm — while
        being exactly the arbitrary-command-execution keys a test escaping
        isolation would write. `remote.*` was an unanchored prefix.
      * `url` and `pushurl` are MUTATION coverage, NOT regression coverage. The
        hazard clause already covered them; deleting it whole nonetheless
        SURVIVED the previous round's suite, because the only test that wrote
        such a key asserted the key name and the redaction and never the shape.
        Counting these two as regression coverage would be a coverage claim
        nobody measured.

    Parametrized so a single surviving key cannot hide behind its siblings.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_keys(scratch, (key, value))
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"a {key} write PASSED:\n{out}"

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert f"+ {key}" in block, (
        f"the failure did not name {key}:\n{block}")
    assert "SHAPE: HAZARD" in block, (
        f"{key} was not ranked as a hazard — it names a remote or executes a "
        f"command, and nothing routine writes it into an existing clone:\n{block}")
    assert "WINDOW, NOT A CULPRIT" not in block, (
        f"{key} got the reassuring lead:\n{block}")


def test_a_key_with_a_SPACE_survives_the_fold_and_still_ranks_HAZARD(tmp_path):
    """🔴 A git key can contain a SPACE, and the fold used to eat it.

    `[remote "my name"]` is legal — `git config 'remote.my name.url' <u>` exits
    0 — and `git submodule add <url> "my dir"` produces the same shape. Folding
    the `--list -z` output on a SPACE truncated the key to `remote.my`: a key
    that does not exist, ungreppable, and one the `remote\\..*\\.url$` hazard
    clause cannot match. The single key this guard names as the token-bearing
    hazard was the one a space defeated.
    """
    scratch = _scratch_root(tmp_path)
    name = "g10-spacekey.sh"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        f"git -C \"{scratch}\" config 'remote.my name.url' "
        "'https://example.invalid/spaced'\n",
        encoding="utf-8")
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    # Positive control: git really accepted the spaced subsection, so a pass
    # below is about the fold and not about a write that never happened.
    assert "my name" in (scratch / ".git" / "config").read_text(encoding="utf-8"), (
        "git did not write the spaced subsection — this run proves nothing")
    assert proc.returncode != 0, f"a spaced-key write PASSED:\n{out}"

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert "+ remote.my name.url" in block, (
        f"the key was truncated at its space instead of printed whole:\n{block}")
    assert "SHAPE: HAZARD" in block, (
        f"a spaced remote URL key escaped the hazard rule:\n{block}")


def test_a_LARGE_delta_still_reaches_the_hazard_arm(tmp_path):
    """🔴 SIGPIPE + `set -o pipefail` silently sent every large delta to the
    REASSURING arm. RED at a7499d67.

    `printf '%s\\n' "$delta" | grep -q …`: `grep -q` exits on the first match,
    `printf` then takes SIGPIPE (141), and `pipefail` reports the PIPELINE as
    141 — so the `if` goes FALSE even though the pattern matched. Both greps
    fell through and `_nogit_delta_shape` returned `ordinary`. The audit
    measured the cliff between 5001 lines (still correct) and 8001 (wrong),
    reproducible 10/10; herestrings remove the pipeline entirely.

    🔴 THE HAZARD KEY MUST SORT EARLY, and getting that wrong is how this test
    first shipped green at base while proving nothing. `_nogit_key_delta` ends
    in `sort -k2`, so a `core.*` key lands AFTER 15000 `bigsect.*` lines —
    `grep -q` then reads almost the whole input, never exits early, and the
    SIGPIPE never happens. Measured: that version PASSED at a7499d67. The key
    here is `alias.*` — also in the hazard set, and it sorts before everything
    else in the fixture, so `grep -q` matches on the first line and abandons
    ~300 KB of unread input. That is the shape the bug needs.
    """
    scratch = _scratch_root(tmp_path)
    name = "g10-bigdelta.sh"
    cfg = scratch / ".git" / "config"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        f'cfg={shlex.quote(str(cfg))}\n'
        '{ echo "[alias]"; echo "\tplantedcmd = !/tmp/planted-by-a-test"\n'
        '  echo "[bigsect]"\n'
        '  i=0; while [ "$i" -lt 15000 ]; do echo "\tkey$i = v$i"; i=$((i+1)); done\n'
        '} >> "$cfg"\n',
        encoding="utf-8")
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    # Positive control: the delta really is past the cliff, so a pass below is
    # about the pipeline and not about a fixture that stayed small.
    entries = cfg.read_text(encoding="utf-8").count("key")
    assert entries >= 15000, (
        f"the fixture only produced {entries} keys — too small to reach the "
        f"measured SIGPIPE cliff, so this run proves nothing")
    assert proc.returncode != 0, f"a 15000-key config delta PASSED:\n{out[-3000:]}"

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out[-3000:]}"
    assert "+ alias.plantedcmd" in block, (
        f"the hazard key was not listed among the 15000:\n{block[:2000]}")
    assert "SHAPE: HAZARD" in block, (
        f"an alias.* write at the head of a large delta was ranked as "
        f"something other than a hazard — SIGPIPE swallowed the match:"
        f"\n{block[:2000]}")
    assert "WINDOW, NOT A CULPRIT" not in block, (
        f"a large delta carrying an alias.* command got the reassuring lead:"
        f"\n{block[:2000]}")


def test_a_LARGE_unrecognised_delta_is_not_flattened_to_ORDINARY(tmp_path):
    """🔴 THE MIRROR OF THE TEST ABOVE, AND THE SWEEP IS WHY IT EXISTS.

    `_nogit_delta_shape` has TWO `grep -q` calls and the test above only reaches
    the first. Mutating the SECOND back to `printf | grep -q` SURVIVED a fully
    green run: nothing exercised a large delta that gets PAST the hazard arm.

    That path is the more dangerous one. `grep -qv ORDINARY` matches on the
    first non-ordinary line, `printf` takes SIGPIPE, `pipefail` reports 141, the
    `if` goes false and the function falls through to `ordinary` — so a delta of
    15000 keys the classifier does not recognise would be announced as ordinary
    git activity with the target ranked SECOND. Unknown keys laundered into the
    reassuring arm, in bulk.

    `aaasect.*` is neither ordinary nor hazard and sorts first, so the `-qv`
    match happens on line one and abandons the rest.
    """
    scratch = _scratch_root(tmp_path)
    name = "g10-bigunknown.sh"
    cfg = scratch / ".git" / "config"
    (scratch / name).write_text(
        "set -euo pipefail\n"
        f'cfg={shlex.quote(str(cfg))}\n'
        '{ echo "[aaasect]"\n'
        '  i=0; while [ "$i" -lt 15000 ]; do echo "\tkey$i = v$i"; i=$((i+1)); done\n'
        '} >> "$cfg"\n',
        encoding="utf-8")
    runner = _runner_over(tmp_path, scratch, [name])

    proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr
    entries = cfg.read_text(encoding="utf-8").count("key")
    assert entries >= 15000, (
        f"the fixture only produced {entries} keys — too small to reach the "
        f"measured SIGPIPE cliff, so this run proves nothing")
    assert proc.returncode != 0, f"a 15000-key config delta PASSED:\n{out[-3000:]}"

    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out[-3000:]}"
    assert "+ aaasect.key0" in block, (
        f"the unknown keys were not listed:\n{block[:2000]}")
    assert "SHAPE: UNRECOGNISED" in block, (
        f"15000 unknown keys were classified as something the runner claims to "
        f"recognise — the ordinary grep's match was swallowed:\n{block[:2000]}")
    assert "CANNOT RANK THE TWO HYPOTHESES" in block, (
        f"the headline ranked a delta of unknown keys:\n{block[:2000]}")


def test_the_shape_ledger_is_pinned_two_way():
    """🔴 THE CLASSIFIER'S TWO SETS ARE A LEDGER, not prose plus a regex.

    Same shape this repo already uses for `EXPECTED_PLUGINS`, `TARGET_FLOORS`
    and drift-check's phase-2 reason tokens. The sets decide which writes get
    the reassuring headline, so silently widening `ordinary` — or narrowing
    `hazard` — must fail the suite, not merely change a message nobody reads
    until the next incident.

    Both directions: a token added to the runner and not here fails, and a
    token removed from the runner while still listed here fails.
    """
    src = RUN_TESTS.read_text(encoding="utf-8")

    def _assignment(name: str) -> str:
        m = re.search(rf"^{name}='([^']*)'$", src, re.M)
        assert m, (
            f"{name} is not a single-quoted one-line assignment in "
            f"run-tests.sh any more — this ledger cannot read it, so it is "
            f"pinning nothing. Re-point it or restore the shape.")
        return m.group(1)

    assert _assignment("NOGIT_HAZARD_KEYS") == (
        r"^[+~-] (core|user|url|http|credential|include|includeif|alias)\."
        r"|^[+~-] (remote|submodule)\..*\.(url|pushurl|uploadpack|receivepack|proxy|update)$"
    ), ("the HAZARD key set moved. Every key it drops starts getting the "
        "'concurrent writer is the leading hypothesis' headline instead of "
        "'AUDIT THIS TARGET FIRST'. Update this ledger in the SAME commit, and "
        "add a behavioural case for the new key.")

    assert _assignment("NOGIT_ORDINARY_KEYS") == (
        r"^[+~-] (branch|remote|worktree|submodule|maintenance)\."
        r"|^[+~-] extensions\.worktreeconfig$"
    ), ("the ORDINARY key set moved. Every key it gains gets the reassuring "
        "headline. Update this ledger in the SAME commit.")


def test_a_global_change_FAILS_even_with_a_cotenant_PROVEN(tmp_path):
    """🔴 NEGATIVE CONTROL on the CLASS BOUNDARY — NOT regression coverage.

    Same label as the control above: base already failed this run, so its
    base-red is about the new wording, not about behaviour.

    Co-tenancy licenses a downgrade for the repo-local file and for NOTHING
    else. `~/.gitconfig` is the 2026-08-21 incident's actual shape; a fix that
    let proven co-tenancy suppress it would have removed the guard's teeth while
    reading, in the output, exactly like the fix that keeps them.

    Measured against a SCRATCH home, never the operator's.
    """
    scratch = _scratch_root(tmp_path)
    home = tmp_path / "scratch-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / ".gitconfig").write_text("[user]\n\tname = pre-existing\n",
                                     encoding="utf-8")
    name = _plant_global_write(scratch, home)
    runner = _runner_over(tmp_path, scratch, [name])

    with _cotenant(scratch):
        proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        f"a GLOBAL config change was downgraded by co-tenancy:\n{out}")
    assert "GUARD 10 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    assert "global-enforced" in out, (
        f"the failure did not classify the change as GLOBAL:\n{out}")
    block = _guard10_failure_block(out)
    assert block, f"GUARD 10's failure block was not printed at all:\n{out}"
    assert str(home / ".gitconfig") in block, (
        f"the failure did not NAME the file that changed:\n{block}\n---\n{out}")
    # The positive control for the assertion above: the write really happened.
    assert "planted-by-a-test" in (home / ".gitconfig").read_text(encoding="utf-8")


def test_a_broken_evidence_probe_fails_TOWARD_enforcing(tmp_path):
    """🔴 A BROKEN PROBE MUST NOT SILENTLY DISABLE THE GUARD.

    The downgrade is licensed by evidence collected from `testlib.gitenv`. If
    that collection cannot run — no python, an import error, a helper that
    raises — the honest answer is "no writer was PROVEN", i.e. enforce. A probe
    whose failure read as proof would be a guard switched off by a typo.

    Its base-red is STRUCTURAL, not behavioural: base has no probe, so the
    mutation anchor is simply absent. Its branch is proved REACHABLE by the
    mutation that makes the failure path return `proven`, which turns it red.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_write(scratch)
    runner = _runner_over(tmp_path, scratch, [name])

    src = runner.read_text(encoding="utf-8")
    assert src.count(_G10_PROBE_IMPORT) == 1, (
        "expected exactly one live import in the evidence probe to break; "
        "found a different number, so this mutation would not land")
    runner.write_text(
        src.replace(_G10_PROBE_IMPORT, "import testlib.no_such_module_g10"),
        encoding="utf-8")
    assert "no_such_module_g10" in runner.read_text(encoding="utf-8"), (
        "the mutation did not land — a non-matching anchor scores a bogus pass")

    with _cotenant(scratch):
        proc = _run_at(runner, scratch, tmp_path)
    out = proc.stdout + proc.stderr

    assert proc.returncode != 0, (
        "a BROKEN evidence probe downgraded a repo-local change — the guard was "
        f"switched off by an import error:\n{out}")
    assert "evidence=probe-failed" in out, (
        f"the run failed, but not because the probe could not run:\n{out}")
    # Its two siblings pin this; without it "the run went red" is satisfied by
    # the runner being red about anything at all.
    assert "GUARD 10 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    assert "repo-local-reported-total=0" in out, (
        f"a failed probe must downgrade NOTHING:\n{out}")


# --------------------------------------------------------------------------- #
# 🔴 THE PROBE'S OUTPUT IS EVIDENCE ONLY IF THE PROBE WROTE IT
# --------------------------------------------------------------------------- #
# The downgrade used to be decided by `[ -s "$out" ]` — output PRESENCE. The
# probe inherits the ambient `PYTHONPATH`, so ANY stdout on that path counted:
# a package that prints at import, a `.pth`, a `sitecustomize`. Measured with NO
# co-tenant present, a single `print()` in `scripts/testlib/__init__.py`
# produced `repo-local-reported=1` and downgraded a genuinely attributable
# write, rendering the stray line as GUARD 9 evidence. That is a downgrade
# WITHOUT PROOF, which is the one thing this design promises cannot happen.
#
# Both tests below run with NO co-tenant, so the correct verdict is FAIL in
# each; a PASS means the guard was switched off by somebody else's print().
def _plant_chatty_import(tmp_path: Path, text: str) -> tuple[Path, Path]:
    """A real `sitecustomize` on `PYTHONPATH` that writes `text` to stdout.

    Injected via `PYTHONPATH`, never by editing this repo's `scripts/testlib/`:
    the probe prepends `$ROOT/scripts` and KEEPS whatever it inherited, and that
    inherited half is precisely the surface under test. `sitecustomize` is
    imported by `site` at interpreter startup, so this is a genuine
    print-at-import, not a stub of one.

    🔴 SCOPED TO THE PROBE by `sys.argv`, deliberately. Printing from EVERY
    python the runner starts would corrupt GUARD 8's captured
    `SPOOL_TRAP_LOG` command substitution and abort the run `exit 2` — the test
    would then be red for a NEIGHBOUR's reason while asserting this one's, which
    is the failure mode this whole file exists to avoid.

    Returns `(pythonpath_dir, witness)`. The witness is the positive control:
    it proves the module actually loaded INSIDE the probe process, so a red
    verdict cannot be scored as "the token worked" when nothing ever printed.
    """
    noisy = tmp_path / "noisy-pythonpath"
    noisy.mkdir(parents=True, exist_ok=True)
    witness = tmp_path / "noise-witness.txt"
    (noisy / "sitecustomize.py").write_text(
        "import os, sys\n"
        "if any('DEVRC-NOGIT-EVIDENCE' in a for a in sys.argv):\n"
        f"    open({str(witness)!r}, 'a').write('fired\\n')\n"
        f"    sys.stdout.write({text!r})\n",
        encoding="utf-8")
    return noisy, witness


def _run_at_with_env(runner: Path, scratch: Path, tmp_path: Path,
                     extra: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **_unguarded_home(tmp_path), **extra}
    for k, v in list(env.items()):
        if v is None:
            del env[k]
    return subprocess.run(["bash", str(runner), str(scratch)], capture_output=True,
                          text=True, timeout=900, cwd=str(REPO_ROOT), env=env)


def test_a_stray_print_on_the_probes_pythonpath_is_NOT_evidence(tmp_path):
    """🔴 REGRESSION for the measured fail-open. Not a stub: a real module on a
    real `PYTHONPATH`, printing at import, exactly as the audit reproduced it.

    With no co-tenant, the repo-local write is attributable and MUST fail. If
    the stray line is accepted as evidence, the run goes green instead.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_write(scratch)
    runner = _runner_over(tmp_path, scratch, [name])
    # The text is deliberately the EXACT wording GUARD 9 uses, so nothing but
    # the token can distinguish it from real evidence.
    noisy, witness = _plant_chatty_import(
        tmp_path,
        "live processes are sitting inside a protected repository (cwd), "
        "and none of them is ours: 1:fake\n")

    assert not live_cotenants([scratch / ".git"]), (
        "something is already sitting in this scratch root, so the 'no proven "
        "writer' premise of this test does not hold")
    proc = _run_at_with_env(runner, scratch, tmp_path,
                            {"PYTHONPATH": str(noisy)})
    out = proc.stdout + proc.stderr

    # 🔴 POSITIVE CONTROL FOR THE FIXTURE ITSELF — the noise really loaded
    # inside the probe process. Without this, a run that went red because the
    # module never imported would be scored as "the token rejected it".
    assert witness.exists() and witness.read_text(encoding="utf-8").strip(), (
        "the chatty sitecustomize never ran inside the probe, so this test "
        f"measured nothing:\n{out}")
    assert proc.returncode != 0, (
        "a stray print() on the probe's PYTHONPATH was accepted as co-tenancy "
        f"evidence and downgraded an ATTRIBUTABLE write:\n{out}")
    assert "GUARD 10 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    assert "repo-local-reported-total=0" in out, (
        f"an unstamped line must downgrade NOTHING:\n{out}")
    assert "evidence=none" in out, (
        f"the run did not record the probe as having returned no evidence:\n{out}")


def test_whitespace_only_probe_stdout_is_NOT_evidence(tmp_path):
    """🔴 The second half of the same site: `NOGIT_EV_STATUS` used to be set
    BEFORE the loop that skips blank lines, so whitespace-only stdout produced
    `proven` with ZERO logged reasons — a downgrade whose stated cause was the
    empty string.
    """
    scratch = _scratch_root(tmp_path)
    name = _plant_repo_local_write(scratch)
    runner = _runner_over(tmp_path, scratch, [name])
    noisy, witness = _plant_chatty_import(tmp_path, "   \n\t\n\n")

    assert not live_cotenants([scratch / ".git"])
    proc = _run_at_with_env(runner, scratch, tmp_path,
                            {"PYTHONPATH": str(noisy)})
    out = proc.stdout + proc.stderr

    assert witness.exists() and witness.read_text(encoding="utf-8").strip(), (
        f"the whitespace-emitting sitecustomize never ran in the probe:\n{out}")
    assert proc.returncode != 0, (
        f"whitespace-only probe stdout was accepted as evidence:\n{out}")
    assert "GUARD 10 problem" in out, (
        f"the run failed, but not for the git-config reason:\n{out}")
    assert "repo-local-reported-total=0" in out, (
        f"blank lines must downgrade NOTHING:\n{out}")
    assert "evidence=none" in out, (
        f"the run did not record the probe as having returned no evidence:\n{out}")


def test_a_LINKED_WORKTREE_protects_the_COMMON_config(tmp_path):
    """🔴 THE PRODUCTION SHAPE, which every other test here approximates.

    #730 is about a clone with ~122 linked worktrees; the suite normally runs
    from one of them, not from the main clone. In that shape
    `rev-parse --git-common-dir` resolves to the MAIN clone's `.git`, so the
    protected repo-local file is the SHARED config — the one with other writers
    — and not anything under `.git/worktrees/<name>/`.

    This pins that the classifier and the evidence probe agree about which file
    that is. Without it, the whole class could be keyed on a path that only
    exists in the non-worktree shape and every test here would still pass.
    """
    main = _scratch_root(tmp_path)
    # A worktree needs a commit to check out.
    for cmd in (["git", "-C", str(main), "commit", "-q", "--allow-empty",
                 "-m", "base", "--no-gpg-sign"],):
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "GIT_AUTHOR_NAME": "t",
                                   "GIT_AUTHOR_EMAIL": "t@example.invalid",
                                   "GIT_COMMITTER_NAME": "t",
                                   "GIT_COMMITTER_EMAIL": "t@example.invalid"})
        assert done.returncode == 0, f"could not seed the scratch repo:\n{done.stderr}"

    linked = tmp_path / "linked-wt"
    add = subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach",
                          "-q", str(linked)], capture_output=True, text=True,
                         timeout=180)
    assert add.returncode == 0, f"could not create the linked worktree:\n{add.stderr}"
    # The runner needs `$ROOT/scripts` to resolve; the worktree holds only the
    # (empty) commit, so the same symlink farm is laid over it.
    for entry in REPO_ROOT.iterdir():
        if entry.name == ".git" or (linked / entry.name).exists():
            continue
        (linked / entry.name).symlink_to(entry)

    common = main / ".git" / "config"
    writer = "g10-write-common.sh"
    (linked / writer).write_text(
        "set -euo pipefail\n"
        f'git -C "{linked}" config devrc-g10.planted yes\n',
        encoding="utf-8")
    target = tmp_path / "plain_tests"
    write_pytest_suite(target, 2, prefix="test_plain")
    runner = runner_with_targets(tmp_path, [str(target)], {str(target): 1},
                                 hook_tests=[], shell_tests=[writer])

    with _cotenant(main):                      # the co-tenant sits in the MAIN tree
        proc = _run_at(runner, linked, tmp_path)
    out = proc.stdout + proc.stderr

    # The write landed in the COMMON config, not in the worktree's own git dir.
    assert "planted" in common.read_text(encoding="utf-8"), (
        "the planted write did not reach the common config — this test's "
        "premise about `git config` in a linked worktree is wrong")
    assert f"present {common}  [repo-local" in out, (
        f"the common config was not classified repo-local from a linked "
        f"worktree:\n{out}")
    assert proc.returncode == 0, (
        f"a repo-local change in a LINKED WORKTREE, with a proven co-tenant in "
        f"the main tree, still failed the run:\n{out}")
    assert re.search(r"repo-local-reported-total=1(?![0-9])", out), (
        f"the linked-worktree downgrade was not counted:\n{out}")
