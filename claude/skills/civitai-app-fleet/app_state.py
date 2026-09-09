#!/usr/bin/env python3
"""Authoritative read of a Civitai App's submission state.

🔴 WHY THIS IS A SCRIPT AND NOT A PARAGRAPH SAYING "BE CAREFUL"

Two separate misreads of this exact data happened in one session, and both
looked like an answer rather than an error:

1. `civitai app status <slug>` returns only the NEWEST submission. A second
   submission of the SAME version — withdrawn moments later — sat on top of a
   healthy `approved/building` row, so the per-slug view reported `withdrawn`
   for an app that was building fine. A human reading that would have reported
   a failed release that never happened.

2. A hand-rolled parser enumerated `live|failed|rejected|building|approved|
   pending` and fell through to `approved` for the real state `deploying`,
   which it had never heard of. It under-reported progress indefinitely and
   never once errored.

So: this reads the LIST view (every row, every version), and it raises on a
state token it does not recognise instead of guessing. An unknown state is a
platform change, and the loud failure is the point — case 2 is only invisible
when the fall-through is silent.

Offline-testable by design: set CIVITAI_STATUS_FILE to a captured `civitai app
status` dump and no network call is made. `scripts/tests/test_civitai_app_fleet.py`
drives every branch below through that seam.
"""

from __future__ import annotations

import os
import subprocess
import sys

# 🔴 THE SEAM TESTS PATCH — and the reason it exists rather than tests reaching
# for `app_state.subprocess.run`.
#
# `app_state.subprocess` IS the shared `subprocess` module object, so patching
# `.run` through it mutates subprocess for every other test in the same worker.
# devrc's suite installs a suite-wide no-launch guard on exactly that surface
# (`scripts/testlib/nolaunch_plugin.py`), and patch/restore through this module
# reached into it: measured 2026-09-08, adding this file's tests took the full
# suite from 7 pre-existing failures to 18, all of them in subprocess-using
# tests elsewhere. A same-named, same-count dummy file caused ZERO extra
# failures, which is what ruled out xdist redistribution and named the content.
#
# Patch THIS name instead — it is local to this module and reaches nothing else.
_RUN = subprocess.run

# Review states a submission row can carry (column 3).
# Provenance, same standard as DEPLOY_STATES below: `approved`, `withdrawn` and
# `rejected` all occur in the live listing 2026-09-08; `pending` was observed on
# a submission the same day, between submit and moderator review. An audit
# pointed out that the "a too-wide set fails silently" argument was made for
# DEPLOY_STATES and then not applied here — widening this set passed a fully
# green suite, because its only guard pinned the single literal `marinated`.
REVIEW_STATES = {"pending", "approved", "rejected", "withdrawn"}

# Deploy states (column 4). "-" means no deploy for this row.
# 🔴 ORDERED WORST-TO-BEST IS NOT THE POINT — completeness is. Adding a state
# the platform introduced is a one-line change here; the alternative (a
# fall-through default) is what silently mis-reported `deploying` for an hour.
#
# 🔴 EVERY MEMBER WAS OBSERVED, none guessed — and that distinction is not
# pedantry. The first draft of this set was wrong in BOTH directions: it omitted
# `preview-live` (live on `w6-ui-dogfood`) and included `queued`, which occurs
# nowhere in the listing. The omission surfaced on `fleet.py`'s first real run,
# via this guard raising, within an hour of the file being merged — the guard
# working exactly as designed, on its author. Re-derive with
#     civitai app status | awk 'NF>=4 && $2 ~ /^[0-9]/ {print $4}' | sort -u
# rather than from memory, and add a member only after seeing it.
#
# Provenance: `-`, `live`, `failed`, `preview-live` from the live listing
# 2026-09-08; `building` and `deploying` observed on submissions the same day.
DEPLOY_STATES = {"-", "building", "deploying", "live", "failed", "preview-live"}

# A row is authoritative for a version unless it was withdrawn. A withdrawn row
# is a real event, but it never describes what the OTHER submission of the same
# version is doing — which is exactly the trap in case 1 above.
_SUPERSEDED_BY_ANY_OTHER_ROW = "withdrawn"


class UnknownState(RuntimeError):
    """A state token this script has never seen. Never guess — say so."""


def read_status(argv_source: str | None = None) -> str:
    """The raw `civitai app status` list output.

    CIVITAI_STATUS_FILE (or an explicit path) short-circuits the CLI so tests,
    and a postmortem on a captured dump, need no network and no auth.
    """
    source = argv_source or os.environ.get("CIVITAI_STATUS_FILE")
    if source:
        with open(source, encoding="utf-8") as handle:
            return handle.read()
    proc = _RUN(
        ["civitai", "app", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"`civitai app status` exited {proc.returncode}: {proc.stderr.strip()[:400]}"
        )
    return proc.stdout


def parse_rows(text: str) -> list[dict[str, str]]:
    """Every submission row, as dicts. Header/notes/blank lines are skipped.

    Deliberately positional (the CLI prints a fixed-width table) but validated:
    a row whose columns do not parse as states is skipped as prose rather than
    guessed at, and a row that parses but carries an UNKNOWN state raises.
    """
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        block_id, version, review, deploy = parts[0], parts[1], parts[2], parts[3]
        # The header row and the CLI's prose notes land here too; the cheapest
        # reliable discriminator is that a data row's 3rd column is a review
        # state. Anything else is not a row, and is not an error either.
        if review not in REVIEW_STATES:
            if review.lower() in {"status", "state"} or not version[:1].isdigit():
                continue
            raise UnknownState(
                f"unrecognised review state {review!r} in row: {line.strip()!r} — "
                "the platform may have added one; add it to REVIEW_STATES rather "
                "than letting this fall through"
            )
        if deploy not in DEPLOY_STATES:
            raise UnknownState(
                f"unrecognised deploy state {deploy!r} in row: {line.strip()!r} — "
                "add it to DEPLOY_STATES. This guard exists because a silent "
                "fall-through mis-reported `deploying` as `approved`."
            )
        rows.append(
            {"app": block_id, "version": version, "review": review, "deploy": deploy}
        )
    return rows


def resolve(rows: list[dict[str, str]], app: str, version: str) -> dict[str, str]:
    """The authoritative row for one app+version.

    Returns the non-withdrawn row when one exists, because a withdrawn
    duplicate says nothing about the sibling submission of the same version.
    Returns the withdrawn row only when it is the ONLY row — then it is the
    answer, not noise.

    TIE-BREAK, when MORE THAN ONE non-withdrawn row shares a version: the FIRST
    is returned, and the CLI lists newest-first, so that is the newest. An audit
    pointed out the docstring said "the non-withdrawn row" in the singular while
    the code already handled a plural; the fixture only ever had one, so nothing
    pinned it. A `pending/-` row above an `approved/live` row therefore resolves
    to `pending/-`, which is correct — it is the newer submission — but it is a
    behaviour, not an accident, so it is written down and tested.
    """
    matching = [r for r in rows if r["app"] == app and r["version"] == version]
    if not matching:
        return {"app": app, "version": version, "review": "none", "deploy": "-"}
    live = [r for r in matching if r["review"] != _SUPERSEDED_BY_ANY_OTHER_ROW]
    return (live or matching)[0]


def submit_floor(rows: list[dict[str, str]], app: str) -> str | None:
    """The highest version ON RECORD for an app — the floor `civitai app submit`
    refuses to submit at or below.

    🔴 On RECORD, not deployed. A withdrawn submission still occupies the floor,
    so an app whose latest row is `withdrawn` cannot be re-submitted at that
    same version. Two apps in this fleet were in exactly that state.
    """
    versions = [r["version"] for r in rows if r["app"] == app]
    if not versions:
        return None

    def key(v: str) -> tuple:
        return tuple(int(p) if p.isdigit() else -1 for p in v.split("."))

    return max(versions, key=key)


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: app_state.py <app> [version]   # omit version for the floor",
            file=sys.stderr,
        )
        return 2
    app = sys.argv[1]
    rows = parse_rows(read_status())
    if len(sys.argv) >= 3:
        row = resolve(rows, app, sys.argv[2])
        print(f"{row['app']} {row['version']} {row['review']}/{row['deploy']}")
        return 0 if row["deploy"] == "live" else 1
    floor = submit_floor(rows, app)
    print(f"{app} floor={floor or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
