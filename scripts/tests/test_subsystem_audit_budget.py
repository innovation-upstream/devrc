"""THE OWNER of the `/analyze-service` index-store per-entry byte budget.

This module is the single source of truth for the two numbers
`scripts/subsystem-audit.py` reports against. The script carries pinned COPIES so
it can run standalone; the pin test below fails, with the eviction playbook, the
moment the two disagree. Any other mention of these numbers (CLAUDE.md, the
`prune-index` skill body, a PR body) must cross-reference THIS module rather than
restate the literal — a second hand-maintained copy is how a budget drifts away
from the argument that justified it.

WHY A BUDGET AT ALL
-------------------
`~/.claude/analyze-service-index/` grows monotonically and nothing applies
pressure. Measured 2026-08-21T18:52Z against the live store:

    entries        77          total      359,395 B (351.0 KB)
    median entry    3,002 B    mean         4,667 B
    largest        27,770 B    p85          6,212 B

The store's measured value is RECENCY-ORDERED SELECTION — in a controlled A/B an
index-primed agent avoided a superseded fact a repo-only agent asserted
confidently — and selection is exactly the property that degrades as entries
grow. So the budget defends the thing the store is FOR, not tidiness.

🔴 THE GROWTH HAD A CAUSE, AND IT WAS A RULE CONTRADICTION.
`analyze-service/reference/write-back.md` said "Prune-on-resolve -- when a gotcha
is fixed, REMOVE the bullet", while the newer `OPEN:`/`RESOLVED <sha>:` schema in
`index-store.md` says MARK it. Both were live and both were followed, so
`RESOLVED` bullets accumulated forever. The docs are reconciled in the same
change that adds this gate; the gate is what stops the prose from being the only
defence, exactly as `test_rules_size.py` does for `claude/RULES.md`.

🔴 WHAT THIS MODULE DOES **NOT** DO, STATED SO NOBODY READS COVERAGE INTO IT.
It never opens the live store. The store is curated, CLIENT-CONFIDENTIAL and not
re-derivable by re-running recon, and devrc is PUBLIC — so no fixture here is
derived from it, every fixture is synthetic, and the corpus statistics above are
AGGREGATE INTEGERS, which is the most that may be recorded in this repo.
⚠ That sentence used to read "and has no off-machine backup" — false; daily
age-encrypted bundles go to MinIO. PUBLIC is the operative half here, and it has
not changed. That means this
module cannot gate the live store's size the way `test_rules_size.py` gates a
tracked file. What it gates instead:

  1. the two constants and their derivation (a change to either must be argued
     here, in a diff a reviewer sees);
  2. that `subsystem-audit.py`'s copies still equal them;
  3. that the numbers are LOAD-BEARING — a synthetic entry one byte over is
     reported over, one exactly at the budget is not. Without (3) the constants
     could be anything and every test here would still pass.
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parent


def _load(relpath: str, modname: str):
    """Import a hyphenated script by path — the convention in this test dir."""
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    loader.exec_module(mod)
    return mod


sa = _load("subsystem-audit.py", "subsystem_audit_undertest")


# --- the numbers ----------------------------------------------------------------

# THE HARD CAP: 12,288 B.
#
# 🔴 DERIVED, NOT PICKED. It is exactly `MAX_BYTES` from
# `scripts/browser-bridge/tests/test_skill_size.py` — the PROVEN ceiling for a
# whole SKILL.md body, demonstrated by the `browser` skill routing 11 reference
# topics and a 21-op CLI from under it. The claim the number makes is therefore
# checkable in one sentence: an index ENTRY that outweighs an entire skill body
# has stopped being a pointer sheet and become the archive it is supposed to
# point AT. `test_hard_cap_is_the_skill_body_budget` imports that module and
# asserts the equality, so this is an executable derivation rather than a prose
# claim about where a literal came from — if the skill budget ever moves, this
# goes red and someone has to decide, instead of the two silently diverging.
#
# Live entries over it on 2026-08-21: 5 of 77 (tekton-builds 27,770 B,
# storage-resolver 23,765 B, monitoring 20,905 B, subsystem-store 14,128 B,
# blocks 13,050 B). Those five hold 99,618 B — 28% of the whole store in 6% of
# its entries, which is the concentration that makes a per-entry cap the right
# instrument rather than a store-wide total.
ENTRY_HARD_BYTES = 12_288

# THE TARGET: 6,144 B.
#
# 🔴 DERIVED FROM THE CORPUS, NOT FROM A ROUND NUMBER. 64 of 77 live entries
# (83%) already sit under 6 KiB — measured p85 is 6,212 B — so the target is the
# store's OWN demonstrated shape, the same way `skill-audit.py`'s 12 KB target is
# the browser skill's demonstrated shape. It is a budget curated entries have
# been meeting all along, not an aspiration imposed on them, which is what makes
# the 13 entries above it a finding instead of a complaint.
#
# It is also exactly half the hard cap, and that agreement between two
# independent derivations is why 6 KiB was taken rather than the raw 6,212:
# a target that is a clean fraction of the cap is one number to remember, and
# rounding DOWN from the measurement keeps the gate on the strict side of what
# was measured.
#
# ⚠ NOT A GATE ON THE LIVE STORE. Nothing fails a build when an entry exceeds
# this; `subsystem-audit.py` REPORTS and a human confirms every cut
# (the `prune-index` skill's confirm-gated, diff-first contract — NOT
# `write-back.md`, whose own protocol copy was retired 2026-08-31 along with the
# append y/N; a prune is a deletion and keeps its prompt). Raising either number
# requires saying in the commit message which entry could not be expressed in
# the budget — the same discipline `test_rules_size.py` imposes.
ENTRY_TARGET_BYTES = 6_144


def _eviction_playbook() -> str:
    return f"""
  How to fix — do NOT delete an OPEN bullet to make an entry fit:

    🔴 An `OPEN:` bullet ALWAYS STAYS. It is the one thing in the store that
       cannot be re-derived by re-running recon, at any age or size. It is never
       an eviction candidate and never counts toward reclaiming bytes.

    What to move OUT of an entry, in order of preference:
      1. RESOLVED bullets whose pointer target RESOLVES — `subsystem-audit.py`
         lists them as EVICTABLE and names the target. Their durable form is
         already in that commit / doc / PR.
      2. A RESOLVED bullet reported `NO HOME` is NOT in (1).
         🔴 write the record first — a `claudedocs/` doc, or a commit that
         carries it — then re-run the audit, and only then evict.
      3. Duplicated statements of one gotcha across several appended bullets —
         MERGE_DUP, one statement.
      4. Anything a `## Pointers` target already holds — DROP_REDUNDANT. Check
         the destination actually holds it before deleting.

    What STAYS:
      - every `OPEN:` bullet
      - `## What it is` (it is what a briefed agent needs first)
      - the pointers themselves — they are the entry's whole job
      - a gotcha whose only written form is this bullet

    Mechanics: run the `prune-index` skill. It runs
    `scripts/subsystem-audit.py`, classifies, shows a unified diff, and writes
    only on explicit confirm. 🔴 It never runs a git command inside the store.

    Budget constants live in scripts/tests/test_subsystem_audit_budget.py:
      target {ENTRY_TARGET_BYTES:,} B   hard cap {ENTRY_HARD_BYTES:,} B
"""


# --- the constants are what the script uses -------------------------------------


def test_audit_script_pins_the_owned_constants():
    """The script's copies must equal the owned numbers.

    Watched to fail: setting `sa.TARGET = 6_143` in a scratch copy makes this go
    red naming TARGET. That is the whole point — the script can run standalone
    with literals, and this is what stops those literals drifting away from the
    argument above.
    """
    assert sa.TARGET == ENTRY_TARGET_BYTES, (
        f"\n\nscripts/subsystem-audit.py::TARGET is {sa.TARGET:,}, but this module "
        f"owns {ENTRY_TARGET_BYTES:,}.\n"
        f"  Change the number HERE, with the argument, then update the pinned copy.\n"
        f"{_eviction_playbook()}"
    )
    assert sa.HARD == ENTRY_HARD_BYTES, (
        f"\n\nscripts/subsystem-audit.py::HARD is {sa.HARD:,}, but this module "
        f"owns {ENTRY_HARD_BYTES:,}.\n{_eviction_playbook()}"
    )


def test_hard_cap_is_the_skill_body_budget():
    """The derivation is EXECUTABLE, not a claim about where a literal came from.

    `ENTRY_HARD_BYTES` says "an entry must not outweigh a whole skill body". The
    only way that sentence stays true is by reading the skill budget from the
    module that owns it. If that module moves its ceiling, this goes red and a
    human decides whether the entry cap follows — rather than the two silently
    diverging and this file's comment quietly becoming false.
    """
    skill = _load("browser-bridge/tests/test_skill_size.py", "skill_size_undertest")
    assert ENTRY_HARD_BYTES == skill.MAX_BYTES, (
        f"\n\nthe entry hard cap ({ENTRY_HARD_BYTES:,}) no longer equals the proven "
        f"SKILL.md body budget ({skill.MAX_BYTES:,}).\n"
        "  The comment on ENTRY_HARD_BYTES claims they are the same number and "
        "derives\n  its whole justification from that. Either follow the skill "
        "budget, or rewrite\n  the justification to stand on its own."
    )


def test_target_is_half_the_hard_cap():
    """The second, independent derivation stated on ENTRY_TARGET_BYTES.

    ⚠ AN INVARIANT GUARD, NOT REGRESSION COVERAGE — it was green the moment it
    was written and has never caught anything. It is here so that changing one
    number without the other fails loudly instead of leaving the comment's "it is
    also exactly half the hard cap" silently false.
    """
    assert ENTRY_TARGET_BYTES * 2 == ENTRY_HARD_BYTES


def test_playbook_names_the_open_rule_and_the_no_home_step():
    """The playbook renders only on FAILURE, so exercise it directly.

    A playbook that had lost the OPEN rule would read as licence to delete the
    one class of content the store cannot re-derive — and nobody would see it
    until the day they were over budget and looking for bytes.
    """
    pb = _eviction_playbook()
    assert "`OPEN:` bullet ALWAYS STAYS" in pb
    assert "NO HOME" in pb and "write the record first" in pb
    assert "never runs a git command inside the store" in pb
    assert f"{ENTRY_TARGET_BYTES:,}" in pb and f"{ENTRY_HARD_BYTES:,}" in pb


# --- the numbers are LOAD-BEARING ------------------------------------------------
#
# 🔴 Without these, every assertion above passes for ANY pair of integers. They
# are what make the constants a gate rather than two numbers in a comment. The
# fixtures are sized RELATIVE to the constants so the tests keep meaning the same
# thing after a deliberate ratchet, and the boundary is probed on BOTH sides —
# `claude/RULES.md`: "measure at >=2 points (a boundary *and* a middle)".


def _entry(size: int) -> str:
    """A minimal VALID entry padded to exactly `size` bytes. Synthetic — the live
    store is never read by this module."""
    head = (
        "---\nservice: pad\nscope: synth\naliases: []\n"
        "sensitivity: public\ncreated_by: analyze-service\n---\n\n"
        "## What it is\nA synthetic fixture.\n\n## Pointers\n\n"
        "## Nuance / work-history\n- 2026-01-01: filler.\n"
    )
    pad = size - len(head.encode())
    assert pad >= 0, f"header alone is {len(head.encode())} B; cannot make {size} B"
    return head + ("x" * pad)


def _store(tmp_path: Path, sizes: dict[str, int]) -> Path:
    root = tmp_path / "store"
    (root / "synth").mkdir(parents=True)
    (root / "synth" / "README.md").write_text("policy: synthetic\n", encoding="utf-8")
    for name, size in sizes.items():
        text = _entry(size).replace("service: pad", f"service: {name}")
        # replacing the service name changed the length; re-pad to exact size
        delta = size - len(text.encode())
        text = text + ("x" * delta) if delta > 0 else text[: len(text) + delta]
        (root / "synth" / f"{name}.md").write_text(text, encoding="utf-8")
    return root


def _statuses(root: Path) -> dict[str, str]:
    a = sa.audit_store(root)
    return {e.filename: e.status for e in a.entries}


def test_target_boundary_is_exact(tmp_path):
    """AT the target is OK; one byte over is not. Probed on both sides."""
    root = _store(tmp_path, {
        "at": ENTRY_TARGET_BYTES,
        "over": ENTRY_TARGET_BYTES + 1,
        "under": ENTRY_TARGET_BYTES - 1024,
    })
    st = _statuses(root)
    assert st["under.md"] == "OK"
    assert st["at.md"] == "OK", "an entry exactly AT the target must not be reported over"
    assert st["over.md"] == "OVER TARGET"


def test_hard_cap_boundary_is_exact(tmp_path):
    root = _store(tmp_path, {
        "at": ENTRY_HARD_BYTES,
        "over": ENTRY_HARD_BYTES + 1,
    })
    st = _statuses(root)
    assert st["at.md"] == "OVER TARGET", "at the cap is over TARGET but not over the CAP"
    assert st["over.md"] == "OVER HARD CAP"


def test_clean_store_says_do_not_churn(tmp_path):
    """The NEGATIVE control for the verdict line.

    A verdict that can only ever say "prune needed" is not a verdict. This is the
    other side: a store with nothing wrong must produce the stop instruction, and
    must NOT produce the warning.
    """
    import io

    root = _store(tmp_path, {"small": 1024})
    a = sa.audit_store(root)
    buf = io.StringIO()
    sa.render(a, show_all=True, n_detail=5, check_prs=False, out=buf)
    text = buf.getvalue()
    assert "no prune needed (stop; do not churn the files)" in text
    assert "⚠ prune needed" not in text
    # the denominator is printed even when everything is clean — a bare zero from
    # a scan that walked nothing must be impossible to mistake for this
    assert "1 entries examined" in text


def test_over_budget_store_says_prune_needed(tmp_path):
    """The POSITIVE control for the same line."""
    import io

    root = _store(tmp_path, {"fat": ENTRY_HARD_BYTES + 4096})
    a = sa.audit_store(root)
    buf = io.StringIO()
    sa.render(a, show_all=True, n_detail=5, check_prs=False, out=buf)
    text = buf.getvalue()
    assert "⚠ prune needed" in text
    assert "over the 12,288 B hard cap" in text
    assert "no prune needed" not in text


@pytest.mark.parametrize("attr", ["TARGET", "HARD"])
def test_budget_line_never_prints_a_naked_number(attr):
    """The output must carry the justification and route to this module.

    `claude/RULES.md`: a number with no argument attached is the thing nobody
    re-derives. The audit prints both beside the constants, so a reader of the
    OUTPUT — not just of the source — knows what the budget claims and where to
    argue with it.
    """
    import io

    a = sa.StoreAudit(
        store_root=Path("/nonexistent"), entries=[], collisions=[], scopes=["synth"],
        scopes_without_readme=[], repos={}, unresolved_scopes=[],
    )
    buf = io.StringIO()
    sa.render(a, show_all=True, n_detail=1, check_prs=False, out=buf)
    text = buf.getvalue()
    assert f"{getattr(sa, attr):,}" in text
    assert "scripts/tests/test_subsystem_audit_budget.py" in text
    assert sa.BUDGET_JUSTIFICATION in text
