#!/usr/bin/env python3
"""Fast-forward the shared base clones — and report, per clone, WHY not.

    scripts/sync-clones.py [REPO ...]

Thin CLI over `scripts/lib/shared_clone_sync.py`; the classification, the status
ledger and every guard live there, and this file only chooses repos, renders and
exits. Read that module's docstring before changing behaviour here.

WHY YOU WANT THIS. The base clones on this fleet are write-only — all work
happens in throwaway worktrees and PRs — so their own branches never advance
while their working trees keep serving CLAUDE.md and .claude/skills/** into
agent context. A 14-day audit of 443 sessions measured them at 606, 238, 213,
161 and 83 commits behind, and found sessions re-deriving that fact by hand up
to 78 times in a single transcript.

🔴 IT NEVER RESOLVES DIRT. No stash (`refs/stash` is repo-GLOBAL here and a
concurrent agent can pop yours), no reset, no discard. A clone whose dirt
overlaps the incoming commits is REFUSED with the overlapping paths named, and a
human decides. There is no flag to override that.

EXIT CODES — the reason this is not `git merge --ff-only || true` in a cron.
Distinct per outcome so a caller can branch WITHOUT parsing text. Over several
repos the exit code is the WORST repo's: a refusal is never averaged away by
nine successes.

    0   every clone ended `synced` or `current`
    1   an unexpected internal error
    2   usage, or NOTHING TO CHECK — a run that examined zero repositories
        exits 2, never 0, because a checker wired to nothing must not report
        success (`claude/RULES.md`: the positive control)
    3   refused-dirty        — dirt overlaps the incoming commits
    4   refused-diverged     — local commits upstream does not have
    5   refused-no-upstream  — the branch tracks nothing; no target to guess
    6   refused-detached     — HEAD is not on a branch
    7   refused-not-a-repo   — the path is not a git repository
    8   refused-fetch-failed — could not refresh the remote-tracking refs
    9   refused-ff-failed    — the pre-check cleared it and git refused anyway

🔴 `synced` AND `current` BOTH EXIT 0 AND ARE NOT THE SAME ANSWER. "I moved you
606 commits" and "there was nothing to do" are precisely the two readings a
stale-clone report exists to separate. They are distinguishable in every output
mode: different status, different sentinel phrase, and a `moved` count that is
zero for one and non-zero for the other. A caller that only reads the exit code
is asking a coarser question than this tool answers — use `--json`.

REPO SELECTION. With arguments, exactly those repos. With none, it DISCOVERS
primary clones (`.git` a directory, so linked worktrees are skipped) under
`$DEVRC_CLONE_ROOTS` (colon-separated) or `~/workspace`, and PRINTS the roots it
scanned and every clone it chose before touching anything — a default that is
not shown is a hardcode wearing a costume. `DEVRC_CLONE_ROOTS` set-but-EMPTY is
an error, not a fall-back to the whole workspace.

Options:
    --no-fetch          measure against the remote-tracking refs as they are.
                        Offline-safe; the numbers are then a FLOOR, since a
                        clone that has never fetched can be far more stale.
    --fetch-timeout N   seconds to allow the network leg (default 120).
    --json              one JSON object: {"repos": [...], "exit_code": N}.
    --quiet             suppress the per-repo lines; print only the summary.
    --roots A:B         override the discovery roots for one run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import shared_clone_sync as scs  # noqa: E402

EXIT_USAGE = 2


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="sync-clones.py",
        description="Fast-forward shared base clones; report why not, per clone.",
    )
    p.add_argument("repos", nargs="*", help="repo paths (default: discovered clones)")
    p.add_argument("--no-fetch", action="store_true", help="do not touch the network")
    p.add_argument("--fetch-timeout", type=float, default=scs.FETCH_TIMEOUT_SECS)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--quiet", action="store_true", help="summary only")
    p.add_argument("--roots", default=None, help="colon-separated discovery roots")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    roots = args.roots.split(":") if args.roots else None

    try:
        run = scs.sync_many(
            args.repos,
            fetch=not args.no_fetch,
            fetch_timeout=args.fetch_timeout,
            roots=roots,
        )
    except scs.EmptyRootsError as exc:
        print(f"sync-clones: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        payload = {
            "repos": [r.as_dict() for r in run.results],
            "exit_code": run.exit_code if run.results else EXIT_USAGE,
            "defaulted_roots": list(run.defaulted_from[0]) if run.defaulted_from else None,
            "defaulted_repos": list(run.defaulted_from[1]) if run.defaulted_from else None,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        if run.defaulted_from is not None:
            scanned, found = run.defaulted_from
            print(
                f"sync-clones: no repo arguments — scanned {', '.join(scanned)} "
                f"and defaulted to {len(found)} primary clone(s):"
            )
            for repo in found:
                print(f"  {repo}")
        if not args.quiet:
            for res in run.results:
                print(res.message())
                for path in res.advisory_paths:
                    print(f"    advisory (does not block): {path}")
                if res.advisory_count > len(res.advisory_paths):
                    extra = res.advisory_count - len(res.advisory_paths)
                    print(f"    advisory (does not block): +{extra} more")

    if not run.results:
        # 🔴 A run that examined nothing is NOT a pass. Zero repos is the same
        # observable as "every repo was fine", and the two must not share an
        # exit code — that is the difference between a checker and a checker
        # wired to nothing.
        print(
            "sync-clones: examined 0 repositories — nothing was checked",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if not args.json:
        counts: dict[str, int] = {}
        for res in run.results:
            counts[res.status] = counts.get(res.status, 0) + 1
        tally = " ".join(f"{s}={counts[s]}" for s in scs.ALL_STATUSES if s in counts)
        print(f"sync-clones: {len(run.results)} repo(s) — {tally} (exit={run.exit_code})")

    return run.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
