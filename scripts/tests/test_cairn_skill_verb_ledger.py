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

import functools
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

# 🔴 A NEGATIVE EXAMPLE IS NOT A MANDATE, AND THE EXEMPTION IS AN EXPLICIT
# SENTINEL RATHER THAN A GUESS ABOUT PROSE. Without any exemption these files
# became unable to document the bare form as the thing NOT to do: writing the
# counter-example failed property (3) with a message asserting the file was
# wrong, while the prose was saying exactly what the guard means.
#
# 🔴 THE FIRST TWO ATTEMPTS SNIFFED FOR WORDS — `❌`, `Never run`, `NOT:` — WITHIN
# A WINDOW, AND TWO CONSECUTIVE AUDIT ROUNDS FOUND HOLES IN IT. Round 2 found
# three (a heading silencing the section beneath it, a marker in the previous
# paragraph, and `❌ Do not skip it. Run <the packaged client>` — a POSITIVE
# mandate wearing a marker). Round 3 found three more in the fix for those: any
# non-period clause joiner walked it (`— run …`, `: run …`), a tight list item
# leaked the marker across a bullet boundary, and `e.g.` or a version number
# between marker and command produced a FALSE POSITIVE on a legitimate
# counter-example. Each fix was a narrower guess about English.
#
# `claude/RULES.md`: *"Prefer deterministic/structural fixes over prompt-tuning,
# prose instructions, or suffix/keyword heuristics."* So the heuristic is GONE.
# A counter-example is marked by a sentinel that ordinary prose cannot produce
# and that a human never writes by accident:
#
#     <!-- not-a-mandate --> ❌ Never run `cairn-validate --scope <scope>` alone.
#
# It is an HTML comment, so it is invisible in rendered markdown and inert to
# every reader; it must sit in the same block and within `_SENTINEL_WINDOW`
# characters before the command. That closes all six escapes at once — there is
# no word to spell around, no clause boundary to argue about, and no abbreviation
# that can be mistaken for one — and it makes every exemption greppable, which a
# keyword heuristic never was.
#
# The exemption deliberately applies to ALL FOUR properties, not only (3): a
# marked counter-example of the WRONG BINARY is prose these skills want to be
# able to write, and both already write its unparameterised cousin.
_NOT_A_MANDATE = "<!-- not-a-mandate -->"
_SENTINEL_WINDOW = 120

# 🔴 TWO-WAY, and the VALUE is the count. A per-file boolean cannot see a file
# that drops one of its two mandates, which is the SHRINK half of the docstring.
# Keys are REPO-RELATIVE `SKILL.md` PATHS, not skill names: three deployed skills
# live OUTSIDE `claude/skills/` (see `_skill_sources`), so a bare name is both
# ambiguous and a silent-overwrite hazard in the dict comprehension below.
#
# ⚠ A KEY NAMES THE WHOLE SKILL, body plus `reference/`/`flows/` sidecars, so the
# count below is the skill's TOTAL. Both post-write sites happen to state their
# mandates in the body today; a demotion to a sidecar would keep the count and
# change `_Hit.source`, which is exactly the outcome we want — the obligation
# belongs to the skill, not to one of its files.
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
        "must still name the WRITER (`cairn-validate`), which property (4) pins. "
        "⚠ Since the 2026-09-17 prune the sentence lives in this skill's sidecar "
        "`claude/skills/resume/reference/cairn-recall.md`, NOT in the body — "
        "which is why the scan reads a skill's whole directory, and what "
        "`…_is_satisfied_by_its_SIDECAR` below pins."
    ),
}


# 🔴 A SKILL IS ITS DIRECTORY, NOT ITS `SKILL.md`. `claude/CLAUDE.md` defines two
# sibling dirs that ship with a skill and are loaded by the agent on demand:
# `reference/` (durable facts) and `flows/` (procedures). Both are deployed by the
# same `home.file` copy as the body, so a mandate written into either is an
# instruction the agent will read — and until 2026-09-17 neither was scanned.
_SIDECAR_DIRS = ("reference", "flows")


@functools.lru_cache(maxsize=1)
def _skill_sources() -> dict[str, dict[str, str]]:
    """Every tracked skill, keyed by its `SKILL.md` repo-relative path.

    The value maps each repo-relative path that makes up that skill's
    agent-loaded body — the `SKILL.md` itself plus every tracked `*.md` under its
    `reference/` or `flows/` dir, at any depth — to that file's text.

    🔴 NOT `claude/skills/**` — that was finding F-2 from round 1, and it is the
    `resume` blind spot one directory over. THREE deployed skills live elsewhere:
    `scripts/browser-bridge/SKILL.md`, `scripts/dl-router/SKILL.md` and
    `scripts/opencode/SKILL.md` are symlinked onto `~/.claude/skills/` by
    `nix/home.nix`, which calls them "the SAME deliberate exception". They are
    agent-loaded skills like any other, so one of them teaching the unsynced form
    is exactly the failure this ledger exists to catch — and a scan rooted at
    `claude/skills` could never see it. Derived, never enumerated: 36 tracked
    SKILL.md today, and a 37th is picked up without editing this file.

    🔴 AND THE SAME CLASS ONE LEVEL DOWN — F-2's mirror image, MEASURED as a real
    failure rather than reasoned about. The scan above reads `SKILL.md` FILES; the
    `resume` prune (#1745) moved this skill's `cairn-validate` sentence into
    `claude/skills/resume/reference/cairn-recall.md`, and the SHRINK arm fired on
    a mandate that had not vanished at all — it had moved one directory down,
    into a file the scan could not see. The blind spot is not merely that false
    alarm: a skill could have taught the UNSYNCED form, or the PACKAGED client,
    from a sidecar and all four properties would have passed. F-2 was "the scan is
    rooted too narrowly across the repo"; this is "the scan is rooted too narrowly
    inside a skill". Fixing it makes the guard WIDER, and the `resume` row is now
    satisfied by the sidecar rather than by the body — which
    `test_the_read_time_exemption_for_resume_is_satisfied_by_its_SIDECAR` pins,
    so a future move back into the body is a decision and not an accident.

    The ledgers stay keyed by `SKILL.md` because the QUESTION they answer is
    per-skill — "does this skill teach the check, and in which spelling" — and an
    agent loading `/resume` can reach every file below that key. `_Hit.source`
    carries which one, so nothing is lost from a failure message.

    ⚠ MEMOISED, and the returned dict must be treated as READ-ONLY — it is the
    same object on every call. Each call shells out to `git ls-files` and reads
    ~130 markdown files, and the widening turned one such walk per test into one
    per (test x skill). MEASURED on this module, same tree, same box: 5.9s before
    the widening, 11.8s widened-uncached, 7.9s widened-cached — so the cache buys
    back most of what the wider scan costs, and the residue is the sidecar reads
    themselves. Nothing in this repo writes to a skill during the run, so there is
    nothing to invalidate; a test that ever needs to would have to clear it.
    """
    files = list(P.repo_files(REPO))
    # skill DIR -> the repo-relative key that names it
    skills = {p.parent: str(p.relative_to(REPO)) for p in files if p.name == "SKILL.md"}
    sources: dict[str, dict[str, str]] = {key: {} for key in skills.values()}
    for p in files:
        if p.suffix != ".md":
            continue
        owner: str | None = None
        if p.name == "SKILL.md" and p.parent in skills:
            owner = skills[p.parent]
        else:
            for parent in p.parents:
                if parent.name in _SIDECAR_DIRS and parent.parent in skills:
                    owner = skills[parent.parent]
                    break
        if owner is not None:
            sources[owner][str(p.relative_to(REPO))] = p.read_text(encoding="utf-8")
    return sources


class _Hit(NamedTuple):
    """One mandate, located well enough to name in a failure message.

    🔴 CARRYING `synced` AND `sep` RATHER THAN A RAW MATCH IS THE POINT. The
    previous version handed callers a `re.Match` and expected each to re-derive
    the surrounding text to answer its own question — which is how "is `m.start()`
    an offset into the same string you are slicing?" became a question anyone had
    to ask (round 2 checked it; it was fine, and it should not have been askable).
    Each property is decided HERE, against the block the match came from, and
    a caller can only read the answer.

    `source` is the repo-relative path the hit came from, which stopped being the
    ledger key when the scan widened to a skill's sidecars: a skill is now keyed
    by its `SKILL.md` and may be mandating the check from `reference/<topic>.md`.
    A failure naming only the key would send a maintainer to the wrong file.
    """

    block: int      # index of the block it was found in
    offset: int     # offset within that block's normalised text
    sep: str        # "-" = the devrc writer, " " = the packaged client
    synced: bool    # does it carry the load-bearing `cairn sync && ` prefix
    source: str = ""  # repo-relative path the text came from


# A BLOCK is the unit the sentinel must share with the command it disclaims.
# Blank lines separate blocks, and so does the start of a list item, a table row
# or a heading: round 3 finding F3 showed a sentinel in one tight bullet leaking
# onto the next, which is the shape markdown uses most.
_BLOCK_BREAK = re.compile(r"\n\s*\n|\n(?=\s*(?:[-*+]\s|\d+\.\s|\||#))")


def _occurrences(text: str, source: str = "") -> list[_Hit]:
    """Mandates in `text`, block by block, sentinel-marked counter-examples removed.

    Normalisation is PER BLOCK, not whole-document: a reflow rewraps WITHIN a
    block, so this keeps a reflow from reading as a spelling change (the round-0
    false positive) without letting a heading, a previous paragraph or a
    neighbouring bullet sit adjacent to a mandate (round 2 N-3, round 3 F3).
    """
    out: list[_Hit] = []
    for i, block in enumerate(_BLOCK_BREAK.split(text)):
        normalised = " ".join(block.split())
        if not normalised:
            continue
        for m in _VALIDATE_RE.finditer(normalised):
            window = normalised[max(0, m.start() - _SENTINEL_WINDOW):m.start()]
            if _NOT_A_MANDATE in window:
                continue
            out.append(_Hit(
                block=i,
                offset=m.start(),
                sep=m.group("sep"),
                synced=normalised[:m.start()].endswith(_SYNC_PREFIX),
                source=source,
            ))
    return out


def _skill_hits(key: str) -> list[_Hit]:
    """Every mandate anywhere in one skill — body and sidecars, path-ordered."""
    return [
        h
        for src, text in sorted(_skill_sources()[key].items())
        for h in _occurrences(text, src)
    ]


def _naming_skills() -> dict[str, list[_Hit]]:
    hits = {key: _skill_hits(key) for key in _skill_sources()}
    return {key: ms for key, ms in hits.items() if ms}


def test_the_scan_sees_a_real_corpus_AND_reaches_sidecars() -> None:
    """POSITIVE CONTROL on the widened scan, in BOTH halves.

    `claude/RULES.md`: a reassuring zero from a scan wired to nothing is
    indistinguishable from a clean result — and widening a scan adds a SECOND way
    to be wired to nothing, which the old control could not see. Before the
    widening every skill had exactly one source, so "the scan found 36 skills"
    stayed true with the sidecar walk deleted.

    So this asserts the corpus is non-trivial AND that at least one skill
    genuinely contributes a file that is not its `SKILL.md`. It is deliberately
    NOT a pinned count: the sidecar corpus grows constantly and a ratchet here
    would be a permanently-red gate over prose nobody is guarding.
    """
    sources = _skill_sources()
    assert len(sources) >= 30, (
        f"only {len(sources)} SKILL.md found — suspect `repo_files`, not the repo"
    )
    assert all(key in files for key, files in sources.items()), (
        "a skill key that does not map to its own SKILL.md means the owner "
        "attribution below is wrong"
    )
    sidecars = {
        src
        for files in sources.values()
        for src in files
        if not src.endswith("/SKILL.md")
    }
    assert sidecars, (
        "the scan reached NO sidecar at all. Every 'not found' this module "
        "reports about a sidecar would be vacuous — suspect `_SIDECAR_DIRS` or "
        "the parent walk in `_skill_sources`."
    )


def test_the_scan_does_NOT_swallow_every_md_beside_a_skill() -> None:
    """The other edge of the widening: `reference/` and `flows/` are the loadable
    sidecars `CLAUDE.md` defines, and the scan must stop there.

    Two concrete files decide it, both real and both under a skill's directory:
    `scripts/browser-bridge/reference/errors.md` IS part of that skill and must be
    in; `scripts/browser-bridge/tests/fixtures/oopif-rig/README.md` is a TEST
    FIXTURE and must not be. Scanning fixtures would make a deliberate
    counter-example in test data fail a guard about what a skill TEACHES — a
    false red with no correct fix.
    """
    files = _skill_sources()["scripts/browser-bridge/SKILL.md"]
    assert "scripts/browser-bridge/reference/errors.md" in files, (
        "a `reference/` sidecar of a skill outside `claude/skills/` was not "
        "attributed to it — the parent walk is wrong for those three skills"
    )
    assert "scripts/browser-bridge/README.md" not in files
    assert (
        "scripts/browser-bridge/tests/fixtures/oopif-rig/README.md" not in files
    ), "the scan reached test fixtures, which are not agent-loaded skill content"


def test_the_read_time_exemption_for_resume_is_satisfied_by_its_SIDECAR() -> None:
    """🔴 REGRESSION TEST for the break this widening fixes. Measured: at
    `origin/main` merged with #1745 and BEFORE this change, the SHRINK arm failed
    with `['claude/skills/resume/SKILL.md'] are declared as naming the
    write-protocol check and no longer do` — the sentence had not vanished, it
    had moved into `reference/cairn-recall.md` one directory down.

    Pinning WHERE it is satisfied from, rather than only THAT it is, is what
    stops this from silently reverting: if the sentence moves back into the body
    this goes red and a maintainer updates the ledger reason deliberately.
    """
    hits = _naming_skills().get("claude/skills/resume/SKILL.md", [])
    assert hits, (
        "the resume skill no longer names `cairn-validate --scope` anywhere in "
        "its body or sidecars — see READ_TIME_EXEMPT for why it must"
    )
    assert {h.source for h in hits} == {
        "claude/skills/resume/reference/cairn-recall.md"
    }, (
        f"the resume skill's read-time diagnostic is stated in "
        f"{sorted({h.source for h in hits})}, not (only) in the sidecar "
        f"READ_TIME_EXEMPT names. Move the ledger reason in the SAME commit."
    )
    assert all(h.sep == "-" for h in hits), (
        "property (4): the exemption's reason says the resume skill must still "
        "name the WRITER `cairn-validate`, not the packaged `cairn validate`"
    )


def test_the_set_of_skills_naming_the_write_protocol_check_is_the_declared_ledger() -> None:
    """🔴 Fails when the set GROWS or SHRINKS. Both directions, separately named.

    A count alone cannot say WHICH side moved, so the two are asserted apart.
    """
    found = set(_naming_skills())
    declared = set(POST_WRITE_SITES) | set(READ_TIME_EXEMPT)

    grew = sorted(found - declared)
    assert not grew, (
        f"skill(s) {grew} invoke the write-protocol check with --scope and "
        f"are in NEITHER ledger. Decide which: add a count to POST_WRITE_SITES if "
        f"it mandates the check after a write, or a reason to READ_TIME_EXEMPT if "
        f"it does not. Leaving it out is how `claude/skills/resume/SKILL.md` sat "
        f"outside this guard while naming the bare form. (A key names the whole "
        f"skill DIRECTORY — body plus `reference/`/`flows/` sidecars — so the "
        f"mandate may be in a sidecar; `_skill_hits(<key>)` says which file.)"
    )
    shrank = sorted(declared - found)
    assert not shrank, (
        f"skill(s) {shrank} are declared as naming the write-protocol "
        f"check and no longer do, in their body OR any `reference/`/`flows/` "
        f"sidecar. A mandate that vanishes is the failure this "
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

    ⚠ THE COUNT IS OVER THE WHOLE SKILL — body plus `reference/`/`flows/`
    sidecars — since the scan widened. A number that moves because a mandate was
    DEMOTED to a sidecar has not changed the skill's obligations, so it must be
    re-derived rather than reasoned about: run the test and read what it prints.
    """
    want = POST_WRITE_SITES[skill]
    hits = _skill_hits(skill)
    assert len(hits) == want, (
        f"{skill} invokes the write-protocol check {len(hits)} time(s); "
        f"POST_WRITE_SITES declares {want}. If a site was added or removed "
        f"deliberately, move the number — do not leave it disagreeing.\n"
        f"  found at: {[f'{h.source}:{h.block}/{h.offset}' for h in hits]}"
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
        f"{h.source} block {h.block}, offset {h.offset}"
        for h in _skill_hits(skill) if not h.synced
    ]
    assert not stray, (
        f"{skill} invokes the write-protocol check at {stray} "
        f"WITHOUT the load-bearing {_SYNC_PREFIX!r} prefix. An unsynced "
        f"validate parses the PRE-WRITE bytes and passes silently. The mandated "
        f"spelling is {POST_WRITE_CHECK!r}. (If this is a COUNTER-EXAMPLE "
        f"rather than a mandate, mark it: the literal sentinel {_NOT_A_MANDATE!r} "
        f"in the SAME block and within {_SENTINEL_WINDOW} characters before it "
        f"exempts the command. Nothing else exempts anything.)"
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
        f"{name} ({', '.join(sorted({h.source for h in hits if h.sep == ' '}))})"
        for name, hits in _naming_skills().items()
        if any(h.sep == " " for h in hits)
    )
    assert not wrong, (
        f"skill(s) {wrong} invoke the write-protocol check with the "
        f"PACKAGED client (`cairn validate`, space) instead of the devrc writer "
        f"(`cairn-validate`, hyphen). The package does not run the write-protocol "
        f"check and its exit codes differ; use {POST_WRITE_CHECK!r}."
    )


# ── `_occurrences` DECIDES EVERYTHING ABOVE, SO IT IS TESTED DIRECTLY ────────
# 🔴 THE FOUR TESTS ABOVE RUN AGAINST THE LIVE CORPUS, AND THE LIVE CORPUS DOES
# NOT EXERCISE THE DECISION LOGIC. Round 3 finding F1, measured: the block split,
# the sentinel, the window and the `cairn` tempering could EACH be deleted and all
# sixteen tests stayed GREEN — seven surviving mutants against a positive control
# that did go red. The cause is simple and was invisible from the corpus side: no
# governed file contains a sentinel, so that whole branch never executed, and the
# three ledgered files happen not to contain the shapes the split exists to
# separate. A guard reading as coverage while providing none is the exact defect
# this PR exists to oppose, so the decision logic gets fixtures of its own.
#
# Every case below is a REGRESSION test — each string is a shape that was
# measured to walk some earlier version of this guard during the #1738 audit
# ladder, named by its finding. They are synthetic on purpose: the corpus cannot
# be relied on to keep containing them.
_MANDATE = "cairn sync && cairn-validate --scope <scope>"


class TestTheOccurrenceScanner:
    """Direct fixtures for `_occurrences`, one per constraint it implements."""

    def test_a_plain_mandate_is_found(self) -> None:
        """POSITIVE CONTROL. Without this, every 'not found' below is
        indistinguishable from a scanner wired to nothing."""
        hits = _occurrences(f"After a write, run `{_MANDATE}`.")
        assert len(hits) == 1
        assert hits[0].sep == "-" and hits[0].synced is True

    def test_a_bare_mandate_is_found_and_reported_unsynced(self) -> None:
        hits = _occurrences("Run `cairn-validate --scope <scope>`.")
        assert len(hits) == 1 and hits[0].synced is False

    @pytest.mark.parametrize("spelling", [
        "cairn-validate --scope=<scope>",          # round 1 F-1, M10
        "cairn-validate --scope devrc",            # round 1 F-1, M11
        "cairn-validate --scope <scope1>",         # round 1 F-1, M13
        "cairn-validate --store X --scope <scope>",  # round 1 F-1, M14
        "cairn validate --scope=<scope>",          # round 1 F-1, M12 (packaged)
    ])
    def test_every_spelling_of_the_invocation_is_seen(self, spelling: str) -> None:
        """🔴 Round 1 F-1. The regex once required a literal ` --scope <x>`, so all
        five of these walked it while mandating the very command it polices."""
        assert len(_occurrences(f"Run `{spelling}`.")) == 1

    def test_a_bare_binary_mention_is_NOT_a_mandate(self) -> None:
        """The other half of F-1: widening to `cairn[- ]validate` would pull in
        nine legitimate mentions in the governed files. Requiring `--scope` is
        what separates naming the binary from invoking it."""
        assert _occurrences("`cairn-validate` IS NOT `cairn validate`.") == []
        assert _occurrences("The fix is `cairn-validate --validate <path>`.") == []

    def test_the_gap_between_binary_and_scope_is_bounded(self) -> None:
        """40 characters. A longer invocation is a deliberate miss, not an
        oversight — stated here so the bound is a decision and not an accident."""
        assert len(_occurrences(f"`cairn-validate {'x' * 39} --scope <s>`")) == 0
        assert len(_occurrences(f"`cairn-validate {'x' * 20} --scope <s>`")) == 1

    def test_the_gap_cannot_cross_another_cairn(self) -> None:
        """🔴 Round 3 N-2. Inside a fence there are no backticks to stop the gap,
        so two commands collapsed into ONE match whose `sep` was the HYPHEN —
        property (4) green while the PACKAGED client carried the `--scope`."""
        hits = _occurrences(
            "```\ncairn-validate --validate F\ncairn validate --scope <scope>\n```"
        )
        assert len(hits) == 1
        assert hits[0].sep == " ", "the packaged client must be the one reported"

    def test_the_gap_cannot_cross_a_backtick(self) -> None:
        assert _occurrences("`cairn-validate` and separately `--scope <scope>`") == []

    def test_a_reflow_of_correct_prose_is_not_a_change(self) -> None:
        """🔴 Round 0 M3. This is why matching is normalised at all; a markdown
        paragraph gets rewrapped constantly."""
        flat = f"the mandated post-write check is `{_MANDATE}` — the SAME spelling"
        wrapped = f"the mandated post-write check is `cairn sync &&\ncairn-validate --scope <scope>` — the SAME spelling"
        assert len(_occurrences(flat)) == len(_occurrences(wrapped)) == 1
        assert _occurrences(flat)[0].synced == _occurrences(wrapped)[0].synced is True

    # ── the sentinel ────────────────────────────────────────────────────────
    def test_the_sentinel_exempts_a_counter_example(self) -> None:
        assert _occurrences(
            f"{_NOT_A_MANDATE} ❌ Never run `cairn-validate --scope <scope>` alone."
        ) == []

    def test_WORDS_ALONE_DO_NOT_EXEMPT(self) -> None:
        """🔴 THE WHOLE REASON THE SENTINEL EXISTS. Two audit rounds found six
        ways to walk a keyword heuristic; these are four of them, and every one
        must now be SEEN. `claude/RULES.md`: prefer a deterministic fix."""
        for prose in (
            "❌ Never run `cairn-validate --scope <scope>` alone.",        # no sentinel
            "❌ Do not skip it. Run `cairn validate --scope <scope>`.",     # round 2 N3a
            "❌ Never run it bare — run `cairn validate --scope <scope>`",  # round 3 F2
            "❌ Never run e.g. `cairn-validate --scope <scope>` alone.",    # round 3 F4
        ):
            assert len(_occurrences(prose)) == 1, f"walked by: {prose!r}"

    def test_the_sentinel_does_not_reach_across_a_blank_line(self) -> None:
        """🔴 Round 2 N3c."""
        assert len(_occurrences(
            f"{_NOT_A_MANDATE} ❌ never do this.\n\nAfter a write: `cairn-validate --scope <scope>`"
        )) == 1

    def test_the_sentinel_does_not_reach_across_a_heading(self) -> None:
        """🔴 Round 2 N3b."""
        assert len(_occurrences(
            f"{_NOT_A_MANDATE} bad.\n## Common mistakes\nRun `cairn-validate --scope <scope>`."
        )) == 1

    def test_the_sentinel_does_not_reach_across_a_tight_list_item(self) -> None:
        """🔴 Round 3 F3 — the shape markdown uses most, and the one a
        paragraph-only split could not separate."""
        assert len(_occurrences(
            f"- {_NOT_A_MANDATE} never the bare form\n"
            f"- Always run `cairn validate --scope <scope>`"
        )) == 1

    def test_the_sentinel_window_is_TIGHT(self) -> None:
        """A sentinel far enough away is not about this command."""
        near = f"{_NOT_A_MANDATE} {'x' * 40} `cairn-validate --scope <s>`"
        far = f"{_NOT_A_MANDATE} {'x' * 400} `cairn-validate --scope <s>`"
        assert _occurrences(near) == []
        assert len(_occurrences(far)) == 1
