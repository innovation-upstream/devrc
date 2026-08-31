"""The two-way pin on `subsystem-audit.py::ACKNOWLEDGED_OVER_CAP`.

WHAT THE LIST IS FOR
--------------------
A handful of live index entries are over the per-entry hard cap and CANNOT be
brought under it by the lifecycle the auditor owns: what remains in them is
`OPEN:` bullets (never evictable, at any age) and gotchas whose only written
form is that bullet. So `## verdict` printed `⚠ prune needed` on every run,
forever, with no action that could ever clear it — and `claude/RULES.md` is
explicit that a permanently-red gate is worse than no gate, because it trains
everyone to stop reading the verdict.

`ACKNOWLEDGED_OVER_CAP` is the fix, in the shape `run-tests.sh` already uses for
`EXPECTED_SKIPS`: a pinned ENUMERATION, never a raised number. An over-cap entry
that is not spelled in it is still a finding.

🔴 THE PIN IS TWO-WAY, AND THE SECOND DIRECTION IS THE POINT
------------------------------------------------------------
  1. an over-cap entry MISSING from the list  -> a finding (new bloat is caught);
  2. a listed entry NO LONGER over the cap    -> a STALE ACKNOWLEDGEMENT finding.

(2) is the direction that decays silently. Without it a line could sit in the
list forever excusing an entry somebody already fixed, and the enumeration would
quietly become the blanket exemption it was written not to be. It is tested
first here, and `test_stale_...` is the first test in the file, deliberately.

🔴 WHERE THE PIN ACTUALLY RUNS — READ THIS BEFORE TRUSTING THIS FILE
--------------------------------------------------------------------
The reconciliation lives in `subsystem-audit.py::account_acknowledged`, NOT in
this module, and that is not an accident of layering. The hermetic gate runs in
a nix sandbox that cannot see `~/.claude/analyze-service-index/` at all, so a
pin implemented as a test assertion over the live store would be STRUCTURALLY
BLIND to the exact drift it claims to catch (`claude/RULES.md` -> "a suite whose
CONFIG pins a dimension is structurally blind to that dimension's bugs"), and
would also make an ordinary `pytest` run red whenever the operator's private
store grew. Putting it in the auditor means it is re-evaluated against the live
store on EVERY invocation.

What THIS file therefore owns is that the mechanism is wired correctly, proven
in both directions against SYNTHETIC stores — plus the structural checks on the
real list that can be made without opening the store.

🔴 OFFLINE, HERMETIC AND SYNTHETIC, like its two siblings. Nothing under
`~/.claude/analyze-service-index/` is opened and no line of it is reproduced.
That store is curated, CLIENT-CONFIDENTIAL and not re-derivable, and devrc is
PUBLIC.
Entry NAMES appear in `ACKNOWLEDGED_OVER_CAP` itself — they are already in the
audit's own printed output — but no entry CONTENT may enter this repo, so every
fixture below is padding generated here. `$WORKSPACE` is repointed at an empty
tmp dir in every test so no real sibling repo is touched either.
"""
import importlib.machinery
import importlib.util
import io
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(relpath: str, modname: str):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / relpath))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    loader.exec_module(mod)
    return mod


sa = _load("subsystem-audit.py", "subsystem_audit_undertest_ack")


# --- synthetic fixtures ----------------------------------------------------------


def _entry_text(service: str, scope: str, size: int) -> str:
    """A minimal VALID entry padded to exactly `size` bytes. Pure padding.

    Deliberately carries complete front matter and one unmarked nuance bullet, so
    the ONLY thing a fixture store can be found guilty of is its size. A fixture
    that also tripped the front-matter or lifecycle counters would let a verdict
    test pass for the wrong reason.
    """
    head = (
        f"---\nservice: {service}\nscope: {scope}\naliases: []\n"
        "sensitivity: public\ncreated_by: analyze-service\n---\n\n"
        "## What it is\nA synthetic fixture.\n\n## Pointers\n\n"
        "## Nuance / work-history\n- 2026-01-01: filler.\n"
    )
    pad = size - len(head.encode())
    assert pad >= 0, f"header alone is {len(head.encode())} B; cannot make {size} B"
    return head + ("x" * pad)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Build a store from `{"<scope>/<file>.md": size}` and return its root.

    `$WORKSPACE` is repointed at an empty directory so `scope_repo` derives
    nothing: these fixtures are about SIZE accounting, and letting a scope name
    like `devrc` resolve to the operator's real checkout would drag git into a
    test that has no business running it.
    """
    empty_ws = tmp_path / "empty-workspace"
    empty_ws.mkdir()
    monkeypatch.setenv("WORKSPACE", str(empty_ws))

    def build(spec: dict[str, int], name: str = "store") -> Path:
        root = tmp_path / name
        for where, size in spec.items():
            scope, filename = where.split("/", 1)
            d = root / scope
            d.mkdir(parents=True, exist_ok=True)
            (d / "README.md").write_text("policy: synthetic\n", encoding="utf-8")
            (d / filename).write_text(
                _entry_text(filename[:-3], scope, size), encoding="utf-8"
            )
        return root

    return build


def _render(root: Path, acknowledged: dict[str, str], scope: str | None = None) -> str:
    a = sa.audit_store(root, scope)
    buf = io.StringIO()
    sa.render(a, show_all=True, n_detail=10, check_prs=False, out=buf,
              acknowledged=acknowledged)
    return buf.getvalue()


def _account(root: Path, acknowledged: dict[str, str], scope: str | None = None):
    a = sa.audit_store(root, scope)
    return sa.account_acknowledged(a.entries, a.scopes, acknowledged)


OVER = sa.HARD + 2048
UNDER = sa.HARD - 2048
REASON = "synthetic reason"


# --- DIRECTION 2: a listed entry that is no longer over cap ----------------------
#
# 🔴 WRITTEN FIRST ON PURPOSE. This is the half that rots without anyone
# noticing: an entry gets pruned, the exemption stays, and the list has silently
# become a standing excuse instead of an enumeration.


def test_stale_a_listed_entry_no_longer_over_cap_is_a_finding(store):
    """The exemption has been earned away — say so, by name."""
    root = store({"synth/fixed.md": UNDER, "synth/other.md": 1024})
    acc = _account(root, {"synth/fixed.md": REASON})

    assert acc.stale == (("synth/fixed.md", UNDER),), (
        "a listed entry that is UNDER the cap must be reported STALE. Without this, "
        "an exemption outlives the bloat it excused and the list becomes a blanket."
    )
    assert acc.honoured == ()
    assert acc.findings == 1


def test_stale_entry_is_named_in_the_output_and_flips_the_verdict(store):
    root = store({"synth/fixed.md": UNDER})
    text = _render(root, {"synth/fixed.md": REASON})

    assert "STALE ACKNOWLEDGEMENT" in text
    assert "synth/fixed.md" in text
    assert "stale acknowledgement(s)" in text
    assert "⚠ prune needed" in text, (
        "a stale acknowledgement is a FINDING, not a note — it must reach the verdict"
    )
    assert "no prune needed" not in text


def test_absent_a_listed_entry_that_no_longer_exists_is_a_finding(store):
    """Renamed or deleted. The line has rotted and must be removed."""
    root = store({"synth/present.md": OVER})
    acc = _account(root, {"synth/present.md": REASON, "synth/vanished.md": REASON})

    assert acc.absent == ("synth/vanished.md",)
    assert acc.honoured != () and acc.unacknowledged == ()
    text = _render(root, {"synth/present.md": REASON, "synth/vanished.md": REASON})
    assert "NO LONGER EXIST" in text and "synth/vanished.md" in text
    assert "⚠ prune needed" in text


def test_a_key_outside_the_audited_scopes_is_not_judged_absent(store):
    """🔴 UNMEASURED IS NOT MISSING.

    Under `--scope synth` the `other` scope was never examined, so a key naming
    it says nothing about whether that entry exists. Reporting it `absent` would
    manufacture a rotten line out of a narrowed run and send the operator to
    delete an exemption that is perfectly correct.
    """
    root = store({"synth/a.md": OVER, "other/b.md": OVER})
    acc = _account(root, {"synth/a.md": REASON, "other/b.md": REASON}, scope="synth")

    assert acc.absent == ()
    assert acc.stale == ()
    assert [e.where for e in acc.honoured] == ["synth/a.md"], (
        "only the audited scope's entries may be accounted"
    )


# --- DIRECTION 1: an over-cap entry missing from the list ------------------------


def test_an_over_cap_entry_missing_from_the_list_is_still_a_finding(store):
    """New bloat is caught exactly as it was before the list existed."""
    root = store({"synth/known.md": OVER, "synth/newbloat.md": OVER})
    acc = _account(root, {"synth/known.md": REASON})

    assert [e.where for e in acc.unacknowledged] == ["synth/newbloat.md"]
    assert [e.where for e in acc.honoured] == ["synth/known.md"]
    assert acc.findings == 1


def test_unacknowledged_over_cap_entry_reaches_the_verdict(store):
    root = store({"synth/known.md": OVER, "synth/newbloat.md": OVER})
    text = _render(root, {"synth/known.md": REASON})

    assert "⚠ prune needed" in text
    assert f"1 entr(ies) over the {sa.HARD:,} B hard cap" in text, (
        "the hard-cap count must exclude ONLY the acknowledged entries"
    )
    assert "synth/newbloat.md" in text


# --- verdict behaviour, both ways -------------------------------------------------


def test_verdict_is_clean_when_the_only_over_cap_entries_are_acknowledged(store):
    """The whole point: a verdict somebody can turn green."""
    root = store({"synth/big.md": OVER, "synth/small.md": 1024})
    text = _render(root, {"synth/big.md": REASON})

    assert "no prune needed (stop; do not churn the files)" in text
    assert "⚠ prune needed" not in text


def test_a_clean_verdict_still_names_the_acknowledged_entries(store):
    """🔴 ACKNOWLEDGED MUST NEVER MEAN INVISIBLE.

    An exemption a reader cannot see is how an enumeration rots into a blanket.
    `run-tests.sh` prints its EXPECTED_SKIPS accounting for exactly this reason,
    so the count, the name and the reason must all survive into a GREEN run —
    the run nobody reads carefully.
    """
    root = store({"synth/big.md": OVER, "synth/small.md": 1024})
    text = _render(root, {"synth/big.md": REASON})

    assert "1 ACKNOWLEDGED over cap" in text
    assert "synth/big.md" in text
    assert REASON in text, "the recorded reason must be printed, not just the name"
    assert "excluded from the verdict, NOT from the store" in text
    assert "all 2 entries within budget" not in text, (
        "the clean line must not claim every entry is within budget while an "
        "acknowledged entry sits over the cap — a verdict that lies to go green "
        "is worse than the red one it replaced"
    )
    assert "1 of 2 entries within budget" in text


def test_introducing_one_unacknowledged_over_cap_entry_flips_it_back(store):
    """The same store as the clean case, plus one unlisted fat entry."""
    ack = {"synth/big.md": REASON}
    clean = _render(store({"synth/big.md": OVER, "synth/small.md": 1024}, "a"), ack)
    dirty = _render(
        store({"synth/big.md": OVER, "synth/small.md": 1024, "synth/rogue.md": OVER}, "b"),
        ack,
    )

    assert "no prune needed" in clean and "⚠ prune needed" not in clean
    assert "⚠ prune needed" in dirty and "no prune needed" not in dirty
    assert "synth/rogue.md" in dirty


def test_acknowledgement_does_not_leak_into_the_target_tier(store):
    """An acknowledged entry is exempt from the CAP finding, not from the target.

    It is still over the 6,144 B target and still counted there. Letting the
    exemption widen by one tier is exactly how a named enumeration turns into the
    raised number it was written to avoid.
    """
    root = store({"synth/big.md": OVER})
    text = _render(root, {"synth/big.md": REASON})

    assert f"1 over the {sa.TARGET:,} B target" in text
    assert "OVER HARD CAP" in text, "the size table still reports its true status"


def test_the_size_table_tags_acknowledged_entries(store):
    root = store({"synth/big.md": OVER, "synth/rogue.md": OVER})
    text = _render(root, {"synth/big.md": REASON})
    tagged = [ln for ln in text.splitlines() if "[ACKNOWLEDGED]" in ln]

    assert len(tagged) == 1 and "synth/big.md" in tagged[0]
    assert not any("synth/rogue.md" in ln for ln in tagged)


# --- the REAL list, exercised without opening the store ---------------------------
#
# 🔴 These drive `ACKNOWLEDGED_OVER_CAP` ITSELF against a MIRROR store — synthetic
# padding written at the real key paths. That exercises the real list (a
# malformed key, a blank reason or a duplicate is caught here) while keeping the
# fixture entirely fabricated, which is what the public-repo rule requires.


def _mirror(store, sizes: dict[str, int], name: str = "mirror") -> Path:
    return store({k: sizes[k] for k in sizes}, name)


def test_every_real_key_is_a_wellformed_scope_slash_filename():
    for key in sa.ACKNOWLEDGED_OVER_CAP:
        assert key.count("/") == 1, f"{key!r} must be exactly `<scope>/<filename>`"
        scope, filename = key.split("/")
        assert scope and filename, f"{key!r} has an empty half"
        assert filename.endswith(".md"), f"{key!r} must name a .md entry"
        assert not scope.startswith("."), f"{key!r} has a suspicious scope"


def test_every_real_key_carries_a_nonempty_reason_naming_the_admission_test():
    """A reason is what stops the list growing by sentiment.

    Each line must state WHY lifecycle pruning cannot reach the entry — that is
    the admission test. A key with a blank or generic reason is an exemption
    nobody can argue with, which is the failure mode this whole design is against.
    """
    for key, reason in sa.ACKNOWLEDGED_OVER_CAP.items():
        assert reason.strip(), f"{key!r} has no recorded reason"
        assert len(reason) > 40, f"{key!r}'s reason is too short to be an argument"
        assert "lifecycle pruning cannot reduce it further" in reason, (
            f"{key!r}'s reason must state the admission test it passed"
        )
        assert "EVICTABLE" in reason, (
            f"{key!r}'s reason must record its measured EVICTABLE bullet count — "
            "the number that proves the lifecycle cannot reach it"
        )


def test_the_real_list_is_fully_honoured_when_every_key_is_over_cap(store):
    """The mirror's clean baseline: no finding, and every key accounted."""
    keys = list(sa.ACKNOWLEDGED_OVER_CAP)
    root = _mirror(store, {k: OVER for k in keys})
    acc = _account(root, sa.ACKNOWLEDGED_OVER_CAP)

    assert acc.findings == 0
    assert sorted(e.where for e in acc.honoured) == sorted(keys)


def test_the_real_list_goes_red_when_one_of_its_keys_drops_under_cap(store):
    """DIRECTION 2 against the real list, one key at a time.

    Parameterising over every key rather than probing one is deliberate: a
    reconciliation that only noticed the first (or the largest) entry would pass
    a single-key probe and be blind to the rest.
    """
    keys = list(sa.ACKNOWLEDGED_OVER_CAP)
    for victim in keys:
        sizes = {k: (UNDER if k == victim else OVER) for k in keys}
        acc = _account(_mirror(store, sizes, f"m-{keys.index(victim)}"),
                       sa.ACKNOWLEDGED_OVER_CAP)
        assert acc.stale == ((victim, UNDER),), (
            f"{victim} dropped under the cap but was not reported STALE"
        )
        assert acc.findings == 1


def test_the_real_list_goes_red_on_an_over_cap_entry_it_does_not_name(store):
    """DIRECTION 1 against the real list."""
    keys = list(sa.ACKNOWLEDGED_OVER_CAP)
    scope = keys[0].split("/")[0]
    intruder = f"{scope}/definitely-not-acknowledged.md"
    assert intruder not in sa.ACKNOWLEDGED_OVER_CAP

    sizes = {k: OVER for k in keys}
    sizes[intruder] = OVER
    acc = _account(_mirror(store, sizes, "intruder"), sa.ACKNOWLEDGED_OVER_CAP)

    assert [e.where for e in acc.unacknowledged] == [intruder]
    assert acc.stale == () and acc.absent == ()
    assert acc.findings == 1
