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
                  neither with a benign in-repo use — and then, later the same
                  day, by `check_talosctl_reset` + `check_mkfs` +
                  `check_dd_to_block_device`, the three device/cluster-
                  destruction families, on the same evidence. Pinned by name in
                  test_guard_core.py.
  "opencode"    — all of the above, plus `check_rm_rf_critical`, which is now
                  the ONLY opencode-only check. opencode agents run unattended
                  (`opencode run` AUTO-REJECTS an `ask` rather than prompting —
                  measured on 1.18.4), so a hard deny is the only thing standing
                  between the model and an irreversible action. `rm -rf` was
                  deliberately held back from "claude-code" because it has
                  frequent legitimate use here — see _IRREVERSIBLE_CHECKS.

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

🔴 NEITHER IS THE HEREDOC HANDLING IN `_scan` (2026-08-11). It LIFTS a heredoc
body out of the surrounding quote state and then parses it as commands exactly
as before — nothing is blanked, nothing is declared inert, and `bash <<EOF` keeps
every deny it had. It exists because the opposite of the reverted helper was also
a bug: the scanner tracked quote state THROUGH the body, so one apostrophe in a
commit message (`don't`) opened a quote that never closed and swallowed every
command AFTER the heredoc. Measured on the shipped guard — `git commit`,
`git stash`, `git clean -f`, `talosctl reset`, `mkfs`, `dd of=/dev/sdc` and
`pkill -f` all resolved ALLOW behind one apostrophe. The change can only make
more argv visible, never fewer.

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
import sys, os, json, re, shlex, ipaddress, subprocess, collections


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
    # 🔴 `time` covers BOTH the bash KEYWORD and GNU /usr/bin/time, which have
    # different flags — the value-taking ones here are GNU's. MEASURED before
    # this entry existed: `time dd bs=1M if=/dev/zero of=/dev/sda` resolved
    # ALLOW at BOTH layers, because argv[0] stayed "time" so
    # check_dd_to_block_device bailed at its `basename(argv[0]) != "dd"`.
    # `dd` is where that mattered: for every other family the glob layer
    # backstops an unpeeled wrapper (`time rm -rf /`, `time mkfs…`,
    # `time talosctl … reset` all deny on the globs alone), but the dd globs are
    # the only SPELLED ones — `"dd *"` is anchored and `"*dd if=*"`/`"*dd of=*"`
    # are literal — so an unrecognised prefix defeats both at once.
    # `\time` (the alias/keyword bypass) needs no entry: the tokeniser already
    # normalises it to `time`. `/usr/bin/time` is handled by the basename().
    "time": {"-f", "--format", "-o", "--output"},
    "nohup": set(), "setsid": set(), "command": set(), "builtin": set(),
}
_WRAPPERS = set(_WRAPPER_VALUE_FLAGS)
# Positional arguments the wrapper itself consumes before the wrapped command
# (`timeout 90 cmd`, `chrt 50 cmd`).
_WRAPPER_POSITIONAL = {"timeout": 1, "chrt": 1}

_SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "busybox"}


# A scanned segment: its text, and the SUBSHELL NESTING DEPTH it sits at. The
# depth is what lets a caller know that a `cd` inside `( … )` does not leak out
# — see `_commands_with_cwd`. Every existing caller ignores it via
# `split_commands`, whose contract is unchanged.
_Seg = collections.namedtuple("_Seg", "text depth")

# A heredoc tag as bash accepts it unquoted. Quoted tags (`<<'EOF'`, `<<"EOF"`)
# are read by _read_heredoc_tag directly.
_HEREDOC_TAG = re.compile(r"[A-Za-z0-9_.-]+")

# Bound on re-parsing nested bodies (a heredoc inside a heredoc inside …), the
# same bound `commands()` puts on `bash -c` recursion.
_BODY_RECURSION_LIMIT = 3

# How many times `_scan` will re-read a tail that an UNTERMINATED quote hid. One
# pass per unbalanced quote character; the cap only exists so text engineered to
# be full of them cannot make a per-Bash-call hook quadratic.
_QUOTE_RECOVERY_LIMIT = 16


def _read_heredoc_tag(text, k):
    """`(tag, index-after-tag)` at position `k`, or `(None, k)`.

    Handles `<<EOF`, `<<'EOF'`, `<<"EOF"` and `<<\\EOF` — the four spellings, all
    of which name the same terminator line.
    """
    n = len(text)
    if k >= n:
        return None, k
    if text[k] in ("'", '"'):
        q = text[k]
        j = text.find(q, k + 1)
        if j == -1:
            return None, k
        return text[k + 1:j], j + 1
    if text[k] == "\\":
        k += 1
    m = _HEREDOC_TAG.match(text, k)
    if not m:
        return None, k
    return m.group(0), m.end()


def _consume_heredocs(text, i, pending):
    """Read the bodies of the heredocs opened on the line that just ended.

    Returns `(index-after-the-last-body, [body-text, …])`. Multiple heredocs on
    one line (`cmd <<A <<B`) are consumed in the order they were opened, which is
    bash's order. A body with no terminator runs to end-of-text — also bash's
    behaviour, and the fail-CLOSED direction here, since the text is still
    scanned as commands rather than dropped.
    """
    bodies, n = [], len(text)
    for tag, strip_tabs in pending:
        lines = []
        while i < n:
            j = text.find("\n", i)
            line = text[i:j] if j != -1 else text[i:]
            nxt = (j + 1) if j != -1 else n
            probe = line.lstrip("\t") if strip_tabs else line
            if probe.rstrip("\r") == tag:
                i = nxt
                break
            lines.append(line)
            i = nxt
        bodies.append("\n".join(lines))
    return i, bodies


def _scan(text, _recovery=0):
    """`([_Seg, …], [substitution-body, …], [heredoc-body, …])`.

    🔴 HEREDOC BODIES ARE LIFTED OUT OF THE SCAN, and that is a BUG FIX, not a
    tidy-up. The scanner tracks quote state character by character, and a heredoc
    body is ordinary prose — so a single apostrophe in a commit message
    (`don't`, `it's`) OPENED a quote that never closed, and every command after
    the heredoc was swallowed into that quoted buffer instead of being emitted as
    its own segment. Measured against the shipped guard, cwd on `trunk`:

        DENY   cat > /tmp/m <<'EOF' / do not stage / EOF / git commit -F /tmp/m
        ALLOW  cat > /tmp/m <<'EOF' / don't stage  / EOF / git commit -F /tmp/m
        ALLOW  …same apostrophe body… / git stash push -m wip
        ALLOW  …same apostrophe body… / talosctl -n 1.2.3.4 reset

    i.e. one apostrophe in a message body silently disabled EVERY argv check for
    the rest of the command line. The two cases differ only in that apostrophe,
    which is the control that names the mechanism.

    🔴 This is NOT the reverted `_strip_message_text()` helper the DESIGN NOTE
    forbids, and the difference is the one that matters: nothing is BLANKED and
    nothing is decided to be inert. The bodies are still returned, still parsed
    as commands, and still checked — so `bash <<EOF` (a body that really does
    execute) keeps exactly the coverage it has today, and a body that merely
    QUOTES a banned command still denies via the documented escape hatch. All
    that changes is that a body can no longer corrupt the parse of the commands
    AROUND it. The change is strictly ADDITIVE — it can only make more argv
    visible, never fewer — which is why it cannot loosen any check.
    """
    segs, subs, bodies = [], [], []
    buf = []
    i, n = 0, len(text)
    quote = None
    quote_at = quote_depth = None
    depth = 0
    pending = []
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
            quote = c; quote_at, quote_depth = i, depth
            buf.append(c); i += 1; continue
        if c == "`":
            j = text.find("`", i + 1)
            if j == -1:
                j = n
            subs.append(text[i + 1:j])
            i = j + 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            sdepth, j = 1, i + 2
            while j < n and sdepth:
                if text[j] == "(":
                    sdepth += 1
                elif text[j] == ")":
                    sdepth -= 1
                j += 1
            subs.append(text[i + 2:j - 1 if sdepth == 0 else n])
            i = j
            continue
        # A heredoc OPERATOR (`<<TAG`, `<<-TAG`, `<<'TAG'`). `<<<` is a
        # here-STRING — it has no body and no terminator line, so it must not be
        # treated as one.
        if text.startswith("<<", i) and not text.startswith("<<<", i):
            j = i + 2
            strip_tabs = False
            if j < n and text[j] == "-":
                strip_tabs = True
                j += 1
            k = j
            while k < n and text[k] in " \t":
                k += 1
            tag, after = _read_heredoc_tag(text, k)
            if tag is not None:
                buf.append(text[i:after])     # keep the operator in the segment
                pending.append((tag, strip_tabs))
                i = after
                continue
        if c in "()":                 # subshell parens are not part of argv
            segs.append(_Seg("".join(buf), depth)); buf = []
            depth = depth + 1 if c == "(" else max(0, depth - 1)
            i += 1; continue
        matched = None
        for op in _SEPARATORS:
            if text.startswith(op, i):
                matched = op
                break
        if matched:
            segs.append(_Seg("".join(buf), depth)); buf = []
            i += len(matched)
            if matched == "\n" and pending:
                i, opened = _consume_heredocs(text, i, pending)
                bodies.extend(opened)
                pending = []
            continue
        buf.append(c); i += 1
    segs.append(_Seg("".join(buf), depth))
    # 🔴 AN UNTERMINATED QUOTE MUST NOT BLIND THE REST OF THE SCAN. A lone
    # apostrophe — in a heredoc body, a `-m` message, any prose the model wrote —
    # opens a quote that never closes, and everything after it lands inside ONE
    # quoted buffer. `_tokenise` then falls back to a whitespace split whose
    # argv[0] is a word from the prose, so every argv check silently stops
    # matching. Found by this change's own non-regression test, on a body that
    # REALLY executes (`bash <<'EOF' / it's fine / git stash push -m wip / EOF`),
    # where lifting the heredoc out was not enough because the corruption then
    # happened INSIDE the body's own parse.
    #
    # An unterminated quote is a malformed command the shell itself would reject,
    # so there is no "correct" reading to preserve. Re-scanning the tail with the
    # offending quote treated as an ordinary character is purely ADDITIVE — it
    # can only surface argv the first pass buried — which is the only direction a
    # guard may err in. Each pass consumes at least the quote character, so it
    # terminates; the limit only bounds adversarial input full of odd quotes.
    if quote is not None and quote_at is not None and _recovery < _QUOTE_RECOVERY_LIMIT:
        r_segs, r_subs, r_bodies = _scan(text[quote_at + 1:], _recovery + 1)
        segs.extend(_Seg(s.text, quote_depth + s.depth) for s in r_segs)
        subs.extend(r_subs)
        bodies.extend(r_bodies)
    return segs, subs, bodies


def split_commands(text, _depth=0):
    """Split shell text into command segments, respecting quotes.

    Also yields the BODY of every `$( … )` / backtick substitution and of every
    HEREDOC as its own text, because a substitution body really does execute and
    a heredoc body may (`bash <<EOF`). Linear scan — no regex, so no backtracking
    to worry about on a guard that runs per call.
    """
    segs, subs, bodies = _scan(text)
    out = [s.text for s in segs] + subs
    for body in bodies:
        out.extend(split_commands(body, _depth + 1)
                   if _depth < _BODY_RECURSION_LIMIT else [body])
    return [s for s in out if s.strip()]


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
# NOT opencode-only any more, and by now MOSTLY not: `check_git_reset_hard_argv`,
# `check_git_stash` and `check_git_clean_force` (all 2026-08-02) run under BOTH
# policies, and `check_talosctl_reset`, `check_mkfs` and `check_dd_to_block_device`
# joined them later the same day. `check_rm_rf_critical` is the ONLY check in
# this section that is still opencode-only, deliberately — see _IRREVERSIBLE_CHECKS.
# POLICIES at the bottom is the authority — read it rather than assuming from
# this section heading.
# =========================================================================== #

# The escape hatch every claude-code-policy deny message must carry. The guard
# parses RAW TEXT, and the argv parser splits on newlines — so a heredoc body
# LINE that documents a banned command is parsed as that command and denied.
# `check_git_add_all` established this convention after the operator's own test
# harness was blocked for containing the literal string `git add -A`; #295
# extended it to the stash/clean messages. Every check that is in
# `_CLAUDE_CODE_CHECKS` hands the caller the same documented way out, because a
# check that fires on every Bash call in every session WILL eventually fire on
# someone writing ABOUT the command.
#
# One string, one place: a message asserted to contain it in test_guard_core.py
# should not be able to drift per-check.
_QUOTING_ESCAPE_HATCH = (
    " (Only QUOTING this command — e.g. a heredoc body documenting the ban, whose lines this "
    "guard parses as real commands? Write the text to a file with the Write tool and use "
    "`git commit -F <file>` / `gh pr create --body-file <file>` — which your RULES prefer over "
    "heredocs anyway.)"
)


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

    Enabled for BOTH policies since 2026-08-02 (it shipped opencode-only). It
    lives in `_CLAUDE_CODE_CHECKS`, which `POLICIES["opencode"]` includes, so
    opencode keeps coverage by inheritance rather than a duplicate entry.
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
                    "`talosctl --nodes <ip> reset` and `sudo talosctl reset` are all caught.)"
                    + _QUOTING_ESCAPE_HATCH)
    return None


_MKFS = re.compile(r"^(?:mkfs(?:\.[A-Za-z0-9_-]+)?|mke2fs|mkswap|newfs|mkntfs|mkdosfs)$")


def check_mkfs(cmd):
    """Formats a filesystem — destroys every byte on the target device.

    Enabled for BOTH policies since 2026-08-02 (it shipped opencode-only), by
    inheritance from `_CLAUDE_CODE_CHECKS`. It matches on the PROGRAM NAME, so
    it cannot fire on a `mkfs` substring inside an argument — `grep -rn 'mkfs'`
    and `echo 'mkfs.ext4 is banned'` stay allowed, asserted in the tests.
    """
    for argv in commands(cmd):
        base = os.path.basename(argv[0])
        if _MKFS.match(base):
            return (f"`{base}` is blocked — it FORMATS a filesystem and destroys everything on "
                    "the target device. No agent on these hosts has a reason to run it. If you "
                    "are provisioning a disk, do it yourself with the device path in front of "
                    "you." + _QUOTING_ESCAPE_HATCH)
    return None


# `dd of=` targets that are safe sinks rather than storage.
_DD_SAFE_DEV = re.compile(r"^/dev/(?:null|zero|stdout|stderr|stdin|tty|full|random|urandom|fd/\d+)$")


def check_dd_to_block_device(cmd):
    """`dd of=/dev/sdX` overwrites a disk in place. `of=/dev/null` is fine.

    Enabled for BOTH policies since 2026-08-02 (it shipped opencode-only), by
    inheritance from `_CLAUDE_CODE_CHECKS`. Only an `of=` under `/dev/` that is
    not one of the pseudo sinks denies, so the ordinary file-to-file `dd` an
    agent might legitimately run is untouched.
    """
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
                        "sinks are allowed.)" + _QUOTING_ESCAPE_HATCH)
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
# STATE-AWARE CHECKS — the first checks in this file that READ THE WORLD.
#
# Every check above is a pure function of the command TEXT. `check_git_commit_to_main`
# is not: "is this a commit to main?" is unanswerable from the text alone, because
# the branch lives in the repo, not in the command. So it shells out to `git`.
# Three properties keep that honest:
#
#   * It runs git ONLY after the parser has already established that this argv is a
#     real `git … commit`. Every other Bash call pays zero subprocesses, so the hot
#     path — which is EVERY Bash call in EVERY session — is unchanged.
#   * Every git call is READ-ONLY (`rev-parse`, `branch --show-current`, `remote`),
#     argv-list form (never a shell), and timeout-bounded. A guard must not be able
#     to hang the tool it guards.
#   * It FAILS OPEN on any resolution failure (not a repo, git missing, detached
#     HEAD, timeout). See the fail-open note on `_commit_to_main_reason` — this is
#     the one place in this file that does not fail closed, and the reasoning is
#     recorded there rather than left implicit.
#
# A check that needs the caller's working directory is marked with `@_wants_cwd`;
# `evaluate` passes cwd to exactly those. That is deliberately an explicit marker
# rather than signature introspection or a module-level "current cwd" global: the
# guard runs in a one-shot process per call, but a mutable global would still be a
# lie waiting to be read by the next test in the same interpreter.
# =========================================================================== #

def _wants_cwd(fn):
    """Mark a check as taking `(cmd, cwd)` rather than `(cmd)`."""
    fn.wants_cwd = True
    return fn


def _git_read(repo_dir, *args):
    """A read-only `git -C <repo_dir> <args>`; None on ANY failure.

    argv-list form (no shell), 5s timeout, stderr swallowed. `repo_dir` comes from
    model-authored command text, so it is never interpolated into a shell string.
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", repo_dir, *args],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


# 🔴 THE ALLOWLIST. Adding to it is a deliberate edit, and it is pinned by
# test_guard_core.py so it cannot grow silently.
#
# RULES.md carves out exactly one exception to "feature branches only": a repo
# whose OWN CLAUDE.md states that committing to the main branch IS deploying.
# Today that is homelab-talos — `trunk` there is reconciled by Flux, so its
# CLAUDE.md line 7 reads "Commit = live deploy". Committing to trunk is the
# documented workflow, not a mistake, and a guard that blocked it would fire on
# routine correct work — the failure mode `_IRREVERSIBLE_CHECKS` documents for
# `rm -rf` (a guard routed around is worse than no guard, because it still
# reports safety).
#
# 🔴 BOTH NAMES BELOW ARE THE SAME REPO, and that is the whole reason this
# allowlist is a SET rather than a path. The working-tree directory is
# `homelab-talos`; the GitHub repo behind it is `ZacxDev/homelab-infra`. A
# path-keyed allowlist and a remote-keyed allowlist would each have been
# silently half-right. `_repo_names` returns both the toplevel basename and every
# remote's repo name, so a LINKED WORKTREE of that repo — whose directory is
# `homelab-trunk`, matching neither entry by path — is still allowlisted via its
# remote. That worktree is the spelling homelab-talos's CLAUDE.md actively tells
# agents to use, so remote-matching is not a nicety here.
# 🔴 A remote entry is OWNER/REPO, never a bare repo name. An adversarial probe
# during review showed why: with bare-name matching, `_repo_names` allowlisted a
# FORK (`someoneelse/homelab-infra`) and even `git@evil.example:x/homelab-infra`,
# because "the last path component" is not an identity. Qualifying by owner makes
# the entry name one repository. Measured after the change — see
# test_allowlist_rejects_a_fork_or_a_lookalike_host.
# 🔴 REMOTE SLUGS ONLY — no directory names. Matching a DIRECTORY name meant any
# repo sitting in a folder called `homelab-talos` inherited the exemption
# regardless of its remote (measured during audit: a checkout with devrc's own
# remote, in a directory of that name, resolved ALLOW). A directory name is
# chosen by whoever made the directory; a remote slug identifies the repository.
# Dropping the directory entry costs nothing — the real homelab-talos checkout
# AND the `homelab-trunk` linked worktree its CLAUDE.md prescribes both carry
# this remote, and a hypothetical remote-less copy is already allowed by the
# no-remotes carve-out.
_TRUNK_DEPLOY_REPOS = frozenset({
    "ZacxDev/homelab-infra",    # homelab-talos: its CLAUDE.md declares commit = live deploy
})

# The branch names RULES.md means by "main/master". `trunk` is included because it
# is a main branch NAME, not because homelab-talos uses it: an un-allowlisted repo
# that happens to name its main branch `trunk` should be blocked too. The
# allowlist, not the branch name, is what makes homelab-talos legal.
_MAIN_BRANCH_NAMES = frozenset({"main", "master", "trunk"})

# `git commit` spellings that do not CREATE a commit. `--dry-run` is the whole
# list: it is git's documented "show what would be committed" mode and is a read.
#
# 🔴 `-n` is NOT here — for `git commit` that is `--no-verify`, which commits
# while SKIPPING hooks. Reading it as a dry run would have opened a bypass that
# looks like a typo.
#
# Matched as an ABBREVIATION, not an exact string, because git accepts any
# unambiguous prefix of a long option: `git commit --dry` really is a dry run
# (verified: rc=0, "Changes to be committed", no commit created). An exact-set
# test denied it — a false positive on a READ, found by audit.
_DRY_RUN_MIN = "--dry"
_DRY_RUN_FULL = "--dry-run"


def _is_dry_run_flag(flag):
    return (flag.startswith(_DRY_RUN_MIN)
            and _DRY_RUN_FULL.startswith(flag))


# `cd`/`pushd` used to be found with a REGEX over the raw text, which could only
# ever produce a set of "directories this line mentions" — never an answer to
# "which one is the command standing in". That is now `_commands_with_cwd`,
# which sees `cd` as argv[0] of a segment and tracks subshell scope, so the
# regex (and the union it fed) is gone. `_CWD_CHANGERS` is its replacement.
#
# `GIT_DIR=`/`GIT_WORK_TREE=` env prefixes. The parser strips `VAR=` prefixes off
# the argv (correctly — they are not the command), so the only place these
# survive is the raw text.
_GIT_DIR_ENV = re.compile(r"\bGIT_(?:DIR|WORK_TREE)=(?P<dir>\"[^\"]*\"|'[^']*'|\S+)")

# git's own "act on this repo" global options, the `-C` siblings.
_GIT_REPO_OPTS = ("--git-dir", "--work-tree")


def _safe_getcwd():
    """🔴 `os.getcwd()` RAISES when the process's cwd has been deleted, and this
    runs on every Bash call. An escaped exception is not a missed deny — it is a
    BLANKET one: bash-guard.py denies on exception, and the opencode plugin fails
    closed on the CLI's rc=2, so every command in the session would be refused
    until the directory came back. Realistic trigger: a session whose cwd was a
    worktree another session removed. Found by audit; returns None instead."""
    try:
        return os.getcwd()
    except OSError:
        return None


# A `$NAME` / `${NAME}` reference. Used to finish the job `os.path.expandvars`
# starts: expandvars only knows the HOOK PROCESS's environment, and the variable
# that matters here (`WT`, `REPO`, …) is set by the command line itself.
_VAR_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

# `NAME=VALUE`, the shell's assignment form.
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)

# Words that may PRECEDE an assignment and still leave it an assignment.
_ASSIGN_LEADERS = frozenset({"export", "declare", "typeset", "local", "readonly"})

# The two spellings of "run this file's assignments in my shell".
_SOURCE_CMDS = frozenset({".", "source"})

# Bounds on reading a sourced env file. It is named by the command text, so it
# is model-controlled input: read it defensively and never let it be large.
_ENV_FILE_MAX_BYTES = 64 * 1024
_ENV_FILE_MAX_LINES = 400


def _literal_value(value):
    """The value of an assignment, IF it is a plain literal — else None.

    Anything carrying `$`, a backtick or a command substitution is not something
    this guard can evaluate, and guessing at it is how a resolver starts naming
    directories the command never touches.
    """
    if value is None:
        return None
    value = value.strip().strip("\"'")
    if not value or "$" in value or "`" in value:
        return None
    return value


def _subst_vars(value, cvars):
    """Fill `$NAME`/`${NAME}` from `cvars`, leaving unknown names untouched."""
    if not cvars or "$" not in value:
        return value
    return _VAR_REF.sub(lambda m: cvars.get(m.group(1) or m.group(2), m.group(0)), value)


def _env_file_vars(raw, base, known):
    """`NAME=VALUE` assignments from a file the command SOURCES. `{}` on anything
    unreadable, oversized or not a plain file.

    Reading it is what makes the mandated worktree recipe resolvable at all. The
    house recipe is `WT=/tmp/wt-$$` — a value this guard can never evaluate,
    because `$$` is the SHELL's pid — and the observed agent pattern is to write
    the EXPANDED path into a scratchpad env file in one tool call and `. ` it in
    the next. The file therefore holds the literal directory; the command text
    never does.
    """
    path = _subst_vars(os.path.expandvars(str(raw).strip("\"'")), known)
    path = os.path.expanduser(path)
    if not path or "$" in path:
        return {}
    if not os.path.isabs(path):
        if not base:
            return {}
        path = os.path.join(base, path)
    out = {}
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > _ENV_FILE_MAX_BYTES:
            return {}
        with open(path, errors="replace") as fh:
            for lineno, line in enumerate(fh):
                if lineno >= _ENV_FILE_MAX_LINES:
                    break
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                head = line.split(None, 1)
                if head and head[0] in _ASSIGN_LEADERS and len(head) > 1:
                    line = head[1].strip()
                m = _ASSIGNMENT.match(line)
                if not m:
                    continue
                val = _literal_value(m.group(2))
                if val is not None:
                    out[m.group(1)] = val
    except OSError:
        return {}
    return out


def _command_vars(cmd, base):
    """Shell variables this command line SETS, as far as they can be evaluated.

    🔴 THE ROOT CAUSE OF THE FALSE POSITIVE THIS FIXES. `_resolve_dir` used only
    `os.path.expandvars`, which reads the HOOK PROCESS's environment — and the
    variable naming the worktree is set by the command itself, so it was never
    there. `git -C "$WT" commit` therefore resolved to NOTHING, fell back to the
    caller's cwd, and denied by naming the primary clone: a false positive on
    precisely the worktree workflow the deny message goes on to RECOMMEND.
    Measured on the shipped guard, cwd = a clone on `trunk`, `$WT` = a worktree
    on a topic branch:
        DENY   WT=<worktree>; git -C "$WT" commit -F <file>
        DENY   . <env-file>; git -C "$WT" commit -F <file>
        DENY   WT=<worktree>; (cd "$WT" && git commit -F <file>)
    all three naming `trunk` in the primary clone, which none of them touch.

    A name assigned TWICE to different literals, or once to anything with a `$`
    or a substitution in it, is dropped rather than guessed — an unresolved name
    then falls back to the cwd exactly as before, which is the fail-closed
    direction this function must not weaken.
    """
    vals, poisoned = {}, set()

    def note(name, value):
        if name in poisoned:
            return
        val = _literal_value(value)
        if val is None or (name in vals and vals[name] != val):
            poisoned.add(name)
            vals.pop(name, None)
            return
        vals[name] = val

    # 🔴 TOP-LEVEL SEGMENTS ONLY — `_scan(cmd)[0]`, not `split_commands`, and the
    # difference is a hole this change would otherwise have opened itself. A
    # heredoc body is PROSE; if its lines could register assignments, a commit
    # message containing the line `WT=/tmp/anything` would decide which repo the
    # guard judges. Reading only the real command line keeps the value under the
    # control of the command rather than of its message text, and an assignment
    # the guard therefore cannot see just falls back to the cwd — fail closed.
    sourced = []
    for seg in _scan(cmd)[0]:
        toks = _tokenise(seg.text)
        i = 0
        while i < len(toks):
            m = _ASSIGNMENT.match(toks[i])
            if m:
                note(m.group(1), m.group(2))
                i += 1
                continue
            if os.path.basename(toks[i]) in _ASSIGN_LEADERS:
                i += 1
                continue
            break
        if i < len(toks) and toks[i] in _SOURCE_CMDS and i + 1 < len(toks):
            sourced.append(toks[i + 1])

    # A sourced file is the LOWER layer: an assignment written in the command
    # text itself is more specific and wins, and a poisoned name stays poisoned.
    for raw in sourced:
        for name, val in _env_file_vars(raw, base, vals).items():
            if name not in vals and name not in poisoned:
                vals[name] = val
    return vals


def _resolve_dir(value, base, cvars=None):
    """A command-supplied path -> an existing directory, or None.

    Expands `$HANDLE` (from the environment first, then from `cvars` — the
    variables the command line itself sets) and `~`, resolves a relative path
    against `base`, and maps `<repo>/.git` onto `<repo>` so a `--git-dir` is
    judged as its worktree.
    """
    if not value:
        return None
    value = os.path.expandvars(value.strip("\"'"))
    if "$" in value:
        value = _subst_vars(value, cvars)
    value = os.path.expanduser(value)
    if not value:
        return None
    if not os.path.isabs(value):
        if not base:
            return None
        value = os.path.join(base, value)
    if os.path.basename(value) == ".git" and os.path.isdir(value):
        value = os.path.dirname(value) or value
    return value if os.path.isdir(value) else None


def _dash_c_dir(argv, base, cvars=None, unresolved=None):
    """The directory a `git` argv acts on: `base`, moved by each `-C <path>`.

    `unresolved`, when given a list, collects every `-C` target that could NOT be
    resolved — so the caller can say so in its deny message instead of silently
    reporting on a directory the command never named.

    git's multiple `-C` options are CUMULATIVE and relative ones compose, so this
    walks them in order rather than taking the last. Only the SEPARATE form is
    handled, because git itself rejects the attached one (measured:
    `git -C/tmp rev-parse` -> "unknown option: -C/tmp").

    🔴 AN UNRESOLVABLE `-C` TARGET FALLS BACK TO `base` RATHER THAN FAILING OPEN,
    and that is not a nicety here. This repo's own CLAUDE.md tells agents to use
    pre-exported handles — `git -C $DEVRC commit` — and the guard parses TEXT, so
    `$DEVRC` arrives unexpanded and names no directory. Measured before this
    fallback existed, with cwd on `main`:
        DENY   git commit -m x
        ALLOW  git -C $DEVRC commit -m x        <- the house-style spelling
        ALLOW  git -C "$WT" commit -m x
    i.e. the guard was blind to precisely the spelling the docs push agents
    toward — the same class of miss that made `check_git_reset_hard` blind to
    `git -C <path> reset --hard`. `expandvars` is tried first (the hook may
    inherit the handle), and only an unresolved path falls back to the cwd.

    The fallback can mis-attribute: cwd on main + a `-C` to a feature-branch repo
    whose path did not resolve now denies. That direction is the safe one — the
    deny message names the repo it judged, so it is self-diagnosing, and the way
    past it is an absolute `-C` path, which resolves.
    """
    # Path resolution goes through _resolve_dir — ONE place. It used to be
    # open-coded here as well, and a mutation sweep caught the consequence: the
    # duplicate meant _resolve_dir's `expandvars` was never exercised by any test
    # (the only handle test went through this copy), so that mutant SURVIVED. A
    # predicate duplicated across call sites is wrong at N-1 of them eventually;
    # here it was merely untested, which is how it starts.
    cur, i, saw = base, 1, False
    while i < len(argv):
        tok = argv[i]
        if tok == "-C" and i + 1 < len(argv):
            moved = _resolve_dir(argv[i + 1], cur, cvars)
            if moved is None:
                unresolved.append(argv[i + 1])
            cur = moved or cur
            i, saw = i + 2, True
            continue
        if tok in _GIT_GLOBAL_VALUE_OPTS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        break
    if not saw:
        return None
    return cur if cur and os.path.isdir(cur) else base


def _argv_named_repo_dirs(argv, base, cvars=None, unresolved=None):
    """The repos an argv NAMES for itself, via `-C` / `--git-dir` / `--work-tree`.

    Empty when the argv names none — the caller then falls back to the directory
    the command is actually standing in. Keeping "named" and "inferred" apart is
    what lets an explicit `git -C <feature-repo> commit` stay ALLOWED from a cwd
    that sits on main.
    """
    dirs = []
    hopped = _dash_c_dir(argv, base, cvars, unresolved)
    if hopped:
        dirs.append(hopped)
    for i, tok in enumerate(argv[1:], 1):
        for opt in _GIT_REPO_OPTS:
            val = None
            if tok == opt and i + 1 < len(argv):
                val = argv[i + 1]
            elif tok.startswith(opt + "="):
                val = tok.split("=", 1)[1]
            resolved = _resolve_dir(val, base, cvars)
            if resolved:
                dirs.append(resolved)
    return list(dict.fromkeys(dirs))


def _git_dir_env_targets(cmd, base, cvars=None):
    """Repos named by a `GIT_DIR=` / `GIT_WORK_TREE=` env assignment anywhere in
    the text.

    Kept as a WHOLE-TEXT scan rather than folded into the per-segment walk, and
    deliberately: these two variables OVERRIDE the working directory, so an
    `export GIT_DIR=…` earlier in the line still governs a later bare `git
    commit`. Judging them IN ADDITION to the effective cwd is the fail-closed
    direction, and it is what keeps the audit-found
    `GIT_DIR=<main>/.git … git commit` hop denied.
    """
    out = []
    for m in _GIT_DIR_ENV.finditer(cmd):
        resolved = _resolve_dir(m.group("dir"), base, cvars)
        if resolved:
            out.append(resolved)
    return list(dict.fromkeys(out))


_CWD_CHANGERS = frozenset({"cd", "pushd"})


def _commands_with_cwd(text, base, cvars=None, _depth=0):
    """`[(argv, cwd-in-effect), …]` — every real argv, paired with the directory
    the shell is standing in WHEN IT RUNS.

    🔴 THE SECOND HALF OF THE FALSE POSITIVE. The previous design could not
    answer "where does this command run?", so for a bare `git commit` it judged
    the UNION of the caller's cwd and every `cd` target anywhere in the text, and
    denied if ANY of them was a blocked repo. That over-approximation denied the
    mandated worktree spellings outright:
        DENY   (cd <worktree-on-a-topic-branch> && git commit -F <file>)
        DENY   bash -c 'cd <worktree-on-a-topic-branch> && git commit …'
    from a cwd that merely HAPPENED to sit on trunk — the commit does not touch
    that repo at all. The union was chosen because "the guard cannot know which
    `cd` won"; it can, if it tracks position and scope, which is what this does.

    Scope is the part that must not be lost. A `cd` inside `( … )` does NOT leak
    to the commands after the subshell, so `_scan`'s per-segment depth drives a
    stack: entering a subshell pushes the current directory, leaving it pops.
    That keeps the negative control denying:
        DENY   (cd <worktree>; ls); git commit        <- the cd did not survive

    A `cd` whose target does not resolve leaves the directory UNCHANGED, which
    keeps the caller's cwd in play — the fail-closed direction, and the same
    direction an unresolvable `-C` takes.

    KNOWN LIMIT, stated rather than papered over: this walks positions, not
    control flow, so a `cd` that would be SKIPPED at runtime (`false && cd <x>;
    git commit`) is still treated as having happened. That direction can only
    mis-attribute a bare commit to a directory the line explicitly names, and the
    ordinary `cd <path> && git …` shape stays blocked by `check_cd_then_git`
    regardless.
    """
    out = []
    segs, subs, bodies = _scan(text)
    cwd, stack, cur_depth = base, [], 0
    for seg in segs:
        while cur_depth < seg.depth:
            stack.append(cwd)
            cur_depth += 1
        while cur_depth > seg.depth and stack:
            cwd = stack.pop()
            cur_depth -= 1
        variants = _peel_variants(_tokenise(seg.text))
        for argv in variants:
            out.append((argv, cwd))
            if _depth < 3:
                for nested in _nested_shell_text(argv):
                    out.extend(_commands_with_cwd(nested, cwd, cvars, _depth + 1))
        primary = variants[0] if variants else None
        if primary and os.path.basename(primary[0]) in _CWD_CHANGERS and len(primary) > 1:
            moved = _resolve_dir(primary[1], cwd, cvars)
            if moved:
                cwd = moved
    # Substitution and heredoc bodies run in their own scope and have no position
    # in this walk, so they are judged against `base` — the same directory they
    # were judged against before, and the fail-closed choice.
    if _depth < _BODY_RECURSION_LIMIT:
        for isolated in subs + bodies:
            out.extend(_commands_with_cwd(isolated, base, cvars, _depth + 1))
    return out


def _remote_slug(url):
    """`owner/repo` for a remote URL, or None.

    Handles the three spellings that name the same repository — scp-style
    (`git@github.com:Owner/repo.git`), https (`https://host/Owner/repo[.git]`)
    and `ssh://git@host/Owner/repo.git` — by stripping any scheme/host prefix and
    taking the LAST TWO path segments.

    🔴 Two segments, not one. Matching the bare repo name allowlisted a FORK
    (`someoneelse/homelab-infra`) and `git@evil.example:x/homelab-infra` — found
    by an adversarial probe, not by a test that was looking for it.
    """
    if not url:
        return None
    path = url.rstrip("/")
    if "://" in path:                      # ssh://, https://, git://
        path = path.split("://", 1)[1]
        path = path.split("/", 1)[1] if "/" in path else ""
    elif ":" in path:                      # scp-style git@host:owner/repo
        path = path.split(":", 1)[1]
    if path.endswith(".git"):
        path = path[:-4]
    parts = [p for p in path.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def _remote_slugs(repo_dir):
    """The `owner/repo` slug of every remote. Empty when the repo has none —
    which is itself the signal the no-remotes carve-out reads, so this is called
    ONCE and its emptiness reused rather than re-running `git remote` (the deny
    path used to exec it twice; found by audit)."""
    slugs = []
    for remote in (_git_read(repo_dir, "remote") or "").split():
        slug = _remote_slug(_git_read(repo_dir, "remote", "get-url", remote))
        if slug:
            slugs.append(slug)
    return slugs


def _is_git_worktree(repo_dir):
    """Is `repo_dir` inside a git worktree at all?

    The one question `_commit_to_main_reason` cannot answer for its caller: that
    function FAILS OPEN on "no branch", and BOTH "not a git repo" and "detached
    HEAD" arrive at that same `None`. An EMPTY RESULT cannot distinguish two
    mechanisms, so the caller has to ask this separately rather than read it out
    of a `None` that means several things at once.
    """
    return _git_read(repo_dir, "rev-parse", "--show-toplevel") is not None


def _commit_to_main_reason(repo_dir):
    """The deny reason for committing in `repo_dir`, or None to allow.

    🔴 FAILS OPEN, unlike everything else in this file, in exactly three cases —
    each because the alternative blocks work that is not the hazard:

      * `repo_dir` is not a git repo, or git is unavailable -> there is no branch
        to be wrong about.
      * detached HEAD -> `branch --show-current` is empty; a detached commit is
        not a commit to main.
      * THE REPO HAS NO REMOTES -> a purely local repo cannot be a shared deploy
        target, and this is the false-positive class that would otherwise dominate.
        `git init` defaults its first branch to `main` or `master`, so WITHOUT this
        carve-out every scratch repo an agent builds under /tmp (including this
        guard's own test fixtures) would trip a 🔴 deny during routine work — the
        "guard fires on normal work, subject learns to route around it" failure
        `_IRREVERSIBLE_CHECKS` documents. It is not a practical bypass: reaching it
        from a real repo means deleting the remote first, which is a louder act
        than the commit it would hide.

    Failing open here is safe in a way it would not be for a destruction check:
    the cost of a miss is a commit on the wrong branch, which is RECOVERABLE
    (`git branch <topic> HEAD` + `git reset --keep origin/main`, the recipe
    devrc's CLAUDE.md already carries) — not a wiped disk.
    """
    branch = _git_read(repo_dir, "branch", "--show-current")
    if not branch or branch not in _MAIN_BRANCH_NAMES:
        return None
    if _git_read(repo_dir, "rev-parse", "--show-toplevel") is None:
        return None                       # not a git repo at all
    slugs = _remote_slugs(repo_dir)
    if not slugs:
        return None                       # the scratch-repo carve-out, argued above
    if any(s in _TRUNK_DEPLOY_REPOS for s in slugs):
        return None
    return (
        f"`git commit` on branch `{branch}` is blocked by your RULES — feature branches only, "
        f"never main/master/trunk. (repo: {repo_dir})\n"
        f"This rule is 🔴 in three separate files and has still been violated twice: 2026-08-06 "
        f"(two un-pushed commits on the workbench blocked `ship.sh` for hours) and 2026-08-09 "
        f"(three more, rescued as PR #366). A commit onto the wrong branch is the SILENT failure "
        f"— no conflict, no error, and `git log` afterwards shows exactly what you expect, because "
        f"you are reading the branch you landed on. In a devrc checkout it also stops that host "
        f"receiving every future change, because `ship.sh` converges with `merge --ff-only` and "
        f"SKIPS a diverged host while still looking healthy.\n"
        f"Do this instead: `git checkout -b <topic>` (or `git worktree add ../<repo>-<topic> "
        f"-b <topic> origin/{branch}`), commit there, and open a PR.\n"
        f"If you have ALREADY committed to `{branch}` here, do not reset first — preserve it: "
        f"`git branch <topic> HEAD && git push -u origin <topic>`, confirm the shas are on origin, "
        f"then `git reset --keep origin/{branch}`.\n"
        f"The ONE repo where committing to the main branch is the correct workflow is "
        f"homelab-talos, whose own CLAUDE.md declares that commit = live deploy (Flux reconciles "
        f"`trunk`). It is allowlisted by name. Adding a repo to that allowlist is a deliberate "
        f"edit to `_TRUNK_DEPLOY_REPOS` in guard_core.py and a decision for the operator, not "
        f"something to do to get past this message."
        + _QUOTING_ESCAPE_HATCH
    )


@_wants_cwd
def check_git_commit_to_main(cmd, cwd=None):
    """🔴 Never commit to main/master/trunk — the rule prose demonstrably failed to hold.

    RULES.md ("Feature branches only", "Re-check WHICH branch you are on before ANY
    write") and devrc's CLAUDE.md ("Never commit to `main` in EITHER host checkout")
    both carry this at 🔴, in three files total, and it was still violated twice in
    four days (2026-08-06, 2026-08-09/PR #366). Prose cannot re-assert itself inside
    a long session; a PreToolUse hook fires on every call.

    Measured ALLOW against the live ~/.claude/hooks/bash-guard.py before this
    landed, with `git add --all` and `git stash` as DENY positive controls in the
    same sweep:
        ALLOW  git commit -m "wip"                       (cwd = devrc, on main)
        ALLOW  git commit --amend --no-edit
        ALLOW  git -C /home/zach/workspace/devrc commit -m "wip"
        ALLOW  git commit -F /tmp/msg.txt

    WHICH DIRECTORY GETS JUDGED — two rules, and the split is deliberate:

      * an argv that names its own repo (`-C`, `--git-dir`, `--work-tree`) is judged
        on THAT repo alone. The command said where it acts, so the caller's cwd is
        irrelevant, and folding it in would deny the documented `git -C $WT commit`
        spelling whenever the cwd happened to sit on main.
        🔴 UNLESS some named target is not a git worktree at all — then the named
        repo answers nothing, and judging "on that repo alone" would mean judging on
        nothing. That case falls back to the cwd candidates and blocks; see the
        comment on `targets` below.
      * a BARE `git commit` is judged against the directory the shell is STANDING
        IN when it runs — the caller's cwd, moved by each `cd`/`pushd` that
        precedes it in the line, with subshell scope respected (`_commands_with_cwd`)
        — plus any `GIT_DIR=`/`GIT_WORK_TREE=` target, which overrides a directory
        rather than being one.

    🔴 The second rule exists because the guard was TEACHING ITS OWN BYPASS. An
    adversarial audit measured, against the real adapter, that `cd <main> && git
    commit` denied (via check_cd_then_git) while the model's two obvious retries
    both ALLOWED:
        ALLOW  (cd <main> && git commit -m x)          <- a subshell
        ALLOW  bash -c 'cd <main> && git commit -m x'
        ALLOW  ls; cd <main>; git commit -m x          <- a non-leading cd
        ALLOW  git --git-dir=<main>/.git --work-tree=<main> commit -m x
    A deny whose adjacent re-spelling succeeds is worse than no deny: it launders
    the action into one the guard has blessed. All four still deny.

    🔴 2026-08-11: that second rule used to be a UNION — the cwd AND every `cd`
    target anywhere in the text, denying if ANY was blocked — and the union was
    wrong in BOTH directions, which is the part worth remembering. It denied the
    mandated worktree spellings from any session whose cwd merely happened to sit
    on a main branch (a primary clone's normal state):
        DENY   git -C "$WT" commit -F <file>         <- named the primary clone
        DENY   . <env-file>; git -C "$WT" commit …      it does not touch
        DENY   (cd <worktree-on-a-topic-branch> && git commit …)
    and, from a cwd that was fine, it silently ALLOWED the mirror image:
        ALLOW  REPO=<repo-on-trunk>; git -C "$REPO" commit -m x
    because an unresolvable `-C` fell back to a cwd that was innocent. Both are
    the same root cause: the guard could not evaluate a shell variable and could
    not say where a command stands. `_command_vars` and `_commands_with_cwd` are
    the two answers; the fallbacks they cannot answer with are unchanged.
    """
    # 🔴 THE HOT-PATH GATE. Resolving variables can READ A SOURCED ENV FILE, and
    # this hook fires on every Bash call in every session — so nothing below runs
    # until a cheap, pure-text pass has established that a real `git … commit`
    # is present at all. `commands()` here is the same parse the other argv
    # checks already do.
    if not _has_real_git_commit(cmd):
        return None
    base = cwd or _safe_getcwd()
    cvars = _command_vars(cmd, base)
    env_named = _git_dir_env_targets(cmd, base, cvars)
    for argv, eff_cwd in _commands_with_cwd(cmd, base, cvars):
        if os.path.basename(argv[0]) != "git":
            continue
        flags, operands = _flags_and_operands(_git_strip_global_opts(argv))
        if not operands or operands[0] != "commit":
            continue
        if any(_is_dry_run_flag(f) for f in flags):
            continue
        unresolved = []
        named = _argv_named_repo_dirs(argv, eff_cwd, cvars, unresolved)
        # 🔴 A NAMED TARGET THAT IS NOT A GIT WORKTREE MUST NOT SUPPRESS THE CWD
        # CHECK. Naming a repo hands the whole verdict to that repo, so if the
        # named directory turns out not to be one, the branch check evaluates
        # NOTHING and the command is allowed unchecked — a fail-OPEN, in the one
        # check whose job is the silent failure. Measured against the live hook
        # before this landed, cwd on `trunk`:
        #     ALLOW  git -C <an-ordinary-directory> commit -m x
        # The directory existing is what made it look answered: `_resolve_dir`
        # returns any real directory, and `_commit_to_main_reason` then fails open
        # on "no branch". So unless EVERY named target resolves to a worktree, fall
        # back to the cwd the command actually stands in and judge that instead —
        # the guard's documented posture for an unresolvable target ("treat it as
        # the cwd repo and block"), the same direction an unresolvable `-C` already
        # takes via `unresolved`.
        #
        # 🔴 This is a DIFFERENT failure from `unresolved`, and neither one covers
        # the other: `unresolved` is a path the guard could not turn into a
        # directory at all (a shell variable it cannot read), whereas this is a
        # path that resolved perfectly well and simply is not a repo. The first
        # already fell back; the second did not, and that gap was the fail-open.
        #
        # Deliberately "any target is not a worktree", not "no target is". They
        # differ only on a MIXED named set — one real worktree plus one junk
        # target, reachable by combining `-C` with `--git-dir`/`--work-tree`:
        #     git -C <feature-repo> --git-dir=<an-ordinary-directory> commit
        # Which repo that lands in is genuinely ambiguous (and `--git-dir` here
        # resolves against the CWD, not against the `-C` hop), so it is exactly the
        # "unparseable -> treat it as the cwd repo and block" case. Requiring only
        # ONE to be a worktree would let a resolvable `-C` vouch for a junk sibling
        # and allow it through.
        not_worktrees = [d for d in named if not _is_git_worktree(d)]
        targets = [] if not_worktrees else named
        candidates = targets or ([eff_cwd] if eff_cwd else []) + env_named
        for repo_dir in candidates:
            reason = _commit_to_main_reason(repo_dir)
            if reason:
                if unresolved and repo_dir == eff_cwd:
                    reason += (
                        f"\n(The `-C {unresolved[0]}` in this command names a path this guard "
                        f"could not resolve — it is a shell variable whose value the command "
                        f"text does not carry — so the caller's own directory was judged "
                        f"instead. If that is the wrong repo, pass `-C` an ABSOLUTE path, or "
                        f"assign the variable in this same command so the value is visible.)")
                elif not_worktrees and repo_dir == eff_cwd:
                    reason += (
                        f"\n(This command names `{not_worktrees[0]}` as its repo, but that path "
                        f"is not a git worktree, so it answers nothing about which branch the "
                        f"commit would land on — the caller's own directory was judged instead. "
                        f"Point `-C`/`--git-dir`/`--work-tree` at a real worktree if that is the "
                        f"wrong repo.)")
                return reason
    return None


def _has_real_git_commit(cmd):
    """True when this line contains a `git … commit` that would CREATE a commit.

    Pure text, zero subprocesses, zero file reads — the cheap precondition that
    keeps `check_git_commit_to_main` off the hot path for the ~everything else.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "git":
            continue
        flags, operands = _flags_and_operands(_git_strip_global_opts(argv))
        if operands and operands[0] == "commit" and not any(
                _is_dry_run_flag(f) for f in flags):
            return True
    return False


def check_pkill_full_pattern(cmd):
    """`pkill -f <pattern>` matches the guard's own caller — and has killed it.

    RULES.md 🔴: "Never let a `-f` pattern reach `pkill`". `-f` matches against the
    FULL command line of every process, so the pattern text matches the very shell
    that is running the `pkill`, and a background script that pkills "its own job"
    kills ITSELF. The documented replacement is to resolve PIDs first: `pgrep -f
    <pat>` -> skip `$$` -> confirm each via `/proc/<pid>/cmdline` -> `kill "$p"`.

    Measured ALLOW against the live hook before this landed (same sweep as above):
        ALLOW  pkill -f e2e/run.sh
        ALLOW  pkill -f "opencode run"

    Deliberately narrow, so the replacement recipe stays usable: `pgrep -f` is a
    READ and is untouched (denying it would leave the deny message pointing at a
    blocked command), and `pkill <name>` without `-f` matches process NAMES only,
    which cannot match the caller's command line and is not what RULES bans.
    Bundled short flags are caught (`pkill -9f`, `pkill -ef`) — the guard tests the
    flag's CHARACTERS, not the token. `-F <pidfile>` is a different, uppercase
    option and does not match.
    """
    for argv in commands(cmd):
        if os.path.basename(argv[0]) != "pkill":
            continue
        flags, _ = _flags_and_operands(argv)
        if not any(f == "--full" or (not f.startswith("--") and "f" in f[1:]) for f in flags):
            continue
        return ("`pkill -f <pattern>` is blocked by your RULES. `-f` matches the FULL command "
                "line of every process — including the shell running this very command — so the "
                "pattern matches your own caller and a background script that pkills 'its own job' "
                "kills ITSELF. Resolve PIDs first, then kill them by number: `pgrep -f <pat>` -> "
                "skip `$$` -> confirm each via `/proc/<pid>/cmdline` -> `kill \"$p\"`. `pgrep -f` "
                "is a read and stays allowed, as does `pkill <name>` without `-f` (that matches "
                "process names, which cannot match your own command line)."
                + _QUOTING_ESCAPE_HATCH)
    return None


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
# 🔴 `check_talosctl_reset`, `check_mkfs` and `check_dd_to_block_device` were
# added later on 2026-08-02 as the TENTH, ELEVENTH and TWELFTH checks, by the
# same kind of explicit operator decision. Measured against the live
# ~/.claude/hooks/bash-guard.py BEFORE the move, with `git add -A` and
# `git reset --hard` as positive controls that came back DENY in the same sweep:
#     ALLOW  talosctl -n 192.168.50.94 reset
#     ALLOW  mkfs.ext4 /dev/sdc
#     ALLOW  dd if=/dev/zero of=/dev/sdc
# i.e. wiping a cluster node, formatting a disk and overwriting a block device
# were all permitted, unprompted, on the operator's primary tool. None of the
# three has ANY benign use from an agent on these hosts: the read/inspect
# neighbours (`talosctl version`, `talosctl -n <ip> get members`, a file-to-file
# `dd`, `dd of=/dev/null`) do not match, and `mkfs` matches on the PROGRAM name
# so merely naming it in an argument is not a command.
#
# They sit BEFORE check_heredoc_to_file and check_cd_then_git so that a command
# tripping both reports the DEVICE-DESTRUCTION reason rather than the token-waste
# or use-`git -C` reason — the more serious of the two problems, the same
# ordering rationale the git argv checks above use.
#
# 🔴 `check_rm_rf_critical` was deliberately NOT moved with them. See
# _IRREVERSIBLE_CHECKS below for why, and do not "finish the job".
#
# 🔴 `check_git_commit_to_main` and `check_pkill_full_pattern` are the THIRTEENTH
# and FOURTEENTH, added 2026-08-10 by the same kind of explicit operator decision,
# and for the same reason the others were: both are 🔴 in RULES.md and both were
# measured ALLOW against the live ~/.claude/hooks/bash-guard.py before the move,
# with `git add --all` and `git stash` as DENY positive controls in the same sweep:
#     ALLOW  git commit -m "wip"                        (cwd = devrc, on `main`)
#     ALLOW  git commit --amend --no-edit
#     ALLOW  git -C /home/zach/workspace/devrc commit -m "wip"
#     ALLOW  pkill -f e2e/run.sh
# commit-to-main is the clearest case in the whole rulebook where PROSE HAS
# DEMONSTRABLY FAILED: it is 🔴 in three separate files and was violated twice in
# four days anyway (2026-08-06, two un-pushed commits blocked `ship.sh` for hours;
# 2026-08-09, three more, rescued as PR #366). Neither incident was a
# misunderstanding of the rule — compliance decays inside a long session and prose
# cannot re-assert itself, while a PreToolUse hook fires on every call.
#
# They sit AFTER the three device/cluster-destruction checks and BEFORE
# check_heredoc_to_file / check_cd_then_git, which is the same "report the more
# serious problem" ordering the checks above use. Both directions matter:
# `talosctl -n <ip> reset && git commit -m done` still reports the NODE WIPE, and
# `cd /repo && git commit -m x` reports the wrong-BRANCH problem rather than the
# use-`git -C` one. That second ordering is also why check_git_commit_to_main
# resolves a leading `cd <path>` itself — running first means it cannot rely on
# check_cd_then_git having rejected the shape.
#
# 🔴 check_git_commit_to_main is the first check in this file that READS THE WORLD
# (it shells out to `git` to resolve the branch) and the first that FAILS OPEN.
# Both properties are argued at the definition; do not "make it consistent" with
# the pure fail-closed checks without reading that.
_CLAUDE_CODE_CHECKS = [
    check_git_add_all,
    check_git_reset_hard,
    check_git_reset_hard_argv,
    check_git_stash,
    check_git_clean_force,
    check_talosctl_reset,
    check_mkfs,
    check_dd_to_block_device,
    check_git_commit_to_main,
    check_pkill_full_pattern,
    check_heredoc_to_file,
    check_cd_then_git,
    check_private_key,
    check_secret_or_ip_publish,
]

# 🔴 THE ONE CHECK THAT REMAINS opencode-ONLY — and it stays that way.
#
# `opencode run` AUTO-REJECTS an `ask` rather than prompting (measured on
# 1.18.4), and the interactive TUI is the only place a human sees one, so for an
# unattended agent a hard deny is the only real control. That justifies the deny
# for opencode. It does NOT justify it for Claude Code, and the difference is
# specific to this check:
#
#   `rm -rf` has legitimate, FREQUENT use on these hosts — build directories,
#   `node_modules`, `.direnv`, throwaway worktrees, scratch trees under /tmp.
#   `check_rm_rf_critical` is narrow (only `/`, `~`/`$HOME`, `.`/`..`, and the
#   top-level system dirs), but "narrow" is not "never": the operator's own
#   cleanup habits put `rm -rf` in front of this guard routinely, and a guard
#   that fires during routine cleanup trains its subject to route around it.
#   A guard that has been routed around is worse than no guard, because it also
#   reports safety. In Claude Code the fallback is a PROMPT the operator sees —
#   the exact control opencode lacks — so the deny buys much less there.
#
# 🔴 This narrowness is a DECISION, not an oversight, and it is NOT the residue
# of a half-finished migration: talosctl/mkfs/dd moved to the claude-code policy
# on 2026-08-02 and `check_rm_rf_critical` was held back in the SAME change, on
# purpose. Do not "finish the job". Moving it is its own operator decision with
# its own evidence, and the thing to bring is a measurement of how often the
# fatal-target set would fire on real sessions — not the observation that it is
# the last one left.
_IRREVERSIBLE_CHECKS = [
    check_rm_rf_critical,
]

POLICIES = {
    "claude-code": list(_CLAUDE_CODE_CHECKS),
    "opencode": list(_CLAUDE_CODE_CHECKS) + list(_IRREVERSIBLE_CHECKS),
}


def evaluate(cmd, policy="claude-code", cwd=None):
    """Return a deny reason, or None to allow. Unknown policy raises — a typo
    must not silently degrade to "no checks".

    `cwd` is the directory the command will run in. Only checks marked
    `@_wants_cwd` receive it; the rest keep the pure `f(cmd)` signature, so this
    is additive and no existing check changed. When the caller does not supply
    one, a cwd-taking check falls back to the guard process's own cwd — which is
    a good default (the hook runs in the session's directory) but not a
    guarantee, which is why bash-guard.py passes the PreToolUse payload's `cwd`
    explicitly rather than relying on it.
    """
    if policy not in POLICIES:
        raise KeyError(f"unknown guard policy {policy!r}; known: {sorted(POLICIES)}")
    for chk in POLICIES[policy]:
        reason = chk(cmd, cwd) if getattr(chk, "wants_cwd", False) else chk(cmd)
        if reason:
            return reason
    return None


# =========================================================================== #
# CLI — the seam the opencode plugin calls.
#
# stdin : {"command": "<shell text>", "cwd": "<dir>"}   ("cwd" optional)
# stdout: {"decision": "deny", "reason": "…"}  |  {"decision": "allow"}
# exit  : 0 on a verdict, 2 on bad input (the caller must fail CLOSED on 2).
#
# `cwd` was added 2026-08-10 for check_git_commit_to_main and is OPTIONAL — an
# older caller that omits it (the opencode plugin, until it is taught to send one)
# still gets a verdict, with the guard process's own cwd as the fallback. A
# missing cwd must never turn into an error: this seam is the one opencode fails
# CLOSED on, so a schema tightening here would deny every bash call in opencode.
# =========================================================================== #
def _cli(argv):
    policy = "claude-code"
    if "--policy" in argv:
        policy = argv[argv.index("--policy") + 1]
    try:
        data = json.load(sys.stdin)
        cmd = data.get("command") or ""
        cwd = data.get("cwd") or None
    except Exception as exc:
        print(json.dumps({"decision": "error", "reason": f"unreadable input: {exc}"}))
        return 2
    if not isinstance(cmd, str):
        print(json.dumps({"decision": "error", "reason": "command is not a string"}))
        return 2
    if cwd is not None and not isinstance(cwd, str):
        cwd = None
    try:
        reason = evaluate(cmd, policy, cwd)
    except Exception as exc:
        print(json.dumps({"decision": "error", "reason": f"guard failed: {exc}"}))
        return 2
    print(json.dumps({"decision": "deny", "reason": reason} if reason
                     else {"decision": "allow"}))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
