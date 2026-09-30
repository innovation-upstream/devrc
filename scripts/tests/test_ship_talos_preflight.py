"""ship.sh's PRE-FLIGHT over the two hosts' `~/workspace/homelab-talos` (rc 26).

WHY THIS EXISTS — a switch that fails for a reason that is not in this repo.
`nix/pkgs/tools/clawgatectl.nix` builds clawgatectl from
`${workspace}/homelab-talos/containers/clawgate`, i.e. from the HOST'S OWN
working tree (deliberately; fetching that private repo would put a GitHub
credential in the nix store). Its `vendorHash` is ONE literal, so it can only be
correct for ONE checkout of that repo. MEASURED 2026-09-29, both directions on
one day, same pin and same devrc commit:

    workbench, before devrc #1928, clawgatectl-0.8.65:
      specified: sha256-T2rgqEv5...  got: sha256-lzXHWLLS...
    laptop,     after devrc #1928, clawgatectl-0.8.64:
      specified: sha256-lzXHWLLS...  got: sha256-T2rgqEv5...   (the exact inverse)

The laptop's checkout was 58 commits behind, so it compiled pre-carve source for
which the OLD hash was right. `ship.sh` reported rc 9 (switch-failed) for
whichever host was the odd one out, and the surfaced nix error was
`Cannot build ... Reason: 1 dependency failed` — which names no dependency and
points at nothing. The handoff's workaround was to tell a HUMAN to run
`for h in "" "ssh zach@10.42.0.100"; do $h git -C ~/workspace/homelab-talos
rev-parse --short HEAD; done` first. This module pins the automated version.

🔴 FOUR DECISIONS ARE UNDER TEST, NOT ONE. The refusal on its own is satisfied
by a pre-flight that refuses unconditionally, so the three CONTINUE cases below
are not extras — without them the refusal test is vacuous:

  * two hosts, DIFFERENT heads  -> refuse, rc 26, remedy printed   (the finding)
  * two hosts, SAME head        -> proceed                         (positive control)
  * one host in scope           -> proceed, "NOT COMPARED"         (a one-host run
                                   genuinely cannot compare and must not block)
  * checkout ABSENT / host UNREACHABLE -> proceed, "NOT COMPARED"  (clawgatectl.nix
                                   guards on pathExists, so a host without the
                                   checkout is a documented, tolerated state; and
                                   a dead host is a reachability fact, not a
                                   disagreement. Refusing on either would be the
                                   permanently-red gate claude/RULES.md forbids.)

HERMETIC. Throwaway git repos under tmp_path, `$HOME` redirected at BOTH
fabricated hosts, `GIT_CONFIG_GLOBAL/SYSTEM` redirected, and the remote host
reached through a fake `ssh` that runs the payload locally with the second
host's `$HOME`. The REFUSING `ssh`/`home-manager` stubs from
`test_ship_converge` sit behind that shim on PATH, so nothing here can reach the
operator's real laptop or run a real switch — and `$SHIP_REPO` points at a path
that does not exist, so a run that gets PAST the pre-flight stops at rc 3
instead of converging anything.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SHIP = SCRIPTS / "ship.sh"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# 🔴 write_exec OWNS THE SHEBANG. A stub written `#!/usr/bin/env bash` runs on the
# dev host and ENOENTs in the nix build sandbox (no /usr/bin/env), which is the
# two-tier hazard test_runtime_shebangs.py forbids outright.
from testlib.mockbin import write_exec  # noqa: E402

# 🔴 THE SSH SANDBOX IS IMPORTED, NOT RE-IMPLEMENTED. `test_ship_converge` owns
# the refusing ssh/home-manager pair and the reasoning for it (with REMOTE_SSH
# unset, ship.sh's default target is the OPERATOR'S REAL LAPTOP). A second copy
# here would be the duplicated-predicate shape, wrong at one of the two sites the
# first time either changed.
from test_ship_converge import _write_sandbox_bin  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (SHIP.is_file() and os.access("/bin/sh", os.X_OK)),
    reason="needs ship.sh and a POSIX sh",
)

TALOS_REL = "workspace/homelab-talos"

# 🔴 PINNED LITERALS, TYPED OUT — never built from ship.sh's own source. The
# refusal is asserted as a WHOLE NORMALISED BLOCK rather than by keyword, because
# a guard on a couple of words is walkable by rewording: "print the remedy" has
# to mean the remedy, spelled the way an operator can paste it.
#
# `{lh}` / `{rh}` / `{rel}` are the only interpolations, and the two shas come
# from the fixture's own git history, so nothing here can agree with the
# implementation by construction.
REFUSAL_TEMPLATE = """\
ship: PRE-FLIGHT REFUSED — the two hosts hold DIFFERENT ~/workspace/homelab-talos commits.
local  (workbench) HEAD={lh}
remote (laptop) HEAD={rh}
{rel}
nix/pkgs/tools/clawgatectl.nix builds clawgatectl from that working tree and pins ONE
vendorHash, which can only be right for ONE checkout. The odd-one-out host fails its
vendor derivation with 'Cannot build ... Reason: 1 dependency failed' — naming nothing —
and this script would report it as a bare rc 9 switch-failed.
remedy, on the host named above:
git -C ~/workspace/homelab-talos fetch origin && git -C ~/workspace/homelab-talos merge --ff-only origin/trunk
then re-run ship.
{fleet}
to proceed anyway, converge one host at a time: ship.sh --no-remote / --no-local."""

#: Pass 1 — nothing has been touched, which is what makes rc 26 different from
#: every rc-20 site (those all sit BETWEEN the two legs, so the fleet is provably
#: in two states when one of those fires).
FLEET_GEN0 = "fleet state: NEITHER host was fetched, landed or switched by this run."

#: Pass 2+ — a RE-EXEC, where the claim above would be FALSE: pass 1 has already
#: converged and switched the local host and its rc was discarded by the exec.
#: Notably including the very first run that DELIVERS this pre-flight, whose pass
#: 1 ran the copy of ship.sh that did not have it.
FLEET_GEN1 = (
    "fleet state: this is re-exec generation 1 — pass 1 ALREADY converged\n"
    "local (workbench) (and, unless --no-switch, switched it), and its rc was discarded\n"
    "by the exec. The remote (laptop) was NOT visited: this refusal precedes both legs."
)

REL_REMOTE_BEHIND = (
    "which is behind: REMOTE (laptop) — its HEAD is an ancestor of the local one."
)
REL_LOCAL_BEHIND = (
    "which is behind: LOCAL (workbench) — its HEAD is an ancestor of the remote one."
)
REL_DIVERGED = (
    "which is behind: NEITHER — the two have DIVERGED. A fast-forward cannot fix this one."
)

#: The wording a run that CANNOT compare must use. ship.sh already says
#: `cross-host agreement NOT COMPARED` at four sites in its verdict block; the
#: pre-flight reuses that phrase rather than minting a second one for the same
#: idea, and this constant is what keeps the two spellings from drifting apart.
NOT_COMPARED_PREFIX = (
    "ship: pre-flight — cross-host agreement NOT COMPARED for ~/workspace/homelab-talos: "
)

#: rc 26 is ship.sh's own code for this refusal. Pinned as a literal here on
#: purpose: reading it out of the script would make this test agree with whatever
#: the script does.
RC_PREFLIGHT_REFUSED = 26

#: The banner the local leg prints. Its PRESENCE is how "the run proceeded past
#: the pre-flight" is asserted behaviourally, rather than by the absence of a
#: refusal — an absence is satisfied by a run that died for some other reason.
LOCAL_LEG_BANNER = "=== local (workbench) ==="


def _norm(text: str) -> str:
    """Strip each line and collapse internal whitespace runs; drop blank lines."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", line.strip())
        if line:
            out.append(line)
    return "\n".join(out)


class Fleet:
    """Two fabricated hosts, each with its own `$HOME/workspace/homelab-talos`.

    `origin.git` holds branch `trunk` — homelab-talos's real default branch, and
    the one the printed remedy names. Both hosts start at the SAME commit; the
    tests move one of them.
    """

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.origin = tmp_path / "origin.git"
        self.home = tmp_path / "home"
        self.rhome = tmp_path / "rhome"
        self.gitconfig = tmp_path / "gitconfig"
        self.gitconfig.write_text(
            "[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = trunk\n"
        )
        self.denybin = _write_sandbox_bin(tmp_path / "sandbox-bin")
        for h in (self.home, self.rhome):
            (h / "workspace").mkdir(parents=True)

        self._run(["git", "init", "-q", "--bare", "-b", "trunk", str(self.origin)])
        builder = tmp_path / "builder"
        self._run(["git", "clone", "-q", str(self.origin), str(builder)])
        (builder / "containers").mkdir()
        (builder / "containers" / "clawgate.go").write_text("package main // base\n")
        self._git(builder, "add", "containers/clawgate.go")
        self._git(builder, "commit", "-qm", "base")
        self._git(builder, "push", "-q", "-u", "origin", "trunk")
        self.base = self._git(builder, "rev-parse", "HEAD")
        self.builder = builder

        self.local = self._clone(self.home)
        self.remote = self._clone(self.rhome)

    # -- setup helpers ------------------------------------------------------ #
    def _env(self):
        e = dict(os.environ)
        e.update(
            GIT_CONFIG_GLOBAL=str(self.gitconfig),
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_TERMINAL_PROMPT="0",
        )
        return e

    def _run(self, argv):
        out = subprocess.run(argv, capture_output=True, text=True, env=self._env())
        assert out.returncode == 0, f"setup {argv} failed: {out.stderr}"
        return out.stdout.strip()

    def _git(self, repo: Path, *args):
        return self._run(["git", "-C", str(repo), *args])

    def _clone(self, home: Path) -> Path:
        dest = home / TALOS_REL
        self._run(["git", "clone", "-q", str(self.origin), str(dest)])
        return dest

    def advance_origin(self) -> str:
        """Push one further commit to origin/trunk and return its sha."""
        self._git(self.builder, "pull", "-q", "--ff-only", "origin", "trunk")
        (self.builder / "containers" / "clawgate.go").write_text(
            "package main // carved\n"
        )
        (self.builder / "go.sum").write_text("a new dependency set\n")
        self._git(self.builder, "add", "containers/clawgate.go", "go.sum")
        self._git(self.builder, "commit", "-qm", "carve the module")
        self._git(self.builder, "push", "-q", "origin", "trunk")
        return self._git(self.builder, "rev-parse", "HEAD")

    def pull(self, repo: Path):
        self._git(repo, "fetch", "-q", "origin")
        self._git(repo, "merge", "-q", "--ff-only", "origin/trunk")

    def head(self, repo: Path) -> str:
        return self._git(repo, "rev-parse", "HEAD")

    def commit_locally(self, repo: Path, name: str) -> str:
        """A commit that exists ONLY in `repo` — never pushed, never fetchable.

        This is what makes the "ancestry cannot be tested here" branch real
        rather than synthetic: the 2026-09-29 laptop was 58 commits behind, so
        running FROM it, the workbench's commit genuinely was not in its object
        store.
        """
        (repo / f"{name}.txt").write_text(f"{name}\n")
        self._git(repo, "add", f"{name}.txt")
        self._git(repo, "commit", "-qm", name)
        return self.head(repo)

    def drop_checkout(self, home: Path):
        target = home / TALOS_REL
        assert target.is_dir(), target
        target.rename(home / "workspace" / "moved-away")

    # -- the fake ssh ------------------------------------------------------- #
    def ssh_shim(self, *, dead: bool = False, trailer: str = "") -> Path:
        """`ssh` that runs the piped payload as the SECOND host, or refuses.

        ship.sh sends the probe as `ssh -o ConnectTimeout=10 <target> bash -s`
        with the script on STDIN — never inlined in the command string, because
        `ssh host "<script>"` names no interpreter and sshd would run the
        account's LOGIN SHELL (zsh on both real hosts). The shim reproduces that
        dispatch rather than hardcoding `bash -c`, so the test cannot pass for a
        payload real sshd would hand to a different interpreter.

        `dead=True` is the UNREACHABLE case: ssh's own 255, with ssh's own
        message, and no marker line at all.

        `trailer` appends one more line AFTER the payload's own output. The
        capture is `2>&1`, so whatever the far side and ssh itself write lands in
        it — a banner, an MOTD, a `Connection to ... closed`, git's stderr. None
        of that is under this script's control, which is why the marker parser is
        anchored on the WHOLE line.
        """
        d = self.root / "ssh-shim"
        d.mkdir(exist_ok=True)
        if dead:
            write_exec(
                d / "ssh",
                'echo "ssh: connect to host port 22: No route to host" >&2\n'
                "exit 255\n",
            )
            return d
        write_exec(
            d / "ssh",
            "prev=; last=\n"
            'for a in "$@"; do prev=$last; last=$a; done\n'
            'if [ "$prev" = bash ] && [ "$last" = -s ]; then\n'
            "  payload=$(cat)\n"
            "  runner=bash\n"
            "else\n"
            "  payload=$last\n"
            "  runner=zsh\n"
            "fi\n"
            f'export HOME="{self.rhome}"\n'
            f'export GIT_CONFIG_GLOBAL="{self.gitconfig}"\n'
            "export GIT_CONFIG_SYSTEM=/dev/null\n"
            + ('exec "$runner" -c "$payload"\n' if not trailer else
               '"$runner" -c "$payload"; rc=$?\n'
               f'printf "%s\\n" {trailer!r}\n'
               "exit $rc\n"),
        )
        return d

    # -- the run ------------------------------------------------------------ #
    def ship(self, *args, dead_remote: bool = False, trailer: str = "",
             script: Path | None = None, **env_extra):
        """Run ship.sh with both fabricated hosts in scope. Returns (rc, out, err).

        `$SHIP_REPO` names a path that does not exist, so a run that gets past
        the pre-flight halts at rc 3 ("no repo") having converged nothing. That
        is deliberate: it keeps the two outcomes — refused vs proceeded — far
        apart and both cheap.
        """
        shim = self.ssh_shim(dead=dead_remote, trailer=trailer)
        env = dict(os.environ)
        env.update(
            HOME=str(self.home),
            GIT_CONFIG_GLOBAL=str(self.gitconfig),
            GIT_CONFIG_SYSTEM="/dev/null",
            GIT_TERMINAL_PROMPT="0",
            SHIP_ROLE="workbench",
            REMOTE_SSH="fixture@second-host",
            SHIP_REPO=str(self.root / "no-such-devrc"),
            SHIP_NO_SWITCH="1",
        )
        env.pop("XDG_STATE_HOME", None)
        env.pop("LAPTOP_SSH", None)
        env.pop("SHIP_SELF_GEN", None)
        # Applied AFTER the pops, so a test can deliberately SET one of them —
        # $SHIP_SELF_GEN is how the re-exec wording branch is reached.
        env.update(env_extra)
        # 🔴 PATH IS COMPOSED HERE AND ONLY HERE, and `extra` may not override it:
        # that spelling is what silently drops the refusing pair and re-opens the
        # path to the operator's real laptop. Same rule as Repo.env in
        # test_ship_converge, for the same measured reason.
        assert "PATH" not in env_extra, (
            "do not pass PATH= to Fleet.ship — it would place a stub AHEAD of the "
            "refusing ssh/home-manager shims. Nothing here needs to."
        )
        # The shim FIRST, then the refusing ssh/home-manager pair, then the real
        # PATH — the same composition Repo.env enforces in test_ship_converge.
        env["PATH"] = os.pathsep.join(
            [str(shim), str(self.denybin), os.environ["PATH"]]
        )
        proc = subprocess.run(
            ["bash", str(script or SHIP), *args],
            capture_output=True, text=True, env=env, timeout=180,
        )
        return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def fleet(tmp_path):
    return Fleet(tmp_path)


def _assert_refusal(err, *, lh, rh, rel, fleet=FLEET_GEN0):
    """The refusal block, whole and normalised — not a keyword search."""
    lines = err.splitlines()
    starts = [i for i, ln in enumerate(lines) if "PRE-FLIGHT REFUSED" in ln]
    assert len(starts) == 1, (
        f"expected exactly one refusal block on stderr, found {len(starts)}:\n{err}"
    )
    block = "\n".join(lines[starts[0]:])
    want = REFUSAL_TEMPLATE.format(lh=lh, rh=rh, rel=rel, fleet=fleet)
    assert _norm(block) == _norm(want), (
        "the refusal text changed.\n"
        f"--- got ---\n{_norm(block)}\n--- want ---\n{_norm(want)}"
    )


# --------------------------------------------------------------------------- #
# 1. THE FINDING — two hosts, two different homelab-talos commits
# --------------------------------------------------------------------------- #
def test_two_hosts_on_different_homelab_talos_commits_are_REFUSED(fleet):
    """🔴 RED at origin/main, GREEN here. The 2026-09-29 shape: the remote host's
    checkout is behind, so its vendor derivation hashes differently and its
    `home-manager switch` dies with an error that names nothing.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)
    assert fleet.head(fleet.local) == ahead
    behind = fleet.head(fleet.remote)
    assert behind != ahead, "fixture did not produce two different heads"

    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\nstdout:\n{out}\nstderr:\n{err}"
    _assert_refusal(err, lh=ahead, rh=behind, rel=REL_REMOTE_BEHIND)
    assert LOCAL_LEG_BANNER not in out, (
        "the refusal must happen BEFORE either leg — nothing may be converged:\n" + out
    )


def test_the_refusal_names_the_LOCAL_host_when_it_is_the_behind_one(fleet):
    """`merge-base --is-ancestor` runs in both directions, and the operator has
    to be told WHICH machine to sync. Naming the wrong one sends them to fix a
    host that is already correct.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.remote)
    # FETCH but do not merge: the local host has SEEN the commit (so ancestry is
    # testable here) and simply has not landed on it. That is the ordinary shape
    # of "behind"; the object-store-miss variant has its own test below.
    fleet._git(fleet.local, "fetch", "-q", "origin")
    behind_local = fleet.head(fleet.local)
    assert behind_local != ahead

    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(err, lh=behind_local, rh=ahead, rel=REL_LOCAL_BEHIND)


def test_diverged_checkouts_are_reported_as_DIVERGED_not_as_behind(fleet):
    """🔴 BEHIND AND DIVERGED ARE DIFFERENT PROBLEMS. `--ff-only` fixes the first
    and REFUSES on the second, so a message that calls a divergence "behind"
    sends the operator to a command that cannot work.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.remote)
    # The local host commits its own work on top of the OLD base instead.
    side = fleet.commit_locally(fleet.local, "local-only-work")
    # ...and has fetched, so it holds the remote's commit and ancestry IS testable.
    fleet._git(fleet.local, "fetch", "-q", "origin")
    assert side != ahead

    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(err, lh=side, rh=ahead, rel=REL_DIVERGED)


def test_an_untestable_ancestry_says_NOT_DETERMINED_rather_than_guessing(fleet):
    """🔴 A comparison against an absent operand reports an ANSWER, not MISSING.

    If the other host's commit is not in this host's object store,
    `merge-base --is-ancestor` cannot be run at all — and the 2026-09-29 laptop
    was exactly that host. Stating "behind" there would be a guess printed as a
    measurement, so the refusal says NOT DETERMINED and names the sha it could
    not resolve. The refusal itself still stands: the two trees differ, which is
    all the vendorHash cares about.
    """
    unreachable_commit = fleet.commit_locally(fleet.remote, "never-pushed")
    lh = fleet.head(fleet.local)
    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(
        err, lh=lh, rh=unreachable_commit,
        rel=(f"which is behind: NOT DETERMINED — {unreachable_commit} is not in "
             "this host's object store, so ancestry cannot be tested here. "
             "Sync BOTH hosts."),
    )


def test_on_a_RE_EXEC_the_refusal_does_not_claim_nothing_was_touched(fleet):
    """🔴 "NEITHER host was touched" is TRUE ON PASS 1 ONLY.

    ship.sh re-execs itself when its own fast-forward replaced it, and by then
    pass 1 has converged and — unless --no-switch — switched the local host,
    with its rc DISCARDED by the exec. That includes the very first run to
    deliver this pre-flight: its pass 1 ran the copy of ship.sh that did not
    have one. Printing "nothing was touched" there would be a false claim in the
    single line an operator reads to decide whether to worry, which is the shape
    this file's whole rc-20 section exists to reject.

    Driven through $SHIP_SELF_GEN — the same variable the re-exec sets and
    exports — rather than by staging a real supersession, because the branch
    under test is the WORDING, not the exec.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)
    behind = fleet.head(fleet.remote)

    rc, out, err = fleet.ship(SHIP_SELF_GEN="1")
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(err, lh=ahead, rh=behind, rel=REL_REMOTE_BEHIND,
                    fleet=FLEET_GEN1)
    assert "NEITHER host was fetched" not in err, (
        "a re-exec generation still claimed nothing had been touched:\n" + err
    )


# --------------------------------------------------------------------------- #
# 2. CONTROL — agreeing hosts PROCEED (without this, test 1 is vacuous)
# --------------------------------------------------------------------------- #
def test_two_hosts_holding_the_SAME_commit_proceed(fleet):
    """🔴 THE POSITIVE CONTROL. A pre-flight that refused unconditionally passes
    every test above; this is the one it cannot pass. Both hosts agree, so the
    run must continue into its legs and must NOT return rc 26.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)
    fleet.pull(fleet.remote)
    assert fleet.head(fleet.local) == fleet.head(fleet.remote) == ahead

    rc, out, err = fleet.ship()
    assert rc != RC_PREFLIGHT_REFUSED, f"agreeing hosts were refused:\n{out}\n{err}"
    assert f"ship: pre-flight — 2 hosts compared, both hold homelab-talos at {ahead}." in out, out
    # 🔴 Scoped to the PRE-FLIGHT's own phrase, not a bare "NOT COMPARED" grep:
    # the verdict block legitimately prints `cross-host agreement NOT COMPARED`
    # about DEVRC commits here, because $SHIP_REPO does not exist and neither leg
    # emits a landed sha. Two different claims, one shared phrase.
    assert NOT_COMPARED_PREFIX not in out, (
        "two hosts WERE compared; saying otherwise understates what was proven:\n" + out
    )
    assert LOCAL_LEG_BANNER in out, (
        "the run did not reach its local leg, so 'proceeded' is unproven:\n" + out
    )


# --------------------------------------------------------------------------- #
# 3. CONTROL — a ONE-HOST run must not refuse, and must say NOT COMPARED
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag,label", [("--no-remote", "local"), ("--no-local", "remote")])
def test_a_single_host_run_is_NOT_COMPARED_and_is_not_refused(fleet, flag, label):
    """🔴 A pre-flight that blocked the legitimate single-host path would be worse
    than no pre-flight: `--no-remote` is the documented way to converge one
    machine, and it is what the refusal above tells the operator to fall back to.
    One host in scope genuinely CANNOT compare two checkouts, so it degrades to
    ship.sh's own existing `cross-host agreement NOT COMPARED` wording.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)          # the hosts DO disagree...
    assert fleet.head(fleet.remote) != ahead

    rc, out, err = fleet.ship(flag)  # ...and it still must not refuse
    assert rc != RC_PREFLIGHT_REFUSED, f"a one-host run was refused:\n{out}\n{err}"
    assert NOT_COMPARED_PREFIX + "1 of 2 hosts in scope." in out, out
    assert "PRE-FLIGHT REFUSED" not in err, err


# --------------------------------------------------------------------------- #
# 4. CONTROL — absent checkout / unreachable host are NOT disagreements
# --------------------------------------------------------------------------- #
def test_a_remote_host_without_the_checkout_is_NOT_COMPARED(fleet):
    """clawgatectl.nix guards on `pathExists` precisely so a host without
    homelab-talos omits the binary instead of failing its whole switch. That host
    is a documented, tolerated state — escalating on it would make ship.sh
    permanently unusable from any machine that does not carry the checkout.
    """
    fleet.advance_origin()
    fleet.pull(fleet.local)
    fleet.drop_checkout(fleet.rhome)

    rc, out, err = fleet.ship()
    assert rc != RC_PREFLIGHT_REFUSED, f"an absent checkout was refused:\n{out}\n{err}"
    assert NOT_COMPARED_PREFIX + (
        "remote (laptop at fixture@second-host) reported ABSENT."
    ) in out, out
    assert LOCAL_LEG_BANNER in out, out


def test_a_LOCAL_host_without_the_checkout_is_NOT_COMPARED_and_costs_no_ssh(fleet):
    """🔴 AND IT SHORT-CIRCUITS. One side missing already decides the outcome, so
    probing the far host buys nothing and costs an extra ssh connection before
    the legs — the same objection ship.sh's address probe is gated on, where one
    measured run of it moved the very window
    `test_a_merge_landing_between_the_two_fetches_is_not_convergence` exists to
    detect. Asserted by making `ssh` FATAL: if the pre-flight reached it, the
    run would report UNREACHABLE instead of the local host's own ABSENT.
    """
    fleet.drop_checkout(fleet.home)
    rc, out, err = fleet.ship(dead_remote=True)
    assert rc != RC_PREFLIGHT_REFUSED, f"{out}\n{err}"
    assert NOT_COMPARED_PREFIX + "local (workbench) reported ABSENT." in out, out
    assert "UNREACHABLE" not in out, (
        "the remote host was probed even though the local side had already "
        "decided the outcome:\n" + out
    )


def test_an_unreachable_remote_host_is_NOT_COMPARED_not_a_disagreement(fleet):
    """🔴 THREE OUTCOMES, THREE WORDS. "we could not look" must never read as
    either a pass or a divergence: the laptop is routinely off-LAN, and a
    pre-flight that called a dead host a disagreement would refuse every run
    made while it is away — a permanently-red gate on the tool you reach for
    when a host is already broken.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)
    assert fleet.head(fleet.remote) != ahead   # they really DO disagree

    rc, out, err = fleet.ship(dead_remote=True)
    assert rc != RC_PREFLIGHT_REFUSED, f"an unreachable host was refused:\n{out}\n{err}"
    assert NOT_COMPARED_PREFIX + (
        "remote (laptop at fixture@second-host) reported UNREACHABLE."
    ) in out, out
    assert "PRE-FLIGHT REFUSED" not in err, err


def test_an_unreadable_checkout_is_NOT_COMPARED_not_a_disagreement(fleet):
    """A directory that exists with a `.git` git cannot read — a half-finished
    clone, a corrupted object store. `rev-parse HEAD` fails, and the honest
    report is UNREADABLE, which is neither ABSENT nor a sha.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)
    dotgit = fleet.remote / ".git"
    (dotgit / "HEAD").write_text("this is not a ref\n")

    rc, out, err = fleet.ship()
    assert rc != RC_PREFLIGHT_REFUSED, f"{out}\n{err}"
    assert NOT_COMPARED_PREFIX + (
        "remote (laptop at fixture@second-host) reported UNREADABLE."
    ) in out, out


# --------------------------------------------------------------------------- #
# 4b. The marker parser is anchored on the WHOLE line
# --------------------------------------------------------------------------- #
#: A 40-hex value that is NEITHER host's HEAD — so if the parser ever reads it,
#: the run sees a disagreement that does not exist. Deliberately distinct from
#: every sha the fixture can produce: a decoy that could coincide with the real
#: answer cannot detect anything.
DECOY_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_a_stray_line_quoting_the_marker_is_not_read_as_the_answer(fleet):
    """🔴 The probe output is captured with `2>&1` from ANOTHER MACHINE, so it
    carries whatever ssh and the far host put there — a banner, an MOTD, git's
    stderr. Reading one of those as the answer produces a CONFIDENT WRONG verdict
    (a refusal naming a commit no host is on), which is strictly worse than a
    missing one, because the missing case already degrades to NOT COMPARED.

    Both hosts genuinely agree here, so the only way to get a refusal is to have
    believed the decoy. Measured: this is the case that kills an unanchored
    marker pattern — a mutant dropping the `^`/`$` anchors SURVIVED the rest of
    this file.
    """
    ahead = fleet.advance_origin()
    fleet.pull(fleet.local)
    fleet.pull(fleet.remote)

    rc, out, err = fleet.ship(
        trailer=f"remote: warning: cached note: ship-talos-head {DECOY_SHA}"
    )
    assert rc != RC_PREFLIGHT_REFUSED, (
        "a stray line was read as the remote host's HEAD:\n" + out + err
    )
    assert f"ship: pre-flight — 2 hosts compared, both hold homelab-talos at {ahead}." in out, out
    # 🔴 Scoped to the PRE-FLIGHT's own lines. The decoy is emitted on EVERY ssh
    # call, so it legitimately appears again inside the remote CONVERGE leg's
    # tee'd output; a bare `DECOY_SHA not in out` would fail for that instead.
    preflight_lines = [ln for ln in out.splitlines() if ln.startswith("ship: pre-flight")]
    assert preflight_lines, out
    assert DECOY_SHA not in "\n".join(preflight_lines), preflight_lines


# --------------------------------------------------------------------------- #
# 5. The rc is in BOTH published ladders (the code, the header, the legend)
# --------------------------------------------------------------------------- #
def test_rc26_is_documented_in_the_header_and_the_legend():
    """🔴 An undocumented rc is an operator staring at a bare number.

    `test_ship_converge.py::test_every_exit_code_ship_can_return_is_documented_
    in_the_header_and_the_legend` already enforces this for every code it can
    parse. This asserts the specific pair for 26, so the intent is legible here
    too rather than only as a side effect of that scan — and it names the
    REMEDY, which the scan cannot see.
    """
    src = SHIP.read_text()
    header = src.split("set -uo pipefail", 1)[0]
    assert re.search(r"^#\s+26\s", header, re.M), (
        "rc 26 is not in ship.sh's 'Exit codes:' header block"
    )
    legend = src.split('echo "ship: incomplete', 1)[1]
    assert "rc26=" in legend, "rc 26 is not in the rc legend printed on failure"
    assert "merge --ff-only origin/trunk" in src, (
        "the printed remedy no longer names the ff-only sync, which is the ONE "
        "command that fixes a behind checkout without touching anything else"
    )
