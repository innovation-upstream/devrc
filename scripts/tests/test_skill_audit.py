"""Unit tests for scripts/skill-audit.py — the /prune-skill auditor.

OFFLINE and hermetic: every fixture is written into a tmp_path; nothing under
~/.claude or any real repo is read.

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

def test_a_date_only_heading_is_dated_history(tmp_path):
    """The ISO-date alternative must carry its own weight.

    Added because a mutation sweep KILLED nothing when that alternative was
    neutered — the word forms in the shared fixture were covering for it. A
    real datapacket heading, `## Marketplace (F-E — E1–E5, all merged
    2026-06-15, DARK behind the mod gate)`, contains no Session/Changelog word
    at all; with only the word forms it would be invisible.
    """
    body = "## Marketplace (E1–E5, all merged 2026-06-15, DARK behind the gate)\n\nbody\n"
    a = sa.audit_one(_write_skill(tmp_path, "dateonly", body))
    assert [t for t, *_ in a["dated"]] == [
        "Marketplace (E1–E5, all merged 2026-06-15, DARK behind the gate)"]


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
    """Positive control for the fence counter. MEASURED on the real
    datapacket-talos app-blocks/SKILL.md (unclosed fence at line 1600), which
    made a shell comment own 250 KB of phantom 'dated history'."""
    body = "## Real\n\n```bash\necho hi\n\n## Later\n\nmore\n"
    p = _write_skill(tmp_path, "unclosed", body)
    a = sa.audit_one(p)
    assert a["fence_ok"] is False
    out = _run(tmp_path)
    assert "unclosed code fence" in out.lower()
    assert "no prune needed" not in out


def test_a_foreign_repos_reference_path_is_not_a_broken_sidecar(tmp_path):
    """`apps/reference/manifest.md` is a page on developer.civitai.com, not this
    skill's sidecar. Two real datapacket skills write paths like that; flagging
    them as broken is a false alarm about another repo."""
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
