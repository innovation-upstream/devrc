"""Deterministic byte-size gate for `claude/RULES.md`.

WHY THIS EXISTS
---------------
`claude/RULES.md` is PAID TWICE on every unit of work:

  1. Claude Code loads it into every session (via `~/.claude/CLAUDE.md`'s
     `@RULES.md` import, deployed by `nix/home.nix`).
  2. `nix/home.nix` CONCATENATES it into `~/.config/opencode/AGENTS.md`
     (PRINCIPLES + RULES + opencode-addendum), so every opencode session
     carries it too -- including the cheap flash-model `nav` subagent whose
     entire job is glob/grep/read and which needs none of it.

It was append-only by process, and it grew accordingly -- measured via
`git show <rev>:claude/RULES.md | wc -c`:

    2026-06-23    8,348      2026-07-30   14,147
    2026-07-11    9,487      2026-08-02   41,566   <- 2.9x in three days

Twelve commits touched it in the three days before the split, titled "rescue
stranded lessons" and "record what today measured". Every one of those commits
was individually correct: a real lesson, really measured. Nothing in the process
ever asked what the file cost, so nothing ever pushed back.

`claude/RULES.md` itself says: "Prefer deterministic/structural fixes over
prompt-tuning, prose instructions, or suffix/keyword heuristics." A comment at
the top of RULES.md asking people to keep it short is the prose fix, and the
growth curve above is what the prose fix achieves. This file is the structural
one.

WHAT THE CEILING PROTECTS -- AND WHAT IT MUST NOT DO
----------------------------------------------------
RULES.md's opening rule is "Read every rule at its WIDEST reading", and the
`git stash` prohibition was re-broadened on 2026-08-01 precisely because a
narrow wording let a subagent walk into the failure it forbade. So this gate
must NEVER be satisfied by narrowing or deleting a rule.

The eviction target is `claude/RULES-ARCHIVE.md`: worked incidents, dates, byte
counts, store paths, PR numbers, retracted theories. The RULE -- its imperative,
its priority marker, its triggers, its widest scope, and any procedure needed to
act on it -- stays in the core. The playbook below says this on every failure,
because the failure message is the only documentation anyone reads at the moment
they are over budget.

The numbers below are the SINGLE source of truth. Any other mention of them
(CLAUDE.md, handoff docs, PR bodies) must cross-reference this module rather
than restate the literal -- a second hand-maintained copy of the number is
exactly how the drift regrows.

This module lives in `scripts/tests`, which is in `HERMETIC_TARGETS` in
`scripts/run-tests.sh`, so it runs in `nix build .#checks.x86_64-linux.pytests`
-- the repo's real pre-merge gate.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RULES_MD = REPO_ROOT / "claude" / "RULES.md"
ARCHIVE_MD = REPO_ROOT / "claude" / "RULES-ARCHIVE.md"

# The hard ceiling: RULES.md must never exceed this many bytes.
#
# Sized off the post-split measurement, NOT off a round number and NOT off an
# aspiration. At the split RULES.md was 33,105 B (down from 41,566 B) and the
# ceiling was set ~1.4 KB above it -- room for a genuinely new rule or two
# before anyone had to make a decision. ⚠ That describes the ORIGINAL sizing,
# not the constant below: the dated ledger entries that follow have raised it
# repeatedly, so read `MAX_BYTES` for the current value and never infer it from
# this paragraph. (It read "this ceiling is that plus ~1.4 KB" in the present
# tense until 2026-08-22, by which point it was understating by ~5.8 KB.)
#
# 🔴 This is an ANTI-REGROWTH gate, not a target. It is deliberately NOT set to
# the 15 KB that motivated the split -- see the module docstring of the split PR
# and the note on MIN_HEADROOM_BYTES. Ratcheting it DOWN as rules consolidate is
# welcome and is the intended direction of travel. Ratcheting it UP requires
# saying in the commit message which rule could not be expressed in the budget.
#
# 2026-08-11: 34,500 -> 35,200 (+700 B). The rule that would not fit was the
# worktree-isolation rule then worded "A worktree isolates the REPO, not the
# SESSION" (Git Workflow), 674 B. It cleared the hard ceiling and tripped only
# MIN_HEADROOM_BYTES. (That wording no longer exists verbatim -- the 2026-08-13
# consolidation below folded it into "a worktree isolates a working DIRECTORY
# only". Kept here as the record of what the bump bought.)
#
# Carving the 635 B instead was measured and rejected, not skipped: at the time
# of the bump RULES.md held 87 bullets / 28,628 B of bullet text in 33,561 B, and
# exactly ONE untagged bullet exceeded 600 B -- the worktree bullet this rule
# extends. Every other large bullet already carries a `→ archive:` tag, i.e. its
# evidence has already been evicted; the 1,951 B "Validate the INSTRUMENT" bullet
# is four rules with four separate tags. The remaining 600 B candidates
# (squash-ancestry, `gh pr view` authority) are ~590 B of pure imperative plus
# the procedure needed to comply. Reclaiming from any of them means narrowing a
# rule, which the playbook below forbids in bold and which the `git stash` ban
# already had to be re-broadened once to undo.
#
# The honest reading: eviction has been run to completion, so the next real rule
# costs ceiling. If that recurs, the answer is consolidation (the four scattered
# worktree bullets are the obvious candidate), not another bump.
#
# 2026-08-13: that consolidation was done (the cluster had grown to five bullets
# after PR #447). Five sibling bullets became THREE: one `worktree add` recipe,
# one "a worktree isolates a working DIRECTORY only" frame with the unisolated
# surfaces as sub-bullets (REPO / SESSION / ENVIRONMENT), and the base-clone
# re-sync bullet, which stayed top-level. Result: 33,300 -> 33,264 B. (The
# consolidation itself landed 33,229; an audit then found the new frame verb
# described only one of its three surfaces correctly, and the reword to fix that
# spent 35 B back. Both changes ship in the same merge, so -36 B is what the
# tree actually sees.)
#
# 🔴 That -36 B is the honest yield, and it is the finding, not a disappointment:
# every clause that survived is scope-bearing, so the only bytes available were
# genuine restatement -- one duplicated `worktree add` recipe, one duplicated
# "never two file-modifying agents in one checkout", and the no-error framing
# (hoisted from the cross-repo bullet, which was the only one that spelled it
# out). Do NOT read this entry as "there is another -71 B in there next time".
# There is not; the next real rule still costs ceiling.
#
# What consolidation DID buy, and why it was worth shipping for 71 B: a fourth
# worktree hazard now attaches to the sub-bullet list for the cost of its own
# text, instead of arriving as a sixth sibling bullet that re-states the frame.
# (That saving is an ESTIMATE of a hypothetical -- it has not been measured, and
# cannot be until such a rule is actually written.)
#
# 🔴 The surface list is deliberately NOT closed, and the frame says so. A fourth
# already exists in this file -- the machine/process surface at the pgrep/pkill
# bullet, where a box-wide pattern reaches a sibling agent's processes
# (-> archive: sibling-agent-kill). The archive's own wording is "not a private
# repo and not a private machine".
#
# 2026-08-14: the 2026-08-13 estimate above got measured, and it held. A fourth
# worktree hazard arrived (`cp -a` of a worktree shares the ORIGINAL's git dir,
# because a worktree's `.git` is a FILE, not a directory) and it did attach to
# the sub-bullet list for the cost of its own text -- 394 B, with the frame,
# the "list is not closed" clause and the silent-failure framing all reused. As
# a sixth sibling bullet it would have re-stated those. The estimate is now a
# measurement of ONE instance, not a general claim; a fifth surface is still
# unmeasured.
#
# That same commit folded in two VERIFICATION lessons rather than worktree ones,
# and deliberately did NOT give either its own bullet: a guard on prose being
# walkable by rewording extends the existing "SPELLED rather than STRUCTURAL"
# bullet (same question, new artifact type), and a fixture whose value equals
# the constant it tests extends the existing "fixtures pairwise distinct" clause
# in the mutation bullet (same collision, different pair). Both therefore cost
# only their own text AND reuse the archive anchor already on those bullets, so
# neither added a `→ archive:` tag. Placement was chosen by where the reader
# already is when the hazard fires, not by which cluster the lesson came from.
#
# 🔴 The 202 B those three additions overshot the budget by was reclaimed WITHOUT
# narrowing anything. An audit measured where it actually came from, and the
# split matters to anyone reusing this as a recipe: only 133 B came out of
# EXISTING rules (stash -89, pkill -26, worktree -18); the remaining ~69 B came
# from trimming the three NEW additions themselves. An earlier version of this
# comment named only the two existing-rule sources and so read as though all
# 202 B were reclaimed from prose already in the file -- which would make
# "reclaim without narrowing" look about 50% cheaper than it is. What the
# existing-rule half looked like:
# the `git stash` section stated "for ANY reason" THREE times (heading, a 🔴
# clause, and "regardless of why") -- the third was dropped and the other two
# kept verbatim, since that is the exact scope clause a narrower wording once
# destroyed; and the pkill bullet's incident numbers ("~15 PIDs", "0 files
# collected and exit 144") moved out to `sibling-agent-kill`, which already held
# them verbatim and was already tagged on that bullet. What did NOT move: the
# clause saying `/proc/<pid>/cwd` CANNOT separate two agents that both sit in the
# base clone. PR #447 trimmed exactly that and re-opened the incident; it is
# named here so the next person shaving this bullet knows which sentence is load-
# bearing.
#
# The cold read is what caught the one real regression in this pass, and no test
# performs it: "run a SINGLE agent in-place -- never two file-modifying ones" had
# been shortened to "never two", which reads as forbidding a second READ-ONLY
# agent that the previous sentence explicitly permits. 20 B restored.
#
# 🔴 SO THAT BULLET SHIPPED 18 B SHORTER THAN IT WAS, and this comment previously
# recorded only the caught regression -- which reads as "untouched" to the next
# person sizing it up. The shipped wording is the MIDDLE position: "run a SINGLE
# agent in-place -- never two file-modifying ones". The antecedent survives
# because "agent" is in the same clause and the full form ("two file-modifying
# agents in one checkout WILL clobber each other") appears earlier in the same
# bullet. Shave it further and that stops being true.
#
# MAX_BYTES was deliberately NOT ratcheted down to bank that. The comment above
# said tightening the gate "is a policy change, not a byte cleanup -- it belongs
# in its own commit where it can be argued on its merits." That cuts both ways,
# and this IS that commit, in the RAISING direction.
#
# 🔴 THE ARGUMENT, so the next person can attack it rather than re-derive it.
#
# The previous ceiling left 32 B of slack above MIN_HEADROOM_BYTES -- i.e. a
# 33 B addition broke the gate. That spends the early-warning margin this floor
# exists to provide: the next contributor got the surprise, not the warning,
# which is the exact failure MIN_HEADROOM_BYTES was introduced to prevent.
#
# The honest question is whether that is a signal to EVICT or to RAISE, and it
# was answered by measurement, not preference. Eviction is exhausted:
#   * `skill-audit.py` reports ZERO work-status history and ZERO dated-lesson
#     blocks in this file -- the two categories that are evictable by rule;
#   * of the 24 lines over 500 B, 18 (14,950 B) ALREADY carry a `-> archive:`
#     tag, meaning their evidence is demoted and what remains is imperative;
#   * the 6 untagged fat lines total 3,595 B and are mechanism plus fix with no
#     narrative to move. The largest (760 B) is the worktree frame bullet -- the
#     one PR #447 trimmed and re-opened an incident doing it.
# An audit re-tested this independently across all 73 bullets and found exactly
# one untagged bullet over 600 B: the same one. There is nothing left to move.
#
# So the choice is raise the ceiling or stop accepting rules. Rules are still
# being learned -- three landed in a single day -- so the ceiling moves.
#
# 🔴 THE COST IS REAL AND IS THE REASON THIS IS NOT GENEROUS. This file loads
# EVERY session AND is concatenated into opencode's `AGENTS.md`, so every byte
# is paid TWICE per session. At the ceiling in force when this paragraph was
# written that was roughly 9.6k tokens per load and ~19k per session; at the
# current MAX_BYTES it is ~10.1k and ~20.2k. Recompute from MAX_BYTES rather
# than trusting either figure -- both go stale on every bump.
#
# Sized in the same units as MIN_HEADROOM_BYTES rather than picked round: the
# mean substantive bullet is ~500 B, so slack for SIX of them (3,000 B) plus the
# 900 B floor is ~3,900 B of free space above the current size. That is about
# three heavy days at the observed rate, which is a working margin without
# removing the pressure the gate exists to apply. A bigger jump would make the
# gate decorative; a smaller one puts the next author back here immediately.
#
# 2026-08-20: 38,400 -> 38,800 (+400 B). The rule that would not fit was the
# FIFTH worktree surface -- "its SUBMODULES", 349 B -- which the 2026-08-13 entry
# above explicitly left open ("a fifth surface is still unmeasured"). It costs
# the same shape as the fourth: one sub-bullet reusing the frame, the "list is
# not closed" clause and the silent-failure framing, so 349 B against that
# entry's measured 394 B.
#
# 🔴 THE PREVIOUS ENTRY'S "there is nothing left to move" WAS OVERSTATED, and
# that is the reusable part. Before bumping, 206 B came out of the mutation
# bullet WITHOUT narrowing it, because that text was DUPLICATED: the sweep
# blind-spot narrative and the "(three in one PR)" count both already sat in
# `mutation-sweep-blind-spots` in the archive, in more detail. Every imperative
# survives verbatim -- "Vary how the sweep is built, not just the mutant count",
# the pairwise-distinct fixture clause, and the mechanical control.
#
# The prior audit measured which bullets CARRY a `-> archive:` tag and concluded
# their evidence was already demoted. A tag means evidence was demoted ONCE; it
# does not mean the inline copy was deleted, and here it had not been. So the
# check that was missing is a DUPLICATION check -- diff each tagged bullet
# against the anchor it names -- and it is cheaper than a bump. Run it before
# raising this number again; this pass did not run it to completion, so the vein
# is not known to be exhausted.
#
# The +400 matches the 2026-08-11 precedent's modesty rather than the sizing
# formula above, which would give ~41,500 and make the gate decorative. It
# leaves 1,208 B of headroom CEILING-relative -- 308 B above the FLOOR, i.e.
# about a third of a large rule, not one. (Labelled retroactively; see the
# MIND THE BASE note in the 2026-08-21 entry below.)
#
# 2026-08-21: 38,800 -> 39,200 (+400). The rule that would not fit was "a
# control that SHARES the step you doubt is a second sample of the same
# unknown, not a control" -- 324 B, appended to the "Validate the INSTRUMENT"
# bullet in Green Test Suite. It is the third control-design failure and was
# missing: the negative control asks whether the instrument can go RED, the
# positive control whether it can OBSERVE the thing, and neither notices that a
# control routed through the SAME untrusted step discriminates nothing, so a
# red one reads as "the system is broken" when it means "my instrument never
# ran". It costs 324 B because it reuses that bullet's frame and the
# `positive-control` anchor already tagged one clause earlier -- no new tag.
#
# 🔴 THE DUPLICATION CHECK THE ENTRY ABOVE ASKED FOR HAS NOW BEEN RUN TO
# COMPLETION, AND THE VEIN IS EXHAUSTED. That entry left it open ("this pass did
# not run it to completion, so the vein is not known to be exhausted"), so it
# was run first, and wider than specified: every core line >120 B was diffed by
# longest-common-block against the WHOLE archive (not merely the anchor it
# tags), `*Supports:` echo lines excluded, threshold lowered to 34 B. Sixteen
# blocks matched. Every one is imperative, scope or failure-shape text that the
# playbook says STAYS -- "a known-red slow test must be `-skip`ped BY NAME",
# "load inflates every test in the run, a failed assertion inflates exactly
# one", "a predicate open-coded at N sites is typically wrong at N-1 of them".
# ZERO blocks were evicted evidence left behind in the core. So the answer to
# "is there another 206 B in there" is measured NO, not assumed no; re-running
# this check before the next bump will not find one either unless new prose
# lands. What DID come out: 24 B, the incident count "Three walked in one PR."
# from the spelled-guards bullet, which its anchor already holds verbatim and in
# more detail. That is the whole remaining yield of a full-file sweep.
#
# So this bump is not "eviction was skipped". Eviction was measured, twice, by
# two different methods, and returned 24 B against a 324 B rule.
#
# +400 rather than the +300 that would just clear MIN_HEADROOM_BYTES: +300
# leaves single-digit-to-low-tens bytes above the floor. (The "34" this line
# carried was computed against a 300 B DRAFT of a rule that shipped at 324 B;
# at the committed size it is 10 B. The point holds at either value -- which is
# why the number should not have been here at all.) That is the exact mistake
# diagnosed by the UNDATED 35,200 -> 38,400 block above (32 B of slack -> "the
# next contributor got the surprise, not the warning"). ⚠ That block is
# 2026-08-15, `d8ffb466`; it carries no date header, which is what made this
# line misattribute it to the 2026-08-20 entry. +400 leaves 1,010 B
# CEILING-relative, i.e. one large rule.
#
# 2026-08-21 (later, #661): the zsh MULTIOS redirection trap cost 106 B and
# RULES.md reached 38,296. 🔴 MIND THE BASE -- the two slack figures in this
# module are measured from DIFFERENT points and subtracting one from the other
# is meaningless: "1,010 B" above is CEILING-relative (MAX_BYTES - size), while
# "4 B" here is FLOOR-relative (MAX_BYTES - size - MIN_HEADROOM_BYTES), and the
# bases differ by MIN_HEADROOM_BYTES. Floor-relative, #642 left 110 B and #661
# spent 106 of it, leaving 4. A draft of this PR's body did that subtraction
# and reported the 106 B rule as having cost 1,006 B.
#
# Recorded because it is the gate working exactly as designed: the next author
# got the WARNING at 4 B rather than the surprise at -1, which is the whole
# reason MIN_HEADROOM_BYTES exists. No bump was needed or taken for it.
#
# 2026-08-22: 39,200 -> 40,300 (+1,100, in TWO steps -- see the bump note
# below for why the second was forced). The rule that would not fit is the
# proactivity gate -- "Default to PROCEEDING -- and never ask for what you can
# measure yourself", a four-branch trigger tree in Push Back Before Acting.
# It is the THIRD-largest bullet in the file, behind "Validate the INSTRUMENT"
# and "Mutation-test a guard"; an earlier draft called it the largest, which
# the 2026-08-11 entry above already contradicted. Its byte size is NOT stated
# here -- see this entry's closing note on derived measurements for why. It is
# over the ~900 B the MIN_HEADROOM_BYTES comment calls a large rule, and that is
# deliberate: it is one decision tree with four branches, and splitting it
# into four bullets costs MORE because each would restate the frame ("stop
# only on a named trigger; each has its own response").
#
# 🔴 EVICTION WAS NOT SKIPPED -- IT WAS PAID BY CONSOLIDATION, WHICH IS WHAT
# THE 2026-08-11 ENTRY SAID THE ANSWER WOULD BE ("the answer is consolidation
# ... not another bump"). The tree SUBSUMES two existing bullets, which were
# deleted, not narrowed: "Flag BEFORE acting, not after" (its whole imperative
# survives verbatim in the outward-facing branch -- own your uncertainty, the
# concrete blast radius, "your call to proceed", mass rollouts and prod
# changes) and "User-facing micro-decisions" (its trigger list survives
# verbatim as the Fork branch's parenthetical, and "don't ship-then-rework"
# with it). The two deleted bullets are fixed history and can be stated: 295 B
# + 192 B = 487 B of text, 489 B counting their newlines. The NET cost is not
# stated, because it is a function of a bullet that kept changing -- and note
# the convention trap that produced two wrong numbers here already: a text-only
# bullet minus a newline-counted eviction corresponds to NOTHING. Subtract
# like from like -- or measure both ends directly, the way the docstring at
# the top of this module already does it:
# `git show <merge-base>:claude/RULES.md | wc -c`. NOT `git diff --stat`,
# which counts LINES for a text file and answers a different question.
#
# 🔴 A COLD READ CAUGHT ONE SCOPE LOSS; A BLIND AUDIT THEN CAUGHT THREE MORE.
# That ratio is the reusable part -- the cold read is necessary and was not
# sufficient, and the author who just wrote the replacement is the worst
# reader of it. What the cold read caught: the first draft's Fork branch read
# "two readings => materially different work", silently dropping the deleted
# bullet's "surface DISAGREEMENT, risk, or a SIMPLER PATH".
#
# What it MISSED, and an adversarial audit found by diffing clause-by-clause
# against `git show origin/main:claude/RULES.md`:
#   1. The RESPONSE imperatives moved. On main, "own your uncertainty, state
#      the concrete blast radius, end with 'your call to proceed'" and "get
#      direction" governed the WHOLE bullet -- both {disagreement, risk,
#      simpler path} and {high-blast-radius actions}. The draft left them only
#      on the outward-facing branch, so Fork kept the trigger and lost the
#      response. The words survived verbatim while the COVERAGE halved, which
#      is this file's own "a guard's DESCRIPTION claims COVERAGE" failure
#      wearing a different hat. Fixed by hoisting them to govern both branches
#      ("On BOTH of those: ..."), which also costs fewer bytes than stating
#      them twice.
#   2. The TRIGGER CATEGORY was swapped while its examples were kept. Main's
#      category was "high-blast-radius autonomous actions", with mass rollouts
#      and prod changes as EXAMPLES; the draft replaced the category with
#      "leaves this machine or cannot be undone" and kept the examples, so a
#      local, git-reversible, high-blast-radius action (a 200-file mechanical
#      refactor) matched main and matched NO branch of the tree. Note the
#      draft's own justification -- "a closed list is NARROWER than the shape
#      it samples" -- argues against what it did: keeping the samples while
#      replacing the shape is not scope-preserving. Both categories now ship.
#   3. The micro-decisions trigger CONDITION vanished. Main fired on options
#      "with several reasonable options"; only the parenthetical example list
#      survived, and it had re-attached grammatically to "a simpler path".
#      Restored, along with "present the choice" -- which is a different act
#      from "one question with a recommendation" (show the options vs advocate
#      one), so the shipped wording does both.
#
# Read a consolidation cold against every bullet it deletes, AND have someone
# who did not write it diff the clauses. The ceiling tests cannot see this
# class of loss -- a mutant that guts the rule to a 60 B stub while keeping
# its `-> archive:` tag passes all five of them.
#
# The duplication sweep the 2026-08-20 entry asked for and the 2026-08-21 entry
# ran to completion is NOT re-run here: it finished one day earlier, by two
# methods, over every core line >120 B against the whole archive, and returned
# 24 B. Re-running it a day later finds the same nothing.
#
# 🔴 THE OUTWARD-FACING BRANCH IS A SHAPE, NOT A LIST, AND THAT SAVED 61 B
# WHILE WIDENING THE RULE. The draft enumerated ten instances (push, PR, email,
# publish, deploy, delete, overwrite, force, prod, mass rollout). A closed list
# is NARROWER than the shape it samples -- this file's own opening rule is
# "read every rule at its WIDEST reading", and an enumeration invites "mine
# isn't on the list", which is precisely how the `git stash` ban failed. It
# ships as "anything that leaves this machine or cannot be undone, mass
# rollouts and prod changes included": the two instances that survive are the
# two the deleted bullet named, so no scope was lost.
#
# 🔴 THE BUMP WAS +900 AND HAD TO BECOME +1,100 WITHIN THE SAME PR, WHICH IS
# ITSELF THE FINDING. Slack figures in this entry are FLOOR-relative unless
# the sentence says otherwise -- and one below does, so read the label, not
# this blanket. +900 left 186 B. A blind adversarial audit then
# found three scope losses in the consolidation (below), and restoring them
# cost 130 B -- taking slack to 56 B (FLOOR-relative), i.e. the sub-one-bullet
# slack mistake the UNDATED 2026-08-15 block (`d8ffb466`) diagnosed at 32 B,
# reproduced by the very round that was
# fixing a different narrowing. So: a ceiling sized to leave less than one
# bullet is not merely tight, it cannot absorb its OWN review round. Size the
# margin to survive the audit, not to clear the floor.
#
# 🔴 THE REMAINING SLACK IS DELIBERATELY NOT WRITTEN DOWN, AND THAT IS THIS
# ENTRY'S LAST FINDING. Three consecutive rounds stated it and three
# consecutive rounds invalidated it -- 186 -> 56, then 256 -> 177 -- each time
# by the very edit that wrote the figure, in the module whose own docstring
# says a second hand-maintained copy of a number "is exactly how the drift
# regrows". A derived measurement does not belong in prose that is edited in
# the same commit as the thing it measures. `test_rules_md_keeps_working_
# headroom` already PRINTS current / ceiling / free / budget / RECLAIM on
# failure; that is the authority. Same for the sizing formula: it is
# size + 3,900, and two drafts carried a stale absolute forward rather than
# recomputing it.
#
# What IS durable: the slack after +1,100 is under one bullet FLOOR-relative
# (it is over one ceiling-relative -- the bases disagree about this very claim,
# which is why the sentence must name one), so the next real rule pays ceiling.
# When it does, the answer is the consolidation
# this entry demonstrated, applied to the Verification Honesty cluster -- the
# largest section in the file and the one with the most sibling bullets
# sharing a frame -- not another bump.
#
# 2026-08-23: 40,300 -> 40,900 (+600 B), and this entry owes an explanation on two
# counts, because the entry directly above says the next rule should be paid for by
# CONSOLIDATION, not another bump.
#
# The rule that would not fit is the "Out of scope" closing-condition clause in the
# proactivity-gate bullet: an agent may only file a task whose closing condition it
# can name, and must say why in its reply when it cannot. It was measured, not
# assumed: agent-filed issues survived 30 days at ~47% against ~5% for PRs, and the
# whole gap is objects nobody could ever close. The clause amends an existing bullet
# rather than adding a sibling -- the frame, the trigger and the "each branch has its
# OWN response" structure are all reused -- so the consolidation the entry above asks
# for was ALREADY applied here. It still costs 258 B, because the surviving text is
# all scope-bearing: the condition, who checks it, the T2 human-judgement case, and
# the say-why forcing function. Removing any one of them reintroduces a defect that
# the first shipped draft of this very clause actually had.
#
# 🔴 The sizing is the finding, and it is the one the 2026-08-22 entry above predicted.
# The first draft cleared the floor with 18 B to spare and was then found by audit to
# be NARROWER than its own design -- it admitted only machine-checkable conditions,
# forbidding the human-judgement tier the rule was written to permit. Fixing that cost
# ~170 B against 18 B of slack. That is the 2026-08-22 entry's "a ceiling sized to
# leave less than one bullet cannot absorb its OWN review round", reproduced within a
# day of being written. So this bump is sized to survive a review round rather than to
# clear the floor: +600 leaves the delta re-audit room to widen a clause without a
# second bump. If the re-audit does NOT spend it, ratchet this back down -- the entry
# above is right that down is the intended direction of travel.
MAX_BYTES = 40_900

# Required working margin below the ceiling.
#
# Sized in units of a REAL edit, the way test_skill_size.py's floor is: measured
# at the split, the mean SUBSTANTIVE bullet in RULES.md (a 🔴/🟡 rule with its
# scope clause and procedure) is ~500 B, and the largest are ~900 B. A floor
# below that would fire at the same moment as the ceiling and deliver the
# surprise it exists to prevent, so it is set to one full large rule.
MIN_HEADROOM_BYTES = 900


def _size() -> int:
    return len(RULES_MD.read_bytes())


def _archive_anchors() -> list[str]:
    """The `## <anchor>` headings that exist in the archive RIGHT NOW.

    Globbed off the file rather than hard-coded, for the same reason
    test_skill_size.py globs its reference topics: a hand-maintained list drifts,
    and a drifted list steers a maintainer into duplicating an entry that
    already has a home.
    """
    return [
        ln[3:].strip()
        for ln in ARCHIVE_MD.read_text(encoding="utf-8").splitlines()
        if ln.startswith("## ") and not ln.startswith("## Table")
    ]


def _eviction_playbook() -> str:
    anchors = _archive_anchors()
    listed = ", ".join(anchors) if anchors else "(none found -- did the archive move?)"
    return f"""
  How to fix -- do NOT delete or narrow a rule to make this pass:

    🔴 A rule must not lose SCOPE. RULES.md's own opening rule is "read every
       rule at its WIDEST reading", and the `git stash` ban had to be
       re-broadened after a narrow wording let a subagent walk straight into
       the failure it forbade. Trimming "for ANY reason", "not just X",
       "this is a CLASS, not one manifestation" or a rule's triggers to buy
       bytes REINTRODUCES that failure mode. Don't.

    What to move OUT (to claude/RULES-ARCHIVE.md):
      - worked incident narratives: dates, hostnames, store paths, byte counts,
        PR numbers, the blow-by-blow of how it was diagnosed
      - superseded or retracted theories
      - enumerated ground cases where one representative case carries the point

    What STAYS in claude/RULES.md:
      - the imperative, its priority marker (🔴/🟡/🟢) and its triggers
      - the widest scope of the hazard
      - enough of the failure SHAPE that a reader knows when the rule applies
      - any command/procedure needed to actually comply

    Mechanics:
      1. Add a `## <anchor>` section to claude/RULES-ARCHIVE.md holding the
         evidence, with an italic `*Supports: <the rule>.*` line under it.
         (existing anchors: {listed})
      2. Leave a `→ archive: <anchor>` tag on the rule in claude/RULES.md.
      3. Re-run this test. Ceiling + headroom constants live in
         scripts/tests/test_rules_size.py.

    Remember RULES.md is paid TWICE -- once per Claude Code session, and again
    inside the generated ~/.config/opencode/AGENTS.md.
"""


def test_rules_md_exists():
    """Guard the guard: a moved/renamed RULES.md must not silently pass."""
    assert RULES_MD.is_file(), (
        f"{RULES_MD} not found -- the size gate below would be vacuous. "
        "If RULES.md moved, update RULES_MD in this module."
    )


def test_archive_exists_and_has_anchors():
    """Guard the guard, part 2.

    The playbook only renders on FAILURE, so a wrong/empty anchor list is
    invisible until the day someone needs it. Exercise the derivation directly,
    and fail loudly if the archive was deleted -- without it the only way to
    satisfy the ceiling is to delete rules, which is the outcome this whole
    module exists to prevent.
    """
    assert ARCHIVE_MD.is_file(), (
        f"{ARCHIVE_MD} not found. RULES.md's eviction target is gone, so the "
        "only remaining way to satisfy MAX_BYTES is to delete or narrow rules. "
        "Restore the archive."
    )
    anchors = _archive_anchors()
    assert anchors, (
        f"{ARCHIVE_MD} has no `## <anchor>` sections -- the eviction playbook "
        "would name no destinations."
    )
    playbook = _eviction_playbook()
    missing = [a for a in anchors if a not in playbook]
    assert not missing, (
        f"the eviction playbook omits existing archive anchors {missing}; a "
        "maintainer following it would create a duplicate entry. The list must "
        "stay derived from the file, never hard-coded."
    )


def test_every_archive_pointer_resolves():
    """A `→ archive: <anchor>` tag in the core must name a real archive section.

    A dangling pointer is worse than no pointer: it tells a reader the evidence
    exists and then wastes the lookup. This also catches an archive section
    renamed without updating the core.
    """
    import re

    anchors = set(_archive_anchors())
    referenced = set(
        re.findall(r"→ archive: ([a-z0-9-]+)", RULES_MD.read_text(encoding="utf-8"))
    )
    dangling = sorted(referenced - anchors)
    assert not dangling, (
        f"claude/RULES.md points at archive anchors that do not exist: "
        f"{dangling}.\nArchive has: {sorted(anchors)}"
    )

    # 🔴 THE OTHER DIRECTION, WHICH WAS ASSERTED IN PROSE AND GATED NOWHERE.
    # An audit found this module claiming anchors "resolve both directions"
    # while only `referenced - anchors` was ever computed. The inverse — an
    # archive section nothing points at — is the same rot one step over, and
    # this module's own `_archive_anchors` docstring already argues the case:
    # "a hand-maintained list drifts, and a drifted list steers a maintainer
    # into duplicating an entry that already has a home." An orphan is exactly
    # how that happens: the evidence is there, nothing routes to it, and the
    # next person writes it again in the core where bytes are scarce.
    #
    # ⚠ THIS IS AN INVARIANT GUARD, NOT REGRESSION COVERAGE — it was green the
    # moment it was written (0 orphans measured), so it never caught the bug it
    # describes. It is labelled as such rather than counted as a catch. Watched
    # to fail by injecting an orphan section; it goes red with its own message.
    #
    # `retired-*` is excluded BY PREFIX and deliberately: a retired rule's
    # evidence is meant to outlive the pointer that used to reach it. Any other
    # unreferenced section is drift.
    orphans = sorted(
        a for a in (anchors - referenced) if not a.startswith("retired-")
    )
    assert not orphans, (
        f"\n\nclaude/RULES-ARCHIVE.md has section(s) nothing points at: "
        f"{orphans}.\n"
        f"  Either add a `→ archive: <anchor>` tag on the rule whose evidence "
        f"this is,\n"
        f"  or rename it `retired-<name>` if the rule itself is gone.\n"
        f"  An orphan is not harmless: the next author cannot find it, so they "
        f"write it\n"
        f"  again in the core, where bytes are the scarce thing."
    )


def test_rules_md_under_hard_ceiling():
    size = _size()
    assert size <= MAX_BYTES, (
        f"\n\nclaude/RULES.md is OVER its hard ceiling.\n"
        f"  current:  {size:,} bytes\n"
        f"  ceiling:  {MAX_BYTES:,} bytes\n"
        f"  OVER BY:  {size - MAX_BYTES:,} bytes\n"
        f"{_eviction_playbook()}"
    )


def test_rules_md_keeps_working_headroom():
    """The ceiling alone is not enough -- keep a margin to edit into.

    Fails in the MAX_BYTES-MIN_HEADROOM .. MAX_BYTES band (and above, where the
    ceiling test also fires with the overage), so "you are one rule from
    breaking it" arrives as a signal rather than as a surprise. The margin is
    sized to one full large rule; see the comment on MIN_HEADROOM_BYTES.
    """
    size = _size()
    headroom = MAX_BYTES - size
    assert headroom >= MIN_HEADROOM_BYTES, (
        f"\n\nclaude/RULES.md has no working headroom left.\n"
        f"  current:   {size:,} bytes\n"
        f"  ceiling:   {MAX_BYTES:,} bytes\n"
        f"  free:      {headroom:,} bytes  (minimum required: "
        f"{MIN_HEADROOM_BYTES:,})\n"
        f"  budget:    {MAX_BYTES - MIN_HEADROOM_BYTES:,} bytes "
        f"(ceiling minus the required margin)\n"
        f"  RECLAIM:   {MIN_HEADROOM_BYTES - headroom:,} bytes\n"
        f"{_eviction_playbook()}"
    )
