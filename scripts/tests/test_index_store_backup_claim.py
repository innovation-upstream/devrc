"""The index store's docs must agree with its nix wiring about whether a backup exists.

WHY THIS EXISTS
---------------
`~/.claude/analyze-service-index/` genuinely had no backup until 2026-08-21, and
fifteen places across docs, module docstrings and test docstrings said so. The
backup landed; **not one of those sentences was updated**, so for nine days the
repo asserted, in a 🔴 block, that the store was irreplaceable with no
off-machine copy.

🔴 THAT IS NOT A COSMETIC STALENESS. It is a "this is impossible" claim sitting
in a RECOVERY path. The failure it produces is silent and total: an agent facing
a destroyed store reads "irreplaceable, no off-machine backup", concludes
recovery is not available, and never looks for `restore-verify.py` or the age-
encrypted bundles in MinIO that would have restored it. A stale comment that
makes a *capability* invisible costs more than one that makes a hazard invisible,
because nobody goes looking for a second opinion about an impossibility.

WHAT THIS PINS, AND WHY IT IS A RELATIONSHIP RATHER THAN A SPELLING
------------------------------------------------------------------
A test that banned the phrase "no off-machine backup" would be the "spelled
rather than structural" guard `claude/RULES.md` warns about — walkable by any
reword, and it would fire on the ⚠ retraction sentences that legitimately QUOTE
the old wording. So this pins the two facts against each other:

    A. does `nix/home.nix` actually wire a backup?   (unit + timer + ExecStart
       naming a `backup.py` that exists on disk)
    B. does the canonical Store-safety section CLAIM one, concretely enough to
       act on? (names the unit, the object store, the bucket, and the tool that
       reads a bundle BACK)

and asserts `A == B`. Delete the unit and the doc claim goes red — pointing at
the sentence to restore. Rename the unit or the bucket and it goes red, because
the doc names them. Write a doc claim with no wiring behind it and it goes red.

🔴 WHAT THIS DOES **NOT** DO, STATED SO NOBODY READS COVERAGE INTO IT. It never
opens the live store, reaches MinIO, or runs systemd — devrc is PUBLIC and the
store is client-confidential. So it cannot tell you the backup *ran*, that a
bundle is *restorable*, or that the unit is enabled on any host. It pins that
the REPO's claim and the REPO's wiring agree. Whether the timer fires is
`systemctl --user list-timers 'analyze-service-index*'`, by hand, on each host.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

HOME_NIX = REPO / "nix" / "home.nix"
BACKUP_PY = REPO / "scripts" / "analyze-service-index" / "backup.py"
STORE_SAFETY_DOC = (
    REPO / "claude" / "skills" / "analyze-service" / "reference" / "index-store.md"
)

# The four things the doc must NAME for a reader to act on the claim. A vague
# "it is backed up" is not enough: the point of the sentence is to send someone
# who has just lost the store to the thing that reads a bundle back.
REQUIRED_IN_CLAIM = (
    "analyze-service-index-backup",          # the unit to check / trigger
    "minio",                                 # where the bundles live
    "analyze-service-index-backups",         # the bucket
    "restore-verify.py",                     # the tool that reads one BACK
)


def _normalise(text: str) -> str:
    """Collapse whitespace and markdown noise so a rewrap cannot walk this."""
    return re.sub(r"[\s`*_]+", " ", text).lower()


def _store_safety_section() -> str:
    """The '🔴 **Store safety.**' block, up to the next markdown heading.

    Sliced rather than whole-file so a mention of the backup ANYWHERE else in
    the doc cannot satisfy the claim — the sentence has to be where a reader
    lands when they are told to read Store safety.
    """
    body = STORE_SAFETY_DOC.read_text(encoding="utf-8")
    start = body.find("**Store safety.**")
    assert start != -1, (
        f"{STORE_SAFETY_DOC} no longer contains a '**Store safety.**' heading. "
        "That section is the canonical statement every other site points at; "
        "if it was renamed, update this test to the new anchor."
    )
    end = body.find("\n## ", start)
    return body[start:] if end == -1 else body[start:end]


def _nix_wires_a_backup() -> bool:
    nix = HOME_NIX.read_text(encoding="utf-8")
    return (
        "systemd.user.services.analyze-service-index-backup" in nix
        and "systemd.user.timers.analyze-service-index-backup" in nix
        and "analyze-service-index/backup.py" in nix
        and BACKUP_PY.is_file()
    )


def _doc_claims_a_backup(section: str) -> bool:
    norm = _normalise(section)
    return all(token.lower() in norm for token in REQUIRED_IN_CLAIM)


def test_the_backup_claim_matches_the_nix_wiring() -> None:
    wired = _nix_wires_a_backup()
    claimed = _doc_claims_a_backup(_store_safety_section())

    if wired and not claimed:
        raise AssertionError(
            "nix/home.nix wires analyze-service-index-backup (unit + timer + "
            f"{BACKUP_PY.relative_to(REPO)}), but the 'Store safety' section of "
            f"{STORE_SAFETY_DOC.relative_to(REPO)} does not say so in actionable "
            f"terms. It must name all of: {', '.join(REQUIRED_IN_CLAIM)}.\n"
            "This is the exact drift the file docstring describes: an agent that "
            "has just lost the store reads that section and needs to be told a "
            "restore path EXISTS and what reads a bundle back."
        )
    if claimed and not wired:
        raise AssertionError(
            f"{STORE_SAFETY_DOC.relative_to(REPO)} claims the store is backed up "
            "off-machine, but nix/home.nix no longer wires it (or backup.py is "
            "gone). Either restore the wiring, or rewrite that section to say the "
            "store has NO off-machine backup — a promised backup that does not "
            "run is worse than an admitted absence."
        )


def test_the_claim_detector_can_fail_and_can_fire() -> None:
    """Negative + positive control on `_doc_claims_a_backup` itself.

    A checker that answers True unconditionally would make the test above green
    forever, which is how a claim-vs-reality gate becomes decoration. So: feed it
    prose that must NOT satisfy the claim, and prose that must.
    """
    # NEGATIVE CONTROL — the pre-2026-08-21 wording, which must not pass.
    stale = (
        "**Store safety.** The content is **curated, irreplaceable, not "
        "re-derivable by re-running recon**, with no off-machine backup."
    )
    assert not _doc_claims_a_backup(stale), (
        "the detector accepted the stale 'no off-machine backup' paragraph as a "
        "backup claim — it is testing nothing"
    )

    # A vague claim must not pass either: naming the fact without naming the
    # restore path is what leaves a reader unable to act on it.
    vague = "**Store safety.** The store is backed up off-machine every day."
    assert not _doc_claims_a_backup(vague), (
        "the detector accepted a claim that names no unit, bucket or restore "
        "tool — a reader who lost the store still cannot act on it"
    )

    # POSITIVE CONTROL — a minimal claim carrying all four tokens, rewrapped and
    # re-cased, so the detector is shown to survive reformatting.
    good = (
        "**Store safety.**\nDaily bundles from\n`ANALYZE-SERVICE-INDEX-BACKUP"
        ".service`\ngo age-encrypted to the homelab MinIO tenant, bucket\n"
        "`analyze-service-index-backups`; read one back with\n`RESTORE-VERIFY.PY`."
    )
    assert _doc_claims_a_backup(good), (
        "the detector could not see a well-formed claim — it would report the "
        "live doc as missing one no matter what it said"
    )


def test_the_section_slice_is_bounded_by_the_next_heading() -> None:
    """The claim must live IN Store safety, not merely somewhere in the file.

    Without this, a passing mention in the schema section below would satisfy
    the gate while the section a reader is sent to still said "no backup".
    """
    section = _store_safety_section()
    assert section.startswith("**Store safety.**"), section[:80]
    assert "\n## " not in section, (
        "the Store-safety slice ran past a markdown heading — it is no longer a "
        "section slice, so a mention anywhere later in the file would satisfy it"
    )
    assert "## File schema" not in section
