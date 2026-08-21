"""Tests for scripts/reclaim-managed-paths.sh — the repair half of the
wrong-writer class.

🔴 WHAT THE SCRIPT IS FOR, because a test that does not state it drifts into
asserting whatever the code does. home-manager deploys by symlinking $HOME/<p>
at a /nix/store file. When something ELSE writes a regular file at one of those
paths, upstream will not take it back:

  * `force = true` suppresses only the COLLISION CHECK (`check-link-targets.sh`
    skips forced prefixes). It removes nothing.
  * the link step's slow path, for a target that exists and is not a symlink:
        if [ -e "$t" && ! -L "$t" ] && cmp -s "$src" "$t"
        then  "Skipping '$t' as it is identical to '$src'"
    A regular file whose content matches the store copy is DELIBERATELY left in
    place, on every switch, forever.

So the leaked population is exactly "regular file, content identical" — which is
also the population it is lossless to delete. A file that DIFFERS is relinked by
the very next switch (`ln -Tsf` on the else branch) and might be someone's work;
the script must not touch it.

🔴 "LOSSLESS" IS NARROWER THAN IT READS, and the narrow part is load-bearing:

  * BYTES ONLY. `cmp` does not compare mode, ownership or xattrs, all of which
    are discarded when the file becomes a symlink. That is the intended outcome,
    but it is a real change, so this means "no BYTES are lost".
  * "the bytes are already in the store" is FALSE for a `mkOutOfStoreSymlink`
    leaf, of which this repo has 16. Such a leaf's manifest entry is a store
    symlink whose target is a path in the MUTABLE working tree (verified:
    home-files/.claude/skills/browser/SKILL.md -> /nix/store/…-hm_SKILL.md ->
    ~/workspace/devrc/scripts/browser-bridge/SKILL.md). For those, `cmp`
    compares against the working tree and the bytes survive deletion only as
    long as the working tree holds them. The guarantee is "identical to the
    source the manifest names", not "recoverable from the store forever".

Everything here runs against fixture trees in tmp_path. Nothing reads the
operator's real $HOME, the real /nix/store, or a real home-manager profile — a
test that skipped itself without one would be green on exactly the machine that
has the bug.
"""

import os
import re
import signal
import socket
import stat
import subprocess
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.afunix import SUN_PATH_MAX, bind_socket_at  # noqa: E402

# Sockets returned by `bind_socket_at` are parked here for the life of the
# process. The inode survives the fd being closed, so this is belt-and-braces
# against a GC'd socket object doing anything surprising mid-test; it is not a
# leak the suite has to manage (a few dozen fds at most).
_sockets = []

RECLAIM = REPO_ROOT / "scripts" / "reclaim-managed-paths.sh"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("cmp") is None,
    reason="needs bash + cmp on PATH",
)


def build(root, *, linked=0, identical=0, differing=0, absent=0, nested=0):
    """A fixture manifest + the $HOME it describes. Returns (home, manifest)."""
    home = root / "home"
    manifest = root / "home-files"
    store = root / "store"
    store.mkdir(parents=True, exist_ok=True)

    def leaf(rel, body):
        src = store / rel.replace("/", "_")
        src.write_text(body)
        m = manifest / rel
        m.parent.mkdir(parents=True, exist_ok=True)
        m.symlink_to(src)
        t = home / rel
        t.parent.mkdir(parents=True, exist_ok=True)
        return src, t

    for i in range(linked):
        src, t = leaf(".config/app/link-%d.md" % i, "deployed-%d\n" % i)
        t.symlink_to(src)
    for i in range(identical):
        src, t = leaf(".config/app/same-%d.md" % i, "same-%d\n" % i)
        t.write_text("same-%d\n" % i)
    for i in range(differing):
        src, t = leaf(".config/app/diff-%d.md" % i, "store-side-%d\n" % i)
        t.write_text("HAND EDITED %d\n" % i)
    for i in range(absent):
        leaf(".config/app/gone-%d.md" % i, "never-linked-%d\n" % i)
    for i in range(nested):
        src, t = leaf(".claude/skills/deep/deeper/nest-%d.md" % i, "nest-%d\n" % i)
        t.write_text("nest-%d\n" % i)
    return home, manifest


def run(home, manifest, *args):
    p = subprocess.run(
        ["bash", str(RECLAIM), "--home", str(home), str(manifest), *args],
        capture_output=True, text=True,
    )
    return p.returncode, p.stdout + p.stderr


def counts(out):
    """examined / reclaimable / differing / absent.

    🔴 THE READER INSISTS ON THE WHOLE LINE. `reclaimable=0` alone is what a
    scanner wired to nothing prints; the examined count beside it is what makes
    the zero mean anything, so a test cannot accidentally read one without the
    other."""
    m = re.search(r"managed paths: examined=(\d+) reclaimable=(\d+) "
                  r"differing=(\d+) absent=(\d+)", out)
    assert m, "no examined/reclaimable line — the pair IS the claim:\n" + out
    return tuple(int(g) for g in m.groups())


# --- the negative control, in the real failure shape ------------------------ #
def test_an_identical_regular_file_at_a_managed_path_is_found_and_removed(tmp_path):
    """🔴 THE SHAPE THAT ACTUALLY HAPPENED. 2026-08-19: an agent ran
    generate-commands.py with its output pointed at ~/.config/opencode/commands;
    34 symlinks became regular files, and 18 of them were still regular files two
    generations later because their content never changed."""
    home, manifest = build(tmp_path, linked=2, identical=3)
    rc, out = run(home, manifest)
    assert rc == 0, out
    assert counts(out) == (5, 3, 0, 0), out
    assert "DRY RUN" in out, "the default must not delete anything\n" + out
    for i in range(3):
        assert (home / (".config/app/same-%d.md" % i)).is_file(), (
            "the dry run deleted a file\n" + out
        )

    rc, out = run(home, manifest, "--apply")
    assert rc == 0, out
    assert "reclaimed 3 of 3" in out, out
    for i in range(3):
        p = home / (".config/app/same-%d.md" % i)
        assert not p.exists() and not p.is_symlink(), (
            "%s survived --apply; home-manager will skip it again on the next "
            "switch\n%s" % (p, out)
        )
    # The healthy links are UNTOUCHED — the repair must not churn what works.
    for i in range(2):
        assert (home / (".config/app/link-%d.md" % i)).is_symlink(), out


def test_a_differing_file_is_reported_and_never_removed(tmp_path):
    """🔴 THE DATA-LOSS GUARD. A managed path whose content differs from the
    store copy is relinked by the very next switch anyway, AND its bytes exist
    nowhere else. Deleting it would be the one destructive thing this script
    could do, so it must be reported and left alone — reported, because a
    silently-skipped finding is a finding nobody acts on."""
    home, manifest = build(tmp_path, linked=1, identical=1, differing=2)
    rc, out = run(home, manifest, "--apply")
    assert rc == 0, out
    assert counts(out) == (4, 1, 2, 0), out
    for i in range(2):
        p = home / (".config/app/diff-%d.md" % i)
        assert p.is_file() and p.read_text() == "HAND EDITED %d\n" % i, (
            "a file whose content differs from the store copy was destroyed\n" + out
        )
    assert "diff-0.md" in out and "diff-1.md" in out, (
        "the differing files were skipped SILENTLY\n" + out
    )


def test_the_examined_count_is_reported_beside_a_zero_finding(tmp_path):
    """🔴 POSITIVE CONTROL. `reclaimable=0` out of 0 examined is exactly what a
    walk wired to nothing prints. So show the walker producing a non-zero
    examined count on a tree whose true answer is zero, and report the pair."""
    home, manifest = build(tmp_path, linked=5, nested=0)
    rc, out = run(home, manifest, "--apply")
    examined, reclaimable, differing, absent = counts(out)
    assert examined == 5, (
        "the walk did not see the 5 manifest leaves it was given: %d" % examined
    )
    assert (reclaimable, differing, absent) == (0, 0, 0), out
    assert rc == 0, out


def test_a_missing_manifest_is_could_not_measure_not_a_clean_zero(tmp_path):
    """The other half of the same trap, and it has its OWN exit code (2) so a
    caller can tell "nothing to do" from "I could not look"."""
    home = tmp_path / "home"
    home.mkdir()
    rc, out = run(home, tmp_path / "no-such-generation")
    assert rc == 2, f"a walk with no manifest exited {rc}\n{out}"
    assert "COULD NOT MEASURE" in out, out
    assert "reclaimable=0" not in out, (
        "a run that walked nothing printed a clean count\n" + out
    )


def test_the_walk_recurses_into_nested_manifest_directories(tmp_path):
    """The real manifest is several levels deep (`.claude/skills/<n>/reference/`).
    A walk that only reads the top level reports a clean scan over a subtree it
    never entered — and every skill lives in one."""
    home, manifest = build(tmp_path, linked=1, nested=3)
    rc, out = run(home, manifest)
    examined, reclaimable, _, _ = counts(out)
    assert (examined, reclaimable) == (4, 3), (
        "nested manifest leaves were not walked\n" + out
    )
    assert ".claude/skills/deep/deeper/nest-0.md" in out, out


def test_a_leaf_pointing_at_a_directory_is_not_descended_into(tmp_path):
    """🔴 -L IS TESTED BEFORE -d, and this is the reason. `home.file` with a
    directory source deploys ONE symlink at a directory target. `[ -d ]` follows
    a symlink, so a walk that asks -d first descends into /nix/store and treats
    store contents as managed paths — every one of which has no $HOME
    counterpart, so they land in `absent` and the numbers become fiction."""
    home, manifest = build(tmp_path, linked=1)
    storedir = tmp_path / "store" / "whole-dir"
    storedir.mkdir(parents=True)
    for i in range(6):
        (storedir / ("inside-%d.md" % i)).write_text("in the store\n")
    (manifest / ".config" / "app" / "wholedir").symlink_to(storedir)
    (home / ".config" / "app" / "wholedir").symlink_to(storedir)
    rc, out = run(home, manifest)
    examined, reclaimable, differing, absent = counts(out)
    assert examined == 2, (
        "the walk descended a directory-valued leaf and counted %d store entries "
        "as managed paths\n%s" % (examined, out)
    )
    assert (reclaimable, differing, absent) == (0, 0, 0), out


def test_a_file_edited_between_two_runs_is_reclassified_by_the_walk(tmp_path):
    """The OUTER protection: a file that stops matching the store copy between
    runs is simply no longer a candidate.

    🔴 THIS DOES NOT REACH THE RE-CHECK, and saying so is the point. A mutant
    that removed the `cmp -s` guard from the delete loop SURVIVED this test:
    by the second run the WALK had already reclassified the file, so an earlier
    check always won and the re-check never executed. The reachable version is
    the test below.
    """
    home, manifest = build(tmp_path, identical=2)
    rc, out = run(home, manifest)
    assert counts(out)[1] == 2, out

    victim = home / ".config/app/same-0.md"
    victim.write_text("SOMEONE EDITED THIS BETWEEN RUNS\n")
    rc, out = run(home, manifest, "--apply")
    assert rc == 0, out
    assert victim.is_file(), out
    assert victim.read_text() == "SOMEONE EDITED THIS BETWEEN RUNS\n", out
    assert not (home / ".config/app/same-1.md").exists(), (
        "the unchanged one should still have been reclaimed\n" + out
    )


def test_a_file_that_changes_between_the_survey_and_the_delete_is_left_alone(tmp_path):
    """🔴 THE RE-CHECK IMMEDIATELY BEFORE THE DESTRUCTIVE STEP, reached.

    The walk is a hypothesis about a moment that has already passed; between it
    and the `rm` a concurrent switch or editor can change the file. That window
    cannot be entered from outside the process, so `cmp` itself is stubbed to
    AGREE while the walk classifies and DISAGREE once the delete loop asks again
    — which is exactly the observable a mid-run change produces.

    The stub is the instrument, so it is validated inside the same test: the run
    must still have FOUND both candidates (the walk's `cmp` said identical), the
    counter must show `cmp` was called AGAIN after the walk, and then nothing may
    have been removed.
    """
    from testlib.mockbin import write_exec

    home, manifest = build(tmp_path, identical=2)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "cmp-calls"
    # Two candidates -> the walk makes exactly 2 `cmp` calls; every later call
    # belongs to the delete loop.
    write_exec(bindir / "cmp", (
        'n=$(cat "%s" 2>/dev/null || echo 0)\n'
        'n=$((n+1)); echo "$n" > "%s"\n'
        'if [ "$n" -le 2 ]; then exit 0; else exit 1; fi\n'
    ) % (counter, counter))

    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    p = subprocess.run(
        ["bash", str(RECLAIM), "--home", str(home), str(manifest), "--apply"],
        capture_output=True, text=True, env=env)
    out = p.stdout + p.stderr

    assert p.returncode == 0, out
    assert counts(out)[1] == 2, (
        "the stub did not let the WALK see both candidates, so the delete loop "
        "was never entered and this test would prove nothing\n" + out
    )
    assert int(counter.read_text()) > 2, (
        "`cmp` was called only during the walk — the re-check is not being made "
        "at all\n" + out
    )
    assert "reclaimed 0 of 2" in out, out
    for i in range(2):
        victim = home / (".config/app/same-%d.md" % i)
        assert victim.is_file(), (
            "%s was deleted after the re-check said it had changed\n%s"
            % (victim, out)
        )


def test_apply_is_not_the_default(tmp_path):
    """A repair that deletes by default is a repair nobody can safely preview.
    Asserted behaviourally rather than by reading the flag parser."""
    home, manifest = build(tmp_path, identical=2)
    rc, out = run(home, manifest)
    assert rc == 0, out
    assert (home / ".config/app/same-0.md").is_file(), out
    assert (home / ".config/app/same-1.md").is_file(), out


def test_an_unknown_flag_is_a_usage_error_not_a_silent_full_run(tmp_path):
    """🔴 A wrapper that reports "nothing to do" instead of erroring is how a
    green gets believed. A typo'd flag must not be swallowed into a run with
    default (destructive-capable) arguments."""
    home, manifest = build(tmp_path, identical=1)
    p = subprocess.run(
        ["bash", str(RECLAIM), "--aply", "--home", str(home), str(manifest)],
        capture_output=True, text=True,
    )
    assert p.returncode == 1, p.stdout + p.stderr
    assert "unknown flag" in (p.stdout + p.stderr)
    assert (home / ".config/app/same-0.md").is_file(), "it ran anyway"


# --- the seam with home-manager activation ---------------------------------- #
def test_the_activation_entry_runs_immediately_before_linkGeneration():
    """🔴 A SEAM, AND THE ONLY THING THAT MAKES rc 19 SELF-CLEARING. drift-check
    rc 19 is not wired to systemd's SuccessExitStatus, which is only defensible
    because an ordinary `home-manager switch` repairs the finding. That claim is
    false the moment the activation entry stops calling this script, stops
    passing --apply, or moves away from linkGeneration — none of which any test
    of either file alone would notice.

    🔴 AND THE PLACEMENT IS ITSELF THE SAFETY PROPERTY. Between this script's
    `rm` and linkGeneration's `ln`, the reclaimed files exist NOWHERE. Every
    activation step inside that window can abort the switch and strand them
    deleted-and-unlinked, which is strictly worse than the bug being repaired.

    This entry used to be `entryBefore ["checkLinkTargets"]`, justified in
    nix/home.nix by "the collision check runs first, and a path this reclaims
    must already be gone by the time linkGeneration decides what to do with it".
    That justification was FALSE, which is why this test now pins the opposite
    ordering and asserts the old one is gone. Read `checkCollision()` in upstream
    check-link-targets.sh: its FIRST branch is
        if cmp -s "$sourcePath" "$targetPath"; then warnEcho …
    Identical content is a WARNING. Only a target that DIFFERS can reach
    `collisionErrors+=`, and a differing target is exactly what this script
    refuses to touch — so this population can never fail checkLinkTargets, forced
    prefix or not. The three `dropStale*` entries DO handle differing content, so
    the sentence is true of them and false of this one.

    Measured on the BUILT activation script (`nix build
    .#homeConfigurations.zach.activationPackage`, then read `activate`):
      * old ordering — reclaimManagedPaths 268, checkLinkTargets 279,
        linkGeneration 513, with `checkNewGenCollision || exit 1`, writeBoundary,
        browserBridgeExtension, checkFilesChanged and installPackages inside the
        window. installPackages is `nix-env --set`, which this repo's memory
        records failing outright on an imperative-profile conflict.
      * new ordering — installPackages 472, reclaimManagedPaths 502,
        linkGeneration 513, with ZERO activation steps in between.

    🔴 entryBetween PINS BOTH ENDS, and that is not belt-and-braces.
    `entryBefore ["linkGeneration"]` alone gives the entry no lower bound, and
    the DAG's topological sort is then free to emit it early again — back inside
    the window this test exists to close, with the test still passing. So the
    `after` half is asserted just as hard as the `before` half.
    """
    src = HOME_NIX.read_text()
    m = re.search(
        r"home\.activation\.reclaimManagedPaths\s*=\s*\n?\s*"
        r"lib\.hm\.dag\.entryBetween\s*\[\s*\"linkGeneration\"\s*\]"
        r"\s*\[\s*\"installPackages\"\s*\](.*?)'';",
        src, re.S)
    assert m, (
        "nix/home.nix has no reclaimManagedPaths activation entry ordered "
        "BETWEEN installPackages and linkGeneration. Both ends matter: without "
        "the `before` the repair runs after the relink and does nothing; "
        "without the `after` the sort may emit it early, reopening the window "
        "in which the reclaimed files exist nowhere on disk."
    )
    assert not re.search(
        r"home\.activation\.reclaimManagedPaths\s*=\s*\n?\s*"
        r"lib\.hm\.dag\.entryBefore\s*\[\s*\"checkLinkTargets\"\s*\]", src), (
        "the entry is back before checkLinkTargets. That ordering was justified "
        "by a false claim about checkCollision() — identical content is a "
        "warnEcho there, never a collisionError — and it puts installPackages "
        "inside the delete/relink window."
    )
    body = m.group(1)
    assert "reclaim-managed-paths.sh" in body, (
        "the activation entry no longer runs the repair script:\n" + body
    )
    assert "--apply" in body, (
        "the activation entry runs the script in DRY-RUN mode, so a switch "
        "reports the finding and repairs nothing:\n" + body
    )
    assert "$newGenPath/home-files" in body, (
        "the entry must walk the NEW generation's manifest. The default is the "
        "profile's CURRENT generation, which during activation is still the OLD "
        "one — so a path newly declared by this switch would not be reclaimed:\n"
        + body
    )


# --- what is AT the managed path: not everything is a regular file ---------- #
#
# 🔴 THE WHOLE CLASS THIS SECTION EXISTS FOR. Both walks used to run `cmp`
# against whatever the target was and read ANY non-zero exit as "content
# differs". A directory and a FIFO are neither reclaimable nor self-healing, and
# treating them as the latter was wrong in a different way each time. Measured
# 2026-08-21, before the `-f` test was added.

def blocking(out):
    m = re.search(r"managed paths: examined=\d+ reclaimable=\d+ differing=\d+ "
                  r"absent=\d+ blocking=(\d+)", out)
    assert m, "no blocking count on the summary line:\n" + out
    return int(m.group(1))


def occupy(home, rel, kind):
    """Put a non-regular-file at a managed target and return its path."""
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if kind == "directory":
        p.mkdir()
    elif kind == "fifo":
        os.mkfifo(p)
    elif kind == "socket":
        # NOT a bare `s.bind(str(p))`. bind(2)'s `sun_path` is 108 bytes and
        # `tmp_path` under `nix-shell`'s TMPDIR is 115 — which is the env the
        # pre-push gate runs in. See scripts/testlib/afunix.py.
        _sockets.append(bind_socket_at(p))
    else:                                       # pragma: no cover - typo guard
        raise AssertionError("unknown kind %r" % kind)
    return p


def test_the_socket_fixture_works_at_a_path_LONGER_than_sun_path(tmp_path):
    """🔴 THE FIXTURE ITSELF, pinned at the length that broke it.

    `socket.bind()` is limited by `sun_path` — 108 bytes on Linux, path
    INCLUDED — and the two socket fixtures in this repo bound directly at
    `tmp_path/…`. Under a bare `pytest` that is 62 chars and green; under
    `nix-shell -p …`, which sets `TMPDIR=/tmp/nix-shell-<pid>-<n>`, it is 115
    and raises `OSError: AF_UNIX path too long`. `githooks/tests-on-push.sh`
    runs the whole suite inside exactly that nix-shell, so both files went RED
    on every push while being green for whoever wrote them.

    This test does not depend on the ambient `TMPDIR` to reproduce that: it
    builds a path deliberately past the limit and asserts the helper still
    produces a real socket there. It is the negative control for the harness —
    revert `bind_socket_at` to a direct bind and it raises OSError here on every
    host, not only under nix-shell.
    """
    deep = tmp_path
    while len(str(deep)) < SUN_PATH_MAX + 40:
        deep = deep / "padpadpadpadpadpad"
    target = deep / "occupied"
    assert len(str(target).encode()) > SUN_PATH_MAX, (
        "the fixture path is only %d bytes — under the %d-byte limit, so this "
        "test would pass against the very bug it exists for"
        % (len(str(target).encode()), SUN_PATH_MAX))

    _sockets.append(bind_socket_at(target))
    assert stat.S_ISSOCK(os.lstat(target).st_mode), (
        "bind_socket_at produced %o at a %d-byte path, not a socket"
        % (os.lstat(target).st_mode, len(str(target).encode())))

    # The positive control for the LIMIT itself: a direct bind at this same path
    # MUST still fail. Without it, a kernel or libc that had quietly raised the
    # limit would make the assertion above true for a reason that has nothing to
    # do with the helper, and the whole guard would be vacuous.
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            s.bind(str(deep / "direct"))
    finally:
        s.close()


@pytest.mark.parametrize("kind", ["directory", "fifo", "socket"])
def test_a_non_regular_file_at_a_managed_path_is_blocking_never_self_healing(
        tmp_path, kind):
    """🔴 THE LABEL WAS THE INVERSE OF THE TRUTH, on the most severe finding.

    `cmp` exits non-zero on a directory, so the old code reported it as "holds a
    regular file whose content DIFFERS from the store copy … the next home-manager
    switch relinks those on its own". Every clause false. Upstream's link slow
    path is `run ln -Tsf … || exit 1`, and `ln` cannot overwrite a directory — so
    the next switch ABORTS on this path rather than repairing it. An operator
    reading "self-healing" would wait for a switch that cannot happen.

    Asserted on the classification the operator acts on (the count AND the
    printed label), not on a word another branch could spell: `blocking=` is its
    own field, so a mutant that merely reworded the message cannot walk it.
    """
    home, manifest = build(tmp_path, identical=1)
    (manifest / ".config" / "app" / "occupied").symlink_to(
        _store_leaf(tmp_path, "occupied", "store side\n"))
    victim = occupy(home, ".config/app/occupied", kind)

    rc, out = _run_timed(home, manifest, "--apply")
    assert rc == 0, out
    examined, reclaimable, differing, absent = counts(out)
    assert (examined, reclaimable, differing, absent) == (2, 1, 0, 0), out
    assert blocking(out) == 1, (
        "a %s at a managed path was not counted as blocking\n%s" % (kind, out)
    )
    assert "not a regular file" in out.lower(), out
    # 🔴 THE PATH AND ITS KIND, PAIRED. A bare `kind in out` would be walkable —
    # the block's own static prose spells "directory" while explaining that ln
    # cannot overwrite one, so a mutant replacing the computed KIND with a
    # literal survives a word check. (Measured on the sibling assertion in
    # test_drift_check.py, which was written that way and let exactly that
    # mutant through.) Pinning `<rel> (<kind>)` makes the computed slot the only
    # thing that can satisfy it.
    assert "occupied (%s)" % kind in out, (
        "the finding does not name WHAT is in the way beside the path it is in "
        "the way of, so nobody can act on it\n" + out
    )
    assert "self-healing" not in out and "DIFFERS" not in out, (
        "a %s was labelled with the benign classification; the next switch "
        "ABORTS on it\n%s" % (kind, out)
    )
    # And it is never removed.
    assert victim.exists() or victim.is_symlink(), (
        "the %s at a managed path was destroyed\n%s" % (kind, out)
    )
    # The genuine candidate beside it is still repaired — a guard that refuses
    # the whole run because one path is odd would be its own outage.
    assert "reclaimed 1 of 1" in out, out


def test_a_fifo_at_a_managed_path_does_not_hang_the_walk(tmp_path):
    """🔴 A HUNG DEPLOY, AND IT WAS IN THE *WALK*, NOT THE DELETE LOOP.

    `cmp -s` on a FIFO blocks in open(2) forever. The delete loop already tested
    `-f` before its `cmp`, so the hang was never reachable there — it was in the
    classification walk, which means the DRY RUN hung too, and so did
    drift-check's copy of the same walk. Measured 2026-08-21 at PR head:
    `timeout 8` -> rc 124 on the dry run, with nothing printed at all.

    That matters beyond a wedged terminal: there is no timeout anywhere in the
    activation call path (`home.activation.reclaimManagedPaths` -> bash -> this
    script), and the drift-check copy runs 4x/day from a timer and over ssh.

    This test is the one place in the file with a wall-clock bound, and the bound
    IS the assertion. 30s is ~100x the observed runtime of every other case here,
    so it cannot flake on load; a regression does not take 30s, it takes forever.
    """
    home, manifest = build(tmp_path, identical=1)
    (manifest / ".config" / "app" / "pipe").symlink_to(
        _store_leaf(tmp_path, "pipe", "store side\n"))
    os.mkfifo(home / ".config" / "app" / "pipe")

    rc, out = bounded(["bash", str(RECLAIM), "--home", str(home), str(manifest)])
    assert rc == 0, out
    assert blocking(out) == 1, out


def _store_leaf(root, name, body):
    src = root / "store" / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(body)
    return src


def bounded(argv, *, timeout=30, **kw):
    """`subprocess.run` with a wall-clock bound AND a process-group kill.

    🔴 TWO SEPARATE HAZARDS, AND THE SECOND ONE BIT DURING THIS PR'S OWN
    MUTATION SWEEP.

    (1) A bare `subprocess.run` with no timeout WEDGES THE WHOLE SUITE rather
        than failing one test. Every case in this section puts something at a
        managed path that `cmp` can block on forever (a FIFO), so a regression
        here presents as a hang, not an assertion — and a hung suite reports no
        verdict at all, which the gate is built to distrust.

    (2) `subprocess.run(timeout=…)` kills only the DIRECT child. The bash script
        is the child; the `cmp` blocked on the FIFO is a GRANDchild, and it
        survives as an orphan holding the fixture open. Measured: a sweep that
        timed out two mutants left four orphaned `cmp` processes reparented to
        init, still blocked on tmp-dir FIFOs after the run that made them was
        gone. So the process is started in its own session and the whole GROUP
        is signalled on timeout.
    """
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True, **kw)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate()
        raise AssertionError(
            "the run did not finish within %ds. `cmp` blocks forever on a FIFO, "
            "so this is the hang shape, not a slow machine — and there is NO "
            "timeout anywhere in the activation call path that runs this script."
            % timeout)
    return proc.returncode, out


def _run_timed(home, manifest, *args):
    return bounded(["bash", str(RECLAIM), "--home", str(home), str(manifest),
                    *args])


# --- the instrument, and the walk of nothing -------------------------------- #

# 🔴 THE ONLY THING THIS SCRIPT NEEDS BESIDES `cmp`. Enumerated rather than
# inherited, because the two tests below REPLACE $PATH — and replacing is the
# point, not an oversight: no amount of PREPENDING can make a binary unfindable,
# and "cmp is absent" is the whole condition under test.
#
# The enumeration is what makes the replacement safe, and it is asserted at
# construction rather than described in prose, so it cannot rot: `_nocmp_bin`
# checks that the directory holds exactly these names and that `cmp` is not
# resolvable from it. That second check is the positive control for the fixture
# — without it a typo in the list would silently produce a PATH that still has
# `cmp`, and both tests would pass while proving nothing.
_NOCMP_TOOLS = ("bash", "sed", "head", "grep", "readlink", "rm", "cat")


def _nocmp_bin(tmp_path, name):
    """A $PATH directory holding _NOCMP_TOOLS and provably no `cmp`."""
    bindir = tmp_path / name
    bindir.mkdir()
    for t in _NOCMP_TOOLS:
        w = shutil.which(t)
        if w:
            (bindir / t).symlink_to(w)
    assert {p.name for p in bindir.iterdir()} <= set(_NOCMP_TOOLS), (
        "the stub bin holds something outside the enumeration: %s"
        % sorted(p.name for p in bindir.iterdir())
    )
    assert shutil.which("cmp", path=str(bindir)) is None, (
        "the stub PATH still resolves `cmp` — the fixture is broken and both "
        "tests using it would pass while proving nothing"
    )
    return bindir


def test_a_missing_cmp_is_could_not_measure_not_a_clean_zero(tmp_path):
    """🔴 VALIDATE THE INSTRUMENT BEFORE READING ITS VERDICT. `cmp` is the ONLY
    thing separating a path this may losslessly delete from one it must never
    touch. Without it every `cmp -s` returns 127, every candidate falls into the
    "differs" branch, and the run prints `reclaimable=0` and exits 0 — a clean
    bill of health from a classifier that classified nothing.

    Measured at PR head under a PATH with no diffutils, on a fixture holding
    three genuine permanent cases: `examined=3 reclaimable=0 differing=3`, rc 0.

    `cmp` is in diffutils, NOT coreutils, so a PATH assembled from an explicit
    package list can plausibly lack it — the drift-check systemd unit's did, and
    still does until the operator switches.
    """
    home, manifest = build(tmp_path, identical=3)
    bindir = _nocmp_bin(tmp_path, "nocmp")

    env = dict(os.environ)
    env["PATH"] = str(bindir)
    p = subprocess.run(
        ["bash", str(RECLAIM), "--home", str(home), str(manifest), "--apply"],
        capture_output=True, text=True, env=env, timeout=30)
    out = p.stdout + p.stderr

    assert p.returncode == 3, (
        "a run that could not classify anything exited %d; it needs its OWN code "
        "so a caller can tell it from 'nothing to do'\n%s" % (p.returncode, out)
    )
    assert "COULD NOT MEASURE" in out and "reason=no-cmp" in out, out
    assert "reclaimable=0" not in out, (
        "a blind run printed a clean count\n" + out
    )
    for i in range(3):
        assert (home / (".config/app/same-%d.md" % i)).is_file(), (
            "--apply deleted something while unable to verify it was identical\n"
            + out
        )


def test_an_empty_manifest_directory_is_could_not_measure(tmp_path):
    """🔴 "A WALK OF NOTHING IS NOT A CLEAN WALK" COVERED ONLY A *MISSING*
    MANIFEST. `[ ! -d ]` says nothing about a directory that exists and holds
    nothing, and `examined=0 reclaimable=0 … rc 0` out of one is byte-identical
    to what a healthy host prints. A real generation has hundreds of leaves (488
    on the workbench), so zero means the path is wrong, the tree is half-built,
    or it is not a manifest at all.

    Measured at PR head: `examined=0 reclaimable=0 differing=0 absent=0`, rc 0.
    """
    home = tmp_path / "home"
    home.mkdir()
    manifest = tmp_path / "empty-generation"
    manifest.mkdir()
    rc, out = run(home, manifest)
    assert rc == 2, f"an empty manifest exited {rc}\n{out}"
    assert "COULD NOT MEASURE" in out and "reason=empty-manifest" in out, out
    assert "reclaimable=0" not in out, (
        "a walk that examined nothing printed a clean count\n" + out
    )


def test_the_three_could_not_measure_reasons_are_distinct_tokens(tmp_path):
    """The reasons are what a reader acts on, and "COULD NOT MEASURE" alone
    cannot tell 'install diffutils' from 'your profile path is wrong'. Driven,
    not read off the source — each token is taken from a run that actually
    produced it."""
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "empty").mkdir()

    seen = {}
    _, out = run(home, tmp_path / "no-such-generation")
    seen["no-manifest"] = out
    _, out = run(home, tmp_path / "empty")
    seen["empty-manifest"] = out

    bindir = _nocmp_bin(tmp_path, "nocmp2")
    env = dict(os.environ)
    env["PATH"] = str(bindir)
    h2, m2 = build(tmp_path / "sub", identical=1)
    p = subprocess.run(["bash", str(RECLAIM), "--home", str(h2), str(m2)],
                       capture_output=True, text=True, env=env, timeout=30)
    seen["no-cmp"] = p.stdout + p.stderr

    for token, out in seen.items():
        assert "reason=%s" % token in out, (
            "the %s case did not emit its own reason token\n%s" % (token, out)
        )
        for other in seen:
            if other != token:
                assert "reason=%s" % other not in out, (
                    "the %s case also emitted reason=%s — the tokens do not "
                    "discriminate\n%s" % (token, other, out)
                )


# --- classification edges the sweep found unpinned -------------------------- #

def test_a_managed_path_with_no_target_is_counted_absent(tmp_path):
    """`build(absent=…)` existed and no test used it, so the `absent` branch was
    never exercised: a manifest leaf whose $HOME counterpart has not been
    created yet (a path this generation newly declares). It is not a finding —
    linkGeneration creates it — but it must be COUNTED, or `examined` stops
    equalling the sum of the classifications and the pair rule this file is built
    on becomes arithmetic nobody can check."""
    home, manifest = build(tmp_path, linked=1, identical=1, absent=3)
    rc, out = run(home, manifest)
    assert rc == 0, out
    examined, reclaimable, differing, absent = counts(out)
    assert (examined, reclaimable, differing, absent) == (5, 1, 0, 3), out
    assert examined == reclaimable + differing + absent + blocking(out) + 1, (
        "examined does not decompose into the printed classifications plus the "
        "one healthy link — a count nobody can check is not a measurement\n" + out
    )


def test_a_dangling_symlink_at_a_target_is_not_counted_absent(tmp_path):
    """🔴 `[ ! -e ] && [ ! -L ]` — BOTH HALVES, and an independent sweep found
    the second one unpinned because no fixture had a dangling symlink at a
    target.

    A dangling symlink is `-e` FALSE and `-L` TRUE. It is not absent: the link
    step sees a symlink, resolves it, finds it does not point at the new source,
    and relinks it — so the honest classification is the same as any other
    home-manager-owned link. Dropping the `-L` half files it under `absent`,
    which under-reports nothing today but makes the labels stop matching what
    home-manager actually does, and those labels are the whole product.

    This is also the real shape of drift-check rc 14, so the fixture is not
    hypothetical: 46 of 139 managed links on the laptop were dangling on
    2026-08-11 after a store GC.
    """
    home, manifest = build(tmp_path, linked=1)
    src = _store_leaf(tmp_path, "ghost", "deployed\n")
    (manifest / ".config" / "app" / "ghost").symlink_to(src)
    (home / ".config" / "app" / "ghost").symlink_to(
        tmp_path / "store" / "collected-by-gc")
    assert not (home / ".config" / "app" / "ghost").exists()
    assert (home / ".config" / "app" / "ghost").is_symlink()

    rc, out = run(home, manifest)
    assert rc == 0, out
    examined, reclaimable, differing, absent = counts(out)
    assert examined == 2, out
    assert absent == 0, (
        "a DANGLING SYMLINK at a managed target was counted as absent. It is a "
        "symlink; home-manager relinks it. Only a target with nothing at all "
        "there is absent.\n" + out
    )
    assert (reclaimable, differing) == (0, 0), out


# --- the delete loop's re-checks, made reachable ---------------------------- #
#
# 🔴 AN INDEPENDENT SWEEP FOUND THREE OF THE FOUR RE-CHECKS UNKILLABLE, because
# the walk classifies first and `cmp` always decided before they could. Two of
# them are now driven with the same instrument the `cmp` re-check test uses — a
# stubbed `cmp` that CHANGES THE WORLD between the walk and the delete loop,
# which is exactly the observable a concurrent switch or editor produces. The
# fourth is unreachable BY DESIGN and is documented as such, in the script and
# here, rather than left looking tested.

def _cmp_stub_that_swaps(bindir, counter, victim, action):
    """A `cmp` that always says "identical", and on its FIRST call (the walk's)
    replaces $victim as described. The delete loop's call therefore asks about a
    different kind of thing than the walk saw."""
    from testlib.mockbin import write_exec
    write_exec(bindir / "cmp", (
        'n=$(cat "%s" 2>/dev/null || echo 0)\n'
        'n=$((n+1)); echo "$n" > "%s"\n'
        'if [ "$n" = 1 ]; then\n'
        '  rm -f "%s"\n'
        '  %s\n'
        'fi\n'
        'exit 0\n'
    ) % (counter, counter, victim, action))


def _run_with_stub(bindir, home, manifest):
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    p = subprocess.run(
        ["bash", str(RECLAIM), "--home", str(home), str(manifest), "--apply"],
        capture_output=True, text=True, env=env, timeout=30)
    return p.returncode, p.stdout + p.stderr


def test_a_target_that_became_a_symlink_after_the_walk_is_not_removed(tmp_path):
    """🔴 `[ -L "$TGT" ] && continue` IN THE DELETE LOOP, reached.

    Unreachable in ordinary operation — the walk skips a symlink target, so it
    never reaches the candidate list — which is why an independent sweep could
    delete this line and see every test stay green. The window it guards is real:
    a concurrent `home-manager switch` relinks the path between this run's walk
    and its `rm`, and without the guard `[ -f ]` FOLLOWS the fresh symlink, `cmp`
    agrees (it points at the store copy), and `rm` destroys the link
    home-manager just made — leaving the path absent until the next switch.

    Driven by stubbing `cmp` to swap the target on its first (walk) call. The
    stub is the instrument, so the test validates it: the walk must still have
    FOUND the candidate, and the swap must actually have happened.
    """
    home, manifest = build(tmp_path, identical=1)
    victim = home / ".config" / "app" / "same-0.md"
    store_copy = tmp_path / "store" / ".config_app_same-0.md"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "cmp-calls"
    _cmp_stub_that_swaps(bindir, counter, victim,
                         'ln -s "%s" "%s"' % (store_copy, victim))

    rc, out = _run_with_stub(bindir, home, manifest)
    assert rc == 0, out
    assert counts(out)[1] == 1, (
        "the stub did not let the WALK see the candidate, so the delete loop was "
        "never entered and this test would prove nothing\n" + out
    )
    assert victim.is_symlink(), (
        "the stub did not perform the swap — the instrument is broken, not the "
        "code\n" + out
    )
    assert "reclaimed 0 of 1" in out, (
        "the re-check did not stop the removal\n" + out
    )


def test_a_target_that_became_a_fifo_after_the_walk_is_not_removed(tmp_path):
    """🔴 `[ -f "$TGT" ] || continue` IN THE DELETE LOOP, reached.

    Same unreachability as the symlink case — the walk's own `-f` test now keeps
    a non-regular file out of the candidate list — and the same real window: the
    path can become a directory or a FIFO between the walk and the `rm`. Without
    the guard the loop runs `cmp` on a FIFO, which BLOCKS in open(2) forever, and
    if it ever returned it would `rm` something that is not the file it measured.

    A FIFO rather than a directory on purpose: `rm -f` on a directory fails
    anyway, so a directory fixture would go green with the guard deleted — the
    mutant would die for the wrong reason. `rm -f` removes a FIFO happily, so the
    survival of the FIFO is attributable to THIS guard.
    """
    home, manifest = build(tmp_path, identical=1)
    victim = home / ".config" / "app" / "same-0.md"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    counter = tmp_path / "cmp-calls"
    _cmp_stub_that_swaps(bindir, counter, victim, 'mkfifo "%s"' % victim)

    rc, out = _run_with_stub(bindir, home, manifest)
    assert rc == 0, out
    assert counts(out)[1] == 1, (
        "the walk never saw the candidate; the delete loop was not entered\n" + out
    )
    import stat as _stat
    assert _stat.S_ISFIFO(os.stat(victim, follow_symlinks=False).st_mode), (
        "the stub did not perform the swap — the instrument is broken\n" + out
    )
    assert "reclaimed 0 of 1" in out, (
        "the re-check did not stop the removal\n" + out
    )


def test_the_delete_loops_existence_recheck_is_documented_as_unreachable():
    """🔴 THE HONEST ENTRY. `[ -e "$TGT" ] || continue` is the fourth re-check
    and it is UNREACHABLE BY DESIGN: `-f` implies `-e` for every operand except a
    dangling symlink, and a dangling symlink is caught by the `-L` test on the
    line between them. So no input can reach this test's `continue` without a
    later guard also firing, and no mutation of it can be killed.

    It is kept — the invariant reads in full at the site that depends on it — but
    it is NOT counted as covered, and this test exists so that claim is written
    down beside the other three rather than left to a reader's inference. It
    pins the two guards the argument DEPENDS on: if either `-L` or `-f` ever
    leaves the loop, the reasoning above stops holding and this test fails,
    which is the only thing that would make `-e` load-bearing again.
    """
    body = RECLAIM.read_text()
    loop = body[body.index("while IFS= read -r REL"):]
    for guard in ('[ -e "$TGT" ] || continue',
                  '[ -L "$TGT" ] && continue',
                  '[ -f "$TGT" ] || continue'):
        assert guard in loop, (
            "%r left the delete loop. The `-e` re-check is documented as "
            "unreachable ONLY because -L and -f both sit below it; remove "
            "either and that argument is void." % guard
        )


# --- one rule, one place: where the manifest lives -------------------------- #

SHIP = REPO_ROOT / "scripts" / "ship.sh"
DRIFT = REPO_ROOT / "scripts" / "drift-check.sh"


def test_all_three_manifest_probes_agree():
    """🔴 ONE RULE, ONE PLACE — and here it is deliberately THREE places, so the
    agreement has to be asserted instead of assumed.

    ship.sh's `ma_manifest`, this script and drift-check's rc-19 payload each
    answer "where is this host's home-files tree", and they render three verdicts
    about ONE tree: the payload reports a finding, this script repairs it,
    ship.sh verifies the result. They cannot share a function — the drift payload
    is piped to `bash -s` over ssh and can source nothing.

    They did NOT agree at PR head: drift-check hardcoded
    `$HOME/.local/state/nix/profiles/…`, honouring neither XDG_STATE_HOME nor the
    gcroots location, while the other two honoured both. On any host that sets
    XDG_STATE_HOME the detector and the repair would have been reading different
    trees — and the detector's own output names the manifest it read, so the
    disagreement would have looked like a fixed bug.

    Asserted structurally (each reader must contain both candidate paths and the
    XDG expansion) rather than by re-running them: the payload's copy cannot be
    invoked in isolation from here without duplicating the extraction the
    drift-check suite already owns.
    """
    readers = {
        "ship.sh": SHIP.read_text(),
        "reclaim-managed-paths.sh": RECLAIM.read_text(),
        "drift-check.sh": DRIFT.read_text(),
    }
    for name, text in readers.items():
        assert "home-manager/gcroots/current-home/home-files" in text, (
            "%s does not probe the gcroots location. home-manager has used both; "
            "a reader that knows only one silently reads a different tree from "
            "its siblings." % name
        )
        assert "nix/profiles/home-manager/home-files" in text, (
            "%s does not probe the profile location" % name
        )
        assert "XDG_STATE_HOME" in text, (
            "%s hardcodes the state directory. drift-check did exactly this and "
            "disagreed with the other two on any host that sets the variable."
            % name
        )


def test_the_manifest_probe_honours_xdg_state_home(tmp_path):
    """The structural test above pins that the words are present; this one pins
    that they are WIRED. A default-path probe is the case with no argument, which
    every other test in this file bypasses by passing the manifest explicitly."""
    state = tmp_path / "state"
    manifest = state / "home-manager" / "gcroots" / "current-home" / "home-files"
    manifest.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    src = _store_leaf(tmp_path, "leaf", "deployed\n")
    (manifest / "leaf.md").symlink_to(src)
    (home / "leaf.md").write_text("deployed\n")

    env = dict(os.environ)
    env["XDG_STATE_HOME"] = str(state)
    env["RECLAIM_HOME"] = str(home)
    env.pop("RECLAIM_HOME_FILES", None)
    p = subprocess.run(["bash", str(RECLAIM)], capture_output=True, text=True,
                       env=env, timeout=30)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert str(manifest) in out, (
        "the default probe did not find the gcroots manifest under "
        "XDG_STATE_HOME\n" + out
    )
    assert counts(out) == (1, 1, 0, 0), out


# --- argument handling and output volume ------------------------------------ #

def test_two_manifest_directories_is_a_usage_error(tmp_path):
    """🔴 THE UNKNOWN-FLAG GUARD CANNOT SEE AN OVER-SUPPLIED POSITIONAL. It
    catches `--aply`; it never looked at `reclaim … dirA dirB`, where the old
    parser silently kept the LAST and walked a manifest the caller did not mean —
    the same "reports nothing to do instead of erroring" shape the flag guard
    exists to refuse, and worse here because the run can delete."""
    home, manifest = build(tmp_path, identical=1)
    other = tmp_path / "other-generation"
    other.mkdir()
    p = subprocess.run(
        ["bash", str(RECLAIM), "--home", str(home), str(manifest), str(other),
         "--apply"],
        capture_output=True, text=True, timeout=30)
    out = p.stdout + p.stderr
    assert p.returncode == 1, out
    assert "two manifest directories" in out, out
    assert (home / ".config/app/same-0.md").is_file(), (
        "it ran anyway, against one of the two trees\n" + out
    )


def test_the_listing_is_capped_but_the_repair_is_not(tmp_path):
    """🔴 THE CAP IS ON THE LISTING, NEVER ON THE WORK. drift-check's
    DRIFT_DANGLING_MAX caps its per-path lines; this script capped nothing, so a
    bad generation could print hundreds of lines into the activation log. The
    hazard in fixing that is capping the wrong thing — a repair that silently
    stopped at the cap would be far worse than the noise.

    So both halves are asserted in one run: the LISTING is truncated with an
    explicit "... and N more", and every candidate is still RECLAIMED. The
    fixture size (7) is not a multiple of the cap (3), so an off-by-one in either
    direction moves the numbers.
    """
    home, manifest = build(tmp_path, identical=7)
    env = dict(os.environ)
    env["RECLAIM_LIST_MAX"] = "3"
    p = subprocess.run(
        ["bash", str(RECLAIM), "--home", str(home), str(manifest), "--apply"],
        capture_output=True, text=True, env=env, timeout=30)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert counts(out) == (7, 7, 0, 0), out

    listed = [ln for ln in out.splitlines() if ln.strip().startswith("x ")]
    assert len(listed) == 3, (
        "the listing was not capped at RECLAIM_LIST_MAX=3 (%d lines)\n%s"
        % (len(listed), out)
    )
    assert "... and 4 more" in out, (
        "the listing was truncated SILENTLY — a reader cannot tell 3 findings "
        "from 7\n" + out
    )
    assert "reclaimed 7 of 7" in out, (
        "the cap reached the WORK. Every candidate must still be repaired; a "
        "repair that stops at the display limit is a far worse bug than a long "
        "log.\n" + out
    )
    for i in range(7):
        assert not (home / (".config/app/same-%d.md" % i)).exists(), out


def test_the_unknown_kind_fallback_cannot_impersonate_a_real_kind():
    """🔴 THE ONE LABEL NO FIXTURE CAN REACH, GUARDED STRUCTURALLY BECAUSE OF IT.

    Both walks classify a non-regular target by probing `-d`, `-p`, `-S`, `-b`
    and `-c`, falling back to a literal. On Linux those probes are exhaustive —
    the remaining types are `-f` (handled earlier) and `-L` (handled earlier
    still) — so the fallback is UNREACHABLE and no fixture can drive it. An
    independent sweep therefore found a mutant that changed the fallback from
    "unknown" to "directory" SURVIVING every behavioural test.

    It is not harmless. A label that names a real kind is a report that ASSERTS
    something about a target nobody measured, on the one code path that exists
    precisely because the measurement failed — and this block's whole job is to
    hand a human an accurate description of what is in the way.

    So the property asserted is the one the mutant violates and the one no
    behavioural test can reach: the fallback must be DISTINGUISHABLE from every
    kind the probes can produce. Pinned in both readers, which must agree.
    """
    kinds = ("directory", "fifo", "socket", "device")
    for name, text in (("reclaim-managed-paths.sh", RECLAIM.read_text()),
                       ("drift-check.sh", DRIFT.read_text())):
        m = re.search(r'(?:KIND|w_kind)="([a-z]+)"\s*\n\s*#?\s*\[ -d ', text)
        assert m, (
            "%s no longer sets a fallback kind immediately before the `-d` "
            "probe; this guard cannot see it any more" % name
        )
        fallback = m.group(1)
        assert fallback not in kinds, (
            "%s falls back to %r, which is one of the kinds the probes can "
            "actually determine. The fallback is only ever used when every "
            "probe MISSED, so naming a real kind reports a measurement that was "
            "not made." % (name, fallback)
        )
