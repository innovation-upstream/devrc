"""CLAUDE.md's description of the merge gate must match what the repo actually has.

WHY THIS EXISTS
---------------
For an unknown span, `CLAUDE.md` asserted "**CI gates both suites**: `nix build
.#checks…`". There is no CI in this repo: no `.github/workflows`, no pre-push
hook, no Tekton trigger, and `statusCheckRollup` is empty on every PR including
merged ones. Nothing has ever gated a devrc merge except a human or agent
running the suite by hand.

That is the worst shape a false claim can take. It sits in the always-loaded
project instructions, it is reassuring, and it is precisely the sort of sentence
nobody re-derives: an agent reads "CI gates both suites", concludes the merge is
protected, and skips the run it was supposed to do.

The fix is not to reword it once — prose rots back. This test makes the claim a
FUNCTION of the repo, checked every run, in BOTH directions:

  * no automated gate present  -> CLAUDE.md must not claim one
  * automated gate present     -> CLAUDE.md must not still say there is none

So wiring up real CI is what makes it safe to claim CI, and removing CI forces
the doc back. Neither drifts silently.

DELIBERATELY STRUCTURAL, NOT SPELLED
------------------------------------
Detection reads the FILESYSTEM (do workflow files exist?), never prose. The
doc-side check looks for the specific refuted assertion rather than the word
"CI" — `CLAUDE.md` legitimately discusses CI in other registers, and a guard
that trips on any mention would be un-satisfiable and get deleted.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = REPO / "CLAUDE.md"

# An automated, server-side or push-side gate. Extend this list if a real gate
# is added by some other mechanism — that is the point of the two-way check.
WORKFLOW_DIR = REPO / ".github" / "workflows"

# The refuted assertion: a claim that something OTHER than the reader runs the
# suite. Matched on normalised whitespace so a reflow cannot walk it.
CLAIMS_AUTOMATED_GATE = re.compile(
    r"\b(CI|GitHub Actions|the pipeline|a pre-push hook)\s+"
    r"(gates|runs|enforces|blocks)\b",
    re.IGNORECASE,
)

# The correction: an explicit statement that the gate is manual. Required
# whenever no automated gate exists, so deleting the paragraph fails too — a
# silent deletion is how the original claim would have been "fixed" cheaply.
STATES_MANUAL_GATE = re.compile(
    r"NOTHING gates a merge in this repo", re.IGNORECASE
)


def _normalised(text: str) -> str:
    """Collapse whitespace, and drop inline code spans.

    A backtick span is a CITATION, not an assertion — the corrected paragraph
    quotes the refuted sentence so a reader grepping for the old wording still
    lands on the retraction. Without this, the doc's own retraction trips the
    guard (it did, on the first run).

    KNOWN LIMIT: a genuine claim written inside backticks would be invisible
    here. Accepted — prose asserts in prose; the alternative is a guard that
    can never be satisfied by a paragraph that explains itself, and an
    un-satisfiable guard gets deleted.
    """
    return re.sub(r"\s+", " ", re.sub(r"`[^`]*`", " ", text))


def _workflow_files() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(
        p for p in WORKFLOW_DIR.iterdir()
        if p.suffix in (".yml", ".yaml") and p.is_file()
    )


def test_claude_md_exists_and_is_readable() -> None:
    """POSITIVE CONTROL: the file this whole test reasons about must be there.

    Without this, a moved/renamed CLAUDE.md would make every assertion below
    operate on an empty string and pass vacuously.
    """
    assert CLAUDE_MD.is_file(), f"CLAUDE.md not found at {CLAUDE_MD}"
    body = CLAUDE_MD.read_text(encoding="utf-8")
    assert len(body) > 2000, (
        f"CLAUDE.md is only {len(body)} bytes — suspiciously small; the checks "
        "below would be near-vacuous against it"
    )


def test_the_regexes_can_actually_match() -> None:
    """POSITIVE CONTROL for the instruments themselves.

    A regex that matches nothing would make the real assertions below report a
    reassuring pass regardless of what CLAUDE.md says. Feed each pattern a
    string it MUST match, and one it must NOT.
    """
    assert CLAIMS_AUTOMATED_GATE.search("CI gates both suites")
    assert CLAIMS_AUTOMATED_GATE.search("GitHub Actions runs the pytest check")
    assert not CLAIMS_AUTOMATED_GATE.search(
        "Run the gate yourself; there is no CI in this repo"
    )
    assert STATES_MANUAL_GATE.search("NOTHING gates a merge in this repo")
    assert not STATES_MANUAL_GATE.search("CI gates both suites")
    # The code-span strip must hide a CITATION but not a bare assertion.
    assert not CLAIMS_AUTOMATED_GATE.search(_normalised("used to read `CI gates both suites`"))
    assert CLAIMS_AUTOMATED_GATE.search(_normalised("CI gates both suites"))
    # ...and must not swallow the whole document via an unbalanced backtick.
    assert "kept" in _normalised("`quoted` kept `also quoted`")


def test_claude_md_gate_description_matches_the_repo() -> None:
    """The load-bearing assertion, checked in BOTH directions."""
    body = _normalised(CLAUDE_MD.read_text(encoding="utf-8"))
    workflows = _workflow_files()

    if not workflows:
        claim = CLAIMS_AUTOMATED_GATE.search(body)
        assert not claim, (
            "CLAUDE.md claims an automated gate "
            f"({claim.group(0)!r}) but this repo has none: "
            f"{WORKFLOW_DIR} contains no workflow files. An agent reading that "
            "sentence will believe the merge is protected and skip running the "
            "suite. Either add real CI, or state that the gate is manual."
        )
        assert STATES_MANUAL_GATE.search(body), (
            "CLAUDE.md no longer states that nothing gates a merge, but this "
            f"repo still has no automated gate ({WORKFLOW_DIR} is empty or "
            "absent). Deleting the warning does not make the gate exist — "
            "restore it, or add the CI it describes."
        )
    else:
        assert not STATES_MANUAL_GATE.search(body), (
            "CLAUDE.md says NOTHING gates a merge, but workflow files now "
            f"exist ({[p.name for p in workflows]}). Update the doc: the "
            "warning is now false in the other direction, and a stale warning "
            "trains readers to ignore this file."
        )
