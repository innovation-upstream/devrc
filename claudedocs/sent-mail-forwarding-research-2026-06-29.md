# Capturing Gmail SENT mail into the self-hosted inbox

**Date:** 2026-06-29
**Goal:** Get Zach's *outgoing* (SENT) Gmail into the homelab Postgres `mail` table so `mail-actions reconcile_owner_replies` can auto-close action items when he replies.
**Constraint recap (load-bearing):** self-hosted on the homelab, no paid third party, **Gmail REST API rejected**, **Cloudflare Workers / SaaS rejected**. Reuse existing infra (k8s, aiosmtpd `mail-receiver`, Postgres, nebula, Hetzner SMTP gateway).

## TL;DR recommendation

**Build a small self-hosted IMAP poller in the `mailbox` namespace** that connects to Gmail over IMAP (app-password), pulls *new* messages from `[Gmail]/Sent Mail`, and **re-injects the raw RFC822 bytes via SMTP into the existing `mail-receiver`** (homelab gateway `:2525` / NodePort `30026`). It reuses the whole parse + dedup + store path, preserves `Message-ID`/`References`/`In-Reply-To` byte-for-byte (so dedup and `thread_key` matching just work), and the only consumer change needed is **none** — `reconcile_owner_replies` already queries owner mail regardless of `via_gmail`. The single biggest caveat: it requires a **Gmail app-password** (a long-lived credential broader than "just sent mail" — it grants full mailbox IMAP), stored as a k8s secret.

---

## How the system actually behaves (verified from source, not assumed)

These are the facts every option below is judged against — confirmed by reading `receiver.py`, `extract.py`, `_db.py`, and `configmap-schema.yaml`:

- **Dedup** is `INSERT … ON CONFLICT (message_id) DO NOTHING`. Any path that delivers a message with a **stable, original `Message-ID`** is idempotent — re-delivering the same sent message is a no-op.
- **`via_gmail`** is a STORED generated column = `headers->>'Delivered-To'` matches his gmail. Sent mail has **no `Delivered-To`** header → it will land with **`via_gmail = false`**. That is fine and in fact correct:
  - `fetch_unprocessed` (the LLM action pipeline) filters `WHERE via_gmail` → sent mail is **never sent to the LLM** as a candidate action. Good (it's not an inbound ask).
  - `fetch_owner_messages` (used by `reconcile_owner_replies`) is **deliberately NOT restricted to `via_gmail`** — it matches `WHERE lower(from_addr) = ANY(owners)`. So owner sent mail with `via_gmail=false` *is* picked up for auto-close. This is exactly the intended design; the code comment even says "the owner's BCC'd/forwarded sent mail may have via_gmail=false."
- **`category`** is a heuristic on `from_addr`; for owner sent mail `from_addr` is his own address → it'll fall through to `'personal'`. Harmless; reconcile doesn't read `category`.
- **`thread_key`** is computed in Python from `References[0]` → `In-Reply-To` → own `Message-ID`. **Requires the original threading headers to survive ingestion.** This is the crux that kills the GmailApp-forward option (see below).
- **No loop risk** from re-injection: the receiver's onward relay is OFF (`MAILPIT_HOST` empty), and re-injection targets the receiver's front door directly, not a relay that points back at it. A re-injected message is stored once (new Message-ID) then deduped on any retry.
- **Distinguishing sent mail in the table:** there is no `direction` column today. Recommended marker is a **`labels` entry `'sent'`** stamped by the ingester (the `labels text[]` column already exists and is used elsewhere). `from_addr ∈ owners` is already a reliable implicit signal, but an explicit `'sent'` label makes ad-hoc queries and any future logic unambiguous without a schema migration. (A generated `direction` column is the heavier alternative — not needed.)

---

## Approach comparison

| Approach | Self-hosted / Google-dep | Completeness | Reliability & latency | Credential exposure | Setup / maint. | Loop/dedup risk | Threading fidelity |
|---|---|---|---|---|---|---|---|
| **1. Gmail filter `from:me → forward`** | Google-side, no infra | **Fails — filters act on INCOMING only** | n/a | none | trivial but **doesn't work** | n/a | n/a |
| **2. Auto-BCC `<x>@inbox.zacx.dev`** | manual habit / 3rd-party ext | **Partial — only when he remembers / only matching rules; misses replies-in-thread** | sub-minute when it fires | none (manual) / ext sees all mail | trivial (manual) or ext install | low (stable Msg-ID) | **degraded** — BCC copy may lack/alter threading headers |
| **3. Google Apps Script (time-driven, reads SENT label, forwards)** | Runs on Google infra, **Google-dependent, not self-hosted**; **but NOT the REST API** | Good — sweeps SENT label | poll interval (≈1–15 min); 90 min/day total trigger budget; **100 emails/day forward cap** | none beyond his own Google session (script runs as him) | moderate; bound to Google account, opaque to homelab | low if it carries a stable id | **POOR — `GmailApp.forward` does not preserve `Message-ID`/`References`/`In-Reply-To`**; would need Gmail REST API (rejected) to fix |
| **4. Self-hosted IMAP poller → re-inject via mail-receiver** ⭐ | **Most self-hosted** — logic runs in his cluster; uses IMAP app-password, **not the REST API** | **Full — sweeps `[Gmail]/Sent Mail`, every sent message** | poll loop (e.g. 60s) or IMAP IDLE; seconds-to-minute latency | **Gmail app-password** (full-mailbox IMAP) in a k8s secret | moderate (one new Deployment, ~150 LOC) | **none** — replays raw bytes, dedup on original Msg-ID | **perfect** — raw RFC822 replayed verbatim, all headers intact |
| 5a. fetchmail/getmail/isync vs Sent folder | self-hosted | full | poll | app-password | reuse existing tools but awkward re-inject step | none | perfect (if it re-injects raw) — but you still write the re-inject glue, so #4 is cleaner |
| 5b. Gmail delegation | Workspace-only feature; n/a personal | n/a | n/a | n/a | n/a | n/a | n/a |

---

## Per-approach detail + evidence

### 1. Gmail filter `from:me → Forward it` — **does not work**
Gmail's filter "Forward it" action and the Forwarding settings operate on **incoming mail only** ("Forward a copy of **incoming** mail to…"). There is no documented mechanism for a filter to fire on outgoing/sent messages. This is the most-cited misconception and it's the crux question — **confirmed negative.** Eliminated.
Sources: [Gmail Help — automatic forwarding](https://support.google.com/mail/answer/10957?hl=en).

### 2. Auto-BCC
Gmail has **no native auto-BCC** (confirmed). Options are a Chrome extension (third-party — flag; the extension sees all outgoing mail) or manually BCC `<x>@inbox.zacx.dev`. Manual BCC is **incomplete by construction** — it only captures mail he remembers to BCC, and crucially the cases that matter most for `reconcile_owner_replies` are *replies in an existing thread*, which is exactly when he's least likely to manually add a BCC. A BCC'd copy *would* route through the existing inbound chain (so via_gmail could even be true if Gmail forwarding also copies it), but threading-header fidelity on the BCC copy is not guaranteed. **Not reliable enough to be the primary mechanism.** Could be a zero-cost supplement.
Sources: [Pipedrive — auto-BCC workarounds](https://www.pipedrive.com/en/blog/automatic-bcc-in-gmail); [Gmail Community — no native auto-BCC](https://support.google.com/mail/thread/232403015/).

### 3. Google Apps Script
A time-driven trigger reads the SENT label and forwards new messages. Runs on Google's infra **as his account** — no third-party SaaS, no homelab footprint, but **Google-dependent and not "self-hosted."** Two hard problems:
- **Threading fidelity is poor.** `GmailApp.forward()` abstracts away low-level headers; it does **not** reliably preserve `Message-ID`, `References`, or `In-Reply-To`. A forwarded copy gets a *new* Message-ID and typically loses the threading chain — which breaks `thread_key` matching, the whole point. Preserving them would require the **Gmail REST API** (`Gmail.Users.Messages` advanced service / raw insert), which Zach **rejected**. So the compliant subset of Apps Script can't carry the headers reconcile needs.
- **Quotas:** consumer accounts get **~100 emails/day** via `GmailApp`/`MailApp`, and time-driven triggers share a **90-minute/day** total execution budget (each run ≤ 6 min). 100/day is probably fine for personal sent volume but is a real ceiling and an opaque failure mode (silent quota exhaustion mid-day).
- State tracking (avoid re-forwarding) is doable via a Gmail label (`forwarded-to-inbox`) or a stored last-seen timestamp in `PropertiesService`.
**Verdict: runner-up at best, and only if header loss were acceptable — it isn't.**
Sources: [Apps Script quotas](https://developers.google.com/apps-script/guides/services/quotas); [GmailMessage / GmailApp reference](https://developers.google.com/apps-script/reference/gmail/gmail-message); consumer 100-email/day cap corroborated across community threads.

### 4. Self-hosted IMAP poller re-injecting via the receiver — **RECOMMENDED**
A small service **on the homelab** opens an IMAP connection to Gmail, selects `[Gmail]/Sent Mail`, fetches new messages as **raw RFC822**, and replays each via SMTP to the existing `mail-receiver`. Because it replays the *original bytes*, every header (`Message-ID`, `Date`, `References`, `In-Reply-To`, `From`) is preserved exactly — so:
- **Dedup** works on the original `Message-ID` (idempotent across restarts/overlapping polls).
- **`thread_key`** computes correctly from the preserved `References`/`In-Reply-To`.
- **Zero consumer changes** — `reconcile_owner_replies` already matches owner `from_addr` regardless of `via_gmail`.

**Credential reality (verified, 2026):** Gmail still issues **app-passwords** for personal accounts with **2-Step Verification enabled** (not in Advanced Protection), and **IMAP is now always-on** (Google removed the enable/disable toggle in Jan 2025). Legacy basic-auth / "less secure apps" is dead, but app-password + IMAP/SMTP explicitly remains the supported path and was confirmed working in mid-2026. This is **not** the Gmail REST API and **not** OAuth — it's the classic IMAP credential, which is what Zach left open as "arguably different."
Caveat: an app-password grants **full mailbox IMAP access**, not a sent-only scope. That's the main security trade-off vs. the (rejected) scoped OAuth token.
Sources: [Gmail Help — app passwords](https://support.google.com/mail/answer/185833?hl=en); [Transition from less secure apps to OAuth](https://support.google.com/a/answer/14114704?hl=en); [App-password + IMAP confirmed working 2026](https://support.google.com/mail/thread/369628083/); [Less secure apps wind-down](https://workspaceupdates.googleblog.com/2023/09/winding-down-google-sync-and-less-secure-apps-support.html).

### 5. Other mechanisms
- **fetchmail / getmail / isync (mbsync)** against `[Gmail]/Sent Mail`: same IMAP-creds model as #4 and equally self-hosted, but these tools deliver to a Maildir/MDA — you'd still write glue to re-inject into the SMTP receiver. Option #4 (a purpose-built ~150-line poller that fetches-and-SMTP-replays) is simpler and keeps a single code path. If you'd rather not maintain Python, `getmail` with an MDA that pipes to `sendmail`→receiver is a legitimate variant.
- **Gmail delegation**: a Workspace admin feature for shared mailbox access; not applicable to a personal `@gmail.com` and doesn't expose sent mail to a third store anyway. N/A.

---

## Implementation sketch (recommended option #4)

**Where it runs:** new `Deployment sent-sync` in ns `mailbox` (sits next to `mail-receiver` + `mailbox-postgres`). Single replica, no PVC — state lives in Postgres (see below).

**Credentials:** Gmail app-password in a new k8s secret `gmail-imap-auth` (keys `imap_user` = `zachlowden1@gmail.com`, `imap_app_password`). Mirror the `secrets.enc.yaml` SOPS pattern already used for `mailbox-postgres-auth`. Requires 2SV on; avoid Advanced Protection.

**Ingest loop (poll; IDLE optional later):**
1. Connect TLS IMAP `imap.gmail.com:993`, login app-password, `SELECT "[Gmail]/Sent Mail"` (read-only is fine).
2. Track last-seen position by **Gmail's stable `X-GM-MSGID`** (preferred — survives UID instability) *or* by `(UIDVALIDITY, last UID)`. Persist it in Postgres, e.g. a tiny `sync_state(folder text primary key, uidvalidity bigint, last_uid bigint, last_gm_msgid bigint)` table in the `mailbox` db — no new PVC, survives pod restarts. On `UIDVALIDITY` change, reset UID tracking (re-scan; dedup makes a re-scan harmless).
3. `FETCH` new messages' full raw bytes (`BODY.PEEK[]`).
4. For each, open SMTP to the receiver and replay the **raw bytes verbatim**:
   - From the **LAN/cluster**: SMTP to `mail-receiver.mailbox.svc:2525` directly (simplest — same as the NodePort `:30026` test path). No need to traverse the Hetzner gateway; that gateway is only for inbound public MX delivery.
   - `MAIL FROM`/`RCPT TO` envelope values are irrelevant to storage (the receiver parses the DATA blob, not the envelope) — use a benign placeholder rcpt like `sent@inbox.zacx.dev`.
5. Advance the persisted cursor only after the SMTP `250` (the receiver stores before returning 250; a crash mid-batch just re-fetches and dedups).

**Tagging sent mail in `mail`:** the receiver as-is won't know a message is "sent." Two clean options:
- **(A, no receiver change)** After re-injection, the poller issues a single `UPDATE mail SET labels = array_append(labels,'sent') WHERE message_id = $1 AND NOT ('sent' = ANY(labels))` against `mailbox-postgres` (it already needs DB access for `sync_state`). Keeps the receiver generic.
- **(B, receiver change)** Teach the receiver to add `'sent'` when the inbound SMTP carries a sentinel header (e.g. `X-Sent-Sync: 1`) the poller injects. Heavier (image rebuild) — prefer (A).

Either way the durable, query-friendly marker is **`'sent' ∈ labels`**. `via_gmail` will be `false` and `category` `'personal'` — both already handled / irrelevant for reconcile.

**How it plugs into `reconcile_owner_replies`:** **nothing to change.** Once a sent message is in `mail` with `from_addr ∈ owners` and intact `References`/`In-Reply-To`, the next `mail-actions run` (or the existing scheduled run) calls `fetch_owner_messages` → keys it by thread → closes any OPEN action in that thread whose `received_at` predates the sent message. The `via_gmail=false` is explicitly tolerated by that query. The `run`-loop's defensive `from_addr ∈ owners → label 'sent', skip LLM` guard also covers the (won't-happen, since `fetch_unprocessed` filters `via_gmail`) case where a sent row reached the survivor loop.

**Verification (before relying on it):** send yourself a test reply from Gmail in an existing thread that has an OPEN `mail_action`; confirm (a) a row lands in `mail` with the original `Message-ID` and `labels @> '{sent}'`, (b) `mail-actions run` reports `closed (owner): 1` for that thread. Don't claim it works until that exact path is exercised.

---

## Runner-up and why-not

- **Apps Script (#3) — runner-up.** Most attractive on "no homelab footprint, runs as him, no new long-lived IMAP credential." Rejected as primary because `GmailApp.forward` **drops the threading headers** `reconcile_owner_replies` depends on, and fixing that needs the Gmail REST API he rejected. Also Google-dependent (opaque quota/trigger failure modes) and not self-hosted, his #1 axis. *Could* be a fallback if the IMAP poller proves troublesome AND header loss were tolerable (it isn't, for thread matching).
- **Auto-BCC (#2)** — useful as a free, zero-maintenance *supplement* (manually BCC the inbox on important sends), but incomplete and unreliable for the replies-in-thread case; not a primary mechanism.
- **Gmail filter forward (#1)** — eliminated: filters only act on incoming mail.
- **getmail/fetchmail/isync (#5a)** — viable and equally self-hosted, but adds an MDA+glue layer; the purpose-built poller is a single, simpler code path.

## Open / UNVERIFIED items
- Exact wording of the latest `GmailApp.forward` header behavior (Message-ID/References) is inferred from the API reference + community reports, not a first-party "headers are stripped" statement — **UNVERIFIED** to the letter, but the conservative read (don't rely on it for threading) is safe.
- `X-GM-MSGID` (Gmail IMAP extension) as the cursor is the robust choice; standard `UIDVALIDITY/UID` is the portable fallback. Pick one at implementation time; both are documented IMAP/Gmail behaviors.
- Whether 2SV is already enabled on the account (prereq for app-password) — confirm on the Google side before building.
