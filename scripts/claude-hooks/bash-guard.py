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
"""
import sys, json, re, ipaddress


# --- message-text stripping -------------------------------------------------
# The git-SHAPE checks below must not fire on a command that merely QUOTES a
# forbidden command inside a commit message or PR body. (Dogfood: the commit
# documenting "never git add -A" was itself blocked by that rule.) So strip
# message text before shape-matching.
#
# Deliberately narrow — a heredoc body is stripped ONLY when it feeds a
# message-carrying command (git commit / gh pr|issue|release|gist). A heredoc
# feeding a SHELL (`bash <<EOF … git add -A … EOF`) really would execute, so it
# is left intact and still blocked.
#
# NEVER applied to the secret/IP guard: a credential in a commit message is
# exactly what that check exists to catch.
# Commands whose message/body argument is inert text. `git notes add` is also in
# PUBLISH_SINK below; keep the two lists in mind together. Anything NOT listed
# here gets no stripping at all.
MESSAGE_CMD = re.compile(
    r"\bgit\s+commit\b"
    r"|\bgit\s+tag\b"
    r"|\bgit\s+notes\s+add\b"
    r"|\bgit\s+merge\b"
    r"|\bgit\s+stash\s+push\b"
    r"|\bgh\s+(?:pr|issue|release|gist)\b"
    r"|\b(?:jj\s+describe|hg\s+commit|svn\s+commit)\b"
)

# Segment separators used to find WHICH sub-command a heredoc operator belongs to.
# Substitution openers are included: a heredoc inside `$(…)`, `<(…)`, `>(…)` or
# backticks belongs to the INNER command, not to an outer `git commit` — without
# them `git commit -F <(bash <<'EOF' … EOF)` had its shell-feeding body stripped.
# `&` matters just as much: `git commit -m x & bash <<'EOF'` backgrounds the
# commit and feeds the heredoc to a REAL shell, so the body must stay visible.
# `&&` is listed before `&` so the two-char form wins.
# Deliberately NOT a bare `(`: that split `gh pr create --title "fix(x): …"` off
# from its own heredoc and re-blocked a legitimate body — the exact false
# positive this whole function exists to prevent, and this repo's commit style.
_SEGMENT = re.compile(r"\n|;|&&|&|\|\||\||\$\(|<\(|>\(|`")

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Quoted -m/--message values. Two DISJOINT patterns, deliberately:
#   * double quotes honour backslash escapes, as the shell does;
#   * single quotes do NOT — the shell has no escapes inside '…', so treating
#     `\'` as an escape let the match run past the closing quote and swallow a
#     following real command (`-m 'msg \' && git add -A`).
# Both use non-ambiguous character classes ([^"\\] / [^']) so a failed match
# cannot backtrack exponentially — the previous `(?:\\.|(?!\3).)*` was a ReDoS
# (an unterminated quote plus ~40 backslashes stalled for minutes, on a hook
# that runs on EVERY Bash call, three times per command).
# `--body`/`--title` matter as much as `-m`: `gh pr create --body '…'` is an
# extremely common shape. `--body-file` is NOT matched — the flag must be
# followed by `=` or whitespace, and `--body-file` continues with `-`.
_MSG_FLAG = r"(-m|-am|--message|--body|--title)"
_MSG_DQ = re.compile(_MSG_FLAG + r"(=|\s+)\"(?:\\.|[^\"\\])*\"")
_MSG_SQ = re.compile(_MSG_FLAG + r"(=|\s+)'[^']*'")

# A message value containing command substitution really can execute; never
# blank those out.
_SUBST = re.compile(r"\$\(|`")


def _strip_message_text(cmd):
    # Only message-carrying commands get ANY stripping — so `docker run -m '2g'`
    # is untouched, and the blast radius stays as small as possible.
    if not MESSAGE_CMD.search(cmd):
        return cmd
    out = cmd
    for pat in (_MSG_DQ, _MSG_SQ):
        out = pat.sub(lambda m: m.group(0) if _SUBST.search(m.group(0))
                      else m.group(1) + m.group(2) + "''", out)
    return _strip_heredoc_bodies(out)


def _strip_heredoc_bodies(out):
    """Blank the body of each heredoc that feeds a message-carrying command.

    Scans the MUTATED string with an advancing cursor. The previous version
    iterated `re.finditer` over the ORIGINAL string while rebinding `out` in the
    loop, so after the first substitution every later `m.start()` indexed a
    shifted string — which silently reclassified a shell-feeding heredoc as
    message text. It also used `.sub()` with no count, stripping EVERY heredoc
    sharing the tag rather than only the matched one.
    """
    pos = 0
    while True:
        m = _HEREDOC.search(out, pos)
        if not m:
            return out
        tag = m.group(2)
        cmdline = _SEGMENT.split(out[:m.start()])[-1]
        nl = out.find("\n", m.end())
        if nl == -1 or not MESSAGE_CMD.search(cmdline):
            pos = m.end()
            continue
        term = re.compile(r"^[ \t]*" + re.escape(tag) + r"[ \t]*$", re.M)
        t = term.search(out, nl + 1)
        if not t:
            # Unterminated heredoc: strip nothing, so anything that follows stays
            # visible to the shape checks.
            pos = m.end()
            continue
        # Slice out exactly this body — never a global substitution.
        out = out[:nl + 1] + out[t.start():]
        pos = nl + 1


def check_git_add_all(cmd):
    cmd = _strip_message_text(cmd)
    for m in re.finditer(r"\bgit\s+add\b([^&|;\n]*)", cmd):
        args = m.group(1)
        if re.search(r"(^|\s)(-A|--all)(\s|$)", args) or re.search(r"(^|\s)\.(\s|$)", args):
            return ("`git add -A` / `git add --all` / `git add .` is blocked by your RULES "
                    "(never blind-stage — it sweeps up unrelated working-tree changes and has "
                    "caused near-miss leaks). Stage specific paths instead: `git add <path> ...`.")
    return None


def check_git_reset_hard(cmd):
    cmd = _strip_message_text(cmd)
    if re.search(r"\bgit\s+reset\b[^&|;\n]*--hard\b", cmd):
        return ("`git reset --hard` is blocked by your RULES (irreversibly destroys uncommitted "
                "work). Use `git restore <path>` / `git checkout -- <path>` for specific files, "
                "or `git checkout <ref> -- <paths>` to take another ref's version. Do NOT reach "
                "for `git stash` — the stash stack is repo-GLOBAL across worktrees and a "
                "concurrent agent can pop yours (RULES: Git Workflow). If you truly need it, "
                "run it yourself.")
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
    cmd = _strip_message_text(cmd)
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
