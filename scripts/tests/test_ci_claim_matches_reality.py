"""CLAUDE.md's merge-gate marker must match what the repo actually has.

WHY THIS EXISTS
---------------
From 2026-08-02 to 2026-08-20, `CLAUDE.md` asserted "**CI gates both suites**:
`nix build .#checks…`" while nothing ran at all. Every merge rested on a human
or agent running the suite by hand.

🔴 THIS PARAGRAPH THEN BECAME THE THING IT WARNS ABOUT, and is corrected in
place rather than deleted, because the drift is the lesson. It used to continue:
"No CI has ever run on a devrc PR: no `.github/workflows`, no branch protection,
no ruleset, no Tekton trigger, and `statusCheckRollup` is empty on every PR."
**Three of those five clauses were measured FALSE on 2026-08-22** — Tekton posts
`tekton/devrc-pytests` and `tekton/devrc-nodetests` on devrc PRs, `main` DOES
carry branch protection, and `statusCheckRollup` is non-empty. Still true: no
`.github/workflows`, and no ruleset.

🔴 AND IT DRIFTED AGAIN, this time in the REASSURING direction. This paragraph
used to continue: "the protection carries **no `required_status_checks`**:
checks RUN, nothing BLOCKS", evidenced by #707 merging **28 minutes** after its
`devrc-pytests` went RED and #711 two minutes after. **Measured FALSE on
2026-08-23**: `required_status_checks.contexts` is
`["tekton/devrc-nodetests","tekton/devrc-pytests"]`, `strict: false`,
`enforce_admins: true` — BOTH tiers block, so a Python-only change is gated too.

⚠ It briefly listed nodetests ALONE (same day, corrected within the hour), and
that one-element state is worth remembering rather than deleting: nodetests
collects `*.test.mjs` ONLY, so a Python-only change could not fail the required
check at all, yet the setting read as "protected". "Partially blocked" is the
shape that reads as "blocked" — which is why the re-measure below says to read
the LIST, not whether the key exists.

The marker was `other` before that change and is `other` after, because it
encodes what REPORTS, not what BLOCKS: something this test cannot see (Tekton)
runs here, and it would become `github-actions` only if a `pull_request`
workflow appeared. Branch protection is invisible to this test in BOTH states —
see SCOPE below — so nothing in this file verifies the paragraph above, and a
green run here is not evidence for it. Re-measure it.

Re-measure both surfaces rather than quoting any of the above:
    gh api repos/innovation-upstream/devrc/branches/main/protection   # required_status_checks?
    gh api repos/innovation-upstream/devrc/rules/branches/main        # org+repo+enterprise rulesets
On the first the key is PRESENT today, so read the `contexts` LIST and `strict`,
not merely whether the key exists — a one-element list is a PARTIAL gate, and an
absent key would read as clean unless you looked for it by name. On the second
the answer is an empty array. Positive-control the `[]` before believing it —
the same call against a repo with rulesets returns non-empty.

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
# `other` exists so this test can never FORCE a false claim: if a Tekton trigger
# or an installed `githooks/` pre-push gate ever starts gating devrc, neither of
# which this test can see, the honest marker is `other` — and a check that
# accepted only {none, github-actions} would demand a machine-readable
# "nothing gates a merge" that is untrue. A guard must not compel the very
# falsehood it exists to prevent.
VALID_MARKERS = {"none", "github-actions", "other"}

# Events that make a workflow run before a merge lands. `push` is deliberately
# ABSENT: it fires AFTER the merge and gates nothing.
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
    # Only two spellings are reachable: PyYAML resolves unquoted `on:`, `On:`
    # and `ON:` all to the boolean True, and GitHub Actions requires the key
    # lowercase, so a quoted `"On":` is a file GitHub rejects. Listing more
    # spellings would advertise coverage that does not exist.
    for key in (True, "on"):
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


def _acceptable_markers(gating: bool) -> set[str]:
    """Which marker values are honest, given detected GitHub Actions gating.

    Extracted as a PURE function on purpose. `.github/workflows` is empty in
    this repo, so the `gating=True` branch can never execute against the real
    tree — an audit confirmed that inverting the comparison inline survived a
    fully green suite. That is the dangerous direction (real CI lands while the
    always-loaded doc keeps saying nothing gates a merge), so the comparison
    must be reachable from a fixture. See test_the_marker_comparison_is_correct.
    """
    if gating:
        return {"github-actions"}
    # No GitHub Actions gate. The marker must not CLAIM one — but `other` stays
    # legal, because Tekton or an installed githooks pre-push gate are invisible
    # here and forbidding them would force a false `none`.
    return {"none", "other"}


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
    assert VALID_MARKERS == {"none", "github-actions", "other"}, (
        "VALID_MARKERS changed; a new value needs a branch in "
        "_acceptable_markers, or it will be accepted while meaning nothing"
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
    # DOES gate. Every entry is a spelling a mutation sweep found UNCOVERED —
    # dropping merge_group, pull_request_target, the scalar branch, or the
    # .yaml suffix each survived a fully green suite before these existed.
    (wf / "pr.yml").write_text("name: ci\non: [pull_request]\njobs: {}\n")
    (wf / "quoted.yml").write_text('name: ci\n"on":\n  pull_request:\njobs: {}\n')
    (wf / "scalar.yaml").write_text("name: ci\non: pull_request\njobs: {}\n")
    (wf / "queue.yml").write_text("name: ci\non:\n  merge_group:\njobs: {}\n")
    (wf / "fork.yml").write_text("name: ci\non: [pull_request_target]\njobs: {}\n")
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

    assert found == {"pr.yml", "quoted.yml", "scalar.yaml", "queue.yml",
                     "fork.yml", "tags.yml", "dispatch.yml", "comment.yml",
                     "sched.yml"}, (
        f"workflow detector wrong: {found} (must read BOTH .yml and .yaml, and "
        "ignore non-YAML plus subdirectories, which GitHub Actions itself "
        "ignores)"
    )
    assert gating == {"pr.yml", "quoted.yml", "scalar.yaml", "queue.yml",
                      "fork.yml"}, (
        f"gating detector wrong: {gating}. It must count every spelling that "
        "runs before a merge — list, quoted `\"on\":`, bare scalar, "
        "merge_group, pull_request_target — and must not be fooled by "
        "release-on-tag, a job named push-*, 'push' in a comment, or a schedule."
    )


def test_an_unreadable_workflow_is_loud_not_a_silent_no_gate(tmp_path) -> None:
    """REGRESSION coverage for the fix that made read failures raise.

    Before it, an unreadable workflow was `continue`d past — so a file that
    genuinely declared `on: [pull_request]` read as "no gate" and the suite
    printed a reassuring green. Reverting to a silent `continue` survived every
    other test in this file, which is why this one exists separately.
    """
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "bad.yml").write_bytes(b"name: \xff\xfe\non: [pull_request]\n")

    global WORKFLOW_DIR
    original = WORKFLOW_DIR
    try:
        WORKFLOW_DIR = wf
        try:
            _gating_workflows()
        except AssertionError as exc:
            assert "bad.yml" in str(exc), (
                f"the failure must NAME the file it could not read: {exc}"
            )
        else:
            raise AssertionError(
                "an unreadable workflow was swallowed and read as 'no gate' — "
                "the reassuring zero this test exists to prevent"
            )
    finally:
        WORKFLOW_DIR = original


def test_the_marker_comparison_is_correct_in_both_directions() -> None:
    """`.github/workflows` is empty here, so the gating=True arm NEVER runs
    against the real tree — an audit showed inverting the comparison inline
    survived a fully green suite. Exercise the pure function directly."""
    assert _acceptable_markers(True) == {"github-actions"}, (
        "with a GitHub Actions gate detected, only 'github-actions' is honest"
    )
    assert _acceptable_markers(False) == {"none", "other"}, (
        "with no GitHub Actions gate, 'github-actions' must be rejected — but "
        "'other' must stay legal, or a future Tekton/githooks gate would be "
        "forced to declare 'none', a falsehood this test would be creating"
    )
    # The property that matters, stated so an inversion cannot pass:
    assert "github-actions" not in _acceptable_markers(False)
    assert "none" not in _acceptable_markers(True)


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
    acceptable = _acceptable_markers(bool(gating))

    assert value in acceptable, (
        f"CLAUDE.md's merge-gate marker says {value!r}, but "
        + (
            f"workflows that run before a merge now exist ({gating}), so only "
            f"{sorted(acceptable)} is honest. A stale 'nothing gates a merge' "
            "warning trains readers to ignore this file — update the marker "
            "and the paragraph."
            if gating else
            f"no GitHub Actions workflow in {WORKFLOW_DIR} runs before a merge, "
            f"so only {sorted(acceptable)} is honest. An agent reading a CI "
            "claim believes the merge is protected and skips running the "
            "suite. Add real CI, or set the marker to 'none' (or 'other' if "
            "something this test cannot see — Tekton, an installed githooks "
            "pre-push gate — now gates it)."
        )
    )
