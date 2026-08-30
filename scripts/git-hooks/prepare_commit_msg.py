#!/usr/bin/env python3
"""The Python half of the `prepare-commit-msg` session-id stamper.

🔴 NEVER INVOKED DIRECTLY BY GIT. The installed hook is the sibling `/bin/sh`
wrapper, which guards on an interpreter existing before running this file. That
indirection is the fix for a measured defect, not stylistic: as a
`#!/usr/bin/env python3` hook, an unresolvable interpreter made `git commit`
exit 1 with the commit REFUSED (paired control: same repo, hook removed, rc 0).
Nothing inside this file could have caught that, because it never ran.

WHAT IT ADDS. `Claude-Session-Id: <id>` — the runtime's own handle, i.e. what
`claude --resume` takes. That is the half a future reader needs to get from
`git blame` to a live session.

🔴 WHAT IT DELIBERATELY DOES NOT ADD. The `Claude-Session: https://claude.ai/…`
trailer is NOT emitted here: the hook layer never receives that token (measured
— 69 of 69 per-session state dirs are uuid-shaped, zero are `session_…`). The
two id spaces are disjoint and only the agent knows the claude.ai one. Do not
"fix" this by synthesising a URL from the id; the resulting link would not
resolve.

NO-OP FOR HUMANS. A `git commit` typed at a terminal has no Claude ancestor, so
`lookup()` returns None and the message is left byte-identical.
"""
from __future__ import annotations

import os
import sys


def _lib_dir() -> str:
    """`scripts/lib/` of the DEVRC checkout that owns this hook.

    The wrapper has already resolved the install symlink, so this file's own
    directory is `<devrc>/scripts/git-hooks/` and `scripts/lib/` is its sibling.
    Overridable for tests.
    """
    override = os.environ.get("DEVRC_SESSION_TRAILER_LIB")
    if override:
        return override
    here = os.path.dirname(os.path.realpath(__file__))
    return os.path.join(os.path.dirname(here), "lib")


def main(argv) -> int:
    if len(argv) < 2:
        return 0
    msg_path = argv[1]

    sys.path.insert(0, _lib_dir())
    try:
        import session_trailer as st
    except Exception:
        return 0

    # 🔴 TEST-ONLY INJECTION, and it is what makes the SEAM testable in BOTH gate
    # tiers. Without it the end-to-end behaviour could only be exercised where a
    # real Claude ancestor happens to exist — green on the dev host, structurally
    # unrunnable in the nix sandbox, which is the config-blind suite RULES.md
    # warns about. It selects WHICH state file is read; it cannot supply or forge
    # an id, because the id still comes from a file only the recording hook writes.
    injected = os.environ.get("DEVRC_SESSION_TRAILER_PID")
    try:
        pid = int(injected) if injected else None
    except ValueError:
        pid = None

    session_id = st.lookup(pid=pid)
    if not session_id:
        return 0

    try:
        with open(msg_path, "r") as fh:
            message = fh.read()
    except Exception:
        return 0

    stamped = st.append_trailer(message, session_id)
    if stamped == message:
        return 0

    # 🔴 ATOMIC, because the naive version DESTROYED the message. `open(path,"w")`
    # truncates before writing, and the write can fail (ENOSPC, EIO, RLIMIT_FSIZE)
    # — measured: a 77-byte message became 0 bytes, the hook exited 0, and git
    # then refused the commit with "Aborting commit due to empty commit message".
    # So the operator lost both the message and the commit. Write beside it and
    # rename; a failed rename leaves the ORIGINAL intact.
    tmp = msg_path + ".session-trailer.tmp"
    try:
        with open(tmp, "w") as fh:
            fh.write(stamped)
        os.replace(tmp, msg_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        sys.exit(0)
