# Handoff — muse system: full inventory, evaluation and ranked fixes

**Date:** 2026-10-01/02 · **Scope:** the whole Meta Muse integration across two repos
**Method:** four parallel subagents (one security audit + three inventory legs), every
load-bearing claim re-verified by the orchestrating session against the file or the ref.
**State: NOTHING WAS CHANGED.** No commits outside this document, no cluster mutations.

---

## 0. 🔴 Incident — an unauthorized message was sent to the live Muse account

A subagent explicitly briefed **not to send** sent one anyway. Root cause is the
`claude/RULES.md` → "zsh is NOT bash" trap, in its (a) form.

The agent built a stub environment as a **string** and interpolated it unquoted:

```bash
E="XDG_STATE_HOME=… MUSE_BB=/bin/false MUSE_CLI=<stub>"
env $E bash $M send "a task"     # zsh does NOT word-split $E
```

zsh passed `$E` to `env` as a **single argument**, so none of the three overrides
applied and the probe battery ran against the real browser-bridge and the real
`muse-cli`. Of four probes, three failed harmlessly; one completed a **real B1 send**.

- Task text sent: the literal string **`a task`** — no secrets, no hostnames, nothing
  client-identifying. Low-stakes, but a real message on Meta's wire.
- **Independently corroborated, not taken on the agent's word:** the browser-flow leg,
  running separately, measured `~/.local/state/muse/last-send` as *16 minutes old* at its
  own start — something had sent before it. Two real sends happened on 2026-10-01; one
  was authorized.
- **Residue:** the agent's follow-up `poll` was killed by a tool timeout, so its `EXIT`
  trap never fired. **One stray `https://muse.ai` background tab on instance `personal`**,
  opened ~22:34–22:36 CDT. It correctly did not attempt cleanup — `browser close` from a
  fresh session closes the *active* tab, which is the operator's. A second leg separately
  counted **5 orphan muse.ai tabs** that were not its own, so this is a standing leak
  (§3, bug B6), not a one-off.

**Rules for dispatching agents against muse — adopt these before the next fan-out:**

1. **Build subprocess env as a dict / `env -i K=V K=V`, never as an interpolated string.**
   Under zsh the string form silently becomes one argument and every override vanishes.
2. **Grant send authority to exactly ONE agent per fan-out**, and say so in every sibling's
   brief. "Do not send" is not sufficient on its own — pair it with stubs the agent cannot
   accidentally bypass.
3. **Assume any agent touching the wrapper may send.** The only structural defence is a
   stub binary on `MUSE_BB` whose absence is detectable (write a marker file, assert it).
4. **Budget for tab residue.** A killed agent cannot run its trap. Expect orphans and
   sweep them deliberately with the operator present.

---

## 1. System map — three layers, two repos

| Layer | Location | Size | Deployed how | Tests |
|---|---|---|---|---|
| Skill (routing/doctrine) | `devrc:claude/skills/muse/SKILL.md` | 120 ln | nix store copy — **needs a `switch`** | none |
| Wrapper (the verbs) | `devrc:scripts/muse/muse` | 454 ln / 20 KB bash | ❌ **not referenced in `nix/` at all** | ❌ **none** |
| Browser flow B1 | `devrc:scripts/browser-bridge/flows/muse.ai.md` | 112 ln | `mkOutOfStoreSymlink` — live, no switch | none |
| Flow routing | `devrc:scripts/browser-bridge/flows/_index.json:22` | — | same | — |
| Bridge service | `homelab-talos:containers/muse-bridge/` | ~830 ln Go | Flux (GitOps) | `main_test.go`, 5 tests, **18.1 %** |
| Bridge app | `homelab-talos:clusters/homelab/apps/muse/` | 8 files | Flux | — |
| **Monitoring** | `homelab-talos:clusters/homelab/apps/muse-monitoring/` | **7 files** | Flux | `prometheus-rules_test.yaml` |
| Public exposure | `homelab-talos:clusters/production/apps/nebula/gateway/muse-bridge-ingress.yaml` | 63 ln | Flux | — |

`muse-monitoring/` was **not in any brief** — it landed ~1 h before the audit and
materially changes the observability answer (§4). Found by a leg widening its own scope.

**Channels.** B1 browser flow (primary), B2 `muse-cli` (behind `--cli`, blocked on Brave
remote-debugging — confirmed live: `auth error: no cookies`), and channel A, the read-only
cluster status via `muse-bridge.zacx.dev`.

**Routes** (`main.go:93-99`): `/v1/health` **unauthenticated** (it is the probe target),
then `nodes`, `pods`, `events`, `workloads`, `flux`, `/openapi.yaml` behind `s.auth(...)`.

### Deployment gap

`git grep -i muse -- nix/` returns **nothing**. The wrapper has no `home.file`, no
`mkOutOfStoreSymlink`, no package, while every sibling tool is declared
(`nix/home.nix:1550, 1750, 3179, 3187`). `command -v muse` → not found.

**So the skill ships to both hosts and auto-fires (tier A), while the tool it instructs you
to run exists only as a repo file.** On the workbench it is worse than merely absent:
`b1_open` hardcodes `B1_REF="bw://laptop/$B1_INSTANCE/$tab"` (`muse:119`) while opening the
tab on the *local* bridge (`:109`) — so from the workbench every post-open op dispatches
over SSH **to the laptop** against a tab id that belongs to the workbench. The skill is
host-portable; the wrapper is not.

---

## 2. What is verified WORKING

- **The full B1 round-trip, end to end.** One authorized send (`What pods are running in
  ns muse?`, no `--force`) returned a correct answer naming a running pod with restart
  count, age and node. Every hop confirmed: composer → Enter → Muse → `custom.homelab-bridge`
  connector → public bridge → cluster → reply.
- **Zero selector drift.** All 14 documented selectors still resolve; `form`=0 and
  `button[type=submit]`=0 confirm Enter-only submission. The 2026-09-29 map is accurate.
- **The read-only guarantee holds at the RBAC layer, not just in code.** Measured:
  `create/delete/update/patch pods` → `no`; `get secrets` → `no`; `watch pods` → `no`.
- **Pod hardening is complete and matches the manifest on every field** — distroless-nonroot,
  static `CGO_ENABLED=0 -trimpath` build, `runAsNonRoot`, `readOnlyRootFilesystem`,
  `drop: [ALL]`, `allowPrivilegeEscalation: false`, `RuntimeDefault` seccomp, both probes,
  resource limits. **Zero manifest-vs-pod drift.**
- **Flux is live and in sync** — `kustomization/muse` not suspended, `interval: 5m`,
  `lastAppliedRevision` == GitRepository artifact == `git ls-remote origin trunk`. All three
  agree: a reconciled system, not an orphaned manifest.
- **No injection surface in the wrapper.** All four `bb_js` call sites pass single-quoted
  literal JS; task text never enters JavaScript — it goes as **argv** to `browser type`.
  The JSON reply is built by `python3` `json.dumps` over argv. No hand-rolled JSON anywhere,
  and the code comments show this was deliberate.
- **No injection or traversal surface in the bridge.** `ns` is `^[a-z0-9-]{1,63}$`, so no
  `/`, `.` or `%` reaches client-go's URL builder; all traversal probes 404'd.
- **Token is never logged, echoed or returned** by the service; errors are fixed strings.
- **The new blackbox monitoring works, with both controls run**: positive `probe_success 1`
  / `probe_http_status_code 200` in 0.40 s against the real public URL; negative (bogus
  path) `404` / `probe_success 0`. It probes the **public** URL deliberately, so it
  traverses the full hairpin, and it explicitly refuses to promote `probe_success` into a
  readiness gate (that would convert a loud alert into a silent absent-scrape).

---

## 3. Findings — ranked, with evidence

Severity is this document's own synthesis across four agents. `✓` = re-verified by the
orchestrating session against the file or ref, not merely relayed.

### CRITICAL / HIGH — security

| id | finding | evidence |
|---|---|---|
| **S1** ✓ | **Public origin bypasses Cloudflare; the bearer token's transport is not authenticated end-to-end.** `muse-bridge-ingress.yaml:27-29` lists `entryPoints: [web, websecure]` and the file contains **no `tls:` block**. Origin `5.161.19.116` answers cleartext HTTP on :80 (measured 200) and presents a self-signed `CN=TRAEFIK DEFAULT CERT` on :443. The origin IP is published in the repo. So edge TLS, WAF and rate limiting are all bypassable, and the one long-lived token granting cluster-wide read crosses CF→Hetzner in plaintext or under an unauthenticated cert. ⚠ **This is the house pattern** — `muster-ingress.yaml:24-26` is identical; it bites harder here because muse is the one route with no Authelia. | `muse-bridge-ingress.yaml:21-37`; measured curl/openssl |
| **S2** ✓ | **A guard the design mandates and the code does not implement.** Design §3.2: *"`ns` must match `^[a-z0-9-]+$` **and must be in a small allowlist or default to 'all-namespaces summary only'**"*. `nsFrom` (`main.go:212-216`) does only the regex — grepped `*.go`, no allowlist exists. RBAC is ClusterRole-wide, so no server-side scoping behind it either. **57 namespaces live.** Namespace scoping is convention only, enforced nowhere. | `main.go:212-216` vs `muse-agent-integration-design.md:112` |
| **S3** ✓ | **The bearer token is readable from `ps` by any local user.** `muse:387` passes `-H "Authorization: Bearer $token"` as curl **argv**. Measured on this host: `/proc` mounted **without `hidepid`**, `/proc/<pid>/cmdline` mode `-r--r--r--`. For up to 15 s per `muse status`, any local process under any user can read the same cluster-wide-read token as S1. One-line fix: `curl --config -` / `-H @-` on stdin. | `muse:387`; `/proc/mounts`, `ls -l /proc/self/cmdline` |
| **S4** | **`/v1/nodes` does an unbounded cluster-wide PodList from etcd on every call.** `handlers.go:17,55` — `ListOptions{}` with no `Limit` and **no `ResourceVersion: "0"`**, so every call is a quorum read, not the watch cache. 837 pods live × 30 req/min permitted, from the public internet, for an endpoint emitting six fields per node. Most plausible cause of the recorded 256Mi OOMKills. | `handlers.go:17,55` |
| **S5** | **RBAC over-grant**: `pods/log`, `services`, `namespaces`, `replicasets` granted, no handler reads any. Measured `get pods/log` → **yes**. Application logs across 57 namespaces routinely carry tokens and PII; one accidentally-merged `/v1/logs` handler away from a cluster-wide secret read. The file's own comment claims least privilege it does not have. | `rbac.yaml:16,19`; `kubectl auth can-i` |

### HIGH — correctness bugs in the wrapper

Found **independently by two agents**, then re-verified here.

| id | finding | evidence |
|---|---|---|
| **B1** ✓ | **The Enter-retry check is dead code — it can only ever fail.** `muse:227` requires `[ "$COMPOSER" = "no" ]`, i.e. the textarea element *gone*. `COMPOSER` comes from `c:!!t` (element exists). The comment three lines above (`:216-218`) records the measured fact: *"the element itself always stays mounted — checking its existence is how Enter #1 was misread as inert."* That fix was applied to the first check (`:219` uses `TALEN`) and **not** to the retry's. **Any send taking the retry path dies claiming "Enter was inert twice" even when the retry succeeded.** A half-applied fix. | `muse:227` vs `:216-219` |
| **B2** ✓ | **The approval guard is broken in both directions — and it is the wrapper's only security branch.** `muse:92` scans `document.body.innerText` for `"Needs approval"` / `"Needs review"`. Neither string appears in the approval UI that SKILL.md `:87-89` and `muse b1` `:432-435` document (*"Allow Muse to access &lt;host&gt;?"*, *"Review the approval request in chat to continue"*). Measured live: all four strings absent — so the guard has **never been positive-controlled**. And `body.innerText` includes the whole transcript: **2 existing "approv" hits were measured in prior message bodies**, so a chat *about* approvals is one phrasing from hard-exiting 5 on every invocation, permanently. It also violates the flow file's own rule (`:45`: *"select on attributes, never on label text"*) on a mixed Spanish/English UI. | `muse:92`; live DOM measurement |
| **B3** ✓ | **`USER` is clobbered and exported.** `muse:101` reads the user-message *count* into `$USER`, the login-name env var — and assigning to an exported name keeps the export. `b1_state` runs up to a dozen times per send, so **every subsequent bridge child process inherits `USER=<count>`**. Reproduced: `USER=zach` → `USER=12`. | `muse:101` |
| **B4** ✓ | **Host hardcoded in the tab ref** — `muse:119` `bw://laptop/…`. Works here by luck. ⚠ **One agent's proposed fix was wrong**: it claimed shell `whoami` reports `laptop`; measured `whoami`=`zach`, `hostname`=`nixos`. The correct source is the bridge's own `browser whoami` (which does resolve this host as `laptop`). | `muse:119`; measured |
| **B5** | **Length assertion compares bash characters to JS UTF-16 units** (`:207`). `a😀b` is 3 in bash, 4 in JS — any emoji makes the assertion fail twice, typing the text **twice** into the live composer, then dying with a misleading "composer did not take the typed text". BMP accents are fine. | `muse:207` |
| **B6** | **Tab leaks on every post-type failure.** `trap - EXIT` is cleared at `:211`, before Enter; from there only the success path closes the tab. The 180 s timeout at `:244` leaks **silently** (other `die`s at least say "Tab left open"). No `trap … INT TERM`, so a killed run always leaks. Corroborated: 5 orphan tabs measured. | `muse:211,244` |
| **B7** | **Multi-word tasks silently truncated on B1.** `:159`/`:166`: `muse send hello world` → `task=hello`, `wait=world`. The `--cli` path joins correctly — the two paths disagree. | `muse:159,166` |

### HIGH — the pacing gate does not hold against an agent loop

Exercised directly, 10 cases on a scratch state dir; mechanism re-verified at `muse:126-141`.

- ✓ **Fails OPEN on a non-numeric gap.** `MUSE_MIN_SEND_GAP_MIN=10m` → `[: integer expected`
  on stderr, then **allowed**: a failing `[` inside an `if` is simply false, and errexit
  exempts conditions.
- ✓ **`pace_check` reads mtime; `pace_mark` writes an epoch into the file that nothing ever
  reads.** The contents are decoration. A restore preserving content but not mtime silently
  resets the gate.
- **`--force` is broken on `--cli`** — consumed at `:152`, never forwarded at `:158`, and
  `cmd_send_cli` re-initialises `force=0` and re-runs `pace_check` (`:278,289`). So
  `send --force --cli` is paced anyway, the opposite of documented.
- 🔴 **`pace_mark` fires only after the reply poll** (`:248`). A send that *submits
  successfully* then times out (`:244`, exit 1) **never stamps** — so a retry loop catching
  exit 1 re-sends immediately with the gate silent. **This is the agent-loop spam path.**
- Bypasses beyond `--force`: gap=0, any non-numeric gap, `touch`/`rm`/`mkdir` over the
  stamp, a different `XDG_STATE_HOME`. A `last-send` *directory* fails open entirely.

**Verdict:** against an honest single caller it holds. Against an agent loop it does not.
The wrapper's own header (`:20`) calls it *"a reminder, not a rate limiter"* — accurate.
**SKILL.md:38's "The wrapper enforces a ≥10 min gap" overstates it.**

### HIGH — three sources of truth, already diverged

The 2026-10-01 knowledge **never reached the flow file**, which is the file SKILL.md, the
wrapper's `b1_hint` (`:66`), `muse b1` (`:439`) and `muse setup` (`:399`) all cite as
authoritative — and `b1_hint` says to follow it *"exactly"*.

| 2026-10-01 knowledge | SKILL.md | `muse b1` | flow file |
|---|---|---|---|
| Approvals gate the composer / Enter inert / exit 5 | ✓ `:86-91` | ✓ `:432-436` | **ABSENT** |
| Feed re-keys nodes — poll id+len, **never count** | ✓ `:92-94` | ✓ `:415-416` | **CONTRADICTED** `:75` |
| Composer mounts before the feed hydrates | ✗ | ✗ | **ABSENT** (only a wrapper comment) |
| Re-throttled tab reads as silence | ✓ `:94` | ✓ `:437` | ✓ `:86` |

**The contradiction is measured, not theoretical:** the assistant count stayed **flat at 16**
across a send that added a reply (the feed virtualizes and re-keys). `muse.ai.md:75` still
instructs polling on *"asst **count** + id + innerText len"* and `:66` gates submit on
*"user count above the pre-send value"*. A hand-driver following it builds a count-based
check that silently never fires. Separately, `muse b1` **duplicates** the recipe inline
rather than citing the flow file — which is how the two diverged.

### MEDIUM

| id | finding |
|---|---|
| **M1** | **Three outcomes the caller cannot distinguish**, all exit 0 with `"sent": true`: a complete reply, an **empty** reply (`cmd_poll_b1` guards this at `:263`; `cmd_send_b1` does not), and one **truncated at the 180 s deadline**. Measured poll cadence is **12.7 s**, not the nominal 8 s — so **any generation stall ≥12.7 s fires stable-twice and silently truncates**, exactly the shape of a connector call (render → pause for network → resume). This run did not hit it only because the reply was complete before the first poll, so **the stability heuristic was never actually exercised**. |
| **M2** | **More indistinguishable pairs:** tab-closed-mid-poll ≡ Muse silence (the poll loop swallows `b1_state` failures with a bare `continue` and **never re-wakes**, unlike the pre-send path, then burns the full 180 s); not-logged-in ≡ feed-never-hydrated (no auth check anywhere in B1). |
| **M3** | **`status` returns exit 0 on HTTP 401/404/5xx** — curl succeeded, the code is only printed. SKILL.md `:102` documents the codes as prose, but a caller branching on exit status reads an auth failure as success. Deliberate per `:390`, but undocumented as a consequence. |
| **M4** | **Exit-code collapse:** `auth` flattens every muse-cli failure to 3; `send --cli` flattens all but 3 to 1; `poll --cli` flattens even 3 to 1. And **exit 2 means both "usage error" and "paced, try later"** — an automated caller cannot tell them apart. |
| **M5** | **`openapi.yaml` describes none of the actual response shapes.** All five data routes return `$ref: ItemList`, whose `items` is `type: array, items: {type: object}` — generic and untyped. The five real item schemas (`nodeInfo`, `podInfo`, `eventInfo`, `wlInfo`, `fluxInfo`) are defined nowhere. Muse evidently inferred them from live JSON, which means **the spec is not the contract — the live response is**, and it can change without the spec moving. Also: **500 is undocumented on every path** (8 distinct 500s exist), 404/405 undocumented, and `truncated` is `omitempty` so it is *absent* rather than `false`. |
| **M6** | **No HTTP server timeouts and no graceful shutdown.** `http.ListenAndServe` (`main.go:104`) leaves `ReadTimeout`/`ReadHeaderTimeout`/`WriteTimeout`/`IdleTimeout` all zero — pre-auth slowloris against `replicas: 1`. No `srv.Shutdown()`, so every rollout cuts in-flight requests. No PDB: a node drain is a hard outage. |
| **M7** | **Arbitrary apiserver text is relayed verbatim to a third-party LLM.** Event `Message` and Flux condition messages pass through at 300 B (`handlers.go:193-206`, `:383-393`). These are free-form strings from kubelet/controllers/Helm that routinely embed mount paths, secret *key names* and registry auth failures. `openapi.yaml:6-8` claims responses are curated *"never env vars, annotations, or secrets"* — the claim does not hold for the one field it explicitly permits. |
| **M8** | **Rate limiting is post-auth only** (`main.go:120-125` returns 401 before `limit.allow`), so wrong tokens cost an attacker nothing and the only defence against brute force is token entropy. No Traefik `rateLimit` middleware on either IngressRoute. |
| **M9** ✓ | **The homelab IngressRoute is not in the request path, and its comment says the opposite.** The last hop proxies **straight to the Service** — `nginx.conf:1205-1207` sets `upstream "muse-bridge.muse.svc.cluster.local:80"`, bypassing homelab Traefik. But `clusters/homelab/apps/muse/ingressroute.yaml` is deployed and its header asserts design §3.3 Option 2, which the handoff doc **formally retracted**: *"'the existing homelab public path' was a FALSE PREMISE … the homelab public IP does NOT answer on 443."* Dead weight carrying a disproved premise. |
| **M10** | **Two monitoring files misstate the last hop** — `prometheus-rules.yaml` says *"→ homelab Traefik"*, `configmap.yaml` says *"→ Traefik IngressRoute"*. Both contradict `nginx.conf:1205-1207`. **This is inside the `MuseBridgePublicPathDown` triage runbook**, so it misdirects an operator during an incident. |
| **M11** ✓ | **`imagePullPolicy: IfNotPresent` on the mutable tag `0.1.0`.** Rebuilding and pushing `0.1.0` would not be picked up on a node with that layer cached — **the deploy reports success and runs the old binary.** Any rebuild must bump the tag. |
| **M12** ✓ | **Nothing automated builds or tests the bridge image.** No Tekton pipeline, build script or Makefile for `containers/muse-bridge/**` — confirmed against `origin/trunk`. Sibling `containers/clawgate/**` *does* have `clawgate-ci-pipeline.yaml` (go build/vet/test -race -cover, firing on any branch). muse-bridge's 5 tests run **only when a human types `go test`**. The build/push step is recorded nowhere in the repo. |

### LOW

Internal Harbor FQDN + full app inventory disclosed in responses · O(n²) `writeCapped`
truncation · nil deref on `*Spec.Replicas` (unreachable in practice) · Host-only ingress
match (no `PathPrefix`) · no token rotation overlap window · `curl` query params not
URL-encoded (`muse:383`) · `b1_hint`/`cmd_b1` print a **literal** `~/workspace/devrc/…`
path rather than `$MUSE_BB`'s value · the composer selector appears **six times** in two
spellings (`muse:92,203,206,209,213,222`) so one selector change needs six edits · `muse
status <typo>` silently reinterprets the word as a namespace for `pods`, returning an empty
list rather than an error · nonexistent namespaces return `200 {"items":[],"count":0}` so a
typo is indistinguishable from an empty namespace · base images pinned by tag not digest
(build not reproducible) · unused `apk add git` in the Dockerfile.

---

## 4. Observability: availability yes, usage no

`muse-monitoring/` (new, ~1 h before the audit) adds a blackbox exporter, a ServiceMonitor
and two alerts — `MuseBridgePublicPathDown` (5 m) and `MuseBridgeProbeMissing` (30 m,
alerting on *blindness*, explicitly motivated by a 2026-09-02 six-day silent outage). The
manifests are unusually well-reasoned and enumerate their own blind spots. **Both controls
were run and pass** (§2).

**But it answers availability, not usage.** The probe is green whether Muse calls hourly or
never. And:

- 🔴 **The audit log is ~99 % probe noise** — `/v1/health 200 bytes=16` at ~6/min for 2d5h,
  with **~33 non-health lines total**. You can find a connector call only by
  `grep -v route=/v1/health`.
- 🔴 **There is no way to tell *who* called.** No client IP, no User-Agent, **no token-id**
  — despite design §3.2 requiring one. Demonstrated in the log itself: a Muse connector
  sequence at 2026-10-01 20:44 and a sibling audit agent's 401/traversal burst at
  2026-10-02 03:22 are **indistinguishable**. Attribution of the 20:44 calls came from the
  handoff doc, not the logs. The relay sets `X-Real-IP`/`X-Forwarded-For` at both nginx
  hops — **the client IP arrives at the pod and is thrown away.**
- **No `/metrics`.** No request counters, no latency histogram.

This directly undercuts the previous handoff's ranked next step #1 (*"Monitor first real
Muse dispatch usage — bridge audit logs, 429 rate-limit hits, memory"*): **two of those
three are not currently measurable.**

---

## 5. Test coverage

| Surface | State |
|---|---|
| Wrapper (454 ln bash) | **Zero.** `find … -print0 \| xargs -0 grep -il muse` over `scripts/` → no matches, with a working positive control. |
| Flow file / B1 JS | **Zero.** |
| SKILL.md body | Zero (the tier-ledger *entry* is pinned two-way by `test_skill_tiers.py`). |
| Bridge | 5 tests, all pass, `go vet` clean, **18.1 % of statements**. |

**Guarantees asserted in docs or comments that NO test enforces:**

- 🔴 **Data minimization — the load-bearing claim of the whole design — is untested.**
  `main.go:5`: *"It NEVER serves env vars, annotations, secrets, or raw YAML."*
  `openapi.yaml:6-8` repeats it to the consumer; design §6 rests the entire "cluster data
  leaks out of Muse" risk row on it. **No test asserts any response's field set.** A field
  added to `podInfo`/`nodeInfo` — or a struct swapped for the raw corev1 object — ships
  silently into Meta's inference context. ~20 lines closes it.
- **"It never logs the token"** (`main.go:163`) — comment only.
- **The 16 KB cap on real handler output** — `writeCapped` is tested only against a
  synthetic fixture; no handler is ever exercised through it.
- **Spec/route agreement** — nothing asserts the embedded `openapi.yaml` matches the routes
  registered at `main.go:93-99`. They have already drifted (M5).

**Two guards narrower than their own names** — the shape where a reader stops looking:

- 🔴 `TestLimiterBurstThenRefill` **never tests refill.** It drains the bucket and asserts
  the 31st is denied; it never advances the clock and never asserts a token comes back. The
  refill arithmetic (`main.go:60-63`, including the `tokens > perMin` clamp) is completely
  unexercised.
- 🔴 `TestRouterWhitelist` builds its **own** `ServeMux` registering only `/v1/health`, so
  it proves nothing about the real route table. **A route registered without `s.auth(...)`
  would be caught by no test.**
- Minor: `TestNsPattern:12` **pins `-x` as valid** — an RFC1123-invalid namespace — actively
  asserting the laxness is intended rather than flagging it. (Measured harmless: kube
  returns an empty list, not an error.)

Structurally uncovered: all five data handlers (~300 of `handlers.go`'s 412 lines, no fake
clientset), the 400-on-bad-ns path, event/condition truncation, `humanAge`, `memGi`, the
flux unstructured walkers, `handleOpenAPI`.

---

## 6. Ranked next steps

**Tier 1 — one-liners, no deploy, high value**

1. **B1** — `muse:227` → `[ "$TALEN" -eq 0 ]`. The retry path currently always fails.
2. **S3** — `muse:387` → `curl --config -` (or `-H @-`) on stdin. Stops leaking the token to `ps`.
3. **B3** — rename `USER` → `UMSGS` (`:101` + readers `:179,184,219`, `base_user` `:202`).
4. **Pacing** — move `pace_mark` to fire on **confirmed submit** (`:228`), not after the reply poll (`:248`). Single highest-value change against the agent-loop spam path.
5. **Pacing** — fail **closed** on a non-numeric `MUSE_MIN_SEND_GAP_MIN` (validate after `:34`).
6. **Docs** — fix SKILL.md `:61`'s `--cli` inversion (it says B1, it is B2); soften `:38`'s "enforces"; correct `:84`'s "~10–25 s" to the measured ~75 s wall.
7. **`usage()`** — delete the phantom `poll --wait` (`:47`), which is advertised and never parsed.
8. **M10** — correct the last hop in the two monitoring comments; it sits inside an alert runbook.

**Tier 2 — small, correctness**

9. **S2** — `ns` allowlist in `nsFrom` + a test that `?ns=kube-system` → 400. Pure code, no cluster risk, closes the widest exposure. *(Requires rebuild + tag bump.)*
10. **Flow file re-map** — strike `count` from `:66`/`:75`, add the approval and hydration gates, re-date. Make `muse b1` **cite** the flow file instead of duplicating it (one rule, one place).
11. **B2** — rebuild the approval guard **structurally**: branch on a `data-message-*` attribute, or at minimum scope the text scan to the composer/toast container, never the transcript. Needs one live approval card to map.
12. **M1/M2** — emit `"stable": true/false`; port `cmd_poll_b1`'s zero-length guard into `cmd_send_b1`; re-wake inside the poll loop instead of a bare `continue`; close the tab on the timeout path.
13. **B4** — derive the host from `browser whoami`, not the `laptop` literal.
14. **Golden field-set test** for the five bridge item structs — pins the design's central guarantee. ~20 lines, no deploy.
15. **Fix `TestLimiterBurstThenRefill`** to actually test refill; make the route table testable via `func (s *server) routes()` and assert every registered pattern except `/v1/health` returns 401.

**Tier 3 — larger, operator-gated**

16. **S1** — drop the `web` entrypoint, add `certResolver: letsencrypt`, add a Cloudflare-only `IPAllowList` middleware; set CF SSL mode to **Full (strict)**. ⚠ Decide whether to fix the **class** (`muster-ingress.yaml` is identical) or just muse. The dashboard half is the operator's.
17. **S4** — `ResourceVersion: "0"` + `Limit`, and a ~30 s cache on `/v1/nodes`; or drop the pod-request rollup entirely.
18. **S5** — delete the four unused RBAC grants; if the allowlist lands, convert to per-namespace RoleBindings keeping ClusterRole only for `nodes`.
19. **M6** — real `http.Server` timeouts + `signal.NotifyContext`/`Shutdown`; `replicas: 2` + a PDB.
20. **M12** — a Tekton pipeline for `containers/muse-bridge/**` modelled on `clawgate-ci-pipeline.yaml`; closes "nothing tests it" and "nothing builds it" at once.
21. **M5** — fill in the five response schemas and the 500/404/405 responses in `openapi.yaml`.
22. **Audit log** — add `client=` (XFF already arrives) and `tokid=` (sha256 already computed at `main.go:124`). ~3 lines; makes usage attributable and satisfies design §3.2.
23. **Nix-deploy the wrapper** as a `mkOutOfStoreSymlink` into `~/.local/bin/muse`, beside `dl-route` (`home.nix:3179`) and `stt` (`:3187`). 🔴 **Sequencing matters: ship B4 and the pacing fix FIRST** — otherwise this puts a laptop-only tool with a leaky gate onto both hosts' `$PATH`. The wrapper is already tracked, so only `home.nix` changes.
24. **Extract the B1 JS** to `scripts/muse/b1.js` and fixture-test it under the existing `node` gate tier. The regression test that would have caught **B2** is concrete and **fails against today's code**: a transcript containing the literal "Needs approval" in a message body must not set the approval flag.

---

## 7. Honesty ledger

**Measured:** the full B1 round-trip with per-stage timestamps; all 14 selector counts and
the flat assistant count across a send; 10 pacing-gate cases; `/proc` mount options and
`cmdline` mode; exported-`USER` propagation; bash-vs-UTF-16 string lengths; unauthenticated
status for every route plus traversal/case/slash variants; `kubectl auth can-i` for read and
write verbs; manifest-vs-pod on every security-relevant field; Flux revision agreement
across three sources; `go test` (5 pass, 18.1 %) and `go vet`; blackbox probe positive and
negative controls; 837 pods / 57 namespaces; the `muse-cli` cookie failure.

**Read, not measured:** all authenticated bridge-route behaviour (no agent held the token,
correctly) — so **S2's impact is a code-reading finding**; confirming it needs one authed
`GET /v1/events?ns=kube-system`. Also the production/nebula hops 3–6 (manifests only), and
the Enter-retry bug B1 (deriving a second send was declined).

**Could not verify:** Cloudflare's SSL/TLS mode (decides whether S1's leg is plaintext or
merely unauthenticated TLS — **S1 holds either way**); whether any CF WAF rule exists; the
image build/push step (**nothing in the repo records it** — the "hand-built" reconstruction
is inference from the absence of automation); `govulncheck`; whether the deployed digest was
built from the committed Dockerfile; whether the pacing gate *blocks* (the gap had elapsed);
**the approval guard against a real approval card — no card was pending, and this is the
single most important thing to test next time one appears**; the stability heuristic against
a *growing* reply; whether the stray muse.ai tab is still open.

**One agent claim corrected here:** the proposed fix for B4 cited shell `whoami`; measured
`whoami`=`zach`. The bug stands, the fix source is `browser whoami`.

**One earlier relay corrected:** `poll --wait` was reported as "in the wrapper, absent from
SKILL.md". It is advertised in `usage():47` and **never parsed** — a wrapper help bug, not a
doc omission.
