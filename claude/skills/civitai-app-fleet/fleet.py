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
    fleet.py --no-platform   # skip `civitai app status` (offline / no auth)

Exit non-zero if any repo could not be inspected, so a fan-out built on this
cannot silently skip a repo it failed to read.
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


def _git(repo: str, *args: str) -> str:
    proc = _RUN(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def default_branch(repo: str) -> str:
    """Read it; never assume `main`.

    `origin/HEAD` is the remote's own answer. Falling back to a guess would
    reintroduce the exact bug this column exists to prevent, so an unreadable
    value is reported as "?" rather than defaulted.
    """
    head = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    return head.rsplit("/", 1)[-1] if head else "?"


def _json_field(repo: str, ref: str, path: str, field: str) -> str:
    raw = _git(repo, "show", f"{ref}:{path}")
    if not raw:
        return "?"
    try:
        return str(json.loads(raw).get(field, "-"))
    except json.JSONDecodeError:
        return "?"


def vitest_projects(repo: str, ref: str) -> str:
    """How many vitest projects — the `--project node` trap.

    A wrong filter does not fail loudly; it matches nothing and runs nothing.
    """
    cfg = _git(repo, "show", f"{ref}:vite.config.ts")
    if not cfg:
        return "?"
    return "2" if "projects:" in cfg else "1"


def lockstep_guard_home(repo: str, ref: str) -> str:
    """Which file carries the manifest/package version-lockstep assertion."""
    listing = _git(repo, "ls-tree", "--name-only", f"{ref}:src")
    names = set(listing.splitlines())
    if "version-lockstep.test.ts" in names:
        return "version-lockstep"
    if "manifest.test.ts" in names:
        return "manifest.test"
    return "none?"


def lockfile(repo: str, ref: str) -> str:
    names = set(_git(repo, "ls-tree", "--name-only", ref).splitlines())
    found = [n for n in ("pnpm-lock.yaml", "package-lock.json", "yarn.lock") if n in names]
    return "+".join(found) if found else "none"


def inspect(directory: str, slug: str, app: str) -> dict[str, str]:
    repo = os.path.join(WORKSPACE, directory)
    if not os.path.isdir(repo):
        return {"app": app, "dir": directory, "error": "checkout missing"}

    _RUN(["git", "-C", repo, "fetch", "origin", "--quiet"], capture_output=True, check=False)
    base = default_branch(repo)
    ref = f"origin/{base}"

    checked_out = _git(repo, "branch", "--show-current") or "(detached)"
    dirty = len([ln for ln in _git(repo, "status", "--porcelain").splitlines() if ln])

    return {
        "app": app,
        "dir": directory,
        "slug": slug,
        "default_branch": base,
        # 🔴 The base clone's CURRENT branch, which is frequently NOT the default
        # and frequently carries someone's uncommitted work. Branch off `ref`,
        # never off local HEAD.
        "checked_out": checked_out,
        "dirty_files": str(dirty),
        "manifest_version": _json_field(repo, ref, "block.manifest.json", "version"),
        "package_version": _json_field(repo, ref, "package.json", "version"),
        "build_command": _json_field(repo, ref, "block.manifest.json", "buildCommand"),
        "lockfile": lockfile(repo, ref),
        "vitest_projects": vitest_projects(repo, ref),
        "lockstep_guard": lockstep_guard_home(repo, ref),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--no-platform", action="store_true", help="skip civitai app status")
    args = ap.parse_args()

    rows = [inspect(d, s, a) for d, s, a in REPOS]

    if not args.no_platform:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import app_state  # noqa: PLC0415 — optional, and only when asked for

            parsed = app_state.parse_rows(app_state.read_status())
            for row in rows:
                if "error" in row:
                    continue
                floor = app_state.submit_floor(parsed, row["app"])
                row["submit_floor"] = floor or "none"
                state = app_state.resolve(parsed, row["app"], row["manifest_version"])
                row["platform"] = f"{state['review']}/{state['deploy']}"
        except Exception as exc:  # platform is optional; NEVER fake it
            for row in rows:
                row.setdefault("submit_floor", "unread")
                row.setdefault("platform", "unread")
            print(f"note: platform state unread ({exc})", file=sys.stderr)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        hdr = f"{'app':<21} {'branch':<7} {'checked-out':<26} {'dirty':>5} {'ver':<9} {'build':<16} {'lock':<16} {'proj':>4} {'guard':<17} {'platform'}"
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
                f"{r['lockstep_guard']:<17} {r.get('platform', '-')}"
            )

    return 1 if any("error" in r for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
