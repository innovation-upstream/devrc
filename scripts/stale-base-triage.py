#!/usr/bin/env python3
# stale-base-triage — is this PR's red INHERITED from a stale base, and which
# commit already fixed it?
#
# WHY THIS EXISTS, MEASURED. `tekton/devrc-pytests` is the only automated signal
# on a PR in this repo and nothing blocks a merge on it, so its whole value is
# whether a human believes a red. MEASURED 2026-09-11 over the post-#1458
# window: of the SIX genuine `failure` verdicts, THREE — half — were the same
# test (`test_the_SUMMARY_BANNER_names_the_real_selection_source`) on PRs 17, 40
# and 40 commits behind `main`, all since merged, and that test passes on
# current `main`. An earlier sample had 8 of 8 failing open PRs 5-42 commits
# behind, with a rebase curing 4 outright. So roughly half the genuine-failure
# signal on the only gate there is comes from PRs re-reporting a red that `main`
# has already fixed, and a human triages a failure their diff cannot reach.
#
# 🔴 WHAT THIS IS, AND WHAT IT IS NOT. It is a REPORTER. It runs no tests, it
# makes no claim that a rebase WILL fix anything, and by default it writes
# nothing anywhere. What it does is assemble a deterministic evidence pair for
# each failing test named in the status description:
#
#   (1) the PR head's copy of the file defining that test is byte-identical to
#       the MERGE-BASE's copy — i.e. this PR never touched it; and
#   (2) `origin/main` HAS moved that file since the merge-base, in commits that
#       are demonstrably not in the PR head's history.
#
# Both halves are blob-OID and commit-list facts, so the verdict is reproducible
# by hand from the lines it prints. It still says "LIKELY cured by rebase",
# because a commit touching the test's file is a candidate fix, not a proof of
# one — only re-running the suite on the merged tree proves that, and this
# script deliberately does not pretend to.
#
# 🔴 WHY IT READS `/commits/{sha}/statuses` (PLURAL). `/commits/{sha}/status`
# (singular) is the ROLL-UP, and it maps `error` onto `failure`. Every non-code
# outcome this pipeline produces — `superseded by a newer run`, `KILLED: the
# gate pod died`, `NO GATE POD`, `clone rc 128` — arrives as `error`, and over
# 200 measured rows the roll-up turned 9 real failures into 48-49 red-looking
# pushes. A triage tool built on it would spend all of its time explaining runs
# that never ran. The list endpoint carries no roll-up field at all, so the
# mistake is unavailable here rather than merely avoided. 🔴 Same family:
# `/commits/{sha}/check-runs` returns NOTHING for these checks — that zero is an
# instrument artefact, not an absence of checks.
#
# 🔴 AND THE LIST IS NEWEST-FIRST, which is the second half of the same trap: a
# last-wins dict over it yields the OLDEST post per context. One agent shipped
# that and reported a known-green head as `pending`. `newest_per_context` folds
# by `max(created_at)` and does not depend on the array's order at all, so it is
# correct whichever way GitHub decides to return it tomorrow.
#
# 🔴 `error` IS REPORTED SEPARATELY AND IS NEVER A CODE FAILURE. Only
# `state == "failure"` is triageable. An `error` row means the gate broke, not
# the change, and folding the two together is the roll-up mistake in a second
# spelling.
#
# 🔴 COMPLETENESS IS THE HARD PART. GitHub caps a status description at 140
# characters and the gate's `FAILING: a | b | TOTAL … failed=N …` line overruns
# it constantly. MEASURED on this repo's own rows: one described `failed=7`
# while naming ONE test; one named NO test at all; one was cut MID-WORD inside a
# test name. So a description can prove "at least one test failed and here is
# its name"; it can NEVER prove "these are all of them" on the names alone.
# Dismissing a PR as INHERITED on an incomplete list would dismiss a real red,
# which is the one error this tool must not make. So the PR-level verdict
# requires PROVABLE completeness; the PER-TEST lines print either way, and a
# `HINT:` line says when a rebase is worth trying anyway.
#
# 🔴 AND THE OBVIOUS WAY TO PROVE IT — READ `failed=N` — FIRES ON ALMOST NOTHING,
# BECAUSE `failed=` IS THE FIELD THE CAP EATS. It is last in the banner, so
# whether it survives is a function of the FAILING TEST'S NAME LENGTH and
# nothing else. MEASURED 2026-09-11 on the three PRs this tool was justified by
# (#1454, #1462, #1499): all three carry `len(desc)=138`, all three are cut
# inside `failed=`, and all three therefore resolved to COULD NOT MEASURE with
# the correct answer already in hand. The verdict was a function of a test name.
#
# 🔴 SO COMPLETENESS IS ALSO DERIVED, AND THE DERIVATION IS A PROOF RATHER THAN
# A HEURISTIC. `scripts/run-tests.sh` GUARD 4 computes, per target,
#     collected = passed + skipped + failed + errors + xfailed + xpassed
#     TOT_FAILED += failed + errors        (i.e. the banner's `failed=` is f+e)
# and sums each term into the TOTAL banner. Therefore
#     collected − passed − skipped  ==  failed + xfailed + xpassed  ≥  failed
# and names are a subset of the failures, so `len(names) ≤ failed ≤ derived`.
# When `derived == len(names)` the inequality is squeezed shut and completeness
# is PROVEN. `collected=`, `passed=` and `skipped=` all precede `failed=` in the
# banner, so they are exactly the fields that survive the cut.
# 🔴 THE BOUND IS AN OVER-COUNT, NEVER AN UNDER-COUNT, and that direction is the
# whole safety argument: an under-count would let this file certify a
# completeness it does not have and dismiss a real red. `xfailed`/`xpassed` can
# only inflate it, and a `skipped=` truncated short by the cap can only inflate
# it further — both push toward WITHHOLDING. See `derived_failure_upper_bound`.
# The coupling to `run-tests.sh` is pinned by
# `test_the_run_tests_collected_arithmetic_this_derivation_rests_on_is_pinned`.
#
# 🔴 PRIOR ART, AND WHAT THIS ADDS TO IT. `scripts/main-status-watch.py`'s
# `screen_all_known_flakes` is the same completeness-proving screen in 15 lines,
# and its comment records that it would have fired ZERO times on the 100 commits
# measured. That zero is not a property of the idea — it is the `failed=N` route
# above being eaten by the cap. This file's contribution is (a) the derived
# route, which turns 0/3 into 3/3 on the justifying population, and (b) the
# EVIDENCE half the screen has no equivalent of: blob OIDs and commit lists that
# name WHICH commit on main already fixed the test. The completeness gate itself
# is prior art and is cited as such; only the derivation and the evidence are new.
#
# EXIT CODES
#   0   ran; no PR was classified INHERITED (the counts say what it DID find)
#  10   at least one PR is INHERITED — likely cured by rebase
#  11   COULD NOT MEASURE at all (no gh, no repo, no PRs read)
#   2   usage
#
# ENV SEAMS (none is read anywhere else; the ledger is pinned two-way by
# `test_every_env_var_the_code_reads_is_documented_in_the_header`)
#   STALE_BASE_TRIAGE_GH            argv0 for the API reader (default: `gh`)
#   STALE_BASE_TRIAGE_REPO          owner/name, overriding the origin remote
#   STALE_BASE_TRIAGE_BUDGET        total wall-clock seconds for all API reads
#   STALE_BASE_TRIAGE_COMMENT_MODE  off (default) | dry-run | on
# ⚠ NOTHING ELSE IN THIS COMMENT MAY BEGIN A LINE WITH A KNOB NAME — the guard
# reads the ledger above by matching `^#   STALE_BASE_TRIAGE_…`, so an ordinary
# sentence wrapping onto a line that started with one would register as a ledger
# entry for a knob that has none: a two-way pin satisfied by prose.
#
# 🔴 IT RUNS NO `git` SUBCOMMAND THAT WRITES — AT ALL, NOT MERELY "BY DEFAULT",
# AND THAT IS NOW PINNED ABSOLUTELY. It used to carry `--fetch`, which wrote
# `refs/stale-base-triage/*`, and the guard could then only say "the refs it
# writes are namespaced" — a relative claim about a repo-GLOBAL surface (`refs/`
# lives in the COMMON git dir, so a worktree gives zero isolation). `--fetch`
# was DELETED after measuring that it bought nothing here: this repo's PRs are
# same-repo branches and `origin`'s refspec is `+refs/heads/*`, so an ordinary
# `git fetch origin` already brings every head — 58 of 58 open PR heads resolved
# in the base clone with no `--fetch` anywhere. `test_no_git_subcommand_that_
# WRITES_is_ever_invoked` now enumerates the read-only subcommands as an
# ALLOWLIST, so an unknown one is a write by default.
#
# ⚠ `--json` WAS DELETED TOO, for having no consumer: `git grep` over the whole
# repo found this script and its test file and nothing else. It cost a global
# `_JSON_MODE`, a `say()` indirection on every human line, and a second exit
# path. `--sweep` was KEPT and is now the primary mode — see the measurement in
# the F2 note above: the sweep surfaces 7 eligible open reds where it surfaced
# 4, including PRs 9 commits behind that are actively being worked.
#
# 🔴 IT WRITES NOTHING BY DEFAULT, AND ARMING IT COSTS A VISIBLE LINE.
# `COMMENT_MODE_DEFAULT` is the literal `"off"`, pinned by
# `test_the_comment_mode_default_is_the_LITERAL_off` — asserting mere membership
# in the legal set would let a one-character edit arm a bot that comments on
# every open PR pass unnoticed. Change that literal in the arming commit; the
# diff should say out loud that a writer went live.
"""Triage a red PR: is the failure inherited from a stale base, and what fixed it?"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 🔴 ONE RULE, ONE PLACE. `classify` here was BYTE-IDENTICAL to
# `main-status-watch.py`'s, and the parsers were duplicated AND already
# divergent — only this copy stripped the `TOTAL` truncation fragment, only this
# copy folded statuses by timestamp. They now live in `scripts/lib/ci_status.py`
# and both files import them; the disagreement between the copies was the
# finding, and consolidating is what made it audible. What stays here is POLICY:
# which context, what completeness means, and what to do about a red.
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from ci_status import (TOTALS_RE as _TOTALS_RE,  # noqa: E402
                       classify, derived_failure_upper_bound,
                       newest_per_context, parse_failed_count,
                       parse_failing_names)

RC_OK, RC_INHERITED, RC_UNMEASURED, RC_USAGE = 0, 10, 11, 2

# The context this triage is about. A literal contract with the devrc-ci
# pipeline; a typo here yields "no verdict found" forever, which is why an empty
# read is reported as UNMEASURED and never as "nothing to triage".
CONTEXT = "tekton/devrc-pytests"

# 🔴 THE DEFAULT IS THE SAFETY CLAIM. See the header.
COMMENT_MODE_DEFAULT = "off"
COMMENT_MODES = ("off", "dry-run", "on")

# The marker that makes a posted comment idempotent. Present in a comment body
# => this PR head has already been told, and posting again is noise.
COMMENT_MARKER = "<!-- stale-base-triage -->"

TOTAL_BUDGET_S_DEFAULT = 300

VERDICT_INHERITED = "INHERITED — likely cured by rebase"
VERDICT_NOT_STALE = "NOT EXPLAINED BY STALENESS"
VERDICT_UNMEASURED = "COULD NOT MEASURE"

HEADER_SENTINEL = '"""Triage a red PR'


def say(msg=""):
    print(msg, flush=True)


class Unmeasured(Exception):
    """Raised for every reason the world could not be read. Never a verdict."""


def print_header():
    """Print the comment header — the part that documents the env knobs.

    Bounded by the docstring's opening line rather than a hardcoded range: a
    literal range silently truncates --help the moment the header grows, which
    `main-green-check.sh` records having happened to it already.
    """
    src = Path(__file__).read_text(encoding="utf-8").splitlines()
    end = next((i for i, ln in enumerate(src) if ln.startswith(HEADER_SENTINEL)), None)
    if end is None:
        say("stale-base-triage: cannot locate the end of the header")
        return False
    for line in src[1:end]:
        say(line[2:] if line.startswith("# ") else line.lstrip("#"))
    return True


# ── status classification and description parsing ─────────────────────────────
# 🔴 `classify`, `newest_per_context`, `parse_failing_names`, `parse_failed_count`
# and `derived_failure_upper_bound` are IMPORTED from `scripts/lib/ci_status.py`
# (see the import block). What each one does, and the trap it exists for, is
# documented there. Re-declaring any of them here is refused by
# `test_the_shared_predicates_are_NOT_re_declared_in_either_consumer`.


def names_provably_complete(description):
    """True only when the description PROVES the named set is the whole set.

    Returns (complete, reason). False on any doubt. TWO independent routes, and
    the DIRECT one is tried first because it is the stronger claim:

      1. `failed=N` survived the 140-char cap and equals the number of names.
      2. `failed=N` did not survive, but `collected − passed − skipped` did —
         an upper bound on `failed` (see `derived_failure_upper_bound`) — and it
         equals the number of names, squeezing `failed` to that number too.

    Route 2 exists because route 1 fires on almost nothing: `failed=` is the
    LAST field in the banner, so whether it survives is decided by the failing
    test's name length. On the three PRs this tool was justified by, route 1
    returned "could not measure" with the answer already in the row.

    This is the guard that stops an INHERITED verdict being handed out on a
    truncated row that named one of seven failures, so every arm that cannot
    PROVE the set complete returns False.
    """
    names = parse_failing_names(description)
    if not names:
        return False, "the description names no failing test"
    derived = derived_failure_upper_bound(description)
    count = parse_failed_count(description)
    if count is not None:
        # 🔴 CONSISTENCY TRIPWIRE, AND IT IS HONESTLY LABELLED: while
        # `run-tests.sh`'s arithmetic holds, `derived >= count` is guaranteed
        # and this arm CANNOT FIRE on a real row. It is here so that an
        # arithmetic change upstream shows up as a refusal to answer rather than
        # as a silently wrong bound, and it is reachable from a test only. The
        # test-time half of the same coupling is
        # `test_the_run_tests_collected_arithmetic_this_derivation_rests_on_is_pinned`.
        if derived is not None and derived < count:
            return False, (f"the banner contradicts itself: failed={count} but "
                           f"collected−passed−skipped={derived}, which cannot be "
                           "smaller — the totals arithmetic this reads is not the "
                           "one `run-tests.sh` documents")
        if count != len(names):
            return False, f"the description says failed={count} but names {len(names)}"
        return True, f"failed={count} and {len(names)} named"
    if derived is None:
        return False, ("the description was truncated before `failed=N`, and "
                       "collected/passed/skipped are not all readable either")
    if derived != len(names):
        return False, (f"the description was truncated before `failed=N`, and "
                       f"collected−passed−skipped={derived} does not equal the "
                       f"{len(names)} named")
    return True, (f"`failed=N` was cut by the 140-char cap, but "
                  f"collected−passed−skipped={derived} — an upper bound on the "
                  f"failures — equals the {len(names)} named")


# ── test name -> defining file ────────────────────────────────────────────────
_PARAM_SUFFIX_RE = re.compile(r"\[.*$")


def normalise_test_name(raw):
    """`Class.method[param-param]` -> (class_or_None, method).

    🔴 THREE SHAPES, EACH MEASURED ON A REAL ROW OF THIS REPO'S OWN CI:
      * bare              `test_no_test_writes_a_usr_bin_env_shebang_at_runtime`
      * class-qualified   `TestAHungRoundTripSAYSWhichSideBlocked.test_a_stall…`
      * parametrised      `test_a_FORGED_actor_in_the_body_is_DISCARDED[record0-…`
    The parametrise brackets are stripped BEFORE the class split, because an id
    can itself contain a dot and would otherwise be read as the class.
    """
    name = (raw or "").strip()
    name = _PARAM_SUFFIX_RE.sub("", name).strip()
    if not name:
        return None, ""
    if "." in name:
        cls, _, method = name.rpartition(".")
        return cls or None, method
    return None, name


def _git(repo, args, check=True, timeout=60):
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Unmeasured("git is not on PATH")
    except subprocess.TimeoutExpired:
        raise Unmeasured(f"git {' '.join(args[:2])} timed out after {timeout}s")
    if check and proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise Unmeasured(
            f"git {' '.join(args[:3])} exited {proc.returncode}: "
            f"{err[0] if err else '(no stderr)'}")
    return proc


def ref_exists(repo, ref):
    return _git(repo, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                check=False).returncode == 0


def path_exists_at(repo, ref, path):
    """🔴 `git cat-file -e <ref>:<path>`, NEVER `git diff --quiet <ref> -- <path>`.

    The latter exits 0 when the path exists on NEITHER side, so used as a
    freshness check it reports a reassuring "matches <ref>" for a file that has
    never existed in that tree. A comparison against an absent operand reports
    SAME, not MISSING — so existence is proved first, separately, here.
    """
    return _git(repo, ["cat-file", "-e", f"{ref}:{path}"], check=False).returncode == 0


def blob_oid(repo, ref, path):
    """The blob OID of `path` at `ref`, or None when the path is absent there."""
    proc = _git(repo, ["rev-parse", f"{ref}:{path}"], check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _grep_files(repo, ref, pattern):
    """`git grep -l -E <pattern> <ref> -- '*.py'` -> list of paths.

    rc 0 means matches, rc 1 means none, anything else is a real error and must
    not read as "none" — an unparseable grep is the same reassuring zero the
    rules warn about, so it raises instead. The `<ref>:` prefix is stripped by
    LENGTH rather than by splitting on `:`, because a ref can contain one.
    """
    proc = _git(repo, ["grep", "-l", "-E", pattern, ref, "--", "*.py"], check=False)
    out = proc.stdout or ""
    if proc.returncode == 1:
        if out.strip():
            raise Unmeasured("git grep reported no matches but printed output")
        return []
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise Unmeasured(f"git grep exited {proc.returncode}: "
                         f"{err[0] if err else '(no stderr)'}")
    prefix = f"{ref}:"
    paths = []
    for line in out.splitlines():
        if not line.startswith(prefix):
            raise Unmeasured(f"git grep printed an unexpected line: {line!r}")
        paths.append(line[len(prefix):])
    return sorted(set(paths))


def resolve_test_file(repo, ref, raw_name):
    """Map a failing test name to the ONE file that defines it, at `ref`.

    🔴 THE MAPPING IS SEARCHED, NEVER GUESSED, and every way it can fail has its
    own outcome rather than a silent fallback:
      `ok`          exactly one file defines it
      `ambiguous`   more than one does — naming a fix commit would be a coin flip
      `not-found`   none does (a renamed or deleted test, or a name this parser
                    mis-read); reported, never treated as "nothing changed"

    TWO PHASES, and the order is the guard. Phase 1 requires the opening paren,
    so a short name cannot match a longer one that starts with it — the way
    test_foo would otherwise match test_foobar. Only when phase 1 finds NOTHING
    does phase 2 drop the paren — which is the truncation case, and nothing
    else: GitHub's 140-char cap lands mid-name often enough that this repo has
    a real row ending `…_CAN_see_the_dif`. Running phase 2 unconditionally would
    turn every short name into a false `ambiguous`; see
    `test_a_COMPLETE_name_is_not_made_ambiguous_by_a_longer_sibling`.
    """
    cls, method = normalise_test_name(raw_name)
    if not method:
        return {"status": "not-found", "name": raw_name, "reason": "empty test name"}
    esc = re.escape(method)
    exact = _grep_files(repo, ref, rf"^[ \t]*(async +)?def {esc}\(")
    files, truncated = exact, False
    if not files:
        files = _grep_files(repo, ref, rf"^[ \t]*(async +)?def {esc}")
        truncated = bool(files)
    if cls and len(files) > 1:
        # A class-qualified name carries a second, independent discriminator.
        # Only ever NARROWS: if it narrows to nothing the class hint is wrong,
        # and the un-narrowed ambiguity is reported rather than an empty one.
        narrowed = [p for p in files
                    if p in _grep_files(repo, ref, rf"^[ \t]*class {re.escape(cls)}\b")]
        if narrowed:
            files = narrowed
    if not files:
        return {"status": "not-found", "name": raw_name,
                "reason": f"no file at {ref} defines `{method}`"}
    if len(files) > 1:
        return {"status": "ambiguous", "name": raw_name, "files": files,
                "reason": f"{len(files)} files define `{method}`: " + ", ".join(files)}
    return {"status": "ok", "name": raw_name, "file": files[0],
            "truncated_name": truncated}


# ── the evidence ──────────────────────────────────────────────────────────────
def commits_touching(repo, since_ref, until_ref, path):
    """Commits in `since..until` that touched `path`, newest first."""
    proc = _git(repo, ["log", "--format=%H%x09%s", f"{since_ref}..{until_ref}",
                       "--", path])
    out = []
    for line in (proc.stdout or "").splitlines():
        sha, _, subject = line.partition("\t")
        if sha.strip():
            out.append({"sha": sha.strip(), "subject": subject.strip()})
    return out


def is_ancestor(repo, maybe_ancestor, ref):
    """🔴 USED ONLY TO ASK ABOUT THE PR HEAD, NEVER TO CONCLUDE "THIS MERGED".

    A SQUASH merge never makes a branch head an ancestor of its base, so
    ancestry answers "was this merged?" with a permanent, confident FALSE — this
    repo squashes, so that reading is always wrong here. What ancestry DOES
    answer correctly is "is commit C in the history this PR head was built on?",
    which is a question about one commit and one head and involves no merge at
    all. That is the only question asked of it. The claim that a fix LANDED is
    made by CONTENT elsewhere in this file: the blob OIDs of the test's file at
    the merge-base, at the head, and at main.
    """
    return _git(repo, ["merge-base", "--is-ancestor", maybe_ancestor, ref],
                check=False).returncode == 0


def prove_candidates(repo, candidates, main_ref, head_ref):
    """Annotate each candidate with the evidence pair; keep those absent from the head.

    🔴 THE PAIR IS COMPUTED AND PRINTED; ONLY ONE HALF IS A FILTER, AND THE
    ASYMMETRY IS DELIBERATE. `on_main` is TRUE BY CONSTRUCTION for anything
    `commits_touching` returns — it walks `merge-base..main`, so every candidate
    is already an ancestor of main. Filtering on it would be a guard that can
    never run, which reads as coverage and stops anyone looking; a mutation
    sweep said exactly that (dropping it from the condition SURVIVED the whole
    suite). It is measured and printed so a reader can VERIFY the claim rather
    than trust it — never to gate on.

    `not in_head` IS a filter, and it is a separate function precisely so it can
    be REACHED: through `triage_one_test` the blob comparison rejects the shapes
    that would produce a head-ancestor candidate before this line runs. It still
    matters, because `git merge-base` returns ONE base while a criss-cross
    history has several, so a candidate in the printed range can be in the head
    after all — and must not be offered as a fix the head is missing.
    """
    proven = []
    for c in candidates:
        c["on_main"] = is_ancestor(repo, c["sha"], main_ref)
        c["in_head"] = is_ancestor(repo, c["sha"], head_ref)
        if not c["in_head"]:
            proven.append(c)
    return proven


def triage_one_test(repo, main_ref, head_ref, merge_base, raw_name):
    """One failing test -> its own verdict plus the evidence for it."""
    res = resolve_test_file(repo, head_ref, raw_name)
    if res["status"] != "ok":
        return {"name": raw_name, "verdict": VERDICT_UNMEASURED,
                "reason": res["reason"], "explained": False}
    path = res["file"]
    out = {"name": raw_name, "file": path, "explained": False,
           "truncated_name": res["truncated_name"]}

    # 🔴 EXISTENCE FIRST, ON EVERY SIDE WE ARE ABOUT TO COMPARE.
    for label, ref in (("merge-base", merge_base), ("main", main_ref)):
        if not path_exists_at(repo, ref, path):
            out["verdict"] = VERDICT_UNMEASURED
            out["reason"] = (f"`{path}` does not exist at {label} ({ref[:12]}) — "
                             "a rename or a new file; a blob comparison against "
                             "an absent operand would report SAME, not MISSING")
            return out
    base_blob = blob_oid(repo, merge_base, path)
    head_blob = blob_oid(repo, head_ref, path)
    main_blob = blob_oid(repo, main_ref, path)
    out.update({"blob_merge_base": base_blob, "blob_head": head_blob,
                "blob_main": main_blob})

    if head_blob != base_blob:
        out["verdict"] = VERDICT_NOT_STALE
        out["reason"] = (f"the PR itself modifies `{path}` (head blob "
                         f"{(head_blob or '?')[:12]} != merge-base "
                         f"{(base_blob or '?')[:12]}) — the failing test's own "
                         "file is part of this change, so staleness does not "
                         "explain it")
        return out
    if main_blob == base_blob:
        out["verdict"] = VERDICT_NOT_STALE
        out["reason"] = (f"`{path}` is unchanged on main since the merge-base "
                         f"(blob {(base_blob or '?')[:12]}) — nothing on main "
                         "can have fixed this; treat it as a real red")
        return out

    candidates = commits_touching(repo, merge_base, main_ref, path)
    if not candidates:
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = (f"`{path}` differs between the merge-base and main, but "
                         "no commit in merge-base..main touched that path (a "
                         "rename would do this)")
        return out
    proven = prove_candidates(repo, candidates, main_ref, head_ref)
    if not proven:
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = ("every candidate failed the evidence pair (on main AND "
                         "not in the PR head) — the refs may have moved under "
                         "this run")
        out["candidates"] = candidates
        return out
    out["verdict"] = VERDICT_INHERITED
    out["explained"] = True
    out["candidates"] = proven
    out["reason"] = (f"`{path}` is byte-identical at the PR head and the "
                     f"merge-base, and main has moved it in "
                     f"{len(proven)} commit(s) absent from the head")
    return out


# ── per-PR triage ─────────────────────────────────────────────────────────────
def triage_pr(repo, main_ref, pr, row):
    """Fold one PR's newest pytests row into a verdict plus its evidence."""
    out = {"number": pr["number"], "title": pr.get("title", ""),
           "head": pr["head_sha"], "url": pr.get("url", "")}
    if row is None:
        out["state"] = "none"
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = f"no `{CONTEXT}` status on this head"
        return out
    klass = classify(row.get("state"), row.get("description"))
    out["state"] = row.get("state")
    out["class"] = klass
    out["description"] = row.get("description") or ""
    out["created_at"] = row.get("created_at") or ""
    if klass != "red":
        out["verdict"] = None          # nothing to triage; NOT a verdict
        return out

    head_ref = pr["head_sha"]
    if not ref_exists(repo, head_ref):
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = (f"head {head_ref[:12]} is not in the local clone — "
                         "run `git fetch origin` and try again")
        return out
    merge_base = _git(repo, ["merge-base", main_ref, head_ref]).stdout.strip()
    out["merge_base"] = merge_base
    out["behind"] = int(_git(
        repo, ["rev-list", "--count", f"{merge_base}..{main_ref}"]).stdout.strip() or 0)

    names = parse_failing_names(out["description"])
    complete, completeness_reason = names_provably_complete(out["description"])
    out["names"] = names
    out["complete"] = complete
    out["completeness_reason"] = completeness_reason
    out["tests"] = [triage_one_test(repo, main_ref, head_ref, merge_base, n)
                    for n in names]

    explained = [t for t in out["tests"] if t["explained"]]
    unexplained = [t for t in out["tests"]
                   if t["verdict"] == VERDICT_NOT_STALE]
    out["any_explained"] = bool(explained)
    if unexplained:
        # 🔴 CONSERVATIVE BY CONSTRUCTION. One named test main cannot have fixed
        # is enough to make this a real red; dismissing it would be the only
        # error this tool must never make.
        out["verdict"] = VERDICT_NOT_STALE
        out["reason"] = (f"{len(unexplained)} named test(s) are not explained by "
                         "staleness: " + "; ".join(t["name"] for t in unexplained))
        return out
    if not names:
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = completeness_reason
        return out
    if not all(t["explained"] for t in out["tests"]):
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = "; ".join(t["reason"] for t in out["tests"]
                                  if not t["explained"])
        return out
    if not complete:
        out["verdict"] = VERDICT_UNMEASURED
        out["reason"] = (f"every NAMED failure is inherited, but {completeness_reason}"
                         " — so the named set cannot be proven complete and other "
                         "failures may remain")
        return out
    out["verdict"] = VERDICT_INHERITED
    out["reason"] = (f"all {len(names)} failing test(s) are inherited from a base "
                     f"{out['behind']} commits behind main")
    return out


# ── IO ────────────────────────────────────────────────────────────────────────
_DEADLINE = None


def _remaining():
    return 60.0 if _DEADLINE is None else _DEADLINE - time.monotonic()


def gh_json(path, args=(), timeout=60):
    gh = os.environ.get("STALE_BASE_TRIAGE_GH", "gh")
    left = _remaining()
    if left <= 0:
        raise Unmeasured(f"ran out of its API budget before reading {path}")
    timeout = min(timeout, left)
    try:
        proc = subprocess.run([gh, "api", path, *args],
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Unmeasured(f"{gh} is not on PATH")
    except subprocess.TimeoutExpired:
        raise Unmeasured(f"gh api {path} timed out after {timeout}s")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise Unmeasured(f"gh api {path} exited {proc.returncode}: "
                         f"{err[0] if err else '(no stderr)'}")
    try:
        return json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise Unmeasured(f"gh api {path} returned unparseable JSON: {exc}")


def resolve_repo(repo_path):
    override = os.environ.get("STALE_BASE_TRIAGE_REPO", "").strip()
    if override:
        return override
    proc = _git(repo_path, ["remote", "get-url", "origin"], check=False)
    if proc.returncode != 0:
        raise Unmeasured("cannot read origin remote (not a git checkout?)")
    url = proc.stdout.strip()
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    if not m:
        raise Unmeasured(f"cannot parse owner/name out of origin url: {url!r}")
    return m.group(1)


def comment_mode(cli_value):
    """off | dry-run | on — CLI wins over env, and anything unrecognised is off.

    🔴 FAILS CLOSED. A typo in the env var must silently disarm the writer, not
    silently arm it, and must never raise: this runs on the reporting path.
    """
    raw = (cli_value or os.environ.get("STALE_BASE_TRIAGE_COMMENT_MODE")
           or COMMENT_MODE_DEFAULT).strip().lower()
    return raw if raw in COMMENT_MODES else COMMENT_MODE_DEFAULT


def comment_body(result):
    lines = [COMMENT_MARKER,
             f"**{VERDICT_INHERITED}** — this head is **{result['behind']}** "
             f"commits behind `main`.", ""]
    for t in result["tests"]:
        for c in t.get("candidates", []):
            lines.append(f"- `{t['name']}` is defined in `{t['file']}`, which "
                         f"`main` moved in {c['sha'][:12]} (_{c['subject']}_) — "
                         "a commit that is on `main` and not in this head.")
    lines += ["", "The PR's own copy of each file above is byte-identical to the "
              "merge-base, so this change did not cause these failures. Rebasing "
              "on `main` is likely to clear them.",
              "", "_Reported by `scripts/stale-base-triage.py`; it ran no tests._"]
    return "\n".join(lines)


def already_commented(repo_slug, number):
    rows = gh_json(f"/repos/{repo_slug}/issues/{number}/comments?per_page=100")
    if not isinstance(rows, list):
        raise Unmeasured("comment listing was not a list")
    return any(COMMENT_MARKER in (r.get("body") or "") for r in rows
               if isinstance(r, dict))


def post_comment(repo_slug, number, body):
    gh = os.environ.get("STALE_BASE_TRIAGE_GH", "gh")
    proc = subprocess.run(
        [gh, "api", "-X", "POST", f"/repos/{repo_slug}/issues/{number}/comments",
         "-f", f"body={body}"], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        raise Unmeasured(f"posting a comment exited {proc.returncode}: "
                         f"{err[0] if err else '(no stderr)'}")
    return True


# ── rendering ─────────────────────────────────────────────────────────────────
def render(result):
    n = result["number"]
    say(f"── PR #{n}  {result['title'][:80]}")
    say(f"   head {result['head'][:12]}   {result.get('url','')}")
    klass = result.get("class")
    # 🔴 THE THREE CASES ARE PRINTED APART, because an earlier draft folded the
    # first into the third and printed `failure   posted ?` above a verdict that
    # said "no status on this head" — a line asserting a red that was never
    # posted. Measured on #359/#355/#612/#646 in the first live sweep.
    if result.get("state") == "none" or klass is None:
        say(f"   {CONTEXT}: no status on this head")
    elif klass != "red":
        say(f"   {CONTEXT}: {result['state']} ({klass}) — "
            "🔴 NOT a code failure: the gate broke, not the change")
        if result.get("description"):
            say(f"       {result['description'][:140]}")
    else:
        say(f"   {CONTEXT}: failure   posted {result.get('created_at','?')}")
    if result.get("verdict") is None:
        say("   nothing to triage")
        say()
        return
    if "behind" in result:
        say(f"   base is {result['behind']} commits behind main "
            f"(merge-base {result.get('merge_base','?')[:12]})")
    if result.get("description"):
        say(f"   description: {result['description'][:140]}")
    for t in result.get("tests", []):
        say(f"   • {t['name']}")
        if t.get("file"):
            say(f"       file: {t['file']}"
                + ("   (name was truncated by GitHub's 140-char cap)"
                   if t.get("truncated_name") else ""))
        say(f"       {t['verdict']}: {t['reason']}")
        for c in t.get("candidates", []):
            say(f"       evidence: {c['sha'][:12]} {c['subject'][:70]}")
            say(f"                 on main: {c['on_main']}   "
                f"in this head: {c['in_head']}")
        if t.get("blob_merge_base"):
            say(f"       blobs: merge-base {t['blob_merge_base'][:12]}  "
                f"head {(t.get('blob_head') or '-')[:12]}  "
                f"main {(t.get('blob_main') or '-')[:12]}")
    if result.get("completeness_reason"):
        say(f"   completeness: {result['completeness_reason']}")
    say(f"   VERDICT: {result['verdict']}")
    if result.get("reason"):
        say(f"            {result['reason']}")
    if result["verdict"] != VERDICT_INHERITED and result.get("any_explained"):
        say("   HINT: at least one named failure IS inherited — a rebase is "
            "worth trying before debugging.")
    say()


def summarise(results, errors):
    inherited = [r for r in results if r.get("verdict") == VERDICT_INHERITED]
    not_stale = [r for r in results if r.get("verdict") == VERDICT_NOT_STALE]
    unmeasured = [r for r in results if r.get("verdict") == VERDICT_UNMEASURED]
    reds = inherited + not_stale + unmeasured
    say("── SUMMARY")
    say(f"   PRs read:               {len(results)}")
    say(f"   red ({CONTEXT}):        {len(reds)}")
    say(f"   INHERITED:              {len(inherited)}"
        + ("  " + ", ".join(f"#{r['number']}" for r in inherited) if inherited else ""))
    say(f"   NOT EXPLAINED:          {len(not_stale)}"
        + ("  " + ", ".join(f"#{r['number']}" for r in not_stale) if not_stale else ""))
    say(f"   COULD NOT MEASURE:      {len(unmeasured)}"
        + ("  " + ", ".join(f"#{r['number']}" for r in unmeasured) if unmeasured else ""))
    # 🔴 A BROKEN GATE IS NEVER A CODE FAILURE, so it is counted on its own line
    # and never inside the red total. Folding it in is the roll-up mistake.
    if errors:
        say(f"   broken gate (`error`):  {len(errors)}  "
            + ", ".join(f"#{r['number']}({r.get('class')})" for r in errors))
    # 🔴 A ZERO HERE IS NOT AN ALL-CLEAR UNTIL IT IS SHOWN OBSERVABLE. The
    # counts above are printed together for exactly that reason: `INHERITED: 0`
    # beside `red: 0` says the population was empty, while `INHERITED: 0` beside
    # `red: 7` says seven reds were examined and none was explained. Those are
    # different claims and the summary must never collapse them.
    if not reds:
        say("   ⚠ no red heads in this population — an INHERITED count of 0 here "
            "is an empty sample, not an all-clear.")
    return len(inherited)


# ── main ──────────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(add_help=False, description=__doc__)
    p.add_argument("--pr", type=int, action="append", default=[],
                   help="triage this PR (repeatable)")
    p.add_argument("--sweep", action="store_true", help="triage every open PR")
    p.add_argument("--repo-path", default=str(Path(__file__).resolve().parents[1]))
    p.add_argument("--main-ref", default="origin/main",
                   help="ref standing for main (default: origin/main)")
    p.add_argument("--comment-mode", choices=COMMENT_MODES, default=None,
                   help=f"post a PR comment (default {COMMENT_MODE_DEFAULT})")
    p.add_argument("-h", "--help", action="store_true")
    return p


def main(argv):
    global _DEADLINE
    args = build_parser().parse_args(argv)
    if args.help:
        return RC_OK if print_header() else RC_UNMEASURED
    if not args.pr and not args.sweep:
        say("usage: stale-base-triage.py [--pr N ...] [--sweep]  (--help for the header)")
        return RC_USAGE
    budget = os.environ.get("STALE_BASE_TRIAGE_BUDGET", "")
    _DEADLINE = time.monotonic() + (int(budget) if budget.isdigit()
                                    else TOTAL_BUDGET_S_DEFAULT)
    repo = Path(args.repo_path)
    mode = comment_mode(args.comment_mode)

    try:
        slug = resolve_repo(repo)
        if args.sweep:
            rows = gh_json(f"/repos/{slug}/pulls?state=open&per_page=100")
            if not isinstance(rows, list):
                raise Unmeasured("the pull-request listing was not a list")
            prs = [{"number": r["number"], "title": r.get("title", ""),
                    "head_sha": r["head"]["sha"], "url": r.get("html_url", "")}
                   for r in rows]
        else:
            prs = []
            for n in args.pr:
                r = gh_json(f"/repos/{slug}/pulls/{n}")
                prs.append({"number": r["number"], "title": r.get("title", ""),
                            "head_sha": r["head"]["sha"],
                            "url": r.get("html_url", "")})
        main_ref = args.main_ref
        if not ref_exists(repo, main_ref):
            raise Unmeasured(f"{main_ref} does not resolve in {repo}")
    except Unmeasured as exc:
        say(f"{VERDICT_UNMEASURED} — {exc}")
        return RC_UNMEASURED

    say(f"repo {slug}   main-ref {main_ref} "
        f"({_git(repo, ['rev-parse', main_ref]).stdout.strip()[:12]})   "
        f"comment-mode {mode}")
    say()
    results, errors = [], []
    for pr in prs:
        try:
            rows = gh_json(f"/repos/{slug}/commits/{pr['head_sha']}/statuses?per_page=100")
            row = newest_per_context(rows if isinstance(rows, list) else []).get(CONTEXT)
            res = triage_pr(repo, main_ref, pr, row)
        except Unmeasured as exc:
            res = {"number": pr["number"], "title": pr.get("title", ""),
                   "head": pr["head_sha"], "url": pr.get("url", ""),
                   "verdict": VERDICT_UNMEASURED, "reason": str(exc)}
        if res.get("class") in ("superseded", "killed", "no-gate-pod", "error-other"):
            errors.append(res)
        results.append(res)
        render(res)

    n_inherited = summarise(results, errors)

    if mode == "off":
        say(f"comment: mode=off (set --comment-mode on to arm it); "
            f"{n_inherited} PR(s) would have been eligible")
    else:
        for r in results:
            if r.get("verdict") != VERDICT_INHERITED:
                continue
            body = comment_body(r)
            if mode == "dry-run":
                say(f"comment: DRY-RUN would comment on #{r['number']}")
                continue
            try:
                if already_commented(slug, r["number"]):
                    say(f"comment: #{r['number']} already carries the marker")
                    continue
                post_comment(slug, r["number"], body)
                say(f"comment: posted on #{r['number']}")
            except Unmeasured as exc:
                say(f"comment: COULD NOT POST on #{r['number']} — {exc}")

    if not results:
        return RC_UNMEASURED
    return RC_INHERITED if n_inherited else RC_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
