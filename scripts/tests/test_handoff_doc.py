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
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
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
1. Watch the drain rate for a day.
"""


# Hermetic git: the sandbox and the dev host must behave the same, so no global
# or system config (a stray `core.hooksPath` or `commit.gpgsign` would otherwise
# decide whether these tests can commit at all).
GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}


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
             = "the drain loop is fixed and the at-max reading was corrected"):
    argv = [sys.executable, str(TOOL), "--repo", str(repo), "--topic", "sample-topic"]
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

    def test_the_diff_is_shown_so_there_is_something_to_decline(
        self, repo: Path, update_file: Path
    ) -> None:
        """A gate that writes nothing AND shows nothing is not a gate either."""
        res = run_tool(repo, update=update_file)
        assert diff_body(res.stdout), f"no diff hunks printed:\n{res.stdout}"
        assert "(y/N)" in res.stdout

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
    (
        "update the handoff doc and push it? (y/N)",
        "🔴 the gate SHAPE is the one /handoff already uses for the index write",
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

    def test_the_decline_half_is_stated_in_step_5_not_borrowed_from_step_4(self) -> None:
        """🔴 STRUCTURAL, because the phrase pin would be VACUOUS here.

        MEASURED: `"on decline, discard"` was already in the skill before this
        change — step 4's index gate says it — so a `phrase in doc` assertion
        passed against the PRE-change file and proved nothing about the doc
        gate. What has to hold is that step 5 states its own decline half, so
        this asserts POSITION: the phrase, and the question that precedes it,
        both occur after step 5 begins."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        step5 = doc.index("5. **Land the handoff doc")
        assert doc.index("update the handoff doc and push it? (y/N)") > step5
        assert doc.rindex("on decline, discard") > step5

    def test_the_pin_can_report_absence(self) -> None:
        """NEGATIVE CONTROL on the pin above: a check that can only pass is not
        a check. This phrase is not in the skill and must not become so."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert "push the handoff without asking anyone" not in doc

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
