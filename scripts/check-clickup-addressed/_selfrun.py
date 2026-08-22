#!/usr/bin/env python3
"""Detect a transcript that is a RUN of this checker, not independent evidence about a task.

Why this exists (2026-08-20). The pipeline already skipped `$CLAUDE_CODE_SESSION_ID`, so it
could not read the transcript it was being written into. It could still read *yesterday's*
run — and a prior run is the worst possible witness: it prints every task ID under test
directly beside completion vocabulary ("likely_addressed", "✓ resolved", "merged"), which is
exactly the shape the proximity scorer is built to reward.

Measured that day: both tasks in the report scored `likely_addressed` on evidence that was
100% a previous run's own output. Excluding it, they were `unclear` and `no_mentions_found`.
One of them (868kr0799) carried a comment reading "Still live, do not close" — the tool told
the operator to close it.

The markers below must be ANCHORED. Measured **2026-08-21** over the population these
scripts actually walk — `CLAUDE_DIR.iterdir()` then `glob("*.jsonl")`, top level only,
**735 transcripts** on this box — a bare `check-clickup-addressed` matches **67 (9.1%)**,
because the skill catalog is injected into every session's context; a bare
`/check-clickup-addressed` matches **24 (3.3%)**, because it is a substring of the *path*
`~/.claude/skills/check-clickup-addressed`. The real anchored markers match **4**. That is a
~17x over-drop for the bare name and ~6x for the slash form — either is unusable.

🔴 These figures DRIFT and are undated nowhere else: 746/62/23 on 2026-08-20 became
735/67/24 on 2026-08-21. Treat them as a ratio and a date, not a constant — re-measure
before quoting them anywhere.

🔴 Measure over THAT population, not over `grep -r ~/.claude/projects`. A recursive grep also
walks `<session>/subagents/*.jsonl` (4559 files) and `<session>/tool-results/*`, which these
scripts never open — doing so gave 213 and 32 against a denominator of ~250, i.e. both
numerator and denominator wrong, and made the rejected candidates look far worse than they
are. The verdict survived re-measurement; the figures did not.
"""
import re

SELF_RUN_MARKERS = (
    # Slash invocation of the skill.
    "<command-name>/check-clickup-addressed</command-name>",
    # Any Bash call into the pipeline, and the SKILL.md body itself once loaded. Also
    # catches the sessions that BUILT this skill, whose test fixtures hardcode real task
    # IDs (868krn3y1, 868kr07fu) next to mock completion text.
    #
    # 🔴 THE MARKER IS A PATH AND THE CODE MOVED (2026-08-22): this literal is the
    # datapacket-talos layout `.claude/skills/check-clickup-addressed/scripts/`, kept because
    # transcripts written before the migration still say it. The devrc layout reverses those
    # two segments, so it matches nothing here — it is handled by NEW_LAYOUT_RE below, which
    # is a regex for a measured reason, not a stylistic one.
    #
    # ⚠ HOW MUCH THE MOVE ACTUALLY BROKE — corrected after an audit, because the first version
    # of this comment overstated it. The path markers catch the INVOCATION; the header below
    # catches the OUTPUT, and every text-mode report prints it. Measured over the 762
    # transcripts these scripts walk, the exclusion set is **11 with or without** the path
    # marker — so the guard did NOT go blind on the ordinary path. The one shape that was
    # genuinely uncovered is a `--json` run, whose output carried no marker at all until
    # `render_json` was fixed the same day. The path markers remain worth keeping: they catch
    # a run whose OUTPUT never reaches the transcript (piped to a file, `--json | jq`,
    # truncated), which the header cannot. Defence in depth, correctly labelled.
    "check-clickup-addressed/scripts/",
    # The report header printed by check-addressed.py — catches a session that pasted the
    # output without running it.
    "## Task Completion Status",
)

# Skill-tool invocation, e.g. {"skill": "check-clickup-addressed"}. A regex rather than a
# literal because spacing varies by serializer, and because the pair appears in two forms:
# raw JSON structure (`"skill":"..."`) when the Skill tool was called, and BACKSLASH-ESCAPED
# (`\"skill\": \"...\"`) when a transcript quotes the call inside message text. The escaped
# form is why this is not a plain string match — the first version missed it.
SELF_RUN_RE = re.compile(r'\\?"skill\\?"\s*:\s*\\?"check-clickup-addressed\\?"')

# The devrc layout, anchored on a SCRIPT FILE rather than on the directory.
#
# 🔴 THE DIRECTORY SPELLING WAS TRIED AND MEASURED UNUSABLE (2026-08-22). `scripts/check-
# clickup-addressed/` reads like the obvious counterpart of the old marker, but devrc's
# CLAUDE.md carries a subsystem table mapping `scripts/<dir>/` to its owning skill, and a
# project CLAUDE.md is injected into every session in that repo. Measured over the 761
# transcripts these scripts actually walk: the two sibling rows `scripts/repo-cos/` and
# `scripts/session-analysis/` appear in **83 (10.9%)** and **72 (9.5%)** of them — the same
# order as the bare name `check-clickup-addressed` at 96 (12.6%), which this file already
# rejects as unusable. The gate's own per-target output (`… scripts/check-clickup-addressed/
# tests`) is a second such source.
#
# Requiring a `.py` FILE immediately after the directory separates a run from a mention:
#   matches      scripts/check-clickup-addressed/check-addressed.py     (an invocation)
#   NO match     scripts/check-clickup-addressed/                       (CLAUDE.md's table)
#   NO match     scripts/check-clickup-addressed/tests/test_corpus.py   (gate output — the
#                                                                        slash breaks it)
# SKILL.md still self-marks, deliberately, because it cites `…/_selfrun.py` — a loaded
# SKILL.md means the skill fired, which IS a run.
#
# 🔴 THE PATH FORM ALONE MISSES THE INVOCATION THIS SKILL'S OWN DOCS TEACH. SKILL.md's quick
# start is `CCUA=~/workspace/devrc/scripts/check-clickup-addressed` then
# `python3 "$CCUA/check-addressed.py"` — the variable means the adjacency never appears in the
# transcript, and `cd <dir> && python3 check-addressed.py` breaks it the same way. A marker
# that only matches the fully-spelled path is a marker for a shape nobody types. So the bare
# entry-point BASENAMES are alternatives here. Measured 2026-08-22 over the 762 transcripts
# these scripts walk, each is in the anchored class, not the ~10% mention class:
#   check-addressed.py 12 (1.6%) · check-completion.py 10 (1.3%)
#   search-sessions.py 10 (1.3%) · recent-comments.py  10 (1.3%)
# for reference: the old path marker 11 (1.4%), the bare skill name 96 (12.6%, rejected).
# The `-` spelling is what keeps them tight: the test modules are `test_check_completion.py`
# with UNDERSCORES, so a pytest run naming them does not match.
NEW_LAYOUT_RE = re.compile(
    r'check-clickup-addressed/[\w.-]+\.py'
    r'|(?:check-addressed|check-completion|search-sessions|recent-comments)\.py'
)

_cache = {}


def is_self_run(path):
    """True if `path` is a transcript of this checker running (or its output being quoted).

    Scans raw JSONL lines rather than decoded message text: the markers live in tool_use
    inputs and command wrappers, which the text-only readers in check-completion.py drop.
    """
    key = str(path)
    if key in _cache:
        return _cache[key]

    hit = False
    try:
        with open(key, errors="replace") as f:
            for line in f:
                if (any(m in line for m in SELF_RUN_MARKERS)
                        or SELF_RUN_RE.search(line)
                        or NEW_LAYOUT_RE.search(line)):
                    hit = True
                    break
    except OSError:
        # Deliberately NOT cached. A transient read error answers "not a self-run", which
        # is the unsafe direction; caching it would pin that answer for the whole process
        # and let a prior run be read as evidence on every later lookup.
        return False

    _cache[key] = hit
    return hit
