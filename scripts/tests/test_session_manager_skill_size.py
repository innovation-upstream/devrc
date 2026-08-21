"""Deterministic byte-size + routing gate for `claude/skills/session-manager/SKILL.md`.

Lives in `scripts/tests/` for the reasons `test_prune_skill_size.py` states for
the skill it gates: this directory is already a HERMETIC_TARGET, every hermetic
target must start with `scripts/`, and keeping the test here stops it shipping
into `~/.claude/skills/session-manager/` as dead weight in the deployed tree.

WHY THIS EXISTS
---------------
`session-manager` was the largest skill body in the repo -- 23,233 B, ~6k tokens
that displace the task the skill was loaded FOR, on every single invocation.
It got there the way every skill body does: each real incident appended its
lesson and nothing ever applied pressure the other way, because a SKILL.md costs
zero until its trigger fires and then costs all of it at once. Prose budgets do
not hold; `scripts/browser-bridge/tests/test_skill_size.py` and
`scripts/tests/test_prune_skill_size.py` both document that failure for their own
files. This is the same deterministic replacement, for this one.

THE CEILING IS A RATCHET, AND IT SITS ABOVE THE 12,288 B TARGET
---------------------------------------------------------------
`scripts/skill-audit.py` targets 12,288 B and browser-bridge MEETS it, so the
target is achievable in general and is not in dispute. This file does not meet
it, deliberately, and the reason is recorded here rather than left to be
rediscovered:

The prune moved every procedure, field ledger, measurement and piece of
archaeology out to `reference/` (7 topics, ~66 KB, zero cost until opened). What
remains is almost entirely a set of claims that MUST be read at the moment of
consumption, because each one exists to stop the tool being MISREAD in a way
that has already happened:

  * every null-vs-zero discriminator -- `waiting_probable: null` is not `false`,
    `summary.unsent_prompt.count` is `null` never `0`, `clawgate_queue` publishes
    `count: null` in three of its four states, `schema_ok: false` means stuck was
    NOT measured. Demoting any of these behind a load is how "nothing is waiting
    on you" gets emitted off a look that never happened;
  * the `waiting_probable` / `unsent_prompt` separation, which is the reason
    neither number is summed into the other;
  * the `clawgate_queue` rename, which records a misread that shipped into a
    brief for three subagents;
  * the `not_measured` derivation, the exit-code table and the three independent
    per-host measurement statuses;
  * 🔴 the NEVER-PASTE-A-CAPTURED-DRAFT rule -- devrc is a PUBLIC repo and
    `unsent_prompt` hands an agent text the operator typed.

A guard the reader has to take a second load to see is not a guard, so those
stay in the core and the ceiling is set where the demotion left the file. It
forbids regrowth; it does not endorse the size. LOWERING it as more moves out is
the intended direction of travel. Raising it needs the same kind of justification
recorded above, in the commit that raises it.

No figure derived from the file is restated in this docstring -- the constants
below and the failure messages are the only place the numbers live, so there is
nothing here that can go stale silently. (`test_prune_skill_size.py` gates its own
docstring's arithmetic with a regex battery because that module states figures in
prose. This one solves the same problem by not stating them.)
"""
from pathlib import Path

import pytest

# 🔴 ONE RULE, ONE PLACE. The tokenizer that decides what counts as a routing
# path carries a lot of measured detail -- URL authorities, `..` containment,
# trailing sentence punctuation, the dot-in-last-segment rule that keeps a bare
# directory and a `<placeholder>` out of the route set -- and six spellings that
# resolve to nothing walked through earlier hand-rolled versions of it. Import it
# rather than growing a second copy that drifts.
from test_prune_skill_size import (  # noqa: E402
    PREFIX_BASES,
    REPO_ROOT,
    _routing_paths,
    _under_repo,
)

SKILL_DIR = REPO_ROOT / "claude" / "skills" / "session-manager"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCE_DIR = SKILL_DIR / "reference"

# The hard ceiling: SKILL.md must never exceed this many bytes.
#
# A measured position, not a derivation: the file landed at 16,039 B after the
# 2026-08-21 prune (23,233 -> 16,039, with 7 reference topics carrying the rest),
# and 16,384 is the next 16 KiB boundary above it. See the docstring for why this
# is above the 12,288 B target.
MAX_BYTES = 16_384

# Required working margin below the ceiling. A file sitting one byte under
# technically holds the line but leaves no room for a one-line correction.
#
# Sized in units of a REAL edit, and DERIVED at test time rather than restated:
# the structure that actually grows here is the reference routing table, so the
# floor is one mean routing row. A hand-written literal would be the same
# stale-figure defect `test_prune_skill_size.py` was bitten by three times.
MIN_HEADROOM_ROWS = 1

# The block the core uses as its reference registry -- the lines a reader loads
# from. Located structurally; if the marker is gone the registry moved, and that
# is a loud failure by design.
REGISTRY_MARKER = "**Reference topics**"

EVICTION_PLAYBOOK = (
    "Do NOT raise MAX_BYTES to make this pass.\n"
    "  Demote to reference/ instead -- and per the `prune-skill` skill's Sec.5, by\n"
    "  VERBATIM LINE-RANGE SLICING (a python slice of the ORIGINAL, never retyping),\n"
    "  then run its Sec.7 verification (un-sliced gap audit + a >=5-population\n"
    "  survival check, one population being NUMBERS) over the result.\n"
    "  Rewording does not work: measured on this very file during the 2026-08-21\n"
    "  prune, a full hand-tightening pass over eight sections bought ~590 B, while\n"
    "  moving whole sections out to reference/ bought over 6,000 B.\n"
    "  What must NOT move out of the core, because a guard behind a second load is\n"
    "  not a guard: any null-vs-zero discriminator, the waiting/unsent separation,\n"
    "  the exit-code table, and the NEVER-PASTE-A-CAPTURED-DRAFT rule."
)


def _resolve_routing_path(token: str) -> Path:
    """Where the string the core wrote actually points, resolved AS WRITTEN.

    Same contract as `test_prune_skill_size._resolve_routing_path`, with this
    skill's directory as the fallback base: a prefix that names the deployed
    tree (`~/.claude/...`) or the repo root is settled IN-TREE, and anything
    else resolves against the skill's own directory -- which is what a reader
    following the core from there opens.

    HERMETIC by construction: `_under_repo` keeps every resolution inside this
    repo, so the verdict cannot depend on whether this host has ever run
    `home-manager switch`.
    """
    for prefix, base in PREFIX_BASES:
        if token.startswith(prefix):
            return _under_repo(base, token[len(prefix):])
    head = token.split("/", 1)[0]
    if head not in ("", ".", "..") and (REPO_ROOT / head).is_dir():
        return _under_repo(REPO_ROOT, token)
    return _under_repo(SKILL_DIR, token)


def _registry_block(body: str) -> str:
    start = body.find(REGISTRY_MARKER)
    assert start != -1, (
        f"{REGISTRY_MARKER!r} not found in {SKILL_MD}. This test pins that block as "
        "the core's reference registry; if the registry moved, re-point the test."
    )
    end = body.find("\n## ", start)
    return body[start:] if end == -1 else body[start:end]


def _existing_topics() -> list[str]:
    """Reference topics that exist RIGHT NOW, read off the filesystem.

    Globbed, never hard-coded: browser-bridge's hand-maintained literal drifted
    to 8 of 11 and steered a maintainer into duplicating a topic that already
    had a home.
    """
    return sorted(p.name for p in REFERENCE_DIR.glob("*.md"))


def _routing_rows(body: str) -> list[str]:
    return [
        ln for ln in body.splitlines()
        if ln.startswith("| ") and "reference/" in ln and "load it when" not in ln
    ]


def _min_headroom_bytes(body: str) -> int:
    rows = _routing_rows(body)
    assert rows, (
        "no reference routing rows found -- the registry is not a table any more, "
        "so the headroom floor cannot be derived from one. Re-point this helper."
    )
    return MIN_HEADROOM_ROWS * (sum(len(ln.encode()) + 1 for ln in rows) // len(rows))


def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"{SKILL_MD} is missing -- did the skill move?"


def test_skill_md_under_hard_ceiling():
    size = SKILL_MD.stat().st_size
    assert size <= MAX_BYTES, (
        f"SKILL.md is {size:,} B, over the {MAX_BYTES:,} B ceiling by "
        f"{size - MAX_BYTES:,} B.\n  " + EVICTION_PLAYBOOK
    )


def test_skill_md_keeps_working_headroom():
    """Fire BEFORE the ceiling, so a breach is never a surprise."""
    body = SKILL_MD.read_text(encoding="utf-8")
    size = SKILL_MD.stat().st_size
    headroom = MAX_BYTES - size
    floor = _min_headroom_bytes(body)
    assert headroom >= floor, (
        f"SKILL.md is {size:,} B, leaving only {headroom:,} B under the "
        f"{MAX_BYTES:,} B ceiling -- below the {floor:,} B working floor (one mean "
        "reference routing row). The next routine edit will breach it. Evict now, "
        "while there is still room to do it deliberately.\n  " + EVICTION_PLAYBOOK
    )


def test_every_reference_topic_is_routed_and_no_route_dangles():
    """Both directions, and the positive control that keeps a ZERO readable.

    An ORPHANED sidecar is unreachable content and it happens silently -- that
    is exactly the state this skill was in before the prune: `clickhouse-queries.md`
    and `cross-host.md` were NAME-DROPPED in a prose sentence ("Reference:
    waiting-signal.md, exit-codes.md, ...") and routed by nothing, so a reader
    following the core could not open either. A bare mention of a basename is not
    a route.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    topics = _existing_topics()
    assert topics, f"no reference topics found under {REFERENCE_DIR}"

    # 🔴 POSITIVE CONTROL FIRST. Everything below reports a count of PROBLEMS, and
    # a zero from a tokenizer that saw nothing is indistinguishable from a zero
    # from a clean file. Prove the instrument can see this body's routes at all
    # before believing any zero it produces.
    routes = _routing_paths(body)
    assert len(routes) >= len(topics), (
        f"the route tokenizer found {len(routes)} routing path(s) in SKILL.md but "
        f"there are {len(topics)} reference topic(s). Either the core stopped "
        "routing to them, or this gate is wired to nothing -- do not read the "
        "assertions below as clean until this one holds."
    )

    dangling = sorted({t for t in routes if not _resolve_routing_path(t).is_file()})
    assert not dangling, (
        "the core writes routing paths that do not resolve to a file:\n"
        + "\n".join(f"  {t}  ->  {_resolve_routing_path(t)}" for t in dangling)
        + "\nA reader follows the pointer AS WRITTEN and lands nowhere. Fix the "
        "path -- prefix, skill segment and extension all count -- or restore the file."
    )

    routed = {_resolve_routing_path(t) for t in _routing_paths(_registry_block(body))}
    unrouted = [t for t in topics if REFERENCE_DIR / t not in routed]
    assert not unrouted, (
        f"reference topics exist but the core's registry does not route to them by "
        f"a path that resolves to them: {unrouted}\n"
        f"Routed by the registry: {sorted(str(p) for p in routed) or 'NOTHING'}.\n"
        "A bare mention of the filename is NOT a route -- write the deployed path, "
        "`~/.claude/skills/session-manager/reference/<topic>.md`. Either add the "
        "routing line (the default -- an orphan is usually a demoted topic that "
        "lost its pointer) or delete the file and say in the commit why it is dead."
    )


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS -- a gate whose red has never been watched is a claim about
# the gate, not about the file. Each case is planted into a copy of the REAL
# body and graded by the REAL resolver, so a mutation anywhere in the pipeline
# moves a verdict here.
# ---------------------------------------------------------------------------

_TOPIC = "payload-contract.md"


def _plant(spelling: str) -> str:
    return SKILL_MD.read_text(encoding="utf-8") + "\n\nPlanted: " + spelling + "\n"


@pytest.mark.parametrize(
    "spelling",
    [
        # The deployed prefix with one character wrong in the skill segment.
        "`~/.claude/skills/session-managr/reference/payload-contract.md`",
        # A topic that does not exist.
        "`~/.claude/skills/session-manager/reference/does-not-exist.md`",
        # Another skill's directory.
        "`~/.claude/skills/browser/reference/payload-contract.md`",
    ],
    ids=["wrong-skill-segment", "missing-topic", "other-skills-dir"],
)
def test_a_planted_dangling_route_is_reported(spelling):
    body = _plant(spelling)
    dangling = [t for t in _routing_paths(body) if not _resolve_routing_path(t).is_file()]
    assert dangling, (
        f"planting {spelling} left the gate GREEN. A reader follows that pointer "
        "AS WRITTEN and lands nowhere; the gate must say so."
    )


def test_a_registry_of_bare_prose_mentions_routes_nothing():
    """The pre-prune shape, executed: a filename name-dropped in prose is not a
    route, so every topic must read as an orphan."""
    body = SKILL_MD.read_text(encoding="utf-8")
    prose = (
        REGISTRY_MARKER + ":\n\n"
        + "Reference: " + ", ".join(_existing_topics()) + ".\n"
    )
    rewritten = body.replace(_registry_block(body), prose)
    assert rewritten != body, "fixture broken: the registry block was not replaced"
    routed = {
        _resolve_routing_path(t) for t in _routing_paths(_registry_block(rewritten))
    }
    unrouted = [t for t in _existing_topics() if REFERENCE_DIR / t not in routed]
    assert unrouted == _existing_topics()


def test_the_deployed_spelling_is_never_stat_where_it_points():
    """HERMETICITY: the `~/.claude/...` route is settled IN-TREE, so the verdict
    is the same in a fresh clone and on a host that has never deployed."""
    token = f"~/.claude/skills/session-manager/reference/{_TOPIC}"
    resolved = _resolve_routing_path(token)
    assert resolved == REFERENCE_DIR / _TOPIC, (
        f"{token} resolved to {resolved} -- the gate is reading the deployed tree, "
        "so its verdict depends on machine state."
    )
