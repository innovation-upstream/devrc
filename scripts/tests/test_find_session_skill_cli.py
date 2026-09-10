#!/usr/bin/env python3
"""CLI-level tests for `find-session.py --skill`.

🔴 WHY A SEPARATE CLI SUITE. The library tests cover the predicate; they cannot
see the three things that live only in `main()`, and an audit round found all
three unguarded after they were "fixed":

  * the empty-query guard reads the NORMALISED skill, so `--skill /` exits 2
    instead of returning a corpus-wide empty result at exit 0;
  * the "opencode NOT searched" disclosure is emitted BEFORE the `--json` early
    return, so the one consumer that cannot infer scope from prose still gets it;
  * that disclosure goes to STDERR, so `--json` stdout stays parseable.

Move the `print` back below the return, or drop `file=sys.stderr`, and no
library test notices. These do.

The opencode corpus is scoped to an empty DB and NO peers, or these read the
real stores and SSH to the other host.
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"


def _load():
    spec = importlib.util.spec_from_file_location(
        "fs_skill_cli", SCRIPTS / "find-session.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fs_skill_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(root, session_id, lines, project="-srv-repo"):
    d = Path(root) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session_id}.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def _stamp(days_ago):
    """🔴 FIXTURE TIMES ARE RELATIVE TO NOW, NEVER A WALL-CLOCK DATE. These
    records were pinned to a literal `2026-08-21`, which was inside every window
    the tool had (there were none) until `DEFAULT_SINCE_DAYS` landed and it was
    18 days old — the whole `TestTheGuardDoesNotBREAKTheToolsPRIMARYMODE` class
    went red on a corpus that had not changed. A fixture anchored to a calendar
    date is a test that expires; anchored to `now`, it cannot."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _assistant(skill, days_ago=1, text="ok"):
    return json.dumps({"type": "assistant", "timestamp": _stamp(days_ago),
                       "cwd": "/srv/repo", "attributionSkill": skill,
                       "message": {"content": [{"type": "text", "text": text}]}})


def _user(days_ago=1, text="hi"):
    return json.dumps({"type": "user", "timestamp": _stamp(days_ago),
                       "cwd": "/srv/repo", "message": {"content": text}})


def run(root, argv):
    """Drive main(). Returns (exit_code, stdout, stderr) with the two streams
    kept SEPARATE — merging them is how a stderr-only line gets miscredited to
    stdout (and zsh's MULTIOS makes the shell version of that mistake easy)."""
    mod = _load()
    mod.ROOT = str(root)
    out, err = io.StringIO(), io.StringIO()
    old_argv, code = sys.argv, 0
    old_env = {k: os.environ.get(k)
               for k in ("DEVRC_OPENCODE_PEERS", "DEVRC_OPENCODE_DB")}
    os.environ["DEVRC_OPENCODE_PEERS"] = ""                 # no host is contacted
    os.environ["DEVRC_OPENCODE_DB"] = str(Path(root) / "_no_opencode.db")
    sys.argv = ["find-session.py"] + list(argv)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = mod.main() or 0
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old_argv
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def corpus(tmp_path):
    _write(tmp_path, "used", [_user(), _assistant("signal")])
    return tmp_path


class TestAQueryThatNamesNothingIsRefusedNotAnsweredWithZero:
    """🔴 Each of these was truthy but named nothing. Returning an empty result
    for them is indistinguishable from "the skill was never used" — the exact
    silent zero `--skill` exists to remove."""

    @pytest.mark.parametrize("bad", ["/", "   ", "", "//"])
    def test_it_exits_2_and_says_so(self, corpus, bad):
        code, out, err = run(corpus, ["--skill", bad])
        assert code == 2, f"--skill {bad!r} did not exit 2 (got {code}, stdout={out!r})"
        assert "names no skill" in err or "nothing to search for" in err
        assert out == "", "a refused query must not print a result set"

    @pytest.mark.parametrize("bad", ["/", "   ", "//", ""])
    def test_it_is_STILL_refused_when_search_terms_are_present(self, corpus, bad):
        """🔴 THE BOUNDARY THE FOUR CASES ABOVE DO NOT PIN. They all run with no
        terms, so they only exercise the `not terms and not skill` conjunction.
        With a term present, a `--skill` that normalised to "" was DROPPED and
        an unfiltered keyword search ran at exit 0 — `find-session redis --skill
        /clawgate`, typo'd to `--skill /`, answering a different question and
        reading as an answer to the one asked. Worse than the silent zero."""
        code, out, err = run(corpus, ["hi", "--skill", bad])
        assert code == 2, (
            f"'hi --skill {bad!r}' returned {code} — the skill filter was "
            f"silently dropped and a keyword result was printed: {out[:200]!r}")
        assert "names no skill" in err

    def test_a_REAL_skill_name_is_NOT_refused(self, corpus):
        """🔴 The negative control for the guard above. A guard that refused
        EVERY query would satisfy all four parametrised cases and break the
        feature — the four would then be measuring the guard's existence, not
        its boundary."""
        code, out, err = run(corpus, ["--skill", "signal"])
        assert code == 0, f"a legitimate --skill was refused: {err!r}"
        assert "used" in out
        assert "nothing to search for" not in err

    def test_a_SLASH_PREFIXED_real_name_is_accepted_not_refused(self, corpus):
        """`--skill /signal` is what a human types after reading `/signal`. The
        normalisation that makes `/` alone a refusal must not also refuse this."""
        code, out, err = run(corpus, ["--skill", "/signal"])
        assert code == 0, f"--skill /signal was refused: {err!r}"
        assert "used" in out


class TestTheScopeDisclosure:
    def test_json_stdout_is_VALID_JSON_and_the_disclosure_is_on_stderr(self, corpus):
        code, out, err = run(corpus, ["--skill", "signal", "--json"])
        assert code == 0
        parsed = json.loads(out)                       # raises if the line leaked
        assert [r["session_id"] for r in parsed] == ["used"]
        assert "NOT searched" in err

    def test_the_disclosure_is_present_on_the_EMPTY_json_path(self, corpus):
        """The case that made it a finding: a bare `[]` with no indication that
        half the fleet's corpus was never searched."""
        code, out, err = run(corpus, ["--skill", "nosuchskill", "--json"])
        assert code == 0
        assert json.loads(out) == []
        assert "NOT searched" in err

    def test_it_is_NOT_claimed_when_the_user_scoped_the_search_themselves(self, corpus):
        """`--claude-only` means the opencode corpus was excluded on purpose, so
        announcing it as an unmet limit would be false."""
        code, out, err = run(corpus, ["--skill", "signal", "--claude-only"])
        assert code == 0
        assert "NOT searched" not in err

    def test_it_is_NOT_claimed_for_a_plain_keyword_search(self, corpus):
        code, out, err = run(corpus, ["hi"])
        assert "NOT searched" not in err


class TestTheGuardDoesNotBREAKTheToolsPRIMARYMODE:
    """🔴 THE NEGATIVE CONTROL FOR THE `--skill` REFUSAL, and it was missing.

    Every other test here passes `--skill`. Reverting the arg's default from
    `None` back to `""` makes an OMITTED `--skill` indistinguishable from an
    empty one, so the refusal fires on every plain keyword search — measured:
    `find-session signal` exits 2 with no output, the tool's primary mode dead.
    The whole CLI suite stayed GREEN through that.

    A guard is only as trustworthy as the case that proves it does not fire."""

    def test_a_plain_keyword_search_with_NO_skill_flag_still_works(self, corpus):
        code, out, err = run(corpus, ["hi"])
        assert code == 0, (
            f"a plain keyword search was refused (rc={code}) — the --skill guard "
            f"is firing when no --skill was given: {err!r}")
        assert "used" in out, "the keyword search returned no results"
        assert "names no skill" not in err

    def test_a_plain_keyword_search_still_works_under_json(self, corpus):
        code, out, err = run(corpus, ["hi", "--json"])
        assert code == 0, err
        assert [r["session_id"] for r in json.loads(out)] == ["used"]


class TestSkillWithOpencodeOnlyIsRefused:
    def test_it_exits_2_rather_than_returning_an_unfiltered_set(self, corpus):
        """The opencode corpus carries no per-record attribution, so this
        combination cannot be answered. Answering it with a term-only result set
        would silently substitute a different question."""
        code, out, err = run(corpus, ["--skill", "signal", "--opencode-only"])
        assert code == 2
        assert "cannot be answered" in err


@pytest.fixture
def corpus_with_an_old_session(tmp_path):
    """A RECENT session and one comfortably outside the default window.

    The two share no search term on purpose: `zzancient` is in the old session
    only, so a test that finds it has found THAT session and not the fixture's
    other one.
    """
    _write(tmp_path, "recent", [_user(days_ago=1), _assistant("signal", 1)])
    old = fs_days_outside_the_window()
    _write(tmp_path, "ancient",
           [_user(days_ago=old, text="zzancient"),
            _assistant("zzoldskill", old, text="zzancient")])
    return tmp_path


def fs_days_outside_the_window():
    """Derived from the constant, never a literal. A test that hardcoded `40`
    would silently stop testing the boundary the day the default moved."""
    mod = _load()
    return mod.DEFAULT_SINCE_DAYS * 3


class TestTheDefaultArchiveWindow:
    """🔴 END-TO-END, THROUGH THE REAL WALK. `resolve_window` is unit-tested in
    `test_find_session_live.py`; this class is the seam check — that the cutoff
    it returns actually reaches `search()` and changes the result set. A window
    computed correctly and never passed on would pass every unit test.

    🔴 EVERY ASSERTION READS SESSION IDS OUT OF `--json`, NOT WORDS OUT OF THE
    HUMAN OUTPUT. The first draft asserted `"ancient" not in out` and FAILED
    against correct code, because the human path echoes the query: `No sessions
    matched: zzancient` contains the word the guard was looking for. That is the
    spelled-guard trap in `claude/RULES.md` — assert the STATE (which session
    ids came back), never a word another line can spell.
    """

    @staticmethod
    def ids(root, argv):
        code, out, err = run(root, list(argv) + ["--json"])
        assert code == 0, err
        return [r["session_id"] for r in json.loads(out)], err

    def test_a_session_OUTSIDE_the_window_is_NOT_returned_by_default(
            self, corpus_with_an_old_session):
        got, _ = self.ids(corpus_with_an_old_session, ["zzancient"])
        assert got == [], (
            f"the default window did not reach the walk — {got} came back, so "
            "the cutoff was computed and then dropped")

    def test_all_time_FINDS_the_one_the_default_window_hid(
            self, corpus_with_an_old_session):
        """🔴 THE POSITIVE CONTROL, and the test above is worthless without it.
        "the default returned nothing" is indistinguishable from "the fixture
        never matched anything" until the same query under `--all-time` returns
        a non-zero count."""
        got, _ = self.ids(corpus_with_an_old_session, ["zzancient", "--all-time"])
        assert got == ["ancient"], (
            "--all-time did not find a session the fixture definitely contains "
            f"— the corpus or the query is wrong, not the window: {got}")

    def test_an_EXPLICIT_since_reaches_it_too(self, corpus_with_an_old_session):
        old = fs_days_outside_the_window()
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=old + 5)).date().isoformat()
        got, _ = self.ids(corpus_with_an_old_session,
                          ["zzancient", "--since", cutoff])
        assert got == ["ancient"]

    def test_a_SKILL_query_is_UNWINDOWED_end_to_end(
            self, corpus_with_an_old_session):
        """🔴 The exemption has to survive the trip to `search()`, not just
        `resolve_window`. `adoption-scan` asks this exact question."""
        got, _ = self.ids(corpus_with_an_old_session, ["--skill", "zzoldskill"])
        assert got == ["ancient"], (
            "a --skill query was windowed — 'has skill X ever been used' "
            f"silently became 'used in the last few days': {got}")

    def test_the_RECENT_session_is_still_found_by_default(
            self, corpus_with_an_old_session):
        """The window must cut the old one and ONLY the old one — the boundary
        control on the two tests above."""
        got, _ = self.ids(corpus_with_an_old_session, ["hi"])
        assert got == ["recent"]

    def test_the_WINDOW_IS_DISCLOSED_even_when_it_hid_everything(
            self, corpus_with_an_old_session):
        """The sentence that stops "No sessions matched" reading as a
        corpus-wide absence."""
        _, err = self.ids(corpus_with_an_old_session, ["zzancient"])
        assert "ARCHIVE window:" in err, err
        assert "--all-time" in err, (
            "the notice must say how to LIFT the bound, not only that one exists")

    def test_the_disclosure_names_the_WHOLE_corpus_under_all_time(
            self, corpus_with_an_old_session):
        _, err = self.ids(corpus_with_an_old_session, ["zzancient", "--all-time"])
        assert "WHOLE corpus" in err and "nothing was cut" in err
