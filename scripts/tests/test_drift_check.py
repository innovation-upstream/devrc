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
     fetch/origin-main, 13 unreachable-for-N-consecutive-runs). One code for
     "something is wrong" would be indistinguishable from the un-pushed-commits
     case that actually bit us twice, which is the only one that needs a
     rescue-before-reset procedure.
  2. THE POSITIVE CONTROL. `test_clean_repo_is_green` proves the checker can
     observe the clean case at all. A reassuring rc=0 from a checker wired to
     nothing is indistinguishable from a real green, so the green is only
     meaningful reported alongside the reds above.
  2b. THE ALERTING POLICY, IN BOTH DIRECTIONS. An unreachable laptop must NOT
     fail the unit on its own (a permanently-red gate is worse than no gate) —
     and a genuine rc 8 must still exit non-zero even while the laptop is
     unreachable, or the softening has simply muted the deadman.
  3. PASSIVITY. Statically — an ALLOWLIST of read-only git subcommands, anchored
     at every command separator, plus an asserted ledger of the only file the
     script writes — and behaviourally: after a run against a diverged repo the
     branch, HEAD, worktree and stash stack must all be byte-identical.
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
        self.state = tmp_path / "state"
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
            # Pinned into tmp_path: the unreachable streak is PERSISTENT state,
            # and left to its default ($XDG_STATE_HOME/…) these tests would both
            # write to the operator's real state dir and inherit a streak from
            # whatever ran before them.
            DRIFT_STATE_DIR=str(self.state),
        )
        env.update(envextra)   # per-test overrides win (e.g. a blocked state dir)
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


def test_off_main_with_diverged_main_is_rc8(fleet):
    """🔴 REGRESSION PIN for a mutant that the whole 41-test suite survived.

    Shape: HEAD sits on a feature branch AND local `main` has un-pushed commits.
    That is the rc 8 incident shape — ship.sh skips the host entirely and moves
    nothing — but it is ALSO 'not on branch main', so the answer depends purely
    on the ORDER of the two checks in drift-check.sh.

    Hoisting the `off_main -> exit 12` guard above the ahead/behind comparison
    (a mutant that deletes nothing and moves one line) makes this case report
    rc 12 with the advice 'ship.sh will move it' — which is FALSE: ship.sh skips
    this host and it receives nothing, forever. The pre-existing suite stayed
    green under that mutant (41 passed, 0 failed).
    """
    fleet.catch_up()
    fleet.add_local_commit("un-pushed work stranded on main")
    fleet.git(fleet.work, "checkout", "-q", "-b", "feat/elsewhere")

    rc, out = fleet.check("--no-remote")
    assert rc == 8, f"off-main + diverged main must be the rc8 shape, got {rc}\n{out}"
    # ...and the verdict must not tell the operator ship.sh will fix the checkout.
    assert "ship.sh SKIPS this host" in out, out
    assert "will NOT move it" in out, out
    assert "ship.sh will land the checkout back on main" not in out, (
        "reported that ship.sh will fix a host ship.sh actually skips\n" + out
    )
    # The stranded commit is still named — the report must be actionable.
    assert "un-pushed work stranded on main" in out, out


def test_off_main_and_behind_is_rc12_matching_the_published_severity_order(fleet):
    """off-main AND behind -> rc 12, not rc 10.

    The file publishes `12 > 10` in its own severity table; returning 10 for a
    host that is both would contradict it, and the aggregate code a timer hands
    to systemd is derived from that very table.
    """
    fleet.git(fleet.work, "checkout", "-q", "-b", "feat/behind-too")  # work is 1 behind
    rc, out = fleet.check("--no-remote")
    assert rc == 12, f"expected rc12, got {rc}\n{out}"
    assert "not on branch main" in out, out
    assert "also BEHIND by 1" in out, out


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


def test_fetch_failure_surfaces_gits_own_stderr(fleet):
    """rc 4 must carry a DIAGNOSIS, not just a verdict.

    The unit's only output is the journal, and rc 4 is the recurring code (key
    rotation, DNS, host-key churn). Discarding git's stderr — which the original
    did, with `2>/dev/null` — makes every one of those indistinguishable from
    every other, on a unit nobody is watching live.
    """
    fleet.git(fleet.work, "remote", "set-url", "origin",
              str(fleet.root / "does-not-exist.git"))
    rc, out = fleet.check("--no-remote")
    assert rc == 4, out
    diag = [ln for ln in out.splitlines() if "] " in ln and " git: " in ln]
    assert diag, f"git's own error was swallowed; nothing to debug from:\n{out}"
    assert any(ln.split(" git: ", 1)[1].strip() for ln in diag), (
        f"the git: prefix is there but carries no message\n{out}"
    )


# --------------------------------------------------------------------------- #
# 3. The REMOTE leg (stubbed ssh — no network, no real host)
# --------------------------------------------------------------------------- #
def test_unreachable_remote_below_threshold_does_not_fail_the_unit(fleet):
    """🔴 A SHUT LAPTOP MUST NOT LOOK LIKE DRIFT.

    The timer is serverMode-gated (workbench-only), so its remote leg always
    ssh's to the laptop — routinely off/asleep/off-LAN. Failing the unit on that
    fires the SAME sticky critical toast as a genuine rc 8, up to 4× a day, and
    a permanently-red gate is worse than no gate.

    So: reported loudly in the journal, exit code unaffected.
    """
    fleet.stub_ssh(255)
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid")
    assert rc == 0, f"a single unreachable run must not fail the unit, got {rc}\n{out}"
    assert "UNREACHABLE" in out
    assert "not a pass" in out, out                  # still not sold as a green
    assert "1/4 consecutive" in out, out
    assert "NOT escalated" in out, out
    # ...and the summary must not claim a clean bill of health for a run that
    # successfully checked nothing.
    assert "NO HOST WAS SUCCESSFULLY CHECKED" in out, out


def test_unreachable_remote_escalates_after_n_consecutive_runs(fleet):
    """🔴 THE OTHER DIRECTION: the softening must not make the deadman mute.

    A host that has been unlookable for the whole threshold window is no longer
    'the laptop is shut' — at that point NOT alerting would be the deadman
    failing at its one job. Threshold reached -> rc 13 -> unit failed -> toast.
    """
    fleet.stub_ssh(255)
    codes = []
    for _ in range(4):
        rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                              DRIFT_UNREACHABLE_ESCALATE="3")
        codes.append(rc)
    assert codes == [0, 0, 13, 13], f"escalation ladder wrong: {codes}\n{out}"
    assert "ESCALATING" in out, out
    assert "CONSECUTIVE unreachable checks" in out, out


def test_unreachable_streak_resets_when_the_host_answers(fleet):
    """The streak counts CONSECUTIVE misses. One successful reach clears it —
    otherwise a laptop that is merely offline every other day would eventually
    escalate anyway and the threshold would be meaningless."""
    fleet.catch_up()
    fleet.stub_ssh(255)
    for _ in range(2):
        rc, _ = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                            DRIFT_UNREACHABLE_ESCALATE="3")
        assert rc == 0

    fleet.stub_ssh(0, stdout="[laptop] clean")       # it answers
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                          DRIFT_UNREACHABLE_ESCALATE="3")
    assert rc == 0, out

    fleet.stub_ssh(255)                              # ...and goes away again
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                          DRIFT_UNREACHABLE_ESCALATE="3")
    assert rc == 0, f"the streak did not reset on a successful reach\n{out}"
    assert "1/3 consecutive" in out, out


def test_local_rc8_still_wins_when_the_remote_is_unreachable(fleet):
    """🔴 THE FAILURE MODE OF THE FIX ABOVE, pinned.

    Softening the unreachable case must not soften anything else. With the
    laptop shut AND this host carrying un-pushed commits, the run must still
    exit 8 — non-zero is what puts the unit in `failed`, which is what
    OnFailure=notify-failure@%n.service turns into the toast.
    """
    fleet.catch_up()
    fleet.add_local_commit("un-pushed while the laptop is shut")
    fleet.stub_ssh(255)
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid")
    assert rc == 8, f"local drift was masked by the unreachable remote: {rc}\n{out}"
    assert "ship.sh SKIPS this host" in out, out
    assert "UNREACHABLE" in out, out                 # both are still reported


def test_unreachable_escalates_immediately_when_the_streak_cannot_be_persisted(fleet):
    """If 'for how long' is unknowable, the run must fail CLOSED (loud), not open.

    A state dir that cannot be created is the one case where the threshold logic
    has no input at all; going quiet there would be an unbounded silent window.
    """
    blocked = fleet.root / "blocked-state"
    blocked.write_text("not a directory\n")          # mkdir -p will fail on this
    fleet.stub_ssh(255)
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                          DRIFT_STATE_DIR=str(blocked))
    assert rc == 13, f"expected an immediate escalation, got {rc}\n{out}"
    assert "could not be" in out and "persisted" in out, out


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


def test_rc8_outranks_an_escalated_rc13(fleet):
    """The severity TABLE, exercised — killed a surviving mutant.

    `test_worst_code_wins_across_hosts` only ever compares 8 against 10, so a
    mutant that reranks rc 8 *below* rc 13 survived it. Un-pushed commits are the
    condition with the rescue-before-reset procedure; an unreachable host is not.
    The number handed to systemd must be the former.
    """
    fleet.catch_up()
    fleet.add_local_commit("un-pushed, while the laptop has been gone for days")
    fleet.stub_ssh(255)
    for _ in range(3):
        rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
                              DRIFT_UNREACHABLE_ESCALATE="2")
    assert "ESCALATING" in out, out          # the remote really did escalate
    assert rc == 8, f"an escalated rc13 outranked the un-pushed-commits rc8\n{out}"


def test_the_unreachable_streak_is_kept_PER_REMOTE_HOST(fleet):
    """Killed a surviving mutant: a single shared counter file.

    Today there is one remote per run so a shared file is invisible — but the
    streak is what decides whether the alert fires, and a counter keyed by
    nothing would let two hosts' absences add up into an escalation neither one
    earned. Driven by flipping the LOCAL role, which flips which host is remote.
    """
    fleet.stub_ssh(255)
    for _ in range(2):                       # laptop misses twice
        rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                              SHIP_ROLE="workbench", DRIFT_UNREACHABLE_ESCALATE="3")
        assert rc == 0, out
    # Now the OTHER direction: from the laptop, the remote is the workbench.
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                          SHIP_ROLE="laptop", DRIFT_UNREACHABLE_ESCALATE="3")
    assert rc == 0, out
    assert "1/3 consecutive" in out, (
        "the workbench inherited the laptop's streak — one shared counter\n" + out
    )


def test_the_remote_payload_is_shell_quoted():
    """STRUCTURAL, and labelled as such.

    `require_int` already rejects every value that could inject, so swapping %q
    back to %s is behaviourally invisible to this suite — a mutant that survives
    every behavioural test. %q is the second layer, and the only way to hold a
    second layer whose first layer never lets anything through is to assert it
    is there.
    """
    src = DRIFT.read_text()
    assert "DRIFT_LABEL=%q" in src and "DRIFT_UNTRACKED_MAX=%q" in src, (
        "the values interpolated into the REMOTE payload are no longer %q-quoted"
    )


def test_clean_local_plus_clean_remote_is_green(fleet):
    """Positive control for the two-host path, including the ssh leg."""
    fleet.catch_up()
    fleet.stub_ssh(0, stdout="[laptop] clean")
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid")
    assert rc == 0, f"expected rc0, got {rc}\n{out}"
    assert "no drift" in out


def test_untracked_max_cannot_inject_a_command_into_the_remote_payload(fleet):
    """🔴 The tunables are INTERPOLATED INTO A SCRIPT THAT RUNS ON THE OTHER HOST.

    `DRIFT_UNTRACKED_MAX='10; echo INJECTED-COMMAND-RAN'` used to be executed
    remotely — a passivity hole on the far side of the ssh hop, which the static
    scanner structurally cannot see (it only reads this file). Operator-supplied,
    so low exploitability, but a deadman forbidden from touching a host must not
    contain a path that runs arbitrary commands on it.
    """
    fleet.stub_ssh(0, stdout="[laptop] clean")
    rc, out = fleet.check(
        "--no-local",
        REMOTE_SSH="stub@example.invalid",
        DRIFT_UNTRACKED_MAX="10; echo INJECTED-COMMAND-RAN",
    )
    # The marker may legitimately appear INSIDE the rejection message (it quotes
    # the offending value back). What must never appear is the marker as the
    # OUTPUT OF A COMMAND — i.e. on a line of its own.
    ran = [ln for ln in out.splitlines() if ln.strip() == "INJECTED-COMMAND-RAN"]
    assert not ran, f"remote command injection: the payload executed\n{out}"
    assert rc == 2, f"a non-integer tunable must be a usage error, got {rc}\n{out}"
    assert "must be a non-negative integer" in out, out


@pytest.mark.parametrize(
    "var", ["DRIFT_UNTRACKED_MAX", "DRIFT_UNREACHABLE_ESCALATE"]
)
def test_non_integer_tunables_are_rejected(fleet, var):
    rc, out = fleet.check("--no-remote", **{var: "not-a-number"})
    assert rc == 2, f"{var} was accepted as {'not-a-number'!r}\n{out}"
    assert var in out, out


def test_checking_nothing_is_refused_rather_than_reported_as_a_pass(fleet):
    """`--no-local --no-remote` used to print 'no drift — both hosts on branch
    main at origin/main' and exit 0 having looked at neither host: the exact
    vacuous green this subsystem exists to prevent, from the subsystem itself."""
    rc, out = fleet.check("--no-local", "--no-remote")
    assert rc == 2, f"expected a usage error, got {rc}\n{out}"
    assert "check NOTHING" in out, out
    assert "no drift" not in out, out
    assert "both hosts" not in out, out


def test_a_single_host_run_does_not_claim_both_hosts(fleet):
    """The summary must state WHAT WAS CHECKED.

    `--no-remote` on a clean repo previously printed "no drift — both hosts on
    branch main at origin/main", naming a host the run never contacted. That
    wording appears in PR #367's own positive-control transcript, where it read
    as evidence of coverage the run did not have.
    """
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert "both hosts" not in out, f"claimed coverage it did not have\n{out}"
    assert "workbench (local)" in out, out
    assert "NOT checked" in out and "laptop" in out, out


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
# 🔴 An ALLOWLIST of read-only git subcommands, NOT a blocklist of mutating ones.
#
# The blocklist this replaced ("git checkout", "git reset", …) anchored on the
# FIRST WORD OF A LINE and enumerated verbs, so it missed every one of:
#   git restore --staged .        git update-ref refs/heads/main origin/main
#   git config user.name evil     git gc --prune=now        git prune
#   git worktree add /tmp/x       git branch new origin/main
#   git symbolic-ref HEAD refs/heads/other                  rm -rf "$repo"
#   say "checking" && git checkout main    echo hi; git reset --hard origin/main
# — while three separate docstrings claimed it enforced passivity. A blocklist of
# verbs is a game you lose once; an allowlist fails CLOSED on a verb nobody
# thought of.
READONLY_GIT_SUBCOMMANDS = frozenset({
    "fetch",        # writes remote-tracking refs ONLY — the one deliberate write
    "rev-parse", "rev-list", "show-ref", "ls-files", "log", "show",
    "status", "diff", "cat-file", "for-each-ref", "merge-base", "describe",
    "name-rev", "shortlog", "var", "check-ignore", "ls-remote", "count-objects",
})

# Non-git commands that can destroy or rewrite a host's files. `mkdir` is
# deliberately absent (it creates, never destroys) and the ONE directory this
# script creates is pinned separately by the redirection ledger below.
DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "mv", "cp", "ln", "dd", "truncate", "tee", "shred", "mkfifo",
    "chmod", "chown", "chgrp", "install", "rsync",
    "home-manager", "nixos-rebuild", "nix-env",
})

# Words that are not the command — strip them and look at what follows.
_TRANSPARENT = frozenset({
    "if", "then", "elif", "else", "fi", "while", "until", "for", "do", "done",
    "case", "esac", "select", "function", "!", "time", "exec", "eval",
    "command", "builtin", "sudo", "env", "nohup", "xargs", "local", "export",
    "declare", "readonly", "return", "in",
})

_PRINTERS = frozenset({"say", "echo", "printf"})


def _walk(line):
    """Split ONE shell line into command segments, quote-aware.

    Segments break at every command separator — `;` `&&` `||` `|` `&` `(` `)`
    `{` `}` — AND at `$(` / backtick, because a command substitution executes
    even inside double quotes. That last part is why the old first-word anchor
    was not enough: `say "x" && git checkout main` is two commands, and the
    second one runs.

    Quoted RUNS are kept in the segment text (so a message's words survive for
    the human-readable report) but never create separators, which is what lets
    `say "… git branch <t> && git push …"` stay a single printer segment.
    """
    segs, cur = [], []
    i, n, q = 0, len(line), None
    while i < n:
        c = line[i]
        if q == "'":
            if c == "'":
                q = None
            else:
                cur.append(c)
            i += 1
            continue
        if q == '"':
            if c == '"':
                q = None
                i += 1
                continue
            if c == "$" and line[i:i + 2] == "$(":
                segs.append("".join(cur)); cur = []; q = None; i += 2
                continue
            if c == "`":
                segs.append("".join(cur)); cur = []; q = None; i += 1
                continue
            cur.append(c)
            i += 1
            continue
        # --- unquoted -------------------------------------------------------
        if c == "\\":
            i += 2
            continue
        if c == "'":
            q = "'"; i += 1; continue
        if c == '"':
            q = '"'; i += 1; continue
        if line[i:i + 2] == "$(":
            segs.append("".join(cur)); cur = []; i += 2; continue
        if c == "`":
            segs.append("".join(cur)); cur = []; i += 1; continue
        if c in "();{}&|":
            segs.append("".join(cur)); cur = []
            i += 2 if line[i:i + 2] in ("&&", "||") else 1
            continue
        cur.append(c)
        i += 1
    segs.append("".join(cur))
    return [s for s in (x.strip() for x in segs) if s]


def _command_tokens(seg):
    """The tokens of a segment with leading assignments/redirections/keywords
    stripped, so tokens[0] is the command actually being run (or [])."""
    toks = seg.split()
    while toks:
        t = toks[0]
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=.*", t) or re.match(r"^\d?[<>]", t):
            toks.pop(0); continue
        if t in _TRANSPARENT:
            toks.pop(0); continue
        break
    return toks


def _strip_redirections(toks):
    """Drop redirection tokens (`2>/dev/null`, `>`, `"$f"`) from an argv list.

    Without this, `git symbolic-ref --quiet --short HEAD 2>/dev/null` looks like
    it has TWO operands and is misread as the ref-WRITING form."""
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"\d?[<>]{1,2}", t):
            i += 2; continue            # separated target
        if re.match(r"^\d?[<>]", t):
            i += 1; continue            # attached target
        out.append(t); i += 1
    return out


def _classify_git(args):
    args = _strip_redirections(args)
    if "--autostash" in args:
        return "git --autostash"
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            i += 2; continue
        if a.startswith("-"):
            i += 1; continue
        break
    if i >= len(args):
        return None                       # bare `git`, or only global flags
    sub = args[i]
    operands = [x for x in args[i + 1:] if not x.startswith("-")]
    if sub == "symbolic-ref":
        # READ: `git symbolic-ref --quiet --short HEAD`.
        # WRITE: `git symbolic-ref HEAD refs/heads/other` — same verb, two operands.
        return None if len(operands) <= 1 else "git symbolic-ref (WRITES a ref)"
    if sub in READONLY_GIT_SUBCOMMANDS:
        return None
    return "git %s (not in the read-only allowlist)" % sub


def _classify(seg):
    toks = _command_tokens(seg)
    if not toks:
        return None
    cmd = toks[0].rsplit("/", 1)[-1]
    if cmd in _PRINTERS:
        # A printer can only PRINT — any substitution inside it became its own
        # segment above, so this carve-out cannot swallow an execution.
        return None
    if cmd == "git":
        return _classify_git(toks[1:])
    if cmd == "sed" and any(t.startswith("-i") for t in toks[1:]):
        return "sed -i (in-place edit)"
    if cmd in DESTRUCTIVE_COMMANDS:
        return "%s (destructive command)" % cmd
    return None


def scan_mutations(text):
    """Return [(lineno, line, reasons)] for every executable segment that could
    MUTATE a checkout.

    Comment lines are excluded on purpose — the header DOCUMENTS the ban and the
    rescue procedure, so a whole-file grep would flag the script's own warning
    label. Line numbers are the file's real ones.
    """
    out = []
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        reasons = []
        for seg in _walk(ln):
            r = _classify(seg)
            if r and r not in reasons:
                reasons.append(r)
        if reasons:
            out.append((i, s, reasons))
    return out


# Every command shape the previous first-word scanner MISSED, plus the two it
# caught. This list IS the finding: a scanner is only worth its docstring once
# each of these has been watched to flag.
MUTATION_PROBES = [
    "git checkout main",
    "git switch -c other",
    "git reset --hard origin/main",
    "git restore --staged .",
    "git update-ref refs/heads/main origin/main",
    'git config user.name evil',
    "git gc --prune=now",
    "git prune",
    "git worktree add /tmp/x",
    "git branch newbranch origin/main",
    "git symbolic-ref HEAD refs/heads/other",
    "git clean -fdx",
    "git stash",
    "git commit -am wat",
    "git push --force origin main",
    "git pull --autostash",
    "git remote set-url origin evil",
    "git apply /tmp/patch",
    "git am /tmp/patch",
    'rm -rf "$repo"',
    'mv "$repo" /tmp/gone',
    'say "checking" && git checkout main',
    "echo hi; git reset --hard origin/main",
    'printf "x" || git clean -fdx',
    'say "note" | git stash',
    'say "oops $(git reset --hard origin/main)"',
    "home-manager switch --flake .",
    'sed -i "s/a/b/" "$repo/f"',
    'git -C "$repo" checkout main',
    "if true; then git reset --hard; fi",
    "for f in x; do rm -rf $f; done",
]


@pytest.mark.parametrize("probe", MUTATION_PROBES)
def test_scanner_flags_every_known_mutation_shape(probe):
    """NEGATIVE CONTROL, one case per shape.

    Each probe is appended to the REAL script, so a probe can only be flagged
    because of itself — the rest of the file is known-clean by
    `test_drift_check_source_never_mutates`.
    """
    offenders = scan_mutations(DRIFT.read_text() + "\n" + probe + "\n")
    assert offenders, "the passivity scanner does not flag: %s" % probe
    assert any(probe.strip() == ln for _, ln, _ in offenders), (
        "flagged something, but not the injected line %r: %r" % (probe, offenders)
    )


# Lines that MUST NOT be flagged. Without these the scanner is indistinguishable
# from one that flags everything — and a scanner that flags everything gets
# deleted the first time it blocks a legitimate change.
BENIGN_PROBES = [
    'say "  fix: git reset --keep origin/main"',
    'say "  rescue (on that host): git branch <topic> main && git push -u origin <topic>"',
    "git fetch origin -q",
    'fetch_err=$(git fetch origin -q 2>&1)',
    "git rev-list --left-right --count origin/main...main 2>/dev/null",
    "branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo DETACHED)",
    "untracked=$(git ls-files --others --exclude-standard 2>/dev/null)",
    "git show-ref --verify --quiet refs/heads/main",
    'git log --oneline --no-decorate origin/main..main | head -n 10 | sed "s|^|  + |"',
    'echo "[$REMOTE_ROLE] UNREACHABLE — ssh failed" ',
    'mkdir -p "$DRIFT_STATE_DIR" 2>/dev/null',
]


@pytest.mark.parametrize("probe", BENIGN_PROBES)
def test_scanner_does_not_flag_legitimate_read_only_lines(probe):
    assert scan_mutations(probe + "\n") == [], probe


def test_drift_check_source_never_mutates():
    offenders = scan_mutations(DRIFT.read_text())
    assert offenders == [], (
        "drift-check.sh is PASSIVE — these segments could mutate a host:\n"
        + "\n".join(f"  line {i}: {ln}   ({h})" for i, ln, h in offenders)
    )
    src = DRIFT.read_text()
    # ...and the read-only primitives must still be the ones doing the work.
    assert "git fetch origin -q" in src
    assert "git rev-list --left-right --count" in src


def test_host_role_lib_never_mutates():
    """The lib is SOURCED into the deadman, so its passivity is the deadman's."""
    offenders = scan_mutations(HOST_ROLE_LIB.read_text())
    assert offenders == [], offenders


def _unquoted_redirect_targets(line):
    """Redirection targets appearing OUTSIDE quotes on one line.

    Quote-aware on purpose: `say "… git branch <topic> main …"` contains a `>`
    that is text, not a redirection, and a naive regex reads it as a write to
    the file `main`.
    """
    targets, i, n, q, prev = [], 0, len(line), None, ""
    while i < n:
        c = line[i]
        if q:
            if c == q:
                q = None
            i += 1; prev = c; continue
        if c in "'\"":
            q = c; i += 1; prev = c; continue
        if c == "#" and prev in ("", " ", "\t"):
            break                       # trailing comment — not code
        if c == ">" and prev not in "0123456789&":
            j = i
            while j < n and line[j] == ">":
                j += 1
            while j < n and line[j] == " ":
                j += 1
            k = j
            while k < n and line[k] not in " \t;|&)":
                k += 1
            targets.append(line[j:k])
            i = k; prev = ">"; continue
        prev = c
        i += 1
    return targets


def test_the_only_files_the_deadman_writes_are_the_streak_counters():
    """LEDGER, not a spot check: every non-/dev/null redirection target in the
    script, asserted as a set. Fails when it GROWS (a new file gets written) or
    SHRINKS (the streak counter stops being persisted, which would silently
    disable the escalation ladder).

    The static command scanner cannot see this: `printf … > "$repo/f"` is a
    printer as far as it is concerned.
    """
    found = set()
    for ln in DRIFT.read_text().splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        for t in _unquoted_redirect_targets(ln):
            if t in ("/dev/null", "&2", "&1", ""):
                continue
            found.add(t)
    assert found == {'"$f"'}, (
        "drift-check.sh writes files other than the unreachable streak counter: %r" % found
    )


def test_redirect_ledger_notices_a_new_write():
    """POSITIVE CONTROL for the ledger — a zero from it would otherwise be
    indistinguishable from a regex that never matches anything."""
    assert _unquoted_redirect_targets('printf "x" > "$repo/f"') == ['"$repo/f"']
    assert _unquoted_redirect_targets('echo hi 2>/dev/null') == []
    assert _unquoted_redirect_targets(
        'say "  git branch <topic> main && git push"') == []


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
    # WORD-BOUNDED, not a raw substring: `"ip" in code` is satisfied by
    # `pipefail` and `"tr" in code` by `untracked`, so half of this table used to
    # pass without the command appearing anywhere as a command.
    present = re.search(r"(?<![\w.-])%s(?![\w.-])" % re.escape(cmd), code) is not None
    assert present, (
        f"{cmd!r} is pinned as a PATH requirement but the scripts no longer call "
        f"it — drop it from UNIT_PATH_REQUIREMENTS (the pin is the accounting)"
    )
    assert attr in _drift_service_block(), (
        f"the drift-check unit's PATH is missing {attr} — {cmd!r} would not resolve "
        f"under systemd, which has none of the login shell's PATH"
    )


# --------------------------------------------------------------------------- #
# 9. Recovery ergonomics — ship.sh must still work on a HALF-BROKEN host
#
# ship.sh is the tool you run when a host is already wrong. Making it hard-depend
# on a second file removed the escape hatch that used to exist (the constants
# were inline), so a missing lib/host-role.sh took out the recovery tool along
# with everything else.
# --------------------------------------------------------------------------- #
def _ship_copy_without_lib(tmp_path):
    """A copy of ship.sh whose lib/host-role.sh does NOT exist."""
    d = tmp_path / "brokenhost" / "scripts"
    d.mkdir(parents=True)
    (d / "ship.sh").write_text(SHIP.read_text())
    return d / "ship.sh"


def test_ship_without_the_lib_still_honours_an_explicit_role(tmp_path):
    """SHIP_ROLE must short-circuit detection without needing the lib."""
    ship = _ship_copy_without_lib(tmp_path)
    env = dict(os.environ, SHIP_ROLE="workbench", SHIP_REPO=str(tmp_path / "nope"))
    out = subprocess.run(["bash", str(ship), "--no-remote", "--no-switch"],
                         capture_output=True, text=True, env=env)
    combined = out.stdout + out.stderr
    assert out.returncode != 6, (
        "SHIP_ROLE no longer overrides a missing lib — the recovery tool is "
        f"unusable on a host missing one file\n{combined}"
    )
    assert "degraded recovery mode" in combined, combined


def test_ship_without_the_lib_and_without_a_role_fails_loudly(tmp_path):
    """The escape hatch is an ESCAPE HATCH: with no role there is no detection
    (detect_role is deliberately NOT duplicated here), so this must exit 6 and
    say what to do — not guess a role."""
    ship = _ship_copy_without_lib(tmp_path)
    env = {k: v for k, v in os.environ.items() if k != "SHIP_ROLE"}
    out = subprocess.run(["bash", str(ship), "--no-remote", "--no-switch"],
                         capture_output=True, text=True, env=env)
    combined = out.stdout + out.stderr
    assert out.returncode == 6, combined
    assert "SHIP_ROLE=workbench" in combined, combined


@pytest.mark.parametrize("script", ["ship.sh", "drift-check.sh"])
def test_invoking_through_a_symlink_still_finds_the_shared_lib(tmp_path, script):
    """${BASH_SOURCE[0]} is the INVOKING path, not the real one.

    Unresolved, a ~/bin shim or any PATH symlink makes both scripts look for
    lib/ next to the SYMLINK and exit 6. Latent today (no such symlink exists),
    but it is exactly the shape that bites during a recovery.
    """
    link = tmp_path / ("link-" + script)
    link.symlink_to(REPO_ROOT / "scripts" / script)
    out = subprocess.run(["bash", str(link), "--detect-role", "192.168.50.250"],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "workbench", out.stdout + out.stderr


def test_host_role_lib_is_safe_to_execute_directly():
    """It is mode 755, so `./scripts/lib/host-role.sh --detect-role` happens.

    Without a shebang the kernel hands it to /bin/sh; under a dash-ish /bin/sh
    ${BASH_SOURCE[0]} is EMPTY, the executed-not-sourced guard never matches, and
    the probe exits 0 having printed nothing — a silent no-op from a tool you are
    reading an answer out of.
    """
    executable = os.access(HOST_ROLE_LIB, os.X_OK)
    first = HOST_ROLE_LIB.read_text().splitlines()[0]
    # Assembled from character codes, not written as a literal: a quoted `#!` in
    # a test file is exactly what `test_runtime_shebangs.py` scans the repo for,
    # and this line would otherwise report itself as an offender.
    hashbang = chr(35) + chr(33)
    assert (not executable) or first.startswith(hashbang), (
        "host-role.sh is executable but has no shebang; first line: %r" % first
    )
    if executable:
        out = subprocess.run([str(HOST_ROLE_LIB), "--detect-role", "192.168.50.155"],
                             capture_output=True, text=True)
        assert out.stdout.strip() == "laptop", (out.returncode, out.stdout, out.stderr)


def test_the_timer_rationale_does_not_claim_laptop_behaviour():
    """A COMMENT IS A CLAIM. The timer is serverMode-gated and ~/.server-mode
    exists only on the workbench, so the timer never runs on the laptop —
    OnStartupSec cannot be justified by the laptop 'having just resumed'.
    """
    timer_start = HOME_NIX.index("systemd.user.timers.drift-check")
    rationale = HOME_NIX[max(0, timer_start - 1500):timer_start]
    assert "laptop is intermittent" not in rationale, (
        "the OnStartupSec rationale still describes laptop resume behaviour that "
        "cannot occur — the timer is workbench-only:\n" + rationale
    )
    assert "workbench" in rationale.lower(), rationale


def test_home_nix_documents_that_an_unreachable_remote_does_not_toast():
    """The alerting POLICY is the load-bearing part of this unit, and it lives in
    two files (the script decides, the unit alerts). Pin the unit-side claim so
    the two cannot silently disagree."""
    start = HOME_NIX.index("PASSIVE DRIFT DEADMAN")
    block = HOME_NIX[start:HOME_NIX.index("systemd.user.services.drift-check")]
    assert "serverMode" in block, "the block never states the timer is workbench-only"
    assert "DRIFT_UNREACHABLE_ESCALATE" in block, block
    assert "still exits 8" in block, block
