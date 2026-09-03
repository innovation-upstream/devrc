#!/usr/bin/env python3
"""Query the handoff-doc section index — the READ half of `handoff_index.py`.

This is what `/resume` and subagents call instead of re-deriving a finding that
is already written down somewhere in the handoff corpus. It answers with SECTIONS
(a `### ` investigation sub-block, a ranked item, a gotchas block), because the
unit of value in this corpus is a paragraph, not a document.

    handoff_search.py --query "clickhouse port-forward times out"
    handoff_search.py --query "stash" --section gotcha --repo devrc --limit 5


🔴 EVERY RESPONSE CARRIES A RECALL BANNER
------------------------------------------
Wording and posture are `scripts/lib/subsystem_recall.py`'s, deliberately — the
same agent reads both surfaces in the same session, and two spellings of one
provenance claim is how a caveat stops being read. The claim is: these are notes
PAST sessions wrote, nothing here was re-derived just now, nothing was matched
against live state, and a hit may describe a gotcha that has since been fixed.
A result is a POINTER TO VERIFY, never a current reading.

It is a single function (`recall_banner`) printed by every renderer on every
status including the empty ones, rather than a sentence per branch: a caveat
spelled at N sites is wrong at N−1 of them, and the branch a caveat gets dropped
from is always the one nobody expected to reach.


🔴 THE SILENT-ZERO GUARD
------------------------
A zero-result query has FIVE causes that mean different things:

    the corpus does not say that      an ANSWER. Carry on.
    your FILTER emptied the corpus    a CALLER ERROR. The query ran against zero
                                      rows because `--repo`/`--section` selected
                                      none — the corpus was never asked.
    nothing was ever indexed          a BROKEN INDEX. The query never ran against
                                      anything, and reading it as an answer is a
                                      confident zero with an empty table behind it.
    the REPOS could not be read       an UNMEASURABLE CORPUS — `--offline` only.
                                      There is no table and no unit to blame; the
                                      checkouts this run was pointed at do not
                                      resolve. See below. "ALL of them" — a run
                                      where SOME resolved is the next row, not
                                      this one.
    the repos read fine and hold      ZERO HANDOFF DOCS DERIVED — `--offline`
    no handoff docs, OR hold some     only. Same "no table, no unit" as above,
    that git could not produce        and it kept rendering as BROKEN INDEX for
                                      one round after that was fixed, because
                                      only the unresolvable half was fixed.
                                      🔴 ONE STATUS, TWO MECHANISMS, AND THE
                                      RENDERING SPLITS ON `unreadable`: a ref
                                      that lists handoff docs whose blobs git
                                      cannot produce also derives zero, and
                                      saying "holds none" there sends the reader
                                      to WRITE a doc that is already committed.

`claude/RULES.md` → "An EMPTY RESULT cannot distinguish two mechanisms". So every
response — hit, no-match, empty-scope, unmeasured and broken alike — carries the
literal `indexed_docs=N indexed_sections=M`, and the zero cases are rendered with
sentences that SHARE NO OPENING PHRASE:

    NO MATCH — …            the SCOPE holds rows and none of them matched
    🔴 EMPTY SCOPE — …      the index holds rows; your filter selects none of them
    🔴 BROKEN INDEX — …     the index holds NOTHING; nothing was searched
    🔴 UNMEASURABLE CORPUS  the repos themselves did not resolve; there is no
                            index to be broken
    🔴 ZERO HANDOFF DOCS    the repos resolved and carry no handoff doc in their
       DERIVED              mainline; again no index to be broken

🔴 THE FOURTH ONE IS A DIAGNOSIS BUG THIS MODULE SHIPPED WITH, and it is exactly
the empty-result trap it lectures about elsewhere. `--offline` builds its corpus
by calling `handoff_index.derive_repo`, which records a STRUCTURAL `unmeasured`
reason per repo — and the offline seam read the derived rows and threw that flag
away. MEASURED, two variants: `--offline --offline-repo /does/not/exist` printed
`🔴 BROKEN INDEX — this table holds ZERO documents … Rebuild it (--rebuild
--write) or check the handoff-index-sync unit`, rc 3, when there is neither a
table nor a unit anywhere in that code path; and with all four handles unset and
no `--offline-repo`, `default_repos()` returned `[]` and produced the SAME false
BROKEN INDEX with not one warning printed. The sibling CLI
(`handoff_index.py`) got the second case right — `no repos to index. Pass
--repo, or set one of: $DEVRC, …`, rc 2 — so the two front ends disagreed about
one fact. Both are fixed: the no-repos case is a usage error with the sibling's
wording, and an unmeasurable repo set gets its own status and exit code.


🔴 THE MIDDLE ONE IS WHY `stats()` TAKES THE SAME FILTERS `search()` DOES. It
originally did not, so the counts printed beside a scoped query described the
WHOLE index: `--repo totally-bogus` rendered `NO MATCH — the index WAS searched`
next to a reassuring `indexed_docs=352`, and both halves of that were false. The
absolute path case is the one that bites, because `$DEVRC` is pre-exported on
this box and `--repo $DEVRC` is the natural thing to type — this `--repo` takes a
repo LABEL (`devrc`), while `handoff_index.py --repo` takes a PATH. A label the
index does not hold is now REJECTED with the labels it does hold, rather than
answered. Both a scoped count of zero and an unknown label produce `empty-scope`,
because they are one fact — the filter, not the corpus, is what is empty.

`SearchOutcome.status` names which, in values that share no spelling
(`hit` / `no-match` / `empty-scope` / `broken-index` / `unmeasured-corpus` /
`derived-zero-docs`), so a
caller can switch on one field rather than parse prose. The exit code follows the
status, and NO non-answer exits zero: an empty index, an empty scope and a corpus
that could not be measured are none of them readings.

🔴 THE EXIT-CODE CONTRACT, AND THE ONE DECISION IN IT THAT IS ARGUABLE
----------------------------------------------------------------------
    0   hit, no-match                     ANSWERS about a scope that was searched
    2   usage (bad --limit, no repos)     argv is wrong; nothing ran
    3   broken-index                      the table is empty
    4   empty-scope                       the FILTER selected nothing
    6   unmeasured-corpus                 NO repo resolved (--offline)
    7   derived-zero-docs                 the repos resolved and derived no handoff
                                          docs — they hold none, or every one they
                                          hold failed to read (--offline)

🔴 `empty-scope` EXITS 4 FOR **BOTH** OF ITS REASONS, INCLUDING `no-rows` — a
filter that is entirely valid (a real repo label, a declared section kind) over a
corpus that simply has nothing under it. That was a deliberate choice against the
alternative of returning 0 for it, and the argument is worth having in front of
you because the flag side is real: a `set -e` script running
`--repo devrc --section gotcha` over a corpus with no gotcha sections now dies,
where reading it as "no results" would not. It is 4 anyway because THE EXIT CODE
IS THE ONE CHANNEL A SCRIPTED CALLER READS WITHOUT THE PROSE, and `no-rows` and
`no-match` are precisely the two zeros this whole module exists to keep apart:
one searched N>0 sections and found nothing, the other searched ZERO. Collapsing
them in that channel re-creates the defect at the only layer where the loud
rendering cannot help. The contract is therefore stated as "no non-answer exits
0", with no exceptions — a rule with one carve-out is a rule people forget. There
are no production callers today; a future one that WANTS the zero should branch on
`scope_reason` (carried in the text and in `--json`), not on the exit code.


THE BACKEND IS INJECTED
-----------------------
`handoff_index.SectionStore` is the seam. `--offline` derives the corpus from git
in-process and answers from `MemorySectionStore`; the default connects to Postgres
via the shared `MailDB` helper. 🔴 The two RANK DIFFERENTLY — Postgres uses
`ts_rank` over an english-stemmed tsvector, the memory backend counts distinct
query tokens — so the renderer prints `backend=` on every response and an offline
result must never be read as a prediction of the indexed one. What they share,
from ONE table, is `handoff_index.SECTION_BOOST` and the recency tiebreak.

⚠ THE POSTGRES PATH IS NOT EXERCISED BY ANY TEST. The gate runs in a nix sandbox
with no cluster and no database, so every claim about it is a claim about code
that has been read, not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import handoff_index  # noqa: E402
from handoff_index import (  # noqa: E402
    SECTIONS,
    SECTION_BOOST,
    Hit,
    IndexStats,
    MemorySectionStore,
    SectionStore,
)

__all__ = [
    "DEFAULT_LIMIT",
    "BODY_CLIP",
    "STATUSES",
    "MIN_LIMIT",
    "EXIT_CODES",
    "ANSWER_STATUSES",
    "REASON_KEYED_STATUSES",
    "SCOPE_REASONS",
    "SCOPE_REASON_EXIT_CODES",
    "exit_code_for",
    "SearchOutcome",
    "recall_banner",
    "stats_line",
    "offline_targets",
    "run_search",
    "render",
    "outcome_json",
    "main",
]

#: A display cap, printed when it bites — never silent. 10 because the point of
#: searching is to read LESS than the doc, and a section is already a paragraph;
#: `DEFAULT_MAX_HITS` in `subsystem_recall` is 10 for the same measured reason.
DEFAULT_LIMIT = 10

#: How much of a hit's body is rendered. A section is the retrieval unit, but an
#: `## Open investigations` block with no `### ` subdivision can be several KB and
#: this output is paid by a resuming session's context. Truncation is MARKED.
BODY_CLIP = 700

#: The five outcomes, in values that share no spelling — see the module docstring.
#: `broken-index` is NOT spelled `*-empty` and `empty-scope` is NOT spelled
#: `*-no-match`: an index with nothing in it, a FILTER that selects nothing, and a
#: query that matched nothing within a populated scope are three different facts
#: with three different next actions, and two statuses that share a word get read
#: as one. `unmeasured-corpus` is the fourth zero and shares a spelling with none
#: of them either — "the repos did not resolve" is not "the table is empty", and
#: the fix (a checkout / an env handle) is nothing like `--rebuild --write`.
#: `derived-zero-docs` is the FIFTH zero and shares a spelling with none of the
#: others either: the repos this run derived from RESOLVED and hold no handoff
#: docs at all. It exists because `broken-index` was being rendered for it —
#: naming a table to rebuild and a unit to check on a path that opens neither —
#: which is the same misdiagnosis `unmeasured-corpus` was carved out to fix, in
#: its sibling case. Only the unresolvable half had been fixed.
STATUSES: tuple[str, ...] = (
    "hit", "no-match", "empty-scope", "broken-index", "unmeasured-corpus",
    "derived-zero-docs",
)

#: 🔴 A `--limit` BELOW THIS IS A CALLER ERROR, NOT A NARROW SEARCH. `--limit 0`
#: returns zero hits from a perfectly good index and used to render the
#: corpus-is-silent prose — the same false claim `--repo <unknown>` made, by
#: another door. `--limit -1` is worse: the memory backend's `hits[:-1]` quietly
#: drops the LAST hit and returns a plausible list, while Postgres rejects
#: `LIMIT -1` outright, so the two backends disagree about what a negative limit
#: even means. Bounded at parse time, where the message can name the flag.
MIN_LIMIT = 1

#: status -> process exit code, for the statuses whose code depends on the status
#: ALONE. 🔴 PINNED against `STATUSES` by
#: `test_every_status_has_an_exit_code_and_vice_versa`, which asserts the three
#: ledgers below PARTITION `STATUSES`: a status in none of them falls through to
#: `.get(status, 0)` and exits 0 — the fluent-zero failure, one level up, in the
#: one channel a scripted caller reads.
EXIT_CODES: dict[str, int] = {
    "broken-index": 3, "unmeasured-corpus": 6, "derived-zero-docs": 7,
}

#: The statuses that ARE answers, and therefore exit 0 by definition. Stated as a
#: named ledger rather than left implicit so the partition test can assert
#: coverage instead of a subset.
ANSWER_STATUSES: tuple[str, ...] = ("hit", "no-match")

#: The statuses whose exit code is decided by the REASON, not by the status.
#: One member today; named so `exit_code_for` reads as a rule rather than as a
#: special case for one literal.
REASON_KEYED_STATUSES: tuple[str, ...] = ("empty-scope",)

#: WHY a scope came back empty. One status, because a caller's action is the same
#: (fix the filter); two reasons, because the fix is not. `unknown-repo` is a
#: label the index does not hold — a typo, or a PATH where a label belongs, which
#: is the natural mistake on a box that pre-exports `$DEVRC`. `no-rows` is a
#: perfectly valid filter over a corpus that has nothing under it. Pinned two-way
#: against what `run_search` emits.
SCOPE_REASONS: tuple[str, ...] = ("unknown-repo", "no-rows")

#: 🔴 BOTH ARE 4, AND THE `no-rows` HALF IS THE ARGUABLE DECISION — the module
#: docstring's exit-code section makes the case in full. Short version: the exit
#: code is the one channel read without the prose, and `no-rows` (searched ZERO
#: sections) versus `no-match` (searched N>0 and found nothing) are the two zeros
#: this module exists to keep apart. Pinned two-way against `SCOPE_REASONS`, so a
#: new reason cannot be added without deciding its code.
SCOPE_REASON_EXIT_CODES: dict[str, int] = {"unknown-repo": 4, "no-rows": 4}


def exit_code_for(outcome: "SearchOutcome") -> int:
    """The process exit code for one outcome. THE ONE PLACE THE MAPPING IS READ.

    🔴 THE UNKNOWN-KEY FALLBACK IS NON-ZERO, NOT ZERO. A reason the ledger does
    not carry is a bug in this module, and the safe direction for a bug is the one
    a caller notices. The partition test makes it unreachable; the direction it
    points is what matters if it ever is not."""
    if outcome.status in REASON_KEYED_STATUSES:
        return SCOPE_REASON_EXIT_CODES.get(outcome.scope_reason, 4)
    return EXIT_CODES.get(outcome.status, 0)


@dataclass(frozen=True)
class SearchOutcome:
    """One query's answer, and everything needed to read its zero correctly.

    `stats` is the WHOLE index; `scoped` is what the `repo`/`sections` filters
    left. They are separate fields rather than one number because the pair is the
    guard: equal means no filter narrowed anything, and `scoped.indexed_sections
    == 0` with `stats.indexed_docs > 0` is the empty-scope case that used to
    render as an answer about the corpus."""

    query: str
    stats: IndexStats
    hits: tuple[Hit, ...]
    status: str
    backend: str
    repo: str | None = None
    sections: tuple[str, ...] = ()
    limit: int = DEFAULT_LIMIT
    scoped: IndexStats | None = None
    known_repos: tuple[str, ...] = ()
    #: Only meaningful on `empty-scope`. See `SCOPE_REASONS`.
    scope_reason: str | None = None
    #: `((label, reason), …)` for every repo the corpus-builder could NOT measure.
    #: 🔴 STRUCTURAL, CARRIED ACROSS THE SEAM. `handoff_index.derive_repo` records
    #: this per repo and `_offline_store` used to drop it on the floor, which is
    #: how an unresolvable checkout got diagnosed as a broken Postgres table. It
    #: is the field, not a grep over the warning prose, for `RepoDerivation`'s
    #: reason: a reworded warning must not be able to disarm a guard.
    unmeasured: tuple[tuple[str, str], ...] = ()
    #: The repo labels this run was POINTED AT, non-empty only when the run built
    #: its own corpus (`--offline`). 🔴 IT IS THE DENOMINATOR, and its absence is
    #: what let `unmeasured-corpus` say "all 1 repo(s) … failed to resolve" for a
    #: run pointed at 2 — a count of failures read as a count of attempts. It is
    #: also what tells a corpus this run BUILT (and found empty) from a Postgres
    #: table that is empty, which are the two rung-1 zeros with opposite remedies.
    targets: tuple[str, ...] = ()
    #: `((label, path), …)` for every doc that IS in a repo's mainline ref and
    #: that git could not produce. 🔴 IT IS WHAT MAKES rc 7's SENTENCE TRUE OR
    #: FALSE. `derive_repo` reports both "the ref holds no handoff docs" and
    #: "every handoff doc in the ref failed to read" as `docs == 0` with no
    #: `unmeasured` reason, and `derived-zero-docs` rendered the first sentence
    #: for both — MEASURED by deleting a committed doc's blob from the object
    #: store: `ls-tree` still listed it, `git show` failed, and the CLI said the
    #: repo holds no `claudedocs/handoff-*.md`. Same empty-result trap as
    #: `handoff_paths_in_ref`'s `None`-vs-`()`, carried one seam further.
    unreadable: tuple[tuple[str, str], ...] = ()

    @property
    def in_scope(self) -> IndexStats:
        """The counts the query actually ran against — the scoped ones when a
        filter was applied, otherwise the whole index. Never None, so no renderer
        has to branch and forget."""
        return self.scoped if self.scoped is not None else self.stats

    @property
    def filtered(self) -> bool:
        return self.repo is not None or bool(self.sections)

    @property
    def truncated(self) -> bool:
        return len(self.hits) >= self.limit


def recall_banner() -> str:
    """The provenance caveat, printed on EVERY status by EVERY renderer.

    🔴 A MODULE-LEVEL FUNCTION, not a property on the outcome and not a literal in
    each branch, for `subsystem_recall.caveat_text`'s reason: there is more than
    one output path and a caveat spelled twice is wrong in one of them.

    What it must keep saying, and why each clause is load-bearing:
      * from handoff docs — the provenance label, matching `subsystem_recall`'s
        `from index` in posture so an agent reading both surfaces sees one claim;
      * POINTERS TO VERIFY — the whole point: a hit is not a current reading;
      * a gotcha may already be fixed — the specific, measured failure mode. The
        `forgejo` case in `subsystem_recall` served an outstanding action for 22
        days after the fix shipped, and this corpus is APPEND-MOSTLY, so a
        superseded paragraph is not deleted, it just stops being true;
      * what the window CANNOT see — an uncommitted doc (reported by
        `handoff_index` as a durability hole, never indexed), anything written
        since the last index run, and live state of any kind."""
    return (
        "from handoff docs — RECALL, NOT LIVE OBSERVATION. Every result below was "
        "written by a PAST session into a `claudedocs/handoff-*.md` doc. Nothing here "
        "was re-derived just now and nothing was matched against anything THIS session "
        "has done, so a hit may describe a gotcha that HAS SINCE BEEN FIXED, a plan that "
        "was abandoned, or a diagnosis that was later retracted in a different doc. "
        "Treat every line as a POINTER TO VERIFY, never as a current reading — re-check "
        "it against the repo before acting on it. This window CANNOT see: live state of "
        "any kind, any doc written or edited since the last index run, and any handoff "
        "doc that is on disk but NOT committed to its repo's mainline (those are "
        "reported as durability holes by `handoff_index.py` and are deliberately never "
        "indexed)."
    )


def stats_line(stats: IndexStats, backend: str, scoped: IndexStats | None = None) -> str:
    """🔴 THE LINE THAT MAKES A ZERO READABLE. Emitted on every status, in a fixed
    `key=value` shape so a caller can grep it rather than parse prose.

    🔴 WHEN A FILTER IS APPLIED, BOTH NUMBERS ARE PRINTED. `indexed_*` is the whole
    index and `in_scope_*` is what the query could reach. Printing only the first
    beside a scoped query is the exact defect this pair was added to close: it
    reads as "352 documents were searched and none matched" when the true
    statement is "0 documents were searched". Printing only the SECOND would be
    the mirror error — a caller could not tell an empty scope from an empty index.
    So: both, always, whenever they can differ."""
    line = (
        f"indexed_docs={stats.indexed_docs} indexed_sections={stats.indexed_sections} "
        f"backend={backend}"
    )
    if scoped is not None:
        line += (
            f" in_scope_docs={scoped.indexed_docs} "
            f"in_scope_sections={scoped.indexed_sections}"
        )
    return line


def run_search(
    store: SectionStore,
    query: str,
    *,
    backend: str,
    repo: str | None = None,
    sections: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
    unmeasured: Sequence[tuple[str, str]] = (),
    targets: Sequence[str] = (),
    unreadable: Sequence[tuple[str, str]] = (),
) -> SearchOutcome:
    """Query the store and CLASSIFY the result. The one place `status` is decided.

    🔴 THE INDEX IS CHECKED BEFORE THE ZERO IS INTERPRETED, and the order is the
    guard. `stats()` is read FIRST and unconditionally: an empty index yields
    `broken-index` whether or not the query would have matched, because with zero
    rows the query did not run against anything and "no match" would be a claim
    about a corpus that was never consulted.

    🔴 AND THE SCOPE IS CHECKED BEFORE THE ZERO IS INTERPRETED TOO. Three ladder
    rungs, widest first, because each one makes the next readable:

        1. index empty            -> broken-index   nothing was ever indexed
        2. filter selects nothing -> empty-scope    the caller emptied the corpus
        3. otherwise              -> hit / no-match a real answer about the scope

    Rung 2 is what the original guard was missing. It fires on an unknown `--repo`
    label (checked against `store.repos()`, so the message can name the real ones)
    AND on a scoped count of zero from a known filter — one status, because they
    are one fact: the filter, not the corpus, is empty. Getting to rung 3 is the
    only thing that licenses the sentence "the docs do not discuss this".

    🔴 AND RUNG 1 IS ITSELF **THREE** MECHANISMS, WHICH IS WHY `unmeasured` AND
    `targets` BOTH REACH HERE. An empty store means one of:

        the table holds nothing              -> broken-index      rc 3
        EVERY repo failed to resolve         -> unmeasured-corpus rc 6
        the repos resolved, and hold no docs -> derived-zero-docs rc 7

    They have nothing in common but the observable — the exact `claude/RULES.md`
    empty-result trap — and three different fixes. The caller is the only thing
    that knows which: `--offline` BUILDS its corpus and passes both the labels it
    was pointed at (`targets`) and the per-repo `unmeasured` reasons; the
    Postgres path builds none and passes neither. A non-empty `targets` is
    therefore exactly "this run derived its own corpus", and it is what keeps
    `broken-index`'s remedy — rebuild the table, check the unit — off a path
    that opens neither.

    🔴 BOTH SPLITS OF RUNG 1 CAME FROM THE SAME BUG, ONE ROUND APART. The first
    fix carved out `unmeasured-corpus` but keyed it on `unmeasured` being merely
    NON-EMPTY, which was wrong in two directions at once, both measured:

      * `--offline-repo <resolves, no docs> --offline-repo /missing` rendered
        "all **1** repo(s) this run was pointed at failed to resolve, so no
        corpus was ever built" — it was pointed at 2, one resolved, and a corpus
        WAS built. The remedy then told the reader to fix checkout paths, one of
        which was fine. Hence `len(unmeasured) == len(targets)`: the word "all"
        is now true by construction, not by hope.
      * `--offline` over a repo that resolves and holds zero handoff docs still
        rendered `🔴 BROKEN INDEX — this table holds ZERO documents … Rebuild it
        (--rebuild --write) or check the handoff-index-sync unit`, rc 3 — the
        original misdiagnosis, surviving untouched in its sibling case because
        only the unresolvable half had been fixed.

    ⚠ SCOPED TO THE ZERO CASE ON PURPOSE. A corpus that is PARTIALLY unmeasured
    but still holds rows answers normally; its warnings are printed by the CLI
    beside the result. Widening this to "any unmeasured repo" would turn a host
    that legitimately lacks one checkout into a permanent non-answer, which is the
    same permanently-red-gate mistake `handoff_index.rebuild_refusal` had to
    unwind."""
    stats = store.stats()
    if stats.indexed_docs == 0:
        # Do not even run the query: with zero rows there is nothing to ask, and
        # a store that fails on a query against an empty table would turn a
        # diagnosable broken index into a traceback.
        if unmeasured and len(unmeasured) == len(targets):
            status = "unmeasured-corpus"
        elif targets:
            status = "derived-zero-docs"
        else:
            status = "broken-index"
        return SearchOutcome(
            query=query, stats=stats, hits=(), status=status,
            backend=backend,
            repo=repo, sections=tuple(sections), limit=limit, scoped=stats,
            known_repos=tuple(store.repos()), unmeasured=tuple(unmeasured),
            targets=tuple(targets),
            unreadable=tuple(unreadable),
        )

    known = tuple(store.repos())
    scoped = store.stats(repo=repo, sections=sections)
    # 🔴 ONE STATUS, TWO REASONS, AND THE REASONS ARE NOT REDUNDANT. Both are
    # "the filter emptied the corpus", so a caller switching on `status` sees one
    # case — but the two have DIFFERENT fixes and the message has to say which.
    # `unknown-repo` is a typo or a path where a label belongs, answerable with
    # the list of labels the index holds. `no-rows` is a filter that is entirely
    # valid — a real repo, a declared section kind — that this corpus simply has
    # nothing under, where listing the labels would be noise. Collapsing them
    # into a bare count check makes the label list unreachable prose.
    if repo is not None and repo not in known:
        reason = "unknown-repo"
    elif scoped.indexed_sections == 0:
        reason = "no-rows"
    else:
        reason = None
    if reason is not None:
        return SearchOutcome(
            query=query, stats=stats, hits=(), status="empty-scope", backend=backend,
            repo=repo, sections=tuple(sections), limit=limit, scoped=scoped,
            known_repos=known, scope_reason=reason, unmeasured=tuple(unmeasured),
            targets=tuple(targets),
            unreadable=tuple(unreadable),
        )

    hits = tuple(store.search(query, repo=repo, sections=sections, limit=limit))
    return SearchOutcome(
        query=query,
        stats=stats,
        hits=hits,
        status="hit" if hits else "no-match",
        backend=backend,
        repo=repo,
        sections=tuple(sections),
        limit=limit,
        scoped=scoped,
        known_repos=known,
        unmeasured=tuple(unmeasured),
        targets=tuple(targets),
        unreadable=tuple(unreadable),
    )


def _clip(text: str, limit: int = BODY_CLIP) -> str:
    body = text.strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + f"\n    … [clipped, {len(body):,} B total — open the doc]"


def render(outcome: SearchOutcome) -> str:
    """The text response. Banner, then stats, then the status-specific block."""
    scope = [f"query={outcome.query!r}"]
    if outcome.repo:
        scope.append(f"repo={outcome.repo}")
    if outcome.sections:
        scope.append("section=" + ",".join(outcome.sections))
    lines = [
        recall_banner(),
        "",
        stats_line(outcome.stats, outcome.backend,
                   outcome.scoped if outcome.filtered else None),
        "  ".join(scope),
    ]

    if outcome.status == "unmeasured-corpus":
        # 🔴 NAMES NEITHER A TABLE NOR A UNIT, AND THAT IS THE POINT. This branch
        # exists because the `broken-index` block below was rendered for it, and
        # every remedy that block offers — `--rebuild --write`, the
        # handoff-index-sync unit — is about a Postgres index that this code path
        # never touches. Sending a reader to rebuild an index when their checkout
        # is missing is a wrong diagnosis delivered with a confident next step,
        # which is worse than no next step at all.
        detail = ", ".join(f"{label} ({reason})" for label, reason in outcome.unmeasured)
        lines += [
            "",
            f"🔴 UNMEASURABLE CORPUS — all {len(outcome.targets)} repo(s) this run "
            f"was pointed at failed to resolve, so no corpus was ever built: {detail}.",
            "   This is NOT a broken index and NOT an answer about the corpus. There is "
            "no table to rebuild and no unit to check on this path — the repos "
            "themselves are what did not resolve.",
            "   Fix the checkout path(s) or the `$DEVRC`/`$HOMELAB`/… handles, or pass "
            "`--offline-repo <path>` explicitly, then re-run.",
        ]
        return "\n".join(lines)

    if outcome.status == "derived-zero-docs":
        # 🔴 SHARES NO OPENING PHRASE WITH ANY OTHER ZERO, and specifically names
        # NEITHER A TABLE NOR A UNIT — for `unmeasured-corpus`'s reason, in the
        # sibling case that fix missed. `--offline` over a repo that resolves and
        # holds no handoff docs opens no database, so "rebuild the index" and
        # "check the handoff-index-sync unit" are a confident wrong next step.
        # The remedy here is about DOCS, because that is the thing that is absent.
        failed = {label for label, _ in outcome.unmeasured}
        resolved = [t for t in outcome.targets if t not in failed]
        # 🔴 "HOLDS NONE" AND "COULD NOT READ ANY" ARE TWO MECHANISMS BEHIND ONE
        # ZERO, AND THIS BRANCH USED TO ASSERT THE FIRST FOR BOTH. REPRODUCED by
        # committing a handoff doc and deleting its blob from `.git/objects`:
        # `ls-tree` still lists the path, `git show` fails, `derive_repo` returns
        # `docs=0` with NO `unmeasured` reason — and the sentence below claimed
        # the repo holds no `handoff-*.md`, sending the reader to write one. The
        # remedies are opposite (write a doc / repair the object store), which is
        # exactly `claude/RULES.md`'s empty-result trap. `unreadable` is the
        # upstream signal the two mechanisms disagree about.
        if outcome.unreadable:
            detail = ", ".join(f"{lab}:{path}" for lab, path in outcome.unreadable)
            lines += [
                "",
                f"🔴 ZERO HANDOFF DOCS DERIVED — and NOT because there are none. The "
                f"{len(resolved)} repo(s) this run read "
                f"({', '.join(resolved) or '(none)'}) resolved a mainline ref that DOES "
                f"list {len(outcome.unreadable)} `{handoff_index.HANDOFF_DIR}/"
                f"handoff-*.md`, and git could not produce the content of a single one: "
                f"{detail}.",
                "   So the corpus is empty because every document in it FAILED TO READ, "
                "not because the ref holds none. Do NOT read this as 'write a handoff "
                "doc' — the docs are committed. A `git show <ref>:<path>` that fails on "
                "a path `git ls-tree` lists means the object store is incomplete: try "
                "`git fsck` and re-fetch, then re-run.",
                "   This is NOT a broken index either: the corpus was BUILT here, "
                "in-process, from git — there is no table to rebuild and no unit to "
                "check on this path. It is also NOT the answer 'the corpus does not "
                "mention that'.",
            ]
            return "\n".join(lines)
        lines += [
            "",
            f"🔴 ZERO HANDOFF DOCS DERIVED — the {len(resolved)} repo(s) this run read "
            f"({', '.join(resolved) or '(none)'}) resolved a mainline ref and hold no "
            f"`{handoff_index.HANDOFF_DIR}/handoff-*.md` in it, so the query ran "
            f"against an empty corpus.",
            "   This is NOT a broken index: the corpus was BUILT here, in-process, from "
            "git — there is no table to rebuild and no unit to check on this path. It "
            "is also NOT the answer 'the corpus does not mention that'.",
        ]
        if outcome.unmeasured:
            detail = ", ".join(f"{lab} ({r})" for lab, r in outcome.unmeasured)
            lines.append(
                f"   Separately, {len(outcome.unmeasured)} of the {len(outcome.targets)} "
                f"repo(s) named did not resolve AT ALL: {detail}. Those are the "
                f"checkout paths to fix; the ones listed above are fine."
            )
        lines.append(
            "   The corpus is read from each repo's MAINLINE REF, not the working tree: "
            "a handoff doc that exists on disk but is not committed and pushed is "
            "invisible here by design."
        )
        return "\n".join(lines)

    if outcome.status == "broken-index":
        lines += [
            "",
            "🔴 BROKEN INDEX — this table holds ZERO documents, so the query above ran "
            "against NOTHING.",
            "   This is NOT the answer 'the corpus does not mention that': the corpus was "
            "never consulted.",
            "   Rebuild it (`scripts/lib/handoff_index.py --rebuild --write`) or check "
            "the handoff-index-sync unit, then re-run.",
        ]
        return "\n".join(lines)

    if outcome.status == "empty-scope":
        # 🔴 SHARES NO OPENING PHRASE WITH EITHER OTHER ZERO, and specifically does
        # NOT say "the index WAS searched" — because it was not.
        lines += [
            "",
            f"🔴 EMPTY SCOPE — your filter selects 0 of the index's "
            f"{outcome.stats.indexed_sections} section(s), so the query matched nothing "
            f"it was never shown.",
            "   This is NOT the answer 'the corpus does not mention that': the filter, "
            "not the corpus, is what is empty.",
        ]
        if outcome.scope_reason == "unknown-repo":
            known = ", ".join(outcome.known_repos) or "(none)"
            lines += [
                f"   NO REPO IS INDEXED UNDER THE LABEL {outcome.repo!r}. --repo takes a "
                f"repo LABEL, not a path (`handoff_index.py --repo` is the one that "
                f"takes a path).",
                f"   Indexed labels: {known}.",
            ]
        else:
            lines += [
                "   The filter is VALID — every label and section kind in it is real — "
                "but this corpus holds no row under it.",
                "   Widen or drop --repo / --section and re-run.",
            ]
        return "\n".join(lines)

    if outcome.status == "no-match":
        scoped_note = (
            f" (the whole index holds {outcome.stats.indexed_sections})"
            if outcome.filtered else ""
        )
        lines += [
            "",
            f"NO MATCH — the index WAS searched and none of the "
            f"{outcome.in_scope.indexed_sections} section(s) in scope matched"
            f"{scoped_note}.",
            "   That is an answer about the corpus, not a broken tool: the docs do not "
            "discuss this in these words.",
        ]
        # 🔴 "WIDEN --repo / --section" IS NOT A REMEDY WHEN THERE IS NO FILTER TO
        # WIDEN. This line read "Try fewer/other terms, or widen --repo / --section"
        # unconditionally, and an UNFILTERED no-match is the common case: the reader
        # is sent to relax a filter they never passed, which either wastes a round
        # trip or — worse — reads as "the zero came from your scope", when the scope
        # was the whole index. Same shape as `handoff_index`'s three remedy lines: a
        # next step that names a command without checking the state it prints in.
        # `outcome.filtered` is the discriminator and it is already read two lines
        # above for `scoped_note`, which is what makes the omission a miss rather
        # than a missing measurement.
        lines.append(
            "   Try fewer/other terms, or widen --repo / --section before concluding "
            "nobody wrote it down."
            if outcome.filtered else
            "   Try fewer or different terms before concluding nobody wrote it down. "
            "There is no filter to widen: this run passed no --repo / --section, so "
            "every section in the index was already in scope."
        )
        return "\n".join(lines)

    boosted = ", ".join(f"{k}×{v}" for k, v in sorted(SECTION_BOOST.items()))
    lines += [
        "",
        f"{len(outcome.hits)} section(s), best first. Ranked by text match, boosted "
        f"{boosted}; recency breaks ties.",
    ]
    for h in outcome.hits:
        lines += [
            "",
            f"── {h.repo}/{h.slug}  [{h.section}#{h.ordinal}]  "
            f"{h.doc_date or 'no-date'}  rank={h.rank:.4f}",
            f"   {h.doc_path}",
            f"   ## {h.heading}",
        ]
        lines += [f"    {ln}" for ln in _clip(h.body).splitlines()]
    if outcome.truncated:
        lines += [
            "",
            f"(--limit {outcome.limit} reached — there may be more. Raise it or narrow "
            f"with --repo/--section.)",
        ]
    return "\n".join(lines)


def outcome_json(outcome: SearchOutcome) -> dict:
    """The same facts as `render`, including the banner — a JSON consumer is an
    agent too, and dropping the caveat for it would be dropping it for the reader
    most likely to paste a hit somewhere as fact."""
    return {
        "caveat": recall_banner(),
        "status": outcome.status,
        "backend": outcome.backend,
        "query": outcome.query,
        "repo": outcome.repo,
        "sections": list(outcome.sections),
        "limit": outcome.limit,
        "indexed_docs": outcome.stats.indexed_docs,
        "indexed_sections": outcome.stats.indexed_sections,
        # The scoped pair travels with the JSON for the reason it is printed in
        # the text: a consumer switching on `status` still needs to be able to
        # say WHY a zero is a zero without re-running the query.
        "in_scope_docs": outcome.in_scope.indexed_docs,
        "in_scope_sections": outcome.in_scope.indexed_sections,
        "known_repos": list(outcome.known_repos),
        "scope_reason": outcome.scope_reason,
        # Carried on EVERY status, not only `unmeasured-corpus`: a partially
        # unmeasured corpus answers normally, and a consumer reading those hits
        # needs to know which repos contributed nothing to them.
        "unmeasured": [
            {"repo": label, "reason": reason} for label, reason in outcome.unmeasured
        ],
        # 🔴 THE DENOMINATOR, ON THE MACHINE SURFACE TOO. `unmeasured` alone is a
        # count of FAILURES, and reading it as a count of ATTEMPTS is exactly how
        # the text renderer came to say "all 1 repo(s) … failed" for a run pointed
        # at 2. Empty on the Postgres path, which builds no corpus.
        "targets": list(outcome.targets),
        # 🔴 THE OTHER MECHANISM BEHIND A ZERO CORPUS, and it belongs here for
        # `unmeasured`'s reason: a consumer reading `indexed_docs=0` with an
        # empty `unmeasured` would otherwise conclude the repos hold no handoff
        # docs, which is the exact false sentence rc 7 used to print. Non-empty
        # means the mainline LISTS docs that git could not produce.
        "unreadable": [
            {"repo": label, "doc_path": path} for label, path in outcome.unreadable
        ],
        "exit_code": exit_code_for(outcome),
        "hits": [
            {
                "repo": h.repo, "slug": h.slug, "doc_path": h.doc_path,
                "doc_date": h.doc_date, "section": h.section, "ordinal": h.ordinal,
                "heading": h.heading, "body": h.body, "rank": h.rank,
            }
            for h in outcome.hits
        ],
    }


def offline_targets(repos: Sequence[str]) -> list[tuple[str, str]]:
    """The `(path, label)` pairs `--offline` will derive from.

    Split out of `_offline_store` so `main` can tell "no repos were named at all"
    from "the repos named did not resolve" BEFORE building a corpus — they are
    different errors with different fixes, and the empty list used to reach the
    store as a silent zero and render as `🔴 BROKEN INDEX`."""
    return [(r, Path(r).name) for r in repos] or handoff_index.default_repos()


def _offline_store(
    targets: Sequence[tuple[str, str]],
) -> tuple[MemorySectionStore, list[str], tuple[tuple[str, str], ...],
           tuple[tuple[str, str], ...]]:
    """The in-process corpus, its warnings, what it could NOT measure, and which
    committed docs it could not READ.

    🔴 THE THIRD ELEMENT IS THE FIX FOR A MISDIAGNOSIS. This function called
    `derive_repo` — which sets a STRUCTURAL `unmeasured` reason per repo — and
    returned only the rows and the warning strings, so `run_search` saw an empty
    store and could not tell an unresolvable checkout from an empty table. The
    flag is carried across the seam rather than re-derived or grepped out of the
    warnings, for `RepoDerivation.unmeasured`'s reason.

    🔴 THE FOURTH IS THE SAME FIX ONE MECHANISM LATER, AND IT IS NOT COVERED BY
    THE THIRD. A repo whose mainline LISTS handoff docs that git cannot produce
    is fully MEASURED — `unmeasured is None` — and contributes `docs == 0`, so it
    reaches `run_search` as the same empty store a genuinely doc-less repo does.
    rc 7 then asserted the doc-less reading. Carried as `(label, path)` pairs so
    the renderer can name the documents rather than a count."""
    derivations = [handoff_index.derive_repo(p, label=lab) for p, lab in targets]
    rows = [s for d in derivations for s in d.sections]
    warnings = [w for d in derivations for w in d.warnings]
    unmeasured = tuple(
        (d.label, d.unmeasured) for d in derivations if d.unmeasured is not None
    )
    unreadable = tuple(
        (d.label, path) for d in derivations for path in d.unreadable
    )
    return MemorySectionStore(rows), warnings, unmeasured, unreadable


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="search the handoff-doc section index",
        epilog="Results are POINTERS TO VERIFY, not current readings.",
    )
    ap.add_argument("--query", required=True)
    ap.add_argument("--repo", default=None,
                    help="restrict to one repo LABEL — the repo's directory name as "
                         "the index stores it (e.g. `devrc`), NOT a filesystem path. "
                         "`handoff_index.py --repo` is the one that takes a path. An "
                         "unknown label is rejected, never answered with a zero")
    ap.add_argument("--section", action="append", default=[], choices=list(SECTIONS),
                    help="restrict to one or more section kinds (repeatable)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                    help=f"maximum hits to return (>= {MIN_LIMIT})")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="derive from git in-process and answer with no database "
                         "(a DIFFERENT ranker — see the module docstring)")
    ap.add_argument("--offline-repo", action="append", default=[],
                    help="repo root for --offline (repeatable)")
    args = ap.parse_args(argv)

    # 🔴 BOUNDED BEFORE THE QUERY RUNS, not clamped silently. `--limit 0` produced
    # zero hits from a healthy index and rendered the corpus-is-silent prose; a
    # negative limit means two different things to the two backends (`hits[:-1]`
    # drops the last hit; Postgres rejects `LIMIT -1`). Neither is a search the
    # caller meant, so this is a usage error with its own exit code, not a value
    # to guess at.
    if args.limit < MIN_LIMIT:
        print(f"handoff-search: --limit must be >= {MIN_LIMIT} (got {args.limit}); "
              f"a limit below that returns an empty result from a healthy index.",
              file=sys.stderr)
        return 2

    if args.offline:
        # 🔴 "NOBODY NAMED A REPO" IS A USAGE ERROR, NOT A BROKEN INDEX. With all
        # four handles unset and no `--offline-repo`, `default_repos()` returns
        # `[]`, the corpus is empty for the most ordinary reason there is, and
        # this used to render `🔴 BROKEN INDEX` with ZERO warnings — a confident
        # wrong diagnosis about a database this path never opens. The wording and
        # the rc match `handoff_index.py`'s for the same condition: two front ends
        # over one corpus must not disagree about one fact.
        targets = offline_targets(args.offline_repo)
        if not targets:
            print(
                "handoff-search: no repos to index. Pass --offline-repo, or set one "
                "of: "
                + ", ".join(f"${h}" for h in handoff_index.REPO_ENV_HANDLES),
                file=sys.stderr,
            )
            return 2
        store, warnings, unmeasured, unreadable = _offline_store(targets)
        for w in warnings:
            print(w, file=sys.stderr)
        backend = "memory"
        outcome = run_search(store, args.query, backend=backend, repo=args.repo,
                             sections=args.section, limit=args.limit,
                             unmeasured=unmeasured, unreadable=unreadable,
                             targets=tuple(label for _, label in targets))
    else:
        MailDB = handoff_index.import_maildb()
        with MailDB() as db:
            store = handoff_index.PostgresSectionStore(db.conn)
            outcome = run_search(store, args.query, backend="postgres", repo=args.repo,
                                 sections=args.section, limit=args.limit)

    print(json.dumps(outcome_json(outcome), indent=2) if args.json else render(outcome))
    # 🔴 NO NON-ANSWER EXITS ZERO, and each gets a DIFFERENT code. A broken index
    # is a broken environment, an unmeasurable corpus is a missing checkout and an
    # empty scope is a caller error; a caller that only checks the exit code must
    # read none of them as "no results", and one that wants to tell them apart
    # must not have to parse prose. The mapping lives in `exit_code_for` — see the
    # module docstring for why `empty-scope`/`no-rows` is 4 and not 0.
    return exit_code_for(outcome)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
