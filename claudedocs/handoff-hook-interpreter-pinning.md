# Handoff: hook-interpreter-pinning — 2026-08-21

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Stop Claude Code hooks dying with `python3: command not found` during a
`home-manager switch` — and, the part that actually mattered, stop `bash-guard.py`
(a `PreToolUse` hook) from **failing open** for ~1s of every switch.

## State now — DONE, shipped, verified. Nothing in flight.

**Root cause.** `home-manager switch` updates `~/.nix-profile` as *remove-then-install*,
writing TWO nix profile generations per switch. The intermediate one drops every
`home.packages` binary:

| profile | bins | `python3` |
|---|---|---|
| `lrhqph9dw99whm3i1w22ngjsxydchx00-profile` | 337 | **ABSENT** |
| `7msd1dxhwg8g4800hf3wb72ar5i4yj8p-profile` | 625 | present |

Hooks were registered as bare `python3 ~/.claude/hooks/X.py`, so any firing in that
~1s window died. Looks unreproducible afterwards because the binary is there when you
go check. **Proof method** (reusable): correlate failure timestamps in
`~/.claude/projects/**/*.jsonl` against `~/.local/state/nix/profiles/profile-*-link`
mtimes. Four matches to the second, e.g. error `2026-08-20T17:52:00.994Z` vs links
written `17:52:00` and `17:52:01`. Host is CDT (−0500).

🔴 **The consequence, confirmed against the Claude Code hooks docs:** non-`0`/`2` exit
codes are classified non-blocking (event-independent) and exit `2` blocks on
`PreToolUse`. So a 127 from `bash-guard.py` meant `git add -A` / `reset --hard` passed
**unchecked**, several times a day, since July.

**Merged and shipped to BOTH hosts** (`ship.sh`, both switched, 0 dangling / 0 stale):

| PR | squash | what |
|---|---|---|
| #609 | `d3ce028` | pin every managed hook's interpreter to an absolute `/nix/store` path |
| #615 | `b4d6ecb` | rollback double-registration, `DEVRC_HOOK_PYTHON` validation, warn on unpinnable spellings |
| #626 | `3c54918` | `matcher` was wrongly in the de-dup identity on events that have none |
| #649 | `d990c9dc` | hook suites ran against the operator's REAL `$HOME` and deleted live nudge state |
| #652 | — | **CLOSED**, superseded by #650 (see Gotchas) |

**Mechanism:** `scripts/claude-hooks/register-nudge-hook.py` writes
`os.path.realpath(sys.executable)` into every managed hook command. The activation
wrapper already runs it *as* `${pkgs.python312}/bin/python3`, so no `home.nix` plumbing
changed. Store paths are immutable and GC-rooted by the current generation, so the
window is CLOSED, not narrowed. New hooks are covered automatically — `#631`'s
`clawgate-task-interview-guard.py` was pinned with no action from anyone.

**Live state, both hosts** (verified post-ship): 15 hook registrations, 15 pinned,
0 bare, **0 duplicates** per `(event, matcher, script)`. Deployed guard under an empty
`PATH` denies `git add --all` and allows a benign control.

## Next steps (ranked)

1. **Y1 — the new `$HOME`-isolation guard is walkable.** `scripts/tests/test_hook_suites_do_not_touch_the_inherited_home.py:82-93`. Its destructive half rests on hard-coded decoy strings tied to nothing. **Measured:** a hyphen-count-preserving rename of `TEST_SID_PREFIXES` (`scripts/claude-hooks/tests/test_search_tool_nudge.py:201`) applied to the *pre-change, still-destructive* suite made the guard **PASS**. Fix: `ast`-read the prefixes and require a decoy per prefix, plus `set(DECOYS) == set(ISOLATED_SUITES)`. Only item here with real teeth; it is future-drift, not a live defect.
2. **Y3 — the guard's title asserts a universal property, the body covers 2 of 12 suites** (`:1` vs `:63-66`). All 12 in `scripts/claude-hooks/tests/` currently measure `created=0 removed=0`; the sweep costs seconds, so widening `ISOLATED_SUITES` (or deriving it from the directory) is nearly free.
3. **Y2 — `test_the_ledger_names_only_files_that_exist` is narrower than its docstring** (`:260-270`): claims to be a positive control, body is `len(...) >= 2` plus `is_file()`.
4. **opencode's `scripts/opencode/plugin/guard.js:135`** — `process.env.DEVRC_GUARD_PYTHON || "python3"`, same mid-switch window. Fails **closed**, so not a fail-open hole, but every opencode bash call is refused during a switch. Needs a substitution derivation (`.source` → `.text` + placeholder) plus node-tier layout-test updates — deliberately not attempted.
5. **~57 stale agent worktrees** under `.claude/worktrees/`. Each pins a branch ref repo-globally at whatever commit it stopped on.

## Gotchas / decisions / dead-ends

- 🔴 **`git merge` inside a pipeline destroys the exit status.** `git merge … | tail -3` makes `set -e` read `tail`'s status, so a CONFLICTED merge reported success. I then gated a tree containing `<<<<<<<` markers and mis-reported the FAIL as a semantic conflict. **Read merge/rebase status directly; branch on the exit code, never a marker grep** (`git merge-tree` prints no markers at all).
- 🔴 **Check for an existing PR before fixing a red `main`.** `main` was red from #643 (a pin anchored on `**Read it fully.**`; the reword moved the closing `**`). I verified the breakage on a pristine tree and wrote #652 — but never asked whether someone was already on it. **#650 landed first and was better** (it adds `doc.count(...) == 1` so the anchor can't silently become ambiguous). "Is it broken?" and "is someone already fixing it?" are two questions.
- **Salvage from the closed #652:** the other three anchors in `test_subsystem_recall.py` still use bare `doc.index(...)`, which raises `ValueError: substring not found` naming no file, no anchor, no remedy — a contributing cause of the red `main` going unnoticed. Worth folding in if anyone touches that test.
- **`DirectoryAdded` — an unresolved source disagreement, decided unilaterally.** The delta auditor said it belongs in `NO_MATCHER_EVENTS`; the Claude Code hooks docs and the implementation put it in `MATCHER_EVENTS`. Kept as matcher-supporting: that is the conservative direction (matcher stays in the identity ⇒ declines to delete). One source still contradicts it.
- **Residual behaviour flip, unmeasured.** With the interpreter pinned, a hook whose *script* symlink is briefly absent during `linkGeneration` now exits `2` = **block** rather than `127` = non-blocking. Safer, but a spurious deny is now possible. That window's duration was never measured.
- **Client subdomains in reachable git history — do NOT re-raise as a 🔴.** Measured this session: 4,350 unique text blobs, **26 paths**, ~530 raw matches, gate `ALLOWLIST` empty. `SECRETS.md` → "Dead credentials in reachable history" has already adjudicated the *class*: history is deliberately not rewritten (it unpublishes nothing already cloned from a public repo and breaks every checkout). What is genuinely missing is only that `SECRETS.md` documents the history blind spot qualitatively and carries no hostname measurement.
- **Known flakes, do not attribute to a branch:** `scripts/tests/test_subsystem_store_api.py::TestTrustedProxyOverTheRealProcess::test_a_VALID_token_from_an_untrusted_peer_is_SERVED_but_bucketed_under_the_PEER` (SIGTERM racing a child's stdout flush).
- **This repo has NO automated merge gate** — `gh pr view <n> --json statusCheckRollup` returns 0 checks on every PR. The hand-run gate is the only evidence. `main` moved ~10× during this session; re-check file overlap and re-gate the tree that actually deploys.

## How to verify

```bash
# 1. every managed hook is pinned, on BOTH hosts, with no duplicates
python3 - <<'PY'
import json, collections, os
d=json.load(open(os.path.expanduser("~/.claude/settings.json")))
tot=pinned=bare=bad=0
for event, arr in d.get("hooks", {}).items():
    seen=collections.Counter()
    for entry in arr:
        for h in entry.get("hooks", []):
            c=h.get("command","")
            if ".claude/hooks/" in c:
                tot+=1
                pinned += c.startswith("/nix/store/")
                bare   += c.startswith("python3 ")
                seen[(entry.get("matcher"), c.split("/")[-1])]+=1
    for k,v in seen.items():
        if v>1: print("DUP", event, k, v); bad+=1
print(f"total={tot} pinned={pinned} bare={bare} duplicates={bad}")
PY
# expect: total=15 pinned=15 bare=0 duplicates=0   (15 will grow as hooks are added)

# 2. reproduce the ORIGINAL symptom against the DEPLOYED registration:
#    run the real command string with no python3 on PATH. Must DENY, not 127.
CMD=$(python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.claude/settings.json')));print([h['command'] for e in d['hooks']['PreToolUse'] for h in e.get('hooks',[]) if 'bash-guard' in h.get('command','')][0])")
printf '%s' '{"tool_name":"Bash","tool_input":{"command":"git add --all"},"hook_event_name":"PreToolUse"}' \
  | env -i HOME=$HOME PATH= /bin/sh -c "$CMD"
# expect: {"hookSpecificOutput": {... "permissionDecision": "deny" ...}}, exit 0

# 3. the registrar is a no-op on a healthy host (run against a COPY, never the live file)
```
Laptop: `ssh zach@192.168.50.155`. Gate: `nix develop <repo> --command bash <repo>/scripts/gate.sh --tier both --set all` — `.envrc` is only `use opencode` and does NOT carry `gateTools`; outside `nix develop` it aborts on missing `logrotate`, which is environment, not a code failure.
