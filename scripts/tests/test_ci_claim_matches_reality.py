"""CLAUDE.md's merge-gate marker must match what the repo actually has.

WHY THIS EXISTS
---------------
For an unknown span, `CLAUDE.md` asserted "**CI gates both suites**: `nix build
.#checks…`". No CI has ever run on a devrc PR: no `.github/workflows`, no branch
protection, no ruleset, no Tekton trigger, and `statusCheckRollup` is empty on
every PR including merged ones. Every merge has rested on a human or agent
running the suite by hand.

That is the worst shape a false claim can take. It lives in the always-loaded
project instructions, it is reassuring, and it is exactly the sort of sentence
nobody re-derives: an agent reads it, concludes the merge is protected, and
skips the run it was supposed to do.

WHY A MARKER, NOT A PROSE MATCH
-------------------------------
The first version of this test regex-matched the prose ("CI gates ..."). An
audit walked it in five ways in one pass — `**CI** gates both suites` (bold on
the word, not the phrase), `CI *now* gates ...`, "Merges are blocked until the
CI pipeline passes", "GitHub Actions will run ...", "Branch protection requires
..." — and it ALSO produced false positives on legitimate unrelated prose
("clawgate-ci gates this repo" matches `\\bci\\b`; `CLAUDE.md` discusses
pipelines and timers routinely). A guard that reds on an innocent edit gets
deleted; one that misses the reword it exists to catch is theatre.

So the pinned claim is a MACHINE-READABLE MARKER — `<!-- merge-gate: none -->`
— and the surrounding prose is free. Rewording costs nothing; changing what
gates a merge forces the marker. This is the "pin the whole normalised claim,
not a feature of it" rule: a machine-readable claim is worth a cosmetic cost.

SCOPE — WHAT THIS CANNOT SEE, STATED SO NOBODY OVER-TRUSTS IT
--------------------------------------------------------------
It detects **GitHub Actions workflow files that trigger on push/pull_request**.
It deliberately does NOT claim to detect:
  * branch protection / rulesets / required checks from a GitHub App (API-only,
    unavailable in the hermetic test sandbox),
  * Tekton triggers (they live in another repo entirely),
  * git hooks — `githooks/` ships a real blocking pre-push gate here, installed
    by pointing `core.hooksPath` elsewhere, which is per-clone and untracked, so
    no test in the sandbox can pin it.
`CLAUDE.md` names all three and says they are not machine-checked. A guard whose
advertised reach exceeds its detector is how the original false claim happened;
do not widen the promise without widening the code.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = REPO / "CLAUDE.md"
WORKFLOW_DIR = REPO / ".github" / "workflows"

# The machine-readable claim. `none` = nothing automated gates a merge;
# `github-actions` = at least one workflow triggers on push/pull_request.
MARKER_RE = re.compile(r"<!--\s*merge-gate:\s*(?P<value>[a-z0-9-]+)\s*-->")
VALID_MARKERS = {"none", "github-actions"}

# A workflow only gates if it runs on push or pull_request. A stale-bot or a
# release-on-tag workflow is not a merge gate, and treating any *.yml as one
# would force CLAUDE.md to delete a TRUE warning.
GATING_TRIGGER_RE = re.compile(r"^\s*on:.*", re.MULTILINE)
TRIGGER_WORDS = ("pull_request", "push")


def _workflow_files() -> list[Path]:
    """GitHub Actions only reads *.yml/*.yaml at the TOP level of this dir."""
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(
        p for p in WORKFLOW_DIR.iterdir()
        if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def _gating_workflows() -> list[Path]:
    out = []
    for p in _workflow_files():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if GATING_TRIGGER_RE.search(text) and any(w in text for w in TRIGGER_WORDS):
            out.append(p)
    return out


def _marker(body: str) -> str | None:
    m = MARKER_RE.search(body)
    return m.group("value") if m else None


def test_claude_md_exists_and_is_substantial() -> None:
    """POSITIVE CONTROL: without this, a moved/renamed CLAUDE.md would make
    every assertion below operate on an empty string and pass vacuously."""
    assert CLAUDE_MD.is_file(), f"CLAUDE.md not found at {CLAUDE_MD}"
    assert len(CLAUDE_MD.read_text(encoding="utf-8")) > 2000, (
        "CLAUDE.md is suspiciously small; the checks below would be near-vacuous"
    )


def test_the_detectors_can_actually_observe_things(tmp_path) -> None:
    """POSITIVE CONTROLS for both detectors.

    A reassuring zero from `_gating_workflows()` is indistinguishable from a
    detector wired to nothing — this repo's own RULES.md names that trap, and an
    audit confirmed the earlier version had no such control (neutering
    `_workflow_files` to `return []` survived a fully green suite, even with a
    real workflow file present). Prove each detector CAN see its subject.
    """
    # Marker parser: must read a value, and must not invent one.
    assert _marker("x <!-- merge-gate: none --> y") == "none"
    assert _marker("x <!-- merge-gate:github-actions--> y") == "github-actions"
    assert _marker("no marker here at all") is None

    # Workflow detector: plant a real gating workflow and a non-gating one,
    # and prove it separates them. Exercises the same functions the assertion
    # below uses, via a redirected WORKFLOW_DIR.
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "gate.yml").write_text("name: gate\non: [pull_request]\njobs: {}\n")
    (wf / "stale.yml").write_text("name: stale\non:\n  schedule:\n    - cron: '0 0 * * *'\n")
    (wf / "notes.md").write_text("not a workflow\n")
    (wf / "nested").mkdir()
    (wf / "nested" / "ignored.yml").write_text("name: x\non: [push]\n")

    global WORKFLOW_DIR
    original = WORKFLOW_DIR
    try:
        WORKFLOW_DIR = wf
        found = {p.name for p in _workflow_files()}
        gating = {p.name for p in _gating_workflows()}
    finally:
        WORKFLOW_DIR = original

    assert found == {"gate.yml", "stale.yml"}, (
        f"workflow detector is wrong: {found} (must ignore non-YAML and "
        "subdirectories, which GitHub Actions itself ignores)"
    )
    assert gating == {"gate.yml"}, (
        f"gating detector is wrong: {gating} — a scheduled-only workflow is not "
        "a merge gate, and calling it one would force CLAUDE.md to delete a "
        "warning that is still true"
    )


def test_claude_md_marker_matches_the_repo() -> None:
    """The load-bearing assertion, checked in BOTH directions."""
    body = CLAUDE_MD.read_text(encoding="utf-8")
    marker = _marker(body)

    assert marker is not None, (
        "CLAUDE.md has no `<!-- merge-gate: ... -->` marker. It is the "
        "machine-readable statement of what gates a merge here; without it the "
        "claim reverts to unverifiable prose, which is how "
        "'CI gates both suites' survived while no CI existed. Restore it."
    )
    assert marker in VALID_MARKERS, (
        f"unknown merge-gate marker {marker!r}; expected one of "
        f"{sorted(VALID_MARKERS)}"
    )

    gating = [p.name for p in _gating_workflows()]

    if gating:
        assert marker == "github-actions", (
            f"CLAUDE.md's marker says {marker!r}, but workflows that trigger on "
            f"push/pull_request now exist ({gating}). A stale 'nothing gates a "
            "merge' warning trains readers to ignore this file — update the "
            "marker and the paragraph."
        )
    else:
        assert marker == "none", (
            f"CLAUDE.md's marker says {marker!r}, but no GitHub Actions "
            f"workflow in {WORKFLOW_DIR} triggers on push/pull_request. An "
            "agent reading that believes the merge is protected and skips "
            "running the suite. Either add real CI, or set the marker to "
            "'none'."
        )
