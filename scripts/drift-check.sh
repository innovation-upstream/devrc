#!/usr/bin/env bash
# drift-check — PASSIVE deadman for the devrc two-host fleet.
#
# Answers one question, unattended, on a timer: "is either host silently no
# longer receiving changes?" It REPORTS. It never fixes.
#
# 🔴 THAT QUESTION HAS TWO HALVES, and for a long time this file only asked the
# first. GIT PARITY (is the checkout still tracking origin/main?) and HOST PARITY
# (is what the checkout describes actually DEPLOYED and the same on both
# machines?) are independent. Every skill on the laptop was a dangling symlink
# into a garbage-collected /nix/store path while `git log` matched origin/main
# exactly — perfect git parity, zero host parity, and this script said clean.
# See "the per-host HOST-PARITY routine" below and exit codes 14 and 15.
#
# ── WHY THIS EXISTS ───────────────────────────────────────────────────────────
# `scripts/ship.sh` converges both hosts and is correct: a host it cannot
# fast-forward is SKIPPED and left exactly as found (rc=8 skipped:diverged). But
# NOTHING RUNS SHIP ON A SCHEDULE. So a host that starts getting skipped stops
# receiving every subsequent change while continuing to look completely healthy —
# same commits in `git log`, same green `home-manager` generation, no error
# anywhere. The only detector was "a human happens to ship something and reads
# the per-host lines".
#
# That has now happened twice:
#   2026-08-06  two un-pushed commits on the workbench blocked it for hours; the
#               regrowth timer would have fired on 08-11 running the very bug the
#               undelivered commit fixed.
#   2026-08-09  THREE un-pushed commits on the workbench (rescued as #366), found
#               only because someone shipped something unrelated. Alongside them,
#               7 untracked files — including a handoff doc for the stranded work.
#
# ── PASSIVE MEANS PASSIVE ─────────────────────────────────────────────────────
# 🔴 This script must NEVER mutate either host's CHECKOUT. It may `git fetch`
# (which writes remote-tracking refs, never the working tree, the index, or any
# local branch — though note `fetch` does trigger git's own `gc --auto`, so
# "this script never causes a gc" would be a claim it cannot make). It must
# NEVER checkout, switch, merge, fast-forward, rebase, reset, stash, clean,
# commit, or run `home-manager`. A deadman that repairs is a deployer with no
# supervision; a deadman that reports is a deadman.
#
# `scripts/tests/test_drift_check.py` enforces that STATICALLY, and the scanner
# is an ALLOWLIST, not a keyword blocklist: a `git` invocation it can RESOLVE
# STATICALLY must name one of a fixed set of read-only subcommands. The check is
# anchored at each COMMAND SEPARATOR (`;`, `&&`, `||`, `|`, `$(`), not at the
# start of the line — so `say "hi" && git checkout main` is caught — and it
# recurses through wrappers (`timeout`, `flock`, `stdbuf`, `ionice`, `nice`) and
# through `ssh <target> …` / `bash -c` / `sh -c`, which each hide a command line
# inside their arguments. `ssh <target> git checkout …` matters most: it mutates
# THE OTHER HOST, this script's primary hazard, and `ssh` is stubbed out of every
# behavioural test, so it was invisible to both layers until #371.
#
# 🔴 WHAT THE SCANNER CANNOT SEE — an accurate claim beats a reassuring one:
#   * a command word produced by EXPANSION (`$g checkout main`, `eval "$cmd"`).
#     The scanner flags the alias where it can see one being made (`g=git`), but
#     an alias built from parts, or read from a file, resolves to nothing static.
#   * 🔴 A NON-GIT MUTATION SURFACE. The allowlist governs `git` verbs; the
#     branch-protection arm runs `gh`, which can DELETE the very protection it
#     reads — `devrc/CLAUDE.md` publishes that one-liner verbatim as an escape
#     hatch, so it is a plausible thing for a maintainer to paste here. Covered
#     by its own guard (`test_the_gh_calls_are_read_only`), NOT by the scanner.
#   * anything inside a string that only becomes code on the FAR SIDE of the ssh
#     hop. The $CHECK payload is scanned because it is literal text in this file;
#     a payload assembled at runtime would not be. `require_int` plus `%q` on the
#     two interpolated values is what covers that, not the scanner.
# The BEHAVIOURAL layer (`test_run_against_diverged_repo_mutates_nothing`) closes
# the local-checkout half of both holes, and closes it for shapes nobody has
# enumerated. Neither layer covers a runtime-built mutation of the REMOTE host.
#
# THE ONLY FILES THIS SCRIPT WRITES are its streak counters under
# $DRIFT_STATE_DIR (default $XDG_STATE_HOME/drift-check): the consecutive-
# unreachable counter per remote role, and the consecutive-UNMEASURED counter per
# (host, built-source scope) that rc 18 rides on. They live outside every repo,
# and `test_the_only_files_the_deadman_writes_are_the_streak_counters` holds that
# ledger — as an asserted set of REDIRECTION TARGETS, so it pins that nothing
# else is written, not how many counter files exist.
#
# ── HOST IDENTITY ─────────────────────────────────────────────────────────────
# Both machines report hostname `nixos`, so identity comes from local IPv4
# addresses. That predicate is NOT reimplemented here — it is sourced from
# scripts/lib/host-role.sh, the same file ship.sh sources. A second copy would
# drift and be wrong, which is exactly how ship.sh's host detection was broken
# before (it hardcoded "local == workbench" and SSH'd to itself).
#
# ── EXIT CODES ────────────────────────────────────────────────────────────────
# Deliberately aligned with ship.sh so the two read consistently: a code means
# the same thing in both, and codes ship.sh owns for actions this script does not
# take (5 conflicted-tree, 7 cannot-ff, 9 switch-failed, 11 verify-failed) are
# left UNUSED rather than repurposed.
#
# 🔴 THE RESERVATION IS RECIPROCAL, AND THE UPWARD DIRECTION IS NOT FREE.
# ship.sh owns 19 (the two hosts landed on DIFFERENT commits), 20 (ship.sh was
# replaced by its own run and the new copy could not be run) and 21 (usage error:
# an unknown argument, or a run asked to check NO host at all). All three are
# ABOVE this script's current ceiling of 18, which is exactly where the note
# further down ("a new DRIFT code has nowhere to go but upward") points the next
# one. So:
#   * 19, 20 and 21 are RESERVED to ship.sh here and must not be taken as DRIFT
#     codes. 22 is now TAKEN by this script (skillOverrides disagree with the
#     tier ledger), 23 by the nix-read untracked ladder and 24 by the
#     branch-protection arm, so the next free code for this script is 25.
#   * anything this script adds above 21 is reserved back the other way; ship.sh
#     documents this in its own header for the same reason.
#
# RESERVED-TO-SHIP: 5 7 9 11 19 20 21
#
# That line is a LEDGER, machine-read, not a comment: it must equal exactly the
# set of codes ship.sh can return and this script cannot, so it fails when the
# set grows (ship.sh gains a code) or shrinks.
# The two ladders are pinned to each other by
# test_the_two_rc_ladders_reserve_each_others_codes in
# scripts/tests/test_drift_check.py, because the alignment lived only in a PR
# description until 2026-08-21 and neither file mentioned the other's codes.
#
#   2   usage error (unknown flag, non-integer tunable), a RUN THAT CHECKED
#       NO HOST AT ALL — either because the flags asked for none (`--no-local
#       --no-remote`) or because the only host it was asked to look at could not
#       be reached (rc 0 from a run that observed nothing is the vacuous green
#       this whole subsystem exists to prevent, so it is never emitted) — or
#       DRIFT_REPO SET BUT EMPTY, which is a caller bug: `${VAR:-default}`
#       cannot tell unset from empty, so an empty value would silently resolve
#       to $HOME/workspace/devrc and fetch into the operator's own clone. UNSET
#       still defaults, deliberately: the remote leg does not forward it.
#   3   repo missing on that host
#   4   git fetch failed, or origin/main is missing / HEAD unborn
#   6   local host could not be identified (see detect_role)
#   8   DRIFT — local `main` has DIVERGED or is AHEAD of origin/main (un-pushed
#       commits). 🔴 THE DANGEROUS ONE — ship.sh will skip this host forever.
#   10  DRIFT — local `main` is BEHIND origin/main (just needs a ship)
#   12  DRIFT — the checkout is not ON branch `main`
#   13  remote host unreachable for $DRIFT_UNREACHABLE_ESCALATE CONSECUTIVE runs
#   14  DRIFT — a host has MANAGED SYMLINKS THAT RESOLVE TO NOTHING. 🔴 The one
#       git is structurally blind to: every skill on the laptop dangled into a
#       garbage-collected /nix/store path while the checkout was byte-identical
#       to origin/main. Git parity is not host parity.
#   15  DRIFT — HOST PARITY: the two hosts' settings.json top-level KEY SETS or
#       their enabledPlugins differ, or a host has a plugin enabled that is not
#       installed there. A short, EXPLICITLY ENUMERATED set of settings.json keys
#       is exempt from the key-set half — see "PER-HOST settings.json KEYS"
#       below. Nothing else is, and an unknown key is never exempt.
#   17  DRIFT — the SOURCE SUBTREE a devrc package is BUILT FROM is not current
#       on that host: behind or ahead of its branch's own upstream, counted with
#       a PATHSPEC limited to that package's `srcDir`. 🔴 The SECOND thing git
#       parity on devrc structurally cannot see — and 🔴 NOT the same thing as
#       "the repo is behind", which is reported but never escalates. See
#       "SOURCE-REPO PARITY" below. The number is high because 5/7/9/11 are
#       reserved to ship.sh meanings this script does not take; its SEVERITY is
#       set by the table, not by the digit, and it ranks between 8 and 14 — see
#       severity() for why.
#   18  DRIFT — a BUILT-SOURCE SCOPE has been UNMEASURABLE for N CONSECUTIVE runs
#       on that host, so its currency has never been evaluated and rc 17 cannot
#       fire for it. 🔴 The gap rc 17 left: "we could not look" correctly set no
#       code, and therefore escalated NEVER. See "UNMEASURED IS NOT FOREVER"
#       below. Same ladder as rc 13, and it ranks just under it — see severity().
#   22  DRIFT — a host's DEPLOYED `skillOverrides` disagree with devrc's skill-
#       listing tier ledger (`claude/skill-tiers.json`), so the always-on skill
#       listing on that host is not the one this repo describes. 🔴 A host with
#       NO overrides at all is NOT ADOPTED and sets NO code — that is the state
#       the mechanism shipped in, and calling it drift would have made this arm
#       permanently red. rc 22 is adopted-then-drifted only. See "SKILL-LISTING
#       TIERS" below. It ranks just under rc 10, because a BEHIND host carries a
#       stale ledger and can produce this finding as a symptom.
#   23  DRIFT — an UNTRACKED file in a path NIX READS has survived N CONSECUTIVE
#       runs on that host. Untracked files have always been reported here as
#       information; this is the subset where the file is not merely unbacked but
#       DEPLOYED — either live through a mkOutOfStoreSymlink or copied into the
#       artifact by the next switch. Measured 2026-08-25: one such file had sat
#       on the workbench for ~3 weeks with every check green. See "UNTRACKED IN A
#       NIX-READ PATH" below. It ranks between rc 15 and rc 12 — see severity().
#   24  DRIFT — `main` ON THE CANONICAL REMOTE HAS NO REQUIRED STATUS CHECKS, so
#       the gate every change to this fleet passes through is OFF. 🔴 The FOURTH
#       kind of parity, and the first three are all blind to it: they ask whether
#       the two hosts match `origin/main`, and this asks whether `origin/main` is
#       still a thing worth matching. Both hosts can be byte-identical, every
#       symlink resolve, every built source be current — and `main` be a branch
#       anyone can push anything to. See "BRANCH PROTECTION" below. It ranks
#       between rc 8 and rc 17 — see severity().
#   16  ACTIONABLE, not drift — the fuzzyclaw PHASE-2 GATE has OPENED: zero rows
#       still take their `age_secs` from fuzzyclaw alone, so the readers can be
#       removed. See "THE FUZZYCLAW PHASE-2 GATE" below. It is the LEAST severe
#       code this file owns, so it can only ever be the verdict when nothing
#       else is wrong, and the final line says ACTIONABLE rather than DRIFT.
#
# ── SOURCE-REPO PARITY (rc 17) ────────────────────────────────────────────────
# 🔴 A THIRD KIND OF PARITY, AND THE FIRST TWO ARE BLIND TO IT. devrc builds some
# packages from a LOCAL WORKING TREE OF ANOTHER REPO — `nix/pkgs/**` derivations
# whose `src` is `${workspace}/<repo>/…`. The devrc checkout can be byte-identical
# to origin/main, every managed symlink can resolve, and the binary that gets
# installed is still built from a source tree that is months old, because NOTHING
# CONVERGES THOSE REPOS: ship.sh is scoped to $HOME/workspace/devrc and this file
# used to have no idea they existed.
#
# MEASURED 2026-08-14, on clawgatectl:
#   17:44  homelab-infra #323 added `task status`/`task comment` and set the Go
#          source's own default buildVersion to "0.7.95".
#   17:48  devrc #483 bumped clawgatectl.nix's hand-written version literal
#          0.7.87 -> 0.7.95 and shipped to BOTH hosts.
# The workbench's ~/workspace/homelab-talos was current, so it got a real 0.7.95.
# The laptop's was frozen 24 commits back; its client.go correctly said "0.7.87"
# and the nix ldflag OVERWROTE that with "0.7.95". The laptop then carried a
# binary with neither subcommand, wearing the label of one that had both:
# `clawgatectl task status <id> in_progress` printed help and exited 0 — a silent
# no-op against a CLI whose write ritual the `clawgate` skill makes mandatory.
# This deadman was FULLY GREEN on that laptop throughout. The version half is
# fixed in nix/pkgs/tools/clawgatectl.nix (the version is now read out of the
# compiled source); THIS is the other half — noticing the stale tree at all.
#
# 🔴 THE UNIT IS THE srcDir SUBTREE, NOT THE REPO — and getting that wrong makes
# this a PERMANENTLY-RED GATE, which is worse than no gate because it trains
# click-through on the one alert that has to keep its meaning. MEASURED
# 2026-08-18 on the workbench: it was 1 commit behind `origin/trunk`, and that
# commit was `2ce7cbdc fix(naida-ai-demo): raise memory limit 128Mi -> 512Mi` —
# `git diff --name-only HEAD..origin/trunk -- containers/clawgate` is EMPTY, so it
# cannot reach the built binary. Over the preceding 14 days that repo took 98
# commits of which only 32 touched `containers/clawgate`; at ~7 commits/day the
# host is behind almost continuously and roughly two thirds of those reds could
# not affect any package devrc builds. So the verdict is computed with a PATHSPEC
# limited to each package's own srcDir (`HEAD..@{u} -- <subtree>`, and the reverse
# for ahead), and the repo-wide numbers are printed beside it as INFORMATION.
#
# 🔴 THE COVERED SET IS DERIVED, NEVER LISTED. The payload reads `nix/pkgs/**.nix`
# out of the checkout it is examining and collects every `${workspace}/<path>` it
# finds outside a comment — the WHOLE path, so the repo and the subtree both fall
# out of the same scan — and one repo may yield SEVERAL scopes. A THIRD such
# package is covered the day it is added; a hardcoded pair would have been
# correct on the day it was written and silently incomplete afterwards, which is
# the same shape as the bug. The derivation is pinned TWO-WAY by
# `test_drift_check.py::test_the_source_repo_set_is_pinned_two_way_against_nix_
# pkgs` — it fails when the set GROWS or SHRINKS.
#
# 🔴 EXAMINED BESIDE STALE, again. `examined=N stale=M unmeasured=K` is the
# claim; none of the three numbers means anything alone, and the unit counted is
# the BUILT-SOURCE SCOPE (repo count printed beside it). A scope whose repo is
# absent, whose fetch failed, whose branch has no upstream, or whose pathspec
# count will not parse is UNMEASURED — never folded into "0 stale", because a
# checker wired to nothing reports exactly that zero.
#
# WHAT IS DRIFT HERE AND WHAT IS NOT — the line is "did we MEASURE a divergence
# IN THE CODE THAT GETS COMPILED":
#   * the package's own srcDir SUBTREE is behind / ahead of the branch's own
#     upstream ..................................................... rc 17
#   * the REPO is behind / ahead but the subtree is not .... reported, NOT drift.
#   * repo ABSENT on this host ......... reported, NOT drift, EVER. The
#     derivations guard on pathExists and simply omit the binary; a host without
#     the checkout is a documented, tolerated state (see clawgatectl.nix's
#     header), so it never escalates at any count.
#   * `git fetch` FAILED ............... reported, NOT drift ON ANY ONE RUN.
#     These repos are private and reached over ssh, and a systemd --user unit has
#     no ssh-agent. "We could not look" must never read as either a pass or a
#     divergence — but a fetch that keeps failing means the currency is never
#     evaluated at all, so it escalates to rc 18 after a LONGER run of
#     consecutive misses than the structural reasons get. See "UNMEASURED IS NOT
#     FOREVER" below.
#   * DETACHED HEAD / branch with no upstream ... reported, NOT drift on any one
#     run. There is no defined answer to compare against; inventing one (assume
#     `main`) would be a guess presented as a measurement. It is STRUCTURAL
#     though — it never heals on its own — so it escalates to rc 18 after
#     DRIFT_UNMEASURED_ESCALATE consecutive runs. This is the shape that was
#     measured concealing a real divergence.
#   * DIRTY TREE ....................... reported, NOT drift, and reported even
#     when the repo is otherwise current: these derivations build the TREE, not
#     the commit, so an uncommitted or untracked file IS in the binary. The
#     workbench's homelab-talos is routinely dirty; making that fail the unit
#     would be a permanently-red gate.
#
# 🔴 THE CROSS-HOST HALF IS INFORMATION ONLY, deliberately. The driver diffs the
# two hosts' `FACT src-repos` lines — 🔴 whose values are the srcDir SUBTREE's
# TREE OID, not the repo HEAD, for the same reason the verdict is scoped: a repo
# HEAD differs whenever the two hosts disagree about ANY commit, including cluster
# manifests no package is built from. It reports scopes whose built tree differs —
# the most direct statement of "these two machines compile different code" — but
# sets NO rc, because whether a given tree is WRONG is already answered per host
# by the upstream comparison, which has a defined correct answer. Two hosts
# sitting on different branches of a shared development repo is normal, and a code
# that fires on it would be a permanently-red gate. Like every cross-host claim
# here it prints NOT COMPARED unless facts arrived from BOTH machines.
#
# READ-ONLY, like everything else in this file: `fetch`, `rev-parse`, `rev-list`,
# `symbolic-ref`, `status`. It never pulls, never switches, never repairs.
#
# ── UNTRACKED IN A NIX-READ PATH (rc 23) ──────────────────────────────────────
# 🔴 THE GAP THE UNTRACKED BLOCK LEFT. Untracked files have always been counted
# and listed here, and have never escalated — correctly, because most of them are
# scratch. But a subset of them is not scratch: it sits in a path NIX READS, and
# is either being served right now or is one `git add` from the next artifact,
# with no commit and no backup anywhere.
#
# MEASURED 2026-08-25 on the workbench:
# nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02 had sat untracked for
# ~3 weeks, reported every run, escalated never. That one turns out NOT to be
# nix-read (nix/system/ holds hand-run sudo scripts the flake never opens) — but
# the same run listed scripts/dl-router/tests/load_test_store.sh, which IS:
# nix/home.nix says `${../scripts/dl-router}` and copies that directory into the
# store WHOLE. Nothing distinguished the two, and no operator was going to.
#
# WHICH PATHS NIX READS IS DERIVED, NEVER LISTED — scripts/lib/nix_read_paths.sh
# reads it out of nix/ at scan time and returns two classes, because the
# consequences differ:
#   LIVE     a mkOutOfStoreSymlink target. The deployed path is a link back into
#            the working tree, resolved at USE time and never through the flake
#            source, so an untracked file there IS being served right now.
#   DROPPED  a nix path literal — nix READS that path, but the flake source is
#            filtered to the files git knows about, so an UNTRACKED file there
#            reached NOTHING.
# 🔴 DROPPED IS NOT "STORE", and calling it that was this block's first bug. The
# class says nix reads the PATH; only nix_read_artifact_reach turns that into a
# claim about the FILE, and every path here is untracked by construction.
# MEASURED 2026-08-25: all six `-dl-router` store generations carry tests/ (37
# files) and NONE carries the untracked tests/load_test_store.sh; a controlled
# four-state build confirms committed / modified / `git add`ed all land (with the
# WORKING-TREE content) and untracked never does.
# Both still escalate — DROPPED is unsaved work in no commit and no backup,
# sitting in a tree nix copies, one `git add` from the artifact — but the reason
# printed for each is now one that is true of it.
#
# THE LADDER IS rc 13's, for rc 18's reason: reported on EVERY run, escalated to
# rc 23 only after N CONSECUTIVE runs (DRIFT_NIXDIRT_ESCALATE, default 12 ≈ 3
# days at the 6h cadence), per (HOST, PATH), and RESET the moment that path stops
# being untracked — committed, deleted or gitignored. 3 days rather than rc 18's
# 24h because creating a file and committing it an hour later is the NORMAL
# working shape here; the finding is "this has been sitting there", not "this
# exists".
#
# 🔴 IT REFUSES TO BE SATISFIED BY MEASURING NOTHING, and that guard is the whole
# reason the derived set reports its own population. A host whose payload derived
# ZERO nix-read paths prints COULD NOT MEASURE with a reason token and sets NO
# code — because an empty nix-read set classifies every untracked file on every
# host as clean, forever, in silence. It also bumps and resets NOTHING in that
# state: a ladder must not be cleared by a scan that walked nothing.
#
# ── UNMEASURED IS NOT FOREVER (rc 18) ─────────────────────────────────────────
# 🔴 THE GAP rc 17 LEFT. A scope that CANNOT be evaluated is reported UNMEASURED
# and sets no code — deliberately, because "we could not look" must read as
# neither a pass nor a divergence. But nothing ever escalated it, so a scope
# could stay unevaluated FOREVER while the run kept reading as clean. That is the
# same shape as the bug rc 17 was built to catch: a green that means "did not
# look", not "looked and found nothing".
#
# MEASURED 2026-08-18 on the workbench: ~/workspace/tmux-fuzzyclaw sat on a local
# branch `docs/tui-rendering-footguns` with NO UPSTREAM. The run reported
# `unmeasured=1` and exited 0 — while CONCEALING a genuinely divergent build
# between the two hosts, which is precisely what rc 17 exists to say out loud.
#
# So an unmeasured scope now carries the SAME ladder an unreachable remote does
# (rc 13): reported on EVERY run, escalated to rc 18 only after N CONSECUTIVE
# runs, with the streak persisted under $DRIFT_STATE_DIR and RESET the moment
# that scope measures. Per (HOST, SCOPE), never per run: one repo recovering must
# not clear another's ladder, and the laptop's blindness is not the workbench's.
#
# 🔴 THE REASONS ARE NOT ONE HAZARD, so they do not share one counter:
#   * NOUPSTREAM (a branch with no upstream, or a detached HEAD) is STRUCTURAL.
#     It never heals on its own, and it is the one measured concealing a real
#     divergence. DRIFT_UNMEASURED_ESCALATE, default 4 ≈ 24h at the 6h cadence —
#     the same patience an unreachable laptop gets. Deliberately not instant:
#     parking on a scratch branch for an afternoon is normal, and a gate that
#     fired on that would be red most of the time.
#   * NOCOUNT (a rev-list over that pathspec that will not parse) is structural
#     too — the identical command fails identically next run — so it takes the
#     same threshold.
#   * FETCHFAILED is the one with a plausibly TRANSIENT cause: no ssh-agent in a
#     user unit, a key rotation, a network outage, a remote that is down. It gets
#     its OWN, longer ladder (DRIFT_UNMEASURED_FETCH_ESCALATE, default 12 ≈ 3
#     days) rather than being folded into the counter above. It still escalates:
#     a fetch failing for three days is not weather, and a currency check that
#     can never fetch is a checker wired to nothing.
#   * ABSENT NEVER escalates, at any count. A host that simply lacks the checkout
#     is a documented, tolerated state — nix/pkgs/tools/clawgatectl.nix guards on
#     pathExists and omits the binary — so escalating would make a host
#     permanently red for a package it correctly does not ship. It is still
#     reported every run; the counter is not even consulted.
#   * an UNKNOWN reason token takes the STRUCTURAL threshold. Like the
#     settings.json allowlist below, this enumeration FAILS CLOSED: a reason
#     nobody has argued about does not get an exemption by default.
#
# 🔴 AND IT CANNOT BE SATISFIED BY MEASURING NOTHING. A ladder is only as good as
# the set it walks, so the block prints `hosts-reporting= scopes= unmeasured=
# escalated=` and refuses to print that summary at all when the HOST count or the
# SCOPE count is zero: a ladder over no scopes is not "nothing is stuck". A host
# is walked only when it returned a `FACT src-unmeasured examined=N …` line of
# its own, so an unreachable laptop bumps nothing — rc 13 already owns that
# finding, and a host nobody looked at must not accumulate a streak.
#
# The state file is per (host, scope): $DRIFT_STATE_DIR/unmeasured-<role>-<scope>
# with `/` escaped to `_` and a literal `_` doubled — injective over the whole
# character set the scope scan can produce ([A-Za-z0-9._/-]), so two different
# scopes can never share a counter. It holds `<reason> <count>`, and a CHANGED
# reason restarts the count: the thresholds differ, so carrying a FETCHFAILED
# streak into a NOUPSTREAM ladder would escalate on evidence that was never about
# that hazard.
#
# KNOWN AND ACCEPTED BOUND, in the style of the two under the unreachable streak
# below: nothing prunes these files (that would need a delete, and this script
# does not delete), so a scope REMOVED from nix/pkgs and later RE-ADDED resumes
# its old count instead of starting over. It errs toward escalating SOONER — but
# only for a scope that was unevaluable before it left and is unevaluable again
# for the SAME reason, which is a state worth saying out loud at whatever count.
#
# ── THE FUZZYCLAW PHASE-2 GATE (rc 16) ────────────────────────────────────────
# "Is it safe to delete the fuzzyclaw readers yet?" was answered by somebody
# remembering to run a probe, which is the same failure mode as "nothing runs
# ship.sh on a schedule" that this whole file exists to fix. So it is a
# measurement: `session-manager scan --json` reports `age_source` per row, and
# `summary.age_sources.fuzzyclaw` counts the rows whose age NO OTHER WRITER
# supplied. Those are pre-deploy sessions the agent ledger has no record of, and
# the count decays as they restart. At 0, phase 2 is unblocked.
#
# 🔴 LOCAL HOST ONLY, and that is not a shortcut. fuzzyclaw task files are LOCAL
# state — `gather()` passes its task index only for `host == local_host` — so a
# remote row structurally CANNOT carry a fuzzyclaw age. Scanning one host gives
# the identical numerator with no ssh. Measured 2026-08-15: 7 of 47 rows locally,
# and the same 7 in a two-host scan of 75.
#
# 🔴 IT OBEYS THE EXAMINED-BESIDE-DANGLING RULE, one subsystem over. The count is
# NEVER printed alone: `N of M row(s) EXAMINED` is the claim, and a 0 over M=0 is
# reported as COULD NOT MEASURE, never as ready. Every way this can fail to
# measure — no session-manager, a crash, unparseable output, a scan of the wrong
# host, fuzzyclaw not actually read, the age histogram ABSENT or in a writer
# vocabulary this gate does not recognise — is its own reason token from
# lib/drift_phase2.py and lands in the same COULD-NOT-MEASURE branch. None of
# them sets rc 16, and none of them is a zero.
#
# 🔴 THAT LAST SENTENCE WAS FALSE ON ARRIVAL, which is why the ledger below
# exists. `summary.age_sources` shipped with no presence or type check, so a
# report without it — including one from a session-manager older than the field,
# i.e. exactly the stale host this deadman is FOR — printed `READY — 0 of 47`
# byte-identical to a real one. The claim is now MACHINE-CHECKED rather than
# restated: `test_drift_check.py::test_the_phase2_reason_token_ledger_is_pinned_
# to_the_fields_read` ast-extracts the emitted token set AND the set of report
# fields the reader consults, and fails when either grows or shrinks — so a
# newly-read field with no reason token of its own is a red test.
#
# It is NON-FATAL to the rest of the run by construction: it is the last block
# before the summary, it only ever raises rc from 0 to 16, and a failure to
# measure raises nothing at all. 🔴 rc 16 is a SUCCESS to systemd
# (`SuccessExitStatus = 16` on the unit in nix/home.nix): it stays set until
# somebody does the cleanup, so failing the unit on it would fire the
# DND-defeating failure toast 4× a day forever — the same permanently-red-gate
# refusal this file already makes for an unreachable remote, below.
#
# ── BRANCH PROTECTION ON THE CANONICAL REMOTE (rc 24) ─────────────────────────
# 🔴 A FOURTH KIND OF PARITY, AND THE OTHER THREE CANNOT SEE IT. Git parity asks
# "is this checkout still tracking origin/main?"; host parity asks "is what it
# describes actually deployed?"; source parity asks "is the code we COMPILE
# current?". All three take `origin/main` as the reference and ask who has
# diverged from it. This one asks the question underneath them: is `origin/main`
# still a branch that anything has to get past a gate to reach?
#
# MEASURED 2026-08-29, TWICE IN ONE DAY on innovation-upstream/devrc:
#   ~19:58Z  `required_status_checks` DELETED as a deliberate, operator-authorised
#            break-glass to merge two PRs. The restore was written as an EXIT
#            trap, it RAN, and it left main unprotected anyway — `PATCH` cannot
#            restore the sub-resource after a DELETE (see the gotcha below).
#   ~21:56Z  `contexts: null` again, and NOT from the same actor. Independent
#            evidence that a window was open in between: commit 837d3fde is a
#            `Merge branch 'main' of github.com:…` — a DIRECT PUSH to main, which
#            required checks with enforce_admins would have rejected.
# Both were found by a human happening to look. Nothing on either host could have
# said so, and this deadman was FULLY GREEN throughout — correctly, because every
# question it asked was about the two checkouts.
#
# 🔴 THE VERDICT IS THE CONTEXT COUNT, NEVER THE `protected` FLAG. The measured
# incident is `protected: true` with `required_status_checks` GONE: deleting that
# sub-resource leaves the protection object standing, so a checker keying on
# `protected` reports healthy on the exact state that bit us. Both shapes are
# real and both are drift here — measured against the live API 2026-08-29:
#   protected true,  contexts ["tekton/devrc-pytests","tekton/devrc-nodetests"]
#   protected false, contexts []          (a repo with no protection at all)
# so the arm reads `<protected> <context-count>` and branches on the SECOND
# field, printing the first beside it because the two states need different
# repairs (restore the sub-resource vs create protection from nothing).
#
# 🔴 EVERY NON-ANSWER IS A REASON, NEVER A ZERO — the same rule the phase-2 gate
# and the tier arm already apply, and it matters more here because the failure
# mode of a credentials-less `gh` is an empty string, and an empty string parsed
# as a count is 0, and 0 is the value that means DRIFT. That would make this arm
# fire on every timer run the moment `gh` lost its token: a permanently-red gate,
# which `claude/RULES.md` is explicit is worse than no gate. So the arm demands a
# well-formed `<bool> <int>` line and prints COULD NOT MEASURE for anything else
# — no `gh`, no network, no auth, a repo it cannot see, an origin that is not a
# GitHub remote at all. None of those set an rc, and none of them read as a pass.
#
# 🔴 THE SLUG IS DERIVED FROM THE CHECKOUT, never hardcoded. `git ls-remote
# --get-url origin` (local, no network — allowlisted, and the only read-only verb
# that yields a remote URL without admitting `git config`/`git remote`, whose
# write forms would breach passivity). A non-GitHub or unparseable origin is a
# COULD NOT MEASURE, which is also what makes this arm inert against the
# throwaway file:// origins the suite builds.
#
# 🔴 PASSIVITY EXTENDS TO THE API. `gh` can mutate — deleting this very
# protection is a one-liner `devrc/CLAUDE.md` publishes verbatim — and the git
# allowlist that guards the rest of this file is structurally blind to it. So the
# only `gh` this script may run is a plain read: no `-X`/`--method` other than
# GET, no `-f`/`--field` body. Pinned by
# `test_drift_check.py::test_the_gh_calls_are_read_only`.
#
# 🔴 THE REPAIR IS NOT THE OBVIOUS ONE, so the finding carries it. `gh api -X
# PATCH …/protection/required_status_checks` CANNOT restore the sub-resource
# after a DELETE — it returns non-zero and changes nothing. Restoring needs a
# full `PUT …/branches/main/protection`. That is why the measured break-glass
# left main unprotected despite a restore trap that ran: the rollback path had
# never been executed once.
#
# ── UNREACHABLE IS NOT DRIFT (the alerting policy) ────────────────────────────
# 🔴 The timer runs on the WORKBENCH ONLY (gated on the ~/.server-mode marker in
# nix/home.nix), so its remote leg always ssh's to the LAPTOP — a machine that is
# routinely shut, asleep, or off-LAN. If every such run failed the unit, the
# operator would get the same sticky critical toast as a genuine rc 8 up to 4×
# a day, and would learn to ignore the one alert that must keep its meaning.
#
# So an unreachable remote is REPORTED on every run but only ESCALATES to rc 13
# after $DRIFT_UNREACHABLE_ESCALATE consecutive misses (default 4 = ~24h at the
# 6h cadence). The streak is persisted in $DRIFT_STATE_DIR and is RESET the
# moment the host answers — including when it answers with drift.
#
# 🔴 This is deliberately the only softening. Below the threshold the remote leg
# contributes NOTHING to the exit code — it does not mask the local leg, so a
# local rc 8 with an unreachable laptop still exits 8, still fails the unit and
# still toasts (`test_local_rc8_still_wins_when_the_remote_is_unreachable`). And
# if the streak cannot be persisted at all, "how long" is unknowable and the run
# escalates immediately rather than going quiet.
#
# When several hosts fail, the exit code is the MOST SEVERE, not the first —
# this differs from ship.sh on purpose. ship.sh keeps the first non-zero because
# every host's line is printed anyway and a human is reading them live. Nobody
# is reading a timer's output, so the single number it hands to systemd must be
# the worst thing found, or an un-pushed workbench could hide behind a merely
# behind laptop. Severity order (worst first):
#     8 > 24 > 17 > 14 > 13 > 18 > 6 > 2 > 4 > 3 > 12 > 23 > 15 > 10 > 22 > 16
# 🔴 THAT ORDER IS THE severity() TABLE, NOT THE DIGITS — it never was monotonic
# (14 outranks 13 outranks 18 outranks 6 outranks 4), 17 is not "less severe
# than 16" because it is larger, 23 outranks 15 while 22 ranks second-LAST, and
# 24 — the LARGEST digit here — ranks SECOND, directly under rc 8. Reading this
# ladder off the numbers gets that one exactly backwards. Every code below 16
# that is still free (5, 7, 9,
# 11) is reserved to a ship.sh meaning this script does not take, so a new DRIFT
# code has nowhere to go but upward; its rank is stated in severity() and here.
# 🔴 UPWARD IS NOT EMPTY EITHER: ship.sh owns 19 (hosts-disagree), 20
# (superseded) and 21 (usage), and this script has now taken 22, 23 and 24, so
# the next free DRIFT code is 25, not 19. See the reciprocal
# reservation under EXIT CODES above — that collision was one increment away.
# (6 and 2 are both unreachable through this path today — the script exits each
# directly, before any per-host leg runs — but the order is documented for every
# code it owns, and severity() ranks them rather than letting them fall through
# to the unknown-code slot, which would rank them 99, ABOVE rc 8.)
# Per-host lines are ALWAYS printed for every host, whatever the code.
#
# Untracked files are counted and listed per host as INFORMATION — they are the
# same loss class (work sitting on one host that no other host and no backup has)
# and cost nothing to report. 🔴 THE SUBSET THAT SITS IN A PATH NIX READS IS NOT
# information only: it rides the rc 23 ladder below. This comment said "they
# never change the exit code" for as long as that was true of all of them.
#
# ── USAGE ─────────────────────────────────────────────────────────────────────
#   scripts/drift-check.sh                 # check this host + the other one
#   scripts/drift-check.sh --no-remote     # this host only (no ssh)
#   scripts/drift-check.sh --no-local      # the other host only
#   scripts/drift-check.sh --detect-role   # print detected local role, exit 0
#
# Env overrides:
#   SHIP_ROLE    force the local role (workbench|laptop) — shared with ship.sh
#   REMOTE_SSH   ssh target for the OTHER host (default derived from role)
#   LAPTOP_SSH   back-compat: applies ONLY when the remote host is the laptop
#   DRIFT_REPO   repo path checked on the LOCAL host (default $HOME/workspace/devrc)
#   DRIFT_UNTRACKED_MAX  max untracked paths listed per host (default 10, integer)
#   DRIFT_DANGLING_MAX   max dangling symlinks listed per host (default 10, integer)
#   DRIFT_PARITY_ROOTS   space-separated dirs, relative to $HOME, scanned for
#                        dangling MANAGED symlinks (default ".claude .config/opencode")
#   DRIFT_MANAGED_PREFIX what counts as a MANAGED symlink target (default
#                        "/nix/store/"). Exists so the test suite can build a
#                        fixture tree; the default is the only correct value on
#                        a real host, and is NOT forwarded over ssh.
#   DRIFT_SRC_FETCH_TIMEOUT  seconds each SOURCE REPO's `git fetch` may take
#                        (default 30, integer). These are private repos over ssh
#                        and a user unit has no agent, so the failure mode to
#                        avoid is a HANG, not an error — an error is reported.
#                        Deliberately NOT forwarded over ssh: the remote host
#                        uses the default, and every value sent across that hop
#                        is one that has to be proved safe.
#   DRIFT_UNREACHABLE_ESCALATE  consecutive unreachable runs before rc 13 (default 4)
#   DRIFT_UNMEASURED_ESCALATE   consecutive runs a built-source scope may be
#                        UNMEASURABLE FOR A STRUCTURAL REASON (no upstream /
#                        detached HEAD / a pathspec count that will not parse)
#                        before rc 18 (default 4 ≈ 24h at the 6h cadence).
#                        Deliberately NOT forwarded over ssh: the ladder is kept
#                        by the host running the driver, for both hosts' scopes.
#   DRIFT_UNMEASURED_FETCH_ESCALATE  the same ladder for the one reason with a
#                        plausibly TRANSIENT cause — a failed `git fetch`
#                        (default 12 ≈ 3 days). Separate from the tunable above
#                        because they are not the same hazard; see "UNMEASURED IS
#                        NOT FOREVER". `repo ABSENT` is on NEITHER ladder.
#   DRIFT_NIXDIRT_ESCALATE  consecutive runs an UNTRACKED file may sit in a
#                        NIX-READ path before rc 23 (default 12 ≈ 3 days at the
#                        6h cadence). Longer than DRIFT_UNMEASURED_ESCALATE on
#                        purpose: writing a file and committing it an hour later
#                        is the normal working shape, and the finding is that it
#                        has been SITTING there. Deliberately NOT forwarded over
#                        ssh — the ladder is kept by the host running the driver,
#                        for both hosts' paths.
#   DRIFT_NIXDIRT_MAX    max nix-read untracked paths ENUMERATED per host
#                        (default 10, integer >= 1). 🔴 A LISTING BOUND ONLY —
#                        `hits=` on the FACT line is the finding and is never
#                        capped, and the driver refuses a report whose `listed=`
#                        disagrees with the pairs it can see. Floored at 1 rather
#                        than merely non-negative: 0 emitted no pairs, so the
#                        ladder walked nothing and rc 23 was disabled with the
#                        summary still printing a clean-looking `hits=0` over a
#                        real denominator. IS forwarded over ssh (the enumeration
#                        happens on each host), hence require_positive_int + %q.
#   DRIFT_STATE_DIR  where the unreachable, unmeasured and nix-dirt streaks are
#                    persisted
#                    (default ${XDG_STATE_HOME:-$HOME/.local/state}/drift-check)
#   DRIFT_SESSION_MANAGER  path to the session-manager used by the phase-2 gate
#                    (default: the copy beside this script). Exists so the test
#                    suite can drive every branch of that gate against a stub —
#                    the real one scans the operator's live tmux, which no test
#                    may do. Deliberately NOT forwarded to the remote host: the
#                    gate is local-only, and every value sent over ssh is one
#                    that has to be proved safe.
#   DRIFT_PHASE2_TIMEOUT  seconds the phase-2 scan may take (default 60, integer)
#   DRIFT_GH          the `gh` binary the branch-protection arm (rc 24) runs
#                    (default `gh`, resolved from PATH). Exists so the suite can
#                    drive that arm against a stub, and — pointed at a path that
#                    does not exist — keep every OTHER test from reaching the
#                    operator's real credentials and the network. Same role as
#                    DRIFT_SESSION_MANAGER, and for the same measured reason.
#   DRIFT_GH_TIMEOUT  seconds that probe may take (default 20, integer)
#   DRIFT_PROTECT_SLUG  owner/repo the rc-24 arm asks about, INSTEAD of deriving
#                    it from the checkout's origin. Exists for the suite: the
#                    derivation reads the same `origin` the git leg fetches
#                    from, so giving the arm a GitHub slug by editing origin
#                    would send `git fetch` to the real github.com. The derived
#                    value is the only correct one on a real host.
#   DRIFT_TIER_LEDGER  path to the skill-listing tier ledger (default: the
#                    claude/skill-tiers.json beside this script's repo). Exists
#                    so the suite can drive the rc-22 arm against a fixture
#                    ledger; the default is the only correct value on a real
#                    host. Deliberately NOT forwarded over ssh — the comparison
#                    happens in the driver, and every value sent across that hop
#                    is one that has to be proved safe.
set -uo pipefail

# --- Host identity: SOURCED, never copied (see header) ------------------------
# The source path is symlink-resolved: invoked through a symlink, an unresolved
# ${BASH_SOURCE[0]} would look for lib/ next to the SYMLINK and not find it.
#
# 🔴 THIS FILE HAS TWO SOURCING CONVENTIONS AND THEY ARE NOT AN INCONSISTENCY —
# do not "tidy" one into the other. This one resolves lib/ relative to THE
# SCRIPT, because host identity must be decided before any repo path is known and
# by the copy of the checker that is running. The nix-read lib (see NU_LIB in the
# CHECK payload) resolves relative to `$repo` INSTEAD, because that payload is
# piped to whichever HOST is being examined and must load that host's own
# checkout — a script-relative path there would silently reach for a lib beside a
# copy that does not exist on the far side. Each is wrong in the other's place.
_drift_self="${BASH_SOURCE[0]}"
_drift_resolved="$(readlink -f "$_drift_self" 2>/dev/null || true)"
[ -n "$_drift_resolved" ] && _drift_self="$_drift_resolved"
_drift_dir="$(cd "$(dirname "$_drift_self")" 2>/dev/null && pwd)"
_drift_lib="$_drift_dir/lib/host-role.sh"
_drift_phase2_py="$_drift_dir/lib/drift_phase2.py"
if [ ! -r "$_drift_lib" ]; then
  echo "drift-check: cannot read $_drift_lib — host identity cannot be resolved." >&2
  exit 6
fi
# shellcheck source=lib/host-role.sh
. "$_drift_lib"

if [ "${1:-}" = "--detect-role" ]; then
  if [ "$#" -ge 2 ]; then detect_role "$2"; else detect_role "$(local_ipv4s | tr '\n' ' ')"; fi
  exit 0
fi

# 🔴 SET-BUT-EMPTY IS A BUG, NOT A REQUEST FOR THE DEFAULT. `${VAR:-default}`
# cannot tell "unset" from "set to the empty string", so a caller that computed
# a repo path and got `""` silently checks — and `git fetch`es — the OPERATOR'S
# OWN CLONE instead of the one it meant. UNSET must keep defaulting (this
# variable is deliberately NOT forwarded over ssh, and the remote host's repo
# lives at its own $HOME/workspace/devrc); EMPTY must stop the run.
if [ "${DRIFT_REPO+set}" = set ] && [ -z "$DRIFT_REPO" ]; then
  echo "drift-check: DRIFT_REPO is SET but EMPTY." >&2
  echo "  That is a caller bug, not a request for the default — an empty value" >&2
  echo "  would silently resolve to \$HOME/workspace/devrc and fetch into the" >&2
  echo "  operator's own clone. Unset it to get the default, or give it a path." >&2
  exit 2
fi
DRIFT_REPO="${DRIFT_REPO:-$HOME/workspace/devrc}"
DRIFT_UNTRACKED_MAX="${DRIFT_UNTRACKED_MAX:-10}"
DRIFT_DANGLING_MAX="${DRIFT_DANGLING_MAX:-10}"
DRIFT_UNREACHABLE_ESCALATE="${DRIFT_UNREACHABLE_ESCALATE:-4}"
# The two rc-18 ladders. Two tunables, not one, because "this branch has no
# upstream" and "the fetch failed" are different hazards with different lifetimes
# — see "UNMEASURED IS NOT FOREVER" in the header for the full argument.
DRIFT_UNMEASURED_ESCALATE="${DRIFT_UNMEASURED_ESCALATE:-4}"
DRIFT_UNMEASURED_FETCH_ESCALATE="${DRIFT_UNMEASURED_FETCH_ESCALATE:-12}"
# The rc 23 ladder — see "UNTRACKED IN A NIX-READ PATH" for why it is longer than
# the structural-unmeasured one rather than the same number reused.
DRIFT_NIXDIRT_ESCALATE="${DRIFT_NIXDIRT_ESCALATE:-12}"
# 🔴 A BOUND, like every sibling listing in this file. rc 23's was the only
# uncapped per-host listing: untracked caps at DRIFT_UNTRACKED_MAX, dangling at
# DRIFT_DANGLING_MAX, commits at `head -n 10`, fetch stderr at `head -n 3`.
# `claude/skills` is a whole-directory STORE source, so an untracked,
# non-gitignored subtree under it emits one FACT token, journal lines AND a state
# file per path — unbounded, on a unit whose only output is the journal.
#
# 🔴 IT BOUNDS ONE RUN'S ENUMERATION, NOT THE STATE DIR OVER TIME. A counter is
# created per (host, path) that gets emitted, so ten new paths per run still
# accrete ten new files per run — the cap stops one run from creating a thousand,
# it does not stop a host's state dir growing monotonically over its lifetime.
# 🔴 NOTHING RECLAIMS THEM, AND n_streak_reset IS NOT AN EXCEPTION: it REWRITES
# the counter to `CLEARED 0` and never unlinks it — see its own header, which
# explains that `rm` is a destructive command to the passivity scanner guarding
# this file, so a read-only deadman may not delete. So the file count only ever
# grows; the files are a few bytes each and that was judged the better trade.
# An earlier revision of this very comment claimed the unlink happens. It does
# not, and acting on that claim means adding the one command this file is
# designed to refuse.
#
# 🔴 AND IT IS THE ONLY CAP IN THIS FILE THAT COULD CHANGE A VERDICT, which is
# why it alone is floored at 1. See the require_positive_int call below.
DRIFT_NIXDIRT_MAX="${DRIFT_NIXDIRT_MAX:-10}"
DRIFT_STATE_DIR="${DRIFT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/drift-check}"
DRIFT_SESSION_MANAGER="${DRIFT_SESSION_MANAGER:-$_drift_dir/session-manager}"
DRIFT_PHASE2_TIMEOUT="${DRIFT_PHASE2_TIMEOUT:-60}"
DRIFT_SRC_FETCH_TIMEOUT="${DRIFT_SRC_FETCH_TIMEOUT:-30}"
# The branch-protection arm (rc 24). Spelled as an explicit unset-test rather
# than `${DRIFT_GH:-gh}` so the word `gh` survives word-bounded as a COMMAND
# NAME: this binary is resolved from the unit's PATH, and
# `test_every_command_the_checker_runs_is_on_the_unit_path` looks for exactly
# that spelling. Overridable for the same reason DRIFT_SESSION_MANAGER is — the
# suite must be able to point the arm at a stub, and at a path that does not
# exist, without the operator's real credentials or the network ever being
# reachable from a test.
if [ -z "${DRIFT_GH+set}" ]; then DRIFT_GH=gh; fi
DRIFT_GH_TIMEOUT="${DRIFT_GH_TIMEOUT:-20}"
# 🔴 A BOUND on the ruleset loop, like every sibling listing here. The rules
# endpoint returns one entry per applying ruleset, repo- and org-level mixed,
# so the loop is over a set this script does not control. Hitting the cap is
# COULD NOT MEASURE, never DRIFT: stopping early means we did not look, and
# "did not look" must never read as "nothing gates main".
DRIFT_GH_RULESET_MAX="${DRIFT_GH_RULESET_MAX:-5}"

# 🔴 Both tunables are INTERPOLATED INTO A SCRIPT THAT RUNS ON THE OTHER HOST
# (piped to `bash -s` over ssh), so a non-integer value is remote code execution
# on a machine this script is otherwise forbidden to touch. Operator-controlled,
# hence low exploitability — but it is a passivity hole on the far side of the
# ssh hop, which the static scanner structurally cannot see. Validate here; the
# printf below ALSO uses %q, deliberately belt-and-braces.
#
# 🔴 ASSERT THE VALUE, NEVER ITS SPELLING — and PROVE the range rather than
# testing for its complement. Three measured traps, each of which produced a
# guard that READ as watertight and was not:
#
#   * `case "$2" in 0) reject ;; esac` is a SPELLING check. `00`, `000` and
#     `007` are all-digits and NONE of them matches the glob `0`, so they walked
#     straight past a floor whose entire purpose was to be unwalkable. Measured
#     here: DRIFT_NIXDIRT_MAX=00 over three untracked nix-read files gave
#     [0,0,0,0] where the default gives [0,0,23,23] — rc 23 switched off by an
#     env var, with the journal still honestly printing `hits=3 listed=0`, so
#     only the exit code lied. Second instance of this exact defect in one
#     night; PR #854 had it too.
#   * `[ "$2" -gt "$CEILING" ]` does NOT answer "no" for a value bash cannot
#     parse — it ERRORS and the `if` then takes the FALSE branch. So a guard
#     written as "reject when too big" waves the too-big value through.
#     Measured: `[ 99999999999999999999 -gt 100000 ]` prints "integer expected"
#     and is FALSE. Requiring the POSITIVE assertion inverts that: a value this
#     shell cannot compare cannot prove itself in range, so it is refused.
#   * the same unparseable value reaching a LADDER disables a verdict rather
#     than merely mis-formatting a list, which is why require_int is bounded too
#     and not only require_positive_int: `[ "$STK" -ge "$THR" ]` with a >2^63
#     THR errors, evaluates FALSE, and the ladder goes quiet forever while the
#     run reports no drift. Measured with STK=5, THR=99999999999999999999.
#
# The ceiling sits far above any real fleet — two hosts, a handful of untracked
# paths, ladders measured in runs per day. Above it a tunable has stopped
# bounding anything and the value is far likelier a typo than an intent.
DRIFT_INT_CEILING=100000

require_int() { # require_int <name> <value> — 0..DRIFT_INT_CEILING inclusive
  case "$2" in
    ''|*[!0-9]*) echo "drift-check: $1 must be a non-negative integer, got: $2" >&2; exit 2 ;;
    # The ONE legal spelling of zero. No-op, so it falls through to the range
    # proof below. (Its own line, not a trailing comment: a `;` inside a trailing
    # comment starts a new segment for the command-word scanner in
    # test_drift_check.py, and the next word reads as a command.)
    0) ;;
    0*) echo "drift-check: $1 must not have a leading zero, got: $2" >&2
        echo "  A leading zero makes the SPELLING and the VALUE disagree, which is exactly" >&2
        echo "  how a guard written against the spelling gets walked past. Write it plain." >&2
        exit 2 ;;
  esac
  if [ "$2" -ge 0 ] 2>/dev/null && [ "$2" -le "$DRIFT_INT_CEILING" ] 2>/dev/null; then
    return 0
  fi
  echo "drift-check: $1 must be between 0 and $DRIFT_INT_CEILING, got: $2" >&2
  echo "  A value this shell cannot COMPARE is refused, never accepted: a >2^63 operand" >&2
  echo "  turns '[ x -ge y ]' into an error that evaluates FALSE, so every ladder and cap" >&2
  echo "  it feeds goes quiet and the run reports no drift." >&2
  exit 2
}
# 🔴 ZERO IS NOT A HARMLESS BOUND WHEN THE BOUND FEEDS A VERDICT. require_int
# accepts 0, which is right for every pure LISTING cap here — DRIFT_UNTRACKED_MAX
# and DRIFT_DANGLING_MAX print their counts separately and 0 there honestly means
# "count them, name none of them". It is wrong for the one cap whose output the
# DRIVER walks: with DRIFT_NIXDIRT_MAX=0 the payload emitted no pairs, so no
# streak was bumped, nothing escalated, and four consecutive runs over three
# genuinely untracked nix-read files returned [0,0,0,0] instead of [0,0,23,23] —
# rc 23 silently switched off from an env var, with the summary still printing a
# clean-looking `hits=0` beside a real non-zero denominator. So this one has a
# FLOOR, and the floor is stated in the message rather than left to be inferred.
require_positive_int() { # require_positive_int <name> <value> — 1..DRIFT_INT_CEILING
  case "$2" in
    ''|*[!0-9]*) echo "drift-check: $1 must be an integer >= 1, got: $2" >&2; exit 2 ;;
    0) echo "drift-check: $1 must be an integer >= 1, got: 0" >&2
       echo "  0 emits no paths at all, which leaves the rc 23 ladder nothing to walk:" >&2
       echo "  no streak is bumped and nothing escalates, while the summary still prints" >&2
       echo "  hits=<n> over a real denominator. That is a verdict turned off by a cap." >&2
       exit 2 ;;
    0*) echo "drift-check: $1 must not have a leading zero, got: $2" >&2
        echo "  The SPELLING and the VALUE disagree: '00' is zero, which this tunable" >&2
        echo "  refuses outright, and '007' is seven. Measured — '00' walked past the '0'" >&2
        echo "  arm and disabled rc 23. Write it plain." >&2
        exit 2 ;;
  esac
  if [ "$2" -ge 1 ] 2>/dev/null && [ "$2" -le "$DRIFT_INT_CEILING" ] 2>/dev/null; then
    return 0
  fi
  echo "drift-check: $1 must be between 1 and $DRIFT_INT_CEILING, got: $2" >&2
  echo "  A value this shell cannot COMPARE is refused, never accepted: with a >2^63" >&2
  echo "  operand '[ \$NU_EMIT -lt \$NU_MAX ]' errors and evaluates FALSE, so NO path is" >&2
  echo "  ever enumerated and rc 23 is switched off exactly as a cap of 0 would." >&2
  exit 2
}
require_int DRIFT_UNTRACKED_MAX "$DRIFT_UNTRACKED_MAX"
require_int DRIFT_DANGLING_MAX "$DRIFT_DANGLING_MAX"
require_int DRIFT_UNREACHABLE_ESCALATE "$DRIFT_UNREACHABLE_ESCALATE"
# Not interpolated into a remote payload either — but a non-integer here would
# make `[ "$STK" -ge "$THR" ]` an error rather than a comparison, and the ladder
# would go quiet in exactly the direction this code exists to refuse.
require_int DRIFT_UNMEASURED_ESCALATE "$DRIFT_UNMEASURED_ESCALATE"
require_int DRIFT_UNMEASURED_FETCH_ESCALATE "$DRIFT_UNMEASURED_FETCH_ESCALATE"
# Same reasoning: not forwarded over ssh either, but a non-integer would turn
# `[ "$STK" -ge "$THR" ]` into an error rather than a comparison and the ladder
# would go quiet — the one direction it may never fail in.
require_int DRIFT_NIXDIRT_ESCALATE "$DRIFT_NIXDIRT_ESCALATE"
# Interpolated into the payload that runs on the OTHER host — same reason
# DRIFT_UNTRACKED_MAX is validated: a non-integer here is remote code execution.
# require_positive_int, not require_int: see its header for the measured
# [0,0,0,0] this floor exists to refuse.
require_positive_int DRIFT_NIXDIRT_MAX "$DRIFT_NIXDIRT_MAX"
# Not interpolated into a remote payload — but it IS handed to `timeout`, where a
# non-integer would make the phase-2 scan fail in a way that reads as "the tool
# is broken" rather than "you passed nonsense".
require_int DRIFT_PHASE2_TIMEOUT "$DRIFT_PHASE2_TIMEOUT"
# Same reasoning as DRIFT_PHASE2_TIMEOUT: not interpolated into a remote payload,
# but handed to `timeout`, where a non-integer reads as "the tool is broken".
require_int DRIFT_SRC_FETCH_TIMEOUT "$DRIFT_SRC_FETCH_TIMEOUT"
# Same again for the branch-protection probe's cap.
require_int DRIFT_GH_TIMEOUT "$DRIFT_GH_TIMEOUT"
# Floored at 1: a cap of 0 would examine no ruleset and report COULD NOT
# MEASURE forever, which is the silent-blindness shape rc 18 exists for.
require_positive_int DRIFT_GH_RULESET_MAX "$DRIFT_GH_RULESET_MAX"

DO_LOCAL=1
DO_REMOTE=1
for a in "$@"; do
  case "$a" in
    --no-remote|--no-laptop) DO_REMOTE=0 ;;
    --no-local)  DO_LOCAL=0 ;;
    --detect-role) : ;;   # handled above
    # Print the contiguous comment block after the shebang (no line numbers to drift).
    -h|--help)   awk 'NR>1 { if (/^#/) print; else exit }' "$0"; exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

# Refuse the combination that looks at nothing. Without this, `--no-local
# --no-remote` printed "no drift — both hosts on branch main at origin/main" and
# exited 0 having checked neither host: a green from a checker wired to nothing,
# which is precisely the failure this whole subsystem exists to prevent.
if [ "$DO_LOCAL" = 0 ] && [ "$DO_REMOTE" = 0 ]; then
  echo "drift-check: --no-local and --no-remote together check NOTHING." >&2
  echo "  refusing to print a pass for a run that looked at no host." >&2
  exit 2
fi

LOCAL_ROLE="$(resolve_local_role)"
if [ "$LOCAL_ROLE" != workbench ] && [ "$LOCAL_ROLE" != laptop ]; then
  echo "drift-check: could not identify this host (role='$LOCAL_ROLE')." >&2
  echo "  local IPv4s: $(local_ipv4s | tr '\n' ' ')" >&2
  echo "  expected a workbench ($WORKBENCH_IP_PRIMARY) or laptop ($LAPTOP_IP_PRIMARY) address." >&2
  echo "  override with SHIP_ROLE=workbench|laptop to force." >&2
  exit 6
fi
REMOTE_ROLE="$(remote_role_of "$LOCAL_ROLE")"
REMOTE_SSH="$(remote_ssh_of "$LOCAL_ROLE")"

# severity <rc> -> a comparable number; higher = worse. Unknown codes rank above
# every known one so a NEW failure mode can never be silently outranked into
# invisibility by a merely-behind host.
severity() {
  case "${1:-0}" in
    0)  echo 0 ;;
    8)  echo 70 ;;
    # 24 (main on the canonical remote has no required status checks) sits
    # between 8 and 17, and it is the LARGEST digit in this table — reading the
    # ladder off the numbers puts it dead last, which is the wrong end.
    #   BELOW 8, because rc 8 is work that exists on exactly ONE machine, in no
    #   commit anyone else has, where a careless rescue destroys the only copy.
    #   Nothing is lost here and the repair is one reversible API call. A hazard
    #   that has already cost something outranks one that is merely open.
    #   ABOVE 17, because 17 is bounded — one package's source is stale on one
    #   host, and what it can corrupt is what that package does. This is the gate
    #   on the branch BOTH hosts converge to, so it admits every other failure
    #   class in this table onto both machines at once, including the un-pushed
    #   and stale-source ones. It was also measured admitting a direct push to
    #   main within hours of being opened, so "merely open" is a short state.
    24) echo 68 ;;
    # 17 (a source repo devrc BUILDS FROM is not current here) sits between 8
    # and 14, and the digit says nothing about that — see the header.
    #   BELOW 8, because a diverged devrc stops every future change to this host,
    #   of which a stale source repo is one instance; and rc 8's rescue can
    #   destroy work if done carelessly, while this one is a pull.
    #   ABOVE 14, because a dangling managed symlink is LOUD at the moment of use
    #   ("command not found") whereas this is SILENT: the measured failure shipped
    #   a binary that ran, exited 0, and did nothing, wearing a version string
    #   that said otherwise. It also carries the AHEAD case — un-pushed commits in
    #   a repo whose code this host compiles — which is rc 8's loss class.
    17) echo 67 ;;
    # 14 (a host's managed symlinks resolve to nothing) sits between 8 and 13.
    # It is BELOW 8 because rc 8 means work exists on exactly one machine and a
    # careless fix destroys it, whereas a broken deployment is repaired by a
    # switch with nothing to lose. It is ABOVE 13 because it is a host we DID
    # observe, saying something is wrong — 13 only says we could not look.
    14) echo 65 ;;
    13) echo 60 ;;
    # 18 (a built-source scope has been UNMEASURABLE for N consecutive runs) sits
    # between 13 and 6, and the digit says nothing about that either.
    #   BELOW 13, because they are the same KIND of finding — a persistent
    #   inability to look, escalated only after a run of consecutive misses — and
    #   13 is that inability about an ENTIRE HOST (every check on it, including
    #   the un-pushed-commits one) while this is one built-source scope.
    #   ABOVE 6/4/3, because those are SINGLE-RUN "could not evaluate" outcomes
    #   that may simply be gone next run, whereas 18 is persistent by
    #   construction: it cannot be emitted until the same scope has failed to
    #   measure N runs in a row. And what it hides is rc 17 (rank 67) — a stale
    #   built source is INVISIBLE for as long as this is true.
    18) echo 59 ;;
    # 6 cannot arrive here today (the script exits 6 before any host leg runs),
    # but it is a code this file OWNS, and an owned code with no case would rank
    # 99 — above rc 8 — which is the wrong answer for "I could not identify the
    # local host" versus "a host has un-pushed commits".
    6)  echo 58 ;;
    # 2 (DRIFT_REPO set-but-EMPTY) is the same shape as 6 above: today it cannot
    # reach here — the top-level guard exits before any host leg, and the CHECK /
    # SRCREPO payload copies only fire if a caller forwards an empty DRIFT_REPO,
    # which neither leg does. But it is a code this file now OWNS and emits, and
    # per the rc 6 note an owned code with no case ranks 99, ABOVE rc 8 — wrong
    # for a caller bug versus a host holding un-pushed commits.
    #   BELOW 6, because 6 means the run could not even identify what it was
    #   looking at, whereas this one knows exactly what is wrong and who to tell.
    #   ABOVE 4/3, because those are single-run "could not evaluate" outcomes
    #   that may simply be gone next run, whereas an empty override is
    #   DETERMINISTIC — re-running changes nothing until the caller is fixed.
    2)  echo 57 ;;
    4)  echo 55 ;;
    3)  echo 50 ;;
    12) echo 40 ;;
    # 23 (an untracked file in a NIX-READ path has persisted N runs) sits between
    # 12 and 15, and the digit says nothing about that either.
    #   BELOW 12, because a checkout that is not on main will be SKIPPED by
    #   ship.sh and therefore stops receiving every future change; this one still
    #   converges normally, it just carries a passenger while it does.
    #   ABOVE 15, because the loss classes differ in kind. A settings.json key
    #   set that disagrees costs the operator a CAPABILITY on one host, and both
    #   copies of the fact still exist. This is content that exists on exactly
    #   ONE machine, in no commit and no backup, AND is being executed there —
    #   rc 8's loss class at a smaller scope, which is why it outranks a parity
    #   difference and not a divergence.
    23) echo 37 ;;
    # 15 ranks BELOW 12 and above 10: a key-set or plugin difference is a real
    # divergence, but ship.sh does not fix it and it costs the operator a
    # capability, not a commit. 14 ranks just under 8 — see the table in the
    # header for why un-pushed commits still outrank a broken deployment.
    15) echo 35 ;;
    10) echo 30 ;;
    # 22 (a host's deployed skillOverrides disagree with the devrc tier ledger)
    # ranks BELOW 10 and above 16, and the digit says nothing about that either.
    #   BELOW 10, because a host that is BEHIND has not received the ledger yet:
    #   a stale checkout can PRODUCE this finding as a symptom, so the code that
    #   names the cause must outrank the one that names the effect. Shipping the
    #   host is also the first thing to try.
    #   ABOVE 16, because 16 is not a fault at all. This one is a real
    #   divergence: the always-on skill listing on that host is not the listing
    #   the repo describes, which degrades routing silently and in exactly the
    #   way the tier mechanism exists to control.
    22) echo 25 ;;
    # 16 is the FLOOR of the owned codes, deliberately. It is not a fault at all
    # — it says an optional cleanup became possible — so it must never outrank a
    # host that is behind, let alone one with un-pushed commits. Being last also
    # means it can only ever BE the verdict on an otherwise-clean run, which is
    # the only run on which "go do this now" is useful advice.
    16) echo 20 ;;
    *)  echo 99 ;;
  esac
}

# ── The per-host CHECK routine ────────────────────────────────────────────────
# Run identically on each host: locally via `bash -c`, remotely by PIPING it to
# `bash -s` over ssh. It is piped rather than inlined because BOTH hosts' login
# shell is zsh, and zsh does not word-split an unbraced `$var` — an inlined
# multi-command script silently behaves differently there. `bash -s` removes the
# interpreter from the equation entirely.
#
# READ-ONLY BY CONSTRUCTION: the only git commands here are `fetch`, `rev-parse`,
# `rev-list`, `symbolic-ref`, `show-ref`, `ls-files` and `log`. `fetch` writes
# remote-tracking refs only.
CHECK='
set -uo pipefail
if [ "${DRIFT_REPO+set}" = set ] && [ -z "$DRIFT_REPO" ]; then
  echo "[${DRIFT_LABEL:-host}] DRIFT_REPO is SET but EMPTY — refusing to fall back to \$HOME/workspace/devrc." >&2
  exit 2
fi
repo="${DRIFT_REPO:-$HOME/workspace/devrc}"
label="${DRIFT_LABEL:-host}"
maxu="${DRIFT_UNTRACKED_MAX:-10}"
say() { echo "[$label] $*"; }

[ -d "$repo/.git" ] || [ -f "$repo/.git" ] || { say "no repo at $repo"; exit 3; }
cd "$repo" || { say "no repo at $repo"; exit 3; }

# The stderr of git fetch is CAPTURED AND REPRINTED, never discarded: rc 4 is a
# recurring code (key rotation, DNS, host-key churn) and for a unit whose only
# output is the journal, that message is the ONLY diagnostic there will ever be.
fetch_err=$(git fetch origin -q 2>&1) || {
  say "git fetch failed — cannot evaluate drift"
  printf "%s\n" "$fetch_err" | sed "s|^|[$label]   git: |"
  exit 4
}
target=$(git rev-parse -q --verify origin/main) || {
  say "no origin/main after a successful fetch — remote/branch misconfigured."
  say "  check: git -C $repo remote -v ; git -C $repo branch -r"
  exit 4
}

# --- INFORMATION (never affects the exit code) --------------------------------
untracked=$(git ls-files --others --exclude-standard 2>/dev/null)
if [ -n "$untracked" ]; then
  n=$(printf "%s\n" "$untracked" | wc -l | tr -d " ")
  say "untracked: $n file(s) — present on this host only, in no commit and no backup"
  printf "%s\n" "$untracked" | head -n "$maxu" | sed "s|^|[$label]     - |"
  [ "$n" -gt "$maxu" ] && say "    ... and $(( n - maxu )) more"
else
  n=0
  say "untracked: 0"
fi

# --- The rc 23 subset: untracked AND in a path NIX READS ----------------------
# 🔴 NOT information. See "UNTRACKED IN A NIX-READ PATH" in the header. This leg
# only MEASURES and reports; the consecutive-run ladder lives in the driver,
# because a streak is persistent state and belongs to the machine keeping the
# record — the same reason the rc 13 and rc 18 ladders are there.
#
# The FACT line is the contract with the driver and its shape is deliberate:
#   FACT nix-untracked untracked=<N> nixread=<M> hits=<H> listed=<L> reason=<TOKEN> [<path>=<REACH>]…
# The counts are emitted UNCONDITIONALLY, including as zeros, because a
# driver that sees no pairs must be able to tell "this host has no untracked
# nix-read files" from "the nix-read set came out empty and every file on every
# host is therefore clean". Pairs are told apart from the header fields by their
# VALUE — LIVE or DROPPED, the whole reach vocabulary — so a repo-root file
# called `reason` cannot be read as a header field.
#
# 🔴 `hits=` EXISTS BECAUSE THE PAIR LIST IS CAPPED AND THE FINDING IS NOT. The
# driver used to COUNT the pairs to get its hit total, so DRIFT_NIXDIRT_MAX
# truncated the machine-readable contract as well as the human listing: measured
# with 15 untracked nix-read files, the human line correctly said `15 of 15`
# while the FACT line carried 10 pairs with no marker at all (the `... and N
# more` note goes to `say`, which the driver does not parse) and the summary
# printed `hits=10`. A count derived from a truncated list is a smaller version
# of the reassuring-zero this whole block refuses, so the count now travels as
# its own field and the list is only ever evidence.
#
# `listed=` is what makes `hits=` checkable rather than merely asserted: the
# driver cross-checks it against the pairs it can actually see and refuses the
# whole report if they disagree, which is the only way a stream truncated in
# transit is distinguishable from a host that had nothing more to say.
#
# 🔴 THE PAIR CARRIES THE REACH, NOT THE CLASS, and that is a correction. A STORE
# class says nix READS the path; it does NOT say the file got into the artifact,
# because nix filters a git checkout to the files git knows about. Every path in
# this block came from `git ls-files --others`, so it is UNTRACKED by
# construction — which makes the reach of a STORE path DROPPED, always. The
# translation is nix_read_artifact_reach in the shared lib, called HERE with the
# known-tracked bit hardcoded to 0, so the rule has exactly one definition and
# ship.sh (which sees both kinds) calls the same function.
NU_REASON=NOLIB
NU_PAIRS=""
NU_M=0
NU_HITS=0
NU_UNREP=0
NU_EMIT=0
NU_MAX="${DRIFT_NIXDIRT_MAX:-10}"
NU_LIB="$repo/scripts/lib/nix_read_paths.sh"
if [ -r "$NU_LIB" ]; then
  # shellcheck source=lib/nix_read_paths.sh
  . "$NU_LIB"
  if nix_read_scan "$repo"; then
    NU_REASON=OK
    NU_M="$NIXREAD_COUNT"
  else
    NU_REASON="$NIXREAD_REASON"
    NU_M="$NIXREAD_COUNT"
  fi
fi
if [ "$NU_REASON" = OK ] && [ "$n" != 0 ]; then
  while IFS= read -r NU_P; do
    [ -n "$NU_P" ] || continue
    # 🔴 HITS COUNTS ALL OF THEM; only the EMITTED pairs are capped. The count is
    # the finding and must never be truncated — a cap that silently shrank the
    # number would be a smaller version of the reassuring-zero this file refuses.
    #
    # 🔴 ONE CAP PREDICATE, NOT ONE PER CLASS. LIVE and DROPPED used to carry
    # their own copy of `hit++ ; emit if under the cap`, and a mutation sweep
    # showed why that is not merely repetition: mutating the cap in the LIVE arm
    # SURVIVED the full suite, because reaching it needs more untracked
    # mkOutOfStoreSymlink targets than any fixture has, so no test of the
    # DROPPED arm could ever see it. Two copies is two things to cover; one is
    # one.
    #
    # 🔴 NO APOSTROPHE ANYWHERE IN THIS PAYLOAD, comments included. CONVERGE-like
    # single-quoted strings end at the first quote: the first draft of this very
    # comment wrote "the DROPPED arm-apostrophe-s test" and broke 173 tests at
    # once, exactly as ship.sh warns about its own empty-string literal.
    NU_R="$(nix_read_artifact_reach "$(nix_read_class_of "$NU_P")" 0)"
    case "$NU_R" in
      LIVE|DROPPED)
        NU_HITS=$(( NU_HITS + 1 ))
        if [ "$NU_EMIT" -lt "$NU_MAX" ]; then
          NU_PAIRS="$NU_PAIRS $NU_P=$NU_R"; NU_EMIT=$(( NU_EMIT + 1 ))
        fi ;;
      UNREPRESENTABLE) NU_UNREP=$(( NU_UNREP + 1 )) ;;
    esac
  done <<NU_EOF
$untracked
NU_EOF
fi

if [ "$NU_REASON" != OK ]; then
  say "untracked-in-nix-read-paths: COULD NOT MEASURE ($NU_REASON) — $n untracked file(s),"
  say "  $NU_M nix-read path(s) derived. A zero here would classify every untracked file on"
  say "  every host as harmless, so this run makes no such claim and sets no code."
elif [ "$NU_HITS" = 0 ]; then
  say "untracked-in-nix-read-paths: 0 of $n untracked file(s), against $NU_M nix-read path(s)."
else
  say "untracked-in-nix-read-paths: $NU_HITS of $n untracked file(s), against $NU_M nix-read path(s):"
  for NU_X in $NU_PAIRS; do
    say "    - ${NU_X%=*} (${NU_X##*=})"
  done
  # The same "... and N more" shape every other capped listing in this file uses,
  # so a truncated list can never be mistaken for the whole set.
  [ "$NU_HITS" -gt "$NU_EMIT" ] && say "    ... and $(( NU_HITS - NU_EMIT )) more (DRIFT_NIXDIRT_MAX=$NU_MAX)"
fi
[ "$NU_UNREP" != 0 ] && say "  ($NU_UNREP untracked path(s) NOT CLASSIFIED — a character the classifier does not model)"
echo "[$label] FACT nix-untracked untracked=$n nixread=$NU_M hits=$NU_HITS listed=$NU_EMIT reason=$NU_REASON$NU_PAIRS"

# --- Which branch is checked out? ---------------------------------------------
branch=$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo DETACHED)
off_main=0
if [ "$branch" != "main" ]; then
  off_main=1
  say "DRIFT — checkout is on '"'"'$branch'"'"', not on branch main."
  # 🔴 NO VERDICT HERE. Whether ship.sh will move this checkout back to main
  # depends on the state of local main, which has not been computed yet: if main
  # is AHEAD, ship.sh skips the host entirely and moves nothing. The advisory is
  # therefore printed at each exit point below, where it can be true.
fi

# --- Where is the LOCAL main branch relative to origin/main? ------------------
# Checked whatever is checked out: local main can be diverged while HEAD sits on
# some feature branch, and that is still a host that will be skipped forever.
if ! git show-ref --verify --quiet refs/heads/main; then
  say "DRIFT — no local main branch exists in this checkout."
  exit 12
fi
main=$(git rev-parse -q --verify refs/heads/main) || {
  say "cannot resolve refs/heads/main"; exit 4; }

counts=$(git rev-list --left-right --count origin/main...main 2>/dev/null) || {
  say "cannot compare main to origin/main"; exit 4; }
behind=$(printf "%s" "$counts" | awk "{print \$1}")
ahead=$(printf "%s" "$counts" | awk "{print \$2}")

if [ "${ahead:-0}" -gt 0 ]; then
  # AHEAD or DIVERGED — the dangerous one. ship.sh cannot fast-forward this host,
  # so it is skipped on EVERY future run and silently stops receiving changes.
  if [ "${behind:-0}" -gt 0 ]; then
    say "🔴 DRIFT — local main has DIVERGED: $ahead un-pushed commit(s), $behind behind."
  else
    say "🔴 DRIFT — local main is AHEAD by $ahead un-pushed commit(s)."
  fi
  say "  ship.sh SKIPS this host (rc=8) and will keep skipping it — it is receiving NOTHING."
  if [ "$off_main" = 1 ]; then
    say "  the checkout is ALSO off main (on '"'"'$branch'"'"') and ship.sh will NOT move it back:"
    say "  it skips this host before touching the checkout at all."
  fi
  git log --oneline --no-decorate origin/main..main 2>/dev/null | head -n 10 | sed "s|^|[$label]     + |"
  say "  rescue (on that host): git branch <topic> main && git push -u origin <topic>"
  say "  then confirm from ANOTHER host, then: git reset --keep origin/main   (never --hard)"
  exit 8
fi

# 🔴 ORDER IS LOAD-BEARING, in BOTH directions.
#   * off-main is checked AFTER the ahead/diverged block, because un-pushed
#     commits on main while HEAD sits on a feature branch are the rc 8 shape —
#     hoisting this above the ahead block reports rc 12 and the FALSE advisory
#     "ship.sh will move it" for a host ship.sh would skip forever.
#     (`test_off_main_with_diverged_main_is_rc8` is the regression pin.)
#   * off-main is checked BEFORE the behind block, because the severity table
#     this file publishes ranks 12 above 10; returning 10 for a host that is BOTH
#     off main and behind would contradict its own ordering.
if [ "$off_main" = 1 ]; then
  if [ "${behind:-0}" -gt 0 ]; then
    say "  local main is also BEHIND by $behind — ship.sh can still fast-forward it and"
    say "  land the checkout back on main; anything committed on '"'"'$branch'"'"' stays invisible to origin/main."
  else
    say "  ship.sh will land the checkout back on main; anything committed on"
    say "  '"'"'$branch'"'"' is invisible to origin/main."
  fi
  exit 12
fi

if [ "${behind:-0}" -gt 0 ]; then
  say "DRIFT — local main is BEHIND origin/main by $behind commit(s) — needs a ship."
  say "  fix: scripts/ship.sh"
  exit 10
fi

say "✅ clean — on branch main, main == origin/main ($target)"
exit 0
'

# ── The per-host HOST-PARITY routine ──────────────────────────────────────────
# 🔴 GIT PARITY IS NOT HOST PARITY. The CHECK above answers "is this host still
# receiving commits?" — and it answered YES, correctly, for the whole period in
# which every skill on the laptop was a dangling symlink into a garbage-collected
# /nix/store path. `git log` matched, `git status` was clean, origin/main was the
# checked-out HEAD, and ~/.claude/skills/*/SKILL.md resolved to nothing. A
# deadman that reports "clean" for that host is the vacuous green this subsystem
# exists to prevent, one level up from the one it was built for.
#
# So this payload reports the differences that MATTER between the two machines
# and are invisible to git:
#
#   1. DANGLING MANAGED SYMLINKS. home-manager deploys by symlinking into
#      /nix/store. A link whose target no longer exists is a file the operator
#      believes is deployed and which is not there. rc 14.
#   2. settings.json TOP-LEVEL KEY SET. Compared across hosts by the driver.
#   3. enabledPlugins, and any plugin ENABLED BUT NOT INSTALLED. rc 15.
#
# 🔴 WHAT "MANAGED" MEANS HERE — STRUCTURAL, NOT SPELLED. A managed link is one
# whose IMMEDIATE target starts with /nix/store/. Nothing hardcodes `skills/`,
# so a new managed subtree is covered the day it is added. Three consequences
# fall out of that definition for free, and each one is a false positive this
# check would otherwise have produced:
#   * ~/.claude/debug/latest points at a SIBLING transcript file and is
#     routinely stale — Claude Code runtime state, not a deployment. Not a store
#     target, so it is not counted. (It is dangling on the workbench right now.)
#   * ~/.claude/skills/clickup/ is a standalone git checkout with ~176 pnpm
#     symlinks under node_modules/, all pointing at RELATIVE paths. Legitimately
#     unmanaged, and excluded by the store-target rule even before pruning.
#   * mkOutOfStoreSymlink links (the `browser` skill) point INTO the store at a
#     path that is itself a symlink to the working tree. First hop is a store
#     path, so they ARE examined — and `[ -e ]` follows the whole chain, so a
#     broken out-of-store link is caught too.
# Directories are pruned for SPEED (never for correctness) when they are named
# node_modules or contain a .git — a nested checkout is by construction not
# home-manager's. Measured on the workbench: 0.43s over ~18500 entries.
#
# 🔴 THE COUNT OF LINKS EXAMINED IS REPORTED ALONGSIDE THE COUNT THAT DANGLED,
# ALWAYS. "0 dangling" from a scan that walked 0 links is indistinguishable from
# a clean host, and is exactly how a scanner wired to nothing reads as a pass.
# The pair is the claim; neither number alone is.
#
# 🔴 NO `find`. The laptop resolves `find` to BUSYBOX, which does not implement
# `-xtype` — and rejects it by printing usage to stderr and EXITING 0. So
# `find -xtype l | wc -l` yields a confident 0 dangling on that host, forever,
# from a check wired to nothing. Measured 2026-08-11. The walk below is bash
# builtins plus `readlink`, which behaves identically under both.
#
# READ-ONLY BY CONSTRUCTION: readlink, sed, sort, tr, head and shell builtins.
# It writes nothing and creates nothing.
#
# Variables that appear inside $(( )) are UPPERCASE on purpose: the test suite
# tokenizes `$(( lower + 1 ))` as a command word in command position, and an
# uppercase name is dropped by that filter instead of needing a ledger entry
# declaring an arithmetic operand to be "prose".
PARITY='
set -uo pipefail
label="${DRIFT_LABEL:-host}"
maxd="${DRIFT_DANGLING_MAX:-10}"
psay() { echo "[$label] $*"; }

# What makes a symlink MANAGED. The default is the only correct value in
# production — home-manager deploys by pointing into the nix store, and
# test_the_managed_prefix_defaults_to_the_nix_store pins it. It is a variable
# solely so the suite can build a fixture tree it fully controls: a HEALTHY
# managed link needs a target that both matches the prefix AND exists, which no
# test can arrange under a real /nix/store. Deliberately NOT forwarded to the
# remote host — production always uses the default there, and every value this
# script sends over ssh is a value that has to be proved safe.
mprefix="${DRIFT_MANAGED_PREFIX:-/nix/store/}"

P_EXAMINED=0
P_DANGLED=0
p_list=""

# Loop and local variable names are UPPERCASE throughout this payload for the
# same reason the arithmetic operands are: the suite tokenizes `for x in …` and
# `local v` with `x`/`v` in command position, and an uppercase name is dropped by
# its `[a-z]…` filter. The alternative is a ledger entry per variable declaring
# it "prose", which would be widening an accounting guard to fit new code.
p_walk() { # p_walk <dir> — recurse, counting managed symlinks and dead ones
  local E BASE T
  for E in "$1"/* "$1"/.*; do
    BASE="${E##*/}"
    [ "$BASE" = "." ] && continue
    [ "$BASE" = ".." ] && continue
    [ -e "$E" ] || [ -L "$E" ] || continue
    if [ -L "$E" ]; then
      T="$(readlink "$E")"
      case "$T" in
        "$mprefix"*)
          P_EXAMINED=$(( P_EXAMINED + 1 ))
          if [ ! -e "$E" ]; then
            P_DANGLED=$(( P_DANGLED + 1 ))
            p_list="$p_list$E -> $T
"
          fi
          ;;
      esac
      continue
    fi
    [ -d "$E" ] || continue
    [ "$BASE" = "node_modules" ] && continue
    [ -e "$E/.git" ] && continue
    p_walk "$E"
  done
}

# Roots are relative to $HOME so the whole payload is exercisable against a
# fixture home in the test suite without a single $HOME-conditional skip.
roots="${DRIFT_PARITY_ROOTS:-.claude .config/opencode}"
P_ROOTS_SEEN=0
for R in $roots; do
  if [ -d "$HOME/$R" ]; then
    P_ROOTS_SEEN=$(( P_ROOTS_SEEN + 1 ))
    p_walk "$HOME/$R"
  fi
done

p_rc=0
if [ "$P_ROOTS_SEEN" = 0 ]; then
  psay "managed symlinks: NOT EVALUATED — none of the roots exist ($roots)"
  psay "  a scan that examined nothing is not a clean scan; it is no scan."
else
  psay "managed symlinks: examined=$P_EXAMINED dangling=$P_DANGLED (roots: $roots)"
fi
if [ "$P_DANGLED" -gt 0 ]; then
  psay "🔴 DRIFT — $P_DANGLED of $P_EXAMINED managed symlink(s) point at a path that does not exist."
  psay "  home-manager believes these are deployed. They resolve to nothing."
  printf "%s" "$p_list" | head -n "$maxd" | sed "s|^|[$label]     x |"
  if [ "$P_DANGLED" -gt "$maxd" ]; then
    psay "    ... and $(( P_DANGLED - maxd )) more"
  fi
  psay "  fix (on that host): home-manager switch --flake ~/workspace/devrc --impure"
  p_rc=14
fi

# --- settings.json: KEY NAMES ONLY --------------------------------------------
# 🔴 Never the values. This file holds tokens, hook command lines and permission
# rules, and this output goes to a systemd journal.
#
# 🔴 THE EXTRACTOR HAS A FORMAT DEPENDENCY, AND IT FAILS LOUD. Top-level keys are
# read as the 2-space-indented lines Claude Code writes. Cross-checked against
# json.load on the real 14 KB workbench file (2026-08-11): identical 11-key set.
# If the file is ever minified the extractor yields NOTHING — and an empty result
# is reported as UNEVALUATED, never as "no divergence", because those two are the
# same observation to a diff and only one of them is good news.
# 🔴 EACH EXTRACTION IS SPLIT FROM ITS NORMALISATION, ON PURPOSE. The obvious
# one-liner `x="$(sed … | sort | tr …)"` hides `sort` and `tr` from the reverse
# PATH guard in the suite: that tokenizer does not honour a backslash-escaped quote
# inside double quotes, so the whole pipeline collapses into one `sed` segment
# and the two commands after the pipes are never seen. The guard then passes
# while the unit PATH goes unchecked for them — a guard that cannot see a
# command is not accounting for it. Extract, then normalise on its own line,
# where every command word sits in plain command position.
#
# The sed scripts are SINGLE-quoted (the same quote-dance CHECK already uses)
# rather than double-quoted with a backslash-escaped quote, for the same reason:
# with `\"` the tokenizer ends the quoted run early and the brace in the address
# reads as a command separator, leaving the trailing `/p` looking like a command.
norm_set() { # norm_set <newline-list> -> sorted, space-separated, or "" if empty
  [ -n "$1" ] || return 0
  printf "%s\n" "$1" | sort | tr "\n" " "
}

set_file="$HOME/.claude/settings.json"
skeys="UNEVALUATED"
eplug="UNEVALUATED"
if [ -r "$set_file" ]; then
  k="$(sed -n '"'"'s/^  "\([^"]*\)":.*/\1/p'"'"' "$set_file")"
  if [ -n "$k" ]; then
    skeys="$(norm_set "$k")"
    # enabledPlugins may legitimately be absent — that is a FACT, not a failure.
    if [ -n "$(sed -n '"'"'/^  "enabledPlugins":/p'"'"' "$set_file")" ]; then
      eplist="$(sed -n '"'"'/^  "enabledPlugins": {/,/^  }/p'"'"' "$set_file")"
      eplist="$(printf "%s\n" "$eplist" | sed -n '"'"'s/^    "\([^"]*\)":.*/\1/p'"'"')"
      eplug="$(norm_set "$eplist")"
      [ -n "$eplug" ] || eplug="NONE"
    else
      eplug="NONE"
    fi
  else
    psay "settings.json: NOT EVALUATED — no 2-space top-level keys found in $set_file"
  fi
else
  psay "settings.json: NOT EVALUATED — $set_file is missing or unreadable"
fi

inst_file="$HOME/.claude/plugins/installed_plugins.json"
iplug="UNEVALUATED"
if [ -r "$inst_file" ]; then
  iplist="$(sed -n '"'"'/^  "plugins": {/,$p'"'"' "$inst_file")"
  iplist="$(printf "%s\n" "$iplist" | sed -n '"'"'s/^    "\([^"]*\)":.*/\1/p'"'"')"
  iplug="$(norm_set "$iplist")"
  [ -n "$iplug" ] || iplug="NONE"
fi

# --- enabled but NOT installed (per-host; needs no second host) ---------------
if [ "$eplug" != UNEVALUATED ] && [ "$eplug" != NONE ] && [ "$iplug" != UNEVALUATED ]; then
  ghost=""
  for PL in $eplug; do
    case " $iplug " in
      *" $PL "*) ;;
      *) ghost="$ghost $PL" ;;
    esac
  done
  if [ -n "$ghost" ]; then
    psay "🔴 DRIFT — plugin(s) ENABLED in settings.json but NOT installed:$ghost"
    psay "  fix (on that host): claude plugin install <plugin>"
    [ "$p_rc" = 0 ] && p_rc=15
  fi
fi

# --- skillOverrides: the SKILL-LISTING TIER LEDGER, AS DEPLOYED ---------------
# 🔴 Reported as a FACT only. The verdict is NOT a property of this host — it is
# the difference between this host and a ledger that lives in a repo, so it is
# computed in the driver (see "SKILL-LISTING TIERS" below) where the ledger can
# be read by its own parser instead of by a second sed.
#
# Three states, and they must never print the same way:
#   NONE         the key is absent — the ledger has never been APPLIED here.
#                Not drift: this is the state the mechanism shipped in.
#   EMPTY        the key exists and holds nothing.
#   UNEVALUATED  settings.json unreadable, or the extractor matched nothing.
#
# 🔴 THE RANGE END IS `^  [}"]`, NOT `^  }`. A minified-away or one-line
# `"skillOverrides": {},` still matches the range START (the pattern is a
# substring match), and with `^  }` as the end the range would run on through
# every following key and harvest their 4-space entries as overrides. Ending at
# the next TOP-LEVEL line — a closing brace or the next key — bounds it either
# way, and an empty harvest reports EMPTY rather than a clean-looking NONE.
#
# Values are printed, unlike the key-name-only rule above: an override value is
# one of `on` / `name-only` / `user-invocable-only` / `off`, which is an enum,
# not a secret. Nothing here reads a permission rule, a hook command or a token.
sover="UNEVALUATED"
if [ -r "$set_file" ]; then
  if [ -n "$(sed -n '"'"'/^  "skillOverrides":/p'"'"' "$set_file")" ]; then
    solist="$(sed -n '"'"'/^  "skillOverrides": {/,/^  [}"]/p'"'"' "$set_file")"
    solist="$(printf "%s\n" "$solist" | sed -n '"'"'s/^    "\([^"]*\)": *"\([^"]*\)".*/\1=\2/p'"'"')"
    sover="$(norm_set "$solist")"
    [ -n "$sover" ] || sover="EMPTY"
  else
    sover="NONE"
  fi
fi

# FACT lines are the machine-readable half — the driver diffs them ACROSS hosts,
# which is the only place a key-set difference can be seen at all.
echo "[$label] FACT settings-keys $skeys"
echo "[$label] FACT enabled-plugins $eplug"
echo "[$label] FACT installed-plugins $iplug"
echo "[$label] FACT skill-overrides $sover"
echo "[$label] PARITY-RC=$p_rc"
'

# ── The per-host SOURCE-REPO routine ──────────────────────────────────────────
# 🔴 See "SOURCE-REPO PARITY (rc 17)" in the header for what this measures, what
# counts as drift and what deliberately does not. Here: how it is derived, and
# why it cannot be a list.
#
# devrc builds packages from LOCAL WORKING TREES of other repos — a `src` of
# `${workspace}/<repo>/…` in nix/pkgs. Nothing converges those repos, so a host
# with a perfect devrc checkout can still compile months-old code. The covered
# set is read out of nix/pkgs ON THE HOST BEING EXAMINED rather than passed in
# from here, for two reasons: a hardcoded pair goes stale the moment a third
# package is added (the same shape as the bug), and deriving it locally means
# nothing derived has to be interpolated into a payload that executes on the
# other machine.
#
# 🔴 TWO UNITS, AND CONFLATING THEM MAKES THIS GATE PERMANENTLY RED.
#   * a REPO is what gets FETCHED — one fetch, however many packages sit in it;
#   * a SCOPE (`<repo>:<srcDir-subtree>`) is what gets JUDGED.
# The scan therefore keeps the WHOLE `${workspace}/…` path, not just its first
# component: `${workspace}/homelab-talos/containers/clawgate` is repo
# `homelab-talos` with subtree `containers/clawgate`, while
# `${workspace}/tmux-fuzzyclaw` is a repo whose srcDir IS its root — an empty
# subtree, for which scope and repo coincide and behaviour is unchanged. One repo
# may carry SEVERAL scopes and each is judged on its own.
#
# 🔴 NO `find`, for the same reason the parity walk avoids it: the laptop resolves
# `find` to BUSYBOX, which does not implement `-xtype`, rejects it by printing
# usage to stderr and EXITS 0 — a confident zero from a scan wired to nothing.
# `shopt -s globstar` plus a glob is bash's own, and behaves identically on both.
#
# 🔴 COMMENTS ARE EXCLUDED BY TRUNCATION — each line is cut at its first `#`
# before anything is read out of it, which covers a whole-line comment and a
# trailing one with the same rule. clawgatectl.nix's own header discusses
# `~/workspace/homelab-talos` in prose repeatedly and this very file documents the
# `${workspace}/` pattern it is looking for, so a whole-file grep would "find"
# sources in the documentation. What survives is scanned for EVERY occurrence, not
# just the first: one line may legitimately name two sources. The known bound,
# stated rather than engineered away: a `#` inside a nix STRING would truncate
# early. That direction UNDER-reports, and under-reporting is what the two-way
# pin against the real nix/pkgs exists to catch.
#
# Loop and local variable names are UPPERCASE for the same reason they are in the
# parity payload: the suite's reverse-PATH tokenizer reads a lowercase word in
# command position (including inside `$(( … ))`) as a command, and an uppercase
# one is dropped by its filter instead of needing a "this is prose" ledger entry.
#
# READ-ONLY BY CONSTRUCTION: git fetch / rev-parse / symbolic-ref / rev-list /
# status, plus printf, wc, tr, sed, head, awk and shell builtins. It writes
# nothing and creates nothing.
SRCREPO='
set -uo pipefail
label="${DRIFT_LABEL:-host}"
if [ "${DRIFT_REPO+set}" = set ] && [ -z "$DRIFT_REPO" ]; then
  echo "[$label] DRIFT_REPO is SET but EMPTY — refusing to fall back to \$HOME/workspace/devrc." >&2
  exit 2
fi
repo="${DRIFT_REPO:-$HOME/workspace/devrc}"
sfto="${DRIFT_SRC_FETCH_TIMEOUT:-30}"
ssay() { echo "[$label] $*"; }

# S_NAMES — deduped REPO roots, the unit that gets FETCHED.
# S_SUBS  — deduped "<repo>:<subtree>" SCOPES, the unit that gets JUDGED. An
#           empty subtree means that package srcDir IS the repo root.
S_NAMES=""
S_SUBS=""

s_add() { # s_add <repo> <subtree-or-empty>
  case " $S_NAMES " in
    *" $1 "*) ;;
    *) S_NAMES="$S_NAMES $1" ;;
  esac
  case " $S_SUBS " in
    *" $1:$2 "*) return 0 ;;
  esac
  S_SUBS="$S_SUBS $1:$2"
}

s_key() { # s_key <repo> <subtree-or-empty> — the scope name used in every message
  if [ -z "$2" ]; then printf "%s" "$1"; else printf "%s/%s" "$1" "$2"; fi
}

s_scan() { # s_scan <nix-file> — collect the workspace-relative srcDir paths it names
  local LN REST PP NAME SUB
  while IFS= read -r LN || [ -n "$LN" ]; do
    LN="${LN%%#*}"
    while : ; do
      case "$LN" in *\$\{workspace\}/*) ;; *) break ;; esac
      REST="${LN#*\$\{workspace\}/}"
      PP="${REST%%[!A-Za-z0-9._/-]*}"
      PP="${PP%/}"
      NAME="${PP%%/*}"
      case "$PP" in
        */*) SUB="${PP#*/}" ;;
        *)   SUB="" ;;
      esac
      [ -n "$NAME" ] && s_add "$NAME" "$SUB"
      LN="$REST"
    done
  done < "$1"
}

shopt -s nullglob globstar
S_FILES=0
for F in "$repo"/nix/pkgs/**/*.nix; do
  [ -f "$F" ] || continue
  S_FILES=$(( S_FILES + 1 ))
  s_scan "$F"
done

S_REPOS=0
S_EXAMINED=0
S_STALE=0
S_UNMEASURED=0
S_FACTS=""
# The SECOND fact line: "<scope>=<REASON>" for every scope this run could not
# evaluate. It exists because the src-repos facts CANNOT answer the question rc
# 18 asks — a scope that reaches the pathspec comparison and fails there has
# already emitted a real tree OID as its fact, so "is this value a reason token?"
# would read that one as measured. The reason belongs beside the scope, once,
# from the payload that knows.
S_UNMEAS_FACTS=""
s_rc=0

# 🔴 A repo we could not evaluate makes EVERY scope under it UNMEASURED — never
# one silent pass hiding behind a repo whose fetch failed. The reason token also
# becomes that scope FACT value, so the cross-host comparison cannot mistake
# "we could not look" for agreement.
s_unmeasured_scopes() { # s_unmeasured_scopes <repo> <reason-token>
  local X SUBP K
  for X in $S_SUBS; do
    case "$X" in
      "$1:"*) ;;
      *) continue ;;
    esac
    SUBP="${X#*:}"
    K="$(s_key "$1" "$SUBP")"
    S_EXAMINED=$(( S_EXAMINED + 1 ))
    S_UNMEASURED=$(( S_UNMEASURED + 1 ))
    S_FACTS="$S_FACTS $K=$2"
    S_UNMEAS_FACTS="$S_UNMEAS_FACTS $K=$2"
  done
}

for N in $S_NAMES; do
  SR="$HOME/workspace/$N"
  S_REPOS=$(( S_REPOS + 1 ))

  # A worktree checkout has .git as a FILE, a normal clone as a directory.
  if [ ! -e "$SR/.git" ]; then
    ssay "source repo $N: ABSENT at $SR — currency NOT evaluated."
    ssay "  nix/pkgs builds from it; this host cannot. Reported, NOT drift: the"
    ssay "  derivations guard on pathExists and simply omit the binary."
    s_unmeasured_scopes "$N" ABSENT
    continue
  fi

  SHA="$(git -C "$SR" rev-parse --short=12 HEAD 2>/dev/null)"
  [ -n "$SHA" ] || SHA=UNBORN
  BR="$(git -C "$SR" symbolic-ref --quiet --short HEAD 2>/dev/null || echo DETACHED)"

  # DIRTY is reported for every present repo, INCLUDING a current one: these
  # derivations copy the working TREE, so an uncommitted edit or an untracked
  # .go file is in the binary while `git log` says nothing happened.
  ST="$(git -C "$SR" status --porcelain 2>/dev/null)"
  S_DIRTY=0
  [ -n "$ST" ] && S_DIRTY="$(printf "%s\n" "$ST" | wc -l | tr -d " ")"
  if [ "$S_DIRTY" != 0 ]; then
    ssay "source repo $N: DIRTY — $S_DIRTY path(s) modified or untracked."
    ssay "  the build reads this TREE, not the commit, so those paths are IN the"
    ssay "  binary. Reported, never drift on its own."
  fi

  FERR="$(timeout "$sfto" git -C "$SR" fetch --quiet origin 2>&1)"
  frc=$?
  if [ "$frc" != 0 ]; then
    ssay "source repo $N: on branch $BR at $SHA — git fetch FAILED (rc=$frc), currency NOT evaluated."
    printf "%s\n" "$FERR" | head -n 3 | sed "s|^|[$label]   git: |"
    ssay "  these repos are private and reached over ssh, and a systemd --user"
    ssay "  unit has no ssh-agent. NOT drift — and NOT a pass either."
    s_unmeasured_scopes "$N" FETCHFAILED
    continue
  fi

  # 🔴 The comparison is against the OWN upstream of whatever branch is checked
  # out, never a hardcoded `main`. These repos do not agree on a default branch
  # — homelab-talos works on `trunk` — and assuming one would be a guess printed
  # as a measurement.
  UP=""
  if [ "$BR" != DETACHED ]; then
    UP="$(git -C "$SR" rev-parse -q --verify --symbolic-full-name "$BR@{upstream}" 2>/dev/null)"
  fi
  if [ -z "$UP" ]; then
    ssay "source repo $N: on branch $BR at $SHA — no upstream to compare against, currency NOT evaluated."
    ssay "  a detached HEAD or an untracked branch has no defined right answer."
    s_unmeasured_scopes "$N" NOUPSTREAM
    continue
  fi

  # 🔴 REPO-WIDE COUNTS ARE INFORMATION, NEVER THE VERDICT. They are true and
  # worth printing — but escalating on them made rc 17 fire on commits that
  # cannot reach any built artefact, which is a permanently-red gate.
  CNT="$(git -C "$SR" rev-list --left-right --count "$UP...HEAD" 2>/dev/null)"
  R_BEHIND="$(printf "%s" "$CNT" | awk "{print \$1}")"
  R_AHEAD="$(printf "%s" "$CNT" | awk "{print \$2}")"
  case "$R_BEHIND" in ""|*[!0-9]*) R_BEHIND=-1 ;; esac
  case "$R_AHEAD" in ""|*[!0-9]*) R_AHEAD=-1 ;; esac
  ssay "source repo $N: on branch $BR at $SHA — repo-wide $R_BEHIND behind / $R_AHEAD ahead of $UP."
  ssay "  repo-wide is INFORMATION ONLY. Only the built-source scope(s) below set the verdict."

  for X in $S_SUBS; do
    case "$X" in
      "$N:"*) ;;
      *) continue ;;
    esac
    SUBP="${X#*:}"
    K="$(s_key "$N" "$SUBP")"
    S_EXAMINED=$(( S_EXAMINED + 1 ))

    # The subtree TREE OID — what the two hosts are compared on, because it is
    # what the derivation actually copies. A repo HEAD would report a difference
    # for any commit anywhere in the repo.
    TREE="$(git -C "$SR" rev-parse --short=12 "HEAD:$SUBP" 2>/dev/null)"
    [ -n "$TREE" ] || TREE=NOSUBTREE
    S_FACTS="$S_FACTS $K=$TREE"

    if [ -z "$SUBP" ]; then
      SB="$R_BEHIND"
      SA="$R_AHEAD"
    else
      SB="$(git -C "$SR" rev-list --count "HEAD..$UP" -- "$SUBP" 2>/dev/null)"
      SA="$(git -C "$SR" rev-list --count "$UP..HEAD" -- "$SUBP" 2>/dev/null)"
    fi
    case "$SB" in ""|*[!0-9]*) SB=-1 ;; esac
    case "$SA" in ""|*[!0-9]*) SA=-1 ;; esac
    if [ "$SB" = -1 ] || [ "$SA" = -1 ]; then
      ssay "  BUILT SOURCE $K: could not compare $BR to $UP over that path — currency NOT evaluated."
      S_UNMEASURED=$(( S_UNMEASURED + 1 ))
      # NOCOUNT, not the tree OID already in S_FACTS: this scope WAS reached and
      # does have a tree, and only this line records that its currency was never
      # decided. Without it the rc 18 ladder would read the OID and call it
      # measured — a scope that can never be judged, wearing a measurement.
      S_UNMEAS_FACTS="$S_UNMEAS_FACTS $K=NOCOUNT"
      continue
    fi

    if [ "$SB" -gt 0 ] || [ "$SA" -gt 0 ]; then
      S_STALE=$(( S_STALE + 1 ))
      ssay "🔴 DRIFT — BUILT SOURCE $K is NOT current: $SB behind / $SA ahead of $UP (repo-wide $R_BEHIND behind / $R_AHEAD ahead)."
      ssay "  nix/pkgs builds a package from THIS SUBTREE. Whatever version string"
      ssay "  that package carries, the code in the binary is the code sitting here."
      ssay "  fix (on that host): git -C $SR pull --ff-only   then a home-manager switch"
      s_rc=17
    elif [ "$R_BEHIND" -gt 0 ] || [ "$R_AHEAD" -gt 0 ]; then
      ssay "  BUILT SOURCE $K is CURRENT ($SB behind / $SA ahead) — the repo-wide $R_BEHIND behind /"
      ssay "  $R_AHEAD ahead touch nothing this package is built from. Information, NOT drift."
    else
      ssay "  BUILT SOURCE $K is CURRENT — $BR == $UP at $SHA."
    fi
  done
done

# 🔴 EXAMINED BESIDE STALE, and UNMEASURED beside both. A bare "0 stale" from a
# scan that walked no scopes, or one whose every fetch failed, is exactly what a
# checker wired to nothing prints. The unit counted here is the BUILT-SOURCE
# SCOPE, not the repo — the repo count is printed beside it.
if [ "$S_EXAMINED" = 0 ]; then
  ssay "source repos: NOT EVALUATED — nix/pkgs under $repo names no \${workspace}/ source ($S_FILES nix file(s) scanned)."
  ssay "  a scan that examined nothing is not a clean scan; it is no scan."
else
  ssay "source repos: examined=$S_EXAMINED stale=$S_STALE unmeasured=$S_UNMEASURED built-source scope(s) over $S_REPOS repo(s) ($S_FILES nix file(s) scanned)"
fi

echo "[$label] FACT src-repos$S_FACTS"
# 🔴 THE DENOMINATOR IS PART OF THIS LINE, not something the driver may infer.
# `examined=` is emitted unconditionally, INCLUDING as `examined=0`, so the
# driver can tell three states apart that would otherwise collapse into one:
# "this host never ran the payload" (no line at all), "it ran and found no
# scopes" (examined=0) and "it ran and every scope measured" (examined=N with no
# pairs after it). Read off the src-repos line those last two are both an empty
# value, which is exactly the reassuring zero the rest of this file refuses.
echo "[$label] FACT src-unmeasured examined=$S_EXAMINED$S_UNMEAS_FACTS"
echo "[$label] SRC-RC=$s_rc"
'
# The payload actually shipped to each host: the git CHECK in a SUBSHELL (so its
# many `exit`s end the subshell and not the run) followed by the parity scan and
# the source-repo scan.
# Composed rather than run as two ssh legs so an unreachable host is still ONE
# missed connection and ONE bump of the streak counter.
#
# The exit status is the GIT verdict, byte-for-byte what it always was — every
# pinned rc in the suite is a statement about that number. The parity and
# source-repo verdicts ride back on the PARITY-RC= and SRC-RC= lines and are
# folded in by the driver through the same severity() table, so there is exactly
# one severity ranking in this file.
PAYLOAD="(
$CHECK
)
_drift_git_rc=\$?
$PARITY
$SRCREPO
exit \$_drift_git_rc"

rc=0
note_rc() { # note_rc <rc> — keep the MOST SEVERE code seen (see header)
  local new="$1"
  [ "$new" = 0 ] && return 0
  if [ "$(severity "$new")" -gt "$(severity "$rc")" ]; then rc="$new"; fi
}

# --- What did this run actually LOOK at? --------------------------------------
# Tracked so the summary can never again claim "both hosts" for a run that
# checked one, or none.
CHECKED=""
UNCHECKED=""
mark_checked()   { CHECKED="${CHECKED:+$CHECKED, }$1"; }
mark_unchecked() { UNCHECKED="${UNCHECKED:+$UNCHECKED, }$1"; }

# --- Reading the parity facts back off a host's output ------------------------
# Each host's payload prints its own verdict AND a few `FACT <name> <values>`
# lines. The per-host verdict (dangling links, enabled-but-absent plugins) needs
# only that host. A KEY-SET or enabledPlugins DIFFERENCE is not a property of
# either host alone — it exists only between them — so it is computed here, from
# both outputs, and only when both were actually obtained.
LOCAL_OUT=""
REMOTE_OUT=""

fact_of() { # fact_of <host-output> <fact-name> -> the value list, or "" if absent
  printf '%s\n' "$1" | sed -n "s/^\[[^]]*\] FACT $2 //p" | head -n 1
}

parity_rc_of() { # parity_rc_of <host-output> -> that host's parity rc (0 if none)
  # A host that printed no PARITY-RC line (an old drift-check.sh on the far side,
  # a truncated stream) yields 0 — deliberately: an ABSENT verdict must not
  # invent a failure. The absence is still visible, because the FACT lines are
  # missing too and the cross-host block then reports NOT COMPARED.
  local V
  V="$(printf '%s\n' "$1" | sed -n 's/^\[[^]]*\] PARITY-RC=//p' | head -n 1)"
  case "$V" in ''|*[!0-9]*) echo 0 ;; *) echo "$V" ;; esac
}

src_rc_of() { # src_rc_of <host-output> -> that host's source-repo rc (0 if none)
  # Same contract as parity_rc_of, for the same reason: an ABSENT verdict (an
  # older drift-check.sh on the far side, a truncated stream) must not invent a
  # failure. The absence stays visible because the FACT line is missing too, and
  # the cross-host block then reports NOT COMPARED.
  local V
  V="$(printf '%s\n' "$1" | sed -n 's/^\[[^]]*\] SRC-RC=//p' | head -n 1)"
  case "$V" in ''|*[!0-9]*) echo 0 ;; *) echo "$V" ;; esac
}

src_names_of() { # src_names_of <name=head list> -> just the names
  local X OUT=""
  for X in $1; do OUT="$OUT ${X%%=*}"; done
  printf '%s' "$OUT"
}

src_head_of() { # src_head_of <name=head list> <name> -> that repo's HEAD token
  local X
  for X in $1; do
    case "$X" in
      "$2="*) printf '%s' "${X#*=}"; return 0 ;;
    esac
  done
  printf ''
}

only_in() { # only_in <set-a> <set-b> -> members of a absent from b
  local X OUT=""
  for X in $1; do
    case " $2 " in
      *" $X "*) ;;
      *) OUT="$OUT $X" ;;
    esac
  done
  printf '%s' "$OUT"
}

# ── PER-HOST settings.json KEYS ───────────────────────────────────────────────
# 🔴 WHY THIS EXISTS, AND WHY IT IS DELIBERATELY TINY. `~/.claude/settings.json`
# is per-host and UNMANAGED BY DESIGN — nix/home.nix says so twice, in the
# comments beside the dropStaleClaudeHooks block and beside the mutable-exception
# list ("settings.json is per-host/unmanaged"). A check that demands identical
# top-level key sets across two machines therefore has a small set of keys it can
# NEVER go green on, and the deadman's first autonomous run (2026-08-11, six
# hours after #406 armed the timer) failed on exactly those. A permanently-red
# gate is worse than no gate: it teaches the operator to click through the one
# alert that has to keep its meaning. Scoping the comparison is what protects it.
#
# 🔴 IT IS AN ENUMERATION, NOT A PATTERN, AND IT FAILS CLOSED. Every exempt key
# is a literal `case` arm carrying its own reason. A key nobody has thought about
# falls to the `*)` arm, gets NO reason, and is therefore NOT exempt — so a
# future key, or a key renamed by an upstream Claude Code release, is drift by
# default and has to be argued onto this list by a human. A prefix rule, a glob,
# or "anything the operator set by hand" would each silently swallow the next
# real divergence, which is the one failure mode that disarms the deadman
# completely (`test_the_allowlist_is_not_a_wildcard`).
#
# 🔴 NOT ENV-OVERRIDABLE, unlike every other tunable in this file. An
# `DRIFT_PERHOST_KEYS` variable would let a unit file, a shell profile or a
# stray export widen this to everything from outside review, and the resulting
# green would be indistinguishable from a real pass. The suite pins the absence
# (`test_the_allowlist_cannot_be_widened_from_the_environment`).
#
# 🔴 EXEMPT IS NOT INVISIBLE. Whichever branch the comparison takes, every key
# silenced here is PRINTED with its reason. A difference that is tolerated and a
# difference that was never looked at must not read the same way, one level down
# from the NOT COMPARED rule above.
#
# WHAT IS NOT ON THIS LIST, ON PURPOSE:
#   * `permissions` — NOT preference. The laptop having no permissions block at
#     all means it prompts for operations the workbench allows; that is a real
#     gap with a real fix (scripts/sync-claude-permissions.sh), not a per-host
#     taste. It stays in the comparison, and the check stays red until the fix is
#     applied on the laptop — which is a deadman working, not a deadman stuck.
#   * `hooks`, `statusLine`, `enabledPlugins`, `env`, `model` — behaviour, not
#     preference. A host quietly losing a hook is precisely what this check is
#     for.
#
# Written as `if [ "$1" = <key> ]` rather than a `case` because the suite's
# reverse PATH tokenizer reads a lowercase `case` ARM LABEL as a command word —
# that is why `workbench` and `laptop` already sit in its prose ledger. Putting
# each key in ARGUMENT position keeps the guard's accounting honest instead of
# widening its ledger to fit new prose.
perhost_reason() { # perhost_reason <key> -> why it may differ, or "" if it may NOT
  if [ "$1" = effortLevel ]; then
    echo "reasoning-effort preference; set per machine and never shipped by nix"
  elif [ "$1" = theme ]; then
    echo "terminal colour theme; the two hosts run different displays/terminals"
  elif [ "$1" = voice ]; then
    echo "TTS voice id for voice mode; a per-machine audio preference"
  else
    echo ""
  fi
}

perhost_split() { # perhost_split <KEEP|DROP> <key-list> -> the allowlisted / the rest
  local K WHY OUT=""
  for K in $2; do
    WHY="$(perhost_reason "$K")"
    if [ "$1" = KEEP ] && [ -n "$WHY" ]; then OUT="$OUT $K"; fi
    if [ "$1" = DROP ] && [ -z "$WHY" ]; then OUT="$OUT $K"; fi
  done
  printf '%s' "$OUT"
}

say_perhost() { # say_perhost <role> <key-list> — name each exempt key AND its reason
  local K WHY
  for K in $2; do
    WHY="$(perhost_reason "$K")"
    echo "[parity]   only on $1: $K — per-host by design: $WHY"
  done
}

# --- Consecutive-unreachable streak (see "UNREACHABLE IS NOT DRIFT" above) -----
# The ONLY file this script writes, and it lives outside every repo.
#
# 🔴 KNOWN AND ACCEPTED BOUNDS — documented rather than engineered away, because
# both cost more to fix than they cost to have, and both err in the SAFE
# direction (they delay an escalation; neither can invent one):
#
#  1. NOT ATOMIC. `streak_bump` is a read-modify-write with no lock, so two runs
#     overlapping in the same instant can both read N and both write N+1,
#     losing a miss. Measured on this host: 20 concurrent bumps landed 10; an
#     earlier round measured 9 on the same code, i.e. the loss is real and
#     non-deterministic, and the DIRECTION is what matters, not the number.
#     It is only
#     reachable when an operator hand-runs the script at the same moment the
#     timer fires (the timer itself is a single serialised oneshot at a 6h
#     cadence), and the effect is UNDERCOUNTING — escalation arrives later,
#     never earlier, so it cannot produce a false alarm. An atomic
#     write-temp-then-`mv` would need `mv`, which the passivity scanner
#     correctly classes as destructive, and a lock would need `flock`, which is
#     one of the wrapper shapes the scanner now recurses through; both trade a
#     real hardening of this file for a bound that only bites a human racing a
#     6-hourly timer.
#
#  2. A HAND-RUN SHARES THE COUNTER WITH THE TIMER. There is one file per remote
#     role, not one per invocation, so `scripts/drift-check.sh` typed at a
#     prompt bumps or resets the same streak the unit is keeping. Two
#     consequences worth knowing before you read an alert: hand-running while
#     the laptop is OFF pushes the streak up and can trip rc 13 sooner than the
#     6h cadence implies, and hand-running while it is ON resets a genuine
#     streak the timer had accumulated. That is deliberate — the streak is a
#     property of "how long has this host been unreachable", not of who asked —
#     but it does mean a hand-run is not a read-only observation of the ladder.
_streak_file() { printf '%s\n' "$DRIFT_STATE_DIR/unreachable-${1:-remote}"; }

streak_bump() { # streak_bump <role> -> new streak, or -1 if it cannot be persisted
  local f prev next
  f="$(_streak_file "$1")"
  mkdir -p "$DRIFT_STATE_DIR" 2>/dev/null || { echo -1; return 0; }
  prev="$(cat "$f" 2>/dev/null || true)"
  case "$prev" in ''|*[!0-9]*) prev=0 ;; esac
  next=$(( prev + 1 ))
  # 🔴 `2>/dev/null` FIRST, then the target. Redirections are applied left to
  # right, and the shell reports a FAILED redirection on whatever fd 2 is at
  # that moment: written the other way round (`> "$f" 2>/dev/null`) an
  # unwritable state dir leaks a raw `drift-check.sh: line NNN: …: Permission
  # denied` into the journal, unprefixed, between the `[host]`-prefixed lines.
  # The fail-closed limb still fires either way; this only fixes the noise.
  printf '%s\n' "$next" 2>/dev/null > "$f" || { echo -1; return 0; }
  echo "$next"
}

streak_reset() { # streak_reset <role> — the host answered; the run of misses ends
  local f
  f="$(_streak_file "$1")"
  [ -d "$DRIFT_STATE_DIR" ] || return 0
  printf '0\n' 2>/dev/null > "$f" || true
}

# --- Consecutive-UNMEASURED streaks, per (host, built-source scope) -----------
# The rc 18 ladder. Same shape as the three functions above and deliberately so —
# see "UNMEASURED IS NOT FOREVER" in the header for what it measures, why each
# reason gets the threshold it gets, and the two bounds it inherits (a bump is a
# non-atomic read-modify-write, and a hand-run shares the counter with the timer).
#
# Local variable names are UPPERCASE apart from `f`, for the same reason the
# embedded payloads use uppercase throughout: the suite's reverse-PATH tokenizer
# reads a lowercase word in command position — including inside `$(( … ))` — as a
# command, and would need a ledger entry per operand declaring it prose. `f` is
# lowercase on purpose: it is the redirection target the write-ledger asserts, and
# that ledger is the pin that this script writes nothing else.

u_threshold() { # u_threshold <reason-token> -> consecutive-run threshold, or NEVER
  # 🔴 AN ENUMERATION THAT FAILS CLOSED, like perhost_reason below. ABSENT is the
  # only exemption and it is spelled out; everything else — including a reason
  # token added later and not thought about here — lands on the STRUCTURAL
  # ladder rather than silently becoming exempt. Written as `if [ "$1" = X ]`
  # rather than a `case` for the reason perhost_reason gives: it keeps every
  # label in ARGUMENT position, where the suite's tokenizer cannot mistake it
  # for a command word.
  if [ "$1" = ABSENT ]; then
    # A host that simply lacks the checkout is documented and tolerated: the
    # derivations guard on pathExists and omit the binary. Escalating here would
    # make that host permanently red for a package it correctly does not ship.
    echo NEVER
  elif [ "$1" = FETCHFAILED ]; then
    # The one reason with a plausibly transient cause (no ssh-agent under a user
    # unit, key rotation, a remote that is down), so it gets a longer ladder —
    # but it does get one: a currency check that can never fetch is wired to
    # nothing, and that is the whole finding.
    echo "$DRIFT_UNMEASURED_FETCH_ESCALATE"
  else
    # NOUPSTREAM and NOCOUNT: structural, they do not heal on their own.
    echo "$DRIFT_UNMEASURED_ESCALATE"
  fi
}

_streak_key() { # _streak_key <scope> -> the filename-safe, INJECTIVE encoding
  # 🔴 INJECTIVE, not merely "sanitised". Every scope this script keys on comes
  # from a scan whose alphabet is [A-Za-z0-9._/-], so doubling `_` and then
  # mapping `/` to `_` is reversible over that whole alphabet: `a/b` and `a_b`
  # cannot land on one file. A plain `/`->`_` would collide them, and two scopes
  # sharing a counter is a ladder that resets itself for reasons nobody can see.
  #
  # ONE encoder, used by both ladder namers below. It was open-coded in
  # _u_streak_file alone until the rc 23 ladder needed the same property; a
  # predicate copied to a second site is typically wrong at one of them.
  local S
  S="${1//_/__}"
  S="${S//\//_}"
  printf '%s' "$S"
}

_u_streak_file() { # _u_streak_file <role> <scope> -> that pair's counter path
  printf '%s\n' "$DRIFT_STATE_DIR/unmeasured-${1:-host}-$(_streak_key "$2")"
}

_n_streak_file() { # _n_streak_file <role> <repo-path> -> that pair's counter path
  # 🔴 ITS OWN FILENAME PREFIX, not a namespace inside the rc 18 one. A scope
  # `nixdirt/x` encodes to `nixdirt_x`, which is exactly what a source repo
  # literally named `nixdirt` with a subtree `x` would produce — implausible, and
  # a collision between two ladders is not a thing to leave to plausibility.
  printf '%s\n' "$DRIFT_STATE_DIR/nixdirt-${1:-host}-$(_streak_key "$2")"
}

u_streak_bump() { # u_streak_bump <role> <scope> <reason> -> new streak, or -1
  _streak_file_bump "$(_u_streak_file "$1" "$2")" "$3"
}

n_streak_bump() { # n_streak_bump <role> <path> <class> -> new streak, or -1
  _streak_file_bump "$(_n_streak_file "$1" "$2")" "$3"
}

_streak_file_bump() { # _streak_file_bump <counter-file> <reason> -> streak, or -1
  local f PREV PREASON PCOUNT NEXT
  f="$1"
  mkdir -p "$DRIFT_STATE_DIR" 2>/dev/null || { echo -1; return 0; }
  PREV="$(cat "$f" 2>/dev/null || true)"
  PREASON=""
  PCOUNT=0
  # 🔴 TWO FIELDS OR NOTHING. `${x%% *}` and `${x##* }` BOTH fall back to the
  # WHOLE STRING when there is no space, so a one-field file would set the reason
  # and the count from the same token — the exact shape that made the phase-2
  # gate print `47 of 47` off a two-field line. Require the space before reading
  # either, and treat anything else as "no prior state".
  case "$PREV" in
    *' '*) PREASON="${PREV%% *}"; PCOUNT="${PREV##* }" ;;
  esac
  case "$PCOUNT" in ''|*[!0-9]*) PCOUNT=0 ;; esac
  # A CHANGED reason starts a NEW run of misses. The two ladders have different
  # thresholds, so carrying a FETCHFAILED count into a NOUPSTREAM ladder would
  # escalate on evidence that was never about that hazard — and the reset written
  # by u_streak_reset ("MEASURED 0") clears the streak through this same rule.
  [ "$PREASON" = "$2" ] || PCOUNT=0
  NEXT=$(( PCOUNT + 1 ))
  # `2>/dev/null` FIRST, then the target — see streak_bump for why the order of
  # the redirections is load-bearing.
  printf '%s %s\n' "$2" "$NEXT" 2>/dev/null > "$f" || { echo -1; return 0; }
  echo "$NEXT"
}

u_streak_reset() { # u_streak_reset <role> <scope> — it MEASURED; the run ends
  # Only ever rewrites a counter that already exists: a scope that has never been
  # unmeasured needs no file, and creating one per scope per run would put state
  # in the journal's way for no gain. The token is not a reason, so the reason
  # comparison in u_streak_bump restarts the count from 0 whatever comes next.
  _streak_file_reset "$(_u_streak_file "$1" "$2")" MEASURED
}

n_streak_reset() { # n_streak_reset <counter-file> — that path is untracked no more
  # 🔴 REWRITES, NEVER REMOVES, and that is forced rather than preferred. This
  # ladder is per-PATH, so cleared counters accumulate where rc 18's ~2 scopes do
  # not — deleting them would be tidier. But `rm` is a DESTRUCTIVE command to the
  # passivity scanner that guards this file (test_drift_check_source_never_
  # mutates), and a read-only deadman that starts deleting files is a worse trade
  # than some small files under $DRIFT_STATE_DIR. Bounded in practice by
  # DRIFT_NIXDIRT_MAX, which caps how many paths can ever be reported per run.
  # Takes the FILE, not the path: the rc 23 complement is discovered by LISTING
  # the state dir (a path that stopped being untracked is, by construction, not
  # in this run's report), so the caller holds a filename and no scope name to
  # re-encode. Decoding _streak_key back to a path would be a second, inverse
  # copy of the encoder — the thing consolidating it was meant to stop.
  _streak_file_reset "$1" CLEARED
}

_streak_file_reset() { # _streak_file_reset <counter-file> <token>
  local f
  f="$1"
  [ -d "$DRIFT_STATE_DIR" ] || return 0
  [ -f "$f" ] || return 0
  printf '%s 0\n' "$2" 2>/dev/null > "$f" || true
}

if [ "$DO_LOCAL" = 1 ]; then
  echo "=== local ($LOCAL_ROLE) ==="
  # Captured rather than streamed so the FACT lines can be diffed against the
  # other host's. stderr is deliberately NOT captured — it still goes straight
  # to the terminal/journal, so a fetch failure's git stderr keeps arriving
  # exactly as before.
  LOCAL_OUT="$(DRIFT_REPO="$DRIFT_REPO" DRIFT_LABEL="$LOCAL_ROLE" \
    DRIFT_UNTRACKED_MAX="$DRIFT_UNTRACKED_MAX" \
    DRIFT_NIXDIRT_MAX="$DRIFT_NIXDIRT_MAX" \
    DRIFT_DANGLING_MAX="$DRIFT_DANGLING_MAX" \
    bash -c "$PAYLOAD")"
  note_rc "$?"
  [ -n "$LOCAL_OUT" ] && printf '%s\n' "$LOCAL_OUT"
  note_rc "$(parity_rc_of "$LOCAL_OUT")"
  note_rc "$(src_rc_of "$LOCAL_OUT")"
  mark_checked "$LOCAL_ROLE (local)"
  echo
else
  mark_unchecked "$LOCAL_ROLE (local, --no-local)"
fi

if [ "$DO_REMOTE" = 1 ]; then
  echo "=== remote ($REMOTE_ROLE — $REMOTE_SSH) ==="
  # DRIFT_REPO is deliberately NOT forwarded: it is a local override, and the
  # remote host's repo lives at its own $HOME/workspace/devrc.
  # `bash -s` (piped, not inlined) — see the CHECK header re: zsh.
  # %q, not %s — these two values are executed on ANOTHER host (see require_int).
  REMOTE_OUT="$(printf 'DRIFT_LABEL=%q\nDRIFT_UNTRACKED_MAX=%q\nDRIFT_DANGLING_MAX=%q\nDRIFT_NIXDIRT_MAX=%q\n%s\n' \
    "$REMOTE_ROLE" "$DRIFT_UNTRACKED_MAX" "$DRIFT_DANGLING_MAX" "$DRIFT_NIXDIRT_MAX" "$PAYLOAD" \
    | ssh -o ConnectTimeout=10 -o BatchMode=yes "$REMOTE_SSH" bash -s)"
  remrc=$?
  [ -n "$REMOTE_OUT" ] && printf '%s\n' "$REMOTE_OUT"
  # ssh itself exits 255 on a connection/auth failure — that is "we could not
  # look". For a deadman that must never read as a pass... but it must not read
  # as DRIFT either: see "UNREACHABLE IS NOT DRIFT" in the header.
  if [ "$remrc" = 255 ]; then
    echo "[$REMOTE_ROLE] ssh to $REMOTE_SSH failed or timed out."
    remrc=13
  fi

  if [ "$remrc" = 13 ]; then
    streak="$(streak_bump "$REMOTE_ROLE")"
    echo "[$REMOTE_ROLE] UNREACHABLE — drift on that host was NOT evaluated. This is not a pass."
    if [ "$streak" -lt 0 ]; then
      echo "[$REMOTE_ROLE]   the consecutive-miss counter under $DRIFT_STATE_DIR could not be"
      echo "[$REMOTE_ROLE]   persisted, so 'for how long' is unknowable — ESCALATING (rc 13)."
      note_rc 13
      mark_unchecked "$REMOTE_ROLE (remote, UNREACHABLE — streak unknown, escalated)"
    elif [ "$streak" -ge "$DRIFT_UNREACHABLE_ESCALATE" ]; then
      echo "[$REMOTE_ROLE]   🔴 $streak CONSECUTIVE unreachable checks (threshold $DRIFT_UNREACHABLE_ESCALATE) —"
      echo "[$REMOTE_ROLE]   that is no longer 'the laptop is shut'. ESCALATING (rc 13)."
      note_rc 13
      mark_unchecked "$REMOTE_ROLE (remote, UNREACHABLE x$streak — escalated)"
    else
      echo "[$REMOTE_ROLE]   $streak/$DRIFT_UNREACHABLE_ESCALATE consecutive — NOT escalated: a laptop that is"
      echo "[$REMOTE_ROLE]   off, asleep or off-LAN is the expected cause and must not look like drift."
      echo "[$REMOTE_ROLE]   At $DRIFT_UNREACHABLE_ESCALATE consecutive misses this becomes rc 13 and fails the unit."
      mark_unchecked "$REMOTE_ROLE (remote, UNREACHABLE $streak/$DRIFT_UNREACHABLE_ESCALATE — not yet escalated)"
    fi
  else
    # It answered — with a verdict, good or bad. The run of misses is over.
    streak_reset "$REMOTE_ROLE"
    note_rc "$remrc"
    note_rc "$(parity_rc_of "$REMOTE_OUT")"
    note_rc "$(src_rc_of "$REMOTE_OUT")"
    mark_checked "$REMOTE_ROLE (remote)"
  fi
  echo
else
  mark_unchecked "$REMOTE_ROLE (remote, --no-remote)"
fi

# ── CROSS-HOST PARITY ─────────────────────────────────────────────────────────
# 🔴 A DIFFERENCE IS NOT A PROPERTY OF EITHER HOST. Both machines can be
# internally consistent — every symlink resolving, every enabled plugin present —
# and still disagree about which keys settings.json has or which plugins are on.
# That is only visible from here, with both outputs in hand.
#
# 🔴 AND IT IS ONLY VISIBLE WITH BOTH. One host checked is not "no divergence
# found", it is "divergence not looked for", and the two must never print the
# same way. An unreachable laptop lands here with an empty REMOTE_OUT and gets
# the SKIPPED branch — it does not silently contribute a clean parity verdict.
echo "=== host parity ($LOCAL_ROLE vs $REMOTE_ROLE) ==="
L_KEYS="$(fact_of "$LOCAL_OUT" settings-keys)"
R_KEYS="$(fact_of "$REMOTE_OUT" settings-keys)"
L_EPLUG="$(fact_of "$LOCAL_OUT" enabled-plugins)"
R_EPLUG="$(fact_of "$REMOTE_OUT" enabled-plugins)"

if [ -z "$L_KEYS" ] || [ -z "$R_KEYS" ]; then
  echo "[parity] NOT COMPARED — needs a fact set from EACH host; obtained from: ${CHECKED:-none}."
  echo "[parity]   this is not 'the hosts agree'. Nothing was compared."
elif [ "$L_KEYS" = UNEVALUATED ] || [ "$R_KEYS" = UNEVALUATED ]; then
  echo "[parity] NOT COMPARED — a host could not read or parse its settings.json (see its line above)."
else
  # KEY NAMES ONLY, never values — this output reaches the journal.
  l_only="$(only_in "$L_KEYS" "$R_KEYS")"
  r_only="$(only_in "$R_KEYS" "$L_KEYS")"
  # Split each side into the keys that MAY differ (enumerated above, each with a
  # reason) and the keys that may not. The verdict is decided by the second list
  # ONLY; the first is reported either way, never hidden.
  l_drift="$(perhost_split DROP "$l_only")"
  r_drift="$(perhost_split DROP "$r_only")"
  l_keep="$(perhost_split KEEP "$l_only")"
  r_keep="$(perhost_split KEEP "$r_only")"
  if [ -n "$l_drift" ] || [ -n "$r_drift" ]; then
    echo "[parity] DRIFT — settings.json top-level KEY SETS differ (names only; no values shown):"
    [ -n "$l_drift" ] && echo "[parity]   only on $LOCAL_ROLE:$l_drift"
    [ -n "$r_drift" ] && echo "[parity]   only on $REMOTE_ROLE:$r_drift"
    note_rc 15
  elif [ -n "$l_keep" ] || [ -n "$r_keep" ]; then
    # Everything that differs is on the enumerated per-host list. That is not
    # drift — but it is also not "identical", and it must not print as if it
    # were, or the next key added to that list disappears from the record.
    echo "[parity] settings.json top-level key sets AGREE apart from the per-host keys below."
  else
    # Counted into a variable first, not interpolated as `$( … | wc -w )` inside
    # the message: a command substitution ENDS the printer segment, leaving the
    # rest of the sentence looking like a command line to the suite's tokenizer.
    N_KEYS="$(printf '%s' "$L_KEYS" | wc -w)"
    echo "[parity] settings.json top-level key sets AGREE ($N_KEYS key names on each host)."
  fi
  # Printed in BOTH the drift and the agree branch: a key exempted here has been
  # decided about, and the decision belongs in the journal beside the verdict.
  if [ -n "$l_keep" ] || [ -n "$r_keep" ]; then
    echo "[parity] IGNORED (allowlisted in drift-check.sh, not drift):"
    say_perhost "$LOCAL_ROLE" "$l_keep"
    say_perhost "$REMOTE_ROLE" "$r_keep"
  fi

  if [ "$L_EPLUG" = UNEVALUATED ] || [ "$R_EPLUG" = UNEVALUATED ]; then
    echo "[parity] enabledPlugins NOT COMPARED — unreadable on at least one host."
  else
    le="$L_EPLUG"; re="$R_EPLUG"
    [ "$le" = NONE ] && le=""
    [ "$re" = NONE ] && re=""
    el_only="$(only_in "$le" "$re")"
    er_only="$(only_in "$re" "$le")"
    if [ -n "$el_only" ] || [ -n "$er_only" ]; then
      echo "[parity] DRIFT — enabledPlugins differ:"
      [ -n "$el_only" ] && echo "[parity]   enabled only on $LOCAL_ROLE:$el_only"
      [ -n "$er_only" ] && echo "[parity]   enabled only on $REMOTE_ROLE:$er_only"
      echo "[parity]   fix: claude plugin install <plugin> on the host that lacks it."
      note_rc 15
    else
      echo "[parity] enabledPlugins AGREE."
    fi
  fi
fi
echo

# ── SKILL-LISTING TIERS (rc 22) ───────────────────────────────────────────────
# 🔴 WHAT THIS MEASURES. Every skill's name + description loads on EVERY session
# under a budget of 1% of the context window, IN CHARACTERS. `skillOverrides` in
# settings.json makes that cost per-skill opt-in, and `claude/skill-tiers.json`
# is devrc's ledger of which skills spend it. That ledger is in git;
# ~/.claude/settings.json is per-host and unmanaged, so nothing keeps them
# together except this arm and `scripts/sync-skill-tiers.py`.
#
# 🔴 THIS IS NOT A CROSS-HOST COMPARISON. Both hosts can agree perfectly and both
# be wrong; the reference is the ledger, not the other machine. So each host is
# reported against the ledger on its own, and a run that reaches only one host
# still says something true about that one.
#
# 🔴 "NO OVERRIDES AT ALL" IS **NOT** DRIFT, AND THAT IS THE WHOLE DESIGN.
# The mechanism shipped with the ledger applied to NO host — deliberately:
# measured 2026-08-24, the listing was 20,708 chars against a 30,000-char budget
# on claude-opus-5 @1M (0.69x), so nothing is being truncated and a wide tier B
# would cost real routing today for a benefit ~1.8 months out. If an unapplied
# host counted as drift, this code would be red on every run from the moment it
# landed — and `claude/RULES.md` is explicit that a permanently-red gate is worse
# than no gate, because it trains everyone to click through. So a host with no
# `skillOverrides` key prints NOT ADOPTED, every run, with NO rc. rc 22 fires
# only once a host HAS been given overrides and they have since disagreed:
# adopted-then-drifted, which is the hazard a ledger in git can actually own.
#
# 🔴 THE EXPECTATION IS READ BY THE LEDGER'S OWN PARSER, never by a second sed.
# `lib/skill_tier_facts.py` projects it through `lib/skill_tiers.py` — the same
# module `sync-skill-tiers.py` writes from — so the checker and the writer cannot
# disagree about what the ledger says. A reader that cannot produce an
# expectation prints COULD NOT MEASURE and sets no rc: an empty expectation would
# make every host look compliant, in silence, which is the reassuring zero this
# whole subsystem exists to refuse.
_drift_tier_py="$_drift_dir/lib/skill_tier_facts.py"

tier_report() { # tier_report <role> <that host's fact> <wanted name=value list> <wanted count>
  local ROLE="$1" FACT="$2" WANT="$3" NW="$4"
  local X N V MISSING="" WRONG="" EXTRA=""
  if [ -z "$FACT" ]; then
    echo "[tiers] $ROLE: NOT REPORTED — that host produced no skill-overrides fact."
    echo "[tiers]   Not reached (--no-local/--no-remote, or unreachable), or its stream was cut"
    echo "[tiers]   short before the FACT lines. NOT a match — nothing was compared for this host."
    return 0
  fi
  if [ "$FACT" = UNEVALUATED ]; then
    echo "[tiers] $ROLE: NOT EVALUATED — settings.json unreadable or unparseable there (see its line above)."
    echo "[tiers]   This is not 'it matches the ledger'. Nothing was compared for this host."
    return 0
  fi
  if [ "$NW" = 0 ]; then
    echo "[tiers] $ROLE: nothing to compare — the ledger asks for 0 overrides (every skill is tier A)."
    return 0
  fi
  if [ "$FACT" = NONE ] || [ "$FACT" = EMPTY ]; then
    echo "[tiers] $ROLE: NOT ADOPTED — no skillOverrides deployed, so none of the ledger's $NW tier-B entries apply."
    echo "[tiers]   This is the state the mechanism SHIPPED in, not drift — applying it is an"
    echo "[tiers]   operator act on a per-host file. Apply it there with:"
    echo "[tiers]     scripts/sync-skill-tiers.py            # dry-run, prints the exact diff"
    echo "[tiers]     scripts/sync-skill-tiers.py --apply    # writes"
    return 0
  fi
  for X in $WANT; do
    N="${X%%=*}"
    V="${X#*=}"
    case " $FACT " in
      *" $N=$V "*) ;;
      *" $N="*) WRONG="$WRONG $X" ;;
      *) MISSING="$MISSING $X" ;;
    esac
  done
  for X in $FACT; do
    N="${X%%=*}"
    case " $WANT " in
      *" $N="*) ;;
      *) EXTRA="$EXTRA $X" ;;
    esac
  done
  if [ -n "$MISSING" ] || [ -n "$WRONG" ] || [ -n "$EXTRA" ]; then
    echo "[tiers] 🔴 DRIFT — $ROLE has skillOverrides that disagree with claude/skill-tiers.json:"
    [ -n "$MISSING" ] && echo "[tiers]   in the ledger, NOT on the host:$MISSING"
    [ -n "$WRONG" ] && echo "[tiers]   on the host with a DIFFERENT value than the ledger asks for:$WRONG"
    [ -n "$EXTRA" ] && echo "[tiers]   on the host, NOT in the ledger (hand-edited, or a retired skill):$EXTRA"
    echo "[tiers]   The always-on skill listing on that host is not the one this repo describes."
    echo "[tiers]   fix (on that host): scripts/sync-skill-tiers.py   (dry-run; --apply to write)"
    echo "[tiers]   If the host is also reported BEHIND above, ship it FIRST — a stale checkout"
    echo "[tiers]   carries a stale ledger, and this finding is then a symptom of that one."
    note_rc 22
  else
    echo "[tiers] $ROLE: matches the ledger ($NW tier-B override(s) deployed as asked)."
  fi
}

echo "=== skill-listing tiers (deployed skillOverrides vs claude/skill-tiers.json) ==="
if [ ! -r "$_drift_tier_py" ]; then
  echo "[tiers] COULD NOT MEASURE — cannot read $_drift_tier_py."
  echo "[tiers]   This is NOT 'the hosts match the ledger'. No rc is set."
else
  T_RAW="$(python3 "$_drift_tier_py" "${DRIFT_TIER_LEDGER:-}" 2>/dev/null)"
  T_TOKEN="${T_RAW%% *}"
  if [ "$T_TOKEN" != ok ]; then
    echo "[tiers] COULD NOT MEASURE — the ledger reader produced no usable expectation."
    echo "[tiers]   An empty expectation would make every host look compliant, in silence."
    echo "[tiers]   This is NOT 'the hosts match the ledger'. No rc is set."
  else
    T_WANT="${T_RAW#ok}"
    T_WANT="${T_WANT# }"
    T_NW="$(printf '%s' "$T_WANT" | wc -w)"
    echo "[tiers] ledger asks for $T_NW name-only override(s); every other skill keeps its description."
    tier_report "$LOCAL_ROLE" "$(fact_of "$LOCAL_OUT" skill-overrides)" "$T_WANT" "$T_NW"
    tier_report "$REMOTE_ROLE" "$(fact_of "$REMOTE_OUT" skill-overrides)" "$T_WANT" "$T_NW"
  fi
fi
echo

# ── CROSS-HOST SOURCE-REPO COMPARISON ─────────────────────────────────────────
# 🔴 INFORMATION ONLY — it sets no exit code, and that is a decision, not an
# omission. Whether a given source-repo HEAD is WRONG has a defined answer and it
# is measured PER HOST above, against that branch's own upstream. "The two hosts
# are on different commits" has no such answer: these are shared development
# repos and one machine sitting on a feature branch is normal. A code that fired
# on it would be red most of the time, and a permanently-red gate is worse than
# no gate — the same refusal this file already makes for an unreachable laptop.
#
# What it IS worth printing is the most direct statement available of "these two
# machines compile different code", which no per-host line can make.
#
# 🔴 AND IT IS ONLY VISIBLE WITH BOTH. One host's facts is not "the hosts agree",
# it is "agreement not looked for", and the two must never print the same way.
echo "=== source-repo parity ($LOCAL_ROLE vs $REMOTE_ROLE) ==="
L_SRC="$(fact_of "$LOCAL_OUT" src-repos)"
R_SRC="$(fact_of "$REMOTE_OUT" src-repos)"
if [ -z "$L_SRC" ] || [ -z "$R_SRC" ]; then
  echo "[srcrepo] NOT COMPARED — needs a fact set from EACH host; obtained from: ${CHECKED:-none}."
  echo "[srcrepo]   this is not 'the two machines build the same source'. Nothing was compared."
else
  L_SN="$(src_names_of "$L_SRC")"
  R_SN="$(src_names_of "$R_SRC")"
  S_AGREE=0
  S_DISAGREE=0
  for N in $L_SN $(only_in "$R_SN" "$L_SN"); do
    LV="$(src_head_of "$L_SRC" "$N")"
    RV="$(src_head_of "$R_SRC" "$N")"
    if [ -z "$LV" ] || [ -z "$RV" ]; then
      echo "[srcrepo]   $N — named by nix/pkgs on only one host's checkout; not compared."
      S_DISAGREE=$(( S_DISAGREE + 1 ))
    elif [ "$LV" != "$RV" ]; then
      echo "[srcrepo]   $N — $LOCAL_ROLE tree $LV, $REMOTE_ROLE tree $RV: the two hosts build DIFFERENT source."
      echo "[srcrepo]     (srcDir subtree trees, not repo HEADs — a commit outside this path does not appear here.)"
      S_DISAGREE=$(( S_DISAGREE + 1 ))
    else
      S_AGREE=$(( S_AGREE + 1 ))
    fi
  done
  # Counted into a variable first, never interpolated as `$(( … ))` inside the
  # message: a command substitution ENDS the printer segment, leaving the rest of
  # the sentence looking like a command line to the suite's tokenizer.
  S_COMPARED=$(( S_AGREE + S_DISAGREE ))
  echo "[srcrepo] compared=$S_COMPARED same=$S_AGREE differing=$S_DISAGREE — information only; the per-host lines above carry the verdict."
fi
echo

# ── BUILT-SOURCE SCOPES THAT STAY UNMEASURED (rc 18) ──────────────────────────
# 🔴 See "UNMEASURED IS NOT FOREVER" in the header for what this measures and why
# each reason gets the ladder it gets. Here: how it refuses to be satisfied by
# measuring nothing.
#
# It runs HERE, in the driver, and not in the per-host payload, for the reason
# the rc 13 ladder is here too: the streak is PERSISTENT STATE, it belongs to the
# machine keeping the record, and the payload is a stateless thing piped to
# whichever host is being examined. A counter kept on the far side would be
# invisible to the operator and would reset with the laptop's state dir.
#
# 🔴 A HOST IS WALKED ONLY IF IT ANSWERED. The `FACT src-unmeasured` line is the
# proof it ran the payload; without it nothing is bumped and nothing is reset —
# an unreachable laptop must not accumulate a streak nobody was looking at, and
# rc 13 already owns that finding.
echo "=== built-source scopes that stay UNMEASURED ($LOCAL_ROLE / $REMOTE_ROLE) ==="
U_REPORTING=0
U_SCOPES=0
U_UNMEASURED=0
U_ESCALATED=0
for HROLE in "$LOCAL_ROLE" "$REMOTE_ROLE"; do
  if [ "$HROLE" = "$LOCAL_ROLE" ]; then
    U_LINE="$(fact_of "$LOCAL_OUT" src-unmeasured)"
    U_SRC="$(fact_of "$LOCAL_OUT" src-repos)"
  else
    U_LINE="$(fact_of "$REMOTE_OUT" src-unmeasured)"
    U_SRC="$(fact_of "$REMOTE_OUT" src-repos)"
  fi
  [ -n "$U_LINE" ] || continue
  U_REPORTING=$(( U_REPORTING + 1 ))

  U_SEEN="${U_LINE%% *}"
  U_SEEN="${U_SEEN#examined=}"
  case "$U_SEEN" in ''|*[!0-9]*) U_SEEN=-1 ;; esac
  if [ "$U_SEEN" -le 0 ]; then
    echo "[srcblind] $HROLE: NOT EVALUATED — that host's nix/pkgs named no \${workspace}/"
    echo "[srcblind]   source, so there is no scope here to be stuck. Not 'nothing is unmeasured'."
    continue
  fi
  U_SCOPES=$(( U_SCOPES + U_SEEN ))

  # 🔴 THE WHOLE-STRING FALLBACK AGAIN: `${x#* }` returns x unchanged when there
  # is no space, so a line with NO pairs (`examined=2`) would be read as the pair
  # `examined=2` — a fabricated scope named `examined`, on the structural ladder.
  # Require the space before taking the remainder.
  U_PAIRS=""
  case "$U_LINE" in *' '*) U_PAIRS="${U_LINE#* }" ;; esac

  U_NAMES=""
  for X in $U_PAIRS; do
    KS="${X%%=*}"
    RSN="${X#*=}"
    U_NAMES="$U_NAMES $KS"
    U_UNMEASURED=$(( U_UNMEASURED + 1 ))
    THR="$(u_threshold "$RSN")"
    if [ "$THR" = NEVER ]; then
      echo "[srcblind] $HROLE $KS: UNMEASURED ($RSN) — reported, and it NEVER escalates."
      echo "[srcblind]   A host without that checkout is a documented, tolerated state: the"
      echo "[srcblind]   derivation guards on pathExists and simply omits the binary."
      continue
    fi
    STK="$(u_streak_bump "$HROLE" "$KS" "$RSN")"
    if [ "$STK" -lt 0 ]; then
      echo "[srcblind] 🔴 $HROLE $KS: UNMEASURED ($RSN), and the streak under $DRIFT_STATE_DIR"
      echo "[srcblind]   could not be persisted, so 'for how long' is unknowable — ESCALATING (rc 18)."
      U_ESCALATED=$(( U_ESCALATED + 1 ))
      note_rc 18
    elif [ "$STK" -ge "$THR" ]; then
      # The whole claim on ONE line — host, scope, reason, streak and threshold.
      # Split across two it cannot be asserted as a single normalised string, and
      # a guard on half a sentence is walkable by rewording the other half.
      echo "[srcblind] 🔴 DRIFT — $HROLE $KS: UNMEASURED ($RSN) for $STK CONSECUTIVE runs (threshold $THR)."
      echo "[srcblind]   Its currency has never been evaluated, so rc 17 CANNOT fire for it: a stale"
      echo "[srcblind]   built source here is invisible for as long as this holds."
      echo "[srcblind]   fix: give that branch an upstream (git -C ~/workspace/<repo> push -u origin HEAD)"
      echo "[srcblind]   or restore the fetch on that host, then re-run."
      U_ESCALATED=$(( U_ESCALATED + 1 ))
      note_rc 18
    else
      echo "[srcblind] $HROLE $KS: UNMEASURED ($RSN) — $STK/$THR consecutive; NOT escalated."
      echo "[srcblind]   Still not a pass: nothing has measured what this host compiles."
    fi
  done

  # The complement — every scope this host DID measure — ends its run of misses.
  # Derived from the src-repos fact minus the pairs above rather than from a
  # second payload line: the two lines are emitted together, so a scope in one
  # and not the other is a scope that measured.
  for X in $(src_names_of "$U_SRC"); do
    case " $U_NAMES " in
      *" $X "*) ;;
      *) u_streak_reset "$HROLE" "$X" ;;
    esac
  done
done

# 🔴 REPORTING BESIDE SCOPES BESIDE ESCALATED, and a refusal at either zero. A
# ladder walked over no hosts, or over no scopes, produces `escalated=0` — the
# identical output to a fleet where every scope measures. Neither number means
# anything alone, and the summary is withheld entirely rather than printed as a
# clean-looking triple over an empty set.
if [ "$U_REPORTING" = 0 ]; then
  echo "[srcblind] NOT EVALUATED — no host returned a src-unmeasured fact set; obtained from: ${CHECKED:-none}."
  echo "[srcblind]   this is not 'nothing is stuck'. No scope on any host was counted."
elif [ "$U_SCOPES" = 0 ]; then
  echo "[srcblind] NOT EVALUATED — $U_REPORTING host(s) answered and between them named ZERO"
  echo "[srcblind]   built-source scopes. A ladder over no scopes is not a clean ladder; it is no ladder."
else
  echo "[srcblind] hosts-reporting=$U_REPORTING scopes=$U_SCOPES unmeasured=$U_UNMEASURED escalated=$U_ESCALATED"
fi
echo

# ── UNTRACKED FILES THAT SIT IN NIX-READ PATHS (rc 23) ────────────────────────
# 🔴 See "UNTRACKED IN A NIX-READ PATH" in the header for what this measures and
# why the ladder is longer than rc 18's. Here: how it refuses to be satisfied by
# measuring nothing.
#
# It runs HERE, in the driver, for the reason rc 13's and rc 18's ladders do: the
# streak is PERSISTENT STATE and belongs to the machine keeping the record, while
# the payload is a stateless thing piped to whichever host is being examined.
#
# 🔴 A HOST IS WALKED ONLY IF IT ANSWERED, and only if its answer carries a
# NON-ZERO nix-read denominator. Without one, every untracked file on that host
# classifies clean — so nothing is bumped AND nothing is reset: a ladder cleared
# by a scan that walked nothing is worse than no ladder.
echo "=== untracked files in NIX-READ paths ($LOCAL_ROLE / $REMOTE_ROLE) ==="
N_REPORTING=0
N_UNTRACKED=0
N_NIXREAD=0
N_HITS=0
N_LISTED=0
N_ESCALATED=0
N_BLIND=0
for HROLE in "$LOCAL_ROLE" "$REMOTE_ROLE"; do
  if [ "$HROLE" = "$LOCAL_ROLE" ]; then
    N_LINE="$(fact_of "$LOCAL_OUT" nix-untracked)"
  else
    N_LINE="$(fact_of "$REMOTE_OUT" nix-untracked)"
  fi
  [ -n "$N_LINE" ] || continue
  N_REPORTING=$(( N_REPORTING + 1 ))

  # 🔴 READ BY KEY, NOT BY POSITION, and the path pairs are matched FIRST, by
  # their VALUE. `${x#* }` positional parsing is what made the phase-2 gate print
  # `47 of 47`. The ORDER of these arms is load-bearing and was wrong once: with
  # `reason=*` ahead of the pair arm, an untracked repo-root file literally named
  # `reason` produced a `reason=<REACH>` token that was read as this line's
  # REASON field — so the host came out COULD NOT MEASURE and the file it named
  # was dropped, silently, in the direction of "nothing to see". LIVE and DROPPED
  # are the whole reach vocabulary and no header field can take either value, so
  # matching the pairs first cannot swallow a header key.
  N_UNT=-1; N_MM=-1; N_HIT=-1; N_LST=-1; N_RSN=""; N_PAIRS=""; N_SEEN=0
  for X in $N_LINE; do
    case "$X" in
      *=LIVE|*=DROPPED) N_PAIRS="$N_PAIRS $X"; N_SEEN=$(( N_SEEN + 1 )) ;;
      untracked=*)      N_UNT="${X#untracked=}" ;;
      nixread=*)        N_MM="${X#nixread=}" ;;
      hits=*)           N_HIT="${X#hits=}" ;;
      listed=*)         N_LST="${X#listed=}" ;;
      reason=*)         N_RSN="${X#reason=}" ;;
    esac
  done
  case "$N_UNT" in ''|*[!0-9]*) N_UNT=-1 ;; esac
  case "$N_MM" in ''|*[!0-9]*) N_MM=-1 ;; esac
  # 🔴 NOT DEFAULTED, and never derived from the pairs. A missing or unparseable
  # `hits=` is a report this driver cannot read, and the one thing it must not do
  # is fall back to counting the list — that IS the bug this field was added to
  # remove. -1 here lands on the refusal below.
  case "$N_HIT" in ''|*[!0-9]*) N_HIT=-1 ;; esac
  case "$N_LST" in ''|*[!0-9]*) N_LST=-1 ;; esac

  if [ "$N_RSN" != OK ] || [ "$N_UNT" -lt 0 ] || [ "$N_MM" -le 0 ] \
     || [ "$N_HIT" -lt 0 ] || [ "$N_LST" -lt 0 ]; then
    echo "[nixdirt] $HROLE: COULD NOT MEASURE — reason=${N_RSN:-ABSENT} untracked=$N_UNT nix-read-paths=$N_MM hits=$N_HIT listed=$N_LST."
    echo "[nixdirt]   With no derived nix-read set every untracked file on that host classifies"
    echo "[nixdirt]   as harmless. That is not a finding of none: nothing was bumped, nothing"
    echo "[nixdirt]   was reset, and NO code is set for it."
    N_BLIND=$(( N_BLIND + 1 ))
    continue
  fi
  # 🔴 THE INTEGRITY CHECK THAT MAKES `listed=` LOAD-BEARING. The emitter says how
  # many pairs it put on the line; this counts how many arrived. They disagree
  # only if the line was mangled or truncated in transit (the remote leg's stdout
  # crosses an ssh hop), and a partial pair list read as a complete one would end
  # every missing path's streak in the complement loop below — silently, in the
  # direction of "nothing to see". So a disagreement is refused, not repaired.
  if [ "$N_SEEN" != "$N_LST" ]; then
    echo "[nixdirt] $HROLE: COULD NOT MEASURE — the report claims listed=$N_LST path(s) but $N_SEEN arrived."
    echo "[nixdirt]   The pair list does not match its own declared length, so this line was"
    echo "[nixdirt]   mangled or truncated between that host and here. Nothing was bumped,"
    echo "[nixdirt]   nothing was reset, and NO code is set for it."
    N_BLIND=$(( N_BLIND + 1 ))
    continue
  fi
  N_UNTRACKED=$(( N_UNTRACKED + N_UNT ))
  N_NIXREAD=$(( N_NIXREAD + N_MM ))
  # 🔴 THE REPORTED TOTAL, never the length of the list. See the FACT-line header
  # in the payload for the measured 15-vs-10.
  N_HITS=$(( N_HITS + N_HIT ))
  N_LISTED=$(( N_LISTED + N_LST ))

  N_KEEP=""
  for X in $N_PAIRS; do
    N_P="${X%=*}"
    N_C="${X##*=}"
    N_F="$(_n_streak_file "$HROLE" "$N_P")"
    N_KEEP="$N_KEEP ${N_F##*/}"
    STK="$(n_streak_bump "$HROLE" "$N_P" "$N_C")"
    if [ "$STK" -lt 0 ]; then
      echo "[nixdirt] 🔴 $HROLE $N_P: UNTRACKED in a NIX-READ path ($N_C), and the streak under"
      echo "[nixdirt]   $DRIFT_STATE_DIR could not be persisted, so 'for how long' is"
      echo "[nixdirt]   unknowable — ESCALATING (rc 23)."
      N_ESCALATED=$(( N_ESCALATED + 1 ))
      note_rc 23
    elif [ "$STK" -ge "$DRIFT_NIXDIRT_ESCALATE" ]; then
      # The whole claim on ONE line — host, path, class, streak and threshold —
      # for the reason the rc 18 line gives: a guard on half a sentence is
      # walkable by rewording the other half.
      echo "[nixdirt] 🔴 DRIFT — $HROLE $N_P: UNTRACKED in a NIX-READ path ($N_C) for $STK CONSECUTIVE runs (threshold $DRIFT_NIXDIRT_ESCALATE)."
      if [ "$N_C" = LIVE ]; then
        echo "[nixdirt]   LIVE: the deployed path is a mkOutOfStoreSymlink back into that working"
        echo "[nixdirt]   tree, which the link resolves at USE time and not through the flake"
        echo "[nixdirt]   source — so this file IS being served on that host right now, with no"
        echo "[nixdirt]   commit, no backup and no other host holding a copy."
      else
        # 🔴 THE SENTENCE THIS REPLACED WAS FALSE. It said "every generation built
        # on that host carries it". Nix filters a git checkout to the files git
        # knows about, so an untracked file reaches NOTHING — measured on all six
        # -dl-router store generations. Overstating here is the precise failure
        # this whole block exists to remove, so the finding keeps its escalation
        # and loses the claim it could not support.
        echo "[nixdirt]   DROPPED: nix reads that path, but the flake source is filtered to the"
        echo "[nixdirt]   files git knows about, so this file did NOT reach the artifact. It is"
        echo "[nixdirt]   unsaved work in no commit, no backup and on no other host — sitting in"
        echo "[nixdirt]   a tree nix copies, one git-add from being deployed."
      fi
      echo "[nixdirt]   fix: commit it, delete it, or gitignore it on that host, then re-run."
      N_ESCALATED=$(( N_ESCALATED + 1 ))
      note_rc 23
    else
      echo "[nixdirt] $HROLE $N_P: UNTRACKED in a NIX-READ path ($N_C) — $STK/$DRIFT_NIXDIRT_ESCALATE consecutive; NOT escalated."
      echo "[nixdirt]   Still not a pass: that content exists on exactly one machine."
    fi
  done

  # 🔴 A TRUNCATED LIST CANNOT CLEAR A LADDER. The complement loop below reads
  # "absent from the report" as "this path is untracked no more" — true only when
  # the report is COMPLETE. Once DRIFT_NIXDIRT_MAX has cut the enumeration,
  # absence also means "it was pushed out of the window", and a path that has
  # been sitting there for eleven runs would have its streak reset by a busy run
  # that listed ten other paths ahead of it. The two causes are indistinguishable
  # from here, so the ladders are LEFT ALONE and the run says why. Same rule as
  # the COULD-NOT-MEASURE arm above: a ladder cleared by evidence that cannot
  # support the claim is worse than a ladder that stands still.
  if [ "$N_HIT" -gt "$N_LST" ]; then
    # Hoisted, not inlined: `$((` inside a quoted message ends the printer
    # segment for the command-word scanner in test_drift_check.py, and the rest
    # of the sentence then reads as a command line. See _PROSE_NOT_COMMANDS —
    # keeping the arithmetic out of the string is cheaper than declaring six
    # more English words to be prose.
    N_MISS=$(( N_HIT - N_LST ))
    echo "[nixdirt] $HROLE: LISTING TRUNCATED — $N_HIT hit(s), $N_LST enumerated, $N_MISS not named (DRIFT_NIXDIRT_MAX=$DRIFT_NIXDIRT_MAX)."
    echo "[nixdirt]   The COUNT is complete; the paths are not. No streak was reset on this"
    echo "[nixdirt]   host this run — absence from a truncated list is not evidence that a"
    echo "[nixdirt]   path stopped being untracked. Raise DRIFT_NIXDIRT_MAX to see them all."
  else
    # The complement — every counter this host kept that this run did NOT report —
    # ends its run. A path that was committed, deleted or gitignored is simply
    # absent from the report, so the state dir is the only place it can be seen.
    # 🔴 Reached only past the denominator guard above: a host that could not
    # measure leaves its ladders exactly as they were.
    for N_F in "$DRIFT_STATE_DIR"/nixdirt-"$HROLE"-*; do
      [ -f "$N_F" ] || continue
      case " $N_KEEP " in
        *" ${N_F##*/} "*) ;;
        *) n_streak_reset "$N_F" ;;
      esac
    done
  fi
done

# 🔴 REPORTING BESIDE THE DENOMINATOR BESIDE THE HITS. `hits=0` is the identical
# output for "no untracked file is nix-read" and "no nix-read path was ever
# derived", and only the denominator separates them — so the summary is withheld
# entirely rather than printed as a clean-looking line over an empty set.
if [ "$N_REPORTING" = 0 ]; then
  echo "[nixdirt] NOT EVALUATED — no host returned a nix-untracked fact; obtained from: ${CHECKED:-none}."
  echo "[nixdirt]   this is not 'nothing is exposed'. No untracked file on any host was classified."
elif [ "$N_NIXREAD" = 0 ]; then
  echo "[nixdirt] NOT EVALUATED — $N_REPORTING host(s) answered and between them derived ZERO"
  echo "[nixdirt]   nix-read paths. A classifier with an empty set calls everything clean."
else
  # 🔴 `listed=` BESIDE `hits=`, for the reason the denominator sits beside them
  # both: they are equal on almost every run, and the run where they are NOT is
  # the run whose per-path lines are incomplete. Printing `hits=` alone would put
  # a complete-looking number over a list that names fewer paths, which is the
  # shape an operator reads as "here is everything".
  echo "[nixdirt] hosts-reporting=$N_REPORTING untracked=$N_UNTRACKED nix-read-paths=$N_NIXREAD hits=$N_HITS listed=$N_LISTED escalated=$N_ESCALATED blind=$N_BLIND"
  # Hoisted out of the message: `$((` inside a quoted string ends the printer
  # segment for test_drift_check.py's command-word scanner, and the rest of the
  # sentence then reads as a command line (see _PROSE_NOT_COMMANDS there).
  N_UNNAMED=$(( N_HITS - N_LISTED ))
  [ "$N_UNNAMED" -gt 0 ] && \
    echo "[nixdirt]   $N_UNNAMED hit(s) counted but NOT named above — raise DRIFT_NIXDIRT_MAX (now $DRIFT_NIXDIRT_MAX) to enumerate them."
fi
echo

# ── BRANCH PROTECTION ON THE CANONICAL REMOTE (rc 24) ─────────────────────────
# See "BRANCH PROTECTION" in the header for what this measures and why it is a
# fourth kind of parity. Here: how it refuses to produce a silent zero.
#
# 🔴 THE HAZARD THIS SHAPE EXISTS TO AVOID IS SPECIFIC TO THIS ARM. Every other
# could-not-measure in this file degrades toward "no finding". Here the natural
# failure — `gh` with no token prints nothing — parses as a count of ZERO, and
# zero is the DRIFT value. So the arm never derives its verdict from a count it
# did not positively read: the line must be exactly `<true|false> <digits>`, and
# anything else is a reason, printed as such, setting no rc.
bp_slug_of() { # bp_slug_of <remote-url> -> owner/repo, or "" if not GitHub
  # 🔴 THE HOST MUST BE ANCHORED, NOT MERELY CONTAINED. `*github.com/*` is an
  # unanchored substring test, and a self-hosted mirror puts that string in the
  # PATH: `https://mirror.internal.example/github.com/other-owner/other-repo.git`
  # matched it and yielded `other-owner/other-repo` — a slug this fleet's remote
  # never named. The arm would then query a REAL, UNRELATED repo and, if that
  # one is protected, print a confident green about a remote it is not watching.
  # Not a 404 into could-not-measure: an affirmative answer about the wrong
  # subject, which is worse than no answer. Measured on three mirror shapes.
  #
  # So: strip the scheme, strip any userinfo, and require what REMAINS to BEGIN
  # with `github.com` followed by the scp `:` or the path `/`.
  local U="$1" S=""
  case "$U" in *://*) U="${U#*://}" ;; esac
  # 🔴 THE USERINFO STRIP MUST BE ANCHORED TOO, or it re-opens the very class the
  # host anchor below closes: `https://mirror.example/u@github.com/o/r.git` has an
  # `@` in its PATH, and stripping to it leaves a string that BEGINS `github.com/`.
  # Userinfo can only precede the host, so it may not contain a `/`.
  case "$U" in
    *@*) case "${U%%@*}" in */*) ;; *) U="${U#*@}" ;; esac ;;
  esac
  case "$U" in
    github.com:*) S="${U#github.com:}" ;;
    github.com/*) S="${U#github.com/}" ;;
    *) return 0 ;;
  esac
  # Trailing slash BEFORE `.git`, then again after: stripping `.git` first left
  # `owner/repo.git` for `…/repo.git/`, a slug that 404s while looking right.
  S="${S%/}"
  S="${S%.git}"
  S="${S%/}"
  # Exactly two non-empty, slash-free components. A URL that yields anything
  # else is NOT turned into a best guess: an owner/repo we invented would be
  # queried, 404, and land in could-not-measure wearing a fabricated subject.
  case "$S" in
    /*|*/) return 0 ;;
    */*/*) return 0 ;;
    */*) printf '%s' "$S" ;;
    *) return 0 ;;
  esac
}

echo "=== branch protection on the canonical remote (main) ==="
# 🔴 THE OVERRIDE EXISTS FOR THE SUITE, and it is not a convenience. The
# derivation reads the SAME `origin` the git leg fetches from, so a test that
# gave this arm a GitHub slug by editing origin would point `git fetch` at the
# real github.com — every rc-24 test would then depend on the live state of a
# remote no test controls, over a network the suite is built not to touch. Same
# role and same reasoning as DRIFT_TIER_LEDGER; the derived value is the only
# correct one on a real host, and the derivation keeps its own coverage
# (`test_the_slug_is_derived_from_the_origin_remote`).
BP_URL=""
BP_SLUG="${DRIFT_PROTECT_SLUG:-}"
if [ -z "$BP_SLUG" ]; then
  # `ls-remote --get-url` is LOCAL — it expands the configured URL and exits
  # without contacting anything (measured 0.003s). It is also the only read-only
  # verb on the passivity allowlist that yields a remote URL; `git config` and
  # `git remote` both have write forms and are deliberately not allowlisted.
  BP_URL="$(git -C "$DRIFT_REPO" ls-remote --get-url origin 2>/dev/null)"
  BP_SLUG="$(bp_slug_of "$BP_URL")"
fi
if [ -z "$BP_SLUG" ] && [ -z "$BP_URL" ]; then
  echo "[protect] COULD NOT MEASURE — no origin remote readable in $DRIFT_REPO."
  echo "[protect]   This is NOT 'main is protected'. No rc is set."
elif [ -z "$BP_SLUG" ]; then
  # The URL itself is deliberately not echoed: this repo is public and an origin
  # can name a private host. The finding is that it is not a GitHub owner/repo,
  # which is all the operator needs to know why the arm stayed quiet.
  echo "[protect] COULD NOT MEASURE — origin is not a github.com owner/repo remote,"
  echo "[protect]   so there is no branch-protection API to ask. No rc is set."
elif ! command -v "$DRIFT_GH" >/dev/null 2>&1; then
  echo "[protect] COULD NOT MEASURE — no usable gh binary at $DRIFT_GH."
  echo "[protect]   This is NOT 'main is protected'. No rc is set."
else
  # A plain GET. 🔴 No -X/--method, no -f/--field: `gh` can DELETE this very
  # protection, and the git allowlist that guards the rest of this file cannot
  # see a `gh` argv at all. Pinned by test_the_gh_calls_are_read_only.
  BP_RAW="$(timeout "$DRIFT_GH_TIMEOUT" "$DRIFT_GH" api "repos/$BP_SLUG/branches/main" --jq '"\(.protected) \(.protection.required_status_checks.contexts // [] | length) \(.protection.required_status_checks.contexts // [] | join(","))"' 2>/dev/null)"
  # 🔴 POSITIONAL READ, not `${x%% *}`/`${x##* }`. Those BOTH fall back to the
  # whole string when there is no space, so a one-field answer set `protected`
  # and the count from the SAME token — one value read twice, wearing the shape
  # of a measurement. `read` simply leaves the later fields EMPTY, and empty is
  # rejected below. The names field is display-only and may legitimately be
  # empty (a branch with no required checks has no context names).
  BP_PROT=""; BP_N=""; BP_NAMES=""
  read -r BP_PROT BP_N BP_NAMES <<EOF
$BP_RAW
EOF
  case "$BP_PROT" in true|false) ;; *) BP_N=-1 ;; esac
  case "$BP_N" in ''|*[!0-9]*) BP_N=-1 ;; esac
  if [ "$BP_N" = -1 ]; then
    echo "[protect] COULD NOT MEASURE — gh gave no usable answer for $BP_SLUG."
    echo "[protect]   No network, no credentials, no access to that repo, or an API shape"
    echo "[protect]   this arm does not recognise. 🔴 An empty answer parses as a count of"
    echo "[protect]   ZERO, and zero is the DRIFT value — so it is refused here rather than"
    echo "[protect]   fired as a finding. This is NOT 'main is protected'. No rc is set."
  elif [ "$BP_N" -gt 0 ]; then
    echo "[protect] $BP_SLUG main: $BP_N required status check(s) — ${BP_NAMES:-<names unavailable>}"
    # 🔴 A COUNT IS NOT THE GATE — `enforce_admins` IS HALF OF IT. Required checks
    # with admin enforcement OFF do not stop an admin pushing straight to main,
    # which is the exact mechanism behind incident 2 (`837d3fde`, a direct push).
    # So a non-zero count alone is not an all-clear.
    #
    # 🔴 AND IT MUST BE READ FROM THE PROTECTION ENDPOINT, NOT THIS ONE. Measured
    # 2026-08-29: `/branches/main` reports `.protection.enforce_admins` ABSENT for
    # innovation-upstream/devrc — so `// false` yields **false** — while
    # `/branches/main/protection` reports `true` for the same branch at the same
    # moment. Keying on the branch endpoint's value would have fired rc 24 on our
    # own healthy repo, every run: a permanently-red gate created by the fix for a
    # blind spot. A second call is the price of reading the field that exists.
    BP_ENF="$(timeout "$DRIFT_GH_TIMEOUT" "$DRIFT_GH" api "repos/$BP_SLUG/branches/main/protection" --jq '.enforce_admins.enabled' 2>/dev/null)"
    case "$BP_ENF" in
      true)
        echo "[protect]   enforce_admins=true — the checks bind admins too."
        ;;
      false)
        echo "[protect] 🔴 DRIFT — $BP_SLUG main requires $BP_N check(s) but enforce_admins is FALSE."
        echo "[protect]   Half a gate. An admin — the actor in both 2026-08-29 occurrences — can"
        echo "[protect]   push straight to main past every required check. This is the mechanism"
        echo "[protect]   that would have let 837d3fde through even with the checks present."
        echo "[protect]   fix: PUT /repos/$BP_SLUG/branches/main/protection with enforce_admins true."
        note_rc 24
        ;;
      *)
        echo "[protect]   enforce_admins=UNKNOWN — the protection endpoint did not answer."
        echo "[protect]   NOT read as 'admins are bound'. No rc is set for this half."
        ;;
    esac
  else
    # 🔴 ZERO CLASSIC CHECKS IS NOT YET DRIFT — RULESETS ARE A SECOND, NEWER
    # MECHANISM AND THIS ENDPOINT CANNOT SEE THEM. Measured 2026-08-29:
    # `astral-sh/uv` reads `protected=true, contexts=[]` here while carrying a
    # ruleset whose types include `required_status_checks` — a fully gated branch
    # that the classic read calls wide open. GitHub's UI now steers restores
    # toward rulesets, so the most likely next repair of THIS repo produces
    # exactly that state, and rc 24 would then fire on every 6-hourly run
    # forever: the DND-bypassing toast 4x/day, i.e. the permanently-red gate this
    # file refuses everywhere else. So ask the ruleset endpoint before deciding.
    # 🔴 COUNTING RULE DECLARATIONS IS NOT READING THE GATE — the same mistake as
    # counting classic contexts without `enforce_admins`, reintroduced on the path
    # added to fix it. A `required_status_checks` rule can list ZERO checks, can
    # sit in a ruleset whose `enforcement` is not `active`, and can carry
    # `bypass_actors` letting an admin push straight past it — which is the exact
    # mechanism of incident 2. So the rule must list at least one check, and the
    # ruleset it belongs to must be active with nobody bypassing it.
    #
    # `/rules/branches/main` exposes `parameters` but NOT `bypass_actors`; the
    # ruleset DETAIL endpoint carries both and is readable without repo-admin
    # (measured on astral-sh/uv: `enforcement=active, bypass_actors=0`).
    BP_RULES_RAW="$(timeout "$DRIFT_GH_TIMEOUT" "$DRIFT_GH" api "repos/$BP_SLUG/rules/branches/main" --jq '[.[]|select(.type=="required_status_checks" and ((.parameters.required_status_checks//[])|length)>0)] | "\(length) \([.[].ruleset_id]|unique|join(","))"' 2>/dev/null)"
    # 🔴 ONE RULESET IS NOT ALL RULESETS. This endpoint returns the rules from
    # EVERY ruleset that applies, repo- and ORG-level mixed (measured live on
    # astral-sh/uv, whose list interleaves `ruleset_source_type` Organization and
    # Repository). Taking `.[0].ruleset_id` let a single ruleset decide the
    # verdict for all of them: an org-level ruleset in `evaluate` mode sorting
    # first — the standard org rollout pattern — made a genuinely gated branch
    # read `enforcement=evaluate, not active` and fire rc 24 every 6h forever.
    # That is a FALSE DRIFT, and a false drift on this arm is the permanently-red
    # gate its own header refuses. So collect every id and let ANY gating ruleset
    # settle it.
    BP_RULES=""; BP_RULE_IDS=""
    read -r BP_RULES BP_RULE_IDS <<EOF
$BP_RULES_RAW
EOF
    case "$BP_RULES" in ''|*[!0-9]*) BP_RULES=-1 ;; esac
    case "$BP_RULE_IDS" in *[!0-9,]*) BP_RULE_IDS="" ;; esac
    if [ "$BP_RULES" = -1 ]; then
      # 🔴 The one case that must NOT become a finding: classic says zero and the
      # ruleset half could not be read, so "unprotected" and "protected by a
      # ruleset I cannot see" are indistinguishable. Firing here would be the
      # empty-answer-is-zero trap one level up.
      echo "[protect] COULD NOT MEASURE — $BP_SLUG main has 0 CLASSIC required checks, and the"
      echo "[protect]   ruleset endpoint did not answer, so a ruleset gate cannot be ruled out."
      echo "[protect]   This is NOT 'main is unprotected' and NOT 'main is protected'. No rc."
    elif [ "$BP_RULES" -gt 0 ]; then
      # The rules list checks. Now ask whether ANY ruleset holding one actually
      # BINDS — active, with no bypass actors. Every id is examined, because one
      # non-binding ruleset among several says nothing about the others.
      BP_GATED=0; BP_GATE_ID=""; BP_UNREADABLE=0; BP_SEEN=0; BP_CAPPED=0; BP_WHY=""
      for BP_ID in $(printf '%s' "$BP_RULE_IDS" | tr ',' ' '); do
        BP_SEEN=$((BP_SEEN + 1))
        if [ "$BP_SEEN" -gt "$DRIFT_GH_RULESET_MAX" ]; then BP_CAPPED=1; break; fi
        BP_RS_RAW="$(timeout "$DRIFT_GH_TIMEOUT" "$DRIFT_GH" api "repos/$BP_SLUG/rulesets/$BP_ID" --jq '"\(.enforcement) \((.bypass_actors//[])|length)"' 2>/dev/null)"
        BP_RS_ENF=""; BP_RS_BYPASS=""
        read -r BP_RS_ENF BP_RS_BYPASS <<EOF
$BP_RS_RAW
EOF
        case "$BP_RS_BYPASS" in ''|*[!0-9]*) BP_RS_BYPASS=-1 ;; esac
        if [ -z "$BP_RS_ENF" ] || [ "$BP_RS_BYPASS" = -1 ]; then
          BP_UNREADABLE=$((BP_UNREADABLE + 1))
        elif [ "$BP_RS_ENF" != active ]; then
          BP_WHY="$BP_WHY $BP_ID=enforcement:$BP_RS_ENF"
        elif [ "$BP_RS_BYPASS" -gt 0 ]; then
          BP_WHY="$BP_WHY $BP_ID=bypass-actors:$BP_RS_BYPASS"
        else
          BP_GATED=1; BP_GATE_ID="$BP_ID"; break
        fi
      done
      if [ "$BP_GATED" = 1 ]; then
        echo "[protect] $BP_SLUG main: 0 classic required checks, but ruleset $BP_GATE_ID gates it"
        echo "[protect]   (enforcement=active, 0 bypass actors, and its required_status_checks rule"
        echo "[protect]   lists at least one check). The gate is ON, by the newer mechanism."
      elif [ "$BP_UNREADABLE" -gt 0 ] || [ "$BP_CAPPED" = 1 ]; then
        # 🔴 NOT DRIFT. No ruleset was PROVEN to gate, but one we could not read
        # may. "Unprotected" and "protected by a ruleset I could not examine" are
        # the same observation, which is the empty-answer-is-zero trap again.
        echo "[protect] COULD NOT MEASURE — $BP_SLUG main has 0 CLASSIC required checks and"
        echo "[protect]   $BP_RULES ruleset rule(s), but none was PROVEN to bind:"
        [ "$BP_UNREADABLE" -gt 0 ] && echo "[protect]     $BP_UNREADABLE ruleset(s) could not be read"
        [ "$BP_CAPPED" = 1 ] && echo "[protect]     stopped after $DRIFT_GH_RULESET_MAX ruleset(s) (DRIFT_GH_RULESET_MAX)"
        [ -n "$BP_WHY" ] && echo "[protect]     examined and not binding:$BP_WHY"
        echo "[protect]   NOT read as protected, and NOT as drift. No rc is set."
      else
        echo "[protect] 🔴 DRIFT — $BP_SLUG main has 0 classic required checks, and NONE of its"
        echo "[protect]   $BP_RULES required-checks ruleset(s) binds:$BP_WHY"
        echo "[protect]   A ruleset in evaluate/disabled mode REPORTS and does not block; one with"
        echo "[protect]   bypass actors is the ruleset spelling of enforce_admins=false, letting"
        echo "[protect]   whoever is listed push straight past every check — the mechanism behind"
        echo "[protect]   837d3fde. Either way nothing gates main."
        echo "[protect]   fix: set enforcement=active and remove the bypass actors on one of them."
        note_rc 24
      fi
    else
    echo "[protect] 🔴 DRIFT — $BP_SLUG main has ZERO required status checks (protected=$BP_PROT),"
    echo "[protect]   by CLASSIC protection and by RULESETS alike — both were checked."
    echo "[protect]   The gate every change to this fleet passes through is OFF: anything can"
    echo "[protect]   land on main, and both hosts converge to main. Measured TWICE on"
    echo "[protect]   2026-08-29, once leaving a DIRECT PUSH on main that required checks"
    echo "[protect]   would have rejected. Neither was detected by anything but a human."
    if [ "$BP_PROT" = true ]; then
      echo "[protect]   protected=true: the protection object stands and required_status_checks"
      echo "[protect]   was deleted out of it — the exact break-glass shape. 🔴 PATCH CANNOT"
      echo "[protect]   restore it; it returns non-zero and changes nothing. Use a full PUT of"
      echo "[protect]   /repos/$BP_SLUG/branches/main/protection, then READ IT BACK — a restore"
      echo "[protect]   that has never been run is a hypothesis, and that is how this was missed."
    else
      echo "[protect]   protected=false: there is no protection object at all, so this is a"
      echo "[protect]   create rather than a restore. Same endpoint, full PUT, read it back."
    fi
    # 🔴 The DIAGNOSTIC must be runnable in the state it is printed in. The
    # protection endpoint 404s with `Branch not protected` exactly when
    # protected=false, so pointing an operator at it there hands them an error
    # under pressure. Name the endpoint that answers in each case.
    if [ "$BP_PROT" = true ]; then
      echo "[protect]   read the live state: gh api /repos/$BP_SLUG/branches/main/protection"
    else
      echo "[protect]   read the live state: gh api /repos/$BP_SLUG/branches/main --jq .protection"
      echo "[protect]   (…/branches/main/protection 404s 'Branch not protected' in this state)"
    fi
    echo "[protect]   rulesets, the other mechanism: gh api /repos/$BP_SLUG/rules/branches/main"
    note_rc 24
    fi
  fi
fi
echo

# ── FUZZYCLAW PHASE-2 GATE ────────────────────────────────────────────────────
# See "THE FUZZYCLAW PHASE-2 GATE" in the header for what this measures and why
# it is local-only. Here: how it refuses to produce a silent zero.
#
# 🔴 EVERY NON-MEASUREMENT IS A REASON, NEVER A COUNT. The gate reads ONE line
# from lib/drift_phase2.py whose first field is `ok` or a reason token, and it
# branches on THAT — not on the numbers beside it. Without the token every
# failure here (no session-manager on this checkout, a crashed scan, a scan of
# the wrong host, fuzzyclaw not actually read) arrives as `0`, and `0` is the
# value that means "phase 2 is ready". A checker whose broken state and whose
# all-clear are the same output is the vacuous green this whole file exists to
# refuse, and it would be handing over a DELETION.
#
# 🔴 AND A REAL ZERO IS ONLY REAL OVER A NON-ZERO DENOMINATOR — the same rule
# the managed-symlink scan applies with `examined=` beside `dangling=`. `0 of 0`
# is a scan that walked nothing; it prints as COULD NOT MEASURE and sets no rc.
echo "=== fuzzyclaw phase-2 readiness ($LOCAL_ROLE) ==="
if [ "$DO_LOCAL" = 0 ]; then
  # The remote leg cannot answer this: fuzzyclaw is local state, so a scan of
  # the other host reports 0 for a reason that has nothing to do with readiness.
  echo "[phase2] NOT EVALUATED — --no-local, and this gate is LOCAL-ONLY (fuzzyclaw"
  echo "[phase2]   task files are local state). Not a zero, and not a pass."
elif [ ! -x "$DRIFT_SESSION_MANAGER" ]; then
  echo "[phase2] COULD NOT MEASURE — no executable session-manager at $DRIFT_SESSION_MANAGER."
  echo "[phase2]   This is NOT a zero and NOT 'phase 2 is ready'."
elif [ ! -r "$_drift_phase2_py" ]; then
  echo "[phase2] COULD NOT MEASURE — cannot read $_drift_phase2_py."
  echo "[phase2]   This is NOT a zero and NOT 'phase 2 is ready'."
else
  # `--no-capture` and `--no-ch`: this needs `age_source` only, and the pane
  # capture is the expensive part of a scan. `--fuzzyclaw` is REQUIRED — the
  # index is opt-in, and without it every age falls to the ledger and the count
  # is a guaranteed zero. drift_phase2.py re-checks that it was actually read.
  p2_out="$(timeout "$DRIFT_PHASE2_TIMEOUT" "$DRIFT_SESSION_MANAGER" scan --json --no-ch --no-capture --fuzzyclaw --host "$LOCAL_ROLE" 2>/dev/null | python3 "$_drift_phase2_py" "$LOCAL_ROLE" 2>/dev/null)"
  p2_token="${p2_out%% *}"
  p2_rest="${p2_out#* }"
  p2_rows="${p2_rest%% *}"
  p2_fz="${p2_rest##* }"
  # A reader that printed nothing, or something that is not `<token> <int> <int>`,
  # is itself a could-not-measure — not a zero. Checked before any comparison,
  # because `[ "" -gt 0 ]` is an error, not a false.
  #
  # 🔴 THE FIELD **COUNT** IS PART OF THAT, AND CHECKING ONLY THE FIELDS' SHAPE
  # WAS NOT ENOUGH. These expansions read positionally, and `${x%% *}` / `${x##
  # * }` both fall back to the WHOLE STRING when there is no space — so a
  # two-field line `ok 47` set BOTH counts from the same field and rendered
  # `47 of 47 row(s) EXAMINED`, a well-formed-looking measurement that is one
  # number read twice. It is fail-safe by luck (it reads as NOT READY, never
  # READY) and it is still a fabricated denominator. `p2_rest` must therefore
  # contain a space (>=3 fields) and must not contain a second one (<=3).
  case "$p2_rest" in *' '*) ;; *) p2_rows=-1 ;; esac
  case "${p2_rest#* }" in *' '*) p2_rows=-1 ;; esac
  case "$p2_rows" in ''|*[!0-9-]*) p2_rows=-1 ;; esac
  case "$p2_fz" in ''|*[!0-9-]*) p2_fz=-1 ;; esac
  if [ -z "$p2_out" ] || [ "$p2_rows" = -1 ] || [ "$p2_fz" = -1 ]; then
    echo "[phase2] COULD NOT MEASURE — the scan produced no usable counts (reason: ${p2_token:-no-output})."
    echo "[phase2]   This is NOT a zero and NOT 'phase 2 is ready'."
  elif [ "$p2_token" != ok ]; then
    # The counts EXIST but the reader says they cannot be trusted (fuzzyclaw was
    # not actually read, or the scan hit the wrong host). Printed anyway, beside
    # the reason, so the finding is legible without being acted on.
    echo "[phase2] COULD NOT MEASURE — reason: $p2_token (raw: $p2_fz of $p2_rows row(s))."
    echo "[phase2]   This is NOT a zero and NOT 'phase 2 is ready'."
  else
    echo "[phase2] fuzzyclaw-only ages: $p2_fz of $p2_rows row(s) EXAMINED"
    if [ "$p2_rows" = 0 ]; then
      echo "[phase2] COULD NOT MEASURE — 0 rows examined. A zero over zero rows is a scan"
      echo "[phase2]   that walked nothing, not a measurement. NOT 'phase 2 is ready'."
    elif [ "$p2_fz" -gt 0 ]; then
      echo "[phase2] NOT READY — $p2_fz of $p2_rows row(s) still take their age ONLY from"
      echo "[phase2]   fuzzyclaw. Removing the readers now would blank those ages. The count"
      echo "[phase2]   decays as those pre-deploy sessions restart; nothing to do but wait."
    else
      echo "[phase2] 🔴 READY — 0 of $p2_rows rows depend on fuzzyclaw for an age."
      echo "[phase2]   Phase 2 is UNBLOCKED: remove the fuzzyclaw readers from"
      echo "[phase2]   session-manager, tmux-claude-counters.sh, verify-agent-work and"
      echo "[phase2]   validation/reconcile.py + refsources.py. This stays reported until"
      echo "[phase2]   they are gone — that is the gate working, not the gate stuck."
      note_rc 16
    fi
  fi
fi
echo

# 🔴 The summary states WHAT WAS CHECKED. It previously said "both hosts on
# branch main at origin/main" regardless — including for a --no-remote run that
# looked at one host, and for a --no-local --no-remote run that looked at none.
#
# 🔴 rc 16 TAKES **BOTH** BRANCHES, and that is not a convenience. It is the one
# owned code that is NOT a statement about host health — nothing is out of sync
# — so suppressing the affirmative line for it withheld the very finding the run
# DID make ("no drift on the host(s) CHECKED") and printed only the cleanup
# notice. An operator then cannot tell an rc 16 over a clean host from an rc 16
# over a host nobody vouched for. Both claims are true and independent, so both
# are printed. Pinned by `test_the_phase2_ready_run_still_prints_the_no_drift_line`.
if [ "$rc" = 0 ] || [ "$rc" = 16 ]; then
  if [ -n "$CHECKED" ]; then
    # 🔴 No phrasing here may name a host this run did not contact — the wording
    # it replaced said "both hosts" unconditionally, and read as coverage the run
    # never had (`test_a_single_host_run_does_not_claim_both_hosts`). The same
    # trap now exists one level down: the CROSS-HOST comparison needs a fact set
    # from each machine, so a clean rc here is not a claim that it ran.
    echo "drift-check: no drift on the host(s) CHECKED: $CHECKED — each on branch main at"
    echo "  origin/main, with every managed symlink resolving. The CROSS-HOST comparison is"
    echo "  a separate claim: read the [parity] block above for whether it ran at all."
  else
    # 🔴 CHECKED NOTHING, AND SAID SO — but used to hand systemd a 0 anyway.
    # Reachable as `--no-local` with the remote unreachable BELOW the escalation
    # threshold: the text says "this is not a clean bill of health" and the exit
    # code says "clean". systemd reads the code. It is the same shape the
    # `--no-local --no-remote` refusal above is already rc 2 for, so it gets the
    # same code: a run that observed no host is a usage outcome, not a verdict.
    #
    # 🔴 GUARDED ON rc IN {0, 16}, deliberately. This can only ever turn a
    # "nothing is wrong" code into a 2 — it can never rewrite a DRIFT verdict,
    # so an rc 8 stays 8 and still reaches OnFailure.
    # (`test_local_rc8_still_wins_when_the_remote_is_unreachable` and
    # `test_checked_nothing_does_not_rewrite_a_real_verdict`.)
    #
    # rc 16 reaching here is unreachable rather than handled: 16 is the LEAST
    # severe owned code, so it survives `note_rc` only when the local leg was
    # clean, and a clean local leg is what puts the host in $CHECKED. If that
    # ever changes, 2 ("observed no host") is the right answer over 16 ("a
    # cleanup is safe") — the reason this branch exists is that a run which
    # vouched for nothing must not hand systemd a code that reads as fine.
    #
    # NOT reachable from the timer, whose ExecStart passes no flags — with both
    # legs on, an unreachable remote still leaves the local host CHECKED. This
    # is consistency between the two "looked at nothing" paths, not a live bug.
    echo "drift-check: NO HOST WAS SUCCESSFULLY CHECKED — this is not a clean bill of health."
    echo "  refusing to exit 0 for a run that produced no verdict about any host."
    [ -n "$UNCHECKED" ] && echo "drift-check: NOT checked: $UNCHECKED"
    exit 2
  fi
fi
if [ "$rc" != 0 ]; then
  # 🔴 THE VERDICT WORD IS ITSELF A CLAIM. rc 16 is not drift — nothing is out of
  # sync and no host needs repairing; a cleanup became possible. Printing "DRIFT"
  # for it would send the operator hunting a divergence that does not exist, and
  # would blunt the word for the codes that DO mean it.
  verdict="DRIFT"
  [ "$rc" = 16 ] && verdict="ACTIONABLE (not drift)"
  echo "drift-check: $verdict (rc=$rc) — see per-host lines above."
  echo "  checked: ${CHECKED:-none}"
  echo "  rc3=no-repo  rc4=fetch/origin-main-unavailable  rc6=host-unidentified"
  echo "  rc8=DIVERGED/AHEAD:un-pushed-commits(ship.sh will skip this host forever)"
  echo "  rc10=behind(needs a ship)  rc12=not-on-branch-main"
  echo "  rc13=remote unreachable for >=$DRIFT_UNREACHABLE_ESCALATE consecutive runs"
  echo "  rc14=managed symlinks resolve to nothing (needs a home-manager switch on that host)"
  echo "  rc15=host parity: settings.json key sets / enabledPlugins differ, or enabled-but-not-installed"
  echo "  rc16=NOT drift: the fuzzyclaw phase-2 gate OPENED (0 rows depend on fuzzyclaw for an age)"
  echo "  rc17=the srcDir SUBTREE a nix/pkgs package is BUILT FROM is behind/ahead its own upstream"
  echo "       on that host. A repo that is behind OUTSIDE every srcDir is reported, never rc 17."
  echo "       (ranks between rc8 and rc14 — the digit is not the severity; see severity() )"
  echo "  rc18=a built-source scope has been UNMEASURABLE for N consecutive runs on that host"
  echo "       (no upstream / fetch failing), so rc17 cannot fire for it. repo ABSENT never"
  echo "       escalates. (ranks just under rc13 — same 'could not look' class, smaller scope)"
  echo "  rc22=a host's deployed skillOverrides disagree with claude/skill-tiers.json. A host"
  echo "       with NO overrides is NOT ADOPTED, never rc22 — that is the shipped state."
  echo "       (ranks just under rc10 — a behind host carries a stale ledger; ship it first)"
  echo "  rc23=an UNTRACKED file in a path NIX READS has survived $DRIFT_NIXDIRT_ESCALATE consecutive runs on that"
  echo "       host — LIVE (a mkOutOfStoreSymlink target, being served right now) or DROPPED"
  echo "       (nix reads the path, but the flake filtered the untracked file OUT of the"
  echo "       artifact — unsaved work, not a deployed one). A host whose nix-read set came EMPTY"
  echo "       is COULD NOT MEASURE, never rc23. (ranks between rc15 and rc12; see severity() )"
  echo "  rc24=main on the canonical remote has ZERO required status checks — the merge gate"
  echo "       is OFF for the branch both hosts converge to. protected=true means the checks"
  echo "       were deleted out of a standing protection object (PATCH cannot restore it; PUT"
  echo "       the whole thing and read it back). A gh that cannot answer is COULD NOT"
  echo "       MEASURE, never rc24. (ranks between rc8 and rc17 — the LARGEST digit here"
  echo "       ranks SECOND; see severity() )"
fi
[ -n "$UNCHECKED" ] && echo "drift-check: NOT checked: $UNCHECKED"
exit "$rc"
