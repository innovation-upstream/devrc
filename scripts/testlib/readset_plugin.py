"""Record the REPO PATHS each test file actually reads — measured, not inferred.

WHY THIS EXISTS, AND WHY IT IS NOT A REGEX
------------------------------------------
`claudedocs/handoff-ci-speedup.md` carries a 🔴 gotcha: a regex classifier for
`git ls-files` / `REPO_ROOT.rglob` called 32 of 139 files "repo-wide scanners",
and the number is NOT TRUSTWORTHY because it over-classifies. The named example
is `test_drift_check.py`, which the regex flagged on line 1440 — where
`git ls-files` appears inside a *fixture string*, a shell snippet the test feeds
to the script under test. Reading the source cannot tell those apart: the same
characters mean "scans the tree" in one line and "is test data" in the next.

Running the test can. This plugin installs a `sys.addaudithook` and records the
paths each test file OPENS, LISTS and SCANS, plus every subprocess it spawns and
the cwd it spawned it in. A `git ls-files` executed with cwd inside a `tmp_path`
fixture repo is visibly not a scan of this repo; one executed at REPO_ROOT
visibly is.

🔴 THE OUTPUT IS A READ SET, NOT A BOOLEAN. "repo-wide" turned out to be the
wrong shape for the question. `test_drift_check.py` genuinely must re-run when a
`.sh` under `scripts/` or a `.nix` under `nix/pkgs/` changes (it rglobs both) —
and genuinely need not when a doc in `claudedocs/` does. Collapsing that to one
bit is what made the earlier number both wrong and unfalsifiable. A read set is
also the input rank 3 (the path->target mapping) actually needs.

WHAT IT CANNOT SEE — state these with the result, never quietly
--------------------------------------------------------------
- **Reads inside a subprocess.** The audit hook is per-interpreter, so a child
  process's own `open` calls are invisible. We record the ARGV and CWD instead,
  and a consumer must treat a subprocess rooted at REPO_ROOT as reading
  everything beneath it. That is deliberately the pessimistic direction.
- **A test whose outcome depends on a file it never reads** (e.g. it asserts on
  a count someone else computed). No read-tracer can see that; only perturbation
  can, and perturbation has its own blind spot (an innocuous edit does not trip
  a scanner that only fails on violations). The two methods are complementary
  and neither is sufficient alone.
- **Reads by a C extension that bypasses the audit events.** Rare here, but it
  is why the runner ships a POSITIVE CONTROL: a file with a known, deliberate
  repo read must appear in the output, or the instrument is wired to nothing.

Attribution covers BOTH phases, because module-level reads are real: several
files in this corpus do `(REPO_ROOT / "nix" / "home.nix").read_text()` at import
time, which happens during COLLECTION and would be lost by a runtest-only hook.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Events worth paying for. The hook runs on EVERY audited operation in the
# interpreter, so the first thing it does is a frozenset membership test and an
# early return; anything more expensive here shows up as suite wall time.
_PATH_EVENTS = frozenset({"open", "os.listdir", "os.scandir", "os.stat"})
_EXEC_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec"})
_WANTED = _PATH_EVENTS | _EXEC_EVENTS

# Noise that is never a signal about the source tree.
_SKIP_PARTS = ("__pycache__", "/.git/", "/.pytest_cache/", "/node_modules/")

# file-under-test (repo-relative str) -> {"paths": set, "execs": set}
_RECORDS: dict[str, dict[str, set]] = {}
_current: list[str] = []          # a stack; [-1] is the file being attributed
_writing = False                  # re-entrancy guard for our own output write


def _rel(p: str) -> str | None:
    """Repo-relative path, or None if it is not inside this repo."""
    try:
        # No resolve() here: it stats the path, which re-enters the hook and is
        # the single most expensive thing this function could do. Trading exact
        # symlink resolution for a hook that does not quadratically re-enter.
        if not p.startswith(str(REPO_ROOT)):
            return None
        rel = p[len(str(REPO_ROOT)):].lstrip("/")
        return rel or "."
    except Exception:
        return None


def _bucket() -> dict[str, set] | None:
    if _writing or not _current:
        return None
    return _RECORDS.setdefault(_current[-1], {"paths": set(), "execs": set()})


def _hook(event: str, args) -> None:
    if event not in _WANTED:
        return
    b = _bucket()
    if b is None:
        return
    try:
        if event in _PATH_EVENTS:
            target = args[0]
            if isinstance(target, (bytes, int)):
                return                      # fd or bytes path: not useful here
            s = os.fspath(target) if hasattr(target, "__fspath__") else str(target)
            if any(part in s for part in _SKIP_PARTS):
                return
            rel = _rel(s)
            if rel is not None:
                b["paths"].add(rel)
        else:
            # (executable, args, cwd, env) for subprocess.Popen
            argv = args[1] if len(args) > 1 else args[0]
            cwd = args[2] if len(args) > 2 else None
            if isinstance(argv, (list, tuple)):
                argv_s = " ".join(str(a) for a in argv[:6])
            else:
                argv_s = str(argv)
            # An empty cwd means "inherited", which for this suite is REPO_ROOT;
            # a cwd outside the repo is the case that ACQUITS a `git ls-files`,
            # so it gets its own token rather than being blanked to look local.
            if cwd is None:
                cwd_s = "<inherited>"
            else:
                cwd_s = _rel(str(cwd))
                if cwd_s is None:
                    cwd_s = "<outside-repo>"
            b["execs"].add(f"{argv_s}\t@{cwd_s}")
    except Exception:
        # A tracer must never be able to fail the suite it is measuring.
        return


sys.addaudithook(_hook)


# ---------------------------------------------------------------- pytest hooks

def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def pytest_collectstart(collector):
    """Module IMPORT happens here, and module-level repo reads are real reads.

    🔴 KEYED ON `nodeid`, NOT on a repo-relative path. The first draft used
    `_rel(collector.path)`, which returns None for any test file outside
    REPO_ROOT — so nothing was pushed, `_current` stayed empty, and EVERY
    module-level read was silently dropped. The controls caught it: a
    deliberate import-time `flake.nix` read did not appear in the output. It
    also made the two phases disagree, since runtest attributes by nodeid.
    One key for both phases, or the halves cannot be merged.
    """
    nodeid = getattr(collector, "nodeid", "") or ""
    if nodeid.endswith(".py"):
        _current.append(nodeid)
    else:
        _current.append(_current[-1] if _current else "<pre-collection>")


def pytest_collectreport(report):
    if _current:
        _current.pop()


def pytest_runtest_protocol(item, nextitem):
    _current.append(_file_of(item.nodeid))
    return None  # advisory only; let the default protocol run


def pytest_runtest_logfinish(nodeid, location):
    if _current:
        _current.pop()


def pytest_sessionfinish(session, exitstatus):
    global _writing
    out = os.environ.get("DEVRC_READSET_OUT")
    if not out:
        return
    worker = os.environ.get("PYTEST_XDIST_WORKER", "master")
    _writing = True
    try:
        payload = {
            f: {"paths": sorted(v["paths"]), "execs": sorted(v["execs"])}
            for f, v in _RECORDS.items()
        }
        Path(f"{out}.{worker}.json").write_text(json.dumps(payload, indent=1))
    finally:
        _writing = False
