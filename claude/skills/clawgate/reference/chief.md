# chief — the agent-identified terminal door

**Driving `clawgatectl chief` from Zach's own Claude Code, and knowing what it can and
cannot do before you try.**

🔴 **STATE, MEASURED 2026-09-17 ON THE LIVE POD (clawgate 0.8.38): THE DOOR IS OFF.**

```
terminal write surface (AGENT-IDENTIFIED door): DISABLED (fail-closed) —
CLAWGATE_CHIEF_TOKEN is not set. Agent-attributed writes answer 503 until it is
armed; the other tiers are unaffected.
```

No `chief` row exists in `clawgatectl agent ls`, and no chief token exists in any
`clawgate` namespace Secret. So **every `chief write` / `chief launch` answers 503
today, whoever runs it** — that is the designed unarmed state, not a fault. Arming it
is an operator act (below), because it hands a chat box `tmux send-keys` on both
machines. ⚠ Re-measure before asserting any of this: `clawgatectl health` for the
version, and the pod's boot banner for the door.

## What chief IS

A kubeclaw agent pod — the same provisioning path as any dispatched agent — whose own
per-agent hooks token the clawgate server has been configured to recognise at one
extra door. There is no chief-specific image, no chief-specific chart, and no
reserved name in the code: **which agent is chief is decided by one secret on the
server**, so re-pointing it at a different agent is a secret edit, not a re-provision.

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

Criterion 4 of task 521 asks chief to list tmux windows, read a transcript,
raise/resolve an attention entry, manage a layout, and write to a pane. **Only the
last one is reachable on a credential a pod holds.** The other four are behind
`requireHookToken`, which compares against the server's SHARED secret — and what a pod
carries under the NAME `CLAWGATE_HOOK_TOKEN` is its OWN row token, a different value
from a different source. The name collision is why this was believed to work.

| capability | route | gate | from inside chief's pod |
|---|---|---|---|
| send a message to a pane | `POST /api/term/chief/send-keys` | `requireChiefToken` | ✅ **when armed** |
| read the bound task | `GET /agent/task` | `requireAgentToken` | ✅ always |
| list tmux windows | `GET /api/tmux/snapshot` | `requireHookToken` | ❌ 401 |
| read a transcript | `GET /api/transcripts/{id}` | `requireHookToken` | ❌ 401 |
| raise / resolve attention | `POST /api/attention[/{id}/resolve]` | `requireHookToken` | ❌ 401 |
| manage a layout | `POST /api/layout/views` | `requireHookToken` | ❌ 401 |

So **chief can be TOLD a pane id and write there; it cannot enumerate one.** That is
not an oversight to route around — closing it is a privilege decision (re-tier those
routes, or hand chief the shared hook secret, which is 33 routes and a tier that fails
OPEN when unconfigured). The table is machine-checked by
`<homelab-infra>/containers/clawgate/cmd/clawgatectl/chief_capability_reach_test.go`, which reds in **both** directions: a
capability becoming reachable is reviewed as the privilege grant it is, and one
becoming unreachable means a chief that worked has silently stopped.

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
