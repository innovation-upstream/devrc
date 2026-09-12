"""The `policy:` line: ONE rule, two documents, two probes — pinned to agree.

🔴 WHY THIS FILE EXISTS (devrc#1170 🟡5). The index store has one shared write
protocol, `claude/skills/subsystem-index/SKILL.md`, and its write half told every
caller: *"Read the policy file the probe named on its `policy:` line"*. That was
satisfiable for `/handoff`, whose probe is `subsystem_touch.py` and has printed
the line since 2026-08-13. It was **not** satisfiable for `/analyze-service`,
whose probe is `service_recon.py` — measured 2026-09-10 and again on 2026-09-11,
**0** occurrences of `policy:` in that file. Meanwhile that caller's own
reference, `claude/skills/analyze-service/reference/index-store.md`, sent it to
*"each scope's own README.md"* instead — the instruction measured unfollowable in
4 of 5 scopes on 2026-08-13, and the very one the SKILL.md sentence replaced.

So: one rule, stated two ways, and the way that was stated twice was the wrong
one. `claude/RULES.md` → "One rule, one place": a predicate open-coded at N sites
is typically wrong at N−1 of them, and consolidating is what makes the
disagreement audible.

🔴 THE GUARD IS A RELATIONSHIP, NOT A WORD. Two halves, and neither alone would
have caught this:

  * the DOC half compares the two documents' copies of the sentence to each
    other, whole and normalised — a reword of one alone goes red naming both
    files. Pinning a keyword instead would be walkable by rewording, which is
    exactly how one of two copies goes stale.
  * the CODE half runs BOTH probes over ONE synthetic store and asserts they
    print the same path and the same basis. A doc pin alone would stay green
    against a probe that prints nothing, which is the state this closes.

🔴 EVERY FIXTURE IS SYNTHETIC. The real store is client-confidential and devrc is
PUBLIC: every store here is built under `tmp_path`, and the scope/service words
are invented and pairwise distinct from each other and from every constant
asserted against.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import service_recon as sr  # noqa: E402
import subsystem_touch as st  # noqa: E402

SKILL_DOC = ROOT / "claude" / "skills" / "subsystem-index" / "SKILL.md"
REFERENCE_DOC = (
    ROOT / "claude" / "skills" / "analyze-service" / "reference" / "index-store.md"
)

#: The sentence both documents must carry, word for word. It is spelled ONCE
#: here; the test reads it out of each file and compares the two FILES, so this
#: constant is the locator, and the assertion that matters is doc-vs-doc.
SENTENCE_HEAD = "Read the policy file the probe named on its `policy:` line"

#: The instruction this replaced. It must not come back in either document: it is
#: the one measured unfollowable in 4 of 5 scopes, and a new scope starts without
#: a README by construction, so the gap regrows on its own.
SUPERSEDED = "Each scope's own `README.md` states the policy governing it"

#: Scope/service words used below. No word is another's prefix, none is a
#: substring of any `POLICY_*` constant, and none repeats across roles — a
#: wrong-field read therefore produces nothing rather than a plausible answer.
SCOPE = "tallow-repo"
OTHER_SCOPE = "brine-repo"
SERVICE = "kestrel"


def _sentence_from(doc: Path) -> str:
    """The whole sentence, normalised — never a keyword.

    Whole-string, because `claude/RULES.md` says a guard on WORDS over prose is
    walkable by REWORDING. Normalised on whitespace only, so the two documents
    may wrap differently (one is a bullet, the other is mid-paragraph) while
    still being held to the same words.
    """
    text = doc.read_text(encoding="utf-8")
    start = text.find(SENTENCE_HEAD)
    assert start != -1, (
        f"{doc} no longer carries the shared write-half sentence (looked for "
        f"{SENTENCE_HEAD!r}). It is the instruction that routes a writer to the "
        f"governing policy file; if it moved, move BOTH copies and this locator."
    )
    end = text.find("**", start)
    assert end != -1, f"{doc}: the sentence is not bold-terminated; cannot delimit it"
    return re.sub(r"\s+", " ", text[start:end]).strip()


class TestTheTwoDocumentsAgree:
    def test_both_carry_the_sentence_and_it_is_the_SAME_sentence(self) -> None:
        """🔴 The whole point. Two copies of one rule, compared to each other."""
        skill = _sentence_from(SKILL_DOC)
        reference = _sentence_from(REFERENCE_DOC)
        assert skill == reference, (
            "the shared write-half instruction has drifted between the two "
            f"documents.\n  {SKILL_DOC}:\n    {skill!r}\n  {REFERENCE_DOC}:\n"
            f"    {reference!r}\nThey state ONE rule; edit both in one commit."
        )

    def test_the_sentence_is_not_a_stub(self) -> None:
        """A locator that matched an empty remainder would make the equality
        above vacuous — two empty strings agree perfectly."""
        assert len(_sentence_from(SKILL_DOC)) > len(SENTENCE_HEAD) + 20

    def test_the_superseded_scope_README_instruction_is_in_NEITHER(self) -> None:
        for doc in (SKILL_DOC, REFERENCE_DOC):
            assert SUPERSEDED not in doc.read_text(encoding="utf-8"), (
                f"{doc} has regrown the instruction measured unfollowable in 4 of "
                f"5 scopes. The probe resolves the governing file deterministically; "
                f"send the writer there instead."
            )


class TestBothProbesPrintTheLine:
    """The CODE half — the claim the sentence makes about the tooling."""

    @pytest.fixture
    def store(self, tmp_path: Path) -> Path:
        """A store whose SCOPE has its own README and whose OTHER_SCOPE does not."""
        s = tmp_path / "store"
        (s / SCOPE).mkdir(parents=True)
        (s / OTHER_SCOPE).mkdir(parents=True)
        (s / "README.md").write_text("store-wide policy\n", encoding="utf-8")
        (s / SCOPE / "README.md").write_text("this scope's policy\n", encoding="utf-8")
        (s / SCOPE / f"{SERVICE}.md").write_text(
            f"---\nservice: {SERVICE}\nscope: {SCOPE}\nsensitivity: public\n---\n\n"
            "## What it is\nA synthetic subsystem.\n\n"
            "## Pointers\n- nothing\n\n"
            "## Nuance / work-history\n- 2026-01-02 a note\n",
            encoding="utf-8",
        )
        return s

    def test_service_recon_prints_a_policy_line_AT_ALL(self, store: Path) -> None:
        """🔴 RED AT BASE. Measured 0 occurrences of `policy:` in
        `scripts/lib/service_recon.py` on 2026-09-10 and 2026-09-11."""
        brief = sr.recon(
            SERVICE, repos=[], store_root=store, env={}, cwd=None,
        )
        idx = sr.with_policy(
            sr._dc_replace(brief.index, scope=SCOPE), store
        )
        text = sr.render_brief(sr._dc_replace(brief, index=idx))
        assert "\npolicy: " in text, (
            "the /analyze-service probe names no policy file, so the shared write "
            "half's instruction is unsatisfiable for its callers"
        )

    @pytest.mark.parametrize("scope,expected_basis", [
        (SCOPE, st.POLICY_SCOPE),
        (OTHER_SCOPE, st.POLICY_STORE_ROOT),
    ])
    def test_the_two_probes_name_the_SAME_file_and_basis(
        self, store: Path, scope: str, expected_basis: str
    ) -> None:
        """🔴 THE SEAM GUARD. Both probes, one store, one scope — compared to each
        OTHER, not each to a literal. Two components each correct in isolation can
        still disagree, and that disagreement is the defect a caller meets.

        Both bases are exercised, so a resolver that only ever returned one would
        fail here even though every individual assertion would still "pass"
        somewhere.
        """
        touch_path, touch_basis = st.governing_policy(store, scope)
        recon_index = sr.with_policy(sr.IndexResult("hit", scope=scope), store)
        assert (recon_index.policy_path, recon_index.policy_basis) == (
            touch_path, touch_basis
        ), "the two probes disagree about which file governs one scope"
        assert touch_basis == expected_basis

    def test_the_rendered_payloads_are_byte_identical(self, store: Path) -> None:
        """Not just the fields — the LINE. A writer reads the rendered brief, so
        a renderer that dropped or reworded the payload would leave the fields
        agreeing and the instruction still unfollowable."""
        report = st.build_report(
            st.PathSource(kind="caller", window="supplied", paths=()),
            store,
            SCOPE,
            today="2026-09-11",
        )
        touch_line = next(
            ln.strip() for ln in st.render_text(report).splitlines()
            if ln.strip().startswith("policy: ")
        )
        idx = sr.with_policy(sr.IndexResult("hit", scope=SCOPE), store)
        recon_line = (
            f"policy: {idx.policy_path or '(none)'}  ({idx.policy_basis})"
        )
        assert recon_line == touch_line


class TestTheFourthBasisIsItsOwnClaim:
    """🔴 `NOT RESOLVED` is not a spelling of `NONE`.

    `NONE` is a measurement — two paths stat'd, neither exists. `NOT RESOLVED`
    means no scope was reached, so nothing was asked. Folding them would let a
    run that never looked report what a run that looked and found nothing
    reports, which is the empty-result confusion `claude/RULES.md` names.
    """

    def test_a_result_with_no_scope_says_NOT_RESOLVED(self, tmp_path: Path) -> None:
        got = sr.with_policy(sr.IndexResult(sr.INDEX_UNSTAMPED), tmp_path)
        assert got.policy_path is None
        assert got.policy_basis == sr.POLICY_NO_SCOPE

    def test_a_scope_over_an_EMPTY_store_says_NONE_instead(self, tmp_path: Path) -> None:
        """The discriminating control: same absence of a README, different cause,
        different word. Without this pair the test above would pass against a
        function that returned `NOT RESOLVED` unconditionally."""
        got = sr.with_policy(sr.IndexResult("hit", scope=SCOPE), tmp_path)
        assert got.policy_path is None
        assert got.policy_basis == st.POLICY_NONE

    def test_the_four_bases_share_no_spelling(self) -> None:
        bases = (st.POLICY_SCOPE, st.POLICY_STORE_ROOT, st.POLICY_NONE,
                 sr.POLICY_NO_SCOPE)
        assert len(set(bases)) == 4
        for a in bases:
            for b in bases:
                if a != b:
                    assert a not in b, f"{a!r} is a substring of {b!r}"
