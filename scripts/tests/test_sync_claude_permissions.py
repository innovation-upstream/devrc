"""Gate on `scripts/sync-claude-permissions.py` — the reviewed baseline that
closes the laptop's missing `permissions` block.

WHY THIS NEEDS ITS OWN GATE
---------------------------
The script writes to a file that is outside the repo, unmanaged by nix, and
rewritten by Claude Code itself. Three things can go wrong, and two of them are
silent:

  * it could DESTROY the operator's own answers (a rewrite instead of a merge);
  * the curated list could itself be junk of the kind #380 gates, in which case
    this "fix" ships the accretion problem to a second machine;
  * it could be non-idempotent, so the second run appends duplicates forever.

All three are asserted below against fixtures in `tmp_path`. Nothing here reads
or writes the real `~/.claude/settings.json` — the live file is the operator's,
and a test that touched it would be modifying a production host from a suite.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "sync-claude-permissions.py"

_spec = importlib.util.spec_from_file_location("sync_claude_permissions", SCRIPT)
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)

from test_settings_allow_junk import classify  # noqa: E402


# --- the laptop's measured shape -------------------------------------------- #
# Top-level keys as read off the laptop on 2026-08-11 (names only; the values
# here are placeholders). The point of the fixture is the ABSENCE of a
# `permissions` key, which is what made it prompt for operations the workbench
# allowed.
LAPTOP_KEYS = ["alwaysThinkingEnabled", "autoCompactWindow", "effortLevel",
               "enabledPlugins", "fileCheckpointingEnabled", "hooks",
               "preferredNotifChannel", "skipWorkflowUsageWarning",
               "statusLine", "voice", "voiceEnabled"]


def _settings(tmp_path, body) -> Path:
    p = tmp_path / "settings.json"
    p.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    return p


def _run(path, *args):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--settings", str(path), *args],
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


# --- the curated list itself ------------------------------------------------ #
def test_the_curated_list_is_junk_free_by_380s_own_detector():
    """🔴 THE POINT OF NOT COPYING THE WORKBENCH'S BLOCK.

    #380 measured 38 junk entries in a 248-entry allow-list. If this baseline
    carried any of that shape, the fix would be propagating the accretion
    problem to a second machine under the name of a fix.

    POSITIVE CONTROL FIRST: a zero here is only a measurement if the detector can
    fire at all, and `classify` returning None for everything looks identical to
    a clean list.
    """
    assert classify("Bash(apiVersion: v1)"), (
        "the junk detector does not fire on a known-junk entry, so the clean "
        "result below would be a fact about the detector, not about the list"
    )
    findings = [(classify(e), e) for e in sync.CURATED if classify(e)]
    assert findings == [], findings


def test_the_curated_list_carries_no_host_or_client_specific_entry():
    """Every entry must be host-agnostic. The workbench's real list contains an
    absolute `/home/zach/workspace/...` path in 30+ rules, a client-named repo
    path, and an `ssh root@<public-ip>` one-liner — none of which belong in a
    baseline, and two of which this repo's own scanners forbid in tracked files.
    """
    for e in sync.CURATED:
        assert "/home/" not in e, f"host-specific path in the baseline: {e}"
        assert "kubeconfig" not in e.lower(), f"checkout-specific kubeconfig: {e}"
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", e), f"an IP literal: {e}"
        assert "@" not in e or e.startswith("mcp__"), f"an ssh-target shape: {e}"


@pytest.mark.parametrize("forbidden", [
    "Bash(git add:*)",       # claude/RULES.md: never blind-stage
    "Bash(git commit:*)",    # claude/RULES.md: commit only when asked
    "Bash(curl:*)", "Bash(wget:*)", "Bash(nc:*)", "Bash(ssh:*)",
    "Bash(python3:*)", "Bash(sops:*)", "Bash(chmod:*)", "Bash(chown:*)",
    "Bash(kubectl:*)",       # matches `kubectl delete`
    "Bash(k3s kubectl:*)",
    "Bash(xargs:*)",         # runs whatever it is piped
])
def test_the_baseline_pre_approves_no_arbitrary_execution_shape(forbidden):
    """🔴 Enumerated one per case rather than as a set difference, so a failure
    NAMES the rule that got in. Each of these is present on the workbench and is
    deliberately not part of the reviewed baseline."""
    assert forbidden not in sync.CURATED, (
        f"{forbidden} was added to the baseline — it pre-approves arbitrary "
        "execution, egress, secret decryption or a mutating verb"
    )


def test_the_script_spawns_no_subprocess():
    """🔴 THE OTHER HALF OF AN ACKNOWLEDGEMENT IN test_no_real_launchers.py.

    That suite's hazard scan sees the word `systemctl` in this file and correctly
    refuses to absorb a new reacher silently. The name occurs only inside the
    literal `Bash(systemctl status:*)` in CURATED — a permission RULE, not an
    invocation — and the acknowledgement there points at this test rather than
    restating a claim that could rot the next time this file changes.

    Asserted on the AST, not by grepping for the word: a comment saying "we do
    not spawn anything" is a claim, and an import statement is a fact.
    """
    import ast
    tree = ast.parse(SCRIPT.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "subprocess" not in imported, imported
    called = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for spawner in ("os.system", "os.popen", "os.execv", "os.execvp",
                    "os.spawnv", "os.fork", "pty.spawn"):
        assert spawner not in called, f"{spawner} is called — the file reaches a binary"


# --- the merge --------------------------------------------------------------- #
def test_a_settings_file_with_no_permissions_key_gains_one(tmp_path):
    """THE MEASURED SHAPE: the laptop had no `permissions` key at all."""
    body = {k: "placeholder" for k in LAPTOP_KEYS}
    p = _settings(tmp_path, body)
    rc, out = _run(p)
    assert rc == 0, out
    data = json.loads(p.read_text())
    assert data["permissions"]["allow"] == sync.CURATED, out
    # Every pre-existing key survives, byte-for-byte in value.
    for k in LAPTOP_KEYS:
        assert data[k] == "placeholder", f"{k} was rewritten"


def test_it_is_additive_and_removes_nothing(tmp_path):
    """🔴 THE DESTRUCTIVE FAILURE. The operator's own answers live in this list;
    a rewrite instead of a merge would silently revoke them.

    The fixture deliberately includes a JUNK entry: cleaning the list is #380's
    job and a separate reviewed act, so this script must leave it alone rather
    than quietly deciding for the operator.
    """
    mine = ["Bash(my-own-tool:*)", "Bash(apiVersion: v1)", "Bash(ls:*)"]
    p = _settings(tmp_path, {"permissions": {"allow": list(mine), "deny": ["Bash(rm:*)"],
                                             "defaultMode": "acceptEdits"}})
    rc, out = _run(p)
    assert rc == 0, out
    allow = json.loads(p.read_text())["permissions"]["allow"]
    assert allow[:3] == mine, f"existing entries were reordered or dropped: {allow[:3]}"
    assert set(sync.CURATED) <= set(allow)
    # `Bash(ls:*)` was already there and must not appear twice.
    assert allow.count("Bash(ls:*)") == 1, allow
    perms = json.loads(p.read_text())["permissions"]
    assert perms["deny"] == ["Bash(rm:*)"], "an untouched sibling key was rewritten"
    assert perms["defaultMode"] == "acceptEdits"


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    """Idempotence, asserted on the BYTES rather than on the printed count: a
    script that re-appends every entry would still print '0 to add' if it
    counted wrong."""
    p = _settings(tmp_path, {"hooks": {}})
    rc, _ = _run(p)
    assert rc == 0
    first = p.read_bytes()
    rc, out = _run(p)
    assert rc == 0, out
    assert "nothing to do" in out, out
    assert p.read_bytes() == first, "the second run rewrote the file"


def test_dry_run_writes_nothing(tmp_path):
    p = _settings(tmp_path, {"hooks": {}})
    before = p.read_bytes()
    rc, out = _run(p, "--dry-run")
    assert rc == 0, out
    assert p.read_bytes() == before, "--dry-run modified the file"
    assert "+ Bash(ls:*)" in out, "--dry-run did not report what it would add\n" + out


def test_a_write_leaves_a_backup(tmp_path):
    p = _settings(tmp_path, {"hooks": {}})
    before = p.read_bytes()
    rc, out = _run(p)
    assert rc == 0, out
    backups = list(tmp_path.glob("settings.json.bak-*"))
    assert len(backups) == 1, out
    assert backups[0].read_bytes() == before


# --- refusals ---------------------------------------------------------------- #
def test_a_missing_file_is_refused_rather_than_created(tmp_path):
    """Creating settings.json here would race Claude Code's own writer and could
    strand a file it then overwrites. Distinct rc so a caller can tell it apart
    from a parse failure."""
    rc, out = _run(tmp_path / "nope.json")
    assert rc == 3, out
    assert not (tmp_path / "nope.json").exists()


@pytest.mark.parametrize("body,label", [
    ("{ this is not json", "unparseable"),
    ('["a list, not an object"]', "not an object"),
    ('{"permissions": "a string"}', "permissions not an object"),
    ('{"permissions": {"allow": "a string"}}', "allow not a list"),
])
def test_a_malformed_settings_file_is_refused_untouched(tmp_path, body, label):
    """🔴 Refuse, never repair. Every one of these shapes must leave the file
    byte-identical — a script that "fixed" an unexpected structure would be
    deleting whatever it did not understand."""
    p = tmp_path / "settings.json"
    p.write_text(body, encoding="utf-8")
    before = p.read_bytes()
    rc, out = _run(p)
    assert rc == 4, f"{label}: expected rc 4, got {rc}\n{out}"
    assert p.read_bytes() == before, f"{label}: the file was modified"
    assert not list(tmp_path.glob("settings.json.bak-*")), f"{label}: backed up a refusal"


def test_the_junk_refusal_is_REACHABLE_and_refuses_before_writing(tmp_path, monkeypatch):
    """🔴 PROVING THE GUARD RUNS, not merely that it exists.

    `_reject_junk` returning nothing is indistinguishable from a clean list, so
    the test above cannot tell a working detector from a `return []`. Here the
    baseline is deliberately poisoned with the exact shape #380 measured, and the
    script must exit 2 having written NOTHING — the guard's own rc, not a crash.
    """
    p = _settings(tmp_path, {"hooks": {}})
    before = p.read_bytes()
    monkeypatch.setattr(sync, "CURATED", ["Bash(ls:*)", "Bash(apiVersion: v1)"])
    rc = sync.main(["--settings", str(p)])
    assert rc == 2, "a poisoned baseline was accepted"
    assert p.read_bytes() == before, "the refusal still wrote to the file"


# --- the seam with the deadman ---------------------------------------------- #
def test_the_key_this_creates_is_the_key_the_deadman_compares():
    """🔴 THE SEAM NEITHER FILE OWNS. This script exists to close a `permissions`
    divergence that `drift-check.sh` reports; if the deadman ever exempted
    `permissions`, this script would be closing a gap nothing measures, and the
    two would be individually correct and jointly pointless.

    Pinned as a RELATIONSHIP: the key this writes must NOT be on the deadman's
    per-host allowlist.
    """
    drift = (REPO_ROOT / "scripts" / "drift-check.sh").read_text()
    i = drift.index("perhost_reason() {")
    j = drift.index("\n}\n", i) + 3
    proc = subprocess.run(
        ["bash", "-c", drift[i:j] + '\nperhost_reason "$1"\n', "_", "permissions"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", (
        "drift-check.sh now exempts `permissions` from the parity comparison, so "
        "nothing observes whether this script was ever run"
    )
