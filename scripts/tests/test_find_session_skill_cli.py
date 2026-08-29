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


def _assistant(skill):
    return json.dumps({"type": "assistant", "timestamp": "2026-08-21T10:01:00.000Z",
                       "cwd": "/srv/repo", "attributionSkill": skill,
                       "message": {"content": [{"type": "text", "text": "ok"}]}})


def _user():
    return json.dumps({"type": "user", "timestamp": "2026-08-21T10:00:00.000Z",
                       "cwd": "/srv/repo", "message": {"content": "hi"}})


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

    @pytest.mark.parametrize("bad", ["/", "   ", "//"])
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


class TestSkillWithOpencodeOnlyIsRefused:
    def test_it_exits_2_rather_than_returning_an_unfiltered_set(self, corpus):
        """The opencode corpus carries no per-record attribution, so this
        combination cannot be answered. Answering it with a term-only result set
        would silently substitute a different question."""
        code, out, err = run(corpus, ["--skill", "signal", "--opencode-only"])
        assert code == 2
        assert "cannot be answered" in err
