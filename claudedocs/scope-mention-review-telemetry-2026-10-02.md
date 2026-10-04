# SCOPE — instrumenting the `mention-review` TUI into the activity pipeline

**Status: SCOPE ONLY.** No production code written, no PR opened, no clawgate/muster task
created, no privilege changed, nothing deployed, no file in any repo modified. Everything
below is a proposal plus the measurements that justify it.

- **Date:** 2026-10-02
- **Repos:** `innovation-upstream/devrc` (`/home/zach/workspace/devrc`) — read at
  `main` = `46e8dbc7`, working tree clean except two untracked `claudedocs/scope-chief-*.md`.
  The TUI source is vendored in-tree at `nix/pkgs/tools/mention-review/src` (Go module
  `github.com/innovation-upstream/devrc/mention-review`). Live binary `mention-review 0.3.0`
  at `/nix/store/6vcidbwf4xbnvpvms7apvx3khr34fbr6-mention-review-0.3.0/bin/mention-review`.
  Live reads against the activity ClickHouse (`activity_reader`, the endpoint in the
  `activity` skill's workbench row).
- **Trigger:** `scripts/mention-open.py` has emitted `source=tool kind=invocation` click
  telemetry since 2026-09-12. The TUI it launches emits nothing, so the expensive part of
  every review — the session itself — is dark.

---

## 0. TL;DR — what changed in my understanding of the problem

Six findings reframe the brief. Each is sourced and measured below. **Two of them are
corrections to numbers the brief told me not to re-derive**, which is why I re-derived them
anyway: the brief said *verify each before building on it*, and two did not survive.

1. 🔴 **The brief's "the TUI emits nothing" is TRUE, but the command it cites proves
   nothing — it is a false zero.** `grep -rln "activity|spool|telemetry|invocation"` has an
   unescaped alternation with no `-E`, so the pipe is matched **literally**. I ran the
   positive control: the same spelling for `"package|internal"` — tokens that are in almost
   every file — also returns **exit 1, no matches**. The instrument cannot match anything.
   The claim is nonetheless correct under a working command (§1.1).

2. 🔴 **`CLICK_DIM_FIELDS` is 14, not 16. There is headroom 2, not 0 — and the question the
   brief asks does not apply to this work at all.** The brief's "16 against `_MAX_DIMS` 16 —
   exactly full" is wrong, and `invocation.py:76-79` says so in the repo already: *"16 clears
   the widest current ledger (14) with room for two more fields."* **More importantly the cap
   is PER-EVENT, not a pool.** `sanitize_dims` runs once per `build_fields` call
   (`invocation.py:152`), so the TUI's row gets its own 16 slots and shares nothing with the
   click row. **Raising the cap is neither a decision nor a migration here — it is not
   required.** (§5)

3. 🔴 **`PIPE_BUF` is the wrong invariant, and both the brief and `spool_emit.py`'s own
   comment state it wrongly.** Measured: a **5,000-byte** single-write append — *over* the
   4,096 PIPE_BUF — produced **0 torn lines in 3,200**. A **586-byte** append split into
   **two** writes — well *under* PIPE_BUF — produced **618 torn lines in 3,200**. The
   invariant is **one `write()` syscall**, not "under PIPE_BUF". Getting this backwards is
   actively dangerous: it licenses a two-write implementation for small lines. (§3.2)

4. 🔴 **The highest-risk finding, and nothing in the brief points at it: the Go tier has NO
   spool isolation.** GUARD 8 exports `ACTIVITY_SPOOL_DIR` and `XDG_STATE_HOME` in
   `scripts/run-tests.sh:3109-3110`. Measured: that file references `ACTIVITY_SPOOL_DIR`
   **10** times; `scripts/run-go-tests.sh` references it **0** times, as does
   `run-node-tests.sh`. So **the moment a Go spool writer exists, its own Go tests append
   rows to the operator's real production dataset** — the exact leak GUARD 8 was built to
   close, through the one tier that postdates it. This must land in the *same* PR as the
   writer, not after. (§4.1)

5. 🔴 **The brief's latency numbers are mislabelled — it repeats the very retraction it
   warns about.** `1,212 → 922 → 735 ms` is **diff-readable**, not time-to-first-paint.
   First frame was `277 / 285 / 287 ms` across the same three configurations — which
   `app.go:794-797` labels **a NULL, not a cost**. The brief presents the diff-readable
   series under the words "time-to-first-paint", which is precisely the first-frame /
   diff-readable conflation that caused the earlier retraction. (§1.2)

6. 🔴 **The consumer side is already broken for the emitter that exists.**
   `adoption-scan.py`'s `ITEMS` ledger holds 9 ids and **`mention-open` is not one of them** —
   neither is `cairn`, which has **365** live rows. So mention-open's 189 rows reach
   ClickHouse and are invisible to the report that exists to read them. Emitting from the TUI
   without a consumer entry produces rows nobody reads. (§2.3)

**The headline cost, measured live: 174 TUI launches in 20 days, every one of them dark.**

---

## 1. Verification of the briefed measurements

The brief said: *do NOT re-derive, but DO verify each before building on it.* Results.

| briefed claim | verdict |
|---|---|
| The TUI emits nothing | **TRUE, but the cited command is a false zero** — §1.1 |
| `mention-open.py:2277` launches detached and does not wait | **CONFIRMED** — §1.3 |
| `invocation.py` is a thin, non-blocking wrapper; every failure swallowed | **CONFIRMED** — §1.4 |
| `_MAX_DIMS` is a self-imposed ledger, overflow is silent truncation | **CONFIRMED** (and the `dropped` counter makes it non-silent) — §5 |
| `CLICK_DIM_FIELDS` is 16 against `_MAX_DIMS` 16, exactly full | 🔴 **FALSE — it is 14 against 16** — §5 |
| Latency went `1,212 → 922 → 735 ms` time-to-first-paint | 🔴 **MISLABELLED — those are diff-readable** — §1.2 |
| `t_rest` 666 ms vs `t_graphql` 855 ms, REST first 5 of 5 | **CONFIRMED verbatim** — §1.2 |
| The source records the inversion as one host at one moment | **CONFIRMED verbatim** — §1.2 |
| The latency history is unreproducible | **CONFIRMED** — no committed measurement script exists |

### 1.1 "The TUI emits nothing" — true claim, broken instrument

The brief's command, run exactly as written, in the module root:

```bash
cd nix/pkgs/tools/mention-review/src
grep -rln "activity|spool|telemetry|invocation" .     # -> exit 1, no output
```

🔴 **Validate the instrument before reading its verdict.** `grep` on this host is a shell
function wrapping `ugrep`, and with no `-E` the `|` is a literal character. The negative
control for the instrument itself:

```bash
grep -rln "package|internal" .                        # -> exit 1, no output
```

Those tokens are in all 47 `.go` files. A pattern that cannot match `package|internal`
cannot match anything — **the briefed zero is a fact about the pattern, not about the TUI.**

The working command, enumerating rather than recursing (the `-r` form also honours
`.gitignore`, and this module *has* one — `vendor/`, which is build output):

```bash
find . -type f -name '*.go' -print0 | xargs -0 grep -lniE 'activity|spool|telemetry|invocation'
```

**4 files, 4 matches, all of them the word "invocation" in the CLI-argument sense:**

| file:line | text |
|---|---|
| `internal/argv/argv.go:41` | `// Args is a validated invocation.` |
| `internal/argv/argv_test.go:62` | `func TestAGoodInvocationSplitsOwnerAndName` |
| `internal/ghapi/ghapi_test.go:552` | `` `Client.detail` invocation and inspects no source `` |
| `internal/ghapi/detailrouting_test.go:20` | `` counts no `Client.detail` invocation `` |

Positive control that the sweep walked the tree: the same `find | xargs` with
`|package` appended returns **47 of 47** files.
`nix/pkgs/tools/mention-review/default.nix` matches none of the four tokens either.

**Verdict: the TUI emits no telemetry of any kind. Zero spool writers, zero Go dependency on
any collector path.** Independently corroborated live in §2.1: ClickHouse holds **0** rows
for `tool='mention-review'`.

### 1.2 The latency history — what the source actually says

`internal/ui/app.go:779-806`, verbatim structure:

```
                   in series   concurrent   + skeleton
diff readable       1,212 ms      922 ms       735 ms
first frame           277 ms      285 ms       287 ms
```

> `app.go:794-797`: *"⚠ THE SECOND ROW IS A NULL, NOT A COST. It is inside the run-to-run
> spread with no consistent direction across rounds. The first frame was already painted
> before any network call, so there was never anything there to win."*

🔴 **So the brief's series is the FIRST row wearing the SECOND row's name.** The brief then
warns, correctly, that an earlier *"~1.0s time-to-first-paint"* reading was retracted for
never distinguishing *first frame on screen* from *diff readable* — and then hands me the
diff-readable series labelled "time-to-first-paint". **The retraction recurred inside the
brief that documents it.** This is the strongest possible argument for §6's position that
these must be two separately named dims and never one number.

The leg split and its scoping comment both hold verbatim:

- `app.go:797-801`: *"t_rest median 666 ms against t_graphql 855 ms"*, REST first in 5 of 5.
  The two deltas close to 2 ms (`922-735=187`, `855-666=189`), which is what identifies the
  skeleton's early-diff paint as the mechanism rather than a correlation (`panels.go:68-76`).
- `app.go:803-806`: *"THE PROPOSAL'S M5/M6 FIGURES READ THE OTHER WAY ROUND (0.54–0.69 s
  GraphQL, 0.66 s REST) AND REASONING FROM THEM IS HOW THE SKELETON GOT REMOVED FROM THIS PR
  ONCE, BEFORE THE LEGS WERE RE-TIMED AND IT CAME BACK. A stored measurement is a claim about
  the day it was taken. Re-take it before building on it."*

🔴 **That is the whole argument for a trend, and it is stronger than the brief states.** It
is not merely that the inversion *could* reverse — **it has already been measured both ways
in this project's own history**, and reasoning from the stale direction deleted a working
optimisation once. If REST becomes the long pole, the skeleton's 187 ms goes to zero with
**nothing failing and no test reddening**: the 187 ms is a property of `max(t_graphql,
t_rest) - t_rest`, which is 0 when `t_rest` is the max. A trend is the only instrument that
can see that.

**Reproducibility: confirmed absent.** No committed script measures any of this —
`find /home/zach/workspace/devrc -name '*coldopen*' -o -name '*latency*'` returns no
non-`.go` file. The measurements exist only as prose in a Go comment, and
`app.go:782` records the method (*"driven headlessly in its own tmux socket, poll
granularity 12–16 ms"*) without the driver.

### 1.3 The detached launch — the finding that kills the attractive design

**CONFIRMED.** `scripts/mention-open.py`, `open_tui` at `:2213`, the spawn at `:2277-2280`:

```python
subprocess.Popen(
    ["alacritty", "--class", REVIEW_CLASS,
     "-e", REVIEW_EXE, repo, num],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
```

Three independent reasons the Python parent cannot emit on the TUI's behalf:

1. **No `wait()`, no `communicate()`, no returncode read.** The next statement is
   `return 0, CLICK_SURFACE_TUI` (`:2286`). `open_tui` returns a *launch* outcome.
2. **It is not even the TUI's parent in any useful sense.** `argv[0]` is `alacritty`;
   `mention-review` is `-e`, i.e. a grandchild inside a terminal emulator the display manager
   owns. The handler would have to reap through a process it does not control.
3. **Both streams are `DEVNULL`**, so there is no channel for the child to report through
   even if someone waited.

The TUI outlives its launcher by minutes to hours. **"One spool writer, in Python" is
dead.** The live click rows corroborate the shape: `mention-open`'s outcome for a TUI launch
is `picked` / `auto-open` — a *routing* verdict — never a session verdict (§2.1).

### 1.4 The non-blocking posture — confirmed, and it is mandatory

`scripts/collector/invocation.py:178-188`:

```python
def emit_invocation(tool, outcome, dims=None, duration_ms=None, exit_code=None,
                    spool_dir=None) -> str:
    try:
        import spool_emit as SE  # imported lazily so an absent module is a no-op
        ...
        return SE.emit(fields, ...)
    except Exception:  # noqa: BLE001 — best-effort telemetry never raises
        return ""
```

Plus `spool_emit.emit` (`:83-98`) swallowing `OSError` around the append itself. Measured
behaviour of the Python reference, which the Go port must match:

| situation | measured result |
|---|---|
| spool dir absent (3 levels deep) | **created** (`mkdir(parents=True)`, `:92`); line written; line returned |
| spool dir unwritable (parent `0o500`) | **no file, no raise**; line still returned |
| `spool_emit` module absent | no-op, returns `""` |

🔴 **A review session must never die for a telemetry write**, and the TUI makes this sharper
than any existing caller: the other emitters are batch scripts, but this one holds the
operator's half-composed PR comment in memory. A panic in an emit path loses *work*, not just
a row.

---

## 2. Measurements taken for this scope

All read-only. Live ClickHouse reads via `activity_reader` (SELECT-only).

### 2.1 The live `source=tool` roster — and the headline cost

```bash
P=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
    sops -d --extract '["stringData"]["reader-password"]' \
    ~/workspace/homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml)
Q() { curl -s --max-time 25 "$CH" --user "activity_reader:$P" --data-binary "$1"; }
Q "SELECT JSONExtractString(payload,'tool') AS tool, count() c, min(ts) first, max(ts) last
   FROM activity.events WHERE source='tool' AND kind='invocation'
   GROUP BY tool ORDER BY c DESC FORMAT TSVWithNames"
```

**Positive control first** (a zero below is only meaningful if the table answers at all):
`SELECT count() FROM activity.events WHERE source='tool'` → **45,273**.

| tool | rows | first | last |
|---|---:|---|---|
| `obs-read` | 32,842 | 2026-07-24 | 2026-10-02 |
| `ticket-status` | 6,695 | 2026-07-24 | 2026-08-26 |
| `opencode-dispatch` | 4,509 | 2026-08-20 | 2026-10-01 |
| `verify-agent-work` | 568 | 2026-07-24 | 2026-09-21 |
| `cairn` | 365 | 2026-10-02 | 2026-10-02 |
| **`mention-open`** | **189** | **2026-09-12** | **2026-10-02** |
| `playwright-nixos` | 105 | 2026-08-07 | 2026-08-22 |
| **`mention-review`** | **0** | — | — |

`mention-review` → **0** is the negative control for this whole scope: the thing being
designed does not exist, measured rather than assumed.

**The cost, by surface:**

```bash
Q "SELECT JSONExtractString(payload,'surface') AS surface,
          JSONExtractString(payload,'outcome') AS outcome, count() c
   FROM activity.events WHERE source='tool'
     AND JSONExtractString(payload,'tool')='mention-open'
   GROUP BY surface, outcome ORDER BY c DESC FORMAT TSVWithNames"
```

| surface | outcome | rows |
|---|---|---:|
| `tui` | `picked` | 159 |
| `tui` | `auto-open` | 15 |
| *(empty)* | `dismissed` | 12 |
| `browser` | `picked` | 3 |

🔴 **174 TUI launches between 2026-09-12 and 2026-10-02 — 20 days, ≈8.7/day — and the
pipeline knows nothing about any of them beyond the fact that a terminal was spawned.** Every
one of those 174 rows records a *routing decision* and then goes silent. The review session —
its load latency, how long the operator stayed, which panels they used, whether they
approved, commented, merged or walked away — is 100% dark. That is the measurement that
justifies this work, and it is the one number the brief did not have.

### 2.2 The `IS NOT NULL` trap — reproduced, with its own negative control

The `activity` skill records this trap; I hit it deliberately to confirm it is live on this
table rather than inherited prose, because §6's queries depend on getting it right.

| arm | query | rows |
|---|---|---:|
| the trap | `WHERE JSONExtractString(payload,'surface') IS NOT NULL` | **1,183,955** |
| **negative control** | predicate **removed** entirely | **1,183,955** |
| the correct test | `WHERE JSONExtractString(payload,'surface') != ''` | **177** |

🔴 **The trap arm and the predicate-removed arm are byte-identical, which is the proof the
filter is inert** — ClickHouse returns `''` for a missing key, never `NULL`, so the predicate
can never be false. Inflation factor **6,689×**. Any §6 dim-adoption query must use `!= ''`
and must run the predicate-removed arm as a control. The 177 is also a useful cross-check: it
is the 174 TUI + 3 browser rows that carry a `surface`, against 189 total — the 12
`dismissed` rows have no surface, correctly.

### 2.3 The consumer ledger is already stale

```bash
python3 -c "import re; src=open('scripts/session-analysis/adoption-scan.py').read();
print(re.findall(r'\"id\": \"([^\"]+)\"', src))"
```

**9 ids:** `verify-agent-work`, `obs-read`, `ticket-status`, `playwright-nixos`,
`cmd:verify-agent`, `cmd:obs-read`, `task-spec-drafter`, `opencode-dispatch`,
`browser-bridge`.

- `mention-open` → **absent**, with 189 live rows.
- `cairn` → **absent**, with 365 live rows.
- So **2 of the 7 live emitters are invisible** to the report whose job is to read them,
  and `ticket-status` / `playwright-nixos` are in the ledger but have not emitted since
  August.

🔴 **Consequence for this scope: an emit with no consumer entry is a dim that answers no
question — which is the brief's own test for whether a dim earns its cost.** A `mention-review`
row added without an `ITEMS` entry joins `mention-open` and `cairn` in a ledger-shaped
blind spot. The ledger is also **not** two-way pinned against the live roster (nothing could
fail when `cairn` started emitting), which is why it drifted silently.

### 2.4 The seam is cross-tier as well as cross-language

```bash
grep -c 'ACTIVITY_SPOOL_DIR' scripts/run-tests.sh        # -> 10
grep -c 'ACTIVITY_SPOOL_DIR' scripts/run-go-tests.sh     # -> 0  (grep exit 1)
grep -c 'ACTIVITY_SPOOL_DIR' scripts/run-node-tests.sh   # -> 0
```

The 10 is the positive control: the same pattern, the same grep, a file where it must hit.
GUARD 8's two exports are `run-tests.sh:3109-3110`. The single line every Go package goes
through is `scripts/run-go-tests.sh:323`:

```bash
( cd "$module" && go test -json -count=1 "./$pkg" ) > "$json" 2>&1
```

Neither `ACTIVITY_SPOOL_DIR` nor `XDG_STATE_HOME` is set anywhere in that runner's 422
lines, and `spool_emit`'s fallback resolves to
`${XDG_STATE_HOME:-~/.local/state}/activity/spool` **at call time**. Measured ambient
environment on this host: both variables **unset**, and
`~/.local/state/activity/spool/` exists (empty — `activity-collector` is `active` and has
drained it).

🔴 **So a Go test that exercises the emit path today writes into the operator's own
production dataset, where the row is indistinguishable from real activity.** This is
GUARD 8's exact founding defect, re-opened by a tier that arrived after it. Note the
two-tier asymmetry: the `checks.gotests` nix-sandbox tier has a sandboxed `HOME` and would
*not* leak, so **the dev-host tier is the one that leaks and the sandbox tier is
structurally blind to it** — greening one says nothing about the other.

### 2.5 Line size, and what the atomicity invariant actually is

Realistic 16-dim event built through the real `build_fields` + `build_line`:

```bash
nix develop ~/workspace/devrc -c python3 -c "<see Appendix>"
```

| quantity | measured |
|---|---:|
| v1 line, 16 dims, with `duration_ms` + `exit_code` | **586 B** |
| `PIPE_BUF` on this host (`<limits.h>`) | 4,096 |
| payload keys (16 dims + `tool` + `outcome`) | 18 |
| `dropped` key present | **no** |

586 B is 14% of PIPE_BUF — but §3.2 shows that is not the relevant comparison.

---

## 3. Decision 1 — the emit seam

### 3.1 Options, and what I reject

| # | design | verdict |
|---|---|---|
| **S1** | **A Go `internal/spool` package writing the v1 line directly** | **RECOMMENDED** |
| S2 | Go `exec`s `scripts/collector/emit` (bash) | **Rejected** — fork per event; `emit` is not on the TUI's `PATH` (the binary is a nix-store package, `scripts/` is not in its closure); and it would make the shell helper a build dependency of a Go program |
| S3 | Go `exec`s `python3 invocation.py` | **Rejected** — ~50–100 ms interpreter start paid at *exit*, so the operator watches the window hang; same `PATH`/closure problem; and it puts a Python runtime in a Go tool's runtime requirements |
| S4 | The Python launcher emits on the TUI's behalf | 🔴 **DEAD** — §1.3. Detached grandchild, no wait, DEVNULL streams |
| S5 | TUI writes a sidecar; a systemd path unit forwards it | **Rejected** — the spool *is* already that mechanism. A second queue with its own rotation, its own failure modes and its own deadman, to avoid writing one line |

**S1 is the only design that keeps one line format, one consumer and no new moving part.**
Its whole cost is ~80 lines of Go with no non-stdlib imports, and it writes the identical
bytes the Python emitters write — which §4 is about proving rather than asserting.

### 3.2 🔴 The atomicity invariant is ONE `write()`, not "under `PIPE_BUF`"

The brief proposes *"a single write under `PIPE_BUF` (4096 on Linux, so concurrent appends
cannot interleave)"* and says to test it rather than assert it. I tested it, and **the stated
reason is wrong in both directions.**

Design: 16 goroutines × 200 iterations each append to one file opened
`O_APPEND|O_CREATE|O_WRONLY`. Each writer's line is a single repeated character unique to
that writer, so **a torn line is exactly a line containing more than one distinct
character** — a detector with no dependence on length arithmetic.

| arm | line size | writes per line | lines | **torn** |
|---|---:|---:|---:|---:|
| under test | 586 B | **1** | 3,200 | **0** |
| over PIPE_BUF | **5,000 B** | **1** | 3,200 | **0** |
| **positive control** | 586 B | **2** | 3,200 | **618** |

Three conclusions:

1. **A 5,000-byte single append — 122% of PIPE_BUF — did not tear.** `PIPE_BUF` bounds
   atomic writes to *pipes and FIFOs*; for a regular file the kernel holds the inode lock
   across the write and `O_APPEND` makes offset-advance-plus-write one indivisible step. So
   the 4,096 figure does not bound anything here.
2. 🔴 **Being under PIPE_BUF does NOT make a two-write line safe** — the positive control
   tore 19% of its lines at 586 B. This is the dangerous half of the error: an implementer
   told "under 4096 is atomic" may reasonably write a header then a body.
3. **The detector works.** The control going red is what makes the two zeros evidence rather
   than a harness wired to nothing. ⚠ My *first* detector was broken — it compared 3-char
   tag groups and false-positived on every 5,000 B line because the repeat was truncated
   mid-tag. I rebuilt it; the numbers above are from the rebuilt one.

⚠ **Scope of the claim:** one host, one filesystem (`stat -f -c %T` on the spool's parent →
`ext2/ext3` magic, i.e. ext4), Go 1.x `os.File.WriteString`, 16 concurrent writers. I did not
test NFS, tmpfs, or a different kernel. The *mechanism* (inode lock + `O_APPEND`) is not
filesystem-agnostic — network filesystems notably do not provide it — but the spool is always
local.

**So `spool_emit.py:16-17` and `:93` are both wrong in the same way** (*"concurrent writers do
not interleave for sub-PIPE_BUF lines"* / *"O_APPEND single write == atomic for sub-PIPE_BUF
lines"*). They are wrong in the *safe* direction — Python's buffered writer flushes a 586 B
line in one syscall on close, so the code is correct — but the stated reason would license an
incorrect change, and a comment is a claim too. **Fixing those two comments belongs in this
work** (§8, W0).

### 3.3 🔴 `os.Exit` skips `defer` — and `main` currently has four such exits

This is the part of S1 that is not boilerplate. A naive `defer emit(...)` in `main` misses
**four of the six exit paths**:

| path | `cmd/mention-review/main.go` | reached via | a `defer` fires? |
|---|---|---|---|
| bad argv (64/65/66) | `:49-51` | `os.Exit(aerr.Code)` | **no** |
| bubbletea run error | `:81-83` | `os.Exit(1)` | **no** |
| `--version` | `:38-40` | `return` | yes |
| normal quit | `:84` | fall off `main` | yes |

The fix is the standard Go shape — `func run() int` with a single
`func main() { os.Exit(run()) }` — so every exit becomes a `return` and one emit site sees
all of them. **That is a refactor of `main` and it must be in the PR**, because the
alternative is an emit at four sites, which is the "one rule, one place" defect the repo's own
rules call a bug-finding instrument.

⚠ **Whether the argv-error paths should emit at all is a genuine fork** (§10, Q1). They never
draw a frame, so every latency dim is absent and `outcome` would be `bad-args`. I lean
**yes, emit** — a spike in `bad-args` is exactly the signal that the hint wrapper is passing
malformed refs, which is a real past failure class — but it means `adoption-scan` will count
them as invocations, so the consumer entry must be able to split them.

🔴 **A `SIGKILL`, a closed terminal window, or a panic loses the row, and that is accepted.**
The alternative — emit at *start* as well, so a launch is always recorded — doubles the row
count per session and breaks the one-row-per-invocation contract that `adoption-scan`'s
counting assumes. State the loss in the package comment rather than engineering around it.
⚠ The magnitude is unmeasured: I do not know how often the operator closes the window with
the WM rather than quitting (§9.4).

### 3.4 What happens when the spool dir does not exist

The brief asks explicitly. The Python reference **creates it** (`spool_emit.py:92`,
`mkdir(parents=True, exist_ok=True)`), measured in §1.4. **The Go port should do the same**,
with `os.MkdirAll(dir, 0o755)` before the open, and should swallow the error exactly as
Python swallows `OSError`.

Reasoning: the dir's absence is the normal state on a host where the collector has not run
yet, and it is also what a test harness produces when it points `ACTIVITY_SPOOL_DIR` at a
fresh `t.TempDir()`. Declining to create it would make the Go writer **silently stricter than
the Python one**, so the two emitters would disagree about whether a given host can be
instrumented — a divergence no test on either side would see, which is §4's whole subject.
Mode `0o755` matches what the collector already finds there (`drwxr-xr-x`).

### 3.5 The shape of the Go side

Three small pieces, none of them clever:

- `internal/spool` — `Emit(fields map[string]any, dir string) string`, mirroring
  `spool_emit`: `v1`, tab-separated, `_PLAIN_KEYS` verbatim and everything else
  `b64:<key>=`, auto-filled `ts` (UTC, `2006-01-02 15:04:05.000`) and `host`, resolution
  order `ACTIVITY_SPOOL_DIR` → `$XDG_STATE_HOME/activity/spool` →
  `$HOME/.local/state/activity/spool`. `MkdirAll`, then one
  `OpenFile(O_APPEND|O_CREATE|O_WRONLY, 0o644)` and **one** `WriteString(line + "\n")`.
  Every error path returns the line and no error. **No non-stdlib import.**
- `internal/spool` also owns the dim ledger as a Go constant set (§4.2).
- A `metrics` accumulator threaded through `App` — counters and timestamps only, no I/O —
  so the pure-function discipline the UI package maintains is not broken. `App` is already
  a value type stepped functionally (`app.go:816` `func (a App) ReadIntents()`), so the
  accumulator must be a value too, or it will silently fork per `Step`. ⚠ **This is the
  part I am least sure of** (§10, Q4): the counters need to survive `Step`'s
  copy-and-return, and `Step` is driven by a test harness that walks every binding
  (`intents_test.go`), so getting it wrong is loud rather than silent — but the design
  needs a reading of `Step`'s value semantics I have not done.

🔴 **Do NOT retry a short write.** Go's `os.File.Write` loops internally on a partial write,
and a retry appends the remainder at whatever the end-of-file is *now* — which is how a
concurrent writer's line gets split around yours. On `ENOSPC` the first write returns a
partial count and the second returns the error, so the loop can produce **both** a truncated
line and, under concurrency, a torn one. The mitigation is to check `n != len(buf)` and treat
it as a dropped row, never as something to finish. §7 covers what the collector does with the
truncated remains.

---

## 4. Decision 2 — the cross-language seam guard

**This is the highest-risk part of the work and I agree with the brief's framing.** Two
hermetically-tested components, broken together, with no test on either side able to see it:
`internal/spool`'s Go tests would pass against Go's own notion of the format, and
`test_invocation.py` would pass against Python's, while the bytes diverge.

### 4.1 The guard that must ship first: GUARD 8 for the Go tier

Before any seam pin, §2.4's leak must close, **in the same PR as the writer**. Attach the two
exports at `scripts/run-go-tests.sh`, on the single line every package goes through
(`:323`), mirroring `run-tests.sh:3109-3110` — one enforcement point, not a per-package
`TestMain`, for the reason GUARD 8's own header gives: a conftest-shaped fix protected 1
target of 17.

**Coverage, labelled as the rules require:**

- **Regression test:** a planted Go test that emits, run under the patched runner, must write
  into the run dir; **red at `46e8dbc7`**, where it lands in
  `$HOME/.local/state/activity/spool/current.log`. Its own positive control is
  `spool-rows=1` — a guard that reports 0 rows wrote nothing and proves nothing.
- **Mutation:** remove the exports → the regression test must fail **naming the real spool
  path**, not merely differing.
- 🔴 **Isolate the mutation.** The fallback trap (`XDG_STATE_HOME`) and the direct variable
  (`ACTIVITY_SPOOL_DIR`) are two separate refusals. A mutant that removes **both** dies for
  whichever is checked first, proving nothing about the other. Mutate each alone.
- **Invariant guard (labelled as such):** a two-way pin that the Go runner and the pytest
  runner export the *same* variable names — so the Go tier cannot be narrowed to a different
  spelling that looks like isolation.

### 4.2 The two-way field-set pin

**One ledger, read by both languages — never two hand-typed copies.** A committed
`nix/pkgs/tools/mention-review/dims.json` (or equally `scripts/collector/tool-dims.json`;
the location is a fork, §10 Q3) holding the emitted key set:

```
{ "mention-review": ["surface", "t_first_frame_ms", "t_diff_readable_ms", ...] }
```

Both sides then assert against **it**, not against each other:

| side | guard | fails when |
|---|---|---|
| Go | `TestTheEmittedDimSetMatchesTheLedger` — build a real event, extract the payload keys, compare to the ledger as a **set** | a key is added **or** removed without the ledger moving |
| Python | `test_the_mention_review_dim_ledger_fits_the_collectors_cap` — assert `len(ledger) <= _MAX_DIMS` and that `sanitize_dims` returns **no `dropped` key** for it | the ledger outgrows the cap |
| Python | `test_the_mention_review_ledger_names_only_sanitizer_safe_keys` — every key `<= _MAX_KEY_LEN`, no two keys sharing a 64-char prefix | a key collision would silently remove a column (`invocation.py:109-115`) |

🔴 **Both directions must be watched red.** Grow: add a key to the Go builder, leave the
ledger → the Go set-compare fires naming the surplus key. Shrink: delete a key from the
builder, leave the ledger → it fires naming the missing one. A guard asserting only
`⊆` or only `⊇` is half a pin and is the shape that lets a rename through as
"one added, one removed".

### 4.3 🔴 The behavioural case — because a structural check type-checks past a wrong argument

The brief is right that the field-set pin is insufficient, and the concrete defect is worth
naming: `dims["t_rest_ms"] = m.tGraphql`. **Every structural guard above stays green** — the
key set is identical, the types are identical, the ledger is satisfied. Only a value
assertion can see it.

**The round-trip must exec the real Go binary and feed its bytes to the real Python
consumer.** I validated that this works end to end. Hand-building the line the way a Go
writer would — *not* via `spool_emit` — and parsing it with the production parser:

```python
ev = collector.parse_line(go_shaped_line, host_override='workbench')
```

→ parses, yielding `source=tool`, `kind=invocation`, `text='mention-review'`,
`duration_ms=184233`, `exit_code=0`, `ts`, `host`, and the payload intact.

**Negative controls — `parse_line` provably CAN reject** (four distinct mechanisms, each
measured to return `None`):

| mutation | result |
|---|---|
| `v2` instead of `v1` | `None` |
| a token with no `=` | `None` |
| missing `kind` | `None` |
| malformed base64 (`b64:text=!!!`) | `None` |

A round-trip test whose parser cannot return `None` is wired to nothing; these four are what
make a `PARSED: True` meaningful.

**Fixture discipline, because this is where the value assertion gets faked green:**

- 🔴 **Pairwise-distinct values, each distinct from every constant the assertion names.**
  The swapped-argument defect is invisible if `t_rest_ms` and `t_graphql_ms` are both
  seeded 666, and it is *also* invisible if the fixture uses the real 666/855 and the
  assertion names 666/855 — a mutant that hardcodes the literal survives. Feed values the
  constants **cannot** equal (e.g. 101/202/303/404) and watch each land under its own key.
- **Overshoot the bounds; avoid power-of-two multiples of any step.** A fixture whose
  durations sit exactly on a clamp boundary never executes the clamp.
- **One row must carry a key the ledger does not** (to prove the Go side rejects or the
  Python side counts it), and **one must omit an optional key** (to prove absence is
  distinguishable from zero — `exit_code=0` and *no* `exit_code` are different claims, and
  `INT_COLS` coerces an unparseable value to **0**, which erases the difference).

⚠ **What this guard structurally cannot see:** it proves Go's bytes parse into the fields
Python expects. It does **not** prove ClickHouse accepts the row, because `parse_line`'s
output is POSTed as `JSONEachRow` by a daemon this test does not run. The `payload` column is
`JSON`-typed; a Go-emitted payload that is valid JSON but, say, changes a field's type between
runs is a ClickHouse-side concern no local test reaches. §11's closing condition is a live
read for exactly that reason.

### 4.4 The consumer entry, and pinning the ledger that drifted

§2.3 found `adoption-scan`'s `ITEMS` ledger missing 2 of 7 live emitters. Adding a
`mention-review` entry is necessary; **making the ledger two-way is the fix for why it
drifted.** A guard that compares `ITEMS` ids against the distinct `tool` values in the table
cannot run in CI (it needs the cluster), so the honest options are a guard against a
*committed* roster of emitters (and a two-way pin between that roster and the `emit_invocation`
call sites, which *is* greppable), or accepting the drift and detecting it in the §11 closing
read. I recommend the former and note it is **scope-adjacent**: `mention-open` and `cairn`
are pre-existing gaps, not ones this work creates (§10, Q5).

---

## 5. Decision 3 — the dim ledger

### 5.1 The 16/16 question, resolved by measurement

```bash
nix develop ~/workspace/devrc -c python3 -c "<Appendix>"
# CLICK_DIM_FIELDS len = 14
# _MAX_DIMS = 16
# headroom = 2
```

🔴 **`CLICK_DIM_FIELDS` is 14. The brief's "16 against 16 — exactly full" is false.**
`mention-open.py:1472-1474` lists exactly 14 names, and `invocation.py:76-79` states the
intent in the repo already: *"16 clears the widest current ledger (14) with room for two
more fields."*

**Independently confirmed from the consumer side** — a live key census of all 189
`mention-open` rows returns 16 payload keys: the 14 ledger dims plus `tool` and `outcome`,
which `build_fields` adds *outside* `sanitize_dims` (`invocation.py:150-152`). **And
`dropped` is absent from the census**, which is a positive confirmation that nothing has
ever been truncated — the loud test over the silence has never fired in production.

### 5.2 🔴 And the question does not apply to this work

**The cap is per-event, not a pool.** `sanitize_dims(dims)` is called once per
`build_fields` call, on that call's own dict. The TUI's row is a *different row* with a
different `tool` value, so **it gets its own 16 slots and shares nothing with
`CLICK_DIM_FIELDS`.** Measured directly: a 16-dim TUI event produced 18 payload keys and
**no `dropped`** (§2.5).

So, answering the brief's question precisely: **raising `_MAX_DIMS` is neither a decision nor
a migration for this work — it is not required.** The sibling repo-picker scope wanting a
dim is likewise unaffected unless it adds a **15th and 16th** dim *to the click row*, which
is where the real 2-slot headroom lives. ⚠ I did not read that sibling doc's dim proposal; I
checked and `claudedocs/scope-chief-model-selector-2026-09-19.md` contains no dim discussion,
so I could not confirm which row it targets (§9.5).

⚠ **If the TUI ledger ever did approach 16, the brief's framing is still right about the
posture:** raising the cap "only moves the cliff" (`invocation.py:68-72`), and because
`_MAX_DIMS` is one constant shared by every caller, a raise doubles the accident ceiling for
emitters that gained nothing. The correct move at that point is a per-tool cap or a deliberate
raise with its own argument — not headroom.

---

## 6. Decision 4 — what to collect

The brief's test: **a dim that answers no question is cost.** Each row names the question,
and I argue against three.

### 6.1 Recommended — 13 dims

| dim | type | the question it answers | argument |
|---|---|---|---|
| `surface` | enum | which launch path (`tui`) | **Joins to the click row.** `mention-open` already emits `surface=tui` on 174 rows; without this the two halves of one review cannot be matched. Cheap, 1 value today, and the join key is the whole reason the TUI's row is interesting rather than standalone |
| `t_first_frame_ms` | int | chrome on screen | 🔴 **Separate from the next, non-negotiably.** §1.2: conflating these caused one retraction and the brief repeated it. Measured as a NULL across three configurations (277/285/287) — **which is exactly why it must be collected**: a dim whose job is to stay flat is how a regression in it becomes visible |
| `t_diff_readable_ms` | int | the number the operator feels | The 1,212→922→735 series. The actual headline |
| `t_rest_ms` | int | the REST leg | Half of the inversion |
| `t_graphql_ms` | int | the GraphQL leg | The other half. 🔴 **The pair is the single best-argued dim in this scope**: if REST becomes the long pole, the skeleton's 187 ms silently becomes 0 with nothing failing (§1.2). These two dims are the only instrument that can see that, and this project has *already* measured the inversion both ways once |
| `leg_first` | enum | which leg landed first | Derivable from the pair, and kept anyway: the 5-of-5 claim is about *ordering*, and a trend over an enum is far cheaper to query than a comparison over two ints across 180 days. ⚠ The one dim here I would drop first if the ledger tightened |
| `load_outcome` | enum | `ok` / `no-token` / `not-a-pr` / `api-error` | Partitions every latency number. A 4,000 ms "diff readable" from an API error is not a latency sample, and without this the trend is polluted by failures |
| `panels_visited` | int (0–4) | did they use the layout | 4 panels exist (`PanelOverview/Commits/Files/Diff`, `app.go:17-20`), `FocusDefault = PanelDiff` (`:239`), and `tab` cycles all four *during the skeleton* (`coldopen_test.go:745`). A distribution pinned at 1 says three panels are dead weight |
| `nav_moves` | int | depth of engagement | Separates "glanced and quit" from "read it". The denominator that makes `exit_reason` and `wrote` interpretable |
| `exit_reason` | enum | `quit` / `wrote-then-quit` / `bad-args` / `run-error` | 🔴 The brief asks for this and it is well-earned. §3.3 shows `main` has four exit paths and two of them currently skip `defer`, so this dim is also the thing that *proves* the exit refactor works |
| `wrote` | bool | did any write verb fire | The cheapest possible read of "did this review produce an action" |
| `write_verb` | enum | which one | `KnownIntents()` (`intents.go:174-185`) enumerates 8 intents; 5 are writes — `PostComment`, `Approve`, `RequestChanges`, `SubmitReview`, `MergePR`. 🔴 **An enum over a two-way-ledgered set, not a free string.** `Confirmed` (`:199`) and `NotConfirmed` (`:215`) already pin 4+1 two-way, so the dim can be derived from that ledger rather than hand-listed — a sixth write verb then cannot reach the payload unledgered |
| `confirmed` | bool | did it go through the y/N prompt | Separates the one additive verb from the four guarded ones without naming the verb twice |

**13 dims against a 16 cap: headroom 3**, measured, with `tool` and `outcome` free (§5.2).
A realistic 16-dim line measures 586 B, so 13 is comfortably inside every bound.

### 6.2 Argued against — what I am NOT proposing

| rejected dim | why |
|---|---|
| **`session_duration_ms` as a dim** | Redundant: `duration_ms` is a first-class **column** (`INT_COLS`), not a payload key. Putting it in the payload spends a dim slot on something queryable without `JSONExtract` |
| **`repo` / `platform`** | 🔴 **Already on the click row**, and the TUI row joins to it via `surface` + `ts`. Re-emitting is duplication — and `repo` is the one field in the click ledger with real cardinality, so duplicating it doubles the public-repo exposure surface for zero new information |
| **`hunks` / `files_changed` / `lines_changed`** | **Tempting and I reject it.** These describe the *pull request*, not the session, and PR size is already knowable from GitHub. It is the dim most likely to correlate with the latency numbers and therefore the most tempting — but `app.go:779-780` records the measurement fixture as *"one public 4-file / 1,395-line pull request"*, i.e. the existing numbers are **single-fixture** and a size dim would not make them comparable, only look comparable. If a size normaliser is wanted later it needs its own argument |
| **Anything naming a file** | 🔴 Hard constraint, §0 of the brief. `files_opened` as a **count** is borderline-useful and I dropped it in favour of `nav_moves`; a file *name* is forbidden outright |
| **`pr_title`, diff content, comment body** | 🔴 Forbidden. Captured text, public repo |
| **`terminal_size` / `geometry`** | Answers no question anybody has asked. `panels_visited` already covers "did the layout work" |

### 6.3 🔴 Privacy — what the TUI must never emit

The repo is **public**, and this tool's whole subject matter is the operator's private review
of other people's code.

- **No diff content, no file paths, no file names, no PR titles, no comment bodies, no
  branch names, no author logins.** Shapes, counts, durations and low-cardinality enums only.
- `invocation.py:17-25` is explicit that the module **is not a scrubber** — *"the call site is
  the privacy boundary"*. So the Go call site carries the obligation, and the `_MAX_VALUE_LEN`
  truncation must not be mistaken for protection.
- 🔴 **The `reason` / free-string hazard.** The click row has a `reason` dim. A Go analogue
  carrying, say, an API error *message* would put GitHub's response text — potentially naming
  a private repo — into a public-repo-adjacent dataset. **Every string dim in §6.1 is a closed
  enum**, and that is deliberate: `load_outcome` is 4 values, not an error string.
- ⚠ **This document is not covered by the repo's own gates.** `test_no_captured_text.py`
  reads JSON/JSONL/JSONC and `test_no_captured_markup.py` reads `.html`/`.txt`; **neither
  scans `.md`.** I kept this file to `owner/repo` placeholders and store paths by hand. A
  reviewer should re-check it rather than trust a green gate, because there isn't one.
- ⚠ `activity.events` is the operator's **personal** dataset with a 180-day TTL. The rows
  proposed here are about the operator's own behaviour, which is the dataset's purpose — but
  the *dims* are what make them safe, and the enum discipline is the whole mechanism.

---

## 7. Decision 5 — failure modes and degraded behaviour

| # | failure | degraded behaviour | verified? |
|---|---|---|---|
| 1 | **Telemetry off** (`spool_emit`'s Go analogue has no "off" switch; the off state is *the collector not running*) | The line accumulates in `current.log` and is never shipped. The TUI is unaffected. This is the intended graceful no-op (`invocation.py:26-30`) | **measured** — collector `active`, spool drained empty |
| 2 | **Spool dir absent** | `MkdirAll` creates it (§3.4), matching Python. If `MkdirAll` fails, the write is skipped and the TUI continues | **measured** for Python; the Go behaviour is the proposal |
| 3 | **Spool unwritable** | Open fails, error swallowed, no row, TUI exits normally with its real exit code | **measured** for Python (parent `0o500` → no file, no raise) |
| 4 | **Disk full (`ENOSPC`)** | 🔴 **The one mode with a real hazard.** A partial write leaves a truncated line with no `\n`, and the *next* append lands on the same line — so **one subsequent row is also corrupted**. `parse_line` then rejects both (malformed token / bad base64 → `None`, measured §4.3), so they are **dropped, not misingested**. Mitigation: never retry a short write (§3.5). ⚠ Whether the collector's *reader* tolerates a partial trailing line is **unverified** (§9.2) | partially |
| 5 | **The collector is not running** | Identical to (1): rows queue on disk. ⚠ Unbounded growth is the collector's concern, not the TUI's, and the `tlm` deadman pill is what surfaces a stalled source | **measured** (service state) |
| 6 | **Two TUIs open at once** | **Safe.** Each process appends its own line with one `write()`; §3.2 measured 16 concurrent writers producing 0 torn lines. Two rows, two `invocation` events, correctly counted as two. No shared state, no lockfile, no sequence number needed | **measured** |
| 7 | **`SIGKILL` / window closed / panic** | **The row is lost**, accepted and documented (§3.3). The session is undercounted, never miscounted | not measured |
| 8 | **Clock skew between hosts** | `ts` is UTC from the local clock; the collector stamps `host` from `ACTIVITY_HOST`. A skewed host's latency dims are still valid (they are deltas), only its `ts` ordering suffers | not measured |
| 9 | 🔴 **A Go test leaks into production** | §2.4 — **today this is not a degraded mode, it is a live defect waiting for the first writer.** Closed by W1 | **measured** |

---

## 8. Work items and sequencing

| PR | item | independently revertible of | why here |
|---|---|---|---|
| **W0** | Fix the two `PIPE_BUF` comments in `spool_emit.py:16-17,93` | everything | A comment is a claim; this one would license a torn-line implementation. Zero behaviour change, lands alone, and §3.2's measurement is its justification |
| **W1** | 🔴 **GUARD 8 for the Go tier** — the two exports at `run-go-tests.sh:323` | everything | **Must precede or accompany W2.** Without it, W2's own tests write to the operator's production dataset. Independently valuable: it closes the hole for any future Go emitter |
| **W2** | `internal/spool` + the `main` exit refactor (§3.3) + the metrics accumulator | W3+ | The writer. Lands with the field-set pin and the behavioural round-trip (§4.2, §4.3) |
| **W3** | The `adoption-scan` consumer entry + the two-way roster pin (§4.4) | W2 | Without it the rows are invisible, like `mention-open`'s 189 |
| **W4** | A committed latency harness, so the trend is reproducible | W2 | §1.2: the numbers exist only as prose and the method is recorded without the driver. ⚠ Arguably this should be **first** — it is the thing that makes the latency dims checkable against a known baseline — but it needs the dims to have somewhere to land. Fork, §10 Q2 |

Dependencies: **W1 → none** (and blocks W2). **W0 → none.** **W2 → W1.** **W3 → W2.**
**W4 → W2.**

🔴 **Every "red at base" below is a specification of what must be WATCHED, not a
measurement.** Nothing in this scope was built; no Go test was written or run; no
`go build`, no `go test`, no `scripts/gate.sh`. Base for every such claim: `main` =
`46e8dbc7`.

---

## 9. What I did NOT investigate

1. **Whether any proposed code compiles or passes.** No code was written. Every guard in §4
   is a specification.
2. **The collector's reader against a partial trailing line.** I measured `parse_line`'s
   verdict on malformed *strings* (§4.3) but did not read `collector.py`'s file-tailing and
   rotation logic, so failure mode 4's "dropped, not misingested" is proven for the parser and
   **assumed** for the reader.
3. **`Step`'s value semantics for the metrics accumulator.** §3.5 flags this as the design's
   least-certain part. `App` is stepped by value; I did not verify that a counter survives
   the copy-and-return across every binding, nor read `intents_test.go`'s harness.
4. **How often the operator kills the window rather than quitting**, which bounds failure
   mode 7's undercount. Unmeasured, and measurable only after W2 ships (a `quit` row count
   against the click row's 174).
5. **The sibling repo-picker scope's dim proposal.** I grepped
   `claudedocs/scope-chief-model-selector-2026-09-19.md` for dim discussion and found none, so
   I could not confirm whether its dim targets the click row (where the real 2-slot headroom
   is) or a new row (where it is irrelevant). §5.2's conclusion holds either way for *this*
   work.
6. **ClickHouse's acceptance of a Go-emitted `payload`.** §4.3's round-trip ends at
   `parse_line`. The `JSONEachRow` POST is not exercised by any local test — which is why §11
   is a live read.
7. **`mention-open`'s and `cairn`'s absence from `ITEMS`** beyond establishing it. Whether
   those are oversights or deliberate exclusions, I did not determine; I treated them as
   evidence the ledger is not two-way, which is independent of the answer.
8. **Whether the 174 TUI launches are 174 distinct reviews.** A re-open of the same PR emits a
   second click row. I did not de-duplicate by `repo`, so "≈8.7/day" is launches, not reviews.
9. **The `e2e_test.go` / `coldopen_test.go` harness as a latency driver.** `e2e_test.go:26`
   uses `tea.WithoutRenderer()`; whether it can produce W4's wall-clock numbers, or whether
   W4 needs the tmux-socket driver `app.go:782` describes, is unresolved.
10. **Non-local filesystems.** §3.2's atomicity result is one host, ext4. The spool is always
    local, so I judged this out of scope rather than unknown-and-risky.

---

## 10. Open questions

1. 🔴 **Should the argv-error exits (64/65/66) emit a row?** (§3.3) They draw no frame, so
   every latency dim is absent and `outcome='bad-args'`. **For:** a spike is the signal that
   the hint wrapper is passing malformed refs. **Against:** `adoption-scan` counts them as
   invocations, inflating the adoption number with failures. **My recommendation: emit, and
   give the consumer entry a split on `exit_reason`.** Operator's call — it changes what the
   adoption number *means*.
2. **Should the latency harness (W4) land before the dims (W2)?** It is the thing that makes
   the dims checkable against a known baseline, and §1.2 shows the stored numbers are already
   mislabelled once. But it has nowhere to write until W2 exists. **My recommendation: W2
   then W4**, accepting that W2's first rows have no baseline to compare against.
3. **Where does the shared dim ledger live** — `nix/pkgs/tools/mention-review/dims.json`
   (next to its only Go consumer) or `scripts/collector/tool-dims.json` (next to the cap it
   must fit)? The second scales to a third emitter; the first keeps the Go module
   self-contained. **Weak preference for the second**, since the constraint it must satisfy
   (`_MAX_DIMS`) lives there.
4. **Does the metrics accumulator survive `Step`'s value semantics?** (§9.3) Needs a read of
   `app.go`'s `Step` and `intents_test.go` before W2 is costed. This is the one item that
   could make W2 materially larger than "~80 lines plus a refactor".
5. **Is fixing `mention-open`'s and `cairn`'s absence from `ITEMS` in scope?** (§2.3) They are
   pre-existing gaps this work did not create, but W3 touches that exact ledger and the
   two-way pin would fail on them immediately — so W3 either fixes them or grandfathers them.
   **My recommendation: fix them in W3**, because a pin with a grandfather ledger for its own
   first two entries is a pin nobody will trust.
6. **Does a 180-day TTL suit a latency trend?** The whole argument for `t_rest`/`t_graphql` is
   multi-month drift in GitHub's API. 180 days bounds the trend to two quarters. Not a blocker,
   but it bounds the claim the dims can ever support.

---

## 11. Closing condition

**A command a later session can RUN**, after W2 and W3 have merged and `scripts/ship.sh` has
converged both hosts:

```bash
P=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key \
    sops -d --extract '["stringData"]["reader-password"]' \
    ~/workspace/homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml)
Q() { curl -s --max-time 25 "$CH" --user "activity_reader:$P" --data-binary "$1"; }

# 1. THE CLAIM: the TUI emits, and the row carries the legs.
Q "SELECT count() AS rows,
          countIf(JSONExtractString(payload,'t_rest_ms') != '')    AS with_rest,
          countIf(JSONExtractString(payload,'t_graphql_ms') != '') AS with_graphql,
          countIf(JSONExtractString(payload,'dropped') != '')      AS truncated
   FROM activity.events
   WHERE source='tool' AND JSONExtractString(payload,'tool')='mention-review'"

# 2. NEGATIVE CONTROL for arm 1's predicates (the documented IS NOT NULL trap, §2.2):
#    this MUST return a number far larger than `rows`. If it equals `rows`, the
#    filter is inert and arm 1 proved nothing.
Q "SELECT count() FROM activity.events"

# 3. POSITIVE CONTROL that the GUARD 8 fix holds: must be 0.
grep -c 'ACTIVITY_SPOOL_DIR' scripts/run-go-tests.sh   # must be >= 1
ls ~/.local/state/activity/spool/current.log 2>/dev/null; \
  scripts/gate.sh --tier go; \
  Q "SELECT count() FROM activity.events
     WHERE source='tool' AND JSONExtractString(payload,'tool')='mention-review'
       AND JSONExtractString(payload,'surface')='test-fixture'"   # must be 0
```

**It closes when all four hold:**

1. `rows > 0` **and** `with_rest == with_graphql == rows` — the TUI emits and both legs land.
   Today `rows = 0`, measured (§2.1), so this is red at `46e8dbc7` by construction.
2. `truncated == 0` — no `dropped` key, i.e. the ledger fits the cap in production and not
   just in a test.
3. Arm 2 returns ≫ `rows` — the predicates in arm 1 are live, not inert.
4. Arm 3 returns **0** — no test fixture ever reached the real dataset, i.e. W1 held
   through a full Go-tier run.

**And a named human judgement over named evidence, for the part no command can settle:** the
operator reads §6.1's 13-dim table against the first two weeks of live rows and decides
whether `leg_first` and `confirmed` — the two dims I flagged as weakest — earned their slots.
That is a question about whether a dim answered a question, and only the person asking the
questions can close it.

---

## Appendix — measurement reproduction

```bash
# --- the TUI emits nothing (NOT the brief's command; see §1.1) ---
cd ~/workspace/devrc/nix/pkgs/tools/mention-review/src
find . -type f -name '*.go' -print0 | xargs -0 grep -lniE 'activity|spool|telemetry|invocation'
find . -type f -name '*.go' -print0 | xargs -0 grep -lniE 'activity|...|package' | wc -l  # 47/47 control

# --- CLICK_DIM_FIELDS vs _MAX_DIMS, and the 586 B line ---
cd ~/workspace/devrc
nix develop ~/workspace/devrc -c python3 -c "
import sys, importlib.util
sys.path.insert(0,'scripts'); sys.path.insert(0,'scripts/collector')
spec=importlib.util.spec_from_file_location('mo','scripts/mention-open.py')
mo=importlib.util.module_from_spec(spec); spec.loader.exec_module(mo)
import invocation as inv
print(len(mo.CLICK_DIM_FIELDS), inv._MAX_DIMS)        # -> 14 16
"
# the 586 B figure: build_fields() with 16 dims + duration_ms + exit_code,
# then spool_emit.build_line(), then len(line.encode()).

# --- atomicity (§3.2): 16 goroutines x 200 appends, one repeated char per writer ---
# arm A: 586 B, ONE WriteString  -> 3200 lines, 0 torn
# arm B: 5000 B, ONE WriteString -> 3200 lines, 0 torn
# arm C: 586 B, TWO WriteStrings -> 3200 lines, 618 torn   (POSITIVE CONTROL)
# torn = any line containing >1 distinct byte value.
# PIPE_BUF read from <limits.h> via a 3-line C program -> 4096.

# --- the Go-shaped line through the real consumer (§4.3) ---
nix develop ~/workspace/devrc -c python3 -c "
import sys; sys.path.insert(0,'scripts/collector'); import collector as C
# hand-build a v1 line (NOT via spool_emit), then:
print(C.parse_line(line, host_override='workbench'))
# negative controls: v2 prefix / token with no '=' / missing kind / 'b64:text=!!!'
"

# --- Go tier has no spool isolation (§2.4) ---
grep -c 'ACTIVITY_SPOOL_DIR' scripts/run-tests.sh      # 10  (positive control)
grep -c 'ACTIVITY_SPOOL_DIR' scripts/run-go-tests.sh   # 0
```

**Key source locations, for the next reader:**

| fact | file:line |
|---|---|
| the detached launch, no wait, DEVNULL streams | `scripts/mention-open.py:2277-2280` |
| `open_tui` returns a launch outcome | `scripts/mention-open.py:2213,2286` |
| `_MAX_DIMS = 16`, and why not higher | `scripts/collector/invocation.py:74-81` |
| `sanitize_dims` slices, counts into `dropped` | `scripts/collector/invocation.py:87,124-125` |
| `tool`/`outcome` added OUTSIDE `sanitize_dims` | `scripts/collector/invocation.py:150-152` |
| never raises | `scripts/collector/invocation.py:178-188` |
| `CLICK_DIM_FIELDS` — **14** names | `scripts/mention-open.py:1472-1474` |
| the existing cross-module cap guard | `scripts/tests/test_mention_open.py:9904` |
| 🔴 the two wrong `PIPE_BUF` comments | `scripts/collector/keylog/spool_emit.py:16-17,93` |
| `mkdir(parents=True)` before the append | `scripts/collector/keylog/spool_emit.py:92` |
| `_PLAIN_KEYS` — the plain/b64 split | `scripts/collector/keylog/spool_emit.py:44` |
| `parse_line` — the consumer contract | `scripts/collector/collector.py:205-250` |
| GUARD 8's two exports (pytest tier only) | `scripts/run-tests.sh:3109-3110` |
| 🔴 the Go tier's unisolated `go test` line | `scripts/run-go-tests.sh:323` |
| the latency table, verbatim | `nix/pkgs/.../internal/ui/app.go:779-793` |
| "the second row is a NULL, not a cost" | `nix/pkgs/.../internal/ui/app.go:794-797` |
| `t_rest` 666 vs `t_graphql` 855, REST first 5/5 | `nix/pkgs/.../internal/ui/app.go:797-801` |
| 🔴 "a stored measurement is a claim about the day it was taken" | `nix/pkgs/.../internal/ui/app.go:803-806` |
| the skeleton's 187 ms, and that it needs REST to be faster | `nix/pkgs/.../internal/ui/panels.go:68-76` |
| `KnownIntents()` — 8 intents, 5 of them writes | `nix/pkgs/.../internal/ui/intents.go:174-185` |
| the two-way confirmation ledger | `nix/pkgs/.../internal/ui/intents.go:199,215` |
| the four panels, `FocusDefault` | `nix/pkgs/.../internal/ui/app.go:17-20,239` |
| the four exit paths, two skipping `defer` | `nix/pkgs/.../cmd/mention-review/main.go:38,49-51,81-84` |
| exit codes 64/65/66 | `nix/pkgs/.../internal/argv/argv.go:36-38` |
| `adoption-scan`'s `ITEMS` ledger (9 ids) | `scripts/session-analysis/adoption-scan.py:97-149` |
