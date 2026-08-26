{}:
{
  enable = true;
  lfs.enable = true;
  settings = {
    url."git@github.com:".insteadOf = "https://github.com/";
    init.defaultBranch = "trunk";
    pull.rebase = true;
    push.autoSetupRemote = true;
    rerere.enabled = true;
    merge.conflictstyle = "zdiff3";
    diff.algorithm = "histogram";
    # 🔴 autoStash must stay FALSE on both counts — it is the one way the
    # repo-global stash gets used WITHOUT anyone typing `git stash`.
    #
    # RULES.md forbids `git stash` in any repo shared with other sessions or
    # agents: `refs/stash` lives in the COMMON git dir, so every worktree of a
    # repo shares one stack. Two parallel subagents stole each other's work that
    # way (2026-07-25). The PreToolUse guard (scripts/claude-hooks/guard_core.py,
    # `check_git_stash`) enforces that ban — but it matches COMMAND TEXT, and
    # autoStash is git pushing and popping the shared stack INTERNALLY. Nobody
    # types `git stash`, so the guard structurally cannot fire.
    #
    # With `pull.rebase = true` above, that made every `git pull` on a dirty
    # tree a silent stash push/pop against a stack shared with every concurrent
    # worktree. MEASURED 2026-08-03: a subagent's routine `git rebase` printed
    # "Created autostash" against a stack holding 9 entries belonging to other
    # sessions.
    #
    # false is the FAIL-CLOSED choice: rebase/merge now REFUSE on a dirty tree
    # instead of silently reaching for the shared stack. The refusal is the
    # signal — resolve it by committing, or by copying the file aside
    # (`cp <file> /tmp/…`), which is what RULES.md prescribes.
    rebase.autoStash = false;
    merge.autoStash = false;

    # 🔴 SSH KEEPALIVES, or a long pre-push gate kills the push (#782).
    #
    # MEASURED 2026-08-26 against real github.com, twice independently:
    # github.com closes an IDLE `git-receive-pack` session after ~360s (361s in
    # both runs; the clean one gave rc=255 and "Connection to github.com closed
    # by remote host."). And git opens AND negotiates the connection BEFORE it
    # runs `pre-push` — measured with a GIT_SSH_COMMAND stamp rather than
    # inferred from interleaved output: ssh-launch 04:12:04Z, hook START
    # 04:12:05Z. So the connection sits idle for the hook's ENTIRE runtime.
    #
    # `githooks/tests-on-push.sh` is exactly such a hook and it runs the whole
    # suite. Paired push arms, identical 420s hook, one variable changed:
    #
    #     no keepalive           -> push rc=141 (SIGPIPE), branch ABSENT
    #     ServerAliveInterval=30 -> push rc=0,             branch CREATED
    #
    # An idle session carrying the keepalive was still alive at 1367s (3.8x).
    # This is NOT flaky — it is a hard threshold, and it fires more often the
    # longer the suite grows.
    #
    # 🔴 IT READS AS A NETWORK FLAKE. The hook prints its own "devrc test suite
    # passed" AFTER the connection is already dead, and a wrapper's trailing
    # command swallows the 141. Verify a push with `git ls-remote`, NEVER with
    # the wrapper's exit code.
    #
    # 🔴 WHY HERE AND NOT `githooks/install.sh`, WHICH IS WHERE THIS FIX WAS
    # FIRST WRITTEN. That installer runs `git config --global`, and on a
    # home-manager host THAT CANNOT WORK: `git config --global` resolves to
    # ~/.config/git/config, which is a symlink into the read-only nix store —
    # THE FILE THIS MODULE GENERATES. Measured: the installer dies
    # "could not lock config file ... Read-only file system", rc=255, on its
    # PRE-EXISTING core.hooksPath line, before reaching anything new. (That is
    # also why `core.hooksPath` reads empty on this host, which the issue had
    # attributed to another session toggling it.) A fix placed in the installer
    # is INERT on exactly the machines devrc targets.
    #
    # 🔴 THE INTERVAL MUST STAY WELL UNDER THE ~360s CLOSE. A keepalive longer
    # than the server's idle timeout is not a weaker fix, it is NO fix, and it
    # would look identical. Pinned by scripts/tests/test_push_keepalive.py
    # against the MEASURED close, not against a copy of itself.
    #
    # ⚠ THE TRADE, STATED. ServerAliveCountMax is set EXPLICITLY rather than
    # inherited (the default is 3): ssh now tears a connection down after
    # interval x countmax = 30 x 6 = 180s of SERVER UNRESPONSIVENESS. Before
    # this, only TCPKeepAlive applied and a stall of any length survived. On a
    # flaky link (Mullvad is active on the workbench host) a >180s stall will
    # now kill a push that previously hung on and recovered. 6 rather than the
    # default 3 buys back some of that tolerance while still bounding it. This
    # is the fix's own failure mode; it is a trade, not a free win.
    #
    # ⚠ AND IT OVERRIDES ~/.ssh/config, FOR EVERY HOST AND EVERY REMOTE. A `-o`
    # on the command line beats ssh_config. Measured: `ssh -G submodel-8x-66`
    # reports serveraliveinterval 120 (from ~/.ssh/include/submodel.conf), but
    # with this option in front it reports 30. Harmless where the per-host value
    # is merely longer, NOT harmless if someone deliberately chose 0 or a long
    # interval for a host. Host aliases, IdentityFile and IdentitiesOnly are
    # unaffected — only the options named here are forced.
    #
    # 🔴 GIT_SSH_COMMAND (env) BEATS core.sshCommand (config), so any script
    # that exports the former bypasses this entirely. devrc has two such
    # exports; both carry the keepalive, and
    # scripts/tests/test_push_keepalive.py asserts that EVERY such export does
    # — a requirement spread across three sites regenerates the same bug at the
    # site nobody updated.
    core.sshCommand = "ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=6";
  };
}
