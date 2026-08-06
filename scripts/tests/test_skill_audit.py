"""Unit tests for scripts/skill-audit.py — the /prune-skill auditor.

OFFLINE and hermetic: every fixture is either written into a tmp_path or tracked
in this repo under tests/fixtures/skill_audit/. Nothing under ~/.claude, $HOME
or any out-of-repo clone is read, and no test in this file skips — so it means
exactly the same thing on a dev host and in the nix build sandbox.

🔴 HARNESS DISCIPLINE (claude/RULES.md, "A harness that COUNTS needs a POSITIVE
control too"). This auditor's reassuring answers are ZEROS — "0 dated-history
blocks", "0 fat lines", "0 missing references". A zero is indistinguishable
from a detector wired to nothing, so every counter here is exercised in BOTH
directions against TWO fixtures:

  GOOD_SKILL  the negative control — small, no dated history, every reference
              resolves, no orphans. Every counter must read 0 and the verdict
              must be "no prune needed".
  BAD_SKILL   the positive control — over budget, 3 dated blocks, 2 fat lines,
              1 missing reference, 1 orphan on disk. Every counter must move
              OFF zero, at the exact expected value.

The expected values are literals derived from the fixture text, never from the
implementation. Where a count can be computed two ways (section bytes vs file
bytes), the test cross-checks them rather than trusting one.
"""
import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
AUDIT_PY = SCRIPTS / "skill-audit.py"

# Whole-FILE fixtures, tracked in this repo. The three file-scale pins here used
# to read SKILL.md files out of a private clone at an absolute path and skip when
# it was absent — see the tests that use these. They are deliberately NOT named
# SKILL.md, so running skill-audit.py on this repo root cannot mistake a fixture
# for a real skill.
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "skill_audit"


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sa = _load("skill-audit.py", "skill_audit")


# --- fixtures ------------------------------------------------------------------

GOOD_SKILL = """\
---
name: good
description: A lean core that routes to reference files.
---

## Quick start

```bash
TOOL=~/workspace/devrc/scripts/tool
$TOOL status
```

## Ops

| command | does |
|---|---|
| `status` | print health |
| `run` | do the thing |

## Reference files — load ONE only when its trigger fires

Read them at `~/workspace/devrc/scripts/good/reference/<file>`.

| file | load it when… |
|---|---|
| `reference/errors.md` | an op returned an error you don't recognise |
| `reference/setup.md` | first-time setup |
"""

# Three dated blocks, deliberately of three different SHAPES, and deliberately
# SIBLINGS under a non-dated parent — the datapacket app-blocks arrangement,
# where 40 `### Session …` blocks hang under one `## Roadmap`:
#   "### Session <ISO date>"       — the ISO-date shape
#   "### Changelog"                — an undated word form
#   "### What we shipped in Q3"    — the other word form
# Plus: two lines over 500 B, one reference that does NOT exist, and one
# reference file on disk that the core never names (the orphan). Sized past the
# 12,288 B target so the over-budget path renders.
_FAT = "x" * 600
_NARRATIVE = ("We shipped the thing, then we shipped another thing. None of this "
              "tells anyone how to do the work today. " * 40)
BAD_SKILL = f"""\
---
name: bad
description: A core that ate its own work history.
---

## Quick start

```bash
TOOL=/opt/tool
$TOOL status
```

## Ops

Something real that must be KEPT_HOT. {_FAT}

## Reference files

| file | load it when… |
|---|---|
| `reference/errors.md` | an op returned an error |
| `reference/gone.md` | THIS FILE DOES NOT EXIST |

## Roadmap

Lead paragraph that is not itself history. {_FAT}

### Session 2026-08-01 — shipped the thing

{_NARRATIVE}

### Changelog

{_NARRATIVE}

### What we shipped in Q3

{_NARRATIVE}
"""


def _write_skill(tmp_path, name, body, refs=(), extra_ref_files=()):
    d = tmp_path / name
    (d / "reference").mkdir(parents=True)
    (d / "SKILL.md").write_text(body)
    for r in refs:
        (d / "reference" / r).write_text(f"# {r}\n")
    for r in extra_ref_files:
        (d / "reference" / r).write_text(f"# {r}\n")
    return d / "SKILL.md"


def good(tmp_path):
    return _write_skill(tmp_path, "good", GOOD_SKILL, refs=("errors.md", "setup.md"))


def bad(tmp_path):
    # errors.md exists (so `gone.md` is a REAL miss, not a missing dir);
    # orphan.md exists on disk but the core never names it.
    return _write_skill(tmp_path, "bad", BAD_SKILL,
                        refs=("errors.md",), extra_ref_files=("orphan.md",))


# --- budgets are the contract --------------------------------------------------

def test_budget_constants_match_the_proven_browser_ceiling():
    """TARGET is not a round number picked by taste — it is the ceiling
    test_skill_size.py already enforces on scripts/browser-bridge/SKILL.md. If
    that file's MAX_BYTES moves, this must move with it or the two documents
    disagree about the same budget."""
    ceiling = SCRIPTS / "browser-bridge" / "tests" / "test_skill_size.py"
    src = ceiling.read_text()
    assert "MAX_BYTES = 12_288" in src, (
        "browser-bridge's MAX_BYTES changed; skill-audit.TARGET was derived from "
        "it and must be re-derived, not left to drift."
    )
    assert sa.TARGET == 12_288
    assert sa.HARD == 40_960
    assert sa.TARGET < sa.HARD


def test_the_real_browser_skill_is_under_the_target():
    """The existence proof the whole command rests on: a genuinely complex tool
    (21 ops, 11 reference topics) fits the budget. If this ever fails, either
    the browser skill regressed or the claim in prune-skill.md is false."""
    browser = SCRIPTS / "browser-bridge" / "SKILL.md"
    a = sa.audit_one(browser)
    assert a["status"] == "OK", f"browser SKILL.md is {a['size']:,} B"
    assert a["refs"], "browser must route to reference files — it is the pattern's exemplar"
    assert not a["missing_refs"] and not a["orphan_refs"]


# --- NEGATIVE CONTROL: the good fixture must come back clean -------------------

def test_good_fixture_is_clean_on_every_counter(tmp_path):
    a = sa.audit_one(good(tmp_path))
    assert a["status"] == "OK"
    assert a["dated"] == [] and a["dated_bytes"] == 0
    assert a["fat"] == [] and a["fat_bytes"] == 0
    assert a["missing_refs"] == []
    assert a["orphan_refs"] == []
    assert a["fence_ok"] is True
    assert len(a["refs"]) == 2


def test_good_fixture_verdict_is_no_prune_needed(tmp_path):
    good(tmp_path)
    out = _run(tmp_path)
    assert "no prune needed" in out
    assert "prune needed —" not in out


# --- POSITIVE CONTROL: every counter must move OFF zero ------------------------

def test_positive_control_dated_history_counter_moves(tmp_path):
    """A '0 dated-history blocks' result is only evidence once this passes."""
    a = sa.audit_one(bad(tmp_path))
    titles = [t for t, *_ in a["dated"]]
    assert len(a["dated"]) == 3, titles
    assert a["dated_bytes"] > 0
    # All three SHAPES fire, not just the ISO-date one.
    assert any("Changelog" in t for t in titles)
    assert any("Session 2026-08-01" in t for t in titles)
    assert any("What we shipped" in t for t in titles)
    # And the eviction is the biggest single win: >50% of the file.
    assert a["dated_bytes"] > a["size"] // 2


def test_positive_control_fat_line_counter_moves(tmp_path):
    a = sa.audit_one(bad(tmp_path))
    # 2 padded lines + the 3 single-line narrative paragraphs. The count is a
    # literal read off the fixture, not off the implementation.
    assert len(a["fat"]) == 5, [b for b, _ in a["fat"]]
    assert all(b > sa.FAT_LINE for b, _ in a["fat"])
    assert a["fat_bytes"] == sum(b for b, _ in a["fat"])
    assert a["fat_bytes"] > 12_000


def test_positive_control_missing_reference_counter_moves(tmp_path):
    a = sa.audit_one(bad(tmp_path))
    assert a["missing_refs"] == ["reference/gone.md"]


def test_positive_control_orphan_reference_counter_moves(tmp_path):
    """The other direction: a file on disk nothing routes to is unreachable,
    therefore dead. Mirrors test_eviction_playbook_lists_every_reference_topic."""
    a = sa.audit_one(bad(tmp_path))
    assert a["orphan_refs"] == ["reference/orphan.md"]


def test_positive_control_over_budget_status_moves(tmp_path):
    p = bad(tmp_path)
    a = sa.audit_one(p)
    assert a["size"] > sa.TARGET
    assert a["status"] == "OVER TARGET"
    # And the hard-cap band is reachable too — pad past 40,960 B.
    p.write_text(BAD_SKILL + "\npadding\n" * 20000)
    assert sa.audit_one(p)["status"] == "OVER HARD CAP"


def test_bad_fixture_verdict_says_prune_needed(tmp_path):
    bad(tmp_path)
    out = _run(tmp_path)
    assert "⚠ prune needed" in out
    assert "no prune needed" not in out
    assert "EVICT_HISTORY" in out


# --- the detectors, exercised on their edges ------------------------------------

def test_a_date_only_heading_is_a_LESSON_not_evictable_history(tmp_path):
    """The ISO-date alternative must carry its own weight — but into the RIGHT bucket.

    This test previously asserted a date-only heading was EVICT_HISTORY. That
    assertion was WRONG and is why it now reads differently: it pinned a
    classification that, measured across the 67-skill datapacket-talos corpus on
    2026-08-04, was correct on exactly ONE skill. app-blocks carries 45
    work-status headings; every other skill carries ZERO while carrying 8-17
    dated-but-topical ones. The merged metric therefore reported 59% "evictable
    history" for manage-alerts, whose two largest such blocks were its most
    valuable operational content ("Common silent-failure modes", "The INERT-ALERT
    defect class"). Evicting on that signal would have gutted the skill.

    The date branch is still pinned — it just resolves to `lessons`, not `dated`.
    """
    body = "## Marketplace (E1–E5, all merged 2026-06-15, DARK behind the gate)\n\nbody\n"
    a = sa.audit_one(_write_skill(tmp_path, "dateonly", body))
    title = "Marketplace (E1–E5, all merged 2026-06-15, DARK behind the gate)"
    assert [t for t, *_ in a["lessons"]] == [title]
    assert a["dated"] == [], "a date CITATION is not work-status narrative"
    assert a["dated_bytes"] == 0


def test_work_status_and_lessons_are_disjoint(tmp_path):
    """No block may appear in both buckets — the whole point of the split is that
    a reader can act on one and not the other."""
    body = ("## Session 2026-08-02 — what we did\n\nnarrative\n\n"
            "## Failure modes (from the 2026-05-22 audit)\n\nguidance\n")
    a = sa.audit_one(_write_skill(tmp_path, "disjoint", body))
    ws = {t for t, *_ in a["dated"]}
    ls = {t for t, *_ in a["lessons"]}
    assert ws == {"Session 2026-08-02 — what we did"}
    assert ls == {"Failure modes (from the 2026-05-22 audit)"}
    assert not (ws & ls)


def test_a_dated_work_status_heading_goes_to_work_status_not_lessons(tmp_path):
    """Precedence: a heading carrying BOTH a work-status word and a date is
    work-status. Without this, `### Session 2026-07-29` would land in the
    non-evictable bucket and app-blocks' 45 real Session blocks would go
    unreported."""
    body = "### Session 2026-07-29→30 — dogfood + audit\n\nnarrative\n"
    a = sa.audit_one(_write_skill(tmp_path, "both", body))
    assert len(a["dated"]) == 1 and a["lessons"] == []


def test_corpus_shaped_file_of_dated_lessons_reports_zero_work_status():
    """Whole-file regression pin, so the conflation cannot return.

    This used to read a live skill corpus out of a separate PRIVATE clone at an
    absolute out-of-repo path, and skip when that clone was absent — so it meant
    one thing on a dev host and vanished in the tier that gates merges. Re-pointed
    2026-08-06 at a synthetic fixture TRACKED IN THIS REPO, reproducing the
    corpus-majority shape measured 2026-08-04: a skill whose dated headings are
    all durable guidance. Every value below is a literal read off the fixture,
    not off the implementation.

    The `dated == []` half is the one that matters: with the buckets merged, all
    nine lessons land in the evictable pile, which is how a 59% "evictable
    history" figure was reported for a skill whose largest dated blocks were its
    most valuable operational content.
    """
    a = sa.audit_one(FIXTURES / "dated_lessons_corpus.md")
    assert a["dated"] == [], (
        f"reported {len(a['dated'])} work-status block(s); every dated heading in "
        "this fixture is durable guidance, so the buckets have re-merged")
    assert a["dated_bytes"] == 0
    assert [t for t, *_ in a["lessons"]] == [
        "Common silent-failure modes (from the 2026-05-22 audit)",
        "The inert-check defect class (2026-06-01)",
        "Why the retry budget is per-target, not global (2026-06-14)",
        "Backoff must be capped (decided 2026-06-20)",
        "Draining a queue safely (2026-06-28)",
        "Reading the saturation panel (2026-07-03)",
        "Two timeouts, two meanings (2026-07-11)",
        "Config precedence (established 2026-07-19)",
        "The idempotency key must cover the payload (2026-07-25)",
    ]


def test_corpus_shaped_file_of_session_narrative_reports_work_status():
    """The other half of the same pin, and the POSITIVE control for it.

    `dated == []` above is a zero, and a zero is indistinguishable from a
    detector wired to nothing — so the same detector must be shown to move on a
    file that genuinely carries session narrative. Same fixture convention:
    five `### Session <date>` siblings under one undated `## Roadmap`, the
    arrangement measured on the one corpus skill that really had accreted it.

    The buckets must also be DISJOINT at file scale: the one dated-but-topical
    heading here belongs to `lessons` and to nothing else.
    """
    a = sa.audit_one(FIXTURES / "work_status_corpus.md")
    ws = [t for t, *_ in a["dated"]]
    assert ws == [
        "Session 2026-07-01 — first cut",
        "Session 2026-07-08 — wiring the collector",
        "Session 2026-07-15→16 — dogfood + audit",
        "Session 2026-07-22 — fixing the retry path",
        "Session 2026-07-29 — cleanup",
    ]
    assert a["dated_bytes"] > 0
    ls = [t for t, *_ in a["lessons"]]
    assert ls == ["Failure modes (from the 2026-05-22 audit)"]
    assert not (set(ws) & set(ls))


@pytest.mark.parametrize("heading", [
    "Session notes",
    "Changelog",
    "Work log",
    "What we shipped",
    "What shipped",
    "Release notes",
    "History",
])
def test_each_undated_word_form_is_dated_history_on_its_own(tmp_path, heading):
    """The complement of the date test, one alternative at a time.

    Also added off the mutation sweep: a single test using "Changelog" left
    every OTHER word alternative unpinned — neutering `\\bsessions?\\b` killed
    nothing, because the only "Session" heading in the fixtures also carried an
    ISO date and was covered by the date branch.
    """
    body = f"## {heading}\n\nbody\n"
    a = sa.audit_one(_write_skill(tmp_path, "word_" + heading.replace(" ", "_"), body))
    assert [t for t, *_ in a["dated"]] == [heading]


def test_a_version_number_heading_is_NOT_dated_history(tmp_path):
    """Precision, not just recall: `1.10.4` and `v0.6.0-civitai.3` are not
    dates, and a procedure heading must not be classified as evictable."""
    body = ("## Talos 1.10.4 upgrade procedure\n\nbody\n\n"
            "## Rebuild v0.6.0-civitai.3\n\nbody\n")
    a = sa.audit_one(_write_skill(tmp_path, "versions", body))
    assert a["dated"] == []


def test_dated_block_nested_inside_another_is_counted_once(tmp_path):
    """Two dated headings where one nests inside the other must not double-count
    — otherwise the projected saving exceeds the file size."""
    body = ("## Changelog\n\nlead\n\n### Session 2026-08-01 — a\n\nbody\n")
    p = _write_skill(tmp_path, "nest", body)
    a = sa.audit_one(p)
    assert len(a["dated"]) == 1, [t for t, *_ in a["dated"]]
    assert a["dated_bytes"] <= a["size"]


def test_headings_inside_a_code_fence_are_not_sections(tmp_path):
    """A `# comment` in a ```bash block is not a heading. Counting it as one
    silently re-partitions the file — this is exactly the mechanism behind the
    unclosed-fence defect the fence check exists for."""
    body = ("## Real\n\n```bash\n# 3. SQL state: see handoff-2026-05-24.md\n"
            "echo hi\n```\n\ntail\n")
    p = _write_skill(tmp_path, "fenced", body)
    a = sa.audit_one(p)
    assert [t for t, *_ in a["h2"]] == ["Real"]
    assert a["dated"] == [], "a shell comment must never register as dated history"
    assert a["fence_ok"] is True


def test_unclosed_fence_is_reported(tmp_path):
    """Positive control for the fence counter: a genuinely unclosed fence.

    The docstring here used to cite datapacket-talos app-blocks/SKILL.md as a
    real instance ('unclosed fence at line 1600, a shell comment owning 250 KB
    of phantom dated history'). RETRACTED 2026-08-03 — that file is well-formed
    under CommonMark; the finding was an artifact of the marker-parity check
    this suite now pins against (see the false-positive tests below).
    """
    body = "## Real\n\n```bash\necho hi\n\n## Later\n\nmore\n"
    p = _write_skill(tmp_path, "unclosed", body)
    a = sa.audit_one(p)
    assert a["fence_ok"] is False
    out = _run(tmp_path)
    assert "unclosed code fence" in out.lower()
    assert "no prune needed" not in out


# --- fence FALSE POSITIVES: odd marker parity that is nonetheless well-formed ---
# Each of these has an ODD number of ``` markers and is valid CommonMark. The
# marker-parity heuristic these replaced called all of them "unclosed", which is
# how two independent readers concluded a 726 KB production skill was broken.

def test_info_string_marker_cannot_close_a_fence(tmp_path):
    """The exact shape from datapacket-talos manage-support-stack/SKILL.md: an
    outer fence displaying a literal ```action example. 3 markers, odd parity,
    perfectly well-formed — a closing fence may not carry an info string."""
    body = ('## Contract\n\n```\n```action\n{"type": "apply_filter"}\n```\n\ntail\n')
    a = sa.audit_one(_write_skill(tmp_path, "infoclose", body))
    assert a["fence_ok"] is True, "an info-string marker must not close a fence"
    assert [t for t, *_ in a["h2"]] == ["Contract"]


def test_longer_outer_fence_wraps_a_shorter_literal_one(tmp_path):
    """A 4-backtick fence is closed only by >=4 backticks, so the ``` pair
    inside it is literal content, not structure."""
    body = "## Doc\n\n````\n```bash\necho hi\n```\n````\n\n## After\n\ntail\n"
    a = sa.audit_one(_write_skill(tmp_path, "nested", body))
    assert a["fence_ok"] is True
    assert [t for t, *_ in a["h2"]] == ["Doc", "After"], \
        "the heading after a correctly-nested fence must stay visible"


def test_a_shorter_marker_cannot_close_a_longer_fence(tmp_path):
    """Complement of the above: ``` inside a ```` block leaves it OPEN, so a
    genuinely unclosed longer fence is still caught."""
    body = "## Doc\n\n````\n```\nstill inside\n"
    a = sa.audit_one(_write_skill(tmp_path, "shortmarker", body))
    assert a["fence_ok"] is False


def test_tilde_fence_is_tracked_and_not_closed_by_backticks(tmp_path):
    """Fences are per-character: ``` cannot close a ~~~ block."""
    body = "## Doc\n\n~~~\n```\n~~~\n\n## After\n\ntail\n"
    a = sa.audit_one(_write_skill(tmp_path, "tilde", body))
    assert a["fence_ok"] is True
    assert [t for t, *_ in a["h2"]] == ["Doc", "After"]


@pytest.mark.parametrize("fixture,h2", [
    # The shape the parity check falsely accused in a real support-stack doc:
    # an outer bare fence displaying a ```action marker.
    ("odd_parity_info_string.md", ["Contract", "Runbook", "After the fences"]),
    # The other one: a heredoc inside ```bash that writes out another ```bash
    # block, alongside the `# 3. …` shell comment that a broken fence walk reads
    # as an H1 and re-partitions the file on.
    ("odd_parity_nested_lang.md", ["Runbook", "Still visible", "Also still visible"]),
])
def test_odd_marker_parity_wellformed_file_is_not_reported_broken(fixture, h2):
    """Whole-FILE regression pin on the false-positive class.

    This used to read two SKILL.md files out of a PRIVATE clone at an absolute
    path and skip when it was absent — so the highest-value assertion in this
    file was structurally unobservable in the tier that gates merges.
    Re-pointed 2026-08-06 at synthetic fixtures TRACKED IN THIS REPO that
    reproduce both accused shapes at file scale.

    The odd-parity assertion is the fixture's own positive control: it is what
    the parity heuristic tripped on, so if a future edit quietly evens the
    marker count, this test would still pass while no longer exercising the
    trap. Pin the property, not just the verdict.
    """
    p = FIXTURES / fixture
    lines = p.read_text().splitlines(keepends=True)
    markers = sum(1 for ln in lines if sa.FENCE.match(ln.rstrip("\n")))
    assert markers % 2 == 1, (
        f"{fixture} has {markers} fence markers — EVEN parity no longer exercises "
        "the false positive this fixture exists for; restore an odd marker")
    a = sa.audit_one(p)
    assert a["fence_ok"] is True, \
        f"{fixture}: well-formed under CommonMark; a FAIL here means the fence walk regressed"
    assert [t for t, *_ in a["h2"]] == h2, \
        f"{fixture}: the fence walk re-partitioned the file"


# --- skill naming ---------------------------------------------------------------

def test_loose_file_is_named_after_the_file_not_its_directory(tmp_path):
    """A scratch copy must not inherit its containing directory's name — an
    app-blocks copy under scratchpad/ was reported as the skill 'scratchpad'."""
    d = tmp_path / "scratchpad"
    d.mkdir()
    p = d / "app-blocks-candidate.md"
    p.write_text("## Real\n\nbody\n")
    assert sa.audit_one(p)["name"] == "app-blocks-candidate"


def test_a_real_skill_is_still_named_after_its_directory(tmp_path):
    """Complement: the .claude/skills/<name>/SKILL.md convention is unchanged."""
    assert sa.audit_one(_write_skill(tmp_path, "manage-redis", "## X\n\nb\n"))["name"] \
        == "manage-redis"


def test_a_foreign_repos_reference_path_is_not_a_broken_sidecar(tmp_path):
    """`apps/reference/manifest.md` is a page on a client's public developer-docs
    site, not this skill's sidecar. Two real datapacket skills write paths like
    that; flagging them as broken is a false alarm about another repo."""
    body = ("## Docs\n\nSee `apps/reference/manifest.md` on the docs site.\n"
            "And `reference/errors.md` here.\n")
    p = _write_skill(tmp_path, "foreign", body, refs=("errors.md",))
    a = sa.audit_one(p)
    assert a["refs"] == ["reference/errors.md"]
    assert a["missing_refs"] == []


def test_a_placeholder_reference_path_is_not_checked(tmp_path):
    """The <var> convention (datapacket's doc-rot gate) must not read as a
    broken path — `reference/<file>.md` names no file."""
    body = "## Docs\n\nRead them at `reference/<file>.md`.\n"
    p = _write_skill(tmp_path, "placeholder", body)
    a = sa.audit_one(p)
    assert a["refs"] == []
    assert a["missing_refs"] == []


def test_relative_paths_warn_only_when_no_absolute_base_is_stated(tmp_path):
    """The deployment caveat: a devrc skill symlinked into ~/.claude/skills/
    gets only SKILL.md, so a bare `reference/x.md` does not resolve for the
    reader — UNLESS the core states the absolute base, as browser's does."""
    with_base = ("## Docs\n\nRead them at `~/workspace/devrc/scripts/x/reference/<file>`.\n"
                 "| `reference/errors.md` | when |\n")
    without = "## Docs\n\n| `reference/errors.md` | when |\n"
    a = sa.audit_one(_write_skill(tmp_path, "withbase", with_base, refs=("errors.md",)))
    b = sa.audit_one(_write_skill(tmp_path, "nobase", without, refs=("errors.md",)))
    assert a["relative_refs"] == 1 and a["abs_ref_base"] is True
    assert b["relative_refs"] == 1 and b["abs_ref_base"] is False
    out = _run(tmp_path)
    assert "nobase: 1 RELATIVE reference path" in out
    assert "withbase: 1 RELATIVE reference path" not in out


# --- accounting cross-checks (compute, don't parse) -----------------------------

def test_section_bytes_sum_to_the_file_size(tmp_path):
    """An independent way to compute the same number. If the H2 weights plus the
    preamble do not reconstruct the file exactly, the partition dropped or
    double-counted lines and every 'where are the bytes' figure is wrong."""
    for fixture in (good, bad):
        a = sa.audit_one(fixture(tmp_path / fixture.__name__))
        assert a["preamble_bytes"] + sum(b for *_, b in a["h2"]) == a["size"]


def test_dated_bytes_never_exceed_the_file(tmp_path):
    a = sa.audit_one(bad(tmp_path))
    assert 0 < a["dated_bytes"] <= a["size"]


# --- target resolution ----------------------------------------------------------

def test_resolve_targets_accepts_file_skill_dir_and_skills_root(tmp_path):
    p = good(tmp_path)
    bad(tmp_path)
    assert sa.resolve_targets([str(p)]) == [p.resolve()]
    assert sa.resolve_targets([str(p.parent)]) == [p.resolve()]
    roots = sa.resolve_targets([str(tmp_path)])
    assert sorted(x.parent.name for x in roots) == ["bad", "good"]


def test_no_skill_md_anywhere_exits_nonzero_rather_than_reporting_clean(tmp_path):
    """A guard the audit itself needs: an empty/wrong path must NOT print a
    reassuring 'all 0 skills within budget'."""
    (tmp_path / "empty").mkdir()
    r = subprocess.run([sys.executable, str(AUDIT_PY), str(tmp_path / "empty")],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "no SKILL.md found" in r.stderr
    assert "no prune needed" not in r.stdout


# --- helper ---------------------------------------------------------------------

def _run(root, *extra):
    r = subprocess.run([sys.executable, str(AUDIT_PY), str(root), *extra],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout
