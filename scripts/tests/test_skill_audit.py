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
