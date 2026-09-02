"""Tests for `scripts/claude-hooks/git-add-provenance-nudge.py`.

The hook warns when a `git add` stages an untracked file whose mtime predates
the session — somebody else's in-flight work swept into your index.

🔴 THE TEST THAT MATTERS MOST IS THE SILENT ONE.
`test_a_file_written_during_the_session_is_NOT_flagged` is the false-positive
control, and it guards the whole reason this is a nudge rather than a
`guard_core` check: in THIS repo staging a new file is MANDATORY (`CLAUDE.md` —
"A NEW file must be `git add`ed or the flake silently omits it from the
deploy"). A hook that fires on ordinary new-file adds would fire on the deploy
path for every new skill, hook and test, and `claude/RULES.md` calls a
permanently-red gate worse than no gate.

    run:  python -m pytest scripts/claude-hooks/tests/test_git_add_provenance_nudge.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "git-add-provenance-nudge.py"

#: Session start, and the two sides of it. Pairwise distinct and far apart, so a
#: mutant that swaps the comparison or drops the offset cannot land on the
#: boundary and pass by luck.
T_START = 1_780_000_000.0
T_BEFORE = T_START - 86_400.0      # a day before the session — foreign
T_AFTER = T_START + 3_600.0        # an hour into the session — ours


def _sh(*args: str, cwd: Path) -> str:
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, check=True,
        env=dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null",
                 GIT_CONFIG_SYSTEM="/dev/null"),
    ).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _sh("git", "init", "-q", "-b", "main", ".", cwd=r)
    _sh("git", "config", "user.email", "t@example.invalid", cwd=r)
    _sh("git", "config", "user.name", "T", cwd=r)
    (r / "tracked.txt").write_text("one\n", encoding="utf-8")
    _sh("git", "add", "tracked.txt", cwd=r)
    _sh("git", "commit", "-qm", "base", cwd=r)
    return r


def transcript(tmp_path: Path, *, timestamped: bool = True) -> Path:
    """A transcript whose earliest timestamp is `T_START`.

    The first record deliberately carries NO timestamp — that is the real shape
    at claude-code 2.1.232, where the opening `leafUuid` summary has none and the
    first timestamped record was line 5. A hook that read line 1 would find no
    clock and go silent on every real session.
    """
    p = tmp_path / "transcript.jsonl"
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(T_START, timezone.utc).isoformat().replace(
        "+00:00", "Z")
    rows = [{"type": "summary", "leafUuid": "abc", "sessionId": "s"}]
    if timestamped:
        rows.append({"type": "attachment", "timestamp": iso})
    else:
        rows.append({"type": "attachment"})
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def stage(repo: Path, name: str, mtime: float, *, body: str = "x\n") -> None:
    f = repo / name
    f.write_text(body, encoding="utf-8")
    _sh("git", "add", "--", name, cwd=repo)
    os.utime(f, (mtime, mtime))


def run(payload: dict, home: Path) -> tuple[int, str]:
    p = subprocess.run(
        [sys.executable, str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
        env=dict(os.environ, HOME=str(home)),
    )
    return p.returncode, p.stdout.strip()


def call(repo: Path, tmp_path: Path, *, session: str = "sess-1",
         cmd: str = "git add scripts/thing", tool: str = "Bash",
         timestamped: bool = True, transcript_path: str | None = "auto") -> str:
    tp = transcript_path
    if tp == "auto":
        tp = str(transcript(tmp_path, timestamped=timestamped))
    payload = {
        "tool_name": tool,
        "session_id": session,
        "cwd": str(repo),
        "tool_input": {"command": cmd},
    }
    if tp is not None:
        payload["transcript_path"] = tp
    rc, out = run(payload, tmp_path)
    assert rc == 0, f"a nudge must never fail the call (rc={rc}, out={out})"
    return out


# --------------------------------------------------------------------------
# the two directions
# --------------------------------------------------------------------------

def test_a_file_predating_the_session_IS_flagged(repo: Path, tmp_path: Path):
    """The incident: `scripts/memory-detail`, 181 lines of another effort's
    uncommitted work, staged to unblock a `home-manager switch`."""
    stage(repo, "memory-detail", T_BEFORE)
    out = call(repo, tmp_path)
    assert "additionalContext" in out
    assert "memory-detail" in out


def test_a_file_written_during_the_session_is_NOT_flagged(repo: Path, tmp_path: Path):
    """🔴 THE FALSE-POSITIVE CONTROL. Staging a new file is MANDATORY in this
    repo — the flake omits untracked paths from the deploy. If this ever fires,
    the hook is nagging on the correct, required action."""
    stage(repo, "brand-new.py", T_AFTER)
    assert call(repo, tmp_path) == ""


def test_a_modified_TRACKED_file_is_not_flagged(repo: Path, tmp_path: Path):
    """`--diff-filter=A` is the predicate. Editing a tracked file and staging it
    is ordinary work and says nothing about provenance — even with an old mtime,
    which a checkout or a restore routinely produces."""
    (repo / "tracked.txt").write_text("two\n", encoding="utf-8")
    _sh("git", "add", "--", "tracked.txt", cwd=repo)
    os.utime(repo / "tracked.txt", (T_BEFORE, T_BEFORE))
    assert call(repo, tmp_path) == ""


def test_an_unstaged_untracked_file_is_not_flagged(repo: Path, tmp_path: Path):
    """Merely EXISTING in the tree is not staging it. The hazard is the index."""
    f = repo / "someone-elses.py"
    f.write_text("x\n", encoding="utf-8")
    os.utime(f, (T_BEFORE, T_BEFORE))
    assert call(repo, tmp_path) == ""


# --------------------------------------------------------------------------
# the clock — no clock means SILENCE, never "everything is old"
# --------------------------------------------------------------------------

def test_no_transcript_path_means_no_nudge(repo: Path, tmp_path: Path):
    """🔴 Without a clock every staged-new file looks equally old, so the
    fail-open direction is the loud one. Silence is correct here.

    ⚠ THIS PINS BEHAVIOUR, NOT THE GUARD — say so rather than counting it as
    coverage it does not provide. MEASURED: mutating the hook's
    `if started is None: sys.exit(0)` to `if False:` leaves all 19 tests GREEN.
    The removal is masked, because `foreign_staged(repo, None)` then raises
    `TypeError` on `mtime < None` and `main`'s outer `except Exception: pass`
    swallows it — the observable result is silence either way.

    The two defences are deliberate and BOTH stay: a nudge may never break the
    Bash call it observes, so the broad catch cannot be narrowed to make the
    guard uniquely responsible, and control flow through an exception is not
    something to rely on. So the guard is belt-and-braces and its mutant
    SURVIVES BY DESIGN. `claude/RULES.md` asks for that to be labelled, not
    hidden: what this test proves is that the no-clock path is silent, not that
    the explicit guard is the thing making it silent."""
    stage(repo, "memory-detail", T_BEFORE)
    assert call(repo, tmp_path, transcript_path=None) == ""


def test_a_transcript_with_no_timestamp_means_no_nudge(repo: Path, tmp_path: Path):
    stage(repo, "memory-detail", T_BEFORE)
    assert call(repo, tmp_path, timestamped=False) == ""


def test_an_unreadable_transcript_means_no_nudge(repo: Path, tmp_path: Path):
    stage(repo, "memory-detail", T_BEFORE)
    assert call(repo, tmp_path, transcript_path=str(tmp_path / "nope.jsonl")) == ""


# --------------------------------------------------------------------------
# trigger scoping
# --------------------------------------------------------------------------

def test_a_non_bash_tool_is_silent(repo: Path, tmp_path: Path):
    stage(repo, "memory-detail", T_BEFORE)
    assert call(repo, tmp_path, tool="Edit") == ""


def test_a_command_that_is_not_a_git_add_is_silent(repo: Path, tmp_path: Path):
    """The index is dirty in exactly the flagged way; the command is unrelated.
    Firing here would make every Bash call in a dirty tree carry the nudge."""
    stage(repo, "memory-detail", T_BEFORE)
    assert call(repo, tmp_path, cmd="git status -sb") == ""


def test_a_git_dash_C_hop_is_followed(repo: Path, tmp_path: Path):
    """`git -C <dir> add …` is the spelling this repo's CLAUDE.md mandates, so
    it must not be the spelling the hook is blind to."""
    stage(repo, "memory-detail", T_BEFORE)
    other = tmp_path / "elsewhere"
    other.mkdir()
    out = call(repo, tmp_path, cmd=f"git -C {repo} add scripts/memory-detail")
    assert "memory-detail" in out


# --------------------------------------------------------------------------
# robustness — a nudge may never break the call it observes
# --------------------------------------------------------------------------

def test_malformed_stdin_exits_zero_and_says_nothing(tmp_path: Path):
    p = subprocess.run([sys.executable, str(HOOK)], input="not json",
                       capture_output=True, text=True,
                       env=dict(os.environ, HOME=str(tmp_path)))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_a_cwd_that_is_not_a_repo_is_silent(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    rc, out = run({"tool_name": "Bash", "session_id": "s", "cwd": str(plain),
                   "transcript_path": str(transcript(tmp_path)),
                   "tool_input": {"command": "git add x"}}, tmp_path)
    assert rc == 0 and out == ""


def test_a_staged_then_deleted_file_does_not_crash(repo: Path, tmp_path: Path):
    """`os.stat` fails on it; it carries no evidence either way, so it is
    skipped rather than reported or raised on."""
    stage(repo, "gone.py", T_BEFORE)
    (repo / "gone.py").unlink()
    assert call(repo, tmp_path) == ""


# --------------------------------------------------------------------------
# the message, and not repeating it
# --------------------------------------------------------------------------

def test_the_same_file_is_reported_only_once_per_session(repo: Path, tmp_path: Path):
    """Re-reporting on every subsequent `git add` is how a nudge becomes
    wallpaper and stops being read."""
    stage(repo, "memory-detail", T_BEFORE)
    assert "memory-detail" in call(repo, tmp_path, session="dedupe-1")
    assert call(repo, tmp_path, session="dedupe-1") == ""


def test_a_different_session_is_told_again(repo: Path, tmp_path: Path):
    stage(repo, "memory-detail", T_BEFORE)
    assert "memory-detail" in call(repo, tmp_path, session="A")
    assert "memory-detail" in call(repo, tmp_path, session="B")


def test_the_message_carries_the_unstage_remedy(repo: Path, tmp_path: Path):
    """A nudge that names a hazard and no fix is a nag."""
    stage(repo, "memory-detail", T_BEFORE)
    assert "git restore --staged" in call(repo, tmp_path)


def test_the_message_ASKS_rather_than_asserting_authorship(repo: Path, tmp_path: Path):
    """🔴 mtime cannot name an author — `cp -a`, a checkout and a restore all
    move it. The text must not claim the file is someone else's, or a reader
    acting on it will unstage their own work."""
    stage(repo, "memory-detail", T_BEFORE)
    out = json.loads(call(repo, tmp_path))
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "heuristic" in msg
    assert "nothing was blocked" in msg


def test_several_files_are_all_named(repo: Path, tmp_path: Path):
    for n in ("a-one.py", "b-two.py", "c-three.py"):
        stage(repo, n, T_BEFORE)
    out = call(repo, tmp_path)
    for n in ("a-one.py", "b-two.py", "c-three.py"):
        assert n in out


# --------------------------------------------------------------------------
# the on-disk footprint — `test_on_disk_artifact_names.py` names this file as
# where these are pinned, so the entry there is a claim these tests must honour
# --------------------------------------------------------------------------

def test_the_cache_directory_literal_is_pinned():
    """🔴 The hook is listed in `PINNED_HERE` over in
    `test_on_disk_artifact_names.py`, and that entry says the names live here.
    A rename that nothing asserts on leaves orphaned state under `~/.cache` that
    nobody can find to clear."""
    src = HOOK.read_text(encoding="utf-8")
    assert 'CACHE_DIR = "~/.cache/claude-git-add-provenance"' in src


def test_the_session_id_is_sanitised_into_the_filename(repo: Path, tmp_path: Path):
    """A session id is opaque and goes into a filename. An id containing `/`
    would otherwise write outside the cache directory."""
    stage(repo, "memory-detail", T_BEFORE)
    assert "memory-detail" in call(repo, tmp_path, session="a/../../b c")
    d = tmp_path / ".cache" / "claude-git-add-provenance"
    names = [p.name for p in d.iterdir()]
    assert names == ["a_.._.._b_c"], names


def test_the_dedupe_ledger_lands_in_that_directory(repo: Path, tmp_path: Path):
    stage(repo, "memory-detail", T_BEFORE)
    call(repo, tmp_path, session="ledger-1")
    f = tmp_path / ".cache" / "claude-git-add-provenance" / "ledger-1"
    assert f.is_file()
    assert "memory-detail" in f.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# 🔴 POSITIVE CONTROL on the instrument itself
# --------------------------------------------------------------------------

def test_the_diff_filter_A_query_really_does_list_a_staged_new_file(repo: Path):
    """`claude/RULES.md`: a reassuring zero is indistinguishable from a query
    wired to nothing. Every silent test above rests on this command returning
    the file when there IS one — so watch the number move."""
    before = _sh("git", "diff", "--cached", "--name-only", "--diff-filter=A",
                 cwd=repo)
    assert before.strip() == "", "fixture leaked a staged add"
    stage(repo, "control.py", T_BEFORE)
    after = _sh("git", "diff", "--cached", "--name-only", "--diff-filter=A",
                cwd=repo)
    assert "control.py" in after
