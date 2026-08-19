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

WHY THE CEILING IS ABOVE THE 12,288 B TARGET, AND WHAT THAT COSTS
-----------------------------------------------------------------
The skill states a 12,288 B target and browser-bridge MEETS it (11,821 B while
routing ~11x its own weight), so the target is achievable and is not in dispute.
This file does not: it sits at 12,861 B (12.56 KiB) after being cut from 14,918 B
by demoting §6 (landing), §4's deployment table, §7's verification rationale, §0's
axes and the always-loaded model to three sidecars, plus stripping evidence from
every remaining section.

The residual is the classification taxonomy (§3) and the rule NAMES in §0/§7.
Demoting those was considered and rejected on the record: §3 is consulted at the
moment of the decision, so putting it behind a load is the same defect as burying
a rule in a table cell, and reducing §0/§7 further would make required checks
invisible without a second load.

So this ceiling is a RATCHET, not an endorsement: it pins the file where the
demotion passes left it and forbids regrowth. It is deliberately NOT set to 12,288,
because a permanently-red gate trains everyone to click through -- which
`claude/RULES.md` names as worse than no gate. Lowering it as the file gets
leaner is the intended direction of travel; raising it needs the same kind of
justification recorded above.

The honest accounting: the skill is 573 B -- 4.66% -- over the target it asks
others to meet (12,861 against 12,288; `skill-audit.py` prints the same 573 B
independently). That is disclosed in the body, in the PR that introduced it, and
here. Every number in this docstring is re-measured, not carried forward: an
earlier revision restated a size, a growth figure, a percentage and a per-pass
byte ledger that were all wrong, in the module that declares itself the single
source of truth for them.
"""
import os
import re
from pathlib import Path

import pytest

# The hard ceiling: SKILL.md must never exceed this many bytes.
#
# NOT a derivation -- a measured position. SKILL.md is 12,861 B (`stat -c %s` and
# `git cat-file -s` agree), so 13,056 leaves 195 B of headroom, of which
# MIN_HEADROOM_BYTES (192) is the floor that must remain: 3 B of true working
# room before the headroom test fires -- i.e. effectively none; the next edit
# here must evict something. The comment here previously read
# "12,864 B measured + 192 B headroom"; the file measured 12,834 at the time, so
# the arithmetic was describing a size the file never had. Re-measure before
# touching this number, and lower it as the file gets leaner -- never raise it.
MAX_BYTES = 13_056

# Required working margin below the ceiling. A file sitting one byte under
# technically holds the line but leaves no room for a one-line correction, which
# is the exact position browser-bridge was re-breached from three times in a day.
#
# Sized in units of a REAL edit rather than a round number, and re-measured
# against the current file rather than restated: the two structures that actually
# grow here are the reference routing table (3 rows, 348 B -> mean 116 B/row) and
# §3's verdict bullets (9 lines, 1,739 B -> mean 193 B). 192 B is therefore
# ~one mean §3 bullet, or one routing row with room to spare -- enough that the
# headroom test fires BEFORE the ceiling rather than arriving alongside it.
#
# It is NOT two mean routing rows: that claim was here, and at the real 116 B/row
# two rows are 232 B > 192. Kept at 192 on the measurement that does hold rather
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


def _dangling_routes(body: str) -> list[str]:
    """Direction 1 of the gate: routing paths in `body` that resolve to no file.

    Factored out so the probes at the bottom of this module grade THE GATE
    rather than a re-implementation of it. A probe that rebuilt this pipeline
    out of `_routing_paths` and `_resolve_routing_path` by hand would stay green
    against a mutation of the wiring between them.
    """
    return sorted(
        {t for t in _routing_paths(body) if not _resolve_routing_path(t).is_file()}
    )


def _unrouted_topics(body: str) -> list[str]:
    """Direction 2 of the gate: topics on disk the registry does not route to."""
    routed = {_resolve_routing_path(t) for t in _routing_paths(_registry_block(body))}
    return [t for t in _existing_topics() if REFERENCE_DIR / t not in routed]


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
