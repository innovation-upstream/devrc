"""`handoff_doc.py` logs ONE JSONL row per write attempt, and the reader turns it into rates.

WHAT THIS SUITE IS FOR, AND THE MEASUREMENT IT PROTECTS
-------------------------------------------------------
`handoff_doc.py` is the /handoff write gate and had ZERO logging, so the cost it
is believed to impose — a SECOND PASS, where a session composes content, is
refused at rule (p)/(q)/(r), picks an exit flag and re-runs — was unmeasurable.
`git log` records only the committed end state, so it cannot tell

    "evicted BEFORE composing"  (#2001's widened warning band worked)
    "evicted AFTER a refusal"   (it did not, and a pass was paid)

apart, and that ordering is the entire claim. The refusals leave no commit at all,
which is why a logger that recorded only successes would measure nothing.

🔴 EVERY BEHAVIOURAL TEST HERE DRIVES THE REAL CLI IN A REAL THROWAWAY GIT REPO
with a real local bare remote, exactly as `test_handoff_doc.py` does, and reads the
JSONL file off disk. A test that called `handoff_writelog.record` directly would
prove the writer works and nothing about whether the gate reaches it — which is
the "verified in isolation" shape `claude/RULES.md` names: the defect lives in the
seam, and the seam here is thirty-nine `return` sites.

🔴 WHAT IS AN INVARIANT GUARD HERE AND WHAT IS REGRESSION COVERAGE. The behavioural
tests are regression coverage: on pre-change code there is no log file at all, so
they are RED at `origin/main`. `TestTheReturnLedger` is an INVARIANT GUARD — it
pins that every `return` in `_write_gate` goes through `_done`, a function that
does not exist on pre-change code, so it ERRORS rather than failing there. It is
labelled as one and is not counted as regression coverage; its value is forward,
against the fortieth exit path somebody adds.

Nothing here touches a real repository, a real remote, or the operator's real log:
every path is under pytest's `tmp_path`, and every run exports
`DEVRC_HANDOFF_WRITELOG` into it.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from testlib import hermetic_git  # noqa: E402

import handoff_doc as hd  # noqa: E402
import handoff_writelog as wl  # noqa: E402

TOOL = REPO_ROOT / "scripts" / "lib" / "handoff_doc.py"
READER = REPO_ROOT / "scripts" / "lib" / "handoff_writelog.py"

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    **hermetic_git.MAINTENANCE_OFF,
}

# 🔴 PAIRWISE-DISTINCT FIXTURE VALUES, AND DISTINCT FROM EVERY CONSTANT THE
# ASSERTIONS NAME. A fixture whose numbers can only ever produce the value a
# constant already holds cannot see a mutant that hardcodes that constant — it
# SURVIVES a fully green suite. So: the session ids below share no prefix, the
# byte sizes in the synthetic-log tests are mutually prime-ish and none equals
# `handoff_budget.MAX_BYTES`, `GRANDFATHER_STEP` or `BUDGET_NEAR_BYTES`, and the
# two topics differ in more than one character.
SESSION_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
SESSION_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"

BASE_DOC = """# Handoff: widget-drain — 2026-08-01

## Goal
Stop the widget queue dropping work under load.
- closing-condition: check — `python3 tools/queue_probe.py --for 240` reports 30/s

## State now
- Branch / PR: `feat/widget` / none
- What's DONE this session: the drain instrumentation landed
- Deploy/verify status: NOT deployed

## Gotchas / decisions / dead-ends
- Bumping the pool size did nothing; the ceiling is not connections.
- MEASURED: raising the worker count to 64 moved the drain rate by 0.04/s.
"""

UPDATE_DOC = """## State now
- Branch / PR: `feat/widget` / #41
- What's DONE this session: the drain loop is fixed
- Deploy/verify status: deployed, verified against the real path

## Gotchas / decisions / dead-ends
- The retry budget is decremented in the wrapper, not the client.
"""

ADVANCED = "the drain loop is fixed and the retry budget was traced"


def _sh(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True,
        env=dict(os.environ, **GIT_ENV),
    )
    assert proc.returncode == 0, f"{args} failed: {proc.stderr or proc.stdout}"
    return proc.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repo with one commit, a handoff doc, and a bare remote to push to."""
    origin = tmp_path / "origin.git"
    _sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    _sh("git", "init", "-q", "-b", "main", cwd=work)
    for k, v in (("user.name", "Test Runner"),
                 ("user.email", "test@example.invalid"),
                 ("commit.gpgsign", "false")):
        _sh("git", "config", k, v, cwd=work)
    _sh("git", "remote", "add", "origin", str(origin), cwd=work)
    docs = work / "claudedocs"
    docs.mkdir()
    (docs / "handoff-widget-drain.md").write_text(BASE_DOC, encoding="utf-8")
    _sh("git", "add", "--", "claudedocs/handoff-widget-drain.md", cwd=work)
    _sh("git", "commit", "-q", "-m", "seed", cwd=work)
    _sh("git", "push", "-q", "origin", "main", cwd=work)
    return work


@pytest.fixture()
def logfile(tmp_path: Path) -> Path:
    """Where this test's rows land. OUTSIDE the fixture repo, deliberately.

    🔴 `tmp_path` ITSELF HOLDS NO `.git` AND THE FIXTURE REPO IS A SUBDIRECTORY OF
    IT, which is what makes this path legal under the module's promise 2. A log
    placed inside `repo` would be REFUSED — see
    `test_a_log_path_inside_a_repo_working_tree_is_refused`, which is that
    behaviour's own test rather than an accident of this fixture.
    """
    return tmp_path / "handoff-writes.jsonl"


@pytest.fixture()
def update_file(tmp_path: Path) -> Path:
    p = tmp_path / "update.md"
    p.write_text(UPDATE_DOC, encoding="utf-8")
    return p


def run_tool(
    repo: Path,
    logfile: Path,
    *extra: str,
    update: Path | None = None,
    advanced: str | None = ADVANCED,
    topic: str = "widget-drain",
    session: str | None = SESSION_A,
    env_extra: dict[str, str] | None = None,
):
    """The real CLI, with the telemetry log pointed into `tmp_path`.

    🔴 THE SESSION ID IS INJECTED RATHER THAN INHERITED. `resolve_session_id`
    reads the ambient `CLAUDE_CODE_SESSION_ID`/`OPENCODE_SESSION_ID` and then falls
    back to walking `/proc` for a Claude ancestor — so a suite run from inside a
    real session would silently attribute every row to THAT session, and the
    two-session grouping test would pass for the wrong reason. Both names are set
    (one to the value, one removed) so exactly one answer is reachable.
    """
    argv = [sys.executable, str(TOOL), "--repo", str(repo), "--topic", topic]
    if update is not None:
        argv += ["--update", str(update)]
    if advanced is not None:
        argv += ["--advanced", advanced]
    argv += list(extra)
    env = dict(os.environ, **GIT_ENV, DEVRC_HANDOFF_WRITELOG=str(logfile))
    env.pop("OPENCODE_SESSION_ID", None)
    if session is None:
        # 🔴 BOTH RESOLVERS HAVE TO BE BLOCKED, OR "no session" SILENTLY BECOMES
        # "this suite's own session" — which is how this arm came to SKIP on every
        # developer host on its first draft, and a skip nobody counts is a pass.
        # The env half is `""` (which `session_trailer.valid_id` rejects); the
        # `/proc` half is `DEVRC_SESSION_TRAILER_ROOT` pointed at an empty
        # directory, so `lookup()` finds no state file for whatever Claude ancestor
        # it discovers and returns None. That variable is `session_trailer`'s own
        # documented override, not a hook invented here.
        env["CLAUDE_CODE_SESSION_ID"] = ""
        empty = logfile.parent / "no-session-state"
        empty.mkdir(exist_ok=True)
        env["DEVRC_SESSION_TRAILER_ROOT"] = str(empty)
    else:
        env["CLAUDE_CODE_SESSION_ID"] = session
    if env_extra:
        env.update(env_extra)
    return subprocess.run(argv, capture_output=True, text=True, env=env)


def rows(logfile: Path) -> list[dict]:
    assert logfile.exists(), (
        f"no telemetry log at {logfile}: the write gate did not log this attempt"
    )
    out = []
    for line in logfile.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def only_row(logfile: Path) -> dict:
    got = rows(logfile)
    assert len(got) == 1, f"expected exactly one row, got {len(got)}: {got}"
    return got[0]


# ---------------------------------------------------------------------------
# the row itself
# ---------------------------------------------------------------------------


class TestOneRowPerInvocation:
    def test_a_proposal_run_logs_one_row_with_the_printed_status(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """REGRESSION. On pre-change code no log file exists at all, so `rows`
        fails on its own assertion."""
        proc = run_tool(repo, logfile, update=update_file)
        assert proc.returncode == 0, proc.stderr
        assert "status=proposed" in proc.stdout
        row = only_row(logfile)
        assert row["status"] == "proposed"
        assert row["exit_code"] == 0
        assert row["confirmed"] is False
        assert row["doc"] == "claudedocs/handoff-widget-drain.md"
        assert row["repo"] == "work"
        assert row["session"] == SESSION_A
        assert row["schema"] == wl.SCHEMA
        assert row["ts"].endswith("Z")

    def test_a_confirmed_push_logs_pushed_and_the_bytes_that_landed(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """REGRESSION, and the byte fields are the load-bearing half.

        `bytes_after` must be the size of what was WRITTEN, measured against the
        file on disk rather than against a number this test computed — a derived
        expectation is the shape `claude/RULES.md` forbids.
        """
        proc = run_tool(repo, logfile, "--confirm", "--push", update=update_file)
        assert proc.returncode == 0, proc.stderr
        assert "status=pushed" in proc.stdout
        row = only_row(logfile)
        assert row["status"] == "pushed"
        assert row["exit_code"] == 0
        assert row["confirmed"] is True
        landed = (repo / "claudedocs" / "handoff-widget-drain.md").read_bytes()
        assert row["bytes_after"] == len(landed)
        assert row["bytes_before"] == len(BASE_DOC.encode("utf-8"))
        assert row["bytes_after"] > row["bytes_before"], (
            "this fixture APPENDS a gotcha, so the write must grow the doc — if it "
            "does not, the shrank/grew classification below is being fed a "
            "direction the fixture cannot produce"
        )

    def test_the_two_run_shape_logs_two_rows_and_only_one_is_confirmed(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """🔴 THE FIELD THE WHOLE SECOND-PASS RATE RESTS ON.

        `handoff_doc` has a deliberate two-run shape: a proposal run that writes
        nothing, then an identical run with `--confirm --push`. Without
        `confirmed`, every ordinary write looks like two attempts and any
        second-pass rate is inflated ~2x BY CONSTRUCTION.
        """
        run_tool(repo, logfile, update=update_file)
        run_tool(repo, logfile, "--confirm", "--push", update=update_file)
        got = rows(logfile)
        assert [r["status"] for r in got] == ["proposed", "pushed"]
        assert [r["confirmed"] for r in got] == [False, True]

    def test_a_refusal_before_the_document_is_read_logs_null_sizes(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """`--advanced` missing -> rule (d) refuses before anything is computed.

        The sizes are `null` rather than `0`: nothing was measured, and a `0` here
        would read as an empty document.
        """
        proc = run_tool(repo, logfile, update=update_file, advanced=None)
        assert proc.returncode == hd.EXIT_NO_ADVANCE
        row = only_row(logfile)
        assert row["status"] == "no-advance"
        assert row["exit_code"] == hd.EXIT_NO_ADVANCE
        assert row["bytes_before"] is None
        assert row["bytes_after"] is None
        assert row["allowance"] is None

    def test_a_BRAND_NEW_doc_records_a_null_bytes_before_and_not_a_zero(
        self, repo: Path, logfile: Path, tmp_path: Path
    ):
        """🔴 `null` AND `0` ARE DIFFERENT FACTS, and this is the only case that
        can tell them apart.

        A doc that does not exist has NO before; a TRACKED doc someone emptied is
        0 bytes and that 0 is applicable. Conflating them makes every new doc look
        like a shrink from 0 — which would score as `warning acted upon` in the
        reader for a document nobody pruned.

        ⚠ MEASURED: the obvious candidate for this claim — the `no-advance`
        refusal, whose row also carries `null` — CANNOT see it. That arm returns
        BEFORE `_stamp_size` is ever called, so its `null` comes from `Attempt`'s
        default and a mutant that dropped the `base_exists` conditional SURVIVED it
        on a green suite. This case reaches the stamp with no document on disk,
        which is the only place the conditional executes.
        """
        update = tmp_path / "whole-doc.md"
        update.write_text(
            "## Goal\nA brand new effort.\n"
            "- closing-condition: check — `true` exits 0\n\n"
            "## State now\n- Branch / PR: `feat/new` / none\n",
            encoding="utf-8",
        )
        topic = "a-brand-new-effort"
        assert not (repo / f"claudedocs/handoff-{topic}.md").exists()
        proc = run_tool(
            repo, logfile, "--new-effort", update=update, topic=topic)
        assert proc.returncode == 0, proc.stderr
        row = only_row(logfile)
        assert row["status"] == "proposed"
        assert isinstance(row["bytes_after"], int) and row["bytes_after"] > 0, (
            "the stamp was not reached, so this case cannot see the conditional"
        )
        assert row["bytes_before"] is None, (
            f"a doc that does not exist recorded bytes_before={row['bytes_before']!r}; "
            f"`0` here makes every new doc read as a shrink from zero"
        )

    def test_an_argument_refusal_logs_a_null_status_and_its_exit_code(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """🔴 `null` IS THE HONEST STATUS FOR AN ARM THAT PRINTS NO TOKEN, and the
        exit code is what discriminates it.

        `--push` without `--confirm` is exit 2 and prints no `status=` line. A
        derived token here would claim the transcript contains something it does
        not. The assertion is two-sided: `status` null AND no `status=` on either
        stream, so it cannot pass by the tool having quietly started printing one.
        """
        proc = run_tool(repo, logfile, "--push", update=update_file)
        assert proc.returncode == hd.EXIT_USAGE
        assert "status=" not in proc.stdout
        assert "status=" not in proc.stderr
        row = only_row(logfile)
        assert row["status"] is None
        assert row["exit_code"] == hd.EXIT_USAGE

    def test_an_unresolvable_session_is_null_and_never_a_placeholder(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """🔴 A PLACEHOLDER WOULD MERGE EVERY UNATTRIBUTED RUN INTO ONE SESSION,
        which is the exact defect `resolve_session_id` returns "" to prevent. The
        row must carry JSON `null`, not `""` and not `"unknown"`.

        ⚠ MEASURED RATHER THAN ASSUMED: `resolve_session_id` falls back to walking
        `/proc`, so this asserts on what came back and SKIPS when the fallback
        found a real ancestor — a pass that depended on the suite's own launcher
        would be a fact about the launcher.
        """
        proc = run_tool(repo, logfile, update=update_file, session=None)
        assert proc.returncode == 0, proc.stderr
        row = only_row(logfile)
        assert row["session"] is None, (
            "a session id was resolved despite BOTH resolvers being blocked — so "
            "either a third one exists or `DEVRC_SESSION_TRAILER_ROOT` no longer "
            f"governs `session_trailer.lookup`: {row['session']!r}"
        )

    def test_an_empty_session_id_becomes_null_and_never_an_empty_string(self):
        """The unit claim behind the row above, and it cannot skip. `""` is
        `resolve_session_id`'s "unresolvable", and a row must carry JSON `null`:
        an empty string would group as a real session id in the reader."""
        assert wl.Attempt(session="").row()["session"] is None
        assert wl.Attempt(session=None).row()["session"] is None
        assert wl.Attempt(session=SESSION_B).row()["session"] == SESSION_B

    def test_the_exit_flags_a_second_pass_carries_are_recorded(
        self, repo: Path, update_file: Path, logfile: Path, tmp_path: Path
    ):
        """🔴 HOW A SECOND PASS IS RECOGNISED: a refusal, then a re-run carrying an
        exit flag. `prune_requested` is derived from the same list, so the two
        cannot disagree.

        ⚠ `--override-size-ratchet` is used rather than `--prune`, because a prune
        needs a file whose lines resolve in the document and this test is about the
        FLAG being recorded, not about rule (q).
        """
        proc = run_tool(
            repo, logfile, "--override-size-ratchet", "the writeup lands tonight",
            update=update_file,
        )
        assert proc.returncode == 0, proc.stderr
        row = only_row(logfile)
        assert row["exit_flags"] == ["--override-size-ratchet"]
        assert row["prune_requested"] is True

    def test_an_ordinary_run_carries_no_exit_flags(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """The negative control for the row above. Without it, `prune_requested`
        could be hardcoded `True` and the test above would still pass."""
        run_tool(repo, logfile, update=update_file)
        row = only_row(logfile)
        assert row["exit_flags"] == []
        assert row["prune_requested"] is False

    def test_the_log_file_is_created_0600(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        run_tool(repo, logfile, update=update_file)
        assert stat.S_IMODE(logfile.stat().st_mode) == 0o600

    def test_no_document_content_reaches_the_row(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """🔴 PROMISE 3, AS A BEHAVIOURAL CHECK RATHER THAN A FIELD-LIST REVIEW.

        Every distinctive sentence of the base, the update and the `--advanced`
        argument is searched for in the RAW line. A field-list review is walkable
        by a field added later; a scan of the bytes is not.
        """
        run_tool(repo, logfile, "--confirm", update=update_file)
        raw = logfile.read_text(encoding="utf-8")
        for secret in (
            "retry budget", "pool size", "queue_probe", "drain loop",
            ADVANCED, "closing-condition",
        ):
            assert secret not in raw, (
                f"{secret!r} reached the telemetry row — promise 3 says sizes, "
                f"statuses and paths only"
            )


# ---------------------------------------------------------------------------
# every exit path logs — enumerated, not asserted
# ---------------------------------------------------------------------------


def _unforced_update(tmp_path: Path) -> Path:
    p = tmp_path / "unforced.md"
    p.write_text(
        "## Next steps (ranked)\n1. Watch the drain rate for a day.\n",
        encoding="utf-8",
    )
    return p


def _unevidenced_update(tmp_path: Path) -> Path:
    p = tmp_path / "unevidenced.md"
    p.write_text(
        "## Open investigations — live diagnosis state\n"
        "### the drain stalls on a lock\n"
        "- as-of: 2026-09-01\n"
        "- **Ruled out:** not a per-shard issue.\n",
        encoding="utf-8",
    )
    return p


def _no_change_update(tmp_path: Path) -> Path:
    """A delta that reproduces the base's own `## State now` verbatim."""
    p = tmp_path / "nochange.md"
    start = BASE_DOC.index("## State now")
    end = BASE_DOC.index("## Gotchas")
    p.write_text(BASE_DOC[start:end].rstrip("\n") + "\n", encoding="utf-8")
    return p


class TestEveryExitPathLogs:
    """🔴 ENUMERATED, ONE CASE PER `status=` TOKEN THE MODULE CAN PRINT.

    The brief for this work says "do not hand-pick a subset", and an assertion that
    coverage is complete is not coverage. So: one reachable case per token, each
    asserting BOTH the status the row carries AND the exit code — and
    `TestTheReturnLedger` below closes the gap a per-case list structurally cannot,
    which is a FORTIETH exit path nobody wrote a case for.
    """

    def test_dated_topic(self, repo: Path, update_file: Path, logfile: Path):
        proc = run_tool(repo, logfile, update=update_file, topic="widget-2026-08-01")
        assert proc.returncode == hd.EXIT_DOC_PER_EFFORT
        assert only_row(logfile)["status"] == "dated-topic"

    def test_new_doc(self, repo: Path, update_file: Path, logfile: Path):
        proc = run_tool(repo, logfile, update=update_file, topic="a-second-effort")
        assert proc.returncode == hd.EXIT_DOC_PER_EFFORT
        row = only_row(logfile)
        assert row["status"] == "new-doc"
        assert row["doc"] == "claudedocs/handoff-a-second-effort.md"

    def test_no_advance(self, repo: Path, update_file: Path, logfile: Path):
        proc = run_tool(repo, logfile, update=update_file, advanced=None)
        assert proc.returncode == hd.EXIT_NO_ADVANCE
        assert only_row(logfile)["status"] == "no-advance"

    def test_unforced(self, repo: Path, logfile: Path, tmp_path: Path):
        proc = run_tool(repo, logfile, update=_unforced_update(tmp_path))
        assert proc.returncode == hd.EXIT_UNFORCED
        assert only_row(logfile)["status"] == "unforced"

    def test_unevidenced(self, repo: Path, logfile: Path, tmp_path: Path):
        proc = run_tool(repo, logfile, update=_unevidenced_update(tmp_path))
        assert proc.returncode == hd.EXIT_UNEVIDENCED
        assert only_row(logfile)["status"] == "unevidenced"

    def test_no_change(self, repo: Path, logfile: Path, tmp_path: Path):
        proc = run_tool(repo, logfile, update=_no_change_update(tmp_path))
        assert proc.returncode == hd.EXIT_NO_CHANGE
        row = only_row(logfile)
        assert row["status"] == "no-change"
        # The sizes ARE known on this arm — the merge happened, it just changed
        # nothing — which is what distinguishes it from the `no-advance` row.
        assert row["bytes_after"] == row["bytes_before"]

    def test_proposed(self, repo: Path, update_file: Path, logfile: Path):
        run_tool(repo, logfile, update=update_file)
        assert only_row(logfile)["status"] == "proposed"

    def test_written_without_push(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        proc = run_tool(repo, logfile, "--confirm", update=update_file)
        assert proc.returncode == 0, proc.stderr
        assert "status=written" in proc.stdout
        assert only_row(logfile)["status"] == "written"

    def test_pushed(self, repo: Path, update_file: Path, logfile: Path):
        run_tool(repo, logfile, "--confirm", "--push", update=update_file)
        assert only_row(logfile)["status"] == "pushed"

    def test_behind(
        self, repo: Path, update_file: Path, logfile: Path, tmp_path: Path
    ):
        """The remote moves under us -> rule EXIT_BEHIND, nothing written."""
        other = tmp_path / "other"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other),
            cwd=tmp_path)
        for k, v in (("user.name", "Other"), ("user.email", "o@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        (other / "unrelated.md").write_text("elsewhere\n", encoding="utf-8")
        _sh("git", "add", "--", "unrelated.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "other session", cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        proc = run_tool(repo, logfile, "--confirm", "--push", update=update_file)
        assert proc.returncode == hd.EXIT_BEHIND, proc.stderr
        assert only_row(logfile)["status"] == "behind"

    def test_stale_base(
        self, repo: Path, update_file: Path, logfile: Path, tmp_path: Path
    ):
        """The mainline is AHEAD on this doc and the working copy is empty.

        🔴 EMPTYING THE LOCAL COPY IS NOT ENOUGH, AND THAT IS MEASURED RATHER THAN
        CAUTIONARY: `replaces_mainline_doc` needs `currency.mainline` populated,
        which happens only when `<mainline>` carries commits to the doc that this
        checkout does not. A first draft of this test emptied the file and got
        exit 0. So another clone pushes to the doc, this one FETCHES WITHOUT
        MERGING — exactly the state the rule exists to judge — and only then is the
        working copy emptied.
        """
        other = tmp_path / "other-session"
        _sh("git", "clone", "-q", str(tmp_path / "origin.git"), str(other),
            cwd=tmp_path)
        for k, v in (("user.name", "Other"), ("user.email", "o@example.invalid"),
                     ("commit.gpgsign", "false")):
            _sh("git", "config", k, v, cwd=other)
        doc_there = other / "claudedocs" / "handoff-widget-drain.md"
        doc_there.write_text(
            BASE_DOC + "- authored in the other clone, never merged here\n",
            encoding="utf-8")
        _sh("git", "add", "--", "claudedocs/handoff-widget-drain.md", cwd=other)
        _sh("git", "commit", "-q", "-m", "the real handoff, authored elsewhere",
            cwd=other)
        _sh("git", "push", "-q", "origin", "main", cwd=other)
        _sh("git", "fetch", "-q", "origin", cwd=repo)
        (repo / "claudedocs" / "handoff-widget-drain.md").write_text(
            "", encoding="utf-8")
        proc = run_tool(repo, logfile, "--confirm", update=update_file)
        assert proc.returncode == hd.EXIT_STALE_BASE, proc.stderr
        assert only_row(logfile)["status"] == "stale-base"

    def test_failed(self, repo: Path, update_file: Path, logfile: Path):
        """`--repo` is a git repo but its remote does not exist -> exit 3.

        🔴 THIS IS THE `status=failed` ARM AND IT IS REACHED THROUGH THE REMOTE
        CHECK, not through a broken file: the `--push` pre-flight cannot answer
        whether the remote moved, so it refuses BEFORE writing.
        """
        proc = run_tool(
            repo, logfile, "--confirm", "--push", "--remote", "nosuchremote",
            update=update_file,
        )
        assert proc.returncode == hd.EXIT_FAIL, (proc.returncode, proc.stderr)
        assert "status=failed" in proc.stderr
        assert only_row(logfile)["status"] == "failed"

    def test_not_a_git_repo_logs_a_null_status(
        self, update_file: Path, logfile: Path, tmp_path: Path
    ):
        """One of the three environment arms: exit 3, no `status=` token."""
        bare = tmp_path / "notarepo"
        bare.mkdir()
        proc = run_tool(bare, logfile, update=update_file)
        assert proc.returncode == hd.EXIT_FAIL
        row = only_row(logfile)
        assert row["status"] is None
        assert row["exit_code"] == hd.EXIT_FAIL

    def test_an_unreadable_update_logs_a_null_status(
        self, repo: Path, logfile: Path, tmp_path: Path
    ):
        proc = run_tool(repo, logfile, update=tmp_path / "does-not-exist.md")
        assert proc.returncode == hd.EXIT_FAIL
        row = only_row(logfile)
        assert row["status"] is None
        assert row["exit_code"] == hd.EXIT_FAIL

    def test_prune_refused(self, repo: Path, logfile: Path, tmp_path: Path):
        """A `--prune` naming a line the document does not carry -> exit 15."""
        prune = tmp_path / "prune.txt"
        prune.write_text("- a line this document has never contained\n",
                         encoding="utf-8")
        proc = run_tool(
            repo, logfile, "--prune", str(prune), "--prune-count", "1",
        )
        assert proc.returncode == hd.EXIT_PRUNE_REFUSED, proc.stderr
        row = only_row(logfile)
        assert row["status"] == "prune-refused"
        # 🔴 THE SIZES ARE PRESENT ON A REFUSAL, which is the whole reason for the
        # provisional stamp above rule (q). A refusal leaves no commit, so this row
        # is the only place these bytes will ever exist.
        assert isinstance(row["bytes_after"], int)
        assert isinstance(row["allowance"], int)

    def test_prune_unconserved(self, repo: Path, logfile: Path, tmp_path: Path):
        """A prune removing a DURABLE-looking line with no archive -> exit 16."""
        prune = tmp_path / "prune.txt"
        # 🔴 THE `MEASURED` LINE, NOT THE OTHER ONE. `durable_reason` flags an
        # EVIDENCE VERB (`_EVIDENCE_VERB`: MEASURED/RETRACTED/CLOSED/…) or a bare
        # ISO date; the plain "pool size" gotcha carries neither, so pruning THAT
        # one exits 0 and this case would be vacuous. Measured: it did, before this
        # comment existed.
        durable = (
            "- MEASURED: raising the worker count to 64 moved the drain rate by "
            "0.04/s.\n"
        )
        assert durable.strip() in BASE_DOC, "the fixture no longer carries it"
        assert hd.durable_reason(durable) == hd.DURABLE_EVIDENCE, (
            "rule (f) does not read this line as durable, so rule (r) cannot refuse "
            "it and this case is vacuous"
        )
        prune.write_text(durable, encoding="utf-8")
        proc = run_tool(
            repo, logfile, "--prune", str(prune), "--prune-count", "1",
        )
        assert proc.returncode == hd.EXIT_PRUNE_UNCONSERVED, proc.stderr
        assert only_row(logfile)["status"] == "prune-unconserved"

    def test_size_ratchet(self, repo: Path, logfile: Path, tmp_path: Path):
        """A doc already over its own ceiling, and this update GROWS it -> 14.

        🔴 THE DOC IS PADDED PAST `handoff_budget.MAX_BYTES` RATHER THAN THE TEST
        ASSERTING A SIZE, because the ceiling is `handoff_budget`'s to own. The pad
        is derived from that constant at run time, so a changed ceiling moves the
        fixture with it instead of leaving a silently-unreachable case.
        """
        import handoff_budget

        doc = repo / "claudedocs" / "handoff-widget-drain.md"
        pad = "\n".join(
            f"- padding line {i} with no durable marker" for i in range(4000))
        doc.write_text(BASE_DOC + pad + "\n", encoding="utf-8")
        assert len(doc.read_bytes()) > handoff_budget.MAX_BYTES, (
            "the fixture did not actually cross the ceiling, so rule (p) cannot "
            "fire and this case is vacuous"
        )
        _sh("git", "add", "--", "claudedocs/handoff-widget-drain.md", cwd=repo)
        _sh("git", "commit", "-q", "-m", "pad", cwd=repo)
        update = tmp_path / "grow.md"
        update.write_text(
            "## Gotchas / decisions / dead-ends\n- one more gotcha\n",
            encoding="utf-8",
        )
        proc = run_tool(repo, logfile, update=update)
        assert proc.returncode == hd.EXIT_SIZE_RATCHET, proc.stderr
        row = only_row(logfile)
        assert row["status"] == "size-ratchet"
        assert row["bytes_after"] > row["allowance"], (
            "rule (p) fired, so the row must show the doc past its OWN allowance"
        )

    def test_undefined_done(self, repo: Path, logfile: Path, tmp_path: Path):
        """A NEW doc that declares no closing condition -> exit 11."""
        update = tmp_path / "nofinish.md"
        update.write_text(
            "## Goal\nSomething with no finish line.\n\n"
            "## State now\n- Branch / PR: `feat/x` / none\n",
            encoding="utf-8",
        )
        proc = run_tool(
            repo, logfile, "--new-effort", update=update, topic="brand-new-effort")
        assert proc.returncode == hd.EXIT_UNDEFINED_DONE, proc.stderr
        assert only_row(logfile)["status"] == "undefined-done"

    def test_rank_growth(self, repo: Path, logfile: Path, tmp_path: Path):
        """The self-generated half of the ranked queue grows -> exit 12."""
        doc = repo / "claudedocs" / "handoff-widget-drain.md"
        doc.write_text(
            BASE_DOC
            + "\n## Next steps (ranked)\n"
            + "1. Read the wrapper. forcing: none — self-generated\n",
            encoding="utf-8",
        )
        _sh("git", "add", "--", "claudedocs/handoff-widget-drain.md", cwd=repo)
        _sh("git", "commit", "-q", "-m", "queue", cwd=repo)
        update = tmp_path / "grew.md"
        update.write_text(
            "## Next steps (ranked)\n"
            "1. Read the wrapper. forcing: none — self-generated\n"
            "2. Read the client too. forcing: none — self-generated\n"
            "3. And the pool. forcing: none — self-generated\n",
            encoding="utf-8",
        )
        proc = run_tool(repo, logfile, update=update)
        assert proc.returncode == hd.EXIT_RANK_GROWTH, proc.stderr
        assert only_row(logfile)["status"] == "rank-growth"

    def test_leak_refused(self, repo: Path, update_file: Path, logfile: Path):
        """The repo's own scanner refuses the delta -> exit 13, nothing written.

        A `tests/leakscan.py` that always exits non-zero is planted in the fixture
        repo: rule (o) treats ANY non-zero as a refusal, deliberately.
        """
        tests = repo / "tests"
        tests.mkdir()
        (tests / "leakscan.py").write_text(
            "import sys\nprint('planted refusal')\nsys.exit(1)\n", encoding="utf-8")
        _sh("git", "add", "--", "tests/leakscan.py", cwd=repo)
        _sh("git", "commit", "-q", "-m", "scanner", cwd=repo)
        proc = run_tool(repo, logfile, "--confirm", update=update_file)
        assert proc.returncode == hd.EXIT_LEAK_REFUSED, proc.stderr
        assert only_row(logfile)["status"] == "leak-refused"


class TestTheReturnLedger:
    """🔴 INVARIANT GUARD, LABELLED AS ONE. It pins a shape no bug has violated.

    `_write_gate` returns from thirty-nine places. A fortieth added later that
    returns a bare `EXIT_*` would log `status=null, exit_code=null` — a silently
    unattributed attempt, which is exactly the hole this work exists to close and
    the one a per-case list above cannot see. This reads the module's own AST, in
    the shape `api.DeclaredRoutes()` and `cli_verbs_from_parser` use in the sibling
    `cairn` repo: a ledger a compiled-away or renamed site cannot satisfy.

    It is NOT regression coverage — on pre-change code `_write_gate` does not
    exist, so it errors rather than failing, and it is not counted.
    """

    @staticmethod
    def _gate() -> ast.FunctionDef:
        tree = ast.parse(Path(hd.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_write_gate":
                return node
        raise AssertionError("`_write_gate` is not a top-level function any more")

    def test_every_return_in_the_write_gate_goes_through_done(self):
        bare = []
        for node in ast.walk(self._gate()):
            if not isinstance(node, ast.Return):
                continue
            value = node.value
            ok = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "_done"
            )
            if not ok:
                bare.append(node.lineno)
        assert not bare, (
            "these `return`s in `_write_gate` do not go through `_done`, so the "
            f"attempts they end are logged with a null status: lines {bare}. Wrap "
            "each as `return _done(log, \"<the status= token it prints>\", EXIT_X)`."
        )

    def test_the_ledger_is_not_vacuous(self):
        """POSITIVE CONTROL on the guard above: a zero `return` count would make
        it pass over an empty function."""
        returns = [n for n in ast.walk(self._gate()) if isinstance(n, ast.Return)]
        assert len(returns) >= 30, (
            f"`_write_gate` has only {len(returns)} return(s); the guard above is "
            f"reading the wrong function or the gate was restructured"
        )

    def test_every_status_done_is_called_with_is_one_the_module_prints(self):
        """🔴 THE TOKENS ARE NOT A FREE VOCABULARY. A row must carry the token the
        run PRINTS, so every literal handed to `_done` has to appear as a
        `status=<token>` somewhere in the module's source. A typo'd label would
        otherwise be invisible — it logs fine and matches nothing a human greps.
        """
        source = Path(hd.__file__).read_text(encoding="utf-8")
        labels = set()
        for node in ast.walk(self._gate()):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_done" and len(node.args) >= 2):
                arg = node.args[1]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    labels.add(arg.value)
        assert labels, "no status literals found — the walk is reading nothing"
        missing = sorted(t for t in labels if f"status={t}" not in source)
        assert not missing, (
            f"these `_done` labels are printed nowhere in {Path(hd.__file__).name}: "
            f"{missing}"
        )


class TestTheExitFlagLedger:
    def test_every_exit_flag_dest_is_a_real_parser_destination(self):
        """🔴 A `(flag, dest)` LEDGER IS WRONG SILENTLY. A renamed flag or dest
        leaves `exit_flags_passed` matching nothing and `prune_requested` False on
        every second pass — a reassuring zero. This asks the PARSER."""
        parser = hd.build_parser()
        options = {s for action in parser._actions for s in action.option_strings}
        dests = {action.dest for action in parser._actions}
        for flag, dest in hd.EXIT_FLAG_DESTS:
            assert flag in options, f"{flag} is not an option of build_parser()"
            assert dest in dests, f"{dest} is not a destination of build_parser()"

    def test_the_ledger_covers_every_flag_the_brief_names(self):
        assert {flag for flag, _ in hd.EXIT_FLAG_DESTS} == {
            "--prune", "--archive-write", "--autoevict", "--override-size-ratchet",
        }

    def test_autoevict_is_reported_as_passed_and_not_as_archive_write(self):
        """🔴 `_write_gate` SETS `args.archive_write = True` WHEN `--autoevict` IS
        PASSED. Reading the flags after that would report a flag the caller never
        typed, and the measurement is "what did the session RE-RUN with"."""
        args = hd.build_parser().parse_args(
            ["--repo", ".", "--topic", "t", "--update", "u", "--autoevict"])
        assert hd.exit_flags_passed(args) == ("--autoevict",)


# ---------------------------------------------------------------------------
# fail-open
# ---------------------------------------------------------------------------


class TestFailOpen:
    """🔴 LOGGING MUST NEVER BREAK OR ALTER A HANDOFF WRITE.

    `/handoff`'s write path is the ONLY step that records a session, and
    `~/.claude/hooks/handoff-write-guard.py` blocks Stop until one is written. A
    write gate that dies because a log path is unwritable is strictly worse than no
    logging at all.
    """

    def test_an_unwritable_log_directory_does_not_change_the_write(
        self, repo: Path, update_file: Path, tmp_path: Path
    ):
        """REGRESSION for the fail-open guard, and the mutation target.

        The log is pointed inside a directory with mode 0500, so `os.open` raises
        `PermissionError`. The assertions are about the GATE: its exit code and its
        own printed status, unchanged, and the document on disk actually updated.
        """
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "sub").mkdir()
        os.chmod(locked / "sub", 0o500)
        try:
            target = locked / "sub" / "writes.jsonl"
            proc = run_tool(repo, target, "--confirm", "--push", update=update_file)
            assert proc.returncode == 0, (
                "the handoff write did NOT fail open: an unwritable telemetry path "
                f"changed its exit code to {proc.returncode}.\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
            )
            assert "status=pushed" in proc.stdout, (
                "the handoff write did NOT fail open: an unwritable telemetry path "
                f"changed its printed status.\nstdout:\n{proc.stdout}"
            )
            assert not target.exists()
            landed = (repo / "claudedocs" / "handoff-widget-drain.md").read_text(
                encoding="utf-8")
            assert "decremented in the wrapper" in landed, (
                "the document was not updated, so the run did not actually do the "
                "work this test claims it fails open on"
            )
        finally:
            os.chmod(locked / "sub", 0o700)

    def test_record_swallows_a_non_serialisable_field(self):
        """🔴 PROMISE 1 IS WIDER THAN THE WRITE. Path resolution and
        serialisation are inside the same `try`: a `TypeError` from a field nothing
        can encode is the same fact as a full disk from the gate's point of view.
        """
        attempt = wl.Attempt(repo="r", doc="claudedocs/handoff-x.md")
        attempt.status = "written"
        attempt.exit_code = 0
        attempt.bytes_after = object()  # type: ignore[assignment]
        assert wl.record(attempt, {"XDG_STATE_HOME": "/nonexistent-root"}) == ""

    def test_a_log_path_inside_a_repo_working_tree_is_refused(
        self, repo: Path, update_file: Path
    ):
        """🔴 PROMISE 2. devrc is PUBLIC; a telemetry file under a checkout is one
        `git add -A` from being published. The refusal is silent and costs only the
        log — the write still succeeds.
        """
        target = repo / "claudedocs" / "writes.jsonl"
        proc = run_tool(repo, target, "--confirm", update=update_file)
        assert proc.returncode == 0, proc.stderr
        assert "status=written" in proc.stdout
        assert not target.exists(), (
            f"{target} was written INSIDE the repo working tree at {repo}"
        )

    def test_the_refusal_names_the_repo_it_found(self, repo: Path):
        """The diagnostic half, so a human can tell "disabled" from "refused"."""
        reason = wl.log_refusal(
            {wl.LOG_PATH_ENV: str(repo / "claudedocs" / "writes.jsonl")})
        assert str(repo) in reason and "working tree" in reason

    def test_a_worktrees_dotgit_FILE_is_recognised_as_a_repo(self, tmp_path: Path):
        """🔴 `.git` IS TESTED WITH `exists()`, NOT `is_dir()`, AND A WORKTREE IS
        THE COMMON CASE FOR THIS REPO'S AGENTS. A worktree's `.git` is a FILE
        holding `gitdir: …`; an `is_dir()` spelling would have admitted every one.

        This is the mutation-kill case for that spelling: with `is_dir()` the
        assertion below goes red on its own message.
        """
        fake_worktree = tmp_path / "wt"
        (fake_worktree / "claudedocs").mkdir(parents=True)
        (fake_worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n",
                                            encoding="utf-8")
        target = fake_worktree / "claudedocs" / "writes.jsonl"
        assert wl.resolve_log_path({wl.LOG_PATH_ENV: str(target)}) is None, (
            "a worktree's `.git` FILE was not recognised as a working tree, so a "
            "telemetry log would be written inside it"
        )

    def test_a_disable_value_turns_logging_off_without_touching_the_write(
        self, repo: Path, update_file: Path, tmp_path: Path
    ):
        proc = run_tool(
            repo, tmp_path / "unused.jsonl", "--confirm", update=update_file,
            env_extra={wl.LOG_PATH_ENV: "off"},
        )
        assert proc.returncode == 0, proc.stderr
        assert "status=written" in proc.stdout
        assert not (tmp_path / "unused.jsonl").exists()

    def test_a_fifo_at_the_target_is_refused_rather_than_blocking(
        self, tmp_path: Path
    ):
        """🔴 "DOES NOT RAISE" DOES NOT IMPLY "DOES NOT HANG", and a hang is
        STRICTLY WORSE than this module not existing: `open(..., 'a')` on a FIFO
        with no reader blocks forever. `hook_telemetry` measured that exact failure
        on a wired Stop hook (no return within 12 s).
        """
        fifo = tmp_path / "state" / "devrc" / wl.LOG_NAME
        fifo.parent.mkdir(parents=True)
        os.mkfifo(fifo)
        attempt = wl.Attempt(repo="r", doc="claudedocs/handoff-x.md")
        attempt.status = "written"
        attempt.exit_code = 0
        assert wl.record(attempt, {"XDG_STATE_HOME": str(tmp_path / "state")}) == ""


class TestWhereTheLogLives:
    def test_the_default_path_follows_xdg_state_home(self, tmp_path: Path):
        """🔴 READ AT CALL TIME, NEVER CAPTURED AT IMPORT. `run-tests.sh` GUARD 8
        exports `XDG_STATE_HOME` to a temp dir for every target, so a constant
        captured at import would be read before a test could move it — and a suite
        that forgot to override the log path would write to the operator's real one.
        """
        got = wl.default_log_path({wl.STATE_HOME_ENV: str(tmp_path)})
        assert got == tmp_path / wl.STATE_SUBDIR / wl.LOG_NAME

    def test_with_no_xdg_state_home_it_falls_back_under_home(self):
        got = wl.default_log_path({})
        assert got == Path.home() / ".local" / "state" / wl.STATE_SUBDIR / wl.LOG_NAME

    def test_the_override_wins_over_the_default(self, tmp_path: Path):
        target = tmp_path / "elsewhere" / "x.jsonl"
        assert wl.resolve_log_path(
            {wl.LOG_PATH_ENV: str(target), wl.STATE_HOME_ENV: str(tmp_path)}
        ) == target.resolve()


class TestConcurrency:
    def test_a_row_is_one_append_of_one_complete_line(self, tmp_path: Path):
        """🔴 ONE `O_APPEND` WRITE INCLUDING THE TRAILING NEWLINE, so interleaving
        cannot corrupt a record. Asserted two ways: the line never contains a raw
        newline, and the file ends in exactly one per row.
        """
        log = tmp_path / "x.jsonl"
        for i, status in enumerate(("proposed", "size-ratchet", "pushed")):
            attempt = wl.Attempt(repo="r", doc="claudedocs/handoff-x.md")
            attempt.status = status
            attempt.exit_code = i
            line = wl.record(attempt, {wl.LOG_PATH_ENV: str(log)})
            assert line and "\n" not in line
        text = log.read_text(encoding="utf-8")
        assert text.endswith("\n")
        assert len(text.splitlines()) == 3
        assert [json.loads(x)["status"] for x in text.splitlines()] == [
            "proposed", "size-ratchet", "pushed"]

    def test_the_line_fits_well_inside_PIPE_BUF(self, tmp_path: Path):
        """The atomicity claim is scoped to sub-`PIPE_BUF` writes (4096 on Linux),
        so the longest realistic row has to be measured rather than assumed."""
        attempt = wl.Attempt(
            repo="a-repo-with-a-fairly-long-name",
            doc="claudedocs/handoff-" + ("a-long-effort-slug-" * 6) + ".md",
            confirmed=True,
            exit_flags=("--prune", "--archive-write", "--autoevict",
                        "--override-size-ratchet"),
            session=SESSION_A,
        )
        attempt.status = "prune-unconserved"
        attempt.exit_code = 16
        attempt.bytes_before = 314159
        attempt.bytes_after = 271828
        attempt.allowance = 165536
        attempt.grandfathered = True
        line = wl.record(attempt, {wl.LOG_PATH_ENV: str(tmp_path / "x.jsonl")})
        assert 0 < len(line.encode("utf-8")) + 1 < 2048, len(line)


# ---------------------------------------------------------------------------
# the reader
# ---------------------------------------------------------------------------


def synth(
    status: str,
    *,
    doc: str = "claudedocs/handoff-widget-drain.md",
    session: str | None = SESSION_A,
    ts: str = "2026-10-01T00:00:00Z",
    confirmed: bool = True,
    warned: bool = False,
    before: int | None = 83_521,
    after: int | None = 97_343,
    allowance: int = 165_537,
) -> dict:
    """One synthetic row. Every default is pairwise distinct from the others AND
    from `MAX_BYTES` (65,536), `GRANDFATHER_STEP`/`BUDGET_NEAR_BYTES` (16,384) and
    from each other — so a mutant that hardcodes any of those constants cannot
    survive by coincidence."""
    return {
        "schema": wl.SCHEMA, "ts": ts, "session": session, "repo": "work",
        "doc": doc, "status": status, "exit_code": 0,
        "bytes_before": before, "bytes_after": after, "allowance": allowance,
        "grandfathered": True, "band_warning_fired": warned,
        "confirmed": confirmed, "prune_requested": False, "exit_flags": [],
    }


class TestSecondPassRate:
    def test_a_clean_log_of_plain_writes_scores_zero(self):
        """The NEGATIVE half of the positive-control pair."""
        hits, total, _ = wl.second_pass([
            synth("pushed", ts="2026-10-01T00:00:01Z"),
            synth("pushed", ts="2026-10-01T00:00:02Z"),
        ])
        assert (hits, total) == (0, 2)

    def test_a_refusal_then_a_rewrite_moves_the_rate_off_zero(self):
        """🔴 THE POSITIVE CONTROL. A zero is otherwise indistinguishable from a
        reader wired to nothing: `1 on the positive control, 0 on a clean log`."""
        hits, total, _ = wl.second_pass([
            synth("size-ratchet", ts="2026-10-01T00:00:01Z"),
            synth("pushed", ts="2026-10-01T00:00:02Z"),
        ])
        assert (hits, total) == (1, 2)

    @pytest.mark.parametrize("refusal", wl.REFUSAL_STATUSES)
    def test_every_refusal_status_counts(self, refusal: str):
        hits, _, _ = wl.second_pass([
            synth(refusal, ts="2026-10-01T00:00:01Z"),
            synth("written", ts="2026-10-01T00:00:02Z"),
        ])
        assert hits == 1, f"{refusal} did not count as a refused pass"

    def test_a_refusal_with_no_later_write_is_not_a_second_pass(self):
        """A session that was refused and gave up paid no second pass."""
        hits, total, _ = wl.second_pass([synth("size-ratchet")])
        assert (hits, total) == (0, 1)

    def test_a_later_write_to_a_DIFFERENT_doc_is_not_a_second_pass(self):
        hits, _, _ = wl.second_pass([
            synth("size-ratchet", ts="2026-10-01T00:00:01Z"),
            synth("pushed", doc="claudedocs/handoff-other-effort.md",
                  ts="2026-10-01T00:00:02Z"),
        ])
        assert hits == 0

    def test_a_later_write_in_a_DIFFERENT_session_is_not_a_second_pass(self):
        """The cost is one session re-running. Another session writing the same
        doc tomorrow is not a pass anyone paid."""
        hits, _, _ = wl.second_pass([
            synth("size-ratchet", session=SESSION_A, ts="2026-10-01T00:00:01Z"),
            synth("pushed", session=SESSION_B, ts="2026-10-01T00:00:02Z"),
        ])
        assert hits == 0

    def test_a_write_BEFORE_the_refusal_is_not_a_second_pass(self):
        """ORDER IS THE WHOLE CLAIM: "evicted before composing" vs "evicted after
        being refused" is what git cannot tell apart, so a reader that ignored
        order would reproduce the defect it exists to measure."""
        hits, _, _ = wl.second_pass([
            synth("pushed", ts="2026-10-01T00:00:01Z"),
            synth("size-ratchet", ts="2026-10-01T00:00:02Z"),
        ])
        assert hits == 0

    def test_a_proposal_run_followed_by_a_confirm_run_is_NOT_a_second_pass(self):
        """🔴 THE `confirmed` FIELD'S OWN TEST. Without the filter every ordinary
        write is two attempts and the rate is inflated ~2x BY CONSTRUCTION. Both
        rows are REFUSALS here, so a reader that ignored `confirmed` would score
        the proposal row as a hit against the confirm row's own refusal — and the
        denominator would be 2 instead of 1.
        """
        hits, total, _ = wl.second_pass([
            synth("size-ratchet", confirmed=False, ts="2026-10-01T00:00:01Z"),
            synth("pushed", confirmed=False, ts="2026-10-01T00:00:02Z"),
            synth("pushed", confirmed=True, ts="2026-10-01T00:00:03Z"),
        ])
        assert (hits, total) == (0, 1), (
            "proposal rows reached the second-pass measurement; the rate is "
            "inflated by the tool's own two-run shape"
        )

    def test_a_null_session_is_excluded_and_counted(self):
        """🔴 NEVER GROUPED. Grouping on `null` would merge every unattributed row
        from every session into one bucket and manufacture pairs."""
        hits, total, unattributable = wl.second_pass([
            synth("size-ratchet", session=None, ts="2026-10-01T00:00:01Z"),
            synth("pushed", session=None, ts="2026-10-01T00:00:02Z"),
        ])
        assert (hits, total, unattributable) == (0, 2, 2)

    def test_push_failed_is_not_counted_as_a_landing(self):
        """The commit exists on that path, but the run ended in a recovery the
        operator still has to carry out — counting it clean would read as resolved
        while the arc is open."""
        hits, _, _ = wl.second_pass([
            synth("size-ratchet", ts="2026-10-01T00:00:01Z"),
            synth("push-failed", ts="2026-10-01T00:00:02Z"),
        ])
        assert hits == 0

    def test_same_second_rows_are_ordered_by_their_position_in_the_file(self):
        """🔴 MEASURED, NOT HYPOTHETICAL: `ts` is seconds-resolution and a
        proposal run plus its confirm run routinely land in the SAME second. File
        order is the append order, which is the truth."""
        same = "2026-10-01T00:00:07Z"
        hits, _, _ = wl.second_pass([
            synth("size-ratchet", ts=same), synth("pushed", ts=same)])
        assert hits == 1
        hits_reversed, _, _ = wl.second_pass([
            synth("pushed", ts=same), synth("size-ratchet", ts=same)])
        assert hits_reversed == 0


class TestWarningActedUpon:
    def test_a_warned_run_whose_next_write_SHRANK_counts_as_acted_upon(self):
        shrank, grew, pending, _, docs = wl.warning_acted_upon([
            synth("proposed", warned=True, ts="2026-10-01T00:00:01Z"),
            synth("pushed", before=97_343, after=83_521,
                  ts="2026-10-01T00:00:02Z"),
        ])
        assert (shrank, grew, pending, docs) == (1, 0, 0, 1)

    def test_a_warned_run_whose_next_write_GREW_ANYWAY_counts_as_ignored(self):
        """🔴 THE METRIC THAT MATTERS MOST. `handoff_doc`'s own
        `EXIT_SIZE_RATCHET` docstring records that the #1648 pre-write warning has
        been printed on every over-budget write since and the mechanism went on
        regardless. A warning that fires is not evidence; only the action is."""
        shrank, grew, pending, _, docs = wl.warning_acted_upon([
            synth("proposed", warned=True, ts="2026-10-01T00:00:01Z"),
            synth("pushed", before=83_521, after=97_343,
                  ts="2026-10-01T00:00:02Z"),
        ])
        assert (shrank, grew, pending, docs) == (0, 1, 0, 1)

    def test_a_warned_run_with_no_later_write_is_PENDING_not_a_failure(self):
        """Folding pending into GREW would score an open arc as a failure; folding
        it into SHRANK would score it as a success. It is excluded from both."""
        shrank, grew, pending, _, _ = wl.warning_acted_upon([
            synth("size-ratchet", warned=True)])
        assert (shrank, grew, pending) == (0, 0, 1)

    def test_a_later_write_with_no_bytes_before_is_UNCLASSIFIABLE(self):
        """A doc that did not exist has no direction to read."""
        shrank, grew, _, unclassifiable, _ = wl.warning_acted_upon([
            synth("proposed", warned=True, ts="2026-10-01T00:00:01Z"),
            synth("pushed", before=None, after=41_983,
                  ts="2026-10-01T00:00:02Z"),
        ])
        assert (shrank, grew, unclassifiable) == (0, 0, 1)

    def test_an_unwarned_run_is_not_in_the_denominator_at_all(self):
        shrank, grew, pending, unclassifiable, docs = wl.warning_acted_upon([
            synth("proposed", warned=False, ts="2026-10-01T00:00:01Z"),
            synth("pushed", ts="2026-10-01T00:00:02Z"),
        ])
        assert (shrank, grew, pending, unclassifiable, docs) == (0, 0, 0, 0, 0)

    def test_the_next_write_to_a_DIFFERENT_doc_does_not_resolve_a_warning(self):
        _, _, pending, _, _ = wl.warning_acted_upon([
            synth("proposed", warned=True, ts="2026-10-01T00:00:01Z"),
            synth("pushed", doc="claudedocs/handoff-other-effort.md",
                  ts="2026-10-01T00:00:02Z"),
        ])
        assert pending == 1

    def test_docs_warned_is_distinct_documents_not_warning_events(self):
        """🔴 #2001 WIDENED THE BAND AND MOVED docs-warned 10 -> 24, so a raw
        warning COUNT rises by construction. The distinct-document count is what
        tells a rising count from a rising population."""
        _, _, pending, _, docs = wl.warning_acted_upon([
            synth("proposed", warned=True, ts="2026-10-01T00:00:01Z"),
            synth("proposed", warned=True, ts="2026-10-01T00:00:02Z"),
            synth("proposed", warned=True, doc="claudedocs/handoff-second.md",
                  ts="2026-10-01T00:00:03Z"),
        ])
        assert (pending, docs) == (3, 2)


class TestTheDenominatorIsTheDocsOwnAllowance:
    def test_a_grandfathered_doc_over_MAX_BYTES_but_under_its_allowance_is_not_forced(
        self,
    ):
        """🔴 THE BRIEF'S MEASURED DEFECT, PINNED. Many docs sit at 3-5x
        `MAX_BYTES` legitimately under a GRANDFATHERED entry, so `MAX_BYTES` is the
        wrong denominator: of 38 git-derived "over the ceiling" shrinks only 10
        were real forced evictions.

        This row is 217,081 B — far over `MAX_BYTES` — and 283,009 B is its own
        allowance, so it is NOT over budget, and the warning never fired. The
        reader must therefore count it in NO bucket.
        """
        import handoff_budget

        row = synth("pushed", before=229_381, after=217_081, allowance=283_009,
                    warned=False)
        assert row["bytes_after"] > handoff_budget.MAX_BYTES, (
            "the fixture is not actually over MAX_BYTES, so this case cannot see "
            "the defect it is named for"
        )
        assert row["bytes_after"] < row["allowance"]
        report = wl.build_report([row])
        assert (report.warned_shrank, report.warned_grew) == (0, 0)
        assert report.second_pass_hits == 0

    def test_an_ungrandfathered_doc_records_the_bare_ceiling(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """The control for the row below: with no ledger entry the allowance IS
        `MAX_BYTES`, and `grandfathered` says so."""
        import handoff_budget

        run_tool(repo, logfile, update=update_file)
        row = only_row(logfile)
        assert row["grandfathered"] is False
        assert row["allowance"] == handoff_budget.MAX_BYTES

    def test_a_real_grandfathered_doc_over_MAX_BYTES_is_NOT_a_forced_event(
        self, repo: Path, logfile: Path, tmp_path: Path
    ):
        """🔴 THE BRIEF'S DEFECT, END TO END THROUGH THE REAL GATE.

        The topic is DERIVED AT RUN TIME from `handoff_budget.GRANDFATHERED`'s
        largest plaintext entry rather than typed, because that ledger is a
        RATCHET: entries are deleted the day their document drops under
        `MAX_BYTES`, so a hand-written topic would become a silently
        unreachable case. The derivation makes the fixture move with the ledger.

        The document is padded to well over `MAX_BYTES` and well under its OWN
        allowance, with more than one band's headroom left — so the honest answers
        are: rule (p) does NOT fire, the band warning does NOT fire, and the row
        carries the doc's own ceiling. A `_stamp_size` that reached for `MAX_BYTES`
        would report a doc 2.2x over budget that is not over budget at all, which
        is the metric the brief measured wrong: of 38 git-derived "over the
        ceiling" shrinks only 10 were real forced evictions.
        """
        import handoff_budget

        plaintext = {
            k: v for k, v in handoff_budget.GRANDFATHERED.items()
            if k.startswith("claudedocs/handoff-") and k.endswith(".md")
            and v > handoff_budget.MAX_BYTES
        }
        assert plaintext, (
            "no plaintext GRANDFATHERED entry is above MAX_BYTES any more, so this "
            "case cannot be built — it is not vacuously passing, it is unbuildable"
        )
        relpath, allowance = max(plaintext.items(), key=lambda kv: kv[1])
        topic = relpath[len("claudedocs/handoff-"):-len(".md")]

        # Over MAX_BYTES, under the allowance, and with MORE than one warning band
        # of headroom so `band_warning_fired` is false for a reason the fixture
        # states rather than one it stumbles into.
        target = allowance - 3 * handoff_budget.BUDGET_NEAR_BYTES
        assert target > handoff_budget.MAX_BYTES, (relpath, allowance)
        pad_line = "- padding with no durable marker and no bare ISO date\n"
        body = BASE_DOC
        while len(body.encode("utf-8")) < target:
            body += pad_line
        doc = repo / relpath
        doc.write_text(body, encoding="utf-8")
        _sh("git", "add", "--", relpath, cwd=repo)
        _sh("git", "commit", "-q", "-m", "a grandfathered doc", cwd=repo)

        update = tmp_path / "grow-grandfathered.md"
        update.write_text(
            "## Gotchas / decisions / dead-ends\n- one more ordinary gotcha\n",
            encoding="utf-8",
        )
        proc = run_tool(repo, logfile, update=update, topic=topic)
        assert proc.returncode == 0, (
            "rule (p) refused an ordinary edit to a doc UNDER its own allowance — "
            f"the ratchet is reading the wrong ceiling:\n{proc.stderr}"
        )
        row = only_row(logfile)
        assert row["allowance"] == allowance, (
            f"the row carries {row['allowance']} for {relpath}, whose own ledger "
            f"allowance is {allowance}. A metric denominated in MAX_BYTES "
            f"({handoff_budget.MAX_BYTES}) classifies this ordinary edit as a "
            f"forced eviction."
        )
        assert row["grandfathered"] is True
        assert row["bytes_after"] > handoff_budget.MAX_BYTES, (
            "the fixture did not actually cross MAX_BYTES, so it cannot see the "
            "defect it is named for"
        )
        assert row["bytes_after"] < row["allowance"]
        assert row["band_warning_fired"] is False, (
            "the fixture left less than one band of headroom, so this case is "
            "measuring the warning rather than the allowance"
        )
        assert row["status"] == "proposed"


class TestTheBandWarningFlag:
    """🔴 THE FIELD THE SECOND METRIC RESTS ON, PINNED IN BOTH DIRECTIONS.

    `band_warning_fired` is read off the STRING `budget_warning` returns, so the
    row and the transcript cannot disagree. A one-sided test would pass with the
    field hardcoded either way, so both arms are here and they use the SAME
    fixture document at two different sizes — the only thing that moves is the
    headroom.
    """

    @staticmethod
    def _pad_to(repo: Path, headroom: int) -> int:
        """Grow the fixture doc until exactly `headroom` bytes are left under its
        allowance. Returns the size written."""
        import handoff_budget

        relpath = "claudedocs/handoff-widget-drain.md"
        assert handoff_budget.lookup(relpath, handoff_budget.GRANDFATHERED) is None, (
            "this fixture path has acquired a GRANDFATHERED entry, so its allowance "
            "is no longer MAX_BYTES and the headroom arithmetic below is wrong"
        )
        target = handoff_budget.MAX_BYTES - headroom
        body = BASE_DOC
        pad = "- padding with no durable marker and no bare ISO date\n"
        while len(body.encode("utf-8")) < target:
            body += pad
        (repo / relpath).write_text(body, encoding="utf-8")
        _sh("git", "add", "--", relpath, cwd=repo)
        _sh("git", "commit", "-q", "-m", "pad", cwd=repo)
        return len(body.encode("utf-8"))

    @staticmethod
    def _grow(tmp_path: Path) -> Path:
        p = tmp_path / "grow.md"
        p.write_text(
            "## Gotchas / decisions / dead-ends\n- one more ordinary gotcha\n",
            encoding="utf-8")
        return p

    def test_a_run_INSIDE_the_band_records_true(
        self, repo: Path, logfile: Path, tmp_path: Path
    ):
        """Headroom deliberately a FRACTION of the band rather than a multiple of
        it: a fixture sitting exactly ON the boundary would leave the comparison
        untested in one direction."""
        import handoff_budget

        before = self._pad_to(repo, handoff_budget.BUDGET_NEAR_BYTES // 3)
        proc = run_tool(repo, logfile, update=self._grow(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert "⚠ Size:" in proc.stdout, (
            f"`budget_warning` printed nothing at {before} B, so the fixture is "
            f"not actually inside the band and this case is vacuous:\n{proc.stdout}"
        )
        row = only_row(logfile)
        assert row["band_warning_fired"] is True
        assert row["bytes_after"] < row["allowance"], (
            "the doc went OVER its allowance, so this is the over-budget arm "
            "rather than the near-ceiling band"
        )

    def test_a_run_WELL_CLEAR_of_the_band_records_false(
        self, repo: Path, logfile: Path, tmp_path: Path
    ):
        """The negative control. Without it the field could be hardcoded `True`.

        🔴 THE HEADROOM IS NOT A MULTIPLE OF THE BAND, AND THE FIRST DRAFT'S WAS.
        `4 * BUDGET_NEAR_BYTES` is exactly `MAX_BYTES`, so the pad target was 0,
        the loop padded nothing, and the fixture never crossed its own setup —
        measured, it died on `nothing to commit`. A bound that overshoots by a
        non-multiple cannot land on a boundary the comparison is supposed to be
        tested either side of.
        """
        import handoff_budget

        self._pad_to(repo, 2 * handoff_budget.BUDGET_NEAR_BYTES + 777)
        proc = run_tool(repo, logfile, update=self._grow(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert "⚠ Size:" not in proc.stdout, proc.stdout
        assert only_row(logfile)["band_warning_fired"] is False


class TestTheReaderOutput:
    def test_rates_are_printed_with_their_denominators(self, tmp_path: Path):
        """🔴 NEVER A BARE COUNT. The band change moved docs-warned 10 -> 24, so a
        raw count rises by construction and reads as a regression."""
        log = tmp_path / "x.jsonl"
        log.write_text("\n".join(json.dumps(r) for r in (
            synth("size-ratchet", ts="2026-10-01T00:00:01Z"),
            synth("pushed", ts="2026-10-01T00:00:02Z"),
        )) + "\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(READER), "--log", str(log)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        # 🔴 1/2, NOT 1/1 — AND THE FIRST DRAFT OF THIS TEST ASSERTED 1/1. The
        # denominator is EVERY confirmed attempt (the refusal and the rewrite are
        # both attempts), not just the ones that were hits. An expectation derived
        # from the numerator is the shape `claude/RULES.md` forbids.
        assert "1/2 (50.0%)" in proc.stdout, proc.stdout
        assert "rows: 2" in proc.stdout

    def test_an_empty_log_says_NOTHING_MEASURED_rather_than_zero_percent(
        self, tmp_path: Path
    ):
        """🔴 A RATE OVER AN EMPTY DENOMINATOR IS NOT 0% — it is "could not
        measure", and the two must not share a rendering."""
        log = tmp_path / "empty.jsonl"
        log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(READER), "--log", str(log)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "NOTHING MEASURED" in proc.stdout
        assert "0.0%" not in proc.stdout

    def test_a_truncated_final_line_is_COUNTED_rather_than_swallowed(
        self, tmp_path: Path
    ):
        """A concurrent writer's partial line is the expected artefact of reading
        a live log; silently dropping it would make a shrinking denominator look
        like a falling rate."""
        log = tmp_path / "torn.jsonl"
        log.write_text(
            json.dumps(synth("pushed")) + "\n" + '{"schema":1,"ts":"2026-',
            encoding="utf-8",
        )
        got, malformed = wl.read_rows(log)
        assert (len(got), malformed) == (1, 1)

    def test_the_status_table_accounts_for_every_row_including_null_statuses(self):
        rowset = [
            synth("pushed"), synth("size-ratchet"),
            {**synth("pushed"), "status": None},
        ]
        report = wl.build_report(rowset)
        assert sum(report.by_status.values()) == report.rows == 3
        assert report.by_status["<no status= token>"] == 1

    def test_the_reader_reads_the_log_the_gate_actually_wrote(
        self, repo: Path, update_file: Path, logfile: Path
    ):
        """🔴 THE SEAM. Every test above exercises ONE side; this one builds the
        combined state — a real run writes the file, and the real reader parses it
        — which is the surface neither side's tests load."""
        run_tool(repo, logfile, update=update_file)
        run_tool(repo, logfile, "--confirm", "--push", update=update_file)
        proc = subprocess.run(
            [sys.executable, str(READER), "--log", str(logfile)],
            capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert "rows: 2" in proc.stdout
        assert "proposed" in proc.stdout and "pushed" in proc.stdout
        assert "malformed" not in proc.stdout, (
            "the reader could not parse a line the gate wrote — the two sides "
            f"disagree about the format:\n{proc.stdout}"
        )
