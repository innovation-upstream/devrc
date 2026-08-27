"""Deterministic byte-size gate for `claude/skills/prune-skill/SKILL.md`.

Lives in `scripts/tests/` rather than beside the skill, mirroring
`scripts/tests/test_rules_size.py` which gates `claude/RULES.md` the same way.
Two reasons: `scripts/tests` is already a HERMETIC_TARGET, and every hermetic
target must start with `scripts/` -- an invariant asserted by
`test_run_tests_targets.py` as a POSITIVE CONTROL on its own parser, so bending
it to admit a `claude/` target would weaken a vacuity guard. Keeping the test
here also stops it shipping into `~/.claude/skills/prune-skill/` via
`home.file recursive`, where it would be dead weight in the deployed tree.

WHY THIS EXISTS
---------------
`prune-skill` is the skill that tells everyone else to hold a byte budget, and it
had no enforcement of its own. It grew 11,083 -> 14,918 B in a single change that
added twelve rules (`git cat-file -s cebbd6d:claude/skills/prune-skill/SKILL.md`,
and that change's own commit message), and came back down only by moving whole
blocks out to sidecars. A prose budget did not hold here either -- the same
failure `scripts/browser-bridge/tests/test_skill_size.py` documents for the
browser skill.

`claude/RULES.md`: "Prefer deterministic/structural fixes over prompt-tuning,
prose instructions, or suffix/keyword heuristics." This file is that fix. The
numbers below are the SINGLE source of truth for this skill's ceiling -- any other
mention (the skill body, a handoff doc, a PR description) must cross-reference
this module rather than restate the literal, because a second hand-maintained copy
is exactly how the drift regrows.

THE CEILING IS NOW THE TARGET — THE EXEMPTION IS RETIRED (2026-08-27)
---------------------------------------------------------------------
The skill states a 12,288 B target and browser-bridge MEETS it while routing ~11x
its own weight, so the target is achievable and is not in dispute.
This file did not, and said so: it sat 524 B over that bar, pinned by a ceiling of
13,056. A further pass demoted the budget rationale and §3's classification
sub-rules into two more sidecars (`budgets-and-scope.md`,
`classification-rules.md`); it sits at 11,953 B (11.67 KiB) now, inside the shared
12,038 B enforced budget for the first time. MAX_BYTES is therefore lowered to the
TARGET itself (11,953 against 12,288; the margin is 335 B), which is the direction
of travel this docstring already named as intended.

The slack is genuinely thin (11,953 against a 12,096 B effective floor), and that
is the honest position rather than a comfortable one: the next addition here has
to evict something. That is the same contract this skill imposes on every file it
prunes, and it no longer imposes it from above the line.

(The browser-bridge sentence states no byte count on purpose. That count belongs
to ANOTHER skill's file: restating it here made this gate red for someone else's
cosmetic churn, and -- worse -- walkable by a one-number edit. The claim it
carries is a RELATIONSHIP, and is gated as one; see `BB_CLAIM` below.)

The residual is the five VERDICT NAMES in §3 and the rule names in §0/§7. Their
sub-rules are now behind `classification-rules.md`; the names themselves stay,
because §3 is consulted at the moment of the decision and putting the taxonomy
behind a load is the same defect as burying a rule in a table cell.

This ceiling stays a RATCHET and an INDEPENDENT constant -- it is numerically the
target, not imported from it, because the BB_CLAIM assertion below exists
precisely to watch the seam where browser-bridge's own ceiling could be raised.
Lowering as the file gets leaner remains the direction of travel; raising it needs
the same kind of justification recorded above.

Every number in this docstring is re-measured, not carried forward: an earlier
revision restated a size, a growth figure, a percentage and a per-pass byte ledger
that were all wrong, in the module that declares itself the single source of truth
for them.
"""
import importlib.util
import os
import re
from pathlib import Path

import pytest

# The hard ceiling: SKILL.md must never exceed this many bytes.
#
# NOT a derivation -- a measured position, now equal to the 12,288 B target
# rather than above it. SKILL.md is 11,953 B (`stat -c %s` and `git cat-file -s`
# agree), so 12,288 leaves 335 B of headroom, of which MIN_HEADROOM_BYTES (192)
# is the floor that must remain: 143 B of true working room before the headroom
# test fires -- barely any; the next edit here will have to evict something. That
# is deliberate and is the same contract the skill imposes on its subjects.
# Re-measure before touching this number, and lower it as the file gets leaner --
# never raise it. (Was 13,056 while the body was 12,812 B and over target.)
MAX_BYTES = 12_288

# Required working margin below the ceiling. A file sitting one byte under
# technically holds the line but leaves no room for a one-line correction, which
# is the exact position browser-bridge was re-breached from three times in a day.
#
# Sized in units of a REAL edit rather than a round number, and re-measured
# against the current file rather than restated: the two structures that actually
# grow here are the reference routing table (5 rows, 722 B -> mean 144 B/row) and
# §3's verdict bullets (9 lines, 1,739 B -> mean 193 B). 192 B is therefore
# ~one mean §3 bullet, or one routing row with room to spare -- enough that the
# headroom test fires BEFORE the ceiling rather than arriving alongside it.
#
# It is NOT two mean routing rows: that claim was here, and at the real 144 B/row
# two rows are 288 B > 192. Kept at 192 on the measurement that does hold rather
# than raised to fit a sentence.
MIN_HEADROOM_BYTES = 192

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_DIR = REPO_ROOT / "claude" / "skills" / "prune-skill"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCE_DIR = SKILL_DIR / "reference"

# A routing PATH the core writes, captured WHOLE -- a maximal run of path
# characters holding a `reference/` segment. The run ends at a backtick, a
# paren (so a markdown link `[t](reference/x.md)` yields the bare path), a
# quote or whitespace, and it keeps everything the author wrote on BOTH sides
# of `reference/`.
#
# Capturing the whole token is the entire point. The previous shape,
# `reference/([\w.-]+\.md)`, matched anywhere inside a token and kept only the
# BASENAME, so four spellings that resolve to nothing were accepted -- each one
# executed against that form, each reporting `4 passed`:
# `reference/reference/x.md`, `~/.claude/skills/<other>/reference/x.md`,
# `reference/x.mdx` (backtracks to a `.md` prefix of the real token) and
# `reference/x.md.bak`.
#
# A placeholder (`reference/<topic>.md`) still does not match as a route: `<`
# is not a path character, so the token ends at `reference/`, and _routing_paths
# drops a token whose last segment has no dot.
ROUTING_PATH = re.compile(r"[\w~@./+-]*reference/[\w~@./+-]*")

# What a routing path's PREFIX says its base is. `~/.claude/` is the DEPLOYED
# tree, which nix populates recursively from this repo's `claude/` (the
# `.claude/skills` home.file entry in `nix/home.nix`), and `~/workspace/devrc/`
# is this repo's root -- so both absolute spellings are settled IN-TREE. Nothing
# here reads the deployed copy: the gate must give the same verdict in a fresh
# clone, in a worktree, and on a host that has never run `home-manager switch`.
# That claim is only true because `_under_repo` CONTAINS every resolution below
# it: an UNMAPPED absolute path (`/home/<user>/.claude/...`, `/etc/...`) used to
# be stat'd where it pointed and passed on a host that had deployed, which is
# exactly the host-dependence this comment denies. `_under_repo` is what makes
# it hold; a test pins it (`test_deployed_spelling_is_never_stat_where_it_points`).
# The two mkOutOfStoreSymlink skills (`browser`, `dl-router`) are deployed from
# `scripts/` instead and are deliberately NOT mapped -- this gate settles
# prune-skill's own routes, and a route into one of those would read as dangling.
PREFIX_BASES = (
    ("~/workspace/devrc/", REPO_ROOT),
    (f"{Path.home() / 'workspace' / 'devrc'}/", REPO_ROOT),
    ("~/.claude/", REPO_ROOT / "claude"),
    (".claude/", REPO_ROOT / "claude"),
)

# The block the core uses as its reference registry. Its lines are what a reader
# loads from, so a line that stops being a path is a real break even when the
# basename still appears in prose elsewhere.
REGISTRY_MARKER = "**Reference topics**"


def _routing_paths(text: str) -> list[str]:
    """Every routing path in `text`, AS WRITTEN, de-duplicated in order.

    A token counts as a route when `reference` is a whole path SEGMENT of it
    (so `cross-reference/x.md` is prose, not a route) and its last segment
    holds a dot (so the bare directory `~/.claude/skills/prune-skill/reference/`
    and the placeholder `reference/<topic>.md`, whose token ends at `<`, are
    not routes). Trailing sentence punctuation is stripped -- no file ends in
    one -- so `see reference/x.md.` is the same route as `` `reference/x.md` ``.

    A token preceded by `:` is a URL's authority, not a repo path: `:` is not a
    path character, so `https://host/x/reference/y.md` reaches here as
    `//host/x/reference/y.md`, and joining that onto a base yields an absolute
    `//host/...` -- a dangling route reported for a link that was never a claim
    about this tree.
    """
    found = []
    for match in ROUTING_PATH.finditer(text):
        if match.start() and text[match.start() - 1] == ":":
            continue
        token = match.group(0).rstrip(".,;:!?")
        segments = token.split("/")
        if "reference" not in segments[:-1] or "." not in segments[-1]:
            continue
        found.append(token)
    return list(dict.fromkeys(found))


# The segment an off-tree route is redirected onto. No committed tree contains
# it, so a route that would otherwise resolve outside the repo dangles on EVERY
# host instead of borrowing a verdict from whatever sits next to the checkout.
OFF_TREE_MARKER = "<outside-this-repo>"


def _under_repo(base: Path, rel: str) -> Path:
    """`rel` joined under `base`, guaranteed to stay inside the repo tree.

    Both steps are load-bearing for HERMETICITY -- the gate must give the same
    verdict in a fresh clone, in a worktree and on a host that has never run
    `home-manager switch`:

      * the leading `/` of an absolute token is stripped before joining, because
        `base / "/abs/path"` DISCARDS `base` and returns the token verbatim.
        That is how `/home/<user>/.claude/skills/prune-skill/reference/<x>.md`
        was stat'd on the REAL filesystem. Both arms executed on one host, same
        code: the deployed user's spelling came back GREEN (that file exists
        here) and `/home/example-user/...` RED -- the verdict was a fact about
        the machine, not about the repo, so it would flip in a fresh clone, in
        CI, or before `home-manager switch`. An unmapped absolute now lands at a
        nonexistent IN-TREE location;
      * a `..` run that normalises ABOVE the repo root is redirected onto
        OFF_TREE_MARKER. `../prune-skill/reference/<x>.md` resolved to
        `<parent-of-checkout>/prune-skill/...` -- outside the repo entirely.

    So nothing outside the repo tree is ever stat'd, whatever the token says.
    """
    joined = Path(os.path.normpath(os.path.join(str(base), rel.lstrip("/"))))
    if joined != REPO_ROOT and REPO_ROOT not in joined.parents:
        # `..` segments are dropped rather than carried into the returned path:
        # the OS resolves `..` at stat time, so keeping them would let the
        # escape complete if a directory named OFF_TREE_MARKER ever existed.
        tail = [seg for seg in rel.split("/") if seg not in ("", ".", "..")]
        return REPO_ROOT.joinpath(OFF_TREE_MARKER, *tail)
    return joined


def _resolve_routing_path(token: str) -> Path:
    """Where the string the core wrote actually points -- resolved AS WRITTEN.

    Each spelling is resolved against the base its own prefix names, never by
    basename:

      * `~/.claude/...` and `.claude/...`  -> this repo's `claude/` tree;
      * `~/workspace/devrc/...`            -> this repo's root;
      * a first segment that is a directory at the repo root
        (`claude/skills/<name>/reference/<x>.md`) -> the repo root;
      * anything else (`reference/<x>.md`) -> the skill's OWN directory, which
        is what a reader following the core from there opens.

    So a wrong prefix, a wrong skill segment or a wrong extension resolves to
    a path that does not exist, instead of collapsing onto a basename that does.

    `.` and `..` are excluded from the repo-root head test on purpose: they are
    directories at the repo root TRIVIALLY (`(REPO_ROOT / "..").is_dir()` is
    always True), so they used to be resolved against REPO_ROOT and turned two
    CORRECT pointers -- `./reference/<x>.md` and `../<skill>/reference/<x>.md`
    -- red. A gate that reds on correct input trains people to bypass it. They
    are ordinary relative routes and resolve against the skill's own directory.

    An unmapped absolute path also falls through to the last case, where
    `_under_repo` keeps the base rather than letting pathlib discard it: it
    resolves to a nonexistent in-tree path and is reported dangling, on every
    host. It is never stat'd where it points.
    """
    for prefix, base in PREFIX_BASES:
        if token.startswith(prefix):
            return _under_repo(base, token[len(prefix):])
    head = token.split("/", 1)[0]
    if head not in ("", ".", "..") and (REPO_ROOT / head).is_dir():
        return _under_repo(REPO_ROOT, token)
    return _under_repo(SKILL_DIR, token)


def dangling_routes_in(body: str, resolve) -> list[str]:
    """Direction 1 of the gate: routing paths in `body` that resolve to no file.

    Factored out so the probes at the bottom of this module grade THE GATE
    rather than a re-implementation of it. A probe that rebuilt this pipeline
    out of `_routing_paths` and `_resolve_routing_path` by hand would stay green
    against a mutation of the wiring between them.

    🔴 AND PARAMETERISED BY `resolve`, NOT BOUND TO THIS SKILL. A second
    size-gate module (`test_session_manager_skill_size.py`) gates a different
    skill through the same tokenizer with its own resolver base, and it started
    life with the pipeline open-coded inside each assertion AND again inside
    each probe. Measured on that module before this refactor: forcing the
    verdict to a constant (`dangling = sorted(set())`, `unrouted = []`) left all
    nine of its routing tests GREEN -- the probes graded a copy, so the gate
    could be deleted without a red. One rule, one place: both modules now bind
    their own `resolve` to this body.
    """
    return sorted({t for t in _routing_paths(body) if not resolve(t).is_file()})


def unrouted_topics_in(registry: str, topics, reference_dir: Path, resolve) -> list[str]:
    """Direction 2 of the gate: topics on disk the registry does not route to.

    Takes the ALREADY-EXTRACTED registry block rather than the whole body: each
    skill locates its own registry (different marker, different terminator), and
    that extraction is the one part of the pipeline that is genuinely per-skill.
    """
    routed = {resolve(t) for t in _routing_paths(registry)}
    return [t for t in topics if reference_dir / t not in routed]


def _dangling_routes(body: str) -> list[str]:
    """This skill's binding of `dangling_routes_in`."""
    return dangling_routes_in(body, _resolve_routing_path)


def _unrouted_topics(body: str) -> list[str]:
    """This skill's binding of `unrouted_topics_in`."""
    return unrouted_topics_in(
        _registry_block(body), _existing_topics(), REFERENCE_DIR, _resolve_routing_path
    )


def _existing_topics() -> list[str]:
    """Reference topics that exist RIGHT NOW, read off the filesystem.

    Globbed rather than hard-coded: browser-bridge's hand-maintained literal
    drifted to 8 of 11 topics and steered a maintainer into creating a duplicate
    topic for content that already had a home. A hard-coded list regrows that bug
    on the next reference file added; this cannot.
    """
    return sorted(p.name for p in REFERENCE_DIR.glob("*.md"))


def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"{SKILL_MD} is missing -- did the skill move?"


def test_skill_md_under_hard_ceiling():
    size = SKILL_MD.stat().st_size
    assert size <= MAX_BYTES, (
        f"SKILL.md is {size:,} B, over the {MAX_BYTES:,} B ceiling by "
        f"{size - MAX_BYTES:,} B.\n"
        "Do NOT raise MAX_BYTES to make this pass. Demote to reference/ instead "
        "-- and per the skill's own §5, by VERBATIM LINE-RANGE SLICING, then run "
        "its §7 verification (gap audit + >=5-population survival check) over the "
        "result. Rewording does not work: what has ever moved this file is moving "
        "whole blocks out to reference/ -- 14,918 -> 12,834 B in #531. (A per-pass "
        "byte ledger used to be quoted here; it reconciled with no pair of "
        "committed blobs and its intermediates were never committed, so it is "
        "dropped rather than guessed at.)"
    )


def test_skill_md_keeps_working_headroom():
    """Fire BEFORE the ceiling, so a breach is never a surprise."""
    size = SKILL_MD.stat().st_size
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"SKILL.md is {size:,} B, leaving only {headroom:,} B under the "
        f"{MAX_BYTES:,} B ceiling -- below the {MIN_HEADROOM_BYTES:,} B working "
        "floor. The next routine edit will breach it. Evict now, while there is "
        "still room to do it deliberately."
    )


def _registry_block(body: str) -> str:
    """The core's reference registry -- the block a reader loads from.

    Located structurally rather than by column names: everything from the
    `**Reference topics**` marker to the next H2. If the marker is gone the
    registry moved, and that is a loud failure by design -- re-point this test
    at the new registry rather than deleting the assertion.

    The WHOLE block counts, not only markdown table rows. The skill's own §3
    asks for "ONE routing line" per demoted topic, which is a bullet as often as
    a table row, and a rows-only reader reported `Routed by the table: NOTHING`
    for a registry rewritten as bullets while every route in it resolved. What
    makes a line a route is that it holds a path that RESOLVES -- checked below
    -- not the punctuation around it.
    """
    start = body.find(REGISTRY_MARKER)
    assert start != -1, (
        f"{REGISTRY_MARKER!r} not found in {SKILL_MD}. This test pins that block as "
        "the core's reference registry; if the registry moved, re-point the test."
    )
    end = body.find("\n## ", start)
    return body[start:] if end == -1 else body[start:end]


def figure_problems_in(src: str, checks) -> list[str]:
    """Cross-check every `(label, pattern, expected)` figure against `src`.

    Extracted from the test body so each of the three FIRING arms -- no match,
    more than one match, and a captured value that differs from the measured one
    -- can be driven by a planted positive control instead of waiting for the
    live corpus to go dirty. The live gate below calls this with the module's own
    source; the controls call it with synthetic text. Pure: no filesystem, no
    globals, so a control's input is exactly what the caller passes.
    """
    problems = []
    for label, pattern, expected in checks:
        found = re.findall(pattern, src)
        if not found:
            problems.append(f"{label}: PATTERN DID NOT MATCH ({pattern!r}) — the "
                            f"sentence was reworded, so this figure is now unchecked")
        elif len(found) > 1:
            problems.append(f"{label}: pattern matched {len(found)}x ({found}) — an "
                            "added sentence can shadow the live figure; make the "
                            "pattern name exactly one sentence")
        elif found[0] != expected:
            problems.append(f"{label}: prose says {found[0]}, measured {expected}")
    return problems


def bb_claim_problems_in(src: str, bb: int, target: int, bb_claim: str) -> list[str]:
    """The browser-bridge pair: the sentence is present verbatim, and it is TRUE.

    Two separate guards, deliberately kept together because they are two halves
    of one claim -- the wording pin cannot see a false sentence, and the
    `bb > target` measurement cannot see a reworded one. Extracted for the same
    reason as `figure_problems_in`: both arms are otherwise only reachable by
    dirtying the real corpus.
    """
    problems = []
    if " ".join(src.split()).count(bb_claim) != 1:
        problems.append(
            "browser-bridge claim: the sentence asserting that browser-bridge "
            f"meets the target is not present exactly once as written.\n    "
            f"Expected verbatim (whitespace-normalised):\n      {bb_claim}\n    "
            "A reword makes this figure unchecked, so restore the wording or "
            "re-point this pin deliberately."
        )
    if bb > target:
        problems.append(
            f"browser-bridge claim: prose says browser-bridge MEETS the "
            f"{target:,} B target, but it measures {bb:,} B -- over by "
            f"{bb - target:,} B. The sentence is now FALSE, and this module's "
            "whole 'the target is achievable and is not in dispute' argument "
            "rests on it. Fix browser-bridge or rewrite the argument -- do NOT "
            "restate the new number, which is how the literal form of this "
            "check certified the same falsehood."
        )
    return problems


def test_this_modules_own_stated_figures_are_re_measured():
    """🔴 THE DOCSTRING IS A CLAIM, AND PROSE COULD NOT HOLD IT.

    This module declares itself the single source of truth for these numbers and
    asserts "Every number in this docstring is re-measured, not carried forward".
    THREE CONSECUTIVE audit rounds found it restating a stale one anyway — each
    time by the same mechanism: an edit moved SKILL.md *after* the derived
    figures were written, so the arithmetic described a size the file no longer
    had. Round 2 fixed a figure and left another; round 3 fixed those and the
    round's own §4 edit shrank the file 2 B, staling five more.

    A fourth hand-fix would be the fourth instance of one defect. This is the
    deterministic replacement: derive every figure the prose states, and require
    the prose to contain it. It cannot go stale silently, and it cannot go
    VACUOUS either — each pattern must MATCH, so a reworded sentence fails loudly
    instead of quietly checking nothing.

    Cost, stated: a cosmetic reword of these sentences fails this test. That is
    the trade — a machine-checkable claim for a bit of prose rigidity.

    🔴 Three refinements a round-4 audit forced, each closing a way this gate
    could have been decorative:
      - THE TARGET IS IMPORTED, NOT RESTATED. It used to hard-code 12,288 —
        which made the check then labelled "skill-audit cross-check" (retired
        2026-08-27 with the exemption) pin the sentence
        against this test's OWN copy of the number rather than against the tool
        it names. Setting `TARGET` in skill-audit.py to 12,000 made the tool
        print 859 while the prose still claimed 571, and this gate said PASSED.
        That is the exact defect (F2) the gate was written to close, reproduced
        inside its own fix.
      - EXACTLY ONE MATCH IS REQUIRED, not the first. `re.search` takes the
        earliest hit, so an added sentence quoting a HISTORICAL figure shadows
        the live one — and this module already quotes historical figures in
        near-identical phrasing two lines below `MAX_BYTES`. Proven: a decoy
        sentence let a 99,999 B claim ship green.
      - COVERAGE IS ENUMERATED. The first version gated 6 of ~12 numeric claims;
        corrupting the other six left the suite green, and three of those six
        were the exact shapes rounds 2 and 3 found stale (a percentage beside a
        re-measured byte count, a second literal in the same sentence, the
        routing-table arithmetic). Every figure below is now derived. Any figure
        deliberately NOT gated must be named in `UNGATED` with its reason, so a
        gap is a declaration rather than a silence.
    """
    src = Path(__file__).read_text()
    size = SKILL_MD.stat().st_size

    # The canonical target lives in the tool this docstring cites. Importing it
    # is what makes "skill-audit.py prints the same N" a real cross-check.
    spec = importlib.util.spec_from_file_location(
        "_sa_target", REPO_ROOT / "scripts" / "skill-audit.py")
    _sa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_sa)
    target = _sa.TARGET
    # The shared ENFORCED budget (ceiling - devrc's working margin). The
    # docstring cites it, so it is gated like every other figure rather than
    # left as a fourth hand-maintained copy.
    shared_budget = _sa.BUDGET

    over = size - target
    headroom = MAX_BYTES - size
    slack = headroom - MIN_HEADROOM_BYTES
    eff_floor = MAX_BYTES - MIN_HEADROOM_BYTES   # this module's own effective floor
    rows = [ln for ln in SKILL_MD.read_text().splitlines()
            if ln.startswith("| ") and "reference/" in ln and "Load it when" not in ln]
    row_bytes = sum(len(ln.encode()) + 1 for ln in rows)
    bb = (REPO_ROOT / "scripts" / "browser-bridge" / "SKILL.md").stat().st_size

    # Figures deliberately left hand-maintained, named so the gap is DECLARED.
    # 🔴 A round-5 audit found this dict INCOMPLETE on its first outing: six
    # numeric claims were neither gated nor named, and corrupting all six at once
    # left the suite green. The contract this dict exists to keep is that a gap is
    # a DECLARATION, never a silence — so an incomplete UNGATED is worse than none,
    # because it advertises a completeness it does not have. Five of the six are
    # now gated below; the remainder is named here with its reason.
    UNGATED = {
        "§3 verdict-bullet mean (9 lines, 1,739 B, mean 193)":
            "extracting '§3 verdict bullets' needs a heading-and-bullet parser whose "
            "own drift would be invisible; the figure only sizes MIN_HEADROOM_BYTES, "
            "which this gate pins directly via 'true working room'",
        "browser-bridge routes '~11x its own weight'":
            "13.55x today counting reference/**/*.md, i.e. recursively including its "
            "sites/ subdir; one level only gives 12.29x — the figure MOVES with the "
            "method, which is why it is declared here rather than restated. Deriving it "
            "means summing that skill's whole "
            "reference/ tree, which makes this gate depend on a SECOND skill's layout; "
            "the load-bearing half of that sentence (browser-bridge MEETS the target) "
            "is gated as a relationship by BB_CLAIM + the bb <= target assertion "
            "below, and the phrase itself can no longer be reworded away because the "
            "whole normalised sentence is pinned",
    }

    # (label, regex with ONE capturing group, expected literal)
    checks = [
        ("size in the docstring",    r"it sits at ([\d,]+) B",                    f"{size:,}"),
        ("size in KiB",              r"it sits at [\d,]+ B \(([\d.]+) KiB\)",     f"{size / 1024:.2f}"),
        ("size in the ceiling note", r"SKILL\.md is ([\d,]+) B \(`stat -c %s`",   f"{size:,}"),
        # 5 specs retired 2026-08-27 with the over-target exemption: the sentences
        # they pinned no longer exist. The two figures those sentences carried that
        # ARE still printed are re-gated in the three below, so retiring a spec never
        # means un-gating a number the docstring still states.
        ("size in the accounting",   r"\(([\d,]+) against [\d,]+; the margin", f"{size:,}"),
        ("the target it cites",      r"\([\d,]+ against ([\d,]+); the margin", f"{target:,}"),
        ("margin in the accounting", r"the margin is ([\d,]+) B\)",             f"{headroom:,}"),
        ("headroom",                 r"leaves ([\d,]+) B of headroom",            f"{headroom:,}"),
        ("true working room",        r"must remain: ([\d,]+) B of true working",  f"{slack:,}"),
        # 🔴 FOUR FIGURES THE #924 PRE-MERGE AUDIT CORRUPTED WITH THE SUITE GREEN.
        # Three were text this same PR introduced, so the round that added prose
        # also added the gap — which is why UNGATED is a contract and not a note.
        # The line-41 size is the "second literal restating a gated figure" shape
        # the comment below already names: bump SKILL.md by a byte, fix the specs
        # that go red, and an ungated copy stays stale and green.
        # NB: anchored on the tail, not on "the shared " — the sentence WRAPS between
        # those two words, so a pattern spanning the break silently never matches
        # and reports itself as "reworded". The harness said so; it was not guessed.
        ("shared budget in the docstring", r"([\d,]+) B enforced budget", f"{shared_budget:,}"),
        ("size, restated at the floor", r"\(([\d,]+) against a [\d,]+ B effective floor\)", f"{size:,}"),
        ("effective floor",          r"against a ([\d,]+) B effective floor\)",   f"{eff_floor:,}"),
        ("target in the ceiling note", r"now equal to the ([\d,]+) B target",     f"{target:,}"),
        ("routing-table bytes",      r"routing table \(5 rows, ([\d,]+) B",       f"{row_bytes:,}"),
        ("routing-table mean",       r"routing table \(5 rows, [\d,]+ B -> mean ([\d,]+) B/row", f"{row_bytes // len(rows):,}"),
        # NOTE: browser-bridge is NOT gated by a restated byte count any more.
        # See BB_CLAIM below for the walk that made the literal indefensible.
        # The hand-maintained restatements of the target, in the module whose
        # headline fix was "it pinned the cross-check against its own copy of the
        # number". Each is a separate sentence, so each needs its own anchor. Two
        # of the three were retired with the exemption (2026-08-27); count the
        # specs below rather than trusting a total written beside them.
        ("target in the preamble",   r"The skill states a ([\d,]+) B target",     f"{target:,}"),
        # A SECOND copy of the routing-table mean, and a figure derived from it.
        # This is the "second literal restating a gated figure" shape that rounds
        # 2 and 3 both found stale, so gating one copy and not the other is the
        # same defect with an extra step.
        ("routing mean, restated",   r"at the real ([\d,]+) B/row",               f"{row_bytes // len(rows):,}"),
        ("two mean routing rows",    r"two rows are ([\d,]+) B > ",               f"{2 * (row_bytes // len(rows)):,}"),
    ]
    problems = figure_problems_in(src, checks)

    # ── 🔴 THE CEILING vs THE TARGET: assert the CLAIM, not its spelling ─────
    # This module's docstring now heads a section "THE CEILING IS NOW THE TARGET
    # — THE EXEMPTION IS RETIRED" and states the ceiling is "now equal to the
    # 12,288 B target". Until 2026-08-27 nothing asserted that, and it was WALKED
    # in one edit during the pre-merge audit of the PR that wrote it: set
    # MAX_BYTES = 13_000, make the four prose edits the surviving figure specs
    # demand (headroom 973, true working 781, margin 973) and the suite returns
    # `40 passed` while both sentences above still claim the exemption is gone.
    #
    # This is character-for-character the defect BB_CLAIM below was written to
    # close, applied to a different claim in the same file: the byte count is a
    # SPELLING of the claim; `MAX_BYTES <= target` IS the claim. The figure specs
    # can only ever pin the spelling, because every one of them re-derives from
    # MAX_BYTES itself and therefore moves WITH it.
    if MAX_BYTES > target:
        problems.append(
            f"the ceiling MAX_BYTES={MAX_BYTES:,} is ABOVE the {target:,} B target, "
            "so the docstring's 'THE EXEMPTION IS RETIRED' / 'now equal to the "
            "target' is FALSE. Raising the ceiling back above the target is a "
            "deliberate re-exemption: restore the WHY-THE-CEILING-IS-ABOVE section "
            "and its accounting in the same edit, or lower the ceiling."
        )

    # ── The one figure this module does NOT own ──────────────────────────────
    # Every other gated figure derives from something this module's own change
    # set owns: prune-skill/SKILL.md, skill-audit.py's TARGET, this file. `bb`
    # does not -- it measures `scripts/browser-bridge/SKILL.md`, which unrelated
    # PRs edit. That coupling turned `main` RED for a 48 B cosmetic edit (#551
    # grew the file, #581 was the one-line re-measure), the fourth stale-figure
    # failure in a day; #581's own message asked for exactly this fix, to derive
    # the claim at test time instead of restating a literal.
    #
    # 🔴 But brittleness was the lesser half. The literal form was WALKABLE, and
    # the walk is executed rather than argued: pad browser-bridge to 12,388 B --
    # over the target -- then update the prose literal to 12,388 to clear the
    # pin, and this test reports `1 passed` while the sentence it guards asserts
    # that browser-bridge MEETS a target it now breaches. The gate CERTIFIED a
    # falsehood, and the repair that silences it is the one that installs it.
    # That is `claude/RULES.md`'s SPELLED-not-STRUCTURAL guard: the byte count is
    # a spelling of the claim; `bb <= target` IS the claim.
    #
    # Not vacuous despite browser-bridge's own ceiling being this same TARGET
    # (skill-audit.py:40 defines TARGET as that skill's PROVEN ceiling). That
    # constant is independent and raisable: raise browser-bridge's MAX_BYTES and
    # grow the file, and its own gate stays GREEN while this sentence turns
    # false. This assertion is the only thing watching that seam.
    #
    # Pinned as the WHOLE normalised sentence, not a phrase, so a reword cannot
    # walk it -- and built from the imported `target`, never a second hand-copy
    # of 12,288, which is the F2 defect this module was written to close.
    BB_CLAIM = (
        f"The skill states a {target:,} B target and browser-bridge MEETS it "
        "while routing ~11x its own weight, so the target is achievable and is "
        "not in dispute."
    )
    problems += bb_claim_problems_in(src, bb, target, BB_CLAIM)

    assert not problems, (
        "this module's own figures no longer describe what they measure:\n  "
        + "\n  ".join(problems)
        + f"\n\nMeasured now: SKILL.md={size:,} B ({size / 1024:.2f} KiB), target "
          f"{target:,} (imported from skill-audit.py), over by {over:,} B "
          f"({100.0 * over / target:.2f}%), headroom {headroom:,} B, working slack "
          f"{slack:,} B, routing table {row_bytes:,} B over {len(rows)} rows, "
          f"browser-bridge {bb:,} B.\n"
        f"Deliberately ungated: {list(UNGATED)}\n"
        "Re-measure and update the prose; do NOT relax this test."
    )


# ── Positive controls for the five guard arms above ─────────────────────────
#
# The gate above only ever runs against a CLEAN corpus, so every one of its
# `problems.append` branches was recorded by `scripts/dead-guard-scan.py` as
# never executed: the guards were asserted to work, never watched to. These
# controls feed synthetic inputs that MUST make one specific arm fire, and each
# asserts that arm's WHOLE message, so a mutant cannot be scored dead by a
# neighbouring arm's wording.
#
# The three `figure_problems_in` arms are mutually exclusive branches of one
# loop, so each fixture is built to leave the other two silent:
#   * no-match   -- the pattern is absent, so neither count nor value is reached;
#   * multi-match -- `expected` EQUALS the first capture, so disabling this arm
#     falls through to a value comparison that is TRUE and yields nothing, never
#     a second message that could be mistaken for this one;
#   * value-mismatch -- exactly one capture, so only the value arm can fire.
# Fixture figures are pairwise distinct and share no value with any figure the
# live corpus measures, so a mutant hardcoding a real constant survives none of
# them. They are deliberately NOT restated from the gated docstring either --
# this module's own rule is that a second hand-copy of a live number is how the
# drift regrows, and these controls must not become such a copy.


def test_control_figure_pattern_that_matches_nothing_reports_it_unchecked():
    """Arm 1: a reworded sentence means the figure is silently unchecked."""
    src = "the ledger sits at 4,441 B today"
    pattern = r"no sentence in this fixture says ([\d,]+) B"
    problems = figure_problems_in(src, [("absent figure", pattern, "9,173")])
    assert problems == [
        f"absent figure: PATTERN DID NOT MATCH ({pattern!r}) — the "
        "sentence was reworded, so this figure is now unchecked"
    ]


def test_control_figure_pattern_that_matches_twice_reports_the_shadowing():
    """Arm 2: a second sentence can shadow the live figure.

    `expected` is the FIRST capture on purpose: with this arm neutered the loop
    falls through to a value comparison that passes, so the control goes empty
    rather than red-for-the-wrong-arm.
    """
    src = "row A is 2,207 B and row B is 6,619 B"
    problems = figure_problems_in(src, [("doubled figure", r"is ([\d,]+) B", "2,207")])
    assert problems == [
        "doubled figure: pattern matched 2x (['2,207', '6,619']) — an "
        "added sentence can shadow the live figure; make the "
        "pattern name exactly one sentence"
    ]


def test_control_figure_whose_prose_value_drifted_reports_both_numbers():
    """Arm 3: one clean match whose captured value is no longer the measurement."""
    src = "the tally reads 5,003 B in prose"
    problems = figure_problems_in(
        src, [("stale figure", r"tally reads ([\d,]+) B", "8,761")])
    assert problems == ["stale figure: prose says 5,003, measured 8,761"]


def test_control_all_three_figure_arms_fire_together_with_distinct_wording():
    """The three arms are distinguishable, not one message wearing three hats."""
    src = "row A is 2,207 B and row B is 6,619 B. the tally reads 5,003 B in prose"
    absent = r"no sentence in this fixture says ([\d,]+) B"
    problems = figure_problems_in(src, [
        ("absent figure", absent, "9,173"),
        ("doubled figure", r"is ([\d,]+) B", "2,207"),
        ("stale figure", r"tally reads ([\d,]+) B", "8,761"),
    ])
    assert problems == [
        f"absent figure: PATTERN DID NOT MATCH ({absent!r}) — the "
        "sentence was reworded, so this figure is now unchecked",
        "doubled figure: pattern matched 2x (['2,207', '6,619']) — an "
        "added sentence can shadow the live figure; make the "
        "pattern name exactly one sentence",
        "stale figure: prose says 5,003, measured 8,761",
    ]


_CONTROL_CLAIM = "PINNED FIXTURE SENTENCE about a target being met."


@pytest.mark.parametrize("occurrences", [0, 2])
def test_control_bb_claim_not_present_exactly_once_is_reported(occurrences):
    """BB arm 1: the wording pin. `bb <= target`, so the value arm stays silent."""
    src = "prelude\n" + "\n".join([_CONTROL_CLAIM] * occurrences) + "\ncoda"
    problems = bb_claim_problems_in(src, 7_331, 10_247, _CONTROL_CLAIM)
    assert problems == [
        "browser-bridge claim: the sentence asserting that browser-bridge "
        "meets the target is not present exactly once as written.\n    "
        f"Expected verbatim (whitespace-normalised):\n      {_CONTROL_CLAIM}\n    "
        "A reword makes this figure unchecked, so restore the wording or "
        "re-point this pin deliberately."
    ]


def test_control_bb_over_target_reports_the_sentence_as_false():
    """BB arm 2: the sentence is present verbatim and is nonetheless FALSE.

    This is the walk the module's own comment describes -- the prose still says
    browser-bridge MEETS the target while the file measures over it. The claim
    is planted exactly once so the wording arm cannot fire and steal the kill.
    """
    src = f"prelude\n{_CONTROL_CLAIM}\ncoda"
    problems = bb_claim_problems_in(src, 13_701, 11_453, _CONTROL_CLAIM)
    assert problems == [
        "browser-bridge claim: prose says browser-bridge MEETS the "
        "11,453 B target, but it measures 13,701 B -- over by "
        "2,248 B. The sentence is now FALSE, and this module's "
        "whole 'the target is achievable and is not in dispute' argument "
        "rests on it. Fix browser-bridge or rewrite the argument -- do NOT "
        "restate the new number, which is how the literal form of this "
        "check certified the same falsehood."
    ]


def test_control_clean_input_produces_no_problems_at_all():
    """Negative control: neither helper invents a problem on a clean corpus."""
    src = f"the tally reads 8,761 B in prose\n{_CONTROL_CLAIM}"
    assert figure_problems_in(
        src, [("stale figure", r"tally reads ([\d,]+) B", "8,761")]) == []
    assert bb_claim_problems_in(src, 7_331, 10_247, _CONTROL_CLAIM) == []


def test_every_reference_topic_is_routed_from_the_core():
    """An ORPHANED sidecar is unreachable content, and it happens silently.

    One campaign skill held 40 KB across three sidecars that no routing line
    mentioned -- previously-demoted topics whose pointers were lost in a later
    edit. `skill-audit.py` reports orphans, but nothing FAILS on one, so the
    condition persisted across sessions. This makes it fail.

    STRUCTURAL, NOT SPELLED. This started as `t not in body` -- a bare substring
    test on the basename -- and two executed probes walked straight through it,
    both shipping `4 passed`:

      * renaming `reference/` -> `refrence/` throughout SKILL.md (every routing
        path in the core dead) -- the basenames still appeared, so it passed;
      * rewriting a routing table cell to a bare prose mention
        (`` `reference/staleness-pass.md` `` -> "the staleness-pass.md notes")
        -- not a path at all, and it passed.

    The skill's own section 4 is entirely about routing paths that RESOLVE, so a
    guard that a non-path satisfies is the defect that section warns about.

    RESOLVED AS WRITTEN, NOT BY BASENAME. The first structural version kept only
    the basename out of `reference/<x>.md` and looked it up under REFERENCE_DIR,
    which discards the prefix and the extension -- so four more spellings walked
    through it, all executed, all reporting `4 passed`:

      * `reference/reference/staleness-pass.md` -- the exact dead shape a
        sibling finding of this PR had just removed from the core;
      * `~/.claude/skills/browser/reference/staleness-pass.md` -- another
        skill's directory;
      * `reference/staleness-pass.mdx` and `reference/staleness-pass.md.bak` --
        wrong extension, wrong suffix.

    Each is now resolved by `_resolve_routing_path` against the base its own
    prefix names, and must be a file there. LIMIT, stated rather than papered
    over with an exception list: a route into ANOTHER repo's skill tree, e.g.
    `.claude/skills/gitops-gate/reference/gate-catalog.md`, has no base inside
    this repo, so it resolves under `claude/skills/` here and is reported
    dangling even when it exists in that other repo.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    topics = _existing_topics()
    assert topics, f"no reference topics found under {REFERENCE_DIR}"

    # Direction 1: no dangling route. Every routing path the core writes must
    # resolve AS WRITTEN -- prefix, skill segment and extension included -- to a
    # file that exists. Resolving by basename instead is what accepted the four
    # spellings named in the docstring.
    dangling = _dangling_routes(body)
    assert not dangling, (
        "the core writes routing paths that do not resolve to a file:\n"
        + "\n".join(f"  {t}  ->  {_resolve_routing_path(t)}" for t in dangling)
        + "\nA reader follows the pointer AS WRITTEN and lands nowhere. Fix the "
        "path -- the prefix, the skill segment and the extension all count -- or "
        "restore the file."
    )

    # Direction 2: no orphan. Every topic on disk must be reachable from the
    # registry by a path that RESOLVES TO IT, not merely name-dropped in prose
    # and not by a path that resolves somewhere else.
    routed = {_resolve_routing_path(t) for t in _routing_paths(_registry_block(body))}
    unrouted = _unrouted_topics(body)
    assert not unrouted, (
        f"reference topics exist but the core's registry does not route to "
        f"them by a path that resolves to them: {unrouted}\n"
        f"Routed by the registry: {sorted(str(p) for p in routed) or 'NOTHING'}.\n"
        "A bare mention of the filename is NOT a route -- write "
        "`reference/<topic>.md`. Either add the routing line (the default -- an "
        "orphan is usually a demoted topic that lost its pointer, not dead "
        "weight) or delete the file and say in the commit why it is dead."
    )


# ---------------------------------------------------------------------------
# PROBES -- the routing repair's own regression coverage.
#
# Every case below was executed BY HAND in the round that replaced the basename
# lookup with resolution AS WRITTEN, and NONE of it was committed. Re-measured
# here before writing them down: reverting `_resolve_routing_path` to
# `REFERENCE_DIR / token.split("/")[-1]` -- a straight functional revert that
# puts all four documented false-greens back to GREEN -- still printed
# `4 passed`. The same ten-mutant battery was then run BOTH ways: against the
# four pre-existing tests it killed 2 (the two that break the CORE's own routes,
# so the one live body catches them) and 8 survived; against these probes all
# ten die. This is precisely how the BASENAME hole itself survived an audit:
# probes run in a shell prove nothing about the next edit. They are tests now.
#
# Each case is planted into a copy of the REAL SKILL.md body and graded by the
# REAL `_dangling_routes` / `_unrouted_topics`, so a mutation ANYWHERE in the
# pipeline -- the regex, the URL/segment/dot/punctuation filters, PREFIX_BASES,
# the resolver, the containment, the registry reader -- moves a verdict here.
# The unplanted body is green (asserted above), so an EQUALITY on the verdict
# is available and is used: it catches a mutation that adds a route as well as
# one that drops it.
# ---------------------------------------------------------------------------

_TOPIC = "staleness-pass.md"


def _plant(spelling: str) -> str:
    """The real core, plus one planted routing spelling."""
    return (
        SKILL_MD.read_text(encoding="utf-8")
        + "\n\nPlanted by the routing probes: "
        + spelling
        + "\n"
    )


# (planted markdown, the token the gate must report dangling)
DANGLING_SPELLINGS = [
    # The dead shape a sibling finding removed from the core; a basename
    # resolver collapses it onto the real file.
    ("`reference/reference/staleness-pass.md`", "reference/reference/staleness-pass.md"),
    # Another skill's directory.
    (
        "`~/.claude/skills/browser/reference/staleness-pass.md`",
        "~/.claude/skills/browser/reference/staleness-pass.md",
    ),
    # Wrong extension, and wrong suffix.
    ("`reference/staleness-pass.mdx`", "reference/staleness-pass.mdx"),
    ("`reference/staleness-pass.md.bak`", "reference/staleness-pass.md.bak"),
    # One character wrong in the skill segment.
    (
        "`~/.claude/skills/prune-skil/reference/staleness-pass.md`",
        "~/.claude/skills/prune-skil/reference/staleness-pass.md",
    ),
    # An absolute path under NO mapped prefix. Written with a fixed user so the
    # verdict does not depend on this machine's $HOME; the real deployed
    # spelling is pinned separately below.
    (
        "`/home/example-user/.claude/skills/prune-skill/reference/staleness-pass.md`",
        "/home/example-user/.claude/skills/prune-skill/reference/staleness-pass.md",
    ),
]

LEGITIMATE_SPELLINGS = [
    # The core's own form.
    "`reference/staleness-pass.md`",
    # A markdown link -- the destination is the route, the brackets are not.
    "[the staleness pass](reference/staleness-pass.md)",
    # A URL that happens to contain `/reference/`: not a claim about this tree.
    "https://example.dev/a/reference/staleness-pass.md",
    # Trailing sentence punctuation is not part of a filename.
    "see reference/staleness-pass.md.",
    # `reference` is not a whole segment here -- prose, not a route.
    "`cross-reference/index.md`",
    # The repo-root-relative and repo-absolute spellings.
    "`.claude/skills/prune-skill/reference/staleness-pass.md`",
    "`claude/skills/prune-skill/reference/staleness-pass.md`",
    "`~/workspace/devrc/claude/skills/prune-skill/reference/staleness-pass.md`",
    # The deployed-tree spelling, which PREFIX_BASES settles in-tree.
    "`~/.claude/skills/prune-skill/reference/staleness-pass.md`",
    # `.` / `..` heads: correct pointers that the repo-root head test used to
    # hijack, because `(REPO_ROOT / "..").is_dir()` is trivially True.
    "`./reference/staleness-pass.md`",
    "`../prune-skill/reference/staleness-pass.md`",
    # A bare directory and the placeholder: not routes, and must not become
    # ones -- both are written in the core today.
    "`~/.claude/skills/prune-skill/reference/`",
    "`reference/<topic>.md`",
]


@pytest.mark.parametrize("spelling,token", DANGLING_SPELLINGS, ids=lambda v: v)
def test_planted_dangling_route_is_reported(spelling, token):
    """A route that resolves to nothing must be REPORTED, not collapsed."""
    assert _dangling_routes(_plant(spelling)) == [token], (
        f"planting {spelling} left the gate green (or reported something else). "
        "A reader follows this pointer AS WRITTEN and lands nowhere; the gate "
        "must say so. Resolving by basename, or dropping the token's prefix, is "
        "what accepted these spellings before."
    )


@pytest.mark.parametrize("spelling", LEGITIMATE_SPELLINGS, ids=lambda v: v)
def test_planted_legitimate_route_stays_green(spelling):
    """A correct pointer must NOT be reported. A gate that reds on correct
    input trains people to bypass it -- `claude/RULES.md` names a permanently
    red gate as worse than no gate."""
    assert _dangling_routes(_plant(spelling)) == [], (
        f"planting {spelling} turned the gate red on a CORRECT pointer."
    )


def test_absolute_route_is_dangling_even_when_the_file_really_exists(tmp_path):
    """HERMETICITY, executed: an absolute route to a file that DOES exist on
    this host must still be red, and must not be stat'd where it points.

    Machine state may not decide the verdict -- otherwise the gate says one
    thing here and another in a fresh clone or in CI.
    """
    real = tmp_path / "reference" / _TOPIC
    real.parent.mkdir(parents=True)
    real.write_text("a real file, outside the repo\n", encoding="utf-8")
    token = str(real)
    assert real.is_file(), "fixture broken: the off-repo file was not created"

    resolved = _resolve_routing_path(token)
    assert REPO_ROOT in resolved.parents, (
        f"{token} resolved to {resolved}, outside the repo tree -- the gate is "
        "reading the host filesystem and its verdict depends on machine state."
    )
    assert _dangling_routes(_plant(f"`{token}`")) == [token]


def test_deployed_spelling_is_never_stat_where_it_points():
    """The exact spelling that made the gate host-dependent: the DEPLOYED copy
    under `$HOME/.claude/`. It passed on a host that had run `home-manager
    switch` and failed everywhere else.

    Graded on the RESOLUTION, not on a planted body, so the assertion holds
    whatever this host's `$HOME` is and whether or not the deploy exists.
    """
    token = str(Path.home() / ".claude/skills/prune-skill/reference" / _TOPIC)
    resolved = _resolve_routing_path(token)
    assert REPO_ROOT in resolved.parents, (
        f"{token} resolved to {resolved} -- outside the repo. On a deployed host "
        "that file exists, so the gate would go GREEN on a route this repo "
        "cannot settle."
    )
    assert not resolved.is_file()


def test_dotdot_escape_cannot_reach_outside_the_repo():
    """A `..` run that normalises above the repo root is contained too."""
    token = "../../../../etc/reference/passwd.md"
    resolved = _resolve_routing_path(token)
    assert REPO_ROOT in resolved.parents, f"{token} escaped to {resolved}"
    assert _dangling_routes(_plant(f"`{token}`")) == [token]


def test_dot_heads_resolve_against_the_skill_directory():
    """`.`/`..` are relative heads, not repo-root directories."""
    assert _resolve_routing_path(f"./reference/{_TOPIC}") == REFERENCE_DIR / _TOPIC
    assert (
        _resolve_routing_path(f"../prune-skill/reference/{_TOPIC}")
        == REFERENCE_DIR / _TOPIC
    )


def test_registry_written_as_bullets_still_routes_every_topic():
    """The registry is read WHOLE, not as table rows.

    The skill's own section 3 asks for "ONE routing line" per demoted topic,
    which is a bullet as often as a table row. A rows-only reader reported
    `Routed by the table: NOTHING` for a registry rewritten as bullets while
    every route in it resolved -- so this rewrite must stay green.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    bullets = (
        REGISTRY_MARKER
        + ":\n\n"
        + "\n".join(f"- load it when… `reference/{t}`" for t in _existing_topics())
        + "\n"
    )
    rewritten = body.replace(_registry_block(body), bullets)
    assert rewritten != body, "fixture broken: the registry block was not replaced"
    assert _unrouted_topics(rewritten) == []
    assert _dangling_routes(rewritten) == []


def test_registry_of_bare_prose_mentions_routes_nothing():
    """The other direction of the same reader: a filename NAME-DROPPED in prose
    is not a route, so every topic reads as an orphan."""
    body = SKILL_MD.read_text(encoding="utf-8")
    prose = (
        REGISTRY_MARKER
        + ":\n\n"
        + "\n".join(f"- see the {t} notes" for t in _existing_topics())
        + "\n"
    )
    rewritten = body.replace(_registry_block(body), prose)
    assert rewritten != body, "fixture broken: the registry block was not replaced"
    assert _unrouted_topics(rewritten) == _existing_topics()


@pytest.mark.parametrize(
    "spelling",
    [
        "`reference/README`",      # last segment has no dot
        r"`reference\staleness-pass.md`",  # backslash is not a separator here
    ],
    ids=["dotless-last-segment", "backslash-separator"],
)
def test_stated_limit_these_shapes_are_invisible_not_red(spelling):
    r"""A STATED LIMIT, pinned so it stays a known one rather than a surprise.

    Neither shape is seen as a route, so a DANGLING route written that way is
    invisible rather than red. Both are kept deliberately:

      * the dot requirement on the last segment is what keeps the bare
        directory (`~/.claude/skills/prune-skill/reference/`) and the
        placeholder (`reference/<topic>.md`) -- both written in the core today,
        both green above -- out of the route set. Dropping it would red the core
        on correct input; widening it to "dotless but non-empty" would make
        ordinary prose (`the reference/notes section`) a route;
      * `\` is not a path separator on this platform, and the whole corpus is
        POSIX-spelled, so a token containing one never reaches the filesystem.

    If either shape ever appears in a core, this test is the place it was
    accepted, and the fix is to narrow it -- not to discover it in a reader's
    404.
    """
    assert _routing_paths(_plant(spelling)) == _routing_paths(
        SKILL_MD.read_text(encoding="utf-8")
    )
