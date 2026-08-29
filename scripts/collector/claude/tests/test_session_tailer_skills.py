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
    """🔴 THE REASON THERE ARE TWO FIELDS. Measured on the workbench corpus
    2026-08-29, counting SESSIONS (subagent transcripts excluded):

      * `browser`  — 50 attributed, **0** ever typed `/browser`.
      * `clawgate` — 70 attributed, 32 typed: 42 attributed-only AND
                     4 typed-but-never-attributed.

    Both directions have live instances, so collapsing to one field is a
    measurable loss, not a simplification. These two tests are what fail if
    someone tries it."""

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
        """The inverse, measured 8 times for `/clawgate` alone: the command was
        typed but no assistant record carried the attribution."""
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


class TestNoOperatorFreeTextReachesThePayload:
    """🔴 devrc is PUBLIC and this payload ships to ClickHouse for every session
    on both hosts. The command NAME is a bounded, low-cardinality token; its
    ARGS are operator free-text."""

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
