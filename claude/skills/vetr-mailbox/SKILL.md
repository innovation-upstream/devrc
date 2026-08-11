---
name: vetr-mailbox
description: "Send 1:1 personalized email AS zach@vetr.com via Workspace SMTP — bounded, personalized, rate-limited, deduped, and it INBOXES (unlike Omnisend's cold bulk domain). Use for 1:1 vet-recruitment outreach to the Vetr waitlist and other genuinely personal founder email. NOT a bulk blaster — bulk owner campaigns belong on Omnisend."
---

# vetr-mailbox — send AS zach@vetr.com (Workspace SMTP)

Outbound-only 1:1 email for the Vetr launch: the `/mailbox` send-as path
(`smtp.gmail.com:587`, STARTTLS with a **verifying** TLS context, app-password login)
repointed at `zach@vetr.com`.

**Why:** proven 2026-07-05 — a manual send from `zach@vetr.com` inboxes while the same
domain via Omnisend (Mailgun shared IP, bulk) lands in spam. Workspace 1:1 mail = trusted
sender reputation + reads like human correspondence.

## 🔴 Guardrails (this is zach's PRIMARY business mailbox — do not torch it)

- **1:1, personalized, bounded audiences ONLY.** Built for VET recruitment (~164 CA+FL
  waitlist vets) — high-value, genuinely personal founder notes.
- **NOT for bulk owner blasts.** Hundreds of templated emails to strangers from a Workspace
  mailbox risks: (1) Google throttling/**suspending the mailbox** (kills real business
  email), (2) **CAN-SPAM** — bulk commercial mail legally needs a working unsubscribe +
  physical address, (3) bounces/complaints **burn the reputation** that makes it inbox.
  Owner-demand campaigns stay on **Omnisend** (ESP handles unsubscribe/bounce/complaint
  hygiene); owner demand is gated behind vet supply anyway.
- **Rate-limit + stagger** (`--sleep`, default ~45s jittered), keep `--limit` small (≤~20–25
  /run, well under Workspace's ~2,000/day external cap and abuse heuristics).
- **Dedup is automatic** (append-only send log) — a contact never gets two of the same.
- **Nothing sends without `--send`.** Always `--dry-run` first.
- Confirm the body + batch with zach before any outward send (publishes as him).

## Setup (one-time)

1. **[H] Workspace app-password for `zach@vetr.com`:** myaccount.google.com (as zach@vetr.com)
   → Security → 2-Step Verification (must be ON) → App passwords → generate → copy the
   16-char value. (If "App passwords" is missing, the Workspace admin console must allow it:
   Security → Less secure apps / App passwords. zach is the admin.)
2. Store creds at **`~/.config/vetr/vetr-mailbox.env`** (`chmod 600`); add a pointer in
   `~/.config/vetr` / CREDENTIALS.md like the other vetr secrets:
   ```
   VETR_SMTP_USER=zach@vetr.com
   VETR_SMTP_APP_PASSWORD=xxxx xxxx xxxx xxxx
   VETR_SMTP_FROM_NAME=Zach Lowden
   VETR_REPLY_TO=zach@vetr.com
   ```

## Send (the loop)

Recipients = a JSON array of `{"email","name"}`. Body = plain text with `{{first_name}}`
`{{last_name}}` `{{name}}` placeholders. Vet-recruitment copy lives in the vet-launch skill:
`.claude/skills/vet-launch/vet-1to1-outreach.md`.

```bash
# 1) DRY RUN — renders + lists who would get it, sends nothing:
python3 ~/.claude/skills/vetr-mailbox/send.py \
  --recipients /path/batch.json --subject "Quick question for a California vet" \
  --body /path/body.txt --dry-run
# 2) test to yourself first (your own address in a 1-row batch.json), with --send
# 3) real batch, capped + staggered:
python3 ~/.claude/skills/vetr-mailbox/send.py \
  --recipients /path/batch.json --subject "Quick question for a California vet" \
  --body /path/body.txt --limit 15 --sleep 45 --campaign vet_1to1_ca --send
```
- **HTML (optional):** `--html body.html` → `multipart/alternative` on top of the `--body`
  text (always kept as fallback). ⚠️ **Keep HTML LIGHT** (formatted text, one CTA button, no
  big images) — heavy marketing HTML is the promotional fingerprint that spam-folders; this
  channel inboxes precisely because it reads like personal mail. Placeholders render in both
  parts.
- Skips anyone already in `~/.config/vetr/vetr-mailbox-sent.jsonl` (dedup) or
  `~/.config/vetr/vetr-mailbox-suppress.txt` (opt-outs — add anyone who says "stop").
- Aborts if a `{{ }}` placeholder didn't render, or on the first SMTP failure (logs how many
  sent before it — safe to re-run; dedup skips the already-sent).

## Building a vet recipients batch

- **FL vets** already have name+email: `.claude/skills/vet-launch/fl-vets-areacode-ids.json`
  (array of `{email,name,...}`) → filter/slice to a batch.
- **CA vets** are Omnisend contact-IDs only (`ca-vets-areacode-ids.json`) → resolve
  email+name via Omnisend `get_contacts`/contact GET, then build the batch JSON.
- Exclude anyone already Omnisend-emailed for a clean split (the dedup log only tracks THIS
  tool's sends).

## Managing replies (v1 = Workspace inbox)

Replies to `zach@vetr.com` land in the normal Vetr Workspace inbox — handle them there and
add responders to a "do-not-re-mail / interested" note. **Optional upgrade** to manage them
like the `/mailbox` store: (a) Gmail-forward `zach@vetr.com` → an `@inbox.zacx.dev` address
(replies flow into the Postgres `mail` table, queryable), or (b) IMAP-poll `zach@vetr.com`
with the same app-password. Defer until send volume warrants.

## Relation to other tools

- **`/mailbox`** — the original send-as (personal `zachlowden1@gmail.com`) + self-hosted
  inbox. This skill is its Vetr-Workspace sibling for outbound launch mail.
- **`vet-launch`** — the campaign runbook (Omnisend segments, variants, funnel). vetr-mailbox
  is the 1:1 supply channel that bypasses Omnisend's deliverability problem.
