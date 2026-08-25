#!/usr/bin/env python3
"""The MEASUREMENT LAYER for the devrc explainer page.

WHY THIS FILE EXISTS, AND WHY IT IS NOT A TEMPLATE
--------------------------------------------------
`CLAUDE.md` in this repo carries, in prose, a running tally of facts about the
system: how many tests, which gate blocks a merge, what the skill listing costs.
That prose has been measurably FALSE in both directions — the merge-gate line
read "CI gates both suites" and later "NO AUTOMATED GATE IS RUNNING", and both
were wrong at some point. A page that restates those numbers inherits the same
decay.

So the explainer is a MEASUREMENT, not a document. Every number it renders comes
from a function in this module, taken at build time, stamped with the moment it
was taken, and carrying the command that would settle it independently.

THE THREE RULES THIS MODULE ENFORCES
------------------------------------
1. 🔴 `UNMEASURED` IS A FIRST-CLASS RESULT, NEVER AN OMISSION. A measurer that
   raises does not drop its row — it produces a row whose status is
   `unmeasured`, carrying the REASON and the `settle` command. An omitted row is
   byte-identical to a row that measured clean, which is the silent-zero this
   repo keeps re-learning (`scripts/drift-check.sh` rc 18: "we could not look"
   correctly set no code, and therefore escalated NEVER).

2. 🔴 AN ALL-UNMEASURED BUILD IS A FAILURE, NOT A PAGE. `MeasurementSet.verdict`
   returns `all-unmeasured` when nothing measured, and the generator exits
   non-zero on it. A page where every row says UNMEASURED looks like a careful
   page; it is a broken build.

3. 🔴 A CONSTANT A TEST ALREADY OWNS IS READ FROM THAT TEST. `MAX_BYTES` lives
   in `scripts/tests/test_rules_size.py`; `TIER_A_CEILING_CHARS` lives in
   `scripts/tests/test_skill_tiers.py`; the per-target floors live in
   `scripts/run-tests.sh`. This module imports or shells out to each owner. It
   never restates one — a second hand-maintained copy of a number is exactly how
   the drift regrows, and those tests exist because it did.

🔴 PUBLIC-REPO CONSTRAINT. This file is committed; what it MEASURES is not.
Every real identifier — scope names, hostnames, IP addresses, home paths, unit
names — is read at run time from the local machine and never appears as a
literal here or in any fixture. `scripts/present/sanitize.py` swaps them for
synthetic stand-ins when the page is built with `--sanitize`.
"""
from __future__ import annotations

import datetime as _dt
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# The result type
# --------------------------------------------------------------------------- #

MEASURED = "measured"
UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class Measurement:
    """One fact, with everything needed to distrust it.

    `asof` is when THIS run looked, not when the underlying thing last changed.
    It is stamped per row on purpose: a byte count re-derived from the tree ages
    in minutes, a systemd timer roster ages in days, and a footer date would
    claim one freshness for both.
    """

    key: str
    label: str
    section: str
    status: str
    asof: str
    source: str                      # what was read / run to produce this
    value: str | None = None         # the headline number, already formatted
    detail: str = ""                 # one sentence of context
    reason: str | None = None        # why unmeasured — required when unmeasured
    settle: str | None = None        # the command that would settle it
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

    @property
    def measured(self) -> bool:
        return self.status == MEASURED


@dataclass
class MeasurementSet:
    items: list[Measurement] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)

    def by_key(self, key: str) -> Measurement | None:
        for m in self.items:
            if m.key == key:
                return m
        return None

    def in_section(self, section: str) -> list[Measurement]:
        return [m for m in self.items if m.section == section]

    @property
    def measured(self) -> list[Measurement]:
        return [m for m in self.items if m.measured]

    @property
    def unmeasured(self) -> list[Measurement]:
        return [m for m in self.items if not m.measured]

    def verdict(self) -> str:
        """`ok` | `all-unmeasured` | `empty`.

        `empty` and `all-unmeasured` are separated deliberately: a registry that
        produced no rows at all is a different defect from a machine where every
        probe failed, and collapsing them would hide the first behind the second.
        """
        if not self.items:
            return "empty"
        if not self.measured:
            return "all-unmeasured"
        return "ok"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class Unmeasurable(Exception):
    """Raised by a measurer that cannot answer. Carries the reason verbatim."""


def _const(path: Path, name: str):
    """Read a module-level literal constant out of a file by PARSING it.

    🔴 PARSE, DO NOT IMPORT. `scripts/tests/test_rules_size.py` owns `MAX_BYTES`
    and `scripts/tests/test_skill_tiers.py` owns `TIER_A_CEILING_CHARS`; reading
    them from their owners is what makes this module a reader of those numbers
    rather than a second author of them.

    Importing was the first cut and it was measurably worse: a test module that
    imports `pytest` fails to import under a bare interpreter, so the ceiling
    came back UNREAD on a build where the file was present and perfectly
    readable. The absence was honest, and it was an absence caused by the
    measurement technique rather than by the tree — which is the worst kind,
    because it looks like a finding. Parsing has no import graph and no side
    effects.

    🔴 A NAME BOUND MORE THAN ONCE IS `UNMEASURABLE`, NOT "THE FIRST ONE". The
    first cut returned the first top-level binding and ignored every later one,
    so `X = 0` followed later by `X = 24_000` reported **0**, and `X = 1;
    X += 23_999` reported **1**. Both are a WRONG NUMBER rendered as a measured
    fact — the failure this whole page exists to prevent — and both look exactly
    like a correct read. The premise of parsing is that a source this module
    cannot understand yields an ABSENCE; a name whose value depends on execution
    order is a source this module cannot understand. Tuple unpacking
    (`A, B = 1, 2`) counts as a binding for the same reason: it is a real
    definition of the name that `literal_eval` cannot evaluate, and silently
    walking past it would resurrect the same defect one syntax form over.
    """
    if not path.is_file():
        raise Unmeasurable(f"{path} does not exist")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise Unmeasurable(f"{path.name} could not be parsed: {exc!r}") from exc

    def _binds(target) -> bool:
        """Does this assignment target bind `name`? Recurses into unpacking."""
        if isinstance(target, ast.Name):
            return target.id == name
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(_binds(el) for el in target.elts)
        if isinstance(target, ast.Starred):
            return _binds(target.value)
        return False

    values: list = []          # the literal-evaluable bindings, in source order
    bindings = 0               # EVERY top-level binding, evaluable or not
    for node in tree.body:
        if isinstance(node, ast.AugAssign):
            if _binds(node.target):
                bindings += 1
            continue
        if isinstance(node, ast.AnnAssign) and node.value is None:
            continue           # `X: int` annotates; it does not bind
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, ast.AnnAssign) else [])
        if not any(_binds(t) for t in targets):
            continue
        bindings += 1
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            values.append(node.value)

    if bindings > 1:
        raise Unmeasurable(
            f"{path.name} binds {name} {bindings} times at module level — this "
            "module reads a number by PARSING, which cannot tell you which "
            "binding wins at run time, and reporting the first one would render "
            "a stale value as a measured fact"
        )
    if not values:
        if bindings:
            raise Unmeasurable(
                f"{path.name} binds {name} in a form this module cannot evaluate "
                "(unpacking or an augmented assignment), so its value is unread")
        raise Unmeasurable(
            f"{path.name} no longer defines {name} — this module reads that number "
            "from its owner and must not restate it"
        )
    try:
        return ast.literal_eval(values[0])
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
        raise Unmeasurable(
            f"{path.name}:{name} is not a literal ({exc})") from exc


def _run(argv: list[str], cwd: Path | None = None, timeout: int = 120):
    """Run a command and return (rc, stdout, stderr).

    🔴 stdout and stderr are captured SEPARATELY, never merged. `claude/RULES.md`
    → zsh trap (d): a merged capture cannot tell you which stream a line came
    from, and this module branches on stdout content.
    """
    exe = shutil.which(argv[0])
    if exe is None:
        raise Unmeasurable(f"`{argv[0]}` is not on PATH")
    try:
        proc = subprocess.run(
            [exe] + argv[1:],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
    except subprocess.TimeoutExpired:
        raise Unmeasurable(f"`{' '.join(argv)}` did not finish in {timeout}s")
    except OSError as exc:
        raise Unmeasurable(f"`{' '.join(argv)}` could not be run: {exc}")
    return proc.returncode, proc.stdout, proc.stderr


def _bytes_of(path: Path) -> int:
    if not path.is_file():
        raise Unmeasurable(f"{path} does not exist")
    return len(path.read_bytes())


def _pct(part: int, whole: int) -> str:
    if whole <= 0:
        return "n/a"
    return f"{100.0 * part / whole:.1f}%"


# --------------------------------------------------------------------------- #
# The environment a measurement runs against
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Env:
    """Everything a measurer is allowed to look at, injected rather than found.

    Tests drive the whole registry against a synthetic tree by constructing an
    `Env` pointing at `tmp_path`. Nothing here reads a global; nothing here has
    a default that silently falls back to the operator's real machine.
    """

    repo: Path
    home: Path
    claude_dir: Path
    index_store: Path
    allow_systemd: bool = True
    #: 🔴 MAY A MEASURER LEAVE THIS MACHINE? Exactly one does (`m_branch_protection`
    #: shells `gh api`), and it was reached by every test that called `take()` —
    #: five outbound GitHub calls per suite run, each with a 45s ceiling and no
    #: aggregate bound, inside a check that BLOCKS a merge. A gate whose runtime
    #: depends on a third party's availability is a flake source, and a flaky
    #: required gate trains everyone to click through it. Tests set this False;
    #: the row then renders UNMEASURED with the reason, which is the same shape
    #: an offline build already produced and is therefore already covered.
    allow_network: bool = True

    @classmethod
    def live(cls, repo: Path | None = None) -> "Env":
        repo = Path(repo or Path(__file__).resolve().parents[2])
        home = Path(os.path.expanduser("~"))
        claude_dir = home / ".claude"
        return cls(
            repo=repo,
            home=home,
            claude_dir=claude_dir,
            index_store=claude_dir / "analyze-service-index",
        )


# --------------------------------------------------------------------------- #
# Measurers
#
# Each one returns a *dict* of the Measurement fields it can fill, or raises
# Unmeasurable with a human reason. The registry below wraps them so a raise
# becomes an UNMEASURED ROW rather than a missing one.
# --------------------------------------------------------------------------- #


def m_rules_bytes(env: Env) -> dict:
    owner = env.repo / "scripts" / "tests" / "test_rules_size.py"
    ceiling = int(_const(owner, "MAX_BYTES"))
    headroom_floor = int(_const(owner, "MIN_HEADROOM_BYTES"))
    rules = env.repo / "claude" / "RULES.md"
    size = _bytes_of(rules)
    headroom = ceiling - size
    return dict(
        value=f"{size:,} B of {ceiling:,} B",
        detail=(
            f"{_pct(size, ceiling)} of the ceiling; {headroom:,} B headroom "
            f"against a {headroom_floor:,} B minimum. Loaded into EVERY session "
            "and concatenated into opencode's AGENTS.md, so it is paid twice."
        ),
        source="claude/RULES.md, sized against MAX_BYTES in scripts/tests/test_rules_size.py",
        columns=("what", "bytes"),
        rows=(
            ("RULES.md (always-on core)", f"{size:,}"),
            ("hard ceiling (MAX_BYTES)", f"{ceiling:,}"),
            ("headroom now", f"{headroom:,}"),
            ("minimum headroom (MIN_HEADROOM_BYTES)", f"{headroom_floor:,}"),
        ),
    )


def m_rules_archive_bytes(env: Env) -> dict:
    archive = env.repo / "claude" / "RULES-ARCHIVE.md"
    size = _bytes_of(archive)
    core = _bytes_of(env.repo / "claude" / "RULES.md")
    ratio = size / core if core else 0
    return dict(
        value=f"{size:,} B",
        detail=(
            f"The eviction target for RULES.md — {ratio:.1f}x the core it serves, "
            "and it costs ZERO per session because nothing auto-loads it. That "
            "asymmetry is the whole design: the imperative stays in the core, the "
            "worked incident moves here."
        ),
        source="claude/RULES-ARCHIVE.md",
    )


def m_skill_listing(env: Env) -> dict:
    lib = env.repo / "scripts" / "lib"
    if not (lib / "skill_tiers.py").is_file():
        raise Unmeasurable(f"{lib / 'skill_tiers.py'} does not exist")
    sys.path.insert(0, str(lib))
    try:
        import skill_tiers  # noqa: PLC0415
    except Exception as exc:
        raise Unmeasurable(f"scripts/lib/skill_tiers.py did not import: {exc!r}")
    finally:
        if sys.path and sys.path[0] == str(lib):
            sys.path.pop(0)

    try:
        skills = skill_tiers.shipped_skills()
        ledger = skill_tiers.load_ledger()
    except Exception as exc:
        raise Unmeasurable(f"the tier ledger could not be read: {exc!r}")
    if not skills:
        raise Unmeasurable(
            "skill_tiers.shipped_skills() returned NOTHING — an empty set is not "
            "evidence of an empty tree, it is evidence the discovery broke"
        )

    total = skill_tiers.devrc_listing_chars(ledger, skills)
    tier_a = skill_tiers.tier_a_names(ledger)
    tier_b = skill_tiers.tier_b_names(ledger)
    a_chars = skill_tiers.tier_a_chars(ledger, skills)
    cap = int(skill_tiers.PER_ENTRY_CAP_CHARS)

    owner = env.repo / "scripts" / "tests" / "test_skill_tiers.py"
    try:
        ceiling_note = f"{int(_const(owner, 'TIER_A_CEILING_CHARS')):,} chars (TIER_A_CEILING_CHARS)"
    except Unmeasurable as exc:
        # A partial absence INSIDE a measured row still says so, in place. The
        # row is genuinely measured — the listing was counted — so demoting the
        # whole thing to UNMEASURED would hide the numbers that DID come back.
        ceiling_note = f"UNREAD — {exc}"

    costliest = skill_tiers.costliest(ledger, skills, n=5)
    rows = [
        ("skills shipped (in-tree + mkOutOfStoreSymlink)", str(len(skills))),
        ("tier A — full description in the always-on listing", str(len(tier_a))),
        ("tier B — `name-only`, still /name-invocable", str(len(tier_b))),
        ("devrc's listing cost under this ledger", f"{total:,} chars"),
        ("tier-A block cost", f"{a_chars:,} chars"),
        ("tier-A ratchet", ceiling_note),
        ("per-entry cap (upstream, not devrc's)", f"{cap:,} chars"),
    ]
    rows += [(f"costliest tier-A entry: {n}", f"{c:,} chars") for n, c in costliest[:3]]

    return dict(
        value=f"{len(skills)} skills, {total:,} chars",
        detail=(
            "Charged on EVERY session before you type anything. The budget is "
            "floor(contextWindow x zx(model) x 0.01) CHARACTERS — never quote a "
            "bare multiple of 'the budget' without naming the model. On overflow "
            "Claude Code drops descriptions starting with the least-invoked "
            "skills, with no error: the routing keywords vanish silently."
        ),
        source="scripts/lib/skill_tiers.py (the ledger's own parser), ratchet from scripts/tests/test_skill_tiers.py",
        columns=("what", "measured"),
        rows=tuple(rows),
    )


def m_skill_inventory(env: Env) -> dict:
    """The SHALLOW-BUT-COMPLETE inventory: name, one line, where it lives.

    Deliberately renders the skill's own description rather than a paraphrase.
    The skill is the operating surface; this page ROUTES to it. A second
    description here would be a fourth documentation surface competing with
    CLAUDE.md, the skills, their reference/ dirs and the index store.
    """
    lib = env.repo / "scripts" / "lib"
    sys.path.insert(0, str(lib))
    try:
        import skill_tiers  # noqa: PLC0415
        skills = skill_tiers.shipped_skills()
        ledger = skill_tiers.load_ledger()
    except Exception as exc:
        raise Unmeasurable(f"the shipped-skill set could not be read: {exc!r}")
    finally:
        if sys.path and sys.path[0] == str(lib):
            sys.path.pop(0)
    if not skills:
        raise Unmeasurable("no skills discovered — the discovery is broken, not the tree")

    rows = []
    for name in sorted(skills):
        rel, desc = skills[name]
        tier = ledger.get(name, {}).get("tier", "?")
        first = desc.split(". ")[0].strip()
        if len(first) > 150:
            first = first[:147].rstrip() + "..."
        rows.append((f"/{name}", tier, first, rel))
    return dict(
        value=f"{len(rows)} skills",
        detail=(
            "The inventory is deliberately shallow. Each row is name, tier, the "
            "skill's OWN first sentence, and the path — load the skill to operate "
            "the subsystem. This page routes; it does not restate."
        ),
        source="claude/skills/*/SKILL.md front-matter + claude/skill-tiers.json",
        columns=("skill", "tier", "what it is (its own words)", "path"),
        rows=tuple(rows),
    )


def m_memory_index(env: Env) -> dict:
    """MEMORY.md is per-project LOCAL state, never committed.

    The caps come from `scripts/memory-audit.py`, which owns them.
    """
    audit = env.repo / "scripts" / "memory-audit.py"
    target = int(_const(audit, "TARGET"))
    hard = int(_const(audit, "HARD"))

    # 🔴 THE CAP BINDS PER PROJECT, so the number that matters is the LARGEST
    # index on this machine, not "the one for this checkout". A worktree's slug
    # differs from its base clone's, and silently reporting whichever index
    # happened to sort first would answer a different question than the one the
    # cap asks — the exact substitution this page exists to avoid.
    found = sorted((env.claude_dir / "projects").glob("*/memory/MEMORY.md"))
    if not found:
        raise Unmeasurable(
            f"no MEMORY.md under {env.claude_dir / 'projects'} — the auto-memory "
            "index is per-project LOCAL state and this machine has none"
        )
    sized = sorted(((p, _bytes_of(p)) for p in found), key=lambda t: -t[1])
    biggest, size = sized[0]
    verdict = "OK" if size <= target else ("WARN" if size < hard else "OVER-HARD-CAP")
    rows = [
        ("largest index on this host", f"{size:,}"),
        ("soft target (TARGET)", f"{target:,}"),
        ("hard load cap (HARD)", f"{hard:,}"),
        ("verdict for the largest", verdict),
        ("project indexes on this host", str(len(sized))),
    ]
    rows += [
        (f"  {p.parent.parent.name}", f"{n:,}") for p, n in sized[:6]
    ]
    return dict(
        value=f"{size:,} B of {hard:,} B hard cap",
        detail=(
            f"soft target {target:,} B, hard cap {hard:,} B; largest index on this "
            f"host is {verdict}. 🔴 Content past the hard cap is DROPPED ON LOAD, "
            "silently — the index does not error, it just carries less than it "
            "says. The cap binds PER PROJECT, so the largest index is the number "
            "that matters; each project's is listed below."
        ),
        source="every local ~/.claude/projects/*/memory/MEMORY.md, caps read from scripts/memory-audit.py",
        columns=("project / quantity", "bytes"),
        rows=tuple(rows),
    )


def m_pytest_floors(env: Env) -> dict:
    """Per-target collected-test floors, from the runner that OWNS them.

    `--check-floors` runs GUARD 3a only: it validates the two-way pin between
    TARGET_FLOORS and the target list and prints the table. Milliseconds, no
    pytest.
    """
    runner = env.repo / "scripts" / "run-tests.sh"
    if not runner.is_file():
        raise Unmeasurable(f"{runner} does not exist")
    rc, out, err = _run(["bash", str(runner), "--check-floors"], cwd=env.repo)
    if "floor" not in out:
        raise Unmeasurable(
            f"`run-tests.sh --check-floors` printed no floor table (rc={rc}); "
            f"stderr head: {err.strip()[:200]!r}"
        )
    rows = []
    global_floor = None
    target_count = None
    for line in out.splitlines():
        s = line.strip()
        m = re.match(r"^floor\s+(\d+)\s+(\S+)$", s)
        if m:
            rows.append((m.group(2), m.group(1)))
            continue
        m = re.search(r"GLOBAL floor \(sum over the hermetic set\)\s*=\s*(\d+)", s)
        if m:
            global_floor = int(m.group(1))
        m = re.search(r"all (\d+) floor\(s\) pin a known target", s)
        if m:
            target_count = int(m.group(1))
    if not rows or global_floor is None:
        raise Unmeasurable(
            "the floor table parsed to nothing — PARSING output makes its FORMAT "
            "a dependency; re-read scripts/run-tests.sh --check-floors by hand"
        )
    return dict(
        value=f"{global_floor:,} collected tests (floor), {len(rows)} targets",
        detail=(
            "There is NO hand-written total. The global floor is the SUM of the "
            "per-target floors, and each floor is a function of a measurement — "
            "`m - min(50, max(1, m/20))`. The single literal it replaced took "
            "eleven values across eight PRs in one day. Resolve a conflict here "
            "by re-running the gate and copying the number it PRINTS, never by "
            "arithmetic on the two sides."
            + (f" Two-way pin: {target_count} floors, {target_count} targets."
               if target_count else "")
        ),
        source="scripts/run-tests.sh --check-floors (GUARD 3a; the runner owns TARGET_FLOORS)",
        columns=("target", "floor"),
        rows=tuple(rows),
    )


def m_node_floors(env: Env) -> dict:
    runner = env.repo / "scripts" / "run-node-tests.sh"
    if not runner.is_file():
        raise Unmeasurable(f"{runner} does not exist")
    text = runner.read_text(encoding="utf-8", errors="replace")
    block = re.search(r"^SUITES=\((.*?)^\)", text, re.S | re.M)
    if not block:
        raise Unmeasurable(
            "the SUITES=( ... ) block was not found in scripts/run-node-tests.sh — "
            "an empty match set means the pattern is wrong, not that there are no suites"
        )
    rows = []
    for line in block.group(1).splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        # `path|fileFloor` or `path|fileFloor|testFloor` — both shapes have
        # shipped, so parse the trailing field COUNT rather than assuming one.
        m = re.match(r'^"([^"|]+)\|(\d+)(?:\|(\d+))?"$', s)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3) or "-"))
    if not rows:
        raise Unmeasurable(
            "the SUITES block parsed to zero entries — the entry FORMAT is a "
            "dependency this measurer did not pin; re-read the block by hand"
        )
    files = sum(int(c) for _, c, _ in rows)
    tests = sum(int(t) for _, _, t in rows if t.isdigit())
    return dict(
        value=f"{files} test files / {tests} tests (floors) across {len(rows)} suites",
        detail=(
            "The node tier is a DIFFERENT RUNNER with a different floor shape — "
            "it floors the count of `.test.mjs` FILES as well as tests, because "
            "`node --test <dir>` silently yields a bogus count of one. Its global "
            "floor is also derived from the per-suite entries, never written: the "
            "hand-maintained global it replaced sat BELOW the sum of the "
            "per-suite floors already beside it, i.e. two numbers disagreeing "
            "about the same thing."
        ),
        source="SUITES=() in scripts/run-node-tests.sh",
        columns=("suite", "file floor", "test floor"),
        rows=tuple(rows),
    )


def m_gate_tiers(env: Env) -> dict:
    """The two gate TIERS — the same suites, two different environments.

    This is the measurement that most rewards being taken rather than asserted:
    which contexts branch protection requires is a live GitHub fact this
    generator cannot see offline, so that half renders as its own row.
    """
    gate = env.repo / "scripts" / "gate.sh"
    if not gate.is_file():
        raise Unmeasurable(f"{gate} does not exist")
    flake = env.repo / "flake.nix"
    if not flake.is_file():
        raise Unmeasurable(f"{flake} does not exist")
    ftext = flake.read_text(encoding="utf-8", errors="replace")
    # The derivation NAME is the reliable token: `checks.${system} = { … }` is
    # an interpolated attrset, so the attribute path is not literally in the file.
    checks = sorted(set(re.findall(r'runCommandLocal\s+"devrc-([a-z0-9-]+)"', ftext)))
    gtext = gate.read_text(encoding="utf-8", errors="replace")
    exits = sorted(set(re.findall(r"^#\s+(\d+)\s*=\s*(.+)$", gtext, re.M)))
    #: 🔴 THE TIERS ARE A STRUCTURE, SO THE COUNT IS DERIVED FROM IT. This layer's
    #: charter is that no quantity is TYPED — a literal `2` here would be the
    #: first hand-maintained number on a page built to have none, and it would
    #: survive a third tier being added right below it.
    tiers = (
        ("dev-host tier", "nix develop --impure --command bash scripts/gate.sh --tier both"),
        ("sandbox tier", "nix build .#checks.x86_64-linux.{pytests,nodetests}"),
    )
    rows = list(tiers) + [
        ("sandbox source", "a `cp -r ${./.}` store copy with NO .git — repo-local git facts evaluate differently"),
        ("gate.sh could-not-vouch code", "90 — status/content disagreement or a truncated run; NOT 'the tests failed'"),
    ]
    rows += [(f"gate.sh exit {code}", desc.strip()) for code, desc in exits if code in {"0", "1", "2", "90"}]
    return dict(
        value=(f"{len(tiers)} tiers, {len(checks)} flake checks" if checks
               else f"{len(tiers)} tiers"),
        detail=(
            "🔴 TWO TIERS, NOT TWO SPELLINGS OF ONE. `scripts/gate.sh` runs the "
            "runners on the DEV HOST; it never invokes `nix build`. The sandbox "
            "tier builds from a store copy with no `.git`, so anything keyed on "
            "the repo being a git checkout evaluates differently there. Four "
            "consecutive green dev-host runs preceded a red sandbox check on "
            "#773. Name the tier AND the base sha in any claim that a merge is safe."
            + (f" flake checks discovered: {', '.join(checks)}." if checks else "")
        ),
        source="scripts/gate.sh header + flake.nix checks",
        columns=("tier / fact", "what it is"),
        rows=tuple(rows),
    )


def m_branch_protection(env: Env) -> dict:
    """Which checks BLOCK a merge — a live GitHub fact, deliberately probed.

    This measurer is the page's worked example of `UNMEASURED`: offline, or
    without `gh` authenticated, it cannot answer, and it says so with the exact
    command that would settle it rather than rendering a reassuring blank.

    🔴 IT IS THE ONLY MEASURER THAT LEAVES THIS MACHINE, so it is the only one
    gated on `env.allow_network`. See that field for why the suite turns it off.
    """
    if not env.allow_network:
        raise Unmeasurable(
            "network probing was disabled for this build (--no-network), so the "
            "live branch-protection fact was not read — this row is an absence, "
            "never an 'unprotected'")
    rc, out, _ = _run(["git", "-C", str(env.repo), "remote", "get-url", "origin"])
    if rc != 0 or not out.strip():
        raise Unmeasurable("no `origin` remote — cannot name the repository to ask about")
    url = out.strip()
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        raise Unmeasurable(f"could not parse an owner/repo out of the origin URL")
    slug = f"{m.group(1)}/{m.group(2)}"
    api = f"/repos/{slug}/branches/main/protection"
    rc, out, err = _run(
        ["gh", "api", api, "--jq", ".required_status_checks.contexts, .enforce_admins.enabled"],
        cwd=env.repo, timeout=45,
    )
    if rc != 0:
        raise Unmeasurable(
            "`gh api` could not read branch protection "
            f"(rc={rc}): {err.strip().splitlines()[0][:160] if err.strip() else 'no stderr'}"
        )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        raise Unmeasurable("`gh api` returned an EMPTY body — an empty result is not a clean result")
    try:
        contexts = json.loads(lines[0])
    except Exception:
        raise Unmeasurable("required_status_checks.contexts did not parse as JSON")
    admins = lines[1].strip() if len(lines) > 1 else "unknown"
    return dict(
        value=f"{len(contexts)} required context(s)",
        detail=(
            "🔴 A one-element `contexts` list reads as 'blocked' at a glance. "
            "Check the LIST, not that the key exists — this repo shipped a day "
            "where nodetests alone was required, which a Python-only PR could "
            "not fail. `strict` is deliberately FALSE, so a green check is a "
            "claim about the PR's BRANCH, never about the tree its merge creates."
            f" enforce_admins={admins}."
        ),
        source=f"gh api {api}",
        columns=("required context",),
        rows=tuple((c,) for c in contexts) or (("(none — nothing blocks a merge)",),),
    )


def m_hooks(env: Env) -> dict:
    """What the agent may DO — the blocking-hook inventory, shipped vs registered.

    Two counts on purpose. The repo SHIPS hook scripts; a host REGISTERS them in
    a per-host, unmanaged `settings.json`. Those are independent facts, and
    reporting one as the other is how a hook reads as active while firing nowhere.
    """
    shipped_dir = env.repo / "scripts" / "claude-hooks"
    if not shipped_dir.is_dir():
        raise Unmeasurable(f"{shipped_dir} does not exist")
    shipped = sorted(
        p.name for p in shipped_dir.iterdir()
        if p.is_file() and p.suffix in {".py", ".sh"} and not p.name.startswith("_")
    )
    if not shipped:
        raise Unmeasurable("no hook scripts found — an empty listing is the failure, not the all-clear")

    registered: list[tuple[str, str, str]] = []
    settings = env.claude_dir / "settings.json"
    reg_note = ""
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            for event, matchers in (data.get("hooks") or {}).items():
                for entry in matchers:
                    for hook in entry.get("hooks", []):
                        cmd = str(hook.get("command", ""))
                        leaf = cmd.strip().split("/")[-1].split()[0] if cmd.strip() else "(empty)"
                        registered.append((event, entry.get("matcher", "*"), leaf))
        except Exception as exc:
            reg_note = f" (settings.json unreadable: {exc!r})"
    else:
        reg_note = " (this host has no ~/.claude/settings.json)"

    # 🔴 THE REFUSAL LIST IS MEASURED, NOT ENUMERATED IN PROSE. The hand-written
    # version named four refusals out of the guard's real policy and read as the
    # whole list — it omitted `git stash`, which is 🔴 in RULES.md and is the one
    # this repo has an incident for. A sentence that names some of a set and
    # reads as all of it is the "description claims coverage the body does not
    # provide" shape, in a document. Counted from the guard's own policy block;
    # if that block cannot be parsed the count is simply absent, because this row
    # is really about SHIPPED-vs-REGISTERED and an unparseable list must not take
    # the whole row down with it.
    guard = shipped_dir / "bash-guard.py"
    refusal_note = ""
    if guard.is_file():
        gtext = guard.read_text(encoding="utf-8", errors="replace")
        start = gtext.find("The list today:")
        block = gtext[start:start + 4000] if start >= 0 else ""
        refusals = re.findall(r"^\s+-\s+(\S.*?)\s+->", block, re.M)
        if refusals:
            refusal_note = (
                f" `bash-guard.py` is the blocking one and its policy block lists "
                f"{len(refusals)} refusals — among them `git add -A`, `git reset "
                "--hard`, `git stash` (the stash stack is repo-GLOBAL, so a "
                "worktree gives no isolation), `git commit` on a main branch, "
                "`pkill -f`, and publishing a secret or a public IP.")

    rows = [("SHIPPED in scripts/claude-hooks/", "", n) for n in shipped]
    rows += [("REGISTERED", f"{e} / {m}", leaf) for e, m, leaf in sorted(registered)]
    return dict(
        value=f"{len(shipped)} shipped, {len(registered)} registered on this host",
        detail=(
            "🔴 SHIPPED and REGISTERED are independent. `~/.claude/settings.json` "
            "is per-host and unmanaged by design, so a hook can be in the tree and "
            "fire on neither machine."
            + refusal_note
            + " A hook that only NUDGES cannot "
            "stop anything — read the exit code it returns before calling it a guard."
            + reg_note
        ),
        source="scripts/claude-hooks/ (tree) + ~/.claude/settings.json (this host)",
        columns=("kind", "event / matcher", "hook"),
        rows=tuple(rows),
    )


def m_timers(env: Env) -> dict:
    if not env.allow_systemd:
        raise Unmeasurable("systemd probing was disabled for this build (--no-systemd)")
    rc, out, err = _run(["systemctl", "--user", "list-timers", "--all", "--no-pager", "--no-legend"])
    if rc != 0:
        raise Unmeasurable(f"`systemctl --user list-timers` failed (rc={rc}): {err.strip()[:160]}")
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        unit = next((p for p in parts if p.endswith(".timer")), None)
        if not unit:
            continue
        idx = parts.index(unit)
        activates = parts[idx + 1] if idx + 1 < len(parts) else ""
        nxt = " ".join(parts[:2]) if parts[0] != "-" else "(not scheduled)"
        rows.append((unit, nxt, activates))
    if not rows:
        raise Unmeasurable(
            "systemctl listed ZERO timers — a bare zero from a scan that walked "
            "nothing is the failure, not the all-clear"
        )
    dead = [r for r in rows if r[1] == "(not scheduled)"]
    return dict(
        value=f"{len(rows)} user timers, {len(dead)} not scheduled",
        detail=(
            "The unattended half of the system. Nothing runs `ship.sh` on a "
            "schedule — `drift-check.timer` is the passive deadman that notices "
            "when a host has stopped receiving changes. Timers with no next "
            "elapse are counted, not hidden: a retired timer left installed is a "
            "fact about the host."
        ),
        source="systemctl --user list-timers --all",
        columns=("timer", "next", "activates"),
        rows=tuple(rows),
    )


def m_index_store(env: Env) -> dict:
    """The subsystem index store — LOCAL state, read through its OWN parser.

    🔴 Scope names are real client/repo identifiers. They are read at run time
    from `~/.claude/analyze-service-index/` and never appear in this file. The
    `--sanitize` build swaps them for synthetic stand-ins.
    """
    lib = env.repo / "scripts" / "lib"
    if not (lib / "subsystem_recall.py").is_file():
        raise Unmeasurable(f"{lib / 'subsystem_recall.py'} does not exist")
    if not env.index_store.is_dir():
        raise Unmeasurable(
            f"no index store at {env.index_store} — it is per-machine local "
            "state, so a host without one is a state, not a defect"
        )
    sys.path.insert(0, str(lib))
    try:
        import subsystem_recall  # noqa: PLC0415
        _, idx = subsystem_recall.load_store(env.index_store, verb="present")
    except Exception as exc:
        raise Unmeasurable(f"the index store did not load through its own parser: {exc!r}")
    finally:
        if sys.path and sys.path[0] == str(lib):
            sys.path.pop(0)

    scopes = list(idx.scopes)
    rows = []
    total = 0
    for scope in scopes:
        n = len(idx.entries(scope))
        total += n
        rows.append((scope, str(n)))
    if not scopes:
        raise Unmeasurable("the store parsed to ZERO scopes — that is a broken read, not an empty store")
    return dict(
        value=f"{total} entries across {len(scopes)} scopes",
        detail=(
            "What a past session learned about a subsystem, keyed by scope and "
            "recalled by `/analyze-service`. It costs nothing per session — it is "
            "pulled on demand, which is why it can be large where RULES.md cannot. "
            "It is LOCAL state on each machine and is not in this repo."
        ),
        source="~/.claude/analyze-service-index, read via scripts/lib/subsystem_recall.load_store()",
        columns=("scope", "entries"),
        rows=tuple(rows),
    )


def m_telemetry_sources(env: Env) -> dict:
    """How many per-source DIRECTORIES the collector tree holds.

    Counted from the collector's own layout rather than from a list in a
    document: a source directory that is added and never written down would be
    invisible to the document and is visible here.

    🔴 THE LABEL SAYS DIRECTORIES BECAUSE DIRECTORIES ARE WHAT IT COUNTS. It
    used to render "N collector sources", which was a different and smaller
    number than the pipeline actually has — shell and terminal-multiplexer
    events are emitted by a FILE (`scripts/collector/emit`), not a directory, so
    they were outside the count while the label claimed to be counting sources.
    Two other places on the same page said otherwise: this row's own `detail`
    named six kinds against a value of five, and the skill inventory rendered a
    description naming nine. Measuring the source SET properly would mean a
    roster this tree does not have; naming what was measured costs nothing and
    stops the page contradicting itself.
    """
    collector = env.repo / "scripts" / "collector"
    if not collector.is_dir():
        raise Unmeasurable(f"{collector} does not exist")
    sources = sorted(
        d.name for d in collector.iterdir()
        if d.is_dir() and not d.name.startswith((".", "_")) and d.name != "tests"
    )
    if not sources:
        raise Unmeasurable(
            "the collector holds no per-source directory — an empty listing is "
            "the failure, not the all-clear"
        )
    rows = []
    for name in sources:
        d = collector / name
        py = len(list(d.rglob("*.py")))
        has_tests = (d / "tests").is_dir()
        rows.append((name, str(py), "yes" if has_tests else "no"))
    emit = collector / "emit"
    return dict(
        value=f"{len(sources)} per-source directories",
        detail=(
            "Shell, terminal multiplexer, keyboard, window manager, browser and "
            "agent transcripts, landing in a columnar store with dashboards over "
            "it. Each source carries a DEADMAN, because a source that stops "
            "reporting looks exactly like a quiet one — this is the same "
            "silent-zero shape the rest of the system is built against, in the "
            "data layer. The pipeline is what makes a claim like 'this tool is "
            "dead' measurable instead of impressionistic. "
            "🔴 THIS COUNTS DIRECTORIES, NOT SOURCES, and the two differ"
            + (": shell and terminal-multiplexer events are emitted by "
               "`scripts/collector/emit`, a FILE, so they are outside the number "
               "above." if emit.is_file() else
               " wherever a source is emitted by something other than a directory.")
            + " The label states what was counted rather than what one would "
            "like to have counted."
        ),
        source="per-source directories under scripts/collector/",
        columns=("source", ".py files", "has its own suite"),
        rows=tuple(rows),
    )


def m_managed_paths(env: Env) -> dict:
    home_nix = env.repo / "nix" / "home.nix"
    if not home_nix.is_file():
        raise Unmeasurable(f"{home_nix} does not exist")
    text = home_nix.read_text(encoding="utf-8", errors="replace")
    targets = re.findall(r'home\.file\."([^"]+)"', text)
    if not targets:
        raise Unmeasurable(
            "no `home.file.\"...\"` targets matched — the deploy idiom has changed "
            "shape; an empty match set is not an empty deploy"
        )
    out_of_store = re.findall(r'home\.file\."([^"]+)"\.source\s*=\s*config\.lib\.file\.mkOutOfStoreSymlink', text)
    return dict(
        value=f"{len(targets)} managed paths, {len(out_of_store)} mutable",
        detail=(
            "🔴 MERGED IS NOT DEPLOYED. Every one of these changes only on a "
            "`home-manager switch` — `git pull` changes nothing nix manages. That "
            "git-immunity is deliberate (a concurrent `git checkout` cannot swap "
            "deployed code out mid-verification) and is exactly what makes it easy "
            "to trip on. The mutable ones are `mkOutOfStoreSymlink`: the working "
            "copy IS the live file. `readlink -f` is the only arbiter of which is "
            "which — terminates in the repo means live, terminates in /nix/store "
            "means it needs a switch."
        ),
        source="nix/home.nix",
        columns=("kind", "count"),
        rows=(
            ("home.file targets", str(len(targets))),
            ("mkOutOfStoreSymlink (live, no switch needed)", str(len(out_of_store))),
            ("store copies (need a switch)", str(len(targets) - len(out_of_store))),
        ),
    )


def m_drift_ladder(env: Env) -> dict:
    """The rc ladder, parsed out of the deadman's own header comment."""
    drift = env.repo / "scripts" / "drift-check.sh"
    if not drift.is_file():
        raise Unmeasurable(f"{drift} does not exist")
    text = drift.read_text(encoding="utf-8", errors="replace")
    start = text.find("EXIT CODES")
    if start < 0:
        raise Unmeasurable(
            "no EXIT CODES banner in scripts/drift-check.sh — the header's shape "
            "is a dependency this measurer did not pin"
        )
    # The block runs to the NEXT banner, and the banner-to-banner span contains
    # blank comment lines, so it cannot be delimited by one.
    nxt = text.find("# ──", start)
    block = text[start:nxt if nxt > start else len(text)]

    rows = []
    current = None
    for line in block.splitlines():
        m = re.match(r"^#\s{2,6}(\d{1,2})\s{2,}(\S.*)$", line)
        if m:
            current = [m.group(1), m.group(2).strip()]
            rows.append(current)
        elif current is not None and re.match(r"^#\s{6,}\S", line):
            current[1] += " " + line.lstrip("# ").strip()
        elif current is not None and not line.strip("# "):
            current = None
    rows = [(c, re.sub(r"\s+", " ", d)[:420]) for c, d in rows]
    if not rows:
        raise Unmeasurable("the EXIT CODES block parsed to zero codes")

    reserved = re.search(r"RESERVED-TO-SHIP:\s*([\d ]+)", text)
    # 🔴 THE LABEL NAMES THE BANNER, NOT THE SCRIPT'S RETURN SET. This parses the
    # EXIT CODES block, which documents the DRIFT codes and starts at 2. rc 0
    # (clean) and rc 1 are real returns and are not in it, so "N exit codes" was
    # a count of one thing wearing the name of another — and a reader deciding
    # whether their rc is covered would have looked for it in the wrong set.
    banner_lo = min(int(c) for c, _ in rows)
    return dict(
        value=f"{len(rows)} exit codes documented in the banner",
        detail=(
            f"The banner starts at rc {banner_lo}: rc 0 (no drift) and rc 1 are "
            "real returns and are deliberately not in it, so the number above "
            "counts documented DRIFT codes, not everything this script can "
            "return. "
            "A deadman that REPORTS and never fixes — it may `git fetch`, and a "
            "static allowlist scanner in its own test suite proves it can run no "
            "mutating git subcommand, including through `ssh`. 🔴 rc 18 is the "
            "one to understand: a scope that could never be MEASURED set no code, "
            "so it escalated NEVER and the run read clean while rc 17 was "
            "structurally unable to fire. UNMEASURED now rides a consecutive-run "
            "ladder, per (host, scope), reset the moment it measures — and `repo "
            "ABSENT` never escalates at any count, because that is a supported state."
            + (f" Codes reserved to ship.sh: {reserved.group(1).strip()}." if reserved else "")
        ),
        source="the EXIT CODES block in scripts/drift-check.sh (pinned against ship.sh by test_drift_check.py)",
        columns=("rc", "meaning"),
        rows=tuple(rows),
    )


def m_repo_head(env: Env) -> dict:
    rc, head, _ = _run(["git", "-C", str(env.repo), "rev-parse", "--short", "HEAD"])
    if rc != 0:
        raise Unmeasurable("`git rev-parse HEAD` failed — is this a git checkout?")
    rc2, branch, _ = _run(["git", "-C", str(env.repo), "branch", "--show-current"])
    rc3, dirty, _ = _run(["git", "-C", str(env.repo), "status", "--porcelain"])
    dirt = len([ln for ln in dirty.splitlines() if ln.strip()]) if rc3 == 0 else -1
    dirt_s = "clean" if dirt == 0 else (f"{dirt} modified/untracked path(s)" if dirt > 0 else "unknown")
    return dict(
        value=f"{head.strip()} on {branch.strip() or '(detached)'} — {dirt_s}",
        detail=(
            "🔴 The page's own provenance, and it is load-bearing. A build taken "
            "against a DIRTY tree is evidence about the working copy, never about "
            "the commit — the same rule that makes a live probe off an uncommitted "
            "fix say nothing about `main`."
        ),
        source="git rev-parse HEAD / branch --show-current / status --porcelain",
    )


def m_hook_gate_install(env: Env) -> dict:
    """Is a blocking pre-push gate INSTALLED? Volatile — measured at build time.

    `git config --get core.hooksPath` is what answers this; `ls .git/hooks` never
    does, because githooks installs by REPOINTING that key. The value has been
    observed changing within a single session with no action by anyone, so this
    is measured here rather than stated anywhere in prose.
    """
    # 🔴 THIS GUARD EXISTS BECAUSE ITS ABSENCE WAS CAUGHT BY A NEGATIVE CONTROL.
    # `git config --get` exits non-zero for "unset" and for "not a repository"
    # alike, so without this the measurer reported a confident "(unset on both
    # scopes)" for a directory that is not a checkout at all — and, because it
    # could then never fail, it single-handedly made the all-unmeasured build
    # verdict UNREACHABLE. A measurer that can only ever succeed is not a
    # measurement.
    rc, _, _ = _run(["git", "-C", str(env.repo), "rev-parse", "--git-dir"])
    if rc != 0:
        raise Unmeasurable(
            f"{env.repo} is not a git checkout, so `core.hooksPath` has no "
            "meaning there — 'unset' and 'no repository' are different answers"
        )
    rows = []
    for scope in ("--local", "--global"):
        rc, out, _ = _run(["git", "-C", str(env.repo), "config", scope, "--get", "core.hooksPath"])
        rows.append((f"core.hooksPath {scope}", out.strip() if rc == 0 and out.strip() else "(unset)"))
    shipped = env.repo / "githooks"
    rows.append(("githooks/ ships in this repo", "yes" if shipped.is_dir() else "no"))
    return dict(
        value=next((v for k, v in rows if v != "(unset)" and "hooksPath" in k), "(unset on both scopes)"),
        detail=(
            "🔴 NEITHER 'installed' NOR 'uninstalled' IS SAFE TO CARRY IN PROSE. "
            "A repo-LOCAL value wins over the global one that `githooks/install.sh` "
            "writes, and a local value has been observed appearing and vanishing "
            "within one session. A pre-push gate that runs the suite IN the "
            "worktree has rewritten the branch it was pushing."
        ),
        source="git config --local/--global --get core.hooksPath",
        columns=("scope", "value"),
        rows=tuple(rows),
    )


def _tracked(env: Env, prefix: str = "scripts") -> list[str]:
    rc, out, err = _run(["git", "-C", str(env.repo), "ls-files", prefix])
    if rc != 0:
        raise Unmeasurable(f"`git ls-files {prefix}` failed (rc={rc}): {err.strip()[:160]}")
    files = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if not files:
        raise Unmeasurable(
            f"`git ls-files {prefix}` returned NOTHING — an empty file set is not "
            "an empty tree, it is a broken enumeration"
        )
    return files


#: The corpus-walk predicate, spelled out so the page can print it verbatim.
#:
#: 🔴 IT IS TEXTUAL, AND THE PAGE SAYS SO. A line that DESCRIBES a walk in a
#: docstring matches too, so this is an upper bound on independent walkers, not
#: a proof of one. Reporting it as an exact count would be the "a count of
#: DECLARATIONS is not a count of INSTANCES" error in miniature — every row is
#: printed with its file and line so the reader can settle it themselves.
_JSONL_WALK = re.compile(r"\.jsonl")
_WALK_VERB = re.compile(r"\bglob\b|\brglob\b|\biterdir\b|\bwalk\b")


def m_jsonl_walkers(env: Env) -> dict:
    """Soft seam: how many independent places walk the transcript corpus."""
    hits = []
    for rel in _tracked(env):
        if not rel.endswith(".py") or "/tests/" in rel or "/test_" in rel:
            continue
        p = env.repo / rel
        if not p.is_file():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _JSONL_WALK.search(line) and _WALK_VERB.search(line) and "*" in line:
                hits.append((rel, i))
    if not hits:
        raise Unmeasurable(
            "the corpus-walk predicate matched NOTHING — 'no matches' means "
            "'possibly the wrong pattern', not 'nothing there'"
        )
    files = sorted({r for r, _ in hits})
    subsystems = sorted({r.split("/")[1] if r.count("/") > 1 else Path(r).stem for r in files})
    return dict(
        value=f"{len(hits)} sites in {len(files)} files across {len(subsystems)} subsystems",
        detail=(
            "Each of these opens the Claude transcript corpus itself, and at "
            "least three DIFFERENT definitions of that corpus are in play — "
            "recursive, recursive-minus-synthetic-agent-dirs, and top-level-only. "
            "None is wrong; nothing owns the RELATIONSHIP between them, so a "
            "change to what a transcript looks like lands in N places and is "
            "caught in however many happen to have tests. 🔴 The count is an "
            "UPPER BOUND: the predicate is textual, so a docstring describing a "
            "walk matches too. Every site is printed with its line so you can "
            "settle it rather than trust it."
        ),
        source=(
            "every tracked non-test scripts/**/*.py line matching "
            "/\\.jsonl/ AND /glob|rglob|iterdir|walk/ AND a literal '*'"
        ),
        columns=("site", "line", "subsystem"),
        rows=tuple(
            (r, str(i), r.split("/")[1] if r.count("/") > 1 else "-") for r, i in hits
        ),
    )


#: A file is counted as a DETECTOR only if it both scrapes the pane command AND
#: carries a `claude` literal to compare it against. Scraping alone is not
#: detection — one file here reads the pane command for an unrelated purpose,
#: and a documentation file merely mentions the field. Requiring the CONJUNCTION
#: is what keeps this a count of instances rather than a count of mentions.
_CLAUDE_LITERAL = re.compile(r"""['"/]claude|claude\*|CLAUDE_RE""", re.I)


def m_session_detectors(env: Env) -> dict:
    """Soft seam: how many independent predicates decide 'a Claude is running here'."""
    hits: dict[str, list[int]] = {}
    for rel in _tracked(env) + [".tmux.conf"]:
        p = env.repo / rel
        if not p.is_file() or "/tests/" in rel or rel.endswith((".md", ".txt")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "pane_current_command" not in text or not _CLAUDE_LITERAL.search(text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "pane_current_command" in line:
                hits.setdefault(rel, []).append(i)
    if not hits:
        raise Unmeasurable(
            "no file both scrapes `pane_current_command` AND carries a `claude` "
            "literal — the conjunction is wrong, not the tree"
        )
    rows = tuple(
        (rel, ", ".join(str(i) for i in sorted(ns)[:4]),
         f"{len((env.repo / rel).read_text(errors='replace').splitlines()):,} lines")
        for rel, ns in sorted(hits.items())
    )
    return dict(
        value=f"{len(hits)} independent detectors",
        detail=(
            "Independent implementations answer 'is a Claude running in this "
            "pane', with predicates that are NOT equivalent: a case-insensitive regex, a "
            "regex plus a full /proc process-TREE walk, and exact string "
            "equality. They are not equivalent — a Claude under a wrapper or a "
            "shell is invisible to the regex-only ones, which render those "
            "windows unknown. 🔴 The honest note: two nearby tools "
            "(`session-resolve`, `waiting-windows`) deliberately do NOT re-derive "
            "the predicate and say so in their headers. The seam is the ones that do."
        ),
        source=(
            "every tracked non-test, non-doc file containing BOTH "
            "`pane_current_command` AND a `claude` literal"
        ),
        columns=("file", "line(s)", "size"),
        rows=rows,
    )


def m_session_surfaces(env: Env) -> dict:
    """Soft seam: the session-manager constellation, source and test weight."""
    surfaces = ["session-manager", "session-resolve", "waiting-windows", "session-write"]
    rows = []
    src_total = 0
    test_total = 0
    tests_dir = env.repo / "scripts" / "tests"
    for name in surfaces:
        p = env.repo / "scripts" / name
        if not p.is_file():
            rows.append((f"scripts/{name}", "ABSENT", "-"))
            continue
        n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        src_total += n
        suite = sorted(tests_dir.glob(f"test_{name.replace('-', '_')}*.py"))
        t = 0
        for s in suite:
            t += len(s.read_text(encoding="utf-8", errors="replace").splitlines())
        test_total += t
        rows.append((f"scripts/{name}", f"{n:,} lines", f"{t:,} lines in {len(suite)} suite(s)"))
    if src_total == 0:
        raise Unmeasurable("none of the session surfaces resolved to a file")
    ratio = test_total / src_total if src_total else 0
    return dict(
        value=f"{src_total:,} source + {test_total:,} test lines ({ratio:.1f}x)",
        detail=(
            "These surfaces answer overlapping questions about the same tmux "
            "state, and the satellites exist because of what the primary does "
            "and does not expose: one satellite's own header says it adds "
            "'exactly TWO things ... a CLOCK and a THRESHOLD', because the "
            "primary computed a waiting field that nothing consumed. 🔴 This is "
            "analysis, not a backlog item. The question — would one owner with "
            "two flags be smaller than one owner per question? — is not "
            "measured here, and nobody should merge the split on this page's "
            "say-so. What would settle it: whether the satellites are coupled to "
            "the primary's CLI (they shell out to it) or to a shared library."
        ),
        source="wc -l over scripts/session-* and scripts/waiting-windows and their suites",
        columns=("surface", "source", "tests"),
        rows=tuple(rows),
    )


#: An HTTP client is an IMPORT, not a word. Anchored at the start of a logical
#: line so a module named in a comment or a docstring cannot count.
_HTTP_IMPORT = re.compile(
    r"^\s*(?:import|from)\s+(?:urllib|requests|httpx|aiohttp|http\.client|http)\b",
    re.M,
)
_HTTP_CALL = re.compile(r"\burlopen\s*\(|\brequests\.(?:get|post|put|delete)\s*\(")


def imports_http_client(source: str) -> bool:
    """True when this source actually reaches for an HTTP client.

    Exposed (rather than inlined) so `test_present_measure.py` can drive it
    against both a positive and a negative control — the negative one being a
    file whose prose says "pull requests", which is what broke the first cut.
    """
    return bool(_HTTP_IMPORT.search(source) or _HTTP_CALL.search(source))


def m_store_api_clients(env: Env) -> dict:
    """Soft seam, now CLOSED: a complete subsystem that never acquired a reader.

    🔴 WHAT THIS ROW MEASURES CHANGED ON 2026-08-25, AND THE CHANGE IS THE
    POINT. It used to size a hosted HTTP API over the recall store — server plus
    build tooling plus suite — against a client set that stayed empty. The
    service was retired on that date (`claudedocs/
    decision-subsystem-store-api-retired-2026-08-25.md`); what the row measures
    now is that the server-side artefacts are GONE while the local readers'
    HTTP-client count is unchanged at whatever it always was.

    Kept rather than deleted because the interesting half was never the server:
    it is that a reader can stay absent through nine merged PRs with every gate
    green. A row asserting the count is still zero is what would notice a
    hosted client being reintroduced without the demand that was missing the
    first time.
    """
    server_dir = env.repo / "scripts" / "subsystem-store-api"
    suite = env.repo / "scripts" / "tests" / "test_subsystem_store_api.py"

    # A client would need an HTTP client library. Count the readers that IMPORT one.
    #
    # 🔴 THE PREDICATE IS AN IMPORT STATEMENT, NOT A WORD. The first cut matched
    # the bare token `requests` anywhere in the file and scored two readers as
    # HTTP-capable off the phrase "pull requests" in their prose — a measured row
    # that directly contradicted the section it sat under. Caught by reading the
    # rendered page, not by any test, which is the whole argument for looking at
    # the artefact.
    readers = ["scripts/lib/subsystem_recall.py", "scripts/lib/subsystem_resolver.py",
               "scripts/lib/subsystem_touch.py", "scripts/subsystem-audit.py"]
    rows = []
    clients = 0
    present = 0
    for rel in readers:
        p = env.repo / rel
        if not p.is_file():
            rows.append((rel, "ABSENT"))
            continue
        present += 1
        has = imports_http_client(p.read_text(encoding="utf-8", errors="replace"))
        clients += 1 if has else 0
        rows.append((rel, "speaks HTTP" if has else "no HTTP client"))

    # 🔴 A ZERO OVER ZERO READERS IS NOT A ZERO. With no reader file present
    # nothing was scanned, and `0 clients` would be byte-identical to the real
    # finding — the silent zero this whole module is built against. It also
    # keeps an empty tree ALL-unmeasured, which is the condition the generator
    # exits non-zero on; a row that "measures" against no input would turn that
    # broken build into a page.
    if present == 0:
        raise Unmeasurable(
            f"none of the {len(readers)} local store readers exists under "
            f"{env.repo} — nothing was scanned, so the client count is an "
            "absence and not a zero"
        )

    rows.append(("the hosted server + its build tooling",
                 "still present" if server_dir.is_dir() else "retired — no longer in this tree"))
    rows.append(("its test suite",
                 "still present" if suite.is_file() else "retired — no longer in this tree"))
    return dict(
        value=f"{clients} local reader(s) can speak to a hosted store",
        detail=(
            "The server was built, tested and hosted. The consuming client was "
            "designed and decided — 'hosted is an ENTRY-LEVEL ADVISORY, never the "
            "primary read' — and then never written; the handoff says so in its "
            "own words. The only things that ever spoke to it were its own seed "
            "and byte-identity scripts, and the service was retired on that "
            "evidence. 🔴 The shape worth recognising is not the retirement but "
            "the interval before it: a subsystem can be complete, correct, "
            "well-tested and have no reader, with every gate green throughout. "
            "The readers named here are a local disk library by design — this "
            "row going non-zero means a network hop was reintroduced."
        ),
        source=(
            "presence check on scripts/subsystem-store-api/ and its suite, plus an "
            "HTTP-client import scan over the local store readers"
        ),
        columns=("reader / artefact", "finding"),
        rows=tuple(rows),
    )


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #

#: `(key, section, label, fn, settle_command)`.
#:
#: 🔴 THE `settle` COMMAND IS NOT DECORATION. It is what an UNMEASURED row hands
#: the reader instead of a blank. A row with no settle command would tell someone
#: a fact is missing and give them nowhere to go, which is the same dead end as
#: omitting it.
REGISTRY: tuple[tuple[str, str, str, object, str], ...] = (
    ("repo.head", "how-to-read", "This build's provenance", m_repo_head,
     "git -C <repo> rev-parse --short HEAD && git -C <repo> status --porcelain"),
    ("rules.bytes", "told", "RULES.md against its ceiling", m_rules_bytes,
     "python -c \"import sys;sys.path.insert(0,'scripts/tests');import test_rules_size as t;print(t.MAX_BYTES)\""),
    ("rules.archive", "told", "RULES-ARCHIVE.md (costs zero per session)", m_rules_archive_bytes,
     "wc -c claude/RULES-ARCHIVE.md"),
    ("skills.listing", "told", "The always-on skill listing", m_skill_listing,
     "nix develop --impure --command python -m pytest scripts/tests/test_skill_tiers.py -q"),
    ("memory.index", "told", "MEMORY.md against its hard cap", m_memory_index,
     "python scripts/memory-audit.py"),
    ("skills.inventory", "told", "The skill inventory (routing table)", m_skill_inventory,
     "ls claude/skills/*/SKILL.md"),
    ("hooks", "may-do", "Hooks: shipped vs registered", m_hooks,
     "jq '.hooks' ~/.claude/settings.json"),
    ("gate.tiers", "verified", "The two gate tiers", m_gate_tiers,
     "nix develop --impure --command bash scripts/gate.sh --tier both"),
    ("gate.protection", "verified", "What actually BLOCKS a merge", m_branch_protection,
     "gh api /repos/<owner>/<repo>/branches/main/protection --jq .required_status_checks"),
    ("tests.pytest", "verified", "Per-target collected-test floors", m_pytest_floors,
     "bash scripts/run-tests.sh --check-floors"),
    ("tests.node", "verified", "The node tier's file floors", m_node_floors,
     "grep -A40 '^SUITES=(' scripts/run-node-tests.sh"),
    ("gate.hooks_installed", "verified", "Is a blocking pre-push gate installed?", m_hook_gate_install,
     "git config --local --get core.hooksPath; git config --global --get core.hooksPath"),
    ("ship.managed", "ships", "What home-manager actually deploys", m_managed_paths,
     "grep -c 'home\\.file\\.\"' nix/home.nix"),
    ("drift.ladder", "drift", "drift-check.sh exit-code ladder", m_drift_ladder,
     "bash scripts/drift-check.sh; echo rc=$?"),
    ("timers", "drift", "systemd user timers on this host", m_timers,
     "systemctl --user list-timers --all"),
    ("telemetry.sources", "observed", "Activity-telemetry sources", m_telemetry_sources,
     "ls -d scripts/collector/*/"),
    ("index.store", "observed", "The subsystem index store", m_index_store,
     "python scripts/analyze-service-index/... --list  (or: ls ~/.claude/analyze-service-index)"),
    ("seam.jsonl", "soft", "Independent transcript-corpus walkers", m_jsonl_walkers,
     "grep -rn '\\*\\.jsonl' --include='*.py' scripts | grep -E 'glob|rglob|iterdir|walk' | grep -v tests/"),
    ("seam.detectors", "soft", "Independent Claude-session detectors", m_session_detectors,
     "grep -rn pane_current_command scripts .tmux.conf | grep -v tests/"),
    ("seam.sessions", "soft", "The session-surface constellation", m_session_surfaces,
     "wc -l scripts/session-manager scripts/session-resolve scripts/waiting-windows scripts/session-write"),
    ("seam.store_api", "soft", "The retired hosted API, and the readers' client set", m_store_api_clients,
     "ls scripts/subsystem-store-api 2>&1; grep -rn 'urllib\\|requests\\|urlopen' scripts/lib/subsystem_*.py"),
)


def take(env: Env, registry=REGISTRY) -> MeasurementSet:
    """Run every measurer. A raise becomes an UNMEASURED ROW, never a gap.

    🔴 `except Exception` is correct here and is not laziness. The alternative —
    letting one measurer's ImportError abort the build — trades a page with one
    honest UNMEASURED row for no page at all, and the whole point of this layer
    is that an absence must be REPORTED as an absence.
    """
    out = MeasurementSet()
    for key, section, label, fn, settle in registry:
        asof = _now()
        try:
            fields = fn(env)
        except Unmeasurable as exc:
            out.items.append(Measurement(
                key=key, label=label, section=section, status=UNMEASURED,
                asof=asof, source="(not reached)", reason=str(exc), settle=settle,
            ))
            continue
        except Exception as exc:  # noqa: BLE001 - see docstring
            out.items.append(Measurement(
                key=key, label=label, section=section, status=UNMEASURED,
                asof=asof, source="(not reached)",
                reason=f"the measurer raised {type(exc).__name__}: {exc}",
                settle=settle,
            ))
            continue
        if not isinstance(fields, dict) or not fields.get("value"):
            out.items.append(Measurement(
                key=key, label=label, section=section, status=UNMEASURED,
                asof=asof, source="(not reached)",
                reason="the measurer returned no value — a blank is never rendered as clean",
                settle=settle,
            ))
            continue
        out.items.append(Measurement(
            key=key, label=label, section=section, status=MEASURED, asof=asof,
            settle=settle, **fields,
        ))
    return out
