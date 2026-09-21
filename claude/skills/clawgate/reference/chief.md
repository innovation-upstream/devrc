# chief — the agent-identified terminal door

**Driving `clawgatectl chief` from Zach's own Claude Code, and knowing what it can and
cannot do before you try.**

🔴 **STATE, RE-MEASURED 2026-09-20 ON THE LIVE POD (clawgate 0.8.47): THE DOOR IS NOW
CONFIGURED.** The 0.8.38 reading below said DISABLED and was stale within three days —
which is the point of the re-measure instruction, not an exception to it.

```
terminal write surface (AGENT-IDENTIFIED door): CONFIGURED — CLAWGATE_CHIEF_TOKEN is
set and differs from the other secrets. ⚠ NOT YET KNOWN TO BE USABLE: the door refuses
at request time in FOUR further ways …
```

⚠ **CONFIGURED is not USABLE, and the banner says so itself.** It means the secret is
set and distinct; the resolve-to-an-agent-row check happens at the FIRST WRITE. Three of
the four remaining refusals log a line prefixed `chief:` on that first write — grep the
pod log for the PREFIX, not for any one message.

⚠ **There is still no agent named `chief`.** Measured 2026-09-20, `CLAWGATE_CHIEF_TOKEN`
resolves to **`zesty-stoat`** (confirmed by comparing SHA-256 digests of the server
secret and each `devpod-*/devpod-secrets` HOOKS_TOKEN — never by printing them).
Arming remains an operator act, because it hands a chat box `tmux send-keys` on both
machines. ⚠ Re-measure before asserting any of this: `clawgatectl health` for the
version, and the pod's boot banner for the door.

## What chief IS

A kubeclaw agent pod — the same provisioning path as any dispatched agent — whose own
per-agent hooks token the clawgate server has been configured to recognise at one
extra door. There is no chief-specific image and no chief-specific chart.

🔴 **"NO RESERVED NAME IN THE CODE" IS NO LONGER TRUE, AND ONLY HALF OF THE OLD
INVARIANT SURVIVED — 0.8.47.** This section used to say, flatly, *"which agent is chief
is decided by one secret on the server, so re-pointing it at a different agent is a
secret edit, not a re-provision."* Split it in two:

| what | decided by | still a secret edit? |
|---|---|---|
| **who may WRITE** through the door | `CLAWGATE_CHIEF_TOKEN` → `requireChiefToken` → `Agents.GetByHooksToken` | ✅ yes, unchanged |
| **which INSTRUCTIONS the agent boots with** | the reserved name `chief` (`agents.ChiefName`), at PROVISION time | ❌ **no — it needs a re-provision** |

`instructionsFor`/`skillsFor` branch on the agent's NAME, because `buildHelmValues` seeds
the workspace long before any server-side secret is readable from that package. So an
agent that is chief BY SECRET but not NAMED `chief` boots with the ordinary **worker**
prompt.

🔴 **That was the live state, and it is what made chief unusable.** Measured 2026-09-20:
chief was `zesty-stoat`, so `/data/workspace/AGENTS.md` was `clawgateTaskSkill` — a
task-execution manual opening *"clawgate assigned you a task"*. Asked **"Recap the
fleet"**, chief ran `agent_get_task` (×3) → `no task assigned to you` →
`agent_list_pull_requests` → `agent has no repo` → told the operator it had nothing to
do and asked to be assigned work. It was not malfunctioning; it was executing the only
instructions it had. Fixed in 0.8.47 by a third branch returning `chiefFleetSkill`.

🔴 **The fix does NOT reach a running agent.** The `base64 -d > /data/workspace/AGENTS.md`
init command is baked into the deployment spec when the agent is provisioned, so a
restart re-runs the OLD one. **Re-provision the agent under the name `chief`, then
re-point `CLAWGATE_CHIEF_TOKEN` at its hooks token.**

## The three verbs, and the credential each one needs

They are not one tier. Presenting the wrong value is a 401 at whichever step does not
match, and the two directions use disjoint credentials by design.

| verb | route | credential | who runs it |
|---|---|---|---|
| `chief ask --text …` | `POST /operator/agents/{id}/message` | **`CLAWGATE_OPERATOR_TOKEN`** — the reserved *operator* agent's own hooks token — **plus** `CLAWGATE_HOOK_TOKEN` for the `GET /api/agents` name→id resolve it does first | the OPERATOR (you), talking TO chief |
| `chief write --host --pane --text` | `POST /api/term/chief/send-keys` | **`CLAWGATE_CHIEF_TOKEN`** | CHIEF, inside its own pod |
| `chief launch --host --cwd [--text]` | `POST /api/term/chief/new-session` | **`CLAWGATE_CHIEF_TOKEN`** | CHIEF, inside its own pod |

🔴 **`chief ask` needs TWO credentials and it is not a bug.** `GET /api/agents` is
behind `requireHookToken` (compares against the server's shared secret);
`POST /operator/agents/{id}/message` is behind `requireOperatorToken` (resolves the
bearer to the *operator* agent ROW). One value cannot satisfy both. Read the operator
token out of its Secret:

```bash
kubectl --kubeconfig $KC -n devpod-operator get secret devpod-secrets \
  -o jsonpath='{.data.HOOKS_TOKEN}' | base64 -d
```

🔴 **`CLAWGATE_CHIEF_TOKEN` is not a free-form secret you can invent.**
`requireChiefToken` resolves it through `Agents.GetByHooksToken`, so it MUST BE some
agent row's hooks token. Inside chief's own pod that value and `CLAWGATE_HOOK_TOKEN`
are legitimately the SAME string, and nothing objects to that — an earlier client-side
sameness guard refused exactly the only configuration that works, and was removed.

## 🔴 A write BLOCKS on a human, for up to 5 minutes

`chief write` / `chief launch` raise an approval card on the operator's queue and wait.
Nothing is queued for a host until the tap. So:

- `--timeout` defaults to **6m** on those two verbs, not the root command's 30s, and an
  explicit value below 6m is **refused before anything is sent**.
- 🔴 **YOUR HARNESS TIMEOUT IS THE HARDER HALF.** A Claude Code / opencode Bash call
  typically dies at ~120s. Killing the process mid-wait cancels the request, the server
  refuses the write, **and the card is cleared under the operator's finger**. Run these
  two verbs outside a per-command timeout, or do not run them.
- Reads are NOT gated. Only the two write verbs wait.

Exit codes that mean different things: **2** no credential *or* the operator said NO ·
**3** the server rejected the credential · **9** the server refuses to serve the door at
all (unarmed / resolves to no agent / resolves to the reserved `operator`) · **10** THIS
client stopped waiting and the card was destroyed · **6** nothing reached the server.

## 🔴 What chief CANNOT do — measured, and pinned by a test

🔴 **THIS SECTION WAS WRONG IN FOUR OF SIX ROWS UNTIL 2026-09-20, AND ITS HEADING
OVERSTATED THE CASE.** It said criterion 4's five capabilities were all unreachable
bar the pane write — *"The other four are behind `requireHookToken`"*. **Task 607
moved four routes onto `requireHookOrAgentToken` and this table was never updated.**
Re-measured below with chief's own agent-row token, using the shared hook token as a
positive control on the same routes.

🔴 **THE LEDGER IS ASYMMETRIC BY METHOD — do not read a path, read a METHOD.** Chief
may RAISE and RESOLVE an attention entry but may not LIST the queue: `POST
/api/attention` is `requireHookOrAgentToken` while `GET /api/attention` was
deliberately left on `requireHookToken` (reading the operator's queue was not moved).
A 401 on the GET says NOTHING about the POSTs, and a table keyed on
`/api/attention` alone cannot express that.

| capability | route | gate | from inside chief's pod |
|---|---|---|---|
| send a message to a pane | `POST /api/term/chief/send-keys` | `requireChiefToken` | ✅ **when armed** |
| read the bound task | `GET /agent/task` | `requireAgentToken` | ✅ always (answers "no task assigned" — expected) |
| list tmux windows | `GET /api/tmux/snapshot` | `requireHookOrAgentToken` | ✅ **200** — was ❌ 401 here |
| read a transcript | `GET /api/transcripts/{id}` | `requireHookOrAgentToken` | ✅ **404 on a bogus id = auth PASSED** — was ❌ 401 here |
| raise an attention entry | `POST /api/attention` | `requireHookOrAgentToken` | ✅ — was ❌ 401 here |
| resolve an attention entry | `POST /api/attention/{id}/resolve` | `requireHookOrAgentToken` | ✅ — was ❌ 401 here |
| **read** the attention queue | `GET /api/attention` | `requireHookToken` | ❌ **401** — not moved, on purpose |
| the task board | `GET /api/tasks` | `requireHookToken` | ❌ 401 |
| manage a layout | `POST /api/layout/views` | `requireHookToken` | ❌ 401 |

🔴 **"CHIEF CANNOT ENUMERATE A PANE" WAS FALSE — MEASURED 2026-09-20.** Probed with
chief's own agent-row token against the live pod, with the shared hook token as a
positive control on the same three routes:

| route | chief token | hook token (control) |
|---|---|---|
| `/api/tmux/snapshot` | **200** | 200 |
| `/api/attention` | 401 | 200 |
| `/api/tasks` | 401 | 200 |

That 200 is not empty: **199 KB, 83 window records across BOTH hosts**, carrying
`label`, `status`, `busy`, `waiting_probable`, `unsent_prompt`, `task`, `repo`, `path`,
`age_secs` and `pane_preview`. So chief **can** enumerate panes and holds everything a
fleet recap needs. 0.8.47's `chiefFleetSkill` tells it so; before that it had no way to
know the route existed. The remaining 401s are still a privilege decision, not an
oversight to route around.

🔴 **RETRACTION — "the two-way test did not fire" was FALSE, and the pin is healthy.**
An earlier draft of this section said `chief_capability_reach_test.go` had silently
stopped firing and left it "NOT yet diagnosed". Diagnosed 2026-09-20, and the opposite
is true. The ledger row reads `route: "GET /api/tmux/snapshot", wantReachable: true`
with `why: "requireHookOrAgentToken since task 607"`, and the file's own comment records
the firing: *"Before task 607: moving `GET /api/tmux/snapshot` from requireHookToken to
requireAgentToken redded `list tmux windows` … Task 607 then made that arm real, and it
fired: all four routes moved, all four rows had to be edited."*
`TestTheChiefCapabilityReachLedger` and `TestTheChiefReachLedgerCanSeeBothVerdicts` both
run and PASS. **The pin did its job; THIS DOCUMENT was the thing that went stale**, for
four rows, for however long task 607 has been in.

⚠ **The reading that produced the false claim was a `-run` filter matching nothing.**
`go test -run TestChiefCapabilityReach` prints `ok … [no tests to run]` and exits 0 —
indistinguishable from a pass — because the real names begin `TestTheChief…`. Count the
`--- PASS` lines, never the `ok`. Same trap the deploy runbook names for the pin guard.

⚠ So the table above IS machine-checked, and the honest caveat is narrower than the one
it replaces: the test pins clawgate's ROUTE TIERS, not this markdown. Nothing reds when
this file drifts from it. Re-probe before quoting a row.

⚠ **You, the operator's Claude Code, are not in a pod** — you hold the shared hook
token in `~/.claude/clawgate.env`, so `tmux windows`, `transcript`, `attention` and the
`layout` verbs all work for YOU. The table above is about chief. Do not read your own
success as evidence about the agent.

## Arming it — an OPERATOR act, four steps

Nothing below is an agent's to run. It ends in `tmux send-keys` on both machines.

1. **Create the agent** (needs a browser session — `POST /agents` is `requireSession`,
   and there is still no machine route): the clawgate UI's Agents tab → dispatch an
   agent named `chief`. Standing default model is **deepseek**
   (`CLAWGATE_AGENT_MODEL`) unless Zach says otherwise. ⚠ The name `chief` is
   convention only; the server does not reserve it.
2. **Read that agent's own hooks token** — this exact value becomes the chief token:
   ```bash
   kubectl --kubeconfig $KC -n devpod-chief get secret devpod-secrets \
     -o jsonpath='{.data.HOOKS_TOKEN}' | base64 -d
   ```
3. **Put it in the clawgate Secret and reference it from the deployment.** The env
   block goes in `clusters/workbench/apps/clawgate/deployment.yaml` beside
   `CLAWGATE_TERMINAL_TOKEN`; the value goes in the `clawgate-secrets` Secret under
   the same key. Committing to `trunk` is what deploys the manifest.
4. **Confirm the banner flipped** — merging is not evidence, and neither is the pod
   restarting:
   ```bash
   kubectl --kubeconfig $KC -n clawgate logs deploy/clawgate | grep 'AGENT-IDENTIFIED'
   # want: ... (AGENT-IDENTIFIED door): ENABLED
   ```

🔴 **The server refuses FIVE configurations, all as a 503 that says which:** unset ·
shorter than 32 chars · equal to `CLAWGATE_HOOK_TOKEN` · equal to
`CLAWGATE_TERMINAL_TOKEN` · resolving to the reserved `operator` agent. The last one
means **there is no armable configuration until a non-operator agent exists** — that
is the intended outcome, not a bug to work around.

## What gets recorded, and where to read it

Every write through this door stores `actor` = the NAME of the agent whose credential
was presented, resolved by the server from the token and **never** from the request
body (`termwrite.Write.Actor`, migration 0035). That is the one field that makes a
chief write and a host script's write different rows after the fact.

- `clawgatectl term ls` reads the ledger (needs `CLAWGATE_TERMINAL_TOKEN`).
- ⚠ Rows written before 0035 read `pre-0035-unattributed` — a sentinel, not a guess.
- ⚠ `tier` does **not** identify the caller: two doors store `token`. Read `actor`.

## Pointers

- `auth-doors.md` — which door takes which credential, measured both ways.
- `agent-dispatch.md` — provisioning a kubeclaw agent, and a silent non-start.
- `cross-session-reach.md` — 🔴 `term send` RUNS what you type.
- Task **521** on the board carries the full delta, the red-at-base matrix and the
  arming handover; task **522** is the chief slide-out on the tmux page.
