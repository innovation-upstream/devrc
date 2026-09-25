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

🔴 AND THE WARNING NEVER REFUSES. An UNCLEARABLE blocking check DEADLOCKS
against `~/.claude/hooks/handoff-write-guard.py`, which blocks Stop until a
handoff is written: a session working on one of the documents already
grandfathered OVER the base ceiling could then neither record its work nor end
its turn. The write guard's own measurement — 22 of 253 sessions never recorded,
ZERO because a gate correctly declined — says an unrecorded session costs more
than an oversized doc.

⚠ THAT SENTENCE ONCE READ "a blocking check", FULL STOP, AND IT IS NOW NARROWER
THAN IT LOOKS. `handoff_doc.py`'s rule (p) IS a blocking check on these numbers:
a doc already over its allowance may not GROW (`status=size-ratchet`, exit 14).
What makes it not the deadlock above is that it is CLEARABLE two ways that do
not require the doc to come back under the ceiling — a net-<=-0 delta, or
`--override-size-ratchet "<why>"`, which always lands and records the reason on
the commit. The word that was missing is UNCLEARABLE; the measurement behind it
is unchanged and still decides the direction every such gate must fail in.
`budget_warning` itself still refuses nothing.
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

# 🔴 WHERE THE GATE THAT ENFORCES `MAX_BYTES` LIVES, relative to a repo root.
# It is HERE, beside the number it enforces, because two tools must answer "does
# a gate read THIS repo?" and a second copy is how they come to disagree:
# `handoff_doc.gate_enforces_budget()` re-exports it, and `handoff-audit.py`
# resolves it against each AUDITED root. That distinction is the whole point —
# asking whether the file exists next to the SCRIPT answers a question about the
# script's own checkout and is true in every devrc clone, which made the banner
# claim a gate for corpora nothing enforces (round 1 of #1815, F1). The cost of
# that class is recorded in `gate_enforces_budget`'s own docstring: civitai/cli
# #618, 35,517 B evicted against a gate that could not see the repo.
GATE_RELPATH = "scripts/tests/test_handoff_doc_size.py"

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
#
# 🔴 THE KEY IS A BARE REPO-RELATIVE PATH, AND SINCE #1871 THIS LEDGER COVERS
# DOCUMENTS IN OTHER REPOSITORIES TOO. `handoff_doc.py` runs against a `--repo`
# the caller names, so rule (p) reads this dict for a doc in ANY checkout, and
# the lookup carries no repo component. Two consequences the next person adding
# an entry has to hold at once:
#
#   * 🔴 ONE ENTRY GOVERNS EVERY REPO THAT HAS A DOC OF THAT NAME. There is no
#     collision today — measured across devrc, cairn and homelab-talos when the
#     grandfathering block below was added: no filename appears in two of them,
#     and none of the 17 new paths collided with the 11 that were already here.
#     That is a measurement of one moment, not a property. Two repos that grow a
#     `claudedocs/handoff-<same-topic>.md` share ONE allowance, and the larger
#     document is the one that decides it.
#   * 🔴 `test_handoff_doc_size.py` CAN ONLY SEE devrc's TREE, so its check (d)
#     ("an entry naming a path that does not exist went stale") is FALSE for a
#     foreign entry. `LIVES_ELSEWHERE` below is what declares those, and it is
#     the only reason a foreign entry does not red that gate on day one.
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

    # --- #1871: the ALREADY-OVER population in the OTHER repos this tool runs
    # --- against, grandfathered so rule (p) ratchets each doc from where it is
    # --- rather than refusing the next update to all seventeen on day one.
    #
    # 🔴 EVERY PATH BELOW IS ALSO IN `LIVES_ELSEWHERE`. Measured 2026-09-25 with
    # `handoff_index.handoff_paths_on_disk` over three repos: devrc 0 of 136
    # docs over allowance, cairn 2 of 3, homelab-talos 15 of 75. devrc's own
    # corpus contributes NOTHING here, which is why the ledger above did not
    # need to grow for it.
    #
    # ⚠ ONE OF THEM IS AN `-archive.md` SINK, AND IT IS THE ENTRY THAT MAKES THE
    # REFUSAL'S OWN REMEDY 2 WORTH READING TWICE — see `write-gate.md` §I, which
    # owns that finding. It gets an entry like the rest: the operator's ruling
    # was to grandfather, never to exempt archives from `is_handoff_doc`.
    "claudedocs/handoff-cairn-control-plane.md": 98_304,
    "claudedocs/handoff-cairn-control-plane-archive.md": 147_456,
    "claudedocs/handoff-chief-cairn-client.md": 163_840,
    "claudedocs/handoff-clawgate-task-detail-page.md": 81_920,
    "claudedocs/handoff-clawgate-to-muster-extraction.md": 163_840,
    "claudedocs/handoff-clawgate-ux-audit.md": 114_688,
    "claudedocs/handoff-clickup-mirror.md": 294_912,
    "claudedocs/handoff-clickup-mirror-check.md": 98_304,
    "claudedocs/handoff-comic-flex.md": 360_448,
    "claudedocs/handoff-homelab-ci-alerting-lock-leak.md": 98_304,
    "claudedocs/handoff-media-autoremixer.md": 98_304,
    "claudedocs/handoff-nebula-pre-departure-hardening.md": 98_304,
    "claudedocs/handoff-promptver-teardown.md": 81_920,
    "claudedocs/handoff-session-makework-audit.md": 147_456,
    "claudedocs/handoff-tekton-ci-budget-sizing.md": 98_304,
    "claudedocs/handoff-tekton-ci-speedup.md": 180_224,
    "claudedocs/handoff-tekton-remote-dispatch.md": 327_680,
}

# 🔴 THE LEDGER PATHS WHOSE DOCUMENT IS NOT IN devrc, and the repo that holds
# each. A SECOND STRUCTURE RATHER THAN A RICHER VALUE ON PURPOSE:
# `GRANDFATHERED` stays `dict[str, int]` because `handoff_doc.budget_position`
# reads it with a bare `.get(relpath, MAX_BYTES)` and three mutation rows in
# `scripts/tests/mutation_battery_handoff_archive_and_cap.py` anchor on whole
# ledger lines — widening the value would move all four for a field only one
# reader wants.
#
# 🔴 WHAT IT BUYS, AND WHAT IT COSTS. It buys check (d): an entry named here is
# EXPECTED to be absent from devrc's corpus, so it is not reported stale. It
# costs check (d) for exactly those entries — devrc's gate cannot see a rename
# in another repo, and nothing else will. That is not a hole this module can
# close; a gate reads one tree. `test_handoff_doc_size.py` spends the mapping
# the other way instead, asserting that every path here IS in `GRANDFATHERED`
# and is NOT present in devrc — so the collision hazard the ledger's header
# names fails LOUDLY the day devrc grows a doc of one of these names, rather
# than silently handing two documents one allowance.
LIVES_ELSEWHERE: dict[str, str] = {
    "claudedocs/handoff-cairn-control-plane.md": "cairn",
    "claudedocs/handoff-cairn-control-plane-archive.md": "cairn",
    "claudedocs/handoff-chief-cairn-client.md": "homelab-talos",
    "claudedocs/handoff-clawgate-task-detail-page.md": "homelab-talos",
    "claudedocs/handoff-clawgate-to-muster-extraction.md": "homelab-talos",
    "claudedocs/handoff-clawgate-ux-audit.md": "homelab-talos",
    "claudedocs/handoff-clickup-mirror.md": "homelab-talos",
    "claudedocs/handoff-clickup-mirror-check.md": "homelab-talos",
    "claudedocs/handoff-comic-flex.md": "homelab-talos",
    "claudedocs/handoff-homelab-ci-alerting-lock-leak.md": "homelab-talos",
    "claudedocs/handoff-media-autoremixer.md": "homelab-talos",
    "claudedocs/handoff-nebula-pre-departure-hardening.md": "homelab-talos",
    "claudedocs/handoff-promptver-teardown.md": "homelab-talos",
    "claudedocs/handoff-session-makework-audit.md": "homelab-talos",
    "claudedocs/handoff-tekton-ci-budget-sizing.md": "homelab-talos",
    "claudedocs/handoff-tekton-ci-speedup.md": "homelab-talos",
    "claudedocs/handoff-tekton-remote-dispatch.md": "homelab-talos",
}


def tightest_allowance(size: int, step: int = GRANDFATHER_STEP) -> int:
    """The smallest multiple of `step` that fits `size`. PURE.

    The one place the quantisation is expressed, so the ledger, the failure
    messages and (e)'s check cannot disagree about what a correct entry is.
    """
    return max(step, math.ceil(size / step) * step)
