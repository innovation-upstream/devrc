#!/usr/bin/env python3
"""The overlap-bucketing contention figure is RETRACTED — it may be quoted, never asserted.

🔴 WHY THIS EXISTS. `CLAUDE.md` retracted "14.5 min alone -> 48.9 min at 6+
overlapping" and says in terms that **re-deriving it is the trap**: bucketing
runs by how many others overlapped them is length-biased (a long run overlaps
more runs BY CONSTRUCTION), and a null Monte Carlo with zero interaction
reproduces the shape, the 14.5-min baseline and ~2.06x of the 3.4x.

It was retracted in `CLAUDE.md` and in `claudedocs/handoff-gate-speed-and-ci-signal.md`
— and `scripts/scoped-tests.sh` went on stating it as the measured reason for its
own existence, in its `WHY THIS EXISTS` header. One tree, two files, opposite
claims, with the live one justifying a script. Nothing noticed, because a
retraction in prose cannot reach a comment in a shell script.

🔴 THE RULE THIS ENFORCES IS A RELATIONSHIP, NOT A WORD. A plain "the string
must not appear" guard would fail the three legitimate sites that quote the
figure IN ORDER to retract it. So the binding rule pins the normalised TEXT
around every occurrence, two-way.

🔴 WHAT THIS DOES NOT CATCH — stated, not solved. It is keyed on the literal
digits, so a re-derivation of the same CLAIM in DIFFERENT digits ("11.2 min ->
39.1 min at 5+ overlapping") passes untouched, exactly as the plain guard it
replaces would. An earlier draft of this docstring said a plain guard was wrong
"in both directions" and then resolved only one of them, which read as coverage
this file does not provide. The defence against a re-derivation is the prose in
CLAUDE.md and in `scoped-tests.sh`'s header telling you not to, not this test.

An INVARIANT GUARD, not regression coverage — it pins a property no shipped bug
violated once `scoped-tests.sh` was fixed in the same commit. Its evidence is
the positive control below, which fails if the scanner is wired to nothing, and
the mutation battery in the PR.
"""
import hashlib
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
from testlib.public_ip_scan import SKIP_SUFFIXES, repo_files  # noqa: E402

# The figure, in the spellings it has actually been written in.
#
# 🔴 BOTH BRANCHES REQUIRE `min`, AND THAT IS THE POINT. A bare `\b48\.9\b`
# matched "48.9% of runs", "an image 48.9 MB" and "cost $48.9" — this repo is
# full of measured numbers in prose, and a hit on one produces a confident,
# WRONG instruction to delete an unrelated measurement. The figure is always a
# duration, so the unit is what separates it from every other 48.9.
#
# 🔴 `min(?:ute)?s?` BECAUSE `min\b` IS A SPELLING, NOT A UNIT. An earlier
# draft demanded exactly `min` while its own comment said "the unit is what
# separates it" — so `48.9 minutes at 6+ overlapping`, `14.5 minutes alone`,
# `48.9 mins` and `the 48.9-minute figure` were ALL invisible, every one of
# which the pre-fix pattern had caught. That is the regression this guard
# exists for, re-opened by the fix that narrowed it. Measured: all four now
# match, and the three false positives above still do not.
#
# 🔴 `[-\s]*` because the HYPHENATED spelling is the one CLAUDE.md's own
# retraction uses ("the 14.5-min baseline"), and so does this file's sibling
# note in `scoped-tests.sh`. `\b14\.5\s*min\b` did not match it, so a
# re-assertion written "14.5-min at 0 overlap" was invisible to the whole guard.
FIGURE = re.compile(
    r"\b48\.9[-\s]*min(?:ute)?s?\b|\b14\.5[-\s]*min(?:ute)?s?\b", re.I)

# How much normalised text around each match is pinned.
CONTEXT = 90

# ⚠ A THIRD TEST WAS DELETED HERE, DELIBERATELY — AND THE COVERAGE OVERLAPS
# RATHER THAN NESTS. It asserted "a retraction appears within NEAR_LINES=20
# LINES of the figure". The `unledgered` check below catches what mattered: any
# file carrying the figure that the ledger does not name. But "strictly wider"
# was FALSE and is retracted — constructed and measured: a ledgered site whose
# retraction sits beyond CONTEXT normalised chars can lose that retraction with
# the digest UNCHANGED, which the deleted test would have caught. Not reachable
# for the three current sites (their markers fall inside the 90-char windows),
# so it is latent. 🔴 A FOURTH LEDGER ENTRY INHERITS IT — if you add one, check
# its retraction marker lands inside the window.

# 🔴 BINARY FILES ARE SKIPPED BY EXTENSION, and `repo_files` does NOT do this —
# it filters directories only. Read as text with `errors="replace"`, a byte run
# inside a binary can spell the figure between word boundaries and produce a
# spurious hit nobody can diagnose.
#
# 🔴 `testlib`'s OWN SET, not a fourth hand-rolled copy. An earlier draft of
# this file wrote its own tuple in the same commit whose headline was a
# one-rule-one-place consolidation, and it diverged both ways (added
# .sqlite/.db/.tar, omitted .webp/.xz/.zst/.otf/.so/.bin). The two extras this
# tree actually tracks are kept as a named local addition.
_EXTRA_BINARY = frozenset({".sqlite", ".db", ".tar"})
_BINARY_SUFFIXES = SKIP_SUFFIXES | _EXTRA_BINARY


def _tracked_text_files():
    """-> (relpath, lines) for every tracked text file.

    🔴 VIA `repo_files`, NOT A HAND-ROLLED `git ls-files`. `flake.nix`'s
    `checks.pytests` builds from a `cp -r` of the flake source with NO `.git`,
    and `scripts/tests` is in `run-tests.sh`'s HERMETIC_TARGETS — so an
    unguarded `git ls-files` exits 128 THERE while passing on the dev host.
    MEASURED on this file's first version: 3 failed in a no-`.git` replica of
    the head tree, while `test_doc_path_rot.py` — which carries the documented
    fallback — passed 76/76 in the SAME replica. `repo_files` owns that
    fallback; this is the one-rule-one-place call into it.
    """
    for path in repo_files(REPO):
        if path.suffix.lower() in _BINARY_SUFFIXES:
            continue
        try:
            rel = str(path.relative_to(REPO))
            yield rel, path.read_text(encoding="utf-8", errors="replace").splitlines()
        except (OSError, ValueError):
            continue


# 🔴 THE `*` BRANCH REQUIRES A FOLLOWING SPACE. Without it this ate one star
# of a markdown `**BOLD**` run landing at a line start, so rewrapping
# CLAUDE.md's block changed the pinned window and failed the guard with
# "the text CHANGED" over an edit that changed no words. `* ` is a markdown
# bullet or a C continuation line; `**` is emphasis and must survive untouched.
# ⚠ THIS FIXES THE STAR CAUSE ONLY, NOT ALL REFLOW SENSITIVITY — the hyphen
# cause below survives it. Two blocks in this file used to credit the same
# failures to different causes; they cannot both be right.
_COMMENT_LEAD = re.compile(r"^[ \t]*(?:#+[ \t]?|//+[ \t]?|\*[ \t])")
_WS = re.compile(r"\s+")


def _normalised(lines):
    """The file as ONE whitespace-collapsed string, comment markers stripped.

    Stripping the leading `#`/`//` makes a shell-comment rewrap invisible;
    collapsing whitespace absorbs a rewrap's newlines. A REWORD is not cosmetic
    and must fail.

    ⚠ THIS IS NOT "REFLOW-SAFE BY CONSTRUCTION" — that claim was made in this
    file, in its sibling comments and in its commit message, and it is FALSE.
    Reproduce it: rewrap CLAUDE.md to width 80 and this normalisation's output
    CHANGES, because a hyphen break splits a word (`re-deriving` -> `re-
    deriving`) and no amount of whitespace collapsing rejoins it. The star
    cause is fixed above; this one is not.

    🔴 AN EARLIER DRAFT SAID "the digest moved at 3 of 7 reflow widths, the
    count version at 1 of 7" AND NAMED NONE OF THE SEVEN. `claude/RULES.md`
    requires naming the points you measured, so that pair was unreproducible as
    written and is WITHDRAWN rather than restated — a wider independent sweep
    agreed on the DIRECTION (the text pin is more reflow-sensitive than the
    count it replaced, not less) and disagreed on the proportions. The
    direction is what the retraction rests on and it stands; the numbers were
    mine and I could not reproduce them.

    What the text pin genuinely buys over a count is catching an in-place
    reword, which no count can. If a reflow fails this guard, read the printed
    text before assuming a reword.
    """
    return _WS.sub(" ", " ".join(_COMMENT_LEAD.sub("", l) for l in lines)).strip()


def _hits():
    """-> [(relpath, window)] — one entry per match, with its pinned context."""
    found = []
    for rel, lines in _tracked_text_files():
        if rel == "scripts/tests/test_retracted_contention_figure.py":
            continue  # this file quotes the figure to define it
        text = _normalised(lines)
        for m in FIGURE.finditer(text):
            lo = max(0, m.start() - CONTEXT)
            found.append((rel, text[lo:m.end() + CONTEXT]))
    return found


# 🔴 THE LEDGER PINS NORMALISED TEXT, AND BOTH EARLIER VERSIONS WERE WALKABLE.
#
# v1 asserted "a RETRACTED marker appears within 20 lines of the figure". It
# SURVIVED the mutant that matters — re-inserting the bare claim into
# `scoped-tests.sh`'s header, the exact site it was re-derived at before —
# because the retraction note the same commit added sat ten lines below and
# satisfied the window. No window size fixes that: an in-place edit is always
# adjacent to the note that excuses it.
#
# v2 asserted a per-file COUNT. It ALSO survived, for a different reason: the
# killing mutant REPLACES the quoting line with an assertion built from the
# SAME two tokens, so the count is identical whether you count lines or
# matches. A count cannot tell quotation from assertion.
#
# So this pins the text, which is what `claude/RULES.md` prescribes for a prose
# artifact: "when the artifact under test IS prose … pin the WHOLE normalised
# string. A cosmetic reword then fails the test — pay it, for a machine-readable
# claim."
#
# Digest of the sorted normalised windows around every occurrence in each file.
# Regenerate ONLY after reading the text the failure prints — see that message.
EXPECTED_SITES = {
    "CLAUDE.md": "007080f0f6c54edb",
    "claudedocs/handoff-gate-speed-and-ci-signal.md": "701dca06b11b1941",
    "scripts/scoped-tests.sh": "fde2e081df2cd385",
}


def _digest(windows):
    h = hashlib.sha256()
    for w in sorted(windows):
        h.update(w.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def test_the_scanner_can_see_the_figure_at_all():
    """🔴 POSITIVE CONTROL. A reassuring zero below is worthless without it.

    The figure IS in this tree — quoted by every site that retracts it — so a
    scanner that finds nothing is a scanner wired to nothing, not a clean tree.
    `claude/RULES.md`: report the pair, never the zero alone.
    """
    hits = _hits()
    assert len(hits) >= 2, (
        f"the scanner found {len(hits)} occurrence(s) of the retracted figure. "
        "At least the retracting sites should match, so this is a broken "
        "scanner rather than a clean tree — check FIGURE against how the "
        "figure is actually spelled now."
    )


def test_each_ledgered_site_still_QUOTES_the_figure_rather_than_ASSERTING_it():
    """🔴 THE BINDING ASSERTION — pinned TEXT, because two counts were walkable.

    Pinned two-way: a file carrying the figure with no ledger entry fails, an
    entry naming a file that no longer carries it fails, and any change to the
    normalised text around an occurrence fails.

    The last one is the point. The killing mutant rewrites the quoting line IN
    PLACE into an assertion built from the same tokens — identical under any
    count, adjacent to the retraction note under any proximity window, and
    caught here only because the words changed.
    """
    sites = {}
    for rel, window in _hits():
        sites.setdefault(rel, []).append(window)

    unledgered = sorted(r for r in sites if r not in EXPECTED_SITES)
    assert not unledgered, (
        "\n\nthe RETRACTED contention figure appears in file(s) with no ledger "
        f"entry: {unledgered}.\n"
        "  If this is a new RETRACTION, add it with its digest. If it is a new "
        "ASSERTION, delete it — bucketing runs by overlap is length-biased and "
        "that dataset cannot size contention."
    )
    stale = sorted(r for r in EXPECTED_SITES if r not in sites)
    assert not stale, (
        "\n\nledger entries naming a file that no longer carries the figure: "
        f"{stale}.\n  A ledger that names nothing reads as coverage that no "
        "longer runs — drop the entry."
    )
    moved = {r: (EXPECTED_SITES[r], _digest(w))
             for r, w in sites.items() if _digest(w) != EXPECTED_SITES[r]}
    if moved:
        detail = []
        for r in sorted(moved):
            exp, got = moved[r]
            detail.append(f"{r}: expected {exp}, found {got}")
            detail += [f"    …{w}…" for w in sorted(sites[r])]
        raise AssertionError(
            "\n\nthe text around the RETRACTED contention figure CHANGED:\n  "
            + "\n  ".join(detail)
            + "\n\n  🔴 READ THE TEXT ABOVE BEFORE UPDATING THE DIGEST. The "
              "failure this guard exists for is a quoting line rewritten IN "
              "PLACE into an assertion — same tokens, same count, retraction "
              "note still beside it. `scripts/scoped-tests.sh` once stated the "
              "figure as the measured reason for its own existence while "
              "CLAUDE.md retracted it.\n"
              "  Still QUOTING it in order to retract it? Legitimate edit — "
              "update the digest.\n"
              "  ASSERTING it? Delete the assertion. Say 'dozens of concurrent "
              "full suites' with no magnitude; the 20.1-min median and the "
              "60.0s collection cost are NOT retracted and are what justify "
              "scoping.\n"
              "  ⚠ A REFLOW CAN REACH HERE. Most rewrapping is absorbed (the "
              "file is collapsed to one whitespace-normalised string with "
              "comment markers stripped), but a hyphen break inside a word "
              "moves the text. Read the printed window: if the WORDS are "
              "unchanged and only the breaks moved, it is a reflow — update "
              "the digest."
        )
