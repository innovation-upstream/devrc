#!/usr/bin/env python3
"""The ONE place that makes "a test cannot touch the operator's REAL git config,
and cannot reach a REAL remote" true — for EVERY target, not for one directory.

🔴 WHAT LEAKED, MEASURED 2026-08-21
------------------------------------
A test ran `githooks/install.sh` for real. That script sets `core.hooksPath`
**`--global`**, so it rewrote the operator's `~/.gitconfig` to point at a pytest
tmpdir — every git command on the machine then looked for hooks in a directory
that no longer existed. In the same window ~63 fixture commits (`base`, `ahead`,
`local side`, `un-pushed work stranded on main`, `autocommit: N change(s) …`)
were pushed to the REAL `origin/main`, whose tree became a single file named
`f`, and the base clone ended up `core.bare = true` on a populated working tree.

Two distinct surfaces, one root cause: a fixture repo's git commands ran with
the operator's own git environment, and nothing said they could not.

🔴 WHY A PLUGIN AND NOT N CONFTESTS — the trap this repo has hit TWICE
----------------------------------------------------------------------
#399 installed the no-real-launcher guard from `scripts/tests/conftest.py` and
protected 1 target of 17. #614 installed the spool guard from
`scripts/browser-bridge/tests/conftest.py` and protected 1 directory of 13.
`scripts/run-tests.sh` runs ONE pytest process per target directory, so a
conftest fixture protects exactly that directory and nothing else — and the
target added next month gets none of it.

So the rule lives HERE, in one module, and is registered at TWO entry points:

  * `scripts/run-tests.sh` loads it with `-p testlib.nogit_plugin` on the single
    `python -m pytest` line every target goes through, and exports the same
    variables for the whole script — which is what covers the NON-pytest targets
    (`HOOK_TESTS`, `SHELL_TESTS`) that no conftest can ever reach;
  * `scripts/tests/conftest.py` imports the fixture, so a bare
    `pytest scripts/tests` outside the runner is covered by the SAME code rather
    than by a second copy of it.

🔴 THE FOUR LEVERS, AND WHY EACH ONE
-------------------------------------
  GIT_CONFIG_GLOBAL  -> a throwaway file inside the guard dir. This is the
      surgical fix for the surface that was actually poisoned: `git config
      --global <anything>` now writes there, and `~/.gitconfig` is not merely
      "not written by well-behaved code" — it is unreachable.

  GIT_CONFIG_SYSTEM + GIT_CONFIG_NOSYSTEM -> `/dev/null` and `1`. The system
      file is the same hazard one level up. Both are set because they cover
      different git versions (`GIT_CONFIG_SYSTEM` is 2.32+); either alone is
      enough on a modern git, and neither is a substitute for the other on an
      old one.

  GIT_ALLOW_PROTOCOL=file -> git itself refuses `https` and `ssh` transports
      ("fatal: transport 'https' not allowed", exit 128) while `file://` and
      plain-path remotes — which is every fixture remote in this tree — keep
      working. Verified against git 2.55.0 rather than assumed: see
      `scripts/tests/test_nogit_isolation.py`, which pins the refusal AND its
      positive control (the same command succeeds with the lever removed).

  GIT_TERMINAL_PROMPT=0 -> belt and braces. If a git op ever escaped the
      allowlist it must fail, not sit forever waiting for the operator to type a
      password into a test runner.

🔴 WHAT THIS DELIBERATELY DOES **NOT** DO: reassign HOME.
Several suites legitimately read `~/.claude/...` (the analyze-service index
store among them), so a blanket HOME rewrite would break real tests and be
reverted — trading a durable guard for a temporary one. `GIT_CONFIG_GLOBAL` is
the narrower lever that closes the measured surface exactly.

The residual hazard that leaves, stated rather than implied: code that REMOVES
`GIT_CONFIG_GLOBAL` from its own environment (`monkeypatch.delenv`, a hand-built
`subprocess` env) drops back to `$HOME/.gitconfig`. There is no cheap trap for
that without owning HOME — so `run-tests.sh` watches the real files directly
(GUARD 10's tripwire) and fails the run, naming the target, if any of them
changes during it.

🔴 THE POSITIVE CONTROLS, WHICH ARE THE POINT OF THE FILE
----------------------------------------------------------
"the operator's config was not touched" is the observable that a working guard
and a guard wired to nothing SHARE. So every session records, per target:

  * the SESSION MARKER (`nogit(session)`) — one line per pytest session. A
    target with no marker is a target this plugin never loaded in, and the
    runner fails it rather than reading its zero as protection.

  * the CONFIG CONTROL — a REAL `git config --global` write, the exact command
    that poisoned `~/.gitconfig`, deliberately executed and then followed: it
    must be readable back through `--global` AND present in the guard's own
    file. That is "feed it a case that MUST produce a non-zero count and watch
    the number move", per target: the runner counts the control keys and a
    target whose count did not move has proved nothing.

  * the PROTOCOL CONTROL — a real `git ls-remote` at an `https://` URL, which
    must be refused BY GIT (the refusal text), not by DNS and not by a missing
    credential. A failure for any other reason is recorded as ALLOWED, because
    a probe that cannot tell those apart is not a control.

IT DELIBERATELY NEVER UNDOES ITSELF, for the same reason as `spool_plugin`: the
variables are process-local, the process is exiting, and restoring the ambient
values at teardown reopens the hole for exactly the writes that are hardest to
see (an atexit hook, a lingering thread, a fixture finalizer).
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

# The handle `run-tests.sh` exports so a session under the runner can find the
# ONE guard directory and write into the ONE ledger the runner reads. Same shape
# as nolaunch_plugin.STUB_DIR_ENV and spool_plugin.GUARD_DIR_ENV, and for the
# same reason: per-target attribution needs one ledger, not one per session.
GUARD_DIR_ENV = "DEVRC_TEST_GIT_GUARD_DIR"

# 🔴 THE NESTING FLAG — load-bearing accounting, not tidiness. Some tests START
# ANOTHER PYTEST (`test_no_real_launchers.py` builds control/mutant pairs that
# way, and this file's own suite does too). Those child sessions load this plugin
# and inherit GUARD_DIR_ENV; without the flag each would append its own marker to
# the runner's ledger, and "3 markers, expected 1" reads as "this target ran
# WITHOUT the guard" — a false red on the one check that exists to catch a true
# one. A nested session is still fully ISOLATED; it is only silent in the ledger.
# `run-tests.sh` UNSETS this when it installs GUARD 10: a fresh run is the root of
# the chain.
NESTED_ENV = "DEVRC_TEST_GIT_IN_SESSION"

# The four levers. Spelled once, here, and read by name everywhere else.
CONFIG_ENV = "GIT_CONFIG_GLOBAL"
SYSTEM_ENV = "GIT_CONFIG_SYSTEM"
NOSYSTEM_ENV = "GIT_CONFIG_NOSYSTEM"
PROTOCOL_ENV = "GIT_ALLOW_PROTOCOL"
PROMPT_ENV = "GIT_TERMINAL_PROMPT"

# 🔴 `file` covers BOTH `file://…` and a plain filesystem path — measured against
# git 2.55.0, both directions (clone and push), and pinned by
# `test_nogit_isolation.py::test_the_allowlist_permits_every_local_remote_shape`.
# Every fixture remote in this tree is one of those two shapes, so the allowlist
# is not a compromise between safety and working tests: it refuses exactly the
# transports that can reach another machine.
ALLOWED_PROTOCOLS = "file"

CONFIG_NAME = "gitconfig"
SESSIONS_LOG = "sessions.log"
SESSION_MARKER = "nogit(session)"

# The control key's section. `git config --file <guard> --get-regexp` on this
# prefix is how `run-tests.sh` counts controls, so it is a fixed string on both
# sides of a process boundary and is pinned two-way by the test suite.
CONTROL_SECTION = "devrc-nogit-guard"
CONTROL_PREFIX = "control-"

# git's wording when another process holds `<cfg>.lock`. Matched lower-cased on
# the substring, for the same reason REFUSAL_TOKEN is: a phrasing change across
# git versions must not silently turn a contended write into an unretried one.
LOCK_CONTENTION = "could not lock config file"
# 6 attempts with the full-jitter backoff below averages ~0.5s and is bounded by
# ~1s of sleeping, against a contention window that is one small write per
# session. Raising it would start hiding a genuinely stuck lock instead of a
# collision. LOCK_TIMEOUT bounds the whole loop at 6 * 5s = 30s worst case.
LOCK_RETRIES = 6
LOCK_TIMEOUT = 5
CONTROL_OK = "emitted"
CONTROL_UNCONTAINED = "WITHHELD-UNCONTAINED"

# 🔴 THE CROSS-PROCESS MUTEX THAT MAKES THE RETRY LOOP A FALLBACK RATHER THAN THE
# PLAN. Every session sharing one guard dir fires the control write above; git's
# own `<cfg>.lock` is exclusive and NON-blocking, so the losers exit 255
# immediately and the retry loop is a thundering herd racing to re-collide. It
# only wins while the write is faster than the backoff: MEASURED on this box,
# 96 concurrent writers to an empty guard file produced 3 exhaustions, 160
# produced 74, and 16 writers against a config large enough to make one write
# outlast the jitter produced 11. The operator's box is the loaded case, and
# GUARD 10 read the exhaustion as `control=unmeasured` — the guard unable to run
# its own positive control, which the runner correctly refuses to score as a
# pass.
#
# So the writers are SERIALISED here instead, on an advisory `flock` every
# participant takes before invoking git. There is no collision left to back off
# from, and nothing about the guard's accounting moves: the redirect still names
# the ONE file `run-tests.sh` exported (checked for equality at its GUARD 10
# evaluation), and the control keys still accumulate in that ONE file where
# `_nogit_controls()` counts them one-per-session.
#
# 🔴 `.pylock`, NEVER `.lock`: `<cfg>.lock` is git's OWN lock file name, and
# creating it ourselves would make every `git config --global` write in the run
# fail outright — the guard's positive control silenced by the thing meant to
# protect it.
#
# The lock is advisory and released by the kernel when the holder's fd closes,
# so a killed worker cannot wedge the run. Acquisition is bounded: on timeout the
# WRITE proceeds UNLOCKED, which is exactly the pre-serialisation behaviour, and
# the retry loop below is still there to catch it.
#
# 🔴 THE *WRITE* DEGRADES TO PRE-SERIALISATION BEHAVIOUR; THE *TIME* DOES NOT —
# and the retry loop's own comment below states a worst case that this constant
# invalidated. Serialising inside the retry makes the ceiling
#   LOCK_RETRIES * (CONFIG_LOCK_WAIT + LOCK_TIMEOUT) + jitter
#   = 6 * (60 + 5) + 0.75 ≈ 391s
# against ≈31s before. That is inside a session-scoped autouse fixture, so it is
# a per-session stall, not a per-test one. It does not threaten `gate.sh`'s
# 3600s tier cap — the hazard is an operator reasoning from the stale figure and
# concluding a session cannot stall for minutes. Re-derive this product if you
# change ANY of the three constants.
CONFIG_LOCK_SUFFIX = ".pylock"
CONFIG_LOCK_WAIT = 60.0
CONFIG_LOCK_POLL = 0.005

# The protocol probe. `.invalid` is reserved by RFC 2606 and can never resolve,
# so if the allowlist is NOT in effect this fails at DNS — a DIFFERENT error,
# which is exactly what makes the two states distinguishable. Nothing leaves the
# machine in either case.
PROBE_URL = "https://devrc-nogit-guard.invalid/refused.git"
PROTOCOL_REFUSED = "refused"
PROTOCOL_ALLOWED = "ALLOWED"
# git's own wording. Matched case-insensitively on the substring rather than the
# whole sentence so a phrasing change across versions does not silently invert
# the verdict — and `test_nogit_isolation.py` pins BOTH halves of the pair, so a
# git upgrade that changed it goes red in a named test rather than here.
REFUSAL_TOKEN = "not allowed"


def guard_config_path(guard_dir: Path) -> Path:
    """The throwaway file `git config --global` writes to under this guard."""
    return Path(guard_dir) / CONFIG_NAME


def guard_config_lock_path(guard_dir: Path) -> Path:
    """The advisory lock serialising this guard's `git config --global` writes.

    A SIBLING of the config file, never the config file itself and never git's
    own `<cfg>.lock` — see CONFIG_LOCK_SUFFIX. Nothing reads it; it exists only
    to be flock'd.
    """
    return Path(guard_dir) / (CONFIG_NAME + CONFIG_LOCK_SUFFIX)


@contextlib.contextmanager
def _config_write_lock(guard_dir: Path, wait: float | None = None):
    """Hold the guard dir's config mutex for one `git config --global` write.

    Yields True if the lock is held, False if it could not be taken — and False
    is not a failure path: the caller writes anyway, because an unserialised
    write is the behaviour that shipped before this lock existed and the retry
    loop still covers it. FAILING here must never turn into `unmeasured`, which
    is the verdict this whole change exists to stop producing.

    Polled `LOCK_NB` rather than a blocking `LOCK_EX` so the wait is BOUNDED. A
    blocking acquire against a holder that wedged would hang the session inside
    a session-scoped autouse fixture, i.e. hang the target with no output.
    """
    fh = None
    held = False
    try:
        try:
            Path(guard_dir).mkdir(parents=True, exist_ok=True)
            fh = guard_config_lock_path(guard_dir).open("a+")
        except OSError:
            fh = None
        if fh is not None:
            # 🔴 READ THE MODULE ATTRIBUTE, do not bind it as a default argument.
            # A default is captured at IMPORT, so `monkeypatch.setattr(mod,
            # "CONFIG_LOCK_WAIT", 0.2)` had no effect while its neighbours
            # LOCK_RETRIES / LOCK_TIMEOUT / CONFIG_LOCK_SUFFIX all honoured it —
            # an inconsistency that silently cost a measurement (a run set to
            # 0.2s took 60.4s). An explicit `wait=` argument still wins.
            if wait is None:
                wait = CONFIG_LOCK_WAIT
            deadline = time.monotonic() + wait
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    held = True
                    break
                except OSError as exc:
                    # 🔴 ONLY contention is worth waiting out. `flock` reports a
                    # held lock as EWOULDBLOCK/EAGAIN; anything else — ENOLCK on
                    # a filesystem without lock support, EPERM under a
                    # restrictive seccomp — will NEVER succeed, so polling it
                    # burns the whole CONFIG_LOCK_WAIT per acquire, silently,
                    # every session. MEASURED with flock forced to ENOLCK:
                    # held=False after the full wait rather than immediately.
                    # Not reachable on today's hosts (the guard dir is a local
                    # `mktemp -d` in every tier), which is why this fails fast
                    # rather than escalating.
                    if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                        break
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(CONFIG_LOCK_POLL)
        yield held
    finally:
        if fh is not None:
            if held:
                with contextlib.suppress(OSError):
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                fh.close()


def install(guard_dir: Path) -> Path:
    """Point every lever inside `guard_dir`. Never undone — see the header.

    Unconditional, including over an ambient value: a test runner that honoured
    an inherited `GIT_CONFIG_GLOBAL` would be trusting whatever env the operator
    happened to have, and that is a likelier accident than a deliberate intent.

    The config file is CREATED empty rather than left absent. `git config
    --global` would create it either way, but an existing file is what lets the
    runner's up-front check distinguish "the redirect works" from "git wrote
    somewhere else and this path never appeared".
    """
    guard_dir = Path(guard_dir)
    guard_dir.mkdir(parents=True, exist_ok=True)
    cfg = guard_config_path(guard_dir)
    if not cfg.exists():
        cfg.write_text("", encoding="utf-8")
    os.environ[CONFIG_ENV] = str(cfg)
    os.environ[SYSTEM_ENV] = os.devnull
    os.environ[NOSYSTEM_ENV] = "1"
    os.environ[PROTOCOL_ENV] = ALLOWED_PROTOCOLS
    os.environ[PROMPT_ENV] = "0"
    return cfg


def _git(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess | None:
    """Run a git command, or return None if that is impossible.

    None is a real outcome, not a swallowed error: it reaches the runner through
    the marker's `control=`/`protocol=` fields and fails the target, because a
    session that could not run its own controls has not proved anything.
    """
    exe = shutil.which("git")
    if exe is None:
        return None
    # 🔴 LC_ALL=C. BOTH controls in this file decide their verdict by matching an
    # ENGLISH substring of git's output — REFUSAL_TOKEN ("not allowed") and
    # LOCK_CONTENTION ("could not lock config file") — and nothing upstream pins
    # the locale. Under a localised git the substring misses and the control
    # reports `unmeasured`/`ALLOWED`, i.e. the guard fails or goes quiet for a
    # reason that has nothing to do with what it is guarding. Pinning it here
    # covers every call rather than at the two comparison sites.
    env = {**os.environ, "LC_ALL": "C", "LANGUAGE": "C"}
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True,
                              timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):  # noqa: BLE001
        return None


def config_control(guard_dir: Path) -> tuple[str, str]:
    """Fire a REAL `git config --global` write and follow where it landed.

    Returns `(status, detail)`. This is the exact command that rewrote the
    operator's `~/.gitconfig`, run on purpose — the only way to show that the
    redirect governs real git rather than that a variable was set.

    FAIL-CLOSED in the one way that matters: it verifies the value is present in
    the GUARD'S OWN FILE, not merely that `--global --get` reads it back. Reading
    it back proves git wrote somewhere; only the file check proves it wrote
    *here*. If the value is not in the guard file the status is
    WITHHELD-UNCONTAINED, which the runner turns into a loud failure — a guard
    that could not show containment must not report success.
    """
    cfg = guard_config_path(guard_dir)
    key = f"{CONTROL_SECTION}.{CONTROL_PREFIX}{os.getpid()}"
    token = f"nogit-{os.getpid()}"

    # 🔴 RETRY ONLY ON LOCK CONTENTION — DEFENCE IN DEPTH, NOT THE MECHANISM.
    # Under pytest-xdist every worker runs its own session and fires this
    # control, and they all write the SAME guard file — git takes an exclusive
    # `<cfg>.lock` per write, so the losers exit 255 with "could not lock config
    # file". MEASURED: 8 concurrent `git config --global` writes to one file ->
    # 4 exit 0, 4 exit 255 with that message. Before the retry, GUARD 10 reported
    # `control=unmeasured` for 16 of 27 targets — the guard could not run its own
    # positive control, which it correctly refuses to score as a pass.
    #
    # The retry ALONE was not enough, and was never going to be: it randomises a
    # collision instead of removing one, so it holds only while a write finishes
    # inside the backoff window. On a loaded box it does not, and GUARD 10 went
    # back to `unmeasured` (measured on `scripts/validation/tests`). The
    # `_config_write_lock` below is what removes the collision; this loop is KEPT
    # because it covers the contention the lock cannot see — a writer that never
    # took it (a test in the target firing its own `git config --global`, a
    # concurrent process reaching the same file, a session that failed to open
    # the lock at all).
    #
    # The retry is deliberately NARROW: any other non-zero exit still returns
    # "unmeasured" on the first try. Widening it to all failures would convert a
    # genuinely broken control into a slow green, which is the exact failure this
    # guard exists to prevent.
    for attempt in range(LOCK_RETRIES):
        # A SHORT timeout, not _git's 30s default: this is one tiny local write.
        # 🔴 THE WORST CASE IS NO LONGER `LOCK_RETRIES * timeout`. That sentence
        # stood here through the commit that added the mutex and was wrong by
        # ~13x: serialising INSIDE the retry means each attempt can also wait
        # out CONFIG_LOCK_WAIT first, so the ceiling is
        #   LOCK_RETRIES * (CONFIG_LOCK_WAIT + LOCK_TIMEOUT) + jitter ≈ 391s,
        # not ≈31s. See the CONFIG_LOCK_WAIT block above for the full derivation
        # and why the wrong number is the hazard rather than the duration.
        #
        # The mutex is held around the git invocation ONLY — never across the
        # backoff sleep below. Sleeping under it would make every OTHER
        # participant wait out a delay incurred for a writer that is not
        # participating, which is the shape that turns a mutex into the stall it
        # was added to prevent.
        with _config_write_lock(guard_dir) as held:
            wrote = _git(["config", "--global", key, token], timeout=LOCK_TIMEOUT)
        if wrote is None:
            return "unmeasured", "git is not runnable from this session"
        if wrote.returncode == 0:
            break
        if LOCK_CONTENTION not in (wrote.stderr or "").lower():
            return "unmeasured", f"git config --global exited {wrote.returncode}"
        if attempt == LOCK_RETRIES - 1:
            # 🔴 SAY WHICH MECHANISM. Three different causes produce a
            # BYTE-IDENTICAL "still lock-contended" verdict, and the incident
            # that motivated the mutex was diagnosed from exactly this string —
            # so it could not distinguish them, which is the repo's own "an
            # EMPTY RESULT cannot distinguish two mechanisms" rule:
            #   (a) genuine contention — the mutex was HELD and writers still
            #       collided, i.e. a writer that never took the mutex exists;
            #   (b) the mutex was NOT held (fail-open path), so this is the
            #       pre-serialisation behaviour and says nothing about (a);
            #   (c) a STALE `<cfg>.lock` left behind. `_git`'s own
            #       `timeout=LOCK_TIMEOUT` SIGKILLs git, and git does not remove
            #       its lockfile on SIGKILL — so ONE timed-out write poisons the
            #       guard dir for every remaining session, permanently, with
            #       this same message. Nothing cleans it up.
            # These fields are what make (a)/(b)/(c) separable in a later
            # incident. Do NOT delete the stale-lock probe to tidy this up: its
            # absence is what made the original diagnosis unrecoverable.
            git_lock = Path(str(cfg) + ".lock")
            try:
                stale = git_lock.exists()
            except OSError:
                stale = False
            return ("unmeasured",
                    f"git config --global exited {wrote.returncode} — still "
                    f"lock-contended after {LOCK_RETRIES} attempts "
                    f"(mutex={'held' if held else 'NOT-held'}, "
                    f"stale-git-lock={'PRESENT' if stale else 'absent'})")
        # FULL JITTER, not a pid-derived offset. The first version used
        # `(os.getpid() % 17) / 1000` — at most 16ms against a 50ms step, and
        # two pids congruent mod 17 got IDENTICAL backoff, so the losers
        # re-collided in lockstep exactly as if there were no jitter at all.
        time.sleep(random.uniform(0.0, 0.05 * (attempt + 1)))

    read = _git(["config", "--global", "--get", key])
    if read is None or read.returncode != 0 or read.stdout.strip() != token:
        got = "" if read is None else read.stdout.strip()
        return "unmeasured", f"the value did not read back through --global ({got!r})"

    try:
        contained = token in cfg.read_text(encoding="utf-8")
    except OSError:
        contained = False
    if not contained:
        return CONTROL_UNCONTAINED, f"the write is not in {cfg}"
    return CONTROL_OK, str(cfg)


def protocol_control() -> tuple[str, str]:
    """Attempt a REAL `https` git operation and require GIT to refuse it.

    Returns `(status, detail)`. The distinction the whole control rests on: a
    refusal from the ALLOWLIST is `fatal: transport 'https' not allowed` and
    happens before any name resolution, while a broken allowlist produces a DNS
    or credential error instead. Both are non-zero exits, so keying on "it
    failed" would score a completely unprotected run as protected. This keys on
    the refusal itself and reports anything else as ALLOWED, with the message
    that was actually produced, so a git rewording is diagnosable in one look
    rather than being silently absorbed.
    """
    proc = _git(["ls-remote", PROBE_URL])
    if proc is None:
        return "unmeasured", "git is not runnable from this session"
    blob = (proc.stderr or "") + (proc.stdout or "")
    first = next((ln for ln in blob.splitlines() if ln.strip()), "(no output)")
    if REFUSAL_TOKEN in blob.lower():
        return PROTOCOL_REFUSED, first.strip()
    if proc.returncode == 0:
        return PROTOCOL_ALLOWED, "the https probe SUCCEEDED — no allowlist at all"
    return PROTOCOL_ALLOWED, f"failed for another reason: {first.strip()}"


def _write_marker(guard_dir: Path, fields: dict[str, str]) -> None:
    """Append one TAB-separated session marker to `<guard_dir>/sessions.log`.

    TAB-separated because the values are filesystem paths and git error strings:
    a space-separated record would be mis-parsed under a TMPDIR containing a
    space, and the mis-parse would read as "this target ran unprotected".
    """
    line = SESSION_MARKER + "".join(
        f"\t{k}={str(v).replace(chr(9), ' ')}" for k, v in fields.items())
    try:
        Path(guard_dir).mkdir(parents=True, exist_ok=True)
        with (Path(guard_dir) / SESSIONS_LOG).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:  # pragma: no cover — an unwritable guard dir fails the run
        pass          # via the missing marker, which is the louder signal


def _inherited_guard_dir() -> Path | None:
    """The guard dir `run-tests.sh` already installed, if this session is under it.

    Verified, not trusted — the same check the sibling plugins make: an env var
    naming a directory that does not exist would silently downgrade this session
    to no accounting at all.
    """
    raw = os.environ.get(GUARD_DIR_ENV)
    if not raw:
        return None
    d = Path(raw)
    return d if d.is_dir() else None


@pytest.fixture(scope="session", autouse=True)
def no_real_git(tmp_path_factory):
    """Isolate git's global config and refuse remote transports, session-wide.

    AUTOUSE and session-scoped: protection a test has to ask for is protection
    the next test forgets — and the test that forgets is the one that rewrites
    the operator's `~/.gitconfig`. The offending test in the measured incident
    asked for nothing at all; it simply ran a real installer.
    """
    guard_dir = _inherited_guard_dir()
    if guard_dir is None:
        # Not under the runner (a bare `pytest <dir>`). The protection must not
        # be conditional on the runner, so install our own — the accounting is
        # what the runner adds, not the isolation.
        guard_dir = Path(tmp_path_factory.mktemp("nogit-guard"))
        install(guard_dir)
    else:
        # Under the runner the exports are already in place; re-install anyway so
        # this session cannot inherit a HALF-applied environment (a parent that
        # exported the config path but not the protocol allowlist would otherwise
        # be recorded as fully protected).
        install(guard_dir)

    nested = os.environ.get(NESTED_ENV) == "1"
    os.environ[NESTED_ENV] = "1"
    if nested:
        # Isolated, but silent — see NESTED_ENV. Yielding without writing is the
        # whole difference; the marker and the controls belong to the session the
        # RUNNER started, and only to that one.
        yield guard_dir
        return

    note = " ".join(sys.argv[1:]) or "(no args)"
    control, control_detail = config_control(guard_dir)
    protocol, protocol_detail = protocol_control()
    _write_marker(guard_dir, {
        "redirect": os.environ.get(CONFIG_ENV, "(unset)"),
        "system": os.environ.get(SYSTEM_ENV, "(unset)"),
        "protocols": os.environ.get(PROTOCOL_ENV, "(unset)"),
        "control": control,
        "control-detail": control_detail,
        "protocol": protocol,
        "protocol-detail": protocol_detail,
        "args": note,
    })
    yield guard_dir
