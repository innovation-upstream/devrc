"""Tests for scripts/lib/subsystem_resolver.py — P0 of derived session→subsystem association.

WHAT IS BEING PROTECTED
-----------------------
`claudedocs/decision-subsystem-store-rejected-2026-08-11.md` → "What replaced the
premise": a session's subsystems are DERIVED from the paths it touched, by
matching path components against each `/analyze-service` index entry's slug and
`aliases:`. Nothing persists a location.

🔴 THE FAILURE MODE IS A SILENT ZERO. If the matcher is wrong, associations
simply do not match, and downstream "this subsystem had 0 sessions" is
indistinguishable from "this subsystem is dormant". `claude/RULES.md` →
"Validate the INSTRUMENT before you read its verdict": a reassuring zero from a
matcher wired to nothing looks exactly like a real one. So every zero asserted
here is asserted TOGETHER with a positive control on the same code path
(`TestPositiveControl`), and every zero result is additionally checked to be
ACCOUNTED FOR (`unmatched_paths` covers the input).

WHY THE FIXTURES ARE HAND-AUTHORED AND NOT A SNAPSHOT OF THE REAL STORE
-----------------------------------------------------------------------
🔴 The real corpus MUST NOT be copied in here. `~/.claude/analyze-service-index/`
carries client-identifying infrastructure detail; all 21 live entries lack a
`sensitivity:` field, which `analyze-service/SKILL.md` defines as fail-safe
`client-confidential`. This repo is PUBLIC, and `scripts/testlib/
client_host_scan.py` exists precisely because six client subdomains had already
leaked into fixtures once (devrc `60e6d9d` scrubbed them retroactively) — several
live aliases are exactly that shape.

So the fixtures below reproduce the SHAPES measured in the live store on
2026-08-10 (read-only probe, nothing written), with synthetic names.

⚠ The list that follows describes the LIVE CORPUS, not `ENTRIES` below.
`ENTRIES` is 9 entries across 3 scopes — deliberately smaller and deliberately
NOT proportioned like the real store, because its job is to carry one clean
instance of each SHAPE (and two constructed ones the live store does not contain
at all: the ambiguity pair and a cross-entry alias clash). Read the counts here
as "what the store looks like", never as "what the fixture asserts".

Measured live, 2026-08-10:

  * 21 entries, 1 scope, 0 `kind:` fields, 0 `sensitivity:` fields;
  * every entry still spells the scope `repo:`, not `scope:` — the loader must
    read the older key (`TestLoader::test_legacy_repo_key_is_read_as_scope`);
  * `aliases:` is an inline YAML flow list, with members carrying spaces, dots
    and underscores;
  * 0 filename-tier collisions and 0 cross-entry alias collisions today, so the
    AMBIGUITY guard has no natural instance and must be reached by construction
    — which is why `TestReachability` proves it is reachable rather than
    assuming it;
  * exactly ONE normalization collision exists: one entry lists BOTH the `-` and
    the `_` spelling of one alias. Two spellings of one alias on ONE entry are a
    single address, NOT an ambiguity. `pghero` below reproduces it.

NOTHING HERE SKIPS. There is no external binary, no network, no filesystem
outside `tmp_path`, and no import of the real store. `run-tests.sh` pins
`EXPECTED_SKIPS` as an exact set, so a skip added here breaks the gate on
purpose.

EVERY NEGATIVE CONTROL ASSERTS ITS OWN GUARD'S SENTINEL PHRASE, never merely a
non-zero raise: a control that passes because a NEIGHBOURING guard fired is
green for the wrong reason and stays green with the guard it claims to test
deleted. `TestMutationKillMatrix` proves that by actually deleting each guard
from a copy of the source and watching the specific expectation die.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "lib" / "subsystem_resolver.py"
# Formerly `claude/commands/analyze-service.md`. Upstream merged custom commands
# INTO skills, so `claude/commands/` was retired and every command became
# `claude/skills/<name>/SKILL.md`; this is the SAME doc at its new path, and
# `test_the_doc_path_is_the_deployed_one` below is what pins it to the file that
# actually ships.
SKILL_DOC = ROOT / "claude" / "skills" / "analyze-service" / "SKILL.md"

sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from testlib.skills_mapping import (  # noqa: E402
    assert_skills_mapping_deploys_repo_skills,
)

import subsystem_resolver as sr  # noqa: E402


# =============================================================================
# Fixtures — realistic shapes, synthetic names, PAIRWISE DISTINCT fields.
# =============================================================================
#
# 🔴 Field distinctness is deliberate (`claude/RULES.md`: "pick fixtures whose
# fields are pairwise distinct so a wrong-field bug can't pass"). No scope name
# appears as any slug or alias; no slug appears as another entry's slug except
# the ONE deliberate kind-qualified pair; every alias list is disjoint from every
# other except the ONE deliberate shadow case. So a matcher that compared the
# wrong field would produce zero, not a plausible answer.

SCOPE_A = "homelab-talos"   # the busy scope: no ambiguity anywhere in it
SCOPE_B = "devrc"           # holds the deliberate kind-qualified ambiguity
SCOPE_EMPTY = "empty-scope"  # exists, holds nothing — an honest empty result

ENTRIES: list[dict[str, object]] = [
    # 1. The real `_`/`-` collision, reproduced: BOTH spellings of one alias on
    #    ONE entry. Must dedupe to one address, must NOT read as ambiguous.
    {
        "service": "pghero",
        "repo": SCOPE_A,
        "aliases": ["pg-hero", "pg_hero", "hero-dashboard"],
        "filename": "pghero.md",
    },
    # 2. Underscore-only alias (`media_ingestion`) — the case that proves aliases
    #    are normalized BEFORE comparison, not after. Also a multi-word alias.
    {
        "service": "image-ingestion",
        "scope": SCOPE_A,
        "aliases": ["ingestion", "blob-upload", "media_ingestion", "image scan"],
        "filename": "image-ingestion.md",
    },
    # 3. A dotted alias whose trailing segment is NOT a kind word, so it stays
    #    part of the ref.
    {
        "service": "object-store",
        "scope": SCOPE_A,
        "aliases": ["s3.local", "nvme-tenant"],
        "filename": "object-store.md",
    },
    # 4. The command doc's own worked example. Note `externaldns` reaches this
    #    entry via an ALIAS — normalization alone does not fold it into
    #    `external-dns`, and asserting that it did would be a false pin.
    {
        "service": "External DNS",
        "scope": SCOPE_A,
        "aliases": ["externaldns", "external_dns", "edns"],
        "filename": "external-dns.md",
    },
    # 5. 🔴 THE SHADOW CASE. This entry's SLUG is entry 2's ALIAS. Tier 1 must
    #    win: `blob-upload` resolves here, never to image-ingestion.md.
    {
        "service": "blob-upload",
        "scope": SCOPE_A,
        "aliases": ["upload-api"],
        "filename": "blob-upload.md",
    },
    # 6. Plain control entry, no overlap with anything.
    {
        "service": "bar-status-poll",
        "scope": SCOPE_A,
        "aliases": ["status-poll"],
        "filename": "bar-status-poll.md",
    },
    # 7 + 8. THE DELIBERATE AMBIGUITY, in a different scope so it cannot
    #    contaminate the tests above. `analyze-service/SKILL.md`'s own example:
    #    `repo-cos.md` vs `repo-cos.process.md`.
    {
        "service": "repo-cos",
        "scope": SCOPE_B,
        "aliases": ["chief-of-staff"],
        "filename": "repo-cos.md",
    },
    {
        "service": "repo-cos",
        "scope": SCOPE_B,
        "aliases": ["weekly-ritual"],
        "filename": "repo-cos.process.md",
    },
    # 9. Unambiguous entry in scope B, so scope B is not only ambiguity.
    {
        "service": "collector",
        "scope": SCOPE_B,
        "aliases": ["telemetry-collector"],
        "filename": "collector.md",
    },
]


@pytest.fixture()
def index() -> sr.SubsystemIndex:
    return sr.build_index(ENTRIES, extra_scopes=[SCOPE_EMPTY])


@pytest.fixture(scope="module")
def doc() -> str:
    """The skill doc, read once — the other half of the one-predicate pair."""
    return SKILL_DOC.read_text(encoding="utf-8")


# =============================================================================
# normalize_ref — the shared predicate. Literal expectations, hand-written.
# =============================================================================


class TestNormalizeRef:
    """Every expected value below is written from the RULE, not from a run.

    `claude/RULES.md`: "Never derive a test's expectation from the implementation
    it tests." The rule is: lowercase, `_` → `-`, anything else outside
    `[a-z0-9.-]` → `-`, collapse runs of `-`, trim leading/trailing `-`.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # --- the `_` fold, which is the whole reason #362 touched this ---
            ("pg_hero", "pg-hero"),
            ("PG_Hero", "pg-hero"),
            ("image_ingestion", "image-ingestion"),
            ("media_ingestion", "media-ingestion"),
            (
                "bastion_config_stale_until_reload_2026_07_08",
                "bastion-config-stale-until-reload-2026-07-08",
            ),
            # --- case + whitespace ---
            ("External DNS", "external-dns"),
            ("  Spaced  ", "spaced"),
            ("release changelog bot", "release-changelog-bot"),
            # --- already canonical, unchanged ---
            ("external-dns", "external-dns"),
            ("bar-status-poll", "bar-status-poll"),
            # --- 🔴 NOT equal to `external-dns`. The doc says these "land on one
            #     file"; they do so via `aliases:`, NOT via normalization. A test
            #     asserting normalization folds them would pin a false rule.
            ("externaldns", "externaldns"),
            # --- `.` SURVIVES: it is inside the character class ---
            ("repo-cos.process", "repo-cos.process"),
            ("values.yaml", "values.yaml"),
            ("s3.local", "s3.local"),
            ("UPPER_Case-Mixed.Name", "upper-case-mixed.name"),
            # --- other chars fold, runs collapse, ends trim ---
            ("Foo!!!Bar", "foo-bar"),
            ("a//b", "a-b"),
            ("a  b", "a-b"),
            ("-leading", "leading"),
            ("trailing-", "trailing"),
            ("--both--", "both"),
            # --- normalizes away entirely ---
            ("---", ""),
            ("!!!", ""),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_normalization(self, raw: str, expected: str) -> None:
        assert sr.normalize_ref(raw) == expected

    def test_is_idempotent(self) -> None:
        """Applied "identically on read and write" — so a second pass is a no-op."""
        for raw, _ in [("PG_Hero", ""), ("External DNS", ""), ("Foo!!!Bar", "")]:
            once = sr.normalize_ref(raw)
            assert sr.normalize_ref(once) == once


class TestSplitKind:
    """"A trailing dot-segment is a kind ONLY if it is in that enum, else it's
    part of the slug." Expectations written from that sentence."""

    @pytest.mark.parametrize(
        "ref,expected",
        [
            ("repo-cos.process", ("repo-cos", "process")),
            ("x.service", ("x", "service")),
            ("x.org", ("x", "org")),
            ("x.doc", ("x", "doc")),
            # not in the enum → part of the slug
            ("values.yaml", ("values.yaml", None)),
            ("forgejo.example.invalid", ("forgejo.example.invalid", None)),
            ("a.b.org", ("a.b", "org")),
            # no dot at all
            ("repo-cos", ("repo-cos", None)),
            ("doc", ("doc", None)),
            # a leading dot leaves no slug, so it is not a kind qualification
            (".process", (".process", None)),
            (".doc", (".doc", None)),
        ],
    )
    def test_split(self, ref: str, expected: tuple[str, str | None]) -> None:
        assert sr.split_kind(ref) == expected

    def test_kind_enum_is_exactly_the_documented_four(self) -> None:
        assert sr.KINDS == ("service", "process", "org", "doc")


class TestPathRefs:
    """Which refs a path offers. Hand-written, including the dedupe and the
    one-extension-only rule."""

    @pytest.mark.parametrize(
        "path,expected",
        [
            (
                "clusters/homelab/apps/pghero/values.yaml",
                (
                    ("clusters", "clusters"),
                    ("homelab", "homelab"),
                    ("apps", "apps"),
                    ("pghero", "pghero"),
                    ("values.yaml", "values.yaml"),
                    ("values", "values"),
                ),
            ),
            (
                "scripts/lib/subsystem_resolver.py",
                (
                    ("scripts", "scripts"),
                    ("lib", "lib"),
                    ("subsystem_resolver.py", "subsystem-resolver.py"),
                    ("subsystem_resolver", "subsystem-resolver"),
                ),
            ),
            # dedupe: a component repeated in one path counts once
            ("a/a/b", (("a", "a"), ("b", "b"))),
            # a leading "./" is not a component
            ("./docs/x.md", (("docs", "docs"), ("x.md", "x.md"), ("x", "x"))),
            # 🔴 ONE extension only. Stripping greedily would let any dotted
            #     filename impersonate a short slug.
            ("foo.tar.gz", (("foo.tar.gz", "foo.tar.gz"), ("foo.tar", "foo.tar"))),
            # a dotfile has no stem to strip
            (".gitignore", ((".gitignore", ".gitignore"),)),
            # only the FINAL component contributes a stem: `a.b` mid-path does not
            ("a.b/c", (("a.b", "a.b"), ("c", "c"))),
        ],
    )
    def test_refs(self, path: str, expected: tuple[tuple[str, str], ...]) -> None:
        assert sr.path_refs(path) == expected


# =============================================================================
# Index construction — the MALFORMED ENTRY negative controls.
# =============================================================================


class TestBuildIndexValidation:
    """Each control asserts THIS guard's sentinel ('malformed index entry') AND
    the clause naming what was wrong — so a different rejection cannot pass it."""

    def test_valid_corpus_builds(self, index: sr.SubsystemIndex) -> None:
        assert len(index) == len(ENTRIES)
        assert index.scopes == (SCOPE_B, SCOPE_EMPTY, SCOPE_A)

    def test_missing_service_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"scope": SCOPE_A, "aliases": ["x"]}])
        assert "malformed index entry" in str(exc.value)
        assert "missing or empty `service:`" in str(exc.value)
        # An entry with neither a filename nor a service still has to be
        # NAMEABLE in the error, or the message points at nothing.
        assert "<unnamed>" in str(exc.value)

    def test_blank_service_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "   ", "scope": SCOPE_A}])
        assert "missing or empty `service:`" in str(exc.value)

    def test_service_that_normalizes_away_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "!!!", "scope": SCOPE_A}])
        assert "normalizes to the empty string" in str(exc.value)

    def test_missing_scope_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "lonely"}])
        assert "missing or empty `scope:`" in str(exc.value)

    def test_unknown_kind_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "x", "scope": SCOPE_A, "kind": "vendor"}])
        assert "is not one of service|process|org|doc" in str(exc.value)

    def test_kind_contradicting_the_filename_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index(
                [{"service": "x", "scope": SCOPE_A, "kind": "org", "filename": "x.process.md"}]
            )
        assert "contradicts the filename's kind" in str(exc.value)

    def test_filename_slug_disagreeing_with_service_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "alpha", "scope": SCOPE_A, "filename": "beta.md"}])
        assert "the two must agree or a ref reaches the wrong file" in str(exc.value)

    def test_non_md_filename_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "x", "scope": SCOPE_A, "filename": "x.txt"}])
        assert "is not a `.md` name" in str(exc.value)

    def test_alias_that_normalizes_away_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "x", "scope": SCOPE_A, "aliases": ["ok", "---"]}])
        assert "normalizes to the empty string" in str(exc.value)

    def test_empty_string_alias_is_rejected_by_the_TYPE_guard(self) -> None:
        """Distinct from the case above, and it must say so: `""` never reaches
        normalization, so reporting "normalizes to the empty string" would name
        the wrong guard."""
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "x", "scope": SCOPE_A, "aliases": [""]}])
        assert "is not a non-empty string" in str(exc.value)
        assert "normalizes to the empty string" not in str(exc.value)

    def test_non_string_alias_is_malformed_not_an_AttributeError(self) -> None:
        """A YAML-ish loader can hand back an int. It must land here, named, and
        not as an AttributeError from `.strip()` three frames down."""
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "x", "scope": SCOPE_A, "aliases": [123]}])
        assert "is not a non-empty string" in str(exc.value)

    def test_bare_string_aliases_is_malformed(self) -> None:
        """`aliases: foo` instead of `aliases: [foo]` would otherwise iterate CHARS."""
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([{"service": "x", "scope": SCOPE_A, "aliases": "foo"}])
        assert "must be a list, not a bare string" in str(exc.value)

    def test_duplicate_entry_is_malformed(self) -> None:
        dup = {"service": "twice", "scope": SCOPE_A}
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.build_index([dup, dict(dup)])
        assert "duplicate 'twice'" in str(exc.value)

    def test_same_slug_different_kind_is_NOT_duplicate(self) -> None:
        """`repo-cos.md` + `repo-cos.process.md` coexist — that is ambiguity at
        RESOLVE time, never a malformed index."""
        built = sr.build_index(
            [
                {"service": "repo-cos", "scope": SCOPE_B, "filename": "repo-cos.md"},
                {"service": "repo-cos", "scope": SCOPE_B, "filename": "repo-cos.process.md"},
            ]
        )
        assert len(built) == 2

    def test_intra_entry_alias_spellings_dedupe_to_one_address(self) -> None:
        """The one real collision in the live corpus. NOT an error, NOT ambiguity."""
        entry = sr.SubsystemEntry.from_mapping(ENTRIES[0])
        assert entry.slug == "pghero"
        assert entry.aliases == ("hero-dashboard", "pg-hero")
        assert entry.raw_aliases == ("pg-hero", "pg_hero", "hero-dashboard")

    def test_normalized_aliases_are_sorted(self) -> None:
        """`aliases` is built from a set, whose iteration order is arbitrary (and
        randomized per process for strings). The sort is what makes an entry
        reproducible; four members are asserted rather than two so set order
        agreeing with sorted order by chance is not a plausible pass."""
        entry = sr.SubsystemEntry.from_mapping(ENTRIES[1])
        assert entry.aliases == (
            "blob-upload",
            "image-scan",
            "ingestion",
            "media-ingestion",
        )
        assert list(entry.aliases) == sorted(entry.aliases)

    def test_declared_kind_agreeing_with_a_service_suffix_is_accepted(self) -> None:
        """`service: repo-cos.process` + `kind: process` agree, so this must
        BUILD. The guard next to it rejects only DISagreement."""
        entry = sr.SubsystemEntry.from_mapping(
            {"service": "repo-cos.process", "scope": SCOPE_B, "kind": "process"}
        )
        assert (entry.slug, entry.kind) == ("repo-cos", "process")

    def test_declared_kind_disagreeing_with_a_service_suffix_is_malformed(self) -> None:
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.SubsystemEntry.from_mapping(
                {"service": "repo-cos.process", "scope": SCOPE_B, "kind": "org"}
            )
        assert "carries kind 'process' but" in str(exc.value)

    def test_service_carrying_a_kind_suffix_without_a_filename(self) -> None:
        entry = sr.SubsystemEntry.from_mapping({"service": "repo-cos.process", "scope": SCOPE_B})
        assert (entry.slug, entry.kind, entry.filename) == (
            "repo-cos",
            "process",
            "repo-cos.process.md",
        )


# =============================================================================
# resolve_ref — tiering, ambiguity, and the shadow rule.
# =============================================================================


class TestResolveRefTiers:
    def test_filename_tier_exact_slug(self, index: sr.SubsystemIndex) -> None:
        entry, tier = sr.resolve_ref_tiered("pghero", index, SCOPE_A)
        assert (entry.filename, tier) == ("pghero.md", "filename")

    def test_normalized_ref_reaches_the_slug(self, index: sr.SubsystemIndex) -> None:
        entry, tier = sr.resolve_ref_tiered("Image_Ingestion", index, SCOPE_A)
        assert (entry.filename, tier) == ("image-ingestion.md", "filename")

    def test_alias_tier(self, index: sr.SubsystemIndex) -> None:
        entry, tier = sr.resolve_ref_tiered("hero-dashboard", index, SCOPE_A)
        assert (entry.filename, tier) == ("pghero.md", "alias")

    def test_alias_is_normalized_before_comparison(self, index: sr.SubsystemIndex) -> None:
        """`media_ingestion` is stored underscore-spelled; a `-` ref must reach it."""
        entry, tier = sr.resolve_ref_tiered("media-ingestion", index, SCOPE_A)
        assert (entry.filename, tier) == ("image-ingestion.md", "alias")

    def test_underscore_ref_reaches_a_dash_alias(self, index: sr.SubsystemIndex) -> None:
        """The mirror direction: `pg_hero` ref onto the folded alias set."""
        entry, tier = sr.resolve_ref_tiered("pg_hero", index, SCOPE_A)
        assert (entry.filename, tier) == ("pghero.md", "alias")

    def test_the_doc_worked_example_externaldns(self, index: sr.SubsystemIndex) -> None:
        """"`External DNS` / `externaldns` / `external-dns` land on one file"."""
        hits = {
            ref: sr.resolve_ref(ref, index, SCOPE_A).filename
            for ref in ("External DNS", "externaldns", "external-dns", "external_dns")
        }
        assert hits == {
            "External DNS": "external-dns.md",
            "externaldns": "external-dns.md",
            "external-dns": "external-dns.md",
            "external_dns": "external-dns.md",
        }

    def test_alias_never_outranks_a_filename(self, index: sr.SubsystemIndex) -> None:
        """🔴 THE SHADOW RULE. `blob-upload` is BOTH a filename slug and another
        entry's alias. Tier 1 must win, and tier 2 must never be consulted.

        This is the defect the decision record names: "aliasing around it
        SHADOWED the future entry, because the resolver matched aliases before
        declaring a miss"."""
        entry, tier = sr.resolve_ref_tiered("blob-upload", index, SCOPE_A)
        assert (entry.filename, tier) == ("blob-upload.md", "filename")

    def test_a_miss_is_none_not_an_error(self, index: sr.SubsystemIndex) -> None:
        assert sr.resolve_ref("no-such-thing-anywhere", index, SCOPE_A) is None

    def test_a_ref_that_normalizes_away_is_a_miss(self, index: sr.SubsystemIndex) -> None:
        assert sr.resolve_ref("!!!", index, SCOPE_A) is None

    def test_matching_is_exact_never_substring(self, index: sr.SubsystemIndex) -> None:
        """`store` must not reach `object-store`; `pg` must not reach `pghero`."""
        for ref in ("store", "pg", "ingest", "poll", "hero"):
            assert sr.resolve_ref(ref, index, SCOPE_A) is None, ref

    def test_scopes_are_isolated(self, index: sr.SubsystemIndex) -> None:
        assert sr.resolve_ref("pghero", index, SCOPE_B) is None
        assert sr.resolve_ref("collector", index, SCOPE_A) is None

    def test_scope_is_normalized_on_lookup(self, index: sr.SubsystemIndex) -> None:
        assert sr.resolve_ref("collector", index, "DEVRC").filename == "collector.md"

    # --- kind qualification ---------------------------------------------------

    def test_kind_qualified_ref_selects_only_that_file(self, index: sr.SubsystemIndex) -> None:
        entry, tier = sr.resolve_ref_tiered("repo-cos.process", index, SCOPE_B)
        assert (entry.filename, tier) == ("repo-cos.process.md", "filename")

    def test_kind_qualified_ref_for_an_absent_kind_is_a_miss(
        self, index: sr.SubsystemIndex
    ) -> None:
        assert sr.resolve_ref("repo-cos.org", index, SCOPE_B) is None

    def test_bare_slug_still_works_when_no_qualified_sibling_exists(
        self, index: sr.SubsystemIndex
    ) -> None:
        """"Bare `<slug>.md` stays the default … a scope with no qualified
        filename behaves exactly as before"."""
        entry, tier = sr.resolve_ref_tiered("collector", index, SCOPE_B)
        assert (entry.filename, tier) == ("collector.md", "filename")

    def test_bare_slug_reaches_a_lone_kind_qualified_file(self) -> None:
        """One qualified file and no bare sibling: the bare ref still resolves."""
        only_qualified = sr.build_index(
            [{"service": "ritual", "scope": SCOPE_B, "filename": "ritual.process.md"}]
        )
        entry, tier = sr.resolve_ref_tiered("ritual", only_qualified, SCOPE_B)
        assert (entry.filename, tier) == ("ritual.process.md", "filename")

    # --- ambiguity ------------------------------------------------------------

    def test_ambiguous_filename_tier_raises_with_candidates(
        self, index: sr.SubsystemIndex
    ) -> None:
        with pytest.raises(sr.AmbiguousRefError) as exc:
            sr.resolve_ref("repo-cos", index, SCOPE_B)
        assert "ambiguous ref" in str(exc.value)
        assert exc.value.tier == "filename"
        assert exc.value.candidates == ("repo-cos.md", "repo-cos.process.md")
        assert "repo-cos.md, repo-cos.process.md" in str(exc.value)

    def test_ambiguous_alias_tier_raises_with_candidates(self) -> None:
        """Two entries sharing one alias. Absent from the live corpus today
        (measured: 0 cross-entry alias collisions), so it is constructed."""
        clashing = sr.build_index(
            [
                {"service": "left", "scope": SCOPE_A, "aliases": ["shared-name"]},
                {"service": "right", "scope": SCOPE_A, "aliases": ["shared-name"]},
            ]
        )
        with pytest.raises(sr.AmbiguousRefError) as exc:
            sr.resolve_ref("shared-name", clashing, SCOPE_A)
        assert "ambiguous ref" in str(exc.value)
        assert exc.value.tier == "alias"
        assert exc.value.candidates == ("left.md", "right.md")

    def test_a_tier_1_hit_suppresses_an_ambiguous_tier_2(self) -> None:
        """Ambiguity in the alias tier must not surface when tier 1 already
        decided — "consulted ONLY if tier 1 returned zero hits"."""
        mixed = sr.build_index(
            [
                {"service": "anchor", "scope": SCOPE_A},
                {"service": "left", "scope": SCOPE_A, "aliases": ["anchor"]},
                {"service": "right", "scope": SCOPE_A, "aliases": ["anchor"]},
            ]
        )
        assert sr.resolve_ref("anchor", mixed, SCOPE_A).filename == "anchor.md"

    # --- unknown scope --------------------------------------------------------

    def test_unknown_scope_raises(self, index: sr.SubsystemIndex) -> None:
        with pytest.raises(sr.UnknownScopeError) as exc:
            sr.resolve_ref("pghero", index, "typo-scope")
        assert "unknown scope" in str(exc.value)
        assert SCOPE_A in str(exc.value)

    def test_existing_but_empty_scope_is_a_miss_not_an_error(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 The distinction that keeps a typo from reading as "0 subsystems"."""
        assert sr.resolve_ref("pghero", index, SCOPE_EMPTY) is None


# =============================================================================
# 🔴 POSITIVE CONTROL — the pair, in one test, on one code path.
# =============================================================================


class TestPositiveControl:
    """`claude/RULES.md` → "Positive control — can it ever observe the thing?"

    A reassuring zero is indistinguishable from a matcher wired to nothing. So
    the zero is never asserted alone: the same call, same index, same scope, same
    path SHAPE, differing only in whether the directory names a real entry."""

    POSITIVE_PATHS = [
        "clusters/homelab/apps/pghero/values.yaml",
        "clusters/homelab/apps/pghero/kustomization.yaml",
        "clusters/homelab/apps/pghero/ingressroute.yaml",
    ]
    # Same depth, same filenames, same scope — ONLY the subsystem directory
    # differs, and it names nothing in the index.
    NEGATIVE_PATHS = [
        "clusters/homelab/apps/unlisted-widget/values.yaml",
        "clusters/homelab/apps/unlisted-widget/kustomization.yaml",
        "clusters/homelab/apps/unlisted-widget/ingressroute.yaml",
    ]

    def test_the_pair(self, index: sr.SubsystemIndex) -> None:
        positive = sr.associate_paths(self.POSITIVE_PATHS, index, SCOPE_A, min_paths=2)
        negative = sr.associate_paths(self.NEGATIVE_PATHS, index, SCOPE_A, min_paths=2)

        # THE PAIR, reported together: 1 under the positive control, 0 under test.
        assert len(positive.matched) == 1, "positive control produced no match — the matcher is wired to nothing"
        assert len(negative.matched) == 0

        assert positive.subsystem_refs == ("pghero",)
        assert negative.subsystem_refs == ()

    def test_the_zero_is_accounted_for(self, index: sr.SubsystemIndex) -> None:
        """A zero that cannot say WHICH paths it failed to match is not a result."""
        negative = sr.associate_paths(self.NEGATIVE_PATHS, index, SCOPE_A, min_paths=2)
        assert negative.unmatched_paths == tuple(self.NEGATIVE_PATHS)
        assert negative.below_threshold == ()
        assert negative.ambiguous == ()
        assert negative.considered_paths == tuple(self.NEGATIVE_PATHS)

    def test_generic_components_do_not_manufacture_matches(
        self, index: sr.SubsystemIndex
    ) -> None:
        """`clusters`, `apps`, `values.yaml` appear in BOTH sets and name nothing.
        If they matched, the negative control above would be vacuous."""
        for ref in ("clusters", "homelab", "apps", "values", "values.yaml", "kustomization"):
            assert sr.resolve_ref(ref, index, SCOPE_A) is None, ref


# =============================================================================
# associate_paths — the guards. Each control asserts its OWN sentinel.
# =============================================================================


class TestAssociateGuards:
    GOOD = ["clusters/homelab/apps/pghero/values.yaml"]

    def test_unknown_scope(self, index: sr.SubsystemIndex) -> None:
        with pytest.raises(sr.UnknownScopeError) as exc:
            sr.associate_paths(self.GOOD, index, "not-a-scope")
        assert "unknown scope" in str(exc.value)

    def test_empty_path_set_is_an_ORDINARY_input_not_an_error(
        self, index: sr.SubsystemIndex
    ) -> None:
        """A session with no git activity is common, not exceptional.

        This raised `EmptyPathSetError` until review. Making the common case an
        exception forces every P1 caller to wrap the call, and a wrapped call is
        how a caller ends up swallowing the genuine errors too."""
        result = sr.associate_paths([], index, SCOPE_A)
        assert result.matched == ()
        assert result.below_threshold == ()
        assert result.ambiguous == ()
        assert result.considered_paths == ()
        assert result.unmatched_paths == ()
        assert result.scope == SCOPE_A
        assert result.min_paths == sr.DEFAULT_MIN_PATHS

    def test_the_TWO_KINDS_OF_ZERO_stay_distinguishable(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 The property the removed exception was protecting, kept
        structurally.

        Dropping the guard is only safe if a consumer can still tell "we were
        given nothing to look at" from "we looked and found nothing" — otherwise
        the zero really does become manufactured. `considered_paths` is that
        discriminator, and nothing else in the result is."""
        nothing_given = sr.associate_paths([], index, SCOPE_A)
        looked_and_missed = sr.associate_paths(
            ["clusters/homelab/apps/unlisted-widget/values.yaml"], index, SCOPE_A
        )

        # Identical on the field a naive consumer reads …
        assert nothing_given.matched == looked_and_missed.matched == ()
        # … and DIFFERENT on the field that accounts for the input.
        assert nothing_given.considered_paths == ()
        assert looked_and_missed.considered_paths == (
            "clusters/homelab/apps/unlisted-widget/values.yaml",
        )
        assert nothing_given.unmatched_paths == ()
        assert looked_and_missed.unmatched_paths == (
            "clusters/homelab/apps/unlisted-widget/values.yaml",
        )

        # The named affordance over that same discriminator.
        assert nothing_given.looked_at_nothing is True
        assert looked_and_missed.looked_at_nothing is False

    def test_looked_at_nothing_tracks_considered_paths_only(
        self, index: sr.SubsystemIndex
    ) -> None:
        """It must key on whether we LOOKED, never on whether we FOUND — a
        successful match and a total miss are both `looked_at_nothing is False`."""
        matched = sr.associate_paths(
            ["x/pghero/a.yaml", "x/pghero/b.yaml"], index, SCOPE_A, min_paths=2
        )
        assert matched.subsystem_refs == ("pghero",)
        assert matched.looked_at_nothing is False
        assert sr.associate_paths([], index, SCOPE_A).looked_at_nothing is True

    def test_an_unknown_scope_still_raises_even_with_no_paths(
        self, index: sr.SubsystemIndex
    ) -> None:
        """Relaxing the empty-set case must NOT relax the scope case: a typo'd
        scope with no paths is the one combination that could quietly become a
        well-formed empty result."""
        with pytest.raises(sr.UnknownScopeError) as exc:
            sr.associate_paths([], index, "not-a-scope")
        assert "unknown scope" in str(exc.value)

    def test_absolute_path(self, index: sr.SubsystemIndex) -> None:
        with pytest.raises(sr.InvalidPathError) as exc:
            sr.associate_paths(["/home/zach/workspace/x/pghero/values.yaml"], index, SCOPE_A)
        assert "invalid repo-relative path" in str(exc.value)

    def test_parent_traversal(self, index: sr.SubsystemIndex) -> None:
        with pytest.raises(sr.InvalidPathError) as exc:
            sr.associate_paths(["../elsewhere/pghero/values.yaml"], index, SCOPE_A)
        assert "invalid repo-relative path" in str(exc.value)
        assert "escapes the repo root" in str(exc.value)

    def test_empty_string_path(self, index: sr.SubsystemIndex) -> None:
        with pytest.raises(sr.InvalidPathError) as exc:
            sr.associate_paths(["a/b", ""], index, SCOPE_A)
        assert "invalid repo-relative path" in str(exc.value)

    @pytest.mark.parametrize("bad", [0, -1, "2", 2.0, True])
    def test_min_paths_must_be_a_positive_int(
        self, index: sr.SubsystemIndex, bad: object
    ) -> None:
        with pytest.raises(ValueError) as exc:
            sr.associate_paths(self.GOOD, index, SCOPE_A, min_paths=bad)
        assert "min_paths must be an int >= 1" in str(exc.value)

    def test_ambiguity_does_not_raise_here_it_is_recorded(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 One undecidable ref must not blind a whole session — but it must
        also never be silently resolved."""
        result = sr.associate_paths(
            ["scripts/repo-cos/scan.py", "scripts/repo-cos/digest.py"],
            index,
            SCOPE_B,
            min_paths=1,
        )
        assert result.matched == ()
        assert len(result.ambiguous) == 1
        amb = result.ambiguous[0]
        assert amb.ref == "repo-cos"
        assert amb.tier == "filename"
        assert amb.candidates == ("repo-cos.md", "repo-cos.process.md")
        assert amb.paths == ("scripts/repo-cos/scan.py", "scripts/repo-cos/digest.py")

    def test_an_ambiguous_ref_is_not_attributed_to_the_PREVIOUS_component(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 Found by the independent mutation sweep.

        Here `collector` resolves and `repo-cos` is ambiguous, IN THAT ORDER
        within one path. The ambiguity branch must abandon the ref outright — if
        it merely records and falls through, `entry` still holds the previous
        component's match and the ambiguous ref gets attributed to `collector`,
        with `repo-cos` as its evidence component. That is worse than a miss: it
        is a confident wrong answer.
        """
        result = sr.associate_paths(
            ["scripts/collector/repo-cos/emit.py"], index, SCOPE_B, min_paths=1
        )
        assert result.subsystem_refs == ("collector",)
        (match,) = result.matched
        assert [e.component for e in match.evidence] == ["collector"]
        assert len(result.ambiguous) == 1
        assert result.ambiguous[0].ref == "repo-cos"

    def test_an_ambiguous_ref_does_not_stop_an_unambiguous_one(
        self, index: sr.SubsystemIndex
    ) -> None:
        result = sr.associate_paths(
            [
                "scripts/repo-cos/scan.py",
                "scripts/collector/main.py",
                "scripts/collector/emit.py",
            ],
            index,
            SCOPE_B,
            min_paths=2,
        )
        assert result.subsystem_refs == ("collector",)
        assert len(result.ambiguous) == 1


class TestReachability:
    """🔴 `claude/RULES.md`: "prove it REACHABLE, not just breakable" — a
    mutation test still passes when an EARLIER check always wins, so the guard
    never executes.

    Each case below repairs ONLY the element the guard rejects and asserts the
    call then succeeds. That is the proof no earlier guard was the real gate."""

    def test_scope_guard_is_reached_with_a_path_set_that_is_otherwise_fine(
        self, index: sr.SubsystemIndex
    ) -> None:
        # Same paths, same min_paths: only the scope differs, so nothing earlier
        # can be the real gate.
        assert sr.associate_paths(["a/b"], index, SCOPE_A) is not None
        with pytest.raises(sr.UnknownScopeError):
            sr.associate_paths(["a/b"], index, "not-a-scope")

    def test_invalid_path_is_reached_past_the_scope_guard(
        self, index: sr.SubsystemIndex
    ) -> None:
        # A known scope, so only the path check can be firing.
        assert sr.associate_paths(["x/pghero/y.yaml"], index, SCOPE_A) is not None
        with pytest.raises(sr.InvalidPathError):
            sr.associate_paths(["/x/pghero/y.yaml"], index, SCOPE_A)

    def test_min_paths_guard_is_reached_before_the_scope_guard(
        self, index: sr.SubsystemIndex
    ) -> None:
        # Deliberately pinned: min_paths is validated FIRST, so it fires even for
        # an unknown scope. Documented order, asserted rather than assumed.
        with pytest.raises(ValueError):
            sr.associate_paths(["a/b"], index, "not-a-scope", min_paths=0)

    def test_ambiguity_is_reached_with_an_otherwise_valid_call(
        self, index: sr.SubsystemIndex
    ) -> None:
        # Known scope, valid non-empty paths, legal min_paths — the ONLY thing
        # left that can produce the ambiguous record is the ambiguity branch.
        ok = sr.associate_paths(["scripts/collector/a.py"], index, SCOPE_B, min_paths=1)
        assert ok.subsystem_refs == ("collector",)
        amb = sr.associate_paths(["scripts/repo-cos/a.py"], index, SCOPE_B, min_paths=1)
        assert len(amb.ambiguous) == 1

    def test_malformed_entry_is_reached_with_an_otherwise_valid_mapping(self) -> None:
        base = {"service": "fine", "scope": SCOPE_A, "aliases": ["ok"]}
        assert len(sr.build_index([base])) == 1
        broken = dict(base, kind="vendor")
        with pytest.raises(sr.MalformedEntryError):
            sr.build_index([broken])


# =============================================================================
# The precision threshold — boundary at TWO values, not one.
# =============================================================================


class TestPrecisionThreshold:
    """`claude/RULES.md`: "One measurement is not a general claim … measure at
    ≥2 points (a boundary AND a middle)". The threshold is exercised at
    min_paths=2 and min_paths=3, each at below / at / above."""

    @staticmethod
    def _paths(n: int) -> list[str]:
        return [f"clusters/homelab/apps/pghero/file{i}.yaml" for i in range(n)]

    @pytest.mark.parametrize(
        "min_paths,n,expect_matched",
        [
            # --- point 1: min_paths=2 (the default) ---
            (2, 1, False),   # below  — a single grazed file
            (2, 2, True),    # at     — the boundary itself
            (2, 3, True),    # above
            # --- point 2: min_paths=3 ---
            (3, 2, False),   # below
            (3, 3, True),    # at
            (3, 4, True),    # above
            # --- point 3: min_paths=1 admits the graze, by explicit request ---
            (1, 1, True),
        ],
    )
    def test_boundary(
        self, index: sr.SubsystemIndex, min_paths: int, n: int, expect_matched: bool
    ) -> None:
        result = sr.associate_paths(self._paths(n), index, SCOPE_A, min_paths=min_paths)
        if expect_matched:
            assert result.subsystem_refs == ("pghero",)
            assert result.below_threshold == ()
        else:
            assert result.subsystem_refs == ()
            assert [m.entry.slug for m in result.below_threshold] == ["pghero"]

    def test_a_below_threshold_match_is_kept_not_discarded(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 Dropping it would recreate the silent zero one level down: a graze
        would be indistinguishable from never having been seen."""
        result = sr.associate_paths(self._paths(1), index, SCOPE_A, min_paths=2)
        assert result.matched == ()
        assert len(result.below_threshold) == 1
        below = result.below_threshold[0]
        assert below.entry.slug == "pghero"
        assert below.path_count == 1
        assert result.unmatched_paths == ()  # it WAS matched, just not enough

    def test_the_default_is_the_named_constant(self) -> None:
        assert sr.DEFAULT_MIN_PATHS == 2
        result = sr.associate_paths(self._paths(2), index=sr.build_index(ENTRIES), scope=SCOPE_A)
        assert result.min_paths == sr.DEFAULT_MIN_PATHS
        assert result.subsystem_refs == ("pghero",)

    def test_distinct_paths_not_component_hits(self, index: sr.SubsystemIndex) -> None:
        """One path whose components hit the same entry TWICE is still ONE path.

        🔴 This test used to feed `.../pghero/pghero.yaml` and was VACUOUS.
        `path_refs` keys on `(raw_component, ref)`, so the directory and the
        filename stem there collapse to a single pair INSIDE `path_refs` — the
        bucket dedupe in `associate_paths` was never reached, and deleting it
        left the suite green.

        The shape that actually reaches the dedupe is TWO DISTINCT components on
        one path resolving to ONE entry, which is the ordinary live layout
        (an alias directory above a slug directory). Here `ingestion` is
        image-ingestion's alias and `image-ingestion` is its slug: two different
        components, two different refs, one entry.

        Without the dedupe, this single grazed file counts as 2 and clears
        `min_paths=2` on its own — the threshold would measure how many ways a
        path spells a subsystem rather than how much of it the session touched,
        which is precisely the distinction P1's signal quality rests on.
        """
        path = "apps/ingestion/image-ingestion/values.yaml"
        result = sr.associate_paths([path], index, SCOPE_A, min_paths=2)

        assert result.matched == ()
        assert len(result.below_threshold) == 1
        below = result.below_threshold[0]
        assert below.entry.filename == "image-ingestion.md"
        assert below.path_count == 1
        assert below.paths == (path,)

        # Both components WERE seen — this is the two-component shape, not the
        # collapsed one the old fixture accidentally produced.
        assert [e.component for e in below.evidence] == ["ingestion", "image-ingestion"]
        assert [e.tier for e in below.evidence] == ["alias", "filename"]

    def test_path_refs_collapses_a_directory_that_repeats_as_the_stem(self) -> None:
        """The behaviour the old vacuous test was really exercising, asserted at
        the level it actually happens: `path_refs`, not `associate_paths`."""
        assert sr.path_refs("clusters/homelab/apps/pghero/pghero.yaml") == (
            ("clusters", "clusters"),
            ("homelab", "homelab"),
            ("apps", "apps"),
            ("pghero", "pghero"),
            ("pghero.yaml", "pghero.yaml"),
        )


# =============================================================================
# Evidence — the "why" attached to every association.
# =============================================================================


class TestEvidence:
    def test_filename_tier_evidence(self, index: sr.SubsystemIndex) -> None:
        result = sr.associate_paths(
            ["clusters/homelab/apps/pghero/values.yaml"], index, SCOPE_A, min_paths=1
        )
        (match,) = result.matched
        (ev,) = match.evidence
        assert ev.path == "clusters/homelab/apps/pghero/values.yaml"
        assert ev.component == "pghero"
        assert ev.ref == "pghero"
        assert ev.tier == "filename"
        assert ev.matched_alias is None

    def test_alias_tier_evidence_names_the_alias_as_written(
        self, index: sr.SubsystemIndex
    ) -> None:
        result = sr.associate_paths(
            ["services/media_ingestion/worker.py"], index, SCOPE_A, min_paths=1
        )
        (match,) = result.matched
        assert match.entry.filename == "image-ingestion.md"
        (ev,) = match.evidence
        assert ev.component == "media_ingestion"
        assert ev.ref == "media-ingestion"
        assert ev.tier == "alias"
        assert ev.matched_alias == "media_ingestion"

    def test_one_path_can_touch_two_subsystems(self, index: sr.SubsystemIndex) -> None:
        """Two DIFFERENT refs in one path resolving to two entries is not
        ambiguity — ambiguity is one ref naming two entries."""
        result = sr.associate_paths(
            [
                "apps/pghero/object-store/backup.yaml",
                "apps/pghero/object-store/restore.yaml",
            ],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("object-store", "pghero")
        assert result.ambiguous == ()

    def test_matched_refs_are_sorted_and_deduped(self, index: sr.SubsystemIndex) -> None:
        result = sr.associate_paths(
            [
                "a/object-store/x.yaml",
                "a/object-store/y.yaml",
                "b/bar-status-poll/x.sh",
                "b/bar-status-poll/y.sh",
                "b/bar-status-poll/y.sh",  # duplicate input path
            ],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("bar-status-poll", "object-store")
        assert len(result.considered_paths) == 4

    def test_output_order_is_canonical_not_arrival_order(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 Found by an independent mutation sweep: `sorted()` on the result
        was unobservable, because every fixture happened to arrive in sorted
        order. Here the paths arrive in REVERSE ref order, so insertion order
        and canonical order disagree.

        P1 emits one row per (session, subsystem); a re-run over the same
        session must produce the same rows in the same order or a diff of two
        runs shows churn that is not there."""
        result = sr.associate_paths(
            [
                "x/pghero/a.yaml",
                "x/pghero/b.yaml",
                "x/object-store/a.yaml",
                "x/object-store/b.yaml",
                "x/bar-status-poll/a.sh",
                "x/bar-status-poll/b.sh",
            ],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("bar-status-poll", "object-store", "pghero")
        assert [m.entry.ref for m in result.matched] == [
            "bar-status-poll",
            "object-store",
            "pghero",
        ]

    def test_below_threshold_order_is_canonical_too(self, index: sr.SubsystemIndex) -> None:
        result = sr.associate_paths(
            ["x/pghero/a.yaml", "x/object-store/a.yaml", "x/bar-status-poll/a.sh"],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert [m.entry.ref for m in result.below_threshold] == [
            "bar-status-poll",
            "object-store",
            "pghero",
        ]

    def test_ambiguous_order_is_canonical(self) -> None:
        """Same sweep finding, on the `ambiguous` tuple: two ambiguous refs are
        fed in reverse order so insertion order cannot masquerade as sorted."""
        twice_ambiguous = sr.build_index(
            [
                {"service": "zeta", "scope": SCOPE_B, "filename": "zeta.md"},
                {"service": "zeta", "scope": SCOPE_B, "filename": "zeta.process.md"},
                {"service": "alpha", "scope": SCOPE_B, "filename": "alpha.md"},
                {"service": "alpha", "scope": SCOPE_B, "filename": "alpha.org.md"},
            ]
        )
        result = sr.associate_paths(
            ["x/zeta/a.py", "x/alpha/a.py"], twice_ambiguous, SCOPE_B, min_paths=1
        )
        assert [a.ref for a in result.ambiguous] == ["alpha", "zeta"]

    @pytest.mark.parametrize(
        "cls",
        [sr.SubsystemEntry, sr.SubsystemIndex, sr.Evidence, sr.SubsystemMatch,
         sr.AmbiguousRef, sr.Association],
    )
    def test_result_types_are_frozen(self, cls: type) -> None:
        """These are value objects handed to P1. A consumer that can mutate one
        can corrupt a shared index. (Sweep finding: `frozen=True` → `False`
        survived — nothing observed the contract.)"""
        assert cls.__dataclass_params__.frozen is True

    def test_an_entry_cannot_be_mutated(self, index: sr.SubsystemIndex) -> None:
        entry = sr.resolve_ref("pghero", index, SCOPE_A)
        with pytest.raises(Exception):
            entry.slug = "hijacked"  # type: ignore[misc]

    def test_kind_qualified_entry_ref_is_qualified(self, index: sr.SubsystemIndex) -> None:
        result = sr.associate_paths(
            ["docs/repo-cos.process/a.md", "docs/repo-cos.process/b.md"],
            index,
            SCOPE_B,
            min_paths=2,
        )
        assert result.subsystem_refs == ("repo-cos.process",)


# =============================================================================
# Live-corpus path shapes that no other fixture in this file could produce.
# =============================================================================


class TestLiveCorpusPathShapes:
    """🔴 Written from ONE question, after an audit found two vacuous guards that
    shared a blind spot: **which path shapes present in the live store can no
    fixture in this file physically produce?**

    Both the 133-mutant sweep and the hand-written matrix missed those guards
    because both were built from the MODULE's vocabulary — they asked "what can
    I break in the code?", never "what can the corpus feed it?". A mutation
    sweep cannot find a case the fixtures cannot express; it can only find code
    the fixtures already reach.

    Shapes below were measured against the live store on 2026-08-11 (read-only):
    2 aliases contain a SPACE, 2 contain a DOT, 9 carry 2+ hyphens, and the
    dominant layout is a per-service directory under a deep prefix — but a flat
    `<slug>.yaml` beside its siblings is also common, and NO test above ever
    matched on a filename stem alone.
    """

    def test_match_from_the_FILENAME_STEM_alone(self, index: sr.SubsystemIndex) -> None:
        """`clusters/production/apps/<slug>.yaml` — a flat per-service manifest
        with no directory named for the subsystem. Every association test above
        matched on a DIRECTORY component; this one cannot."""
        result = sr.associate_paths(
            [
                "clusters/production/apps/object-store.yaml",
                "clusters/staging/apps/object-store.yaml",
            ],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("object-store",)
        (match,) = result.matched
        assert [e.component for e in match.evidence] == ["object-store", "object-store"]
        assert {e.tier for e in match.evidence} == {"filename"}

    def test_KNOWN_LIMITATION_a_multi_dot_filename_does_not_match_its_first_segment(
        self, index: sr.SubsystemIndex
    ) -> None:
        """🔴 FOUND BY THIS PASS, and pinned as a limitation rather than fixed.

        `object-store.secret.yaml` — a SOPS secret beside its manifest, an
        entirely ordinary GitOps filename — does NOT associate with
        `object-store`. `path_refs` strips exactly one extension, so the stem is
        `object-store.secret`, and `secret` is not in the kind enum, so the whole
        token stays the ref and misses.

        Deliberately NOT changed. Stripping extensions greedily would let any
        dotted filename impersonate a short slug (`redis.bak.old` → `redis`,
        `db.dump.sql` → `db`), and this module's whole precision stance is exact
        component equality. The failure direction here is UNDER-counting, which
        can only demote a subsystem below `min_paths` — never falsely tag one.
        Under-counting is the safe side of a threshold.

        The cost is real and belongs in P1's evaluation: a session editing
        `minio.yaml` and `minio.secret.yaml` scores 1, not 2. If P1 measures that
        this loses material signal, the fix is a kind-enum-style allowlist of
        known infix segments (`secret`, `values`, `patch`), NOT greedy stripping.
        """
        result = sr.associate_paths(
            ["clusters/production/apps/object-store.secret.yaml"],
            index,
            SCOPE_A,
            min_paths=1,
        )
        assert result.subsystem_refs == ()
        assert result.unmatched_paths == (
            "clusters/production/apps/object-store.secret.yaml",
        )
        # The mechanism, pinned at the level it happens.
        assert sr.path_refs("apps/object-store.secret.yaml")[-1] == (
            "object-store.secret",
            "object-store.secret",
        )

    def test_a_SPACE_alias_is_reachable_from_a_path_only_after_normalization(
        self, index: sr.SubsystemIndex
    ) -> None:
        """Two live aliases contain a space (a human wrote them as a phrase).
        A path can never contain one, so such an alias is reachable ONLY because
        normalization folds the space to `-`. `image scan` is the fixture's."""
        result = sr.associate_paths(
            ["services/image-scan/worker.py", "services/image-scan/queue.py"],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("image-ingestion",)
        ev = result.matched[0].evidence[0]
        assert ev.component == "image-scan"
        assert ev.ref == "image-scan"
        assert ev.tier == "alias"
        assert ev.matched_alias == "image scan"  # as WRITTEN, spaces intact

    def test_a_DOTTED_alias_survives_kind_splitting_and_still_matches(
        self, index: sr.SubsystemIndex
    ) -> None:
        """Two live aliases are dotted. The trailing segment is not in the kind
        enum, so the whole token must stay the ref — if `split_kind` were greedy
        the alias would be unreachable."""
        result = sr.associate_paths(["infra/s3.local/config.yaml"], index, SCOPE_A, min_paths=1)
        assert result.subsystem_refs == ("object-store",)
        assert result.matched[0].evidence[0].matched_alias == "s3.local"

    def test_a_SINGLE_COMPONENT_path_at_the_repo_root(
        self, index: sr.SubsystemIndex
    ) -> None:
        """A top-level file with no directory above it. `path_refs` has to cope
        with a one-element split; nothing above exercised that at association
        level."""
        result = sr.associate_paths(["bar-status-poll"], index, SCOPE_A, min_paths=1)
        assert result.subsystem_refs == ("bar-status-poll",)
        assert result.unmatched_paths == ()

    def test_two_paths_spelling_one_subsystem_DIFFERENTLY_both_count(
        self, index: sr.SubsystemIndex
    ) -> None:
        """The complement of `test_distinct_paths_not_component_hits`: two ways
        of spelling one subsystem on the SAME path collapse to one, but on
        DIFFERENT paths they are two genuinely distinct files and must both
        count toward the threshold. Aliases exist precisely so a subsystem can
        be spelled more than one way in a tree."""
        result = sr.associate_paths(
            ["apps/ingestion/worker.py", "apps/image-ingestion/values.yaml"],
            index,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("image-ingestion",)
        (match,) = result.matched
        assert match.path_count == 2
        assert [e.tier for e in match.evidence] == ["alias", "filename"]


# =============================================================================
# The thin disk loader.
# =============================================================================


def _write_entry(dirpath: Path, name: str, body: str) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / name).write_text(body, encoding="utf-8")


class TestLoader:
    def test_legacy_repo_key_is_read_as_scope(self, tmp_path: Path) -> None:
        """MEASURED on the live store 2026-08-10: 21 of 21 entries still spell it
        `repo:`. A loader that only knows `scope:` reads the whole corpus as
        malformed — or, worse, as empty."""
        _write_entry(
            tmp_path / "homelab-talos",
            "pghero.md",
            "---\nservice: pghero\naliases: [pg-hero, pg_hero]\nrepo: homelab-talos\n"
            "namespace: db-pghero\n---\n\n## What it is\nA dashboard.\n",
        )
        loaded = sr.load_index(tmp_path)
        assert loaded.scopes == ("homelab-talos",)
        assert sr.resolve_ref("pg_hero", loaded, "homelab-talos").filename == "pghero.md"

    def test_readme_is_not_an_entry(self, tmp_path: Path) -> None:
        scope = tmp_path / "homelab-talos"
        _write_entry(scope, "README.md", "# store policy\n\nNo remote. No stash.\n")
        _write_entry(scope, "flux.md", "---\nservice: flux\nrepo: homelab-talos\n---\n")
        loaded = sr.load_index(tmp_path)
        assert len(loaded) == 1
        assert sr.resolve_ref("readme", loaded, "homelab-talos") is None

    def test_empty_scope_dir_is_registered_not_dropped(self, tmp_path: Path) -> None:
        (tmp_path / "brand-new-scope").mkdir()
        _write_entry(
            tmp_path / "homelab-talos", "flux.md", "---\nservice: flux\nrepo: homelab-talos\n---\n"
        )
        loaded = sr.load_index(tmp_path)
        assert loaded.scopes == ("brand-new-scope", "homelab-talos")
        # existing-but-empty → honest miss
        assert sr.resolve_ref("flux", loaded, "brand-new-scope") is None
        # never-heard-of → error
        with pytest.raises(sr.UnknownScopeError):
            sr.resolve_ref("flux", loaded, "nope")

    @pytest.mark.parametrize("key", ["scope", "repo"])
    def test_directory_name_beats_a_stale_scope_field(self, tmp_path: Path, key: str) -> None:
        """🔴 This test only wrote `repo:` and was VACUOUS for the `scope:` case.

        The loader pops `repo:` before `from_mapping` ever sees it, so a stale
        `repo:` proves nothing about the override — changing
        `fm["scope"] = dirname` to `fm.setdefault(...)` left the suite green.
        Only `scope:`, the key the #362 migration will start writing, exercises
        it. Harmless today (0 of 21 live entries spell `scope:`) and a live
        defect the moment that changes, which is exactly when nobody would be
        looking at this test.

        With `setdefault`, the entry lands in scope `some-old-name` while
        `homelab-talos` is registered empty — so the file is unreachable under
        the scope it actually lives in.
        """
        _write_entry(
            tmp_path / "homelab-talos",
            "flux.md",
            f"---\nservice: flux\n{key}: some-old-name\n---\n",
        )
        loaded = sr.load_index(tmp_path)
        assert loaded.scopes == ("homelab-talos",)
        assert "some-old-name" not in loaded.scopes
        assert sr.resolve_ref("flux", loaded, "homelab-talos").filename == "flux.md"

    def test_kind_qualified_filename_on_disk(self, tmp_path: Path) -> None:
        scope = tmp_path / "devrc"
        _write_entry(scope, "repo-cos.md", "---\nservice: repo-cos\nrepo: devrc\n---\n")
        _write_entry(
            scope, "repo-cos.process.md", "---\nservice: repo-cos\nrepo: devrc\n---\n"
        )
        loaded = sr.load_index(tmp_path)
        assert sr.resolve_ref("repo-cos.process", loaded, "devrc").filename == "repo-cos.process.md"
        with pytest.raises(sr.AmbiguousRefError):
            sr.resolve_ref("repo-cos", loaded, "devrc")

    def test_a_malformed_file_names_itself(self, tmp_path: Path) -> None:
        _write_entry(tmp_path / "devrc", "broken.md", "---\naliases: [x]\nrepo: devrc\n---\n")
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.load_index(tmp_path)
        assert "broken.md" in str(exc.value)
        assert "missing or empty `service:`" in str(exc.value)

    def test_entries_are_read_in_filename_order(self, tmp_path: Path) -> None:
        """🔴 Sweep finding: the loader's two `sorted()` calls were unobservable.

        Files are CREATED in reverse name order here, so directory order (which
        on many filesystems is creation order) disagrees with the contract."""
        scope = tmp_path / "homelab-talos"
        for name in ("zeta", "mid", "alpha"):
            _write_entry(scope, f"{name}.md", f"---\nservice: {name}\nrepo: homelab-talos\n---\n")
        loaded = sr.load_index(tmp_path)
        assert [e.filename for e in loaded.entries("homelab-talos")] == [
            "alpha.md",
            "mid.md",
            "zeta.md",
        ]

    def test_scopes_are_read_in_directory_order(self, tmp_path: Path) -> None:
        """The FIRST malformed file reached decides which error is reported, so
        scope order is part of the loader's reproducibility, not cosmetics.

        Two scopes, each holding one malformed entry, created zeta-first. The
        error must name the alphabetically-first scope's file every run."""
        for scope_name in ("zeta-scope", "alpha-scope"):
            _write_entry(
                tmp_path / scope_name,
                f"broken-in-{scope_name}.md",
                "---\naliases: [x]\n---\n",
            )
        with pytest.raises(sr.MalformedEntryError) as exc:
            sr.load_index(tmp_path)
        assert "broken-in-alpha-scope.md" in str(exc.value)
        assert "broken-in-zeta-scope.md" not in str(exc.value)


class TestFrontMatterParser:
    """Hand-rolled instead of PyYAML, so the string-only behaviour is pinned."""

    def test_flow_list_and_scalars(self) -> None:
        parsed = sr.parse_front_matter(
            "---\nservice: pghero\naliases: [pg-hero, pg_hero, hero dashboard]\n"
            "repo: homelab-talos\nnamespace: db-pghero\n---\n\nbody\n"
        )
        assert parsed == {
            "service": "pghero",
            "aliases": ["pg-hero", "pg_hero", "hero dashboard"],
            "repo": "homelab-talos",
            "namespace": "db-pghero",
        }

    @pytest.mark.parametrize("token", ["no", "on", "yes", "off", "null"])
    def test_yaml_implicit_typing_is_not_applied(self, token: str) -> None:
        """🔴 The reason this is hand-rolled. PyYAML turns `service: no` into the
        boolean False and `aliases: [on]` into [True]; both then fail to
        normalize as strings, deep inside the resolver rather than here."""
        parsed = sr.parse_front_matter(f"---\nservice: {token}\naliases: [{token}]\n---\n")
        assert parsed["service"] == token
        assert parsed["aliases"] == [token]

    def test_numeric_looking_values_stay_strings(self) -> None:
        parsed = sr.parse_front_matter("---\nservice: 1.0\nrepo: 2026\n---\n")
        assert parsed == {"service": "1.0", "repo": "2026"}

    def test_no_front_matter_is_empty(self) -> None:
        assert sr.parse_front_matter("# just a heading\n") == {}

    # --- the skip branches, each observable on its own -----------------------
    # 🔴 All four survived the independent mutation sweep until these landed:
    # every fixture was well-formed front matter, so no test ever entered a skip
    # branch and `continue` → `pass` changed nothing.

    def test_comment_lines_inside_front_matter_are_skipped(self) -> None:
        parsed = sr.parse_front_matter(
            "---\n# a note to the author\nservice: pghero\nrepo: homelab-talos\n---\n"
        )
        assert parsed == {"service": "pghero", "repo": "homelab-talos"}

    def test_a_comment_CONTAINING_A_COLON_is_still_skipped(self) -> None:
        """🔴 The case that makes the comment skip load-bearing rather than
        decorative. A colon-free comment is also caught by the `no colon` skip
        below it, so only this one can tell the two branches apart — without it,
        `# note: superseded` becomes a key called `# note`."""
        parsed = sr.parse_front_matter(
            "---\n# note: superseded by the newer sheet\nservice: pghero\n---\n"
        )
        assert parsed == {"service": "pghero"}
        assert not any(k.startswith("#") for k in parsed)

    def test_blank_lines_inside_front_matter_are_skipped(self) -> None:
        parsed = sr.parse_front_matter("---\nservice: pghero\n\n   \nrepo: x\n---\n")
        assert parsed == {"service": "pghero", "repo": "x"}

    def test_a_line_with_no_colon_is_skipped(self) -> None:
        parsed = sr.parse_front_matter("---\nservice: pghero\njust some words\nrepo: x\n---\n")
        assert parsed == {"service": "pghero", "repo": "x"}

    def test_a_line_with_an_empty_key_is_skipped(self) -> None:
        parsed = sr.parse_front_matter("---\nservice: pghero\n : orphaned\nrepo: x\n---\n")
        assert parsed == {"service": "pghero", "repo": "x"}

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("[a, b]", ["a", "b"]),          # both brackets → a list
            ("[a, b", "[a, b"),              # opens only → a scalar
            ("a, b]", "a, b]"),              # closes only → a scalar
            ("[]", []),                      # empty list
            ("plain", "plain"),
        ],
    )
    def test_flow_list_needs_BOTH_brackets(self, value: str, expected: object) -> None:
        """A half-bracketed value is a scalar, not a list. With `or` instead of
        `and` here, `a, b]` would be sliced into `["a", "b"` — a silent
        corruption of an alias list."""
        parsed = sr.parse_front_matter(f"---\naliases: {value}\n---\n")
        assert parsed["aliases"] == expected

    def test_quotes_are_stripped(self) -> None:
        parsed = sr.parse_front_matter("---\nservice: \"pghero\"\naliases: ['a', \"b\"]\n---\n")
        assert parsed == {"service": "pghero", "aliases": ["a", "b"]}


# =============================================================================
# 🔴 The prose/code duplication, made DETECTABLE.
# =============================================================================


class TestCommandDocIsPinned:
    """`claude/skills/analyze-service/SKILL.md` states these rules in prose because
    its reader is an LLM. This module states them in Python. That is one
    predicate at two sites, which `claude/RULES.md` says regenerates the same bug
    at both — and here the drift is SILENT (a ref stops resolving, and the miss
    reads as "no index yet").

    It cannot be deduplicated: you cannot import a function into markdown. So it
    is made DETECTABLE, in TWO layers, because the first layer alone has a blind
    spot that an audit demonstrated:

      1. SUBSTRING PINS (`DOC_SENTENCES`) — each row is a normative sentence,
         verbatim. Reword or delete a PINNED sentence and the row names it.
      2. REGION HASHES (`HASHED_REGIONS`) — sha256 over the marked blocks in the
         doc. This catches what layer 1 structurally cannot: an ADDITION, or the
         deletion of a clause nobody thought to pin.

    🔴 WHY LAYER 2 EXISTS. An audit fed this class six semantic doc mutations;
    all fourteen substring pins stayed intact and ALL SIX SURVIVED — including
    deleting the `repo:`→`scope` back-compat clause outright, which was not in
    `DOC_SENTENCES` at all. Addition is also the LIKELIER drift direction here,
    because the doc's editor is an LLM and an LLM appends. A pin can only ever
    see the sentences someone already thought of; a hash sees the block.

    🔴 THIS IS STILL A DRIFT DETECTOR, NOT A PROOF OF AGREEMENT — and weaker
    than "neither moved without the other", which is what this docstring used to
    claim and which was itself too strong. What is actually guaranteed:

      * a change INSIDE a hashed region cannot land silently;
      * a pinned sentence cannot be reworded or removed silently.

    What is NOT guaranteed: prose OUTSIDE the hashed regions can still move
    materially and stay green, and nothing here can tell you the prose and the
    code MEAN the same thing. Read a green as "nothing moved unnoticed in the
    regions we watch", never as "they agree".
    """

    DOC_SENTENCES: list[tuple[str, str]] = [
        (
            "lowercase, `_` → `-`, any other char outside `[a-z0-9.-]` → `-`, "
            "collapsed, trimmed of leading/trailing `-`",
            "the normalization rule itself",
        ),
        (
            "applied identically on read and write **and to `aliases:` before comparing**",
            "aliases are normalized before comparison",
        ),
        (
            "`External DNS` / `externaldns` / `external-dns` land on one file, "
            "and so do `image_ingestion` / `image-ingestion`",
            "the worked normalization example",
        ),
        (
            "bastion_config_stale_until_reload_2026_07_08",
            "the `_`-spelled MEMORY.md slug that makes the `_` fold load-bearing",
        ),
        (
            "kind ∈ `service` | `process` | `org` | `doc`",
            "the kind enum",
        ),
        (
            "A trailing dot-segment is a kind **only if it is in that enum**, "
            "else it's part of the slug.",
            "how a kind suffix is distinguished from a dotted slug",
        ),
        (
            "Bare `<slug>.md` stays the default",
            "unqualified filenames keep working unchanged",
        ),
        (
            "an alias can never outrank a filename",
            "tier ordering",
        ),
        (
            "**Filename tier** — normalized ref vs `<slug>.md` *and* every "
            "`<slug>.<kind>.md` in the scope.",
            "what tier 1 compares",
        ),
        (
            "A ref naming its own kind (`repo-cos.process`) matches only that qualified file.",
            "a kind-qualified ref is exclusive",
        ),
        (
            "consulted **only if tier 1 returned zero hits**",
            "tier 2 is conditional on tier 1 missing",
        ),
        (
            "**>1 in a tier → never pick: stop, call the ref ambiguous and "
            "list the candidates**",
            "ambiguity errors, never shadows",
        ),
        (
            "The EXECUTABLE authority for the two rules above is "
            "`scripts/lib/subsystem_resolver.py`",
            "the pointer naming this module as the authority",
        ),
        (
            "scripts/tests/test_subsystem_resolver.py",
            "the pointer naming this test as the drift detector",
        ),
        # 🔴 ADDED after an audit deleted this clause and all 14 other pins
        # stayed green. `load_index` + `SubsystemEntry.from_mapping` read `repo:`
        # as `scope:` SOLELY because of this sentence, and 21 of 21 live entries
        # still spell it that way — dropping the clause would make the loader's
        # back-compat read look like unexplained legacy cruft to the next editor.
        (
            "**replaces `repo:`**, which older files still carry and reads as `scope`",
            "the repo:->scope back-compat the loader implements",
        ),
    ]

    # (marker name, the code that implements the block) — the doc carries
    # `<!-- <name>:begin -->` / `<!-- <name>:end -->` around each.
    HASHED_REGIONS: list[tuple[str, str, str]] = [
        (
            "resolver-rules",
            "52a56d94431ba4de3c6d696fa35b3ed4e443de612ec17a8f33a049174762e279",
            "normalize_ref / split_kind / resolve_ref_tiered",
        ),
        (
            "entry-schema",
            # Re-pinned when `created_by:` was added to the front-matter schema
            # (the `/handoff` writer). Read against the code before updating, as
            # the failure message demands: `from_mapping` does not read the new
            # field and must not — it is PROVENANCE, not identity, and an entry
            # is addressable without it. `parse_front_matter` preserves it (it
            # preserves unknown keys), `from_mapping` ignores it, and
            # `subsystem_touch.census` is what reads it. The behavioural half of
            # that claim is `test_subsystem_touch.py::TestEntrySchemaAgreement`.
            "66ff2115bf38226e8419abee5dc77d6dd8ff9903e0834f869e6c52e7c54f783c",
            "SubsystemEntry.from_mapping / load_index (+ subsystem_touch.census for created_by)",
        ),
    ]

    @pytest.mark.parametrize(
        "sentence,why", DOC_SENTENCES, ids=[w for _, w in DOC_SENTENCES]
    )
    def test_sentence_still_present(self, doc: str, sentence: str, why: str) -> None:
        assert sentence in doc, (
            f"analyze-service/SKILL.md no longer contains the sentence pinning {why}.\n"
            f"  missing: {sentence!r}\n"
            f"  Either restore it, or change scripts/lib/subsystem_resolver.py in the SAME\n"
            f"  commit and update this pin. The two are one predicate at two sites; the\n"
            f"  drift between them is SILENT (associations stop matching and the zero\n"
            f"  reads as 'this subsystem had no sessions')."
        )

    # --- layer 2: region hashes ----------------------------------------------

    @staticmethod
    def _region(doc: str, name: str) -> str:
        """Bytes between `<!-- <name>:begin -->` and `<!-- <name>:end -->`.

        Raises rather than returning "" on a miss — an empty region would hash
        to a constant and the guard would pass forever against nothing, which is
        the silent-zero shape this whole module exists to avoid.
        """
        # Matched on the marker PREFIX, not the whole comment: both markers carry
        # explanatory prose after the name, and that prose is documentation for
        # the next editor — it must be editable without silently changing which
        # bytes are hashed.
        begin = f"<!-- {name}:begin"
        end = f"<!-- {name}:end"
        i = doc.find(begin)
        assert i != -1, f"marker {begin!r} is missing from analyze-service/SKILL.md"
        i = doc.index("-->", i) + len("-->")
        j = doc.find(end, i)
        assert j != -1, f"marker {end!r} is missing from analyze-service/SKILL.md"
        body = doc[i:j].strip()
        assert body, f"region {name!r} is EMPTY — the hash would guard nothing"
        return body

    @pytest.mark.parametrize(
        "name,expected_sha,implemented_by",
        HASHED_REGIONS,
        ids=[n for n, _, _ in HASHED_REGIONS],
    )
    def test_region_hash(
        self, doc: str, name: str, expected_sha: str, implemented_by: str
    ) -> None:
        actual = hashlib.sha256(self._region(doc, name).encode("utf-8")).hexdigest()
        assert actual == expected_sha, (
            f"\nThe `{name}` block of claude/skills/analyze-service/SKILL.md CHANGED.\n"
            f"  expected sha256 {expected_sha}\n"
            f"  actual   sha256 {actual}\n\n"
            f"This is not a formatting nit. That block is the PROSE HALF of a\n"
            f"predicate whose code half is {implemented_by} in\n"
            f"scripts/lib/subsystem_resolver.py. A substring pin cannot see a\n"
            f"sentence being ADDED, or an unpinned clause being deleted — this\n"
            f"hash is what does.\n\n"
            f"So: re-read the block against the code, make them agree, then paste\n"
            f"the actual sha above into HASHED_REGIONS in the SAME commit.\n"
            f"Updating the hash WITHOUT reading the code is the one way to make\n"
            f"this guard worthless."
        )

    def test_the_region_extractor_can_observe_a_change(self, doc: str) -> None:
        """🔴 Positive control on layer 2. A hash comparison that always passes
        is indistinguishable from one wired to a constant, so prove the hash
        MOVES when the region does — by hashing a deliberately altered copy."""
        for name, expected_sha, _ in self.HASHED_REGIONS:
            body = self._region(doc, name)
            mutated = body.replace("normalized", "NORMALISED", 1) + "\n- an added bullet"
            assert mutated != body
            assert (
                hashlib.sha256(mutated.encode("utf-8")).hexdigest() != expected_sha
            ), f"region {name!r}: the hash did not move for a changed body"

    def test_a_missing_marker_fails_loudly(self) -> None:
        """Negative control on the extractor: a region it cannot find must raise,
        never silently hash the empty string."""
        with pytest.raises(AssertionError) as exc:
            self._region("no markers here at all\n", "resolver-rules")
        assert "is missing from analyze-service/SKILL.md" in str(exc.value)

    def test_an_empty_region_fails_loudly(self) -> None:
        with pytest.raises(AssertionError) as exc:
            self._region(
                "<!-- resolver-rules:begin -->\n\n<!-- resolver-rules:end -->\n",
                "resolver-rules",
            )
        assert "is EMPTY — the hash would guard nothing" in str(exc.value)

    def test_the_pin_can_fail(self, doc: str) -> None:
        """Negative control on the pin itself: it must be able to report absence.

        Without this, a `in doc` check against a doc that happened to contain
        everything is indistinguishable from a check pointed at the wrong file."""
        sentinel = "a sentence that is deliberately not in analyze-service/SKILL.md"
        assert sentinel not in doc

    def test_the_doc_path_is_the_deployed_one(self) -> None:
        """Pinning a file that is not the one that SHIPS would be a vacuous green.

        `claude/skills/` is the managed source symlinked to `~/.claude/skills/`.
        The path shape alone is a tautology against the constant above, so the
        load-bearing half is the `nix/home.nix` check: it is what makes this a
        claim about DEPLOYMENT rather than about this file's own spelling.

        This test earned its keep — it is what went red when the commands→skills
        migration moved the doc out from under a pin written against the old
        `claude/commands/analyze-service.md` path.
        """
        assert SKILL_DOC.exists(), f"the pinned doc is gone: {SKILL_DOC}"
        assert SKILL_DOC.name == "SKILL.md"
        assert SKILL_DOC.parent.name == "analyze-service"
        assert SKILL_DOC.parent.parent.name == "skills"

        # The non-tautological half: nix must actually deploy the directory this
        # pin lives under. A pin under a directory home-manager does not ship is
        # precisely the vacuous green this test exists to prevent.
        # One shared, STRUCTURAL predicate (testlib/skills_mapping.py) instead of
        # the literal `source = ../claude/skills;` this used to grep for: the
        # mapping's source is now a derivation BUILT from that path, which the
        # spelled version could not tell apart from a source pointed elsewhere.
        home_nix = (ROOT / "nix" / "home.nix").read_text(encoding="utf-8")
        assert_skills_mapping_deploys_repo_skills(home_nix)

    # --- the behavioural half: what each sentence ASSERTS ---------------------

    def test_behaviour_underscore_fold(self) -> None:
        assert sr.normalize_ref("image_ingestion") == sr.normalize_ref("image-ingestion")
        assert (
            sr.normalize_ref("bastion_config_stale_until_reload_2026_07_08")
            == "bastion-config-stale-until-reload-2026-07-08"
        )

    def test_behaviour_kind_enum(self) -> None:
        assert set(sr.KINDS) == {"service", "process", "org", "doc"}
        assert sr.split_kind("x.process") == ("x", "process")
        assert sr.split_kind("x.vendor") == ("x.vendor", None)

    def test_behaviour_tier_order(self, index: sr.SubsystemIndex) -> None:
        assert sr.resolve_ref_tiered("blob-upload", index, SCOPE_A)[1] == "filename"

    def test_behaviour_ambiguity_never_picks(self, index: sr.SubsystemIndex) -> None:
        with pytest.raises(sr.AmbiguousRefError):
            sr.resolve_ref("repo-cos", index, SCOPE_B)


# =============================================================================
# 🔴 MUTATION KILL MATRIX — every guard is broken on purpose, in-suite.
# =============================================================================


def _load_mutant(tmp_path: Path, name: str, replacements: list[tuple[str, str]]):
    """Import a copy of the module with the named guard(s) neutered.

    🔴 The anchor-uniqueness assert is not decoration. `claude/RULES.md`: "A
    count=1 text replace on a pattern that occurs more than once is a live
    hazard" — a mutation applied to the wrong occurrence produces a mutant that
    is green for reasons nobody inspected.
    """
    src = MODULE_PATH.read_text(encoding="utf-8")
    for old, new in replacements:
        n = src.count(old)
        assert n == 1, f"mutation anchor occurs {n}x, expected exactly 1: {old!r}"
        src = src.replace(old, new)
    path = tmp_path / f"{name}.py"
    path.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 🔴 Registered BEFORE exec_module, and left registered. `@dataclass`
    # resolves string annotations (`from __future__ import annotations`) by
    # looking the defining class's module up in `sys.modules`; an unregistered
    # mutant makes that lookup return None and every dataclass in the file dies
    # with `AttributeError: 'NoneType' object has no attribute '__dict__'` —
    # which would read as "the mutation broke the module", i.e. a mutation test
    # passing for a reason that has nothing to do with the guard.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _mutant_index(mod, entries=None, extra=(SCOPE_EMPTY,)):
    return mod.build_index(entries if entries is not None else ENTRIES, extra_scopes=extra)


class TestMutationKillMatrix:
    """`claude/RULES.md`: "break it, confirm a test fails with THIS guard's
    specific error/exit code, then reach it with a case no earlier check
    rejects."

    Each test below deletes ONE guard from a copy of the source and asserts the
    corresponding expectation ABOVE no longer holds. A guard whose mutant still
    satisfies its test is a guard the suite is not actually measuring.

    The mutants are imported under unique module names from `tmp_path`; nothing
    on disk in the repo is touched, and the real module is never reloaded.
    """

    def test_kills_missing_service_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_service",
            [
                (
                    "        if not isinstance(raw_service, str) or not raw_service.strip():",
                    "        if False:",
                )
            ],
        )
        with pytest.raises(Exception) as exc:
            mod.build_index([{"scope": SCOPE_A}])
        assert "missing or empty `service:`" not in str(exc.value)

    def test_kills_unknown_scope_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_scope",
            [("        if key not in self.by_scope:", "        if False:")],
        )
        idx = _mutant_index(mod)
        with pytest.raises(Exception) as exc:
            mod.resolve_ref("pghero", idx, "typo-scope")
        assert not isinstance(exc.value, mod.UnknownScopeError)
        assert "unknown scope" not in str(exc.value)

    def test_kills_filename_tier_ambiguity_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_amb_file",
            [
                (
                    '    if len(hits) > 1:\n'
                    '        raise AmbiguousRefError(nref, "filename", '
                    'sorted(e.filename for e in hits), scope)',
                    "    if False:\n        pass",
                )
            ],
        )
        idx = _mutant_index(mod)
        # The guard is gone: the resolver now SHADOWS, silently picking one.
        picked = mod.resolve_ref("repo-cos", idx, SCOPE_B)
        assert picked is not None
        assert picked.filename in ("repo-cos.md", "repo-cos.process.md")

    def test_kills_alias_tier_ambiguity_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_amb_alias",
            [
                (
                    '    if len(hits) > 1:\n'
                    '        raise AmbiguousRefError(nref, "alias", '
                    'sorted(e.filename for e in hits), scope)',
                    "    if False:\n        pass",
                )
            ],
        )
        idx = mod.build_index(
            [
                {"service": "left", "scope": SCOPE_A, "aliases": ["shared-name"]},
                {"service": "right", "scope": SCOPE_A, "aliases": ["shared-name"]},
            ]
        )
        assert mod.resolve_ref("shared-name", idx, SCOPE_A) is not None

    def test_kills_tier_ordering(self, tmp_path: Path) -> None:
        """Neuter the tier-1 RETURN so the alias tier decides. This is the exact
        shadow bug the decision record describes."""
        mod = _load_mutant(
            tmp_path,
            "m_tier_order",
            [('    if hits:\n        return hits[0], "filename"', "    if False:\n        pass")],
        )
        idx = _mutant_index(mod)
        shadowed = mod.resolve_ref("blob-upload", idx, SCOPE_A)
        assert shadowed is not None
        assert shadowed.filename == "image-ingestion.md"  # WRONG entry — guard dead

    def test_kills_the_two_kinds_of_zero_discriminator(self, tmp_path: Path) -> None:
        """`considered_paths` is what replaced `EmptyPathSetError`. If it stops
        accounting for the input, an empty path set and a path set that matched
        nothing become byte-identical results — which is the manufactured zero
        the exception used to prevent, arrived at by a different route."""
        mod = _load_mutant(
            tmp_path,
            "m_considered",
            [("        considered_paths=tuple(ordered),", "        considered_paths=(),")],
        )
        idx = _mutant_index(mod)
        nothing_given = mod.associate_paths([], idx, SCOPE_A)
        looked_and_missed = mod.associate_paths(
            ["clusters/homelab/apps/unlisted-widget/values.yaml"], idx, SCOPE_A
        )
        assert nothing_given.considered_paths == looked_and_missed.considered_paths == ()

    def test_kills_the_unmatched_paths_accounting(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_unmatched",
            [
                (
                    "        unmatched_paths=tuple(p for p in ordered if p not in matched_paths),",
                    "        unmatched_paths=(),",
                )
            ],
        )
        idx = _mutant_index(mod)
        result = mod.associate_paths(
            ["clusters/homelab/apps/unlisted-widget/values.yaml"], idx, SCOPE_A
        )
        # A zero that no longer says what it failed to match.
        assert result.matched == () and result.unmatched_paths == ()

    def test_kills_absolute_path_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path, "m_abs", [('    if path.startswith("/"):', "    if False:")]
        )
        idx = _mutant_index(mod)
        result = mod.associate_paths(
            ["/home/zach/workspace/x/pghero/a.yaml", "/home/zach/workspace/x/pghero/b.yaml"],
            idx,
            SCOPE_A,
            min_paths=2,
        )
        assert result.subsystem_refs == ("pghero",)  # accepted, where the real one raises

    def test_kills_parent_traversal_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(tmp_path, "m_dotdot", [('    if ".." in parts:', "    if False:")])
        idx = _mutant_index(mod)
        assert mod.associate_paths(["../x/pghero/a.yaml"], idx, SCOPE_A, min_paths=1) is not None

    def test_kills_the_underscore_fold(self, tmp_path: Path) -> None:
        """🔴 This mutation is why `normalize_ref` no longer opens with an
        explicit `.replace("_", "-")`. That line mirrored the doc's "`_` → `-`"
        clause and read as the code implementing it — but `_` is already outside
        `[a-z0-9.-]`, so deleting the line changed nothing and the mutant stayed
        green. An unkillable mutation means the guard is not where it looks like
        it is. The fold lives in the character class alone now, so admitting `_`
        to that class is what kills it."""
        mod = _load_mutant(
            tmp_path,
            "m_fold",
            [('_NON_SLUG = re.compile(r"[^a-z0-9.-]")', '_NON_SLUG = re.compile(r"[^a-z0-9._-]")')],
        )
        assert mod.normalize_ref("image_ingestion") != "image-ingestion"
        idx = _mutant_index(mod)
        assert mod.resolve_ref("Image_Ingestion", idx, SCOPE_A) is None

    def test_kills_alias_normalization(self, tmp_path: Path) -> None:
        """Aliases must be normalized BEFORE comparison. `media_ingestion` is the
        only underscore-spelled alias with no `-` twin, so it is the case a
        stored-raw alias set cannot satisfy."""
        mod = _load_mutant(
            tmp_path, "m_alias_norm", [("            na = normalize_ref(alias)", "            na = alias")]
        )
        idx = _mutant_index(mod)
        assert mod.resolve_ref("media-ingestion", idx, SCOPE_A) is None

    def test_kills_exact_match_becoming_substring(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_substring",
            [
                (
                    "        hits = [e for e in entries if e.slug == nref]",
                    "        hits = [e for e in entries if nref in e.slug]",
                )
            ],
        )
        idx = _mutant_index(mod)
        assert mod.resolve_ref("store", idx, SCOPE_A) is not None  # real one returns None

    def test_kills_the_precision_threshold(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_threshold",
            [
                (
                    "        (matched if m.path_count >= min_paths else below).append(m)",
                    "        (matched if m.path_count >= 1 else below).append(m)",
                )
            ],
        )
        idx = _mutant_index(mod)
        result = mod.associate_paths(
            ["clusters/homelab/apps/pghero/file0.yaml"], idx, SCOPE_A, min_paths=3
        )
        assert result.subsystem_refs == ("pghero",)  # a single graze now tags it

    def test_kills_the_duplicate_entry_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(tmp_path, "m_dup", [("        if key in seen:", "        if False:")])
        dup = {"service": "twice", "scope": SCOPE_A}
        idx = mod.build_index([dup, dict(dup)])
        # Two identical entries now coexist and every ref to them is ambiguous —
        # a corrupt index accepted silently at load.
        with pytest.raises(mod.AmbiguousRefError):
            mod.resolve_ref("twice", idx, SCOPE_A)

    def test_kills_the_kind_enum_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_kind",
            [
                (
                    "            if not isinstance(declared_kind, str) or "
                    "normalize_ref(declared_kind) not in KINDS:",
                    "            if False:",
                )
            ],
        )
        idx = mod.build_index([{"service": "x", "scope": SCOPE_A, "kind": "vendor"}])
        assert len(idx) == 1  # a kind outside the enum accepted

    def test_kills_the_filename_slug_agreement_guard(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path, "m_agree", [("            if file_slug != slug_all:", "            if False:")]
        )
        idx = mod.build_index([{"service": "alpha", "scope": SCOPE_A, "filename": "beta.md"}])
        # `alpha` now points at a file called beta.md: a ref that reaches the
        # wrong sheet, which is exactly what the guard exists to stop.
        assert mod.resolve_ref("alpha", idx, SCOPE_A).filename == "beta.md"

    def test_kills_the_readme_exclusion(self, tmp_path: Path) -> None:
        mod = _load_mutant(
            tmp_path,
            "m_readme",
            [
                (
                    '            if md.name == "README.md":\n                continue',
                    "            if False:\n                continue",
                )
            ],
        )
        store = tmp_path / "store" / "homelab-talos"
        store.mkdir(parents=True)
        (store / "README.md").write_text("# store policy\n", encoding="utf-8")
        (store / "flux.md").write_text(
            "---\nservice: flux\nrepo: homelab-talos\n---\n", encoding="utf-8"
        )
        with pytest.raises(mod.MalformedEntryError):
            mod.load_index(tmp_path / "store")

    def test_the_harness_itself_can_report_a_bad_anchor(self, tmp_path: Path) -> None:
        """Negative control on the mutation harness: an anchor that is not unique
        must abort rather than silently mutate the wrong occurrence."""
        with pytest.raises(AssertionError) as exc:
            _load_mutant(tmp_path, "m_bad", [("return", "return")])
        assert "mutation anchor occurs" in str(exc.value)

    def test_the_harness_produces_a_WORKING_mutant(self, tmp_path: Path) -> None:
        """Positive control on the mutation harness: with a no-op replacement the
        mutant must behave exactly like the real module.

        Without this, every "the guard is dead" assertion above could equally be
        explained by a mutant that fails to import or is subtly broken."""
        mod = _load_mutant(
            tmp_path,
            "m_noop",
            [("DEFAULT_MIN_PATHS = 2", "DEFAULT_MIN_PATHS = 2  # noqa")],
        )
        idx = _mutant_index(mod)
        assert mod.resolve_ref("blob-upload", idx, SCOPE_A).filename == "blob-upload.md"
        with pytest.raises(mod.AmbiguousRefError):
            mod.resolve_ref("repo-cos", idx, SCOPE_B)
        with pytest.raises(mod.UnknownScopeError):
            mod.associate_paths(["a/b"], idx, "not-a-scope")
        assert mod.associate_paths([], idx, SCOPE_A).considered_paths == ()


# =============================================================================
# Entry markdown shape — the ONE parser both readers share.
#
# `extract_sections` and its two mutation kills MOVED here from
# test_subsystem_recall.py when the parser itself moved down into this module:
# `subsystem_touch` has to show a `/handoff` what an entry ALREADY records
# before proposing an append, `subsystem_recall` already imports
# `subsystem_touch`, and a copy in the writer would be a second parser free to
# drift from the one measured against the real corpus.
#
# `parse_journal_bullets` is new here for the same reason.
#
# 🔴 EVERY FIXTURE BELOW IS SYNTHETIC. This repo is PUBLIC and the real store is
# client-confidential; the SHAPES are measured from the live corpus (2026-08-12,
# read-only: 26 entries, 110 top-level bullets all at indent 0, 250 continuation
# lines all at indent 2, 62 bullets dated and 48 not, longest bullet 19 lines),
# the CONTENT is invented.
# =============================================================================

HEADINGS = (sr.POINTERS_HEADING, sr.NUANCE_HEADING)

# A body in the shape the corpus actually has: wrapped multi-line prose, with a
# mixture of dated and undated bullets. NOT one-liners.
WRAPPED_BODY = (
    "- 2026-03-04: the retry budget is per-batch, not per-item, so a batch of\n"
    "  400 with one poison record burns the whole budget and the other 399 are\n"
    "  never attempted.\n"
    "- an undated note, of the kind 48 of the corpus's 110 bullets are,\n"
    "  and which must still be parsed and shown\n"
    "- 2026-02-11: the flush interval is a floor, not a schedule.\n"
)


class TestEntryMarkdownShape:
    """Section extraction. The parser is unchanged by the move; these are the
    behaviours it was verified with, re-homed with it."""

    def test_a_heading_inside_a_fence_does_not_end_the_section(self) -> None:
        """🔴 Otherwise HALF an entry's nuance is surfaced while the output looks
        like a complete read — a silent under-report."""
        text = (
            "## Pointers\n- p\n\n"
            "## Nuance / work-history\n"
            "- 2026-01-01: run this:\n```\n## not a heading\n```\n"
            "- 2026-01-02: the SECOND bullet, after the fence\n"
        )
        got = sr.extract_sections(text, HEADINGS)
        assert "the SECOND bullet, after the fence" in got[sr.NUANCE_HEADING]

    def test_a_present_but_EMPTY_section_is_present_not_absent(self) -> None:
        mid = sr.extract_sections("## Pointers\n\n## Nuance / work-history\n- x\n", HEADINGS)
        assert sr.POINTERS_HEADING in mid and mid[sr.POINTERS_HEADING] == ""
        end = sr.extract_sections("## Nuance / work-history\n- x\n\n## Pointers\n", HEADINGS)
        assert sr.POINTERS_HEADING in end and end[sr.POINTERS_HEADING] == ""

    def test_an_absent_section_is_absent(self) -> None:
        assert sr.NUANCE_HEADING not in sr.extract_sections("## Pointers\n- p\n", HEADINGS)


class TestJournalBullets:
    """🔴 THE POSITIVE CONTROL COMES FIRST, and its pair with it. An empty
    result is indistinguishable from a parser wired to nothing, so "it found
    nothing" is only a reading once this same parser has been watched to find
    something."""

    def test_POSITIVE_CONTROL_a_real_shaped_body_yields_its_bullets(self) -> None:
        got = sr.parse_journal_bullets(WRAPPED_BODY)
        assert len(got) == 3, "the parser observed no bullets in a body that has three"
        assert [b.date for b in got] == ["2026-03-04", None, "2026-02-11"]

    def test_NEGATIVE_PAIR_an_empty_body_yields_none(self) -> None:
        """Reported WITH the count above: 3 under test, 0 on the empty body. A
        bare 0 from a parser never shown to produce non-zero is not evidence."""
        assert sr.parse_journal_bullets("") == ()
        assert sr.parse_journal_bullets("\n\n") == ()

    def test_a_bullet_keeps_its_CONTINUATION_LINES_verbatim(self) -> None:
        """🔴 The corpus is wrapped prose — 110 bullets carry 250 continuation
        lines between them. A parser that took one line per bullet would truncate
        most real entries, and a truncated bullet is one an agent cannot
        recognize as a near-duplicate of the line it is about to write."""
        first = sr.parse_journal_bullets(WRAPPED_BODY)[0]
        assert len(first.lines) == 3
        assert first.lines[1] == (
            "  400 with one poison record burns the whole budget and the other 399 are"
        )
        assert first.text.endswith("never attempted.")

    def test_an_INDENTED_dash_is_a_continuation_not_a_new_bullet(self) -> None:
        """Measured: every one of the corpus's 110 bullets is at indent 0 and
        every one of its 250 continuation lines is at indent 2. Folding them
        together would split one bullet into several and report a longer history
        than the entry has."""
        got = sr.parse_journal_bullets("- 2026-01-01: parent\n  - nested item\n  - another\n")
        assert len(got) == 1
        assert len(got[0].lines) == 3

    def test_a_dash_inside_a_FENCE_is_not_a_bullet(self) -> None:
        got = sr.parse_journal_bullets(
            "- 2026-01-01: run:\n```\n- not-a-bullet\n```\n- 2026-01-02: b\n"
        )
        assert len(got) == 2
        assert "- not-a-bullet" in got[0].text

    def test_an_UNDATED_bullet_is_an_ordinary_reading_not_a_failure(self) -> None:
        """44% of the real corpus. A parser that required a date would drop them
        on the floor and call the result a complete read."""
        got = sr.parse_journal_bullets("- no date here at all\n")
        assert len(got) == 1 and got[0].date is None

    def test_a_SHAPED_but_IMPOSSIBLE_date_is_rejected(self) -> None:
        """`2026-13-45` matches the shape and is not a date. Returning it would
        put a nonexistent day into a recency claim and into arithmetic on it."""
        assert sr.parse_journal_bullets("- 2026-13-45: x\n")[0].date is None
        assert sr.parse_journal_bullets("- 2026-02-30: x\n")[0].date is None

    def test_a_date_must_START_the_bullet(self) -> None:
        assert sr.parse_journal_bullets("- fixed on 2026-01-01 by hand\n")[0].date is None

    def test_asterisk_bullets_parse_too(self) -> None:
        got = sr.parse_journal_bullets("* 2026-01-01: a\n* 2026-01-02: b\n")
        assert [b.date for b in got] == ["2026-01-01", "2026-01-02"]

    def test_trailing_blank_lines_do_not_inflate_a_bullet(self) -> None:
        got = sr.parse_journal_bullets("- 2026-01-01: a\n\n\n- 2026-01-02: b\n")
        assert [len(b.lines) for b in got] == [1, 1]

    def test_text_BEFORE_the_first_bullet_yields_no_bullet(self) -> None:
        """Its own state for the caller to report — `subsystem_touch` renders it
        as `unbulleted`, never as an empty history."""
        assert sr.parse_journal_bullets("some prose the schema does not allow\n") == ()

    def test_ORDER_IS_PRESERVED_and_no_recency_is_claimed(self) -> None:
        """This function makes no newest-first claim; recency is derived from the
        DATES by the caller, because newest-first is a convention a past appender
        can have broken."""
        got = sr.parse_journal_bullets("- 2026-01-01: old first\n- 2026-09-09: new second\n")
        assert [b.date for b in got] == ["2026-01-01", "2026-09-09"]


class TestEntryMarkdownMutationKills:
    """One kill per guard in the shared parser. Two came from
    test_subsystem_recall.py with the code; the rest are new."""

    def test_kills_the_section_fence_skip(self, tmp_path: Path) -> None:
        """Relocated. Without it a `#` inside a fence ends the section early —
        HALF an entry's nuance, looking exactly like a complete read."""
        mod = _load_mutant(
            tmp_path,
            "m_sec_fence",
            [(
                "        if _is_fence(line):\n            in_fence = not in_fence\n"
                "            if current is not None:",
                "        if False:\n            in_fence = not in_fence\n"
                "            if current is not None:",
            )],
        )
        text = "## Nuance / work-history\n- a\n```\n## not a heading\n```\n- the SECOND bullet\n"
        got = mod.extract_sections(text, HEADINGS)
        assert "the SECOND bullet" not in got.get(sr.NUANCE_HEADING, "")

    def test_kills_the_present_but_empty_tracking(self, tmp_path: Path) -> None:
        """Relocated. Without it presence is derived from content, so an empty
        section mid-file reads ABSENT while the same section at EOF reads
        present — two answers to one question."""
        mod = _load_mutant(
            tmp_path,
            "m_seen",
            [("            if current is not None:\n                seen.add(current)",
              "            if False:\n                seen.add(current)")],
        )
        got = mod.extract_sections(
            "## Pointers\n- p\n## Nuance / work-history\n- n\n", HEADINGS
        )
        assert got == {}, "the presence tracking was not what produced the keys"

    def test_kills_the_bullet_fence_skip(self, tmp_path: Path) -> None:
        """Without it a `- ` line inside a fence is promoted to a bullet and the
        display invents history the entry does not have."""
        mod = _load_mutant(
            tmp_path,
            "m_bul_fence",
            [(
                "        if _is_fence(line):\n            in_fence = not in_fence\n"
                "            if bullets:",
                "        if False:\n            in_fence = not in_fence\n"
                "            if bullets:",
            )],
        )
        got = mod.parse_journal_bullets("- 2026-01-01: run:\n```\n- not-a-bullet\n```\n")
        assert len(got) != 1, "the fence skip was not what kept the sample line out"

    def test_kills_the_column_zero_bullet_rule(self, tmp_path: Path) -> None:
        """Without it an indented continuation becomes its own bullet, and one
        wrapped bullet is reported as three."""
        mod = _load_mutant(
            tmp_path,
            "m_col0",
            [('_JOURNAL_BULLET = re.compile(r"^[-*][ \\t]+")',
              '_JOURNAL_BULLET = re.compile(r"^\\s*[-*][ \\t]+")')],
        )
        got = mod.parse_journal_bullets("- 2026-01-01: parent\n  - nested item\n  - another\n")
        assert len(got) != 1, "the column-0 rule was not what grouped the nested lines"

    def test_kills_the_date_VALIDATION(self, tmp_path: Path) -> None:
        """Without it a shaped-but-impossible date is returned as real, and every
        recency claim built on it is arithmetic on a day that does not exist."""
        mod = _load_mutant(
            tmp_path,
            "m_date_valid",
            [("        _date.fromisoformat(m.group(1))", "        pass")],
        )
        assert mod.parse_journal_bullets("- 2026-13-45: x\n")[0].date == "2026-13-45"

    def test_kills_the_trailing_blank_strip(self, tmp_path: Path) -> None:
        """Without it a blank separator inflates the preceding bullet's line
        count, so the per-bullet display cap spends its budget on blanks."""
        mod = _load_mutant(
            tmp_path,
            "m_blank_strip",
            [("        while group and not group[-1].strip():", "        while False:")],
        )
        got = mod.parse_journal_bullets("- 2026-01-01: a\n\n\n- 2026-01-02: b\n")
        assert len(got[0].lines) != 1

    def test_the_harness_produces_a_WORKING_mutant_for_these_anchors(
        self, tmp_path: Path
    ) -> None:
        """Positive control on the harness for THIS section: with a no-op
        replacement the mutant must parse exactly as the real module does."""
        mod = _load_mutant(
            tmp_path,
            "m_shape_noop",
            [('POINTERS_HEADING = "## Pointers"', 'POINTERS_HEADING = "## Pointers"  # noqa')],
        )
        assert [b.date for b in mod.parse_journal_bullets(WRAPPED_BODY)] == [
            "2026-03-04",
            None,
            "2026-02-11",
        ]
