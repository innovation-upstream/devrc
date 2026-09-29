# muse.ai — dispatching tasks to the user's Meta Muse agent

**Load this when:** a result envelope named this file in `site_flows` · you are
about to send a task to, or poll a reply from, the user's Muse personal agent ·
a read of muse.ai looks empty or stale · you are about to conclude a send
"did nothing" · you need the agent's goals/plan state.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md` (`reference/…` paths
below resolve from that bridge dir). Mechanism files stay authoritative for
mechanism: throttling and `wake` → `reference/spa-wake.md`; envelopes and
getting a selector out of a read → `reference/read-envelopes.md`. This file is
only what is true of **muse.ai**.

---

## 🔴 This is the user's LIVE Muse account, on Meta's side of the wire

- **NEVER touch the user's own muse.ai tab** — no `nav`, no `type`, no click.
  Re-resolve the ref they gave you with `context` immediately before use (it may
  be closed or moved); then **`open https://muse.ai` on that instance** and work
  only in your own tab. `close` every tab you own before finishing.
- **Tasks transit Meta's infrastructure.** Never send hostnames, IPs, tokens,
  cluster names or credentials as task text. Tasks must be self-contained,
  low-stakes and free of anything you would not post publicly.
- **NEVER `activate`** (screen theft). A hidden tab is fixed with `wake` /
  `--wake=3000` — a wake ends at detach, so pass `--wake=3000` on every read op
  that must observe live state. `type`/`key` take no `--wake` (read-op flag
  only); assert their effect with a wake'd `js` read instead.

## Selectors (mapped live 2026-09-29)

| what | selector |
|---|---|
| composer | `textarea[placeholder="Message"]` (also `aria-label="Message"`) |
| submit | **Enter** — there is NO `<form>` and no send button: `key Enter --selector 'textarea[placeholder="Message"]'` |
| scroll root | `#hatch-chat-scroll` |
| message feed | `[role="log"]` (`aria-live="polite"`), inner wrapper `div.max-w-3xl` |
| one message | `div[data-message-item="true"]` |
| message role | `data-message-role="assistant"\|"user"` — THE discriminator. **Id prefixes are NOT**: user ids are UUIDs and assistant ids are `assistant-msg-*` **or** plain UUIDs, so only the role attribute is authoritative |
| turn grouping | `data-message-turn-id` groups one multi-part reply; `data-message-group-position` = `first`/`middle`/`last`/`single` |
| card / presentation | `data-message-has-presentation="true"` (goal/plan/task cards, "By Muse" chips) — a card can be a separate node in the same turn |
| date separators | `div.text-center` with a date/time string, no data attrs — skip in polls |
| sidebar goals | left-rail `section.flex.flex-col` rows (goal titles + relative times) — no stable ids; read via a byte-capped `text`, never deep selectors |

UI text is mixed Spanish/English. Select on **attributes**, never on label text.

## Send-task flow

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
TAB=…                       # your OWN tab id (from `open`); never the user's
REF=bw://laptop/<instance>/$TAB

# 0. snapshot BEFORE send — the delta baseline
$BB $REF js '(function(){var a=document.querySelectorAll("[data-message-role=assistant]");var l=a[a.length-1];return "asst="+a.length+" lastId="+(l?l.getAttribute("data-message-id"):"none")})()' --wake=3000

# 1. type + ASSERT it took (React composers can silently drop a synthetic type)
$BB $REF type "<task>" --selector 'textarea[placeholder="Message"]'
$BB $REF js '(function(){var t=document.querySelector("textarea[placeholder=Message]");return "len="+(t?t.value.length:"gone")})()' --wake=3000
# len must equal the task length; retry the type if not — do NOT press Enter blind

# 2. submit + CONFIRM submission (composer cleared AND a new user message)
$BB $REF key Enter --selector 'textarea[placeholder="Message"]'
sleep 4
$BB $REF js '(function(){var t=document.querySelector("textarea[placeholder=Message]");var u=document.querySelectorAll("[data-message-role=user]");return "taLen="+(t?t.value.length:"gone")+" user="+u.length})()' --wake=3000
# taLen=0 + user count above the pre-send value = submitted
```

A `taLen` still equal to the typed length after Enter means the keypress was
inert (re-throttled tab) — re-`wake`, re-`type`, re-`key Enter`.

## Poll-reply flow (delta-only)

```bash
# poll every ~5 s until asst count + id + innerText len are IDENTICAL twice.
# Measured: first reply ≤14 s after Enter, stable ≤23 s. Cap the wait ~3 min.
$BB $REF js '(function(){var a=document.querySelectorAll("[data-message-role=assistant]");var l=a[a.length-1];return "asst="+a.length+" id="+(l?l.getAttribute("data-message-id"):"none")+" len="+(l?(l.innerText||"").length:0)+" tail="+(l?(l.innerText||"").replace(/\s+/g," ").slice(-30):"")})()' --wake=3000

# the DELTA = only the nodes of the newest turn, never the whole feed:
$BB $REF js '(function(){var a=document.querySelectorAll("[data-message-role=assistant]");var l=a[a.length-1];var turn=l.getAttribute("data-message-turn-id");var p=[];for(var i=0;i<a.length;i++){if(a[i].getAttribute("data-message-turn-id")===turn)p.push((a[i].innerText||"").trim())}return p.join("\n---\n")})()' --wake=3000
```

Delta-only rules: byte-capped `text` over `html` always; return only the newest
turn's text (a turn can carry a trailing presentation card — keep it, it is the
plan/task output the user asked about); no screenshots unless diagnosis needs
them. "No reply yet" after ~3 min: re-`wake` once (a re-throttled tab looks
exactly like silence) before concluding anything.

## Status flow (goals / plan state)

- In-chat plan/goal cards: read the newest `div[data-message-has-presentation="true"]`
  nodes' `innerText` (byte-capped). Cards are turn-grouped like messages.
- Sidebar goal list: byte-capped `text` on the left rail — rows carry a title
  plus a relative time ("1h", "10:22 am"). No stable ids; do not selector-hunt.

## 🔴 DOM churn: re-map procedure

muse.ai is a young, fast-moving SPA — these selectors WILL rot. On a failed
selector (or an empty read that a `--wake=3000` re-read does not fix):

1. Re-`context` your own tab (make sure you are still on muse.ai, not an
   interstitial).
2. Re-derive the composer: `js` enumerate `textarea,input,[contenteditable=true]`
   → tag + `placeholder` + `aria-label`. The composer is the visible
   message-taking control near the blurred footer bar.
3. Re-derive the message contract: `js`-enumerate
   `[data-message-item]`-shaped nodes — dump `data-message-*` attribute NAMES of
   the first few (the names are the contract; the values change per message).
   If `data-message-item` is gone, walk up from known feed text (`#hatch-chat-scroll`
   may have been renamed too) and re-read the wrappers.
4. Update THIS file's table with the new names and the date, then commit — the
   cached selectors are only as good as the last date on them.
