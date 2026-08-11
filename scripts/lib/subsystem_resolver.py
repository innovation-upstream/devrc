"""Map changed repo-relative paths onto `/analyze-service` index entries.

P0 of the derived session→subsystem association described in
`claudedocs/decision-subsystem-store-rejected-2026-08-11.md` → "What replaced the
premise: derived session association". This module is the WHOLE of P0: a pure,
source-agnostic function plus a thin disk loader. It emits nothing, knows nothing
about ClickHouse, and has never heard of Claude Code or opencode — P1 feeds it
paths from either.

🔴 THIS FILE IS THE EXECUTABLE AUTHORITY FOR THE REF-NORMALIZATION AND
RESOLUTION RULES.
`claude/commands/analyze-service.md` states the same rules in prose, for a reader
that is an LLM rather than an interpreter, so the predicate necessarily exists at
two sites — and `claude/RULES.md` → "One rule, one place" says a duplicated
predicate regenerates the same bug at both. It cannot be literally deduplicated
(you cannot import a function into a markdown file), so it is made DETECTABLE
instead:

  * this module is named in that command as the authority;
  * `scripts/tests/test_subsystem_resolver.py` → `TestCommandDocIsPinned` holds
    the command's normative sentences as literal substrings AND the behaviour
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
    build_index(mappings)             -> SubsystemIndex (raises MalformedEntryError)
    resolve_ref(ref, index, scope)    -> SubsystemEntry|None
                                        (raises UnknownScopeError, AmbiguousRefError)
    associate_paths(paths, index, scope, *, min_paths=DEFAULT_MIN_PATHS)
                                      -> Association
                                        (raises UnknownScopeError,
                                         InvalidPathError, ValueError)
    load_index(root)                  -> SubsystemIndex (the thin disk loader)

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
from pathlib import Path
from typing import Iterable, Mapping, Sequence

__all__ = [
    "KINDS",
    "DEFAULT_MIN_PATHS",
    "ResolverError",
    "MalformedEntryError",
    "UnknownScopeError",
    "AmbiguousRefError",
    "InvalidPathError",
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
    """An index entry cannot be interpreted. Sentinel: 'malformed index entry'."""


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


# --- The shared predicate ------------------------------------------------------


def normalize_ref(raw: str) -> str:
    """Fold a ref/slug/alias/path-component to its canonical form.

    The rule, from `claude/commands/analyze-service.md`: lowercase, `_` → `-`,
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
            return MalformedEntryError(f"malformed index entry {source!r}: {why}")

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

    @property
    def scopes(self) -> tuple[str, ...]:
        return tuple(sorted(self.by_scope))

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


def build_index(
    mappings: Iterable[Mapping[str, object]],
    *,
    extra_scopes: Iterable[str] = (),
) -> SubsystemIndex:
    """Validate and group entry mappings.

    `extra_scopes` registers scopes that exist but hold no entries (a scope dir
    the loader found empty), so they resolve to an empty result rather than
    `UnknownScopeError`.
    """
    by_scope: dict[str, list[SubsystemEntry]] = {}
    seen: dict[tuple[str, str, str | None], str] = {}
    for mapping in mappings:
        source = str(mapping.get("filename") or mapping.get("service") or "<unnamed>")
        entry = SubsystemEntry.from_mapping(mapping, source=source)
        key = (entry.scope, entry.slug, entry.kind)
        if key in seen:
            raise MalformedEntryError(
                f"malformed index entry {source!r}: duplicate {entry.ref!r} in scope "
                f"{entry.scope!r} — already defined by {seen[key]!r}"
            )
        seen[key] = entry.filename
        by_scope.setdefault(entry.scope, []).append(entry)
    for scope in extra_scopes:
        key = normalize_ref(scope)
        if key:
            by_scope.setdefault(key, [])
    return SubsystemIndex(by_scope={k: tuple(v) for k, v in by_scope.items()})


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


def load_index(root: Path) -> SubsystemIndex:
    """Read `<root>/<scope>/*.md` into a `SubsystemIndex`. READ-ONLY.

    `README.md` is skipped in every scope — each scope dir carries one as its
    store-policy sheet, and it is not an entry.

    A scope dir with no entries is REGISTERED, not dropped: "Lazy — a scope dir
    or service file may not exist yet". An existing empty scope must resolve to
    an honest empty result, while a scope that does not exist stays an error.

    🔴 FAIL-CLOSED, AND P1 MUST DECIDE WHETHER THAT IS STILL RIGHT. One malformed
    entry aborts the WHOLE index. That is correct for the interactive caller this
    was written for — a human running `/analyze-service` should be told the store
    is broken, loudly, rather than handed a silently short index. It is likely
    WRONG for P1's 5-minute timer, where the same raise means every session in
    that window gets no association at all, and downstream that is indistinguishable
    from "these subsystems had no sessions" — the exact silent zero this module
    exists to prevent, reintroduced one layer up.

    Deliberately NOT changed here: the alternative (skip the bad entry, report it)
    needs somewhere for the report to GO, and that is P1's design decision, not
    something to guess at now. When P1 lands, either give it a skip-and-report
    mode or have the timer treat this raise as a hard alert — but do not let it
    log-and-continue.
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
            fm = dict(parse_front_matter(md.read_text(encoding="utf-8", errors="replace")))
            fm["filename"] = md.name
            # The directory name is the authority on scope: it is where the file
            # actually lives. A `scope:`/`repo:` field that disagrees is stale
            # front matter, not a relocation.
            fm["scope"] = scope_dir.name
            # ⚠ REDUNDANT-BUT-KEPT, labelled: `from_mapping` reads `scope` in
            # preference to `repo`, and `scope` was just set unconditionally on
            # the line above, so the pop cannot change any outcome. It stays to
            # keep the mapping honest — leaving a contradicted `repo:` in a dict
            # that is also the malformed-entry error's source label would put a
            # stale value in front of whoever reads that error.
            fm.pop("repo", None)
            mappings.append(fm)
    return build_index(mappings, extra_scopes=scopes)
