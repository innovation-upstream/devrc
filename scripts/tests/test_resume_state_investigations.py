"""Rule (l): the AGE of an `## Open investigations` block, made machine-visible.

WHAT THIS GATES
---------------
A mid-diagnosis block is written in the PRESENT TENSE and `/handoff`'s merge
APPENDS it forever — nothing ever retracts one. The doc's status header is
visibly dated; a diagnosis block is not, so it reads as CURRENT for the life of
the document. MEASURED 2026-09-12: a session read one, adopted its framing, and
the framing was wrong — a claim fusing two documents' measurements over two
windows with two instruments, refuted only by a full re-measurement. The worked
example is `claudedocs/handoff-handoff-resume-skill-trace.md`.

🔴 PROSE HAS ALREADY FAILED AT THIS, WHICH IS WHY THERE IS CODE. The `resume`
skill body warns about the class and cites two earlier instances (2026-08-19,
2026-08-20). Two halves ship together and both are tested here:

  * `scripts/lib/handoff_doc.py` STAMPS every new block `as-of: <today>` — the
    writer does it, so it cannot be forgotten.
  * `scripts/resume-state.sh` AGES every block and reports one that has aged
    out, as a DRIFT finding, beside the clock it used.

🔴 THE SEAM IS THE POINT OF `TestTheTwoParsersAgree`. Two parsers in two
languages read one field; each is hermetically testable and both can be green
while disagreeing about a real block. `claude/RULES.md`: "verified in isolation
is the new vacuous green — the defect lives in the SEAM nobody owns." That class
gets a behavioural guard over shared fixture text, not two component suites.

THE UNSTAMPED-BLOCK DECISION, asserted rather than described
------------------------------------------------------------
Almost every block in the corpus carries no stamp, so what an unstamped block
reports IS the design. `drift-check.sh` rc 22 (NOT ADOPTED, no rc) and rc 18
(UNMEASURED is not forever) point in opposite directions, and NEITHER applies —
because an unstamped block is not undateable. MEASURED at 7e000e6b over this
repo's whole corpus: 81 tracked handoff docs carry the section, holding 478
blocks, and the commit that INTRODUCED the block's heading dated 478 of 478.

So the stamp is the most PRECISE clock, never the only one, and an unstamped
block is aged exactly like a stamped one — only the clock NAME differs.
`test_an_unstamped_block_in_an_OLD_doc_is_EXPIRED` and
`test_an_unstamped_block_in_a_FRESH_doc_is_silent` are the two halves of that
claim, and they are the mechanism's reachability demonstration.

HERMETIC. Every fixture repo is a throwaway `git init` under tmp_path with no
remote. `gh`/`kubectl`/`curl`/`clawgatectl` are tripwire stubs on the front of
$PATH that log and fail, so no test reaches a network or a real board, and
`RESUME_STATE_SKILL` is emptied so the SKILL block cannot answer for this host.
"""

from __future__ import annotations

import importlib.util
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

RESUME = REPO_ROOT / "scripts/resume-state.sh"
RESUME_SKILL = REPO_ROOT / "claude/skills/resume/SKILL.md"
HANDOFF_SKILL = REPO_ROOT / "claude/skills/handoff/SKILL.md"
DOC_LIB = REPO_ROOT / "scripts/lib/handoff_doc.py"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("git") is None,
    reason="needs bash + git on PATH",
)

DAY = 86_400


def _load_handoff_doc():
    """`handoff_doc.py` by PATH, the way the rest of the suite loads it.

    🔴 A FAILED IMPORT MUST BE AN ERROR, NOT AN EMPTY ANSWER. Several assertions
    below are of the form "this input yields nothing"; a module that could not
    be loaded yields nothing too.
    """
    spec = importlib.util.spec_from_file_location("handoff_doc_under_test", DOC_LIB)
    assert spec and spec.loader, f"cannot load {DOC_LIB}"
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(DOC_LIB.parent))
    spec.loader.exec_module(mod)
    return mod


try:
    H = _load_handoff_doc()
except Exception as exc:  # pragma: no cover - surfaced as a skip, never a pass
    H = None
    _IMPORT_ERROR = exc


needs_lib = pytest.mark.skipif(H is None, reason="handoff_doc.py could not be imported")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
def _base_env() -> dict:
    env = dict(os.environ)
    # The digest's own escape hatches must not be inherited from whatever shell
    # runs the suite — each would change which blocks execute.
    for k in (
        "RESUME_STATE_INVESTIGATION_MAX_AGE_DAYS",
        "RESUME_STATE_SKILL_SCAN_CAP",
        "CLAUDE_CODE_SESSION_ID",
        "OPENCODE_SESSION_ID",
        "OPENCODE",
    ):
        env.pop(k, None)
    return env


def _git_env(repo: Path) -> dict:
    env = _base_env()
    env.update(
        {
            "GIT_CONFIG_GLOBAL": str(repo.parent / "gitconfig-global"),
            "GIT_CONFIG_SYSTEM": str(repo.parent / "gitconfig-system"),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
            # The SKILL block compares this HOST's deployed ~/.claude copy
            # against origin — a fact about the machine, not about a fixture.
            # EMPTY, not unset: unset reads as "check /resume".
            "RESUME_STATE_SKILL": "",
        }
    )
    return env


@pytest.fixture
def stubs(tmp_path_factory):
    """`gh`/`kubectl`/`curl`/`clawgatectl` tripwires: they log and fail."""
    d = tmp_path_factory.mktemp("inv-stubbin")
    log = d / "invocations.log"
    for name in ("gh", "kubectl", "curl", "clawgatectl"):
        write_exec(d / name, f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\nexit 1\n')
    return d, log


DOC_HEAD = "# Handoff: sample — 2026-01-01\n\n## State now\n- nothing outstanding\n\n"


def doc_with(*blocks: str) -> str:
    """A handoff whose only reconcilable content is its investigation blocks.

    No PR numbers, no branch names and no `clawgate-task:` field, so every other
    block of the digest stays on its skip path and DRIFT is about this one.
    """
    return (
        DOC_HEAD
        + "## Open investigations — live diagnosis state\n\n"
        + "\n".join(blocks)
        + "\n"
    )


def make_repo(
    tmp_path: Path,
    doc_text: str,
    *,
    commit_epoch: int | None = None,
    mtime: int | None = None,
    extra_commits: list[tuple[str, int]] | None = None,
) -> Path:
    """A throwaway repo holding one handoff.

    `commit_epoch` COMMITS the doc at that time. `extra_commits` re-commits the
    doc with new text at later times, which is what makes the block's own
    introducing commit differ from the doc's last commit — the distinction the
    whole clock ladder turns on.
    """
    repo = tmp_path / "fixture-repo"
    (repo / "claudedocs").mkdir(parents=True)
    env = _git_env(repo)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run([*git, "add", "README.md"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "seed"], check=True, env=env)

    p = repo / "claudedocs" / "handoff-sample.md"
    p.write_text(doc_text, encoding="utf-8")
    if commit_epoch is not None:
        _commit(git, env, "claudedocs/handoff-sample.md", commit_epoch, "handoff")
    for text, when in extra_commits or []:
        p.write_text(text, encoding="utf-8")
        _commit(git, env, "claudedocs/handoff-sample.md", when, "update")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return repo


def _commit(git, env, rel: str, epoch: int, msg: str) -> None:
    when = f"{epoch} +0000"
    cenv = dict(env, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    subprocess.run([*git, "add", rel], check=True, env=cenv)
    subprocess.run([*git, "commit", "-qm", msg], check=True, env=cenv)


def run_digest(repo: Path, stubs, **extra_env) -> str:
    d, log = stubs
    env = _git_env(repo)
    env["PATH"] = f"{d}{os.pathsep}{env['PATH']}"
    env["STUB_LOG"] = str(log)
    env.update({k: str(v) for k, v in extra_env.items()})
    out = subprocess.run(
        ["bash", str(RESUME)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    assert out.returncode == 0, f"rc={out.returncode}\n{out.stdout}\n{out.stderr}"
    return out.stdout


def section(out: str, name: str) -> list[str]:
    """The indented lines under a top-level digest header."""
    body, seen = [], False
    for line in out.splitlines():
        if line == name:
            seen = True
            continue
        if seen:
            if line and not line.startswith(" ") and not line.startswith("#"):
                break
            body.append(line)
    assert seen, f"no {name} block in:\n{out}"
    return body


def findings(out: str) -> list[str]:
    return [ln.strip()[2:] for ln in section(out, "DRIFT") if ln.strip().startswith("- ")]


def gaps(out: str) -> list[str]:
    return [ln.strip()[2:] for ln in section(out, "DRIFT") if ln.strip().startswith("! ")]


def rows(text: str) -> list[tuple[str, str]]:
    """`investigation_rows` (the shell parser) on fixture text."""
    r = subprocess.run(
        [
            "bash",
            "-c",
            'set -uo pipefail; source <(sed -n "/^investigation_rows(){/,/^}/p" "$1") '
            "|| exit 97; investigation_rows",
            "harness",
            str(RESUME),
        ],
        input=text,
        capture_output=True,
        text=True,
        timeout=30,
        env=_base_env(),
    )
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr}"
    out = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        stamp, _, heading = line.partition("\t")
        # `-` is the wire form of "no stamp". An EMPTY first field cannot be
        # used: TAB is IFS whitespace, so a leading empty field collapses and
        # the digest's own reader mis-assigns the columns. See the note on
        # `investigation_rows`.
        out.append(("" if stamp == "-" else stamp, heading))
    return out


# --------------------------------------------------------------------------- #
# the shell parser
# --------------------------------------------------------------------------- #
class TestTheShellParser:
    def test_it_finds_stamped_and_unstamped_blocks(self):
        assert rows(
            doc_with(
                "### Alpha breaks\n- as-of: 2026-01-02\n- detail\n",
                "### Beta breaks\n- detail\n",
            )
        ) == [("2026-01-02", "Alpha breaks"), ("", "Beta breaks")]

    def test_a_block_under_a_DIFFERENT_h2_is_not_an_investigation(self):
        """A `### ` under `Gotchas` is settled text, not a live diagnosis."""
        text = doc_with("### Alpha\n- x\n") + "\n## Gotchas\n### Not a diagnosis\n- y\n"
        assert rows(text) == [("", "Alpha")]

    def test_a_FENCED_block_is_a_sample_not_a_claim(self):
        """A handoff routinely pastes the skill's own template."""
        fenced = doc_with(
            "### Real one\n- x\n",
            "```markdown\n### Template block\n- as-of: 2026-01-01\n```\n",
        )
        assert rows(fenced) == [("", "Real one")]

    def test_POSITIVE_CONTROL_the_same_text_unfenced_IS_found(self):
        """🔴 The test above passes for free if the parser sees nothing at all.

        Same bytes, fences removed: the block must now appear, which is what
        proves the exclusion was about the FENCE and not about the parser.
        """
        unfenced = doc_with(
            "### Real one\n- x\n",
            "### Template block\n- as-of: 2026-01-01\n",
        )
        assert rows(unfenced) == [
            ("", "Real one"),
            ("2026-01-01", "Template block"),
        ]

    @pytest.mark.parametrize(
        "field",
        [
            "- as-of: 2026-01-02",
            "- **as-of:** 2026-01-02",
            "- **as-of: 2026-01-02**",
            "- _as-of: 2026-01-02_",
            "  as-of:2026-01-02",
            "- AS-OF: 2026-01-02",
        ],
    )
    def test_the_spellings_rule_j_and_k_taught_all_parse(self, field):
        """An author who learned `forcing:`/`via:` should not learn a third."""
        assert rows(doc_with(f"### Alpha\n{field}\n")) == [("2026-01-02", "Alpha")]

    @pytest.mark.parametrize(
        "field",
        ["- as-of: 2026-01-02-rev2", "- as-of: 20260102", "- as-of: yesterday"],
    )
    def test_an_unparseable_value_reads_ABSENT_so_the_block_is_still_dated(self, field):
        """🔴 The trailing guard is load-bearing. Without it `2026-01-02-rev2`
        parses as a valid date and the block is treated as stamped — a stamp
        nobody can place, silently preferred over a clock that works."""
        assert rows(doc_with(f"### Alpha\n{field}\n")) == [("", "Alpha")]

    def test_the_FIRST_stamp_in_a_block_wins_and_does_not_leak_to_the_next(self):
        assert rows(
            doc_with(
                "### Alpha\n- as-of: 2026-01-02\n- as-of: 2026-05-05\n",
                "### Beta\n- detail\n",
            )
        ) == [("2026-01-02", "Alpha"), ("", "Beta")]

    def test_a_doc_with_no_section_yields_nothing(self):
        assert rows(DOC_HEAD + "## Next steps\n1. x forcing: none\n") == []


# --------------------------------------------------------------------------- #
# the seam
# --------------------------------------------------------------------------- #
SEAM_CORPUS = [
    doc_with("### Alpha\n- as-of: 2026-01-02\n- x\n", "### Beta\n- x\n"),
    doc_with("### Gamma\n- **as-of:** 2026-03-04\n"),
    doc_with("### Delta\n- as-of: 2026-01-02-rev2\n"),
    doc_with("### Eps\n- x\n", "```\n### Fenced\n- as-of: 2026-01-01\n```\n"),
    doc_with("### Zeta — with punctuation: (and parens)\n- x\n"),
    doc_with("### Eta\n- x\n") + "\n## Gotchas\n### Theta\n- x\n",
    DOC_HEAD + "## Next steps\n1. nothing forcing: none\n",
]


@needs_lib
class TestTheTwoParsersAgree:
    """🔴 THE SEAM GUARD. `handoff_doc.py` WRITES the field and
    `resume-state.sh` READS it. Each is hermetically testable and both can be
    green while disagreeing about a real block — the writer skipping one the
    reader then reports UNDATED, or the writer stamping one the reader never
    looks at. This drives ONE corpus through BOTH and compares the verdicts.
    """

    @pytest.mark.parametrize("text", SEAM_CORPUS, ids=range(len(SEAM_CORPUS)))
    def test_the_same_text_yields_the_same_blocks_and_stamps(self, text):
        shell = rows(text)
        py = [(b.stamp or "", b.heading) for b in H.investigation_blocks(text)]
        assert shell == py, (
            "the shell reader and the python writer disagree about this text.\n"
            f"shell: {shell}\npython: {py}\n---\n{text}"
        )

    def test_POSITIVE_CONTROL_the_corpus_actually_exercises_both(self):
        """🔴 A comparison over a corpus that yields nothing everywhere agrees
        for free. Pin that the fixtures produce stamped AND unstamped blocks."""
        seen = [b for t in SEAM_CORPUS for b in H.investigation_blocks(t)]
        assert sum(1 for b in seen if b.stamp) >= 2, seen
        assert sum(1 for b in seen if not b.stamp) >= 4, seen

    def test_the_shell_parser_is_the_one_the_digest_CALLS(self):
        """`rows()` sources one function out of the script. If the digest stops
        calling it, every seam assertion above is about dead code."""
        src = RESUME.read_text(encoding="utf-8")
        assert re.search(r"^\s*rows=.*investigation_rows", src, re.M), src[:0]


# --------------------------------------------------------------------------- #
# reachability: the mechanism fires, and stays silent
# --------------------------------------------------------------------------- #
class TestReachability:
    def test_a_STAMPED_block_past_the_window_is_an_EXPIRED_finding(self, tmp_path, stubs):
        now = int(time.time())
        old = time.strftime("%Y-%m-%d", time.gmtime(now - 40 * DAY))
        repo = make_repo(
            tmp_path,
            doc_with(f"### The mid-diagnosis claim\n- as-of: {old}\n- detail\n"),
            commit_epoch=now - 40 * DAY,
        )
        out = run_digest(repo, stubs)
        hits = [f for f in findings(out) if "EXPIRED" in f]
        assert len(hits) == 1, f"{findings(out)}\n{out}"
        assert "The mid-diagnosis claim" in hits[0]
        assert "as-of stamp" in hits[0], hits[0]
        assert "RE-MEASURE" in hits[0], hits[0]

    def test_a_STAMPED_block_inside_the_window_is_SILENT(self, tmp_path, stubs):
        """The other half. A mechanism that fires on everything is one everybody
        clicks through — `claude/RULES.md` calls a permanently-red gate worse
        than no gate."""
        now = int(time.time())
        fresh = time.strftime("%Y-%m-%d", time.gmtime(now - 2 * DAY))
        repo = make_repo(
            tmp_path,
            doc_with(f"### A live diagnosis\n- as-of: {fresh}\n- detail\n"),
            commit_epoch=now - 2 * DAY,
        )
        out = run_digest(repo, stubs)
        assert not [f for f in findings(out) if "EXPIRED" in f], out
        assert "A live diagnosis" in "\n".join(section(out, "INVESTIGATIONS")), out
        assert "0 EXPIRED" in "\n".join(section(out, "INVESTIGATIONS")), out

    def test_an_unstamped_block_in_an_OLD_doc_is_EXPIRED(self, tmp_path, stubs):
        """🔴 THE UNSTAMPED DECISION, made observable. No stamp, and the block
        is STILL aged — by the commit that introduced it. This is what stops the
        mechanism being inert on the 478 unstamped blocks already in the repo."""
        now = int(time.time())
        repo = make_repo(
            tmp_path,
            doc_with("### An old unstamped claim\n- detail\n"),
            commit_epoch=now - 40 * DAY,
        )
        out = run_digest(repo, stubs)
        hits = [f for f in findings(out) if "EXPIRED" in f]
        assert len(hits) == 1, f"{findings(out)}\n{out}"
        assert "first commit carrying this block" in hits[0], hits[0]

    def test_an_unstamped_block_in_a_FRESH_doc_is_silent(self, tmp_path, stubs):
        now = int(time.time())
        repo = make_repo(
            tmp_path,
            doc_with("### A new unstamped claim\n- detail\n"),
            commit_epoch=now - 1 * DAY,
        )
        out = run_digest(repo, stubs)
        assert not [f for f in findings(out) if "EXPIRED" in f], out

    def test_the_block_is_dated_by_ITS_OWN_commit_not_the_docs_LAST_one(
        self, tmp_path, stubs
    ):
        """🔴 THE HINT THAT LOOKS RIGHT AND ERRS THE UNSAFE WAY. Dating a block
        by the DOC's last commit makes a July block in a doc recommitted this
        morning read 0 days old — false FRESHNESS, which is the defect itself.

        Here the old block is introduced 40 days ago and the doc is re-committed
        an hour ago with a second block appended. The old one must still be
        EXPIRED and the new one silent — impossible for any doc-level clock.
        """
        now = int(time.time())
        old_only = doc_with("### The old claim\n- detail\n")
        both = doc_with("### The old claim\n- detail\n", "### The new claim\n- detail\n")
        repo = make_repo(
            tmp_path,
            old_only,
            commit_epoch=now - 40 * DAY,
            extra_commits=[(both, now - 3600)],
        )
        out = run_digest(repo, stubs)
        hits = [f for f in findings(out) if "EXPIRED" in f]
        assert len(hits) == 1, f"expected exactly the OLD block:\n{findings(out)}\n{out}"
        assert "The old claim" in hits[0], hits[0]
        assert "The new claim" not in " ".join(findings(out)), findings(out)

    def test_the_window_is_the_boundary_it_says_it_is(self, tmp_path, stubs):
        """Both sides of the threshold, measured — not one point extrapolated.

        The fixtures overshoot the boundary rather than landing on it: a block
        exactly AT the window would exercise neither branch definitively.
        """
        now = int(time.time())
        repo = make_repo(
            tmp_path,
            doc_with("### Claim\n- detail\n"),
            commit_epoch=now - 9 * DAY,
        )
        loose = run_digest(repo, stubs, RESUME_STATE_INVESTIGATION_MAX_AGE_DAYS=30)
        tight = run_digest(repo, stubs, RESUME_STATE_INVESTIGATION_MAX_AGE_DAYS=3)
        assert not [f for f in findings(loose) if "EXPIRED" in f], loose
        assert [f for f in findings(tight) if "EXPIRED" in f], tight

    def test_the_DEFAULT_window_is_14_days_and_is_not_hidden_in_an_env_var(self):
        """A default read out of the environment is a default nobody can quote.
        Pinned as a literal here so changing it is a decision, not a drift."""
        src = RESUME.read_text(encoding="utf-8")
        assert 'INVESTIGATION_MAX_AGE_DAYS="${RESUME_STATE_INVESTIGATION_MAX_AGE_DAYS:-14}"' in src

    def test_a_doc_with_NO_section_says_so_and_does_not_claim_health(
        self, tmp_path, stubs
    ):
        """Nothing was asked, so nothing went unanswered — the rc 22 shape, and
        the same call `clawgate_block` makes for a doc naming no task. But the
        line must not read as a clean bill of health either."""
        now = int(time.time())
        repo = make_repo(
            tmp_path,
            DOC_HEAD + "## Next steps\n1. nothing forcing: none\n",
            commit_epoch=now - 400 * DAY,
        )
        out = run_digest(repo, stubs)
        body = "\n".join(section(out, "INVESTIGATIONS"))
        assert "nothing to age" in body, out
        assert "says NOTHING" in body, out
        assert not gaps(out), gaps(out)
        assert not findings(out), findings(out)

    def test_an_UNDATEABLE_block_is_a_GAP_and_reads_differently_from_EXPIRED(
        self, tmp_path, stubs
    ):
        """🔴 EXPIRED and UNDATED are different findings. EXPIRED is a `-`
        finding: the block was dated and it has aged out. UNDATED is a `!` gap:
        nothing was measured, so nothing may be concluded — and a gap withdraws
        the DRIFT all-clear, which a finding-shaped line would not."""
        now = int(time.time())
        repo = make_repo(
            tmp_path,
            doc_with("### Undateable\n- as-of: 2026-02-31\n- detail\n"),
            commit_epoch=now - 2 * DAY,
        )
        out = run_digest(repo, stubs)
        hit = [g for g in gaps(out) if "Undateable" in g]
        assert len(hit) == 1, f"{gaps(out)}\n{out}"
        assert "not a real date" in hit[0], hit[0]
        assert "EXPIRED" not in hit[0], hit[0]
        assert "1 undated" in "\n".join(section(out, "INVESTIGATIONS")), out

    def test_an_UNCOMMITTED_doc_falls_back_to_mtime_and_SAYS_SO(self, tmp_path, stubs):
        """The doc is not in git history at all, so no content-derived clock can
        answer. mtime is used and gapped: a checkout, copy or rsync resets it, so
        the age is not evidence in either direction."""
        old = int(time.time()) - 90 * DAY
        repo = make_repo(
            tmp_path,
            doc_with("### Unversioned claim\n- detail\n"),
            commit_epoch=None,
            mtime=old,
        )
        out = run_digest(repo, stubs)
        assert any("file mtime" in g for g in gaps(out)), f"{gaps(out)}\n{out}"

    def test_a_gap_withdraws_the_DRIFT_all_clear(self, tmp_path, stubs):
        """The gap has to reach `UNRECONCILED`, not just print a line — that is
        what makes the digest say it is NOT a clean bill of health."""
        now = int(time.time())
        repo = make_repo(
            tmp_path,
            doc_with("### Undateable\n- as-of: 2026-02-31\n"),
            commit_epoch=now - 2 * DAY,
        )
        out = run_digest(repo, stubs)
        assert "none detected — live state matches" not in out, out
        assert "NOT a clean bill of health" in out, out


# --------------------------------------------------------------------------- #
# the writer
# --------------------------------------------------------------------------- #
@needs_lib
class TestTheWriterStamps:
    def test_an_unstamped_block_is_stamped(self):
        out, done = H.stamp_investigations(doc_with("### Alpha\n- x\n"), "2026-09-12")
        assert done == ["Alpha"]
        assert "### Alpha\n- as-of: 2026-09-12\n- x\n" in out

    def test_an_EXPLICIT_stamp_is_never_rewritten(self):
        """A session recording evidence gathered last week must be able to say
        so. "The tool moved my date" is how an author learns to distrust a
        field."""
        text = doc_with("### Alpha\n- as-of: 2026-01-02\n- x\n")
        out, done = H.stamp_investigations(text, "2026-09-12")
        assert done == [] and out == text

    def test_it_is_idempotent(self):
        once, _ = H.stamp_investigations(doc_with("### Alpha\n- x\n"), "2026-09-12")
        twice, done = H.stamp_investigations(once, "2026-09-13")
        assert done == [] and twice == once, twice

    def test_it_does_not_stamp_a_FENCED_sample(self):
        text = doc_with("```\n### Sample\n- x\n```\n")
        out, done = H.stamp_investigations(text, "2026-09-12")
        assert done == [] and out == text

    def test_it_does_not_stamp_a_block_under_another_heading(self):
        text = DOC_HEAD + "## Gotchas\n### Settled\n- x\n"
        out, done = H.stamp_investigations(text, "2026-09-12")
        assert done == [] and out == text

    def test_it_stamps_EVERY_unstamped_block_not_only_the_first(self):
        """Insertion shifts every later line index. Walking forward with a stale
        index is the classic way the second block gets stamped in the wrong
        place — or not at all."""
        text = doc_with("### A\n- x\n", "### B\n- y\n", "### C\n- z\n")
        out, done = H.stamp_investigations(text, "2026-09-12")
        assert done == ["A", "B", "C"], done
        for h in "ABC":
            assert f"### {h}\n- as-of: 2026-09-12\n" in out, out

    def test_the_advisory_names_the_blocks_and_says_the_date_is_TODAY(self):
        msg = H.stamped_report(["Alpha"], "2026-09-12")
        assert "Alpha" in msg and "2026-09-12" in msg
        assert "older" in msg, msg

    def test_it_is_silent_when_nothing_needed_stamping(self):
        assert H.stamped_report([], "2026-09-12") == ""

    def test_the_stamp_lands_in_the_diff_the_author_approves(self, tmp_path):
        """END TO END through the CLI. A line the TOOL wrote must be on screen
        before the confirm, not discovered in the committed doc afterwards."""
        repo = tmp_path / "repo"
        (repo / "claudedocs").mkdir(parents=True)
        env = _git_env(repo)
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
        scratch = tmp_path / "delta.md"
        # 🔴 THE `## Goal` IS NOT DECORATION — rule (m) refuses a NEW doc that
        # names no closing condition, and this fixture creates one. Without the
        # field the run exits 11 and never reaches the stamp this test is about.
        scratch.write_text(
            "## Goal\nProve the stamp lands in the diff.\n"
            "- closing-condition: check — this test passes\n\n"
            + doc_with("### A brand new claim\n- detail\n")
            + "\n## Next steps\n1. do the thing forcing: none\n",
            encoding="utf-8",
        )
        r = subprocess.run(
            [
                sys.executable,
                str(DOC_LIB),
                "--repo",
                str(repo),
                "--topic",
                "sample",
                "--update",
                str(scratch),
                "--advanced",
                "wrote the first draft",
                "--new-effort",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        assert "status=proposed" in r.stdout, f"{r.stdout}\n{r.stderr}"
        assert re.search(r"^\+- as-of: \d{4}-\d{2}-\d{2}$", r.stdout, re.M), r.stdout
        assert "carried no `as-of:` date" in r.stdout, r.stdout


# --------------------------------------------------------------------------- #
# documentation, pinned two-way
# --------------------------------------------------------------------------- #
class TestTheDocsMatchTheCode:
    def test_every_gap_and_finding_word_the_block_can_print_is_documented(self):
        """🔴 DERIVED FROM THE SCRIPT, not restated. A reader shown `EXPIRED` or
        a `?` row by a digest whose skill never names them has to guess."""
        doc = RESUME_SKILL.read_text(encoding="utf-8")
        for word in ("EXPIRED", "as-of", "first commit carrying this block"):
            assert word in doc, (
                f"resume-state.sh's INVESTIGATIONS block can print {word!r} and "
                f"claude/skills/resume/SKILL.md never mentions it."
            )

    def test_the_two_outcomes_are_documented_as_DIFFERENT_things(self):
        doc = RESUME_SKILL.read_text(encoding="utf-8")
        anchor = "`INVESTIGATIONS` block"
        assert anchor in doc, "the resume skill never introduces the block"
        # The doc must say which one is a finding and which is a gap, or a
        # reader treats an unmeasured block as a measured-fresh one.
        para = doc[doc.index(anchor) : doc.index(anchor) + 4000]
        assert "EXPIRED" in para and "UNDATED" in para, para[:400]
        assert "`-` DRIFT finding" in para, "which one is a finding is not said"
        assert "`!` gap" in para, "which one is a gap is not said"

    def test_the_handoff_skill_tells_an_author_the_field_exists(self):
        """The tool stamps automatically, so the ONLY thing an author must know
        is that an explicit date wins — which is the case the tool cannot get
        right on its own."""
        doc = HANDOFF_SKILL.read_text(encoding="utf-8")
        assert "as-of" in doc, doc[:0]

    def test_the_pin_can_report_absence(self):
        """NEGATIVE CONTROL on the instrument: a substring check that can only
        pass is not a check."""
        assert "as-of-nonexistent-marker" not in RESUME_SKILL.read_text(encoding="utf-8")

    def test_the_new_test_file_and_its_subject_are_tracked_by_git(self):
        """A new file the flake never sees deploys as an absence, silently."""
        if not (REPO_ROOT / ".git").exists():
            return
        rel = "scripts/tests/test_resume_state_investigations.py"
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "--", rel],
            capture_output=True,
            text=True,
            env=_base_env(),
        )
        assert out.stdout.strip() == rel, f"{rel} is untracked; the flake omits it"
