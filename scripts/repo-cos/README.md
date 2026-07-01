# repo-cos — repo chief-of-staff (v0)

An autonomous **idea-generation** experiment. Instead of Zach originating every task
(CEO model), this scans his workbench codebases for improvement opportunities, ranks
them, and (opt-in) emails the top few as bounded, evidence-backed proposals.

**Quality bar:** a proposal earns a slot if it *increases productivity OR makes the
repos/products better*. That bar is broad, so the slop-defense is **structural**, not
wording:

1. **Evidence-grounded** — every proposal cites concrete `repo/file:line` refs pulled
   from a deterministic pre-scan. The LLM is told never to invent a file; the parser
   drops any proposal whose evidence doesn't map back to a real candidate.
2. **Hard cap** — output is capped to the top `--top` (default **5**) by leverage,
   truncated even if the model returns more.
3. **Bounded/shippable** — each proposal must be a finishable change an agent could
   implement *and verify*, not "consider refactoring X someday".
4. **CI-verifiable bias** — the pipeline prefers proposals whose value is
   CI/test-verifiable (fix a skipped test, add a missing test, remove dead code, fix a
   real bug) and ranks them above "nice idea, your call" items.

This mirrors the proven `scripts/mail-actions/` pattern: a deterministic Stage-1 filter
shrinks the corpus, then the LLM runs on the small survivor set.

## Pipeline

| Stage | Module | What | Cost |
|-------|--------|------|------|
| 0 | `feedback.py` | pull Zach's IMAP reply to LAST digest → synthesis context | none |
| 0.5 | `exclusions.py` | parse that reply → **HARD** repo-level exclusions (deterministic) | none |
| 1 | `prescan.py` | cheap grep/git signals, capped **per repo** | none |
| 2 | `llm.py` | OpenRouter clusters survivors → ranked JSON proposals | one call |
| 3 | `digest.py` | one formatter shared by stdout **and** email | none |
| 4 | `email_send.py` | Gmail SMTP, gated behind `--email` (default OFF) | none |

### Stage-1 signals (deterministic, evidence-bearing)

- **markers** — `TODO|FIXME|HACK|XXX|BUG` (ripgrep, else python walk)
- **skipped_test** — `@pytest.mark.skip`/`.skip(`/`xfail`/`it.skip`/`t.Skip(`/`#[ignore]` — a
  skipped test is a concrete, CI-verifiable fix
- **churn** — files changed most in the last ~90d (`git log --name-only --since`)
- **large_file** — files over `LARGE_FILE_LOC` (800) LOC — split candidates
- **stale_lock** — lockfiles untouched > 1y — best-effort dep-freshness

Each signal is **capped per repo** (see `prescan.CAP_PER_SIGNAL`) so a huge repo like
`civit/civitai` can't flood the LLM input, and a **global** `--limit-candidates` cap is
applied by round-robin interleaving so no single repo monopolizes the budget.

## Reply-feedback loop (Stage 0)

The runs are no longer stateless: **your emailed REPLY to last week's digest steers the
next one.** Before synthesis, `feedback.py` reads back your reply over **IMAP** (stdlib
`imaplib`, the *same* Gmail app-password used for send — no new deps, no cluster/Postgres
path) and prepends last week's proposals + your reply to the LLM prompt as context, with
an instruction to *drop what you rejected and honor your steering* while still surfacing
genuinely new evidence-backed candidates. It's context only — the structural evidence /
anti-slop rules still fully apply.

- **Matching:** searches recent mail SINCE the last digest for the ASCII-stable subject
  core `Repo proposals` (the full subject's 🧭 + em-dash are flaky in IMAP SEARCH), then
  picks the most-recent genuine **reply from you** (`Re:` / `In-Reply-To`), so the
  original digest isn't mistaken for feedback. Looks in `INBOX`, falls back to
  `[Gmail]/All Mail`.
- **De-quoting:** drops `>`-quoted lines and everything from the `On … wrote:` attribution
  onward, leaving only your new words (capped 4000 chars).
- **Best-effort + safe:** no prior digest / no creds / IMAP down / no reply / parse error
  → logged to stderr and skipped; the run proceeds exactly as a stateless one. `--no-feedback`
  skips the fetch entirely (clean / testing runs); `--json` reports `feedback_applied`.
- **Limits:** relies on subject-thread matching — if Gmail groups a reply under a
  different subject (or you compose fresh instead of replying), it won't be found. The
  loop closes naturally because each run persists its own subject → next run's "previous".

## Deterministic repo-exclusion layer (Stage 0.5) — `exclusions.py`

Context-injection alone proved **too weak**: a reply that said `1. this project is paused
/ … / 5. we are not the code owner for that repo` was *ignored* — the model re-proposed
the exact paused repos. So a reply that scopes out a repo now becomes a **HARD,
DETERMINISTIC filter** (no LLM) that DROPS those repos from the scan before synthesis ever
runs — they **cannot reappear**. (The context-injection above is kept for nuance; this
*adds* the hard layer.)

- **Positional mapping (primary):** a line starting `N.` / `N)` / `N -` / `#N` / `N:` maps
  to proposal **N** in the digest you actually saw → that proposal's repo.
- **Keyword intent (deterministic set):** `paused / skip / ignore / stop / drop / remove /
  don't / do not / won't / leave it / not (mine|ours|relevant|interested) / no / archived /
  deprecated` → **exclude**. `not (the|code) owner` / `deprecated` / `archived` → the
  exclusion is **permanent** (vs. `paused`, which is undoable). `resume / unpause /
  un-exclude / re-enable / reactivate / bring back / … again` + a repo ref → **resume**
  (un-exclude).
- **Name mentions:** a reply naming a repo/alias (`kubeclaw`, `civitai`, `homelab`,
  `datapacket`, …) with an exclude/resume intent applies even without a position number.
- **Position source = the digest you SAW, not `latest.json`.** Proposals rotate run-to-run
  and every run overwrites `latest.json`, so `--email` also writes **`last_emailed.json`**
  (the emailed set). `parse_reply` maps `1./2./…` against `last_emailed.json` → else the
  newest `history/*.json` with `emailed:true` → else `latest.json`.
- **State:** `~/.config/repo-cos/exclusions.json` (`{repos:{<name>:{reason, excluded_at,
  permanent, source}}}`) — **hand-editable**. Robust to a missing/corrupt file (→ empty).
- **Visibility + undo:** excluded repos are surfaced as a digest footer (`Excluded
  (paused/not-yours): … — reply "resume <repo>" to re-enable.`). `--show-exclusions` prints
  the current state and exits; `--no-feedback` skips the reply parse too.
- **Limits:** the keyword set is finite — a reply that's *pure prose* with no positional
  anchor and no repo name won't exclude anything (it still reaches the LLM as context).
  Unparseable lines are ignored and never raise.

## Usage

```sh
# The free smoke test — Stage-1 only, no API key, no spend. Prints raw candidates.
scripts/repo-cos/scan.py --no-llm --repos "$HOME/workspace/devrc,$HOME/workspace/homelab-talos"

# Full dry run (DEFAULT) — Stage 1+2, prints the digest to stdout, sends NOTHING.
OPENROUTER_API_KEY=... scripts/repo-cos/scan.py --dry-run

# Send the digest (opt-in; default OFF). Same body the dry-run prints.
OPENROUTER_API_KEY=... scripts/repo-cos/scan.py --email
```

Run under nix-shell (stdlib + requests only):

```sh
nix-shell -p 'python3.withPackages(p:[p.requests])' --run \
  'python scripts/repo-cos/scan.py --dry-run'
```

Flags: `--dry-run` (default), `--email`, `--no-llm`/`--candidates-only`, `--no-feedback`
(skip Stage 0 **and** the exclusion parse), `--show-exclusions` (print exclusion state +
exit), `--repos`, `--limit-candidates N` (default 60), `--top N` (default 5), `--model`
(default `deepseek/deepseek-v4-flash`), `--json`.

The weekly unit / `run-weekly.sh` need **no change**: `feedback.py` uses only stdlib
`imaplib` (already available) and the SAME app-password already decrypted for SMTP send —
so the reply-fetch adds no new system deps or secrets to the timer.

## Repos

Workbench-local. Discovers the default list, filtered to existing dirs. **`naida`/`vetr`
live only on the LAPTOP (`~/workspace/scratch/`)** so this workbench tool can't see them
yet.

## Email secret

`--email` reuses the **same Gmail app-password the mailbox sent-poller uses**: SOPS secret
`mailbox-gmail-imap` on homelab-talos `origin/trunk`
(`clusters/homelab/apps/mailbox/secrets-imap.enc.yaml`), keys `IMAP_USER` /
`IMAP_APP_PASSWORD` — verified against `sent-poller.yaml` which mounts the same secret.
The app password authenticates SMTP send just as it does IMAP read. Env overrides
`REPO_COS_SMTP_USER` / `REPO_COS_SMTP_PASSWORD` win if set.

## Step 2 (NOT built yet)

A weekly, `serverMode`-gated **workbench systemd user timer** (mirroring
`mail-actions-archive`) would run `scan.py --email` every Monday. It is deliberately NOT
wired: we validate signal quality on a manual `--dry-run` first, then add the timer +
home-manager unit in a follow-up once the proposals prove worth an inbox slot.

## Tests

```sh
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests])' --run \
  'python -m pytest scripts/repo-cos/tests -q'
```

Covers the deterministic parts with fixtures: marker/skip extraction (file:line correct
across languages), per-repo + global capping, churn/large/lockfile signals, the digest
formatter, and the LLM JSON parse/repair + anti-slop filters. HTTP (OpenRouter) and SMTP
are mocked — no live network in unit tests.
