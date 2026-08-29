"""Tests for the SKILL-USAGE block of the Layer-A rollup (`skills_used` /
`commands_typed`).

Why this block exists: before it, nothing in devrc read Claude Code's
`attributionSkill` field (measured 2026-08-29: 0 references across `scripts/`
and `claude/`, against 120,366 records carrying it in `~/.claude/projects`), so
"is skill X actually used?" had no deterministic answer on the runtime where
most work happens. `adoption-scan` could only see the 9 tools that emit through
`invocation.py`; skills emit nothing.

🔴 The two fields are INDEPENDENT SIGNALS, and the tests below pin BOTH
directions of that because both have live instances. A reader that keeps only
one loses real usage.
"""
import importlib.util
import json
import sys
from pathlib import Path

_CLAUDE_DIR = Path(__file__).resolve().parent.parent          # scripts/collector/claude
_COLLECTOR_DIR = _CLAUDE_DIR.parent                            # scripts/collector
sys.path.insert(0, str(_CLAUDE_DIR))
sys.path.insert(0, str(_COLLECTOR_DIR))

_spec = importlib.util.spec_from_file_location(
    "session_tailer_skills", _CLAUDE_DIR / "session-tailer.py")
S = importlib.util.module_from_spec(_spec)
sys.modules["session_tailer_skills"] = S
_spec.loader.exec_module(S)

REPO = "/srv/checkouts/widget-repo"


def _assistant(*, skill=None, ts="2026-07-11T10:01:00.000Z", isSidechain=False):
    """An assistant record. `attributionSkill` is a TOP-LEVEL field — a sibling
    of `message`, not a field inside it. A fixture that nested it would pass
    against an implementation that read the wrong place."""
    rec = {"type": "assistant", "timestamp": ts, "cwd": REPO,
           "isSidechain": isSidechain,
           "message": {"role": "assistant", "model": "m", "content": [],
                       "usage": {}}}
    if skill is not None:
        rec["attributionSkill"] = skill
    return rec


def _typed(text="just a plain question", *, ts="2026-07-11T10:00:00.000Z"):
    return {"type": "user", "timestamp": ts, "cwd": REPO,
            "message": {"role": "user", "content": text}}


def _command(name, args="", *, ts="2026-07-11T10:00:00.000Z"):
    body = f"<command-name>/{name}</command-name>"
    if args:
        body += f"<command-args>{args}</command-args>"
    return _typed(body, ts=ts)


# --------------------------------------------------------------------------- #
# Positive control
# --------------------------------------------------------------------------- #
class TestSkillCapturePositiveControl:
    """A reassuring `{}` is indistinguishable from an extractor wired to
    nothing. Before quoting any zero from this block, something must prove the
    number CAN move."""

    def test_an_attributed_record_produces_a_NON_ZERO_count(self):
        r = S.build_rollup([_typed(), _assistant(skill="signal")])
        assert r["skills_used"] == {"signal": 1}, r["skills_used"]

    def test_a_typed_command_produces_a_NON_ZERO_count(self):
        r = S.build_rollup([_command("handoff"), _assistant()])
        assert r["commands_typed"] == {"handoff": 1}, r["commands_typed"]

    def test_an_unattributed_session_is_EMPTY_not_populated(self):
        """The negative half of the control: the extractor must be able to
        return nothing, or the two above prove only that it always fires."""
        r = S.build_rollup([_typed(), _assistant()])
        assert r["skills_used"] == {}
        assert r["commands_typed"] == {}


# --------------------------------------------------------------------------- #
# The two directions
# --------------------------------------------------------------------------- #
class TestNeitherSignalIsASuperset:
    """🔴 THE REASON THERE IS MORE THAN ONE FIELD. Measured on the workbench
    corpus 2026-08-29, counting SESSIONS (subagent transcripts excluded), with
    this module's own counting rule:

      * `browser`  —  50 attributed,   0 typed -> 50 attributed-only.
      * `clawgate` —  72 attributed,  28 typed -> 44 attributed-only,
                                                   **0** typed-only.
      * `handoff`  — 383 attributed, 346 typed -> 40 attributed-only,
                                                   **3** typed-only.

    ⚠ An earlier revision of this docstring cited `clawgate` 70/32 with 4
    typed-only, and "measured 8 times". BOTH WERE WRONG: they came from a raw
    file grep, which matches `<command-name>` inside quoted TOOL OUTPUT — the
    exact false positive `test_a_command_quoted_in_TOOL_OUTPUT_is_not_an_
    invocation` forbids. clawgate's typed-only is 0. `handoff` is the live
    instance. Do not re-derive either number with a grep."""

    def test_an_AUTO_FIRED_skill_is_seen_with_no_command_ever_typed(self):
        """The case the pre-existing `kind=command` telemetry is structurally
        blind to — and the majority case for most skills."""
        r = S.build_rollup([_typed("look at the page I have open"),
                            _assistant(skill="browser")])
        assert r["skills_used"] == {"browser": 1}
        assert r["commands_typed"] == {}, (
            "nothing was typed — a non-empty commands_typed here would mean the "
            "auto-fire signal is being inferred from the typed one")

    def test_a_TYPED_command_is_seen_with_no_attributed_record(self):
        """The inverse direction: typed, but no assistant record carried the
        attribution. Live instance: `handoff`, 3 sessions (NOT `clawgate`, whose
        typed-only is 0 — see the class docstring's retraction)."""
        r = S.build_rollup([_command("clawgate", "status"), _assistant()])
        assert r["commands_typed"] == {"clawgate": 1}
        assert r["skills_used"] == {}


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #
class TestCounting:
    def test_records_are_counted_per_skill_not_deduped_to_one(self):
        r = S.build_rollup([
            _typed(),
            _assistant(skill="signal"),
            _assistant(skill="signal"),
            _assistant(skill="browser"),
        ])
        assert r["skills_used"] == {"signal": 2, "browser": 1}

    def test_repeated_commands_accumulate(self):
        r = S.build_rollup([
            _command("handoff", ts="2026-07-11T10:00:00.000Z"),
            _command("handoff", ts="2026-07-11T10:05:00.000Z"),
            _command("resume", ts="2026-07-11T10:09:00.000Z"),
            _assistant(),
        ])
        assert r["commands_typed"] == {"handoff": 2, "resume": 1}

    def test_a_SIDECHAIN_record_does_not_contribute(self):
        """Subagent turns are excluded from every other rollup field (they are
        skipped before the timestamp min/max). A skill a SUBAGENT loaded is not
        this session's usage, and counting it would inflate every session that
        dispatched one."""
        r = S.build_rollup([_typed(),
                            _assistant(skill="signal", isSidechain=True)])
        assert r["skills_used"] == {}

    def test_a_non_string_attribution_is_ignored_not_crashed_on(self):
        """Same posture as `unusable_file_paths`: a malformed transcript must
        not kill the pass for every OTHER session in the same tick."""
        rec = _assistant()
        rec["attributionSkill"] = {"name": "signal"}
        blank = _assistant()
        blank["attributionSkill"] = "   "
        r = S.build_rollup([_typed(), rec, blank])
        assert r["skills_used"] == {}


# --------------------------------------------------------------------------- #
# Honest naming + leak guard
# --------------------------------------------------------------------------- #
class TestTheNameBoundDoesNotREJECTRealIdentities:
    """🔴 A bound drawn around the names that happen to exist TODAY rejects the
    ones that arrive tomorrow — and rejects them SILENTLY, which is the failure
    this whole change removes. A skill identity is namespace-qualified:
    `plugin:skill`, and `apps/web:deploy` for a directory-scoped one."""

    def test_a_PLUGIN_qualified_skill_is_recorded(self):
        r = S.build_rollup([_typed(), _assistant(skill="cloudflare:wrangler")])
        assert r["skills_used"] == {"cloudflare:wrangler": 1}
        assert r["unusable_skill_names"] == 0

    def test_a_DIRECTORY_scoped_skill_is_recorded(self):
        r = S.build_rollup([_typed(), _assistant(skill="apps/web:deploy")])
        assert r["skills_used"] == {"apps/web:deploy": 1}
        assert r["unusable_skill_names"] == 0

    def test_the_two_skill_name_bounds_agree(self):
        """🔴 The bound is DUPLICATED in scripts/lib/transcript_search.py and
        cannot be a shared import: nix/home.nix deploys
        `scripts/collector/claude` ALONE to the daemon's runtime path, so an
        import from scripts/lib would pass every test here and break the running
        service on both hosts. Two copies, one enforced ledger — if they drift,
        the emitter and the search disagree about what a skill name IS."""
        lib = (Path(__file__).resolve().parents[3] / "lib" / "transcript_search.py")
        src = lib.read_text(encoding="utf-8")
        marker = "SKILL_NAME_PATTERN = r"
        assert marker in src, f"{lib} no longer defines SKILL_NAME_PATTERN"
        theirs = src.split(marker, 1)[1].splitlines()[0].strip()
        assert theirs == repr(S.SKILL_NAME_PATTERN).replace("'", '"') or \
            theirs.strip('"\'') == S.SKILL_NAME_PATTERN, (
                f"the two skill-name bounds have drifted: tailer has "
                f"{S.SKILL_NAME_PATTERN!r}, transcript_search.py has {theirs}")


class TestAnEmptyTagDoesNotSWALLOWARealCommand:
    """🔴 A regression introduced by the fix for the args leak. Latching the
    first tag MATCH — rather than the first NON-EMPTY one — let an empty tag in
    an earlier block discard a genuine command in a later block, counting
    nothing, so the loss was invisible."""

    def test_a_real_command_after_an_empty_tag_is_still_counted(self):
        rec = _typed()
        rec["message"]["content"] = [
            {"type": "text", "text": "<command-name>   </command-name>"},
            {"type": "text", "text": "<command-name>/handoff</command-name>"},
        ]
        r = S.build_rollup([rec, _assistant()])
        assert r["commands_typed"] == {"handoff": 1}

    def test_a_command_turn_whose_only_tag_is_empty_is_COUNTED_as_unusable(self):
        """The discarded case has to be observable, or the filter is
        indistinguishable from one wired to nothing."""
        r = S.build_rollup(
            [_typed("<command-name></command-name><command-args>x</command-args>"),
             _assistant()])
        assert r["commands_typed"] == {}
        assert r["unusable_skill_names"] == 1


class TestCommandsTypedIsNotAClaimAboutSkills:
    def test_a_BUILT_IN_command_lands_in_commands_typed(self):
        """`/login` and `/clear` are Claude Code built-ins, not skills. The field
        is named `commands_typed` — not `skills_typed` — precisely because it
        holds them, and a reader wanting skills must intersect with the skill
        list rather than trusting this key's name."""
        r = S.build_rollup([_command("login"), _assistant()])
        assert r["commands_typed"] == {"login": 1}
        assert r["skills_used"] == {}, (
            "a built-in command must never be reported as a skill USE")


class TestTheExplicitInvocationRoute:
    """The THIRD route: a `Skill` tool_use block. 1,305 of them in the live
    corpus, and reading only the other two undercounted real usage by up to
    87.5% on a single skill (`next-lever`: 1 seen, 8 actual)."""

    def test_a_Skill_tool_use_is_recorded(self):
        rec = _assistant()
        rec["message"]["content"] = [{"type": "tool_use", "name": "Skill",
                                      "input": {"skill": "audit-pr", "args": "11"}}]
        r = S.build_rollup([_typed(), rec])
        assert r["skills_invoked"] == {"audit-pr": 1}

    def test_the_INVOCATION_ARGS_never_reach_the_payload(self):
        """🔴 `input.args` sits beside `input.skill` and is operator free-text —
        live examples carry account ids. This payload is public."""
        secret = "lookUpAccount8753561Fenwick"
        rec = _assistant()
        rec["message"]["content"] = [{"type": "tool_use", "name": "Skill",
                                      "input": {"skill": "postgres-query",
                                                "args": secret}}]
        ev = S.build_event("sid", S.build_rollup([_typed(), rec]))
        assert secret not in ev["payload"]
        assert "postgres-query" in ev["payload"]

    def test_a_NON_Skill_tool_use_contributes_nothing(self):
        rec = _assistant()
        rec["message"]["content"] = [{"type": "tool_use", "name": "Bash",
                                      "input": {"skill": "not-a-skill"}}]
        r = S.build_rollup([_typed(), rec])
        assert r["skills_invoked"] == {}


class TestNoOperatorFreeTextReachesThePayload:
    """🔴 devrc is PUBLIC and this payload ships to ClickHouse for every session
    on both hosts. The command NAME is a bounded, low-cardinality token; its
    ARGS are operator free-text."""

    def test_an_EMPTY_command_name_does_not_promote_the_ARGS_to_a_key(self):
        """🔴 `classify()` returns `cname + " " + cargs`, so an empty name tag
        makes `ctext` the ARGS and the first word of operator free-text became a
        payload key. The original guard only ever built a NON-empty name, so it
        passed while this leaked."""
        secret = "harbourPermitFenwick2026"
        ev = S.build_event("sid", S.build_rollup(
            [_typed(f"<command-name></command-name><command-args>{secret} and more"
                    "</command-args>"), _assistant()]))
        assert secret not in ev["payload"]

    def test_a_WHITESPACE_command_name_does_not_either(self):
        secret = "harbourPermitFenwick2026"
        ev = S.build_event("sid", S.build_rollup(
            [_typed(f"<command-name>   </command-name><command-args>{secret}"
                    "</command-args>"), _assistant()]))
        assert secret not in ev["payload"]

    def test_PROSE_in_the_name_tag_with_no_args_tag_is_rejected(self):
        secret = "harbourPermitFenwick2026"
        ev = S.build_event("sid", S.build_rollup(
            [_typed(f"<command-name>{secret} and more prose</command-name>"),
             _assistant()]))
        assert secret not in ev["payload"]

    def test_an_ABSURDLY_LONG_name_cannot_inflate_the_payload(self):
        """A 4,000-char name went straight through and multiplied the payload
        size — for every session, on both hosts."""
        ev = S.build_event("sid", S.build_rollup(
            [_typed("<command-name>/" + "z" * 4000 + "</command-name>"), _assistant()]))
        assert len(ev["payload"]) < 2000
        assert "zzzz" not in ev["payload"]

    def test_a_rejected_name_is_COUNTED_not_silently_dropped(self):
        """A filter nobody can count is indistinguishable from one wired to
        nothing — the repo's own rule."""
        r = S.build_rollup(
            [_typed("<command-name>not a valid name</command-name>"), _assistant()])
        assert r["commands_typed"] == {}
        assert r["unusable_skill_names"] == 1

    def test_a_LEGITIMATE_name_still_gets_through(self):
        """The negative control for the guard above: a bound that rejects
        everything would pass every leak test and break the feature."""
        r = S.build_rollup([_command("check-clickup-addressed"), _assistant()])
        assert r["commands_typed"] == {"check-clickup-addressed": 1}
        assert r["unusable_skill_names"] == 0

    def test_only_the_command_NAME_is_kept_never_its_args(self):
        secret = "read the harbour permit thread with Fenwick"
        r = S.build_rollup([_command("signal", secret), _assistant()])
        assert r["commands_typed"] == {"signal": 1}
        assert secret not in json.dumps(r)

    def test_the_args_do_not_survive_into_the_emitted_EVENT(self):
        """The blast radius is `build_event`, which json-dumps the whole rollup
        as the payload — so the assertion belongs against the EVENT, not only
        against the dict."""
        secret = "read the harbour permit thread with Fenwick"
        ev = S.build_event("sid", S.build_rollup(
            [_command("signal", secret), _assistant(skill="signal")]))
        assert secret not in ev["payload"]
        assert "signal" in ev["payload"]


class TestTheBlockIsPartOfTheEmittedPayload:
    """Unlike the absolute-path window, these keys ARE meant to ship — the whole
    point is that the answer becomes queryable in ClickHouse rather than
    requiring a grep of `~/.claude/projects` on the right host."""

    def test_both_keys_are_present_on_every_event(self):
        ev = S.build_event("sid", S.build_rollup([_typed(), _assistant()]))
        payload = json.loads(ev["payload"])
        assert "skills_used" in payload
        assert "commands_typed" in payload

    def test_an_unreadable_transcript_leaves_them_EMPTY_not_absent(self):
        """Same convention as `tool_counts`: `unreadable` / `stats_unavailable`
        carry the could-not-observe verdict, so these stay `{}` rather than
        encoding unobservability a second time in a different shape."""
        r = S.build_rollup([])
        assert r["skills_used"] == {}
        assert r["commands_typed"] == {}
        assert r["unreadable"] is True
