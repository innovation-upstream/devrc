#!/usr/bin/env python3
"""`scripts/cairn-cutover.py` — the criterion 9 cutover's refusals.

🔴 WHAT THIS FILE IS GUARDING: THAT EVERY WAY OF NOT KNOWING ENDS IN A REFUSAL.
The script's job is to move a curated, client-confidential, per-host store into a
hosted one and then make local disk read-only. Every failure of that operation
is silent by nature — a bullet that was overwritten, an entry that was stranded,
a freeze that did not take. So the guards below are all of one shape: an input
the script CANNOT resolve must stop it, and a zero it did not measure must never
read as a clean one.

The two claims that carry the most weight, and are therefore tested with
controls rather than assertions:

  * the backup precondition REFUSES on every way of not getting an answer, not
    only on a stale answer (devrc#1132 exists because fifteen places asserted
    this store's backup state and were wrong);
  * the freeze's evidence is a WATCHED EACCES on a real file, and the check
    fails when a single entry is still writable.
"""

from __future__ import annotations

import argparse
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CUTOVER = REPO / "scripts" / "cairn-cutover.py"
SERVER_PY = REPO / "scripts" / "subsystem-store-api" / "server.py"

sys.path.insert(0, str(REPO / "scripts"))
from testlib import mockbin  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, str(path))
    )
    module = importlib.util.module_from_spec(spec)
    # `sys.modules[name] = module` BEFORE `exec_module` — see
    # `test_cairn_cli._load_api` for the dataclass/`__module__` mechanism.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cc():
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    return _load("cairn_cutover_under_test", CUTOVER)


@pytest.fixture(autouse=True)
def _never_touch_the_operators_run_root(cc, tmp_path, monkeypatch):
    """🔴 NO TEST MAY WRITE INTO THE REAL `~/.local/share/cairn-cutover/runs`.

    Measured: five tests called `main(... --freeze --apply)` with no `--run-dir`,
    so P5 wrote a mode ledger into the OPERATOR's run root — 66 directories
    accumulated there from test runs alone. That is not merely untidy. The
    default `--unfreeze` picks the NEWEST ledger under that root, so after any
    test run the operator's documented P5 rollback selected a SYNTHETIC ledger,
    matched nothing, and left the store frozen while reporting the entries as
    "created after the freeze". A test poisoning the recovery path of the thing
    it tests is the worst shape available.

    Every test now passes `--run-dir`; this redirects the DEFAULT too, so a
    future test that forgets cannot reach the real one. Belt and braces on
    purpose — the explicit flag documents intent, this makes forgetting safe.
    """
    monkeypatch.setattr(cc, "DEFAULT_RUN_ROOT", tmp_path / "default-run-root")


@pytest.fixture(scope="module")
def srv():
    sys.path.insert(0, str(REPO / "scripts" / "lib"))
    return _load("srv_cut", SERVER_PY)


def _entry(scope: str, service: str, *bullets: str, aliases: str | None = None) -> str:
    head = ["---", f"service: {service}", f"scope: {scope}", "sensitivity: internal"]
    if aliases is not None:
        head.append(f"aliases: [{aliases}]")
    head.append("---")
    return "\n".join([
        *head, "", "## What it is", f"the {service}.", "",
        "## Pointers", f"- `{scope}: src/{service}.py`", "",
        "## Nuance / work-history", *bullets, "",
    ])


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


# =============================================================================
# The merge rule
# =============================================================================


class TestTheMergeRule:
    def test_an_entry_the_pod_lacks_is_a_pure_ADD(self, cc, tmp_path):
        local = _tree(tmp_path / "l", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        pod = _tree(tmp_path / "p", {})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.ADD]

    def test_a_byte_identical_entry_is_SAME_which_is_what_makes_a_RERUN_a_NOOP(
        self, cc, tmp_path
    ):
        text = _entry("sc", "a", "- 2026-01-01: x.")
        local = _tree(tmp_path / "l", {"sc/a.md": text})
        pod = _tree(tmp_path / "p", {"sc/a.md": text})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.SAME]
        assert plan.shippable == []

    @pytest.mark.parametrize(
        "pod_body, why",
        [
            # 🔴 THE CASE THE FIRST FIX INVERTED. A PURE APPEND: the host added a
            # bullet, so the pod's lines are a strict SUBSET. `pod_only` (pod
            # bullets minus local) is EMPTY here — which the first version read
            # as "the difference is outside the bullet region" and refused. It is
            # the safest divergence there is, and it is what the append-only
            # protocol emits every single time, so refusing it made the gate
            # permanently red on the commonest input.
            ("- 2026-02-09: the first bullet.", "a pure append"),
            # Cosmetic-only differences must not block either.
            ("- 2026-02-09: the first bullet.\n", "a trailing newline"),
            ("- 2026-02-09: the first bullet.\r", "a CRLF line ending"),
        ],
    )
    def test_a_divergence_the_HOST_STRICTLY_CONTAINS_is_SUPERSEDED(
        self, cc, tmp_path, pod_body, why
    ):
        """The strongest arm of rule 3, and the one that must never refuse.

        When every line of the served copy is also in the host's copy, nothing on
        the pod can be lost by pushing — a supersession that is PROVEN, not
        argued. The reason text must say that, not the old sentence about the
        difference lying outside the bullet region, which for an append is false.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a",
            "- 2026-02-10: a bullet only this host has.",
            "- 2026-02-09: the first bullet.")})
        pod_dir = tmp_path / "p"
        (pod_dir / "sc").mkdir(parents=True)
        (pod_dir / "sc" / "a.md").write_text(_entry("sc", "a", pod_body))
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod_dir),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.SUPERSEDES], (
            f"{why} was refused: {plan.items[0].reason}"
        )
        assert "contains EVERY line" in plan.items[0].reason

    def test_a_STALE_pod_copy_is_SUPERSEDED_by_the_host_not_hand_merged(
        self, cc, tmp_path
    ):
        """The commonest divergence by far, and the one that must NOT stop a run.

        The pod's tree was produced FROM this host's tree, so for any entry the
        pod did not itself change, the host's copy contains everything the pod's
        does. Here the pod holds the pre-rewrite `OPEN:` line and the host holds
        the `RESOLVED` one — measured on the real store as the shape of 14 of 15
        such divergences. Refusing these would make the script demand a hand
        merge for every ordinary edit since the last seed, i.e. a gate nobody
        could pass.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: RESOLVED beef1234: the lease expired early.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: OPEN: the lease expired early.")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.SUPERSEDES]
        assert "NONE is API-attributed" in plan.items[0].reason

    def test_an_API_ATTRIBUTED_pod_bullet_the_host_lacks_forces_a_HAND_MERGE(
        self, cc, tmp_path
    ):
        """🔴 THE DISCRIMINATOR THE WHOLE RULE TURNS ON.

        A bullet carrying the `[cairn: actor/session]` trailer was written
        THROUGH the pod, so it exists in the served copy and nowhere else. This
        fixture differs from the SUPERSEDES one above by exactly that trailer and
        by nothing else — same scope, same entry, same divergence, same bullet
        count — so a mutant that stops looking for attribution flips this case
        and only this case.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: the lease expired early.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a",
            "- 2026-02-09: the lease expired early.",
            "- 2026-02-11: the retry storm follows it [cairn: zach/sess-77]")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.NEEDS_MERGE]
        assert "exists nowhere else" in plan.items[0].reason

    def test_a_HOST_vs_HOST_divergence_is_ALWAYS_a_hand_merge(self, cc, tmp_path):
        """🔴 NO SUPERSESSION ARGUMENT EXISTS HERE, whatever the mtimes say.

        Neither host's copy is a derivative of the other's — they are two
        independent edits of one file on two unreplicated stores. Note that the
        pod's copy carries NO attribution here, so the previous rule would have
        said SUPERSEDES: the peer check is what makes this case land differently,
        which is exactly the mutation that must be caught.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": _entry(
            "sc", "a", "- 2026-03-04: this host's account.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a", "- 2026-03-01: the seeded copy.")})
        peer = {"sc/a.md": cc.EntryFacts("sc/a.md", "e" * 64, (), ())}
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None, peer=peer)
        assert [i.verdict for i in plan.items] == [cc.NEEDS_MERGE]
        assert "neither is a derivative" in plan.items[0].reason

    def test_a_HOST_vs_HOST_agreement_is_NOT_a_hand_merge(self, cc, tmp_path):
        """The control for the rule above: the peer holding the SAME bytes must
        not trip it, or every shared entry would demand a merge and the check
        would be a constant `True` wearing a predicate's clothes."""
        text = _entry("sc", "a", "- 2026-03-04: agreed.")
        local = _tree(tmp_path / "l", {"sc/a.md": text})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry("sc", "a", "- 2026-03-01: old.")})
        same_sha = cc.read_store(local)["sc/a.md"].sha256
        peer = {"sc/a.md": cc.EntryFacts("sc/a.md", same_sha, (), ())}
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None, peer=peer)
        assert [i.verdict for i in plan.items] == [cc.SUPERSEDES]

    def test_a_HOST_vs_HOST_divergence_is_a_HAND_MERGE_even_when_the_POD_LACKS_IT(
        self, cc, tmp_path
    ):
        """🔴 THE ORDERING BUG RULE 4 WAS WRITTEN TO FORBID, COMMITTED BY THE CODE
        THAT STATES IT.

        The `ADD` clause used to run BEFORE the peer check, so for any entry the
        pod does not yet hold — which is EVERY entry in a host-exclusive scope,
        i.e. the whole population this migration is about — a host-vs-host
        disagreement was classified `ADD` and pushed. First-host-to-run-wins,
        silently, with no operator decision: exactly the last-write-wins the rule
        forbids "whatever the mtimes say".

        This fixture differs from the earlier host-vs-host case in ONE respect —
        the pod does not hold the entry — which is the axis the bug lived on.
        """
        local = _tree(tmp_path / "l", {"sc/only-on-hosts.md": _entry(
            "sc", "only-on-hosts", "- 2026-03-04: this host's account.")})
        peer = {"sc/only-on-hosts.md": cc.EntryFacts(
            "sc/only-on-hosts.md", "d" * 64, (), ())}
        plan = cc.plan_delta(cc.read_store(local), {},   # pod holds NOTHING
                             store_root=local, merged_dir=None, peer=peer)
        assert [i.verdict for i in plan.items] == [cc.NEEDS_MERGE], (
            "a host-vs-host divergence was pushed as an ADD because the pod "
            "happened not to hold it"
        )
        assert plan.shippable == []

    def test_a_hand_authored_MERGE_wins_even_when_the_POD_LACKS_THE_ENTRY(
        self, cc, tmp_path
    ):
        """The same ordering defect, one clause over: `--merged` was consulted
        BELOW the `ADD` return, so an operator's hand-written resolution for a
        host-only entry was silently discarded in favour of the local copy. A
        human decision must not be overridden by a classifier."""
        local = _tree(tmp_path / "l", {"sc/only-on-hosts.md": _entry(
            "sc", "only-on-hosts", "- 2026-03-04: the local copy.")})
        merged = _tree(tmp_path / "m", {"sc/only-on-hosts.md": _entry(
            "sc", "only-on-hosts", "- 2026-03-04: the RESOLVED copy.")})
        plan = cc.plan_delta(cc.read_store(local), {},
                             store_root=local, merged_dir=merged)
        assert [i.verdict for i in plan.items] == [cc.MERGED]
        assert plan.items[0].source == merged / "sc/only-on-hosts.md"
        assert "RESOLVED copy" in plan.items[0].source.read_text()

    def test_a_PROSE_ONLY_divergence_is_SUPERSEDED_with_a_NON_VACUOUS_reason(
        self, cc, tmp_path
    ):
        """🔴 THE VACUOUS JUSTIFICATION. The bytes differ and the bullet lists do
        not, so whatever moved is outside the region the attribution rule can
        see. Classifying it SUPERSEDES printed "the served copy holds 0 bullet
        line(s) this host lacks and NONE is API-attributed" as the reason to
        OVERWRITE it — a vacuous truth offered as evidence from a scan that
        examined nothing.

        🔴 AND `cairn put` — added by the same change — produces exactly this
        shape: its stated reasons to exist are updating `## Pointers` and
        rewriting an `OPEN:` marker, both outside the bullet set. So rule 3's
        premise is broken by this file's own sibling, not by a hypothetical.

        The fixture differs ONLY in the `## What it is` PROSE — a line that does
        not begin `- `, so it is not in the bullet set either side, which is what
        makes `pod_only` empty.

        ⚠ MEASURED WHILE WRITING THIS: `read_store` extracts EVERY line beginning
        `- `, so a `## Pointers` entry counts as a bullet too. That makes the
        attribution scan a deliberate SUPERSET (fail-safe), but it also means a
        pointers-only divergence is NOT the empty-`pod_only` case — this fixture
        was written that way first and classified SUPERSEDES, correctly.
        """
        local = _tree(tmp_path / "l", {"sc/a.md": "\n".join([
            "---", "service: a", "scope: sc", "sensitivity: internal", "---", "",
            "## What it is", "the a, as this host describes it.", "",
            "## Pointers", "- `sc: src/a.py`", "",
            "## Nuance / work-history", "- 2026-01-01: one shared bullet.", "",
        ])})
        pod = _tree(tmp_path / "p", {"sc/a.md": "\n".join([
            "---", "service: a", "scope: sc", "sensitivity: internal", "---", "",
            "## What it is", "the a, rewritten through a whole-file PUT.", "",
            "## Pointers", "- `sc: src/a.py`", "",
            "## Nuance / work-history", "- 2026-01-01: one shared bullet.", "",
        ])})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        assert [i.verdict for i in plan.items] == [cc.SUPERSEDES], (
            f"classified {plan.items[0].verdict} with reason: {plan.items[0].reason}"
        )
        # 🔴 THE VERDICT IS SUPERSEDES AND THE REASON MUST NOT BE VACUOUS. That
        # was the whole finding: the old sentence read "the served copy holds 0
        # bullet line(s) this host lacks and NONE is API-attributed" — a scan
        # that examined nothing, printed as the justification for overwriting.
        # A version that REFUSED here was then tried and reverted: measured on
        # the real trees it turned 1 hand-merge into 10, because a wrapped
        # bullet's continuation lines are non-bullet lines.
        assert "1 line(s) this host lacks" in plan.items[0].reason
        assert "seed-time snapshot" in plan.items[0].reason

    def test_a_STALE_hand_merge_over_an_IDENTICAL_entry_is_IGNORED_and_ANNOUNCED(
        self, cc, tmp_path
    ):
        """🔴 THE DEFECT THE PREVIOUS FIX INTRODUCED, one clause over.

        Hoisting `--merged` above `ADD` was right — an operator's resolution for
        a host-only entry was being discarded. But `--merged` defaults to a
        PERSISTENT directory, so a resolution left over from an earlier round
        then preempted `SAME` as well, pushing stale bytes over a copy the pod
        and this host already agreed on byte for byte. It was reported only as a
        bare count in the verdict line, and `comm -23` compares names, so nothing
        downstream could catch it.

        An override now wins wherever a decision is needed, and is ANNOUNCED as
        stale where nothing is in dispute.
        """
        text = _entry("sc", "a", "- 2026-01-01: agreed by both.")
        local = _tree(tmp_path / "l", {"sc/a.md": text})
        pod = _tree(tmp_path / "p", {"sc/a.md": text})
        merged = _tree(tmp_path / "m", {"sc/a.md": _entry(
            "sc", "a", "- 2025-12-01: a resolution from an earlier round.")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=merged)
        assert [i.verdict for i in plan.items] == [cc.SAME], (
            f"a stale override pushed over an identical entry: {plan.items[0].reason}"
        )
        assert "STALE" in plan.items[0].reason
        assert plan.shippable == []

    def test_an_override_STILL_wins_where_a_decision_IS_needed(self, cc, tmp_path):
        """The control for the rule above — without it, "ignore stale overrides"
        could be implemented as "ignore overrides", which silently re-opens the
        defect the hoist was made to fix."""
        local = _tree(tmp_path / "l", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: host.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: pod prose.")})
        merged = _tree(tmp_path / "m", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: merged.")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=merged)
        assert [i.verdict for i in plan.items] == [cc.MERGED]

    def test_ONLY_a_hand_authored_file_clears_a_NEEDS_MERGE(self, cc, tmp_path):
        local = _tree(tmp_path / "l", {"sc/a.md": _entry("sc", "a", "- 2026-02-09: host.")})
        pod = _tree(tmp_path / "p", {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: host.",
            "- 2026-02-11: pod-only [cairn: zach/sess-77]")})
        merged = tmp_path / "m"
        before = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                               store_root=local, merged_dir=merged)
        assert [i.verdict for i in before.items] == [cc.NEEDS_MERGE]
        _tree(merged, {"sc/a.md": _entry(
            "sc", "a", "- 2026-02-09: host.",
            "- 2026-02-11: pod-only [cairn: zach/sess-77]")})
        after = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                              store_root=local, merged_dir=merged)
        assert [i.verdict for i in after.items] == [cc.MERGED]
        # The SOURCE must be the hand-authored file, not the host's copy —
        # otherwise the resolution is accepted and then silently discarded.
        assert after.items[0].source == merged / "sc/a.md"

    def test_the_attribution_pattern_matches_what_the_SERVER_actually_renders(
        self, cc, srv
    ):
        """🔴 PINNED BEHAVIOURALLY AGAINST THE WRITER, not against a copy of its
        regex. The discriminator is only as good as its agreement with the thing
        that produces the trailer; a second spelling of one pattern is the shape
        that drifts silently. So the SERVER renders a bullet and this asserts the
        cutover's pattern sees it.

        The NEGATIVE half is the part that matters: an ordinary bullet with no
        trailer must NOT match, or every divergence becomes a hand merge and the
        script is unusable.
        """
        rendered = srv.render_bullet(
            "the retry storm follows it", actor="zach", session="sess-77",
            today="2026-02-11",
        )
        assert cc.ATTRIBUTION.search(rendered), rendered
        assert not cc.ATTRIBUTION.search("- 2026-02-11: the retry storm follows it")

    def test_the_walk_skips_what_seed_sh_cannot_ship(self, cc, tmp_path):
        """Dot-scopes, symlinked scopes and non-`.md` files are outside the
        population `seed.sh` ships. A planner walking a wider set would push
        entries the pusher drops and then report them as landed."""
        root = _tree(tmp_path / "l", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            ".hidden/b.md": _entry("hidden", "b", "- 2026-01-01: x."),
            "sc/notes.txt": "not an entry",
        })
        (root / "linked").symlink_to(root / "sc")
        assert sorted(cc.read_store(root)) == ["sc/a.md"]

    def test_the_PLANNER_and_the_PUSHER_walk_the_SAME_population(self, cc, tmp_path):
        """🔴 A SEAM GUARD. Two components each tested alone can still be broken
        TOGETHER, and this is the seam nobody owns: `read_store` decides what the
        plan covers, `seed.sh::_shippable_entries` decides what the push ships.
        A file in one set and not the other is silent in both directions — an
        entry planned and not shipped is reported as landed, an entry shipped and
        not planned bypasses the whole merge rule.

        `seed.sh` was itself bitten by exactly this: two walks with different
        rules printed `remote_entries=1 staged_entries=2` one line above
        `seed: OK`, rc 0.

        So this runs the SHELL predicate — copied out of `seed.sh` by nothing, it
        is executed from the file itself — beside the Python one over a tree
        holding every edge case each is documented to handle, and asserts the two
        sets are equal. It pins a RELATIONSHIP, not a component.
        """
        import re as _re
        import subprocess

        root = _tree(tmp_path / "store", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
            "sc/notes.txt": "not an entry",
            "other/c.md": _entry("other", "c", "- 2026-01-01: z."),
            ".dot-scope/d.md": _entry("dot", "d", "- 2026-01-01: w."),
            "sc/nested/deep.md": _entry("deep", "deep", "- 2026-01-01: v."),
            "top-level.md": "a stray entry at depth 1",
        })
        (root / "linked-scope").symlink_to(root / "sc")
        (root / "sc" / "adir.md").mkdir()      # a DIRECTORY named *.md

        # The shell half, taken from `seed.sh`'s own source so a change there
        # fails HERE rather than diverging quietly.
        seed_src = (REPO / "scripts" / "subsystem-store-api" / "seed.sh").read_text()
        m = _re.search(r"\(\s*cd \"\$1\" && (find \. .*?)\s*\)", seed_src)
        assert m, "could not find _shippable_entries' find expression in seed.sh"
        find_expr = m.group(1)
        # 🔴 `mockbin.SH` (/bin/sh), not `bash`. The expression is plain `find`
        # with no bashisms, and /bin/sh is the one interpreter path guaranteed to
        # exist inside the nix build sandbox — the tier this suite is gated on
        # and the tier that is structurally blind to nothing else here.
        got = subprocess.run(
            [mockbin.SH, "-c", f'cd "$1" && {find_expr}', "_", str(root)],
            capture_output=True, text=True, timeout=60,
        )
        assert got.returncode == 0, got.stderr
        shell_set = {line[2:] for line in got.stdout.splitlines() if line.startswith("./")}

        python_set = set(cc.read_store(root))
        # POSITIVE CONTROL: both must be NON-EMPTY. Two empty sets are equal, and
        # a comparison over nothing is the reassuring zero this repo refuses.
        assert shell_set, "the shell predicate matched nothing — it proves nothing"
        assert python_set, "the python walk matched nothing — it proves nothing"
        assert python_set == shell_set, (
            f"the planner and the pusher disagree.\n"
            f"  only the planner sees: {sorted(python_set - shell_set)}\n"
            f"  only the pusher sees:  {sorted(shell_set - python_set)}"
        )


# =============================================================================
# Ref collisions
# =============================================================================


class TestRefCollisions:
    def test_two_entries_claiming_one_ALIAS_is_LIVE_and_BLOCKS(self, cc):
        union = {
            "sc/one.md": cc.EntryFacts("sc/one.md", "1" * 64, ("shared-alias",), ()),
            "sc/two.md": cc.EntryFacts("sc/two.md", "2" * 64, ("shared-alias",), ()),
        }
        found = cc.ref_collisions(union)
        assert [c.live for c in found] == [True]
        assert found[0].claimants == ("one.md", "two.md")

    def test_an_alias_SHADOWED_by_a_filename_is_LATENT_and_does_NOT_block(self, cc):
        """🔴 MEASURED, NOT ASSUMED — and the measurement is the next test.

        `resolve_ref_tiered` reaches the alias tier only when the FILENAME tier
        returned zero hits, so an alias spelling another entry's filename can
        never be chosen. Treating it as live would refuse a migration over a
        defect that is already present, already harmless, and present on the
        real store today — a permanently-red gate.
        """
        union = {
            "sc/one.md": cc.EntryFacts("sc/one.md", "1" * 64, (), ()),
            "sc/two.md": cc.EntryFacts("sc/two.md", "2" * 64, ("one",), ()),
        }
        found = cc.ref_collisions(union)
        assert [c.live for c in found] == [False]
        assert found[0].shadowed_by == ("one.md",)

    def test_the_FILENAME_tier_really_does_win__measured_against_the_resolver(self):
        """The precedence claim the LATENT class rests on, exercised on the real
        resolver rather than restated from its docstring.

        🔴 A DOCSTRING IS NOT A CODE PATH. The whole `latent` classification is
        an argument about which tier answers first; if that is wrong, this script
        waves through a collision that makes two entries unwritable.
        """
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_recall as rc

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {
                "sc/one.md": _entry("sc", "one", "- 2026-01-01: x."),
                "sc/two.md": _entry("sc", "two", "- 2026-01-01: y.", aliases="one"),
            })
            _store, index = rc.load_store(str(root), verb="read")
            entry, tier = rc.resolve_ref_tiered("one", index, "sc")
        assert entry is not None and entry.filename == "one.md"
        assert tier == "filename", (
            "the alias tier answered first; the LATENT classification is unsafe"
        )

    def test_one_slug_in_two_SCOPES_is_not_a_collision(self, cc):
        """Refs resolve WITHIN a scope, so the same stem in two scopes is fine.

        ⚠ RENAMED. This was called `test_two_files_with_one_SLUG_is_a_FILENAME_
        collision` while asserting the empty list — a name that said the opposite
        of its assertion, over the arm that had no positive coverage at all. The
        real FILENAME collision now has its own test below.
        """
        union = {
            "sc/dup.md": cc.EntryFacts("sc/dup.md", "1" * 64, (), ()),
            "other/dup.md": cc.EntryFacts("other/dup.md", "2" * 64, (), ()),
        }
        assert cc.ref_collisions(union) == []

    def test_a_KIND_QUALIFIED_file_collides_with_its_BARE_sibling(self, cc):
        """🔴 THE COLLISION THE FIRST VERSION COULD NOT SEE — and the FILENAME
        arm's only positive coverage.

        `resolve_ref_tiered` matches a kind-less ref against `e.slug` **with no
        kind constraint**, so `svc` hits BOTH `svc.md` and `svc.process.md` and
        raises `AmbiguousRefError` — both entries become unwritable, which is the
        exact condition P2 exists to detect. The first implementation registered
        only kind-less files under their slug, so it returned `[]` for this pair
        while the real resolver raised. `repo-cos.process` is the resolver
        docstring's own worked example, so the shape is in live use.

        The qualified ref is NOT ambiguous and must not be reported — that is the
        second assertion, and without it a fix could pass by flagging everything.
        """
        union = {
            "sc/svc.md": cc.EntryFacts("sc/svc.md", "1" * 64, (), ()),
            "sc/svc.process.md": cc.EntryFacts("sc/svc.process.md", "2" * 64, (), ()),
        }
        found = cc.ref_collisions(union)
        assert [(c.tier, c.ref, c.claimants) for c in found] == [
            ("FILENAME", "svc", ("svc.md", "svc.process.md"))
        ], found
        assert found[0].live

    def test_that_collision_is_the_one_the_REAL_resolver_raises_on(self, cc):
        """The measurement behind the test above, against the resolver itself —
        so the checker's claim is pinned to the reader's behaviour and not to my
        reading of it."""
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_recall as rc
        import subsystem_resolver as sr

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {
                "sc/svc.md": _entry("sc", "svc", "- 2026-01-01: x."),
                "sc/svc.process.md": _entry("sc", "svc", "- 2026-01-01: y."),
            })
            _store, index = rc.load_store(str(root), verb="read")
            with pytest.raises(sr.AmbiguousRefError):
                rc.resolve_ref_tiered("svc", index, "sc")
            # The QUALIFIED ref is unambiguous — the control that keeps the
            # assertion above from being "any ref in this scope raises".
            entry, tier = rc.resolve_ref_tiered("svc.process", index, "sc")
            assert entry is not None and tier == "filename"

    def test_TWO_kind_qualified_siblings_do_NOT_collide__no_FALSE_positive(self, cc):
        """🔴 THE FALSE POSITIVE THE PREVIOUS FIX INTRODUCED.

        Registering a bare slug for EVERY entry plus a `slug.kind` key flattened
        two namespaces the resolver keys differently. Measured: `svc.process.md`
        (slug `svc`, kind `process`) beside `svc.process.doc.md` (slug
        `svc.process`, kind `doc`) both landed under the key `svc.process` and
        were reported LIVE — while the resolver answers `svc.process`
        unambiguously. A LIVE collision BLOCKS the cutover, so this is the
        permanently-red-gate direction the fix's own comment names.

        The checker now asks tier 1 directly instead of approximating it.
        """
        union = {
            "sc/svc.process.md": cc.EntryFacts("sc/svc.process.md", "1" * 64, (), ()),
            "sc/svc.process.doc.md": cc.EntryFacts(
                "sc/svc.process.doc.md", "2" * 64, (), ()),
        }
        assert cc.ref_collisions(union) == []

    def test_the_QUALIFIED_ref_branch_is_REACHED__a_bare_lookup_would_invent_one(
        self, cc
    ):
        """🔴 THE CASE THAT MAKES THE KIND-QUALIFIED LOOKUP OBSERVABLE.

        A mutation sweep scored `if rkind is not None:` -> `if False:` as
        SURVIVED: with only two entries, collapsing every ref to the bare
        `slug == ref` lookup happened to return the same COUNTS, so no assertion
        moved. A branch whose removal changes nothing is not covered, however
        many tests mention it.

        Three entries make the two lookups disagree. Under the correct rule
        `a.process` is kind-qualified — slug `a`, kind `process` — and matches
        exactly `a.process.md`. Under a bare lookup it matches every entry whose
        SLUG is the string `a.process`, which is both of the doubly-qualified
        files, and reports a LIVE collision the resolver does not have. LIVE
        blocks the cutover, so the mutant is the permanently-red-gate direction.
        """
        union = {
            "sc/a.process.md": cc.EntryFacts("sc/a.process.md", "1" * 64, (), ()),
            "sc/a.process.doc.md": cc.EntryFacts(
                "sc/a.process.doc.md", "2" * 64, (), ()),
            "sc/a.process.org.md": cc.EntryFacts(
                "sc/a.process.org.md", "3" * 64, (), ()),
        }
        assert cc.ref_collisions(union) == [], (
            "the kind-qualified lookup was bypassed, inventing a collision on a "
            "ref the resolver answers unambiguously"
        )

    def test_that_THREE_entry_shape_is_unambiguous_for_the_REAL_resolver_too(self):
        """The measurement the test above rests on, taken from the resolver."""
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_recall as rc

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {
                "sc/a.process.md": _entry("sc", "a", "- 2026-01-01: x."),
                "sc/a.process.doc.md": _entry("sc", "a.process", "- 2026-01-01: y."),
                "sc/a.process.org.md": _entry("sc", "a.process", "- 2026-01-01: z."),
            })
            _store, index = rc.load_store(str(root), verb="read")
            assert len(index.entries("sc")) == 3, (
                f"fixture did not load: {getattr(index, 'malformed', None)}"
            )
            for ref, want in (
                ("a", "a.process.md"),
                ("a.process", "a.process.md"),
                ("a.process.doc", "a.process.doc.md"),
                ("a.process.org", "a.process.org.md"),
            ):
                entry, tier = rc.resolve_ref_tiered(ref, index, "sc")
                assert entry is not None and entry.filename == want, (ref, entry)

    def test_that_NON_collision_is_also_what_the_REAL_resolver_says(self):
        """Measured against the resolver, so the claim above is its behaviour and
        not my reading of it. All three refs resolve, none ambiguously.

        ⚠ THE FIXTURE'S `service:` VALUES ARE LOAD-BEARING AND COST A ROUND TRIP.
        Written with `service: svc` on both files, the second is MALFORMED —
        *"filename 'svc.process.doc.md' has slug 'svc.process' but `service:`
        normalizes to 'svc' — the two must agree or a ref reaches the wrong
        file"* — so the loader drops it and the test proved nothing about two
        loaded siblings. A malformed entry cannot be a claimant at all, which is
        a *narrower* fixture than the one the collision check needs to be right
        about. It is spelled correctly here so both files genuinely load.
        """
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_recall as rc

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root, {
                "sc/svc.process.md": _entry("sc", "svc", "- 2026-01-01: x."),
                "sc/svc.process.doc.md": _entry(
                    "sc", "svc.process", "- 2026-01-01: y."),
            })
            _store, index = rc.load_store(str(root), verb="read")
            # POSITIVE CONTROL: both files must have LOADED. Without this the
            # three lookups below can all pass over a one-entry index, which is
            # exactly how the first version of this test passed nothing.
            assert sorted(e.filename for e in index.entries("sc")) == [
                "svc.process.doc.md", "svc.process.md"
            ], f"malformed: {getattr(index, 'malformed', None)}"
            for ref, want in (
                ("svc", "svc.process.md"),
                ("svc.process", "svc.process.md"),
                ("svc.process.doc", "svc.process.doc.md"),
            ):
                entry, tier = rc.resolve_ref_tiered(ref, index, "sc")
                assert entry is not None and entry.filename == want, (ref, entry)
                assert tier == "filename"

    def test_a_NON_KIND_dotted_stem_is_NOT_split__no_FALSE_collision(self, cc):
        """The other direction, and the permanently-red-gate one.

        `split_kind` treats a trailing dot-segment as a kind ONLY if it is in
        `KINDS`. The hand-rolled `stem.split(".")` took `foo` out of
        `foo.notes.md`, so `foo.md` beside `foo.notes.md` would have been
        reported as a LIVE collision the resolver does not have — blocking a
        cutover over a defect that does not exist.
        """
        import subsystem_resolver as sr

        assert "notes" not in sr.KINDS, "fixture assumes `notes` is not a KIND"
        union = {
            "sc/foo.md": cc.EntryFacts("sc/foo.md", "1" * 64, (), ()),
            "sc/foo.notes.md": cc.EntryFacts("sc/foo.notes.md", "2" * 64, (), ()),
        }
        assert cc.ref_collisions(union) == []

    def test_alias_owner_parsing_refuses_a_malformed_spec(self, cc):
        assert cc.parse_alias_owner(["sc:the-ref=winner.md"]) == {("sc", "the-ref"): "winner.md"}
        for bad in ["sc-the-ref=winner.md", "sc:the-ref", "sc:=winner.md", ":a=b"]:
            with pytest.raises(ValueError):
                cc.parse_alias_owner([bad])

    def test_normalisation_is_the_RESOLVERS_not_a_second_spelling(self, cc):
        """The rule as people describe it (`lower`, `_`->`-`) is NOT the rule as
        it runs: the real one also folds every other non-slug character. A
        collision check normalising differently models a reader that does not
        exist."""
        sys.path.insert(0, str(REPO / "scripts" / "lib"))
        import subsystem_resolver

        assert cc.normalize_ref is subsystem_resolver.normalize_ref
        # A case the naive spelling gets wrong, so the identity above is not the
        # only thing holding this together.
        assert cc.normalize_ref("Foo Bar_Baz") == subsystem_resolver.normalize_ref(
            "Foo Bar_Baz"
        )
        assert cc.normalize_ref("Foo Bar_Baz") != "foo bar-baz"


# =============================================================================
# The backup precondition
# =============================================================================


class TestBackupPrecondition:
    """🔴 EVERY WAY OF NOT GETTING AN ANSWER IS A REFUSAL.

    The failure this guards is specific and has happened: fifteen places in this
    repo asserted the store's backup state and were wrong about it, in both
    directions across its life (devrc#1132). So the gate asks the cluster, and a
    missing field, a missing binary, a non-zero exit and an unparseable body are
    all refusals with their own sentence — never "0 hours ago" and never a pass.
    """

    def _fake_kubectl(self, tmp_path: Path, *, body: str, rc: int = 0) -> Path:
        """A `kubectl` on PATH that prints exactly what a test wants.

        🔴 A FAKE BINARY, NOT A MONKEYPATCH OF `run`. The thing under test is
        how this code reads a REAL subprocess's stdout and status; patching the
        runner would test the test's own idea of subprocess semantics.

        🔴 WRITTEN THROUGH `testlib.mockbin.write_exec`, WHICH OWNS THE SHEBANG.
        The first version wrote `#!/usr/bin/env bash` itself and was green on the
        dev host and RED in the nix sandbox — `/usr/bin/env` does not exist
        there, so every one of these execs failed ENOENT and the code under test
        correctly reported COULD NOT MEASURE for the wrong reason. That is the
        two-tier hazard exactly as `mockbin`'s own header documents it; this is
        the seventh site to pay it and the first not to re-derive the fix.
        """
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        # POSIX `sh`: a quoted heredoc plus `exit` and nothing bash-specific.
        return mockbin.write_exec(
            bindir / "kubectl", f"cat <<'MOCKEOF'\n{body}\nMOCKEOF\nexit {rc}\n"
        ).parent

    def _with_path(self, monkeypatch, bindir: Path):
        monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")

    def test_a_FRESH_successful_run_passes(self, cc, tmp_path, monkeypatch):
        now = datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=11)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": stamp}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None, now=now)
        assert ok, sentence
        assert "backup OK" in sentence and "11.0 h ago" in sentence

    def test_a_STALE_run_refuses_and_prints_the_age_AND_the_ceiling(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE FIXTURE OVERSHOOTS THE BOUNDARY, DELIBERATELY. 50 h against a
        36 h ceiling is neither a multiple of the ceiling nor adjacent to it, so
        an off-by-one or a flipped comparison cannot land on the same verdict by
        arithmetic accident — and the boundary itself is measured separately
        below, from both sides."""
        now = datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": stamp}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None, now=now)
        assert not ok
        assert "50.0 h old" in sentence and "36.0 h" in sentence
        # The remedy is named, and it names the form that actually updates
        # lastSuccessfulTime — a hand-rolled Job does not.
        assert "create job --from=cronjob/cj" in sentence

    @pytest.mark.parametrize("hours, expected", [(35.5, True), (36.5, False)])
    def test_the_boundary_is_measured_from_BOTH_sides(
        self, cc, tmp_path, monkeypatch, hours, expected
    ):
        """One measurement is not a general claim: a guard tested only on the
        far side of its boundary passes with the comparison deleted."""
        now = datetime(2026, 4, 7, 15, 0, tzinfo=timezone.utc)
        stamp = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": stamp}})))
        ok, _ = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None, now=now)
        assert ok is expected

    def test_a_CronJob_that_has_NEVER_succeeded_is_COULD_NOT_MEASURE_not_a_pass(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE SILENT-ZERO ARM. `status.lastSuccessfulTime` is absent on a
        CronJob that has never completed a run — and a bare `-o jsonpath` prints
        an empty string for that AND for a mis-spelled query, which is why the
        whole object is fetched and the key looked up here."""
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastScheduleTime": "2026-04-07T03:45:00Z"}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "COULD NOT MEASURE" in sentence
        assert "never completed a run" in sentence

    def test_a_MISSING_CronJob_refuses(self, cc, tmp_path, monkeypatch):
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body='Error from server (NotFound): cronjobs "cj" not found', rc=1))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "COULD NOT MEASURE" in sentence and "exited 1" in sentence

    def test_kubectl_NOT_ON_PATH_refuses(self, cc, tmp_path, monkeypatch):
        """An absent tool is the case a naive `rc != 0` check gets right by luck
        and a `try/except` around a parse gets wrong. It must not pass."""
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "COULD NOT MEASURE" in sentence

    def test_UNPARSEABLE_output_refuses(self, cc, tmp_path, monkeypatch):
        self._with_path(monkeypatch, self._fake_kubectl(tmp_path, body="<html>edge</html>"))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "did not parse" in sentence

    def test_an_UNPARSEABLE_TIMESTAMP_refuses(self, cc, tmp_path, monkeypatch):
        self._with_path(monkeypatch, self._fake_kubectl(
            tmp_path, body=json.dumps({"status": {"lastSuccessfulTime": "yesterday"}})))
        ok, sentence = cc.backup_precondition(
            namespace="ns", cronjob="cj", max_age_h=36.0, kubeconfig=None)
        assert not ok
        assert "unparseable timestamp" in sentence


# =============================================================================
# The write-route probe
# =============================================================================


class TestWriteRouteProbe:
    def _serve(self, code: int, status: str):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = b"probe\n"
                self.send_response(code)
                self.send_header("x-store-status", status)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def test_a_400_means_the_route_IS_deployed(self, cc):
        """🔴 THE INVERSION IS THE POINT, AND IT IS WHY THE PROBE IS SAFE. Only a
        server that DISPATCHED the POST can validate the body and refuse it 400;
        a read-only image never gets that far. So a refusal is the pass, and no
        input capable of being written is ever sent."""
        srv = self._serve(400, "bad-request")
        try:
            ok, rc, sentence = cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="t", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert ok, sentence
        assert rc is None
        assert "IS deployed" in sentence

    def test_a_405_is_the_ONLY_answer_that_means_NO_WRITE_ROUTE(self, cc):
        """🔴 THE CODE, NOT JUST THE VERDICT. Every falsy result used to be
        answered by the caller with RC_NO_WRITE_ROUTE, whose documented meaning
        is "the running image has no write path" — so an unreachable pod during
        P0 told the operator to redeploy the store. Only this arm may carry it.
        """
        srv = self._serve(405, "read-only")
        try:
            ok, rc, sentence = cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="t", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert not ok
        assert rc == cc.RC_NO_WRITE_ROUTE
        assert "READ-ONLY" in sentence and "operator problem" in sentence

    @pytest.mark.parametrize("code, status", [(401, "unauthorized"), (403, "")])
    def test_any_OTHER_answer_is_UNMEASURED_never_a_pass(self, cc, code, status):
        srv = self._serve(code, status)
        try:
            ok, rc, sentence = cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="t", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert not ok
        assert rc == cc.RC_COULD_NOT_MEASURE, (
            "a credential or edge refusal was reported as 'the image has no write "
            "path', which sends the operator to redeploy the store"
        )
        assert "COULD NOT MEASURE" in sentence

    def test_an_UNREACHABLE_host_is_UNMEASURED_not_a_missing_write_route(self, cc):
        import socket as _socket

        with _socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead = sock.getsockname()[1]
        ok, rc, sentence = cc.write_route_deployed(
            url=f"http://127.0.0.1:{dead}", token="t", scope="sc")
        assert not ok
        assert rc == cc.RC_COULD_NOT_MEASURE
        assert "COULD NOT MEASURE" in sentence

    def test_the_probe_sends_the_User_Agent_the_edge_REQUIRES(self, cc):
        """🔴 THE FOURTH SITE THAT HAND-ROLLED THIS HEADER IS NOW THE CLI'S OWN
        HELPER, AND THIS IS WHAT PINS IT. A drifted copy would be 403'd by the
        edge, which this probe classifies as COULD NOT MEASURE — so the symptom
        of a wrong header is an operator being told the store is unreachable."""
        seen = {}

        class Capture(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                seen.update({k.lower(): v for k, v in self.headers.items()})
                self.send_response(400)
                self.send_header("x-store-status", "bad-request")
                self.send_header("content-length", "0")
                self.end_headers()

            def log_message(self, *_a):
                return

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cc.write_route_deployed(
                url=f"http://127.0.0.1:{srv.server_address[1]}", token="tok", scope="sc")
        finally:
            srv.shutdown(); srv.server_close()
        assert seen.get("user-agent") == "subsystem-store-client/1"
        assert seen.get("authorization") == "Bearer tok"

    def test_the_probe_body_CANNOT_be_written_by_the_real_server(self, srv, tmp_path):
        """🔴 THE SAFETY CLAIM, EXERCISED AGAINST THE REAL VALIDATOR rather than
        argued. `{}` must be refused by `_bullet_request_problem` — i.e. before
        any ref is resolved, any file opened or any bullet rendered — so the
        probe cannot write whatever ref or scope it is pointed at."""
        assert srv._bullet_request_problem({}) is not None
        assert srv._bullet_request_problem(json.loads("{}")) is not None


# =============================================================================
# The freeze
# =============================================================================


class TestTheFreezeIsWatchedNotAsserted:
    def test_probe_writable_reports_by_SYSCALL_and_moves_with_the_mode(
        self, cc, tmp_path
    ):
        """The positive control and the negative control of the freeze's only
        instrument, as a pair. A prober that always said `refused` would satisfy
        every downstream assertion in this file."""
        path = tmp_path / "e.md"
        path.write_text("x")
        assert cc.probe_writable(path) == "writable"
        path.chmod(0o444)
        assert cc.probe_writable(path) == "refused"
        path.chmod(0o644)
        assert cc.probe_writable(path) == "writable"

    def test_the_probe_MUTATES_NOTHING(self, cc, tmp_path):
        """It opens a curated entry for append. If it ever wrote a byte — or
        moved an mtime — it would be a mutation of client-confidential content
        with a crash window, run over every entry in the store."""
        path = tmp_path / "e.md"
        path.write_text("- 2026-01-01: content that must not move.\n")
        before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_size)
        assert cc.probe_writable(path) == "writable"
        after = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_size)
        assert before == after

    def test_survey_prints_EXAMINED_beside_every_count(self, cc, tmp_path):
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
        })
        assert cc.survey(root) == {"examined": 2, "writable": 2, "refused": 0, "other": 0}
        cc.set_entry_mode(root, 0o444)
        assert cc.survey(root) == {"examined": 2, "writable": 0, "refused": 2, "other": 0}

    def test_an_EMPTY_store_reports_examined_0_rather_than_a_clean_zero(self, cc, tmp_path):
        """🔴 THE REASSURING ZERO. `writable: 0` from a walk that visited nothing
        is byte-identical to a fully frozen store; only `examined` separates
        them, which is why every count is printed beside it."""
        empty = tmp_path / "empty"
        empty.mkdir()
        assert cc.survey(empty) == {"examined": 0, "writable": 0, "refused": 0, "other": 0}

    def test_the_freeze_leaves_SCOPE_DIRECTORIES_writable(self, cc, tmp_path):
        """A deliberate asymmetry with a known cost: the hosted API has no CREATE
        route, so freezing the directories too would leave a brand-new
        subsystem's first entry with nowhere to go at all."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        cc.set_entry_mode(root, 0o444)
        assert (root / "sc").stat().st_mode & 0o200, "the scope directory was frozen too"
        (root / "sc" / "brand-new.md").write_text("still possible")

    def test_set_entry_mode_is_IDEMPOTENT(self, cc, tmp_path):
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
        })
        assert cc.set_entry_mode(root, 0o444) == 2
        assert cc.set_entry_mode(root, 0o444) == 0


# =============================================================================
# End to end: the refusals stop the run, and DRY RUN changes nothing
# =============================================================================


class TestTheScriptRefusesRatherThanProceeds:
    def test_an_EMPTY_local_store_refuses_before_anything_else(self, cc, tmp_path):
        """A cutover that pushed an empty delta and then froze an empty store
        would report success having done nothing — and would then be believed."""
        empty = tmp_path / "empty"
        empty.mkdir()
        rc = cc.main(["--store", str(empty)])
        assert rc == cc.RC_NO_STORE

    def test_a_FAILED_backup_check_stops_before_the_store_is_even_read(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 ORDER MATTERS, NOT JUST OUTCOME. The backup gate must run before any
        network call and before any mode bit moves, so a run that cannot prove a
        backup exists has not touched anything at all."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
        before = {p: p.stat().st_mode for p in root.rglob("*.md")}
        rc = cc.main(["--store", str(root), "--run-dir", str(tmp_path / "run")])
        assert rc == cc.RC_BACKUP
        assert {p: p.stat().st_mode for p in root.rglob("*.md")} == before
        assert not (tmp_path / "run" / "delta").exists()

    def test_MAIN_propagates_the_PROBES_OWN_code_not_a_blanket_no_write_route(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE WIRING, NOT THE PROBE. `write_route_deployed` returns its own rc
        and the unit tests above pin it — but `main` used to discard that and
        answer every falsy result with `RC_NO_WRITE_ROUTE`, so an UNREACHABLE pod
        during P0 told the operator "the running image is read-only" and sent
        them to redeploy the store over a network blip. A sweep proved the unit
        tests blind to it: replacing `probe_rc or …` with the constant SURVIVED.

        So this drives `main` far enough to reach the probe, with a REAL dead
        port behind it, and asserts the code that comes out.
        """
        store = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        run_dir = tmp_path / "run"
        _tree(run_dir / "cache", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})

        import socket as _socket

        with _socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            dead = sock.getsockname()[1]

        monkeypatch.setattr(cc, "backup_precondition",
                            lambda **_kw: (True, "backup OK — stubbed"))
        # The only `run` on the path to the probe is `cairn sync`; the cache is
        # pre-populated above, so a no-op success is faithful.
        monkeypatch.setattr(cc, "run", lambda *_a, **_kw: cc.Ran(0, "live", ""))
        monkeypatch.setattr(cc, "_config", lambda: (f"http://127.0.0.1:{dead}", "tok"))

        rc = cc.main(["--store", str(store), "--run-dir", str(run_dir)])
        assert rc == cc.RC_COULD_NOT_MEASURE, (
            f"an unreachable pod during P0 exited {rc}; RC_NO_WRITE_ROUTE (12) "
            f"would send the operator to redeploy the store."
        )

    def test_UNFREEZE_RESTORES_the_recorded_modes_and_never_WIDENS_one(
        self, cc, tmp_path
    ):
        """🔴 A ROLLBACK RESTORES; IT DOES NOT NORMALISE.

        `--unfreeze` used to `chmod 0644` every entry unconditionally and call
        itself the rollback of the freeze. For a file that was **0600** — a
        plausible mode on a client-confidential entry, and the mode this effort's
        own staging file uses — that is a permission WIDENING on exactly the
        content the widening matters for, performed by the recovery path. So the
        freeze now records the modes first and the restore reads them back.

        The fixture makes the two behaviours distinguishable ON PURPOSE: one
        entry at 0600 and one at 0644. A restore that normalises passes a
        same-mode fixture and fails this one.
        """
        root = _tree(tmp_path / "s", {
            "sc/private.md": _entry("sc", "private", "- 2026-01-01: x."),
            "sc/ordinary.md": _entry("sc", "ordinary", "- 2026-01-01: y."),
        })
        (root / "sc" / "private.md").chmod(0o600)
        (root / "sc" / "ordinary.md").chmod(0o644)
        ledger = tmp_path / "run" / cc.MODE_LEDGER
        assert cc.save_modes(root, ledger) == 2
        assert ledger.stat().st_mode & 0o777 == 0o600, "the ledger itself is 0600"

        cc.set_entry_mode(root, 0o444)
        assert cc.survey(root)["refused"] == 2

        args = ["--store", str(root), "--unfreeze", "--mode-ledger", str(ledger)]
        assert cc.main(args) == cc.RC_OK
        assert cc.survey(root)["refused"] == 2, "a dry run changed the mode bits"

        assert cc.main([*args, "--apply"]) == cc.RC_OK
        assert cc.survey(root)["writable"] == 2
        assert (root / "sc" / "private.md").stat().st_mode & 0o777 == 0o600, (
            "the rollback WIDENED a 0600 entry — that is the defect it replaces"
        )
        assert (root / "sc" / "ordinary.md").stat().st_mode & 0o777 == 0o644

    def test_UNFREEZE_with_NO_LEDGER_refuses_rather_than_inventing_a_mode(
        self, cc, tmp_path
    ):
        """The other half: with nothing to restore TO, guessing 0644 is the very
        widening above. Refusing is the only honest answer."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        (root / "sc" / "a.md").chmod(0o600)
        cc.set_entry_mode(root, 0o444)
        rc = cc.main([
            "--store", str(root), "--unfreeze", "--apply",
            "--mode-ledger", str(tmp_path / "nope" / cc.MODE_LEDGER),
        ])
        assert rc == cc.RC_COULD_NOT_MEASURE
        assert cc.survey(root)["refused"] == 1, "it changed modes anyway"

    @pytest.mark.parametrize(
        "payload, needle",
        [
            ("{ not json", "does not parse"),
            ("", "does not parse"),
            ("[]", "not an object"),
            ('{"store": "/nowhere", "modes": {}}', "was taken against"),
            ('{"modes": {"sc/a.md": 420}}', "no `modes` object"),   # no store field
            ('{"store": "%STORE%", "modes": {"sc/a.md": "0600"}}', "is not a mode"),
            ('{"store": "%STORE%", "modes": {"sc/a.md": true}}', "is not a mode"),
            ('{"store": "%STORE%", "modes": []}', "no `modes` object"),
        ],
    )
    def test_an_UNUSABLE_ledger_is_a_NAMED_REFUSAL_not_a_traceback(
        self, cc, tmp_path, payload, needle
    ):
        """🔴 THE GUARD CHECKED `is_file()` AND PRINTED "no readable mode ledger".

        Existence is not readability, and the gap was not theoretical: truncated
        JSON, an empty file, a string mode and a top-level list each died on an
        UNCAUGHT exception at exit 1 — on the RECOVERY path, where the operator
        is least able to interpret a bare traceback. That is verbatim the
        condition the guard's own comment claimed to have closed, one step over.

        The `store` arm is the other half: a ledger records which store it came
        from, because `--unfreeze` picks the newest one under a SHARED root and
        rel paths collide readily between stores.
        """
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        (root / "sc" / "a.md").chmod(0o600)
        cc.set_entry_mode(root, 0o444)
        ledger = tmp_path / "run" / cc.MODE_LEDGER
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(payload.replace("%STORE%", str(root.resolve())))
        rc = cc.main([
            "--store", str(root), "--unfreeze", "--apply", "--mode-ledger", str(ledger),
        ])
        assert rc == cc.RC_COULD_NOT_MEASURE, rc
        # The store must be untouched — a refusal that half-applied would be worse
        # than the traceback it replaces.
        assert (root / "sc" / "a.md").stat().st_mode & 0o777 == 0o444

    def test_a_ledger_from_ANOTHER_STORE_is_refused_even_when_the_paths_line_up(
        self, cc, tmp_path
    ):
        """The reason the ledger names its store. Two stores with the SAME rel
        paths — the common case, since every store uses `<scope>/<entry>.md` —
        and the modes recorded in one must not be applied to the other."""
        a = _tree(tmp_path / "a", {"sc/x.md": _entry("sc", "x", "- 2026-01-01: a.")})
        b = _tree(tmp_path / "b", {"sc/x.md": _entry("sc", "x", "- 2026-01-01: b.")})
        (a / "sc" / "x.md").chmod(0o600)
        (b / "sc" / "x.md").chmod(0o640)
        ledger = tmp_path / "run" / cc.MODE_LEDGER
        cc.save_modes(a, ledger)
        cc.set_entry_mode(b, 0o444)
        rc = cc.main([
            "--store", str(b), "--unfreeze", "--apply", "--mode-ledger", str(ledger),
        ])
        assert rc == cc.RC_COULD_NOT_MEASURE
        assert (b / "sc" / "x.md").stat().st_mode & 0o777 == 0o444, (
            "store B was restored to store A's recorded modes"
        )

    def test_an_entry_created_AFTER_the_freeze_is_LEFT_ALONE_not_guessed_at(
        self, cc, tmp_path
    ):
        """Scope directories stay writable, so a new entry CAN appear between the
        freeze and the unfreeze. It has no recorded mode; inventing one is the
        defect. It is counted, reported, and untouched."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry("sc", "a", "- 2026-01-01: x.")})
        ledger = tmp_path / "run" / cc.MODE_LEDGER
        cc.save_modes(root, ledger)
        cc.set_entry_mode(root, 0o444)
        newcomer = root / "sc" / "later.md"
        newcomer.write_text(_entry("sc", "later", "- 2026-01-02: z."))
        newcomer.chmod(0o640)
        rc = cc.main([
            "--store", str(root), "--unfreeze", "--apply", "--mode-ledger", str(ledger),
        ])
        assert rc == cc.RC_COULD_NOT_MEASURE
        assert newcomer.stat().st_mode & 0o777 == 0o640
        assert (root / "sc" / "a.md").stat().st_mode & 0o777 == 0o644

    def test_FREEZE_alone_is_dry_run_by_default_then_takes_and_is_idempotent(
        self, cc, tmp_path
    ):
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
        })
        assert cc.main(["--store", str(root), "--run-dir", str(tmp_path / "run"), "--freeze"]) == cc.RC_OK
        assert cc.survey(root)["writable"] == 2, "a dry run froze the store"
        assert cc.main(["--store", str(root), "--run-dir", str(tmp_path / "run"), "--freeze", "--apply"]) == cc.RC_OK
        assert cc.survey(root) == {"examined": 2, "writable": 0, "refused": 2, "other": 0}
        # The idempotent re-run: already frozen, still exit 0, nothing changed.
        assert cc.main(["--store", str(root), "--run-dir", str(tmp_path / "run"), "--freeze", "--apply"]) == cc.RC_OK
        assert cc.survey(root)["refused"] == 2

    def test_a_FREEZE_over_an_empty_store_is_COULD_NOT_MEASURE_not_success(
        self, cc, tmp_path
    ):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert cc.main(["--store", str(empty), "--run-dir", str(tmp_path / "run"), "--freeze", "--apply"]) == cc.RC_COULD_NOT_MEASURE

    def test_a_PARTIAL_freeze_FAILS_and_the_mode_bits_are_ROLLED_BACK(
        self, cc, tmp_path, monkeypatch
    ):
        """🔴 THE ONLY TEST THAT REACHES THE VERIFICATION ITSELF.

        Every other freeze test exercises a chmod that WORKS, so the check
        `refused == examined` is satisfied by the happy path and its removal
        changes nothing — a mutation sweep scored exactly that, which is why this
        test exists. Reaching it needs a freeze that is applied and does not take,
        so `set_entry_mode` is replaced by one that freezes all but one file. That
        is not a contrived state: an ACL, an overlay mount, a file owned by
        another user or a `chmod` that raced a writer all produce it, and the
        consequence is a store that LOOKS frozen while one entry still accepts a
        write that will die at the next seed.

        Two assertions, and the second is the point: the specific exit code
        (RC_FREEZE_INEFFECTIVE, not merely non-zero), and that the mode bits were
        RESTORED rather than left half-applied.
        """
        root = _tree(tmp_path / "s", {
            "sc/a.md": _entry("sc", "a", "- 2026-01-01: x."),
            "sc/b.md": _entry("sc", "b", "- 2026-01-01: y."),
            "sc/c.md": _entry("sc", "c", "- 2026-01-01: z."),
        })
        real = cc.set_entry_mode

        def leaky(store, mode):
            if mode != 0o444:
                return real(store, mode)
            changed = 0
            for rel in sorted(cc.read_store(store))[1:]:   # skips exactly one
                (store / rel).chmod(mode)
                changed += 1
            return changed

        monkeypatch.setattr(cc, "set_entry_mode", leaky)
        rc = cc.main(["--store", str(root), "--run-dir", str(tmp_path / "run"), "--freeze", "--apply"])
        assert rc == cc.RC_FREEZE_INEFFECTIVE, rc
        after = cc.survey(root)
        assert after == {"examined": 3, "writable": 3, "refused": 0, "other": 0}, (
            f"the failed freeze was left half-applied: {after}. A store that looks "
            f"frozen and is not is worse than one that plainly is not."
        )

    def test_ROLLBACK_PUSH_over_an_empty_pre_push_dir_refuses(self, cc, tmp_path):
        """A rollback that restores nothing must not report success — that is the
        reassuring zero wearing a remediation's clothes."""
        run_dir = tmp_path / "run"
        (run_dir / "pre-push").mkdir(parents=True)
        assert cc.main(["--rollback-push", str(run_dir)]) == cc.RC_USAGE
        assert cc.main(["--rollback-push", str(tmp_path / "nope")]) == cc.RC_USAGE

    def test_the_MANIFEST_carries_no_bullet_text(self, cc, tmp_path, capsys):
        """The peer manifest crosses machines. It answers "does the other host
        hold this, and is it the same bytes?", which a sha256 answers — so
        shipping the prose would move client-confidential content over a channel
        that does not need it, for no gain."""
        root = _tree(tmp_path / "s", {"sc/a.md": _entry(
            "sc", "a", "- 2026-01-01: a distinctive sentence nobody else writes.")})
        assert cc.main(["--store", str(root), "--manifest"]) == cc.RC_OK
        out = capsys.readouterr().out
        assert "a distinctive sentence nobody else writes" not in out
        payload = json.loads(out)
        assert set(payload) == {"sc/a.md"}
        assert set(payload["sc/a.md"]) == {"sha256", "aliases"}


class TestTheDeltaTree:
    def test_materialise_copies_ONLY_the_shippable_entries(self, cc, tmp_path):
        text = _entry("sc", "same", "- 2026-01-01: unchanged.")
        local = _tree(tmp_path / "l", {
            "sc/same.md": text,
            "sc/new.md": _entry("sc", "new", "- 2026-01-02: brand new."),
        })
        pod = _tree(tmp_path / "p", {"sc/same.md": text})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        dest = tmp_path / "delta"
        cc._materialise(plan, dest)
        assert sorted(str(p.relative_to(dest)) for p in dest.rglob("*.md")) == ["sc/new.md"]

    def test_materialise_REBUILDS_the_tree_so_a_stale_run_cannot_leak_in(
        self, cc, tmp_path
    ):
        """`seed.sh` rsyncs SOURCE->STAGE with `--delete`, so anything left in
        the delta tree from an earlier plan is pushed by a later one."""
        dest = tmp_path / "delta"
        _tree(dest, {"sc/left-over.md": "from an earlier run"})
        local = _tree(tmp_path / "l", {"sc/new.md": _entry("sc", "new", "- 2026-01-02: x.")})
        plan = cc.plan_delta(cc.read_store(local), {}, store_root=local, merged_dir=None)
        cc._materialise(plan, dest)
        assert not (dest / "sc" / "left-over.md").exists()

    def test_save_prepush_keeps_the_SERVED_bytes_of_every_OVERWRITE_and_no_ADD(
        self, cc, tmp_path
    ):
        """🔴 THIS IS PHASE 3's ROLLBACK, AND IT MUST BE TAKEN BEFORE THE PUSH.
        An ADD has no pre-image (and the API has no delete verb, so it could not
        be rolled back anyway); an overwrite replaces bytes that afterwards exist
        only in the daily backup.

        ⚠ WHAT THIS TEST CANNOT DISTINGUISH, said rather than left to be found:
        the ADD is skipped because the served copy HAS NO SUCH FILE, which is the
        same fact the verdict `ADD` records. A sweep proved the two inseparable —
        the redundant verdict check was deleted for that reason — so this pins
        the OUTCOME (only overwrites are saved) and makes no claim about which
        expression produced it.
        """
        local = _tree(tmp_path / "l", {
            "sc/changed.md": _entry("sc", "changed", "- 2026-02-09: the host's newer line."),
            "sc/added.md": _entry("sc", "added", "- 2026-02-10: brand new."),
        })
        pod = _tree(tmp_path / "p", {
            "sc/changed.md": _entry("sc", "changed", "- 2026-02-01: the served copy.")})
        plan = cc.plan_delta(cc.read_store(local), cc.read_store(pod),
                             store_root=local, merged_dir=None)
        dest = tmp_path / "pre-push"
        cc._save_prepush(plan, pod, dest)
        saved = sorted(str(p.relative_to(dest)) for p in dest.rglob("*.md"))
        assert saved == ["sc/changed.md"]
        assert "the served copy" in (dest / "sc" / "changed.md").read_text()


class TestRunPreservesPartialOutputOnTimeout:
    """🔴 A TIMED-OUT CHILD'S STDOUT IS EVIDENCE, NOT DEBRIS.

    `run` used to return `Ran(124, "", …)`, so `_acceptance`'s refusal — "Read
    its per-scope FAIL lines above" — pointed at output the same function had
    just discarded. The acceptance sweep is now N+1 requests per scope, which
    makes the 600s timeout genuinely reachable and makes the partial capture
    the only record of which scopes DID complete.
    """

    def test_a_TIMEOUT_keeps_what_the_child_had_already_printed(self, cc):
        r = cc.run(
            ["bash", "-c", "echo PASS scope=one; echo PASS scope=two; sleep 30"],
            timeout=2,
        )
        assert r.rc == 124, f"expected the timeout code, got {r.rc}"
        # 🔴 THE LOAD-BEARING ASSERTION. Pre-change this was `""`.
        assert "PASS scope=one" in r.out and "PASS scope=two" in r.out, (
            f"the timed-out child's stdout was discarded: {r.out!r}"
        )
        assert "timed out after 2s" in r.err

    def test_the_partial_capture_is_a_STR_not_the_BYTES_python_hands_back(self, cc):
        """⚠ MEASURED ON CPython 3.12.14: `TimeoutExpired.stdout` is BYTES even
        under `text=True`, and `.stderr` came back None.

        Passing either through unchanged is not a cosmetic wart — `_acceptance`
        does `sys.stdout.write(verified.out)`, and a `bytes` there raises
        `TypeError`, turning a timeout into a traceback. So this asserts the
        TYPE, which is the thing the caller depends on, and then exercises the
        caller's actual operation.
        """
        r = cc.run(["bash", "-c", "echo something; sleep 30"], timeout=2)
        # 🔴 THE CONTENT CHECK COMES FIRST, AND IT IS WHAT KEEPS THE TYPE CHECK
        # FROM BEING VACUOUS. `""` is a `str`, so the assertions below are
        # satisfied by the pre-change code that discarded the capture entirely
        # — they say something only about a capture that actually happened.
        assert r.out.strip(), (
            f"nothing was captured, so the type assertions below would pass "
            f"against a `run` that threw the output away: {r.out!r}"
        )
        assert isinstance(r.out, str), f"partial stdout is {type(r.out).__name__}"
        assert isinstance(r.err, str), f"partial stderr is {type(r.err).__name__}"
        # The operation `_acceptance` performs on it, run for real.
        io.StringIO().write(r.out)

    def test_a_child_that_printed_NOTHING_before_timing_out_SAYS_SO(self, cc):
        """🔴 THE EMPTY CASE IS REPORTED, NOT LEFT AS AN EMPTY STRING.

        `claude/RULES.md`: an empty result cannot distinguish two mechanisms.
        "No FAIL lines" reads identically whether the sweep found nothing or
        never got started, so the timeout note says which one this was.
        """
        r = cc.run(["bash", "-c", "sleep 30"], timeout=2)
        assert r.rc == 124
        assert r.out == ""
        assert "printed NOTHING to stdout" in r.err, (
            f"an empty partial capture was not distinguished from a clean one: "
            f"{r.err!r}"
        )


class TestAcceptanceRefusalNamesWhatItActuallyHas:
    """🔴 THE REFUSAL TEXT, WHICH HAD NO TEST AT ALL UNTIL NOW.

    `_acceptance`'s refusal is the last thing an operator reads before deciding
    what a failed cutover means, and it selects between "read the FAIL lines"
    and "there are none". Nothing outside `cairn-cutover.py` mentioned
    `printed NO per-scope lines`, and there was no `RC_ACCEPTANCE` test in this
    file — so the branch was shipped on reasoning alone, twice, and was wrong
    the first time.

    The three cases need three different next actions, so they are three tests.
    """

    def test_FAIL_lines_present_points_at_them_AND_COUNTS_THEM(self, cc):
        r = cc.Ran(
            1,
            "PASS scope=one entries=2\n"
            "FAIL scope=two entry SET differs local-only=1 pod-only=0\n"
            "FAIL scope=three ref=x entry bytes differ\n"
            "verify: scopes=3 pass=1 fail=2\n",
            "",
        )
        msg = cc._acceptance_refusal(r)
        assert "2 per-scope FAIL line(s) above" in msg, msg
        assert "NO `FAIL scope=` LINE" not in msg
        assert "The store was NOT frozen." in msg

    def test_OUTPUT_but_NO_FAIL_lines_says_the_sweep_STOPPED_not_that_it_FAILED(
        self, cc
    ):
        """🔴 THE SHAPE THE FIRST VERSION OF THIS BRANCH GOT WRONG.

        8 of 16 scopes compared clean, then the pod hangs and the 600s timeout
        fires. There is plenty of stdout and not one FAIL line — so the old
        `if verified.out.strip()` test selected "Read its per-scope FAIL lines
        above" over zero of them.

        The distinction is not cosmetic: "a scope differs" and "the sweep did
        not finish" have opposite next actions. One means the push was lossy;
        the other means re-run it.
        """
        out = "".join(f"PASS scope=s{i} entries=1 bytes=10\n" for i in range(8))
        msg = cc._acceptance_refusal(cc.Ran(124, out, "timed out after 600s"))
        assert "NO `FAIL scope=` LINE AT ALL" in msg, msg
        assert "8 scope(s) compared clean" in msg, msg
        assert "timed out" in msg, msg
        assert "UNCOMPARED" in msg, msg
        # 🔴 AND IT MUST NOT TELL THEM TO READ FAIL LINES THAT DO NOT EXIST.
        assert "Read its" not in msg, msg
        assert "Do not read this as a byte-identity failure." in msg

    def test_NO_output_at_all_says_it_never_got_started(self, cc):
        msg = cc._acceptance_refusal(cc.Ran(124, "", "timed out after 600s"))
        assert "NO per-scope lines at all" in msg, msg
        assert "cut short" in msg and "timed out" in msg, msg
        assert "Read its" not in msg, msg

    def test_a_NON_timeout_failure_does_not_claim_a_timeout(self, cc):
        """The `(timed out)` clause is gated on rc 124, not glued on.

        A verifier that exited 1 having printed nothing is a different fault
        from one that was killed, and saying "timed out" about it would send
        the operator to look at the pod instead of at the script.
        """
        msg = cc._acceptance_refusal(cc.Ran(1, "", ""))
        assert "timed out" not in msg, msg
        assert "NO per-scope lines at all" in msg, msg

    def test_the_refusal_is_what_ACCEPTANCE_actually_returns(self, cc, monkeypatch):
        """🔴 THE SEAM. The three tests above exercise the text in isolation;
        this proves `_acceptance` reaches it and returns `RC_ACCEPTANCE`.

        Without this they are a claim about a helper nobody calls — the
        "verified in isolation" shape. The verifier is stubbed to the
        completed-then-stopped case, which is the one that was mis-worded.
        """
        out = "PASS scope=one entries=1 bytes=10\n"

        def fake_run(cmd, *, timeout=120, cwd=None):
            if "verify-byte-identity.sh" in " ".join(str(c) for c in cmd):
                return cc.Ran(124, out, "timed out after 600s")
            return cc.Ran(0, "", "")

        monkeypatch.setattr(cc, "run", fake_run)
        monkeypatch.setattr(cc, "read_store", lambda root: {})
        monkeypatch.setattr(cc, "_config", lambda: ("http://127.0.0.1:1", "t"))
        monkeypatch.setenv("SUBSYSTEM_STORE_TOKEN_FILE", "/nonexistent-token")

        args = argparse.Namespace(store=Path("/nonexistent-store"))
        rc = cc._acceptance(args, Path("/nonexistent-cache"))
        assert rc == cc.RC_ACCEPTANCE, f"expected RC_ACCEPTANCE, got {rc}"
