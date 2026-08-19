# Always-loaded files, the overshoot clause, landing, and multi-agent execution

Routed from the prune-skill core (§Budgets / §3 / §4 / §6) — `~/.claude/skills/prune-skill/SKILL.md`, source `~/workspace/devrc/claude/skills/prune-skill/SKILL.md`. The core carries the imperatives; this
carries the reasoning and the measured cases.

## Always-loaded files (`CLAUDE.md`, `claude/RULES.md`) — the playbook does not apply

There is no trigger, so a `reference/` sidecar saves **nothing**: the file is pasted into every
session whole, and a pointer defers no load. Only two levers reduce per-session cost:

1. **MIGRATION into a trigger-gated owner** (a skill), where the content genuinely costs 0 until
   its trigger fires.
2. **DELETING content that is wrong.**

🔴 **Migration converts an always-on rule into one that may never fire.** So a rule that bites
people who are **not** doing that subsystem's work must STAY. Worked example: a cluster's node
topology moved into the node-provisioning skill, but four rules stayed behind — including one
warning that a particular control-plane node carries a web-pool label *deliberately*, because
removing it evicts production pods. That rule was buried **inside a table cell** of the block
being moved; it had to be **lifted into prose first**, or it would have travelled with the table
and become invisible to everyone not provisioning a node.

🔴 **Replacing prose with a pointer can make discovery WORSE.** In the same campaign the target
skill was not in the always-loaded file's skill-routing table at all — so the pointer had to be
added *and* a routing row created. Check reachability before you delete the prose.

🔴 **Correctness outranks bytes here.** A staleness pass over one such section produced 13
corrections, 3 verified deletions and 2 dedups and ended **+1,378 B larger**. That is a success,
not a regression: report the growth, do not optimise it away.

**Check what governs the file before executing the auditor's number.** `RULES.md` is not a skill
body: always-loaded and under its own tighter gate. `scripts/tests/test_rules_size.py` **owns**
its cap and headroom floor — read them there, never from a doc that restates them (a restated
copy once went stale by two revisions, in the very document warning against that). That gate
passing means the right pass is *headroom* — demote narrative to `claude/RULES-ARCHIVE.md`, the
ungated demand-loaded SINK — not a 12 KB cut that guts rule scope.

## The overshoot clause

Landing over target is allowed **once, deliberately, and never by drift**. Condition: getting
under it would cut the core's *routing value itself* — an index the whole skill navigates by.
Then stop at the smallest defensible size **under the hard cap** and record the reason and the
number in the commit message, so the next pass re-derives instead of re-litigating.

Measured: in a 9-skill campaign, **8 of 9 landed under target unaided** (mean core 8,576 B). The
ninth was router-heavy and landed 1,516 B over, with its gate index intact and the reason
written down. The target is not the thing that failed; the absence of an escape clause was.

## Landing: a push is not a saving

A skill body loads from the **deployed copy**, not from the ref you pushed — and the deployed
copy may not be the clone. This is "a deploy reporting success is a claim about the DEPLOY, not
about the CONSUMER", instantiated. **Resolve the deployed path first:**

```bash
SKILL=prune-skill        # ← the skill you pruned
readlink -f ~/.claude/skills/"$SKILL"/SKILL.md
```

| Resolves to… | Meaning |
|---|---|
| a path **inside the repo** (`mkOutOfStoreSymlink`) | the clone IS live — re-sync it and you are done |
| **`/nix/store/…`** (a `home.file` copy — the devrc default) | the clone is **NOT** live; a `home-manager switch` is required |

🔴 **Measured on this very skill:** the clone held 14,918 B while `~/.claude/skills/prune-skill/SKILL.md` resolved into `/nix/store` and still served **11,083 B** — the pre-change body. Re-measure at the **resolved** path, never `wc -c` in the clone.

For a plain clone the other failure is staleness: after five pushed prunes one was **160 commits
behind, still serving the 92,270 B body** — every session paying the old cost while every commit
verified green. Re-sync after every prune:

```bash
REPO=~/workspace/devrc; MAIN=main        # ← set these two
git -C "$REPO" fetch origin && git -C "$REPO" merge --ff-only "origin/$MAIN"
```

`--ff-only` is the point: it cannot conflict or autostash — it fast-forwards or refuses.

🔴 **Expect it to refuse**, because other sessions leave in-flight docs in a shared clone.
Classify every blocker against committed blobs before touching it:

| Blocker is… | Verdict |
|---|---|
| byte-identical to `origin/<main>` | read-refresh artifact — discarding is lossless |
| byte-identical to an **OLDER** commit | stale orphan — discarding is **CORRECT**; keeping it REVERTS work |
| matching **NO** commit | 🔴 genuine unsaved work — move it aside, ff-merge, put it back |

One such sweep found 20 dirty tracked files of which 18 were read-refresh artifacts and 2 were
stale orphans (one of them the pre-prune body of a skill just pruned — keeping it would have
undone the prune), plus an untracked doc carrying status updates present in **no commit**.

🔴 **Never `git stash`** to clear the tree — `refs/stash` is repo-GLOBAL and shared across every
worktree, so it sweeps up other sessions' work. Copy files aside instead.

## Multi-agent execution

The core's §3 contemplates read-only *classifier* subagents. If you dispatch *file-modifying*
prune agents:

- **one worktree each**, **≤2–3 concurrent** (4 hit a session limit simultaneously, one with
  ~330 lines uncommitted at the time)
- 🔴 **a PER-AGENT scratch path** — subagents share ONE scratchpad dir, and an agent overwrote
  the parent's verification script at the same filename
- expect **non-fast-forward pushes** between siblings: rebase, never force
- 🔴 **before pushing, assert the siblings' prune commits are ancestors of your HEAD and their
  skills are still small in your tree.** A stale base silently reverts a sibling's prune — one
  agent's gate correctly blocked a commit that would have taken a skill from 9,271 back to
  60,266 B.

## Landing the change — the per-repo git workflow

Moved out of the core (§6), which keeps three imperatives: never `git stash`, never `git add -A`,
and re-measure the DEPLOYED copy. This section adds only what is per-repo — the deployed-copy
rule, the 160-commit staleness case and the ff-merge blocker table are stated ONCE, in
§"Landing: a push is not a saving" above, and are deliberately not restated here.

- **datapacket-talos**: a **throwaway worktree off `origin/trunk`**, never the primary clone (its `CLAUDE.md` rule #10); `git push origin HEAD:trunk`, verify `git show origin/trunk:<file> | head`.
- **devrc**: feature branch + PR against `origin/main`.
- 🔴 **Never `git add -A`** in either — stage explicit paths. (`git stash` is banned outright: see above.)

## Routing-path forms by deployment (the §4 lookup table)

Moved out of the core; the core keeps the imperative. Which form is correct depends on how the skill is deployed:

| Deployment | Write the path as |
|---|---|
| **devrc skill** (`claude/skills/<name>/`) — `home.file … recursive`, so `reference/` DOES ship | BOTH forms: `` `reference/<topic>.md` `` at `~/.claude/skills/<name>/reference/`, source `~/workspace/devrc/claude/skills/<name>/reference/` |
| **`mkOutOfStoreSymlink` exceptions** — `browser` → `scripts/browser-bridge/`, `dl-router` → `scripts/dl-router/`; only `SKILL.md` + the CLI are linked, **not** a `reference/` subtree | repo-absolute: `~/workspace/devrc/scripts/<subsystem>/reference/<topic>.md` |
| **Repo-local skill** (a project's own `.claude/skills/<name>/`) — the whole dir ships | repo-root-relative: `.claude/skills/<name>/reference/<topic>.md`, **or** short table entries with the expansion stated once above the table |
