"""Gate the clawgate and muster skills' TWO BASE URLS: a task-side path must never
be built on the permission ROUTER's base.

WHY THIS EXISTS
---------------
The task/agent/runbook half of clawgate was extracted into a separate service,
`muster` -- and, 2026-09-29, into a separate SKILL (`claude/skills/muster/`),
because the prose no longer fitted the byte ceiling on clawgate's always-loaded
core. Both trees are scanned here; scanning only clawgate's would leave the file
the split created -- the one most likely to spell a base URL -- unguarded, while
still returning a reassuring zero. Two processes answer on two base URLs:

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
must appear in a skill's core WITHIN REACH of the task base, so deleting the
routing block -- or adding a task family to a core without routing it -- reds
this file. It fails when the set GROWS *or* SHRINKS, and it runs over BOTH cores.

The third guard stops the first two passing VACUOUSLY: each core must name both
bases, so a deleted routing block cannot read as clean.

🔴 REMOVED 2026-09-29 -- a fourth guard, `test_the_family_ledger_matches_the_runtime`,
and its parse control. DO NOT RE-ADD THEM; the reasoning, so it is not re-derived:

  * it was GREEN at the base ref and GREEN at head. By this repo's own rule that
    makes it an INVARIANT PIN, not regression coverage, and it was never labelled
    as one;
  * its docstring claimed the ledger below "must EQUAL the one every consumer
    reads". It did not check that. `scripts/lib/clawgate_tasks.py` holds no code
    constant of path families -- what every consumer actually reads there is
    `TASK_API_URL_VARS`/`ROUTER_API_URL_VARS`, which are ENV VAR names and are
    already pinned whole by `scripts/tests/test_clawgate_tasks.py`. The only
    anchor available was a PROSE COMMENT, matched by regex. So the pin measured
    comment WORDING: a harmless reword reddened it, and a real change to the
    runtime split could land without reddening it at all. That is
    `claude/RULES.md`'s "a guard's DESCRIPTION claims COVERAGE -- check the
    implementation is as wide as the sentence";
  * its documented response to a red was "re-point this regex", i.e. edit the
    gate. A gate whose red is routinely cleared by editing the gate trains the
    click-through the rules forbid;
  * and it actively DEGRADED the evidence for guard 1: mutating the ledger
    removed that guard's INPUT as well as this one, so the mutant died for two
    reasons at once (recorded as M3 below).

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
  green both ways             the three hermetic controls -- they are controls,
                              not regression coverage

Re-measured 2026-09-29 for the SKILL SPLIT, base ref `origin/main` = 79a9b22a,
this file copied into a detached worktree of the base under
`PYTHONDONTWRITEBYTECODE=1`. The two legs added by the split:

  RED at base, GREEN at HEAD  …is_routed_to_the_task_base[muster] and
                              …are_distinct_everywhere[muster] -- at base the
                              file `claude/skills/muster/SKILL.md` does not
                              exist, so both error on the read
  RED at base, GREEN at HEAD  …is_routed[clawgate] / …are_distinct[clawgate]
                              (unchanged: the pre-#1920 core named no task base)

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
  M3  drop `/agent/task` from the family ledger
      -> the (now removed) runtime pin red. ⚠ The mis-based-URL CONTROL also went
         red, because the ledger feeds ALL_TASK_FAMILIES -- a mutant that removes
         a guard's INPUT as well as the guard, so read M1 (which isolates that
         detector) as the evidence for guard 1, not M3. This coupling is part of
         why the pin was removed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 🔴 BOTH skill trees, not just clawgate's. The task half now has its own skill
#: (`claude/skills/muster/`), so a gate that scanned only `clawgate/` would have
#: gone quietly blind to exactly the file the split created — the one most likely
#: to spell a base URL. Widened 2026-09-29 with the split itself.
SKILL_DIRS = (
    REPO_ROOT / "claude" / "skills" / "clawgate",
    REPO_ROOT / "claude" / "skills" / "muster",
)

#: The ALWAYS-LOADED cores — one per service. Both are checked, because either one
#: can name a task family and neither one is where the reader necessarily starts.
SKILL_CORES = tuple(d / "SKILL.md" for d in SKILL_DIRS)

# --------------------------------------------------------------------------- #
# The ledger. Families are PATH PREFIXES, matched immediately after a base.
# --------------------------------------------------------------------------- #

#: Every task-side path prefix, MEASURED 2026-09-29: each answered 401 on
#: `:30306` and 404 on `:30302`, with `/zzz-control` 404 on both as the negative
#: control. The first three are also the ones `clawgatectl` routes and the ones
#: `scripts/lib/clawgate_tasks.py` names in prose.
#:
#: ⚠ These were two tuples until 2026-09-29, kept apart so a two-way pin against
#: that prose could "compare like with like". That pin is GONE -- see the note at
#: the end of this module's docstring -- and with it the only reason to split
#: them, so they are one list again.
ALL_TASK_FAMILIES = (
    "/api/tasks",
    "/api/agents",
    "/agent/task",
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
    return sorted(p for d in SKILL_DIRS for p in d.rglob("*.md") if p.is_file())


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


@pytest.mark.parametrize("core", SKILL_CORES, ids=lambda c: c.parent.name)
def test_every_task_family_named_in_the_core_is_routed_to_the_task_base(core):
    """🔴 A RELATIONSHIP, not a word. Fails when the set GROWS (a new task family
    is documented without routing it) or SHRINKS (the routing block is deleted,
    or the task base stops being named).

    Red at `origin/main`: clawgate's `SKILL.md` names `/api/tasks`, `/api/agents`,
    `/agent/task` and `/tasks` and mentions no task base anywhere, so all four
    come back unrouted.

    🔴 PARAMETRISED OVER BOTH CORES, 2026-09-29. The task half moved to its own
    skill, so most of the families this guard is about are now named in
    `muster/SKILL.md` -- and a guard still reading only clawgate's core would have
    scored a reassuring zero over a file that no longer contains the thing being
    guarded. The clawgate leg is NOT vestigial: its core keeps a pointer block
    that names the task families precisely so a reader who lands there cannot
    build one on the router base, and this leg is what stops that block being
    deleted or left un-based.
    """
    unrouted = _families_not_routed(core.read_text())
    assert not unrouted, (
        f"{core.relative_to(REPO_ROOT)} names these TASK-side path "
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
# GUARD 3 -- the two bases are not the same string.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("core", SKILL_CORES, ids=lambda c: c.parent.name)
def test_the_two_bases_are_distinct_everywhere_the_skill_states_them(core):
    """The guard that stops the two above passing VACUOUSLY: if a core stops
    naming the task base at all, `_families_not_routed` has no anchor to measure
    against and a deleted routing block would read as clean.

    🔴 It IS regression coverage, measured, not an invariant guard: red at
    `origin/main` (3573a413), where `SKILL.md` contained no `:30306` anywhere. An
    earlier draft of this docstring asserted the opposite ('it never failed
    against pre-change content') without running it -- the claim was wrong. The
    muster leg is red against pre-change content too, in the blunter way: the file
    did not exist.

    🔴 BOTH cores must name BOTH bases, and that is the point rather than
    symmetry for its own sake. A core naming only its OWN base teaches a reader
    nothing about the base that 404s, and the 404 is silent.
    """
    text = core.read_text()
    who = core.relative_to(REPO_ROOT)
    assert ":30306" in text, (
        f"{who} does not name the task base at all; every routing "
        "guard in this file then passes vacuously"
    )
    assert ":30302" in text, (
        f"{who} no longer names the router base; the split it "
        "documents needs both halves"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
