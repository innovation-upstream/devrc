#!/usr/bin/env python3
"""One place that writes an executable test helper, with a shebang that execs in
BOTH tiers.

🔴 THE TRAP (cost this suite 14 red tests on 2026-08-02)
--------------------------------------------------------
A test that writes a stub script at RUNTIME and then execs it must not use
`#!/usr/bin/env <interp>`:

  * On a NixOS dev host `/usr/bin/env` exists (it is a symlink into the
    coreutils store path), so the stub execs and the suite is green.
  * The nix BUILD SANDBOX — `nix build .#checks.x86_64-linux.pytests`, which is
    the authoritative gate — has NO `/usr/bin/env`, so `execve` fails ENOENT.

nixpkgs' `patchShebangs src/scripts` (see flake.nix) rewrites the shebangs of
files that are IN THE SOURCE TREE at build time — which is why the real
`scripts/collector/emit` works in the sandbox — but it cannot touch a file a
test creates while running. So the defect is invisible on the dev host and only
the sandbox can observe it. That is the two-tier hazard in RULES.md, and the
failure reads as "my mock emit exited 1", not as "wrong shebang".

Worse, in this suite the symptom was mostly SILENT: `emitEvent` swallows every
error by design (telemetry must never crash OpenCode), so a stub that cannot
exec produces an EMPTY log and a node exit code of 0 — the tests failed later,
on an empty list, pointing at the wrong thing.

WHY THIS FILE EXISTS AT ALL
---------------------------
The repo had already learned this — four separate sites carry a hand-written
comment explaining it (`scripts/tests/test_playwright_nixos.py`,
`scripts/tests/test_notify_failure.py`, `scripts/claude-hooks/tests/
test_claude_notify.py`, `scripts/task-spec-drafter/tests/
test_allowlist_covers_prompt_shapes.py`), and `scripts/browser-bridge/tests/
test_browser_agent.py` solves it a fifth way by rewriting the shebang to
`sys.executable`. A rule re-derived at every call site regenerates the same bug
at the next one — which is exactly what happened here. This is that rule, once.
"""
from __future__ import annotations

import os
from pathlib import Path

# POSIX `sh`, the convention already used by the other suites in this repo.
# NOT `/usr/bin/env sh`, and not `bash` — the stub bodies below are POSIX, and
# /bin/sh is the one interpreter path guaranteed to exist inside the sandbox.
SH = "/bin/sh"
SHEBANG = f"#!{SH}\n"


def write_exec(path: Path, body: str) -> Path:
    """Write `body` as an executable POSIX-sh script at `path`.

    `body` must NOT carry its own shebang — this function owns it, so a call
    site cannot reintroduce `/usr/bin/env`.
    """
    if body.lstrip().startswith("#!"):
        raise AssertionError(
            "write_exec owns the shebang — pass the body only. A call site that "
            "supplies its own is how #!/usr/bin/env comes back.")
    path.write_text(SHEBANG + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def shell_does_brace_expansion() -> bool:
    """MEASURE whether the shell node's `execSync` uses will brace-expand.

    🔴 Measured, never assumed — it differs between the two tiers:
      * dev host (NixOS): /bin/sh → bash-interactive, `{a,b}` expands to `a b`.
      * nix build sandbox: it does NOT expand.

    That matters because the pre-fix `execSync` bug had TWO effects, and only
    one of them is universal:
      * word-splitting + quote removal — happens in EVERY POSIX shell, so the
        payload is mangled in both tiers;
      * brace expansion of the unquoted JSON object into three separate
        `b64:payload=` args — bash-only, and it is what produced the EXACT
        stored value `args_summary:{"name":"…"}` in the 2,699 live rows (the
        workbench runs bash).

    Tests assert the universal half unconditionally and gate the bash-only
    signature on this probe, so the claim carries the scope it was measured at.
    """
    import subprocess  # noqa: PLC0415 — keep this module import-cheap
    try:
        r = subprocess.run([SH, "-c", 'printf "%s\\n" {a,b}'],
                           capture_output=True, text=True, timeout=10)
    except OSError:
        return False
    return r.stdout.split() == ["a", "b"]


def interpreter_is_executable() -> bool:
    """True when the shebang interpreter really exists and is runnable.

    Used by the self-check test: if this is ever False the stubs cannot exec and
    every test that depends on one must go RED loudly, not silently empty.
    """
    return os.path.isfile(SH) and os.access(SH, os.X_OK)
