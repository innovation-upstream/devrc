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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

SCRIPT = REPO_ROOT / "scripts/resume-state.sh"
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
               fetch=True, home=None):
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
    return out.stdout


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
    assert any("never pushed" in f for f in findings(out)), findings(out)


def test_the_history_walk_is_capped_and_says_so(tmp_path, stub_bin):
    """Hitting the cap is "older than the newest N", never a clean answer.

    The cap exists so a long-lived path cannot turn a resume into a thousand
    rev-parse calls; it is env-overridable ONLY so this test can reach the
    branch with a 3-commit fixture instead of a 201-commit one.
    """
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n", "v2\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    out = run_resume(
        checkout, stub_bin, claude_dir=dep, skill_repo=checkout,
        extra_env={"RESUME_STATE_SKILL_SCAN_CAP": "1"},
    )
    line = skill_line(out)
    assert "scan capped" in line and "older than the newest 1" in line, line
    assert "CURRENT" not in line, line
    assert findings(out), out


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


def test_a_store_copy_is_labelled_as_needing_a_switch(tmp_path, stub_bin):
    """The other side of the same fork. The label is the actionable half: a
    store copy does not change on `git pull`, only on `home-manager switch`."""
    checkout, _, _ = make_devrc(tmp_path, ["v0\n", "v1\n"])
    dep = make_deployed(tmp_path, body="v0\n")
    line = skill_line(run_resume(checkout, stub_bin, claude_dir=dep, skill_repo=checkout))
    assert "store copy at" in line, line
    assert "home-manager switch" in line, line
    assert "live working-tree copy" not in line, line


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
    assert "COULD NOT MEASURE" in line, f"expected COULD NOT MEASURE; got: {line}"
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
    assert "COULD NOT MEASURE" in line and "is not on origin/main" in line, line
    assert "CURRENT" not in line, line
    assert any("neverpushed skill" in g for g in gap_lines(out)), gap_lines(out)


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
