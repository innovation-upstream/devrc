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

The fetch result is a `fetch: ok|FAILED|skipped` column in the table and a key
in `--json` — on every row that was inspected. A row that returned early
(missing checkout, non-repository) has neither, because no fetch was attempted.
A trailing stderr note names every possibly-stale row in table mode; `--json`
omits it, on the grounds that a JSON consumer should read the per-row `fetch`
key rather than parse stderr.

`skipped` is shown in the same column and note as `FAILED` because `--no-fetch`
produces byte-IDENTICAL staleness, and an earlier round warned about one while
leaving the other indistinguishable from fresh data. They are NOT identical in
every respect and the difference is deliberate: `FAILED` exits non-zero,
`skipped` does not. Something went wrong in the first case; you asked for the
second.

🔴 A FAILED FETCH DOES NOT DISCARD THE ROW. It is a staleness warning, not a
read failure: the columns were read, they may just be old. An earlier round
folded it into `error`, and an error row prints as a bare `!!` INSTEAD of the
row — so a GLOBAL fetch failure (no network, no SSH agent, VPN down) collapsed
every row and this tool emitted no inventory at all. Serving probably-correct
data with a caveat beats serving none.

⚠ Measured, because the first wording of this paragraph overstated it by 7×:
ONE unreachable remote collapses exactly ONE row — the other six print in full.
The all-seven case needs a cause common to all seven. The bug was real; the
blast radius written down for it was not, and a reader told the rows are coupled
when they are independent will mis-triage the next occurrence.

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


def _ref_exists(repo: str, ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") is not None


def vitest_projects(repo: str, ref: str) -> str:
    """Whether the repo declares MULTIPLE vitest projects — the `--project` trap.

    Reported as `1` / `2+`, not as a count: the body tests for a `projects:`
    key, which cannot distinguish two from three. An earlier draft labelled this
    "how many vitest projects" and printed `2` for any multi-project repo, which
    is a number asserted rather than counted.

    🔴 `no-vite` is NOT `?`. `git show <ref>:vite.config.ts` fails both when the
    REF is unreadable and when the FILE is simply absent, and an earlier draft
    collapsed the two into UNREADABLE — the exact conflation the sentinel above
    exists to prevent, and one that made a perfectly readable repo (a non-vite
    block, or one that renamed to vite.config.mts) look like a failed read. The
    ref is checked first, so "I read it and there is none" is distinct from "I
    could not read it".
    """
    cfg = _git(repo, "show", f"{ref}:vite.config.ts")
    if cfg:
        return "2+" if "projects:" in cfg else "1"
    return "no-vite" if _ref_exists(repo, ref) else UNREADABLE


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
    # An earlier round folded it into `error`, and `main()` printed an error row
    # as a bare `!!` line INSTEAD of the row. A GLOBAL fetch failure therefore
    # collapsed every row and the tool emitted no inventory at all. The columns
    # had all been read successfully; they were merely possibly-stale.
    # Discarding probably-correct data is a worse failure than serving it with a
    # caveat.
    #
    # 🔴 AND THE SAME RULE BINDS THE UNREADABLE-COLUMN AXIS. An audit caught the
    # round that wrote the sentence above applying it to the fetch axis ONLY: a
    # row with ONE unreadable column still lost its other ten readable facts to
    # a `!!` line — branch, checked-out, dirty count, both versions,
    # buildCommand, lockfile, guard file, platform. A repo with no
    # `vite.config.ts` on the default ref is enough to trigger it. So `error` is
    # recorded (and the exit code is non-zero), but `main()` still PRINTS the
    # row with `?` in the columns it could not read. Only a row with no columns
    # at all — a missing checkout or a non-repository — is replaced.
    unread = sorted(k for k, v in row.items() if v == UNREADABLE)
    if unread:
        row["error"] = "unreadable: " + ", ".join(unread)
    return row


def _inspected(row: dict[str, str]) -> bool:
    """Did `inspect()` get far enough to read ANY column?

    🔴 ONE PREDICATE, ONE PLACE. `main()` asks this question twice — the
    printer's `!!` discriminator, and the platform loop's never-inspected case —
    and open-coding the same question at two sites is exactly how the loop and
    the printer came to disagree in the first place (the `not-consulted` double
    meaning fixed a round ago). `default_branch` is the first key `inspect()`
    writes past BOTH of its early returns, so its presence is precisely "this
    row has columns"; a missing checkout or a non-repository has none.
    """
    return "default_branch" in row


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
                # The floor is keyed on the APP SLUG alone, so it is answerable
                # even for a row whose version could not be read. It is computed
                # before the version guard below for that reason: reporting it
                # as unavailable would be the same fabrication in the other
                # direction — claiming a read failed when it succeeded.
                floor = app_state.submit_floor(parsed, row["app"])
                row["submit_floor"] = floor or "none"
                # 🔴 A ROW THAT WAS NEVER INSPECTED IS NOT A ROW WHOSE VERSION
                # COULD NOT BE READ, and for one round it said it was. Moving
                # the floor above the version guard let an early-return row —
                # missing checkout, non-repository — fall into the guard below
                # and acquire `version-unread`, which claims a manifest was
                # consulted and found unreadable on a row where no file was ever
                # opened. Table mode never showed it (such a row prints `!!`),
                # but `--json` did, and `--json` is what drives a fan-out.
                if not _inspected(row):
                    row["platform"] = "not-inspected"
                    continue
                # Enrich anything with a usable version. An `error` row is not
                # automatically unusable — only one whose VERSION could not be
                # read is, and skipping on `error` alone denied platform state
                # to rows that had it available.
                #
                # 🔴 THE SKIP NEEDS ITS OWN WORD. This predicate is narrower than
                # the `!!` discriminator in the printer below, so a row can reach
                # the table AND be skipped here — and it then printed
                # `not-consulted` on a run where the platform WAS consulted,
                # giving that string the same double meaning the `-` comment
                # below forbids. Four cases, four strings: `not-consulted`
                # (--no-platform), `unread` (the CLI failed for every row),
                # `not-inspected` (consulted, but this row has no columns at
                # all), `version-unread` (consulted, this row was inspected and
                # had no version to ask about). Do NOT drop the guard instead —
                # `resolve(parsed, app, "?")` answers `none/-`, a real-looking
                # state for a version that was never read, which is worse than
                # any of the four.
                if row.get("manifest_version", UNREADABLE) == UNREADABLE:
                    row["platform"] = "version-unread"
                    continue
                state = app_state.resolve(parsed, row["app"], row["manifest_version"])
                row["platform"] = f"{state['review']}/{state['deploy']}"

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        # 🔴 `proj` is 7 wide, not 4, because `no-vite` is 7 characters and
        # Python's `:>N` PADS but never TRUNCATES — an over-long value silently
        # pushes `guard`, `fetch` and `platform` right on that row alone. Any
        # widening of a `vitest_projects()` return value has to move this number
        # and the matching one in the row below together.
        hdr = f"{'app':<21} {'branch':<7} {'checked-out':<26} {'dirty':>5} {'ver':<9} {'build':<16} {'lock':<16} {'proj':>7} {'guard':<17} {'fetch':<8} {'platform'}"
        print(hdr)
        print("-" * len(hdr))
        for r in rows:
            # Only a row with NO columns is replaced — a missing checkout or a
            # non-repository, where `inspect()` returned early. A row with an
            # unreadable COLUMN still prints, with `?` where the read failed;
            # its `error` key and the non-zero exit already say so.
            if not _inspected(r):
                print(f"{r['app']:<21} !! {r.get('error', 'unknown')}")
                continue
            ver = r["manifest_version"]
            pkg = r["package_version"]
            if pkg != ver:
                # 🔴 `!` ASSERTS A LOCKSTEP VIOLATION, so it may only be printed
                # when BOTH sides were actually read. `?` differs from every real
                # version string, so a bare `!=` fabricated the marker for a row
                # whose manifest version could not be read at all: `?/0.8.8!`
                # claims a comparison that never happened. Both values are still
                # shown — that part is a real reading — the ASSERTION is dropped.
                broken = UNREADABLE not in (ver, pkg)
                ver = f"{ver}/{pkg}{'!' if broken else ''}"
                # ⚠ KNOWN AND DELIBERATE: a two-version cell overflows this
                # column's 9 and shifts the rest of THIS row. Unlike `proj`,
                # whose value set is closed and short, a version string has no
                # bound, so no width fixes it and truncating would turn a read
                # version into a fabricated one. Overflowing loudly is the least
                # bad of the three; the alignment guard is scoped accordingly.
                #
                # ⚠ AND `ver` IS NOT THE ONLY SUCH CELL — the sentence above
                # named it as the exception to `proj` and read as exhaustive,
                # which it is not. `branch` is the other one: `default_branch()`
                # returns whatever `origin/HEAD` names, which is unbounded, and
                # its cell (`:<7`, header and row alike) is padded but never
                # truncated for the same reason this one is. Measured with a
                # `development` default branch: `platform` starts at column 142
                # in the header and in a `main` row, at 146 in that row. Zero
                # blast radius TODAY — every default branch in REPOS is `main`
                # or `trunk` — so nothing is being widened; what is fixed is the
                # claim. `checked-out`, `build` and `lock` are the cells that
                # ARE bounded, by an explicit `[:N]` slice.
            print(
                f"{r['app']:<21} {r['default_branch']:<7} {r['checked_out'][:26]:<26} "
                f"{r['dirty_files']:>5} {ver:<9} {r['build_command'][:16]:<16} "
                f"{r['lockfile'][:16]:<16} {r['vitest_projects']:>7} "
                # The fetch column exists because the docstring claimed the
                # result was "reported per row" while the table had no such
                # column — true of --json only. `skipped` is shown for the same
                # reason `FAILED` is: --no-fetch produces byte-identical
                # staleness to a failed fetch, and an earlier round warned about
                # one while leaving the other indistinguishable from fresh data.
                f"{r['lockstep_guard']:<17} {r['fetch']:<8} "
                # `-` is a REAL deploy state meaning "no deploy for this row",
                # so it must never also mean "not consulted". By the same rule
                # `not-consulted` reaches this cell ONLY via --no-platform (or
                # the CLI raising before the loop): a row the loop consulted and
                # then skipped says `version-unread`, set above.
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
