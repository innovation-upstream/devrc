"""The handoff-document size budget: the ceiling, the step, and the ledger.

🔴 WHY THIS IS A LIB AND NOT A TEST CONSTANT — it was the latter until 2026-09-13.
`scripts/tests/test_handoff_doc_size.py` still OWNS the POLICY: it is the thing
that fails, it carries the whole rationale for the ceiling and the grandfather
design, and it prints the eviction playbook. What moved here is only the NUMBERS,
because a SECOND reader appeared: `handoff_doc.py` warns an author BEFORE a write
that their update would go over. Production code importing a test module is a
direction this repo has nowhere else (checked: zero occurrences), while
test → lib is the direction that file already uses for `handoff_index`.

🔴 THE WARNING EXISTS BECAUSE THE GATE HAS NO FEEDBACK LOOP. Measured
2026-09-13, the evening the gate landed: THREE different documents went over in
one session, and each author found out when an UNRELATED PR went red. `/handoff`
appends by design and nothing in that loop says you are approaching a ceiling.

🔴 AND THE WARNING NEVER REFUSES. A blocking check DEADLOCKS against
`~/.claude/hooks/handoff-write-guard.py`, which blocks Stop until a handoff is
written: a session working on one of the documents already grandfathered OVER the
base ceiling could then neither record its work nor end its turn. The write
guard's own measurement — 22 of 253 sessions never recorded, ZERO because a gate
correctly declined — says an unrecorded session costs more than an oversized doc.
"""
from __future__ import annotations

import math

# UP requires saying in the commit message which document could not be expressed
# in the budget, and why eviction was not the answer.
#
# ⚠ THE CURRENT SIZES ARE DELIBERATELY NOT WRITTEN DOWN HERE — they are derived
# measurements edited in the same commits as the things they measure, and
# test_rules_size.py records three consecutive rounds where exactly that went
# stale inside its own PR. The failure messages PRINT current / ceiling / over-by
# and the exact ledger line to paste; that is the authority.
MAX_BYTES = 65_536

# The quantum a grandfathered allowance is rounded up to. See "WHY THE ALLOWANCE
# IS QUANTISED" above. One step is ~1.2 median documents, so it is a real
# working margin rather than a rounding artefact.
GRANDFATHER_STEP = 16_384

# 🔴 THE LEDGER. Explicit and ENUMERATED, never a pattern — an unlisted document
# over the ceiling is a failure by default.
#
# path -> allowance in bytes, which must be `ceil(measured / GRANDFATHER_STEP) *
# GRANDFATHER_STEP`. Do not hand-compute it: every failure message prints the
# exact line to paste.
#
# 🔴 REMOVING AN ENTRY IS THE GOAL. A doc that comes back under MAX_BYTES fails
# (c) until its entry is deleted, so this dict can only shrink over time unless
# someone deliberately adds to it.
#
# 🔴 NO MEASURED SIZE IS RECORDED HERE — see the ⚠ note above `MAX_BYTES` for
# why. Read the file (`stat -c %s <path>`); every failure message prints
# current / allowance / over-by and the exact line to paste, and that is the
# authority. An allowance only ever changes by a deliberate edit to its line
# below.
GRANDFATHERED: dict[str, int] = {
    "claudedocs/handoff-tmux-webapp.md": 180_224,
    "claudedocs/handoff-audit-pr-ladder.md": 196_608,
    "claudedocs/handoff-cairn-oss-multi-instance.md": 98_304,
    "claudedocs/handoff-cairn-phase3.md": 163_840,
    "claudedocs/handoff-nix-disk-cleanup.md": 114_688,
    "claudedocs/handoff-subsystem-store.md": 98_304,
    "claudedocs/handoff-gate-flake-store-api.md": 98_304,
    "claudedocs/handoff-tmux-restore-chain.md": 98_304,
    "claudedocs/handoff-skill-chain-usage-audit.md": 81_920,
    "claudedocs/handoff-cairn-task-linkage.md": 81_920,
    # 🔴 THE TWELFTH ENTRY IS A MERGED-TREE FINDING, NOT A DAY-ONE MEASUREMENT,
    # and it is worth a line because it is the shape this ledger will keep
    # meeting. This doc did not exist when the ceiling was measured; it landed on
    # `main` while this branch was open. The two changes share NO FILE — the
    # branch never touched it and `main` never touched this module — so both
    # sides were green and only the MERGED tree was red. `claude/RULES.md`:
    # "DISJOINT FILES ARE NOT SAFETY … one side widens a function's required
    # inputs, the other adds a CALLER". Here the gate is the widened input and a
    # new document is the caller. Caught by merging `main` in and re-running,
    # which is the check that rule asks for.
    "claudedocs/handoff-gate-speed-and-ci-signal.md": 81_920,
    # `claudedocs/handoff-handoff-search-index.md` was the twelfth entry and is
    # GONE: it was pruned back under MAX_BYTES on its own, so check (c) demands
    # the entry be deleted rather than left standing.
    # That is the ratchet working — an entry is not a permanent exemption, and
    # leaving it here would have let the doc regrow 22 KB unobserved.
}


def tightest_allowance(size: int, step: int = GRANDFATHER_STEP) -> int:
    """The smallest multiple of `step` that fits `size`. PURE.

    The one place the quantisation is expressed, so the ledger, the failure
    messages and (e)'s check cannot disagree about what a correct entry is.
    """
    return max(step, math.ceil(size / step) * step)
