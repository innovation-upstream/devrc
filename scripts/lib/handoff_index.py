#!/usr/bin/env python3
"""Derive a SECTION-GRAINED full-text index of the handoff-doc corpus, and write it.

P1 of "handoff-doc full-text search". There is no server-side query over handoff
doc BODIES today, and that is the gap this closes:

  * `scripts/initiatives/sync.py` stores DERIVED metadata only — `summary` is one
    parsed line, `current_doc` is a PATH, and `search_text` is session prompts.
    Not one byte of a doc's body reaches Postgres.
  * `scripts/initiatives/viewer.py` live-reads bodies off disk (`read_doc_detail_live`,
    512 KB cap) and its search is CLIENT-SIDE JS over what the page already shipped.

So the corpus is queryable only by a human who already knows which doc to open,
and `/resume` and subagents re-derive findings that are written down.

⚠ CORPUS SIZE, AT THE SCOPE EACH FIGURE WAS ACTUALLY MEASURED, AND IT MOVES.
Re-measured HERE by running `--dry-run` with all four `$DEVRC`/`$HOMELAB`/
`$DATAPACKET`/`$CIVITAI` handles set:

    2026-09-01  devrc 96 / 992 · homelab-talos 54 / 522 ·
                datapacket-talos 288 / 2450 · civitai 1 / 14
                TOTAL 439 docs / 3978 sections

🔴 EVERY ONE OF THESE NUMBERS IS STALE THE DAY AFTER IT IS WRITTEN, and this
docstring has now carried three generations of them: devrc read 94/968 earlier the
SAME day and 959 sections before that, moving because `main` moved and for no
other reason. Quote them as a SCALE, never as a value to check against, and re-run
`--dry-run` if you need the number. (The brief that commissioned this work carried
a second-hand `~424 docs / 8.6 MB` over the same four repos. That is now
first-hand and slightly low — but the same caveat applies to the replacement.)

WHAT THIS MODULE IS NOT
-----------------------
🔴 NOT A SYSTEM OF RECORD. The index is DERIVED and DISPOSABLE; git is the record.
`--rebuild` deletes its in-scope rows and re-derives them from scratch, and that
must always remain a safe operation. Nothing may ever be stored here that cannot be re-derived from a
git ref, and no consumer may treat a row as authoritative over the doc it came
from.


WHY THE CORPUS COMES FROM GIT REFS AND NOT THE WORKING TREE
-----------------------------------------------------------
🔴 THIS DOCSTRING IS THE ONE PLACE THE WORKTREE COUNT IS STATED. It was carried in
three places at once (here, `scripts/README.md`, `nix/home.nix`) and two of them
disagreed with the third inside a single PR — nothing pins the number, so a copy
of it is a claim that rots silently. The other two now point here and quote none.

Measured 2026-09-01: `git worktree list` in devrc alone returns **148** entries
(the base clone plus 147 worktrees). A working-tree scan therefore indexes: a doc
as it exists mid-edit in somebody's branch, the SAME doc N times over N worktrees,
and stale orphan copies whose content matches an older commit. None of those is
reproducible, and two runs an hour apart would disagree for reasons that have
nothing to do with what anyone wrote. (148 is devrc's count only, and it moves
every time an agent dispatches — the box-wide figure is larger and is not measured
here. The argument does not depend on which number it is, which is exactly why no
consumer should re-state it.)

So the source is `git ls-tree` + `git show` against each repo's own MAINLINE ref,
and the mainline is DERIVED via `scripts/lib/git_mainline.resolve_base_ref` — the
module that exists because a hardcoded ladder of `main`/`master` returned
`no-base-ref` on the first repo whose mainline was `trunk`. Never hardcode it here.

🔴 AN UNTRACKED DOC IS REPORTED, NOT INDEXED, and the distinction is the point.
A handoff doc that exists on disk and NOT in the mainline ref is a DURABILITY
HOLE: it is one `git checkout` by a concurrent session away from silent deletion
(`claude/RULES.md` → "Docs/notes written into a working tree are UNSAVED WORK").
Indexing it would make the search surface hide the hole by answering from it.
Reporting it makes the hole actionable. One exists today, measured 2026-09-01.


WHY SECTIONS AND NOT DOCS
-------------------------
The highest-value content in this corpus is a "Ruled out" / "RETRACTED" paragraph
inside an `## Open investigations` block — the thing that stops a future session
re-deriving a dead end. At DOC granularity that paragraph is invisible: it is a
few hundred bytes inside a 40 KB document, `ts_rank` drowns it, and the answer a
caller gets back is "this 40 KB doc mentions your term somewhere".

One row per SECTION, with `## Open investigations` split per `### ` sub-block and
`## Next steps` split per ranked item, makes the retrieval unit the same size as
the finding.


THE PARSER IS BORROWED, NOT REIMPLEMENTED
-----------------------------------------
Every heading/fence/item rule comes from `scripts/lib/handoff_doc.py`, which is
the WRITER of these documents and therefore the executable authority on their
shape. It already owns:

    split_front_matter   the CLOSED-`---`-block-at-line-1 rule
    split_sections       the fence-aware H2 walk
    canonical_prefix     "is this heading canonical", the ONE owner
    _item_blocks         a ranked item is a BLOCK, not a line (measured: 179 of
                         257 devrc items wrap onto continuation lines)
    _FORCING             the forcing-tag grammar, incl. its markup class
    _unfenced            visible-line iteration

A second copy of any of those would be a parser free to drift from the one
measured against the real corpus — and `handoff_doc`'s docstrings record three
separate occasions where exactly that happened. Two private names are imported
(`_item_blocks`, `_FORCING`, `_unfenced`); that is deliberate and is cheaper than
re-spelling them. `test_handoff_index.py` pins this module's per-item
`(rank, forcing_kind)` sequence against `handoff_doc.ranked_items` so the two can
never disagree about one document.

⚠ `scripts/handoff-audit.py` (merged as `f71ff648`, on `main`) reads the SAME
corpus and does NOT
fit here: it globs `claudedocs/handoff-*.md` off DISK, which is the working-tree
source this module exists to avoid, and its parser is `skill-audit.py`'s
byte/heading walk aimed at BUDGET measurement rather than at retrieval units. The
overlap is the corpus, not the reader. Nothing is duplicated: it borrows
skill-audit's walk, this borrows handoff_doc's.


CONTRACT SUMMARY
----------------
    resolve_mainline(repo)                    -> (ref | None, ladder)
    identity_collisions(rows)                 -> tuple[str, ...]        PURE
    unset_repo_handles(env=None)              -> tuple[str, ...]
    prune_config_refusal(unset)               -> str | None             PURE
    incomplete_kind(derivation)               -> str | None             PURE
    incomplete_reason(derivation)             -> str | None             PURE
    may_replace_stored_rows(derivation)       -> bool                   PURE
    authority_label_collisions(derivations)   -> tuple[str, ...]        PURE
    rebuild_downgrade_reason(derivations)     -> str | None             PURE
    rebuild_refusal(derivations, rows, ..)    -> str | None             PURE
    partial_scope_warnings(derivations)       -> tuple[str, ...]        PURE
    rebuild_delete_labels(derivs, stored, ..) -> tuple[str, ...]        PURE
    orphan_labels(derivs, stored, ..)         -> tuple[str, ...]        PURE
    orphan_label_warning(labels)              -> tuple[str, ...]        PURE
    rebuild_plan_lines(derivs, ..)            -> tuple[str, ...]        PURE
    handoff_paths_in_ref(repo, ref)           -> tuple[str, ...] | None
    doc_text_at_ref(repo, ref, path)          -> str | None
    handoff_paths_on_disk(repo)               -> DiskScan
    untracked_docs(on_disk, tracked)          -> tuple[str, ...]        PURE
    untracked_warnings(repo_label, paths)     -> tuple[str, ...]        PURE
    unscannable_warnings(repo_label, scan)    -> tuple[str, ...]        PURE
    derivation_warnings(derivations)          -> tuple[str, ...]        PURE
    all_clear_blockers(derivations)           -> tuple[str, ...]        PURE
    derivation_json(derivations)              -> dict                   PURE
    slug_for(doc_path)                        -> str                    PURE
    doc_date_for(doc_path, text)              -> str | None             PURE
    fold_forcing_kind(raw)                    -> str | None             PURE
    front_matter_fields(text)                 -> dict[str, str]         PURE
    clawgate_task_for(text)                   -> str | None             PURE
    sections_for_doc(repo, slug, path, ...)   -> tuple[Section, ...]    PURE
    derive_repo(repo, ...)                    -> RepoDerivation
    MemorySectionStore / PostgresSectionStore -> the injectable seam
    main(argv)                                -> int
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import contextlib
import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Mapping, Protocol, Sequence

_LIB = Path(__file__).resolve().parent
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import handoff_doc  # noqa: E402
from git_mainline import resolve_base_ref  # noqa: E402

__all__ = [
    "SECTIONS",
    "PREFIX_SECTION",
    "SECTION_BOOST",
    "DEFAULT_BOOST",
    "HANDOFF_GLOB",
    "HANDOFF_DIR",
    "TABLE",
    "TABLES_DDL",
    "SCHEMA_LOCK_KEY",
    "REPO_ENV_HANDLES",
    "Section",
    "IndexStats",
    "Hit",
    "RepoDerivation",
    "DiskScan",
    "SectionStore",
    "MemorySectionStore",
    "PostgresSectionStore",
    "resolve_mainline",
    "handoff_paths_in_ref",
    "doc_text_at_ref",
    "handoff_paths_on_disk",
    "untracked_docs",
    "untracked_warnings",
    "unscannable_warnings",
    "identity_collisions",
    "unset_repo_handles",
    "prune_config_refusal",
    "INCOMPLETE_KINDS",
    "INCOMPLETE_UNMEASURED",
    "INCOMPLETE_UNREADABLE",
    "incomplete_kind",
    "incomplete_reason",
    "may_replace_stored_rows",
    "authority_label_collisions",
    "rebuild_downgrade_reason",
    "rebuild_refusal",
    "partial_scope_warnings",
    "rebuild_delete_labels",
    "orphan_labels",
    "orphan_label_warning",
    "rebuild_plan_lines",
    "derivation_warnings",
    "all_clear_blockers",
    "derivation_json",
    "render_derivation",
    "RC_OK",
    "RC_USAGE",
    "RC_REFUSED",
    "RC_COLLISION",
    "slug_for",
    "doc_date_for",
    "fold_forcing_kind",
    "front_matter_fields",
    "clawgate_task_for",
    "sections_for_doc",
    "derive_repo",
    "default_repos",
    "main",
]

# --------------------------------------------------------------------------- #
# The section vocabulary
# --------------------------------------------------------------------------- #

#: The CLOSED set of section tokens a row may carry. Closed on purpose: the query
#: CLI's `--section` filter and the rank boost both switch on it, and an open
#: vocabulary would let a renamed heading mint a token no consumer boosts, filters
#: or knows how to ask for — a row that exists and is unreachable.
SECTIONS: tuple[str, ...] = (
    "goal",
    "state",
    "investigation",
    "next_step",
    "gotcha",
    "verify",
)

#: `handoff_doc.CANONICAL_HEADING_PREFIXES` -> this module's section token.
#:
#: 🔴 PINNED TWO-WAY by `test_every_canonical_prefix_has_a_section`: a prefix with
#: no mapping fails, and a mapping naming no prefix fails. That is what makes a
#: future heading added to `handoff_doc` a RED TEST rather than a silently
#: unindexed section — the failure direction that costs nothing to notice and
#: everything to miss.
#:
#: Two mappings are judgements and are written down as such:
#:   `findings`      -> investigation. It is an APPEND_PREFIXES sibling of
#:                      `open investigations` in handoff_doc — both are diagnosis
#:                      state that accumulates — so it belongs with the bucket the
#:                      rank boost favours, not in its own token.
#:   `what shipped`  -> state. It is a statement of what currently exists, i.e.
#:                      the same question `state now` answers, one tense back.
PREFIX_SECTION: Mapping[str, str] = {
    "goal": "goal",
    "state now": "state",
    "what shipped": "state",
    "next steps": "next_step",
    "how to verify": "verify",
    "open investigations": "investigation",
    "findings": "investigation",
    "gotchas": "gotcha",
}

#: Rank multipliers, applied in BOTH backends from this ONE table — the SQL's
#: CASE expression is BUILT from it (`_boost_case`), never typed a second time.
#: `claude/RULES.md`: a predicate duplicated across call sites is wrong at N−1 of
#: them, and two rankers disagreeing about which section matters is invisible
#: until somebody compares two runs.
#:
#: WHY THESE TWO: `investigation` and `gotcha` are the re-discovery-prevention
#: content — a ruled-out theory and a recorded dead end are the only sections
#: whose value is that a future session does NOT repeat the work. `goal`/`state`
#: describe a situation that has almost certainly moved on; `verify` is a recipe
#: the reader can re-derive. The numbers are a display preference and not a
#: measurement, which is why they live in one named table a reader can argue with.
SECTION_BOOST: Mapping[str, float] = {
    "investigation": 2.0,
    "gotcha": 1.75,
}
DEFAULT_BOOST = 1.0

#: Where a handoff doc lives, and what one is called. `/handoff` writes
#: `claudedocs/handoff-<topic>.md` in every repo (`scripts/lib/handoff_doc.py`).
HANDOFF_DIR = "claudedocs"
HANDOFF_GLOB = "handoff-*.md"
_HANDOFF_NAME = re.compile(r"\Ahandoff-.+\.md\Z")

#: Env handles pre-exported in `.zshenv` (devrc CLAUDE.md → "Shell environment").
#: 🔴 NAMES, NOT PATHS. This repo is PUBLIC and a real client checkout path is
#: exactly the kind of thing that must never be committed; a handle that is unset
#: on a host simply contributes no repo, which is also how a laptop without a
#: checkout is meant to behave.
REPO_ENV_HANDLES: tuple[str, ...] = ("DEVRC", "HOMELAB", "DATAPACKET", "CIVITAI")

# --------------------------------------------------------------------------- #
# Schema — idempotent, additive-only DDL, in sync.py's shape
# --------------------------------------------------------------------------- #

TABLE = "initiatives.handoff_section"

#: Same advisory-lock discipline as `sync.py::ensure_schema` — a manual run racing
#: the timer must not collide on the not-fully-race-safe `CREATE … IF NOT EXISTS`.
#: A DIFFERENT key from sync.py's: these are different objects and serialising them
#: against each other would make the timer and this block each other for no reason.
SCHEMA_LOCK_KEY = 0x48_41_4E_44  # "HAND"

#: 🔴 `tsv` IS A GENERATED COLUMN, not a trigger and not a write-time computation.
#: The alternative — computing the tsvector in Python at insert — puts the text
#: search configuration in TWO places (the writer and `plainto_tsquery` at read
#: time) and they drift the first time either moves. Generated-always means the
#: database is the only thing that decides what a row's search vector is.
TABLES_DDL = f"""
CREATE SCHEMA IF NOT EXISTS initiatives;

CREATE TABLE IF NOT EXISTS {TABLE} (
    id            bigserial PRIMARY KEY,
    repo          text NOT NULL,
    slug          text NOT NULL,
    doc_path      text NOT NULL,
    doc_date      date,
    commit_sha    text,
    section       text NOT NULL,
    ordinal       int  NOT NULL,
    heading       text,
    body          text,
    forcing_kind  text,
    clawgate_task text,
    tsv tsvector GENERATED ALWAYS AS (
        to_tsvector('english', coalesce(heading, '') || ' ' || coalesce(body, ''))
    ) STORED
);

CREATE INDEX IF NOT EXISTS handoff_section_tsv_idx
    ON {TABLE} USING GIN (tsv);

CREATE UNIQUE INDEX IF NOT EXISTS handoff_section_identity_idx
    ON {TABLE} (repo, slug, section, ordinal);

CREATE INDEX IF NOT EXISTS handoff_section_repo_idx
    ON {TABLE} (repo, doc_date DESC);
"""


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Section:
    """One indexable unit: a whole H2 section, one `### ` sub-block, or one
    ranked item. `ordinal` is unique within `(repo, slug, section)` — assigned
    sequentially per token across the WHOLE document, so a doc carrying two
    `## Gotchas` headings does not collide on the table's UNIQUE index."""

    repo: str
    slug: str
    doc_path: str
    doc_date: str | None
    commit_sha: str | None
    section: str
    ordinal: int
    heading: str
    body: str
    forcing_kind: str | None = None
    clawgate_task: str | None = None

    def as_row(self) -> dict:
        return {
            "repo": self.repo,
            "slug": self.slug,
            "doc_path": self.doc_path,
            "doc_date": self.doc_date,
            "commit_sha": self.commit_sha,
            "section": self.section,
            "ordinal": self.ordinal,
            "heading": self.heading,
            "body": self.body,
            "forcing_kind": self.forcing_kind,
            "clawgate_task": self.clawgate_task,
        }


@dataclass(frozen=True)
class IndexStats:
    """🔴 THE SILENT-ZERO GUARD'S RAW MATERIAL. Every query response carries these
    two numbers, because a zero-result query is TWO different facts — "the corpus
    does not say that" and "nothing was ever indexed" — and they have opposite
    next actions. `claude/RULES.md`: an empty result cannot distinguish two
    mechanisms."""

    indexed_docs: int = 0
    indexed_sections: int = 0


@dataclass(frozen=True)
class Hit:
    repo: str
    slug: str
    doc_path: str
    doc_date: str | None
    section: str
    ordinal: int
    heading: str
    body: str
    rank: float


@dataclass(frozen=True)
class DiskScan:
    """What the WORKING-TREE half of the durability comparison actually saw.

    🔴 IT EXISTS BECAUSE `()` MEANT THREE DIFFERENT THINGS. `handoff_paths_on_disk`
    used to return a bare tuple, so "the directory is absent", "the directory is
    unreadable" and "the directory is readable and holds no handoff docs" were one
    value — and `render_derivation` printed the literal all-clear "every handoff
    doc on disk is also in it" for all three. MEASURED: with a real untracked doc
    present, `chmod 0o000 claudedocs/` turned a `🔴 DURABILITY HOLE` into that
    all-clear, with the doc still sitting there uncommitted.

    🔴 AND THE MECHANISM WAS NOT THE ONE THE OLD CODE GUARDED. The old body wrapped
    `Path.rglob` in `except OSError: return ()` — an UNREACHABLE clause for this
    case, because `rglob` walks via `os.scandir` and SWALLOWS a per-directory
    `PermissionError` internally. MEASURED on CPython 3.12.14: `rglob` over a
    `chmod 0o000` directory returns `[]` and raises nothing. So the walk is now
    `os.walk(..., onerror=…)`, which is the only form that HANDS the error back,
    and the errors are recorded rather than swallowed.

    `scanned` is "the walk was attempted and reached its end"; `errors` is every
    directory it could not read. The all-clear requires BOTH, plus at least one
    path actually seen — a comparison whose disk side is empty compared nothing.
    The default is `scanned=False`: an unset field must never be able to license
    an all-clear."""

    paths: tuple[str, ...] = ()
    scanned: bool = False
    #: `HANDOFF_DIR` does not exist. A MEASURED absence (`git_mainline`'s
    #: distinction), not a failure — an ordinary state for a worktree sitting on a
    #: branch that predates the directory.
    absent: bool = False
    errors: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """The walk ran AND hid nothing. The one predicate the all-clear reads."""
        return self.scanned and not self.errors


@dataclass
class RepoDerivation:
    """What one repo contributed, and what could NOT be measured about it.

    🔴 `unmeasured` IS A STRUCTURAL FLAG, NOT A GREP OVER `warnings`. The refusal
    guard (`rebuild_refusal`) has to answer "did any repo fail to measure" before
    it lets a destructive rebuild run, and answering that by searching the warning
    strings for the word `UNMEASURED` would be exactly the spelled-not-structural
    guard `claude/RULES.md` forbids: a reworded warning walks past it while the
    hazard is unchanged. It carries the REASON token rather than a bool so the
    refusal message can name what went wrong without re-deriving it.

    🔴 `disk` IS THE SAME KIND OF FIELD, FOR THE OTHER HALF OF THE REPORT. The
    all-clear asserts something about the DISK side and used to be gated on the
    REF-side document count (`elif total_docs:`) — two different measurements, one
    of which cannot see the other's failure. See `DiskScan`.

    🔴 `unreadable` IS THE THIRD FIELD OF THAT KIND, AND IT EXISTS BECAUSE `docs`
    ALONE CANNOT TELL "THE REF HOLDS NONE" FROM "GIT COULD NOT PRODUCE ANY". Both
    render as `docs == 0` with `unmeasured is None`, and downstream that zero was
    reported as the FIRST fact: `handoff_search`'s rc 7 said the repo "resolved a
    mainline ref and hold no `claudedocs/handoff-*.md` in it" for a repo that
    holds one and whose blob had gone missing from the object store. Same
    conflation as `handoff_paths_in_ref`'s `None`-vs-`()`, one function later.
    Carried as the PATHS, not a count, so the report can name them."""

    repo: str
    label: str
    ref: str | None = None
    ladder: tuple[str, ...] = ()
    sections: list[Section] = field(default_factory=list)
    docs: int = 0
    untracked: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)
    unmeasured: str | None = None
    disk: DiskScan = field(default_factory=DiskScan)


# --------------------------------------------------------------------------- #
# Git — the corpus source
# --------------------------------------------------------------------------- #


def _git(repo: str | Path, args: Sequence[str]) -> str | None:
    """stdout, or None if git refused. Never raises, never takes the index lock.

    `GIT_OPTIONAL_LOCKS=0` for `git_mainline._git`'s reason: a concurrent agent in
    the same checkout is the normal case in these repos, and a helper that can
    block someone else's commit is not read-only in the way that matters.

    🔴 THE DECODE IS PINNED TO UTF-8 WITH `errors="replace"`, AND THAT IS A
    BLAST-RADIUS DECISION, NOT A STYLE ONE. A bare `text=True` decodes with the
    process's locale and RAISES `UnicodeDecodeError` on the first undecodable
    byte — and this helper is called once per document, inside a loop over every
    repo. So ONE committed doc carrying a stray `\\xff` (a pasted terminal capture,
    a latin-1 quote, a truncated multibyte sequence) killed the whole process and
    every OTHER repo's rows with it, from a `raise` no caller was catching. The
    unit sets no `LANG`/`LC_ALL`, so the locale is whatever systemd hands it.

    `replace` rather than `ignore`: a U+FFFD in a body is visible in a search
    result and tells a reader the source is malformed, whereas silently dropping
    the byte produces a plausible-looking string that is quietly not the document.

    🔴 AND THERE IS DELIBERATELY NO `UnicodeDecodeError` IN THE `except`. Adding
    one looks like belt-and-braces and is not: with `errors="replace"` the decode
    cannot raise, so the clause is UNREACHABLE — a guard that can never execute,
    which `claude/RULES.md` says to prove reachable or not write. It was written,
    and a mutation sweep confirmed it: deleting the name from the `except` tuple
    left the WHOLE suite green — including the malformed-doc test written for this
    very bug — because nothing can reach it. The fix is the decode PARAMETERS. An
    `except`
    naming an exception the code cannot produce reads as coverage and provides
    none, which is the failure mode that stops the next person looking."""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def resolve_mainline(repo: str | Path) -> tuple[str | None, tuple[str, ...]]:
    """`(the repo's mainline ref, the ladder that was tried)`.

    A thin, NAMED pass-through to `git_mainline.resolve_base_ref` so that the one
    place this module decides "which ref is the corpus" is greppable — and so a
    reader looking for a hardcoded `origin/main` finds this docstring instead of
    one. `None` is a MEASURED absence: every rung was checked and none exists."""
    return resolve_base_ref(repo)


def handoff_paths_in_ref(repo: str | Path, ref: str) -> tuple[str, ...] | None:
    """`claudedocs/handoff-*.md` paths present in `ref`, sorted — or `None`.

    🔴 `None` IS "GIT FAILED", `()` IS "THE REF GENUINELY HOLDS NONE", AND THEY
    USED TO BE THE SAME VALUE. `_git` returns `None` on a non-zero rc, and this
    function folded that into an empty tuple — so a `ls-tree` that failed (a
    corrupt or partially-fetched object store, a ref that vanished between
    `resolve_mainline` and here, a repo whose `.git` became unreadable mid-run)
    was indistinguishable from a mainline that simply carries no handoff docs.
    The repo then landed in `derive_repo`'s MEASURED set with `unmeasured=None,
    docs=0` and no warning at all.

    That was survivable while the distinction only affected a count. It is not
    survivable now that `rebuild_delete_labels` reads the same MEASURED set to
    decide what a `--rebuild` may DELETE: a failed `ls-tree` would make the run
    authoritative over that repo, delete its rows, re-insert none, and exit 0.
    `claude/RULES.md` → "an EMPTY RESULT cannot distinguish two mechanisms";
    here the two mechanisms have opposite correct actions, so the caller is
    given the fact rather than a shared observable. `derive_repo` turns the
    `None` into a structural `unmeasured` reason, which is what keeps the rows.

    🔴 THE FILTER IS APPLIED IN PYTHON, NOT AS A GIT PATHSPEC. `git ls-tree`
    pathspecs are matched by git's own glob rules and a `*` there does not mean
    what a shell `*` means for a path containing a slash; enumerating the
    directory and matching the BASENAME is the version whose semantics this file
    can state. `-z` because a path may contain anything but NUL."""
    out = _git(repo, ["ls-tree", "-r", "--name-only", "-z", ref, "--", HANDOFF_DIR])
    if out is None:
        return None
    names = [p for p in out.split("\0") if p]
    return tuple(sorted(p for p in names if _HANDOFF_NAME.match(Path(p).name)))


def ref_commit_sha(repo: str | Path, ref: str) -> str | None:
    """The commit `ref` resolves to — the ONE fact that makes a derivation
    reproducible, recorded on every row it produced.

    ⚠ IT IS THE REF TIP, NOT THE DOC'S LAST-TOUCHING COMMIT, and the difference is
    worth stating because the column name invites the other reading. Per-doc
    attribution would cost one `git log -1` per document (424 subprocesses on the
    measured corpus) to answer a question no consumer asks. What a caller needs is
    "which tree was this index built from", so that is what is stored."""
    out = _git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return out.strip() if out and out.strip() else None


def doc_text_at_ref(repo: str | Path, ref: str, path: str) -> str | None:
    """The doc's content AS OF `ref`. `None` when git could not produce it —
    never an empty string, which is a legitimate (if odd) document."""
    return _git(repo, ["show", f"{ref}:{path}"])


def handoff_paths_on_disk(repo: str | Path) -> DiskScan:
    """The WORKING-TREE half of the durability comparison, AND whether it ran.

    Read ONLY to compute the untracked-doc report. Nothing derived from this
    function is ever indexed — see `untracked_docs`.

    🔴 RECURSIVE, AND THE RECURSION IS THE WHOLE POINT. This function is one HALF
    of a set difference whose other half is `handoff_paths_in_ref`, which runs
    `git ls-tree -r` — RECURSIVE. A non-recursive disk scan made the two halves
    walk different shapes: a doc at `claudedocs/sub/handoff-x.md` on disk and
    absent from the ref produced `untracked=()`, and `render_derivation` printed
    the literal all-clear "every handoff doc on disk is also in it". That is the
    failure mode `claude/RULES.md` calls worse than no check — reporting COVERAGE
    it does not provide, so nobody looks again. The two sides of a difference must
    walk the same tree or the difference is meaningless.

    🔴 AND THE MATCHER IS `_HANDOFF_NAME`, THE SAME ONE THE REF SIDE USES, not the
    `HANDOFF_GLOB` shell pattern. Same argument, one level down: two spellings of
    "is this a handoff doc" over the two halves of one difference is the duplicated
    predicate that is wrong at N−1 sites.

    🔴 `os.walk(onerror=…)`, NOT `Path.rglob`, AND THAT IS THE FIX FOR THE MEASURED
    DEFECT — see `DiskScan`. `rglob` swallows a per-directory `PermissionError`
    inside `os.scandir` and returns `[]`, so an UNREADABLE `claudedocs/` was
    indistinguishable from a clean one and the old `except OSError: return ()`
    around it could never fire. `os.walk` is the form that hands the error to a
    callback, which is what lets an unreadable directory be REPORTED instead of
    read as a finding of nothing."""
    root = Path(repo)
    base = root / HANDOFF_DIR
    if not base.exists():
        # A measured absence. `os.walk` would report it through `onerror` as an
        # ENOENT, which reads as a failure — it is not one, and folding it in
        # would make every repo without the directory look unscannable.
        return DiskScan(paths=(), scanned=True, absent=True, errors=())

    errors: list[str] = []

    def _onerror(exc: OSError) -> None:
        errors.append(f"{getattr(exc, 'filename', None) or base}: {exc.strerror or exc}")

    out: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(base, onerror=_onerror):
        for name in filenames:
            if not _HANDOFF_NAME.match(name):
                continue
            p = Path(dirpath) / name
            if not p.is_file():
                continue
            try:
                out.append(p.relative_to(root).as_posix())
            except ValueError:  # pragma: no cover - the walk is rooted at repo
                continue
    return DiskScan(
        paths=tuple(sorted(out)),
        scanned=True,
        absent=False,
        errors=tuple(errors),
    )


def unscannable_warnings(repo_label: str, scan: DiskScan) -> tuple[str, ...]:
    """The warning lines for a disk scan that could not read everything. PURE.

    Empty when the walk was clean — a per-repo "0 unreadable directories" would
    bury the real ones, exactly as `untracked_warnings` argues for its own zero."""
    if scan.complete:
        return ()
    if not scan.scanned:  # pragma: no cover - no caller can produce this today
        return (
            f"⚠ UNSCANNED DISK — {repo_label}: the working-tree walk never ran, so "
            f"the on-disk-vs-ref comparison has NO disk side. Untracked handoff "
            f"docs, if any, are NOT reported.",
        )
    n = len(scan.errors)
    return (
        f"⚠ UNSCANNABLE DISK — {repo_label}: {n} director{'y' if n == 1 else 'ies'} "
        f"under {HANDOFF_DIR}/ could not be read, so any handoff doc inside "
        f"{'it' if n == 1 else 'them'} is INVISIBLE to the durability check — an "
        f"uncommitted doc there is NOT reported and this run cannot say the repo "
        f"is clean. Fix the permissions and re-run.",
        *(f"    {e}" for e in scan.errors),
    )


def untracked_docs(on_disk: Iterable[str], tracked: Iterable[str]) -> tuple[str, ...]:
    """Docs present on disk and ABSENT from the mainline ref. PURE.

    🔴 A DURABILITY HOLE, reported and NOT indexed. Such a doc is one routine
    `git checkout` / `stash` / deploy by a concurrent session away from silent,
    unreported deletion — `claude/RULES.md` → "Docs/notes written into a working
    tree are UNSAVED WORK". Indexing it would make the search surface answer FROM
    the hole, which conceals it: a caller gets their answer and nobody ever learns
    the doc is not committed.

    Set difference in ONE direction only. The other direction — in the ref, not on
    disk — is ordinary and not a finding: a working tree may sit on any branch, or
    be a worktree of a subdirectory, and a doc the mainline carries is by
    definition already durable."""
    have = set(tracked)
    return tuple(sorted(p for p in set(on_disk) if p not in have))


def untracked_warnings(repo_label: str, paths: Sequence[str]) -> tuple[str, ...]:
    """The warning lines for `paths`. PURE, so the wording is testable without git.

    Empty when there is nothing to report — a clean repo prints no line at all,
    because a per-repo "0 untracked" would bury the real ones."""
    if not paths:
        return ()
    n = len(paths)
    head = (
        f"🔴 DURABILITY HOLE — {n} handoff doc{'' if n == 1 else 's'} in {repo_label} "
        f"{'is' if n == 1 else 'are'} on DISK and NOT in the mainline ref, so "
        f"{'it is' if n == 1 else 'they are'} NOT INDEXED and one `git checkout` "
        f"from gone. Commit {'it' if n == 1 else 'them'} or open a PR."
    )
    return (head, *(f"    {p}" for p in paths))


# --------------------------------------------------------------------------- #
# Per-doc derivation — PURE from here down
# --------------------------------------------------------------------------- #


def slug_for(doc_path: str) -> str:
    """`claudedocs/handoff-limewire-comps.md` -> `limewire-comps`;
    `claudedocs/sub/handoff-limewire-comps.md` -> `sub/limewire-comps`.

    The identity half of the table's UNIQUE index, together with `repo`. Derived
    from the PATH rather than from an H1, because the path is what `/handoff`
    controls and an H1 is prose an updating session rewrites.

    🔴 THE PATH, NOT THE BASENAME, AND THE DIFFERENCE IS A SILENT OVERWRITE.
    A basename-only slug makes `claudedocs/handoff-a.md` and
    `claudedocs/sub/handoff-a.md` ONE identity, so the table's
    `ON CONFLICT (repo, slug, section, ordinal) DO UPDATE` overwrites the first
    doc's rows with the second's — no error, no warning, and `render_derivation`
    goes on reporting `docs=2` while `stats()` reports `indexed_docs=1`. Two
    numbers disagreeing with nothing comparing them is precisely a silent zero
    wearing a different hat. `handoff_paths_in_ref` is recursive (`ls-tree -r`),
    so a nested doc is not hypothetical — it is a path the collector already
    returns.

    The `claudedocs/` prefix is stripped so the common case is UNCHANGED: every
    doc directly under `HANDOFF_DIR` keeps the bare slug it has always had, and
    only a nested one grows a directory component. That keeps the ~1,500 existing
    rows' identities stable across this change while making a collision
    impossible — and `identity_collisions` is the belt to this braces, because a
    unique slug does not rule out two REPOS resolving to one label."""
    parts = list(PurePosixPath(doc_path).parts)
    if parts[:1] == [HANDOFF_DIR]:
        parts = parts[1:]
    if not parts:
        return ""
    name = parts[-1]
    if name.endswith(".md"):
        name = name[: -len(".md")]
    if name.startswith("handoff-"):
        name = name[len("handoff-") :]
    return "/".join([*parts[:-1], name])


def identity_collisions(rows: Sequence["Section"]) -> tuple[str, ...]:
    """Rows sharing the table's UNIQUE `(repo, slug, section, ordinal)`. PURE.

    🔴 THE DETECTOR FOR "THE WRITE SILENTLY LOST A DOCUMENT". `upsert`'s
    `ON CONFLICT DO UPDATE` cannot raise on a duplicate identity — that is what
    the clause is FOR — so a derivation that produces two rows with one identity
    writes one row and reports success. This is the only thing between that and
    a caller reading `docs=N` beside `indexed_docs=N-1` and noticing neither.

    Reports the DOC PATHS behind each clash, not just the identity, because the
    identity alone ("relayrepo/a goal#0 appears twice") does not tell you which
    two files to go look at. Empty tuple when the derivation is clean — a
    per-run "0 collisions" would bury the real ones, exactly as with
    `untracked_warnings`.

    🔴 IT COUNTS OCCURRENCES, NOT DISTINCT PATHS, and the difference is a whole
    second failure mode. De-duplicating by path made the detector blind to the
    case that a unique slug cannot fix: two repos whose directory basenames
    COLLIDE resolve to one `label` (`default_repos` uses `Path(raw).name`), so
    `~/a/proj` and `~/b/proj` both index as `proj` and their identically-named
    docs share every identity — with identical `doc_path`s, which a
    path-de-duplicating check reads as one document. N rows claiming one identity
    means N−1 of them are overwritten, whatever they came from."""
    seen: dict[tuple[str, str, str, int], list[str]] = {}
    for r in rows:
        seen.setdefault((r.repo, r.slug, r.section, r.ordinal), []).append(r.doc_path)
    out: list[str] = []
    for key, paths in sorted(seen.items()):
        if len(paths) > 1:
            repo, slug, section, ordinal = key
            out.append(
                f"🔴 IDENTITY COLLISION — {repo}/{slug} [{section}#{ordinal}] is claimed "
                f"by {len(paths)} rows; ON CONFLICT DO UPDATE would keep only the last "
                f"and report success. Sources: " + ", ".join(sorted(paths))
            )
    return tuple(out)


#: An ISO date anywhere in the doc's H1/preamble. `handoff_doc._TOPIC_DATE` also
#: admits a compact `YYYYMMDD`; this wants a value a `date` column can take, so
#: only the hyphenated form is accepted and the compact one falls through to the
#: filename rule below.
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def doc_date_for(doc_path: str, text: str) -> str | None:
    """The doc's topic date as `YYYY-MM-DD`, or None. A REAL calendar date.

    Order — PREAMBLE FIRST, filename second. `/handoff`'s template writes
    `# Handoff: <topic> — <date>`, so the preamble is the authored statement;
    a date in the filename is a convention only some docs follow.

    🔴 THE SHAPE IS NOT THE VALIDATION, AND DELEGATING TO THE `date` COLUMN WAS
    WRONG. `_ISO_DATE` is `\\d{4}-\\d{2}-\\d{2}`, which admits `2026-99-99` and
    `1234-56-78`; the previous docstring argued Postgres would reject those "at
    write time rather than this function guessing". It would — and that is the
    problem, not the safety net. The rejection lands INSIDE the write
    transaction, after `--rebuild` has deleted, so one typo in one preamble
    emptied the whole index. It is also DETERMINISTIC: the same doc fails the same
    way every 6h, so the index stays empty until a human reads the unit's journal.
    A malformed date is a display field being wrong; it must never be able to take
    the corpus down with it.

    So an unparseable date is treated as ABSENT, not fatal, and the scan CONTINUES
    to the next candidate — `date.fromisoformat` on the match, then the next match
    in the same source, then the filename. `2026-99-99` in a preamble followed by
    a real date still yields the real one, and a doc whose only date is impossible
    sorts with the undated docs (last), which is the fail-safe direction for a
    tiebreak key.

    🔴 THE SEARCH IS BOUNDED TO THE PREAMBLE, never the whole document. Every
    handoff doc quotes dozens of dates in its body (`MEASURED 2026-08-29 …`), and
    a whole-document search would return whichever one happened to come first."""
    _fm, body = handoff_doc.split_front_matter(text)
    preamble, _secs = handoff_doc.split_sections(body)
    for source in (preamble, Path(doc_path).name):
        for m in _ISO_DATE.finditer(source):
            with contextlib.suppress(ValueError):
                return _dt.date.fromisoformat(m.group(0)).isoformat()
    return None


def fold_forcing_kind(raw: object) -> str | None:
    """A declared forcing kind, folded to the CLOSED vocabulary. `None` otherwise.

    🔴 THE CLOSED SET IS `handoff_doc.FORCING_KINDS`, IMPORTED. It is a closed
    vocabulary precisely so that a "false positive" would require prose reading
    `forcing` + punctuation + one of seven words, and re-spelling the seven here
    would let this module drift into accepting a kind the writer rejects — or
    refusing one it accepts.

    An UNRECOGNISED kind folds to `None` rather than being stored raw. That is
    the fail-safe direction for a column consumers filter on: a stored
    `forcing_kind='cleanup'` is a value no query asks for and no reader can
    interpret, and it would make `WHERE forcing_kind IS NULL` — "items declaring
    nothing" — quietly wrong. The near-miss is still fully searchable, because
    the item's own text is the row's `body`."""
    if not isinstance(raw, str):
        return None
    kind = raw.strip().lower()
    return kind if kind in handoff_doc.FORCING_KINDS else None


_FM_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$")


def front_matter_fields(text: str) -> dict[str, str]:
    """`key: value` pairs from the doc's front matter. `{}` when there is none.

    🔴 THE CLOSED-BLOCK RULE IS `handoff_doc.split_front_matter`'S, NOT A SECOND
    ONE. Front matter is `---` on LINE 1 through the next `---` line, and nothing
    else: a `---` later in a markdown document is a horizontal rule, and an
    UNTERMINATED opening `---` is preamble prose. Both readings matter here —
    `clawgate_task` is a durable field a caller reconciles against, so accepting
    it out of a non-front-matter block would attach a task id to a document that
    never declared one.

    Only the delimiters are handoff_doc's; the `key: value` split is this
    module's, because handoff_doc reads exactly one key (`clawgate-task`) and has
    no general field parser to borrow."""
    fm, _rest = handoff_doc.split_front_matter(text)
    if not fm:
        return {}
    out: dict[str, str] = {}
    for line in fm.splitlines():
        if line.strip() in ("---", ""):
            continue
        m = _FM_FIELD.match(line)
        if m:
            out[m.group(1).lower()] = m.group(2)
    return out


def clawgate_task_for(text: str) -> str | None:
    """The doc's `clawgate-task:` front-matter value, or None.

    The KEY is `handoff_doc.CLAWGATE_TASK_KEY`, imported — the same constant
    `scripts/lib/clawgate_handoff.sh` is pinned against, so this is the third
    reader of one spelling rather than a fourth spelling."""
    value = front_matter_fields(text).get(handoff_doc.CLAWGATE_TASK_KEY)
    return value or None


_H3 = re.compile(r"^###\s+(\S.*?)\s*$")


def _subblocks(section_body: str) -> list[tuple[str, str]]:
    """`[(h3 heading text, block text), ...]` for a section body, FENCE AWARE.

    Visible-line membership comes from `handoff_doc._unfenced`, for the reason
    `split_sections` is fence aware: a handoff doc routinely pastes a markdown
    template full of `### ` lines inside a code block, and treating those as
    sub-block boundaries would shred the section.

    Returns `[]` when the body carries no `### ` at all — the caller then indexes
    the section whole, which is the correct unit for a section nobody subdivided."""
    lines = section_body.splitlines()
    visible = {idx for idx, _ln in handoff_doc._unfenced(section_body)}
    starts = [
        (i, m.group(1))
        for i, line in enumerate(lines)
        if i in visible and (m := _H3.match(line))
    ]
    if not starts:
        return []
    out: list[tuple[str, str]] = []
    for n, (start, heading) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(lines)
        out.append((heading, "\n".join(lines[start + 1 : end]).strip()))
    return out


def _next_step_units(section_body: str) -> list[tuple[str, str, str | None]]:
    """`[(rank, item text, forcing kind), ...]` for a `## Next steps` body.

    🔴 THE WALK IS `handoff_doc._item_blocks` AND THE TAG IS `handoff_doc._FORCING`,
    both imported. That pairing is byte-identical to what `handoff_doc.ranked_items`
    does — deliberately, because it is the same question — and
    `test_ranked_item_units_agree_with_handoff_doc` pins the two against each other
    on a multi-item fixture so a change to either is a red test rather than a
    silent divergence between what `/handoff` gates and what search returns.

    Why not simply CALL `ranked_items`: it returns the item's FIRST-LINE text and
    discards the block, and the block is what has to be searchable — 179 of 257
    devrc items wrap onto continuation lines (measured, `_item_blocks` docstring),
    so first-line-only indexing would drop the majority of every item's content."""
    out: list[tuple[str, str, str | None]] = []
    for m, own, _hidden in handoff_doc._item_blocks(section_body):
        block = "\n".join(own)
        found = handoff_doc._FORCING.search(block)
        out.append((m.group(1), block, fold_forcing_kind(found.group(1)) if found else None))
    return out


def sections_for_doc(
    repo: str,
    doc_path: str,
    text: str,
    *,
    commit_sha: str | None = None,
) -> tuple[Section, ...]:
    """Every indexable row for ONE document. PURE — no I/O, no git, no DB.

    A section whose heading is not canonical (`handoff_doc.canonical_prefix`
    returns None) produces NO row. That is a deliberate narrowing rather than a
    gap: the retrieval units this index is built around are the six the skill
    mandates, and a bespoke heading indexed under a token no consumer filters on
    is a row that exists and cannot be asked for. The doc is still fully reachable
    through its canonical sections.

    A document with a MISSING section simply yields fewer rows — there is no
    placeholder. A zero-row document (no canonical heading at all) is legitimate
    and is counted as a doc that contributed nothing, never as a failure."""
    slug = slug_for(doc_path)
    date = doc_date_for(doc_path, text)
    task = clawgate_task_for(text)
    _fm, body = handoff_doc.split_front_matter(text)
    _pre, secs = handoff_doc.split_sections(body)

    # Ordinals are assigned per SECTION TOKEN across the whole document, not per
    # H2 block. That is what keeps `(repo, slug, section, ordinal)` unique when a
    # doc carries two headings that map to one token — `## Gotchas` and
    # `## Gotchas / dead-ends`, or `## State now` beside `## What shipped`.
    counters: dict[str, int] = {}
    rows: list[Section] = []

    def emit(token: str, heading: str, body_text: str, kind: str | None = None) -> None:
        ordinal = counters.get(token, 0)
        counters[token] = ordinal + 1
        rows.append(
            Section(
                repo=repo,
                slug=slug,
                doc_path=doc_path,
                doc_date=date,
                commit_sha=commit_sha,
                section=token,
                ordinal=ordinal,
                heading=heading,
                body=body_text,
                forcing_kind=kind,
                clawgate_task=task,
            )
        )

    for heading_line, section_body in secs:
        heading = handoff_doc.heading_text(heading_line)
        prefix = handoff_doc.canonical_prefix(heading)
        token = PREFIX_SECTION.get(prefix) if prefix else None
        if token is None:
            continue
        if token == "investigation":
            blocks = _subblocks(section_body)
            if blocks:
                for sub_heading, sub_body in blocks:
                    emit(token, sub_heading, sub_body)
                continue
        elif token == "next_step":
            units = _next_step_units(section_body)
            if units:
                for _rank, item_text, kind in units:
                    # The block's own first line ALREADY carries `N. `, so the
                    # rank is not re-prefixed — doing so produced `1. 1. …`,
                    # caught by test_next_steps_split_per_ranked_item_carrying_
                    # the_whole_block before this shipped.
                    emit(token, item_text.splitlines()[0], item_text, kind)
                continue
        emit(token, heading, section_body.strip())
    return tuple(rows)


# --------------------------------------------------------------------------- #
# Per-repo derivation
# --------------------------------------------------------------------------- #


def derive_repo(repo: str | Path, *, label: str | None = None) -> RepoDerivation:
    """Derive every section of every mainline handoff doc in `repo`.

    The ONE function here that touches git. A repo whose mainline cannot be
    resolved contributes NOTHING and says so with the ladder it tried — never a
    silent zero (`git_mainline.resolve_base_ref` returns the ladder for exactly
    this)."""
    root = Path(repo)
    name = label or root.name
    out = RepoDerivation(repo=str(root), label=name)
    if not root.is_dir():
        out.unmeasured = "no-such-directory"
        out.warnings.append(f"⚠ UNMEASURED — {name}: no such directory ({root}); contributed 0 rows")
        return out
    ref, ladder = resolve_mainline(root)
    out.ladder = ladder
    if ref is None:
        out.unmeasured = "no-mainline-ref"
        out.warnings.append(
            f"⚠ UNMEASURED — {name}: no mainline ref resolved; tried "
            f"{', '.join(ladder) or '(nothing)'}. Contributed 0 rows — this is NOT "
            f"'the repo has no handoff docs'."
        )
        return out
    out.ref = ref
    sha = ref_commit_sha(root, ref)
    tracked = handoff_paths_in_ref(root, ref)
    # 🔴 A FAILED `ls-tree` IS UNMEASURED, NOT "NO HANDOFF DOCS". Those two
    # states share the observable (zero paths) and nothing else — see
    # `handoff_paths_in_ref`. Folding them together put a repo git could not read
    # into the MEASURED set, which is the set `rebuild_delete_labels` is
    # authoritative over: the run would have deleted its rows and re-inserted
    # none, silently, exit 0. Returning here (rather than continuing with an
    # empty `tracked`) is what keeps it out of that set.
    if tracked is None:
        out.unmeasured = "ls-tree-failed"
        out.warnings.append(
            f"⚠ UNMEASURED — {name}: `git ls-tree {ref} -- {HANDOFF_DIR}` failed, so "
            f"this repo's mainline could not be enumerated. Contributed 0 rows — this "
            f"is NOT 'the repo has no handoff docs', and its existing index rows are "
            f"left alone rather than rebuilt from a reading that never happened."
        )
        return out
    out.disk = handoff_paths_on_disk(root)
    out.untracked = untracked_docs(out.disk.paths, tracked)
    out.warnings.extend(untracked_warnings(name, out.untracked))
    # 🔴 SURFACED, NOT SWALLOWED. An unreadable directory under `claudedocs/` used
    # to produce an empty disk side with no warning and no flag, which rendered as
    # the all-clear. It is a gap in the durability check, so it is a warning about
    # the CHECK — and `render_derivation` also refuses the all-clear on it.
    out.warnings.extend(unscannable_warnings(name, out.disk))
    # 🔴 THE UNREADABLE PATHS ARE RECORDED, NOT ONLY WARNED ABOUT. A warning
    # string is prose a consumer would have to grep; `unreadable` is the fact,
    # and it is what keeps a `docs == 0` from being reported as "this ref holds
    # no handoff docs" when git simply could not hand any over.
    unreadable: list[str] = []
    for path in tracked:
        text = doc_text_at_ref(root, ref, path)
        if text is None:
            unreadable.append(path)
            out.warnings.append(f"⚠ UNREADABLE — {name}: `git show {ref}:{path}` failed; skipped")
            continue
        out.docs += 1
        out.sections.extend(sections_for_doc(name, path, text, commit_sha=sha))
    out.unreadable = tuple(unreadable)
    return out


def default_repos() -> list[tuple[str, str]]:
    """`[(path, label), ...]` from the `.zshenv` handles that are SET and exist.

    An unset handle contributes nothing, silently — that is a host without that
    checkout, which is an ordinary state, not a fault. A handle that is set and
    does NOT exist is left in: `derive_repo` reports it as UNMEASURED, which is
    the loud direction for the case that is actually wrong.

    🔴 SILENT IS FINE FOR AN INDEX AND FATAL FOR A PRUNE, WHICH IS WHY
    `unset_repo_handles` EXISTS BESIDE THIS. The narrowing here is invisible by
    design — a repo simply is not in the returned list — and `--prune` reads that
    same list as THE CONFIG, i.e. as the authoritative statement of which repos
    exist. So a host with two of four handles set derives a config that is
    NARROWER than the corpus, and every label outside it reads as "removed".
    Nothing downstream can see that: those repos produce no derivation, so they
    are not UNMEASURED either. `main` gates `--prune` on the handle set, not on
    this list's contents."""
    out: list[tuple[str, str]] = []
    for handle in REPO_ENV_HANDLES:
        raw = os.environ.get(handle)
        if not raw:
            continue
        out.append((raw, Path(raw).name))
    return out


def unset_repo_handles(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The `REPO_ENV_HANDLES` this environment gives no value for, in order.

    The complement of what `default_repos` reads: `len(default_repos()) ==
    len(REPO_ENV_HANDLES) - len(unset_repo_handles())`, because that function
    appends exactly one entry per handle that is set. Stated as the HANDLES
    rather than as a count so the refusal can name them — "two handles are
    unset" is not actionable, "$DATAPACKET and $CIVITAI are unset" is.

    Takes `env` so a test can construct the state directly; defaults to the real
    environment, which is the only thing `main` ever passes."""
    src = os.environ if env is None else env
    return tuple(h for h in REPO_ENV_HANDLES if not src.get(h))


def prune_config_refusal(unset: Sequence[str]) -> str | None:
    """🔴 `--prune` NEEDS THE CONFIG TO BE AS WIDE AS THE CORPUS, AND AN UNSET
    HANDLE IS EXACTLY WHERE IT IS NOT. PURE.

    `rebuild_refusal`'s prune arm asks "did a repo fail to READ". This asks the
    other half of the same question — "is this config the WHOLE config" — and the
    two do not overlap at all: a handle that is simply UNSET produces no
    derivation, so it can never be UNMEASURED, and the read-failure guard is
    structurally blind to it. **The narrowest configs produce zero unmeasured
    repos, so that guard is weakest exactly where the risk is highest.**

    MEASURED, end to end, before this existed: `$DEVRC`/`$HOMELAB` set,
    `$DATAPACKET`/`$CIVITAI` unset (the ordinary state on a host without those
    checkouts — `nix/agent-handles.nix` existence-guards every handle), a table
    holding all four labels. `--rebuild --prune --write` derived 2 repos, both
    measured, `bad == []`, no refusal — and bound the DELETE to all four labels
    at exit 0. That is the same ~62%-of-the-corpus blast radius as the unit's
    two-handle `Environment`, relocated to the operator's own shell.

    It cannot become a permanently-red gate: `--prune` is typed by an operator
    and is never in the unit's argv, and every non-prune path is untouched. What
    it DOES mean is that pruning is only possible from a host that has every
    checkout — which is the correct answer, because pruning from a partial view
    is precisely the thing that deletes the repos you cannot see.

    🔴 THE REMEDY HAS TO NAME THE RETIRED-REPO ROUTE, BECAUSE THE OBVIOUS TWO DO
    NOT EXIST THERE. This message used to say "Set every handle and re-run
    --prune from a host that has all the checkouts — or drop --prune", and for
    the case an operator most often runs `--prune` for — a repo that is GONE —
    NEITHER remedy is reachable. `nix/agent-handles.nix` existence-guards every
    handle (`[[ -d <path> ]]`), so a deleted checkout leaves its handle unset on
    EVERY host: there is no host to re-run from, and dropping `--prune` un-blocks
    the rebuild while leaving the very rows the operator came to remove. The only
    thing that works is to stop the label being part of the config — drop the
    handle from `nix/agent-handles.nix` AND `REPO_ENV_HANDLES`, ship, then prune —
    and that route was named nowhere in the tool. Guidance that recommends a
    command the printing state makes impossible is worse than no guidance: it
    reads as considered, so the reader retries it instead of looking further."""
    if not unset:
        return None
    names = ", ".join(f"${h}" for h in unset)
    return (
        f"REFUSING --rebuild --prune: {len(unset)} of {len(REPO_ENV_HANDLES)} repo "
        f"handle(s) are UNSET — {names}. --prune deletes every stored label THIS "
        f"config does not name, which is only sound if this config is as wide as "
        f"the corpus; an unset handle narrows it SILENTLY (the repo produces no "
        f"derivation at all, so it is not even UNMEASURED), and its rows would be "
        f"collected as 'removed from config'. If those checkouts still exist "
        f"somewhere, re-run --prune from a host that has ALL of them. If the repo "
        f"is RETIRED and its checkout is gone everywhere, NO host can satisfy this "
        f"guard — the handle is existence-guarded, so it is unset on all of them: "
        f"drop it from BOTH nix/agent-handles.nix and "
        f"handoff_index.REPO_ENV_HANDLES, ship that, and then --prune, which stops "
        f"asking for a handle the config no longer has. Dropping --prune is not a "
        f"third route to the same place: it un-blocks the REBUILD but deletes only "
        f"what this run MEASURED, so the rows you came to remove stay exactly "
        f"where they are."
    )


# --------------------------------------------------------------------------- #
# The store seam
# --------------------------------------------------------------------------- #


class SectionStore(Protocol):
    """🔴 THE SEAM THAT KEEPS THE TESTS HERMETIC. The gate runs in a nix sandbox
    with no cluster and no Postgres, so every test must be able to exercise the
    derivation, the query plumbing and the reporting without one. Two
    implementations satisfy this protocol and BOTH are production code — the
    memory backend is what makes `handoff_search.py --offline` work on a laptop
    with no kubeconfig, not a test double.

    🔴 `stats()` TAKES THE SAME FILTERS `search()` DOES, and that is the whole
    silent-zero guard, not a convenience. An UNSCOPED count beside a SCOPED query
    is a comparison between two different corpora: `--repo <never-indexed>` used
    to render `NO MATCH … the index WAS searched` next to a reassuring
    `indexed_docs=352`, which is false twice over — the index was not searched,
    and 352 is not the number of documents the query could have matched. The
    filters must reach the counter or the counter is describing somebody else's
    question. `repos()` exists so a bad `--repo` can be REJECTED with the known
    values rather than answered with a fluent zero."""

    def stats(
        self, *, repo: str | None = None, sections: Sequence[str] = ()
    ) -> IndexStats: ...

    def repos(self) -> tuple[str, ...]: ...

    def search(
        self,
        query: str,
        *,
        repo: str | None = None,
        sections: Sequence[str] = (),
        limit: int = 10,
    ) -> list[Hit]: ...


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^0-9A-Za-z_]+", text.lower()) if t]


class MemorySectionStore:
    """An in-process store over derived `Section` rows. No database, no network.

    🔴 IT IS A DIFFERENT RANKER FROM POSTGRES AND SAYS SO. Postgres ranks with
    `ts_rank` over an english-stemmed tsvector; this ranks by how many DISTINCT
    query tokens a row carries. They will not agree on ordering within a tie band,
    and pretending otherwise is the failure `claude/RULES.md` calls "verified in
    isolation": a caller must never read an offline result as a prediction of the
    indexed one. What the two DO share, from one table, is the SECTION BOOST and
    the recency tiebreak — `SECTION_BOOST` is read here and compiled into the SQL
    by `_boost_case`, so the one thing a reader would notice moving cannot move in
    only one of them.

    Every response from this backend is labelled `backend=memory` by the renderer."""

    def __init__(self, sections: Iterable[Section]):
        self._rows = list(sections)

    def _selected(self, repo: str | None, sections: Sequence[str]) -> list[Section]:
        """The rows a query with these filters could reach. 🔴 THE ONE PREDICATE,
        read by both `stats` and `search`, so the counter and the query can never
        disagree about what is in scope — the disagreement being the bug."""
        want = set(sections)
        return [
            r for r in self._rows
            if (repo is None or r.repo == repo) and (not want or r.section in want)
        ]

    def stats(
        self, *, repo: str | None = None, sections: Sequence[str] = ()
    ) -> IndexStats:
        rows = self._selected(repo, sections)
        docs = {(r.repo, r.slug) for r in rows}
        return IndexStats(indexed_docs=len(docs), indexed_sections=len(rows))

    def repos(self) -> tuple[str, ...]:
        return tuple(sorted({r.repo for r in self._rows}))

    def search(
        self,
        query: str,
        *,
        repo: str | None = None,
        sections: Sequence[str] = (),
        limit: int = 10,
    ) -> list[Hit]:
        wanted = set(_tokens(query))
        if not wanted:
            return []
        hits: list[Hit] = []
        for row in self._selected(repo, sections):
            present = wanted & set(_tokens(f"{row.heading} {row.body}"))
            if not present:
                continue
            base = len(present) / len(wanted)
            hits.append(
                Hit(
                    repo=row.repo,
                    slug=row.slug,
                    doc_path=row.doc_path,
                    doc_date=row.doc_date,
                    section=row.section,
                    ordinal=row.ordinal,
                    heading=row.heading,
                    body=row.body,
                    rank=base * SECTION_BOOST.get(row.section, DEFAULT_BOOST),
                )
            )
        # Recency is the TIEBREAK, never the primary key, and a doc with no date
        # sorts last rather than first — an undated doc must not outrank a dated
        # one by accident of a missing field.
        hits.sort(key=lambda h: (-h.rank, h.doc_date is None, _neg_date(h.doc_date),
                                 h.repo, h.slug, h.section, h.ordinal))
        return hits[:limit]


def _neg_date(d: str | None) -> str:
    """A sort key that puts the NEWEST date first among strings.

    `str` cannot be negated, so descending order is expressed by complementing
    each digit. Kept tiny and named rather than inlined as a lambda, because a
    reader hitting `''.join(...)` inside a sort key has to reverse-engineer the
    direction."""
    if not d:
        return ""
    return "".join(str(9 - int(c)) if c.isdigit() else c for c in d)


def _boost_case(column: str = "section") -> str:
    """The SQL `CASE` that applies `SECTION_BOOST`, BUILT from that table.

    🔴 NEVER TYPED OUT IN THE SQL STRING. One table, two backends: the numbers a
    reader can argue with live in exactly one place, and a change to them cannot
    land in the offline ranker while missing the indexed one. The keys are module
    constants and never user input, so interpolating them is safe — the same
    argument `sync.py::_ensure_view` makes for its view names."""
    arms = " ".join(
        f"WHEN '{token}' THEN {boost}" for token, boost in sorted(SECTION_BOOST.items())
    )
    return f"CASE {column} {arms} ELSE {DEFAULT_BOOST} END"


def _filter_predicates(*, repo: bool, sections: bool) -> list[str]:
    """The `WHERE` fragments for the `repo`/`sections` filters. ONE definition.

    🔴 READ BY BOTH `search_sql` AND `stats_sql`, so the query and the count that
    qualifies it cannot come to different views of what is in scope. That
    divergence is not hypothetical: the count used to have no filters at all,
    which is how `--repo <never-indexed>` printed `indexed_docs=352` beside a
    zero-result query and called the result an answer about the corpus."""
    out: list[str] = []
    if repo:
        out.append("repo = %s")
    if sections:
        out.append("section = ANY(%s)")
    return out


class PostgresSectionStore:
    """The indexed backend: `ts_rank` over the generated `tsv`, GIN-backed.

    🔴 NO TEST REACHES A DATABASE, AND THAT IS STATED RATHER THAN PAPERED OVER.
    The gate runs in a nix sandbox with no cluster and no Postgres, so every claim
    about what a SERVER does with this class is a claim about code that has been
    read, not run. What IS covered hermetically: the SQL text this class builds
    (pinned), the boost table it shares with the memory backend, the row shape it
    inserts, every caller path above it, and — through a fake connection that
    records `(statement, commit)` in order — that a `--rebuild` write issues its
    SCOPED DELETE and every INSERT inside ONE transaction with exactly ONE commit
    at the end. That last one is a real behavioural guard against a real incident,
    but a recorder is not a server. What is still NOT covered: that Postgres
    accepts the DDL, that the generated column is legal, that `ts_rank` orders as
    expected, that the delete and the inserts really are one transaction on the real
    server, or that a real query returns anything at all."""

    def __init__(self, conn):
        self._conn = conn

    # -- write ------------------------------------------------------------- #

    def ensure_schema(self) -> None:
        """Idempotent, additive-only DDL under a transaction-scoped advisory lock —
        `sync.py::ensure_schema`'s shape, for its reason: a manual run racing the
        timer must not collide on `CREATE … IF NOT EXISTS`, which is not fully
        race-safe."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK_KEY,))
            cur.execute(TABLES_DDL)
        self._conn.commit()

    #: The column order every write uses. ONE list — the INSERT's column clause,
    #: its placeholder count and its per-row value extraction all read it, so they
    #: cannot drift into a silently-shifted row.
    WRITE_COLUMNS: tuple[str, ...] = (
        "repo", "slug", "doc_path", "doc_date", "commit_sha", "section",
        "ordinal", "heading", "body", "forcing_kind", "clawgate_task",
    )

    @classmethod
    def insert_sql(cls) -> str:
        """The upsert statement, as text, so a test can pin it without a database."""
        cols = cls.WRITE_COLUMNS
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}"
            for c in cols
            if c not in ("repo", "slug", "section", "ordinal")
        )
        return (
            f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (repo, slug, section, ordinal) DO UPDATE SET {updates}"
        )

    #: 🔴 A PREDICATE, NOT A `TRUNCATE`. The statement this replaced was
    #: `TRUNCATE initiatives.handoff_section` with no WHERE at all, so a run
    #: scoped to ONE repo emptied EVERY repo and reported success. The scope is
    #: computed by `rebuild_delete_labels` and passed in; there is no code path
    #: that deletes without one.
    DELETE_SQL = f"DELETE FROM {TABLE} WHERE repo = ANY(%s)"

    def write(
        self,
        rows: Sequence[Section],
        *,
        rebuild: bool = False,
        rebuild_labels: Sequence[str] = (),
    ) -> int:
        """Write `rows`. With `rebuild`, DELETE the in-scope labels first — IN THE
        SAME TRANSACTION.

        🔴 THE DELETE IS SCOPED, AND AN UNSCOPED REBUILD IS A `ValueError`, NOT A
        WHOLE-TABLE WIPE. Measured against this class over a recording connection:
        `--repo ~/workspace/devrc --rebuild --write` issued `TRUNCATE
        initiatives.handoff_section`, re-inserted only devrc, printed `wrote 968
        section row(s)` and exited 0 — homelab-talos's ~515 sections silently
        gone. `rebuild_labels` is therefore REQUIRED whenever `rebuild` is set;
        an empty scope means the caller has not decided what it is authoritative
        for, and guessing "everything" is exactly the bug. `rebuild_delete_labels`
        is the one place that decision is made.

        🔴 ONE TRANSACTION, ONE COMMIT, ALL-OR-NOTHING PER RUN. This replaces a
        `truncate()` that committed on its own followed by an `upsert()` that
        committed at the end, and the gap between those two commits was a window
        in which the table was EMPTY AND COMMITTED. Anything that raised inside
        the row loop — a bad `doc_date` (see `doc_date_for`), a dropped
        port-forward, the process being killed by `TimeoutStartSec` — left it that
        way, because `MailDB.__exit__` only calls `conn.close()` and an unfinished
        transaction is discarded, not flushed. The truncate, however, was already
        durable. `scripts/initiatives/sync.py::write_snapshot` is the in-repo
        template: it "commits once at the end (all-or-nothing per run)".

        The old `upsert` docstring claimed the opposite — "a run that dies
        mid-write leaves the previous generation intact rather than a half-empty
        table". That was true of `upsert` ALONE and false of every `--rebuild`
        run, which is every run the timer makes. A comment is a claim too, and
        this one would have talked a reader out of looking.

        ⚠ A SCOPED DELETE IS NOT AS CHEAP AS `TRUNCATE`, and that is a knowing
        trade: `TRUNCATE` reclaims the whole relation in one step while `DELETE`
        writes a tuple version per row and leaves the space to autovacuum. On a
        corpus measured in the low thousands of rows that is not a cost worth
        buying a whole-table wipe with. It is stated because a future reader
        looking at row counts an order of magnitude larger should re-derive it,
        not because it bites today.

        ON CONFLICT DO UPDATE is still the insert form: with the delete now
        inside the transaction it is what keeps a manual non-rebuild run
        idempotent, and it is the reason `identity_collisions` has to exist —
        the clause cannot raise on a duplicate identity, so nothing but that
        function would notice one.

        🔴 A doc that SHRINKS (a section removed) leaves its old high ordinals
        behind on a NON-rebuild run — that is what `--rebuild` is for, and it is
        why the timer's unit runs it."""
        if rebuild and not rebuild_labels:
            raise ValueError(
                "refusing a --rebuild with an EMPTY delete scope: the caller has "
                "not said which repo labels this run is authoritative for, and "
                "'all of them' is the whole-table wipe this parameter exists to "
                "prevent. See rebuild_delete_labels()."
            )
        sql = self.insert_sql()
        cols = self.WRITE_COLUMNS
        n = 0
        with self._conn.cursor() as cur:
            if rebuild:
                cur.execute(self.DELETE_SQL, [list(rebuild_labels)])
            for row in rows:
                data = row.as_row()
                cur.execute(sql, [data[c] for c in cols])
                n += 1
        self._conn.commit()
        return n

    # -- read -------------------------------------------------------------- #

    #: 🔴 SCOPED, and the `{where}` is why. See `SectionStore.stats`: an unscoped
    #: count rendered beside a scoped query is a number about a different corpus,
    #: and it is what made `--repo <never-indexed>` read as an answer.
    STATS_SQL = f"SELECT count(DISTINCT (repo, slug)), count(*) FROM {TABLE}"

    @staticmethod
    def stats_sql(*, repo: bool, sections: bool) -> str:
        """The count query, as text, so a test can pin it without a database.

        🔴 THE PREDICATES ARE THE SAME STRINGS `search_sql` USES. Two hand-typed
        WHERE clauses over one table is the duplicated predicate
        `claude/RULES.md` says is wrong at N−1 sites — and here the two sites are
        the query and the number that tells you whether to believe the query."""
        where = _filter_predicates(repo=repo, sections=sections)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        return f"{PostgresSectionStore.STATS_SQL}{clause}"

    def stats(
        self, *, repo: str | None = None, sections: Sequence[str] = ()
    ) -> IndexStats:
        sql = self.stats_sql(repo=repo is not None, sections=bool(sections))
        params: list[object] = []
        if repo is not None:
            params.append(repo)
        if sections:
            params.append(list(sections))
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return IndexStats(indexed_docs=int(row[0]), indexed_sections=int(row[1]))

    REPOS_SQL = f"SELECT DISTINCT repo FROM {TABLE} ORDER BY 1"

    def repos(self) -> tuple[str, ...]:
        """Every repo label the index actually holds — the answer a rejected
        `--repo` is shown, so the caller learns the vocabulary instead of being
        handed a fluent zero."""
        with self._conn.cursor() as cur:
            cur.execute(self.REPOS_SQL)
            return tuple(r[0] for r in cur.fetchall())

    @staticmethod
    def search_sql(*, repo: bool, sections: bool) -> str:
        """The ranked query, as text, so a test can pin it without a database.

        `plainto_tsquery` and not `websearch_to_tsquery`: the caller is `/resume`
        and subagents passing a phrase, not a human typing search operators, and
        `plainto_tsquery` ANDs the terms — which is the behaviour that makes a
        two-word query narrow rather than widen."""
        where = ["tsv @@ q", *_filter_predicates(repo=repo, sections=sections)]
        return (
            f"SELECT repo, slug, doc_path, doc_date, section, ordinal, heading, body, "
            f"ts_rank(tsv, q) * {_boost_case()} AS rank "
            f"FROM {TABLE}, plainto_tsquery('english', %s) q "
            f"WHERE {' AND '.join(where)} "
            f"ORDER BY rank DESC, doc_date DESC NULLS LAST, repo, slug, section, ordinal "
            f"LIMIT %s"
        )

    def search(
        self,
        query: str,
        *,
        repo: str | None = None,
        sections: Sequence[str] = (),
        limit: int = 10,
    ) -> list[Hit]:
        sql = self.search_sql(repo=repo is not None, sections=bool(sections))
        params: list[object] = [query]
        if repo is not None:
            params.append(repo)
        if sections:
            params.append(list(sections))
        params.append(limit)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [
            Hit(
                repo=r[0], slug=r[1], doc_path=r[2],
                doc_date=r[3].isoformat() if hasattr(r[3], "isoformat") else r[3],
                section=r[4], ordinal=r[5], heading=r[6], body=r[7], rank=float(r[8]),
            )
            for r in rows
        ]


#: `scripts/mail-actions/_db.py` — kubectl port-forward + psycopg2 + DSN-from-secret.
#: The ONE connection path in this repo; `sync.py` loads it the same way and for
#: the same reason, and inventing a second one is what this constant prevents.
MAILDB_PATH = Path(__file__).resolve().parents[1] / "mail-actions" / "_db.py"


def import_maildb():
    """Load `MailDB` by EXPLICIT importlib path — `sync.py::_import_maildb`'s recipe.

    Do NOT put `mail-actions/` on `sys.path`: its `llm.py` shadows other modules
    and breaks callers (devrc CLAUDE.md; `repo-cos/feedback.py` hit the same trap).
    `_db.py` imports only stdlib + psycopg2, so a standalone load is safe."""
    spec = importlib.util.spec_from_file_location("handoff_index_maildb", MAILDB_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {MAILDB_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.MailDB


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


#: 🔴 THE ENUMERATED KINDS OF INCOMPLETENESS. They are constants rather than
#: literals at each site because `rebuild_plan_lines` PARTITIONS the configured
#: repos on this value, and a partition keyed on a string literal silently loses
#: a repo the day a third kind is added: it matches no branch, appears in no
#: bucket, and the operator counts the labels, finds one missing, and has no way
#: to learn why. `incomplete_kinds_are_partitioned` is the check that says so
#: instead.
INCOMPLETE_UNMEASURED = "unmeasured"
INCOMPLETE_UNREADABLE = "docs-unreadable"
INCOMPLETE_KINDS = (INCOMPLETE_UNMEASURED, INCOMPLETE_UNREADABLE)


def incomplete_kind(d: RepoDerivation) -> str | None:
    """WHICH KIND of incompleteness withholds authority over `d.label` — or `None`.

    🔴 THE KIND IS SPLIT OUT OF `incomplete_reason` BECAUSE ONE CONSUMER NEEDS TO
    BRANCH ON IT AND WAS RE-DERIVING IT FROM THE RAW FIELDS. `rebuild_plan_lines`
    sorts every configured repo into three buckets, and it read
    `d.unmeasured is not None` for one and `d.unmeasured is None and d.unreadable`
    for the other — the exact "predicate open-coded at N sites" shape
    `incomplete_reason`'s own docstring says the four spellings came from, still
    present at the one site that most needed it. Recovering the kind by matching
    on the REASON STRING would be worse again: the reason for an unmeasured repo
    is `d.unmeasured` itself, a free-form token, so any such match is a guard on
    WORDS.

    So the kind is the structural value and the reason is derived FROM it. A
    third kind therefore has exactly one place to be added, and
    `rebuild_plan_lines` reports an unenumerated one rather than dropping it.

    PURE."""
    if d.unmeasured:
        return INCOMPLETE_UNMEASURED
    if d.unreadable:
        return INCOMPLETE_UNREADABLE
    return None


def incomplete_reason(d: RepoDerivation) -> str | None:
    """WHY this run cannot speak for `d.label`'s stored rows — `None` if it can.

    🔴 THE ONE PREDICATE THAT AUTHORISES DESTROYING A REPO'S ROWS, AND IT EXISTS
    BECAUSE THE SAME DEFECT SHIPPED FOUR TIMES IN FOUR SPELLINGS. Every one of
    them was a `--rebuild` deleting rows it could not put back, and every one was
    a different way of computing "which repos may I destroy":

      1. an unpredicated `TRUNCATE` — authority over the whole table, granted by
         nothing at all (`rebuild_delete_labels`' opening note).
      2. the scope taken over the wrong LABEL SET — stored-but-not-configured
         rows collected on every timer tick, ~62% of the corpus per 6h.
      3. a refusal that checked whether a repo could be READ when the risk was
         whether the config was WIDE (`prune_config_refusal`) — the read-failure
         guard is structurally blind to an unset handle, so it is weakest exactly
         where the risk is highest.
      4. this one: the scope keyed on `unmeasured`, which answers "did the ref
         resolve", while the question the DELETE asks is "did anything come
         back". A repo whose mainline lists docs git cannot produce resolves
         fine, so it landed in the scope with nothing to re-insert.

    THE COMMON SHAPE IS NOT "SOMEONE FORGOT A FIELD". It is that authority was
    granted by a NEGATIVE predicate — the absence of whichever failure had
    already been seen — instead of by a POSITIVE demonstration that this run
    holds a complete replacement. A blacklist re-opens the hole for every failure
    nobody has met yet, which is why four rounds of naming one more failure never
    converged. So the default is inverted here: a label is authoritative only
    when this run read the repo IN FULL, and any incompleteness — of any kind,
    including kinds not yet invented — withholds it.

    🔴 AND IT IS *ONE* FUNCTION BECAUSE THE FOUR SITES ARE THE BUG. `claude/RULES.md`
    → "one rule, one place": a predicate open-coded at N sites is typically wrong
    at N−1 of them in the same direction. `rebuild_delete_labels`,
    `partial_scope_warnings`, `rebuild_refusal` and `rebuild_plan_lines` each
    re-derived "is this repo good" from whatever field was nearest, and spelling
    3 above is literally two of those sites disagreeing about which question they
    were answering. They now read this.

    TWO WAYS TO BE INCOMPLETE, AND THEY ARE KEPT DISTINCT IN THE REASON STRING
    RATHER THAN COLLAPSED INTO A BOOL, because the remedies are nothing alike:

      * `d.unmeasured` — nothing came back at all: no checkout, no mainline ref,
        a failed `ls-tree`. Fix the handle or the checkout.
      * `d.unreadable` — the ref was enumerated and git could not produce the
        content of at least one doc it lists. Repair the object store.

    ⚠ `unmeasured` IS DELIBERATELY NOT SET FOR THE SECOND CASE, and that is a
    considered reversal of the obvious fix. Calling a repo that read 39 of 40
    docs "UNMEASURED" would make that field mean both "nothing came back" and
    "not everything came back" — the exact conflation this module has now been
    burned by three times (`handoff_paths_in_ref`'s `None`-vs-`()`, `DiskScan`'s
    three meanings of `()`, and `docs == 0`'s two mechanisms). It would also
    throw away the 39 readable docs: they are still upserted, because an
    ON CONFLICT insert REFRESHES without destroying, and refusing to index what
    was read buys nothing the delete scope does not already buy.

    PURE, so the whole authority decision is testable without a database."""
    kind = incomplete_kind(d)
    if kind is None:
        return None
    if kind == INCOMPLETE_UNMEASURED:
        return d.unmeasured
    # The counts are in the reason because "1 of 40" and "40 of 40" are very
    # different operator situations and the label alone cannot say which.
    return f"docs-unreadable ({len(d.unreadable)} of {len(d.unreadable) + d.docs})"


def may_replace_stored_rows(d: RepoDerivation) -> bool:
    """Did this run obtain a COMPLETE replacement for `d.label`'s stored rows?

    The positive spelling of `incomplete_reason`, so a call site reads as the
    question the DELETE actually asks. Never re-derive either of these from the
    underlying fields — see `incomplete_reason`'s note on the four spellings."""
    return incomplete_reason(d) is None


def authority_label_collisions(
    derivations: Sequence[RepoDerivation],
) -> tuple[str, ...]:
    """Labels claimed by BOTH a replaceable derivation and an incomplete one. PURE.

    🔴 AUTHORITY IS DEMONSTRATED PER *DERIVATION* AND EXERCISED PER *LABEL*, AND
    THOSE ARE NOT THE SAME KEY. `rebuild_delete_labels` returns LABELS and
    `DELETE_SQL` binds LABELS, so when two derivations share one label — the
    `~/a/proj` vs `~/b/proj` case `identity_collisions` names explicitly — a
    single healthy twin grants authority over BOTH twins' rows.

    MEASURED, two checkouts named `protorepo` under different parents, the right
    one's doc blobs removed, `--rebuild --write`: `DELETE params
    [[['protorepo']]]`, 21 INSERTs all from the healthy twin, rc 0 — the broken
    twin's rows deleted with nothing to replace them. Identical before the
    incomplete-read widening, so this is not a regression it introduced.
    `identity_collisions` cannot see it either: unreadable docs produce no
    sections, so nothing collides at the SECTION level.

    ⚠ THIS FUNCTION DOES NOT FIX THAT. The structural fix — keying authority on
    something a label cannot alias — moves the delete scope, the stored key and
    `handoff_search`'s scoping together, and is its own round. What it fixes is
    the REPORT: without it this run printed "Their existing rows are left
    untouched … this run destroyed nothing it could not replace" and "kept their
    old rows" ABOUT ROWS IT HAD JUST DELETED, and listed the label under both
    DELETE and KEPT in the pre-flight. A silent wrong is better than a stated
    wrong: a stated wrong is what stops the next reader looking."""
    ok = {d.label for d in derivations if may_replace_stored_rows(d)}
    bad = {d.label for d in derivations if incomplete_reason(d)}
    return tuple(sorted(ok & bad))


def rebuild_downgrade_reason(
    derivations: Sequence[RepoDerivation],
) -> str | None:
    """WHY a `--rebuild` must drop its DELETE and write as a plain upsert. PURE.

    🔴 A DOWNGRADE RATHER THAN A REFUSAL, AND THE DIFFERENCE IS A TOAST 4×/DAY
    FOREVER. When not ONE configured repo was read in full the delete scope is
    EMPTY, and `PostgresSectionStore.write` RAISES on an empty rebuild scope by
    design — so this state has to be handled somewhere. The round that found it
    made it `rebuild_refusal`'s fourth arm, rc 4. MEASURED on the TIMER's own
    argv (`--rebuild --write`, unscoped) with 1 of 4 handles present and the
    present repo carrying ONE corrupt blob of three docs, across three commits of
    one file:

        pre-widening : rc 0  DELETE ['<label>'] + 18 INSERTs
        that arm     : rc 4  SQL kinds []        nothing written at all
        here         : rc 0  no DELETE           + 18 INSERTs

    Three absent checkouts is not a broken machine: `nix/home.nix` states in as
    many words that a handle pointing at an absent checkout is SUPPORTED, and
    that the price is a standing PARTIAL warning. `OnFailure=notify-failure@%n
    .service` on a 6h timer then fires until a human repairs an object store, and
    the index goes stale meanwhile — `claude/RULES.md`'s permanently-red gate,
    which is the mistake `rebuild_refusal`'s own all-unmeasured arm had to unwind
    once already.

    🔴 AND THE FALLBACK IS NOT AN INVENTION — IT IS THE REMEDY THE REFUSAL ITSELF
    NAMED. That arm's text ended "Re-running WITHOUT --rebuild is a real remedy
    here: it upserts those N row(s) and deletes nothing." A remedy only a human
    can type is not a remedy for a unit, so the run takes it: rebuild off,
    delete scope empty, every row that DID read upserted. It is strictly
    non-destructive — an ON CONFLICT insert refreshes without destroying — so
    downgrading cannot lose a row the refusal would have kept.

    ⚠ WHAT THE DOWNGRADE DOES NOT BUY, STATED RATHER THAN DISCOVERED: a section
    REMOVED from a doc keeps its stale high-ordinal row, because that is exactly
    what the DELETE is for (`PostgresSectionStore.write`'s closing note). The run
    says so, and says it on the success line, because "wrote N section row(s)"
    otherwise reads as a completed rebuild.

    Returns `None` when the rebuild may keep its DELETE. It is `main`'s SINGLE
    spelling of that decision — the scope is computed only when this returns
    `None` — so an empty scope can never reach `write()` and the two cannot
    drift. `TestARebuildWithNothingReadInFullIsDowngradedNotRefused` pins the
    equivalence against `rebuild_delete_labels`' actual return."""
    if not derivations:
        return None
    if any(may_replace_stored_rows(d) for d in derivations):
        return None
    detail = ", ".join(f"{d.label} ({incomplete_reason(d)})" for d in derivations)
    return (
        f"REBUILD DOWNGRADED TO AN UPSERT: not ONE of the {len(derivations)} "
        f"repo(s) was read COMPLETELY — {detail}. A rebuild DELETES a repo's rows "
        f"before re-deriving them, so it may only run for repos this run can "
        f"REPLACE, and there are none: the delete scope would be EMPTY. This is "
        f"NOT 'the corpus is empty' — the documents that DID read are still "
        f"upserted, and an ON CONFLICT insert REFRESHES without destroying, so "
        f"NOTHING was deleted and no stored row was lost. What the skipped DELETE "
        f"would have bought and this run does not: a section REMOVED from a doc "
        f"keeps its stale row until some repo reads in FULL again. Where the "
        f"reason is a doc that would not read, `git ls-tree` lists a path `git "
        f"show` cannot produce, which means an incomplete object store — try "
        f"`git fsck` and re-fetch."
    )


def rebuild_refusal(
    derivations: Sequence[RepoDerivation],
    rows: Sequence[Section],
    *,
    prune: bool = False,
) -> str | None:
    """`None` when a `--rebuild` is safe to run; otherwise WHY it is refused.

    🔴 THE GUARD BETWEEN A BAD RUN AND AN EMPTY PRODUCTION INDEX. `--rebuild`
    deletes BEFORE it knows it has anything to put back, and the timer runs
    exactly `--rebuild` every 6h. Two failures reached that delete and were
    reported as success:

      * EVERY repo UNMEASURED — a renamed checkout, an unset env handle, a
        `git` that could not resolve a mainline. Measured: driving
        `main(["--repo","/bogus/a","--repo","/bogus/b","--rebuild"])` emptied the
        table and returned 0, so `OnFailure=notify-failure@%n.service` never fired
        and the only symptom was the search CLI's `🔴 BROKEN INDEX` days later.
      * a derivation of ZERO rows from repos that DID resolve — every doc
        unreadable, or `HANDOFF_DIR` gone.

    Both are "I could not measure the corpus", and neither is "the corpus is
    empty". Rebuilding on either replaces a good index with a confident nothing.
    The refusal is checked against `RepoDerivation.unmeasured` — a STRUCTURAL
    field, never a grep over the warning prose, which a reword would walk past.

    🔴 A PARTIAL DERIVATION IS NOT A REFUSAL, AND THAT IS A DELIBERATE REVERSAL.
    This guard first shipped refusing when ANY repo came back UNMEASURED. Measured
    on one present repo (2 real rows) plus one absent repo: `REFUSING --rebuild: 1
    of 2 repo(s) came back UNMEASURED`, rc 4, nothing written — on a unit carrying
    `OnFailure=notify-failure@%n.service` and a 6h timer, i.e. a failure toast
    4×/day forever with the index frozen at whatever it last held. A host that
    legitimately does not have `$CIVITAI` or `$HOMELAB` checked out is a SUPPORTED
    state (`nix/home.nix` says so where it sets the handles), and
    `claude/RULES.md` is explicit that a permanently-red gate is worse than no
    gate: it trains everyone to click through. So the rule is:

        refuse when ALL repos are unmeasured, or when the measured subset
        yields ZERO rows — index what resolved, and warn loudly about the rest.

    What makes the partial case safe is NOT this function: it is
    `rebuild_delete_labels`, which scopes the delete to the labels this run
    actually MEASURED, so an unmeasured repo's rows are left exactly where they
    are rather than truncated away. Refusing here as well would buy nothing and
    cost the toast. The loud half is `partial_scope_warnings`, which every output
    path prints and which the write's own success line repeats — a partial run
    must not be able to read as a complete one.

    🔴 THE ALL-UNMEASURED ARM'S REMEDY WAS FALSE IN ITS OWN STATE. It offered
    "re-run without --rebuild to refresh what CAN be measured", and when ALL
    repos are unmeasured there is nothing that can be measured. MEASURED, same
    argv minus `--rebuild`: rc 0, `wrote 0 section row(s)`, and the store WAS
    opened — a silent success, which is the precise failure the sentence above it
    describes as the reason to refuse. A remedy is a claim about the state it is
    printed in, so it earns the same evidence a diagnosis does; the arm now names
    fixing the handles as the only route and says why the other is not one. Swept
    with `prune_config_refusal` and `orphan_label_warning`, which had the same
    shape for a different state."""
    # 🔴 `bad` IS EVERY REPO THIS RUN CANNOT REPLACE, NOT EVERY REPO THAT FAILED TO
    # RESOLVE — see `incomplete_reason`, which owns that decision for all four
    # sites. `unmeasured` is kept as its own list because two of the arms below
    # make claims that are true ONLY of that narrower class: "came back
    # UNMEASURED", and "the N repo(s) that resolved a mainline ref".
    bad = [d for d in derivations if incomplete_reason(d)]
    unmeasured = [d for d in derivations if d.unmeasured]
    # 🔴 `--prune` NEEDS THE CONFIG TO BE AUTHORITATIVE ABOUT REPO IDENTITY, AND AN
    # UNMEASURED REPO IS EXACTLY WHERE IT IS NOT. Pruning means "delete every
    # stored label the config does not name" — a claim that the CONFIGURED
    # spelling of each repo is the STORED spelling. A repo that could not be read
    # is the case where those two can differ: table holds `civitai`, the checkout
    # is renamed, `$CIVITAI` now yields the label `civitai-old`, that label is
    # UNMEASURED, and `civitai` reads as "dropped from the config" and is
    # collected. This docstring's own text names a renamed checkout as a cause of
    # UNMEASURED, so the collision is not hypothetical.
    #
    # Refusing here rather than filtering inside `rebuild_delete_labels` is the
    # deliberate choice: the run cannot tell a rename from a genuine removal, and
    # guessing either way destroys or preserves rows on a coin flip. It cannot
    # become a permanently-red gate the way the ALL-unmeasured arm nearly did,
    # because `--prune` is typed by an operator and is never in the unit's argv.
    #
    # 🔴 THIS IS ONLY HALF OF `--prune`'s PRECONDITION, AND THE OTHER HALF IS NOT
    # HERE — see `prune_config_refusal`. This asks "did a configured repo fail to
    # READ"; an UNSET handle produces no derivation at all, so it cannot appear
    # in `bad` and this guard is structurally blind to it. The narrowest configs
    # yield zero unmeasured repos, i.e. this guard is weakest exactly where the
    # risk is highest. `main` runs the config-width guard first.
    #
    # 🔴 THE OPENER IS A DISJUNCTION ("UNMEASURED or INCOMPLETE") RATHER THAN A
    # CONDITIONAL, so it is true in every state this arm fires in and there is no
    # second spelling to drift. The per-repo `detail` carries which of the two it
    # actually was, which is the fact the operator acts on.
    #
    # 🔴 AND THE TWO HALVES OF THE DISJUNCTION ARE REFUSED FOR TWO DIFFERENT
    # REASONS, WHICH THE MESSAGE HAS TO SAY RATHER THAN COLLAPSE. The widening
    # first shipped claiming an unreadable repo is "refused for the same reason it
    # is kept out of the delete scope: this run has nothing to put back". That is
    # false twice: `--prune` never puts back the disappeared labels' rows — it
    # exists to delete them — and the incomplete repo is already outside
    # `measured`, so its OWN rows are safe with or without this arm. The renamed
    # -checkout ambiguity the arm was built for cannot arise from an unreadable
    # doc either: the ref resolved, the directory is present, the label is
    # unchanged. The real reason is narrower and is worth stating plainly — a run
    # that could not read its own corpus in FULL cannot vouch for the corpus at
    # all, and `--prune` is the one act whose scope is decided by what this run
    # did NOT find. Being conservative on a destructive operator flag is cheap;
    # `--prune` is typed by a human and is never in the unit's argv, so this arm
    # cannot become the permanently-red gate the ALL-unmeasured arm nearly did.
    if prune and bad:
        detail = ", ".join(f"{d.label} ({incomplete_reason(d)})" for d in bad)
        return (
            f"REFUSING --rebuild --prune: {len(bad)} of {len(derivations)} repo(s) came "
            f"back UNMEASURED or INCOMPLETE — {detail}. --prune deletes every stored "
            f"label this config does not name, which is only sound if the spelling of "
            f"every repo is the table's spelling; an unmeasured repo (a renamed or "
            f"missing checkout) is precisely where those can differ, and the rows it "
            f"would collect may be that same repo under its old label. A repo whose "
            f"docs would not READ is refused for a DIFFERENT reason, stated so it is "
            f"not mistaken for that one: it raises no spelling ambiguity and its own "
            f"rows are already safe, but --prune decides what to DESTROY from what "
            f"this run did NOT find, and a run that could not read its corpus in FULL "
            f"cannot vouch for the corpus at all. Fix the handles and the object store "
            f"so every repo reads in FULL, then re-run --prune — or drop --prune, "
            f"which deletes only what this run read COMPLETELY."
        )
    if derivations and len(unmeasured) == len(derivations):
        detail = ", ".join(f"{d.label} ({d.unmeasured})" for d in unmeasured)
        return (
            f"REFUSING --rebuild: ALL {len(derivations)} repo(s) came back "
            f"UNMEASURED — {detail}. A rebuild here would replace a good index with "
            f"a confident nothing, and the run would report success. Fix the repo "
            f"handles — in THIS state that is the only remedy. Re-running without "
            f"--rebuild is not a second one: with every repo unmeasured there is "
            f"nothing left to refresh, so it writes 0 row(s) and exits 0, which is "
            f"the silent success this refusal exists to prevent."
        )
    if not rows:
        measured = len(derivations) - len(unmeasured)
        return (
            "REFUSING --rebuild: the derivation produced ZERO rows from the "
            f"{measured} repo(s) that resolved a mainline ref. That is not 'the "
            "corpus is empty' — it is a corpus that could not be read. Nothing is "
            "deleted and nothing is written."
        )
    # 🔴 "NOT ONE REPO WAS READ IN FULL" IS DELIBERATELY *NOT* AN ARM HERE, AND IT
    # WAS ONE FOR EXACTLY ONE COMMIT. Reaching this point means at least one repo
    # resolved AND rows came back, but possibly no repo is REPLACEABLE — which
    # makes the delete scope empty, and `PostgresSectionStore.write` RAISES on an
    # empty rebuild scope by design. Refusing was the first answer and it was
    # wrong for the TIMER: measured on the unit's own argv with three absent
    # checkouts (a state `nix/home.nix` documents as SUPPORTED) plus one corrupt
    # blob, it turned rc 0 + a written index into rc 4 + nothing written, i.e. a
    # notify-failure toast every 6h until a human repairs an object store.
    # `rebuild_downgrade_reason` takes the remedy this arm's own text named —
    # write, but without the DELETE — and `main` reads it. See that function.
    return None


def partial_scope_warnings(derivations: Sequence[RepoDerivation]) -> tuple[str, ...]:
    """The loud half of the partial-rebuild decision. PURE.

    🔴 A PARTIAL RUN MUST NOT BE ABLE TO READ AS A COMPLETE ONE. `rebuild_refusal`
    now PROCEEDS when some repos measured and some did not, so the only thing left
    standing between "indexed what resolved" and a reader believing the index
    covers every configured repo is this sentence. Empty when every repo measured,
    for `untracked_warnings`' reason: a per-run "0 repos missing" buries the real
    ones.

    Printed by `render_derivation` (so `--dry-run` and `--json` carry it) AND
    repeated on the write's own success line, because the success line is the one
    output a scripted caller is most likely to be reading alone.

    🔴 THE LAST SENTENCE IS A PROMISE ABOUT ANOTHER FUNCTION'S BEHAVIOUR, AND IT
    WAS FALSE. This reasons over CONFIGURED labels; `rebuild_delete_labels` used
    to compute its extra "disappeared from config" set over STORED labels, and a
    repo can be configured-but-unmeasured under one spelling and
    not-configured-at-all under another (a renamed checkout: table holds
    `civitai`, `$CIVITAI` now yields `civitai-old`). The delete scope then
    included `civitai` while this sentence promised, in the same transaction,
    that the unmeasured repo's rows were preserved.

    It is true now, and true STRUCTURALLY rather than by inspection: the
    disappeared-set is behind an explicit `--prune`, and `rebuild_refusal`
    refuses any `--prune` run with an INCOMPLETE repo. So whenever this warning
    fires AND a write happens, the delete scope is exactly the MEASURED labels.
    `TestThePartialPromiseIsKept` pins that as a relationship between the two
    functions rather than trusting either one's own docstring.

    🔴 IT SPEAKS FOR EVERY REPO THE RUN IS NOT AUTHORITATIVE FOR, NOT ONLY THE
    UNMEASURED ONES — `incomplete_reason` owns that set for all four sites. It
    used to key on `unmeasured` alone, in step with the delete scope, so a repo
    whose docs would not READ was outside BOTH: it was silently bound to the
    DELETE and this sentence did not mention it. The two moved together then and
    they move together now, which is the property `TestThePartialPromiseIsKept`
    pins.

    🔴 "CONTRIBUTED NOTHING" WAS DROPPED FROM THIS SENTENCE, AND THE DELETION IS
    THE POINT. It is true of an UNMEASURED repo and false of a partially readable
    one, which contributes every row it could read (an ON CONFLICT insert
    refreshes without destroying, so there is no reason to throw those away).
    Keeping the phrase would have made this warning false in exactly the state it
    was widened to cover.

    🔴 THE ALL-BAD SHORT-CIRCUIT IS GONE, AND ITS REMOVAL IS A CORRECTION TO A
    CLAIM MADE ONE COMMIT AGO. `rebuild_delete_labels` says this function "says so
    on every output path"; it did not. It returned `()` when EVERY repo was
    incomplete and deferred to `rebuild_refusal` — which `main` consults only
    under `--rebuild`. MEASURED on HEAD, two repos each with one unreadable doc,
    `--write` WITHOUT `--rebuild`: `partial_scope_warnings() == ()`, `wrote 24
    section row(s)`, rc 0, and the word PARTIAL nowhere on stdout. The hole
    pre-dated the widening; the CLAIM did not, and a false claim is the sentence
    that stops the next person looking. It is now a WRITING state besides, since
    `rebuild_downgrade_reason` proceeds where the fourth refusal arm stopped, so
    silence there is not a deferral to anything.

    The old worry — saying it twice in two voices is how one of them stops being
    read — is answered by making the two say different things: this one is a
    SCOPE statement ("no repo's rows were replaced"), the refusal is a REFUSAL
    with a remedy, and the all-bad variant below cannot be mistaken for the mixed
    one because it names no `ok` list at all.

    🔴 THE "LEFT UNTOUCHED" PROMISE IS CONDITIONAL, BECAUSE AUTHORITY IS PER
    DERIVATION AND THE DELETE IS PER LABEL. Two configured repos can share one
    label (`~/a/proj` and `~/b/proj`), and a healthy twin then grants a DELETE
    over the broken twin's rows. See `authority_label_collisions`, which is the
    residual this qualification exists to stop MIS-STATING."""
    bad = [d for d in derivations if incomplete_reason(d)]
    if not bad:
        # A per-run "0 repos missing" buries the real ones — `untracked_warnings`'
        # reason. This is the ONLY suppression left.
        return ()
    detail = ", ".join(f"{d.label} ({incomplete_reason(d)})" for d in bad)
    ok = ", ".join(d.label for d in derivations if may_replace_stored_rows(d))
    shared = authority_label_collisions(derivations)
    kept = (
        "Their existing rows are left untouched (the rebuild delete is scoped to "
        "what was read in FULL), so this run destroyed nothing it could not "
        "replace."
        if not shared else
        f"🔴 EXCEPT WHERE A LABEL IS SHARED: {', '.join(shared)} is ALSO produced "
        f"by a derivation that WAS read in full, and the delete binds LABELS, not "
        f"derivations — so those rows ARE deleted and replaced by the other "
        f"checkout's alone. Every OTHER named repo's rows are left untouched (the "
        f"rebuild delete is scoped to what was read in FULL)."
    )
    if not ok:
        return (
            f"🔴 NOTHING WAS READ COMPLETELY — all {len(derivations)} configured "
            f"repo(s) were incomplete, so this run speaks for none of them: "
            f"{detail}. No repo's stored rows were replaced: with nothing read in "
            f"FULL the delete scope is EMPTY, so a --rebuild is DOWNGRADED to a "
            f"plain upsert and a plain run never deletes — every stored row is "
            f"either refreshed by a document that DID read or left exactly as it "
            f"was. An unscoped "
            f"query's silence is NOT evidence these repos are silent, and a "
            f"section REMOVED from a doc keeps its stale row until some repo "
            f"reads in FULL again.",
        )
    return (
        f"🔴 PARTIAL INDEX — {len(bad)} of {len(derivations)} configured repo(s) "
        f"were NOT read completely, so this run does not speak for them: {detail}. "
        f"Read in FULL, and therefore rebuilt, covers only: {ok}. A query scoped to "
        f"a repo that contributed nothing will return an EMPTY "
        f"SCOPE, and an unscoped query's silence is NOT evidence those repos are "
        f"silent. " + kept,
    )


def rebuild_delete_labels(
    derivations: Sequence[RepoDerivation],
    stored: Sequence[str],
    *,
    scoped: bool,
    prune: bool = False,
) -> tuple[str, ...]:
    """The repo labels a `--rebuild` run is AUTHORITATIVE for — what it may delete.

    🔴 THIS REPLACES AN UNQUALIFIED `TRUNCATE`, AND THE OLD ONE WAS A MEASURED
    DATA-LOSS BUG. `handoff_index.py --repo ~/workspace/devrc --rebuild --write`
    issued `TRUNCATE initiatives.handoff_section` — EVERY repo — re-inserted only
    devrc, printed `wrote 968 section row(s) … (after TRUNCATE, one transaction)`
    and exited 0. homelab-talos's ~515 sections were gone, silently, from the
    exact command a human types to refresh one repo. Scoping the delete is chosen
    over refusing a scoped rebuild because it makes the NATURAL command correct
    rather than erroring at it.

    Two inputs decide the scope, and both are structural:

      * `scoped` — did the caller name repos with `--repo`? A scoped run speaks
        only for what it was pointed at.
      * `incomplete_reason(d)` — a repo this run did not read IN FULL is not a
        repo whose rows it may destroy, whether nothing came back (`unmeasured`)
        or only some of it did (`unreadable`). This is what makes
        `rebuild_refusal`'s new
        partial-tolerance safe: the absent `$CIVITAI` keeps its rows.

    🔴 A REBUILD NEVER DELETES ROWS FOR A REPO IT WAS NOT TOLD ABOUT. THAT IS A
    REVERSAL, AND THE THING IT REVERSES SHIPPED ONE ROUND AGO. An earlier version
    of this function let an UNSCOPED run also claim authority over "any stored
    label the config no longer names", reasoning that a repo dropped from the env
    handles would otherwise stay indexed forever. Two measurements killed it:

      * The unit's own environment sets only `$DEVRC` and `$HOMELAB` while a
        human `--dry-run` on this host measures FOUR repos. Once the timer is
        armed, its unscoped `--rebuild --write` classifies `datapacket-talos` and
        `civitai` as "disappeared from config" and deletes ~2,476 of ~4,008
        sections — 62% of the corpus — every 6h, exit 0, with `## warnings:
        none` printed above it, because from the unit's environment nothing IS
        unmeasured. An implicit garbage collection that runs on every timer tick
        is not something an operator can reason about.
      * CONFIGURED and STORED are two spellings of the same repo, and they can
        disagree. Table holds `civitai`; the checkout is renamed so
        `$CIVITAI=…/civitai-old`. The configured label is now `civitai-old`
        (UNMEASURED — `rebuild_refusal`'s own docstring names a renamed checkout
        as a cause), so stored `civitai` is "not configured at all" and was
        COLLECTED — while `partial_scope_warnings` printed, in the same
        transaction, that the unmeasured repo's rows were left untouched.

    So the collection is now an EXPLICIT `--prune`, which the timer does not
    pass. The harm it was buying against — a removed repo's rows lingering — is
    strictly smaller than silently destroying the majority of the corpus on a
    schedule, and it is recoverable by an operator running `--prune` on purpose.
    Lingering rows are also no longer SILENT: `orphan_label_warning` reports them
    on every `--write` run — the set lives in the table, which a dry-run never
    opens — so the stale-corpus case is loud rather than auto-collected.

    Three inputs decide the scope, and all three are structural:

      * `scoped` — did the caller name repos with `--repo`? A scoped run speaks
        only for what it was pointed at, and `main` rejects `--prune` with
        `--repo` as a usage error rather than guessing.
      * `incomplete_reason(d)` — a repo this run did not read IN FULL is not a
        repo whose rows it may destroy, whether nothing came back (`unmeasured`)
        or only some of it did (`unreadable`). This is what makes
        `rebuild_refusal`'s
        partial-tolerance safe: the absent `$CIVITAI` keeps its rows.
      * `prune` — an operator act, never the timer's. `rebuild_refusal` refuses a
        `--prune` run in which ANY repo came back INCOMPLETE — unmeasured OR
        holding a doc that would not read; the arm widened with the scope
        predicate and this bullet said `unmeasured` for one commit after it did.
        That is what makes the two-spellings collision above unreachable: it
        REQUIRES a configured-but-unmeasured label.

    🔴 THE INVARIANT THAT MAKES `partial_scope_warnings` TRUE: whenever that
    warning fires (some repos read in full, some not) and the run is allowed to
    write, the returned scope is exactly `measured`. Scoped and default runs
    return `measured` outright; a `--prune` run with any INCOMPLETE repo never
    reaches a write. Pinned as a seam test, not left as prose.

    🔴 THE EXPOSURE THAT USED TO BE RECORDED HERE IS CLOSED, AND CLOSING IT MOVED
    THE DELETE SCOPE. The note read: a repo that *measures* and whose every doc is
    UNREADABLE has its rows deleted and ZERO re-inserted, at rc 0. `unmeasured`
    was the only field this scope read, and it answers "did the ref resolve", not
    "did anything come back" — so a repo whose mainline LISTS handoff docs git
    cannot produce (`unmeasured is None`, `docs == 0`) landed in `measured` and
    was bound to the DELETE with nothing to put back.

    RE-MEASURED before the fix, on two repos (one healthy, one with every doc blob
    removed from `.git/objects`), `--rebuild --write`: rc 0, `DELETE …
    ['badrepo', 'goodrepo']`, two INSERTs, both for `goodrepo` — the broken
    repo's rows gone, nothing re-inserted, `partial_scope_warnings() == ()` and no
    PARTIAL notice on the success line. The only signal was one `⚠ UNREADABLE`
    stderr line.

    🔴 AND THE HAZARD WAS WIDER THAN THE FILING. The report described "every doc
    unreadable"; MEASURED on ONE repo with 2 committed docs and ONE blob removed:
    `unmeasured=None docs=1 unreadable=1`, scope `('zarfrepo',)`, rc 0, 2 of 4
    sections re-inserted — the second doc's stored rows deleted and never
    replaced, from a run that looks HEALTHIER than the total case because it
    writes rows. A guard scoped to the total case would have closed the fourth
    instance and left the shape open, which is what `incomplete_reason` exists to
    stop: the scope now asks whether this run holds a COMPLETE replacement, and
    ANY unreadable doc withholds authority.

    ⚠ WHAT THIS COSTS, STATED RATHER THAN DISCOVERED: a single corrupt blob
    freezes that repo's rows until the object store is repaired. The rows are
    PRESERVED, not lost, every readable doc is still upserted, and
    `partial_scope_warnings` says so on every output path — which is strictly the
    conservative direction. `--prune` additionally refuses while any repo is
    incomplete, for the reason `rebuild_refusal`'s prune arm gives. When NO repo
    is replaceable this returns `()` and `rebuild_downgrade_reason` turns the run
    into an upsert rather than letting `write` raise on the empty scope.

    ⚠ "SAYS SO ON EVERY OUTPUT PATH" IS TRUE NOW AND WAS NOT WHEN IT WAS FIRST
    WRITTEN. `partial_scope_warnings` returned `()` when EVERY repo was
    incomplete, so a `--write` run without `--rebuild` printed no PARTIAL notice
    at all. The suppression is gone; the claim is kept because it is now the
    property, not because it was asserted.

    🔴 THE RESIDUAL, RECORDED HERE RATHER THAN ONLY IN A REVIEW THREAD: THIS SCOPE
    IS A SET OF *LABELS*, AND TWO DERIVATIONS CAN SHARE ONE. `may_replace_stored_
    rows` is evaluated per DERIVATION; `DELETE_SQL` binds per LABEL. So
    `--repo ~/left/protorepo --repo ~/right/protorepo` with the right twin's doc
    blob removed yields scope `('protorepo',)` from the healthy twin and deletes
    BOTH twins' rows, re-inserting only the healthy one's — MEASURED, rc 0.
    `identity_collisions` does not catch it, because unreadable docs produce no
    sections to collide.

    ⚠ DELIBERATELY NOT FIXED IN THIS ROUND, and the reason is scope, not doubt:
    keying authority on something a label cannot alias moves this scope, the
    stored key and `handoff_search`'s `--repo` scoping together. What this round
    DID fix is the false REPORT it produced — see `authority_label_collisions`.
    Until then this is the honest statement of what the scope covers: a label one
    checkout read in FULL is a label this run will DELETE, even where a DIFFERENT
    checkout spells itself the same and could not be read.

    PURE, so the whole decision is testable without a database."""
    measured = {d.label for d in derivations if may_replace_stored_rows(d)}
    if scoped or not prune:
        return tuple(sorted(measured))
    configured = {d.label for d in derivations}
    disappeared = {r for r in stored if r not in configured}
    return tuple(sorted(measured | disappeared))


def orphan_labels(
    derivations: Sequence[RepoDerivation],
    stored: Sequence[str],
    *,
    scoped: bool,
    deleted: Sequence[str] = (),
) -> tuple[str, ...]:
    """Stored labels this run's CONFIG does not name AND did not delete. PURE.

    Empty for a scoped run: `--repo` says what to look at, never what the whole
    config is, so "not configured" is a question a scoped run cannot answer.

    🔴 THIS IS THE LOUD HALF OF REMOVING THE IMPLICIT PRUNE. Auto-collecting
    these was the 62%-of-the-corpus delete; NOT collecting them, silently, would
    just move the failure from data loss to a stale corpus every query keeps
    answering from. So they are REPORTED on every write, with the one command
    that removes them, and the operator decides.

    🔴 `deleted` IS THE RUN'S ACTUAL DELETE SCOPE, AND WITHOUT IT THE REPORT WAS
    FALSE ON THE ONE PATH THAT ACTS. This function used to read only the
    PRE-WRITE `stored` list, so a successful `--rebuild --prune --write` DELETEd
    `wibbleton-retired` in the same transaction and then printed "the table holds
    1 repo label(s) this config does not name: wibbleton-retired … They are NOT
    deleted … Remove them deliberately with `--rebuild --prune --write`" — the
    remedy for a state the run had just left, naming rows that no longer exist.

    It takes the BOUND SCOPE rather than a `prune` flag on purpose: the claim
    being made is "these rows are still there", and the only thing that can
    answer it is what was actually bound to the DELETE. A boolean would be a
    second spelling of that fact, free to drift from it. Without `--prune` the
    scope is the MEASURED labels, which are configured by construction, so
    subtracting it is a no-op — the non-prune report is unchanged."""
    if scoped:
        return ()
    configured = {d.label for d in derivations}
    gone = set(deleted)
    return tuple(sorted({r for r in stored if r not in configured and r not in gone}))


def orphan_label_warning(labels: Sequence[str]) -> tuple[str, ...]:
    """The sentence `orphan_labels` earns. Empty when there are none — a per-run
    "0 orphans" is `untracked_warnings`' problem: it buries the real ones.

    🔴 TWO THINGS IN THIS SENTENCE WERE WRONG, AND BOTH POINTED THE READER AT THE
    DESTRUCTIVE COMMAND. It used to open "Nothing will ever refresh them", which
    is a claim about EVERY host and this run can only speak for ITS OWN config —
    the index is shared, and the laptop's missing `$CIVITAI` does not stop the
    workbench refreshing `civitai` on its next tick. And it offered `--rebuild
    --prune --write` as the FIRST remedy with "re-add the handle" second, in
    exactly the state where the handle being unset is the likely cause: the tool
    suggested deleting the data before suggesting the fix that keeps it.

    🔴 PINNED AS A WHOLE NORMALISED STRING by the test, not by substrings. A
    guard on WORDS is walkable by REWORDING, and a mutant that changed "only
    ever" to "ALWAYS" survived three substring assertions. A cosmetic reword now
    fails the suite; that is the intended cost of a machine-readable claim.

    🔴 AND THE THIRD THING WRONG WITH IT: IT RECOMMENDED A COMMAND THE PRINTING
    STATE CAN MAKE IMPOSSIBLE. The tail said "Only when the repo is genuinely
    gone, remove the rows deliberately with `--rebuild --prune --write` (which
    refuses unless every handle is SET…)" — and "genuinely gone" is exactly the
    state where the handle CANNOT be set: `nix/agent-handles.nix`
    existence-guards each one on its directory, so a deleted checkout leaves it
    unset on EVERY host and `prune_config_refusal` then refuses everywhere,
    forever. The sentence named the one command its own parenthetical rules out.
    The remedy now splits on the question that decides it — does ANY host still
    have the checkout — and names the config change for the case where none does.
    Same defect and same fix as `prune_config_refusal`'s own remedy line: one bug
    printed in two places, which is why they were swept together."""
    if not labels:
        return ()
    return (
        f"⚠ ORPHANED LABELS — the table holds {len(labels)} repo label(s) THIS "
        f"config does not name: {', '.join(labels)}. A query can still answer "
        f"FROM them, and nothing on THIS host will refresh them — the index is "
        f"shared, so another host whose config DOES name them still can. They "
        f"are NOT deleted: a rebuild only ever deletes repos it was told about. "
        f"If the repo is meant to be indexed, RE-ADD ITS HANDLE — that is the "
        f"fix whenever the checkout still exists somewhere, and an unset handle "
        f"is the ordinary reason a label reads as unnamed here. Only when the "
        f"repo is genuinely gone do you remove the rows, and HOW depends on "
        f"whether any host still has the checkout. If one does, run `--rebuild "
        f"--prune --write` THERE: it refuses unless every handle is SET and "
        f"every repo MEASURES, because an absent or renamed checkout looks "
        f"exactly like a removed one. If the checkout is gone EVERYWHERE, no "
        f"host can ever satisfy that — the handles are existence-guarded, so an "
        f"absent checkout is an unset handle on all of them — and the fix is a "
        f"CONFIG change, not a command: drop the handle from "
        f"nix/agent-handles.nix and from handoff_index.REPO_ENV_HANDLES, ship "
        f"it, and then --prune.",
    )


def rebuild_plan_lines(
    derivations: Sequence[RepoDerivation], *, scoped: bool, prune: bool, write: bool,
    downgraded: bool = False,
) -> tuple[str, ...]:
    """What a `--rebuild` would DELETE, as far as this run can know it. PURE.

    🔴 THE PRE-FLIGHT COULD NOT SHOW THE ONE THING THE RUN DESTROYS. The delete
    scope was computed only inside `main`'s `--write` branch — it needed
    `store.repos()` — so `--dry-run`, which `nix/home.nix` documents as the thing
    to watch before arming the timer, was structurally incapable of printing it.
    `scripts/README.md`'s "a green dry-run is a real pre-flight" read wider than
    what dry-run actually checked.

    Half of that is fixed by the same change that fixed the delete itself: with
    the disappeared-from-config set behind `--prune`, a DEFAULT rebuild's scope
    is exactly the MEASURED labels, which is a fact about the derivation alone
    and needs no database. So dry-run now prints the real scope.

    ⚠ THE OTHER HALF IS STATED, NOT CLOSED. Under `--prune` the scope also
    includes stored labels this config does not name, and that set exists only in
    the table. Dry-run says so in those words rather than printing a scope that
    is missing rows — a pre-flight that quietly under-reports what a run deletes
    is worse than one that names its own blind spot.

    🔴 `write` IS HERE BECAUSE TWO OF THESE SENTENCES WERE WRITTEN FOR THE WRONG
    RUN. The blind-spot sentence said "a --dry-run CANNOT show it" during
    `--write` runs, where the scope IS printed a few lines later; and the
    no-prune sentence promised that an unnamed stored label "is REPORTED", which
    needs `store.repos()` — a read a dry-run never performs. That second one is
    the sharper error, because the branch two lines above names precisely this
    blind spot while its sibling asserts a report and then shows none.

    🔴 AND `write` ALONE WAS NOT ENOUGH, BECAUSE IT IS THE INTENT AND EVERY
    SENTENCE HERE DESCRIBES AN OUTCOME. `main` used to print this block ABOVE its
    gates, so a refusing run got a plan for a run that never ran. MEASURED, both
    at rc 4 with the store never opened: `--rebuild --prune --write` with one
    handle unset printed "this run opens the table, and the full bound scope is
    printed with the row count below" (recorded SQL log: `[]`); `--rebuild
    --write` with every repo unmeasured printed "is REPORTED (see ORPHANED LABELS
    below) and kept" above a run that emitted no such section. `--prune
    --dry-run`'s "The --write run prints the full bound scope" had the same
    falsity mode via a collision or a zero-row derivation.

    🔴 SO THIS FUNCTION HAS A CALL-SITE PRECONDITION, AND IT IS NOT DECORATION:
    **it must be called only after every gate has passed.** All four branches
    then describe a run that is going to happen, and the two that speak about a
    DIFFERENT run ("The --write run prints…") are sound because the gates are
    mode-identical — a dry-run reaching this point proves the `--write` run
    reaches it too. Pinned by `TestThePlanDescribesTheRunThatActuallyHappens`,
    which asserts the plan block is ABSENT from a refusing run of each shape:
    stating the precondition here and not testing it is how the last two rounds
    of this same defect got through."""
    # 🔴 THREE BUCKETS, NOT TWO, BECAUSE "NOT IN THE DELETE SCOPE" IS NOT ONE
    # STATE. A repo can be kept out because NOTHING came back (`unmeasured`) or
    # because only SOME of it did (`unreadable`) — different causes, different
    # remedies, and the second one is invisible in a two-bucket plan: it appears
    # in neither list, so a reader counts the labels, finds one missing, and has
    # no way to learn why. The pre-flight is the operator's only view of what a
    # run destroys, so a repo it silently omits is the same defect one layer up.
    #
    # 🔴 AND THE BUCKETS ARE A PARTITION ON `incomplete_kind`, NOT THREE HAND-ROLLED
    # PREDICATES OVER THE RAW FIELDS. This was the one consolidated site that still
    # re-derived: `measured` went through `may_replace_stored_rows` while `kept`
    # read `d.unmeasured is not None` and `incomplete` read `d.unmeasured is None
    # and d.unreadable`. Teach `incomplete_reason` a THIRD kind — the case its own
    # docstring says the design exists for — and a repo drops out of all three
    # buckets while `partial_scope_warnings` still names it, so the plan and the
    # warning disagree about the same run. Bucketing on the kind makes that
    # impossible to write; the `unenumerated` line makes it impossible to hide.
    by_kind: dict[str | None, list[RepoDerivation]] = {}
    for d in derivations:
        by_kind.setdefault(incomplete_kind(d), []).append(d)
    measured = tuple(sorted(d.label for d in by_kind.get(None, ())))
    kept = tuple(sorted(d.label for d in by_kind.get(INCOMPLETE_UNMEASURED, ())))
    incomplete = tuple(sorted(
        f"{d.label} ({incomplete_reason(d)})"
        for d in by_kind.get(INCOMPLETE_UNREADABLE, ())))
    unenumerated = tuple(sorted(
        f"{d.label} ({incomplete_reason(d)})"
        for kind, ds in by_kind.items() if kind not in (None, *INCOMPLETE_KINDS)
        for d in ds))
    # 🔴 `downgraded` IS `main`'s ACTUAL DECISION, PASSED IN, NOT RE-DERIVED HERE.
    # `measured` being empty is the same condition, and computing it twice is how
    # a plan comes to describe a run that did not happen — the defect the
    # gate-ordering note above records. It is a flag rather than a second call to
    # `rebuild_downgrade_reason` for the reason `orphan_labels` takes the BOUND
    # SCOPE rather than an `args.prune` flag: one decision, one owner.
    header = (
        "## rebuild delete scope — SKIPPED (the --rebuild is DOWNGRADED to an "
        "upsert; NOTHING is deleted)" if downgraded else "## rebuild delete scope"
    )
    out = [
        header,
        f"  DELETE (read in FULL, will be re-derived): {', '.join(measured) or '(none)'}",
        f"  KEPT (configured but UNMEASURED): {', '.join(kept) or '(none)'}",
        f"  KEPT (resolved, but a doc would not READ — this run cannot replace "
        f"their rows): {', '.join(incomplete) or '(none)'}",
    ]
    if unenumerated:
        # The partition, asserted where an operator can see it rather than in a
        # comment: a kind this plan does not enumerate must be REPORTED, never
        # silently dropped out of all three buckets above.
        out.append(
            f"  🔴 KEPT (UNCLASSIFIED — `incomplete_kind` returned a kind this "
            f"plan does not enumerate; the buckets above are NOT a complete view "
            f"of this run): {', '.join(unenumerated)}"
        )
    shared = authority_label_collisions(derivations)
    if shared:
        # 🔴 THE ONE PLACE A LABEL CAN HONESTLY APPEAR IN TWO BUCKETS. Authority is
        # demonstrated per DERIVATION and the DELETE binds LABELS, so a label two
        # checkouts share is BOTH deleted (on the healthy twin's authority) and
        # listed as kept (on the broken twin's). Listing it twice with no comment
        # is the false report; naming the collision is the true one. See
        # `authority_label_collisions` for why the underlying defect is its own
        # round.
        out.append(
            f"  🔴 SHARED LABEL — {', '.join(shared)} appears ABOVE IN TWO "
            f"BUCKETS because more than one configured checkout derives it, and "
            f"only some of them were read in FULL. The DELETE binds the LABEL, so "
            f"the rows of the checkout(s) that were NOT read are deleted too and "
            f"replaced by the other's alone. Give the checkouts distinct "
            f"directory names, or run them in separate --repo invocations."
        )
    if scoped:
        # 🔴 A SCOPED RUN REPORTS NO ORPHANS EITHER, and saying so here is what
        # keeps this branch distinguishable from the no-prune one below. A mutant
        # that removed this condition fell through to that branch and printed its
        # "a stored label … is REPORTED and kept" sentence — false twice over for
        # a scoped run, since `orphan_labels` returns () when scoped — with the
        # whole suite green.
        out.append(
            "  This run is SCOPED by --repo, so it claims authority over nothing else "
            "— no stored label outside the list above can be deleted, and none is "
            "REPORTED either: a scoped run was told what to look at, never what the "
            "whole config is."
        )
    elif prune and not write:
        out.append(
            "  --prune: ALSO deletes every stored label this config does not name. "
            "That set lives in the TABLE, not in this derivation, so a --dry-run "
            "CANNOT show it — this is the one part of the delete a pre-flight does "
            "not cover. The --write run prints the full bound scope."
        )
    elif prune:
        out.append(
            "  --prune: ALSO deletes every stored label this config does not name. "
            "That set lives in the TABLE, not in this derivation, so it is not in "
            "the list above — this run opens the table, and the full bound scope is "
            "printed with the row count below."
        )
    elif write:
        out.append(
            "  No --prune, so a stored label this config does not name is REPORTED "
            "(see ORPHANED LABELS below) and kept, never deleted. This list is the "
            "COMPLETE delete scope."
        )
    else:
        out.append(
            "  No --prune, so a stored label this config does not name is kept, "
            "never deleted. This list is the COMPLETE delete scope. Which labels "
            "those are lives in the TABLE, which a --dry-run never opens, so this "
            "run cannot list them — a --write run REPORTS them."
        )
    return tuple(out)


def derivation_warnings(derivations: Sequence[RepoDerivation]) -> tuple[str, ...]:
    """Every warning a run has to report, from ONE function.

    🔴 IT IS A FUNCTION BECAUSE THERE IS MORE THAN ONE OUTPUT PATH. The warning
    block lived only inside `render_derivation`, which `--json` REPLACES — so an
    agent using the machine surface (the surface most likely to be consumed
    without a human reading it) got rows and no durability-hole report, no
    identity collisions and no partial-index notice. A caveat spelled in one
    renderer is a caveat the other renderer does not have."""
    return (
        *(w for d in derivations for w in d.warnings),
        *identity_collisions([s for d in derivations for s in d.sections]),
        *partial_scope_warnings(derivations),
    )


def all_clear_blockers(derivations: Sequence[RepoDerivation]) -> tuple[str, ...]:
    """Why this run may NOT print the all-clear, beyond having no warnings. PURE.

    🔴 THE ALL-CLEAR ASSERTS SOMETHING ABOUT THE DISK SIDE AND WAS GATED ON THE
    REF SIDE. `render_derivation` read `elif total_docs:` — a count of documents
    found in the git REF — and then printed "every handoff doc on disk is also in
    it". Those are two different measurements, and the ref-side count cannot see a
    disk-side failure. MEASURED on one repo with a real durability hole present:
    readable → `🔴 DURABILITY HOLE — 1 handoff doc …`; `chmod 0o000 claudedocs/` →
    `## warnings: none — … every handoff doc on disk is also in it.` The
    uncommitted doc had not moved.

    So the sentence is now gated on the measurement it actually describes:

      * every repo's disk walk RAN and hid nothing (`DiskScan.complete`), and
      * the walks saw at least one path BETWEEN THEM — a set difference whose
        disk side is empty compared nothing, and "every handoff doc on disk is
        also in it" over zero docs is vacuously true, which is the shape
        `claude/RULES.md` calls worse than no check.

    The ref-side count stays as a third condition (it is `render_derivation`'s
    original one, and it is right — it just was not sufficient). Returning the
    REASONS rather than a bool is what lets the report say which measurement was
    missing instead of a bare NOT AN ALL-CLEAR.

    ⚠ ARMS 1 AND 3 ARE AGGREGATE; ARM 2 IS PER-REPO. That asymmetry is
    deliberate and this docstring used to obscure it by describing arm 3 in
    per-repo language ("the walk saw at least one path"). It sums across every
    derivation, and it has to: a repo with no `claudedocs/` directory at all is
    an ordinary, correct state, so a per-repo version of arms 1 and 3 would
    withhold the all-clear forever on any host indexing one — a permanently-red
    gate, which is the mistake `rebuild_refusal` already had to unwind once.
    Arm 2 is per-repo because a FAILED walk is not an ordinary state and the
    report has to name which repo hid something. The all-clear these gate is
    itself a single sentence about the whole run, so the aggregate reading is
    also the one it claims.

    ⚠ THE `incomplete` ARM CANNOT CHANGE `render_derivation`'S OUTPUT, AND THAT IS
    STATED BECAUSE A MUTATION SWEEP FOUND IT. Deleting the arm left the whole
    suite green: an incomplete walk always also produces an `⚠ UNSCANNABLE DISK`
    warning (both read the same `DiskScan.complete`), and any warning at all sends
    the renderer down the `if warnings:` branch, which never consults the
    blockers. So in the TEXT report it is a second copy of one predicate.

    It is kept, and it is not decoration, because `derivation_json` exposes
    `all_clear_blockers` as its own machine-readable field: a consumer asking
    "what stopped the all-clear" must get the complete answer from THAT list, not
    have to also parse prose out of `warnings`. Its coverage is therefore a
    contract of this pure function and is tested AS one — reached directly rather
    than through the renderer, which structurally cannot reach it."""
    out: list[str] = []
    if not sum(d.docs for d in derivations):
        out.append(
            "ZERO documents were scanned in any mainline ref, so the "
            "on-disk-vs-ref comparison had nothing to compare"
        )
    incomplete = [d.label for d in derivations if not d.disk.complete]
    if incomplete:
        out.append(
            "the working-tree walk did not complete for: "
            + ", ".join(incomplete)
            + " — so an uncommitted doc there would be INVISIBLE to this check"
        )
    if not sum(len(d.disk.paths) for d in derivations):
        # ⚠ DO NOT QUOTE THE ALL-CLEAR SENTENCE HERE. `TestTheAllClearIsEarned`
        # pins the WHOLE normalised sentence as absent from a non-all-clear
        # report — the guard against a reword walking past it — so echoing the
        # words inside the reason for withholding it makes that pin unsatisfiable.
        out.append(
            "the DISK side of the comparison saw ZERO handoff docs, so an "
            "all-clear about it would be vacuously true and measure nothing"
        )
    return tuple(out)


def derivation_json(derivations: Sequence[RepoDerivation]) -> dict:
    """The machine surface, carrying everything `render_derivation` prints.

    🔴 `--json` USED TO EMIT A BARE LIST OF ROWS AND NOTHING ELSE. The warning
    block lived inside `render_derivation`, which `--json` replaces — so the
    surface built FOR AN AGENT was the one surface with no durability-hole
    report, no identity collisions and no partial-index notice, on a CLI whose
    default mode is now dry-run. That is the same defect as a caveat spelled in
    one renderer: the consumer least able to notice the omission got it.

    Shape: an OBJECT, not a list. The rows moved under `rows` so the warnings can
    sit beside them; there are no consumers of the old list shape (P1, and the
    timer that would use it ships gated off), so this is a rename, not a
    migration. `all_clear` is a BOOLEAN a caller can branch on, with
    `all_clear_blockers` naming what was not measured when it is false — the
    same distinction the text renderer draws between "no findings" and "nothing
    examined"."""
    warnings = derivation_warnings(derivations)
    blockers = all_clear_blockers(derivations)
    return {
        "rows": [s.as_row() for d in derivations for s in d.sections],
        "warnings": list(warnings),
        "all_clear": not warnings and not blockers,
        "all_clear_blockers": list(blockers),
        "totals": {
            "docs": sum(d.docs for d in derivations),
            "sections": sum(len(d.sections) for d in derivations),
        },
        "repos": [
            {
                "label": d.label,
                "repo": d.repo,
                "ref": d.ref,
                "docs": d.docs,
                "sections": len(d.sections),
                "unmeasured": d.unmeasured,
                "untracked": list(d.untracked),
                # 🔴 `docs: 0` HAS TWO MECHANISMS AND ONLY THIS FIELD SEPARATES
                # THEM — the ref genuinely holds none, or every one of them
                # failed to read. A consumer reading the count alone draws the
                # first conclusion, which is the false sentence `handoff_search`
                # rc 7 used to print.
                "unreadable": list(d.unreadable),
                # 🔴 THE AUTHORITY DECISION IS PUBLISHED, NOT LEFT TO BE
                # RE-DERIVED. `unmeasured` and `unreadable` are both here, so a
                # consumer COULD compute this — and computing it is precisely
                # how the same predicate came to be spelled four different ways
                # inside this module. It is the value `rebuild_delete_labels`
                # actually reads, from `incomplete_reason`, so a machine consumer
                # and the DELETE cannot disagree.
                "rebuildable": may_replace_stored_rows(d),
                "incomplete_reason": incomplete_reason(d),
                # The KIND alongside the reason, for the same reason the reason is
                # here: the reason for an unmeasured repo is a free-form token, so
                # a consumer that needs to BRANCH would otherwise match on words.
                "incomplete_kind": incomplete_kind(d),
                "disk_scan_complete": d.disk.complete,
                "disk_paths_seen": len(d.disk.paths),
                "disk_scan_errors": list(d.disk.errors),
            }
            for d in derivations
        ],
    }


def render_derivation(derivations: Sequence[RepoDerivation]) -> str:
    lines = ["# handoff-index derivation"]
    total_docs = total_rows = 0
    for d in derivations:
        total_docs += d.docs
        total_rows += len(d.sections)
        ref = d.ref or "NO MAINLINE REF"
        lines.append(f"  {d.label:<24} {ref:<20} docs={d.docs:<5} sections={len(d.sections)}")
    lines.append(f"  TOTAL  docs={total_docs}  sections={total_rows}")
    warnings = derivation_warnings(derivations)
    blockers = all_clear_blockers(derivations)
    if warnings:
        lines.append("")
        lines.append("## warnings")
        lines.extend(f"  {w}" for w in warnings)
    elif not blockers:
        lines.append("")
        lines.append("## warnings: none — every repo resolved a mainline ref and every")
        lines.append("   handoff doc on disk is also in it.")
    else:
        # 🔴 THE ALL-CLEAR IS CONDITIONAL ON HAVING MEASURED THE THING IT CLAIMS.
        # Zero warnings is not the same fact as zero findings: a comparison that
        # never ran, or ran over nothing, produces no warnings either. Printing
        # "every handoff doc on disk is also in it" for that is a vacuous green —
        # a guard whose description claims coverage its body did not provide
        # (claude/RULES.md). A reader must not be able to mistake "nothing was
        # examined" for "no problems were found", so the reasons are NAMED.
        lines.append("")
        lines.append("## warnings: NOT AN ALL-CLEAR — this run did not measure what the")
        lines.append("   all-clear would claim. This is an absence of MEASUREMENT, not")
        lines.append("   an absence of findings:")
        lines.extend(f"     - {b}" for b in blockers)
    return "\n".join(lines)


#: Exit codes, named because the systemd unit's `OnFailure` fires on ANY non-zero
#: and a human reading the journal needs to know which failure it was.
#:   0  wrote, or dry-ran, cleanly
#:   2  nothing to do / contradictory flags (--write+--dry-run, --prune without
#:      --rebuild, --prune with --repo) — a usage error, no database touched
#:   4  the derivation could not be trusted; the write was REFUSED (F1's guard,
#:      and the --prune-with-an-UNMEASURED-repo guard)
#:   5  the derivation is internally inconsistent (an identity collision)
RC_OK = 0
RC_USAGE = 2
RC_REFUSED = 4
RC_COLLISION = 5


@contextlib.contextmanager
def _maildb_store() -> Iterator[PostgresSectionStore]:
    """The production store, behind a context manager so `main` has ONE seam.

    Injectable via `main(..., open_store=...)` so the CLI's control flow — the
    refusal guard, the exit codes, the transaction ordering — is testable against
    a fake connection in a sandbox with no Postgres, which is where the gate
    runs."""
    MailDB = import_maildb()
    with MailDB() as db:
        yield PostgresSectionStore(db.conn)


def main(argv: Sequence[str] | None = None, *, open_store=_maildb_store) -> int:
    ap = argparse.ArgumentParser(
        description="derive + write the handoff-doc section index (P1)",
        epilog=(
            "DRY-RUN IS THE DEFAULT: --write is required to touch the database. "
            "The index is DERIVED and DISPOSABLE; git is the system of record."
        ),
    )
    ap.add_argument("--repo", action="append", default=[],
                    help="repo root PATH to index (repeatable) — a filesystem path, "
                         "NOT the repo LABEL that handoff_search.py's --repo takes; "
                         "default: the $DEVRC/$HOMELAB/… handles that are set")
    ap.add_argument("--rebuild", action="store_true",
                    help="DELETE this run's in-scope repo rows and re-derive them from "
                         "scratch, in ONE transaction; the delete is scoped to the "
                         "repos this run MEASURED (never the whole table, and never a "
                         "repo it was not told about), and is refused if ALL repos "
                         "came back UNMEASURED or the derivation is empty")
    ap.add_argument("--prune", action="store_true",
                    help="ALSO delete stored rows for repo labels this config does not "
                         "name (an OPERATOR act — the timer never passes it). Requires "
                         "--rebuild, incompatible with --repo, and refused unless "
                         "EVERY repo handle is SET and every repo MEASURES: an unset "
                         "handle narrows the config silently and a renamed checkout is "
                         "indistinguishable from a removed one, so either way the run "
                         "would delete the wrong rows")
    ap.add_argument("--write", action="store_true",
                    help="actually write to the database (without it, this only "
                         "derives and reports — see --dry-run)")
    ap.add_argument("--dry-run", action="store_true",
                    help="derive and report; touch no database. THE DEFAULT — the "
                         "flag exists to say so explicitly in a script")
    ap.add_argument("--json", action="store_true", help="emit the derived rows as JSON")
    args = ap.parse_args(argv)

    # 🔴 THE DEFAULT IS THE SAFE ONE. `main([])` used to open a port-forward to
    # production and upsert every derived row — a bare invocation, the shape
    # somebody types to "see what it does", was a prod write. Mutating now needs
    # `--write`, which is one word in the unit's ExecStart and a deliberate act
    # everywhere else. Asking for BOTH is a contradiction, not a preference, and
    # guessing which one the caller meant is how a "dry run" writes.
    if args.write and args.dry_run:
        print("handoff-index: --write and --dry-run contradict each other; pick one.",
              file=sys.stderr)
        return RC_USAGE

    # 🔴 `--prune` IS REJECTED, NEVER REINTERPRETED. Both of these are cases where
    # the flag has no coherent meaning, and the tempting "just ignore it" is how a
    # destructive flag becomes decoration in one argv and live in the next.
    #   * with --repo the run was told what to LOOK at and never what the whole
    #     config is, so "a label the config does not name" is unanswerable — and
    #     answering it from a partial config would delete every repo the caller
    #     did not happen to list;
    #   * without --rebuild there is no delete for it to widen.
    #
    # 🔴 THE --repo CHECK RUNS FIRST, AND THE ORDER IS THE MESSAGE. Both exit
    # RC_USAGE, so the order decides only which sentence the operator reads — and
    # `--prune --repo` with no `--rebuild` used to answer "pass --rebuild", which
    # names the flag whose ADDITION would leave the argv still wrong and reads as
    # an instruction to retry with the dangerous combination. The --repo conflict
    # is the one that survives adding --rebuild, so it is the one to say.
    if args.prune and args.repo:
        print("handoff-index: --prune and --repo contradict each other. --prune "
              "deletes every stored label THE CONFIG does not name, and a --repo run "
              "was never told what the config is — treating its argv as the config "
              "would delete every repo you did not list. Re-run --prune unscoped.",
              file=sys.stderr)
        return RC_USAGE
    if args.prune and not args.rebuild:
        print("handoff-index: --prune only widens a --rebuild's delete; it does "
              "nothing on its own. Pass --rebuild, or drop --prune.",
              file=sys.stderr)
        return RC_USAGE

    # 🔴 READ THE HANDLE SET IN THE SAME BREATH AS THE CONFIG IT PRODUCES, so the
    # gate below cannot be reasoning about a different environment from the one
    # `default_repos()` just read. `--prune` is unscoped by the check above, so
    # under `--prune` these two lines describe exactly one thing.
    config_unset = unset_repo_handles()
    repos = [(r, Path(r).name) for r in args.repo] or default_repos()
    if not repos:
        print(
            "handoff-index: no repos to index. Pass --repo, or set one of: "
            + ", ".join(f"${h}" for h in REPO_ENV_HANDLES),
            file=sys.stderr,
        )
        return RC_USAGE

    # 🔴 EXPLICIT `--repo` IS WHAT MAKES A RUN SCOPED, and the distinction decides
    # what a `--rebuild` may delete. A run told which repos to look at speaks only
    # for those; a run that read the CONFIGURED set (`default_repos`) is
    # authoritative over that config — never, any more, over labels it was not
    # told about, which is what `--prune` now opts into. See
    # `rebuild_delete_labels`.
    scoped = bool(args.repo)
    derivations = [derive_repo(path, label=label) for path, label in repos]
    rows = [s for d in derivations for s in d.sections]

    # 🔴 `--json` KEEPS STDOUT PURE. Everything else this function prints goes to
    # stderr in that mode. `--json --write` used to emit the JSON document and
    # then the prose success line on the SAME stream, so `json.loads(stdout)`
    # raised on a run that had worked perfectly — the machine surface, which is
    # the one consumed without a human reading it, was the one that could not be
    # parsed. `say` is the one place that choice is made, so a new line cannot
    # reintroduce it by being added in the wrong place.
    def say(text: str) -> None:
        print(text, file=sys.stderr if args.json else sys.stdout)

    if args.json:
        print(json.dumps(derivation_json(derivations), indent=2, sort_keys=True))
    else:
        print(render_derivation(derivations))

    # 🔴 EVERY GATE IS EVALUATED BEFORE THE `--write` BRANCH, and that is the fix
    # for a pre-flight that passed for a config the real run refused.
    # `nix/home.nix` tells the operator to watch a `--dry-run` before arming the
    # timer; MEASURED, `--repo /nope --rebuild` (dry-run) exited 0 with no
    # "REFUS" text anywhere while the identical argv plus `--write` exited 4. A
    # documented pre-flight that cannot fail is not a pre-flight. So the gates run
    # in both modes, report identically, and return the SAME code — the only
    # difference between the two runs is whether a store is opened.
    #
    # 🔴 EVERY REFUSAL EXITS NON-ZERO. The unit carries
    # `OnFailure=notify-failure@%n.service`, so a run that refuses must not return
    # 0 — a silent success is how an empty index went unnoticed. The checks run
    # BEFORE the store is opened: there is no reason to take a port-forward for a
    # write that is not going to happen.
    gate: tuple[int, list[str]] | None = None
    collisions = identity_collisions(rows)
    if collisions:
        gate = (RC_COLLISION, [
            *collisions,
            "handoff-index: refusing to write a derivation that would silently "
            "lose documents.",
        ])
    elif args.rebuild:
        # 🔴 THE REFUSAL IS REBUILD-SCOPED, DELIBERATELY, AND THE BOUNDARY IS
        # STATED BECAUSE IT LOOKS LIKE AN OVERSIGHT. A non-rebuild run over an
        # UNMEASURED repo still exits 0: it DESTROYS nothing (the upsert simply
        # refreshes fewer rows) and the warning is printed either way. What is
        # unrecoverable is DELETING on a derivation that could not be measured at
        # all, and that is what refuses. The timer passes `--rebuild`, so the
        # scheduled path is the covered one.
        #
        # 🔴 TWO PRUNE GUARDS, AND THEY ARE NOT REDUNDANT — see
        # `prune_config_refusal`. That one asks "is this config as WIDE as the
        # corpus" (an UNSET handle); `rebuild_refusal` asks "did every configured
        # repo READ" (an UNMEASURED one). The sets are disjoint by construction:
        # an unset handle produces no derivation, so it can never be unmeasured,
        # and the narrowest configs produce ZERO unmeasured repos. The config
        # check runs first because it is the precondition for `--prune`'s whole
        # premise, and the two messages open with different tokens (UNSET vs came
        # back UNMEASURED) so an assertion cannot confuse them.
        refusal = (
            prune_config_refusal(config_unset) if args.prune else None
        ) or rebuild_refusal(derivations, rows, prune=args.prune)
        if refusal:
            gate = (RC_REFUSED, [f"🔴 {refusal}"])

    if gate is not None:
        rc, lines = gate
        for line in lines:
            print(line, file=sys.stderr)
        if not args.write:
            print("handoff-index: this was a DRY RUN — nothing was written, and the "
                  "SAME check would stop the --write run with this same exit code. "
                  "Fix it before arming the timer.", file=sys.stderr)
        return rc

    # 🔴 THE PRE-FLIGHT SHOWS THE DELETE SCOPE, and it names its own blind spot.
    # See `rebuild_plan_lines`: printed in BOTH modes, because the thing an
    # operator is told to watch before arming the timer is a --dry-run, and what a
    # rebuild DESTROYS was the one fact that mode could not reach.
    #
    # 🔴 IT IS PRINTED *AFTER* THE GATE, AND THAT ORDERING IS WHAT MAKES THE PLAN'S
    # SENTENCES TRUE. They were emitted above the gate block and describe what the
    # run WILL DO, so on a refusing run every one of them was a statement about a
    # run that never happened. MEASURED, `--rebuild --prune --write` with three
    # handles set and one unset: stdout printed "this run opens the table, and the
    # full bound scope is printed with the row count below" — recorded SQL log
    # `[]`, rc 4, no bound scope, no row count. And `--rebuild --write` with every
    # repo unmeasured printed "is REPORTED (see ORPHANED LABELS below) and kept"
    # above a run that emitted no ORPHANED LABELS section and exited 4.
    #
    # The alternative — keep the position and pass a "this run will actually
    # write" boolean instead of `args.write` — was rejected: that boolean IS "no
    # gate fired", so it would be a second spelling of the gate's own verdict, free
    # to drift from it, which is exactly the argument `orphan_labels` makes for
    # taking the BOUND SCOPE rather than an `args.prune` flag. Ordering makes every
    # branch sound at once instead of one flag per claim, INCLUDING the two that
    # speak about a DIFFERENT run: because the gates are mode-identical (the
    # comment above, pinned by `test_the_gate_fires_in_DRY_RUN_too`), a dry-run
    # that reaches this point proves the `--write` run reaches it too — so "The
    # --write run prints the full bound scope" is now true by construction rather
    # than by hope.
    # 🔴 THE DOWNGRADE IS DECIDED ONCE, BEFORE THE PLAN PRINTS, AND `rebuild`
    # MEANS THIS AND NOT `args.rebuild` FROM HERE DOWN. The pre-flight is the
    # operator's only view of what the run destroys, so it has to describe the run
    # that is going to happen — a plan headed "rebuild delete scope" above a run
    # that takes no DELETE is the same defect the gate-ordering above fixed. And
    # because the delete scope is computed ONLY when `rebuild` is true, an empty
    # scope can never reach `store.write`, whose `ValueError` on one is the
    # backstop rather than the mechanism.
    downgrade = rebuild_downgrade_reason(derivations) if args.rebuild else None
    rebuild = args.rebuild and downgrade is None
    if args.rebuild:
        say("")
        if downgrade is not None:
            say(f"🔴 {downgrade}")
            say("")
        for line in rebuild_plan_lines(derivations, scoped=scoped, prune=args.prune,
                                       write=args.write, downgraded=downgrade is not None):
            say(line)

    if not args.write:
        say("\n(no --write: nothing was written. Dry-run is the DEFAULT; pass "
            "--write to touch the database.)")
        return RC_OK

    with open_store() as store:
        store.ensure_schema()
        # 🔴 READ THE STORED LABELS BEFORE DECIDING WHAT TO DELETE. Two different
        # consumers need them and only one may destroy anything: `--prune` widens
        # the delete scope with the labels the config no longer names, and
        # `orphan_labels` REPORTS that same set on every `--write` run (never on
        # a dry-run — this read is the whole reason, and `rebuild_plan_lines`
        # says so where the pre-flight would otherwise imply otherwise). Without
        # --prune the scope does not depend on this read at all — the report
        # still does.
        stored = tuple(store.repos())
        labels = (
            rebuild_delete_labels(derivations, stored, scoped=scoped, prune=args.prune)
            if rebuild else ()
        )
        n = store.write(rows, rebuild=rebuild, rebuild_labels=labels)
    scope_note = (
        f" (after DELETE of {len(labels)} repo label(s): {', '.join(labels)} — "
        f"one transaction)" if rebuild else
        " (--rebuild DOWNGRADED to an upsert — nothing was deleted)"
        if downgrade is not None else ""
    )
    partial = partial_scope_warnings(derivations)
    # 🔴 THE SUCCESS LINE ITSELF SAYS PARTIAL. It is the one line a scripted
    # caller is most likely to read alone, and `wrote 968 section row(s)` reads as
    # a complete index whether or not it is one.
    say(f"\nwrote {n} section row(s) to {TABLE}{scope_note}"
        # 🔴 NOT "contributed nothing", WHICH IS WHAT THIS SAID AND IS TRUE ONLY
        # OF THE UNMEASURED HALF. A repo kept out of the delete scope because one
        # of its docs would not read still contributes every doc that DID read.
        #
        # 🔴 AND NOT "and kept their old rows" EITHER, WHICH IS A CLAIM THIS LINE
        # CANNOT MAKE. The delete binds LABELS and authority is per DERIVATION, so
        # a repo that shares its label with a healthy checkout did NOT keep its old
        # rows — see `authority_label_collisions`. The warning it points at knows
        # which case this is; the one-liner does not, so it points rather than
        # asserts.
        # …and it points at "the warning above" rather than naming it PARTIAL
        # INDEX: when NOTHING was read completely that warning opens with a
        # different token, and a pointer at a heading that is not on the page is
        # how a reader concludes there is no warning to find.
        + ("\n🔴 THIS INDEX IS PARTIAL — see the 🔴 warning above; some configured "
           "repos were not read COMPLETELY, and that warning says which of their "
           "rows were kept."
           if partial else ""))
    for line in partial:
        print(line, file=sys.stderr)
    # 🔴 A REPO THE CONFIG NO LONGER NAMES IS REPORTED, NEVER COLLECTED. This is
    # the other half of removing the implicit prune: not deleting them silently
    # would only trade a data-loss bug for a stale-corpus one. Printed AFTER the
    # write so it sits beside the row count a reader is checking, and to stderr
    # like every other warning.
    #
    # 🔴 `deleted=labels` IS THE FIX FOR A REPORT THAT CONTRADICTED THE
    # TRANSACTION ABOVE IT. `stored` is the PRE-write read, so a successful
    # `--prune` deleted a label and then printed "They are NOT deleted … Remove
    # them deliberately with `--rebuild --prune --write`" about the rows it had
    # just removed. Passing the BOUND DELETE SCOPE — not an `args.prune` flag —
    # keeps the sentence keyed on what actually happened.
    for line in orphan_label_warning(
            orphan_labels(derivations, stored, scoped=scoped, deleted=labels)):
        print(line, file=sys.stderr)
    return RC_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
