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
The first version regex-matched the prose ("CI gates ..."). An audit walked it
five ways in one pass — `**CI** gates both suites` (bold on the word, not the
phrase), "CI *now* gates", "Merges are blocked until the CI pipeline passes",
"GitHub Actions will run ...", "Branch protection requires ..." — and it ALSO
fired on innocent prose ("clawgate-ci gates this repo" matches `\\bci\\b`). A
guard that reds on an honest edit gets deleted; one that misses the reword it
exists to catch is theatre. Detecting a lie in prose is undecidable.

So the pinned claim is a MACHINE-READABLE MARKER — `<!-- merge-gate: none -->`
— and the prose around it is free.

WHAT "GATING" MEANS HERE, PRECISELY
-----------------------------------
A workflow gates a MERGE only if it runs on `pull_request` (or `merge_group`).
A `push` workflow runs AFTER the merge and blocks nothing; a release-on-tag or
`schedule` workflow never sees a PR at all.

The second version got this wrong in both directions and a delta audit caught
it: it regex-matched an `on:` line and then substring-searched the WHOLE FILE
for "push"/"pull_request". That counted release-on-tag (`on: push: tags:`), a
`workflow_dispatch` workflow with a job named `push-image`, and even one with
the word "push" in a COMMENT — while MISSING a real `pull_request` gate written
as `"on":`, the quoted spelling linters recommend because YAML 1.1 reads bare
`on` as the boolean True. Both failure directions are live hazards: a false
positive forces CLAUDE.md to assert a gate that does not exist, and a false
negative lets real CI land while the doc keeps saying nothing gates a merge.
It now PARSES the YAML (`pyyaml` is in `gatePyEnv`, flake.nix:94).

SCOPE — WHAT THIS CANNOT SEE, STATED SO NOBODY OVER-TRUSTS IT
--------------------------------------------------------------
It detects GitHub Actions workflows that RUN on pull requests. It does NOT
detect, and does not claim to:
  * whether such a run actually BLOCKS a merge — that is branch protection /
    rulesets / required checks, which are API-only and unavailable in the
    hermetic test sandbox;
  * Tekton triggers (they live in another repo entirely);
  * git hooks — `githooks/` ships a real blocking pre-push gate here, installed
    by pointing `core.hooksPath` elsewhere, which is per-clone and untracked, so
    no test in the sandbox can pin it.
`CLAUDE.md` names all three as not machine-checked. A guard whose advertised
reach exceeds its detector is how the original false claim happened; do not
widen the promise without widening the code.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent.parent
CLAUDE_MD = REPO / "CLAUDE.md"
WORKFLOW_DIR = REPO / ".github" / "workflows"

MARKER_RE = re.compile(r"<!--\s*merge-gate:\s*(?P<value>[a-z0-9-]+)\s*-->")
VALID_MARKERS = {"none", "github-actions"}

# Events that make a workflow run on a pull request. `push` is deliberately
# ABSENT: it fires after the merge and gates nothing.
GATING_EVENTS = {"pull_request", "pull_request_target", "merge_group"}

# The marker must sit on a line that still says something to a HUMAN. An HTML
# comment renders as nothing, so a lone marker would leave an always-loaded file
# showing the reader no warning at all while the suite stayed green — a delta
# audit found exactly that hole after the prose check was removed. A length
# floor, not a phrase: reword freely, just do not delete the sentence.
MARKER_LINE_MIN_VISIBLE = 40


def _marker_matches(body: str) -> list[re.Match]:
    return list(MARKER_RE.finditer(body))


def _marker_line(body: str, match: re.Match) -> str:
    start = body.rfind("\n", 0, match.start()) + 1
    end = body.find("\n", match.end())
    return body[start:end if end != -1 else len(body)]


def _workflow_files() -> list[Path]:
    """GitHub Actions only reads *.yml/*.yaml at the TOP level of this dir."""
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(
        p for p in WORKFLOW_DIR.iterdir()
        if p.is_file() and p.suffix in (".yml", ".yaml")
    )


def _on_events(doc: object) -> set[str]:
    """The event names a parsed workflow's `on:` declares.

    PyYAML resolves an unquoted `on` KEY to the boolean True (YAML 1.1), so both
    spellings must be looked up. Value may be a scalar, a list, or a mapping.
    """
    if not isinstance(doc, dict):
        return set()
    for key in (True, "on", "On", "ON"):
        if key in doc:
            spec = doc[key]
            break
    else:
        return set()
    if isinstance(spec, str):
        return {spec}
    if isinstance(spec, list):
        return {e for e in spec if isinstance(e, str)}
    if isinstance(spec, dict):
        return {k for k in spec if isinstance(k, str)}
    return set()


def _gating_workflows() -> list[Path]:
    """Workflows that RUN on a pull request. Unreadable files are LOUD."""
    out = []
    for p in _workflow_files():
        try:
            doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            # Never silently drop: an unparseable workflow is UNMEASURED, and
            # treating it as "not a gate" is precisely the reassuring zero this
            # whole test exists to prevent.
            raise AssertionError(
                f"cannot determine whether {p} gates a merge ({exc}). Fix the "
                "file or teach this test about it — do NOT let it read as "
                "'no gate'."
            ) from exc
        if _on_events(doc) & GATING_EVENTS:
            out.append(p)
    return out


def test_claude_md_exists_and_is_substantial() -> None:
    """POSITIVE CONTROL: without this, a moved/renamed CLAUDE.md would make
    every assertion below operate on an empty string and pass vacuously."""
    assert CLAUDE_MD.is_file(), f"CLAUDE.md not found at {CLAUDE_MD}"
    assert len(CLAUDE_MD.read_text(encoding="utf-8")) > 2000, (
        "CLAUDE.md is suspiciously small; the checks below would be near-vacuous"
    )


def test_the_detectors_can_actually_observe_things(tmp_path) -> None:
    """POSITIVE CONTROLS for every detector.

    A reassuring zero from `_gating_workflows()` is indistinguishable from a
    detector wired to nothing — this repo's own RULES.md names that trap, and an
    audit confirmed an earlier version could be neutered to `return []` and stay
    green WITH a real workflow present. Each case below is one the detector got
    WRONG at some point in this PR's history; they are regression coverage, not
    decoration.
    """
    assert VALID_MARKERS == {"none", "github-actions"}, (
        "VALID_MARKERS changed; a new value needs a branch in the assertion "
        "below, or it will be accepted while meaning nothing"
    )
    # Pin the guard's OWN constant. Without this, the cheapest way to silence a
    # failure is to lower the threshold — a mutation battery caught exactly
    # that: setting the floor to 0 let the whole warning be deleted and stayed
    # green. A constant a guard depends on is part of the guard.
    assert MARKER_LINE_MIN_VISIBLE >= 40, (
        f"MARKER_LINE_MIN_VISIBLE was lowered to {MARKER_LINE_MIN_VISIBLE}; at "
        "that value a bare marker with no human-readable warning passes, which "
        "is the hole this floor exists to close"
    )
    assert [m.group("value") for m in _marker_matches("a <!-- merge-gate: none --> b")] == ["none"]
    assert [m.group("value") for m in _marker_matches("<!-- merge-gate:github-actions-->")] == ["github-actions"]
    assert _marker_matches("no marker here") == []

    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    # DOES gate a PR
    (wf / "pr.yml").write_text("name: ci\non: [pull_request]\njobs: {}\n")
    (wf / "quoted.yml").write_text('name: ci\n"on":\n  pull_request:\njobs: {}\n')
    # Does NOT gate a merge, each a measured false positive of the old detector
    (wf / "tags.yml").write_text("name: rel\non:\n  push:\n    tags: ['v*']\njobs: {}\n")
    (wf / "dispatch.yml").write_text("name: x\non: workflow_dispatch\njobs:\n  push-image:\n    runs-on: x\n")
    (wf / "comment.yml").write_text("name: x\non: workflow_dispatch\n# run before you push\njobs: {}\n")
    (wf / "sched.yml").write_text("name: s\non:\n  schedule:\n    - cron: '0 0 * * *'\njobs: {}\n")
    # Ignored by GitHub Actions itself
    (wf / "notes.md").write_text("not a workflow\n")
    (wf / "nested").mkdir()
    (wf / "nested" / "ignored.yml").write_text("name: x\non: [pull_request]\n")

    global WORKFLOW_DIR
    original = WORKFLOW_DIR
    try:
        WORKFLOW_DIR = wf
        found = {p.name for p in _workflow_files()}
        gating = {p.name for p in _gating_workflows()}
    finally:
        WORKFLOW_DIR = original

    assert found == {"pr.yml", "quoted.yml", "tags.yml", "dispatch.yml",
                     "comment.yml", "sched.yml"}, (
        f"workflow detector wrong: {found} (must ignore non-YAML and "
        "subdirectories, which GitHub Actions itself ignores)"
    )
    assert gating == {"pr.yml", "quoted.yml"}, (
        f"gating detector wrong: {gating}. It must count ONLY workflows that "
        "run on a pull request — including the quoted `\"on\":` spelling — and "
        "must not be fooled by release-on-tag, a job named push-*, the word "
        "'push' in a comment, or a schedule."
    )


def test_claude_md_marker_matches_the_repo() -> None:
    """The load-bearing assertion, checked in BOTH directions."""
    body = CLAUDE_MD.read_text(encoding="utf-8")
    markers = _marker_matches(body)

    assert markers, (
        "CLAUDE.md has no `<!-- merge-gate: ... -->` marker. It is the "
        "machine-readable statement of what gates a merge here; without it the "
        "claim reverts to unverifiable prose, which is how "
        "'CI gates both suites' survived while no CI existed. Restore it."
    )
    assert len(markers) == 1, (
        f"CLAUDE.md has {len(markers)} merge-gate markers "
        f"({[m.group('value') for m in markers]}). Exactly one must be the "
        "claim — a second one (even inside a code fence, as an example) makes "
        "which is authoritative depend on position in the file."
    )
    marker = markers[0]
    value = marker.group("value")

    visible = MARKER_RE.sub("", _marker_line(body, marker)).strip()
    assert len(visible) >= MARKER_LINE_MIN_VISIBLE, (
        f"the merge-gate marker sits on a line with only {len(visible)} "
        f"visible characters ({visible!r}). An HTML comment renders as NOTHING, "
        "so this would leave readers of an always-loaded file with no warning "
        "at all while this test stayed green. Keep the sentence."
    )
    assert value in VALID_MARKERS, (
        f"unknown merge-gate marker {value!r}; expected one of {sorted(VALID_MARKERS)}"
    )

    gating = [p.name for p in _gating_workflows()]

    if gating:
        assert value == "github-actions", (
            f"CLAUDE.md's marker says {value!r}, but workflows that run on pull "
            f"requests now exist ({gating}). A stale 'nothing gates a merge' "
            "warning trains readers to ignore this file — update the marker and "
            "the paragraph."
        )
    else:
        assert value == "none", (
            f"CLAUDE.md's marker says {value!r}, but no GitHub Actions workflow "
            f"in {WORKFLOW_DIR} runs on a pull request. An agent reading that "
            "believes the merge is protected and skips running the suite. "
            "Either add real CI, or set the marker to 'none'."
        )
