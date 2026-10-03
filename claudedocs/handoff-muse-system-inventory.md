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
| **S1** ✓ | **Public origin bypasses Cloudflare; the bearer token's transport is not authenticated end-to-end.** `muse-bridge-ingress.yaml:27-29` lists `entryPoints: [web, websecure]` and the file contains **no `tls:` block**. Origin `<hetzner-origin-ip>` answers cleartext HTTP on :80 (measured 200) and presents a self-signed `CN=TRAEFIK DEFAULT CERT` on :443. The origin IP is published in the repo. So edge TLS, WAF and rate limiting are all bypassable, and the one long-lived token granting cluster-wide read crosses CF→Hetzner in plaintext or under an unauthenticated cert. ⚠ **This is the house pattern** — `muster-ingress.yaml:24-26` is identical; it bites harder here because muse is the one route with no Authelia. | `muse-bridge-ingress.yaml:21-37`; measured curl/openssl |
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

🔴 **Numbers are ORIGINAL ranks** — Tier 1 (1–8) and rank 9 are done and
removed; survivors keep their numbers because rank is half a `claim-work`
claim's identity. Claim before acting:
`claim-work --slug-for <this doc> <rank>`.

25. **IN FLIGHT: devrc#1993** — `muse status <ns>` 400 handling. Implemented,
    tested, test-merged against #1986; PR open, NOT merged. What remains is not
    implementation: offer `/audit-pr 1993` **round 0 FIRST** (the only round
    that can conclude *close this, do not audit it*, actionable only while the
    merge decision is open), then the nine axes; read `gh pr checks 1993` for
    the 72-file scoped set this session left unresolved; merge; then
    `claim-work --release muse-system-inventory-25`. Repo: devrc —
    `scripts/muse/muse`, `claude/skills/muse/SKILL.md`,
    `scripts/tests/test_muse_wrapper.py`.
    forcing: regression — a live break introduced by #944 in the operator's own
    CLI; the fix is written but unmerged, so the break is still live.

**Security-forcing — the public exposure, operator-gated where noted**

18. **S5 — delete the four unused RBAC grants**
    (`clusters/homelab/apps/muse/rbac.yaml:16,19`): `pods/log`, `services`,
    `namespaces`, `replicasets`. No handler reads any. **Cheapest remaining
    security item AND the one that closes the arc's closing condition, second
    clause — prefer it over 16 while 16 is blocked.** Repo: homelab-infra.
    forcing: security — `pods/log` across 57 namespaces is one merged handler
    away from a cluster-wide secret read; measured `can-i get pods/log` → yes.
16. **S1 — close the origin bypass** in
    `clusters/production/apps/nebula/gateway/muse-bridge-ingress.yaml`: drop the
    `web` entrypoint, add `certResolver: letsencrypt`, add a Cloudflare-only
    `IPAllowList`.
    🔴 **BLOCKED ON THREE OPERATOR DECISIONS — do NOT start building.** Asked
    2026-10-03, unanswered at session end: **(a)** class or instance —
    `muster-ingress.yaml:24-26` is byte-identical, so muse-only leaves the
    pattern live next door while both doubles the blast radius in one change;
    **(b)** who flips Cloudflare SSL mode to Full (strict) and WHEN relative to
    the merge — Flux reconciles on a 5m interval, so there is a window either
    way and the wrong order takes `muse-bridge.zacx.dev` down; **(c)** merge it,
    or stage the PR for the operator to merge when they choose. The session's
    own recommendation, offered and not adopted: stage as a PR, muse-only,
    muster filed as its own item, operator flips Cloudflare after the merge with
    the blackbox probe watched. Repo: homelab-infra.
    forcing: security — measured: the Hetzner origin answers cleartext HTTP on
    :80 and a self-signed cert on :443; the bearer token crosses that leg, and
    it is now the token guarding a working allowlist.
11. **B2 — rebuild the approval guard structurally.** See the open investigation;
    BLOCKED on capturing one live approval card. Repo: devrc.
    forcing: security — it is the wrapper's only security branch and has never
    been positive-controlled.
19. **M6 — real `http.Server` timeouts + graceful shutdown**
    (`containers/muse-bridge/main.go`), `replicas: 2` + a PDB. Repo:
    homelab-infra.
    forcing: security — all four timeouts are zero and the origin is directly
    reachable, so pre-auth slowloris reaches a single replica.
17. **S4 — bound `/v1/nodes`**: `ResourceVersion: "0"` + `Limit`, or drop the
    cluster-wide pod rollup (`handlers.go:17,55`). ⚠ Outside the allowlist BY
    DESIGN — `nodeInfo` carries no pod or namespace identity, so not a bypass.
    Repo: homelab-infra.
    forcing: security — 30 req/min × an unbounded 837-pod etcd read, drivable
    from the public internet by one token.
22. **Audit log — add `client=` and `tokid=`** (`main.go`, the `ServeHTTP` audit
    line). XFF already arrives and the sha256 is already computed in `auth`.
    🔴 **Fix the injection in the same change:** that line interpolates the raw
    `ns` and `r.URL.RequestURI()` with `%s`, so a `%0a` in the query can forge
    log lines in the one surface used for attribution. Found 2026-10-02 while
    implementing S2, and deliberately not changed there.
    ⚠ **Sequencing:** do NOT move the audit log into per-route middleware —
    UNMATCHED requests would stop being logged (a 404 is served by
    `NotFoundHandler`, outside any per-route wrapper), and those probe lines are
    the attribution data this item exists to improve. Repo: homelab-infra.
    forcing: security — design §3.2 mandates token-id; today a Muse call and an
    audit agent's probes are indistinguishable, and either can be spoofed.
14. **Golden field-set test** for the five bridge item structs. Repo:
    homelab-infra.
    forcing: security — the data-minimization claim (`main.go:5`,
    `openapi.yaml`) is the design's central control and NO test asserts any
    response's field set.

**Gate-forcing**

20. **M12 — a Tekton pipeline for `containers/muse-bridge/**`**, modelled on
    `clawgate-ci-pipeline.yaml`, and it must PUSH (clawgate-ci deliberately does
    not). Deleting `scripts/release-muse-bridge.sh` is part of this item.
    Repo: homelab-infra.
    forcing: gate — nothing automated builds or tests the bridge, and
    `imagePullPolicy: IfNotPresent` on a mutable tag means a rebuild can deploy
    "successfully" and run the old binary.

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
## Goal

Make the Meta Muse integration correct and safe to drive from agents: the wrapper
that sends from the operator's live account, the B1 browser flow, and the
read-only `muse-bridge` cluster API exposed to the public internet.

- **closing-condition:** `check` — every item under "Ranked next steps" below is
  either merged or explicitly dropped by the operator, AND
  `KUBECONFIG=$KC_HOMELAB kubectl auth can-i --list --as=system:serviceaccount:muse:muse-reader`
  shows no verb the handlers do not use. Frozen at round 1; later audits open a
  NEW arc rather than extending this one.

## State now

- **Branch:** devrc `main`, clean (`?? nix/system/apply-networkmanager-openvpn.sh`
  is not ours). homelab-infra `trunk` untouched this session. All worktrees were
  under the scratchpad, none in the repo.
- 🔴 **RANK 25 IS IMPLEMENTED AND OPEN AS devrc#1993** —
  `fix/muse-status-400-policy-denial`, commit `16d6cf29`. **NOT merged, NOT
  deployed**, so the break is still live on both hosts.
  `claim-work muse-system-inventory-25` is HELD — release it on merge.
  Matrix **4 RED at base `5ddffdac` / 27 green at HEAD**; ⚠ only TWO of the four
  are regressions (see Gotchas). Mutation sweep **6 mutants, all KILLED by the
  intended test**, three controls each. Tested END-TO-END behind stubbed
  `curl`/`sops`, hermetic in both tiers, no `devhost-tests` entry needed. Exit
  status deliberately UNCHANGED (0 for every HTTP answer) with a guard pinning
  that contract, so M3/M4 has to change it on purpose. Rationale: the PR body.
- 🔴 **devrc#1986 `muse-cli-ripout` edits the SAME file** (retire the muse-cli
  channel, operator decision; 749-line diff, also deletes
  `scripts/devhost-tests/test_muse_cli_exit_contract.py`). **TEST-MERGED, not
  reasoned about:** `merge-tree` exit 0 (branched on the EXIT CODE, never a
  marker grep), then the real merged tree was built and the suite run there —
  **21 passed, all 11 new tests present and green** (27→21 because #1986
  removes the muse-cli tests). Safe in either merge order. ⚠ #1986 likely
  MOOTS the `--cli` half of M4 and of the `--force`/`--cli` pacing bug —
  re-read those before working them.
- ⚠ **ONE GATE IS UNRESOLVED, NOT GREEN.** `scoped-tests.sh` selected **72
  files** and was still running at session end; **no verdict was read, so none
  is claimed.** What WAS run and green: the muse suite (27), the merged tree
  (21), and **281** tests across `test_no_public_ips` / `test_no_captured_text`
  / `test_no_captured_markup` / `test_skill_descriptions` / `test_skill_tiers`
  / `test_runtime_shebangs`. **Read `gh pr checks 1993`** rather than re-running
  72 files locally.
- **RANK 16 (S1) IS BLOCKED ON THREE OPERATOR DECISIONS, not on work** — they
  are enumerated on the ranked item. Nothing in homelab-infra was touched.
- **Rank 9 (S2) is still done and live, re-measured this session:** pod
  `muse-bridge-576d566995-2r6d4`, image `0.1.1`, **0 restarts**, startup line
  `muse-bridge listening on :8080 (rate=30/min cap=16384B ns-allow=muse)`. The
  2026-10-02 22:41Z probe table stands; this session re-confirmed the pod
  identity and the allowlist only, not the whole table. 🔴 Its load-bearing
  row, if the table is ever lost: `/v1/pods?ns=kube-system` → **400
  `namespace not served`**, and that is the exact request which once returned
  the contents of `kube-system`.
- **Durable facts carried forward** (they outlive any one session):
  - 🔴 `0.1.0` and `0.1.1` carry the **SAME digest** (`sha256:0b0b98b3…`), so
    `0.1.0` no longer means "the pre-allowlist build" anywhere except a node
    still holding the old cached layer.
  - **`scripts/muse/muse` is still NOT nix-deployed** — `git grep scripts/muse
    -- nix/` is empty and `~/.local/bin/muse` does not exist (checked with
    `[ -e ]`, never `readlink -f`). Only reachable copy is
    `$DEVRC/scripts/muse/muse`; `SKILL.md` is a store copy needing a `switch`.
    Defect (23) is sequenced after rank 25 for this reason — **#1993 unblocks it.**
  - **Bridge build/push lives in `homelab-infra:
    scripts/release-muse-bridge.sh`** (stopgap; rank 20 is the fix). No git
    writes — the Deployment bump goes via worktree + PR, per PR 925.
  - **CI:** `tekton/gitops-validate` is the COMPLETE expected status set for
    homelab-infra and has **no Go leg**; the 18 bridge tests run only when a
    human types `go test`. `go1.26.8` is on `$PATH` directly, so they run as
    `go test -C <dir> ./... -count=1 -race` — devrc's `gate.sh`/`scoped-tests.sh`
    know nothing about homelab-infra.
  - **No clawgate task, and that is NOT a clean bill of health:**
    `clawgate_handoff.sh resolve` exited **5** again (fourth time this arc). An
    unknown session id answers 200 with an EMPTY ARRAY, so the zero cannot
    distinguish "touched no task" from "wrong id". No `clawgate-task:` field
    written, and no task was created — `/handoff` records, it does not mint.

## Open investigations — live diagnosis state

### The B1 approval guard has never been positive-controlled and is wrong in both directions
- as-of: 2026-10-02

- **Symptom + exact repro:** `scripts/muse/muse`'s `b1_state` sets `APPROVAL` by
  scanning `document.body.innerText` for `"Needs approval"` / `"Needs review"`.
  Exit 5 ("operator approval pending") is the wrapper's only security branch.
  Repro of the false-positive half: have any muse.ai message body contain the
  literal string `Needs approval`; every subsequent `muse send` hard-exits 5.
- **Observed (with values):** measured live on the muse.ai feed 2026-10-01 —
  `has_needs_approval: false`, `has_needs_review: false`, `has_allow_muse: false`,
  `has_review_the_approval: false`. The strings the UI actually emits, documented
  in `claude/skills/muse/SKILL.md` itself, are `"Allow Muse to access <host>?"`
  and the toast `"Review the approval request in chat to continue"` — **neither is
  what the code matches**. Separately, **2 occurrences of `approv`** were measured
  in the existing transcript, inside prior message bodies discussing connector
  approval modes.
- **Ruled out:** "the guard works and simply had nothing to detect" — the strings
  it matches appear nowhere in the documented UI or the live DOM, so a pending card
  could not trip it. `via: measurement`.
- **Ruled out:** "scoping the text scan is enough" — the flow file's own rule is
  `select on attributes, never on label text` (`flows/muse.ai.md:45`), because the
  UI is mixed Spanish/English. `via: doc`.
- **Leading hypothesis:** the guard must branch on a `data-message-*` attribute on
  the approval card, not on words. The attribute name is unknown because no
  approval card has been observed since the flow was mapped.
- **Next probe:** the next time an approval prompt appears, BEFORE clicking Allow,
  capture the card's attributes:
  ```bash
  BB=$DEVRC/scripts/browser-bridge/browser
  $BB --instance personal open https://muse.ai --wake=4000   # your OWN tab
  $BB bw://<host>/personal/<tabId> js '(function(){var o=[];document.querySelectorAll("[data-message-item],[data-message-role]").forEach(function(n){var a={};for(var i=0;i<n.attributes.length;i++){var x=n.attributes[i];if(x.name.indexOf("data-")===0)a[x.name]=x.value}o.push(a)});return JSON.stringify(o)})()' --wake=3000
  ```
  🔴 Do NOT click Allow to produce one — approving is the operator's gate.

### 🔴 I pushed over a LIVE image tag while testing the guard that exists to prevent it
- as-of: 2026-10-02

- **Symptom + exact repro:** `scripts/release-muse-bridge.sh 0.1.0` was run to
  exercise its *refusal* path. It did not refuse — it built and **pushed over the
  live `harbor.homelab.lan/muse/muse-bridge:0.1.0` tag**, which is the exact
  `imagePullPolicy: IfNotPresent` hazard (M11) the guard was written against.
- **Root cause — an instrument that fails OPEN, never validated.** The guard was
  `if docker manifest inspect "$REF" >/dev/null 2>&1; then die ...`. That
  subcommand does **not** use the docker daemon's trust store, and Harbor serves
  a self-signed cert, so it cannot reach this registry **at all** and exits
  non-zero for *every* tag. The "tag does not exist" branch was therefore
  unconditional: the guard could never fire, for any input.
- **Observed (with values):** measured 2026-10-02 on this host —

  | tag | `docker manifest inspect` | `docker buildx imagetools inspect` | `docker pull -q` |
  |---|---|---|---|
  | `0.1.0` (exists) | rc=1 | rc=1 | **rc=0** |
  | `0.9.9` (absent) | rc=1 | rc=1 | **rc=1** |

  Only `docker pull` discriminates, because it goes through the daemon.
- **Blast radius, measured, not inferred:** the running pod was untouched —
  `muse-bridge-88f74d4c6-mnds9`, **0 restarts**, `started 2026-09-29T21:35:05Z`,
  `imageID sha256:62efb46f…`, still serving the pre-allowlist binary. What
  changed is that Harbor's `0.1.0` now resolves to `sha256:0b0b98b3…` — the
  merged allowlist build — so with `IfNotPresent` the tag means the OLD binary
  only on the node still holding that layer. A reschedule elsewhere would have
  enabled the allowlist at an unplanned moment.
- **Ruled out:** "this is harmless because the content is the reviewed code" —
  true about the content and irrelevant to the defect: the tag stopped matching
  what the Deployment claimed, in a cluster whose pull policy makes that
  unobservable per-node. `via: measurement`.
- **Ruled out:** restoring `0.1.0` by rebuilding the pre-merge source and pushing
  it back. These builds are **not reproducible** (base images pinned by tag, not
  digest — a handoff LOW finding), so that would mint a THIRD digest asserting
  "this is the old binary" that nobody can verify. Worse than the honest mess.
  `via: code` (the Dockerfile's `FROM golang:1.26-alpine` / `distroless:nonroot`).
- **Resolution, operator-chosen:** finish deliberately. `0.1.1` built and pushed
  from the merged tree, Deployment bumped via homelab-infra **#944**, so the
  live state matches a commit instead of an accident. `0.1.0` and `0.1.1` now
  carry the same digest, which is why the bump was required rather than a reuse.
- **Next probe:** none — closed by #944 plus the guard rewrite. The guard now
  uses `docker pull -q` **and runs its own positive+negative control on every
  invocation** (a bogus tag must read absent, `0.1.0` must read present), and was
  watched to refuse `0.1.0` before building. Residual question worth one look if
  anyone cares: whether Harbor's own API would be a better instrument than
  `docker pull`, which has the side effect of pulling layers.

## Defects (batched)

Fixed as ONE round, not one rank each. Original ranks kept for reference.
⚠ The `muse status` 400 defect was PROMOTED to rank 25 — it is a live
regression now, not a latent one — and is deliberately not duplicated here.

- **(10) Flow file re-map** — `flows/muse.ai.md:66,75` still teach polling on
  assistant COUNT, measured flat across a send; add the approval and hydration
  gates; make `muse b1` cite the file instead of duplicating it.
- **(12) M1/M2 indistinguishable outcomes** — emit `"stable": true/false`; port
  `cmd_poll_b1`'s zero-length guard into `cmd_send_b1`; re-wake inside the poll
  loop instead of a bare `continue`; close the tab on the timeout path.
- **(13) B4 host hardcode** — `muse:119` `bw://laptop/`; derive from
  `browser whoami` (NOT shell `whoami`, which is the username).
- **(15) `TestLimiterBurstThenRefill` never tests refill.** ⚠ The other half is
  DONE properly: `routes()` extracted AND the finding it existed for closed —
  every non-public route is asserted to 401 on the real table, with a two-way
  public-set ledger. The refill arithmetic is still unexercised.
- **(21) M5 — fill in `openapi.yaml`'s five response schemas** and the
  500/404/405 responses; every data route still returns an untyped `ItemList`.
  ⚠ #942 updated the `ns` parameter and `BadNs` descriptions only.
- **(23) Nix-deploy the wrapper** as a `mkOutOfStoreSymlink` into
  `~/.local/bin/muse`. 🔴 Sequenced AFTER (13) **and after rank 25** — shipping
  it now would put a tool that 400s on most of its own arguments on both hosts'
  `$PATH`.
- **(24) Extract the B1 JS** to `scripts/muse/b1.js` and fixture-test it under
  the `node` tier.

## Gotchas / decisions / dead-ends

- 🔴 **I merged a public IP into this PUBLIC repo through a gate that caught it.**
  #1972's check read `failed=2`; the description named one test
  (`test_prune_skill_size`, inherited from a stale base). I verified that one,
  built a merged tree, ran **two** test files, and merged. The second failure was
  `test_no_unallowlisted_public_ip_literal_is_committed` firing on my own diff —
  the Hetzner origin IP, quoted from the security audit. Another session scrubbed
  it (#1979). **Reconcile the failure COUNT, never just the named test.**
- 🔴 **A dispatched agent sent a real message from the live Muse account.** Its
  brief said "do not send"; it built a stub env as an interpolated shell string
  (`env $E …`) and zsh does not word-split, so every override silently vanished.
  **Build subprocess env as a dict; stub `MUSE_BB` with a binary whose absence is
  detectable; grant send authority to exactly ONE agent per fan-out.**
- **`readlink -f` resolves paths that do not exist** — it printed
  `/home/zach/.local/bin/muse` for a missing file and I read that as proof of
  existence. Use `[ -e ]`.
- **zsh ate a git ref**: `$ref:scripts/...` applies the `:s` history modifier.
  Brace it — `${ref}:scripts/...`.
- **The audit ladder's own shape, five rounds running:** every round's defect was
  introduced by the PREVIOUS round's fix, and most were guards whose DESCRIPTION
  was wider than their body. Two were live production bugs introduced while fixing
  others — `--config -` silently truncating the token at a quote (presenting as a
  401), and the rc-3/rc-2 carve-out that left the expired-cookie case stamping.
- **muse-cli's exit codes do NOT match the wrapper's** — `AuthError→2`,
  `GatewayError→3`, `TimeoutError→4` (`muse_cli/cli.py:713-721`, v0.3.2). The
  wrapper's own header legend said rc 3 was auth; that is what produced the
  wrong-code carve-out. Now pinned by
  `scripts/devhost-tests/test_muse_cli_exit_contract.py`.
- **A test needing a BINARY goes in `scripts/devhost-tests/`, not `scripts/tests/`**
  — a skip in a hermetic target is an UNPINNED SKIP and GUARD 2 fails the sandbox
  tier while the dev host stays green.
- **Decision:** `[ -n "$force" ]` in place of the `-eq 1` test is an **equivalent
  mutant** (measured across all four stamp × `--force` states) and is deliberately
  NOT tested. Do not add a test to force a kill there.

- 🔴 **A Go test that PANICS aborts the test binary and suppresses every later
  test's verdict — which silently hollows out a mutation sweep.** Measured
  2026-10-02 while sweeping #942: de-gating `handleFlux` made the route test
  panic on the nil k8s client, so the run printed **one** FAIL and the two seam
  guards that should also have fired never executed. Reading that output, the
  ledger guard and the structural guard both looked like they had **failed to
  catch the mutant**. Fix: `defer recover()` inside the loop body and report the
  panic as a failure — the same mutant then fires all three, each with its own
  message. **Any sweep over a suite where one test can panic is measuring a
  prefix of itself.**
- **A nil dependency is a usable positive control.** `museOnly()` builds the
  server with `k8s: nil`, so "the request got past the gate" is observable as a
  panic. A 400-only test cannot distinguish a correct allowlist from a gate that
  refuses *every* namespace — the panic case is what separates them, and it costs
  no fake clientset.
- **The error message for a disallowed ns is deliberately the SAME as for a
  malformed one** (`invalid or missing ns`). Distinguishing them would let a
  prober enumerate the allowlist one namespace at a time.
- **Decision: the compiled-in default is NOT the knob.** The env var is, and it
  **replaces** the default rather than merging with it — otherwise an operator
  narrowing the list to `monitoring` would silently retain `muse`. A test pins
  the replacement semantics and a second pins the default at exactly `["muse"]`,
  so widening the compiled-in list fails the suite rather than passing quietly.
- **The go toolchain here is on `$PATH` directly** (`go1.26.8`), so bridge tests
  run as `go test -C <dir> ./... -count=1 -race` — not through devrc's
  `gate.sh`/`scoped-tests.sh`, which are the python/node/go tiers of **devrc**
  and know nothing about homelab-infra. `go build` leaves a `muse-bridge` binary
  in the package dir; delete it before staging.
- ⚠ **The editor LSP in this session reported dozens of phantom compile errors**
  for `containers/muse-bridge` (`undefined: server`, `could not import
  k8s.io/api/core/v1`) — a single-file/GOROOT-only view of a module outside the
  workspace. `go build ./...` and `go vet ./...` were clean throughout. Do not
  chase those; trust the toolchain.

- 🔴 **THREE OF MY OWN SWEEP HARNESSES PRODUCED FALSE READINGS, each of which
  read as a result.** (a) A guard asserted the body contained `"ns"`, and
  `"namespace not served"` contains no such substring — every gated route read
  as UNGATED. (b) A mutation's anchor line occurred TWICE, so the assert failed,
  nothing was mutated, and the mutant scored **SURVIVED against an unmutated
  tree** (caught only because the traceback printed above the result). (c) A
  positive control computed FROM the body under test, so whenever the primary
  assertion correctly fired the control `t.Fatalf`'d "wired to nothing". **Give
  every mutant three controls before reading its verdict: anchor occurs exactly
  once, the bytes actually changed, and the tree compiles.** A mutant that does
  not compile scores NOTHING.
- 🔴 **A SPELLED GUARD IS WALKABLE EVEN WHEN YOU WROTE IT KNOWING THAT.** Two
  successive route guards were defeated by a different spelling — the second
  keyed on the variable NAME (`m2 := mux`) four lines below its own comment
  warning about exactly this. **Assert the STATE, by enumerating what the
  system really does**; for `http.ServeMux` that is `index.segments`
  (`map[routingIndexKey][]*http.pattern`) plus `index.multis`, with
  `pattern.str` the registered string, read by reflection — `f.String()` works
  directly, no `unsafe` needed.
- 🔴 **READING UNEXPORTED INTERNALS IS DEFENSIBLE ONLY AS A PAIR:** `t.Fatal` on
  every absent field (so a rename breaks loudly instead of silently ceasing to
  check) **and** a positive control registering an extra item and asserting it
  is SEEN (so fields that exist but no longer hold data cannot pass). One
  control per BRANCH — a single `segments`-shaped control left the `multis` loop
  no-op'able and SURVIVING.
- 🔴 **A FIXTURE CONSTANT CAN MAKE A WHOLE CLASS INVISIBLE.** Every route test
  built its server with `k8s: nil` on purpose (a nil client is how "got past the
  gate" is observed as a panic) — so a registration CONDITIONAL on `s.k8s`
  appeared in neither the table nor the mux, they agreed, and the suite was
  green while production served it with no token. **Ask which dimension your
  fixture pins**, then assert the thing is independent of it.
- 🔴 **A PAYLOAD CHANGE CAN VOID A MUTATION CONTROL IN THE SAME COMMIT, WITH THE
  SUITE GREEN THROUGHOUT.** Moving a filter upstream meant a test's input could
  no longer reach the state it was asserting about: the mutant was KILLED at the
  base sha and SURVIVED at the head. Pin an invariant against a HAND-BUILT
  input, not one produced by the code under test.
- 🔴 **"ONE MUTANT SURVIVES" WAS WRONG TWICE, IN THE SAME PARAGRAPH.** It named
  one; an audit found a second; it then named two and said "the complete set";
  the next audit measured a third. **A list of residual limits reads as
  COMPLETE** — write "the ones found so far", and never offer a survivor count
  as closed.
- **Decision: two guard gaps are ACCEPTED and documented rather than closed** —
  a registration in `main()` after construction, and a mux interposed inside
  `ServeHTTP`. Neither is reachable by an in-process test. Closing the second
  structurally would stop unmatched requests being logged, which is a behaviour
  regression to fix a guard gap: the wrong trade. Both are listed in
  `ns_allow_test.go` with their reasons.
- **The audit ladder's own shape, FIVE rounds: every round's findings were
  defects in the PREVIOUS round's fix, and the dominant finding four rounds
  running was a false sentence a fix wrote while explaining itself.** It ended
  on the ATTRIBUTION GATE (`audit-dispatch.py --round 5` → exit 5), not on a
  clean round and not on judgement: rounds 3 and 4 each changed zero executable
  payload lines, i.e. the ladder had started auditing prose it had itself
  written.
- ⚠ **Do not trust a payload figure without saying which unit it is.** My posted
  `payload=40` for round 2 counted comment lines inside payload files; the
  executable-line figure for that range is **10**. One name, one number, per
  round.
- ⚠ **`gh pr view --json comments` does NOT return review comments**, so an
  `audit-claims` block posted as a review is invisible to `audit-dispatch.py`.
  Post it as an issue comment.
- ⚠ **A cross-repo audit assembly cannot verify its own payload figures** — the
  brief is assembled in `devrc`, so neither endpoint of a homelab-infra range
  resolves there and the gate reads the count as POSTED. Measure it yourself in
  the target clone and say so.
- ⚠ **The editor LSP reported dozens of phantom compile errors** for
  `containers/muse-bridge` (`undefined: server`, broken `k8s.io` imports) — a
  single-file/GOROOT-only view of a module outside the workspace. `go build
  ./...` and `go vet ./...` were clean throughout. Do not chase those.
- **A test needing a BINARY goes in `scripts/devhost-tests/`, not
  `scripts/tests/`** — a skip in a hermetic target is an UNPINNED SKIP and
  GUARD 2 fails the sandbox tier while the dev host stays green.
- **`scripts/kustomize-validate.sh` needs `nix develop`** — outside it, it fails
  all 124 roots with `kustomize: command not found` and prints `FAIL`. An
  environment defect, not a change defect.

- 🔴 **A GUARD BUILT ON AN UNVALIDATED INSTRUMENT IS NOT A WEAK GUARD, IT IS AN
  INVERTED ONE — AND I SHIPPED ONE INTO THE ACTION IT WAS GUARDING.** `docker
  manifest inspect` cannot reach a self-signed registry and fails non-zero for
  EVERY tag, so `if inspect; then die; fi` reads "the tag is free" always. I
  wrote that guard, wrote a comment explaining the trap it prevented, and then
  pushed over a live production tag with it. **The rule I broke is already in
  `claude/RULES.md`: validate the instrument before reading its verdict — BOTH
  controls, a case it must reject and a case it must accept.** For an existence
  check that means: feed it a thing that exists and a thing that does not, and
  require DIFFERENT answers. A single rc=1 is not evidence of absence; it is
  evidence of nothing.
- 🔴 **"Testing the refusal path" IS running the command.** The test that found
  this was `script 0.1.0` — chosen *because* 0.1.0 exists, expecting a refusal.
  When the guard is the only thing standing between a test invocation and an
  irreversible action, a broken guard turns the test into the action. **Put the
  refusal checks before anything that mutates, and prove they fire with the
  mutating steps still unreachable** — e.g. by pointing the script at a registry
  that cannot be written, or by a `--preflight-only` mode. A script whose
  dangerous path is reachable from its own happy-path test is mis-shaped.
- ⚠ **`docker pull` as an existence probe has a side effect** — it downloads
  layers. Acceptable here (small image, LAN registry) and noted so nobody reads
  it as a free metadata read.
- **Where the muse-bridge build steps live now:** `homelab-infra:
  scripts/release-muse-bridge.sh` (added in #944). Phases: default = preflight +
  tests + build + push + read-back; `--verify-only` = probe what is live, and it
  was watched to FAIL against the pre-allowlist binary before being trusted. It
  deliberately makes **no git writes** — the Deployment bump goes through a
  worktree and a PR, matching PR 925's `deploy(muster): 0.2.1` precedent, because
  committing to `trunk` IS deploying and the main checkout is shared.

- 🔴 **TWO FALSE READINGS IN ONE SESSION, BOTH "AN ABSENCE READ AS AN ANSWER",
  AND THE SECOND CAME TEN MINUTES AFTER DIAGNOSING THE FIRST.** (a) `docker
  manifest inspect` returns rc=1 for every tag on a self-signed registry, so an
  existence guard built on it is INVERTED, not weak — it pushed over a live
  production tag. (b) A CI wait loop keyed on
  `gh api .../status --jq '.statuses[0].state'` exited immediately, because that
  is `null` before any status is POSTED and `null != "pending"` is true — a
  not-yet-created status read as settled. **For any "is it there / is it done"
  probe, require the thing to EXIST as a separate condition from its value**, and
  demand different answers for a case that is present and one that is not.
- 🔴 **"Testing the refusal path" IS running the command.** The invocation that
  found (a) was deliberately chosen to be refused. When a broken guard is the
  only thing between a test and an irreversible action, the test becomes the
  action. **Put refusal checks before anything that mutates and prove they fire
  with the mutating steps unreachable** — a script whose dangerous path is
  reachable from its own happy-path test is mis-shaped.
- **`flux` is NOT installed on this host.** Trigger a reconcile with
  `kubectl -n flux-system annotate --overwrite kustomization/<name>
  reconcile.fluxcd.io/requestedAt="$(date -u +%FT%TZ)"` — same mechanism, and it
  worked (Deployment picked up `0.1.1`, rollout completed).
- **`sops` is NOT on PATH either**, so `release-muse-bridge.sh --verify-only`
  SKIPS its authenticated probe and says so rather than passing silently. Run
  the probe under `nix-shell -p sops`. A verifier that announces what it did not
  check is the design; a pass from it alone would have been the startup line
  only, which is necessary and NOT sufficient.
- **Decision: two guard gaps in `ns_allow_test.go` are ACCEPTED and documented**
  — a registration in `main()` after construction, and a mux interposed inside
  `ServeHTTP`. Neither is reachable in-process; closing the second structurally
  would stop unmatched requests being logged. A behaviour regression to close a
  guard gap is the wrong trade.
- ⚠ **A cross-repo audit assembly cannot verify its own payload figures** — the
  brief is assembled in `devrc`, so neither endpoint of a homelab-infra range
  resolves there and the gate reads the count as POSTED. Measure it in the
  target clone and say so.
- ⚠ **`gh pr view --json comments` does NOT return review comments**, so an
  `audit-claims` block posted as a review is invisible to `audit-dispatch.py`.
  Post it as an issue comment.

- 🔴 **RETRACTED (2026-10-03): the bridge does NOT return the same message for a
  disallowed and a malformed `ns`.** This doc recorded, as a decision, that
  *"the error message for a disallowed ns is deliberately the SAME as for a
  malformed one (`invalid or missing ns`)"* to stop allowlist enumeration.
  `nsRejection` (`muse-bridge/main.go:126-138`, read at `origin/trunk`) **names
  which half refused**: `malformed ns: must match <pattern>` vs `namespace not
  served: this bridge is restricted to an allowlist`. The anti-enumeration
  property is preserved a DIFFERENT way — neither message names a MEMBER of the
  list. Load-bearing: a client fix written to the retracted sentence would have
  refused to distinguish two cases the server already distinguishes.
  `via: code`.
- 🔴 **A NEGATIVE CONTROL THAT IS RED AT BASE IS NOT REGRESSION COVERAGE, AND
  THE MATRIX WILL READ AS IF IT WERE.** Four of #1993's tests are red at base;
  only TWO are regressions. The other two are negative controls, red there only
  because they share the positive precondition — the branch they constrain does
  not exist at base at all. Reported as a bare "4 red at base", that reads as
  four regressions caught. What proves a negative control discriminates is a
  MUTATION that makes the guard fire wrongly, never the base run. Label them.
  `via: measurement`.
- ⚠ **`scoped-tests.sh` fans out to 72 files on a `claude/skills/*/SKILL.md`
  edit** (every skill listing/size/audit/tier test names that path), needs the
  dev shell (`nix develop <wt> --command bash scripts/scoped-tests.sh`; run bare
  it exits 3 `FATAL — required tool(s) missing from PATH: logrotate dash`, an
  ENVIRONMENT defect, not a change defect), and takes longer than one foreground
  Bash call allows (max 600s). 🔴 **A log that stops growing is BUFFERED, not
  hung** — measured 6,501 B unchanged across ~15 min with the process live in
  the browser-bridge leg. Prove liveness from the PROCESS, never the log's size
  or mtime; a verdict never read is UNRESOLVED, never green.

## How to verify

```bash
# 🔴 RANK 25 — the matrix, both ends. 4 RED at base / 27 green at HEAD.
#   ⚠ TWO of the four are NEGATIVE CONTROLS, not regressions (see Gotchas).
WT=<a worktree at fix/muse-status-400-policy-denial>
nix develop $DEVRC -c python3 -m pytest "$WT"/scripts/tests/test_muse_wrapper.py -q   # 27 passed
#   base end: worktree origin/main, copy the test file in, re-run -> 4 failed / 23 passed
#   merged with devrc#1986 (same file) -> 21 passed, all 11 new tests present

# the gate this session did NOT resolve — read CI, do not re-run 72 files locally
gh pr checks 1993 --repo innovation-upstream/devrc

# content + skill gates — MANDATORY here: this repo is PUBLIC and this arc
# already leaked an origin IP through a gate that caught it
nix develop $DEVRC -c python3 -m pytest \
  $DEVRC/scripts/tests/test_no_public_ips.py \
  $DEVRC/scripts/tests/test_no_captured_text.py \
  $DEVRC/scripts/tests/test_no_captured_markup.py \
  $DEVRC/scripts/tests/test_skill_descriptions.py \
  $DEVRC/scripts/tests/test_skill_tiers.py \
  $DEVRC/scripts/tests/test_runtime_shebangs.py -q    # 281 passed

# 🔴 IS THE ALLOWLIST STILL LIVE? (reproduces the pre-#942 symptom)
~/workspace/homelab-talos/scripts/release-muse-bridge.sh --verify-only
#   ⚠ SKIPS the authenticated half when sops is absent and SAYS so. For the
#   behavioural proof run it under: nix-shell -p sops --run '…'
KUBECONFIG=$KC_HOMELAB kubectl -n muse logs deploy/muse-bridge | head -1   # ns-allow=muse

# the bridge suite (18 tests) — there is NO repo-level Go gate in homelab-infra
go test -C $HOMELAB/containers/muse-bridge ./... -count=1 -race -v | grep -cE 'PASS:'   # 18
nix develop $HOMELAB -c scripts/kustomize-validate.sh | tail -1   # PASS (124 roots)

# the arc's closing condition, second clause (rank 18 is what closes it)
KUBECONFIG=$KC_HOMELAB kubectl auth can-i --list \
  --as=system:serviceaccount:muse:muse-reader | grep -E 'pods/log|services|namespaces|replicasets'
```

🔴 **Do NOT run `muse send` to verify** — it sends from the operator's live
account across Meta's wire and the pacing gate allows one send per 10 min.
⚠ `muse status <ns>` still 400s for every namespace but `muse`. That is the
server being right; #1993 makes the CLI SAY so and is **not merged**, so an
unpatched copy still reads the denial as a typo.
