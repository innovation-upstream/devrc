"""Fetcher: job lifecycle, cancellation, bounds. yt-dlp is never executed —
the spawner is injected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetcher import (  # noqa: E402
    STATE_CANCELLED, STATE_DONE, STATE_ERROR, STATE_RUNNING, Fetcher,
    build_argv,
)

URL = "https://example-site.test/v/1"


class FakeProc:
    """A subprocess stand-in whose exit code the test controls."""

    def __init__(self, rc=None):
        self.rc = rc
        self.terminated = False

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True

    def finish(self, rc=0):
        self.rc = rc


class Spawner:
    def __init__(self):
        self.procs = []
        self.argvs = []

    def __call__(self, argv, cwd=None):
        self.argvs.append(argv)
        proc = FakeProc()
        self.procs.append(proc)
        return proc


# --- argv ------------------------------------------------------------------ #
def test_build_argv_includes_cookies_when_configured():
    argv = build_argv(URL, "/dest", cookies_from_browser="brave:Profile 2")
    i = argv.index("--cookies-from-browser")
    assert argv[i + 1] == "brave:Profile 2"


def test_build_argv_omits_cookies_when_empty():
    assert "--cookies-from-browser" not in build_argv(URL, "/dest")


def test_build_argv_never_overwrites():
    assert "--no-overwrites" in build_argv(URL, "/dest")


# --- lifecycle ------------------------------------------------------------- #
def test_submit_starts_a_running_job_in_the_target_directory(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock)
    job = f.submit(URL, "Jane Doe")
    assert job.state == STATE_RUNNING
    assert job.started_at == clock.now
    assert str(library / "Jane Doe") in spawn.argvs[0]


def test_job_completes_when_the_process_exits_zero(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock)
    job = f.submit(URL, "Jane Doe")
    assert f.status(job.id)["state"] == STATE_RUNNING
    spawn.procs[0].finish(0)
    clock.advance(3)
    status = f.status(job.id)
    assert status["state"] == STATE_DONE
    assert status["returncode"] == 0
    assert status["endedAt"] == clock.now


def test_job_errors_when_the_process_exits_nonzero(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock)
    job = f.submit(URL, "Jane Doe")
    spawn.procs[0].finish(1)
    status = f.status(job.id)
    assert status["state"] == STATE_ERROR
    assert "exited 1" in status["error"]


def test_spawn_failure_is_recorded_not_raised(library, clock):
    def boom(argv, cwd=None):
        raise OSError("yt-dlp not found")

    f = Fetcher(library, runner=boom, clock=clock)
    job = f.submit(URL, "Jane Doe")
    assert job.state == STATE_ERROR
    assert "not found" in job.error


def test_cancel_terminates_and_marks_the_job(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock)
    job = f.submit(URL, "Jane Doe")
    assert f.cancel(job.id) is True
    assert spawn.procs[0].terminated is True
    assert f.status(job.id)["state"] == STATE_CANCELLED


def test_cancel_is_idempotent_and_false_for_finished_jobs(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock)
    job = f.submit(URL, "Jane Doe")
    f.cancel(job.id)
    assert f.cancel(job.id) is False
    assert f.cancel("no-such-job") is False


def test_status_of_an_unknown_job_is_none(library, clock):
    assert Fetcher(library, runner=Spawner(), clock=clock).status("nope") is None


def test_max_jobs_is_enforced_and_frees_up_on_completion(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock, max_jobs=2)
    f.submit(URL, "Jane Doe")
    f.submit(URL, "Jane Doe")
    with pytest.raises(RuntimeError, match="too many"):
        f.submit(URL, "Jane Doe")
    spawn.procs[0].finish(0)
    assert f.submit(URL, "Jane Doe").state == STATE_RUNNING


def test_jobs_are_listed_newest_first(library, clock):
    spawn = Spawner()
    f = Fetcher(library, runner=spawn, clock=clock)
    first = f.submit(URL, "Jane Doe")
    clock.advance(10)
    second = f.submit(URL, "john-smith")
    assert [j["jobId"] for j in f.jobs()] == [second.id, first.id]


def test_job_dict_shape(library, clock):
    f = Fetcher(library, runner=Spawner(), clock=clock)
    job = f.submit(URL, "Jane Doe")
    assert set(job.as_dict()) == {"jobId", "url", "dir", "state", "createdAt",
                                  "startedAt", "endedAt", "returncode", "error"}


def test_submit_creates_the_destination_directory(library, clock):
    f = Fetcher(library, runner=Spawner(), clock=clock)
    f.submit(URL, "other")
    assert (library / "other").is_dir()
