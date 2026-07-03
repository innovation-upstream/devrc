#!/usr/bin/env python3
"""PostToolUse nudge: when a Bash call re-types a repo path (`cd <repo>` / `REPO=<repo>`)
or a kubeconfig path (`export KUBECONFIG=<path>`), inject context reminding Claude that
the canonical handle is ALREADY exported in .zshenv and persists across calls.

Why this exists: non-interactive `zsh -c` (the Bash tool) doesn't keep shell state
between calls, so agents re-`cd`/`export` the same handful of paths on ~50% of Bash
calls (measured ~3.8k plumbing turns in one week of transcripts). devrc pre-exports
$DEVRC/$HOMELAB/$DATAPACKET/$CIVITAI/$CIVITAI_CLI + $KC_* in .zshenv; the CLAUDE.md
pointers documenting them are opt-in, and opt-in guidance has historically not stuck.
This is the deterministic, in-the-moment version: it fires the instant the plumbing
pattern runs, once per handle per session, so it teaches without nagging.

Deterministic (matches literal path prefixes), non-blocking (only adds context, never
denies), fail-open (any error -> exit 0 silently; must never break the Bash tool).
"""
import sys, json, os, re

HOME = os.path.expanduser("~")

# Absolute repo root -> canonical env var (must match nix/programs/zsh/default.nix).
REPO_VARS = {
    f"{HOME}/workspace/devrc": "DEVRC",
    f"{HOME}/workspace/homelab-talos": "HOMELAB",
    f"{HOME}/workspace/civit/datapacket-talos": "DATAPACKET",
    f"{HOME}/workspace/civit/civitai": "CIVITAI",
    f"{HOME}/workspace/civit/civitai-cli": "CIVITAI_CLI",
}
# Absolute kubeconfig path -> canonical env var.
KC_VARS = {
    f"{HOME}/workspace/homelab-talos/homelab-kubeconfig": "KC_HOMELAB",
    f"{HOME}/workspace/homelab-talos/workbench-kubeconfig": "KC_WORKBENCH",
    f"{HOME}/workspace/civit/datapacket-talos/prod-kubeconfig": "KC_DPPROD",
    f"{HOME}/.kube/homelab-nebula.yaml": "KC_NEBULA",
}
# Relative kubeconfig references (e.g. datapacket's `KUBECONFIG=./prod-kubeconfig`) by basename.
KC_BASENAMES = {os.path.basename(p): v for p, v in KC_VARS.items()}

CACHE_DIR = f"{HOME}/.cache/claude-shell-env-nudge"


def _norm(path):
    """Strip quotes, expand ~, drop a trailing slash — for absolute-path matching."""
    p = path.strip().strip("'\"")
    if p.startswith("~"):
        p = HOME + p[1:]
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def analyze(cmd):
    """Pure: return an ordered, de-duplicated list of (var, hint) suggestions for `cmd`.

    Only fires on the actual plumbing shapes — a kubeconfig assignment, or a repo path
    used via `cd`/variable-assignment — not on incidental path mentions. Skips any handle
    the command is already using as `$VAR`.
    """
    suggestions = {}  # var -> hint (dict preserves first hint, dedupes var)

    # 1) KUBECONFIG=<path>  (covers `export KUBECONFIG=x` and inline `KUBECONFIG=x kubectl`)
    for m in re.finditer(r"KUBECONFIG=(['\"]?)([^\s'\";|&]+)\1", cmd):
        raw = m.group(2)
        if raw.startswith("$"):  # already a variable
            continue
        norm = _norm(raw)
        var = KC_VARS.get(norm) or KC_BASENAMES.get(os.path.basename(norm))
        if var:
            suggestions.setdefault(var, f"KUBECONFIG=${var} kubectl …")

    # 2) repo root via `cd <repo>` or `<VAR>=<repo>` (the re-plumbing patterns).
    # Match only when the value is EXACTLY the repo root (terminator after it) — not a
    # path INTO the repo like `KUBECONFIG=<repo>/prod-kubeconfig`, which is a file ref.
    for path, var in REPO_VARS.items():
        pat = r"(?:\bcd\s+|=)['\"]?" + re.escape(path) + r"['\"]?(?:\s|;|&|$)"
        if re.search(pat, cmd):
            suggestions.setdefault(var, f"git -C ${var} … (no cd needed)")

    # Drop handles the command already uses as $VAR / ${VAR}.
    out = []
    for var, hint in suggestions.items():
        if re.search(r"\$\{?" + re.escape(var) + r"\b", cmd):
            continue
        out.append((var, hint))
    return out


def _already_nudged(session, var):
    """Per-session dedupe so each handle is suggested at most once. Fail-open on any IO error."""
    if not session:
        return False
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        f = os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9_.-]", "_", session))
        seen = set()
        if os.path.exists(f):
            with open(f) as fh:
                seen = set(fh.read().split())
        if var in seen:
            return True
        with open(f, "a") as fh:
            fh.write(var + "\n")
        return False
    except Exception:
        return False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        if data.get("tool_name") != "Bash":
            sys.exit(0)
        cmd = (data.get("tool_input") or {}).get("command", "")
        if not cmd:
            sys.exit(0)
        session = data.get("session_id") or ""
        fresh = [(v, h) for v, h in analyze(cmd) if not _already_nudged(session, v)]
        if not fresh:
            sys.exit(0)
        lines = "\n".join(f"  • ${v} → {h}" for v, h in fresh)
        nudge = (
            "shell-env: the handle(s) below are pre-exported in .zshenv (devrc) and persist "
            "across every `zsh -c` — non-interactive shells don't keep state between calls, so "
            "re-`cd`/`export`-ing the literal path each time is wasted. Prefer them next time:\n"
            f"{lines}"
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": nudge,
            }
        }))
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
