"""Gate the clawgate skill's TWO BASE URLS: a task-side path must never be built
on the permission ROUTER's base.

WHY THIS EXISTS
---------------
The task/agent/runbook half of clawgate was extracted into a separate service,
`muster`. Two processes now answer on two base URLs:

    router  http://192.168.50.250:30302   $CLAWGATE_API_URL
    tasks   http://192.168.50.250:30306   $CLAWGATE_TASK_API_URL

The runtime already models this -- `scripts/lib/clawgate_tasks.py` owns the
ordered ledger (`TASK_API_URL_VARS` / `ROUTER_API_URL_VARS`), `clawgate_handoff.sh`
mirrors it, and `clawgatectl` picks a base PER ROUTE. The SKILL did not: 18 of its
19 files mentioned `muster` zero times, and two of them handed a reader a literal
`http://192.168.50.250:30302` with a task path glued onto it.

🔴 THE FAILURE IS SILENT, WHICH IS WHY IT NEEDS A MACHINE GUARD. Measured
2026-09-29 against clawgate 0.8.65 and muster 0.2.0: a task path on the router
base does not 401 and does not connect-refuse -- it answers `404 page not found`
(text/plain), which `clawgatectl` maps to rc 7, "this CLI is newer than the
server". So the operator's reading of a mis-based URL is "my binary is stale",
not "my base is wrong". `/zzz-control` 404s on both bases, so those 404s are the
route's absence rather than a dead port.

WHAT IS AND IS NOT ASSERTED
---------------------------
🔴 The artifact under test is PROSE, and `claude/RULES.md` warns that a guard on
WORDS is walkable by REWORDING. So the primary guard here is NOT a word check: it
is STRUCTURAL, over a machine-readable construct a reword cannot dissolve -- a
router base token with a task-side path CONCATENATED onto it. You cannot reword
`http://192.168.50.250:30302/api/tasks` into something that is still a working
instruction and no longer matches.

The second guard pins a RELATIONSHIP rather than a word: every task-path family
must appear in the skill's core WITHIN REACH of the task base, so deleting the
routing block -- or adding a task family to the skill without routing it -- reds
this file. It fails when the set GROWS *or* SHRINKS.

The third guard is the two-way pin that stops this module's own ledger drifting
from the runtime's: the families below are asserted EQUAL to the ones
`scripts/lib/clawgate_tasks.py` names, so a change there cannot leave this test
guarding a stale set.

NOT asserted: that any particular sentence is present, that the frontmatter
description reads a certain way, or that muster is named a certain number of
times. All three are rewordable, and pinning them would buy friction rather than
coverage.

POSITIVE CONTROLS. Both detectors' reassuring answers are ZEROS ("0 mis-based
URLs"), which is indistinguishable from a detector wired to nothing
(`claude/RULES.md` -> "a harness that COUNTS needs a POSITIVE control"). Every
counter here is therefore exercised against a synthetic fixture that MUST move it
off zero, at an exact expected count.

RED/GREEN MATRIX, measured (not asserted from the implementation)
-----------------------------------------------------------------
Base ref `origin/main` = 3573a413, run in a detached worktree with this file
copied in and `PYTHONDONTWRITEBYTECODE=1`:

  RED at base, GREEN at HEAD  test_no_task_side_path_is_built_on_the_router_base
                              (4 real offenders: flows/task-authoring.md
                              `:30302/api/tags`, reference/agent-dispatch.md
                              `:30302/agents`, reference/{auth-doors,deploy}.md
                              `:30302/tasks`)
  RED at base, GREEN at HEAD  test_every_task_family_named_in_the_core_is_routed…
                              (6 unrouted families; SKILL.md had no task base)
  RED at base, GREEN at HEAD  test_the_two_bases_are_distinct_everywhere…
  green both ways             the three hermetic controls and the ledger pin --
                              they are controls, not regression coverage

MUTATION RECORD -- each guard broken on purpose and watched to die for ITS OWN
reason, in the same scratch worktree, with an unmutated re-run as the positive
control (9 passed) after every mutant:

  M1  re-glue `/api/tags` onto the router base in flows/task-authoring.md
      -> only test_no_task_side_path… red
  M2  strip every `:30306` from SKILL.md
      -> only test_the_two_bases_are_distinct… red. 🔴 Guard 2 stayed GREEN,
         because `CLAWGATE_TASK_API_URL` is also an anchor -- which is exactly
         why guard 4 exists and why M5 was needed to isolate guard 2.
  M5  strip BOTH anchors (`:30306` and the env var) from SKILL.md, keeping every
      task path -> test_every_task_family… red naming all 10 families
  M3  drop `/agent/task` from LEDGER_TASK_FAMILIES
      -> test_the_family_ledger_matches_the_runtime red. ⚠ The mis-based-URL
         CONTROL also went red, because the ledger feeds ALL_TASK_FAMILIES --
         a mutant that removes a guard's INPUT as well as the guard, so read M1
         (which isolates that detector) as the evidence for guard 1, not M3.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "claude" / "skills" / "clawgate"
SKILL_CORE = SKILL_DIR / "SKILL.md"
TASKS_LIB = REPO_ROOT / "scripts" / "lib" / "clawgate_tasks.py"

# --------------------------------------------------------------------------- #
# The ledger. Families are PATH PREFIXES, matched immediately after a base.
# --------------------------------------------------------------------------- #

#: The three families the runtime ledger names. Pinned two-way against
#: `scripts/lib/clawgate_tasks.py` by `test_the_family_ledger_matches_the_runtime`
#: so this constant cannot silently drift from the module every consumer reads.
LEDGER_TASK_FAMILIES = ("/api/tasks", "/api/agents", "/agent/task")

#: Further task-side prefixes, MEASURED 2026-09-29 rather than inherited from the
#: runtime ledger (which only needs the three the CLI routes). Each answered 401
#: on `:30306` and 404 on `:30302`, with `/zzz-control` 404 on both as the
#: negative control. They are kept SEPARATE from the ledger above because the
#: two-way pin must compare like with like -- folding them in would red that pin
#: for a reason that has nothing to do with drift.
MEASURED_TASK_FAMILIES = (
    "/api/tags",
    "/api/projects",
    "/api/sessions/",
    "/tasks",
    "/agents",
    "/repos",
    "/runbooks",
    "/ui/tasks",
    "/ui/agents",
)

ALL_TASK_FAMILIES = LEDGER_TASK_FAMILIES + MEASURED_TASK_FAMILIES

#: How a router base is spelled in prose and in shell. The literal NodePort and
#: the env var are the two forms that actually appear; both are checked because a
#: reader copies either one.
ROUTER_BASE_TOKENS = (
    "http://192.168.50.250:30302",
    "$CLAWGATE_API_URL",
    "${CLAWGATE_API_URL}",
)

#: The task base, in the two forms a reader can copy.
TASK_BASE_TOKENS = (
    "http://192.168.50.250:30306",
    ":30306",
    "$CLAWGATE_TASK_API_URL",
    "${CLAWGATE_TASK_API_URL}",
    "CLAWGATE_TASK_API_URL",
)

#: How far from a task family a task-base mention may sit and still count as
#: routing it. Generous on purpose: the guard is about the family being routed at
#: all, not about sentence layout.
REACH_CHARS = 1200


def _skill_files():
    return sorted(p for p in SKILL_DIR.rglob("*.md") if p.is_file())


def _mis_based_urls(text: str):
    """Every (base, family) pair where a task path is glued onto a router base.

    Returns a list of the offending literal substrings, so a failure names the
    exact bytes rather than a count.
    """
    hits = []
    for base in ROUTER_BASE_TOKENS:
        start = 0
        while True:
            i = text.find(base, start)
            if i < 0:
                break
            start = i + 1
            tail = text[i + len(base):]
            for fam in ALL_TASK_FAMILIES:
                if tail.startswith(fam):
                    hits.append(base + fam)
                    break
    return hits


# --------------------------------------------------------------------------- #
# GUARD 1 -- the structural one. A task path on the router base.
# --------------------------------------------------------------------------- #

def test_no_task_side_path_is_built_on_the_router_base():
    """🔴 THE REGRESSION GUARD. Red at `origin/main`, where
    `flows/task-authoring.md` built `…:30302/api/tags` and
    `reference/agent-dispatch.md` built `…:30302/agents`.

    Matching is on CONCATENATION, not on wording: a base token immediately
    followed by a task-side path prefix. That construct is what a reader copies
    and what a script would send, so it cannot be reworded away while remaining a
    usable instruction.
    """
    offenders = {}
    for path in _skill_files():
        hits = _mis_based_urls(path.read_text())
        if hits:
            offenders[path.relative_to(REPO_ROOT)] = hits
    assert not offenders, (
        "a TASK-side path is built on the PERMISSION ROUTER's base. On "
        "`:30302` these answer `404 page not found` (text/plain -> clawgatectl "
        "rc 7, which reads as a stale binary, not a wrong base). Use "
        "`$CLAWGATE_TASK_API_URL` / `http://192.168.50.250:30306`:\n"
        + "\n".join(
            f"  {p}: " + ", ".join(sorted(set(h)))
            for p, h in sorted(offenders.items())
        )
    )


def test_control_the_mis_based_url_detector_can_go_red():
    """POSITIVE CONTROL for guard 1 -- it must COUNT, not merely be able to fail.

    Three planted URLs across both base spellings and three different families,
    plus two lines that must NOT match: a correctly-based task URL, and a
    correctly-based ROUTER url whose path merely CONTAINS a family name rather
    than starting with one.
    """
    bad = (
        "curl http://192.168.50.250:30302/api/tasks\n"
        "curl $CLAWGATE_API_URL/agent/task\n"
        "curl ${CLAWGATE_API_URL}/runbooks\n"
        "curl http://192.168.50.250:30306/api/tasks      # correct, must not match\n"
        "curl $CLAWGATE_TASK_API_URL/api/agents          # correct, must not match\n"
        "curl http://192.168.50.250:30302/api/send       # router route, must not match\n"
        "see http://192.168.50.250:30302/api/transcripts/stream  # must not match\n"
    )
    hits = _mis_based_urls(bad)
    assert hits == [
        "http://192.168.50.250:30302/api/tasks",
        "$CLAWGATE_API_URL/agent/task",
        "${CLAWGATE_API_URL}/runbooks",
    ], hits


def test_control_a_clean_text_scores_zero():
    """NEGATIVE control: the detector must not fire on correctly-based prose."""
    good = (
        "The task board is http://192.168.50.250:30306/api/tasks and the router "
        "is http://192.168.50.250:30302/api/send; $CLAWGATE_TASK_API_URL/agent/task "
        "is the agent's own read."
    )
    assert _mis_based_urls(good) == []


# --------------------------------------------------------------------------- #
# GUARD 2 -- the RELATIONSHIP. Every task family is routed to the task base.
# --------------------------------------------------------------------------- #

def _families_not_routed(text: str, families=ALL_TASK_FAMILIES):
    """Families the text MENTIONS but never near a task-base token.

    A family the text does not mention at all is not an offence -- this guard is
    about routing what is named, not about mandating a vocabulary.
    """
    base_spans = []
    for tok in TASK_BASE_TOKENS:
        for m in re.finditer(re.escape(tok), text):
            base_spans.append(m.start())
    unrouted = []
    for fam in families:
        idxs = [m.start() for m in re.finditer(re.escape(fam), text)]
        if not idxs:
            continue
        if not any(
            abs(i - b) <= REACH_CHARS for i in idxs for b in base_spans
        ):
            unrouted.append(fam)
    return unrouted


def test_every_task_family_named_in_the_core_is_routed_to_the_task_base():
    """🔴 A RELATIONSHIP, not a word. Fails when the set GROWS (a new task family
    is documented without routing it) or SHRINKS (the routing block is deleted,
    or the task base stops being named).

    Red at `origin/main`: `SKILL.md` names `/api/tasks`, `/api/agents`,
    `/agent/task` and `/tasks` and mentions no task base anywhere, so all four
    come back unrouted.
    """
    unrouted = _families_not_routed(SKILL_CORE.read_text())
    assert not unrouted, (
        f"{SKILL_CORE.relative_to(REPO_ROOT)} names these TASK-side path "
        "families but never within "
        f"{REACH_CHARS} chars of the task base "
        "(`:30306` / `CLAWGATE_TASK_API_URL`), so a reader building a URL from "
        f"it has nothing to build it on: {unrouted}"
    )


def test_control_the_routing_detector_can_go_red():
    """POSITIVE CONTROL for guard 2, at an exact expected set.

    ⚠ `/tasks` and `/agents` are SUBSTRINGS of `/api/tasks` and `/api/agents`, so
    a text naming only the `/api/` forms reports all four. That over-report is the
    safe direction for a guard whose job is "is this routed at all", and it is
    pinned here so nobody mistakes it for a bug and 'fixes' it into an
    under-report.
    """
    text = (
        "Read the board with GET /api/tasks and the roster with GET /api/agents. "
        "The router base is http://192.168.50.250:30302."
    )
    assert _families_not_routed(text) == [
        "/api/tasks",
        "/api/agents",
        "/tasks",
        "/agents",
    ]


def test_control_the_routing_detector_accepts_a_routed_family():
    """NEGATIVE control: naming the task base near the family satisfies it, and a
    family the text never mentions is not reported as unrouted."""
    text = (
        "Read the board with GET /api/tasks on "
        "http://192.168.50.250:30306 (`CLAWGATE_TASK_API_URL`)."
    )
    assert _families_not_routed(text) == []


# --------------------------------------------------------------------------- #
# GUARD 3 -- two-way pin against the runtime ledger.
# --------------------------------------------------------------------------- #

_RUNTIME_LEDGER_RE = re.compile(r"^#:\s*task side\s*—(.*)$", re.M)


def test_the_family_ledger_matches_the_runtime():
    """🔴 The ledger this module guards must EQUAL the one every consumer reads.

    `scripts/lib/clawgate_tasks.py` is the single source of the task/router
    split for the bar poller, the session manager and the write-back hook. If it
    grows or loses a family, this test reds rather than letting the skill gate
    keep guarding a stale set. Set equality -- it fails in BOTH directions.
    """
    text = TASKS_LIB.read_text()
    m = _RUNTIME_LEDGER_RE.search(text)
    assert m, (
        f"could not find the `#:   task side — …` ledger line in "
        f"{TASKS_LIB.relative_to(REPO_ROOT)}. That line is the two-way pin's "
        "only anchor; if it was reworded, re-point this regex AND re-check "
        "LEDGER_TASK_FAMILIES against it by hand."
    )
    runtime = {
        f.strip().rstrip("*").rstrip(",").strip("`")
        for f in m.group(1).split(",")
        if f.strip()
    }
    assert runtime == set(LEDGER_TASK_FAMILIES), (
        "this module's LEDGER_TASK_FAMILIES has drifted from "
        f"{TASKS_LIB.relative_to(REPO_ROOT)}: runtime={sorted(runtime)} "
        f"module={sorted(LEDGER_TASK_FAMILIES)}"
    )


def test_control_the_runtime_ledger_parse_finds_something():
    """POSITIVE control for the parse above: a zero-length match set would make
    `runtime == set(...)` fail loudly rather than pass vacuously, but a parse that
    silently returned the WHOLE line as one family would pass nothing useful --
    so assert the parse produced exactly three non-empty entries."""
    m = _RUNTIME_LEDGER_RE.search(TASKS_LIB.read_text())
    assert m
    parsed = [f.strip() for f in m.group(1).split(",") if f.strip()]
    assert len(parsed) == 3, parsed
    assert all(f.startswith("/") for f in parsed), parsed


# --------------------------------------------------------------------------- #
# GUARD 4 -- the two bases are not the same string.
# --------------------------------------------------------------------------- #

def test_the_two_bases_are_distinct_everywhere_the_skill_states_them():
    """The guard that stops the two above passing VACUOUSLY: if the skill stops
    naming the task base at all, `_families_not_routed` has no anchor to measure
    against and a deleted routing block would read as clean.

    🔴 It IS regression coverage, measured, not an invariant guard: red at
    `origin/main` (3573a413), where `SKILL.md` contained no `:30306` anywhere. An
    earlier draft of this docstring asserted the opposite ('it never failed
    against pre-change content') without running it -- the claim was wrong.
    """
    text = SKILL_CORE.read_text()
    assert ":30306" in text, (
        "the clawgate SKILL.md does not name the task base at all; every routing "
        "guard in this file then passes vacuously"
    )
    assert ":30302" in text, (
        "the clawgate SKILL.md no longer names the router base; the split it "
        "documents needs both halves"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
