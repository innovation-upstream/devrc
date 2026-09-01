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
A zero-result query has TWO causes that mean opposite things:

    the corpus does not say that      an ANSWER. Carry on.
    nothing was ever indexed          a BROKEN INDEX. The query never ran against
                                      anything, and reading it as an answer is a
                                      confident zero with an empty table behind it.

`claude/RULES.md` → "An EMPTY RESULT cannot distinguish two mechanisms". So every
response — hit, no-match and broken alike — carries the literal
`indexed_docs=N indexed_sections=M`, and the two zero cases are rendered with
sentences that SHARE NO OPENING PHRASE:

    NO MATCH — …            the index holds rows and none of them matched
    🔴 BROKEN INDEX — …     the index holds NOTHING; nothing was searched

`SearchOutcome.status` names which, in values that share no spelling
(`hit` / `no-match` / `broken-index`), so a caller can switch on one field rather
than parse prose. The exit code follows the status, and `broken-index` exits
non-zero: an empty index is a broken environment, not a reading.


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
    "SearchOutcome",
    "recall_banner",
    "stats_line",
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

#: The three outcomes, in values that share no spelling — see the module docstring.
#: `broken-index` is NOT spelled `*-empty`: an index with nothing in it and a query
#: that matched nothing in a full index are different facts, and two statuses that
#: share a word get read as one.
STATUSES: tuple[str, ...] = ("hit", "no-match", "broken-index")


@dataclass(frozen=True)
class SearchOutcome:
    query: str
    stats: IndexStats
    hits: tuple[Hit, ...]
    status: str
    backend: str
    repo: str | None = None
    sections: tuple[str, ...] = ()
    limit: int = DEFAULT_LIMIT

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


def stats_line(stats: IndexStats, backend: str) -> str:
    """🔴 THE LINE THAT MAKES A ZERO READABLE. Emitted on every status, in a fixed
    `key=value` shape so a caller can grep it rather than parse prose."""
    return (
        f"indexed_docs={stats.indexed_docs} indexed_sections={stats.indexed_sections} "
        f"backend={backend}"
    )


def run_search(
    store: SectionStore,
    query: str,
    *,
    backend: str,
    repo: str | None = None,
    sections: Sequence[str] = (),
    limit: int = DEFAULT_LIMIT,
) -> SearchOutcome:
    """Query the store and CLASSIFY the result. The one place `status` is decided.

    🔴 THE INDEX IS CHECKED BEFORE THE ZERO IS INTERPRETED, and the order is the
    guard. `stats()` is read FIRST and unconditionally: an empty index yields
    `broken-index` whether or not the query would have matched, because with zero
    rows the query did not run against anything and "no match" would be a claim
    about a corpus that was never consulted."""
    stats = store.stats()
    hits = tuple(store.search(query, repo=repo, sections=sections, limit=limit))
    if stats.indexed_docs == 0:
        status = "broken-index"
    elif hits:
        status = "hit"
    else:
        status = "no-match"
    return SearchOutcome(
        query=query,
        stats=stats,
        hits=hits,
        status=status,
        backend=backend,
        repo=repo,
        sections=tuple(sections),
        limit=limit,
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
        stats_line(outcome.stats, outcome.backend),
        "  ".join(scope),
    ]

    if outcome.status == "broken-index":
        lines += [
            "",
            "🔴 BROKEN INDEX — this table holds ZERO documents, so the query above ran "
            "against NOTHING.",
            "   This is NOT the answer 'the corpus does not mention that': the corpus was "
            "never consulted.",
            "   Rebuild it (`scripts/lib/handoff_index.py --rebuild`) or check the "
            "handoff-index-sync unit, then re-run.",
        ]
        return "\n".join(lines)

    if outcome.status == "no-match":
        lines += [
            "",
            f"NO MATCH — the index WAS searched and none of its "
            f"{outcome.stats.indexed_sections} section(s) matched.",
            "   That is an answer about the corpus, not a broken tool: the docs do not "
            "discuss this in these words.",
            "   Try fewer/other terms, or widen --repo / --section before concluding "
            "nobody wrote it down.",
        ]
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
        "hits": [
            {
                "repo": h.repo, "slug": h.slug, "doc_path": h.doc_path,
                "doc_date": h.doc_date, "section": h.section, "ordinal": h.ordinal,
                "heading": h.heading, "body": h.body, "rank": h.rank,
            }
            for h in outcome.hits
        ],
    }


def _offline_store(repos: Sequence[str]) -> tuple[MemorySectionStore, list[str]]:
    targets = [(r, Path(r).name) for r in repos] or handoff_index.default_repos()
    derivations = [handoff_index.derive_repo(p, label=lab) for p, lab in targets]
    rows = [s for d in derivations for s in d.sections]
    warnings = [w for d in derivations for w in d.warnings]
    return MemorySectionStore(rows), warnings


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="search the handoff-doc section index",
        epilog="Results are POINTERS TO VERIFY, not current readings.",
    )
    ap.add_argument("--query", required=True)
    ap.add_argument("--repo", default=None, help="restrict to one repo label")
    ap.add_argument("--section", action="append", default=[], choices=list(SECTIONS),
                    help="restrict to one or more section kinds (repeatable)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="derive from git in-process and answer with no database "
                         "(a DIFFERENT ranker — see the module docstring)")
    ap.add_argument("--offline-repo", action="append", default=[],
                    help="repo root for --offline (repeatable)")
    args = ap.parse_args(argv)

    if args.offline:
        store, warnings = _offline_store(args.offline_repo)
        for w in warnings:
            print(w, file=sys.stderr)
        backend = "memory"
        outcome = run_search(store, args.query, backend=backend, repo=args.repo,
                             sections=args.section, limit=args.limit)
    else:
        MailDB = handoff_index.import_maildb()
        with MailDB() as db:
            store = handoff_index.PostgresSectionStore(db.conn)
            outcome = run_search(store, args.query, backend="postgres", repo=args.repo,
                                 sections=args.section, limit=args.limit)

    print(json.dumps(outcome_json(outcome), indent=2) if args.json else render(outcome))
    # 🔴 A BROKEN INDEX EXITS NON-ZERO. It is a broken environment, not a reading,
    # and a caller that only checks the exit code must not read it as "no results".
    return 3 if outcome.status == "broken-index" else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
