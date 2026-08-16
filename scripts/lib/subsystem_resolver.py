"""Map changed repo-relative paths onto `/analyze-service` index entries.

P0 of the derived session→subsystem association described in
`claudedocs/decision-subsystem-store-rejected-2026-08-11.md` → "What replaced the
premise: derived session association". This module is the WHOLE of P0: a pure,
source-agnostic function plus a thin disk loader. It emits nothing, knows nothing
about ClickHouse, and has never heard of Claude Code or opencode — P1 feeds it
paths from either.

🔴 THIS FILE IS THE EXECUTABLE AUTHORITY FOR THE REF-NORMALIZATION AND
RESOLUTION RULES.
`claude/skills/analyze-service/SKILL.md` states the same rules in prose, for a reader
that is an LLM rather than an interpreter, so the predicate necessarily exists at
two sites — and `claude/RULES.md` → "One rule, one place" says a duplicated
predicate regenerates the same bug at both. It cannot be literally deduplicated
(you cannot import a function into a markdown file), so it is made DETECTABLE
instead:

  * this module is named in that skill as the authority;
  * `scripts/tests/test_subsystem_resolver.py` → `TestCommandDocIsPinned` holds
    the skill's normative sentences as literal substrings AND the behaviour
    each one asserts. Reword the prose without touching the code (or vice versa)
    and that test goes red naming the sentence that moved.

That matters more here than usual because the failure is SILENT: if the two
implementations drift, associations simply stop matching, and a zero reads as
"this subsystem had no sessions" rather than "the matcher is broken".

WHAT IT MATCHES ON, AND WHY NOT A STORED PATH
---------------------------------------------
The index deliberately never persists WHERE a subsystem lives — location is
re-derived live on every recon (`analyze-service.md`: "Phase 1 never caches
location"). So association matches PATH COMPONENTS against each entry's slug and
its `aliases:`, after normalization. No `paths:` field is added to the schema and
none is inferred and cached here.

Matching is EXACT normalized-component equality, never substring. `redis` does
not match a component `redis-wedge-relief`, and `handoff-redis-2026-01-01.md`
does not match `redis`. Substring matching would make every short slug (`db`,
`s3`, `rum`) match most of the tree.

CONTRACT SUMMARY
----------------
    normalize_ref(raw)                -> str            (the shared predicate)
    split_kind(ref)                   -> (slug, kind|None)
    SubsystemEntry.from_mapping(m)    -> SubsystemEntry (raises MalformedEntryError)
    build_index(mappings, *, on_malformed=RAISE)
                                      -> SubsystemIndex (raises MalformedEntryError
                                         under RAISE; collects into
                                         `.malformed` under COLLECT)
    resolve_ref(ref, index, scope)    -> SubsystemEntry|None
                                        (raises UnknownScopeError, AmbiguousRefError)
    associate_paths(paths, index, scope, *, min_paths=DEFAULT_MIN_PATHS)
                                      -> Association
                                        (raises UnknownScopeError,
                                         InvalidPathError, ValueError)
    entry_mapping(text, *, filename, scope)
                                      -> dict  (ONE entry file -> the mapping the
                                         loader would build; the shared step a
                                         validator and the loader must not spell
                                         twice)
    load_index(root, *, on_malformed=RAISE)
                                      -> SubsystemIndex (the thin disk loader)

Every raise carries a distinct sentinel phrase so a caller — or a mutation test —
can tell WHICH guard fired, not merely that something did:

    "malformed index entry"        MalformedEntryError
    "unknown scope"                UnknownScopeError
    "ambiguous ref"                AmbiguousRefError
    "invalid repo-relative path"   InvalidPathError

An EMPTY PATH SET is not among them — it returns an empty, accounted result, and
`Association.considered_paths` is what keeps that zero distinguishable from
"we looked and matched nothing". See `associate_paths`.

A guard whose test passes because a NEIGHBOUR's error fired is green for the
wrong reason and stays green with the guard deleted (`claude/RULES.md` →
"Mutation-test a guard before certifying it").
"""

from __future__ import annotations

import re
from collections.abc import Sequence as _AbcSequence
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = [
    "KINDS",
    "DEFAULT_MIN_PATHS",
    "POINTERS_HEADING",
    "NUANCE_HEADING",
    "ResolverError",
    "MalformedEntryError",
    "UnknownScopeError",
    "AmbiguousRefError",
    "InvalidPathError",
    "EntryUnreadableError",
    "MalformedEntry",
    "ON_MALFORMED",
    "ON_MALFORMED_RAISE",
    "ON_MALFORMED_COLLECT",
    "OPENNESS_OPEN",
    "OPENNESS_RESOLVED",
    "JournalBullet",
    "extract_sections",
    "parse_journal_bullets",
    "SubsystemEntry",
    "SubsystemIndex",
    "Evidence",
    "SubsystemMatch",
    "AmbiguousRef",
    "Association",
    "normalize_ref",
    "split_kind",
    "path_refs",
    "build_index",
    "resolve_ref",
    "resolve_ref_tiered",
    "associate_paths",
    "parse_front_matter",
    "entry_mapping",
    "load_index",
]

# The kind enum from `analyze-service.md`: "kind ∈ `service` | `process` | `org`
# | `doc`". A trailing dot-segment is a kind ONLY if it is in this tuple —
# otherwise it is part of the slug, which is what keeps a slug like
# `forgejo.example.com` or a component like `values.yaml` intact.
KINDS: tuple[str, ...] = ("service", "process", "org", "doc")

# --- The precision threshold ---------------------------------------------------
# "A session that grazes one file under a directory should not tag that subsystem
# the same as one that rewrites it." The unit is DISTINCT PATHS, not commits:
# this function is source-agnostic and receives paths, so a commit count is not
# something it can see (a caller that has commits can supply its own threshold
# semantics by calling once per commit and aggregating).
#
# 🔴 The value is a NAMED DEFAULT, not a tuned constant, and it is deliberately
# the weakest defensible one. There is no measurement to justify 3 or 5 — the
# association has never been run over real sessions, which is P1's job. 2 is
# chosen because it is the smallest value that excludes the case the brief names
# (a single grazed file) while admitting everything else, so P1 measures against
# a floor rather than against a guess. Callers pass `min_paths=` explicitly;
# raise this only with the measurement quoted.
DEFAULT_MIN_PATHS = 2

# Derived from the command's normalization sentence — the character class it
# names is `[a-z0-9.-]`, so `_` and everything else outside it folds to `-`.
#
# 🔴 `_` IS FOLDED HERE, AND ONLY HERE. `normalize_ref` used to open with an
# explicit `.replace("_", "-")` mirroring the doc's "`_` → `-`" clause — but `_`
# is already outside this class, so that line could be deleted with no
# behavioural change at all. A mutation test aimed at it was UNKILLABLE, which is
# how the redundancy was found: it read as the guard implementing the `_` rule
# while this regex was the thing actually doing it. One site, so a mutation of it
# is observable.
_NON_SLUG = re.compile(r"[^a-z0-9.-]")
_DASH_RUN = re.compile(r"-{2,}")

# ⚠ The `\A` is REDUNDANT-BUT-KEPT (labelled, so a sweep does not re-derive it):
# this pattern is only ever used with `.match()`, which already anchors at
# position 0. It stays because it makes "front matter must be at the TOP of the
# file" readable at the pattern rather than at the call site.
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


# --- Errors --------------------------------------------------------------------


class ResolverError(Exception):
    """Base for every error this module raises."""


class MalformedEntryError(ResolverError):
    """An index entry cannot be interpreted. Sentinel: 'malformed index entry'.

    `.why` is the REASON with the sentinel prefix stripped, and `.source` is the
    entry it was raised about. Both are carried STRUCTURALLY rather than left to
    be recovered by splitting `str(exc)` on `": "` — a degrading loader has to
    print one row per bad entry, and a row assembled by re-parsing an error
    message is a second parser for a format nothing pins. `str(exc)` is unchanged
    and still leads with the sentinel, so every existing caller and every `in`
    assertion over it keeps working.
    """

    def __init__(self, message: str, *, source: str | None = None, why: str | None = None) -> None:
        self.source = source
        self.why = message if why is None else why
        super().__init__(message)


class UnknownScopeError(ResolverError):
    """The requested scope is not in the index. Sentinel: 'unknown scope'."""


# ⚠ There is deliberately NO EmptyPathSetError. An empty path set returns an
# empty, accounted `Association`; see `associate_paths`' docstring for why, and
# for the `considered_paths` property that keeps the two kinds of zero apart
# without an exception. Do not reintroduce it without re-reading that.


class InvalidPathError(ResolverError):
    """A path is not usable as a repo-relative path. Sentinel: 'invalid repo-relative path'.

    Absolute paths and `..` traversal are rejected rather than normalized away.
    An absolute path drags its whole prefix into the component set — `/home/zach/
    workspace/<repo>/...` offers `home`, `zach` and `workspace` as refs — so
    accepting one silently converts a caller bug into plausible-looking
    associations.
    """


class AmbiguousRefError(ResolverError):
    """A ref resolved to more than one entry within a tier. Sentinel: 'ambiguous ref'.

    `analyze-service.md`: ">1 in a tier → never pick: stop, call the ref
    ambiguous and list the candidates". Never resolved by preference order, never
    by first-hit; `.candidates` is the list for a human to choose from.
    """

    def __init__(self, ref: str, tier: str, candidates: Sequence[str], scope: str) -> None:
        self.ref = ref
        self.tier = tier
        self.scope = scope
        self.candidates: tuple[str, ...] = tuple(candidates)
        super().__init__(
            f"ambiguous ref {ref!r} in scope {scope!r}: {len(self.candidates)} candidates "
            f"in the {tier} tier ({', '.join(self.candidates)}). The resolver never picks — "
            f"disambiguate the ref or the index."
        )


class EntryUnreadableError(ResolverError):
    """An entry file cannot be read. Sentinel: 'index entry unreadable'.

    🔴 It exists because the alternative is an unnamed `OSError` escaping from
    inside `load_index`. The store is a plain directory of hand-curated files on
    a machine that also runs an hourly autocommit: a file can be mid-rename, a
    directory can be sitting where a `.md` is expected, a mode can be wrong. Any
    of those would otherwise reach the caller as `IsADirectoryError` or
    `PermissionError` with no indication that the SUBSYSTEM STORE was the thing
    that failed.

    🔴 IT LIVES HERE, IN THE MODULE BOTH READERS IMPORT, RATHER THAN IN EITHER OF
    THEM. `subsystem_recall` raises it when a `/resume` read fails and
    `subsystem_touch` raises it when a `/handoff` read fails — the SAME condition
    on the SAME files. Two classes spelling one condition is how a caller ends up
    catching the reader's and missing the writer's; the precedent is
    `StoreMissingError`, which `subsystem_recall` imports from `subsystem_touch`
    for exactly this reason rather than declaring a second one. It cannot live in
    either of those two modules, because `subsystem_recall` already imports
    `subsystem_touch` and the reverse edge would close a cycle.
    """


# --- Degrading: what a rejected entry looks like when it is not an exception ----
#
# 🔴 ONE BAD ENTRY USED TO COST THE WHOLE SCOPE. Measured on a synthetic store:
# 2 good entries listed 2; 2 good + 1 malformed listed **0** and exited 3, so
# `/resume` step 4, `--list`, `--ref` and `--search` all died together on one
# wrapped `aliases:` line. `RAISE` is still the default — every existing caller
# keeps its fail-closed contract and no test changes meaning — and `COLLECT` is
# opt-in, per call site, because whether a rejection should abort is a POLICY of
# the caller and not a property of the store.
#
# 🔴 COLLECT IS NOT "SKIP". A collected entry is carried on the index, counted,
# and every reader that uses this mode is obliged to print it: silently serving a
# short index would be a WORSE failure than the collapse it replaces, because a
# missing entry is indistinguishable from an entry that was never written.
ON_MALFORMED_RAISE = "raise"
ON_MALFORMED_COLLECT = "collect"
ON_MALFORMED: tuple[str, ...] = (ON_MALFORMED_RAISE, ON_MALFORMED_COLLECT)


@dataclass(frozen=True)
class MalformedEntry:
    """One entry file that could not be interpreted, and WHY — as data, not a raise.

    `scope` is the NORMALIZED owning scope, which for a disk load is the
    directory name (the authority on scope; see `load_index`). It is what lets a
    reader report this entry against the scope it belongs to and no other — a
    malformed entry in `scope-b/` must not appear while recalling `scope-a/`, and
    must not make `scope-a` look broken.
    """

    scope: str
    filename: str
    reason: str
    """The `why` clause — the sentence after the sentinel, e.g. "`aliases:` must
    be a list, not a bare string"."""

    @property
    def label(self) -> str:
        """`<scope>/<filename>` — how every surface names this file."""
        return f"{self.scope}/{self.filename}" if self.scope else self.filename

    @property
    def line(self) -> str:
        """ONE row, carrying the sentinel phrase.

        🔴 The sentinel is repeated PER ROW rather than hoisted into a block
        header, because a row is what gets copied into a report, quoted in a
        message, or grepped for — and a row that has left its header behind is a
        row that no longer says what kind of problem it describes.
        """
        return f"malformed index entry `{self.label}`: {self.reason}"


# --- The shared predicate ------------------------------------------------------


def normalize_ref(raw: str) -> str:
    """Fold a ref/slug/alias/path-component to its canonical form.

    The rule, from `claude/skills/analyze-service/SKILL.md`: lowercase, `_` → `-`,
    any other char outside `[a-z0-9.-]` → `-`, collapsed, trimmed. Applied
    identically on read and write and to `aliases:` before comparing.

    `.` SURVIVES — it is inside the character class. That is what lets kind
    qualification (`<slug>.<kind>`) and dotted slugs work at all.

    Returns "" for input that normalizes away entirely; every caller treats an
    empty result as "not a ref", never as a wildcard.
    """
    s = raw.strip().lower()
    s = _NON_SLUG.sub("-", s)  # `_` folds here too — see the note on _NON_SLUG
    s = _DASH_RUN.sub("-", s)
    return s.strip("-")


def split_kind(ref: str) -> tuple[str, str | None]:
    """Split an ALREADY-NORMALIZED ref into (slug, kind|None).

    A trailing dot-segment is a kind only if it is in `KINDS`; otherwise the
    whole ref is the slug. A leading-dot ref (`.process`) has no slug, so it is
    not a kind qualification either.

    Used for BOTH index filenames and incoming refs, on purpose — one splitter,
    so a ref and the filename it should reach can never disagree about where the
    kind boundary is.
    """
    head, sep, tail = ref.rpartition(".")
    if sep and head and tail in KINDS:
        return head, tail
    return ref, None


def path_refs(path: str) -> tuple[tuple[str, str], ...]:
    """Candidate `(raw_component, normalized_ref)` pairs for one repo-relative path.

    Every path component contributes its normalized self. The FINAL component
    additionally contributes its stem (one extension stripped), so
    `apps/minio/values.yaml` offers `values` as well as `values.yaml`, and a file
    named `pg_hero.yaml` reaches the `pghero` entry via its `pg_hero` alias.

    Only ONE extension is stripped: `foo.tar.gz` offers `foo.tar`, not `foo`.
    Stripping greedily would let any dotted filename impersonate a short slug.

    Duplicate pairs are collapsed, insertion order preserved, so a path like
    `redis/redis/values.yaml` counts once per distinct (component, ref).
    """
    parts = [p for p in path.split("/") if p not in ("", ".")]
    out: dict[tuple[str, str], None] = {}
    for i, part in enumerate(parts):
        ref = normalize_ref(part)
        if ref:
            out[(part, ref)] = None
        if i == len(parts) - 1 and "." in part.strip("."):
            stem = part.rsplit(".", 1)[0]
            stem_ref = normalize_ref(stem)
            if stem_ref:
                out[(stem, stem_ref)] = None
    return tuple(out)


# --- The index -----------------------------------------------------------------


@dataclass(frozen=True)
class SubsystemEntry:
    """One `/analyze-service` index entry, normalized and validated."""

    slug: str
    """Normalized slug — the filename's slug part, `service:` and filename agreeing."""

    kind: str | None
    """From a kind-qualified filename (`<slug>.<kind>.md`); None for a bare `<slug>.md`."""

    scope: str
    """Normalized owning scope. `repo:` is read as `scope:` (older files carry it)."""

    aliases: tuple[str, ...]
    """Normalized, deduped, sorted. Deduping is load-bearing — see `from_mapping`."""

    raw_aliases: tuple[str, ...]
    """As written in the file, for evidence and for reporting a candidate honestly."""

    filename: str
    """`<slug>.md` or `<slug>.<kind>.md` — the name a candidate list must show."""

    @property
    def ref(self) -> str:
        """The canonical ref that addresses this entry unambiguously."""
        return f"{self.slug}.{self.kind}" if self.kind else self.slug

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object], *, source: str = "<in-memory>") -> "SubsystemEntry":
        """Validate one entry mapping. Every rejection says 'malformed index entry'.

        Accepted keys: `service` (required), `scope` or `repo` (required, one of),
        `aliases` (optional sequence), `kind` (optional), `filename` (optional —
        supplied by the loader, otherwise derived).
        """

        def bad(why: str) -> MalformedEntryError:
            return MalformedEntryError(
                f"malformed index entry {source!r}: {why}", source=source, why=why
            )

        raw_service = mapping.get("service")
        if not isinstance(raw_service, str) or not raw_service.strip():
            raise bad("missing or empty `service:` — an entry with no name cannot be addressed")
        slug_all = normalize_ref(raw_service)
        if not slug_all:
            raise bad(f"`service: {raw_service!r}` normalizes to the empty string")

        raw_scope = mapping.get("scope")
        if raw_scope is None:
            raw_scope = mapping.get("repo")
        if not isinstance(raw_scope, str) or not raw_scope.strip():
            raise bad("missing or empty `scope:` (or the older `repo:`)")
        scope = normalize_ref(raw_scope)
        if not scope:
            raise bad(f"`scope: {raw_scope!r}` normalizes to the empty string")

        # Kind may arrive from the filename, the front matter, or both. Both must
        # agree: a `kind:` field the filename contradicts is a claim about the
        # entry that nothing enforces, and `claude/RULES.md` is explicit that a
        # declaration which no code path honours is worse than no declaration.
        filename = mapping.get("filename")
        file_kind: str | None = None
        if filename is not None:
            if not isinstance(filename, str) or not filename.endswith(".md"):
                raise bad(f"`filename` {filename!r} is not a `.md` name")
            file_slug, file_kind = split_kind(normalize_ref(filename[: -len(".md")]))
            if file_slug != slug_all:
                raise bad(
                    f"filename {filename!r} has slug {file_slug!r} but `service:` normalizes to "
                    f"{slug_all!r} — the two must agree or a ref reaches the wrong file"
                )

        declared_kind = mapping.get("kind")
        if declared_kind is not None:
            if not isinstance(declared_kind, str) or normalize_ref(declared_kind) not in KINDS:
                raise bad(
                    f"`kind: {declared_kind!r}` is not one of {'|'.join(KINDS)}"
                )
            declared_kind = normalize_ref(declared_kind)
        if declared_kind is not None and file_kind is not None and declared_kind != file_kind:
            raise bad(
                f"`kind: {declared_kind!r}` contradicts the filename's kind {file_kind!r}"
            )
        kind = file_kind if file_kind is not None else declared_kind

        # The slug is the filename's SLUG PART. If no filename was supplied and
        # `service:` itself carries a kind suffix, honour it — otherwise
        # `service: repo-cos.process` would produce slug `repo-cos.process`,
        # which no ref could ever reach.
        slug = slug_all
        if filename is None:
            slug, service_kind = split_kind(slug_all)
            if service_kind is not None:
                if kind is not None and kind != service_kind:
                    raise bad(
                        f"`service: {raw_service!r}` carries kind {service_kind!r} but "
                        f"`kind: {kind!r}` was declared"
                    )
                kind = service_kind

        raw_aliases_in = mapping.get("aliases") or ()
        if isinstance(raw_aliases_in, (str, bytes)):
            raise bad("`aliases:` must be a list, not a bare string")
        if not isinstance(raw_aliases_in, _AbcSequence):
            raise bad(f"`aliases:` must be a list, got {type(raw_aliases_in).__name__}")
        raw_aliases: list[str] = []
        normalized: set[str] = set()
        for alias in raw_aliases_in:
            if not isinstance(alias, str) or not alias.strip():
                raise bad(f"alias {alias!r} is not a non-empty string")
            na = normalize_ref(alias)
            if not na:
                raise bad(f"alias {alias!r} normalizes to the empty string")
            raw_aliases.append(alias)
            # DEDUPED, not rejected. The live corpus carries `pghero.md` with
            # BOTH `pg-hero` and `pg_hero` in one `aliases:` list; they fold to
            # one ref. Two spellings of one alias on ONE entry are a single
            # address, not an ambiguity — that distinction is the whole reason
            # ambiguity is measured per ENTRY and never per alias-occurrence.
            normalized.add(na)

        derived_filename = f"{slug}.{kind}.md" if kind else f"{slug}.md"
        return cls(
            slug=slug,
            kind=kind,
            scope=scope,
            aliases=tuple(sorted(normalized)),
            raw_aliases=tuple(raw_aliases),
            filename=filename if isinstance(filename, str) else derived_filename,
        )


@dataclass(frozen=True)
class SubsystemIndex:
    """Entries grouped by scope. A scope may legitimately be present but EMPTY.

    That distinction is load-bearing: an existing-but-empty scope dir yields an
    honest empty result, while a scope the index has never heard of raises
    `UnknownScopeError`. Collapsing the two would turn a typo'd scope into
    "0 subsystems", which is the exact silent zero this work exists to avoid.
    """

    by_scope: Mapping[str, tuple[SubsystemEntry, ...]]

    malformed: tuple[MalformedEntry, ...] = ()
    """Entries that were REJECTED, when the index was built with `COLLECT`.

    Always empty under the default `RAISE` — there the first rejection is the
    whole answer. It is a field on the index rather than a second return value so
    that "the entries" and "what could not become an entry" cannot be separated
    by a caller that only unpacks the first thing: a reader holding a
    `SubsystemIndex` is holding the bad news too, whether or not it asked.
    """

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted(self.by_scope))

    def malformed_in(self, scope: str) -> tuple[MalformedEntry, ...]:
        """The rejected entries belonging to ONE scope, normalized like every ref.

        No `UnknownScopeError`: this answers "what is broken here", and an
        unknown scope has nothing broken in it. Raising would force every caller
        to wrap a question it asks on every code path.
        """
        key = normalize_ref(scope)
        return tuple(m for m in self.malformed if m.scope == key)

    def malformed_outside(self, scopes: Iterable[str]) -> tuple[MalformedEntry, ...]:
        """The rejected entries in EVERY OTHER scope — the store-wide defect count.

        A reader is scope-scoped, so without this a broken entry in a scope
        nobody happens to recall today is invisible until someone recalls it. The
        surfaces print it as a COUNT with its scopes named, never as full rows:
        loud enough to be actionable, cheap enough to sit on every output.
        """
        keys = {normalize_ref(s) for s in scopes}
        return tuple(m for m in self.malformed if m.scope not in keys)

    def entries(self, scope: str) -> tuple[SubsystemEntry, ...]:
        key = normalize_ref(scope)
        if key not in self.by_scope:
            known = ", ".join(self.scopes) or "(none)"
            raise UnknownScopeError(
                f"unknown scope {scope!r} (normalized {key!r}); the index holds: {known}"
            )
        return self.by_scope[key]

    def __len__(self) -> int:
        return sum(len(v) for v in self.by_scope.values())


def _rejection(
    mapping: Mapping[str, object], source: str, exc: MalformedEntryError
) -> MalformedEntry:
    """Turn one raise into one row. The scope comes from the MAPPING, deliberately.

    On a disk load `scope` was set from the directory name before validation ran,
    so it is known even for an entry too broken to construct — which is what lets
    a rejected entry be reported against the scope it lives in instead of against
    the store at large. An in-memory mapping with no usable scope yields `""`,
    and `malformed_in` then matches no scope, so such a row surfaces only through
    the store-wide count. Stated rather than left to be discovered.
    """
    raw_scope = mapping.get("scope")
    if not isinstance(raw_scope, str) or not raw_scope.strip():
        raw_scope = mapping.get("repo")
    scope = normalize_ref(raw_scope) if isinstance(raw_scope, str) else ""
    filename = mapping.get("filename")
    return MalformedEntry(
        scope=scope,
        filename=filename if isinstance(filename, str) and filename else source,
        reason=exc.why,
    )


def build_index(
    mappings: Iterable[Mapping[str, object]],
    *,
    extra_scopes: Iterable[str] = (),
    on_malformed: str = ON_MALFORMED_RAISE,
) -> SubsystemIndex:
    """Validate and group entry mappings.

    `extra_scopes` registers scopes that exist but hold no entries (a scope dir
    the loader found empty), so they resolve to an empty result rather than
    `UnknownScopeError`.

    `on_malformed` picks the POLICY, and the default keeps the historical one:

      `RAISE`   — the first rejection aborts the whole build. Right for a WRITER,
                  which is about to modify a curated store and must not act on a
                  partial picture of it.
      `COLLECT` — a rejection becomes a `MalformedEntry` on the index and the
                  build continues. Right for a READER, where aborting spends
                  every good entry in the scope to report one bad one. **A
                  collecting caller MUST print what it collected**; see
                  `ON_MALFORMED`'s note.

    🔴 BOTH REJECTION SITES COLLECT — the per-entry validator AND the duplicate
    check. A duplicate is a relationship between two files, and the one recorded
    is the LATER of the pair in the loader's sorted order, so the first spelling
    of a ref keeps serving and the collision is still named. Collecting only the
    first site would have left `COLLECT` able to raise, which is the shape a
    caller cannot defend against because it looks handled.
    """
    if on_malformed not in ON_MALFORMED:
        raise ValueError(
            f"on_malformed must be one of {ON_MALFORMED}, got {on_malformed!r}"
        )
    collecting = on_malformed == ON_MALFORMED_COLLECT
    by_scope: dict[str, list[SubsystemEntry]] = {}
    malformed: list[MalformedEntry] = []
    seen: dict[tuple[str, str, str | None], str] = {}
    for mapping in mappings:
        source = str(mapping.get("filename") or mapping.get("service") or "<unnamed>")
        try:
            entry = SubsystemEntry.from_mapping(mapping, source=source)
        except MalformedEntryError as exc:
            if not collecting:
                raise
            malformed.append(_rejection(mapping, source, exc))
            continue
        key = (entry.scope, entry.slug, entry.kind)
        if key in seen:
            dup = MalformedEntryError(
                f"malformed index entry {source!r}: duplicate {entry.ref!r} in scope "
                f"{entry.scope!r} — already defined by {seen[key]!r}",
                source=source,
                why=(
                    f"duplicate {entry.ref!r} in scope {entry.scope!r} — already defined "
                    f"by {seen[key]!r}"
                ),
            )
            if not collecting:
                raise dup
            malformed.append(_rejection(mapping, source, dup))
            continue
        seen[key] = entry.filename
        by_scope.setdefault(entry.scope, []).append(entry)
    # 🔴 A SCOPE THAT HOLDS ONLY BROKEN ENTRIES STILL EXISTS. Without this the
    # scope would be unknown to the index and a reader would answer `scope-absent`
    # — "nothing recorded yet" — about a directory full of content it simply
    # could not parse. That is the exact conflation this store guards against
    # everywhere else. (On a disk load `extra_scopes` already registers every
    # directory; this covers `build_index` called directly.)
    for m in malformed:
        if m.scope:
            by_scope.setdefault(m.scope, [])
    for scope in extra_scopes:
        key = normalize_ref(scope)
        if key:
            by_scope.setdefault(key, [])
    return SubsystemIndex(
        by_scope={k: tuple(v) for k, v in by_scope.items()},
        malformed=tuple(malformed),
    )


# --- Resolution ----------------------------------------------------------------


def resolve_ref_tiered(
    ref: str, index: SubsystemIndex, scope: str
) -> tuple[SubsystemEntry | None, str | None]:
    """Resolve one ref within one scope, reporting WHICH tier hit.

    The direct port of the #362 rule. Two tiers; an alias can never outrank a
    filename:
      1. FILENAME — normalized ref vs `<slug>.md` and every `<slug>.<kind>.md`.
         A ref naming its own kind (`repo-cos.process`) matches ONLY that
         qualified file.
      2. ALIAS — normalized `aliases:` across the scope, consulted **only if
         tier 1 returned zero hits**.

    One hit → `(entry, tier)`. >1 in a tier → `AmbiguousRefError` listing
    candidates, never a pick. Zero in both → `(None, None)` — an honest miss,
    not an error.

    The tier is RETURNED rather than re-derived by the caller: `associate_paths`
    needs it for evidence, and a second expression computing "was that a
    filename or an alias hit?" is the same predicate twice, one edit from
    disagreeing with the branch that actually chose the entry.
    """
    entries = index.entries(scope)
    nref = normalize_ref(ref)
    # ⚠ REDUNDANT-BUT-KEPT, and labelled so the next mutation sweep does not
    # re-derive it: no entry can have an empty slug (`from_mapping` rejects one),
    # so an empty ref would miss both tiers anyway. This is a cheap short-circuit
    # that states the intent, not a guard — mutating it away changes nothing.
    if not nref:
        return None, None

    slug, kind = split_kind(nref)
    if kind is not None:
        hits = [e for e in entries if e.slug == slug and e.kind == kind]
    else:
        hits = [e for e in entries if e.slug == nref]
    if len(hits) > 1:
        raise AmbiguousRefError(nref, "filename", sorted(e.filename for e in hits), scope)
    if hits:
        return hits[0], "filename"

    hits = [e for e in entries if nref in e.aliases]
    if len(hits) > 1:
        raise AmbiguousRefError(nref, "alias", sorted(e.filename for e in hits), scope)
    if hits:
        return hits[0], "alias"
    return None, None


def resolve_ref(ref: str, index: SubsystemIndex, scope: str) -> SubsystemEntry | None:
    """`resolve_ref_tiered` without the tier — the plain "which entry is this?"."""
    return resolve_ref_tiered(ref, index, scope)[0]


# --- Association ---------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """Why one path was attributed to one entry."""

    path: str
    component: str
    """The RAW path component (or filename stem) that produced the ref."""
    ref: str
    """The component after normalization — what was actually compared."""
    tier: str
    """`"filename"` or `"alias"`."""
    matched_alias: str | None = None
    """The alias as WRITTEN in the entry, when `tier == "alias"`."""


@dataclass(frozen=True)
class SubsystemMatch:
    entry: SubsystemEntry
    paths: tuple[str, ...]
    evidence: tuple[Evidence, ...]

    @property
    def path_count(self) -> int:
        return len(self.paths)


@dataclass(frozen=True)
class AmbiguousRef:
    """A ref that could not be resolved because it named more than one entry."""

    ref: str
    tier: str
    candidates: tuple[str, ...]
    paths: tuple[str, ...]
    """The paths that produced it — so a human can see what would have been tagged."""


@dataclass(frozen=True)
class Association:
    """The full, accounted-for result. Nothing is dropped silently.

    `matched + below_threshold + ambiguous + unmatched_paths` is the complete
    story of the input: a caller reading only `matched` still has somewhere to
    look for the difference between "nothing was touched" and "something was
    touched and discarded".
    """

    scope: str
    min_paths: int

    matched: tuple[SubsystemMatch, ...]
    """Subsystems that cleared `min_paths`, in canonical ref order.

    🔴 `if not assoc.matched:` CONFLATES THE TWO ZEROS. It is empty both when no
    paths were supplied and when paths were supplied and matched nothing — and
    downstream those mean opposite things ("we have no reading" vs "this
    subsystem was genuinely not touched"). Use `looked_at_nothing` to separate
    them; `considered_paths` is the underlying evidence.
    """

    below_threshold: tuple[SubsystemMatch, ...]
    ambiguous: tuple[AmbiguousRef, ...]
    unmatched_paths: tuple[str, ...]
    considered_paths: tuple[str, ...]
    """Every path actually examined, deduped, in input order. The discriminator."""

    @property
    def looked_at_nothing(self) -> bool:
        """True when NO paths were supplied — as distinct from "matched nothing".

        The discriminator is `considered_paths`; this names it, so a consumer
        does not have to know which field carries the distinction or re-derive
        `len(considered_paths) == 0` at every call site (one rule, one place).

        🔴 It is an AFFORDANCE, not a gate. Nothing can force a consumer to
        consult it — that was the one thing raising an exception did buy, and it
        was traded away deliberately because it made the ordinary case
        exceptional. In particular `if not assoc.matched:` still conflates the
        two zeros; see the note on `matched`.
        """
        return not self.considered_paths

    @property
    def subsystem_refs(self) -> tuple[str, ...]:
        """Canonical refs of the subsystems that CLEARED the threshold.

        Already in canonical (ref-sorted) order — `associate_paths` establishes
        it once, in ONE place. This used to re-`sorted()` here, and an
        independent mutation sweep found that mutation unkillable: two sites
        asserting one ordering means neither can be observed to be wrong.
        """
        return tuple(m.entry.ref for m in self.matched)


def _validate_path(path: object) -> str:
    if not isinstance(path, str) or not path.strip():
        raise InvalidPathError(f"invalid repo-relative path {path!r}: empty or not a string")
    if path.startswith("/"):
        raise InvalidPathError(
            f"invalid repo-relative path {path!r}: absolute paths drag their prefix "
            f"into the component set (home, zach, workspace, …) and manufacture matches"
        )
    parts = path.split("/")
    if ".." in parts:
        raise InvalidPathError(
            f"invalid repo-relative path {path!r}: `..` escapes the repo root"
        )
    return path


def associate_paths(
    paths: Iterable[str],
    index: SubsystemIndex,
    scope: str,
    *,
    min_paths: int = DEFAULT_MIN_PATHS,
) -> Association:
    """Map changed repo-relative paths onto the subsystems they touch.

    Pure: no I/O, no clock, no globals. `index` is injected.

    Guard order — each is reachable by an input no earlier guard rejects:
      1. `min_paths` sanity            → ValueError
      2. scope known                   → UnknownScopeError
      3. each path repo-relative       → InvalidPathError
    An ambiguous ref does NOT raise here: one undecidable ref must not blind the
    whole session. It is recorded in `.ambiguous`, contributes to no subsystem,
    and a caller that ignores that field is discarding a known unknown.

    🔴 AN EMPTY PATH SET IS NOT AN ERROR. It returns a fully empty, fully
    accounted `Association`. This raised `EmptyPathSetError` until review: the
    argument for raising was that a manufactured zero is indistinguishable from
    a real one, but in P1's per-session call pattern a session with no git
    activity is an ORDINARY input, not an exceptional one. Making the common
    case an exception forces every caller to wrap the call, and a wrapped call
    is how a caller ends up swallowing the genuine errors too.

    The property the exception was protecting is kept STRUCTURALLY instead:
    `considered_paths` distinguishes the two zeros without any exception.

        no paths supplied      → considered_paths == ()   unmatched_paths == ()
        paths, none matched    → considered_paths != ()   unmatched_paths != ()

    So a consumer can still tell "we were given nothing to look at" from "we
    looked and found nothing", which is the whole reason the guard existed.
    Genuine programmer errors — a malformed entry, an unknown scope, a path that
    is not repo-relative — still raise.
    """
    if not isinstance(min_paths, int) or isinstance(min_paths, bool) or min_paths < 1:
        raise ValueError(f"min_paths must be an int >= 1, got {min_paths!r}")

    # Scope guard, before a single path is counted: an unknown scope must not be
    # able to produce a well-formed empty result. (An empty path set may — see
    # the docstring; a typo'd scope may not.)
    index.entries(scope)

    ordered: list[str] = []
    for raw in paths:
        p = _validate_path(raw)
        if p not in ordered:
            ordered.append(p)

    paths_by_entry: dict[str, list[str]] = {}
    evidence_by_entry: dict[str, list[Evidence]] = {}
    entry_by_ref: dict[str, SubsystemEntry] = {}
    ambiguous: dict[tuple[str, str], AmbiguousRef] = {}
    matched_paths: set[str] = set()

    for path in ordered:
        for component, ref in path_refs(path):
            try:
                entry, tier = resolve_ref_tiered(ref, index, scope)
            except AmbiguousRefError as exc:
                key = (exc.ref, exc.tier)
                prev = ambiguous.get(key)
                seen_paths = (prev.paths if prev else ()) + (path,)
                ambiguous[key] = AmbiguousRef(
                    ref=exc.ref,
                    tier=exc.tier,
                    candidates=exc.candidates,
                    paths=tuple(dict.fromkeys(seen_paths)),
                )
                continue
            # `tier` is None exactly when `entry` is — they are returned
            # together — so testing both was defensiveness no input could
            # distinguish (a mutation sweep found the `or` unkillable).
            if entry is None:
                continue

            matched_alias: str | None = None
            if tier == "alias":
                matched_alias = next(
                    (a for a in entry.raw_aliases if normalize_ref(a) == ref), None
                )

            key = entry.ref
            entry_by_ref[key] = entry
            bucket = paths_by_entry.setdefault(key, [])
            if path not in bucket:
                bucket.append(path)
            evidence_by_entry.setdefault(key, []).append(
                Evidence(
                    path=path,
                    component=component,
                    ref=ref,
                    tier=tier,
                    matched_alias=matched_alias,
                )
            )
            matched_paths.add(path)

    matched: list[SubsystemMatch] = []
    below: list[SubsystemMatch] = []
    # 🔴 THE ONE ordering site. Output order is by canonical ref, NOT by the
    # order paths happened to arrive — P1 emits one row per (session, subsystem)
    # and a re-run over the same session must produce the same rows in the same
    # order, or a diff of two runs shows churn that is not there.
    for key in sorted(paths_by_entry):
        m = SubsystemMatch(
            entry=entry_by_ref[key],
            paths=tuple(paths_by_entry[key]),
            evidence=tuple(evidence_by_entry[key]),
        )
        (matched if m.path_count >= min_paths else below).append(m)

    return Association(
        scope=normalize_ref(scope),
        min_paths=min_paths,
        matched=tuple(matched),
        below_threshold=tuple(below),
        ambiguous=tuple(ambiguous[k] for k in sorted(ambiguous)),
        unmatched_paths=tuple(p for p in ordered if p not in matched_paths),
        considered_paths=tuple(ordered),
    )


# --- Entry markdown shape ------------------------------------------------------
#
# 🔴 ONE PARSER, TWO CONSUMERS. `subsystem_recall` (the `/resume` reader) and
# `subsystem_touch` (the `/handoff` writer) both have to read an entry's prose:
# the reader to surface it, the writer to show what is ALREADY THERE before it
# proposes an append. These functions started in `subsystem_recall`, where they
# were verified against the real 23-entry corpus, and moved DOWN here unchanged
# when the writer needed them — because `subsystem_recall` already imports
# `subsystem_touch`, so the writer cannot import the reader without closing a
# cycle, and a copy in the writer would be a second parser free to drift from the
# one the corpus was measured against.
#
# They are pure and touch no filesystem, which is why they sit above the disk
# loader with the rest of the pure functions.


POINTERS_HEADING = "## Pointers"
NUANCE_HEADING = "## Nuance / work-history"

# A top-level journal bullet starts at COLUMN 0. Measured over the whole live
# corpus on 2026-08-12 (26 entries, 110 top-level bullets): every bullet line is
# at indent 0 and every one of the 250 continuation lines is at indent 2. So an
# indented `-` is a CONTINUATION (a nested list, or prose that happens to start
# with a dash), never a new bullet — folding the two together would split one
# bullet into several and report a history longer than the entry has.
_JOURNAL_BULLET = re.compile(r"^[-*][ \t]+")

# `- YYYY-MM-DD: …` — the dated form. The date is OPTIONAL: 62 of those 110
# bullets carry one and 48 do not, so a parser that required a date would drop
# 44% of the real corpus on the floor and call the result a complete read.
_JOURNAL_DATE = re.compile(r"^[-*][ \t]+(\d{4}-\d{2}-\d{2})(?=[:,)\]\s]|$)")

# `- [YYYY-MM-DD: ]OPEN: …` / `- [YYYY-MM-DD: ]RESOLVED <sha>: …` — the openness
# marker, and the reason it is a PREFIX rather than a phrase.
#
# 🔴 WHY THIS IS SCHEMA AND NOT A PROSE DETECTOR. The motivating entry
# (`datapacket-talos/forgejo`) carries a bullet proposing a one-line config change
# as future work. That change landed at 15:02:21 on 2026-07-24 (the sha is in the
# client repo and is deliberately not reproduced here);
# the entry was written at
# 15:00:18 — stale 2m03s after it was written, and still being served as an open
# action 22 days later. Nothing could have noticed, because "this remedy is not
# applied yet" was a claim made only in prose.
#
# The obvious repair is to grep the prose for remedy words. Measured over the live
# corpus (196 nuance bullets, 2026-08-15) that finds TWO bullets — and
# `claude/RULES.md` names the failure it would be: "a guard on WORDS is walkable by
# REWORDING". A writer who says "the endpoint is already correct" instead of
# "FIX:" walks past it, and the walk is silent. A prefix a writer must TYPE cannot
# be walked by rewording the sentence after it; that is the whole reason the
# marker sits before the prose rather than inside it.
#
# `RESOLVED` takes the sha that closed it so the claim is checkable — `git cat-file
# -e <sha>` answers it — rather than being a second unverifiable assertion.
_JOURNAL_OPENNESS = re.compile(
    r"^[-*][ \t]+"
    r"(?:\d{4}-\d{2}-\d{2}:[ \t]+)?"           # the optional leading date
    r"(OPEN|RESOLVED)"
    r"(?:[ \t]+([0-9a-fA-F]{7,40}))?"          # RESOLVED carries the closing sha
    r":"                                        # exact terminator, no fuzz
)

OPENNESS_OPEN = "open"
OPENNESS_RESOLVED = "resolved"

# The retrospective advisory — DELIBERATELY NARROW, and a FLOOR, not a list.
#
# These two shapes are the only ones that scored 2 hits and 0 false positives over
# the 196-bullet corpus. The ones rejected, and why, so nobody re-adds them:
#   `TODO`        1 hit, FALSE — an entry describing an UPSTREAM project's TODO as
#                 a fact about that project, not a remedy this operator owes.
#   `not yet`     3 hits, 2 FALSE — one describes a mechanism (deps not yet on
#                 npm), one is an explicit WONTFIX that says "don't re-litigate".
#   `should be` / `next step` / `pending` / `deferred` / `proposed fix` — 0 hits
#                 each. Adding a marker with no corpus evidence buys recall that
#                 cannot be demonstrated and precision that cannot be defended.
#
# 🔴 RECALL IS UNKNOWN AND MUST BE REPORTED AS SUCH. This finds bullets that
# HAPPEN to be phrased the two ways already seen. It is an advisory that says
# "at least these"; it is never evidence that an entry has no open actions. The
# schema marker above is the mechanism; this is a net under it for entries written
# before the marker existed.
_UNMARKED_ACTION = re.compile(r"\bFIX\s*[(:]|\bnot (yet )?addressed\b", re.I)

# A bullet that MEANT to carry a marker and just missed the grammar.
#
# 🔴 THE SILENT FAILURE IS THE SAME CLASS AS THE ORIGINAL BUG, which is why this
# exists at all. Measured shapes that parse as NO MARKER today:
#
#   - 2026-08-15 OPEN: …                 (date not followed by `:`)
#   - 2026-08-15: RESOLVED abc1234 (repo): …   (parenthetical before the colon)
#   - 2026-08-15: **OPEN:** …            (marker wrapped in emphasis)
#   - 2026-08-15: OPEN : …               (space before the colon)
#   - 2026-08-15: RESOLVED PR#505: …     (a non-sha reference)
#
# The first matters most: `_JOURNAL_DATE` accepts a date followed by any of
# `[:,)\]\s]` while `_JOURNAL_OPENNESS` requires `date:` + whitespace, and **7 of
# 147 dated bullets in the live corpus (4.8%) already use a date form the
# openness regex cannot parse**. So a writer follows the skill, closes an action
# as `RESOLVED <sha> (<repo>):`, the `🔴 N OPEN` badge disappears — which LOOKS
# like success — and the claim is discarded. Symmetrically a typo'd `OPEN`
# reverts the entry to "nothing declared", reintroducing the exact 22-day failure
# the marker exists to prevent, through a typo, silently.
#
# Deliberately NOT fixed by widening `_JOURNAL_OPENNESS`. A lenient marker
# regex starts matching prose, and inventing a marker is worse than missing one:
# a false `RESOLVED` closes an action nobody closed. So the strict grammar stands
# and the near-misses are REPORTED instead, which is the fail-loud half.
#
# ⚠ THE LEADING `^[-*][ \t]+` IS REDUNDANT HERE AND IN `_JOURNAL_OPENNESS`, and
# is kept knowingly. Both are consumed with `re.match`, which anchors at position
# 0, and neither pattern sets `re.MULTILINE` — so loosening or deleting that
# prefix is an EQUIVALENT mutant that no test can kill (two batteries have now
# reported exactly these two as survivors, which is how the redundancy was
# identified rather than assumed). It stays because the anchoring it duplicates
# is a property of the CALL, not of the pattern: switch either consumer to
# `search` and the prefix becomes the only thing standing between this and
# matching mid-prose. Do not "simplify" it, and do not count it as a guard.
_NEAR_MISS_MARKER = re.compile(
    r"^[-*][ \t]+"
    r"(?:\d{4}-\d{2}-\d{2}[^A-Za-z]{0,3})?"    # a date in any of its corpus forms
    r"[^A-Za-z0-9]{0,4}"                        # `**`, quotes, stray punctuation
    r"(OPEN|RESOLVED)\b",
    re.I,
)


def _is_fence(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("```") or s.startswith("~~~")


def extract_sections(text: str, headings: Sequence[str]) -> dict[str, str]:
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


@dataclass(frozen=True)
class JournalBullet:
    """One top-level bullet of a `## Nuance / work-history` section, VERBATIM.

    🔴 `lines` IS A TUPLE, NOT A STRING, because a real bullet is WRAPPED PROSE.
    Measured over the live corpus on 2026-08-12: 110 top-level bullets carry 250
    continuation lines between them — a median bullet is 3 lines and the longest
    is 19. Any model that assumed one line per bullet would silently truncate
    most of the corpus, and a truncated bullet is exactly the thing an agent
    would fail to recognize as a near-duplicate of the line it is about to write.
    """

    lines: tuple[str, ...]
    date: str | None
    """The ISO date the bullet is dated with, or None. ~44% of the real corpus
    carries no date; `None` is an ordinary reading, not a parse failure."""

    openness: str | None = None
    """`'open'` | `'resolved'` | None — the bullet's DECLARED openness marker.

    None is by far the common reading — **195 of 196** bullets in the live corpus
    on 2026-08-15 — and means only that nothing was declared. 🔴 It does NOT mean
    "this bullet proposes no work": an unmarked bullet that proposes a remedy is
    exactly the `forgejo` failure, and `unmarked_action` is the (narrow,
    floor-only) net for that case.

    ⚠ The 196th is why this reads 195 and not 196. A past session had ALREADY
    written `- OPEN: rotate …` by hand, with no tooling asking it to and no
    convention documented — so this schema is a formalisation of a shape the
    corpus invented on its own, not one imposed on it. An earlier revision of
    this docstring claimed all 196 were unmarked; that was written before the
    corpus was grepped for markers, and the grep is what corrected it.
    """

    resolved_by: str | None = None
    """The sha a `RESOLVED <sha>:` bullet names as having closed it, or None.

    Carried so the claim is CHECKABLE — a reader can run `git cat-file -e` on it.
    A `RESOLVED` with no sha parses fine and leaves this None: the marker is still
    worth having, it just cannot be verified.

    🔴 BRANCHED ON, not merely stored. An audit found this field read by nothing
    outside the tests while its docstring claimed "the renderer says which it
    got" — a field in a DTO is not a guard (`claude/RULES.md`), and the design's
    headline rationale ("the sha makes the claim checkable") was unimplemented.
    `--validate` now reports every sha-less `RESOLVED` as an UNVERIFIABLE closure,
    which is the branch that makes the field load-bearing.
    """

    @property
    def first_line(self) -> str:
        return self.lines[0] if self.lines else ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def is_open(self) -> bool:
        return self.openness == OPENNESS_OPEN

    @property
    def unmarked_action(self) -> bool:
        """Does this bullet LOOK like an unmarked open action? A FLOOR, never a list.

        True only for the two phrasings measured to occur with no false positives
        over the live corpus (see `_UNMARKED_ACTION`). An unmarked bullet phrased
        any other way returns False, and that False is not evidence of anything —
        which is why every renderer of this prints it as "at least N", never as a
        count of what exists.

        Suppressed once the bullet declares openness: a bullet that already says
        `OPEN:` is not *unmarked*, and one that says `RESOLVED:` has been closed —
        re-flagging either would train the reader to ignore the advisory.
        """
        if self.openness is not None:
            return False
        return bool(_UNMARKED_ACTION.search(self.text))

    @property
    def near_miss_marker(self) -> bool:
        """Did this bullet TRY to carry a marker and miss the grammar?

        The fail-loud half of a deliberately strict `_JOURNAL_OPENNESS`. True only
        when no marker parsed AND the first line opens with something that reads
        like an attempt — so a writer whose `RESOLVED <sha> (<repo>):` silently
        did nothing is told, instead of seeing the badge vanish and reading that
        as success.
        """
        if self.openness is not None:
            return False
        return bool(_NEAR_MISS_MARKER.match(self.first_line))


def parse_journal_bullets(body: str) -> tuple[JournalBullet, ...]:
    """Group a `## Nuance / work-history` body into top-level bullets.

    Order is preserved exactly as stored — this function makes NO claim about
    which bullet is newest. The store's convention is newest-first, but that is a
    convention a writer can break, so recency is derived from the DATES (see
    `subsystem_touch.EntryJournal.newest_date`) rather than from position.

    Rules, each measured against the corpus rather than assumed:

      * A bullet starts at column 0 (`_JOURNAL_BULLET`). Every other non-blank
        line attaches to the bullet above it, indented or not.
      * Text BEFORE the first bullet is dropped from the bullet list. The caller
        must not read an empty tuple as "the section is empty" — a non-empty body
        that yields no bullets is its own state, and `subsystem_touch` reports it
        as one rather than showing a blank.
      * 🔴 FENCED BLOCKS ARE SKIPPED, for the same reason `extract_sections`
        skips them: a `- ` line inside a fence is sample text, and promoting it
        to a bullet invents history the entry does not have. No fence appears in
        the corpus today (measured: 0 fence lines across all 26 nuance sections)
        — this is here because the sibling parser one screen up already had to
        learn it, and a fence is one pasted snippet away.
      * Trailing blank lines are stripped from each bullet so a blank separator
        cannot inflate a bullet's line count.
    """
    bullets: list[list[str]] = []
    in_fence = False
    for line in body.splitlines():
        if _is_fence(line):
            in_fence = not in_fence
            if bullets:
                bullets[-1].append(line)
            continue
        if not in_fence and _JOURNAL_BULLET.match(line):
            bullets.append([line])
            continue
        if bullets:
            bullets[-1].append(line)
    out: list[JournalBullet] = []
    for group in bullets:
        while group and not group[-1].strip():
            group.pop()
        openness, resolved_by = _bullet_openness(group[0])
        out.append(
            JournalBullet(
                lines=tuple(group),
                date=_bullet_date(group[0]),
                openness=openness,
                resolved_by=resolved_by,
            )
        )
    return tuple(out)


def _bullet_openness(first_line: str) -> tuple[str | None, str | None]:
    """`(openness, resolved_by)` for one bullet's first line.

    Takes the first line only, but 🔴 THAT IS NOT WHAT ENFORCES IT — the guard is
    `re.match`, which anchors at position 0, over a pattern with no `re.MULTILINE`.
    A marker on a continuation line is therefore unreachable whether this is passed
    one line or the whole joined bullet.

    Stated because a mutation battery proved it: replacing `group[0]` with the
    joined bullet text is an EQUIVALENT mutant and survives the suite, and an
    earlier version of this docstring claimed the argument was the protection.
    Two mechanisms reaching one outcome cannot be told apart by any test
    (`claude/RULES.md`), so the honest form is to name the one that actually holds
    and keep the narrower argument as defence-in-depth — if the pattern ever gains
    `re.MULTILINE`, passing one line is what stops markers being invented from
    wrapped prose, and that is a real hazard rather than a hypothetical one.

    The sha is normalised to lower case so `RESOLVED B83BFB58:` and
    `RESOLVED b83bfb58:` are one claim, not two.
    """
    m = _JOURNAL_OPENNESS.match(first_line)
    if not m:
        return None, None
    marker = OPENNESS_OPEN if m.group(1) == "OPEN" else OPENNESS_RESOLVED
    sha = m.group(2)
    return marker, sha.lower() if sha else None


def _bullet_date(first_line: str) -> str | None:
    """The bullet's ISO date, or None — VALIDATED, not just shaped.

    `2026-13-45` matches the shape and is not a date; returning it would put a
    nonexistent day into a recency claim and into any arithmetic done on it.
    `fromisoformat` is the check, so what comes back is always a real date.
    """
    m = _JOURNAL_DATE.match(first_line)
    if not m:
        return None
    try:
        _date.fromisoformat(m.group(1))
    except ValueError:
        return None
    return m.group(1)


# --- The thin disk loader ------------------------------------------------------
#
# Deliberately separate from everything above: the pure functions never touch the
# filesystem, so the tests exercise them with in-memory fixtures and NEVER read
# the real store (which is client-confidential, unbacked-up, and rewritten hourly
# by the autocommit timer).


def parse_front_matter(text: str) -> dict[str, object]:
    """Parse the leading `---` block of an index file into a str/list-of-str dict.

    Hand-rolled rather than PyYAML, for one correctness reason and one
    deployment reason:

      * YAML's implicit typing is actively wrong for this schema. `service: no`
        parses to the boolean False, `service: 1.0` to a float, and an alias
        `on` / `yes` / `off` to a bool — every one of which then fails to
        normalize as a string. The values here are ALWAYS strings.
      * P1 runs this from the collector timer's environment; a lib with no
        third-party import is one fewer thing that can be absent there.

    Handles the two shapes the real corpus uses: `key: value` and an inline flow
    list `key: [a, b, c]`. Quotes are stripped. Unknown keys are preserved so a
    caller can see them; `SubsystemEntry.from_mapping` ignores what it does not
    need.
    """
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}
    out: dict[str, object] = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",")]
            out[key] = [v for v in items if v]
        else:
            out[key] = value.strip("'\"")
    return out


def entry_mapping(text: str, *, filename: str, scope: str) -> dict[str, object]:
    """ONE entry file's bytes -> the mapping `from_mapping` would be handed.

    🔴 EXTRACTED FROM `load_index` SO A VALIDATOR CANNOT BUILD A DIFFERENT ONE.
    `subsystem_touch --validate` has to answer "would the loader accept this
    file?", and the only honest way to answer it is to construct exactly what the
    loader constructs. Re-spelling these four lines at the validator would be the
    duplicated predicate `claude/RULES.md` names: the day one side learns a new
    identity field, the validator starts blessing entries the reader rejects —
    the precise drift a write-time check exists to prevent.

    The directory name is the authority on scope: it is where the file actually
    lives. A `scope:`/`repo:` field that disagrees is stale front matter, not a
    relocation, so `scope` is set unconditionally and `repo` is dropped.
    """
    fm = dict(parse_front_matter(text))
    fm["filename"] = filename
    fm["scope"] = scope
    # ⚠ REDUNDANT-BUT-KEPT, labelled: `from_mapping` reads `scope` in preference
    # to `repo`, and `scope` was just set unconditionally on the line above, so
    # the pop cannot change any outcome. It stays to keep the mapping honest —
    # leaving a contradicted `repo:` in a dict that is also the malformed-entry
    # error's source label would put a stale value in front of whoever reads that
    # error.
    fm.pop("repo", None)
    return fm


def load_index(root: Path, *, on_malformed: str = ON_MALFORMED_RAISE) -> SubsystemIndex:
    """Read `<root>/<scope>/*.md` into a `SubsystemIndex`. READ-ONLY.

    `README.md` is skipped in every scope — each scope dir carries one as its
    store-policy sheet, and it is not an entry.

    A scope dir with no entries is REGISTERED, not dropped: "Lazy — a scope dir
    or service file may not exist yet". An existing empty scope must resolve to
    an honest empty result, while a scope that does not exist stays an error.

    🔴 FAIL-CLOSED BY DEFAULT, AND THE CALLER CHOOSES. `on_malformed=RAISE` (the
    default) aborts the whole index on the first bad entry — correct for a WRITER
    about to modify a curated store. `COLLECT` degrades: the good entries load
    and the rejects come back on `index.malformed` for the caller to PRINT.

    The question this docstring used to defer is now answered, and the answer was
    measured. Fail-closed cost the whole scope: on a synthetic store, 2 good
    entries listed 2, and 2 good + 1 malformed listed **0** with exit 3 — one
    wrapped `aliases:` line took `/resume` step 4, `--list`, `--ref` and
    `--search` down together. The reader (`subsystem_recall`) therefore loads
    with `COLLECT` and reports every reject per-entry in the same output; the
    writer's probe (`subsystem_touch.build_report`) keeps `RAISE`, because it
    gates a WRITE into a store it would then be reading only partially.

    🔴 What `COLLECT` is NOT is "skip the bad entry". Silently serving a short
    index is a worse failure than the collapse, because a dropped entry is
    indistinguishable from an entry nobody ever wrote. The obligation to print is
    part of the mode; see `ON_MALFORMED`.

    ⚠ AN `OSError` STILL FAILS CLOSED IN BOTH MODES, and that is a different
    fact, not an oversight: a malformed entry is a file we read and could not
    interpret, while an unreadable one means the store was not fully READ — the
    set of entries is then unknown, so there is nothing honest to degrade to.
    `load_store`/`build_report` name it `index entry unreadable`.
    """
    mappings: list[Mapping[str, object]] = []
    scopes: list[str] = []
    # Both `sorted()` calls here are DETERMINISM guards: they fix which entry a
    # malformed-index error names, so the same store always produces the same
    # message. ⚠ The scope-level one is not observable from a test — no test can
    # control `iterdir()` order — so it is the one guard in this module a
    # mutation sweep cannot kill. Stated rather than left to look covered.
    for scope_dir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        scopes.append(scope_dir.name)
        for md in sorted(scope_dir.glob("*.md")):
            if md.name == "README.md":
                continue
            mappings.append(
                entry_mapping(
                    md.read_text(encoding="utf-8", errors="replace"),
                    filename=md.name,
                    scope=scope_dir.name,
                )
            )
    return build_index(mappings, extra_scopes=scopes, on_malformed=on_malformed)
