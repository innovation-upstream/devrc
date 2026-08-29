#!/usr/bin/env python3
"""The `tasks:` front-matter schema — cg#428 Layer A.

WHAT THIS PINS, and why each case exists rather than being obvious:

  * A subsystem entry can say which tasks it answers, in ANY task system. The
    parser enumerates none of them; only URL resolution is system-specific, and
    that lives elsewhere (Layer B, which imports the ref grammar shipped by
    devrc#1011 rather than growing a second one).
  * The id half is preserved BYTE-IDENTICALLY, `#` included. That is the whole
    reason this schema exists: GitHub's lossless form is `owner/repo#N`, and any
    encoding that cannot carry a `#` cannot carry a GitHub reference. The
    round-trip test writes a file, reads it back through the REAL loader and
    compares bytes — not two calls to the same in-memory parser.
  * Both the inline and block list forms parse. Before this change the block
    form did not merely get ignored: it CORRUPTED the mapping, promoting a list
    item to a phantom front-matter key by that item's own internal colon. There
    is a test for exactly that shape, because "ignored" was the documented
    belief and it was wrong.

🔴 WHICH OF THESE ARE REGRESSION TESTS, STATED HONESTLY — because most of them
are NOT, and counting them as such would overstate the coverage this file
provides (`claude/RULES.md`: a guard's description claims coverage).

  REGRESSION (watched to fail on `origin/main`'s reader, for their OWN reason):
    `test_the_block_form_no_longer_promotes_an_item_to_a_PHANTOM_KEY`
    `test_a_key_AFTER_a_block_list_is_still_read`
  Both exercise `parse_front_matter`, which already existed, and both go red on
  pre-change code with the corruption reproduced verbatim::

    {'service': 'thing', 'tasks': '',
     '- clickup': '868kx9eut',
     '- github': 'innovation-upstream/devrc#428'}

  INVARIANT GUARDS (green before AND after — they pin behaviour this change must
  NOT alter, and they are not evidence that anything was fixed):
    `test_a_BARE_key_with_no_items_still_reads_as_a_STRING`
    `test_the_LIVE_STORE_shape_is_unaffected_by_the_widening`

  NEW-FEATURE TESTS (everything else). On pre-change code these can only raise
  `ImportError` at collection, because the names they call do not exist yet.
  That is a collection failure, not a per-test red, and it is deliberately NOT
  described as one — a module-wide ImportError fails every test for one reason
  and so discriminates between none of them.

Hermetic: builds its own store in a tmp dir and NEVER reads the real one, which
is client-confidential and rewritten by a timer.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

from subsystem_resolver import (  # noqa: E402
    MalformedEntryError,
    SubsystemEntry,
    TaskRef,
    TaskRefError,
    entry_mapping,
    format_task_refs,
    lossy_tag_for,
    parse_front_matter,
    parse_task_ref,
)

RECALL = REPO / "scripts" / "lib" / "subsystem_recall.py"
TOUCH = REPO / "scripts" / "lib" / "subsystem_touch.py"

# A ref per shape that matters, kept in one place so a widening cannot quietly
# drop one. The GitHub one is the reason the `#` cases exist; the Linear and Jira
# ones are systems that appear NOWHERE in the source and must still round-trip.
CLICKUP = "clickup:868kx9eut"
GITHUB = "github:innovation-upstream/devrc#428"
CLAWGATE = "clawgate:428"
LINEAR = "linear:ENG-441"


def write_entry(store: Path, scope: str, slug: str, front: str, *, body: str = "") -> Path:
    """One entry file on disk, front matter given VERBATIM between the fences."""
    d = store / scope
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(
        "---\n"
        f"service: {slug}\n"
        f"scope: {scope}\n"
        "sensitivity: public\n"
        f"{front}"
        "---\n"
        "\n"
        "## What it is\n"
        "A fixture.\n"
        "\n"
        "## Pointers\n"
        "- `scripts/nothing` — a fixture.\n"
        "\n"
        "## Nuance / work-history\n"
        f"- 2026-08-29: a fixture bullet.\n{body}",
        encoding="utf-8",
    )
    return p


def load(path: Path, scope: str) -> SubsystemEntry:
    """One file -> the entry the LOADER would build, via the loader's own mapping."""
    return SubsystemEntry.from_mapping(
        entry_mapping(path.read_text(encoding="utf-8"), filename=path.name, scope=scope),
        source=path.name,
    )


def run_recall(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RECALL), *args],
        capture_output=True, text=True, timeout=120,
    )


# --------------------------------------------------------------------------- #
# Criterion 1 — `<system>:<id>`, id half verbatim
# --------------------------------------------------------------------------- #
class TestTheRefGrammar:
    @pytest.mark.parametrize(
        "raw,system,ident",
        [
            (CLICKUP, "clickup", "868kx9eut"),
            (GITHUB, "github", "innovation-upstream/devrc#428"),
            (CLAWGATE, "clawgate", "428"),
            (LINEAR, "linear", "ENG-441"),
            # A colon in the ID half is not a second separator — the split is on
            # the FIRST colon only, so an id that contains one survives whole.
            ("weird:a:b:c", "weird", "a:b:c"),
        ],
    )
    def test_the_split_is_on_the_first_colon_and_the_id_half_is_verbatim(
        self, raw, system, ident
    ):
        ref = parse_task_ref(raw)
        assert ref.system == system
        assert ref.ident == ident
        assert str(ref) == raw

    def test_the_ID_half_keeps_its_case_while_the_system_half_is_folded(self):
        """`ENG-441` is Linear's key, not ours. Folding it would hand Linear a
        key it does not recognise; folding the SYSTEM half is safe because this
        code is the only thing that compares it."""
        ref = parse_task_ref("LINEAR:ENG-441")
        assert ref.system == "linear"
        assert ref.ident == "ENG-441", "the id half must not be case-folded"

    @pytest.mark.parametrize(
        "bad,expected_remedy",
        [
            # criterion 7's two shapes, plus the empty cases. Each names the
            # remedy for ITS OWN failure — a whitespace ref is a lost inline list
            # and telling its author "write `<system>:<id>`" would be describing
            # a shape they already wrote.
            ("nocolon", "<system>:<id>"),      # no separator
            (":428", "<system>:<id>"),         # empty system half
            ("github:", "<system>:<id>"),      # empty id half
            ("", "<system>:<id>"),             # empty
            ("   ", "<system>:<id>"),          # whitespace only
            ("a b:1", "one ref per list item"),      # a space in the system half
            ("github:a b", "one ref per list item"),  # a space in the id half
        ],
    )
    def test_a_malformed_ref_is_REJECTED_with_a_message_naming_the_shape(
        self, bad, expected_remedy
    ):
        with pytest.raises(TaskRefError) as exc:
            parse_task_ref(bad)
        assert expected_remedy in str(exc.value), (
            "the rejection must name the fix for THIS failure, not just report it"
        )

    def test_a_non_string_is_rejected_rather_than_coerced(self):
        with pytest.raises(TaskRefError):
            parse_task_ref(428)


# --------------------------------------------------------------------------- #
# Criterion 3 — the parser enumerates NO systems
# --------------------------------------------------------------------------- #
class TestNoSystemIsEnumerated:
    def test_a_system_appearing_NOWHERE_in_the_source_round_trips_unchanged(self, tmp_path):
        """🔴 THE CONTROL FOR THIS TEST IS THE GREP, NOT THE ASSERT.

        Asserting that `linear:ENG-441` round-trips proves nothing on its own —
        it would pass just as well if `linear` were hardcoded in an allowlist
        beside `clickup`. So the test first establishes that the system name is
        absent from the two modules that implement the schema, and only then
        asserts the round-trip. Together those say "it works BECAUSE nothing
        enumerates it".
        """
        alien = "zzqhorizon"
        for module in (REPO / "scripts/lib/subsystem_resolver.py",
                       REPO / "scripts/lib/subsystem_recall.py"):
            assert alien not in module.read_text(encoding="utf-8"), (
                f"{module.name} names {alien!r} — pick a system name that is "
                f"genuinely absent, or this test is vacuous"
            )
        store = tmp_path / "store"
        p = write_entry(store, "devrc", "thing", f"tasks: [{alien}:HORIZON-7]\n")
        entry = load(p, "devrc")
        assert [str(t) for t in entry.tasks] == [f"{alien}:HORIZON-7"]

    def test_the_three_first_class_systems_are_not_special_cased_in_the_parser(self):
        """clickup / github / clawgate parse by the SAME code path as any other
        system — nothing about them is validated differently here."""
        refs = [parse_task_ref(r) for r in (CLICKUP, GITHUB, CLAWGATE, LINEAR)]
        assert len({type(r) for r in refs}) == 1
        assert all(isinstance(r, TaskRef) for r in refs)


# --------------------------------------------------------------------------- #
# Criteria 1 + 5 — the `#` round-trip, through the real reader, on disk
# --------------------------------------------------------------------------- #
class TestTheHashRoundTripsThroughDisk:
    def test_a_github_ref_survives_write_read_BYTE_IDENTICALLY(self, tmp_path):
        """🔴 THE CASE THAT CATCHES A COMMENT-STRIPPING PARSER.

        A front-matter parser that treats `#` as a comment introducer truncates
        `github:innovation-upstream/devrc#428` to `github:innovation-upstream/devrc`
        — a well-formed ref pointing at a repo instead of an issue, which is
        exactly the kind of wrong that reads as right. Compared on BYTES, and
        written through `format_task_refs` so the writer is under test too.
        """
        store = tmp_path / "store"
        line = format_task_refs([parse_task_ref(GITHUB)])
        assert line == f"tasks: [{GITHUB}]"
        p = write_entry(store, "devrc", "thing", line + "\n")
        assert "#428" in p.read_text(encoding="utf-8"), "the fixture itself lost the #"
        entry = load(p, "devrc")
        assert [str(t) for t in entry.tasks] == [GITHUB]
        assert entry.tasks[0].ident == "innovation-upstream/devrc#428"

    def test_a_hash_bearing_ref_is_not_confused_with_a_COMMENT_line(self, tmp_path):
        """A front-matter line that STARTS with `#` is a comment and is skipped —
        that behaviour predates this change and must survive it. A `#` in the
        MIDDLE of a value is data. Both in one fixture so a fix to either cannot
        silently break the other."""
        store = tmp_path / "store"
        p = write_entry(
            store, "devrc", "thing",
            f"# tasks: [{CLAWGATE}]\ntasks: [{GITHUB}]\n",
        )
        entry = load(p, "devrc")
        assert [str(t) for t in entry.tasks] == [GITHUB], (
            "the commented line must not contribute a ref, and the real one must "
            "keep its #"
        )


# --------------------------------------------------------------------------- #
# Criterion 2 — `task:` scalar sugar
# --------------------------------------------------------------------------- #
class TestScalarSugar:
    def test_task_scalar_reads_back_IDENTICALLY_to_the_one_element_list(self, tmp_path):
        store = tmp_path / "store"
        scalar = load(write_entry(store, "a", "thing", f"task: {CLICKUP}\n"), "a")
        listed = load(write_entry(store, "b", "thing", f"tasks: [{CLICKUP}]\n"), "b")
        assert scalar.tasks == listed.tasks
        assert [str(t) for t in scalar.tasks] == [CLICKUP]

    def test_setting_BOTH_is_rejected_rather_than_one_silently_winning(self, tmp_path):
        """Whichever key won, the other's refs would vanish without a word. The
        error names which key to keep."""
        store = tmp_path / "store"
        p = write_entry(store, "devrc", "thing", f"task: {CLICKUP}\ntasks: [{GITHUB}]\n")
        with pytest.raises(MalformedEntryError) as exc:
            load(p, "devrc")
        assert "`task:`" in str(exc.value) and "`tasks:`" in str(exc.value)

    def test_a_LIST_under_the_scalar_key_is_named_not_flattened(self, tmp_path):
        store = tmp_path / "store"
        p = write_entry(store, "devrc", "thing", f"task: [{CLICKUP}]\n")
        with pytest.raises(MalformedEntryError) as exc:
            load(p, "devrc")
        assert "tasks:" in str(exc.value)


# --------------------------------------------------------------------------- #
# Criterion 6 — inline AND block forms
# --------------------------------------------------------------------------- #
class TestBothListForms:
    def test_the_block_form_and_the_inline_form_yield_THE_SAME_refs(self, tmp_path):
        store = tmp_path / "store"
        inline = load(
            write_entry(store, "a", "thing", f"tasks: [{CLICKUP}, {GITHUB}, {LINEAR}]\n"),
            "a",
        )
        block = load(
            write_entry(
                store, "b", "thing",
                f"tasks:\n  - {CLICKUP}\n  - {GITHUB}\n  - {LINEAR}\n",
            ),
            "b",
        )
        assert [str(t) for t in block.tasks] == [str(t) for t in inline.tasks]
        assert [str(t) for t in block.tasks] == [CLICKUP, GITHUB, LINEAR]

    def test_the_block_form_no_longer_promotes_an_item_to_a_PHANTOM_KEY(self):
        """🔴 THE EXACT PRE-CHANGE CORRUPTION, PINNED.

        Measured on the reader before this change, with this exact fixture:

            {'service': 'thing', 'tasks': '',
             '- clickup': '868kx9eut',
             '- github': 'innovation-upstream/devrc#428'}

        The key silently empty and EVERY item promoted to a front-matter key by
        its own internal colon. The card that specified this work described the
        block form as "parses clean and is silently ignored" — that was measured
        and wrong, which is why the block form is SUPPORTED rather than rejected.

        ⚠ Note which assertion does the work. `set(fm) == {...}` is the one that
        cannot be walked: asserting only that `fm['tasks']` is right would stay
        green while phantom keys piled up beside it.
        """
        fm = parse_front_matter(
            "---\n"
            "service: thing\n"
            "tasks:\n"
            f"  - {CLICKUP}\n"
            f"  - {GITHUB}\n"
            "---\n"
        )
        assert fm["tasks"] == [CLICKUP, GITHUB]
        assert not any(k.startswith("- ") for k in fm), (
            f"a list item became a front-matter key: {sorted(fm)}"
        )
        assert set(fm) == {"service", "tasks"}

    def test_a_key_AFTER_a_block_list_is_still_read(self):
        """The item-swallowing must stop at the first non-item line, or every key
        below a block list disappears."""
        fm = parse_front_matter(
            "---\n"
            "tasks:\n"
            f"  - {CLICKUP}\n"
            "\n"
            "service: thing\n"
            "sensitivity: public\n"
            "---\n"
        )
        assert fm["tasks"] == [CLICKUP]
        assert fm["service"] == "thing"
        assert fm["sensitivity"] == "public"

    def test_a_BARE_key_with_no_items_still_reads_as_a_STRING(self):
        """🔴 THE NARROWING THAT KEEPS THIS ADDITIVE.

        A bare `sensitivity:` used to yield `""` and must keep doing so:
        `fold_sensitivity` and friends call string methods on it, and handing
        them a list would raise `AttributeError` from inside a reader, pointing
        at a file the operator would have to guess at. The block list is
        recognised by LOOKAHEAD precisely so no existing key changes type.
        """
        fm = parse_front_matter("---\nservice: thing\nsensitivity:\ncreated_by: x\n---\n")
        assert fm["sensitivity"] == ""
        assert isinstance(fm["sensitivity"], str)

    def test_the_LIVE_STORE_shape_is_unaffected_by_the_widening(self, tmp_path):
        """The measurement the widening rests on, re-taken hermetically.

        0 of 120 live entries carry a front-matter line beginning `- `, so block
        support changes nothing for them. This asserts the mechanism rather than
        re-reading the real store: an entry with no block list parses to exactly
        what it parsed to before.
        """
        fm = parse_front_matter(
            "---\n"
            "service: thing\n"
            "scope: devrc\n"
            "aliases: [a, b]\n"
            "sensitivity: public\n"
            "created_by: x\n"
            "---\n"
        )
        assert fm == {
            "service": "thing", "scope": "devrc", "aliases": ["a", "b"],
            "sensitivity": "public", "created_by": "x",
        }


# --------------------------------------------------------------------------- #
# Criterion 7 — `--validate` rejects a malformed ref and NAMES THE FILE
# --------------------------------------------------------------------------- #
class TestValidateRejectsAndNamesTheFile:
    @pytest.mark.parametrize("bad", ["nocolon", ":428", "github:"])
    def test_a_malformed_ref_makes_the_entry_MALFORMED_naming_the_file(self, tmp_path, bad):
        store = tmp_path / "store"
        p = write_entry(store, "devrc", "thing", f"tasks: [{bad}]\n")
        with pytest.raises(MalformedEntryError) as exc:
            load(p, "devrc")
        msg = str(exc.value)
        assert "thing.md" in msg, "the rejection must name the file"
        assert "<system>:<id>" in msg, "the rejection must name the fix"

    def test_the_validator_and_the_reader_share_ONE_predicate(self, tmp_path):
        """🔴 NOT A STYLE POINT. `subsystem_touch --validate` answers "would the
        loader accept this file?", and it can only answer honestly by building
        what the loader builds. This runs the REAL validator over a store holding
        one bad ref and asserts it reports it — if the check were re-spelled at
        the validator, the two would drift the day one side learned a new key.
        """
        store = tmp_path / "store"
        write_entry(store, "devrc", "good", f"tasks: [{CLICKUP}]\n")
        write_entry(store, "devrc", "bad", "tasks: [nocolon]\n")
        proc = subprocess.run(
            [sys.executable, str(TOUCH), "--validate", "--store", str(store),
             "--scope", "devrc"],
            capture_output=True, text=True, timeout=120,
        )
        combined = proc.stdout + proc.stderr
        assert "bad.md" in combined, f"validator did not name the bad file:\n{combined}"
        assert proc.returncode != 0, (
            f"a store with a malformed entry must not validate clean "
            f"(rc={proc.returncode})\n{combined}"
        )

    def test_a_DUPLICATE_ref_is_deduped_rather_than_rejected(self, tmp_path):
        """Same reasoning as `aliases:`: one task written twice is one task, and
        refusing the whole file over it would make the entry invisible for a
        harmless mistake."""
        store = tmp_path / "store"
        p = write_entry(store, "devrc", "thing", f"tasks: [{CLICKUP}, {CLICKUP}, {GITHUB}]\n")
        assert [str(t) for t in load(p, "devrc").tasks] == [CLICKUP, GITHUB]


# --------------------------------------------------------------------------- #
# Criterion 8 — surfaced without re-dumping
# --------------------------------------------------------------------------- #
class TestTheReadSurface:
    def test_the_index_row_stays_ONE_LINE_and_carries_a_count(self, tmp_path):
        store = tmp_path / "store"
        write_entry(store, "devrc", "thing", f"tasks: [{CLICKUP}, {GITHUB}, {LINEAR}]\n")
        proc = run_recall("--store", str(store), "--scope", "devrc", "--list")
        assert proc.returncode == 0, proc.stderr
        rows = [ln for ln in proc.stdout.splitlines() if ln.startswith("  thing ")]
        assert len(rows) == 1, f"expected exactly one index row, got {rows}"
        assert "3 tasks" in rows[0]
        for ref in (CLICKUP, GITHUB, LINEAR):
            assert ref not in rows[0], (
                "the index row must carry a COUNT, not the refs — the refs belong "
                "in the body"
            )

    def test_the_singular_is_used_for_one_task(self, tmp_path):
        store = tmp_path / "store"
        write_entry(store, "devrc", "thing", f"tasks: [{CLICKUP}]\n")
        proc = run_recall("--store", str(store), "--scope", "devrc", "--list")
        row = next(ln for ln in proc.stdout.splitlines() if ln.startswith("  thing "))
        assert "1 task" in row and "1 tasks" not in row

    def test_an_entry_with_NO_tasks_renders_no_badge_at_all(self, tmp_path):
        """The conditionality that makes this additive: 120 of 120 live rows must
        be byte-identical to what they rendered before."""
        store = tmp_path / "store"
        write_entry(store, "devrc", "thing", "")
        proc = run_recall("--store", str(store), "--scope", "devrc", "--list")
        row = next(ln for ln in proc.stdout.splitlines() if ln.startswith("  thing "))
        assert "task" not in row and "🔗" not in row

    def test_the_BODY_prints_the_refs_themselves(self, tmp_path):
        store = tmp_path / "store"
        write_entry(store, "devrc", "thing", f"tasks: [{CLICKUP}, {GITHUB}]\n")
        proc = run_recall("--store", str(store), "--scope", "devrc", "--ref", "thing")
        assert proc.returncode == 0, proc.stderr
        assert CLICKUP in proc.stdout
        assert GITHUB in proc.stdout, "the # must survive all the way to the screen"

    def test_the_JSON_entries_carry_tasks_and_an_empty_list_when_there_are_none(
        self, tmp_path
    ):
        store = tmp_path / "store"
        write_entry(store, "devrc", "withtasks", f"tasks: [{CLICKUP}]\n")
        write_entry(store, "devrc", "without", "")
        proc = run_recall("--store", str(store), "--scope", "devrc", "--limit", "2", "--json")
        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        by_ref = {e["ref"]: e for e in payload["entries"]}
        assert by_ref["withtasks"]["tasks"] == [CLICKUP]
        assert by_ref["without"]["tasks"] == [], (
            "an entry with no tasks must carry [], never a missing key — absence "
            "would read as 'this reader predates tasks'"
        )


# --------------------------------------------------------------------------- #
# Criterion 9 — the lossy tag is DERIVED, never parsed back
# --------------------------------------------------------------------------- #
class TestTheLossyTagIsDerivationOnly:
    def test_two_DISTINCT_refs_collapse_to_ONE_tag(self):
        """🔴 THE PROOF THAT NO INVERSE CAN EXIST, not merely that none is written.

        `github-mirror` flattens `owner/repo#N` into a tag charset with no `#`.
        The map is not injective, so any function claiming to parse a tag back
        into a ref is choosing between two real originals with even odds — which
        is why the derivation is one-way BY CONSTRUCTION and not by discipline.
        A reviewer can delete a missing-function assertion; they cannot delete
        this collision.
        """
        a = parse_task_ref("github:zacxdev/homelab-infra#429")
        b = parse_task_ref("github:zacxdev-homelab/infra#429")
        assert str(a) != str(b)
        assert lossy_tag_for(a) == lossy_tag_for(b) == "github:zacxdev-homelab-infra-429"

    def test_the_tag_is_legal_in_the_clawgate_grammar(self):
        """Charset `[a-z0-9._/-]`, at most one colon, 64 runes. Asserted rather
        than described, because the whole reason the tag is lossy is that the
        lossless ref does NOT satisfy this."""
        for raw in (CLICKUP, GITHUB, CLAWGATE, LINEAR):
            tag = lossy_tag_for(parse_task_ref(raw))
            assert tag.count(":") == 1, tag
            assert len(tag) <= 64, tag
            body = tag.replace(":", "", 1)
            assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789._/-" for c in body), tag

    def test_no_inverse_is_exported(self):
        import subsystem_resolver as sr

        exported = set(sr.__all__)
        assert "lossy_tag_for" in exported
        for forbidden in ("parse_lossy_tag", "task_ref_from_tag", "ref_for_tag"):
            assert forbidden not in exported, (
                f"{forbidden} would invent one of two real refs — see the "
                f"collision test above"
            )
