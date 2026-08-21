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

THE FIX, and why it is this one. bind(2) reads `sun_path`, but it RESOLVES that
path through symlinks like any other syscall. So: make a SHORT symlink pointing
at the destination's directory, and bind through it. The kernel creates the
socket inode in the real directory; the only string that has to fit in 108 bytes
is the short one. The result is a real socket (`stat.S_ISSOCK` true, `[ -S ]`
true) at a path of any length.

🔴 A `rename(2)` INTO PLACE WAS THE FIRST FIX AND IT IS WRONG — recorded because
it looks correct and passed on the dev host. rename does not read `sun_path`, so
the length problem really is solved; what it cannot do is cross a FILESYSTEM. The
nix build sandbox puts `TMPDIR` on `/build` and the staging dir on `/tmp`, which
are different mounts, so every socket fixture died there with
`OSError: [Errno 18] Invalid cross-device link` — GREEN on the dev host and in
`nix-shell`, RED in the tier that gates merges, which is the two-tier blind spot
`claude/RULES.md` names. Binding through a symlink has no same-filesystem
constraint at all: nothing moves.

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


def staging_prefix():
    """The `mkdtemp` prefix this process uses, exported so a test can glob for
    ITS OWN staging directories and nobody else's. One writer, one reader —
    a test that spelled the prefix itself would silently stop matching the day
    this changed."""
    return "afu%d-" % os.getpid()


def _staging_root():
    """The shortest directory we can stage the symlink in, checked, never assumed.

    `/tmp` first because it is the shortest thing that exists on every host this
    suite runs on — the dev host, the nix build sandbox and CI alike. The
    ambient `TMPDIR` is the fallback and not the default precisely because it is
    the value that breaks this: `nix-shell` sets it 33 chars deep and pytest
    nests ~80 more underneath.

    It does NOT need to share a filesystem with the destination — only a rename
    would need that, and this helper does not rename (see the module docstring).
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
    parent = os.path.dirname(path) or "."
    name = os.path.basename(path)
    os.makedirs(parent, exist_ok=True)

    root = _staging_root()
    # 🔴 THE PID IS IN THE PREFIX SO A CONCURRENT RUN CANNOT BE MISTAKEN FOR A
    # LEAK. `test_the_socket_fixture_does_not_MOVE_the_inode_across_filesystems`
    # asserts that no staging directory survives the call, by diffing
    # `/tmp/afu*` before and after. That diff anticipates a STALE directory from
    # a crashed earlier run — it is a before/after diff precisely for that — but
    # not a SIBLING creating one inside the window, which is routine on this box
    # (three suites of this repo were running concurrently on 2026-08-21, load
    # average 28). Without the pid the assertion would fail naming another
    # agent's directory, i.e. a flake that reads exactly like a leak in the code
    # under test. `mkdtemp` still appends its own random suffix, so this narrows
    # the namespace without making it collidable.
    d = tempfile.mkdtemp(prefix=staging_prefix(), dir=root)
    link = os.path.join(d, "d")
    os.symlink(parent, link)
    short = os.path.join(link, name)
    assert len(short.encode()) < _STAGING_MARGIN, (
        "the short bind path is %d bytes (%r), too close to the %d-byte "
        "sun_path limit to be safe. This helper is the fixture, not the code "
        "under test — shorten the staging root or the leaf NAME (the leaf is "
        "the only part of the destination that still counts)."
        % (len(short.encode()), short, SUN_PATH_MAX))

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        # bind(2) resolves `link` to the real parent directory, so the inode is
        # created AT `path`. Nothing is moved afterwards — which is what makes
        # this immune to the cross-device failure a rename hits in the nix
        # sandbox, where TMPDIR is on /build and the staging dir on /tmp.
        s.bind(short)
    except BaseException:                        # pragma: no cover - defensive
        s.close()
        raise
    finally:
        # Only the symlink and its directory are ours to remove; the socket now
        # lives in the caller's tree.
        for cleanup in (lambda: os.unlink(link), lambda: os.rmdir(d)):
            try:
                cleanup()
            except OSError:                      # pragma: no cover - defensive
                pass

    # 🔴 POSITIVE CONTROL, in the helper itself, and it is what proves the
    # symlink resolved to where we meant. A fixture that quietly produced a
    # regular file — or a socket at the STAGING path rather than the real one —
    # would make every "a socket at a managed path is BLOCKING" test pass for the
    # wrong reason, because the walks classify a regular file too, just
    # differently. `os.lstat(path)` reads the DESTINATION, after the staging
    # symlink is already gone.
    assert stat.S_ISSOCK(os.lstat(path).st_mode), (
        "%r is not a socket (mode %o). The fixture did not build the thing "
        "under test." % (path, os.lstat(path).st_mode))
    return s
