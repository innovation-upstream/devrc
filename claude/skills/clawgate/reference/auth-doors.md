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
live pod (0.8.32): `GET /tasks/1` **on the router base** → **`303` → `/login?next=%2Ftasks%2F1`**,
and `/tasks` likewise; `/login` answers 200, so `CLAWGATE_UI_PASSWORD` is set and the gate is real.
Re-measure before quoting either way — this flipped once and the deployment env is what decides it.

⚠ **That probe no longer reproduces: `/tasks/1` on the ROUTER base is a 404 today** (2026-09-29)
because the tasks UI is muster's. The URL is deliberately not spelled out above any more — a
literal router-base task URL is exactly what `scripts/tests/test_clawgate_skill_task_base.py`
refuses, and a reader copying it would get the 404 rather than the 303 the sentence promises. The
conclusion — the human tier IS gated — still holds on **`http://192.168.50.250:30306/tasks/1`**,
where the refusal is a `401` + JSON rather than a redirect. Re-run the probe on the MUSTER base.

🔴 **SINCE THE muster SPLIT THERE IS A THIRD AXIS: WHICH SERVER.** The two doors are still the
trap, but `/api/tasks*` and `/tasks*` moved to **muster** (`http://192.168.50.250:30306`,
`CLAWGATE_TASK_API_URL`) while approvals stayed on the router (`:30302`). **A credential question
and a base-URL question produce the same-looking failure**, so settle the base FIRST: on the wrong
one you get a text/plain `404 page not found`, never a 401. `SKILL.md` → "TWO SERVICES".

**The two doors take DIFFERENT credentials, and this is the trap:**

| door | service | wrapper | credential | measured |
|---|---|---|---|---|
| `/api/*` (machine) | muster for `/api/tasks*`, router for `/api/send` etc. | `requireHookToken` | `Authorization: Bearer $CLAWGATE_HOOK_TOKEN` | `:30306/api/tasks?limit=1` → **200** (2026-09-29); `:30302/api/tasks` → **404** |
| `/tasks/{id}`, `/tasks`, the tasks UI | **muster `:30306`** | `requireSession` | a **session cookie** | **`401` + JSON** on muster 2026-09-29 (clawgate answered `303 → /login` pre-split) — the hook token buys NOTHING here |

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
⚠ On **muster** (`:30306`) since the split; the `server.go:699` cite is clawgate's pre-split tree.

🔴 **`DELETE /api/tasks/{id}` deletes a task AND tears down its live dispatched agent pod**
(`dismissTask`; **no in-progress guard, deliberately**). ⚠ This said *"unauthenticated on the LAN"*;
it is **`requireHookToken`** (`server.go:699`), not `requireSession` — so it is wrong in the
OPPOSITE direction from the retraction above, and the correction does not make it safer: **every
agent and hook on this box already holds that token** (`~/.claude/clawgate.env`), so it is one curl
from anything that can read the file.

## `POST /agents` — corrected too
⚠ On **muster** (`:30306/agents`) since the split — `:30302/agents` is a 404, which is neither the
401 nor the 200 this section contrasts.

**`POST /agents` is FORM-ENCODED, not JSON** (hence no `clawgatectl` verb). ⚠ This said *"behind
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

## Per-host kubeconfig paths (evicted from the core 2026-09-13)

workbench `.250` → `~/workspace/homelab-talos/workbench-kubeconfig`;
laptop `.155` → `~/workspace/homelab-infra/workbench-kubeconfig`.
The other is **absent** on each host, which is what makes `ls`-then-pick the right move rather than
a hardcoded path. Telling the hosts apart: `troubleshooting.md`.
