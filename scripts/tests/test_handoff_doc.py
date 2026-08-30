"""Behavioural tests for `scripts/lib/handoff_doc.py` — the /handoff write gate.

THE INCIDENT THIS SUITE EXISTS FOR. A session re-entered work from a handoff
doc, did ten minutes of real analysis, then wrote and PUSHED an updated handoff
to a shared branch with no confirm gate. The operator never approved it. The
update itself was correct and valuable — it answered the doc's open question and
corrected a prior misreading — so the fix is NOT "stop updating the handoff";
it is to make the update safe: gated on the push, append-only where the
diagnosis state lives, and never offered at all by a session that went nowhere.

🔴 WHAT MAKES THESE TESTS WORTH ANYTHING — every one of them exercises the real
CLI in a real throwaway git repo with a real local bare remote, and the
decline-direction ones HASH THE WHOLE REPO TREE either side of the run. A gate
that has only ever been watched to ACCEPT is not a gate; a suite that only
checks the new text arrived would pass a wholesale rewrite that silently
deleted every earlier finding.

Nothing here touches a real repository or a real remote: every path is under
pytest's tmp_path and every push target is a bare repo created in the same
tmp_path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from testlib import hermetic_git  # noqa: E402
from testlib.mockbin import write_exec  # noqa: E402

TOOL = REPO_ROOT / "scripts" / "lib" / "handoff_doc.py"
HANDOFF_SKILL = REPO_ROOT / "claude" / "skills" / "handoff" / "SKILL.md"


def _load_module():
    spec = importlib.util.spec_from_file_location("handoff_doc", TOOL)
    assert spec and spec.loader, TOOL
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


hd = _load_module()


# --------------------------------------------------------------------------
# fixtures — a real repo, a real bare remote, a real prior handoff
# --------------------------------------------------------------------------

# 🔴 Two DISTINCT earlier findings with pairwise-distinct wording. A fixture
# whose findings share phrasing lets a merge that drops one still match a
# substring of the other, and the test goes green on a doc that lost content.
PRIOR_FINDING_A = (
    "### the widget queue drains at 3/s, not 30/s\n"
    "- **Symptom + exact repro:** `GET /queue/depth` climbs monotonically under load.\n"
    "- **Observed (with values):** depth 41,022 after 240s; drain counter 3.04/s.\n"
    "- **Ruled out:** disk saturation — `iostat` util stayed under 11% throughout.\n"
)
PRIOR_FINDING_B = (
    "### the retry budget is consumed before the first real attempt\n"
    "- **Symptom + exact repro:** every call reports `attempts=5` with one upstream hit.\n"
    "- **Observed (with values):** `x-retry-remaining: 0` on the FIRST response.\n"
    "- **Leading hypothesis:** the budget is decremented in the wrapper, not the client.\n"
)
NEW_FINDING_C = (
    "### the at-max reading was misread — the adapter serves a different window\n"
    "- **Observed (with values):** adapter 110m against the raw source's 32m.\n"
    "- **Supersedes:** the earlier at-max interpretation recorded above was wrong.\n"
)

BASE_DOC = f"""# Handoff: sample-topic — 2026-08-01

## Goal
Make the sample subsystem stop dropping work under load.

## State now
- Branch / PR: `feat/sample` / none
- What's DONE this session: the queue instrumentation landed
- Deploy/verify status: NOT deployed

## Open investigations — live diagnosis state
{PRIOR_FINDING_A}
{PRIOR_FINDING_B}
## Next steps (ranked)
1. Instrument the drain loop.
2. Re-read the retry wrapper.

## Gotchas / decisions / dead-ends
- Bumping the pool size did nothing; the ceiling is not connections.

## How to verify
`python3 tools/queue_probe.py --for 240`
"""

UPDATE_DOC = f"""## State now
- Branch / PR: `feat/sample` / #99
- What's DONE this session: the drain loop is fixed and merged
- Deploy/verify status: deployed, verified against the real path

## Open investigations — live diagnosis state
{NEW_FINDING_C}
## Next steps (ranked)
1. Watch the drain rate for a day. forcing: gate — the load soak blocks the release
"""

# 🔴 The fixture BASE_DOC's ranked items carry NO `forcing:` field, ON PURPOSE.
# Rule (j) reads the UPDATE, never the merged doc, so a repo full of legacy
# untagged items must keep updating cleanly — a gate that refused on history
# would be red on every established repo, which `claude/RULES.md` calls worse
# than no gate. `test_legacy_base_items_are_not_retroactively_refused` is the
# guard, and it can only be honest while the base above stays untagged.


# Hermetic git: the sandbox and the dev host must behave the same, so no global
# or system config (a stray `core.hooksPath` or `commit.gpgsign` would otherwise
# decide whether these tests can commit at all).
# 🔴 BACKGROUND GIT MAINTENANCE IS PINNED OFF, AND THAT IS WHAT MAKES
# `tree_hash` HONEST. That function hashes every file under the repo root
# INCLUDING `.git`, deliberately — a stray lockfile is signal, not noise, because
# it is how you catch the tool writing something it should not. But git also
# creates and deletes `.git/objects/maintenance.lock` on its own, from a
# `gc --auto` fired by an ordinary command; when that happened between
# `rglob` listing the file and `read_bytes` reading it, the walk died on
#
#     FileNotFoundError: …/work/.git/objects/maintenance.lock
#
# Measured in CI (`devrc-ci-x9zkh`, on a PR touching neither this file nor the
# tool). Filtering `.git` out of the hash would have "fixed" it by deleting the
# guard's whole point, so the SOURCE of the transience is removed instead: with
# maintenance and gc pinned off, the only lock that can ever appear under a
# fixture repo is one the code under test created — which is exactly what
# `tree_hash` is watching for.
#
# It lives in GIT_ENV rather than in each `git init` because there are five of
# those and the next fixture would silently be the sixth. Both `_sh` and
# `run_tool` pass this env, so it reaches the TOOL's git calls too, not just the
# fixtures' — and env vars are inherited, so any git the tool spawns gets it.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    # 🔴 CONSOLIDATED 2026-08-24. These five keys were spelled out here, again
    # in restore-verify, and nowhere in five other modules that need them — the
    # N-sites-wrong-at-N-1 shape. One copy now: testlib/hermetic_git.py, whose
    # ledger test fails when a new module joins the class without them.
    **hermetic_git.MAINTENANCE_OFF,
}


def _sh(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV)
    )
    assert proc.returncode == 0, f"{args} failed: {proc.stderr or proc.stdout}"
    return proc.stdout


def tree_hash(root: Path) -> str:
    """A hash of EVERY file under root, path and bytes — including `.git`.

    This is the decline-direction instrument: it sees a written doc, a new
    commit, a moved ref and a stray lockfile alike. Hashing only the handoff
    doc would miss a commit; hashing only `git log` would miss the file.
    """
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(str(path.relative_to(root)).encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def test_git_maintenance_cannot_fire_inside_a_fixture_repo(repo: Path):
    """🔴 THE REGRESSION GUARD FOR `tree_hash`'s FileNotFoundError.

    `tree_hash` hashes `.git` on purpose, so it can only stay honest if git is
    not concurrently creating and deleting lock files under it. This asserts the
    pin is REACHING a real git process launched the way this module launches
    them — not merely that the constant is spelled correctly, which a dict
    literal cannot get wrong in an interesting way.

    The negative control is the load-bearing half: it re-runs the same query with
    ONLY the `GIT_CONFIG_COUNT` injection removed — `GIT_CONFIG_GLOBAL` and
    `GIT_CONFIG_SYSTEM` stay pinned at `/dev/null`, so exactly one variable
    moves. If git defaulted these off by itself, the assertions above would pass
    while proving nothing, and this control is what tells them apart.
    """
    # `--default ''` so an UNSET key exits 0 and returns empty, instead of exit 1
    # killing the call inside `_sh`'s own `returncode == 0` assert. Without it a
    # broken pin fails with "('git','config',…) failed:" — a message about the
    # helper, not about maintenance being armed.
    def _cfg(key: str) -> str:
        return _sh("git", "config", "--get", "--default", "", key, cwd=repo).strip()

    assert _cfg("maintenance.auto") == "false", (
        "background git maintenance is NOT pinned off in a fixture repo, so "
        "`gc --auto` can create and delete .git/objects/maintenance.lock mid-walk "
        "and `tree_hash` will die on FileNotFoundError"
    )
    assert _cfg("gc.auto") == "0", "gc.auto is not pinned off in a fixture repo"

    without_injection = {
        k: v for k, v in dict(os.environ, **GIT_ENV).items()
        if not k.startswith(("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY", "GIT_CONFIG_VALUE"))
    }
    probe = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "maintenance.auto"],
        capture_output=True, text=True, env=without_injection,
    )
    assert probe.returncode != 0, (
        "git reports maintenance.auto without our injection, so the assertions "
        f"above are about a git default rather than about GIT_ENV: {probe.stdout!r}"
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repo with one commit, a handoff doc, and a bare remote to push to."""
    origin = tmp_path / "origin.git"
    _sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)

    work = tmp_path / "work"
    work.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=work)
    for k, v in (
        ("user.name", "Test Runner"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
    ):
        _sh("git", "config", k, v, cwd=work)
    _sh("git", "remote", "add", "origin", str(origin), cwd=work)

    docs = work / "claudedocs"
    docs.mkdir()
    (docs / "handoff-sample-topic.md").write_text(BASE_DOC, encoding="utf-8")
    (work / "README.md").write_text("sample\n", encoding="utf-8")
    _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", "README.md", cwd=work)
    _sh("git", "commit", "-q", "-m", "seed", cwd=work)
    _sh("git", "push", "-q", "origin", "main", cwd=work)
    return work


@pytest.fixture()
def update_file(tmp_path: Path) -> Path:
    p = tmp_path / "update.md"
    p.write_text(UPDATE_DOC, encoding="utf-8")
    return p


def run_tool(repo: Path, *extra: str, update: Path | None = None, advanced: str | None
             = "the drain loop is fixed and the at-max reading was corrected",
             topic: str = "sample-topic"):
    argv = [sys.executable, str(TOOL), "--repo", str(repo), "--topic", topic]
    if update is not None:
        argv += ["--update", str(update)]
    if advanced is not None:
        argv += ["--advanced", advanced]
    argv += list(extra)
    return subprocess.run(
        argv, capture_output=True, text=True, env=dict(os.environ, **GIT_ENV)
    )


def advance_remote(tmp_path: Path) -> None:
    """Push a commit to origin from a SECOND clone — the other session."""
    # Clone the BARE origin, not the working repo — pushing into a non-bare
    # checkout's current branch is refused, which would fail for a reason
    # that has nothing to do with what the callers are testing.
    other = tmp_path / "other"
    _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
    for k, v in (("user.name", "Other"), ("user.email", "o@example.invalid"),
                 ("commit.gpgsign", "false")):
        _sh("git", "config", k, v, cwd=other)
    (other / "OTHER.md").write_text("other session\n", encoding="utf-8")
    _sh("git", "add", "--", "OTHER.md", cwd=other)
    _sh("git", "commit", "-q", "-m", "another session's work", cwd=other)
    _sh("git", "push", "-q", "origin", "main", cwd=other)


_SHA40 = re.compile(r"\b[0-9a-f]{40}\b")
_SHA12 = re.compile(r"\b[0-9a-f]{12}\b")


def normalised_run(res, repo: Path, update: Path) -> str:
    """`rc` + stdout + stderr, with every varying path and sha tokenised.

    🔴 THE INSTRUMENT FOR THE BYTE-IDENTITY PINS, and the first version of it was
    WRONG in the direction that matters. It replaced only the repo path — but
    git's own error text quotes SIBLING paths beside the repo (`…/sibling`,
    `…/nope.git`), so two genuinely unchanged exit paths compared DIFFERENT
    purely because the scratch directory was named differently. Replace the
    parent too, and always AFTER the more specific paths or the specific ones
    stop matching.

    Its positive control is that failure: before the parent was tokenised the
    comparison DID report a difference, so it is not a check that can only pass.
    """
    text = f"rc={res.returncode}\n--- stdout\n{res.stdout}--- stderr\n{res.stderr}"
    text = text.replace(str(repo), "<REPO>").replace(str(update), "<UPDATE>")
    text = text.replace(str(repo.parent), "<TMP>")
    return _SHA12.sub("<SHA12>", _SHA40.sub("<SHA40>", text))


def doc_of(repo: Path) -> str:
    return (repo / "claudedocs" / "handoff-sample-topic.md").read_text(encoding="utf-8")


def commit_shas(repo: Path) -> list[str]:
    return _sh("git", "log", "--format=%H", cwd=repo).split()


_HUNK = re.compile(r"^(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@)")


def diff_body(text: str) -> list[str]:
    """The hunks of a unified diff — `@@` headers and the +/-/space payload.

    Two normalizations, both so a diff this tool PRINTED can be compared with
    one `git show` produced: file headers are dropped (git spells them
    differently), and a hunk header is truncated after the second `@@` (git
    appends the enclosing section's text, difflib does not). Collection stops
    at the first line that is not part of a diff, which is what keeps the
    tool's own trailing prose out of the comparison.
    """
    out: list[str] = []
    seen = False
    for line in text.splitlines():
        if line.startswith(("--- ", "+++ ", "diff --git", "index ")):
            continue
        m = _HUNK.match(line)
        if m:
            seen = True
            out.append(m.group(1))
            continue
        if not seen:
            continue
        if line[:1] in ("+", "-", " "):
            out.append(line)
        else:
            break
    return out


# --------------------------------------------------------------------------
# instrument controls — before any verdict is read off these helpers
# --------------------------------------------------------------------------


class TestInstrumentControls:
    def test_tree_hash_moves_when_a_byte_moves(self, repo: Path) -> None:
        """POSITIVE CONTROL. A hash that never changes would make every
        decline-direction assertion below vacuously green."""
        before = tree_hash(repo)
        (repo / "claudedocs" / "handoff-sample-topic.md").write_text(
            BASE_DOC + "x", encoding="utf-8"
        )
        assert tree_hash(repo) != before

    def test_tree_hash_moves_when_only_a_commit_is_made(self, repo: Path) -> None:
        """POSITIVE CONTROL, second axis: a commit with no working-tree change
        must still be visible, or 'nothing was written' could hide a commit."""
        before = tree_hash(repo)
        _sh("git", "commit", "-q", "--allow-empty", "-m", "probe", cwd=repo)
        assert tree_hash(repo) != before

    def test_tree_hash_is_stable_across_a_no_op(self, repo: Path) -> None:
        """NEGATIVE CONTROL: it does not just always differ."""
        assert tree_hash(repo) == tree_hash(repo)

    def test_diff_body_can_report_a_difference(self) -> None:
        """POSITIVE CONTROL for the diff comparator used by the accept test."""
        a = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-one\n+two\n"
        b = "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-one\n+three\n"
        assert diff_body(a) != diff_body(b)
        assert diff_body(a) == ["@@ -1 +1 @@", "-one", "+two"]


# --------------------------------------------------------------------------
# rule (b) — the gate, watched in BOTH directions
# --------------------------------------------------------------------------


class TestDeclineWritesNothing:
    """The direction a gate is almost never tested in."""

    def test_default_mode_leaves_the_tree_byte_identical(
        self, repo: Path, update_file: Path
    ) -> None:
        before = tree_hash(repo)
        res = run_tool(repo, update=update_file)
        assert res.returncode == 0, res.stderr
        assert "status=proposed" in res.stdout
        assert tree_hash(repo) == before, (
            "the default (pre-confirm) mode changed something in the repo. It "
            "must print the diff and nothing else — a decline is then the "
            "absence of a second invocation, not a code path that has to behave."
        )

    def test_default_mode_makes_no_commit(self, repo: Path, update_file: Path) -> None:
        before = commit_shas(repo)
        run_tool(repo, update=update_file)
        assert commit_shas(repo) == before

    def test_default_mode_moves_no_remote_ref(
        self, repo: Path, update_file: Path
    ) -> None:
        remote = Path(_sh("git", "remote", "get-url", "origin", cwd=repo).strip())
        before = _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo)
        run_tool(repo, update=update_file, advanced="fixed the drain loop")
        assert _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo) == before

    def test_the_diff_is_shown_because_that_is_now_the_runs_whole_job(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 The y/N was retired 2026-08-23, so the diff is ALL this run
        produces — it exists to put the change in the transcript before the
        confirm lands it. A default-mode run that writes nothing AND shows
        nothing would be pure cost.

        The old assertion here was `"(y/N)" in res.stdout`. That is now the
        WRONG direction and is asserted as such below."""
        res = run_tool(repo, update=update_file)
        assert diff_body(res.stdout), f"no diff hunks printed:\n{res.stdout}"
        assert "status=proposed" in res.stdout
        assert "--confirm --push" in res.stdout, (
            "the run must name the command that lands it — with no prompt, this "
            "line is the only thing telling the caller what comes next"
        )

    def test_no_run_asks_a_yes_no_question(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE RETIREMENT, asserted rather than described. Operator decision
        2026-08-23: the prompt was always answered `y`, so it bought a round trip
        and no safety — the same evidence that retired the index write's prompt
        on 2026-08-15.

        Watched to fail against the pre-change module, where `status=proposed`
        printed ``Ask exactly one `update the handoff doc and push it? (y/N)```.

        ⚠ Scoped to the tool's OWN output. It cannot stop a skill body telling an
        agent to ask; `test_the_skill_does_not_reinstate_the_prompt` covers that
        side, and neither test covers a human deciding to ask anyway."""
        res = run_tool(repo, update=update_file)
        both = res.stdout + res.stderr
        assert "(y/N)" not in both, (
            f"the tool is still asking a yes/no question:\n{both}"
        )
        assert "y ->" not in both and "n ->" not in both

    def test_the_warnings_survive_the_prompt(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 The prompt was not what made this safe, and removing it must not
        take the part that did. With no y/N the refusing statuses and the
        warnings printed above the diff are the ONLY reader between a proposal
        and a pushed commit, so the run has to say so where a caller will see
        it."""
        res = run_tool(repo, update=update_file)
        assert "READ THE WARNINGS ABOVE THE DIFF" in res.stdout, res.stdout

    def test_push_without_confirm_is_refused(
        self, repo: Path, update_file: Path
    ) -> None:
        before = tree_hash(repo)
        res = run_tool(repo, "--push", update=update_file)
        assert res.returncode == hd.EXIT_USAGE
        assert "--push requires --confirm" in res.stderr
        assert tree_hash(repo) == before


class TestAcceptLandsExactlyWhatWasShown:
    def test_confirm_makes_exactly_one_commit(
        self, repo: Path, update_file: Path
    ) -> None:
        before = commit_shas(repo)
        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, res.stderr
        after = commit_shas(repo)
        assert len(after) == len(before) + 1, f"{len(after) - len(before)} commits made"
        assert "status=written commit=" in res.stdout

    def test_the_commit_carries_exactly_the_diff_that_was_shown(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 The load-bearing pair: what the human approved and what landed."""
        shown = run_tool(repo, update=update_file).stdout
        run_tool(repo, "--confirm", update=update_file)
        landed = _sh("git", "show", "--format=", "-p", "HEAD", cwd=repo)
        assert diff_body(shown) == diff_body(landed)

    def test_the_commit_touches_only_the_handoff_doc(
        self, repo: Path, update_file: Path
    ) -> None:
        """Even with unrelated work already staged — the commit is path-limited,
        so a handoff update can never smuggle a sibling change into a shared
        branch. `git add -A` in the caller's history must not reach this commit."""
        (repo / "secret-wip.txt").write_text("unrelated\n", encoding="utf-8")
        _sh("git", "add", "--", "secret-wip.txt", cwd=repo)
        run_tool(repo, "--confirm", update=update_file)
        touched = _sh("git", "show", "--name-only", "--format=", "HEAD", cwd=repo).split()
        assert touched == ["claudedocs/handoff-sample-topic.md"]

    def test_confirm_alone_does_not_push(self, repo: Path, update_file: Path) -> None:
        """Rule (b): only the push is the act that needs consent, and it takes
        its own flag.

        ⚠ The FILE write is local and reversible; the COMMIT this also makes is
        not "cheap" in the sense that phrasing implied — see
        `TestALocalCommitDoesNotGoUNANNOUNCED`, which is why that path now states
        what it left behind.
        """
        remote = Path(_sh("git", "remote", "get-url", "origin", cwd=repo).strip())
        before = _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo)
        run_tool(repo, "--confirm", update=update_file)
        assert _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo) == before

    def test_confirm_push_moves_the_remote(
        self, repo: Path, update_file: Path
    ) -> None:
        remote = Path(_sh("git", "remote", "get-url", "origin", cwd=repo).strip())
        before = _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo).split()
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 0, res.stderr
        assert "status=pushed" in res.stdout
        after = _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo).split()
        assert len(after) == len(before) + 1


# --------------------------------------------------------------------------
# rule (c) — replace the status header, APPEND the findings
# --------------------------------------------------------------------------


class TestAppendVsReplace:
    def test_both_earlier_findings_survive_verbatim(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 The assertion a 'the new text is present' test would not make.
        A wholesale rewrite passes that one and fails this."""
        run_tool(repo, "--confirm", update=update_file)
        after = doc_of(repo)
        assert PRIOR_FINDING_A in after, "the first earlier finding was lost"
        assert PRIOR_FINDING_B in after, "the second earlier finding was lost"

    def test_the_new_finding_is_appended_after_them(
        self, repo: Path, update_file: Path
    ) -> None:
        run_tool(repo, "--confirm", update=update_file)
        after = doc_of(repo)
        assert NEW_FINDING_C in after
        assert after.index(PRIOR_FINDING_A) < after.index(NEW_FINDING_C)
        assert after.index(PRIOR_FINDING_B) < after.index(NEW_FINDING_C)

    def test_the_status_header_is_replaced_not_appended(
        self, repo: Path, update_file: Path
    ) -> None:
        run_tool(repo, "--confirm", update=update_file)
        after = doc_of(repo)
        assert "Deploy/verify status: deployed, verified against the real path" in after
        assert "Deploy/verify status: NOT deployed" not in after, (
            "the old status survived — 'State now' is current state and must be "
            "OVERWRITTEN, or the doc accumulates contradictory status blocks"
        )
        assert after.count("## State now") == 1

    def test_next_steps_are_replaced(self, repo: Path, update_file: Path) -> None:
        run_tool(repo, "--confirm", update=update_file)
        after = doc_of(repo)
        assert "Watch the drain rate for a day." in after
        assert "Instrument the drain loop." not in after
        assert after.count("## Next steps (ranked)") == 1

    def test_a_superseding_finding_with_the_same_heading_still_appends(self) -> None:
        """The incident's own shape: the update CORRECTED a prior reading. Both
        halves must survive — the value is seeing that it was corrected, not
        finding the old reading silently gone."""
        base = "## Open investigations — live diagnosis state\n### at-max time\n- reading: 32m\n"
        upd = "## Open investigations\n### at-max time\n- corrected reading: 110m\n"
        out = hd.merge(base, upd)
        assert "- reading: 32m" in out
        assert "- corrected reading: 110m" in out
        assert out.index("- reading: 32m") < out.index("- corrected reading: 110m")

    def test_gotchas_append_too(self, repo: Path, tmp_path: Path) -> None:
        upd = tmp_path / "u.md"
        upd.write_text(
            "## Gotchas / decisions / dead-ends\n- The retry wrapper owns the budget.\n",
            encoding="utf-8",
        )
        run_tool(repo, "--confirm", update=upd)
        after = doc_of(repo)
        assert "Bumping the pool size did nothing" in after
        assert "The retry wrapper owns the budget." in after

    def test_a_section_the_update_omits_is_left_alone(
        self, repo: Path, update_file: Path
    ) -> None:
        """An update is a delta. Omitting `## Goal` must not delete it."""
        run_tool(repo, "--confirm", update=update_file)
        after = doc_of(repo)
        assert "Make the sample subsystem stop dropping work under load." in after
        assert "`python3 tools/queue_probe.py --for 240`" in after

    def test_a_section_only_the_update_has_is_added(
        self, repo: Path, tmp_path: Path
    ) -> None:
        upd = tmp_path / "u.md"
        upd.write_text("## Rollback\n`git revert <sha>` and redeploy.\n", encoding="utf-8")
        run_tool(repo, "--confirm", update=upd)
        assert "## Rollback" in doc_of(repo)

    def test_headings_inside_a_fenced_block_are_not_sections(self) -> None:
        """The skill's own step-2 template is a fenced markdown block full of
        `## ` lines. Splitting on those would shred any doc that quotes one."""
        base = "## Goal\nold goal\n\n## Template\n```markdown\n## State now\nnot a heading\n```\n"
        upd = "## Goal\nnew goal\n"
        out = hd.merge(base, upd)
        assert "new goal" in out and "old goal" not in out
        assert "```markdown\n## State now\nnot a heading\n```" in out

    def test_split_sections_is_lossless(self) -> None:
        pre, secs = hd.split_sections(BASE_DOC)
        assert pre + "".join(h + b for h, b in secs) == BASE_DOC

    def test_append_bucket_classification(self) -> None:
        assert hd.append_bucket("## Open investigations — live diagnosis state")
        assert hd.append_bucket("## Findings")
        assert hd.append_bucket("## Gotchas / decisions / dead-ends")
        assert hd.append_bucket("## State now") is None
        assert hd.append_bucket("## Next steps (ranked)") is None
        assert hd.append_bucket("## How to verify") is None


# --------------------------------------------------------------------------
# rule (d) — a session that did not advance state gets NO offer
# --------------------------------------------------------------------------


class TestNoAdvanceMakesNoOffer:
    def test_missing_advanced_exits_no_advance(
        self, repo: Path, update_file: Path
    ) -> None:
        before = tree_hash(repo)
        res = run_tool(repo, update=update_file, advanced=None)
        assert res.returncode == hd.EXIT_NO_ADVANCE
        assert "status=no-advance" in res.stderr
        assert tree_hash(repo) == before

    def test_missing_advanced_prints_NO_DIFF_AT_ALL(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 Not an empty diff — no offer. An empty diff is still a prompt, and
        a prompt is what a session that went nowhere must not produce."""
        res = run_tool(repo, update=update_file, advanced=None)
        both = res.stdout + res.stderr
        assert diff_body(both) == [], f"a diff was offered anyway:\n{both}"
        # No prompt exists anywhere since 2026-08-23, so this clause no longer
        # distinguishes this path from a normal proposal — it is kept as a cheap
        # invariant, and `diff_body(both) == []` above is what carries the case.
        assert "(y/N)" not in both

    @pytest.mark.parametrize(
        "sentinel", ["none", "nothing", "  N/A ", "no change", "-", "unchanged", ""]
    )
    def test_sentinel_advanced_values_are_refused(
        self, repo: Path, update_file: Path, sentinel: str
    ) -> None:
        res = run_tool(repo, update=update_file, advanced=sentinel)
        assert res.returncode == hd.EXIT_NO_ADVANCE
        assert diff_body(res.stdout + res.stderr) == []

    def test_confirm_does_not_bypass_the_advance_check(
        self, repo: Path, update_file: Path
    ) -> None:
        """The check is not a pre-flight for the proposal step — it gates the
        write itself, so re-running with --confirm cannot walk around it."""
        before = tree_hash(repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file, advanced="none")
        assert res.returncode == hd.EXIT_NO_ADVANCE
        assert tree_hash(repo) == before

    def test_a_real_advance_is_accepted(self, repo: Path, update_file: Path) -> None:
        """NEGATIVE CONTROL on the refusal: it does not refuse everything."""
        res = run_tool(repo, update=update_file, advanced="fixed the drain loop")
        assert res.returncode == 0
        assert diff_body(res.stdout)

    def test_advance_is_real_predicate(self) -> None:
        assert not hd.advance_is_real(None)
        assert not hd.advance_is_real("   ")
        assert not hd.advance_is_real("Nothing")
        assert hd.advance_is_real("the drain loop is fixed")


class TestNoChangeIsNotAnEmptyCommit:
    """The guard that does NOT depend on the caller answering honestly."""

    def test_an_update_that_changes_nothing_exits_no_change(
        self, repo: Path, tmp_path: Path
    ) -> None:
        upd = tmp_path / "u.md"
        upd.write_text(
            "## State now\n"
            "- Branch / PR: `feat/sample` / none\n"
            "- What's DONE this session: the queue instrumentation landed\n"
            "- Deploy/verify status: NOT deployed\n",
            encoding="utf-8",
        )
        before = tree_hash(repo)
        res = run_tool(repo, "--confirm", update=upd, advanced="re-read the doc")
        assert res.returncode == hd.EXIT_NO_CHANGE, res.stdout + res.stderr
        assert "status=no-change" in res.stderr
        assert diff_body(res.stdout + res.stderr) == []
        assert tree_hash(repo) == before, "a no-op update still touched the repo"

    def test_no_change_makes_no_commit(self, repo: Path, tmp_path: Path) -> None:
        upd = tmp_path / "u.md"
        upd.write_text("## Goal\nMake the sample subsystem stop dropping work under load.\n",
                       encoding="utf-8")
        before = commit_shas(repo)
        run_tool(repo, "--confirm", update=upd, advanced="re-read the doc")
        assert commit_shas(repo) == before


# --------------------------------------------------------------------------
# rule (f) — a REPLACE that drops durable-looking content says so, and still
#            does it; and rule (g) — every run states the buckets
# --------------------------------------------------------------------------
#
# 🔴 THE FIELD TRAP, REPRODUCED. A session hit this on two CONSECUTIVE handoff
# updates: a completed arc, a survey's negative result and a closure were each
# about to be deleted for sitting under "State now", which is a REPLACE heading.
# It caught them by hand-reading the diff and then wrote a prose gotcha — which
# is a prompt-tuning patch for something structural, hence rule (f).
#
# 🔴 THE THREE FIXTURE LINES CARRY EXACTLY ONE SIGNAL EACH, and that is a
# MUTATION-ISOLATION requirement, not tidiness. A line reading
# `- 🔴 MEASURED 2026-08-14 — …` carries BOTH a date and an evidence verb, so a
# mutant that breaks the date regex is still caught by the verb branch and is
# scored SURVIVED while the guard it targets is genuinely dead. Each line below
# is therefore orthogonal, and `TestTheDurableSignalsAreEachReachable` pins that
# orthogonality so a later edit cannot quietly reintroduce the overlap.
DURABLE_DATED_LINE = (
    "- 🔴 2026-08-14 — the coverage sweep's ledger exempts generated files, so "
    "nothing will catch a regression in them."
)
DURABLE_VERB_LINE = (
    "- RETRACTED — the capture survey came back empty: 0 of 48 captures showed "
    "the banner, so the earlier sighting was a local cache."
)
DURABLE_OPEN_LINE = (
    "- OPEN: the coverage sweep still exempts generated files."
)
# 🔴 THE CLOSURE-SHAPED CASE, and it needs its own fixture for the same reason
# the others do — one line per WORD, not merely one per branch. `RETRACTED` and
# `CLOSED` are both the evidence-verb branch, so a single line carrying either
# would let a mutant that deletes one word from the list hide behind the other,
# and the mutant would be scored SURVIVED with that word genuinely dead.
DURABLE_CLOSED_LINE = (
    "- The brand-coverage question is CLOSED: every surface now reads from one "
    "token set."
)

# The base doc's "State now" carries all three, exactly as the field case did.
DURABLE_BASE_DOC = f"""# Handoff: drop-topic — 2026-08-14

## Goal
Stop the coverage sweep from exempting generated files.

## State now
- Branch / PR: `feat/coverage-sweep` / none
{DURABLE_DATED_LINE}
{DURABLE_VERB_LINE}
{DURABLE_CLOSED_LINE}
{DURABLE_OPEN_LINE}
- Deploy/verify status: NOT deployed

## Findings
### the ledger is built from the wrong glob
- **Observed (with values):** 0 generated paths in a ledger of 311 entries.
- **Ruled out:** a stale manifest — re-derived it on 2026-08-13 and it matched.

## Next steps (ranked)
1. Re-derive the ledger from the build manifest.
"""

# …and the update rewrites the status without carrying any of them forward.
DURABLE_UPDATE_DOC = """## State now
- Branch / PR: `feat/coverage-sweep` / #412
- Deploy/verify status: deployed, verified against the real path
"""

# Ordinary status churn: pairwise-distinct wording, no durable signal anywhere.
# The dates here are the shape the predicate must NOT fire on — welded into a
# path token, and inside a code span besides.
CHURN_BASE_DOC = """# Handoff: churn-topic

## State now
- Branch / PR: `feat/churn` / none
- What's DONE this session: the probe harness landed
- Design spec: `claudedocs/churn-design-2026-08-02.md`
- Deploy/verify status: NOT deployed
"""
CHURN_UPDATE_DOC = """## State now
- Branch / PR: `feat/churn` / #77
- What's DONE this session: the probe harness is wired to the runner
- Design spec: `claudedocs/churn-design-2026-08-02.md`
- Deploy/verify status: deployed
"""

WARNING_HEAD = "line(s) that look DURABLE"


@pytest.fixture()
def durable_repo(repo: Path) -> Path:
    """`repo`, with the handoff doc replaced by the field-trap base."""
    (repo / "claudedocs" / "handoff-sample-topic.md").write_text(
        DURABLE_BASE_DOC, encoding="utf-8"
    )
    _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=repo)
    _sh("git", "commit", "-q", "-m", "seed durable", cwd=repo)
    return repo


def write_update(tmp_path: Path, text: str, name: str = "durable-update.md") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def warning_block(stdout: str) -> str:
    """The rule (f) block only — from its headline to the line before the diff."""
    if WARNING_HEAD not in stdout:
        return ""
    start = stdout.index("🔴 This replace DROPS")
    rest = stdout[start:]
    cut = rest.find("--- a/")
    return rest if cut < 0 else rest[:cut]


# 🔴 THE RED-AT-BASE MATRIX, and the honest split inside it. This whole file was
# run against `d12f84c8` — the commit before rules (f) and (g) existed — by
# extracting that revision's `handoff_doc.py` and `SKILL.md` into a scratch tree
# and pointing this exact test file at them. Result: **32 failed, 97 passed at
# d12f84c8; 129 passed at HEAD.**
#
# ⚠ EIGHT OF THE NEW TESTS PASS AT BASE, and they are NOT regression coverage —
# they pin an invariant the bug never violated, and saying so is the difference
# between coverage and the appearance of it:
#
#   the three SILENCE / exemption tests   base never warns at all, so their
#   (churn, default fixture, carried-      green there is vacuous. They are
#    forward, APPEND-exempt)               proven LIVE at HEAD by the mutation
#                                          battery instead — `verb-list-widened-
#                                          with-DONE` and `append-branch-also-
#                                          classified` each kill one.
#   the three EXIT-CODE invariants        4, 5 and the constants must not move;
#                                          that they held before is the point.
#   `test_the_module_spells_no_openness_   base has no openness regex either.
#    grammar_of_its_own`                   It guards the FUTURE, not the past.
#
# MUTATION BATTERY (run under `PYTHONDONTWRITEBYTECODE=1`, the module restored
# from a byte-copy and re-hashed after every mutant): **20 mutants, 20 killed by
# the specifically expected test**, one no-op mutant kept as a negative control
# and SURVIVED. Three rounds were needed, and every round found a hole in THESE
# ASSERTIONS rather than in the code:
#   1. `POSITIVE-CONTROL-bucket-label` SURVIVED — `BUCKET_REPLACE` renamed to
#      `REPLACED` still satisfied a substring `in`. Now whole normalised lines.
#   2. `code-span-strip-removed` SURVIVED — the fixture's date was suppressed by
#      the OTHER net too, so the assertion could not tell a live net from a dead
#      one. Now each suppression case is chosen so only ONE net can catch it.
#   3. `verb-list-widened-with-DONE` came back SKIPPED (its anchor had moved when
#      `CLOSED` was added) — a skipped mutant is a coverage claim nobody holds,
#      so the harness prints ANCHOR NOT UNIQUE loudly rather than scoring it.


class TestAReplaceThatDropsDurableContentSaysSo:
    """The field trap, and both directions of the guard it produced."""

    def test_the_field_trap_is_named(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """The whole point: three durable lines are about to be deleted for
        sitting under a REPLACE heading, and the run says which ones."""
        upd = write_update(tmp_path, DURABLE_UPDATE_DOC)
        res = run_tool(durable_repo, update=upd)
        assert res.returncode == hd.EXIT_OK, res.stderr
        block = warning_block(res.stdout)
        assert block, f"no durable-drop warning was printed:\n{res.stdout}"
        # 🔴 The whole normalised headline, not a keyword — a reworded headline
        # is an output change a `in` assertion walks straight past.
        assert block.splitlines()[0] == (
            "🔴 This replace DROPS 4 line(s) that look DURABLE "
            "(they sit under a REPLACE heading):"
        ), block
        for fragment in (
            "the coverage sweep's ledger exempts generated files",
            "the capture survey came back empty",
            "The brand-coverage question is CLOSED",
            "the coverage sweep still exempts generated files",
        ):
            assert fragment in block, f"{fragment!r} was not named:\n{block}"

    def test_it_names_the_heading_and_a_usable_base_line_number(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """🔴 The address must resolve. A line number computed off the MERGED
        doc, or off the section body rather than the file, would still print a
        plausible integer — so this opens the base doc at every number it
        printed and checks the line is the one that was flagged."""
        upd = write_update(tmp_path, DURABLE_UPDATE_DOC)
        res = run_tool(durable_repo, update=upd)
        base_lines = DURABLE_BASE_DOC.splitlines()
        found = re.findall(r"^  (.+?):(\d+): (.*?)  \[", warning_block(res.stdout),
                           re.M)
        assert len(found) == 4, res.stdout
        for heading, line_no, quoted in found:
            assert heading == "State now", heading
            assert base_lines[int(line_no) - 1].strip().startswith(quoted[:40])

    def test_the_warning_comes_BEFORE_the_diff(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """It annotates the diff, so it must arrive before it — after several
        hundred lines of hunks it is a footnote nobody reaches."""
        upd = write_update(tmp_path, DURABLE_UPDATE_DOC)
        out = run_tool(durable_repo, update=upd).stdout
        assert out.index(WARNING_HEAD) < out.index("--- a/claudedocs/")

    def test_it_WARNS_and_never_refuses(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """🔴 The direction that makes this survivable. A gate that can block
        the ordinary case is a permanently-red gate everyone clicks through: the
        write must still land, exit 0, one commit, with the warning shown."""
        upd = write_update(tmp_path, DURABLE_UPDATE_DOC)
        before = commit_shas(durable_repo)
        res = run_tool(durable_repo, "--confirm", update=upd)
        assert res.returncode == hd.EXIT_OK, res.stdout + res.stderr
        assert WARNING_HEAD in res.stdout
        assert "status=written commit=" in res.stdout
        assert len(commit_shas(durable_repo)) == len(before) + 1
        after = (durable_repo / "claudedocs" / "handoff-sample-topic.md").read_text(
            encoding="utf-8"
        )
        assert "Deploy/verify status: deployed" in after
        assert DURABLE_DATED_LINE not in after, (
            "the replace was BLOCKED. Rule (f) warns and never refuses — "
            "replacing stale status is the ordinary case."
        )

    def test_ordinary_status_churn_is_SILENT(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 The other direction, and the failure mode being fixed. A warning
        that fires on every run is noise, and noise is what gets ignored."""
        (repo / "claudedocs" / "handoff-sample-topic.md").write_text(
            CHURN_BASE_DOC, encoding="utf-8"
        )
        upd = write_update(tmp_path, CHURN_UPDATE_DOC, "churn.md")
        res = run_tool(repo, update=upd)
        assert res.returncode == hd.EXIT_OK, res.stderr
        assert diff_body(res.stdout), "nothing changed — the silence is vacuous"
        assert WARNING_HEAD not in res.stdout, (
            "ordinary status churn produced a durable-drop warning:\n" + res.stdout
        )

    def test_the_suites_own_default_fixture_is_SILENT_too(
        self, repo: Path, update_file: Path
    ) -> None:
        """A second, independent silence sample: the doc every other test in
        this file runs against. One hand-built quiet fixture could be quiet by
        construction; this one was written years of tests ago for other reasons."""
        res = run_tool(repo, update=update_file)
        assert WARNING_HEAD not in res.stdout, res.stdout

    def test_a_durable_line_CARRIED_FORWARD_is_not_named(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """Moving the line forward is one of the two remedies the block names,
        so taking it must clear the warning for that line — otherwise the
        remedy does not work and the block fires forever."""
        upd = write_update(
            tmp_path,
            "## State now\n"
            "- Branch / PR: `feat/coverage-sweep` / #412\n"
            f"{DURABLE_DATED_LINE}\n"
            f"{DURABLE_VERB_LINE}\n"
            f"{DURABLE_CLOSED_LINE}\n"
            f"{DURABLE_OPEN_LINE}\n"
            "- Deploy/verify status: deployed\n",
        )
        res = run_tool(durable_repo, update=upd)
        assert res.returncode == hd.EXIT_OK, res.stderr
        assert diff_body(res.stdout), "nothing changed — the silence is vacuous"
        assert WARNING_HEAD not in res.stdout, res.stdout

    def test_only_SOME_carried_forward_still_names_the_rest(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """NEGATIVE CONTROL on the test above: carrying forward is not a blanket
        mute. Three of four kept, one dropped, one named."""
        upd = write_update(
            tmp_path,
            "## State now\n"
            f"{DURABLE_DATED_LINE}\n"
            f"{DURABLE_VERB_LINE}\n"
            f"{DURABLE_CLOSED_LINE}\n"
            "- Deploy/verify status: deployed\n",
        )
        block = warning_block(run_tool(durable_repo, update=upd).stdout)
        assert "DROPS 1 line(s)" in block, block
        assert "the coverage sweep still exempts generated files" in block

    def test_an_APPEND_section_is_never_warned_about(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """Rule (c) already guarantees those survive verbatim, so a warning
        there would be about a deletion that cannot happen.

        🔴 DISCRIMINATING BY CONSTRUCTION: `## Findings` in the fixture carries a
        dated line that `durable_reason` DOES flag, and the update below does not
        repeat it. So a mutant that ran the classification over the append branch
        too has something to report here and the test goes red — without that
        line the assertion could not tell the branches apart."""
        upd = write_update(
            tmp_path,
            "## Findings\n"
            "### a second glob is applied after the ledger is built\n"
            "- **Observed (with values):** 311 entries in, 311 out.\n",
            "findings.md",
        )
        res = run_tool(durable_repo, update=upd)
        assert res.returncode == hd.EXIT_OK, res.stderr
        assert WARNING_HEAD not in res.stdout, res.stdout

    def test_a_fenced_sample_is_not_scanned(self, tmp_path: Path) -> None:
        """A pasted log or sample command inside a fence carries dates and says
        nothing durable — 610 of the real corpus's REPLACE-bucket lines are
        inside one."""
        base = (
            "## How to verify\n"
            "```\n"
            "$ probe --since 2026-08-14\n"
            "ok 2026-08-14T09:00:00Z\n"
            "```\n"
        )
        report = hd.merge_report(base, "## How to verify\n`probe --now`\n")
        assert report.dropped == (), report.dropped

    def test_a_date_welded_into_a_path_is_not_a_claim(self) -> None:
        """Handoff docs cite each other constantly; a filename is a reference,
        not a measurement. TWO nets, and each case below is chosen so that
        exactly ONE of them can suppress it — a case both nets catch cannot tell
        a live net from a dead one, which is how the first draft of this test
        scored a `prose = line` mutant SURVIVED."""
        # only the LEADING path boundary can suppress this (no code span).
        assert hd.durable_reason(
            "- Design spec: claudedocs/churn-design-2026-08-02.md and nothing else"
        ) is None
        # only the CODE-SPAN strip can suppress this: the date's left neighbour
        # is a space, so the boundary lets it through.
        assert hd.durable_reason(
            "- Re-run it with `probe --since 2026-08-02` and compare"
        ) is None
        # and both together on the shape the corpus actually carries.
        assert hd.durable_reason(
            "- Design spec: `claudedocs/churn-design-2026-08-02.md`"
        ) is None

    def test_the_same_date_OUTSIDE_a_code_span_IS_a_claim(self) -> None:
        """NEGATIVE CONTROL on the three suppressions above: they are not a
        blanket mute on dates. Same date, free-standing, flagged."""
        assert hd.durable_reason(
            "- Re-ran it on 2026-08-02 and the ledger was still short"
        ) == "dated claim"

    def test_the_listing_is_BOUNDED_and_says_how_many_it_elided(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 An unbounded list printed ABOVE the diff pushes the diff off the
        screen. The elision count is what stops `…` reading as `and nothing
        else worth mentioning`."""
        many = "\n".join(
            f"- 🔴 2026-08-{day:02d} — measurement number {day} of the ledger sweep."
            for day in range(1, 10)
        )
        (repo / "claudedocs" / "handoff-sample-topic.md").write_text(
            f"## State now\n{many}\n", encoding="utf-8"
        )
        upd = write_update(tmp_path, "## State now\n- all of it is stale now\n", "b.md")
        block = warning_block(run_tool(repo, update=upd).stdout)
        assert "DROPS 9 line(s)" in block, block
        rows = [ln for ln in block.splitlines() if re.match(r"^  \S.*:\d+: ", ln)]
        assert len(rows) == hd.DROPPED_SHOWN_MAX, f"{len(rows)} rows printed:\n{block}"
        assert "… and 3 more not shown" in block, block

    def test_a_very_long_line_is_clipped(self) -> None:
        """The second bound: one pathological line must not be the whole block."""
        long = "- 2026-08-14 — " + ("x" * 4000)
        row = hd.dropped_durable_report(
            [hd.DroppedDurable("State now", 4, long, "dated claim")]
        )
        assert max(len(ln) for ln in row.splitlines()) < 300, row
        assert "…" in row


class TestTheDurableSignalsAreEachReachable:
    """🔴 A guard that can never execute is not a guard, and a mutation sweep
    over overlapping fixtures reports SURVIVED for a guard that is genuinely
    dead. `durable_reason` tries three signals in precedence order, so each
    fixture line must be reachable — matched by ITS signal and by no other."""

    @pytest.mark.parametrize(
        "line,expected",
        [
            (DURABLE_DATED_LINE, "dated claim"),
            (DURABLE_VERB_LINE, "evidence verb"),
            (DURABLE_CLOSED_LINE, "evidence verb"),
            (DURABLE_OPEN_LINE, "openness/open"),
        ],
        ids=["dated", "verb-RETRACTED", "verb-CLOSED", "openness"],
    )
    def test_each_fixture_line_is_flagged_by_exactly_its_own_signal(
        self, line: str, expected: str
    ) -> None:
        """🔴 LITERAL expectations, never `hd.DURABLE_DATED`. Reading the
        expected value out of the module under test makes the assertion true by
        construction — a mutant that renamed the reason would pass. These
        strings are printed to a human, so pinning them literally is also what
        keeps the output stable."""
        assert hd.durable_reason(line) == expected

    def test_the_reason_constants_are_the_strings_that_get_printed(self) -> None:
        """The other half: the module's constants must BE those literals, so a
        rename is a visible test change rather than a silent output change."""
        assert hd.DURABLE_DATED == "dated claim"
        assert hd.DURABLE_EVIDENCE == "evidence verb"

    def test_the_fixture_lines_do_not_overlap_across_BRANCHES(self) -> None:
        """🔴 THE ISOLATION PIN. If the dated line ever also carries an evidence
        verb, breaking the date regex still leaves it flagged and the mutant is
        scored SURVIVED while the guard is dead. Assert the orthogonality
        directly rather than trusting the wording to stay put."""
        verb_lines = (DURABLE_VERB_LINE, DURABLE_CLOSED_LINE)
        assert hd._BARE_ISO_DATE.search(DURABLE_DATED_LINE)
        assert not hd._EVIDENCE_VERB.search(DURABLE_DATED_LINE)
        assert not hd._EVIDENCE_VERB.search(DURABLE_OPEN_LINE)
        assert not hd._BARE_ISO_DATE.search(DURABLE_OPEN_LINE)
        for line in verb_lines:
            assert hd._EVIDENCE_VERB.search(line), line
            assert not hd._BARE_ISO_DATE.search(line), line

    def test_the_two_verb_fixtures_do_not_overlap_on_the_WORD(self) -> None:
        """🔴 ONE LINE PER WORD, not merely one per branch — and this is the pin
        that makes `verb-RETRACTED-dropped` and `verb-CLOSED-dropped` isolate.
        If one fixture matched both words, deleting either from the list would
        leave it flagged by the other and the mutant would be scored SURVIVED
        with that word genuinely dead."""
        matched = {
            "RETRACTED": DURABLE_VERB_LINE,
            "CLOSED": DURABLE_CLOSED_LINE,
        }
        for word, own in matched.items():
            for other_word, other in matched.items():
                found = hd._EVIDENCE_VERB.search(other)
                assert found is not None, other
                if word == other_word:
                    assert found.group(1) == word, (word, found.group(1))
                else:
                    assert found.group(1) != word, (
                        f"{other!r} also matches {word!r} — the two verb "
                        f"fixtures overlap and neither word can be isolated"
                    )

    def test_a_hyphen_compounded_verb_is_a_MODIFIER_not_a_declaration(self) -> None:
        """Measured: `the loop-CLOSED reframing of the #1 soak item` is an
        inventory line about a doc edit. Same reasoning as the date's leading
        boundary — a shouted word welded to its neighbour modifies it."""
        assert hd.durable_reason(
            "- The loop-CLOSED reframing of the soak item landed in the header."
        ) is None
        # NEGATIVE CONTROL: the same word, free-standing, is still a claim.
        assert hd.durable_reason(
            "- The soak item is CLOSED and the header says so."
        ) == "evidence verb"

    def test_VERIFIED_is_NOT_in_the_vocabulary(self) -> None:
        """🔴 REJECTED ON MEASUREMENT, and this is the line that decided it.
        `Deploy/verify status:` is a field the handoff skill's own step-2
        TEMPLATE prescribes, so on any session that deployed successfully a
        `VERIFIED` net fires on the template's own status line — the definition
        of the churn rule (f) must stay silent on. Pinned so a later widening
        has to delete an assertion that says why."""
        assert hd.durable_reason(
            "- **Deploy/verify status: DEPLOYED AND VERIFIED.**"
        ) is None
        assert hd.durable_reason("- Both VERIFIED + switched, no skips.") is None
        assert hd.durable_reason("- CONFIRMED on the second run.") is None

    def test_the_evidence_verb_is_case_sensitive(self) -> None:
        """All-caps is the precision half — measured, the case-insensitive form
        matches 10x as many corpus lines. A lowercase sentence must stay quiet."""
        assert hd.durable_reason("- we retracted that and decided to move on") is None

    def test_the_predicate_is_quiet_on_ordinary_status_lines(self) -> None:
        for line in (
            "- Branch / PR: `feat/coverage-sweep` / #412",
            "- Deploy/verify status: deployed, verified against the real path",
            "1. Re-derive the ledger from the build manifest.",
            "`python3 tools/queue_probe.py --for 240`",
        ):
            assert hd.durable_reason(line) is None, line


class TestTheOpennessPredicateIsSHAREDNotReimplemented:
    """🔴 ONE RULE, ONE PLACE. `subsystem_resolver` owns the `OPEN:` /
    `RESOLVED <sha>:` grammar and its near-miss detector, each backed by a
    committed evaluation matrix. A second copy here would regenerate that
    module's bugs at a second site and disagree with `subsystem_touch
    --validate` about the same line."""

    def test_it_is_the_SAME_function_object(self) -> None:
        import subsystem_resolver  # the module handoff_doc's import registered

        assert hd.parse_journal_bullets is subsystem_resolver.parse_journal_bullets

    def test_the_two_call_sites_agree_over_the_COMMITTED_matrix(self) -> None:
        """🔴 THE SEAM PIN, and it is two-way. Over every shape in
        `fixtures/near_miss_shapes.json` — the matrix `test_subsystem_touch.py`
        reads — a population the resolver calls anything but `none` must come
        back from `durable_reason` spelled `openness/<that population>`, and a
        population it calls `none` must NEVER come back as an openness reason.
        A divergence in either direction fails here."""
        import json

        import subsystem_resolver

        fixture = json.loads(
            (REPO_ROOT / "scripts" / "tests" / "fixtures" / "near_miss_shapes.json")
            .read_text(encoding="utf-8")
        )
        lines = [
            ln
            for key in ("attempts", "prose", "accepted_false_positives", "real")
            for ln in fixture[key]
        ]
        assert len(lines) >= 40, f"the matrix shrank to {len(lines)} shapes"
        seen: set[str] = set()
        for line in lines:
            bullets = subsystem_resolver.parse_journal_bullets(line)
            population = bullets[0].openness_population if bullets else "none"
            seen.add(population)
            reason = hd.durable_reason(line)
            if population == "none":
                assert not (reason or "").startswith("openness/"), (
                    f"handoff_doc invented an openness verdict the resolver does "
                    f"not make, for {line!r}: {reason!r}"
                )
            else:
                assert reason == f"openness/{population}", (
                    f"the two call sites disagree about {line!r}: resolver says "
                    f"{population!r}, handoff_doc says {reason!r}"
                )
        assert {"none"} < seen, (
            f"POSITIVE CONTROL: the matrix produced only {seen} — an agreement "
            f"test over one population cannot see a disagreement"
        )

    def test_the_module_spells_no_openness_grammar_of_its_own(self) -> None:
        """The structural half. `test_it_is_the_SAME_function_object` proves the
        import is wired today; this is what fails if someone later adds a
        second, local regex beside it and the import quietly stops mattering."""
        src = TOOL.read_text(encoding="utf-8")
        code = "\n".join(
            ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
        )
        code = code[code.index("EXIT_OK = 0"):]  # past the module docstring
        for token in ("OPEN|RESOLVED", "RESOLVED|OPEN", r"\bOPEN\b", "OPENNESS_"):
            assert token not in code, (
                f"scripts/lib/handoff_doc.py appears to spell the openness "
                f"grammar itself ({token!r}). It belongs to subsystem_resolver — "
                f"import it, do not copy it."
            )


class TestEveryRunStatesItsBuckets:
    """Rule (g). The replace/append rule lived only in a module docstring and in
    step 5 of the skill, neither of which is in front of an author choosing a
    heading."""

    def test_the_line_names_both_buckets(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """🔴 THE WHOLE LINE, not `in`. MEASURED: the first draft asserted
        `"State now → REPLACE" in line`, and a mutant renaming the label to
        `REPLACED` SURVIVED the battery — the mutated line still contains the
        substring. A guard on prose is walked by a longer word unless it pins
        the normalised string."""
        upd = write_update(
            tmp_path,
            DURABLE_UPDATE_DOC + "\n## Findings\n### a second glob\n- 311 in, 311 out.\n",
        )
        out = run_tool(durable_repo, update=upd).stdout
        line = next(ln for ln in out.splitlines() if ln.startswith("buckets: "))
        assert line == "buckets: State now → REPLACE · Findings → APPEND", line

    def test_a_section_only_the_update_has_is_reported_as_NEW(
        self, repo: Path, tmp_path: Path
    ) -> None:
        upd = write_update(tmp_path, "## Rollback\n`git revert <sha>`\n", "new.md")
        out = run_tool(repo, update=upd).stdout
        line = next(ln for ln in out.splitlines() if ln.startswith("buckets: "))
        assert line == "buckets: Rollback → NEW", line

    def test_the_bucket_labels_are_the_words_that_get_printed(self) -> None:
        """The constants themselves, for the same reason the reason-strings are
        pinned: they are read by a human and a rename is an output change."""
        assert (hd.BUCKET_REPLACE, hd.BUCKET_APPEND, hd.BUCKET_NEW) == (
            "REPLACE",
            "APPEND",
            "NEW",
        )

    def test_it_is_printed_before_the_diff_and_carries_no_status_token(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        """`status=` is the machine-readable verdict and the skill's contract
        pins one per run — a second one would be read as a second verdict."""
        upd = write_update(tmp_path, DURABLE_UPDATE_DOC)
        out = run_tool(durable_repo, update=upd).stdout
        assert out.index("buckets: ") < out.index("--- a/claudedocs/")
        buckets = next(ln for ln in out.splitlines() if ln.startswith("buckets: "))
        assert "status=" not in buckets
        assert "status=" not in warning_block(out)


class TestRuleFDidNotMoveTheExitCodes:
    """🔴 Rule (f) adds no exit code and must not reach one. `TestTheOtherExITSDidNotMove`
    pins the four failure paths byte-for-byte on the SUITE's fixture; these
    re-assert 4 and 5 with durable content actually present, which is the state
    that would trip a guard written as a refusal."""

    def test_no_advance_is_still_4_and_prints_nothing_at_all(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        upd = write_update(tmp_path, DURABLE_UPDATE_DOC)
        before = tree_hash(durable_repo)
        res = run_tool(durable_repo, "--confirm", update=upd, advanced=None)
        assert res.returncode == hd.EXIT_NO_ADVANCE
        both = res.stdout + res.stderr
        assert WARNING_HEAD not in both, "rule (f) leaked past rule (d)'s refusal"
        assert "buckets:" not in both
        assert diff_body(both) == []
        assert tree_hash(durable_repo) == before

    def test_no_change_is_still_5_with_durable_content_in_the_doc(
        self, durable_repo: Path, tmp_path: Path
    ) -> None:
        upd = write_update(
            tmp_path,
            "## State now\n"
            "- Branch / PR: `feat/coverage-sweep` / none\n"
            f"{DURABLE_DATED_LINE}\n"
            f"{DURABLE_VERB_LINE}\n"
            f"{DURABLE_CLOSED_LINE}\n"
            f"{DURABLE_OPEN_LINE}\n"
            "- Deploy/verify status: NOT deployed\n",
            "noop.md",
        )
        before = tree_hash(durable_repo)
        res = run_tool(durable_repo, "--confirm", update=upd)
        assert res.returncode == hd.EXIT_NO_CHANGE, res.stdout + res.stderr
        assert "status=no-change" in res.stderr
        assert WARNING_HEAD not in res.stdout + res.stderr
        assert tree_hash(durable_repo) == before

    def test_no_two_exit_constants_share_a_value(self) -> None:
        """🔴 THE COLLISION THIS SUITE COULD NOT SEE. Every other guard here
        pins a NAMED constant to a number, so two names sharing one value pass
        them all: the enumeration below stops at 6, and nothing asks whether the
        set is injective.

        MEASURED on this PR. `main` landed `EXIT_DOC_PER_EFFORT = 7` while this
        branch carried `EXIT_STALE_BASE = 7`. The merge conflicted only where
        the two blocks sat ADJACENT — resolving it the obvious way, by keeping
        both sides, yields two constants equal to 7 and an exit-code contract
        where `status=stale-base` and `status=doc-per-effort` are
        indistinguishable to any caller branching on the number. The doc/code
        guard did not catch it either: it scrapes `status=` tokens, and both
        tokens are present and documented.

        A green suite on the merged tree would have said nothing. This is the
        guard that makes the next PR to claim a code collide LOUDLY.
        """
        codes = {n: v for n, v in vars(hd).items()
                 if n.startswith("EXIT_") and isinstance(v, int)}
        assert len(codes) >= 8, f"the scraper found too few constants: {codes}"
        seen: dict[int, str] = {}
        for name, value in sorted(codes.items()):
            assert value not in seen, (
                f"{name} and {seen[value]} are both {value}. Two exit constants "
                f"sharing a value make their statuses indistinguishable to any "
                f"caller that branches on the number. Give the newer one the "
                f"next free code and update the skill's exit-code list with it."
            )
            seen[value] = name

    def test_the_exit_code_constants_did_not_move(self) -> None:
        """Their VALUES, not just their names — a caller reads the number."""
        assert (hd.EXIT_OK, hd.EXIT_USAGE, hd.EXIT_FAIL) == (0, 2, 3)
        assert (hd.EXIT_NO_ADVANCE, hd.EXIT_NO_CHANGE, hd.EXIT_BEHIND) == (4, 5, 6)

    def test_merge_still_returns_a_plain_string(self) -> None:
        """`merge()` is public and called directly by other tests in this file;
        rule (f) moved its body into `merge_report` and it must stay a string."""
        out = hd.merge(BASE_DOC, UPDATE_DOC)
        assert isinstance(out, str)
        assert out == hd.merge_report(BASE_DOC, UPDATE_DOC).text


# --------------------------------------------------------------------------
# the skill and the module must not drift apart
# --------------------------------------------------------------------------

SKILL_PINS: list[tuple[str, str]] = [
    (
        "scripts/lib/handoff_doc.py",
        "the step actually invokes the tool that owns the gate",
    ),
    (
        "--advanced",
        "rule (d) reaches the executor: the advance question is asked",
    ),
    (
        "--confirm",
        "the accept half of the gate is spelled out for the executor",
    ),
    # 🔴 SUPERSEDES the pin on the literal question `"update the handoff doc and
    # push it? (y/N)"`. Operator decision 2026-08-23 retired that prompt on the
    # same evidence that retired the index write's on 2026-08-15 — it was always
    # answered `y`. Pinning the question would now REQUIRE the skill to carry a
    # prompt the tool no longer prints, which is a gate pinning its own drift.
    (
        "Land it — no question. SHOW the diff, then push",
        "🔴 the write rule is the one /handoff already uses for the index write",
    ),
    (
        "The two-run shape STAYS",
        "🔴 …and the diff-before-write half, which the prompt was never doing",
    ),
    (
        "they are the only reader now",
        "🔴 what carries the load instead: the refusals and the warnings",
    ),
    (
        "Do NOT forbid updating the handoff",
        "rule (a): the fix is a safe update, never a suppressed one",
    ),
    (
        "NOT PUSHED",
        "🔴 the executor is told that --confirm without --push LEAVES A COMMIT",
    ),
    (
        "Do NOT retry by re-running with `--push`",
        "🔴 …and that the obvious retry duplicates the findings instead",
    ),
    (
        "line(s) that look DURABLE",
        "rule (f): the executor is told what the drop warning IS when it fires",
    ),
    (
        "a WARNING, never a refusal",
        "🔴 rule (f): …and that it blocks nothing, so it is not clicked through",
    ),
    (
        "a silent run is NOT evidence that nothing durable was dropped",
        "🔴 rule (f) is a FLOOR — silence must not be read as a guarantee",
    ),
    (
        "buckets:",
        "rule (g): the executor is told the bucket line exists and to read it",
    ),
    # 🔴 THE NEW-DOC PATH. MEASURED 2026-08-23: the skill told step 2 to `Write`
    # a brand-new handoff doc directly, and step 5 -- the only step that commits
    # -- then returned `status=no-change` (exit 5) against it, whose documented
    # instruction is "report the line and stop". Following the skill literally,
    # a NEW handoff was never committed and ended the session untracked, which
    # `claude/RULES.md` calls unsaved work one routine `checkout` from silent
    # deletion. The module already handled the no-base case correctly (see
    # `base_text = ... if doc.exists() else ""` and the MergeReport branch under
    # it); the skill routed around it.
    (
        "is written by step 5 and by nothing else",
        "🔴 the doc has exactly ONE writer, whether or not it already exists",
    ),
    (
        "Never `Write` the doc yourself",
        "🔴 …stated as a prohibition at the step that used to do it",
    ),
    (
        "This step CREATES the doc as well as updating it",
        "🔴 …and the gate step claims the new-doc case, so it is not left orphaned",
    ),
    # 🔴 The skill scoped the block boundary to "the first unindented line after
    # a blank one". A col-0 tag after the item's OWN indented fence has a FENCE,
    # not a blank, immediately above it — so an author reading that sentence
    # concluded the opposite of what the walk does, and got `[no forcing: field]`
    # for a field they had written. Behaviour pinned by the `col-0` params of
    # `test_an_indented_fence_does_not_cost_the_tag_that_follows_it`; this pins
    # that the skill still states the scope.
    (
        "which an intervening FENCE does not reset",
        "rule (j): the block boundary is stated as WIDE as the walk is",
    ),
    # 🔴 The same over-claim on the other side of that sentence: the skill said
    # flatly that `forcing function:` / `forcing = gate` "are near-misses, NAMED
    # not absent". `_FORCING_ATTEMPT` is anchored on the CLOSED VOCABULARY, so
    # that holds only when the value IS a listed kind — `forcing function:
    # followup` gets `[no forcing: field]`. Behaviour pinned by
    # `test_a_near_miss_with_an_UNLISTED_kind_is_NOT_named_it_reads_ABSENT`.
    (
        "`forcing = gate` **with a listed kind**",
        "rule (j): the near-miss promise is scoped to the closed vocabulary",
    ),
]


class TestBehindRemoteWritesNothing:
    """🔴 The gap this closes, and it is not hypothetical.

    MEASURED 2026-08-15: `--confirm --push` committed the doc to `main` in a
    SHARED base clone, then the push was rejected non-fast-forward because two
    other sessions had pushed during the session. The commit STAYED. An
    un-pushed commit on `main` in a devrc checkout is exactly what `ship.sh`
    skips over — silently, because `merge --ff-only` refuses and the host is
    left "as found" — so that host stops receiving every future change while
    still looking healthy. It has bitten this repo twice.

    The tool already had the right property everywhere else: a failure writes
    nothing. Push was the one path that traded it for a commit the caller then
    had to know how to undo.
    """

    def _advance_remote(self, repo: Path, tmp_path: Path) -> None:
        return advance_remote(tmp_path)

    def test_behind_remote_writes_NOTHING(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 THE LOAD-BEARING CASE. Not 'the push fails cleanly' — nothing is
        written at all, so there is no commit to strand on a shared branch."""
        self._advance_remote(repo, tmp_path)
        # 🔴 NOT `tree_hash` here, and the reason matters: it hashes `.git` too,
        # and the pre-check's `git fetch` legitimately writes FETCH_HEAD and
        # remote refs. Using it would fail for a side effect that is the guard
        # working, so assert the three things "nothing written" actually means.
        doc_before, shas_before = doc_of(repo), commit_shas(repo)
        remote_before = _sh("git", "rev-parse", "refs/heads/main",
                            cwd=tmp_path / "origin.git")
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 6, (res.returncode, res.stdout, res.stderr)
        assert "status=behind" in res.stderr
        assert doc_of(repo) == doc_before, "the doc was written before refusing"
        assert commit_shas(repo) == shas_before, (
            "a commit was made and then stranded — the exact failure this closes"
        )
        assert _sh("git", "rev-parse", "refs/heads/main",
                   cwd=tmp_path / "origin.git") == remote_before

    def test_it_names_the_hazard_and_the_recovery(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """A refusal a caller cannot act on gets worked around. It must name the
        fast-forward, and the preserve-verify-reset path for a real divergence."""
        self._advance_remote(repo, tmp_path)
        err = run_tool(repo, "--confirm", "--push", update=update_file).stderr
        assert "merge --ff-only" in err
        assert "reset --keep" in err
        assert "ship.sh" in err, "the consequence is what makes this worth obeying"

    # --- the remedy DEPENDS on the tree, and a dirty tree gets a different one ---
    #
    # 🔴 MEASURED 2026-08-19. An agent hit this refusal in a shared clone that was
    # 90 commits behind with 38 uncommitted paths belonging to at least three
    # sessions. It correctly declined the `merge --ff-only` this message printed —
    # that repo's own rules forbid committing, adding, stashing, checking out or
    # switching in the primary clone precisely because the tree is shared. The
    # tool was recommending an operation the target repo bans, and the message's
    # `ship.sh` framing (devrc-specific) was repeated back as fact about a repo
    # `ship.sh` does not converge.

    def _dirty_the_tree(self, repo: Path) -> str:
        """Leave an uncommitted path that is NOT the handoff doc — i.e. the shape
        of someone else's in-progress work."""
        other = repo / "SOMEONE_ELSES_WIP.md"
        other.write_text("another session is mid-edit here\n", encoding="utf-8")
        return other.name

    def test_a_DIRTY_checkout_is_NOT_told_to_fast_forward(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 The load-bearing half. `merge --ff-only` into a tree holding another
        session's work either refuses or overwrites it."""
        self._advance_remote(repo, tmp_path)
        self._dirty_the_tree(repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 6, (res.returncode, res.stderr)
        err = res.stderr
        # 🔴 The property is "no PASTEABLE COMMAND", not "the string never
        # appears". The dirty branch NAMES `merge --ff-only` inside the sentence
        # explaining why not to run it, which is correct and must stay legal —
        # an earlier version of this assertion forbade the string outright and
        # would have banned explaining the hazard at all.
        assert f"git -C {repo} merge --ff-only" not in err, (
            "the tool handed over a runnable fast-forward for a tree that holds "
            "uncommitted work"
        )
        assert "merge --ff-only" in err, (
            "it should still NAME the operation it is warning against"
        )
        assert "THIS CHECKOUT IS DIRTY" in err
        assert "worktree add" in err, "it must name the remedy, not just refuse one"
        assert "HEAD:" in err, "the push must go HEAD->branch from the worktree"

    def test_the_dirty_message_NAMES_the_paths_it_is_protecting(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """A refusal that does not say WHAT it saw is one the caller second-guesses."""
        self._advance_remote(repo, tmp_path)
        name = self._dirty_the_tree(repo)
        err = run_tool(repo, "--confirm", "--push", update=update_file).stderr
        assert name in err, "the dirty path is the evidence for the whole branch"
        assert "uncommitted path(s)" in err

    def test_the_dirty_message_gates_worktree_REMOVAL_on_the_push(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """Removing a worktree after a FAILED push deletes the branch ref and
        orphans the commit — the recipe is incomplete without this."""
        self._advance_remote(repo, tmp_path)
        self._dirty_the_tree(repo)
        err = run_tool(repo, "--confirm", "--push", update=update_file).stderr
        assert "AFTER the push succeeds" in err
        assert "orphans the commit" in err

    def test_a_CLEAN_checkout_still_gets_the_fast_forward(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 The negative control. Without it, "never suggest ff-only" would pass
        every test above while making the clean case needlessly heavy — the
        fast-forward is correct there and is the cheaper remedy."""
        self._advance_remote(repo, tmp_path)
        err = run_tool(repo, "--confirm", "--push", update=update_file).stderr
        assert "merge --ff-only" in err
        assert "This checkout is CLEAN" in err
        assert "THIS CHECKOUT IS DIRTY" not in err

    def test_the_ship_sh_claim_is_SCOPED_not_asserted_of_every_repo(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """`ship.sh` converges devrc only. Stating it flatly taught a reader that a
        stranded commit in an unrelated repo blocks it — they repeated the claim."""
        self._advance_remote(repo, tmp_path)
        err = run_tool(repo, "--confirm", "--push", update=update_file).stderr
        assert "In a devrc checkout" in err, "the ship.sh consequence must be scoped"
        assert "elsewhere" in err, "and the other case must be stated, not implied"

    def test_it_does_not_fire_when_the_remote_has_not_moved(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 The negative control. A check that always refuses would pass the
        two tests above while making --push permanently unusable."""
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 0, (res.returncode, res.stderr)
        assert "status=pushed" in res.stdout
        assert "status=behind" not in res.stderr

    def test_it_does_not_fetch_or_refuse_WITHOUT_push(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """A behind remote is irrelevant to a local-only `--confirm`: there is no
        push to be rejected, so refusing would block honest offline work."""
        self._advance_remote(repo, tmp_path)
        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, (res.returncode, res.stderr)
        assert "status=written" in res.stdout

    def test_an_UNREACHABLE_remote_refuses_rather_than_assuming_pushable(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 A fetch that FAILS is not '0 behind'. Guessing pushable is exactly
        the confident-wrong-answer this guard exists to prevent, and it would
        strand the commit the same way."""
        doc_before, shas_before = doc_of(repo), commit_shas(repo)
        _sh("git", "remote", "set-url", "origin",
            str(repo.parent / "does-not-exist.git"), cwd=repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode != 0
        assert "refusing to commit something that may not be pushable" in res.stderr
        # 🔴 Pin the DIAGNOSTIC, not just the refusal. Two guards reach "refuse"
        # here — the non-zero exit and the empty-sha fallback — so disabling the
        # first left the suite green while the message degraded from "cannot read
        # origin/main: <git's actual error>" to "returned no sha". The refusal is
        # the safety property; naming WHY (network vs auth vs no such remote) is
        # what makes it actionable.
        assert "cannot read origin/main" in res.stderr, res.stderr
        # …and the local-only escape hatch, since a dead remote must not cost the
        # whole handoff.
        assert "re-run without" in res.stderr and "--push" in res.stderr
        assert doc_of(repo) == doc_before
        assert commit_shas(repo) == shas_before


class TestPushabilityCasesTheFetchVersionGotWRONG:
    """🔴 Each of these was measured wrong, or dangerously right, before the
    mechanism moved from `git fetch` + `FETCH_HEAD` to `git ls-remote`.

    `FETCH_HEAD` is shared mutable state: a concurrent fetch between the write
    and the read made the check return a confident 0 on a checkout that was
    genuinely behind — write, commit, push rejected, stranded commit. And
    `fetch <remote> <branch>` simply FAILS when the branch is not on the remote,
    which made a first push impossible.
    """

    def test_a_branch_NOT_YET_on_the_remote_is_pushable(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE REGRESSION. A first push cannot be rejected non-fast-forward,
        so it must not be refused. `git fetch origin <branch>` exits 128 here."""
        _sh("git", "checkout", "-q", "-b", "brand-new", cwd=repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert "status=pushed" in res.stdout
        assert "status=behind" not in res.stderr

    def test_AHEAD_only_is_pushable(self, repo: Path, update_file: Path) -> None:
        """Ahead is the normal case; refusing it would block every handoff."""
        (repo / "extra.md").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "extra.md", cwd=repo)
        _sh("git", "commit", "-q", "-m", "local work", cwd=repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 0, res.stderr
        assert "status=pushed" in res.stdout

    def test_AHEAD_and_BEHIND_refuses(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """Diverged. `behind`-only logic that asked 'is the remote strictly
        ahead' would call this pushable and strand the commit."""
        other = tmp_path / "other2"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        for k, v in (("user.name", "O"), ("user.email", "o@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        (other / "theirs.md").write_text("t\n", encoding="utf-8")
        _sh("git", "add", "--", "theirs.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "theirs", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        (repo / "mine.md").write_text("m\n", encoding="utf-8")
        _sh("git", "add", "--", "mine.md", cwd=repo)
        _sh("git", "commit", "-q", "-m", "mine", cwd=repo)
        shas = commit_shas(repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 6, (res.returncode, res.stderr)
        assert commit_shas(repo) == shas

    def test_AHEAD_and_BEHIND_refuses_even_when_the_tip_IS_known_locally(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 The sibling test above does NOT reach the ancestry comparison.

        Its repo has never fetched the other clone's commit, so the lookup
        refuses on the unknown tip and the `merge-base` result is never
        consulted — measured: replacing that comparison with a flat `False` left
        the whole suite green. Fetching the object first (without merging it) is
        what forces the ancestry path to be the thing deciding.
        """
        other = tmp_path / "other4"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        for k, v in (("user.name", "O"), ("user.email", "o@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        (other / "theirs2.md").write_text("t\n", encoding="utf-8")
        _sh("git", "add", "--", "theirs2.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "theirs2", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        # The object is now LOCAL, but not merged: ahead 1, behind 1.
        _sh("git", "fetch", "-q", "origin", "main", cwd=repo)
        (repo / "mine2.md").write_text("m\n", encoding="utf-8")
        _sh("git", "add", "--", "mine2.md", cwd=repo)
        _sh("git", "commit", "-q", "-m", "mine2", cwd=repo)
        hd = _load_module()
        tip = _sh("git", "rev-parse", "refs/heads/main", cwd=tmp_path / "origin.git").strip()
        assert hd.git_allow(repo, "cat-file", "-e", f"{tip}^{{commit}}").code == 0, (
            "premise: the remote tip must be present locally, or this test takes "
            "the unknown-tip path and proves nothing about ancestry"
        )
        shas = commit_shas(repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 6, (res.returncode, res.stderr)
        assert commit_shas(repo) == shas

    def test_the_lookup_writes_NOTHING_into_dot_git(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 The claim the previous version got wrong. `fetch` writes
        `refs/remotes/<remote>/<branch>` in the COMMON gitdir — shared by every
        worktree — plus objects and reflogs, and two concurrent fetches failed to
        lock in 30/30 trials. `ls-remote` must write nothing at all."""
        def snapshot() -> dict:
            return {
                str(p.relative_to(repo)): p.stat().st_mtime_ns
                for p in (repo / ".git").rglob("*") if p.is_file()
            }
        # Make the remote ahead so the pre-check refuses AFTER doing its lookup.
        other = tmp_path / "other3"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        for k, v in (("user.name", "O"), ("user.email", "o@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        (other / "z.md").write_text("z\n", encoding="utf-8")
        _sh("git", "add", "--", "z.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "z", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        before = snapshot()
        assert run_tool(repo, "--confirm", "--push", update=update_file).returncode == 6
        assert snapshot() == before, (
            "the pushability lookup wrote into .git — on a shared gitdir that is "
            "a side effect on every other worktree, and FETCH_HEAD in particular "
            "is what made the old check racy"
        )

    def test_an_unreadable_remote_tip_is_NOT_read_as_pushable(
        self, repo: Path, update_file: Path
    ) -> None:
        """A remote tip this repo has never fetched is by definition a commit
        HEAD lacks. `merge-base` would FAIL on the unknown object, and a failure
        must not collapse to 'pushable'."""
        hd = _load_module()
        assert hd.remote_has_commits_we_lack(repo, "origin", "main") is False
        # An unknown sha must land on REFUSE via the ancestry check's non-zero,
        # which is the single fail-safe now that the redundant guard is gone.
        ghost = "0" * 40
        assert hd.git_allow(repo, "merge-base", "--is-ancestor", ghost, "HEAD").code != 0


class TestResolveBranch:
    """🔴 Extracted by this PR and shipped with ZERO coverage — a mutant
    returning the constant "main", ignoring `--branch` and never raising on a
    detached HEAD, survived all 55 tests."""

    def test_it_honours_the_override(self, repo: Path) -> None:
        hd = _load_module()
        assert hd.resolve_branch(repo, "release-42") == "release-42"

    def test_it_reads_the_current_branch(self, repo: Path) -> None:
        hd = _load_module()
        _sh("git", "checkout", "-q", "-b", "topic-x", cwd=repo)
        assert hd.resolve_branch(repo, None) == "topic-x"

    def test_a_detached_HEAD_refuses_rather_than_guessing(self, repo: Path) -> None:
        hd = _load_module()
        _sh("git", "checkout", "-q", "--detach", cwd=repo)
        with pytest.raises(hd.GitError) as exc:
            hd.resolve_branch(repo, None)
        assert "detached HEAD" in str(exc.value)


class TestPushFailureHandsOverTheRecovery:
    """The RESIDUAL path: the pre-check passed and the push still failed.

    🔴 It cannot be designed away — the remote can move in the window between
    the fetch and the push — so the commit really does exist at that point. What
    must not happen is the caller not being told: an un-pushed commit on a shared
    branch is the state `ship.sh` skips over silently, and a session that does
    not know it is there will not clean it up.

    Triggered deterministically by pointing `origin` at a NON-BARE repo with
    `main` checked out: fetch succeeds (so the pre-check passes), push is refused
    ("refusing to update checked out branch"). Discovered by accident when a
    fixture cloned the wrong path.
    """

    def _origin_that_fetches_but_refuses_push(self, repo: Path, tmp_path: Path) -> None:
        sibling = tmp_path / "sibling"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(sibling), cwd=tmp_path)
        _sh("git", "remote", "set-url", "origin", str(sibling), cwd=repo)

    def test_it_says_the_commit_exists_and_names_the_recovery(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        self._origin_that_fetches_but_refuses_push(repo, tmp_path)
        before = commit_shas(repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode != 0
        assert "status=push-failed" in res.stderr
        # The commit DOES exist — the message must not pretend otherwise.
        assert len(commit_shas(repo)) == len(before) + 1
        # 🔴 "in that order" is pinned too, not just the three commands. The
        # ORDER is the load-bearing part — verifying the sha reached the remote
        # BEFORE moving the branch pointer is what makes the recovery safe, and
        # deleting that clause left the suite green while the commands stayed.
        for needed in ("EXISTS LOCALLY", "ship.sh", "in that order",
                       "branch <topic>", "ls-remote", "reset --keep"):
            assert needed in res.stderr, f"recovery step missing: {needed}"

    def test_the_pre_check_did_not_fire_here(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 Distinguishes the two paths. Without this, a pre-check that refused
        everything would satisfy the test above for the wrong reason."""
        self._origin_that_fetches_but_refuses_push(repo, tmp_path)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert "status=behind" not in res.stderr
        assert "status=written" in res.stdout, "the write must have happened first"


class TestALocalCommitDoesNotGoUNANNOUNCED:
    """🔴 THE DEFECT, MEASURED. `--confirm` WITHOUT `--push` made a commit and
    said, in full: `status=written commit=<sha40>`. Not the branch it landed on,
    and not one word about the commit existing only in this checkout.

    That end state is IDENTICAL to the one `status=push-failed` spends nine
    alarmed lines on — "🔴 THE COMMIT … EXISTS LOCALLY … and is NOT on
    <remote>" plus a preserve→verify→`reset --keep` recovery — because it is the
    same state, reached by the ordinary SUCCESS path instead of a failure.
    `claude/RULES.md` calls docs written into a working tree UNSAVED WORK; this
    repo's `CLAUDE.md` records the un-pushed-commit incident twice.

    The corpus: 69 distinct shas came out of `status=written commit=`, from 58
    transcripts, of which only 19 ever printed `status=pushed`. Of the handoff
    commits still in this repo's object store, roughly a third are contained by
    NO remote branch — every one of them on a feature branch, none on `main`,
    which is why the feature-branch remedy is the DEFAULT below and the shared
    branch is the special case, not the other way round.

    🔴 EXIT CODE UNCHANGED. This is information, not a refusal: a local write is
    a legitimate thing to want, and turning it into a failure would push callers
    toward `--push` on a shared branch, which is worse.
    """

    def test_it_names_the_commit_the_BRANCH_and_that_it_is_not_pushed(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE HEADLINE REGRESSION. Red at base: the old output was exactly
        `status=written commit=<sha>` and stopped there."""
        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, res.stderr
        sha = commit_shas(repo)[0]
        assert f"status=written commit={sha} branch=main" in res.stdout, res.stdout
        assert (
            "NOT PUSHED — the commit exists only in this checkout; push it or "
            "open a PR in THIS session." in res.stdout
        ), res.stdout

    def test_the_PUSHED_path_says_nothing_about_not_being_pushed(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE NEGATIVE CONTROL, and the whole reason to bother with one: a
        warning printed on the path where it is false is wallpaper, and the next
        reader learns to skip the line on the path where it is true."""
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 0, res.stderr
        assert "status=pushed" in res.stdout
        assert "NOT PUSHED" not in res.stdout + res.stderr
        assert "branch <topic>" not in res.stdout
        assert "no-change" not in res.stdout

    def test_a_feature_branch_gets_the_PASTEABLE_push_command(
        self, repo: Path, update_file: Path
    ) -> None:
        """Pasteable beats descriptive, and `git push` is safe to paste in a way
        the `behind` path's `merge --ff-only` is not: a push that should not
        happen is REJECTED, never destructive, so this needs no dirty-tree check."""
        _sh("git", "checkout", "-q", "-b", "docs/handoff-sample", cwd=repo)
        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, res.stderr
        assert "branch=docs/handoff-sample" in res.stdout
        assert (
            f"    git -C {repo} push -u origin HEAD:refs/heads/docs/handoff-sample"
            in res.stdout
        ), res.stdout
        # 🔴 The retry note belongs on THIS arm too. A mutation that deleted it
        # from the feature-branch arm alone SURVIVED the whole suite, because
        # both retry tests happened to run on `main` — the shared arm — so the
        # note they read came from the other branch of the same function.
        assert "Do NOT retry by re-running this tool with --push" in res.stdout

    def test_a_SHARED_branch_is_NOT_handed_a_push_command_it_must_not_run(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 A WRONG pasteable command is worse than a descriptive one. devrc's
        own rules forbid committing to `main` in either host checkout, so
        printing `push … HEAD:refs/heads/main` would be the tool recommending an
        operation the target repo refuses — the shape that already shipped once
        in a `behind` message. The topic-branch route is what this repo's
        diverged-host recipe and `status=push-failed` both already name."""
        res = run_tool(repo, "--confirm", update=update_file)
        assert "push -u origin HEAD:refs/heads/main" not in res.stdout, res.stdout
        assert (
            f"    git -C {repo} branch <topic> HEAD && "
            f"git -C {repo} push -u origin <topic>" in res.stdout
        ), res.stdout

    def test_the_ship_sh_claim_is_SCOPED_here_too(
        self, repo: Path, update_file: Path
    ) -> None:
        """`ship.sh` converges devrc only. The `behind` message learned this the
        hard way — stating it flatly taught a reader that a stranded commit in an
        unrelated repo blocks it, and they repeated the claim — so the same
        scoping is required of the new message rather than re-derived."""
        out = run_tool(repo, "--confirm", update=update_file).stdout
        assert "In a devrc checkout" in out, "the ship.sh consequence must be scoped"
        assert "elsewhere" in out, "and the other case must be stated, not implied"

    def test_a_feature_branch_is_NOT_told_it_is_shared(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 NEGATIVE CONTROL on the classifier. A `branch_is_shared` that
        always returned True would satisfy every shared-branch assertion above
        while burying the ordinary case — measured as the COMMON case — under a
        `ship.sh` warning that is false for it."""
        _sh("git", "checkout", "-q", "-b", "docs/handoff-sample", cwd=repo)
        out = run_tool(repo, "--confirm", update=update_file).stdout
        assert "SHARED branch" not in out, out
        assert "ship.sh" not in out, out

    def test_the_STRUCTURAL_signal_catches_a_shared_branch_the_NAME_LIST_misses(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 The name list is a fallback, not the answer. A remote whose default
        branch is not called main/master/trunk is not hypothetical — this
        module's own history records a concurrent `git fetch origin stable` — and
        a name-only classifier calls that branch a feature branch and hands over
        a push command the repo may forbid."""
        _sh("git", "checkout", "-q", "-b", "stable", cwd=repo)
        _sh("git", "push", "-q", "origin", "stable", cwd=repo)
        _sh("git", "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/stable", cwd=repo)
        out = run_tool(repo, "--confirm", update=update_file).stdout
        assert "`stable` is a SHARED branch" in out, out
        assert "push -u origin HEAD:refs/heads/stable" not in out

    def test_the_retry_the_message_RULES_OUT_is_ruled_out_for_a_REPLACE_delta(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 THE CLAIM IN THE PROSE, MEASURED — point A of two.

        The message tells the caller not to retry with `--push`. That is the
        single most likely next action, so an unverified claim there would be
        worse than silence. Point A: a delta that only REPLACES sections leaves
        the doc equal to the merge result, so the no-change guard fires first.
        """
        replace_only = tmp_path / "replace-only.md"
        replace_only.write_text(
            "## State now\n- Branch / PR: `feat/sample` / #99\n", encoding="utf-8"
        )
        first = run_tool(repo, "--confirm", update=replace_only)
        assert first.returncode == 0
        assert "Do NOT retry by re-running this tool with --push" in first.stdout
        remote = Path(_sh("git", "remote", "get-url", "origin", cwd=repo).strip())
        before = _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo)
        again = run_tool(repo, "--confirm", "--push", update=replace_only)
        assert again.returncode == 5, (again.returncode, again.stdout, again.stderr)
        assert "status=no-change" in again.stderr
        assert _sh("git", "-C", str(remote), "log", "--format=%H", cwd=repo) == before

    def test_the_retry_is_ruled_out_for_an_APPEND_delta_TOO_and_differently(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 POINT B, and it is the one that FOUND THE BUG IN THIS MESSAGE.

        The note's first draft said the retry "will NOT land it: … it exits 5
        `no-change`". That was one measurement on a replace-only fixture stated
        as a general claim. A delta carrying an APPEND section — which the
        canonical `## Open investigations` block IS, and which every real handoff
        update carries — appends a SECOND copy under rule (c), so the retry
        exits 0, pushes, and silently duplicates the findings.

        Both halves are ruled out, for different reasons, and the message now
        says so. Exit code 0 is why this half is invisible without the test.
        """
        first = run_tool(repo, "--confirm", update=update_file)
        assert first.returncode == 0
        again = run_tool(repo, "--confirm", "--push", update=update_file)
        assert again.returncode == 0, (again.returncode, again.stderr)
        marker = "### the at-max reading was misread"
        assert doc_of(repo).count(marker) == 2, (
            "the retry appended the update a SECOND time — that is the hazard "
            "the note names, and if this ever becomes 1 the note is stale"
        )
        assert "APPENDS your findings a second time" in first.stdout

    def test_a_detached_HEAD_still_SUCCEEDS_and_names_no_bogus_target(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 `resolve_branch` is now called unconditionally, and it RAISES on a
        detached HEAD. That must not turn a working local write into a refusal:
        without `--push` there is no push to be wrong about, so the failure costs
        a NAME, never the write. And with no branch there is no push target, so
        no push command may be printed — a `HEAD:refs/heads/<unresolved>` would
        be exactly the wrong-pasteable-command failure."""
        shas_before = commit_shas(repo)
        _sh("git", "checkout", "-q", "--detach", cwd=repo)
        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, (res.returncode, res.stderr)
        assert len(commit_shas(repo)) == len(shas_before) + 1
        assert "branch=<unresolved>" in res.stdout, res.stdout
        assert "detached HEAD, no --branch" in res.stdout
        assert "refs/heads/" not in res.stdout, res.stdout
        assert f"git -C {repo} branch <topic> HEAD" in res.stdout

    def test_a_detached_HEAD_still_REFUSES_under_push(
        self, repo: Path, update_file: Path
    ) -> None:
        """The other half of the pair: deferring the resolve error must not have
        smuggled a detached-HEAD push past the refusal that used to catch it."""
        doc_before, shas_before = doc_of(repo), commit_shas(repo)
        _sh("git", "checkout", "-q", "--detach", cwd=repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 3, (res.returncode, res.stderr)
        assert "detached HEAD and no --branch given" in res.stderr
        assert doc_of(repo) == doc_before
        assert commit_shas(repo) == shas_before

    def test_branch_is_shared_predicate(self, repo: Path) -> None:
        """The predicate in one place, so the message and the tests agree — and
        so the UNION of the two signals is pinned rather than assumed. Neither
        may veto the other: a false True costs a line of prose, a false False
        costs the louder half of the warning exactly where it matters."""
        # No refs/remotes/origin/HEAD in this fixture (git init + remote add
        # never creates one) — so these exercise the NAME fallback alone.
        assert hd.branch_is_shared(repo, "origin", "main") is True
        assert hd.branch_is_shared(repo, "origin", "trunk") is True
        assert hd.branch_is_shared(repo, "origin", "master") is True
        assert hd.branch_is_shared(repo, "origin", "docs/handoff-x") is False
        assert hd.branch_is_shared(repo, "origin", "stable") is False
        # …and now the structural signal alone, on a name the list does not know.
        _sh("git", "checkout", "-q", "-b", "stable", cwd=repo)
        _sh("git", "push", "-q", "origin", "stable", cwd=repo)
        _sh("git", "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/stable", cwd=repo)
        assert hd.branch_is_shared(repo, "origin", "stable") is True
        assert hd.branch_is_shared(repo, "origin", "docs/handoff-x") is False
        # The name list still wins where they disagree — union, not override.
        assert hd.branch_is_shared(repo, "origin", "main") is True


# --------------------------------------------------------------------------
# 🔴 the property most likely to regress: every OTHER exit path, byte for byte
# --------------------------------------------------------------------------

# Full normalised `rc` + stdout + stderr for the exits that carry NO git-authored
# text. Pinned WHOLE, not by keyword: when the artifact under test is prose, a
# keyword guard is walkable by rewording, and these messages are the entire
# product of their code paths. A cosmetic reword must fail here — that is the
# cost of a machine-readable claim that they did not change.
PIN_NO_ADVANCE = """rc=4
--- stdout
--- stderr
status=no-advance
This session did not state what changed since the handoff was written, so no update is offered: no diff, no write, no commit.
  If state DID advance, re-run with --advanced '<what changed>'.
  If it did not, say so plainly and write nothing — a handoff that still describes reality is not stale.
"""

PIN_NO_CHANGE = """rc=5
--- stdout
--- stderr
status=no-change
The merge of <UPDATE> into claudedocs/handoff-sample-topic.md changes nothing. No diff, no commit — an empty commit is not a handoff update.
"""

PIN_BEHIND_CLEAN_STDERR = """status=behind remote=origin branch=main
NOTHING WRITTEN — not the doc, not a commit, not a ref.
  origin/main has commit(s) this checkout does not, so the push would be rejected and the commit would be left behind on a shared branch. In a devrc checkout that is the state that silently blocks `ship.sh`; elsewhere it is a stranded commit on a branch other people push to.
  This checkout is CLEAN, so a fast-forward is safe. Run it, then re-run this exact command:
    git -C <REPO> merge --ff-only origin/main
  🔴 If `--branch main` is not the branch you are ON, do NOT run that merge — it would merge an unrelated branch into your checkout. Push from a checkout of main instead.
  If the merge refuses, this checkout has DIVERGED — preserve, verify, then move the pointer, in that order:
    git -C <REPO> branch <topic> HEAD && git -C <REPO> push -u origin <topic>
    git -C <REPO> ls-remote --heads origin <topic>
    git -C <REPO> reset --keep origin/main
"""

PIN_BEHIND_DIRTY_STDERR = """status=behind remote=origin branch=main
NOTHING WRITTEN — not the doc, not a commit, not a ref.
  origin/main has commit(s) this checkout does not, so the push would be rejected and the commit would be left behind on a shared branch. In a devrc checkout that is the state that silently blocks `ship.sh`; elsewhere it is a stranded commit on a branch other people push to.
  🔴 THIS CHECKOUT IS DIRTY — 2 uncommitted path(s): README.md, other-wip.txt
  DO NOT fast-forward it. Some or all of that work is probably another session's, and `merge --ff-only` would either refuse or overwrite it. Several repos forbid committing in a shared primary clone for exactly this reason.
  Commit and push from a THROWAWAY WORKTREE off the remote branch instead, leaving this tree untouched:
    git -C <REPO> worktree add /tmp/handoff-wt origin/main
    # write the doc there, commit it path-limited, then:
    git -C /tmp/handoff-wt push origin HEAD:main
  🔴 Remove the worktree only AFTER the push succeeds — removing it after a failed push deletes the branch ref and orphans the commit.
  Verify by CONTENT, never ancestry: a squash merge never makes your head an ancestor of main.
"""

# push-failed interleaves git's OWN stderr, whose wording is a git-version
# dependency this suite must not pin. So its two tool-authored halves are pinned
# instead — the head token and everything from the 🔴 line to the end.
PIN_PUSH_FAILED_TAIL = """🔴 THE COMMIT <SHA12> EXISTS LOCALLY on `main` and is NOT on origin. On a shared branch that is the state `ship.sh` skips over silently.
  Preserve, verify, then move the pointer — in that order:
    git -C <REPO> branch <topic> HEAD && git -C <REPO> push -u origin <topic>
    git -C <REPO> ls-remote --heads origin <topic>   # confirm it landed
    git -C <REPO> reset --keep origin/main   # --keep refuses rather than destroys
"""


class TestTheOtherExITSDidNotMove:
    """🔴 Adding a line to ONE exit path is exactly how the neighbouring paths
    get edited by accident, and nothing else in this suite reads their messages
    whole — the existing tests check for keywords, which a reword walks straight
    past. These pin the bytes.

    Verified against the pre-change module by extracting it with `git show` and
    running all seven exit paths through the same normaliser: 7/7 identical. The
    normaliser's positive control is recorded in `normalised_run`.
    """

    def _noop_update(self, tmp_path: Path) -> Path:
        p = tmp_path / "noop.md"
        p.write_text("## Goal\nMake the sample subsystem stop dropping work "
                     "under load.\n", encoding="utf-8")
        return p

    def test_no_advance_is_byte_identical(
        self, repo: Path, update_file: Path
    ) -> None:
        res = run_tool(repo, "--confirm", update=update_file, advanced=None)
        assert normalised_run(res, repo, update_file) == PIN_NO_ADVANCE

    def test_no_change_is_byte_identical(self, repo: Path, tmp_path: Path) -> None:
        noop = self._noop_update(tmp_path)
        res = run_tool(repo, "--confirm", update=noop)
        assert normalised_run(res, repo, noop) == PIN_NO_CHANGE

    def test_behind_CLEAN_is_byte_identical(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        advance_remote(tmp_path)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 6
        assert normalised_run(res, repo, update_file).split("--- stderr\n")[1] == (
            PIN_BEHIND_CLEAN_STDERR
        )
        assert "status=" not in res.stdout, "nothing was written; no status on stdout"

    def test_behind_DIRTY_is_byte_identical(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        advance_remote(tmp_path)
        (repo / "README.md").write_text("someone else's WIP\n", encoding="utf-8")
        (repo / "other-wip.txt").write_text("wip\n", encoding="utf-8")
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 6
        assert normalised_run(res, repo, update_file).split("--- stderr\n")[1] == (
            PIN_BEHIND_DIRTY_STDERR
        )

    def test_push_failed_is_byte_identical(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        sibling = tmp_path / "sibling"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(sibling),
            cwd=tmp_path)
        _sh("git", "remote", "set-url", "origin", str(sibling), cwd=repo)
        res = run_tool(repo, "--confirm", "--push", update=update_file)
        assert res.returncode == 3
        norm = normalised_run(res, repo, update_file)
        err = norm.split("--- stderr\n")[1]
        assert err.startswith("status=push-failed\n")
        assert err[err.index("🔴 THE COMMIT"):] == PIN_PUSH_FAILED_TAIL
        # 🔴 And the stdout half: `status=written` on the push path must NOT have
        # grown the `branch=` token, or the not-pushed change reached a path it
        # has no business on.
        assert norm.rstrip("\n").endswith("status=written commit=<SHA40>") is False
        assert "status=written commit=<SHA40>\n--- stderr" in norm, norm

    def test_the_pins_can_report_a_difference(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 NEGATIVE CONTROL on the four pins above. A normaliser that
        tokenised too much — or an equality that compared something constant —
        would make every one of them vacuous. Feed it a run whose output is
        genuinely different and watch it NOT match."""
        res = run_tool(repo, "--confirm", update=update_file)
        text = normalised_run(res, repo, update_file)
        assert text != PIN_NO_ADVANCE
        assert text != PIN_NO_CHANGE
        assert "<SHA40>" in text, "the sha WAS tokenised — the instrument works"
        assert str(repo) not in text, "the repo path WAS tokenised"


class TestSkillAndModuleAgree:
    """The module cannot enforce a protocol its only caller stopped following,
    and the drift is silent — the step simply stops happening. Same shape as
    the step-4 pins in test_subsystem_touch.py."""

    @pytest.mark.parametrize("phrase,why", SKILL_PINS, ids=[w for _, w in SKILL_PINS])
    def test_skill_pins(self, phrase: str, why: str) -> None:
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert phrase in doc, (
            f"claude/skills/handoff/SKILL.md no longer pins {why}.\n"
            f"  missing: {phrase!r}\n"
            f"  Restore it or change scripts/lib/handoff_doc.py in the SAME commit."
        )

    def test_step_5_states_its_own_write_rule_not_a_borrowed_one(self) -> None:
        """🔴 STRUCTURAL, because a phrase pin is VACUOUS here.

        MEASURED, and the reason this test is positional: `"on decline,
        discard"` was already in the skill before step 5 existed — step 4 said
        it — so `phrase in doc` passed against the PRE-change file and proved
        nothing about the doc write. The same trap applies to the current
        wording: "no question" now appears at BOTH steps.

        So this asserts POSITION: step 5 carries its own write rule and its own
        statement of what replaced the prompt, both after step 5 begins.

        ⚠ The old version of this test anchored on the literal y/N question.
        That prompt was retired 2026-08-23, so anchoring there would now pin the
        skill to a prompt the tool does not print."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        step5 = doc.index("5. **Land the handoff doc")
        assert doc.index("Land it — no question. SHOW the diff, then push") > step5
        assert doc.rindex("they are the only reader now") > step5

    def test_the_pin_can_report_absence(self) -> None:
        """NEGATIVE CONTROL on the pin above: a check that can only pass is not
        a check. This phrase is not in the skill and must not become so."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert "push the handoff without asking anyone" not in doc

    def test_the_skill_does_not_reinstate_the_prompt(self) -> None:
        """🔴 THE OTHER HALF of `test_no_run_asks_a_yes_no_question`, which can
        only see the TOOL's output. The prompt lived in prose as much as in the
        module, and prose is what an agent actually executes — so a well-meaning
        edit could put the question back at step 5 while the tool stayed silent,
        and every module-level test would remain green.

        Scoped deliberately: the two surviving `y/N` mentions in the skill both
        describe the RETIREMENT in the past tense, so this asserts that no
        INSTRUCTION to ask survives, not that the string is absent."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        for banned in (
            "Ask exactly",
            "(y/N)`.",
            "a single y/N",
            "blocks on a y/N",
            "behind an explicit y/N",
            "Step 5 keeps its y/N",
        ):
            assert banned not in doc, (
                f"claude/skills/handoff/SKILL.md still instructs the executor to "
                f"prompt: {banned!r}. The y/N was retired 2026-08-23 by operator "
                f"decision at BOTH writes; if it is being reinstated, change "
                f"scripts/lib/handoff_doc.py in the SAME commit."
            )

    def test_every_exit_code_the_module_can_return_is_documented(self) -> None:
        """A status the tool returns and the skill never mentions leaves the
        agent improvising at the moment it is about to push to a shared branch."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        # 🔴 DERIVED FROM THE MODULE, not restated. The hand-written literal
        # ("no-advance", "no-change", "proposed") is why `behind` — added by the
        # very PR that closes the stranded-commit bug — reached the skill's only
        # audience undocumented: the test built to catch exactly that could not
        # see a status nobody remembered to add to its own list.
        src = TOOL.read_text(encoding="utf-8")
        emitted = sorted(set(re.findall(r'status=([a-z-]+)', src)))
        assert len(emitted) >= 5, f"the scraper found too few statuses: {emitted}"
        for status in emitted:
            assert status in doc, (
                f"the module can print `status={status}` and "
                f"claude/skills/handoff/SKILL.md never mentions it. An agent hits "
                f"an undocumented status at the moment it is about to push to a "
                f"shared branch. Document it, or stop emitting it."
            )

    def test_every_refusal_MARKER_the_module_prints_reaches_the_skill(self) -> None:
        """🔴 THE SEAM. SKILL.md's step-5 legend now enumerates the four rule-(j)
        row markers and tells the executor that **only one of them means "add a
        field"** — that legend is the whole reason the other three stopped
        getting the add-a-field remedy, i.e. it is load-bearing prose, not a
        summary. Every marker is pinned on the MODULE side by the tests above;
        nothing pinned the SKILL side.

        🔴 DERIVED FROM `hd.REFUSAL_MARKERS`, and deliberately NOT four
        `SKILL_PINS` entries, which is what this repo would ordinarily reach
        for. A pin asserts the literal is still IN the skill — so renaming
        `[fenced]` in the module goes red in the module's own tests, gets fixed
        there, and leaves the legend naming a marker the tool no longer prints,
        with the pin STILL GREEN because the skill does still contain the old
        token. A pin catches deletion from the skill; only derivation catches
        the rename, and the rename is the drift the auditor named.

        Same idiom, and the same reason, as
        `test_every_exit_code_the_module_can_return_is_documented` directly
        above: a hand-written list is exactly how `behind` reached the skill's
        only audience undocumented.

        Shown reachable by the `refusal-marker-renamed` row in
        `scripts/tests/mutants-handoff-cap.sh`.
        """
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert len(hd.REFUSAL_MARKERS) == 4, (
            "a fifth cause was added or one was dropped — the skill's legend "
            "enumerates them, so it needs the same edit in the same commit"
        )
        assert len(set(hd.REFUSAL_MARKERS)) == 4, "two markers collapsed onto one token"
        for marker in hd.REFUSAL_MARKERS:
            assert marker in doc, (
                f"scripts/lib/handoff_doc.py prints a refused row starting "
                f"{marker!r} and claude/skills/handoff/SKILL.md's step-5 legend "
                f"never mentions it. The executor's only map from a marker to "
                f"what to do about it would send them to the wrong remedy — and "
                f"three of the four mean something OTHER than 'add a field'. "
                f"Update the legend, or stop printing the marker."
            )

    def test_the_marker_pin_can_report_absence(self) -> None:
        """NEGATIVE CONTROL on the loop above — it iterates a module constant, so
        without this it is indistinguishable from a loop over an empty tuple that
        can only pass. A token shaped exactly like a marker, which the skill must
        not contain."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert "[no forcing: declaration]" not in doc

    # --- the seam the marker loop above CANNOT see: the legend's INSTRUCTION ---
    #
    # 🔴 THE BLIND SPOT, NAMED. `test_every_refusal_MARKER_the_module_prints
    # _reaches_the_skill` asserts the TOKEN `[fenced]` is present. It stayed
    # green for a whole round while the legend's instruction beside that token
    # — "move it out of the fence" — was the exact thing
    # `FENCED_FIELD_REMEDY`'s own comment says is harmful, because obeying it on
    # a fence that quotes this tool's vocabulary line promotes a quoted example
    # into a declaration and produces a FALSE `forcing: none`.
    #
    # 🔴 AND THE MODULE SIDE WAS UNPINNED TOO. MEASURED 2026-08-28 at
    # `e34ed6ef`: reverting `FENCED_FIELD_REMEDY` to a bare
    # "…where it does not count. Move it out of the fence onto one of the item's
    # own lines." left the WHOLE suite green — 237 passed — because
    # `test_a_field_inside_a_FENCE_still_does_not_count_and_says_why` asserts
    # only `"[fenced]"` and `"code fence"`, both of which the bare text keeps.
    # So the corrected remedy was documentation, not a guarantee.
    #
    # 🔴 WHOLE NORMALISED STRINGS, DELIBERATELY. `claude/RULES.md`: when the
    # artifact under test IS prose, a guard on WORDS is walkable by REWORDING,
    # so the whole string is pinned and a cosmetic reword costs a test edit.
    # That price is the point — it makes the claim machine-readable.
    _FENCED_REMEDY = (
        "🔴 The item(s) marked [fenced] carry the field INSIDE a code fence, "
        "where it does not count — a pasted sample is not a declaration. If "
        "that field is YOUR declaration, move it out of the fence onto one of "
        "the item's own lines, INDENTED — at column 0 it reads as absent. If "
        "it is quoted output, a copied example or this tool's own vocabulary "
        "line, the item is genuinely untagged and needs one of its own — do "
        "NOT promote the quote."
    )
    _FENCED_LEGEND = (
        "`[fenced]` **yours ⇒ unfence it; a QUOTE ⇒ tag the item, do NOT "
        "promote it** 📖 write-gate §C."
    )
    #: The one instruction BOTH sides must carry. Derived rather than restated:
    #: the skill assertion reads it off the module, so dropping it from either
    #: side goes red, which is the relationship the token loop cannot see.
    _NO_PROMOTE = "do NOT promote"

    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(s.split())

    def _fenced_legend_clause(self) -> str:
        """The step-5 legend's `[fenced]` clause, LOCATED not restated.

        Anchored on the module's own `MARK_FENCED`, so a rename that the token
        loop above forces into the skill lands here too rather than leaving this
        test looking for a marker nobody prints."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        legend = doc.index("Read each row's marker")
        start = doc.index(f"`{hd.MARK_FENCED}`", legend)
        end = doc.index("⚠", start)
        return self._norm(doc[start:end])

    def test_the_FENCED_remedy_is_not_a_bare_move_it_out(self) -> None:
        """🔴 THE MODULE HALF. Shown reachable by the
        `fenced-remedy-reverted-to-bare-move-it-out` row in
        `scripts/tests/mutants-handoff-cap.sh`, which reverts this constant to
        the bare text measured green above and is killed here.

        ⚠ NOT regression coverage — the remedy is already correct at
        `e34ed6ef`. It is the guarantee that was missing, labelled as one."""
        assert self._norm(hd.FENCED_FIELD_REMEDY) == self._norm(self._FENCED_REMEDY)
        assert self._NO_PROMOTE in hd.FENCED_FIELD_REMEDY

    def test_the_printed_refusal_carries_that_remedy_verbatim(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """A constant nobody prints is not a remedy. The BEHAVIOURAL half: the
        real refusal text an executor reads must contain it."""
        text = (
            "## Next steps (ranked)\n"
            "1. Land the fix.\n"
            "   ```\n"
            "   forcing: gate\n"
            "   ```\n"
        )
        res = run_tool(repo, update=write_delta(tmp_path, "fencedremedy.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert self._norm(hd.FENCED_FIELD_REMEDY) in self._norm(res.stderr)

    def test_the_skill_legend_does_not_tell_the_executor_to_promote_a_quote(
        self,
    ) -> None:
        """🔴 THE SKILL HALF, AND THE ONE THAT WAS ACTUALLY BROKEN. RED at
        `e34ed6ef`, where the clause reads ``[fenced]` move it out of the
        fence.` — the module's own comment calls that instruction harmful, and
        the module comment elsewhere calls this legend "the executor's only map
        from a marker to what to do about it".

        The `_NO_PROMOTE` assertion is DERIVED from the module constant, so it
        also fails if the module drops the instruction — that is the seam, and
        it is what the marker-token loop above structurally cannot see.

        Shown reachable by the `skill-legend-says-only-move-it-out` row in
        `scripts/tests/mutants-handoff-cap.sh`."""
        clause = self._fenced_legend_clause()
        assert self._NO_PROMOTE in hd.FENCED_FIELD_REMEDY, (
            "the module stopped telling the caller not to promote a quoted "
            "field; the skill legend below is derived from it, so fix the "
            "module first"
        )
        assert self._NO_PROMOTE in clause, (
            f"claude/skills/handoff/SKILL.md's step-5 legend maps {hd.MARK_FENCED} "
            f"to {clause!r}. FENCED_FIELD_REMEDY says the commonest fenced field "
            f"is a QUOTE of this tool's own vocabulary line, and that obeying a "
            f"bare 'move it out of the fence' promotes it into a declaration — a "
            f"false `forcing: none`. The legend must not instruct the harm the "
            f"module documents."
        )
        assert clause == self._norm(self._FENCED_LEGEND)

    def test_the_legend_clause_locator_can_report_absence(self) -> None:
        """NEGATIVE CONTROL on `_fenced_legend_clause`. A locator that silently
        returned the whole document, or an empty string, would make both
        assertions above unfalsifiable in one direction.

        Two halves, and the docstring used to describe only the first as if it
        covered both. The `_NO_PROMOTE` line is a control on the PREDICATE, not
        on the locator — it feeds the predicate the bare legend text this suite
        refuses and shows it goes false, so a predicate that could only ever be
        true would be caught. The `len` and `startswith` lines are the control
        on the LOCATOR's own return: bounded rather than the whole document,
        and anchored on the marker rather than empty."""
        clause = self._fenced_legend_clause()
        assert self._NO_PROMOTE not in "`[fenced]` move it out of the fence."
        assert 0 < len(clause) < 400, f"the locator returned {len(clause)} bytes"
        assert clause.startswith(f"`{hd.MARK_FENCED}`")

    # --- the OTHER prose claim round 4 corrected, and the one nothing pinned ---
    #
    # 🔴 THE EXISTING `SKILL_PINS` ENTRY CANNOT SEE THIS DRIFT. That entry is the
    # substring "`forcing = gate` **with a listed kind**" — which the round-3
    # wording ALSO contains, verbatim, because the correction moved the
    # parenthetical rather than the phrase the pin quotes. So the pin is green
    # against both the true and the false sentence and distinguishes nothing.
    #
    # 🔴 WHOLE NORMALISED STRING, for the same reason `_FENCED_REMEDY` is one.
    _NEAR_MISS_CLAUSE = (
        "`forcing function:`/`forcing = gate` **with a listed kind** "
        "(unlisted reads ABSENT), and a fenced field regardless of kind, are "
        "**near-misses, NAMED** not absent."
    )

    def _near_miss_clause(self) -> str:
        """Step 3's near-miss sentence, LOCATED not restated.

        Bounded by the 📖 reference marker that closes every clause in this
        block, so the locator cannot silently widen to the rest of the file."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        start = doc.index("`forcing function:`")
        return self._norm(doc[start : doc.index("📖", start)])

    def test_the_skill_binds_UNLISTED_reads_ABSENT_to_the_right_antecedent(
        self,
    ) -> None:
        """🔴 RED at `6a862d8c`, where the clause read "…**with a listed kind**,
        and a fenced field, are **near-misses, NAMED** not absent (an unlisted
        kind there reads ABSENT)". "there" sat against its nearest antecedent,
        "a fenced field" — for which it is FALSE.

        RE-MEASURED 2026-08-28 at `976b09b5`, both sides of the distinction:

            fenced `forcing: followup`   -> fenced=True  -> row `[fenced]`
            fenced `forcing: gate`       -> fenced=True  -> row `[fenced]`
            `forcing = followup`         -> row `[no forcing: field]`
            `forcing = gate`             -> row `[unparsed forcing field on: …]`

        i.e. `fenced` is `any(_FORCING.search(ln) …)` and never consults
        `FORCING_KINDS`, so an unlisted kind inside a fence is NAMED, not
        absent. Only the `forcing function:` / `forcing = …` spellings fall
        through to `[no forcing: field]`, because `_FORCING_ATTEMPT` is anchored
        on the closed vocabulary. Round 4 rebound the parenthetical to those.

        ⚠ NOT regression coverage — the sentence is already correct here. It is
        the guarantee it never got: the only `SKILL_PINS` entry over it is a
        substring of the FALSE wording too (see the comment above), so the
        correction was revertible with the whole suite green.

        Shown reachable by the `skill-near-miss-clause-rebound-to-the-fence`
        row in `scripts/tests/mutants-handoff-cap.sh`."""
        assert self._near_miss_clause() == self._norm(self._NEAR_MISS_CLAUSE), (
            "claude/skills/handoff/SKILL.md's step-3 near-miss sentence no "
            "longer reads as pinned. If '(unlisted reads ABSENT)' drifted back "
            "onto 'a fenced field', that is the MEASURED-FALSE binding — a "
            "fenced field with an unlisted kind is reported `[fenced]`, not "
            "absent, because `fenced` never consults FORCING_KINDS. Re-measure "
            "before rewording, and update _NEAR_MISS_CLAUSE in the SAME commit "
            "for a deliberate reword."
        )

    def test_the_near_miss_clause_locator_can_report_absence(self) -> None:
        """NEGATIVE CONTROL on `_near_miss_clause`, in both directions a locator
        can be vacuous. The PREDICATE half feeds it the round-3 sentence this
        suite refuses and shows the comparison goes false — an equality that
        could only ever hold would be caught here. The LOCATOR half shows the
        return is bounded rather than the whole document, and anchored where it
        claims to be rather than empty."""
        clause = self._near_miss_clause()
        assert self._norm(self._NEAR_MISS_CLAUSE) != self._norm(
            "`forcing function:`/`forcing = gate` **with a listed kind**, and a "
            "fenced field, are **near-misses, NAMED** not absent (an unlisted "
            "kind there reads ABSENT)."
        )
        assert 0 < len(clause) < 400, f"the locator returned {len(clause)} bytes"
        assert clause.startswith("`forcing function:`")

    def test_the_tool_is_tracked_by_git(self) -> None:
        """A new file the flake never sees deploys as an absence, silently.

        Asserted in BOTH environments and never skipped: in the nix sandbox the
        flake source contains only tracked files, so the module's PRESENCE is
        the evidence; on the dev host `git ls-files` is asked directly."""
        assert TOOL.exists(), f"{TOOL} is missing from this tree"
        if not (REPO_ROOT / ".git").exists():
            return
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", "scripts/lib/handoff_doc.py"],
            capture_output=True,
            text=True,
            env=dict(os.environ, **GIT_ENV),
        )
        assert out.stdout.strip() == "scripts/lib/handoff_doc.py", (
            "scripts/lib/handoff_doc.py is not tracked by git, so the flake will "
            "omit it from the deploy and `home-manager switch` will succeed with "
            "the file simply absent."
        )


class TestTheNewDocPathReachesTheGate:
    """🔴 A brand-new handoff doc must reach step 5, not be `Write`n in step 2.

    MEASURED 2026-08-23, in a throwaway repo, following the skill literally:
    step 2 wrote `claudedocs/handoff-t.md` in full, then step 5 -- the only step
    that commits -- was run with that content as its delta and returned

        status=no-change   (exit 5)

    whose documented instruction is "report the line and stop". Nothing was
    committed. The doc ended the session untracked, which `claude/RULES.md`
    names as unsaved work one routine `checkout` away from silent deletion. Two
    untracked handoff docs were sitting in the devrc working tree at the time,
    the older one two days old, against 52 tracked ones -- consistent with the
    update path landing and the new-doc path leaking.

    The module was never the problem: `main()` reads `base_text = "" if not
    doc.exists()` and the MergeReport branch under it makes the delta the doc
    verbatim. The skill routed around a path the tool already had.

    Both halves are asserted here, because either alone is walkable: the PROSE
    contract (step 2 no longer instructs a direct write) and the BEHAVIOUR the
    prose now promises (the tool creates, gates and commits a doc with no base).
    """

    def _skill(self) -> str:
        return HANDOFF_SKILL.read_text(encoding="utf-8")

    def test_step_2_no_longer_instructs_a_direct_write(self) -> None:
        """🔴 THE REGRESSION ASSERTION. Watched to fail against the pre-change
        file: `git show origin/main:claude/skills/handoff/SKILL.md` opens step 2
        with exactly this sentence."""
        doc = self._skill()
        assert "**Write the handoff doc** to `claudedocs/" not in doc, (
            "step 2 instructs the executor to write claudedocs/handoff-<topic>.md "
            "directly. That path never reaches a commit: step 5 is the only step "
            "that commits, and against an already-written doc it returns "
            "status=no-change (exit 5), whose instruction is to stop. The doc is "
            "then left untracked. Draft into a scratch file and let step 5 land "
            "it -- handoff_doc.py handles the no-base case."
        )

    def test_the_scratch_instruction_precedes_the_kickoff(self) -> None:
        """Structural, not a phrase: the drafting instruction is useless if it
        arrives after the step that consumes the doc's path."""
        doc = self._skill()
        draft = doc.find("**Draft the handoff doc into a SCRATCH FILE")
        assert draft >= 0, "step 2's drafting instruction is gone"
        kickoff = doc.find("**Output a kickoff block**")
        gate = doc.find("5. **Land the handoff doc")
        assert draft < kickoff < gate

    def test_both_directions_of_the_swap_are_asserted(self) -> None:
        """⚠ NOT a negative control, and it was mislabelled as one until an
        audit said so. It re-states the same two assertions against the same
        real file as the tests above; it never shows the negative assertion CAN
        go red, so it proves nothing they do not.

        It survives as a cheap statement of the pair -- the old sentence is gone
        AND the new one is present -- because a rewrite that satisfies only one
        half is the plausible regression. The evidence that these can report a
        difference is external and was run by hand: all three assertions in this
        class are RED against `git show origin/main:claude/skills/handoff/SKILL.md`.
        `claude/RULES.md`: "a guard's DESCRIPTION claims COVERAGE."
        """
        doc = self._skill()
        assert "**Write the handoff doc** to `claudedocs/" not in doc
        assert "**Draft the handoff doc into a SCRATCH FILE" in doc

    def test_the_module_creates_a_doc_that_does_not_exist(
        self, repo: Path, update_file: Path
    ) -> None:
        """The BEHAVIOUR the prose now promises: with no base the run offers the
        ordinary gate -- a diff, `status=proposed`, nothing written.

        ⚠ AN INVARIANT GUARD, NOT REGRESSION COVERAGE, and labelled as one. The
        module already behaved this way; the defect was that the skill never
        routed here, so this was green before the change and never caught the
        bug. It is here because the prose contract above now DEPENDS on this
        behaviour, and nothing else asserts it."""
        doc = repo / "claudedocs" / "handoff-sample-topic.md"
        doc.unlink()
        res = run_tool(repo, update=update_file)
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert "status=proposed" in res.stdout + res.stderr
        assert "+++ b/claudedocs/handoff-sample-topic.md" in res.stdout
        assert not doc.exists(), "the proposal wrote the doc — the gate is bypassed"

    def test_the_no_base_run_confirms_into_exactly_one_commit(
        self, repo: Path, update_file: Path
    ) -> None:
        """…and `--confirm` lands it, so the new-doc case ends the session
        COMMITTED rather than untracked -- the property whose absence is the
        whole finding.

        ⚠ Also an INVARIANT GUARD on the module (green before the change). What
        regressed was the ROUTE to it, asserted by the prose tests above."""
        doc = repo / "claudedocs" / "handoff-sample-topic.md"
        doc.unlink()
        before = commit_shas(repo)
        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert doc.exists(), "the doc was not created"
        after = commit_shas(repo)
        assert len(after) == len(before) + 1, "expected exactly one commit"
        tracked = subprocess.run(
            ["git", "-C", str(repo), "ls-files", "--",
             "claudedocs/handoff-sample-topic.md"],
            capture_output=True, text=True, env=dict(os.environ, **GIT_ENV),
        ).stdout.strip()
        assert tracked == "claudedocs/handoff-sample-topic.md", (
            "the new doc is not tracked after --confirm — it would end the "
            "session as untracked working-tree content, which is the failure "
            "this class exists for"
        )


class TestBlockedCommitLeavesNoTrace:
    """A REFUSED commit must not leave the doc written and staged.

    🔴 MEASURED 2026-08-21, and this is the failure the class exists for. A
    PreToolUse hook enforcing "never commit in the primary clone" refused the
    `git commit` — correct behaviour — but the tool had ALREADY written the
    merged doc and `git add`ed it. The refusal therefore left a modified, STAGED
    file in a checkout shared with other sessions, where the next person's
    `git commit` sweeps it in. The caller then re-ran the tool to read the error
    and the merge appended the same block a SECOND time.

    A blocked commit is not a no-op, and the caller has no reason to expect it
    left anything behind: `status=failed` reads as "nothing happened".
    """

    @staticmethod
    def _block_commits(repo: Path) -> None:
        """Refuse every commit, the way a real guard hook does."""
        # write_exec owns the shebang — a call site that supplies its own
        # trips scripts/tests/test_runtime_shebangs.py, a REPO-WIDE scan that a
        # three-file test run structurally cannot see.
        write_exec(repo / ".git" / "hooks" / "pre-commit",
                   "echo 'blocked by guard' >&2\nexit 1\n")

    def test_the_hook_actually_blocks(self, repo: Path) -> None:
        """POSITIVE CONTROL for the fixture itself.

        Without this, a hook that silently failed to install would make every
        assertion below pass for the wrong reason — the commit would simply
        succeed and there would be nothing to roll back."""
        self._block_commits(repo)
        (repo / "canary.txt").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "canary.txt", cwd=repo)
        out = subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "should be refused"],
            capture_output=True, text=True, env=dict(os.environ, **GIT_ENV),
        )
        assert out.returncode != 0, "the pre-commit hook did not block — fixture is inert"

    def test_doc_is_restored_and_nothing_is_staged(
        self, repo: Path, update_file: Path
    ) -> None:
        before = doc_of(repo)
        shas_before = commit_shas(repo)
        self._block_commits(repo)

        res = run_tool(repo, "--confirm", update=update_file)

        assert res.returncode == 3, f"expected EXIT_FAIL, got {res.returncode}"
        assert "status=failed" in res.stderr
        # 🔴 The two assertions this class exists for.
        assert doc_of(repo) == before, (
            "the doc was left MODIFIED after the commit was refused — in a shared "
            "checkout that is another session's `git commit` away from being swept in"
        )
        staged = _sh("git", "diff", "--cached", "--name-only", cwd=repo).split()
        assert staged == [], f"paths left STAGED after a refused commit: {staged}"
        assert commit_shas(repo) == shas_before, "a commit was made despite the refusal"

    def test_unrelated_staged_work_is_not_unstaged_by_the_rollback(
        self, repo: Path, update_file: Path
    ) -> None:
        """The rollback must be PATH-LIMITED, like the commit it undoes.

        A blanket `git reset` would unstage a co-worker's staged files as a side
        effect of our own failure — trading one shared-checkout defect for a
        worse one."""
        (repo / "OTHER.md").write_text("someone else's staged work\n", encoding="utf-8")
        _sh("git", "add", "--", "OTHER.md", cwd=repo)
        self._block_commits(repo)

        run_tool(repo, "--confirm", update=update_file)

        staged = _sh("git", "diff", "--cached", "--name-only", cwd=repo).split()
        assert staged == ["OTHER.md"], (
            f"the rollback must leave unrelated staged work alone; staged={staged}"
        )

    def test_a_LANDED_commit_is_never_rolled_back(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE HIGHEST-CONSEQUENCE BRANCH, and it had no test.

        The `committed` flag exists so a commit that LANDED and then hit a later
        failure is left alone. Roll back there and the tool DISCARDS a committed
        change — strictly worse than the defect it was written to fix.

        🔴 THE FIRST VERSION OF THIS TEST WAS VACUOUS and only mutation testing
        found it: it used a failing `post-commit` hook, and git IGNORES that
        hook's exit status (measured: `git commit` rc=0, commit created). The
        except-branch was never reached, so deleting the guard still passed.
        A `git` shim that fails `rev-parse` ONLY AFTER a commit has run is the
        real shape — the commit lands, then a later git step errors.
        """
        real_git = shutil.which("git")
        assert real_git, "git not on PATH"
        shim = repo / "gitshim-revparse"
        shim.mkdir()
        marker = shim / "committed.marker"
        # `git()` invokes ["git", "-C", <repo>, *args] — the SUBCOMMAND is $3,
        # not $1. Scan all args instead of indexing, which is what the earlier
        # broken shim got wrong.
        fired = shim / "intercepted.marker"
        write_exec(
            shim / "git",
            f'for a in "$@"; do [ "$a" = "commit" ] && : > "{marker}"; done\n'
            f'for a in "$@"; do\n'
            f'  if [ "$a" = "rev-parse" ] && [ -f "{marker}" ]; then\n'
            f'    : > "{fired}"; exit 7\n'
            f'  fi\n'
            f'done\n'
            f'exec {real_git} "$@"\n',
        )
        shas_before = commit_shas(repo)
        env = dict(os.environ, **GIT_ENV)
        env["PATH"] = f"{shim}:{env['PATH']}"

        res = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(repo), "--topic",
             "sample-topic", "--update", str(update_file), "--advanced",
             "a landed commit must survive a later failure", "--confirm"],
            capture_output=True, text=True, env=env,
        )

        # 🔴 POSITIVE CONTROL. Without these two the test passes with an INERT
        # shim — measured: reverting the shim to its known-broken form left this
        # green. `doc_of == head_doc` is ALSO true on the plain success path, so
        # it cannot on its own prove the failure branch ran.
        assert fired.exists(), (
            "the git shim never intercepted rev-parse — this test proved nothing"
        )
        assert res.returncode == 3, (
            f"expected EXIT_FAIL (the later step failed), got {res.returncode}"
        )
        assert commit_shas(repo) != shas_before, (
            "fixture inert: no commit was made, so the guard was never exercised"
        )
        head_doc = _sh("git", "show", "HEAD:claudedocs/handoff-sample-topic.md",
                       cwd=repo)
        assert doc_of(repo) == head_doc, (
            "the worktree was rolled back even though the commit LANDED — that "
            "discards committed work"
        )

    def test_first_ever_handoff_leaves_no_untracked_file_behind(
        self, repo: Path, update_file: Path
    ) -> None:
        """The `original is None` -> `unlink` branch, which had no fixture.

        Every other fixture pre-creates the doc, so the first-ever-handoff path
        was never exercised: dropping the `unlink` survived the suite. A doc left
        behind here is an UNTRACKED file in a shared checkout — invisible to
        `git status -s` habits that scan for ` M`, and it makes a re-run append
        to a doc the caller believes was never written."""
        doc = repo / "claudedocs" / "handoff-brand-new-topic.md"
        assert not doc.exists()
        self._block_commits(repo)

        # `--new-effort` is rule (i-b)'s assertion, added 2026-08-28: this repo
        # already carries `handoff-sample-topic.md`, so creating a SECOND doc is
        # now refused (exit 7) unless the caller says it is a new effort. Here it
        # genuinely is — this test is about the ROLLBACK path, and without the
        # flag the run would never reach the commit it needs to see refused.
        res = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(repo), "--topic",
             "brand-new-topic", "--update", str(update_file), "--advanced",
             "a first-ever handoff for this topic", "--new-effort", "--confirm"],
            capture_output=True, text=True, env=dict(os.environ, **GIT_ENV),
        )

        assert res.returncode == 3, f"expected EXIT_FAIL, got {res.returncode}"
        assert not doc.exists(), (
            "the doc the run CREATED was left behind after the commit was refused"
        )
        untracked = _sh("git", "status", "--porcelain", "--untracked-files=all",
                        cwd=repo).strip()
        assert untracked == "", f"left untracked residue: {untracked!r}"

    def test_the_reset_FALLBACK_is_also_path_limited(
        self, repo: Path, update_file: Path
    ) -> None:
        """The fallback arm had no test — only the primary `restore` arm did.

        `git reset` without `-- <path>` unstages EVERYTHING, so a co-worker's
        staged file would be unstaged by our failure. Forcing the fallback by
        making `git restore` unavailable proves the second arm carries the same
        path limit as the first."""
        # 🔴 Resolve the real git ABSOLUTELY. `exec /usr/bin/env git` searches
        # PATH — which this shim is prepended to — so the shim re-execs itself
        # forever. (Measured: the run had to be killed.)
        real_git = shutil.which("git")
        assert real_git, "git not on PATH"
        shim = repo / "gitshim"
        shim.mkdir()
        # 🔴 `git()` invokes ["git", "-C", <repo>, *args], so the subcommand is
        # $3 — an earlier version tested $1, never intercepted anything, and the
        # fallback arm was never exercised (the mutant survived). Scan all args.
        fired = shim / "intercepted.marker"
        write_exec(shim / "git",
                   f'for a in "$@"; do\n'
                   f'  if [ "$a" = "restore" ]; then : > "{fired}"; exit 129; fi\n'
                   f'done\n'
                   f'exec {real_git} "$@"\n')
        (repo / "OTHER.md").write_text("someone else's staged work\n", encoding="utf-8")
        _sh("git", "add", "--", "OTHER.md", cwd=repo)
        self._block_commits(repo)

        env = dict(os.environ, **GIT_ENV)
        env["PATH"] = f"{shim}:{env['PATH']}"
        res = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(repo), "--topic",
             "sample-topic", "--update", str(update_file), "--advanced",
             "forcing the reset fallback", "--confirm"],
            capture_output=True, text=True, env=env,
        )

        # 🔴 POSITIVE CONTROL — see the sibling above. An inert shim means the
        # PRIMARY `restore` arm succeeded and the fallback never ran, so the
        # path-limit this test exists to prove was never exercised. Measured:
        # with a broken shim AND a blanket-reset mutant, this stayed green.
        assert fired.exists(), (
            "the git shim never intercepted `restore` — the fallback arm did not "
            "run, so this test proved nothing"
        )
        assert res.returncode == 3, (
            f"expected EXIT_FAIL, got {res.returncode}"
        )

        staged = _sh("git", "diff", "--cached", "--name-only", cwd=repo).split()
        assert staged == ["OTHER.md"], (
            f"the reset fallback must be path-limited too; staged={staged}"
        )

    def test_incomplete_rollback_advises_ONLY_the_half_that_failed(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 A message that contradicts what happened is worse than none.

        The index and worktree halves fail INDEPENDENTLY. Printing advice for
        both was measured to tell an operator their content "was never committed
        … restore by hand" while the bytes HAD been restored and the content WAS
        in HEAD — advice that makes someone hand-rewrite a doc they still have.
        Here the index half fails and the worktree half succeeds."""
        real_git = shutil.which("git")
        assert real_git, "git not on PATH"
        shim = repo / "gitshim-index"
        shim.mkdir()
        fired = shim / "intercepted.marker"
        write_exec(shim / "git",
                   f'for a in "$@"; do\n'
                   f'  case "$a" in restore|reset) : > "{fired}"; exit 129 ;; esac\n'
                   f'done\n'
                   f'exec {real_git} "$@"\n')
        before = doc_of(repo)
        self._block_commits(repo)
        env = dict(os.environ, **GIT_ENV)
        env["PATH"] = f"{shim}:{env['PATH']}"

        res = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(repo), "--topic",
             "sample-topic", "--update", str(update_file), "--advanced",
             "only the index half fails", "--confirm"],
            capture_output=True, text=True, env=env,
        )

        assert fired.exists(), "the shim never intercepted — test proved nothing"
        assert res.returncode == 3
        assert "still STAGED" in res.stderr, res.stderr
        # The worktree half SUCCEEDED, so nothing may claim otherwise.
        assert "still MODIFIED" not in res.stderr, res.stderr
        # 🔴 COUNT the advice lines, do not grep for a PHRASE. An earlier
        # version asserted the absence of the new wording — so reverting the
        # message to its old, false text satisfied it vacuously (measured: that
        # mutant SURVIVED). One half failed, so exactly one fix line may appear.
        block = res.stderr.split("ROLLBACK INCOMPLETE", 1)[1]
        fix_lines = [ln for ln in block.splitlines() if ln.startswith("    ")]
        assert len(fix_lines) == 1, (
            f"expected exactly ONE fix line (only the index half failed), got "
            f"{len(fix_lines)}:\n" + "\n".join(fix_lines)
        )
        assert "restore --staged" in fix_lines[0], fix_lines[0]
        # 🔴 Indent-INDEPENDENT complement. The count above pins how many lines
        # are indented, which a reword can walk in one direction (emit the
        # worktree advice at a different indent) and break in the other (add an
        # explanatory comment to the index arm). The worktree arm is comment
        # lines in BOTH its old and new wording, so their absence is the claim
        # that survives rewording AND reindenting.
        # 🔴 The trade, stated: this ALSO fires if the index arm ever gains a
        # comment line — a false FAILURE on a legitimate reword. That direction
        # is chosen deliberately over a false PASS, and the invariant it depends
        # on ("the index arm is one bare command line") is pinned as a contract
        # beside that line in handoff_doc.py.
        assert not any(ln.lstrip().startswith("#") for ln in block.splitlines()), (
            "worktree advice (comment lines) printed although that half "
            "succeeded:\n" + block
        )
        assert doc_of(repo) == before, "the worktree half did not actually restore"

    def test_a_landed_commit_says_so_instead_of_a_bare_status_failed(
        self, repo: Path, update_file: Path
    ) -> None:
        """`status=failed` now contracts to "nothing happened" — so the ONE
        branch where a commit DOES exist must say so, or the contract lies."""
        real_git = shutil.which("git")
        assert real_git, "git not on PATH"
        shim = repo / "gitshim-note"
        shim.mkdir()
        marker = shim / "committed.marker"
        fired = shim / "intercepted.marker"
        write_exec(
            shim / "git",
            f'for a in "$@"; do [ "$a" = "commit" ] && : > "{marker}"; done\n'
            f'for a in "$@"; do\n'
            f'  if [ "$a" = "rev-parse" ] && [ -f "{marker}" ]; then\n'
            f'    : > "{fired}"; exit 7\n'
            f'  fi\n'
            f'done\n'
            f'exec {real_git} "$@"\n',
        )
        env = dict(os.environ, **GIT_ENV)
        env["PATH"] = f"{shim}:{env['PATH']}"

        res = subprocess.run(
            [sys.executable, str(TOOL), "--repo", str(repo), "--topic",
             "sample-topic", "--update", str(update_file), "--advanced",
             "the commit landed", "--confirm"],
            capture_output=True, text=True, env=env,
        )

        assert fired.exists(), "the shim never intercepted — test proved nothing"
        assert res.returncode == 3
        assert "THE COMMIT LANDED" in res.stderr, (
            "a commit exists but status=failed said nothing about it:\n" + res.stderr
        )
        assert "un-pushed" in res.stderr, res.stderr

    def test_worktree_only_failure_does_NOT_print_index_advice(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The MIRROR of the only-the-failed-half test, and it was unpinned.

        Round 2 gated both halves but tested only one, so ungating the INDEX arm
        survived the suite — a worktree-only failure would tell the operator to
        `git restore --staged` an index that had already been cleaned. Behaviour
        was correct; the coverage was not.

        Driven directly because the worktree half only fails on a real `OSError`
        from `write_bytes`, which no subprocess fixture can force.
        """
        doc = repo / "claudedocs" / "handoff-sample-topic.md"
        original = doc.read_bytes()

        def boom(self, data):  # noqa: ANN001, ANN202 - patched Path.write_bytes
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_bytes", boom)
        note = hd._undo_write(repo, doc, "claudedocs/handoff-sample-topic.md",
                              original)

        assert "still MODIFIED" in note, note
        assert "still STAGED" not in note, (
            "claimed the index was left staged although that half succeeded:\n" + note
        )
        assert "restore --staged" not in note, (
            "printed unstage advice although the index half succeeded:\n" + note
        )
    def test_the_mainline_module_it_imports_is_tracked_by_git(self) -> None:
        """🔴 THE IMPORT MAKES IT LOAD-BEARING. `handoff_doc.py` imports
        `git_mainline` at module scope, so an untracked `git_mainline.py` does not
        merely lose rule (h) — it makes the whole tool fail to start on a freshly
        deployed host, while the switch reports success."""
        mod = REPO_ROOT / "scripts" / "lib" / "git_mainline.py"
        assert mod.exists(), f"{mod} is missing from this tree"
        if not (REPO_ROOT / ".git").exists():
            return
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", "scripts/lib/git_mainline.py"],
            capture_output=True,
            text=True,
            env=dict(os.environ, **GIT_ENV),
        )
        assert out.stdout.strip() == "scripts/lib/git_mainline.py", (
            "scripts/lib/git_mainline.py is not tracked by git; the flake omits "
            "untracked files, so the deployed handoff_doc.py would raise "
            "ModuleNotFoundError on every run."
        )


# =============================================================================
# 🔴 RULE (h) — A BASE THAT IS THE WRONG DOCUMENT SAYS SO.
#
# THE INCIDENT. `handoff_doc.py` resolves its base from `--repo`'s working tree
# and never asked whether that clone was current. Pointed at one 313 commits
# behind, it printed `State now → NEW` — because the stale base genuinely lacked
# the section — and confirming would have rebuilt an 891-line / 14-heading doc
# from a 290-line / 9-heading base, silently discarding ~601 lines including a
# whole incident writeup, and exited 0 saying `status=written`. `BUCKET_NEW` was
# a bare label appended to a list; the `buckets:` line was the only tell and it
# reads as a routine classification.
#
# 🔴 RED-AT-BASE MATRIX, base `9667fb8b`. A full copy of the tree with
# `scripts/lib/*.py` rolled back to that revision (`git show "${sha}:${path}"`,
# each extraction asserted non-empty, each rolled-back file asserted to DIFFER
# from the working copy — a rollback that silently did nothing scores every test
# as green for the wrong reason):
#
#   test_handoff_doc.py + test_subsystem_touch.py
#     at 9667fb8b ......... 17 failed, 993 passed
#     at HEAD ............. 1010 passed
#   test_git_mainline.py ... cannot COLLECT at 9667fb8b (the module does not
#                            exist there); 13 passed at HEAD.
#
# ⚠ EIGHT OF THE NEW TESTS PASS AT BASE and are NOT regression coverage — the
# base never warns at all, so their green there is vacuous. Each says so in its
# own docstring, and each is proven LIVE at HEAD by the mutation battery:
#
#   the two SILENCE controls        `block-speaks-on-EVERY-run` and
#   (current clone, behind-on-code)  `currency-trigger-is-the-CLONE-not-the-DOC`
#   the three EXIT-CODE invariants  0/4/5 must not move; that they held before
#                                    is the point
#   the three derivation guards     dangling symref, local-counterpart decoy,
#   in test_subsystem_touch.py       and the `main`-repo control
#
# MUTATION BATTERY (isolated copy of the tree, `PYTHONDONTWRITEBYTECODE=1` and
# `__pycache__` cleared between mutants, every module restored from a byte copy
# and re-hashed, every anchor checked UNIQUE first): **21 mutants, 21 killed by
# the specifically expected test on that test's own assertion, 0 skipped**, plus
# a positive control (KILLED) and a no-op negative control (SURVIVED, required).
# Five rounds, and four of the five found a fault in THESE ASSERTIONS or in the
# harness rather than in the code:
#   1. the first POSITIVE CONTROL was DEAD — `EXIT_NO_ADVANCE = 4` -> `40`
#      survived the whole suite, because the tests compare against
#      `hd.EXIT_NO_ADVANCE` and the expectation moved with the mutation.
#   2. the harness scored six KILLED mutants as SURVIVED — it matched the
#      asserted source line raw, and pytest re-wraps it in the traceback.
#   3. `established-floor-to-zero` killed a FIXTURE PRECONDITION, not the guard:
#      the precondition read `< hd.MIN_ESTABLISHED_SECTIONS`, so lowering the
#      constant failed the setup and the real assertion never ran.
#   4. `size-tell-widened-to-LINES-only` and `block-speaks-on-EVERY-run` genuinely
#      SURVIVED: one fixture was shorter than its base on BOTH dimensions so it
#      could not observe the widening, and the silence assertion was on WORDS —
#      the mutant printed a block carrying neither headline. Fixed by a
#      discriminating fixture and by `between_buckets_and_diff`.
#   5. `commits-behind-unmeasured-becomes-ZERO` was scored against a test that
#      cannot REACH it (with no base ref the function is never called), and
#      `touch-ignores-the-derivation` was written so the derived rung still
#      answered. Both re-aimed.
# =============================================================================

# A base doc that is ESTABLISHED (5 sections, all canonical) and yet has NO
# `State now` — the exact shape a 313-commits-stale copy had. Deliberately not
# BASE_DOC minus a line: each section's text is distinct so a merge that keeps
# the wrong one cannot match a substring of the right one.
STALE_BASE_DOC = """# Handoff: sample-topic — 2026-05-01

## Goal
Make the sample subsystem stop dropping work under load.

## Live state
- the queue instrumentation has not been written yet

## Open investigations — live diagnosis state
### the drain counter has never been read
- **Observed (with values):** no counter exists; the endpoint 404s.

## Next steps (ranked)
1. Write the counter.

## How to verify
`python3 tools/queue_probe.py --for 60`
"""

# What the mainline copy grew into: more sections, more lines, and the whole
# incident writeup the stale base cannot see.
MAINLINE_DOC = STALE_BASE_DOC.replace(
    "## Live state", "## State now\n- Branch / PR: `feat/sample` / #99\n\n## Live state"
) + """
## Gotchas / decisions / dead-ends
- Bumping the pool size did nothing; the ceiling is not connections.

## The outage we caused
""" + "".join(f"- outage note {i}\n" for i in range(1, 40)) + """
## Corrections
""" + "".join(f"- correction {i}\n" for i in range(1, 40))


def _cfg(repo: Path) -> None:
    for k, v in (("user.name", "T"), ("user.email", "t@example.invalid"),
                 ("commit.gpgsign", "false")):
        _sh("git", "config", k, v, cwd=repo)


def mainline_repo(tmp_path: Path, mainline: str, doc_text: str) -> tuple[Path, Path]:
    """`(a real clone, the seed checkout that can push to its origin)`.

    🔴 A REAL `git clone`, because `refs/remotes/origin/HEAD` — the ref the
    derivation reads — is written by clone itself. Hand-writing that symref would
    test a shape invented here rather than the one the field repo has. Still no
    network: the origin is a bare repo in the same tmp_path.
    """
    origin = tmp_path / f"{mainline}-origin.git"
    _sh("git", "init", "-q", "--bare", "-b", mainline, str(origin), cwd=tmp_path)
    seed = tmp_path / f"{mainline}-seed"
    seed.mkdir()
    _sh("git", "init", "-q", "-b", mainline, cwd=seed)
    _cfg(seed)
    _sh("git", "remote", "add", "origin", str(origin), cwd=seed)
    (seed / "claudedocs").mkdir()
    (seed / "claudedocs" / "handoff-sample-topic.md").write_text(
        doc_text, encoding="utf-8"
    )
    _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=seed)
    _sh("git", "commit", "-q", "-m", "the base as it was", cwd=seed)
    _sh("git", "push", "-q", "origin", mainline, cwd=seed)
    work = tmp_path / f"{mainline}-work"
    _sh("git", "clone", "-q", str(origin), str(work), cwd=tmp_path)
    _cfg(work)
    return work, seed


def advance_doc_on_mainline(work: Path, seed: Path, mainline: str, text: str) -> None:
    """Move the doc forward on the mainline and FETCH it, leaving HEAD behind."""
    (seed / "claudedocs" / "handoff-sample-topic.md").write_text(text, encoding="utf-8")
    _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=seed)
    _sh("git", "commit", "-q", "-m", "the incident writeup", cwd=seed)
    _sh("git", "push", "-q", "origin", mainline, cwd=seed)
    _sh("git", "fetch", "-q", "origin", cwd=work)


STALE_HEAD = "🔴 THE BASE DOCUMENT IS NOT THE NEWEST COMMITTED COPY"
TELL_HEAD = "🔴 THIS MERGE LOOKS LIKE IT RESOLVED THE WRONG BASE"


def rule_h_block(stdout: str) -> str:
    """Rule (h)'s block only — headline through the line before the diff."""
    idx = [stdout.index(h) for h in (STALE_HEAD, TELL_HEAD) if h in stdout]
    if not idx:
        return ""
    rest = stdout[min(idx):]
    cut = rest.find("--- a/")
    return rest if cut < 0 else rest[:cut]


def between_buckets_and_diff(stdout: str) -> list[str]:
    """Every line printed after the `buckets:` line and before the diff starts.

    🔴 THE STRUCTURAL FORM OF "SILENT", and the mutation battery is why it
    exists. Asserting `STALE_HEAD not in out and TELL_HEAD not in out` is a guard
    on WORDS: the mutant that removed rule (h)'s early return still printed its
    remedy paragraph on every ordinary run — a block with neither headline in it
    — and SURVIVED a green suite. `claude/RULES.md`: a guard that can pass while
    the hazard exists in a different shape is spelled, not structural. This asks
    the question that actually matters — did the run print ANYTHING extra?
    """
    head = "buckets:"
    if head not in stdout:
        return []
    rest = stdout[stdout.index(head):].splitlines()[1:]
    out: list[str] = []
    for line in rest:
        if line.startswith("--- a/") or line.startswith("status="):
            break
        out.append(line)
    return [ln for ln in out if ln.strip()]


class TestAStaleBaseIsLoud:
    """Both directions, and the loudness is measured against the diff's position."""

    def test_a_STALE_base_on_a_TRUNK_mainline_repo_WARNS(self, tmp_path: Path,
                                                        update_file: Path) -> None:
        """🔴 THE REGRESSION, red at base `9667fb8b` — which prints only
        `buckets: State now → NEW` and nothing else.

        The repo's mainline is `trunk` ON PURPOSE: the incident's repo is
        `homelab-infra`, and a currency check that assumed `main` would report
        "cannot measure" there and print nothing — the same silence, reached a
        second way."""
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        res = run_tool(work, update=update_file)
        assert res.returncode == 0, res.stderr
        block = rule_h_block(res.stdout)
        assert STALE_HEAD in block, res.stdout
        # the STATE, not just the word: the ref it derived and the counts a
        # reader needs to decide. A guard on the headline alone is walkable by
        # rewording the headline.
        assert "origin/trunk" in block
        base_shape = hd.doc_shape(STALE_BASE_DOC)
        main_shape = hd.doc_shape(MAINLINE_DOC)
        assert f"{base_shape.sections} sections / {base_shape.lines} lines" in block
        assert f"{main_shape.sections} sections / {main_shape.lines} lines" in block
        # the fixture must actually be lopsided, or the size line proves nothing
        assert main_shape.lines > base_shape.lines * 3
        assert main_shape.sections > base_shape.sections

    def test_the_block_is_printed_ABOVE_the_diff(self, tmp_path: Path,
                                                 update_file: Path) -> None:
        """🔴 POSITION IS THE FIX. The information already existed one token wide
        inside `buckets:`; putting it after several hundred diff lines would
        reproduce the incident with more words."""
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        out = run_tool(work, update=update_file).stdout
        assert out.index(STALE_HEAD) < out.index("--- a/"), out

    def test_a_doc_ABSENT_here_but_PRESENT_on_the_mainline_warns(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """The same bug in its loudest disguise: no base at all, so every section
        merges as NEW and the committed document is replaced by the delta. Red at
        base, which treats a missing base as an ordinary first write."""
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        (work / "claudedocs" / "handoff-sample-topic.md").unlink()
        _sh("git", "rm", "-q", "--cached", "--",
            "claudedocs/handoff-sample-topic.md", cwd=work)
        _sh("git", "commit", "-q", "-m", "drop it locally", cwd=work)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        out = run_tool(work, update=update_file).stdout
        assert STALE_HEAD in out, out
        # Wording widened deliberately: the trigger is now "no USABLE doc"
        # (missing, empty or whitespace), because a blank base destroys the
        # committed document exactly as a missing one does. The old string said
        # "there is NO <path>", which is false of a file that exists and is blank.
        assert "no usable claudedocs/handoff-sample-topic.md" in out, out
        assert "will be replaced by this delta" in out, out

    def test_a_CURRENT_clone_prints_NOTHING_new(self, repo: Path,
                                                update_file: Path) -> None:
        """🔴 THE NOISE CONTROL, and the reason the tells are shaped as they are.
        A warning that fires every run is the failure being fixed.

        ⚠ GREEN AT BASE `9667fb8b` — the base never warns at all, so this green
        is vacuous there. It is proven LIVE at HEAD by the mutation battery:
        `currency-trigger-inverted` and `established-floor-to-zero` each turn it
        red."""
        out = run_tool(repo, update=update_file).stdout
        assert "buckets:" in out, "the ordinary run must still classify"
        assert STALE_HEAD not in out and TELL_HEAD not in out, out
        assert rule_h_block(out) == ""
        # 🔴 THE STRUCTURAL HALF — see `between_buckets_and_diff`. The two
        # headline assertions above are walkable by printing a block that
        # contains neither, which a mutant did.
        assert between_buckets_and_diff(out) == [], out

    def test_behind_on_CODE_but_current_on_the_DOC_stays_SILENT(
        self, repo: Path, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 THE TRIGGER IS THE DOC, NOT THE CLONE. Nearly every agent worktree
        is a few commits behind mainline; warning on that would fire constantly
        and teach everyone to skip the block. The whole-clone count is printed as
        CONTEXT inside a block the doc already triggered, never as the trigger.

        ⚠ GREEN AT BASE `9667fb8b` — vacuous there, since the base never warns.
        Proven LIVE at HEAD by the mutation battery: `currency-trigger-is-the-
        CLONE-not-the-DOC` turns it red."""
        advance_remote(tmp_path)
        _sh("git", "fetch", "-q", "origin", cwd=repo)
        behind = _sh("git", "rev-list", "--count", "HEAD..origin/main", cwd=repo)
        assert int(behind.strip()) > 0, "the control did not make the clone behind"
        out = run_tool(repo, update=update_file).stdout
        assert STALE_HEAD not in out and TELL_HEAD not in out, out


class TestTheTellsAreEachReachableAndEachNarrow:
    """Unit-level, both directions per tell — a tell that cannot go quiet is
    noise and a tell that cannot go loud is decoration."""

    def _buckets(self, base: str, update: str):
        return hd.merge_report(base, update).buckets

    def test_a_SKELETON_heading_arriving_NEW_on_an_established_base_is_a_tell(
        self,
    ) -> None:
        upd = "## State now\n- the drain loop is fixed\n"
        tells = hd.wrong_base_tells(STALE_BASE_DOC, upd,
                                    self._buckets(STALE_BASE_DOC, upd))
        assert any("state now" in t for t in tells), tells

    def test_a_REGLOSSED_heading_is_NOT_a_tell(self) -> None:
        """🔴 THE MEASURED FALSE-FIRE THIS RULE'S SHAPE EXISTS FOR. Membership is
        by canonical PREFIX, not by full heading text: an author re-glossing
        `## State now` to `## State now — THE STORE IS PUBLIC` makes it NEW
        against a base that plainly HAS a state-now section. Replaying the 49
        real updates in this repo's history, full-text membership fires on 4 of
        them (8.2%) and prefix membership on 0."""
        base = BASE_DOC  # carries a plain `## State now`
        upd = "## State now — 🔴 THE STORE IS PUBLIC\n- rewritten\n"
        assert hd.merge_report(base, upd).buckets == (
            ("State now — 🔴 THE STORE IS PUBLIC", hd.BUCKET_NEW),
        ), "the fixture must actually produce a NEW bucket, or it proves nothing"
        assert hd.wrong_base_tells(base, upd, self._buckets(base, upd)) == ()

    def test_a_STUB_base_is_not_established_so_growth_is_not_a_tell(self) -> None:
        """A doc below `MIN_ESTABLISHED_SECTIONS` is a stub; a canonical heading
        arriving there is ordinary growth. Of the 44 real handoff docs exactly
        one has 3 sections and three have 4."""
        stub = "# H\n\n## Goal\ng\n\n## Next steps\nn\n"
        upd = "## State now\n- s\n"
        # 🔴 A LITERAL 2, not `< hd.MIN_ESTABLISHED_SECTIONS`. Derived from the
        # constant, this precondition ATE the mutant that lowered it: the
        # mutation battery reported the guard killed while the assertion below
        # never ran. `claude/RULES.md` — never derive a test's expectation from
        # the implementation it tests.
        assert hd.doc_shape(stub).sections == 2
        assert hd.wrong_base_tells(stub, upd, self._buckets(stub, upd)) == ()

    def test_an_update_LARGER_than_its_base_is_a_tell(self) -> None:
        upd = "".join(
            f"## Section {i}\n" + "".join(f"- line {j}\n" for j in range(30))
            for i in range(9)
        )
        tells = hd.wrong_base_tells(BASE_DOC, upd, self._buckets(BASE_DOC, upd))
        assert any("LARGER than the base" in t for t in tells), tells

    def test_an_ORDINARY_delta_is_not_a_size_tell(self) -> None:
        """The realistic control: the fixture pair this whole file is built on."""
        assert hd.wrong_base_tells(
            BASE_DOC, UPDATE_DOC, self._buckets(BASE_DOC, UPDATE_DOC)
        ) == ()

    def test_MORE_LINES_ALONE_is_not_a_size_tell(self) -> None:
        """🔴 THE DISCRIMINATING FIXTURE, and the mutation battery is what
        demanded it. `test_an_ORDINARY_delta_is_not_a_size_tell` cannot see the
        `and`→lines-only mutant at all: its update is SHORTER than its base on
        both dimensions, so widening the rule changes nothing and the mutant
        SURVIVED a green run. `claude/RULES.md` — a fixture that can only ever
        produce the same verdict cannot observe the mutation.

        So: one section (fewer than the base's six) carrying far more lines than
        the base's 22. Lines-only fires here; both-dimensions does not.

        BOTH DIMENSIONS ARE REQUIRED because it is measured: replaying the 49
        real updates in this repo's history, `lines only` fires on 10 (20.4%)
        and `lines AND sections` on 1 (2.0%) — comparable to rule (f)'s
        documented 2.4%. A handoff update routinely rewrites more lines than a
        short doc contains."""
        upd = "## Next steps (ranked)\n" + "".join(
            f"{i}. step {i}\n" for i in range(1, 80)
        )
        base = hd.doc_shape(BASE_DOC)
        got = hd.doc_shape(upd)
        assert got.lines > base.lines and got.sections < base.sections, (base, got)
        assert hd.wrong_base_tells(BASE_DOC, upd, self._buckets(BASE_DOC, upd)) == ()

    def test_NO_BASE_AT_ALL_yields_no_TELL_because_every_heading_is_NEW(self) -> None:
        """The heuristic half must not fire on a genuine first write. That case
        is `base_currency`'s, and it is the half with hard evidence."""
        assert hd.wrong_base_tells("", UPDATE_DOC, ()) == ()


class TestBaseCurrencyMeasuresAndSaysWhenItCannot:
    def test_it_derives_a_TRUNK_mainline(self, tmp_path: Path) -> None:
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        cur = hd.base_currency(work, "claudedocs/handoff-sample-topic.md")
        assert cur.base_ref == "origin/trunk" and cur.unmeasured is None
        assert cur.doc_behind == 1 and cur.stale
        assert cur.mainline == hd.doc_shape(MAINLINE_DOC)

    def test_a_repo_with_NO_mainline_ref_reports_UNMEASURED_not_zero(
        self, tmp_path: Path
    ) -> None:
        """🔴 AN UNANSWERABLE QUESTION IS NOT A CLEAN ANSWER. A 0 here would be
        indistinguishable from a measured current base."""
        lone = tmp_path / "lone"
        lone.mkdir()
        _sh("git", "init", "-q", "-b", "topic", cwd=lone)
        _cfg(lone)
        (lone / "f.txt").write_text("x\n", encoding="utf-8")
        _sh("git", "add", "--", "f.txt", cwd=lone)
        _sh("git", "commit", "-q", "-m", "seed", cwd=lone)
        cur = hd.base_currency(lone, "claudedocs/handoff-sample-topic.md")
        assert cur.base_ref is None and cur.doc_behind is None
        assert not cur.stale and cur.unmeasured
        assert "origin/main" in cur.unmeasured, cur.unmeasured

    def test_an_UNMEASURED_currency_is_PRINTED_when_a_tell_fired(self) -> None:
        """…and only then. Printing it every run would be the noise the block
        exists to avoid; withholding it beside a live suspicion would hand over a
        doubt with no way to settle it."""
        cur = hd.BaseCurrency(None, ("origin/main",), None, None, None,
                              "no mainline ref resolves in this clone")
        loud = hd.wrong_base_report(("a tell",), cur, "d.md", Path("/r"),
                                    hd.doc_shape(BASE_DOC),
                                    cur.replaces_mainline_doc(BASE_DOC))
        assert "base currency UNCHECKED" in loud
        quiet = hd.wrong_base_report((), cur, "d.md", Path("/r"),
                                     hd.doc_shape(BASE_DOC),
                                     cur.replaces_mainline_doc(BASE_DOC))
        assert quiet == "", "an unmeasurable check must not speak on its own"


class TestRuleHDidNotMoveTheExitCodes:
    """🔴 IT WARNS AND NEVER REFUSES. A refusal here becomes a gate people learn
    to click through, and 4/5/6 are load-bearing in the skill.

    ⚠ ALL GREEN AT BASE `9667fb8b` — these are INVARIANT GUARDS, not regression
    coverage. That the codes held before is exactly the point.
    """

    def test_a_stale_base_still_exits_0_and_still_writes_under_confirm(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        res = run_tool(work, "--confirm", update=update_file)
        assert res.returncode == 0, res.stderr + res.stdout
        assert "status=written" in res.stdout + res.stderr
        doc = (work / "claudedocs" / "handoff-sample-topic.md").read_text(
            encoding="utf-8"
        )
        assert "the drain loop is fixed and merged" in doc

    def test_NO_ADVANCE_is_still_4_and_prints_no_block(self, tmp_path: Path,
                                                      update_file: Path) -> None:
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        res = run_tool(work, update=update_file, advanced="nothing")
        assert res.returncode == hd.EXIT_NO_ADVANCE
        assert STALE_HEAD not in res.stdout + res.stderr

    def test_NO_CHANGE_is_still_5_and_prints_no_block(self, tmp_path: Path) -> None:
        work, seed = mainline_repo(tmp_path, "trunk", STALE_BASE_DOC)
        advance_doc_on_mainline(work, seed, "trunk", MAINLINE_DOC)
        noop = tmp_path / "noop.md"
        noop.write_text("## Goal\nMake the sample subsystem stop dropping work "
                        "under load.\n", encoding="utf-8")
        res = run_tool(work, update=noop)
        assert res.returncode == hd.EXIT_NO_CHANGE, res.stdout + res.stderr
        assert STALE_HEAD not in res.stdout + res.stderr


# --------------------------------------------------------------------------
# rules (i) and (j) — one doc per effort, and a forcing function per rank
# (operator decision 2026-08-28; see the module docstring and
#  claude/skills/handoff/reference/write-gate.md §C)
# --------------------------------------------------------------------------

FORCED_UPDATE = """## State now
- Branch / PR: `feat/sample` / #99

## Next steps (ranked)
1. Watch the drain rate for a day. forcing: gate — the load soak blocks the release
"""


def write_delta(tmp_path: Path, name: str, text: str) -> Path:
    """A named delta file. NAMED per-test rather than reusing `update.md`,
    because several tests in this class write two different deltas in one
    tmp_path and a shared name would let one silently read the other's bytes."""
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


class TestADatedTopicIsRefused:
    """Rule (i-a). A slug carrying a date names a PER-SESSION doc: next
    session's date differs, so it can never be updated in place.

    🔴 RED AT `origin/main` — before this change every case below exited 0 and
    wrote the doc. That is the regression this class covers, and it is why the
    write-nothing assertions hash the whole tree rather than checking the file.
    """

    @pytest.mark.parametrize(
        "topic,token",
        [
            ("browser-bridge-2026-08-01", "2026-08-01"),   # devrc spelling: trailing
            ("2026-07-18-remix-session", "2026-07-18"),    # homelab spelling: leading
            # 🔴 the token is `2026`, not `2026-07`: the year-month arm was
            # DELETED as a dead predicate (see `_TOPIC_DATE`), and the bare-year
            # arm catches this spelling. Asserting `2026-07` here would have
            # passed either way — the refusal echoes the topic, which contains
            # it — which is how the redundant arm stayed invisible.
            ("remix-2026-07-session", "2026"),             # year-month spelling
            ("q3-2026-cleanup", "2026"),                   # bare year
        ],
        ids=["trailing-iso", "leading-iso", "year-month", "bare-year"],
    )
    def test_every_dated_spelling_in_the_corpus_is_refused(
        self, repo: Path, update_file: Path, topic: str, token: str
    ) -> None:
        before = tree_hash(repo)
        res = run_tool(repo, update=update_file, topic=topic)
        assert res.returncode == hd.EXIT_DOC_PER_EFFORT, res.stdout + res.stderr
        assert "status=dated-topic" in res.stderr
        assert token in res.stderr, "the refusal must name the date it found"
        assert tree_hash(repo) == before, "a refusal wrote something"

    def test_the_refusal_names_the_undated_topic_to_use(
        self, repo: Path, update_file: Path
    ) -> None:
        """A refusal with no pasteable fix is one people work around."""
        res = run_tool(repo, update=update_file, topic="2026-07-18-remix-session")
        assert "--topic remix-session" in res.stderr, res.stderr

    def test_new_effort_does_NOT_bypass_a_dated_topic(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE BYPASS TEST. `--new-effort` is rule (i-b)'s assertion; if it
        also let a dated slug through, the crisp half of the rule would be
        one flag away from inert — and that flag is right there in the OTHER
        refusal's remedy text, so a session would find it."""
        before = tree_hash(repo)
        res = run_tool(
            repo, "--new-effort", update=update_file, topic="remix-2026-08-01"
        )
        assert res.returncode == hd.EXIT_DOC_PER_EFFORT
        assert "status=dated-topic" in res.stderr
        assert tree_hash(repo) == before

    def test_an_undated_topic_is_NOT_refused(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 POSITIVE CONTROL. A guard that refuses everything is not a guard,
        and `_TOPIC_DATE` is a regex over a caller-supplied string — the
        cheapest place for an over-broad pattern to hide."""
        res = run_tool(repo, update=update_file, topic="sample-topic")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=proposed" in res.stdout

    @pytest.mark.parametrize(
        "topic", ["h2-planning", "ipv6-rollout", "s3-403-triage", "phase-2-store"]
    )
    def test_ordinary_numbers_in_a_slug_are_not_dates(self, topic: str) -> None:
        """The over-broad direction, at four points. A slug is allowed to carry
        digits — `h2`, `ipv6`, `403`, `phase-2` — and a pattern that ate them
        would make the rule unpredictable at the moment a session obeys it."""
        assert hd.topic_carries_a_date(topic) is None


class TestASecondDocForAnExistingEffortIsRefused:
    """Rule (i-b). Creating the N+1th doc stops being the SILENT DEFAULT.

    🔴 WHAT THIS DOES NOT CLAIM: it does not detect that two slugs are the same
    effort. No fuzzy match is attempted or wanted. What it pins is that the
    caller is shown the list and must make an explicit assertion.
    """

    def test_a_new_topic_is_refused_and_lists_what_exists(
        self, repo: Path, update_file: Path
    ) -> None:
        before = tree_hash(repo)
        res = run_tool(repo, update=update_file, topic="remix-hardening-session")
        assert res.returncode == hd.EXIT_DOC_PER_EFFORT, res.stdout + res.stderr
        assert "status=new-doc" in res.stderr
        assert "handoff-sample-topic.md" in res.stderr, (
            "the refusal must LIST the existing docs — without the list it is a "
            "block with no way to comply, and --new-effort becomes reflexive"
        )
        assert tree_hash(repo) == before

    def test_new_effort_lands_it(self, repo: Path, update_file: Path) -> None:
        """The assertion works — otherwise the rule bans new efforts outright."""
        res = run_tool(repo, "--new-effort", update=update_file, topic="genuinely-new")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=proposed" in res.stdout

    def test_updating_an_EXISTING_doc_never_asks(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE SILENCE THAT MATTERS. The ordinary path — updating in place —
        is the behaviour this whole change is trying to make normal, so it must
        not acquire a flag. If this ever fails, the rule is inverted."""
        res = run_tool(repo, update=update_file)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=new-doc" not in res.stdout + res.stderr

    def test_the_FIRST_doc_in_a_repo_needs_no_flag(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 THE BOOTSTRAP CASE, and the one an over-eager rule would break.
        A repo with no handoff docs at all has no effort to duplicate, so
        `existing` is empty and the question is never asked. Requiring
        `--new-effort` here would gate the one case that is unambiguously
        correct."""
        origin = tmp_path / "origin.git"
        _sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
        work = tmp_path / "fresh"
        work.mkdir()
        _sh("git", "init", "-q", "-b", "main", cwd=work)
        for k, v in (("user.name", "T"), ("user.email", "t@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=work)
        _sh("git", "remote", "add", "origin", str(origin), cwd=work)
        (work / "README.md").write_text("fresh\n", encoding="utf-8")
        _sh("git", "add", "--", "README.md", cwd=work)
        _sh("git", "commit", "-q", "-m", "seed", cwd=work)

        res = run_tool(work, update=update_file, topic="first-effort")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=proposed" in res.stdout


class TestARankedItemMustNameAForcingFunction:
    """Rule (j). The closed vocabulary is the structural half; the truth of the
    evidence beside it is NOT checkable and is not claimed to be."""

    def test_an_untagged_item_is_refused_and_writes_nothing(
        self, repo: Path, tmp_path: Path
    ) -> None:
        upd = write_delta(
            tmp_path, "untagged.md",
            "## Next steps (ranked)\n1. Re-read the retry wrapper.\n",
        )
        before = tree_hash(repo)
        res = run_tool(repo, update=upd)
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert "status=unforced" in res.stderr
        assert "Re-read the retry wrapper" in res.stderr, "name the offending item"
        assert tree_hash(repo) == before

    def test_a_kind_outside_the_vocabulary_is_refused(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 THE POINT OF AN ALLOWLIST. `followup` is precisely the label a
        self-generated item reaches for, and it is refused BY DEFAULT rather
        than by being enumerated as banned — which is what a rewording cannot
        walk around."""
        upd = write_delta(
            tmp_path, "followup.md",
            "## Next steps (ranked)\n1. Tidy the docs. forcing: followup\n",
        )
        res = run_tool(repo, update=upd)
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert "'followup'" in res.stderr, "say WHAT was typed, or it cannot be fixed"

    def test_the_refusal_prints_the_whole_vocabulary(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """A closed set the caller cannot see is a guessing game."""
        upd = write_delta(
            tmp_path, "vocab.md", "## Next steps (ranked)\n1. Do a thing.\n"
        )
        res = run_tool(repo, update=upd)
        for kind in hd.EXTERNAL_FORCING_KINDS:
            assert kind in res.stderr, f"{kind} missing from the printed vocabulary"

    @pytest.mark.parametrize("kind", sorted(hd.EXTERNAL_FORCING_KINDS))
    def test_every_external_kind_is_accepted(
        self, repo: Path, tmp_path: Path, kind: str
    ) -> None:
        """🔴 POSITIVE CONTROL, PER MEMBER. A set constant is exactly the place a
        typo survives a green suite: one unreachable member would refuse an item
        the skill's own vocabulary told the author to write."""
        upd = write_delta(
            tmp_path, f"kind-{kind}.md",
            f"## Next steps (ranked)\n1. Do the thing. forcing: {kind} — evidence\n",
        )
        res = run_tool(repo, update=upd)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=proposed" in res.stdout

    def test_forcing_none_is_ACCEPTED_and_reported(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 Refusing `none` would not delete self-generated items — it would
        teach sessions to type `incident` falsely, moving the population
        underground. So it lands, and it is COUNTED where a reader sees it."""
        upd = write_delta(
            tmp_path, "none.md",
            "## Next steps (ranked)\n1. Refactor the parser. forcing: none\n",
        )
        res = run_tool(repo, update=upd)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=proposed" in res.stdout
        assert "forcing: none" in res.stdout and "NO external forcing" in res.stdout
        assert "Refactor the parser" in res.stdout

    def test_the_self_generated_block_is_SILENT_when_every_item_is_external(
        self, repo: Path, update_file: Path
    ) -> None:
        """The ordinary run must not carry a reassuring "0 self-generated" line —
        `dropped_durable_report`'s stated reason, applied to the same shape."""
        res = run_tool(repo, update=update_file)
        assert "NO external forcing" not in res.stdout

    def test_legacy_base_items_are_not_retroactively_refused(
        self, repo: Path, update_file: Path
    ) -> None:
        """🔴 THE PERMANENTLY-RED-GATE GUARD, and the reason rule (j) reads the
        UPDATE rather than the merge. `BASE_DOC`'s two ranked items carry no
        `forcing:` field — as every one of the 384 real ranked items in the
        corpus does not. If this ever fails, the rule became a gate that no
        established repo can pass, which `claude/RULES.md` calls worse than no
        gate at all."""
        assert "forcing:" not in BASE_DOC, (
            "the fixture stopped being legacy, so this test proves nothing"
        )
        res = run_tool(repo, update=update_file)
        assert res.returncode == 0, res.stdout + res.stderr

    def test_an_update_with_no_next_steps_section_is_not_asked(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Omitting a section leaves it ALONE (the merge's contract), so an
        update that touches no ranks changes no ranks and must not be gated."""
        upd = write_delta(
            tmp_path, "nosteps.md",
            "## State now\n- Branch / PR: `feat/sample` / #101\n",
        )
        res = run_tool(repo, update=upd)
        assert res.returncode == 0, res.stdout + res.stderr

    def test_a_numbered_line_inside_a_FENCE_is_not_a_ranked_item(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Fence awareness, for the reason `split_sections` has it: a handoff
        pastes numbered output constantly, and a sample log line is not a work
        item to be tagged."""
        upd = write_delta(
            tmp_path, "fenced.md",
            "## Next steps (ranked)\n"
            "1. Watch the drain rate. forcing: incident — paging since 09:00Z\n"
            "```\n1. this is sample output, not a rank\n2. neither is this\n```\n",
        )
        res = run_tool(repo, update=upd)
        assert res.returncode == 0, res.stdout + res.stderr
        assert len(hd.ranked_items(upd.read_text(encoding="utf-8"))) == 1

    def test_a_nested_numbered_line_is_not_a_rank(self) -> None:
        """The ranks are half a claim's identity (`claim-work --slug-for <doc>
        <rank>`), so counting a sub-item as a rank would re-point live claims."""
        text = (
            "## Next steps (ranked)\n"
            "1. Fix it. forcing: gate — CI red\n"
            "    1. sub-step, not a rank\n"
        )
        assert [i.rank for i in hd.ranked_items(text)] == ["1"]

    def test_items_outside_a_next_steps_heading_are_not_asked(self) -> None:
        """Scoped to the queue. A numbered list under `## Goal` is prose."""
        assert hd.ranked_items("## Goal\n1. ship the thing\n") == []


class TestTheFieldIsFoundOnTheWholeItemNotTheNumberedLine:
    """🔴 RED AT `3f7f2e62` — every test in this class. Rule (j)'s first version
    ran `_FORCING.search` against the single line `_RANKED_ITEM` had matched, so
    a tag on any CONTINUATION line of a multi-line item was invisible.

    MEASURED over the committed corpus: 179 of 257 ranked items in devrc's
    `claudedocs/` and 99 of 181 in homelab-talos' wrap onto continuation lines,
    so the majority shape was structurally unable to pass. And the refusal it
    got was the worst kind — `[no forcing: field]` about an item that HAS one,
    under a printed remedy already satisfied: the obvious fix is a no-op, the
    re-run is byte-identical, and `/handoff` step 5 is this doc's SOLE writer,
    so the session's handoff never lands at all.

    Two halves, and the class covers both, because either alone is still broken:
    the BLOCK search (the behaviour), and a remedy per CAUSE (the message).
    """

    # 🔴 THE OBSERVED FAILURE, verbatim in shape: two items that BOTH carry a
    # correct tag, on the last line of a wrapped item. `rc=8` at `3f7f2e62`.
    OBSERVED = (
        "## Next steps (ranked)\n"
        "1. 🔴 **Land the pre-push gate change-scoping** (`devrc#952`).\n"
        "   The hook scopes to the whole tree, so every push re-runs the full\n"
        "   suite and the gate is red for reasons the push did not cause.\n"
        "   forcing: gate — devrc#952's CI leg has been red since 2026-08-26\n"
        "2. **Fix the `measure.py` latent misreport.**\n"
        "   It prints a zero where the query returned no rows at all.\n"
        "   forcing: regression — measured against the 2026-08-20 baseline\n"
    )

    @staticmethod
    def _naive_kinds(text: str) -> list[str | None]:
        """The boundary this change REJECTED: numbered line to the next numbered
        line, or the end of the section. Kept here as the sensitivity control for
        `test_trailing_boilerplate_does_not_tag_the_last_item` — without it that
        assertion could be passing for any reason at all."""
        _fm, body = hd.split_front_matter(text)
        _pre, secs = hd.split_sections(body)
        out: list[str | None] = []
        for heading, sb in secs:
            if not hd.heading_text(heading).lower().startswith(hd.NEXT_STEPS_PREFIX):
                continue
            lines = [ln for _i, ln in hd._unfenced(sb)]
            starts = [k for k, ln in enumerate(lines) if hd._RANKED_ITEM.match(ln)]
            for n, k in enumerate(starts):
                end = starts[n + 1] if n + 1 < len(starts) else len(lines)
                m = hd._FORCING.search("\n".join(lines[k:end]))
                out.append(m.group(1).lower() if m else None)
        return out

    def test_the_observed_refusal_of_two_correctly_tagged_items_is_gone(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """The exact reported symptom: `rc=8`, `status=unforced`, two items that
        are both tagged. Reproduced through the TOOL, not just the parser."""
        upd = write_delta(tmp_path, "wrapped.md", self.OBSERVED)
        res = run_tool(repo, update=upd)
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=unforced" not in res.stderr
        assert [i.kind for i in hd.ranked_items(self.OBSERVED)] == ["gate", "regression"]

    @pytest.mark.parametrize(
        "tagline",
        (
            "   forcing: gate — CI red",
            "   **forcing: gate** — CI red",
            "   `forcing: gate` — CI red",
            "   Forcing: Gate — CI red",
            "   forcing:gate — CI red",
            # 🔴 THE ONE SPELLING THIS CHANGE ADMITTED rather than reported.
            # Emphasis BETWEEN the key and the colon, in a skill body that bolds
            # its field names. Safe structurally, not by a guess about intent:
            # what follows the colon must be a member of a seven-word closed
            # vocabulary, so a false positive requires prose that literally reads
            # `forcing` + punctuation + a kind — which is the tag.
            "   **forcing:** gate — CI red",
            "   **forcing**: gate — CI red",
        ),
    )
    def test_an_accepted_spelling_on_a_continuation_line_counts(
        self, repo: Path, tmp_path: Path, tagline: str
    ) -> None:
        text = "## Next steps (ranked)\n1. Land the retry-wrapper fix.\n" + tagline + "\n"
        res = run_tool(repo, update=write_delta(tmp_path, "spelling.md", text))
        assert res.returncode == 0, res.stdout + res.stderr
        assert [i.kind for i in hd.ranked_items(text)] == ["gate"]

    @pytest.mark.parametrize(
        "attempt",
        (
            "forcing function: gate — CI red",   # a word between key and colon
            "forcing = gate",                    # a separator that is not a colon
            "forcing — gate",                    # ditto, em dash
        ),
    )
    def test_a_near_miss_is_REFUSED_and_NAMED_never_called_absent(
        self, repo: Path, tmp_path: Path, attempt: str
    ) -> None:
        """🔴 THE MESSAGE HALF. These stay refused — the grammar is deliberately
        strict, `subsystem_resolver._NEAR_MISS_MARKER`'s reason — but the refusal
        must say the field is UNPARSED, not ABSENT, and must not print the remedy
        the author has already carried out."""
        text = "## Next steps (ranked)\n1. Land the fix.\n   " + attempt + "\n"
        res = run_tool(repo, update=write_delta(tmp_path, "nearmiss.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        err = res.stderr
        assert "[no forcing: field]" not in err, (
            "the item HAS a field — telling it there is none is the reported bug"
        )
        assert "Tag each item marked" not in err, (
            "a remedy already satisfied: the fix is a no-op and the re-run "
            "prints identical bytes, so the session cannot recover"
        )
        assert "unparsed forcing field on:" in err, "say WHICH line, or it cannot be fixed"
        assert attempt in err, "quote the offending line verbatim"

    @pytest.mark.parametrize(
        "attempt",
        ("forcing function: followup", "forcing = tech-debt", "forcing — someday"),
    )
    def test_a_near_miss_with_an_UNLISTED_kind_is_NOT_named_it_reads_ABSENT(
        self, repo: Path, tmp_path: Path, attempt: str
    ) -> None:
        """🔴 THE SCOPE OF THE TEST ABOVE, WHICH SKILL.md USED TO OVERSTATE.
        `_FORCING_ATTEMPT` is anchored on the CLOSED VOCABULARY at both ends —
        that anchoring is the entire reason it is safe to run over prose — so a
        near-miss whose VALUE is not a listed kind matches nothing and falls
        through to `[no forcing: field]`.

        The skill said flatly that ``forcing function:`` / ``forcing = gate``
        "are near-misses, NAMED not absent"; measured at `e34ed6ef`,
        `forcing function: followup` gets `[no forcing: field]`. The sentence is
        now scoped to a listed kind, and this is what makes that scoping a
        claim rather than a promise.

        ⚠ NOT a defect and NOT being fixed: loosening the kind anchor is how the
        net starts quoting ordinary prose back at an author. It is a documented
        limit — `claude/RULES.md`, a comment is a claim too."""
        text = "## Next steps (ranked)\n1. Land the fix.\n   " + attempt + "\n"
        item = hd.ranked_items(text)[0]
        assert item.kind is None and item.near_miss is None, repr(attempt)
        res = run_tool(repo, update=write_delta(tmp_path, "unlisted.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert hd.MARK_NO_FIELD in res.stderr
        assert "unparsed forcing field on:" not in res.stderr

    def test_a_field_inside_a_FENCE_still_does_not_count_and_says_why(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """`_unfenced`'s contract is preserved — a pasted sample is not a
        declaration — but the author can SEE the field in their file, so
        `[no forcing: field]` would read as a lie about the input."""
        text = (
            "## Next steps (ranked)\n"
            "1. Land the fix.\n"
            "   ```\n"
            "   forcing: gate\n"
            "   ```\n"
        )
        assert [i.kind for i in hd.ranked_items(text)] == [None], (
            "a fenced field must NOT be accepted as a tag"
        )
        res = run_tool(repo, update=write_delta(tmp_path, "fencedtag.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert "[fenced]" in res.stderr
        assert "[no forcing: field]" not in res.stderr
        assert "code fence" in res.stderr, "name the reason it did not count"

    def test_trailing_boilerplate_does_not_tag_the_last_item(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 WHY THE BLOCK DOES NOT SIMPLY RUN TO THE NEXT NUMBERED ITEM.

        The corpus shows what sits after a ranked list: 14 blocks are followed by
        unindented prose after a blank line, and 7 of those are the skill's own
        `🔴 **This list is a WORK QUEUE …**` boilerplate, copied verbatim. The
        template block it comes from also carries "`forcing: none` is the honest
        opt-out" — so under the naive boundary the LAST untagged item silently
        acquires a `none` from text its author pasted out of the instructions.

        The `_naive_kinds` control is what makes this test sensitive: it asserts
        the rejected boundary DOES tag item 2 on this very input.

        ⚠ NOT RED AT `3f7f2e62` — labelled, not counted as regression coverage.
        It is the ONE test in this class that passes against the pre-change tree
        (measured: 15 failed, 1 passed), because a single-line search cannot see
        the boilerplate either. It guards the ALTERNATIVE fix — the naive block
        boundary a reader would reach for next — and it is shown reachable by
        the `naive-block-boundary` row in `scripts/tests/mutants-handoff-cap.sh`,
        which it kills.
        """
        skill = HANDOFF_SKILL.read_text(encoding="utf-8").splitlines()
        start = next(i for i, ln in enumerate(skill) if "EVERY item MUST carry" in ln)
        tail = "\n".join(ln.strip() for ln in skill[start : start + 4])
        assert "forcing: none" in tail, (
            "the copied boilerplate no longer spells a valid kind, so this test "
            "proves nothing — re-derive the window or drop it"
        )
        text = "## Next steps (ranked)\n1. Fix A.\n2. Fix B.\n\n" + tail + "\n"
        assert self._naive_kinds(text) == [None, "none"], (
            "CONTROL: the rejected boundary must still mis-tag this input, or "
            "the assertion below is vacuous"
        )
        assert [i.kind for i in hd.ranked_items(text)] == [None, None]
        res = run_tool(repo, update=write_delta(tmp_path, "boiler.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr

    def test_a_genuinely_untagged_multi_line_item_still_reports_ABSENCE(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 THE PRECISION HALF. The block widened; the DIAGNOSIS must not. A
        legacy wrapped item using the ordinary English word "forcing" is not an
        attempt at the field, and reporting it as one would replace one wrong
        message with another."""
        text = (
            "## Next steps (ranked)\n"
            "1. **Land the retry-wrapper fix.** The pre-push hook is forcing a\n"
            "   full-tree run on every push, which is the thing to scope.\n"
            "   Files: `githooks/pre-push`, `scripts/lib/git_mainline.py`.\n"
        )
        items = hd.ranked_items(text)
        assert [i.kind for i in items] == [None]
        assert items[0].near_miss is None and items[0].fenced is False
        res = run_tool(repo, update=write_delta(tmp_path, "legacy.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert "[no forcing: field]" in res.stderr
        assert "unparsed" not in res.stderr
        assert "continuation line counts" in res.stderr, (
            "the remedy must state the thing the old one did not: where the "
            "field is allowed to sit"
        )

    def test_the_block_stops_at_the_next_ranked_item(self) -> None:
        """A tag belongs to the item it is written under, and to no other. Item 1
        must NOT borrow item 2's."""
        text = (
            "## Next steps (ranked)\n"
            "1. Fix A.\n"
            "   more about A.\n"
            "2. Fix B.\n"
            "   forcing: user — the operator asked on 2026-08-26\n"
        )
        assert [i.kind for i in hd.ranked_items(text)] == [None, "user"]

    def test_an_indented_paragraph_after_a_blank_line_is_still_the_item(self) -> None:
        """The other side of the boundary. A blank line does not end a list item
        when what follows is indented — that is ordinary markdown, and a handoff
        item with two paragraphs is a shape the corpus has."""
        text = (
            "## Next steps (ranked)\n"
            "1. Fix A.\n"
            "\n"
            "   The second paragraph, still item 1.\n"
            "   forcing: security — the token in the log is live\n"
        )
        assert [i.kind for i in hd.ranked_items(text)] == ["security"]


class TestTheWIDENINGDidNotOpenTwoHOLES:
    """🔴 RED AT `503d7136` — and both defects were INTRODUCED by the round that
    widened rule (j)'s search from the numbered line to the whole item. Delta
    re-audited and reproduced 2026-08-28 before either was touched.

    **The fence erased the boundary's memory.** `_item_blocks` reset its
    "a blank line has intervened" flag on every line inside a fence, so the first
    VISIBLE line after a fence close could never be a boundary. An item with its
    own correctly-INDENTED fence therefore swallowed the section's trailing
    paragraph — the skill's `🔴 **This list is a WORK QUEUE …**` boilerplate,
    which spells `forcing: none` — and `ranked_items` returned `kind='none'`.
    An UNTAGGED item accepted, counted as self-generated, and the gate passed.
    That is the exact counterfactual `_item_blocks`' own docstring cites as its
    reason for rejecting the naive boundary, re-entered through the fence path,
    and it is the ACCEPT direction: nothing downstream refuses it.

    **`\\b` cannot see past an underscore.** `_MARKUP` was widened to
    ``[*_`~]{0,3}`` to admit emphasis, but both patterns anchored the key on
    `\\b{FORCING_KEY}` — and `_` is a word character, so `_forcing: gate_` has no
    boundary to match. Measured at `503d7136`: `**forcing: gate**` parsed to
    `gate`; `_forcing: gate_`, `__forcing: gate__` and `_forcing_: gate` all came
    back `kind=None, near_miss=None`, i.e. `[no forcing: field]` plus a remedy
    the author had already carried out — the unrecoverable refusal, for one of
    markdown's two emphasis characters, in the very spelling class that round set
    out to admit. `_FORCING_ATTEMPT` shared the anchor, so the safety net that
    exists to catch exactly this had the identical hole.

    🔴 THE MATRIX, PER TEST AND MEASURED — deliberately not "every test in this
    class", which would be false here and is exactly the kind of blanket claim a
    reader stops checking. **7 failed, 6 passed at `503d7136`:**

      * `test_a_fence_does_not_erase_the_blank_line_boundary` — RED,
        `assert ['none'] == [None]`.
      * `test_UNDERSCORE_emphasis_parses_like_asterisk_emphasis` — RED on 3 of 4
        params. `` _`forcing: gate`_ `` PASSED: the backtick between the `_` and
        the key hands `\\b` a boundary to match, so that one param is an
        INVARIANT GUARD carried along, not regression coverage.
      * `test_an_UNDERSCORE_emphasised_near_miss_is_NAMED_not_called_absent` —
        RED on all 3 params.
      * `test_an_indented_fence_does_not_cost_the_tag_that_follows_it` — PASSED,
        both params. It is the COST side of the fence fix rather than coverage
        of the defect, and is shown reachable by the `a-blank-line-ends-the-item`
        and `block-collapsed-to-the-numbered-line` mutation rows, which it kills.
      * `test_a_longer_word_around_the_key_is_STILL_not_the_field` — PASSED.
        Labelled an invariant guard in its own docstring.
    """

    @staticmethod
    def _boilerplate_tail() -> str:
        """The skill's own trailing paragraph, READ FROM THE SKILL — the same
        window `test_trailing_boilerplate_does_not_tag_the_last_item` derives,
        and for its reason: a hand-copied constant would quietly stop being the
        text the corpus actually pastes under a ranked list."""
        skill = HANDOFF_SKILL.read_text(encoding="utf-8").splitlines()
        start = next(i for i, ln in enumerate(skill) if "EVERY item MUST carry" in ln)
        tail = "\n".join(ln.strip() for ln in skill[start : start + 4])
        assert "forcing: none" in tail, (
            "the copied boilerplate no longer spells a valid kind, so this test "
            "proves nothing — re-derive the window or drop it"
        )
        return tail

    def test_a_fence_does_not_erase_the_blank_line_boundary(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 THE ACCEPT DIRECTION, which is the one that costs something: the
        gate PASSES on an item nobody tagged.

        Distinct from the col-0-fence gap declared in the last round's report —
        the fence here is INDENTED, i.e. genuinely the item's own, so a rule
        about fences at column 0 does not reach it.
        """
        tail = self._boilerplate_tail()
        text = (
            "## Next steps (ranked)\n"
            "1. Fix A.\n"
            "\n"
            "   ```\n"
            "   ./scripts/measure.py --since 2026-08-01\n"
            "   ```\n" + tail + "\n"
        )
        items = hd.ranked_items(text)
        assert [i.kind for i in items] == [None], (
            "the item carries NO tag — the `none` is boilerplate its author "
            "pasted out of the instructions, being read as a declaration"
        )
        assert items[0].fenced is False and items[0].near_miss is None, (
            "and the diagnosis must stay the plain one: nothing in the item's "
            "own fence looks like a field, and nothing in it is a near-miss"
        )
        res = run_tool(repo, update=write_delta(tmp_path, "fencebound.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert "[no forcing: field]" in res.stderr

    @pytest.mark.parametrize(
        "gap,indent,expected",
        (
            ("", "   ", "regression"),
            ("\n", "   ", "regression"),
            ("", "", None),
            ("\n", "", None),
        ),
        ids=("no-blank-indented", "blank-indented", "no-blank-col0", "blank-col0"),
    )
    def test_an_indented_fence_does_not_cost_the_tag_that_follows_it(
        self, gap: str, indent: str, expected: str | None
    ) -> None:
        """Both sides of the same boundary, measured at FOUR points.

        **INDENTED (the cost side of the fix).** A tag on an indented line after
        the item's own fence still counts, whether or not a blank line sits
        between them — dropping the memory reset must not buy the fix above at
        the price of this shape, which the corpus has. ⚠ PASSED at `503d7136`,
        both params: labelled, not counted as regression coverage. Shown
        reachable by the `a-blank-line-ends-the-item` and
        `block-collapsed-to-the-numbered-line` rows in
        `scripts/tests/mutants-handoff-cap.sh`, which they kill.

        🔴 **COLUMN 0 — the cost the fix DID charge, asserted as DELIBERATE.**
        The `no-blank-col0` param is RED at `503d7136`, where it returned
        `kind='regression'`: the fence used to clear the "a blank has
        intervened" flag, so a col-0 line after it continued the item. Here the
        blank before the fence still counts, that line IS the boundary, and the
        tag is dropped. **This is not a bug to fix on sight.** The walk cannot
        tell an author's col-0 tag from col-0 pasted boilerplate — which is
        exactly what `test_a_fence_does_not_erase_the_blank_line_boundary`
        refuses — and falsely ACCEPTING an untagged item is worse than refusing
        a tagged one. Corpus impact: **0 of 442** ranked items. What makes the
        refusal clearable rather than unrecoverable is `MISSING_FIELD_REMEDY`
        naming the indent, pinned by
        `test_the_missing_field_remedy_tells_a_FLUSH_LEFT_author_to_INDENT`.
        (`blank-col0` was already absent at `503d7136` — an invariant guard
        carried along, not coverage of this change.)"""
        text = (
            "## Next steps (ranked)\n"
            "1. Fix A.\n"
            "\n"
            "   ```\n"
            "   ./scripts/measure.py --since 2026-08-01\n"
            "   ```\n" + gap + indent
            + "forcing: regression — vs the 2026-08-20 baseline\n"
        )
        item = hd.ranked_items(text)[0]
        assert item.kind == expected, (repr(gap), repr(indent))
        if expected is None:
            # The DIAGNOSIS half: a dropped boundary line is not in the item's
            # own lines and not in its fenced lines either, so the row must be
            # the plain one — anything else would be a claim about a line this
            # walk never looked at.
            assert item.near_miss is None and item.fenced is False

    def test_the_missing_field_remedy_tells_a_FLUSH_LEFT_author_to_INDENT(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """🔴 RED at `e34ed6ef`. The remedy said only "A continuation line
        counts — the field does not have to sit on the numbered line", which is
        read as a promise this walk does not keep: the col-0 param above HAS a
        tag on a continuation line and is told it carries none.

        That is the unrecoverable-refusal shape the whole ladder exists to
        remove — the printed fix is already done, so the re-run is byte-
        identical. The boundary is deliberately NOT loosened (see the docstring
        above); naming the INDENT is what makes the refusal clearable instead.

        Shown reachable by the `missing-field-remedy-omits-the-indent` row in
        `scripts/tests/mutants-handoff-cap.sh`."""
        text = (
            "## Next steps (ranked)\n"
            "1. Fix A.\n"
            "\n"
            "   ```\n"
            "   ./scripts/measure.py --since 2026-08-01\n"
            "   ```\n"
            "forcing: regression — vs the 2026-08-20 baseline\n"
        )
        res = run_tool(repo, update=write_delta(tmp_path, "col0tag.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert hd.MARK_NO_FIELD in res.stderr
        norm = " ".join(res.stderr.split())
        assert " ".join(hd.MISSING_FIELD_REMEDY.split()) in norm
        assert "MUST be INDENTED" in norm, (
            "the author HAS written the field on a continuation line and is "
            "being told to write one. Without the indent instruction the fix "
            "the refusal prints is a no-op and the re-run prints identical "
            "bytes — the unrecoverable refusal, re-entered through the fence "
            "boundary."
        )

    #: The whole of `MISSING_FIELD_REMEDY`, normalised.
    #:
    #: 🔴 A WHOLE STRING, DELIBERATELY, and the same discipline as
    #: `TestSkillAndModuleAgree._FENCED_REMEDY`. `claude/RULES.md`: when the
    #: artifact under test IS prose, a guard on WORDS is walkable by REWORDING —
    #: pin the WHOLE normalised string. A cosmetic reword then costs a test edit;
    #: that price is the point, because it is what makes the claim
    #: machine-readable.
    #:
    #: 🔴 A LITERAL, NOT `hd.MISSING_FIELD_REMEDY`. The test above asserts the
    #: constant reaches stderr by comparing the constant against the output that
    #: PRINTED it — self-referential, so it is the BEHAVIOURAL half and any
    #: reword passes it. That is what left this clause unguarded.
    _MISSING_REMEDY = (
        "Tag each item marked [no forcing: field] above. A continuation line "
        "counts — the field does not have to sit on the numbered line, but it "
        "MUST be INDENTED: a flush-left line ENDS the item once a blank has "
        "intervened, so a tag at column 0 below one is outside the item and "
        "reads as absent."
    )

    def test_the_missing_field_remedy_states_the_walks_ACTUAL_boundary(self) -> None:
        """🔴 THE SCOPE, PINNED. At `6a862d8c` this remedy printed the
        unqualified "a flush-left line ENDS the item", which is FALSE: the walk
        breaks on an unindented line only ONCE A BLANK HAS INTERVENED.
        RE-MEASURED 2026-08-28 at `976b09b5`, both points, `ranked_items` on
        `## Next steps (ranked)\\n1. Fix A.\\n` plus a col-0 `forcing: gate`:

            no blank between them -> kind='gate'   (the tag PARSES)
            a blank between them  -> kind=None     (the tag is DROPPED)

        So the unqualified sentence was wrong in the first case, and it
        contradicted write-gate.md, the constant's own `#:` comment and
        SKILL.md, all three of which were already scoped.

        ⚠ NOT regression coverage — round 4 already corrected the wording. This
        is the GUARANTEE that correction never got, labelled as one: the
        clause was revertible with the whole suite green, because the only
        assertion over it was the self-referential one in the test above.

        The BEHAVIOURAL half — that this constant actually reaches an
        executor's stderr rather than sitting unprinted — is
        `test_the_missing_field_remedy_tells_a_FLUSH_LEFT_author_to_INDENT`
        directly above; a literal-only check would type-check past a constant
        nobody prints.

        Shown reachable by the `missing-field-remedy-scope-unqualified` row in
        `scripts/tests/mutants-handoff-cap.sh`."""
        assert " ".join(hd.MISSING_FIELD_REMEDY.split()) == " ".join(
            self._MISSING_REMEDY.split()
        ), (
            "MISSING_FIELD_REMEDY no longer reads as pinned. If the boundary "
            "clause was re-widened to a bare 'a flush-left line ENDS the item', "
            "that is the MEASURED-FALSE wording — a col-0 tag with no blank "
            "before it parses fine. Re-measure before rewording, and update "
            "_MISSING_REMEDY in the SAME commit for a deliberate reword."
        )

    @pytest.mark.parametrize(
        "tagline",
        (
            "   _forcing: gate_ — CI red",
            "   __forcing: gate__ — CI red",
            "   _forcing_: gate — CI red",
            "   _`forcing: gate`_ — CI red",
        ),
    )
    def test_UNDERSCORE_emphasis_parses_like_asterisk_emphasis(
        self, repo: Path, tmp_path: Path, tagline: str
    ) -> None:
        """`*` and `_` are the SAME markdown construct. Admitting one and
        refusing the other with `[no forcing: field]` is a refusal over which of
        two interchangeable characters the author's editor inserted — and it is
        unrecoverable, because the remedy it prints is already done.

        ⚠ The last param passed at `503d7136` — see the class docstring's
        matrix. It is kept as an invariant guard, not counted as coverage."""
        text = "## Next steps (ranked)\n1. Land the retry-wrapper fix.\n" + tagline + "\n"
        assert [i.kind for i in hd.ranked_items(text)] == ["gate"]
        res = run_tool(repo, update=write_delta(tmp_path, "underscore.md", text))
        assert res.returncode == 0, res.stdout + res.stderr
        assert "status=unforced" not in res.stderr

    @pytest.mark.parametrize(
        "attempt",
        (
            "_forcing = gate_",
            "_forcing function: gate_",
            "__forcing — gate__",
        ),
    )
    def test_an_UNDERSCORE_emphasised_near_miss_is_NAMED_not_called_absent(
        self, repo: Path, tmp_path: Path, attempt: str
    ) -> None:
        """🔴 THE SAFETY NET'S OWN HOLE. `_FORCING_ATTEMPT` shared the broken
        anchor, so an emphasised near-miss fell all the way through to
        `[no forcing: field]` — the one refusal a re-run cannot clear — for a
        spelling the skill now actively tells authors is fine."""
        text = "## Next steps (ranked)\n1. Land the fix.\n   " + attempt + "\n"
        res = run_tool(repo, update=write_delta(tmp_path, "usnearmiss.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert "[no forcing: field]" not in res.stderr, (
            "the item HAS a field — telling it there is none is the reported bug"
        )
        assert "Tag each item marked" not in res.stderr
        assert "[unparsed forcing field on:" in res.stderr
        assert attempt in res.stderr, "quote the offending line verbatim"

    @pytest.mark.parametrize(
        "line",
        (
            "   The pre-push hook is enforcing: gate-scoped runs, which is fine.",
            "   reinforcing: gate — a longer word merely ENDING with the key",
            "   forcings: gate — plural, so not the key either",
        ),
    )
    def test_a_longer_word_around_the_key_is_STILL_not_the_field(
        self, line: str
    ) -> None:
        """INVARIANT GUARD — labelled, not counted as regression coverage: this
        passes at `503d7136` too. It pins the ONE job `\\b` was doing, so the
        lookbehind that replaced it cannot buy underscore emphasis by admitting
        `enforcing:` — in BOTH patterns, or the near-miss net starts quoting
        ordinary prose back at an author as an unparsed field."""
        text = "## Next steps (ranked)\n1. Land the fix.\n" + line + "\n"
        item = hd.ranked_items(text)[0]
        assert item.kind is None, f"{line!r} was accepted as the field"
        assert item.near_miss is None, f"{line!r} was reported as a near-miss"

    @pytest.mark.parametrize(
        "line",
        (
            "   forcing = mygate — a longer word merely ENDING with a kind",
            "   forcing — theincident, which is not the vocabulary word",
            "   forcing = wontregression, nor is that one",
        ),
    )
    def test_a_longer_word_around_the_KIND_is_not_a_near_miss_either(
        self, line: str
    ) -> None:
        """🔴 THE LEADING ANCHOR ON THE **KIND**, WHICH NOTHING DISTINGUISHED.

        `_FORCING_ATTEMPT` anchors both ends of both tokens, and the module's
        comment claims exactly that — but the only fixture exercising the kind's
        anchors was `_forcing = gate_`, whose `gate` is at the END of the line,
        so it can only see the TRAILING one. MEASURED at `e34ed6ef`: DELETING
        the LEADING `(?<![A-Za-z0-9])` before the kind alternation makes
        `forcing = mygate` a near-miss, and the full suite stayed green — 237
        passed against the `e34ed6ef` test file. Three of four anchors were
        shown reachable; this is the fourth.

        The stake is not cosmetic: `_FORCING_ATTEMPT` QUOTES the line it matched
        back at the author as "an unparsed forcing field", so a leaky kind
        anchor means ordinary prose containing a vocabulary word inside a longer
        word gets read back as a malformed tag.

        ⚠ INVARIANT GUARD, not regression coverage — green at `503d7136` too
        (`\\b` excluded these as well). It is shown reachable by the
        `near-miss-kind-LEADING-anchor-DELETED` row in
        `scripts/tests/mutants-handoff-cap.sh`. 🔴 It is deliberately NOT the
        killer for `near-miss-kind-LEADING-anchor-on-word-boundary`: MEASURED,
        `\\b` excludes `mygate` exactly as the lookbehind does, so that row needs
        the OPPOSITE direction, which
        `test_an_EMPHASISED_KIND_is_still_seen_by_the_near_miss_net` supplies.
        Two failure directions, two rows, two fixtures — the first draft of this
        pair pointed the `\\b` row at THIS test and the battery reported it
        SURVIVED, which is how the distinction was found."""
        text = "## Next steps (ranked)\n1. Land the fix.\n" + line + "\n"
        item = hd.ranked_items(text)[0]
        assert item.kind is None, f"{line!r} was accepted as the field"
        assert item.near_miss is None, f"{line!r} was reported as a near-miss"

    @pytest.mark.parametrize(
        "attempt", ("forcing = _gate — CI red", "forcing — _incident")
    )
    def test_an_EMPHASISED_KIND_is_still_seen_by_the_near_miss_net(
        self, repo: Path, tmp_path: Path, attempt: str
    ) -> None:
        """🔴 THE OTHER DIRECTION ON THE SAME ANCHOR, AND THE ONE THAT ISOLATES
        IT. The round that replaced `\\b` with `(?<![A-Za-z0-9])` did it on the
        KEY *and* the KIND for one reason — `_` is a word character — but every
        fixture put the emphasis around the KEY (`_forcing = gate_`), where the
        kind's LEADING anchor is never exercised: `gate` there is preceded by a
        space, which `\\b` handles fine.

        This puts the emphasis on the KIND instead. MEASURED at `e34ed6ef`
        across all three one-anchor mutants: `forcing = _gate` is a near-miss at
        HEAD, is NOT one when the kind's leading anchor becomes `\\b`, and IS
        still one when the TRAILING anchor becomes `\\b` — so it separates the
        two ends, which `_forcing = gate_` (killed only by the trailing row)
        cannot.

        ⚠ INVARIANT GUARD, not regression coverage: this is current behaviour,
        red only against the mutant. Shown reachable by the
        `near-miss-kind-LEADING-anchor-on-word-boundary` row in
        `scripts/tests/mutants-handoff-cap.sh`."""
        text = "## Next steps (ranked)\n1. Land the fix.\n   " + attempt + "\n"
        item = hd.ranked_items(text)[0]
        assert item.kind is None, f"{attempt!r} was accepted as the field"
        assert item.near_miss is not None, (
            f"{attempt!r} fell through to [no forcing: field] — the refusal a "
            f"re-run cannot clear, for an emphasised kind"
        )
        res = run_tool(repo, update=write_delta(tmp_path, "emphkind.md", text))
        assert res.returncode == hd.EXIT_UNFORCED, res.stdout + res.stderr
        assert hd.MARK_NO_FIELD not in res.stderr
        assert attempt in res.stderr, "quote the offending line verbatim"

    @pytest.mark.parametrize(
        "line,kind,near_miss",
        (
            # P1 — `_FORCING`'s key, LEADING. Parses to a kind; the rest are
            # `_FORCING_ATTEMPT`'s, which can only ever produce a near-miss.
            ("   some_forcing: none", "none", False),
            ("   éforcing: gate", "gate", False),
            ("   強forcing: gate", "gate", False),
            # P2 — `_FORCING_ATTEMPT`'s key, LEADING. No colon, so `_FORCING`
            # cannot match and this position is isolated from P1.
            ("   my_forcing = gate", None, True),
            ("   éforcing = gate", None, True),
            # P3 — `_FORCING_ATTEMPT`'s key, TRAILING.
            ("   the forcing_fn returns none", None, True),
            ("   the forcingé returns none", None, True),
            ("   the forcing強 returns none", None, True),
            # P4 — that pattern's KIND, LEADING.
            ("   forcing = _gate", None, True),
            ("   forcing = égate", None, True),
            # P5 — that pattern's KIND, TRAILING.
            ("   forcing the user_id column", None, True),
            ("   forcing the gate_keeper to retry", None, True),
            ("   forcing = gateé", None, True),
        ),
        ids=("P1-key-leading-underscore", "P1-key-leading-latin1",
             "P1-key-leading-cjk",
             "P2-attempt-key-leading-underscore", "P2-attempt-key-leading-latin1",
             "P3-attempt-key-trailing-underscore",
             "P3-attempt-key-trailing-latin1", "P3-attempt-key-trailing-cjk",
             "P4-attempt-kind-leading-underscore",
             "P4-attempt-kind-leading-latin1",
             "P5-attempt-kind-trailing-underscore",
             "P5-attempt-kind-trailing-underscore-2",
             "P5-attempt-kind-trailing-latin1"),
    )
    def test_the_widened_anchors_admit_these_and_the_comment_says_so(
        self, line: str, kind: str | None, near_miss: bool
    ) -> None:
        """🔴 THE COMMENT'S CLAIM, MADE MACHINE-READABLE — AND THE PARAMS ARE A
        GRID, BECAUSE AN ENUMERATION OF EXAMPLES UNDERCOUNTED IT TWICE.

        `_FORCING`'s comment first named *only* "a snake_case identifier ending
        in `_forcing`"; a delta audit found three more and the comment was
        rewritten to "FOUR ADMISSIONS, NOT ONE"; a second delta audit found six
        more still. The reason is structural: the widening replaced `\\b` with a
        `[A-Za-z0-9]` lookaround at FIVE positions, and `\\b` differs from it for
        TWO character classes — `_`, and any non-ASCII word character, since
        Python's `\\w` is unicode. That is a 5x2 GRID, so the params enumerate
        all ten cells rather than the examples anyone happened to notice:

          P1 `_FORCING`'s key, LEADING            P2 `_FORCING_ATTEMPT`'s, LEADING
          P3 `_FORCING_ATTEMPT`'s key, TRAILING   P4 its KIND, LEADING
          P5 its KIND, TRAILING

        There is no P6: `_FORCING`'s own KIND, `([A-Za-z-]+)`, carries no
        trailing lookaround. MEASURED 2026-08-28, all ten cells alike — admitted
        at HEAD, and NO match under the old `\\b` spelling of the same pattern.

        Each is bounded by the same closed-vocabulary argument as the markup
        class, and each occurs 0 times over both corpora, so none is being
        fixed. They are pinned so the comment stops being the only record —
        `claude/RULES.md`: a comment is a claim too.

        ⚠ NOT regression coverage: this asserts CURRENT behaviour. Its evidence
        is the base measurement above, and it is shown reachable by the four
        anchor rows in `scripts/tests/mutants-handoff-cap.sh`."""
        text = "## Next steps (ranked)\n1. Land the fix.\n" + line + "\n"
        item = hd.ranked_items(text)[0]
        assert item.kind == kind, f"{line!r} -> {item.kind!r}"
        assert (item.near_miss is not None) is near_miss, (
            f"{line!r} -> near_miss={item.near_miss!r}"
        )

    def test_the_comment_still_states_the_ASCII_scope(self) -> None:
        """The half above cannot see: the module's prose must SAY the exclusion
        is ASCII-only, because a maintainer reading "the character before the
        key is a letter" would conclude `éforcing:` is excluded and it is not.

        A phrase pin, and a weak one by construction — the behaviour is pinned
        by the params above; this only keeps the comment from re-narrowing.

        🔴 NO COUNT IS PINNED HERE ANY MORE, AND THAT IS THE FIX. This used to
        assert the literal "FOUR ADMISSIONS, NOT ONE" — a guard machine-
        enforcing a number that a later delta audit measured wrong (ten cells,
        not four). A count in prose goes stale the moment a position or a
        character class is added; the params above are the census, and what the
        comment owes is the SHAPE — that the scope is stated by POSITION rather
        than by a list of examples, which is the failure mode that undercounted
        it twice.

        Shown reachable by the `ascii-scope-narrowed-off-every-position`,
        `admissions-restated-as-a-list` and `retired-count-back-in-the-comment`
        rows in `scripts/tests/mutants-handoff-cap.sh` — one per assertion,
        because a mutant that gutted the paragraph would die to whichever
        assertion runs first and prove nothing about the other two.

        ⚠ `assert "an ASCII letter" in src` has NO row of its own. It is the
        antecedent the `AT EVERY POSITION` clause qualifies, and that row moves
        the clause rather than this phrase; it is carried, not shown reachable."""
        src = TOOL.read_text(encoding="utf-8")
        assert "an ASCII letter" in src
        assert "AT EVERY POSITION" in src
        assert "THE ADMISSIONS ARE A GRID, NOT A LIST" in src
        assert "FOUR ADMISSIONS, NOT ONE" not in src, (
            "the retired count is back in the comment; it was measured wrong "
            "(ten cells, not four) and pinning it re-enforces the error"
        )


class TestRulesIAndJDidNotMoveTheOtherExits:
    """INVARIANT GUARDS — not regression coverage, and labelled so.

    The new refusals sit BEFORE rules (d) and (h) in `main()`, which is exactly
    the shape that silently steals another rule's exit code.
    """

    def test_no_advance_is_still_4_on_an_undated_existing_doc(
        self, repo: Path, update_file: Path
    ) -> None:
        res = run_tool(repo, update=update_file, advanced="nothing")
        assert res.returncode == hd.EXIT_NO_ADVANCE, res.stdout + res.stderr

    def test_no_change_is_still_5(self, repo: Path, tmp_path: Path) -> None:
        noop = write_delta(
            tmp_path, "noop2.md",
            "## Goal\nMake the sample subsystem stop dropping work under load.\n",
        )
        res = run_tool(repo, update=noop)
        assert res.returncode == hd.EXIT_NO_CHANGE, res.stdout + res.stderr

    def test_the_new_codes_do_not_collide_with_the_old(self) -> None:
        codes = [
            hd.EXIT_OK, hd.EXIT_USAGE, hd.EXIT_FAIL, hd.EXIT_NO_ADVANCE,
            hd.EXIT_NO_CHANGE, hd.EXIT_BEHIND, hd.EXIT_DOC_PER_EFFORT,
            hd.EXIT_UNFORCED,
        ]
        assert len(set(codes)) == len(codes), f"exit codes collide: {codes}"

    def test_none_is_in_the_vocabulary_but_not_in_the_external_set(self) -> None:
        """`EXTERNAL_FORCING_KINDS` is DERIVED. If it ever became a second
        literal list, a kind added to one and not the other would silently
        un-count items — the N-sites-wrong-at-N-1 shape."""
        assert "none" in hd.FORCING_KINDS
        assert "none" not in hd.EXTERNAL_FORCING_KINDS
        assert hd.EXTERNAL_FORCING_KINDS < hd.FORCING_KINDS

def repo_lacking_the_doc(tmp_path: Path) -> Path:
    """A checkout whose mainline HAS the handoff doc and whose HEAD does not.

    🔴 This is the talos-infra shape, measured 2026-08-29: the doc was authored
    and pushed from a WORKTREE, the primary clone was never re-synced, and the
    doc therefore exists at `origin/main` and nowhere in the working tree. It is
    NOT the new-doc case — a genuinely new doc is absent on BOTH sides.
    """
    origin = tmp_path / "origin.git"
    _sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)

    work = tmp_path / "work"
    work.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=work)
    for k, v in (("user.name", "Test Runner"),
                 ("user.email", "test@example.invalid"),
                 ("commit.gpgsign", "false")):
        _sh("git", "config", k, v, cwd=work)
    _sh("git", "remote", "add", "origin", str(origin), cwd=work)
    (work / "README.md").write_text("sample\n", encoding="utf-8")
    _sh("git", "add", "--", "README.md", cwd=work)
    _sh("git", "commit", "-q", "-m", "seed without the doc", cwd=work)
    _sh("git", "push", "-q", "origin", "main", cwd=work)

    # The other worktree authors and pushes the doc. `work` never merges it.
    other = tmp_path / "other"
    _sh("git", "clone", "-q", str(origin), str(other), cwd=tmp_path)
    for k, v in (("user.name", "Other"), ("user.email", "o@example.invalid"),
                 ("commit.gpgsign", "false")):
        _sh("git", "config", k, v, cwd=other)
    (other / "claudedocs").mkdir()
    (other / "claudedocs" / "handoff-sample-topic.md").write_text(
        BASE_DOC, encoding="utf-8")
    _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=other)
    _sh("git", "commit", "-q", "-m", "the real handoff, authored elsewhere", cwd=other)
    _sh("git", "push", "-q", "origin", "main", cwd=other)

    # Fetched, deliberately not merged — exactly the state the tool must judge.
    _sh("git", "fetch", "-q", "origin", cwd=work)
    return work


class TestAbsentBasePresentOnMainlineIsRefused:
    """🔴 The doc is absent HERE and present on mainline ⇒ every section merges
    as NEW and the committed document is REPLACED by the delta.

    The tool already DETECTED this and printed it; it exited 0 anyway. That was
    survivable while a human answered a y/N, but that prompt was retired
    2026-08-23, so the warning became the only thing between the diff and a
    pushed commit — against this skill's own rule that blast radius earns a
    REFUSAL, not a question. `--push` does not cover it: the `behind` check asks
    about `<remote>/<push-branch>`, a DIFFERENT ref from the mainline this
    compares against, so a current feature branch sails past it.
    """

    def test_it_REFUSES_and_writes_nothing(self, tmp_path: Path,
                                           update_file: Path) -> None:
        work = repo_lacking_the_doc(tmp_path)
        shas_before = commit_shas(work)
        res = run_tool(work, "--confirm", update=update_file)
        assert res.returncode == hd.EXIT_STALE_BASE, (
            res.returncode, res.stdout, res.stderr)
        assert "status=stale-base" in res.stderr, res.stderr
        assert not (work / "claudedocs" / "handoff-sample-topic.md").exists(), (
            "the doc was written despite the refusal")
        assert commit_shas(work) == shas_before, "a commit was made anyway"

    def test_the_refusal_names_the_remedy(self, tmp_path: Path,
                                          update_file: Path) -> None:
        """A refusal a caller cannot act on gets worked around."""
        work = repo_lacking_the_doc(tmp_path)
        res = run_tool(work, "--confirm", update=update_file)
        blob = res.stdout + res.stderr
        assert "origin/main" in blob
        assert "claudedocs/handoff-sample-topic.md" in blob
        assert "--allow-replacing-mainline-doc" in blob, (
            "the override must be named, or the refusal is a dead end")

    def test_the_explicit_override_still_lets_it_through(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 NEGATIVE CONTROL. A refusal with no way past it would make a
        legitimate re-author impossible and train everyone to route around it."""
        work = repo_lacking_the_doc(tmp_path)
        res = run_tool(work, "--confirm", "--allow-replacing-mainline-doc",
                       update=update_file)
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert "status=written" in res.stdout
        assert (work / "claudedocs" / "handoff-sample-topic.md").exists()

    def test_a_GENUINELY_NEW_doc_is_untouched_by_this(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 THE CASE THIS MUST NOT BREAK. Absent on BOTH sides is the new-doc
        path the skill says step 5 owns. Refusing it would make first writes
        impossible — the failure mode that matters more than the one being fixed.
        """
        work = repo_lacking_the_doc(tmp_path)
        # Same clone, a topic that exists nowhere — mainline included.
        argv = [sys.executable, str(TOOL), "--repo", str(work),
                "--topic", "brand-new-topic", "--update", str(update_file),
                "--advanced", "first write", "--confirm"]
        res = subprocess.run(argv, capture_output=True, text=True,
                             env=dict(os.environ, **GIT_ENV))
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert "status=written" in res.stdout
        assert (work / "claudedocs" / "handoff-brand-new-topic.md").exists()

    def test_a_doc_PRESENT_locally_but_behind_stays_a_WARNING(
        self, repo: Path, update_file: Path, tmp_path: Path
    ) -> None:
        """🔴 THE OTHER CASE THIS MUST NOT BREAK. When the doc exists here, the
        merge can still classify its sections, so updating a deliberately-behind
        clone stays legitimate — a warning, exit 0, as the tool already says.

        🔴 THE REMOTE COMMIT MUST TOUCH THE DOC ITSELF. An earlier version of
        this test used `advance_remote`, which pushes an unrelated `OTHER.md`:
        `doc_behind` stayed 0, so `mainline` was never read and the case the
        assertion names could not arise. MEASURED — the mutant that drops the
        `not base_text` half of the predicate SURVIVED against that fixture,
        because the fixture could not produce a doc-behind reading at all. The
        test read as coverage while providing none.
        """
        other = tmp_path / "doc-mover"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other),
            cwd=tmp_path)
        for k, v in (("user.name", "Other"), ("user.email", "o@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        moved = other / "claudedocs" / "handoff-sample-topic.md"
        moved.write_text(BASE_DOC + "\n## Added elsewhere\n\nlater work.\n",
                         encoding="utf-8")
        _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "advance the DOC itself", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        _sh("git", "fetch", "-q", "origin", cwd=repo)

        # The precondition the old fixture silently lacked: the mainline really
        # is ahead ON THIS PATH, so `mainline` is populated and the predicate's
        # two halves are actually distinguishable.
        behind = _sh("git", "rev-list", "--count", "HEAD..origin/main", "--",
                     "claudedocs/handoff-sample-topic.md", cwd=repo)
        assert behind.strip() != "0", (
            "fixture is vacuous: the mainline is not ahead on the doc path, so "
            "this test cannot tell the two halves of the predicate apart")

        res = run_tool(repo, "--confirm", update=update_file)
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert "status=stale-base" not in res.stderr
        assert "status=written" in res.stdout

    @pytest.mark.parametrize(
        "blank,label",
        [("", "empty"), ("\n", "one newline"), ("   \n\n", "whitespace")],
    )
    def test_a_BLANK_local_doc_is_as_absent_as_a_MISSING_one(
        self, tmp_path: Path, update_file: Path, blank: str, label: str
    ) -> None:
        """🔴 A bare falsiness test let a single newline WALK this guard.

        MEASURED before the fix, against a mainline doc of 6 sections: `""`
        refused (rc 7), while `"\\n"` and `"   \\n\\n"` both exited 0 and REPLACED
        the committed document with the delta. The merge treats all three the
        same — 0 sections, so every section arrives NEW — so the destructive
        outcome is identical and only the predicate disagreed. `wrong_base_tells`
        already used `.strip()` 74 lines earlier in the same file.
        """
        work = repo_lacking_the_doc(tmp_path)
        doc = work / "claudedocs" / "handoff-sample-topic.md"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text(blank, encoding="utf-8")

        res = run_tool(work, "--confirm", update=update_file)
        assert res.returncode == hd.EXIT_STALE_BASE, (
            f"a {label} local doc walked the guard", res.stdout, res.stderr)
        assert doc.read_text(encoding="utf-8") == blank, (
            "the committed document was replaced by the delta")

    def test_the_PROPOSAL_run_still_shows_the_diff_and_does_not_refuse(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 THE `args.confirm` HALF OF THE GUARD, which nothing else pins.

        An adversarial mutation that drops `and args.confirm` SURVIVED the whole
        176-test file: the refusal then fires on the PROPOSAL run too, exiting 7
        before the diff is printed. `reference/write-gate.md` calls that diff
        "the only record of what landed", and the proposal run exists to put it
        in the transcript — so refusing there destroys the thing the two-run
        shape is for, while writing nothing and looking like a working guard.

        The proposal run must WARN and show the diff; only `--confirm` refuses.
        """
        work = repo_lacking_the_doc(tmp_path)
        res = run_tool(work, update=update_file)  # no --confirm
        assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
        assert "status=proposed" in res.stdout
        assert "status=stale-base" not in res.stderr
        assert "THE BASE DOCUMENT IS NOT THE NEWEST COMMITTED COPY" in res.stdout, (
            "the proposal run must still carry the warning")
        # 🔴 A `+`-prefixed line FROM THE UPDATE BODY, not merely "+" anywhere.
        # `unified()` always emits a `+++` header, so `"+" in res.stdout` was
        # satisfied by the header alone: MEASURED, a mutant that stripped every
        # +/- body line from the printed diff left that assertion green. The
        # message claimed the diff reached the transcript and did not check it.
        body = [l for l in res.stdout.splitlines()
                if l.startswith("+") and not l.startswith("+++")]
        assert body, "the diff's added lines must reach the transcript"

    def test_the_override_is_NOT_reachable_by_abbreviation(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 The flag's help calls it "deliberately long" so it cannot be passed
        by reflex — but argparse abbreviates long options by DEFAULT, and `--al`
        was measured to be accepted and to replace the committed document. A
        guard spelled as a long name is walkable by shortening it unless
        `allow_abbrev=False` says otherwise.
        """
        work = repo_lacking_the_doc(tmp_path)
        for abbrev in ("--al", "--allow", "--allow-replacing"):
            res = run_tool(work, "--confirm", abbrev, update=update_file)
            assert res.returncode == hd.EXIT_USAGE, (
                f"{abbrev} was accepted as the override", res.stdout, res.stderr)
            assert not (work / "claudedocs" / "handoff-sample-topic.md").exists()

    @pytest.mark.parametrize("blank,label", [("", "empty"), ("\n", "one newline")])
    def test_the_WARNING_and_the_REFUSAL_describe_the_SAME_set(
        self, tmp_path: Path, update_file: Path, blank: str, label: str
    ) -> None:
        """🔴 THE SEAM. Two consumers of one fact, and they drifted for a round.

        The refusal decides whether to stop; the warning decides whether to print
        the loud "the committed document will be replaced" line or a mild size
        comparison. When the refusal was widened to `.strip()` the warning kept
        `if not local.lines:` — true only for a strictly 0-line file — so a
        `"\\n"` base got `0 sections / 1 lines` and never the loud line, in
        exactly the shape where every section merges as NEW. They had been
        equivalent before the widening, which is why the drift was silent.

        So this pins the RELATIONSHIP, on the PROPOSAL run — the one an operator
        reads before deciding — not either half alone.
        """
        work = repo_lacking_the_doc(tmp_path)
        doc = work / "claudedocs" / "handoff-sample-topic.md"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text(blank, encoding="utf-8")

        proposal = run_tool(work, update=update_file)  # no --confirm
        assert proposal.returncode == 0, proposal.stderr
        assert "will be replaced by this delta" in proposal.stdout, (
            f"a {label} base got the MILD warning in the shape that replaces the "
            f"document: {proposal.stdout[-400:]}")

        confirmed = run_tool(work, "--confirm", update=update_file)
        assert confirmed.returncode == hd.EXIT_STALE_BASE, (
            "the warning said REPLACED but the refusal did not fire — the two "
            "halves disagree", confirmed.stdout, confirmed.stderr)

    def test_the_remedy_does_not_tell_a_PROPOSAL_reader_they_are_safe(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 The remedy block once said "if you do not see `status=stale-base`,
        this run is the warning". The refusal is gated on `--confirm`, so a
        proposal run can NEVER print that line — the sentence told every reader
        of the run they use to decide that they were in the benign case,
        including the ones about to destroy a document.
        """
        work = repo_lacking_the_doc(tmp_path)
        out = run_tool(work, update=update_file).stdout
        assert "will be replaced by this delta" in out
        assert "If you do not see that line, this run is the warning" not in out
        assert "ONLY on `--confirm`" in out, (
            "the remedy must say the refusal is deferred, not absent")

    def test_a_doc_DELETED_on_the_mainline_gets_NEITHER_the_loud_line_nor_a_refusal(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 THE OTHER ARM OF THE SEAM — the one two rounds of fixes missed.

        `nothing_to_merge_into` is true whenever there is no usable base HERE.
        The refusal additionally needs a mainline copy to LOSE. The set
        difference is real: the mainline's commits to this doc DELETED it (a
        retirement, a revert, a rename), so this checkout never had it and
        neither does the mainline.

        MEASURED at the round-2 tip: the warning fired on the blank half alone
        and printed "and <ref> has one … will be replaced by this delta" about a
        document that does not exist, while `--confirm` exited 0 and wrote — and
        the remedy told the operator that confirming would refuse.

        So: no loud replacement claim, and no refusal. A first write here is
        legitimate and must land.
        """
        work = repo_lacking_the_doc(tmp_path)
        # Delete it on the mainline, so `doc_behind` is non-zero but `git show`
        # finds nothing — the shape that populates `mainline` with None.
        other = tmp_path / "deleter"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        for k, v in (("user.name", "D"), ("user.email", "d@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        _sh("git", "rm", "-q", "--", "claudedocs/handoff-sample-topic.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "retire the handoff", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        _sh("git", "fetch", "-q", "origin", cwd=work)

        proposal = run_tool(work, update=update_file)
        assert proposal.returncode == 0, proposal.stderr
        # 🔴 ASSERT THE PRECONDITION, not only the two negatives. Every other
        # assertion here is satisfied by "the currency check never ran" —
        # MEASURED with a mutant that returns an UNMEASURED BaseCurrency when
        # `git show` fails: the run then printed NOTHING AT ALL and this test
        # stayed green, along with the whole 186-test file. The negatives are
        # only meaningful once the stale path is known to have executed.
        assert "THE BASE DOCUMENT IS NOT THE NEWEST COMMITTED COPY" in proposal.stdout, (
            "the stale path never ran, so the negatives below prove nothing")
        assert "will be replaced by this delta" not in proposal.stdout, (
            "claimed a replacement of a document the mainline does not have")

        confirmed = run_tool(work, "--confirm", update=update_file)
        assert confirmed.returncode == 0, (
            "a legitimate first write was refused", confirmed.stdout, confirmed.stderr)
        assert (work / "claudedocs" / "handoff-sample-topic.md").exists()

    def test_the_remedy_is_pinned_as_a_WHOLE_normalised_string(self) -> None:
        """🔴 A guard on WORDS is walkable by REWORDING. The previous version
        asserted the presence of "ONLY on `--confirm`" and the absence of one
        retired sentence — both satisfied by a reword that reintroduces the exact
        false meaning ("…refuses ONLY on --confirm. If no status=stale-base
        appears, you are in the benign case."). `WRONG_BASE_REMEDY` is a module
        constant, so the whole normalised string can be pinned. A cosmetic reword
        then fails this test — that is the price of a machine-readable claim.
        """
        expected = (
            "Settle it BEFORE confirming: read the mainline copy — `git -C "
            "{repo} show {ref}:{relpath}` — and re-run against a current clone "
            "if it is the fuller document. Updating a deliberately-behind clone "
            "is legitimate, so on its own this is a WARNING and no exit code "
            "changed. It is a FLOOR: a silent run is NOT evidence that the base "
            "is current. 🔴 ONE shape refuses instead — no usable doc here while "
            "the mainline has one — and it refuses ONLY on `--confirm`, as "
            "`status=stale-base` (exit 9). A proposal run therefore NEVER prints "
            "that line whatever shape it is in, so its absence here is not "
            "evidence you are in the benign case: read the line above instead, "
            "which fires on exactly the shape that refuses."
        )
        assert " ".join(hd.WRONG_BASE_REMEDY.split()) == " ".join(expected.split())

    @pytest.mark.parametrize("blank", ["", "\n", "   \n\n"])
    def test_wrong_base_tells_shares_the_predicate_and_stays_SILENT_on_a_blank_base(
        self, blank: str
    ) -> None:
        """🔴 GUARDS THE CONSOLIDATION ITSELF, which shipped unguarded.

        `wrong_base_tells` used to open-code `not base_text.strip()`; it now
        calls `nothing_to_merge_into`. MEASURED: mutating that call back to a
        bare `not base_text` SURVIVED the whole 186-test file — no mutant existed
        for the line the commit had just changed.

        The consequence is not cosmetic. With the bare test, a first write into a
        newline-only file raises a tell, so the operator gets the whole
        "THIS MERGE LOOKS LIKE IT RESOLVED THE WRONG BASE" block on a run where
        nothing is wrong. Measured: 0 tells at HEAD, 1 tell under the mutant.
        """
        assert hd.wrong_base_tells(blank, UPDATE_DOC, {}) == ()

    def test_an_EMPTY_mainline_doc_is_NOT_something_to_lose(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 THE MIRROR BUG: refusing where NOTHING is destroyed.

        `self.mainline is not None` is not "there is a copy to lose" — a
        committed but EMPTY mainline doc parses to `DocShape(0, 0, …)`, which is
        not None. MEASURED before the fix: an empty committed mainline copy plus
        no local doc exited 7 `NOTHING WRITTEN` and printed "and <ref> has one
        (0 section(s) / 0 line(s))" — a self-contradicting sentence — blocking a
        legitimate first write. Replacing nothing with something costs nothing,
        so it must not refuse.
        """
        work = repo_lacking_the_doc(tmp_path)
        other = tmp_path / "emptier"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        for k, v in (("user.name", "E"), ("user.email", "e@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        (other / "claudedocs" / "handoff-sample-topic.md").write_text("", encoding="utf-8")
        _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=other)
        _sh("git", "commit", "-q", "--allow-empty", "-m", "empty the handoff", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        _sh("git", "fetch", "-q", "origin", cwd=work)

        res = run_tool(work, "--confirm", update=update_file)
        assert res.returncode == 0, (
            "refused a first write where the mainline copy is EMPTY — nothing "
            "would have been destroyed", res.stdout, res.stderr)
        assert "status=stale-base" not in res.stderr
        assert (work / "claudedocs" / "handoff-sample-topic.md").exists()

    def _mainline_doc(self, tmp_path: Path, work: Path, body: str, msg: str) -> None:
        """Commit `body` as the mainline copy of the handoff doc, then fetch."""
        other = tmp_path / f"ml-{abs(hash(body)) % 10**8}"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other), cwd=tmp_path)
        for k, v in (("user.name", "M"), ("user.email", "m@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        d = other / "claudedocs"
        d.mkdir(exist_ok=True)
        (d / "handoff-sample-topic.md").write_text(body, encoding="utf-8")
        _sh("git", "add", "--", "claudedocs/handoff-sample-topic.md", cwd=other)
        # 🔴 NO `--allow-empty`. Git refusing "nothing to commit" is what
        # catches a fixture body identical to what the mainline already
        # holds; suppressing it would let a test go silently vacuous.
        _sh("git", "commit", "-q", "-m", msg, cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        _sh("git", "fetch", "-q", "origin", cwd=work)

    @pytest.mark.parametrize("blank", ["\n", "   \n\n"])
    def test_a_WHITESPACE_mainline_doc_is_ALSO_not_something_to_lose(
        self, tmp_path: Path, update_file: Path, blank: str
    ) -> None:
        """🔴 The MIRROR bug, one notch along — round 4 fixed only `""`.

        `bool(mainline.lines)` called a whitespace-only mainline "a document to
        lose": `doc_shape("\\n")` is `DocShape(0, 1)`. MEASURED at that tip, the
        refusal printed — in ONE sentence — that whitespace is equivalent to
        absent for the LOCAL copy, and that a 1-line whitespace MAINLINE copy is
        "one" that would be destroyed. The local side went through
        `nothing_to_merge_into`; the mainline side was a bare falsiness test on
        a line count, so the two halves of one equivalence class disagreed.
        """
        work = repo_lacking_the_doc(tmp_path)
        self._mainline_doc(tmp_path, work, blank, "whitespace the handoff")

        # 🔴 ASSERT THE MECHANISM. Every assertion below is satisfied by "the
        # mainline copy was never read at all" — MEASURED, a mutant making
        # `base_currency` skip the `git show` entirely SURVIVES this test when
        # run alone, while the sibling headingless test dies to it. The stale
        # HEADER is not enough either: it prints whenever `doc_behind` is
        # non-zero. Only the mainline SHAPE line proves the copy was read and
        # measured, which is the claim this test's name makes.
        prop = run_tool(work, update=update_file)
        assert "origin/main: 0 sections /" in prop.stdout, (
            "the mainline copy was never read, so the negatives below prove "
            "nothing", prop.stdout[-400:])
        assert "will be replaced by this delta" not in prop.stdout

        res = run_tool(work, "--confirm", update=update_file)
        assert res.returncode == 0, (
            "refused a first write against a WHITESPACE mainline copy — nothing "
            "would have been destroyed", res.stdout, res.stderr)
        assert "status=stale-base" not in res.stderr
        assert (work / "claudedocs" / "handoff-sample-topic.md").exists()

    def test_a_HEADINGLESS_prose_mainline_doc_IS_something_to_lose(
        self, tmp_path: Path, update_file: Path
    ) -> None:
        """🔴 PINS THE QUANTITY, and it is the fail-OPEN direction.

        MEASURED: swapping `bool(mainline.lines)` for `bool(mainline.sections)`
        SURVIVED the whole 190-test file. A real prose handoff carrying no `##`
        heading is `DocShape(sections=0, lines=6)` — under `sections` the guard
        does not fire and `--confirm` REPLACES that committed document with the
        delta. That is the exact destruction this PR exists to prevent, and no
        test could see the difference because every fixture pinned the
        `lines == 0` boundary rather than the quantity.

        Real content with zero headings must therefore REFUSE.
        """
        work = repo_lacking_the_doc(tmp_path)
        prose = ("A real handoff that happens to carry no markdown heading.\n"
                 "Second line of genuine content.\n"
                 "Third line, still content.\n")
        self._mainline_doc(tmp_path, work, prose, "headingless prose handoff")

        # The precondition this test turns on: content, but zero sections.
        shape = hd.doc_shape(prose)
        assert shape.sections == 0 and shape.lines > 0, shape

        res = run_tool(work, "--confirm", update=update_file)
        assert res.returncode == hd.EXIT_STALE_BASE, (
            "did NOT refuse against a headingless prose mainline doc — that is "
            "the fail-open direction, and it destroys a real document",
            res.stdout, res.stderr)
        assert "status=stale-base" in res.stderr
