#!/usr/bin/env python3
"""Give a host a REVIEWED baseline `permissions.allow` in ~/.claude/settings.json.

WHY THIS IS A SCRIPT AND NOT A NIX MODULE
-----------------------------------------
`~/.claude/settings.json` is per-host and deliberately UNMANAGED — `nix/home.nix`
says so twice, and the file is written by Claude Code itself every time a
permission prompt is answered. Anything nix owned here would be clobbered on the
next "allow", or would clobber the operator's own answers. So the baseline is
applied by an idempotent script the operator runs, not by a switch.

WHAT IT DOES
------------
Adds the entries in CURATED below to `permissions.allow` if they are absent.
It is **strictly additive**: it never removes, reorders or rewrites an existing
entry, and it never touches `deny`, `ask`, `defaultMode`, or any other key. Run
it twice and the second run adds nothing.

🔴 WHY THE LIST IS CURATED AND NOT COPIED
-----------------------------------------
The workbench's block held 210 entries on 2026-08-11, and #380 exists precisely
because that file collects garbage: answering "allow" stores the PROMPT TEXT
verbatim, so pasted YAML, heredoc bodies and `curl -u user:pass` one-liners all
became permanent rules there (38 of them, accreting unnoticed for two months).
Copying it wholesale would replicate that accretion onto a second machine and
hand it a second copy of every mistake. So the baseline below is a subset chosen
by hand, and it is checked against #380's detector before anything is written --
see `_reject_junk`. What was deliberately LEFT OUT, and why:

  * anything naming an absolute `/home/zach/workspace/<repo>` path or a specific
    kubeconfig -- host- and checkout-specific, not a baseline;
  * an `ssh … root@<public-ip> …` rule and a client-named `git -C …` rule --
    a public IP and a client name, which this repo's own
    `test_no_public_ips.py` / `test_no_client_hostnames.py` forbid in tracked
    files, and which nobody should be pre-approving anyway;
  * `Bash(git add:*)` and `Bash(git commit:*)` -- `claude/RULES.md` forbids blind
    staging and unrequested commits, and the permission prompt is the last thing
    standing between an agent and `git add -A`;
  * arbitrary-execution and egress shapes: `curl`, `wget`, `nc`, `ssh`, `source`,
    `xargs`, `python3`, `sqlite3`, `chmod`, `chown`, `sops` (decrypts secrets),
    bare `kubectl:*` / `k3s kubectl:*` (matches `delete`), and every mutating
    `kubectl`/`helm`/`docker` verb;
  * `Bash(/config/config.xml)` and `Bash(export KUBECONFIG=…)` -- captured junk
    and a no-op rule respectively.

The result is the shape that actually causes the papercut: read-only inspection
of the local machine, read-only cluster and network queries, documentation
lookups. Broadening it is a review decision, made here, in git.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"

# --- the reviewed baseline ---------------------------------------------------
#
# Grouped by what each group grants. Every entry is host-agnostic: no absolute
# checkout path, no kubeconfig filename, no credential, no IP.
CURATED: list[str] = [
    # Read-only inspection of the local filesystem. This is the bulk of the
    # papercut: an agent cannot read a repo without being prompted.
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(grep:*)",
    "Bash(find:*)",
    "Bash(tree:*)",
    "Bash(wc:*)",
    "Bash(sort:*)",
    "Bash(readlink:*)",
    "Bash(jq:*)",
    "Bash(mkdir:*)",

    # Read-only inspection of the local machine.
    "Bash(systemctl status:*)",
    "Bash(journalctl:*)",
    "Bash(ip addr show:*)",
    "Bash(ip link:*)",

    # Read-only network diagnostics. No egress that transfers anything.
    "Bash(dig:*)",
    "Bash(nslookup:*)",
    "Bash(host:*)",
    "Bash(ping:*)",

    # Read-only cluster queries. Deliberately verb-by-verb: a bare
    # `Bash(kubectl:*)` also matches `kubectl delete`.
    "Bash(kubectl get:*)",
    "Bash(kubectl describe:*)",
    "Bash(kubectl logs:*)",
    "Bash(kubectl top:*)",
    "Bash(flux get:*)",
    "Bash(helm show values:*)",

    # Read-only container/registry and certificate inspection.
    "Bash(docker images:*)",
    "Bash(docker info:*)",
    "Bash(openssl x509:*)",

    # Read-only GitHub queries.
    "Bash(gh pr list:*)",
    "Bash(gh pr view:*)",

    # Documentation lookups.
    "WebSearch",
    "WebFetch(domain:github.com)",
    "WebFetch(domain:raw.githubusercontent.com)",
    "WebFetch(domain:mynixos.com)",
    "WebFetch(domain:discourse.nixos.org)",
    "WebFetch(domain:artifacthub.io)",
    "WebFetch(domain:hub.docker.com)",
    "WebFetch(domain:grafana.com)",
    "WebFetch(domain:docs.nvidia.com)",
    "WebFetch(domain:www.talos.dev)",
    "WebFetch(domain:docs.siderolabs.com)",
    "mcp__context7__resolve-library-id",
    "mcp__context7__get-library-docs",
    "mcp__context7__query-docs",
]


def _classify():
    """#380's junk detector, IMPORTED rather than restated.

    Its module docstring says the patterns there are the single source of truth
    and that any other mention must cross-reference it. A second copy of those
    regexes living here would agree with itself forever while #380's evolved --
    the shape this repo keeps finding. Returns None if the detector cannot be
    imported, so the script still works from a checkout without the test suite.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "tests"))
    try:
        from test_settings_allow_junk import classify  # noqa: E402
    except Exception:
        return None
    return classify


def _reject_junk(entries) -> list[tuple[str, str]]:
    """Every curated entry that #380 would flag. Must be empty before writing."""
    classify = _classify()
    if classify is None:
        return []
    return [(classify(e), e) for e in entries if classify(e)]


def merge(existing: list[str], curated: list[str]) -> tuple[list[str], list[str]]:
    """-> (the new allow list, the entries actually added).

    Additive and order-preserving: existing entries keep their positions and the
    additions are appended in CURATED order. Nothing is ever dropped, including
    duplicates already in the file -- removing entries is #380's job and a
    separate, reviewed act.
    """
    present = set(existing)
    added = [e for e in curated if e not in present]
    return existing + added, added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS,
                    help="settings.json to update (default: ~/.claude/settings.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be added and write nothing")
    args = ap.parse_args(argv)

    junk = _reject_junk(CURATED)
    if junk:
        print("REFUSING TO WRITE — the curated list itself contains entries that "
              "#380's detector flags as junk:", file=sys.stderr)
        for cls, e in junk:
            print(f"  [{cls}] {e}", file=sys.stderr)
        return 2

    path = args.settings
    if not path.exists():
        print(f"{path} does not exist — nothing to merge into.", file=sys.stderr)
        print("  Claude Code writes this file on first run; start it once, then "
              "re-run this script.", file=sys.stderr)
        return 3
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"{path} is not readable JSON ({exc}) — refusing to touch it.",
              file=sys.stderr)
        return 4
    if not isinstance(data, dict):
        print(f"{path} is not a JSON object — refusing to touch it.", file=sys.stderr)
        return 4

    perms = data.get("permissions")
    if perms is None:
        perms = {}
    if not isinstance(perms, dict):
        print(f"{path}: `permissions` is not an object — refusing to touch it.",
              file=sys.stderr)
        return 4
    existing = perms.get("allow") or []
    if not isinstance(existing, list):
        print(f"{path}: `permissions.allow` is not a list — refusing to touch it.",
              file=sys.stderr)
        return 4

    merged, added = merge(list(existing), CURATED)
    print(f"{path}: {len(existing)} existing entr{'y' if len(existing) == 1 else 'ies'}, "
          f"{len(added)} to add.")
    for e in added:
        print(f"  + {e}")
    if not added:
        print("  nothing to do — already in sync.")
        return 0
    if args.dry_run:
        print("  --dry-run: nothing written.")
        return 0

    perms["allow"] = merged
    data["permissions"] = perms

    backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%dT%H%M%S"))
    shutil.copy2(path, backup)
    # Re-emit by PARSING and DUMPING, which is also what #380's playbook
    # prescribes: a hand-edited JSON file is how the multi-line entries got in.
    body = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(tmp, path.stat().st_mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    print(f"  wrote {path} ({len(merged)} entries). Backup: {backup}")
    print("  🔴 Claude Code reads settings.json at STARTUP — restart any running "
          "session before expecting the new rules to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
