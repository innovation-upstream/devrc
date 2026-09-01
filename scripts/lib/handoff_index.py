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

⚠ CORPUS SIZE, AT THE SCOPE EACH FIGURE WAS ACTUALLY MEASURED. Measured HERE,
2026-09-01, by running this module: **devrc 94 docs / 959 sections** off
`origin/main`, **homelab-talos 54 docs / 506 sections** off `origin/trunk`. A
wider figure of ~424 docs / 8.6 MB over four repos (adding two client checkouts)
comes from the brief that commissioned this work and was NOT re-derived here —
quote it as second-hand or re-run `--dry-run` against those repos.

WHAT THIS MODULE IS NOT
-----------------------
🔴 NOT A SYSTEM OF RECORD. The index is DERIVED and DISPOSABLE; git is the record.
`--rebuild` truncates and re-derives from scratch, and that must always remain a
safe operation. Nothing may ever be stored here that cannot be re-derived from a
git ref, and no consumer may treat a row as authoritative over the doc it came
from.


WHY THE CORPUS COMES FROM GIT REFS AND NOT THE WORKING TREE
-----------------------------------------------------------
Measured 2026-09-01: `git worktree list` in devrc alone returns **143** entries.
A working-tree scan therefore indexes: a doc as it exists mid-edit in somebody's
branch, the SAME doc N times over N worktrees, and stale orphan copies whose
content matches an older commit. None of those is reproducible, and two runs an
hour apart would disagree for reasons that have nothing to do with what anyone
wrote. (143 is devrc's count only — the box-wide figure is larger and is not
measured here. The argument does not depend on which number it is.)

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

⚠ `scripts/handoff-audit.py` (open PR #1064) reads the SAME corpus and does NOT
fit here: it globs `claudedocs/handoff-*.md` off DISK, which is the working-tree
source this module exists to avoid, and its parser is `skill-audit.py`'s
byte/heading walk aimed at BUDGET measurement rather than at retrieval units. The
overlap is the corpus, not the reader. Nothing is duplicated: it borrows
skill-audit's walk, this borrows handoff_doc's.


CONTRACT SUMMARY
----------------
    resolve_mainline(repo)                    -> (ref | None, ladder)
    handoff_paths_in_ref(repo, ref)           -> tuple[str, ...]
    doc_text_at_ref(repo, ref, path)          -> str | None
    handoff_paths_on_disk(repo)               -> tuple[str, ...]
    untracked_docs(on_disk, tracked)          -> tuple[str, ...]        PURE
    untracked_warnings(repo_label, paths)     -> tuple[str, ...]        PURE
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Protocol, Sequence

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
    "SectionStore",
    "MemorySectionStore",
    "PostgresSectionStore",
    "resolve_mainline",
    "handoff_paths_in_ref",
    "doc_text_at_ref",
    "handoff_paths_on_disk",
    "untracked_docs",
    "untracked_warnings",
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


@dataclass
class RepoDerivation:
    """What one repo contributed, and what could NOT be measured about it."""

    repo: str
    label: str
    ref: str | None = None
    ladder: tuple[str, ...] = ()
    sections: list[Section] = field(default_factory=list)
    docs: int = 0
    untracked: tuple[str, ...] = ()
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Git — the corpus source
# --------------------------------------------------------------------------- #


def _git(repo: str | Path, args: Sequence[str]) -> str | None:
    """stdout, or None if git refused. Never raises, never takes the index lock.

    `GIT_OPTIONAL_LOCKS=0` for `git_mainline._git`'s reason: a concurrent agent in
    the same checkout is the normal case in these repos, and a helper that can
    block someone else's commit is not read-only in the way that matters."""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
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


def handoff_paths_in_ref(repo: str | Path, ref: str) -> tuple[str, ...]:
    """`claudedocs/handoff-*.md` paths present in `ref`, sorted.

    🔴 THE FILTER IS APPLIED IN PYTHON, NOT AS A GIT PATHSPEC. `git ls-tree`
    pathspecs are matched by git's own glob rules and a `*` there does not mean
    what a shell `*` means for a path containing a slash; enumerating the
    directory and matching the BASENAME is the version whose semantics this file
    can state. `-z` because a path may contain anything but NUL."""
    out = _git(repo, ["ls-tree", "-r", "--name-only", "-z", ref, "--", HANDOFF_DIR])
    if out is None:
        return ()
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


def handoff_paths_on_disk(repo: str | Path) -> tuple[str, ...]:
    """`claudedocs/handoff-*.md` paths in the WORKING TREE, repo-relative, sorted.

    Read ONLY to compute the untracked-doc report. Nothing derived from this
    function is ever indexed — see `untracked_docs`."""
    base = Path(repo) / HANDOFF_DIR
    try:
        found = [p for p in base.glob(HANDOFF_GLOB) if p.is_file()]
    except OSError:
        return ()
    out = []
    for p in found:
        try:
            out.append(p.relative_to(Path(repo)).as_posix())
        except ValueError:  # pragma: no cover - glob is rooted at repo
            continue
    return tuple(sorted(out))


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
    """`claudedocs/handoff-limewire-comps.md` -> `limewire-comps`.

    The identity half of the table's UNIQUE index, together with `repo`. Derived
    from the FILENAME rather than from an H1, because the filename is what
    `/handoff` controls and an H1 is prose an updating session rewrites."""
    name = Path(doc_path).name
    if name.endswith(".md"):
        name = name[: -len(".md")]
    if name.startswith("handoff-"):
        name = name[len("handoff-") :]
    return name


#: An ISO date anywhere in the doc's H1/preamble. `handoff_doc._TOPIC_DATE` also
#: admits a compact `YYYYMMDD`; this wants a value a `date` column can take, so
#: only the hyphenated form is accepted and the compact one falls through to the
#: filename rule below.
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def doc_date_for(doc_path: str, text: str) -> str | None:
    """The doc's topic date as `YYYY-MM-DD`, or None.

    Order — PREAMBLE FIRST, filename second. `/handoff`'s template writes
    `# Handoff: <topic> — <date>`, so the preamble is the authored statement;
    a date in the filename is a convention only some docs follow. Neither is
    validated as a real calendar date beyond its shape: this is a sort key and a
    display field, and a `date` column will reject a genuinely impossible value
    at write time rather than this function guessing.

    🔴 THE SEARCH IS BOUNDED TO THE PREAMBLE, never the whole document. Every
    handoff doc quotes dozens of dates in its body (`MEASURED 2026-08-29 …`), and
    a whole-document search would return whichever one happened to come first."""
    _fm, body = handoff_doc.split_front_matter(text)
    preamble, _secs = handoff_doc.split_sections(body)
    for source in (preamble, Path(doc_path).name):
        m = _ISO_DATE.search(source)
        if m:
            return m.group(0)
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
        out.warnings.append(f"⚠ UNMEASURED — {name}: no such directory ({root}); contributed 0 rows")
        return out
    ref, ladder = resolve_mainline(root)
    out.ladder = ladder
    if ref is None:
        out.warnings.append(
            f"⚠ UNMEASURED — {name}: no mainline ref resolved; tried "
            f"{', '.join(ladder) or '(nothing)'}. Contributed 0 rows — this is NOT "
            f"'the repo has no handoff docs'."
        )
        return out
    out.ref = ref
    sha = ref_commit_sha(root, ref)
    tracked = handoff_paths_in_ref(root, ref)
    out.untracked = untracked_docs(handoff_paths_on_disk(root), tracked)
    out.warnings.extend(untracked_warnings(name, out.untracked))
    for path in tracked:
        text = doc_text_at_ref(root, ref, path)
        if text is None:
            out.warnings.append(f"⚠ UNREADABLE — {name}: `git show {ref}:{path}` failed; skipped")
            continue
        out.docs += 1
        out.sections.extend(sections_for_doc(name, path, text, commit_sha=sha))
    return out


def default_repos() -> list[tuple[str, str]]:
    """`[(path, label), ...]` from the `.zshenv` handles that are SET and exist.

    An unset handle contributes nothing, silently — that is a host without that
    checkout, which is an ordinary state, not a fault. A handle that is set and
    does NOT exist is left in: `derive_repo` reports it as UNMEASURED, which is
    the loud direction for the case that is actually wrong."""
    out: list[tuple[str, str]] = []
    for handle in REPO_ENV_HANDLES:
        raw = os.environ.get(handle)
        if not raw:
            continue
        out.append((raw, Path(raw).name))
    return out


# --------------------------------------------------------------------------- #
# The store seam
# --------------------------------------------------------------------------- #


class SectionStore(Protocol):
    """🔴 THE SEAM THAT KEEPS THE TESTS HERMETIC. The gate runs in a nix sandbox
    with no cluster and no Postgres, so every test must be able to exercise the
    derivation, the query plumbing and the reporting without one. Two
    implementations satisfy this protocol and BOTH are production code — the
    memory backend is what makes `handoff_search.py --offline` work on a laptop
    with no kubeconfig, not a test double."""

    def stats(self) -> IndexStats: ...

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

    def stats(self) -> IndexStats:
        docs = {(r.repo, r.slug) for r in self._rows}
        return IndexStats(indexed_docs=len(docs), indexed_sections=len(self._rows))

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
        want_sections = set(sections)
        hits: list[Hit] = []
        for row in self._rows:
            if repo is not None and row.repo != repo:
                continue
            if want_sections and row.section not in want_sections:
                continue
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


class PostgresSectionStore:
    """The indexed backend: `ts_rank` over the generated `tsv`, GIN-backed.

    🔴 NOT EXERCISED BY ANY TEST, AND THAT IS STATED RATHER THAN PAPERED OVER.
    The gate runs in a nix sandbox with no cluster and no Postgres, so every claim
    about THIS class is a claim about code that has been read, not run. What IS
    covered hermetically: the SQL text this class builds (pinned), the boost table
    it shares with the memory backend, the row shape it inserts, and every caller
    path above it. What is NOT: that Postgres accepts the DDL, that the generated
    column is legal, that `ts_rank` orders as expected, or that a real query
    returns anything at all."""

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

    def truncate(self) -> None:
        """Drop every row. Safe BY DESIGN — the index is derived and disposable,
        and `--rebuild` is the supported way to change the derivation. git remains
        the system of record; nothing is lost that a re-run cannot rebuild."""
        with self._conn.cursor() as cur:
            cur.execute(f"TRUNCATE {TABLE}")
        self._conn.commit()

    def upsert(self, rows: Sequence[Section]) -> int:
        """Insert or refresh rows on the `(repo, slug, section, ordinal)` identity.

        ON CONFLICT DO UPDATE rather than delete-then-insert so a run that dies
        mid-write leaves the previous generation intact rather than a half-empty
        table. 🔴 A doc that SHRINKS (a section removed) leaves its old high
        ordinals behind — that is what `--rebuild` is for, and it is why the
        timer's unit runs `--rebuild`."""
        cols = [
            "repo", "slug", "doc_path", "doc_date", "commit_sha", "section",
            "ordinal", "heading", "body", "forcing_kind", "clawgate_task",
        ]
        placeholders = ", ".join(["%s"] * len(cols))
        updates = ", ".join(
            f"{c} = EXCLUDED.{c}" for c in cols if c not in ("repo", "slug", "section", "ordinal")
        )
        sql = (
            f"INSERT INTO {TABLE} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT (repo, slug, section, ordinal) DO UPDATE SET {updates}"
        )
        n = 0
        with self._conn.cursor() as cur:
            for row in rows:
                data = row.as_row()
                cur.execute(sql, [data[c] for c in cols])
                n += 1
        self._conn.commit()
        return n

    # -- read -------------------------------------------------------------- #

    STATS_SQL = (
        f"SELECT count(DISTINCT (repo, slug)), count(*) FROM {TABLE}"
    )

    def stats(self) -> IndexStats:
        with self._conn.cursor() as cur:
            cur.execute(self.STATS_SQL)
            row = cur.fetchone()
        return IndexStats(indexed_docs=int(row[0]), indexed_sections=int(row[1]))

    @staticmethod
    def search_sql(*, repo: bool, sections: bool) -> str:
        """The ranked query, as text, so a test can pin it without a database.

        `plainto_tsquery` and not `websearch_to_tsquery`: the caller is `/resume`
        and subagents passing a phrase, not a human typing search operators, and
        `plainto_tsquery` ANDs the terms — which is the behaviour that makes a
        two-word query narrow rather than widen."""
        where = ["tsv @@ q"]
        if repo:
            where.append("repo = %s")
        if sections:
            where.append("section = ANY(%s)")
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


def render_derivation(derivations: Sequence[RepoDerivation]) -> str:
    lines = ["# handoff-index derivation"]
    total_docs = total_rows = 0
    for d in derivations:
        total_docs += d.docs
        total_rows += len(d.sections)
        ref = d.ref or "NO MAINLINE REF"
        lines.append(f"  {d.label:<24} {ref:<20} docs={d.docs:<5} sections={len(d.sections)}")
    lines.append(f"  TOTAL  docs={total_docs}  sections={total_rows}")
    warnings = [w for d in derivations for w in d.warnings]
    if warnings:
        lines.append("")
        lines.append("## warnings")
        lines.extend(f"  {w}" for w in warnings)
    else:
        lines.append("")
        lines.append("## warnings: none — every repo resolved a mainline ref and every")
        lines.append("   handoff doc on disk is also in it.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="derive + write the handoff-doc section index (P1)",
        epilog="The index is DERIVED and DISPOSABLE. git is the system of record.",
    )
    ap.add_argument("--repo", action="append", default=[],
                    help="repo root to index (repeatable); default: the $DEVRC/$HOMELAB/… handles that are set")
    ap.add_argument("--rebuild", action="store_true",
                    help="TRUNCATE the table and re-derive from scratch")
    ap.add_argument("--dry-run", action="store_true",
                    help="derive and report; touch no database")
    ap.add_argument("--json", action="store_true", help="emit the derived rows as JSON")
    args = ap.parse_args(argv)

    repos = [(r, Path(r).name) for r in args.repo] or default_repos()
    if not repos:
        print(
            "handoff-index: no repos to index. Pass --repo, or set one of: "
            + ", ".join(f"${h}" for h in REPO_ENV_HANDLES),
            file=sys.stderr,
        )
        return 2

    derivations = [derive_repo(path, label=label) for path, label in repos]
    rows = [s for d in derivations for s in d.sections]

    if args.json:
        print(json.dumps([s.as_row() for s in rows], indent=2, sort_keys=True))
    else:
        print(render_derivation(derivations))

    if args.dry_run:
        print("\n(--dry-run: nothing was written)")
        return 0

    MailDB = import_maildb()
    with MailDB() as db:
        store = PostgresSectionStore(db.conn)
        store.ensure_schema()
        if args.rebuild:
            store.truncate()
        n = store.upsert(rows)
    print(f"\nwrote {n} section row(s) to {TABLE}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
