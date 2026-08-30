"""Unit tests for scripts/handoff-audit.py — the handoff-doc auditor.

OFFLINE and hermetic: every fixture is written into a tmp_path. Nothing under
~/.claude, $HOME or any out-of-repo clone is read, and no test here skips.

🔴 HARNESS DISCIPLINE (claude/RULES.md). This auditor's reassuring answers are
ZEROS — "0 evictable bytes", "0 completed ranks". A zero is indistinguishable
from a detector wired to nothing, so every counter is exercised in BOTH
directions: a fixture that MUST score zero, and one that MUST score an exact
non-zero value derived from the fixture text rather than from the code.

🔴 THREE OF THESE ARE REGRESSION TESTS FOR DEFECTS THAT ACTUALLY SHIPPED IN THE
FIRST RUN OF THIS TOOL, each of which produced a confident wrong corpus number:

  test_h1_title_is_never_work_status
      The first version reused skill-audit's WORK_STATUS_HEADING, which keys on
      a bare `\\bsessions?\\b`. A handoff doc's H1 is `# Handoff: <topic> — <date>`
      and topics like `session-makework-audit` / `find-session-live-first`
      contain the word. An H1's extent is the whole file, so entire documents
      were scored as evictable history — 60,122 B of a 60,122 B doc.
  test_forward_looking_headings_are_not_evictable
      Same matcher swept up `Kickoff message for next session` and `Quick state
      checks (next session)` — the live half of the doc.
  test_done_marker_is_bounded_to_the_item_head
      A DONE scan over the whole item body marks an OPEN item complete because
      its body mentions some other PR merging.

Measured effect of the first two: the work-status bucket read 1,026,330 B over
343 blocks; corrected it is 236,746 B over 119 — a 4.3x overstatement, in the
bucket that dominated the report.
"""
import importlib.machinery
import importlib.util
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


HA = _load("handoff-audit.py", "_handoff_audit")


def _doc(tmp_path, body, name="handoff-x.md"):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return HA.audit_one(p)


# --- the negative control: a clean doc must score zero everywhere ---------------

CLEAN = """# Handoff: widget-tuning — 2026-08-29

## Goal
Make the widget faster.

## State now
- Nothing has shipped yet.

## Next steps (ranked)
1. **Measure the widget** (repo; scripts/widget.py).
2. **Then tune it** — only after 1.

## Gotchas / decisions / dead-ends
- The widget cache is per-node.

## How to verify
Run the widget.
"""


def test_clean_doc_scores_zero_on_every_counter(tmp_path):
    a = _doc(tmp_path, CLEAN)
    assert a["gross"] == 0
    assert a["net"] == 0
    assert a["done"] == []
    assert a["resolved"] == []
    assert a["retracted"] == []
    assert a["dated"] == []
    # positive half of the same read: the parser DID see the ranked items, so the
    # zero above is an absence and not a failure to parse.
    assert [n for n, *_ in a["ranked"]] == [1, 2]
    assert a["fence_ok"] is True


# --- regression: an H1 whose topic slug contains "session" -----------------------

H1_SESSION = """# Handoff: session-makework-audit — 2026-08-26

## Goal
Audit make-work.

## Next steps (ranked)
1. **Do the thing.**
"""


def test_h1_title_is_never_work_status(tmp_path):
    """RED before the fix: the H1 matched `\\bsession\\b` and its extent is the
    whole file, so the entire doc was scored as evictable history."""
    a = _doc(tmp_path, H1_SESSION)
    assert a["dated"] == []
    assert a["dated_b"] == 0
    # and the guard is not passing merely because nothing parsed
    assert len(a["ranked"]) == 1


# --- regression: forward-looking headings are the LIVE half ----------------------

FORWARD = """# Handoff: thing — 2026-08-29

## Kickoff message for next session
Paste this to resume.

## Quick state checks (next session)
- check the thing

## Next steps (ranked)
1. **Do it.**
"""


def test_forward_looking_headings_are_not_evictable(tmp_path):
    a = _doc(tmp_path, FORWARD)
    assert a["dated"] == [], "a kickoff/next-session heading is instructions, not history"
    assert a["dated_b"] == 0


# --- the positive control: completed-work headings MUST be detected --------------

SHIPPED = """# Handoff: thing — 2026-08-29

## What shipped
- PR #1 merged.
- PR #2 merged.

## Next steps (ranked)
1. **Do it.**
"""


def test_completed_work_heading_is_detected(tmp_path):
    """The counter must move OFF zero, or the three tests above prove nothing."""
    a = _doc(tmp_path, SHIPPED)
    assert len(a["dated"]) == 1
    assert a["dated"][0][0] == "What shipped"
    assert a["dated_b"] > 0
    assert a["gross"] >= a["dated_b"]


# --- regression: the DONE scan is bounded to the item's own head -----------------

DONE_IN_BODY = """# Handoff: thing — 2026-08-29

## Next steps (ranked)
1. **Build the exporter** (repo; scripts/x.py). This depends on the upstream
   change, which was MERGED last week as #900, so the blocker is gone and this
   item is still entirely open and unstarted. It has not SHIPPED.
2. ✅ **DONE** — the other thing.
"""


def test_done_marker_is_bounded_to_the_item_head(tmp_path):
    """Item 1 is OPEN; only its BODY says MERGED/SHIPPED. Item 2 is genuinely done.

    An unbounded scan marks both complete — and in a real doc that word is almost
    always somewhere in the body, so the unbounded version marks nearly every
    item done.
    """
    a = _doc(tmp_path, DONE_IN_BODY)
    assert len(a["ranked"]) == 2
    assert [r[0] for r in a["done"]] == [2]
    # the body-mention really is inside item 1's extent, so the bound is what
    # excludes it rather than the text being absent
    n1, s1, e1, _b1, _d1 = a["ranked"][0]
    body = "".join(DONE_IN_BODY.splitlines(keepends=True)[s1:e1])
    assert "MERGED" in body and "SHIPPED" in body


# --- the rank number must survive an eviction ------------------------------------


BIG_DONE = """# Handoff: thing — 2026-08-29

## Next steps (ranked)
1. ✅ **DONE** — shipped it. """ + ("Verification narrative. " * 40) + """
2. **Still open** — do the next thing.
"""


def test_net_saving_is_charged_per_evicted_rank(tmp_path):
    """Evicting a completed rank leaves a resume line, because the NUMBER is half
    a claim-work slug identity. The projection must pay for it.

    The done item is deliberately larger than RESUME_COST so the clamp in
    test_net_never_goes_below_zero is not what this test is measuring.
    """
    a = _doc(tmp_path, BIG_DONE)
    assert [r[0] for r in a["done"]] == [1]
    assert a["gross"] > HA.RESUME_COST
    assert a["gross"] - a["net"] == HA.RESUME_COST * 1


def test_net_never_goes_below_zero(tmp_path):
    """A tiny completed item costs more to summarise than it saves; the projection
    must clamp rather than report a negative saving that cancels real overage."""
    a = _doc(tmp_path, "# Handoff: t — 2026-08-29\n\n## Next steps (ranked)\n1. ✅ DONE\n")
    assert len(a["done"]) == 1
    assert a["gross"] < HA.RESUME_COST
    assert a["net"] == 0


# --- fence awareness is inherited, and must actually be inherited ----------------

FENCED = """# Handoff: thing — 2026-08-29

## Next steps (ranked)
1. **Do it.**

```bash
## What shipped
2. not a real ranked item
```

## Gotchas / decisions / dead-ends
- a live bullet

```bash
- this REFUTED line is inside a fence
```
"""


def test_headings_and_items_inside_a_fence_are_ignored(tmp_path):
    """🔴 The fenced item is numbered 2, NOT 1, on purpose.

    With `1.` this test passed for the WRONG REASON — `_ranked_items` drops
    non-ascending numbers, so the fenced line was rejected by the ascending-run
    rule and the fence played no part. A fenced `2.` after a real item 1 ascends,
    so only genuine fence awareness can exclude it. Caught by the round-1 audit.
    """
    a = _doc(tmp_path, FENCED)
    assert [t for t, *_ in a["h2"]] == ["Next steps (ranked)",
                                        "Gotchas / decisions / dead-ends"]
    assert a["dated"] == [], "a ## heading inside a ```bash block is a comment"
    assert len(a["ranked"]) == 1, "the fenced `2.` must not be booked as a rank"
    assert a["retracted"] == [], "a REFUTED line inside a fence is not a bullet"


def test_unclosed_fence_is_reported(tmp_path):
    a = _doc(tmp_path, "# H\n\n## Next steps (ranked)\n1. x\n\n```bash\nunclosed\n")
    assert a["fence_ok"] is False


# --- resolved investigations and retracted bullets --------------------------------

INVESTIGATIONS = """# Handoff: thing — 2026-08-29

## Open investigations — live diagnosis state
### ✅ CLOSED — the first question
It was answered.

### Why the second thing happens
- **Leading hypothesis:** unknown.

## Gotchas / decisions / dead-ends
- **Dead end (do not re-derive):** the cache theory was REFUTED.
- The widget cache is per-node.
"""


def test_resolved_investigations_and_retracted_bullets(tmp_path):
    a = _doc(tmp_path, INVESTIGATIONS)
    assert [t for t, *_ in a["resolved"]] == ["✅ CLOSED — the first question"]
    assert a["resolved_b"] > 0
    assert len(a["retracted"]) == 1, "only the REFUTED/dead-end bullet, not the live one"
    assert a["retracted_b"] > 0


def test_budget_is_borrowed_not_redeclared():
    """The target must come from skill-audit (which reads it from the owning gate),
    so there is never a second hand-maintained copy to drift."""
    assert HA.TARGET == HA.SA.TARGET
    assert HA.HARD == HA.SA.HARD


# --- regression: a list item's extent must stop at the next heading ---------------

CROSSES_HEADING = """# Handoff: thing — 2026-08-29

## Gotchas / decisions / dead-ends
- **A perfectly live gotcha** — the cache is per-node, and this is the last
  top-level bullet in this section.

### 🔴 The X finding was RAISED AND THEN REFUTED — do not re-derive it
""" + ("Narrative about the refutation. " * 60) + """

### Another subsection
""" + ("More narrative. " * 60) + """
"""


def test_bullet_extent_stops_at_the_next_heading(tmp_path):
    """RED before the fix: the last bullet in a section ran to the section END,
    swallowing every H3 after it, and the RETRACTED predicate then matched on the
    SWALLOWED text — booking the whole tail as a 'retracted bullet'.

    Measured on the real corpus before the fix: 16 of 237 retracted bullets crossed
    a heading, 10 of them (45,309 B) matched ONLY via swallowed content. Worst
    single case: 16,380 B booked for a bullet containing no retraction.
    """
    a = _doc(tmp_path, CROSSES_HEADING)
    assert a["retracted"] == [], "the live bullet must not absorb the REFUTED H3"
    assert a["retracted_b"] == 0


def test_a_genuinely_retracted_bullet_is_still_caught(tmp_path):
    """The positive control for the test above: clipping must not silence the
    detector, or the zero there would mean nothing."""
    a = _doc(tmp_path, """# Handoff: t — 2026-08-29

## Gotchas / decisions / dead-ends
- **Dead end (do not re-derive):** the cache theory was REFUTED.
- A live one.
""")
    assert len(a["retracted"]) == 1
    assert a["retracted_b"] > 0


def test_ranked_item_extent_stops_at_the_next_heading(tmp_path):
    """Same clip, the other walker: a done item must not absorb the H3 below it."""
    body = """# Handoff: t — 2026-08-29

## Next steps (ranked)
1. ✅ **DONE** — shipped it.

### A subsection that follows the list
""" + ("Narrative. " * 80)
    a = _doc(tmp_path, body)
    assert len(a["done"]) == 1
    n, s, e, b, _done = a["ranked"][0]
    assert b < 200, f"item extent swallowed the H3: {b} B"


# --- the advisory counter must be wired, in both directions -----------------------


def test_generic_lesson_counter_is_wired(tmp_path):
    """🔴 POSITIVE CONTROL. Round 1 shipped with NO test on this counter at all:
    replacing the whole regex with a never-matching literal left the suite fully
    green while the report printed `RELOCATE_DURABLE candidates: 0`. A zero from a
    dead matcher is indistinguishable from a zero from clean docs — and this is the
    number most likely to be quoted uncritically, because it is labelled advisory.
    """
    a = _doc(tmp_path, """# Handoff: t — 2026-08-29

## Gotchas / decisions / dead-ends
- zsh does not word-split an unquoted parameter, so the loop runs once.
- A fact about this initiative and nothing else.
""")
    assert a["generic"] == 1


def test_generic_lesson_counter_is_zero_on_an_initiative_only_doc(tmp_path):
    a = _doc(tmp_path, CLEAN)
    assert a["generic"] == 0


# --- the borrowed budget must not be re-spelled ------------------------------------


def test_budget_literals_do_not_appear_in_the_source():
    """🔴 test_budget_is_borrowed_not_redeclared checks the VALUES agree, which a
    hand-copied literal also satisfies — round 1 proved it: replacing
    `TARGET = SA.TARGET` with `12_288` left all 11 tests green. That test catches
    the eventual DRIFT once the owning gate moves, which is the real harm; this one
    catches the duplicate at the moment it is written, which is what its docstring
    actually promised.
    """
    src = (SCRIPTS / "handoff-audit.py").read_text()
    for lit in ("12_288", "12288", "40_960", "40960"):
        assert lit not in src, (
            f"{lit!r} is spelled in handoff-audit.py — the budget must come from "
            "skill-audit.py, which reads it from the gate that owns it"
        )


# --- the population a quoted total came from --------------------------------------


def test_resolve_targets_reports_what_each_root_contributed(tmp_path):
    """An existing-but-EMPTY root contributed silently before this: only a
    nonexistent path warned, and that warning goes to stderr where a redirect
    loses it."""
    full = tmp_path / "a" / "claudedocs"
    full.mkdir(parents=True)
    (full / "handoff-one.md").write_text("# H\n")
    empty = tmp_path / "b"
    empty.mkdir()
    docs, per_root = HA.resolve_targets([str(tmp_path / "a"), str(empty)])
    assert len(docs) == 1
    assert per_root == [(str(tmp_path / "a"), 1), (str(empty), 0)]


def test_resolve_targets_flags_a_nonexistent_root(tmp_path):
    docs, per_root = HA.resolve_targets([str(tmp_path / "nope")])
    assert docs == []
    assert per_root == [(str(tmp_path / "nope"), None)]
