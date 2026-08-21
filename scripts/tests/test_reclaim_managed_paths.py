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
also the population it is LOSSLESS to delete, because those bytes are already in
the store. A file that DIFFERS is relinked by the very next switch (`ln -Tsf` on
the else branch) and might be someone's work; the script must not touch it.

Everything here runs against fixture trees in tmp_path. Nothing reads the
operator's real $HOME, the real /nix/store, or a real home-manager profile — a
test that skipped itself without one would be green on exactly the machine that
has the bug.
"""

import os
import re
import subprocess
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

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
def test_the_activation_entry_runs_this_script_with_apply_before_link_targets():
    """🔴 A SEAM, AND THE ONLY THING THAT MAKES rc 19 SELF-CLEARING. drift-check
    rc 19 is not wired to systemd's SuccessExitStatus, which is only defensible
    because an ordinary `home-manager switch` repairs the finding. That claim is
    false the moment the activation entry stops calling this script, stops
    passing --apply, or moves after checkLinkTargets — none of which any test of
    either file alone would notice.

    It must run BEFORE checkLinkTargets: the collision check is what aborts the
    switch on a foreign file, so a path this reclaims has to be gone by then.
    """
    src = HOME_NIX.read_text()
    m = re.search(
        r"home\.activation\.reclaimManagedPaths\s*=\s*"
        r"lib\.hm\.dag\.entryBefore\s*\[\s*\"checkLinkTargets\"\s*\](.*?)'';",
        src, re.S)
    assert m, (
        "nix/home.nix has no reclaimManagedPaths activation entry ordered before "
        "checkLinkTargets — rc 19 would then be a finding nothing clears"
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
