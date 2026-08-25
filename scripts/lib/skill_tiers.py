#!/usr/bin/env python3
"""The skill-listing TIER LEDGER, read once and shared by everything that needs it.

`claude/skill-tiers.json` assigns every shipped skill to tier A (a full
description in the always-on listing) or tier B (`name-only` -- roughly 12 chars,
still `/name`-invocable and still callable by the Skill tool, but with no routing
prose, so it no longer fires from a described symptom).

Three consumers read it and they must not disagree:

  * `scripts/sync-skill-tiers.py`   applies it to a host's ~/.claude/settings.json
  * `scripts/drift-check.sh`        reports a host that has drifted from it
  * `scripts/tests/test_skill_tiers.py`  pins it two-way against the shipped tree

so the parsing, the discovery and the cost formula live HERE, once.
`claude/RULES.md` -> "One rule, one place": a predicate open-coded at N sites is
typically wrong at N-1 of them, in the same direction.

🔴 THE COST FORMULA IS THE REAL ONE, NOT THE GATE'S.
`scripts/tests/test_skill_descriptions.py` deliberately measures the SMALLER
number -- `len(name) + len(desc)` -- and its docstring explains why that ratchet
is left alone. What Claude Code actually charges, decompiled from the shipped
binary (see `claudedocs/proposal-skill-listing-tiers.md` section 1), is

    entry:      "- " + name + ": " + description   ->  len(name) + 4 + min(len(desc), 1536)
    name-only:  "- " + name                        ->  len(name) + 2
    total:      sum(entries) + (n - 1)             ->  newline-joined

i.e. the older measure undercounts by exactly `5n - 1`. THIS module uses the real
formula; the two numbers are supposed to differ, and neither is a bug.

🔴 A RATIO WITHOUT ITS MODEL IS MEANINGLESS. The budget is
`floor(contextWindow * zx(model) * 0.01)` CHARACTERS, and `zx` is 4 for the 14
models up to 4.6 and **3 for claude-opus-5 and newer** -- 6,000 at 200k and
30,000 at 1M on this session's model, not 8,000 / 40,000. Never quote one bare
multiple of "the budget".
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "claude" / "skill-tiers.json"
SKILLS_DIR = REPO_ROOT / "claude" / "skills"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

# The upstream per-entry ceiling. Not a devrc choice.
PER_ENTRY_CAP_CHARS = 1_536

# What a tier-B entry becomes in `skillOverrides`.
#
# 🔴 NOT `user-invocable-only` and NOT `off`. Those two remove the skill from the
# model's reach entirely (cost 0, hidden from the listing); `name-only` keeps the
# NAME in the listing and keeps the Skill tool able to call it. Tiering is a
# routing decision, never a disabling one -- if a skill should be off, delete it.
TIER_B_OVERRIDE_VALUE = "name-only"

# The values Claude Code's resolver understands. An unknown string is not a
# no-op that we would notice: it falls through to the default, silently.
VALID_OVERRIDE_VALUES = ("on", "name-only", "user-invocable-only", "off")

VALID_TIERS = ("A", "B")

# Skills deployed by `mkOutOfStoreSymlink` from `scripts/`, not by the recursive
# `claude/skills` mapping. They are listing entries like any other.
#
# 🔴 DERIVED FROM nix/home.nix, never hand-listed here. The equivalent list in
# `test_skill_descriptions.py` fell a whole entry behind once (`opencode` went
# unmeasured while every check stayed green); deriving it means a fourth one is
# covered the day it is added. `test_skill_tiers.py` pins this discovery against
# that module's, so the two cannot silently disagree either.
HOME_NIX_SKILL_SOURCE = re.compile(
    r'home\.file\."\.claude/skills/[^"]+/SKILL\.md"\.source\s*=\s*'
    r'config\.lib\.file\.mkOutOfStoreSymlink\s*"\$\{workspace\}/devrc/([^"]+)"',
)


def _load_parser():
    """The repo's OWN frontmatter reader -- the one that builds the deployed
    opencode command listing. A second regex here would be free to disagree with
    what actually ships, and disagreement (not absence) is what makes a gate lie.
    """
    path = REPO_ROOT / "scripts" / "opencode" / "generate-commands.py"
    loader = importlib.machinery.SourceFileLoader("_gen_commands_tiers", str(path))
    spec = importlib.util.spec_from_loader("_gen_commands_tiers", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod.parse_skill


def skill_md_paths() -> list[Path]:
    """Every SKILL.md that becomes a listing entry, in-tree and out."""
    found = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    nix = HOME_NIX.read_text(encoding="utf-8")
    out_of_tree = sorted(HOME_NIX_SKILL_SOURCE.findall(nix))
    if not out_of_tree:
        raise RuntimeError(
            f"the mkOutOfStoreSymlink pattern matched NOTHING in {HOME_NIX}. An "
            "empty match set is not evidence of an empty ledger -- the deploy "
            "idiom has changed shape. Re-derive HOME_NIX_SKILL_SOURCE rather "
            "than letting this return a short list."
        )
    for rel in out_of_tree:
        p = REPO_ROOT / rel
        if not p.is_file():
            raise RuntimeError(
                f"nix/home.nix deploys {rel} as a ~/.claude/skills entry, but "
                f"{p} does not exist."
            )
        found.append(p)
    return found


def shipped_skills() -> dict[str, tuple[str, str]]:
    """name -> (repo-relative path, description) for every listing entry.

    A SKILL.md the parser rejects is REPORTED by `unparseable()`, not silently
    dropped -- upstream drops such a skill from the listing entirely, which is
    the loud version of the same failure.
    """
    parse_skill = _load_parser()
    out: dict[str, tuple[str, str]] = {}
    for path in skill_md_paths():
        parsed = parse_skill(path)
        if parsed is None:
            continue
        out[parsed["name"]] = (str(path.relative_to(REPO_ROOT)), parsed["description"])
    return out


def unparseable() -> list[str]:
    parse_skill = _load_parser()
    return [str(p.relative_to(REPO_ROOT)) for p in skill_md_paths()
            if parse_skill(p) is None]


def load_ledger(path: Path | None = None) -> dict[str, dict]:
    """name -> {"tier": "A"|"B", "why": str}. Raises on a malformed ledger.

    Refusing to parse is the right failure: every consumer of this ledger takes
    an ACTION from it (writing settings.json, calling a host drifted), and a
    partially-read ledger produces a confident wrong one.
    """
    path = path or LEDGER_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        raise ValueError(f"{path}: expected a top-level object with a `skills` object")
    skills = data["skills"]
    if not skills:
        raise ValueError(
            f"{path}: the `skills` object is EMPTY. An empty ledger satisfies "
            "every per-entry check in silence -- it is not a clean ledger."
        )
    for name, entry in skills.items():
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: entry for `{name}` is not an object")
        tier = entry.get("tier")
        if tier not in VALID_TIERS:
            raise ValueError(
                f"{path}: `{name}` has tier {tier!r}; expected one of {VALID_TIERS}"
            )
        if tier == "B" and not str(entry.get("why", "")).strip():
            raise ValueError(
                f"{path}: `{name}` is tier B with no `why`. Every tier-B call is "
                "a routing decision someone has to be able to audit; a tier-A "
                "entry needs none because A is the default."
            )
    return skills


def tier_of(ledger: dict[str, dict], name: str) -> str:
    return ledger[name]["tier"]


def tier_b_names(ledger: dict[str, dict]) -> list[str]:
    return sorted(n for n, e in ledger.items() if e["tier"] == "B")


def tier_a_names(ledger: dict[str, dict]) -> list[str]:
    return sorted(n for n, e in ledger.items() if e["tier"] == "A")


def expected_overrides(ledger: dict[str, dict]) -> dict[str, str]:
    """The `skillOverrides` map this ledger asks a host to carry.

    Tier A is deliberately ABSENT rather than written as `"on"`: `on` is already
    the default, so emitting it would add a line per skill that says nothing, and
    would make every tier-A flip a settings.json write instead of a no-op.
    """
    return {n: TIER_B_OVERRIDE_VALUE for n in tier_b_names(ledger)}


def reconcile(ledger: dict[str, dict], skills: dict[str, tuple[str, str]]):
    """-> (untiered, phantom). THE two-way pin, in one place.

    `untiered`: a shipped skill with no ledger entry -- it silently keeps a full
    description, so the mechanism stops covering the tree as the tree grows.
    `phantom`:  a ledger entry naming no shipped skill -- a dead override that
    can never be observed, pointing at a skill that was renamed or retired.
    """
    untiered = sorted(set(skills) - set(ledger))
    phantom = sorted(set(ledger) - set(skills))
    return untiered, phantom


# --- cost, with the REAL formula --------------------------------------------- #

def entry_chars(name: str, description: str) -> int:
    """`"- " + name + ": " + description`, capped per-entry."""
    return len(name) + 4 + min(len(description), PER_ENTRY_CAP_CHARS)


def name_only_chars(name: str) -> int:
    """`"- " + name`."""
    return len(name) + 2


def _joined(costs: list[int]) -> int:
    """Newline-joined: the separators are `n - 1`, and an empty block costs 0."""
    return sum(costs) + max(0, len(costs) - 1) if costs else 0


def tier_a_chars(ledger: dict[str, dict], skills: dict[str, tuple[str, str]]) -> int:
    """What the tier-A block costs, every session. The ratchet's subject."""
    return _joined([entry_chars(n, skills[n][1])
                    for n in tier_a_names(ledger) if n in skills])


def tier_b_chars(ledger: dict[str, dict], skills: dict[str, tuple[str, str]]) -> int:
    return _joined([name_only_chars(n) for n in tier_b_names(ledger) if n in skills])


def devrc_listing_chars(ledger: dict[str, dict],
                        skills: dict[str, tuple[str, str]]) -> int:
    """devrc's whole contribution under this ledger -- A full, B name-only, one
    separator between every pair. Reported as INFORMATION beside the ratchet:
    the listing also carries non-devrc entries this repo cannot see, so this is
    a floor on the listing and never the listing."""
    costs = []
    for name in sorted(skills):
        if name in ledger and ledger[name]["tier"] == "B":
            costs.append(name_only_chars(name))
        else:
            costs.append(entry_chars(name, skills[name][1]))
    return _joined(costs)


def costliest(ledger: dict[str, dict], skills: dict[str, tuple[str, str]],
              n: int = 10) -> list[tuple[str, int]]:
    """The tier-A entries to consider demoting first, largest first."""
    return sorted(((name, entry_chars(name, skills[name][1]))
                   for name in tier_a_names(ledger) if name in skills),
                  key=lambda pair: (-pair[1], pair[0]))[:n]
