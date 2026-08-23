# prune-index — writing into the store safely, and verifying the cut

Loaded on demand. The core (`~/.claude/skills/prune-index/SKILL.md`) carries the
safety rules in short form; this is the mechanism behind them, plus the
verification the auditor cannot perform.

## 🔴 Why the auditor may never write, and what that buys you

`scripts/subsystem-audit.py` has **no write path at all** — no `write_text`, no
`mkdir`, no `unlink`, no `shutil`, and its one shell-out (`git`) refuses any repo
path that resolves inside the store root. Two tests hold that: one byte-hashes
every fixture file before and after a full audit + render, the other greps the
source for write calls. Both exist because a hash test alone goes green for a
write on a path no fixture happens to reach.

The point is not tidiness. It means **the measurement can never be the thing that
damaged the store**, so when something looks wrong after a prune, the audit is
not on the list of suspects. Keep it that way: put any new mutation in the skill,
which is confirm-gated, not in the script, which is not.

## The store is git, and that is a hazard rather than a safety net

`~/.claude/analyze-service-index/` is **not** one repo: each `<scope>/` is its
own remote-less repo, the root is not a repo, and an **out-of-band autocommit**
runs against them. Consequences:

- 🔴 **`git stash` is repo-GLOBAL.** `refs/stash` lives in the common git dir, so
  a concurrent session can pop or drop yours. Never stash here for any reason —
  set work aside with `cp <file> /tmp/…` and copy it back.
- 🔴 **Never `git reset --hard`, `git clean`, or `git checkout --`** in a scope.
  Each destroys curated content that has no other copy, and the autocommit means
  "it's in git" is a claim about *some* commit, not about the bytes you want.
- 🔴 **Never add a remote and never push.** Nothing in this store leaves the
  machine.
- **Write the file and run no git command.** Committing a scope is the store's
  own concern.

## The confirm-gated write, step by step

1. Audit first. Never propose a cut the audit did not classify.
2. Back up (`cp -a`, `&&`-chained, count the files). This is the only safety net.
3. For ONE entry, build the proposed new bytes.
4. Present a **unified diff** against the current file — one compact block — and
   ask a single yes/no. Never batch a scope behind one prompt: a bad cut must be
   rejectable on its own.
5. On confirm: **re-read the file** (a concurrent session may have appended since
   step 3), re-apply the change to *current* bytes, then plain `Write`. On
   decline, discard and move on.
6. Never touch a file the user did not confirm.

🔴 **Build the new entry by VERBATIM SLICING of the original**, not by retyping
it. Content survival then becomes structural instead of something you have to
trust, and the §"gap audit" below is what checks it. The loss mode slicing does
NOT cover: a bullet you *summarise* into a shorter one and slice into nothing is
silently gone — and it looks like good pruning.

## 🔴 Never copy an entry's content into a public place

devrc is PUBLIC. Entries carry client-identifying infrastructure detail down to
named individuals, and the fail-safe is that an entry with no `sensitivity:`
field reads as `client-confidential`.

So: no entry prose in a commit message, a PR body, an issue, a test fixture, a
`claudedocs/` doc in devrc, or a chat summary that will be pasted somewhere. What
you MAY record is **aggregate integers** — entry counts, byte totals, population
counts, "N of M". devrc commit `60e6d9d` exists because this data class had to be
scrubbed out of a public repo retroactively.

**A `NO HOME` bullet is the sharp edge here**: the fix is "write the record
first", and the obvious place — a devrc `claudedocs/` doc — is exactly where it
must not go if the content is client-confidential. Write it in the **owning
repo's** `claudedocs/`, or as a commit message there, and let the bullet point at
that.

## Verify — the four checks the auditor does NOT perform

The audit's structural pass tells you bytes went down and nothing collides. None
of the following is visible to it:

1. **OPEN parity.** Count `OPEN:` bullets before and after; the numbers must be
   IDENTICAL. A prune that lost one destroyed the store's only irreplaceable
   content while every other number improved. This is the single most important
   check on this page.
2. **The gap audit.** Diff each rewritten entry against the backup and read every
   removed line. For each, name where it now lives. A line you cannot place is a
   loss, not a saving.
3. **The destination really holds it.** For every `EVICT_RESOLVED`, open the
   target the audit named and confirm it carries the finding — not merely that it
   exists. `git cat-file -e` proves a commit is reachable, not that its message
   or diff records the gotcha the bullet described.
4. **Drive it once for real.** Run `/analyze-service` against the pruned service
   and check the brief still answers what the cut bullets used to. Content being
   present is not the same as the entry still being useful.

🔴 **A control must mutate the DESTINATION, never your new text.** If you test
"did this survive?" by changing the entry you just wrote, a pass proves nothing —
that is an invalid control, not a clean result.

## Landing

Nothing about this store lands in a PR: the writes are local and final, and the
store never leaves the machine. What DOES land in devrc is any change to the
audit script, this skill, or the `analyze-service` reference docs — those go
through the ordinary feature-branch + PR flow, gated by
`scripts/gate.sh --tier both --set all` on the tree rebased onto current
`origin/main`.

🔴 **A new file must be `git add`ed** or the flake silently omits it from the
deploy — that includes a new `reference/*.md` in this directory. And a change to
a skill is not live until `home-manager switch` (or `scripts/ship.sh`) runs:
`~/.claude/skills/prune-index/` is a nix-store symlink, so editing the repo
changes nothing until the switch. `readlink -f` is the arbiter, never a diff.
