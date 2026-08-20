#!/usr/bin/env python3
"""REAL-PANE verification for `session-write`, in a PRIVATE tmux server.

🔴 WHY THIS EXISTS, AND WHY A GREEN UNIT SUITE IS NOT A SUBSTITUTE
-------------------------------------------------------------------
The hole this checks for was invisible to a fully green 117-test suite, because
every test in that suite stubbed the tmux seam. The defect was not in
session-write's logic at all — it was in a PREMISE about what tmux and readline
do with a payload, and a stub cannot disagree with a premise. Only a real pane
can. RULES.md: "verified in isolation is the new vacuous green — the defect
lives in the SEAM nobody owns", and an audit-driven fix RESETS the gate.

So this drives the REAL `session-write` — real `validate_text`, real
`_assert_allowed`, real argv construction, real `subprocess` — end to end
against a REAL bash pane, and reads the ANSWER OFF THE FILESYSTEM rather than
off the tool's own report.

🔴 IT NEVER TOUCHES THE OPERATOR'S TMUX. Every command carries `-L <unique
socket>`; the server is created here, killed here, and its socket removed here,
including on failure. `tmux kill-server` is never run without `-L`.

Resolution is pointed at the private server too: the raw `list-panes` /
`list-windows` / `list-clients` strings are read FROM IT with session-resolve's
own format constants, so the target session-write resolves is a real window on
the private server and the pane it writes to is a real bash process.

    usage:  python3 scripts/session-write-harness/real_pane_check.py
    exit:   0 = every case behaved as required, 1 = at least one did not
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, ".."))


def _load(name, path):
    spec = importlib.util.spec_from_loader(
        name, importlib.machinery.SourceFileLoader(name, path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: 🔴 THE INSTRUMENT'S OWN NEGATIVE CONTROL. A rig that reports "refused" is
#: only evidence if it CAN report "executed" — otherwise a rig wired to nothing
#: is indistinguishable from a working guard. Point this at a copy of
#: session-write carrying the pre-fix DENYLIST `validate_text` and the Ctrl-O
#: case must FAIL with "THE MARKER FILE WAS CREATED". Measured 2026-08-20: it
#: does. Without that run, every PASS below is unfalsified.
_SW_SRC = os.environ.get("SESSION_WRITE_SRC") or os.path.join(_SCRIPTS,
                                                              "session-write")

sw = _load("session_write", _SW_SRC)
sr = sw.sr

#: 🔴 The measured execution event. Ctrl-O is readline `operate-and-get-next`,
#: a BASH DEFAULT — not an exotic rebinding.
CTRL_O = "\x0f"

SOCKET = f"sw-verify-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def tmux(*argv, check=True):
    """Every tmux call in this file goes through here, so `-L <private>` cannot
    be forgotten on one of them and reach the operator's server."""
    proc = subprocess.run(["tmux", "-L", SOCKET, *argv],
                          capture_output=True, text=True, timeout=20)
    if check and proc.returncode != 0:
        raise RuntimeError(f"tmux {argv!r} -> rc {proc.returncode}: "
                           f"{proc.stderr.strip()}")
    return proc.stdout


def _runner(argv, timeout=None):
    """session-write's subprocess seam, rerouted onto the PRIVATE server.

    argv[0] is `tmux`; everything after it is exactly what session-write built,
    so the allowlist, the `-l --` separator and the payload are all the real
    ones. Only the socket is ours.
    """
    assert argv[0] == "tmux", argv
    proc = subprocess.run(["tmux", "-L", SOCKET, *argv[1:]],
                          capture_output=True, text=True,
                          timeout=timeout or 20)
    return proc.returncode, proc.stdout, proc.stderr


HOST = "workbench"


def build_sources(session):
    """Real tmux output from the private server, through the REAL resolver."""
    panes = tmux("list-panes", "-a", "-F", sr.PANE_FORMAT)
    # `#{host}` renders the machine's hostname; session-resolve compares it to
    # `local_host`, and both come from here, so G2 sees a genuinely local target.
    host = panes.splitlines()[0].split(sr.FIELD_SEP)[0] if panes.strip() else ""
    return host, sr.Sources(
        host=sr.HOST_ALL,
        local_host=host,
        panes_raw=panes,
        windows_raw=tmux("list-windows", "-a", "-F", sr.WINDOW_FORMAT),
        clients_raw=tmux("list-clients", "-F", sr.CLIENT_FORMAT),
        slot_table_text="",
        registry_records=[],
        sm_payload={"rows": [], "hosts": {}},
    )


class Case:
    def __init__(self, name, argv_text, must_refuse, why):
        self.name = name
        self.argv_text = argv_text
        self.must_refuse = must_refuse
        self.why = why


def main() -> int:
    if shutil.which("tmux") is None:
        print("tmux not on PATH — cannot verify against a real pane")
        return 1

    workdir = tempfile.mkdtemp(prefix="sw-verify-")
    marker = os.path.join(workdir, "EXECUTED")
    logdir = os.path.join(workdir, "logs")
    os.makedirs(logdir, exist_ok=True)

    failures = []
    try:
        # A bare bash, no rc files: `operate-and-get-next` is a DEFAULT binding,
        # so nothing in the operator's config is needed to make this fire.
        tmux("-f", "/dev/null", "new-session", "-d", "-s", "verify",
             "-x", "80", "-y", "24", "bash --norc --noprofile -i")
        time.sleep(1.0)

        host, src = build_sources("verify")
        pane = tmux("list-panes", "-t", "verify", "-F", "#{pane_id}").strip()
        window = tmux("list-windows", "-t", "verify",
                      "-F", "#{window_id}").strip()
        selector = f"verify:{window}"
        print(f"session-write src   : {_SW_SRC}")
        print(f"private tmux socket : {SOCKET}")
        print(f"real pane           : {pane} ({selector}) on host {host!r}")
        print(f"scratch dir         : {workdir}")
        print()

        cases = [
            Case("NEGATIVE CONTROL: printable payload, must be TYPED",
                 f"touch {marker}", False,
                 "proves the rig can deliver a payload to the pane at all — "
                 "without this a refusal proves nothing, since a broken rig "
                 "also types nothing"),
            Case("THE AUDITED BYPASS: same payload + Ctrl-O, must be REFUSED",
                 f"touch {marker}{CTRL_O}", True,
                 "measured to EXECUTE with no Enter sent, on the denylist "
                 "version of validate_text"),
            Case("ESC (0x1b), must be REFUSED", "echo hi\x1b", True,
                 "starts every escape sequence; zsh \\x1ba is accept-and-hold"),
            Case("Ctrl-V (0x16), must be REFUSED", "echo hi\x16", True,
                 "readline quoted-insert — smuggles the byte after it"),
        ]

        for case in cases:
            # Fresh log per case, inside the tool's own root, so G14 is
            # satisfied by a real path rather than bypassed.
            ws = sw.WriteSources(runner=_runner, resolver_src=src,
                                 log_path=os.path.join(logdir, "audit.log"),
                                 log_root=logdir)
            if os.path.exists(marker):
                os.remove(marker)

            code = sw.main(["type", selector, "--text", case.argv_text],
                           ws=ws)
            time.sleep(1.2)

            refused = code != 0
            executed = os.path.exists(marker)
            # What the pane actually holds, read off tmux rather than inferred.
            buf = tmux("capture-pane", "-p", "-t", pane).strip()

            ok = (refused == case.must_refuse) and not executed
            note = []
            if refused != case.must_refuse:
                note.append(f"expected refuse={case.must_refuse}, got {refused}")
            if executed:
                note.append("🔴 THE MARKER FILE WAS CREATED — IT EXECUTED")

            print(f"{'PASS' if ok else 'FAIL'}  {case.name}")
            print(f"        why      : {case.why}")
            print(f"        exit     : {code}"
                  f"  ({'refused' if refused else 'accepted'})")
            print(f"        executed : {executed}")
            print(f"        pane now : {buf.splitlines()[-1] if buf else '(empty)'}")
            if note:
                print(f"        PROBLEM  : {'; '.join(note)}")
            if not ok:
                failures.append(case.name)

            # Clear the pane's buffer between cases so a typed payload cannot
            # be completed by the next one (which is how the two negative
            # controls fused during the original measurement).
            tmux("send-keys", "-t", pane, "C-u", check=False)
            time.sleep(0.3)
            print()

        # ---- and the payload the tool is FOR must still arrive verbatim. ----
        ws = sw.WriteSources(runner=_runner, resolver_src=src,
                             log_path=os.path.join(logdir, "audit.log"),
                             log_root=logdir)
        legit = "restart the poller and report back"
        code = sw.main(["type", selector, "--text", legit], ws=ws)
        time.sleep(1.0)
        buf = tmux("capture-pane", "-p", "-t", pane).strip()
        ok = code == 0 and legit in buf
        print(f"{'PASS' if ok else 'FAIL'}  POSITIVE CONTROL: a legitimate "
              f"payload still types correctly")
        print(f"        exit     : {code}")
        print(f"        pane now : {buf.splitlines()[-1] if buf else '(empty)'}")
        print(f"        verbatim : {legit in buf}")
        if not ok:
            failures.append("legitimate payload")
        print()

    finally:
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"],
                       capture_output=True, text=True)
        sock = os.path.join(f"/tmp/tmux-{os.getuid()}", SOCKET)
        if os.path.exists(sock):
            os.remove(sock)
        print(f"cleaned up private server {SOCKET} (socket removed: "
              f"{not os.path.exists(sock)})")

    print()
    if failures:
        print(f"🔴 {len(failures)} FAILURE(S): {failures}")
        return 1
    print("ALL CASES BEHAVED AS REQUIRED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
