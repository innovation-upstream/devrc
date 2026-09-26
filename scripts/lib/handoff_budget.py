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
# 🔴 REMOVING AN ENTRY IS THE GOAL — FOR A devrc ENTRY. A devrc doc that comes
# back under MAX_BYTES fails (c) until its entry is deleted.
#
# 🔴 AND FOR A `LIVES_ELSEWHERE` ENTRY THAT PROPERTY IS LOST, NOT WEAKENED —
# MEASURED, NOT INFERRED. `test_handoff_doc_size.oversize_findings` computes (b)
# over-allowance, (c) now-fits and (e)'s TIGHTNESS half inside
# `for path, size in sorted(sizes.items())`, and `sizes` is devrc's tree, so a
# path that is not in it reaches none of the three. (d) is switched off for those
# entries on purpose. Driven both ways with the real function: the same foreign
# path PRESENT in `sizes` produces the (b) / (c) / (e) finding, ABSENT it produces
# none. There is no second channel either — `handoff_doc.budget_warning`'s
# "DELETE its GRANDFATHERED entry" arm is `and gated`, and
# `gate_enforces_budget` is False for every one of the four non-devrc checkouts
# (True for devrc, which is that measurement's positive control).
#
# So: for a foreign entry this dict does NOT ratchet down, and NOTHING checks
# that it should. A foreign doc pruned back under the ceiling keeps a slack
# allowance it may silently regrow into, and a foreign doc renamed leaves an
# entry no gate can see. What still holds is stated where it is true, and no
# wider: `handoff_doc.py`'s rule (p) is NOT gated on `gate_enforces_budget`, so
# it still refuses GROWTH past the allowance below in ANY repo at write time —
# the allowance is a real bound, it is just no longer a TIGHTENING one. And
# `test_every_grandfathered_entry_is_a_correctly_stepped_allowance` still pins
# every entry here as a multiple of the step and strictly over MAX_BYTES, which
# is tree-independent and therefore applies to foreign entries too.
#
# ⚠ A CHEAP REAL CHECK WAS CONSIDERED AND NOT ADDED, and the reason is mechanical
# rather than a judgement call. A test that re-measured each foreign entry
# against its declared repo when that checkout is present would have to SKIP when
# it is not, and `scripts/run-tests.sh` GUARD 2 pins the skip set EXACTLY: its
# only conditional is `unset:VAR` — ONE variable, and it must be the same
# predicate the test uses — while the predicate here is four
# `handoff_index.REPO_ENV_HANDLES` handles plus four path existence checks. A
# flat pin then reds the host where the checkouts DO exist and the test runs,
# which is the failure the SIGNAL_PG_DSN entry in that file records. The
# sanctioned route exists and is named so nobody re-derives it: register such a
# test in `DEVHOST_TARGETS` with a structural pin asserting the registration, the
# way `test_nvim_clipboard_osc52.py` does. It was measured GREEN on this host
# (0 findings over all 17 pre-existing foreign entries), so it would be adding a
# gate, not fixing a red.
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
#     collision today, measured across ALL FIVE repos this tool can be pointed
#     at — the four `handoff_index.REPO_ENV_HANDLES` resolve, plus cairn, which
#     has NO handle and was therefore enumerated by hand: 572 handoff docs, 0
#     filenames appearing in two repos, and 0 of the 54 paths added below
#     colliding with the 28 that were already here. ⚠ THE PREVIOUS ROUND
#     MEASURED THIS OVER THREE REPOS — devrc, cairn, homelab-talos — off guessed
#     paths rather than off the handles, and missed 357 documents and 54 breaches
#     entirely. Enumerate from `REPO_ENV_HANDLES`, never from a path anyone
#     writes down. Both directions of the collision detector were watched to
#     move: injecting one repo's doc name into another's set takes the count from
#     0 to 1, so the zero is a fact about the corpus and not about a detector
#     wired to nothing.
#     That is still a measurement of one moment, not a property. Two repos that
#     grow a `claudedocs/handoff-<same-topic>.md` share ONE allowance, and the
#     larger document is the one that decides it.
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
    # --- rather than refusing their next update on day one.
    #
    # 🔴 READ THE SCALE BEFORE READING THE RULING AS CHEAP, BECAUSE THE SCALE IS
    # WHAT IT COSTS. This ledger goes from 28 entries to 82 and `LIVES_ELSEWHERE`
    # from 17 to 71. 71 of 82 entries — the large majority — are therefore
    # foreign, and for every one of them the anti-decay checks (b), (c), (d) and
    # (e)'s tightness half are all OFF, for the reasons measured at the top of
    # this comment. That is not a caveat on the decision; it is the decision. A
    # ledger designed as a ratchet is, after this change, mostly a list of
    # allowances that no gate will ever tighten. It was taken knowingly — the
    # alternative was refusing the next update to 54 live documents in a repo
    # devrc's gate has never been able to read — and it is written here at full
    # size so nobody later reports it as a discovery.
    #
    # 🔴 EVERY PATH BELOW IS ALSO IN `LIVES_ELSEWHERE`. Measured 2026-09-25 with
    # `handoff_index.handoff_paths_on_disk`, over the population enumerated from
    # `handoff_index.REPO_ENV_HANDLES` ITSELF plus cairn, which has no handle:
    #
    #   repo (label)        docs   over its allowance with this block in place
    #   devrc                136   0
    #   homelab-talos         75   0
    #   datapacket-talos     357   0   (54 of them BECAUSE of this block)
    #   civitai                1   0
    #   cairn                  3   0
    #
    # devrc's own corpus contributes NOTHING here, which is why the ledger above
    # did not need to grow for it.
    #
    # ⚠ THE FIRST CUT OF THIS BLOCK SCANNED THREE REPOS AND WAS SHORT BY 54
    # DOCUMENTS, and the mechanism is worth one line because it is repeatable:
    # the three-repo population came from hand-written paths, so the two handles
    # that were set and not guessed — `$DATAPACKET` (357 docs) and `$CIVITAI` —
    # were absent from the scan and every breach in them was invisible. All four
    # handles are set on the measuring host; `handoff_index.unset_repo_handles()`
    # returned `()`. Enumerate from the handles.
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

    # --- #1871 round 2: the SAME ruling, applied to the population the
    # --- first cut MIS-MEASURED. 54 documents in $DATAPACKET, none of which
    # --- the three-repo scan could see. Sorted by path, not by size, so a
    # --- reader can find an entry; the sizes are in no comment here by the
    # --- ⚠ rule above `MAX_BYTES`.
    #
    # 🔴 THE TIGHTEST GAP IN THIS BLOCK IS 571 B
    # (`handoff-linstor-commitment-visibility.md`, 179,653 B against an
    # allowance of 180,224 B). Every one of the 54 is STRICTLY above its
    # measured size, which is the ruling: grandfather at the quantum so
    # nothing is red on its own next byte. A doc whose size were an exact
    # multiple of the step would break that — `tightest_allowance` returns
    # the size itself there — and none of these is.
    "claudedocs/handoff-abuse-detection-moderator-dashboard.md": 163_840,
    "claudedocs/handoff-alert-recall-and-skill-consumers.md": 147_456,
    "claudedocs/handoff-app-blocks-bridge-validator-counter.md": 81_920,
    "claudedocs/handoff-app-blocks-earning-and-supply.md": 81_920,
    "claudedocs/handoff-app-blocks-loading-skeleton.md": 81_920,
    "claudedocs/handoff-app-blocks-orchestrator-access.md": 147_456,
    "claudedocs/handoff-app-blocks-post-from-app.md": 114_688,
    "claudedocs/handoff-app-frame-redesign.md": 163_840,
    "claudedocs/handoff-app-listing-collaborators-2026-08-13.md": 98_304,
    "claudedocs/handoff-app-listing-polish-and-coverage.md": 147_456,
    "claudedocs/handoff-app-taste-rollout.md": 163_840,
    "claudedocs/handoff-appblock-tool-calling.md": 425_984,
    "claudedocs/handoff-appblocks-flag-access.md": 163_840,
    "claudedocs/handoff-apps-build-consolidation.md": 131_072,
    "claudedocs/handoff-bot-account-detection-research.md": 81_920,
    "claudedocs/handoff-cairn-backup-durability.md": 131_072,
    "claudedocs/handoff-civitai-ci-sharding-and-mock-drift.md": 81_920,
    "claudedocs/handoff-claude-pool.md": 180_224,
    "claudedocs/handoff-clickhouse-tracker-dead-letter.md": 98_304,
    "claudedocs/handoff-clickup-task-audit.md": 196_608,
    "claudedocs/handoff-clickup-urgent-high-triage.md": 81_920,
    "claudedocs/handoff-cocry-tiering-2026-08-12.md": 114_688,
    "claudedocs/handoff-cp-k8s-skew.md": 98_304,
    "claudedocs/handoff-csam-archive-oom.md": 131_072,
    "claudedocs/handoff-datapacket-api.md": 131_072,
    "claudedocs/handoff-db-pool-defaults-2026-08-17.md": 98_304,
    "claudedocs/handoff-discord-tester-feedback-isolation.md": 98_304,
    "claudedocs/handoff-dp-error-triage.md": 147_456,
    "claudedocs/handoff-dp-prod-excursion-smt-and-hpa-floors.md": 81_920,
    "claudedocs/handoff-dp1-node-cost-and-drainability-archive.md": 81_920,
    "claudedocs/handoff-dp1-node-cost-and-drainability.md": 131_072,
    "claudedocs/handoff-draft-reaper-bug.md": 81_920,
    "claudedocs/handoff-endpoint-load-and-cache-exposure-2026-08-23.md": 81_920,
    "claudedocs/handoff-etcd-cp-disk.md": 147_456,
    "claudedocs/handoff-external-ip-source-drift.md": 147_456,
    "claudedocs/handoff-faro-rum-blind-spot.md": 98_304,
    "claudedocs/handoff-feedback-triage-surface.md": 81_920,
    "claudedocs/handoff-hidemeta-derivative-strip.md": 147_456,
    "claudedocs/handoff-image-cacher-cf-edge-ttl-tradeoff.md": 114_688,
    "claudedocs/handoff-kafka-cdc-exposure.md": 131_072,
    "claudedocs/handoff-linstor-commitment-visibility.md": 180_224,
    "claudedocs/handoff-meilisearch-relocation.md": 98_304,
    "claudedocs/handoff-minio-chat-storage.md": 131_072,
    "claudedocs/handoff-model-benchmarking-mobile-chrome.md": 98_304,
    "claudedocs/handoff-mongo-ha.md": 114_688,
    "claudedocs/handoff-og-500s-and-redis-cow-headroom.md": 163_840,
    "claudedocs/handoff-playable-collections-feedback-round-3.md": 98_304,
    "claudedocs/handoff-playable-collections-feedback-round-4.md": 163_840,
    "claudedocs/handoff-r2-b2-tiering-thrash.md": 196_608,
    "claudedocs/handoff-skill-prune-campaign.md": 81_920,
    "claudedocs/handoff-ssr-cpu-regression.md": 180_224,
    "claudedocs/handoff-staging-preview-next-orch.md": 81_920,
    "claudedocs/handoff-verify-b2-grain-and-ci-consolidation-2026-08-12.md": 114_688,
    "claudedocs/handoff-wedge-wave-per-node-and-inverted-hypothesis-2026-08-14.md": 81_920,
}

# 🔴 THE LEDGER PATHS WHOSE DOCUMENT IS NOT IN devrc, and the repo that holds
# each. A SECOND STRUCTURE RATHER THAN A RICHER VALUE ON PURPOSE:
# `GRANDFATHERED` stays `dict[str, int]` because `handoff_doc.budget_position`
# reads it with a bare `.get(relpath, MAX_BYTES)` and three mutation rows in
# `scripts/tests/mutation_battery_handoff_archive_and_cap.py` anchor on whole
# ledger lines — widening the value would move all four for a field only one
# reader wants.
#
# 🔴 WHAT IT BUYS, AND WHAT IT COSTS — AND THE COST IS WIDER THAN (d), WHICH IS
# WHAT AN EARLIER WORDING HERE GOT WRONG. It buys exactly one thing: an entry
# named here is EXPECTED to be absent from devrc's corpus, so check (d) does not
# report it stale.
#
# What it costs is FOUR checks, not one, and the extra three are not a
# consequence of this dict at all — they are a consequence of the path being
# foreign, which is why declaring it here cannot buy them back:
#
#   (b) over its own allowance ....... OFF. Needs the doc's size.
#   (c) back under MAX_BYTES ......... OFF. Needs the doc's size.
#   (d) the entry went stale ......... OFF, deliberately, by this dict.
#   (e) tightest-step, TIGHTNESS half  OFF. Needs the doc's size.
#   (e) tightest-step, WELL-FORMED ... ON. Tree-independent; see below.
#
# MEASURED rather than read off the source: (b), (c) and (e)'s tightness all sit
# inside `oversize_findings`' `for path, size in sorted(sizes.items())`, and
# `sizes` is devrc's own walk. Driving the real function with a foreign path
# PRESENT in `sizes` produces each finding; ABSENT, all three are empty.
#
# 🔴 AND NOTHING ELSE CHECKS THEM — there is no second channel, which is the half
# worth stating because a reader assumes one. `handoff_doc.budget_warning`'s
# "DELETE its GRANDFATHERED entry" arm is `and gated`, and
# `handoff_doc.gate_enforces_budget` is False for every non-devrc checkout on the
# measuring host (True for devrc — the positive control for that measurement), so
# that arm is unreachable in precisely the repos these entries name.
#
# 🔴 SO THE PROPERTY IS LOST FOR THESE ENTRIES, AND THAT IS THE WHOLE STATEMENT.
# It is not traded for something, it is not closed elsewhere, and this dict is
# not the thing that lost it. A gate reads one tree; a cross-repo ledger is a
# ledger most of whose rows no gate can re-measure. The decision to pay that is
# recorded above `GRANDFATHERED`, at its true scale.
#
# What IS still true, stated no wider than it holds:
#
#   * `handoff_doc.py`'s rule (p) is NOT gated on `gate_enforces_budget`, so a
#     foreign doc still cannot GROW past its allowance through this tool, in any
#     repo. The allowance bounds the doc; nothing tightens the allowance.
#   * `test_every_grandfathered_entry_is_a_correctly_stepped_allowance` reads the
#     ledger and not the tree, so every entry here — foreign included — is still
#     pinned to a multiple of `GRANDFATHER_STEP` and strictly over `MAX_BYTES`.
#   * `test_handoff_doc_size.py` spends this mapping the other way, asserting
#     that every path here IS in `GRANDFATHERED` and is NOT present in devrc — so
#     the collision hazard the ledger's header names fails LOUDLY the day devrc
#     grows a doc of one of these names, rather than silently handing two
#     documents one allowance.
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

    # #1871 round 2 — the 54 $DATAPACKET documents. Same ruling, correct
    # population. The value is a repo LABEL and never a path, for
    # `handoff_index.REPO_ENV_HANDLES`' own stated reason: this repo is
    # PUBLIC.
    "claudedocs/handoff-abuse-detection-moderator-dashboard.md": "datapacket-talos",
    "claudedocs/handoff-alert-recall-and-skill-consumers.md": "datapacket-talos",
    "claudedocs/handoff-app-blocks-bridge-validator-counter.md": "datapacket-talos",
    "claudedocs/handoff-app-blocks-earning-and-supply.md": "datapacket-talos",
    "claudedocs/handoff-app-blocks-loading-skeleton.md": "datapacket-talos",
    "claudedocs/handoff-app-blocks-orchestrator-access.md": "datapacket-talos",
    "claudedocs/handoff-app-blocks-post-from-app.md": "datapacket-talos",
    "claudedocs/handoff-app-frame-redesign.md": "datapacket-talos",
    "claudedocs/handoff-app-listing-collaborators-2026-08-13.md": "datapacket-talos",
    "claudedocs/handoff-app-listing-polish-and-coverage.md": "datapacket-talos",
    "claudedocs/handoff-app-taste-rollout.md": "datapacket-talos",
    "claudedocs/handoff-appblock-tool-calling.md": "datapacket-talos",
    "claudedocs/handoff-appblocks-flag-access.md": "datapacket-talos",
    "claudedocs/handoff-apps-build-consolidation.md": "datapacket-talos",
    "claudedocs/handoff-bot-account-detection-research.md": "datapacket-talos",
    "claudedocs/handoff-cairn-backup-durability.md": "datapacket-talos",
    "claudedocs/handoff-civitai-ci-sharding-and-mock-drift.md": "datapacket-talos",
    "claudedocs/handoff-claude-pool.md": "datapacket-talos",
    "claudedocs/handoff-clickhouse-tracker-dead-letter.md": "datapacket-talos",
    "claudedocs/handoff-clickup-task-audit.md": "datapacket-talos",
    "claudedocs/handoff-clickup-urgent-high-triage.md": "datapacket-talos",
    "claudedocs/handoff-cocry-tiering-2026-08-12.md": "datapacket-talos",
    "claudedocs/handoff-cp-k8s-skew.md": "datapacket-talos",
    "claudedocs/handoff-csam-archive-oom.md": "datapacket-talos",
    "claudedocs/handoff-datapacket-api.md": "datapacket-talos",
    "claudedocs/handoff-db-pool-defaults-2026-08-17.md": "datapacket-talos",
    "claudedocs/handoff-discord-tester-feedback-isolation.md": "datapacket-talos",
    "claudedocs/handoff-dp-error-triage.md": "datapacket-talos",
    "claudedocs/handoff-dp-prod-excursion-smt-and-hpa-floors.md": "datapacket-talos",
    "claudedocs/handoff-dp1-node-cost-and-drainability-archive.md": "datapacket-talos",
    "claudedocs/handoff-dp1-node-cost-and-drainability.md": "datapacket-talos",
    "claudedocs/handoff-draft-reaper-bug.md": "datapacket-talos",
    "claudedocs/handoff-endpoint-load-and-cache-exposure-2026-08-23.md": "datapacket-talos",
    "claudedocs/handoff-etcd-cp-disk.md": "datapacket-talos",
    "claudedocs/handoff-external-ip-source-drift.md": "datapacket-talos",
    "claudedocs/handoff-faro-rum-blind-spot.md": "datapacket-talos",
    "claudedocs/handoff-feedback-triage-surface.md": "datapacket-talos",
    "claudedocs/handoff-hidemeta-derivative-strip.md": "datapacket-talos",
    "claudedocs/handoff-image-cacher-cf-edge-ttl-tradeoff.md": "datapacket-talos",
    "claudedocs/handoff-kafka-cdc-exposure.md": "datapacket-talos",
    "claudedocs/handoff-linstor-commitment-visibility.md": "datapacket-talos",
    "claudedocs/handoff-meilisearch-relocation.md": "datapacket-talos",
    "claudedocs/handoff-minio-chat-storage.md": "datapacket-talos",
    "claudedocs/handoff-model-benchmarking-mobile-chrome.md": "datapacket-talos",
    "claudedocs/handoff-mongo-ha.md": "datapacket-talos",
    "claudedocs/handoff-og-500s-and-redis-cow-headroom.md": "datapacket-talos",
    "claudedocs/handoff-playable-collections-feedback-round-3.md": "datapacket-talos",
    "claudedocs/handoff-playable-collections-feedback-round-4.md": "datapacket-talos",
    "claudedocs/handoff-r2-b2-tiering-thrash.md": "datapacket-talos",
    "claudedocs/handoff-skill-prune-campaign.md": "datapacket-talos",
    "claudedocs/handoff-ssr-cpu-regression.md": "datapacket-talos",
    "claudedocs/handoff-staging-preview-next-orch.md": "datapacket-talos",
    "claudedocs/handoff-verify-b2-grain-and-ci-consolidation-2026-08-12.md": "datapacket-talos",
    "claudedocs/handoff-wedge-wave-per-node-and-inverted-hypothesis-2026-08-14.md": "datapacket-talos",
}


def tightest_allowance(size: int, step: int = GRANDFATHER_STEP) -> int:
    """The smallest multiple of `step` that fits `size`. PURE.

    The one place the quantisation is expressed, so the ledger, the failure
    messages and (e)'s check cannot disagree about what a correct entry is.
    """
    return max(step, math.ceil(size / step) * step)
