# Scope — a model selector on clawgate's chief panel (any OpenRouter model)

**Status:** SCOPE ONLY. No production code, no PR, no clawgate task, no deploy, no
privilege change was made producing this document. Everything below is read-only
measurement plus design.

**Date:** 2026-09-19 · **Base:** `homelab-infra` (`/home/zach/workspace/homelab-talos`)
at `origin/trunk` = `15744e013` (base clone verified level with origin at time of writing)

**Repos in scope:** `ZacxDev/homelab-infra` — **every work item**. 🔴 **`ZacxDev/openclaw-image`
is NOT involved:** it builds a different image from the one clawgate provisions (§2.2). No
agent-image change is required at all, because the capability this feature needs is already
present in the version running today.

---

## 0. The one-paragraph answer

The picker itself is nearly free: the chief panel already renders the *same*
`ui.AgentDetailView` the agent-detail page renders, that view **already carries
`Model: a.Model`**, and `agentModelControl(v)` — the searchable OpenRouter combobox, its
route, its store method and its OpenRouter proxy — already exists and is mounted in
`agentDetailHeader`. What is new is (a) **mounting** it on the panel, (b) a **thread-scoped**
effective model distinct from the agent-level preference, (c) making each gateway request
carry the chosen model, (d) **validating** the slug, (e) **stamping** the model onto
assistant rows, and (f) telling the operator when a switch is live.

🔴 **Item (c) is the one the brief got wrong, and §2 answers it with a live measurement.**
Per-request model selection **works today, with no pod roll and no image bump** — but **not**
through the body `model` field. That field is an *agent target*; an `openrouter/<slug>` there
is a hard **400 before any turn runs**, so the hardcoded `"openclaw"` literal must **stay**.
The real door is the **`x-openclaw-model` header**. One unmeasured link remains (§2.4) and it
costs one real agent turn to close.

---

## 1. Facts established, with evidence

All paths relative to `containers/clawgate/` inside `homelab-infra` unless stated.

### 1.1 The gateway request hardcodes the model — in TWO places, not one

| # | file:line | literal |
|---|---|---|
| 1 | `internal/agents/gateway.go:58` | `"model": "openclaw"` in the `/v1/chat/completions` body |
| 2 | `internal/agents/responses.go:402` | `Model: "openclaw"` in `responsesRequest` inside `runToolLoop` |

`internal/agents/responses.go:92` declares `Model string \`json:"model"\`` on
`responsesRequest`, so the field exists on the tool-loop path — it is simply always
filled with the same literal. **The brief named #1 and the struct field; #2 is the
one that matters more**, because `chatTurn` reaches `ChatWithTools` → `runToolLoop`
first and only falls back to `Chat` → `chatStream` on `ErrResponsesUnsupported`
(`internal/api/agents.go:1481-1492`). A change that only fixed `gateway.go` would be
**dead on the live path** and would look correct in review.

### 1.2 Nothing passes `a.Model` to the gateway; it is a provision-time value only

- `Provisioner.Chat(ctx, a Agent, sessionKey, message, emit)` —
  `internal/agents/provision.go:1118` — receives the whole `Agent` and calls
  `chatStream(ctx, p.http, a.Name, a.HooksToken, sessionKey, …)`. `a.Model` is not read.
- `Provisioner.ChatWithTools(...)` — `provision.go:1129` — same, into `runToolLoop`.
- `a.Model`'s only consumer is `buildHelmValues`:
  `internal/agents/values.go:343` →
  `"model": map[string]any{"primary": orDefault(a.Model, orDefault(cfg.Model, defaultModel))}`.
- `defaultModel = "openrouter/xiaomi/mimo-v2.5"` — `internal/agents/values.go:15`
  (the brief said `values.go:39-52` for this; the *datalist* is `ModelOptions` at
  ~`values.go:39-54`, the default **constant** is line 15. Both facts hold, the line
  numbers differ.)
- `defaultModelFallbacks = []string{"openrouter/deepseek/deepseek-v4-flash-0731"}` —
  `values.go:25` — delivered via `configOverlay`, not `agent.model` (see the 🔴 block at
  `values.go:361-378`: the chart renders exactly one `model` key, so `agent.model.fallbacks`
  is never read and `agent.defaults.model` would emit a *second* `"model"` key).

**Consequence:** today "the agent's model" is a Helm value. Changing it means
`helm upgrade`, which is what `ReapplyProfiles` does (`provision.go`, the whole
function is a `store.Get` → `buildHelmValues` → `p.helm.Upgrade`), and the chart rolls
the pod because `checksum/config` hashes `configmap.yaml`.

### 1.3 The pod is already credentialed for *any* OpenRouter slug

The vendored chart (`internal/agents/chart/kubeclaw/`) writes
`agents.defaults.model.primary` as a bare scalar
(`templates/configmap.yaml:31-33`), and the pod's auth profile is an **OpenRouter
api_key profile** (`values.yaml:74-94`: `agent.auth.provider: "openrouter"`, key from
`OPENROUTER_API_KEY` in the existing secret). clawgate injects that key cluster-wide
(`agents.Config.OpenRouterAPIKey`, `values.go` "shared OpenRouter credentials").

**This is the enabler for the whole design:** no credential, secret or config change is
needed to *resolve* a different `openrouter/<slug>` inside an already-running pod. The only
question was whether the gateway will honour a per-request model — §2 answers it: yes, via
the `x-openclaw-model` header, not the body field.

### 1.4 The chief panel has no model control, and that is the whole gap

`internal/ui/chief_panel.go` contains **zero** occurrences of `model` (case-insensitive
grep over the file: 0). Confirmed independently of the brief.

But the data is already there:
- `internal/api/chief_panel_ui.go:120-137` builds `ui.ChiefPanelView{Chat: ui.AgentDetailView{… Model: a.Model …}}`.
- `ui.AgentDetailView.Model` is declared at `internal/ui/agents_detail.go:34`.
- `agentModelControl(v AgentDetailView)` — `internal/ui/agents_detail.go:454-490` —
  renders the combobox, `hx-post`s `/agents/{id}/model`, autosaves on
  `change from:[data-combobox-hidden]`, shows a `↳ restarting…` hint, and carries the
  dropdown warning `"Selecting a model restarts the agent"`.
- Its **only** caller is `agentDetailHeader` at `internal/ui/agents_detail.go:214`.
  `agentChatPane` — the part the chief panel re-mounts — does not call it.

So the control was written once, mounted once, and the panel re-mounts the *pane*, not
the *header*. That is why the panel has no picker.

The panel does have a first-class place to put one: `chiefPanelActions`
(`chief_panel.go:410-427`) is an **out-of-band title-bar slot** (`hx-swap-oob`) already
holding `chiefNewThreadButton` + `chatHistoryButton`, cleared deliberately when
unconfigured.

### 1.5 "Threads" are `chat_sessions` rows — there is no thread table

- `chat_sessions` — migration `0009_chat_sessions.sql`: `id, agent_id, session_key
  UNIQUE, title, created_at, updated_at` (+ `read_at` from a later migration).
- `chat_messages` — `0001_init.sql:84-92` (`agent_id, role, content, created_at`),
  `+ session_id` (`0009`), `+ kind/tool_id/tool_name/tool_ok` (`0015`).
- `agents.ChatMessage` — `internal/agents/store.go:69-85`. `agents.ChatSession` — `:90-100`.
- `ui.ChatLine` — `internal/ui/agents_detail.go:20-27` — `Role/Content/Kind/ToolID/ToolName/ToolOK`.
  **No model field on any of the three.**
- The panel's per-thread noun is deliberate: `SessionView.Title` empty renders
  `"New thread"` in `chiefThreadRows` and `"New chat"` in `sessionDrawer`, pinned by
  `ui.TestAnUntitledSessionsPlaceholderIsPerSurfaceAndLedgered`.
- A thread's gateway context is the `session_key` string alone — `Provisioner.Chat`
  hands it straight to `chatStream` and consults no row (`chief_agent.go:144-148`).

### 1.6 Migration high-water mark

`internal/db/migrations/` tops out at **`0038_attention_raised_by.sql`**. **The next
number is `0039`** — the brief's figure, re-derived today and still correct.
`internal/db/migrate_test.go::TestMigrationVersionsAreUniqueAndContiguous` parses the
same `NNNN_` prefixes `Migrate` parses and asserts **uniqueness and contiguity from 1**;
its header records the real incident (#174/#175 both claiming `0019`, clawgate failing to
start). **Two PRs in this scope must not both claim 0039** — see §8.

### 1.7 The OpenRouter proxy, as built

`internal/api/openrouter.go` — whole file, 100 lines:
- `openRouterModelsURL = "https://openrouter.ai/api/v1/models"`, **no key** (line 20).
- `openRouterCacheTTL = time.Hour` (line 21) — the TTL the operator wants raised.
- `openRouterMaxResult = 25` (line 22) — the per-query result cap.
- `orCache` is a **package-level** `*modelCache{mu sync.Mutex; ids []string; fetched time.Time}`
  (line 31). Process-local: a clawgate redeploy empties it. The operator has accepted that.
- `models()` serves the **stale** cache on fetch error or empty result (lines 41-44) and
  only advances `fetched` on success — so a failing fetch retries on every call rather
  than backing off. Worth noting; not a change the operator asked for.
- Slugs are returned already prefixed `"openrouter/"+id` (line 76), sorted (line 79).
- `handleOpenRouterModels` filters case-insensitive substring, caps at 25, sets
  `Cache-Control: private, max-age=300` (lines 85-100).
- Route: `mux.HandleFunc("GET /api/openrouter/models", s.requireSession(s.handleOpenRouterModels))`
  — `internal/api/agents.go:98`.

🔴 **The cap and the validation interact and the brief's plan has a hole here.** The
catalogue is capped at **25 results per query**, so "validate the slug against the cached
catalogue" must validate against `orCache.ids` (the full list), **not** against what
`handleOpenRouterModels` returns. Validating against the capped response would reject
valid models purely because they sorted past position 25 for an empty query. §4 W3 states
this explicitly.

### 1.8 The existing apply route, as built

`handleAgentModel` — `internal/api/agents.go:916-961`, route
`POST /agents/{id}/model` wrapped in `requireSession` (`agents.go:103`):
1. parse id, `ParseForm`, `model := strings.TrimSpace(r.FormValue("model"))`;
2. `s.ext.Agents.SetModel(ctx, id, model)` — `internal/agents/pgstore.go:145-148`,
   `UPDATE agents SET model=$2, updated_at=now() … RETURNING`;
3. **if** the agent is `StatusRunning` or `StatusProvisioning`, `safeGo` a
   `ReapplyProfiles` in the background;
4. `HX-Trigger: agents:changed`, `200`, **empty body**.

⚠ Both of the brief's criticisms are confirmed by the code:
- **It answers 200 before the roll completes.** `safeGo` is fire-and-forget; nothing
  waits, nothing reports, and `ReapplyProfiles` only logs. In a chat panel a message sent
  in that window is answered by the old model, with nothing on screen saying so. The
  existing UI's only acknowledgement is a client-side `↳ restarting…` span revealed by
  `hx-on::after-request` (`agents_detail.go:483`) — a *claim about the request*, not
  about the pod.
- **No validation at all.** Any string is accepted and stored. A typo is rendered into
  `agent.model.primary`, the pod rolls, and the new pod cannot start — which presents as
  "chief is broken".

`requireSession` **does** enforce a human session today (`internal/api/auth.go:349-373`:
`BrowserAuthRefusal` → 503/303, `hasValidSession` → next, else `refuseUnauthenticated`).
⚠ **`internal/api/routes_golden_test.go`'s header comment is STALE and says the
opposite** — it describes `requireSession` as *"(auth.go:40) — a PASS-THROUGH NO-OP; the
body is `return next`"*. It was, and `auth.go:330-348` records the change. A session
reading that comment would conclude a new `requireSession` route is unauthenticated.
Worth a one-line fix in whichever PR touches that file; it is not this feature's bug.

### 1.9 Gates a new route/control must satisfy

- **`internal/api/routes_golden_test.go` + `internal/api/testdata/routes.golden`** — a
  checked-in golden set of every registered route. A new route **fails the suite** until
  the golden is regenerated: `UPDATE_ROUTES_GOLDEN=1 go test ./internal/api -run TestRoutesMatchGolden`.
  Known limit, stated in its own header: **it does not see auth wrappers.**
- **`TestEveryBrowserSurfaceRequiresAHumanSession`** (`internal/api`) — a *relationship*
  guard over the route table; the auth tier of a new browser route is checked here, not by
  the golden.
- **Tailwind** — `tailwind.config.js` content globs are `./internal/ui/**/*.go`,
  `./internal/api/login.go`, `./web/**/*.{html,js}`. See §7 for the trap.
- **`internal/ui/css_test_only_classes_test.go`** — `TestTheStylesheetCarriesNoNewTestOnlyClasses`
  plus the helpers `cssScannedSources(t)` (splits **non-test** from **test** sources) and
  `cssTokenOccurs`. This is the existing, first-class mechanism for §7's requirement.
- **e2e** — `e2e/tests/chief-panel.spec.ts`, `chief-threads.spec.ts`,
  `chief-threads-mobile.spec.ts`; axe walks the open panel *and its open thread list* at
  step 12 of `e2e/ux-audit/clawgate-surfaces.audit.ts`. A new interactive control in the
  panel enters an axe-walked surface.
- **`internal/agents/values_model_test.go`** — nine existing tests over model rendering
  and the fallback overlay. The natural home for provision-side model assertions.
- **Fakes** — `internal/api/agent_test.go` holds `fakeAgents` with `SetModel` at
  `agent_test.go:175`. Any `agents` store-interface change (and any `ext.go:216`
  `ChatWithTools` signature change) obliges every fake in `internal/api` and
  `internal/agents`.

### 1.10 Corrections to the brief

Two are material; the rest are line numbers.

🔴 **0. The central premise is wrong in a way that would have broken chief.** The brief
treats the body `model` field as the place a real model slug goes ("does the gateway honour
a real `openrouter/<slug>` in the `model` field"). It does not, in any version, and putting
one there is a **hard 400 before any turn runs** — see §2. The hardcoded `"openclaw"` is not
a placeholder to be replaced; it is the correct value. Per-request model selection is real,
and it lives in the **`x-openclaw-model` header**.

🔴 **0b. Two different agent images were treated as one.** The brief says *"The agent image
is `harbor.homelab.lan/library/openclaw` / the homelab custom `clawdbot` image"* and then
points at `openclaw-image`'s Dockerfile pin. clawgate provisions **`clawdbot`**;
`openclaw-image` builds a **different** image for homelab devpods. The `2026.6.1` pin in
that Dockerfile is not the version chief runs (§2.2) — chief's pod runs **2026.5.7**.

Five smaller refinements:

1. **The hardcoded model is in two places, not one**, and the one the brief named is on
   the *fallback* path (§1.1). This changes the work item, not the design.
2. `internal/ui/agents.go:1089` — the combobox family is real but spans
   `modelField()` (`:999`), `modelFieldFor` (`:1028`), `modelFieldWithDisplay` (`:1007`),
   `modelFieldForDisplay` (`:1036`), `shortModelLabel` (`:963`), `modelName` (`:973`),
   `agentModelLabel` (`:987`), `curatedModels()` (`:928`). The *mounted agent control* is
   `agentModelControl` in **`agents_detail.go:454`**, not `agents.go`.
3. `defaultModel` is `values.go:15`; the datalist `ModelOptions` is `values.go:~39-54`.
4. **`ChiefPanelView.Chat.Model` is already populated** (`chief_panel_ui.go:125`) — the
   panel is not missing the *data*, only the *control*. This makes W1 smaller than the
   brief implies.
5. The catalogue cap interacts with validation (§1.7) — validate against `orCache.ids`,
   never the route's capped response.

---

## 2. THE VERDICT — can the model be chosen per request at the pod's gateway?

### 2.0 The answer, in one box

> 🔴 **YES — per-request model selection works, with NO pod roll, and it already works in
> the version deployed today. But NOT through the `model` field the brief pointed at.**
>
> The body `model` field is an **AGENT TARGET**, not an LLM id. Its accepted grammar is
> `openclaw` | `openclaw/default` | `openclaw/<agentId>` | `openclaw:<agentId>` |
> `agent:<agentId>` — one path segment after the prefix. An `openrouter/<vendor>/<slug>`
> value is **rejected with HTTP 400 before any turn runs**, and cannot even match the regex.
>
> 🔴 **So a naive implementation of the brief's design would take chief from "answers with
> the wrong model" to "answers nothing at all", on every turn.** The hardcoded
> `"model": "openclaw"` literal at `gateway.go:58` and `responses.go:402` **must stay**.
>
> The real per-request door is the **`x-openclaw-model` HTTP header**, documented by
> upstream as the correct place for exactly this, and present in the image running on the
> pods today.

This is the answer to (A)/(B)/(C)/(D) from every angle asked: **(C) reject + (D) a fixed
alias grammar** for the body field; **per-request override via a header** as the supported
mechanism. Not (A) — the field is read and validated, never ignored. Explicitly not (B).

### 2.1 How it was established

Two independent lines of evidence, deliberately not sharing a step:

**(a) A live, read-only probe of a real running agent pod.** Against `devpod-zesty-stoat`
(a non-chief agent), over `127.0.0.1:18789` from inside the pod, token derived in-pod:

| probe | result |
|---|---|
| `GET /v1/models` (authed) | `200` → ids `openclaw`, `openclaw/default`, `openclaw/zesty-stoat` |
| `GET /v1/models` (no auth) | `401 {"error":{"message":"Unauthorized","type":"unauthorized"}}` |
| `GET /health` | `200 {"ok":true,"status":"live"}` |
| `GET /v1/models/openclaw%2Fzesty-stoat` | `200` |
| `GET /v1/models/openrouter%2Fxiaomi%2Fmimo-v2.5` | **`400 {"message":"Invalid model id."}`** |
| `POST /v1/chat/completions` with `"model":"openrouter/definitely/not-a-real-model-xyz"` | **`400 {"error":{"message":"Invalid \`model\`. Use \`openclaw\` or \`openclaw/<agentId>\`.","type":"invalid_request_error"}}`** — **no turn ran** |

🔴 **The mutating probe was only run after the ordering was line-verified in the running
image's own bundle**, which is what made it provably non-mutating: the 400 is emitted at
the validation gate, *before* `buildAgentPrompt`. `GET /v1/models` was run first precisely
because it is non-mutating and answers most of the question on its own — the advertised
model ids are **agent names**, which is the whole verdict in one response.

**(b) The published package source, for two versions, read directly.** Both tarballs are
bundled with hashed chunk names but **not minified**, so identifiers and strings are intact.

`openclaw@2026.6.1`, `dist/http-utils-CpQkTpSb.js`:

```js
const OPENCLAW_MODEL_ID = "openclaw";
const OPENCLAW_DEFAULT_MODEL_ID = "openclaw/default";
function resolveAgentIdFromModel(model, cfg = getRuntimeConfig()) {
  const raw = model?.trim(); if (!raw) return;
  const lowered = normalizeLowercaseStringOrEmpty(raw);
  if (lowered === "openclaw" || lowered === "openclaw/default") return resolveDefaultAgentId(cfg);
  const agentId = (raw.match(/^openclaw[:/](?<agentId>[a-z0-9][a-z0-9_-]{0,63})$/i)
    ?? raw.match(/^agent:(?<agentId>[a-z0-9][a-z0-9_-]{0,63})$/i))?.groups?.agentId;
  if (!agentId) return; return normalizeAgentId(agentId);
}
async function resolveOpenAiCompatModelOverride(params) {
  const requestModel = params.model?.trim();
  if (requestModel && !resolveAgentIdFromModel(requestModel))
    return { errorMessage: "Invalid `model`. Use `openclaw` or `openclaw/<agentId>`." };
  const raw = getHeader(params.req, "x-openclaw-model")?.trim();
  ...
  if (!policy.allowsKey(normalized)) return { errorMessage: `Model '${normalized}' is not allowed for agent '${params.agentId}'.` };
  return { modelOverride: raw };
}
```

⚠ **The regex allows ONE path segment after `openclaw/`.** `openrouter/anthropic/claude-sonnet-4`
is not merely rejected — it is **not syntactically expressible** in that field. There is no
future config value that changes this: the whole
`gateway.http.endpoints.chatCompletions.*` schema is enumerated at
`2026.6.1 dist/runtime-schema-CoGt090u.js:376-386` and
`2026.9.5 dist/schema-CwAIqZVE.mjs:870-877` — `enabled`, `maxBodyBytes`, `maxImageParts`,
`maxTotalImageBytes`, `images.*`. **No body-`model` pass-through or allowlist knob exists
in either version.**

Both endpoints enforce it:
- `/v1/chat/completions` — `2026.6.1 dist/openai-http-bSSJac6P.js:498` defaults the field to
  `"openclaw"`, `:558` calls the resolver, `:563-568` `sendJson(res, 400, {error:{…,type:"invalid_request_error"}})`.
- `/v1/responses` — `dist/openresponses-http-D3C1z4N7.js:604-620`, same shape (the field is
  *optional* there; absent ⇒ default agent).
- The accepted header value is plumbed into the run: `openai-http…:57` /
  `openresponses…:567` → `model: params.modelOverride` inside `buildAgentCommandInput`.
- `/v1/models` is built from agent ids only: `dist/models-http-CT61NANT.js:31-33`.

**And upstream documents it as a contract, in the same tarball**
(`docs/gateway/openai-http-api.md:89-107`): *"Agent-first model contract … OpenClaw treats
the OpenAI `model` field as an **agent target**, not a raw provider model id"*, with
`x-openclaw-model: <provider/model-or-bare-id>` named as the backend-model override; and
`:373`: *"Backend provider/model overrides belong in `x-openclaw-model`, not the OpenAI
`model` field."*

**(c) The two lines agree, on different artifacts.** The live pod runs **2026.5.7**, whose
bundle has different chunk hashes (`dist/http-utils-3xCgv22F.js:19-30`,
`dist/openai-http-CtQN39Ne.js:302-313`) and the same functions with the same grammar and
the same pre-turn 400. So the source reading is not a claim about a version nobody runs.

### 2.2 The pinned / deployed version — and it is NOT what the brief assumed

🔴 **Three separate "pinned" versions exist and the brief conflated two of them.**

| what | value | evidence |
|---|---|---|
| **The clawgate agent image** | `harbor.homelab.lan/library/**clawdbot**:2026.5.7` | `clusters/workbench/apps/clawgate/deployment.yaml:320-328` → `CLAWGATE_AGENT_IMAGE_REPO` / `CLAWGATE_AGENT_IMAGE_TAG`; wired at `containers/clawgate/main.go:311-312` → `Config.ImageRepo`/`ImageTag`; consumed by `values.go:311-314` via `imageTagFor()` (`values.go:591`, defaulting to `latest` when empty) |
| **OpenClaw inside that image** | **`2026.5.7`** | `kubectl exec` → `cat /usr/local/lib/node_modules/openclaw/package.json` → `"version": "2026.5.7"` on `devpod-zesty-stoat` **and** `devpod-witty-heron` (two points, same answer) |
| **`ZacxDev/openclaw-image`'s Dockerfile** | `ARG OPENCLAW_VERSION=2026.6.1` | `/home/zach/workspace/openclaw-image/Dockerfile:15`, Renovate-managed (`# renovate: datasource=npm depName=openclaw`) |

🔴 **`openclaw-image` is NOT the image clawgate provisions.** It builds
`ghcr.io/zacxdev/openclaw-image` / `harbor.homelab.lan/library/openclaw-image` (running on
*homelab* devpods at `2026.6.1` and `2026.6.11-py-cg0.8.31`). clawgate's agents come from
the separate `clawdbot` repo. **The brief's "The agent image is
`harbor.homelab.lan/library/openclaw` / the homelab custom `clawdbot` image" reads as one
thing with two names; they are two images with two version trains**, and the 2026.6.1 pin
is on the one clawgate does *not* use.

⚠ **Two image generations are in service, and the older one is far older than the tag
suggests.** Measured across every running `devpod-*` namespace on the workbench:

| tag | OpenClaw | pods | ages |
|---|---|---|---|
| `clawdbot:2026.5.7` (imageID `sha256:37cebd23…`) | **2026.5.7** (measured in 2 pods) | `zesty-stoat`, `witty-heron`, `nimble-shrew`, `operator` | 43h – 27d |
| `clawdbot:latest` (imageID `sha256:76e5dc60…`) | **2026.3.13** (measured in `jarvis`; the other four share the identical imageID, so byte-identical) | `jarvis`, `orchestrator`, `homelab-infra`, `pitch-counter`, `kubeclaw-demo` | 7d12h – 231d |

`2026.3.13` is the version `deployment.yaml`'s own comment warns about (*"plain latest is
still 2026.3.13 which 404s /v1/responses"*) — so those five pods take the
`ErrResponsesUnsupported` fallback path in `chatTurn` today. **Whether `x-openclaw-model`
exists in 2026.3.13 was NOT measured.** It does not matter for chief (see §2.6), but it
matters for any claim that the header works "on the fleet".

**Verdict for the deployed version (2026.5.7): body field = agent target, rejected with
400 for an LLM slug (live-measured); `x-openclaw-model` header present in the running
bundle (source-measured in the running image, `dist/model-selection-shared-DOxyWoaQ.js:476-480`).**

### 2.3 Current upstream

**`openclaw@2026.9.5`, published `2026-09-19T01:14:57Z` — today.** `npm dist-tags`:
`latest=2026.9.5`, `beta=2026.9.5`, `extended-stable=2026.6.35`. 257 versions total.

**The operator's expectation was right: ours is out of date — by four months of releases.
But the capability this feature needs did NOT arrive in that gap; it is already in 2026.5.7.**
Nothing needs upgrading to build this.

The grammar is byte-identical in 2026.9.5 (`dist/http-utils-C75jQxJM.mjs`), with the
rejection promoted to a typed error:

```js
function isOpenClawAgentModelId(model) { /^openclaw[:/][a-z0-9][a-z0-9_-]{0,63}$/i.test(raw)
                                      || /^agent:[a-z0-9][a-z0-9_-]{0,63}$/i.test(raw) }
var InvalidGatewayModelError = class extends Error {
  constructor() { super("Invalid `model`. Use `openclaw` or `openclaw/<agentId>`."); } };
function resolveAgentIdForRequest(params) {
  if (params.model?.trim() && !isOpenClawAgentModelId(params.model)) throw new InvalidGatewayModelError();
  ... assertKnownAgentId(fromModel, cfg)   // throws UnknownGatewayAgentError
}
```
→ `dist/openai-http-DXFpArY9.mjs:543-546`, `dist/openresponses-http-CYxkOAWh.mjs:521,640`
→ `sendInvalidRequest` → `dist/http-common-BHn0MfxE.mjs:193` → `400`.

**Two deltas that matter to a future image bump, neither blocking:**
1. **Unknown agent ids are now rejected.** 2026.5.7/2026.6.1 have no `assertKnownAgentId`,
   so `model: "openclaw/doesnotexist"` passes validation and proceeds with a nonexistent
   agent; 2026.9.5 answers `400 Unknown agent 'doesnotexist'.` A robustness improvement.
2. 🔴 **`x-openclaw-model` gains an authority check.**
   `dist/http-auth-utils-CqFtwt0r.mjs:635-641` `authorizeOpenAiCompatibleHttpModelOverride`
   → trusted-proxy / `auth.mode=none`-with-narrowed-scopes callers get
   `403 missing scope: operator.admin`. **Shared-secret bearer callers are treated as owner
   (`usesSharedSecretGatewayMethod ⇒ true`) and are unaffected.** clawgate authenticates
   with `Authorization: Bearer sha256("gw-"+HOOKS_TOKEN)` (`gateway.go:21-24`, matching the
   chart's own derivation), i.e. a shared secret — **so clawgate keeps the header on
   2026.9.5.** ⚠ Re-verify that at the moment of any image bump rather than inheriting it
   from this paragraph; it is the one thing in the verdict that a future version could
   revoke.

### 2.4 Will the header be *accepted* on these pods? — INFERRED, not measured

The header value is checked against `createModelVisibilityPolicy(...).allowsKey(...)`. The
allowlist is derived from **`cfg.agents.defaults.models`** — *plural* — and
`allowAny = !visibility.hasEntries`, with `allowsKey` short-circuiting on `allowAny`
(2026.5.7 `dist/model-selection-shared-DOxyWoaQ.js:476-480`; 2026.6.1
`dist/model-selection-shared-WGXc9fXh.js:833-851, 564-584` — two versions, same logic).

The chart never renders a `models` map: `chart/kubeclaw/templates/configmap.yaml:28-40`
emits `agents.defaults.{workspace, model:{primary}, thinkingDefault?, maxConcurrent}` plus
verbatim `agent.defaults` extras, and clawgate adds only
`agents.defaults.model.fallbacks` via `configOverlay` (`values.go:374-378`). Live
configmaps confirm the singular `model` key and no `models` key.

**So `allowAny` should be `true` and any parseable `x-openclaw-model` value should be
accepted.** This is derived from source plus the rendered config shape. **It was NOT
exercised live** — a *valid* header override passes validation and therefore **runs a real
agent turn**, which both investigations declined to do unasked.

🔴 **This is the one open measurement, and it is the first thing to do — before W5 is
written, not after.** It is a single turn against a **non-chief** pod with a trivial prompt:
```
POST http://<pod>:18789/v1/chat/completions
Authorization: Bearer <sha256("gw-"+HOOKS_TOKEN)>
x-openclaw-model: openrouter/anthropic/claude-haiku-4.5
{"model":"openclaw","messages":[{"role":"user","content":"reply with only the word ok"}],"stream":false}
```
Three things to read off it: **(1)** does it 200 rather than `403`/`Model '…' is not allowed`;
**(2)** does the response (or the pod's log) name the model that answered — i.e. is
sent-vs-served **observable**, which §5 says decides what the stamp is allowed to claim;
**(3)** does a **three-segment** `openrouter/<vendor>/<slug>` parse, since the docs say
`<provider/model-or-bare-id>` and every slug in this system has three segments.
⚠ It costs one real turn on a real agent — **operator's call**, and it is why §12 asks.

### 2.5 What remains unmeasured

- **The header end-to-end** (§2.4) — the single most load-bearing gap.
- **Whether a model the visibility policy accepts then RESOLVES in the run.**
  `modelOverride` is handed to `buildAgentCommandInput` as `model:`; the downstream catalog
  lookup and provider-credential selection were not traced. The pod does hold
  `OPENROUTER_API_KEY` (§2.6), so the credential half is not in doubt; the resolution half
  is untraced.
- **Whether `x-openclaw-model` exists in 2026.3.13** (the five `latest` pods).
- **When the agent-first contract was introduced.** Neither CHANGELOG mentions
  `x-openclaw-model`, `chat/completions` or "agent target" (zero grep hits in both) — the
  changelogs are curated prose. Only the two named versions were bisected, not the 257-version list.
- **Whether harbor holds a `clawdbot:2026.6.1`** — no registry query was made.

### 2.6 The pod is already credentialed — and which row is chief is UNCONFIRMED

`devpod-secrets` holds exactly `GITHUB_TOKEN`, `HOOKS_TOKEN`, `OPENROUTER_API_KEY`,
delivered via `envFrom: {secretRef: {name: devpod-secrets, optional: true}}` and confirmed
present in-process (lengths only; **no value printed anywhere**). Server side,
`CLAWGATE_OPENROUTER_API_KEY` comes from `secretKeyRef {name: clawgate-db, key: OPENROUTER_API_KEY}`.

⚠ **One live fact is UNCONFIRMED and the investigation fell into the trap the code
documents.** A pod-name search for "chief" found nothing and concluded "there is no chief
agent". But `internal/api/chief_agent.go:21-31` says exactly this in advance: *"THE
CANONICAL NAME IS GENERATED AND IS NOT 'chief' … the live one is `zesty-stoat`, namespace
`devpod-zesty-stoat`"* — chief is resolved by mutable **DisplayName**, lowest-id tie-break
(`chief_agent.go:88-110`). `devpod-zesty-stoat` **is running**, on `clawdbot:2026.5.7`,
with primary `openrouter/deepseek/deepseek-v4-flash-0731` (a per-dispatch override of the
cluster default) and fallback `openrouter/deepseek/deepseek-v4-flash-0731`.
**So chief is almost certainly `zesty-stoat`, on the 2026.5.7 image, and the verdict holds
for it — but confirm with `SELECT id, name, display_name, model FROM agents WHERE
lower(trim(display_name))='chief' ORDER BY id` before relying on it.** The live probe in
§2.1 was run against that very pod, read-only, with no turn executed.

For completeness, the fleet's models (from each `devpod-<name>/<name>-config` configmap):
`operator` → `openrouter/anthropic/claude-haiku-4.5`; `zesty-stoat` →
`openrouter/deepseek/deepseek-v4-flash-0731`; `witty-heron`, `nimble-shrew` →
`openrouter/xiaomi/mimo-v2.5`; the five `latest` pods → `openrouter/minimax/minimax-m2.7`
(predating the current env). Cluster defaults:
`CLAWGATE_AGENT_MODEL=openrouter/xiaomi/mimo-v2.5`,
`CLAWGATE_AGENT_MODEL_FALLBACKS=openrouter/deepseek/deepseek-v4-flash-0731`,
`CLAWGATE_OPERATOR_MODEL=openrouter/anthropic/claude-haiku-4.5`.

Deployed clawgate: **`0.8.45`** (`harbor.homelab.lan/library/clawgate:0.8.45`,
imageID `sha256:6de86031…`, pod age 11h, 0 restarts), matching the git pin at
`clusters/workbench/apps/clawgate/deployment.yaml:84` (commit `7aee536fc`).
`clawgatectl health` → `{"status":"ok","version":"0.8.45"}`. Clawgate's full log buffer
(54,546 lines, with a positive control returning 52,494 on a pattern that must match)
contains **one** `model` mention — `suggest: using OpenRouter generator
(model=anthropic/claude-haiku-4.5)`, informational. **Zero model-related errors.**

---

## 3. Design

### 3.1 Where the preference lives, and what a new thread inherits

The operator's two behaviours map onto two different rows:

| concept | store | why there |
|---|---|---|
| **preference** — the default for FUTURE threads | **`agents.model`** (exists: migration `0002`, `0014`; `SetModel` at `pgstore.go:145`) | It is already the agent's model, already written by an existing route, already rendered by an existing control, and already the value `buildHelmValues` reads. Adding a second "agent default model" column would be two writers of one fact. |
| **effective model** — what THIS thread's turns use | **new column `chat_sessions.model TEXT NOT NULL DEFAULT ''`** (migration `0039`) | A thread is a `chat_sessions` row (§1.5). Nothing else is per-thread. |

**Inheritance rule (recommended): SNAPSHOT AT THREAD CREATION, not resolve-at-send.**

- New thread → `chat_sessions.model` is written with the agent's *current* `agents.model`
  at the moment the row is created.
- A turn resolves `effective := session.Model` and falls back to `agents.model` → `cfg.Model`
  → `defaultModel` only when the session's is empty.

🔴 **Resolve-at-send is the tempting design and it breaks the operator's stated rule.**
If a turn resolved `agents.model` live, then changing the preference would retroactively
change *every* existing thread that had never been explicitly switched — which is exactly
"selecting a model disrupts an active thread", the thing the second apply step exists to
prevent. Snapshotting is what makes "future threads only" true.

⚠ **Therefore the migration must BACKFILL.** Every pre-`0039` thread has `model=''`, and
under the fallback rule those threads *would* drift with the preference. `0039` must
`UPDATE chat_sessions s SET model = a.model FROM agents a WHERE a.id = s.agent_id AND
s.model = '' AND a.model <> ''` so existing threads are pinned to what they have been
using. Threads whose agent has `model=''` (cluster default) stay `''` and legitimately
follow the cluster default — that is the same behaviour they have today.

⚠ **The empty string is doing two jobs and that is a real ambiguity.** `''` means both
"inherit" and "cluster default". It is survivable because the fallback chain ends at
`defaultModel` either way, and because `agents.model` already uses `''` for exactly this
(`values.go:343`'s nested `orDefault`). A `NULL`-vs-`''` distinction would be more
precise and is not worth a nullable column plus three `sql.NullString` scans; **say so in
the migration comment rather than leaving a reader to wonder.**

### 3.2 THE DESIGN — a header per request, no pod roll anywhere

Given §2, both of the operator's behaviours become **pure database writes plus one HTTP
header**. No pod roll, no restart, no credential change, no image bump.

**The mechanism.** 🔴 **The body `model` literal stays `"openclaw"` at both call sites.**
What is added is a header:

| # | file | change |
|---|---|---|
| 1 | `internal/agents/gateway.go` (`chatStream`) | add `x-openclaw-model: <slug>` when a slug is supplied; body `"model": "openclaw"` **unchanged** |
| 2 | `internal/agents/responses.go` (`streamResponses` / `runToolLoop`) | same header; `responsesRequest.Model` stays `"openclaw"` |

**One rule, one place.** Both sites set the header through a single helper —
`setGatewayModel(req *http.Request, slug string)` — which is also the only place that
knows the header's name. 🔴 **Two call sites open-coding `req.Header.Set("x-openclaw-model", …)`
is the shape that gets fixed at one site and not the other** (`responses.go` is the live
path, `gateway.go` the fallback — §1.1), and a one-place helper is what makes the second
site correct by construction.

**The threading, without a signature change.** `chatTurn`
(`internal/api/agents.go:1476-1492`) holds the `Agent` **by value**, and both gateway
entrypoints already receive it. So:

- a shared `gatewayModelFor(a Agent) string` returns `a.Model` (empty ⇒ no header, i.e.
  the pod's configured primary);
- `chatTurn` sets `a.Model = effective` — the thread's snapshot — before calling.

🔴 **This avoids changing `ChatWithTools`'s signature**, which would otherwise ripple
through `internal/api/ext.go:216` and every fake in `internal/api` and `internal/agents`.
⚠ It is also a **mutation of a parameter that reads as innocuous**: `a` is a value copy, so
nothing else observes it — but a future refactor changing `a Agent` to `a *Agent` would
silently make the panel's thread choice overwrite the agent's stored preference in memory.
**Pin the value-receiver assumption with a guard**, or pass the slug explicitly and accept
the signature churn. This is a genuine fork; **recommendation: the value copy plus the
guard**, because the churn touches ~a dozen fakes for no behavioural gain.

⚠ **`kickoffChat` is a third caller of `runToolLoop`** (`provision.go:1140-1150`) and it has
no session. Under `gatewayModelFor(a)` it gets the agent's preference, which is the right
answer for a kickoff turn — but **assert it**, or a later refactor routes kickoff to the
no-header default and nobody notices.

⚠ **The five `clawdbot:latest` pods (OpenClaw 2026.3.13) are not covered.** They already
404 `/v1/responses` and take the `ErrResponsesUnsupported` fallback; whether they honour the
header is unmeasured (§2.5). Chief is not among them (§2.6). **The header must therefore be
harmless when unsupported** — an unknown request header is ignored by any HTTP server, so
the failure mode there is "the model silently does not change", which is the same state
those pods are in today. Say that in the helper's comment; do not claim fleet-wide coverage.

**What the pod's `agent.model.primary` still governs.** Once every clawgate-initiated turn
carries a header, the Helm value only decides what a turn *the pod starts by itself* uses —
in-pod channels, cron/workflow jobs, and the runtime **fallback** chain
(`agents.defaults.model.fallbacks`, `values.go:374-378`). For a chat-driven agent like chief
that is close to nothing. This is what makes the preference/effective split cheap: the
preference is a *chat-time* default, and it needs no pod state at all.

🔴 **Consequence, and it is the one design decision left: the existing apply route still
rolls the pod, and on this design it no longer needs to.** `handleAgentModel` rolls whenever
the agent is running (`agents.go:945-957`), because `agents.model` is *also* the Helm value.
Two options, and the operator's stated UX ("nothing disrupted") forces one of them:

- **(a) Reuse `agents.model` and stop rolling from the panel's write.** Cheapest. The
  consequence is that the stored preference and the pod's rendered `model.primary` diverge
  until something *else* calls `ReapplyProfiles` (a privilege change, a re-dispatch), at
  which point the pod rolls to the model the operator already chose. **That deferred roll is
  benign in content** — it converges on the operator's own choice — but it is a restart
  attached to an unrelated action, so it must be written down where the next reader will
  find it, not just here.
- **(b) Add a separate `agents.chat_model` column** for the chat-time preference, leaving
  `agents.model` as the provision-time value. No divergence, no deferred roll — at the cost
  of a second column and a UI that must explain why there are two "models".

**Recommendation: (a)**, with the deferred roll named in `handleAgentModel`'s comment and a
guard pinning that the panel's write does **not** roll. (b) is the more correct model and
the wrong trade for a one-operator system: two columns named `model` and `chat_model` is a
fact nobody will remember which half of.

### 3.3 If the §2.4 probe comes back negative — the degradation

The one unmeasured link is whether the header is *accepted and resolved* (§2.4). If it is
rejected (`403 missing scope`, or `Model '…' is not allowed`), or accepted-and-ignored, the
design collapses onto the provision-time model and the honest UX is materially worse:

- **The preference control still ships and is still worth shipping** — it is already what
  `buildHelmValues` reads and it already has a route. What it cannot do is "future threads
  only": the pod has one model at a time, so a new thread on a different model rolls the pod
  for *every* thread.
- **Per-thread effective models become unimplementable as designed.** Two open threads on
  two models would need two pods. The honest degradation:
  - `chat_sessions.model` is still written and still **stamped** (W4 is verdict-independent);
  - "apply to this thread" becomes **"apply and restart chief"** — an explicit confirm
    naming the cost, applying to *all* threads;
  - the preference/effective split collapses to one value with a lag, and the UI must **say
    so** rather than implying isolation it does not have.
- **Cost of a roll: UNMEASURED, and do not quote a number.** `ReapplyProfiles` →
  `helm.Upgrade` is synchronous inside a `safeGo`; readiness runs the chart's init script
  (workspace sync, skills, credential write — several hundred lines of
  `chart/kubeclaw/templates/deployment.yaml`) and, for a repo-backed agent, a `git clone`.
  What *is* certain: it is a restart, the gateway's TCP listener disappears during it
  (`probeGatewayReady`, `gateway.go:37-45`), and an in-flight turn dies with it.
- **A roll also re-opens a question nobody has answered:** the `session_key` is stable
  across restarts by design (`recapSessionName`'s header makes that point for recaps), so
  the *thread row* survives — what the new pod does with an old gateway key is **unmeasured**.
- On this path, **an agent-image bump to 2026.9.5 is the thing to try before building
  anything** (W0), since the header's authority model is the only place the two versions
  differ in a way that could matter.

### 3.4 How the operator learns the switch took effect, and the in-flight turn

This is required on **both** paths, and it is the part the existing route gets wrong.

**On the measured design (§3.2).** The switch is live on the *next turn*, so the honest
acknowledgement is about the next turn, not about a pod:
1. the apply POST returns the **resolved effective model** in its body (not an empty 200),
   and the panel swaps a small thread-scoped indicator showing it;
2. every assistant message is **stamped** (§4 W4), so the transcript itself is the
   evidence — a reader can see where the model changed without trusting a toast;
3. **an in-flight turn is never retargeted.** The apply writes the row; the running turn
   already sent its request with the old model. The UI must say *"applies from your next
   message"* rather than implying it interrupts. Cancelling and retargeting a live turn is
   a different feature and is explicitly out of scope.

**On the §3.3 degradation.** The acknowledgement has to be about the *pod*:
1. the apply returns 202-shaped state, not 200-as-done;
2. the panel polls the agent's live status — `liveStatusIcon` already does exactly this
   (`agents_detail.go:436-450`: `GET /ui/agents/{id}/status` on `load, every 10s`), so the
   mechanism exists;
3. the composer is **disabled while the pod is not running**, because a message sent
   mid-roll is either lost or answered by the old model. Today nothing stops it.
4. an in-flight turn is **killed** by the roll. The operator must be told before the
   apply, not after.

---

## 4. Work items

Auth tier is `requireSession` (operator browser session) for every UI/route item below
unless stated. Repo is `ZacxDev/homelab-infra` unless stated.

### W-PROBE — measure the header end-to-end *(prerequisite, not a code change)*
**Repo:** none · **Deps:** none · **Migration:** none · **Auth:** operator judgement

§2.4, in full. One turn, one non-chief pod, three things read off it: does the header
**200** rather than 403/not-allowed; is the model that answered **observable** (which
decides what W4's column comment may claim — §5); does a **three-segment**
`openrouter/<vendor>/<slug>` parse.

🔴 **It costs one real agent turn on a real agent.** That is why it is a work item with the
operator's name on it (§12) and not something this scope did unasked. Everything in §3.2
is otherwise derived from source plus the rendered config shape, which is strong evidence
and is not a measurement.

⚠ **Do not substitute an invalid-slug probe for it.** An invalid header value proves the
*rejection* path, not the acceptance path — and a rejection can come from the visibility
policy, from parsing, or from authority, which are three different mechanisms with three
different fixes. An empty result cannot distinguish them.

### W0 — bump the agent image *(only if W-PROBE comes back negative)*
**Repo:** `ZacxDev/homelab-infra` (the tag env) and/or the `clawdbot` image repo ·
**Deps:** W-PROBE · **Migration:** none

🔴 **Note the repo carefully — this is not `ZacxDev/openclaw-image`.** clawgate provisions
`harbor.homelab.lan/library/**clawdbot**`, pinned by
`CLAWGATE_AGENT_IMAGE_REPO`/`CLAWGATE_AGENT_IMAGE_TAG` at
`clusters/workbench/apps/clawgate/deployment.yaml:320-328` → `main.go:311-312` →
`Config.ImageRepo`/`ImageTag` → `values.go:311-314` / `imageTagFor()` (`values.go:591`).
`openclaw-image` (whose Dockerfile pins `2026.6.1`) builds a **different image** used by
homelab devpods (§2.2). Editing the wrong Dockerfile changes nothing chief runs.

`Config.OperatorImageTag` is the precedent for adopting a newer image **narrowly** — it
exists because the Operator agent needed `2026.5.7+` for native `/v1/responses` function
tools (`values.go:84-87`, `:588-596`). The same mechanism can give chief a newer tag
without moving the fleet.

Bumping an image is a **deploy act** and is outside this deliverable. ⚠ Target **2026.9.5**
only after re-reading §2.3's second delta: the header gains
`authorizeOpenAiCompatibleHttpModelOverride`, and clawgate's shared-secret bearer is
expected to pass it — **expected, from source; verify at the bump.**

### W1 — mount the model control on the chief panel *(the actual gap)*
**Files:** `internal/ui/chief_panel.go`, `internal/ui/agents_detail.go` ·
**Routes:** none new · **Migration:** none · **Deps:** none

- Export/expose `agentModelControl` to the panel, or add a panel-shaped variant. The
  control is `internal/ui/agents_detail.go:454`; both files are package `ui`, so no
  export is needed — only a **call**.
- Mount it in `chiefPanelActions` (`chief_panel.go:410`, the OOB title-bar slot) or
  directly in `chiefPanelBody`'s configured branch (`chief_panel.go:355-400`).
  **Recommendation: the title bar**, because the slot is already cleared-not-skipped on
  the unconfigured branch, so a panel with no chief cannot render a picker that would post
  to a stale id.
- 🔴 **Do not copy the agent-detail control's prose.** It says *"Selecting a model
  restarts the agent"* and reveals `↳ restarting…`. Under §3.2(a) that is **false** in the
  panel once W4b lands; under §3.3 it is true. The string must be derived from the same
  place the behaviour is, not written twice — see W6.
- ⚠ **Width.** The panel is operator-resizable (`chief-panel` width is persisted,
  `chief_panel.go:805+`'s script) and the control is designed for a `max-w-xl` header.
  It must degrade at the panel's minimum width without pushing the thread-list launcher
  off the title bar.

**Independently revertible of:** everything. Reverting W1 alone returns the panel to
today's markup.

### W2 — raise the OpenRouter cache TTL
**Files:** `internal/api/openrouter.go` · **Routes:** none new · **Migration:** none · **Deps:** none

- `openRouterCacheTTL = time.Hour` → a longer value. **Recommend 24h**, with the reason in
  the constant's comment: the catalogue is a public, slowly-changing list; a redeploy
  empties the cache anyway, so the TTL's only job is to stop a re-fetch inside one
  process's lifetime.
- 🔴 **State the accepted cost in the code, not just here.** "A clawgate redeploy empties
  this and the first picker open after a deploy pays a live fetch" is the operator's
  explicit trade. A comment saying so is what stops the next session from 'fixing' it with
  a Postgres table the operator declined.
- ⚠ **Do not also change `openRouterMaxResult`.** It is a *response* cap, unrelated to the
  TTL, and W3 depends on the distinction (§1.7).
- ⚠ A longer TTL widens the window in which a genuinely new model is missing from the
  cache — which is exactly why W3's escape hatch is not optional.

### W3 — validate the slug, with an escape hatch
**Files:** `internal/api/openrouter.go` (a validation helper beside the cache),
`internal/api/agents.go` (`handleAgentModel`), plus the new thread-apply route (W5) ·
**Routes:** modifies `POST /agents/{id}/model` · **Migration:** none · **Deps:** W2 (same file)

- New helper on `modelCache`: `known(slug string) bool` — checks the **full `c.ids`**
  under the mutex. **Not** the capped route response (§1.7).
- `handleAgentModel` rejects an unknown slug with a **422 and a body naming the slug**,
  instead of storing it. Today it stores anything (§1.8).
- 🔴 **Empty is not invalid.** `""` means "cluster default" throughout
  (`values.go:343`'s nested `orDefault`, `curatedModels()`'s first entry). Validation must
  accept it, or the "default" option in the existing picker starts failing.
- 🔴 **A cold or failed cache must not become a gate that refuses everything.** `models()`
  serves a possibly-**empty** stale cache on error (`openrouter.go:41-44`). If `c.ids` is
  empty, validation has *no opinion* and must **allow** — fail-open, and say so in the
  comment. A validator that refuses every slug because the catalogue fetch failed turns an
  OpenRouter outage into "chief's model picker is broken".
- **Escape hatch for a model too new for a stale cache.** Recommended shape: the submit
  carries an explicit acknowledgement — a form field (`allow_unlisted=1`) set by a
  deliberate second action in the UI, not a checkbox that is easy to leave on. On that
  path the slug is stored and the response says plainly that it was not in the catalogue.
  ⚠ The escape hatch is what makes this validation *safe to add*, so it must land in the
  **same PR**; validation without it is a new way to be blocked.
- ⚠ **Validation is shape-blind.** `known()` proves a slug is in OpenRouter's catalogue.
  It does **not** prove the pod can run it (quota, provider outage, a model the key has no
  access to). It closes the *typo* hazard the brief named, and nothing wider. Say that in
  the helper's comment so the next reader does not treat a green validation as a promise
  the pod will start.

### W4 — stamp the model on assistant messages
**Files:** `internal/db/migrations/0039_chat_message_model.sql`, `internal/agents/store.go`
(`ChatMessage`), `internal/agents/pgstore.go` (`AddChatMessage` + the message readers),
`internal/ui/agents_detail.go` (`ChatLine` + the assistant bubble), `internal/api/agents.go`
(the persist loop at ~`1418-1425`), `internal/api/chief_panel_ui.go:114-117` (the
`ChatLine` build) · **Migration:** `0039` · **Deps:** none (independent of the verdict)

- `ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS model TEXT NOT NULL DEFAULT ''` —
  the same append-only, safe-default shape as `0015` (`kind`/`tool_id`/`tool_name`/`tool_ok`).
  Legacy rows read `''` and render exactly as today.
- Stamp it where the assistant parts are persisted — `internal/api/agents.go`'s loop
  `for _, part := range parts { … AddChatMessage(… Role: "assistant" …) }` — with the
  **effective model the turn actually ran on**, resolved once before the turn and reused,
  **never re-read after the turn**. 🔴 Re-reading would record the model the operator
  switched to *during* the turn, mislabelling the answer — the precise failure the whole
  stamp exists to prevent.
- User rows are **not** stamped (`''`). A user message has no model.
- Display: only show the stamp when it **differs from the thread's** effective model, or
  put it behind the existing message affordances. A per-message chip on every bubble is
  noise in the common case where a whole thread is one model.
- ⚠ **It does not automatically appear in the machine API.** `internal/api/machine_agents.go`
  uses a **dedicated DTO** rather than re-tagging `agents.ChatMessage` (its own comment at
  `:249` says why). Surfacing it there is a separate decision; **make it explicitly** in the
  PR description rather than by omission.

### W4b — carry the model to the gateway as a header
**Files:** `internal/agents/gateway.go`, `internal/agents/responses.go`,
`internal/api/agents.go` (`chatTurn` sets `a.Model`) ·
**Routes:** none · **Migration:** none · **Deps:** W-PROBE (its semantics), §9's `chatTurn` overlap

§3.2, in full. The two mechanical facts that must not be lost:

🔴 **The body `model` literal STAYS `"openclaw"` at both sites.** `gateway.go:58` and
`responses.go:402` are correct as written. Replacing either with an OpenRouter slug produces
a hard **400 on every turn** — chief stops answering entirely (§2.0). A reviewer who read
only the brief would approve exactly that change.

🔴 **Both sites, through one helper.** `responses.go` is the live path and `gateway.go` the
`ErrResponsesUnsupported` fallback (§1.1); a fix applied to one is invisible until the other
is reached, which is the hardest kind of half-fix to notice.

⚠ The helper must be **harmless on a pod that does not support the header** — an unknown
request header is ignored, so the worst case is "the model does not change", which is the
current state. State that; do not claim fleet coverage (the five `clawdbot:latest` pods are
unmeasured, §2.5).

**Independently revertible of:** W1, W2, W3, W4. Reverting it returns every turn to the
pod's configured primary with no schema residue.

### W5 — the thread-scoped effective model + its apply step
**Files:** `internal/db/migrations/0040_chat_session_model.sql`, `internal/agents/store.go`
(`ChatSession`), `internal/agents/pgstore.go` (a `SetSessionModel`, and `model` in
`chat_sessions` scans + `LatestOrCreateSession`/`CreateSession`),
`internal/api/agents.go` (a new handler + `handleAgentSessionCreate`'s snapshot),
`internal/api/ext.go` (store interface), `internal/ui/chief_panel.go` (the apply control),
`internal/api/testdata/routes.golden` ·
**Routes:** **new** `POST /agents/{id}/sessions/{sessionId}/model`, `requireSession` ·
**Migration:** `0040` · **Deps:** W3 (validation), W4 (the stamp is what makes it legible),
and §2's verdict for its semantics

- `chat_sessions.model TEXT NOT NULL DEFAULT ''` + the backfill in §3.1.
- Snapshot on creation in `handleAgentSessionCreate` (`internal/api/agents.go:1154`).
  🔴 **Write it on the agent the handler resolved (`a`), not the path's `{id}`** — that
  handler already retargets `a = chief` on the panel branch for exactly this class of bug,
  and its own 🔴 comment block (`:1171-1210`) is the record of it going wrong once.
- The apply control is a **second, explicit action** — not the same combobox. Recommended:
  the panel's picker sets the *preference*; a distinct "use for this thread" affordance,
  disabled unless the picked slug differs from the thread's effective one, performs the
  thread apply. **Two controls, two routes, two writes** — which is also what makes them
  independently testable.
- ⚠ **Route-shape decision to make once:** a *new* route under the session, or a `session`
  form field on the existing `POST /agents/{id}/model`. **Recommend the new route.** The
  existing route's contract is "set the agent's model and maybe roll the pod"; overloading
  it with a mode that must *not* roll puts two behaviours behind one path, and
  `routes.golden` would not show the difference.

### W6 — one source of truth for "does switching restart the agent?"
**Files:** `internal/ui/agents_detail.go`, `internal/ui/chief_panel.go`,
`internal/api/agents.go` · **Routes:** none new · **Migration:** none · **Deps:** §2's verdict

The string *"Selecting a model restarts the agent"* and the `↳ restarting…` reveal are
**claims about behaviour** currently written as literals in the renderer
(`agents_detail.go:483-485`). Under §3.2 option (a) the panel's write stops rolling while the
agent-detail control's still does (until §10 lands). **Two surfaces would then disagree about
one fact, in prose, with nothing failing.**

So: derive the copy from a single named predicate (e.g. a `modelSwitchRolls` bool on the
view, set by the handler-side truth), and pin it. This is small, and it is the item that
stops the feature shipping a sentence that is false on one surface.

### W7 — the acknowledgement path
**Files:** `internal/api/agents.go` (both apply handlers), `internal/ui/chief_panel.go` ·
**Routes:** changes the *response* of `POST /agents/{id}/model` · **Migration:** none ·
**Deps:** W1, W5, W6

§3.4. On the measured design: return the resolved effective model and render it; say "from
your next message". On the §3.3 degradation: 202-shaped, poll `GET /ui/agents/{id}/status`
(the mechanism already exists at `agents_detail.go:436`), disable the composer while the pod
is not running.

⚠ **Changing the existing route's response body from empty-200 is a contract change.** Its
current consumer is `hx-swap: none` + `HX-Trigger` (`agents_detail.go:471-476`), which
ignores the body — so adding one is safe *today*. That is a fact to verify at the moment of
the change, not to inherit from this document.

---

## 5. Test coverage — per item

Format per item: **what pins it** · **the red-at-`origin/trunk` guard** · **the mutation
that must kill it, and the message that must appear** · **fixture states required**.

🔴 Two rules apply to every row and are not restated in each:
1. **A guard must be watched fail on pre-change code.** Report the matrix as
   "red at `15744e013`, green at HEAD". A guard that pins an invariant the bug never
   violated is an *invariant guard*: label it, do not count it as regression coverage.
2. **A mutation must die with THIS guard's own message.** If a different assertion's
   failure kills the test, the guard is green for the wrong reason and would stay green if
   deleted. Confirm the failing assertion by name.

### W1 — the control is mounted on the panel

- **Pins:** render `RenderChiefPanelBody` with a configured `ChiefPanelView` and assert
  the output contains the combobox's own structural markers — the hidden input named
  `model` and the `data-combobox-hidden` attribute the autosave trigger keys on — **and**
  that the form's `hx-post` resolves to the chief's id.
- **Red at trunk:** yes, and this is the one guard that is *provably* red today: the file
  has zero `model` occurrences (§1.4), so any assertion for a model control fails at
  `origin/trunk`.
- **Mutation:** delete the mount call from `chiefPanelActions`. Must fail with the
  panel-mount assertion, not with a golden-route or CSS assertion.
- 🔴 **Assert the STATE, not a WORD.** `strings.Contains(html, "model")` passes on the
  word "model" appearing anywhere — including in an unrelated tooltip. Assert the input's
  `name="model"` **plus** the `hx-post` path **plus** `data-combobox-hidden`: three
  properties of one control, which a different feature cannot spell by accident.
- **Fixtures:** configured panel with a chief; **and** the unconfigured panel
  (`ChiefPanelView{}`), which must render **no** picker — the OOB slot is cleared, not
  skipped (`chief_panel.go:333-340`), so a picker leaking into it would post to no agent.

### W2 — the TTL

- **Pins:** the constant's value, and that `models()` does **not** re-fetch inside it.
- **Red at trunk:** a bare `openRouterCacheTTL == 24*time.Hour` assertion is red at trunk
  but is a **tautology** — it restates the constant. 🔴 **Label it an invariant guard, not
  regression coverage.** The behavioural half is what earns its place: seed `orCache` with
  `fetched: time.Now().Add(-2 * time.Hour)` and a sentinel id, call `models()` with an
  HTTP transport that **fails the test if it is invoked**, and assert the sentinel comes
  back. That is red at trunk (1h TTL → it would fetch) and green after.
- **Mutation:** set the TTL back to `time.Hour`. Must fail with the no-fetch assertion's
  own message ("the cache re-fetched inside its TTL").
- 🔴 **`orCache` is package-level mutable state, so the test must reset it** (or the
  helper must take a `*modelCache`) or these tests become order-dependent and one of them
  will pass because another left the cache warm. **Prefer refactoring `models()` /
  `known()` to methods on an injectable `*modelCache`** — it is a smaller change than the
  flakiness it removes.
- **Fixtures:** a warm cache inside TTL; a warm cache past TTL; an **empty** cache; a
  cache whose fetch **fails** (stale-serve path).

### W3 — validation and the escape hatch

- **Pins:** (a) a known slug is stored; (b) an unknown slug is **refused** and **not**
  stored; (c) `""` is accepted; (d) an **empty catalogue** allows anything (fail-open);
  (e) the escape hatch stores an unlisted slug and says so.
- **Red at trunk:** (b) and (e) are red at trunk (no validation exists). (a), (c), (d) are
  **invariant guards** — they pin behaviour that already holds and would break *if the
  validator over-reached*. Label them as such.
- **Mutations, each with its own message:**
  - remove the `known()` check → (b) fails with *"an unknown slug was stored"*;
  - invert fail-open to fail-closed on an empty catalogue → (d) fails with
    *"validation refused a slug while the catalogue was empty"* — 🔴 this is the mutation
    most likely to survive a careless suite, because a fixture with a **warm** cache
    cannot observe it. §6 names it;
  - make the escape hatch unconditional (ignore the acknowledgement field) → a guard that
    an **unacknowledged** unlisted slug is still refused must fail;
  - make validation reject `""` → (c) fails with *"the cluster-default option was refused"*.
- 🔴 **Validate against the FULL catalogue, and prove it.** Build a cache with **≥ 26**
  ids where the model under test sorts **past position 25**, and assert it validates.
  A fixture with 3 ids cannot distinguish `known()` from "is in the capped response" —
  both pass — so it would score a `known()`-reads-the-capped-list mutant as SURVIVED.
  ⚠ Choose ids that are **pairwise distinct and distinct from any constant the assertion
  names**, and overshoot the cap rather than sitting exactly on it: a fixture with exactly
  25 or exactly 26 ids lands on the boundary and a mutant changing `>=` to `>` dies for
  the wrong reason.
- **Fixtures:** warm catalogue containing the slug; warm catalogue **missing** a valid new
  model (the stale-cache state); **empty** catalogue; an **invalid** slug; the escape hatch
  acknowledged and unacknowledged; `""`; a 26+-entry catalogue with the target past the cap.

### W4 — the stamp

- **Pins:** (a) migration `0039` exists, is contiguous, and adds the column with the safe
  default; (b) an assistant row persisted by a turn carries the effective model; (c) a
  **user** row carries `''`; (d) a thread whose rows span **two** models renders both
  distinguishably; (e) a legacy row (`model=''`) renders exactly as it does today.
- **Red at trunk:** (b) and (d) are red at trunk (no column, no stamp). (a) is already
  enforced by `TestMigrationVersionsAreUniqueAndContiguous`; do not re-implement it. (c)
  and (e) are invariant guards.
- **Mutations:**
  - stamp the **agent's** model instead of the **thread's effective** one → (b) must fail
    with *"the assistant row records the agent preference, not the model the turn ran on"*,
    which requires a fixture where the two **differ** (see §6);
  - **re-read** the model after the turn instead of using the pre-resolved value → a guard
    that changes the preference *between* turn-start and persist must fail. 🔴 This mutant
    survives every fixture in which nothing changes mid-turn, so the mid-turn fixture is
    mandatory, not thorough;
  - stamp user rows too → (c) fails;
  - drop the `DEFAULT ''` → the legacy-render guard (e) fails on a NULL scan.
- **Fixtures:** a session with assistant rows under model A and assistant rows under
  model B (the two-model thread); a pre-migration-shaped row with `model=''`; a turn
  during which the preference is changed.

### W4b — the gateway header (and the body field that must NOT move)

🔴 **This is the highest-value guard in the whole scope**, because the failure it prevents is
total (chief answers nothing) and the mistake that causes it is the *obvious reading of the
brief*.

- **Pins:** against an `httptest` gateway capturing the request, for **both** paths
  (`chatStream` and `runToolLoop`): (a) the body's `model` field is **exactly `"openclaw"`**;
  (b) `x-openclaw-model` carries the effective slug; (c) an empty effective model sends **no**
  such header; (d) the kickoff path sends the agent's preference.
- **Red at trunk:** (b), (c) and (d) are red at trunk (no header exists). **(a) is an
  invariant guard** — it pins behaviour that already holds — and it is the one worth writing
  anyway, because it is the only thing that fails when someone "fixes" the literal.
- **Mutations, each with its own message:**
  - set the body `model` to the slug → (a) fails with *"the gateway body must send the agent
    target `openclaw`; an LLM slug there is a hard 400 from OpenClaw and no turn runs"*.
    🔴 That message must name the consequence, not just the mismatch — a bare
    `want "openclaw", got "openrouter/…"` reads like a cosmetic assertion and gets
    "corrected" in the wrong direction;
  - add the header in `responses.go` only → the `chatStream` case fails;
  - add it in `gateway.go` only → the `runToolLoop` case fails. 🔴 **Both single-site
    mutants must be in the sweep.** A suite exercising only the live path scores the
    `gateway.go`-missing mutant as SURVIVED, and that is the path a 2026.3.13 pod takes;
  - send the header unconditionally with an empty value → (c) fails.
- 🔴 **Prove the guard is REACHABLE on each path, not just breakable.** `runToolLoop` is
  reached first and `chatStream` only via `ErrResponsesUnsupported`, so a test that never
  makes the stub 404 `/v1/responses` cannot execute the `gateway.go` assertion at all — it
  would pass with that code deleted. The stub must be able to answer **404 on
  `/v1/responses`** so the fallback is genuinely taken.
- **Fixtures:** a gateway stub recording body+headers; the same stub 404-ing `/v1/responses`;
  an agent with `Model: ""`; an agent with a slug; a kickoff (no session).
- ⚠ **These are unit-level guards over the request clawgate BUILDS.** They cannot prove the
  real gateway honours it — only W-PROBE can, and it is not a test.

### W5 — thread-scoped model, snapshot and apply

- **Pins:** (a) a new thread's `model` equals the agent's `model` **at creation**;
  (b) changing the **preference** afterwards does **not** change any existing thread's
  effective model; (c) the apply route changes **only** the named thread; (d) the apply
  route validates (W3) — including the escape hatch; (e) the backfill pins pre-`0040`
  threads; (f) the panel branch snapshots onto the **resolved chief**, not the path id;
  (g) the route appears in `routes.golden` under `requireSession`.
- **Red at trunk:** (a)–(f) all red at trunk (nothing exists). (g) is enforced by the
  existing golden + `TestEveryBrowserSurfaceRequiresAHumanSession`; **regenerate the
  golden in the same commit** or the suite is red for a bookkeeping reason.
- **Mutations:**
  - replace the snapshot with resolve-at-send → (b) must fail with *"changing the
    preference retargeted an existing thread"*. 🔴 **This is the mutation that decides the
    feature**, and it is invisible unless a fixture has a thread created **before** the
    preference changed and a turn **after**;
  - make the apply write `agents.model` instead of `chat_sessions.model` → (c) fails —
    requires **two** threads in the fixture, or the mutant is indistinguishable;
  - drop the backfill from the migration → (e) fails;
  - use `{id}` instead of the resolved `a.ID` in the panel branch → (f) fails. Fixture:
    **two** agents, both display-named `chief`, with the lower id being the resolved one
    (`chiefAgent`'s documented tie-break, `chief_agent.go:96-109`) and the *stale* id
    posted.
- **Fixtures:** two threads on one agent; a thread created before a preference change; a
  pre-migration thread; two chief-labelled agents; an agent with `model=''`.

### W6 — the restart claim

- **Pins:** the two surfaces' copy is derived from one predicate. Assert the **whole
  normalised string** rendered on each surface for each value of the predicate, and assert
  that the *literal* appears in exactly one non-test source file.
- **Red at trunk:** red if trunk has the string in two places; today it is in one
  (`agents_detail.go`), so the guard's honest framing is: *the panel must not introduce a
  second copy*. That makes it an invariant guard the moment W1 lands correctly — which is
  the point of writing it with W1 rather than after.
- **Mutation:** paste the literal into `chief_panel.go`. Must fail with *"the restart
  claim is written in two places"*.
- 🔴 **A guard on the WORD "restarts" is walkable by rewording.** Pin the whole normalised
  sentence and the single-source-of-truth relationship, not a keyword.
- **Fixtures:** predicate true and false; both surfaces rendered for each.

### W7 — the acknowledgement

- **Pins:** the apply response carries the resolved effective model (§3.2) / the
  not-yet-live state (§3.3); the composer is disabled while the pod is not running (§3.3
  only); an in-flight turn is **not** retargeted (both).
- **Red at trunk:** yes — trunk returns an empty 200 and never disables anything.
- **Mutations:** return an empty body → the response-content guard fails; retarget the
  in-flight turn → the in-flight guard fails with *"a turn already in flight used the new
  model"*.
- **Fixtures:** an apply issued **mid-turn** (the turn's model must be the pre-apply one
  and the next turn's the post-apply one) — this is the fixture that the whole item rests
  on; a pod in each of running / provisioning / stopped.

### The "gateway ignores the model" fixture — and what it forces the stamp to claim

The brief named this state, and §2 turns it from a hypothetical into a **known** one: an
unknown request header is ignored by any HTTP server, and the five `clawdbot:latest` pods
(OpenClaw 2026.3.13) are unmeasured (§2.5). So a gateway that takes the header and answers
from a different model is a **real** state, not a paranoid one.

**Two fixtures, and they catch different things:**
1. a stub that **rejects** an LLM slug in the body with `400` — the production behaviour
   measured in §2.1, and the fixture that makes the W4b guard a *regression* test rather
   than an invariant guard: it fails exactly the way production fails;
2. a stub that **accepts the header and answers from a fixed model regardless** — the
   silent case.

🔴 **Fixture 2 is what decides the wording of W4's column comment, and the honest answer is
the weaker one.** Nothing in clawgate today can observe which model *answered*: the SSE
chunk parser reads only `choices[].delta.content` (`gateway.go:94-100`) and the responses
parser only `output[].content[].text` (`responses.go:messageText`) — **neither reads a
`model` field off the response.** So:

- the stamp records **what clawgate ASKED for**, and the migration comment must say exactly
  that — *"the model clawgate requested for this turn"* — never *"the model that answered"*;
- a sent-vs-served check is only possible if W-PROBE finds the response (or the pod log)
  names the model used. **If it does, add it as a separate assertion, in its own PR.** If it
  does not, say so in the comment and stop.

⚠ **Writing the stronger claim is the default failure here, not a stretch.** "Stamp the
model on assistant messages" invites a comment that says the column holds the answering
model, and four audit rounds elsewhere in this codebase have found exactly that shape —
a comment asserting a guarantee one notch stronger than the code. Write the claim **after**
the code, from what it does.

---

## 6. The six named fixture states — where each is constructed

A guard can only observe states its fixture can construct. The brief named six; here is
where each lives and which mutant it is the *only* witness to.

| state | constructed in | the mutant only it can kill |
|---|---|---|
| **a stale catalogue missing a valid new model** | `internal/api` — seed `orCache.ids` without the slug, `fetched: now` | escape hatch made unreachable / validation fail-closed |
| **an invalid slug** | `internal/api` — POST a slug absent from a non-empty cache | the `known()` check removed |
| **a thread whose messages span two models** | `internal/agents` store test + `internal/ui` render test — two `AddChatMessage` calls with different `model` | the stamp reading a single agent-level value |
| **a preference set but not applied** | `internal/api` — `SetModel` on the agent, thread row untouched, then a turn | snapshot → resolve-at-send |
| **an apply mid-turn** | `internal/api` — a gateway stub that blocks in-flight while the apply POST lands | post-turn re-read of the model; in-flight retarget |
| **a gateway that ignores the model field** | `internal/agents` — an `httptest` gateway answering from a fixed model regardless of header | a stamp comment claiming "the model that answered" |
| **a gateway that REJECTS an LLM slug in the body** (`400`, production behaviour) | `internal/agents` — the same stub, 400-ing a non-`openclaw` body `model` | the body literal replaced with a slug — the total-failure mutant |
| **a gateway that 404s `/v1/responses`** | `internal/agents` — needed to *reach* the `chatStream` fallback at all | the header added on the live path only |

⚠ **Two of these are cross-package**, and that is the seam this feature's tests are most
likely to miss: the *stamp* lives in `internal/api`'s persist loop, the *effective-model
resolution* in `internal/agents`, and the *render* in `internal/ui`. Three surfaces, each
hermetically testable, and a defect that lives in none of them alone — e.g. `internal/api`
resolving correctly and `internal/ui` rendering a different field. **At least one guard
must load all three** (build a session, run a stubbed turn, render the panel, assert the
stamp on screen matches the model the stub was asked for) or the seam is unowned.

---

## 7. The CSS trap — mandatory for any item adding a class

`tailwind.config.js`'s content list includes `./internal/ui/**/*.go`, and that glob
**does not exclude `_test.go`**. So a guard of the form *"assert class `X` appears in
`web/static/app.css`"* is satisfied by the test file's **own** literal: it passes on a
tree where the renderer never emits the class. Measured on a sibling PR today; the config's
own comment records the mirror-image incident (a package-wide `internal/api` glob shipped
`.[project:foo-bar]` and two other fixture strings into the stylesheet).

**The requirement:** any new-class guard must assert the **rendering source is inside a
content glob** — the precedent is `internal/api/browser_auth_test.go:570-640`
(`TestEveryDocumentRenderedFromApiIsInTailwindsScanPath`), which *derives* the set of
emitters and checks each is scanned, with a **positive control** that fatals when the
detector finds nothing.

**And the mechanism already exists for the attribution half:**
`internal/ui/css_test_only_classes_test.go` provides `cssScannedSources(t)` — which
already splits **non-test** from **test** sources — and `cssTokenOccurs`.
`TestTheStylesheetCarriesNoNewTestOnlyClasses` fails when a class in `app.css` is
attributable only to a test file, with positive controls on both the extractor
(`len(classes) < 200` → fatal) and the attribution (`< 100` attributed → fatal).

🔴 **So the correct guard is: assert the class is attributable to a NON-TEST source**, via
`cssScannedSources`. Do not write a fresh `strings.Contains(appCSS, ".my-class")` — that is
the exact self-satisfying assertion. And note `app.css` is a **gitignored build artifact**:
the test must fail with the `make css` instruction (the existing test's `t.Fatalf` shows the
shape) rather than skipping, or it becomes a test that silently does not run.

**Cheapest correct answer: add no new class.** The combobox, the chips and the hint styles
all already exist and are already attributed. W1 as scoped needs **no new class**; only a
thread-scoped indicator (W5/W7) plausibly does.

---

## 8. Sequencing, the recommended first PR, and revertibility

| PR | item | migration | independently revertible of | why here |
|---|---|---|---|---|
| **1** | **W1** — mount the existing control on the panel | none | everything | The literal gap (§1.4), zero new routes, zero schema, zero new CSS, and the only item **provably red at trunk** by grep. It ships a working picker for the *agent-level* model on the surface the operator asked for, and it is correct whatever W-PROBE returns. |
| **2** | **W2 + W3** — TTL and validation+hatch together | none | 1 | One file, one concern ("the catalogue is a stale local list and the submit must survive that"). 🔴 They must ship **together**: a longer TTL widens the stale window, and validation without the hatch is a new way to be blocked. |
| **3** | **W4** — the stamp | **0039** | 1, 2 | Verdict-independent, append-only, safe default, legacy rows unchanged. It is also the **evidence surface** every later item's acknowledgement leans on. |
| **4** | **W4b** — the gateway header | none | 1, 2, 3 | Where the whole design lives, and where the total-failure mutant lives. Small, self-contained, and its guards are the ones worth writing most carefully. **Needs W-PROBE first** and **needs §9's `chatTurn` sequencing.** |
| **5** | **W6** — one source of truth for the restart claim | none | 3, 4 | Tiny, and it must precede W5/W7 or those items write the second copy of a behavioural claim. |
| **6** | **W5** — thread-scoped model + apply | **0040** | — (needs 3 for legibility, 2 for validation, 4 to have any effect) | The largest item: one new route, one migration, a golden regeneration. **Dead on arrival without PR 4** — the thread's model would be stored, stamped, displayed and never sent. Must not merge first. |
| **7** | **W7** — the acknowledgement | none | — (needs 6) | Changes an existing route's response contract; last, so the contract moves once. |
| **0a** | **W-PROBE** | none | — | **Before PR 4 is written.** Not a code change; one real agent turn, operator's call (§12). |
| **0b** | **W0** — image bump | none | all | **Only if W-PROBE comes back negative.** A deploy act, outside this deliverable. |

🔴 **PR 3 claims `0039` and PR 6 claims `0040`. If they are authored concurrently, both will
claim `0039`** — the exact semantic conflict `migrate_test.go`'s header records from
#174/#175: it merges cleanly, passes on both branches, and stops clawgate from starting on
deploy. **Whichever migration merges second must be renumbered before merge, and the merged
tree must be TESTED, not reasoned about.** ⚠ And the base will move under these PRs from the
sibling scope's work too (§9), so a merged-tree run done once at the start is not the claim
you need — **re-run it whenever the base moves.**

**Recommended first PR: W1.** It is the smallest change that closes the stated gap, it
cannot be wrong about W-PROBE because it does not touch the gateway, it needs no schema, no
new route, no golden regeneration and no new CSS class, and reverting it leaves no residue.
⚠ **Be honest about what PR 1 alone gives the operator:** a model picker on the chief panel
that behaves exactly like the agent-detail one — i.e. it **restarts chief**. That is the
current behaviour and the existing copy says so truthfully, so PR 1 ships no lie; the
"nothing disrupted" half of the requested UX arrives with PR 4.

---

## 9. Overlaps and conflicts to sequence, not discover

🔴 **`chatTurn` (`internal/api/agents.go:1476-1492`) is touched by two separate efforts.**
- **This scope** wants it to resolve the thread's effective model before the gateway call
  (§3.2 — set `a.Model = effective` on the value copy).
- **`claudedocs/scope-chief-situational-awareness-2026-09-19.md`** — a concurrent
  investigation in this same `claudedocs/` directory — scopes **W6 seam A** as its **PR 2**:
  add a chief branch to the same `if a.Name == agents.OperatorName { … } else { … }` at
  `agents.go:1476-1479`, so chief stops receiving the worker system prompt and worker tools.

They edit **adjacent lines of one function** for unrelated reasons. A clean git merge is
likely and proves nothing: the semantic hazard is that one side rewrites the branch
structure the other inserted into. Concretely — if the chief branch lands as a
three-way `if/else if/else`, an independently-written `a.Model = effective` placed in the
`else` arm would apply to workers and **not to chief**, which is the only agent this
feature is for.

**Recommendation:** let the situational-awareness PR 2 land **first** (it is smaller, it is
already scoped and sequenced, and it fixes a live wrong-prompt symptom), then write this
feature's change against the merged shape. If they must run concurrently, the model
resolution belongs **above** the branch — resolved once, before any prompt/tool selection —
which makes it structurally independent of however many arms the branch grows.

Two smaller overlaps:
- **`internal/api/agents.go`** is edited by W3, W4, W5, W7 **and** the sibling scope. It is
  1,533 lines and the edits are in different functions, but the base moves under each PR.
  **Re-run the merged-tree test whenever the base moves**, not once at the start.
- **`internal/api/testdata/routes.golden`** — W5 regenerates it; any concurrent route
  change conflicts there. It is a mechanical conflict with a mechanical fix
  (`UPDATE_ROUTES_GOLDEN=1 go test ./internal/api -run TestRoutesMatchGolden`), and it is
  the one conflict that is *safe* to resolve by regenerating.

---

## 10. Follow-on wins — named, deliberately NOT folded in

1. 🔴 **Per-request models DO work (§2), so the agent-detail page's model switch is heavier
   than it needs to be.** `handleAgentModel` rolls the pod on every change
   (`agents.go:945-957`) and the control announces *"Selecting a model restarts the
   agent"*. Once clawgate sends `x-openclaw-model` on every turn (W4b), the roll is only
   needed to change what a turn **the pod starts by itself** uses — in-pod channels,
   cron/workflow jobs, and the fallback chain — not what a **chat** turn uses. Splitting
   those is a real win and a real behaviour change on a surface this feature does not
   otherwise touch. **Its own PR.** ⚠ It also removes the deferred-roll wart §3.2(a)
   accepts, so it is the natural sequel rather than an optional extra.
1b. **The five `clawdbot:latest` pods run OpenClaw 2026.3.13** (§2.2) — four months behind
   the `2026.5.7` pods and six behind upstream, and they 404 `/v1/responses` so they take
   the tool-less fallback for every turn. Nothing in this feature depends on fixing that,
   and it is a standing fleet-staleness finding worth its own look.
2. **`models()` has no error backoff** — a failing fetch re-tries on every call because
   `fetched` only advances on success (`openrouter.go:41-47`). Harmless at today's call
   rate; worth a note in W2's comment, not a change.
3. **`routes_golden_test.go`'s header comment is stale** about `requireSession` (§1.8).
   One-line fix, any PR that touches the file.
4. **The machine API does not expose the stamp** (§4 W4). A decision, not a defect.

---

## 11. What I did NOT investigate

- 🔴 **The header end-to-end (W-PROBE, §2.4)** — the one load-bearing gap in the design.
  Accepted-and-resolved is derived from source plus the rendered config shape; it is not a
  measurement, and no amount of re-reading the source will make it one.
- 🔴 **Which agent row is actually chief.** §2.6: a pod-name search says "no chief", which is
  the trap `chief_agent.go:21-31` documents in advance. `zesty-stoat` is the documented live
  chief and it is running on `2026.5.7`, but **no `SELECT … WHERE display_name='chief'` was
  run**, so "chief's pod is on the newer image" is highly likely and unconfirmed.
- **Whether `x-openclaw-model` exists in OpenClaw 2026.3.13** (the five `latest` pods).
- **Whether harbor holds a `clawdbot:2026.6.1` tag** — no registry query was made, so W0's
  feasibility is unpriced.
- **Whether the response or the pod log names the model that answered.** This is not a
  curiosity: it decides whether W4's column may claim an observation or only an intent (§5).
  The stream parsers read no `model` field (`gateway.go:94-100`, `responses.go:messageText`),
  so absent a W-PROBE finding, the weaker claim is the only honest one.
- **Any cost, quota or rate-limit consequence of letting the operator pick an arbitrary
  model.** `openrouter/auto` and a frontier model differ by orders of magnitude per turn
  and nothing in this design budgets, caps or warns. The sibling scope names the same gap
  ("cost/rate limits of chief's model") and also leaves it open. **This is a real
  unscoped risk, not an oversight to be quietly inherited.**
- **Whether a model switch should be per-thread at all on the OPERATOR page** — this scope
  is chief-panel-shaped because the request was. `agentChatPane` is shared, so W5 could
  reach `/agents/{name}` and `/operator` for free; whether it *should* was not asked.
- **Timing of a pod roll** (§3.3) — no measurement was taken. Do not quote one.
- **What a restarted pod does with a pre-existing gateway `session_key`** — unmeasured.
- **OpenClaw's thinking/reasoning-budget interaction with an arbitrary model.**
  `agent.thinkingDefault` exists precisely because reasoning models "burn their output
  budget on reasoning and emit empty/incomplete turns"
  (`chart/kubeclaw/values.yaml:62-67`) — and it is a **pod-level** setting. A per-request
  model can therefore be paired with a `thinkingDefault` tuned for a *different* model.
  Unmeasured, and a plausible way for a per-request model switch to produce empty turns
  that look like a clawgate bug.
- **Mobile layout** of a picker in a resizable panel (§4 W1's width note) — not designed.
- **The `e2e` specs that would need extending** — enumerated as surfaces (§1.9), not written.

---

## 12. Questions for the operator — asked once, together

0. 🔴 **May one real turn be spent on W-PROBE (§2.4)?** One trivial prompt, against a
   **non-chief** pod (`witty-heron` or `nimble-shrew`, both on `2026.5.7`), carrying
   `x-openclaw-model: openrouter/anthropic/claude-haiku-4.5`. It is the only thing standing
   between "derived from upstream's own source and docs" and "measured". Everything in §3.2
   rests on it, and W5 is dead without it. **Recommendation: yes — and it should happen
   before PR 4 is written, not after it is merged.**
1. **Given §2, W5 (per-thread) is buildable — confirm you still want it.** The verdict makes
   it cheap (a header, no roll), but it is still the largest item, and if W-PROBE comes back
   negative it degrades to "apply and restart, for all threads" (§3.3), which is a materially
   different feature from the one described. **Recommendation: ship PRs 1–3 regardless;
   decide W5 after W-PROBE.**
2. **Should the thread-scoped model reach `/agents/{name}` and `/operator` too?**
   `agentChatPane` is shared, so it is nearly free — but it widens the blast radius of a
   feature requested for one surface. **Recommendation: chief panel only, first.**
3. **Any cost ceiling?** The design lets a mis-click select the most expensive model in the
   catalogue as chief's standing default, with no confirm and no cap (§11).
   **Recommendation: no cap in v1, but the picker's curated list should lead with the
   models you actually want** — `curatedModels()` (`internal/ui/agents.go:928-949`) is
   where that is decided, and it currently leads with two Anthropic models plus
   `ModelOptions`.
