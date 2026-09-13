# clawgate's two auth doors — the measured record

🔴 **ROUTED FROM `SKILL.md`'s Key facts.** The core carries the trap in three lines; this file
carries the evidence, because the evidence is what stops the claim being re-derived wrongly a
third time. **This page has been WRONG IN BOTH DIRECTIONS** — first asserting the LAN door was
open when it was gated, then asserting one route was `requireSession` when it is
`requireHookToken`. Re-measure before quoting it either way; the deployment env decides.

## The retraction, as written 2026-09-12

🔴 **RETRACTED 2026-09-12 — clawgate's HUMAN tier IS gated again; do not re-derive the old claim.**
This block read *"clawgate has NO human auth of its own (since 0.7.37): `requireSession` is a
pass-through no-op, so the LAN NodePort is fully unauthenticated"*. **Measured false** against the
live pod (0.8.32): `GET http://192.168.50.250:30302/tasks/1` → **`303` → `/login?next=%2Ftasks%2F1`**,
and `/tasks` likewise; `/login` answers 200, so `CLAWGATE_UI_PASSWORD` is set and the gate is real.
Re-measure before quoting either way — this flipped once and the deployment env is what decides it.

**The two doors take DIFFERENT credentials, and this is the trap:**

| door | wrapper | credential | measured |
|---|---|---|---|
| `/api/*` (machine) | `requireHookToken` | `Authorization: Bearer $CLAWGATE_HOOK_TOKEN` | `GET /api/tasks?limit=1` → **200** |
| `/tasks/{id}`, `/tasks`, the UI | `requireSession` | a **session cookie** | **303 → `/login`** — the hook token buys NOTHING here |

🔴 **So a working hook token is not evidence the UI is reachable**, and an agent that proves the API
door open has proven nothing about the human one. This is what made the extension's "open in
clawgate" links inert (PR #802): the token read the task list fine and every link hit a login form.

**Public host = TWO gates, not one.** `https://clawgate.zacx.dev/tasks/1` → **302** to
`login.zacx.dev` (Authelia), and clawgate's own `/login` still sits behind it — **clawgate does not
trust Authelia's forwarded headers** (`git grep -E 'Remote-User|forwardAuth' internal/api/` → no
hits). Passing Authelia does not mint a clawgate session.

⚠ `CLAWGATE_SECURE_COOKIES` is **unset** in the live deployment, and `internal/api/auth.go:67-70`
says it is off by default precisely so a cookie minted at the plain-HTTP LAN door is not silently
rejected. So a one-time LAN login **does** stick. Cookies still expire — both `clawgate_session`
and `authelia_session` were EXPIRED in workbench Brave `Default` on 2026-09-12.

`requireHookToken` is **enforce-when-set**: an empty token opens the machine endpoints. 🔴 **`POST
/api/auto-approve-all`** arms a global auto-approve window over **every** future request in **every**
project + sweeps the pending queue (checkpoints excepted) — **never fire it to test a theory.** Which
wrapper each of the 120 routes carries: `task-api.md` — and that inventory now inherits this
retraction, so re-measure a route's door rather than trusting a remembered "open".

## `DELETE /api/tasks/{id}` — corrected in the OTHER direction

(`dismissTask`; **no in-progress guard, deliberately**). ⚠ This said *"unauthenticated on the LAN"*;
it is **`requireHookToken`** (`server.go:699`), not `requireSession` — so it is wrong in the
OPPOSITE direction from the retraction above, and the correction does not make it safer: **every
agent and hook on this box already holds that token** (`~/.claude/clawgate.env`), so it is one curl
from anything that can read the file.

## `POST /agents` — corrected too

- **`POST /agents` is FORM-ENCODED, not JSON** (hence no `clawgatectl` verb). ⚠ This said *"behind
  the no-op `requireSession` → no auth on the LAN NodePort"* — **measured false 2026-09-12: a
  credential-less `POST /agents` on the LAN returns `401`.** `requireSession` enforces now (see the
  retraction above), so LAN dispatch needs a session. 🔴 The webhook rule is UNCHANGED and still
  right: a future webhook needs a **separate hostname**, never a path bypass on
  `clawgate.zacx.dev` — a bypass would put agent dispatch on the open internet.

## The idle-task reaper, retired (evicted from the core 2026-09-13)

Since **0.7.96** (`cf529d41`, live) the daily idle-task reaper **tags `stale` + posts a system
comment** instead of calling `dismissTask`, so **nothing destroys a task or an agent pod on a
timer**. `CLAWGATE_TASK_TTL` is still **unset in the deployment**, so the 7d default is LIVE — it
now costs a tag, not the task (`off`/`0` disables).

## `requireHookToken` is enforce-when-set (evicted from the core 2026-09-13)

An **empty** token opens the machine endpoints. So "the API answered" is not evidence a credential
was checked — confirm `CLAWGATE_HOOK_TOKEN` is actually set in the deployment before reading a 200
as proof of anything.

## Per-host kubeconfig paths (evicted from the core 2026-09-13)

workbench `.250` → `~/workspace/homelab-talos/workbench-kubeconfig`;
laptop `.155` → `~/workspace/homelab-infra/workbench-kubeconfig`.
The other is **absent** on each host, which is what makes `ls`-then-pick the right move rather than
a hardcoded path. Telling the hosts apart: `troubleshooting.md`.
