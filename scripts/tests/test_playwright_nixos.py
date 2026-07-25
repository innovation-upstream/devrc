"""Hermetic tests for scripts/playwright-nixos adoption instrumentation.

No real nix / Chromium: a stub `nix` is prepended to PATH so both the
resolve-FAILURE path (empty browser bundle) and the OK/exec path are driven
offline. The event is emitted via the REAL sibling `emit` binary into a temp
spool and round-tripped through the real collector.parse_line.

Only the emit behaviour is under test here (the browser-resolution logic needs a
real nix and is out of scope for the hermetic suite).

    run:  pytest scripts/tests/test_playwright_nixos.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "playwright-nixos"


def _load_collector():
    sys.path.insert(0, str(SCRIPTS / "collector"))
    import collector as C  # noqa: PLC0415
    return C


def _stub_nix(dirpath: Path):
    """A fake `nix`: `build` echoes $FAKE_BROWSERS, `eval` prints a version."""
    p = dirpath / "nix"
    p.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        '  build) [ -n "${FAKE_BROWSERS:-}" ] && echo "$FAKE_BROWSERS" ;;\n'
        '  eval)  echo "1.2.3" ;;\n'
        "esac\n"
        "exit 0\n"
    )
    p.chmod(0o755)


def _run(tmp_path, args, fake_browsers=None):
    stub = tmp_path / "bin"
    stub.mkdir()
    _stub_nix(stub)
    spool = tmp_path / "spool"
    env = {**os.environ,
           "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
           "ACTIVITY_SPOOL_DIR": str(spool)}
    if fake_browsers is not None:
        env["FAKE_BROWSERS"] = str(fake_browsers)
    else:
        env.pop("FAKE_BROWSERS", None)
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True,
                       text=True, env=env, cwd=str(tmp_path))
    return r, spool


def _read_event(spool, C):
    line = (spool / "current.log").read_text().strip().splitlines()[-1]
    ev = C.parse_line(line)
    assert ev is not None
    return ev


def test_resolve_failure_emits_error(tmp_path):
    C = _load_collector()
    # No FAKE_BROWSERS -> empty bundle -> resolve-error path -> exit 1.
    r, spool = _run(tmp_path, ["node", "e2e.mjs"], fake_browsers=None)
    assert r.returncode == 1
    ev = _read_event(spool, C)
    assert ev["source"] == "tool" and ev["kind"] == "invocation"
    assert ev["text"] == "playwright-nixos"
    p = json.loads(ev["payload"])
    assert p["outcome"] == "error" and p["mode"] == "resolve-error"
    assert p["driver"] == "1.2.3"


def test_exec_path_emits_ok(tmp_path):
    C = _load_collector()
    browsers = tmp_path / "browsers"      # must be an existing dir
    browsers.mkdir()
    # `true` is the wrapped command -> exec replaces the shell -> exit 0.
    r, spool = _run(tmp_path, ["true"], fake_browsers=browsers)
    assert r.returncode == 0, r.stderr
    ev = _read_event(spool, C)
    assert ev["text"] == "playwright-nixos"
    p = json.loads(ev["payload"])
    assert p["outcome"] == "ok" and p["mode"] == "exec"
    assert p["driver"] == "1.2.3"


def test_info_modes_do_not_emit(tmp_path):
    """`--version` / `--env` are introspection, not a run — they emit nothing."""
    C = _load_collector()  # noqa: F841
    browsers = tmp_path / "browsers"
    browsers.mkdir()
    r, spool = _run(tmp_path, ["--version"], fake_browsers=browsers)
    assert r.returncode == 0
    assert not (spool / "current.log").exists()
