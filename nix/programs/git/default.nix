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
  };
}
