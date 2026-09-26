"""Deterministic per-document byte ceiling for `claudedocs/**/handoff-*.md`.

WHY THIS EXISTS
---------------
🔴 NOT TO SAVE PER-SESSION CONTEXT. Handoff docs are demand-loaded — a session
opens one, on purpose, and pays for that one. 3.3 MB of corpus on disk costs a
session that does not read it exactly nothing, and any justification of this
gate in those terms would be false. The cost is that a doc nobody can finish
reading is a doc nobody prunes, and `scripts/handoff-audit.py` measured the
mechanism directly over 18 docs and every revision of each:

    123 revisions — 121 grew or held, 2 shrank. 16 of 18 never shrank once.
    First revision -> latest: 3,033 -> 7,748 lines, x2.55.

That is not an accident of who was writing. `/handoff` MANDATES append-verbatim
for Gotchas and Open investigations, and the stable-rank rule makes a completed
item stay in place. Both rules are correct and there is simply no counterweight.
This is the counterweight: not "write less", but "evict before you add".

WHERE THE NUMBER COMES FROM
---------------------------
MEASURED 2026-09-13 over the whole 119-document corpus (84 live + 35 archived):

    median  13,730 B      p75  30,262 B      p90  64,074 B
    p95     95,922 B      max 314,233 B  (24x the median)

`MAX_BYTES` is the p90 rounded up to the next power of two, i.e. 4.8x the
median. A doc past it is in the top decile, and by the growth measurement above
is far enough along that dated narrative is almost certainly what it is carrying.

🔴 IT FAILS 11 OF 119 DOCUMENTS TODAY, AND THAT IS WHY `GRANDFATHERED` EXISTS.
`claude/RULES.md`: "a permanently-red gate is worse than no gate — it trains
everyone to click through". Eleven enumerated entries make the gate GREEN at
HEAD on the day it lands while still binding on every one of them. A ceiling
nothing hits would have been the other failure; at p90 this one binds on the
tail and on any doc that grows into it.

⚠ THE ARCHIVE IS NOT EXEMPT, deliberately. The largest archived doc is 24,442 B
— an eighth of the ceiling — so covering `claudedocs/archive/` costs nothing
today, and exempting it would make "move it to the archive" the way to dodge the
cap. It is also self-punishing rather than free: an archived doc drops out of
`resume-state.sh`'s newest-of-N fallback (only an explicitly named path resolves
one), so archiving a doc you are still appending to has a visible cost.

WHAT THE GRANDFATHER LEDGER IS, AND IS NOT
------------------------------------------
An enumeration, never a pattern — `drift-check.sh`'s allowlist argument: an
unknown entry is not covered by default, so a NEW doc over the ceiling fails
rather than being swept in by a glob. And a RATCHET, not an exemption; every
direction of drift comes back here, through the checks ENUMERATED below rather
than through a count of them -- this sentence said "pinned four ways" over a list
of five for as long as the list has had five entries, which is the miscount
`RATCHET_WHO_MAY_PIN`'s comment in `test_handoff_doc.py` records two files over:

  a. a doc over MAX_BYTES with no entry            -> FAIL (the ceiling itself)
  b. a doc over its own entry's allowance          -> FAIL (the ratchet)
  c. an entry whose doc now fits under MAX_BYTES   -> FAIL, delete the entry
  d. an entry naming a path that does not exist    -> FAIL, it went stale
                                                      UNLESS `LIVES_ELSEWHERE`
                                                      declares it foreign
  e. an allowance that is not the tightest step    -> FAIL, with the literal
                                                      to paste

(c) is the one that keeps this from decaying into a permanent exemption list,
and (d) is what catches a rename or a move into `claudedocs/archive/`.

🔴 AND SINCE #1871 THE LEDGER COVERS DOCUMENTS IN OTHER REPOSITORIES, WHICH IS
WHY (d) HAS AN EXCEPTION AT ALL. `handoff_doc.py` enforces rule (p) against a
`--repo` the caller names and looks the allowance up by a repo-relative path
carrying no repo component, so an over-ceiling doc in another checkout needs an
entry HERE. This module reads only devrc's tree, so every such entry is a key (d)
would call stale. `handoff_budget.LIVES_ELSEWHERE` declares them, and
`test_every_FOREIGN_entry_is_declared_and_is_NOT_a_devrc_document` spends the
declaration in the other direction: the day devrc grows a doc with one of those
names, two documents share one allowance, and that test is what makes it loud.

🔴 A FOREIGN ENTRY'S KEY IS A DIGEST OF ITS PATH, NOT THE PATH, BECAUSE devrc IS
PUBLIC — `handoff_budget.digest_key` owns the scheme and states plainly that it
is not a secret. Three consequences bind this module: the allowance lookup goes
through `handoff_budget.lookup` (plaintext key, then digest) in BOTH
`oversize_findings` and production, never a bare `.get`; the collision guard
RESOLVES rather than intersecting raw keys, because a raw-key intersection
against devrc's plaintext corpus is empty by construction and would pass while
measuring nothing; and
`test_every_ledger_KEY_is_a_devrc_path_or_a_WELL_FORMED_digest` is what stops a
plaintext foreign key being re-added by the next person, who will reach for the
path because that is what the failure messages here hand them.

🔴 AND FOR A FOREIGN ENTRY (b), (c) AND (e)'S TIGHTNESS HALF ARE GONE TOO — NOT
ONLY (d). An earlier wording of this paragraph named (d) alone, which reads as
"one check traded for a declaration" and is wrong about three more.
`oversize_findings` computes those three inside
`for path, size in sorted(sizes.items())`, and a foreign path is never in
`sizes` — so they are not weakened, they never run. Watched both ways with the
real function: PRESENT in `sizes`, each of the three fires; ABSENT, all three are
empty. Only (e)'s WELL-FORMEDNESS half survives, in
`test_every_grandfathered_entry_is_a_correctly_stepped_allowance`, which reads
the ledger and not the tree.

⚠ NOTHING REPLACES ANY OF THEM, AND THERE IS NO SECOND CHANNEL. `handoff_doc`'s
"DELETE its GRANDFATHERED entry" warning is `and gated`, and
`gate_enforces_budget` is False in every repo these entries name. So a foreign
doc pruned back under the ceiling keeps a slack allowance nothing will tighten,
and a foreign rename leaves a stale entry nothing can see. A gate reads one tree;
that is the price of a cross-repo ledger, paid deliberately, not an oversight —
and since #1871 round 2 it is paid on 71 of 82 entries, which
`handoff_budget.GRANDFATHERED`'s own header states at full size.

🔴 WHY THE ALLOWANCE IS QUANTISED RATHER THAN THE MEASURED SIZE. Pinning each
doc at exactly the bytes it has today makes the gate red on the very NEXT byte
— and `/handoff`'s append-verbatim mandate means the ordinary path appends. A
gate that is red by construction on the ordinary path is the same thing
`RULES.md` forbids, arrived at from the other side: everyone learns to bump the
number without reading why. `GRANDFATHER_STEP` makes the failure arrive roughly
once per step of growth instead of once per session, which is often enough to be
pressure and rare enough that the person who hits it still reads the playbook.
The tightening half of (e) is what stops the quantum becoming slack: as a doc
shrinks past a step boundary the entry must come down with it.

⚠ A DELIBERATE DEVIATION FROM THE SIBLING GATES, SAID OUT LOUD: there is no
`MIN_HEADROOM_BYTES` warning band here. `test_rules_size.py` and the three skill
gates each govern ONE file, where a band buys "you are one rule from breaking
it" as a signal instead of a surprise. Over 119 documents the same band would
put every doc within it into the failure message on day one — 12 of them at a
4 KiB band — and the signal this corpus needs is the LEDGER: a doc that breaches
gets an entry, and the entry only ever tightens.

THE POPULATION, AND WHY IT IS A FILESYSTEM WALK
-----------------------------------------------
🔴 NOT `git ls-files`, WHICH THE OTHER CONTENT GATES USE. This module runs in
BOTH tiers, and the `nix build` sandbox tier builds from a `cp -r ${./.}` store
copy with NO `.git` — so a `git ls-files` population would be EMPTY there and
this gate would report a serene zero in exactly the tier CI reads.
`test_the_scan_sees_a_real_corpus` is the positive control for that: a zero
population fails loudly rather than passing vacuously.

The walk is `handoff_index.handoff_paths_on_disk`, borrowed rather than
reimplemented, for `RULES.md`'s "one rule, one place" — two spellings of "is
this a handoff doc" is the duplicated predicate that ends up wrong at N-1 sites.
It is recursive, which is what makes `claudedocs/archive/` covered at all, and
it reports whether the walk COMPLETED, which is what lets an unreadable
directory be a refusal instead of a finding of nothing.

⚠ It therefore sees UNTRACKED docs too. That is the right direction for a
ceiling — it fires before you commit, not after — but it means a scratch file
under `claudedocs/` is gated like any other doc.

The numbers below are the SINGLE source of truth. Any other mention of them
(CLAUDE.md, a handoff doc, a PR body) must cross-reference this module rather
than restate the literal.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
import handoff_index  # noqa: E402
from handoff_budget import (  # noqa: E402
    FOREIGN_KEY_HEX, FOREIGN_KEY_PREFIX, GRANDFATHER_STEP, GRANDFATHERED,
    LIVES_ELSEWHERE, MAX_BYTES, digest_key, is_foreign_key, lookup,
    tightest_allowance)

# The hard per-document ceiling. See "WHERE THE NUMBER COMES FROM" above.
#
# 🔴 An ANTI-REGROWTH ratchet, not a target. Ratcheting it DOWN as the corpus
# gets pruned is welcome and is the intended direction of travel. Ratcheting it
# 🔴 THE CEILING, THE STEP AND THE LEDGER NOW LIVE IN
# `scripts/lib/handoff_budget.py`, imported above. This file still OWNS the
# POLICY — it is what fails, it carries the rationale, and it prints the
# playbook — but a SECOND reader appeared (`handoff_doc.py`, which warns an
# author before a write), and production code importing a test module is a
# direction this repo has nowhere else. Change a number THERE; the
# assertions below are unchanged and still decide whether it is correct.


def _eviction_playbook() -> str:
    return f"""
  How to fix — in order, and raising a number is LAST:

    1. EVICT WHAT HAS CLOSED. A shipped rank, a merged PR's plan, a question
       that got answered. `/handoff` keeps completed items in place by design
       and nothing else ever removes them; this gate is what makes that cost
       something. Start here — it is usually the whole answer.
    2. DEMOTE DATED EVIDENCE to a sibling `claudedocs/refs/<topic>.md` and leave
       a pointer. The direct analogue of a skill's `reference/`: measurements,
       incident narratives, byte counts, superseded or retracted reasoning, and
       worked examples where the rule already states its own shape. Keep the
       imperative, the open questions and the gotchas in the doc itself.
       ⚠ A `refs/` file is NOT indexed by `handoff_search` — it is not a handoff
       doc. That is the same trade a skill makes with `reference/`, and it is
       the reason only DATED material goes there, never an open thread.
    3. SPLIT BY INITIATIVE if the doc is genuinely carrying two. Each half is
       then gated on its own. Do NOT split a single initiative in half to get
       under the number — that hides the same bytes in two files and costs a
       reader the thread.
    4. RAISE THE ALLOWANCE by one step, saying in the commit message what could
       not be evicted. Legitimate, and fourth.

  🔴 DO NOT satisfy this by deleting an open investigation, a gotcha or a
     ruled-out theory. Those are the sections whose whole value is that a future
     session does not repeat the work — `handoff_index.SECTION_BOOST` ranks them
     highest in search for exactly that reason.

  Ceiling, step and the grandfather ledger all live in
  scripts/tests/test_handoff_doc_size.py — cross-reference it, never restate the
  numbers.
"""


def handoff_docs(root: Path) -> tuple[dict[str, int], handoff_index.DiskScan]:
    """`({relpath: size}, the scan that produced it)` for one repo root.

    Returns the scan alongside the sizes so a caller can refuse on an incomplete
    walk instead of reading a short population as a clean one. Takes the root as
    an argument so every control below can drive the REAL function against a
    planted tree rather than a reimplementation of it.
    """
    scan = handoff_index.handoff_paths_on_disk(root)
    return {p: (root / p).stat().st_size for p in scan.paths}, scan


def this_repos_corpus() -> dict[str, int]:
    """`{relpath: size}` for THIS repo, from BOTH enumerations, UNIONED.

    🔴 THE UNION IS THE SAFE DIRECTION, and it is the population the ceiling is
    enforced over. `handoff_docs` borrows `handoff_index`'s `os.walk` + regex;
    this adds an independent `rglob` + shell glob. Taking the union means a
    NARROWING in either mechanism cannot let a document escape the cap — it can
    only ever add work, never excuse a file.
    `test_the_borrowed_walk_agrees_with_an_INDEPENDENT_enumeration` is what
    keeps the two honest; this function is what makes a disagreement fail SAFE
    in the meantime.

    🔴 IT ALSO PUTS THE CEILING TEST INTO `ledger-check.sh`'s POPULATION, AND
    THAT IS NOT A SIDE EFFECT — IT IS MEASURED. `scripts/testlib/census_scan.py`
    derives the tests whose verdict depends on the repo's FILE SET, and
    `ledger-check.sh` runs exactly those as the fast pre-merge screen for "a new
    file landed without its ledger row". That IS this gate's failure mode: a
    handoff doc over the ceiling landing on `main` from a branch that never
    touched this file. MEASURED while writing this — the first version of this
    module was absent from that derived set entirely, because the scanner reads
    a `Path(__file__)`-derived root and the borrowed walk reaches its own
    through a PARAMETER, a blind spot its header states. The literal
    `REPO_ROOT`-rooted `rglob` below is what the scanner can see, so the ceiling
    test is now screened rather than only caught by the full tier.

    ⚠ Verify with `python3 scripts/testlib/census_scan.py | grep
    handoff_doc_size`, never by reading this paragraph.
    """
    walked, _ = handoff_docs(REPO_ROOT)
    globbed = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / handoff_index.HANDOFF_DIR).rglob("handoff-*.md")
        if p.is_file()
    }
    sizes = dict(walked)
    for rel in globbed:
        sizes.setdefault(rel, (REPO_ROOT / rel).stat().st_size)
    return sizes


def oversize_findings(
    sizes: dict[str, int],
    ledger: dict[str, int],
    elsewhere: "set[str] | frozenset[str] | dict[str, str]" = frozenset(),
) -> dict[str, list[str]]:
    """The five checks, as `{kind: [rendered finding, …]}`. PURE.

    Split from the tests so the failure branches can be driven from synthetic
    inputs — a ceiling test reads a tree it never writes, so on a compliant tree
    nothing exercises them and they would be asserted-but-never-watched.

    `elsewhere` names ledger KEYS whose document is in ANOTHER repository, so
    (d) must not read their absence from `sizes` as staleness. 🔴 IT DEFAULTS
    EMPTY, which is what keeps the stale branch exercised: every existing control
    calls this with two arguments and still watches (d) fire.

    🔴 THE ALLOWANCE IS RESOLVED THROUGH `handoff_budget.lookup`, NOT
    `ledger.get`, because a foreign entry's key is a DIGEST of its path. On
    devrc's own tree that resolves plaintext-first and is therefore identical to
    the `.get` it replaced; the difference only shows on a path whose digest is in
    the ledger, which is exactly the collision
    `test_every_FOREIGN_entry_is_declared_and_is_NOT_a_devrc_document` fails on —
    so the two agree about which shapes are loud.
    """
    over_ceiling: list[str] = []
    over_allowance: list[str] = []
    now_fits: list[str] = []
    stale: list[str] = []
    mis_stepped: list[str] = []

    for path, size in sorted(sizes.items()):
        allowance = lookup(path, ledger)
        if allowance is None:
            if size > MAX_BYTES:
                over_ceiling.append(
                    f"{path}: {size:,} B, over the {MAX_BYTES:,} B ceiling by "
                    f"{size - MAX_BYTES:,} B"
                )
            continue
        if size <= MAX_BYTES:
            now_fits.append(
                f"{path}: {size:,} B now fits under the {MAX_BYTES:,} B ceiling "
                f"— DELETE its GRANDFATHERED entry (the ledger is a ratchet, not "
                f"a permanent exemption)"
            )
            continue
        if size > allowance:
            over_allowance.append(
                f"{path}: {size:,} B, over its grandfathered allowance of "
                f"{allowance:,} B by {size - allowance:,} B"
            )
            continue
        tight = tightest_allowance(size)
        if allowance != tight:
            mis_stepped.append(
                f'{path}: allowance {allowance:,} is not the tightest step for '
                f'{size:,} B — replace the line with `"{path}": {tight:_},`'
            )

    # 🔴 PLAIN MEMBERSHIP, NOT `lookup`, AND THE ASYMMETRY IS DELIBERATE. This
    # loop iterates the ledger's OWN keys, which are spelled identically in
    # `elsewhere`, so there is nothing to resolve. Resolving here would be a
    # WIDENING in the wrong direction: a genuinely stale devrc entry whose path
    # happened to digest to a declared foreign key would be exempted from (d)
    # instead of reported.
    for path in sorted(ledger):
        if path not in sizes and path not in elsewhere:
            stale.append(
                f"{path}: named in GRANDFATHERED and not present in the corpus "
                f"— it was renamed, moved or deleted. Repoint or remove the entry"
            )

    return {
        "over_ceiling": over_ceiling,
        "over_allowance": over_allowance,
        "now_fits": now_fits,
        "stale": stale,
        "mis_stepped": mis_stepped,
    }


def _render(findings: dict[str, list[str]]) -> str:
    titles = {
        "over_ceiling": "OVER THE CEILING, and not in the grandfather ledger",
        "over_allowance": "OVER ITS GRANDFATHERED ALLOWANCE",
        "now_fits": "BACK UNDER THE CEILING — delete the entry",
        "stale": "LEDGER ENTRY MATCHES NO DOCUMENT",
        "mis_stepped": "LEDGER ENTRY IS NOT THE TIGHTEST STEP",
    }
    out = []
    for kind, title in titles.items():
        if findings[kind]:
            out.append(f"\n  {title}:")
            out += [f"    - {line}" for line in findings[kind]]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# guard the guard
# --------------------------------------------------------------------------- #
def test_the_scan_sees_a_real_corpus():
    """🔴 POSITIVE CONTROL, and the reason this module does not read git.

    Every assertion below is "nothing was over the ceiling", which a population
    of zero satisfies perfectly. That is not hypothetical here: the `nix build`
    sandbox tier has no `.git`, so a `git ls-files` population WOULD be empty in
    the tier CI actually reads, and this whole module would have passed while
    measuring nothing.

    Also refuses on an INCOMPLETE walk — an unreadable directory under
    `claudedocs/` must be a refusal, not a short population read as a clean one.
    """
    sizes, scan = handoff_docs(REPO_ROOT)
    assert scan.scanned and not scan.absent, f"the walk did not run: {scan}"
    assert not scan.errors, f"the walk could not read everything: {scan.errors}"
    assert len(sizes) > 50, (
        f"only {len(sizes)} handoff docs found under {REPO_ROOT}/claudedocs — "
        "the corpus was ~119 when this gate was written, so a number this small "
        "means the scan is broken, not that the corpus was pruned. Every "
        "assertion in this module is vacuous at a population of zero."
    )


def test_the_scan_reaches_the_ARCHIVE_subdirectory():
    """The recursion, watched rather than assumed.

    A non-recursive walk finds the 84 live docs, passes
    `test_the_scan_sees_a_real_corpus` comfortably, and leaves every archived
    doc silently ungated. The population count cannot tell those apart; only
    asking for a path under `archive/` can.
    """
    sizes, _ = handoff_docs(REPO_ROOT)
    archived = [p for p in sizes if p.startswith("claudedocs/archive/")]
    assert archived, (
        "no docs found under claudedocs/archive/ — either the archive moved or "
        "the walk stopped being recursive, and in the second case every "
        "archived doc is ungated while this module still reports a clean run."
    )


#: Names the shared predicate must accept or reject, as `(name, is_a_handoff)`.
#:
#: 🔴 A BEHAVIOURAL TABLE, BECAUSE THE STRING PIN ALONE WAS WALKABLE — MEASURED.
#: The first version of the test below asserted only `.pattern`, and a mutant
#: recompiling the predicate with `re.IGNORECASE` SURVIVED it: the flag changes
#: `.flags`, never `.pattern`, so a case-folded widening — which sweeps in every
#: `HANDOFF-*.MD` and `Handoff-*.Md` in the corpus — read as no change at all.
#: `claude/RULES.md`: "a guard can be SPELLED rather than STRUCTURAL — ask
#: whether it can pass while the hazard exists in a different shape".
#:
#: The rejections are the load-bearing half; every one of them is a widening
#: somebody could plausibly make.
NAME_PREDICATE_TABLE = (
    ("handoff-topic.md", True),
    ("handoff-topic-2026-01-01.md", True),
    ("handoff-a.md", True),
    ("HANDOFF-TOPIC.MD", False),       # the case-folding widening (mutant C10)
    ("Handoff-Topic.md", False),
    ("handoff-topic.markdown", False),
    ("handoff.md", False),             # nothing after the `-`
    ("handoff-.md", False),            # …and nothing is not `.+`
    ("SESSION-HANDOFF.md", False),     # the caps family is resume-state's, not this gate's
    ("my-handoff-notes.md", False),    # `handoff` present, not at the start
    ("handoff-topic.md.bak", False),
    ("kickoff-topic.md", False),
)


def test_the_borrowed_walk_agrees_with_an_INDEPENDENT_enumeration():
    """A cross-check against a second enumeration that fails DIFFERENTLY, and
    the thing that puts this module in `ledger-check.sh`'s population.

    🔴 TWO JOBS, AND THE SECOND ONE IS WHY IT IS SPELLED THIS WAY.

    (1) The guard. `handoff_docs` borrows `handoff_index`'s walk rather than
    reimplementing it — right for "one rule, one place", and it means this gate
    inherits that walk's blind spots silently. A walk that quietly stopped
    descending, or narrowed its predicate, would shrink the governed population
    and every assertion here would still pass. `claude/RULES.md` asks for a
    cross-check against a second tool that fails differently: `Path.rglob` with
    a shell glob is a different mechanism from `os.walk` plus a regex, and the
    two disagreeing is a finding whichever side moved.

    (2) 🔴 THE DISCOVERY GAP, MEASURED. `scripts/testlib/census_scan.py` derives
    the tests whose verdict depends on the repo's FILE SET, and
    `ledger-check.sh` runs exactly those as the fast pre-merge screen for "a
    file landed without its ledger row" — which is this module's failure mode
    precisely. This module was NOT in that derived set: the scanner reads a
    `Path(__file__)`-derived root, and ours reaches its walk through a
    PARAMETER, which its own header lists as a known blind spot. So the screen
    built for this class could not see the newest member of it. A literal
    `REPO_ROOT.rglob` here is what makes it visible — pinned by
    `test_census_scan.py`'s anchors staying green, not by this comment.

    ⚠ NOT a substitute for the seam pin below. This compares two ENUMERATIONS of
    the same tree; that one pins what the shared predicate MEANS. A widening
    applied to both sides at once passes here and fails there.
    """
    borrowed, scan = handoff_docs(REPO_ROOT)
    assert scan.complete, scan
    direct = {
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / handoff_index.HANDOFF_DIR).rglob("handoff-*.md")
        if p.is_file()
    }
    assert direct, (
        "the independent rglob found no handoff docs — it is the control half "
        "of this comparison and two empty sets compare equal, so a zero here "
        "would make the assertion below vacuous."
    )
    only_borrowed = sorted(set(borrowed) - direct)
    only_direct = sorted(direct - set(borrowed))
    assert not (only_borrowed or only_direct), (
        "the borrowed walk and an independent rglob disagree about which files "
        "are handoff docs — one of them narrowed.\n"
        f"  only in handoff_index's walk: {only_borrowed}\n"
        f"  only in the direct rglob:     {only_direct}\n"
        "⚠ `handoff-.md` is the one name the two mechanisms legitimately "
        "disagree on (`*` matches empty, `.+` does not). If that is what this "
        "is, rename the file — it is not a topic."
    )


def test_the_size_predicate_is_the_INDEX_MODULES_and_not_a_second_spelling():
    """🔴 A SEAM LEDGER. `handoff_index` is the single declared answer to "is
    this a handoff doc", and this module is a second consumer of that answer.

    Pinned three ways, because the first two are each individually walkable: the
    whole pattern string (a respelling), the compiled FLAGS (the `re.IGNORECASE`
    widening that survived the string pin), and the decision on a table of real
    names (anything the first two miss).
    """
    pred = handoff_index._HANDOFF_NAME
    assert pred.pattern == r"\Ahandoff-.+\.md\Z", pred.pattern
    assert pred.flags == re.UNICODE, f"unexpected flags: {pred.flags!r}"
    assert handoff_index.HANDOFF_DIR == "claudedocs"

    wrong = [
        (name, want, bool(pred.match(name)))
        for name, want in NAME_PREDICATE_TABLE
        if bool(pred.match(name)) is not want
    ]
    assert not wrong, (
        "the shared handoff-name predicate changed which files it accepts — "
        "(name, expected, actual): " + repr(wrong)
    )


def test_control_the_checker_reports_every_kind_of_breach(tmp_path):
    """🔴 NEGATIVE CONTROL. Drives the REAL checker with synthetic inputs.

    On a compliant tree none of the five branches executes, so each would pass
    identically if it were `pass`, if its comparison were inverted, or if it
    appended to the wrong list. `claude/RULES.md` calls that a harness nobody
    has watched go red.

    The fixture values are chosen so no two findings could be produced by the
    same input: each doc trips exactly one branch.
    """
    sizes = {
        "a-over.md": MAX_BYTES + 1,
        "b-allowance.md": MAX_BYTES + GRANDFATHER_STEP + 1,
        "c-fits.md": MAX_BYTES - 1,
        "e-loose.md": MAX_BYTES + 1,
        "ok.md": 10,
    }
    ledger = {
        "b-allowance.md": MAX_BYTES + GRANDFATHER_STEP,
        "c-fits.md": MAX_BYTES + GRANDFATHER_STEP,
        "d-stale.md": MAX_BYTES + GRANDFATHER_STEP,
        "e-loose.md": MAX_BYTES + 4 * GRANDFATHER_STEP,
    }
    f = oversize_findings(sizes, ledger)
    assert [x.split(":")[0] for x in f["over_ceiling"]] == ["a-over.md"], f
    assert [x.split(":")[0] for x in f["over_allowance"]] == ["b-allowance.md"], f
    assert [x.split(":")[0] for x in f["now_fits"]] == ["c-fits.md"], f
    assert [x.split(":")[0] for x in f["stale"]] == ["d-stale.md"], f
    assert [x.split(":")[0] for x in f["mis_stepped"]] == ["e-loose.md"], f
    # The mis-stepped finding must hand over a PASTEABLE line, not just a
    # complaint — a message that says "wrong" without saying "this instead" is
    # how a ledger drifts further while someone guesses.
    assert f'"e-loose.md": {tightest_allowance(MAX_BYTES + 1):_},' in f["mis_stepped"][0]

    # POSITIVE CONTROL for the same call: a compliant input produces NOTHING, so
    # the findings above are facts about the fixture and not about the function
    # always returning something.
    clean = oversize_findings(
        {"ok.md": 10, "big.md": MAX_BYTES + 1},
        {"big.md": tightest_allowance(MAX_BYTES + 1)},
    )
    assert not any(clean.values()), clean


def test_control_the_checker_reads_the_REAL_tree_and_can_fail_on_it(tmp_path):
    """The other half of the negative control: `handoff_docs` itself.

    `oversize_findings` above is pure, so it proves nothing about the DISCOVERY
    — a walk that returned `{}` would make every assertion in this module pass.
    So plant a real oversized doc in a real directory, walk it with the real
    function, and watch the real checker report it.
    """
    (tmp_path / "claudedocs" / "archive").mkdir(parents=True)
    fat = tmp_path / "claudedocs" / "archive" / "handoff-planted.md"
    fat.write_bytes(b"x" * (MAX_BYTES + 1))
    (tmp_path / "claudedocs" / "handoff-thin.md").write_bytes(b"x" * 10)
    # A non-handoff file in the same directory must NOT be gated.
    (tmp_path / "claudedocs" / "SOME-DESIGN.md").write_bytes(b"x" * (MAX_BYTES + 1))

    sizes, scan = handoff_docs(tmp_path)
    assert scan.complete, scan
    assert set(sizes) == {
        "claudedocs/archive/handoff-planted.md",
        "claudedocs/handoff-thin.md",
    }, sizes

    findings = oversize_findings(sizes, {})
    assert len(findings["over_ceiling"]) == 1, findings
    assert "handoff-planted.md" in findings["over_ceiling"][0], findings
    assert "handoff-thin" not in _render(findings), findings


def test_tightest_allowance_quantises_up_and_never_returns_zero():
    """The pure helper, at a boundary AND a middle — one measurement is not a
    general claim, and an exact multiple is the case an off-by-one gets wrong."""
    s = GRANDFATHER_STEP
    assert tightest_allowance(1) == s
    assert tightest_allowance(0) == s          # never a zero allowance
    assert tightest_allowance(s) == s          # exact multiple stays put
    assert tightest_allowance(s + 1) == 2 * s  # one byte over steps up
    assert tightest_allowance(2 * s - 1) == 2 * s
    assert tightest_allowance(3 * s) == 3 * s


# --------------------------------------------------------------------------- #
# the gate
# --------------------------------------------------------------------------- #
def test_no_handoff_doc_exceeds_its_budget():
    # 🔴 `this_repos_corpus()`, NOT `handoff_docs(REPO_ROOT)` — deliberately, and
    # a module-scoped fixture wrapping it would undo BOTH reasons. It enforces
    # over the UNION of the two enumerations (a narrowing in either cannot
    # excuse a file), and the literal REPO_ROOT walk inside it is what
    # `census_scan.py` can see, which is what puts THIS test in
    # `ledger-check.sh`'s pre-merge screen. See that function's docstring.
    sizes = this_repos_corpus()
    findings = oversize_findings(sizes, GRANDFATHERED, LIVES_ELSEWHERE)
    assert not any(findings.values()), (
        f"\n\nA handoff document is over budget, or the grandfather ledger has "
        f"drifted.\n"
        f"  corpus:   {len(sizes):,} documents under claudedocs/\n"
        f"  ceiling:  {MAX_BYTES:,} bytes per document\n"
        f"  step:     {GRANDFATHER_STEP:,} bytes (grandfathered allowances are "
        f"rounded up to this)\n"
        f"  ledger:   {len(GRANDFATHERED):,} grandfathered documents\n"
        f"{_render(findings)}\n"
        f"{_eviction_playbook()}"
    )


def test_every_FOREIGN_entry_is_declared_and_is_NOT_a_devrc_document():
    """🔴 THE COLLISION GUARD, AND THE PRICE OF A CROSS-REPO LEDGER PAID BACK.

    `LIVES_ELSEWHERE` switches check (d) OFF for the entries it names, so it is
    the one structure in this module that can make a real stale entry invisible.
    Three claims, each a different way that goes wrong:

      1. every declared key is actually IN the ledger — a declaration for a key
         nobody grandfathered exempts nothing and reads as if it did;
      2. no declared key RESOLVES to a document in devrc's OWN corpus. The ledger
         key carries no repo — the path itself for a devrc doc, `digest_key(path)`
         for a foreign one — so a devrc doc of that name would share one
         allowance with a foreign document, and whichever is smaller would be
         silently governed by the other's number. This is the assertion that
         makes the ledger header's collision hazard MECHANICAL rather than a
         comment;
      3. the exemption is not a blanket one — an UNDECLARED absent path must
         still be reported stale, driven through the REAL checker.

    🔴 CLAIM 2 RESOLVES THROUGH `lookup` RATHER THAN INTERSECTING RAW KEYS, AND
    THAT IS THE WHOLE DIFFERENCE BETWEEN A GUARD AND A DECORATION HERE. Every
    foreign key is a digest and every devrc path is plaintext, so
    `set(LIVES_ELSEWHERE) & set(sizes)` — which is what this assertion used to
    be — is now EMPTY BY CONSTRUCTION: it would pass forever, on any tree, while
    measuring nothing. `claude/RULES.md`: a guard can be SPELLED rather than
    structural, so ask whether it can pass while the hazard exists in a different
    shape. The hazard's shape changed with the key, and the control below is what
    proves the resolution actually reaches the digest.

    ⚠ WHAT IT CANNOT DO: confirm a declared document still exists where it is
    declared to live. This suite reads one tree. That is stated in the module
    docstring and in `handoff_budget.LIVES_ELSEWHERE`'s own comment, and it is
    not closable from here.
    """
    undeclared = sorted(set(LIVES_ELSEWHERE) - set(GRANDFATHERED))
    assert not undeclared, (
        "LIVES_ELSEWHERE names keys that are not in GRANDFATHERED, so the "
        "declaration exempts nothing while reading as though it did: "
        f"{undeclared}"
    )

    # 🔴 POSITIVE CONTROL FOR THE RESOLUTION ITSELF, BEFORE THE ZERO IT PRODUCES
    # IS READ AS EVIDENCE. A digest-keyed declaration must be reachable from the
    # PATH, and a path whose digest is absent must not resolve — otherwise the
    # empty intersection below is a fact about `lookup` and not about the corpus.
    # The path is synthetic: naming a real foreign document here would re-publish
    # the name the digest exists to keep out of this tree.
    probe = "claudedocs/handoff-a-synthetic-foreign-topic.md"
    assert lookup(probe, {digest_key(probe): "some-other-repo"}) == "some-other-repo"
    assert lookup(probe, {digest_key(probe + "x"): "some-other-repo"}) is None

    sizes = this_repos_corpus()
    collisions = sorted(p for p in sizes if lookup(p, LIVES_ELSEWHERE) is not None)
    assert not collisions, (
        "a devrc handoff doc RESOLVES to a ledger key declared as living in "
        "ANOTHER repo — by its own name or by its digest — and the ledger key "
        "carries no repo, so one allowance now governs two different documents. "
        "Rename one, or split the ledger by repo; do NOT pick a value silently. "
        "Offenders (devrc path -> declared repo): "
        + repr({p: lookup(p, LIVES_ELSEWHERE) for p in collisions})
    )

    # 🔴 POSITIVE CONTROL for the exemption's narrowness, through the REAL
    # checker: a declared path is silent, an undeclared one is still stale.
    both = oversize_findings(
        {},
        {"declared.md": MAX_BYTES + GRANDFATHER_STEP,
         "undeclared.md": MAX_BYTES + GRANDFATHER_STEP},
        {"declared.md": "some-other-repo"},
    )
    assert [x.split(":")[0] for x in both["stale"]] == ["undeclared.md"], both


def test_every_ledger_KEY_is_a_devrc_path_or_a_WELL_FORMED_digest():
    """🔴 THE PUBLIC-REPO REMEDIATION, MADE MECHANICAL INSTEAD OF A ONE-OFF SCRUB.

    devrc is a PUBLIC repository and this ledger is the only place in it that
    names foreign documents IN BULK — enumerated, sorted and machine-readable —
    so a foreign entry is keyed by `handoff_budget.digest_key(path)`. That scrub
    is worth nothing on its own: the next person adding an entry for a doc in
    another checkout will reach for the path, because that is what the failure
    message they are pasting hands them.

    ⚠ "IN BULK" IS THE WHOLE OF THE CLAIM, AND AN EARLIER DRAFT OVERSTATED IT AS
    "the only place in the tree". MEASURED at the re-key commit over all 1,545
    tracked files: 9 of the 71 formerly-plaintext slugs still appear, in 6 files,
    from two other repositories — all 9 present at the merge-base, so a residual
    that pre-dates this branch rather than anything this ledger controls.

    Two claims, and TOGETHER they leave no third option for a plaintext foreign
    key:

      1. every `LIVES_ELSEWHERE` key is a well-formed digest. A foreign entry
         spelled as a readable path is the disclosure the re-keying removed, and
         this is the assertion that refuses it;
      2. every `GRANDFATHERED` key is either DIGEST-SHAPED or a plaintext
         `claudedocs/…` path whose basename the shared handoff-name predicate
         accepts. A key that is neither resolves for no document at all while
         reading as an allowance — the same shape as a stale entry, minus the
         check that catches one.

    🔴 CLAIM 2 IS A SHAPE TEST, NOT A MEMBERSHIP TEST, AND THE DIFFERENCE IS
    DELIBERATE. It branches on `is_foreign_key(k)` — prefix plus
    `FOREIGN_KEY_HEX` hex characters — and NEVER on `k in LIVES_ELSEWHERE`, so a
    digest-shaped `GRANDFATHERED` key that nobody declared foreign PASSES here.
    A membership arm was considered and NOT added, because the case is already
    covered and covered better: driven through the real `oversize_findings` with
    a digest key absent from `elsewhere`, check (d) reports it stale (1 finding);
    with the same key declared, 0. Adding the arm here would be a second copy of
    one predicate — wrong at one of the two sites the first time either moves —
    and it would report "malformed key" for an entry whose actual defect is a
    MISSING DECLARATION, which is what (d)'s message already says correctly.

    So a foreign entry re-added in plaintext is either left UNDECLARED, and check
    (d) reports it stale because devrc's tree does not hold it, or DECLARED, and
    claim 1 refuses it. It also fails on a change to `FOREIGN_KEY_HEX` or to
    `FOREIGN_KEY_PREFIX`, because every stored key was generated at the old
    values and `is_foreign_key` validates against the new ones — which is the
    right direction: shortening the digest is a real weakening of the collision
    bound `digest_key`'s comment computes.

    ⚠ IT IS TREE-INDEPENDENT AND IT IS NOT A LEAK SCAN. It says nothing about
    prose elsewhere in the repo naming a foreign document; it governs the ledger.
    """
    not_digested = sorted(k for k in LIVES_ELSEWHERE if not is_foreign_key(k))
    assert not not_digested, (
        "a FOREIGN ledger entry is keyed by a readable path rather than by "
        f"`digest_key(path)`. devrc is PUBLIC and this ledger is the only place "
        f"in it that names another repo's documents IN BULK — enumerated and "
        f"machine-readable — so the key must be "
        f"`{FOREIGN_KEY_PREFIX}` followed by {FOREIGN_KEY_HEX} lowercase hex "
        f"characters. Replace each offender with `digest_key(\"<the path>\")` in "
        f"BOTH dicts and say in the commit message how many entries moved, not "
        f"which. 🔴 THAT LAST CLAUSE IS ABOUT YOUR COMMIT MESSAGE AND NOTHING "
        f"ENFORCES IT: there is no `commit-msg` hook here, and this gate reads "
        f"tracked FILES, which a commit message is not. A sibling commit in the "
        f"PR that added this test spelled five foreign slugs and six of their "
        f"byte sizes in its message and had to be reworded and force-pushed. "
        f"Offenders: {not_digested}"
    )

    malformed = sorted(
        k for k in GRANDFATHERED
        if not is_foreign_key(k)
        and not (k.startswith(handoff_index.HANDOFF_DIR + "/")
                 and handoff_index._HANDOFF_NAME.match(k.rsplit("/", 1)[-1]))
    )
    assert not malformed, (
        "a GRANDFATHERED key is neither a well-formed foreign digest nor a "
        f"plaintext {handoff_index.HANDOFF_DIR}/ path this gate's own name "
        "predicate accepts, so it can never resolve for any document while "
        f"still reading as an allowance. Offenders: {malformed}"
    )


def test_the_ledger_RESOLVER_reads_a_digest_key_and_prefers_plaintext():
    """🔴 `handoff_budget.lookup`, DRIVEN DIRECTLY, because it is the seam every
    reader of this ledger now sits behind.

    `budget_position` (production, rule (p)) and `oversize_findings` (this module)
    both resolve through it, and neither can see the digest arm on devrc's own
    tree — every devrc key is plaintext, so the digest fallback never executes
    there. A mutant deleting that fallback therefore SURVIVES everything else in
    this module while silently handing all 71 foreign documents the bare ceiling.
    This is the test that reaches it.

    Four claims, at a boundary and a middle: a plaintext key resolves; a digest
    key resolves FROM THE PATH; a miss is `None` rather than an exception; and
    plaintext WINS over a digest present in the same ledger, which is the
    precedence `lookup`'s docstring states and the only way a devrc entry cannot
    be shadowed.
    """
    p = "claudedocs/handoff-a-synthetic-topic.md"
    other = "claudedocs/handoff-another-synthetic-topic.md"

    assert lookup(p, {p: 81_920}) == 81_920
    assert lookup(p, {digest_key(p): 98_304}) == 98_304
    assert lookup(p, {digest_key(other): 98_304}) is None
    assert lookup(p, {}) is None
    # Precedence, watched rather than assumed: both keys present, plaintext wins.
    assert lookup(p, {p: 81_920, digest_key(p): 98_304}) == 81_920
    # …and the digest is a function of the WHOLE path, not of the basename.
    assert digest_key(p) != digest_key("claudedocs/archive/handoff-a-synthetic-topic.md")


def test_every_grandfathered_entry_is_a_correctly_stepped_allowance():
    """A pure check on the LEDGER ITSELF, independent of the tree.

    `test_no_handoff_doc_exceeds_its_budget` can only see a mis-stepped entry
    for a doc that EXISTS and is still over the ceiling. An entry that is not a
    multiple of the step at all is a malformed ledger whatever the tree says,
    and it must not be able to hide behind a stale or shrunken document.
    """
    bad = {
        p: a
        for p, a in GRANDFATHERED.items()
        if a % GRANDFATHER_STEP or a <= MAX_BYTES
    }
    assert not bad, (
        f"GRANDFATHERED entries must be multiples of GRANDFATHER_STEP "
        f"({GRANDFATHER_STEP:,}) and strictly greater than MAX_BYTES "
        f"({MAX_BYTES:,}); an allowance at or below the ceiling is not an "
        f"exemption, it is a doc that needs its entry deleted. Offenders: {bad}"
    )
