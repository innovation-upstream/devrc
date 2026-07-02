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
| 0 | `feedback.py` | read Zach's reply to LAST digest → synthesis context (Postgres default; IMAP fallback) | none |
| 0.5 | `exclusions.py` | parse that reply → **HARD** repo-level exclusions **+ per-recommendation dismissals** (deterministic) | none |
| 1 | `prescan.py` | cheap grep/git signals, capped **per repo** | none |
| 2 | `llm.py` | OpenRouter clusters survivors → ranked JSON proposals | one call |
| 3 | `digest.py` | one formatter shared by stdout **and** email | none |
| 4 | `email_send.py` | send via his postfix RELAY (default; Gmail SMTP fallback), gated behind `--email` | none |

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
next one.** Before synthesis, `feedback.py` reads back your reply and prepends last week's
proposals + your reply to the LLM prompt as context, with an instruction to *drop what you
rejected and honor your steering* while still surfacing genuinely new evidence-backed
candidates. It's context only — the structural evidence / anti-slop rules still fully apply.

**Reply source (`REPO_COS_REPLY_SRC`, default `postgres`):**

- **postgres (DEFAULT) — his infra.** The digest's `Reply-To` is
  `repo-cos@inbox.zacx.dev`; your reply routes Gmail→his MX→mail-receiver→the homelab
  Postgres `mail` table. `feedback.py` queries for the most-recent row where
  `'repo-cos@inbox.zacx.dev' = ANY(to_addrs)` AND `from_addr ILIKE '%zachlowden1@gmail.com%'`
  AND `received_at >` the last digest's `generated_at`. **That WHERE clause IS the ownership
  gate.** It reuses the mail-actions DB helper (`scripts/mail-actions/_db.py`: `kubectl
  port-forward` to the homelab `mailbox-postgres` + psycopg2 + DSN-from-secret). `text_body`
  is already plain-text from the receiver, so only the quoted reply history is stripped.
- **imap (fallback) — Gmail.** `REPO_COS_REPLY_SRC=imap` reads the reply out of Gmail over
  IMAP (stdlib `imaplib`, the same Gmail app-password the `gmail` send path uses). Only
  relevant if the digest was sent via `REPO_COS_SEND=gmail`. Matching: searches recent mail
  SINCE the last digest for the ASCII-stable subject core `Repo proposals` (the full
  subject's 🧭 + em-dash are flaky in IMAP SEARCH), picks the most-recent genuine **reply
  from you** (`Re:` / `In-Reply-To`) so the original digest isn't mistaken for feedback,
  `INBOX` then `[Gmail]/All Mail`.

- **De-quoting (both):** drops `>`-quoted lines and everything from the `On … wrote:`
  attribution onward, leaving only your new words (capped 4000 chars).
- **Best-effort + safe (both):** no prior digest / no creds / source down / no reply / parse
  error → logged to stderr and skipped; the run proceeds exactly as a stateless one.
  `--no-feedback` skips the fetch entirely (clean / testing runs); `--json` reports
  `feedback_applied`.

## Deterministic repo-exclusion layer (Stage 0.5) — `exclusions.py`

Context-injection alone proved **too weak**: a reply that said `1. this project is paused
/ … / 5. we are not the code owner for that repo` was *ignored* — the model re-proposed
the exact paused repos. So a reply that scopes out a repo now becomes a **HARD,
DETERMINISTIC filter** (no LLM) that DROPS those repos from the scan before synthesis ever
runs — they **cannot reappear**. (The context-injection above is kept for nuance; this
*adds* the hard layer.)

- **Positional mapping (primary):** a line starting `N.` / `N)` / `N -` / `#N` / `N:` maps
  to proposal **N** in the digest you actually saw → that proposal's repo **and evidence**.
- **Two intents, split by a strict precedence** (a reply distinguishes *pause the repo* from
  *skip this one recommendation*):
  1. **repo-pause** — `paused / on hold / hold off` → **repo exclusion** (non-permanent).
  2. **repo-owner/dead** — `not (the|our|my|code) owner / not ours / deprecated / archived /
     dead / …` → **repo exclusion, permanent**. (`dont own the 3d model FEATURE` does **not**
     match — that's `dont own`, not `not owner`, and about a feature → falls to dismiss.)
  3. **recommendation-dismiss** — `skip / not needed / not relevant / dismiss / nah / no /
     don't (propose|want|need)` **when no repo-pause/owner language is present** → **dismiss
     THAT proposal** (collect its evidence `repo/file:line` refs); the **repo stays in scope**.
  Higher tiers win: a line with both `paused` and `skip` is a repo-pause. `resume / unpause /
  … again` beats all three → **resume** (un-exclude a repo).
- **Name mentions:** a reply naming a repo/alias (`kubeclaw`, `civitai`, `homelab`,
  `datapacket`, …) with a **repo-level** intent applies even without a position number.
  (Dismissal needs a *positional* line — there's no proposal to look up from a bare name.)
- **Position source = the digest you SAW, not `latest.json`.** Proposals rotate run-to-run
  and every run overwrites `latest.json`, so `--email` also writes **`last_emailed.json`**
  (the emailed set). `parse_reply` maps `1./2./…` against `last_emailed.json` → else the
  newest `history/*.json` with `emailed:true` → else `latest.json`.
- **State:** `~/.config/repo-cos/exclusions.json` — **hand-editable**, two keys:
  `{repos:{<name>:{reason, excluded_at, permanent, source}}}` (repo-level) and
  `{dismissed:{<repo/file:line>:{reason, dismissed_at, repo}}}` (per-recommendation). Robust
  to a missing/corrupt file **or an older file without `dismissed`** (→ empty). Dismissals
  accumulate and are never auto-removed — hand-edit the JSON to un-dismiss one.
- **Pre-scan dismiss filter (the guarantee):** `filter_candidates(candidates, state)` runs
  right after `prescan.scan_all(...)` and **before** synthesis — it drops any candidate whose
  `repo/file:line` ref is in `dismissed`, so a dismissed proposal's signal **never reaches
  the LLM and cannot re-form**, while the rest of that repo still surfaces. Logs
  `dismissed: N candidate(s) suppressed` to stderr.
- **Visibility + undo:** excluded repos surface as a digest footer (`Excluded
  (paused/not-yours): … — reply "resume <repo>" to re-enable.`); dismissals add a terse
  `Dismissed N past proposal(s).` line. `--show-exclusions` prints **both** (repos +
  each dismissed `repo/file:line` + reason) and exits; `--no-feedback` skips the reply parse.
- **Limits:** the keyword set is finite — a reply that's *pure prose* with no positional
  anchor and no repo name (and no clear skip/pause anchor) won't exclude or dismiss anything
  (it still reaches the LLM as context).
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

Run under nix-shell. The default relay-send + Postgres-read paths need `requests` +
`psycopg2` + `kubectl` on PATH:

```sh
nix-shell -p 'python3.withPackages(p:[p.requests p.psycopg2])' kubectl --run \
  'python scripts/repo-cos/scan.py --dry-run'
```

Flags: `--dry-run` (default), `--email`, `--no-llm`/`--candidates-only`, `--no-feedback`
(skip Stage 0 **and** the exclusion parse), `--show-exclusions` (print exclusion state +
exit), `--repos`, `--limit-candidates N` (default 60), `--top N` (default 5), `--model`
(default `deepseek/deepseek-v4-flash`), `--json`.

Env toggles: `REPO_COS_SEND=relay|gmail` (default `relay`), `REPO_COS_REPLY_SRC=postgres|imap`
(default `postgres`). Relay overrides: `REPO_COS_FROM`, `REPO_COS_REPLY_TO`,
`REPO_COS_PROD_KUBECONFIG` (production cluster). Postgres uses `KUBECONFIG` (homelab, via the
mail-actions `_db.py` helper).

## Repos

Workbench-local. Discovers the default list, filtered to existing dirs. **`naida`/`vetr`
live only on the LAPTOP (`~/workspace/scratch/`)** so this workbench tool can't see them
yet.

## Mail infra (self-hosted, default)

SEND, READ, and SIGN are all on Zach's own infra; Gmail is only where he happens to type
the reply.

- **SEND — postfix relay (default).** `email_send.py` sends via `service/postfix-relay` in
  ns `nebula` of the **production** cluster (`REPO_COS_PROD_KUBECONFIG`, default
  `~/workspace/homelab-talos/production-kubeconfig`) over a `kubectl port-forward` to :587.
  No SMTP auth — the relay trusts MYNETWORKS (127.0.0.0/8) over the localhost hop; it
  presents a `mail.zacx.dev` cert, so STARTTLS is done with hostname-verify OFF (justified:
  a localhost hop to our OWN relay, already tunneled through the authenticated k8s API).
  `From: repo-cos@mail.zacx.dev` (DKIM-signed by the relay; SPF/DMARC published → clean
  Gmail deliverability), `Reply-To: repo-cos@inbox.zacx.dev`.
- **READ — Postgres (default).** The reply is read from the homelab `mail` table (see the
  Stage-0 reply-feedback section) — no Gmail IMAP.
- **Gmail fallback.** `REPO_COS_SEND=gmail` / `REPO_COS_REPLY_SRC=imap` reuse the **same
  Gmail app-password the mailbox sent-poller uses**: SOPS secret `mailbox-gmail-imap` on
  homelab-talos `origin/trunk` (`clusters/homelab/apps/mailbox/secrets-imap.enc.yaml`), keys
  `IMAP_USER` / `IMAP_APP_PASSWORD`. Only these fallback paths need the app-password /
  `SOPS_AGE_KEY_FILE`. Env overrides `REPO_COS_SMTP_USER` / `REPO_COS_SMTP_PASSWORD` win.

## Weekly timer

LIVE: a `serverMode`-gated **workbench systemd user timer** (`repo-cos.timer`, Mon 08:00,
mirroring `mail-actions-archive`) runs `scan.py --email` via `run-weekly.sh`. The self-
hosted mail default means the weekly send now depends on the **production cluster** (relay)
+ the **homelab cluster** (postgres) + two `kubectl port-forward`s; both are best-effort so
a hiccup logs + skips rather than wedging. Check `systemctl --user list-timers | grep repo-cos`.

## Tests

```sh
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2])' --run \
  'python -m pytest scripts/repo-cos/tests -q'
```

Covers the deterministic parts with fixtures: marker/skip extraction (file:line correct
across languages), per-repo + global capping, churn/large/lockfile signals, the digest
formatter, the LLM JSON parse/repair + anti-slop filters, and BOTH mail paths — the relay
send (From/Reply-To/To headers, unverified-STARTTLS context) and the Postgres reply read
(ownership-gate SQL, cleaned reply, no-row/DB-error → None). HTTP (OpenRouter), SMTP, the
port-forward, and the DB cursor are all mocked — no live network / cluster in unit tests.
