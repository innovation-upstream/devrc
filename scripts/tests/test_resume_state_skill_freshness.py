"""Behavioural tests for the SKILL block in scripts/resume-state.sh.

THE BUG THIS PINS (measured 2026-08-25, before the fix):
a session invoked `/resume` at 17:19 and executed its step 6, which said to
claim a work item with `gh pr list` and push the branch early. That step had
already been REPLACED — `scripts/claim-work.sh` and a rewritten step 6 landed as
devrc #847 (200e6383, 19:39), saying "claim-work is the lock. This is a COMMAND,
not a habit." The session followed a superseded copy of its own instructions and
never noticed, because nothing in the system could say "the procedure you are
executing is behind origin/main".

A skill is loaded ONCE, from `~/.claude/skills/<name>/SKILL.md`, which nix
manages: a symlink either into the working tree (`mkOutOfStoreSymlink`) or into
a /nix/store copy written by the last `home-manager switch`. `git pull` moves
NEITHER, so the loaded text can be arbitrarily old with a perfectly clean
checkout.

WHAT IS ASSERTED — the DEPLOYED copy is the operand (that is what was read), the
report carries DIRECTION AND SIZE (N commits behind, naming the newest commit it
lacks) rather than a bare "differs", and every way the comparison can fail to
run prints COULD NOT MEASURE **and** a `!` GAP. A silent pass here would BE the
defect.

🔴 SCOPE, STATED IN THE TESTS AS WELL AS THE PR:
`test_this_check_cannot_see_a_skill_that_changes_MID_session` pins what this
does NOT cover. The check runs at resume START; #847 landed 2h20m after the
measured session began, so the very incident that motivated this would still not
be caught by it. What is caught is the other half — loading a copy that was
already behind.

HERMETIC. Every fixture is a throwaway `git init` repo under tmp_path with a
REAL bare repo on disk as `origin`, so the script's `git fetch` runs for real
with no network. `$HOME` is redirected into tmp_path and `$DEVRC` is POPPED, so
neither the real `~/.claude` nor the real `~/workspace/devrc` can answer for a
fixture — a suite that reads the host's own deploy state would pass or fail by
accident of when someone last ran `home-manager switch`. `gh`/`kubectl`/`curl`
are stubbed as tripwires that log and fail.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

SCRIPT = REPO_ROOT / "scripts/resume-state.sh"
RESUME_SKILL = REPO_ROOT / "claude/skills/resume/SKILL.md"
REL = "claude/skills/resume/SKILL.md"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git + bash on PATH",
)

# A handoff that names no PR, no branch and no clawgate task, so every other
# block of the digest stays on its skip path and DRIFT is about the SKILL block
# alone. (`extract_branches` disqualifies tokens with a file extension and
# `extract_prs` wants >=2 digits, so this text yields nothing.)
INERT_HANDOFF = "## Handoff\nnothing outstanding\n"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def stub_bin(tmp_path_factory):
    """`gh`/`kubectl`/`curl` tripwires on the front of $PATH."""
    d = tmp_path_factory.mktemp("skillstub")
    log = d / "invocations.log"
    for name in ("gh", "kubectl", "curl"):
        # testlib.mockbin owns the shebang: the nix build sandbox that runs the
        # authoritative gate has no /usr/bin/env, and patchShebangs cannot reach
        # a file a test writes at runtime.
        write_exec(d / name, f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\nexit 1\n')
    return d, log


def _git_env(where):
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(Path(where) / "gitconfig-global"),
            "GIT_CONFIG_SYSTEM": str(Path(where) / "gitconfig-system"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
    )
    return env


def _git(repo, *args, env=None):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, env=env or _git_env(repo)
    )


def make_devrc(tmp_path, versions, skill="resume"):
    """origin.git + `checkout` (stale by construction) + `pusher`.

    `versions` are successive SKILL.md bodies. v0 is committed and pushed, then
    `checkout` pulls it — so `checkout`'s own `origin/main` ref is stale for
    every later version until the script FETCHES. That is deliberate: a
    comparison against the local ref alone would report every fixture current.

    Returns (checkout, pusher, subjects) where subjects[i] is commit i's subject.
    """
    rel = f"claude/skills/{skill}/SKILL.md"
    origin = tmp_path / "origin.git"
    env = _git_env(tmp_path)
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=env
    )
    pusher = tmp_path / "pusher"
    checkout = tmp_path / "checkout"
    for dest in (pusher, checkout):
        subprocess.run(
            ["git", "clone", "-q", str(origin), str(dest)], check=True, env=env
        )

    (pusher / "claudedocs").mkdir(parents=True)
    (pusher / "claudedocs/handoff-fixture.md").write_text(INERT_HANDOFF)
    (pusher / rel).parent.mkdir(parents=True)

    subjects = []
    for i, body in enumerate(versions):
        (pusher / rel).write_text(body)
        paths = [rel] + (["claudedocs/handoff-fixture.md"] if i == 0 else [])
        _git(pusher, "add", *paths, env=_git_env(pusher))
        subj = f"skill v{i}"
        _git(pusher, "commit", "-qm", subj, env=_git_env(pusher))
        _git(pusher, "push", "-q", "origin", "main", env=_git_env(pusher))
        subjects.append(subj)
        if i == 0:
            # the checkout takes v0 and is then simply never updated
            _git(checkout, "pull", "-q", "origin", "main", env=_git_env(checkout))
    return checkout, pusher, subjects


def make_deployed(tmp_path, body=None, link_to=None, skill="resume", name="claude"):
    """A fake `~/.claude` whose skills/<skill>/SKILL.md is a copy or a symlink.

    `body`     a regular file — the /nix/store shape (a `home.file` copy).
    `link_to`  a symlink — the `mkOutOfStoreSymlink` shape, which resolves back
               into the working tree.
    """
    d = tmp_path / name
    (d / "skills" / skill).mkdir(parents=True)
    p = d / "skills" / skill / "SKILL.md"
    if link_to is not None:
        p.symlink_to(link_to)
    else:
        p.write_text(body)
    return d


def run_resume(repo, stub_bin, *, claude_dir=None, skill_repo=None, extra_env=None,
               fetch=True, home=None, raw=False):
    """Run the digest and return its stdout — or the whole CompletedProcess.

    `fetch=False` sets $RESUME_STATE_SKIP_FETCH, which the block declares on its
    own line; `raw=True` hands back stderr too, because "writes nothing to
    stderr" is a property one test asserts and stdout-only cannot see.
    """
    d, log = stub_bin
    env = _git_env(repo)
    env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    # 🔴 The host must not be able to answer for a fixture. $DEVRC is exported
    # by this repo's own .zshenv, and $HOME/.claude exists on every dev box; the
    # SKILL block consults both as fallbacks, so leaving either in place would
    # make these assertions depend on when someone last ran a switch.
    env.pop("DEVRC", None)
    # Unset unless a test says otherwise: UNSET is the shipped default (check
    # /resume) and it is the state a real session runs in. A suite that always
    # set it could not tell the default apart from an accident.
    env.pop("RESUME_STATE_SKILL", None)
    env["HOME"] = str(home or (Path(repo).parent / "fakehome"))
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    # Cleared by default: staleness is only visible after a fetch, and the
    # origin here is a real bare repo on disk, so the fetch costs nothing.
    env["RESUME_STATE_SKIP_FETCH"] = "" if fetch else "1"
    if claude_dir is not None:
        env["RESUME_STATE_CLAUDE_DIR"] = str(claude_dir)
    if skill_repo is not None:
        env["RESUME_STATE_SKILL_REPO"] = str(skill_repo)
    env.update(extra_env or {})
    out = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert out.returncode == 0, f"script failed rc={out.returncode}\n{out.stderr}"
    return out if raw else out.stdout


def skill_lines(stdout):
    """Every `skill-read: …` line, stripped. Fails loudly if there are none."""
    hits = [
        ln.strip() for ln in stdout.splitlines() if ln.strip().startswith("skill-read:")
    ]
    assert hits, f"no skill-read: line at all in:\n{stdout}"
    return hits


def skill_line(stdout):
    hits = skill_lines(stdout)
    assert len(hits) == 1, f"expected one skill-read: line, got {hits}"
    return hits[0]


def drift_lines(stdout):
    lines = stdout.splitlines()
    return [ln.strip() for ln in lines[lines.index("DRIFT") + 1:] if ln.strip()]


def gap_lines(stdout):
    return [ln[2:] for ln in drift_lines(stdout) if ln.startswith("! ")]


def findings(stdout):
    return [ln[2:] for ln in drift_lines(stdout) if ln.startswith("- ")]


# --------------------------------------------------------------------------- #
# harness sanity — the instrument must be able to see both answers
# --------------------------------------------------------------------------- #
def test_harness_positive_control_the_block_is_reachable_and_parsed(tmp_path, stub_bin):
    """POSITIVE CONTROL for `skill_line` and for the fixture shape.

    Every other assertion is "the line says X" or "the line does not say Y". A
    parser wired to nothing, or a fixture the script never looks at, would make
    the negative assertions pass for free.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0 body\n"])
    dep = make_deployed(tmp_path, body="v0 body\n")
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert " resume — " in line, line


def test_no_network_tool_is_ever_invoked(tmp_path, stub_bin):
    """The tripwires must stay untouched, and the tripwire itself must work.

    ⚠ AN INVARIANT GUARD, NOT REGRESSION COVERAGE — and the ONE test in this
    module that is GREEN at `origin/main` too (base reaches no network tool
    either, because base has no SKILL block at all). Labelled rather than
    counted: it pins hermeticity going forward, it does not demonstrate the bug.
    Every other test here was watched RED at origin/main 200e6383.
    """
    _, log = stub_bin
    checkout, _, _ = make_devrc(tmp_path, ["v0 body\n"])
    dep = make_deployed(tmp_path, body="v0 body\n")
    run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    assert not log.exists() or log.read_text() == "", log.read_text()
    # …and the stub really does log, so the emptiness above is a measurement
    # and not a counter wired to nothing.
    env = dict(os.environ, STUB_LOG=str(log))
    subprocess.run([str(stub_bin[0] / "gh"), "probe"], env=env, check=False)
    assert "gh probe" in log.read_text()


# --------------------------------------------------------------------------- #
# 🔴 THE REGRESSION — a deployed copy that is behind origin
# --------------------------------------------------------------------------- #
def test_a_stale_deployed_skill_is_reported_with_direction_and_size(tmp_path, stub_bin):
    """NEGATIVE CONTROL, and the measured incident in miniature.

    The deployed copy is v0; origin/main has moved twice since. RED before this
    change: the digest had no SKILL block at all, and printed a clean DRIFT
    all-clear over a session executing a two-revisions-old procedure.
    """
    checkout, _, subjects = make_devrc(tmp_path, ["v0\n", "v1\n", "v2\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    line = skill_line(out)
    assert "2 commit(s) BEHIND origin/main" in line, line
    assert REL in line, line
    # DIRECTION AND SIZE, plus the newest commit it does not have — "differs"
    # would leave the reader with no idea whether to care.
    assert "newest it lacks: " in line and subjects[-1] in line, line
    assert "CURRENT" not in line, line

    found = " ".join(findings(out))
    assert "THIS SESSION IS EXECUTING is STALE" in found, found
    assert str(dep / "skills/resume/SKILL.md") in found, found
    assert "git -C" in found and "show origin/main:" + REL in found, found
    assert "matches the handoff's claims" not in " ".join(drift_lines(out)), out


def test_staleness_is_measured_against_a_FETCHED_origin(tmp_path, stub_bin):
    """The checkout's own `origin/main` ref still points at v0 until the script
    fetches — this fixture is built that way on purpose. Skipping the fetch must
    therefore change the answer, which is what proves the fetch is load-bearing
    rather than incidental."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    stale_ref = _local_origin_main(checkout)

    with_fetch = skill_line(
        run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    )
    assert "1 commit(s) BEHIND" in with_fetch, with_fetch
    assert _local_origin_main(checkout) != stale_ref, (
        "fixture broken: the run did not actually advance origin/main"
    )


def _local_origin_main(repo):
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "origin/main"],
        capture_output=True, text=True, env=_git_env(repo),
    )
    return r.stdout.strip()


def test_an_up_to_date_deployed_skill_says_so_and_stays_clean(tmp_path, stub_bin):
    """POSITIVE CONTROL. A hardcoded 'BEHIND' passes the test above; this is what
    makes the warning a measurement. The number must be able to be zero."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v1\n")   # the tip
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    line = skill_line(out)
    assert "CURRENT with origin/main" in line, line
    assert "BEHIND" not in line and "STALE" not in line, line
    assert gap_lines(out) == [], gap_lines(out)
    assert "matches the handoff's claims" in " ".join(drift_lines(out)), out


def test_a_deployed_copy_matching_no_commit_is_not_a_pass(tmp_path, stub_bin):
    """Content nobody ever pushed is not "current" — it means the artefact was
    built from a tree that exists on one machine. Reporting it as clean is the
    same false green the whole block exists to remove."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="something nobody committed\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    line = skill_line(out)
    assert "matches NO commit" in line, line
    assert "CURRENT" not in line, line
    # the CAUSE is hedged — the exact sentence is pinned whole by
    # test_a_matchless_deployed_copy_HEDGES_the_cause_it_did_not_measure
    assert any(
        "could not place the deployed copy" in f for f in findings(out)
    ), findings(out)


def _norm(text, checkout):
    """The finding with its only run-dependent operand replaced.

    🔴 WHOLE-STRING, because the artifact under test IS PROSE. A guard on a word
    is walkable by rewording — and the first version of the capped test asserted
    only that `findings(out)` was non-empty, so the sentence it emitted could
    have said anything at all, including the cause it had not measured.
    """
    return text.replace(str(checkout), "<REPO>")


def test_the_history_walk_is_capped_and_says_what_it_did_NOT_measure(tmp_path, stub_bin):
    """Hitting the cap means the WALK ran out of budget — nothing more.

    🔴 THE SENTENCE IS THE SUBJECT. This branch used to be routed through the
    "built from a tree that was never pushed, so its instructions are not anyone
    else's" wording, which is a CAUSE the scan never measured: a capped walk is
    perfectly compatible with an ordinary old release. The whole normalised
    finding is pinned so a reword has to come here and be argued.

    BOUNDS OVERSHOOT ON PURPOSE — cap 2 over 5 commits with the deployed copy at
    the very bottom, so the walk is genuinely exhausted rather than landing
    exactly on its own boundary. The boundary itself is the next test's job.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n", "v2\n", "v3\n", "v4\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL_SCAN_CAP": "2"},
    )
    line = skill_line(out)
    assert "older than the newest 2 commit(s)" in line, (
        f"expected the capped sentence; got: {line}"
    )
    assert "the DISTANCE was not measured" in line, (
        f"expected the capped sentence to say the distance was not measured; got: {line}"
    )
    assert "CURRENT" not in line, line

    found = findings(out)
    assert len(found) == 1, found
    normalised = _norm(found[0], checkout).replace(str(tmp_path), "<TMP>")
    assert normalised == (
        "the /resume skill THIS SESSION IS EXECUTING is older than the newest 2 "
        "commit(s) touching claude/skills/resume/SKILL.md on origin/main — the "
        "deployed copy at <TMP>/claude/skills/resume/SKILL.md is NOT current, and "
        "this run stopped after 2 commit(s) without measuring by how much (raise "
        "RESUME_STATE_SKILL_SCAN_CAP for the number); read it with: git -C <REPO> "
        "show origin/main:claude/skills/resume/SKILL.md and follow THAT text, not "
        "the loaded one"
    ), normalised
    # ⚠ it names the EFFECTIVE cap, never `RESUME_STATE_SKILL_SCAN_CAP=<n>` —
    # that spelling read `=200` on an UNSET run and `=200` on an `abc` run,
    # while the gap line beside it correctly quoted `=abc`.
    assert "RESUME_STATE_SKILL_SCAN_CAP=" not in normalised, normalised
    # the cause it did NOT measure must not appear
    for false_cause in ("never pushed", "uncommitted tree", "not anyone else's"):
        assert false_cause not in " ".join(found), (found, false_cause)


def test_a_match_exactly_AT_the_cap_is_still_FOUND_not_reported_as_capped(
    tmp_path, stub_bin
):
    """🔴 THE BOUNDARY, which the test above structurally cannot see.

    `-gt` vs `-ge` on the scan counter is a one-character mutation. With the
    deployed copy anywhere BELOW the cap both operators emit byte-identical
    output, so a fixture placed there pins the wording and not the boundary —
    measured: `-gt` -> `-ge` SURVIVED the whole suite.

    This fixture puts the matching commit EXACTLY at the cap: cap 2, and the
    deployed copy is the second commit the walk examines. `-gt` finds it and
    reports `1 commit(s) BEHIND`; `-ge` stops one iteration early and reports
    the capped sentence instead. Different output, so the operator is observable.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n", "v2\n", "v3\n"])
    dep = make_deployed(tmp_path, body="v2\n")   # one commit back from the tip
    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL_SCAN_CAP": "2"},
    )
    line = skill_line(out)
    assert "1 commit(s) BEHIND origin/main" in line, (
        "the match sits exactly ON the cap and must still be found — an "
        f"off-by-one in the cap test stops the walk one commit early. got: {line}"
    )
    assert "older than the newest" not in line, line


def test_a_matchless_deployed_copy_HEDGES_the_cause_it_did_not_measure(
    tmp_path, stub_bin
):
    """Two shapes reach "matches NO commit", and the old sentence was false of
    one of them.

    (a) built from an uncommitted tree; (b) built from a branch that is PUSHED
    but not merged — which is what `home-manager switch --flake ~/workspace/devrc`
    off a feature branch produces, and CLAUDE.md recommends exactly that for
    validating a nix edit end to end. "was built from a tree that was never
    pushed, so its instructions are not anyone else's" asserts (a) and is wrong
    about (b). Whole normalised sentence, same reason as above.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="a revision that lives on no branch\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    found = findings(out)
    assert len(found) == 1, found
    normalised = _norm(found[0], checkout).replace(str(tmp_path), "<TMP>")
    assert normalised == (
        "the /resume skill THIS SESSION IS EXECUTING matches NO commit of "
        "claude/skills/resume/SKILL.md on origin/main — this walk could not place "
        "the deployed copy at <TMP>/claude/skills/resume/SKILL.md in that path's "
        "history, so what you loaded is not what origin/main says today; it may be "
        "uncommitted, on a branch that has not merged, or older than a rename of "
        "this path (the walk has no --follow); read it with: git -C <REPO> show "
        "origin/main:claude/skills/resume/SKILL.md to compare"
    ), normalised
    assert "never pushed" not in normalised, normalised
    # 🔴 AND IT NO LONGER ASSERTS ABSENCE FROM origin/main. That wording was
    # false for the rename case, which reaches this same branch with the content
    # present on origin/main at another path — see
    # test_a_renamed_skill_path_truncates_the_walk_never_to_CURRENT. The sentence
    # states the OBSERVATION (this walk could not place it) and lists causes as
    # possibilities; each earlier revision asserted a cause and was false of the
    # case found next.
    assert "is not on origin/main" not in normalised, normalised


# --------------------------------------------------------------------------- #
# 🔴 readlink -f IS THE ARBITER — live working-tree copy vs /nix/store copy
# --------------------------------------------------------------------------- #
def test_a_live_symlinked_skill_is_resolved_through_and_measured(tmp_path, stub_bin):
    """`mkOutOfStoreSymlink` shape: the deployed path is a symlink INTO the
    checkout. The comparison must follow it (the resolved path is what was
    read) and still report the checkout's distance from origin."""
    checkout, _, subjects = make_devrc(tmp_path, ["v0\n", "v1\n", "v2\n"])
    dep = make_deployed(tmp_path, link_to=checkout / REL)
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    line = skill_line(out)
    assert "live working-tree copy at" in line, line
    assert str(checkout / REL) in line, line
    assert "2 commit(s) BEHIND origin/main" in line, line
    assert subjects[-1] in line, line


def test_a_dirty_checkout_does_not_mask_a_stale_STORE_deploy(tmp_path, stub_bin):
    """🔴 THE SEAM BETWEEN THE TWO FORKS, and a mutant survived until this
    existed: dropping `[ "$live" -eq 1 ] &&` from the uncommitted-edits test was
    invisible while every fixture's checkout was CLEAN.

    The real shape is common: you are editing the skill in the repo (dirty tree)
    while the session runs a store copy deployed two switches ago. The tree's
    dirtiness says nothing about the DEPLOYED artefact, and letting it claim the
    "work in progress, not stale" branch would silence exactly the case this
    block exists for.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    (checkout / REL).write_text("v1\nand an uncommitted edit in the tree\n")
    dep = make_deployed(tmp_path, body="v0\n")     # store copy, two revisions old
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert "1 commit(s) BEHIND" in line, f"expected the store copy to read BEHIND; got: {line}"
    assert "UNCOMMITTED" not in line, (
        f"the TREE is dirty, not the deployed copy — got: {line}"
    )


def test_the_skill_repo_is_fetched_even_when_it_is_not_the_repo_being_resumed(
    tmp_path, stub_bin
):
    """🔴 The normal case: `/resume` runs in a PROJECT repo while the skill lives
    in devrc. Nothing else in the digest touches devrc, so if this block does not
    fetch it itself, the comparison runs against whatever ref that checkout
    happened to have — and the fixture is built stale on purpose.

    A mutant that skipped the fetch here survived until this test existed,
    because every earlier fixture resumed IN the skill repo, where
    `handoff_freshness` had already fetched it.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    project = tmp_path / "project"
    (project / "claudedocs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True, env=_git_env(tmp_path))
    (project / "claudedocs/handoff-project.md").write_text(INERT_HANDOFF)
    _git(project, "add", "claudedocs/handoff-project.md", env=_git_env(project))
    _git(project, "commit", "-qm", "seed", env=_git_env(project))

    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(project, stub_bin, claude_dir=dep, skill_repo=checkout)
    line = skill_line(out)
    assert "1 commit(s) BEHIND" in line, (
        "the skill repo was not fetched, so a stale local ref answered instead; "
        f"got: {line}"
    )
    # the digest is genuinely about the project repo, not devrc
    assert str(project) in out, out


def test_a_non_live_copy_is_not_called_a_live_working_tree_copy(tmp_path, stub_bin):
    """The other side of the live/not-live fork.

    ⚠ This USED to assert "store copy at …" for a plain file under tmp_path, and
    that assertion was WRONG once the label became three-way: a file that is
    neither in a checkout nor under /nix/store is UNMANAGED, and telling its
    reader a switch will replace it is a false instruction (see
    test_an_UNMANAGED_foreign_file_is_not_told_a_switch_will_replace_it and
    test_a_REAL_store_path_is_still_labelled_as_needing_a_switch, which now own
    the two non-live arms). What survives here is the fork itself.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert "live working-tree copy" not in line, line
    assert "home-manager" in line, line


def test_uncommitted_edits_on_the_live_copy_are_not_called_stale(tmp_path, stub_bin):
    """A skill being edited RIGHT NOW is this session's work-in-progress, not a
    stale deploy — the same fork the handoff check makes. It is still reported,
    as a gap, because executing unpushed instructions is its own trap."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    (checkout / REL).write_text("v0\nan uncommitted edit from THIS session\n")
    dep = make_deployed(tmp_path, link_to=checkout / REL)
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    line = skill_line(out)
    assert "UNCOMMITTED" in line, line
    assert "STALE" not in line and "BEHIND" not in line, line
    assert any("unpushed" in g for g in gap_lines(out)), gap_lines(out)
    assert findings(out) == [], findings(out)
    assert "matches the handoff's claims" not in " ".join(drift_lines(out)), out


# --------------------------------------------------------------------------- #
# 🔴 EVERY WAY IT CANNOT MEASURE IS A `!` GAP, NEVER A QUIET PASS
# --------------------------------------------------------------------------- #
def _assert_could_not_measure(out, needle):
    """🔴 EVERY MESSAGE HERE NAMES THE EXPECTED TEXT, not just the actual.

    A bare `assert needle in line, line` prints only what the code DID produce,
    so a mutation battery reading the failure output cannot tell "this guard
    went red" from "a different guard's message killed the test". Measured: two
    mutants scored KILLED-WRONG-REASON against the first draft of this helper,
    and one earlier expectation was matching a neighbouring guard's text.
    """
    line = skill_line(out)
    # 🔴 THE NEEDLE IS NAMED IN **BOTH** MESSAGES, not just the second. Removing
    # one of these guards does not necessarily produce a DIFFERENT
    # could-not-measure line — measured, removing the no-checkout guard makes
    # `git -C ""` fall back to the CURRENT directory and report a confident
    # CURRENT — so the first assert is the one that fires, and a message that
    # named only the shared banner left a mutation battery unable to tell which
    # guard had gone red.
    assert "COULD NOT MEASURE" in line, (
        f"expected COULD NOT MEASURE ({needle!r}); got: {line}"
    )
    assert needle in line, f"expected {needle!r} in the skill-read line; got: {line}"
    assert "CURRENT" not in line, f"must not claim CURRENT; got: {line}"
    gaps = gap_lines(out)
    assert any("/resume skill" in g for g in gaps), (
        f"expected a '! …/resume skill …' GAP; got: {gaps}"
    )
    # and the digest must NOT then issue a clean bill of health
    assert "matches the handoff's claims" not in " ".join(drift_lines(out)), out


def test_a_missing_deployed_skill_is_a_gap(tmp_path, stub_bin):
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    empty = tmp_path / "no-claude-dir"
    empty.mkdir()
    out = run_resume(checkout, stub_bin, claude_dir=empty, skill_repo=checkout)
    _assert_could_not_measure(out, "no deployed copy at")


def test_a_dangling_symlink_is_a_gap_not_a_pass(tmp_path, stub_bin):
    """🔴 Measured on the laptop 2026-08-11: 46 of 139 managed symlinks dangled
    into a garbage-collected /nix/store path over a perfectly clean checkout. A
    check built on `diff` alone reports an error nobody classifies; `readlink -f`
    is what tells the two apart."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    dep = make_deployed(tmp_path, link_to=tmp_path / "nix-store-gone" / "SKILL.md")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    _assert_could_not_measure(out, "resolves nowhere")


def test_no_git_checkout_of_the_source_is_a_gap(tmp_path, stub_bin):
    """A store copy carries no history; without a checkout of the source there
    is nothing to compare it to. That is an UNKNOWN, not a pass."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    notrepo = tmp_path / "not-a-repo"
    notrepo.mkdir()
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=notrepo)
    _assert_could_not_measure(out, "no git checkout of the skill source found")


def test_a_source_checkout_with_no_origin_remote_is_a_gap(tmp_path, stub_bin):
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    lone = tmp_path / "lone"
    (lone / "claudedocs").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(lone)], check=True, env=_git_env(tmp_path))
    (lone / "README.md").write_text("seed\n")
    _git(lone, "add", "README.md", env=_git_env(lone))
    _git(lone, "commit", "-qm", "seed", env=_git_env(lone))
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=lone)
    _assert_could_not_measure(out, "has no origin remote")


def test_a_skill_absent_from_origin_is_not_reported_as_matching(tmp_path, stub_bin):
    """🔴 A comparison against an absent operand reports SAME, not MISSING.
    `git diff --quiet <ref> -- <p>` exits 0 when <p> is on NEITHER side, so a
    check built on it alone would call a skill that has never been pushed
    'identical to origin/main'. This pins the cat-file existence probe."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    dep = make_deployed(tmp_path, body="brand new\n", skill="neverpushed")
    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL": "neverpushed"},
    )
    line = skill_line(out)
    assert "COULD NOT MEASURE" in line and "is not on origin/main" in line, (
        f"expected COULD NOT MEASURE ('is not on origin/main'); got: {line}"
    )
    assert "CURRENT" not in line, (
        f"a skill absent from origin/main must not read as CURRENT; got: {line}"
    )
    assert any("neverpushed skill" in g for g in gap_lines(out)), (
        f"expected a '! …/neverpushed skill …' GAP; got: {gap_lines(out)}"
    )


# --------------------------------------------------------------------------- #
# the default, the opt-out, and the override
# --------------------------------------------------------------------------- #
def test_the_DEFAULT_skill_is_resume_with_no_env_at_all(tmp_path, stub_bin):
    """🔴 The whole point is that it fires WITHOUT being asked. If the default
    were empty, every one of the tests above would still pass while the real
    /resume run checked nothing."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    # no RESUME_STATE_SKILL in the environment at all — run_resume pops it
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    line = skill_line(out)
    assert " resume — " in line, line
    assert "1 commit(s) BEHIND" in line, line


def test_an_empty_override_checks_nothing_but_SAYS_so(tmp_path, stub_bin):
    """`RESUME_STATE_SKILL=""` is how a hermetic caller says "check none". It is
    an explicit act and it prints an explicit line — it must never be a silent
    omission, and it must not be reachable by merely leaving the var unset."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL": ""},
    )
    line = skill_line(out)
    assert "NO skill was checked" in line, line
    assert "BEHIND" not in line and "CURRENT" not in line, line


def test_the_override_can_name_another_skill_and_several(tmp_path, stub_bin):
    """Other skills can borrow the check; the list is space-separated."""
    checkout, pusher, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    # a second skill, pushed once and never changed
    other = "claude/skills/handoff/SKILL.md"
    (pusher / other).parent.mkdir(parents=True)
    (pusher / other).write_text("handoff body\n")
    _git(pusher, "add", other, env=_git_env(pusher))
    _git(pusher, "commit", "-qm", "add handoff skill", env=_git_env(pusher))
    _git(pusher, "push", "-q", "origin", "main", env=_git_env(pusher))

    dep = make_deployed(tmp_path, body="v0\n")
    (dep / "skills/handoff").mkdir(parents=True)
    (dep / "skills/handoff/SKILL.md").write_text("handoff body\n")

    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL": "resume handoff"},
    )
    lines = skill_lines(out)
    assert len(lines) == 2, lines
    assert any(" resume — " in l and "BEHIND" in l for l in lines), lines
    assert any(" handoff — " in l and "CURRENT" in l for l in lines), lines


def test_bounded_fetch_refuses_an_empty_dir_instead_of_dying(tmp_path):
    """🔴 NON-BLOCKING IS A PROPERTY, NOT AN INTENTION.

    `FETCH_RC[""]` is `bad array subscript` in bash — an ERROR, printed twice,
    and the run exits 1. Found by a mutant that removed one of the guards that
    keeps `$d` non-empty: instead of falling through to the next check, the
    whole digest died. Every caller still guards `$d`, so this is defence in
    depth — and it is pinned here because an unreachable guard nobody has
    watched work is indistinguishable from no guard.
    """
    r = subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; bounded_fetch ""; echo "rc=$?"'],
        capture_output=True, text=True, timeout=30, cwd=str(tmp_path),
    )
    # 🔴 EXACT, not `"rc=1" in stdout`. At `origin/main` the function does not
    # exist, bash exits 127, and `rc=127` CONTAINS `rc=1` — the substring form
    # was GREEN on pre-change code, i.e. it proved nothing. Measured, not
    # reasoned: it passed at 200e6383 and only the exact match went red there.
    assert r.stdout.strip().splitlines()[-1] == "rc=1", r.stdout + r.stderr
    assert "bad array subscript" not in r.stderr, r.stderr


# --------------------------------------------------------------------------- #
# 🔴 A FINDING MUST NOT SUPPRESS "NOTHING WAS RECONCILED"
# --------------------------------------------------------------------------- #
def test_a_stale_skill_does_not_suppress_the_no_handoff_notice(tmp_path, stub_bin):
    """🔴 THE REGRESSION THIS ROUND EXISTS FOR, and the SKILL block created it.

    `main` printed the "(no handoff loaded …)" notice from an `elif` under
    `${#DRIFT[@]} -gt 0`, so ANY finding suppressed it. Before this block that
    was nearly unreachable — every finding came from reconciling a handoff, so a
    finding implied a handoff. A stale deployed skill is the first finding that
    does NOT, and the combination is ordinary: run `/resume` in a repo with no
    handoff doc, on a host whose deploy is behind.

    The reader then gets a bare `-` list under a header the /resume skill defines
    as "the lines where live state contradicts the handoff" — with no handoff
    reconciled at all. That code's own comment says the notice exists because
    "the reassuring shape of it is the actual harm"; a findings list without it
    is the same harm wearing a different face.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    # the fixture's handoff doc is committed to the PUSHER only, so this
    # checkout genuinely has none
    (checkout / "claudedocs/handoff-fixture.md").unlink()
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)

    assert "handoff: (none found — git-only)" in out, out
    drift = drift_lines(out)
    notice = "(no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"
    assert notice in drift, (
        "a finding suppressed the notice — the reader sees DRIFT findings with "
        f"no statement that nothing was reconciled:\n{drift}"
    )
    # and it is not INSTEAD of the finding: both facts are independent
    assert any("is STALE" in f for f in findings(out)), findings(out)
    # …and it LEADS, because it frames everything under it
    assert drift[0] == notice, drift


def test_the_notice_still_appears_when_there_is_no_finding_at_all(tmp_path, stub_bin):
    """POSITIVE CONTROL for the move: hoisting the notice out of the `elif`
    chain must not lose it on the path that always had it."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    (checkout / "claudedocs/handoff-fixture.md").unlink()
    dep = make_deployed(tmp_path, body="v0\n")          # current: no finding
    drift = drift_lines(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert drift == [
        "(no handoff loaded — nothing to reconcile; this is NOT a clean bill of health)"
    ], (
        # exactly once: hoisting the notice out of the `elif` chain without
        # removing it from the arm prints it TWICE on this path, which reads as
        # a formatting bug and trains the eye to skip it.
        f"the notice must appear exactly once and alone here; got: {drift}"
    )


def test_the_notice_is_absent_when_a_handoff_WAS_loaded(tmp_path, stub_bin):
    """NEGATIVE CONTROL. Printing it unconditionally would be the mirror-image
    lie — a run that DID reconcile a doc must not claim it reconciled nothing."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    assert "handoff: handoff-fixture.md" in out, out
    assert "no handoff loaded" not in out, (
        "the digest claimed nothing was reconciled while it had just reconciled "
        f"handoff-fixture.md:\n{drift_lines(out)}"
    )


# --------------------------------------------------------------------------- #
# 🔴 THE COULD-NOT-MEASURE VOCABULARY IS DERIVED FROM THE SCRIPT, NOT RESTATED
# --------------------------------------------------------------------------- #
def _could_not_measure_reasons():
    """Every reason `skill_freshness` can print, as its longest STATIC fragment.

    Scraped, deliberately: an enumeration maintained by hand is an enumeration
    that goes stale the first time someone adds a branch — measured, it already
    had: the script grew `no origin/<default-branch> ref in …` and `could not
    hash the deployed copy or the … blob` while SKILL.md still named five.

    🔴 THE FIRST VERSION WAS NARROWER THAN THIS DOCSTRING. It matched
    `COULD NOT MEASURE \\((.*?)\\)\\\\n'` — a parenthetical that ENDS a printf
    format string — so a reason emitted by `echo`, or one with any trailing text
    after the closing paren, was invisible, and `len(reasons) >= 7` could not
    notice because the floor counts what was found. Same class as the guard it
    was written to prevent.

    Now: find every `COULD NOT MEASURE (` anywhere in the source and walk
    forward balancing parens to its own close. That is emitter-agnostic and
    survives nesting and trailing text.

    ⚠ FULL-LINE COMMENTS ARE STRIPPED FIRST, and that is not tidiness: the
    widened scan immediately picked up a COMMENT that discusses the message
    shape (`a \\`COULD NOT MEASURE (…)\\` parenthetical`) and scored `…` as an
    eighth reason, which would have demanded a doc entry for a reason nothing
    emits. Prose about the emitter is not the emitter.
    """
    src = "\n".join(
        ln for ln in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not ln.lstrip().startswith("#")
    )
    marker = "COULD NOT MEASURE ("
    out = set()
    i = src.find(marker)
    while i != -1:
        j = i + len(marker)
        depth = 1
        while j < len(src) and depth:
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
            elif src[j] == "\n":            # unbalanced — do not run off the line
                break
            j += 1
        body = src[i + len(marker): j - 1 if depth == 0 else j]
        # the parts between interpolations are what a doc can carry verbatim
        frag = max((p.strip(" ;<>") for p in body.split("%s")), key=len).strip()
        if frag:
            out.add(frag)
        i = src.find(marker, j)
    return out


#  The doc spells the count as a WORD. Unpinned prose with a number in it is
#  exactly the drift `4519a276` had to fix elsewhere in this PR, so the word is
#  derived from the scrape rather than trusted.
_COUNT_WORDS = {
    4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}


def test_every_COULD_NOT_MEASURE_reason_the_script_can_print_is_documented():
    """Modelled on test_resume_state_clawgate.py's clock sweep, which exists
    because exactly this drifted once before."""
    reasons = _could_not_measure_reasons()
    assert len(reasons) >= 7, f"the scraper found too few reasons: {reasons}"
    doc = RESUME_SKILL.read_text(encoding="utf-8")
    word = _COUNT_WORDS.get(len(reasons))
    assert word, f"add {len(reasons)} to _COUNT_WORDS"
    assert f"**{word}** reasons" in doc, (
        f"the script can print {len(reasons)} COULD NOT MEASURE reasons, and "
        f"claude/skills/resume/SKILL.md does not say '**{word}** reasons'. A "
        "spelled-out count in prose is drift waiting to happen — that is why it "
        "is derived here instead of trusted."
    )
    for reason in sorted(reasons):
        assert reason in doc, (
            f"resume-state.sh can print COULD NOT MEASURE ({reason!r}…) and "
            "claude/skills/resume/SKILL.md never mentions it. A reader told "
            "there are five reasons, shown a sixth, has to guess what it means."
        )


def test_the_reason_scraper_can_report_a_missing_one():
    """🔴 NEGATIVE CONTROL on the instrument. A phrase check that can only pass
    is not a check — and this one's whole job is to go red on an addition
    nobody documented."""
    doc = RESUME_SKILL.read_text(encoding="utf-8")
    invented = "the moon was in the wrong phase"
    assert invented not in doc
    assert invented not in _could_not_measure_reasons()
    # POSITIVE half: the scraper really does read the script, not an empty set
    assert "no deployed copy at" in _could_not_measure_reasons()
    # 🔴 AND IT MUST NOT COUNT PROSE ABOUT THE EMITTER. Widening the scan to be
    # emitter-agnostic immediately swept in a COMMENT reading "a `COULD NOT
    # MEASURE (…)` parenthetical" and scored `…` as an eighth reason — which
    # would have demanded a SKILL.md entry for a message nothing prints.
    assert "…" not in _could_not_measure_reasons()


# --------------------------------------------------------------------------- #
# 🔴 THE FETCH — bounded, declared, and memoised BY RESULT
# --------------------------------------------------------------------------- #
def _source_and_call(snippet, cwd, timeout=90):
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; {snippet}'],
        capture_output=True, text=True, timeout=timeout, cwd=str(cwd),
    )


def _hanging_remote(tmp_path, name="hangs"):
    """A repo whose `git fetch` blocks forever, hermetically.

    The origin is an ordinary LOCAL bare repo and `remote.origin.uploadpack` is
    a script that sleeps, so `git fetch` spawns it and waits. No network, no
    ssh, no listening socket.

    🔴 THE TRANSPORT IS PART OF THE FIXTURE, NOT AN ACCIDENT. Two neater-looking
    mechanisms are both unusable here, and finding out cost a debugging round:
      * `ext::sh -c "sleep …"` — refused by git's default `protocol.ext.allow`;
      * `core.gitProxy` + a `git://` URL — hangs beautifully by hand, and fails
        INSTANTLY under this suite, because `testlib/nogit_plugin` exports
        `GIT_ALLOW_PROTOCOL=file` to refuse every transport that can reach
        another machine. That guard is right and must not be widened for a
        test; the fixture moved to the `file` transport instead. It presented as
        a 0.0 s "bounded" fetch — i.e. the timing assertion is what caught it,
        and a test asserting only `rc == 1` would have passed on a fetch that
        never hung at all.
    """
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=_git_env(tmp_path))
    subprocess.run(["git", "init", "-q", "--bare", str(repo / "origin.git")],
                   check=True, env=_git_env(tmp_path))
    # testlib.mockbin owns the shebang — the nix build sandbox that runs the
    # authoritative gate has no /usr/bin/env, and patchShebangs cannot reach a
    # file a test writes at runtime. test_runtime_shebangs.py enforces it, and
    # caught the hand-written `#!/bin/sh` this fixture first carried.
    sleeper = repo / "sleep-upload-pack.sh"
    write_exec(sleeper, "sleep 600\n")
    _git(repo, "remote", "add", "origin", str(repo / "origin.git"))
    _git(repo, "config", "remote.origin.uploadpack", str(sleeper))
    return repo


def test_a_hanging_fetch_is_BOUNDED_and_the_memo_spares_a_second_wait(tmp_path):
    """🔴 THE HEADLINE PRODUCTION PROPERTY: "a fetch that cannot complete must
    cost seconds, not a hung resume." It had no guard at all — deleting
    `timeout 25` SURVIVED all 398 tests in the three resume suites.

    Two properties, one wait, because the second is what makes the first
    affordable in production:
      * the first call returns rc 1 in ~25s rather than hanging for 600s;
      * the SECOND call returns from the memo in well under a second — so a
        digest whose handoff repo and skill repo are the same checkout pays the
        bounded wait ONCE, not twice.

    ⚠ COSTS ~25 SECONDS by construction. The sleep must outlast the timeout for
    the timeout to be observable, and the timeout is a production constant this
    test refuses to parameterise.
    """
    repo = _hanging_remote(tmp_path)
    t0 = time.monotonic()
    r = _source_and_call(
        f'bounded_fetch "{repo}"; echo "first=$?"; '
        f'S=$SECONDS; bounded_fetch "{repo}"; echo "second=$?"; '
        f'echo "memo_secs=$((SECONDS-S))"',
        cwd=repo,
    )
    elapsed = time.monotonic() - t0
    # 🔴 EXACT LINES, never `"first=1" in stdout` — `first=127` (the status of a
    # function that does not exist) CONTAINS `first=1`, so the substring form is
    # green against a build where `bounded_fetch` was never defined. That is the
    # same trap this module already hit once, in test_bounded_fetch_refuses_an_
    # empty_dir.
    lines = r.stdout.split()
    assert "first=1" in lines, r.stdout + r.stderr
    assert "second=1" in lines, (
        "the memo answered 0 for a fetch that FAILED — a caller reading that "
        f"claims fresh refs it does not have:\n{r.stdout}"
    )
    assert "memo_secs=0" in lines, (
        f"the second call re-ran the fetch instead of reading the memo:\n{r.stdout}"
    )
    assert 20 <= elapsed < 60, (
        f"the fetch was not bounded to ~25s (took {elapsed:.1f}s) — with the "
        "timeout removed the sleeping proxy runs for 600s"
    )


def test_every_git_fetch_in_the_script_is_wrapped_in_a_timeout():
    """The STRUCTURAL half, and it pins a RELATIONSHIP rather than a word: not
    "the string `timeout` appears" but "every `git … fetch` in this file is
    bounded". A second, unbounded fetch added elsewhere fails here even though
    the behavioural test above would never see it."""
    # 🔴 STRIP QUOTED SPANS FIRST. The first version matched any non-comment line
    # containing both `git` and `fetch`, so a future GAP MESSAGE telling the
    # reader to run `git fetch` — ordinary prose inside a quoted string — would
    # have failed as "an unbounded fetch". A scanner that cannot tell a command
    # from a sentence about a command reports on the wrong thing.
    quoted = re.compile(r"\"[^\"]*\"|'[^']*'")
    src = SCRIPT.read_text(encoding="utf-8")
    fetches = []
    for ln in src.splitlines():
        if ln.strip().startswith("#"):
            continue
        code = quoted.sub(" ", ln)          # what is left is command, not prose
        if re.search(r"\bgit\b[^#]*\bfetch\b", code):
            fetches.append((ln.strip(), code))
    assert fetches, "POSITIVE CONTROL: no `git fetch` found at all — the scanner is blind"
    for raw, code in fetches:
        m = re.search(r"\btimeout (\d+) git\b", code)
        assert m, f"an unbounded `git fetch`: {raw}"
        assert 1 <= int(m.group(1)) <= 60, f"the bound is not seconds-scale: {raw}"


def test_the_fetch_scanner_does_not_mistake_PROSE_for_a_command():
    """NEGATIVE CONTROL on the scanner above, and the reason it strips quotes.

    A line that merely *mentions* `git fetch` inside a message must not be
    scored as an unbounded invocation — otherwise the guard fires on a
    documentation change and everyone learns to edit the guard.
    """
    quoted = re.compile(r"\"[^\"]*\"|'[^']*'")
    prose = '    UNRECONCILED+=("could not reach origin — try `git fetch origin` by hand")'
    assert not re.search(r"\bgit\b[^#]*\bfetch\b", quoted.sub(" ", prose)), (
        "the scanner still reads a quoted sentence as a command"
    )
    real = '      timeout 25 git -C "$d" fetch --quiet origin >/dev/null 2>&1 || rc=1'
    assert re.search(r"\bgit\b[^#]*\bfetch\b", quoted.sub(" ", real)), (
        "POSITIVE CONTROL: stripping quotes hid the REAL invocation too"
    )


def test_a_SKIPPED_fetch_is_declared_on_the_line(tmp_path, stub_bin):
    """`RESUME_STATE_SKIP_FETCH` makes the comparison run against whatever refs
    are already on disk. That is a materially weaker claim and the line has to
    say so — the prefix had no assertion anywhere, and `run_resume`'s own
    `fetch=` parameter was dead code no test ever passed."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep,
                                 skill_repo=checkout, fetch=False))
    assert line.startswith("skill-read: [fetch skipped; compared against refs already on disk]"), line
    # …and the fixture is built stale-until-fetched, so the weaker claim really
    # is weaker: without the fetch this reads CURRENT.
    assert "CURRENT" in line, line


def test_a_FAILED_fetch_is_declared_on_the_line(tmp_path, stub_bin):
    """The other prefix. An unreachable origin must not silently degrade into a
    confident comparison against stale local refs."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    _git(checkout, "remote", "set-url", "origin", str(tmp_path / "no-such-origin.git"))
    dep = make_deployed(tmp_path, body="v0\n")
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert line.startswith("skill-read: [fetch failed; compared against refs already on disk]"), line


# --------------------------------------------------------------------------- #
# 🔴 ORDER, PROVENANCE, AND THE ODD INPUTS
# --------------------------------------------------------------------------- #
def test_the_SKILL_block_LEADS_the_digest(tmp_path, stub_bin):
    """Declared load-bearing in the code comment ("FIRST, deliberately") and in
    SKILL.md, and pinned nowhere: moving the call SURVIVED the suite. It leads
    because it is a claim about the INSTRUCTIONS, which the reader has to weigh
    before the findings those instructions produce."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    headers = [ln for ln in out.splitlines()
               if ln and not ln.startswith((" ", "#"))]
    assert headers == ["SKILL", "GIT/PR", "WORKLOAD", "ALERTS", "CLAWGATE", "DRIFT"], (
        f"the SKILL block must LEAD the digest; got {headers}"
    )


def test_an_UNMANAGED_foreign_file_is_not_told_a_switch_will_replace_it(
    tmp_path, stub_bin
):
    """🔴 A THREE-WAY ANSWER REPORTED AS TWO IS A FALSE INSTRUCTION.

    `readlink -f` can land in a checkout (live), in /nix/store (a copy a switch
    replaces), or in NEITHER — a hand-placed foreign file, which is the new-host
    case. CLAUDE.md is explicit that `home.file.force` does NOT clobber one, so
    labelling it "store copy … only a home-manager switch replaces it" sends the
    reader to run a switch that cannot fix it.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")     # a plain file under tmp_path
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert "UNMANAGED file at" in line, f"expected UNMANAGED provenance; got: {line}"
    assert "home-manager will NOT replace it" in line, (
        f"expected UNMANAGED provenance to say a switch will not fix it; got: {line}"
    )
    assert "only a home-manager switch replaces it" not in line, (
        f"expected UNMANAGED provenance, not the store-copy instruction; got: {line}"
    )
    # the comparison itself still happens — unmanaged is a provenance, not a gap
    assert "1 commit(s) BEHIND origin/main" in line, line


def test_the_provenance_noun_phrase_does_not_carry_its_own_remedy(tmp_path, stub_bin):
    """🔴 A NOUN PHRASE SPLICED INTO OTHER PEOPLE'S SENTENCES MUST STAY ONE.

    `$prov` is interpolated into a `COULD NOT MEASURE (…)` parenthetical and
    into a gap sentence after "is". Fusing the remedy into it produced nested
    parens inside the parenthetical, "…is a UNMANAGED file at X", and an
    imperative ("remove it and re-switch") spliced mid-clause of a sentence that
    continued "and no git checkout of its source could be found". The remedy
    lives in `$prov_note` and is appended only where a sentence can end.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    notrepo = tmp_path / "not-a-repo"
    notrepo.mkdir()
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=notrepo)
    line = skill_line(out)
    body = line.split("COULD NOT MEASURE (", 1)[1]
    assert "(" not in body, (
        "the provenance must not carry its remedy into the parenthetical — "
        f"nested parens in: {line}"
    )
    assert "remove it and re-switch" not in line, (
        f"the provenance must not carry its remedy here; got: {line}"
    )
    gap = " ".join(gap_lines(out))
    assert "is an UNMANAGED file at" in gap, (
        f"the gap sentence must read grammatically after 'is'; got: {gap}"
    )
    assert "is a UNMANAGED" not in gap, gap


def test_a_REAL_store_path_is_still_labelled_as_needing_a_switch(tmp_path, stub_bin):
    """POSITIVE CONTROL for the three-way fork. Without it the fix above would
    be indistinguishable from renaming EVERY store copy to "UNMANAGED", which
    would be a worse lie than the one it replaced.

    A test cannot write into /nix/store, so the deployed path is symlinked at a
    file that genuinely lives there: the realpath of `git`, which is a store
    path on this host AND inside the nix build sandbox — the two tiers that run
    this suite. Its CONTENT is irrelevant; the assertion is the provenance
    label. (A `tmp_path` directory named `nix/store` would not do: the script
    matches an absolute `/nix/store/*` prefix, which is the point.)
    """
    git_real = Path(os.path.realpath(shutil.which("git")))
    assert str(git_real).startswith("/nix/store/"), (
        f"fixture precondition: git does not resolve into /nix/store ({git_real}), "
        "so this control cannot exercise the store arm on this host"
    )
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, link_to=git_real)
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert "store copy at /nix/store/" in line, line
    assert "only a home-manager switch replaces it" in line, line
    assert "UNMANAGED" not in line, line


def test_a_non_integer_scan_cap_is_reported_and_writes_NOTHING_to_stderr(
    tmp_path, stub_bin
):
    """`[ "$scanned" -gt "abc" ]` prints `integer expected` to STDERR once per
    commit walked, then evaluates false — so the cap silently stops applying
    AND the digest scribbles on a stream its callers capture."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    proc = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout, raw=True,
        extra_env={"RESUME_STATE_SKILL_SCAN_CAP": "abc"},
    )
    assert "integer expected" not in proc.stderr, proc.stderr
    assert proc.stderr == "", f"the digest wrote to stderr:\n{proc.stderr}"
    assert any("not a positive integer" in g for g in gap_lines(proc.stdout)), (
        gap_lines(proc.stdout)
    )
    # and the walk still ran, on the default
    assert "1 commit(s) BEHIND origin/main" in skill_line(proc.stdout)


def test_a_LEADING_ZERO_cap_is_rejected_like_a_zero(tmp_path, stub_bin):
    """🔴 `[` READS DECIMAL, SO `00` IS A ZERO. A `|0)` arm rejects `0` and
    accepts `00` and `007`; `[ 1 -gt 00 ]` is TRUE, so the walk caps on the very
    first commit and the digest prints "older than the newest 00 commit(s)" —
    precisely the state the zero arm exists to prevent, wearing two characters.
    Measured. `run-tests.sh` rejects leading zeros for the same reason.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL_SCAN_CAP": "00"},
    )
    line = skill_line(out)
    assert "older than the newest 00" not in line, (
        f"a leading zero must be rejected, not used as a cap; got: {line}"
    )
    assert "1 commit(s) BEHIND origin/main" in line, (
        f"a leading zero must be rejected and the default used; got: {line}"
    )
    assert any("not a positive integer" in g for g in gap_lines(out)), (
        f"a leading zero must be rejected loudly; gaps: {gap_lines(out)}"
    )


def test_a_renamed_skill_path_truncates_the_walk_never_to_CURRENT(tmp_path, stub_bin):
    """RECORDED LIMIT, NOW ACTUALLY EXERCISED.

    🔴 THE PREVIOUS VERSION OF THIS TEST EXERCISED NO RENAME. It did
    `git mv REL REL.tmp` and back inside one commit, which git records as a
    plain `M` — measured: `--name-status` prints `M claude/…/SKILL.md`, and
    `git log -- <rel>` still returns every commit. The walk was never truncated,
    the digest printed the ordinary `2 commit(s) BEHIND`, and the single
    assertion ("CURRENT" absent) passed for that reason. Worse, a comment in
    resume-state.sh named this test as the pin for the `--follow` limitation, so
    the file claimed coverage that did not exist.

    The real shape is a file that ORIGINATES elsewhere and is renamed IN — which
    is exactly this repo's history: every skill moved from `claude/commands/` to
    `claude/skills/<name>/SKILL.md`. Then `git log -- <new path>` stops at the
    rename, and content older than it is genuinely invisible to the walk.

    Two assertions, because the limitation has a shape AND a safe direction:
      * the walk really is truncated — `matches NO commit`, not a number;
      * it is never the reassuring direction — a copy that is not the tip cannot
        read CURRENT, because the tip-hash comparison runs before the walk.
    """
    old_path = "claude/commands/resume.md"
    origin = tmp_path / "origin.git"
    env = _git_env(tmp_path)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)],
                   check=True, env=env)
    pusher = tmp_path / "pusher"
    checkout = tmp_path / "checkout"
    for dest in (pusher, checkout):
        subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, env=env)

    # v0 and v1 live at the OLD path; only then is it renamed into place.
    (pusher / old_path).parent.mkdir(parents=True)
    (pusher / "claudedocs").mkdir(parents=True)
    (pusher / "claudedocs/handoff-fixture.md").write_text(INERT_HANDOFF)
    (pusher / old_path).write_text("v0 — the pre-rename revision\n")
    _git(pusher, "add", old_path, "claudedocs/handoff-fixture.md")
    _git(pusher, "commit", "-qm", "skill v0 at the old path")
    (pusher / old_path).write_text("v1 — still at the old path\n")
    _git(pusher, "add", old_path)
    _git(pusher, "commit", "-qm", "skill v1 at the old path")
    _git(pusher, "push", "-q", "origin", "main")
    _git(checkout, "pull", "-q", "origin", "main")

    (pusher / REL).parent.mkdir(parents=True, exist_ok=True)
    _git(pusher, "mv", old_path, REL)
    _git(pusher, "commit", "-qm", "migrate commands/ -> skills/")
    (pusher / REL).write_text("v2 — after the migration\n")
    _git(pusher, "add", REL)
    _git(pusher, "commit", "-qm", "skill v2")
    _git(pusher, "push", "-q", "origin", "main")

    # FIXTURE PRECONDITION, asserted rather than assumed: the walk this test is
    # about must really be truncated at the rename.
    seen = subprocess.run(
        ["git", "-C", str(pusher), "log", "--format=%s", "origin/main", "--", REL],
        capture_output=True, text=True, env=_git_env(pusher),
    ).stdout.split("\n")
    assert "skill v0 at the old path" not in seen, (
        f"fixture: the walk was not truncated by the rename — it sees {seen}"
    )

    dep = make_deployed(tmp_path, body="v0 — the pre-rename revision\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    line = skill_line(out)
    assert "matches NO commit" in line, (
        f"expected the truncated-walk sentence after a rename; got: {line}"
    )
    assert "CURRENT" not in line, (
        "a stale copy read as CURRENT after a rename — the tip comparison must "
        f"run before the walk: {line}"
    )
    # 🔴 AND THE HEDGE MUST COVER THIS CASE. The content IS on origin/main, at
    # the old path — so a finding asserting "built from a tree that is not on
    # origin/main" would be false here, which is what the previous wording said.
    found = " ".join(findings(out))
    assert "older than a rename of this path" in found, found
    assert "is not on origin/main" not in found, (
        f"the finding asserts the content is absent from origin/main; it is "
        f"present at {old_path}: {found}"
    )


# --------------------------------------------------------------------------- #
# 🔴 WHAT THIS CHECK STRUCTURALLY CANNOT SEE
# --------------------------------------------------------------------------- #
def test_this_check_cannot_see_a_skill_that_changes_MID_session(tmp_path, stub_bin):
    """AN INVARIANT GUARD, NOT REGRESSION COVERAGE — and it is here to keep the
    scope claim honest rather than to catch a defect.

    The measured incident was mid-session: the session started at 17:19 with a
    copy that was current AT THAT MOMENT, and #847 landed at 19:39. This block
    runs once, at resume START. A run that measures CURRENT and a push that
    lands afterwards produce exactly the output below — a clean SKILL line — and
    nothing re-checks. Anyone reading `CURRENT with origin/main` is reading a
    claim about the instant the digest ran, never about the rest of the session.
    """
    checkout, pusher, _ = make_devrc(tmp_path, ["v0\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout)
    assert "CURRENT with origin/main" in skill_line(out)

    # …now the skill changes under the running session, exactly as #847 did.
    (pusher / REL).write_text("v1 — the procedure was replaced\n")
    _git(pusher, "add", REL, env=_git_env(pusher))
    _git(pusher, "commit", "-qm", "replace step 6", env=_git_env(pusher))
    _git(pusher, "push", "-q", "origin", "main", env=_git_env(pusher))

    # The session is already running; nothing re-reads the digest. The check
    # only reports on a NEW run — which is precisely the residual gap.
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert "1 commit(s) BEHIND" in line, line
