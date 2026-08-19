#!/usr/bin/env python3
"""Surface what the `/analyze-service` index already records about a scope.

THE READ HALF. The store had two writers — `/analyze-service` (infra recon) and
`/handoff` (`scripts/lib/subsystem_touch.py`) — and no general reader. `/resume`
never opened it: a fresh session read the handoff doc, reconciled live state via
`resume-state.sh`, and the store's stated purpose — "the terse pointer sheet that
outlives this handoff doc" — outlived the doc with nobody looking at it.

🔴 THE PATTERN IS COPIED, NOT INVENTED. `/analyze-service` step 1 is why the
store stuck at all: "Read at recon START… surface its `## Pointers` +
`## Nuance / work-history` before re-discovering gotchas", labelled `from index`,
never presented as live observation. This module is that step, made deterministic
and pointed at a scope instead of at one service.

🔴 IT NEVER WRITES. Not "does not today": there is no write path in this file,
and `TestRecallNeverWrites` hashes a store tree either side of every mode and
every failure. The store is curated, client-confidential and has no off-machine
backup, so the only writers stay the two confirm-gated, diff-first ones in the
skills.

🔴 IT DOES NOT REIMPLEMENT MATCHING. Ref normalization, kind splitting, tier
resolution, ambiguity and the on-disk index shape all come from
`subsystem_resolver`, which is the executable authority. Scope derivation comes
from `subsystem_touch.scope_for_repo` — the WRITER's own function, imported
rather than re-spelled, so a reader and a writer in the same repo can never
disagree about which scope directory they mean. Neither a second matcher nor a
second normalizer is introduced here.


WHY IT SURFACES THE WHOLE SCOPE, AND NOT THE WRITER'S PATH-DERIVED SET
---------------------------------------------------------------------
`subsystem_touch` picks entries by resolving a PATH WINDOW — the session's
transcript, the PRs a branch landed, or git — and keeping what clears
`min_paths`. That is right for a writer: it is answering "what did I touch?",
and it must not propose a bullet against a subsystem the session never opened.

Pointing the same rule at `/resume` produces a reader that goes blind exactly
when recall is worth most. A resuming session has:

  * NO PRs of its own — it has not done anything yet;
  * a transcript one turn long, naming no files;
  * a git window that is empty in the ordinary resume state (a clean tree at
    `origin/main`), and otherwise describes the PREVIOUS session's leftovers.

All three collapse to `looked-at-nothing`. So the writer's selection rule would
answer "the index has nothing for you" on a fresh session in a repo whose scope
is full — a confident zero produced by the question, not by the store.

The reader therefore selects by SCOPE ALONE: every entry under `<scope>/`,
in canonical ref order. Three reasons it can afford to:

  1. The unit is a REPO. A scope holds one repo's subsystems, and the store's own
     bloat discipline — pointers not copies, dated bullets ≤2 lines, prune on
     resolve — is what keeps an entry small enough that "all of them" is a page
     rather than a dump.
  2. Any narrowing predicate would be a SECOND selection rule that the writer
     does not have, free to drift from it, and its failure mode is to hide an
     entry silently. That is the exact shape this codebase keeps paying for.
  3. The volume is bounded and observable rather than assumed: `--limit` caps the
     output and a truncation is PRINTED (`… N more`), never silent.

`--ref` narrows to one entry when the caller already knows what it is looking at,
and it goes through `resolve_ref_tiered` — the writer's resolver — so an
ambiguous ref is reported with its candidates and never picked.


…AND WHY "THE WHOLE SCOPE" IS NOW AN *INDEX* PLUS *ONE BODY*
------------------------------------------------------------
The argument above is about SELECTION — which entries the reader is allowed to
know about — and it stands: the reader still reads every entry in the scope, and
`--list`/the digest index still name every one of them. What did not stand is the
claim `claude/skills/resume/SKILL.md` made about the COST: "it costs a page, not a
dump".

Measured on 2026-08-13 against the scope holding 25 of the store's 29 entries:

    old default (`--limit 12`)   31,485 B  (~7,871 tok)  and it hid 13 of 25
    old `--limit 25`             62,643 B  (~15,660 tok)
    fixed caveat + header         1,290 B
    per entry                    ~2,454 B  (min 1,245 / median 2,277 / max 5,183)

So the old default was the worst of both worlds — expensive AND incomplete — and
a step that displaces the task it was loaded for gets dropped from `/resume`,
which is how the store went unread the first time.

The digest splits the two things the old output conflated:

    the INDEX     one line per entry, EVERY entry, never truncated (~60 B/line).
                  What a resuming session actually needs: what is on record here,
                  how much of it there is, and how sensitive it is.
    ONE BODY      the single entry most likely to be the one being resumed,
                  printed in full — because an index with no worked example is a
                  menu, and a menu costs a round trip.

Selection of that one body goes through `subsystem_resolver.associate_paths` —
the WRITER's own path→subsystem matcher — over a path window read out of the
repo's newest handoff doc (`focus_window`), and falls back to the newest entry by
mtime. Which of the two fired is PRINTED, never implicit.


…AND WHY THE INDEX IS NOW PAGINATED AT 100
------------------------------------------
The index line is ~60 B, so an unbounded index is a cost that grows with the
store forever: today's biggest scope holds 26 entries (~1.6 KB of index), but the
store is append-mostly and pruning is manual. `LISTING_PAGE_SIZE` caps a page at
100 lines (~6 KB) and the remainder is announced, never dropped — `--page N`
reaches it. The order is NEWEST-FIRST BY FILE MTIME, tie-broken by ref, and the
header says so: capping an ALPHABETICAL list hides entries by an accident of
their name, while capping a RECENCY list hides the stale ones, which is the only
cut that means anything to a resuming session. (`full` mode's bodies keep the
canonical ref-ascending order — that output is a dump by request and its reader
is scanning for a name, not for recency.)


…AND WHY THERE IS A `--search`
------------------------------
Reading by WHOLE ENTRY does not scale on either axis: entry count grows and entry
size grows. `--search` reads by MATCH instead — it returns HUNKS, each carrying
its own `scope/ref`, section and `sensitivity=`, because hunk output interleaves
entries and a label printed once per invocation would get separated from the
content it governs.

🔴 IT SHELLS OUT TO NOTHING, and that is a measurement, not a preference. The
whole store is ~81 KB / 30 files; a pure-stdlib scan of it takes single-digit
milliseconds, so `rg` would buy nothing and would cost a nix-store binary that is
not guaranteed on PATH plus an output format nobody pinned.

The scorer is TWO-STAGE, which is the shape a prototype round found correct
(ranking whole lines by `difflib` ratio returned noise; ranking a line's token
set returned front matter):

  1. ENTRY — an entry qualifies when its NAME (ref + aliases) or its best BLOCK
     clears the threshold.
  2. BLOCK — within a qualifying entry, every block at or above the threshold is
     emitted. If none is but the NAME qualified, the entry's best block is
     emitted with `basis=entry-name`, so a name-only hit is a hit and not the
     silent nothing the two-stage prototype produced.

A BLOCK — not a line — is the unit because a bullet is the unit the store is
written in: `nginx` on one line and `rate-limit` on its continuation line is ONE
fact, and a per-line scorer scores each half at 0.5 and returns nothing.

Compound/concatenated query terms are handled DELIBERATELY rather than by
lowering the cutoff: a block's candidate token set includes every ADJACENT PAIR
JOINED (`rate`,`limit` ⇒ `ratelimit`), so `ratelimit` hits `rate-limit` at 1.00,
and the reverse direction is covered by the prefix/substring rules. Tokens
shorter than `MIN_INEXACT_LEN` must match EXACTLY — one rule, and it is why `pod`
does not fuzzily reach `pgo`.

🔴 A SUB-THRESHOLD ZERO IS ACCOUNTED FOR. `SearchReport.best_below` carries the
highest-scoring hunk that did NOT clear the threshold, and the renderer prints it
on a no-match. "Nothing matched" and "something nearly matched at 0.50" are
different facts and a searcher that prints the same thing for both is the
confident-wrong instrument this module exists to avoid.


CONTRACT SUMMARY
----------------
    extract_sections(text, headings)          -> dict[str, str]
    read_entry(store_root, entry)             -> RecalledEntry
    focus_paths_from_text(text)               -> tuple[str, ...]
    focus_window(repo)                        -> FocusWindow
    select_featured(entries, index, scope, …) -> (ref, basis)
    recall(store_root, scope, *, ref=…, limit=…, mode=…, page=…, focus_paths=…)
                                              -> RecallReport
    render_text(report) / report_json(report) -> str / dict
    tokenize(text) / pair_strength(a, b) / score_unit(q_tokens, unit_tokens)
    entry_blocks(text)                        -> tuple[Block, ...]
    search(store_root, scope, query, *, context=…, threshold=…, …)
                                              -> SearchReport
    render_search(report) / search_json(report) -> str / dict
    main(argv)                                -> int

🔴 THE FAILURE MODE IS A CONFIDENT ZERO, and an empty surface has FOUR causes
that mean different things. `RecallReport.status` names which, in values that
share no spelling:

    "scope-absent"    the store has no `<scope>/` directory — NOTHING RECORDED
                      YET. The ordinary case in most repos (the store holds 2
                      scopes; work spans ~12 repos) and NOT an error.
    "scope-empty"     `<scope>/` exists and holds no entries. Also nothing
                      recorded yet, by a DIFFERENT mechanism — someone made the
                      directory. Kept apart on purpose: collapsing them would
                      make "the store was pruned to nothing" indistinguishable
                      from "this repo was never indexed".
    "scope-unreadable" `<scope>/` holds entry files and NOT ONE of them could be
                      indexed. A THIRD mechanism behind an empty surface, and the
                      one that is not a reading at all: there IS content here and
                      the tool cannot see it. Exits non-zero. Kept apart from
                      `scope-empty` for the same reason `scope-empty` is kept
                      apart from `scope-absent` — `/resume` reports an empty
                      scope as an ordinary non-finding, and reporting "we could
                      not read anything" that way would be a confident zero with
                      a broken store behind it.
    "ref-absent"      a `--ref` was given and resolved to no entry.
    "ref-ambiguous"   a `--ref` named more than one entry. Never picked.
    "recalled"        at least one entry surfaced.

and the two conditions that are NOT readings but broken environments, each with
its own sentinel phrase so a caller — or a mutation test — can tell WHICH fired:

    "store root not found"   StoreMissingError  (reused from subsystem_touch:
                             the same condition, so the same class, not a
                             second spelling of one error)
    "index entry unreadable" EntryUnreadableError (reused from
                             subsystem_resolver, for the same reason —
                             subsystem_touch raises it on the same files)


🔴 ONE MALFORMED ENTRY USED TO COST THE WHOLE SCOPE
---------------------------------------------------
It no longer does. Measured on a synthetic store, before this change:

    2 good entries          -> rc=0, both listed
    2 good + 1 malformed    -> rc=3, good entries still listed: 0

A single wrapped `aliases:` list — the front-matter parser is line-based, so a
list broken across two physical lines reads as an unterminated bare string —
took `/resume` step 4, `--list`, `--ref` and `--search` down together, and the
writer that produced it had no signal at all (`/handoff`'s own template prints
`aliases:` on ONE line, so the confirm-gate diff shown to the human contained
the defect while being structurally incapable of revealing it).

The reader now loads with `ON_MALFORMED_COLLECT`, so the good entries are served
and every reject is reported PER ENTRY — named, with its reason — in the same
output. 🔴 THE REPORT IS THE POINT, NOT THE DEGRADATION. Silently skipping a bad
entry would be strictly worse than the collapse it replaces: a dropped entry is
indistinguishable from an entry nobody ever wrote, which is the confident zero
this whole module exists to prevent. So the MALFORMED block renders on EVERY
status, before anything else, and the index header stops claiming completeness
the moment a reject exists.

    "malformed index entry"  the row's sentinel, one per rejected file. It is a
                             ROW and no longer a raise for the reader; the
                             WRITER's probe (`subsystem_touch.build_report`)
                             still raises it, because it gates a write into a
                             store it would otherwise be reading in part.

🔴 EVERY OUTPUT PATH CARRIES THE CAVEAT, INCLUDING THE EMPTY ONES. It is a
single property on the report (`RecallReport.caveat`) that both renderers print,
rather than a sentence per branch — a caveat spelled at N sites is wrong at N−1
of them. Index content is labelled `from index` and is RECALL: it was curated by
past sessions, was not re-derived, and was not matched against anything this
session did.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subsystem_resolver import (  # noqa: E402
    NUANCE_HEADING,
    POINTERS_HEADING,
    AmbiguousRefError,
    ON_MALFORMED_COLLECT,
    EntryUnreadableError,
    MalformedEntry,
    MalformedEntryError,
    ResolverError,
    SubsystemEntry,
    SubsystemIndex,
    UnknownScopeError,
    associate_paths,
    load_index,
    normalize_ref,
    parse_front_matter,
    parse_journal_bullets,
    resolve_ref_tiered,
)
from subsystem_resolver import extract_sections as _extract_sections  # noqa: E402
from subsystem_touch import (  # noqa: E402
    DEFAULT_STORE_ROOT,
    StoreMissingError,
    TouchError,
    scope_for_repo,
)

__all__ = [
    "RECALL_LABEL",
    "POINTERS_HEADING",
    "NUANCE_HEADING",
    "SURFACED_HEADINGS",
    "DEFAULT_ENTRY_LIMIT",
    "DEFAULT_MODE",
    "RECALL_MODES",
    "LISTING_PAGE_SIZE",
    "DEFAULT_SEARCH_THRESHOLD",
    "FUZZY_FLOOR",
    "MIN_INEXACT_LEN",
    "DEFAULT_MAX_HITS",
    "CONTEXT_BULLET",
    "HUNK_BASES",
    "FOCUS_MIN_PATHS",
    "HANDOFF_GLOBS",
    "SENSITIVITY_FAIL_SAFE",
    "KNOWN_SENSITIVITIES",
    "STATUS_PRECEDENCE",
    "UNREADABLE_STATUSES",
    "EntryUnreadableError",
    "MalformedEntry",
    "StoreMissingError",
    "FocusWindow",
    "RecalledEntry",
    "RecallReport",
    "Block",
    "Hunk",
    "SearchReport",
    "caveat_text",
    "scope_label",
    "unreadable_summary",
    "render_malformed",
    "extract_sections",
    "read_entry",
    "fold_sensitivity",
    "discarded_sensitivity",
    "sensitivity_label",
    "load_store",
    "listing_order",
    "listing_page",
    "tokenize",
    "pair_strength",
    "score_unit",
    "entry_blocks",
    "search",
    "render_search",
    "search_json",
    "focus_paths_from_text",
    "focus_window",
    "select_featured",
    "short_heading",
    "listing_line",
    "recall",
    "render_text",
    "report_json",
    "main",
]

# 🔴 THE WORD `/analyze-service` USES, REUSED VERBATIM. Its brief says nuance and
# pointers are `from index` while location and config are `re-derived live`, and
# "Never present index recall as live observation". A reader that invented its
# own label would put two spellings of one provenance claim in front of the same
# agent, in the same session, for the same store.
RECALL_LABEL = "from index"

# The two sections the recon step front-loads, RE-EXPORTED from the resolver —
# they are schema headings, so they belong with the rest of the on-disk shape and
# not in one of the two modules that read it. `## What it is` is deliberately NOT
# in `SURFACED_HEADINGS`: it is one line of durable boilerplate that a resuming
# session either already knows or can read in the file, and including it turns a
# recall block into a dump of the store. The point is recall, not a dump.
#
# The tuple itself stays HERE because it is this reader's DISPLAY CHOICE, not a
# fact about the store: `subsystem_touch` reads the same entries and wants only
# `NUANCE_HEADING`. A shared constant would have made one module's display
# decision binding on the other.
SURFACED_HEADINGS: tuple[str, ...] = (POINTERS_HEADING, NUANCE_HEADING)

# A cap, not a filter — see the module docstring on selection. Truncation is
# always PRINTED. 12 is chosen as "more than any scope currently holds per repo
# that is not the infra repo, and small enough to read at the top of a session";
# it is a display bound with an escape hatch (`--limit`), not a safety bound, so
# unlike `MAX_TRANSCRIPT_AGE_SECONDS` in the writer it is deliberately overridable.
#
# ⚠ IT IS A FULL-MODE CONCEPT ONLY. `digest` prints exactly one body by design and
# `list` prints none, so neither applies a cap and neither can truncate; `limit`
# is still carried on the report (and in the JSON) so a caller can see what a
# `--limit` run WOULD have used, but it changes nothing outside `full`. That is
# the same shape `--ref` already had: a narrowing ignores the display cap.
DEFAULT_ENTRY_LIMIT = 12

# The three display modes. `ref` is NOT one of them: a `--ref` run is a narrowing
# to a single named entry and prints that entry in full whatever the mode says,
# exactly as it did before the digest existed.
#
#   "digest"  the caveat + the FULL index (every entry, one line) + ONE body.
#             The default, and what `/resume` step 4 runs.
#   "list"    the caveat + the FULL index, and no body at all.
#   "full"    the pre-digest behaviour, byte-for-byte: up to `limit` bodies, with
#             a LOUD truncation notice. Selected by passing `--limit`.
RECALL_MODES: tuple[str, ...] = ("digest", "list", "full")
DEFAULT_MODE = "digest"

# 🔴 THE INDEX IS CAPPED, AND THE CAP IS LOUD. Every line is ~60 B, so an index
# that grows with the store forever is a per-session cost with no ceiling; 100
# lines is ~6 KB, which is still smaller than the ONE featured body it sits above
# on a large entry. Past the cap the remainder is COUNTED and `--page N` reaches
# it — nothing is dropped and nothing is silent.
#
# ⚠ It is a LISTING cap and not a selection rule: `total_in_scope` still counts
# every entry, and the truncation notice names both the count and the flag. The
# module docstring argues why the page order is newest-first by mtime rather than
# the canonical ref order that `full` mode's bodies keep.
LISTING_PAGE_SIZE = 100

# --- Search tuning. Every number here is gated by `TestSearchFixtureCorpus`, ----
# which scores a labelled query→expected-hunk set; none of them is taste.
#
# The threshold a hunk must clear. Scores are a COVERAGE MEAN over the query's
# tokens (see `score_unit`), so 0.60 means: on a two-token query both tokens must
# land (one alone scores 0.50), while on a three-token query two strong tokens
# (0.67) are enough. That asymmetry is deliberate — a longer query is a
# description, and demanding every word of a description is how a search returns
# a confident zero.
DEFAULT_SEARCH_THRESHOLD = 0.60

# Below this, `difflib`'s ratio is not evidence of a typo. 0.82 and not 0.80
# because 0.80 is exactly where a one-character SUBSTITUTION in a five-letter word
# lands — and so does the ordinary English pair `probe`/`prone`, which is not a
# typo of anything. Every typo class in the fixture set clears 0.88.
FUZZY_FLOOR = 0.82

# 🔴 ONE length rule, not three. A token shorter than this must match EXACTLY:
# no prefix rule, no substring rule, no fuzzy rule. Short tokens are where every
# inexact rule turns into noise (`pod` prefixes `podman`, `pgo`, `podinfo`), and a
# reader cannot tell a noisy hit from a real one once it is on the screen.
MIN_INEXACT_LEN = 4

# What an inexact match is WORTH, so a weak hit prints as visibly weak. Ordered,
# and each strictly below 1.0 — an exact token match must always outrank them.
PREFIX_STRENGTH = 0.92
SUBSTRING_STRENGTH = 0.85

# A display cap on HUNKS, with the same contract as `--limit`: printed when it
# bites, never silent. 10 because the point of searching is to read LESS than the
# digest, and it is measured: a one-word query for a heavily-mentioned subsystem
# returns 22 hunks on the real store, which at 20 shown costs ~9.4 KB — twice the
# 4.9 KB digest it was supposed to be cheaper than. At 10 it is ~4.6 KB, and the
# other 12 are counted on the screen with the flag that shows them.
DEFAULT_MAX_HITS = 10

# `context=CONTEXT_BULLET` means "the enclosing bullet/block", which is the
# DEFAULT and not merely one option — see `--help`. A sentinel rather than `None`
# so a report always carries a concrete value into JSON.
CONTEXT_BULLET = -1

# Why a hunk is on the screen. Two values sharing no spelling, printed per hunk:
#   "line"        this block itself cleared the threshold.
#   "entry-name"  the ENTRY's ref/aliases cleared it and no block did, so the
#                 entry's best block is shown as the worked example. Without this
#                 value a name-only hit is indistinguishable from a no-match.
HUNK_BASES: tuple[str, ...] = ("line", "entry-name")

# 🔴 ONE path is enough to feature an entry, where the WRITER needs two
# (`DEFAULT_MIN_PATHS = 2`). The asymmetry is deliberate and is about what the
# two are deciding. The writer is proposing a DURABLE journal bullet against a
# subsystem, so a single incidental path must not be enough. The reader is only
# choosing which of N already-listed entries to print first, it PRINTS the basis
# it used, and every other entry is one line above and one `--ref` away — so the
# cost of a weak signal is a slightly worse first pick, not a wrong record.
FOCUS_MIN_PATHS = 1

# Where the path window comes from, in the order `scripts/resume-state.sh`
# resolves a handoff — lowercase family first, the uppercase `*HANDOFF*.md`
# family (civitai-manager's `SESSION-HANDOFF.md`) only as a fallback. Same repo,
# same step, same doc: `/resume` has just read this file at step 2, so it is the
# session's own statement of what is being worked on, and reading it costs one
# file read with no git, no network and no subprocess.
HANDOFF_GLOBS: tuple[str, ...] = ("claudedocs/handoff-*.md", "claudedocs/*HANDOFF*.md")

# Path-shaped tokens are taken from BACKTICKED SPANS ONLY. Handoff docs are prose
# and the convention in this fleet is that a real path is code-quoted; harvesting
# bare prose would mint tokens out of ordinary English (`resume-state.sh` learned
# this the hard way with branch tokens — see its comment on the fabricated
# "referenced by handoff no longer exists" line). The failure direction here is
# benign either way: a token that names no entry is dropped by the resolver, and
# a token that names the wrong one produces a differently-featured entry whose
# basis is printed on the same screen.
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_PATH_TOKEN_STRIP = "`,;:()[]{}<>\"'*_"

# 🔴 FAIL-SAFE, from `analyze-service/SKILL.md`: "`sensitivity:` — fail-safe:
# absent means sensitive… absent or unrecognized ⇒ `client-confidential`, never
# public". Surfaced per entry because this reader's whole job is to put curated,
# client-identifying content in front of an agent that may be one paste away from
# a PUBLIC repo, and the marker is the only thing that says so.
SENSITIVITY_FAIL_SAFE = "client-confidential"
KNOWN_SENSITIVITIES: tuple[str, ...] = ("client-confidential", "personal", "public")

# Stated once so the renderers, the JSON and the tests read the SAME rule.
# Precedence, and why each outranks the next:
#   1. scope-absent     — nothing about the store's contents can change this answer.
#   2. scope-unreadable — the scope holds files and NOT ONE of them indexed. It
#                         outranks `scope-empty` and every ref outcome because it
#                         is the one case where the tool's answer is about ITSELF:
#                         a `--ref` into a scope nothing could be read from is
#                         `ref-absent` only in the sense that nothing is present
#                         to resolve against, and reporting it that way ("nothing
#                         recorded under that name yet") would be a lie about the
#                         store rather than a fact about the ref.
#   3. scope-empty      — the scope exists; there is simply nothing in it.
#   4. ref-ambiguous    — a ref was given and names more than one entry: never pick.
#   5. ref-absent       — a ref was given and names none.
#   6. recalled         — something was surfaced.
# `StoreMissingError` / `EntryUnreadableError` are NOT in this tuple: they raise.
# A status constant no code path could emit would be a declaration with nothing
# behind it. `MalformedEntryError` used to be in that list too and no longer is —
# for this reader it is a ROW (`MalformedEntry`), not a raise.
#
# The last three belong to `search`, which shares this vocabulary rather than
# minting a second one — a caller must be able to switch on ONE status field:
#   7. search-hit        at least one hunk cleared the threshold.
#   8. search-no-match   nothing did. NOT spelled `*-empty`: an empty scope and a
#                        query that matched nothing in a full scope are different
#                        facts, and two statuses that share a word get read as one.
#   9. search-unreadable the searched scopes held files and none could be indexed,
#                        so the query never ran against anything. Same argument as
#                        `scope-unreadable`, and the same reason it must not
#                        collapse into `search-no-match`: a zero from a scan that
#                        walked nothing is not a zero.
STATUS_PRECEDENCE: tuple[str, ...] = (
    "scope-absent",
    "scope-unreadable",
    "scope-empty",
    "ref-ambiguous",
    "ref-absent",
    "recalled",
    "search-hit",
    "search-no-match",
    "search-unreadable",
)

# 🔴 THE ONE PLACE THE EXIT CODE IS DECIDED, for both report types. `main()`
# switches on membership here rather than on either status by name, so a third
# "nothing could be read" outcome cannot be added and silently exit 0.
#
# WHY THESE TWO AND NOTHING ELSE — the rule is CONTENT SERVED, not "was anything
# wrong". `/resume` step 4's contract is "if it exits non-zero, print the stderr
# line verbatim, note that recall was unavailable, and continue", so a non-zero
# throws away every entry the run DID surface. A scope with 2 good entries and 1
# malformed therefore exits 0 with a loud in-band MALFORMED block: recall was
# available, and it was also honest about what it could not read. Only when
# nothing readable exists at all is "recall was unavailable" the truth.
UNREADABLE_STATUSES: tuple[str, ...] = ("scope-unreadable", "search-unreadable")


def caveat_text(scope: str) -> str:
    """What every window this module opens can and cannot see. ONE spelling.

    🔴 A MODULE-LEVEL FUNCTION rather than a property on one report, because
    there are now TWO report types and a caveat spelled twice is wrong in one of
    them. `/analyze-service` words the provenance as `from index`; this reuses
    that exact label.
    """
    return (
        f"{RECALL_LABEL} — RECALL, NOT LIVE OBSERVATION. These are notes curated by "
        f"PAST sessions in the local store under `{scope}`. Nothing here was "
        f"re-derived just now, nothing was matched against anything THIS session has "
        f"done, and an entry is exactly as fresh as the last time someone pruned it "
        f"(prune-on-resolve is manual), so a bullet may describe a gotcha already "
        f"fixed. This window CANNOT see: live state of any kind, any repo whose scope "
        f"has no directory in this store, and any work neither `/analyze-service` nor "
        f"`/handoff` ever recorded. Treat every line as a POINTER to verify, never as "
        f"a current reading. `🔴 N OPEN` on an index row means N bullets DECLARE "
        f"unfinished business — re-check each against the repo, because a remedy that "
        f"has since landed reads exactly like one that has not; the absence of that "
        f"marker means nothing was declared, NOT that nothing is open. Three further "
        f"badges say the row's own numbers cannot be trusted: `🔴 N NEAR-MISS` — N "
        f"bullets TRIED to write a marker and missed the grammar, so they declare "
        f"nothing and `N OPEN` is short by up to N; `⚠ N UNVERIFIABLE` — N `RESOLVED:` "
        f"bullets name no sha, so the closure cannot be checked; and `🔴 NO <heading>` "
        f"— that heading is absent or renamed, so `N nuance` and every openness count "
        f"on that row are 0 BY PARSE FAILURE and not by measurement, and the entry's "
        f"content is on disk but invisible to this read. Sensitivity is "
        f"marked per entry; absent means `{SENSITIVITY_FAIL_SAFE}` — never copy an "
        f"entry's content into a public repo."
    )


def scope_label(scopes: Sequence[str]) -> str:
    """`a/, b/` — how a set of scopes is named in prose. ONE spelling.

    The caveat, the search header and the unreadable summary all need it, and the
    first two used to build it inline in two slightly different ways. A label
    spelled at N sites is wrong at N−1 of them, and this one is inside a sentence
    that says what the tool could not see.
    """
    return "/, ".join(scopes) + "/" if scopes else "(no scope)"


def unreadable_summary(label: str, malformed: Sequence[MalformedEntry]) -> str:
    """The ONE sentence that says "there is content here and none of it could be read".

    🔴 IT MUST SHARE NO PHRASE WITH THE EMPTY CASE. `render_text`'s `scope-empty`
    branch opens `NOTHING RECORDED YET`; this opens `NOTHING COULD BE READ`, and
    the two are different facts with opposite next actions — carry on versus fix
    the store. `/resume` reports `scope-empty` as an ordinary non-finding, so a
    shared opening word is all it would take for a broken store to be read as an
    empty one.

    Shared by both report types because both can reach the condition and a
    sentence written twice is a sentence that will differ.
    """
    n = len(malformed)
    return (
        f"NOTHING COULD BE READ — `{label}` holds {n} entry file"
        f"{'' if n == 1 else 's'} and NOT ONE of them could be indexed. This is NOT an "
        f"empty scope and NOT 'nothing recorded yet': there is content here and the tool "
        f"cannot see it. Every reason is listed above; fix the file(s) and re-run."
    )


def render_malformed(
    malformed: Sequence[MalformedEntry], elsewhere: Sequence[MalformedEntry], label: str
) -> list[str]:
    """The MALFORMED block — per entry, named, with its reason. Empty when clean.

    🔴 ONE RENDERER, PRINTED ON EVERY STATUS BY BOTH SURFACES, IMMEDIATELY AFTER
    THE CAVEAT. Not a footer and not a branch: a reject that renders only on the
    paths somebody remembered is a reject that will be missed on the path they
    did not, and `scope-absent` — the most common status in most repos — is
    exactly where a store-wide defect would otherwise never be mentioned.

    `elsewhere` is a COUNT with its scopes named, never full rows. A reader is
    scope-scoped, so a broken entry in a scope nobody recalls today is invisible
    until someone does; naming the scopes makes it actionable without putting
    another scope's filenames (which are client-identifying) on this screen.
    """
    out: list[str] = []
    if malformed:
        n = len(malformed)
        out.append("")
        out.append(
            f"🔴 MALFORMED — {n} entry file{'' if n == 1 else 's'} in `{label}` could NOT be "
            f"indexed and {'is' if n == 1 else 'are'} therefore absent from EVERYTHING below "
            f"(the index, --ref, --search). Not dropped, not hidden: listed here, once each."
        )
        for m in malformed:
            out.append(f"  {m.line}")
        out.append(
            "  (A STORE DEFECT, not an absence of content. Front matter is parsed LINE BY "
            "LINE, so the usual cause is a value wrapped across two physical lines — an "
            "`aliases: [...]` list in particular must be on ONE line. Check a file with "
            "`subsystem_touch.py --validate <path>`, or a whole scope with `--validate`.)"
        )
    if elsewhere:
        by_scope: dict[str, int] = {}
        for m in elsewhere:
            by_scope[m.scope or "(no scope)"] = by_scope.get(m.scope or "(no scope)", 0) + 1
        where = ", ".join(f"{s} ({n})" for s, n in sorted(by_scope.items()))
        k = len(elsewhere)
        if not malformed:
            out.append("")
        out.append(
            f"  (+{k} further malformed entr{'y' if k == 1 else 'ies'} in OTHER scopes of "
            f"this store, not shown: {where}. Nothing on this screen is affected by them; "
            f"they are named so a defect in a scope nobody recalls today is still visible.)"
        )
    return out


# --- Errors --------------------------------------------------------------------
#
# ⚠ There is deliberately NO `RecallError` base any more. `EntryUnreadableError`
# was its only subclass and moved to `subsystem_resolver` when `subsystem_touch`
# needed to raise the SAME condition on the SAME files; what was left was a base
# class with nothing under it — a declaration with no code path behind it, which
# is the shape this codebase argues against everywhere else. `main()` catches
# `(TouchError, ResolverError)`, which between them cover every error this module
# can now emit.


# --- Section extraction --------------------------------------------------------


def extract_sections(text: str, headings: Sequence[str] = SURFACED_HEADINGS) -> dict[str, str]:
    """This reader's default-binding shim over the ONE section parser.

    🔴 THE PARSER ITSELF LIVES IN `subsystem_resolver`, which is where the fence
    handling, the present-but-empty tracking and their mutation kills now live
    too. It moved there — unchanged — when `subsystem_touch` needed the same
    extraction to show a `/handoff` what an entry ALREADY says before proposing
    an append. `subsystem_recall` imports `subsystem_touch`, so the writer cannot
    import the reader back without closing a cycle, and a second copy in the
    writer would be a parser free to drift from the one measured against the real
    corpus.

    All this adds is the DEFAULT: which sections *this* reader surfaces. That is
    a display decision and stays with the display.
    """
    return _extract_sections(text, headings)


def fold_sensitivity(raw: object) -> str:
    """Apply the schema's fail-safe: absent OR unrecognized ⇒ client-confidential.

    🔴 `public` is "a deliberate operator claim a recon run may never infer", so
    every path that is not an exact, known, operator-written value folds to the
    sensitive end. This is the FIRST executable spelling of that rule — the
    writer only ever emits the literal at the fail-safe value and never reads one
    back — so it is not a second implementation of an existing predicate.
    """
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in KNOWN_SENSITIVITIES:
            return v
    return SENSITIVITY_FAIL_SAFE


def discarded_sensitivity(raw: object) -> str | None:
    """The marker a file DECLARED and `fold_sensitivity` overrode, if any.

    🔴 THE FOLD IS NEVER WEAKENED — this only makes it VISIBLE. `sensitivity:`
    is the one field this module treats as safety-critical, and there is a real
    difference between the two inputs that both render as `client-confidential`:

        (absent)              nobody said anything. The ordinary case, and the
                              fail-safe default is the whole answer.
        `sensitivity: internal`  somebody WROTE a value and the tool overrode it,
                              because `internal` is not one of the schema's three.

    Silently rewriting the second reads as "the file says client-confidential",
    which it does not — and the author who typed `internal` gets no signal that
    the schema does not know the word. So an overridden declaration is printed
    beside the effective value; an absent one prints nothing.

    DERIVED from `fold_sensitivity` rather than re-testing membership, so the two
    cannot drift into disagreeing about which values are known.
    """
    if not isinstance(raw, str):
        return None
    written = raw.strip()
    if not written:
        return None
    return None if fold_sensitivity(raw) == written.lower() else written


# --- The recalled entry --------------------------------------------------------


@dataclass(frozen=True)
class RecalledEntry:
    """One entry's surfaced sections, plus what was NOT there."""

    ref: str
    filename: str
    sensitivity: str
    sections: Mapping[str, str] = field(default_factory=dict)

    declared_sensitivity: str | None = None
    """A `sensitivity:` the file wrote that the schema does not know, which
    `fold_sensitivity` overrode — see `discarded_sensitivity`. `None` when the
    marker was absent or was honoured. Printed beside the effective value so an
    override is never silent."""

    bullet_count: int = 0
    """Top-level `## Nuance / work-history` bullets, via the resolver's own
    `parse_journal_bullets`. The index line's SIZE SIGNAL: it is what a reader
    wants in order to decide whether an entry is worth a `--ref`, and it is the
    unit the store's own prune-on-resolve discipline is denominated in. A byte
    count would have been cheaper and would have measured markdown, not history.
    """

    open_count: int = 0
    """Bullets DECLARING `OPEN:` — unfinished business the writer marked.

    On the index line this is the one field that changes what a reader should DO,
    which is why it earns a place beside the size signal: an entry carrying an
    open action may be describing a remedy that has since landed, and reading it
    as current is the `forgejo` failure (proposed a fix at 15:00:18 that shipped
    at 15:02:21, served as outstanding for 22 days).

    🔴 A ZERO HERE IS NOT "NOTHING IS OPEN". The marker is opt-in and every bullet
    written before it existed carries none, so zero means "nothing was declared".
    The digest's caveat says so; this docstring exists so a future caller cannot
    quietly promote the field to a completeness claim.
    """

    near_miss_count: int = 0
    """Bullets that TRIED to write an openness marker and missed the grammar.

    🔴 THE POPULATION MOST LIKELY TO HOLD A STALE OPEN ACTION, and until this
    field existed it was byte-identical to "no marker" on the read surface: the
    writer's `RESOLVED <sha> (<repo>):` or `**OPEN:**` declared nothing, the
    badge simply did not render, and the vanishing badge LOOKS like success.

    Measured over the live store on 2026-08-19 — 53 entries, 323 top-level
    nuance bullets: **8 declare `OPEN:` and parse, 11 declare `RESOLVED <sha>:`,
    and 2 attempted a marker and missed** (one `OPEN`-shaped, one
    `RESOLVED`-shaped). The advisory that reported those 2 lived only in
    `subsystem_touch --validate`, which `/resume` never runs.

    ⚠ The proposal this closes states "2 of 10 textual `OPEN:` markers do not
    parse". The near-miss count of 2 reproduces exactly; the denominator does
    not — a raw `grep -o 'OPEN:'` over the store returns **11**, not 10, and not
    every occurrence leads a top-level bullet. The rate is quoted here as the
    two populations rather than as a ratio, because the ratio's denominator is
    the part that moved.

    Counted from `JournalBullet.openness_population`, never from the raw
    `near_miss_marker` predicate, so this surface and `--validate` can never
    disagree about which population a bullet belongs to.
    """

    unverifiable_count: int = 0
    """`RESOLVED:` bullets naming no sha — closed, but the closure is unprovable.

    ⚠ Advisory, not a defect: closing an action is the point, and a sha-less
    `RESOLVED` is a real closure that simply cannot be checked with
    `git cat-file -e`. It rides the same row as `near_miss_count` because both
    are "the marker did not fully land" and a reader who sees one wants the
    other. Measured 2026-08-19: **0** across all 53 live entries, so this badge
    does not fire on the store today — it is here because a sha-less `RESOLVED`
    is one hurried write away, not because the corpus is full of them.
    """

    mtime: float = 0.0
    """The entry file's mtime. Used ONLY as the featured-entry fallback, and
    deliberately NOT rendered or emitted in JSON — `render_text` must produce
    identical bytes for an unchanged store, and a printed timestamp would make a
    diff of two runs show movement that is not there."""

    missing_sections: tuple[str, ...] = ()
    """Requested headings this entry does not carry — REPORTED, never silent.

    An entry with pointers and no work-history is ordinary (nothing has been
    journaled against it yet). An entry with NEITHER is also ordinary — the
    writer's own `new_entry_template` ships a stub. What is not ordinary is a
    reader that shows nothing and lets a caller conclude the entry is empty when
    the extractor missed, so the two are told apart in the output.

    🔴 IT REACHES THE INDEX ROW, not only a printed body. It was computed here
    and rendered ONLY under an entry the digest printed in full — and the digest
    prints exactly ONE body out of N, so for every other entry the field existed
    and was discarded. Measured differential control (two synthetic entries
    differing in NOTHING but the nuance heading): the renamed one reported
    `0 nuance`, lost its `🔴 1 OPEN` badge entirely, and `--validate` called it
    `OK` at exit 0 — rendering byte-identical to a well-formed entry with an
    empty work-history. Heading matching is exact-string at column 0, so a
    rename, a trailing colon or an indent all land here.
    """

    @property
    def is_bare(self) -> bool:
        """True when neither surfaced section had any content."""
        return not any(self.sections.values())


def read_entry(store_root: str | Path, entry: SubsystemEntry) -> RecalledEntry:
    """Read ONE entry's surfaced sections. READ-ONLY.

    The file is located from the loader's own `scope` + `filename`, never from a
    path reconstructed out of the ref — `<slug>.<kind>.md` and `<slug>.md` are
    different files and only the loader knows which one this entry came from.
    """
    path = Path(store_root) / entry.scope / entry.filename
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise EntryUnreadableError(
            f"index entry unreadable: {path} ({type(exc).__name__}: {exc}) — the store "
            f"was not fully read, so this report is INCOMPLETE; nothing was written"
        ) from exc
    sections = extract_sections(text)
    fm = parse_front_matter(text)
    try:
        mtime = path.stat().st_mtime
    except OSError:  # pragma: no cover - the read above already succeeded
        mtime = 0.0
    bullets = parse_journal_bullets(sections.get(NUANCE_HEADING, ""))
    # 🔴 ONE PASS, ONE PREDICATE. Every count on the index row is read off
    # `openness_population` — the resolver's single source of the precedence
    # order — rather than off `is_open` / `near_miss_marker` / `resolved_by`
    # separately. A delta audit already caught two surfaces disagreeing about
    # one bullet because each decided membership for itself; the index row is
    # now the third consumer, and it branches on the same thing `--validate`
    # does, so the two can never report different populations for one file.
    populations = Counter(b.openness_population for b in bullets)
    return RecalledEntry(
        ref=entry.ref,
        filename=entry.filename,
        sensitivity=fold_sensitivity(fm.get("sensitivity")),
        declared_sensitivity=discarded_sensitivity(fm.get("sensitivity")),
        sections=sections,
        bullet_count=len(bullets),
        open_count=populations["open"],
        near_miss_count=populations["near-miss"],
        unverifiable_count=populations["unverifiable"],
        mtime=mtime,
        missing_sections=tuple(h for h in SURFACED_HEADINGS if h not in sections),
    )


# --- The focus window ----------------------------------------------------------
#
# 🔴 THIS IS THE ONLY THING IN THE MODULE THAT LOOKS OUTSIDE THE STORE, and it
# reads exactly one file. It runs no git, no `gh` and no subprocess — the writer's
# path sources do all three, and importing that cost into `/resume` step 4 is the
# thing this whole change exists to avoid.


@dataclass(frozen=True)
class FocusWindow:
    """Repo-relative paths standing in for "what is being worked on", plus where
    they came from. `source` is `None` exactly when `paths` is empty, and the
    renderer says which of the two selectors fired either way — an unattributed
    pick is the failure this dataclass exists to prevent."""

    paths: tuple[str, ...] = ()
    source: str | None = None


def _is_repo_relative(token: str) -> bool:
    """Superset of `subsystem_resolver._validate_path`'s rejections, plus shape.

    Kept a hair STRICTER than the resolver on purpose: anything this lets
    through is handed to `associate_paths`, which raises `InvalidPathError` on a
    path it considers malformed. A reader that raised because a handoff doc
    quoted a URL would be a `/resume` step that fails on ordinary prose.
    """
    if not token or "/" not in token:
        return False
    if any(c.isspace() for c in token):
        return False
    if token[0] in "/~-":  # absolute, home-relative, a CLI flag
        return False
    if "$" in token or "://" in token or "@" in token:  # a var, a URL, a host
        return False
    return ".." not in token.split("/")


def focus_paths_from_text(text: str) -> tuple[str, ...]:
    """Repo-relative path tokens quoted in `text`, deduped, in order of first use."""
    out: dict[str, None] = {}
    for span in _BACKTICKED.findall(text):
        for raw in span.split():
            token = raw.strip(_PATH_TOKEN_STRIP).rstrip(".").rstrip("/")
            if _is_repo_relative(token):
                out[token] = None
    return tuple(out)


def focus_window(repo: str | Path) -> FocusWindow:
    """The repo's newest handoff doc, as a path window. READ-ONLY, one file read.

    Resolution order mirrors `scripts/resume-state.sh` (see `HANDOFF_GLOBS`), so
    step 3 and step 4 of `/resume` cannot end up reconciling one initiative while
    recalling against another. An absent or unreadable doc is an ORDINARY
    outcome — most repos have no handoff at the moment they are resumed — and
    returns an empty window rather than raising: the caller's fallback is a real
    answer, not a degraded one.
    """
    root = Path(repo)
    doc: Path | None = None
    for pattern in HANDOFF_GLOBS:
        try:
            matches = [p for p in root.glob(pattern) if p.is_file()]
        except OSError:
            matches = []
        if matches:
            # mtime, then name: two docs written in the same second must still
            # resolve identically on every run.
            doc = max(matches, key=lambda p: (p.stat().st_mtime, p.name))
            break
    if doc is None:
        return FocusWindow()
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
        rel = doc.relative_to(root).as_posix()
    except (OSError, ValueError):
        return FocusWindow()
    paths = (rel,) + tuple(p for p in focus_paths_from_text(text) if p != rel)
    return FocusWindow(paths=paths, source=rel)


def select_featured(
    entries: Sequence[RecalledEntry],
    index: SubsystemIndex,
    scope: str,
    *,
    focus_paths: Sequence[str] = (),
    focus_source: str | None = None,
) -> tuple[str, str]:
    """Pick the ONE entry to print in full, and say why. Returns `(ref, basis)`.

    🔴 THE BASIS IS RETURNED, NOT LOGGED, so no caller can render a pick without
    also rendering how it was made. Two selectors, in this order:

      1. RESOLVED — `subsystem_resolver.associate_paths` over `focus_paths`.
         That is the WRITER's matcher, unmodified; this module adds no second
         one. Ranking is `path_count` descending, and the tie-break is the
         fallback's own mtime signal rather than a third rule.
      2. MOST-RECENT FALLBACK — the newest entry file by mtime.

    🔴 WHY MTIME AND NOT GIT RECENCY, WHICH WOULD OTHERWISE BE THE OBVIOUS
    CHOICE: mtime is trustworthy for THIS store specifically because the store
    has no remote and is never cloned, checked out or rebased — the only thing
    that ever touches those files is a session editing them in place, so mtime is
    the edit time. Git recency is NOT usable as the primary signal here: the
    store's history is mostly bulk autocommits ("4 change(s)"), so four entries
    share one commit timestamp and the intra-commit order is unrecoverable. The
    ordinary git-recency argument (a checkout rewrites mtime and git does not) is
    exactly backwards for a repo nobody ever checks out.
    """
    if len(entries) == 0:  # pragma: no cover - callers guard on `scope-empty` first
        # Spelled `len(...) == 0` and not `not entries` on purpose: the latter is
        # the mutation anchor for `recall`'s `scope-empty` guard, and a duplicate
        # anchor turns that kill into a mutation applied to whichever occurrence
        # came first (`claude/RULES.md` — the `count=1` replace hazard).
        raise ValueError("select_featured called with no entries")

    by_ref = {e.ref: e for e in entries}
    if focus_paths:
        matched = [
            m
            for m in associate_paths(
                focus_paths, index, scope, min_paths=FOCUS_MIN_PATHS
            ).matched
            if m.entry.ref in by_ref
        ]
        if matched:
            matched.sort(key=lambda m: (-m.path_count, -by_ref[m.entry.ref].mtime, m.entry.ref))
            best = matched[0]
            shown = ", ".join(best.paths[:3]) + (" …" if best.path_count > 3 else "")
            return best.entry.ref, (
                f"resolved via {focus_source or 'the supplied path window'} — "
                f"{best.path_count} of {len(focus_paths)} quoted path(s) name it: {shown}"
            )

    why = (
        "no handoff doc to read a path window from"
        if not focus_paths
        else f"nothing quoted in {focus_source} resolved to an entry"
    )
    newest = max(entries, key=lambda e: (e.mtime, e.ref))
    return newest.ref, f"most-recent fallback — newest entry file in `{scope}/` ({why})"


# --- The report ----------------------------------------------------------------


@dataclass(frozen=True)
class RecallReport:
    """One deterministic answer to "what does the index already record here?"."""

    status: str
    scope: str
    store_root: str
    entries: tuple[RecalledEntry, ...] = ()
    total_in_scope: int = 0
    """Entries the scope holds, BEFORE `--limit`. The truncation discriminator."""

    limit: int = DEFAULT_ENTRY_LIMIT
    ref: str | None = None
    candidates: tuple[str, ...] = ()
    """Filenames an ambiguous `--ref` named. The resolver never picks; nor does this."""

    known_scopes: tuple[str, ...] = ()
    """Every scope the store holds — printed on `scope-absent` so a typo'd or
    unexpectedly-normalized scope is visible instead of reading as "nothing
    recorded yet"."""

    malformed: tuple[MalformedEntry, ...] = ()
    """Entry files in THIS scope that could not be indexed. Rendered on every
    status, before anything else — see `render_malformed`. These are NOT in
    `entries`, `listing` or `total_in_scope`: they never became entries, and
    counting them as if they had would be the silent short index the collecting
    loader exists to avoid."""

    malformed_elsewhere: tuple[MalformedEntry, ...] = ()
    """The same, in every OTHER scope of the store. A count with its scopes named,
    so a defect outside the scope being recalled is still visible."""

    @property
    def malformed_total(self) -> int:
        return len(self.malformed) + len(self.malformed_elsewhere)

    mode: str = DEFAULT_MODE
    """Which display mode produced this report — see `RECALL_MODES`."""

    listing: tuple[RecalledEntry, ...] = ()
    """ONE PAGE of the index, one line per entry, newest-first by file mtime.

    Up to `LISTING_PAGE_SIZE` lines. It is a PAGE and not a filter: `listing_total`
    counts every entry in the scope, `listing_pages` says how many pages that is,
    and the renderer prints the remainder with the `--page` that reaches it — so
    the set of things on record stays complete even when only one of them is
    printed in full. Empty in `full` mode, which prints bodies and a truncation
    notice instead."""

    listing_total: int = 0
    """Entries the index covers, BEFORE the page cap. The pagination discriminator:
    `listing_total > len(listing)` is the ONLY thing that makes the notice fire."""

    listing_page: int = 1
    listing_pages: int = 1
    """1-based page, and how many pages `listing_total` makes at
    `LISTING_PAGE_SIZE`. `listing_pages` is at least 1 even for an empty index, so
    "page 1 of 1" never reads as "page 1 of 0"."""

    @property
    def page_is_past_the_end(self) -> bool:
        """🔴 THE ONE PLACE THIS QUESTION IS ASKED. Three renderer branches need
        it — the index header, the page notice and `--list`'s completeness line —
        and the first shipped version answered it separately in each, which is how
        a header printed `entries 801–800 of 150 (page 9 of 2)` while the notice
        directly under it was correct."""
        return self.listing_page > self.listing_pages

    @property
    def listing_before_page(self) -> int:
        """Index rows on EARLIER pages. 0 on a page past the end: nothing was
        listed there, so "before" describes no position."""
        if self.page_is_past_the_end:
            return 0
        return (self.listing_page - 1) * LISTING_PAGE_SIZE

    @property
    def listing_after_page(self) -> int:
        """Index rows on LATER pages — what a truncation notice is about.

        🔴 THE BUG THIS PROPERTY EXISTS FOR. The notice originally fired on
        `listing_total > len(listing)`, which is "this page does not hold the
        whole index" — TRUE on the LAST page too. On page 2 of 2 it therefore
        announced page 1's 100 entries as still unseen and routed the reader to a
        `--page 3` that does not exist: a false truncation notice contradicting
        the correct header on the line above it. What a notice must count is what
        comes AFTER this page, which is 0 on the last one.
        """
        if self.page_is_past_the_end:
            return 0
        return max(0, self.listing_total - self.listing_before_page - len(self.listing))

    featured_basis: str | None = None
    """Which selector chose `entries[0]`, in words — `select_featured`'s second
    return value. Set in `digest` mode only. Never None there: a featured entry
    with no stated basis is the implicit pick this module refuses to make."""

    @property
    def omitted(self) -> int:
        """Entries in the scope whose BODY was not printed.

        A `--ref` run reports 0: one of four is a NARROWING, not a truncation,
        and calling it an omission trains the reader to ignore the real one. The
        digest reports a real count — 24 of 25 bodies genuinely were not printed
        — and the renderer says so in the digest's own words, because there
        every one of those 24 is still LISTED and one `--ref` away, which is not
        what `--limit` truncation means.
        """
        return max(0, self.total_in_scope - len(self.entries)) if self.ref is None else 0

    @property
    def caveat(self) -> str:
        """What this window can and cannot see — `caveat_text`, and nothing local.

        🔴 Delegated rather than written into each renderer: the two renderers,
        every status branch AND the search report must make the SAME claim, and a
        sentence duplicated per branch is one edit away from a branch that
        promises more than the store can support.
        """
        return caveat_text(f"{self.scope}/")


def load_store(store_root: str | Path, *, verb: str) -> tuple[Path, SubsystemIndex]:
    """Resolve the store root and load its index, or raise with a sentinel.

    🔴 ONE PLACE, because `recall` and `search` open the SAME store for the same
    reasons and a predicate open-coded at two sites is wrong at one of them.
    `verb` is the only thing that differs: a store-missing message has to say what
    did NOT happen ("nothing was recalled" / "nothing was searched") or it reads
    as the ordinary nothing-recorded-yet case, which is the confident zero this
    module exists to prevent.

    🔴 IT LOADS WITH `ON_MALFORMED_COLLECT`, AND THAT IS THIS MODULE'S POLICY
    DECISION, NOT THE LOADER'S. A malformed entry no longer raises here: it comes
    back on `index.malformed` and every caller of this function is obliged to
    render it (`render_malformed`). The measurement that forced the change is in
    the module docstring — fail-closed cost the whole scope, 2 good entries and 1
    bad one served ZERO. The WRITER (`subsystem_touch.build_report`) deliberately
    keeps the raise: it gates a write, and writing into a store you have read
    only part of is the one case where aborting is cheaper than degrading.

    ⚠ `MalformedEntryError` can therefore no longer escape this function from the
    validator, so nothing catches it — but an `OSError` still fails closed below,
    because "could not interpret this file" and "could not READ the store" are
    different facts and only the first has an honest degraded form.
    """
    store = Path(store_root)
    if not store.is_dir():
        raise StoreMissingError(
            f"store root not found: {store} — expected the `/analyze-service` index "
            f"store. Nothing was {verb}; this is NOT 'nothing recorded yet'"
        )
    try:
        return store, load_index(store, on_malformed=ON_MALFORMED_COLLECT)
    except OSError as exc:
        raise EntryUnreadableError(
            f"index entry unreadable: under {store} ({type(exc).__name__}: {exc}) — the "
            f"store was not fully read, so this report would be INCOMPLETE"
        ) from exc


def recall(
    store_root: str | Path,
    scope: str,
    *,
    ref: str | None = None,
    limit: int = DEFAULT_ENTRY_LIMIT,
    mode: str = DEFAULT_MODE,
    page: int = 1,
    focus_paths: Sequence[str] = (),
    focus_source: str | None = None,
) -> RecallReport:
    """Surface the index's `## Pointers` + `## Nuance / work-history` for a scope.

    READ-ONLY. No clock, no network, no git, no prompt — `/resume`'s job is to
    re-enter work, and a recall step that interrogated the network or blocked on
    a confirm would make the thing it is supposed to accelerate slower.

    `focus_paths` is INJECTED, exactly as `associate_paths` injects its index:
    this function performs no repo I/O of its own, so a test can pin the
    selection rule without a fixture repo and `main()` owns the one call to
    `focus_window`. `focus_source` is only ever quoted back in the printed basis.

    Guard order — each reachable by an input no earlier guard rejects:
      1. `limit` sanity      → ValueError
      2. `page` sanity       → ValueError  (a valid limit still reaches it)
      3. `mode` known        → ValueError  (a valid limit AND page still reach it)
      4. store root exists   → StoreMissingError
      5. store is readable   → EntryUnreadableError
         (a MALFORMED entry does not raise: it is collected and reported. Only an
         `OSError` — the store was not fully read — still fails closed here.)
      6. scope known         → status `scope-absent`, NOT an error
      7. anything readable   → status `scope-unreadable` when the scope holds
         entry files and none of them indexed. Checked BEFORE `--ref`, because a
         ref cannot be honestly called absent from a scope nothing was read from.

    🔴 AN ABSENT SCOPE IS A STATUS, NOT AN EXCEPTION, and that is the single most
    load-bearing decision here. The store holds 2 scopes while work spans ~12
    repos, so "this repo has nothing recorded yet" is the ORDINARY outcome, not a
    failure. Raising would make the common case the exceptional one, every caller
    would wrap the call, and a wrapped call is how the genuine errors above get
    swallowed too — the same argument `associate_paths` makes for an empty path
    set.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be an int >= 1, got {limit!r}")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError(f"page must be an int >= 1, got {page!r}")
    if mode not in RECALL_MODES:
        raise ValueError(f"mode must be one of {RECALL_MODES}, got {mode!r}")

    store, index = load_store(store_root, verb="recalled")
    # Computed ONCE, before any status branch, and passed to every one of them —
    # the block renders on all of them (`render_malformed`), so deriving it per
    # branch would be the same predicate at six sites, wrong at five.
    bad = index.malformed_in(scope)
    bad_elsewhere = index.malformed_outside((scope,))

    try:
        entries = index.entries(scope)
    except UnknownScopeError:
        return RecallReport(
            status="scope-absent",
            scope=normalize_ref(scope),
            store_root=str(store),
            limit=limit,
            mode=mode,
            ref=ref,
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )

    # 🔴 BEFORE `--ref`, AND BEFORE `scope-empty`. A scope whose every file was
    # rejected reaches both of those branches looking identical to a scope that
    # holds nothing — `ref-absent` would say "nothing recorded under that name
    # yet" and `scope-empty` would say "NOTHING RECORDED YET", and both would be
    # false about a directory full of content. This is the discriminator, and it
    # is the ONLY thing standing between a broken store and a status `/resume`
    # reports as an ordinary non-finding.
    if not entries and bad:
        return RecallReport(
            status="scope-unreadable",
            scope=normalize_ref(scope),
            store_root=str(store),
            total_in_scope=0,
            limit=limit,
            mode=mode,
            ref=normalize_ref(ref) if ref is not None else None,
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )

    if ref is not None:
        try:
            entry, _tier = resolve_ref_tiered(ref, index, scope)
        except AmbiguousRefError as exc:
            return RecallReport(
                status="ref-ambiguous",
                scope=normalize_ref(scope),
                store_root=str(store),
                total_in_scope=len(entries),
                limit=limit,
                mode=mode,
                ref=exc.ref,
                candidates=exc.candidates,
                known_scopes=index.scopes,
                malformed=bad,
                malformed_elsewhere=bad_elsewhere,
            )
        if entry is None:
            return RecallReport(
                status="ref-absent",
                scope=normalize_ref(scope),
                store_root=str(store),
                total_in_scope=len(entries),
                limit=limit,
                mode=mode,
                ref=normalize_ref(ref),
                known_scopes=index.scopes,
                malformed=bad,
                malformed_elsewhere=bad_elsewhere,
            )
        # A `--ref` run is a NARROWING and prints its one entry in full whatever
        # `mode` says — no index, no featured basis, byte-identical to what it
        # printed before the digest existed.
        return RecallReport(
            status="recalled",
            scope=normalize_ref(scope),
            store_root=str(store),
            entries=(read_entry(store, entry),),
            total_in_scope=len(entries),
            limit=limit,
            mode=mode,
            ref=normalize_ref(ref),
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )

    if not entries:
        return RecallReport(
            status="scope-empty",
            scope=normalize_ref(scope),
            store_root=str(store),
            total_in_scope=0,
            limit=limit,
            mode=mode,
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )

    # THE ONE ordering site: canonical ref ascending, so two runs over an
    # unchanged store produce identical bytes and a diff of them shows only real
    # movement.
    ordered = sorted(entries, key=lambda e: e.ref)

    if mode == "full":
        return RecallReport(
            status="recalled",
            scope=normalize_ref(scope),
            store_root=str(store),
            entries=tuple(read_entry(store, e) for e in ordered[:limit]),
            total_in_scope=len(ordered),
            limit=limit,
            mode=mode,
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )

    # `digest` and `list` both read EVERY entry — the index line carries a bullet
    # count, which only the file can answer, and the featured pick needs every
    # entry's mtime. Reading 26 small files costs milliseconds; it is the OUTPUT
    # that had to shrink, not the input. The PAGE cap is applied to the rendered
    # listing only, never to `read`.
    read = tuple(read_entry(store, e) for e in ordered)
    page_slice, pages = listing_page(read, page)
    if mode == "list":
        return RecallReport(
            status="recalled",
            scope=normalize_ref(scope),
            store_root=str(store),
            total_in_scope=len(ordered),
            limit=limit,
            mode=mode,
            listing=page_slice,
            listing_total=len(read),
            listing_page=page,
            listing_pages=pages,
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )

    featured_ref, basis = select_featured(
        read, index, scope, focus_paths=focus_paths, focus_source=focus_source
    )
    return RecallReport(
        status="recalled",
        scope=normalize_ref(scope),
        store_root=str(store),
        entries=tuple(e for e in read if e.ref == featured_ref),
        total_in_scope=len(ordered),
        limit=limit,
        mode=mode,
        listing=page_slice,
        listing_total=len(read),
        listing_page=page,
        listing_pages=pages,
        featured_basis=basis,
        known_scopes=index.scopes,
        malformed=bad,
        malformed_elsewhere=bad_elsewhere,
    )


# --- The index page ------------------------------------------------------------


def listing_order(entries: Sequence[RecalledEntry]) -> tuple[RecalledEntry, ...]:
    """The index's OWN order: newest-first by file mtime, tie-broken by ref.

    🔴 A SECOND ORDERING SITE, on purpose, and the only one that is not
    ref-ascending. It exists because the index is now CAPPED (`LISTING_PAGE_SIZE`)
    and a cap makes the order load-bearing: cutting an alphabetical list at 100
    hides entries by an accident of their names, while cutting a recency list
    hides the stale ones — the only cut that means anything to a session
    re-entering work. `full` mode's bodies keep the canonical ref order, because
    that output is a requested dump whose reader is scanning for a name.

    Determinism is unaffected: the store is never cloned, checked out or rebased
    (see `select_featured` on why mtime is the edit time here), and the ref
    tie-break settles two entries written in the same nanosecond.
    """
    return tuple(sorted(entries, key=lambda e: (-e.mtime, e.ref)))


def listing_page(
    entries: Sequence[RecalledEntry], page: int
) -> tuple[tuple[RecalledEntry, ...], int]:
    """One page of the ordered index, and how many pages there are in total.

    A page PAST the end returns an empty slice rather than clamping to the last
    one: clamping would answer a question the caller did not ask and print a full
    page under a heading saying `--page 9`, which is the silent-wrong shape this
    module refuses everywhere else. The renderer says the page is past the end and
    names the valid range.
    """
    ordered = listing_order(entries)
    pages = max(1, -(-len(ordered) // LISTING_PAGE_SIZE))  # ceil, no float
    start = (page - 1) * LISTING_PAGE_SIZE
    return ordered[start : start + LISTING_PAGE_SIZE], pages


# --- Rendering -----------------------------------------------------------------


def sensitivity_label(effective: str, declared: str | None) -> str:
    """`<effective>` — or `<effective> (declared: <x>)` when a marker was overridden.

    ONE spelling, taking the two VALUES rather than a report type, because THREE
    surfaces print it — the index row, the `--ref`/featured entry header and the
    search hunk — and only two of them hold a `RecalledEntry`.
    """
    if declared is None:
        return effective
    return f"{effective} (declared: {declared})"


def short_heading(heading: str) -> str:
    """`## Nuance / work-history` → `Nuance / work-history`. ONE spelling.

    The index row names a heading in ~60 B of budget, so it drops the ATX
    marker the printed body keeps. It still names the heading in FULL — `NO
    Pointers` and `NO Nuance / work-history` are different facts with different
    next actions, and a badge that said only `NO SECTION` would make them one.
    """
    return heading.lstrip("#").strip()


def listing_line(entry: RecalledEntry, width: int) -> str:
    """ONE index line: `  <ref>   N nuance  <sensitivity>[  <badges>]`. ~60 B.

    The ref is what `--ref` takes, the count is the size signal that says whether
    a `--ref` is worth spending, and the sensitivity has to travel WITH the entry
    it describes — a sensitivity stated once at the top of a block is a
    sensitivity that gets copied away from.

    🔴 THIS SAID "THREE FIELDS AND NO FOURTH", AND THE FOURTH IS ADDED
    DELIBERATELY. The bar that wording set is the right one, so it is restated
    rather than dropped: a field earns this line only if it changes what the
    reader DOES, because the index is the one thing printed for every entry and
    the cost is paid on every read. `open` clears that bar where size does not —
    an entry with unfinished business may be describing a remedy that has since
    landed, and the reader cannot tell from the body. It is also **conditional**:
    entries with nothing declared open render byte-identical to before, so the
    common case pays nothing. Nothing else has cleared this bar; keep it that way.

    ⚠ THE COUNT IS `## Nuance / work-history` BULLETS ONLY, and the word says so
    rather than leaving it to be assumed. It was `N bullets`, which reads as
    ENTRY SIZE to anyone scanning the index — an entry with 5 pointers and 7
    nuance bullets showed `7 bullets` and has 12. Naming the section is free
    (`nuance` is one character shorter than `bullets`) and the count itself is
    deliberately not entry size: pointers are durable and do not grow, while
    work-history is what the store's prune-on-resolve discipline is denominated
    in, so it is the number that predicts whether a `--ref` is worth spending.

    🔴 THREE MORE BADGES, AND THE BAR ABOVE IS WHY THEY CLEAR IT — every one
    reports a state in which the NUMBERS TO THEIR LEFT ARE NOT MEASUREMENTS:

      `🔴 N NEAR-MISS`  N bullets tried to write a marker and missed the
                        grammar. They declare nothing, so `N OPEN` is short by
                        up to N and the reader cannot tell. Measured
                        2026-08-19: 2 such bullets across 53 entries, against
                        8 that declare `OPEN:` and parse.
      `⚠ N UNVERIFIABLE` N `RESOLVED:` bullets name no sha. Advisory — closing
                        is the point — but the closure cannot be checked.
      `🔴 NO <heading>` the heading is absent or renamed, so the section was
                        never parsed: `0 nuance` and a missing `OPEN` badge on
                        that row mean PARSE FAILURE, not an empty entry. This
                        one used to render only under a printed BODY, and the
                        digest prints one body out of N — so on every other row
                        it was computed and thrown away.

    All three are CONDITIONAL, like `OPEN` and for the same reason: measured
    over the live store on 2026-08-19, 1 entry of 53 would carry a near-miss
    badge and 0 would carry either of the other two, so the other 52 rows stay
    byte-identical to what they render today.

    ⚠ ORDER IS `--validate`'S ORDER (declared → near-miss → unverifiable), so a
    reader who has seen one surface can read the other without re-learning it.
    The `NO <heading>` badge sits last because it is not a bullet population at
    all — it says the parser never reached the bullets.

    ⚠ THE BADGES ARE TEXT-ROW ONLY, deliberately, following `open_count`: the
    JSON `listing` rows carry `ref`/`file`/`sensitivity`/`nuance_bullets` and no
    openness field of any kind. A JSON consumer that wants the populations
    reads `entries[].missing_sections` or runs `--validate`; splitting the row's
    vocabulary across two payloads is how the two start to disagree.
    """
    base = f"  {entry.ref.ljust(width)}  {entry.bullet_count:>3} nuance   {sensitivity_label(entry.sensitivity, entry.declared_sensitivity)}"
    badges: list[str] = []
    if entry.open_count:
        badges.append(f"🔴 {entry.open_count} OPEN")
    if entry.near_miss_count:
        badges.append(f"🔴 {entry.near_miss_count} NEAR-MISS")
    if entry.unverifiable_count:
        badges.append(f"⚠ {entry.unverifiable_count} UNVERIFIABLE")
    if entry.missing_sections:
        badges.append(
            "🔴 NO " + ", ".join(short_heading(h) for h in entry.missing_sections)
        )
    if not badges:
        return base
    return base + "   " + "   ".join(badges)


def _render_listing(report: RecallReport) -> list[str]:
    """The index block, its ORDER stated, and its remainder counted.

    🔴 The single-page wording is unchanged from the pre-pagination default
    ("ALL N … none omitted"), because on every scope that fits in one page the
    claim is still exactly true and weakening it would train the reader to skim
    past the page notice on the day it is not. The multi-page wording shares no
    phrase with it.

    🔴 …AND IT IS WITHDRAWN THE MOMENT A REJECT EXISTS. "ALL N entries, none
    omitted" is a COMPLETENESS CLAIM, and it is simply false about a scope whose
    third file could not be indexed — the index really does omit it. The claim is
    a comment about the store like any other, and `claude/RULES.md` is explicit
    that a comment the implementation contradicts is a defect: so the clean
    branch keeps its exact historical bytes (nothing pinned to it moves) and a
    scope with rejects gets its OWN wording, sharing no phrase with either the
    clean or the paginated one.
    """
    width = max((len(e.ref) for e in report.listing), default=0)
    order = "newest-first by file mtime"
    shown = len(report.listing)
    n_bad = len(report.malformed)

    if report.page_is_past_the_end:
        # 🔴 NO ARITHMETIC ON A PAGE THAT DOES NOT EXIST. The first shipped
        # version computed the range unconditionally and printed
        # `entries 801–800 of 150 … (page 9 of 2)` — an inverted range and an
        # impossible page-of-page — directly above a correct guidance line. A
        # header is a claim like any other; it does not get to be wrong because
        # something below it is right.
        head = (
            f"INDEX ({RECALL_LABEL}) — no entries: page {report.listing_page} is past the "
            f"end of `{report.scope}/`, which holds {report.listing_total} in "
            f"{report.listing_pages} page{'' if report.listing_pages == 1 else 's'} "
            f"({order}):"
        )
    elif report.listing_pages <= 1 and n_bad:
        head = (
            f"INDEX ({RECALL_LABEL}) — the {shown} READABLE entr"
            f"{'y' if shown == 1 else 'ies'} in `{report.scope}/` ({order}). NOT complete: "
            f"{n_bad} further file{'' if n_bad == 1 else 's'} in this scope could not be "
            f"indexed and {'is' if n_bad == 1 else 'are'} named above, never here:"
        )
    elif report.listing_pages <= 1:
        head = (
            f"INDEX ({RECALL_LABEL}) — ALL {shown} entr"
            f"{'y' if shown == 1 else 'ies'} in `{report.scope}/`, "
            f"none omitted ({order}):"
        )
    else:
        first = report.listing_before_page + 1
        # The paginated header already declines to claim completeness, so a
        # reject only needs the count corrected: `listing_total` is READABLE
        # entries, and without this the reader would take it for the file count.
        rejects = (
            f", plus {n_bad} that could NOT be indexed (named above)" if n_bad else ""
        )
        head = (
            f"INDEX ({RECALL_LABEL}) — entries {first}–{first + shown - 1} "
            f"of {report.listing_total} in `{report.scope}/`{rejects}, {order} "
            f"(page {report.listing_page} of {report.listing_pages}):"
        )
    out = ["", head, *(listing_line(e, width) for e in report.listing)]

    if report.page_is_past_the_end:
        # Its own branch and its own words: a page past the end lists nothing,
        # and "nothing on this page" must not be readable as "nothing on record".
        out.append(
            f"  (PAGE {report.listing_page} IS PAST THE END — `{report.scope}/` holds "
            f"{report.listing_total} entr"
            f"{'y' if report.listing_total == 1 else 'ies'} across "
            f"{report.listing_pages} page"
            f"{'' if report.listing_pages == 1 else 's'} of {LISTING_PAGE_SIZE}. Nothing "
            f"was listed here and nothing is missing from the store; re-run with "
            f"`--page 1` … `--page {report.listing_pages}`.)"
        )
    elif report.listing_after_page:
        # 🔴 GATED ON WHAT COMES AFTER THIS PAGE, never on "this page is not the
        # whole index" — see `RecallReport.listing_after_page`. On the LAST page
        # this is 0 and nothing prints, because there is nothing left to announce
        # and a notice pointing at a page that does not exist is worse than none.
        remaining = report.listing_after_page
        nxt = report.listing_page + 1
        out.append(
            f"  (… {remaining} more entr{'y' if remaining == 1 else 'ies'} NOT LISTED on "
            f"this page — the index is capped at {LISTING_PAGE_SIZE} lines per page. "
            f"Nothing is hidden and nothing was filtered: `--page {nxt}`"
            f"{f' … `--page {report.listing_pages}`' if nxt < report.listing_pages else ''} "
            f"lists the rest, oldest last.)"
        )
    return out


def render_text(report: RecallReport) -> str:
    """The agent-facing recall block. Deterministic: same report in, same bytes out."""
    out: list[str] = [
        f"subsystem-recall: status={report.status} scope={report.scope}",
        f"  store: {report.store_root}",
        f"  caveat: {report.caveat}",
    ]

    # 🔴 BEFORE EVERY STATUS BRANCH, INCLUDING THE ONES THAT RETURN IMMEDIATELY.
    # A reject reported only on the paths somebody remembered is a reject that
    # will be missed on the path they did not — and `scope-absent`, the most
    # common status in most repos, is precisely where a store-wide defect would
    # otherwise never be mentioned at all.
    out.extend(render_malformed(report.malformed, report.malformed_elsewhere, f"{report.scope}/"))

    if report.status == "scope-unreadable":
        out.append("")
        out.append(unreadable_summary(f"{report.scope}/", report.malformed))
        return "\n".join(out)

    if report.status == "scope-absent":
        out.append("")
        out.append(
            f"NOTHING RECORDED YET — the store has no `{report.scope}/` directory. This is "
            f"the ordinary case in most repos (the store is young and its scopes are "
            f"few), NOT an error and NOT an absence of drift: nothing was checked, so "
            f"nothing can be concluded from it. Carry on with the resume and say plainly "
            f"that the index had nothing for this repo."
        )
        out.append(f"  scopes the store does hold: {', '.join(report.known_scopes) or '(none)'}")
        return "\n".join(out)

    if report.status == "scope-empty":
        out.append("")
        out.append(
            f"NOTHING RECORDED YET — `{report.scope}/` exists but holds no entries. Same "
            f"conclusion as an absent scope and a DIFFERENT mechanism: the directory was "
            f"made and never filled, or was pruned to nothing. Not an error."
        )
        return "\n".join(out)

    if report.status == "ref-ambiguous":
        out.append("")
        out.append(
            f"AMBIGUOUS REF `{report.ref}` — it names more than one entry, so nothing was "
            f"surfaced. The resolver never picks; neither does this. Candidates: "
            f"{', '.join(report.candidates)}. Re-run naming one of them."
        )
        return "\n".join(out)

    if report.status == "ref-absent":
        out.append("")
        # 🔴 THE ABSENCE IS QUALIFIED WHEN THE SCOPE HAS REJECTS. "Nothing
        # recorded under that name yet" is a claim about the STORE, and it is
        # false if the entry exists in a file the loader refused. The malformed
        # rows are already above; this sentence is what stops the reader
        # concluding from them in the wrong direction.
        extra = (
            f" ⚠ BUT {len(report.malformed)} entry file"
            f"{'' if len(report.malformed) == 1 else 's'} in this scope could not be indexed "
            f"(listed above) — this ref may name one of them, in which case it IS recorded "
            f"and merely invisible. Check those before concluding the name is new; re-run "
            f"without `--ref` to see what IS readable."
            if report.malformed
            else " Nothing recorded under that name yet; re-run without `--ref` to see what "
            "IS recorded."
        )
        out.append(
            f"NO SUCH ENTRY — `{report.ref}` resolves to nothing in `{report.scope}/`, "
            f"which holds {report.total_in_scope} entr"
            f"{'y' if report.total_in_scope == 1 else 'ies'}.{extra}"
        )
        return "\n".join(out)

    # `listing_total`, not `listing`: a `--page` past the end has an EMPTY slice
    # and still has an index block to render — the notice saying so is the whole
    # point. Gating on the slice would make that page render as `full` mode.
    if report.listing_total:
        out.extend(_render_listing(report))

    if not report.entries:
        # `list` mode. LOUD about what it did not do: an index with no bodies is
        # not an empty scope, and the two must not read the same.
        #
        # 🔴 IT DESCRIBES THE PAGE, NOT THE SCOPE. This line originally asserted
        # "the N entries above are the complete index", with N the SCOPE total —
        # false on every paginated page (100 were above, not 150) and flatly
        # self-contradictory past the end (zero were). Same class as a false
        # truncation notice: it claims a completeness the page does not have.
        # `total_in_scope` still appears, because "this is not an empty scope" is
        # the other half of the sentence and has to stay true.
        shown = len(report.listing)
        if report.page_is_past_the_end:
            what = (
                f"NO entries were listed — see the page notice above. `{report.scope}/` "
                f"holds {report.total_in_scope}"
            )
        elif report.listing_pages > 1:
            what = (
                f"The {shown} entr{'y' if shown == 1 else 'ies'} above "
                f"{'is' if shown == 1 else 'are'} page {report.listing_page} of "
                f"{report.listing_pages} of the index for `{report.scope}/`, which holds "
                f"{report.total_in_scope} in all"
            )
        elif report.malformed:
            # 🔴 The same withdrawn completeness claim as the header above, in the
            # other place it is spelled. "the complete index" is false when a file
            # in the scope could not be indexed, and this line is the one a reader
            # quotes when they say "the store has N of them".
            n_bad = len(report.malformed)
            what = (
                f"The {report.total_in_scope} entr"
                f"{'y above is' if report.total_in_scope == 1 else 'ies above are'} every "
                f"READABLE entry in `{report.scope}/` — NOT the complete index: {n_bad} "
                f"further file{'' if n_bad == 1 else 's'} could not be indexed"
            )
        else:
            what = (
                f"The {report.total_in_scope} entr"
                f"{'y above is' if report.total_in_scope == 1 else 'ies above are'} the "
                f"complete index for `{report.scope}/`"
            )
        out.append("")
        out.append(
            f"NO ENTRY BODIES WERE PRINTED (--list). {what} — this is not an empty scope. "
            f"Run `--ref <name>` for one entry's `{POINTERS_HEADING}` + `{NUANCE_HEADING}`."
        )
        return "\n".join(out)

    n = len(report.entries)
    out.append("")
    if report.featured_basis is not None:
        # 🔴 THE BASIS, NEVER THE PICK ALONE. An entry featured without saying
        # which selector chose it is indistinguishable from an entry the tool
        # thinks is IMPORTANT, and that is a claim the store cannot support.
        out.append(
            f"FEATURED IN FULL ({RECALL_LABEL}) — 1 of {report.total_in_scope}, "
            f"{report.featured_basis}:"
        )
    else:
        out.append(
            f"RECALL ({RECALL_LABEL}) — {n} of {report.total_in_scope} entr"
            f"{'y' if report.total_in_scope == 1 else 'ies'} in `{report.scope}/`:"
        )
    for e in report.entries:
        out.append("")
        out.append(
            f"  ### {e.ref}  ({report.scope}/{e.filename}, "
            f"sensitivity={sensitivity_label(e.sensitivity, e.declared_sensitivity)})"
        )
        for heading in SURFACED_HEADINGS:
            body = e.sections.get(heading)
            if body:
                out.append(f"    {heading}")
                for line in body.splitlines():
                    out.append(f"      {line}")
        if e.is_bare:
            # 🔴 Said, not left blank. An entry that exists with nothing under
            # either surfaced heading is a real state (the writer's own template
            # ships a stub), and printing nothing for it is indistinguishable
            # from an extractor that failed to find the sections.
            out.append(
                f"    (no `{POINTERS_HEADING}` or `{NUANCE_HEADING}` content — the entry "
                f"exists but has not been filled in)"
            )
        elif e.missing_sections:
            out.append(f"    (no {', '.join(f'`{h}`' for h in e.missing_sections)} section)")

    if report.omitted and report.listing_total:
        # The digest's own words. It is NOT the `--limit` truncation below: every
        # one of these entries IS listed above and is one `--ref` away, so
        # borrowing the truncation wording would train the reader to read a
        # complete index as a lossy one — and then to ignore the real notice.
        out.append("")
        out.append(
            f"… {report.omitted} further entr{'y' if report.omitted == 1 else 'ies'} in "
            f"`{report.scope}/` LISTED ABOVE but NOT shown in full. Nothing is hidden: this "
            f"is the default digest, not a judgement about relevance. `--ref <name>` prints "
            f"any one of them in full; `--limit {report.total_in_scope}` prints them all."
        )
    elif report.omitted:
        out.append("")
        out.append(
            f"… {report.omitted} more entr{'y' if report.omitted == 1 else 'ies'} in "
            f"`{report.scope}/` NOT shown (--limit {report.limit}). This is a display cap, "
            f"not a judgement about relevance — raise it to see the rest."
        )
    return "\n".join(out)


def _malformed_json(
    malformed: Sequence[MalformedEntry], elsewhere: Sequence[MalformedEntry]
) -> dict:
    """The reject rows + counts, shaped ONCE for both report types.

    Both JSON payloads carry the same three keys with the same meanings; writing
    the dict twice is how one of them ends up with a count and no rows.
    """
    return {
        "malformed": [
            {"scope": m.scope, "file": m.filename, "reason": m.reason, "line": m.line}
            for m in malformed
        ],
        "malformed_elsewhere": sorted({m.scope for m in elsewhere}),
        "malformed_elsewhere_count": len(elsewhere),
    }


def report_json(report: RecallReport) -> dict:
    return {
        "status": report.status,
        "scope": report.scope,
        "store_root": report.store_root,
        "label": RECALL_LABEL,
        "caveat": report.caveat,
        "ref": report.ref,
        "candidates": list(report.candidates),
        "limit": report.limit,
        "mode": report.mode,
        "total_in_scope": report.total_in_scope,
        "omitted": report.omitted,
        "known_scopes": list(report.known_scopes),
        "featured_basis": report.featured_basis,
        # 🔴 The rejects travel in the JSON too, as ROWS and not just a count. A
        # consumer reading `listing` has no other way to tell a complete index
        # from one three files short, and a count alone cannot be acted on.
        **_malformed_json(report.malformed, report.malformed_elsewhere),
        # The pagination facts travel WITH the page, or a JSON consumer reading
        # `listing` has no way to tell a complete index from page 1 of 3.
        "listing_total": report.listing_total,
        "listing_page": report.listing_page,
        "listing_pages": report.listing_pages,
        "listing_page_size": LISTING_PAGE_SIZE,
        "listing_order": "mtime-desc,ref-asc",
        # The index, as ROWS — ref, size signal, sensitivity. No `sections`:
        # a JSON consumer that wanted every body can ask for `--limit <n>`, and
        # putting them here would rebuild the dump the digest exists to avoid.
        "listing": [
            {
                "ref": e.ref,
                "file": e.filename,
                "sensitivity": e.sensitivity,
                "declared_sensitivity": e.declared_sensitivity,
                "nuance_bullets": e.bullet_count,
            }
            for e in report.listing
        ],
        "entries": [
            {
                "ref": e.ref,
                "file": e.filename,
                "sensitivity": e.sensitivity,
                "declared_sensitivity": e.declared_sensitivity,
                "sections": dict(e.sections),
                "missing_sections": list(e.missing_sections),
                "is_bare": e.is_bare,
            }
            for e in report.entries
        ],
    }


# --- Search: the scorer --------------------------------------------------------
#
# 🔴 STDLIB ONLY, AND THAT IS A MEASUREMENT. The store is ~81 KB across 30 files;
# a `re`-based scan of all of it is single-digit milliseconds, so an external
# matcher would buy no speed while costing a binary that is not guaranteed on
# PATH and an output format nobody pinned. This module shells out to NOTHING and
# that property is asserted, not asserted-about.

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> tuple[str, ...]:
    """Lowercase alphanumeric runs, in order. THE one tokenizer.

    Punctuation is a separator, so `rate-limit`, `rate_limit` and `rate limit` all
    tokenize identically — which is why the compound handling below only has to
    solve the CONCATENATED spelling (`ratelimit`) and not four punctuation
    variants.
    """
    return tuple(_TOKEN.findall(text.lower()))


def candidate_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    """A unit's tokens PLUS every adjacent pair joined.

    🔴 THIS IS THE COMPOUND-TERM FIX, and it is deliberate rather than a lowered
    cutoff. `ratelimit` never clears a fuzzy threshold against `rate` or `limit`
    — it is not a typo of either — so the corpus side grows the concatenation and
    the match becomes EXACT (1.00) instead of fuzzy-and-arguable. The other
    direction (`rate limit` searched against a corpus that writes `ratelimit`) is
    covered by the prefix/substring rules in `pair_strength`.

    Adjacent PAIRS only. Triples were not added: nothing in the corpus or the
    fixture set needs them, and each extra join widens the false-match surface.
    """
    return tuple(tokens) + tuple(a + b for a, b in zip(tokens, tokens[1:]))


def pair_strength(q: str, t: str) -> float:
    """How strongly one query token matches one candidate token, in [0, 1].

    The ladder, each rung strictly below the one above so an exact match always
    wins and a weak hit PRINTS as weak:

        1.00  identical
        0.92  one is a prefix of the other   (`postgres` → `postgresql`)
        0.85  one contains the other         (`limit` → `ratelimit`)
        ratio a `difflib` ratio ≥ FUZZY_FLOOR (`conection` → `connection`, 0.95)
        0.00  otherwise

    🔴 Tokens shorter than `MIN_INEXACT_LEN` take the first rung or nothing. One
    length rule guarding all three inexact rungs, not three — a per-rung length
    constant is how a predicate ends up wrong at N−1 of its sites.

    The `difflib` call is gated on a length window as well as on the floor: a
    ratio can only reach `FUZZY_FLOOR` when the lengths are close, so the window
    changes no answer and keeps a full-store scan in the millisecond range.
    """
    if q == t:
        return 1.0
    if len(q) < MIN_INEXACT_LEN or len(t) < MIN_INEXACT_LEN:
        return 0.0
    if t.startswith(q) or q.startswith(t):
        return PREFIX_STRENGTH
    if q in t or t in q:
        return SUBSTRING_STRENGTH
    if abs(len(q) - len(t)) > 2:
        return 0.0
    ratio = difflib.SequenceMatcher(None, q, t).ratio()
    return ratio if ratio >= FUZZY_FLOOR else 0.0


def score_unit(query_tokens: Sequence[str], unit_tokens: Sequence[str]) -> float:
    """COVERAGE of the query by the unit: the mean best strength per query token.

    🔴 A MEAN AND NOT A MAX, which is the whole reason an absent term is
    observable. `nginx zzzz` against a block that says `nginx` scores 0.50 and
    does not clear the default threshold, so a query whose second word appears
    nowhere returns nothing rather than the first word's hits wearing the second
    word's authority. A max would have made every multi-token query as loose as
    its loosest word.

    An empty query scores 0 everywhere rather than matching everything: "the user
    asked for nothing" and "everything matches" are different answers and only one
    of them is honest.
    """
    if not query_tokens:
        return 0.0
    cands = candidate_tokens(unit_tokens)
    if not cands:
        return 0.0
    total = 0.0
    for q in query_tokens:
        best = 0.0
        for t in cands:
            s = pair_strength(q, t)
            if s > best:
                best = s
                if best == 1.0:
                    break
        total += best
    return total / len(query_tokens)


# --- Search: what a hunk is ----------------------------------------------------


@dataclass(frozen=True)
class Block:
    """One searchable unit of an entry body: a bullet with its continuation
    lines, or a paragraph, ALWAYS under a named section.

    🔴 THE UNIT IS A BLOCK, NOT A LINE, and that is what makes multi-token queries
    work at all. The store is written in bullets that wrap: `nginx` on the first
    line and `rate-limit` on its continuation is ONE fact, and a per-line scorer
    gives each half 0.50 and returns nothing — the exact "returns NOTHING" failure
    a per-line two-stage prototype produced.
    """

    section: str
    start: int
    """1-based line number of the block's first line, in the entry FILE."""
    lines: tuple[str, ...] = ()

    @property
    def end(self) -> int:
        return self.start + len(self.lines) - 1

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")


def entry_blocks(text: str) -> tuple[Block, ...]:
    """Split an entry body into searchable blocks, each under its section.

    🔴 EVERYTHING BEFORE THE FIRST HEADING IS SKIPPED, and that is the front-matter
    exclusion — expressed as a property of the OUTPUT rather than as a second
    front-matter parser. A hunk has to name its section (the caller prints it), so
    text with no section cannot be a hunk; front matter, which carries no `##`,
    falls out for free. That matters: a prototype that folded slug tokens into
    every line ranked the `---` fence line first, and `parse_front_matter` in
    `subsystem_resolver` stays the ONE thing that reads front matter.

    Headings themselves are not blocks — a query matching the literal words
    "Nuance / work-history" would otherwise hit every entry in the store.

    Fenced code is kept whole: a fence's contents are not bullets even when they
    begin with `-`, and splitting a snippet mid-command emits a fragment that
    reads like a complete instruction.
    """
    blocks: list[Block] = []
    section: str | None = None
    cur: list[str] = []
    start = 0
    in_fence = False

    def flush() -> None:
        nonlocal cur, start
        if cur and section is not None:
            blocks.append(Block(section=section, start=start, lines=tuple(cur)))
        cur = []
        start = 0

    for n, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            if not cur:
                start = n
            cur.append(line)
            continue
        if in_fence:
            cur.append(line)
            continue
        heading = _HEADING.match(line)
        if heading:
            flush()
            section = heading.group(0).strip()
            continue
        if not line.strip():
            flush()
            continue
        if _BULLET.match(line):
            flush()
        if not cur:
            start = n
        cur.append(line)
    flush()
    return tuple(blocks)


@dataclass(frozen=True)
class Hunk:
    """One match, carrying EVERYTHING needed to read it safely on its own.

    🔴 THE LABELS TRAVEL WITH THE CONTENT. The caveat prints once per invocation
    and sensitivity is a per-entry fact, but hunk output INTERLEAVES entries — so
    a `scope/ref` or a `sensitivity=` printed once at the top would end up
    describing somebody else's lines by the time a reader reaches them. Every
    hunk therefore restates its scope, ref, file, line, section and sensitivity.
    """

    scope: str
    ref: str
    filename: str
    sensitivity: str
    declared_sensitivity: str | None
    """A `sensitivity:` the schema does not know, which the fail-safe overrode.
    Carried onto the HUNK and not just the entry: hunk output interleaves
    entries, so an override noted anywhere but on the hunk itself is an override
    the reader never sees next to the lines it governs."""

    section: str
    start: int
    lines: tuple[str, ...]
    score: float
    basis: str
    """One of `HUNK_BASES`."""

    name_score: float = 0.0
    """How well the query matched this entry's ref/aliases. A TIE-BREAK ONLY — it
    is deliberately NOT folded into `score`, which stays a claim about the printed
    lines and nothing else.

    🔴 It exists because a one-word query for a subsystem's NAME hits that
    subsystem's own entry and half a dozen passing mentions elsewhere, ALL at
    1.00, and an alphabetical tie-break then puts the passing mentions first. That
    is the "ranks noise above signal" failure with a perfect-looking score beside
    it. Adding it to `score` instead would have made a printed 1.15 or a hunk
    whose number nothing on the screen explains."""

    @property
    def end(self) -> int:
        return self.start + len(self.lines) - 1


@dataclass(frozen=True)
class SearchReport:
    """One deterministic answer to "where does the index say anything about X?"."""

    status: str
    scope: str
    store_root: str
    query: str
    threshold: float = DEFAULT_SEARCH_THRESHOLD
    context: int = CONTEXT_BULLET
    hunks: tuple[Hunk, ...] = ()
    total_hits: int = 0
    """Hunks that cleared the threshold, BEFORE `max_hits`. The truncation
    discriminator, exactly as `total_in_scope` is for the digest."""

    max_hits: int = DEFAULT_MAX_HITS
    entries_searched: int = 0
    scopes_searched: tuple[str, ...] = ()
    known_scopes: tuple[str, ...] = ()
    best_below: tuple[str, float] | None = None
    """`(ref, score)` of the best hunk that did NOT clear the threshold.

    🔴 THIS IS WHAT MAKES A ZERO READABLE. `claude/RULES.md`: an empty result
    cannot distinguish two mechanisms. "The query matched nothing anywhere" and
    "the best candidate scored 0.50 against a threshold of 0.60" are different
    facts with different next actions — lower the threshold, or rephrase — and a
    searcher that prints the same blank for both has diagnosed nothing."""

    malformed: tuple[MalformedEntry, ...] = ()
    """Entry files in the SEARCHED scopes that could not be indexed — so they were
    never searched, and `entries_searched` does not count them. Rendered on every
    status: a hit list is incomplete in a way only this block can say."""

    malformed_elsewhere: tuple[MalformedEntry, ...] = ()
    """The same, outside the searched scopes. Empty under `--all-scopes`, which
    searches everything the store holds."""

    @property
    def omitted(self) -> int:
        return max(0, self.total_hits - len(self.hunks))

    @property
    def label(self) -> str:
        """The searched scopes, in prose. ONE spelling, shared with the caveat."""
        return scope_label(self.scopes_searched)

    @property
    def caveat(self) -> str:
        # The label is handed over UNQUOTED — `caveat_text` owns the backticks, so
        # quoting here too would print them twice and is exactly the kind of
        # near-miss a second spelling of one string produces.
        return caveat_text(self.label)


def _entry_hunks(
    store: Path,
    entry: SubsystemEntry,
    query_tokens: Sequence[str],
    *,
    threshold: float,
    context: int,
) -> tuple[list[Hunk], list[Hunk]]:
    """Every hunk this ONE entry offers, split into (cleared, below).

    Stage 2 of the two-stage scorer. Stage 1 — does the entry qualify at all —
    is the caller's, because it needs the NAME score and the best block score
    together.
    """
    recalled = read_entry(store, entry)
    path = store / entry.scope / entry.filename
    text = path.read_text(encoding="utf-8", errors="replace")
    raw = text.splitlines()

    name_tokens = tokenize(" ".join((entry.ref, entry.slug, *entry.aliases)))
    name_score = score_unit(query_tokens, name_tokens)

    def make(block: Block, score: float, basis: str) -> Hunk:
        if context == CONTEXT_BULLET:
            lines, start = block.lines, block.start
        else:
            # `-C N` — N RAW lines either side of the block's first line, clamped
            # to the file. Deliberately raw: the caller asked for a window, and a
            # window that quietly snapped back to a bullet would not be one.
            lo = max(1, block.start - context)
            hi = min(len(raw), block.end + context)
            lines, start = tuple(raw[lo - 1 : hi]), lo
        return Hunk(
            scope=entry.scope,
            ref=entry.ref,
            filename=entry.filename,
            sensitivity=recalled.sensitivity,
            declared_sensitivity=recalled.declared_sensitivity,
            section=block.section,
            start=start,
            lines=lines,
            score=score,
            basis=basis,
            name_score=name_score,
        )

    scored =[(score_unit(query_tokens, tokenize(b.text)), b) for b in entry_blocks(text)]
    cleared = [make(b, s, "line") for s, b in scored if s >= threshold]
    if cleared:
        return cleared, []

    best = max(scored, default=None, key=lambda sb: (sb[0], -sb[1].start))
    if best is None or (best[0] <= 0.0 and name_score < threshold):
        # 🔴 A ZERO-SCORING BLOCK IS NOT A NEAR MISS. Reporting it as one would
        # make `best_below` say "the closest candidate scored 0.00", which reads
        # as a weak match and is really an ABSENT TERM — the two need different
        # next actions (lower the threshold vs. rephrase), so they must not
        # collapse into one sentence.
        return [], []
    if name_score >= threshold:
        # 🔴 THE NAME-ONLY HIT. The entry IS the answer — its ref or an alias is
        # what was searched for — and no single block happened to clear. Emitting
        # nothing here is what made a per-line two-stage prototype return NOTHING
        # for a query that named an entry outright. The basis says which selector
        # fired, so a name hit is never mistaken for a content hit.
        return [make(best[1], name_score, "entry-name")], []
    return [], [make(best[1], best[0], "line")]


def search(
    store_root: str | Path,
    scope: str,
    query: str,
    *,
    context: int = CONTEXT_BULLET,
    threshold: float = DEFAULT_SEARCH_THRESHOLD,
    max_hits: int = DEFAULT_MAX_HITS,
    all_scopes: bool = False,
) -> SearchReport:
    """Find HUNKS matching `query`. READ-ONLY, stdlib only, nothing is spawned.

    Guard order — each reachable by an input no earlier guard rejects:
      1. `query` non-empty      → ValueError
      2. `threshold` in [0, 1]  → ValueError  (a real query still reaches it)
      3. `max_hits` sanity      → ValueError
      4. `context` sanity       → ValueError
      5. store root exists      → StoreMissingError  (reused, same condition)
      6. scope known            → status `scope-absent`, NOT an error

    Statuses are `STATUS_PRECEDENCE`'s, not a second vocabulary: `search-hit`,
    `search-no-match`, and `scope-absent` when a single named scope is not in the
    store. `--all-scopes` cannot produce `scope-absent` — it names no scope.
    """
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        raise ValueError(f"threshold must be a number in [0, 1], got {threshold!r}")
    if not 0.0 <= float(threshold) <= 1.0:
        raise ValueError(f"threshold must be a number in [0, 1], got {threshold!r}")
    if not isinstance(max_hits, int) or isinstance(max_hits, bool) or max_hits < 1:
        raise ValueError(f"max-hits must be an int >= 1, got {max_hits!r}")
    if not isinstance(context, int) or isinstance(context, bool) or context < CONTEXT_BULLET:
        raise ValueError(f"context must be an int >= 0, got {context!r}")

    store, index = load_store(store_root, verb="searched")
    bad = index.malformed_in(scope)
    bad_elsewhere = index.malformed_outside((scope,))

    if all_scopes:
        scopes = index.scopes
    elif normalize_ref(scope) not in index.scopes:
        # Asked of `index.scopes` rather than by catching the loader's raise: the
        # ONE `except UnknownScopeError` in this module is `recall`'s, and a
        # second copy of a catch is a second place for the two to disagree about
        # what an unknown scope means.
        return SearchReport(
            status="scope-absent",
            scope=normalize_ref(scope),
            store_root=str(store),
            query=query,
            threshold=float(threshold),
            context=context,
            max_hits=max_hits,
            known_scopes=index.scopes,
            malformed=bad,
            malformed_elsewhere=bad_elsewhere,
        )
    else:
        scopes = (normalize_ref(scope),)

    # Re-derived AFTER `scopes` is settled, because `--all-scopes` changes what
    # "here" means: with it, every reject in the store is in the searched set and
    # nothing is elsewhere. Deriving it once above would have reported a
    # store-wide scan's own broken entries as somebody else's problem.
    bad = tuple(m for m in index.malformed if m.scope in scopes)
    bad_elsewhere = index.malformed_outside(scopes)

    query_tokens = tokenize(query)
    cleared: list[Hunk] = []
    below: list[Hunk] = []
    searched = 0
    for sc in scopes:
        for entry in sorted(index.entries(sc), key=lambda e: e.ref):
            searched += 1
            hits, misses = _entry_hunks(
                store, entry, query_tokens, threshold=float(threshold), context=context
            )
            cleared.extend(hits)
            below.extend(misses)

    # THE ONE ordering site for hunks: score descending, then the entry-name
    # tie-break (see `Hunk.name_score`), then scope/ref/line ascending so two runs
    # over an unchanged store produce identical bytes.
    cleared.sort(key=lambda h: (-h.score, -h.name_score, h.scope, h.ref, h.start))
    worst = max(below, default=None, key=lambda h: (h.score, -h.start))
    return SearchReport(
        # 🔴 `search-unreadable` OUTRANKS `search-no-match`, and the discriminator
        # is `searched == 0` — not `cleared == 0`. A query that ran over zero
        # readable entries produced a zero that says nothing about the query, and
        # `render_search`'s no-match branch would have printed "searched 0 entries
        # … nothing cleared the threshold" — technically true, and read by
        # everyone as "the store has nothing on this". Same class of bug as
        # `scope-empty` swallowing a broken scope.
        status=(
            "search-unreadable"
            if searched == 0 and bad
            else ("search-hit" if cleared else "search-no-match")
        ),
        scope=normalize_ref(scope) if not all_scopes else "(all scopes)",
        store_root=str(store),
        query=query,
        threshold=float(threshold),
        context=context,
        hunks=tuple(cleared[:max_hits]),
        total_hits=len(cleared),
        max_hits=max_hits,
        entries_searched=searched,
        scopes_searched=tuple(scopes),
        known_scopes=index.scopes,
        best_below=(worst.ref, round(worst.score, 3)) if worst is not None else None,
        malformed=bad,
        malformed_elsewhere=bad_elsewhere,
    )


def render_search(report: SearchReport) -> str:
    """The agent-facing search block. Deterministic: same report in, same bytes out."""
    ctx = "bullet" if report.context == CONTEXT_BULLET else f"±{report.context} raw lines"
    out: list[str] = [
        f"subsystem-recall: status={report.status} scope={report.scope} "
        f"query={report.query!r} threshold={report.threshold:.2f} context={ctx}",
        f"  store: {report.store_root}",
        f"  caveat: {report.caveat}",
    ]

    # Same rule as `render_text`: before every branch, on every status.
    out.extend(render_malformed(report.malformed, report.malformed_elsewhere, report.label))

    if report.status == "search-unreadable":
        out.append("")
        out.append(unreadable_summary(report.label, report.malformed))
        out.append(
            f"  The query {report.query!r} was never run against anything — this is NOT "
            f"'no matches'."
        )
        return "\n".join(out)

    if report.status == "scope-absent":
        out.append("")
        out.append(
            f"NOTHING RECORDED YET — the store has no `{report.scope}/` directory, so the "
            f"query was never run. This is NOT 'no matches': nothing was searched, so "
            f"nothing can be concluded from it."
        )
        out.append(f"  scopes the store does hold: {', '.join(report.known_scopes) or '(none)'}")
        return "\n".join(out)

    scanned = (
        f"{report.entries_searched} entr"
        f"{'y' if report.entries_searched == 1 else 'ies'} in "
        f"{', '.join(f'`{s}/`' for s in report.scopes_searched) or '(none)'}"
    )

    if report.status == "search-no-match":
        out.append("")
        # 🔴 THE ZERO CARRIES ITS OWN EVIDENCE. How much was scanned (so a zero
        # from an empty scan is visible), and the best NEAR miss with its score
        # (so "matched nothing" and "just missed" are distinguishable).
        near = (
            f" The closest candidate was `{report.best_below[0]}` at "
            f"{report.best_below[1]:.2f}, below the {report.threshold:.2f} threshold — "
            f"re-run with `--threshold {max(0.0, report.best_below[1] - 0.01):.2f}` to see "
            f"it, or rephrase."
            if report.best_below is not None
            else " No candidate scored above zero at all, so this is an absent term rather "
            "than a weak one."
        )
        out.append(f"NO MATCH — searched {scanned}, and nothing cleared the threshold.{near}")
        return "\n".join(out)

    out.append("")
    out.append(
        f"SEARCH ({RECALL_LABEL}) — {len(report.hunks)} of {report.total_hits} hunk"
        f"{'' if report.total_hits == 1 else 's'} at or above {report.threshold:.2f}, "
        f"from {scanned}:"
    )
    for h in report.hunks:
        out.append("")
        out.append(
            f"  [{h.score:.2f} {h.basis}] {h.scope}/{h.ref}  {h.section}  "
            f"({h.scope}/{h.filename}:{h.start}-{h.end}, "
            f"sensitivity={sensitivity_label(h.sensitivity, h.declared_sensitivity)})"
        )
        for line in h.lines:
            out.append(f"    {line}")

    if report.omitted:
        out.append("")
        out.append(
            f"… {report.omitted} further hunk{'' if report.omitted == 1 else 's'} cleared "
            f"the threshold and were NOT shown (--max-hits {report.max_hits}). This is a "
            f"display cap, not a judgement about relevance — raise it to see the rest."
        )
    return "\n".join(out)


def search_json(report: SearchReport) -> dict:
    return {
        "status": report.status,
        "scope": report.scope,
        "store_root": report.store_root,
        "label": RECALL_LABEL,
        "caveat": report.caveat,
        "query": report.query,
        "threshold": report.threshold,
        "context": report.context,
        "max_hits": report.max_hits,
        "total_hits": report.total_hits,
        "omitted": report.omitted,
        "entries_searched": report.entries_searched,
        "scopes_searched": list(report.scopes_searched),
        "known_scopes": list(report.known_scopes),
        **_malformed_json(report.malformed, report.malformed_elsewhere),
        "best_below": (
            {"ref": report.best_below[0], "score": report.best_below[1]}
            if report.best_below is not None
            else None
        ),
        "hunks": [
            {
                "scope": h.scope,
                "ref": h.ref,
                "file": h.filename,
                "sensitivity": h.sensitivity,
                "declared_sensitivity": h.declared_sensitivity,
                "section": h.section,
                "start_line": h.start,
                "end_line": h.end,
                "score": round(h.score, 4),
                "name_score": round(h.name_score, 4),
                "basis": h.basis,
                "lines": list(h.lines),
            }
            for h in report.hunks
        ],
    }


# --- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="subsystem-recall",
        description=(
            "Surface what the /analyze-service index already records for this repo's "
            "scope: `## Pointers` + `## Nuance / work-history`, and nothing else. "
            "READ-ONLY: it never writes to the store, and it never touches the network."
        ),
    )
    p.add_argument("--repo", default=".", help="repo whose scope to read (default: cwd)")
    p.add_argument("--scope", default=None, help="override the derived store scope")
    p.add_argument("--store", default=str(DEFAULT_STORE_ROOT), help="store root")
    p.add_argument(
        "--ref",
        default=None,
        help=(
            "surface ONE entry by ref instead of the whole scope, resolved by the "
            "writer's own resolver. An ambiguous ref is reported, never picked."
        ),
    )
    p.add_argument(
        "--list",
        action="store_true",
        dest="listing",
        help=(
            "the INDEX ONLY: one line per entry — ref, bullet count, sensitivity — for "
            "EVERY entry in the scope. Never truncated, and never a body."
        ),
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            f"print up to N entry BODIES instead of the default digest (the pre-digest "
            f"behaviour; N={DEFAULT_ENTRY_LIMIT} was the old default). A truncation is "
            f"always printed; it is never silent."
        ),
    )
    p.add_argument(
        "--page",
        type=int,
        default=None,
        help=(
            f"which page of the INDEX to list, 1-based (default 1). The index is capped "
            f"at {LISTING_PAGE_SIZE} lines per page and the remainder is always counted "
            f"and never dropped. Order is NEWEST-FIRST by entry-file mtime, tie-broken by "
            f"ref, so page 1 is the freshest and the last page is the stalest. Applies to "
            f"the digest and to --list; --ref and --limit print no index."
        ),
    )
    p.add_argument(
        "-s",
        "--search",
        default=None,
        metavar="QUERY",
        help=(
            "search the scope for QUERY and print matching HUNKS instead of whole "
            "entries — for when the scope has grown past the point where reading entries "
            "whole is affordable. Fuzzy and stdlib-only: it shells out to nothing. Every "
            "hunk carries its own scope/ref, section and sensitivity=, plus its score and "
            "the threshold, so a weak match is visibly weak. A query term that matches "
            "nothing costs its share of the score, so a two-word query needs both words."
        ),
    )
    p.add_argument(
        "-C",
        "--context",
        type=int,
        default=None,
        metavar="N",
        help=(
            "with --search: print N RAW lines either side of the match instead of the "
            "default, which is the ENCLOSING BULLET. Why the bullet is the default: an "
            "entry is structured (`## Pointers`, `## Nuance / work-history`) and its "
            "bullets wrap, so a fixed line window can cut one in half and emit a fragment "
            "that reads like a complete instruction when it is not — `-C 0` gives you the "
            "matched block's own lines only, and a small `-C` can still slice a "
            "neighbouring bullet's tail onto the screen. The tradeoff is yours: the "
            "bullet is safer to quote, a raw window shows you what SURROUNDS the match "
            "(the heading above it, the next bullet) which is what you want when you are "
            "orienting rather than quoting."
        ),
    )
    p.add_argument(
        "--all-scopes",
        action="store_true",
        help="with --search: search every scope in the store, not just this repo's.",
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=None,
        metavar="F",
        help=(
            f"with --search: the score a hunk must clear, in [0, 1] "
            f"(default {DEFAULT_SEARCH_THRESHOLD:.2f}). A no-match prints the best "
            f"sub-threshold candidate and the exact --threshold that would surface it."
        ),
    )
    p.add_argument(
        "--max-hits",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"with --search: print at most N hunks (default {DEFAULT_MAX_HITS}). A "
            f"truncation is always printed; it is never silent."
        ),
    )
    p.add_argument("--json", action="store_true")
    return p


# 🔴 ONE RULE, ONE PLACE. `--search`, `--ref` and `--list` are three SELECTORS
# over the same store and every pair of them is the same conflict; three pairwise
# `if`s would be that predicate open-coded at three sites, wrong at two of them
# the first time somebody adds a fourth selector. The message keeps the wording a
# pinned test already asserts ("select different things").
_SELECTORS: tuple[tuple[str, str], ...] = (
    ("--search", "hunks matching a query"),
    ("--ref", "one entry's body"),
    ("--list", "the whole index"),
)

# Flags that only mean something under `--search`. Rejected rather than ignored:
# a flag silently doing nothing is the failure mode where a caller believes a
# setting took effect.
_SEARCH_ONLY: tuple[tuple[str, str], ...] = (
    ("context", "-C/--context"),
    ("all_scopes", "--all-scopes"),
    ("threshold", "--threshold"),
    ("max_hits", "--max-hits"),
)


def _exit_for(status: str, label: str, malformed: Sequence[MalformedEntry]) -> int:
    """The exit code, and the one stderr line that goes with it. ONE decision site.

    🔴 CONTENT SERVED ⇒ 0; NOTHING READABLE ⇒ 3. `/resume` step 4 branches on
    zero/non-zero and its instruction is "print the stderr line verbatim, note
    that recall was unavailable, and continue". That instruction is TRUE only when
    recall really was unavailable — a scope with 2 good entries and 1 malformed
    one has just served both good ones, and exiting non-zero would throw them
    away to report a defect the output already names, loudly, in band. So the
    partial case exits 0 with the MALFORMED block, and only `*-unreadable` exits
    non-zero.

    🔴 IT REUSES 3 RATHER THAN MINTING A CODE. 3 is already "the store is broken"
    for this CLI (`StoreMissingError`, `EntryUnreadableError`), the skill's
    documented handling is identical for all of them, and a fourth code would
    need every consumer to learn it before it changed any behaviour — a
    declaration no code path honours.

    The DETAIL is on stdout (per-entry rows, the whole point); this line is the
    quotable one-sentence summary, and it is the only thing written to stderr, so
    the verbatim-print instruction yields a sentence and not a wall.
    """
    if status not in UNREADABLE_STATUSES:
        return 0
    n = len(malformed)
    print(
        f"subsystem-recall: {status}: all {n} entry file{'' if n == 1 else 's'} under "
        f"`{label}` are MALFORMED — nothing could be read, so recall was unavailable. "
        f"This is NOT an empty scope and NOT 'nothing recorded yet'. Per-entry reasons "
        f"are on stdout; check a file with `subsystem_touch.py --validate <path>`.",
        file=sys.stderr,
    )
    return 3


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    # 🔴 Rejected, not silently reconciled. Every combination below has an obvious
    # "sensible" reading and they are DIFFERENT readings, so honouring one would
    # give the caller output they did not ask for and no sign of it.
    chosen = [
        flag
        for flag, _what in _SELECTORS
        if (flag == "--search" and args.search is not None)
        or (flag == "--ref" and args.ref is not None)
        or (flag == "--list" and args.listing)
    ]
    if len(chosen) > 1:
        what = {f: w for f, w in _SELECTORS}
        print(
            "subsystem-recall: "
            + " and ".join(chosen)
            + " select different things ("
            + " vs ".join(what[f] for f in chosen)
            + "). Pass one.",
            file=sys.stderr,
        )
        return 2
    if args.listing and args.limit is not None:
        print(
            "subsystem-recall: --limit is a cap on entry BODIES and --list prints none; "
            "the index is never truncated. Drop one.",
            file=sys.stderr,
        )
        return 2
    if args.page is not None and (args.ref is not None or args.limit is not None):
        print(
            "subsystem-recall: --page pages the INDEX, and --ref/--limit print no index "
            "at all. Drop one.",
            file=sys.stderr,
        )
        return 2
    if args.search is None:
        stray = [
            flag
            for attr, flag in _SEARCH_ONLY
            if getattr(args, attr) not in (None, False)
        ]
        if stray:
            print(
                f"subsystem-recall: {', '.join(stray)} only mean something with --search, "
                f"and doing nothing quietly is how a caller comes to believe a setting "
                f"took effect. Add --search or drop them.",
                file=sys.stderr,
            )
            return 2

    # `--limit` is what selects the pre-digest full-body mode. Nothing else does:
    # a default of DEFAULT_ENTRY_LIMIT here would make "the caller asked for a cap"
    # indistinguishable from "the caller asked for nothing".
    mode = "list" if args.listing else ("full" if args.limit is not None else DEFAULT_MODE)
    limit = args.limit if args.limit is not None else DEFAULT_ENTRY_LIMIT

    try:
        repo = Path(args.repo).resolve()
        scope = args.scope if args.scope is not None else scope_for_repo(repo)
        if args.search is not None:
            found = search(
                args.store,
                scope,
                args.search,
                context=args.context if args.context is not None else CONTEXT_BULLET,
                threshold=(
                    args.threshold if args.threshold is not None else DEFAULT_SEARCH_THRESHOLD
                ),
                max_hits=args.max_hits if args.max_hits is not None else DEFAULT_MAX_HITS,
                all_scopes=args.all_scopes,
            )
            print(
                json.dumps(search_json(found), indent=2)
                if args.json
                else render_search(found)
            )
            return _exit_for(found.status, found.label, found.malformed)
        # The ONE call to `focus_window`, and only where it is EVIDENCE. The
        # digest is the only mode that features an entry, and the window is
        # derived from `repo` — so an explicitly overridden `--scope` disables
        # it: the handoff doc in THIS repo says nothing about somebody else's
        # scope, and letting it vote there would be a relevance claim built on a
        # path window that never described the entries it ranked. The printed
        # basis then says `most-recent fallback`, which is the truth.
        window = (
            focus_window(repo)
            if mode == DEFAULT_MODE and args.scope is None
            else FocusWindow()
        )
        report = recall(
            args.store,
            scope,
            ref=args.ref,
            limit=limit,
            mode=mode,
            page=args.page if args.page is not None else 1,
            focus_paths=window.paths,
            focus_source=window.source,
        )
    except ValueError as exc:
        print(f"subsystem-recall: {exc}", file=sys.stderr)
        return 2
    except (TouchError, ResolverError) as exc:
        print(f"subsystem-recall: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(report_json(report), indent=2) if args.json else render_text(report))
    return _exit_for(report.status, f"{report.scope}/", report.malformed)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
