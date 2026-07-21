"""Unit tests for scripts/verify-agent-work — the post-agent verification gate.

All HERMETIC — temp dirs / fixture git repos, no network, no cluster, no real
build. The gate's subprocess-running layer (`run_gate`) is bypassed by
injecting a fake `gate_runner` into `verify()`, so no toolchain is required.

The script is extensionless, so it is loaded via SourceFileLoader.

    run:  pytest scripts/tests/test_verify_agent_work.py
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]


def _load(name, modname):
    loader = importlib.machinery.SourceFileLoader(modname, str(SCRIPTS / name))
    spec = importlib.util.spec_from_loader(modname, loader)
    mod = importlib.util.module_from_spec(spec)
    # register before exec so @dataclass (with `from __future__ import
    # annotations`) can resolve the module via sys.modules on py>=3.12.
    sys.modules[modname] = mod
    loader.exec_module(mod)
    return mod


vaw = _load("verify-agent-work", "verify_agent_work")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def write(path: Path, content: str = "{}"):
    path.write_text(content)


def git(repo: Path, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def init_repo(repo: Path):
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t.dev")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "commit.gpgsign", "false")


def commit_all(repo: Path, msg="c"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


# --------------------------------------------------------------------------- #
# stack detection
# --------------------------------------------------------------------------- #


def test_detect_ts_stack(tmp_path):
    write(tmp_path / "package.json", json.dumps({"scripts": {"test": "jest"}}))
    stacks = vaw.detect_stacks(tmp_path)
    assert set(stacks) == {"ts"}
    assert stacks["ts"]["scripts"] == {"test": "jest"}


def test_detect_go_stack(tmp_path):
    write(tmp_path / "go.mod", "module x\n\ngo 1.22\n")
    assert set(vaw.detect_stacks(tmp_path)) == {"go"}


def test_detect_python_stack(tmp_path):
    write(tmp_path / "pyproject.toml", "[tool.ruff]\n[tool.pytest.ini_options]\n")
    stacks = vaw.detect_stacks(tmp_path)
    assert set(stacks) == {"python"}
    assert stacks["python"]["has_ruff"] is True
    assert stacks["python"]["has_pytest"] is True


def test_detect_nix_stack(tmp_path):
    write(tmp_path / "flake.nix", "{ }\n")
    assert set(vaw.detect_stacks(tmp_path)) == {"nix"}


def test_detect_mixed_stack(tmp_path):
    write(tmp_path / "package.json", "{}")
    write(tmp_path / "go.mod", "module x\n")
    write(tmp_path / "flake.nix", "{ }\n")
    assert set(vaw.detect_stacks(tmp_path)) == {"ts", "go", "nix"}


def test_detect_no_stack(tmp_path):
    (tmp_path / "README.md").write_text("# hi")
    assert vaw.detect_stacks(tmp_path) == {}


def test_expects_node_modules_flag(tmp_path):
    write(tmp_path / "package.json", json.dumps({"dependencies": {"left-pad": "1"}}))
    assert vaw.detect_stacks(tmp_path)["ts"]["expects_node_modules"] is True
    write(tmp_path / "package.json", json.dumps({"name": "x"}))
    assert vaw.detect_stacks(tmp_path)["ts"]["expects_node_modules"] is False


@pytest.mark.parametrize(
    "lockfile,expected",
    [
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("package-lock.json", "npm"),
        ("bun.lockb", "bun"),
    ],
)
def test_detect_package_manager(tmp_path, lockfile, expected):
    write(tmp_path / lockfile, "")
    assert vaw.detect_package_manager(tmp_path) == expected


def test_detect_package_manager_default_npm(tmp_path):
    assert vaw.detect_package_manager(tmp_path) == "npm"


# --------------------------------------------------------------------------- #
# node_modules footgun
# --------------------------------------------------------------------------- #


def test_node_modules_missing_warns(tmp_path):
    write(tmp_path / "package.json", json.dumps({"dependencies": {"x": "1"}}))
    meta = vaw.detect_stacks(tmp_path)["ts"]
    chk = vaw.check_node_modules(tmp_path, meta)
    assert chk is not None
    assert chk.status == vaw.WARN
    assert "absent" in chk.summary


def test_node_modules_broken_symlink_warns(tmp_path):
    write(tmp_path / "package.json", json.dumps({"dependencies": {"x": "1"}}))
    (tmp_path / "node_modules").symlink_to(tmp_path / "does-not-exist")
    meta = vaw.detect_stacks(tmp_path)["ts"]
    chk = vaw.check_node_modules(tmp_path, meta)
    assert chk is not None
    assert chk.status == vaw.WARN
    assert "BROKEN symlink" in chk.summary


def test_node_modules_present_ok(tmp_path):
    write(tmp_path / "package.json", json.dumps({"dependencies": {"x": "1"}}))
    (tmp_path / "node_modules").mkdir()
    meta = vaw.detect_stacks(tmp_path)["ts"]
    assert vaw.check_node_modules(tmp_path, meta) is None


def test_node_modules_not_required_when_no_deps(tmp_path):
    write(tmp_path / "package.json", json.dumps({"name": "x"}))
    meta = vaw.detect_stacks(tmp_path)["ts"]
    assert vaw.check_node_modules(tmp_path, meta) is None


# --------------------------------------------------------------------------- #
# git state
# --------------------------------------------------------------------------- #


def test_git_state_not_a_repo(tmp_path):
    assert vaw.git_state(tmp_path) == {"is_repo": False}


def test_git_state_clean(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")
    commit_all(tmp_path)
    st = vaw.git_state(tmp_path)
    assert st["is_repo"] is True
    assert st["dirty"] is False
    assert st["branch"] == "main"


def test_git_state_dirty_untracked(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")
    commit_all(tmp_path)
    write(tmp_path / "new.txt", "x")  # untracked
    st = vaw.git_state(tmp_path)
    assert st["dirty"] is True
    assert st["untracked"] == 1
    assert st["tracked_changes"] == 0


def test_git_state_dirty_modified(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")
    commit_all(tmp_path)
    write(tmp_path / "f.txt", "changed")  # tracked modification
    st = vaw.git_state(tmp_path)
    assert st["dirty"] is True
    assert st["tracked_changes"] == 1


def test_git_state_ahead_of_upstream(tmp_path):
    # bare "remote" + a clone, commit ahead → ahead==1.
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "-q", "--bare", "-b", "main")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    git(work, "config", "user.email", "t@t.dev")
    git(work, "config", "user.name", "t")
    git(work, "config", "commit.gpgsign", "false")
    write(work / "a.txt", "1")
    commit_all(work)
    git(work, "push", "-q", "-u", "origin", "main")
    write(work / "b.txt", "2")
    commit_all(work)  # local commit, not pushed
    st = vaw.git_state(work)
    assert st["upstream"] is not None
    assert st["ahead"] == 1
    assert st["behind"] == 0


def test_git_state_no_upstream_has_commits(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")
    commit_all(tmp_path)
    st = vaw.git_state(tmp_path)
    assert st["upstream"] is None
    assert st["has_commits"] is True


# --------------------------------------------------------------------------- #
# check_git verdicts
# --------------------------------------------------------------------------- #


def _by_name(checks):
    return {c.name: c for c in checks}


def test_check_git_clean_passes(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")
    commit_all(tmp_path)
    checks = _by_name(vaw.check_git(vaw.git_state(tmp_path), tmp_path, use_gh=False))
    assert checks["git:clean-tree"].status == vaw.PASS


def test_check_git_dirty_warns(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")  # uncommitted
    checks = _by_name(vaw.check_git(vaw.git_state(tmp_path), tmp_path, use_gh=False))
    assert checks["git:clean-tree"].status == vaw.WARN
    assert checks["git:pushed"].status == vaw.SKIP  # no commits yet


def test_check_git_no_upstream_warns(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")
    commit_all(tmp_path)
    checks = _by_name(vaw.check_git(vaw.git_state(tmp_path), tmp_path, use_gh=False))
    assert checks["git:pushed"].status == vaw.WARN  # committed but not pushed


# --------------------------------------------------------------------------- #
# gate plan
# --------------------------------------------------------------------------- #


def _plan_names(specs):
    return [s.name for s in specs]


def test_plan_ts_typecheck_and_test(tmp_path):
    write(
        tmp_path / "package.json",
        json.dumps({"scripts": {"typecheck": "tsc --noEmit", "test": "jest"}}),
    )
    write(tmp_path / "pnpm-lock.yaml", "")
    stacks = vaw.detect_stacks(tmp_path)
    specs, skips = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    names = _plan_names(specs)
    assert "ts:typecheck" in names
    assert "ts:test" in names
    tc = next(s for s in specs if s.name == "ts:typecheck")
    assert tc.cmd == ["pnpm", "run", "typecheck"]


def test_plan_ts_tsc_fallback_when_no_script(tmp_path):
    write(tmp_path / "package.json", json.dumps({"dependencies": {"x": "1"}}))
    write(tmp_path / "tsconfig.json", "{}")
    (tmp_path / "node_modules").mkdir()
    stacks = vaw.detect_stacks(tmp_path)
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    tc = next(s for s in specs if s.name == "ts:typecheck")
    assert tc.cmd == ["npx", "--no-install", "tsc", "--noEmit"]
    # a runnable gate (typecheck) resolved → stack is NOT incomplete even though
    # there's no test script.
    assert not any(c.status == vaw.INCOMPLETE for c in aux)


def test_plan_ts_no_runnable_gate_is_incomplete(tmp_path):
    # package.json with deps but NO typecheck/test script and NO tsconfig →
    # nothing authoritative to run → INCOMPLETE, never a silent green.
    write(tmp_path / "package.json", json.dumps({"dependencies": {"x": "1"}}))
    (tmp_path / "node_modules").mkdir()
    stacks = vaw.detect_stacks(tmp_path)
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    assert _plan_names(specs) == []
    ts_aux = [c for c in aux if c.name == "ts"]
    assert ts_aux and ts_aux[0].status == vaw.INCOMPLETE


def test_plan_ts_incomplete_when_node_not_ok(tmp_path):
    write(tmp_path / "package.json", json.dumps({"scripts": {"typecheck": "tsc"}}))
    stacks = vaw.detect_stacks(tmp_path)
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=False)
    assert _plan_names(specs) == []  # nothing runs
    ts_aux = [c for c in aux if c.name == "ts"]
    assert ts_aux and ts_aux[0].status == vaw.INCOMPLETE


def test_plan_monorepo_no_root_script_resolves_members(tmp_path):
    # workspace root with NO root typecheck/test script → fan out to members.
    write(
        tmp_path / "package.json",
        json.dumps({"private": True, "workspaces": ["packages/*"]}),
    )
    pkg_a = tmp_path / "packages" / "a"
    pkg_a.mkdir(parents=True)
    write(pkg_a / "package.json", json.dumps({"scripts": {"typecheck": "tsc"}}))
    stacks = vaw.detect_stacks(tmp_path)
    assert stacks["ts"]["is_workspace_root"] is True
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    names = _plan_names(specs)
    assert any(n.startswith("ts:typecheck[a]") for n in names)
    assert not any(c.status == vaw.INCOMPLETE for c in aux)


def test_plan_monorepo_no_runnable_member_is_incomplete(tmp_path):
    # workspace root, members have no scripts/tsconfig → nothing runs → INCOMPLETE.
    write(
        tmp_path / "package.json",
        json.dumps({"private": True, "workspaces": ["packages/*"]}),
    )
    pkg_a = tmp_path / "packages" / "a"
    pkg_a.mkdir(parents=True)
    write(pkg_a / "package.json", json.dumps({"name": "a"}))
    stacks = vaw.detect_stacks(tmp_path)
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    assert _plan_names(specs) == []
    assert any(c.name == "ts" and c.status == vaw.INCOMPLETE for c in aux)


def test_plan_pnpm_workspace_detected(tmp_path):
    write(tmp_path / "package.json", json.dumps({"private": True}))
    write(tmp_path / "pnpm-workspace.yaml", "packages:\n  - 'apps/*'\n  - 'libs/*'\n")
    globs = vaw.detect_stacks(tmp_path)["ts"]["workspace_globs"]
    assert "apps/*" in globs and "libs/*" in globs


def test_plan_go_race_when_cgo(tmp_path):
    write(tmp_path / "go.mod", "module x\n")
    stacks = vaw.detect_stacks(tmp_path)
    # force cgo/race ON via config so the assertion is deterministic
    specs, _ = vaw.build_gate_plan(tmp_path, stacks, {"go": {"race": True}}, node_ok=True)
    assert _plan_names(specs) == ["go:build", "go:vet", "go:test"]
    test_spec = next(s for s in specs if s.name == "go:test")
    assert "go test -race ./..." in " ".join(test_spec.cmd)


def test_plan_go_no_race_when_cgo_unavailable(tmp_path):
    write(tmp_path / "go.mod", "module x\n")
    stacks = vaw.detect_stacks(tmp_path)
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {"go": {"race": False}}, node_ok=True)
    test_spec = next(s for s in specs if s.name == "go:test")
    cmd = " ".join(test_spec.cmd)
    assert "-race" not in cmd
    assert "go test ./..." in cmd
    assert any(c.name == "go:race" and c.status == vaw.SKIP for c in aux)


def test_plan_nested_go_modules(tmp_path):
    write(tmp_path / "go.mod", "module root\n")
    nested = tmp_path / "sub" / "svc"
    nested.mkdir(parents=True)
    write(nested / "go.mod", "module svc\n")
    stacks = vaw.detect_stacks(tmp_path)
    specs, _ = vaw.build_gate_plan(tmp_path, stacks, {"go": {"race": True}}, node_ok=True)
    names = _plan_names(specs)
    assert "go:build" in names  # root module
    assert any("[sub/svc]" in n for n in names)  # nested module covered


def test_plan_nix_default_parse(tmp_path):
    write(tmp_path / "flake.nix", "{ }\n")
    stacks = vaw.detect_stacks(tmp_path)
    specs, _ = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    assert _plan_names(specs) == ["nix:parse"]
    assert specs[0].cmd[0] == "nix-instantiate"


def test_plan_config_override_and_skip(tmp_path):
    write(tmp_path / "flake.nix", "{ }\n")
    write(tmp_path / "go.mod", "module x\n")
    cfg = {"skip": ["go"], "nix": {"check": "nix flake check"}}
    stacks = vaw.detect_stacks(tmp_path)
    specs, _ = vaw.build_gate_plan(tmp_path, stacks, cfg, node_ok=True)
    names = _plan_names(specs)
    assert "nix:check" in names  # override applied
    assert not any(n.startswith("go:") for n in names)  # go skipped


def test_plan_python(tmp_path):
    write(tmp_path / "pyproject.toml", "[tool.ruff]\n[tool.pytest.ini_options]\n")
    stacks = vaw.detect_stacks(tmp_path)
    specs, _ = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    names = _plan_names(specs)
    assert "python:lint" in names
    assert "python:test" in names


# --------------------------------------------------------------------------- #
# config loading
# --------------------------------------------------------------------------- #


def test_load_config_present(tmp_path):
    write(tmp_path / ".verify-agent.json", json.dumps({"skip": ["nix"]}))
    assert vaw.load_config(tmp_path) == {"skip": ["nix"]}


def test_load_config_absent(tmp_path):
    assert vaw.load_config(tmp_path) == {}


def test_load_config_malformed(tmp_path):
    write(tmp_path / ".verify-agent.json", "not json{")
    assert vaw.load_config(tmp_path) == {}


# --------------------------------------------------------------------------- #
# output truncation
# --------------------------------------------------------------------------- #


def test_truncate_keeps_tail(tmp_path):
    text = "\n".join(str(i) for i in range(100))
    out = vaw._truncate(text, max_lines=10)
    assert "truncated" in out
    assert out.strip().endswith("99")
    assert "0\n" not in out.split("truncated")[1][:5]  # early lines gone


def test_truncate_short_untouched():
    assert vaw._truncate("a\nb\nc", max_lines=10) == "a\nb\nc"


# --------------------------------------------------------------------------- #
# end-to-end verify() with an injected fake gate runner (no real build)
# --------------------------------------------------------------------------- #


def _fake_runner(results_by_name):
    def runner(spec, timeout):
        status = results_by_name.get(spec.name, vaw.PASS)
        return vaw.Check(spec.name, status, f"fake {spec.name}")
    return runner


def test_verify_all_pass_exit_zero(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "go.mod", "module x\n")
    commit_all(tmp_path)
    # detach upstream noise: no upstream → git:pushed WARN, but WARN != fail
    res = vaw.verify(
        tmp_path, use_gh=False, gate_runner=_fake_runner({})
    )
    assert res.verdict == vaw.PASS
    assert res.exit_code == 0
    names = {c.name for c in res.checks}
    assert {"go:build", "go:vet", "go:test"} <= names


def test_verify_gate_fail_exit_one(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "go.mod", "module x\n")
    commit_all(tmp_path)
    res = vaw.verify(
        tmp_path, use_gh=False, gate_runner=_fake_runner({"go:test": vaw.FAIL})
    )
    assert res.verdict == vaw.FAIL
    assert res.exit_code == 1


def test_verify_warn_not_fail_by_default(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")  # dirty → WARN
    res = vaw.verify(tmp_path, use_gh=False, gate_runner=_fake_runner({}))
    assert any(c.status == vaw.WARN for c in res.checks)
    assert res.exit_code == 0  # WARN alone does not fail


def test_verify_strict_promotes_warn(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "f.txt", "hi")  # dirty → WARN
    res = vaw.verify(
        tmp_path, use_gh=False, strict=True, gate_runner=_fake_runner({})
    )
    assert res.exit_code == 1  # strict promotes WARN


def test_verify_node_modules_footgun_is_incomplete_nonzero(tmp_path):
    # broken node_modules for a DETECTED TS stack: the typecheck genuinely did
    # not run → INCOMPLETE + non-zero, NOT a silent green. The env WARN explains
    # WHY (attributes a 'cannot find module' flood to env, not code).
    init_repo(tmp_path)
    write(tmp_path / "package.json", json.dumps({"dependencies": {"x": "1"}}))
    commit_all(tmp_path)
    res = vaw.verify(tmp_path, use_gh=False, gate_runner=_fake_runner({}))
    by = _by_name(res.checks)
    assert by["env:node_modules"].status == vaw.WARN
    assert by["ts"].status == vaw.INCOMPLETE
    assert res.verdict == vaw.INCOMPLETE
    assert res.exit_code == 1


def test_verify_incomplete_makes_nonzero(tmp_path):
    # a detected stack whose gate returns INCOMPLETE (e.g. missing toolchain)
    # must never count as an overall PASS.
    init_repo(tmp_path)
    write(tmp_path / "go.mod", "module x\n")
    commit_all(tmp_path)
    res = vaw.verify(
        tmp_path, use_gh=False, gate_runner=_fake_runner({"go:build": vaw.INCOMPLETE})
    )
    assert res.verdict == vaw.INCOMPLETE
    assert res.exit_code == 1


def test_verify_no_stack_present_is_pass(tmp_path):
    # the legitimate PASS: nothing to verify → PASS / exit 0.
    init_repo(tmp_path)
    write(tmp_path / "README.md", "hi")
    commit_all(tmp_path)
    res = vaw.verify(tmp_path, use_gh=False, gate_runner=_fake_runner({}))
    assert res.stacks == []
    assert any(c.name == "git:clean-tree" for c in res.checks)
    assert res.verdict == vaw.PASS
    assert res.exit_code == 0


# --------------------------------------------------------------------------- #
# run_gate: missing-toolchain / exit-127 → INCOMPLETE (real subprocess)
# --------------------------------------------------------------------------- #


def test_run_gate_pass_real(tmp_path):
    spec = vaw.GateSpec("probe", ["true"], tmp_path, tool="true")
    chk = vaw.run_gate(spec)
    assert chk.status == vaw.PASS


def test_run_gate_fail_real(tmp_path):
    spec = vaw.GateSpec("probe", ["bash", "-c", "echo boom >&2; exit 3"], tmp_path, tool="bash")
    chk = vaw.run_gate(spec)
    assert chk.status == vaw.FAIL
    assert "boom" in chk.output


def test_run_gate_missing_tool_is_incomplete(tmp_path):
    spec = vaw.GateSpec("ts:typecheck", ["pnpm", "run", "typecheck"], tmp_path,
                        tool="definitely-not-a-real-tool-xyz")
    chk = vaw.run_gate(spec)
    assert chk.status == vaw.INCOMPLETE
    assert "not found" in chk.summary


def test_run_gate_exit127_is_incomplete(tmp_path):
    # inner tool missing under a `bash -c` wrapper (tool=bash exists) → 127.
    spec = vaw.GateSpec("go:build", ["bash", "-c", "definitely-not-a-real-tool-xyz build"],
                        tmp_path, tool="bash")
    chk = vaw.run_gate(spec)
    assert chk.status == vaw.INCOMPLETE


def test_python_no_gate_is_incomplete(tmp_path):
    # requirements.txt but no ruff, no pytest config, no tests dir → INCOMPLETE.
    write(tmp_path / "requirements.txt", "requests\n")
    stacks = vaw.detect_stacks(tmp_path)
    specs, aux = vaw.build_gate_plan(tmp_path, stacks, {}, node_ok=True)
    assert _plan_names(specs) == []
    assert any(c.name == "python" and c.status == vaw.INCOMPLETE for c in aux)


# --------------------------------------------------------------------------- #
# JSON output shape + exit codes
# --------------------------------------------------------------------------- #


def test_json_shape(tmp_path):
    init_repo(tmp_path)
    write(tmp_path / "go.mod", "module x\n")
    commit_all(tmp_path)
    res = vaw.verify(tmp_path, use_gh=False, gate_runner=_fake_runner({"go:build": vaw.FAIL}))
    d = res.to_dict()
    assert set(d) >= {"target", "verdict", "exit_code", "stacks", "checks"}
    assert d["verdict"] == vaw.FAIL
    assert d["exit_code"] == 1
    assert isinstance(d["checks"], list)
    for c in d["checks"]:
        assert "name" in c and "status" in c and "summary" in c
    # round-trips through json
    assert json.loads(json.dumps(d))["verdict"] == vaw.FAIL


def test_main_bad_target_exit_two(capsys):
    rc = vaw.main(["/nonexistent/path/xyz", "--no-gh"])
    assert rc == 2


def test_main_json_smoke(tmp_path, capsys):
    init_repo(tmp_path)
    write(tmp_path / "README.md", "hi")
    commit_all(tmp_path)
    rc = vaw.main([str(tmp_path), "--json", "--no-gh"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["target"].endswith(tmp_path.name)
    assert rc == parsed["exit_code"]
