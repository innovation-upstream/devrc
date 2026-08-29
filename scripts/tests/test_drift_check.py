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

import json
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
# The SECOND lib the checker sources — the derived nix-read predicate behind
# rc 23. Its functions run inside the CHECK payload, so the reverse-PATH
# tokenizer sees their names in command position exactly as it sees
# host-role.sh's, and they are accounted for the same way.
NIXREAD_LIB = REPO_ROOT / "scripts" / "lib" / "nix_read_paths.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git + bash on PATH",
)


def age_histogram(rows, fuzzy):
    """A `summary.age_sources` histogram that is COHERENT — its values sum to
    `rows`, which is an invariant of the producer, not a nicety: `summarize()`
    derives `total_sessions = len(rows)` and `age_sources = _count_by(... for r
    in rows)` from the SAME list one line apart, so every row lands in exactly
    one bucket.

    🔴 THE HAND-WRITTEN VERSION OF THIS WAS NOT COHERENT, and that is worth
    recording. It was the literal `{"fuzzyclaw": fuzzy, "ledger": 27, "none":
    13}`, which sums to `fuzzy + 40` regardless of `rows` — so `sm_report(rows=
    47, fuzzy=0)`, the fixture behind the whole READY path, published a
    histogram accounting for 40 of 47 rows. Nothing could see it until the
    reader gained a coherence check, and then every READY test went red at once.
    A fixture that cannot occur in production tests a payload the code will
    never receive.

    🔴 A ZERO IS SPELLED BY ABSENCE, not by `fuzzyclaw: 0` — `_count_by` creates
    a key only for a value it OBSERVED. Reproduced here so the READY tests
    exercise the shape a real all-clear scan actually emits.

    The default `rows=47, fuzzy=7` reproduces the measured workbench payload
    exactly (`{fuzzyclaw: 7, ledger: 27, none: 13}`), and the three buckets are
    kept pairwise distinct so an assertion cannot pass by reading the wrong key.
    """
    out = {}
    if fuzzy:
        out["fuzzyclaw"] = fuzzy
    rest = rows - fuzzy
    if rest > 0:
        none = rest // 3
        if none == fuzzy:
            none += 1
        none = min(none, rest)
        if none:
            out["none"] = none
        if rest - none:
            out["ledger"] = rest - none
    return out


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
        self.state.mkdir(exist_ok=True)

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

    # -- the nix-read fixture tree (rc 23) ---------------------------------- #
    #
    # Committed through the BUILDER and then fast-forwarded in, never written
    # straight into `work`: a file written into the checkout would itself be
    # untracked, and a fixture that is part of the population under measurement
    # cannot be trusted to isolate anything.
    #
    # 🔴 Names pairwise distinct, and distinct from every constant the assertions
    # name (LIVE, DROPPED, nix-read, rc23). `kilo-live.txt` is deliberately NOT
    # committed — it is a mkOutOfStoreSymlink target that only ever exists as an
    # untracked file, which is exactly the shape the ladder is about.
    NIXREAD_FLAKE = (
        "{\n"
        "  outputs = { self, ... }: {\n"
        "    homeConfigurations.someone.modules = [ ./nix/home.nix ];\n"
        "  };\n"
        "}\n"
    )
    NIXREAD_HOME_NIX = (
        "{ config, ... }:\n"
        'let workspace = "${config.home.homeDirectory}/workspace";\n'
        "in {\n"
        '  home.file.".alpha".source = ../copied-alpha.txt;\n'
        '  home.file.".bravo".source = ../charlie-dir;\n'
        # A repo-ROOT file whose NAME collides with a header field of the FACT
        # line the payload emits. Deliberately named `reason`: it is the one
        # spelling that can be mistaken for `reason=<TOKEN>`, and the parser
        # matched it as one until the arm order was fixed.
        '  home.file.".papa".source = ../reason;\n'
        '  home.file.".delta".source =\n'
        '    config.lib.file.mkOutOfStoreSymlink "${workspace}/devrc/kilo-live.txt";\n'
        "}\n"
    )

    def seed_nix_read(self, *, with_lib=True, with_flake=True):
        """Land a real flake shape + the predicate lib on origin/main and here."""
        b = self.builder
        (b / "nix").mkdir(parents=True, exist_ok=True)
        added = []
        if with_flake:
            (b / "flake.nix").write_text(self.NIXREAD_FLAKE)
            (b / "nix" / "home.nix").write_text(self.NIXREAD_HOME_NIX)
            added += ["flake.nix", "nix/home.nix"]
        (b / "copied-alpha.txt").write_text("alpha\n")
        (b / "charlie-dir" / "nested").mkdir(parents=True, exist_ok=True)
        (b / "charlie-dir" / "nested" / "foxtrot.txt").write_text("foxtrot\n")
        added += ["copied-alpha.txt", "charlie-dir/nested/foxtrot.txt"]
        if with_lib:
            lib = b / "scripts" / "lib" / "nix_read_paths.sh"
            lib.parent.mkdir(parents=True, exist_ok=True)
            lib.write_text(NIXREAD_LIB.read_text())
            added.append("scripts/lib/nix_read_paths.sh")
        self.git(b, "add", *added)
        self.git(b, "commit", "-q", "-m", "seed the nix-read fixture")
        self.git(b, "push", "-q", "origin", "main")
        self.catch_up()

    def add_local_commit(self, msg="un-pushed local work"):
        p = self.work / ("local-%d.txt" % len(list(self.work.glob("local-*.txt"))))
        p.write_text(msg + "\n")
        self.git(self.work, "add", p.name)
        self.git(self.work, "commit", "-q", "-m", msg)

    # -- the subject under test --------------------------------------------- #
    def check(self, *args, repo=None, script=None, **envextra):
        env = self.env(
            SHIP_ROLE="workbench",           # bypass IP detection (no `ip` here)
            DRIFT_REPO=str(repo or self.work),
            # 🔴 THE FOURTH HERMETICITY SEAM, and it caught a live breach the
            # moment it was added. The fuzzyclaw phase-2 gate EXECS
            # `scripts/session-manager`, which scans the operator's REAL tmux —
            # unstubbed, every test in this file did exactly that (48 live
            # windows, measured), and one of them declared "READY — 0 of 48"
            # because the fixture $HOME has no fuzzyclaw task files. A
            # read-only breach is still a breach, and the verdict it produced
            # was wrong.
            #
            # Defaulted to a path INSIDE tmp_path that does not exist, so the
            # gate takes its no-session-manager branch. A test that wants the
            # gate to answer calls `stub_session_manager()`, exactly like
            # `stub_ssh`.
            DRIFT_SESSION_MANAGER=str(self.bin / "session-manager"),
            # 🔴 THE FIFTH HERMETICITY SEAM, and the same one the phase-2 gate
            # already paid for. The branch-protection arm (rc 24) runs `gh`,
            # which on this machine is authenticated and talks to the real
            # GitHub. Left to its default every test in this file would query a
            # live repo over the network — a read-only breach is still a breach,
            # and the arm's verdict would then depend on the state of a remote
            # nobody in this suite controls.
            #
            # Defaulted to a path inside tmp_path that does not exist, so the arm
            # takes its no-gh branch. A test that wants it to answer calls
            # `stub_gh()`, exactly like `stub_ssh`. (Belt and braces: the fixture
            # origin is a file:// path, so the slug derivation refuses before gh
            # is ever consulted — two independent reasons, deliberately.)
            DRIFT_GH=str(self.bin / "gh"),
            # Pinned into tmp_path: the unreachable streak is PERSISTENT state,
            # and left to its default ($XDG_STATE_HOME/…) these tests would both
            # write to the operator's real state dir and inherit a streak from
            # whatever ran before them.
            DRIFT_STATE_DIR=str(self.state),
        )
        env.update(envextra)   # per-test overrides win (e.g. a blocked state dir)
        # `script` runs a COPY of the checker whose `lib/` a test controls. The
        # reader path is derived from the script's own resolved dirname and is
        # deliberately NOT env-overridable, so a copy is the only way to drive
        # the shell against a reader that breaks the output contract.
        proc = subprocess.run(
            ["bash", str(script or DRIFT), *args],
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

    def stub_gh(self, stdout="", exit_code=0, log=None):
        """Install a stub `gh` that prints `stdout` and exits `exit_code`.

        🔴 `stdout` is the ONE LINE the arm's `--jq` produces, and the shapes
        used here are MEASURED against the live API on 2026-08-29, not invented:

            true 2      innovation-upstream/devrc, healthy
                        (contexts tekton/devrc-pytests + tekton/devrc-nodetests)
            false 0     a repo with no protection object at all

        The third shape — `true 0`, a standing protection object whose
        required_status_checks was DELETED out of it — is the one the incident
        actually produced, and it is the reason the verdict reads the COUNT and
        never `protected`.

        `log` names a file the stub appends its argv to, so a test can assert
        WHAT was asked rather than only what came back.
        """
        body = []
        if log is not None:
            body.append('printf "%s\\n" "$*" >> ' + f"'{log}'")
        if stdout:
            body.append("echo '%s'" % stdout.replace("'", "'\\''"))
        body.append("exit %d" % exit_code)
        write_exec(self.bin / "gh", "\n".join(body) + "\n")

    def set_origin(self, url):
        """Point the work clone's origin at `url`.

        The arm derives the repo slug from `git ls-remote --get-url origin`, so
        this is how a test gives it a GitHub remote to reason about. It does NOT
        make anything reachable — the fixture never has a network — which is the
        point: the slug and the API answer are independently drivable.
        """
        self._run(["git", "-C", str(self.work), "remote", "set-url", "origin", url])

    def stub_session_manager(self, payload, exit_code=0):
        """Install a stub `session-manager` that prints `payload` on stdout.

        `payload` is a dict (serialised to JSON) or a raw string, so both the
        well-formed reports and the malformed ones — a crash, an empty stream,
        a truncated body — are drivable. The real binary is never executed by
        this suite: it scans the operator's live tmux.
        """
        text = payload if isinstance(payload, str) else json.dumps(payload)
        body = "cat <<'SM_JSON_EOF'\n%s\nSM_JSON_EOF\nexit %d\n" % (
            text, exit_code)
        write_exec(self.bin / "session-manager", body)

    def sm_report(self, rows=47, fuzzy=7, host="workbench", files_seen=400,
                  status="ok"):
        """The SHAPE `session-manager scan --json` really emits, cut down to the
        four facts the phase-2 reader consults.

        Pinned against the live payload rather than invented: `age_sources` is
        a histogram keyed by WRITER (`ledger`/`fuzzyclaw`/`none`) and
        `total_sessions` is the row count, both under `summary`. Measured on
        the workbench 2026-08-15 — `{fuzzyclaw: 7, ledger: 27, none: 13}` over
        47 rows — which is the default here so the fixture is a real
        observation and not a round number.
        """
        return {
            "local_host": host,
            "fuzzyclaw": {"status": status, "files_seen": files_seen},
            "summary": {
                "total_sessions": rows,
                "age_sources": age_histogram(rows, fuzzy),
            },
        }


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
    assert "un-pushed commit(s)" in out, out
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

    🔴 This is THE TIMER'S SHAPE — both legs on, which is what ExecStart runs.
    It used to be written as `--no-local`, which made it a run that checked NO
    host at all and passed for a second reason entirely (see
    `test_no_local_plus_an_unreachable_remote_is_not_a_pass`, which is now the
    rc 2 case). The property being pinned here is "the remote leg does not
    poison a successfully-checked local host", so the local host has to actually
    be checked.
    """
    fleet.catch_up()
    fleet.stub_ssh(255)
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid")
    assert rc == 0, f"a single unreachable run must not fail the unit, got {rc}\n{out}"
    assert "UNREACHABLE" in out
    assert "not a pass" in out, out                  # still not sold as a green
    assert "1/4 consecutive" in out, out
    assert "NOT escalated" in out, out
    # ...and the summary must name the host it DID check, not "both hosts".
    assert "workbench (local)" in out, out
    assert "both hosts" not in out, out


def test_no_local_plus_an_unreachable_remote_is_not_a_pass(fleet):
    """🟢 THE OTHER 'checked nothing' PATH, which used to exit 0.

    `--no-local` with the remote unreachable BELOW the escalation threshold
    printed "NO HOST WAS SUCCESSFULLY CHECKED — this is not a clean bill of
    health" and then handed systemd a 0. systemd reads the code, not the text.

    It is the same shape `--no-local --no-remote` is already rc 2 for, and it is
    NOT reachable from the timer (ExecStart passes no flags, so the local leg is
    always checked) — this is consistency between the two paths, not a live bug.
    """
    fleet.stub_ssh(255)
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid")
    assert rc == 2, (
        f"a run that observed no host at all must not exit 0, got {rc}\n{out}"
    )
    assert "NO HOST WAS SUCCESSFULLY CHECKED" in out, out
    assert "no drift" not in out, out
    # ...and it is still the UNESCALATED unreachable case underneath, i.e. the
    # rc 2 is coming from "checked nothing", not from an escalation.
    assert "1/4 consecutive" in out and "NOT escalated" in out, out


def test_checked_nothing_does_not_rewrite_a_real_verdict(fleet):
    """🔴 THE FAILURE MODE OF THE FIX ABOVE.

    Making "checked nothing" non-zero must not touch the aggregation: a genuine
    rc 8 has to stay exactly 8, because 8 is the code with the rescue-before-
    reset procedure and the one the legend is read against. A 2 here would still
    fail the unit and still toast — and would describe the incident wrong.
    """
    fleet.stub_ssh(8, stdout="[laptop] AHEAD by 3 un-pushed commit(s).")
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid")
    assert rc == 8, f"a real remote verdict was rewritten to a usage code: {rc}\n{out}"

    # ...and the same for an ESCALATED unreachable, which also checks no host:
    # it must stay 13, not become 2.
    fleet.stub_ssh(255)
    for _ in range(2):
        rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                              DRIFT_UNREACHABLE_ESCALATE="2")
    assert rc == 13, f"an escalated unreachable was rewritten to rc 2: {rc}\n{out}"
    assert "ESCALATING" in out, out


def test_unreachable_remote_escalates_after_n_consecutive_runs(fleet):
    """🔴 THE OTHER DIRECTION: the softening must not make the deadman mute.

    A host that has been unlookable for the whole threshold window is no longer
    'the laptop is shut' — at that point NOT alerting would be the deadman
    failing at its one job. Threshold reached -> rc 13 -> unit failed -> toast.
    """
    fleet.catch_up()          # local is clean, so the ladder is the only variable
    fleet.stub_ssh(255)
    codes = []
    for _ in range(4):
        rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
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
        rc, _ = fleet.check(REMOTE_SSH="stub@example.invalid",
                            DRIFT_UNREACHABLE_ESCALATE="3")
        assert rc == 0

    fleet.stub_ssh(0, stdout="[laptop] clean")       # it answers
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
                          DRIFT_UNREACHABLE_ESCALATE="3")
    assert rc == 0, out

    fleet.stub_ssh(255)                              # ...and goes away again
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
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


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory write permissions"
)
def test_unreachable_escalates_when_the_streak_FILE_cannot_be_written(fleet):
    """🔴 THE SECOND FAIL-CLOSED LIMB — the one the test above does NOT reach.

    `test_unreachable_escalates_immediately_when_the_streak_cannot_be_persisted`
    points DRIFT_STATE_DIR at a regular FILE, so `mkdir -p` fails and the
    function returns from its FIRST limb. The `printf … > "$f" || echo -1` limb
    — an existing, readable, but UNWRITABLE state dir — was never executed by
    any test, and mutating its `echo -1` to `echo 0` left the whole suite green.

    That mutant is not cosmetic: `streak_bump` would return 0 forever, 0 is
    below every threshold, and the escalation ladder goes PERMANENTLY MUTE —
    the silent deadman this entire feature exists to prevent.
    """
    ro = fleet.root / "readonly-state"
    ro.mkdir()
    counter = ro / "unreachable-laptop"
    counter.write_text("2\n")                        # a streak already in flight
    # BOTH are required. A mode-500 DIRECTORY does not stop a write to a file
    # that already exists inside it (directory write permission gates create and
    # unlink, not the open-for-write of an existing inode) — with only the
    # chmod on the dir this test passes on the `echo 0` mutant, i.e. it would be
    # a vacuous guard. The read-only FILE is what makes the redirection fail
    # while `cat` still succeeds, so `prev` is a real 2 and the -1 can only come
    # from the write limb.
    counter.chmod(0o400)
    ro.chmod(0o500)
    try:
        fleet.stub_ssh(255)
        rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid",
                              DRIFT_STATE_DIR=str(ro))
    finally:
        ro.chmod(0o700)                              # so tmp_path can be cleaned
        counter.chmod(0o600)
    assert rc == 13, (
        "an unpersistable streak must escalate immediately — 'for how long' is "
        f"unknowable, so going quiet is an unbounded silent window. got {rc}\n{out}"
    )
    assert "could not be" in out and "persisted" in out, out
    # ...and it must be the UNKNOWN-streak branch, not the threshold branch: the
    # counter on disk still says 2, below the default threshold of 4.
    assert "CONSECUTIVE unreachable checks" not in out, (
        "escalated via the threshold branch, so this does not exercise the "
        "cannot-persist limb at all\n" + out
    )
    # 🟢 …and it does so QUIETLY. The unit's only output is the journal, where
    # every line is either a `[host]` per-host line or a `drift-check:` summary.
    # `> "$f" 2>/dev/null` cannot suppress the SHELL's own message for a failed
    # redirection (fd 2 is still the terminal when the open fails), so it leaked
    # a raw `drift-check.sh: line NNN: …: Permission denied`. Reordering to
    # `2>/dev/null > "$f"` applies the redirections in the other order and fixes
    # it. Cosmetic — escalation fired correctly either way — but this is the one
    # code path whose output a human only ever reads under stress.
    stray = [
        ln for ln in out.splitlines()
        if ln.strip() and not ln.startswith(("[", "===", "drift-check: ", "  "))
    ]
    assert stray == [], (
        "unprefixed output leaked into the journal between the [host] lines: %r" % stray
    )


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
    fleet.catch_up()
    fleet.stub_ssh(255)
    for _ in range(2):                       # laptop misses twice
        rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
                              SHIP_ROLE="workbench", DRIFT_UNREACHABLE_ESCALATE="3")
        assert rc == 0, out
    # Now the OTHER direction: from the laptop, the remote is the workbench.
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
                          SHIP_ROLE="laptop", DRIFT_UNREACHABLE_ESCALATE="3")
    assert rc == 0, out
    assert "1/3 consecutive" in out, (
        "the workbench inherited the laptop's streak — one shared counter\n" + out
    )


# --------------------------------------------------------------------------- #
# THE REMOTE-PAYLOAD LEDGER
#
# 🔴 DERIVED FROM THE printf, NEVER HAND-LISTED. Both guards below used to name
# their variables literally, and both went stale the moment a fourth value
# started crossing the ssh hop: DRIFT_NIXDIRT_MAX was interpolated into the
# payload that EXECUTES on the other host while the %q assertion still listed
# DRIFT_LABEL/DRIFT_UNTRACKED_MAX and the injection parametrize still listed
# DRIFT_UNTRACKED_MAX/DRIFT_UNREACHABLE_ESCALATE. A mutant that deleted
# `require_int DRIFT_NIXDIRT_MAX` **and** downgraded its %q to %s SURVIVED
# 304/304. DRIFT_DANGLING_MAX had been uncovered by both for longer.
#
# So the set is read out of the printf format string itself. Adding a fifth
# forwarded value cannot be invisible to this suite, and REMOVING one cannot
# leave a guard pointing at nothing: `_forwarded()` fails loudly if it cannot
# find the format at all, which is the positive control for its own regex.
# --------------------------------------------------------------------------- #
#
# The one forwarded value that is NOT an integer, enumerated with its reason
# rather than pattern-matched. It is a ROLE NAME chosen by this script from
# `resolve_local_role`, never by the operator, so there is nothing for
# require_int to check; it still crosses the hop, so it still needs %q.
_FORWARDED_NON_INT = {"DRIFT_LABEL"}


def _forwarded():
    """[(name, conversion)…] for every value interpolated ahead of the REMOTE
    payload, read off the `printf` format string in drift-check.sh."""
    src = DRIFT.read_text()
    m = re.search(r"REMOTE_OUT=\"\$\(printf '([^']*)'", src)
    assert m, (
        "could not find the remote payload's printf format at all — this "
        "ledger is now wired to nothing, which is worse than a stale list"
    )
    fmt = m.group(1)
    pairs = re.findall(r"([A-Z][A-Z0-9_]*)=%(\w)", fmt)
    assert len(pairs) >= 2, (
        "the ledger derived %r from %r; a one-element result is what a broken "
        "regex looks like" % (pairs, fmt)
    )
    return pairs


def test_the_remote_payload_ledger_can_see_the_real_variables():
    """🔴 POSITIVE CONTROL for the derivation the two guards below stand on.

    A regex that matched nothing would make both of them vacuously green, which
    is precisely the failure they exist to end. So: it must find the payload's
    own DRIFT_LABEL, and it must find at least one of the *_MAX values that make
    this a ledger rather than a single-variable check.
    """
    names = [n for n, _c in _forwarded()]
    assert "DRIFT_LABEL" in names, names
    assert [n for n in names if n.endswith("_MAX")], (
        "the derived set names no capped tunable, so a cap could be forwarded "
        "unnoticed again: %r" % names
    )


def test_every_variable_forwarded_to_the_remote_host_is_shell_quoted():
    """STRUCTURAL, and labelled as such.

    `require_int` already rejects every value that could inject, so swapping %q
    back to %s is behaviourally invisible to this suite — a mutant that survives
    every behavioural test. %q is the second layer, and the only way to hold a
    second layer whose first layer never lets anything through is to assert it
    is there. Asserted over the DERIVED set, so it covers the next one too.
    """
    bad = [(n, c) for n, c in _forwarded() if c != "q"]
    assert bad == [], (
        "these values are interpolated into a script that EXECUTES on the other "
        "host without %%q-quoting: %r" % (bad,)
    )


def test_every_INTEGER_forwarded_to_the_remote_host_is_range_validated():
    """The FIRST layer of the same pair, over the same derived set.

    %q makes an injected value inert; require_int/require_positive_int stops it
    before it is ever sent. Each is asserted separately because each can be
    deleted separately, and a mutant that removed only the validation left the
    payload well-formed and every behavioural test green.
    """
    src = DRIFT.read_text()
    missing = []
    for name, _conv in _forwarded():
        if name in _FORWARDED_NON_INT:
            continue
        if not re.search(r"^require(_positive)?_int %s " % name, src, re.M):
            missing.append(name)
    assert missing == [], (
        "forwarded to the other host with no integer validation, so a "
        "non-integer reaches an ssh payload: %r" % missing
    )


@pytest.mark.parametrize(
    "var", sorted({n for n, _c in _forwarded()} - _FORWARDED_NON_INT))
def test_no_forwarded_tunable_can_inject_a_command_into_the_remote_payload(fleet, var):
    """🔴 THE BEHAVIOURAL HALF, derived from the same ledger.

    Each of these is INTERPOLATED INTO A SCRIPT THAT RUNS ON THE OTHER HOST.
    `<VAR>='10; echo INJECTED-COMMAND-RAN'` used to be executed remotely — a
    passivity hole on the far side of the ssh hop, which the static scanner
    structurally cannot see (it only reads this file). Operator-supplied, so low
    exploitability, but a deadman forbidden from touching a host must not
    contain a path that runs arbitrary commands on it.
    """
    fleet.stub_ssh(0, stdout="[laptop] clean")
    rc, out = fleet.check(
        "--no-local",
        REMOTE_SSH="stub@example.invalid",
        **{var: "10; echo INJECTED-COMMAND-RAN"},
    )
    # The marker may legitimately appear INSIDE the rejection message (it quotes
    # the offending value back). What must never appear is the marker as the
    # OUTPUT OF A COMMAND — i.e. on a line of its own.
    ran = [ln for ln in out.splitlines() if ln.strip() == "INJECTED-COMMAND-RAN"]
    assert not ran, f"remote command injection via {var}: the payload executed\n{out}"
    assert rc == 2, f"a non-integer {var} must be a usage error, got {rc}\n{out}"
    assert var in out, out
    assert "must be an integer" in out or "must be a non-negative integer" in out, out


def test_clean_local_plus_clean_remote_is_green(fleet):
    """Positive control for the two-host path, including the ssh leg."""
    fleet.catch_up()
    fleet.stub_ssh(0, stdout="[laptop] clean")
    rc, out = fleet.check(REMOTE_SSH="stub@example.invalid")
    assert rc == 0, f"expected rc0, got {rc}\n{out}"
    assert "no drift" in out


# NOTE: the DRIFT_UNTRACKED_MAX injection case that used to live here is now the
# `test_no_forwarded_tunable_can_inject_a_command_into_the_remote_payload`
# parametrize above, driven off the payload's own printf. Keeping a hand-named
# copy beside a derived ledger is how the derived one stops being read.


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

# 🔴 Commands whose ARGUMENTS are themselves a command line. Before these were
# handled, `_classify` looked only at toks[0], so EVERY one of these hid a
# mutation from the scanner:
#
#     ssh "$REMOTE_SSH" git checkout -q -B evil     bash -c "git reset --hard"
#     timeout 5 git checkout main                   flock /tmp/l git checkout main
#     stdbuf -oL git checkout main                  nice -n 5 git reset --hard
#
# The behavioural layer catches every one of those that touches the LOCAL
# checkout (`test_run_against_diverged_repo_mutates_nothing`). The `ssh <target>
# …` shape is the one that was invisible to BOTH layers: it mutates the OTHER
# host — this script's primary hazard — and `ssh` is stubbed in every test here,
# so the injected line ran green. `ssh …` and `bash -c` are also shapes already
# present in drift-check.sh, so a maintainer writing one is entirely plausible.
_WRAPPERS = frozenset({
    "timeout", "flock", "stdbuf", "ionice", "nice", "chrt", "setsid",
})
_SHELL_RUNNERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_REMOTE_RUNNERS = frozenset({"ssh", "rsh", "doas", "chroot"})

# Aliasing a command into a variable (`g=git; $g checkout main`) defeats any
# argv-based scanner, because the second segment's command word is `$g`. It
# cannot be resolved statically, so the ALIAS ITSELF is what gets flagged.
_ALIASABLE = frozenset({"git"}) | DESTRUCTIVE_COMMANDS

_MAX_NEST = 4


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


def _classify_nested(toks, depth):
    """Classify the innermost command of a wrapper / `ssh host …` / `sh -c …`.

    Their argument grammars all differ (`timeout 5 CMD`, `flock -n /lock CMD`,
    `ssh -o X=y host CMD`, `bash -c 'CMD'`) and a parser that gets each exactly
    right is one nobody will maintain. So: try EVERY suffix and report the first
    that classifies.

    That over-approximates — `ssh host cat ./rm` would flag — and that is the
    correct direction for a passivity scanner, which must fail CLOSED. The cost
    of over-approximating is false positives, and a scanner that fails the suite
    on legitimate code gets deleted by the next maintainer; the two guards that
    hold that side are `test_scanner_does_not_flag_legitimate_read_only_lines`
    (which includes the real `ssh … bash -s` and `bash -c "$CHECK"` lines) and
    `test_drift_check_source_never_mutates` over the whole real file.
    """
    if depth >= _MAX_NEST:
        return None
    for i in range(1, len(toks)):
        r = _classify(" ".join(toks[i:]), depth + 1)
        if r:
            return r
    return None


def _classify(seg, depth=0):
    # Leading assignments are stripped by _command_tokens, so an alias would
    # otherwise vanish entirely. Inspect them BEFORE that happens.
    for t in seg.split():
        m = re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*=(.+)", t)
        if not m:
            break
        if m.group(1).strip("\"'").rsplit("/", 1)[-1] in _ALIASABLE:
            return "%s (a command aliased into a variable — defeats an argv scan)" % t

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
    if cmd in ("sed", "perl") and any(t.startswith("-i") for t in toks[1:]):
        return "%s -i (in-place edit)" % cmd
    if cmd == "find" and any(
        t in ("-delete", "-exec", "-execdir", "-fprint", "-fprintf") for t in toks[1:]
    ):
        return "find with a mutating action"
    if cmd in DESTRUCTIVE_COMMANDS:
        return "%s (destructive command)" % cmd
    if cmd in _WRAPPERS or cmd in _SHELL_RUNNERS or cmd in _REMOTE_RUNNERS:
        return _classify_nested(toks, depth)
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
    # --- INDIRECT / WRAPPED invocations (the #369 delta-audit finding) --------
    # 🔴 The first one is the one that mattered: inserted into drift-check.sh it
    # ran the FULL suite green (105 passed), because it mutates the OTHER host —
    # which `ssh` is stubbed out of in every behavioural test — and toks[0] was
    # `ssh`, so the static layer never looked past it. Both layers blind at once.
    'ssh "$REMOTE_SSH" git checkout -q -B evil',
    'ssh -o BatchMode=yes host git reset --hard origin/main',
    'ssh host home-manager switch --flake .',
    'bash -c "git reset --hard"',
    "sh -c 'git checkout main'",
    "timeout 5 git checkout main",
    "flock /tmp/lock git checkout main",
    "flock -n /tmp/lock git reset --hard",
    "stdbuf -oL git checkout main",
    "ionice -c3 git clean -fdx",
    "nice -n 5 git reset --hard",
    'timeout 5 rm -rf "$repo"',
    "g=git",                                    # the alias itself is the flag
    "find . -name '*.txt' -delete",
    'perl -i -pe "s/a/b/" "$repo/f"',
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
    # The two REAL indirect-invocation lines in drift-check.sh. The wrapper
    # recursion added for the shapes above over-approximates on purpose, so
    # these are the guard that it did not over-approximate onto the script
    # itself — without them the recursion could be flagging everything and this
    # suite could not tell.
    'ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_SSH" bash -s',
    'bash -c "$CHECK"',
    "timeout 10 git fetch origin -q",
    'ssh "$REMOTE_SSH" git rev-parse -q --verify origin/main',
    'DRIFT_REPO="$DRIFT_REPO" DRIFT_LABEL="$LOCAL_ROLE" bash -c "$CHECK"',
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

    🔴 This test used to also assert `enableDriftDeadman = false;` — "the master
    switch must ship OFF, enabling a timer is a live change to a host and belongs
    in its own supervised deploy". #406 IS that supervised deploy. It changed
    `nix/home.nix` and nothing else, so the assertion outlived its own premise and
    left `main` RED — `collected=7785 passed=7783 failed=1`, the only failure in
    either tier.

    WHAT #406 MEASURED, because it is the part worth keeping. The switch had sat
    false since #367 behind two preconditions a scratch clone structurally cannot
    test:

      1. the ssh leg reaches the laptop from a systemd-user context (a user unit
         has no ssh-agent) — a hand-run did it for real and found GENUINE drift
         on its first run: the laptop 1 commit behind origin/main, rc 10;
      2. the failure toast actually DISPLAYS — and this one was **FALSE** at
         first. dunst was paused with 286 notifications queued, so the toast was
         sent, exited 0, and was silently binned. Four rounds of auditing had
         confirmed the script hands systemd the right exit code and that
         OnFailure was wired; none of them could see that the notifier's output
         went nowhere.

    (2) is why the flag waited, and it is the reason to distrust a green here: a
    timer alerting into a paused queue manufactures the appearance of coverage
    over exactly the failure it exists to catch.

    A process rule ("do the enabling in its own deploy") is not a property a unit
    test can hold, and the one that tried became a gate everybody would have to
    merge through. What IS testable, and is what the gating actually depends on,
    is that the switch is still declared EXPLICITLY and exactly once — deleted,
    defaulted, or bound twice, `lib.optionals (serverMode && enableDriftDeadman)`
    either fails to evaluate or quietly stops meaning anything while the
    structural assertion above keeps passing and gates nothing. Deliberately
    agnostic about the VALUE: pinning that again would re-break the gate the next
    time someone legitimately changes it.
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
    declared = re.findall(r"^\s*enableDriftDeadman = (true|false);", HOME_NIX, re.M)
    assert len(declared) == 1, (
        "the master switch must be declared exactly once, explicitly true or false — "
        f"found {len(declared)}: {declared}. Gating on a name that is absent, defaulted "
        "or set twice makes the WantedBy assertion above pass while gating nothing."
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
    # Added by #369 and missing from the accounting until #371 — the table's own
    # docstring calls itself "the accounting", so a command the scripts run and
    # the table omits makes that claim false. No live risk: all three are in
    # pkgs.coreutils, which the unit's PATH already carries. `readlink` is the
    # least obviously safe of them — it resolves BASH_SOURCE so the script can
    # find lib/host-role.sh when invoked through a symlink, and without it the
    # script exits 6 with "cannot read …/lib/host-role.sh".
    "readlink": "pkgs.coreutils",
    "mkdir": "pkgs.coreutils",
    "cat": "pkgs.coreutils",
    # Added by the host-parity payload, and only VISIBLE to the reverse guard
    # because that payload deliberately splits `sed … | sort | tr …` across two
    # lines. Written as the obvious one-liner the tokenizer collapses the whole
    # pipeline into a single `sed` segment, `sort` is never seen, and the guard
    # goes green having accounted for nothing. Measured both ways.
    "sort": "pkgs.coreutils",
    # Found only by the REVERSE guard below, never by review: `dirname` builds
    # the path to lib/host-role.sh, and `bash` is what the local leg and the
    # remote payload are both handed to.
    "dirname": "pkgs.coreutils",
    "bash": "pkgs.bash",
    # The fuzzyclaw phase-2 gate: `timeout` caps the scan, `python3` runs
    # lib/drift_phase2.py over its JSON. Both are resolved from PATH BY THIS
    # SCRIPT, which is what puts them in this table rather than the child table
    # below.
    "timeout": "pkgs.coreutils",
    "python3": "pkgs.python3",
    # The branch-protection arm (rc 24). Same silent-failure shape as `ip` and
    # `python3` above: without it on the unit PATH the arm reports COULD NOT
    # MEASURE on every timer run forever, from a unit that looks correct — and
    # what it watches is the merge gate that was found deleted twice in one day
    # with nothing else looking. The script resolves it itself (the DRIFT_GH
    # default is the bare word `gh`), which is what puts it in THIS table rather
    # than the child one below.
    "gh": "pkgs.gh",
}

# 🔴 A SECOND SEAM, AND THE TABLE ABOVE STRUCTURALLY CANNOT SEE IT. The phase-2
# gate EXECS `scripts/session-manager`, and that child resolves binaries of its
# own from the same unit PATH: `python3` (its `#!/usr/bin/env python3` shebang)
# and `tmux` (`tmux list-panes -a`, its only subprocess on a local scan). Neither
# word ever appears in drift-check.sh, so the reverse tokenizer cannot find them
# and `test_every_command_the_checker_runs_is_on_the_unit_path` would fail if
# they were added above (it asserts the command IS called by the scripts).
#
# The failure is the silent kind this file keeps meeting: nothing crashes. The
# gate reports COULD NOT MEASURE on every timer run, forever, from a unit that
# looks correct — a checker wired to nothing, wearing an honest error message.
CHILD_PATH_REQUIREMENTS = {
    "python3": "pkgs.python3",
    "tmux": "pkgs.tmux",
}

# Shell builtins/keywords: present as command words, never resolved via PATH.
_SHELL_BUILTINS = frozenset("""
    : . [ alias bg bind break builtin caller cd command compgen complete
    continue declare dirs disown echo enable eval exec exit export false fc fg
    getopts hash help history jobs let local logout mapfile popd printf pushd
    pwd read readonly return set shift shopt source suspend test times trap
    true type typeset ulimit umask unalias unset wait
""".split())

# 🔴 NOT commands — prose. `_walk` splits at `$(`, and `$((` starts with it, so
# arithmetic inside a quoted message (`say "... and $(( n - maxu )) more"`)
# ends the printer segment and leaves the REST OF THE SENTENCE looking like a
# command line. Kept as an asserted ledger rather than a silent filter: a new
# entry here is a maintainer declaring "this word is prose", which is a claim a
# reviewer can check, whereas a heuristic filter would swallow a real command.
_PROSE_NOT_COMMANDS = frozenset({
    "a", "f", "n", "prev", "more", "see", "the", "laptop", "workbench",
})


def _defined_functions(text):
    return set(re.findall(r"^\s*([A-Za-z_][A-Za-z_0-9]*)\s*\(\)", text, re.M))


def _candidate_command_words():
    """Every lowercase word that appears in COMMAND POSITION in either script."""
    words = set()
    for path in (DRIFT, HOST_ROLE_LIB):
        for ln in path.read_text().splitlines():
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            for seg in _walk(ln):
                toks = _command_tokens(seg)
                if toks:
                    words.add(toks[0].rsplit("/", 1)[-1])
    return {w for w in words if re.fullmatch(r"[a-z][a-z0-9._-]*", w)}


def test_the_unit_path_table_accounts_for_every_command_the_scripts_run():
    """🔴 THE REVERSE DIRECTION — and the one that can actually bite.

    `test_every_command_the_checker_runs_is_on_the_unit_path` only walks the
    table and asks "is this still used?". It is one-directional: a command ADDED
    to the scripts and never added to the table is invisible to it, so the
    docstring calling the table "the accounting" was false the moment #369 added
    `readlink`, `mkdir` and `cat` (harmless — all in coreutils, already on the
    PATH — but the next addition need not be).

    This walks the SCRIPTS and asks "is this accounted for?". It found `dirname`
    and `bash`, neither of which any reviewer noticed across two rounds.

    The failure it ultimately guards is silent: a command missing from the unit
    PATH does not crash — `ip` going missing makes host detection return
    "unknown" and the deadman exit 6 forever, from a unit that looks correct.
    """
    known = (
        set(UNIT_PATH_REQUIREMENTS)
        | _SHELL_BUILTINS
        | _PROSE_NOT_COMMANDS
        | _defined_functions(DRIFT.read_text())
        | _defined_functions(HOST_ROLE_LIB.read_text())
        | _defined_functions(NIXREAD_LIB.read_text())
    )
    unaccounted = sorted(_candidate_command_words() - known)
    assert unaccounted == [], (
        "these run as commands in drift-check.sh / lib/host-role.sh but are in "
        "neither UNIT_PATH_REQUIREMENTS nor the builtin/prose ledgers: %r\n"
        "Add each to UNIT_PATH_REQUIREMENTS with the nixpkgs attr that provides "
        "it (and to the unit's makeBinPath in nix/home.nix), or — if it is prose "
        "that only LOOKS like a command — to _PROSE_NOT_COMMANDS." % unaccounted
    )


def test_the_reverse_path_guard_can_actually_see_a_new_command(tmp_path):
    """🔴 POSITIVE CONTROL. The guard above passing is indistinguishable from a
    tokenizer wired to nothing — an empty set minus anything is still empty. So:
    watch the number move.

      * the tokenizer must find the real commands (a non-zero count), and
      * a command INSERTED into a copy of the script must come out unaccounted.
    """
    found = _candidate_command_words()
    assert {"git", "ssh", "awk"} <= found, (
        "the tokenizer does not see the commands the script definitely runs: %r" % found
    )

    # Insert an unaccounted command into a COPY and re-derive from that copy.
    copy = tmp_path / "drift-check.sh"
    copy.write_text(DRIFT.read_text() + '\njq -r .x "$f"\n')
    words = set()
    for ln in copy.read_text().splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        for seg in _walk(ln):
            toks = _command_tokens(seg)
            if toks:
                words.add(toks[0].rsplit("/", 1)[-1])
    words = {w for w in words if re.fullmatch(r"[a-z][a-z0-9._-]*", w)}
    known = (
        set(UNIT_PATH_REQUIREMENTS) | _SHELL_BUILTINS | _PROSE_NOT_COMMANDS
        | _defined_functions(DRIFT.read_text())
        | _defined_functions(HOST_ROLE_LIB.read_text())
        | _defined_functions(NIXREAD_LIB.read_text())
    )
    assert sorted(words - known) == ["jq"], (
        "an added command was NOT reported as unaccounted: %r" % sorted(words - known)
    )


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


# --------------------------------------------------------------------------- #
# 10. HOST PARITY — the drift git is structurally blind to
#
# 🔴 WHY THIS SECTION EXISTS. Everything above answers "is this host still
# receiving commits?", and for the whole period in which EVERY skill on the
# laptop was a dangling symlink into a garbage-collected /nix/store path, the
# honest answer was YES. `git log` matched origin/main, the tree was clean, and
# ~/.claude/skills/*/SKILL.md resolved to nothing. Git parity is not host parity,
# and a deadman that reports "clean" for that host has moved the failure, not
# removed it.
#
# Every fixture here is built in tmp_path — NOT read from the operator's real
# $HOME. A test that skipped itself when ~/.claude was absent would be green on
# exactly the machine that has the bug (`run-tests.sh` GUARD 2 forbids that by
# name), and a test that READ the real ~/.claude would pass or fail for reasons
# having nothing to do with this code.
# --------------------------------------------------------------------------- #

# The tail of the store path the laptop's links actually pointed into. Used as
# the fixture's dead-generation marker so the negative control is built to the
# real shape rather than an invented one.
DEAD_STORE = "-home-manager-files"


def _mkhome(root, *, healthy=0, dangling=0, store=None,
            settings_keys=None, enabled=None, installed=None,
            minified=False, extra_links=()):
    """Build a fixture $HOME reproducing the REAL deployment shape.

    `healthy`/`dangling` are counts of MANAGED symlinks (targets under `store`);
    the dangling ones point at a path that is never created, which is exactly the
    laptop's state — a live symlink into a store path that had been GC'd.
    """
    store = store or (root.parent / "fakestore")
    claude = root / ".claude"
    (claude / "skills" / "activity" / "reference").mkdir(parents=True)
    (root / ".config" / "opencode" / "skills").mkdir(parents=True)

    live = store / "gen-live" / ".claude" / "skills"
    live.mkdir(parents=True, exist_ok=True)
    for i in range(healthy):
        tgt = live / ("ok-%d.md" % i)
        tgt.write_text("deployed\n")
        (claude / "skills" / "activity" / ("ok-%d.md" % i)).symlink_to(tgt)
    for i in range(dangling):
        dead = (store / ("1gfc1d16rii1pknsc2mcg29ia5f25hrg" + DEAD_STORE)
                / ".claude" / "skills" / "activity" / ("SKILL-%d.md" % i))
        (claude / "skills" / "activity" / ("SKILL-%d.md" % i)).symlink_to(dead)
    for name, target in extra_links:
        p = claude / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.symlink_to(target)

    if settings_keys is not None:
        # 🔴 SCALAR values, on the SAME LINE as their key. An earlier version used
        # nested objects, which `json.dumps(indent=2)` puts on FOLLOWING lines —
        # so a mutant that widened the extractor to capture the value could not
        # put a secret anywhere the assertion would see, and the leak guard
        # passed while being structurally unable to fail. Measured: with nested
        # values that mutant was killed only by the unrelated AGREE test.
        body = {k: "SECRET-VALUE-%s" % k for k in settings_keys}
        if enabled is not None:
            body["enabledPlugins"] = {p: True for p in enabled}
        text = (json.dumps(body, separators=(",", ":")) if minified
                else json.dumps(body, indent=2))
        (claude / "settings.json").write_text(text + "\n")

    if installed is not None:
        (claude / "plugins").mkdir(parents=True, exist_ok=True)
        (claude / "plugins" / "installed_plugins.json").write_text(
            json.dumps({"version": 2,
                        "plugins": {p: [{"scope": "user"}] for p in installed}},
                       indent=2) + "\n")
    return store


def _parity(fleet, *args, store=None, **env):
    """Run the checker with the parity scan pointed at the fixture store."""
    e = {"DRIFT_MANAGED_PREFIX": str(store) + "/"} if store else {}
    e.update(env)
    return fleet.check(*args, **e)


def _examined(out):
    m = re.search(r"managed symlinks: examined=(\d+) dangling=(\d+)", out)
    assert m, "no examined/dangling PAIR in the output — the pair IS the claim:\n" + out
    return int(m.group(1)), int(m.group(2))


def _payload_literal(name):
    """The text of a `NAME='…'` payload, with the quote-dance resolved."""
    src = DRIFT.read_text()
    i = src.index("\n%s='" % name) + len(name) + 3
    j = src.index("\n'\n", i)
    return src[i:j + 1].replace("'\"'\"'", "'")


def _remote_running_the_real_payload(fleet, home, store):
    """A stub `ssh` that RUNS the payload it is handed, against a second fixture
    home. Faithful in the way that matters: the remote leg executes the SAME
    payload text the local leg does, so a bug in it cannot hide on one side."""
    write_exec(fleet.bin / "ssh", (
        "export HOME=%s\n"
        "export DRIFT_MANAGED_PREFIX=%s/\n"
        "exec bash -s\n" % (home, store)
    ))


# --- the negative control, in the real failure shape ------------------------ #
def test_dangling_managed_symlink_is_rc14(fleet):
    """🔴 NEGATIVE CONTROL, built to the shape that actually bit us: a live
    symlink whose target is a /nix/store/…-home-manager-files/… path that does
    not exist, while git is perfectly in sync."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=2, dangling=3)
    rc, out = _parity(fleet, "--no-remote", store=store)
    assert rc == 14, f"a host whose deployment resolves to nothing must be rc 14, got {rc}\n{out}"
    assert "3 of 5 managed symlink(s) point at a path that does not exist" in out, out
    assert DEAD_STORE in out, "the dead store path is not named, so nobody can diagnose it\n" + out
    assert "home-manager switch" in out, "no fix is offered\n" + out
    assert "✅ clean — on branch main" in out, (
        "the git check must be UNCHANGED and must still run — the parity scan is "
        "additional evidence, not a replacement\n" + out
    )


def test_the_examined_count_is_reported_beside_the_dangling_count(fleet):
    """🔴 POSITIVE CONTROL FOR THE SCANNER ITSELF. `dangling=0` from a walk that
    examined 0 links is indistinguishable from a healthy host — the exact vacuity
    this subsystem exists to refuse. So the scanner must be SHOWN to produce a
    non-zero examined count, and the pair must always be printed."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=7, dangling=0)
    rc, out = _parity(fleet, "--no-remote", store=store)
    examined, dangling = _examined(out)
    assert examined == 7, f"the scanner did not see the 7 links it was given: {examined}\n{out}"
    assert dangling == 0, out
    assert rc == 0, out


def test_a_scan_with_no_roots_says_so_instead_of_reporting_zero(fleet):
    """The other half of the same trap: with nothing to walk the answer is NOT
    EVALUATED, never `dangling=0`."""
    fleet.catch_up()
    rc, out = _parity(fleet, "--no-remote", DRIFT_PARITY_ROOTS="no/such/dir")
    assert "managed symlinks: NOT EVALUATED" in out, out
    assert "dangling=0" not in out, "a scan that walked nothing reported a clean count\n" + out


def test_a_symlink_outside_the_managed_prefix_is_not_drift(fleet):
    """~/.claude/debug/latest points at a SIBLING transcript and is routinely
    stale — Claude Code runtime state, not a deployment. Counting it would train
    the operator to ignore rc 14, which is worse than not having it."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, dangling=0,
                    extra_links=(("debug/latest", "./gone-2026-08-11.txt"),))
    rc, out = _parity(fleet, "--no-remote", store=store)
    assert _examined(out) == (1, 0), "an unmanaged dangling link was counted\n" + out
    assert rc == 0, out


def test_a_nested_git_checkout_is_neither_walked_nor_flagged(fleet):
    """~/.claude/skills/clickup/ is a standalone repo with a large pnpm
    node_modules. Flagging it would be a permanent false positive; walking it
    would make the deadman slow for nothing.

    The fixture puts a DANGLING managed link inside it, so this cannot pass by
    the directory merely being empty."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, dangling=0)
    clickup = fleet.home / ".claude" / "skills" / "clickup"
    (clickup / ".git").mkdir(parents=True)
    (clickup / "node_modules" / ".pnpm").mkdir(parents=True)
    (clickup / "SKILL.md").symlink_to(store / ("deadbeef" + DEAD_STORE) / "SKILL.md")
    (clickup / "node_modules" / "unified").symlink_to(".pnpm/unified@11.0.5")

    rc, out = _parity(fleet, "--no-remote", store=store)
    assert _examined(out) == (1, 0), (
        "the nested checkout was walked — a legitimately unmanaged repo is being "
        "reported as fleet drift\n" + out
    )
    assert rc == 0, out


def test_node_modules_is_pruned_even_without_a_git_dir(fleet):
    """The two prune rules are independent; a vendored node_modules with no .git
    beside it must still not be walked."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, dangling=0)
    nm = fleet.home / ".claude" / "skills" / "activity" / "node_modules"
    nm.mkdir(parents=True)
    (nm / "x.md").symlink_to(store / ("cafe" + DEAD_STORE) / "x.md")
    _, out = _parity(fleet, "--no-remote", store=store)
    assert _examined(out) == (1, 0), out


# --- settings.json key-set divergence (needs a fact set from EACH host) ----- #
def test_settings_values_never_reach_the_output(fleet):
    """🔴 THE CONFIDENTIALITY CLAIM, IN ITS OWN TEST.

    settings.json holds tokens, hook command lines and permission rules, and this
    output goes to a systemd journal. Only key NAMES may appear.

    It is a separate test on purpose. Folded in beside the divergence assertions
    it was UNREACHABLE: a mutant that widened the extractor to capture values
    also changed the key names, so an earlier assertion failed first and this one
    never ran — the guard was killed for the wrong reason and would have stayed
    green with itself deleted. Measured, then split.
    """
    fleet.catch_up()
    lstore = _mkhome(fleet.home, healthy=1,
                     settings_keys=["hooks", "permissions", "theme"],
                     enabled=["gopls-lsp@m"], installed=["gopls-lsp@m"])
    _, out = _parity(fleet, "--no-remote", store=lstore)
    # Positive control first — and deliberately ORTHOGONAL to the leak: it
    # asserts only that the extractor produced SOMETHING, never which names. An
    # earlier version pinned the exact key list, and a mutant that leaked values
    # changed that list too, so the positive control failed first and the leak
    # assertion never ran. A guard shadowed by its own control is not a guard.
    m = re.search(r"FACT settings-keys (.+)", out)
    assert m and m.group(1).strip() != "UNEVALUATED", (
        "the extractor produced no key names, so the no-leak assertion below "
        "would be vacuous\n" + out
    )
    assert "SECRET-VALUE" not in out, (
        "🔴 a settings.json VALUE reached the output — this goes to the journal\n" + out
    )


def _two_host_parity(fleet, local_keys, remote_keys, **env):
    """Run the checker across two fixture homes differing only in settings keys.

    Both sides carry the same plugin state, so the ONLY thing that can move the
    verdict is the top-level key set — otherwise a green here could come from a
    plugin agreement rather than from the key comparison under test.
    """
    fleet.catch_up()
    lstore = _mkhome(fleet.home, healthy=1, settings_keys=local_keys,
                     enabled=["gopls-lsp@m"], installed=["gopls-lsp@m"])
    rhome = fleet.root / "remote-home"
    rhome.mkdir()
    rstore = _mkhome(rhome, healthy=1, store=fleet.root / "remote-store",
                     settings_keys=remote_keys,
                     enabled=["gopls-lsp@m"], installed=["gopls-lsp@m"])
    _remote_running_the_real_payload(fleet, rhome, rstore)
    return _parity(fleet, store=lstore, REMOTE_SSH="stub@example.invalid", **env)


def test_settings_key_set_divergence_is_rc15(fleet):
    """🔴 NEGATIVE CONTROL for the key-set check: a key present on one host and
    absent on the other, watched red with this code's own message.

    🔴 The keys here are deliberately NOT the ones on the per-host allowlist
    (`theme`/`voice`/`effortLevel`). This test previously used exactly those, so
    scoping the comparison would have turned the subsystem's primary negative
    control green while looking like a test that still fired. `statusLine` and
    `hooks` are the right shape: a host quietly losing either is the failure this
    check exists for, and neither is anybody's preference.
    """
    rc, out = _two_host_parity(
        fleet,
        ["hooks", "permissions", "statusLine"],
        ["hooks", "env", "model"],
    )
    assert rc == 15, f"diverging key sets must be rc 15, got {rc}\n{out}"
    assert "settings.json top-level KEY SETS differ" in out, out
    assert "only on workbench: permissions statusLine" in out, out
    assert "only on laptop: env model" in out, out


# --- the per-host allowlist ------------------------------------------------- #
#
# 🔴 WHAT THIS SECTION IS DEFENDING. `~/.claude/settings.json` is per-host and
# unmanaged by design, so a handful of keys can NEVER agree across the fleet and
# the deadman was red on them from its first autonomous run. The fix is a scoped
# comparison — and the way a scoped comparison fails is by scoping away
# EVERYTHING, which leaves a green deadman wired to nothing. So every test below
# is paired: one asserting the allowlisted keys go quiet, one asserting that a
# key beside them still fires.
ALLOWLISTED = ("effortLevel", "theme", "voice")


def _perhost_reason(key):
    """Call the script's own `perhost_reason` for `key`, in isolation.

    Extracted and executed rather than re-implemented: a second copy of the
    enumeration in the test file would agree with itself forever while the script
    drifted, which is the shape of guard this repo keeps finding.
    """
    src = DRIFT.read_text()
    i = src.index("perhost_reason() {")
    j = src.index("\n}\n", i) + 3
    proc = subprocess.run(
        ["bash", "-c", src[i:j] + '\nperhost_reason "$1"\n', "_", key],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_the_measured_live_divergence_is_no_longer_drift(fleet):
    """🔴 THE RED THIS CHANGE EXISTS TO FIX, in the shape measured on 2026-08-11.

    Four keys differed on the live fleet: `theme` (workbench only), `voice` and
    `effortLevel` (laptop only) — all per-host preference — and `permissions`,
    which is a real gap and is NOT allowlisted. With `permissions` present on
    both hosts (the state `scripts/sync-claude-permissions.sh` produces on the
    laptop), the remaining three must produce rc 0.
    """
    rc, out = _two_host_parity(
        fleet,
        ["hooks", "permissions", "theme"],
        ["hooks", "permissions", "effortLevel", "voice"],
    )
    assert rc == 0, f"the per-host preference keys still fail the deadman: {rc}\n{out}"
    assert "AGREE apart from the per-host keys below" in out, out
    # 🔴 SILENCED IS NOT HIDDEN. Each exempted key is named with its reason, in
    # the same block as the verdict — a tolerated difference and a difference
    # nobody looked at must never print the same way.
    assert "IGNORED (allowlisted in drift-check.sh, not drift)" in out, out
    for key in ALLOWLISTED:
        assert key in out, f"exempted key {key} vanished from the report\n{out}"
    assert "per-host by design: terminal colour theme" in out, out
    assert "per-host by design: TTS voice" in out, out
    assert "per-host by design: reasoning-effort" in out, out


def test_the_measured_live_divergence_still_fires_on_permissions(fleet):
    """The OTHER half of the pair, in the same measured shape: before the laptop
    gets a permissions block, the check must still be red — and red naming ONLY
    `permissions`, not the three preference keys it now tolerates.

    This is what makes the rc 0 above a measurement rather than a silencing.
    """
    rc, out = _two_host_parity(
        fleet,
        ["hooks", "permissions", "theme"],
        ["hooks", "effortLevel", "voice"],
    )
    assert rc == 15, f"the real permissions gap was swallowed by the allowlist: {rc}\n{out}"
    assert "only on workbench: permissions" in out, out
    drift_lines = [ln for ln in out.splitlines()
                   if ln.startswith("[parity]   only on")
                   and "per-host by design" not in ln]
    assert drift_lines == ["[parity]   only on workbench: permissions"], (
        "the DRIFT verdict named a key the allowlist covers\n" + "\n".join(drift_lines)
    )


@pytest.mark.parametrize("unknown", ["autoCompactWindow", "zzSomeFutureKey"])
def test_an_unknown_key_beside_allowlisted_ones_still_fires(fleet, unknown):
    """🔴 FAILS CLOSED. The allowlist is an enumeration, so a key nobody has
    argued about — an upstream addition, a rename, a typo — is drift by default
    EVEN WHEN it arrives alongside keys that are legitimately exempt.

    Parameterised over a real Claude Code key and an invented one: a guard that
    only rejects made-up names would be spelled rather than structural.
    """
    rc, out = _two_host_parity(
        fleet,
        ["hooks", "theme", unknown],
        ["hooks", "voice", "effortLevel"],
    )
    assert rc == 15, f"an unknown key was silently exempted: {rc}\n{out}"
    assert "only on workbench: %s" % unknown in out, out
    assert "theme" not in out.split("IGNORED")[0].split("KEY SETS differ")[-1], (
        "an allowlisted key was reported as drift\n" + out
    )


def test_the_allowlist_is_not_a_wildcard():
    """🔴 THE MUTANT THAT DISARMS THE DEADMAN, pinned directly.

    `perhost_reason` returning a reason for everything makes every future
    divergence invisible while every behavioural test above still passes on its
    happy path. So assert the enumeration BOTH ways: each listed key has a
    reason, and keys that are not listed have none.
    """
    for key in ALLOWLISTED:
        why = _perhost_reason(key)
        assert why, f"allowlisted key {key} carries no reason"
        assert len(why) > 20, f"the reason for {key} is not a reason: {why!r}"
    for key in ("permissions", "hooks", "statusLine", "enabledPlugins", "env",
                "model", "alwaysThinkingEnabled", "", "*", "zzSomeFutureKey"):
        assert _perhost_reason(key) == "", (
            f"{key!r} is exempt from the parity check and nobody decided that"
        )


def test_the_allowlist_cannot_be_widened_from_the_environment(fleet):
    """🔴 Every other tunable in this file is an env override. This one must not
    be: a stray export in a unit file or a shell profile could otherwise widen it
    to everything, and the resulting green would be indistinguishable from a real
    pass. Both halves are asserted — no such variable is READ by the script, and
    setting the plausible names changes nothing.
    """
    src = "\n".join(ln for ln in DRIFT.read_text().splitlines()
                    if not ln.strip().startswith("#"))
    assert not re.search(r"DRIFT_(PERHOST|ALLOW|IGNORE|EXEMPT)", src), (
        "the per-host allowlist reads an environment variable — it must be "
        "reviewable source, not runtime input"
    )
    rc, out = _two_host_parity(
        fleet,
        ["hooks", "permissions"],
        ["hooks"],
        DRIFT_PERHOST_KEYS="permissions",
        DRIFT_ALLOW_KEYS="permissions",
        DRIFT_IGNORE_KEYS="permissions",
        DRIFT_EXEMPT_KEYS="permissions",
    )
    assert rc == 15, f"an env var widened the allowlist: {rc}\n{out}"
    assert "only on workbench: permissions" in out, out


def test_an_allowlisted_key_present_on_BOTH_hosts_is_not_reported(fleet):
    """The allowlist scopes the DIFFERENCE, not the key. A key both hosts have is
    not a difference at all, so it must not appear in the IGNORED block — that
    block is a record of decisions actually applied, and padding it with keys
    that never diverged would make it noise nobody reads."""
    keys = ["hooks", "theme", "voice"]
    rc, out = _two_host_parity(fleet, keys, keys)
    assert rc == 0, out
    assert "key sets AGREE (4 key names on each host)" in out, out
    assert "IGNORED" not in out, (
        "keys that agree were listed as allowlisted exemptions\n" + out
    )


def test_identical_key_sets_agree(fleet):
    """POSITIVE CONTROL for the comparator: it must be able to say AGREE, or the
    rc 15 above proves only that it always fires."""
    fleet.catch_up()
    keys = ["hooks", "permissions", "theme"]
    lstore = _mkhome(fleet.home, healthy=1, settings_keys=keys,
                     enabled=["gopls-lsp@m"], installed=["gopls-lsp@m"])
    rhome = fleet.root / "remote-home"
    rhome.mkdir()
    rstore = _mkhome(rhome, healthy=1, store=fleet.root / "remote-store",
                     settings_keys=keys,
                     enabled=["gopls-lsp@m"], installed=["gopls-lsp@m"])
    _remote_running_the_real_payload(fleet, rhome, rstore)
    rc, out = _parity(fleet, store=lstore, REMOTE_SSH="stub@example.invalid")
    # 4, not 3: `enabledPlugins` is itself a top-level key and must be counted
    # like any other — the comparator has no special case for it.
    assert "key sets AGREE (4 key names on each host)" in out, out
    assert "enabledPlugins AGREE" in out, out
    assert rc == 0, out


def test_enabled_plugin_divergence_is_rc15(fleet):
    """The exact shape the laptop was in before pyright-lsp was installed there:
    both hosts internally consistent, disagreeing with each other."""
    fleet.catch_up()
    lstore = _mkhome(fleet.home, healthy=1, settings_keys=["hooks"],
                     enabled=["gopls-lsp@m", "pyright-lsp@m"],
                     installed=["gopls-lsp@m", "pyright-lsp@m"])
    rhome = fleet.root / "remote-home"
    rhome.mkdir()
    rstore = _mkhome(rhome, healthy=1, store=fleet.root / "remote-store",
                     settings_keys=["hooks"], enabled=["gopls-lsp@m"],
                     installed=["gopls-lsp@m"])
    _remote_running_the_real_payload(fleet, rhome, rstore)
    rc, out = _parity(fleet, store=lstore, REMOTE_SSH="stub@example.invalid")
    assert rc == 15, f"{rc}\n{out}"
    assert "enabledPlugins differ" in out, out
    assert "enabled only on workbench: pyright-lsp@m" in out, out


def test_enabled_but_not_installed_is_rc15(fleet):
    """🔴 NEGATIVE CONTROL for the third case — a plugin switched ON in
    settings.json that is not in the plugin cache. It needs only ONE host, so it
    is decided per-host rather than in the cross-host block."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, settings_keys=["hooks"],
                    enabled=["gopls-lsp@m", "pyright-lsp@m"],
                    installed=["gopls-lsp@m"])
    rc, out = _parity(fleet, "--no-remote", store=store)
    assert rc == 15, f"{rc}\n{out}"
    assert "ENABLED in settings.json but NOT installed: pyright-lsp@m" in out, out


def test_a_minified_settings_json_is_unevaluated_not_agreed(fleet):
    """🔴 THE EXTRACTOR'S FORMAT DEPENDENCY, MADE TO FAIL LOUD.

    Keys are read as 2-space-indented lines. A minified file yields NOTHING — and
    an empty key set compared against another empty key set is a diff that finds
    no difference, i.e. it would print AGREE. That reassuring output would mean
    the check had stopped working, so the empty case must be UNEVALUATED."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, settings_keys=["hooks", "theme"],
                    minified=True)
    _, out = _parity(fleet, "--no-remote", store=store)
    assert "settings.json: NOT EVALUATED" in out, out
    assert "FACT settings-keys UNEVALUATED" in out, out
    assert "AGREE" not in out, "an unparseable settings.json reported agreement\n" + out


def test_a_missing_settings_json_is_unevaluated(fleet):
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1)
    _, out = _parity(fleet, "--no-remote", store=store)
    assert "settings.json: NOT EVALUATED" in out and "missing or unreadable" in out, out


# --- unreachable is still not drift, and still not a pass ------------------- #
def test_an_unreachable_remote_leaves_parity_uncompared_not_agreed(fleet):
    """🔴 An ssh timeout must not read as a clean parity result. `only_in` over an
    EMPTY remote set finds nothing missing — a diff that found nothing, not
    agreement. The two must never print the same way."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, settings_keys=["hooks", "theme"])
    fleet.stub_ssh(255)
    rc, out = _parity(fleet, store=store, REMOTE_SSH="stub@example.invalid")
    assert "[parity] NOT COMPARED" in out, out
    assert "AGREE" not in out, "an unreachable host produced an agreement verdict\n" + out
    assert "UNREACHABLE" in out, out
    # Unchanged policy: below the threshold this does NOT fail the unit.
    assert rc == 0, f"an unreachable laptop became drift, got {rc}\n{out}"


def test_a_no_remote_run_does_not_claim_parity_was_compared(fleet):
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=1, settings_keys=["hooks"])
    rc, out = _parity(fleet, "--no-remote", store=store)
    assert "[parity] NOT COMPARED" in out, out
    assert rc == 0, out


# --- interaction with the (unchanged) git verdict --------------------------- #
def test_the_parity_scan_still_runs_when_the_git_leg_exits_early(fleet):
    """🔴 THE SEAM. The git payload `exit`s on its first finding, so a naive
    concatenation would silently skip the parity scan on exactly the hosts that
    are already unhealthy. The subshell is what keeps both running."""
    fleet.add_local_commit()          # rc 8 territory
    store = _mkhome(fleet.home, healthy=2, dangling=1)
    rc, out = _parity(fleet, "--no-remote", store=store)
    assert "un-pushed commit(s)" in out, out
    assert "examined=3 dangling=1" in out, (
        "the parity scan did not run on a host the git check had already failed\n" + out
    )
    assert rc == 8, f"rc 8 must still outrank rc 14, got {rc}\n{out}"


def test_dangling_links_outrank_a_merely_behind_host(fleet):
    """Severity asserted rather than assumed: a broken deployment is worse than a
    host that just needs a ship, and the single number handed to systemd must be
    the worst thing found."""
    store = _mkhome(fleet.home, healthy=1, dangling=1)
    rc, out = _parity(fleet, "--no-remote", store=store)   # `work` starts BEHIND
    assert "is BEHIND origin/main" in out, out
    assert rc == 14, f"rc 14 must outrank rc 10, got {rc}\n{out}"


def test_parity_findings_never_rewrite_the_dangerous_rc8(fleet):
    fleet.add_local_commit()
    store = _mkhome(fleet.home, healthy=1, settings_keys=["hooks"],
                    enabled=["ghost@m"], installed=[])
    rc, out = _parity(fleet, "--no-remote", store=store)
    assert "NOT installed: ghost@m" in out, out
    assert rc == 8, f"a parity finding masked the un-pushed-commits verdict: {rc}\n{out}"


# --- structural pins -------------------------------------------------------- #
def test_the_managed_prefix_defaults_to_the_nix_store():
    """DRIFT_MANAGED_PREFIX exists so the suite can build a fixture tree. If its
    DEFAULT ever moves, every test above keeps passing against the fake store
    while production examines nothing — the vacuous zero, one level down."""
    assert 'mprefix="${DRIFT_MANAGED_PREFIX:-/nix/store/}"' in DRIFT.read_text()


def test_the_parity_payload_does_not_use_find():
    """🔴 The laptop resolves `find` to BUSYBOX, which does not implement
    `-xtype`: it prints usage to stderr and EXITS 0. `find -xtype l | wc -l`
    therefore yields a confident `0 dangling` on that host forever. Measured
    2026-08-11. The walk must stay on bash builtins + readlink."""
    assert not re.search(r"(?<![\w.-])find(?![\w.-])", _payload_literal("PARITY")), (
        "the parity payload calls `find`; busybox find makes its answer vacuous"
    )


@pytest.mark.parametrize("name", ["CHECK", "PARITY"])
def test_the_embedded_payloads_are_valid_bash(name):
    """🔴 `bash -n scripts/drift-check.sh` does NOT check these.

    They are single-quoted STRINGS to the outer parser, so a syntax error inside
    one is invisible to it and surfaces only when a host runs it. Worse, a stray
    apostrophe in a payload COMMENT silently ENDS the string early — the outer
    file still parses and the payload quietly loses everything after it. That
    happened while writing this section.

    RED/GREEN: the PARITY case is regression coverage (red before this change,
    because there was no PARITY payload). The CHECK case is an INVARIANT GUARD —
    CHECK was already valid and this pins that it stays so; it is not evidence
    that anything was broken."""
    proc = subprocess.run(["bash", "-n"], input=_payload_literal(name),
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"{name} payload is not valid bash:\n{proc.stderr}"


def test_the_payloads_are_not_silently_truncated():
    """The other half: valid bash that STOPS EARLY is still valid bash. Pin the
    last line of each payload so an apostrophe that ends the string prematurely
    fails here instead of shipping half a payload to both hosts."""
    assert _payload_literal("CHECK").rstrip().endswith("exit 0")
    assert _payload_literal("PARITY").rstrip().endswith('echo "[$label] PARITY-RC=$p_rc"')


def test_the_parity_scan_writes_nothing_into_the_home_it_walks(fleet):
    """PASSIVITY, behaviourally — the parity scan reads a host's whole config
    tree, so "it only reads" is worth measuring rather than asserting.

    RED/GREEN: this does NOT go red on pre-change code — with no scan there is
    nothing to write, so it passes vacuously there. It is not regression
    coverage for a bug that existed; it is a guard on the new scan. Proved
    REACHABLE by mutation: injecting `echo scanned > "$HOME/.claude/.scan-marker"`
    into the payload fails it with this test's own message."""
    fleet.catch_up()
    store = _mkhome(fleet.home, healthy=3, dangling=2, settings_keys=["hooks"],
                    enabled=["gopls-lsp@m"], installed=["gopls-lsp@m"])

    def snapshot():
        return sorted(
            (str(p.relative_to(fleet.home)), p.is_symlink(),
             None if p.is_symlink() or p.is_dir() else p.read_bytes())
            for p in fleet.home.rglob("*")
        )

    before = snapshot()
    _parity(fleet, "--no-remote", store=store)
    assert snapshot() == before, "the parity scan modified the tree it was reading"


# --------------------------------------------------------------------------- #
# 11. THE FUZZYCLAW PHASE-2 GATE (rc 16)
#
# 🔴 WHY IT IS A GATE AND NOT A NOTE. "Is it safe to delete the fuzzyclaw
# readers?" was answered by a human remembering to run a probe — the same shape
# as "nothing runs ship.sh on a schedule", which is the failure this whole file
# exists to convert into a measurement.
#
# 🔴 AND WHY THE ZERO IS THE DANGEROUS DIRECTION. The answer this gate hands over
# is a DELETION, and every way it can break produces the number that authorises
# one: no session-manager -> 0, a crashed scan -> 0, fuzzyclaw not read -> 0, a
# scan of the wrong host -> 0, a host with no windows -> 0. So the load-bearing
# tests here are not the READY case; they are the six ways a zero can be false.
# Each must be visibly distinct from a real zero AND must not set rc 16.
# --------------------------------------------------------------------------- #
def _phase2(fleet, report=None, raw=None, exit_code=0, **env):
    """A clean local-only run with the phase-2 gate pointed at a stubbed scan."""
    fleet.catch_up()
    if raw is not None:
        fleet.stub_session_manager(raw, exit_code=exit_code)
    elif report is not None:
        fleet.stub_session_manager(report, exit_code=exit_code)
    return fleet.check("--no-remote", **env)


def test_the_phase2_stub_is_the_instrument_and_it_can_move(fleet):
    """INSTRUMENT CHECK, before any verdict is read off this stub.

    A stub whose output the gate never actually reads would make every
    assertion below pass for the wrong reason — and the zero it would report is
    exactly the value that means READY. So: two different stub payloads, two
    different rendered numbers, from the same fixture.
    """
    rc_a, out_a = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=7))
    assert "fuzzyclaw-only ages: 7 of 47 row(s) EXAMINED" in out_a, out_a
    rc_b, out_b = _phase2(fleet, fleet.sm_report(rows=12, fuzzy=3))
    assert "fuzzyclaw-only ages: 3 of 12 row(s) EXAMINED" in out_b, out_b
    # BOTH numbers moved, so neither slot is static prose
    assert rc_a == 0 and rc_b == 0


def test_a_nonzero_fuzzyclaw_count_is_NOT_READY_and_does_not_fail_the_unit(fleet):
    """🔴 TODAY'S STATE, and it must stay green. 7 of 47 rows were measured on
    the workbench 2026-08-15. A gate that went red for the NORMAL condition
    would be permanently red, which trains the operator to click through the one
    alert that has to keep its meaning."""
    rc, out = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=7))
    assert rc == 0, out
    assert "fuzzyclaw-only ages: 7 of 47 row(s) EXAMINED" in out
    assert "NOT READY — 7 of 47 row(s) still take their age ONLY from" in out
    assert "READY — 0 of" not in out
    assert "rc=16" not in out


def test_a_REAL_zero_over_real_rows_is_READY_and_is_rc16(fleet):
    """The transition this gate exists to catch, pinned at BOTH the exit code
    and the rendered claim."""
    rc, out = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=0))
    assert rc == 16, out
    assert "fuzzyclaw-only ages: 0 of 47 row(s) EXAMINED" in out
    assert "🔴 READY — 0 of 47 rows depend on fuzzyclaw for an age." in out
    assert "Phase 2 is UNBLOCKED" in out
    # 🔴 rc 16 is NOT drift, and the verdict word is a claim like any other.
    assert "drift-check: ACTIONABLE (not drift) (rc=16)" in out
    assert "drift-check: DRIFT (rc=16)" not in out
    assert "rc16=NOT drift: the fuzzyclaw phase-2 gate OPENED" in out


def test_a_zero_over_ZERO_ROWS_is_not_ready_and_says_it_walked_nothing(fleet):
    """🔴 THE SILENT ZERO, in the one form this file already has a name for:
    "a scan that examined nothing is not a clean scan; it is no scan." A host
    with no tmux windows reports `0 of 0`, which is byte-identical to a real
    zero if only the numerator is printed."""
    rc, out = _phase2(fleet, fleet.sm_report(rows=0, fuzzy=0))
    assert rc == 0, out
    # the pair is the claim — the denominator is what distinguishes this
    assert "fuzzyclaw-only ages: 0 of 0 row(s) EXAMINED" in out
    assert "COULD NOT MEASURE — 0 rows examined. A zero over zero rows is a scan" in out
    assert "that walked nothing, not a measurement. NOT 'phase 2 is ready'." in out
    assert "READY — 0 of" not in out
    assert "UNBLOCKED" not in out


def test_a_missing_session_manager_is_a_could_not_measure_not_a_zero(fleet):
    """The default in this suite: no stub installed, so the path does not exist.
    A tool that is not there cannot have measured 0."""
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert "COULD NOT MEASURE — no executable session-manager at" in out
    assert "This is NOT a zero and NOT 'phase 2 is ready'." in out
    assert "EXAMINED" not in out


def test_a_CRASHED_scan_is_a_could_not_measure_not_a_zero(fleet):
    """A non-zero exit with no JSON on stdout. The reader sees an empty stream,
    which `json.load` rejects — and the reason token names it rather than
    letting the run fall through to the numbers."""
    rc, out = _phase2(fleet, raw="", exit_code=2)
    assert rc == 0, out
    assert "COULD NOT MEASURE — the scan produced no usable counts" in out
    assert "reason: no-json:JSONDecodeError" in out
    assert "UNBLOCKED" not in out


def test_TRUNCATED_json_is_a_could_not_measure_not_a_zero(fleet):
    """Half a payload parses as nothing, and a stream cut mid-object is what a
    `timeout` kill actually looks like."""
    rc, out = _phase2(fleet, raw='{"summary": {"total_sessions": 47,')
    assert rc == 0, out
    assert "reason: no-json:" in out
    assert "UNBLOCKED" not in out


def test_a_scan_of_the_WRONG_HOST_is_a_could_not_measure_not_a_zero(fleet):
    """🔴 THE ONE THAT WOULD HAVE BEEN INVISIBLE. `session-manager` decides which
    host is local from ACTIVITY_HOST / the collector env file; drift-check
    decides from lib/host-role.sh and the machine's IPs. If they disagree,
    `--host <role>` names the REMOTE machine and the scan ssh's there — and a
    remote row can never carry a fuzzyclaw age, so the count comes back 0 and
    the gate authorises a deletion off the wrong machine."""
    rc, out = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=0, host="laptop"))
    assert rc == 0, out
    assert "COULD NOT MEASURE — reason: host-mismatch:laptop" in out
    assert "UNBLOCKED" not in out
    # ...and the counts are still shown, so the finding is legible
    assert "raw: 0 of 47 row(s)" in out


def test_fuzzyclaw_NOT_READ_is_a_could_not_measure_not_a_zero(fleet):
    """fuzzyclaw is opt-in. With the index unread, NO row can have a fuzzyclaw
    age — so 0 is guaranteed by the reader being wired to nothing, which is the
    same output as the answer that authorises deleting it."""
    rc, out = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=0,
                                             status="skipped"))
    assert rc == 0, out
    assert "COULD NOT MEASURE — reason: fuzzyclaw-skipped" in out
    assert "UNBLOCKED" not in out


def test_ZERO_TASK_FILES_is_a_could_not_measure_not_a_zero(fleet):
    """🔴 MEASURED, NOT IMAGINED — this suite produced the false READY itself.
    An empty (or absent) `~/.tmux/tasks` reads successfully, so `status` is
    "ok" with `files_seen: 0`, and the first version of this gate announced
    "READY — 0 of 48 rows" off a fixture HOME while the real count on that
    machine was 7. `status == "ok"` is necessary and NOT sufficient."""
    rc, out = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=0, files_seen=0))
    assert rc == 0, out
    assert "COULD NOT MEASURE — reason: fuzzyclaw-no-task-files" in out
    assert "UNBLOCKED" not in out
    # ...and with task files present the SAME payload IS ready — the positive
    # control that pins this guard to `files_seen` and nothing else.
    rc2, out2 = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=0, files_seen=1))
    assert rc2 == 16, out2


def test_a_no_local_run_does_not_report_a_phase2_zero(fleet):
    """The gate is LOCAL-ONLY (fuzzyclaw task files are local state), so a
    --no-local run has not measured it. That must not read as a pass, for the
    same reason `[parity] NOT COMPARED` exists one block up."""
    fleet.catch_up()
    fleet.stub_ssh(0, "[laptop] clean")
    fleet.stub_session_manager(fleet.sm_report(rows=47, fuzzy=0))
    rc, out = fleet.check("--no-local")
    assert "NOT EVALUATED — --no-local, and this gate is LOCAL-ONLY" in out
    assert "Not a zero, and not a pass." in out
    assert "UNBLOCKED" not in out
    assert rc != 16


def test_rc16_never_outranks_a_real_drift(fleet):
    """🔴 SEVERITY. rc 16 says an optional cleanup became possible; rc 8 says
    work exists on exactly one machine. A run that is both must report 8, or the
    codes that need a rescue procedure hide behind a housekeeping note."""
    fleet.catch_up()
    fleet.add_local_commit()
    fleet.stub_session_manager(fleet.sm_report(rows=47, fuzzy=0))
    rc, out = fleet.check("--no-remote")
    assert rc == 8, out
    # the phase-2 finding is still PRINTED — it just does not win
    assert "Phase 2 is UNBLOCKED" in out
    assert "drift-check: DRIFT (rc=8)" in out
    # ...and the mirror image: alone, the same phase-2 state IS the verdict
    fleet.git(fleet.work, "reset", "--soft", "HEAD~1")
    rc2, _ = fleet.check("--no-remote")
    assert rc2 == 16


def test_the_phase2_gate_does_not_fail_the_run_when_it_cannot_measure(fleet):
    """"Cheap and non-fatal": a broken phase-2 gate must never change another
    leg's verdict. A behind-only host is rc 10 with the gate answering, silent,
    and crashed alike.

    RED/GREEN: this is GREEN at the base sha and is NOT regression coverage —
    with no gate there is nothing to interfere, so it passes vacuously there.
    It is a non-interference guard on the new block. Proved REACHABLE by
    mutation: changing `note_rc 16` to `note_rc 4` in the could-not-measure
    branch fails it with rc 4 != 10."""
    fleet.stub_session_manager(fleet.sm_report(rows=47, fuzzy=7))
    rc_ok, _ = fleet.check("--no-remote")          # `work` is 1 behind origin
    fleet.stub_session_manager("not json at all")
    rc_broken, _ = fleet.check("--no-remote")
    fleet.stub_session_manager("", exit_code=127)
    rc_gone, _ = fleet.check("--no-remote")
    assert rc_ok == rc_broken == rc_gone == 10


def test_the_phase2_gate_passes_the_flags_that_make_the_measurement_valid(fleet):
    """🔴 THE FLAGS ARE LOAD-BEARING, and two of the four fail SILENTLY.

    Without `--fuzzyclaw` the index is never read and the count is a guaranteed
    0; without `--host <role>` the scan can ssh to the other machine; `--no-ch`
    and `--no-capture` keep a 6-hourly timer from opening a ClickHouse
    connection and capturing every pane. Asserted from the argv the stub
    actually received, not from the source text.
    """
    fleet.catch_up()
    argv_log = fleet.root / "sm-argv.txt"
    write_exec(
        fleet.bin / "session-manager",
        'printf "%%s\\n" "$*" >> "$SM_ARGV_LOG"\n'
        "cat <<'SM_JSON_EOF'\n%s\nSM_JSON_EOF\n" % json.dumps(fleet.sm_report()),
    )
    fleet.check("--no-remote", SM_ARGV_LOG=str(argv_log))
    argv = argv_log.read_text().strip()
    assert argv.split() == ["scan", "--json", "--no-ch", "--no-capture",
                            "--fuzzyclaw", "--host", "workbench"], argv


def test_the_phase2_reader_is_read_only_and_imports_nothing_else():
    """The passivity scanner walks drift-check.sh and lib/host-role.sh only, so
    a NEW file under lib/ is unscanned by it. This is that file's guard: it may
    import `json` and `sys`, and nothing else — no `os`, no `subprocess`, no
    `open`, no `pathlib`."""
    import ast

    src = (REPO_ROOT / "scripts" / "lib" / "drift_phase2.py").read_text()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported == {"json", "sys"}, imported
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "open" not in called, "the phase-2 reader opened a file"
    assert "exec" not in called and "eval" not in called


@pytest.mark.parametrize("cmd,attr", sorted(CHILD_PATH_REQUIREMENTS.items()))
def test_the_phase2_child_binaries_are_on_the_unit_path(cmd, attr):
    """🔴 THE SEAM NEITHER EXISTING GUARD CAN SEE. `session-manager` is exec'd
    by drift-check but resolves ITS OWN binaries from the same unit PATH, and
    neither `python3`-as-a-shebang nor `tmux` appears as a command word in
    drift-check.sh — so the reverse tokenizer cannot find them and the forward
    table would reject them. Nothing crashes when they are missing: the gate
    just reports COULD NOT MEASURE on every timer run, forever."""
    assert attr in _drift_service_block(), (
        "the drift-check unit's PATH is missing %s — the phase-2 gate execs "
        "session-manager, which needs %r and would silently never measure"
        % (attr, cmd)
    )


def test_the_phase2_child_requirements_are_what_session_manager_actually_runs():
    """The table above is only accounting if it matches the child. `tmux` is
    session-manager's ONLY subprocess on a local scan, and `python3` is its
    shebang — both read off session-manager itself, so a child that grows a new
    dependency makes this fail rather than silently unmeasurable.

    RED/GREEN: GREEN at the base sha — it reads facts about session-manager
    that were already true there. It is an INVARIANT GUARD on the ledger, not
    regression coverage: it fires when the child's dependencies change and the
    table does not.

    🔴 THE SHEBANG IS MATCHED BY ITS TAIL, NOT PINNED WHOLE, and both halves of
    that are forced. `patchShebangs` rewrites it to an absolute store path
    inside the nix sandbox — the tier that gates merges — so a whole-string pin
    is red exactly where it must be green; and the repo-wide scan in
    `test_runtime_shebangs.py` forbids this file from containing the literal
    interpreter path at all. What both tiers agree on, and what this table is
    actually about, is that the interpreter IS python3.
    """
    sm_src = (REPO_ROOT / "scripts" / "session-manager").read_text()
    shebang = sm_src.splitlines()[0]
    # 🔴 ASSEMBLED FROM CHARACTER CODES, the same idiom and the same reason as
    # `testlib/shebang_scan.py`'s own needles: a quote followed by the two
    # shebang characters is shape 1 of the hazard that scan looks for, so
    # writing it as a literal makes this read-only assertion its own offender.
    assert shebang.startswith(chr(35) + chr(33)), shebang
    assert shebang.rstrip().endswith("python3"), shebang
    assert 'TMUX_PANES_ARGV = ("tmux"' in sm_src
    assert set(CHILD_PATH_REQUIREMENTS) == {"python3", "tmux"}


# --------------------------------------------------------------------------- #
# 12. THE PHASE-2 REASON-TOKEN LEDGER, THE THIRD STRUCTURAL ZERO, AND THE
#     ALERTING POLICY FOR rc 16
#
# 🔴 WHY THIS SECTION EXISTS AND SECTION 11 WAS NOT ENOUGH. Section 11 tests
# every reason token the reader HAS. It structurally cannot see a field the
# reader READS and gives NO token to — and that is precisely what shipped:
# `summary.age_sources` was read as `(summ.get("age_sources") or {}).get(
# "fuzzyclaw", 0)`, so a report without it printed `ok 47 0` and the run
# declared `🔴 READY — 0 of 47 rows`, byte-identical to a legitimate one.
# Section 11's own fixture (`Fleet.sm_report`) ALWAYS emits `age_sources`, so no
# test could reach the shape, and the mutant `.get("fuzzyclaw", 0)` ->
# `.get("fuzzyclaw", 1)` SURVIVED a fully green run of both changed suites.
#
# 🔴 AND IT WAS REACHABLE ON THE HOST SHAPE THIS DEADMAN IS FOR. `age_sources`
# landed 2026-08-13 and `DRIFT_SESSION_MANAGER` defaults to the CHECKOUT's own
# `scripts/session-manager` — so a host a few days behind ran a scan that never
# emitted the field and got READY. The staleness detector, disabled by staleness.
#
# So the guard here is not another token test: it is a SEAM guard pinning the
# RELATIONSHIP between the tokens emitted and the fields read, failing when
# either set GROWS or SHRINKS, plus a behavioural half proving every declared
# token is REACHABLE — a structural check alone type-checks past a dead branch.
# --------------------------------------------------------------------------- #
PHASE2_PY = REPO_ROOT / "scripts" / "lib" / "drift_phase2.py"

# Every reason token `lib/drift_phase2.py` may emit. A token built with a `%`
# format is written here as its literal PREFIX + `*` — the variable tail is a
# JSON key / host name / exception class and is not part of the contract.
PHASE2_TOKENS = {
    "ok",
    "no-json:*",                 # json.load raised — crash, truncation, empty stream
    "no-json:not-an-object",     # valid JSON that is not a report
    "no-counts",                 # total_sessions, or an age_sources value, is not an int
    "no-age-sources",            # THE ONE THAT SHIPPED MISSING
    "unknown-age-writer:*",      # the age_source VOCABULARY changed under us
    "age-sources-incoherent:*",  # the histogram does not account for every row
    "host-mismatch:*",           # the scan answered about the other machine
    "fuzzyclaw-*",               # fuzzyclaw.status was not "ok"
    "fuzzyclaw-no-task-files",   # status ok over an empty ~/.tmux/tasks
}

# 🔴 THE SEAM ITSELF: every field of the report the reader consults, mapped to
# the token that fires when it is absent or unusable. This is the ledger that
# makes "a newly-consulted field with no reason token" a RED TEST rather than a
# silent zero — add a `.get("something_new")` to the reader and the extracted
# field set below stops matching this dict, and the only way to green it is to
# write down which token covers that field's absence.
#
# `fuzzyclaw` appears once as a KEY OF THE HISTOGRAM and once as a TOP-LEVEL
# BLOCK. Keyed by name, so the entry names the histogram reader's token; the
# top-level block's absence makes `fz.get("status")` None -> `fuzzyclaw-*`,
# which `status` already accounts for.
PHASE2_FIELD_TOKENS = {
    "summary":        "no-counts",
    "total_sessions": "no-counts",
    "age_sources":    "no-age-sources",
    "fuzzyclaw":      "unknown-age-writer:*",
    "local_host":     "host-mismatch:*",
    "status":         "fuzzyclaw-*",
    "files_seen":     "fuzzyclaw-no-task-files",
}


def _phase2_ast():
    import ast
    return ast.parse(PHASE2_PY.read_text())


def _emitted_tokens() -> set:
    """Every first argument to `emit(...)`, normalised at the first `%`."""
    import ast
    out = set()
    for node in ast.walk(_phase2_ast()):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "emit"
                and node.args):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.add(arg.value)
        elif (isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Mod)
              and isinstance(arg.left, ast.Constant)
              and isinstance(arg.left.value, str)):
            out.add(arg.left.value.split("%")[0] + "*")
        else:  # pragma: no cover - a shape the ledger cannot account for
            raise AssertionError(
                "emit() called with an expression this guard cannot read "
                "statically: %r. The token set is the contract — keep it a "
                "literal or a `<literal>%%s` format." % ast.dump(arg))
    return out


def _fields_read() -> set:
    """Every string literal used as the first argument of a `.get(...)`."""
    import ast
    return {
        n.args[0].value
        for n in ast.walk(_phase2_ast())
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get" and n.args
        and isinstance(n.args[0], ast.Constant)
        and isinstance(n.args[0].value, str)
    }


def _read_phase2(payload, want="workbench"):
    """Drive the REAL reader over stdin and return (token, whole line)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    proc = subprocess.run([sys.executable, str(PHASE2_PY), want],
                          input=text, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    line = proc.stdout.strip()
    assert len(line.split()) == 3, f"output contract broken: {line!r}"
    return line.split()[0], line


def _normalise(token) -> str:
    if token in PHASE2_TOKENS:
        return token
    for known in PHASE2_TOKENS:
        if known.endswith("*") and token.startswith(known[:-1]):
            return known
    return token


def _report(rows=47, sources=None, host="workbench", status="ok", files_seen=3,
            omit_sources=False):
    """A phase-2-shaped report, built from the SAME `age_histogram` the fleet
    fixture uses, so the coherence guard is satisfied unless a test breaks it
    on purpose. `rows` may be a non-int here (that IS one of the cases)."""
    if sources is None:
        sources = age_histogram(rows, 7) if isinstance(rows, int) else {}
    summary = {"total_sessions": rows}
    if not omit_sources:
        summary["age_sources"] = sources
    return {"local_host": host,
            "fuzzyclaw": {"status": status, "files_seen": files_seen},
            "summary": summary}


# -- the ledger seam -------------------------------------------------------- #
def test_the_phase2_reason_token_ledger_is_pinned_to_the_fields_read():
    """🔴 THE STRUCTURAL FIX FOR THE WHOLE CLASS, not for the one instance.

    Two sets, both extracted from the reader's own source, both pinned two-way:

      * the tokens it EMITS must be exactly `PHASE2_TOKENS`;
      * the report fields it READS must be exactly the keys of
        `PHASE2_FIELD_TOKENS`, whose values name the token that fires when the
        field is missing or unusable.

    Consult a new field and the second assertion fails; the only way to green it
    is to write down which token covers its absence — which is the step that did
    not happen for `age_sources`. Delete a token and the first fails, so a guard
    cannot be quietly removed either.

    RED/GREEN: RED at 494d14d, which emits none of `no-age-sources`,
    `unknown-age-writer:*`, `age-sources-incoherent:*`. Regression coverage for
    the MECHANISM, not only for the one instance.
    """
    assert _emitted_tokens() == PHASE2_TOKENS, (
        "the reader's reason-token set drifted from the ledger:\n"
        "  only in source: %r\n  only in ledger: %r"
        % (sorted(_emitted_tokens() - PHASE2_TOKENS),
           sorted(PHASE2_TOKENS - _emitted_tokens())))
    assert _fields_read() == set(PHASE2_FIELD_TOKENS), (
        "the reader consults a report field with no entry in the reason-token "
        "ledger — that is the shape that shipped `age_sources` as a silent "
        "zero.\n  read but unaccounted: %r\n  accounted but unread: %r"
        % (sorted(_fields_read() - set(PHASE2_FIELD_TOKENS)),
           sorted(set(PHASE2_FIELD_TOKENS) - _fields_read())))
    unknown = {f: t for f, t in PHASE2_FIELD_TOKENS.items()
               if t not in PHASE2_TOKENS}
    assert not unknown, f"ledger names tokens the reader cannot emit: {unknown}"


def test_every_declared_phase2_token_is_actually_reachable():
    """🔴 THE BEHAVIOURAL HALF. A structural ledger type-checks past a token
    whose branch can never run — and an unreachable guard is exactly what makes
    a mutation sweep report SURVIVED for a right-looking reason. So every token
    in the ledger gets a payload that MUST produce it, driven through the real
    reader, and the produced set must equal the declared set.
    """
    cases = {
        "ok":                       _report(),
        "no-json:*":                "not json at all",
        "no-json:not-an-object":    "[1, 2, 3]",
        "no-counts":                _report(rows="47"),
        "no-age-sources":           _report(omit_sources=True),
        "unknown-age-writer:*":     _report(sources={"fz": 7, "ledger": 40}),
        "age-sources-incoherent:*": _report(sources={"ledger": 40}),
        "host-mismatch:*":          _report(host="laptop"),
        "fuzzyclaw-*":              _report(status="skipped"),
        "fuzzyclaw-no-task-files":  _report(files_seen=0),
    }
    assert set(cases) == PHASE2_TOKENS, (
        "a declared token has no reachability case: %r"
        % sorted(PHASE2_TOKENS ^ set(cases)))
    produced = {}
    for want, payload in cases.items():
        token, line = _read_phase2(payload)
        produced[want] = _normalise(token)
        assert produced[want] == want, (
            f"payload for {want!r} produced {token!r} ({line!r})")
    assert set(produced.values()) == PHASE2_TOKENS


# -- the third structural zero ---------------------------------------------- #
def test_a_report_without_age_sources_is_a_could_not_measure_not_a_zero(fleet):
    """🔴 THE DEFECT, END TO END THROUGH THE SHELL. A session-manager older than
    `age_sources` (it landed 2026-08-13) emits a report without it, and
    `DRIFT_SESSION_MANAGER` defaults to the checkout's own copy — so this is the
    STALE HOST, the one condition drift-check exists to detect.

    Measured at 494d14d with this exact stub: `🔴 READY — 0 of 47 rows depend on
    fuzzyclaw`, rc 16, indistinguishable from a real all-clear.
    """
    rc, out = _phase2(fleet, {"local_host": "workbench",
                              "fuzzyclaw": {"status": "ok", "files_seen": 3},
                              "summary": {"total_sessions": 47}})
    assert rc == 0, out
    # Pinned whole: the reason is NAMED, and the line says COULD NOT MEASURE
    # rather than reporting a count. The fuzzyclaw count is genuinely unknown
    # here (unlike host-mismatch, where the numbers are real but about the wrong
    # machine), so this is the no-usable-counts phrasing, not the raw one.
    assert ("[phase2] COULD NOT MEASURE — the scan produced no usable counts "
            "(reason: no-age-sources)." in out), out
    assert "[phase2]   This is NOT a zero and NOT 'phase 2 is ready'." in out, out
    assert "READY" not in out, out
    assert "UNBLOCKED" not in out, out
    assert "EXAMINED" not in out, out


@pytest.mark.parametrize("sources,reason", [
    # the whole histogram renamed away — what this PR did to a sibling key
    (None, "no-age-sources"),
    # not a dict at all (it used to raise AttributeError)
    ("age_sources", "no-age-sources"),
    # the age_source VALUE renamed: the count would silently read 0
    ({"fz": 7, "ledger": 27, "none": 13}, "unknown-age-writer:fz"),
    # a histogram that does not account for every row is not a measurement
    ({"ledger": 27, "none": 13}, "age-sources-incoherent:40"),
    # a non-int count must never print as a count
    ({"fuzzyclaw": "7", "ledger": 27, "none": 13}, "no-counts"),
])
def test_a_broken_age_histogram_is_a_could_not_measure_not_a_zero(
        fleet, sources, reason):
    """Each way `summary.age_sources` can arrive unusable, through the shell.
    All five produce the deletion-authorising number at 494d14d."""
    report = fleet.sm_report(rows=47, fuzzy=0)
    if sources is None:
        del report["summary"]["age_sources"]
    else:
        report["summary"]["age_sources"] = sources
    rc, out = _phase2(fleet, report)
    assert rc == 0, out
    assert "[phase2] COULD NOT MEASURE" in out, out
    assert f"(reason: {reason})." in out, out
    assert "READY" not in out, out
    assert "EXAMINED" not in out, out


def test_an_absent_fuzzyclaw_KEY_in_a_sound_histogram_IS_a_real_zero(fleet):
    """🔴 THE DECISION, WRITTEN DOWN. `summary.age_sources` is
    `_count_by(r.get("age_source") or "none" for r in rows)` — a histogram that
    creates a key only for a value it OBSERVED — so zero fuzzyclaw-sourced rows
    emits NO `fuzzyclaw` key rather than `fuzzyclaw: 0`. Refusing to measure
    that would make the gate structurally unable to ever say READY, i.e. a gate
    that can never open.

    It is a real zero only because two guards hold at the same time: the writer
    vocabulary is recognised (so the key is not merely RENAMED) and the
    histogram accounts for every row (so it is not merely TRUNCATED). That
    conjunction is the measurement; neither guard alone is.
    """
    rc, out = _phase2(fleet, _report(rows=47,
                                     sources={"ledger": 34, "none": 13}))
    assert rc == 16, out
    assert "fuzzyclaw-only ages: 0 of 47 row(s) EXAMINED" in out, out
    assert "🔴 READY — 0 of 47 rows depend on fuzzyclaw for an age." in out, out


@pytest.mark.parametrize("payload,reason", [
    ({"total_sessions": True, "age_sources": {"ledger": 1}}, "no-counts"),
    ({"total_sessions": 47,
      "age_sources": {"fuzzyclaw": True, "ledger": 46}}, "no-counts"),
])
def test_a_bool_never_prints_as_a_phase2_count(payload, reason):
    """🔴 READ AT THE READER, BECAUSE THE SHELL CANNOT SEE THIS ONE. `bool` is
    an `int` in Python, so without the explicit bool checks `total_sessions:
    true` reaches the output as the word `True` — which drift-check.sh's numeric
    filter then rejects as unparseable, i.e. the right verdict via a completely
    different mechanism. Through the shell the guard's removal mutant is
    UNOBSERVABLE (measured: it survived a sweep). Read here instead, where the
    two mechanisms ARE distinguishable, so the guard has a test that can go red
    when it is deleted.
    """
    token, line = _read_phase2({"local_host": "workbench",
                                "fuzzyclaw": {"status": "ok", "files_seen": 3},
                                "summary": payload})
    assert token == reason, line


def test_a_reason_token_never_contains_a_space():
    """🔴 THE OUTPUT CONTRACT IS POSITIONAL. drift-check.sh reads the token as
    `${p2_out%% *}` and the counts as the fields after it, so a space inside an
    interpolated JSON key or host name would truncate the reason AND shift both
    counts, printing a prefix of the real reason over numbers belonging to the
    wrong fields."""
    token, line = _read_phase2(_report(sources={"a b c": 47}))
    assert token == "unknown-age-writer:a_b_c", line
    assert len(line.split()) == 3, line
    token2, line2 = _read_phase2(_report(host="two words"))
    assert token2 == "host-mismatch:two_words", line2
    assert len(line2.split()) == 3, line2


# -- the alerting policy for rc 16 ------------------------------------------ #
def test_the_unit_does_not_fail_on_the_phase2_actionable_code():
    """🔴 rc 16 MUST NOT FAIL THE UNIT. `Type = "oneshot"` fails on any non-zero
    exit, `OnFailure` fires `notify-failure@`, and that toast is the ONE class
    deliberately wired to DEFEAT do-not-disturb (`zz_notify_failure_bypass`,
    `override_pause_level = 100`) — a bypass justified in home.nix by a MEASURED
    rate of ~1 firing in 9 days.

    rc 16 stays set until somebody deletes the fuzzyclaw readers, and the timer
    runs every 6h, so without `SuccessExitStatus` the gate OPENING converts that
    into 4 DND-defeating toasts a day, forever, on runs where nothing is wrong.
    That is the permanently-red gate this same subsystem already refuses for an
    unreachable remote.

    Asserted TOGETHER WITH the OnFailure line, deliberately: deleting the alert
    would also make "does not toast on 16" true, and that is the wrong fix.
    """
    block = _drift_service_block()
    assert "SuccessExitStatus = 16;" in block, (
        "drift-check.service has no SuccessExitStatus, so rc 16 — the phase-2 "
        "gate reporting that a CLEANUP is possible — puts the unit in `failed` "
        "and fires the DND-defeating failure toast 4x a day forever:\n" + block)
    assert "notify-failure@%n.service" in block, (
        "the failure alert was removed rather than the exit code excused")


def test_only_16_is_excused_from_failing_the_unit():
    """The excuse must name exactly one code. `SuccessExitStatus` takes a LIST,
    so a second entry would silently mute a real drift verdict — the deadman's
    whole point. An INVARIANT GUARD, not regression coverage: at 494d14d there
    is no SuccessExitStatus at all, so it fails there for the OTHER reason (the
    assertion above owns that). It fires if somebody later widens the excuse."""
    block = _drift_service_block()
    m = re.search(r"SuccessExitStatus\s*=\s*([^;]+);", block)
    assert m, "no SuccessExitStatus in the drift-check service block"
    assert re.findall(r"\d+", m.group(1)) == ["16"], (
        "only rc 16 (ACTIONABLE, not drift) may be excused from failing the "
        f"unit; found {m.group(1).strip()!r}")


def test_the_phase2_ready_run_still_prints_the_no_drift_line(fleet):
    """🔴 BOTH CLAIMS, BECAUSE THEY ARE INDEPENDENT. rc 16 is the one owned code
    that says nothing about host health, so routing it through the DRIFT branch
    alone withheld the finding the run actually made — "no drift on the host(s)
    CHECKED" — and printed only the cleanup notice. An operator then cannot tell
    an rc 16 over a clean host from an rc 16 over a host nobody vouched for.
    """
    rc, out = _phase2(fleet, fleet.sm_report(rows=47, fuzzy=0))
    assert rc == 16, out
    assert "no drift on the host(s) CHECKED" in out, out
    assert "ACTIONABLE (not drift) (rc=16)" in out, out
    assert "🔴 READY — 0 of 47 rows depend on fuzzyclaw for an age." in out, out


def test_a_plain_clean_run_does_not_gain_the_verdict_block(fleet):
    """The other half of the pair above: widening the affirmative branch to
    `rc = 0 || rc = 16` must not give an ordinary rc-0 run a verdict line. No
    session-manager is stubbed, so the gate takes its COULD NOT MEASURE branch
    and the run is a plain rc 0."""
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert "no drift on the host(s) CHECKED" in out, out
    assert "ACTIONABLE" not in out, out
    assert "(rc=" not in out, out


def test_a_real_drift_verdict_does_not_gain_the_no_drift_line(fleet):
    """The mirror image, and the dangerous one to get wrong: rc 8 must print
    DRIFT and must NOT print the affirmative line."""
    fleet.catch_up()
    fleet.add_local_commit("commit the workbench never pushed")
    rc, out = fleet.check("--no-remote")
    assert rc == 8, out
    assert "no drift on the host(s) CHECKED" not in out, out
    assert "DRIFT (rc=8)" in out, out


# -- the shell's own robustness to a reader that breaks the contract -------- #
def _drift_copy(fleet, reader_src):
    """A COPY of drift-check.sh whose `lib/drift_phase2.py` a test controls.

    🔴 WHY A COPY. `_drift_phase2_py` is derived from the script's own resolved
    dirname and is deliberately not env-overridable (unlike
    `DRIFT_SESSION_MANAGER`), so the numeric filters that defend against a
    malformed reader line are otherwise unreachable from any test — the real
    reader cannot produce one. Measured: removing either `case` filter SURVIVED
    a full mutation sweep before this existed.

    🔴 AND IT IS NOT A HYPOTHETICAL. `scripts/` is read from the checkout while
    the skill docs beside it deploy as a store copy, and any future consumer of
    this line is a second implementation of the contract. A shell that trusts
    its reader is a shell whose COULD NOT MEASURE depends on someone else's bug.
    """
    d = fleet.root / "driftcopy"
    (d / "lib").mkdir(parents=True, exist_ok=True)
    shutil.copy(str(DRIFT), str(d / "drift-check.sh"))
    shutil.copy(str(HOST_ROLE_LIB), str(d / "lib" / "host-role.sh"))
    if reader_src is not None:
        (d / "lib" / "drift_phase2.py").write_text(reader_src)
    return d / "drift-check.sh"


@pytest.mark.parametrize("line", [
    "ok forty-seven 0",   # rows is not a number
    "ok 47 zero",         # the fuzzyclaw count is not a number
    "ok 47",              # only TWO fields — both counts came from field 2
    "ok",                 # only one
    "ok 47 0 9",          # FOUR — the middle two are not the pair we read
    "",                   # nothing at all
])
def test_a_malformed_reader_line_is_a_could_not_measure_not_a_zero(fleet, line):
    """🔴 THE COUNTS ARE READ POSITIONALLY, so a line that is not
    `<token> <int> <int>` puts arbitrary text where a number belongs — and
    `[ "$x" -gt 0 ]` over text is a shell ERROR, not a false. The `case` filters
    turn each into -1 first, which is what makes this COULD NOT MEASURE instead
    of a crash or, worse, a fall-through to the READY branch.

    🔴 THE FIELD COUNT IS HALF OF IT, and it is the half that was missing.
    `${x%% *}` and `${x##* }` both return the WHOLE STRING when it holds no
    space, so `ok 47` set both counts from field 2 and printed `47 of 47 row(s)
    EXAMINED` — a fabricated denominator that passed every numeric filter.
    MEASURED here before the fix; it reads as NOT READY, so it was fail-safe by
    luck rather than by construction, which is not the standard this gate holds
    every other non-measurement to.
    """
    fleet.catch_up()
    fleet.stub_session_manager(fleet.sm_report(rows=47, fuzzy=0))
    script = _drift_copy(
        fleet, "import sys\nsys.stdin.read()\nprint(%r)\n" % line)
    rc, out = fleet.check("--no-remote", script=str(script))
    assert rc == 0, out
    assert "[phase2] COULD NOT MEASURE" in out, out
    assert "READY" not in out, out
    assert "UNBLOCKED" not in out, out


def test_an_unreadable_phase2_reader_is_a_could_not_measure(fleet):
    """The `[ ! -r "$_drift_phase2_py" ]` branch, reachable only through a copy.
    A checkout missing the reader must not report a zero."""
    fleet.catch_up()
    fleet.stub_session_manager(fleet.sm_report(rows=47, fuzzy=0))
    script = _drift_copy(fleet, None)      # lib/ has host-role.sh but no reader
    rc, out = fleet.check("--no-remote", script=str(script))
    assert rc == 0, out
    assert "[phase2] COULD NOT MEASURE — cannot read" in out, out
    assert "This is NOT a zero and NOT 'phase 2 is ready'." in out, out
    assert "READY" not in out, out


def test_the_drift_copy_harness_can_still_produce_a_ready(fleet):
    """🔴 POSITIVE CONTROL FOR THE HARNESS ABOVE. Every assertion in the two
    tests before this one is that something did NOT happen, and a copy whose
    phase-2 gate is broken for an unrelated reason (a missing lib, a bad path)
    would satisfy all of them while measuring nothing. So: the same copy, the
    REAL reader, and the READY verdict must come back."""
    fleet.catch_up()
    fleet.stub_session_manager(fleet.sm_report(rows=47, fuzzy=0))
    script = _drift_copy(fleet, PHASE2_PY.read_text())
    rc, out = fleet.check("--no-remote", script=str(script))
    assert rc == 16, out
    assert "🔴 READY — 0 of 47 rows depend on fuzzyclaw for an age." in out, out


def test_a_non_integer_phase2_timeout_is_rejected(fleet):
    """`DRIFT_PHASE2_TIMEOUT` is handed to `timeout`, and `require_int` is what
    keeps it an integer. Removing that call survived a mutation sweep because
    nothing exercised it."""
    rc, out = fleet.check("--no-remote", DRIFT_PHASE2_TIMEOUT="60; echo PWNED")
    assert rc == 2, f"expected a usage error, got {rc}\n{out}"
    assert "DRIFT_PHASE2_TIMEOUT must be a non-negative integer" in out, out
    assert not [ln for ln in out.splitlines() if ln.strip() == "PWNED"], out


# --------------------------------------------------------------------------- #
# 11. SOURCE-REPO PARITY (rc 17) — the repos devrc BUILDS PACKAGES FROM
#
# 🔴 A THIRD KIND OF PARITY. devrc has `nix/pkgs/**` derivations whose `src` is
# `${workspace}/<repo>/…` — a LOCAL working tree of a DIFFERENT repo. Nothing
# converges those: ship.sh is scoped to $HOME/workspace/devrc. So a host can have
# a perfect devrc checkout, every managed symlink resolving, and still compile
# months-old code, and both existing halves of this deadman report clean.
#
# MEASURED 2026-08-14 on clawgatectl: the laptop's ~/workspace/homelab-talos was
# 24 commits behind, so it built a CLI without `task status`/`task comment` —
# and devrc's hand-written version literal stamped "0.7.95" onto it anyway, so
# `clawgatectl task status <id> in_progress` printed help and exited 0. Silent.
# This file was green on that host the whole time.
#
# The load-bearing tests here are, in order:
#   * the NEGATIVE CONTROLS — behind, and ahead — watched red with rc 17;
#   * the POSITIVE CONTROL — a current repo is green, so the reds mean something;
#   * the four NOT-DRIFT-BUT-NOT-A-PASS shapes (absent, fetch failed, no
#     upstream, dirty), each of which must be counted as UNMEASURED rather than
#     folded into a reassuring "0 stale";
#   * the TWO-WAY PIN of the covered set against the real nix/pkgs, which is what
#     makes "a third package is covered automatically" a fact instead of a hope.
# --------------------------------------------------------------------------- #

# 🔴 THE LEDGER. The BUILT-SOURCE SCOPES devrc compiles, as of this commit — the
# full `${workspace}/…` path of each package's `srcDir`, NOT merely its repo. That
# distinction is the whole point of the pathspec scoping: `homelab-talos` takes
# ~7 commits a day of which only about a third touch `containers/clawgate`, so
# escalating on the REPO made rc 17 a permanently-red gate (measured — see the
# SOURCE-REPO PARITY block in drift-check.sh).
#
# Pinned as a LITERAL and cross-checked BOTH ways below — against an independent
# Python extraction and against what the shell payload itself reports — so the
# set fails the suite when it GROWS (a new source package nobody told the
# deadman about) and when it SHRINKS (a package moved to fetchFromGitHub and the
# checker is now fetching a repo for no reason). Same discipline as
# run-tests.sh's TARGET_FLOORS: a derived value, pinned two-way against the
# thing it is derived from.
EXPECTED_BUILT_SOURCE_SCOPES = {"homelab-talos/containers/clawgate", "tmux-fuzzyclaw"}

# The repos those scopes live in — the unit that gets FETCHED (one fetch however
# many packages sit in it), which is what the unit's time budget is a function of.
EXPECTED_SOURCE_REPOS = {q.split("/", 1)[0] for q in EXPECTED_BUILT_SOURCE_SCOPES}

NIX_PKGS = REPO_ROOT / "nix" / "pkgs"


def _oracle_source_repos():
    """An INDEPENDENT extraction of the same set, built differently on purpose.

    The payload walks with a bash glob and bash parameter expansion; this walks
    with pathlib.rglob and a regex. Two constructions that agree are evidence;
    one construction compared to itself is not (a shared blind spot survives).

    Returns the FULL srcDir paths, because that — not the repo — is the unit the
    verdict is computed over.
    """
    out = set()
    for p in sorted(NIX_PKGS.rglob("*.nix")):
        for ln in p.read_text().splitlines():
            for hit in re.findall(r"\$\{workspace\}/([A-Za-z0-9._/-]+)",
                                  ln.split("#", 1)[0]):
                out.add(hit.rstrip("/"))
    return out


def _src_facts(out):
    """The `FACT src-repos …` line as a {name: head} dict.

    Raises rather than returning {} when the line is missing: an absent fact set
    and an empty one are the same value to a comparison, and only one of them is
    good news."""
    m = re.search(r"FACT src-repos(.*)", out)
    assert m, "no `FACT src-repos` line in the output:\n" + out
    d = {}
    for tok in m.group(1).split():
        k, _, v = tok.partition("=")
        d[k] = v
    return d


def _src_counts(out):
    """(examined, stale, unmeasured) — 🔴 THE TRIPLE IS THE CLAIM, never one of
    them. A bare `stale=0` from a scan that walked no repos, or one whose every
    fetch failed, is indistinguishable from a clean host."""
    m = re.search(r"source repos: examined=(\d+) stale=(\d+) unmeasured=(\d+)", out)
    assert m, ("no examined/stale/unmeasured TRIPLE in the output — the triple "
               "IS the claim:\n" + out)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _nixpkg(fleet, filename, *names, repo=None):
    """Write a nix/pkgs file into the devrc checkout naming `${workspace}/<name>`
    sources, in the shape the real derivations use."""
    d = (repo or fleet.work) / "nix" / "pkgs" / "tools"
    d.mkdir(parents=True, exist_ok=True)
    body = ["{ pkgs, workspace }:", "let"]
    for n in names:
        body.append('  src%s = pkgs.lib.cleanSource (/. + "${workspace}/%s");' % (len(body), n))
    body.append("in [ ]")
    (d / filename).write_text("\n".join(body) + "\n")


# The paths every fixture source repo carries. Two of them sit UNDER a srcDir a
# package would be built from and one deliberately does not — that third path is
# what makes "behind, but not in anything we compile" constructible, which is the
# case the whole pathspec scoping exists for and which no single-file fixture can
# express.
SRC_FILES = ("f", "containers/clawgate/main.go", "clusters/naida/deploy.yaml")


def _src_repo(fleet, name, *, branch="main", home=None):
    """A bare origin plus a clone at <home>/workspace/<name>, both on `branch`.

    Returns (clone, builder). The builder is a second clone used to advance the
    upstream, so `behind` is produced the way it happens in life — someone else
    pushed — rather than by rewriting the clone's refs."""
    home = home or fleet.home
    origin = fleet.root / ("srcorigin-%s-%s.git" % (home.name, name))
    fleet._run(["git", "init", "-q", "--bare", "-b", branch, str(origin)])
    builder = fleet.root / ("srcbuild-%s-%s" % (home.name, name))
    fleet._run(["git", "clone", "-q", str(origin), str(builder)])
    for rel in SRC_FILES:
        f = builder / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("base\n")
    fleet.git(builder, "checkout", "-q", "-B", branch)
    for rel in SRC_FILES:
        fleet.git(builder, "add", rel)
    fleet.git(builder, "commit", "-q", "-m", "base")
    fleet.git(builder, "push", "-q", "-u", "origin", branch)
    clone = home / "workspace" / name
    clone.parent.mkdir(parents=True, exist_ok=True)
    fleet._run(["git", "clone", "-q", str(origin), str(clone)])
    fleet.git(clone, "checkout", "-q", branch)
    return clone, builder


def _push_upstream(fleet, builder, branch="main", n=1, path="f"):
    """Advance the upstream by `n` commits, each touching exactly `path`.

    🔴 `path` is the load-bearing parameter. The verdict is computed with a
    pathspec limited to the package's own srcDir, so "behind by N" is only
    meaningful once you say WHERE — and a fixture that always writes the same
    file cannot tell a commit that changes the built source from one that
    cannot.
    """
    for i in range(n):
        f = builder / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("base\nupstream-%d\n" % i)
        fleet.git(builder, "add", path)
        fleet.git(builder, "commit", "-q", "-m", "upstream %d (%s)" % (i, path))
    fleet.git(builder, "push", "-q", "origin", branch)


# --- the negative controls, in the real failure shape ----------------------- #
def test_a_source_repo_behind_its_upstream_is_rc17(fleet):
    """🔴 THE MEASURED FAILURE, reproduced: the checkout devrc compiles is behind
    what its own upstream says, and no other check in this file can see it."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _, builder = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, builder, branch="trunk", n=3)

    rc, out = fleet.check("--no-remote")
    assert rc == 17, f"a stale source repo must be rc 17, got {rc}\n{out}"
    assert "BUILT SOURCE homelab-talos is NOT current: 3 behind / 0 ahead" in out, out
    assert _src_counts(out) == (1, 1, 0), out


def test_a_source_repo_with_UNPUSHED_commits_is_rc17(fleet):
    """The other direction, and it is the same loss class as rc 8 one repo over:
    a commit that exists on exactly one machine, in code the other machine
    compiles."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    clone, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    (clone / "new.go").write_text("package main\n")
    fleet.git(clone, "add", "new.go")
    fleet.git(clone, "commit", "-q", "-m", "un-pushed")

    rc, out = fleet.check("--no-remote")
    assert rc == 17, f"un-pushed source commits must be rc 17, got {rc}\n{out}"
    assert "0 behind / 1 ahead" in out, out
    assert _src_counts(out) == (1, 1, 0), out


# --- the positive control --------------------------------------------------- #
def test_a_current_source_repo_is_green(fleet):
    """🔴 REPORT THIS ALONGSIDE THE REDS, never alone: a zero from a checker
    wired to nothing looks exactly like this. Here the checker must both find the
    repo (examined=1) and pronounce it current."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _src_repo(fleet, "homelab-talos", branch="trunk")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"a current source repo must not be drift, got {rc}\n{out}"
    assert "BUILT SOURCE homelab-talos is CURRENT — trunk ==" in out, out
    assert _src_counts(out) == (1, 0, 0), out


def test_the_examined_count_is_reported_beside_the_stale_count(fleet):
    """One stale repo among two must print BOTH numbers.

    `stale=1` alone says nothing about coverage, and `examined=2` alone says
    nothing about health. The pair is the claim — the same rule the managed
    symlink scan follows one subsystem over."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    _, b1 = _src_repo(fleet, "homelab-talos", branch="trunk")
    _src_repo(fleet, "tmux-fuzzyclaw")
    _push_upstream(fleet, b1, branch="trunk")

    rc, out = fleet.check("--no-remote")
    assert rc == 17, out
    assert _src_counts(out) == (2, 1, 0), out
    assert "BUILT SOURCE tmux-fuzzyclaw is CURRENT" in out, out


def test_a_scan_that_found_no_source_packages_says_so_instead_of_reporting_zero(fleet):
    """🔴 A checkout with no `${workspace}/` derivations must print NOT EVALUATED.

    `examined=0 stale=0` would be a scan that walked nothing wearing the exact
    output of a clean host — the failure mode this whole file exists to refuse."""
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert "source repos: NOT EVALUATED" in out, out
    assert "a scan that examined nothing is not a clean scan" in out, out
    assert "source repos: examined=" not in out, (
        "a scan that walked nothing printed a count triple:\n" + out
    )


# --- reported, but NOT drift (and never folded into a zero) ------------------ #
def test_an_absent_source_repo_is_reported_as_unmeasured_not_as_clean(fleet):
    """A host without the checkout is a documented, tolerated state — the
    derivations guard on pathExists and simply omit the binary. It must still
    never be counted as a repo that was found current."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"an absent source repo is not drift, got {rc}\n{out}"
    assert "source repo homelab-talos: ABSENT" in out, out
    assert _src_counts(out) == (1, 0, 1), out
    assert _src_facts(out)["homelab-talos"] == "ABSENT", out


def test_a_failed_fetch_is_reported_as_unmeasured_not_as_clean(fleet):
    """These repos are private and reached over ssh, and a systemd --user unit
    has no ssh-agent. "We could not look" must read as neither a pass nor a
    divergence — and git's own stderr must survive, because for a unit whose only
    output is the journal that message is the entire diagnosis."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    clone, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    fleet.git(clone, "remote", "set-url", "origin",
              str(fleet.root / "definitely-not-a-repo"))

    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"an unreachable source remote is not drift, got {rc}\n{out}"
    assert "git fetch FAILED" in out, out
    assert "git:" in out, "git's own stderr was swallowed\n" + out
    assert _src_counts(out) == (1, 0, 1), out


def test_a_detached_source_repo_is_reported_as_unmeasured_not_as_clean(fleet):
    """A detached HEAD has no upstream, so there is no defined answer to compare
    against. Assuming `main` would be a guess printed as a measurement — and
    these repos do not even agree on a default branch (homelab-talos uses
    `trunk`)."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    clone, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    fleet.git(clone, "checkout", "-q", "--detach", "HEAD")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"a detached source checkout is not drift, got {rc}\n{out}"
    assert "no upstream to compare against" in out, out
    assert _src_counts(out) == (1, 0, 1), out


def test_a_dirty_source_repo_is_reported_even_when_it_is_current(fleet):
    """🔴 REPORTED, NEVER DRIFT — and reported even on a repo that is otherwise
    perfectly current, which is the case a "report the problems" check would
    drop. These derivations copy the working TREE, so an untracked .go file is IN
    the binary while `git log` says nothing happened. The workbench's
    homelab-talos is routinely dirty; failing the unit on it would be a
    permanently-red gate."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    clone, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    (clone / "scratch.go").write_text("package main\n")
    (clone / "f").write_text("edited\n")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"a dirty source repo is not drift on its own, got {rc}\n{out}"
    assert "source repo homelab-talos: DIRTY — 2 path(s)" in out, out
    assert "BUILT SOURCE homelab-talos is CURRENT" in out, out
    assert _src_counts(out) == (1, 0, 0), out


# --- the covered set is DERIVED, and pinned two-way -------------------------- #
def _run_srcrepo_payload(tmp_path, repo, home, label="pin"):
    """Run ONLY the SRCREPO payload, against a chosen repo and a chosen $HOME.

    Used to point the REAL extraction at the REAL nix/pkgs without running the
    git leg over the operator's own checkout: the fixture $HOME contains no
    `workspace/` at all, so every repo it names comes back ABSENT and not one
    git command touches anything real."""
    payload = _payload_literal("SRCREPO")
    env = dict(os.environ)
    env.update(HOME=str(home), DRIFT_REPO=str(repo), DRIFT_LABEL=label)
    proc = subprocess.run(["bash", "-c", payload],
                          capture_output=True, text=True, env=env)
    return proc.stdout + proc.stderr


def test_the_source_repo_set_is_pinned_two_way_against_nix_pkgs(tmp_path):
    """🔴 THE LEDGER, in three independent spellings that must all agree.

      1. `EXPECTED_SOURCE_REPOS` — the literal a human has reviewed;
      2. `_oracle_source_repos()` — pathlib + regex over nix/pkgs;
      3. what the SHELL PAYLOAD itself reports, run against the real nix/pkgs.

    Fails when the set GROWS — a new `${workspace}/`-sourced package that nobody
    told the deadman about, which is the exact shape of the bug this whole
    section exists for — and when it SHRINKS. (3) is what makes (1) and (2) more
    than bookkeeping: a checker that agrees with a regex but does not actually
    walk the tree is the thing being ruled out.
    """
    home = tmp_path / "empty-home"
    home.mkdir()
    out = _run_srcrepo_payload(tmp_path, REPO_ROOT, home)
    reported = set(_src_facts(out))

    assert _oracle_source_repos() == EXPECTED_BUILT_SOURCE_SCOPES, (
        "nix/pkgs' `${workspace}/`-sourced SCOPE set has CHANGED. Update "
        "EXPECTED_BUILT_SOURCE_SCOPES — and check that the new srcDir is one the "
        "deadman should be judging on both hosts: %r" % (_oracle_source_repos(),)
    )
    assert reported == EXPECTED_BUILT_SOURCE_SCOPES, (
        "drift-check.sh's own scan disagrees with nix/pkgs: it reported %r\n%s"
        % (sorted(reported), out)
    )
    # 🔴 AND THE SUBTREE MUST SURVIVE THE ROUND TRIP. `homelab-talos` alone would
    # satisfy a repo-level pin while silently restoring the whole-repo verdict
    # that made this gate permanently red, so assert the path is still there.
    assert any("/" in k for k in reported), (
        "no scope carries a srcDir SUBTREE — the scan has collapsed back to repo "
        "roots, which is the permanently-red-gate defect: %r" % sorted(reported)
    )
    # ...and it must have walked real files to get there. `0 scanned` producing
    # the right answer would mean the answer came from somewhere else.
    m = re.search(r"\((\d+) nix file\(s\) scanned\)", out)
    assert m and int(m.group(1)) >= len(list(NIX_PKGS.rglob("*.nix"))), out


def test_a_third_source_package_is_covered_with_no_change_to_the_checker(fleet):
    """🔴 THE GENERALISATION CLAIM, asserted rather than hoped.

    The set is derived from nix/pkgs at scan time, so a package added later is
    covered the day it lands. A hardcoded pair would have been correct when it
    was written and silently incomplete afterwards — the same shape as the bug.
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    _nixpkg(fleet, "brand-new.nix", "some-new-repo")
    _src_repo(fleet, "homelab-talos", branch="trunk")
    _src_repo(fleet, "tmux-fuzzyclaw")
    _, b3 = _src_repo(fleet, "some-new-repo")
    _push_upstream(fleet, b3, n=2)

    rc, out = fleet.check("--no-remote")
    assert rc == 17, f"the third package was not covered, got {rc}\n{out}"
    assert "BUILT SOURCE some-new-repo is NOT current: 2 behind" in out, out
    assert _src_counts(out) == (3, 1, 0), out


def test_two_subtrees_of_ONE_repo_are_judged_separately(fleet):
    """clawgatectl's src is `${workspace}/homelab-talos/containers/clawgate` — a
    SUBDIRECTORY. The repo root is what has a `.git` and gets fetched ONCE, but
    each srcDir under it is its own verdict.

    Here the upstream moves inside ONE of the two subtrees. A repo-level check
    would call both stale (or, worse, one repo "stale" with no way to say which
    package is affected); the scoped check must report exactly one.
    """
    fleet.catch_up()
    _nixpkg(fleet, "a.nix", "homelab-talos/containers/clawgate")
    _nixpkg(fleet, "b.nix", "homelab-talos/clusters/naida")
    _, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk", n=2,
                   path="containers/clawgate/main.go")

    rc, out = fleet.check("--no-remote")
    assert rc == 17, out
    # ONE repo, TWO scopes, exactly ONE of them stale.
    assert _src_counts(out) == (2, 1, 0), out
    assert "over 1 repo(s)" in out, "the repo was walked more than once\n" + out
    assert set(_src_facts(out)) == {
        "homelab-talos/containers/clawgate", "homelab-talos/clusters/naida"}, out
    assert "BUILT SOURCE homelab-talos/containers/clawgate is NOT current: 2 behind" in out, out
    assert "BUILT SOURCE homelab-talos/clusters/naida is CURRENT" in out, out


def test_two_source_repos_named_on_ONE_line_are_both_found(fleet):
    """🔴 FOUND BY A SURVIVING MUTANT, not by review.

    Mutating `LN="$REST"` (advance past the occurrence just consumed) to
    `LN=""` (stop after the first) SURVIVED the whole suite including the
    two-way pin — because every line in the real nix/pkgs happens to name at
    most one source, so the pin compared two extractions that shared the blind
    spot. A `let a = "${workspace}/x"; b = "${workspace}/y";` line is entirely
    legal nix, and under that mutant the second repo silently stops being
    watched. This is the fixture the pin cannot build for itself.
    """
    fleet.catch_up()
    d = fleet.work / "nix" / "pkgs" / "tools"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pair.nix").write_text(
        '{ pkgs, workspace }:\n'
        'let a = "${workspace}/first-repo"; b = "${workspace}/second-repo";\n'
        'in [ ]\n'
    )
    _src_repo(fleet, "first-repo")
    _, b2 = _src_repo(fleet, "second-repo")
    _push_upstream(fleet, b2, n=4)

    rc, out = fleet.check("--no-remote")
    assert set(_src_facts(out)) == {"first-repo", "second-repo"}, out
    assert rc == 17, f"the SECOND repo on the line went unwatched, got {rc}\n{out}"
    assert "BUILT SOURCE second-repo is NOT current: 4 behind" in out, out
    assert _src_counts(out) == (2, 1, 0), out


def test_a_workspace_path_inside_a_comment_is_not_a_source_repo(fleet):
    """clawgatectl.nix's header discusses its own source path in prose, and this
    checker's own header documents the `${workspace}/` pattern it looks for. A
    whole-file grep would "find" repos in the documentation and then report them
    ABSENT forever."""
    fleet.catch_up()
    d = fleet.work / "nix" / "pkgs" / "tools"
    d.mkdir(parents=True, exist_ok=True)
    (d / "commented.nix").write_text(
        "# the source lives at ${workspace}/ghost-repo, historically\n"
        '{ pkgs, workspace }:\n'
        '  src = pkgs.lib.cleanSource (/. + "${workspace}/real-repo");'
        '   # was ${workspace}/old-ghost\n'
    )
    _src_repo(fleet, "real-repo")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert set(_src_facts(out)) == {"real-repo"}, out
    assert "ghost" not in out, out


# --- severity: where rc 17 sorts -------------------------------------------- #
def test_unpushed_devrc_commits_still_outrank_a_stale_source_repo(fleet):
    """rc 8 > rc 17. A diverged devrc stops EVERY future change to the host, of
    which a stale source repo is one instance, and its rescue can destroy work."""
    fleet.catch_up()
    fleet.add_local_commit()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk")

    rc, out = fleet.check("--no-remote")
    assert rc == 8, f"rc 8 must outrank rc 17, got {rc}\n{out}"
    assert "BUILT SOURCE homelab-talos is NOT current" in out, (
        "the source-repo finding must still be PRINTED even when outranked\n" + out
    )


def test_a_stale_source_repo_outranks_dangling_symlinks_and_a_behind_checkout(fleet):
    """rc 17 > rc 14 and rc 17 > rc 10.

    A dangling managed symlink is LOUD at the moment of use (`command not
    found`); a stale source repo is SILENT — the measured failure ran, exited 0
    and did nothing. `behind` is merely a ship away."""
    lstore = _mkhome(fleet.home, healthy=1, dangling=2)
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk")

    # `work` is left one commit BEHIND origin/main (the fixture's default), so
    # rc 10 is live too and all three conditions are present at once.
    rc, out = _parity(fleet, "--no-remote", store=lstore)
    assert rc == 17, f"rc 17 must outrank rc 14 and rc 10, got {rc}\n{out}"
    assert "managed symlink(s) point at a path that does not exist" in out, out
    assert "local main is BEHIND origin/main" in out, out


def test_the_rc17_legend_is_printed_with_the_verdict(fleet):
    """The journal is the only place this output is ever read, so the code it
    hands systemd has to be legible there without opening the source."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk")

    rc, out = fleet.check("--no-remote")
    assert rc == 17, out
    assert "drift-check: DRIFT (rc=17)" in out, out
    assert "rc17=the srcDir SUBTREE" in out, out


# --- the cross-host half ----------------------------------------------------- #
def test_the_cross_host_source_comparison_is_NOT_COMPARED_with_one_host(fleet):
    """One host's facts is not "the machines agree", it is "agreement not looked
    for", and the two must never print the same way."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    _src_repo(fleet, "homelab-talos", branch="trunk")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert "[srcrepo] NOT COMPARED" in out, out
    assert "Nothing was compared" in out, out


def _two_host_src(fleet, *, remote_extra=0):
    """Both hosts run the REAL payload against the SAME nix/pkgs but their OWN
    $HOME/workspace, which is exactly how the two machines differ in life.

    `remote_extra` commits are made AND PUSHED on the remote side, so that host
    ends up CURRENT with its own upstream at a sha the local host does not have.
    That is the shape the cross-host claim is about, and it is deliberately not
    reachable through "behind": with identical fixture content and one clock
    second, two independently built base commits hash the SAME, so a test that
    tried to make the heads differ by advancing an upstream compared a sha to
    itself and passed for the wrong reason (measured).
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    lstore = _mkhome(fleet.home, healthy=1)
    _src_repo(fleet, "homelab-talos", branch="trunk")

    rhome = fleet.root / "remote-home"
    rhome.mkdir()
    rstore = _mkhome(rhome, healthy=1, store=fleet.root / "remote-store")
    rclone, _ = _src_repo(fleet, "homelab-talos", branch="trunk", home=rhome)
    for i in range(remote_extra):
        (rclone / "f").write_text("remote-only-%d\n" % i)
        fleet.git(rclone, "commit", "-q", "-am", "remote work %d" % i)
    if remote_extra:
        fleet.git(rclone, "push", "-q", "origin", "trunk")
    _remote_running_the_real_payload(fleet, rhome, rstore)
    return _parity(fleet, store=lstore, REMOTE_SSH="stub@example.invalid")


def test_the_cross_host_comparison_reports_differing_heads_without_setting_an_rc(fleet):
    """🔴 INFORMATION, NOT A VERDICT — and that is a decision, not an omission.

    Whether a given HEAD is WRONG has a defined answer and is measured per host
    against that branch's own upstream. "The two hosts are on different commits"
    has no such answer: these are shared development repos and one machine on a
    feature branch is normal. A code that fired on it would be red most of the
    time, and a permanently-red gate is worse than no gate.

    So this is the load-bearing case: BOTH hosts are current with their own
    upstreams — nothing is stale anywhere — and their source HEADs still differ.
    The difference is printed and the exit code stays 0.
    """
    rc, out = _two_host_src(fleet, remote_extra=1)
    assert rc == 0, (
        "a cross-host source difference must not set an exit code, got %d\n%s"
        % (rc, out))
    assert "the two hosts build DIFFERENT source" in out, out
    assert "information only" in out, out
    assert "compared=1 same=0 differing=1" in out, out


def test_the_cross_host_comparison_reports_agreement_when_the_heads_match(fleet):
    """🔴 POSITIVE CONTROL for the comparison. Without it, `differing=0` is
    indistinguishable from a comparator wired to nothing — an empty union minus
    anything is still empty."""
    rc, out = _two_host_src(fleet)
    assert rc == 0, out
    assert "[srcrepo] compared=1 same=1 differing=0" in out, out
    assert "NOT COMPARED" not in out.split("=== source-repo parity")[1], out


def test_a_non_integer_source_fetch_timeout_is_rejected(fleet):
    """`DRIFT_SRC_FETCH_TIMEOUT` is handed to `timeout`, so `require_int` is what
    keeps it an integer — and what keeps a shell metacharacter out of a command
    line. Its sibling for DRIFT_PHASE2_TIMEOUT survived a mutation sweep because
    nothing exercised it; this is the same guard, exercised."""
    rc, out = fleet.check("--no-remote", DRIFT_SRC_FETCH_TIMEOUT="30; echo PWNED")
    assert rc == 2, f"expected a usage error, got {rc}\n{out}"
    assert "DRIFT_SRC_FETCH_TIMEOUT must be a non-negative integer" in out, out
    assert not [ln for ln in out.splitlines() if ln.strip() == "PWNED"], out


def test_the_source_fetch_timeout_is_not_forwarded_over_ssh(fleet):
    """⚠ INVARIANT GUARD, not regression coverage — trivially GREEN AT a2707be,
    where the variable did not exist. It pins a property of the new code that
    nothing else asserts.

    🔴 Every value this script sends across the ssh hop is a value that has to be
    proved safe on the FAR side, where the static passivity scanner cannot see
    it. The remote host uses the default — asserted on the payload the driver
    actually builds, not on a comment claiming it.
    """
    src = DRIFT.read_text()
    i = src.index("| ssh -o ConnectTimeout=10")
    forwarded = src[src.rindex("printf 'DRIFT_LABEL", 0, i):i]
    # Positive control: the slice must contain the values that ARE forwarded, or
    # "the timeout is not in it" is a claim about an empty string.
    for expected in ("DRIFT_LABEL", "DRIFT_UNTRACKED_MAX", "DRIFT_DANGLING_MAX"):
        assert expected in forwarded, (
            "the forwarded-env slice is not the one the driver builds: %r"
            % forwarded)
    assert "DRIFT_SRC_FETCH_TIMEOUT" not in forwarded, (
        "the source-fetch timeout is interpolated into the REMOTE payload:\n"
        + forwarded
    )


# --- the SEAM: the script's fetch budget vs the unit's start timeout ---------- #
def test_the_unit_start_timeout_can_absorb_every_source_repo_fetch():
    """🔴 A SEAM NEITHER FILE'S TESTS OWN.

    The per-fetch cap is a tunable in drift-check.sh; the ceiling on the whole
    run is `TimeoutStartSec` in nix/home.nix. Their PRODUCT is what decides
    whether the unit finishes, and nothing computed it: the ceiling was 180,
    sized for "two `git fetch`es plus one ssh round trip", while the source-repo
    leg adds `2 hosts x N repos x cap` on top.

    The failure it guards is the silent kind. systemd kills the cgroup at the
    ceiling, the deadman reports NOTHING — no verdict about either host — and
    the unit merely looks slow. So this recomputes the budget from BOTH files
    and fails when either moves out from under the other, whether the cap grows,
    a third source repo lands, or the ceiling shrinks.
    """
    m = re.search(r'DRIFT_SRC_FETCH_TIMEOUT="\$\{DRIFT_SRC_FETCH_TIMEOUT:-(\d+)\}"',
                  DRIFT.read_text())
    assert m, "no DRIFT_SRC_FETCH_TIMEOUT default in drift-check.sh"
    cap = int(m.group(1))
    m2 = re.search(r'DRIFT_PHASE2_TIMEOUT="\$\{DRIFT_PHASE2_TIMEOUT:-(\d+)\}"',
                   DRIFT.read_text())
    assert m2, "no DRIFT_PHASE2_TIMEOUT default in drift-check.sh"
    phase2 = int(m2.group(1))
    # The branch-protection probe (rc 24) is a third capped subprocess, and it
    # is exactly the kind of addition this seam exists to catch: a network call
    # added to the script with no corresponding room in the unit's ceiling.
    m4 = re.search(r'DRIFT_GH_TIMEOUT="\$\{DRIFT_GH_TIMEOUT:-(\d+)\}"',
                   DRIFT.read_text())
    assert m4, "no DRIFT_GH_TIMEOUT default in drift-check.sh"
    gh = int(m4.group(1))

    block = _drift_service_block()
    m3 = re.search(r"TimeoutStartSec = (\d+);", block)
    assert m3, "the drift-check unit declares no TimeoutStartSec:\n" + block
    ceiling = int(m3.group(1))

    # 2 hosts x every source repo, plus the phase-2 scan, plus the 60s this
    # already needed for the two devrc fetches and the ssh round trip.
    needed = 2 * len(EXPECTED_SOURCE_REPOS) * cap + phase2 + gh + 60
    assert ceiling >= needed, (
        "TimeoutStartSec=%d cannot absorb the worst case: %d source fetches at "
        "%ds + a %ds phase-2 scan + a %ds branch-protection probe + 60s of devrc "
        "fetch/ssh = %ds. systemd would kill the run and the deadman would report "
        "nothing, on a schedule."
        % (ceiling, 2 * len(EXPECTED_SOURCE_REPOS), cap, phase2, gh, needed)
    )


# --- passivity, behaviourally ------------------------------------------------ #
def test_a_run_against_a_stale_source_repo_mutates_nothing(fleet):
    """🔴 THE CONTRACT. This leg FETCHES — a write to remote-tracking refs and
    nothing else. Branch, HEAD, worktree and stash stack of the source repo must
    all be byte-identical afterwards, for shapes nobody enumerated.
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos")
    clone, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk", n=2)
    (clone / "wip.go").write_text("package main // uncommitted\n")

    def snapshot():
        return (
            fleet.git(clone, "symbolic-ref", "--quiet", "--short", "HEAD"),
            fleet.git(clone, "rev-parse", "HEAD"),
            fleet.git(clone, "status", "--porcelain"),
            fleet.git(clone, "stash", "list"),
            (clone / "wip.go").read_text(),
        )

    before = snapshot()
    rc, out = fleet.check("--no-remote")
    assert rc == 17, out
    assert snapshot() == before, (
        "🔴 the deadman MUTATED a source repo — it may only fetch\n" + out
    )


# --------------------------------------------------------------------------- #
# 11b. THE VERDICT IS SCOPED TO THE srcDir SUBTREE, NOT THE REPO
#
# 🔴 rc 17 shipped escalating on WHOLE-REPO staleness, and that is a
# permanently-red gate — the failure mode RULES.md names explicitly, because it
# trains click-through on the one alert that has to keep its meaning. MEASURED on
# the workbench 2026-08-18: it was 1 commit behind `origin/trunk`, and that commit
# was `2ce7cbdc fix(naida-ai-demo): raise memory limit 128Mi -> 512Mi` —
# `git diff --name-only HEAD..origin/trunk -- containers/clawgate` EMPTY, so it
# cannot reach the built binary. Over the preceding 14 days that repo took 98
# commits of which only 32 touched `containers/clawgate`; at ~7 commits/day the
# host is behind nearly continuously and about two thirds of those reds could not
# affect any package devrc builds.
#
# The two tests that matter are a PAIR, and neither is worth anything alone:
#   * a commit OUTSIDE every srcDir must NOT escalate — the regression;
#   * a commit INSIDE one still MUST — or the noise was "fixed" by breaking the
#     detector, and every mutation would pass.
# --------------------------------------------------------------------------- #
def test_a_commit_OUTSIDE_every_srcDir_is_reported_but_is_NOT_rc17(fleet):
    """🔴 THE REGRESSION, in the measured shape: the repo is behind, and not by
    anything the package is compiled from.

    The fixture mirrors the real one — a `containers/clawgate` srcDir and an
    unrelated `clusters/naida` path — and the upstream commit lands only in the
    latter. rc must stay 0, and the finding must still be legible: the repo-wide
    count is printed, and the built source is stated to be CURRENT.
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos/containers/clawgate")
    _, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk", n=1,
                   path="clusters/naida/deploy.yaml")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, (
        "a commit that cannot reach the built binary escalated to rc %d — that is "
        "the permanently-red gate\\n%s" % (rc, out))
    # 🔴 Reported, not dropped: the repo-wide number is true and useful.
    assert "repo-wide 1 behind / 0 ahead" in out, (
        "the whole-repo count was silently dropped\\n" + out)
    assert "repo-wide is INFORMATION ONLY" in out, out
    assert "BUILT SOURCE homelab-talos/containers/clawgate is CURRENT (0 behind / 0 ahead)" in out, out
    assert "touch nothing this package is built from" in out, out
    assert _src_counts(out) == (1, 0, 0), out


def test_a_commit_INSIDE_a_srcDir_is_still_rc17(fleet):
    """🔴 THE OTHER HALF OF THE PAIR — and the guard against "fixing" the noise
    by breaking the detector.

    Byte-for-byte the fixture above except for WHICH path the upstream commit
    touches. That is the only variable, so a green here plus a green above is a
    statement about the pathspec and nothing else.
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos/containers/clawgate")
    _, b = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b, branch="trunk", n=1,
                   path="containers/clawgate/main.go")

    rc, out = fleet.check("--no-remote")
    assert rc == 17, (
        "a commit INSIDE the srcDir did not escalate — the detector is broken, "
        "not merely quiet\\n%s" % out)
    assert "BUILT SOURCE homelab-talos/containers/clawgate is NOT current: 1 behind / 0 ahead" in out, out
    assert "repo-wide 1 behind / 0 ahead" in out, out
    assert _src_counts(out) == (1, 1, 0), out


def test_UNPUSHED_commits_inside_a_srcDir_still_escalate_and_outside_do_not(fleet):
    """The AHEAD direction gets the same pathspec, in both directions.

    Scoping only the behind half would leave un-pushed cluster manifests firing
    rc 17 forever while un-pushed clawgate code was the case that mattered.
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos/containers/clawgate")
    clone, _ = _src_repo(fleet, "homelab-talos", branch="trunk")

    # (a) un-pushed work OUTSIDE the srcDir — reported, not drift.
    (clone / "clusters" / "naida" / "deploy.yaml").write_text("local edit\\n")
    fleet.git(clone, "add", "clusters/naida/deploy.yaml")
    fleet.git(clone, "commit", "-q", "-m", "local manifest tweak")
    rc, out = fleet.check("--no-remote")
    assert rc == 0, "un-pushed work outside every srcDir escalated\\n" + out
    assert "repo-wide 0 behind / 1 ahead" in out, out
    assert "BUILT SOURCE homelab-talos/containers/clawgate is CURRENT (0 behind / 0 ahead)" in out, out

    # (b) now un-pushed work INSIDE it — same repo, same run shape, must fire.
    (clone / "containers" / "clawgate" / "main.go").write_text("local code\\n")
    fleet.git(clone, "add", "containers/clawgate/main.go")
    fleet.git(clone, "commit", "-q", "-m", "un-pushed clawgate change")
    rc, out = fleet.check("--no-remote")
    assert rc == 17, "un-pushed work INSIDE the srcDir did not escalate\\n" + out
    assert "BUILT SOURCE homelab-talos/containers/clawgate is NOT current: 0 behind / 1 ahead" in out, out


def test_a_root_srcDir_package_is_unchanged_by_the_scoping(fleet):
    """⚠ MOSTLY AN INVARIANT GUARD, and the distinction is worth stating.

    MEASURED at b10c4ae: RED — but only on the message wording, which this commit
    renamed. Its BEHAVIOURAL claim (rc 17 for a root-srcDir package that is one
    commit behind) already held there, so this is not regression coverage for the
    scoping defect. What it does hold is the boundary the fix could have broken:
    tmux-fuzzyclaw's srcDir IS its repo root, its scope and its repo coincide, and
    every commit anywhere in it genuinely does change what is built. A pathspec
    accidentally applied to the root case would silence it, and that mutation is
    in the battery.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    _, b = _src_repo(fleet, "tmux-fuzzyclaw")
    _push_upstream(fleet, b, n=1, path="clusters/naida/deploy.yaml")

    rc, out = fleet.check("--no-remote")
    assert rc == 17, (
        "a root-srcDir package stopped escalating — for it, EVERY commit is in "
        "the built source\\n%s" % out)
    assert "BUILT SOURCE tmux-fuzzyclaw is NOT current: 1 behind / 0 ahead" in out, out
    assert set(_src_facts(out)) == {"tmux-fuzzyclaw"}, out


def test_an_unmeasurable_repo_makes_EVERY_scope_under_it_unmeasured(fleet):
    """🔴 One repo, several packages: a repo we could not evaluate must not let
    any of its scopes read as a silent pass.

    Without this, `examined` counted repos and a two-package repo whose fetch
    failed contributed ONE unmeasured — leaving the second package accounted for
    nowhere at all.
    """
    fleet.catch_up()
    _nixpkg(fleet, "a.nix", "homelab-talos/containers/clawgate")
    _nixpkg(fleet, "b.nix", "homelab-talos/clusters/naida")
    clone, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    fleet.git(clone, "remote", "set-url", "origin",
              str(fleet.root / "definitely-not-a-repo"))

    rc, out = fleet.check("--no-remote")
    assert rc == 0, "an unreachable source remote is not drift\\n" + out
    assert _src_counts(out) == (2, 0, 2), out
    facts = _src_facts(out)
    assert facts == {"homelab-talos/containers/clawgate": "FETCHFAILED",
                     "homelab-talos/clusters/naida": "FETCHFAILED"}, out


# --- the cross-host half, scoped the same way -------------------------------- #
def _two_host_scoped(fleet, *, remote_path):
    """Both hosts current with their own upstreams; the REMOTE carries one extra
    pushed commit touching `remote_path`. The only variable is where it lands."""
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos/containers/clawgate")
    lstore = _mkhome(fleet.home, healthy=1)
    _src_repo(fleet, "homelab-talos", branch="trunk")

    rhome = fleet.root / "remote-home"
    rhome.mkdir()
    rstore = _mkhome(rhome, healthy=1, store=fleet.root / "remote-store")
    rclone, _ = _src_repo(fleet, "homelab-talos", branch="trunk", home=rhome)
    f = rclone / remote_path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("remote-only\\n")
    fleet.git(rclone, "add", remote_path)
    fleet.git(rclone, "commit", "-q", "-m", "remote work")
    fleet.git(rclone, "push", "-q", "origin", "trunk")
    _remote_running_the_real_payload(fleet, rhome, rstore)
    return _parity(fleet, store=lstore, REMOTE_SSH="stub@example.invalid")


def test_the_cross_host_comparison_ignores_divergence_OUTSIDE_every_srcDir(fleet):
    """🔴 "The two hosts build DIFFERENT source" must mean DIFFERENT BUILT
    SOURCE. Compared on repo HEADs it fired whenever the machines disagreed about
    any commit at all — cluster manifests included — which is the same
    permanently-noisy shape one level over.
    """
    rc, out = _two_host_scoped(fleet, remote_path="clusters/naida/deploy.yaml")
    assert rc == 0, out
    block = out.split("=== source-repo parity")[1]
    assert "the two hosts build DIFFERENT source" not in block, (
        "a divergence outside every srcDir was reported as different built "
        "source\\n" + block)
    assert "compared=1 same=1 differing=0" in block, block


def test_the_cross_host_comparison_still_reports_a_differing_built_subtree(fleet):
    """POSITIVE CONTROL for the test above — without it, `differing=0` is
    indistinguishable from a comparator wired to nothing."""
    rc, out = _two_host_scoped(fleet, remote_path="containers/clawgate/main.go")
    block = out.split("=== source-repo parity")[1]
    assert "the two hosts build DIFFERENT source" in block, block
    assert "srcDir subtree trees, not repo HEADs" in block, block
    assert "compared=1 same=0 differing=1" in block, block


# --------------------------------------------------------------------------- #
# 13. rc 18 — A BUILT-SOURCE SCOPE THAT STAYS UNMEASURED
#
# 🔴 THE GAP rc 17 LEFT, and it is the same shape as the bug rc 17 was built to
# catch. "We could not look" correctly sets no exit code — so it escalated NEVER,
# and a scope whose currency is never evaluated read as a clean run forever.
# Measured live on the workbench 2026-08-18: tmux-fuzzyclaw on a local branch
# with no upstream, `unmeasured=1`, rc 0 — while concealing a genuinely divergent
# build between the two hosts.
#
# The ladder is deliberately the rc-13 one: reported every run, escalated only
# after N CONSECUTIVE runs, per (HOST, SCOPE), reset the moment it measures. So
# these tests are written in the same two directions rc 13's are — the softening
# must not make the deadman mute, AND the escalation must not fire on the normal
# case (a scratch branch for an afternoon, a laptop that never had the checkout).
# --------------------------------------------------------------------------- #
def _blind(out):
    """(hosts-reporting, scopes, unmeasured, escalated) off the rc-18 summary.

    🔴 THE QUADRUPLE IS THE CLAIM, never `escalated=0` alone: a ladder walked
    over no hosts, or over no scopes, prints exactly that zero. Raises when the
    line is absent, because an absent summary and a clean one are the same value
    to a comparison and only one of them is good news."""
    m = re.search(r"\[srcblind\] hosts-reporting=(\d+) scopes=(\d+) "
                  r"unmeasured=(\d+) escalated=(\d+)", out)
    assert m, ("no hosts-reporting/scopes/unmeasured/escalated line — the "
               "quadruple IS the claim:\n" + out)
    return tuple(int(g) for g in m.groups())


def _no_upstream(fleet, clone, branch="docs/tui-rendering-footguns"):
    """Park the source clone on a local branch with NO upstream.

    🔴 THE MEASURED SHAPE, not a synonym for it. A detached HEAD reaches the same
    NOUPSTREAM branch in the checker, but the live instance was a NAMED branch
    somebody was working on — the state that looks completely normal in
    `git status` and that a human will leave in place for days.
    """
    fleet.git(clone, "checkout", "-q", "-b", branch)


def _blind_state_files(fleet):
    return sorted(p.name for p in fleet.state.glob("unmeasured-*"))


# --- the softening, in both directions -------------------------------------- #
def test_a_scope_with_no_upstream_below_the_threshold_does_not_fail_the_unit(fleet):
    """🔴 A SCRATCH BRANCH FOR AN AFTERNOON MUST NOT LOOK LIKE DRIFT.

    These are working repos and parking one on an unpushed branch is normal, so
    a code that fired on the first run would be red most of the time — the
    permanently-red gate this file refuses everywhere else. Reported loudly,
    exit code unaffected.

    RED AT BASE (15da9908) on the streak line: base reports the scope as
    unmeasured and then says nothing further, forever. The rc==0 half is an
    INVARIANT GUARD — base is green on it too — and is asserted here only
    because "not escalated" is meaningless without it.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)

    rc, out = fleet.check("--no-remote")
    assert rc == 0, f"one unmeasured run must not fail the unit, got {rc}\n{out}"
    assert "tmux-fuzzyclaw: UNMEASURED (NOUPSTREAM) — 1/4 consecutive" in out, out
    assert "NOT escalated" in out, out
    assert "Still not a pass" in out, out          # and not sold as a green
    assert _blind(out) == (1, 1, 1, 0), out


def test_a_scope_with_no_upstream_escalates_after_n_consecutive_runs(fleet):
    """🔴 THE OTHER DIRECTION: the softening must not make the deadman mute.

    A scope whose currency has been unevaluable for the whole threshold window is
    no longer "somebody is mid-branch" — it is a scope rc 17 CANNOT fire for, so
    a stale built source there is invisible. At that point not alerting is the
    deadman failing at its one job.

    RED AT BASE (15da9908): the ladder there is [0, 0, 0, 0] — base never
    escalates, which is the whole defect.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)

    codes = []
    for _ in range(4):
        rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3")
        codes.append(rc)
    assert codes == [0, 0, 18, 18], f"escalation ladder wrong: {codes}\n{out}"
    # `out` is the FOURTH run's, so the streak reads 4 over a threshold of 3 —
    # the ladder keeps counting past the threshold rather than latching, which is
    # what makes "how long has this been true" legible in the journal.
    assert ("workbench tmux-fuzzyclaw: UNMEASURED (NOUPSTREAM) for 4 CONSECUTIVE "
            "runs (threshold 3)." in out), out
    assert "rc 17 CANNOT fire for it" in out, out
    assert _blind(out) == (1, 1, 1, 1), out


def test_the_unmeasured_streak_resets_when_the_scope_measures(fleet):
    """The streak counts CONSECUTIVE runs. One real measurement clears it —
    otherwise a repo that is merely branchy every other day escalates anyway and
    the threshold means nothing.

    RED AT BASE (15da9908): no ladder exists there at all.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3")
        assert rc == 0, out
    assert "2/3 consecutive" in out, out

    fleet.git(clone, "checkout", "-q", "main")      # back on a tracked branch
    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3")
    assert rc == 0, out
    assert _blind(out) == (1, 1, 0, 0), out         # measured: nothing unmeasured

    _no_upstream(fleet, clone, branch="docs/again")  # ...and it goes away again
    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3")
    assert rc == 0, f"the streak did not reset on a real measurement\n{out}"
    assert "1/3 consecutive" in out, out


def test_a_measured_fleet_reports_a_zero_over_a_REAL_denominator(fleet):
    """🔴 POSITIVE CONTROL for the quadruple, and the partner of every red above.

    `escalated=0` is the output of a ladder wired to nothing. It is only evidence
    beside a non-zero `scopes=`: one scope was found, walked, and measured.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    _src_repo(fleet, "tmux-fuzzyclaw")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    assert _blind(out) == (1, 1, 0, 0), out
    blind_lines = [ln for ln in out.splitlines() if ln.startswith("[srcblind]")]
    assert not [ln for ln in blind_lines if "UNMEASURED" in ln], blind_lines


# --- the reasons are not one hazard ----------------------------------------- #
def test_an_ABSENT_source_repo_never_escalates_however_many_runs(fleet):
    """🔴 THE ONE EXEMPTION, and it must hold at every count.

    `clawgatectl.nix` deliberately supports a host without the checkout: the
    derivation guards on pathExists and omits the binary. Escalating on absence
    would make that host permanently red for a package it correctly does not
    ship — which is worse than no gate.

    Driven at threshold 1, so a single shared counter would fire on run one.

    RED AT BASE (15da9908) on the NEVER-escalates line and the counter-file
    assertion. The rc==0 half is an INVARIANT GUARD — base never escalates
    anything — and is the property that must survive, not new coverage.
    """
    fleet.catch_up()
    _nixpkg(fleet, "clawgatectl.nix", "homelab-talos/containers/clawgate")

    for _ in range(5):
        rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="1",
                              DRIFT_UNMEASURED_FETCH_ESCALATE="1")
        assert rc == 0, f"an absent source repo escalated: {rc}\n{out}"
    assert "UNMEASURED (ABSENT) — reported, and it NEVER escalates" in out, out
    assert _blind(out) == (1, 1, 1, 0), out
    assert _blind_state_files(fleet) == [], (
        "an ABSENT scope consulted the counter at all: %r"
        % _blind_state_files(fleet))


def test_a_FAILED_FETCH_takes_its_own_LONGER_ladder(fleet):
    """🔴 THE REASONS DO NOT SHARE A COUNTER, and this is the pair that proves it.

    A failed fetch has a plausibly transient cause (no ssh-agent under a user
    unit, a key rotation, a remote that is down), so it gets more patience than
    the structural reasons. Same fixture, both directions:

      * 5 consecutive runs at the STRUCTURAL threshold of 4 -> still rc 0, i.e.
        it is genuinely not on that ladder rather than merely slower;
      * the same shape with its OWN threshold at 2 -> rc 18, i.e. it does still
        escalate. A reason that can never escalate is the defect, one costume on.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    fleet.git(clone, "remote", "set-url", "origin",
              str(fleet.root / "definitely-not-a-repo"))

    for _ in range(5):
        rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="4")
        assert rc == 0, f"a failed fetch took the structural ladder: {rc}\n{out}"
    assert "UNMEASURED (FETCHFAILED) — 5/12 consecutive" in out, out

    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_FETCH_ESCALATE="2")
    assert rc == 18, f"a persistently failing fetch never escalates: {rc}\n{out}"
    assert ("workbench tmux-fuzzyclaw: UNMEASURED (FETCHFAILED) for 6 CONSECUTIVE "
            "runs (threshold 2)." in out), out


def test_a_CHANGED_reason_restarts_the_ladder(fleet):
    """The two ladders have different thresholds, so a FETCHFAILED streak must
    not be spent on a NOUPSTREAM escalation — that would escalate on evidence
    that was never about this hazard.

    Fetch first (it runs before the upstream lookup), then the fetch is repaired
    and the branch is left untracked: at threshold 3 a carried-over count would
    be 3 and fire on that very run.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    good = fleet.git(clone, "remote", "get-url", "origin")
    fleet.git(clone, "remote", "set-url", "origin",
              str(fleet.root / "definitely-not-a-repo"))
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3",
                              DRIFT_UNMEASURED_FETCH_ESCALATE="3")
        assert rc == 0, out
    assert "(FETCHFAILED) — 2/3 consecutive" in out, out

    fleet.git(clone, "remote", "set-url", "origin", good)
    _no_upstream(fleet, clone)
    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3",
                          DRIFT_UNMEASURED_FETCH_ESCALATE="3")
    assert rc == 0, f"a FETCHFAILED streak was spent on a NOUPSTREAM ladder\n{out}"
    assert "(NOUPSTREAM) — 1/3 consecutive" in out, out


# --- per (HOST, SCOPE), never per run --------------------------------------- #
def test_the_unmeasured_streak_is_kept_PER_SCOPE(fleet):
    """🔴 TWO SCOPES ARE TWO LADDERS. Keyed per HOST alone, two unmeasured scopes
    would bump one counter twice a run and reach a threshold of 4 in two runs —
    an escalation manufactured by counting, on a fleet where nothing has been
    unevaluable for long enough to matter.

    Both directions in one fixture: two runs must stay green (no double
    counting), and the same pair must still escalate on its own ladder at four.
    """
    fleet.catch_up()
    _nixpkg(fleet, "a.nix", "homelab-talos/containers/clawgate")
    _nixpkg(fleet, "b.nix", "tmux-fuzzyclaw")
    c1, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    c2, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, c1)
    _no_upstream(fleet, c2)

    for _ in range(2):
        rc, out = fleet.check("--no-remote")
        assert rc == 0, f"two scopes shared one counter: {rc}\n{out}"
    assert _blind(out) == (1, 2, 2, 0), out
    assert "homelab-talos/containers/clawgate: UNMEASURED (NOUPSTREAM) — 2/4" in out, out
    assert "tmux-fuzzyclaw: UNMEASURED (NOUPSTREAM) — 2/4" in out, out

    for _ in range(2):
        rc, out = fleet.check("--no-remote")
    assert rc == 18, f"per-scope ladders never reach the threshold: {rc}\n{out}"
    assert _blind(out) == (1, 2, 2, 2), out


def test_one_scope_recovering_does_not_reset_ANOTHER_scopes_ladder(fleet):
    """The complement of the test above, and the direction a shared counter
    breaks the other way: the repo that recovered would clear the ladder of the
    repo that did not, and the stuck one would never escalate."""
    fleet.catch_up()
    _nixpkg(fleet, "a.nix", "homelab-talos/containers/clawgate")
    _nixpkg(fleet, "b.nix", "tmux-fuzzyclaw")
    c1, _ = _src_repo(fleet, "homelab-talos", branch="trunk")
    c2, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, c1)
    _no_upstream(fleet, c2)

    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3")
        assert rc == 0, out

    fleet.git(c2, "checkout", "-q", "main")          # one of the two recovers
    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="3")
    assert rc == 18, (
        "the recovered scope reset the stuck scope's ladder: %d\n%s" % (rc, out))
    assert "homelab-talos/containers/clawgate: UNMEASURED (NOUPSTREAM) for 3 CONSECUTIVE" in out, out
    assert _blind(out) == (1, 2, 1, 1), out


def test_the_scope_counter_filename_is_INJECTIVE(fleet):
    """🔴 `a/b` AND `a_b` MUST NOT SHARE A FILE. The scope alphabet includes both
    `/` and `_`, so the obvious `/`->`_` sanitisation collides them — and two
    scopes sharing a counter is a ladder that double-counts on one run and resets
    itself on another, for reasons nothing in the output can explain.

    Behavioural, not a filename assertion alone: at threshold 2 a collision
    reaches 2 on the FIRST run and escalates. Both spellings are checked to exist
    separately as well, because a green rc alone would not say which property
    held.
    """
    fleet.catch_up()
    _nixpkg(fleet, "a.nix", "talos/containers")
    _nixpkg(fleet, "b.nix", "talos_containers")
    c1, _ = _src_repo(fleet, "talos")
    c2, _ = _src_repo(fleet, "talos_containers")
    _no_upstream(fleet, c1)
    _no_upstream(fleet, c2)

    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="2")
    assert rc == 0, f"two scopes collided onto one counter: {rc}\n{out}"
    assert _blind(out) == (1, 2, 2, 0), out
    assert _blind_state_files(fleet) == [
        "unmeasured-workbench-talos__containers",
        "unmeasured-workbench-talos_containers",
    ], _blind_state_files(fleet)


def test_the_unmeasured_streak_is_kept_PER_HOST(fleet):
    """One host's blindness is not the other's. The remote runs the SAME payload
    against its own $HOME, with its clone parked on an untracked branch while the
    local clone is current: only the remote's scope may have a ladder."""
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    lstore = _mkhome(fleet.home, healthy=1)
    _src_repo(fleet, "tmux-fuzzyclaw")

    rhome = fleet.root / "remote-home"
    rhome.mkdir()
    rstore = _mkhome(rhome, healthy=1, store=fleet.root / "remote-store")
    rclone, _ = _src_repo(fleet, "tmux-fuzzyclaw", home=rhome)
    _no_upstream(fleet, rclone)
    _remote_running_the_real_payload(fleet, rhome, rstore)

    rc, out = _parity(fleet, store=lstore, REMOTE_SSH="stub@example.invalid")
    assert rc == 0, out
    assert "laptop tmux-fuzzyclaw: UNMEASURED (NOUPSTREAM) — 1/4" in out, out
    assert "workbench tmux-fuzzyclaw: UNMEASURED" not in out, out
    assert _blind(out) == (2, 2, 1, 0), out
    assert _blind_state_files(fleet) == ["unmeasured-laptop-tmux-fuzzyclaw"], (
        _blind_state_files(fleet))


def test_a_host_that_never_ANSWERED_accumulates_no_ladder(fleet):
    """🔴 A HOST NOBODY LOOKED AT MUST NOT ACQUIRE A STREAK. An unreachable
    laptop reports no scopes at all, and rc 13 already owns that finding; bumping
    a scope ladder for it would escalate a second code off one missed ssh, and
    would go on doing it while the machine is simply shut.

    RED AT BASE (15da9908) on `_blind()` (no such summary exists there). The
    rc==0 half is an INVARIANT GUARD."""
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    _src_repo(fleet, "tmux-fuzzyclaw")
    fleet.stub_ssh(255)

    for _ in range(5):
        rc, out = fleet.check(REMOTE_SSH="stub@example.invalid",
                              DRIFT_UNMEASURED_ESCALATE="2",
                              DRIFT_UNREACHABLE_ESCALATE="99")
        assert rc == 0, f"an unreachable host grew a scope ladder: {rc}\n{out}"
    assert _blind(out) == (1, 1, 0, 0), out          # only the local host walked
    assert _blind_state_files(fleet) == [], _blind_state_files(fleet)


# --- it cannot be satisfied by measuring nothing ---------------------------- #
def test_a_ladder_over_ZERO_SCOPES_says_so_instead_of_printing_a_clean_line(fleet):
    """🔴 `escalated=0` over no scopes is a checker wired to nothing wearing the
    output of a clean fleet. Both refusals are asserted: the per-host line naming
    WHY that host contributed nothing, and the withheld summary."""
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    blk = out.split("=== built-source scopes")[1]
    assert "workbench: NOT EVALUATED" in blk, blk
    assert "named no ${workspace}/" in blk, blk
    assert "named ZERO" in blk, blk
    assert "hosts-reporting=" not in blk, (
        "a ladder over zero scopes printed a clean-looking summary:\n" + blk)


def test_a_ladder_over_ZERO_HOSTS_says_so_instead_of_printing_a_clean_line(fleet):
    """The same refusal one level up: a run that reached no host has no scopes to
    have measured, and must not print a summary at all.

    RED AT BASE (15da9908) on the block assertions; the rc==2 half is an
    INVARIANT GUARD — that refusal predates this change."""
    fleet.stub_ssh(255)
    rc, out = fleet.check("--no-local", REMOTE_SSH="stub@example.invalid")
    assert rc == 2, out                              # the existing checked-nothing rc
    blk = out.split("=== built-source scopes")[1]
    assert "no host returned a src-unmeasured fact set" in blk, blk
    assert "hosts-reporting=" not in blk, blk


def test_the_ladder_escalates_immediately_when_the_streak_cannot_be_persisted(fleet):
    """If 'for how long' is unknowable the run must fail CLOSED, exactly as the
    unreachable ladder does: a state dir that cannot be created is the one case
    where the threshold logic has no input at all, and going quiet there is an
    unbounded silent window."""
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)
    blocked = fleet.root / "blocked-state"
    blocked.write_text("not a directory\n")

    rc, out = fleet.check("--no-remote", DRIFT_STATE_DIR=str(blocked))
    assert rc == 18, f"expected an immediate escalation, got {rc}\n{out}"
    assert "could not be persisted" in out, out
    assert _blind(out) == (1, 1, 1, 1), out


@pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores directory write permissions"
)
def test_the_ladder_escalates_when_the_streak_FILE_cannot_be_written(fleet):
    """🔴 THE SECOND FAIL-CLOSED LIMB, and the one the test above does NOT reach
    — the identical blind spot the rc 13 ladder had.

    `test_the_ladder_escalates_immediately_when_the_streak_cannot_be_persisted`
    points DRIFT_STATE_DIR at a regular FILE, so `mkdir -p` fails and
    u_streak_bump returns from its FIRST limb. The `printf … > "$f" || echo -1`
    limb — an existing, readable, but UNWRITABLE state dir — is only reached
    here, and mutating its `echo -1` to `echo 0` would otherwise leave the whole
    suite green while the ladder went permanently mute: 0 is below every
    threshold, forever.

    BOTH permission changes are required, for the reason the rc 13 twin records:
    a mode-500 directory does not stop a write to a file that already EXISTS
    inside it, so without the read-only file this passes on the mutant and is a
    vacuous guard.
    """
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)

    ro = fleet.root / "readonly-state"
    ro.mkdir()
    counter = ro / "unmeasured-workbench-tmux-fuzzyclaw"
    counter.write_text("NOUPSTREAM 2\n")             # a streak already in flight
    counter.chmod(0o400)
    ro.chmod(0o500)
    try:
        rc, out = fleet.check("--no-remote", DRIFT_STATE_DIR=str(ro))
    finally:
        ro.chmod(0o700)                              # so tmp_path can be cleaned
        counter.chmod(0o600)
    assert rc == 18, (
        "an unpersistable streak must escalate immediately — 'for how long' is "
        f"unknowable, so going quiet is an unbounded silent window. got {rc}\n{out}"
    )
    assert "could not be persisted" in out, out
    # ...and via the UNKNOWN-streak branch, not the threshold one: the counter on
    # disk says 2, below the default threshold of 4. Without this the test would
    # pass on a mutant that never reaches the write limb at all.
    assert "CONSECUTIVE runs" not in out, (
        "escalated through the threshold branch, so the cannot-persist limb was "
        "never exercised\n" + out)
    # The same journal-hygiene claim the rc 13 twin makes: `2>/dev/null > "$f"`
    # (in that order) is what keeps the shell's own redirection error out of the
    # journal, and it is only observable on this path.
    stray = [
        ln for ln in out.splitlines()
        if ln.strip() and not ln.startswith(("[", "===", "drift-check: ", "  "))
    ]
    assert stray == [], (
        "unprefixed output leaked into the journal between the [host] lines: %r" % stray
    )


# --- it must not outrank, or be outranked by, the wrong thing --------------- #
def test_unpushed_devrc_commits_still_outrank_an_escalated_rc18(fleet):
    """rc 8 is the code with the rescue-before-reset procedure and the one the
    legend is read against. A scope nobody could measure must never displace it.

    RED AT BASE (15da9908) only on the escalation line: base cannot produce an
    rc 18 to be outranked, so the rc==8 half is an INVARIANT GUARD. It is the
    property that must not break, not evidence of a fixed bug."""
    fleet.catch_up()
    fleet.add_local_commit("un-pushed while a source scope is unmeasurable")
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)

    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="1")
    assert rc == 8, f"an escalated rc 18 displaced rc 8: {rc}\n{out}"
    assert "CONSECUTIVE runs" in out, out             # both are still reported


def test_an_escalated_rc18_outranks_a_merely_BEHIND_host(fleet):
    """The other side of the rank: 18 sits above 10 (and 12 and 15) because a
    scope that cannot be measured hides an rc 17 indefinitely, while a behind
    checkout is one `ship.sh` away and says exactly what it is."""
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")   # fleet is 1 BEHIND
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)

    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="1")
    assert rc == 18, f"rc 18 lost to a behind checkout: {rc}\n{out}"
    assert "is BEHIND origin/main" in out, out        # both are still reported


def test_a_MEASURED_stale_source_still_outranks_an_unmeasurable_one(fleet):
    """17 over 18: "we looked and it is wrong" beats "we could not look", the
    same argument severity() already makes for 14 over 13.

    RED AT BASE (15da9908) only on the escalation line — base has no rc 18 — so
    the rc==17 half is an INVARIANT GUARD."""
    fleet.catch_up()
    _nixpkg(fleet, "a.nix", "homelab-talos/containers/clawgate")
    _nixpkg(fleet, "b.nix", "tmux-fuzzyclaw")
    _, b1 = _src_repo(fleet, "homelab-talos", branch="trunk")
    _push_upstream(fleet, b1, branch="trunk", path="containers/clawgate/main.go")
    c2, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, c2)

    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="1")
    assert rc == 17, f"an unmeasurable scope displaced a measured one: {rc}\n{out}"
    assert "CONSECUTIVE runs" in out, out


def test_the_rc18_legend_is_printed_with_the_verdict(fleet):
    """The journal is the only surface this unit has, so the code must arrive
    with its meaning — including that ABSENT is not on the ladder."""
    fleet.catch_up()
    _nixpkg(fleet, "tmux-fuzzyclaw.nix", "tmux-fuzzyclaw")
    clone, _ = _src_repo(fleet, "tmux-fuzzyclaw")
    _no_upstream(fleet, clone)

    rc, out = fleet.check("--no-remote", DRIFT_UNMEASURED_ESCALATE="1")
    assert rc == 18, out
    assert "drift-check: DRIFT (rc=18)" in out, out
    assert "rc18=a built-source scope has been UNMEASURABLE" in out, out
    assert "repo ABSENT never" in out, out


@pytest.mark.parametrize("var", ["DRIFT_UNMEASURED_ESCALATE",
                                 "DRIFT_UNMEASURED_FETCH_ESCALATE"])
def test_a_non_integer_unmeasured_threshold_is_rejected(fleet, var):
    """Both are compared with `-ge`, where a non-integer is a shell ERROR rather
    than a false — and an erroring comparison is a ladder that goes quiet, which
    is the direction this whole code exists to refuse."""
    rc, out = fleet.check("--no-remote", **{var: "4; touch /tmp/pwned"})
    assert rc == 2, out
    assert "must be a non-negative integer" in out, out


# --------------------------------------------------------------------------- #
# THE TWO RC LADDERS
#
# 🔴 drift-check.sh and ship.sh publish ONE numbering between them, and until
# 2026-08-21 that fact lived only in a PR description. Neither file named a
# single one of the other's codes, while this file's own header said "a new DRIFT
# code has nowhere to go but upward" — pointing the next one straight at 19,
# which ship.sh had just taken for hosts-disagree. A collision one increment
# away, invisible to every test in either suite.
# --------------------------------------------------------------------------- #
def _codes_ship_can_return():
    """ship.sh's non-zero statuses: `exit N` and `rc=N` outside comments.

    The same two spellings ship.sh's own ledger test uses — it writes rc 19 only
    as an assignment, never as `exit 19`.
    """
    code = "\n".join(
        ln for ln in SHIP.read_text().splitlines() if not ln.strip().startswith("#")
    )
    c = {int(m) for m in re.findall(r"\bexit (\d+)", code)}
    c |= {int(m) for m in re.findall(r"\b[a-z_]*rc=(\d+)", code)}
    c.discard(0)
    return c


def _codes_drift_can_return():
    """drift-check.sh's codes, read off severity()'s case labels.

    Not a grep for assignments: this script sets its codes through several
    per-scope variables (`p_rc`, `s_rc`, streak ladders) and an assignment scan
    misses 13, 16 and 18. severity() is the authoritative table — the header says
    so ("its rank is stated in severity() and here") — and every code that can be
    returned has to be ranked there or it falls into the unknown-code slot.
    """
    body = DRIFT.read_text().split("severity() {", 1)[1].split("\n}", 1)[0]
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    c = {int(m) for m in re.findall(r"^\s*(\d+)\)", body, re.M)}
    c.discard(0)
    # rc 2 exits directly, before any per-host leg, so it is never ranked.
    c.add(2)
    return c


def _reserved_ledger(path, tag):
    """The `# RESERVED-TO-<X>: n n n` line out of a script's header."""
    m = re.search(rf"^#\s*RESERVED-TO-{tag}:\s*([0-9 ]+)$", path.read_text(), re.M)
    assert m, (
        f"{path.name} has no `# RESERVED-TO-{tag}:` ledger line — the reciprocal "
        f"reservation is back to living outside both files"
    )
    return {int(n) for n in m.group(1).split()}


def test_the_two_rc_ladders_reserve_each_others_codes():
    """🔴 A LEDGER of the shared numbering, failing when either side moves.

    Both halves are asserted as SETS, so this goes red when a code is added on
    either side without being reserved on the other — not merely when 19 or 20 is
    taken. And the "next free code" both headers publish is DERIVED here from the
    two measured sets, so a stale number in the prose is a failure rather than a
    thing a reader has to notice.
    """
    ship_codes = _codes_ship_can_return()
    drift_codes = _codes_drift_can_return()

    # 🔴 POSITIVE CONTROL for both parsers. A ledger computed from an empty set
    # is satisfied by an empty reservation line, in silence.
    assert {2, 19, 20} <= ship_codes, (
        f"the ship.sh parser is reading almost nothing: {sorted(ship_codes)}"
    )
    assert {8, 10, 14, 18} <= drift_codes, (
        f"the drift-check.sh parser is reading almost nothing: {sorted(drift_codes)}"
    )

    ship_only = ship_codes - drift_codes
    drift_only = drift_codes - ship_codes

    assert _reserved_ledger(SHIP, "DRIFT-CHECK") == drift_only, (
        f"ship.sh reserves {sorted(_reserved_ledger(SHIP, 'DRIFT-CHECK'))} to "
        f"drift-check.sh, but drift-check.sh alone can return "
        f"{sorted(drift_only)}. A code missing from that ledger is a code ship.sh "
        f"may take next."
    )
    assert _reserved_ledger(DRIFT, "SHIP") == ship_only, (
        f"drift-check.sh reserves {sorted(_reserved_ledger(DRIFT, 'SHIP'))} to "
        f"ship.sh, but ship.sh alone can return {sorted(ship_only)}."
    )

    # ...and neither script actually emits a code it has reserved to the other.
    assert not (drift_codes & _reserved_ledger(DRIFT, "SHIP")), (
        "drift-check.sh returns a code it reserves to ship.sh"
    )
    assert not (ship_codes & _reserved_ledger(SHIP, "DRIFT-CHECK")), (
        "ship.sh returns a code it reserves to drift-check.sh"
    )

    next_free = max(ship_codes | drift_codes) + 1
    for path in (SHIP, DRIFT):
        header = path.read_text().split("\n{\n", 1)[0].split("\nset -", 1)[0]
        assert re.search(rf"next free[^.]*\b{next_free}\b", header), (
            f"{path.name}'s header does not publish {next_free} as the next free "
            f"code. The two ladders now reach {max(ship_codes | drift_codes)}, so "
            f"'nowhere to go but upward' points at a number that is already taken."
        )


# --------------------------------------------------------------------------- #
# SKILL-LISTING TIERS (rc 22)
#
# `claude/skill-tiers.json` decides which skills spend always-on listing budget
# on a full description and which ship as `name-only`. That ledger is in git;
# `~/.claude/settings.json` is per-host and unmanaged, so nothing keeps them
# together except this arm and `scripts/sync-skill-tiers.py`.
#
# 🔴 THE DESIGN DECISION THESE TESTS EXIST TO PIN: a host with NO skillOverrides
# is NOT ADOPTED, not drift, and sets NO rc. The mechanism shipped applied to
# zero hosts — deliberately, because nothing is being truncated today — so
# counting an unapplied host as drift would have made this code RED ON EVERY RUN
# from the moment it landed, and `claude/RULES.md` is explicit that a
# permanently-red gate is worse than no gate. rc 22 fires only on
# adopted-then-drifted.
#
# The comparison is NOT cross-host: both machines can agree perfectly and both be
# wrong. The reference is the ledger.
# --------------------------------------------------------------------------- #

TIER_LEDGER_FIXTURE = {
    "skills": {
        "alpha": {"tier": "B", "why": "a fixture rationale, long enough to pass"},
        "beta": {"tier": "B", "why": "a second fixture rationale, also long"},
        "gamma": {"tier": "A"},
    }
}
TIER_WANT = {"alpha": "name-only", "beta": "name-only"}


def _tier_ledger(tmp_path, body=None):
    p = tmp_path / "skill-tiers.json"
    p.write_text(json.dumps(body if body is not None else TIER_LEDGER_FIXTURE,
                            indent=2), encoding="utf-8")
    return p


def _settings_with(home, overrides, extra=None):
    """Write a fixture ~/.claude/settings.json in the SHAPE Claude Code writes:
    2-space top-level keys, 4-space entries, `json.dumps(indent=2)`. The
    extractor under test is a sed with a format dependency, so a fixture built
    any other way would be testing a format nobody ships."""
    claude = home / ".claude"
    claude.mkdir(parents=True, exist_ok=True)
    body = {"theme": "dark"}
    if overrides is not None:
        body["skillOverrides"] = overrides
    body.update(extra or {})
    (claude / "settings.json").write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8")


def _tiers_block(out):
    """Just the [tiers] lines, so an assertion cannot be satisfied by a word
    that appears in some other arm's output."""
    return "\n".join(ln for ln in out.splitlines() if ln.startswith("[tiers]"))


def _tier_check(fleet, ledger, *args, **env):
    return fleet.check("--no-remote", *args,
                       DRIFT_TIER_LEDGER=str(ledger), **env)


def test_a_host_with_no_skill_overrides_is_NOT_ADOPTED_not_drift(fleet, tmp_path):
    """🔴 THE PERMANENTLY-RED-GATE GUARD, and the shipped state of the fleet.

    Neither host had skillOverrides when this landed. If that counted as drift
    the deadman would fail on every run forever, training the operator to click
    through the one alert that has to keep its meaning.
    """
    fleet.catch_up()
    _settings_with(fleet.home, None)
    rc, out = _tier_check(fleet, _tier_ledger(tmp_path))
    block = _tiers_block(out)
    assert rc == 0, f"an unadopted host was reported as drift (rc {rc})\n{out}"
    assert "NOT ADOPTED" in block, block
    assert "DRIFT" not in block, block
    assert "sync-skill-tiers.py --apply" in block, "no fix is offered\n" + block
    assert "matches the ledger" not in block, (
        "an unadopted host must not read as compliant\n" + block
    )


def test_a_host_matching_the_ledger_is_green(fleet, tmp_path):
    """🔴 POSITIVE CONTROL. Report this ALONGSIDE the reds below: a green from an
    arm wired to nothing looks exactly like this one, so the compliant case must
    be shown to produce the AFFIRMATIVE line and not merely the absence of a
    complaint."""
    fleet.catch_up()
    _settings_with(fleet.home, dict(TIER_WANT))
    rc, out = _tier_check(fleet, _tier_ledger(tmp_path))
    block = _tiers_block(out)
    assert rc == 0, out
    assert "matches the ledger (2 tier-B override(s) deployed as asked)" in block, block
    assert "NOT ADOPTED" not in block, block


def test_a_missing_ledger_entry_on_an_adopted_host_is_rc22(fleet, tmp_path):
    """NEGATIVE CONTROL 1: the ledger gained an entry and nobody re-applied it.
    Isolated — the host carries a VALID override for the other skill, so this can
    only fail on the one that is absent."""
    fleet.catch_up()
    _settings_with(fleet.home, {"alpha": "name-only"})
    rc, out = _tier_check(fleet, _tier_ledger(tmp_path))
    block = _tiers_block(out)
    assert rc == 22, f"expected rc 22, got {rc}\n{out}"
    assert "in the ledger, NOT on the host: beta=name-only" in block, block
    assert "alpha" not in block.split("NOT on the host:")[1].splitlines()[0], block


def test_a_wrong_override_value_is_rc22(fleet, tmp_path):
    """NEGATIVE CONTROL 2: the skill IS overridden, at a value the ledger does
    not ask for. Reported as its own category — `off` hides the skill entirely,
    which is a different fault from a missing entry and must not read the same."""
    fleet.catch_up()
    _settings_with(fleet.home, {"alpha": "off", "beta": "name-only"})
    rc, out = _tier_check(fleet, _tier_ledger(tmp_path))
    block = _tiers_block(out)
    assert rc == 22, out
    assert "DIFFERENT value than the ledger asks for: alpha=name-only" in block, block
    assert "NOT on the host" not in block, block


def test_an_override_the_ledger_does_not_name_is_rc22(fleet, tmp_path):
    """NEGATIVE CONTROL 3: a hand-edit, or an override left behind by a retired
    skill. The ledger is the reference in BOTH directions or it is not a
    ledger."""
    fleet.catch_up()
    _settings_with(fleet.home, dict(TIER_WANT, delta="off"))
    rc, out = _tier_check(fleet, _tier_ledger(tmp_path))
    block = _tiers_block(out)
    assert rc == 22, out
    assert "NOT in the ledger (hand-edited, or a retired skill): delta=off" in block, block


def test_rc22_names_the_fix_and_the_ordering_against_a_behind_host(fleet, tmp_path):
    """A host reported BEHIND carries a STALE ledger, so this finding can be a
    symptom of that one. The output has to say which to do first, or an operator
    re-applies a ledger that is about to change under them."""
    fleet.catch_up()
    _settings_with(fleet.home, {"alpha": "name-only"})
    _, out = _tier_check(fleet, _tier_ledger(tmp_path))
    block = _tiers_block(out)
    assert "sync-skill-tiers.py" in block, block
    assert "ship it FIRST" in block, block


def test_an_unusable_ledger_is_COULD_NOT_MEASURE_and_sets_no_rc(fleet, tmp_path):
    """🔴 The reassuring zero this arm refuses. An unreadable ledger yields an
    EMPTY expectation, and an empty expectation makes every host look compliant.
    It must print COULD NOT MEASURE, set no rc, and never say `matches`."""
    fleet.catch_up()
    _settings_with(fleet.home, dict(TIER_WANT))
    rc, out = _tier_check(fleet, tmp_path / "no-such-ledger.json")
    block = _tiers_block(out)
    assert rc == 0, out
    assert "COULD NOT MEASURE" in block, block
    assert "matches the ledger" not in block, block
    assert "NOT ADOPTED" not in block, block

    bad = tmp_path / "broken.json"
    bad.write_text("{ not json", encoding="utf-8")
    rc, out = _tier_check(fleet, bad)
    assert rc == 0, out
    assert "COULD NOT MEASURE" in _tiers_block(out), out


def test_a_host_that_did_not_report_the_fact_is_not_reported_as_matching(
        fleet, tmp_path):
    """A host that produced no fact must never print like one that matches.

    🔴 The reason is NOT "the far side runs an older drift-check.sh" — it cannot.
    `PAYLOAD` is composed HERE and piped to `bash -s`, so the remote always runs
    THIS script's payload. The real causes are a host that was not reached and a
    stream cut short before the FACT lines, and the stub is the second shape: a
    remote that answers cleanly, reports the earlier facts, and stops.
    """
    fleet.catch_up()
    _settings_with(fleet.home, dict(TIER_WANT))
    fleet.stub_ssh(0, stdout=("[laptop] clean\n"
                              "[laptop] FACT settings-keys theme\n"
                              "[laptop] PARITY-RC=0"))
    rc, out = fleet.check(DRIFT_TIER_LEDGER=str(_tier_ledger(tmp_path)))
    block = _tiers_block(out)
    assert "laptop: NOT REPORTED" in block, block
    assert "laptop: matches the ledger" not in block, block


def test_the_ledger_the_arm_defaults_to_is_the_repos_own(fleet):
    """With no override the arm reads `claude/skill-tiers.json` beside the script
    — the copy that ships WITH this checker, so the two cannot be a version
    apart. Measured through the live ledger's real tier-B count."""
    fleet.catch_up()
    _settings_with(fleet.home, None)
    rc, out = fleet.check("--no-remote")
    block = _tiers_block(out)
    n = len(json.loads((REPO_ROOT / "claude" / "skill-tiers.json").read_text()
                       )["skills"])
    n_b = sum(1 for e in json.loads(
        (REPO_ROOT / "claude" / "skill-tiers.json").read_text()
    )["skills"].values() if e["tier"] == "B")
    assert n >= 30, f"the live ledger has only {n} entries — is it being read?"
    assert f"ledger asks for {n_b} name-only override(s)" in block, block


# --- the EXTRACTOR, driven directly ---------------------------------------- #

def _parity_fact(home, name="skill-overrides"):
    """Run the real PARITY payload against a fixture $HOME and return one fact."""
    proc = subprocess.run(
        ["bash", "-c", _payload_literal("PARITY")],
        capture_output=True, text=True,
        env={"HOME": str(home), "PATH": os.environ.get("PATH", ""),
             "DRIFT_LABEL": "fx", "DRIFT_PARITY_ROOTS": "no/such/dir"},
    )
    m = re.search(r"^\[fx\] FACT %s (.*)$" % re.escape(name), proc.stdout, re.M)
    assert m, f"no `{name}` fact in the payload output:\n{proc.stdout}{proc.stderr}"
    return m.group(1).strip()


def test_the_extractor_can_actually_see_overrides(tmp_path):
    """🔴 POSITIVE CONTROL FOR THE EXTRACTOR. Every other assertion about this
    fact is a NONE / EMPTY / mismatch, and a sed that matches nothing produces
    those too. It has to be shown reading real values out of the real shape."""
    home = tmp_path / "h"
    _settings_with(home, {"zeta": "name-only", "alpha": "off"})
    assert _parity_fact(home) == "alpha=off zeta=name-only"


def test_an_absent_key_is_NONE_and_an_unreadable_file_is_UNEVALUATED(tmp_path):
    """Three states that must never collapse into one another."""
    home = tmp_path / "h"
    _settings_with(home, None)
    assert _parity_fact(home) == "NONE"

    empty = tmp_path / "e"
    (empty / ".claude").mkdir(parents=True)
    assert _parity_fact(empty) == "UNEVALUATED"


def test_an_empty_overrides_object_does_not_swallow_the_following_key(tmp_path):
    """🔴 ISOLATED MUTATION GUARD on the sed RANGE END, and the reason it is
    `^  [}"]` rather than `^  }`.

    `"skillOverrides": {},` on ONE line still matches the range START (the
    pattern is an unanchored substring), so with `^  }` as the end the range runs
    on through every FOLLOWING key and harvests their 4-space entries as
    overrides. The fixture puts a nested object with entries of exactly that
    shape immediately after, so a widened range would report them — and reporting
    them would then be diffed against the ledger and called drift on a host that
    has no overrides at all.
    """
    home = tmp_path / "h"
    _settings_with(home, {}, extra={"statusLine": {"padStart": "yes",
                                                   "command": "bar-status"}})
    fact = _parity_fact(home)
    assert fact == "EMPTY", fact
    assert "padStart" not in fact and "command" not in fact, fact


def test_a_populated_block_followed_by_another_key_stops_at_the_brace(tmp_path):
    """The other side of the same boundary: a REAL multi-line block followed by
    another object key must yield exactly its own entries. Without this, an
    over-tight range end would pass the test above by matching nothing at all."""
    home = tmp_path / "h"
    _settings_with(home, {"alpha": "name-only", "beta": "off"},
                   extra={"statusLine": {"command": "bar-status"}})
    assert _parity_fact(home) == "alpha=name-only beta=off"


def test_the_extractor_reports_only_the_enum_values_it_finds(tmp_path):
    """The override VALUES are printed, unlike the key-name-only rule the rest of
    this payload follows for settings.json — they are an enum, not a secret. This
    pins that nothing ELSE from the file rides along: the fixture's other keys
    carry values that would be a leak if they appeared."""
    home = tmp_path / "h"
    _settings_with(home, {"alpha": "name-only"},
                   extra={"apiKeyHelper": "SECRET-DO-NOT-PRINT"})
    fact = _parity_fact(home)
    assert fact == "alpha=name-only"
    assert "SECRET" not in fact


# --------------------------------------------------------------------------- #
# UNTRACKED FILES IN NIX-READ PATHS (rc 23)
#
# 🔴 THE GAP THE UNTRACKED BLOCK LEFT. Untracked files have been counted and
# listed here from the start and have never escalated — right for most of them,
# and wrong for the subset that is DEPLOYED CODE with no commit and no backup.
# Measured 2026-08-25: one untracked file had sat on the workbench for ~3 weeks
# with every check green, and the same run listed a second one that IS nix-read
# (nix/home.nix copies scripts/dl-router into the store whole). Nothing in the
# output distinguished them.
#
# The design decisions these tests pin, each of which could have gone the other
# way and been silently worse:
#   * a host whose derived nix-read set is EMPTY is COULD NOT MEASURE and sets NO
#     rc, and bumps and resets NOTHING. An empty set classifies every untracked
#     file on every host as clean, which is the reassuring-zero failure this
#     whole subsystem exists to refuse.
#   * the ladder is per (HOST, PATH) and RESETS the moment a path stops being
#     reported — a ladder that only ever increments is the rc 18 bug this repo
#     already paid for.
#   * the threshold is LONGER than rc 18's, because writing a file and
#     committing it an hour later is the normal working shape here.
# --------------------------------------------------------------------------- #
def _nixdirt(out):
    """(hosts-reporting, untracked, nix-read-paths, hits, listed, escalated,
    blind) off the summary line, or None when the block withheld it.

    `listed` sits beside `hits` because the two are NOT the same number once
    DRIFT_NIXDIRT_MAX has cut the enumeration, and every test that reads a hit
    count out of this tuple would otherwise be reading a capped one.
    """
    m = re.search(
        r"\[nixdirt\] hosts-reporting=(\d+) untracked=(\d+) nix-read-paths=(\d+) "
        r"hits=(\d+) listed=(\d+) escalated=(\d+) blind=(\d+)", out)
    return tuple(int(g) for g in m.groups()) if m else None


def test_an_untracked_nix_read_file_is_reported_but_does_not_fail_the_unit(fleet):
    """Below the threshold this must stay quiet to systemd. A file created ten
    minutes ago and not yet committed is the normal working shape, and a deadman
    that alerts on it trains everyone to click through.

    RED AT origin/main (200e6383): no [nixdirt] block exists there at all, so the
    assertion on its summary line cannot match.
    """
    fleet.seed_nix_read()
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 0, out
    assert "charlie-dir/nested/november.txt: UNTRACKED in a NIX-READ path (DROPPED)" in out, out
    assert "1/3 consecutive; NOT escalated" in out, out
    assert _nixdirt(out)[:1] == (1,), out
    assert _nixdirt(out)[3:] == (1, 1, 0, 0), out   # hits, listed, escalated, blind


def test_an_untracked_nix_read_file_escalates_after_n_consecutive_runs(fleet):
    """🔴 THE OTHER DIRECTION. A file that has been sitting in a nix-read path
    for the whole window is not "mid-edit" — it is content on exactly one machine
    that the machine is also running.

    RED AT origin/main (200e6383): the ladder there is [0, 0, 0, 0] for every
    untracked file, nix-read or not — that is the defect.
    """
    fleet.seed_nix_read()
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")

    codes = []
    for _ in range(4):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
        codes.append(rc)
    assert codes == [0, 0, 23, 23], f"escalation ladder wrong: {codes}\n{out}"
    # The whole claim on ONE normalised line — host, path, class, streak,
    # threshold. Split across two it is walkable by rewording the other half.
    assert ("DRIFT — workbench charlie-dir/nested/november.txt: UNTRACKED in a "
            "NIX-READ path (DROPPED) for 4 CONSECUTIVE runs (threshold 3)." in out), out
    assert _nixdirt(out)[5] == 1, out            # escalated


def test_an_untracked_mkOutOfStoreSymlink_target_escalates_as_LIVE(fleet):
    """The LIVE class names a different consequence and must say so: the deployed
    path is a link back into the working tree, so this file is being SERVED right
    now — no switch, no generation boundary, no other copy anywhere."""
    fleet.seed_nix_read()
    (fleet.work / "kilo-live.txt").write_text("kilo\n")

    for _ in range(3):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 23, out
    assert ("DRIFT — workbench kilo-live.txt: UNTRACKED in a NIX-READ path "
            "(LIVE) for 3 CONSECUTIVE runs (threshold 3)." in out), out
    assert "IS being served on that host right now" in out, out


def test_the_nixdirt_streak_resets_when_the_file_is_committed(fleet):
    """🔴 THE LADDER MUST COME DOWN. A ladder that only ever increments escalates
    on evidence from a state that has since been fixed — the rc 18 bug this repo
    already paid for once."""
    fleet.seed_nix_read()
    p = fleet.work / "charlie-dir" / "nested" / "november.txt"
    p.write_text("nov\n")
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
        assert rc == 0, out
    assert "2/3 consecutive" in out, out

    # It stops being UNTRACKED — the fix an operator would actually make.
    fleet.git(fleet.work, "add", "charlie-dir/nested/november.txt")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 0, out
    assert _nixdirt(out)[3] == 0, out          # hits back to zero

    # ...and if it returns, the count starts from ONE, not from three.
    fleet.git(fleet.work, "rm", "-q", "--cached", "charlie-dir/nested/november.txt")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 0, f"the streak did not reset when the file was tracked\n{out}"
    assert "1/3 consecutive" in out, out


def test_an_untracked_file_outside_every_nix_read_path_never_escalates(fleet):
    """The complement, and the reason this is not just "escalate on untracked".
    A scratch file that nix never opens is reported as information and stays
    information however long it sits there."""
    fleet.seed_nix_read()
    (fleet.work / "outside-mike.txt").write_text("mike\n")

    codes = []
    for _ in range(4):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
        codes.append(rc)
    assert codes == [0, 0, 0, 0], f"a non-nix-read file escalated: {codes}\n{out}"
    assert "untracked: 1 file(s)" in out, out          # still REPORTED
    assert _nixdirt(out)[3] == 0, out                  # ...and not a hit


def test_a_measured_fleet_reports_a_zero_over_a_REAL_nixread_denominator(fleet):
    """🔴 POSITIVE CONTROL for the whole block, and the partner of every red
    above. `hits=0` is exactly what a classifier wired to nothing prints. It is
    only evidence beside a NON-ZERO nix-read-paths count: a set was derived, the
    untracked files were walked against it, and none matched."""
    fleet.seed_nix_read()
    (fleet.work / "outside-mike.txt").write_text("mike\n")

    rc, out = fleet.check("--no-remote")
    assert rc == 0, out
    hosts, untracked, nixread, hits, listed, escalated, blind = _nixdirt(out)
    assert (hosts, untracked, hits, listed, escalated, blind) == (1, 1, 0, 0, 0, 0), out
    assert nixread >= 4, f"the denominator is {nixread}; a zero there is no scan\n{out}"


def test_a_host_with_no_derivable_nix_read_set_is_COULD_NOT_MEASURE(fleet):
    """🔴 THE REFUSAL. Without a derived set every untracked file classifies
    clean — so the run says it could not look, sets NO code, and the summary is
    withheld rather than printed as a clean-looking line over an empty set."""
    fleet.seed_nix_read(with_flake=False)     # lib present, nothing to derive from
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")

    for _ in range(4):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
        assert rc == 0, out
    assert "[nixdirt] workbench: COULD NOT MEASURE" in out, out
    assert "nix-read-paths=0" in out, out
    assert _nixdirt(out) is None, f"a summary was printed over an empty set\n{out}"
    assert "NOT EVALUATED" in out, out


def test_a_host_without_the_predicate_lib_is_COULD_NOT_MEASURE(fleet):
    """The pre-deploy state: a host still on a commit that predates the lib. It
    must read as "we could not look", never as "nothing is exposed"."""
    fleet.seed_nix_read(with_lib=False)
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 0, out
    assert "COULD NOT MEASURE — reason=NOLIB" in out, out
    assert "untracked-in-nix-read-paths: COULD NOT MEASURE (NOLIB)" in out, out


def test_a_blind_host_does_not_clear_a_ladder_it_could_not_measure(fleet):
    """🔴 A ladder cleared by a scan that walked nothing is worse than no ladder:
    the counter resets every run and the escalation can never arrive."""
    fleet.seed_nix_read()
    p = fleet.work / "charlie-dir" / "nested" / "november.txt"
    p.write_text("nov\n")
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert "2/3 consecutive" in out, out

    # The lib disappears — the host goes blind for one run.
    lib = fleet.work / "scripts" / "lib" / "nix_read_paths.sh"
    saved = lib.read_text()
    lib.unlink()
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 0, out
    assert "COULD NOT MEASURE" in out, out

    lib.write_text(saved)
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 23, (
        "the blind run reset a ladder it could not measure\n%s" % out)


def test_the_nixdirt_ladder_is_kept_PER_PATH(fleet):
    """One path clearing must not clear another. They are separate exposures and
    a shared counter is a ladder that resets itself for reasons nobody sees."""
    fleet.seed_nix_read()
    a = fleet.work / "charlie-dir" / "nested" / "november.txt"
    b = fleet.work / "charlie-dir" / "nested" / "oscar.txt"
    a.write_text("nov\n")
    b.write_text("osc\n")
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert out.count("2/3 consecutive") == 2, out

    a.unlink()
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 23, f"oscar's own ladder was cleared by november going away\n{out}"
    assert "oscar.txt" in out and "november.txt" not in out, out


def test_the_nixdirt_counter_filename_is_INJECTIVE(fleet):
    """`a/b` and `a_b` must not land on one counter. The encoder doubles `_`
    before mapping `/`, which is reversible over the whole path alphabet."""
    fleet.seed_nix_read()
    (fleet.work / "charlie-dir" / "nested" / "papa.txt").write_text("p\n")
    (fleet.work / "charlie-dir" / "nested_papa.txt").write_text("q\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="9")
    assert rc == 0, out
    files = sorted(p.name for p in fleet.state.glob("nixdirt-*"))
    assert len(files) == 2, f"two paths collided onto {files}\n{out}"


def test_rc23_ranks_between_rc15_and_rc12():
    """The digit is not the severity. rc 12 (a checkout ship.sh will SKIP
    forever) outranks it; rc 15 (a settings.json key set that differs, which
    costs a capability and loses nothing) does not."""
    body = DRIFT.read_text().split("severity() {", 1)[1].split("\n}", 1)[0]
    ranks = {int(m): int(v) for m, v in
             re.findall(r"^\s*(\d+)\)\s*echo (\d+)", body, re.M)}
    assert ranks[12] > ranks[23] > ranks[15], ranks


def test_the_rc23_legend_is_printed_with_the_verdict(fleet):
    fleet.seed_nix_read()
    (fleet.work / "kilo-live.txt").write_text("kilo\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 23, out
    assert "rc23=" in out, f"the rc legend does not mention the code it returned\n{out}"
    assert "DRIFT (rc=23)" in out, out


def test_a_non_integer_nixdirt_threshold_is_rejected(fleet):
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="four")
    assert rc == 2, out
    assert "DRIFT_NIXDIRT_ESCALATE must be a non-negative integer" in out, out


def test_unpushed_devrc_commits_still_outrank_an_escalated_rc23(fleet):
    """A host holding un-pushed commits is the code the whole legend is read
    against; an untracked passenger must never displace it.

    INVARIANT GUARD, not regression coverage — GREEN at origin/main (200e6383),
    where rc 23 did not exist and rc 8 won by default. Kept because the ranking
    is the claim: severity() is a table, not the digits, and 23 > 8 numerically."""
    fleet.seed_nix_read()
    fleet.add_local_commit()
    (fleet.work / "kilo-live.txt").write_text("kilo\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 8, out


def test_the_nixdirt_summary_is_withheld_when_no_host_reported(fleet):
    """A run that contacted nobody must not print a triple that reads clean."""
    fleet.stub_ssh(255)
    rc, out = fleet.check("--no-local")
    assert _nixdirt(out) is None, out
    assert "[nixdirt] NOT EVALUATED — no host returned a nix-untracked fact" in out, out


def test_a_path_named_like_a_header_field_is_read_as_a_PATH(fleet):
    """🔴 REACHABILITY, and a bug this actually found. The FACT line carries
    `reason=<TOKEN>` header fields and `<path>=<CLASS>` pairs in one
    whitespace-separated list. An untracked repo-root file named `reason` emits
    the token `reason=DROPPED` — and with the header arms matched first, the driver
    read it as the line's REASON, called the host COULD NOT MEASURE and dropped
    the very file it was reporting. Silently, in the reassuring direction.

    The fixture names `../reason` in home.nix so the path is genuinely nix-read;
    a fixture where it classified NONE could not reach this at all.
    """
    fleet.seed_nix_read()
    (fleet.work / "reason").write_text("collides with a header field\n")

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 23, f"a path named like a header field was not classified\n{out}"
    assert "[nixdirt] workbench: COULD NOT MEASURE" not in out, (
        "a path token was read as the line's reason field\n%s" % out)
    assert ("DRIFT — workbench reason: UNTRACKED in a NIX-READ path (DROPPED) for "
            "1 CONSECUTIVE runs (threshold 1)." in out), out


def test_a_fact_line_claiming_OK_over_a_ZERO_denominator_is_still_refused(fleet):
    """🔴 REACHABILITY for the DENOMINATOR guard, which no real payload can
    exercise: nix_read_scan cannot return OK with a zero count, so `[ $N_MM -le
    0 ]` sits behind a check that always wins and a mutation of it survives a
    fully green suite. What it defends against is a malformed or OLDER payload on
    the far side of the ssh hop — which is what a stubbed remote is for.

    Reported COULD NOT MEASURE and NOT escalated: with a zero denominator the
    pair on that line is a classification nothing was measured against.

    🔴 The stub line carries a WELL-FORMED hits=/listed= pair on purpose. Those
    fields have their own refusal arms, and a fixture missing them would take
    THAT branch instead — the denominator guard would never execute and this
    test would be green for a neighbour's reason.
    """
    fleet.seed_nix_read()
    fleet.stub_ssh(0, stdout=(
        "[laptop] FACT nix-untracked untracked=1 nixread=0 hits=1 listed=1 "
        "reason=OK papa-x.txt=DROPPED"))
    rc, out = fleet.check("--no-local", DRIFT_NIXDIRT_ESCALATE="1")
    assert ("[nixdirt] laptop: COULD NOT MEASURE — reason=OK untracked=1 "
            "nix-read-paths=0 hits=1 listed=1." in out), out
    assert rc != 23, f"escalated off a zero denominator\n{out}"
    assert "papa-x.txt" not in out.split("[nixdirt]", 1)[1].split("===", 1)[0], out


def test_the_payload_classifies_by_CLASS_and_not_merely_by_being_untracked(fleet):
    """🔴 ISOLATED REACHABILITY for the payload's class filter — the arm that
    decides which untracked files reach the driver at all. The driver-side
    `*=LIVE|*=DROPPED` filter cannot be reached by a NONE path, because the payload
    never emits one; so this is where the distinction is actually made, and this
    is the case that proves it is made on the CLASS.
    """
    fleet.seed_nix_read()
    (fleet.work / "outside-mike.txt").write_text("mike\n")          # NONE
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")  # DROPPED

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="9")
    assert rc == 0, out
    assert "untracked: 2 file(s)" in out, out
    assert "untracked-in-nix-read-paths: 1 of 2 untracked file(s)" in out, out
    assert "november.txt" in out
    hits = _nixdirt(out)[3]
    assert hits == 1, f"the class filter counted {hits} of 2 untracked files\n{out}"


def test_the_DROPPED_reason_does_not_claim_the_file_is_in_the_artifact(fleet):
    """🔴 THE CORRECTION. rc 23 is by construction about UNTRACKED files, and nix
    filters a git checkout to the files git knows about — so a nix-read path here
    reached NOTHING. The escalation is unchanged (unsaved work, no commit, no
    backup, one `git add` from the artifact); the SENTENCE is what was wrong.

    It used to read "nix reads it at eval/build time, so every generation built on
    that host carries it". Measured 2026-08-25: all six `-dl-router` store
    generations carry tests/ (37 files) and none carries the untracked
    tests/load_test_store.sh. A verdict that overstates is the exact failure this
    block exists to remove.
    """
    fleet.seed_nix_read()
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 23, out
    assert "did NOT reach the artifact" in out, out
    assert "one git-add from being deployed" in out, out
    assert "every generation built on" not in out, (
        "the escalation still claims an untracked file is in the artifact\n%s" % out)


def test_the_LIVE_reason_DOES_claim_the_file_is_being_served(fleet):
    """The other half, in the same block, so a mutant that makes both reasons the
    same text is caught. A mkOutOfStoreSymlink target never goes through the
    flake source at all — the deployed link resolves into the working tree at USE
    time — so for LIVE the strong claim is the true one."""
    fleet.seed_nix_read()
    (fleet.work / "kilo-live.txt").write_text("kilo\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 23, out
    assert "IS being served on that host right now" in out, out
    assert "did NOT reach the artifact" not in out, out


def test_the_two_classes_get_DIFFERENT_reasons_in_one_run(fleet):
    """🔴 ASSERTED TOGETHER, in one run, over two fixture paths whose names and
    classes are pairwise distinct. A mutant that collapses the branch — printing
    either reason for both — passes each single-class test above and dies here."""
    fleet.seed_nix_read()
    (fleet.work / "kilo-live.txt").write_text("kilo\n")
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="1")
    assert rc == 23, out
    assert "kilo-live.txt: UNTRACKED in a NIX-READ path (LIVE)" in out, out
    assert ("charlie-dir/nested/november.txt: UNTRACKED in a NIX-READ path "
            "(DROPPED)" in out), out
    assert "IS being served on that host right now" in out, out
    assert "did NOT reach the artifact" in out, out


def test_the_escalation_itself_is_unchanged_for_a_DROPPED_path(fleet):
    """🔴 THE CORRECTION MUST NOT WEAKEN THE LADDER. Fixing an overstated reason
    is not a reason to stop reporting: the file is still unsaved work on exactly
    one machine. Same ladder, same threshold, same code."""
    fleet.seed_nix_read()
    (fleet.work / "charlie-dir" / "nested" / "november.txt").write_text("nov\n")
    codes = []
    for _ in range(3):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="2")
        codes.append(rc)
    assert codes == [0, 23, 23], f"the DROPPED ladder stopped escalating: {codes}\n{out}"


# --------------------------------------------------------------------------- #
# THE ENUMERATION CAP (DRIFT_NIXDIRT_MAX)
#
# 🔴 WHY THIS SECTION EXISTS. The cap was added to bound a listing — `claude/
# skills` is a whole-directory STORE source, so one untracked subtree can put an
# unbounded number of paths into the journal and the state dir. It shipped with
# NO test of any kind, and it did not bound a listing: it truncated `NU_PAIRS`,
# which is ALSO the machine-readable FACT line the driver parses. Measured on
# 41e92a1f with 15 untracked nix-read files — the human line said `15 of 15`, the
# FACT line carried 10 pairs and no marker (the `... and N more` note goes to the
# `say` stream, which the driver does not read), and the summary printed
# `hits=10`. The code's own comment two lines above the cap said "HITS COUNTS ALL
# OF THEM… The count is the finding and must never be truncated."
#
# The three properties pinned here, each of which the cap broke or could break:
#   * the COUNT survives the cap (`hits=` is its own field, never the list's
#     length) and the driver says how many it did not name;
#   * a TRUNCATED report resets no ladder — absence from a capped list is not
#     evidence a path stopped being untracked, and the complement loop cannot
#     tell the two apart;
#   * the cap can never reach ZERO, because at 0 the payload emits no pairs, the
#     ladder walks nothing, and rc 23 is switched off by an env var while the
#     summary still prints a clean-looking `hits=0` over a real denominator.
# --------------------------------------------------------------------------- #
def _nu_fact(out):
    """The `FACT nix-untracked` line, as (header dict, [(path, reach)…])."""
    line = [ln for ln in out.splitlines() if "FACT nix-untracked" in ln]
    assert line, f"no nix-untracked FACT line at all\n{out}"
    toks = line[0].split("FACT nix-untracked", 1)[1].split()
    head, pairs = {}, []
    for t in toks:
        if t.endswith("=LIVE") or t.endswith("=DROPPED"):
            pairs.append(tuple(t.rsplit("=", 1)))
        elif "=" in t:
            k, v = t.split("=", 1)
            head[k] = v
    return head, pairs


def _seed_many(fleet, n, prefix="delta"):
    """`n` untracked files under the DIRECTORY store source, names pairwise
    distinct and sorting deterministically. Bounds deliberately overshoot the
    default cap of 10 rather than sitting on a multiple of it."""
    d = fleet.work / "charlie-dir" / "nested"
    made = []
    for i in range(n):
        p = d / ("%s-%02d.txt" % (prefix, i))
        p.write_text("%s %d\n" % (prefix, i))
        made.append("charlie-dir/nested/%s" % p.name)
    return made


def test_the_FACT_line_carries_the_FULL_hit_count_not_the_length_of_a_capped_list(fleet):
    """🔴 THE CONTRACT IS THE COUNT, AND THE COUNT IS NEVER TRUNCATED.

    RED AT 41e92a1f: the FACT line has no `hits=` field at all, so this parse
    KeyErrors; before that, with the driver counting pairs, the summary read
    `hits=10` over 15 real hits. Fifteen and ten are pairwise distinct and
    neither equals the cap's default in the other's place, so a mutant that
    hardcodes either number cannot pass both assertions.
    """
    fleet.seed_nix_read()
    _seed_many(fleet, 15)

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    head, pairs = _nu_fact(out)
    assert head["hits"] == "15", (
        "the FACT line's hit count is %r — the driver's only machine-readable "
        "number is the truncated one again\n%s" % (head.get("hits"), out))
    assert head["listed"] == "10", (head, out)
    assert len(pairs) == 10, (
        "the enumeration is not bounded at DRIFT_NIXDIRT_MAX: %d pairs\n%s"
        % (len(pairs), out))
    # ...and the driver's summary reports the total, not the list.
    hosts, untracked, nixread, hits, listed, escalated, blind = _nixdirt(out)
    assert (hits, listed) == (15, 10), out
    assert "5 hit(s) counted but NOT named above" in out, (
        "the summary hides the difference between what it counted and what it "
        "named\n%s" % out)


def test_the_human_listing_and_the_FACT_line_agree_on_the_TOTAL(fleet):
    """🔴 THE SEAM. The two outputs are produced by different code — `say` for the
    operator, `echo … FACT` for the driver — and they diverged: `15 of 15` beside
    a ten-pair contract. Neither surface's own test could see that, because each
    was only ever read on its own.

    RED AT 41e92a1f for the FACT half (no `hits=` field).
    """
    fleet.seed_nix_read()
    _seed_many(fleet, 13, prefix="echo")

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    human = [ln for ln in out.splitlines()
             if "untracked-in-nix-read-paths:" in ln]
    assert human, out
    m = re.search(r"untracked-in-nix-read-paths: (\d+) of (\d+) untracked", human[0])
    assert m, human
    head, _ = _nu_fact(out)
    assert m.group(1) == head["hits"], (
        "the operator is told %s hits and the driver is told %s\n%s"
        % (m.group(1), head["hits"], out))
    assert _nixdirt(out)[3] == 13, out


def test_a_capped_run_still_escalates_every_path_it_DID_name(fleet):
    """🔴 REACHABILITY for the ladder under a cap. A fix that made the COUNT
    honest while quietly dropping the escalation would satisfy every count
    assertion above. Three named paths, all three on the ladder, rc 23."""
    fleet.seed_nix_read()
    _seed_many(fleet, 7, prefix="foxtrot")

    codes = []
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="2",
                              DRIFT_NIXDIRT_MAX="3")
        codes.append(rc)
    assert codes == [0, 23], f"a capped run stopped escalating: {codes}\n{out}"
    head, pairs = _nu_fact(out)
    assert (head["hits"], head["listed"]) == ("7", "3"), (head, out)
    assert _nixdirt(out)[5] == 3, (
        "only some of the NAMED paths escalated\n%s" % out)


def test_a_TRUNCATED_report_resets_no_streak_it_could_not_name(fleet):
    """🔴 THE SECOND-ORDER DEFECT, and the one a count-only fix leaves behind.

    The complement loop reads "absent from this run's pairs" as "this path is
    untracked no more" and ends its streak. Under a cap, absence ALSO means
    "pushed out of the enumeration window" — so a path that has been sitting
    there for eleven runs has its ladder cleared by a busy run that happened to
    name ten others ahead of it, and the escalation can never arrive.

    RED AT 41e92a1f: `zulu.txt` reaches 2/3, twelve alphabetically-earlier paths
    appear, and the run after that reports it at 1/3 — the reset — instead of
    escalating at 3/3.
    """
    fleet.seed_nix_read()
    z = fleet.work / "charlie-dir" / "nested" / "zulu.txt"
    z.write_text("zulu\n")
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
        assert rc == 0, out
    assert "zulu.txt: UNTRACKED in a NIX-READ path (DROPPED) — 2/3" in out, out

    # Twelve paths that sort BEFORE zulu.txt fill the whole ten-wide window.
    _seed_many(fleet, 12, prefix="alfa")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    _, pairs = _nu_fact(out)
    assert "zulu.txt" not in [p for p, _c in pairs], (
        "the fixture did not push zulu.txt out of the window; the reset this "
        "test exists for is unreachable\n%s" % out)
    assert "LISTING TRUNCATED" in out, (
        "a truncated report did not say so to the driver\n%s" % out)
    assert "No streak was reset on this" in out, out

    # The window clears; zulu must RESUME at 3, not restart at 1. Three, not
    # four: the truncated run neither reset the streak nor bumped it — it could
    # not see the path, so it is evidence about nothing, in either direction.
    for p in fleet.work.glob("charlie-dir/nested/alfa-*.txt"):
        p.unlink()
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 23, (
        "zulu.txt's ladder was cleared by a run that could not name it\n%s" % out)
    assert ("DRIFT — workbench charlie-dir/nested/zulu.txt: UNTRACKED in a "
            "NIX-READ path (DROPPED) for 3 CONSECUTIVE runs (threshold 3)."
            in out), out


def test_an_UNtruncated_run_still_resets_a_streak_that_ended(fleet):
    """🔴 THE PARTNER, and the control on the test above. Suppressing the reset
    whenever a report is truncated must not suppress it when the report is
    COMPLETE — that would be the rc-18 only-ever-increments bug, reintroduced
    through the other door. Same fixture shape, no truncation."""
    fleet.seed_nix_read()
    z = fleet.work / "charlie-dir" / "nested" / "zulu.txt"
    z.write_text("zulu\n")
    for _ in range(2):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert "zulu.txt: UNTRACKED in a NIX-READ path (DROPPED) — 2/3" in out, out

    z.unlink()
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert "LISTING TRUNCATED" not in out, out
    assert _nixdirt(out)[3] == 0, out

    z.write_text("zulu\n")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
    assert rc == 0, f"the streak did not reset on a COMPLETE report\n{out}"
    assert "zulu.txt: UNTRACKED in a NIX-READ path (DROPPED) — 1/3" in out, out


def test_DRIFT_NIXDIRT_MAX_of_ZERO_is_refused_rather_than_disabling_rc23(fleet):
    """🔴 THE VERDICT-KILLING VALUE. Measured on 41e92a1f: three untracked
    nix-read files, DRIFT_NIXDIRT_ESCALATE=3, four consecutive runs returned
    [0,0,0,0] where the default returns [0,0,23,23], and the summary read
    `untracked=3 nix-read-paths=6 hits=0 escalated=0` — a clean-looking zero over
    a real denominator, which is the exact shape the block two lines below
    withholds its summary to avoid.

    Every other cap in this file bounds a LISTING whose count is printed
    separately, so 0 is honest there. This one feeds the ladder, so it has a
    floor, and the floor is a usage error rather than a silent substitution: a
    value that would turn a verdict off must not be quietly reinterpreted.
    """
    fleet.seed_nix_read()
    _seed_many(fleet, 3, prefix="golf")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3",
                          DRIFT_NIXDIRT_MAX="0")
    assert rc == 2, f"a cap of 0 was accepted, so rc 23 is off: {rc}\n{out}"
    assert "DRIFT_NIXDIRT_MAX must be an integer >= 1, got: 0" in out, out
    assert _nixdirt(out) is None, f"a summary was printed at all\n{out}"


def test_a_non_integer_DRIFT_NIXDIRT_MAX_is_still_refused(fleet):
    """The floor must not have replaced the type check — both arms, distinct
    messages, so a mutant that keeps one and drops the other is caught."""
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_MAX="ten")
    assert rc == 2, out
    assert "DRIFT_NIXDIRT_MAX must be an integer >= 1, got: ten" in out, out


# --------------------------------------------------------------------------- #
# THE FLOOR MUST BE A VALUE GUARD, NOT A SPELLING GUARD
#
# 🔴 THE FIRST VERSION OF THIS FLOOR WAS WALKABLE, and this is the SECOND
# instance of the identical defect in one night (PR #854 had `''|*[!0-9]*|0)`
# accepting `00`/`007`). `case "$2" in 0) reject ;; esac` matches the glob `0`
# and nothing else, so `00` and `000` are all-digits, miss both arms, and sail
# through — after which `[ "$NU_EMIT" -lt 00 ]` is false and NO path is ever
# enumerated. MEASURED on the pre-fix head, 3 untracked nix-read files,
# DRIFT_NIXDIRT_ESCALATE=3, four consecutive runs:
#
#     DRIFT_NIXDIRT_MAX=10 (default)      -> [0, 0, 23, 23]   <- control
#     DRIFT_NIXDIRT_MAX=00                -> [0, 0,  0,  0]
#     DRIFT_NIXDIRT_MAX=000               -> [0, 0,  0,  0]
#     DRIFT_NIXDIRT_MAX=99999999999999999999 -> [0, 0, 0, 0]
#
# 🔴 The oversized case is NOT caught by an upper-bound test, and that is the
# subtle half. `[ 99999999999999999999 -gt 100000 ]` does not answer "yes" — it
# ERRORS and evaluates FALSE, so a guard written as "reject when too big" waves
# the too-big value through. The guard therefore PROVES the value is in range
# (`-ge 1` AND `-le CEILING`, both required to succeed) instead of testing for
# the complement: a value the shell cannot compare cannot prove itself, and is
# refused. These tests pin the VALUE behaviour, so a future rewrite that goes
# back to matching spellings fails here rather than in production.
#
# Reachable by following the tool's OWN advice ("raise DRIFT_NIXDIRT_MAX to see
# them all") to an absurd degree, which is why the huge value is a real case and
# not a hypothetical.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spelling", ["00", "000", "0000000"])
def test_a_PADDED_zero_is_refused_like_a_bare_zero(fleet, spelling):
    """RED AT 12d4c01d: all-digits, matches neither arm, accepted — and rc 23
    goes silent. The message names the leading zero rather than pretending the
    value was not a number, because it WAS a number: it was zero."""
    fleet.seed_nix_read()
    _seed_many(fleet, 3, prefix="papa")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3",
                          DRIFT_NIXDIRT_MAX=spelling)
    assert rc == 2, (
        "DRIFT_NIXDIRT_MAX=%s was accepted; rc 23 is switched off by spelling\n%s"
        % (spelling, out))
    assert "DRIFT_NIXDIRT_MAX must not have a leading zero" in out, out


def test_a_padded_zero_does_not_reach_the_LADDER_at_all(fleet):
    """🔴 THE CONSEQUENCE, asserted as behaviour beside the message above.

    A test that only checked the wording would pass against a guard that printed
    a warning and carried on. This is the ladder itself: four consecutive runs
    over three genuinely untracked nix-read files must NOT be the all-zero shape
    the default refuses. Both sequences are named so neither can be hardcoded.
    """
    fleet.seed_nix_read()
    _seed_many(fleet, 3, prefix="quebec")

    control = []
    for _ in range(4):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3")
        control.append(rc)
    assert control == [0, 0, 23, 23], f"the control ladder is wrong: {control}\n{out}"

    walked = []
    for _ in range(4):
        rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3",
                              DRIFT_NIXDIRT_MAX="00")
        walked.append(rc)
    assert walked == [2, 2, 2, 2], (
        "a padded zero produced %r; [0,0,0,0] is rc 23 disabled by an env var, "
        "which is the exact shape this floor exists to refuse\n%s" % (walked, out))


@pytest.mark.parametrize("huge", ["99999999999999999999", "9223372036854775808"])
def test_a_value_this_shell_cannot_COMPARE_is_refused(fleet, huge):
    """RED AT 12d4c01d: digits pass the type check, then `[ 0 -lt <huge> ]`
    errors and evaluates FALSE, so nothing is enumerated and rc 23 goes silent —
    the same [0,0,0,0] as a cap of zero, reached by taking the tool's own
    "raise DRIFT_NIXDIRT_MAX" advice too far.

    Both operands are above 2^63-1 and are pairwise distinct; the second is
    exactly one past the maximum, so a guard that only rejects absurd LENGTHS
    rather than uncomparable VALUES is caught too.
    """
    fleet.seed_nix_read()
    _seed_many(fleet, 3, prefix="romeo")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="3",
                          DRIFT_NIXDIRT_MAX=huge)
    assert rc == 2, (
        "DRIFT_NIXDIRT_MAX=%s was accepted; the cap it produces enumerates "
        "NOTHING\n%s" % (huge, out))
    assert "DRIFT_NIXDIRT_MAX must be between 1 and" in out, out


def test_the_CEILING_is_a_real_boundary_measured_from_both_sides(fleet):
    """🔴 THE BOUNDARY ITSELF, both sides, so the ceiling is pinned as a VALUE
    and not as "some large number". Read out of the script rather than restated
    here: a constant duplicated into the test is a constant that can drift.
    """
    m = re.search(r"^DRIFT_INT_CEILING=(\d+)", DRIFT.read_text(), re.M)
    assert m, "the ceiling is no longer a named constant"
    ceiling = int(m.group(1))
    fleet.seed_nix_read()
    _seed_many(fleet, 3, prefix="sierra")

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_MAX=str(ceiling))
    assert rc == 0, f"the ceiling itself was rejected\n{out}"

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_MAX=str(ceiling + 1))
    assert rc == 2, f"one past the ceiling was accepted\n{out}"
    assert "must be between 1 and %d" % ceiling in out, out


def test_the_same_spelling_hazard_is_closed_for_every_LADDER_threshold(fleet):
    """🔴 THE WIDEST READING, and it is not hypothetical.

    `require_int`'s callers include four consecutive-run ladders, and a
    threshold the shell cannot compare makes `[ "$STK" -ge "$THR" ]` an error
    that evaluates FALSE — so the ladder never escalates and the run reports no
    drift. Measured directly: STK=5, THR=99999999999999999999 -> QUIET. That is
    the same verdict-disabling class as the cap, reached through a different
    tunable, so require_int is bounded too rather than only the floor.

    DRIFT_NIXDIRT_ESCALATE is the one this PR introduced, so it is the one
    asserted; `0` stays LEGAL there (escalate immediately is a real request),
    which is exactly why it needed the bound rather than the floor.
    """
    fleet.seed_nix_read()
    _seed_many(fleet, 1, prefix="tango")

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="99999999999999999999")
    assert rc == 2, (
        "an uncomparable ladder threshold was accepted — the ladder it feeds "
        "can never escalate\n%s" % out)
    assert "DRIFT_NIXDIRT_ESCALATE must be between 0 and" in out, out

    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="04")
    assert rc == 2, f"a padded ladder threshold was accepted\n{out}"
    assert "DRIFT_NIXDIRT_ESCALATE must not have a leading zero" in out, out

    # ...and ZERO is still legal here, which is the whole reason this tunable
    # takes require_int and not require_positive_int. A guard that rejected it
    # would satisfy both assertions above and break a documented setting.
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="0")
    assert rc == 23, (
        "DRIFT_NIXDIRT_ESCALATE=0 must mean 'escalate immediately', not be "
        "rejected\n%s" % out)


def test_the_ceiling_clears_every_DEFAULT_this_script_sets():
    """🔴 THE ONE CORRECTNESS PROPERTY THE CEILING'S VALUE MUST HAVE, and the
    reason the boundary test above deliberately does NOT hardcode the number.

    Moving the ceiling is a TUNING decision, not a bug — a mutation sweep
    confirmed that raising it by one is an equivalent mutant, and pinning the
    literal in the test would turn a legitimate retune into a red suite (the
    "constant duplicated into the test" shape this repo has been bitten by).
    What is NOT a tuning decision is a ceiling BELOW one of this script's own
    defaults: every run would then reject its own configuration and exit 2, and
    a permanently-red deadman is worse than no deadman because it trains
    everyone to click through.

    Derived from the source on both sides — the ceiling and the defaults — so a
    tenth tunable is covered with no edit here.

    🔴 THE DERIVATION IS PINNED TWO-WAY, and it had to be: the first version of
    this test matched names with `[A-Z_]+`, which silently dropped the ONE
    default containing a digit — DRIFT_PHASE2_TIMEOUT, at 60, the LARGEST of
    them and therefore the only one a ceiling of 50 would have caught. A
    `len(defaults) >= 5` floor passed happily on the 8 that remained, and the
    guard scanned clean against a ceiling below a real default. So the ledger
    is now asserted EQUAL to the set of names require_int/require_positive_int
    validates: a regex that goes narrow on either side fails loudly instead of
    quietly measuring less.
    """
    src = DRIFT.read_text()
    m = re.search(r"^DRIFT_INT_CEILING=(\d+)", src, re.M)
    assert m, "the ceiling is no longer a named constant this test can find"
    ceiling = int(m.group(1))

    defaults = {
        name: int(val) for name, val in
        re.findall(r'^(DRIFT_[A-Z0-9_]+)="\$\{\1:-(\d+)\}"', src, re.M)
    }
    validated = set(re.findall(
        r"^require(?:_positive)?_int (DRIFT_[A-Z0-9_]+) ", src, re.M))
    assert validated, "no validated tunables found — this ledger is wired to nothing"
    assert set(defaults) == validated, (
        "the numeric-default ledger and the validated-tunable ledger disagree: "
        "defaults-only=%r validated-only=%r. One of the two regexes is measuring "
        "less than it looks like it measures."
        % (sorted(set(defaults) - validated), sorted(validated - set(defaults)))
    )
    too_big = {n: v for n, v in defaults.items() if v > ceiling}
    assert too_big == {}, (
        "these defaults exceed DRIFT_INT_CEILING=%d, so drift-check would reject "
        "its OWN configuration on every run: %r" % (ceiling, too_big)
    )


def test_a_ZERO_accepting_tunable_still_accepts_its_zero(fleet):
    """🔴 THE CONTROL ON THE TIGHTENING. Two tunables legitimately take 0 —
    GNU `timeout 0` means NO timeout — and a blanket swap to the floor would
    have broken both silently. Asserted so the next person tightening this file
    sees which ones must stay permissive."""
    fleet.seed_nix_read()
    for var in ("DRIFT_PHASE2_TIMEOUT", "DRIFT_SRC_FETCH_TIMEOUT",
                "DRIFT_UNTRACKED_MAX", "DRIFT_DANGLING_MAX"):
        rc, out = fleet.check("--no-remote", **{var: "0"})
        assert rc == 0, f"{var}=0 was rejected, but 0 is meaningful there\n{out}"


def test_a_cap_of_ONE_is_accepted_and_enumerates_exactly_one(fleet):
    """🔴 THE BOUNDARY ON THE LEGAL SIDE, so the floor is pinned as `>= 1` and
    not as some larger number nobody stated. Without this the guard could reject
    1 as well and every other test here would still pass."""
    fleet.seed_nix_read()
    _seed_many(fleet, 4, prefix="hotel")
    rc, out = fleet.check("--no-remote", DRIFT_NIXDIRT_ESCALATE="9",
                          DRIFT_NIXDIRT_MAX="1")
    assert rc == 0, out
    head, pairs = _nu_fact(out)
    assert (head["hits"], head["listed"]) == ("4", "1"), (head, out)
    assert len(pairs) == 1, (pairs, out)


def test_the_driver_refuses_a_report_with_NO_hit_count(fleet):
    """🔴 REACHABILITY for the `hits=` refusal, which no real payload can produce
    — the emitter always writes the field. What it defends against is an OLDER
    drift-check.sh on the far side of the ssh hop, whose FACT line has no `hits=`
    at all: derived from the pairs, that host's truncated list would become its
    hit count and read as a smaller finding than it is.

    Stubbed remote, because that is the only way a malformed contract arrives.

    🔴 ISOLATED: `listed=` is present and correct, so the sibling arm beside it
    CANNOT be what fails. A first version of this test dropped both fields and
    was killed by the `listed=` arm — a mutant deleting the `hits=` check alone
    then survived, green for a neighbour's reason.
    """
    fleet.seed_nix_read()
    fleet.stub_ssh(0, stdout=(
        "[laptop] FACT nix-untracked untracked=2 nixread=6 listed=1 reason=OK "
        "india-x.txt=DROPPED"))
    rc, out = fleet.check("--no-local", DRIFT_NIXDIRT_ESCALATE="1")
    assert "[nixdirt] laptop: COULD NOT MEASURE" in out, out
    assert "hits=-1 listed=1." in out, (
        "the refusal does not name the missing field, or fired on the wrong "
        "one\n%s" % out)
    assert rc != 23, f"escalated off a report with no hit count\n{out}"


def test_the_driver_refuses_a_report_with_NO_listed_count(fleet):
    """The other arm of the same pair, isolated the same way — `hits=` present
    and correct — so the two guards are proved to exist SEPARATELY rather than
    as one check wearing two names."""
    fleet.seed_nix_read()
    fleet.stub_ssh(0, stdout=(
        "[laptop] FACT nix-untracked untracked=2 nixread=6 hits=1 reason=OK "
        "india-x.txt=DROPPED"))
    rc, out = fleet.check("--no-local", DRIFT_NIXDIRT_ESCALATE="1")
    assert "[nixdirt] laptop: COULD NOT MEASURE" in out, out
    assert "hits=1 listed=-1." in out, out
    assert rc != 23, f"escalated off a report with no listed count\n{out}"


def test_the_driver_refuses_a_report_whose_listed_count_disagrees_with_its_pairs(fleet):
    """🔴 THE INTEGRITY CHECK THAT MAKES `listed=` LOAD-BEARING rather than
    decorative — without it the driver could simply count the pairs and the field
    would be an unchecked assertion.

    A report claiming three enumerated paths while carrying one is a line
    mangled or truncated between the hosts. Read as complete, the two missing
    paths' streaks would be ended by the complement loop, silently, in the
    direction of "nothing to see". So the whole report is refused.
    """
    fleet.seed_nix_read()
    fleet.stub_ssh(0, stdout=(
        "[laptop] FACT nix-untracked untracked=4 nixread=6 hits=4 listed=3 "
        "reason=OK juliett-x.txt=DROPPED"))
    rc, out = fleet.check("--no-local", DRIFT_NIXDIRT_ESCALATE="1")
    assert ("[nixdirt] laptop: COULD NOT MEASURE — the report claims listed=3 "
            "path(s) but 1 arrived." in out), out
    assert rc != 23, f"escalated off a self-inconsistent report\n{out}"
    assert _nixdirt(out) is None, (
        "a summary was printed over a report the driver refused\n%s" % out)


def test_a_CONSISTENT_report_is_accepted(fleet):
    """🔴 POSITIVE CONTROL for the integrity check. A guard that refused every
    stubbed report would satisfy the test above while proving nothing — the same
    fixture shape, with `listed=` telling the truth, must go all the way to an
    escalation."""
    fleet.seed_nix_read()
    fleet.stub_ssh(0, stdout=(
        "[laptop] FACT nix-untracked untracked=4 nixread=6 hits=4 listed=1 "
        "reason=OK juliett-x.txt=DROPPED"))
    rc, out = fleet.check("--no-local", DRIFT_NIXDIRT_ESCALATE="1")
    assert "[nixdirt] laptop: COULD NOT MEASURE" not in out, out
    assert rc == 23, f"a well-formed report did not escalate\n{out}"
    assert "juliett-x.txt" in out, out
    assert _nixdirt(out)[3:5] == (4, 1), out


# --------------------------------------------------------------------------- #
# BRANCH PROTECTION ON THE CANONICAL REMOTE (rc 24)
#
# The other three parities all take `origin/main` as the reference and ask who
# has diverged from it. This one asks whether `origin/main` is still a branch
# anything has to get past a gate to reach.
#
# 🔴 THE DESIGN DECISION THESE TESTS EXIST TO PIN, and it is the opposite of the
# rc-22 one. There, a could-not-measure would have made the arm permanently RED;
# here, a could-not-measure would make it permanently GREEN — worse, because the
# natural failure of `gh` (no token, no network) is an EMPTY string, an empty
# string parses as a count of zero, and zero is the DRIFT value. Both directions
# are wrong and they are wrong for different reasons, so the arm is tested from
# both ends: it must fire on a real zero, and it must refuse to fire on an
# absence.
#
# 🔴 AND THE VERDICT MUST READ THE COUNT, NEVER `protected`. The measured
# incident is `protected: true` with required_status_checks deleted out of the
# standing object, so an arm keying on the flag reports healthy on the exact
# state that bit us. `test_a_standing_protection_object_with_checks_deleted…` is
# that discriminator; it and the healthy case differ ONLY in the count.
#
# Every payload below is the line the arm's own `--jq` produces, measured
# against the live API on 2026-08-29 — see Fleet.stub_gh.
# --------------------------------------------------------------------------- #

# owner/repo pairwise distinct from every real slug this repo names, so an
# assertion cannot pass by matching something the script hardcoded.
BP_SLUG = "fixture-owner/fixture-repo"


def _protect(fleet, *args, gh=None, gh_rc=0, log=None, **env):
    """Run the checker with the rc-24 arm pointed at a fixture slug.

    `gh=None` installs NO stub at all — the DRIFT_GH path stays absent, which is
    the could-not-measure branch. Any string installs a stub printing it.
    """
    fleet.catch_up()
    if gh is not None:
        fleet.stub_gh(gh, exit_code=gh_rc, log=log)
    return fleet.check(*args, DRIFT_PROTECT_SLUG=BP_SLUG, **env)


# --- the reds --------------------------------------------------------------- #
def test_zero_required_checks_is_rc24(fleet):
    """A branch with no protection object at all — `protected false, 0 checks`."""
    rc, out = _protect(fleet, "--no-remote", gh="false 0")
    assert rc == 24, f"an unprotected main did not fire rc 24: {rc}\n{out}"
    assert "ZERO required status checks" in out, out
    assert BP_SLUG in out, out
    # protected=false is a CREATE, not a restore, and the finding must say which.
    assert "no protection object at all" in out, out


def test_a_standing_protection_object_with_checks_deleted_is_still_rc24(fleet):
    """🔴 THE MEASURED INCIDENT SHAPE, and the discriminator for the whole arm.

    2026-08-29: `required_status_checks` was DELETED out of a protection object
    that stayed standing, so the branch still reports `protected: true`. An arm
    that keyed on that flag would call this healthy — which is why the verdict
    reads the COUNT. This test and `test_required_checks_present_is_not_drift`
    differ in exactly one field.
    """
    rc, out = _protect(fleet, "--no-remote", gh="true 0")
    assert rc == 24, f"the deleted-sub-resource shape did not fire rc 24: {rc}\n{out}"
    assert "protected=true" in out, out
    # And it must hand over the repair that WORKS. PATCH cannot restore a
    # deleted sub-resource; the break-glass that produced this incident failed
    # for exactly that reason, from a trap that ran.
    assert "PATCH CANNOT" in out, out
    assert "PUT" in out, out


# --- the positive control --------------------------------------------------- #
def test_required_checks_present_is_not_drift(fleet):
    """🔴 REPORT THIS ALONGSIDE THE REDS. An arm that can only ever say "fine"
    is indistinguishable from this, and an arm wired to nothing says it too."""
    rc, out = _protect(fleet, "--no-remote", gh="true 2")
    assert rc == 0, f"a protected main was reported as drift: {rc}\n{out}"
    assert "2 required status check(s)" in out, out
    assert "DRIFT" not in out, out


# --- the could-not-measure family: every one of these must NOT be rc 24 ----- #
def test_no_gh_at_all_is_could_not_measure_not_a_pass(fleet):
    rc, out = _protect(fleet, "--no-remote", gh=None)
    assert rc == 0, out
    assert "[protect] COULD NOT MEASURE" in out, out
    assert "no usable `gh`" in out, out
    assert "NOT 'main is protected'" in out, out


def test_a_gh_that_ANSWERS_NOTHING_is_refused_rather_than_read_as_zero(fleet):
    """🔴 THE LOAD-BEARING ONE. `gh` with no credentials prints NOTHING. An
    empty string read positionally yields a count of 0, and 0 is the DRIFT
    value — so the failure mode of a broken instrument is a confident finding
    about a healthy repo, fired on every timer run forever. That is the
    permanently-red gate `claude/RULES.md` refuses, arrived at from the other
    direction.
    """
    rc, out = _protect(fleet, "--no-remote", gh="", gh_rc=1)
    assert rc != 24, (
        "an EMPTY gh answer was read as a count of zero and fired rc 24\n" + out
    )
    assert rc == 0, out
    assert "[protect] COULD NOT MEASURE" in out, out
    assert "parses as a count of" in out, out


@pytest.mark.parametrize("payload", [
    "true",             # one field: both expansions fall back to the whole string
    "true 2 extra",     # three fields
    "yes 0",            # first field is not a boolean — an API shape change
    "true many",        # count is not a number
    "  ",               # whitespace only
])
def test_a_malformed_answer_is_could_not_measure(fleet, payload):
    """The contract is exactly `<true|false> <digits>`. Anything else is a
    reason, never a verdict — including `true` alone, which without the
    field-count check sets BOTH fields from the same token and renders a
    well-formed-looking measurement out of one value read twice.
    """
    rc, out = _protect(fleet, "--no-remote", gh=payload)
    assert rc != 24, f"a malformed answer {payload!r} fired rc 24\n{out}"
    assert rc == 0, out
    assert "[protect] COULD NOT MEASURE" in out, out


def test_a_non_github_origin_is_could_not_measure_not_a_pass(fleet):
    """The suite's own origin is a local path. The arm must say so rather than
    report a clean protection state for a remote that has no such API — and it
    must not echo the URL, because this repo is public and an origin can name a
    private host."""
    fleet.catch_up()
    rc, out = fleet.check("--no-remote")          # no DRIFT_PROTECT_SLUG
    assert rc == 0, out
    assert "[protect] COULD NOT MEASURE" in out, out
    assert "not a github.com owner/repo remote" in out, out
    assert str(fleet.origin) not in out, "the origin URL was echoed into the report\n" + out


# --- the derivation, which the override above bypasses ---------------------- #
@pytest.mark.parametrize("url,slug", [
    ("git@github.com:alpha-owner/bravo-repo.git", "alpha-owner/bravo-repo"),
    ("https://github.com/charlie-owner/delta-repo.git", "charlie-owner/delta-repo"),
    ("https://github.com/echo-owner/foxtrot-repo", "echo-owner/foxtrot-repo"),
])
def test_the_slug_is_derived_from_the_origin_remote(fleet, url, slug):
    """🔴 THE OVERRIDE THE OTHER TESTS USE MAKES THE DERIVATION UNTESTED, so it
    is tested here — against the real URL spellings `git remote -v` produces,
    ssh and https, with and without `.git`.

    🔴 `GIT_ALLOW_PROTOCOL=file` keeps the git leg offline: origin now names
    github.com, and without it the fetch would leave the machine. It refuses
    BOTH ssh and https (measured: `fatal: transport 'ssh' not allowed` in 7ms)
    while `ls-remote --get-url` still resolves, because that is a config
    expansion and opens no transport — which is exactly the pairing this test
    needs. NOT `GIT_SSH_COMMAND`: that covers only the ssh spelling, and
    `test_push_keepalive.py` requires every such export to carry
    ServerAliveInterval, which is meaningless on a command that never connects.

    The fetch failing is expected and is not what is asserted — rc 24 (severity
    68) outranks rc 4 (55), so the arm's finding is still the verdict.
    """
    fleet.catch_up()
    fleet.set_origin(url)
    log = fleet.root / "gh-argv.log"
    fleet.stub_gh("false 0", log=log)
    rc, out = fleet.check("--no-remote", GIT_ALLOW_PROTOCOL="file")
    assert rc == 24, f"{url} did not reach the arm: {rc}\n{out}"
    assert slug in out, f"{url} did not resolve to {slug}\n{out}"
    argv = log.read_text()
    assert f"repos/{slug}/branches/main" in argv, (
        f"gh was asked about the wrong repo for {url}: {argv!r}"
    )


@pytest.mark.parametrize("url", [
    "git@gitlab.com:owner/repo.git",
    "https://github.com.evil.example/owner/repo.git",   # host is NOT github.com
    "git@github.com:owner.git",                         # no owner/repo pair
    "https://github.com/owner/group/repo.git",          # three components
    "https://github.com/owner/",                        # empty repo component
])
def test_a_url_that_is_not_an_owner_repo_pair_is_refused_not_guessed(fleet, url):
    """🔴 FAILS CLOSED. A best-guess slug would be queried, 404, and land in
    could-not-measure wearing a subject nobody chose — a wrong repo name in the
    journal is worse than an honest refusal, because it reads as a measurement.
    """
    fleet.catch_up()
    fleet.set_origin(url)
    log = fleet.root / "gh-argv.log"
    fleet.stub_gh("false 0", log=log)
    rc, out = fleet.check("--no-remote", GIT_ALLOW_PROTOCOL="file")   # see above
    assert rc != 24, f"{url} was turned into a slug and queried\n{out}"
    assert "[protect] COULD NOT MEASURE" in out, out
    assert not log.exists(), f"gh was called for a non-github origin: {log.read_text()!r}"


# --- severity, in both directions ------------------------------------------- #
def test_unpushed_devrc_commits_still_outrank_an_unprotected_main(fleet):
    """rc 8 is work that exists on exactly one machine; this one loses nothing
    and is repaired by one reversible call. Both findings are live here."""
    fleet.catch_up()
    fleet.add_local_commit("commit the workbench never pushed")
    fleet.stub_gh("false 0")
    rc, out = fleet.check("--no-remote", DRIFT_PROTECT_SLUG=BP_SLUG)
    assert rc == 8, f"rc 24 masked the un-pushed commits: {rc}\n{out}"
    assert "ZERO required status checks" in out, "the rc24 finding was lost\n" + out


def test_an_unprotected_main_outranks_a_merely_behind_host(fleet):
    """The fixture clone starts one commit BEHIND (rc 10). A host that just
    needs a ship must not hide a merge gate that is switched off."""
    fleet.stub_gh("false 0")                       # no catch_up: still behind
    rc, out = fleet.check("--no-remote", DRIFT_PROTECT_SLUG=BP_SLUG)
    assert rc == 24, f"rc 10 masked the unprotected main: {rc}\n{out}"
    assert "local main is BEHIND origin/main" in out, "the rc10 finding was lost\n" + out


def test_the_rc24_legend_is_printed_with_the_verdict(fleet):
    """The journal is the only place this output is ever read."""
    rc, out = _protect(fleet, "--no-remote", gh="true 0")
    assert rc == 24, out
    assert "rc24=" in out, out
    assert "drift-check: DRIFT (rc=24)" in out, out


def test_rc24_is_ranked_between_rc8_and_rc17_in_the_severity_table():
    """Asserted against severity() itself, not only through the two behavioural
    orderings above — those pin the pairs they exercise, and the published
    ladder in the header is a claim about ALL of them."""
    body = DRIFT.read_text().split("severity() {", 1)[1].split("\n}", 1)[0]
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("#"))
    ranks = {int(c): int(r) for c, r in re.findall(r"^\s*(\d+)\)\s*echo (\d+)", body, re.M)}
    assert 24 in ranks, "rc 24 is not ranked in severity(); it would fall to 99, above rc 8"
    assert ranks[8] > ranks[24] > ranks[17], (
        "rc 24 is not between rc 8 and rc 17: %r" % {k: ranks[k] for k in (8, 24, 17)}
    )
    # ...and the header's published order must agree with the table it describes.
    header = DRIFT.read_text().split("\nset -", 1)[0]
    assert "8 > 24 > 17" in header, (
        "the header's severity ladder does not place rc 24 where severity() does"
    )


# --- passivity, on a surface the git allowlist cannot see -------------------- #
def test_the_gh_calls_are_read_only():
    """🔴 THE ALLOWLIST GUARDING THIS FILE IS GIT-SHAPED AND `gh` IS NOT GIT.

    `gh api -X DELETE …/branches/main/protection/required_status_checks` is the
    break-glass `devrc/CLAUDE.md` publishes verbatim — a plausible thing for a
    maintainer to paste into the arm that reads this very endpoint — and every
    static check in this suite would pass with it there. A deadman that can
    delete the protection it watches is not a deadman.
    """
    code = "\n".join(
        ln for ln in DRIFT.read_text().splitlines() if not ln.strip().startswith("#")
    )
    calls = [ln for ln in code.splitlines() if re.search(r"\$DRIFT_GH\"?\s", ln)]
    assert calls, "no gh invocation found — this guard is wired to nothing"
    for ln in calls:
        assert not re.search(r"(?<![\w-])-X(?![\w-])", ln), f"gh call sets a method: {ln}"
        assert "--method" not in ln, f"gh call sets a method: {ln}"
        assert not re.search(r"(?<![\w-])-f(?![\w-])", ln), f"gh call sends a body: {ln}"
        assert "--field" not in ln, f"gh call sends a body: {ln}"
        assert not re.search(r"(?<![\w-])--input(?![\w-])", ln), f"gh call sends a body: {ln}"


def test_the_gh_read_only_guard_can_actually_see_a_write(tmp_path):
    """🔴 NEGATIVE CONTROL. The guard above passing is indistinguishable from a
    regex that matches nothing, so watch it go red on the exact line the
    docstring names.
    """
    poisoned = DRIFT.read_text() + (
        '\ntimeout 5 "$DRIFT_GH" api -X DELETE '
        '"repos/$BP_SLUG/branches/main/protection/required_status_checks"\n'
    )
    code = "\n".join(
        ln for ln in poisoned.splitlines() if not ln.strip().startswith("#")
    )
    calls = [ln for ln in code.splitlines() if re.search(r"\$DRIFT_GH\"?\s", ln)]
    offenders = [ln for ln in calls if re.search(r"(?<![\w-])-X(?![\w-])", ln)]
    assert offenders, "the read-only guard cannot see a -X DELETE"
