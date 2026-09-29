# Handoff: check-clickup-addressed migrated to devrc — deletion FROZEN — 2026-08-22

## Run this first
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
gh pr view 381 --repo ZacxDev/homelab-infra --json state,mergedAt
git -C ~/workspace/civit/datapacket-talos log --oneline origin/trunk -5 -- .claude/skills/check-clickup-addressed/
```
🔴 RECALL, NOT LIVE OBSERVATION. Everything below is a pointer to verify, not a current reading.

## State in one line

The skill **moved to devrc and is deployed**; the **talos copy is still there, still being
developed, and its deletion is deliberately frozen**. Both copies exist right now, on purpose.

## Done

- **devrc PR #709 MERGED** (`845134d9a1f7`). Docs at `claude/skills/check-clickup-addressed/`
  (`SKILL.md` + `reference/validation-history.md`), code + suite at
  `scripts/check-clickup-addressed/`. Registered as a gated target of `scripts/run-tests.sh`
  (176 tests, floor 168) — **no gate had ever run this suite before**.
- **Deployed and verified by resolution AND content**, not by the switch's exit code:
  `readlink -f ~/.claude/skills/check-clickup-addressed/SKILL.md` terminates in
  `/nix/store/…-devrc-claude-skills/`, the deployed body says `**176** collected`, and the
  scripts are correctly ABSENT from `~/.claude/skills/` (they run from the working tree).
  🔴 `home-manager switch` needs `--impure` here — without it, it fails on `/home` path access
  and deploys NOTHING while the harness reports exit 0.
- **The laptop is NOT converged.** `scripts/ship.sh` is the both-hosts primitive.

## FROZEN, and why

`datapacket-talos` is **actively developing its copy** — measured 2026-08-22: two merges
20 minutes apart, plus an open PR. Deleting the directory would strand in-flight work and
drop fixes.

**The freeze lifts when:** talos **#1246** lands or closes AND the copy goes quiet.
**Then, in ONE window:** port the delta into devrc, then delete.

### The delta to port before deleting (re-derive, this list rots)
```bash
git -C ~/workspace/civit/datapacket-talos log --oneline 1c4418011..origin/trunk \
  -- .claude/skills/check-clickup-addressed/
```
Known at time of writing, all AFTER the last port:
- `ed1c004e7` (#1247) — transcript scan made OPT-IN. **A behaviour change, not a fix.**
- `05b1e4f4a` (#1249) — stop asserting an English word is a repo.
- `#1246` (open) — unreadable own-date, minute-rounded tie, silent suppression.

### The prepared removal
Local branch **`zach/ccua-removal-prepared`** in the talos clone (worktree at
`…/scratchpad/wt-talos`) holds the `git rm` plus the SUPERSEDED banner on
`claudedocs/handoff-ccua-hardening-2026-08-21.md`. The `git rm` is trivially re-derivable;
**the banner text is the only bespoke content worth recovering.**

### How to port (the mechanical part)
`git apply --3way` **cannot** work cross-repo — the blobs live in the other repo. Use
`format-patch` + path rewriting + `--reject`:
```
.claude/skills/check-clickup-addressed/scripts/  -> scripts/check-clickup-addressed/
.claude/skills/check-clickup-addressed/test/     -> scripts/check-clickup-addressed/tests/
.claude/skills/check-clickup-addressed/SKILL.md  -> claude/skills/check-clickup-addressed/SKILL.md
.claude/…/README-validation.md -> claude/skills/check-clickup-addressed/reference/validation-history.md
```
Then fix `SCRIPT_DIR = Path(__file__).parent.parent` in any new test file (the old layout had
`/ "scripts"`), and bump BOTH the SKILL.md count and the `TARGET_FLOORS` entry.

🔴 **Verify the port carried the FIX, not just its tests**: revert only the production files
to pre-port state and confirm the new tests go RED. When #1238 was ported, 12 of its 15 tests
went red — that is what proves a production hunk was not dropped.

## 🔴 The lesson that cost the most to learn

**The deletion is what caught the near-regression.** Rebasing the removal onto current trunk
conflicted `UD` (modified upstream, deleted by me) and surfaced `#1238` — 690 insertions
including a 404-line test file — that landed AFTER the migration snapshot. A blind `git rm`
would have deleted a shipped fix and silently regressed the now-global skill to flagging
answered tickets forever.

**Migrating a live, actively-developed component is a moving target.** The source moved 17
commits in a few hours, then 2 more while the port was in review.

## Related, and NOT done

- **`render_body` upgrade (homelab-infra) — the interview-at-pickup design depends on it.**
  PR #381 gets acceptance criteria onto the ClickUp ticket and stops them being clobbered, but
  `render_body` still reads ClickUp's LOSSY `description`, so criteria arrive in the clawgate
  body as **unmarked prose** and **the pickup detector does not fire**. W2 is a prerequisite,
  not the goal. The follow-up PR owns a one-time re-PATCH of every mirrored ticket.
- **Measured ClickUp facts** (throwaway tasks, out of scope, deleted) worth not re-deriving:
  `PUT {"markdown_content":…}` does NOT store verbatim — it re-serialises `- x` to `*   x` —
  but H2/bold/link/blockquote survive and **soft-wrapped paragraphs are preserved**. The PUT
  RESPONSE does not carry `markdown_description` at all. Plain `description` is fully lossy
  (0 of 184 H2 headings survive).

## Tripwires this session hit twice each
- **A tool's summary line contradicting its own data.** A background task reported
  "completed (exit code 0)" over `GATE: RESULT=FAIL exit=1` (trailing `grep` set the status),
  and a probe printed "soft wrap JOINED" over data showing it preserved (its check omitted the
  normalisation the production code applies). **Read the content, never the printed verdict.**
- **Reading a stale tree and believing it.** A doc claim was flagged as rot from the devrc
  primary clone (detached, behind); current `main` had already corrected it.
