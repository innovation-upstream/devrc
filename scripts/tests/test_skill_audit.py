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
import re
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


# --- a DIRECTORY routed by a VARIABLE segment ----------------------------------
# The third routing shape. A set that is expected to keep growing (browser's
# per-site docs) cannot afford a row per member, because the body is loaded on
# every task. A row naming the directory with a `<placeholder>` filename routes
# to every member, because something at RUN TIME resolves the placeholder — so
# those members are reachable, not dead. The four tests below pin that the
# affordance is REAL, NARROW, and cannot be used to switch the counter off.

_VAR_ROUTED = """\
---
name: varrouted
description: A core that routes to a directory, not to each of its members.
---

## Reference

| file | load it when… |
|---|---|
| `reference/errors.md` | any error you don't recognise |
| `reference/sites/<host>.md` | you are driving a site that has one |
"""


def _var_routed(tmp_path, name="varrouted", body=_VAR_ROUTED, members=("a.test.md",)):
    d = tmp_path / name
    (d / "reference" / "sites").mkdir(parents=True)
    (d / "SKILL.md").write_text(body)
    (d / "reference" / "errors.md").write_text("# errors\n")
    for m in members:
        (d / "reference" / "sites" / m).write_text(f"# {m}\n")
    return d / "SKILL.md"


def test_a_member_of_a_placeholder_routed_directory_is_not_an_orphan(tmp_path):
    """`reference/sites/<host>.md` in the body routes to every file in that
    directory without naming one. None of them is dead."""
    a = sa.audit_one(_var_routed(tmp_path, members=("a.test.md", "b.test.md")))
    assert a["orphan_refs"] == [], (
        "a directory routed with a variable segment must not report its members "
        f"as orphans: {a['orphan_refs']}")
    assert a["missing_refs"] == []


def test_the_affordance_does_NOT_extend_to_a_sibling_directory(tmp_path):
    """NARROW: only the directory the placeholder row actually names. A file in
    a DIFFERENT subdirectory is still unreachable, and still an orphan."""
    p = _var_routed(tmp_path, name="narrow")
    (p.parent / "reference" / "other").mkdir()
    (p.parent / "reference" / "other" / "lost.md").write_text("# lost\n")
    assert sa.audit_one(p)["orphan_refs"] == ["reference/other/lost.md"]


def test_a_toplevel_placeholder_cannot_switch_the_whole_counter_off(tmp_path):
    """🔴 THE ABUSE CASE. A row spelled `reference/<topic>.md` would, on a naive
    prefix rule, excuse EVERY file in reference/ — turning the orphan check off
    repo-wide with one line. It must not: the affordance applies to a real
    SUBdirectory only, never to reference/ itself."""
    body = _VAR_ROUTED.replace("`reference/sites/<host>.md`", "`reference/<topic>.md`")
    p = _var_routed(tmp_path, name="abuse", body=body)
    (p.parent / "reference" / "orphan.md").write_text("# orphan\n")
    assert "reference/orphan.md" in sa.audit_one(p)["orphan_refs"]


def test_without_the_placeholder_row_the_members_ARE_orphans(tmp_path):
    """MUTATION, in-suite: delete the routing row and the same files must go
    back to being reported. Without this, the test above cannot distinguish
    'the affordance works' from 'the orphan check stopped looking'."""
    body = "\n".join(l for l in _VAR_ROUTED.splitlines()
                     if "reference/sites/" not in l) + "\n"
    p = _var_routed(tmp_path, name="unrouted", body=body,
                    members=("a.test.md", "b.test.md"))
    assert sa.audit_one(p)["orphan_refs"] == [
        "reference/sites/a.test.md", "reference/sites/b.test.md"]


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


# --- numbered-corpus integrity --------------------------------------------------
# The numbers in a split corpus are an API: they are cited from the core, from
# sibling reference files and from OTHER skills, so a demote that renumbers
# per-file silently breaks every citation while leaving all PATHS valid — no path
# gate can see it. Measured on datapacket-talos app-blocks 2026-08-17: intact at
# 200 items / 0 dangling; renumbering its 9 shards to 1..n each produced 53
# dangling citations, which is the mutation the reporter below must catch.

def _corpus_skill(tmp_path, name, core, shards):
    d = tmp_path / name
    (d / "reference").mkdir(parents=True)
    (d / "SKILL.md").write_text(core)
    for fn, nums in shards.items():
        body = "# shard\n\n" + "".join(f"{n}. **item {n}** body\n\n" for n in nums)
        (d / "reference" / fn).write_text(body)
    return d / "SKILL.md"


# Two sparse shards of one 1..200-style sequence, as a real split corpus looks.
# 12 apiece, so a total renumber still leaves >= CORPUS_MIN defined numbers —
# otherwise the size gate, not the logic, is what the mutation test measures.
SHARDS = {"a.md": [3, 17, 45, 92, 140, 155, 161, 170, 175, 182, 190, 196],
          "b.md": [8, 23, 60, 111, 150, 158, 166, 172, 178, 185, 193, 199]}
CORE_CITES = "# core\n\nSee gotcha #45 and gotcha #111 for detail.\n"


def test_a_split_corpus_with_frozen_numbers_is_clean(tmp_path):
    a = sa.audit_one(_corpus_skill(tmp_path, "ok", CORE_CITES, SHARDS))
    assert a["corpus_n"] == 24
    assert a["corpus_dangling"] == []
    assert a["corpus_dupes"] == []


def test_positive_control_renumbering_shards_breaks_citations(tmp_path):
    """THE mutation this check exists for — and the one a ceiling-bounded
    citation regex could not see, because renumbering drops the ceiling too."""
    renumbered = {"a.md": list(range(1, 13)), "b.md": list(range(1, 13))}
    a = sa.audit_one(_corpus_skill(tmp_path, "renum", CORE_CITES, renumbered))
    assert a["corpus_dangling"] == [45, 111], (
        "renumbering every shard to 1..n must strand both citations; got "
        f"{a['corpus_dangling']}")


def test_positive_control_a_duplicate_number_across_shards_is_reported(tmp_path):
    """A PARTIAL renumber — one shard rewritten, the other left alone."""
    clashing = {"a.md": SHARDS["a.md"], "b.md": SHARDS["a.md"][:-1] + [141]}
    a = sa.audit_one(_corpus_skill(tmp_path, "dupe", CORE_CITES, clashing))
    assert [n for n, *_ in a["corpus_dupes"]] == SHARDS["a.md"][:-1]


def test_a_dense_procedure_list_is_not_a_corpus(tmp_path):
    """1..n is a procedure. Scoring these as shards reported 15 bogus
    collisions against app-blocks' provably-intact corpus."""
    dense = {"a.md": [1, 2, 3, 4, 5, 6], "b.md": [1, 2, 3, 4, 5, 6]}
    a = sa.audit_one(_corpus_skill(tmp_path, "dense", "# core\n\ngotcha #3\n", dense))
    assert a["corpus_dupes"] == []


def test_a_skill_citing_another_skills_corpus_is_not_policed(tmp_path):
    """Several datapacket skills cite app-blocks' numbers ("gotchas #137" in
    manage-design-system, one in gitops-gate). None of those is theirs to
    resolve. The SIZE gate is what keeps them silent — they have no corpus of
    their own — so this pins the real shape: a reference/ dir, a borrowed
    citation, and too few numbers to be a corpus."""
    tiny = {"a.md": [1, 2, 3]}
    a = sa.audit_one(_corpus_skill(tmp_path, "borrow",
                                   "# core\n\nsee gotcha #137 elsewhere\n", tiny))
    assert a["corpus_n"] == 0 and a["corpus_dangling"] == []


def test_a_bare_hash_number_is_not_read_as_a_citation(tmp_path):
    """`#2319` is a PR. Across app-blocks the bare form matches 313 distinct
    numbers, 189 of them outside the corpus entirely."""
    a = sa.audit_one(_corpus_skill(tmp_path, "prs",
                                   CORE_CITES + "\nShipped in #2319 and #201.\n", SHARDS))
    assert a["corpus_dangling"] == []


def test_a_skill_with_no_reference_dir_is_never_policed(tmp_path):
    d = tmp_path / "flat"
    d.mkdir()
    (d / "SKILL.md").write_text(CORE_CITES + "".join(
        f"{n}. **step {n}**\n\n" for n in range(1, 21)))
    a = sa.audit_one(d / "SKILL.md")
    assert a["corpus_n"] == 0


# --- repo-root-relative sidecar spelling -----------------------------------------
# A repo-local skill may route as `.claude/skills/<name>/reference/<topic>.md` — the
# form prune-skill's sec 4 recommends, because a BARE relative path is resolved by the
# reader against the CWD and not found. That spelling used to score as "not a sidecar",
# so a freshly-split skill reported "no skill routes to a reference/ sidecar yet" with
# five live routing lines, and a missing topic was reported by nobody. Measured on
# datapacket-talos image-cacher 2026-08-17.

REPO_ROOT_CORE = """# core

| File | Load it when… |
|---|---|
| `.claude/skills/rr/reference/alpha.md` | alpha things |
| `.claude/skills/rr/reference/beta.md` | beta things |
"""


def _rr_skill(tmp_path, core, refs):
    d = tmp_path / "rr"
    (d / "reference").mkdir(parents=True)
    (d / "SKILL.md").write_text(core)
    for r in refs:
        (d / "reference" / r).write_text(f"# {r}\n")
    return d / "SKILL.md"


def test_a_repo_root_relative_routing_line_is_recognised(tmp_path):
    a = sa.audit_one(_rr_skill(tmp_path, REPO_ROOT_CORE, ("alpha.md", "beta.md")))
    assert len(a["refs"]) == 2, f"routing lines not seen as sidecars: {a['refs']}"
    assert a["missing_refs"] == []
    assert a["orphan_refs"] == []


def test_positive_control_a_missing_repo_root_relative_topic_is_reported(tmp_path):
    """THE control: before the fix this returned [] — a dead routing line, silently."""
    a = sa.audit_one(_rr_skill(tmp_path, REPO_ROOT_CORE, ("alpha.md",)))
    assert a["missing_refs"] == [".claude/skills/rr/reference/beta.md"], (
        f"a dead repo-root-relative routing line went unreported: {a['missing_refs']}")


def test_a_repo_root_relative_orphan_is_still_reported(tmp_path):
    core = "# core\n\n| `.claude/skills/rr/reference/alpha.md` | alpha |\n"
    a = sa.audit_one(_rr_skill(tmp_path, core, ("alpha.md", "nobody-routes-here.md")))
    assert a["orphan_refs"] == ["reference/nobody-routes-here.md"]


def test_another_repos_reference_path_is_still_not_a_sidecar(tmp_path):
    """Regression guard: `apps/reference/manifest.md` is a page on a CLIENT's public
    docs site, seen in two real datapacket skills. It names a directory that is not
    this skill, so it must stay out — reporting it broken is a false alarm."""
    core = "# core\n\nsee `apps/reference/manifest.md` on the docs site\n"
    a = sa.audit_one(_rr_skill(tmp_path, core, ()))
    assert a["refs"] == [] and a["missing_refs"] == []


# --- deployed (`~/.claude/…`) sidecar spelling -----------------------------------
# 🔴 THE SPELLING A DEVRC READER ACTUALLY USES. devrc skills are READ from
# ~/.claude/skills/ (home-manager copies) by an agent whose cwd is some unrelated
# project, so neither a bare `reference/x.md` nor a repo-relative
# `claude/skills/<n>/reference/x.md` resolves for that reader — only this one does.
#
# It expands to an absolute path under the HOME deploy root rather than under the
# SOURCE skill_dir, so `Path.relative_to(skill_dir)` raises and the resolver used
# to give up and return None. Measured on devrc prune-skill 2026-08-19: three
# sidecars and eleven routing lines scored as "no skill routes to a reference/
# sidecar yet". Converting the corpus to this spelling WITHOUT this fix would have
# traded one blindness for another.

DEPLOYED_CORE = """# core

| File | Load it when… |
|---|---|
| `~/.claude/skills/rr/reference/alpha.md` | alpha things |
| `~/.claude/skills/rr/reference/beta.md` | beta things |
"""


def test_a_deployed_spelling_routing_line_is_recognised(tmp_path):
    a = sa.audit_one(_rr_skill(tmp_path, DEPLOYED_CORE, ("alpha.md", "beta.md")))
    assert len(a["refs"]) == 2, f"deployed routing lines not seen as sidecars: {a['refs']}"
    assert a["missing_refs"] == []
    assert a["orphan_refs"] == []


def test_positive_control_a_missing_deployed_topic_is_reported(tmp_path):
    """THE control: at the pre-fix base this returned [] — a dead routing line in
    the ONLY spelling a devrc reader can follow, reported by nobody."""
    a = sa.audit_one(_rr_skill(tmp_path, DEPLOYED_CORE, ("alpha.md",)))
    assert a["missing_refs"] == ["~/.claude/skills/rr/reference/beta.md"], (
        f"a dead deployed routing line went unreported: {a['missing_refs']}")


def test_a_deployed_spelling_orphan_is_still_reported(tmp_path):
    """🔴 INVARIANT GUARD, not regression coverage — VERIFIED to survive the
    mutation (fall-through -> `return None`), because orphan detection does not
    depend on ref RESOLUTION. Only the two tests above are killed by that mutant.
    Kept because it pins the opposite failure: a fall-through that claimed every
    deployed path would make the routed file itself look orphaned."""
    core = "# core\n\n| `~/.claude/skills/rr/reference/alpha.md` | alpha |\n"
    a = sa.audit_one(_rr_skill(tmp_path, core, ("alpha.md", "nobody-routes-here.md")))
    assert a["orphan_refs"] == ["reference/nobody-routes-here.md"]


def test_a_deployed_path_naming_ANOTHER_skill_is_not_this_skills_sidecar(tmp_path):
    """The fall-through must keep (3)'s disambiguation: the marker names THIS
    skill's own directory. A cross-skill deployed path is a real, common shape —
    prune-skill cites other skills — and must not be scored as a local sidecar,
    or every cross-reference becomes a phantom missing topic.

    🔴 INVARIANT GUARD, not regression coverage — VERIFIED to survive the
    mutation, and it must: a resolver that recognises NOTHING trivially claims
    nothing. It pins the over-reach the fix could have introduced, which is the
    failure only the FIXED resolver can reach."""
    core = "# core\n\nsee `~/.claude/skills/OTHER/reference/alpha.md` in the other skill\n"
    a = sa.audit_one(_rr_skill(tmp_path, core, ()))
    assert a["refs"] == [] and a["missing_refs"] == [], (
        f"a cross-skill deployed path was claimed as a local sidecar: {a}")


def test_a_skill_whose_name_is_a_SUFFIX_of_another_is_not_claimed(tmp_path):
    """🔴 THE SHAPE THAT ACTUALLY COLLIDES, and the one the test above cannot see.

    `OTHER` shares no suffix with `rr`, so that case passes against a plain
    substring `find()` too — it proves nothing about the real hazard. `mailbox`
    and `vetr-mailbox` are BOTH real skills in this repo, and `mailbox/reference/`
    is a substring of `vetr-mailbox/reference/`: before the segment-boundary fix,
    resolving a `vetr-mailbox` route while auditing `mailbox` returned
    'reference/gmail.md' — a cross-reference claimed as a local sidecar and then
    reported as a phantom missing topic.

    🔴 LATENT, NOT OBSERVED — the distinction is the point. Neither skill currently
    references the other, and `skill-audit.py` output over the real corpus is
    BYTE-IDENTICAL before and after the fix. This is a constructed probe of a
    REACHABLE defect, not a field observation; an earlier revision of this
    docstring wrote it in the past tense as if it had been seen in the corpus,
    which is a stronger claim than the evidence supports.

    Kills a `find()`-without-boundary-check mutant; the OTHER case does not.
    """
    d = tmp_path / "mailbox"
    (d / "reference").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "# core\n\nsee `~/.claude/skills/vetr-mailbox/reference/gmail.md`\n")
    a = sa.audit_one(d / "SKILL.md")
    assert a["refs"] == [] and a["missing_refs"] == [], (
        f"a sibling skill whose name is a SUFFIX of this one was claimed: {a}")


def test_two_spellings_of_one_topic_count_once(tmp_path):
    """app-blocks names legacy-hackathon-ops.md both ways; counting spellings
    reported 16 "reference file(s)" for 15 files.

    🔴 INVARIANT GUARD, not a regression test — it passes at the pre-fix base too,
    for the wrong reason: there the second spelling was not recognised at all, so
    the count was 1 by blindness rather than by deduping. Nothing expressible as a
    baseline test separates those, so do not read this as covering the dedupe.
    """
    core = ("# core\n\n`reference/alpha.md` in the table, and "
            "`.claude/skills/rr/reference/alpha.md` in the repo-layout section\n")
    a = sa.audit_one(_rr_skill(tmp_path, core, ("alpha.md",)))
    assert len(a["refs"]) == 1, f"same file counted {len(a['refs'])}x: {a['refs']}"


# --- the working-margin band: the auditor must not disagree with the gate ------
# 🔴 REGRESSION. Measured on main at 3393c60b: scripts/browser-bridge/SKILL.md was
# 12,259 B. test_skill_size.py::test_skill_md_keeps_working_headroom was RED (29 B
# of free space against a 250 B required margin — "RECLAIM: 221 bytes") while this
# auditor — the tool /prune-skill documents a maintainer to run — printed
#
#     ✓ all 1 skill(s) within budget — no prune needed (stop; do not churn the files)
#
# about that same file. It compared against the CEILING only and was structurally
# blind to the headroom floor, so the documented instrument said STOP about a file
# the authoritative gate was rejecting. These tests pin both halves: the constants
# are READ from the gate rather than restated, and a file in the band is a FINDING
# — checked in BOTH directions, because a checker that always says "prune needed"
# is as useless as one that never does.

GATE_PY = SCRIPTS / "browser-bridge" / "tests" / "test_skill_size.py"
gate = _load("browser-bridge/tests/test_skill_size.py", "browser_skill_size_gate")


def _skill_of_size(tmp_path, name, size):
    """A well-formed SKILL.md of EXACTLY `size` bytes and nothing else wrong.

    No reference/ dir: a skill that HAS one but routes nowhere is an ORPHAN
    finding, which would move the verdict for a reason that is not the size band
    — red for the wrong reason, and still red with the band check deleted.
    """
    # 🔴 Make the fixture root a GOVERNED tree. These fixtures model a devrc
    # skill, and governance is now three-valued: without a repo + gate marker they
    # resolve to None and are correctly labelled "[governance unknown]", which is
    # true of the fixture and false of what it represents. Planting the marker
    # makes the fixture mean what its assertions have always assumed.
    if not (tmp_path / ".git").exists():
        # callers pass a not-yet-created subdir as the root, so create it first
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / ".git").write_text("gitdir: /fixture\n")
        m = tmp_path / sa._GATE_MARKER
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("MAX_BYTES = 12288\nMIN_HEADROOM_BYTES = 250\n")
    d = tmp_path / name
    d.mkdir(parents=True)
    head = (f"---\nname: {name}\ndescription: a fixture sized to the byte.\n"
            f"---\n\n## Ops\n\n").encode()
    assert size >= len(head) + 1, f"{size} is too small to build a valid fixture"
    remaining = size - len(head)
    chunks = []
    while remaining > 80:
        chunks.append(b"x" * 79 + b"\n")
        remaining -= 80
    chunks.append(b"x" * (remaining - 1) + b"\n")
    p = d / "SKILL.md"
    p.write_bytes(head + b"".join(chunks))
    assert len(p.read_bytes()) == size, (
        "the fixture builder is wrong — every size test below is void")
    return p


def test_the_gate_module_is_the_one_the_auditor_reads():
    """Guard the guard: if this path is wrong, everything below measures a module
    the auditor never loads."""
    assert GATE_PY.is_file()
    assert sa._BUDGET_SOURCE == GATE_PY.resolve()


def test_the_budget_constants_come_from_the_gate_not_a_second_copy():
    assert sa.TARGET == gate.MAX_BYTES
    assert sa.MIN_HEADROOM == gate.MIN_HEADROOM_BYTES
    assert sa.BUDGET == gate.MAX_BYTES - gate.MIN_HEADROOM_BYTES


def test_no_budget_number_is_written_as_a_literal_in_the_auditor():
    """Equality alone cannot separate a derived value from a coincidence — a
    pasted literal satisfies it until the day the gate moves.

    MIN_HEADROOM is deliberately NOT checked: it could legitimately equal an
    unrelated constant (FAT_LINE is 500), and a false failure on a coincidence is
    worse than the narrower pin. The mechanical control in
    test_changing_the_gates_numbers_changes_what_the_auditor_reads covers it.
    """
    import ast
    literals = {n.value for n in ast.walk(ast.parse(AUDIT_PY.read_text()))
                if isinstance(n, ast.Constant) and isinstance(n.value, int)
                and not isinstance(n.value, bool)}
    assert sa.TARGET not in literals, (
        f"{sa.TARGET} is a numeric literal in skill-audit.py — the ceiling is "
        f"owned by {GATE_PY}, and a second hand-maintained copy is exactly how "
        "the audit and the gate came to disagree.")
    assert sa.BUDGET not in literals, (
        f"{sa.BUDGET} (the ENFORCED budget) is a literal in skill-audit.py; it "
        "must be derived as TARGET - MIN_HEADROOM.")


def test_changing_the_gates_numbers_changes_what_the_auditor_reads(tmp_path):
    """The mechanical control for the derivation: feed _load_budget a DIFFERENT
    gate file and watch the numbers move. Values picked so neither can equal the
    real ones by accident."""
    fake = tmp_path / "fake_gate.py"
    fake.write_text("MAX_BYTES = 9_001\nMIN_HEADROOM_BYTES = 137\n")
    assert sa._load_budget(fake) == (9_001, 137)


def test_a_missing_gate_file_is_a_hard_failure_not_a_fallback(tmp_path):
    """A default literal on the missing-file path would BE the duplicate this
    design removes — and it would apply silently, which is the worse half."""
    with pytest.raises(SystemExit) as e:
        sa._load_budget(tmp_path / "not-there.py")
    assert "OWNS the ceiling" in str(e.value)


def test_a_renamed_gate_constant_fails_loudly(tmp_path):
    fake = tmp_path / "renamed.py"
    fake.write_text("CEILING = 9_001\n")
    with pytest.raises(SystemExit) as e:
        sa._load_budget(fake)
    assert "no longer exports the budget constants" in str(e.value)


# --- the band itself, in BOTH directions ---------------------------------------

def test_a_file_in_the_warning_band_says_prune_needed(tmp_path):
    """THE non-vacuity test: a body inside the ceiling but past the working margin
    is exactly what the ceiling-only check called ✓."""
    size = sa.BUDGET + 1
    assert size < sa.TARGET, "the band is empty — this test would prove nothing"
    p = _skill_of_size(tmp_path, "tight", size)
    assert sa.audit_one(p)["status"] == "NO HEADROOM"
    out = _run(tmp_path)
    assert "⚠ prune needed" in out, out
    assert "no prune needed" not in out, out
    assert "the gate REJECTS these" in out, out


def test_a_comfortably_small_file_still_says_no_prune_needed(tmp_path):
    p = _skill_of_size(tmp_path, "lean", 2_000)
    assert sa.audit_one(p)["status"] == "OK"
    out = _run(tmp_path)
    assert "no prune needed" in out
    assert "prune needed —" not in out


@pytest.mark.parametrize("offset,expected", [(-1, "OK"), (0, "OK"), (1, "NO HEADROOM")])
def test_the_enforced_budget_boundary_is_exact(tmp_path, offset, expected):
    """Inclusive: BUDGET bytes leaves exactly MIN_HEADROOM free, which is what the
    gate requires (>=, not >). One byte more is a finding."""
    p = _skill_of_size(tmp_path, "b", sa.BUDGET + offset)
    assert sa.audit_one(p)["status"] == expected


@pytest.mark.parametrize("offset,expected",
                         [(-1, "NO HEADROOM"), (0, "NO HEADROOM"), (1, "OVER TARGET")])
def test_the_ceiling_boundary_is_exact(tmp_path, offset, expected):
    p = _skill_of_size(tmp_path, "c", sa.TARGET + offset)
    assert sa.audit_one(p)["status"] == expected


def test_the_hard_cap_boundary_is_unchanged_by_the_new_band(tmp_path):
    assert sa.audit_one(_skill_of_size(tmp_path / "a", "h", sa.HARD))["status"] == "OVER TARGET"
    assert sa.audit_one(_skill_of_size(tmp_path / "b", "h", sa.HARD + 1))["status"] == "OVER HARD CAP"


@pytest.mark.parametrize("base,delta", [
    (None, 2_000),
    ("BUDGET", -100), ("BUDGET", -1), ("BUDGET", 0), ("BUDGET", 1), ("BUDGET", 100),
    ("TARGET", -1), ("TARGET", 0), ("TARGET", 1), ("TARGET", 5_000),
])
def test_the_auditor_and_the_gate_never_disagree(tmp_path, base, delta):
    """The disagreement itself, pinned. For ANY size, "the auditor reports a
    finding" must equal "the gate's headroom assertion fails" — the right-hand
    side computed from the GATE's own constants, never from the auditor's.

    This is the property the bug violated: at 12,259 B the gate said RED and the
    auditor said ✓.
    """
    size = delta if base is None else {"BUDGET": sa.BUDGET, "TARGET": sa.TARGET}[base] + delta
    p = _skill_of_size(tmp_path, "x", size)
    gate_is_red = (gate.MAX_BYTES - size) < gate.MIN_HEADROOM_BYTES
    auditor_reports_finding = sa.audit_one(p)["status"] != "OK"
    assert auditor_reports_finding == gate_is_red, (
        f"at {size:,} B the gate says {'RED' if gate_is_red else 'green'} and the "
        f"auditor says {'finding' if auditor_reports_finding else '✓'} — that is "
        "the exact disagreement this block exists to prevent.")


def test_the_budget_line_states_the_ENFORCED_number_not_only_the_ceiling(tmp_path):
    """A maintainer reads the header to learn what they must fit under. Printing
    only the ceiling is what makes 12,2xx B look like room."""
    _skill_of_size(tmp_path, "lean", 2_000)
    out = _run(tmp_path)
    assert f"budget {sa.BUDGET:,} B ENFORCED" in out, out
    assert f"ceiling {sa.TARGET:,} B" in out, out
    assert f"{sa.MIN_HEADROOM:,} B working margin" in out, out


def test_the_real_browser_skill_agrees_with_its_own_gate():
    """The live cross-check on the file the whole pattern is the exemplar for —
    the repo's CURRENT SKILL.md, so a regrowth into the band is caught on the next
    commit that touches it, not only in fixtures."""
    browser = SCRIPTS / "browser-bridge" / "SKILL.md"
    size = len(browser.read_bytes())
    gate_is_red = (gate.MAX_BYTES - size) < gate.MIN_HEADROOM_BYTES
    assert sa.audit_one(browser)["status"] == "OK" and not gate_is_red, (
        f"browser SKILL.md is {size:,} B; the enforced budget is {sa.BUDGET:,} B")


# ── _foreign_repos: which trees this tool's budget actually governs ──────────
# 🔴 ADDED AFTER A PRE-MERGE AUDIT FOUND THIS LOGIC SHIPPED WITH ZERO COVERAGE
# (devrc #924, finding 8), which is how findings 3 and 4 got in: a dead ternary
# branch that made a mixed-repo run name only the foreign repo, and a path-equality
# devrc test that classified every devrc WORKTREE as foreign — on a host with 60+
# of them, and with SKILL.md telling readers to believe that line over the number.
# Both are pinned below. Each case is one line of production behaviour.


def _fake_repo(root, *, governed):
    """A directory that looks like a repo to the walker.

    `governed` decides whether the gate marker is present -- that, not the path,
    is what makes a tree one this tool's budget binds.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").write_text("gitdir: /elsewhere\n")   # a WORKTREE's .git is a FILE
    if governed:
        marker = root / sa._GATE_MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("MAX_BYTES = 1\nMIN_HEADROOM_BYTES = 1\n")
    skills = root / ".claude" / "skills" / "x"
    skills.mkdir(parents=True, exist_ok=True)
    (skills / "SKILL.md").write_text("# x\n")
    return skills / "SKILL.md"


def test_foreign_repos_governed_tree_is_not_flagged(tmp_path):
    """The gate marker is present, so the budget binds and there is nothing to warn about."""
    f = _fake_repo(tmp_path / "devrc-like", governed=True)
    assert sa._foreign_repos([str(f)]) == []


def test_foreign_repos_a_worktree_of_a_governed_repo_is_governed(tmp_path):
    """🔴 THE REGRESSION THAT SHIPPED. A worktree's `.git` is a FILE, so a check
    comparing the repo root to the script's own checkout path classified every
    devrc worktree as foreign and printed 'THESE SKILLS ARE IN <devrc>, NOT devrc'.
    Identity is the marker the tree CONTAINS, never where the tree sits."""
    f = _fake_repo(tmp_path / "some-worktree-path", governed=True)
    assert sa._foreign_repos([str(f)]) == [], (
        "a governed tree must stay governed no matter where it is checked out")


def test_foreign_repos_ungoverned_tree_is_flagged(tmp_path):
    f = _fake_repo(tmp_path / "other-repo", governed=False)
    assert sa._foreign_repos([str(f)]) == [tmp_path / "other-repo"]


def test_foreign_repos_returns_every_ungoverned_root_not_just_one(tmp_path):
    """🔴 The dead-ternary bug: both branches were `sorted(roots)[0]`, so two
    foreign repos silently collapsed to the alphabetically-first one."""
    a = _fake_repo(tmp_path / "aaa-repo", governed=False)
    b = _fake_repo(tmp_path / "zzz-repo", governed=False)
    assert sa._foreign_repos([str(a), str(b)]) == [tmp_path / "aaa-repo", tmp_path / "zzz-repo"]


def test_foreign_repos_mixed_run_still_reports_the_ungoverned_one(tmp_path):
    """A governed path must not mask an ungoverned one -- and vice versa; the
    header's `mixed` flag is what stops the banner reading as a whole-run verdict."""
    g = _fake_repo(tmp_path / "governed", governed=True)
    u = _fake_repo(tmp_path / "ungoverned", governed=False)
    assert sa._foreign_repos([str(g), str(u)]) == [tmp_path / "ungoverned"]


def test_a_path_in_no_repo_at_all_is_unknowable_not_governed(tmp_path):
    """🔴 THIS TEST PREVIOUSLY BLESSED A DEFECT. It asserted only that such a path
    is "not flagged", which is true of the ARGUMENT and was then used to justify
    computing governance from the CLI arguments — so a directory above the repo
    roots made `foreign` empty and printed ENFORCED over entirely foreign skills
    (round-3 delta audit, finding 3). The honest claim is three-valued: unknowable
    is NOT governed, and callers must not collapse it to either bool. The
    end-to-end guard is test_governance_comes_from_the_targets_not_the_cli_argument.
    """
    loose = tmp_path / "loose"
    loose.mkdir()
    (loose / "SKILL.md").write_text("# x\n")
    assert sa._is_governed(loose / "SKILL.md") is None
    assert sa._foreign_repos([str(loose / "SKILL.md")]) == []


# ── governance is PER-SKILL, and `mixed` is a real feature ───────────────────
# 🔴 ROUND-3 DELTA AUDIT, findings 1/3/6. The round-2 fix for "a mixed run named
# only the foreign repo" introduced the same harm one step further on: the mark
# was keyed to the RUN, so the governed skill of a mixed run was stamped
# [ungoverned] while the banner directly above promised "the mark is per-line".
# Two mutants also survived a fully green suite (`if mixed:` -> `if False:`, and
# `mixed=...` -> `mixed=False`) because the test named "mixed" audited a single
# ungoverned directory, where `mixed` is False throughout.


def _governed_repo(root, *, governed, skill_bytes):
    """A repo fixture with one skill of a chosen size."""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".git").write_text("gitdir: /elsewhere\n")
    if governed:
        m = root / sa._GATE_MARKER
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text("MAX_BYTES = 12288\nMIN_HEADROOM_BYTES = 250\n")
    d = root / ".claude" / "skills" / root.name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_bytes(b"# s\n" + b"y" * (skill_bytes - 4))
    return root / ".claude" / "skills"


def _size_rows(out):
    """Just the rows of the `## sizes` section.

    🔴 Scoped deliberately: `--all` also prints per-skill DETAIL blocks whose
    section-weight lines have the identical `N B  ...` shape, so an unscoped
    regex counted 6 rows for a 2-skill run and failed against correct behaviour.
    """
    lines, keep, rows = out.splitlines(), False, []
    for l in lines:
        if l.startswith("## sizes"):
            keep = True
            continue
        if keep and l.startswith("##"):
            break
        if keep and re.match(r"\s+[\d,]+ B\s", l):
            rows.append(l)
    return rows


def _mixed_out(tmp_path, n_gov=2):
    """A run over n_gov governed repos and one ungoverned, all genuinely in breach.

    🔴 n_gov DEFAULTS TO 2, NOT 1, and that is load-bearing. With one of each, the
    verdict's two counts are equal, so swapping them in the f-string produces a
    byte-identical string and the guard cannot see the inversion — the same
    pairwise-identical-fixture trap this file fixed at the sizes row and then
    re-created in the verdict guard. Round-7 finding 2.
    """
    args = []
    for i in range(n_gov):
        args.append(str(_governed_repo(tmp_path / f"govrepo{i}", governed=True,
                                       skill_bytes=sa.BUDGET + 116)))
    args.append(str(_governed_repo(tmp_path / "ungovrepo", governed=False,
                                   skill_bytes=sa.BUDGET + 116)))
    r = subprocess.run([sys.executable, str(AUDIT_PY), *args, "--all"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_mixed_run_marks_ungoverned_per_line_not_per_run(tmp_path):
    """🔴 The governed skill must NOT be stamped [ungoverned]."""
    out = _mixed_out(tmp_path)
    # 🔴 Count SKILL lines only. The banner itself contains the literal
    # "[ungoverned]" because it explains the mark, so an unscoped grep counts the
    # explanation as an instance — which is how the first version of this test
    # failed against correct behaviour.
    rows = _size_rows(out)
    marked = [l for l in rows if "[ungoverned]" in l]
    assert len(rows) == 3, f"expected three skill rows, got {len(rows)}:\n{out}"
    assert len(marked) == 1, f"expected exactly one marked row, got {len(marked)}:\n{out}"
    # 🔴 WHICH row, not just how many. The mark and the budget label come from one
    # expression, so inverting it (`is False` -> `is True`) swaps them TOGETHER and
    # leaves "exactly one marked" true — round-5 finding 1 reproduced that at 141
    # green, i.e. round-3's headline defect could come back and pass.
    assert "ungovrepo" in marked[0], f"the UNGOVERNED skill must be the marked one:\n{out}"
    assert "devrc reference budget" in marked[0], out
    gov_rows = [l for l in rows if "govrepo" in l and "ungovrepo" not in l]
    assert len(gov_rows) == 2, f"expected two governed rows:\n{out}"
    assert all("[ungoverned]" not in l for l in gov_rows), (
        f"the governed skills must be unmarked:\n{out}")
    assert all("over the enforced budget" in l for l in gov_rows), out


def test_mixed_run_still_calls_the_governed_skill_ENFORCED(tmp_path):
    """The other half: a devrc file in real breach must keep being told so."""
    out = _mixed_out(tmp_path)
    assert "over the enforced budget" in out, out
    assert "over the devrc reference budget" in out, out


def test_mixed_banner_is_printed_when_the_run_mixes(tmp_path):
    """Kills `if mixed:` -> `if False:` and `mixed=...` -> `mixed=False`, both of
    which survived the round-2 suite because its 'mixed' test was not mixed."""
    assert "This run MIXES governed and ungoverned trees" in _mixed_out(tmp_path)


def test_a_purely_foreign_run_is_not_called_mixed(tmp_path):
    """The negative control: without a governed target there is nothing to mix,
    so the extra warning must stay off or it becomes noise on every foreign run."""
    u = _governed_repo(tmp_path / "onlyforeign", governed=False, skill_bytes=sa.BUDGET + 50)
    out = _run(u, "--all")
    assert "devrc's DEFAULT (not enforced here)" in out, out
    assert "This run MIXES" not in out, out


def test_detail_header_and_verdict_do_not_assert_the_gate_on_a_foreign_tree(tmp_path):
    """🟡 Round-3 finding 2: the sizes-list note was de-asserted but `_overage`'s
    detail header ("over enforced budget by N") and the verdict's "cut ~N B total"
    were not — verdicts about a gate that does not bind the file."""
    u = _governed_repo(tmp_path / "foreigndetail", governed=False, skill_bytes=sa.BUDGET + 166)
    out = _run(u, "--all")
    assert "over enforced budget by" not in out, out
    assert "over devrc reference budget by" in out, out
    assert "does not govern every tree here" in out, out


def test_governance_comes_from_the_targets_not_the_cli_argument(tmp_path):
    """🟡 Round-3 finding 3, and the round-2 test that BLESSED it.

    A directory argument ABOVE the repo roots resolves to no repo, so keying on
    the ARGUMENT made `foreign` empty and printed ENFORCED over skills that were
    entirely foreign. The argument's governance really is unknown; the targets'
    is not, and the targets are what gets audited.
    """
    _governed_repo(tmp_path / "parent" / "repoA", governed=False, skill_bytes=sa.BUDGET + 20)
    out = _run(tmp_path / "parent", "--all")
    assert "devrc's DEFAULT (not enforced here)" in out, out
    assert f"budget {sa.BUDGET:,} B ENFORCED" not in out, (
        "an argument above the repo roots must not launder foreign skills as enforced")


def test_the_gate_marker_names_a_file_that_actually_exists():
    """🔴 Every other governed-side fixture builds the marker as `root /
    sa._GATE_MARKER`, so it holds for ANY value of the constant — mutating it to a
    wrong filename left the suite at 141 green while the real tool printed
    "THESE SKILLS ARE IN <devrc> — NOT a tree governed by…" over devrc's own
    skills, the exact self-contradiction the structural test exists to prevent.
    This is the only assertion that pins the constant to reality."""
    assert (SCRIPTS.parent / sa._GATE_MARKER).is_file(), (
        f"_GATE_MARKER points at {sa._GATE_MARKER}, which does not exist — devrc "
        "would classify ITSELF as ungoverned on every run")


def test_a_skill_in_no_repo_is_labelled_unknown_not_enforced(tmp_path):
    """🔴 F2, a REGRESSION round 4 introduced: `is False` sent the None arm down
    the GOVERNED branch, so a skill in no repo at all was told "over the enforced
    budget" under a banner saying nothing binds there. Round 2 had marked it.
    Reachable, not synthetic: 34 of the 37 skills installed at ~/.claude/skills
    resolve into /nix/store and are in no repo."""
    loose = tmp_path / "norepo" / ".claude" / "skills" / "s"
    loose.mkdir(parents=True)
    (loose / "SKILL.md").write_bytes(b"# s\n" + b"y" * (sa.BUDGET + 112))
    out = _run(tmp_path / "norepo" / ".claude" / "skills", "--all")
    # 🔴 THE HEADER, NOT ONLY THE ROW. This test and its sibling both built the
    # exact state that the three-valued run-state fix exists for, and both scoped
    # every assertion to a size row or a detail block — so FIVE separate reverts of
    # that fix passed a fully green suite, including the one that restores the
    # header/row contradiction verbatim. The row was right and the banner above it
    # still said "the number the gate rejects at". Round-9 finding 1.
    assert f"budget {sa.BUDGET:,} B ENFORCED" not in out, (
        f"the header must not claim enforcement over a tree in no repo:\n{out}")
    assert "is the number the gate rejects at" not in out, (
        f"the enforced explainer must not print for an unknowable tree:\n{out}")
    assert "IN NO REPO" in out, f"the unknown banner must name the actual state:\n{out}"
    row = [l for l in _size_rows(out) if "B" in l][0]
    assert "[governance unknown]" in row, f"None must get its OWN label:\n{out}"
    assert "over the enforced budget" not in row, (
        f"an unknowable tree must not be told devrc's gate enforces it:\n{out}")


def test_mixed_verdict_tells_the_governed_skill_the_gate_rejects_it(tmp_path):
    """🔴 F3: the verdict's REJECT clause was keyed to the RUN, so on a mixed run
    the devrc file in real breach was told to "check this repo's own" gate —
    circular, since it IS devrc — and never told the gate rejects it."""
    out = _mixed_out(tmp_path)
    verdict = out[out.index("## verdict"):]
    # 🔴 The counts must be DISTINCT (2 and 1), or swapping them in the f-string
    # is a no-op and this assertion cannot see the inversion.
    assert "the gate REJECTS 2 of them" in verdict, verdict
    assert "the other 1" in verdict, verdict
    assert "check that repo's own gate" in verdict, verdict


def test_the_within_budget_verdict_does_not_claim_enforcement_on_a_foreign_tree(tmp_path):
    """🔴 F3: the "all N skill(s) within budget (N B enforced)" line — the most-read
    line for the common "point it at another repo, it's fine" case — shipped with no
    test, so reverting it outright left the suite fully green."""
    u = _governed_repo(tmp_path / "smallforeign", governed=False, skill_bytes=2_000)
    out = _run(u, "--all")
    assert "within budget" in out, out
    assert f"({sa.BUDGET:,} B — enforced)" not in out, (
        f"an ungoverned tree must not be told the budget is enforced:\n{out}")
    assert "not enforced here" in out, out


def test_the_detail_header_says_unknown_for_a_skill_in_no_repo(tmp_path):
    """🔴 F4: the None arm of `_overage` was unguarded — the sizes-row test scopes
    to `_size_rows(out)[0]` and never reads the detail header, so reverting only
    that default to "enforced budget" left the suite green. Its False-arm sibling
    pins exactly this; the None arm had no equivalent."""
    loose = tmp_path / "norepo2" / ".claude" / "skills" / "s"
    loose.mkdir(parents=True)
    (loose / "SKILL.md").write_bytes(b"# s\n" + b"y" * (sa.BUDGET + 112))
    out = _run(tmp_path / "norepo2" / ".claude" / "skills", "--all")
    detail = out[out.index("## s —"):] if "## s —" in out else out
    assert "over enforced budget by" not in detail, (
        f"an unknowable tree must not get an enforced-budget detail header:\n{out}")
    assert "governance unknown" in detail, out


def test_a_governed_plus_unknown_run_is_mixed_and_names_only_real_states(tmp_path):
    """🔴 F1/F3: `mixed` was keyed to `foreign` alone, so a governed + no-repo run
    dropped "SOME OF THESE" and asserted the whole run was ungoverned; and the
    banner named "[ungoverned]" and "ungoverned trees" in a run that contains
    neither. Both reverts passed a green suite."""
    g = _governed_repo(tmp_path / "gv", governed=True, skill_bytes=sa.BUDGET + 116)
    n = tmp_path / "nr" / ".claude" / "skills" / "s"
    n.mkdir(parents=True)
    (n / "SKILL.md").write_bytes(b"# s\n" + b"y" * (sa.BUDGET + 116))
    r = subprocess.run([sys.executable, str(AUDIT_PY), str(g),
                        str(tmp_path / "nr" / ".claude" / "skills"), "--all"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "SOME OF THESE SKILLS ARE IN NO REPO" in out, out
    assert "MIXES governed and unknowable trees" in out, out
    # the marker named must be the one the rows actually carry
    assert "[ungoverned]" not in out, (
        f"no tree here is ungoverned; naming that marker points at a string the "
        f"run never emits:\n{out}")
    assert "[governance unknown]" in out, out


def test_the_cut_total_qualifier_asserts_nothing_about_an_individual_tree(tmp_path):
    """🟡 F2: this qualifier was `is False` (under-covering unknown), then
    `is not True` (attaching "not enforced there" to a total that included
    genuinely governed devrc skills in real breach). Neither bool is true of a
    mixed total."""
    out = _mixed_out(tmp_path)          # 2 governed + 1 ungoverned, all in breach
    verdict = out[out.index("## verdict"):]
    assert "cut ~" in verdict, verdict
    assert "not enforced there" not in verdict, (
        f"the total includes governed skills, so it must not claim they are "
        f"unenforced:\n{verdict}")
    assert "does not govern every tree here" in verdict, verdict


def test_a_purely_foreign_header_says_not_enforced_here_precisely(tmp_path):
    """🟢 F6: "not enforced on every tree here" implies some are. On a run where
    NONE is governed the older, exact wording is the true one."""
    u = _governed_repo(tmp_path / "onlyf", governed=False, skill_bytes=2_000)
    out = _run(u, "--all")
    assert "devrc's DEFAULT (not enforced here)" in out, out
    assert "not enforced on every tree here" not in out, out
