"""ship.sh's PRE-FLIGHT over the two hosts' clawgate MODULE FILES (rc 26).

WHY THIS EXISTS — a switch that fails for a reason that is not in this repo.
`nix/pkgs/tools/clawgatectl.nix` builds clawgatectl from
`${workspace}/homelab-talos/containers/clawgate`, i.e. from the HOST'S OWN
working tree (deliberately; fetching that private repo would put a GitHub
credential in the nix store). Its `vendorHash` is a single literal pinning the
OUTPUT of the vendor derivation, and that output is decided by `go.mod` and
`go.sum`. MEASURED 2026-09-29, both directions on one day:

    workbench, before devrc #1928, clawgatectl-0.8.65:
      specified: sha256-T2rgqEv5...  got: sha256-lzXHWLLS...
    laptop,     after devrc #1928, clawgatectl-0.8.64:
      specified: sha256-lzXHWLLS...  got: sha256-T2rgqEv5...   (the exact inverse)

The laptop's checkout was 58 commits behind, so it compiled pre-carve source for
which the OLD hash was right. `ship.sh` reported a bare rc 9 (switch-failed) and
the surfaced nix error was `Cannot build ... Reason: 1 dependency failed` — which
names no dependency and points at nothing.

🔴 THE PREDICATE IS THE FILES' CONTENT, READ OFF DISK — NOT THE COMMIT, and this
module exists mostly to pin that distinction. #1934 shipped a `git rev-parse
HEAD` comparison, which was wrong in BOTH directions:

  * UNSOUND — the derivation reads the WORKING TREE, and both hosts'
    homelab-talos are routinely dirty (drift-check.sh reports DIRTY on every
    present one for exactly this reason). Two hosts on the SAME commit with a
    different UNCOMMITTED `go.mod`/`go.sum` hash differently, so the check
    printed "2 hosts compared, both hold <sha>", proceeded, and the switch failed
    with the very error it exists to prevent. `test_same_commit_with_a_DIRTY_*`
    below is that hole.
  * IMPRECISE — a `claudedocs/` edit moves HEAD and cannot move vendorHash, and
    the old check refused on it. homelab-talos took 98 commits in 14 days of
    which only 32 touched containers/clawgate (drift-check's own measurement), so
    most refusals were churn, and churn is what trains an operator to work around
    a gate. `test_different_commits_with_identical_module_files_*` is that half.

🔴 SIX DECISIONS ARE UNDER TEST, NOT ONE. A refusal test alone is satisfied by a
pre-flight that refuses unconditionally, and a "proceeds" test alone by one wired
to nothing, so none of these is an extra:

  * module files differ (committed)   -> refuse, rc 26, ff-only remedy
  * module files differ (UNCOMMITTED) -> refuse, rc 26, and the remedy says a
                                         fast-forward CANNOT fix it
  * module files identical, DIFFERENT commits -> proceed  (precision)
  * module files identical, same commit       -> proceed  (positive control)
  * one host in scope                  -> proceed, "NOT COMPARED"
  * ABSENT / UNREACHABLE / UNREADABLE  -> proceed, "NOT COMPARED"

HERMETIC. Throwaway git repos under `tmp_path`, `$HOME` redirected at BOTH
fabricated hosts, `GIT_CONFIG_GLOBAL/SYSTEM` redirected, and the remote host
reached through a fake `ssh` that runs the payload locally with the second host's
`$HOME`. The REFUSING `ssh`/`home-manager` stubs from `test_ship_converge` sit
behind that shim on PATH, so nothing here can reach the operator's real laptop or
run a real switch — and `$SHIP_REPO` points at a path that does not exist, so a
run that gets PAST the pre-flight stops at rc 3 instead of converging anything.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parent
SHIP = SCRIPTS / "ship.sh"
NIX_PKGS = REPO_ROOT / "nix" / "pkgs"

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
MOD_REL = "containers/clawgate"

# 🔴 PINNED LITERALS, TYPED OUT — never built from ship.sh's own source. The
# refusal is asserted as a WHOLE NORMALISED BLOCK rather than by keyword, because
# a guard on a couple of words is walkable by rewording: "print the remedy" has to
# mean the remedy, spelled the way an operator can paste it.
#
# The only interpolations are digests and shas taken from the FIXTURE's own files
# and git history — sha256 is an external standard, not the implementation under
# test — so nothing here can agree with ship.sh by construction.
REFUSAL_TEMPLATE = """\
ship: PRE-FLIGHT REFUSED — the two hosts' containers/clawgate {diff} differ.
local  (workbench) go.mod={lmod} go.sum={lsum} HEAD={lh}
remote (laptop) go.mod={rmod} go.sum={rsum} HEAD={rh}
nix/pkgs/tools/clawgatectl.nix builds clawgatectl from that working tree and pins ONE
vendorHash, which those files decide. The odd-one-out host fails its vendor derivation
with 'Cannot build ... Reason: 1 dependency failed' — naming nothing — and this script
would report it as a bare rc 9 switch-failed.
{remedy}
then re-run ship.
{fleet}
to proceed anyway, converge one host at a time: ship.sh --no-remote / --no-local."""

#: 🔴 THE UNCOMMITTED REMEDY, and it is a DIFFERENT remedy, not a reworded one.
#: Both hosts are on the same commit, so `merge --ff-only` is a no-op: printing it
#: would send the operator to a command that reports success and changes nothing.
REMEDY_UNCOMMITTED = """\
both hosts are on the SAME commit {sha}, so this difference is UNCOMMITTED
(or untracked) and a fetch + fast-forward CANNOT fix it. On each host:
git -C ~/workspace/homelab-talos status --porcelain -- containers/clawgate/go.mod containers/clawgate/go.sum
then commit the edit you want to keep, or discard it:
git -C ~/workspace/homelab-talos checkout -- containers/clawgate/go.mod containers/clawgate/go.sum"""

#: The committed-drift remedy: a fast-forward, plus the reminder that a dirty file
#: can also contribute (which is the case above, reachable from here too).
REMEDY_COMMITTED = """\
{rel}
remedy, on the host named above:
git -C ~/workspace/homelab-talos fetch origin && git -C ~/workspace/homelab-talos merge --ff-only origin/trunk
if it still differs afterwards the edit is UNCOMMITTED on one host; commit or discard it."""

REL_REMOTE_BEHIND = (
    "which is behind: REMOTE (laptop) — its HEAD is an ancestor of the local one."
)
REL_LOCAL_BEHIND = (
    "which is behind: LOCAL (workbench) — its HEAD is an ancestor of the remote one."
)
REL_DIVERGED = (
    "which is behind: NEITHER — the two have DIVERGED. A fast-forward cannot fix this one."
)

#: Pass 1 — nothing has been touched, which is what makes rc 26 different from
#: every rc-20 site (those all sit BETWEEN the two legs, so the fleet is provably
#: in two states when one of those fires).
FLEET_GEN0 = "fleet state: NEITHER host was fetched, landed or switched by this run."

#: Pass 2+ — a RE-EXEC, where the claim above would be FALSE: pass 1 has already
#: converged and switched the local host and its rc was discarded by the exec.
FLEET_GEN1 = (
    "fleet state: this is re-exec generation 1 — pass 1 ALREADY converged\n"
    "local (workbench) (and, unless --no-switch, switched it), and its rc was discarded\n"
    "by the exec. The remote (laptop) was NOT visited: this refusal precedes both legs."
)

#: The wording a run that CANNOT compare must use. ship.sh already says
#: `cross-host agreement NOT COMPARED` at four sites in its verdict block; the
#: pre-flight reuses that phrase rather than minting a second one for the same
#: idea, and this constant is what keeps the two spellings from drifting apart.
NOT_COMPARED_PREFIX = (
    "ship: pre-flight — cross-host agreement NOT COMPARED for "
    "containers/clawgate go.mod+go.sum: "
)

#: The line a run that DID compare and agreed must print.
AGREED_PREFIX = "ship: pre-flight — 2 hosts compared, identical containers/clawgate go.mod+go.sum"

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


def _dig12(path: Path) -> str:
    """The first 12 hex chars of the file's sha256 — what ship.sh prints.

    Computed with `hashlib`, i.e. against the sha256 STANDARD, never by asking
    ship.sh what it thinks the hash is.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


class Fleet:
    """Two fabricated hosts, each with its own `$HOME/workspace/homelab-talos`.

    `origin.git` holds branch `trunk` — homelab-talos's real default branch, and
    the one the printed remedy names. The seeded tree carries the two shapes the
    predicate has to tell apart:

        containers/clawgate/go.mod   } the files that DECIDE vendorHash
        containers/clawgate/go.sum   }
        containers/clawgate/main.go  — in the src, but only forces a REBUILD
        claudedocs/notes.md          — cannot reach the build at all

    Both hosts start byte-identical; each test moves one of them.
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
        (builder / MOD_REL).mkdir(parents=True)
        (builder / "claudedocs").mkdir()
        # 🔴 PAIRWISE-DISTINCT CONTENT, and distinct from every constant the
        # assertions name: a fixture whose files could hash to the same value
        # cannot see a mutant that compares the wrong one.
        (builder / MOD_REL / "go.mod").write_text("module clawgate\n\ngo 1.24\n")
        (builder / MOD_REL / "go.sum").write_text("example.com/one v1.0.0 h1:aaaa\n")
        (builder / MOD_REL / "main.go").write_text("package main // base\n")
        (builder / "claudedocs" / "notes.md").write_text("base notes\n")
        self._git(builder, "add", MOD_REL, "claudedocs")
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

    # -- moving the fleet --------------------------------------------------- #
    def advance_origin(self, *, module: bool) -> str:
        """Push one further commit to origin/trunk. Returns its sha.

        `module=True` carves the module files — the divergence that can actually
        break a build. `module=False` touches `claudedocs/` only, which moves HEAD
        and cannot move `vendorHash`: the precision case.
        """
        self._git(self.builder, "pull", "-q", "--ff-only", "origin", "trunk")
        if module:
            (self.builder / MOD_REL / "go.mod").write_text("module clawgate\n\ngo 1.25\n")
            (self.builder / MOD_REL / "go.sum").write_text(
                "example.com/one v1.0.0 h1:aaaa\nexample.com/two v2.0.0 h1:bbbb\n"
            )
            paths = [f"{MOD_REL}/go.mod", f"{MOD_REL}/go.sum"]
            msg = "carve the module"
        else:
            (self.builder / "claudedocs" / "notes.md").write_text("base notes\nmore\n")
            (self.builder / MOD_REL / "main.go").write_text("package main // reworded\n")
            paths = ["claudedocs/notes.md", f"{MOD_REL}/main.go"]
            msg = "docs and a comment — nothing vendorHash can see"
        self._git(self.builder, "add", *paths)
        self._git(self.builder, "commit", "-qm", msg)
        self._git(self.builder, "push", "-q", "origin", "trunk")
        return self._git(self.builder, "rev-parse", "HEAD")

    def pull(self, repo: Path):
        self._git(repo, "fetch", "-q", "origin")
        self._git(repo, "merge", "-q", "--ff-only", "origin/trunk")

    def fetch(self, repo: Path):
        """Fetch WITHOUT merging: the host has SEEN the commit, not landed on it."""
        self._git(repo, "fetch", "-q", "origin")

    def head(self, repo: Path) -> str:
        return self._git(repo, "rev-parse", "HEAD")

    def dirty_module(self, repo: Path, name: str, text: str) -> None:
        """Edit a module file IN THE WORKING TREE and never commit it.

        🔴 THE SOUNDNESS CASE, and it is the ordinary state of these checkouts,
        not a contrived one: drift-check.sh reports DIRTY on every present
        homelab-talos precisely because they are routinely edited in place, and
        `cleanSource` carries a dirty file straight into the derivation.
        """
        p = repo / MOD_REL / name
        assert p.is_file(), p
        p.write_text(text)
        assert self._git(repo, "status", "--porcelain", "--", f"{MOD_REL}/{name}"), (
            "fixture did not actually dirty the tree"
        )

    def commit_module_locally(self, repo: Path, text: str) -> str:
        """A module carve that exists ONLY in `repo` — never pushed, unfetchable.

        This is what makes the "ancestry cannot be tested here" branch real
        rather than synthetic: the 2026-09-29 laptop was 58 commits behind, so
        running FROM it, the workbench's commit genuinely was not in its object
        store.
        """
        (repo / MOD_REL / "go.mod").write_text(text)
        self._git(repo, "add", f"{MOD_REL}/go.mod")
        self._git(repo, "commit", "-qm", "a local-only carve")
        return self.head(repo)

    def drop_checkout(self, home: Path):
        target = home / TALOS_REL
        assert target.is_dir(), target
        target.rename(home / "workspace" / "moved-away")

    def drop_module_file(self, repo: Path, name: str):
        p = repo / MOD_REL / name
        assert p.is_file(), p
        p.unlink()

    def make_module_file_unreadable(self, repo: Path, name: str):
        """chmod 000 — present, so not ABSENT, but no digest can be taken.

        🔴 SELF-CHECKED. Running as root would defeat the chmod and the test would
        pass while producing a state it never created; the assertion below makes
        that loud instead. (Nix builds run as an unprivileged build user, so both
        tiers reach the real state.)
        """
        p = repo / MOD_REL / name
        assert p.is_file(), p
        p.chmod(0o000)
        assert not os.access(p, os.R_OK), (
            f"{p} is still readable after chmod 000 — running as root? This "
            f"fixture cannot produce the UNREADABLE state here."
        )

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
        env["PATH"] = os.pathsep.join(
            [str(shim), str(self.denybin), os.environ["PATH"]]
        )
        proc = subprocess.run(
            ["bash", str(script or SHIP), *args],
            capture_output=True, text=True, env=env, timeout=180,
        )
        return proc.returncode, proc.stdout, proc.stderr

    # -- what the message should say --------------------------------------- #
    def digests(self, repo: Path) -> tuple[str, str]:
        return (_dig12(repo / MOD_REL / "go.mod"),
                _dig12(repo / MOD_REL / "go.sum"))


@pytest.fixture
def fleet(tmp_path):
    return Fleet(tmp_path)


def _assert_refusal(f, err, *, diff, remedy, fleet_state=FLEET_GEN0):
    """The refusal block, whole and normalised — not a keyword search.

    Digests and shas are read off the FIXTURE, so the expectation is derived from
    the files on disk rather than from ship.sh's opinion of them.
    """
    lines = err.splitlines()
    starts = [i for i, ln in enumerate(lines) if "PRE-FLIGHT REFUSED" in ln]
    assert len(starts) == 1, (
        f"expected exactly one refusal block on stderr, found {len(starts)}:\n{err}"
    )
    block = "\n".join(lines[starts[0]:])
    lmod, lsum = f.digests(f.local)
    rmod, rsum = f.digests(f.remote)
    want = REFUSAL_TEMPLATE.format(
        diff=diff, lmod=lmod, lsum=lsum, rmod=rmod, rsum=rsum,
        lh=f.head(f.local), rh=f.head(f.remote),
        remedy=remedy, fleet=fleet_state,
    )
    assert _norm(block) == _norm(want), (
        "the refusal text changed.\n"
        f"--- got ---\n{_norm(block)}\n--- want ---\n{_norm(want)}"
    )


def _preflight_block(out: str) -> str:
    """Only the pre-flight's own stdout lines.

    🔴 Needed because ship.sh's VERDICT block legitimately reuses this
    vocabulary about a different repo: with `$SHIP_REPO` absent, every run here
    also prints `cross-host agreement NOT COMPARED — 0 of 2 hosts reported a
    landed sha` and an rc legend naming `DIFFERENT commits`. A bare substring
    check over the whole output cannot tell the two claims apart.
    """
    keep, block = False, []
    for ln in out.splitlines():
        if ln.startswith("ship: pre-flight") or ln.startswith("ship: PRE-FLIGHT"):
            keep = True
        elif not ln.startswith("  "):
            keep = False
        if keep:
            block.append(ln)
    return "\n".join(block)


def _assert_proceeded(rc, out, err):
    assert rc != RC_PREFLIGHT_REFUSED, f"refused; rc={rc}\n{out}\n{err}"
    assert "PRE-FLIGHT REFUSED" not in err, err
    assert LOCAL_LEG_BANNER in out, (
        "the run did not reach its local leg, so 'proceeded' is unproven:\n" + out
    )


# --------------------------------------------------------------------------- #
# 1. 🔴 THE SOUNDNESS GAP #1934 LEFT OPEN — same commit, DIRTY module file
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name,text", [
    ("go.sum", "example.com/one v1.0.0 h1:aaaa\nexample.com/three v3.0.0 h1:cccc\n"),
    ("go.mod", "module clawgate\n\ngo 1.26\n"),
])
def test_same_commit_with_a_DIRTY_module_file_is_REFUSED(fleet, name, text):
    """🔴 RED at 01d30d10d (#1934's predicate), GREEN here.

    The two hosts hold the SAME commit, so the HEAD comparison #1934 shipped says
    "2 hosts compared, both hold <sha>" and proceeds — and the switch then fails
    with the nameless nix error the pre-flight exists to convert. `cleanSource`
    carries an uncommitted edit straight into the derivation, so this is not an
    edge case; it is the routine state of these two checkouts.

    Both files are parametrised because comparing only one of them is a live
    mutant: a `go.sum`-only divergence is a real dependency-set change and must
    be caught on its own.
    """
    fleet.dirty_module(fleet.remote, name, text)
    same = fleet.head(fleet.local)
    assert same == fleet.head(fleet.remote), "fixture moved a commit"

    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\nstdout:\n{out}\nstderr:\n{err}"
    _assert_refusal(fleet, err, diff=name,
                    remedy=REMEDY_UNCOMMITTED.format(sha=same))
    assert LOCAL_LEG_BANNER not in out, (
        "the refusal must happen BEFORE either leg — nothing may be converged:\n" + out
    )
    # 🔴 The ff-only line must NOT appear: on one commit it is a no-op, so
    # printing it sends the operator to a command that reports success and
    # changes nothing.
    assert "merge --ff-only" not in err, (
        "an UNCOMMITTED divergence was given the fast-forward remedy, which "
        "cannot fix it:\n" + err
    )


# --------------------------------------------------------------------------- #
# 2. 🔴 THE PRECISION GAP — different commits, identical module files
# --------------------------------------------------------------------------- #
def test_different_commits_with_identical_module_files_are_NOT_refused(fleet):
    """🔴 RED at 01d30d10d, GREEN here.

    `claudedocs/` and a reworded comment in `main.go` cannot change `vendorHash`:
    the module files decide it, and a `src` difference makes nix REBUILD, not
    mismatch. #1934's HEAD comparison refused on this, and homelab-talos takes
    ~7 commits a day of which two thirds cannot reach the built subtree — so that
    refusal was churn, and churn is what teaches an operator to bypass a gate.
    """
    ahead = fleet.advance_origin(module=False)
    fleet.pull(fleet.local)
    assert fleet.head(fleet.local) == ahead
    assert fleet.head(fleet.remote) != ahead, "fixture did not split the commits"
    assert fleet.digests(fleet.local) == fleet.digests(fleet.remote), (
        "fixture changed a module file; this test would then be about the other case"
    )

    rc, out, err = fleet.ship()
    _assert_proceeded(rc, out, err)
    assert AGREED_PREFIX in out, out
    # The DIFFERENT commits must be SAID, not silently tolerated: an operator who
    # knows the old behaviour needs to see that this was considered and allowed.
    assert "the two checkouts are at DIFFERENT commits" in out, out
    assert "NOT refused: a difference outside the Go module files cannot change vendorHash" in out, out
    assert NOT_COMPARED_PREFIX not in out, out


# --------------------------------------------------------------------------- #
# 3. COMMITTED module drift — refused, with the fast-forward remedy
# --------------------------------------------------------------------------- #
def test_committed_module_drift_is_REFUSED_with_the_ff_only_remedy(fleet):
    """The 2026-09-29 shape: the remote host's checkout is behind a module carve."""
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)
    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(fleet, err, diff="go.mod and go.sum",
                    remedy=REMEDY_COMMITTED.format(rel=REL_REMOTE_BEHIND))
    assert LOCAL_LEG_BANNER not in out, out


def test_the_refusal_names_the_LOCAL_host_when_it_is_the_behind_one(fleet):
    """`merge-base --is-ancestor` runs in both directions, and the operator has to
    be told WHICH machine to sync. Naming the wrong one sends them to fix a host
    that is already correct.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.remote)
    # FETCH but do not merge: the local host has SEEN the commit (so ancestry is
    # testable here) and simply has not landed on it.
    fleet.fetch(fleet.local)
    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(fleet, err, diff="go.mod and go.sum",
                    remedy=REMEDY_COMMITTED.format(rel=REL_LOCAL_BEHIND))


def test_diverged_checkouts_are_reported_as_DIVERGED_not_as_behind(fleet):
    """🔴 BEHIND AND DIVERGED ARE DIFFERENT PROBLEMS. `--ff-only` fixes the first
    and REFUSES on the second, so a message that calls a divergence "behind"
    sends the operator to a command that cannot work.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.remote)
    # The local host commits its OWN carve on top of the old base instead...
    fleet.commit_module_locally(fleet.local, "module clawgate\n\ngo 1.24 // local\n")
    # ...and has fetched, so it holds the remote's commit and ancestry IS testable.
    fleet.fetch(fleet.local)
    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(fleet, err, diff="go.mod and go.sum",
                    remedy=REMEDY_COMMITTED.format(rel=REL_DIVERGED))


def test_an_untestable_ancestry_says_NOT_DETERMINED_rather_than_guessing(fleet):
    """🔴 A comparison against an absent operand reports an ANSWER, not MISSING.

    If the other host's commit is not in this host's object store,
    `merge-base --is-ancestor` cannot be run at all — and the 2026-09-29 laptop
    was exactly that host. Stating "behind" there would be a guess printed as a
    measurement, so the refusal says NOT DETERMINED and names the sha it could
    not resolve. The refusal itself still stands: the module files differ, which
    is all `vendorHash` cares about.
    """
    unreachable = fleet.commit_module_locally(
        fleet.remote, "module clawgate\n\ngo 1.27 // never pushed\n")
    rc, out, err = fleet.ship()
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(
        fleet, err, diff="go.mod",
        remedy=REMEDY_COMMITTED.format(
            rel=(f"which is behind: NOT DETERMINED — {unreachable} is not in this "
                 "host's object store, so ancestry cannot be tested here. "
                 "Sync BOTH hosts.")),
    )


def test_on_a_RE_EXEC_the_refusal_does_not_claim_nothing_was_touched(fleet):
    """🔴 "NEITHER host was touched" is TRUE ON PASS 1 ONLY.

    ship.sh re-execs itself when its own fast-forward replaced it, and by then
    pass 1 has converged and — unless --no-switch — switched the local host, with
    its rc DISCARDED by the exec. Printing "nothing was touched" there would be a
    false claim in the single line an operator reads to decide whether to worry.

    Driven through $SHIP_SELF_GEN — the same variable the re-exec sets and
    exports — because the branch under test is the WORDING, not the exec.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)
    rc, out, err = fleet.ship(SHIP_SELF_GEN="1")
    assert rc == RC_PREFLIGHT_REFUSED, f"rc={rc}\n{out}\n{err}"
    _assert_refusal(fleet, err, diff="go.mod and go.sum",
                    remedy=REMEDY_COMMITTED.format(rel=REL_REMOTE_BEHIND),
                    fleet_state=FLEET_GEN1)
    assert "NEITHER host was fetched" not in err, (
        "a re-exec generation still claimed nothing had been touched:\n" + err
    )


# --------------------------------------------------------------------------- #
# 4. CONTROL — byte-identical hosts PROCEED (without this, every test above is
#    satisfied by a pre-flight that refuses unconditionally)
# --------------------------------------------------------------------------- #
def test_two_hosts_that_are_byte_identical_proceed(fleet):
    """🔴 THE POSITIVE CONTROL. A pre-flight that refused unconditionally passes
    every refusal test above; this is the one it cannot pass.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)
    fleet.pull(fleet.remote)
    lmod, lsum = fleet.digests(fleet.local)
    assert (lmod, lsum) == fleet.digests(fleet.remote)

    rc, out, err = fleet.ship()
    _assert_proceeded(rc, out, err)
    assert f"{AGREED_PREFIX} (go.mod {lmod})." in out, out
    # 🔴 Scoped to the pre-flight's own block. `DIFFERENT commits` also appears in
    # the rc-19 legend line, which is about devrc and is printed here because
    # $SHIP_REPO does not exist — a bare substring check would fail on that.
    assert "DIFFERENT commits" not in _preflight_block(out), (
        "the hosts are on the same commit; saying otherwise is noise:\n" + out
    )
    assert NOT_COMPARED_PREFIX not in out, (
        "two hosts WERE compared; saying otherwise understates what was proven:\n" + out
    )


# --------------------------------------------------------------------------- #
# 5. CONTROL — a ONE-HOST run must not refuse, and must say NOT COMPARED
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag", ["--no-remote", "--no-local"])
def test_a_single_host_run_is_NOT_COMPARED_and_is_not_refused(fleet, flag):
    """🔴 A pre-flight that blocked the legitimate single-host path would be worse
    than no pre-flight: `--no-remote` is the documented way to converge one
    machine, and it is what the refusal tells the operator to fall back to. One
    host in scope genuinely CANNOT compare two checkouts, so it degrades to
    ship.sh's own existing `cross-host agreement NOT COMPARED` wording.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)          # the hosts DO diverge on the module files...
    assert fleet.digests(fleet.local) != fleet.digests(fleet.remote)

    rc, out, err = fleet.ship(flag)  # ...and it still must not refuse
    assert rc != RC_PREFLIGHT_REFUSED, f"a one-host run was refused:\n{out}\n{err}"
    assert NOT_COMPARED_PREFIX + "1 of 2 hosts in scope." in out, out
    assert "PRE-FLIGHT REFUSED" not in err, err


# --------------------------------------------------------------------------- #
# 6. CONTROL — absent / unreachable / unreadable are NOT disagreements
# --------------------------------------------------------------------------- #
def test_a_remote_host_without_the_checkout_is_NOT_COMPARED(fleet):
    """clawgatectl.nix guards on `pathExists` precisely so a host without
    homelab-talos omits the binary instead of failing its whole switch. That host
    is a documented, tolerated state — escalating on it would make ship.sh
    unusable from any machine that does not carry the checkout.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)
    fleet.drop_checkout(fleet.rhome)

    rc, out, err = fleet.ship()
    _assert_proceeded(rc, out, err)
    assert NOT_COMPARED_PREFIX + (
        "remote (laptop at fixture@second-host) reported go.mod=ABSENT go.sum=ABSENT."
    ) in out, out


def test_a_remote_host_missing_ONLY_go_sum_is_NOT_COMPARED(fleet):
    """One of the two files absent is still "we could not compare", never a
    disagreement — and the message names WHICH, so the operator is not sent to
    look for a whole missing checkout.
    """
    fleet.drop_module_file(fleet.remote, "go.sum")
    lmod, _ = fleet.digests(fleet.local)
    rmod = _dig12(fleet.remote / MOD_REL / "go.mod")

    rc, out, err = fleet.ship()
    _assert_proceeded(rc, out, err)
    assert NOT_COMPARED_PREFIX + (
        f"remote (laptop at fixture@second-host) reported go.mod={rmod} go.sum=ABSENT."
    ) in out, out
    assert lmod == rmod, "fixture changed go.mod too; the message would differ"


def test_a_LOCAL_host_without_the_module_files_is_NOT_COMPARED_and_costs_no_ssh(fleet):
    """🔴 AND IT SHORT-CIRCUITS. One side missing already decides the outcome, so
    probing the far host buys nothing and costs an extra ssh connection before the
    legs — the same objection ship.sh's address probe is gated on, where one
    measured run of it moved the very window
    `test_a_merge_landing_between_the_two_fetches_is_not_convergence` exists to
    detect. Asserted by making `ssh` FATAL: if the pre-flight reached it, the run
    would report UNREACHABLE instead of the local host's own ABSENT.
    """
    fleet.drop_checkout(fleet.home)
    rc, out, err = fleet.ship(dead_remote=True)
    _assert_proceeded(rc, out, err)
    assert NOT_COMPARED_PREFIX + (
        "local (workbench) reported go.mod=ABSENT go.sum=ABSENT."
    ) in out, out
    assert "UNREACHABLE" not in out, (
        "the remote host was probed even though the local side had already "
        "decided the outcome:\n" + out
    )


def test_an_unreachable_remote_host_is_NOT_COMPARED_not_a_disagreement(fleet):
    """🔴 THREE OUTCOMES, THREE WORDS. "we could not look" must never read as
    either a pass or a divergence: the laptop is routinely off-LAN, and a
    pre-flight that called a dead host a disagreement would refuse every run made
    while it is away — a permanently-red gate on the tool you reach for when a
    host is already broken.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)
    assert fleet.digests(fleet.local) != fleet.digests(fleet.remote)

    rc, out, err = fleet.ship(dead_remote=True)
    _assert_proceeded(rc, out, err)
    assert NOT_COMPARED_PREFIX + (
        "remote (laptop at fixture@second-host) reported "
        "go.mod=UNREACHABLE go.sum=UNREACHABLE."
    ) in out, out


def test_an_unreadable_module_file_is_NOT_COMPARED_not_a_disagreement(fleet):
    """A file that is present but yields no digest — unreadable, or a host with no
    `sha256sum`. UNREADABLE is neither ABSENT nor a hash, and it must not be
    compared as though it were one.
    """
    fleet.make_module_file_unreadable(fleet.remote, "go.mod")
    rc, out, err = fleet.ship()
    _assert_proceeded(rc, out, err)
    assert NOT_COMPARED_PREFIX in out, out
    assert "go.mod=UNREADABLE" in out, out


def test_BOTH_hosts_degraded_is_NOT_COMPARED_and_never_reads_as_AGREEMENT(fleet):
    """🔴 A TWO-GATE SEAM: two absences must never compare EQUAL.

    `ship_talos_is_digest` is applied at two call sites — once per host — and the
    hazard lives in the RELATIONSHIP, not in either site. With BOTH gates removed,
    `lmod=ABSENT` and `rmod=ABSENT` compare equal and the run prints `identical
    containers/clawgate go.mod+go.sum (go.mod ABSENT)`: a green computed from two
    absences, which is the reassuring zero in its cross-host spelling. MEASURED:
    removing either gate ALONE does not reach it (the other one still fires), so
    no single-site test can see this and this one is what closes it.

    ⚠ IT DOES *NOT* PROVE THE GUARD IS STRUCTURAL RATHER THAN A WORD LIST — an
    earlier version of this docstring claimed that and the claim was measured
    FALSE. Substituting a literal `ABSENT|UNREADABLE|UNREACHABLE` word list for
    the hex test leaves the whole file green, because those are the only
    non-digest values the probe can emit; the two forms only diverge on a
    vocabulary that does not exist yet. ship.sh says the same thing at the
    function.
    """
    fleet.drop_checkout(fleet.home)
    fleet.drop_checkout(fleet.rhome)
    rc, out, err = fleet.ship()
    _assert_proceeded(rc, out, err)
    assert NOT_COMPARED_PREFIX in out, out
    assert AGREED_PREFIX not in out, (
        "two absences were reported as an AGREEMENT:\n" + out
    )


# --------------------------------------------------------------------------- #
# 7. The marker parser is anchored on the WHOLE line
# --------------------------------------------------------------------------- #
#: A 64-hex value that is NEITHER host's digest — so if the parser ever reads it,
#: the run sees a disagreement that does not exist. Deliberately distinct from
#: every digest the fixture can produce: a decoy that could coincide with the real
#: answer cannot detect anything.
DECOY_DIGEST = "0123456789abcdef" * 4


def test_a_stray_line_quoting_a_marker_is_not_read_as_the_answer(fleet):
    """🔴 The probe output is captured with `2>&1` from ANOTHER MACHINE, so it
    carries whatever ssh and the far host put there — a banner, an MOTD, git's
    stderr. Reading one of those as the answer produces a CONFIDENT WRONG verdict
    (a refusal naming a digest no host holds), which is strictly worse than a
    missing one, because the missing case already degrades to NOT COMPARED.

    Both hosts are byte-identical here, so the only way to get a refusal is to
    have believed the decoy. Measured on the #1934 predicate: a mutant dropping
    the `^`/`$` anchors SURVIVED every other test in the file.
    """
    fleet.advance_origin(module=True)
    fleet.pull(fleet.local)
    fleet.pull(fleet.remote)
    lmod, _ = fleet.digests(fleet.local)

    rc, out, err = fleet.ship(
        trailer=f"remote: warning: cached note: ship-talos-gomod {DECOY_DIGEST}"
    )
    _assert_proceeded(rc, out, err)
    assert f"{AGREED_PREFIX} (go.mod {lmod})." in out, out
    # 🔴 Scoped to the PRE-FLIGHT's own lines. The decoy is emitted on EVERY ssh
    # call, so it legitimately appears again inside the remote CONVERGE leg's
    # tee'd output; a bare `DECOY not in out` would fail for that instead.
    preflight_lines = [ln for ln in out.splitlines() if ln.startswith("ship: pre-flight")]
    assert preflight_lines, out
    assert DECOY_DIGEST[:12] not in "\n".join(preflight_lines), preflight_lines


# --------------------------------------------------------------------------- #
# 8. The SCOPE of the predicate is a measured fact, pinned BOTH ways
# --------------------------------------------------------------------------- #
def _nix_pkg_sources():
    """{file: (has_real_vendorHash, names_a_workspace_src)} over nix/pkgs.

    Comments are truncated at the first `#` before anything is read, the same
    rule drift-check.sh's own nix/pkgs scan uses and for the same reason: these
    files discuss `${workspace}` and `vendorHash` in prose at length, so a
    whole-file grep would "find" sources in the documentation.
    """
    out = {}
    for f in sorted(NIX_PKGS.rglob("*.nix")):
        code = "\n".join(ln.split("#", 1)[0] for ln in f.read_text().splitlines())
        vendor = re.findall(r"vendorHash\s*=\s*(\S+)", code)
        real = [v for v in vendor if not v.startswith("null")]
        out[str(f.relative_to(REPO_ROOT))] = (bool(real), "${workspace}" in code)
    return out


def test_the_predicate_covers_EVERY_package_that_can_produce_this_failure():
    """🔴 A LEDGER, failing when the set GROWS or SHRINKS.

    The pre-flight names ONE path instead of deriving a set, and that is only
    honest while exactly one package can produce the failure. It takes BOTH:

      * a non-null `vendorHash` — a fixed-output vendor derivation that can
        MISMATCH. `tmux-fuzzyclaw.nix` builds from a `${workspace}` tree too, but
        `vendorHash = null`, so there is no pinned output to be wrong.
      * a `${workspace}` src — a per-host working tree that can DIFFER.
        `stt-voice` and `mention-review` have real vendorHashes but
        `src = ./src` INSIDE devrc, which ship.sh itself converges, so their two
        hosts are identical by construction.

    If a second package ever satisfies both, this pre-flight is INCOMPLETE and
    this test is the thing that says so — before the next nameless nix error.
    """
    pkgs = _nix_pkg_sources()
    # POSITIVE CONTROL on the scanner: it must actually see the two properties
    # somewhere, or an empty parse satisfies the ledger in silence.
    assert any(v for v, _ in pkgs.values()), f"no vendorHash found at all: {pkgs}"
    assert any(w for _, w in pkgs.values()), f"no ${{workspace}} src found at all: {pkgs}"

    both = sorted(f for f, (v, w) in pkgs.items() if v and w)
    assert both == ["nix/pkgs/tools/clawgatectl.nix"], (
        f"the set of nix/pkgs packages with BOTH a real vendorHash and a "
        f"${{workspace}} src is {both}, not just clawgatectl. ship.sh's PRE-FLIGHT "
        f"digests ONE path (containers/clawgate/go.mod+go.sum) and is therefore "
        f"blind to the others. Widen the probe, or this gate is prose."
    )

    # ...and the path the probe digests is the one clawgatectl.nix actually builds.
    nix = (REPO_ROOT / "nix" / "pkgs" / "tools" / "clawgatectl.nix").read_text()
    assert 'srcDir = "${workspace}/homelab-talos/containers/clawgate"' in nix, (
        "clawgatectl.nix's srcDir moved; ship.sh's probe still digests "
        "containers/clawgate/go.mod and go.sum"
    )
    ship = SHIP.read_text()
    assert 'm="$d/containers/clawgate"' in ship, (
        "ship.sh no longer digests containers/clawgate — the two sides disagree "
        "about which subtree decides vendorHash"
    )


SINGLE_QUOTED_PAYLOADS = ("TALOS_PROBE", "CONVERGE")


def _payload(src: str, name: str) -> str:
    """The body of ship.sh's `NAME='…'` heredoc-style payload string."""
    head = f"{name}='"
    assert src.count(head) == 1, f"{head} is not unique in ship.sh"
    return src.split(head, 1)[1].split("\n'\n", 1)[0]


@pytest.mark.parametrize("name", SINGLE_QUOTED_PAYLOADS)
def test_neither_single_quoted_payload_contains_an_apostrophe(name):
    """🔴 A CLASS THAT HAS NOW FIRED TWICE, ONCE SILENTLY, ONCE LOUDLY.

    `TALOS_PROBE` and `CONVERGE` are SINGLE-QUOTED shell strings, so an apostrophe
    anywhere inside them — code or comment — ends the string. An even number
    re-opens it and the file still parses while the payload becomes different text;
    an odd number turns the remainder into ordinary shell code.

    MEASURED while writing this module, both failures inside one sitting:

      * ship.sh's own warning about the hazard SPELLED the forbidden pair, so the
        payload bash received was two bytes shorter than the source showed. Green
        under `bash -n`, green under the whole suite, and true on main since that
        comment was written.
      * then one `driver's` in a TALOS_PROBE comment ended the string outright:
        `gate: command not found`, `m: unbound variable`, 102 tests red.

    The first is why this is a TEST and not a comment: prose is exactly what the
    reviewer's eye skips, `bash -n` cannot see it, and every behavioural test in
    this repo passed straight through it. The second is why the rule is "no
    apostrophe" rather than "no empty-string literal" — the narrow wording is what
    let both through.

    `'"'"'` is the sanctioned escape (close, quote an apostrophe, reopen) and
    CONVERGE uses it deliberately, so it is allowed and nothing else is.
    """
    src = SHIP.read_text()
    payload = _payload(src, name)
    # The sanctioned escape, removed first so the scan can be absolute about the rest.
    scrubbed = payload.replace("'\"'\"'", "")
    offenders = [(i, ln) for i, ln in enumerate(scrubbed.splitlines(), 1) if "'" in ln]
    assert not offenders, (
        f"{name} contains {len(offenders)} apostrophe-bearing line(s), which end "
        f"its single-quoted string and silently change the payload:\n"
        + "\n".join(f"  line {i}: {ln}" for i, ln in offenders)
        + "\n(the only legal spelling is the '\"'\"' escape; in prose, NAME the "
          "token instead of writing it)"
    )


def test_the_apostrophe_scan_can_actually_go_red():
    """🔴 POSITIVE CONTROL. The test above does its real work in a `for` over
    whatever the split returned, so a parser wired to nothing passes it in silence —
    and a reassuring zero is the exact shape this whole module is about. Both
    historical spellings must be visible, and the sanctioned escape must not be.
    """
    assert [ln for ln in "a = ''\n".splitlines() if "'" in ln], "blind to the pair"
    assert [ln for ln in "# the driver's gate\n".splitlines() if "'" in ln], (
        "blind to a lone apostrophe"
    )
    escaped = "echo \"$HOME\"'\"'\"'s files\n".replace("'\"'\"'", "")
    assert not [ln for ln in escaped.splitlines() if "'" in ln], (
        "the sanctioned '\"'\"' escape is being reported as an offender, which "
        "would make the guard unsatisfiable for CONVERGE"
    )


def test_rc26_is_documented_in_the_header_and_the_legend():
    """🔴 An undocumented rc is an operator staring at a bare number.

    `test_ship_converge.py::test_every_exit_code_ship_can_return_is_documented_
    in_the_header_and_the_legend` already enforces this for every code it can
    parse. This asserts the specific pair for 26 so the intent is legible here
    too, and it names the two REMEDIES, which that scan cannot see.
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
    assert "checkout -- containers/clawgate/go.mod containers/clawgate/go.sum" in src, (
        "the UNCOMMITTED remedy is gone — a fast-forward cannot fix a dirty file, "
        "so that case needs its own instruction"
    )
