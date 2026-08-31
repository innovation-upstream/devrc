"""The index store's docs must agree with its nix wiring about whether a backup exists.

WHY THIS EXISTS
---------------
`~/.claude/analyze-service-index/` genuinely had no backup until 2026-08-21.
The backup landed; the sentences saying it had none were not updated, so for
nine days the repo asserted, in 🔴 blocks, that the store was irreplaceable with
no off-machine copy.

🔴 THAT IS NOT COSMETIC STALENESS. It is a "this is impossible" claim in a
RECOVERY path. The failure is silent and total: an agent facing a destroyed
store reads "irreplaceable, no off-machine backup", concludes recovery is
unavailable, and never looks for `restore-verify.py` or the age-encrypted
bundles that would have restored it. A stale comment that hides a CAPABILITY
costs more than one that hides a hazard, because nobody seeks a second opinion
about an impossibility.

WHAT THIS PINS
--------------
Two facts against each other:

    A. does `nix/home.nix` actually wire a backup? — a LIVE declaration of the
       service and the timer (comments stripped), the timer enabled via
       `WantedBy = [ "timers.target" ]`, an ExecStart naming a `backup.py` that
       exists on disk, and neither unit gated behind `lib.mkIf`.
    B. does the canonical Store-safety section state the matching claim?

and asserts `A == B`, with a canonical paragraph for EACH state.

🔴 WHY THE CLAIM IS PINNED AS A WHOLE NORMALISED STRING, NOT AS TOKENS. The
first version of this guard required four substrings to be present. An audit
walked it in one pass: a section reading "IRREPLACEABLE — there is NO
off-machine backup and no way to recover it. The `analyze-service-index-backup`
unit was REMOVED, the bucket `analyze-service-index-backups` was deleted, and
`restore-verify.py` no longer works" contains every required token and PASSED —
the guard certified agreement while the doc said the exact opposite of the
truth. Token presence cannot see POLARITY. `claude/RULES.md` gives the rule for
this case directly: when the artifact under test IS prose, a guard on WORDS is
walkable by REWORDING, so pin the WHOLE normalised string and pay the cosmetic
reword cost for a machine-readable claim.

🔴 AND WHY THERE IS AN `ABSENT` PARAGRAPH TOO. The same audit found the reverse
state unsatisfiable: with the wiring removed, the old guard failed and told the
maintainer to "rewrite that section to say the store has NO off-machine backup"
— which was exactly what they had just done, because any such rewrite that
NAMED the retired unit and bucket still tripped the token check. A gate whose
instruction cannot be followed is a permanently-red gate, and `claude/RULES.md`
says a permanently-red gate is worse than no gate. So decommissioning has a
canonical paragraph the failure message prints in full, ready to paste.

🔴 WHAT THIS DOES **NOT** DO, STATED SO NOBODY READS COVERAGE INTO IT. It never
opens the live store, reaches MinIO, or runs systemd — devrc is PUBLIC and the
store is client-confidential. So it cannot tell you the backup RAN, that a
bundle is RESTORABLE, or that the timer is enabled on any particular host. It
pins that the REPO's claim and the REPO's wiring agree. Per-host liveness is
`systemctl --user list-timers 'analyze-service-index*'`, by hand, on each host.

⚠ NOR IS IT A COMPLETENESS CHECK ON THE PROSE. It reads ONE section of ONE
file. Other files carry their own statements about this store, and this guard
is structurally blind to every one of them — it cannot tell you the corpus
agrees, only that the canonical section does.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from testlib.nix_units import (  # noqa: E402
    declares,
    is_conditional,
    unit_source,
)

HOME_NIX = REPO / "nix" / "home.nix"
BACKUP_PY = REPO / "scripts" / "analyze-service-index" / "backup.py"
STORE_SAFETY_DOC = (
    REPO / "claude" / "skills" / "analyze-service" / "reference" / "index-store.md"
)

BACKUP_SERVICE = "systemd.user.services.analyze-service-index-backup"
BACKUP_TIMER = "systemd.user.timers.analyze-service-index-backup"

# 🔴 THE CANONICAL CLAIM FOR EACH STATE, pinned whole. Normalisation collapses
# whitespace and drops markdown emphasis, so a rewrap or a bold change is free;
# changing a WORD is not, and that is the point.
CLAIM_PRESENT = (
    "daily, off-machine — analyze-service-index-backup.service bundles every scope, "
    "age-encrypted, to the homelab MinIO minio-archive tenant, bucket "
    "analyze-service-index-backups, key <host>-<machine-id>/<scope>/<ts>.bundle.age. "
    "Read them back with scripts/analyze-service-index/restore-verify.py; "
    "escrow-verify.py checks the key material."
)

CLAIM_ABSENT = (
    "There is NO off-machine backup. The analyze-service-index-backup unit has been "
    "retired, so nothing leaves this machine and a destroyed scope is recoverable only "
    "from its own hourly commits — and not at all if the disk is gone."
)


def _normalise(text: str) -> str:
    """Collapse whitespace and markdown emphasis; keep the words."""
    return re.sub(r"\s+", " ", re.sub(r"[`*_]", "", text)).strip().lower()


def _store_safety_section() -> str:
    """The '🔴 **Store safety.**' block, up to the next markdown heading.

    Sliced rather than whole-file so a mention of the backup ANYWHERE else in
    the doc cannot satisfy the claim — the sentence has to be where a reader
    lands when they are told to read Store safety.
    """
    body = STORE_SAFETY_DOC.read_text(encoding="utf-8")
    anchor = "**Store safety.**"
    count = body.count(anchor)
    assert count == 1, (
        f"{STORE_SAFETY_DOC} contains {count} '{anchor}' anchors; this reader "
        "assumes exactly one. If the section was renamed or duplicated, fix the "
        "doc or update this anchor — do not let it slice an arbitrary one."
    )
    start = body.find(anchor)
    end = body.find("\n## ", start)
    return body[start:] if end == -1 else body[start:end]


def _nix_wires_a_backup(src: str | None = None, *, script_exists: bool = True) -> bool:
    """A LIVE, ENABLED, UNCONDITIONAL backup — not a mention of one.

    Every clause here is a way the backup can stop running while a naive
    substring search still matches:
      * comments that quote the attribute path of a unit being retired,
      * a timer declared but never `WantedBy` anything, so it is never enabled,
      * an ExecStart naming a script that has been deleted,
      * a `lib.mkIf` gate, which makes an unconditional doc promise false on
        every host the condition excludes.

    `src`/`script_exists` are injectable so the mutation test below can drive
    each clause. Production callers pass neither.
    """
    if src is None:
        src = HOME_NIX.read_text(encoding="utf-8")
        script_exists = BACKUP_PY.is_file()
    if not (declares(BACKUP_SERVICE, src) and declares(BACKUP_TIMER, src)):
        return False
    if is_conditional(BACKUP_SERVICE, src) or is_conditional(BACKUP_TIMER, src):
        return False
    if "timers.target" not in unit_source(BACKUP_TIMER, src):
        return False
    if "analyze-service-index/backup.py" not in unit_source(BACKUP_SERVICE, src):
        return False
    return script_exists


def _section_states(claim: str, section: str) -> bool:
    return _normalise(claim) in _normalise(section)


def test_the_backup_claim_matches_the_nix_wiring() -> None:
    section = _store_safety_section()
    wired = _nix_wires_a_backup()

    if wired and not _section_states(CLAIM_PRESENT, section):
        raise AssertionError(
            "nix/home.nix wires a live, enabled, unconditional backup (service + "
            f"timer + {BACKUP_PY.relative_to(REPO)}), but the 'Store safety' section "
            f"of {STORE_SAFETY_DOC.relative_to(REPO)} does not carry the canonical "
            "claim. An agent that has just lost the store reads that section and "
            "must be told a restore path EXISTS and what reads a bundle back.\n\n"
            "Expected this text (whitespace and emphasis are free, WORDS are not):\n\n"
            f"{CLAIM_PRESENT}\n"
        )
    if not wired and not _section_states(CLAIM_ABSENT, section):
        raise AssertionError(
            "nix/home.nix no longer wires a live, enabled, unconditional backup, but "
            f"{STORE_SAFETY_DOC.relative_to(REPO)} still claims one. A promised "
            "backup that does not run is worse than an admitted absence.\n\n"
            "Either restore the wiring, or replace the claim with this paragraph:\n\n"
            f"{CLAIM_ABSENT}\n"
        )


def test_the_two_canonical_claims_are_mutually_exclusive() -> None:
    """Neither paragraph may satisfy the other's check.

    Without this, a future edit that made the two texts overlap would let one
    section satisfy BOTH states, and the `A == B` assertion above would stop
    discriminating while still passing.
    """
    assert not _section_states(CLAIM_PRESENT, CLAIM_ABSENT)
    assert not _section_states(CLAIM_ABSENT, CLAIM_PRESENT)


def test_the_claim_detector_sees_polarity_not_just_tokens() -> None:
    """🔴 The regression that killed the first version of this guard.

    A token-presence detector passed this text. It names the unit, the bucket
    and the restore tool — every token the old `REQUIRED_IN_CLAIM` demanded —
    while telling the reader the exact opposite of the truth.
    """
    reversed_claim = (
        "**Store safety.** IRREPLACEABLE — there is **NO** off-machine backup and no "
        "way to recover it. The `analyze-service-index-backup` unit was **REMOVED**, "
        "the MinIO bucket `analyze-service-index-backups` was **deleted**, and "
        "`restore-verify.py` no longer works. A destructive write here is PERMANENT."
    )
    assert not _section_states(CLAIM_PRESENT, reversed_claim), (
        "the detector accepted a meaning-REVERSED section that happens to contain "
        "the right nouns — it is pinning vocabulary, not the claim"
    )

    # The stale pre-2026-08-21 wording must not pass either.
    stale = (
        "**Store safety.** The content is **curated, irreplaceable, not re-derivable "
        "by re-running recon**, with no off-machine backup."
    )
    assert not _section_states(CLAIM_PRESENT, stale)

    # A vague claim naming no restore path must not pass.
    assert not _section_states(
        CLAIM_PRESENT, "**Store safety.** The store is backed up off-machine every day."
    )

    # POSITIVE CONTROL — the canonical claim, rewrapped and re-emphasised, must
    # still be seen. Without this the detector could be answering False always.
    rewrapped = (
        "- __daily, off-machine__ —\n  `analyze-service-index-backup.service`\n  bundles "
        "every    scope, **age-encrypted**, to the homelab MinIO `minio-archive`\n"
        "  tenant, bucket `analyze-service-index-backups`, key\n"
        "  `<host>-<machine-id>/<scope>/<ts>.bundle.age`. Read them back with\n"
        "  `scripts/analyze-service-index/restore-verify.py`; `escrow-verify.py`\n"
        "  checks the key material."
    )
    assert _section_states(CLAIM_PRESENT, rewrapped), (
        "the detector could not see the canonical claim after a rewrap — it would "
        "report the live doc as missing one no matter what it said"
    )


def test_the_wiring_check_is_not_fooled_by_comments_or_a_disabled_timer() -> None:
    """🔴 The other regression that killed the first version.

    An audit renamed both attribute paths and left only `#` comment lines naming
    the originals. Live declarations: 0 and 0. The substring-based check stayed
    GREEN while the docs promised daily bundles.

    Each mutant below breaks the backup in a way a naive `substring in src`
    cannot see, and each is asserted to flip `_nix_wires_a_backup` to False —
    the whole predicate, not one clause of it, so a mutant that an earlier
    clause happens to reject cannot be mistaken for this clause working.
    """
    live = HOME_NIX.read_text(encoding="utf-8")

    # POSITIVE CONTROL. Without this every assertion below is satisfied by a
    # predicate that returns False unconditionally.
    assert _nix_wires_a_backup(live), (
        "the live home.nix must read as wired, or every mutant below passes "
        "for the wrong reason"
    )

    # 1. Declared only inside a comment — the measured walk.
    commented = live.replace(
        f"  {BACKUP_SERVICE} =", f"  # DISABLED 2026-09-01: {BACKUP_SERVICE} =", 1
    )
    assert not _nix_wires_a_backup(commented), (
        "a commented-out declaration still reads as wired — the reader is "
        "answering about the prose, not the configuration"
    )

    # 2. Timer declared but never enabled: it exists and never fires.
    #    🔴 Built by index arithmetic on the RAW source, not by replacing the
    #    comment-stripped block: the first attempt did the latter, the
    #    `str.replace` matched nothing, and the "mutant" was byte-identical to
    #    `live` — a mutant that never applied, scored as SURVIVED. The
    #    `mutated != live` assertion below is what makes that impossible.
    wanted_by = 'WantedBy = [ "timers.target" ];'
    assert wanted_by in unit_source(BACKUP_TIMER, live)
    at = live.index(f"  {BACKUP_TIMER} =")
    cut = live.index(wanted_by, at)
    unenabled = live[:cut] + live[cut + len(wanted_by):]
    assert unenabled != live, "the mutant did not apply — it proves nothing"
    assert not _nix_wires_a_backup(unenabled), (
        "a timer with no `WantedBy` reads as wired — it is declared, never "
        "enabled, and never fires"
    )

    # 3. Gated behind `lib.mkIf`: false on every host the condition excludes,
    #    while the doc promises it unconditionally.
    gated = live.replace(
        f"  {BACKUP_TIMER} = {{", f"  {BACKUP_TIMER} = lib.mkIf serverMode {{", 1
    )
    assert is_conditional(BACKUP_TIMER, gated)
    assert not is_conditional(BACKUP_TIMER, live)
    assert not _nix_wires_a_backup(gated), (
        "a `lib.mkIf` gate went unnoticed — an unconditional doc promise about "
        "a conditional unit is false on every excluded host"
    )

    # 4. ExecStart names a script that is not on disk.
    assert not _nix_wires_a_backup(live, script_exists=False), (
        "a missing backup.py reads as wired — the unit would fail on every run"
    )


def test_the_section_slice_is_bounded_by_the_next_heading() -> None:
    """The claim must live IN Store safety, not merely somewhere in the file."""
    section = _store_safety_section()
    assert section.startswith("**Store safety.**"), section[:80]
    assert "\n## " not in section, (
        "the Store-safety slice ran past a markdown heading — it is no longer a "
        "section slice, so a mention anywhere later in the file would satisfy it"
    )
    assert "## File schema" not in section
