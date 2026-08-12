# clawgate hooks — install on another host, and the Stop / Suggestions hook

Read when: wiring clawgate's hooks onto a new machine, or debugging the "Suggested next step"
(Stop hook) path. Day-to-day hook management lives in the core SKILL.md.

## Install the PermissionRequest hook on another host (e.g. laptop, ssh `zach@10.42.0.100`)

1. **Reachability decides the API URL.**
   - Homelab LAN → `http://192.168.50.250:30302`.
   - A **nebula** host (10.42.0.x, e.g. the laptop) → **`http://10.42.0.10:8109`** (homelab gateway
     → clawgate; hook-token auth — `curl .../health` returns 200).
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

Live test:
```bash
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

- It has **NO reason/context channel** — an approver comment is **record-only**. Only `PreToolUse`
  can steer the model, via `additionalContext`. Do not design a feature that relies on feeding an
  approver's words back into the session through this hook.
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
