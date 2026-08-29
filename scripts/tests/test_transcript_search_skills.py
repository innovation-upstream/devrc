#!/usr/bin/env python3
"""Tests for the `--skill` search predicate (`transcript_search.session_used_skill`).

WHY IT EXISTS. "Which sessions used skill X?" had no answer but a keyword search,
and a keyword search cannot tell a skill INVOCATION from the word appearing in
prose or in a path. Measured 2026-08-29 on the live corpus: `find-session.py
signal` returned **666** sessions, nearly all of them `scripts/signal/tests/...`
in test output; `--skill signal` returns **1** — the session that actually used
it. A whole investigation reached the wrong conclusion inside that gap.

🔴 THE HAZARD THIS FILE GUARDS IS A *SPELLED* PREDICATE. A substring or
case-loose match would quietly turn `--skill` back into the keyword search it
replaces, and would still pass a naive "it found the right session" test. The
tests below therefore assert the predicate REJECTS: a prose mention, a path
fragment, and a prefix of the real name.

Kept in its own file rather than added to `test_transcript_search.py` because
that module pins its own two red-ledgers against its test-function list.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "lib"))

import transcript_search as ts  # noqa: E402


def _user(text, **kw):
    d = {"type": "user", "timestamp": "2026-08-21T10:00:00.000Z",
         "cwd": "/srv/repo", "message": {"content": text}}
    d.update(kw)
    return json.dumps(d)


def _assistant(text="ok", *, skill=None, **kw):
    d = {"type": "assistant", "timestamp": "2026-08-21T10:01:00.000Z",
         "cwd": "/srv/repo",
         "message": {"content": [{"type": "text", "text": text}]}}
    if skill is not None:
        d["attributionSkill"] = skill
    d.update(kw)
    return json.dumps(d)


def _write(root, session_id, lines, project="-srv-repo"):
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def _ids(results):
    return sorted(r["session_id"] for r in results)


def _load_tailer():
    """Load the emitter's copy of the rule WITHOUT leaking import state.

    🔴 The previous revision left `scripts/collector` and
    `scripts/collector/claude` at the FRONT of `sys.path` and `st_for_agree` in
    `sys.modules` for the rest of the pytest process — so a later test doing a
    bare `import tailer|collector|emit|...` would resolve against the collector
    copy, order-dependently. Restored in `finally`.
    """
    import importlib.util
    tailer_path = (Path(__file__).resolve().parents[1]
                   / "collector" / "claude" / "session-tailer.py")
    saved_path, saved_mod = list(sys.path), sys.modules.get("st_for_agree")
    try:
        sys.path.insert(0, str(tailer_path.parent))
        sys.path.insert(0, str(tailer_path.parent.parent))
        spec = importlib.util.spec_from_file_location("st_for_agree", tailer_path)
        st = importlib.util.module_from_spec(spec)
        sys.modules["st_for_agree"] = st
        spec.loader.exec_module(st)
        return st
    finally:
        sys.path[:] = saved_path
        if saved_mod is None:
            sys.modules.pop("st_for_agree", None)
        else:
            sys.modules["st_for_agree"] = saved_mod


# --------------------------------------------------------------------------- #
# Positive control
# --------------------------------------------------------------------------- #
class TestPositiveControl:
    """A predicate that matches nothing returns the same reassuring empty list as
    a skill that was genuinely never used. Prove the number can move first."""

    def test_a_session_that_used_the_skill_IS_returned(self, tmp_path):
        _write(tmp_path, "used", [_user("hi"), _assistant(skill="signal")])
        assert _ids(ts.search([], root=tmp_path, skill="signal")) == ["used"]

    def test_a_session_that_did_NOT_is_excluded(self, tmp_path):
        _write(tmp_path, "used", [_user("hi"), _assistant(skill="signal")])
        _write(tmp_path, "other", [_user("hi"), _assistant(skill="browser")])
        assert _ids(ts.search([], root=tmp_path, skill="signal")) == ["used"]


# --------------------------------------------------------------------------- #
# The spelled-predicate hazard
# --------------------------------------------------------------------------- #
class TestThePredicateIsStructuralNotSpelled:
    """🔴 Each of these three passes trivially under a substring match, which is
    exactly the implementation someone would reach for."""

    def test_a_session_that_merely_MENTIONS_the_word_is_not_a_use(self, tmp_path):
        """The 666-vs-1 case, in miniature."""
        _write(tmp_path, "prose", [
            _user("the signal chat pipeline is down, what do you think?"),
            _assistant("signal signal signal — no skill was ever loaded here"),
        ])
        assert ts.search([], root=tmp_path, skill="signal") == []

    def test_a_PATH_containing_the_name_is_not_a_use(self, tmp_path):
        """`scripts/signal/tests/...` in test output is what dominated the real
        keyword search's 666 hits."""
        _write(tmp_path, "paths", [
            _user("run the suite"),
            _assistant("PASSED scripts/signal/tests/test_approval_gate.py :: 508/508"),
        ])
        assert ts.search([], root=tmp_path, skill="signal") == []

    def test_a_PREFIX_of_the_skill_name_does_not_match_it(self, tmp_path):
        _write(tmp_path, "used", [_user("hi"), _assistant(skill="signal")])
        assert ts.search([], root=tmp_path, skill="sig") == []

    def test_a_LONGER_name_starting_with_it_does_not_match_either(self, tmp_path):
        _write(tmp_path, "sibling", [_user("hi"), _assistant(skill="signal-extra")])
        assert ts.search([], root=tmp_path, skill="signal") == []


# --------------------------------------------------------------------------- #
# Both routes into a skill
# --------------------------------------------------------------------------- #
class TestBothInvocationRoutes:
    """Neither signal is a superset of the other — see the measurement in
    session-tailer.py's `skills_used` comment. The predicate ORs them, so a
    regression in either route is a real loss of coverage."""

    def test_an_AUTO_FIRED_skill_is_found_with_no_command_ever_typed(self, tmp_path):
        _write(tmp_path, "auto", [
            _user("look at the page I have open"),   # no slash command anywhere
            _assistant(skill="browser"),
        ])
        assert _ids(ts.search([], root=tmp_path, skill="browser")) == ["auto"]

    def test_a_TYPED_command_is_found_with_no_attributed_record(self, tmp_path):
        _write(tmp_path, "typed", [
            _user("<command-name>/clawgate</command-name>"
                  "<command-args>status</command-args>"),
            _assistant("here is the status"),        # no attributionSkill
        ])
        assert _ids(ts.search([], root=tmp_path, skill="clawgate")) == ["typed"]

    def test_the_leading_slash_is_accepted_on_the_QUERY_too(self, tmp_path):
        """`--skill /signal` is what a human types after reading `/signal`
        somewhere. Rejecting it would be a silent empty result."""
        _write(tmp_path, "used", [_user("hi"), _assistant(skill="signal")])
        assert _ids(ts.search([], root=tmp_path, skill="/signal")) == ["used"]


# --------------------------------------------------------------------------- #
# Composition with terms
# --------------------------------------------------------------------------- #
class TestCompositionWithTerms:
    def test_skill_NARROWS_the_term_search_it_does_not_widen_it(self, tmp_path):
        _write(tmp_path, "both", [_user("harbour permit"), _assistant(skill="signal")])
        _write(tmp_path, "skill_only", [_user("something else"),
                                        _assistant(skill="signal")])
        _write(tmp_path, "term_only", [_user("harbour permit"), _assistant()])
        got = _ids(ts.search(["harbour permit"], root=tmp_path, skill="signal"))
        assert got == ["both"], (
            "a session matching only ONE of the two conditions was returned — "
            "the skill predicate must AND with the terms, never OR")

    def test_a_skill_only_query_works_under_match_any(self, tmp_path):
        """🔴 `match_any` over an EMPTY term list is False, not vacuously true,
        so `--skill X --any` returned NOTHING before this was handled — an empty
        result indistinguishable from 'the skill was never used'."""
        _write(tmp_path, "used", [_user("hi"), _assistant(skill="signal")])
        assert _ids(ts.search([], root=tmp_path, skill="signal",
                              match_any=True)) == ["used"]


# --------------------------------------------------------------------------- #
# The empty-query guard still holds
# --------------------------------------------------------------------------- #
class TestTheCorpusWideGuardStillHolds:
    def test_no_terms_and_no_skill_still_raises(self, tmp_path):
        """Relaxing the guard for `skill` must not open the corpus-wide path it
        was written to close."""
        with pytest.raises(ValueError):
            ts.search([], root=tmp_path)

    def test_an_empty_skill_string_is_not_a_query(self, tmp_path):
        with pytest.raises(ValueError):
            ts.search([], root=tmp_path, skill="   ")

    def test_a_BARE_SLASH_is_not_a_query_either(self, tmp_path):
        """🔴 Two normalisations that disagreed: `search()` stripped whitespace
        while the predicate ALSO stripped a leading `/`. So `--skill /` was
        truthy, slipped this guard, normalised to "" inside the predicate, and
        failed every session — a corpus-wide SILENT ZERO at exit 0. Same silent
        zero, sibling input. One rule, one place."""
        _write(tmp_path, "used", [_user("hi"), _assistant(skill="signal")])
        with pytest.raises(ValueError):
            ts.search([], root=tmp_path, skill="/")


class TestTheBoundAppliesToALLTHREERoutes:
    """🔴 A bound on ONE of three routes is not a bound. An earlier revision
    applied it to the typed route only, so the emitter and this module still
    disagreed: `--skill "not a valid name"` matched a session ClickHouse
    reported as having used no such skill.

    🔴 These are also the ONLY behavioural tests of the bound on this side.
    Without them, `SKILL_NAME_RE = re.compile(r".*")` — a mutant that makes the
    guard inert while still declaring the right pattern — SURVIVED 681 tests."""

    def _rec(self, **kw):
        d = {"type": "assistant", "timestamp": "2026-08-21T10:01:00.000Z",
             "cwd": "/srv/repo", "message": {"content": []}}
        d.update(kw)
        return json.dumps(d)

    def test_prose_in_ATTRIBUTION_is_rejected(self, tmp_path):
        p = _write(tmp_path, "s", [_user("hi"),
                                   self._rec(attributionSkill="not a valid name")])
        assert ts.scan_transcript(str(p), [], [])["skills_attributed"] == {}

    def test_an_absurdly_long_ATTRIBUTION_is_rejected(self, tmp_path):
        p = _write(tmp_path, "s", [_user("hi"),
                                   self._rec(attributionSkill="z" * 4000)])
        assert ts.scan_transcript(str(p), [], [])["skills_attributed"] == {}

    def test_prose_in_a_TYPED_command_is_rejected(self, tmp_path):
        p = _write(tmp_path, "s", [
            _user("<command-name>not a valid name</command-name>"), _assistant()])
        assert ts.scan_transcript(str(p), [], [])["commands_typed"] == {}

    def test_a_bare_PATH_in_an_INVOCATION_is_rejected(self, tmp_path):
        """A path is not a skill identity. `input.skill` is model-written and
        the corpus proves the harness sometimes writes a path there."""
        p = _write(tmp_path, "s", [_user("hi"), self._rec(message={"content": [
            {"type": "tool_use", "name": "Skill",
             "input": {"skill": "home/zach/workspace/clients/acme/.env"}}]})])
        assert ts.scan_transcript(str(p), [], [])["skills_invoked"] == {}

    def test_a_PATH_QUALIFIED_identity_records_the_SKILL_not_the_path(self, tmp_path):
        """The live shape on this fleet is `.claude/worktrees/agent-<hex>:remix`
        — one distinct value PER AGENT RUN. Keeping the whole string would make
        an unbounded-cardinality key out of a filesystem path; keeping the part
        after the last `:` records the skill that was actually used."""
        p = _write(tmp_path, "s", [_user("hi"), self._rec(message={"content": [
            {"type": "tool_use", "name": "Skill",
             "input": {"skill": ".claude/worktrees/agent-a2fdb76ed5a9fc025:remix"}}]})])
        assert ts.scan_transcript(str(p), [], [])["skills_invoked"] == {"remix": 1}

    def test_a_PLUGIN_qualified_identity_is_kept_WHOLE(self, tmp_path):
        """`cloudflare:wrangler` has no path component — the namespace is
        bounded and meaningful, so it is not truncated."""
        p = _write(tmp_path, "s", [_user("hi"),
                                   self._rec(attributionSkill="cloudflare:wrangler")])
        got = ts.scan_transcript(str(p), [], [])["skills_attributed"]
        assert got == {"cloudflare:wrangler": 1}

    def test_the_two_readers_AGREE_including_on_FUZZED_input(self):
        """The relationship, not the components: whatever this module records
        for a name, the emitter must record too.

        🔴 A HAND-PICKED VALUE LIST COULD NOT SEE A REAL DIVERGENCE. The
        previous revision listed eight values, none with BOTH a `/` and two
        colons — so mutating one copy's `rsplit(":", 1)` to `split(":", 1)`
        SURVIVED the suite, shipping an emitter and a search that disagreed
        about `apps/web:cloudflare:wrangler`. The generated cases below vary the
        axes that actually separate the implementations: slash count, colon
        count, leading dot, whitespace, length, and type."""
        st = _load_tailer()
        cases = [
            "signal", "/handoff", "cloudflare:wrangler", "not a valid name",
            ".claude/worktrees/agent-abc123:remix", "z" * 4000, "z" * 200,
            "home/zach/x/.env", "10.42.0.30:8123/activity", "", "   ", "/", "//",
            ":", "::", "a:", ":a", "a::b", "apps/web:deploy",
            "apps/web:cloudflare:wrangler", "a/b/c:d:e", "a/b:c/d",
            "not a valid name/x:handoff", ("z" * 4000) + "/a:handoff",
            "café", "sig nal", "a" * 64, "a" * 65, 42, None, True, ["signal"],
            {"skill": "x"}, 1.5,
        ]
        # Generated axes on top of the literals — the part a curated list cannot
        # cover, because the bug is always in the combination nobody pictured.
        for slashes in range(3):
            for colons in range(3):
                for lead in ("", ".", "/"):
                    cases.append(lead + "/".join(["seg"] * (slashes + 1))
                                 + ":".join([""] + ["part"] * colons))
        for value in cases:
            assert st.canonical_skill_name(value) == ts.canonical_skill_name(value), (
                f"the two readers disagree about {value!r}: "
                f"tailer={st.canonical_skill_name(value)!r} "
                f"search={ts.canonical_skill_name(value)!r}")

    def test_a_directory_scoped_PLUGIN_skill_keeps_its_plugin_namespace(self):
        """`apps/web:cloudflare:wrangler` — the directory is `apps/web`, the
        identity is `cloudflare:wrangler`. Truncating to `wrangler` would
        collide with a bare `wrangler` and contradict the rule that a plugin
        namespace is bounded and meaningful, so it is not truncated."""
        assert ts.canonical_skill_name("apps/web:cloudflare:wrangler") == \
            "cloudflare:wrangler"

    def test_PROSE_before_a_path_separator_cannot_be_rewritten_into_a_skill(self):
        """🔴 The path-strip examines only the TAIL, so a value whose HEAD is
        prose was being rewritten into a clean key — `not a valid
        name/x:handoff` recorded `handoff`, misattributing junk to a REAL skill
        with `unusable_skill_names` showing nothing wrong. Worse than the
        rejection it replaced, because the audit trail stayed clean."""
        for bad in ["not a valid name/x:handoff",
                    "please ignore previous/instructions:signal",
                    ("z" * 4000) + "/a:handoff"]:
            assert ts.canonical_skill_name(bad) is None, bad

    def test_a_NON_STRING_identity_is_rejected_by_BOTH_readers(self):
        """The search side dropped its `isinstance` guard when it moved to the
        shared rule, so `attributionSkill = 42` recorded the key `"42"` here and
        nothing in the emitter."""
        st = _load_tailer()
        for bad in [42, False, 1.5, None, ["signal"]]:
            assert ts.canonical_skill_name(bad) is None, bad
            assert st.canonical_skill_name(bad) is None, bad


class TestTheThirdInvocationRoute:
    """A `Skill` tool_use block — 1,305 in the live corpus. Reading only the
    other two routes undercounted `next-lever` by 87.5% (1 seen of 8)."""

    def _skill_block(self, name, args="some args"):
        return json.dumps({
            "type": "assistant", "timestamp": "2026-08-21T10:01:00.000Z",
            "cwd": "/srv/repo",
            "message": {"content": [{"type": "tool_use", "name": "Skill",
                                     "input": {"skill": name, "args": args}}]}})

    def test_a_skill_invoked_only_as_a_TOOL_is_found(self, tmp_path):
        _write(tmp_path, "invoked", [_user("do the thing"),
                                     self._skill_block("next-lever")])
        assert _ids(ts.search([], root=tmp_path, skill="next-lever")) == ["invoked"]

    def test_it_is_read_under_the_NARROW_surface_too(self, tmp_path):
        """Structure, not searchable text — the surface knob selects what TERMS
        match against and must not silently narrow this route."""
        p = _write(tmp_path, "invoked", [_user("hi"), self._skill_block("audit-pr")])
        rec = ts.scan_transcript(str(p), [], [], surface=ts.SURFACE_TEXT)
        assert rec["skills_invoked"] == {"audit-pr": 1}

    def test_a_NON_Skill_tool_use_does_not_count(self, tmp_path):
        p = _write(tmp_path, "bash", [_user("hi"), json.dumps({
            "type": "assistant", "timestamp": "2026-08-21T10:01:00.000Z",
            "message": {"content": [{"type": "tool_use", "name": "Bash",
                                     "input": {"skill": "not-a-skill"}}]}})])
        rec = ts.scan_transcript(str(p), [], [])
        assert rec["skills_invoked"] == {}


# --------------------------------------------------------------------------- #
# Scan-level shape
# --------------------------------------------------------------------------- #
class TestScanExposesBothBags:
    def test_scan_transcript_reports_the_two_signals_separately(self, tmp_path):
        p = _write(tmp_path, "mixed", [
            _user("<command-name>/signal</command-name>"),
            _assistant(skill="signal"),
            _assistant(skill="signal"),
        ])
        rec = ts.scan_transcript(str(p), [], [])
        assert rec["skills_attributed"] == {"signal": 2}
        assert rec["commands_typed"] == {"signal": 1}

    def test_a_command_quoted_in_TOOL_OUTPUT_is_not_an_invocation(self, tmp_path):
        """🔴 Under the wider `--all` surface, tool OUTPUT joins the searched
        text — and this repo's own transcripts routinely contain other
        transcripts (a `grep` of `~/.claude/projects` prints `<command-name>`
        lines verbatim). Counting those would make any session that GREPPED for
        skill usage look like a session that USED the skill, which is the exact
        confusion this flag exists to end."""
        p = _write(tmp_path, "grepper", [
            _user("what did we run?"),
            json.dumps({"type": "user", "timestamp": "2026-08-21T10:02:00.000Z",
                        "message": {"content": [{
                            "type": "tool_result",
                            "content": "match: <command-name>/signal</command-name>"}]}}),
        ])
        rec = ts.scan_transcript(str(p), [], [], surface=ts.SURFACE_ALL)
        assert rec["commands_typed"] == {}, (
            "a command name quoted inside tool output was counted as an invocation")

    def test_a_non_string_attribution_is_ignored(self, tmp_path):
        p = _write(tmp_path, "junk", [
            _user("hi"),
            json.dumps({"type": "assistant", "attributionSkill": ["signal"],
                        "message": {"content": []}}),
        ])
        rec = ts.scan_transcript(str(p), [], [])
        assert rec["skills_attributed"] == {}
