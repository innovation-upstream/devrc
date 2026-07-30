#!/usr/bin/env python3
"""PreToolUse guard for Bash commands. Returns a `deny` decision (with a reason
Claude can act on) when a command matches a known-bad pattern. Extensible: add a
check_* function returning a reason string (or None) and append it to CHECKS.

Current guards:
  - git add -A / --all / .   -> stage specific paths instead (RULES: never blind-stage)
  - git reset --hard         -> use git restore/checkout, NOT stash (RULES: never reset --hard)
  - large heredoc -> file    -> use the Write tool (token waste; audit-driven)
  - cd <path> && git ...     -> use git -C <path> (audit: #1 command shape, 1482x)
  - private key in a command -> reference the key file instead (never inline)
  - secret/public-IP + a publish sink (git commit / gh pr|issue) -> scrub before
    committing/posting (insights: leaked ingress IP into a public repo once)

DESIGN NOTE — do not add "message text" stripping. The checks match the RAW
command string, so a command that merely QUOTES a blocked shape (a commit
message documenting "never git add -A") is blocked too. That false positive is
DELIBERATELY accepted.

A `_strip_message_text()` helper that blanked -m/--body values and heredoc
bodies was built and reverted (PR #217, 2026-07-30). Three adversarial audit
rounds each found a fresh hole in it, every one letting a genuinely-EXECUTING
destructive command through:
  r1: .sub() with no count blanked every same-tag heredoc, incl. `bash <<EOF`;
      substitution-fed heredocs misread as message text; stale finditer offsets;
      an escaped `\\'` over-matching past a closing quote; and a ReDoS.
  r2: `&` was not a segment separator -> `git commit -m x & bash <<EOF` stripped
      a body that runs in a real shell.
  r3: the widened command list became a SUBSTRING test -> `bash -s -- 'git
      merge' <<EOF` used a decoy argument to blank an executing body.
Deciding which bytes are inert message vs. executable command is shell parsing;
regexes cannot do it, and every round traded one failure mode for another. The
guard with NO stripping blocked all of those shapes. The false positive costs
one extra step -- write the message to a file and use `git commit -F <file>` --
which RULES already prefers over heredocs. That is a better trade than a
security guard with a rotating cast of bypasses.
"""
import sys, json, re, ipaddress


# Blind-stage forms. Quotes are STRIPPED from the argument text before matching,
# because the shell removes them too: `git add "-A"`, `git add '-A'` and
# `git add "."` all stage everything, but were invisible to a match that
# required whitespace around the flag (each verified to stage every file).
# `-[A-Za-z]*A[A-Za-z]*` catches BUNDLED short flags (`-Av`, `-vA`, `-uA`) --
# `-A` is the only uppercase-A short option `git add` has, so this cannot
# collide with -n/-v/-f/-i/-p/-e/-u/-N.
# `--no-ignore-removal` is git's documented alias for `--all`.
# Erring toward over-matching is deliberate: this guard fails CLOSED, and a
# false positive costs one `git add <path>` retype.
_BLIND_FLAG = re.compile(r"(^|\s)(-[A-Za-z]*A[A-Za-z]*|--all|--no-ignore-removal)(\s|$)")
_BLIND_PATH = re.compile(r"(^|\s)(\.|:/)(\s|$)")


def check_git_add_all(cmd):
    for m in re.finditer(r"\bgit\s+add\b([^&|;\n]*)", cmd):
        args = m.group(1).replace('"', "").replace("'", "")
        if _BLIND_FLAG.search(args) or _BLIND_PATH.search(args):
            return ("`git add -A` / `git add --all` / `git add .` is blocked by your RULES "
                    "(never blind-stage — it sweeps up unrelated working-tree changes and has "
                    "caused near-miss leaks). Stage specific paths instead: `git add <path> ...`. "
                    "(Only QUOTING this rule, e.g. in a commit message or PR body? This guard "
                    "matches the raw command text and cannot tell the difference. Write the "
                    "message to a file with the Write tool and use `git commit -F <file>` / "
                    "`gh pr create --body-file <file>` — which your RULES prefer over heredocs "
                    "anyway.)")
    return None


def check_git_reset_hard(cmd):
    if re.search(r"\bgit\s+reset\b[^&|;\n]*--hard\b", cmd):
        return ("`git reset --hard` is blocked by your RULES (irreversibly destroys uncommitted "
                "work). Use `git restore <path>` / `git checkout -- <path>` for specific files, "
                "or `git checkout <ref> -- <paths>` to take another ref's version. Do NOT reach "
                "for `git stash` — the stash stack is repo-GLOBAL across worktrees and a "
                "concurrent agent can pop yours (RULES: Git Workflow). If you truly need it, "
                "run it yourself. (Only QUOTING this rule in a commit message or PR body? Write "
                "it to a file and use `git commit -F <file>`.)")
    return None


def check_heredoc_to_file(cmd):
    # Heredoc present?
    m = re.search(r"<<-?\s*['\"]?[A-Za-z_][A-Za-z0-9_]*", cmd)
    if not m:
        return None
    FILE = (r"(?:/tmp/\S+"
            r"|(?:/|~/|\.{1,2}/)?\S+\.(?:py|sh|bash|js|ts|tsx|json|ya?ml|nix|go|rb|toml|conf|md|txt|sql|env|ini|service|cfg))")
    # The heredoc must land in a FILE — i.e. it feeds `cat`/`tee` redirected to a file,
    # NOT a real command (`python3 -`, `psql`, `kubectl apply -f -`, `bash`, …).
    # Inspect ONLY the sub-command the heredoc operator attaches to, so an unrelated
    # `> file` redirect elsewhere in a compound command can't trip a false positive.
    head = cmd[:m.start()]
    cmdline = re.split(r"\n|;|&&|\|\||\|", head)[-1]      # text before `<<` on its command
    tail = cmd[m.end():].split("\n", 1)[0]                # same line, after the tag (for `<<EOF > file`)
    feeds_dumper = bool(re.search(r"\b(?:cat|tee)\b", cmdline)) and not cmdline.rstrip().endswith("|")
    writes_file = feeds_dumper and bool(
        re.search(r">>?\s*" + FILE, cmdline)
        or re.search(r"\btee\b\s+(?:-a\s+)?" + FILE, cmdline)
        or re.search(r">>?\s*" + FILE, tail)
    )
    if not writes_file:
        return None
    # Exemptions where a real shell is genuinely needed.
    if re.search(r"\bsudo\b", cmd):
        return None
    if re.search(r"\$\(|\$\{|`", cmd):
        return None
    # Only the worst offenders.
    if cmd.count("\n") < 12:
        return None
    return ("Large heredoc writing to a file detected. Use the Write tool instead of "
            "`cat/tee >file <<EOF` — the heredoc body costs tokens twice (call + result) "
            "and clutters the filesystem. Write the same content with the Write tool. "
            "(If this genuinely needs shell features — substitution, sudo, or piping to a "
            "command rather than a file — adjust the command so it isn't a plain heredoc→file write.)")


def check_cd_then_git(cmd):
    # `cd <path> && git …` / `cd <path>; git …` — the #1 wasteful command shape.
    # `git -C <path>` is always equivalent and avoids the cd approval prompt.
    if re.match(r"\s*cd\s+\S+\s*(&&|;)", cmd) and re.search(r"\bgit\s", cmd):
        return ("`cd <path> && git …` is blocked — use `git -C <path> …` instead. "
                "`cd` triggers approval prompts and can run untrusted hooks; the working "
                "directory already persists between tool calls. (If you genuinely need a "
                "multi-step shell session in that dir, run it yourself.)")
    return None


# --- secret / IP leak guard -------------------------------------------------
# Rationale (from the /insights audit): a real session leaked an ingress origin
# IP into a public-repo comment, and another tried to persist ClickHouse creds.
# But Zach's normal infra work is FULL of internal IPs (nebula 10.x, LAN
# 192.168.x, NodePort IPs) and $VAR-referenced creds, so a blanket scan would
# be pure false positives. Two surgical rules instead:
#   1. A private-key BLOCK in a command  -> deny outright (never a legit arg).
#   2. A secret token OR a *public* (globally-routable) IP -> deny ONLY when the
#      command also publishes (git commit / git notes / gh pr|issue|release|gist).
# Internal/RFC1918/loopback/CGNAT IPs are exempt via ipaddress.is_global, and
# creds referenced as $VARS or written to config files (no publish sink) pass.

PUBLISH_SINK = re.compile(
    r"\bgit\s+commit\b"
    r"|\bgit\s+notes\s+add\b"
    r"|\bgh\s+(?:pr|issue)\s+(?:create|comment|edit|review)\b"
    r"|\bgh\s+release\s+create\b"
    r"|\bgh\s+gist\s+create\b"
)

# High-confidence secret shapes (near-zero false-positive). Gated behind a
# publish sink so deliberate cred-file writes (~/.config/*/env) still pass.
SECRET_PATTERNS = [
    (r"\bAKIA[0-9A-Z]{16}\b", "an AWS access key id"),
    (r"\bASIA[0-9A-Z]{16}\b", "an AWS temporary access key id"),
    (r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "a GitHub token"),
    (r"\bgithub_pat_[A-Za-z0-9_]{40,}\b", "a GitHub fine-grained PAT"),
    (r"\bglpat-[A-Za-z0-9_-]{20,}", "a GitLab token"),
    (r"\bsk-ant-[A-Za-z0-9_-]{20,}", "an Anthropic API key"),
    (r"\bsk-or-v1-[A-Za-z0-9]{20,}", "an OpenRouter API key"),
    (r"\bsk-proj-[A-Za-z0-9_-]{20,}", "an OpenAI project key"),
    (r"\bxox[baprs]-[A-Za-z0-9-]{10,}", "a Slack token"),
    (r"\bAIza[0-9A-Za-z_-]{35}\b", "a Google API key"),
]

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _public_ips(text):
    found = []
    for m in IPV4_RE.finditer(text):
        try:
            ip = ipaddress.ip_address(m.group(0))
        except ValueError:
            continue  # octet > 255 etc. -> a version string, not an IP
        if ip.is_global and not ip.is_multicast:
            found.append(m.group(0))
    return found


def check_private_key(cmd):
    if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", cmd):
        return ("This command contains a PRIVATE KEY block. Private keys must never appear "
                "inline in a shell command (they leak into process args, logs, and shell "
                "history). Reference the key by file path instead. If you truly need this, "
                "run it yourself.")
    return None


def check_secret_or_ip_publish(cmd):
    if not PUBLISH_SINK.search(cmd):
        return None
    for pat, label in SECRET_PATTERNS:
        if re.search(pat, cmd):
            return (f"This command commits/posts to a repo and contains what looks like {label}. "
                    "Your RULES forbid writing credentials into repos, commits, or PR/issue "
                    "comments — reference an env var or secret store instead. If it's a false "
                    "positive, run the command yourself.")
    ips = _public_ips(cmd)
    if ips:
        return (f"This command commits/posts to a repo and contains a public IP address "
                f"({ips[0]}). Origin/ingress IPs must not land in a public repo (a leaked "
                "ingress IP already needed remediation once). Scrub it or use a hostname/"
                "placeholder. If the IP is intentional and non-sensitive, run it yourself.")
    return None


CHECKS = [check_git_add_all, check_git_reset_hard, check_heredoc_to_file,
          check_cd_then_git, check_private_key, check_secret_or_ip_publish]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (data.get("tool_input") or {}).get("command", "")
    if not cmd:
        sys.exit(0)
    for chk in CHECKS:
        reason = chk(cmd)
        if reason:
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }))
            sys.exit(0)
    sys.exit(0)


main()
