#!/usr/bin/env python3
"""WHICH MACHINE am I — one implementation, every consumer.

🔴 BOTH NixOS HOSTS REPORT HOSTNAME `nixos`. Nothing in this fleet can be told
apart by name, and several things in this repo hold PER-HOST, UNREPLICATED state
whose contents differ between the two machines:

    ~/.claude/analyze-service-index/   the /analyze-service index store — one
                                       git repo per scope, no remote, no sync.
                                       Measured 2026-08-27: workbench 115
                                       entries / 14 scopes, laptop 33 / 11, and
                                       across the four scope names present on
                                       BOTH there was exactly ONE entry name in
                                       common.

A tool that reads such a state and reports a GLOBAL fact ("the store has no
`vetr-app/` scope") is stating one host's disk as though it were the fleet's.
That is the defect this module exists to make un-writable: every consumer prints
the identity of the machine it actually read.

WHY THE LABEL ALONE IS NOT ENOUGH — MEASURED
--------------------------------------------
`host_label()` reads `ASIB_HOST` / `ACTIVITY_HOST` and falls back to the
hostname. Under the `analyze-service-index-backup` systemd unit that is
`workbench-<machine-id>` (nix/home.nix sets `ASIB_HOST=<name>-%m` and systemd
expands `%m`). In an INTERACTIVE or agent shell — which is every
`/analyze-service`, `/handoff` and `/resume` run — `ASIB_HOST` is unset and the
label degrades to `nixos`, i.e. to a value BOTH hosts print. Measured on the
workbench 2026-08-27: `printenv ASIB_HOST ACTIVITY_HOST` exits 1, `hostname`
says `nixos`.

So a header that printed `host_label()` alone would read as coverage while
providing none: it would be identical on the two machines it is supposed to
distinguish. `this_host()` joins the readable label to the machine id, which is
distinct per host by construction, needs no systemd, and is the same token the
backup keys already carry.

WHO OWNS WHAT
-------------
`host_label()` moved here from `scripts/analyze-service-index/backup.py`;
`machine_id()`'s read + shape-check moved here from
`scripts/analyze-service-index/restore-verify.py`. Both still expose their
original names (`B.host_label()`, `RV.machine_id()`) — the callers and their
tests are unchanged — but there is now ONE implementation of each.
`restore-verify.py` keeps its own `_MACHINE_ID_FILES` module global as the
injection seam its tests point at a fixture; only the LOGIC moved.
"""
from __future__ import annotations

import os
import re
import socket
from pathlib import Path

# Where `%m` comes from. A module-level tuple so a test can point a reader at
# synthetic files and exercise the REAL function, rather than re-implementing
# its shape check in the test — which would only ever prove the test agrees with
# itself.
MACHINE_ID_FILES: tuple[str, ...] = ("/etc/machine-id", "/var/lib/dbus/machine-id")

#: What `/etc/machine-id` is defined to hold: 32 lowercase hex digits.
_MACHINE_ID_SHAPE = re.compile(r"[0-9a-f]{32}")

#: Printed instead of an id when no file could be read or none had the right
#: shape. A SENTENCE, not an empty string: the whole point of this module is
#: that a host claim is never silently unqualified.
MACHINE_ID_UNREADABLE = "machine-id-unreadable"


def machine_id(files: "tuple[str, ...] | None" = None) -> str | None:
    """systemd's `%m` — the only reliable "which machine am I" signal here.

    🔴 SHAPE-CHECKED, NOT JUST NON-EMPTY. Returning whatever junk a file happened
    to hold would make `restore-verify.py`'s `prefix_belongs_to_this_host` answer
    True for any prefix containing it — an error in the FALSE DATA-LOSS
    direction, which is the one that gets someone to act destructively.

    Returns `None` when no candidate file is readable or none parses. The caller
    decides what an unknown machine means; this never guesses.
    """
    for p in (MACHINE_ID_FILES if files is None else files):
        try:
            v = Path(p).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _MACHINE_ID_SHAPE.fullmatch(v):
            return v
    return None


def host_label() -> str:
    """The READABLE name of this machine, for a human reading a key or a header.

    🔴 Both machines are hostname `nixos` (see MEMORY.md / SECRETS.md), and the
    per-host stores are DIVERGENT content. Without a distinct label per host the
    backup's objects would share a key prefix and silently evict each other under
    retention — turning the backup into a second way to lose the data.

    Precedence is `ASIB_HOST` then `ACTIVITY_HOST` then the hostname. Only the
    first is set by the backup unit; the fallback is the SHARED `nixos`, which is
    why `this_host()` exists.
    """
    for var in ("ASIB_HOST", "ACTIVITY_HOST"):
        v = os.environ.get(var)
        if v and v.strip():
            return re.sub(r"[^A-Za-z0-9._-]", "-", v.strip())
    return re.sub(r"[^A-Za-z0-9._-]", "-", socket.gethostname() or "unknown")


# How much of the machine id `this_host()` prints. 🔴 A PREFIX, NEVER THE WHOLE
# ID. `/etc/machine-id` is a stable, unique installation identifier, and
# `this_host()` is a DISPLAY value: it lands on every `/analyze-service`,
# `/handoff` and `/resume` run, in four rendered headers and three JSON payloads,
# and `/handoff` writes into `claudedocs/` — which is COMMITTED, in a PUBLIC
# repo. Tool output also gets pasted into PR bodies routinely. None of the four
# content gates screens for a machine id, so nothing downstream would catch it.
# 12 hex separates two machines with room to spare; the job here is to tell the
# fleet apart, not to identify the hardware. The repo's own prose already
# truncates it (`restore-verify.py` writes `workbench-d48f…`), so this makes the
# code match a convention the operator was already following by hand.
# 🔴 THIS DOES NOT TOUCH `machine_id()` OR `host_label()`. The backup object key
# is built from `host_label()` and stays FULL — truncating a key prefix would
# repoint every future object, which is a data-loss shape, not a privacy fix.
MACHINE_ID_DISPLAY_CHARS = 12


def this_host() -> str:
    """An identity that DIFFERS between the two machines on a hand-run.

    `<label>-<machine-id-prefix>` — see `MACHINE_ID_DISPLAY_CHARS` for why it is
    a prefix. Collapses to just the label when the label already carries the id
    (the shape the backup unit sets, which is operator-chosen and left as they
    set it), and to `<label>-machine-id-unreadable` when the id cannot be read at
    all — never to a bare, shared `nixos` that would read as a fact about the
    fleet.
    """
    label = host_label()
    mid = machine_id()
    if mid is None:
        return f"{label}-{MACHINE_ID_UNREADABLE}"
    if mid in label:
        return label
    return f"{label}-{mid[:MACHINE_ID_DISPLAY_CHARS]}"
