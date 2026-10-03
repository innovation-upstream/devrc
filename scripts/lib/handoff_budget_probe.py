#!/usr/bin/env python3
"""Size ONE handoff document against its budget, for `resume-state.sh`.

🔴 WHY THIS EXISTS. A session learned its handoff budget at WRITE time, which is
after the expensive part: `budget_warning` fires while the text is already
composed, and rule (p) (`size-ratchet`, exit 14) REFUSES there. The resume digest
printed SKILL, GIT/PR, WORKLOAD, ALERTS, CLAWGATE, INVESTIGATIONS, DOD and DRIFT
and said nothing about bytes — so the second pass to prune or evict was the only
way to find out. This is what lets the digest say it up front (#1996).

🔴 IT RETYPES NO NUMBER. `MAX_BYTES` and the ledger come from `handoff_budget`;
the band comes from `handoff_doc.BUDGET_NEAR_BYTES`, which is where the warning
reads it. ⚠ NOT from `handoff_budget.GRANDFATHER_STEP` directly, even though the
band is currently defined as that: reading the step would be a SECOND derivation
that keeps agreeing until someone decouples the two, at which point the digest
would quietly describe a band the warning no longer uses.

🔴 AND THE LEDGER LOOKUP IS `handoff_budget.lookup`, NOT A `.get`. A document in
another repository is keyed by a digest of its relpath because devrc is public,
so a bare `.get` would resolve devrc's own docs and report every FOREIGN
grandfathered doc as massively over its budget — the exact false alarm the
`gated` distinction below exists to prevent. `claude/RULES.md`: one rule, one
place.

⚠ THE TEXT COMES FROM STDIN, NOT FROM THE PATH, AND THAT IS THE POINT.
`resume-state.sh` reconciles a working-tree copy against `origin/<default>` and
may choose EITHER; sizing the file on disk would answer about a copy the digest
did not read, which is the same class of error `handoff_freshness` was written to
close. The relpath argument is used ONLY to resolve the ledger key.

Output is TSV `key<TAB>value` on stdout, so the caller formats and this file
decides nothing about presentation. The reason always goes to stderr rather than
being implied by a zero.

🔴 THREE EXIT CODES, BECAUSE TWO OF THE FAILURES ARE NOT THE SAME KIND OF
FAILURE and the digest must channel them differently:

  0  answered.
  1  COULD NOT answer — bad usage, an unreadable module. A source that did not
     answer, so the caller raises a GAP.
  2  NO BUDGET APPLIES: the resolved document is not a `claudedocs/**/handoff-*`
     doc, so no ceiling governs it and none went unmeasured. ⚠ This is reachable
     on an ORDINARY path — `resume-state.sh` also resolves `claudedocs/*HANDOFF*.md`
     — and routing it to the gap channel would fire the `!! GAPS` banner on every
     such run. `claude/RULES.md`'s permanently-red-gate objection; `dod_block`
     took the same ruling for a doc with no closing-condition field, and for the
     same reason: nothing failed to answer, the document simply is not governed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import handoff_budget  # noqa: E402
import handoff_doc  # noqa: E402


def probe(repo: Path, relpath: str, text: str) -> dict[str, object]:
    """Facts about `text` sized against `relpath`'s budget. PURE apart from the
    `gate_enforces_budget` filesystem read, which asks whether `repo` SHIPS the
    gate — not whether this script's own checkout does. Asking the latter is how
    a banner once claimed a gate for corpora nothing enforces (#1815 F1).
    """
    size = len(text.encode("utf-8"))
    hit = handoff_budget.lookup(relpath, handoff_budget.GRANDFATHERED)
    allowance = handoff_budget.MAX_BYTES if hit is None else hit
    band = handoff_doc.BUDGET_NEAR_BYTES
    headroom = allowance - size
    if size > allowance:
        zone = "over"
    elif headroom < band:
        zone = "band"
    else:
        zone = "clear"
    # 🔴 THE THOUSANDS SEPARATORS ARE APPLIED HERE, NOT IN THE SHELL, AND THAT IS
    # A CORRECTNESS CHOICE RATHER THAN TIDINESS. `printf "%'d"` is LOCALE-DEPENDENT
    # — measured on this host, `LC_ALL=C printf "%'d" 65536` prints `65536` with no
    # separator while the UTF-8 locale prints `65,536`. The digest would then
    # format its numbers differently from `budget_warning`, which uses Python's
    # `{:,}` and always separates, and a test suite that does not pin the locale is
    # structurally blind to the difference. Python's `{:,}` is locale-independent,
    # so emitting the display strings makes the two agree by construction.
    return {
        "bytes": size,
        "allowance": allowance,
        "ceiling": handoff_budget.MAX_BYTES,
        "headroom": headroom,
        "band": band,
        "grandfathered": "yes" if hit is not None else "no",
        "gated": "yes" if handoff_doc.gate_enforces_budget(repo) else "no",
        "zone": zone,
        "bytes_fmt": f"{size:,}",
        "allowance_fmt": f"{allowance:,}",
        "headroom_fmt": f"{headroom:,}",
        "band_fmt": f"{band:,}",
        # Only meaningful in the `over` zone; always present so the caller never
        # has to branch on whether a field exists.
        "over_by_fmt": f"{max(0, -headroom):,}",
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(f"usage: {Path(__file__).name} <repo-root> <relpath>  # text on stdin",
              file=sys.stderr)
        return 1
    repo, relpath = Path(argv[0]), argv[1]
    # 🔴 THE SAME PREDICATE THE WARNING USES, AND IT IS NOT A STRING TEST HERE BY
    # ACCIDENT: `budget_position` gates on `claudedocs/` + `/handoff-`, so a
    # digest that answered for `claudedocs/proposal-x.md` would report a ceiling
    # nothing applies. Asked of `budget_position` rather than re-spelled.
    if not handoff_doc.budget_position(relpath, "", "").is_handoff_doc:
        print(f"no budget applies: {relpath} is not a claudedocs/**/handoff-* doc",
              file=sys.stderr)
        return 2
    facts = probe(repo, relpath, sys.stdin.read())
    for k, v in facts.items():
        print(f"{k}\t{v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
