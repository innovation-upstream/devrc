#!/usr/bin/env python3
"""Find guard branches with zero corpus instances.

    python3 scripts/dead-guard-scan.py --repo <path>   # 0 clean, 1 unresolved flag
    python3 scripts/dead-guard-scan.py --self-test     # positive + negative controls
    python3 scripts/dead-guard-scan.py --repo <path> --census <out.tsv>

A guard branch that never executes -- against the real corpus AND the guard's
own battery -- is EVIDENCE of one of three things, two of which are the defect
`claude/RULES.md` names under "A guard's DESCRIPTION claims COVERAGE": dead
recognition code, a reporting branch with no positive control, or (not a
defect) a branch driven through a subprocess this tracer cannot see. For the
first two the remedy is to DELETE it and state the limit, not to harden it. The
analysis and its full limits live in `scripts/lib/dead_guard.py`. Resolve a
flag by deleting the branch or writing one line at the site:
`# pragma: no cover - <reason>`.

🔴 THIS IS ADVISORY. It gates nothing until deliberately wired into CI, and its
own precision on real code is a human call -- flags are evidence for that
reading, not a verdict to auto-apply.

🔴 WHAT A CLEAN EXIT DOES NOT MEAN. Only the `instrument` rows of
`scripts/data/dead-guard-registry.tsv` are measured. Everything else -- bash,
TypeScript, Go -- is listed there as out-of-instrument WITH ITS REASON. Exit 0
means "no unresolved flag among the guards this tool can see", never "the
repo's guards are alive".

🔴 AND THE DENOMINATOR IS NOT DERIVABLE FROM THIS TOOL. An earlier revision of
this docstring claimed "the census prints that ratio on every run so the number
is not quotable without its denominator". It did not: what is printed is a
count of instrumented FILES and a count of registry ROWS, neither of which is a
guard total. Nothing here enumerates every guard in a repo, so any "N of M"
figure is a human's survey, not a measurement -- do not quote one as if this
tool produced it. That false claim shipped in this file and in the PR body, and
it is the same over-claiming-guard defect the tool exists to find.
"""

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))
import dead_guard as dg  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "scripts" / "data" / "dead-guard-registry.tsv"
PLUGIN_DIR = REPO_ROOT / "scripts" / "testlib"

# Exit codes, so a caller can tell "found something" from "could not look".
EXIT_CLEAN, EXIT_FLAGS, EXIT_UNDECIDABLE = 0, 1, 2


def repo_slug(repo):
    """`owner/name` from origin. The clone DIRECTORY is not usable as a key --
    `datapacket-talos` is civitai/talos-infra and `homelab-talos` is
    ZacxDev/homelab-infra."""
    try:
        url = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else ""


def load_registry(path=REGISTRY):
    rows = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            raise SystemExit(f"registry: malformed row (want 4 tab-separated "
                             f"fields, got {len(parts)}): {line!r}")
        rows.append(dict(zip(("slug", "lang", "status", "selector"), parts)))
    return rows


def guard_files(repo, rows, slug):
    """Absolute paths of the `instrument` guards present in this clone."""
    out = []
    for r in rows:
        if r["slug"] != slug or r["status"] != "instrument":
            continue
        for p in sorted(pathlib.Path(repo).glob(r["selector"])):
            if p.is_file() and p.suffix == ".py" and p not in out:
                out.append(p)
    return out


_WALK_SKIP = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache",
              ".pytest_cache", ".claude"}


def test_dirs(repo):
    """Every directory holding a `test_*.py` -- the surface a registry must
    have an opinion about.

    Prefers `git ls-files` (tracked files only, so build output and stray
    scratch files cannot inflate the ledger). Falls back to a filesystem walk
    when git returns nothing, so this works in a plain copy of a tree -- which
    is how the mutation battery runs, and is also just a repo that has not been
    initialised. A hard git dependency here would make the fallback path exit
    with an empty ledger, i.e. a silent "everything is registered".
    """
    repo = pathlib.Path(repo)
    out = subprocess.run(["git", "-C", str(repo), "ls-files"],
                         capture_output=True, text=True, timeout=120).stdout
    files = [f for f in out.splitlines()
             if re.search(r"(^|/)test_[^/]*\.py$", f) and "/" in f]
    if not files:
        files = [str(p.relative_to(repo)) for p in repo.rglob("test_*.py")
                 if p.is_file()
                 and not _WALK_SKIP & set(p.relative_to(repo).parts)]
        files = [f for f in files if "/" in f]
    return sorted({f.rsplit("/", 1)[0] for f in files})


def unregistered_test_dirs(repo, rows, slug):
    """Test directories no registry row mentions, in EITHER status.

    🔴 THIS IS THE REGISTRY'S OWN GUARD, AND IT EXISTS BECAUSE THE FIRST ONE WAS
    NARROWER THAN ITS DOCSTRING. `test_registry_parses_and_every_repo_declares_
    what_is_NOT_measured` claims it would catch "a repo with only `instrument`
    rows"; its body only asserts that at least one out-of-instrument row exists.
    It therefore could not see a guard surface present in NEITHER status -- and
    12 python guard modules under `scripts/claude-hooks/`, including the
    PreToolUse deny-guards `guard_core.py` and `bash-guard.py`, were exactly
    that: absent, and so reading as measured and clean.

    Mechanical, not heuristic: it does not try to decide what a "guard" is. It
    requires a DECISION to have been recorded for every directory that holds
    tests -- which is the homelab-infra `ci-manifest.txt` pattern.

    🔴 FORWARD DIRECTION ONLY, and the docs used to claim otherwise. A NEW test
    directory with no row fails, so the surface cannot silently GROW. A row
    naming a directory that no longer exists does NOT fail: the out-of-
    instrument column is prose, so there is no reliable way to tell a stale
    path from an explanatory sentence. Saying "bidirectional" here was the
    description-wider-than-implementation defect recurring inside its own fix.
    (`instrument` rows ARE checked in the other direction -- a selector that
    matches no file makes the whole scan UNDECIDABLE.)

    🔴 MATCHED ON PATH SEGMENTS, NOT SUBSTRINGS. A plain `d in text` test
    accepted `scripts/mail`, `scripts/collector`, `scripts/dl-router/test` and
    even `"s"` as "registered", because each is a substring of some row -- so a
    future directory that happens to be a path PREFIX of a registered one would
    be silently accepted, which is the silent "everything is registered" this
    function exists to prevent.
    """
    mine = [r for r in rows if r["slug"] == slug]
    named = set()
    for r in mine:
        for tok in re.split(r"[,\s]+", r["selector"]):
            tok = tok.strip().rstrip(",;")
            if not tok or tok.startswith("-"):
                continue
            named.add(tok)
            # A selector naming a FILE or glob (`.../test_*.py`) also registers
            # the directory holding it -- that is the directory the ledger is
            # about. A selector naming a DIRECTORY registers only itself: it
            # must NOT register its parent, or listing
            # `scripts/collector/tests` would silently accept a later
            # `scripts/collector/`, which is a different directory needing its
            # own decision.
            # 🔴 ...AND ONLY FOR `instrument` ROWS. The out-of-instrument
            # column is PROSE, and prose is full of dotted words: the bash row
            # naming `scripts/drift-check.sh` registered the bare directory
            # `scripts` in three repos, so a future `scripts/test_x.py` would
            # have been silently accepted -- the exact "everything is
            # registered" this function exists to prevent, re-admitted through
            # the sentence rather than the path.
            if r["status"] != "instrument":
                continue
            last = tok.rsplit("/", 1)[-1]
            if "/" in tok and ("." in last or "*" in last or "?" in last):
                named.add(tok.rsplit("/", 1)[0])
    # EXACT membership only. An earlier attempt also accepted a directory when
    # some registered path sat BELOW it, which re-admitted the permissive
    # failure: registering `scripts/collector/tests` made a later
    # `scripts/collector/` -- a different directory, needing its own decision --
    # read as already registered.
    return [d for d in test_dirs(repo) if d not in named]


def run_traced(repo, targets, python=None, extra_args=()):
    """Trace the guards, ONE PYTEST INVOCATION PER TEST DIRECTORY.

    🔴 `conftest.py` IS NOT NAMESPACED. This repo has several `conftest.py`
    files and no `__init__.py`, so an importer binds to whichever lands in
    `sys.modules` first and collecting two test directories in ONE pytest run
    fails with an ImportError naming the wrong file. (`scripts/browser-bridge/
    tests/cli_budget.py` documents the same trap; devrc's own runner uses one
    target per invocation for this reason.) Measured here: adding
    `scripts/claude-hooks/tests/` to the registry made `test_bash_guard.py`
    collect zero tests, so NOT ONE of its lines was traced -- which, before the
    zero-lines guard existed, would have been published as "every branch in the
    PreToolUse deny-guard is dead".

    Returns (trace, (rc, nfail), tail), traces merged across groups. `trace` is
    None when a group produced no trace file at all -- a missing file must not
    read as 'nothing executed'.

    🔴 THE INTERPRETER IS PART OF THE MEASUREMENT, AND IT LEAKS FROM cwd.
    `sys.executable` is whatever python is running THIS script, which under
    direnv is the venv of whatever directory the operator happens to stand in
    -- during authoring that was a different repo's `.venv` entirely. A
    different interpreter takes different branches (version gates, optional
    imports falling into `except ImportError`), so a trace carries the
    interpreter's identity or it is not reproducible. Hence `--python`, and
    hence the interpreter is printed on every run and written into the census.
    """
    python = python or sys.executable
    tests = [t for t in targets if t.name.startswith("test_")]
    groups = {}
    for t in tests:
        groups.setdefault(t.parent, []).append(t)
    if not groups:
        # 🔴 NEVER INVOKE pytest WITH NO PATH ARGUMENTS. It would collect the
        # TARGET REPO'S ENTIRE SUITE from cwd -- running arbitrary tests, with
        # their side effects, in a repo we were asked only to read. With no
        # registered test file there is nothing to drive the guards with, so
        # the honest result is an empty trace: every library module then has
        # zero traced lines and is reported UNDECIDABLE, which is true.
        return ({"executed": {}, "clobbered": False}, (0, 0),
                "no registered test file to drive the guards with")

    merged, code, nfail, tails = {}, 0, 0, []
    clobbered = False
    for parent, files in sorted(groups.items(), key=lambda kv: str(kv[0])):
        with tempfile.TemporaryDirectory(prefix="dgs-") as td:
            out = pathlib.Path(td) / "executed.json"
            env = dict(os.environ)
            # EVERY target is traced in EVERY group: a library module is
            # exercised by whichever suite happens to import it.
            env["DGS_TARGETS"] = os.pathsep.join(str(t) for t in targets)
            env["DGS_OUT"] = str(out)
            env["PYTHONPATH"] = os.pathsep.join(
                [str(PLUGIN_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
            env.pop("PYTEST_ADDOPTS", None)
            env["PYTHONDONTWRITEBYTECODE"] = "1"   # a "read-only" scan must not
            # litter the target repo with __pycache__ -- and a stale .pyc is how
            # a same-length edit gets scored without ever executing.
            cmd = [python, "-m", "pytest", "-p", "dead_guard_plugin",
                   "-q", "--no-header", "--continue-on-collection-errors",
                   "-p", "no:cacheprovider",
                   *[str(t) for t in files], *extra_args]
            try:
                proc = subprocess.run(cmd, cwd=str(repo), env=env,
                                      capture_output=True, text=True, timeout=3600)
            except subprocess.TimeoutExpired as e:
                # NOT a clean run and NOT a set of findings: we did not measure.
                return None, (-1, 0), f"traced run timed out after 3600s: {e}"
            tails.append((proc.stdout or "")[-2000:] + (proc.stderr or "")[-1000:])
            # Count FAILED lines rather than reading the exit code: a red run
            # leaves branches unexecuted for a reason that is NOT deadness, so
            # the caller must say how much of the census rests on it.
            nfail += len(re.findall(r"^FAILED ", proc.stdout or "", re.M))
            code = code or proc.returncode
            if not out.exists():
                return None, (proc.returncode, nfail), "\n".join(tails)
            part = json.loads(out.read_text(encoding="utf-8"))
            clobbered = clobbered or bool(part.get("clobbered"))
            for path, lines in part.get("executed", {}).items():
                merged.setdefault(path, set()).update(lines)

    return ({"executed": {k: sorted(v) for k, v in merged.items()},
             "clobbered": clobbered},
            (code, nfail), "\n".join(tails))


def interpreter_id(python, redact=False):
    """`<path> (Python X.Y.Z)` -- recorded so a trace carries its own scope.

    🔴 `redact=True` for anything COMMITTED. The absolute path is the operator's
    home directory, and under direnv it is routinely another repo's `.venv`
    (see `run_traced`) -- committing it publishes a local filesystem layout and
    pins the artifact to one machine. The VERSION is the part that changes
    which branches run; the path is a diagnostic for the person at the
    terminal, so it is printed and not written.
    """
    try:
        v = subprocess.run([python, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
                           capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        v = "?"
    if redact:
        return f"Python {v or '?'}"
    return f"{python} (Python {v or '?'})"


def scan(repo, census_path=None, verbose=True, python=None, registry=None):
    repo = pathlib.Path(repo).resolve()
    python = python or sys.executable
    rows = load_registry(registry or REGISTRY)
    slug = repo_slug(repo)
    if not slug:
        print(f"could not read origin for {repo} -- cannot key the registry",
              file=sys.stderr)
        return EXIT_UNDECIDABLE
    known = {r["slug"] for r in rows}
    if slug not in known:
        print(f"{slug} is not in {REGISTRY.name}. Add rows for it -- including "
              f"out-of-instrument rows for the languages this tool cannot see -- "
              f"rather than reading this silence as coverage.", file=sys.stderr)
        return EXIT_UNDECIDABLE

    targets = guard_files(repo, rows, slug)
    oo = [r for r in rows if r["slug"] == slug and r["status"] == "out-of-instrument"]
    inst = [r for r in rows if r["slug"] == slug and r["status"] == "instrument"]

    # 🔴 AN `instrument` SELECTOR THAT MATCHES NOTHING IS A TYPO, NOT A CLEAN
    # REPO. Without this, a one-character slip in a selector silently reduces
    # the report to nothing and exits 0 -- shaped identically to the legitimate
    # "this repo has no Python guards" case, which is the single most damaging
    # confusion this tool can produce.
    empty = [r["selector"] for r in inst
             if not [p for p in pathlib.Path(repo).glob(r["selector"])
                     if p.is_file() and p.suffix == ".py"]]
    if empty:
        print(f"{slug}: {len(empty)} `instrument` selector(s) matched NO python "
              f"file in this clone -- a typo, or the guards moved. That is "
              f"UNDECIDABLE, not clean:\n  " + "\n  ".join(empty),
              file=sys.stderr)
        return EXIT_UNDECIDABLE

    if not targets:
        # 🔴 STILL WRITE THE CENSUS. A repo with nothing instrumentable is
        # exactly the one whose absence from the artifact would read as "swept
        # and clean" -- talos-infra and civitai are both this case, and they
        # hold ~170 of the ~270 guards. Recording their out-of-instrument rows
        # is the only thing that keeps the census honest about its own reach.
        print(f"{slug}: 0 instrumentable guards present in this clone; "
              f"{len(oo)} out-of-instrument row(s) registered.")
        if census_path:
            write_census(census_path, slug, [], oo, [],
                         interpreter_id(python, redact=True))
        return EXIT_CLEAN

    trace, rc, tail = run_traced(repo, targets, python=python)
    if trace is None:
        print(f"{slug}: the traced run produced no trace file (pytest rc={rc}). "
              f"That is UNDECIDABLE, not clean.\n{tail}", file=sys.stderr)
        return EXIT_UNDECIDABLE
    executed = trace.get("executed", {})
    no_tests = not [t for t in targets if t.name.startswith("test_")]

    # 🔴 A DISARMED TRACER MUST NOT PUBLISH. If any test cleared or replaced
    # `sys.settrace`, every target executed afterwards went unrecorded, so the
    # trace is a LOWER BOUND -- and a lower bound on execution is an UPPER
    # bound on deadness, i.e. false positives against live code, on a green run.
    if trace.get("clobbered"):
        print(f"{slug}: a test cleared or replaced sys.settrace during the run, "
              f"so the trace is a lower bound and live branches would be "
              f"reported dead. UNDECIDABLE, not clean.", file=sys.stderr)
        return EXIT_UNDECIDABLE

    all_flags, undecidable = [], []
    for t in targets:
        # REPO-RELATIVE in the artifact: the census is committed and read on
        # other machines, where an absolute path names a directory that does
        # not exist -- and here it would bake in a throwaway worktree's name.
        rel = str(t.relative_to(repo))
        # PARSEABILITY FIRST. A file that will not parse is "cannot be
        # analysed" whatever the trace says -- and it is also, necessarily,
        # never traced, so the zero-lines arm below would otherwise claim it
        # was driven by a subprocess. Attribute the cause you actually know.
        try:
            src = t.read_text(encoding="utf-8")
            dg.branch_bodies(src, rel)
        except (SyntaxError, ValueError, OSError) as e:
            # TWO names, not five. An audit found that only SyntaxError and
            # IndentationError were caught, so a file this tool could not
            # analyse escaped as a traceback and exit 1 -- indistinguishable
            # from "found dead branches" -- and no census was written at all.
            # The first fix widened this to five names; three of them were
            # REDUNDANT SPELLINGS that read as coverage while adding nothing,
            # which is the very defect this tool looks for. Measured on 3.12:
            # so a file this tool could not analyse escaped as a traceback and
            # exit 1 -- indistinguishable from "found dead branches" -- and no
            # census was written at all. UnicodeDecodeError is the reachable
            # one: a .py file in latin-1 raises it from `read_text`.
            #
            # 🔴 `tokenize.TokenError` IS DELIBERATELY ABSENT, though it is not
            # a SyntaxError subclass and the first fix DID add it. Measured on
            # 3.12: every input that raises TokenError (unterminated string,
            # unterminated triple-quote, open bracket at EOF, trailing
            # backslash) raises SyntaxError from `ast.parse` FIRST, and
            # `ast.parse` runs before any tokenising. The catch was therefore
            # unreachable -- a dead guard branch, in the fix for a dead-guard
            # finder. Deleted and stated, per this repo's own rule, rather than
            # kept as reassuring width. If you find an input that parses but
            # will not tokenise, add it back WITH that input as a test.
            undecidable.append(f"{rel}: cannot be analysed ({type(e).__name__}: {e})")
            continue
        # 🔴 ZERO EXECUTED LINES IS "NEVER TRACED", NOT "ENTIRELY DEAD". The
        # common cause is a library guard whose tests drive it through a
        # SUBPROCESS, which `sys.settrace` cannot see. Reporting every branch
        # of such a file would be the tool's worst output: a confident,
        # complete, entirely false census of working code.
        # ⚠️ LIMIT: this cannot fire for a pytest FILE, because collection
        # imports it and its module-level lines always trace. It protects
        # library modules. A test file whose subject runs only in a subprocess
        # still under-reports, and nothing here detects that.
        if not executed.get(str(t)):
            # Say WHICH zero this is. "driven through a subprocess" is a guess
            # when no test file was registered at all -- the code knows that
            # case and used to report the wrong cause anyway, under a comment
            # 40 lines above saying "attribute the cause you actually know".
            why = ("no registered test file drives it -- the registry lists "
                   "library modules only" if no_tests else
                   "it is driven through a subprocess, or was never imported")
            undecidable.append(
                f"{rel}: NO line of this file was traced; {why} -- not dead.")
            continue
        # 🔴 NO try/except HERE. `src` is already in memory and `branch_bodies`
        # already parsed it at the check above, so `evaluate` cannot raise the
        # analysis errors -- the handler that used to wrap this was unreachable
        # width in the fix for a dead-guard finder. If this ever does raise, the
        # traceback is the honest outcome: it means an assumption above is wrong.
        all_flags.extend(dg.evaluate(rel, src, set(executed.get(str(t), []))))

    unres = dg.unresolved(all_flags)
    interp = interpreter_id(python)
    if verbose:
        _report(slug, targets, all_flags, unres, oo, undecidable, rc, interp)
    if census_path:
        write_census(census_path, slug, all_flags, oo, undecidable,
                     interpreter_id(python, redact=True), rc)
    # Undecidable outranks flags: "I could not measure part of this" must not
    # be reported as "here is the measurement".
    if undecidable:
        return EXIT_UNDECIDABLE
    return EXIT_FLAGS if unres else EXIT_CLEAN


def _report(slug, targets, flags, unres, oo, undecidable, rc, interp):
    code, nfail = rc if isinstance(rc, tuple) else (rc, 0)
    print(f"== {slug}")
    print(f"   interpreter  : {interp}")
    print(f"   instrumented : {len(targets)} guard file(s)   (pytest rc={code})")
    if nfail:
        # 🔴 A branch inside a test that FAILED did not run for a reason other
        # than deadness. Saying "N flagged" over a red run, without this line,
        # would be the over-claim this tool exists to stop.
        print(f"   🔴 RED RUN   : {nfail} test(s) FAILED. Branches downstream of "
              f"a failure did not execute for a reason that is NOT deadness -- "
              f"flags in those files are weaker evidence. Green the run, or "
              f"attribute each flag by hand.")
    # Say ROWS, not guards. These are registry entries, each covering an
    # unknown number of files; calling them a guard count would invent a
    # denominator this tool cannot measure.
    print(f"   NOT measured : {len(oo)} registered out-of-instrument ROW(s) "
          f"(registry entries, NOT a guard count -- no denominator is derivable "
          f"here)")
    print(f"   flagged      : {len(flags)} branch bodies never executed "
          f"({len(flags) - len(unres)} justified, {len(unres)} unresolved)")
    for f in sorted(unres, key=lambda x: (x.path, x.branch.first_line)):
        b = f.branch
        print(f"   FLAG {f.path}:{b.first_line} [{b.kind}] {b.snippet}")
    for u in undecidable:
        print(f"   UNDECIDABLE {u}")


_CENSUS_HEADER = [
    "# Measured census of guard branches with zero corpus instances.",
    "# Regenerate, per repo: scripts/dead-guard-scan.py --repo <path> --census <this file>",
    "# 🔴 out-of-instrument rows are NOT a clean result -- they are guards this tool",
    "#    CANNOT see. A repo's absence from this file is not evidence about it.",
    "# 🔴 A flag has THREE readings and only two are defects: dead code, an untested",
    "#    reporting branch, or a branch driven through a subprocess. See",
    "#    scripts/lib/dead_guard.py. Adjudicating them is a human's job.",
    "repo_slug\tstatus\tlocation\tkind\tcase_handled\tcorpus_instances\tjustification",
]


def _tsv(s):
    """A TAB inside a snippet or a reason would shift every later column."""
    return str(s).replace("\t", "\\t").replace("\n", " ").replace("\r", " ")


def write_census(path, slug, flags, oo, undecidable, interp="?", rc=(0, 0)):
    """Rewrite THIS repo's rows, leaving other repos' rows intact.

    🔴 IDEMPOTENT, because the regeneration command is printed in the file's own
    header. Appending meant that following your own instructions doubled every
    row, and a flag resolved in the source was never removed from the artifact
    -- so the census drifted upward from the thing it claims to measure.
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    code, nfail = rc if isinstance(rc, tuple) else (rc, 0)

    # 🔴 KEEP OTHER REPOS' PROVENANCE LINES. Dropping every `#` line deleted the
    # `# <slug> measured under ...` note for every OTHER repo on every scan, so
    # the committed census ended up carrying ONE note (the last repo scanned)
    # for four repos -- and the missing ones included the RED-RUN warning this
    # same fix round had just added. A per-repo idempotent rewrite must replace
    # only THIS repo's lines, not everything that starts with a hash.
    kept = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            if line in _CENSUS_HEADER or line.startswith("repo_slug\t"):
                continue                       # header is re-emitted below
            # NOTE: this repo's own old lines are NOT skipped here. They are
            # grouped like everyone else's and then REPLACED wholesale by
            # `blocks[slug] = mine` below. Two `continue`s used to do it here
            # as well; the battery showed them to be dead (mutating them away
            # changed nothing), so they are gone rather than kept as
            # reassuring width.
            kept.append(line)

    out = []
    blocks = {}
    for line in kept:
        key = line.split("\t", 1)[0] if not line.startswith("#") else \
            line.split(" ", 2)[1] if line.startswith("# ") else ""
        blocks.setdefault(key, []).append(line)
    # The interpreter VERSION is part of the measurement (a different one takes
    # different branches). The PATH is not written -- see `interpreter_id`.
    note = f"# {slug} measured under {interp}"
    if nfail:
        note += (f" -- 🔴 RED RUN, {nfail} test(s) FAILED: branches downstream of "
                 f"a failure did not execute for a reason that is NOT deadness")
    out.append(f"{note} (pytest rc={code})")
    for f in sorted(flags, key=lambda x: (x.path, x.branch.first_line)):
        b = f.branch
        out.append(f"{slug}\tflagged\t{_tsv(f.path)}:{b.first_line}\t{b.kind}\t"
                   f"{_tsv(b.snippet)}\t0\t{_tsv(f.justified_reason or '-')}")
    for r in oo:
        out.append(f"{slug}\tout-of-instrument\t-\t{r['lang']}\t"
                   f"{_tsv(r['selector'])}\tunmeasured\t-")
    for u in undecidable:
        out.append(f"{slug}\tundecidable\t{_tsv(u)}\t-\t-\t-\t-")
    # 🔴 ORDER-STABLE, SORTED BY SLUG. Appending this repo's block after the
    # kept lines moved the scanned repo to EOF, so re-deriving ONE repo
    # produced a whole-file reordering diff -- a reviewer re-running the
    # command printed in the census's own header could not tell "nothing
    # changed" from "everything moved". Idempotent per repo is not enough; the
    # artifact has to be byte-stable under any scan order.
    mine = out
    blocks[slug] = mine
    body = []
    for key in sorted(blocks):
        body.extend(blocks[key])
    p.write_text("\n".join(list(_CENSUS_HEADER) + body) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# controls

_LIVE = '''
def scan(lines):
    hits = []
    for ln in lines:
        if "MARKER" in ln:
            hits.append(ln)
    return hits
'''

_DEAD = '''
def scan(lines):
    hits = []
    for ln in lines:
        if "MARKER" in ln:
            hits.append(ln)
        if "NOBODY_WRITES_THIS" in ln:
            hits.append("dead")
    return hits
'''


def _controls(body, corpus_line):
    """Run the real analysis over a synthetic guard, driven for real."""
    ns = {}
    exec(compile(body, "<control>", "exec"), ns)
    executed = set()
    target = "<control>"

    def tracer(frame, event, arg):
        if frame.f_code.co_filename == target:
            if event == "line":
                executed.add(frame.f_lineno)
            return tracer
        return None

    sys.settrace(tracer)
    try:
        ns["scan"]([corpus_line])
    finally:
        sys.settrace(None)
    return dg.evaluate("<control>", body, executed)


def self_test():
    """🔴 A detector shown only to go GREEN has not been shown to work.

    Both directions are driven here, plus the justification hatch and the
    `__main__` exclusion, because every one of them is a way this tool could
    report a comforting zero while measuring nothing.
    """
    ok = True

    # POSITIVE CONTROL -- a planted zero-instance branch MUST be flagged.
    flags = _controls(_DEAD, "a MARKER here")
    hit = [f for f in flags if "NOBODY_WRITES_THIS" in f.branch.snippet
           or f.branch.snippet == 'hits.append("dead")']
    print(f"positive control (planted dead branch): {len(flags)} flag(s) "
          f"-> {[f'{f.branch.kind}:{f.branch.first_line}' for f in flags]}")
    ok &= len(flags) == 1 and bool(hit)

    # NEGATIVE CONTROL -- a branch with a real corpus instance must NOT flag.
    flags = _controls(_LIVE, "a MARKER here")
    print(f"negative control (branch with a real instance): {len(flags)} flag(s)")
    ok &= flags == []

    # NEGATIVE CONTROL -- the tool must be able to see the SAME guard go dead
    # when the corpus stops containing the case. Without this, the positive
    # above could be a property of the source text rather than of execution.
    flags = _controls(_LIVE, "nothing matching")
    print(f"corpus control (same guard, corpus lacks the case): "
          f"{len(flags)} flag(s)")
    ok &= len(flags) == 1

    # HATCH -- a one-line justification at the site resolves a flag, and a
    # BARE marker with no reason does not.
    src = _DEAD.replace('if "NOBODY_WRITES_THIS" in ln:',
                        'if "NOBODY_WRITES_THIS" in ln:  # pragma: no cover - unreachable by design')
    flags = _controls(src, "a MARKER here")
    print(f"hatch control (justified): {len(flags)} flag(s), "
          f"{len(dg.unresolved(flags))} unresolved")
    ok &= len(flags) == 1 and dg.unresolved(flags) == []

    # HATCH NEGATIVE -- `# pragma: no cover` with NO reason must NOT resolve.
    src = _DEAD.replace('if "NOBODY_WRITES_THIS" in ln:',
                        'if "NOBODY_WRITES_THIS" in ln:  # pragma: no cover')
    flags = _controls(src, "a MARKER here")
    print(f"hatch control (bare marker, no reason): "
          f"{len(dg.unresolved(flags))} unresolved (want 1)")
    ok &= len(dg.unresolved(flags)) == 1

    # EXCLUSION -- `if __name__ == "__main__":` is a module entry point, not a
    # guard branch, and never runs under a runner.
    n = len(dg.branch_bodies('if __name__ == "__main__":\n    pass\n'))
    print(f"__main__ exclusion: {n} branch bodies enumerated (want 0)")
    ok &= n == 0

    # SUB-LINE LIMIT, asserted rather than only documented: a ternary and an
    # `and` are NOT enumerated, so they can never be silently reported clean.
    n = len(dg.branch_bodies("x = 1 if a else 2\ny = a and b\n"))
    print(f"sub-line branches enumerated: {n} (want 0 -- stated limit)")
    ok &= n == 0

    print("SELF-TEST: %s" % ("PASS" if ok else "FAIL"))
    return EXIT_CLEAN if ok else EXIT_FLAGS


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="path to a clone to scan")
    ap.add_argument("--census", help="append the measured census to this TSV")
    ap.add_argument("--self-test", action="store_true",
                    help="run the detector's own positive/negative controls")
    ap.add_argument("--registry", default=None,
                    help="registry TSV to use instead of the committed one "
                         "(the detector's own end-to-end tests drive it this way)")
    ap.add_argument("--python", default=None,
                    help="interpreter to run the guards under. Defaults to the "
                         "one running this script, which under direnv is the "
                         "venv of your CWD -- not necessarily the target repo's")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    if not args.repo:
        ap.error("give --repo <path>, or --self-test")
    return scan(args.repo, args.census, python=args.python,
                registry=args.registry)


if __name__ == "__main__":
    sys.exit(main())
