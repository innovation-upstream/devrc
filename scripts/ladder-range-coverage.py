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
    "Adjacency", "label frm to from_round to_round added deleted commits reason"
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
        commits = churn.commits if churn else None
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
        adjacencies.append(
            Adjacency(label, frm, to, r_from, r_to, added, deleted, commits, reason)
        )

    return Ladder(
        pr, head, base, len(blocks), len(usable), usable[0].round_no,
        adjacencies, control, uncovered_a, uncovered_d,
        (interior_a, interior_d), (tail_a, tail_d), None, malformed, bare,
    )


# --------------------------------------------------------------------------- #
# Facts — `gh`, or a file so a test needs neither it nor a network
# --------------------------------------------------------------------------- #

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
    out.append("⚠ A COMMIT COUNT IS NOT A CHURN COUNT. A GAP of many commits and "
               "0 lines is `--not")
    out.append("  <base>` working: those commits are an upstream bring-in already "
               "in the base, which is")
    out.append("  shape A of the reference file's range table. Three of the 20 "
               "ladders look like that.")
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
    ap.add_argument("--audit-dispatch", help="path to audit-dispatch.py")
    args = ap.parse_args(argv)

    if not args.prs and not args.facts_file:
        ap.error("give at least one PR number, or --facts-file")

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
