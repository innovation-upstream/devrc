"""Put a real AF_UNIX socket at an ARBITRARILY LONG path.

🔴 WHY THIS EXISTS — a fixture that reds the pre-push gate and nothing else.
Two suites need a genuine socket at a managed home-manager path so the walk under
test can classify it (`[ -S ]`, `S_ISSOCK`). The obvious spelling —

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(tmp_path / "home" / ".config" / "app" / "occupied"))

— is bounded by `sun_path`, which is **108 bytes on Linux**, path INCLUDED. That
is a limit on the string handed to bind(2), not on the filesystem, and pytest's
`tmp_path` is nested enough to blow through it as soon as `TMPDIR` is not `/tmp`:

  * default `TMPDIR` (unset -> /tmp)         -> 62 chars, binds fine, green.
  * `nix-shell -p …` sets `TMPDIR` to
    `/tmp/nix-shell-<pid>-<n>`               -> 115 chars, `OSError: AF_UNIX path
                                                too long`.

`githooks/tests-on-push.sh` runs the whole suite as
`nix-shell "${TOOL_ENV[@]}" --run "bash run-tests.sh --set all"`, so the second
row is the one that gates every push: both files went red on every push while
being green under a bare `pytest`. Measured 2026-08-21.

THE FIX, and why it is this one. bind(2) is the only syscall in the chain that
reads `sun_path`; **rename(2) is not**. So: bind in a directory short enough to
be safe under any `TMPDIR`, then `os.rename()` the socket inode to wherever the
test actually wants it. The result is a real socket (`stat.S_ISSOCK` true, `[ -S ]`
true, `os.path.getsize` 0) at a path of any length.

🔴 THE SHORT PATH IS ALWAYS USED, never as a fallback. A "bind directly, and on
OSError fall back" version leaves the fallback DEAD under a short `TMPDIR` — i.e.
unexercised in exactly the environment most people run tests in, and exercised
for the first time on the gate it was written for. One code path, every run.

`os.chdir` + a relative bind is the other standard workaround and is rejected on
purpose: cwd is process-global, so it is unsafe the moment anything runs tests in
parallel, and it leaks on an exception between the chdir and the restore.
"""
from __future__ import annotations

import os
import socket
import stat
import tempfile

# bind(2)'s limit. `sun_path` is `char[108]` on Linux (`sizeof` includes the NUL,
# so 107 usable bytes); other platforms are smaller. The staging directory is
# checked against a margin well inside it rather than against the true limit —
# a fixture that binds at 106 bytes is one rename away from being the same bug.
SUN_PATH_MAX = 108
_STAGING_MARGIN = 60


def _staging_root():
    """The shortest directory we can stage a bind in, checked, never assumed.

    `/tmp` first because it is the shortest thing that exists on every host this
    suite runs on — the dev host, the nix build sandbox and CI alike. The
    ambient `TMPDIR` is the fallback and not the default precisely because it is
    the value that breaks this: `nix-shell` sets it 33 chars deep and pytest
    nests ~80 more underneath.
    """
    for cand in ("/tmp", tempfile.gettempdir()):
        try:
            if os.path.isdir(cand) and os.access(cand, os.W_OK):
                return cand
        except OSError:                          # pragma: no cover - defensive
            continue
    raise AssertionError(                        # pragma: no cover - defensive
        "no writable staging directory for an AF_UNIX bind (tried /tmp and %r)"
        % tempfile.gettempdir())


def bind_socket_at(path):
    """Create a real AF_UNIX socket at `path` and return the open socket object.

    The caller keeps the returned socket alive for as long as the test needs the
    inode to stay bound; letting it be collected does not remove the file (an
    AF_UNIX socket file outlives its fd), so tests that only need the inode may
    discard it. `path.parent` is created if missing.

    Raises AssertionError — not OSError — if the staging bind itself would be
    over the limit, because that is a broken harness rather than a test failure.
    """
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    root = _staging_root()
    d = tempfile.mkdtemp(prefix="afu", dir=root)
    staged = os.path.join(d, "s")
    assert len(staged.encode()) < _STAGING_MARGIN, (
        "the staging socket path is %d bytes (%r), too close to the %d-byte "
        "sun_path limit to be a safe place to bind. This helper is the fixture, "
        "not the code under test — fix the staging root rather than the test."
        % (len(staged.encode()), staged, SUN_PATH_MAX))

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(staged)
        # rename(2) does not read sun_path, so the DESTINATION length is
        # unbounded. This is the entire trick.
        os.rename(staged, path)
    except BaseException:                        # pragma: no cover - defensive
        s.close()
        raise
    finally:
        try:
            os.rmdir(d)
        except OSError:                          # pragma: no cover - defensive
            pass

    # 🔴 POSITIVE CONTROL, in the helper itself. A fixture that quietly produced
    # a regular file would make every "a socket at a managed path is BLOCKING"
    # test pass for the wrong reason — the reclaim/drift walks classify a regular
    # file too, just differently. Assert the inode type we claim to have made.
    assert stat.S_ISSOCK(os.lstat(path).st_mode), (
        "%r is not a socket after the rename (mode %o). The fixture did not "
        "build the thing under test." % (path, os.lstat(path).st_mode))
    return s
