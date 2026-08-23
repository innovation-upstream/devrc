# clawgate hooks — install on another host, and the Stop / Suggestions hook

Read when: wiring clawgate's hooks onto a new machine, or debugging the "Suggested next step"
(Stop hook) path. Day-to-day hook management lives in the core SKILL.md.

## Install the PermissionRequest hook on another host (e.g. laptop, ssh `zach@10.42.0.100`)

1. **Reachability decides the API URL.**
   - Homelab LAN → `http://192.168.50.250:30302`.
   - A **nebula** host (10.42.0.x, e.g. the laptop) → **`http://10.42.0.10:8109`** (homelab gateway
     → clawgate; hook-token auth — check with `clawgatectl --api-url http://10.42.0.10:8109 health`).
   - ⚠ The public `clawgate.zacx.dev` sits behind Authelia passkey (forward-auth), so **do not use
     it for the machine hook** — use a hook-token-gated path.
2. Copy the hook script:
   ```bash
   cat hook/clawgate-hook.sh | ssh HOST 'mkdir -p ~/.claude; cat > ~/.claude/clawgate-hook.sh && chmod +x ~/.claude/clawgate-hook.sh'
   ```
3. Write `~/.claude/clawgate.env` on the host with the reachable `CLAWGATE_API_URL` + the shared
   `CLAWGATE_HOOK_TOKEN` — **pipe it via stdin so the token isn't in the command line.**
4. Point its `~/.claude/settings.json` PermissionRequest at it, labelling the host (**back up
   settings.json first**):
   ```bash
   jq '.hooks.PermissionRequest = [{"hooks":[{"type":"command","command":"CLAUDE_HOST=<label> /home/<user>/.claude/clawgate-hook.sh","timeout":180}]}]'
   ```
   ⚠ `CLAUDE_HOST` **must be a command prefix** — the hook reads it before sourcing `clawgate.env`.
   Prereqs on the host: `jq`, `curl`.
5. Test:
   ```bash
   printf '<mock PermissionRequest json>' | CLAUDE_HOST=<label> CLAWGATE_HOOK_DEADLINE=8 ~/.claude/clawgate-hook.sh
   ```
   `~/.claude/clawgate-hook.log` should show `send ... host=<label> -> <url>`. Takes effect in the
   host's next new Claude session.

## Stop hook — "Suggested next step" (0.7.23)

`clawgate-stop-hook.sh` is registered (async) in `hooks.Stop` **alongside an unrelated tmux
task-hook — preserve both when editing** (`jq '.hooks.Stop' ~/.claude/settings.json`).

It fires on every turn end, ships `{session_id, project, cwd, message, transcript_tail}` to
`POST /api/suggest` (hook token), and **always exits 0**. Kill-switch `CLAWGATE_SUGGEST=off`.

Generation runs server-side (OpenRouter `anthropic/claude-haiku-4.5`, key
`CLAWGATE_OPENROUTER_API_KEY`; falls back to a deterministic stub when the key is empty or
`CLAWGATE_SUGGEST_STUB=1`) ONLY for per-project-opt-in projects (throttled 1/10min), or on demand
from the 💡 Suggestions tab.

Tests: `bats hook/tests/clawgate-stop-hook.bats`.

Live test — ⚠ **stays curl: `clawgatectl` has no `/api/suggest` verb** (nor `/api/send`), so the
`B=`/`HOOK=` preamble survives here and only here:
```bash
B=http://192.168.50.250:30302
HOOK=$(grep '^CLAWGATE_HOOK_TOKEN=' ~/.claude/clawgate.env | cut -d= -f2)
curl -s -X POST "$B/api/suggest" -H "Authorization: Bearer $HOOK" -H 'Content-Type: application/json' \
  -d '{"session_id":"t1","project":"clawgate","cwd":"/x","message":"done","transcript_tail":"...JSONL..."}'
```
then open `/suggestions` (a card per session; tap its context for the scroll-back detail view) and
click "Suggest next step".

### `message` extraction (0.7.28+)
The real Claude Code Stop hook ships `transcript_path` + a `transcript_tail` but **NO usable
`message`**, so the hook itself scans the transcript (from the END, last ~2MB) for the last
assistant record WITH text — **structurally via jq, NOT regex**. The JSONL schema varies:
top-level `type:"assistant"` vs `message.role:"assistant"`, and type-AFTER-content field order. The
server also re-derives from the tail at ingest (`suggest.LastAssistantText`) as a fallback.

🔑 **Lesson: parse the transcript structurally; a field-order regex silently missed the old
schema.**

### Back-filling existing blank cards
After an extraction/tail change, re-POST `/api/suggest` per session with a freshly-extracted message
+ a bigger tail from the on-disk transcript at `~/.claude/projects/*/<session_id>.jsonl`
(session_id = the transcript filename). ⚠ Pass the big tail via `jq --rawfile` (a 512KB `--arg`
blows `ARG_MAX`) and `curl --data-binary @file`.

## PermissionRequest hook semantics (verified against the code, vs the docs)

`PermissionRequest` fires **ONLY when approval is actually needed**. Its output is:

```json
{"hookSpecificOutput":{"hookEventName":"PermissionRequest","decision":{"behavior":"allow"}}}
```

(`"behavior"` is `"allow"` or `"deny"`.)

- It has **NO reason/context channel** — an approver comment is **record-only**; only `PreToolUse`
  can steer the model (`additionalContext`). Never design on feeding an approver's words back into
  the session through this hook.
### Every path that DEFERS (this list is exhaustive; verified against `hook/clawgate-hook.sh` 2026-08-12)
Two of these defer **before any network call**, so a missing card is not evidence clawgate is down:

| # | condition | contacts the server? |
|---|---|---|
| 1 | `permission_mode` is `bypassPermissions` or `plan` (`clawgate-hook.sh:67`) — the user already chose to bypass, so don't intercept | **no** |
| 2 | tool is `AskUserQuestion` (`:79`) — an interactive "pick an option" prompt, not allow/deny-routable | **no** |
| 3 | any non-approve/reject decision (e.g. an `ignore`/dismiss) | yes |
| 4 | any error / timeout / unreachable server — fail-safe, behaves as if the hook were absent; this is why a clawgate outage never blocks Claude Code | attempted |

🔴 **Debugging "no card appeared"? Rule out 1 and 2 first** — they produce exactly the same
observable as an outage, and `log`-lines in the hook (`permission_mode=…; deferring` /
`is an interactive question prompt`) are the only thing that distinguishes them.

## What clawgate can and cannot carry for the proactivity gate

RULES.md's "Default to PROCEEDING" tree names four triggers, only TWO of which can end in a
question (Fork, and Outward-facing/irreversible/high-blast-radius — Out of scope and Named
hazard both route to "don't ask"). Clawgate is the **transport** for those questions when Zach is not at the tmux
window — it is **not** the gate. Verified 2026-08-22 against live `0.7.98`.

🔴 **`approve-with-comment` is NOT the Fork branch, however much it looks like one.** The
comment is **record-only** — `clawgate-hook.sh` logs it and emits a bare `allow`/`deny`
(§"PermissionRequest hook semantics" above, and the script's own comment: *"No
reason/additionalContext channel exists"*). An agent that turns a Fork into a card gets
back **permission, never an answer**, and proceeds down whichever reading it had already
picked — the exact ship-then-rework the Fork branch exists to prevent, while the operator
believes they answered. A Fork must reach a human through a channel that can carry prose.

That leaves clawgate carrying **less** of the tree than its shape suggests, which matters
because stranded windows are a measured problem here (`window-triage`, `session-manager`
exist for it): a rule that adds ask-branches without changing where the ask LANDS makes it
worse, not better — and this hook is not that change.

**What it structurally cannot carry.**

| trigger | reaches the phone? |
|---|---|
| Out of scope | **No** — semantic, produces no `PermissionRequest`. File a task instead (`flows/task-authoring.md` — note the criteria-less-create denial comes from a devrc PreToolUse hook, `clawgate-task-interview-guard.py`, NOT this one; and an agent caps at `ready_for_review` regardless of criteria, `taskstatus.go:79-81`). |
| Fork | **No.** A design fork raises no `PermissionRequest` at all; and even when one does surface, the return channel is binary — see the 🔴 above. Never route a Fork here. |
| Outward-facing / irreversible | **Partially** — an allowlisted command never prompts, so it never reaches the phone at all. |
| Named hazard | **No, and must not** — a named hazard's response is stage-it or hand-it-over, never solicit approval. |

Plus the defer paths in the section above: `bypassPermissions` / `plan` / `AskUserQuestion`
never contact the server, and any outage or timeout defers to the terminal. The hook is
**fail-safe toward proceeding**, so no rule may be gated on it.

🔴 **Budget the asks — one per task.** `requireSession` is a literal `return next`
(`internal/api/auth.go`), and `POST /api/auto-approve-all` is registered behind it
(`internal/api/server.go`), so the LAN NodePort can arm a **global** auto-approve window over
every future request in every project with no app-level auth. ⚠ That posture is **deliberate,
not an oversight** — the same file states it: human auth was removed in favour of the Authelia
forward-auth edge on the public path, and the LAN is treated as trusted-open. The hazard is
therefore about BLAST RADIUS on a trusted LAN, not a missing gate. Notification fatigue is
therefore not a comfort problem — it is the thing that arms that lever and kills every gate
at once, silently. RULES.md's "a permanently-red gate trains everyone to click through" has
this mirror image: **a too-noisy gate trains you to disable it globally.**

⚠ **The tree does NOT hand you this budget — do not cite it as if it did.** Its "a Fork ANSWER
buys the whole run" clause is scoped to Forks, and a Fork never reaches this hook (table above).
The only branch that does is Outward-facing/irreversible, which the tree governs with "an
APPROVAL covers only the step it was given for" — i.e. **per-step by design**. So the
one-ask-per-task budget is THIS file's, resting on the auto-approve-all blast radius above, and
it has to be argued here rather than borrowed. Batch what can be batched; never let one run emit
a stream of cards.
