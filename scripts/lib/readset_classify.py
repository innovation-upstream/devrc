#!/usr/bin/env python3
"""Turn measured read sets into a per-test-file TRIGGER SET.

Input: the `*.json` shards written by `testlib/readset_plugin.py` (one per xdist
worker). Output: for each test file, the repo paths whose change could plausibly
change that file's outcome — plus the subset that must run on ANY change.

🔴 THE CLASSIFICATION IS DELIBERATELY PESSIMISTIC IN ONE DIRECTION. A tracer
sees reads, not causation, so "read it" is treated as "depends on it" even when
the test merely stat'd the path. The reverse error — calling a file scoped when
it is not — would silently skip a test that should have run, so every ambiguous
case resolves to ALWAYS-RUN. A mapping built from this must ALSO fail safe:
an unknown path runs everything.

Why a subprocess is treated as reading a whole subtree: the audit hook is
per-interpreter and cannot see a child's opens. A `git ls-files` at REPO_ROOT is
therefore scored as reading the entire tree, which is what it does.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 🔴 SUBCOMMANDS THAT READ A TREE — not "any command whose name is git".
# The first draft matched the bare token "git" and promptly flagged
# `git init -q /tmp/...` as a repo-wide scan: cwd was <inherited>, argv
# contained "git", done. That is the SAME over-classification the regex
# classifier made (`claudedocs/handoff-ci-speedup.md`), just one layer up —
# matching the tool instead of the operation. `init`, `config`, `commit` and
# friends are not reads of this tree.
_GIT_READERS = frozenset({"ls-files", "grep", "diff", "log", "show",
                          "rev-list", "status", "ls-tree", "blame"})
# Tools whose whole purpose is to walk a tree from cwd.
_DIRECT_SCANNERS = frozenset({"rg", "grep", "egrep", "fgrep", "find",
                              "ugrep", "ack"})
# Interpreters that run code this tracer cannot see into.
_OPAQUE_INTERPRETERS = frozenset({"bash", "sh", "zsh", "python", "python3",
                                  "perl", "env"})
# git options that consume the NEXT argv token; their value is not a subcommand.
_GIT_OPTS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree",
                                  "--namespace", "--exec-path"})

# A cwd token meaning "this repo" — the two that make a scan repo-wide.
_REPO_CWD = (".", "<inherited>")


def _load(shards: list[Path]) -> dict[str, dict[str, set]]:
    merged: dict[str, dict[str, set]] = defaultdict(
        lambda: {"paths": set(), "execs": set()})
    for shard in shards:
        data = json.loads(shard.read_text())
        for f, rec in data.items():
            merged[f]["paths"].update(rec.get("paths", []))
            merged[f]["execs"].update(rec.get("execs", []))
    return merged


def _exec_verdict(execs: set[str]) -> tuple[list[str], list[str]]:
    """Split execs into (proven repo scans, OPAQUE ones we cannot see into).

    🔴 ONLY THE COMMAND AND ITS SUBCOMMAND ARE INSPECTED. Scanning every token
    of the argv was wrong twice in one sitting: it flagged a stub binary called
    `.../bw --nointeraction status` (because "status" is a git read verb), and
    it flagged `bash -c '<300-line script>'` because a read verb appeared in the
    SCRIPT TEXT. That is the identical failure the regex classifier made and
    that this whole exercise exists to remove — matching characters rather than
    the operation being performed.

    🔴 OPAQUE IS ITS OWN BUCKET, NOT A QUIET "ALWAYS-RUN". `bash -c`, `sh -c`
    and friends run code this tracer cannot see; at a repo-root cwd their read
    set is genuinely UNKNOWN. Folding unknown into always-run would inflate the
    always-run count with cases nobody measured and make the number look
    authoritative; folding it into scoped would be unsafe. It is reported
    separately so a consumer can fail safe on it AND a reader can see how much
    of the corpus is still unmeasured.
    """
    scans: list[str] = []
    opaque: list[str] = []
    for e in sorted(execs):
        argv, _, cwd = e.partition("\t@")
        toks = argv.split()
        if not toks:
            continue

        # 🔴 THERE WAS A `-C <path>` BRANCH HERE AND IT WAS DEAD CODE — deleted
        # after a mutation sweep showed it could never be the sole acquitter.
        # It only rewrote the cwd when the `-C` path was absolute AND outside
        # the repo, which is exactly the case the OPERAND rule below already
        # rejects; disabling the branch changed no verdict. The remaining case,
        # `git -C <relative-subdir> ls-files`, genuinely IS a scan of this tree
        # and must keep counting as one. Re-adding a `-C` rule needs a case
        # that this function would otherwise get wrong — there wasn't one.
        if cwd not in _REPO_CWD:
            continue                    # scanning someone else's tree

        head = toks[0].rsplit("/", 1)[-1]

        # An opaque interpreter: we cannot see what it reads.
        if head in _OPAQUE_INTERPRETERS and any(t in ("-c",) for t in toks[1:3]):
            opaque.append(" ".join(toks[:3]))
            continue

        # An absolute OPERAND outside the repo names the real subject. Skip
        # toks[0] (the executable's own path is not what it reads) — but a stub
        # binary living in /tmp is itself evidence the work is not in this repo.
        operands = [t for t in toks[1:] if not t.startswith("-")]
        if any(t.startswith("/") and not t.startswith(str(REPO_ROOT))
               for t in operands):
            continue
        if toks[0].startswith("/") and not toks[0].startswith(str(REPO_ROOT)):
            continue                    # a stub/fixture binary outside the repo

        if head == "git":
            # 🔴 SKIP THE VALUE OF ANY OPTION THAT TAKES ONE. Taking the first
            # non-flag token as the subcommand reads `git -C scripts ls-files`
            # as subcommand "scripts" and acquits a REAL scan of this tree —
            # under-classification, the direction that silently skips a test
            # that should have run. Caught by
            # test_dash_C_into_a_repo_SUBDIR_is_still_a_scan_of_this_tree.
            sub, skip_next = "", False
            for t in toks[1:]:
                if skip_next:
                    skip_next = False
                    continue
                if t in _GIT_OPTS_WITH_VALUE:
                    skip_next = True
                    continue
                if t.startswith("-"):
                    continue
                sub = t
                break
            if sub in _GIT_READERS:
                scans.append(argv)
        elif head in _DIRECT_SCANNERS:
            scans.append(argv)
    return scans, opaque


def _top(p: str, depth: int = 2) -> str:
    parts = [x for x in p.split("/") if x]
    return "/".join(parts[:depth]) if parts else "."


def classify(merged: dict[str, dict[str, set]], self_scope: bool = True) -> dict:
    out = {}
    for f, rec in merged.items():
        if f.startswith("<"):
            continue                    # bookkeeping buckets, not a test file
        paths = set(rec["paths"])
        # A test reading its own file/dir is not a dependency worth recording —
        # it is how pytest imports it. Everything else is.
        own = str(Path(f).parent)
        external = {p for p in paths
                    if not (self_scope and (p == f or p.startswith(own + "/")))}
        scanners, opaque = _exec_verdict(rec["execs"])
        reads_root = "." in paths
        always = bool(scanners) or reads_root
        out[f] = {
            "always_run": always,
            "opaque": bool(opaque) and not always,
            "why_always": (["reads repo root"] if reads_root else []) + scanners,
            "why_opaque": opaque[:3],
            "triggers": sorted({_top(p) for p in external}),
            "n_paths": len(external),
            "sample_paths": sorted(external)[:12],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("shards", nargs="+", type=Path)
    ap.add_argument("--json", type=Path, help="write full result here")
    args = ap.parse_args()

    merged = _load(args.shards)
    result = classify(merged)

    always = sorted(f for f, r in result.items() if r["always_run"])
    opaque = sorted(f for f, r in result.items() if r["opaque"])
    scoped = sorted(f for f, r in result.items()
                    if not r["always_run"] and not r["opaque"])

    print(f"measured test files : {len(result)}")
    print(f"  ALWAYS-RUN        : {len(always)}   (proven to read the tree)")
    print(f"  OPAQUE            : {len(opaque)}   (spawn an interpreter at repo "
          f"root — read set UNKNOWN, not measured; a consumer must fail safe "
          f"and run them)")
    print(f"  scoped            : {len(scoped)}   (proven bounded)")
    if not result:
        print("\n🔴 ZERO FILES MEASURED — the tracer produced nothing. That is an "
              "instrument failure, not a clean result. Do not read the zeros above "
              "as 'no file is repo-wide'.", file=sys.stderr)
        return 3

    print("\n=== ALWAYS-RUN (any change) ===")
    for f in always:
        why = "; ".join(result[f]["why_always"][:2])
        print(f"  {f}\n      why: {why}")

    print("\n=== SCOPED — top trigger prefixes ===")
    for f in scoped:
        t = result[f]["triggers"]
        print(f"  {f}\n      triggers({len(t)}): {', '.join(t[:8])}")

    if args.json:
        args.json.write_text(json.dumps(result, indent=1, sort_keys=True))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
