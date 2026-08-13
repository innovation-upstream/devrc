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
        """Rule (b): the write is local and reversible; only the push is the
        act that needs consent, and it takes its own flag."""
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
]


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
        for status in ("no-advance", "no-change", "proposed"):
            assert status in doc, f"SKILL.md never mentions `{status}`"

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
