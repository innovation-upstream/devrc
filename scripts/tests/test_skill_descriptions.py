"""Gate on the SKILL LISTING — the third always-on cost, and the one that fails
SILENTLY.

Every skill's `name` + `description` loads on EVERY session under a budget of 1%
of the context window, with a per-entry cap of 1,536 characters. On overflow
Claude Code DROPS descriptions, starting with the skills invoked least, with no
error at all. So a description is ROUTING SURFACE, not documentation
(`CLAUDE.md` -> Layout), and nothing in this repo measured it.

WHY THIS EXISTS
---------------
`clickup` shipped a 175-char description of generic API-speak -- "Interact with
ClickUp tasks and documents ... Use when working with ClickUp task/doc URLs or
IDs". It was the SHORTEST of the 36 shipped entries (`git show
<pre>:claude/skills/clickup/SKILL.md`), so it carried the fewest trigger
keywords, and it named none of the task-shaped siblings a "task" prompt could
route to instead -- `clawgate` (self-hosted approval UI plus its own Tasks),
`initiatives` (durable cross-repo board), `mailbox` (email action-items queue).
A low-invocation skill is also first in line to be dropped on overflow, so the
one entry least able to afford weak routing had the weakest.

WHAT IS AND IS NOT MACHINE-CHECKABLE HERE
-----------------------------------------
"Is this description good routing surface?" is a judgement, and pretending
otherwise would produce a guard that reads as coverage while providing none
(`claude/RULES.md` -> "A guard's DESCRIPTION claims COVERAGE"). Four things ARE
decidable, and only those are asserted:

  1. every shipped skill still HAS a parseable, non-empty description -- an entry
     the listing drops entirely routes nothing;
  2. no entry breaches the per-entry cap -- past it the description is truncated
     or dropped silently;
  3. `clickup` names its disambiguating siblings, AND each name it uses is a
     skill that actually exists, AND the ledger of them cannot drift from the
     sentence in EITHER direction;
  4. devrc's TOTAL listing cost does not regrow past a ratchet.

WHY (4) EXISTS, AND WHAT IT DELIBERATELY DOES NOT CLAIM
-------------------------------------------------------
(2) is a PER-ENTRY cap; nothing measured the SUM, and the sum is what the
listing budget is spent against. Re-measured 2026-08-23 on the live workbench;
the budget MODEL below re-derived from the shipped binary 2026-08-24.

🔴 CHARS ARE THE UNIT -- LITERALLY, NOT AS AN APPROXIMATION OF TOKENS.
🔴 DO-NOT-RE-DERIVE: the chars/token divisor and the "no tokenizer reaches 6.7"
break-even argument this docstring used to carry (inherited from
`claudedocs/handoff-skill-listing-budget.md`) are MOOT. Claude Code never
tokenizes the listing. Read the model below instead of re-deriving that one.

Decompiled 2026-08-24 from the live binary -- `claude --version` 2.1.232,
`/nix/store/43fmrchzsg9g110qqk9n7a5z0icsf0qh-claude-code-2.1.232/bin/
.claude-wrapped`. ⚠ `bin/claude` beside it is a 20 KB wrapper STUB: grepping it
returns a confident 0 for keys that DO exist in the real bundle (measured --
`skillListingBudgetFraction` is 0 in the stub, 4 in the wrapped binary, while
`enabledPlugins` is 30 as a positive control). Grep the wrapped file, always
beside a control.

    budget_chars = floor(contextWindow * zx(model) * skillListingBudgetFraction)

with `skillListingBudgetFraction` defaulting to 0.01 and documented in the
binary's own settings schema as "Fraction of the context window (IN CHARACTERS)
reserved for the skill listing".

🔴 `zx(model)` IS NOT A CONSTANT -- it is 4 for a 14-model set (`claude-3-*`
through `claude-opus-4-6` / `claude-sonnet-4-6` / `claude-haiku-4-5`) and **3
for anything newer, including `claude-opus-5`**. The `=4` default in the
budget function is dead code: both real call sites pass `zx(mainLoopModel)`.

    model class          200k ctx    1M ctx
    claude-3.x .. 4.6      8,000      40,000
    claude-opus-5+         6,000      30,000

🔴 SO A SINGLE RATIO IS MEANINGLESS WITHOUT ITS MODEL. Quoting one bare number
here is the exact defect this paragraph replaced -- do not swap one
model-specific figure for another.

Entry cost, from the binary: `"- " + name + ": " + description`, i.e.
**name + 4 + min(len(desc), 1536)**; a name-only entry costs `name + 2`; the
entries are newline-joined, so the total carries a further **(n - 1)**.

    devrc's 36 entries, as this module measures  12,679 chars
    + 4 per entry (144) + 35 separators             179 chars
    ---------------------------------------------------------
    what Claude Code actually charges            12,858 chars

      vs a claude-3.x..4.6 200k budget (8,000)      1.61x over
      vs a claude-opus-5   200k budget (6,000)      2.14x over
      vs either 1M budget (40,000 / 30,000)         fits, with room

🔴 THIS MODULE DELIBERATELY MEASURES THE SMALLER NUMBER. `listing_total_chars`
sums `len(name) + len(desc)` and so UNDERCOUNTS the real charge by 179 chars at
36 entries (the per-entry 4 plus the separators; the general form is 5n - 1).
That is the conservative direction -- the gate can only ever be stricter than
reality -- and it is left alone ON PURPOSE: re-pointing the ratchet at the real
formula belongs with the tiering work, not with a retirement. Do not "fix" it
here without moving the ceiling in the same commit.

⚠ Two overrides sit ABOVE all of this. `SLASH_COMMAND_TOOL_CHAR_BUDGET`
short-circuits the whole computation before the fraction or the context window
are read; `skillListingMaxDescChars` raises the 1,536 per-entry cap. And the
context-window lookup does not always return 1e6 for a 1M-capable model -- a
session that cannot get 1M silently computes against 200,000 instead.

⚠ LATENT TRAP: the budget pass measures with `.length` while the renderer uses
`Bun.stringWidth`. They agree today because the only non-ASCII characters
across all descriptions are `—` and `…`. ONE EMOJI in a description desyncs
this gate from what Claude Code actually charges. No devrc skill defines
`when_to_use`, so the `description - when_to_use` concatenation contributes
nothing today either.

The 36 is down from 39: `ux-sweep`, `gpu-operator-check` and `session-audit`
were RETIRED on 2026-08-23 after measuring near-zero use of each -- no direct
script/service use and no genuine prompt demand across 5,582 transcript files.
Claude Code's own `skillUsage` counter recorded 0 for `ux-sweep` and
`gpu-operator-check` (absent entirely) and exactly 1 for `session-audit`, last
fired 2026-05-30. That is the eviction the playbook below asks for, and it is
why the total moved without any description being trimmed.

The 12,679 counts devrc's entries ONLY. The cloudflare plugin cost a further
5,221 chars of listing until it was removed from `enabledPlugins` in
`~/.claude/settings.json`, and it currently contributes nothing. 🔴 That file is
PER-HOST and unmanaged by design (`CLAUDE.md` -> Git discipline), so that zero
is a measurement of THE WORKBENCH, not a devrc fact and not a claim about the
laptop. Nothing in this repo can hold it there -- if the plugin comes back, so
does the 5,221, and no assertion here will notice. It is stated to explain the
drop from 20,013, not relied on.

WHAT HAPPENS ON OVERFLOW, AND WHO SURVIVES IT
---------------------------------------------
Over budget, Claude Code does NOT trim evenly. It builds an exempt set, then
decays every OTHER entry toward its bare `- name` form, cheapest-to-keep first,
which is what "descriptions are dropped starting with the skills invoked least"
means concretely -- the trigger keywords go, the name stays.

🔴 The exemption is `type === "prompt" && source === "bundled"` (plus entries
that are name-only already). **"bundled" is NOT "built-in".** `source` has a
SEPARATE `"builtin"` arm -- `init`, `statusline`, `security-review`,
`team-onboarding`, `commit-push-pr` are `builtin` and are NOT protected. Plugin
skills are not protected either. So the earlier claim in this docstring that
built-in skills are merely "ADDITIONAL" had the direction wrong twice: bundled
entries are SENIOR (they spend the budget first and never lose their
descriptions, so devrc's 36 compete for the remainder), while builtin and plugin
entries are ordinary competitors like devrc's own.

devrc's 36 entries are therefore a FLOOR on the listing, never the count: the
non-devrc entries are real, they are not readable from this repo, and no
assertion here can see them.

Two rounds of trimming plus one retirement round have narrowed devrc's share
(14,792 -> 13,925 -> 13,491 -> 12,679 as measured here) without ever closing the
200k gap -- and retiring three whole unused skills bought only 812 chars, which
is the honest measurement of how far this approach goes.

🔴 So this ceiling is NOT "the listing fits" -- it demonstrably does not on 200k,
and a gate pinned at the real 200k budget would be permanently red, which
`claude/RULES.md` says is worse than no gate. It is a RATCHET on the one number
devrc controls: having paid to cut the total, we do not silently pay it back.
Closing the remaining gap needs skills RETIRED or MERGED and/or the cloudflare
plugin disabled -- operator calls, not something a test can make.

(3) is a RELATIONSHIP, not a word: renaming or deleting any ledgered sibling
turns it red, so the disambiguation cannot rot into a pointer at nothing. The
DECLARED gap: the sibling ledger below is a judgement call. It is pinned EQUAL to
the sentence, so it can no longer silently shrink (that hole was real, and is
measured in the docstring of the test that closes it), and every name in it must
resolve to a real skill -- but nothing here can know that a FIFTH task-shaped
skill has appeared. Add it here when one does.

A FOURTH did, which is why that sentence now reads FIFTH.
`check-clickup-addressed` was migrated into devrc after this gate was written,
and it is the most confusable sibling yet: it works the SAME ClickUp tasks and
asks the opposite question -- not "what is on this ticket" but "was the work
actually done", answered by reading session transcripts. The overlap has a cheap
half and an expensive half, and only the expensive half is a listing entry:
`node query.mjs awaiting` (in `clickup`) is the ~45s triage pass answering WHICH
tasks have someone else's comment last, and `check-clickup-addressed` is the
transcript-reading verdict on WHETHER each is done. `awaiting` is a CLI command,
not a skill, so it costs the listing nothing and must NOT be added to the ledger
below -- every key there is asserted to be a real skill directory.

NOT asserted: a minimum description length. Measured when this was written, the
defect was 175 chars and the next-shortest entry was 181 (`espanso-audit`), so
any floor between them would be reverse-engineered from this one incident rather
than measured -- a number that reds a genuinely narrow skill with a genuinely
short description while a 200-char generic entry sails through. Length was the
symptom; the missing siblings are the part with a decidable answer.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "claude" / "skills"

# The per-entry ceiling Claude Code applies to one listing entry. NOT a devrc
# choice -- an upstream limit, restated in `CLAUDE.md` -> Layout. Do not raise it
# to make a description fit; cut the description.
PER_ENTRY_CAP_CHARS = 1_536

# Vacuity floor on the scan itself. 36 entries were measured at the time this
# gate was written; a glob that silently matches nothing would otherwise report a
# clean sweep of an empty set -- the "reassuring zero" `claude/RULES.md` names.
# Set below the measurement so adding or retiring a skill does not red the gate,
# high enough that a broken discovery path cannot pass.
MIN_LISTING_ENTRIES = 30

# 🔴 THE RATCHET. devrc's listing entries summed to 14,792 chars at
# 99b2636f; #749's trims took it to 13,925, six description shrinks took it to
# 13,491 -- none of them dropping a trigger phrase or a disambiguation clause --
# and retiring the three effectively-unused skills (`ux-sweep`,
# `gpu-operator-check`, `session-audit` -- 0/0/1 recorded invocations, see the
# docstring; plus ux-audit-loops' now-dangling pointer at the first) takes
# it to 12,679 across 36 entries. The ceiling sits ~250 chars above that
# measurement -- deliberately less than one average entry (12,679 / 36 = 352),
# so a NEW skill cannot be added without an eviction in the SAME commit. That is
# the same rule `claude/RULES.md` and
# `scripts/browser-bridge/tests/test_skill_size.py` already apply to the other
# two always-on documents.
#
# LOWER it when you cut; do NOT raise it to make a new description fit. A ceiling
# left at the previous, larger number licenses exactly the regrowth this constant
# exists to catch, so re-pinning it is part of the cut, not follow-up work. The
# eviction playbook is printed by the failing assertion below.
LISTING_TOTAL_CEILING_CHARS = 12_929

# The skills deployed by `mkOutOfStoreSymlink` from `scripts/` instead of by the
# recursive `claude/skills` mapping (`nix/home.nix`). They are listing entries
# like any other, so a scan of `claude/skills/` alone is a count of DECLARATIONS
# rather than of INSTANCES. Their absence is a failure below, not a skip.
#
# 🔴 This list SHRANK silently once already: `scripts/opencode/SKILL.md` is
# deployed to `~/.claude/skills/opencode/SKILL.md` by the same idiom and was
# missing here, so this module measured 38 of 39 entries -- opencode's per-entry
# cap and its parseability were never checked, and it was invisible to the total
# below. Nothing failed; the gate simply covered less than its docstring claimed.
# `test_the_out_of_tree_ledger_matches_nix_home` now pins the set two-way against
# `nix/home.nix`, so a fourth one cannot arrive unmeasured.
OUT_OF_TREE_SKILLS = (
    REPO_ROOT / "scripts" / "browser-bridge" / "SKILL.md",
    REPO_ROOT / "scripts" / "dl-router" / "SKILL.md",
    REPO_ROOT / "scripts" / "opencode" / "SKILL.md",
)

# `nix/home.nix` is the only place that decides which out-of-tree files become
# `~/.claude/skills/<x>/SKILL.md`. Read, never restated.
HOME_NIX = REPO_ROOT / "nix" / "home.nix"
HOME_NIX_SKILL_SOURCE = re.compile(
    r'home\.file\."\.claude/skills/[^"]+/SKILL\.md"\.source\s*=\s*'
    r'config\.lib\.file\.mkOutOfStoreSymlink\s*"\$\{workspace\}/devrc/([^"]+)"',
)

# The task-shaped siblings a bare "task"/"ticket" prompt could route to instead
# of `clickup`, each with why it is confusable. Keys must be real skill
# directories -- asserted, so a rename cannot leave a dead pointer in the
# always-on listing.
CLICKUP_SIBLINGS = {
    "clawgate": "the self-hosted approval UI, which has its own Tasks",
    "initiatives": "the durable cross-repo initiative board",
    "mailbox": "the email action-items queue",
    "check-clickup-addressed": (
        "verifies from session transcripts whether work on a ClickUp task was "
        "actually completed -- the same tasks, the opposite question"
    ),
}

# The disambiguating sentence, pinned WHOLE and built from the sibling names.
#
# 🔴 Deliberately not a per-name substring check. `claude/RULES.md`: "When the
# artifact under test IS prose, a guard on WORDS is walkable by REWORDING -- pin
# the WHOLE normalised string." A description could name EVERY sibling in
# passing ("see also clawgate, initiatives, mailbox and check-clickup-addressed")
# and satisfy a word check while telling the router nothing about which one owns
# which case -- that exact walk is a control below. The stated
# cost, accepted: a cosmetic reword of this ONE sentence fails this test. The
# routing keywords in the rest of the description are deliberately left free to
# change -- rigidifying those would make the gate fight the very tuning it exists
# to protect.
CLICKUP_DISAMBIGUATION = (
    "This is the EXTERNAL ClickUp workspace — the self-hosted approval UI and "
    "ITS Tasks are `clawgate`, the durable cross-repo board is `initiatives`, "
    "the email action-items queue is `mailbox`, and verifying from session "
    "transcripts whether work on a task was actually done is "
    "`check-clickup-addressed`."
)


def _load_parser():
    """The repo's OWN frontmatter reader, not a second copy of one.

    `scripts/opencode/generate-commands.py::parse_skill` is what turns these
    files into the opencode command listing, so measuring with it means this gate
    measures the string that actually ships. A hand-rolled regex here would be a
    second implementation free to disagree with the deployed one -- and it is
    disagreement, not absence, that would make this gate lie.
    """
    path = REPO_ROOT / "scripts" / "opencode" / "generate-commands.py"
    loader = importlib.machinery.SourceFileLoader("_gen_commands", str(path))
    spec = importlib.util.spec_from_loader("_gen_commands", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod.parse_skill


parse_skill = _load_parser()


def _skill_md_paths() -> list[Path]:
    """Every SKILL.md that becomes a listing entry, in-tree and out."""
    found = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    for extra in OUT_OF_TREE_SKILLS:
        assert extra.is_file(), (
            f"{extra} is missing. It is deployed into ~/.claude/skills/ by an "
            "mkOutOfStoreSymlink in nix/home.nix, so it IS a listing entry. If it "
            "moved, re-point this list -- do not drop it, or the gate silently "
            "stops measuring a skill that still ships."
        )
        found.append(extra)
    return found


def _entries() -> list[tuple[str, str, str]]:
    """(repo-relative path, name, description) for every listing entry.

    A file the parser rejects is reported by `_unparseable()`, not silently
    skipped: opencode DROPS such a skill from the listing, which is the loud
    version of the failure this module gates.
    """
    out = []
    for path in _skill_md_paths():
        parsed = parse_skill(path)
        if parsed is None:
            continue
        out.append((str(path.relative_to(REPO_ROOT)), parsed["name"],
                    parsed["description"]))
    return out


def _unparseable() -> list[str]:
    return [str(p.relative_to(REPO_ROOT)) for p in _skill_md_paths()
            if parse_skill(p) is None]


def entry_chars(name: str, description: str) -> int:
    """Characters one listing entry costs.

    `name` counts because the cap is on the ENTRY, not on the description alone.

    A double-quoted YAML value reaches us with its `\\"` escapes unresolved, so
    such a description measures a character or two LONG. That is deliberate and
    stated: the error is conservative, so this check can only ever be stricter
    than reality, never laxer.
    """
    return len(name) + len(description)


def listing_total_chars(entries) -> int:
    """Direction 4: what the WHOLE listing costs, every session.

    Factored out for the same reason as `over_cap` -- the controls below grade
    THE GATE, not a second implementation of it.
    """
    return sum(entry_chars(name, desc) for _, name, desc in entries)


def costliest(entries, n: int = 10) -> list[tuple[str, int]]:
    """The n most expensive entries, largest first -- the eviction shortlist."""
    return sorted(((name, entry_chars(name, desc)) for _, name, desc in entries),
                  key=lambda pair: (-pair[1], pair[0]))[:n]


def over_cap(entries) -> list[tuple[str, int]]:
    """Direction 1 of the gate, factored out so the controls below grade THE
    GATE rather than a re-implementation of it."""
    return sorted(
        (label, entry_chars(name, desc))
        for label, name, desc in entries
        if entry_chars(name, desc) > PER_ENTRY_CAP_CHARS
    )


# --------------------------------------------------------------------------- #
# The live tree
# --------------------------------------------------------------------------- #

def test_the_scan_finds_the_skills_at_all():
    """POSITIVE CONTROL on discovery. Every other assertion here is a reassuring
    ZERO, and a zero from a scan that walked nothing is indistinguishable from a
    clean sweep."""
    entries = _entries()
    assert len(entries) >= MIN_LISTING_ENTRIES, (
        f"only {len(entries)} listing entries found under {SKILLS_DIR} (+ the "
        f"{len(OUT_OF_TREE_SKILLS)} out-of-tree skills), below the "
        f"{MIN_LISTING_ENTRIES} vacuity floor. Every check in this module would "
        "otherwise pass over an almost-empty set. Fix the discovery path."
    )


def test_every_shipped_skill_has_a_parseable_description():
    """A SKILL.md the frontmatter reader rejects is DROPPED from the listing --
    it routes nothing, and nothing says so."""
    broken = _unparseable()
    assert not broken, (
        "these SKILL.md files have no `name`+`description` frontmatter the "
        f"repo's own parser can read, so they drop out of the listing: {broken}"
    )
    empty = [label for label, _, desc in _entries() if not desc.strip()]
    assert not empty, f"these skills have an empty description: {empty}"


def test_no_listing_entry_breaches_the_per_entry_cap():
    offenders = over_cap(_entries())
    assert not offenders, (
        "these listing entries exceed the "
        f"{PER_ENTRY_CAP_CHARS:,}-char per-entry cap:\n"
        + "\n".join(f"  {label}: {n:,} chars" for label, n in offenders)
        + "\nPast the cap the description is truncated or dropped SILENTLY, "
        "taking its trigger keywords with it. Cut the description -- move the "
        "narrative into the body, which costs 0 until the skill is invoked. Do "
        "NOT raise PER_ENTRY_CAP_CHARS; it is an upstream limit, not our choice."
    )


def test_the_out_of_tree_ledger_matches_nix_home():
    """🔴 SEAM guard, pinned two-way. The defect it closes was real and silent.

    `OUT_OF_TREE_SKILLS` is a hand-maintained list of files that `nix/home.nix`
    deploys into `~/.claude/skills/`. Nothing connected the two, so the list
    could -- and did -- fall behind by one, leaving `opencode` unmeasured by
    every check in this module while all of them stayed green.

    Fails when the set GROWS (a new mkOutOfStoreSymlink skill nobody ledgered)
    and when it SHRINKS (a ledger entry whose nix line is gone).
    """
    nix = HOME_NIX.read_text(encoding="utf-8")
    from_nix = set(HOME_NIX_SKILL_SOURCE.findall(nix))
    assert from_nix, (
        f"the mkOutOfStoreSymlink pattern matched NOTHING in {HOME_NIX}. An "
        "empty match set is not evidence of an empty ledger -- the deploy idiom "
        "in home.nix has changed shape. Re-derive HOME_NIX_SKILL_SOURCE; do not "
        "let this pass on a zero."
    )
    ledgered = {str(p.relative_to(REPO_ROOT)) for p in OUT_OF_TREE_SKILLS}
    assert from_nix == ledgered, (
        "OUT_OF_TREE_SKILLS and nix/home.nix disagree about which files ship as "
        "~/.claude/skills/*/SKILL.md.\n"
        f"  deployed by home.nix but NOT ledgered here (so this whole module "
        f"silently skips them): {sorted(from_nix - ledgered)}\n"
        f"  ledgered here but no longer deployed: {sorted(ledgered - from_nix)}\n"
        "Update OUT_OF_TREE_SKILLS in the same commit."
    )


def test_the_listing_total_does_not_regrow_past_its_ratchet():
    """🔴 The SUM, which the per-entry cap above structurally cannot see.

    36 entries each comfortably under the 1,536-char per-entry cap still overrun
    the listing budget on a 200k window -- 1.61x on a claude-3.x..4.6 model,
    2.14x on claude-opus-5, because the budget's chars-per-token multiplier is
    model-dependent (4 vs 3). So the per-entry check can be green while
    descriptions are being dropped. See this module's docstring for the
    decompiled formula, for why a ratio quoted without its model is meaningless,
    and for why this is a ratchet rather than a gate on the real budget.
    """
    entries = _entries()
    total = listing_total_chars(entries)
    assert total <= LISTING_TOTAL_CEILING_CHARS, (
        f"devrc's skill listing costs {total:,} chars across {len(entries)} "
        f"entries, over the {LISTING_TOTAL_CEILING_CHARS:,}-char ratchet by "
        f"{total - LISTING_TOTAL_CEILING_CHARS:,}.\n"
        "This loads on EVERY session and overflows SILENTLY -- Claude Code drops "
        "descriptions starting with the skills invoked LEAST, taking their "
        "trigger keywords with them, with no error.\n"
        "\nEVICTION PLAYBOOK -- do these in order, in THIS commit:\n"
        "  1. Cut mechanism/implementation prose out of the costliest "
        "descriptions below. A description is ROUTING SURFACE: the key use case, "
        "the literal phrases Zach says, and the clause disambiguating it from a "
        "sibling skill. How it works belongs in the body, which costs 0 until "
        "the skill is invoked.\n"
        "  2. Do NOT drop a trigger phrase or a disambiguation clause to fit -- "
        "that trades a silent overflow for a silent mis-route.\n"
        "  3. If you are ADDING a skill, pay for it by retiring one. Rank "
        "candidates by cost-per-invocation, not by size: "
        "`scripts/session-analysis/adoption-scan.py` for the tracked tools, and "
        "the `claude`/`command` rows of `activity.events` for typed slash "
        "commands. A skill nobody has invoked is the eviction; a skill invoked "
        "constantly is not, however long its entry.\n"
        "  4. Only when 1-3 are exhausted, LOWER nothing here -- raising "
        "LISTING_TOTAL_CEILING_CHARS pays the cost back and is what this "
        "constant exists to prevent.\n"
        "\ncostliest entries now:\n"
        + "\n".join(f"  {name:26} {n:,} chars" for name, n in costliest(entries))
    )


def test_the_sibling_ledger_and_the_pinned_sentence_name_the_SAME_skills():
    """🔴 Two-way, so the ledger cannot silently SHRINK.

    Everything else here that iterates siblings iterates the LEDGER, so deleting
    an entry deletes its assertions along with it: the gate keeps passing while
    checking strictly less than its docstring claims -- a guard reading as
    coverage while providing none (`claude/RULES.md`). MEASURED, not reasoned:
    dropping `check-clickup-addressed` from CLICKUP_SIBLINGS left this whole
    module GREEN at 14 passed -- one quieter parametrised case than before, and
    no failure anywhere.

    Pinning the two sets EQUAL fails in both directions -- a name added to the
    sentence without a ledger entry (so its skill is never proved to exist), and
    a ledger entry deleted while the sentence still routes at it.
    """
    named = set(re.findall(r"`([a-z0-9][a-z0-9-]*)`", CLICKUP_DISAMBIGUATION))
    assert named == set(CLICKUP_SIBLINGS), (
        "the pinned disambiguation sentence and CLICKUP_SIBLINGS have drifted "
        "apart.\n  named in the sentence but not in the ledger (so nothing "
        f"proves the skill exists): {sorted(named - set(CLICKUP_SIBLINGS))}"
        "\n  in the ledger but not named in the sentence (so the ledger "
        "describes routing the listing does not carry): "
        f"{sorted(set(CLICKUP_SIBLINGS) - named)}"
        "\nUpdate both, plus clickup's description, in the same commit."
    )


@pytest.mark.parametrize("sibling", sorted(CLICKUP_SIBLINGS))
def test_each_clickup_sibling_is_a_real_skill(sibling):
    """The disambiguation must point at something that EXISTS. A renamed or
    retired sibling turns this red instead of leaving the always-on listing
    routing readers at a skill that is gone."""
    assert (SKILLS_DIR / sibling / "SKILL.md").is_file(), (
        f"clickup's description disambiguates from `{sibling}` "
        f"({CLICKUP_SIBLINGS[sibling]}), but claude/skills/{sibling}/SKILL.md "
        "does not exist. Update both the description and this ledger."
    )


def test_clickup_disambiguates_from_its_task_shaped_siblings():
    """🔴 THE DEFECT THIS MODULE WAS WRITTEN FOR.

    `clickup`'s description named none of the skills a "task"/"ticket" prompt
    could route to instead, so three sibling skills competed for the same
    prompts with nothing in the always-on listing to separate them.

    Pinned as the WHOLE sentence, not as three separate name lookups -- see
    CLICKUP_DISAMBIGUATION for why a word check is walkable here.
    """
    entries = {name: desc for _, name, desc in _entries()}
    assert "clickup" in entries, "the clickup skill is not in the listing at all"
    normalised = " ".join(entries["clickup"].split())

    assert normalised.count(CLICKUP_DISAMBIGUATION) == 1, (
        "clickup's description no longer carries its disambiguation sentence "
        "exactly once as written.\n  Expected verbatim:\n    "
        + CLICKUP_DISAMBIGUATION
        + "\n  Description is now:\n    "
        + normalised
        + "\nWithout it a 'task'/'ticket' prompt has nothing in the always-on "
        f"listing separating clickup from {sorted(CLICKUP_SIBLINGS)}. Restore "
        "the wording, or re-point this pin deliberately in the same commit."
    )


# --------------------------------------------------------------------------- #
# NEGATIVE CONTROLS -- each check is shown able to go RED, against realistic
# input rather than a textbook fixture, and graded by the SAME helpers the live
# tests use so a mutation anywhere in the pipeline moves a verdict here.
# --------------------------------------------------------------------------- #

def _real_clickup_description() -> str:
    parsed = parse_skill(SKILLS_DIR / "clickup" / "SKILL.md")
    assert parsed is not None, "fixture broken: clickup frontmatter unreadable"
    return parsed["description"]


def test_control_an_over_cap_entry_is_reported():
    """Can `over_cap` go red at all? Built from the REAL description padded past
    the cap, not from a synthetic wall of 'x' -- a check that only ever sees
    textbook input is a check whose behaviour on real input is unmeasured."""
    fat = _real_clickup_description()
    fat = fat + " " + ("routing keywords " * 100)
    assert entry_chars("clickup", fat) > PER_ENTRY_CAP_CHARS, "fixture too small"
    assert over_cap([("fixture/SKILL.md", "clickup", fat)]) == [
        ("fixture/SKILL.md", entry_chars("clickup", fat))
    ]


def test_control_an_at_cap_entry_is_not_reported():
    """The other side of the boundary: exactly AT the cap is legal. A gate that
    reds on correct input trains everyone to click through it."""
    name = "clickup"
    desc = "x" * (PER_ENTRY_CAP_CHARS - len(name))
    assert entry_chars(name, desc) == PER_ENTRY_CAP_CHARS
    assert over_cap([("fixture/SKILL.md", name, desc)]) == []


def test_control_the_name_counts_toward_the_cap():
    """ISOLATED mutation: only the name grows. If `entry_chars` ever drops the
    name, this is the assertion that moves -- the cap-breach tests above would
    not, because their descriptions breach on their own."""
    desc = "x" * (PER_ENTRY_CAP_CHARS - 1)
    assert over_cap([("fixture/SKILL.md", "ab", desc)]) == [
        ("fixture/SKILL.md", PER_ENTRY_CAP_CHARS + 1)
    ]
    assert over_cap([("fixture/SKILL.md", "a", desc)]) == []


def test_control_the_pre_change_clickup_description_fails_the_pin():
    """🔴 RED-AT-BASE, kept as a test rather than as a claim in a commit message.

    This is the exact string that shipped before this gate existed. It must fail
    the disambiguation pin -- if it ever passes, the pin has been loosened into
    something the original defect satisfies.
    """
    pre_change = (
        "Interact with ClickUp tasks and documents - get task details, view "
        "comments, create and manage tasks, create and edit docs. Use when "
        "working with ClickUp task/doc URLs or IDs."
    )
    normalised = " ".join(pre_change.split())
    assert normalised.count(CLICKUP_DISAMBIGUATION) == 0
    for sibling in CLICKUP_SIBLINGS:
        assert sibling not in normalised, (
            f"the pre-change description mentioned `{sibling}` after all -- "
            "re-derive this fixture from git history before trusting it"
        )
    # ...and it was under the cap the whole time, which is the point: the cap
    # check alone would NEVER have caught this defect. Two independent hazards,
    # two independent checks.
    assert entry_chars("clickup", pre_change) < PER_ENTRY_CAP_CHARS


def test_control_a_passing_mention_of_every_sibling_still_fails_the_pin():
    """The walk the whole-sentence pin exists to block: name all three siblings,
    disambiguate from none of them. A per-name substring check passes this."""
    walked = (
        "Interact with ClickUp tasks and documents. See also clawgate, "
        "initiatives, mailbox and check-clickup-addressed."
    )
    for sibling in CLICKUP_SIBLINGS:
        assert sibling in walked, "fixture broken: it must name every sibling"
    assert " ".join(walked.split()).count(CLICKUP_DISAMBIGUATION) == 0


def test_control_a_skill_without_frontmatter_is_reported_not_skipped(tmp_path):
    """A SKILL.md the parser rejects must arrive as a failure. Graded through
    the real parser, because "dropped from the listing" is exactly what
    `parse_skill` returning None means downstream."""
    bare = tmp_path / "SKILL.md"
    bare.write_text("# a skill with no frontmatter at all\n", encoding="utf-8")
    assert parse_skill(bare) is None

    no_desc = tmp_path / "nodesc.md"
    no_desc.write_text("---\nname: orphan\n---\nbody\n", encoding="utf-8")
    assert parse_skill(no_desc) is None


def test_control_the_total_ratchet_can_go_red():
    """Can `listing_total_chars` see a regrowth at all?

    Built from the REAL live entries plus ONE realistically-sized new skill --
    not a synthetic wall of text -- because "someone adds a skill" is the exact
    way this ceiling gets breached. The mutation is ISOLATED to the thing under
    test: one extra entry, nothing else touched.
    """
    entries = _entries()
    live_total = listing_total_chars(entries)
    assert live_total <= LISTING_TOTAL_CEILING_CHARS, "live tree already red"

    # A new entry the size of the current mean. It must be enough on its own to
    # breach the ceiling -- if it is not, the ratchet has been left so slack that
    # a whole skill can be added for free, which is the failure mode it exists
    # to prevent.
    mean = live_total // len(entries)
    newcomer = ("fixture/SKILL.md", "a-new-skill", "x" * (mean - len("a-new-skill")))
    assert entry_chars(newcomer[1], newcomer[2]) == mean
    assert listing_total_chars(entries + [newcomer]) > LISTING_TOTAL_CEILING_CHARS, (
        f"the ceiling has {LISTING_TOTAL_CEILING_CHARS - live_total} chars of "
        f"headroom, enough to absorb a whole {mean}-char entry unnoticed. "
        "Re-tighten LISTING_TOTAL_CEILING_CHARS to the current measurement plus "
        "less than one average entry."
    )


def test_control_the_total_is_a_SUM_not_a_max():
    """ISOLATED mutation guard. Every entry here is under the per-entry cap, so
    `over_cap` stays empty -- only a real sum moves. Fixture values are pairwise
    distinct AND distinct from the cap, so a mutant returning `max(...)`, the
    count, or the cap constant cannot coincide with the right answer."""
    fixture = [
        ("a/SKILL.md", "aa", "x" * 300),
        ("b/SKILL.md", "bbb", "x" * 500),
        ("c/SKILL.md", "cccc", "x" * 700),
    ]
    assert over_cap(fixture) == []
    assert listing_total_chars(fixture) == 302 + 503 + 704
    assert listing_total_chars([]) == 0


def test_control_costliest_ranks_by_cost_largest_first():
    """The eviction shortlist must actually be the expensive end. Fixture is
    supplied out of order and with a name long enough to change the ranking on
    its own, so a mutant sorting by description length or by insertion order
    lands on a different answer."""
    fixture = [
        ("mid/SKILL.md", "m", "x" * 400),
        ("big/SKILL.md", "a-very-long-skill-name", "x" * 390),
        ("small/SKILL.md", "s", "x" * 100),
    ]
    assert costliest(fixture, 2) == [("a-very-long-skill-name", 412), ("m", 401)]


def test_control_the_module_measures_the_real_tree_not_a_fixture():
    """Cheap tripwire against this file drifting into self-referential green:
    the live entries must include skills this module never names."""
    names = {name for _, name, _ in _entries()}
    for expected in ("clickup", "browser", "dl-router", "opencode",
                     "session-manager"):
        assert expected in names, (
            f"`{expected}` is not in the scanned listing -- discovery is reading "
            "the wrong tree, or an out-of-tree skill stopped being picked up"
        )
