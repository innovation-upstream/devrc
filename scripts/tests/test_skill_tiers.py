"""Gate on the SKILL-LISTING TIER LEDGER — `claude/skill-tiers.json`.

WHAT THE MECHANISM IS
---------------------
Every skill's `name` + `description` loads on EVERY session under a budget of 1%
of the context window, measured IN CHARACTERS. `skillOverrides` in
settings.json makes that cost per-skill opt-in: a skill set to `name-only` costs
`len(name) + 2` instead of `len(name) + 4 + len(desc)`, stays `/name`-invocable
and stays callable by the Skill tool. What it loses is the ROUTING PROSE that
makes it fire from a described symptom.

`claude/skill-tiers.json` is devrc's ledger of that call, `scripts/
sync-skill-tiers.py` applies it to a host, and `scripts/drift-check.sh` reports
a host that has drifted from it (rc 22). This module gates the ledger itself.

🔴 THE ONE PROPERTY THAT MAKES IT SCALE is the two-way pin below: every shipped
skill has exactly one ledger entry and every ledger entry names a shipped skill.
Without it the mechanism silently stops covering the tree the moment a skill is
added — a new skill would just keep a full description and nothing would say so,
which is precisely how a guard reads as coverage while providing none.

WHY THE COST FORMULA HERE IS NOT THE ONE IN test_skill_descriptions.py
---------------------------------------------------------------------
That module deliberately measures the SMALLER number, `len(name) + len(desc)`,
and its docstring says why it is left alone. What Claude Code actually charges,
decompiled from the shipped binary (`claudedocs/proposal-skill-listing-tiers.md`
section 1), is `len(name) + 4 + min(len(desc), 1536)` per entry plus one newline
between entries — so the older measure undercounts by exactly `5n - 1`. That
relationship is PINNED below rather than described, because it is the only thing
keeping the two numbers legible as "two measures of one listing" rather than as
a disagreement.

🔴 A RATIO WITHOUT ITS MODEL IS MEANINGLESS, so this module quotes none. The
budget is `floor(contextWindow * zx(model) * 0.01)` characters and `zx` is 4 for
the 14 models up to 4.6 and **3 for claude-opus-5 and newer** — 6,000 at 200k and
30,000 at 1M on this session's model, not 8,000 / 40,000. The ceiling below is a
RATCHET on the number devrc controls, not a gate on any budget.

WHY THE LEDGER IS DELIBERATELY CONSERVATIVE, AND WHY A TEST SAYS SO
-------------------------------------------------------------------
Measured 2026-08-24: the whole listing was 20,708 chars against a 30,000 budget
on claude-opus-5 @1M — 0.69x. NOTHING is being truncated today. A wide tier B
would trade real routing now for headroom ~1.8 months out, so the shipped tier B
is small and every entry carries a rationale. `test_the_split_stays_conservative`
pins that as a property, not as a paragraph nobody re-reads.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "tests"))

import skill_tiers  # noqa: E402

FACTS_READER = REPO_ROOT / "scripts" / "lib" / "skill_tier_facts.py"
SYNC_SCRIPT = REPO_ROOT / "scripts" / "sync-skill-tiers.py"

# Vacuity floor on the discovery itself. 37 entries were measured when this gate
# was written; every assertion here is otherwise satisfied by an empty tree.
MIN_SKILLS = 30

# --------------------------------------------------------------------------- #
# THE MEASUREMENT, PINNED
#
# 🔴 Every figure below is RECOMPUTED from the live tree by
# `test_the_quoted_measurements_match_the_live_tree`, which prints the exact
# replacement values when one drifts. They are quoted in the source so a reviewer
# can see the ceiling was set from a real measurement rather than chosen — and
# pinned so that audit trail cannot rot into a lie.
#
# This is deliberate friction: ADDING OR RETIRING A SKILL REDS THIS TEST, and the
# fix is to copy the printed numbers. That is the point. The three transcribed
# figures this replaced were each wrong within a day of being written — one was a
# 36-entry total quoted in a 37-entry tree, and one contradicted the number the
# same change reported to its reviewer.
# --------------------------------------------------------------------------- #
MEASURED_ENTRIES = 37
MEASURED_TIER_A_ENTRIES = 24
MEASURED_TIER_A_CHARS = 8_952
# devrc's whole listing under the ledger (tier A in full, tier B name-only).
MEASURED_UNDER_LEDGER_CHARS = 9_144
# ...and what the same 37 entries would cost with every skill tier A. The
# difference is what the ledger buys: 3,907 chars.
MEASURED_ALL_TIER_A_CHARS = 13_051

# 🔴 THE TIER-A RATCHET, in the REAL formula: the tier-A block cost
# `sum(len(name) + 4 + min(len(desc), 1536)) + (n - 1)`.
#
# The ceiling sits 254 chars above MEASURED_TIER_A_CHARS — less than the MEAN
# tier-A entry, which is 8,946 / 24 = 372.8.
#
# 🔴 READ WHAT THAT DOES AND DOES NOT BUY. A headroom below the mean bounds an
# AVERAGE entry. It does NOT stop every addition, and the difference is not
# academic:
#   * a new tier-A skill whose entry is under 254 chars lands with NO eviction,
#     and entries that small exist today — `handoff` is 205 and `audit-pr` 215;
#   * the sibling ceiling in `test_skill_descriptions.py` has ALREADY been walked
#     this way. `subsystem-index` arrived in #790 at 182 chars as that module
#     measures, slipped inside its ~250 of headroom with no eviction, and took
#     that headroom to 68.
# So the honest claim is: this ceiling forces the eviction conversation for a
# TYPICAL new skill, and a small one gets through until the accumulated slack
# runs out. It is a ratchet on regrowth, not a per-addition gate. Do not restate
# it as "a NEW skill cannot be added without an eviction" — that sentence claims
# coverage this code does not provide, which `claude/RULES.md` calls worse than
# no guard because it stops anyone looking.
#
# LOWER it when you cut; do NOT raise it to make a new description fit. Demoting
# one skill to tier B in claude/skill-tiers.json is a ONE-LINE edit and is the
# intended move. The playbook is printed by the failing assertion below.
TIER_A_CEILING_CHARS = 9_200

# 🔴 Skills that must NEVER be tier B, pinned as a RELATIONSHIP rather than left
# to review. Each one fires from a SYMPTOM Zach describes rather than from its own
# name, so name-only would silently delete the only thing that routes it:
#
#   dl-router   "downloads are landing in the wrong folder" — and it looks dead
#               to Claude Code's usage counter while running as a live systemd
#               service, which is exactly how it would get demoted by mistake.
#   browser     "look at the page I have open"
#   clickup     a pasted app.clickup.com link, with four confusable siblings
#   mailbox     "email someone on my behalf"
#   vetr-mailbox  the OTHER side of that prompt. Name-only, "email them on my
#               behalf" routes to `mailbox` and sends from a different account:
#               a mis-route that takes a WRONG ACTION, not one that degrades an
#               answer. That distinction is the ledger's stated tie-break.
#   session-manager  "is anything waiting on me"
#   obs-read    "is it actually zero or did my query miss"
#
# This is not a restatement of the ledger — it is the subset whose tier is a
# safety property, and it fails if any of them is ever flipped.
MUST_AUTO_FIRE = (
    "dl-router", "browser", "clickup", "mailbox", "vetr-mailbox",
    "session-manager", "obs-read",
)


@pytest.fixture(scope="module")
def ledger():
    return skill_tiers.load_ledger()


@pytest.fixture(scope="module")
def skills():
    return skill_tiers.shipped_skills()


# --------------------------------------------------------------------------- #
# The live tree
# --------------------------------------------------------------------------- #

def test_the_scan_finds_the_skills_at_all(skills):
    """POSITIVE CONTROL on discovery. Every other assertion in this module is a
    reassuring ZERO, and a zero from a scan that walked nothing is
    indistinguishable from a clean sweep."""
    assert len(skills) >= MIN_SKILLS, (
        f"only {len(skills)} shipped skills found, below the {MIN_SKILLS} "
        "vacuity floor. Every check here would otherwise pass over an "
        "almost-empty set. Fix the discovery path in scripts/lib/skill_tiers.py."
    )


def test_every_shipped_skill_has_exactly_one_ledger_entry(ledger, skills):
    """🔴 THE TWO-WAY PIN. The property that makes this mechanism scale.

    Fails when the set GROWS (a skill is added and nobody tiered it, so it
    silently keeps a full description) and when it SHRINKS (a ledger entry names
    a skill that was renamed or retired, so the override is dead config nobody
    can observe). Adding a skill is therefore a deliberate tiering decision, in
    the same commit, forever.
    """
    untiered, phantom = skill_tiers.reconcile(ledger, skills)
    assert not untiered and not phantom, (
        "claude/skill-tiers.json and the shipped skills disagree.\n"
        f"  shipped but NOT tiered (keeps a full description in silence): {untiered}\n"
        f"  tiered but NOT shipped (a dead override): {phantom}\n"
        "\nAdd or remove the entry in claude/skill-tiers.json, in THIS commit.\n"
        '  tier A: {"tier": "A"}                       — keeps its description\n'
        '  tier B: {"tier": "B", "why": "<one line>"}  — name-only\n'
        "Choose by whether the skill must AUTO-FIRE from a symptom Zach "
        "describes, never by how often it has been invoked: `dl-router` runs as "
        "a live systemd service and `adoption-scan` has 20,494 tool-invocation "
        "events, and Claude Code's counter reports 0 skill invocations for both."
    )


def test_the_two_discovery_paths_agree(skills):
    """🔴 SEAM GUARD. Two modules find the listing entries independently.

    `skill_tiers.skill_md_paths` DERIVES the out-of-tree skills from
    nix/home.nix; `test_skill_descriptions.OUT_OF_TREE_SKILLS` is a hand-kept
    list of the same files (which fell a whole entry behind once, leaving
    `opencode` unmeasured while every check stayed green). Each is verified
    against nix/home.nix on its own; nothing checked them against EACH OTHER, and
    a defect in the seam is invisible to both.
    """
    import test_skill_descriptions as tsd
    other = {name for _, name, _ in tsd._entries()}
    assert set(skills) == other, (
        "the two discovery paths disagree about which skills are listing "
        "entries.\n"
        f"  only skill_tiers sees: {sorted(set(skills) - other)}\n"
        f"  only test_skill_descriptions sees: {sorted(other - set(skills))}\n"
        "One of them is measuring less than it claims. Fix the one that is "
        "wrong — do not relax this."
    )


def test_every_tier_b_entry_carries_a_rationale(ledger):
    """A tier-B call is a routing decision someone has to be able to audit.

    `load_ledger` refuses a rationale-less tier-B entry, so this asserts the
    LIVE ledger reaches that bar rather than that the parser exists.
    """
    thin = sorted(n for n in skill_tiers.tier_b_names(ledger)
                  if len(str(ledger[n]["why"]).strip()) < 20)
    assert not thin, (
        f"these tier-B entries have a `why` too short to audit: {thin}. Say what "
        "Zach types to reach the skill, or why no symptom routes to it."
    )
    assert all("why" not in ledger[n] for n in skill_tiers.tier_a_names(ledger)), (
        "a tier-A entry carries a `why`. A is the DEFAULT — a rationale for a "
        "default is noise that makes the tier-B rationales harder to find."
    )


def test_only_tier_b_gets_an_override_and_it_is_name_only(ledger):
    """🔴 The projection is `name-only`, never `off` or `user-invocable-only`.

    Those two REMOVE the skill from the model's reach. Tiering is a routing
    decision and must never become a disabling one — if a skill should be off,
    delete it. Tier A is written as ABSENCE rather than `"on"`: `on` is already
    the default, so emitting it would add a line per skill that says nothing.
    """
    want = skill_tiers.expected_overrides(ledger)
    assert set(want) == set(skill_tiers.tier_b_names(ledger))
    assert set(want.values()) == {"name-only"}, sorted(set(want.values()))
    assert "name-only" in skill_tiers.VALID_OVERRIDE_VALUES
    for name in skill_tiers.tier_a_names(ledger):
        assert name not in want, f"tier-A skill {name} got an override"


def test_the_split_stays_conservative(ledger, skills):
    """🔴 The ledger shipped SMALL on purpose, and that is a property, not prose.

    Nothing is being truncated today (measured: 20,708 chars against a 30,000
    budget on claude-opus-5 @1M). A wide tier B would delete real routing now for
    headroom ~1.8 months out. If a future change wants a majority of skills
    name-only, that is a deliberate decision and this assertion is where it gets
    argued — not a thing that happens one entry at a time.
    """
    b = skill_tiers.tier_b_names(ledger)
    assert len(b) * 2 < len(skills), (
        f"{len(b)} of {len(skills)} skills are tier B — a majority-name-only "
        "listing. That is a strategy change, not an increment: argue it here, "
        "and re-read the runway argument in "
        "claudedocs/proposal-skill-listing-tiers.md section 3 first."
    )


@pytest.mark.parametrize("name", MUST_AUTO_FIRE)
def test_the_symptom_routed_skills_stay_tier_a(ledger, skills, name):
    """Each of these fires from something Zach DESCRIBES, not from its own name.
    See MUST_AUTO_FIRE for the per-skill reason."""
    assert name in skills, f"{name} is not a shipped skill any more"
    assert skill_tiers.tier_of(ledger, name) == "A", (
        f"`{name}` was demoted to tier B. It routes from a SYMPTOM, not from its "
        "name, so name-only deletes the only thing that reaches it. See "
        "MUST_AUTO_FIRE in this module for why this one is on the list."
    )


def test_the_quoted_measurements_match_the_live_tree(ledger, skills):
    """🔴 THE FIGURES IN THIS MODULE'S SOURCE ARE PINNED, NOT TRANSCRIBED.

    A ceiling is only auditable if the reader can see what it was set from, so the
    measurement is quoted at the top of this file. A quoted number nobody can
    re-derive is exactly how the previous version shipped THREE wrong figures in
    one comment — a 36-entry total quoted in a 37-entry tree, and a saving that
    contradicted the number the same change reported to its reviewer.

    This asserts each against the live computation and prints the replacements, in
    the idiom `run-tests.sh` uses for its floors: resolve a drift by copying what
    the failure says, never by arithmetic.
    """
    live = {
        "MEASURED_ENTRIES": len(skills),
        "MEASURED_TIER_A_ENTRIES": len(skill_tiers.tier_a_names(ledger)),
        "MEASURED_TIER_A_CHARS": skill_tiers.tier_a_chars(ledger, skills),
        "MEASURED_UNDER_LEDGER_CHARS": skill_tiers.devrc_listing_chars(ledger, skills),
        "MEASURED_ALL_TIER_A_CHARS": skill_tiers.devrc_listing_chars(
            {name: {"tier": "A"} for name in skills}, skills),
    }
    quoted = {
        "MEASURED_ENTRIES": MEASURED_ENTRIES,
        "MEASURED_TIER_A_ENTRIES": MEASURED_TIER_A_ENTRIES,
        "MEASURED_TIER_A_CHARS": MEASURED_TIER_A_CHARS,
        "MEASURED_UNDER_LEDGER_CHARS": MEASURED_UNDER_LEDGER_CHARS,
        "MEASURED_ALL_TIER_A_CHARS": MEASURED_ALL_TIER_A_CHARS,
    }
    assert quoted == live, (
        "the measurements quoted at the top of this module no longer describe the "
        "tree. Copy these values in — do NOT recompute them by hand, and do not "
        "adjust TIER_A_CEILING_CHARS to match unless you are deliberately "
        "re-pinning the ratchet (raising it pays the saving back):\n"
        + "\n".join(f"    {k} = {v:_}" for k, v in live.items())
        + f"\n  the ledger saves {live['MEASURED_ALL_TIER_A_CHARS'] - live['MEASURED_UNDER_LEDGER_CHARS']:,} "
        f"chars across {live['MEASURED_ENTRIES']} entries; the mean tier-A entry is "
        f"{live['MEASURED_TIER_A_CHARS'] / live['MEASURED_TIER_A_ENTRIES']:.1f}, "
        f"and TIER_A_CEILING_CHARS leaves "
        f"{TIER_A_CEILING_CHARS - live['MEASURED_TIER_A_CHARS']} of headroom.\n"
        "  🔴 Headroom below the mean bounds an AVERAGE entry, not every entry — "
        "see the comment on TIER_A_CEILING_CHARS before claiming otherwise."
    )


def test_the_tier_a_cost_does_not_regrow_past_its_ratchet(ledger, skills):
    """🔴 THE RATCHET, in the REAL charging formula.

    The per-entry cap that `test_skill_descriptions.py` gates is a PER-ENTRY
    limit; nothing there measures what tier A costs as a block, and tier A is the
    part of the listing devrc still pays for on every session.
    """
    total = skill_tiers.tier_a_chars(ledger, skills)
    a = skill_tiers.tier_a_names(ledger)
    assert total <= TIER_A_CEILING_CHARS, (
        f"tier A costs {total:,} chars across {len(a)} entries, over the "
        f"{TIER_A_CEILING_CHARS:,}-char ratchet by {total - TIER_A_CEILING_CHARS:,}.\n"
        "This loads on EVERY session and overflows SILENTLY — Claude Code drops "
        "descriptions starting with the skills invoked least, taking their "
        "trigger keywords with them, with no error.\n"
        "\nPLAYBOOK — do these in order, in THIS commit:\n"
        "  1. DEMOTE a tier-A skill that Zach reaches for BY NAME. One line in "
        "claude/skill-tiers.json: {\"tier\": \"B\", \"why\": \"…\"}. This is the "
        "move the mechanism exists for and it costs ~350 chars each.\n"
        "  2. Cut mechanism prose out of the costliest descriptions below. A "
        "description is ROUTING SURFACE; how it works belongs in the body, which "
        "costs 0 until the skill is invoked.\n"
        "  3. Do NOT drop a trigger phrase or a disambiguation clause to fit — "
        "that trades a silent overflow for a silent mis-route.\n"
        "  4. Do NOT raise TIER_A_CEILING_CHARS. Raising it pays the cost back "
        "and is what this constant exists to prevent.\n"
        "\ncostliest tier-A entries now:\n"
        + "\n".join(f"  {n:26} {c:,} chars"
                    for n, c in skill_tiers.costliest(ledger, skills))
    )


def test_the_ledger_json_is_readable_by_a_human_reviewer():
    """The file is data, but it is also the artefact a reviewer reads. Its `_doc`
    must survive, because everything explaining the tie-break lives there."""
    data = json.loads(skill_tiers.LEDGER_PATH.read_text(encoding="utf-8"))
    doc = data.get("_doc")
    assert isinstance(doc, list) and len(" ".join(doc)) > 400, (
        "claude/skill-tiers.json lost its `_doc`. That block carries the "
        "auto-fire question, the mis-route tie-break and the measurement that "
        "makes the conservative split defensible."
    )


# --------------------------------------------------------------------------- #
# The FACTS READER — the projection drift-check.sh consumes
# --------------------------------------------------------------------------- #

def _reader(*args):
    return subprocess.run([sys.executable, str(FACTS_READER), *args],
                          capture_output=True, text=True)


def test_the_facts_reader_projects_the_live_ledger(ledger):
    proc = _reader()
    assert proc.returncode == 0, proc.stderr
    tokens = proc.stdout.split()
    assert tokens[0] == "ok"
    assert set(tokens[1:]) == {f"{n}=name-only"
                               for n in skill_tiers.tier_b_names(ledger)}


@pytest.mark.parametrize("body,expect", [
    (None, "err ledger-absent"),
    ("{ not json", "err ledger-malformed"),
    ('{"skills": {}}', "err ledger-malformed"),
    ('{"skills": {"x": {"tier": "Q"}}}', "err ledger-malformed"),
    ('{"skills": {"x": {"tier": "B"}}}', "err ledger-malformed"),
])
def test_control_the_facts_reader_errs_rather_than_projecting_nothing(
        tmp_path, body, expect):
    """🔴 NEGATIVE CONTROL, and the one that matters most for the drift arm.

    An empty expectation makes EVERY host look compliant, in silence. So each way
    the ledger can be unusable must produce `err <token>` and a non-zero exit —
    never a bare `ok` with no names. The fixtures are the real failure shapes: a
    missing file, unparseable JSON, an empty ledger, a bad tier, and a tier-B
    entry with no rationale.
    """
    path = tmp_path / "ledger.json"
    if body is not None:
        path.write_text(body, encoding="utf-8")
    proc = _reader(str(path))
    assert proc.returncode == 1, proc.stdout
    assert proc.stderr.strip() == expect, proc.stderr
    assert proc.stdout.strip() == "", (
        "the reader printed an expectation while reporting an error — a "
        "consumer that reads stdout would treat it as authoritative"
    )


def test_control_the_facts_reader_can_project_a_fixture(tmp_path):
    """POSITIVE CONTROL for the same path: the reader is shown to emit a NON-EMPTY
    projection from a ledger it has never seen, so the `err` results above are
    not simply "it can never succeed"."""
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"skills": {
        "aaa": {"tier": "B", "why": "a fixture rationale long enough to pass"},
        "bbb": {"tier": "A"},
        "ccc": {"tier": "B", "why": "a second fixture rationale, also long"},
    }}), encoding="utf-8")
    proc = _reader(str(path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["ok", "aaa=name-only", "ccc=name-only"]


# --------------------------------------------------------------------------- #
# CONTROLS on the two-way pin and the cost model
# --------------------------------------------------------------------------- #

def test_control_the_two_way_pin_detects_an_untiered_skill(ledger, skills):
    """POSITIVE CONTROL, direction 1. Isolated mutation: ONE extra shipped skill,
    the ledger untouched."""
    mutated = dict(skills)
    mutated["a-brand-new-skill"] = ("claude/skills/a-brand-new-skill/SKILL.md", "x")
    untiered, phantom = skill_tiers.reconcile(ledger, mutated)
    assert untiered == ["a-brand-new-skill"], untiered
    assert phantom == [], phantom


def test_control_the_two_way_pin_detects_a_ledger_entry_pointing_at_nothing(
        ledger, skills):
    """POSITIVE CONTROL, direction 2. Isolated the other way: ONE extra ledger
    entry, the tree untouched. Both directions are needed — a pin that only
    catches growth keeps passing while a rename leaves a dead override behind."""
    mutated = dict(ledger)
    mutated["a-retired-skill"] = {"tier": "B", "why": "a fixture rationale here"}
    untiered, phantom = skill_tiers.reconcile(mutated, skills)
    assert phantom == ["a-retired-skill"], phantom
    assert untiered == [], untiered


def test_control_the_tier_a_ratchet_can_go_red(ledger, skills):
    """🔴 NEGATIVE CONTROL, built from the REAL tree plus ONE realistically-sized
    new tier-A skill — not a synthetic wall of text — because "someone adds a
    skill" is exactly how this ceiling gets breached.

    It doubles as the headroom check: if one MEAN-SIZED entry does not breach it,
    the ratchet has been left slack enough to absorb a typical whole skill
    unnoticed.

    🔴 IT PROVES NOTHING ABOUT A SMALL ENTRY, and that gap is real rather than
    theoretical — a tier-A skill under the current 254 chars of headroom lands
    with no eviction, which is exactly how the sibling ceiling in
    `test_skill_descriptions.py` went from ~250 of headroom to 68 when
    `subsystem-index` arrived. The fixture is the mean deliberately: a control
    built from the smallest possible entry would grade a property this ceiling
    does not have.
    """
    live = skill_tiers.tier_a_chars(ledger, skills)
    assert live <= TIER_A_CEILING_CHARS, "live tree already red"

    n_a = len(skill_tiers.tier_a_names(ledger))
    mean = live // n_a
    name = "a-new-skill"
    mutated_skills = dict(skills)
    mutated_skills[name] = ("fixture/SKILL.md", "x" * (mean - len(name) - 4))
    mutated_ledger = dict(ledger)
    mutated_ledger[name] = {"tier": "A"}
    assert skill_tiers.entry_chars(name, mutated_skills[name][1]) == mean
    grown = skill_tiers.tier_a_chars(mutated_ledger, mutated_skills)
    assert grown > TIER_A_CEILING_CHARS, (
        f"the ceiling has {TIER_A_CEILING_CHARS - live} chars of headroom, "
        f"enough to absorb a whole {mean}-char tier-A entry unnoticed. "
        "Re-tighten TIER_A_CEILING_CHARS to the current measurement plus less "
        "than one average entry."
    )


def test_control_demoting_one_skill_moves_the_number(ledger, skills):
    """🔴 ISOLATED MUTATION on the tier itself, and the claim the whole mechanism
    rests on: flipping ONE entry to tier B must drop the tier-A cost by that
    entry's full description.

    Without this, a `tier_a_chars` that ignored the tier and summed every skill
    would satisfy every other assertion here.
    """
    victim = "clickup"
    assert skill_tiers.tier_of(ledger, victim) == "A"
    before = skill_tiers.tier_a_chars(ledger, skills)
    mutated = dict(ledger)
    mutated[victim] = {"tier": "B", "why": "a fixture rationale, long enough"}
    after = skill_tiers.tier_a_chars(mutated, skills)
    # One entry leaves the block, so its cost AND its separator go with it.
    expected = before - skill_tiers.entry_chars(victim, skills[victim][1]) - 1
    assert after == expected, (before, after, expected)
    # ...and it reappears in the tier-B block at the name-only price.
    assert (skill_tiers.tier_b_chars(mutated, skills)
            - skill_tiers.tier_b_chars(ledger, skills)
            == skill_tiers.name_only_chars(victim) + 1)


def test_the_real_formula_exceeds_the_older_gate_measure_by_5n_minus_1(skills):
    """🔴 PINS THE RELATIONSHIP between the two measures of one listing.

    `test_skill_descriptions.listing_total_chars` sums `len(name) + len(desc)`;
    the real charge adds 4 per entry plus one separator between entries. The
    difference is therefore exactly `5n - 1`, and that identity is what makes the
    two numbers legible as two measures rather than as a disagreement. It holds
    only while no description exceeds the per-entry cap, which is asserted first
    rather than assumed.
    """
    import test_skill_descriptions as tsd
    entries = tsd._entries()
    assert all(len(d) <= skill_tiers.PER_ENTRY_CAP_CHARS for _, _, d in entries)
    n = len(entries)
    all_tier_a = {name: {"tier": "A"} for name in skills}
    real = skill_tiers.devrc_listing_chars(all_tier_a, skills)
    assert real - tsd.listing_total_chars(entries) == 5 * n - 1, (
        f"the two measures differ by {real - tsd.listing_total_chars(entries)}, "
        f"not the {5 * n - 1} the decompiled formula predicts at {n} entries. "
        "One of them has changed shape."
    )


def test_control_entry_chars_caps_at_the_per_entry_cap():
    """Boundary, both sides. A description AT the cap is charged in full; one
    over it is charged the cap, because upstream truncates rather than growing."""
    cap = skill_tiers.PER_ENTRY_CAP_CHARS
    assert skill_tiers.entry_chars("ab", "x" * cap) == 2 + 4 + cap
    assert skill_tiers.entry_chars("ab", "x" * (cap + 5000)) == 2 + 4 + cap
    assert skill_tiers.entry_chars("ab", "x" * (cap - 1)) == 2 + 4 + cap - 1


def test_control_the_block_costs_are_sums_not_maxima():
    """ISOLATED mutation guard. Fixture values are pairwise distinct AND distinct
    from every constant this module names (1536, 9200, 4, 2), so a mutant that
    returns `max(...)`, the entry count, or a hardcoded constant lands on a
    different answer. The bounds deliberately overshoot rather than sitting on a
    multiple of the +4/+2 step."""
    fx_skills = {
        "aa": ("a/SKILL.md", "x" * 301),
        "bbb": ("b/SKILL.md", "x" * 503),
        "cccc": ("c/SKILL.md", "x" * 707),
    }
    a_only = {"aa": {"tier": "A"}, "bbb": {"tier": "A"}, "cccc": {"tier": "A"}}
    # 2+4+301 + 3+4+503 + 4+4+707 = 307 + 510 + 719 = 1536... deliberately NOT:
    assert skill_tiers.entry_chars("aa", fx_skills["aa"][1]) == 307
    assert skill_tiers.entry_chars("bbb", fx_skills["bbb"][1]) == 510
    assert skill_tiers.entry_chars("cccc", fx_skills["cccc"][1]) == 715
    assert skill_tiers.tier_a_chars(a_only, fx_skills) == 307 + 510 + 715 + 2

    b_only = {n: {"tier": "B", "why": "fixture rationale long enough here"}
              for n in fx_skills}
    assert skill_tiers.tier_b_chars(b_only, fx_skills) == 4 + 5 + 6 + 2
    assert skill_tiers.tier_a_chars(b_only, fx_skills) == 0
    assert skill_tiers.tier_b_chars(a_only, fx_skills) == 0


def test_control_a_malformed_ledger_is_refused_in_process(tmp_path):
    """The parser's own negative controls, graded through `load_ledger` so a
    mutation there moves a verdict here."""
    def w(obj):
        p = tmp_path / "l.json"
        p.write_text(json.dumps(obj), encoding="utf-8")
        return p

    with pytest.raises(ValueError, match="skills"):
        skill_tiers.load_ledger(w({"nope": {}}))
    with pytest.raises(ValueError, match="EMPTY"):
        skill_tiers.load_ledger(w({"skills": {}}))
    with pytest.raises(ValueError, match="tier"):
        skill_tiers.load_ledger(w({"skills": {"x": {"tier": "C"}}}))
    with pytest.raises(ValueError, match="why"):
        skill_tiers.load_ledger(w({"skills": {"x": {"tier": "B"}}}))
    with pytest.raises(ValueError, match="not an object"):
        skill_tiers.load_ledger(w({"skills": {"x": "B"}}))
    # ...and a well-formed one is accepted, so the above are not vacuous.
    ok = skill_tiers.load_ledger(w({"skills": {
        "x": {"tier": "B", "why": "a rationale long enough to be auditable"},
        "y": {"tier": "A"},
    }}))
    assert skill_tiers.tier_b_names(ok) == ["x"]


# --------------------------------------------------------------------------- #
# scripts/sync-skill-tiers.py
# --------------------------------------------------------------------------- #

def _sync_module():
    loader = importlib.machinery.SourceFileLoader("_sync_skill_tiers",
                                                  str(SYNC_SCRIPT))
    spec = importlib.util.spec_from_loader("_sync_skill_tiers", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


sync = _sync_module()


@pytest.fixture(autouse=True)
def _no_reading_the_operators_machine(tmp_path, monkeypatch):
    """🔴 HERMETICITY SEAM. `main()` consults `~/.claude.json` (the operator's real
    project list) and `~/.claude/plugins` by default. Left alone, these tests
    would walk the operator's own checkouts — and the control that proves the
    project scan CAN see an overriding entry would be measuring their machine
    rather than its own fixture. Both are pointed at paths that do not exist;
    the plugin scan is exercised against a fixture tree of its own below.
    """
    monkeypatch.setattr(sync, "CLAUDE_JSON", tmp_path / "no-claude.json")
    monkeypatch.setattr(sync, "PLUGINS_DIR", tmp_path / "no-plugins")


def test_plugin_skill_names_reads_a_plugin_tree(tmp_path, monkeypatch):
    """POSITIVE CONTROL for the dead-config warning. The resolver hard-returns
    `"on"` for a plugin skill, so a ledger entry naming one would never take
    effect — the scan that notices has to be shown able to see a plugin skill at
    all, or its silence means nothing."""
    monkeypatch.setattr(sync, "PLUGINS_DIR", tmp_path)
    p = tmp_path / "marketplace" / "somepack" / "skills" / "dataviz"
    p.mkdir(parents=True)
    (p / "SKILL.md").write_text("---\nname: dataviz\n---\n", encoding="utf-8")
    assert sync.plugin_skill_names() == {"dataviz"}
    monkeypatch.setattr(sync, "PLUGINS_DIR", tmp_path / "gone")
    assert sync.plugin_skill_names() == set()


def _settings(tmp_path, body):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return p


def _run_sync(path, tmp_path, *extra):
    """Never without --settings and --project-root. The default settings path is
    the OPERATOR'S LIVE FILE, and the default project root is their real
    workspace; a test that omits either is a test that writes to, or walks, the
    machine it is running on."""
    empty = tmp_path / "no-projects"
    empty.mkdir(exist_ok=True)
    return sync.main(["--settings", str(path), "--project-root", str(empty), *extra])


def test_the_sync_script_defaults_to_dry_run(tmp_path, capsys):
    """🔴 THE SAFETY PROPERTY. Applying the ledger is an operator act on a
    per-host, unmanaged file — it must never be a side effect of running the
    tool. Graded on the FILE BYTES, not on the absence of a message."""
    path = _settings(tmp_path, {"theme": "dark"})
    before = path.read_bytes()
    assert _run_sync(path, tmp_path) == 0
    assert path.read_bytes() == before, "a default run WROTE to settings.json"
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "--apply" in out, out


def test_the_sync_script_writes_only_under_apply(tmp_path, ledger, capsys):
    path = _settings(tmp_path, {"theme": "dark", "permissions": {"allow": ["Bash(ls:*)"]}})
    assert _run_sync(path, tmp_path, "--apply") == 0
    capsys.readouterr()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["skillOverrides"] == skill_tiers.expected_overrides(ledger)
    # Every other key survives byte-for-byte in value.
    assert data["theme"] == "dark"
    assert data["permissions"] == {"allow": ["Bash(ls:*)"]}
    # ...and it is idempotent.
    assert _run_sync(path, tmp_path, "--apply") == 0
    assert "already in sync" in capsys.readouterr().out


def test_the_sync_script_merges_into_an_existing_key_rather_than_clobbering(
        tmp_path, ledger, capsys):
    """A host may already carry overrides this ledger says nothing about —
    a hand-set `off` on a bundled skill, say. Wiping the key would silently
    re-enable it."""
    path = _settings(tmp_path, {"skillOverrides": {"dataviz": "off"}})
    assert _run_sync(path, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["skillOverrides"]["dataviz"] == "off", "an untiered entry was lost"
    for name in skill_tiers.tier_b_names(ledger):
        assert data["skillOverrides"][name] == "name-only"
    assert "not tiered by the ledger, untouched" in out, out


def test_the_sync_script_leaves_a_conflicting_value_alone_unless_forced(
        tmp_path, ledger, capsys):
    """A hand-set value for a TIERED skill is somebody's decision. Quietly
    promoting an `off` to `name-only` would re-enable a skill they hid."""
    victim = skill_tiers.tier_b_names(ledger)[0]
    path = _settings(tmp_path, {"skillOverrides": {victim: "off"}})
    assert _run_sync(path, tmp_path, "--apply") == 0
    out = capsys.readouterr().out
    assert json.loads(path.read_text())["skillOverrides"][victim] == "off", out
    assert "LEAVING ALONE" in out, out

    assert _run_sync(path, tmp_path, "--apply", "--force-value") == 0
    out = capsys.readouterr().out
    assert json.loads(path.read_text())["skillOverrides"][victim] == "name-only"
    assert "OVERWRITING" in out, out


def test_the_sync_script_refuses_a_settings_file_it_cannot_understand(tmp_path):
    bad = tmp_path / "settings.json"
    bad.write_text("{ not json", encoding="utf-8")
    assert _run_sync(bad, tmp_path) == 4
    bad.write_text(json.dumps({"skillOverrides": ["a", "list"]}), encoding="utf-8")
    assert _run_sync(bad, tmp_path) == 4
    assert _run_sync(tmp_path / "absent.json", tmp_path) == 3


def test_the_sync_script_refuses_when_the_ledger_and_the_tree_disagree(
        tmp_path, monkeypatch, ledger):
    """🔴 NEGATIVE CONTROL on the refusal. Two-way, and each direction on its own.

    Isolated mutation: only the ledger moves; the tree, the settings file and the
    flags are untouched, so the exit 2 can only come from the reconciliation.
    """
    path = _settings(tmp_path, {"theme": "dark"})
    before = path.read_bytes()

    phantom = dict(ledger)
    phantom["not-a-real-skill"] = {"tier": "B", "why": "a fixture rationale here"}
    monkeypatch.setattr(sync.skill_tiers, "load_ledger", lambda *a, **k: phantom)
    assert _run_sync(path, tmp_path, "--apply") == 2
    assert path.read_bytes() == before, "it wrote despite refusing"

    short = {n: e for n, e in ledger.items() if n != "clickup"}
    monkeypatch.setattr(sync.skill_tiers, "load_ledger", lambda *a, **k: short)
    assert _run_sync(path, tmp_path, "--apply") == 2
    assert path.read_bytes() == before

    # ...and with the real ledger restored it proceeds, so the two refusals above
    # are not simply "this fixture never works".
    monkeypatch.undo()
    assert _run_sync(path, tmp_path, "--apply") == 0


def test_control_the_project_scope_scan_can_actually_see_an_override(tmp_path):
    """🔴 POSITIVE CONTROL, and the reason the live run's reassuring zero is
    quotable at all.

    `~/.claude/settings.json` is the LOWEST-precedence ordinary scope: settings
    merge user -> project -> local, later wins, per-key. A `skillOverrides` entry
    in any project's own settings BEATS the ledger there, silently. The scan that
    looks for those reports a count, and a count from a scan wired to nothing is
    indistinguishable from a clean fleet — so it is shown here finding one.
    """
    root = tmp_path / "workspace"
    proj = root / "some-repo" / ".claude"
    proj.mkdir(parents=True)
    (proj / "settings.json").write_text(
        json.dumps({"skillOverrides": {"prune-index": "off"}}), encoding="utf-8")
    (proj / "settings.local.json").write_text(
        json.dumps({"skillOverrides": {"sglang": "on"}}), encoding="utf-8")
    # A project with no overrides at all, so "files read" exceeds "hits".
    quiet = root / "quiet-repo" / ".claude"
    quiet.mkdir(parents=True)
    (quiet / "settings.json").write_text(json.dumps({"theme": "dark"}),
                                         encoding="utf-8")

    hits, read = sync.overriding_scopes({"prune-index", "sglang"}, [root])
    assert read == 3, f"the scan opened {read} files, not the 3 it was given"
    assert sorted((p.name, n, v) for p, n, v in hits) == [
        ("settings.json", "prune-index", "off"),
        ("settings.local.json", "sglang", "on"),
    ], hits

    # NEGATIVE half of the same control: a name the ledger does not tier is not
    # reported, so the scan is matching the ledger rather than any override.
    hits2, read2 = sync.overriding_scopes({"handoff"}, [root])
    assert read2 == 3 and hits2 == [], hits2


def test_the_project_scan_survives_a_directory_it_cannot_stat(tmp_path):
    """🔴 RED-AT-BASE, kept as a test rather than as a line in a commit message.

    The FIRST live run of this script died with a `PermissionError` traceback
    from `Path.is_dir()` — `/tmp` holds root-owned mode-700
    `systemd-private-*` directories, and the scan walked into one. It died AFTER
    printing the ledger summary, so the output up to that point read like a
    completed run.

    The fixture reproduces it exactly: an unreadable project directory beside a
    readable one that DOES carry an overriding entry, so this cannot pass by the
    scan degrading to finding nothing.
    """
    root = tmp_path / "workspace"
    blind = root / "unreadable"
    blind.mkdir(parents=True)
    (blind / ".claude").mkdir()
    blind.chmod(0o000)
    good = root / "visible" / ".claude"
    good.mkdir(parents=True)
    (good / "settings.json").write_text(
        json.dumps({"skillOverrides": {"sglang": "off"}}), encoding="utf-8")
    try:
        hits, read = sync.overriding_scopes({"sglang"}, [root])
    finally:
        blind.chmod(0o700)
    assert read == 1, f"the readable project was not scanned: read={read}"
    assert [(p.name, n, v) for p, n, v in hits] == [
        ("settings.json", "sglang", "off")], hits


def test_control_the_project_scan_reports_zero_files_when_there_are_none(tmp_path):
    """The other side: an empty root yields `read == 0`, which the caller prints
    beside the findings so "no overriding scopes" and "nothing was looked at"
    can never read the same way."""
    root = tmp_path / "empty"
    root.mkdir()
    hits, read = sync.overriding_scopes({"prune-index"}, [root])
    assert (hits, read) == ([], 0)
