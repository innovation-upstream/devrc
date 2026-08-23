"""ONE definition of the directories a repo-wide filesystem walk must not read.

WHY THIS FILE EXISTS
--------------------
Four repo-wide scanners each carried their own hand-written skip set, and
`claude/RULES.md` -> "One rule, one place" predicted the outcome exactly: the
predicate was wrong at N-1 of the N sites, in the same direction. Measured
2026-08-20, before this module existed:

    scripts/testlib/public_ip_scan.py            9 entries, HAS .pytest_cache
    scripts/testlib/client_host_scan.py          9 entries, HAS .pytest_cache
    the shared-detector ledger in scripts/tests  7 entries, MISSING it
    scripts/tests/test_clawgate_predicate_...py  6 entries, MISSING it

The two shared copies had learned about `.pytest_cache`; the two open-coded
copies in `scripts/tests/` had not. So an ORDINARY `pytest` run -- one that
writes `.pytest_cache/v/cache/nodeids`, a JSON list of every collected node id
-- planted a file naming the shared /proc session detector 29 times inside the
tree that ledger walks, and the ledger went red on an artefact the developer
never wrote. That is the permanently-red-gate failure `claude/RULES.md` names: a
gate nobody can keep green trains everyone to click through it.

Not hypothetical and not latent: the operator's own `~/workspace/devrc` checkout
was RED on that test at the moment this module was written, from a
`.pytest_cache` last touched by a routine run.

(Deliberately NOT spelling that ledger's module name here. It hunts for its own
trigger token repo-wide, so naming it would make this file one of its findings
-- the same self-reference trap `test_clawgate_predicate_single_source.py` and
`shebang_scan.py` document.)

WHY THERE IS A BASE PLUS PER-SITE ADDITIONS, AND NOT ONE UNION
--------------------------------------------------------------
🔴 Unioning the four sets would have BLINDED TWO SECURITY GATES. This repo is
PUBLIC; `public_ip_scan` and `client_host_scan` exist to stop a real address or
a client hostname reaching it. The two `scripts/tests/` sets skip `.claude` and
`claudedocs` -- and `claudedocs/` is 98 COMMITTED files of handoff prose, which
is precisely the highest-yield place for a real IP or hostname to get written
down. A union would have deleted those 98 files from both gates' view while
every test stayed green. A skip entry is a blind spot, so it is granted per
site, with a reason, never inherited by default.

The inverse direction is a hazard too, which is why `GENERATED` is exactly the
set those two gates already had: consolidation must not silently NARROW them
either. Their effective sets are unchanged by this module, byte for byte, and
`scripts/tests/test_skip_dirs_ledger.py` pins all four two-way so a future edit
cannot widen one (blinding a gate) or narrow one (re-opening this bug) unseen.
"""
from __future__ import annotations

#: Machine-generated or foreign-repo directories. Nothing here is written by
#: hand, so nothing here is this repo's source, so no repo-wide scanner has a
#: reason to read it. Every entry has a reason, because every entry is a blind
#: spot:
#:
#:   .git            VCS internals; also most of the file count in the repo.
#:   .direnv         direnv's per-repo nix profile cache.
#:   result          the `nix build` output symlink (gitignored, at the root).
#:   node_modules    npm dependency trees -- vendored third-party source.
#:   __pycache__     CPython bytecode.
#:   .pytest_cache   MEASURED: `v/cache/nodeids` is a JSON list of every
#:                   collected test node id, so it names, verbatim, every module
#:                   and function in the repo. A content ledger reading it sees
#:                   its own trigger tokens reflected back. Written by any
#:                   ordinary `pytest` run and gitignored -- this is the entry
#:                   this module was created for.
#:   .mypy_cache     mypy's serialized ASTs; carries source string constants.
#:   .ruff_cache     ruff's diagnostic cache. MEASURED PRESENT in the operator's
#:                   checkout (`.ruff_cache/0.16.2/`) even though this repo has
#:                   no ruff config -- an agent ran it once. "We do not use that
#:                   tool" is not evidence the directory is absent.
#:   worktrees       agent worktrees live under `.claude/worktrees/<id>/` and are
#:                   FULL copies of this repo. Without this, a scan re-reports
#:                   every other agent's tree as if it were this one.
#:
#: 🔴 Matched against the path RELATIVE to the scan root, never the absolute
#: one. This checkout can itself sit under a directory named here (a worktree
#: does), and an absolute-parts test then walks ZERO files while reporting a
#: perfect green. Every consumer's positive control exists for that failure.
GENERATED = frozenset({
    ".git", ".direnv", "result", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "worktrees",
})

#: A Python virtualenv: `python -m venv .venv`, `uv venv`, `virtualenv venv`.
#:
#: 🔴 Granted ONLY to walkers that have no `git ls-files` tier, i.e. the two
#: `scripts/tests/` ledgers, which read whatever is on disk. MEASURED in the
#: operator's checkout: `.venv/` holds 476 files, 395 of them `.py`, and both
#: ledgers were parsing every one of them on every run. It contains no trigger
#: token TODAY (measured, zero hits) -- so this is not a fix for a live red, it
#: is the same class of bug one `pip install` away, and creating a virtualenv is
#: exactly the ordinary developer action that must not red a gate.
#:
#: Deliberately NOT granted to `public_ip_scan` / `client_host_scan`. Those two
#: prefer `git ls-files`, so a virtualenv is already outside their view on the
#: dev host and outside the flake source in the nix sandbox -- adding it there
#: would buy a security gate nothing and cost it a stated blind spot.
VIRTUALENVS = frozenset({".venv", "venv"})
