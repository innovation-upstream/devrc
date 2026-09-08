#!/usr/bin/env python3
"""Live inventory of the Civitai App Block fleet. Run this BEFORE any bulk pass.

🔴 WHY THIS IS A SCRIPT AND NOT A TABLE IN THE SKILL BODY

Every column below was hand-rediscovered during a single 2026-09-07 session, and
each one had already caused a wrong assumption by the time it was learned:

  - `sensei`'s default branch is `trunk`; the other six are `main`. A brief that
    said "branch off origin/main" was handed to seven agents; the sensei one had
    to notice and adapt on its own.
  - Six repos run two vitest projects, one runs a single project. `--project
    node` against the latter errors with "No projects matched" and RUNS NOTHING,
    which reads like a passing filter if you only check the exit code.
  - The version-lockstep assertion lives in `src/manifest.test.ts` in some repos
    and `src/version-lockstep.test.ts` in others. Two agents were told to add a
    guard that already existed under a different name.
  - Base clones sit on other people's feature branches with uncommitted work
    more often than they sit on the default branch, and a live session moved one
    underneath this work FOUR times in one afternoon.

A table recording any of that would have been wrong within the day. This reads
it from the repos and the platform, so it cannot rot — the failure mode becomes
"the script errors", not "the doc lies".

Usage:
    fleet.py                 # every repo, one row each
    fleet.py --json          # machine-readable, for driving a fan-out
    fleet.py --no-platform   # skip `civitai app status` (no CLI / no auth)
    fleet.py --no-fetch      # do not write to the shared clones

🔴 BY DEFAULT THIS RUNS `git fetch` IN SEVEN CLONES OTHER SESSIONS ARE STANDING
IN. That is a write to a shared checkout, so it is stated here rather than left
to be discovered, and `--no-fetch` turns it off. To run without touching
anything at all you need BOTH `--no-fetch` and `--no-platform`.

The fetch result is a column in the table and a key in `--json`
(`fetch: ok|FAILED|skipped`), and a trailing stderr note names every row whose
refs may be stale. `skipped` is surfaced as loudly as `FAILED` because
`--no-fetch` produces byte-IDENTICAL staleness — an earlier round warned about
one and left the other indistinguishable from fresh data.

🔴 A FAILED FETCH DOES NOT DISCARD THE ROW. It is a staleness warning, not a
read failure: the columns were read, they may just be old. An earlier round
folded it into `error`, and an error row prints as a bare `!!` INSTEAD of the
row — so a single unreachable remote collapsed all seven rows and this tool
emitted no inventory at all. Serving probably-correct data with a caveat beats
serving none.

Exit is non-zero when a row could not be READ (missing checkout, non-repository,
unreadable column) or when a fetch FAILED. `--no-fetch` alone does not fail: you
asked for it. An early draft promised this while setting the flag in exactly one
place (`isdir`), so a directory whose refs were unreadable produced a full row of
plausible defaults and exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# The fleet. The one genuinely static fact — which repos exist and where — and
# even this is checked: a missing checkout is reported, never skipped.
WORKSPACE = os.path.expanduser("~/workspace/civit")

REPOS = [
    # (local dir, github slug, app slug)
    ("civitai-app-gen-matrix", "ZacxDev/civitai-app-gen-matrix", "gen-matrix"),
    ("civitai-app-custom-generators", "ZacxDev/civitai-app-custom-generators", "custom-generators"),
    ("civitai-app-model-benchmarking", "ZacxDev/civitai-app-model-benchmarking", "model-benchmarking"),
    ("civitai-app-playable-collections", "ZacxDev/civitai-app-playable-collections", "playable-collections"),
    ("civitai-app-requests", "ZacxDev/civitai-app-requests", "app-requests"),
    ("civitai-app-sensei", "ZacxDev/civitai-app-sensei", "sensei"),
    ("civitai-block-generate-from-model", "ZacxDev/civitai-block-generate-from-model", "generate-from-model"),
]

_RUN = subprocess.run  # patched by tests; see app_state.py for why this seam exists

# 🔴 THE SENTINEL, AND WHY EVERY READER MUST USE IT.
#
# An audit of the first draft found this file doing the exact thing it exists to
# prevent: `lockfile()` returned the literal "none" when git failed, so a repo
# whose `pnpm-lock.yaml` was simply unreadable reported as having NO lockfile —
# a fabricated value, in the column that decides the manifest/lockfile check.
# `checked_out` reported "(detached)" and `dirty_files` reported "0" for a
# directory that was not a git repository at all, and "0 dirty" is precisely the
# value that makes a caller proceed.
#
# So: `_git` returns None for FAILED, distinct from "" for "ran, empty output",
# and every reader converts None to UNREADABLE rather than to a plausible
# default. A column that could not be read must never be mistakable for one that
# was.
UNREADABLE = "?"


def _git(repo: str, *args: str) -> str | None:
    """stdout on success; None when git failed. NEVER "" for a failure — the
    caller cannot distinguish that from a legitimately empty result."""
    proc = _RUN(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def default_branch(repo: str) -> str:
    """Read it; never assume `main`.

    `origin/HEAD` is the remote's own answer. Falling back to a guess would
    reintroduce the exact bug this column exists to prevent, so an unreadable
    value is reported as "?" rather than defaulted.
    """
    head = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    return head.rsplit("/", 1)[-1] if head else UNREADABLE


def _json_field(repo: str, ref: str, path: str, field: str) -> str:
    raw = _git(repo, "show", f"{ref}:{path}")
    if raw is None or not raw:
        return UNREADABLE
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return UNREADABLE
    # "-" means the file parsed and the field is genuinely absent — a real
    # answer, distinct from UNREADABLE.
    return str(parsed.get(field, "-"))


def vitest_projects(repo: str, ref: str) -> str:
    """Whether the repo declares MULTIPLE vitest projects — the `--project` trap.

    Deliberately reported as `1` / `2+`, not as a count: the body tests for a
    `projects:` key, which cannot distinguish two from three. An earlier draft
    labelled this "how many vitest projects" and printed `2` for any multi-
    project repo, which is a number asserted rather than counted.
    """
    cfg = _git(repo, "show", f"{ref}:vite.config.ts")
    if cfg is None or not cfg:
        return UNREADABLE
    return "2+" if "projects:" in cfg else "1"


def lockstep_guard_home(repo: str, ref: str) -> str:
    """Which file carries the manifest/package version-lockstep assertion."""
    listing = _git(repo, "ls-tree", "--name-only", f"{ref}:src")
    if listing is None:
        return UNREADABLE
    names = set(listing.splitlines())
    if "version-lockstep.test.ts" in names:
        return "version-lockstep"
    if "manifest.test.ts" in names:
        return "manifest.test"
    return "none?"


def lockfile(repo: str, ref: str) -> str:
    """🔴 Returns UNREADABLE, never "none", when the ref cannot be read.

    The first draft returned "none" on git failure — a fabricated answer in the
    column that decides whether `buildCommand` and the committed lockfile agree.
    A repo with a `pnpm-lock.yaml` it simply could not read reported as having
    no lockfile at all.
    """
    listing = _git(repo, "ls-tree", "--name-only", ref)
    if listing is None:
        return UNREADABLE
    names = set(listing.splitlines())
    found = [n for n in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock") if n in names]
    return "+".join(found) if found else "none"


def inspect(directory: str, slug: str, app: str, *, fetch: bool = True) -> dict[str, str]:
    """One row. Sets "error" whenever a column could not be READ, not only when
    the checkout is missing.

    🔴 The first draft set "error" at exactly one place — `isdir` — so a
    directory that existed but was not a git repository, or whose ref was
    unresolvable, produced a full row of plausible-looking defaults and an exit
    code of 0. A caller branching on that rc proceeded against repos it had read
    nothing from. Anything that returns UNREADABLE is now an error.
    """
    repo = os.path.join(WORKSPACE, directory)
    row: dict[str, str] = {"app": app, "dir": directory, "slug": slug}

    if not os.path.isdir(repo):
        return {**row, "error": "checkout missing"}
    if _git(repo, "rev-parse", "--git-dir") is None:
        return {**row, "error": "not a git repository"}

    # 🔴 A FETCH IS A WRITE TO A CLONE OTHER SESSIONS ARE STANDING IN, and it is
    # opt-out rather than silent. Its failure is recorded, because a fetch that
    # failed leaves every column below describing a STALE ref while this file
    # claims the inventory "cannot rot".
    if fetch:
        proc = _RUN(
            ["git", "-C", repo, "fetch", "origin", "--quiet"],
            capture_output=True,
            text=True,
            check=False,
        )
        row["fetch"] = "ok" if proc.returncode == 0 else "FAILED"
    else:
        row["fetch"] = "skipped"

    base = default_branch(repo)
    ref = f"origin/{base}"

    branch = _git(repo, "branch", "--show-current")
    status = _git(repo, "status", "--porcelain")

    row.update(
        {
            "default_branch": base,
            # 🔴 The base clone's CURRENT branch, which is frequently NOT the
            # default and frequently carries someone's uncommitted work. Branch
            # off `ref`, never off local HEAD.
            "checked_out": UNREADABLE if branch is None else (branch or "(detached)"),
            "dirty_files": (
                UNREADABLE
                if status is None
                else str(len([ln for ln in status.splitlines() if ln]))
            ),
            "manifest_version": _json_field(repo, ref, "block.manifest.json", "version"),
            "package_version": _json_field(repo, ref, "package.json", "version"),
            "build_command": _json_field(repo, ref, "block.manifest.json", "buildCommand"),
            "lockfile": lockfile(repo, ref),
            "vitest_projects": vitest_projects(repo, ref),
            "lockstep_guard": lockstep_guard_home(repo, ref),
        }
    )

    # 🔴 A FAILED FETCH IS A STALENESS WARNING, NOT AN ERROR — and the
    # distinction is the difference between degraded data and no data.
    #
    # An earlier round folded it into `error`, and `main()` prints an error row
    # as a bare `!!` line INSTEAD of the row. So one unreachable remote — no SSH
    # agent, no network, a VPN down — collapsed every one of the seven rows to
    # `!!`, and the tool SKILL.md says to start every bulk pass with emitted no
    # inventory at all. The columns had all been read successfully; they were
    # merely possibly-stale, which is exactly what the warning says. Discarding
    # probably-correct data is a worse failure than serving it with a caveat.
    #
    # `error` is therefore reserved for a row that could not be READ: a missing
    # checkout, a non-repository, or an unreadable column.
    unread = sorted(k for k, v in row.items() if v == UNREADABLE)
    if unread:
        row["error"] = "unreadable: " + ", ".join(unread)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--no-platform", action="store_true", help="skip `civitai app status`")
    ap.add_argument(
        "--no-fetch",
        action="store_true",
        help="do not `git fetch` the clones (they are shared; a fetch writes to them)",
    )
    args = ap.parse_args()

    rows = [inspect(d, s, a, fetch=not args.no_fetch) for d, s, a in REPOS]

    if not args.no_platform:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import app_state  # noqa: PLC0415 — imported only when the platform is consulted

        try:
            parsed = app_state.parse_rows(app_state.read_status())
        except app_state.UnknownState:
            # 🔴 DO NOT SWALLOW THIS. An unrecognised platform state is the whole
            # reason `app_state.parse_rows` raises rather than guessing, and the
            # first draft caught it here under a bare `except Exception` — so the
            # loud guard became a stderr note with a SUCCESS exit, through the
            # very entry point this skill tells you to run first. `preview-live`
            # was found only because that guard was allowed to reach a human.
            raise
        except Exception as exc:  # noqa: BLE001 — CLI absent/unauthed is fine
            for row in rows:
                row.setdefault("submit_floor", "unread")
                row.setdefault("platform", "unread")
            print(f"note: platform state unread ({exc})", file=sys.stderr)
        else:
            for row in rows:
                if "error" in row:
                    continue
                floor = app_state.submit_floor(parsed, row["app"])
                row["submit_floor"] = floor or "none"
                state = app_state.resolve(parsed, row["app"], row["manifest_version"])
                row["platform"] = f"{state['review']}/{state['deploy']}"

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        hdr = f"{'app':<21} {'branch':<7} {'checked-out':<26} {'dirty':>5} {'ver':<9} {'build':<16} {'lock':<16} {'proj':>4} {'guard':<17} {'fetch':<8} {'platform'}"
        print(hdr)
        print("-" * len(hdr))
        for r in rows:
            if "error" in r:
                print(f"{r['app']:<21} !! {r['error']}")
                continue
            ver = r["manifest_version"]
            if r["package_version"] != ver:
                ver = f"{ver}/{r['package_version']}!"  # lockstep broken — loud
            print(
                f"{r['app']:<21} {r['default_branch']:<7} {r['checked_out'][:26]:<26} "
                f"{r['dirty_files']:>5} {ver:<9} {r['build_command'][:16]:<16} "
                f"{r['lockfile'][:16]:<16} {r['vitest_projects']:>4} "
                # The fetch column exists because the docstring claimed the
                # result was "reported per row" while the table had no such
                # column — true of --json only. `skipped` is shown for the same
                # reason `FAILED` is: --no-fetch produces byte-identical
                # staleness to a failed fetch, and an earlier round warned about
                # one while leaving the other indistinguishable from fresh data.
                f"{r['lockstep_guard']:<17} {r['fetch']:<8} "
                # `-` is a REAL deploy state meaning "no deploy for this row",
                # so it must never also mean "not consulted".
                f"{r.get('platform', 'not-consulted')}"
            )

    stale = [r["app"] for r in rows if r.get("fetch") in {"FAILED", "skipped"}]
    if stale and not args.json:
        why = "not fetched" if args.no_fetch else "fetch failed"
        print(f"\nnote: {why} — refs may be stale for: {', '.join(stale)}", file=sys.stderr)

    # A row that could not be READ is an error. A row that is merely possibly
    # STALE is not — it still exits non-zero when the fetch FAILED (something
    # went wrong) but not when --no-fetch skipped it (you asked for that).
    failed_fetch = any(r.get("fetch") == "FAILED" for r in rows)
    return 1 if failed_fetch or any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
