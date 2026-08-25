#!/usr/bin/env python3
"""Find guard branches with zero corpus instances.

    python3 scripts/dead-guard-scan.py --repo <path>   # 0 clean, 1 unresolved flag
    python3 scripts/dead-guard-scan.py --self-test     # positive + negative controls
    python3 scripts/dead-guard-scan.py --repo <path> --census <out.tsv>

A guard branch that never executes -- against the real corpus AND the guard's
own battery -- is either dead recognition code or a reporting branch with no
positive control. Both are the defect `claude/RULES.md` names under "A guard's
DESCRIPTION claims COVERAGE"; the remedy is to DELETE it and state the limit,
not to harden it. The analysis, and its stated limits, live in
`scripts/lib/dead_guard.py`. Resolve a flag by deleting the branch or writing
one line at the site: `# pragma: no cover - <reason>`.

🔴 THIS IS ADVISORY. It gates nothing until deliberately wired into CI, and its
own precision on real code is a human call -- flags are evidence for that
reading, not a verdict to auto-apply.

🔴 WHAT A CLEAN EXIT DOES NOT MEAN. Only the `instrument` rows of
`scripts/data/dead-guard-registry.tsv` are measured -- roughly 88 of ~270
guards across the four repos. Everything else is bash, TypeScript or Go, and is
listed there as out-of-instrument WITH ITS REASON. Exit 0 means "no unresolved
flag among the guards this tool can see", never "the repo's guards are alive".
The census prints that ratio on every run so the number is not quotable without
its denominator.
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


def run_traced(repo, targets, python=None, extra_args=()):
    """Run the guards under pytest with the line tracer. Returns
    (executed, rc, tail). `executed` is None when the trace never landed --
    a missing file must not read as 'nothing executed'.

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
    with tempfile.TemporaryDirectory(prefix="dgs-") as td:
        out = pathlib.Path(td) / "executed.json"
        env = dict(os.environ)
        env["DGS_TARGETS"] = os.pathsep.join(str(t) for t in targets)
        env["DGS_OUT"] = str(out)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(PLUGIN_DIR)] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        env.pop("PYTEST_ADDOPTS", None)
        cmd = [python, "-m", "pytest", "-p", "dead_guard_plugin",
               "-q", "--no-header", "--continue-on-collection-errors",
               "-p", "no:cacheprovider",
               *[str(t) for t in targets if t.name.startswith("test_")], *extra_args]
        proc = subprocess.run(cmd, cwd=str(repo), env=env,
                              capture_output=True, text=True, timeout=3600)
        tail = (proc.stdout or "")[-3000:] + (proc.stderr or "")[-1500:]
        # Count FAILED lines rather than reading the exit code: a red run leaves
        # branches unexecuted for a reason that is NOT deadness, so the caller
        # must be able to say how much of the census rests on it.
        nfail = len(re.findall(r"^FAILED ", proc.stdout or "", re.M))
        if not out.exists():
            return None, proc.returncode, tail
        return (json.loads(out.read_text(encoding="utf-8")),
                (proc.returncode, nfail), tail)


def interpreter_id(python):
    """`<path> (Python X.Y.Z)` -- recorded so a trace carries its own scope."""
    try:
        v = subprocess.run([python, "-c", "import sys;print('.'.join(map(str,sys.version_info[:3])))"],
                           capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        v = "?"
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
                         interpreter_id(python))
        return EXIT_CLEAN

    executed, rc, tail = run_traced(repo, targets, python=python)
    if executed is None:
        print(f"{slug}: the traced run produced no trace file (pytest rc={rc}). "
              f"That is UNDECIDABLE, not clean.\n{tail}", file=sys.stderr)
        return EXIT_UNDECIDABLE

    all_flags, undecidable = [], []
    for t in targets:
        src = t.read_text(encoding="utf-8")
        # REPO-RELATIVE in the artifact: the census is committed and read on
        # other machines, where an absolute path names a directory that does
        # not exist -- and here it would bake in a throwaway worktree's name.
        rel = str(t.relative_to(repo))
        try:
            flags = dg.evaluate(rel, src, set(executed.get(str(t), [])))
        except (SyntaxError, IndentationError) as e:
            undecidable.append(f"{rel}: will not parse ({e})")
            continue
        all_flags.extend(flags)

    unres = dg.unresolved(all_flags)
    interp = interpreter_id(python)
    if verbose:
        _report(slug, targets, all_flags, unres, oo, undecidable, rc, interp)
    if census_path:
        write_census(census_path, slug, all_flags, oo, undecidable, interp)
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
    print(f"   NOT measured : {len(oo)} registered out-of-instrument row(s) -- "
          f"bash / TypeScript / Go")
    print(f"   flagged      : {len(flags)} branch bodies never executed "
          f"({len(flags) - len(unres)} justified, {len(unres)} unresolved)")
    for f in sorted(unres, key=lambda x: (x.path, x.branch.first_line)):
        b = f.branch
        print(f"   FLAG {f.path}:{b.first_line} [{b.kind}] {b.snippet}")
    for u in undecidable:
        print(f"   UNDECIDABLE {u}")


def write_census(path, slug, flags, oo, undecidable, interp="?"):
    p = pathlib.Path(path)
    new = not p.exists()
    with p.open("a", encoding="utf-8") as fh:
        if new:
            fh.write("# Measured census of guard branches with zero corpus "
                     "instances. Regenerate: scripts/dead-guard-scan.py --repo "
                     "<path> --census <this file>\n")
            fh.write("# `status` out-of-instrument rows are NOT a clean result "
                     "-- they are guards this tool cannot see. See "
                     "scripts/data/dead-guard-registry.tsv.\n")
            fh.write("repo_slug\tstatus\tlocation\tkind\tcase_handled"
                     "\tcorpus_instances\tjustification\n")
        # The interpreter is part of the measurement: a different one takes
        # different branches, so a census without it is not reproducible.
        fh.write(f"# {slug} measured under {interp}\n")
        for f in sorted(flags, key=lambda x: (x.path, x.branch.first_line)):
            b = f.branch
            fh.write(f"{slug}\tflagged\t{f.path}:{b.first_line}\t{b.kind}\t"
                     f"{b.snippet}\t0\t{f.justified_reason or '-'}\n")
        for r in oo:
            fh.write(f"{slug}\tout-of-instrument\t-\t{r['lang']}\t"
                     f"{r['selector']}\tunmeasured\t-\n")
        for u in undecidable:
            fh.write(f"{slug}\tundecidable\t{u}\t-\t-\t-\t-\n")


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
