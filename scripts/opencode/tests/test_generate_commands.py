"""Tests for scripts/opencode/generate-commands.py — and above all for its
refusal to write into a home-manager-managed directory.

🔴 WHY THE REFUSAL EXISTS, measured rather than imagined. opencode log,
2026-08-19T21:39:22.454Z, run=146c5448, cwd=/home/zach/workspace/devrc:

    python3 .../scripts/opencode/generate-commands.py .../claude/skills \\
        ~/.config/opencode/commands

Its three previous runs that minute used /tmp/test-commands. This one pointed the
OUTPUT DIRECTORY at the live home-manager deploy path, and the 34 command files'
mtimes are 21:39:22.484–.487 — 30ms later. Every home-manager symlink there
became a plain regular file.

home-manager could not undo it. `force = true` suppresses only the collision
CHECK, and the link step skips a non-symlink target whose content is identical to
the source ("Skipping '$targetPath' as it is identical"). 18 of the 34 were still
regular files two days and two generations later — exactly the 18 whose skill
body had not changed since, so the content still matched.

Hermetic: every fixture is built in tmp_path with a fake $HOME and a fake store
prefix. Nothing reads the operator's real profile or a real /nix/store.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GEN = REPO_ROOT / "scripts" / "opencode" / "generate-commands.py"

EXIT_MANAGED_OUTPUT = 3


def skills_tree(root, names=("alpha", "beta")):
    src = root / "skills"
    for n in names:
        d = src / n
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: %s\ndescription: does %s things\n---\nbody of %s\n" % (n, n, n)
        )
    return src


def run(src, out, *, home=None, store_prefix=None, extra_env=None):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if home is not None:
        env["HOME"] = str(home)
        env.pop("XDG_STATE_HOME", None)
    # 🔴 POP BEFORE THE CONDITIONAL SET, unconditionally. This env is a COPY of
    # the caller's, and `GENERATE_COMMANDS_STORE_PREFIX` is a real override the
    # generator honours (`_default_prefix()` pops it for exactly this reason).
    # Without this line the tests that deliberately pass no `store_prefix` — the
    # ones whose whole claim is "with NO prefix override, the DEFAULT prefix is
    # what applies" — would silently measure whatever the ambient shell exported,
    # and would still be green. An AMBIENT value is not a default.
    env.pop("GENERATE_COMMANDS_STORE_PREFIX", None)
    if store_prefix is not None:
        env["GENERATE_COMMANDS_STORE_PREFIX"] = str(store_prefix)
    if extra_env:
        env.update(extra_env)
    p = subprocess.run([sys.executable, str(GEN), str(src), str(out)],
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


# --- the positive control: it still does its job ---------------------------- #
def test_a_plain_output_directory_is_generated_normally(tmp_path):
    """🔴 FIRST, PROVE THE GENERATOR STILL WORKS. A guard that refuses
    everything passes every refusal test and ships a broken build, and the nix
    derivation calls this with a store `$out` on every switch."""
    src = skills_tree(tmp_path)
    out = tmp_path / "cmds"
    rc, log = run(src, out, home=tmp_path / "fakehome")
    assert rc == 0, log
    assert sorted(p.name for p in out.iterdir()) == ["alpha.md", "beta.md"], log
    assert "body of alpha" in (out / "alpha.md").read_text()
    assert (out / "alpha.md").read_text().startswith(
        "---\ndescription: does alpha things\n---\n")


def test_the_nix_build_shape_is_not_refused(tmp_path):
    """The derivation runs with `$out` a FRESH EMPTY directory outside $HOME and
    no home-manager profile in the sandbox. Both signals must be inert, or the
    guard breaks every deploy of the thing it protects. Modelled with
    HOME=/homeless-shelter, which is what nix actually sets."""
    src = skills_tree(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    rc, log = run(src, out, home=Path("/homeless-shelter"))
    assert rc == 0, log
    assert (out / "alpha.md").exists(), log


# --- signal 1: the generation manifest declares the path -------------------- #
def _manifest_home(tmp_path, rel):
    """A $HOME whose active home-manager generation declares $HOME/<rel>."""
    home = tmp_path / "home"
    manifest = (home / ".local" / "state" / "nix" / "profiles"
                / "home-manager" / "home-files")
    (manifest / rel).mkdir(parents=True)
    (manifest / rel / "already.md").symlink_to(tmp_path / "store" / "already.md")
    return home, manifest


def test_a_directory_the_manifest_declares_is_refused(tmp_path):
    """🔴 THE MANIFEST IS THE AUTHORITATIVE SIGNAL, and the one that survives the
    worst case: a directory ALREADY fully overwritten, where no symlink is left
    on disk to notice. That is the state the workbench was in for the 18 files —
    a second run pointed at it would have found nothing to warn from."""
    src = skills_tree(tmp_path)
    home, _ = _manifest_home(tmp_path, ".config/opencode/commands")
    out = home / ".config" / "opencode" / "commands"
    out.mkdir(parents=True)
    (out / "stale.md").write_text("a regular file someone already wrote\n")

    rc, log = run(src, out, home=home)
    assert rc == EXIT_MANAGED_OUTPUT, (
        "expected the managed-output guard's own exit code %d, got %d — a "
        "different code means a DIFFERENT failure killed this, not the guard\n%s"
        % (EXIT_MANAGED_OUTPUT, rc, log))
    assert "refusing to write into" in log, log
    assert "home-files" in log, "the reason does not name the evidence\n" + log
    assert not (out / "alpha.md").exists(), "it wrote anyway\n" + log
    assert (out / "stale.md").read_text() == (
        "a regular file someone already wrote\n"), "it clobbered the existing tree"


def test_the_guard_runs_before_the_mkdir(tmp_path):
    """The refusal must not CREATE the directory it is refusing. Ordering is
    invisible in the happy path and only observable here."""
    src = skills_tree(tmp_path)
    home, _ = _manifest_home(tmp_path, ".config/opencode/commands")
    out = home / ".config" / "opencode" / "commands"
    assert not out.exists()
    rc, log = run(src, out, home=home)
    assert rc == EXIT_MANAGED_OUTPUT, log
    assert not out.exists(), (
        "the guard created the managed directory on its way to refusing it\n" + log)


def test_a_sibling_directory_the_manifest_does_not_declare_is_allowed(tmp_path):
    """🔴 REACHABILITY, from the other side. The guard must be able to say NO
    only about paths the manifest actually names — otherwise it is a rule about
    $HOME, and generating into a scratch dir under $HOME would be refused for a
    reason that is not true."""
    src = skills_tree(tmp_path)
    home, _ = _manifest_home(tmp_path, ".config/opencode/commands")
    out = home / "scratch" / "commands"
    rc, log = run(src, out, home=home)
    assert rc == 0, log
    assert (out / "alpha.md").exists(), log


# --- signal 2: a store symlink is already sitting in the directory ---------- #
def test_a_directory_holding_a_store_symlink_is_refused(tmp_path):
    """The second, INDEPENDENT signal — for the host whose profile is missing or
    unreadable, where the manifest cannot answer. A store symlink in the output
    directory is home-manager's own handwriting."""
    src = skills_tree(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    (store / "activity.md").write_text("deployed\n")
    out = tmp_path / "commands"
    out.mkdir()
    (out / "activity.md").symlink_to(store / "activity.md")

    # No manifest anywhere: HOME points at an empty dir, so signal 1 CANNOT fire
    # and this test is about signal 2 alone. Without that, a mutant deleting the
    # on-disk check would still die — for the wrong reason.
    rc, log = run(src, out, home=tmp_path / "empty-home", store_prefix=store)
    assert rc == EXIT_MANAGED_OUTPUT, (
        "expected exit %d from the store-symlink signal, got %d\n%s"
        % (EXIT_MANAGED_OUTPUT, rc, log))
    assert "already a home-manager symlink" in log, log
    assert not (out / "alpha.md").exists(), "it wrote anyway\n" + log


def test_a_directory_of_ordinary_files_is_not_refused(tmp_path):
    """The negative side of signal 2: regenerating over a previous plain run in
    /tmp is exactly what the script is FOR. Refusing that would push people back
    to pointing it at the live path."""
    src = skills_tree(tmp_path)
    out = tmp_path / "test-commands"
    out.mkdir()
    (out / "old.md").write_text("from a previous run\n")
    (out / "relative-link.md").symlink_to("old.md")
    rc, log = run(src, out, home=tmp_path / "empty-home",
                  store_prefix=tmp_path / "store")
    assert rc == 0, log
    assert (out / "alpha.md").exists(), log


def test_the_refusal_names_the_path_and_offers_the_alternative(tmp_path):
    """A guard that fires with no way forward gets deleted by the next person who
    hits it. Assert the message carries the offending path AND the thing to do
    instead — the whole normalised text, so a reword that drops either half
    fails rather than quietly passing a two-word check."""
    src = skills_tree(tmp_path)
    home, _ = _manifest_home(tmp_path, ".config/opencode/commands")
    out = home / ".config" / "opencode" / "commands"
    out.mkdir(parents=True)
    rc, log = run(src, out, home=home)
    assert rc == EXIT_MANAGED_OUTPUT, log
    flat = " ".join(log.split())
    assert str(out) in flat, "the refusal does not name the directory\n" + flat
    assert "Generate into a temp directory instead" in flat, flat
    assert "opencodeCommands" in flat, (
        "the message does not say who is supposed to deploy the real thing\n" + flat)


# --- the un-guarded behaviours the refusal must not have broken ------------- #
def test_a_skill_without_frontmatter_is_still_skipped(tmp_path):
    """Pre-existing contract, pinned so the guard's arrival cannot have moved
    it: opencode DROPS a SKILL.md with no frontmatter, and this generator
    mirrors that."""
    src = skills_tree(tmp_path)
    bad = src / "gamma"
    bad.mkdir()
    (bad / "SKILL.md").write_text("no frontmatter at all\n")
    out = tmp_path / "cmds"
    rc, log = run(src, out, home=tmp_path / "fakehome")
    assert rc == 0, log
    assert not (out / "gamma.md").exists(), log
    assert "Generated 2 commands, skipped 1" in log, log


def test_a_missing_source_directory_still_exits_1(tmp_path):
    """The pre-existing failure path, pinned so the new guard cannot have moved
    it."""
    out = tmp_path / "cmds"
    rc, log = run(tmp_path / "no-such-skills", out, home=tmp_path / "fakehome")
    assert rc == 1, log
    assert "does not exist" in log, log


def test_the_two_failure_modes_have_DIFFERENT_exit_codes(tmp_path):
    """🔴 THE DISTINCTNESS CLAIM, MEASURED RATHER THAN ASSERTED AGAINST A
    LITERAL. Asserting `rc != EXIT_MANAGED_OUTPUT` with the constant restated in
    this file cannot see the constant CHANGING: a mutant setting it to 1 —
    collapsing the guard onto the ordinary usage failure — survived that, because
    `1 != 3` is a fact about the test file, not about the script.

    So drive BOTH failures in one test and compare the two OBSERVED codes. A
    caller that cannot tell "you pointed me at a managed path" from "your source
    directory is missing" is a caller that will retry the first one.
    """
    src = skills_tree(tmp_path)
    home, _ = _manifest_home(tmp_path, ".config/opencode/commands")
    managed_out = home / ".config" / "opencode" / "commands"
    managed_out.mkdir(parents=True)

    rc_managed, log_managed = run(src, managed_out, home=home)
    rc_missing, log_missing = run(tmp_path / "no-such-skills",
                                  tmp_path / "cmds", home=home)

    assert rc_managed != 0 and rc_missing != 0, (log_managed, log_missing)
    assert rc_managed != rc_missing, (
        "both failures exit %d — the managed-path refusal is indistinguishable "
        "from an ordinary usage error\n%s\n%s"
        % (rc_managed, log_managed, log_missing)
    )
    assert rc_managed == EXIT_MANAGED_OUTPUT, (
        "the managed-output refusal no longer uses its documented exit code "
        "%d, got %d — anything reading for it will now miss it\n%s"
        % (EXIT_MANAGED_OUTPUT, rc_managed, log_managed)
    )


# --- the DEFAULT store prefix, which no test pinned ------------------------- #
#
# 🔴 EVERY OTHER TEST IN THIS FILE OVERRIDES `GENERATE_COMMANDS_STORE_PREFIX`.
# That variable exists only so a fixture can control the prefix, and the
# consequence was that the production DEFAULT was covered by nothing: an
# independent mutation sweep changed it from "/nix/store/" to "/" and the mutant
# SURVIVED the full suite. drift-check.sh has the sibling guard
# (`test_the_managed_prefix_defaults_to_the_nix_store`); this file had none.
#
# Both directions matter, and each is a different live bug:
#   * WIDENING (-> "/") makes every absolute symlink look like a nix deployment,
#     so the generator refuses ordinary output directories — the guard becomes an
#     outage, and the nix build calls this on every switch.
#   * NARROWING (-> a longer or wrong prefix) silently disables the on-disk
#     fallback, which exists precisely for the host whose profile is unreadable
#     and where the manifest signal cannot fire. That failure is invisible: the
#     generator happily overwrites the live deploy path, which is the exact
#     2026-08-19 incident this file was written for.
#
# So the default is pinned twice: as a literal (any change fails) and
# BEHAVIOURALLY in both directions (a store-prefixed link is refused, a
# non-store absolute link is not). The behavioural pair is what makes this more
# than a restatement of the source — a structural check on a constant type-checks
# past a wrong value, and the value IS the guard here.

def _default_prefix():
    """STORE_PREFIX as the script computes it with the variable UNSET."""
    env = dict(os.environ)
    env.pop("GENERATE_COMMANDS_STORE_PREFIX", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    p = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util,importlib.machinery,sys;"
         "s=importlib.util.spec_from_loader('g',"
         "  importlib.machinery.SourceFileLoader('g', sys.argv[1]));"
         "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
         "print(m.STORE_PREFIX)",
         str(GEN)],
        capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip()


def test_the_store_prefix_defaults_to_the_nix_store():
    """The literal half. Read out of the module with the env var UNSET, so it is
    the value production uses and not one the test supplied."""
    assert _default_prefix() == "/nix/store/", (
        "the default store prefix is %r. It decides whether an existing symlink "
        "in the output directory counts as home-manager's handwriting: widen it "
        "and the generator refuses ordinary directories, narrow it and the "
        "on-disk fallback silently stops firing." % _default_prefix()
    )


def test_the_default_prefix_refuses_a_store_link_and_allows_a_foreign_one(tmp_path):
    """🔴 THE BEHAVIOURAL PAIR, run with NO prefix override — the only test here
    that exercises the production default end to end.

    One fixture each side of the boundary, in the same test, because either
    assertion alone is walkable:
      * a symlink to /nix/store/… MUST be refused — kills any narrowing mutant.
      * a symlink to an absolute path that is NOT under /nix/store MUST NOT be
        refused — kills the widening mutant ("/") that survived the sweep.

    The store target does not exist and does not need to: the check reads the
    link's TARGET STRING (`os.readlink`), which is what makes this hermetic —
    nothing here writes to a real /nix/store, and the operator's real store is
    never consulted.
    """
    src = skills_tree(tmp_path)
    home = tmp_path / "empty-home"           # no manifest: signal 2 alone
    home.mkdir()

    refused = tmp_path / "looks-managed"
    refused.mkdir()
    (refused / "activity.md").symlink_to("/nix/store/deadbeef-hm_activity.md")
    rc, log = run(src, refused, home=home)
    assert rc == EXIT_MANAGED_OUTPUT, (
        "a symlink into /nix/store/ was NOT refused under the default prefix "
        "(exit %d) — the on-disk fallback is off, and it is the only signal on a "
        "host whose profile is unreadable\n%s" % (rc, log)
    )
    assert not (refused / "alpha.md").exists(), "it wrote anyway\n" + log

    allowed = tmp_path / "ordinary"
    allowed.mkdir()
    (allowed / "activity.md").symlink_to(tmp_path / "somewhere" / "else.md")
    rc, log = run(src, allowed, home=home)
    assert rc == 0, (
        "an absolute symlink that is NOT under /nix/store was refused (exit %d). "
        "The prefix has been widened; the guard is now an outage, and the nix "
        "build calls this on every switch.\n%s" % (rc, log)
    )
    assert (allowed / "alpha.md").exists(), (
        "the run was allowed but produced nothing\n" + log
    )


def test_the_harness_does_not_leak_an_AMBIENT_prefix_into_a_no_override_run(
        tmp_path, monkeypatch):
    """🔴 VALIDATE THE HARNESS, not the code under test.

    `run()` builds the child env from `dict(os.environ)`. Every test above that
    passes no `store_prefix` claims to measure the PRODUCTION DEFAULT — and until
    `run()` popped the variable, an operator (or a sibling test, or a systemd
    unit) that had `GENERATE_COMMANDS_STORE_PREFIX` exported would have had that
    value silently substituted for the default, with every one of those tests
    still green. An ambient value is not a default.

    The control is mechanical and cannot be satisfied by the constant's own
    value: the ambient prefix planted here is `/definitely-not-the-store/`, which
    `/nix/store/deadbeef-…` cannot match. If it leaked, the store link below
    would NOT be refused and this test goes red on its own assertion.
    """
    monkeypatch.setenv("GENERATE_COMMANDS_STORE_PREFIX", "/definitely-not-the-store/")
    src = skills_tree(tmp_path)
    home = tmp_path / "empty-home"
    home.mkdir()
    out = tmp_path / "looks-managed"
    out.mkdir()
    (out / "activity.md").symlink_to("/nix/store/deadbeef-hm_activity.md")

    rc, log = run(src, out, home=home)            # no store_prefix= on purpose
    assert rc == EXIT_MANAGED_OUTPUT, (
        "with GENERATE_COMMANDS_STORE_PREFIX exported in the AMBIENT env, a "
        "no-override run behaved as if the prefix were %r (exit %d). run() is "
        "leaking os.environ, so every 'run with NO prefix override' test in this "
        "file measures the shell rather than the default.\n%s"
        % ("/definitely-not-the-store/", rc, log)
    )

    # And the same env DOES reach the child when the test asks for it — otherwise
    # the assertion above would pass for a `run()` that scrubbed the whole
    # environment, which would prove nothing about the pop.
    allowed = tmp_path / "ordinary"
    allowed.mkdir()
    (allowed / "activity.md").symlink_to("/definitely-not-the-store/x.md")
    rc, log = run(src, allowed, home=home,
                  store_prefix="/definitely-not-the-store/")
    assert rc == EXIT_MANAGED_OUTPUT, (
        "an EXPLICIT store_prefix= no longer reaches the child — run() is now "
        "scrubbing rather than overriding, and the pop above proves nothing "
        "(exit %d)\n%s" % (rc, log)
    )
