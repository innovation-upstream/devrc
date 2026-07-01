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

Flags: `--dry-run` (default), `--email`, `--no-llm`/`--candidates-only`, `--repos`,
`--limit-candidates N` (default 60), `--top N` (default 5), `--model` (default
`deepseek/deepseek-v4-flash`), `--json`.

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
