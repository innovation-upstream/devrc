#!/usr/bin/env python3
"""Caller-agnostic command guard: the shared core behind BOTH agent harnesses.

WHY THIS EXISTS
---------------
opencode's permission system matches GLOB PATTERNS against a command node's full
text. Two rounds of patching those patterns each closed the spellings we thought
of and left the ones we did not. Measured holes at devrc c1e4c02:

  * the ask block required tool and verb to be ADJACENT (`"*kubectl delete*"`),
    so `kubectl -n foo delete pod` and — worse — `talosctl -n <ip> reset` (a node
    wipe) resolved ALLOW;
  * the `review` agent's `git -C * diff*` allow-list sat after its own
    `"*": deny`, and the middle `*` is greedy across spaces, so
    `git -C <path> stash push -m 'wip on the diff'` EXECUTED (verified: it
    created a stash; the same command without the word "diff" was denied).

Glob matching over full command text cannot express "this command wipes a node"
— the set of spellings is unbounded. So the durable guard PARSES: it splits a
command line into its constituent commands, strips `VAR=…` prefixes and
`sudo`/`doas`/`env`/`timeout`/… wrappers, recurses into `bash -c '…'`, and then
reasons about argv[0] and the tokens — never about adjacency.

TWO CALLERS, TWO POLICIES
-------------------------
`POLICIES` maps a name to an ordered list of checks:

  "claude-code" — the checks bash-guard.py runs. This policy is FROZEN by
                  deliberate decision: bash-guard.py fires on every Bash call in
                  every Claude Code session on both hosts, so a new deny here
                  changes the operator's primary tool. Adding to it is a
                  decision for the operator, not a side effect of hardening
                  opencode. It was the original six raw-text checks until
                  2026-08-02, when `check_git_reset_hard_argv` was added as
                  exactly such an approved decision (the raw-text reset check
                  was blind to `git -C <path> reset --hard`, the spelling
                  RULES.md mandates), followed the same day by
                  `check_git_stash` + `check_git_clean_force` — both 🔴 in
                  RULES.md, both measured ALLOW against the live hook, and
                  neither with a benign in-repo use. Pinned by name in
                  test_guard_core.py.
  "opencode"    — all of the above, plus the irreversible-action checks below. opencode
                  agents run unattended (`opencode run` AUTO-REJECTS an `ask`
                  rather than prompting — measured on 1.18.4), so a hard deny is
                  the only thing standing between the model and an irreversible
                  action.

A check is `f(cmd) -> reason|None`. Adding one to "opencode" is additive; adding
one to "claude-code" is a behaviour change to report.

DESIGN NOTE — do NOT add "message text" stripping.
--------------------------------------------------
The six ORIGINAL checks match the RAW command string, so a command that merely
QUOTES a blocked shape (a commit message documenting "never git add -A") is
blocked too. That false positive is DELIBERATELY accepted.

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

🔴 The argv-based checks below are NOT a return of that helper, and the
distinction is load-bearing. They never BLANK bytes and never decide which parts
of a string are inert. They tokenise, and a quoted `-m` argument is simply ONE
TOKEN that is not argv[0] — so `git commit -m "never talosctl reset a node"`
carries argv[0]=`git` and no `talosctl` command exists to check. Nothing that
the raw-text checks blocked becomes allowed: the argv checks only ever ADD
denies, and they run alongside the untouched originals.

KNOWN LIMITS (deliberate, not oversights)
-----------------------------------------
  * `ssh host talosctl reset` is not inspected — the guard reasons about what
    runs HERE, and treating an arbitrary remote argv as local is a bigger
    over-block than it is worth.
  * `xargs talosctl reset`, and argv assembled from variables (`$CMD reset`),
    are not caught. The command text does not carry the value.
  * recursion into `bash -c` is bounded at depth 3.
These are gaps in COVERAGE, not bypasses of anything the globs used to hold: the
opencode config keeps a broad `ask` over the same families, so an uncovered
spelling still meets friction rather than silence.
"""
import sys, os, json, re, shlex, ipaddress


# =========================================================================== #
# ORIGINAL SIX — raw-text checks, byte-for-byte the behaviour bash-guard.py has
# always had. Do not modify without re-running scripts/claude-hooks/tests/
# test_bash_guard.py, which is the regression suite for every bypass they close.
#
# NOTE: "the original six" describes the functions DEFINED in this section, not
# the contents of the claude-code policy — that policy also carries the argv
# check `check_git_reset_hard_argv` since 2026-08-02. See POLICIES at the bottom.
# =========================================================================== #

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
# `git [global-opts] add|stage <args>`. The global-opts hop matters: `git -C
# <dir> add -A` is the shape check_cd_then_git and RULES actively PUSH agents
# toward, so a bare `\bgit\s+add\b` anchor made this guard self-defeating —
# real transcripts contain blind `git -C "$WT" add -A` calls it never saw.
# `stage` is git's documented synonym for `add`.
_GIT_ADD = re.compile(
    r"\bgit\s+"
    r"(?:(?:-[Cc]\s+\S+"
    r"|--(?:git-dir|work-tree|namespace|exec-path)(?:=\S+|\s+\S+)"
    r"|-[pP]|--paginate|--no-pager|--bare|--literal-pathspecs)\s+)*"
    r"(?:add|stage)\b([^&|;\n]*)"
)

# Tokens that stage the whole tree. Checked per-TOKEN rather than with a
# whitespace-bounded regex: `(git add -A)` and `git add -A;` defeated the
# boundary, and nested `[A-Za-z]*` around the A backtracked quadratically
# (6s on a 32k run of A's, on a hook that runs for every Bash call).
# Short bundles: -A is the only uppercase-A short option `git add` has, so any
# all-letters dash-token containing A is blind (-Av, -vA, -uA).
_BLIND_SHORT = re.compile(r"-[A-Za-z]+")
# git accepts unique long-option PREFIXES: --al == --all, --no-ignore-r == …removal.
_BLIND_LONG = re.compile(r"--al|--all|--no-ignore-r[a-z-]*")
# Whole-tree pathspecs.
#
# `..` IS included. Measured: it errors at the repo ROOT ("outside repository"),
# stages the WHOLE TREE from a depth-1 subdirectory, and stages only the parent
# directory from depth >= 2 (from `a/b/` it stages `a/`). So blocking it is
# exact at depth 1, harmless at the root (git rejects it anyway), and a
# fail-closed over-block deeper down — which the "err toward over-matching"
# stance above accepts. It appears 0 times in 32k real commands.
# (An earlier root-only test led to it being excluded, then a depth-1-only
# test led to the claim that it is always whole-tree. Both were generalised
# from a single depth. Measure at 0, 1 and 2 before touching this.)
#
# KNOWN LIMITATION, deliberately not chased: whether `X/..` is whole-tree
# depends on runtime cwd and repo-root depth, which the command text does not
# carry — `a/..` at the root is the whole tree, `a/b/..` is just `a/`, and
# staging `a/` is allowed. Same for `$OLDPWD` and
# `$(git rev-parse --show-toplevel)`, which the per-token split shatters
# anyway. Guessing at these is the trap the DESIGN NOTE records.
_TAIL = r"(?:/[./]*)?"              # trailing / ./ .// ./. on any stem
_BLIND_PATH = re.compile(
    r"\.{1,2}" + _TAIL              # . ./ .// ./. .. ../ ../..
    + r"|:/\*?|\*"                  # :/ :/* *
    + r"|\$\{?PWD\}?" + _TAIL       # $PWD ${PWD} $PWD/ $PWD// $PWD/./
    + r"|\$\(pwd\)" + _TAIL         # $(pwd) $(pwd)// …
    + r"|`pwd`" + _TAIL             # `pwd` `pwd`/./ …
    + r"|:\(top[a-z,]*\)\*?"        # :(top) :(top,glob) :(top,icase)
    + r"|:"                         # bare `:` is the whole repo
)


def _stages_everything(argtext):
    # The shell removes quotes, backslashes and the $ of $'…' before git sees
    # the word; do the same, or `git add -\A` / `git add $'-A'` slip past.
    t = re.sub(r"\$(?=['\"])", "", argtext)
    t = t.replace('"', "").replace("'", "").replace("\\", "")
    after_ddash = False
    for raw in t.split():
        if raw == "--":
            after_ddash = True          # everything past this is a PATH, not a flag
            continue
        # A subshell's closing paren sticks to the last token (`(git add -A)`),
        # as does a trailing `;`. Test both forms rather than truncating the
        # capture at `)`, which would hide a real flag after `$(…)`.
        # `$(pwd)` still matches via the unstripped form.
        for tok in {raw, raw.rstrip(");")}:
            if not tok:
                continue
            if not after_ddash:
                if _BLIND_SHORT.fullmatch(tok) and "A" in tok:
                    return True
                if _BLIND_LONG.fullmatch(tok):
                    return True
            if _BLIND_PATH.fullmatch(tok):
                return True
    return False


def check_git_add_all(cmd):
    for m in _GIT_ADD.finditer(cmd):
        if _stages_everything(m.group(1)):
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


# =========================================================================== #
# THE PARSER
#
# Everything below reasons about argv, never about adjacency in the raw text.
# =========================================================================== #

# Operators that end one command and start another. `&` is included: it was the
# separator whose absence became a live bypass in the reverted stripper (r2).
_SEPARATORS = ("&&", "||", ";;", ";", "|", "&", "\n")

# Wrappers that PRECEDE a real command. Stripping them is the whole point of
# the exercise: `sudo talosctl reset` and `FOO=1 env talosctl reset` are the
# same action as `talosctl reset`, and no glob spelling enumerates them all.
#
# 🔴 FLAG ARITY IS PER-WRAPPER, and getting it wrong FAILS OPEN. A single shared
# value-flag set had `-n` in it for `nice -n 5`; that made `sudo -n talosctl
# reset` peel to `['reset']`, and the node-wipe guard silently stopped firing on
# the most ordinary sudo spelling there is. The multi-spelling matrix in
# tests/test_guard_core.py is what caught it — one canonical spelling per rule
# would not have. Hence: exact per-wrapper tables, PLUS `_peel_variants` below,
# which branches on the other arity interpretation so a future table error
# cannot go silently open.
_WRAPPER_VALUE_FLAGS = {
    "sudo": {"-u", "--user", "-g", "--group", "-U", "-C", "--close-from",
             "-p", "--prompt", "-r", "--role", "-t", "--type", "-h", "--host",
             "-D", "--chdir", "-R", "--chroot"},
    "doas": {"-u", "-C"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid"},
    "timeout": {"-k", "--kill-after", "-s", "--signal"},
    "chrt": {"-p", "--pid"},
    "stdbuf": {"-i", "--input", "-o", "--output", "-e", "--error"},
    "exec": {"-a"},
    "nohup": set(), "setsid": set(), "command": set(), "builtin": set(),
}
_WRAPPERS = set(_WRAPPER_VALUE_FLAGS)
# Positional arguments the wrapper itself consumes before the wrapped command
# (`timeout 90 cmd`, `chrt 50 cmd`).
_WRAPPER_POSITIONAL = {"timeout": 1, "chrt": 1}

_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "busybox"}


def split_commands(text):
    """Split shell text into command segments, respecting quotes.

    Also yields the BODY of every `$( … )` / backtick substitution as its own
    text, because a substitution body really does execute. Linear scan — no
    regex, so no backtracking to worry about on a guard that runs per call.
    """
    segs, subs = [], []
    buf = []
    i, n = 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        if quote:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < n:
                buf.append(text[i + 1]); i += 2; continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "\\" and i + 1 < n:
            buf.append(c); buf.append(text[i + 1]); i += 2; continue
        if c in ("'", '"'):
            quote = c; buf.append(c); i += 1; continue
        if c == "`":
            j = text.find("`", i + 1)
            if j == -1:
                j = n
            subs.append(text[i + 1:j])
            i = j + 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            depth, j = 1, i + 2
            while j < n and depth:
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            subs.append(text[i + 2:j - 1 if depth == 0 else n])
            i = j
            continue
        if c in "()":                 # subshell parens are not part of argv
            segs.append("".join(buf)); buf = []; i += 1; continue
        matched = None
        for op in _SEPARATORS:
            if text.startswith(op, i):
                matched = op
                break
        if matched:
            segs.append("".join(buf)); buf = []; i += len(matched); continue
        buf.append(c); i += 1
    segs.append("".join(buf))
    return [s for s in segs + subs if s.strip()]


def _tokenise(seg):
    """argv for one segment. Falls back to a quote-stripping whitespace split
    when the text is not lexable (unbalanced quote from a truncated heredoc,
    say) — a guard must not go blind just because the input is malformed."""
    try:
        return shlex.split(seg, posix=True)
    except ValueError:
        return [t.strip("\"'") for t in seg.split() if t.strip("\"'")]


_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


_PEEL_VARIANT_LIMIT = 8


def _peel_variants(argv):
    """Every plausible peeling of `VAR=…` prefixes and wrapper commands.

    This is the function the whole design turns on. `KUBECONFIG=$KC_HOMELAB
    sudo -n timeout 60 talosctl -n 1.2.3.4 reset` peels to
    `['talosctl', '-n', '1.2.3.4', 'reset']`, which is the same action as a bare
    `talosctl reset` and which no glob spelling enumerates.

    Usually there is exactly one peeling. A dash-flag inside a WRAPPER's own
    option region branches into two — value-taking and not — because a wrong
    arity guess fails OPEN (see the `sudo -n` note above). The extra
    interpretations are argv suffixes, so they can only ever ADD a deny; the
    cost is a bounded over-block on contrived shapes like
    `sudo grep -e talosctl reset`, which is the correct side to err on.
    Element 0 is always the primary (table-driven) peeling.
    """
    out, seen, stack = [], set(), [(0, None)]
    while stack and len(out) < _PEEL_VARIANT_LIMIT:
        i, wrapper = stack.pop(0)
        while i < len(argv):
            tok = argv[i]
            if _ASSIGN.match(tok):
                i += 1
                continue
            base = os.path.basename(tok)
            if base in _WRAPPERS:
                wrapper, i = base, i + 1
                continue
            if wrapper and tok == "--":
                i += 1
                continue
            if wrapper and tok.startswith("-") and tok != "-":
                takes_value = "=" not in tok and tok in _WRAPPER_VALUE_FLAGS.get(wrapper, ())
                alt = i + (1 if takes_value else 2)
                if alt <= len(argv):
                    stack.append((alt, wrapper))
                i += 2 if takes_value else 1
                continue
            if wrapper and _WRAPPER_POSITIONAL.get(wrapper):
                # Branch here too, for the same fail-open reason as the flag
                # arity above: if the positional count is wrong (or a preceding
                # flag already ate the duration), consuming it swallows the real
                # command. Found by the generated (wrapper, value-flag) matrix —
                # `timeout -k talosctl reset` peeled to `['reset']` through BOTH
                # branches, because the alternate still hit this consumption.
                stack.append((i, None))
                i += _WRAPPER_POSITIONAL[wrapper]
                wrapper = None
                continue
            break
        cand = tuple(argv[i:])
        if cand and cand not in seen:
            seen.add(cand)
            out.append(list(cand))
    return out


def _nested_shell_text(argv):
    """Command text carried INSIDE this argv, if any (`bash -c '…'`, `eval …`)."""
    if not argv:
        return []
    base = os.path.basename(argv[0])
    if base in _SHELLS:
        for j, tok in enumerate(argv[1:], 1):
            if tok == "-c" and j + 1 < len(argv):
                return [argv[j + 1]]
            if tok.startswith("-") and "c" in tok[1:] and not tok.startswith("--") and j + 1 < len(argv):
                return [argv[j + 1]]
        return []
    if base == "eval":
        return [" ".join(argv[1:])] if len(argv) > 1 else []
    return []


def commands(text, _depth=0):
    """Every real argv the command line would execute, wrappers peeled.

    Recurses into `bash -c '…'` / `eval …` bodies (bounded at depth 3 so a
    hand-crafted nesting bomb cannot spin the guard).
    """
    out = []
    for seg in split_commands(text):
        for argv in _peel_variants(_tokenise(seg)):
            out.append(argv)
            if _depth < 3:
                for nested in _nested_shell_text(argv):
                    out.extend(commands(nested, _depth + 1))
    return out


def _flags_and_operands(argv):
    """(set of flag tokens, list of operand tokens) for a POSIX-ish argv.
    Everything after a bare `--` is an operand, flags included."""
    flags, operands, after = set(), [], False
    for tok in argv[1:]:
        if after:
            operands.append(tok); continue
        if tok == "--":
            after = True; continue
        if tok.startswith("-") and tok != "-":
            flags.add(tok)
        else:
            operands.append(tok)
    return flags, operands


# =========================================================================== #
# ARGV-BASED CHECKS — the irreversible families globs handle badly.
#
# NOT all opencode-only any more. `check_git_reset_hard_argv` (2026-08-02),
# `check_git_stash` and `check_git_clean_force` (2026-08-02) run under BOTH
# policies; talosctl/mkfs/dd/rm stay opencode-only. POLICIES at the bottom is
# the authority — read it rather than assuming from this section heading.
# =========================================================================== #

def check_talosctl_reset(cmd):
    """Wipes a Talos node.

    🔴 This is the check the glob approach could not express. `*talosctl reset*`
    requires the tool and the verb to be ADJACENT, so the house-style spelling
    `talosctl -n <ip> reset` — the ONLY spelling that targets a specific node,
    i.e. the one an operator actually types — resolved ALLOW.

    Deliberately over-broad: ANY bare `reset` operand in a talosctl argv denies.
    Enumerating which talosctl flags take a value is a moving target across
    versions, and getting it wrong fails OPEN. `talosctl get resetstatus` is not
    a bare `reset` token, so the obvious false positive does not occur.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "talosctl":
            continue
        if any(tok == "reset" for tok in argv[1:]):
            return ("`talosctl reset` is blocked — it WIPES a Talos node (it erases the "
                    "machine's disks and returns it to maintenance mode). There is no undo and "
                    "no prompt worth clicking through. If a node genuinely must be reset, run it "
                    "yourself with the exact `--system-labels-to-wipe` / `--graceful` flags you "
                    "intend. (This guard parses argv, so `talosctl -n <ip> reset`, "
                    "`talosctl --nodes <ip> reset` and `sudo talosctl reset` are all caught.)")
    return None


_MKFS = re.compile(r"^(?:mkfs(?:\.[A-Za-z0-9_-]+)?|mke2fs|mkswap|newfs|mkntfs|mkdosfs)$")


def check_mkfs(cmd):
    """Formats a filesystem — destroys every byte on the target device."""
    for argv in commands(cmd):
        base = os.path.basename(argv[0])
        if _MKFS.match(base):
            return (f"`{base}` is blocked — it FORMATS a filesystem and destroys everything on "
                    "the target device. No agent on these hosts has a reason to run it. If you "
                    "are provisioning a disk, do it yourself with the device path in front of "
                    "you.")
    return None


# `dd of=` targets that are safe sinks rather than storage.
_DD_SAFE_DEV = re.compile(r"^/dev/(?:null|zero|stdout|stderr|stdin|tty|full|random|urandom|fd/\d+)$")


def check_dd_to_block_device(cmd):
    """`dd of=/dev/sdX` overwrites a disk in place. `of=/dev/null` is fine."""
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "dd":
            continue
        for tok in argv[1:]:
            if not tok.startswith("of="):
                continue
            target = tok[3:].strip("\"'")
            if target.startswith("/dev/") and not _DD_SAFE_DEV.match(target):
                return (f"`dd of={target}` is blocked — writing to a block device overwrites the "
                        "disk in place, past every filesystem and every backup tool. If you are "
                        "imaging a device, run it yourself. (`of=/dev/null` and the other pseudo "
                        "sinks are allowed.)")
    return None


# Targets whose recursive deletion is catastrophic and never intended. Compared
# against the token AS WRITTEN, with quotes already removed by the tokeniser —
# `rm -rf "$HOME"` and `rm -rf $HOME` are the same token here.
_RM_FATAL = {
    "/", "/*", "~", "~/", "~/*",
    "$HOME", "${HOME}", "$HOME/", "${HOME}/", "$HOME/*", "${HOME}/*",
    ".", "./", "..", "../",
}
_RM_FATAL_DIRS = {"/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64",
                  "/nix", "/opt", "/proc", "/root", "/run", "/sbin", "/srv",
                  "/sys", "/usr", "/var"}


def _is_fatal_rm_target(tok):
    if tok in _RM_FATAL:
        return True
    stem = tok[:-2] if tok.endswith("/*") else tok.rstrip("/") or "/"
    return stem in _RM_FATAL_DIRS


def check_rm_rf_critical(cmd):
    """Recursive delete of `/`, `$HOME`/`~`, cwd, or a top-level system dir.

    Requires the RECURSIVE flag: without `-r` these targets are directories and
    `rm` refuses. The flag test walks bundles (`-rf`, `-fr`, `-Rf`) rather than
    matching a literal `rm -rf`, which is what the glob did — and which
    `rm -f -r /` walked straight past.

    NOTE a deliberate NARROWING vs. the old glob `"*rm -rf /*": deny`: that
    pattern also denied `rm -rf /tmp/scratch`, because `*` crosses `/`. With a
    parser the fatal set can be exact, so an ordinary absolute-path cleanup now
    resolves to the config's broad `rm -rf` ASK instead of a deny. That is a
    real loosening for `/tmp/...`-shaped targets and is recorded here so it is
    a decision rather than an accident.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "rm":
            continue
        flags, operands = _flags_and_operands(argv)
        recursive = any(
            f == "--recursive" or (not f.startswith("--") and re.search(r"[rR]", f[1:]))
            for f in flags
        )
        if not recursive:
            continue
        for tok in operands:
            if _is_fatal_rm_target(tok):
                return (f"`rm -r` targeting {tok!r} is blocked — that is your home directory, the "
                        "filesystem root, the current directory, or a top-level system directory. "
                        "Deleting it recursively is unrecoverable. Name the specific "
                        "subdirectory you mean.")
    return None


def check_git_stash(cmd):
    """The stash stack is repo-GLOBAL across worktrees.

    RULES 🔴: never `git stash` in a repo shared with other sessions/agents —
    `refs/stash` lives in the COMMON git dir, so a worktree gives ZERO
    isolation and a concurrent agent pops your entry (observed twice). The
    READ-ONLY subcommands (`list`, `show`) are explicitly allowed: `git stash
    list` is the diagnostic RULES tells you to run.

    Parsing matters here for the exact reason the audit found: the `review`
    agent's `git -C * diff*` allow-list let `git -C <path> stash push -m 'wip on
    the diff'` EXECUTE, because the glob's middle `*` is greedy across spaces.
    argv[1] is `stash` regardless of how many global options precede it.

    Enabled for BOTH policies since 2026-08-02 (it shipped opencode-only). It
    lives in `_CLAUDE_CODE_CHECKS`, which `POLICIES["opencode"]` includes, so
    opencode keeps coverage by inheritance rather than a duplicate entry.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "git":
            continue
        _, operands = _flags_and_operands(_git_strip_global_opts(argv))
        if not operands or operands[0] != "stash":
            continue
        sub = operands[1] if len(operands) > 1 else "push"
        if sub in ("list", "show"):
            continue
        return ("`git stash` is blocked by your RULES. The stash stack is repo-GLOBAL: "
                "`refs/stash` lives in the COMMON git dir, so being in your own worktree gives "
                "ZERO isolation and a concurrent agent or session can pop your entry (observed "
                "twice — two parallel subagents stole each other's work). To set work aside, COPY "
                "it aside (`cp <file> /tmp/…`) or commit it to a throwaway branch. "
                "`git stash list` and `git stash show` are reads and remain allowed. "
                "(Only QUOTING this command — e.g. a heredoc body documenting the ban, whose "
                "lines this guard parses as real commands? Write the text to a file with the "
                "Write tool and use `git commit -F <file>` / `gh pr create --body-file <file>` — "
                "which your RULES prefer over heredocs anyway.)")
    return None


def check_git_reset_hard_argv(cmd):
    """`git reset --hard` through a global-option hop.

    🔴 A GAP THIS WORK FOUND, not one it was sent to fix. The original
    `check_git_reset_hard` is a raw-text regex anchored on `\\bgit\\s+reset\\b`,
    so `git -C <path> reset --hard` — the worktree-first spelling RULES.md
    mandates — does NOT match it. Measured at c1e4c02: that command resolved
    ALLOW under the `review` agent's glob list AND was passed by the Claude Code
    guard.

    Enabled for BOTH policies since 2026-08-02. It first shipped opencode-only,
    because switching it on for "claude-code" is a new deny on the operator's
    primary tool and was theirs to approve; they approved it. It now lives in
    `_CLAUDE_CODE_CHECKS`, which `POLICIES["opencode"]` includes, so opencode
    keeps its coverage by inheritance rather than by a duplicate entry.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "git":
            continue
        flags, operands = _flags_and_operands(_git_strip_global_opts(argv))
        if operands and operands[0] == "reset" and "--hard" in flags:
            return ("`git reset --hard` is blocked by your RULES (irreversibly destroys "
                    "uncommitted work). Use `git restore <path>` / `git checkout -- <path>` for "
                    "specific files, or `git checkout <ref> -- <paths>` to take another ref's "
                    "version. Do NOT reach for `git stash` — the stash stack is repo-GLOBAL "
                    "across worktrees. (This argv check also catches the `git -C <path> reset "
                    "--hard` spelling, which the raw-text check misses.)")
    return None


def check_git_clean_force(cmd):
    """`git clean -f` deletes untracked files — including the un-committed docs
    RULES warns are one routine command away from silent loss.

    Enabled for BOTH policies since 2026-08-02 (it shipped opencode-only), by
    inheritance from `_CLAUDE_CODE_CHECKS`. The dry-run spellings (`-n`,
    `--dry-run`) are untouched: `git clean -nd` is the diagnostic the deny
    message points at, so denying it would push the operator toward guessing.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "git":
            continue
        stripped = _git_strip_global_opts(argv)
        flags, operands = _flags_and_operands(stripped)
        if not operands or operands[0] != "clean":
            continue
        if any(f == "--force" or (not f.startswith("--") and "f" in f[1:]) for f in flags):
            return ("`git clean -f` is blocked — it permanently deletes untracked files, and in "
                    "this repo untracked files are routinely real work (handoff docs, scratch "
                    "analyses) rather than junk. Run `git clean -nd` to see what it would remove, "
                    "then delete the specific paths you mean. "
                    "(Only QUOTING this command — e.g. a heredoc body documenting the ban, whose "
                    "lines this guard parses as real commands? Write the text to a file with the "
                    "Write tool and use `git commit -F <file>` / `gh pr create --body-file "
                    "<file>` — which your RULES prefer over heredocs anyway.)")
    return None


# git's global options, which sit BETWEEN `git` and the subcommand. This is the
# hop that every glob spelling kept missing: `git -C <dir> stash` has `stash` at
# argv[3], not argv[1].
_GIT_GLOBAL_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                          "--exec-path", "--super-prefix"}


def _git_strip_global_opts(argv):
    """Return `['git', <subcommand>, …]` with global options removed."""
    out, i = [argv[0]], 1
    while i < len(argv):
        tok = argv[i]
        if tok in _GIT_GLOBAL_VALUE_OPTS:
            i += 2; continue
        if tok.startswith("--") and "=" in tok and tok.split("=", 1)[0] in _GIT_GLOBAL_VALUE_OPTS:
            i += 1; continue
        if tok.startswith("-"):
            i += 1; continue
        break
    return out + argv[i:]


# =========================================================================== #
# POLICIES
# =========================================================================== #

# 🔴 FROZEN. bash-guard.py runs this on EVERY Bash call in EVERY Claude Code
# session on both hosts. Adding to this list is a change to the operator's
# primary tool and must be an explicit, reported decision — not a side effect
# of hardening opencode.
#
# For a long time this was exactly SIX raw-text checks. `check_git_reset_hard_argv`
# is the seventh, added 2026-08-02 as such an explicit decision: the raw-text
# `check_git_reset_hard` is anchored on `\bgit\s+reset\b`, so it never saw
# `git -C <path> reset --hard` — the worktree-first spelling RULES.md actively
# MANDATES. Measured against the live hook before the move: `git reset --hard
# origin/main` denied, `git -C /tmp/wt reset --hard origin/main` ALLOWED. The
# irreversible half of the guard was blind to the spelling the rules push agents
# toward. `check_git_add_all` already handled that same global-option hop; this
# brings reset --hard into line.
#
# Both reset checks are kept. They are not redundant: the raw-text one matches
# shapes the parser cannot reach (quoted prose, argv assembled from variables),
# and the argv one matches hops the regex cannot reach. `evaluate` returns on the
# FIRST hit, so a command matching both is reported ONCE, by the raw-text check
# — which is why every deny message the raw-text check already produced is
# unchanged.
#
# ORDER MATTERS, and it changes one message. The argv check is placed BEFORE
# check_cd_then_git, so `cd /x && git -C <p> reset --hard` now reports the
# reset --hard reason instead of the `cd`-then-git reason. The DECISION is
# unchanged (it denied before and denies now, under both policies) — only the
# text differs, and it now names the more serious of the two problems. That is
# the sole message change; it is asserted in test_guard_core.py.
#
# 🔴 `check_git_stash` and `check_git_clean_force` were added 2026-08-02 as the
# EIGHTH and NINTH checks, by the same kind of explicit operator decision.
# Measured against the live hook before the move (PreToolUse JSON piped into
# ~/.claude/hooks/bash-guard.py, with `git add -A` and `git reset --hard HEAD`
# as positive controls that came back DENY):
#     ALLOW  git stash
#     ALLOW  git stash push -m wip
#     ALLOW  git stash pop
#     ALLOW  git -C <repo> stash
#     ALLOW  git clean -fd
# i.e. the two operations RULES.md treats as 🔴 CRITICAL data-loss — `git stash`
# with a documented incident (two parallel subagents stole each other's work,
# 2026-07-25; the ban was re-BROADENED 2026-08-01 after a subagent read the
# narrow wording and stashed anyway) and `git clean -f`, which deletes exactly
# the untracked handoff docs RULES calls unsaved work — were enforced for
# opencode and silently allowed for the operator's primary tool.
#
# They sit BEFORE check_cd_then_git for the same reason the reset argv check
# does: `cd /x && git stash` now reports the stash reason, which names the more
# serious of the two problems.
#
# The four remaining families (talosctl reset / mkfs / dd / rm -rf) stay
# opencode-ONLY and were deliberately NOT moved here — see _IRREVERSIBLE_CHECKS.
_CLAUDE_CODE_CHECKS = [
    check_git_add_all,
    check_git_reset_hard,
    check_git_reset_hard_argv,
    check_git_stash,
    check_git_clean_force,
    check_heredoc_to_file,
    check_cd_then_git,
    check_private_key,
    check_secret_or_ip_publish,
]

# The irreversible families that remain opencode-ONLY: `opencode run`
# AUTO-REJECTS an `ask` rather than prompting (measured on 1.18.4), and the
# interactive TUI is the only place a human sees one — so for an unattended
# agent a hard deny is the only real control.
#
# 🔴 These four are NOT in the claude-code policy, and that narrowness is a
# DECISION, not an oversight. `rm -rf` in particular risks false positives on
# legitimate build-directory cleanup, and the other three are hardware/cluster
# operations an interactive Claude Code session can be prompted about. Widening
# any of them to claude-code is a separate operator decision.
_IRREVERSIBLE_CHECKS = [
    check_talosctl_reset,
    check_mkfs,
    check_dd_to_block_device,
    check_rm_rf_critical,
]

POLICIES = {
    "claude-code": list(_CLAUDE_CODE_CHECKS),
    "opencode": list(_CLAUDE_CODE_CHECKS) + list(_IRREVERSIBLE_CHECKS),
}


def evaluate(cmd, policy="claude-code"):
    """Return a deny reason, or None to allow. Unknown policy raises — a typo
    must not silently degrade to "no checks"."""
    if policy not in POLICIES:
        raise KeyError(f"unknown guard policy {policy!r}; known: {sorted(POLICIES)}")
    for chk in POLICIES[policy]:
        reason = chk(cmd)
        if reason:
            return reason
    return None


# =========================================================================== #
# CLI — the seam the opencode plugin calls.
#
# stdin : {"command": "<shell text>"}
# stdout: {"decision": "deny", "reason": "…"}  |  {"decision": "allow"}
# exit  : 0 on a verdict, 2 on bad input (the caller must fail CLOSED on 2).
# =========================================================================== #
def _cli(argv):
    policy = "claude-code"
    if "--policy" in argv:
        policy = argv[argv.index("--policy") + 1]
    try:
        data = json.load(sys.stdin)
        cmd = data.get("command") or ""
    except Exception as exc:
        print(json.dumps({"decision": "error", "reason": f"unreadable input: {exc}"}))
        return 2
    if not isinstance(cmd, str):
        print(json.dumps({"decision": "error", "reason": "command is not a string"}))
        return 2
    try:
        reason = evaluate(cmd, policy)
    except Exception as exc:
        print(json.dumps({"decision": "error", "reason": f"guard failed: {exc}"}))
        return 2
    print(json.dumps({"decision": "deny", "reason": reason} if reason
                     else {"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
