#!/usr/bin/env python3
"""Churn an audit ladder's `audit-claims` blocks do NOT cover — the hole in the
churn measurement behind `claude/skills/audit-pr/`.

    scripts/ladder-range-coverage.py 1233
    scripts/ladder-range-coverage.py 958 1233 --repo innovation-upstream/devrc
    scripts/ladder-range-coverage.py --facts-file <json>      # no `gh`, no network

WHY THIS EXISTS
---------------
`claudedocs/audit-ladder-review-2026-09-04.md` measured each block's OWN
`from..to` range, which is correct per the header semantics
(`audit-dispatch.py::range_anchor`: `<from>` = the tip that round's audit READ,
`<to>` = the head its FIXES produced) — but it only covers the churn the blocks
CHAIN ACROSS. That review names the hole against itself:

    #1233 posted blocks for rounds 1, 2 and 4. Its round-4 comment is titled
    "rounds 3 and 4", and round 3's fixes sit in `1b5d2e43..eb947328`, which no
    block's range contains. That churn is in no column of my table.

It also names two more: #1108 and #1219 both start at round 2, so their round-1
churn is outside every range. So the published `19,230 payload / 32,393
scaffolding` is an UNDER-count by an unmeasured amount, and the fix is not a
better eye — it is this report.

🔴 THE REVIEW'S INSTRUMENT WAS A SCRATCHPAD VARIANT AND IS GONE. Its own text
says a second instrument "was written for this review"; nothing committed
measures per-block churn today (`scripts/ladder-depth-sweep.py` measures DEPTH,
from transcripts, not churn from claims blocks). That is why this file exists as
a script and not as a procedure: the repo's own record is that a measurement
living in a scratchpad is a measurement nobody can re-derive.

WHAT IT REPORTS
---------------
Per ladder, every ADJACENCY in the chain of block ranges, classified — and the
classification is the finding, not a detail:

  TIGHT      `to(N)` IS `from(N+1)`. The chain holds; nothing is uncovered.
  GAP        `to(N)..from(N+1)` is non-empty ⇒ churn in NO block's range. The
             #1233 shape. Its churn is what this script exists to print.
  OVERLAP    `from(N+1)` is an ANCESTOR of `to(N)` ⇒ two ranges cover the same
             commits, so the review's per-round columns DOUBLE-COUNT them.
  UNRELATED  neither sha reaches the other. Not a gap with a size — a ladder
             whose blocks do not describe one history (a rebase mid-ladder, or a
             typed sha). Reported as UNMEASURABLE, never as 0.

plus the TAIL adjacency `to(last)..<head>`, under the same four labels.

🔴 THE WINDOW IS `[from(first), head]`, DELIBERATELY — it does NOT reach back to
the base. Churn before the first block is the PR's ORIGINAL work, not ladder
work, and counting it would turn every ordinary PR into a ladder with a huge
hole. The consequence is stated rather than hidden: for a ladder whose first
block is `round=2` (#1108, #1219) ROUND 1's FIXES ARE OUTSIDE THIS WINDOW TOO,
so this script reports that ladder's first block round number and says the
round-1 churn is out of scope. That is the review's third case, and it needs the
operator to decide what round 1 of a ladder whose ledger starts at 2 even means
— it is not a number this script should invent.

READ RULES
----------
The churn command and its rc-0 / silent-stderr rules are NOT reimplemented here:
they come from `audit_dispatch.measure_range_churn`, which was extracted from
the ledger for this caller. Claims blocks are parsed by
`audit_dispatch.parse_claims_blocks`. One rule, one place — a second copy of
either would be a second thing to get wrong.

🔴 A ZERO IS REFUSED WHEN IT CANNOT BE TOLD FROM A BROKEN RUN. "Every adjacency
is TIGHT" and "none of this PR's commits are in this checkout" produce the same
uncovered total — 0 — so before reporting one this script requires a POSITIVE
CONTROL: at least one block's own `from..to` range must measure non-zero churn.
A ladder whose every round measures empty is reported `UNMEASURABLE`, with the
reason, at exit 4. The commits of a MERGED PR are the usual cause; `--fetch`
(default on for `gh` mode) pulls `refs/pull/<n>/head` first, and says so.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOTHING_MEASURABLE = 3
EXIT_REFUSED = 4

# --------------------------------------------------------------------------- #
# The shared core, imported rather than re-typed
# --------------------------------------------------------------------------- #
# `audit-dispatch.py` has a hyphen, so it is not importable by name. Loading it
# by path is deliberate and is the whole point: `parse_claims_blocks`,
# `same_commit` and `measure_range_churn` are the SAME code the briefs are built
# from, so a change to the block grammar or the churn command cannot leave this
# script reading an older dialect.


def load_audit_dispatch(path=None):
    here = Path(__file__).resolve().parent
    target = Path(path) if path else here / "audit-dispatch.py"
    if not target.exists():
        raise SystemExit(
            f"cannot find audit-dispatch.py at {target} — this script reuses its "
            "claims-block parser and churn command rather than carrying a second "
            "copy. Pass --audit-dispatch <path>."
        )
    spec = importlib.util.spec_from_file_location("audit_dispatch", target)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {target} as a module")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Process boundary — ONE runner, injected, so every test is hermetic
# --------------------------------------------------------------------------- #

def real_runner(cmd, cwd=None):
    """-> (rc, stdout, stderr). The only place this module spawns anything."""
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout, p.stderr


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #

TIGHT, GAP, OVERLAP, UNRELATED = "TIGHT", "GAP", "OVERLAP", "UNRELATED"

Adjacency = namedtuple(
    "Adjacency",
    "label frm to from_round to_round added deleted commits reason gap_commits"
)
Ladder = namedtuple(
    "Ladder",
    "pr head base blocks_total blocks_used first_round adjacencies "
    "control_churn uncovered_added uncovered_deleted interior tail "
    "reason malformed bare",
)


def _is_ancestor(runner, repo_dir, a, b):
    """-> True / False / None, where None means git could not answer.

    🔴 `merge-base --is-ancestor` uses its EXIT CODE as the answer: 0 true,
    1 false, anything else an error. Reading `rc != 0` as "false" folds an
    unknown ref (128) into a confident False, which would relabel an
    UNMEASURABLE adjacency as a GAP and give it a number.
    """
    rc, _out, err = runner(
        ["git", "-C", repo_dir, "merge-base", "--is-ancestor", a, b]
    )
    if rc == 0:
        return True
    if rc == 1:
        return False
    # Any other rc is an ERROR, not a False. `_classify` turns None into
    # UNMEASURABLE; collapsing it to False there would label the adjacency a GAP
    # and hand it a size.
    return None


# --------------------------------------------------------------------------- #
# Gap-commit classification — TWO certain buckets, and an honest "ask a human"
# --------------------------------------------------------------------------- #
# 🔴 THE SIGNAL IS SELF-DECLARED, WHICH IS WHY THIS IS NOT A HEURISTIC SOUP.
# Measured over 32 hand-classified tail commits (2026-09-11, devrc +
# homelab-talos + civit-datapacket-talos): EIGHT of them name the audit round
# they belong to, in their own subject line — `audit round 5`, `audit r3`,
# `round-2 audit`, `round-3 audit`, `audit round 9`.
#
# 🔴 WHAT IT MEASURES IS AN **UNLEDGERED ROUND**, NOT UNAUDITED CHURN — and the
# first version of this file asserted the opposite, which is backwards. A commit
# subject reading `audit round 5 — <what was fixed>` is EVIDENCE THAT ROUND 5
# RAN; it is that round's own fix. What the gap proves is that the round posted
# no two-sha `audited=<from>..<to>` block, so its churn chains into nobody's
# range. That is a LEDGERING defect, and reading "an audit happened here" as
# "no audit covered this" applies judgement in the direction the text
# contradicts.
#
# 🔴 THE CASE THAT SETTLES IT IS THE ONE THIS FILE FIRST CALLED ITS CONTROL.
# `eb947328` is devrc #1233's round 3 — and
# `claudedocs/audit-ladder-review-2026-09-04.md` says in terms that #1233's
# round-4 comment is TITLED "rounds 3 and 4". Round 3 was audited, inside round
# 4's comment; what was missing was its block. The review's own mechanism
# hypothesis says the same thing — "a devrc authoring habit: titling one comment
# 'rounds N and N+1' and posting a single block for both".
#
# So the residual hazard is narrower, true, and still machine-checkable:
# **round N's fix has no successor block, so the gate-reset rule was not
# honoured and nothing re-audited that delta.** State that; do not state that
# the code was never looked at.
#
# 🔴 EVERYTHING ELSE IS REPORTED UNCLASSIFIED ON PURPOSE. An earlier draft of
# this had five buckets keyed on conventional-commit types and correction verbs
# (`fix(`, `correct`, `retract`, …). That is a guess dressed as a measurement:
# `claude/RULES.md` is explicit that a guard spelled over WORDS is walkable by
# rewording, and the same applies to a classifier. So the census reports what it
# KNOWS — self-declared round references, and merges, which are structural — and
# hands the rest over by name. A smaller true answer beats a larger guessed one.
_ROUND_REF_RE = re.compile(
    r"""(?ix)
    \b(?:
        audit \s* (?: \s | - ) \s* r (?:ound)? \s* \.? \s* \d+   # audit round 3 · audit r3
      | r (?:ound)? \s* - \s* \d+ \s+ audit                      # round-2 audit
      | round \s+ \d+ \s+ (?:delta \s+ )? (?:re-)? audit         # round 4 delta re-audit
    )\b
    """
)

GapCommit = namedtuple("GapCommit", "sha parents subject round_ref is_merge")


def classify_gap_commits(runner, repo_dir, frm, to, base):
    """-> ([GapCommit], reason) for the commits that CONTRIBUTE the gap's churn.

    🔴 `--not <base>` IS LOAD-BEARING HERE AND OMITTING IT INVERTS THE ANSWER.
    Measured 2026-09-11: listing devrc #1046's tail without it showed 55 of
    `main`'s OWN squash commits, which reads exactly like "the PR kept
    developing" — i.e. it CONFIRMS the hypothesis that the tail is ordinary
    development, using commits that are not in the churn at all. The confirming
    evidence was an artifact of a dropped flag. This function exists partly so
    that flag cannot be dropped by hand again.

    ⚠ `base` must be the PR's OWN base. Two of the three repos measured use
    `trunk`; a hand-written `origin/main` failed loudly on 4 of 10 samples, which
    is the lucky failure — a WRONG-but-resolvable base would have changed the
    numbers silently.
    """
    rc, out, err = runner([
        "git", "-C", repo_dir, "log", "--format=%H%x00%p%x00%s",
        f"{frm}..{to}", "--not", base,
    ])
    if rc != 0:
        return [], (f"`git log {frm}..{to} --not {base}` exited {rc}: "
                    f"{(err or out).strip() or 'no output'}")
    if err.strip():
        return [], f"`git log` wrote to stderr: {err.strip()}"

    commits = []
    for line in out.splitlines():
        parts = line.split("\0")
        if len(parts) < 3:
            continue
        sha, parents, subject = parts[0], parts[1].split(), parts[2]
        commits.append(GapCommit(
            sha=sha, parents=parents, subject=subject,
            round_ref=bool(_ROUND_REF_RE.search(subject)),
            is_merge=len(parents) >= 2,
        ))
    return commits, None


def _classify(ad, runner, repo_dir, frm, to, base):
    """-> (label, RangeChurn|None, reason|None) for one adjacency."""
    if ad.same_commit(frm, to):
        return TIGHT, None, None

    forward = _is_ancestor(runner, repo_dir, frm, to)
    if forward is None:
        return UNRELATED, None, (
            f"`git merge-base --is-ancestor {frm} {to}` could not answer — one "
            "of these shas is not in this checkout, so whether there is a hole "
            "between them is UNKNOWN, not zero"
        )
    if not forward:
        backward = _is_ancestor(runner, repo_dir, to, frm)
        if backward is True:
            return OVERLAP, None, (
                f"`{to}` is an ANCESTOR of `{frm}`, so the next block's range "
                "starts INSIDE this one — the two ranges cover some of the same "
                "commits and a per-round table double-counts them"
            )
        return UNRELATED, None, (
            f"neither `{frm}` nor `{to}` reaches the other, so these blocks do "
            "not describe one history (a rebase mid-ladder, or a mistyped sha). "
            "There is no gap SIZE to report"
        )

    churn = ad.measure_range_churn(runner, repo_dir, frm, to, base)
    if churn.reason is not None:
        return UNRELATED, None, churn.reason
    if churn.commits == 0:
        # Reachable, distinct shas with no commits between them. `from..to` is
        # empty while the shas differ — which git produces for an empty-tree
        # commit pair and for `to` being `frm`'s own alias. Not a hole.
        return TIGHT, churn, None
    return GAP, churn, None


def measure_ladder(ad, runner, repo_dir, pr, head, base, comment_texts):
    """-> Ladder. Pure given `runner`; no `gh`, no network, no cwd dependence."""
    blocks, malformed = ad.parse_claims_blocks(comment_texts)
    usable = [b for b in blocks if b.audited_from and b.audited_to]
    # 🔴 A BLOCK THAT DID NOT PARSE, AND A BARE `audited=<sha>`, ARE THE SAME
    # HOLE BY ANOTHER ROUTE — that round contributes no range, so its churn is
    # in nobody's column exactly as #1233's round 3 is. They are reported beside
    # the adjacencies rather than folded into them, because neither has a SIZE:
    # there is no second sha to measure to.
    bare = [b.round_no for b in blocks if not (b.audited_from and b.audited_to)]
    # Stable sort on the round number, so a duplicate round (#958 posted two
    # `round=3` blocks) keeps the order it was seen in rather than being
    # reordered by sha.
    usable.sort(key=lambda b: b.round_no)

    if not usable:
        return Ladder(
            pr, head, base, len(blocks), 0, None, [], None, None, None,
            (0, 0), (0, 0),
            f"no `audit-claims` block carrying a TWO-SHA `audited=<from>..<to>` "
            f"({len(blocks)} block(s) parsed). A bare `audited=<sha>` names no "
            "range, so it cannot be chained and this report has nothing to "
            "measure between",
            malformed, bare,
        )

    adjacencies, uncovered_a, uncovered_d = [], 0, 0
    interior_a = interior_d = tail_a = tail_d = 0
    # THE POSITIVE CONTROL: a block's OWN range. A ladder's rounds changed
    # something by construction, so if every one of these measures empty the
    # commits are not here and the uncovered total below would be a zero from
    # nothing. Measured before any adjacency, so the refusal is cheap.
    control = 0
    for b in usable:
        own = ad.measure_range_churn(runner, repo_dir, b.audited_from, b.audited_to, base)
        if own.reason is None and own.commits:
            control += own.added + own.deleted

    pairs = [
        (usable[i].audited_to, usable[i + 1].audited_from,
         usable[i].round_no, usable[i + 1].round_no)
        for i in range(len(usable) - 1)
    ]
    pairs.append((usable[-1].audited_to, head, usable[-1].round_no, None))

    for frm, to, r_from, r_to in pairs:
        label, churn, reason = _classify(ad, runner, repo_dir, frm, to, base)
        added = churn.added if churn else None
        deleted = churn.deleted if churn else None
        # 🔴 `churn_commits`, NEVER `commits` — the count must come from the same
        # population as the lines beside it. MEASURED on devrc #1046's tail: this
        # report printed `55 commit(s), 1105 line(s)` when the churn population
        # was TWO commits (a 66-line fix and a 1,039-line semantic-conflict
        # resolution). 53 were an upstream bring-in that `--not <base>` excludes
        # from the churn and that the raw count included. The wrong number is the
        # flattering one: it makes a real finding look like routine drift.
        commits = churn.churn_commits if churn else None
        if label == GAP:
            uncovered_a += added or 0
            uncovered_d += deleted or 0
            # 🔴 INTERIOR AND TAIL ARE DIFFERENT CLAIMS AND MUST NOT BE SUMMED
            # INTO ONE HEADLINE. An INTERIOR gap is unambiguous: a round posted a
            # block, a later round posted one anchored past it, and the churn
            # between them was audited by nobody — #1233's round 3. A TAIL gap is
            # churn after the LAST block, which conflates two things this script
            # cannot separate: fixes made after the final block was posted (which
            # the ladder should have seen) and ordinary development that continued
            # after the ladder ended (which it should not). Measured over the
            # 2026-09-04 review's 20 ladders, the tail is 3,727 of 4,382 lines —
            # so a single total would be quoted as an under-count it does not
            # support.
            if r_to is None:
                tail_a += added or 0
                tail_d += deleted or 0
            else:
                interior_a += added or 0
                interior_d += deleted or 0
        # Only a GAP has commits worth classifying — a TIGHT adjacency has none,
        # and OVERLAP/UNRELATED have no measurable population at all.
        gap_commits = []
        if label == GAP:
            gap_commits, why = classify_gap_commits(
                runner, repo_dir, frm, to, base)
            if why:
                # Reported, never swallowed: an unclassifiable gap must not read
                # as a gap with zero round references.
                gap_commits = [GapCommit("", [], f"COULD NOT LIST: {why}",
                                         False, False)]
        adjacencies.append(
            Adjacency(label, frm, to, r_from, r_to, added, deleted, commits,
                      reason, gap_commits)
        )

    return Ladder(
        pr, head, base, len(blocks), len(usable), usable[0].round_no,
        adjacencies, control, uncovered_a, uncovered_d,
        (interior_a, interior_d), (tail_a, tail_d), None, malformed, bare,
    )


# --------------------------------------------------------------------------- #
# Facts — `gh`, or a file so a test needs neither it nor a network
# --------------------------------------------------------------------------- #

def find_carriers(runner, repo, limit=300, state="all"):
    """-> ([facts, …], note) for every PR in `repo` carrying an `audit-claims`
    fence, newest first.

    🔴 ONE ENUMERATOR, TWO CONSUMERS. Measuring the ladders in a repo and mining
    their terminal-round prose are different tasks over the SAME population, and
    that population has been built by hand twice — the 2026-09-04 review counted
    "42 of 309 merged devrc PRs" in a scratchpad that no longer exists. A
    hand-built list is also the one input nobody re-derives, so a repo added
    later is silently out of scope.

    🔴 IT IS ONE `gh` CALL, NOT ONE PER PR. `gh pr list --json comments` returns
    the bodies, so the fence test is local. Asking per PR is ~300 calls against a
    secondary rate limit and was the reason this stayed manual.

    ⚠ THE BLIND SPOT IS INHERITED AND LOAD-BEARING: `gh` does not return REVIEW
    comments here, so a block posted as a review is invisible — the same gap
    `audit-dispatch.py` warns about when it cannot find a block a human can see.
    A carrier count from this is therefore a FLOOR, never a census, and the note
    it returns says so in the output rather than in this docstring alone.
    """
    cmd = ["gh", "pr", "list", "--repo", repo, "--state", state,
           "--limit", str(limit), "--json",
           "number,comments,headRefOid,baseRefName,title"]
    rc, out, err = runner(cmd)
    if rc != 0:
        raise SystemExit(
            f"`gh pr list --repo {repo}` exited {rc}: "
            f"{(err or out).strip() or 'no output'}"
        )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise SystemExit(f"`gh pr list --repo {repo}` did not print JSON: {e}")

    carriers = []
    for pr in data:
        bodies = [c.get("body", "") for c in pr.get("comments") or []]
        if not any("audit-claims" in (b or "") for b in bodies):
            continue
        carriers.append({
            "pr": pr.get("number"),
            "head": pr.get("headRefOid") or "",
            "base": pr.get("baseRefName") or "",
            "comments": bodies,
            "title": pr.get("title") or "",
        })
    note = (
        f"{repo}: scanned {len(data)} PR(s) (state={state}, limit={limit}), "
        f"{len(carriers)} carry an `audit-claims` fence. ⚠ A FLOOR, not a census "
        "— `gh` does not return REVIEW comments, so a block posted as a review is "
        "invisible here."
    )
    if len(data) >= limit:
        note += (f" ⚠ The scan HIT ITS LIMIT ({limit}), so older PRs were not "
                 "examined at all — raise --limit before reading this as the "
                 "whole repo.")
    return carriers, note


def facts_from_gh(runner, repo, pr):
    cmd = ["gh", "pr", "view", str(pr), "--json",
           "comments,headRefOid,baseRefName,url"]
    if repo:
        cmd += ["--repo", repo]
    rc, out, err = runner(cmd)
    if rc != 0:
        raise SystemExit(
            f"`gh pr view {pr}` exited {rc}: {(err or out).strip() or 'no output'}"
        )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise SystemExit(f"`gh pr view {pr}` did not print JSON: {e}")
    return {
        "pr": pr,
        "head": data.get("headRefOid") or "",
        "base": data.get("baseRefName") or "",
        "comments": [c.get("body", "") for c in data.get("comments") or []],
        "url": data.get("url") or "",
    }


def fetch_pr_ref(runner, repo_dir, pr):
    """`git fetch origin refs/pull/<n>/head` — a MERGED PR's commits are the
    usual reason every range measures empty, and a squash merge leaves them
    reachable from no local branch. Returns a one-line note, never raises: a
    failed fetch is reported beside the refusal it causes, not instead of it.
    """
    rc, _out, err = runner([
        "git", "-C", repo_dir, "fetch", "--quiet", "origin",
        f"refs/pull/{pr}/head",
    ])
    if rc != 0:
        return (f"fetch of refs/pull/{pr}/head FAILED (rc {rc}): "
                f"{err.strip().splitlines()[-1] if err.strip() else 'no output'}")
    return f"fetched refs/pull/{pr}/head"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render(ladders, notes):
    out = []
    out.append("## ladder range coverage — churn that NO `audit-claims` block "
               "range covers")
    out.append("")
    out.append("window per ladder: [first block's `from`, head]. Churn BEFORE the "
               "first block is the PR's")
    out.append("own work, not ladder work, and is deliberately out of scope — "
               "see this script's docstring.")
    out.append("")
    for n in notes:
        out.append(f"note: {n}")
    if notes:
        out.append("")

    total_a = total_d = 0
    int_a = int_d = tl_a = tl_d = 0
    measured = 0
    for L in ladders:
        out.append(f"### PR #{L.pr}  head={L.head[:8] or '?'}  base={L.base or '?'}")
        for r in L.malformed:
            out.append(f"  🔴 UNPARSED BLOCK — {r}")
        if L.bare:
            out.append(f"  🔴 BARE `audited=<sha>` on round(s) "
                       f"{', '.join(str(r) for r in L.bare)} — one sha names no "
                       "range, so that")
            out.append("    round is in the chain's numbering and in nobody's "
                       "coverage. No SIZE is reportable.")
        if L.malformed or L.bare:
            out.append("    Both are the #1233 hole by another route: a round "
                       "that contributes no range.")
        if L.reason:
            out.append(f"  UNMEASURABLE — {L.reason}")
            out.append("")
            continue
        out.append(f"  blocks: {L.blocks_used} usable of {L.blocks_total} parsed"
                   f" · first ledgered round: {L.first_round}")
        if L.first_round and L.first_round > 1:
            out.append(f"  ⚠ this ladder's ledger STARTS at round {L.first_round},"
                       f" so rounds 1..{L.first_round - 1} are outside the window")
            out.append("    and their churn is NOT counted below. That is the "
                       "#1108 / #1219 shape.")
        if not L.control_churn:
            out.append("  🔴 REFUSED — the positive control measured ZERO: not one "
                       "block's own range")
            out.append("    has any churn in this checkout, so an uncovered total "
                       "of 0 here would be a zero")
            out.append("    from nothing rather than a tight chain. The PR's "
                       "commits are almost certainly not")
            out.append("    present — see the fetch note above.")
            out.append("")
            continue
        measured += 1
        out.append(f"  positive control: {L.control_churn} line(s) of churn across "
                   "the blocks' own ranges")
        for a in L.adjacencies:
            where = (f"round {a.from_round} `to` → round {a.to_round} `from`"
                     if a.to_round is not None
                     else f"round {a.from_round} `to` → head (TAIL)")
            if a.label == TIGHT:
                out.append(f"  TIGHT      {where}")
            elif a.label == GAP:
                out.append(f"  🔴 GAP     {where}: {a.frm[:8]}..{a.to[:8]} — "
                           f"{a.commits} commit(s), "
                           f"{(a.added or 0) + (a.deleted or 0)} line(s) "
                           f"(+{a.added}/-{a.deleted}) in NO block's range")
                for c in a.gap_commits:
                    if not c.sha:
                        out.append(f"       ⚠ {c.subject}")
                    elif c.round_ref:
                        out.append(f"       🔴 ROUND-REF  {c.sha[:8]} "
                                   f"{c.subject[:74]}")
                    elif c.is_merge:
                        out.append(f"       MERGE      {c.sha[:8]} "
                                   f"{c.subject[:74]}")
                    else:
                        out.append(f"       unclassified {c.sha[:8]} "
                                   f"{c.subject[:72]}")
            else:
                out.append(f"  {a.label}  {where}: {a.reason}")
        out.append(f"  UNCOVERED: {L.uncovered_added + L.uncovered_deleted} line(s)"
                   f" (+{L.uncovered_added}/-{L.uncovered_deleted})"
                   f" = interior {sum(L.interior)} + tail {sum(L.tail)}")
        total_a += L.uncovered_added
        total_d += L.uncovered_deleted
        int_a += L.interior[0]
        int_d += L.interior[1]
        tl_a += L.tail[0]
        tl_d += L.tail[1]
        out.append("")

    out.append(f"TOTAL uncovered across {measured} measured ladder(s): "
               f"{total_a + total_d} line(s) (+{total_a}/-{total_d})")
    out.append("")
    out.append("🔴 READ THE SPLIT, NOT THE TOTAL — they are different claims and "
               "only the first is")
    out.append("   unambiguous:")
    out.append(f"   INTERIOR  {int_a + int_d} line(s) (+{int_a}/-{int_d}) — a "
               "round posted a block, a later")
    out.append("             round anchored past it, and NOBODY audited the churn "
               "between. This is")
    out.append("             the #1233 defect, and it is the number the review's "
               "table is short by.")
    out.append(f"   TAIL      {tl_a + tl_d} line(s) (+{tl_a}/-{tl_d}) — churn "
               "after the LAST block. This")
    out.append("             conflates two things this script cannot separate: "
               "fixes posted after the")
    out.append("             final block (the ladder should have seen them) and "
               "development that")
    out.append("             continued after the ladder ended (it should not). "
               "Do NOT quote it as")
    out.append("             unaudited ladder work without reading the commits.")
    out.append("")
    out.append("⚠ RAW lines, NOT split payload/scaffolding. That split was made "
               "BY HAND, per PR, in")
    out.append("  the 2026-09-04 review; no pathspec can make it (a docs PR's "
               "payload IS the `.md`). So")
    out.append("  this total is the SIZE of the hole, not the payload the review "
               "under-counted by.")
    # 🔴 DERIVED, NEVER A LITERAL. This line first shipped reading "Three of the
    # 20 ladders look like that" — the figure from the devrc run it was written
    # during — and then printed that sentence verbatim on a 5-ladder run of a
    # different repo. A count in prose beside a measurement it is not computed
    # from is the exact defect class this whole report exists to find, committed
    # inside the report. Counted here instead.
    zero_line_gaps = sum(
        1 for L in ladders if L.reason is None
        for a in L.adjacencies
        if a.label == GAP and a.commits and not (a.added or 0) + (a.deleted or 0)
    )
    # 🔴 The census. Counts only what is SELF-DECLARED or STRUCTURAL; everything
    # else is handed over by name rather than guessed at.
    # 🔴 SPLIT BY INTERIOR/TAIL, because this file forbids summing them thirty
    # lines up and the first census did it anyway. Measured on devrc: 10 of 11
    # ROUND-REF commits are TAIL and exactly ONE is interior, so a single
    # headline is ~91% the ambiguous class — the number that would get quoted.
    def _bucket(tail_only):
        return [c for L in ladders if L.reason is None
                for a in L.adjacencies if a.label == GAP
                and ((a.to_round is None) == tail_only)
                for c in a.gap_commits if c.sha]

    gc_int, gc_tail = _bucket(False), _bucket(True)
    gc = gc_int + gc_tail
    if gc:
        def _counts(rows):
            r = sum(1 for c in rows if c.round_ref)
            m = sum(1 for c in rows if c.is_merge and not c.round_ref)
            return r, m, len(rows) - r - m

        ref, mrg, rest = _counts(gc)
        i_ref, i_mrg, i_rest = _counts(gc_int)
        t_ref, t_mrg, t_rest = _counts(gc_tail)
        gaps_with_ref = sum(
            1 for L in ladders if L.reason is None
            for a in L.adjacencies
            if a.label == GAP and any(c.round_ref for c in a.gap_commits))
        out.append("")
        out.append(f"GAP-COMMIT CENSUS over {len(gc)} commit(s) in the gaps above:")
        out.append(f"  🔴 INTERIOR  round-ref {i_ref} · merge {i_mrg} · "
                   f"unclassified {i_rest}   (of {len(gc_int)} commit(s))")
        out.append(f"     TAIL      round-ref {t_ref} · merge {t_mrg} · "
                   f"unclassified {t_rest}   (of {len(gc_tail)} commit(s))")
        out.append("  🔴 READ THE SPLIT, NOT THE TOTAL — the same rule the line "
                   "counts carry. An INTERIOR")
        out.append("     round-ref is unambiguous; a TAIL one may be a fix posted "
                   "after the final block")
        out.append("     OR work that continued after the ladder ended, and "
                   "nothing here separates them.")
        out.append(f"  🔴 ROUND-REF     {ref} — the commit's own subject names the "
                   "audit round it belongs to.")
        out.append("                      That is an UNLEDGERED ROUND: the round "
                   "RAN (this is its own fix)")
        out.append("                      and posted no two-sha `audited=` block, "
                   "so its delta chains into")
        out.append("                      nobody's range and nothing re-audited "
                   "it. 🔴 NOT evidence the code")
        out.append("                      was never looked at — the subject says "
                   "the opposite.")
        out.append(f"  MERGE          {mrg} — structural (>=2 parents). Read its "
                   "remerge-diff: a SEMANTIC")
        out.append("                      conflict resolution hides here, and is "
                   "real hand-written work.")
        out.append(f"  unclassified   {rest} — 🔴 NOT 'ordinary development'. This "
                   "tool declines to guess.")
        out.append("                      Read them; the subjects are printed "
                   "above.")
        out.append(f"  → {gaps_with_ref} of the gaps carry at least one ROUND-REF "
                   "commit.")
        out.append("⚠ THE CENSUS IS A FLOOR ON UNLEDGERED ROUNDS, NEVER A RATE. "
                   "It can only see a round")
        out.append("  reference a commit chose to write down — a round's fix with "
                   "an ordinary subject is")
        out.append("  indistinguishable here from a feature, and lands in "
                   "`unclassified`. Measured over 32")
        out.append("  hand-classified commits: 8 self-declared, and the hand pass "
                   "found 20 fixes in total.")
    if zero_line_gaps:
        out.append("⚠ A COMMIT COUNT IS NOT A CHURN COUNT. A GAP of many commits "
                   "and 0 lines is `--not")
        out.append("  <base>` working: those commits are an upstream bring-in "
                   "already in the base, which is")
        out.append(f"  shape A of the reference file's range table. "
                   f"{zero_line_gaps} gap(s) in THIS run look like that.")
    return "\n".join(out)


# --------------------------------------------------------------------------- #

def main(argv=None, runner=real_runner, out_stream=sys.stdout,
         err_stream=sys.stderr):
    ap = argparse.ArgumentParser(
        description="churn an audit ladder's claims-block ranges do not cover"
    )
    ap.add_argument("prs", nargs="*", type=int, help="PR number(s)")
    ap.add_argument("--repo", help="owner/name (default: gh's own default)")
    ap.add_argument("--repo-dir", default=".", help="the git checkout to measure in")
    ap.add_argument("--base", help="override the base ref (default: origin/<the "
                                   "PR's own baseRefName>)")
    ap.add_argument("--facts-file", help="JSON object, or list of them, with "
                                        "{pr, head, base, comments[]} — "
                                        "consults no `gh` and no network")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip `git fetch origin refs/pull/<n>/head`")
    ap.add_argument("--find-carriers", action="store_true",
                    help="enumerate every PR in --repo carrying an "
                         "`audit-claims` fence and measure all of them")
    ap.add_argument("--list-only", action="store_true",
                    help="with --find-carriers: print the carriers and stop")
    ap.add_argument("--limit", type=int, default=300,
                    help="with --find-carriers: how many PRs to scan (default 300)")
    ap.add_argument("--audit-dispatch", help="path to audit-dispatch.py")
    args = ap.parse_args(argv)

    if not args.prs and not args.facts_file and not args.find_carriers:
        ap.error("give at least one PR number, --facts-file, or --find-carriers")
    if args.find_carriers and not args.repo:
        ap.error("--find-carriers needs --repo owner/name")

    ad = load_audit_dispatch(args.audit_dispatch)

    facts_list, notes = [], []
    if args.facts_file:
        try:
            raw = json.loads(Path(args.facts_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot read --facts-file: {e}", file=err_stream)
            return EXIT_USAGE
        facts_list = raw if isinstance(raw, list) else [raw]
        notes.append("--facts-file mode: no `gh` was consulted, so nothing here "
                     "was checked against the live PR")
    elif args.find_carriers:
        facts_list, note = find_carriers(runner, args.repo, limit=args.limit)
        notes.append(note)
        if args.list_only:
            print(note, file=out_stream)
            for f in facts_list:
                print(f"  #{f['pr']}  {f['title'][:88]}", file=out_stream)
            return EXIT_OK if facts_list else EXIT_NOTHING_MEASURABLE
        if not facts_list:
            # 🔴 NOT a clean bill of health, and it is the answer rank 8 most
            # often gets: "this repo ran no ladders with a ledger" and "nobody
            # ever posted a block here" are the same observation, and neither is
            # "the ladders here are fine".
            print(note, file=out_stream)
            print("\nUNMEASURABLE — this repo has NO PR carrying an "
                  "`audit-claims` block in the scanned window, so there is no\n"
                  "ledger to measure coverage against. That is a statement about "
                  "the LEDGER, not about whether\nladders ran here: a ladder that "
                  "ran without posting a block is invisible to every instrument\n"
                  "in this family, including the review's.", file=out_stream)
            return EXIT_NOTHING_MEASURABLE
        if not args.no_fetch:
            for f in facts_list:
                notes.append(fetch_pr_ref(runner, args.repo_dir, f["pr"]))
    else:
        for pr in args.prs:
            if not args.no_fetch:
                notes.append(fetch_pr_ref(runner, args.repo_dir, pr))
            facts_list.append(facts_from_gh(runner, args.repo, pr))

    ladders = []
    for f in facts_list:
        base = args.base or f.get("base") or ""
        # A bare branch name is not a ref this checkout can resolve; the review
        # used `origin/main`, and the PR reports `main`.
        if base and "/" not in base:
            base = f"origin/{base}"
        if not f.get("head"):
            ladders.append(Ladder(
                f.get("pr", "?"), "", base, 0, 0, None, [], None, None, None,
                (0, 0), (0, 0),
                "no head sha — the TAIL adjacency (last block → head) cannot be "
                "measured, and that is where a ladder's final round's fixes sit",
                [], [],
            ))
            continue
        ladders.append(measure_ladder(
            ad, runner, args.repo_dir, f.get("pr", "?"), f["head"], base,
            f.get("comments") or [],
        ))

    print(render(ladders, notes), file=out_stream)

    if not ladders:
        return EXIT_NOTHING_MEASURABLE
    if all(L.reason for L in ladders):
        return EXIT_NOTHING_MEASURABLE
    if any(L.reason is None and not L.control_churn for L in ladders):
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
