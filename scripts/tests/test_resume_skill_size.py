"""Deterministic byte-size gate for `claude/skills/resume/SKILL.md`.

WHY THIS EXISTS
---------------
`/resume` is the skill that runs at the START of a session. Its body is paid
before any work happens, on essentially every session that re-enters an
initiative -- which makes it the mirror image of `/handoff` (paid at the end,
when the window is already tight) and gives it the same economics.

It had NO gate until this module, and it grew to **51,356 B** -- 4.2x the 12,288 B
skill-body target `scripts/browser-bridge/tests/test_skill_size.py` owns, and
10,396 B OVER the 40,960 B hard cap the `prune-skill` playbook calls "~10k tokens
before any work starts". One H2 (`Steps`) carried 50,742 B of it, and the skill
routed to NO `reference/` sidecar at all, which is why the body carried
everything.

🔴 THIS IS THE NINTH ENFORCED BYTE CEILING IN THE REPO AND THE SIXTH ON A SKILL
BODY -- it follows an established pattern, it did not invent one. The census,
RE-DERIVED 2026-09-17 (an earlier version of this header said "SIXTH ... FOURTH",
undercounting by three; see the method note below for how that happened):

    claude/RULES.md                         test_rules_size.py
    scripts/browser-bridge/SKILL.md         browser-bridge/tests/test_skill_size.py     [body]
    claude/skills/prune-skill/SKILL.md      test_prune_skill_size.py                    [body]
    claude/skills/session-manager/SKILL.md  test_session_manager_skill_size.py          [body]
    claude/skills/handoff/SKILL.md          test_handoff_skill_size.py                  [body]
    claude/skills/clawgate/SKILL.md         claude-hooks/tests/
                                              test_clawgate_task_interview_guard.py     [body]
    claude/skills/resume/SKILL.md           this module                                 [body]
    claudedocs/**/handoff-*.md              test_handoff_doc_size.py  (many files, not one)
    scripts/browser-bridge/reference/       browser-bridge/tests/
      validation-prompt.md                    test_validation_prompt.py  (a skill SIDECAR)

🔴 HOW THIS WAS DERIVED, BECAUSE THE PREVIOUS NUMBER WAS DERIVED BY COPYING.
CLAUDE.md gives a union grep -- `git grep -lE 'MIN_HEADROOM|st_size <=' -- scripts/`
-- and says in the same breath that it is NOT provably complete. Both halves bit:

  * the union grep returns 12 paths. READ each one: 8 are real doc ceilings and 4
    are over-matches (`dl-router/server.py` compares file sizes while deduping;
    `present/measure.py`, `skill-audit.py` and `test_skill_audit.py` READ another
    gate's constants rather than owning one). Counting the hits gives 12; counting
    the gates gives 8.
  * a NINTH is INVISIBLE to that grep: `test_validation_prompt.py` owns
    `MAX_DOC_BYTES` and asserts `size <= MAX_DOC_BYTES`, matching neither pattern.
    It was found by sweeping module-level `MAX_*BYTES` constants instead --
    a different, also-incomplete method. Two methods, two different misses.

  Each half alone is worse: `MIN_HEADROOM` alone finds 7 of the 8 (it misses
  clawgate); `st_size <=` alone finds 2 (clawgate, plus this module only because
  this very paragraph quotes the pattern) and misses six.

⚠ SO THE NUMBER ABOVE IS A MEASUREMENT WITH A KNOWN BLIND SPOT, NOT A CENSUS.
There is still no test enumerating ceilinged docs two-way, which is the only
thing that would stop this recurring. CLAUDE.md's bullet carries the same list
and was corrected in the same commit as this file; if you add a tenth, update it
there too -- and re-derive rather than incrementing, which is how the first
number here was wrong in both directions at once.

WHAT THE CEILING PROTECTS -- AND WHAT IT MUST NOT DO
----------------------------------------------------
🔴 This gate must NEVER be satisfied by deleting an instruction, and here that
hazard is unusually concrete: this skill's prose is pinned verbatim by tests
across at least ten modules, which `_pin_modules()` below derives rather than
lists. They assert that named strings the TOOLS emit -- `resume-state.sh`'s
COULD-NOT-MEASURE reasons, its digest block names, `handoff_search`'s exit-code
vocabulary, `subsystem_recall`'s scope statuses -- are documented somewhere a
reader of `/resume` will find them.

🔴 THOSE PINS AND THIS CEILING PULL IN OPPOSITE DIRECTIONS BY CONSTRUCTION, and
that tension is the point rather than a defect: the pins forbid deleting an
instruction, the ceiling forbids adding one without evicting. The only move that
satisfies both is the one the `prune-skill` playbook prescribes -- DEMOTE a block
to `claude/skills/resume/reference/<topic>.md` and leave a routing line. A pin
that reads the skill DIRECTORY (body + sidecars) is satisfied by a demotion; one
that reads only the body is not, and a maintainer under budget pressure who meets
it by trimming has lost an instruction.

The numbers below are the SINGLE source of truth. Any other mention of them
(CLAUDE.md, a handoff doc, a PR body) must cross-reference this module rather
than restate the literal -- a second hand-maintained copy of the number is
exactly how the drift regrows.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`.
"""
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_MD = REPO_ROOT / "claude" / "skills" / "resume" / "SKILL.md"
REFERENCE_DIR = SKILL_MD.parent / "reference"

# The hard ceiling: the resume skill body must never exceed this many bytes.
#
# 🔴 An ANTI-REGROWTH ratchet, not a target. Ratcheting it DOWN as content moves
# into reference/ is welcome and is the intended direction of travel. Ratcheting
# it UP requires saying in the commit message which instruction could not be
# expressed in the budget.
#
# ⚠ THE CURRENT SIZE IS DELIBERATELY NOT WRITTEN DOWN HERE. It is a derived
# measurement edited in the same commits as the thing it measures, and
# test_rules_size.py records three consecutive rounds where exactly that went
# stale within its own PR. The failure messages below PRINT current / ceiling /
# free / budget / RECLAIM; that is the authority.
#
# HOW IT WAS SIZED, in units of a REAL edit rather than picked round -- the same
# method test_rules_size.py, test_skill_size.py and test_handoff_skill_size.py
# each use. Measured over the pruned body: mean paragraph 492 B, median 399 B,
# p90 1,011 B; over its 26 🔴 rule lines, mean 430 B, p90 789 B. The ceiling
# leaves roughly two mean rules of usable slack above the size the prune landed
# at -- a working margin without making the gate decorative.
#
# 🔴 WHERE IT LANDED, AND WHY NOT AT THE 12,038 B SKILL BUDGET. The 2026-09-17
# prune moved 51,356 B of body into six `reference/` sidecars and hand-wrote the
# core: **51,356 -> 20,731 B, a 59.6% cut.** That is comfortably under the
# `prune-skill` playbook's 40,960 B hard cap and comfortably OVER its 12,288 B
# target, and the overshoot is deliberate rather than drift. The playbook's
# overshoot clause asks for the reason and the number in writing; this is it.
#
# THE BINDING CONSTRAINT IS NOT PROSE, IT IS THE PINS. Ten modules assert that
# literal strings this skill's TOOLS emit are documented in the body, and most of
# them scrape the expected set FROM the tool, so the ledger is two-way. Measured
# by running them against a 15,179 B draft: 13 tests red, every one of them a
# vocabulary the draft had demoted. What the body is therefore obliged to carry,
# with the module that obliges it:
#
#   - the 8 `clock` literals and 4 clawgate pins   test_resume_state_clawgate
#   - `**seven** reasons` + all 7 reason fragments test_resume_state_skill_freshness
#   - EXPIRED/UNDATED + both finding shapes        test_resume_state_investigations
#   - both `closing-condition:` kinds              test_resume_state_dod
#   - the whole step-4 wiring block, verbatim      test_resume_handoff_search_wiring
#   - the three `scope-*` statuses                 test_subsystem_recall
#   - two whole step-6 sentences + `gh pr list` x2 test_claim_work
#
# 🔴 SO THE HONEST STATEMENT IS: this body is at, or within ~2 KB of, the floor
# those pins impose -- NOT at the floor prose alone would allow. Getting to the
# 12 KB target needs the pins MOVED, not the prose trimmed. The repo already has
# the pattern and this prune used it once: `test_subsystem_recall.py` split
# `RESUME_SENTENCES` into a body table and a `RESUME_SENTENCES_REFERENCE` table
# pointing at `reference/cairn-recall.md`, with a reworded-pin positive control
# and a routing check. Repeating that for the clawgate, investigations and
# skill-freshness vocabularies is the route to a smaller number. It is a real
# change to real guards and was deliberately NOT bundled into the prune.
#
# 🔴 WHAT WOULD NOT FIT, which is what this ledger is for. Three things were held
# IN the body against budget pressure by judgement rather than by a pin, each an
# instruction a reader must have before any tool has run to print it:
#
#   a. the ORDERING imperative at step 2 -- run the reconciler BEFORE reading the
#      doc, because it is what decides which copy is authoritative. A reader who
#      routes to a sidecar to learn this has already read the wrong copy.
#   b. the `handoff_search` LEAK rule at step 4. The corpus spans four repos, two
#      of them client repos, and this repo is PUBLIC. The warning must sit with
#      the command, not a routing hop away: `prune-skill`'s measured failure mode
#      is "a warning demoted AWAY from the instruction it guards", where content
#      survives, paths resolve, every gate passes, and the PAIRING broke.
#   c. the empty-`DRIFT` reading rule at step 2. A gap block prints ALONGSIDE
#      real findings, so "no findings" is only an all-clear when nothing else is
#      beside it -- a reader who has not been told that reads a degraded run as a
#      clean one, and there is no later moment at which the digest says so again.
MAX_BYTES = 22_400

# Required working margin below the ceiling.
#
# Sized to one LARGE edit of this file, matching the sibling gates' reasoning:
# the p90 paragraph here is 1,011 B and the p90 🔴 rule 789 B, so a floor below
# ~800 B would fire at the same moment as the ceiling and deliver exactly the
# surprise it exists to prevent.
MIN_HEADROOM_BYTES = 800


def existing_topics_text(reference_dir: Path) -> str:
    """The reference topics that exist RIGHT NOW, read off the filesystem.

    Globbed rather than hard-coded for the reason test_skill_size.py gives: a
    hand-maintained list drifts, and a drifted list steers a maintainer into
    creating a duplicate topic for content that already has a home.

    Takes the directory as an argument so the empty-glob branch -- the one that
    tells a maintainer `reference/` moved -- can be driven by a planted control
    against a tmp dir instead of waiting for the real tree to lose its sidecars.
    """
    topics = sorted(p.stem for p in reference_dir.glob("*.md"))
    if not topics:
        return f"(none found under {reference_dir} -- did reference/ move?)"
    return ", ".join(topics)


def _existing_topics() -> str:
    return existing_topics_text(REFERENCE_DIR)


def pin_modules_text(modules: list[str]) -> str:
    """Render the pin-module block from an already-derived module list.

    Split from `_pin_modules()` so the nothing-found branch is reachable from a
    planted control: on a healthy tree the derivation never comes back empty, so
    that arm would otherwise be a guard nobody has watched execute.
    """
    if not modules:
        return (
            "NO module under scripts/ was found reading "
            "claude/skills/resume/SKILL.md -- either the pins were deleted or "
            "this derivation is broken. Do NOT read that as freedom to trim: "
            "re-check by hand before editing a single instruction."
        )
    return (
        "The skill's prose is pinned verbatim by tests in: "
        + ", ".join(modules)
        + ". Several derive their expected strings FROM the tool "
        "(resume-state.sh, handoff_search.py, subsystem_recall) and assert each "
        "is documented, so deleting an instruction here fails there -- but a "
        "rewrite that keeps the words and guts the reasoning around them passes. "
        "Read the step cold after editing it."
    )


def _pin_modules() -> list[str]:
    """Which test modules read this skill body, derived by reading them.

    Derived rather than restated for the reason test_handoff_skill_size.py gives
    about hand-maintained lists: the only copy a maintainer ever SEES is the one
    the playbook renders on failure, and a stale list there is worse than none --
    it tells someone under budget pressure that a phrase is unguarded when it is.
    """
    needle = "claude/skills/resume/SKILL.md"
    found: list[str] = []
    for path in sorted((REPO_ROOT / "scripts").rglob("test_*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable test module
            continue
        if needle in text:
            found.append(path.name)
    return found


def _pin_block() -> str:
    return pin_modules_text(_pin_modules())


def _eviction_playbook() -> str:
    return f"""
  How to fix -- do NOT delete or narrow an instruction to make this pass:

    🔴 {_pin_block()}

    What to move OUT (to claude/skills/resume/reference/<topic>.md):
      - dated measurements, incident narratives, byte/token counts, PR numbers
      - per-block output vocabularies the reader consults only when a block fires
      - superseded or retracted reasoning
      (existing topics: {_existing_topics()})
      Leave the imperative in the body with a `📖` pointer to the DEPLOYED path,
      `~/.claude/skills/resume/reference/<topic>.md` -- a bare `reference/x.md`
      resolves against the reader's cwd and is simply NOT FOUND.

    What STAYS in SKILL.md:
      - the imperative, its scope, and the procedure needed to comply
      - every command the executor has to run, with its flags
      - any warning that guards a command the body still carries. `prune-skill`'s
        measured loss mode is a warning demoted AWAY from what it protects: the
        content survives, the path resolves, every gate passes, and the PAIRING
        broke.

    The remaining levers, in order:
      1. move guidance INTO the tool -- resume-state.sh already prints its own
         remedies at the moment each applies, which costs nothing on the runs
         where it does not fire, unlike a paragraph paid on every run;
      2. demote a whole coherent block to reference/ with a routing line;
      3. raise MAX_BYTES, saying in the commit message which instruction could
         not be expressed in the budget. Legitimate, but it is third, not first.

    Ceiling + headroom constants live in scripts/tests/test_resume_skill_size.py.
"""


def _size() -> int:
    return len(SKILL_MD.read_bytes())


def test_skill_md_exists():
    """Guard the guard: a moved/renamed SKILL.md must not silently pass."""
    assert SKILL_MD.is_file(), (
        f"{SKILL_MD} not found -- the size gate below would be vacuous. "
        "If the resume skill moved, update SKILL_MD in this module."
    )


def test_eviction_playbook_lists_every_reference_topic():
    """Guard the guard, part 2: the playbook only renders on FAILURE, so a wrong
    topic list is invisible until the day someone needs it -- which is exactly
    how browser-bridge's drifted to 8 of 11. Exercise the derivation directly."""
    on_disk = sorted(p.stem for p in REFERENCE_DIR.glob("*.md"))
    assert on_disk, (
        f"no reference/*.md found under {REFERENCE_DIR} -- either the eviction "
        "pattern was abandoned or REFERENCE_DIR is wrong; either way the "
        "playbook below would name no destinations, and the only remaining way "
        "to satisfy MAX_BYTES would be to delete instructions."
    )
    playbook = _eviction_playbook()
    missing = [t for t in on_disk if t not in playbook]
    assert not missing, (
        f"the eviction playbook omits existing reference topics {missing}; a "
        "maintainer following it would create a duplicate topic. The list must "
        "stay derived from the filesystem, never hard-coded."
    )


def test_control_existing_topics_reports_an_empty_reference_dir(tmp_path):
    """Positive control for the `reference/ moved` arm of the topic renderer.

    On a healthy tree the glob is never empty, so this branch would otherwise be
    asserted to work and never watched to. Driven IN-PROCESS against an empty tmp
    dir; a subprocess would leave the branch just as unobserved.
    """
    empty = tmp_path / "reference"
    empty.mkdir()
    assert existing_topics_text(empty) == (
        f"(none found under {empty} -- did reference/ move?)"
    )
    # Negative control: the same helper does NOT say "none found" when a topic
    # exists, so the message above is a fact about the empty dir, not the code.
    (empty / "some-topic.md").write_text("x", encoding="utf-8")
    (empty / "another-topic.md").write_text("y", encoding="utf-8")
    assert existing_topics_text(empty) == "another-topic, some-topic"


def test_the_pin_module_derivation_finds_the_real_pins():
    """🔴 The playbook's central claim is that this file is HEAVILY pinned.

    If the derivation silently returned nothing, the playbook would render a
    sentence telling a maintainer the opposite -- at exactly the moment they are
    over budget and looking for something to cut. So assert it finds modules, and
    assert it finds ones known to exist rather than merely "some".
    """
    modules = _pin_modules()
    assert modules, (
        "no scripts/**/test_*.py was found reading claude/skills/resume/SKILL.md. "
        "Either every pin was deleted (a real finding -- say so) or the needle in "
        "_pin_modules() is wrong. Do not leave this derivation broken: the "
        "eviction playbook renders its result to someone under budget pressure."
    )
    # A representative pin whose own docstring says the skill must document what
    # the tool emits. Naming one concrete module makes this a claim about the
    # corpus rather than about the loop terminating.
    assert "test_resume_state_skill_freshness.py" in modules, modules


def test_control_pin_modules_reports_an_empty_derivation():
    """Positive control for the `NO module was found` arm.

    It fires only when the derivation comes back empty -- never true on a healthy
    tree. Driven by handing the renderer an empty list, which is exactly what
    `_pin_modules()` returns in that case.
    """
    empty = pin_modules_text([])
    assert "NO module under scripts/ was found" in empty
    # 🔴 And it must NOT read as permission to trim. A maintainer who hits this
    # arm is by construction the one with no pin protecting them.
    assert "Do NOT read that as freedom to trim" in empty
    # Negative control: a populated list renders the OTHER arm, so the branch
    # above is selected by the emptiness and nothing else. The names are
    # deliberately not the live ones -- a mutant hardcoding a real module dies.
    populated = pin_modules_text(["test_alpha.py", "test_beta.py"])
    assert "NO module under scripts/ was found" not in populated
    assert "test_alpha.py, test_beta.py" in populated


def test_resume_skill_under_hard_ceiling():
    size = _size()
    assert size <= MAX_BYTES, (
        f"\n\nclaude/skills/resume/SKILL.md is OVER its hard ceiling.\n"
        f"  current:  {size:,} bytes\n"
        f"  ceiling:  {MAX_BYTES:,} bytes\n"
        f"  OVER BY:  {size - MAX_BYTES:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_resume_skill_keeps_working_headroom():
    """The ceiling alone is not enough -- keep a margin to edit into.

    Fails in the MAX_BYTES-MIN_HEADROOM .. MAX_BYTES band (and above, where the
    ceiling test also fires with the overage), so "you are one rule from
    breaking it" arrives as a signal rather than as a surprise.
    """
    size = _size()
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"\n\nclaude/skills/resume/SKILL.md has no working headroom left.\n"
        f"  current:   {size:,} bytes\n"
        f"  ceiling:   {MAX_BYTES:,} bytes\n"
        f"  free:      {headroom:,} bytes  (minimum required: "
        f"{MIN_HEADROOM_BYTES:,})\n"
        f"  budget:    {MAX_BYTES - MIN_HEADROOM_BYTES:,} bytes "
        f"(ceiling minus the required margin)\n"
        f"  RECLAIM:   {MIN_HEADROOM_BYTES - headroom:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_the_gate_can_report_a_breach(tmp_path, monkeypatch):
    """🔴 NEGATIVE CONTROL, and it drives the REAL test functions.

    A ceiling test reads a file it never writes, so on a compliant tree nothing
    exercises its failure branch: `test_resume_skill_under_hard_ceiling` would
    pass identically if `_size()` returned a constant, if the comparison were
    inverted, or if SKILL_MD pointed at an empty file. `claude/RULES.md` calls
    that a harness that has not been shown it can go red.

    So: repoint the module at an oversized file and watch it fail with its own
    message, then at a tiny one and watch it pass (a gate that always fails is
    equally useless). The HEADROOM half has its own control below -- it must not
    be certified from this one; see that test's docstring for why.
    """
    fat = tmp_path / "fat.md"
    fat.write_bytes(b"x" * (MAX_BYTES + 1))
    monkeypatch.setattr("test_resume_skill_size.SKILL_MD", fat)

    with pytest.raises(AssertionError) as ceiling:
        test_resume_skill_under_hard_ceiling()
    assert "OVER BY:  1 bytes" in str(ceiling.value)

    # The playbook rendered into that failure must name a real destination -- a
    # maintainer over budget who is given no topic deletes text instead.
    assert "cairn-recall" in str(ceiling.value)

    # POSITIVE HALF: the same function passes on a compliant file, so the red
    # above is a fact about the size and not about the harness.
    thin = tmp_path / "thin.md"
    thin.write_bytes(b"x" * 10)
    monkeypatch.setattr("test_resume_skill_size.SKILL_MD", thin)
    test_resume_skill_under_hard_ceiling()
    test_resume_skill_keeps_working_headroom()


def test_the_headroom_half_fires_on_its_OWN_condition(tmp_path, monkeypatch):
    """🔴 The early-warning half needs a control the ceiling cannot satisfy.

    MEASURED on the sibling gate: a fixture of `MAX_BYTES + 1` makes `headroom`
    negative, which is over the CEILING -- so the headroom assertion fires for
    the ceiling's reason and would have fired at ANY floor value, including zero.
    That is `claude/RULES.md`'s "a mutant that removes a guard TOGETHER WITH ITS
    ENCLOSING CONDITION proves nothing about the guard, and dies for the wrong
    reason", and it left MIN_HEADROOM_BYTES certified by nothing there until it
    was found.

    So this control lands the size squarely INSIDE the warning band, where the
    ceiling is satisfied and only the floor can object:

        MAX_BYTES - MIN_HEADROOM_BYTES + 1  ->  headroom == MIN_HEADROOM_BYTES - 1

    and asserts BOTH directions -- the ceiling test PASSES while the headroom
    test FAILS.

    ⚠ WHAT THIS DOES AND DOES NOT PIN. It does NOT pin MIN_HEADROOM_BYTES to 800:
    the fixture is DERIVED from the constant and moves with it, so any positive
    value leaves this green. What it pins is the comparison's SHAPE -- a literal
    `>= 0`, a `>=` weakened to `>`, or an off-by-one in `headroom` each go red --
    plus the value 0, via the guard below. That is the right trade rather than a
    gap: a test pinning the constant would false-fail every legitimate ratchet,
    and ratcheting DOWN is the intended direction of travel here.
    """
    in_band = tmp_path / "in-band.md"
    in_band.write_bytes(b"x" * (MAX_BYTES - MIN_HEADROOM_BYTES + 1))
    monkeypatch.setattr("test_resume_skill_size.SKILL_MD", in_band)

    # 🔴 THE BAND MUST EXIST, asserted HERE with its own message. At
    # MIN_HEADROOM_BYTES = 0 the fixture above is MAX_BYTES + 1, i.e. over the
    # CEILING -- so the call below would raise for the ceiling's reason with the
    # ceiling's message, and a zero floor would pass unnoticed.
    assert MIN_HEADROOM_BYTES > 0, (
        "MIN_HEADROOM_BYTES is 0, so the warning band is EMPTY and "
        "test_resume_skill_keeps_working_headroom can never fire before the "
        "ceiling does -- the early-warning half of this gate is inert."
    )
    assert len(in_band.read_bytes()) <= MAX_BYTES, (
        "the in-band fixture is over the CEILING, so the assertion below would "
        "fire for the ceiling's reason and prove nothing about the floor"
    )

    # The ceiling is NOT breached -- this is the half that makes the assertion
    # below a statement about MIN_HEADROOM_BYTES rather than about MAX_BYTES.
    test_resume_skill_under_hard_ceiling()

    with pytest.raises(AssertionError) as headroom:
        test_resume_skill_keeps_working_headroom()
    msg = str(headroom.value)
    assert "RECLAIM:   1 bytes" in msg, msg
    assert "cairn-recall" in msg

    # And one byte the other way is clean, so the boundary is pinned on both
    # sides rather than "big enough fails".
    at_floor = tmp_path / "at-floor.md"
    at_floor.write_bytes(b"x" * (MAX_BYTES - MIN_HEADROOM_BYTES))
    monkeypatch.setattr("test_resume_skill_size.SKILL_MD", at_floor)
    test_resume_skill_under_hard_ceiling()
    test_resume_skill_keeps_working_headroom()


def test_the_gate_measures_BYTES_not_characters(tmp_path, monkeypatch):
    """🔴 Pin the UNIT, or the gate silently grants hundreds of bytes.

    MEASURED on the sibling gate: mutating `_size()` from `read_bytes()` to
    `read_text("utf-8")` left every test GREEN while granting the real file
    several hundred bytes of extra allowance. This file is far from pure ASCII --
    it is full of 🔴, ⚠ and em dashes -- so the spread here is larger, not
    smaller.

    The cause is `claude/RULES.md`'s fixture rule: every control above writes
    `b"x" * N`, pure ASCII, where bytes and characters are the same number. A
    fixture that CANNOT distinguish two implementations does not test between
    them. "Feed a value the constant cannot equal and watch the output move."

    So this one is multibyte, sized so the two readings land on OPPOSITE sides of
    the ceiling: over it in bytes, far under it in characters. A byte-reading
    `_size()` fails here; a character-reading one passes, and the
    `pytest.raises` turns that pass into a red test.
    """
    # "é" is 2 bytes, 1 character in UTF-8.
    chars = MAX_BYTES // 2 + 1
    multibyte = tmp_path / "multibyte.md"
    multibyte.write_text("é" * chars, encoding="utf-8")
    assert len(multibyte.read_bytes()) > MAX_BYTES, "fixture is not over in bytes"
    assert chars < MAX_BYTES, "fixture is not under in characters"

    monkeypatch.setattr("test_resume_skill_size.SKILL_MD", multibyte)
    with pytest.raises(AssertionError) as breach:
        test_resume_skill_under_hard_ceiling()
    # Name the unit in the failure, so a future reader of a red run is not left
    # inferring which number the gate meant.
    assert f"{len(multibyte.read_bytes()):,} bytes" in str(breach.value)
