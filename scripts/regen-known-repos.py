#!/usr/bin/env python3
"""Regenerate the mention-resolution repo mapping — OUTSIDE the repository.

    scripts/regen-known-repos.py [--print] [--path <file>]

🔴 THE OUTPUT IS NOT TRACKED, AND THAT IS THE POINT. `gh api user/repos` returns
every repo the token can see, PRIVATE ONES INCLUDED. An earlier version of this
generator wrote its result to `scripts/collector/known_repos.py` and it was
committed: 232 private repos, 217 of them named nowhere else in the tree, went
into a repository that is PUBLIC. The names alone disclose unreleased products,
internal architecture and client relationships, and every content gate in this
repo was structurally blind to it — they scan JSON/JSONL/HTML/TXT and hostnames,
so a `.py` dict of repo names matched none of them.

So the mapping is written to `~/.config/mention-open/known_repos.json` (mode
0600), which is per-host, outside every checkout, and cannot be `git add`ed by
accident. `scripts/mention-open.py` reads it if it is there and works without it.

WHAT IS FILTERED OUT, AND WHY EACH ONE IS A WRONG ANSWER RATHER THAN A MISSING ONE
  * `has_issues == false` — `repo#N` builds an issues URL, and a repo with issues
    disabled 404s for EVERY N. Measured on the first version: 44 of 383 mapped
    repos, including `civitai/ComfyUI`, a fork whose issues are disabled and
    whose `#100` was the example used to justify the mapping in the first place.
  * a bare name owned by TWO owners — last-write-wins silently picked one.
    Measured: 7 collisions, and `bitdex` resolved to a third party's fork rather
    than the client's repo. An ambiguous name resolves to NOTHING; the operator
    writes `owner/repo#N`, exactly as the module's no-guessing rule requires.
  * linked worktrees (`.git` is a FILE, not a directory) — they share the base
    clone's remote, so they add no owner, and their transient names churned the
    output on every run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_PATH = Path(
    os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
) / "mention-open" / "known_repos.json"

WORKSPACE = Path(os.environ.get("DEVRC_WORKSPACE", Path.home() / "workspace"))

# A `gh` leg that fails must not be mistaken for "the operator has few repos".
# The generator REFUSES rather than writing a 90%-smaller file and exiting 0 —
# the previous version's silent `except Exception: continue` is the same lossy
# class this whole change exists to fix.
MIN_API_REPOS = 25

_SSH_REMOTE = re.compile(r"^(?:ssh://)?git@[^:/]+[:/](?P<path>.+?)(?:\.git)?/?$")
_HTTP_REMOTE = re.compile(r"^https?://[^/]+/(?P<path>.+?)(?:\.git)?/?$")


def parse_owner_repo(remote_url: str) -> str:
    """`owner/repo` from a git remote URL, else "". Mirrors mention-open.py."""
    url = (remote_url or "").strip()
    if not url:
        return ""
    m = _SSH_REMOTE.match(url) or _HTTP_REMOTE.match(url)
    if not m:
        return ""
    parts = [p for p in m.group("path").split("/") if p]
    return f"{parts[0]}/{parts[1]}" if len(parts) == 2 else ""


def build_mapping(api_repos: list[dict], local_repos: dict[str, str]) -> dict[str, str]:
    """{name: "owner/repo"} from API rows + measured local checkouts.

    `api_repos` rows are `{"full_name": ..., "has_issues": ...}` as the search
    endpoint returns them. `local_repos` is {directory name: "owner/repo"},
    already measured from git remotes.

    A name that resolves to more than one owner is DROPPED rather than
    arbitrated — see the module docstring. A local checkout is exempt: it is a
    measurement of this disk, so it wins over an API row and settles the
    ambiguity by itself.
    """
    owners: dict[str, set[str]] = {}
    for row in api_repos:
        full = (row.get("full_name") or "").strip()
        if not row.get("has_issues") or "/" not in full:
            continue
        owners.setdefault(full.rsplit("/", 1)[-1].lower(), set()).add(full)

    out: dict[str, str] = {}
    for row in api_repos:
        full = (row.get("full_name") or "").strip()
        if not row.get("has_issues") or "/" not in full:
            continue
        name = full.rsplit("/", 1)[-1]
        if len(owners[name.lower()]) > 1:
            continue
        # BOTH spellings: mention_scan._resolve_repo does an EXACT dict lookup,
        # and GitHub repo names are case-insensitive, so a canonical-case key
        # alone cannot match `comfyui#12`.
        out[name] = full
        out[name.lower()] = full

    # 🔴 THE OVERLAY OBEYS THE SAME TWO FILTERS. Writing local entries in
    # unconditionally re-opened both holes this function exists to close: a
    # checkout of an issues-disabled repo went back in (measured: 1 such key in
    # the mapping generated today), and two checkouts whose bare names collide
    # went back to last-write-wins. A checkout is authoritative about its own
    # OWNER — it is not evidence that the repo accepts issues, and it is not a
    # tiebreak between two different repos with one name.
    issues_off = {(row.get("full_name") or "").strip()
                  for row in api_repos if not row.get("has_issues")}
    local_owners: dict[str, set[str]] = {}
    for name, full in local_repos.items():
        if "/" not in full or full in issues_off:
            continue
        for key in (name.lower(), full.rsplit("/", 1)[-1].lower()):
            local_owners.setdefault(key, set()).add(full)

    for name, full in local_repos.items():
        if "/" not in full or full in issues_off:
            continue
        bare = full.rsplit("/", 1)[-1]
        for spelling in (name, bare):
            existing = out.get(spelling)
            ambiguous = (
                # Two checkouts, one name, different repos — the same
                # ambiguity the API path drops rather than arbitrates.
                len(local_owners[spelling.lower()]) > 1
                # …or this checkout's name collides with a DIFFERENT repo the
                # API already mapped. Overwriting there is last-write-wins
                # across sources: `~/workspace/vendored-widget` cloning
                # `rival/widget` would silently displace `ourorg/widget`.
                # Agreeing on the same repo is not a collision — that is the
                # checkout confirming the owner, which is what makes it source 3.
                or (existing is not None and existing != full)
            )
            if ambiguous:
                out.pop(spelling, None)
                out.pop(spelling.lower(), None)
                continue
            out[spelling] = full
            out[spelling.lower()] = full
    return out


def read_api_repos() -> list[dict]:
    """Rows from `gh api user/repos`. Raises RuntimeError — never returns a
    short list quietly, because a short list is indistinguishable from a fine
    one once it has been written."""
    try:
        r = subprocess.run(
            ["gh", "api", "user/repos", "--paginate", "--jq",
             ".[] | {full_name, has_issues} | tostring"],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"gh could not be run: {exc}") from exc
    if r.returncode != 0:
        raise RuntimeError(f"gh exited {r.returncode}: {r.stderr.strip()[:200]}")
    rows = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if len(rows) < MIN_API_REPOS:
        raise RuntimeError(
            f"gh returned only {len(rows)} repo(s), below the floor of "
            f"{MIN_API_REPOS} — refusing to write a mapping that is probably "
            f"truncated. Check `gh auth status`.")
    return rows


def read_local_repos(workspace: Path = WORKSPACE) -> dict[str, str]:
    """{directory name: "owner/repo"} for real clones under `workspace`."""
    out: dict[str, str] = {}
    try:
        entries = sorted(p for p in workspace.iterdir() if p.is_dir())
    except OSError:
        return out
    for entry in entries:
        dotgit = entry / ".git"
        # A linked worktree's .git is a FILE holding 'gitdir: …' — skip it.
        if not dotgit.is_dir():
            continue
        try:
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=str(entry), capture_output=True,
                               text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        full = parse_owner_repo(r.stdout if r.returncode == 0 else "")
        if full:
            out[entry.name] = full
    return out


def write_mapping(mapping: dict[str, str], path: Path) -> None:
    """Write 0600, parent 0700 — it names private repositories."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(mapping, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--path", type=Path, default=DEFAULT_PATH)
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="write nothing; report what WOULD be written")
    args = ap.parse_args(argv)

    try:
        api = read_api_repos()
    except RuntimeError as exc:
        print(f"regen-known-repos: {exc}", file=sys.stderr)
        return 3

    local = read_local_repos()
    mapping = build_mapping(api, local)
    dropped = len([r for r in api if not r.get("has_issues")])

    if args.print_only:
        print(f"{len(mapping)} key(s) from {len(api)} repo(s) "
              f"({dropped} with issues disabled, skipped); "
              f"{len(local)} local checkout(s). Would write {args.path}")
        return 0

    write_mapping(mapping, args.path)
    print(f"wrote {args.path} — {len(mapping)} key(s) from {len(api)} repo(s) "
          f"({dropped} with issues disabled, skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
