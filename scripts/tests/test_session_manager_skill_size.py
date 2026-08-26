"""Deterministic byte-size + routing + PROTECTED-CLAIM gate for
`claude/skills/session-manager/SKILL.md`.

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
that has already happened -- the null-vs-zero discriminators, the
waiting/unsent separation, the exit-code table, the NEVER-PASTE rule. A guard
the reader has to take a second load to see is not a guard, so those stay in the
core and the ceiling is set where the demotion left the file.

🔴 THAT JUSTIFICATION IS NOW ENFORCED, NOT ASSERTED. It used to be prose only:
an audit MUTATED all four protected claims -- deleted the exit-code `4` row,
rewrote `schema_ok: false` to "means zero stuck", turned
`summary.waiting.probable` "`null`, never `0`" into "`0`", and INVERTED the
waiting/unsent separation -- and all four survived a fully green 584-test suite.
The module's entire argument for sitting over the target was a set nothing
checked, which is the "a guard's DESCRIPTION claims COVERAGE while its body
covers one side" shape `claude/RULES.md` names. `PROTECTED_CLAIMS` below is the
machine-readable version of the paragraph above, each entry pinned as a WHOLE
NORMALISED STRING (a keyword check is walkable by rewording, and "`null`, never
`0`" -> "`0`" is exactly a reword). The eviction playbook's list of what may not
move out is GENERATED from it, so the sentence a maintainer reads cannot be
wider than the check that backs it. The cost is real and accepted: a cosmetic
reword of a protected sentence fails this suite, and the fix is to update the
pin in the same commit -- that is the trade for a machine-readable claim.

The ceiling forbids regrowth; it does not endorse the size. LOWERING it as more
moves out is the intended direction of travel. Raising it needs the same kind of
justification recorded above, in the commit that raises it.

No figure derived from the CURRENT file is restated in this docstring -- the
constants below and the failure messages are the only place those numbers live,
so there is nothing here that can go stale silently. (An earlier revision made
exactly that mistake anyway: it restated a post-prune size that was already
120 B out of date on the branch that introduced it. The number is gone rather
than corrected. `test_prune_skill_size.py` gates its own docstring's arithmetic
with a regex battery because that module states figures in prose; this one
solves the same problem by not stating them.)
"""
import ast
import re
from pathlib import Path

import pytest

# 🔴 ONE RULE, ONE PLACE. The tokenizer that decides what counts as a routing
# path carries a lot of measured detail -- URL authorities, `..` containment,
# trailing sentence punctuation, the dot-in-last-segment rule that keeps a bare
# directory and a `<placeholder>` out of the route set -- and six spellings that
# resolve to nothing walked through earlier hand-rolled versions of it. Import it
# rather than growing a second copy that drifts.
#
# 🔴 AND THE TWO GATE DIRECTIONS THEMSELVES, for a sharper reason. This module
# used to compute `dangling` and `unrouted` inline in the assertion AND again,
# by hand, in each probe below. Measured by an audit: forcing the verdict to a
# constant (`dangling = sorted(set())`, `unrouted = []`) left all NINE routing
# tests green -- the four planted-route probes, this module's only evidence that
# the routing gate works, graded a re-implementation rather than the gate. The
# sibling factored `dangling_routes_in`/`unrouted_topics_in` out for precisely
# that failure; both modules bind their own resolver to them now.
from test_prune_skill_size import (  # noqa: E402
    PREFIX_BASES,
    REPO_ROOT,
    _routing_paths,
    _under_repo,
    dangling_routes_in,
    unrouted_topics_in,
)

SKILL_DIR = REPO_ROOT / "claude" / "skills" / "session-manager"
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCE_DIR = SKILL_DIR / "reference"
SESSION_MANAGER = REPO_ROOT / "scripts" / "session-manager"

# The hard ceiling: SKILL.md must never exceed this many bytes.
#
# A measured position, not a derivation: 16,384 is the 16 KiB boundary
# immediately above where the 2026-08-21 prune left the file, with 7 reference
# topics carrying what came out. The post-prune size itself is deliberately NOT
# restated here -- see the docstring. Where this file actually sits is printed
# by the failure messages below, which read it at test time.
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

# The core's enumeration of the caveat vocabulary, bracketed by the two phrases
# that introduce and close it. A prose list of a set ANOTHER file owns is the
# exact defect the 2026-08-21 prune was opened to fix -- the section it deleted
# listed five of the six `CAVEATS` keys, `kind_scope` missing, and nothing
# noticed. The replacement pointer kept a six-key enumeration, so the defect was
# re-created at one sixth the size: an audit dropped `kind_scope` from the new
# list and invented a seventh key, and each left the suite green.
CAVEAT_LIST_HEAD = "The vocabulary is the keys of `CAVEATS` in `scripts/session-manager` —"
CAVEAT_LIST_TAIL = "— which `measured_caveats` fills in per scan."


def _norm(text: str) -> str:
    """Whitespace-run normalisation, and nothing else.

    Line WRAPPING is cosmetic and must not decide a verdict; wording is not.
    Same normaliser `test_session_manager.py` uses for its whole-string prose
    pins, so a sentence pinned in both places is pinned the same way.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# 🔴 THE PROTECTED CLAIMS -- the ceiling's entire justification, as data.
#
# Each entry is a claim that may NOT leave the core, pinned as one or more WHOLE
# NORMALISED STRINGS. Whole strings, never keywords: `claude/RULES.md` -- "when
# the artifact under test IS prose, a guard on WORDS is walkable by REWORDING --
# pin the WHOLE normalised string", and every mutation that survived the audit
# was a reword, not a deletion.
#
# The NEVER-PASTE rule is pinned here as well as by
# `test_session_manager.py::test_BOTH_shipped_docs_carry_the_NEVER_PASTE_A_
# CAPTURED_DRAFT_rule`, and that is not duplication: the sibling pins a
# RELATIONSHIP (the core and the reference both carry it), this pins a
# CONSEQUENCE OF THIS MODULE'S CEILING (it did not get evicted by someone making
# room). Deleting either leaves a real gap.
# ---------------------------------------------------------------------------
PROTECTED_CLAIMS: dict[str, tuple[str, ...]] = {
    "the exit-code table": (
        # The WHOLE table, header rules included, so deleting or rewriting ANY
        # row reds -- the audit deleted the `4` row specifically, and `4` is the
        # row that separates an unmeasured zero from a measured one.
        "| code | meaning |\n"
        "|---|---|\n"
        "| `0` | ran, found windows (**including** a partial scan where one host "
        "was unreachable) |\n"
        "| `2` | usage / bad `<session>:<window>` / **`tail`: the host answered, "
        "no such window** |\n"
        "| `3` | every requested host answered and the answer is a **real zero** |\n"
        "| `4` | **no** host could be reached — the zero is unmeasured, not measured |\n"
        "| `5` | **`tail` only**: the host answered and there is **no tmux server** "
        "on it |",
    ),
    "the null-vs-zero discriminators": (
        # `--no-capture` collapses BOTH signals to null, and the roll-ups with them.
        "**every** `waiting_probable` AND `unsent_prompt` becomes `null` (both "
        "roll-ups `null`, never `0`)",
        # `waiting_probable`: null is not false, and the roll-up is null not 0.
        "🔴 **`waiting_probable: null` is not `false`.**",
        "`summary.waiting.probable` is likewise **`null`, never `0`**, when nothing "
        "was scraped: the one sentence this tool must never emit is \"nothing is "
        "waiting on you\" off a look that never happened.",
        # `unsent_prompt`: the same pair, plus the unmeasured status.
        "🔴 **`unsent_prompt: null` is an empty box ONLY when "
        "`unsent_prompt_status == \"ok\"`.**",
        "`summary.unsent_prompt.count` is **`null`, never `0`**, when no box was "
        "read.",
        # clawgate_queue: the two the audit rewrote.
        "🔴 **`schema_ok: false` means STUCK WAS NOT MEASURED, not zero stuck**",
        "The last three publish **`count: null`, never `0`**.",
        # the ledger and the age field. Whole sentences, not the `null, never
        # 0` fragment on its own: a fragment survives a reword of everything
        # around it, which is the walk this whole battery exists to close.
        "`status` is `ok` / `partial` / `error` / `skipped` and only the first two "
        "publish integers — the rest are `null`, never `0`; "
        "`summary.rows_with_age` is the meter separating a `stale=0` bucket that "
        "means *nothing is stale* from one that means *nothing has an age*.",
        "🔴 **A null age is not age 0** — no writer has recorded that window yet.",
    ),
    "the waiting/unsent separation": (
        "🔴 **It is NOT part of `waiting_probable` and is never summed into it.**",
        # The two-row table that says what each one means and what to DO. An
        # inversion has to rewrite this to survive.
        "| `waiting_probable` | this window is **BLOCKED** and cannot proceed "
        "without you | go unblock it |\n"
        "| `unsent_prompt` | this window has **WORK PARKED** in its input box | "
        "send it, or clear it |",
        # Separation is not disjointness -- suppressing co-occurrence would make
        # the tool worse, and the reference was corrected for claiming otherwise.
        "🔴 **A row can carry BOTH, and that is correct** — the agent asked a "
        "question and you half-typed a reply. Separation does not mean they never "
        "co-occur; it means neither can raise or be summed into the other.",
        # and the same separation stated from the clawgate_queue side.
        "**For panes that look like they are waiting on a human the field is "
        "`summary.waiting.probable` — a different population, never summed with "
        "this one.**",
    ),
    # 🔴 WIDENED 2026-08-21, and the widening is the point. The rule named
    # `unsent_prompt` alone from the day it landed (2026-08-17) while
    # `clickhouse.rows[].first_msg` -- the opening prompt of every recent
    # session, ~17 KB of operator-typed text in a DEFAULT scan -- had been in
    # the same payload since the tool's first commit (2026-08-11), with no rule
    # attached in any document. Nothing was wrong with the sentence; it was NARROWER than the
    # payload it governed, which is `claude/RULES.md`'s "a guard's DESCRIPTION
    # claims COVERAGE -- check the implementation is as wide as the sentence".
    # The pin is what stops it narrowing back: naming BOTH fields is now the
    # thing that is checked, not merely the thing that is written.
    "the NEVER-PASTE-CAPTURED-OPERATOR-TEXT rule": (
        "🔴 **NEVER PASTE CAPTURED OPERATOR TEXT INTO A COMMITTED FILE.** TWO "
        "fields carry **text the operator typed** — `unsent_prompt` (the draft) "
        "and `clickhouse.rows[].first_msg` (the opening prompt of every recent "
        "session) — and devrc is a **PUBLIC** repo, as is every `claudedocs/` "
        "note, commit message, PR body, comment or test fixture an agent writes "
        "into it. Report either as a **count, a length or a shape**, never "
        "verbatim.",
    ),
}

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
    "  not a guard (this list is GENERATED from PROTECTED_CLAIMS, so it cannot\n"
    "  claim more than is checked):\n"
    + "".join(f"    - {name}\n" for name in PROTECTED_CLAIMS)
    + "  Each is pinned as a whole normalised string; evicting one fails this suite."
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


def _dangling_routes(body: str) -> list[str]:
    """This skill's binding of the sibling's direction-1 gate."""
    return dangling_routes_in(body, _resolve_routing_path)


def _unrouted_topics(body: str, topics: list[str] | None = None) -> list[str]:
    """This skill's binding of the sibling's direction-2 gate.

    `topics` is injectable ONLY so a probe can hand it a topic that is not on
    disk yet; the gate itself always passes the globbed set.
    """
    return unrouted_topics_in(
        _registry_block(body),
        _existing_topics() if topics is None else topics,
        REFERENCE_DIR,
        _resolve_routing_path,
    )


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


def _ceiling_overshoot(size: int) -> int:
    """Bytes by which `size` breaches the ceiling; 0 when it does not.

    A NAMED predicate rather than `size <= MAX_BYTES` inline, so the BOUNDARY is
    reachable by a fixture. Written inline it is one character from rejecting a
    file that sits exactly ON the ceiling, and no real file ever sits there --
    so that mutant survives a green suite. `test_the_ceiling_boundary_is_
    inclusive` feeds it MAX_BYTES-1 / MAX_BYTES / MAX_BYTES+1 and watches the
    answer move.
    """
    return max(0, size - MAX_BYTES)


def _missing_protected_claims(body: str) -> list[tuple[str, str]]:
    """Every protected claim NOT present in `body`, as `(claim name, sentence)`.

    One implementation, used by the gate AND by the mutation probes below, for
    the reason the routing helpers are shared: a probe that re-implemented the
    lookup would stay green against a mutation of the gate.
    """
    haystack = _norm(body)
    return [
        (name, sentence)
        for name, sentences in PROTECTED_CLAIMS.items()
        for sentence in sentences
        if _norm(sentence) not in haystack
    ]


def _script_caveat_keys() -> list[str]:
    """The keys of `CAVEATS` in `scripts/session-manager`, read by `ast`.

    Parsed rather than imported: the script is 4k lines with import-time work,
    and the only thing wanted here is a literal. Parsed rather than grepped:
    a regex over a dict spanning ~150 lines of commentary that itself quotes
    key names is how a "count of declarations" becomes wrong.
    """
    tree = ast.parse(SESSION_MANAGER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "CAVEATS" for t in node.targets):
            continue
        assert isinstance(node.value, ast.Dict), (
            f"`CAVEATS` in {SESSION_MANAGER} is no longer a dict literal, so this "
            "gate can no longer read its keys. Re-point it."
        )
        keys = [k.value for k in node.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        assert len(keys) == len(node.value.keys), (
            f"a `CAVEATS` key in {SESSION_MANAGER} is not a plain string literal; "
            "this gate cannot read the vocabulary."
        )
        return keys
    raise AssertionError(
        f"no `CAVEATS = {{...}}` assignment found in {SESSION_MANAGER} -- the "
        "caveat vocabulary moved, and the core's prose list is now ungated."
    )


def _cores_caveat_list(body: str) -> list[str]:
    """The caveat keys the core ENUMERATES, between its two anchor phrases."""
    text = _norm(body)
    head, tail = _norm(CAVEAT_LIST_HEAD), _norm(CAVEAT_LIST_TAIL)
    start = text.find(head)
    assert start != -1, (
        f"{CAVEAT_LIST_HEAD!r} not found in {SKILL_MD}.\n"
        "That phrase brackets the core's enumeration of the caveat vocabulary and "
        "is what this gate uses to find it. If the section was reworded, re-point "
        "the anchor; if the enumeration was DELETED in favour of the bare pointer, "
        "delete this gate in the same commit and say so -- do not leave an "
        "unanchored prose list of a set the script owns."
    )
    end = text.find(tail, start)
    assert end != -1, (
        f"{CAVEAT_LIST_TAIL!r} not found after the head anchor in {SKILL_MD}; the "
        "enumeration has no closing anchor, so this gate cannot bound it."
    )
    return re.findall(r"`([a-z_]+)`", text[start + len(head):end])


def test_skill_md_exists():
    assert SKILL_MD.is_file(), f"{SKILL_MD} is missing -- did the skill move?"


def test_skill_md_under_hard_ceiling():
    size = SKILL_MD.stat().st_size
    over = _ceiling_overshoot(size)
    assert not over, (
        f"SKILL.md is {size:,} B, over the {MAX_BYTES:,} B ceiling by {over:,} B.\n  "
        + EVICTION_PLAYBOOK
    )


@pytest.mark.parametrize(
    "size,expected",
    [(MAX_BYTES - 1, 0), (MAX_BYTES, 0), (MAX_BYTES + 1, 1)],
    ids=["one-under", "exactly-on-the-ceiling", "one-over"],
)
def test_the_ceiling_boundary_is_inclusive(size, expected):
    """🔴 THE BOUNDARY, WHICH NO REAL FIXTURE EVER SITS ON.

    A file exactly AT MAX_BYTES holds the line; one byte over does not. Without
    this, flipping `<=` to `<` (or the arithmetic by one) is a mutation that
    cannot be caught by any file on disk, because the odds of SKILL.md landing
    on precisely 16,384 B are nil.
    """
    assert _ceiling_overshoot(size) == expected


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


# ---------------------------------------------------------------------------
# PROTECTED CLAIMS -- the ceiling's justification, checked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("claim", list(PROTECTED_CLAIMS), ids=list(PROTECTED_CLAIMS))
def test_a_protected_claim_has_not_left_the_core(claim):
    """🔴 PROTECTED CLAIM STILL IN THE CORE -- pinned as a whole string.

    See the module docstring: this module argues the ceiling belongs above the
    12,288 B target *because* these claims must be readable without a second
    load. Until this test existed the argument was unbacked -- an audit mutated
    every one of them and the suite stayed green.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    missing = [s for name, s in _missing_protected_claims(body) if name == claim]
    assert not missing, (
        f"PROTECTED CLAIM ALTERED OR MISSING FROM THE CORE: {claim}\n"
        + "".join(f"  not found: {_norm(s)!r}\n" for s in missing)
        + "This sentence is why `MAX_BYTES` sits above the 12,288 B target -- a "
        "reader must hit it WITHOUT a second load, so it may not be demoted to "
        "reference/, softened, or reworded away.\n"
        "  * Evicting it to make room under the ceiling is the wrong fix: shrink "
        "somewhere that is not on this list.\n"
        "  * Rewording it deliberately (a typo, punctuation, a genuine "
        "correction)? Update the literal in PROTECTED_CLAIMS in the SAME commit. "
        "Pinning the whole string is what makes a REWORD-shaped weakening -- "
        "'`null`, never `0`' -> '`0`' -- fail here instead of shipping.\n  "
        + EVICTION_PLAYBOOK
    )


def test_the_eviction_playbook_cannot_claim_more_than_is_checked():
    """🔴 THE DESCRIPTION IS AS WIDE AS THE IMPLEMENTATION, structurally.

    The playbook's "what must NOT move out" list is what a maintainer under
    byte pressure actually reads. Before, it was a hand-written sentence naming
    four things and NONE of them were checked -- reading as coverage while
    providing none, which `claude/RULES.md` calls worse than no coverage at all.
    It is generated from `PROTECTED_CLAIMS` now; this pins that it still is, so
    the two cannot be edited apart.
    """
    for name in PROTECTED_CLAIMS:
        assert f"- {name}\n" in EVICTION_PLAYBOOK, (
            f"{name!r} is protected but the eviction playbook does not name it."
        )
    named = re.findall(r"^    - (.+)$", EVICTION_PLAYBOOK, re.M)
    assert named == list(PROTECTED_CLAIMS), (
        "the eviction playbook names a protected item that PROTECTED_CLAIMS does "
        f"not check: playbook={named}, checked={list(PROTECTED_CLAIMS)}. The list "
        "must be generated from the dict, never restated beside it."
    )


# 🔴 NEGATIVE CONTROLS ON THE CLAIM GATE -- the four mutations that survived.
# Each is the audit's own mutation, replayed against a COPY of the real body and
# graded by the REAL `_missing_protected_claims`. `expect` names which entry must
# report it, so a mutant that dies against a DIFFERENT claim (green for the wrong
# reason) is caught too.
_CLAIM_MUTATIONS = (
    (
        "delete-the-exit-code-4-row",
        "| `4` | **no** host could be reached — the zero is unmeasured, "
        "not measured |\n",
        "",
        "the exit-code table",
    ),
    (
        "schema_ok-false-means-zero-stuck",
        "**`schema_ok: false` means STUCK WAS NOT MEASURED, not zero stuck**",
        "**`schema_ok: false` means zero stuck**",
        "the null-vs-zero discriminators",
    ),
    (
        "waiting-probable-rollup-is-zero",
        "`summary.waiting.probable` is likewise **`null`, never `0`**",
        "`summary.waiting.probable` is likewise **`0`**",
        "the null-vs-zero discriminators",
    ),
    (
        "invert-the-waiting-unsent-separation",
        "🔴 **It is NOT part of `waiting_probable` and is never summed into it.**",
        "🔴 **It IS part of `waiting_probable` and is summed into it.**",
        "the waiting/unsent separation",
    ),
)


def test_the_unmutated_core_reports_NO_missing_claim():
    """The green control the mutations below are read against."""
    assert _missing_protected_claims(SKILL_MD.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "old,new,expect", [m[1:] for m in _CLAIM_MUTATIONS], ids=[m[0] for m in _CLAIM_MUTATIONS]
)
def test_a_mutated_protected_claim_is_reported(old, new, expect):
    body = SKILL_MD.read_text(encoding="utf-8")
    assert body.count(old) == 1, (
        f"fixture broken: {old!r} occurs {body.count(old)} times in the core, so "
        "this mutation does not plant what it says it plants."
    )
    reported = _missing_protected_claims(body.replace(old, new))
    assert reported, (
        f"mutating {old!r} -> {new!r} left the claim gate GREEN. That mutation is "
        "one of the four an audit shipped past a 584-test suite; it must red."
    )
    assert {name for name, _ in reported} == {expect}, (
        f"the mutation was caught, but by {sorted({n for n, _ in reported})} rather "
        f"than {expect!r} -- a mutant that dies for the wrong reason is not a kill."
    )


# ---------------------------------------------------------------------------
# THE CAVEAT VOCABULARY -- a prose list of a set the SCRIPT owns
# ---------------------------------------------------------------------------

def test_the_cores_caveat_list_matches_the_scripts_CAVEATS():
    """🔴 THE DEFECT THIS PRUNE WAS OPENED TO FIX, RE-CREATED SMALLER.

    The deleted section restated five caveat entries in prose and had rotted to
    five of six keys. The replacement pointer still enumerates the vocabulary --
    which is legitimate (a reader needs the words) but is still a copy of a set
    `scripts/session-manager` owns, and an audit walked it both ways on a green
    suite: dropping `kind_scope` (the pre-prune bug, exactly) and inventing a
    seventh key. Equality, not containment, so BOTH directions red.
    """
    listed = _cores_caveat_list(SKILL_MD.read_text(encoding="utf-8"))
    owned = _script_caveat_keys()
    assert listed, "the core's caveat enumeration parsed as EMPTY -- gate wired to nothing"
    assert sorted(listed) == sorted(owned), (
        "the core's caveat list disagrees with `CAVEATS` in "
        f"{SESSION_MANAGER}:\n"
        f"  core lists : {listed}\n"
        f"  script owns: {owned}\n"
        f"  only in the core  : {sorted(set(listed) - set(owned))}\n"
        f"  only in the script: {sorted(set(owned) - set(listed))}\n"
        "The script is the authority. Fix the core's list -- or delete the "
        "enumeration and this gate together, keeping only the pointer."
    )


@pytest.mark.parametrize(
    "mutate,label",
    [
        (lambda ks: [k for k in ks if k != "kind_scope"], "drop-kind_scope"),
        (lambda ks: ks + ["invented_key"], "invent-a-seventh-key"),
    ],
    ids=["drop-kind_scope", "invent-a-seventh-key"],
)
def test_a_mutated_caveat_list_is_reported(mutate, label):
    """Both audit mutations, graded by the same comparison the gate makes."""
    listed = _cores_caveat_list(SKILL_MD.read_text(encoding="utf-8"))
    owned = _script_caveat_keys()
    assert sorted(listed) == sorted(owned), "green control failed before the mutation"
    assert sorted(mutate(listed)) != sorted(owned), (
        f"{label} did not move the verdict -- the comparison is wired to nothing."
    )


# ---------------------------------------------------------------------------
# ROUTING
# ---------------------------------------------------------------------------

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
    #
    # 🔴 IT ASKS WHETHER THE TOKENIZER SAW ANYTHING -- NOT `len(routes) >=
    # len(topics)`, WHICH IS WHAT IT USED TO ASK. Routes and topics are both 7,
    # so that comparison fired FIRST on the one hazard the orphan check exists
    # for: add an eighth sidecar and nobody routes to it, and the reader got
    # "this gate is wired to nothing" instead of the `unrouted` playbook naming
    # the file and telling them to add a routing line. An instrument check that
    # pre-empts the finding is not a control, it is a shadow. Counting is also
    # strictly weaker than the per-topic resolution below, which is the real
    # version of the same question.
    routes = _routing_paths(body)
    assert routes, (
        "the route tokenizer found NO routing path anywhere in SKILL.md. Either "
        "the core stopped routing to reference/ entirely, or this gate is wired "
        "to nothing -- do not read the assertions below as clean until this holds."
    )
    assert _routing_paths(_registry_block(body)), (
        f"the {REGISTRY_MARKER} block yielded no routing path. The registry is "
        "what a reader loads topics from; a zero from it is a broken instrument "
        "or a broken registry, and either way not an all-clear."
    )

    dangling = _dangling_routes(body)
    assert not dangling, (
        "the core writes routing paths that do not resolve to a file:\n"
        + "\n".join(f"  {t}  ->  {_resolve_routing_path(t)}" for t in dangling)
        + "\nA reader follows the pointer AS WRITTEN and lands nowhere. Fix the "
        "path -- prefix, skill segment and extension all count -- or restore the file."
    )

    routed = {_resolve_routing_path(t) for t in _routing_paths(_registry_block(body))}
    unrouted = _unrouted_topics(body)
    assert not unrouted, (
        f"reference topics exist but the core's registry does not route to them by "
        f"a path that resolves to them: {unrouted}\n"
        f"Routed by the registry: {sorted(str(p) for p in routed) or 'NOTHING'}.\n"
        "A bare mention of the filename is NOT a route -- write the deployed path, "
        "`~/.claude/skills/session-manager/reference/<topic>.md`. Either add the "
        "routing line (the default -- an orphan is usually a demoted topic that "
        "lost its pointer) or delete the file and say in the commit why it is dead."
    )


def test_a_NEW_orphan_sidecar_reaches_the_unrouted_verdict():
    """🔴 REACHABILITY, not just breakability.

    The headline hazard for direction 2 is an eighth reference topic that
    nobody routes to. Fed through the SAME helper the gate calls, with a topic
    name the registry cannot possibly mention, the verdict must name exactly
    that file -- and must not sweep in the seven that ARE routed.
    """
    body = SKILL_MD.read_text(encoding="utf-8")
    assert _unrouted_topics(body) == [], "green control failed before the mutation"
    orphan = "orphan-sidecar-nobody-routes-to.md"
    assert _unrouted_topics(body, _existing_topics() + [orphan]) == [orphan], (
        "an eighth reference topic that the registry does not mention was NOT "
        "reported as unrouted (or the seven routed ones were swept in with it). "
        "Direction 2 of the routing gate is the only thing that catches an "
        "orphaned sidecar, and an orphan is silent by nature -- nothing else in "
        "this repo would say a word about it."
    )


def self_routing_loops_in(topics, resolve) -> dict[str, list[str]]:
    """Which of `topics` hold a routing path that resolves back to themselves.

    Extracted from the gate below so the RECORDING branch -- the one that files
    a loop -- can be driven by a planted control. On a healthy tree no topic
    self-routes, so that branch was scored by `scripts/dead-guard-scan.py` as
    never executed: the gate's red had been argued, never watched.

    Pure in its inputs: `topics` are the paths to read and `resolve` decides
    where a written token lands, so a control can supply both.
    """
    loops: dict[str, list[str]] = {}
    for topic in topics:
        hits = [t for t in _routing_paths(topic.read_text(encoding="utf-8"))
                if resolve(t) == topic]
        if hits:
            loops[topic.name] = hits
    return loops


def test_control_a_planted_self_route_is_recorded_as_a_loop(tmp_path):
    """Positive control for the loop-recording branch of the self-route gate.

    Two topics are planted side by side and only ONE of them points at itself,
    so a mutant that records unconditionally is caught by the other's absence
    from the verdict -- equality on the whole mapping, not `loops` being truthy.
    The forward pointer is the exact shape the 2026-08-21 prune produced when it
    sliced text verbatim out of the core: correct in the core, a loop once it
    landed inside its own target.
    """
    looper = tmp_path / "loops-onto-itself.md"
    looper.write_text(
        "See `reference/loops-onto-itself.md` for the detail.\n", encoding="utf-8")
    forward = tmp_path / "points-elsewhere.md"
    forward.write_text(
        "See `reference/loops-onto-itself.md` for the detail.\n", encoding="utf-8")

    def resolve(token: str) -> Path:
        return tmp_path / Path(token).name

    assert self_routing_loops_in([looper, forward], resolve) == {
        "loops-onto-itself.md": ["reference/loops-onto-itself.md"]
    }


def test_control_forward_pointers_alone_record_no_loop(tmp_path):
    """Negative control: the branch above is selected by the self-pointer only.

    Without it the positive control cannot tell "the guard fired" from "the
    guard fires on everything", which is the mutant that survives a truthiness
    assertion.
    """
    forward = tmp_path / "points-elsewhere.md"
    forward.write_text(
        "See `reference/some-other-topic.md` for the detail.\n", encoding="utf-8")
    bare = tmp_path / "routes-nothing.md"
    bare.write_text("Prose with no routing path at all.\n", encoding="utf-8")

    def resolve(token: str) -> Path:
        return tmp_path / Path(token).name

    assert self_routing_loops_in([forward, bare], resolve) == {}


def test_no_reference_topic_ROUTES_TO_ITSELF():
    """🔴 THE BLIND SPOT OF THE ROUTING GATE ABOVE: it reads SKILL.md only.

    A self-pointer RESOLVES, so `_dangling_routes` is green on it, and it lives
    in a file the core-only gate never opens. Three shipped in the 2026-08-21
    prune, because the demoted text was sliced VERBATIM out of the core, where
    "see reference/<this file>.md" had been a correct forward pointer and became
    a loop the moment it landed inside its own target. One of the three was
    hand-patched with "-- i.e. this file, above"; the other two were not, and
    nothing could have told anyone.
    """
    loops = self_routing_loops_in(sorted(REFERENCE_DIR.glob("*.md")),
                                  _resolve_routing_path)
    assert not loops, (
        "a reference topic routes to ITSELF -- a reader following the pointer "
        "lands back where they already are:\n"
        + "".join(f"  {name}: {hits}\n" for name, hits in loops.items())
        + "This is what verbatim slicing out of the core produces. Replace the "
        "path with 'this file, above/below', or point at the topic that really "
        "holds the detail."
    )


# ---------------------------------------------------------------------------
# NEGATIVE CONTROLS -- a gate whose red has never been watched is a claim about
# the gate, not about the file. Each case is planted into a copy of the REAL
# body and graded by the REAL `_dangling_routes` / `_unrouted_topics`, so a
# mutation anywhere in the pipeline moves a verdict here. The unplanted body is
# green (asserted above), so EQUALITY on the verdict is available and is used:
# it catches a mutation that ADDS a route as well as one that drops it.
# ---------------------------------------------------------------------------

_TOPIC = "payload-contract.md"


def _plant(spelling: str) -> str:
    return SKILL_MD.read_text(encoding="utf-8") + "\n\nPlanted: " + spelling + "\n"


DANGLING_SPELLINGS = [
    # The deployed prefix with one character wrong in the skill segment.
    ("`~/.claude/skills/session-managr/reference/payload-contract.md`",
     "~/.claude/skills/session-managr/reference/payload-contract.md"),
    # A topic that does not exist.
    ("`~/.claude/skills/session-manager/reference/does-not-exist.md`",
     "~/.claude/skills/session-manager/reference/does-not-exist.md"),
    # Another skill's directory.
    ("`~/.claude/skills/browser/reference/payload-contract.md`",
     "~/.claude/skills/browser/reference/payload-contract.md"),
    # A doubled segment -- what a BASENAME resolver collapses onto the real file.
    ("`reference/reference/payload-contract.md`",
     "reference/reference/payload-contract.md"),
    # A near-miss extension, which backtracks onto a real `.md` prefix.
    ("`~/.claude/skills/session-manager/reference/payload-contract.mdx`",
     "~/.claude/skills/session-manager/reference/payload-contract.mdx"),
]


@pytest.mark.parametrize("spelling,token", DANGLING_SPELLINGS, ids=lambda v: v)
def test_a_planted_dangling_route_is_reported(spelling, token):
    assert _dangling_routes(_plant(spelling)) == [token], (
        f"planting {spelling} left the gate GREEN (or reported something else). "
        "A reader follows that pointer AS WRITTEN and lands nowhere; the gate "
        "must say so, and must name that token and no other."
    )


# 🔴 THE PERMANENTLY-RED-GATE CONTROL. `claude/RULES.md`: a gate that reds on
# CORRECT input is worse than no gate, because it trains everyone to click
# through. Every spelling a reader may legitimately write must stay green --
# without this, "report everything as dangling" is a mutation that kills the
# five probes above and passes.
LEGITIMATE_SPELLINGS = [
    f"`~/.claude/skills/session-manager/reference/{_TOPIC}`",
    f"`.claude/skills/session-manager/reference/{_TOPIC}`",
    f"`claude/skills/session-manager/reference/{_TOPIC}`",
    f"`~/workspace/devrc/claude/skills/session-manager/reference/{_TOPIC}`",
    f"`reference/{_TOPIC}`",
    f"`./reference/{_TOPIC}`",
]


@pytest.mark.parametrize("spelling", LEGITIMATE_SPELLINGS, ids=lambda v: v)
def test_a_planted_legitimate_route_stays_green(spelling):
    assert _dangling_routes(_plant(spelling)) == [], (
        f"planting {spelling} turned the gate RED on a CORRECT pointer."
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
    assert _unrouted_topics(rewritten) == _existing_topics()


def test_the_deployed_spelling_is_never_stat_where_it_points():
    """HERMETICITY: the `~/.claude/...` route is settled IN-TREE, so the verdict
    is the same in a fresh clone and on a host that has never deployed."""
    token = f"~/.claude/skills/session-manager/reference/{_TOPIC}"
    resolved = _resolve_routing_path(token)
    assert resolved == REFERENCE_DIR / _TOPIC, (
        f"{token} resolved to {resolved} -- the gate is reading the deployed tree, "
        "so its verdict depends on machine state."
    )
