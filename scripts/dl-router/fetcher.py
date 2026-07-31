"""yt-dlp job runner for stream sources (HLS/DASH `<video>` elements).

A plain download listener cannot save an HLS/DASH stream — there is no single
file to save — so the context menu hands the page URL to yt-dlp instead, and it
writes straight into the routed subject directory.

Security shape (asserted by tests):
  * the URL is validated as plain http(s) BEFORE it is used, and
  * the command is built as an ARGV LIST and executed with `shell=False`, with a
    `--` terminator so a URL can never be read as a flag.
Never a shell string — the URL comes from a web page.

The process spawner and the clock are injectable; no test ever runs yt-dlp.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from pathlib import Path

from safety import UnsafeName, is_http_url, is_safe_dir_name, safe_rel_path

STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"
TERMINAL_STATES = frozenset({STATE_DONE, STATE_ERROR, STATE_CANCELLED})

DEFAULT_OUTPUT_TEMPLATE = "%(title).150B [%(id)s].%(ext)s"


def build_argv(url: str, dest_dir, *, ytdlp_bin: str = "yt-dlp",
               cookies_from_browser: str = "",
               output_template: str = DEFAULT_OUTPUT_TEMPLATE) -> list:
    """The exact argv handed to `subprocess`. Raises on an unusable URL."""
    if not is_http_url(url):
        raise ValueError(f"refusing non-http(s) or malformed URL: {url!r}")
    argv = [
        str(ytdlp_bin),
        "--no-playlist",
        "--no-continue",
        "--no-overwrites",
        "--no-progress",
        # `home:` pins the --paths TYPE explicitly. yt-dlp parses this argument
        # as `[TYPE:]PATH` and only treats a prefix as a TYPE when it matches a
        # known one, so an absolute path is already safe -- but stating the
        # type removes the ambiguity entirely, which is what lets a directory
        # name legitimately contain a colon ("Series: Volume 1"). The global
        # name rule (safety.py / sanitize.js) therefore does NOT have to reject
        # `:`, and must not: doing so made such a directory appear in /dirs
        # while being impossible to route into.
        "--paths", f"home:{dest_dir}",
        "-o", str(output_template),
    ]
    if cookies_from_browser:
        argv += ["--cookies-from-browser", str(cookies_from_browser)]
    # `--` ends option parsing: even if the URL check were bypassed, a leading
    # `-` could not become a flag.
    argv += ["--", url]
    return argv


def default_runner(argv, cwd=None):
    return subprocess.Popen(argv, cwd=cwd, shell=False,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)


class FetchJob:
    __slots__ = ("id", "url", "dir", "state", "created_at", "started_at",
                 "ended_at", "returncode", "error", "proc", "argv")

    def __init__(self, job_id: str, url: str, dir_name: str, argv: list,
                 created_at: float):
        self.id = job_id
        self.url = url
        self.dir = dir_name
        self.argv = argv
        self.state = STATE_QUEUED
        self.created_at = created_at
        self.started_at = None
        self.ended_at = None
        self.returncode = None
        self.error = None
        self.proc = None

    def as_dict(self) -> dict:
        return {
            "jobId": self.id,
            "url": self.url,
            "dir": self.dir,
            "state": self.state,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "returncode": self.returncode,
            "error": self.error,
        }


class Fetcher:
    """Bounded set of yt-dlp jobs. Poll-driven: no background threads."""

    def __init__(self, library_root, *, ytdlp_bin: str = "yt-dlp",
                 cookies_from_browser: str = "",
                 output_template: str = DEFAULT_OUTPUT_TEMPLATE,
                 max_jobs: int = 4, runner=default_runner, clock=time.time):
        self.library_root = Path(library_root)
        self.ytdlp_bin = ytdlp_bin
        self.cookies_from_browser = cookies_from_browser
        self.output_template = output_template
        self.max_jobs = int(max_jobs)
        self._runner = runner
        self._clock = clock
        self._jobs: dict = {}
        self._lock = threading.Lock()

    def submit(self, url: str, dir_name: str, *, known_dirs=None) -> FetchJob:
        if not is_http_url(url):
            raise ValueError("url must be a plain http(s) URL")
        if not is_safe_dir_name(dir_name):
            raise UnsafeName(f"unsafe directory name: {dir_name!r}")
        if known_dirs is not None and dir_name not in known_dirs:
            raise UnsafeName(f"unknown directory: {dir_name!r}")
        # `is_safe_dir_name` proves the NAME is one component; it says nothing
        # about where that component RESOLVES. yt-dlp is handed this path with
        # `--paths` and will happily follow a symlink out of the library, so
        # resolve it and refuse an escape -- the same check /mkdir and
        # /relocate already do.
        dest = safe_rel_path(dir_name, root=self.library_root)
        argv = build_argv(url, dest, ytdlp_bin=self.ytdlp_bin,
                          cookies_from_browser=self.cookies_from_browser,
                          output_template=self.output_template)
        with self._lock:
            self._reap_locked()
            active = [j for j in self._jobs.values()
                      if j.state not in TERMINAL_STATES]
            if len(active) >= self.max_jobs:
                raise RuntimeError(f"too many active fetch jobs "
                                   f"({len(active)}/{self.max_jobs})")
            job = FetchJob(secrets.token_hex(8), url, dir_name, argv,
                           self._clock())
            self._jobs[job.id] = job
        try:
            os.makedirs(dest, exist_ok=True)
            job.proc = self._runner(argv, cwd=str(self.library_root))
            job.state = STATE_RUNNING
            job.started_at = self._clock()
        except (OSError, ValueError) as exc:
            job.state = STATE_ERROR
            job.error = str(exc)
            job.ended_at = self._clock()
        return job

    def _reap_locked(self) -> None:
        for job in self._jobs.values():
            if job.state != STATE_RUNNING or job.proc is None:
                continue
            rc = job.proc.poll()
            if rc is None:
                continue
            job.returncode = rc
            job.ended_at = self._clock()
            if rc == 0:
                job.state = STATE_DONE
            else:
                job.state = STATE_ERROR
                job.error = f"yt-dlp exited {rc}"

    def status(self, job_id: str):
        with self._lock:
            self._reap_locked()
            job = self._jobs.get(job_id)
            return job.as_dict() if job else None

    def jobs(self) -> list:
        with self._lock:
            self._reap_locked()
            return [j.as_dict() for j in
                    sorted(self._jobs.values(), key=lambda j: j.created_at,
                           reverse=True)]

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state in TERMINAL_STATES:
                return False
            if job.proc is not None:
                try:
                    job.proc.terminate()
                except OSError:
                    pass
            job.state = STATE_CANCELLED
            job.ended_at = self._clock()
            return True
