"""Rule (m)'s CONSUMER: the `DOD` block that puts the arc's finish line on
screen at the moment a round is about to extend it.

WHAT THIS GATES
---------------
`scripts/lib/handoff_doc.py` makes the `closing-condition:` field EXIST. Nothing
about a field existing in a document stops round 14 from happening. What can
stop it is the finish line being in front of the session that is choosing the
round's work — which is this block, in the digest every `/resume` reads before
it reads the doc.

MEASURED (`<homelab-talos>/claudedocs/audit-arc-rabbit-holes-2026-09-13.md`,
committed at `841cf63b3`): over 75 days, 745 doc-linked kickoff sessions across
299 arcs. In all five deep-read arcs the round-1 objective was met by round 1-7
and the arc then ran 13-23 rounds. Of the 185 close-checks that landed on an arc
session, 21 (11%) closed one; the median close-check to re-kickoff gap was 1.0 h. Both numbers describe
rounds that had no object to answer "is it done?" against.

🔴 THE SEAM IS THE POINT OF `TestTheTwoParsersAgree`, and it is the same seam
`test_resume_state_investigations.py` names for rule (l): one field, two
parsers, two languages. Each is hermetically testable and both can be green
while disagreeing about a real document. `claude/RULES.md`: "verified in
isolation is the new vacuous green — the defect lives in the SEAM nobody owns."
So that class asserts a RELATIONSHIP over shared fixture text, not two component
suites side by side.

HERMETIC. Every fixture repo is a throwaway `git init` under tmp_path with no
remote. `gh`/`kubectl`/`curl`/`clawgatectl` are tripwire stubs on the front of
$PATH that log and fail, so no test reaches a network or a real board, and
`RESUME_STATE_SKILL` is emptied so the SKILL block cannot answer for this host.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
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


def _load_handoff_doc():
    """`handoff_doc.py` by PATH, the way the rest of the suite loads it.

    🔴 A FAILED IMPORT MUST BE AN ERROR, NOT AN EMPTY ANSWER — several
    assertions below are of the form "this input yields nothing", and a module
    that could not be loaded yields nothing too.
    """
    spec = importlib.util.spec_from_file_location("handoff_doc_dod", DOC_LIB)
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
            # EMPTY, not unset: unset reads as "check /resume", which would make
            # this suite report on the HOST's deployed skill copy.
            "RESUME_STATE_SKILL": "",
        }
    )
    return env


@pytest.fixture
def stubs(tmp_path_factory):
    """`gh`/`kubectl`/`curl`/`clawgatectl` tripwires: they log and fail."""
    d = tmp_path_factory.mktemp("dod-stubbin")
    log = d / "invocations.log"
    for name in ("gh", "kubectl", "curl", "clawgatectl"):
        write_exec(d / name, f'printf "{name} %s\\n" "$*" >> "$STUB_LOG"\nexit 1\n')
    return d, log


def doc_with_goal(goal_body: str) -> str:
    """A handoff whose only reconcilable content is its `## Goal`.

    No PR numbers, no branch names, no `clawgate-task:` field and no
    investigation blocks, so every other block of the digest stays on its skip
    path and what lands in DRIFT is about this one.
    """
    return (
        "# Handoff: sample — 2026-01-01\n\n"
        "## Goal\n"
        f"{goal_body}"
        "\n## State now\n- nothing outstanding\n"
    )


def make_repo(tmp_path: Path, doc_text: str) -> Path:
    repo = tmp_path / "fixture-repo"
    (repo / "claudedocs").mkdir(parents=True)
    env = _git_env(repo)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run([*git, "add", "README.md"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "seed"], check=True, env=env)
    (repo / "claudedocs" / "handoff-sample.md").write_text(doc_text, encoding="utf-8")
    subprocess.run([*git, "add", "claudedocs/handoff-sample.md"], check=True, env=env)
    subprocess.run([*git, "commit", "-qm", "handoff"], check=True, env=env)
    return repo


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


def gaps(out: str) -> list[str]:
    return [ln.strip()[2:] for ln in section(out, "DRIFT") if ln.strip().startswith("! ")]


def shell_row(text: str) -> str:
    """`dod_row` — the SHELL parser — run on fixture text.

    Sourced out of the script rather than reimplemented, so this cannot drift
    from the function the digest actually calls.
    """
    r = subprocess.run(
        [
            "bash",
            "-c",
            'set -uo pipefail; source <(sed -n "/^dod_row(){/,/^}/p" "$1") '
            "|| exit 97; dod_row",
            "_",
            str(RESUME),
        ],
        input=text,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 0, f"rc={r.returncode}: {r.stderr}"
    return r.stdout.strip()


# --------------------------------------------------------------------------- #
# the block itself
# --------------------------------------------------------------------------- #
CHECK_GOAL = (
    "Stop the widget queue dropping work.\n"
    "- closing-condition: check — `tools/queue_probe.py --for 240` reports 30/s\n"
)
JUDGEMENT_GOAL = (
    "Decide whether the migration is worth doing.\n"
    "- closing-condition: judgement — Zach reads claudedocs/migration-eval.md\n"
)
BARE_GOAL = "Stop the widget queue dropping work.\n"


def test_a_check_condition_is_printed_with_its_RUN_IT_instruction(tmp_path, stubs):
    """🔴 THE WHOLE INTERVENTION. A `check` names something a later session can
    RUN, so the instruction has to be to run it — a session that proposes a
    round of work without running a check the doc named is the failure this
    block exists for."""
    out = run_digest(make_repo(tmp_path, doc_with_goal(CHECK_GOAL)), stubs)
    body = "\n".join(section(out, "DOD"))
    assert "closing-condition: check — `tools/queue_probe.py --for 240` reports 30/s" in body, body
    assert "RUN IT before proposing work" in body, body
    assert "this arc is CLOSED" in body, body


def test_a_judgement_condition_does_NOT_tell_the_session_to_close_it(tmp_path, stubs):
    """🔴 THE HALF THAT IS EASY TO GET WRONG. A `judgement` closes when a NAMED
    person reads NAMED evidence; an agent substituting its own verdict is the
    thing `claude/RULES.md` refuses to let a closing condition mean ('never
    someone will decide'). So the two kinds must render DIFFERENT
    instructions."""
    out = run_digest(make_repo(tmp_path, doc_with_goal(JUDGEMENT_GOAL)), stubs)
    body = "\n".join(section(out, "DOD"))
    assert "Zach reads claudedocs/migration-eval.md" in body, body
    assert "Nothing here closes it for them" in body, body
    assert "RUN IT" not in body, body


def test_every_rendering_repeats_that_the_line_is_FROZEN(tmp_path, stubs):
    """The finding is not "arcs lack a goal", it is that later rounds EXTEND
    one. The frozen clause is what says an outstanding item that is not this
    line starts a NEW arc, and it has to be on screen in both kinds."""
    for goal in (CHECK_GOAL, JUDGEMENT_GOAL):
        repo = make_repo(tmp_path / goal[:12].strip().replace(" ", "-"), doc_with_goal(goal))
        body = "\n".join(section(run_digest(repo, stubs), "DOD"))
        assert "FROZEN at round 1" in body, body
        assert "a NEW arc" in body, body


def test_a_doc_with_no_condition_is_LOUD_in_its_own_block(tmp_path, stubs):
    """🔴 A REASSURING SILENCE IS THE FAILURE MODE, so the block says it at 🔴 —
    and says the thing a reader will otherwise get wrong: unanswerable is not
    the same as unfinished."""
    out = run_digest(make_repo(tmp_path, doc_with_goal(BARE_GOAL)), stubs)
    body = "\n".join(section(out, "DOD"))
    assert "declares NO closing-condition" in body, body
    assert "NOT the same as unfinished" in body, body
    assert "closing-condition: check|judgement" in body, body


def test_a_missing_condition_is_NOT_a_gap(tmp_path, stubs):
    """🔴 THE CHANNEL, AND IT IS A MEASURED CORRECTION. The first version of
    this block raised a `!` gap for a missing field. It fired on EVERY run —
    measured 2026-09-13, 0 of 183 handoff docs across devrc and homelab-talos
    carry one — which turns the `!! GAPS` banner into furniture, and the banner
    is the only thing telling a reader that a findings list is INCOMPLETE. It
    took 49 tests red in one run, all of them asserting the ordinary no-gap
    path.

    It is also the wrong channel by this script's own rule: the CLAWGATE block
    already decides that a doc with no `clawgate-task:` field is NOT a gap —
    nothing was asked, so nothing went unanswered. A document that declares no
    finish line is that same case, and the loud line above is where it belongs.
    """
    out = run_digest(make_repo(tmp_path, doc_with_goal(BARE_GOAL)), stubs)
    assert not [g for g in gaps(out) if "closing-condition" in g], gaps(out)


def test_the_gap_channel_still_WORKS_on_this_fixture(tmp_path, stubs):
    """🔴 POSITIVE CONTROL FOR THE TEST ABOVE, and without it that test is
    indistinguishable from a digest whose gap list is wired to nothing. Force a
    gap this fixture would not otherwise raise — an UNREADABLE `clawgate-task:`
    field — and watch the list become non-empty."""
    doc = doc_with_goal(CHECK_GOAL).replace(
        "# Handoff:", "---\nclawgate-task: not-a-task-id\n---\n# Handoff:", 1
    )
    out = run_digest(make_repo(tmp_path, doc), stubs)
    assert gaps(out), f"the gap channel produced nothing at all:\n{out}"
    assert not [g for g in gaps(out) if "closing-condition" in g], gaps(out)


def test_no_handoff_at_all_says_so_instead_of_reporting_a_missing_field(
    tmp_path, stubs
):
    """Nothing was asked, so nothing went unanswered — the distinction the
    CLAWGATE block already draws for a doc with no task field. Reporting "no
    closing-condition" for a run that loaded no document at all would blame the
    document for the collector's own skip."""
    repo = tmp_path / "empty-repo"
    (repo / "claudedocs").mkdir(parents=True)
    env = _git_env(repo)
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True, env=env)
    body = "\n".join(section(run_digest(repo, stubs), "DOD"))
    assert "no arc to close" in body, body
    assert "declares NO closing-condition" not in body, body


def test_the_block_runs_LAST_and_before_DRIFT(tmp_path, stubs):
    """Its position is load-bearing and is pinned in two places: this, and
    `test_resume_state_skill_freshness.py`'s whole-sequence assertion. It is the
    question every other block's findings feed into, and it contributes a gap,
    so it must run before the block that prints gaps."""
    out = run_digest(make_repo(tmp_path, doc_with_goal(CHECK_GOAL)), stubs)
    headers = [ln for ln in out.splitlines() if ln and not ln.startswith((" ", "#"))]
    assert headers[-2:] == ["DOD", "DRIFT"], headers


# --------------------------------------------------------------------------- #
# the SEAM
# --------------------------------------------------------------------------- #
@needs_lib
class TestTheTwoParsersAgree:
    """🔴 ONE FIELD, TWO PARSERS, TWO LANGUAGES — and each is green in
    isolation while disagreeing about a real document.

    `handoff_doc.py` decides whether the field is THERE (it refuses a write
    without one); `resume-state.sh` decides what a later round SEES. A document
    the writer accepts and the reader cannot parse is a finish line that exists
    and is never shown — which is the whole failure, wearing a green suite.
    """

    #: Shared fixture text. Every case names the shape it is about; the last
    #: three are the ones that must yield NOTHING on both sides, because a
    #: parser that is merely more generous is just as much a disagreement.
    CASES = [
        ("plain", "## Goal\nx\nclosing-condition: check — the probe passes\n"),
        (
            "bulleted-bold-template",
            "## Goal\nx\n- **closing-condition:** `check` — `tools/probe.py` exits 0\n",
        ),
        (
            "judgement",
            "## Goal\nx\n- closing-condition: judgement — Zach reads the r3 transcript\n",
        ),
        (
            "backtick-detail",
            "## Goal\nx\n- closing-condition: check — `gate.sh` exits 0 on main\n",
        ),
        (
            "double-dash-detail",
            "## Goal\nx\n- closing-condition: check — --dry-run exits 0\n",
        ),
        ("colon-separator", "## Goal\nx\n- closing-condition: check: PR #123 merges\n"),
        (
            # No em dash at all: the separator strip must NOT eat one of the
            # dashes. Python anchors with a lookahead, awk tests the shape
            # first — two implementations of one bound, which is exactly the
            # kind of thing that agrees by luck until it does not.
            "no-separator-double-dash",
            "## Goal\nx\n- closing-condition: check --dry-run exits 0\n",
        ),
        (
            # Parses on both sides and is DECLARED on neither: the writer
            # refuses an unknown kind, so the reader must not show one.
            "unknown-kind",
            "## Goal\nx\n- closing-condition: soon — whenever it feels done\n",
        ),
        ("empty-detail", "## Goal\nx\n- closing-condition: check\n"),
        (
            "empty-detail-with-dash",
            "## Goal\nx\n- **closing-condition:** check —\n",
        ),
        (
            # 🔴 The FIRST field wins on both sides, declared or not. Without
            # that, a malformed field would be skipped by one parser in favour
            # of a later good one and the two would disagree about which line
            # the document meant.
            "malformed-first-then-good",
            "## Goal\nx\n- closing-condition: soon — nope\n"
            "- closing-condition: check — the probe passes\n",
        ),
        # 🔴 THE FOUR ROUND-1 DIVERGENCES (audit F5). Every one of these was a
        # REAL disagreement between the two parsers, found outside this matrix
        # and pinned here so the matrix stops being the weak control it was.
        # Three ran in the WORSE direction — the awk READER showed a finish line
        # the python WRITER refuses, so `/resume` would print a condition no
        # `/handoff` run would ever have accepted.
        (
            # awk `^[A-Za-z]+` had no trailing boundary, so this split as kind
            # `check` + detail `2 — …` while python's `(?![A-Za-z0-9])` refused.
            "digit-after-kind",
            "## Goal\nx\n- closing-condition: check2 — the probe passes\n",
        ),
        (
            # python's `_MARKUP` is bounded at 3; awk's run was unbounded.
            "markup-run-of-four",
            "## Goal\nx\n- **closing-condition****: check — the probe passes\n",
        ),
        (
            # …and the boundary itself: three still parses on BOTH sides, so the
            # bound is pinned from both directions rather than "long fails".
            "markup-run-of-three",
            "## Goal\nx\n- **closing-condition***: check — the probe passes\n",
        ),
        (
            # The other direction: python's `\s` is unicode-aware, awk's `[ \t]`
            # was not, so a NBSP after the colon made the WRITER declare a field
            # the READER could not see.
            "nbsp-after-the-colon",
            "## Goal\nx\n- closing-condition:\u00a0check — the probe passes\n",
        ),
        ("absent", "## Goal\nx\n"),
        (
            "wrong-section",
            "## Goal\nx\n## State now\n- closing-condition: check — the probe passes\n",
        ),
        (
            "fenced",
            "## Goal\nx\n```\nclosing-condition: check — the probe passes\n```\n",
        ),
        (
            "near-miss-spelling",
            "## Goal\nx\n- closing condition: check — the probe passes\n",
        ),
    ]

    @pytest.mark.parametrize("name,text", CASES, ids=[c[0] for c in CASES])
    def test_both_parsers_read_the_same_field(self, name: str, text: str) -> None:
        py = H.closing_condition(text)
        sh = shell_row(text)
        if py.is_declared:
            assert sh == f"{py.kind}\t{py.detail}", (
                f"{name}: python says {py.kind!r}/{py.detail!r}, shell says {sh!r}"
            )
        else:
            assert sh == "", (
                f"{name}: python declares nothing, shell says {sh!r} — the "
                f"reader would show a finish line the writer never accepted"
            )

    def test_the_case_matrix_covers_BOTH_verdicts(self) -> None:
        """🔴 POSITIVE CONTROL ON THE MATRIX ITSELF. An agreement test whose
        cases all land on one side cannot see a disagreement — the same
        objection `test_resume_state_investigations.py` records for its own
        two-parser class."""
        verdicts = {H.closing_condition(t).is_declared for _n, t in self.CASES}
        assert verdicts == {True, False}, (
            f"the matrix produced only {verdicts}; it cannot see a disagreement"
        )

    def test_the_shell_parser_can_be_shown_to_ANSWER(self) -> None:
        """🔴 POSITIVE CONTROL ON THE INSTRUMENT. Half the assertions above are
        of the form "the shell says nothing", and a `dod_row` wired to nothing —
        a renamed function, a `sed` range that no longer matches — says nothing
        too, silently, for every case. Feed it one input that MUST produce a
        row and watch the value move."""
        assert shell_row("## Goal\nx\nclosing-condition: check — it passes\n") == (
            "check\tit passes"
        )


# --------------------------------------------------------------------------- #
# the SKILL side
# --------------------------------------------------------------------------- #
def test_the_resume_skill_documents_the_DOD_block() -> None:
    """A block the digest prints and the skill never mentions leaves the agent
    improvising about the one line that can end the session. Same idiom as
    `test_every_exit_code_the_module_can_return_is_documented`."""
    body = RESUME_SKILL.read_text(encoding="utf-8")
    assert "`DOD`" in body, "the resume skill never names the DOD block"
    for kind in sorted(H.CLOSING_KINDS) if H else ("check", "judgement"):
        assert f"closing-condition: {kind}" in body or f"`{kind}`" in body, (
            f"the resume skill never shows the `{kind}` kind, so a reader "
            f"cannot tell which of the two instructions applies to them"
        )


def test_the_handoff_skill_TEMPLATE_carries_the_field() -> None:
    """🔴 THE RED-BY-CONSTRUCTION GUARD. The executor writes its scratch file
    BEFORE step 5 exists to refuse anything, so a field reachable only through
    the refusal makes every new-doc run fail once and fix — which
    `claude/RULES.md` calls worse than no gate. The template is what prevents
    that, and it is what this asserts."""
    body = HANDOFF_SKILL.read_text(encoding="utf-8")
    key = H.CLOSING_KEY if H else "closing-condition"
    assert f"**{key}:**" in body, (
        f"the step-2 template no longer shows `{key}:`; every new handoff will "
        f"now hit status=undefined-done on its first run"
    )
    assert "FROZEN AT ROUND 1" in body
    assert "ADDRESSED" in body and "CLOSED" in body, (
        "the close-check verdict clause is gone from the template — the field "
        "is then a fact nobody is told to answer against"
    )
