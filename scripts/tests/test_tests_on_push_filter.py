"""Guards for `githooks/tests-on-push.sh`'s push filter — the tier that decides
whether the devrc suite runs at all.

WHY THIS FILE EXISTS
--------------------
🔴 THE GATE THAT FOUR COMMENTS CALL "EVERY PUSH" HAD NEVER RUN ON ONE. Measured
2026-08-21 on the workbench, three independent facts:

  1. `flake.nix` runs `run-tests.sh --set hermetic`, and `DEVHOST_TARGETS` is
     appended only in the `all` branch — so `nix build .#checks…pytests` never
     collects `scripts/tests-devhost`.
  2. The only automated `--set all` is this hook, and no pre-push hook was
     installed: `core.hooksPath` pointed at `.git/hooks`, which held 14 files,
     all `*.sample`. (Root cause: `githooks/install.sh` could only write the
     GLOBAL git config, which on a home-manager host is a read-only nix-store
     symlink — see test_githooks_install.py.)
  3. Even once installed, the changed-files filter was
     `^(scripts/|flake\\.nix$|flake\\.lock$)`. `nix/` was absent, so a push
     touching only `nix/home.nix` — the file that DECLARES the activation entry
     `scripts/tests-devhost/test_activation_order.py` measures — printed
     "no Python/test/flake changes … skipping the test gate" and exited 0.

Fact 3 is what this file pins, and it pins the SHAPE of the fix rather than the
one path that was missing: the allowlist is gone. Four gates in this repo
(`test_no_captured_text.py`, `test_no_captured_markup.py`, `test_no_public_ips.py`,
`test_no_client_hostnames.py`) enumerate `git ls-files` and scan EVERY tracked
file, `.md` included — their pinned allowlists name `claudedocs/*.md` entries —
so a push touching only a handoff doc can genuinely red the suite. There is no
inert prefix in this repo to filter on.

WHAT IS REGRESSION COVERAGE HERE AND WHAT IS NOT
------------------------------------------------
`test_a_push_touching_only_nix_home_nix_RUNS_the_gate` and
`test_a_push_touching_only_a_claudedocs_note_RUNS_the_gate` are REGRESSION
coverage: both were measured SKIP against the pre-fix script and are RUN after
it, and both drive a REAL git repository with real commits, so they stay
meaningful if a file-based filter is ever reintroduced.

`test_a_pure_ref_delete_SKIPS_the_gate` is the NEGATIVE CONTROL, not coverage of
a bug: without it a filter wired to `return 0` unconditionally would satisfy
every other test here. It is the only thing proving this harness can observe a
SKIP at all.

The remaining cases (no stdin, malformed line) are INVARIANT GUARDS on the
"fail toward running" contract in the script's header. Nothing ever violated
them; they are here so a future rewrite cannot quietly invert the default.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "githooks" / "tests-on-push.sh"

ZERO = "0" * 40


def _devrc_shaped(root: Path) -> Path:
    """The minimum tree `tests-on-push.sh` accepts as "this is devrc".

    Its applicability gate is `[ -f scripts/run-tests.sh ]` plus a `flake.nix`
    containing `DEVRC`. Built rather than pointed at the real repo so a decision
    can never depend on the state of the checkout the suite is running from.
    """
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run-tests.sh").write_text(
        "#!/usr/bin/env bash\necho 'the real runner must never be reached here'\nexit 99\n")
    (root / "flake.nix").write_text("# DEVRC marker for the applicability gate\n")
    return root


def _decide(repo: Path, stdin_data: str, home: Path) -> str:
    """Run the REAL hook in dry-run mode and return its DECISION token.

    `DEVRC_TESTS_ON_PUSH_DECIDE_ONLY=1` stops the script before it builds a
    nix-shell or runs anything, which is what makes the decision drivable from a
    hermetic test instead of re-implemented here.
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["DEVRC_TESTS_ON_PUSH_DECIDE_ONLY"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 🔴 The mode is pinned and the config file is pointed at nothing. Both are
    # load-bearing: this script reads `$HOME/.claude/audit-on-push.env`, so a
    # dev host with `TESTS_ON_PUSH=off` in it would make every case below exit
    # before deciding — and every assertion would then be about a run that
    # measured nothing.
    env["TESTS_ON_PUSH"] = "on"
    env["TESTS_ON_PUSH_CONF_FILE"] = str(home / "no-such-conf.env")
    env.pop("DEVRC_SKIP_TESTS", None)
    p = subprocess.run(["bash", str(HOOK), str(repo)], input=stdin_data,
                       capture_output=True, text=True, env=env, timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode == 0, "the dry run itself failed (%d):\n%s" % (p.returncode, out)
    for line in p.stdout.splitlines():
        if line.startswith("DECISION: "):
            return line.split(": ", 1)[1].strip()
    raise AssertionError(
        "the hook printed no DECISION line in dry-run mode. That is not a "
        "SKIP and not a RUN — it means the dry run exited somewhere earlier "
        "(applicability gate? mode?) and this test measured nothing.\n" + out)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, timeout=60, check=True).stdout.strip()


@pytest.fixture()
def pushrepo(tmp_path):
    """A real git repo, devrc-shaped, with one commit already 'on the remote'.

    Real commits rather than fabricated shas so the RUN/SKIP cases below still
    exercise a diff if a file-based filter is ever reintroduced — a fixture of
    invented hashes would make such a filter's `git diff` fail and return RUN,
    passing these tests for the wrong reason.
    """
    repo = _devrc_shaped(tmp_path / "repo")
    _git(repo.parent, "init", "-q", "-b", "main", str(repo))
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "scripts/run-tests.sh", "flake.nix")
    _git(repo, "commit", "-qm", "base")
    return repo


def _push_line(repo: Path, base: str) -> str:
    head = _git(repo, "rev-parse", "HEAD")
    return "refs/heads/main %s refs/heads/main %s\n" % (head, base)


def test_a_push_touching_only_nix_home_nix_RUNS_the_gate(pushrepo, tmp_path):
    """🔴 THE REGRESSION. `nix/home.nix` declares every `home.activation` entry,
    including the one `scripts/tests-devhost/test_activation_order.py` was
    written to measure — and it was outside the filter that decides whether
    `--set all` (the only tier that collects that target) runs at all.

    Measured against the pre-fix script: SKIP. After: RUN.
    """
    base = _git(pushrepo, "rev-parse", "HEAD")
    (pushrepo / "nix").mkdir()
    (pushrepo / "nix" / "home.nix").write_text("{ }\n")
    _git(pushrepo, "add", "nix/home.nix")
    _git(pushrepo, "commit", "-qm", "activation entry")
    assert _decide(pushrepo, _push_line(pushrepo, base), tmp_path) == "RUN"


def test_a_push_touching_only_a_claudedocs_note_RUNS_the_gate(pushrepo, tmp_path):
    """The reason the fix is not "add `nix/` to the list". Four gates in this
    repo enumerate `git ls-files` and scan every tracked file including `.md`,
    so a docs-only push can red the suite. An allowlist cannot express that."""
    base = _git(pushrepo, "rev-parse", "HEAD")
    (pushrepo / "claudedocs").mkdir()
    (pushrepo / "claudedocs" / "note.md").write_text("# note\n")
    _git(pushrepo, "add", "claudedocs/note.md")
    _git(pushrepo, "commit", "-qm", "a note")
    assert _decide(pushrepo, _push_line(pushrepo, base), tmp_path) == "RUN"


def test_a_pure_ref_delete_SKIPS_the_gate(pushrepo, tmp_path):
    """🔴 THE NEGATIVE CONTROL — and it is labelled one rather than counted as
    coverage. A filter hardcoded to RUN satisfies every other case in this file;
    this is the only one that can tell them apart. A delete pushes no tree, so
    there is nothing to test."""
    stdin = "(delete) %s refs/heads/gone %s\n" % (ZERO, _git(pushrepo, "rev-parse", "HEAD"))
    assert _decide(pushrepo, stdin, tmp_path) == "SKIP"


def test_a_delete_ALONGSIDE_a_real_update_still_RUNS(pushrepo, tmp_path):
    """One inert line must not exempt the push. `git push --all --prune` sends
    both shapes in one invocation."""
    base = _git(pushrepo, "rev-parse", "HEAD")
    (pushrepo / "scripts" / "x.py").write_text("x = 1\n")
    _git(pushrepo, "add", "scripts/x.py")
    _git(pushrepo, "commit", "-qm", "code")
    stdin = ("(delete) %s refs/heads/gone %s\n" % (ZERO, base)) + _push_line(pushrepo, base)
    assert _decide(pushrepo, stdin, tmp_path) == "RUN"


@pytest.mark.parametrize("stdin_data,why", [
    ("", "no stdin at all"),
    ("refs/heads/main deadbeef\n", "a line missing its remote fields"),
    ("\n\n", "blank lines only"),
])
def test_an_UNREADABLE_push_description_RUNS(pushrepo, tmp_path, stdin_data, why):
    """INVARIANT GUARD, not regression coverage: nothing has ever produced these,
    and the script's header promises every ambiguity fails TOWARD running. Pinned
    so a rewrite cannot invert the default while still passing the cases above.

    `"\\n\\n"` is the interesting one — it is non-empty, so it reaches the loop,
    and every line is skipped as blank. It must still RUN, because "we were told
    about a push and could not tell what it was" is exactly the ambiguous case.
    """
    assert _decide(pushrepo, stdin_data, tmp_path) == "RUN", why
