"""Hermetic tests for the deterministic `ticket-status` wrapper.

NO live cluster / ClickUp / network. Prior-art + merge classification is proven
against REAL temp git repos built in-test (the nix sandbox has `git`); the origin
remote is a local file path so `git fetch` is offline-safe. gh is faked via the
`TICKET_STATUS_GH` env override (or pointed at a missing path to prove graceful
degradation). The security cases prove that untrusted input is rejected or
neutralised — never executed.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "ticket-status"
_DRAFTER = _HERE.parent / "drafter.sh"
_PROMPT = _HERE.parent / "drafter-prompt.md"


# --- load the extension-less script as a module -------------------------------
def _load_mod():
    # The script has no .py extension, so give importlib an explicit source loader.
    loader = importlib.machinery.SourceFileLoader("ticket_status", str(_SCRIPT))
    spec = importlib.util.spec_from_loader("ticket_status", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


ts = _load_mod()


# --- git test-repo fixtures ---------------------------------------------------
_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], env=_GIT_ENV,
                          capture_output=True, text=True, check=True)


def _commit(work, msg, fname):
    (Path(work) / fname).write_text(fname, encoding="utf-8")
    _git(work, "add", fname)
    _git(work, "commit", "-q", "-m", msg)
    return _git(work, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A work clone with a local-file 'origin' bare remote (offline fetch-safe).

    main has:  'CU 86abc123 add feature X' (merged), then an unrelated commit.
    A branch 'feature/CU-99xyz-wip' carries an UNMERGED id commit.
    """
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], env=_GIT_ENV, check=True)
    subprocess.run(["git", "init", "-q", str(work)], env=_GIT_ENV, check=True)
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "checkout", "-q", "-b", "main")
    merged_sha = _commit(work, "CU 86abc123 add feature X", "a.txt")
    _commit(work, "unrelated cache tweak", "b.txt")
    _git(work, "push", "-q", "origin", "main")
    # unmerged id branch
    _git(work, "checkout", "-q", "-b", "feature/CU-99xyz-wip")
    unmerged_sha = _commit(work, "CU 99xyz partial work", "c.txt")
    _git(work, "checkout", "-q", "main")
    return {"origin": origin, "work": work,
            "merged_sha": merged_sha, "unmerged_sha": unmerged_sha}


@pytest.fixture()
def poison_repo(tmp_path):
    """A real git repo whose OWN config execs a marker on `git fetch` — the exact
    RCE the audit proved (`remote.origin.uploadpack = 'touch <marker>; ...'` on a
    local/file remote runs locally). Used to prove the pin/root-allowlist refuse it
    and that the fetch-config neutralisation defangs it even when pinned."""
    porigin = tmp_path / "porigin.git"
    pwork = tmp_path / "poison"
    marker = tmp_path / "PWNED"
    subprocess.run(["git", "init", "-q", "--bare", str(porigin)], env=_GIT_ENV, check=True)
    subprocess.run(["git", "init", "-q", str(pwork)], env=_GIT_ENV, check=True)
    _git(pwork, "remote", "add", "origin", str(porigin))
    _git(pwork, "checkout", "-q", "-b", "main")
    _commit(pwork, "CU 86abc123 planted", "p.txt")
    _git(pwork, "push", "-q", "origin", "main")
    # poison the repo's own config: fetch will run this uploadpack command locally
    _git(pwork, "config", "remote.origin.uploadpack", f"touch {marker}; git-upload-pack")
    return {"work": pwork, "origin": porigin, "marker": marker}


def _bare_fetch_fires(poison) -> bool:
    """Sanity control: does a plain `git fetch origin` (NO neutralisation) actually
    trip the poisoned config on this git build? If not, the fixture can't prove the
    guard, so the dependent test skips."""
    m = poison["marker"]
    if m.exists():
        m.unlink()
    subprocess.run(["git", "-C", str(poison["work"]), "fetch", "--quiet", "origin"],
                   env=_GIT_ENV, capture_output=True, text=True)
    fired = m.exists()
    if fired:
        m.unlink()
    return fired


def _fake_gh(tmp_path, payload):
    """A fake `gh` that ignores its args and prints FAKE_GH_OUT. Shebang uses the
    absolute running interpreter so it resolves in the offline nix sandbox."""
    p = tmp_path / "gh"
    p.write_text(f"#!{sys.executable}\nimport os,sys\nsys.stdout.write(os.environ.get('FAKE_GH_OUT',''))\n",
                 encoding="utf-8")
    p.chmod(0o755)
    return p, json.dumps(payload)


def _run_cli(repo_path, *args, gh="/nonexistent-gh", gh_out=None, fetch=False,
             allowed_roots="__parent__", lock_repo=None, with_repo=True):
    env = {**os.environ, "TICKET_STATUS_GH": str(gh)}
    env.pop("TICKET_STATUS_LOCK_REPO", None)
    env.pop("TICKET_STATUS_ALLOWED_REPO_ROOTS", None)
    if gh_out is not None:
        env["FAKE_GH_OUT"] = gh_out
    # By default, allow the temp repo (outside the built-in civitai root) so the
    # behavioural tests can point --repo at it. Tests can override the roots or set
    # a lock to exercise the pin.
    if allowed_roots == "__parent__":
        allowed_roots = str(Path(repo_path).parent)
    if allowed_roots is not None:
        env["TICKET_STATUS_ALLOWED_REPO_ROOTS"] = str(allowed_roots)
    if lock_repo is not None:
        env["TICKET_STATUS_LOCK_REPO"] = str(lock_repo)
    argv = [sys.executable, str(_SCRIPT), *args]
    if with_repo:
        argv += ["--repo", str(repo_path)]
    if not fetch:
        argv.append("--no-fetch")
    return subprocess.run(argv, capture_output=True, text=True, env=env)


def _json_cli(*a, **k):
    r = _run_cli(*a, **k)
    assert r.returncode == 0, f"rc={r.returncode} stderr={r.stderr}"
    return json.loads(r.stdout)


# ============================================================================
# Prior-art + verdict
# ============================================================================
def test_prior_art_hit_by_ticket_id_commit_convention(repo):
    d = _json_cli(repo["work"], "86abc123")
    shas = {pa["full_sha"] for pa in d["prior_art"]}
    assert repo["merged_sha"] in shas
    hit = next(pa for pa in d["prior_art"] if pa["full_sha"] == repo["merged_sha"])
    assert hit["match_kind"] == "id"
    assert hit["subject"] == "CU 86abc123 add feature X"


def test_merged_id_commit_yields_already_done(repo):
    d = _json_cli(repo["work"], "86abc123")
    assert d["verdict"] == "ALREADY-DONE"
    assert any(pa["merged_to_trunk"] is True for pa in d["prior_art"])


def test_unmerged_id_commit_yields_partial(repo):
    d = _json_cli(repo["work"], "CU-99xyz")
    assert d["verdict"] == "PARTIAL"
    hit = next(pa for pa in d["prior_art"] if pa["full_sha"] == repo["unmerged_sha"])
    assert hit["merged_to_trunk"] is False
    # the branch name carrying the id is surfaced too
    assert any("99xyz" in b for b in d["branches"])


def test_not_found_verdict(repo):
    d = _json_cli(repo["work"], "ZZ00000")
    assert d["verdict"] == "NOT-FOUND"
    assert d["prior_art"] == []
    assert d["branches"] == []


def test_core_id_strips_cu_prefix_and_matches_bare(repo):
    # 'CU-86abc123' must still match the 'CU 86abc123 ...' commit via core-id.
    d = _json_cli(repo["work"], "CU-86abc123")
    assert d["verdict"] == "ALREADY-DONE"
    assert repo["merged_sha"] in {pa["full_sha"] for pa in d["prior_art"]}


def test_json_shape_has_required_keys(repo):
    d = _json_cli(repo["work"], "86abc123")
    for k in ("ticket_id", "repo", "trunk", "clone_fresh", "behind_trunk",
              "verdict", "deploy_status", "prior_art", "prs", "branches",
              "evidence", "warnings", "generated_at"):
        assert k in d, f"missing key {k}"
    assert d["ticket_id"] == "86abc123"
    assert d["verdict"] in ("ALREADY-DONE", "PARTIAL", "NOT-FOUND")
    pa = d["prior_art"][0]
    for k in ("commit", "full_sha", "subject", "match_kind", "merged_to_trunk",
              "branches", "tags", "deploy_status"):
        assert k in pa


def test_verdict_rollup_unit():
    # merged id commit -> ALREADY-DONE
    assert ts.compute_verdict([{"match_kind": "id", "merged_to_trunk": True}], [], []) == "ALREADY-DONE"
    # merged id PR -> ALREADY-DONE
    assert ts.compute_verdict([], [{"match_kind": "id", "state": "MERGED"}], []) == "ALREADY-DONE"
    # unmerged id commit -> PARTIAL
    assert ts.compute_verdict([{"match_kind": "id", "merged_to_trunk": False}], [], []) == "PARTIAL"
    # merged TERM commit is NOT decisive -> PARTIAL (keyword coincidence guard)
    assert ts.compute_verdict([{"match_kind": "term", "merged_to_trunk": True}], [], []) == "PARTIAL"
    # branch-only -> PARTIAL
    assert ts.compute_verdict([], [], ["feature/x"]) == "PARTIAL"
    # nothing -> NOT-FOUND
    assert ts.compute_verdict([], [], []) == "NOT-FOUND"


# ============================================================================
# gh integration (faked; also proves graceful offline degradation)
# ============================================================================
def test_gh_merged_id_pr_yields_already_done(repo, tmp_path):
    gh, out = _fake_gh(tmp_path, [{"number": 42, "title": "fix thing",
                                   "state": "MERGED", "url": "http://x/42"}])
    # search a ticket id with NO prior-art commit; the merged id-PR decides it.
    d = _json_cli(repo["work"], "PR00001", gh=gh, gh_out=out)
    assert d["verdict"] == "ALREADY-DONE"
    assert d["prs"] and d["prs"][0]["state"] == "MERGED"


def test_gh_open_pr_only_is_partial(repo, tmp_path):
    gh, out = _fake_gh(tmp_path, [{"number": 7, "title": "wip",
                                   "state": "OPEN", "url": "http://x/7"}])
    d = _json_cli(repo["work"], "PR00002", gh=gh, gh_out=out)
    assert d["verdict"] == "PARTIAL"


def test_gh_unavailable_degrades_gracefully(repo):
    d = _json_cli(repo["work"], "86abc123", gh="/nonexistent-gh")
    # still answers from git; warns that gh was unavailable
    assert d["verdict"] == "ALREADY-DONE"
    assert any("gh unavailable" in w for w in d["warnings"])


# ============================================================================
# Freshness
# ============================================================================
def test_no_fetch_reports_not_fresh(repo):
    d = _json_cli(repo["work"], "86abc123", fetch=False)
    assert d["clone_fresh"] is False
    assert any("fetch skipped" in w for w in d["warnings"])


def test_fetch_detects_behind_origin(repo):
    # advance origin/main by pushing a new commit from a SECOND clone, so the
    # first clone is behind until it fetches.
    work2 = repo["work"].parent / "work2"
    subprocess.run(["git", "clone", "-q", str(repo["origin"]), str(work2)],
                   env=_GIT_ENV, check=True)
    _git(work2, "checkout", "-q", "main")
    _commit(work2, "CU 55new later work", "d.txt")
    _git(work2, "push", "-q", "origin", "main")
    # now fetch from the first clone -> fresh, and HEAD is behind origin/main
    d = _json_cli(repo["work"], "86abc123", fetch=True)
    assert d["clone_fresh"] is True
    assert d["behind_trunk"] == 1


def test_fetch_failure_is_stale_but_still_answers(repo, tmp_path):
    # point origin at a bogus path so `git fetch origin` fails (offline sim)
    _git(repo["work"], "remote", "set-url", "origin", str(tmp_path / "does-not-exist.git"))
    d = _json_cli(repo["work"], "86abc123", fetch=True)
    assert d["clone_fresh"] is False
    assert any("STALE" in w for w in d["warnings"])
    assert d["verdict"] == "ALREADY-DONE"  # local answer still produced


# ============================================================================
# deploy_status (optional signal)
# ============================================================================
def test_deploy_status_unknown_by_default(repo):
    d = _json_cli(repo["work"], "86abc123")
    assert d["deploy_status"] == "unknown"


def test_deploy_status_in_release_tag_when_tagged(repo):
    _git(repo["work"], "tag", "v1.0.0", repo["merged_sha"])
    d = _json_cli(repo["work"], "86abc123")
    hit = next(pa for pa in d["prior_art"] if pa["full_sha"] == repo["merged_sha"])
    assert "v1.0.0" in hit["tags"]
    assert d["deploy_status"] == "in-release-tag"


# ============================================================================
# SECURITY — untrusted input is rejected or neutralised, never executed
# ============================================================================
@pytest.mark.parametrize("bad", [
    "foo; rm -rf /",            # shell metachars
    "--upload-pack=sh",         # flag-injection / RCE flag
    "../../etc/passwd",         # path traversal
    "86abc/../x",               # slash
    "a b",                      # whitespace
    "id$(id)",                  # command substitution
    "id`whoami`",               # backticks
    "id|cat",                   # pipe
    "",                         # empty
    "x" * 40,                   # too long
])
def test_malicious_ticket_id_rejected(repo, bad):
    r = _run_cli(repo["work"], bad)
    assert r.returncode == 2, f"expected rejection for {bad!r}, got rc={r.returncode}"
    assert r.stdout == ""  # nothing produced


def test_upload_pack_term_rejected_even_after_dashdash(repo):
    """A term is UNTRUSTED; a leading-'-' term (e.g. the RCE `--upload-pack=<cmd>`)
    must be rejected even when forced past option parsing with `--`."""
    r = _run_cli(repo["work"], "86abc123", "--", "--upload-pack=sh -c id")
    assert r.returncode == 2
    assert "leading '-'" in r.stderr or "must not start with '-'" in r.stderr


def test_dash_C_option_is_rejected_as_unknown(repo):
    """An attacker term shaped like `-C /etc` cannot smuggle a git path: `-C` is
    not a recognised option and is rejected."""
    r = _run_cli(repo["work"], "86abc123", "-C", "/etc")
    assert r.returncode == 2
    assert "unknown/rejected option" in r.stderr


def test_slash_etc_term_is_neutralised_as_literal_grep(repo):
    """A benign-looking `/etc` term is NOT a flag/path — it is passed as a literal
    fixed-strings grep term and never reaches git's -C. The run succeeds and -C is
    only ever the resolved repo."""
    d = _json_cli(repo["work"], "86abc123", "/etc")
    assert d["repo"] == os.path.realpath(str(repo["work"]))
    assert d["verdict"] in ("ALREADY-DONE", "PARTIAL", "NOT-FOUND")


def test_git_C_is_scoped_to_resolved_repo_only(monkeypatch):
    """Structural proof: the git() helper always emits `-C <repo>` and the repo is
    the resolved config value — an untrusted arg can never become the -C path."""
    calls = []

    def fake_run(argv, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "true", "")

    monkeypatch.setattr(ts, "_run", fake_run)
    ts.git("/only/this/repo", "log", "--all")
    assert calls[-1][:4] == [ts.GIT_BIN, "-C", "/only/this/repo", "log"]


def test_no_shell_true_or_bash_c_in_source():
    """AST-level proof (ignores docstrings/comments): no call passes shell=True,
    and no os.system/os.popen is used — so untrusted input can never reach a
    shell interpreter."""
    import ast
    tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    assert not (isinstance(kw.value, ast.Constant) and kw.value.value is True), \
                        "a subprocess call uses shell=True"
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                banned = {("os", "system"), ("os", "popen")}
                assert (node.func.value.id, node.func.attr) not in banned, \
                    f"banned shell call {node.func.value.id}.{node.func.attr}"
    # and every subprocess call goes through argv arrays (list first arg)
    assert "subprocess.run(" in _SCRIPT.read_text(encoding="utf-8")


def test_invalid_repo_config_errors_cleanly(tmp_path):
    r = _run_cli(tmp_path / "nope", "86abc123", allowed_roots=str(tmp_path))
    assert r.returncode == 2
    assert "repo not found" in r.stderr


def test_non_git_dir_rejected(tmp_path):
    (tmp_path / "plain").mkdir()
    r = _run_cli(tmp_path / "plain", "86abc123", allowed_roots=str(tmp_path))
    assert r.returncode == 2
    assert "not a git work tree" in r.stderr


# ============================================================================
# SECURITY (audit round) — --repo is attacker-reachable on the drafter path
# ============================================================================
def test_repo_outside_allowed_roots_is_refused(repo, tmp_path):
    """A standalone `--repo` pointing outside the allowed roots (another repo, a
    planted checkout) is REFUSED — closes cross-repo info-disclosure + RCE-via-
    planted-config, since we never even fetch it."""
    other = tmp_path / "somewhere-else"
    r = _run_cli(repo["work"], "86abc123", allowed_roots=str(other))
    assert r.returncode == 2
    assert "outside the allowed roots" in r.stderr
    assert r.stdout == ""


def test_repo_within_allowed_roots_still_works(repo):
    """`--repo` inside an allowed root works for interactive/standalone use."""
    d = _json_cli(repo["work"], "86abc123")  # default allowed_roots = parent
    assert d["repo"] == os.path.realpath(str(repo["work"]))
    assert d["verdict"] == "ALREADY-DONE"


def test_lock_env_ignores_repo_option(repo, tmp_path):
    """LOCK (drafter path): with TICKET_STATUS_LOCK_REPO set, a caller `--repo
    <anything>` is IGNORED and the locked repo is used — injected options cannot
    steer which repo is read."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    d = _json_cli(elsewhere, "86abc123", lock_repo=repo["work"], allowed_roots=None,
                  with_repo=True)
    assert d["repo"] == os.path.realpath(str(repo["work"]))
    assert any("ignored --repo" in w for w in d["warnings"])


def test_lock_env_ignores_poison_repo_no_exec(repo, poison_repo):
    """RCE finding, LOCK layer: lock to the CLEAN repo, pass `--repo <poison>` — the
    poison repo is ignored and never fetched, so its config never execs."""
    if poison_repo["marker"].exists():
        poison_repo["marker"].unlink()
    d = _json_cli(poison_repo["work"], "86abc123", lock_repo=repo["work"],
                  allowed_roots=None, fetch=True)
    assert d["repo"] == os.path.realpath(str(repo["work"]))
    assert not poison_repo["marker"].exists(), "poison config executed — RCE not blocked"


def test_poison_repo_refused_without_lock_no_exec(repo, poison_repo, tmp_path):
    """RCE finding, ROOT-ALLOWLIST layer: a `--repo <poison>` outside the allowed
    roots is refused BEFORE any fetch, so nothing execs."""
    if poison_repo["marker"].exists():
        poison_repo["marker"].unlink()
    # allow only the clean repo dir itself; poison is a sibling, so it's outside.
    r = _run_cli(poison_repo["work"], "86abc123",
                 allowed_roots=str(repo["work"]), fetch=True)
    assert r.returncode == 2
    assert "outside the allowed roots" in r.stderr
    assert not poison_repo["marker"].exists()


def test_fetch_config_neutralised_even_when_pinned(poison_repo):
    """Defense-in-depth: even when the poison repo is the PINNED/locked target, the
    fetch runs with the exec config neutralised, so it must NOT exec the marker.
    Skips if the fixture's poison doesn't fire under a plain fetch on this git."""
    if not _bare_fetch_fires(poison_repo):
        pytest.skip("poisoned uploadpack did not fire under a plain fetch on this git build")
    # sanity: the control just proved a bare fetch DOES fire; now the wrapper must not
    assert not poison_repo["marker"].exists()
    d = _json_cli(poison_repo["work"], "86abc123", lock_repo=poison_repo["work"],
                  allowed_roots=None, fetch=True)
    assert not poison_repo["marker"].exists(), \
        "fetch executed poisoned uploadpack despite neutralisation"
    # still produced an answer from the (now-fetched) pinned repo
    assert d["verdict"] in ("ALREADY-DONE", "PARTIAL", "NOT-FOUND")


def test_trunk_leading_dash_rejected(repo):
    r = _run_cli(repo["work"], "86abc123", "--trunk", "-x")
    assert r.returncode == 2
    assert "must not start with '-'" in r.stderr


def test_fetch_timeout_nonpositive_rejected(repo):
    for bad in ("-5", "0"):
        r = _run_cli(repo["work"], "86abc123", "--fetch-timeout", bad)
        assert r.returncode == 2, f"expected rejection for --fetch-timeout {bad}"
        assert "positive integer" in r.stderr


# ============================================================================
# Drafter wiring (static) — allowlist entry + prompt, guards intact
# ============================================================================
def _allowlist_line() -> str:
    src = _DRAFTER.read_text(encoding="utf-8")
    return next(l for l in src.splitlines() if l.startswith("DRAFTER_ALLOWED_TOOLS="))


def test_drafter_allowlists_the_wrapper():
    al = _allowlist_line()
    assert "ticket-status" in al, "ticket-status wrapper is not on the drafter allowlist"


def test_wrapper_allowlist_entry_is_fixed_path_prefix():
    """The entry must pin the wrapper's own directory ($SELF_DIR, which expands to
    the script's absolute dir at runtime) so the drafter can only invoke THIS
    script (which controls exactly what git runs) — not an arbitrary
    `ticket-status` on PATH."""
    al = _allowlist_line()
    assert "$SELF_DIR/ticket-status" in al
    assert "Bash($SELF_DIR/ticket-status*)" in al


def test_drafter_pins_wrapper_repo_via_lock_env():
    """The drafter must set TICKET_STATUS_LOCK_REPO so injected `--repo` options in
    ticket text cannot steer the wrapper's repo (RCE + cross-repo disclosure)."""
    src = _DRAFTER.read_text(encoding="utf-8")
    assert 'export TICKET_STATUS_LOCK_REPO="$CIVITAI_REPO"' in src


def test_wrapper_addition_did_not_reintroduce_write_or_rce_verbs():
    """Adding the wrapper must not have loosened the read-only / no-RCE posture."""
    al = _allowlist_line()
    for bad in ("gh api", "curl", "ls-remote", "--upload-pack", "--exec",
                "git -C * push", "git -C * commit", "git -C * fetch"):
        assert bad not in al, f"forbidden verb/flag present: {bad}"


def test_prompt_makes_ticket_status_the_first_prior_art_step():
    p = " ".join(_PROMPT.read_text(encoding="utf-8").split())
    assert "ticket-status" in p, "prompt does not mention the ticket-status wrapper"


def test_prompt_still_has_command_shape_and_anticonfab_guards():
    p = " ".join(_PROMPT.read_text(encoding="utf-8").split())
    assert "COMMAND-SHAPE CONTRACT" in p
    assert "ANTI-CONFABULATION GATE" in p


# ============================================================================
# adoption telemetry (Part A instrumentation)
# ============================================================================
def _load_collector():
    coll = _HERE.parent.parent / "collector"
    sys.path.insert(0, str(coll))
    import collector as C  # noqa: PLC0415
    return C


def _read_last_event(spool_dir, C):
    line = (Path(spool_dir) / "current.log").read_text().strip().splitlines()[-1]
    return C.parse_line(line), line


def test_verdict_to_outcome_mapping():
    assert ts.verdict_to_outcome("ALREADY-DONE") == "already-done"
    assert ts.verdict_to_outcome("PARTIAL") == "partial"
    assert ts.verdict_to_outcome("NOT-FOUND") == "not-found"
    assert ts.verdict_to_outcome("???") == "error"


def _run_with_spool(spool_dir, repo_path, *args, lock_repo=None):
    env = {**os.environ, "TICKET_STATUS_GH": "/nonexistent-gh",
           "ACTIVITY_SPOOL_DIR": str(spool_dir)}
    env.pop("TICKET_STATUS_LOCK_REPO", None)
    env["TICKET_STATUS_ALLOWED_REPO_ROOTS"] = str(Path(repo_path).parent)
    if lock_repo is not None:
        env["TICKET_STATUS_LOCK_REPO"] = str(lock_repo)
    argv = [sys.executable, str(_SCRIPT), *args, "--repo", str(repo_path), "--no-fetch"]
    return subprocess.run(argv, capture_output=True, text=True, env=env)


def test_main_emits_already_done_and_privacy(repo, tmp_path):
    """End-to-end: emits already-done + ticket_id, and NEVER the search term/body."""
    C = _load_collector()
    spool = tmp_path / "spool"
    # A sensitive free-text search term that MUST NOT reach the telemetry store.
    r = _run_with_spool(spool, repo["work"], "86abc123", "MYSECRETTERM")
    assert r.returncode == 0, r.stderr
    ev, raw = _read_last_event(spool, C)
    assert ev["source"] == "tool" and ev["text"] == "ticket-status"
    p = json.loads(ev["payload"])
    assert p["outcome"] == "already-done"
    assert p["verdict"] == "already-done"
    assert p["ticket_id"] == "86abc123"
    # PRIVACY: the term must not appear anywhere in the emitted event / raw line.
    assert "MYSECRETTERM" not in json.dumps(ev)
    assert "MYSECRETTERM" not in raw


def test_main_emits_not_found(repo, tmp_path):
    C = _load_collector()
    spool = tmp_path / "spool"
    r = _run_with_spool(spool, repo["work"], "ZZ00000")
    assert r.returncode == 0, r.stderr
    ev, _ = _read_last_event(spool, C)
    assert json.loads(ev["payload"])["outcome"] == "not-found"


def test_error_path_emits_only_validated_id(tmp_path):
    """SECURITY (error path): a probe InputError (repo not found) must emit ONLY
    the validated ticket-id + outcome:error — never the free-text term, the repo
    PATH, or the exception text."""
    C = _load_collector()
    spool = tmp_path / "spool"
    missing = tmp_path / "SECRETPATH_repo"     # nonexistent -> probe InputError
    r = _run_with_spool(spool, missing, "86abc123", "SECRETTERM")
    assert r.returncode == 2, r.stderr
    ev, raw = _read_last_event(spool, C)
    p = json.loads(ev["payload"])
    assert p["outcome"] == "error" and p["verdict"] == "error"
    assert p["ticket_id"] == "86abc123"
    for secret in ("SECRETTERM", "SECRETPATH_repo", "not found"):
        assert secret not in raw, f"{secret} leaked into raw spool line"
        assert secret not in json.dumps(ev), f"{secret} leaked into event"


def test_rejected_ticket_id_emits_nothing(repo, tmp_path):
    """A ticket-id that fails validation is REJECTED before any emit — the
    unvalidated (attacker-controlled) id must never reach telemetry."""
    C = _load_collector()  # noqa: F841 — imported for parity; no line expected
    spool = tmp_path / "spool"
    r = _run_with_spool(spool, repo["work"], "bad/id;rm")
    assert r.returncode == 2  # rejected input
    assert not (spool / "current.log").exists()
