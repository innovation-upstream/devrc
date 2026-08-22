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
COMMIT_SH = REPO_ROOT / "scripts" / "analyze-service-index" / "commit.sh"


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

#: 🔴 READ-ONLY SITES OF THE SAME SHAPE, ledgered so the scan is complete in
#: BOTH directions rather than silently blind to them. Each of these is a
#: `${VAR:-$HOME/...}` repo path exactly like the guarded six — but every
#: consumer only READS (`git -C "$repo" show`, or an age-key `[ -r ]`), so a
#: set-but-empty value mis-targets a read and fails, rather than committing or
#: fetching into the operator's clone. They are exempt from the guard, NOT from
#: the accounting: if one ever gains a writing consumer it must move up into
#: `EMPTY_DEFAULT_SITES`. Found by the audit of #716 — the original regex was
#: narrow enough not to see them, which is the bug this ledger now closes.
READ_ONLY_SITES = (
    # `git -C "$homelab_repo" show origin/trunk:…` — a read. An empty $HOMELAB
    # mis-targets that read and the caller returns 0 with telemetry off; it
    # cannot fetch, commit or init.
    ("scripts/initiatives/run-sync.sh",
     'local homelab_repo="${HOMELAB:-${HOME}/workspace/homelab-talos}"'),
    # Same shape, same read, plus an age-key `[ -r ]` probe.
    ("scripts/collector/run-regrowth-check.sh",
     'HOMELAB_REPO="${HOMELAB:-${HOME}/workspace/homelab-talos}"'),
    # 🔴 These two were found by the WIDENED regex and had been pinned by
    # nobody — not by the original ledger, and not by the audit that widened it.
    # They are a kubeconfig FILE path, not a repo: consumed by `kubectl`, never
    # a git target, so an empty $KUBECONFIG mis-targets a cluster read. Listed
    # because the scan must be complete in both directions, and because their
    # discovery is the evidence that the previous regex was too narrow.
    ("scripts/initiatives/run-sync.sh",
     'export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"'),
    ("scripts/collector/run-regrowth-check.sh",
     'export KUBECONFIG="${KUBECONFIG:-${HOME}/workspace/homelab-talos/homelab-kubeconfig}"'),
)

#: 🔴 THE SCANNED FILE SET. The scan cannot be repo-wide without becoming a
#: lint, so it is an explicit list — every file that today contains a site of
#: either class. `test_the_scanned_file_set_covers_every_ledger_entry` pins it
#: against both ledgers so a file cannot be dropped from the scan while its
#: entries stay pinned.
SCANNED_FILES = tuple(sorted(
    {s[0] for s in EMPTY_DEFAULT_SITES} | {s[0] for s in READ_ONLY_SITES}))

#: Any `${SOMETHING:-…$HOME…}` / `${…:-…~/…}` naming a repo path this repo's
#: scripts then run git against.
#:
#: 🔴 WIDENED after the #716 audit measured the previous version missing **17 of
#: 19** plausible shapes — including `export VAR=`, `local VAR=`, an unquoted
#: RHS, a trailing comment, and the `~/` form the comment above it named
#: explicitly. That was not hypothetical: two live sites of this class already
#: existed unpinned (now in `READ_ONLY_SITES`), so "a further site cannot be
#: added silently" was FALSE at the time it was written. A ledger whose stated
#: scope exceeds its regex reads as coverage while providing none.
_HOME_DEFAULT_RE = re.compile(
    r"""^\s*
        (?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?   # export / local / declare
        \w+=
        (?P<q>["']?)                                      # optional quoting
        \$\{[A-Za-z_][A-Za-z0-9_]*(?:\[0\])?:-            # ${VAR:-  (NOT :=, NOT :?)
        (?:\$\{?HOME\}?|~)                                # $HOME / ${HOME} / ~
        /[^"'\s]*
        (?:workspace/devrc|workspace/homelab-talos|\.claude/analyze-service-index)
        [^"'\s]*
        \}(?P=q)
        \s*(?:\#.*)?$                                     # optional trailing comment
    """,
    re.VERBOSE)


def _sites_in(path: Path):
    return [(n, ln.rstrip("\n")) for n, ln in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1)
        if _HOME_DEFAULT_RE.match(ln)]


def test_the_scanned_file_set_covers_every_ledger_entry():
    """The scan is an explicit file list, so it can rot in the direction that
    hides sites. Pin it against both ledgers, both ways."""
    from_ledgers = sorted(
        {s[0] for s in EMPTY_DEFAULT_SITES} | {s[0] for s in READ_ONLY_SITES})
    assert list(SCANNED_FILES) == from_ledgers
    for rel in SCANNED_FILES:
        assert (REPO_ROOT / rel).is_file(), f"{rel} is scanned but does not exist"


def test_every_home_defaulting_repo_path_is_pinned_in_one_of_the_two_ledgers():
    """🔴 BOTH WAYS, and across BOTH classes. A new site in neither ledger is a
    new copy of the bug; a ledger entry naming no line is accounting that
    describes nothing.

    🔴 The read-only ledger is not a waiver list. It exists so that a site of
    this shape whose consumer only READS is *recorded as considered* instead of
    being invisible to the scan — the state the audit found this file in.
    """
    found = []
    for rel in SCANNED_FILES:
        for _, line in _sites_in(REPO_ROOT / rel):
            found.append((rel, line.strip()))
    pinned = ([(f, line) for f, line, _ in EMPTY_DEFAULT_SITES]
              + [(f, line) for f, line in READ_ONLY_SITES])
    assert sorted(found) == sorted(pinned), (
        "the HOME-defaulting repo-path sites and the ledgers disagree.\n"
        f"  on disk: {sorted(found)}\n"
        f"  pinned : {sorted(pinned)}\n"
        "Do NOT delete an entry to make this pass. If the new site's consumer "
        "WRITES (fetch/commit/init/add), it belongs in EMPTY_DEFAULT_SITES and "
        "needs a set-but-empty guard; if it only READS, add it to "
        "READ_ONLY_SITES with that reason.")


def test_the_regex_sees_the_shapes_its_docstring_claims():
    """🔴 POSITIVE CONTROL ON THE SCANNER ITSELF. The previous version of this
    regex missed 17 of 19 plausible spellings while its comment claimed to catch
    "any" of them, and two live sites sat outside it. A scanner is an instrument:
    a reassuring zero from it is worthless until it has been shown it CAN match.
    """
    must_match = (
        'REPO="${SHIP_REPO:-$HOME/workspace/devrc}"',
        '  repo="${DRIFT_REPO:-$HOME/workspace/devrc}"',
        'export REPO="${X:-${HOME}/workspace/devrc}"',
        '  local repo="${X:-${HOME}/workspace/homelab-talos}"',
        "REPO='${X:-$HOME/workspace/devrc}'",
        'REPO=${X:-$HOME/workspace/devrc}',
        'REPO="${X:-~/workspace/devrc}"',
        'REPO="${X:-$HOME/workspace/devrc}"  # trailing comment',
        'S="${POSITIONAL[0]:-${HOME}/.claude/analyze-service-index}"',
    )
    for line in must_match:
        assert _HOME_DEFAULT_RE.match(line), f"regex MISSED a real shape: {line!r}"

    # 🔴 NEGATIVE CONTROL — it must not match everything, or the ledger test
    # becomes unsatisfiable noise. `:=` and `:?` are different operators: `:?`
    # already errors on empty, and `:=` assigns; neither is this bug.
    must_not_match = (
        'REPO="${X:?$HOME/workspace/devrc}"',
        'REPO="${X:=$HOME/workspace/devrc}"',
        'REPO="$HOME/workspace/devrc"',
        'REPO="${X:-$HOME/workspace/other-repo}"',
        '# REPO="${X:-$HOME/workspace/devrc}"',
    )
    for line in must_not_match:
        assert not _HOME_DEFAULT_RE.match(line), f"regex OVER-matched: {line!r}"


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

        # 🔴 A CONDITION IS NOT A GUARD — IT MUST STOP THE RUN.
        # MEASURED by the #716 audit: the "keep the message, drop the exit"
        # mutant SURVIVED at three of the six sites, because this test only ever
        # asserted that the condition TEXT appeared. Demonstrated end to end —
        # a mutated `commit.sh` that warned instead of dying went on to `git
        # init` and commit inside a fake operator store while this suite
        # reported 9 passed. The docstring below claimed the mutant was killed;
        # it was killed at two sites and the sentence covered six. That is
        # exactly "a guard's DESCRIPTION claims COVERAGE its body does not
        # provide", and it is why the terminator is now asserted separately.
        after = "\n".join(text[idx:].splitlines()[:1])
        guard_body = window.rsplit("if ", 1)[-1] + after
        assert re.search(r"\b(exit\s+\d+|die\b|return\s+\d+)", guard_body), (
            f"{where}: the set-but-EMPTY condition is present but nothing in it "
            f"STOPS the run — no `exit`, `die` or `return`. A guard that prints "
            f"and falls through still resolves to the operator's own path, and "
            f"is strictly worse than none because it reads as protection.")


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


def test_an_empty_store_ARGUMENT_stops_commit_sh_instead_of_writing_the_real_store(tmp_path):
    """🔴 THE THIRD SITE, BEHAVIOURALLY. `commit.sh` was covered only
    structurally, and the #716 audit exploited exactly that: a mutant that
    warned instead of dying went on to `git init`, `git add` and `git commit`
    inside a fake operator store while the suite reported 9 passed.

    The fake HOME contains a real `.claude/analyze-service-index` — the place an
    empty argument resolves to. It must be byte-identical afterwards.
    """
    home = tmp_path / "home"
    store = home / ".claude" / "analyze-service-index"
    store.mkdir(parents=True)
    (store / "note.md").write_text("pre-existing\n")
    env = dict(os.environ)
    env.update(HOME=str(home), GIT_CONFIG_GLOBAL=str(tmp_path / "gc"))
    p = subprocess.run(["bash", str(COMMIT_SH), ""], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode != 0, (
        f"an EMPTY STORE argument did not stop commit.sh (rc {p.returncode}):\n{out}")
    assert "EMPTY" in out, f"it stopped, but not for this reason:\n{out}"
    # 🔴 THE POINT: no repository was created in the operator's real store.
    assert not (store / ".git").exists(), (
        "commit.sh ran `git init` in $HOME/.claude/analyze-service-index "
        "despite being handed an empty STORE argument")
    assert sorted(p.name for p in store.iterdir()) == ["note.md"], (
        f"the real store was modified: {sorted(p.name for p in store.iterdir())}")


@pytest.mark.parametrize("script,var", [(SHIP, "SHIP_REPO"), (DRIFT, "DRIFT_REPO")],
                         ids=["ship.sh", "drift-check.sh"])
def test_an_unset_repo_override_still_defaults(tmp_path, script, var):
    """The other direction, and it is load-bearing: the remote legs deliberately
    do NOT forward these variables, so an UNSET value must keep resolving to
    `$HOME/workspace/devrc`. A guard that failed on unset too would break the
    remote leg of both scripts.

    🔴 PARAMETRISED OVER BOTH SCRIPTS after the #716 audit found this control
    covered `drift-check.sh` alone while the PR body claimed it covered both.
    The audit built a mutant keeping every literal spelling the structural test
    greps for and adding `|| [ -z "${SHIP_REPO:-}" ]`: the suite stayed green
    while `ship.sh` with `SHIP_REPO` unset — the normal invocation, and the only
    one the remote leg ever gets — exited 2 and converged NOTHING on either
    host. A one-sided control is the failure mode this test exists to prevent.
    """
    home = tmp_path / "home"
    _mkrepo(home / "workspace" / "devrc")
    env = dict(os.environ)
    env.update(HOME=str(home), SHIP_ROLE="workbench",
               GIT_CONFIG_GLOBAL=str(tmp_path / "gc"))
    env.pop(var, None)
    env["DRIFT_STATE_DIR"] = str(tmp_path / "state")
    env["DRIFT_SESSION_MANAGER"] = str(tmp_path / "no-such-session-manager")
    p = subprocess.run(["bash", str(script), "--no-remote"], env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert "SET but EMPTY" not in out, f"unset was treated as empty:\n{out}"
    assert p.returncode != 2, (
        f"{script.name} exited 2 (the set-but-EMPTY status) on an UNSET {var} — "
        f"the guard is firing on the normal invocation:\n{out}")
    assert "no repo at" not in out, (
        f"the default no longer resolves to $HOME/workspace/devrc:\n{out}")
    # 🔴 POSITIVE ASSERTION, and it is the one with teeth. Every other check
    # here is NEGATIVE, so the audit slipped an `exit 6` in immediately before
    # the assignment and this test stayed green — nothing asserted the script
    # had actually got anywhere. Both scripts announce each host before doing
    # work (`=== local (…) ===`, `[<host>] …`), which is exactly the signal the
    # empty-value test asserts must be ABSENT. So assert its presence here: the
    # two arms now pin the same observable in opposite directions, and an early
    # exit on the unset path cannot satisfy both.
    started = [ln for ln in out.splitlines()
               if ln.startswith("===") or ln.lstrip().startswith("[")]
    assert started, (
        f"{script.name} with {var} UNSET never began operating on a host "
        f"(rc {p.returncode}). The default path must proceed, not exit early:\n{out}")
