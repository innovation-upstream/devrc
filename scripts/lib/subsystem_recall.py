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


CONTRACT SUMMARY
----------------
    extract_sections(text, headings)          -> dict[str, str]
    read_entry(store_root, entry)             -> RecalledEntry
    focus_paths_from_text(text)               -> tuple[str, ...]
    focus_window(repo)                        -> FocusWindow
    select_featured(entries, index, scope, …) -> (ref, basis)
    recall(store_root, scope, *, ref=…, limit=…, mode=…, focus_paths=…)
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
    "index entry unreadable" EntryUnreadableError (reused from
                             subsystem_resolver, for the same reason —
                             subsystem_touch raises it on the same files)
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
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subsystem_resolver import (  # noqa: E402
    NUANCE_HEADING,
    POINTERS_HEADING,
    AmbiguousRefError,
    EntryUnreadableError,
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
    "FOCUS_MIN_PATHS",
    "HANDOFF_GLOBS",
    "SENSITIVITY_FAIL_SAFE",
    "KNOWN_SENSITIVITIES",
    "STATUS_PRECEDENCE",
    "EntryUnreadableError",
    "StoreMissingError",
    "FocusWindow",
    "RecalledEntry",
    "RecallReport",
    "extract_sections",
    "read_entry",
    "fold_sensitivity",
    "focus_paths_from_text",
    "focus_window",
    "select_featured",
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


# --- The recalled entry --------------------------------------------------------


@dataclass(frozen=True)
class RecalledEntry:
    """One entry's surfaced sections, plus what was NOT there."""

    ref: str
    filename: str
    sensitivity: str
    sections: Mapping[str, str] = field(default_factory=dict)

    bullet_count: int = 0
    """Top-level `## Nuance / work-history` bullets, via the resolver's own
    `parse_journal_bullets`. The index line's SIZE SIGNAL: it is what a reader
    wants in order to decide whether an entry is worth a `--ref`, and it is the
    unit the store's own prune-on-resolve discipline is denominated in. A byte
    count would have been cheaper and would have measured markdown, not history.
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
    return RecalledEntry(
        ref=entry.ref,
        filename=entry.filename,
        sensitivity=fold_sensitivity(fm.get("sensitivity")),
        sections=sections,
        bullet_count=len(parse_journal_bullets(sections.get(NUANCE_HEADING, ""))),
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

    mode: str = DEFAULT_MODE
    """Which display mode produced this report — see `RECALL_MODES`."""

    listing: tuple[RecalledEntry, ...] = ()
    """EVERY entry in the scope, one index line each. NEVER truncated, in any
    mode that populates it: the whole point of the digest is that the set of
    things on record is complete even when only one of them is printed in full.
    Empty in `full` mode, which prints bodies and a truncation notice instead."""

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
    mode: str = DEFAULT_MODE,
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
      2. `mode` known        → ValueError  (a valid limit still reaches it)
      3. store root exists   → StoreMissingError
      4. store is readable   → EntryUnreadableError
         (a malformed entry raises `MalformedEntryError` from the resolver's own
         loader and is deliberately NOT caught: the loader is fail-closed on
         purpose, and an interactive caller must be told the store is broken
         rather than handed a silently short index.)
      5. scope known         → status `scope-absent`, NOT an error

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
    if mode not in RECALL_MODES:
        raise ValueError(f"mode must be one of {RECALL_MODES}, got {mode!r}")

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
            mode=mode,
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
                mode=mode,
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
                mode=mode,
                ref=normalize_ref(ref),
                known_scopes=index.scopes,
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
        )

    # `digest` and `list` both read EVERY entry — the index line carries a bullet
    # count, which only the file can answer. Reading 25 small files costs
    # milliseconds; it is the OUTPUT that had to shrink, not the input.
    read = tuple(read_entry(store, e) for e in ordered)
    if mode == "list":
        return RecallReport(
            status="recalled",
            scope=normalize_ref(scope),
            store_root=str(store),
            total_in_scope=len(ordered),
            limit=limit,
            mode=mode,
            listing=read,
            known_scopes=index.scopes,
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
        listing=read,
        featured_basis=basis,
        known_scopes=index.scopes,
    )


# --- Rendering -----------------------------------------------------------------


def listing_line(entry: RecalledEntry, width: int) -> str:
    """ONE index line: `  <ref>   N bullets  <sensitivity>`. ~60 B.

    Three fields and no fourth. The ref is what `--ref` takes, the bullet count
    is the size signal that says whether a `--ref` is worth spending, and the
    sensitivity has to travel WITH the entry it describes — a sensitivity stated
    once at the top of a block is a sensitivity that gets copied away from.
    """
    n = entry.bullet_count
    return f"  {entry.ref.ljust(width)}  {n:>3} {'bullet ' if n == 1 else 'bullets'}  {entry.sensitivity}"


def _render_listing(report: RecallReport) -> list[str]:
    width = max((len(e.ref) for e in report.listing), default=0)
    return [
        "",
        f"INDEX ({RECALL_LABEL}) — ALL {len(report.listing)} entr"
        f"{'y' if len(report.listing) == 1 else 'ies'} in `{report.scope}/`, none omitted:",
        *(listing_line(e, width) for e in report.listing),
    ]


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

    if report.listing:
        out.extend(_render_listing(report))

    if not report.entries:
        # `list` mode. LOUD about what it did not do: an index with no bodies is
        # not an empty scope, and the two must not read the same.
        out.append("")
        out.append(
            f"NO ENTRY BODIES WERE PRINTED (--list). The {report.total_in_scope} entr"
            f"{'y above is' if report.total_in_scope == 1 else 'ies above are'} the complete "
            f"index for `{report.scope}/` — this is not an empty scope. Run `--ref <name>` for "
            f"one entry's `{POINTERS_HEADING}` + `{NUANCE_HEADING}`."
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

    if report.omitted and report.listing:
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
        # The index, as ROWS — ref, size signal, sensitivity. No `sections`:
        # a JSON consumer that wanted every body can ask for `--limit <n>`, and
        # putting them here would rebuild the dump the digest exists to avoid.
        "listing": [
            {
                "ref": e.ref,
                "file": e.filename,
                "sensitivity": e.sensitivity,
                "bullets": e.bullet_count,
            }
            for e in report.listing
        ],
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
    p.add_argument("--json", action="store_true")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)

    # 🔴 Rejected, not silently reconciled. Either combination has an obvious
    # "sensible" reading and they are different readings, so honouring one would
    # give the caller output they did not ask for and no sign of it.
    if args.listing and args.ref is not None:
        print(
            "subsystem-recall: --list and --ref select different things (the whole "
            "index vs one entry's body). Pass one.",
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

    # `--limit` is what selects the pre-digest full-body mode. Nothing else does:
    # a default of DEFAULT_ENTRY_LIMIT here would make "the caller asked for a cap"
    # indistinguishable from "the caller asked for nothing".
    mode = "list" if args.listing else ("full" if args.limit is not None else DEFAULT_MODE)
    limit = args.limit if args.limit is not None else DEFAULT_ENTRY_LIMIT

    try:
        repo = Path(args.repo).resolve()
        scope = args.scope if args.scope is not None else scope_for_repo(repo)
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
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
