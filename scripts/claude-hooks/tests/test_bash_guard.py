#!/usr/bin/env python3
"""Regression suite for bash-guard.py.

Two jobs:
  1. the real bad command shapes are blocked;
  2. the shapes that a reverted `_strip_message_text()` helper once let through
     STAY blocked. Each "was-a-bypass" case below was proven to execute a
     destructive command in a real shell while that helper was in place (PR
     #217, three audit rounds). They are the reason the helper is gone -- see
     the DESIGN NOTE in bash-guard.py before reintroducing anything like it.

Accepted trade-off: because the checks match raw command text, a command that
merely QUOTES a blocked shape is blocked too. The "FP (accepted)" cases below
assert that deliberately, so the behaviour is documented rather than
rediscovered. Workaround is in the deny message: write the text to a file and
use `git commit -F <file>`.

Run directly (hand-rolled asserts, not pytest-collectable -- the guard calls
main() at import time, so it is only ever driven via subprocess):
    python3 scripts/claude-hooks/tests/test_bash_guard.py
"""
import json, os, shutil, subprocess, sys, tempfile, time

GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bash-guard.py")
GUARD = os.path.normpath(GUARD)

# 🔴 EVERY CASE BELOW IS PINNED TO A NEUTRAL, NON-REPO cwd — do not remove this.
#
# The PreToolUse payload carries a `cwd`, and since 2026-08-10 one check
# (check_git_commit_to_main) READS IT: it resolves the branch of the repo the
# command would act on. That made this suite's verdicts depend on WHERE it was
# run from, and the two tiers disagreed — measured, not predicted:
#     cwd=/tmp (not a repo)              -> PASS   ← the nix sandbox's shape
#     cwd=<devrc checkout, on `main`>    -> FAIL   ← a dev host's shape, because
#                                                    run-tests.sh cd's to the git
#                                                    toplevel before running
#     cwd=<a worktree, on a feature br.> -> PASS
# The `plain commit` case at the bottom asserts `git commit -F …` is ALLOWED, and
# on a checkout sitting on `main` it is now correctly DENIED. So the suite would
# have gone red on the operator's own host while the CI sandbox stayed green —
# the exact two-tier blindness claude/RULES.md warns about, where greening one
# tier moves the bug instead of removing it.
#
# Pinning the cwd is the fix rather than editing that expectation, because the
# expectation is about the PARSER ("`git commit -F <file>` is not a blind-stage"),
# not about branch state. A test whose verdict depends on the checkout's current
# branch is a test that passes by accident of the environment.
# The new check has its OWN adapter-level cases, with their own controlled repos,
# at the bottom of this file.
NEUTRAL_CWD = tempfile.mkdtemp(prefix="bash-guard-neutral-")

# Dangerous substrings are built piecewise so this file can never trip a live
# guard that is inspecting the command used to run it.
# NOTE: keep a trailing word after -A. This USED to matter because the check
# required -A to be followed by whitespace, so a payload ending `-A'` matched
# nothing and made a test vacuous (that hid a real bypass once). Token matching
# now catches the quoted form too, but the trailing word is kept so these read
# as prose rather than as a bare flag.
ADDA = "git a" + "dd -A ever"
HARD = "git re" + "set --hard HEAD"


def run(cmd, cwd=None):
    """Return (blocked, error). `error` is non-empty if the guard misbehaved.

    `cwd` is the PreToolUse payload's cwd, NOT the subprocess's — it defaults to a
    neutral non-repo directory so a case's verdict cannot depend on which branch
    the checkout running the suite happens to be on. Pass it explicitly to
    exercise check_git_commit_to_main.
    """
    try:
        p = subprocess.run([sys.executable, GUARD],
                           input=json.dumps({"tool_name": "Bash",
                                             "cwd": cwd or NEUTRAL_CWD,
                                             "tool_input": {"command": cmd}}),
                           capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "guard TIMED OUT (>30s) -- catastrophic backtracking?"
    if p.returncode != 0:
        return False, f"guard exited {p.returncode}: {p.stderr.strip()[:200]}"
    if p.stderr.strip():
        return False, f"guard wrote stderr: {p.stderr.strip()[:200]}"
    out = p.stdout.strip()
    if not out:
        return False, ""                      # allowed
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, f"guard emitted non-JSON: {out[:200]}"
    decision = (data.get("hookSpecificOutput") or {}).get("permissionDecision")
    if decision != "deny":
        return False, f"unexpected decision: {decision!r}"
    return True, ""


CASES = [
    # --- real bad shapes: MUST BLOCK ------------------------------------
    ("real blind-stage",              ADDA, True),
    ("real hard-reset",               HARD, True),
    ("real cd&&git",                  "cd /tmp && git status", True),
    ("real add -A after a commit",    f'git commit -m "ok" && {ADDA}', True),
    ("explicit path staging",         "git a" + "dd clusters/foo.yaml", False),

    # --- blind-stage forms that evaded the whitespace-bounded match ----
    # Each was verified to stage every file in a throwaway repo (round-4 audit).
    ("blind: double-quoted -A",       'git a' + 'dd "-A"', True),
    ("blind: single-quoted -A",       "git a" + "dd '-A'", True),
    ("blind: quoted --all",           "git a" + "dd '--all'", True),
    ("blind: bundled -Av",            "git a" + "dd -Av", True),
    ("blind: bundled -vA",            "git a" + "dd -vA", True),
    ("blind: quoted dot",             'git a' + 'dd "."', True),
    ("blind: single-quoted dot",      "git a" + "dd '.'", True),
    ("blind: --no-ignore-removal",    "git a" + "dd --no-ignore-removal", True),
    ("blind: root pathspec :/",       "git a" + "dd :/", True),
    # ...without over-blocking ordinary staging
    ("ok: -p patch mode",             "git a" + "dd -p", False),
    ("ok: -u with a path",            "git a" + "dd -u src/", False),
    ("ok: -N intent-to-add",          "git a" + "dd -N newfile.txt", False),
    ("ok: relative path ./src",       "git a" + "dd ./src/main.go", False),
    ("ok: quoted path with spaces",   'git a' + 'dd "my file.txt"', False),
    ("ok: path containing a dot",     "git a" + "dd foo.yaml", False),
    ("ok: file named A",              "git a" + "dd A", False),

    # --- gaps found by the #219 audit: all proven to stage everything ---
    # `git -C <dir> add -A` is the shape RULES + check_cd_then_git push agents
    # into, and it bypassed the guard entirely (11 blind hits in transcripts).
    ("gap: git -C dir add -A",        "git -C /tmp/x a" + "dd -A", True),
    ("gap: git -C dir add .",         "git -C /tmp/x a" + "dd .", True),
    # NOTE: the git-dir must NOT end in `.git`, or the literal substring
    # "git add" makes the case vacuous -- base blocks it without the opt-hop.
    ("gap: git --git-dir= add -A",    "git --git-dir=/tmp/x/store a" + "dd -A", True),
    ("gap: git -C x -C y add -A",     "git -C /tmp/x -C /tmp/y a" + "dd -A", True),
    ("gap: git --exec-path= add -A",  "git --exec-path=/tmp/x a" + "dd -A", True),
    ("gap: git --no-pager add -A",    "git --no-pager a" + "dd -A", True),
    ("gap: blind flag before --",     "git a" + "dd -A -- src/", True),
    ("gap: git -c k=v add -A",        "git -c user.name=x a" + "dd -A", True),
    ("gap: git -P add -A",            "git -P a" + "dd -A", True),
    # backslash / ANSI-C escapes — same evasion class quoting was
    ("gap: backslash -\\A",           "git a" + "dd -\\A", True),
    ("gap: backslash \\-A",           "git a" + "dd \\-A", True),
    ("gap: escaped --a\\ll",          "git a" + "dd --a\\ll", True),
    ("gap: escaped dot",              "git a" + "dd \\.", True),
    ("gap: ANSI-C $'-A'",             "git a" + "dd $'-A'", True),
    # `git stage` is a documented synonym for `git add`
    ("gap: git stage -A",             "git st" + "age -A", True),
    ("gap: git stage .",              "git st" + "age .", True),
    # unique long-option prefixes
    ("gap: --al prefix",              "git a" + "dd --al", True),
    ("gap: --no-ignore-remov",        "git a" + "dd --no-ignore-remov", True),
    # subshell parens defeated the whitespace boundary
    ("gap: (git add -A)",             "(git a" + "dd -A)", True),
    ("gap: $(git add -A)",            "echo $(git a" + "dd -A)", True),
    # other whole-tree pathspecs
    ("gap: ./ and .//",               "git a" + "dd ./", True),
    # `..` errors at the repo ROOT but stages the WHOLE TREE from a
    # subdirectory -- and `git -C <repo>/sub add ..` reaches that with no `cd`.
    # A root-only test once suggested otherwise; don't re-derive from that.
    ("gap: parent .. (blind from subdir)", "git a" + "dd ..", True),
    ("gap: git -C sub add ..",         "git -C /tmp/x/sub a" + "dd ..", True),
    ("gap: ../ from a subdir",         "git -C /tmp/x/sub a" + "dd ../", True),
    ("gap: ../.. from deeper",         "git -C /tmp/x/a/b a" + "dd ../..", True),
    # near-miss siblings of already-blocked whole-tree pathspecs
    ("gap: ${PWD}",                    "git a" + "dd ${PWD}", True),
    ("gap: $PWD/",                     "git a" + "dd $PWD/", True),
    ("gap: backtick pwd",              "git a" + "dd `pwd`", True),
    ("gap: :/* root glob",             "git a" + "dd ':/*'", True),
    ("gap: .// double slash",          "git a" + "dd .//", True),
    # same-shape siblings of the stems above -- all proven whole-tree
    ("gap: $PWD//",                    'git a' + 'dd "$PWD//"', True),
    ("gap: $PWD/./",                   'git a' + 'dd "$PWD/./"', True),
    ("gap: $(pwd)//",                  'git a' + 'dd "$(pwd)//"', True),
    ("gap: `pwd`/./",                  'git a' + 'dd "`pwd`/./"', True),
    ("gap: :(top,glob)",               "git a" + "dd ':(top,glob)'", True),
    ("gap: :(top,icase)",              "git a" + "dd ':(top,icase)'", True),
    ("gap: bare colon pathspec",       "git a" + "dd ':'", True),
    ("gap: glob *",                   "git a" + "dd *", True),
    ("gap: $PWD",                     "git a" + "dd $PWD", True),
    ("gap: :(top)",                   "git a" + "dd ':(top)'", True),

    # ...and the tightening must not catch scoped forms
    ("ok: git -C dir add path",       "git -C /tmp/x a" + "dd src/main.go", False),
    ("ok: git -C dir status",         "git -C /tmp/x status", False),
    ("ok: rooted single path :/foo",  "git a" + "dd :/target.txt", False),
    ("ok: scoped glob *.go",          "git a" + "dd *.go", False),
    ("ok: file literally named -A",   "git a" + "dd -- -A", False),
    ("ok: stage a path",              "git st" + "age src/main.go", False),
    ("ok: --no-all is the opposite",  "git a" + "dd --no-all -u src/", False),
    ("ok: filename with -A inside",   "git a" + "dd 'CHANGELOG -A.md'", False),
    # 🔴 The opt-hop must NOT swallow sibling `add` subcommands. This repo runs
    # `git worktree add` constantly (it is the prescribed workflow), so a
    # regression here would block the thing RULES tells you to do.
    ("ok: git worktree add -B",       "git worktree a" + "dd -B br /tmp/wt origin/main", False),
    ("ok: git -C repo worktree add",  "git -C /repo worktree a" + "dd /tmp/wt -B x", False),
    ("ok: git remote add",            "git remote a" + "dd origin git@h:r.git", False),
    ("ok: git submodule add",         "git submodule a" + "dd https://h/r.git ext/r", False),
    ("ok: git notes add -m",          "git notes a" + "dd -m 'note'", False),
    ("ok: git stash (not stage)",     "git stash list", False),
    # after `--` everything is a path, so a file literally named -A is fine
    ("ok: git add -- x -A",           "git a" + "dd -- x -A", False),
    ("ok: $PWD-rooted single path",   "git a" + "dd $PWD/src/main.go", False),
    ("ok: rooted glob :/ *.go",       "git a" + "dd ':/src/*.go'", False),
    # the widened stems must not swallow scoped paths built from them
    ("ok: $PWD/<path>",               'git a' + 'dd "$PWD/src/main.go"', False),
    ("ok: ${PWD}/<path>",             'git a' + 'dd "${PWD}/x"', False),
    ("ok: $(pwd)/<file>",             'git a' + 'dd "$(pwd)/file.txt"', False),
    ("ok: $PWDX is a different var",  'git a' + 'dd "$PWDX"', False),
    ("ok: $PWD_BACKUP",               'git a' + 'dd "$PWD_BACKUP"', False),
    ("ok: :(top,glob) with a path",   "git a" + "dd ':(top,glob)x'", False),
    ("ok: :(exclude)vendor",          "git a" + "dd ':(exclude)vendor'", False),
    ("ok: :!foo negation",            "git a" + "dd ':!foo'", False),
    ("ok: ..foo is a filename",       "git a" + "dd ..foo", False),
    ("ok: .hidden file",              "git a" + "dd .hidden", False),
    ("ok: .github path",              "git a" + "dd .github/workflows/ci.yml", False),
    ("ok: ../sibling/file.txt",       "git a" + "dd ../sibling/file.txt", False),
    ("ok: ../pkg/ scoped dir",        "git a" + "dd ../pkg/", False),

    # --- was-a-bypass under _strip_message_text: MUST STAY BLOCKED ------
    # round 1: same-tag heredoc / substitution / stale offsets / escaped quote
    ("was-bypass: 2nd heredoc same tag",
     f"git commit -F - <<'EOF'\nnotes\nEOF\nbash <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: shell heredoc first",
     f"bash <<'EOF'\n{ADDA}\nEOF\ngit commit -F - <<'EOF'\nnotes\nEOF", True),
    ("was-bypass: heredoc in <( )",   f"git commit -F <(bash <<'EOF'\n{ADDA}\nEOF\n)", True),
    ("was-bypass: heredoc in $( )",   f"git commit -m \"$(bash <<'EOF'\n{ADDA}\nEOF\n)\"", True),
    ("was-bypass: long body shifts offsets",
     "git commit -F - <<'M1'\n" + "\n".join(f"body line {i}" for i in range(30))
     + f"\nM1\nbash <<'B'\n{ADDA}\nB\ngit commit -m 'ok'", True),
    ("was-bypass: escaped single quote",
     f"git commit -m 'msg \\' && {ADDA} && echo 'done'", True),
    # round 2: `&` backgrounds the commit, heredoc feeds a real shell
    ("was-bypass: & backgrounds the commit",
     f"git commit -m 'ok' & bash <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: & with sh + hard-reset",
     f"git commit -m 'ok' & sh <<'EOF'\n{HARD}\nEOF", True),
    # round 3: decoy argument satisfied a substring command test
    ("was-bypass: decoy 'git merge' arg",
     f"bash -s -- 'git merge' <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: decoy 'git tag' arg",
     f"bash -s -- 'git tag' <<'EOF'\n{ADDA}\nEOF", True),
    ("was-bypass: decoy via python3 -",
     f"python3 - 'jj describe' <<'EOF'\nimport os; os.system('{ADDA}')\nEOF", True),
    # never closed while stripping existed: ${ } funsub
    ("was-bypass: ${ } funsub",
     f"git commit -m \"${{ bash <<'EOF'\n{ADDA}\nEOF\n}}\"", True),
    ("was-bypass: unterminated heredoc",
     f"git commit -F - <<'NOPE'\nsome text\n{ADDA}", True),

    # --- accepted false positives (quoting a rule) ----------------------
    # Deliberate: the guard matches raw text. The deny message tells you to use
    # `git commit -F <file>`. Asserted so the trade-off stays visible.
    ("FP (accepted): -m quoting the rule",
     f'git commit -m "docs: never {ADDA}"', True),
    ("FP (accepted): heredoc commit body",
     f"git commit -F - <<'MSG'\nrule: {ADDA}\nMSG", True),
    ("FP (accepted): gh pr --body",
     f"gh pr create --body 'never {ADDA}'", True),

    # --- unrelated commands stay untouched ------------------------------
    ("non-git -m untouched",          "docker run -m '2g' myimage", False),
    ("plain gh pr create",            'gh pr create --title "x" --body "y"', False),
    ("plain commit",                  "git commit -F /tmp/msg.txt", False),
]

fail = 0
for name, cmd, want in CASES:
    blocked, err = run(cmd)
    if err:
        print(f"ERROR block={blocked!s:<5} want={want!s:<5}  {name}\n       {err}")
        fail += 1
        continue
    ok = blocked == want
    fail += not ok
    print(f"{'PASS' if ok else 'FAIL'}  block={blocked!s:<5} want={want!s:<5}  {name}")

# The secret/IP guard must scan the FULL command, message included.
SECRET = "github_pat_" + "A" * 42
blocked, err = run(f'git commit -m "token {SECRET}"')
ok = blocked and not err
fail += not ok
print(f"{'PASS' if ok else 'FAIL'}  secret inside -m still caught{'  ' + err if err else ''}")

# No pathological backtracking anywhere in the remaining patterns.
for label, probe in [
    ("quote+backslash run", 'git commit -m "' + "\\" * 200),
    # the old _BLIND_FLAG had two adjacent [A-Za-z]* around the A and went
    # quadratic when the run failed the trailing boundary: 6s at 32k.
    ("long -AAAA… run", "git a" + "dd -" + "A" * 32000 + "!"),
]:
    t0 = time.time()
    _, err = run(probe)
    elapsed = time.time() - t0
    ok = elapsed < 2.0 and not err
    fail += not ok
    print(f"{'PASS' if ok else 'FAIL'}  no catastrophic backtracking: {label} ({elapsed:.2f}s)")

# --- check_git_commit_to_main, END TO END THROUGH THE ADAPTER ---------------
# 🔴 The other cases in this file are pure text shapes. This one is not: the check
# answers a question about the WORLD (what branch is this repo on?), so its
# adapter-level coverage has to build real repos and drive the real PreToolUse
# `cwd` field. Verified separately that the CHECK is reachable — no earlier check
# in the claude-code policy fires on a plain `git commit` (test_guard_core.py::
# test_commit_to_main_is_the_ONLY_check_that_fires_on_a_plain_commit), so a DENY
# here is attributable to this check and not to a neighbour.
_repo_root = tempfile.mkdtemp(prefix="bash-guard-repos-")


def _mkrepo(name, branch, remote="git@github.com:someone/thing.git"):
    path = os.path.join(_repo_root, name)
    subprocess.run(["git", "init", "-q", "-b", branch, path],
                   capture_output=True, check=True)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t")):
        subprocess.run(["git", "-C", path, "config", k, v], capture_output=True, check=True)
    if remote:
        subprocess.run(["git", "-C", path, "remote", "add", "origin", remote],
                       capture_output=True, check=True)
    return path


if shutil.which("git"):
    ON_MAIN = _mkrepo("on-main", "main")
    ON_FEAT = _mkrepo("on-feat", "feat/x")
    ON_TRUNK_OK = _mkrepo("homelab-trunk", "trunk",
                          "git@github.com:ZacxDev/homelab-infra.git")
    SCRATCH = _mkrepo("scratch", "main", remote=None)

    BRANCH_CASES = [
        # (name, cmd, payload cwd, want_blocked)
        ("commit on main is denied",       'git commit -m "wip"',   ON_MAIN,     True),
        ("commit --amend on main denied",  "git commit --amend",    ON_MAIN,     True),
        ("commit on a feature branch ok",  'git commit -m "wip"',   ON_FEAT,     False),
        ("commit --dry-run on main ok",    "git commit --dry-run",  ON_MAIN,     False),
        # the allowlist, matched by REMOTE — the directory here is `homelab-trunk`,
        # which is in NO allowlist entry; only `homelab-infra` (its remote) is.
        ("allowlisted trunk deploy ok",    'git commit -m "deploy"', ON_TRUNK_OK, False),
        ("no-remote scratch repo ok",      'git commit -m "x"',     SCRATCH,     False),
        # the -C hop must beat the payload cwd in BOTH directions
        ("-C into main from a feat cwd",   f"git -C {ON_MAIN} commit -m x", ON_FEAT, True),
        ("-C into feat from a main cwd",   f"git -C {ON_FEAT} commit -m x", ON_MAIN, False),
        ("git status on main untouched",   "git status",            ON_MAIN,     False),
    ]
    for name, cmd, cwd, want in BRANCH_CASES:
        blocked, err = run(cmd, cwd=cwd)
        if err:
            print(f"ERROR block={blocked!s:<5} want={want!s:<5}  {name}\n       {err}")
            fail += 1
            continue
        ok = blocked == want
        fail += not ok
        print(f"{'PASS' if ok else 'FAIL'}  block={blocked!s:<5} want={want!s:<5}  {name}")
else:
    # 🔴 FAIL, not skip. This suite's whole job is to be the adapter's regression
    # net; silently dropping nine cases because a binary is missing is how a gate
    # reports safety it does not have. run-tests.sh asserts `git` is on PATH.
    print("FAIL  git is not on PATH — the commit-to-main cases could not run")
    fail += 1

# `pkill -f` through the adapter (pure text, no repo needed).
for name, cmd, want in [
    ("pkill -f is denied",       "pkill -f e2e/run.sh", True),
    ("pkill -9f bundled denied",  "pkill -9f e2e/run.sh", True),
    ("pgrep -f stays allowed",   "pgrep -f e2e/run.sh", False),
    ("pkill by name allowed",    "pkill firefox", False),
]:
    blocked, err = run(cmd)
    ok = blocked == want and not err
    fail += not ok
    print(f"{'PASS' if ok else 'FAIL'}  block={blocked!s:<5} want={want!s:<5}  {name}")

shutil.rmtree(NEUTRAL_CWD, ignore_errors=True)
shutil.rmtree(_repo_root, ignore_errors=True)

print("\nRESULT:", "all good" if not fail else f"{fail} failure(s)")
sys.exit(1 if fail else 0)
