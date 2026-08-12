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


CONTRACT SUMMARY
----------------
    extract_sections(text, headings)          -> dict[str, str]
    read_entry(store_root, entry)             -> RecalledEntry
    recall(store_root, scope, *, ref=…, limit=…)
                                              -> RecallReport
    render_text(report) / report_json(report) -> str / dict
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
    "ref-absent"      a `--ref` was given and resolved to no entry.
    "ref-ambiguous"   a `--ref` named more than one entry. Never picked.
    "recalled"        at least one entry surfaced.

and the two conditions that are NOT readings but broken environments, each with
its own sentinel phrase so a caller — or a mutation test — can tell WHICH fired:

    "store root not found"   StoreMissingError  (reused from subsystem_touch:
                             the same condition, so the same class, not a
                             second spelling of one error)
    "index entry unreadable" EntryUnreadableError
    "malformed index entry"  MalformedEntryError, raised by the resolver's own
                             loader and deliberately NOT caught here

🔴 EVERY OUTPUT PATH CARRIES THE CAVEAT, INCLUDING THE EMPTY ONES. It is a
single property on the report (`RecallReport.caveat`) that both renderers print,
rather than a sentence per branch — a caveat spelled at N sites is wrong at N−1
of them. Index content is labelled `from index` and is RECALL: it was curated by
past sessions, was not re-derived, and was not matched against anything this
session did.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subsystem_resolver import (  # noqa: E402
    AmbiguousRefError,
    MalformedEntryError,
    ResolverError,
    SubsystemEntry,
    UnknownScopeError,
    load_index,
    normalize_ref,
    parse_front_matter,
    resolve_ref_tiered,
)
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
    "SENSITIVITY_FAIL_SAFE",
    "KNOWN_SENSITIVITIES",
    "STATUS_PRECEDENCE",
    "RecallError",
    "EntryUnreadableError",
    "StoreMissingError",
    "RecalledEntry",
    "RecallReport",
    "extract_sections",
    "read_entry",
    "fold_sensitivity",
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

# The two sections the recon step front-loads. `## What it is` is deliberately
# NOT here: it is one line of durable boilerplate that a resuming session either
# already knows or can read in the file, and including it turns a recall block
# into a dump of the store. The point is recall, not a dump.
POINTERS_HEADING = "## Pointers"
NUANCE_HEADING = "## Nuance / work-history"
SURFACED_HEADINGS: tuple[str, ...] = (POINTERS_HEADING, NUANCE_HEADING)

# A cap, not a filter — see the module docstring on selection. Truncation is
# always PRINTED. 12 is chosen as "more than any scope currently holds per repo
# that is not the infra repo, and small enough to read at the top of a session";
# it is a display bound with an escape hatch (`--limit`), not a safety bound, so
# unlike `MAX_TRANSCRIPT_AGE_SECONDS` in the writer it is deliberately overridable.
DEFAULT_ENTRY_LIMIT = 12

# 🔴 FAIL-SAFE, from `analyze-service/SKILL.md`: "`sensitivity:` — fail-safe:
# absent means sensitive… absent or unrecognized ⇒ `client-confidential`, never
# public". Surfaced per entry because this reader's whole job is to put curated,
# client-identifying content in front of an agent that may be one paste away from
# a PUBLIC repo, and the marker is the only thing that says so.
SENSITIVITY_FAIL_SAFE = "client-confidential"
KNOWN_SENSITIVITIES: tuple[str, ...] = ("client-confidential", "personal", "public")

# Stated once so the renderers, the JSON and the tests read the SAME rule.
# Precedence, and why each outranks the next:
#   1. scope-absent   — nothing about the store's contents can change this answer.
#   2. scope-empty    — the scope exists; there is simply nothing in it.
#   3. ref-ambiguous  — a ref was given and names more than one entry: never pick.
#   4. ref-absent     — a ref was given and names none.
#   5. recalled       — something was surfaced.
# `StoreMissingError` / `EntryUnreadableError` / `MalformedEntryError` are NOT in
# this tuple: they raise. A status constant no code path could emit would be a
# declaration with nothing behind it.
STATUS_PRECEDENCE: tuple[str, ...] = (
    "scope-absent",
    "scope-empty",
    "ref-ambiguous",
    "ref-absent",
    "recalled",
)


# --- Errors --------------------------------------------------------------------


class RecallError(Exception):
    """Base for the errors this module raises in its own right."""


class EntryUnreadableError(RecallError):
    """An entry file cannot be read. Sentinel: 'index entry unreadable'.

    🔴 It exists because the alternative is an unnamed `OSError` escaping from
    inside `load_index`. The store is a plain directory of hand-curated files on
    a machine that also runs an hourly autocommit: a file can be mid-rename, a
    directory can be sitting where a `.md` is expected, a mode can be wrong. Any
    of those would otherwise reach the caller as `IsADirectoryError` or
    `PermissionError` with no indication that the SUBSYSTEM STORE was the thing
    that failed — and a resuming session would read it as a broken tool.
    """


# --- Section extraction --------------------------------------------------------


def _is_fence(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("```") or s.startswith("~~~")


def extract_sections(text: str, headings: Sequence[str] = SURFACED_HEADINGS) -> dict[str, str]:
    """Return `{heading: body}` for each requested heading found in `text`.

    Bodies are VERBATIM — the store is markdown precisely so prose survives a
    read unmangled — with surrounding blank lines trimmed. A heading that is
    absent is simply not a key; the caller reports it by name rather than
    printing an empty block (an absent section and an empty one are different
    facts about a curated entry).

    A section runs from its heading to the next ATX heading of any level, or to
    end of file.

    🔴 FENCED BLOCKS ARE SKIPPED. A `#` line inside a code fence is not a
    heading, and treating it as one would END the section early — surfacing
    HALF an entry's nuance while looking exactly like a complete read. That is a
    silent under-report, the failure class this whole module is built against,
    so it is handled rather than left to "entries probably don't contain
    fences".

    Matching is on the EXACT heading string, not a normalized one: these are
    schema headings from `analyze-service/SKILL.md`, not user refs. Normalizing
    them would fold `## Pointers` and `## pointers!` together and quietly widen
    what the store is allowed to look like.
    """
    wanted: dict[str, list[str]] = {h: [] for h in headings}
    # 🔴 PRESENCE IS TRACKED SEPARATELY FROM CONTENT. A heading that appears with
    # nothing under it must be reported as PRESENT-AND-EMPTY, not as absent —
    # "the section was never started" and "the section is there and unfilled"
    # are different facts about a curated entry, and only one of them is a
    # reason to go look somewhere else. Deriving presence from a non-empty body
    # collapses them, and it did: an empty section followed by another heading
    # read as absent while the same empty section at end-of-file read as
    # present, purely because of what came after it.
    seen: set[str] = set()
    current: str | None = None
    in_fence = False
    for line in text.splitlines():
        if _is_fence(line):
            in_fence = not in_fence
            if current is not None:
                wanted[current].append(line)
            continue
        if not in_fence and line.startswith("#"):
            stripped = line.rstrip()
            current = stripped if stripped in wanted else None
            if current is not None:
                seen.add(current)
            continue
        if current is not None:
            wanted[current].append(line)
    return {h: "\n".join(wanted[h]).strip("\n") for h in headings if h in seen}


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


# --- The recalled entry --------------------------------------------------------


@dataclass(frozen=True)
class RecalledEntry:
    """One entry's surfaced sections, plus what was NOT there."""

    ref: str
    filename: str
    sensitivity: str
    sections: Mapping[str, str] = field(default_factory=dict)
    missing_sections: tuple[str, ...] = ()
    """Requested headings this entry does not carry — REPORTED, never silent.

    An entry with pointers and no work-history is ordinary (nothing has been
    journaled against it yet). An entry with NEITHER is also ordinary — the
    writer's own `new_entry_template` ships a stub. What is not ordinary is a
    reader that shows nothing and lets a caller conclude the entry is empty when
    the extractor missed, so the two are told apart in the output.
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
    return RecalledEntry(
        ref=entry.ref,
        filename=entry.filename,
        sensitivity=fold_sensitivity(fm.get("sensitivity")),
        sections=sections,
        missing_sections=tuple(h for h in SURFACED_HEADINGS if h not in sections),
    )


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

    @property
    def omitted(self) -> int:
        return max(0, self.total_in_scope - len(self.entries)) if self.ref is None else 0

    @property
    def caveat(self) -> str:
        """What this window can and cannot see. ONE spelling, every output path.

        🔴 Stated as a property rather than written into each renderer: the two
        renderers and every status branch must make the SAME claim, and a
        sentence duplicated per branch is one edit away from a branch that
        promises more than the store can support. `/analyze-service` words the
        provenance as `from index`; this reuses that exact label.
        """
        return (
            f"{RECALL_LABEL} — RECALL, NOT LIVE OBSERVATION. These are notes curated by "
            f"PAST sessions in the local store under `{self.scope}/`. Nothing here was "
            f"re-derived just now, nothing was matched against anything THIS session has "
            f"done, and an entry is exactly as fresh as the last time someone pruned it "
            f"(prune-on-resolve is manual), so a bullet may describe a gotcha already "
            f"fixed. This window CANNOT see: live state of any kind, any repo whose scope "
            f"has no directory in this store, and any work neither `/analyze-service` nor "
            f"`/handoff` ever recorded. Treat every line as a POINTER to verify, never as "
            f"a current reading. Sensitivity is marked per entry; absent means "
            f"`{SENSITIVITY_FAIL_SAFE}` — never copy an entry's content into a public repo."
        )


def recall(
    store_root: str | Path,
    scope: str,
    *,
    ref: str | None = None,
    limit: int = DEFAULT_ENTRY_LIMIT,
) -> RecallReport:
    """Surface the index's `## Pointers` + `## Nuance / work-history` for a scope.

    READ-ONLY. No clock, no network, no git, no prompt — `/resume`'s job is to
    re-enter work, and a recall step that interrogated the network or blocked on
    a confirm would make the thing it is supposed to accelerate slower.

    Guard order — each reachable by an input no earlier guard rejects:
      1. `limit` sanity      → ValueError
      2. store root exists   → StoreMissingError
      3. store is readable   → EntryUnreadableError
         (a malformed entry raises `MalformedEntryError` from the resolver's own
         loader and is deliberately NOT caught: the loader is fail-closed on
         purpose, and an interactive caller must be told the store is broken
         rather than handed a silently short index.)
      4. scope known         → status `scope-absent`, NOT an error

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

    store = Path(store_root)
    if not store.is_dir():
        raise StoreMissingError(
            f"store root not found: {store} — expected the `/analyze-service` index "
            f"store. Nothing was recalled; this is NOT 'nothing recorded yet'"
        )

    try:
        index = load_index(store)
    except MalformedEntryError:
        raise
    except OSError as exc:
        raise EntryUnreadableError(
            f"index entry unreadable: under {store} ({type(exc).__name__}: {exc}) — the "
            f"store was not fully read, so this report would be INCOMPLETE"
        ) from exc

    try:
        entries = index.entries(scope)
    except UnknownScopeError:
        return RecallReport(
            status="scope-absent",
            scope=normalize_ref(scope),
            store_root=str(store),
            limit=limit,
            ref=ref,
            known_scopes=index.scopes,
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
                ref=exc.ref,
                candidates=exc.candidates,
                known_scopes=index.scopes,
            )
        if entry is None:
            return RecallReport(
                status="ref-absent",
                scope=normalize_ref(scope),
                store_root=str(store),
                total_in_scope=len(entries),
                limit=limit,
                ref=normalize_ref(ref),
                known_scopes=index.scopes,
            )
        return RecallReport(
            status="recalled",
            scope=normalize_ref(scope),
            store_root=str(store),
            entries=(read_entry(store, entry),),
            total_in_scope=len(entries),
            limit=limit,
            ref=normalize_ref(ref),
            known_scopes=index.scopes,
        )

    if not entries:
        return RecallReport(
            status="scope-empty",
            scope=normalize_ref(scope),
            store_root=str(store),
            total_in_scope=0,
            limit=limit,
            known_scopes=index.scopes,
        )

    # THE ONE ordering site: canonical ref ascending, so two runs over an
    # unchanged store produce identical bytes and a diff of them shows only real
    # movement.
    ordered = sorted(entries, key=lambda e: e.ref)
    return RecallReport(
        status="recalled",
        scope=normalize_ref(scope),
        store_root=str(store),
        entries=tuple(read_entry(store, e) for e in ordered[:limit]),
        total_in_scope=len(ordered),
        limit=limit,
        known_scopes=index.scopes,
    )


# --- Rendering -----------------------------------------------------------------


def render_text(report: RecallReport) -> str:
    """The agent-facing recall block. Deterministic: same report in, same bytes out."""
    out: list[str] = [
        f"subsystem-recall: status={report.status} scope={report.scope}",
        f"  store: {report.store_root}",
        f"  caveat: {report.caveat}",
    ]

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
        out.append(
            f"NO SUCH ENTRY — `{report.ref}` resolves to nothing in `{report.scope}/`, "
            f"which holds {report.total_in_scope} entr"
            f"{'y' if report.total_in_scope == 1 else 'ies'}. Nothing recorded under that "
            f"name yet; re-run without `--ref` to see what IS recorded."
        )
        return "\n".join(out)

    n = len(report.entries)
    out.append("")
    out.append(
        f"RECALL ({RECALL_LABEL}) — {n} of {report.total_in_scope} entr"
        f"{'y' if report.total_in_scope == 1 else 'ies'} in `{report.scope}/`:"
    )
    for e in report.entries:
        out.append("")
        out.append(f"  ### {e.ref}  ({report.scope}/{e.filename}, sensitivity={e.sensitivity})")
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

    if report.omitted:
        out.append("")
        out.append(
            f"… {report.omitted} more entr{'y' if report.omitted == 1 else 'ies'} in "
            f"`{report.scope}/` NOT shown (--limit {report.limit}). This is a display cap, "
            f"not a judgement about relevance — raise it to see the rest."
        )
    return "\n".join(out)


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
        "total_in_scope": report.total_in_scope,
        "omitted": report.omitted,
        "known_scopes": list(report.known_scopes),
        "entries": [
            {
                "ref": e.ref,
                "file": e.filename,
                "sensitivity": e.sensitivity,
                "sections": dict(e.sections),
                "missing_sections": list(e.missing_sections),
                "is_bare": e.is_bare,
            }
            for e in report.entries
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
        "--limit",
        type=int,
        default=DEFAULT_ENTRY_LIMIT,
        help=(
            f"display cap on entries (default {DEFAULT_ENTRY_LIMIT}). A truncation is "
            f"always printed; it is never silent."
        ),
    )
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        scope = args.scope if args.scope is not None else scope_for_repo(Path(args.repo).resolve())
        report = recall(args.store, scope, ref=args.ref, limit=args.limit)
    except ValueError as exc:
        print(f"subsystem-recall: {exc}", file=sys.stderr)
        return 2
    except (RecallError, TouchError, ResolverError) as exc:
        print(f"subsystem-recall: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(report_json(report), indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
