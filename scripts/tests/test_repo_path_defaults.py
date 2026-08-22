"""`${VAR:-default}` cannot tell UNSET from SET-BUT-EMPTY — and here the default
is the OPERATOR'S OWN CLONE.

🔴 WHY THIS FILE EXISTS. `ship.sh`, `drift-check.sh` and the analyze-service
index's `commit.sh` each resolve a repository path with `${VAR:-$HOME/...}`.
That form treats an EMPTY value exactly like an absent one, so a caller whose
own path computation returned `""` does not get an error — it silently gets
`$HOME/workspace/devrc` (or `$HOME/.claude/analyze-service-index`) and the
script then fetches, converges, `git add`s and commits in the operator's real
clone or real index store, believing it was handed a sandbox.

UNSET must keep defaulting: the remote legs of `ship.sh` and `drift-check.sh`
deliberately do NOT forward these variables over ssh, and the far host's repo
lives at its own `$HOME/workspace/devrc`. So the fix is not `${VAR:?}` — it is a
set-but-EMPTY guard that stops the run while leaving unset alone.

These guards and their ledger were written for PR #689 and are salvaged here on
their own, because they are independent of that PR's git-interceptor: they are
plain caller-bug guards in three production scripts and they stand whatever
happens to the guard architecture.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIP = REPO_ROOT / "scripts" / "ship.sh"
DRIFT = REPO_ROOT / "scripts" / "drift-check.sh"


def _git(*args, cwd=None, env=None):
    return subprocess.run(["git", *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


def _mkrepo(path: Path, *, bare=False, initial="main") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    argv = ["git", "init", "-q", "-b", initial, str(path)]
    if bare:
        argv.insert(2, "--bare")
    subprocess.run(argv, check=True, capture_output=True)
    if not bare:
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(["git", "-C", str(path), "config", k, v], check=True,
                           capture_output=True)
        (path / "f").write_text("x\n")
        subprocess.run(["git", "-C", str(path), "add", "f"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"],
                       check=True, capture_output=True)
    return path


def _victim_state(victim: Path) -> dict:
    def cfg(k):
        return _git("-C", str(victim), "config", "--get", k).stdout.strip()

    return {
        "core.bare": cfg("core.bare"),
        "core.hooksPath": cfg("core.hooksPath"),
        "origin": cfg("remote.origin.url"),
        "branches": sorted(
            _git("-C", str(victim), "for-each-ref", "--format=%(refname)",
                 "refs/heads").stdout.split()),
        "HEAD": _git("-C", str(victim), "symbolic-ref", "-q", "HEAD").stdout.strip(),
        "commits": len(
            _git("-C", str(victim), "log", "--oneline").stdout.splitlines()),
    }


# --------------------------------------------------------------------------- #
# THE `${VAR:-default}` SITES — pinned BOTH ways
# --------------------------------------------------------------------------- #
#: "<file>|<the assignment line, verbatim>|<the variable the guard must test>".
#: A ledger, not a convenience list: the test below fails when a site GROWS
#: (a new HOME-defaulting repo path with no set-but-empty guard) *or* SHRINKS
#: (an entry naming a line that no longer exists — deleted, renamed, or a typo).
EMPTY_DEFAULT_SITES = (
    ("scripts/ship.sh", 'SHIP_REPO="${SHIP_REPO:-$HOME/workspace/devrc}"', "SHIP_REPO"),
    ("scripts/ship.sh", 'repo="${SHIP_REPO:-$HOME/workspace/devrc}"', "SHIP_REPO"),
    ("scripts/drift-check.sh", 'DRIFT_REPO="${DRIFT_REPO:-$HOME/workspace/devrc}"', "DRIFT_REPO"),
    # TWICE, deliberately: the CHECK payload and the SRCREPO payload each carry
    # their own copy, because each is piped to a `bash -s` on ANOTHER host and
    # cannot source anything. Both need their own guard.
    ("scripts/drift-check.sh", 'repo="${DRIFT_REPO:-$HOME/workspace/devrc}"', "DRIFT_REPO"),
    ("scripts/drift-check.sh", 'repo="${DRIFT_REPO:-$HOME/workspace/devrc}"', "DRIFT_REPO"),
    ("scripts/analyze-service-index/commit.sh",
     'STORE="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"', "POSITIONAL"),
)

#: Any `${SOMETHING:-…$HOME…}` / `${…:-…~/…}` that names a path this repo's
#: scripts then run git against. The scan below finds them mechanically so a
#: SIXTH site cannot be added without either a guard or a ledger entry.
_HOME_DEFAULT_RE = re.compile(
    r'^\s*\w+="\$\{[A-Za-z_][A-Za-z0-9_]*(?:\[0\])?:-\$?\{?HOME\}?/[^"]*'
    r'(?:workspace/devrc|\.claude/analyze-service-index)[^"]*"\s*$')


def _sites_in(path: Path):
    return [(n, ln.rstrip("\n")) for n, ln in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1)
        if _HOME_DEFAULT_RE.match(ln)]


def test_every_home_defaulting_repo_path_is_pinned_in_the_ledger():
    """🔴 BOTH WAYS. A new site with no ledger entry is a new copy of the bug; a
    ledger entry naming no line is accounting that describes nothing."""
    found = []
    for rel in sorted({s[0] for s in EMPTY_DEFAULT_SITES}):
        for _, line in _sites_in(REPO_ROOT / rel):
            found.append((rel, line.strip()))
    pinned = [(f, line) for f, line, _ in EMPTY_DEFAULT_SITES]
    assert sorted(found) == sorted(pinned), (
        "the HOME-defaulting repo-path sites and EMPTY_DEFAULT_SITES disagree.\n"
        f"  on disk: {sorted(found)}\n"
        f"  pinned : {sorted(pinned)}\n"
        "Do NOT delete an entry to make this pass — every one of these silently "
        "resolves to the operator's own clone when its variable is set-but-EMPTY.")


@pytest.mark.parametrize(
    "rel,line,var", sorted(set(EMPTY_DEFAULT_SITES)),
    ids=[f"{f.split('/')[-1]}:{v}" for f, _, v in sorted(set(EMPTY_DEFAULT_SITES))])
def test_each_site_is_immediately_preceded_by_a_set_but_empty_guard(rel, line, var):
    """🔴 `${VAR:-default}` CANNOT TELL UNSET FROM EMPTY. Unset must keep
    defaulting — the remote legs deliberately do not forward these variables —
    but a set-but-EMPTY value is a caller bug that would silently target the
    operator's own clone, and it must stop the run.

    🔴 EVERY OCCURRENCE, not the first. Two of these lines are byte-identical
    (`drift-check.sh`'s CHECK and SRCREPO payloads), and checking only
    `text.index(line)` would have declared the second one guarded on the
    strength of the first — a guard's DESCRIPTION claiming coverage its body
    does not provide.
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    starts = [m.start() for m in re.finditer(re.escape(line), text)]
    assert starts, f"{rel}: `{line}` is not in the file at all"
    for n, idx in enumerate(starts, 1):
        # The guard must be the NEAREST thing above the assignment, not somewhere
        # else in the file: 25 lines is generous for the comment block plus the
        # test, and small enough that a distant unrelated guard cannot satisfy it.
        window = "\n".join(text[:idx].splitlines()[-25:])
        where = f"{rel}: occurrence {n}/{len(starts)} of `{line}`"
        if var == "POSITIONAL":
            assert '[ -z "${POSITIONAL[0]}" ]' in window, (
                f"{where} has no given-but-EMPTY guard above it.")
        else:
            assert f'"${{{var}+set}}" = set' in window and f'[ -z "${var}" ]' in window, (
                f"{where} has no set-but-EMPTY guard above it. Without one, "
                f"{var}='' resolves to $HOME/workspace/devrc.")


@pytest.mark.parametrize("script,var", [(SHIP, "SHIP_REPO"), (DRIFT, "DRIFT_REPO")],
                         ids=["ship.sh", "drift-check.sh"])
def test_an_empty_repo_override_stops_the_run_instead_of_targeting_the_real_clone(
        tmp_path, script, var):
    """🔴 BEHAVIOURAL, not structural. A structural check type-checks past a
    guard that is present but wrong.

    The fake HOME contains a REAL repository at `workspace/devrc` — the place an
    empty value resolves to. If the guard were absent the script would fetch and
    report on it; with the guard it must exit non-zero having said the variable
    is empty, and that repo must be untouched.
    """
    home = tmp_path / "home"
    victim = _mkrepo(home / "workspace" / "devrc")
    before = _victim_state(victim)
    env = dict(os.environ)
    env.update(HOME=str(home), SHIP_ROLE="workbench", GIT_CONFIG_GLOBAL=str(tmp_path / "gc"))
    env[var] = ""
    p = subprocess.run(["bash", str(script), "--no-remote"], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    # 🔴 THE EXACT STATUS, not merely non-zero. MEASURED: a mutant that kept the
    # message and dropped the `exit 2` SURVIVED an earlier version of this test
    # — the script carried on, defaulted to $HOME/workspace/devrc, converged the
    # victim, and failed later for an unrelated reason, so "non-zero" and
    # "EMPTY appears in the output" were both still true. A green for the wrong
    # reason is the failure this whole guard is about.
    assert p.returncode == 2, (
        f"an EMPTY {var} did not stop the run with exit 2 (got {p.returncode}). "
        f"If the guard printed but did not exit, the script continued to the "
        f"default:\n{out}")
    assert "EMPTY" in out, f"the run stopped, but not for this reason:\n{out}"
    # 🔴 AND NOTHING DOWNSTREAM RAN. Both scripts announce each host before doing
    # any work — `=== local (…) ===` and `[<host>] …`. Either line means the
    # guard did not stop the run, whatever the exit status said.
    started = [ln for ln in out.splitlines()
               if ln.startswith("===") or ln.lstrip().startswith("[")]
    assert not started, (
        f"{script.name} began operating on a host despite {var}='':\n"
        + "\n".join(started))
    assert _victim_state(victim) == before, (
        f"{script.name} touched $HOME/workspace/devrc despite {var}=''")


def test_an_unset_repo_override_still_defaults(tmp_path):
    """The other direction, and it is load-bearing: the remote legs deliberately
    do NOT forward these variables, so an UNSET value must keep resolving to
    `$HOME/workspace/devrc`. A guard that failed on unset too would break the
    remote leg of both scripts."""
    home = tmp_path / "home"
    _mkrepo(home / "workspace" / "devrc")
    env = dict(os.environ)
    env.update(HOME=str(home), SHIP_ROLE="workbench",
               GIT_CONFIG_GLOBAL=str(tmp_path / "gc"))
    env.pop("DRIFT_REPO", None)
    env["DRIFT_STATE_DIR"] = str(tmp_path / "state")
    env["DRIFT_SESSION_MANAGER"] = str(tmp_path / "no-such-session-manager")
    p = subprocess.run(["bash", str(DRIFT), "--no-remote"], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert "SET but EMPTY" not in out, f"unset was treated as empty:\n{out}"
    assert "no repo at" not in out, (
        f"the default no longer resolves to $HOME/workspace/devrc:\n{out}")
