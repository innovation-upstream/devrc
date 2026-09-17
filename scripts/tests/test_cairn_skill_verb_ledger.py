"""The `cairn` skill must not deny a verb the `cairn` CLI actually has.

WHY THIS EXISTS
---------------
From devrc#1254 (`34d00d90`, which added `PUT … If-None-Match: *` and the
`cairn create` subcommand) until 2026-09-11, `claude/skills/cairn/SKILL.md`
used to say **"There is no CREATE route, and the failure does not say so."**
The CLI had shipped `cairn create` the whole time.

🔴 THE FIRST CORRECTION WAS ALSO WRONG, AND THAT IS THE REAL LESSON HERE. It
claimed the conclusion survived — that a scope's FIRST entry still could not be
made through the API — "because the index is built by WALKING THE STORE ROOT
narrowed by the caller's allowlist". **False.** `create_entry` runs
`path.parent.mkdir(exist_ok=True)`, and
`test_subsystem_store_api.py::test_a_scopes_FIRST_entry_creates_the_directory`
asserts **201** plus the bytes on disk for an allowlisted scope with no
directory. The only gate is the caller's **token scope allowlist**.

🔴 HOW THE WRONG MECHANISM GOT MEASURED, because the shape recurs: the probe used
a scope that was absent AND non-allowlisted, on a pod whose allowlist enumerated
exactly the scopes it held — so the two sets coincided and the experiment was
structurally incapable of separating them. The 404 came from the allowlist check,
which returns before the index is ever loaded, and it was credited to the index
walk. `claude/RULES.md`: *"An EMPTY RESULT cannot distinguish two mechanisms — go
find the step that differs."* The discriminating control was available and not
run: allowlist a scope, do NOT seed it, `cairn create` → 201.

MEASURED 2026-09-11. Both wrote nothing:

    cairn create --scope civitai-app-requests --ref app-requests --file F
        -> rc 6  [not-found]        (non-allowlisted — THE ALLOWLIST, not the walk)
    cairn create --scope civitai --ref health --file F
        -> rc 9  [already-exists]   (allowlisted, present, ref exists)

WHAT THIS GUARD ASSERTS, AND WHY IT IS NOT A WORD MATCH
-------------------------------------------------------
This guard pins STATE, not words: a two-way ledger of every subcommand
`scripts/cairn` declares. A verb the CLI gains with no ledger row fails; a
ledger row naming no CLI verb fails. Rows marked ``NAMED`` must appear verbatim
in the skill body, which is what makes "the skill silently omits a verb it also
makes claims about" impossible to reintroduce.

🔴 **IT IS HALF THE GUARD, AND AN EARLIER VERSION OF THIS DOCSTRING ARGUED IT
WAS THE WHOLE OF IT — WRONGLY.** It said a guard on the refuted SENTENCE was
rejected because it "would trip on this file's own retraction". Two things were
wrong with that. First, the repo already owns a controlled instrument for the
sentence half — `_unmarked_retractions` in `test_subsystem_store_api.py`, which
normalises case, emphasis and LINE WRAPS, exempts a quote only when a retraction
marker sits within 60 characters, and carries both a negative and a positive
control. Second, the reason was self-serving: a hand sweep run while fixing
`SKILL.md` reported the repo CLEAN, and it was case-sensitive — there were FOUR
live copies of the sentence in other tracked files, one of them straddling a
newline inside a docstring where no line-based grep could ever see it.

So both guards now run, because they catch different failures: this ledger
catches FORWARD drift (a new verb the skill never mentions), and the needle
catches the SENTENCE being re-asserted anywhere in the tree. The docstrings in
this file are phrased to carry a retraction marker beside every quotation of the
refuted claim, which is the discipline that keeps them exempt — cheap, and not
the impossibility the earlier wording claimed.

``DELEGATED`` is not a loophole — it carries its reason, and the reason is
load-bearing: the skill says "Writes are not this skill's" and routes writers to
`subsystem-index`, so it owes no description of `append`/`put`. `create` is NOT
delegated, because the skill makes an explicit CLAIM about creation — in EITHER
direction. ⚠ This sentence used to read "a file that claims a scope's first entry
cannot be created must name the verb that claim is about", which had a false
antecedent the moment the skill was corrected to say the opposite: SKILL.md now
states that a first entry IS created. Stated direction-neutrally so the rule
still reaches the file, matching the `VERB_LEDGER["create"]` row's own wording.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import public_ip_scan as P  # noqa: E402

CAIRN_CLI = REPO / "scripts" / "cairn"
SKILL = REPO / "claude" / "skills" / "cairn" / "SKILL.md"

NAMED = "NAMED"
DELEGATED = "DELEGATED"

# 🔴 TWO-WAY. Every row must name a real subcommand, and every subcommand must
# have a row. Adding a verb to `scripts/cairn` without deciding whether the
# skill has to mention it is exactly the drift this file exists to stop.
VERB_LEDGER: dict[str, tuple[str, str]] = {
    "sync": (NAMED, "the router's verb table — a reader picks it from there"),
    "recall": (NAMED, "the router's verb table"),
    "search": (NAMED, "the router's verb table"),
    "validate": (NAMED, "named, and carries its own 🔴 not-the-write-check caveat"),
    "ls-entries": (NAMED, "the router's verb table"),
    "doctor": (NAMED, "the 'Start here, always' block"),
    "append": (
        DELEGATED,
        "a write verb; the skill routes writers to `subsystem-index` and makes "
        "no claim about append's behaviour",
    ),
    "put": (
        DELEGATED,
        "a write verb; delegated to `subsystem-index` for the same reason as append",
    ),
    "create": (
        NAMED,
        "🔴 NOT delegated. The skill makes an explicit claim about creating a "
        "scope's first entry, so it must name the verb that claim is about — "
        "the omission this test was written for",
    ),
}


def _load_cairn_cli():
    """Exec `scripts/cairn` as a module — it has no `.py` extension."""
    spec = importlib.util.spec_from_loader(
        "cairn_cli_verb_ledger", loader=None, origin=str(CAIRN_CLI)
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(CAIRN_CLI)
    exec(  # noqa: S102 — the file under test IS the thing being read
        compile(CAIRN_CLI.read_text(encoding="utf-8"), str(CAIRN_CLI), "exec"),
        mod.__dict__,
    )
    return mod


def _declared_verbs() -> set[str]:
    """Every subcommand the REAL argparse parser registers.

    🔴 THIS READS THE PARSER, NOT THE SOURCE, AND THE DIFFERENCE IS THE GUARD'S
    STRENGTH. The first version of this file walked the AST for
    `sub.add_parser("<literal>")` calls, which is blind to a verb registered
    through anything but an attribute call with a constant first argument — a
    loop over a table, a name built at run time, a helper wrapper. The parser
    cannot be fooled that way: it is what the CLI actually dispatches on.

    `test_cairn_split.py::…_cairn_who_is_NOT_a_cairn_subcommand` already reads
    the verb set exactly this way. Using a second, weaker technique here is the
    duplicated-predicate shape `claude/RULES.md` warns about under "One rule,
    one place" — a predicate open-coded at two sites is typically wrong at one
    of them. Both sites now ask argparse.
    """
    parser = _load_cairn_cli().build_parser()
    verbs: set[str] = set()
    for action in parser._actions:
        if getattr(action, "choices", None) and hasattr(action, "_name_parser_map"):
            verbs |= set(action.choices)
    return verbs


def test_the_verb_extraction_found_something() -> None:
    """POSITIVE CONTROL — a zero here would make every assertion below vacuous.

    `claude/RULES.md`: a reassuring zero from a check that could not see
    anything is indistinguishable from a clean result. If `build_parser` is ever
    renamed, or the subparsers stop being reachable through `_actions`, this
    test is what says so, instead of the ledger silently passing over an empty
    set.

    ⚠ INVARIANT-GUARD DISCLOSURE, because a guard that reads as regression
    coverage while providing none is worse than none: measured at base
    `8b2b960b`, **9 of this file's 10 tests were already green** before the fix.
    Only `test_a_named_verb_appears_in_the_skill_body[create]` was red. The other
    rows pin invariants the bug never violated — they are what make the ledger
    two-way, and they are NOT evidence that the ledger caught anything.
    """
    verbs = _declared_verbs()
    assert verbs, f"no subcommands parsed out of {CAIRN_CLI} — the extraction is broken"
    # Anchored on verbs whose removal would be a redesign, not a rename.
    assert {"sync", "recall", "doctor"} <= verbs, (
        f"the extraction found {sorted(verbs)}, which is missing verbs the skill's "
        f"own 'Start here' block documents — suspect the parse, not the CLI"
    )


def test_every_cli_verb_has_a_ledger_row() -> None:
    """A verb added to the CLI with no decision recorded about the skill."""
    missing = sorted(_declared_verbs() - VERB_LEDGER.keys())
    assert not missing, (
        f"`scripts/cairn` declares {missing} with no row in VERB_LEDGER.\n"
        f"Decide, in this file: does {SKILL.name} have to NAME it, or is it "
        f"DELEGATED to another skill? Write the reason in the row — a row "
        f"without one is how `create` stayed undocumented for 8 days."
    )


def test_every_ledger_row_names_a_real_verb() -> None:
    """The other direction — a row outliving the verb it describes."""
    stale = sorted(VERB_LEDGER.keys() - _declared_verbs())
    assert not stale, (
        f"VERB_LEDGER has rows for {stale}, which `scripts/cairn` no longer "
        f"declares. Drop the row AND whatever {SKILL.name} says about the verb."
    )


@pytest.mark.parametrize(
    "verb",
    sorted(v for v, (mode, _) in VERB_LEDGER.items() if mode == NAMED),
)
def test_a_named_verb_appears_in_the_skill_body(verb: str) -> None:
    """The skill must actually name each verb the ledger says it names.

    🔴 This is the assertion that would have caught the original defect: the
    skill said "There is no CREATE route" while `cairn create` shipped, and the
    word `create` appeared nowhere as a verb. It pins the verb NAME — state the
    CLI decides — so a reword of the surrounding prose cannot walk it.
    """
    body = SKILL.read_text(encoding="utf-8")
    _mode, reason = VERB_LEDGER[verb]
    assert f"cairn {verb}" in body or f"`{verb}`" in body, (
        f"{SKILL} never names the `{verb}` verb, but VERB_LEDGER marks it {NAMED} "
        f"because: {reason}"
    )


# ── the post-write check: a DERIVED ledger over EVERY skill body ────────────
# 🔴 A SEAM guard, not a component guard. Both `cairn` and `subsystem-index`
# already say, in prose, that "two skills naming one mandated command in two
# ways is how one of them goes unpinned and drifts" — and on 2026-09-16 exactly
# that had happened: `subsystem-index/SKILL.md` carried `cairn sync &&
# cairn-validate --scope <scope>` while `cairn/SKILL.md` carried the bare form
# and asserted, falsely, that it was "the SAME spelling". Nothing could see it,
# because each file was internally consistent and neither was pinned against
# the other.
#
# 🔴 THERE IS NO HAND-WRITTEN LIST OF THE SITES TO SCAN, AND THAT IS THE FIX.
# The first version of this guard (#1738, pre-audit) iterated a hardcoded
# 2-tuple of paths. Its docstring claimed it "fails when the set of skills
# naming this command GROWS or SHRINKS" and it could do neither, because the set
# it compared against was the tuple itself. Measured at that revision, the set
# was ALREADY three: `claude/skills/resume/SKILL.md` named the bare form and was
# outside the guard's field of view. A hand-maintained ledger of SITES is the
# same shape as the bug it guards against, so the sites are DERIVED from
# `git ls-files` (`public_ip_scan.repo_files`) at scan time and the only asserted
# numbers are the two ledgers below.
#
# Four properties, each with its own failing assertion so a mutant dies for the
# right reason (`claude/RULES.md` → "unreachable-guards"):
#   1. the derived set of skills naming the check EQUALS the declared ledgers —
#      fails when it GROWS (a fourth skill, undecided) or SHRINKS (a mandate
#      silently deleted);
#   2. each post-write site names it the DECLARED NUMBER of times — deleting one
#      of two sites inside a file is a SHRINK a per-file boolean cannot see;
#   3. every post-write occurrence carries the load-bearing `cairn sync && `;
#   4. no skill spells it with the PACKAGED client.
#
# 🔴 On (4): `cairn validate --scope <scope>` (space) is a REAL runnable command
# — it even syncs by default — which is what makes the swap survivable by eye.
# It is still the wrong check, and the reason is WHAT EACH ONE REPORTS. Measured
# 2026-09-17 on scope `devrc` at the pinned rev (`flake.lock` → ZacxDev/cairn
# `baee2f0`), both clients:
#
#     cairn validate --no-sync --scope devrc   rc 0, stdout    55 B, stderr 76 B
#         "cairn: devrc: 33 of 33 entry file(s) parse, 0 malformed"
#     cairn-validate --scope devrc             rc 0, stdout 6,677 B, stderr  0 B
#         carries `entry shape:`, `marker reachability:` AND `dropped lines:`
#
# So the package answers "do the files parse"; the writer answers "what is wrong,
# and what is ALREADY LOST" — `dropped lines:` has no counterpart in the package's
# output at all. Their EXIT TABLES also disagree (malformed entry: writer **3**,
# package **5**, where the package's 3 means "unreachable, no cache"), so the two
# must not be read for each other.
#
# ⚠ THE OLDER FORM OF THIS PARAGRAPH — "the package wrote 0 BYTES to stdout" — is
# STALE, and this guard shipped it once before being caught. It was true before
# the pinned rev added an unconditional summary line to `cmd_validate`, precisely
# so that "a CLEAN scope printed NOTHING and exited 0, which is byte-identical to
# a validate that parsed no files at all" would stop being possible. The claim is
# left here corrected rather than deleted because it is quoted in two other
# places (`claude/skills/cairn/SKILL.md`, `scripts/cairn-validate`'s header) and
# the next reader to meet it should know it was measured, not assumed.
#
# Matching runs over WHITESPACE-NORMALISED text, so reflowing a paragraph — which
# markdown prose gets constantly, including in the very PR that added this — is
# not a spelling change.
POST_WRITE_CHECK = "cairn sync && cairn-validate --scope <scope>"
_SYNC_PREFIX = "cairn sync && "

# 🔴 WHAT THIS MATCHES IS THE `--scope` INVOCATION, NOT THE BINARY NAME — and the
# distinction is the whole of finding F-1 from round 1. An earlier form required
# the literal ` --scope <placeholder>`, so FIVE real spellings of the same mandate
# walked it, each measured as a SURVIVING mutant against a killed positive
# control: `--scope=<scope>`, `--scope devrc` (a literal scope), `<scope1>`,
# `--store X --scope <scope>` (an intervening flag), and — worst — `cairn validate
# --scope=<scope>`, the PACKAGED client, which property (4) exists to catch.
#
# It must NOT simply widen to `cairn[- ]validate\b`: nine legitimate mentions in
# these same files are not the mandate at all — bare references to the binary, the
# single-file `--validate <path>` form, and the prose in BOTH skills that names
# `cairn validate` expressly to say it is NOT this check. Requiring a `--scope`
# argument separates them without a hand-written exemption per sentence.
#
# The gap is bounded three ways, and each one closes a measured escape:
#   * it cannot cross a BACKTICK, so `\`cairn-validate\` IS NOT \`cairn validate\``
#     cannot be stitched into a false hit;
#   * it cannot cross another `cairn`, because `sep` must describe the binary that
#     actually carries the `--scope`. Round 2 finding N-2: inside a ``` fence there
#     are no backticks at all, so `cairn-validate --validate F` on one line and
#     `cairn validate --scope <scope>` on the next collapsed into ONE match whose
#     `sep` was the HYPHEN — property (4) went green while the PACKAGED client was
#     the command being mandated, and the count under-reported 2 commands as 1.
#     Both skills already put two commands in one fence; the real one survived
#     only because a trailing `#` comment happened to push the gap past 40 chars;
#   * 40 characters, so an unrelated `--scope` further down a paragraph cannot be
#     reached. A genuinely longer invocation is a deliberate miss, not an oversight.
_VALIDATE_RE = re.compile(
    r"cairn(?P<sep>[- ])validate(?P<mid>(?:(?!cairn)[^`]){0,40}?)"
    r"--scope(?:=|\s)\s*(?P<val>[^\s`]+)"
)

# 🔴 A NEGATIVE EXAMPLE IS NOT A MANDATE. Without this, these two files became
# unable to document the bare form as the thing NOT to do — writing "❌ Never run
# `cairn-validate --scope <scope>` on its own" failed property (3) with a message
# asserting the file is wrong, while the prose was saying exactly what the guard
# means. Same remedy and same shape as `_unmarked_retractions` in
# `test_subsystem_store_api.py`, which this file's own docstring already cites: a
# marker within a short window exempts the quotation.
#
# 🔴 IT IS A WALK SURFACE, SO IT IS BOUNDED THREE WAYS — round 2 finding N-3
# demonstrated all three escapes against the unbounded first version:
#   * SAME PARAGRAPH. The scan is paragraph-scoped, so a `## ❌ Common mistakes`
#     HEADING can no longer silence the first mandate of the section beneath it.
#     Whole-document normalisation had erased the paragraph break and made them
#     adjacent. Paragraph scope is also the right unit for reflow-immunity: a
#     reflow rewraps WITHIN a paragraph, which is exactly what stays normalised.
#   * NO SENTENCE TERMINATOR between the marker and the match. "❌ Do not skip it.
#     Run `cairn sync && cairn validate --scope <scope>`" is a POSITIVE mandate
#     wearing a marker, and it silenced property (4) — the packaged client
#     mandated, guard green.
#   * the window, unchanged at 60, matching `_unmarked_retractions`.
# The exemption deliberately applies to ALL FOUR properties, not only (3): a
# marked negative example of the WRONG BINARY is prose these skills want to be
# able to write, and both already write its unparameterised cousin.
_NEGATIVE_MARKERS = ("❌", "NOT:", "Never run", "never run", "do NOT run")
_MARKER_WINDOW = 60
_SENTENCE_END = re.compile(r"[.!?]")

# 🔴 TWO-WAY, and the VALUE is the count. A per-file boolean cannot see a file
# that drops one of its two mandates, which is the SHRINK half of the docstring.
# Keys are REPO-RELATIVE PATHS, not skill names: three deployed skills live
# OUTSIDE `claude/skills/` (see `_skill_bodies`), so a bare name is both
# ambiguous and a silent-overwrite hazard in the dict comprehension below.
POST_WRITE_SITES: dict[str, int] = {
    # the verb table row, and the prose mandate below it
    "claude/skills/cairn/SKILL.md": 2,
    # the prose mandate, and the runnable command fence
    "claude/skills/subsystem-index/SKILL.md": 2,
}

# Named rather than omitted. An exemption is auditable; an absence is the defect.
READ_TIME_EXEMPT: dict[str, str] = {
    "claude/skills/resume/SKILL.md": (
        "a READ-time diagnostic, not the write protocol: it is reached only from "
        "the `🔴 NO <heading>` badge in `cairn recall` output, and `cairn recall` "
        "syncs before it reads, so the cache is already fresh at that point. It "
        "must still name the WRITER (`cairn-validate`), which property (4) pins."
    ),
}


def _skill_bodies() -> dict[str, str]:
    """Every tracked `SKILL.md` in the repo, keyed by repo-relative path.

    🔴 NOT `claude/skills/**` — that was finding F-2 from round 1, and it is the
    `resume` blind spot one directory over. THREE deployed skills live elsewhere:
    `scripts/browser-bridge/SKILL.md`, `scripts/dl-router/SKILL.md` and
    `scripts/opencode/SKILL.md` are symlinked onto `~/.claude/skills/` by
    `nix/home.nix`, which calls them "the SAME deliberate exception". They are
    agent-loaded skills like any other, so one of them teaching the unsynced form
    is exactly the failure this ledger exists to catch — and a scan rooted at
    `claude/skills` could never see it. Derived, never enumerated: 36 tracked
    SKILL.md today, and a 37th is picked up without editing this file.
    """
    return {
        str(p.relative_to(REPO)): p.read_text(encoding="utf-8")
        for p in P.repo_files(REPO)
        if p.name == "SKILL.md"
    }


class _Hit(NamedTuple):
    """One mandate, located well enough to name in a failure message.

    🔴 CARRYING `synced` AND `sep` RATHER THAN A RAW MATCH IS THE POINT. The
    previous version handed callers a `re.Match` and expected each to re-derive
    the surrounding text to answer its own question — which is how "is `m.start()`
    an offset into the same string you are slicing?" became a question anyone had
    to ask (round 2 checked it; it was fine, and it should not have been askable).
    Each property is decided HERE, against the paragraph the match came from, and
    a caller can only read the answer.
    """

    para: int       # index of the paragraph it was found in
    offset: int     # offset within that paragraph's normalised text
    sep: str        # "-" = the devrc writer, " " = the packaged client
    synced: bool    # does it carry the load-bearing `cairn sync && ` prefix


def _occurrences(text: str) -> list[_Hit]:
    """Mandates in `text`, paragraph by paragraph, negative examples removed.

    Normalisation is PER PARAGRAPH, not whole-document: a reflow rewraps within a
    paragraph, so this keeps a reflow from reading as a spelling change (the
    round-0 false positive) WITHOUT letting a heading or a previous paragraph sit
    adjacent to a mandate (round 2 finding N-3).
    """
    out: list[_Hit] = []
    for i, para in enumerate(re.split(r"\n\s*\n", text)):
        normalised = " ".join(para.split())
        if not normalised:
            continue
        for m in _VALIDATE_RE.finditer(normalised):
            window = normalised[max(0, m.start() - _MARKER_WINDOW):m.start()]
            # A marker only disclaims what FOLLOWS it in the same sentence. Once a
            # sentence has ended, the next clause is a fresh instruction. Measure
            # from the CLOSEST marker, not the first: an early marker whose
            # sentence has since ended must not decide a later one's case.
            nearest = max(
                (window.rfind(mk) for mk in _NEGATIVE_MARKERS if mk in window),
                default=-1,
            )
            if nearest >= 0 and not _SENTENCE_END.search(window[nearest:]):
                continue
            out.append(_Hit(
                para=i,
                offset=m.start(),
                sep=m.group("sep"),
                synced=normalised[:m.start()].endswith(_SYNC_PREFIX),
            ))
    return out


def _naming_skills() -> dict[str, list[_Hit]]:
    hits = {name: _occurrences(body) for name, body in _skill_bodies().items()}
    return {name: ms for name, ms in hits.items() if ms}


def test_the_set_of_skills_naming_the_write_protocol_check_is_the_declared_ledger() -> None:
    """🔴 Fails when the set GROWS or SHRINKS. Both directions, separately named.

    A count alone cannot say WHICH side moved, so the two are asserted apart.
    """
    found = set(_naming_skills())
    declared = set(POST_WRITE_SITES) | set(READ_TIME_EXEMPT)

    grew = sorted(found - declared)
    assert not grew, (
        f"SKILL.md file(s) {grew} invoke the write-protocol check with --scope and "
        f"are in NEITHER ledger. Decide which: add a count to POST_WRITE_SITES if "
        f"it mandates the check after a write, or a reason to READ_TIME_EXEMPT if "
        f"it does not. Leaving it out is how `claude/skills/resume/SKILL.md` sat "
        f"outside this guard while naming the bare form."
    )
    shrank = sorted(declared - found)
    assert not shrank, (
        f"SKILL.md file(s) {shrank} are declared as naming the write-protocol "
        f"check and no longer do. A mandate that vanishes is the failure this "
        f"ledger exists to catch — delete the ledger row deliberately, or restore "
        f"the mandate."
    )


@pytest.mark.parametrize("skill", sorted(POST_WRITE_SITES))
def test_a_post_write_site_names_the_check_the_declared_number_of_times(skill: str) -> None:
    """🔴 The COUNT is the SHRINK detector inside a single file.

    `cairn/SKILL.md` states the mandate twice on purpose — once in the verb table
    a reader scans, once in the prose that explains why the `cairn sync` is
    load-bearing. Dropping either leaves the other, and a boolean "does this file
    mention it" cannot tell.
    """
    want = POST_WRITE_SITES[skill]
    got = len(_naming_skills().get(skill, []))
    assert got == want, (
        f"{skill} invokes the write-protocol check {got} time(s); "
        f"POST_WRITE_SITES declares {want}. If a site was added or removed "
        f"deliberately, move the number — do not leave it disagreeing."
    )


@pytest.mark.parametrize("skill", sorted(POST_WRITE_SITES))
def test_every_post_write_occurrence_carries_the_load_bearing_sync_prefix(skill: str) -> None:
    """🔴 The `cairn sync` is the half that does the work.

    `cairn append` writes to the pod and does NOT touch the local cache
    (`libexec/cairn/cairn` → `cmd_append`: "never queued, never written to the
    cache"), and `cairn-validate` prepends `--store <the synced cache>` with no
    network path of its own. So an unsynced run cleanly parses the PRE-WRITE
    bytes — a silent pass on the exact defect the check exists to catch.

    `&&` is the right connector and not a convenience: `cairn sync` exits non-zero
    when it could not refresh even though a cache survived, so a failed sync SKIPS
    the validate rather than validating stale bytes under a green verdict.
    """
    stray = [
        f"paragraph {h.para}, offset {h.offset}"
        for h in _occurrences(_skill_bodies()[skill]) if not h.synced
    ]
    assert not stray, (
        f"{skill} invokes the write-protocol check at {stray} "
        f"WITHOUT the load-bearing {_SYNC_PREFIX!r} prefix. An unsynced "
        f"validate parses the PRE-WRITE bytes and passes silently. The mandated "
        f"spelling is {POST_WRITE_CHECK!r}. (If this is prose ABOUT the wrong "
        f"spelling rather than a mandate, mark it: one of {_NEGATIVE_MARKERS} "
        f"within {_MARKER_WINDOW} characters before it exempts the quotation.)"
    )


def test_no_skill_spells_the_check_with_the_PACKAGED_client() -> None:
    """🔴 `cairn validate` (space) is a real command and the WRONG check.

    Not a typo guard: the packaged client reimplements the check on the READER's
    resolver, so the two answer different questions. Measured 2026-09-17 on scope
    `devrc` at the pinned rev — package 55 B of stdout ("33 of 33 entry file(s)
    parse, 0 malformed"), writer 6,677 B carrying `entry shape:`, `marker
    reachability:` and `dropped lines:`, the last having no counterpart in the
    package's output at all. Both exit 0; only one of them says what was LOST.
    Their exit tables also disagree — malformed entry is 3 from the writer and 5
    from the package, whose own 3 means "unreachable, no cache" — so a skill that
    names the wrong one sends a reader to the wrong table as well as the wrong
    check.

    🔴 This fires on the `--scope` INVOCATION, not on the binary name, which is
    what lets both skills keep saying "`cairn-validate` IS NOT `cairn validate`"
    in prose without tripping it.
    """
    wrong = sorted(
        name for name, hits in _naming_skills().items()
        if any(h.sep == " " for h in hits)
    )
    assert not wrong, (
        f"SKILL.md file(s) {wrong} invoke the write-protocol check with the "
        f"PACKAGED client (`cairn validate`, space) instead of the devrc writer "
        f"(`cairn-validate`, hyphen). The package does not run the write-protocol "
        f"check and its exit codes differ; use {POST_WRITE_CHECK!r}."
    )
