# Handoff: cairn-oss-multi-instance — 2026-09-05

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Spin `cairn` out into a public OSS repo that both a personal and a **civitai team**
instance build from, then stand up that second instance so client notes live on client
infrastructure. Decided by the operator over three rounds of questions; the full design
is the PRIVATE proposal, not this doc.

## State now

- **`ZacxDev/cairn` EXISTS, is PRIVATE, `main` = `b25abb5`.** 6 commits. Suite **1647
  passed / 0 failed**; `tests/leakscan.py` rc 0 across 33 files with its own controls green.
- **Phase A1 (extraction) DONE.** Server + client + shared libs + tests, MIT, fresh
  history (no imported commits), GitHub Actions CI (leakscan job runs BEFORE tests so a
  test failure cannot mask it; the test job asserts a collected-test floor).
- **Phase A2 (token hot-reload) DONE and MERGED** — cairn PR #1, squash `b25abb5`,
  verified by CONTENT not ancestry.
- **Proposal PR OPEN, NOT merged:** `civitai/talos-infra` **#1414**, branch
  `docs/cairn-civitai-instance-proposal`, worktree at `~/workspace/civit/dp-cairnprop`.
- **devrc `claudedocs/handoff-cairn-phase3.md` ranks 1 and 10 closed** this session via
  devrc #1294 (`b8107b5aa`) and homelab-infra #683 (`93e15ee09`). Both merged and verified.
- 🔴 **NOT deployed anywhere.** No civitai instance exists; no image published; devrc does
  NOT yet consume cairn. The homelab pod still runs its own copy of the code.
- 🔴 **`ZacxDev/cairn` is PRIVATE by deliberate choice** — publishing is irreversible and
  was left as the operator's call. Everything else in A1/A2 was built so that flip is a
  one-step decision, not a project.

### What the extraction actually cost, measured
`subsystem_touch.py` (6,654 lines, the origin's local authoring tool) was NOT extracted.
Its borrowed surface measured **350 lines of 6,654 (5.3%)** and split cleanly: six symbols
used by the library, 327 lines used only by tests of the authoring CLI. The dependency was
**inverted** — `lib/entry_shape.py` + `lib/host_identity.py`, **379 lines replacing 6,654**
— so an authoring tool now depends on the store's format rather than the reverse.

`cairn who` was REMOVED from the client: it shells into tmux on named hosts to answer
"which sessions worked a task", which is session forensics about one operator's machines,
not an operation on a store. Removing it also removed the last private infrastructure baked
into the INTERFACE rather than a comment (`--host` had `choices=[<two real machine names>]`,
so every `--help` printed them), and fixed **81 of 107** then-failing tests.

## Open investigations — live diagnosis state

### A full-suite-only intermittent in cairn's test suite, unattributed
- **Symptom + exact repro:** no reliable repro.
  `TestTheDeployedEntrypoint::test_a_TWO_LINE_token_file_authorises_BOTH_lines` failed
  **once in ~25 full-suite runs**, only ever in a full run.
- **Observed (with values):** first full run at PR head `90d30ab` → `1614 passed, 1 failed`.
  Same test then passed **3/3 alone**, **735/735 in its own file**, **5/5 paired with the
  new SIGHUP tests**, and the immediately following full run → `1615 passed, 0 failed`.
  Every subsequent full run (rounds 2-4, five more) was clean.
- **Ruled out:** that the SIGHUP work caused it — under `-p no:randomly` the new tests
  execute ~11,400 lines AFTER it in the file, the only PR change ordered before it is a
  `running()` → `serving()` extraction that this test does not use (it uses
  `running_subprocess`), plus an autouse signal-disposition fixture. via: measurement
- **Ruled out:** a load flake of the ordinary kind — wall time did not show the ~15×
  inflation that marks contention; the failing run was 531 s against a 412-522 s band.
  via: measurement
- **Leading hypothesis:** genuinely pre-existing and order/timing dependent, inherited from
  the origin repo rather than introduced here. Not confirmed.
- **Next probe:** run the full suite N times on a quiet box and get a RATE, e.g.
  `for i in $(seq 10); do cd ~/workspace/cairn && nix develop ~/workspace/devrc -c python3 -m pytest tests -q -p no:randomly 2>&1 | tail -1; done`
  A rate is what turns this into either "fix it" or "it does not exist".

### Two ledger guards in cairn are narrower than their own sentences — left OPEN by decision
- **Symptom + exact repro:** read `tests/test_subsystem_store_api.py:20239` and `:20271`
  against their docstrings.
- **Observed (with values):** (a) the fail-closed raise-site walk is
  `if isinstance(exc, ast.Call) and exc.args:` — `raise TokenError`, `raise ValueError()`
  and a bare re-raise are still **silently dropped**, while the docstring says an unreadable
  message is "reported as UNCLAIMED rather than dropped". (b) `_EMITTER_ATTRS` matches
  `.write`/`.writelines` on **any receiver**; `server/server.py:2287` and `:2338` are
  **binary** `fh.write(data)` calls one module-level caller away, so a future module-level
  startup helper that writes would make the ledger demand `reload_safe` on bytes.
- **Ruled out:** that either is a hole in the property the ledger guards — a message-less
  raise cannot echo a field value, and nothing reaches the binary writers today. via: code
- **Leading hypothesis:** both are the same class the whole audit ladder was about (a
  description claiming coverage the body does not provide), one notch smaller, and neither
  ships a defect. Recorded on cairn PR #1 as open-by-decision so they read as open, not absent.
- **Next probe:** none needed. Narrow `_EMITTER_ATTRS` to named sinks (`sys.stdout`/`sys.stderr`)
  and extend the fail-closed arm to non-`Call` raises, in one commit, when someone is next in
  that file.

## Next steps (ranked)

🔴 **Numbering is stable and is half a claim's identity** (`claim-work --slug-for <this doc> <rank>`).
Items are marked done IN PLACE; new items APPEND.

1. **Decide whether `ZacxDev/cairn` goes PUBLIC.** It is private today. Everything in A1
   was built for this flip — MIT licence, fresh history, a security-scoped leak gate with
   four control classes, CI. Nothing else in the programme depends on it, so it can wait
   indefinitely, but the OSS repo is the reason the image problem is solved.
   forcing: user — the operator chose the OSS route explicitly; only they can publish

2. **Retire the now-false comment in `homelab-talos`
   `clusters/homelab/apps/subsystem-store/deployment.yaml:269`.** It carries a 🔴 operational
   instruction saying `load_tokens` runs once at startup and *"a secret edit is inert until
   the pod is replaced"*. Cairn PR #1 makes that false. **Do it in the same commit that bumps
   the image tag**, or the next operator reads it and replaces the pod anyway.
   forcing: regression — a live operational instruction that is now wrong

3. **Phase A3 — devrc consumes cairn as a pinned flake input.** `nix/home.nix` currently
   deploys `scripts/cairn` as an out-of-store symlink (edits are live, no switch). A flake
   input changes that: client edits will need a `home-manager switch`. Real ergonomic trade,
   decided deliberately, and `readlink -f` stays the only arbiter of which state a path is in.
   forcing: none

4. **Merge or close `civitai/talos-infra` #1414** (the instance proposal). It has four open
   questions in §11 — teammate count and identities, hostname, who else administers the token
   file, and whether the OSS repo accepts outside contributions from day one. None blocks A3.
   forcing: none

5. **Close the two open ledger 🟢s in cairn** (see the open investigation above). One commit.
   forcing: none

6. **Get a RATE for the full-suite intermittent** (see the open investigation above), then
   either fix it or record that it does not reproduce.
   forcing: none

## Gotchas / decisions / dead-ends

**Operator decisions this session, all acted on — do not re-litigate:**
- Sanitisation is scoped to **security, not tidiness**. Project names and dates ship as-is.
  🔴 This was a CORRECTION of my own over-engineering: I had built a gate finding **480**
  issues of which **4** mattered. The three cosmetic rules were DELETED, not demoted — a gate
  firing 476 times for nothing is one somebody switches off, and then the 4 ship too.
- Extraction scope: server + client + shared libs + tests. History: **fresh start**, one
  initial commit — the only way "the history is clean" is true by construction rather than by
  audit, since devrc's four content gates read `git ls-files` and are blind to history.
- Comments: **keep the mechanism, drop the particulars.** MIT. GitHub Actions.
- Routing (for the future multi-instance client): an explicit scope→instance registry that
  **FAILS LOUD** on an unregistered scope. A default silently recreates the write-to-a-dead-store
  shape that cost six entries in phase 3.

**🔴 A STALE BLOCKER COSTS MORE THAN AN UNKNOWN ONE.** Two of the proposal's five blockers
evaporated on contact with the code, and both had been written down as facts. "The API has no
create route" was quoted from a handoff note that predated the change closing it — `PUT` with
`If-None-Match: *` has created entries for some time, with 14 test references. "The authoring
tool must become instance-aware" assumed a module had to come along that supplied 5.3% of what
was needed. **Nobody re-checks a thing already written down**, so it survives every review and
shapes the schedule. Re-measure a blocker before scheduling work against it.

**🔴 A GREEN LEAK SCAN DOES NOT MEAN THE SANITISATION IS CORRECT.** A scripted pass cleared
430 of 481 findings and was **wrong in two ways the scanner happily passed**: a docstring's
opening `"""` swallowed everything to a date because the "string literal" regex used a negated
class that matches newlines, turning `MEASURED 2026-09-02` into `MEASURED 2000-09-02` — an
incident narrative in costume; and scope substitution produced `BUILT FROM alpha, NOT FROM
beta-infra`, which is meaningless. The tree was reverted byte-identical and done by hand. The
gate checks for tokens; it cannot tell that prose still means something.

**On the audit ladder (4 audit rounds + 4 fix rounds on cairn PR #1):**
- Round 1 found a 🔴: a live bearer token printed **verbatim to stdout** on a refused reload,
  from a process that stays healthy. Unconditional — `MIN_TOKEN_CHARS=43` exceeds
  `MAX_IDENTITY_CHARS=32`, so a token in the identity field always trips that guard.
- Round 2 found **the same defect class alive in a guard round 1 declared closed** (guard 11,
  via the scope field). That is the entire argument for not stopping at the first green.
- 🔴 **Three pre-existing tests actively PINNED the leak** — they asserted the credential
  appeared in the message. The suite did not merely miss it; it required it.
- 🔴 **And the leak predicate itself was blind**: it checked `secret in text` over the RAW
  token while the guard printed it FOLDED, so even a correct fixture would have passed.
- The ladder was stopped on the **attribution gate**, not on a verdict: round 4's fixes changed
  **zero** lines of `server/server.py`, and round 5's would have too.

**Traps paid for, do not re-pay:**
- 🔴 **Do not edit source while a pytest run is in flight** in cairn — the hang classifier greps
  frames' source-line TEXT, which `traceback.format_stack` re-reads from disk at report time, so
  shifted line numbers misclassify and produce false failures.
- A bare `python3 -m pytest` in `~/workspace/cairn` fails `No module named pytest` — that is the
  shell, not the repo. Use `nix develop ~/workspace/devrc -c python3 -m pytest`.
- `cairn ls-entries --scope <x>` **silently ignores `--scope`** and returns the whole store.
- `civitai/talos-infra`'s pre-push gate fails with `python3 pyyaml missing` — that is an
  `error`, not a `failure`. Push from inside
  `nix-shell -p "(python3.withPackages(ps: [ps.pyyaml]))" git` and it passes.

## How to verify

```bash
# cairn: suite + the security gate, both from a clean checkout
cd ~/workspace/cairn && nix develop ~/workspace/devrc -c python3 -m pytest tests -q -p no:randomly
cd ~/workspace/cairn && python3 tests/leakscan.py            # rc 0, controls green
cd ~/workspace/cairn && python3 tests/leakscan.py --self-test # the controls alone

# the three defect shapes the audit ladder closed — all must refuse, neither verbatim nor folded
python3 - <<'PY'
import importlib.util,sys,pathlib,tempfile,os,secrets
spec=importlib.util.spec_from_file_location("srv","server/server.py"); m=importlib.util.module_from_spec(spec)
sys.modules["srv"]=m; sys.path.insert(0,"lib"); spec.loader.exec_module(m)
A,N=secrets.token_urlsafe(43),secrets.token_urlsafe(43)
for label,c in [("identity",f"{A} {N} alpha\n"),("scope",f"{A} zach {N}\n"),("dup",f"{A} zach alpha\n{A} zach {N}\n")]:
    with tempfile.TemporaryDirectory() as d:
        f=pathlib.Path(d)/"t"; f.write_text(c)
        try: m.load_tokens(f,dict(os.environ)); print(f"{label}: LOADED (!)")
        except Exception as e:
            msg=str(e); print(f"{label}: refused verbatim={N in msg} folded={N.lower().replace('_','-') in msg}")
PY
```
Expected: `1647 passed, 0 failed`; leakscan `0 findings across 33 files`; all three shapes
`refused verbatim=False folded=False`.
