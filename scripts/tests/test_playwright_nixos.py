"""Hermetic tests for scripts/playwright-nixos — driver SELECTION, the chromium
build-number GUARD, and the adoption instrumentation.

No real nix / Chromium: a stub `nix` is prepended to PATH, so `build` and `eval`
resolve from env-supplied tables instead of the flake. The adoption event is
emitted via the REAL sibling `emit` binary into a temp spool and round-tripped
through the real collector.parse_line.

🔴 EVERY test plants a `node_modules` inside its own tmp_path. That is not
decoration — the wrapper WALKS UP from $PWD looking for a project, and pytest's
tmp_path lives under /tmp, so a stray /tmp/node_modules on the dev host (there is
one) got picked up as "the project" and silently drove these tests. The suite
still passed, for the wrong reason, and would have behaved differently in the nix
sandbox where /tmp is empty — a two-tier divergence. Planting the project makes
detection terminate at a directory the test owns.

    run:  pytest scripts/tests/test_playwright_nixos.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "playwright-nixos"

# What the flake really offers, mirrored here so a rename in flake.nix that the
# wrapper's naming convention depends on shows up as a test failure.
DEFAULT_ATTR = "playwright-driver"
V157_ATTR = "playwright-driver-1_57"


def _load_collector():
    sys.path.insert(0, str(SCRIPTS / "collector"))
    import collector as C  # noqa: PLC0415
    return C


def _stub_nix(dirpath: Path):
    """A fake `nix` resolving from two env tables of `attr:value` pairs.

    `build <ref>.browsers` -> FAKE_BUNDLES lookup (exit 1 if absent)
    `eval --raw <ref>.version` -> FAKE_VERSIONS lookup (exit 1 if absent, which is
        how the wrapper learns an attr does not exist)
    `eval --json …#packages.<sys> --apply builtins.attrNames` -> keys of FAKE_VERSIONS
    `eval --impure --expr builtins.currentSystem` -> a fixed system string
    """
    p = dirpath / "nix"
    p.write_text(
        # /bin/sh (not `#!/usr/bin/env bash`) so the stub also execs in the nix
        # build sandbox, which has no /usr/bin/env; body is POSIX-sh compatible.
        # NB: POSIX sh DOES word-split an unquoted $1, which is what makes the
        # `for kv in $1` table walk below work (zsh would not — see RULES.md).
        "#!/bin/sh\n"
        "lookup() {\n"
        "  for kv in $1; do\n"
        '    k=${kv%%:*}; v=${kv#*:}\n'
        '    if [ "$k" = "$2" ]; then printf %s "$v"; return 0; fi\n'
        "  done\n"
        "  return 1\n"
        "}\n"
        "attr_of() {\n"          # <path>#<attr>.<leaf>  ->  <attr>
        '  a=${1##*#}; printf %s "${a%.*}"\n'
        "}\n"
        'case "$1" in\n'
        "  build)\n"
        '    for a in "$@"; do ref="$a"; done\n'
        '    lookup "${FAKE_BUNDLES:-}" "$(attr_of "$ref")" || exit 1\n'
        "    ;;\n"
        "  eval)\n"
        '    case "$*" in\n'
        "      *--expr*) printf x86_64-linux ;;\n"
        "      *builtins.attrNames*)\n"
        '        sep=""; printf "["\n'
        '        for kv in ${FAKE_VERSIONS:-}; do\n'
        '          printf \'%s"%s"\' "$sep" "${kv%%:*}"; sep=","\n'
        "        done\n"
        '        printf "]" ;;\n'
        "      *)\n"
        '        for a in "$@"; do ref="$a"; done\n'
        '        lookup "${FAKE_VERSIONS:-}" "$(attr_of "$ref")" || exit 1 ;;\n'
        "    esac\n"
        "    ;;\n"
        "esac\n"
        "exit 0\n"
    )
    p.chmod(0o755)


def _plant_project(root: Path, version: str, chromium_rev: str | None):
    """A minimal node_modules the wrapper's detector will stop at."""
    pc = root / "node_modules" / "playwright-core"
    pc.mkdir(parents=True)
    (pc / "package.json").write_text(json.dumps({"name": "playwright-core", "version": version}))
    if chromium_rev is not None:
        (pc / "browsers.json").write_text(json.dumps({"browsers": [
            {"name": "chromium", "revision": chromium_rev},
            {"name": "firefox", "revision": "999"},
        ]}))


def _plant_bundle(root: Path, name: str, chromium_rev: str | None) -> Path:
    """A store-path-shaped directory holding a chromium-<rev> subdir."""
    b = root / name
    b.mkdir(parents=True)
    if chromium_rev is not None:
        (b / f"chromium-{chromium_rev}").mkdir()
        (b / f"chromium_headless_shell-{chromium_rev}").mkdir()
    return b


def _run(tmp_path, args, *, bundles=None, versions=None, env_extra=None, cwd=None):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    _stub_nix(stub)
    spool = tmp_path / "spool"
    env = {**os.environ,
           "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
           "ACTIVITY_SPOOL_DIR": str(spool)}
    # Never let the host's real session variables reach the wrapper.
    for k in ("PLAYWRIGHT_NIXOS_DRIVER", "PLAYWRIGHT_NIXOS_ALLOW_REV_SKEW",
              "PLAYWRIGHT_BROWSERS_PATH"):
        env.pop(k, None)
    env["FAKE_BUNDLES"] = " ".join(f"{k}:{v}" for k, v in (bundles or {}).items())
    env["FAKE_VERSIONS"] = " ".join(f"{k}:{v}" for k, v in (versions or {}).items())
    env.update(env_extra or {})
    # HOME is redirected so warn_mcp_build_skew cannot read the real
    # ~/.cache/ms-playwright and make assertions on stderr host-dependent.
    env["HOME"] = str(tmp_path / "home")
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                       text=True, env=env, cwd=str(cwd or tmp_path))
    return r, spool


def _std_world(tmp_path):
    """The real shape: two bundles, two versions, mirroring flake.nix."""
    return dict(
        bundles={DEFAULT_ATTR: str(_plant_bundle(tmp_path, "b1228", "1228")),
                 V157_ATTR: str(_plant_bundle(tmp_path, "b1200", "1200"))},
        versions={DEFAULT_ATTR: "1.61.1", V157_ATTR: "1.57.0"},
    )


def _read_event(spool, C):
    line = (spool / "current.log").read_text().strip().splitlines()[-1]
    ev = C.parse_line(line)
    assert ev is not None
    return ev


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def test_selects_the_suffixed_bundle_matching_the_project_pin(tmp_path):
    """1.57 project -> playwright-driver-1_57, NOT the default."""
    _plant_project(tmp_path, "1.57.0", "1200")
    r, _ = _run(tmp_path, ["--select"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    assert f"selected attr  : {V157_ATTR}" in r.stdout, r.stdout
    assert "wants chromium-1200" in r.stdout
    assert "ships chromium-1200" in r.stdout


def test_selects_the_default_when_the_project_is_on_the_default_line(tmp_path):
    _plant_project(tmp_path, "1.61.1", "1228")
    r, _ = _run(tmp_path, ["--select"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    assert f"selected attr  : {DEFAULT_ATTR}" in r.stdout, r.stdout
    assert "the flake default matches" in r.stdout


def test_a_caret_patch_bump_inside_the_line_still_selects_the_same_bundle(tmp_path):
    """Selection is keyed on major.minor — 1.57.2 must still land on -1_57.

    This is what makes `^1.57.0` (a floating caret) safe: the chromium build is
    stable across a minor line, so a patch bump must not fall off the bundle.
    """
    _plant_project(tmp_path, "1.57.2", "1200")
    r, _ = _run(tmp_path, ["--select"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    assert f"selected attr  : {V157_ATTR}" in r.stdout, r.stdout


def test_unknown_line_falls_back_to_default_and_warns(tmp_path):
    """No output for the project's line -> default + a loud warning (not silence)."""
    _plant_project(tmp_path, "1.42.0", None)
    r, _ = _run(tmp_path, ["--select"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    assert f"selected attr  : {DEFAULT_ATTR}" in r.stdout, r.stdout
    assert "offers no matching bundle" in r.stderr, r.stderr


def test_no_project_detected_uses_the_default(tmp_path):
    """Walk-up must terminate: an isolated root has no node_modules anywhere."""
    root = tmp_path / "iso"
    root.mkdir()
    world = _std_world(tmp_path)
    r, _ = _run(tmp_path, ["--select"], cwd=root, **world)
    assert r.returncode == 0, r.stderr
    assert f"selected attr  : {DEFAULT_ATTR}" in r.stdout, r.stdout


def test_list_enumerates_every_driver_with_its_version(tmp_path):
    _plant_project(tmp_path, "1.57.0", "1200")
    r, _ = _run(tmp_path, ["--list"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "1.61.1" in r.stdout and "1.57.0" in r.stdout
    assert V157_ATTR in r.stdout and "(default)" in r.stdout


# --------------------------------------------------------------------------
# The guard: a chromium build mismatch must be LOUD, never a 0-file "pass"
# --------------------------------------------------------------------------

def test_build_mismatch_hard_fails_and_names_both_revisions(tmp_path):
    """The whole point. Forced onto the wrong bundle -> exit 1, both revs named."""
    _plant_project(tmp_path, "1.57.0", "1200")
    r, _ = _run(tmp_path, ["true"], env_extra={"PLAYWRIGHT_NIXOS_DRIVER": "default"},
                **_std_world(tmp_path))
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "chromium build MISMATCH" in r.stderr, r.stderr
    assert "wants chromium-1200" in r.stderr
    assert "ships chromium-1228" in r.stderr


def test_the_override_selects_but_does_not_SUPPRESS_the_guard(tmp_path):
    """🔴 Regression pin for a real defect, 2026-08-06.

    `PLAYWRIGHT_NIXOS_DRIVER` used to short-circuit project detection, which also
    disabled the assertion that depends on it — the guard reported success while
    no longer able to fire. Each knob does exactly one thing; only
    ALLOW_REV_SKEW may silence the assertion.
    """
    _plant_project(tmp_path, "1.61.1", "1228")
    r, _ = _run(tmp_path, ["true"], env_extra={"PLAYWRIGHT_NIXOS_DRIVER": "1.57"},
                **_std_world(tmp_path))
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "chromium build MISMATCH" in r.stderr, r.stderr


def test_allow_rev_skew_downgrades_the_guard_to_a_warning(tmp_path):
    _plant_project(tmp_path, "1.57.0", "1200")
    r, _ = _run(tmp_path, ["true"],
                env_extra={"PLAYWRIGHT_NIXOS_DRIVER": "default",
                           "PLAYWRIGHT_NIXOS_ALLOW_REV_SKEW": "1"},
                **_std_world(tmp_path))
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "chromium build MISMATCH" in r.stderr
    assert "continuing anyway" in r.stderr


def test_matching_bundle_passes_the_guard_and_execs(tmp_path):
    """Positive control for the guard: it must not fire on a correct pairing."""
    _plant_project(tmp_path, "1.57.0", "1200")
    r, _ = _run(tmp_path, ["true"], **_std_world(tmp_path))
    assert r.returncode == 0, (r.returncode, r.stderr)
    assert "MISMATCH" not in r.stderr, r.stderr


def test_unreadable_revision_says_so_instead_of_passing_silently(tmp_path):
    """A skipped assertion must never look like a passed one."""
    _plant_project(tmp_path, "1.57.0", None)      # no browsers.json
    r, _ = _run(tmp_path, ["true"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "assertion SKIPPED" in r.stderr, r.stderr


def test_unknown_override_version_exits_2_and_lists_what_exists(tmp_path):
    _plant_project(tmp_path, "1.57.0", "1200")
    r, _ = _run(tmp_path, ["true"], env_extra={"PLAYWRIGHT_NIXOS_DRIVER": "1.42"},
                **_std_world(tmp_path))
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "no flake output" in r.stderr and V157_ATTR in r.stderr


# --------------------------------------------------------------------------
# Adoption instrumentation
# --------------------------------------------------------------------------

def test_resolve_failure_emits_error(tmp_path):
    C = _load_collector()
    _plant_project(tmp_path, "1.57.0", "1200")
    world = _std_world(tmp_path)
    world["bundles"] = {}                  # nothing realises -> resolve-error, exit 1
    r, spool = _run(tmp_path, ["node", "e2e.mjs"], **world)
    assert r.returncode == 1, r.stderr
    ev = _read_event(spool, C)
    assert ev["source"] == "tool" and ev["kind"] == "invocation"
    assert ev["text"] == "playwright-nixos"
    p = json.loads(ev["payload"])
    assert p["outcome"] == "error" and p["mode"] == "resolve-error"
    assert p["driver"] == "1.57.0"          # the SELECTED version, not the default


def test_exec_path_emits_ok(tmp_path):
    C = _load_collector()
    _plant_project(tmp_path, "1.57.0", "1200")
    # `true` is the wrapped command -> exec replaces the shell -> exit 0.
    r, spool = _run(tmp_path, ["true"], **_std_world(tmp_path))
    assert r.returncode == 0, r.stderr
    ev = _read_event(spool, C)
    assert ev["text"] == "playwright-nixos"
    p = json.loads(ev["payload"])
    assert p["outcome"] == "ok" and p["mode"] == "exec"
    assert p["driver"] == "1.57.0"


def test_build_mismatch_emits_its_own_outcome(tmp_path):
    C = _load_collector()
    _plant_project(tmp_path, "1.57.0", "1200")
    r, spool = _run(tmp_path, ["true"], env_extra={"PLAYWRIGHT_NIXOS_DRIVER": "default"},
                    **_std_world(tmp_path))
    assert r.returncode == 1
    p = json.loads(_read_event(spool, C)["payload"])
    assert p["outcome"] == "error" and p["mode"] == "rev-mismatch"


def test_info_modes_do_not_emit(tmp_path):
    """`--version` / `--env` are introspection, not a run — they emit nothing."""
    C = _load_collector()  # noqa: F841
    _plant_project(tmp_path, "1.57.0", "1200")
    r, spool = _run(tmp_path, ["--version"], **_std_world(tmp_path))
    assert r.returncode == 0
    assert r.stdout.strip() == "1.57.0"
    assert not (spool / "current.log").exists()
