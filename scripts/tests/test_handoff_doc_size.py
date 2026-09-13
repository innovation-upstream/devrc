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
rather than being swept in by a glob. And a RATCHET, not an exemption; it is
pinned four ways, so every direction of drift comes back here:

  a. a doc over MAX_BYTES with no entry            -> FAIL (the ceiling itself)
  b. a doc over its own entry's allowance          -> FAIL (the ratchet)
  c. an entry whose doc now fits under MAX_BYTES   -> FAIL, delete the entry
  d. an entry naming a path that does not exist    -> FAIL, it went stale
  e. an allowance that is not the tightest step    -> FAIL, with the literal
                                                      to paste

(c) is the one that keeps this from decaying into a permanent exemption list,
and (d) is what catches a rename or a move into `claudedocs/archive/`.

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

# The hard per-document ceiling. See "WHERE THE NUMBER COMES FROM" above.
#
# 🔴 An ANTI-REGROWTH ratchet, not a target. Ratcheting it DOWN as the corpus
# gets pruned is welcome and is the intended direction of travel. Ratcheting it
# UP requires saying in the commit message which document could not be expressed
# in the budget, and why eviction was not the answer.
#
# ⚠ THE CURRENT SIZES ARE DELIBERATELY NOT WRITTEN DOWN HERE — they are derived
# measurements edited in the same commits as the things they measure, and
# test_rules_size.py records three consecutive rounds where exactly that went
# stale inside its own PR. The failure messages PRINT current / ceiling / over-by
# and the exact ledger line to paste; that is the authority.
MAX_BYTES = 65_536

# The quantum a grandfathered allowance is rounded up to. See "WHY THE ALLOWANCE
# IS QUANTISED" above. One step is ~1.2 median documents, so it is a real
# working margin rather than a rounding artefact.
GRANDFATHER_STEP = 16_384

# 🔴 THE LEDGER. Explicit and ENUMERATED, never a pattern — an unlisted document
# over the ceiling is a failure by default.
#
# path -> allowance in bytes, which must be `ceil(measured / GRANDFATHER_STEP) *
# GRANDFATHER_STEP`. Do not hand-compute it: every failure message prints the
# exact line to paste.
#
# 🔴 REMOVING AN ENTRY IS THE GOAL. A doc that comes back under MAX_BYTES fails
# (c) until its entry is deleted, so this dict can only shrink over time unless
# someone deliberately adds to it.
#
# Measured 2026-09-13; the trailing comment is the size AT THAT MOMENT and is
# informational — the enforced number is the allowance, and the measurement the
# gate reads is the file.
GRANDFATHERED: dict[str, int] = {
    "claudedocs/handoff-tmux-webapp.md": 327_680,               # 314,233 B
    "claudedocs/handoff-audit-pr-ladder.md": 196_608,           # 194,004 B
    "claudedocs/handoff-cairn-oss-multi-instance.md": 196_608,  # 191,946 B
    "claudedocs/handoff-cairn-phase3.md": 163_840,              # 154,141 B
    "claudedocs/handoff-nix-disk-cleanup.md": 114_688,          #  99,215 B
    "claudedocs/handoff-subsystem-store.md": 98_304,            #  95,922 B
    "claudedocs/handoff-gate-flake-store-api.md": 98_304,       #  86,391 B
    "claudedocs/handoff-tmux-restore-chain.md": 98_304,         #  84,569 B
    "claudedocs/handoff-skill-chain-usage-audit.md": 81_920,    #  79,511 B
    "claudedocs/handoff-cairn-task-linkage.md": 81_920,         #  76,743 B
    "claudedocs/handoff-handoff-search-index.md": 81_920,       #  67,076 B
}


def tightest_allowance(size: int, step: int = GRANDFATHER_STEP) -> int:
    """The smallest multiple of `step` that fits `size`. PURE.

    The one place the quantisation is expressed, so the ledger, the failure
    messages and (e)'s check cannot disagree about what a correct entry is.
    """
    return max(step, math.ceil(size / step) * step)


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


def oversize_findings(
    sizes: dict[str, int], ledger: dict[str, int]
) -> dict[str, list[str]]:
    """The five checks, as `{kind: [rendered finding, …]}`. PURE.

    Split from the tests so the failure branches can be driven from synthetic
    inputs — a ceiling test reads a tree it never writes, so on a compliant tree
    nothing exercises them and they would be asserted-but-never-watched.
    """
    over_ceiling: list[str] = []
    over_allowance: list[str] = []
    now_fits: list[str] = []
    stale: list[str] = []
    mis_stepped: list[str] = []

    for path, size in sorted(sizes.items()):
        allowance = ledger.get(path)
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

    for path in sorted(ledger):
        if path not in sizes:
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
@pytest.fixture(scope="module")
def corpus():
    sizes, scan = handoff_docs(REPO_ROOT)
    return sizes, scan


def test_no_handoff_doc_exceeds_its_budget(corpus):
    sizes, _ = corpus
    findings = oversize_findings(sizes, GRANDFATHERED)
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
