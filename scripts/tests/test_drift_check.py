"""Behavioural + structural tests for scripts/drift-check.sh — the PASSIVE
drift deadman for the two-host devrc fleet.

Everything runs against THROWAWAY git repos built in tmp_path, and every remote
leg runs against a STUB `ssh` on PATH. Nothing here touches ~/workspace/devrc,
either real host, the network, or `home-manager`.

WHAT THIS SUITE IS FOR
----------------------
A deadman that cannot fire is WORSE than no deadman: it converts "nobody was
looking" into "something was looking and said fine". So the load-bearing tests
are not the happy path — they are:

  1. rc-PER-CONDITION. Each drift shape must produce its OWN distinct non-zero
     code (8 diverged/ahead, 10 behind, 12 not-on-main, 3 no-repo, 4
     fetch/origin-main, 13 unreachable). One code for "something is wrong" would
     be indistinguishable from the un-pushed-commits case that actually bit us
     twice, which is the only one that needs a rescue-before-reset procedure.
  2. THE POSITIVE CONTROL. `test_clean_repo_is_green` proves the checker can
     observe the clean case at all. A reassuring rc=0 from a checker wired to
     nothing is indistinguishable from a real green, so the green is only
     meaningful reported alongside the reds above.
  3. PASSIVITY. Both statically (the forbidden mutating primitives must not
     appear as CODE) and behaviourally (after a run against a diverged repo the
     branch, HEAD, worktree and stash stack must all be byte-identical).
  4. THE SEAM. drift-check.sh and ship.sh must resolve host identity through the
     SAME sourced predicate. Asserted as a ledger — neither file may define
     detect_role itself, both must source lib/host-role.sh — plus a behavioural
     check that their `--detect-role` answers agree across a table of IPs. A
     structural check alone would type-check past a second copy that merely
     happens to agree today.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

DRIFT = REPO_ROOT / "scripts" / "drift-check.sh"
SHIP = REPO_ROOT / "scripts" / "ship.sh"
HOST_ROLE_LIB = REPO_ROOT / "scripts" / "lib" / "host-role.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git + bash on PATH",
)


# --------------------------------------------------------------------------- #
# fixture: a throwaway origin + working clone, origin/main one commit ahead
# --------------------------------------------------------------------------- #
class Fleet:
    """origin.git + a `work` clone pinned one commit BEHIND origin/main."""

    def __init__(self, tmp_path):
        self.root = tmp_path
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.home = tmp_path / "home"
        self.bin = tmp_path / "bin"
        self.home.mkdir()
        self.bin.mkdir()

        self.gitconfig = tmp_path / "gitconfig"
        self.gitconfig.write_text(
            "[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n"
        )

        self._run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)])
        builder = tmp_path / "builder"
        self._run(["git", "clone", "-q", str(self.origin), str(builder)])
        (builder / "f").write_text("base\n")
        self.git(builder, "checkout", "-q", "-B", "main")
        self.git(builder, "add", "f")
        self.git(builder, "commit", "-q", "-m", "base")
        self.git(builder, "push", "-q", "-u", "origin", "main")

        self._run(["git", "clone", "-q", str(self.origin), str(self.work)])
        self.git(self.work, "checkout", "-q", "main")

        # origin/main advances: `work` is now exactly 1 BEHIND.
        (builder / "f").write_text("base\nupstream\n")
        self.git(builder, "commit", "-q", "-am", "ahead")
        self.git(builder, "push", "-q", "origin", "main")
        self.builder = builder

    # -- plumbing ----------------------------------------------------------- #
    def env(self, **extra):
        e = dict(os.environ)
        e.update(
            HOME=str(self.home),
            GIT_CONFIG_GLOBAL=str(self.gitconfig),
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_TERMINAL_PROMPT="0",
            PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
        )
        e.update(extra)
        return e

    def _run(self, argv):
        out = subprocess.run(argv, capture_output=True, text=True, env=self.env())
        assert out.returncode == 0, f"setup {argv} failed: {out.stderr}"
        return out.stdout.strip()

    def git(self, repo, *args):
        return self._run(["git", "-C", str(repo), *args])

    # -- state accessors ---------------------------------------------------- #
    def branch(self):
        out = subprocess.run(
            ["git", "-C", str(self.work), "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, env=self.env(),
        )
        return out.stdout.strip() or "DETACHED"

    def head(self):
        return self.git(self.work, "rev-parse", "HEAD")

    def stash_list(self):
        return self.git(self.work, "stash", "list")

    def status(self):
        return self.git(self.work, "status", "--porcelain")

    # -- helpers to build each drift shape ---------------------------------- #
    def catch_up(self):
        """Make `work`'s main equal origin/main (the clean state)."""
        self.git(self.work, "fetch", "origin", "-q")
        self.git(self.work, "merge", "--ff-only", "-q", "origin/main")

    def add_local_commit(self, msg="un-pushed local work"):
        p = self.work / ("local-%d.txt" % len(list(self.work.glob("local-*.txt"))))
        p.write_text(msg + "\n")
        self.git(self.work, "add", p.name)
        self.git(self.work, "commit", "-q", "-m", msg)

    # -- the subject under test --------------------------------------------- #
    def check(self, *args, repo=None, **envextra):
        env = self.env(
            SHIP_ROLE="workbench",           # bypass IP detection (no `ip` here)
            DRIFT_REPO=str(repo or self.work),
            **envextra,
        )
        proc = subprocess.run(
            ["bash", str(DRIFT), *args],
            capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def stub_ssh(self, exit_code, stdout=""):
        """Install a stub `ssh` on PATH that drains stdin and exits `exit_code`.

        Real ssh exits 255 on a connection/auth failure and otherwise passes the
        remote command's code through, so both halves of the remote contract are
        drivable without a network.
        """
        body = ["cat >/dev/null"]
        if stdout:
            # Single-quoted, with any embedded quote escaped the POSIX way.
            body.append("echo '%s'" % stdout.replace("'", "'\\''"))
        body.append("exit %d" % exit_code)
        write_exec(self.bin / "ssh", "\n".join(body) + "\n")


@pytest.fixture
def fleet(tmp_path):
    return Fleet(tmp_path)


# --------------------------------------------------------------------------- #
# 1. POSITIVE CONTROL — the checker can observe a clean host
# --------------------------------------------------------------------------- #
def test_clean_repo_is_green(fleet):
    """rc=0 on a host that is on main, at origin/main, with nothing untracked.

    🔴 Report this ALONGSIDE the reds below, never alone: a zero from a checker
    wired to nothing looks exactly like this one.
    """
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert "clean" in out
    assert "no drift" in out
    assert "untracked: 0" in out


# --------------------------------------------------------------------------- #
# 2. NEGATIVE CONTROLS — one distinct rc per condition
# --------------------------------------------------------------------------- #
def test_ahead_unpushed_commits_is_rc8(fleet):
    """🔴 THE INCIDENT SHAPE: un-pushed commits on local main -> rc 8.

    This is the condition ship.sh reports as `skipped:diverged`, after which the
    host silently receives nothing forever. It must be distinguishable from
    merely-behind (rc 10), because only this one needs rescue-then-reset.
    """
    fleet.catch_up()
    fleet.add_local_commit("commit the workbench never pushed")
    rc, out = fleet.check("--no-remote")
    assert rc == 8, f"expected rc8, got {rc}\n{out}"
    assert "AHEAD by 1 un-pushed commit" in out, out
    # The un-pushed commits must be NAMED — the report has to be actionable
    # without a second trip to the host.
    assert "commit the workbench never pushed" in out, out
    assert "ship.sh SKIPS this host" in out
    assert "reset --keep" in out and "never --hard" in out


def test_diverged_both_ways_is_rc8(fleet):
    """Ahead AND behind -> still rc 8, reported as DIVERGED with both counts."""
    fleet.add_local_commit("local side")          # work was already 1 behind
    rc, out = fleet.check("--no-remote")
    assert rc == 8, f"expected rc8, got {rc}\n{out}"
    assert "DIVERGED" in out
    assert "1 un-pushed commit(s), 1 behind" in out, out


def test_behind_only_is_rc10(fleet):
    """Behind origin/main -> rc 10 (needs a ship), NOT rc 8."""
    rc, out = fleet.check("--no-remote")
    assert rc == 10, f"expected rc10, got {rc}\n{out}"
    assert "BEHIND origin/main by 1 commit" in out, out
    assert "scripts/ship.sh" in out
    # Assert on the per-host DIAGNOSIS lines, not the whole output — the trailing
    # rc legend legitimately contains the words "un-pushed" and "diverged".
    host_lines = "\n".join(ln for ln in out.splitlines() if ln.startswith("[workbench]"))
    assert "un-pushed" not in host_lines, f"behind reported as un-pushed\n{out}"
    assert "DIVERGED" not in host_lines, f"behind reported as diverged\n{out}"


def test_not_on_main_is_rc12(fleet):
    """main == origin/main but the checkout sits on a feature branch -> rc 12."""
    fleet.catch_up()
    fleet.git(fleet.work, "checkout", "-q", "-b", "feat/something")
    rc, out = fleet.check("--no-remote")
    assert rc == 12, f"expected rc12, got {rc}\n{out}"
    assert "not on branch main" in out
    assert "feat/something" in out, out


def test_no_local_main_branch_is_rc12(fleet):
    """A checkout with no local `main` at all -> rc 12, not a crash."""
    fleet.git(fleet.work, "checkout", "-q", "-b", "only-branch")
    fleet.git(fleet.work, "branch", "-D", "main")
    rc, out = fleet.check("--no-remote")
    assert rc == 12, f"expected rc12, got {rc}\n{out}"
    assert "no local main branch" in out, out


def test_missing_repo_is_rc3(fleet):
    rc, out = fleet.check("--no-remote", repo=fleet.root / "nope")
    assert rc == 3, f"expected rc3, got {rc}\n{out}"
    assert "no repo at" in out


def test_missing_origin_main_is_rc4_not_drift(fleet):
    """A misconfigured remote must not be misreported as divergence."""
    fleet.git(fleet.origin, "branch", "-m", "main", "master")
    fleet.git(fleet.work, "update-ref", "-d", "refs/remotes/origin/main")
    rc, out = fleet.check("--no-remote")
    assert rc == 4, f"expected rc4, got {rc}\n{out}"
    assert "no origin/main" in out
    assert "DRIFT — local main" not in out, f"misclassified as drift\n{out}"


def test_fetch_failure_is_rc4(fleet):
    """An unreachable origin is 'cannot evaluate', never a green."""
    fleet.git(fleet.work, "remote", "set-url", "origin",
              str(fleet.root / "does-not-exist.git"))
    rc, out = fleet.check("--no-remote")
    assert rc == 4, f"expected rc4, got {rc}\n{out}"
    assert "git fetch failed" in out
    assert "no drift" not in out, f"reported green despite being unable to look\n{out}"


# --------------------------------------------------------------------------- #
# 3. The REMOTE leg (stubbed ssh — no network, no real host)
# --------------------------------------------------------------------------- #
def test_unreachable_remote_is_rc13(fleet):
    """ssh's 255 must become a LOUD rc 13, never a pass."""
    fleet.stub_ssh(255)
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid")
    assert rc == 13, f"expected rc13, got {rc}\n{out}"
    assert "UNREACHABLE" in out
    assert "this is not a pass" in out
    assert "no drift" not in out


def test_remote_rc_is_passed_through(fleet):
    """A remote host's own drift code reaches the caller unchanged."""
    fleet.stub_ssh(8, stdout="[laptop] AHEAD by 3 un-pushed commit(s).")
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid")
    assert rc == 8, f"expected rc8, got {rc}\n{out}"
    assert "3 un-pushed" in out, out


def test_worst_code_wins_across_hosts(fleet):
    """🔴 The aggregation rule: the WORST condition must not hide behind a milder one.

    Local is merely BEHIND (rc 10); the remote has un-pushed commits (rc 8).
    ship.sh keeps the FIRST non-zero — which here would hand systemd a 10 and
    describe the incident shape as 'needs a ship'. A timer's single exit code is
    the only thing anyone reads, so drift-check keeps the most severe.
    """
    fleet.stub_ssh(8, stdout="[laptop] AHEAD by 2 un-pushed commit(s).")
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid")
    assert rc == 8, f"expected the WORST code (8), got {rc}\n{out}"
    # ...and BOTH hosts' lines are still printed — nothing hides.
    assert "BEHIND origin/main" in out, f"local line missing\n{out}"
    assert "2 un-pushed" in out, f"remote line missing\n{out}"


def test_clean_local_plus_clean_remote_is_green(fleet):
    """Positive control for the two-host path, including the ssh leg."""
    fleet.catch_up()
    fleet.stub_ssh(0, stdout="[laptop] clean")
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid")
    assert rc == 0, f"expected rc0, got {rc}\n{out}"
    assert "no drift" in out


# --------------------------------------------------------------------------- #
# 4. Untracked files are REPORTED but never change the verdict
# --------------------------------------------------------------------------- #
def test_untracked_files_are_listed_but_do_not_fail(fleet):
    """The 2026-08-09 shape: a stranded handoff doc on an otherwise clean host."""
    fleet.catch_up()
    (fleet.work / "claudedocs").mkdir()
    (fleet.work / "claudedocs" / "handoff-stranded.md").write_text("notes\n")
    (fleet.work / "scratch.txt").write_text("x\n")
    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"untracked files must not change the verdict\n{out}"
    assert "untracked: 2 file(s)" in out, out
    assert "claudedocs/handoff-stranded.md" in out, out


def test_untracked_listing_is_capped(fleet):
    fleet.catch_up()
    for i in range(6):
        (fleet.work / ("u%d.txt" % i)).write_text("x\n")
    rc, out = fleet.check("--no-remote", DRIFT_UNTRACKED_MAX="2")
    assert rc == 0, out
    assert "untracked: 6 file(s)" in out
    assert "and 4 more" in out, out


# --------------------------------------------------------------------------- #
# 5. PASSIVITY — behavioural
# --------------------------------------------------------------------------- #
def test_run_against_diverged_repo_mutates_nothing(fleet):
    """🔴 The deadman REPORTS; it never fixes. Nothing about the tree may move."""
    fleet.catch_up()
    fleet.add_local_commit("precious un-pushed work")
    (fleet.work / "f").write_text("base\nupstream\nuncommitted edit\n")
    (fleet.work / "untracked-wip").write_text("wip\n")

    before = (fleet.branch(), fleet.head(), fleet.status(), fleet.stash_list())
    rc, out = fleet.check("--no-remote")
    after = (fleet.branch(), fleet.head(), fleet.status(), fleet.stash_list())

    assert rc == 8, out
    assert before == after, (
        "drift-check mutated the working state\n"
        f"before={before!r}\nafter={after!r}\n{out}"
    )
    assert fleet.stash_list() == "", f"stash entry created (repo-GLOBAL!)\n{out}"
    assert (fleet.work / "untracked-wip").read_text() == "wip\n"
    assert (fleet.work / "f").read_text().endswith("uncommitted edit\n")


def test_run_against_feature_branch_does_not_move_it(fleet):
    """Unlike ship.sh, drift-check must NOT land the checkout on main."""
    fleet.catch_up()
    fleet.git(fleet.work, "checkout", "-q", "-b", "feat/stay-here")
    rc, out = fleet.check("--no-remote")
    assert rc == 12, out
    assert fleet.branch() == "feat/stay-here", f"drift-check moved the branch\n{out}"


# --------------------------------------------------------------------------- #
# 6. PASSIVITY — structural
# --------------------------------------------------------------------------- #
FORBIDDEN_PRIMITIVES = (
    "git checkout", "git switch", "git merge", "git rebase", "git reset",
    "git stash", "git clean", "git commit", "git push", "git pull",
    "git cherry-pick", "git branch -", "--autostash", "home-manager",
)


# A line whose FIRST word is a printer can only PRINT the forbidden primitive,
# not run it — and drift-check's whole value is that it prints the rescue
# procedure (`git branch … && git push …`, `git reset --keep`) so the operator
# does not have to go looking for it. The carve-out is withdrawn the moment the
# line contains a command substitution, which is the one way a printer CAN
# execute something; `test_scanner_catches_a_substitution_inside_a_message`
# is the positive control for that half.
_OUTPUT_ONLY = re.compile(r"^\s*(say|echo|printf)\b")


def scan_mutations(text):
    """Return [(lineno, line, hits)] for every executable line that could MUTATE.

    Comment lines are excluded on purpose — the header DOCUMENTS the ban and the
    rescue procedure, so a whole-file grep would flag the script's own warning
    label.
    """
    code = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    out = []
    for i, ln in enumerate(code, 1):
        hits = [p for p in FORBIDDEN_PRIMITIVES if p in ln]
        if not hits:
            continue
        if _OUTPUT_ONLY.match(ln) and "$(" not in ln and "`" not in ln:
            continue
        out.append((i, ln.strip(), hits))
    return out


def test_drift_check_source_never_mutates():
    offenders = scan_mutations(DRIFT.read_text())
    assert offenders == [], (
        "drift-check.sh is PASSIVE — these lines could mutate a host:\n"
        + "\n".join(f"  line {i}: {ln}   (matched {h})" for i, ln, h in offenders)
    )
    src = DRIFT.read_text()
    # ...and the read-only primitives must still be the ones doing the work.
    assert "git fetch origin -q" in src
    assert "git rev-list --left-right --count" in src


def test_scanner_catches_a_bare_mutating_command(tmp_path):
    """Negative control #1 for the scanner above.

    Without it, `test_drift_check_source_never_mutates` is indistinguishable from
    a scan wired to nothing — the vacuous-green shape this suite exists to stop.
    """
    mutated = DRIFT.read_text() + "\ngit checkout main\n"
    offenders = scan_mutations(mutated)
    assert offenders, "the passivity scanner cannot detect an injected mutation"
    assert any("git checkout main" in ln for _, ln, _ in offenders)


def test_scanner_catches_a_substitution_inside_a_message(tmp_path):
    """Negative control #2 — the carve-out's own boundary.

    The say/echo/printf carve-out exists so the rescue procedure can be PRINTED.
    A command substitution smuggled into such a line would execute, so the
    carve-out must not cover it. This is the mutation that a naive
    "skip all message lines" scanner would sail straight past.
    """
    mutated = DRIFT.read_text() + '\nsay "oops $(git reset --hard origin/main)"\n'
    offenders = scan_mutations(mutated)
    assert offenders, "the carve-out swallows a command substitution"
    assert any("git reset" in " ".join(h) for _, _, h in offenders)


def test_scanner_carve_out_still_allows_a_plain_message():
    """...and the carve-out genuinely carves: a pure message line is NOT flagged.

    Proves the two controls above are not passing merely because the scanner
    flags everything.
    """
    assert scan_mutations('say "  fix: git reset --keep origin/main"\n') == []


# --------------------------------------------------------------------------- #
# 7. THE SEAM — one host-identity predicate, shared by both scripts
# --------------------------------------------------------------------------- #
def test_host_identity_has_exactly_one_definition():
    """A LEDGER, not a spot check: assert who defines the predicate and who
    sources it. Fails when the set GROWS (a second copy reappears) or SHRINKS
    (a consumer stops sourcing and starts open-coding it).

    Host detection has already been wrong once here — ship.sh hardcoded
    'local == workbench' and SSH'd to itself. A duplicated predicate is how that
    returns.
    """
    definers = []
    for path in sorted((REPO_ROOT / "scripts").rglob("*.sh")):
        text = path.read_text()
        code = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
        if any(ln.strip().startswith("detect_role()") for ln in code):
            definers.append(path.relative_to(REPO_ROOT).as_posix())
    assert definers == ["scripts/lib/host-role.sh"], (
        "host-role detection must be defined in exactly one place; found: %r" % definers
    )

    for consumer in (DRIFT, SHIP):
        src = consumer.read_text()
        assert "lib/host-role.sh" in src, (
            f"{consumer.name} no longer sources the shared host-identity library"
        )


@pytest.mark.parametrize(
    "ips,expected",
    [
        ("192.168.50.250", "workbench"),
        ("192.168.50.155", "laptop"),
        ("10.42.0.30", "workbench"),
        ("10.42.0.100", "laptop"),
        ("192.168.50.250 192.168.50.155", "workbench"),
        ("172.17.0.1 127.0.0.1", "unknown"),
        ("", "unknown"),
    ],
)
def test_both_scripts_and_the_lib_agree_on_role(ips, expected):
    """Behavioural half of the seam: a structural 'both source it' check would
    type-check past a copy that merely happens to agree today."""
    answers = {}
    for name, path in (("ship", SHIP), ("drift", DRIFT), ("lib", HOST_ROLE_LIB)):
        out = subprocess.run(
            ["bash", str(path), "--detect-role", ips],
            capture_output=True, text=True, check=True,
        )
        answers[name] = out.stdout.strip()
    assert set(answers.values()) == {expected}, answers


def test_host_role_lib_is_side_effect_free_when_sourced(tmp_path):
    """Sourcing the library must define things and do NOTHING else — it is pulled
    into ship.sh mid-script, where any stray output or exit would be a landmine.
    """
    probe = tmp_path / "probe.sh"
    probe.write_text(
        "set -uo pipefail\n"
        f". {HOST_ROLE_LIB}\n"
        'printf "MARKER:%s\\n" "$(detect_role 192.168.50.250)"\n'
    )
    out = subprocess.run(["bash", str(probe)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout == "MARKER:workbench\n", (
        f"sourcing produced extra output: {out.stdout!r} / {out.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# 8. The systemd wiring in nix/home.nix
# --------------------------------------------------------------------------- #
HOME_NIX = (REPO_ROOT / "nix" / "home.nix").read_text()


def _drift_service_block() -> str:
    """The `systemd.user.services.drift-check` block, isolated.

    Pinned by slicing between the service and the timer declaration so an
    assertion about the service cannot be satisfied by text belonging to some
    other unit — home.nix declares ~15 of them and they all look alike.
    """
    start = HOME_NIX.index("systemd.user.services.drift-check")
    end = HOME_NIX.index("systemd.user.timers.drift-check")
    assert start < end
    return HOME_NIX[start:end]


def test_home_nix_declares_the_service_and_the_timer():
    assert "systemd.user.services.drift-check" in HOME_NIX
    assert "systemd.user.timers.drift-check" in HOME_NIX
    block = _drift_service_block()
    assert "scripts/drift-check.sh" in block, "ExecStart does not run the checker"
    # The alerting mechanism is the EXISTING one — a non-zero exit puts the unit
    # in `failed`, and notify-failure@ turns that into the sticky dunst toast.
    # Without this line the deadman is silent, which is the whole failure it
    # exists to fix, one level up.
    assert "notify-failure@%n.service" in block, "drift produces no notification"


def test_the_timer_is_gated_by_the_master_switch_and_the_service_is_not():
    """The switch must act ONLY on the timers.target wiring.

    If it gated the service too, `systemctl --user start drift-check` — the
    supervised hand-run that is supposed to validate the ssh leg before the timer
    is ever armed — would not exist on an un-enabled host.
    """
    block = _drift_service_block()
    assert "mkIf" not in block, (
        "the drift-check SERVICE must be emitted unconditionally so it can be run by hand"
    )
    timer = HOME_NIX[HOME_NIX.index("systemd.user.timers.drift-check"):]
    timer = timer[:timer.index("\n  };") + 5]
    assert "lib.optionals (serverMode && enableDriftDeadman) [ \"timers.target\" ]" in timer, (
        "the timer's WantedBy is not gated by the master switch:\n" + timer
    )
    assert "enableDriftDeadman = false;" in HOME_NIX, (
        "the master switch must ship OFF — enabling a timer is a live change to a "
        "host and belongs in its own supervised deploy"
    )


# cmd -> the nixpkgs attribute that must appear in the unit's makeBinPath list.
# 🔴 THE SEAM: the script and the unit are tested in different places and neither
# owns their intersection. `ip` is the sharp one — it is how the script decides
# WHICH host it is running on (both report hostname `nixos`), so dropping
# iproute2 from the PATH does not crash anything: detection silently returns
# "unknown" and the deadman exits 6 forever, from a unit that looks correct.
UNIT_PATH_REQUIREMENTS = {
    "git": "pkgs.git",
    "ssh": "pkgs.openssh",
    "ip": "pkgs.iproute2",
    "awk": "pkgs.gawk",
    "sed": "pkgs.gnused",
    "head": "pkgs.coreutils",
    "wc": "pkgs.coreutils",
    "tr": "pkgs.coreutils",
    "cut": "pkgs.coreutils",
}


@pytest.mark.parametrize("cmd,attr", sorted(UNIT_PATH_REQUIREMENTS.items()))
def test_every_command_the_checker_runs_is_on_the_unit_path(cmd, attr):
    scripts_src = DRIFT.read_text() + HOST_ROLE_LIB.read_text()
    code = "\n".join(
        ln for ln in scripts_src.splitlines() if not ln.strip().startswith("#")
    )
    assert cmd in code, (
        f"{cmd!r} is pinned as a PATH requirement but the scripts no longer call "
        f"it — drop it from UNIT_PATH_REQUIREMENTS (the pin is the accounting)"
    )
    assert attr in _drift_service_block(), (
        f"the drift-check unit's PATH is missing {attr} — {cmd!r} would not resolve "
        f"under systemd, which has none of the login shell's PATH"
    )
