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

import hashlib
import math
import typing

_V = typing.TypeVar("_V")

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

# 🔴 A FOREIGN ENTRY IS KEYED BY A DIGEST OF ITS PATH, BECAUSE THIS REPOSITORY IS
# PUBLIC. `devrc` is the only repo whose documents this gate can read, and a
# plaintext key here publishes another repo's internal topic list, IN BULK and in
# one sorted block, to anyone reading the tree or any index built over it. The
# remedy is uniform and needs no per-repo judgement: a key naming a document in
# THIS repo stays readable, a key naming a document in ANY other repo is a digest.
#
# ⚠ THE LEDGER IS NOT THE ONLY PLACE IN THE TREE THAT NAMES A FOREIGN DOCUMENT,
# AND AN EARLIER DRAFT OF THIS COMMENT SAID IT WAS. MEASURED at the re-key
# commit, scanning all 1,545 tracked files for each of the 71 slugs this ledger
# used to carry in plaintext: 9 of them still appear, in 6 tracked files, across
# two other repositories. All 9 pre-date this branch — verified at the
# merge-base, where the same 6 files already carried them — so they are a
# RESIDUAL and not a regression, and scrubbing them is a separate disclosure
# decision nobody has taken. What is true of the ledger is narrower and is what
# the re-key is for: it is the only place that named them IN BULK, enumerated and
# machine-readable. Do not read the digests as a leak scan; see the scope
# disclaimer on `test_every_ledger_KEY_is_a_devrc_path_or_a_WELL_FORMED_digest`.
#
# 🔴 IT IS NOT A SECRET, AND SAYING SO IS PART OF THE DESIGN RATHER THAN A
# CAVEAT ON IT. There is no salt and no key: `digest_key` is a pure, documented
# function of the path, so anyone who GUESSES a path can confirm it in one line.
# What it buys is exactly two things — the names are not READABLE and not
# INDEXABLE in a public tree — and nothing else. A comment here claiming
# confidentiality would be precisely the kind of false claim the rest of this
# module's comments exist to correct.
FOREIGN_KEY_PREFIX = "foreign:"

# 🔴 HOW LONG THE DIGEST IS, AS ARITHMETIC RATHER THAN TASTE. The only property
# the length has to buy is that two DIFFERENT documents never resolve to one key,
# and the expected number of collisions over n keys is `n(n-1)/2 / 16**N`. At
# N=16 (64 bits) that is 1.4e-16 over today's 71 foreign entries and 2.7e-14 even
# over a thousand — so the collision guard in `test_handoff_doc_size.py` is what
# would catch one and will never have to. Shorter is measurably worse (N=8 gives
# 1.2e-4 over a thousand, which is a real number, not a rounding of zero);
# longer buys nothing, because for an unsalted digest of a guessable slug LENGTH
# IS NOT SECRECY and the paragraph above is the honest statement of what it is.
FOREIGN_KEY_HEX = 16


def digest_key(relpath: str) -> str:
    """The ledger key for a document in ANOTHER repository. PURE.

    `relpath` is spelled exactly as a devrc key would be — repo-relative, POSIX
    separators, e.g. `claudedocs/handoff-<topic>.md`. UTF-8 is named rather than
    inherited so the function cannot disagree with itself across environments.

    🔴 NOT A SECRET. See `FOREIGN_KEY_PREFIX`'s comment, which states the whole
    of what this buys; do not add a salt and do not describe this as hiding
    anything from someone who guesses the path.
    """
    return FOREIGN_KEY_PREFIX + hashlib.sha256(
        relpath.encode("utf-8")).hexdigest()[:FOREIGN_KEY_HEX]


def is_foreign_key(key: str) -> bool:
    """Is this ledger key a digest rather than a readable devrc path?

    🔴 A WELL-FORMEDNESS TEST, NOT A `startswith`. A key carrying the prefix and
    something that is not a `FOREIGN_KEY_HEX`-character lowercase hex digest is a
    malformed ledger — it resolves for no document while reading as an allowance —
    and `test_every_ledger_KEY_is_a_devrc_path_or_a_WELL_FORMED_digest` is what
    fails on it. A plaintext path answers False, which is what every caller
    branches on.
    """
    if not key.startswith(FOREIGN_KEY_PREFIX):
        return False
    rest = key[len(FOREIGN_KEY_PREFIX):]
    return (len(rest) == FOREIGN_KEY_HEX
            and all(c in "0123456789abcdef" for c in rest))


def lookup(relpath: str, ledger: dict[str, _V]) -> _V | None:
    """🔴 THE ONE RESOLVER: the plaintext key first, then the digest.

    Every reader of `GRANDFATHERED` or `LIVES_ELSEWHERE` goes through this, and
    that is `claude/RULES.md`'s one-rule-one-place rather than tidiness. A second,
    open-coded `ledger.get(relpath)` anywhere would resolve devrc's 11 documents
    and answer `None` for all 71 foreign ones — an allowance that reads as absent,
    i.e. the bare ceiling, i.e. rule (p) refusing the next update to a document
    the operator grandfathered on purpose. The failure would be silent in devrc,
    which is the only repo any gate here can see.

    PLAINTEXT FIRST, for two reasons: a devrc entry cannot then be shadowed by a
    digest collision, and every synthetic ledger the controls in
    `test_handoff_doc_size.py` drive this with keeps working unchanged.
    """
    hit = ledger.get(relpath)
    if hit is None:
        hit = ledger.get(digest_key(relpath))
    return hit


# 🔴 THE LEDGER. Explicit and ENUMERATED, never a pattern — an unlisted document
# over the ceiling is a failure by default.
#
# key -> allowance in bytes, which must be `ceil(measured / GRANDFATHER_STEP) *
# GRANDFATHER_STEP`. Do not hand-compute it: every failure message prints the
# exact line to paste. The key is a repo-relative PATH for a devrc document and
# `digest_key(path)` for a document in any other repo; `lookup` resolves both and
# no call site chooses between them.
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
# 🔴 THE KEY CARRIES NO REPO — NEITHER SPELLING OF IT — AND SINCE #1871 THIS
# LEDGER COVERS DOCUMENTS IN OTHER REPOSITORIES. `handoff_doc.py` runs against a
# `--repo` the caller names, so rule (p) reads this dict for a doc in ANY
# checkout, and the lookup is by bare repo-relative path in both spellings: the
# path itself for a devrc doc, `digest_key(path)` for a foreign one. Digesting it
# changes nothing about this — a digest of a path is still a function of the path
# alone. Two consequences the next person adding an entry has to hold at once:
#
#   * 🔴 ONE ENTRY GOVERNS EVERY REPO THAT HAS A DOC OF THAT NAME. There is no
#     collision today, measured across ALL FIVE repos this tool can be pointed
#     at — the four `handoff_index.REPO_ENV_HANDLES` resolve, plus cairn, which
#     has NO handle and was therefore enumerated by hand: 572 handoff docs when
#     the 54 entries below were added, 0 filenames appearing in two repos, and 0
#     of those 54 paths colliding with the 28 that were already here. ⚠ THE
#     CORPUS TOTAL IS A MOVING NUMBER — it has already grown since, so treat it as
#     the scale of that measurement and not as today's count. ⚠ AND THE PREVIOUS
#     ROUND MEASURED THIS OVER THREE REPOS — devrc, cairn, homelab-talos — off
#     guessed paths rather than off the handles, so it never resolved TWO of the
#     four handles: it missed $DATAPACKET's 357 documents, all 54 breaches among
#     them, and $CIVITAI's handful, which hold no breach. "357" is that one
#     repo's corpus, NOT the number of documents the round was blind to — the two
#     unseen handles together held one more than that at the time and more than
#     that now. Enumerate from `REPO_ENV_HANDLES`, never from a path anyone
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
    # 🔴 EVERY KEY BELOW IS A DIGEST AND NOT A PATH, BECAUSE THIS REPOSITORY IS
    # PUBLIC. `digest_key` owns the scheme, the arithmetic behind its length, and
    # the plain statement that it is NOT a secret — read it there rather than
    # re-deriving either half here.
    #
    # 🔴 READ THE SCALE BEFORE READING THE RULING AS CHEAP, BECAUSE THE SCALE IS
    # WHAT IT COSTS. This ledger goes from 28 entries to 82 and `LIVES_ELSEWHERE`
    # from 17 to 71. 71 of 82 entries — the large majority — are therefore
    # foreign, and for every one of them the anti-decay checks (b), (c), (d) and
    # (e)'s tightness half are all OFF, for the reasons measured at the top of
    # this comment. That is not a caveat on the decision; it is the decision. A
    # ledger designed as a ratchet is, after this change, mostly a list of
    # allowances that no gate will ever tighten. It was taken knowingly — the
    # alternative was refusing the next update to 71 live documents in repos
    # devrc's gate has never been able to read — and it is written here at full
    # size so nobody later reports it as a discovery.
    #
    # 🔴 EVERY KEY BELOW IS ALSO IN `LIVES_ELSEWHERE`, AND THAT IS NOW THE ONLY
    # PLACE THE REPO IS WRITTEN DOWN — the key no longer says, and neither does
    # the order.
    #
    # 🔴 TO ADD AN ENTRY FOR A DOCUMENT IN ANOTHER REPO, COMPUTE THE KEY. Do not
    # write the path, in either dict — and do not hand-derive the digest either,
    # because the prefix and the length are constants this module owns:
    #
    #   python3 -c 'import sys; sys.path.insert(0, "scripts/lib"); \
    #     import handoff_budget as b; print(b.digest_key("claudedocs/handoff-<topic>.md"))'
    #
    # The population that produced this block was measured 2026-09-25 with
    # `handoff_index.handoff_paths_on_disk`, over the
    # population enumerated from `handoff_index.REPO_ENV_HANDLES` ITSELF plus
    # cairn, which has no handle:
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
    #
    # 🔴 THE TIGHTEST GAP IN THIS BLOCK IS 571 B — 179,653 B measured against an
    # allowance of 180,224 B — and the entry is deliberately NOT named, because
    # naming it is the disclosure the digest exists to prevent. Every one of the
    # 71 is STRICTLY above its measured size, which is the ruling: grandfather at
    # the quantum so nothing is red on its own next byte. A doc whose size were an
    # exact multiple of the step would break that — `tightest_allowance` returns
    # the size itself there — and none of these is.
    #
    # ⚠ SORTED BY KEY, WHICH IS TO SAY SORTED BY DIGEST AND THEREFORE BY NOTHING
    # A READER CAN USE. That is deliberate in the same direction as the digest
    # itself: a block sorted by plaintext path publishes the alphabetical order of
    # the names it is hiding, which narrows a guess. Do not scan this block to
    # find an entry — digest the path.
    "foreign:00a6bb3a110d6f24": 147_456,
    "foreign:06795c4d08f2b8ed": 98_304,
    "foreign:06ee3daeedea79e7": 196_608,
    "foreign:078d96426d88ba97": 131_072,
    "foreign:094d6215d64a2c69": 180_224,
    "foreign:0b2a8bcb8a0a79a2": 81_920,
    "foreign:0c760cd7ecb0899c": 147_456,
    "foreign:12e00341f7b3af1b": 114_688,
    "foreign:1bc5e278c87f7197": 81_920,
    "foreign:1bc92a3244e7ff1b": 81_920,
    "foreign:20681fde4c8918ce": 81_920,
    "foreign:20dc7371927a25d7": 81_920,
    "foreign:24b84976e42ca765": 81_920,
    "foreign:3659ea15f321c01e": 131_072,
    "foreign:3adf06b9266a73b6": 81_920,
    "foreign:44f36be723873626": 131_072,
    "foreign:46fdcd4b092d473f": 163_840,
    "foreign:51988807443f550b": 98_304,
    "foreign:51bcb04416a3d48c": 131_072,
    "foreign:5599546e551fc247": 147_456,
    "foreign:56c7ad75c8085995": 98_304,
    "foreign:57d37b95a40228b1": 98_304,
    "foreign:584885c94a5e64d7": 98_304,
    "foreign:589c8685b63d336a": 163_840,
    "foreign:5d729f9457db1be9": 81_920,
    "foreign:634bb85403a0b6a4": 98_304,
    "foreign:6551bbcce46e2da5": 98_304,
    "foreign:6a7e4c0956982593": 98_304,
    "foreign:6c84b350020fd167": 180_224,
    "foreign:6df616eb20582ce9": 163_840,
    "foreign:6e5abc9df8910d1f": 81_920,
    "foreign:701522030b30a553": 114_688,
    "foreign:77a95bd83499edbe": 163_840,
    "foreign:85b5773be4e98918": 147_456,
    "foreign:8a5c456391d51c93": 425_984,
    "foreign:8c01d0730b50e086": 147_456,
    "foreign:8e8639de61ccca87": 294_912,
    "foreign:94bf72302023e839": 163_840,
    "foreign:9906052c59f2921d": 98_304,
    "foreign:9e18c0a48af69ec9": 98_304,
    "foreign:9f906590bb6b02dd": 147_456,
    "foreign:a21c154b80a0e824": 114_688,
    "foreign:a2cff16912388ad3": 147_456,
    "foreign:a54d698a4a360f96": 98_304,
    "foreign:aab3a955ea0f9dc2": 81_920,
    "foreign:ac2a0093518c7779": 81_920,
    "foreign:ad62fed4ccfa9228": 131_072,
    "foreign:ae3dfc66a8ea0ea4": 114_688,
    "foreign:b336690a745e8339": 81_920,
    "foreign:baebd7e094de26f4": 98_304,
    "foreign:bc3c981116058e7c": 180_224,
    "foreign:bdd015812770e390": 98_304,
    "foreign:c0852103a8ce6314": 180_224,
    "foreign:c692f26aa64cb268": 196_608,
    "foreign:c731a9df4b10b4af": 147_456,
    "foreign:c777a90f7da2224a": 81_920,
    "foreign:ce16bba9538fac32": 81_920,
    "foreign:d35f716fd62ff827": 327_680,
    "foreign:d8cc4a483c72f4a4": 131_072,
    "foreign:db9105fcdb125a02": 98_304,
    "foreign:ddf4d01af2679aab": 81_920,
    "foreign:dfb4571624cc46e0": 98_304,
    "foreign:e1187ee96f4f7c4a": 163_840,
    "foreign:e118a726cef81700": 131_072,
    "foreign:e27faebfc6a3469f": 147_456,
    "foreign:e3ee69dd4457de02": 114_688,
    "foreign:e47637b067a09857": 360_448,
    "foreign:eb27b1240d7c541a": 163_840,
    "foreign:fa4c2a2b41797d4f": 81_920,
    "foreign:fd753d2573b8f4a4": 114_688,
    "foreign:fdd309570487cf03": 163_840,
}

# 🔴 THE LEDGER KEYS WHOSE DOCUMENT IS NOT IN devrc, and the repo that holds
# each. A SECOND STRUCTURE RATHER THAN A RICHER VALUE ON PURPOSE:
# `GRANDFATHERED` stays `dict[str, int]` because `handoff_doc.budget_position`
# reads it for ONE number through `lookup`, and three mutation rows in
# `scripts/tests/mutation_battery_handoff_archive_and_cap.py` (C4, C5, C11) anchor
# on whole ledger lines — widening the value would move all four for a field only
# one reader wants.
#
# 🔴 AND SINCE THE KEYS HERE ARE DIGESTS, THIS DICT IS THE ONLY PLACE THE REPO IS
# WRITTEN DOWN AT ALL. That is a reason to keep the second structure rather than
# an argument against it: the alternative — a repo label welded onto the
# `GRANDFATHERED` value — would put a foreign document's repo beside its
# allowance and still tell you nothing about WHICH document, while costing the
# four movements above.
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
#     that every key here IS in `GRANDFATHERED` and that NO devrc document
#     RESOLVES to one of them — so the collision hazard the ledger's header names
#     fails LOUDLY the day devrc grows a doc of one of these names, rather than
#     silently handing two documents one allowance.
#     🔴 THAT CHECK RESOLVES THROUGH `lookup` RATHER THAN INTERSECTING RAW KEYS,
#     AND THE DIGEST IS EXACTLY WHY. Every key here is a digest and every devrc
#     path is plaintext, so a raw-key intersection is EMPTY BY CONSTRUCTION — it
#     would pass forever while measuring nothing, which is the failure mode the
#     re-keying was most likely to introduce. Its own positive control watches the
#     digest half of that resolution move.
#   * `test_every_ledger_KEY_is_a_devrc_path_or_a_WELL_FORMED_digest` closes the
#     loop the other way, and the two together are what make the re-keying
#     permanent rather than a one-off scrub: a foreign entry re-added in PLAINTEXT
#     is either left undeclared — and (d) then reports it stale, because devrc's
#     tree does not hold it — or declared here, where that test refuses it for
#     being a readable path. There is no third option.
LIVES_ELSEWHERE: dict[str, str] = {
    # 🔴 THE KEY IS A DIGEST; THE VALUE IS A REPO LABEL AND STAYS READABLE, AND
    # THAT ASYMMETRY IS MEASURED RATHER THAN JUDGED. Every label below already
    # appears in devrc's own tree in well over a hundred tracked files each, so
    # digesting a label would hide nothing that is not already published and
    # would cost the only thing the value is for: telling a reader which checkout
    # to go and look in. RE-DERIVE IT RATHER THAN TRUSTING A NUMBER HERE — a
    # previous draft wrote the three counts out and attributed them to one commit,
    # where ONE of the three was already off by one; at the commit before it the
    # other TWO were off by one instead, so the three were never true together at
    # any single ref. The cause is this comment: the block that quotes the counts
    # is itself one of the files being counted:
    #
    #     for l in cairn homelab-talos datapacket-talos; do
    #       printf '%s %s\n' "$l" "$(git grep -l -F -- "$l" | wc -l)"; done
    #
    # The value is a LABEL and never a path — that half is unchanged, and its
    # reason is `handoff_index.REPO_ENV_HANDLES`' own: this repo is PUBLIC.
    "foreign:00a6bb3a110d6f24": "datapacket-talos",
    "foreign:06795c4d08f2b8ed": "homelab-talos",
    "foreign:06ee3daeedea79e7": "datapacket-talos",
    "foreign:078d96426d88ba97": "datapacket-talos",
    "foreign:094d6215d64a2c69": "datapacket-talos",
    "foreign:0b2a8bcb8a0a79a2": "datapacket-talos",
    "foreign:0c760cd7ecb0899c": "datapacket-talos",
    "foreign:12e00341f7b3af1b": "datapacket-talos",
    "foreign:1bc5e278c87f7197": "datapacket-talos",
    "foreign:1bc92a3244e7ff1b": "homelab-talos",
    "foreign:20681fde4c8918ce": "datapacket-talos",
    "foreign:20dc7371927a25d7": "homelab-talos",
    "foreign:24b84976e42ca765": "datapacket-talos",
    "foreign:3659ea15f321c01e": "datapacket-talos",
    "foreign:3adf06b9266a73b6": "datapacket-talos",
    "foreign:44f36be723873626": "datapacket-talos",
    "foreign:46fdcd4b092d473f": "homelab-talos",
    "foreign:51988807443f550b": "homelab-talos",
    "foreign:51bcb04416a3d48c": "datapacket-talos",
    "foreign:5599546e551fc247": "datapacket-talos",
    "foreign:56c7ad75c8085995": "datapacket-talos",
    "foreign:57d37b95a40228b1": "datapacket-talos",
    "foreign:584885c94a5e64d7": "datapacket-talos",
    "foreign:589c8685b63d336a": "datapacket-talos",
    "foreign:5d729f9457db1be9": "datapacket-talos",
    "foreign:634bb85403a0b6a4": "datapacket-talos",
    "foreign:6551bbcce46e2da5": "homelab-talos",
    "foreign:6a7e4c0956982593": "datapacket-talos",
    "foreign:6c84b350020fd167": "datapacket-talos",
    "foreign:6df616eb20582ce9": "homelab-talos",
    "foreign:6e5abc9df8910d1f": "datapacket-talos",
    "foreign:701522030b30a553": "datapacket-talos",
    "foreign:77a95bd83499edbe": "datapacket-talos",
    "foreign:85b5773be4e98918": "homelab-talos",
    "foreign:8a5c456391d51c93": "datapacket-talos",
    "foreign:8c01d0730b50e086": "datapacket-talos",
    "foreign:8e8639de61ccca87": "homelab-talos",
    "foreign:94bf72302023e839": "datapacket-talos",
    "foreign:9906052c59f2921d": "homelab-talos",
    "foreign:9e18c0a48af69ec9": "datapacket-talos",
    "foreign:9f906590bb6b02dd": "datapacket-talos",
    "foreign:a21c154b80a0e824": "homelab-talos",
    "foreign:a2cff16912388ad3": "cairn",
    "foreign:a54d698a4a360f96": "datapacket-talos",
    "foreign:aab3a955ea0f9dc2": "datapacket-talos",
    "foreign:ac2a0093518c7779": "datapacket-talos",
    "foreign:ad62fed4ccfa9228": "datapacket-talos",
    "foreign:ae3dfc66a8ea0ea4": "datapacket-talos",
    "foreign:b336690a745e8339": "datapacket-talos",
    "foreign:baebd7e094de26f4": "datapacket-talos",
    "foreign:bc3c981116058e7c": "homelab-talos",
    "foreign:bdd015812770e390": "datapacket-talos",
    "foreign:c0852103a8ce6314": "datapacket-talos",
    "foreign:c692f26aa64cb268": "datapacket-talos",
    "foreign:c731a9df4b10b4af": "datapacket-talos",
    "foreign:c777a90f7da2224a": "datapacket-talos",
    "foreign:ce16bba9538fac32": "datapacket-talos",
    "foreign:d35f716fd62ff827": "homelab-talos",
    "foreign:d8cc4a483c72f4a4": "datapacket-talos",
    "foreign:db9105fcdb125a02": "cairn",
    "foreign:ddf4d01af2679aab": "datapacket-talos",
    "foreign:dfb4571624cc46e0": "homelab-talos",
    "foreign:e1187ee96f4f7c4a": "datapacket-talos",
    "foreign:e118a726cef81700": "datapacket-talos",
    "foreign:e27faebfc6a3469f": "datapacket-talos",
    "foreign:e3ee69dd4457de02": "datapacket-talos",
    "foreign:e47637b067a09857": "homelab-talos",
    "foreign:eb27b1240d7c541a": "datapacket-talos",
    "foreign:fa4c2a2b41797d4f": "datapacket-talos",
    "foreign:fd753d2573b8f4a4": "datapacket-talos",
    "foreign:fdd309570487cf03": "datapacket-talos",
}


def tightest_allowance(size: int, step: int = GRANDFATHER_STEP) -> int:
    """The smallest multiple of `step` that fits `size`. PURE.

    The one place the quantisation is expressed, so the ledger, the failure
    messages and (e)'s check cannot disagree about what a correct entry is.
    """
    return max(step, math.ceil(size / step) * step)
