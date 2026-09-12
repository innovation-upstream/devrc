# clawgate — troubleshooting playbook

Read when: something is broken. Symptom-first index.

## No native notifications
Check pod logs for `subscription stored` (none = the phone never subscribed) and
`delivered ... to N device(s)` (N>0 = the server delivered, so a missing notification is the
OS/browser swallowing it).
- On Android: allow notifications for the app + set battery to **Unrestricted**.
- Brave web-push is finicky; a **Chrome-installed PWA is the reliable fallback**.

## PWA shows the browser icon, not clawgate's
Stale install. Icons must be **8-bit PNG** (Brave won't render 16-bit).
Fully remove the home-screen icon → clear the site's data in the browser → reload → use
**"Install app"** (WebAPK, uses the manifest icon), **not "Add to Home screen"** (a bookmark
shortcut carrying the browser icon).
Verify: `curl -s .../static/icons/icon-192.png | file -` should say 8-bit.

## `kubectl --kubeconfig …` fails: no such file (the workbench kubeconfig path)
**The path is PER-HOST — a missing file means "wrong host", not "wrong doc".** Measured on both
hosts 2026-08-12:

| host | the file that EXISTS | the other path |
|---|---|---|
| workbench `192.168.50.250` | `~/workspace/homelab-talos/workbench-kubeconfig` | absent |
| laptop `192.168.50.155` | `~/workspace/homelab-infra/workbench-kubeconfig` | absent |

The names differ because the **origin repo** was renamed `homelab-talos` → `homelab-infra` while the
**local checkout dir** was not renamed on every host — repo checkout name ≠ kubeconfig dir. Both
hosts report hostname `nixos`, so disambiguate by IP (`ip -4 addr`) or `browser whoami`.

⚠ **`$KC_WORKBENCH` is exported on the workbench only** — it is empty on the laptop even in a login
shell, so a doc that says "just use `$KC_WORKBENCH`" breaks there. Select the file instead
(verified verbatim in bash and zsh on both hosts):
```bash
KC=$(ls /home/zach/workspace/homelab-{talos,infra}/workbench-kubeconfig 2>/dev/null | head -1)
```

## A UI feature "looks broken" for a user but works in incognito/fresh
**Stale service-worker cache — suspect the SW first.** `app.css` used to be cache-first under a
never-bumped cache, so returning users kept old CSS missing new classes. Fixed in 0.3.6: `app.css`
is network-first. **The cache name is bumped per shell change — read it, don't quote it:** it is
`clawgate-shell-v4` as of 2026-08-12 (`web/static/sw.js:20`), not the `v2` this entry used to name.
A normal reload picks up fresh CSS post-deploy.

## Card not removed when resolved in Claude Code
`DELETE /api/response/{id}` must broadcast resolved (fixed in 0.2.0). The hook DELETEs its request
on decision/timeout. Card actions are optimistic (removed instantly, POST queued in background with
a `↻ N` header indicator); SSE reconciles the badge.

## Stale request cards pile up
The hook DELETEs on ANY exit (trap) and the server TTL is short. ⚠ **`CLAWGATE_REQUEST_TTL=5m` is a
DEPLOYMENT value, not inherent** — the code default is **1h** (`main.go:68`, `defaultRequestTTL =
time.Hour`), so a locally-run clawgate evicts after an hour, not five minutes. The hook poll deadline
is 170s, so nothing legitimate pends longer. On the cluster, orphans auto-evict within ~5 min.

## `fetch ... URL that includes credentials`
The page was opened with basic-auth creds in the URL (`https://user:pass@host`). Client fetches
must build URLs from `location.origin` (credential-free), not relative paths. Fixed in 0.2.1.

## Agent helm install fails: RBAC "attempting to grant permissions not currently held"
The chart's `rbac.create` makes a per-agent Role; clawgate's ClusterRole **`clawgate-agents` must
be a superset** (it needs `pods/log:watch` + `apps/statefulsets`). Add the missing verbs to
`rbac.yaml`.

## Agent model "Unknown model"
Wrong image. Use `CLAWGATE_AGENT_IMAGE_REPO=harbor.homelab.lan/library/clawdbot` (newer OpenClaw),
**NOT `openclaw`** (stale v2026.2.13).

## Agents can't auth to the model
Needs chart 0.4.0+'s `agent.auth.provider: openrouter`, which writes the api_key auth profile from
`OPENROUTER_API_KEY`. Without it there is no auth profile at all.

## Agent `git push` fails with an empty password
Expected if something reverted to `$GITHUB_TOKEN` — openclaw's exec sandbox strips it. The helper
must read `/root/.gh-token`. See `~/.claude/skills/clawgate/reference/architecture.md` → Repos tab.

## Mass e2e failure
🔴 More often the **CSS-cwd trap** or the box than a regression. Build `app.css` from inside
`containers/clawgate/`, then run the **pristine-`origin/trunk` baseline**, before theorising. See
the core SKILL.md deploy section, and `~/.claude/skills/clawgate/reference/architecture.md` → e2e for the flake specifics and
the `clawgate-e2e-pg-*` container leak.

## A host's transcripts look stale — is the stream down, or is the host just idle?
🔴 **Staleness alone cannot tell you, and guessing gets it wrong.** A stale transcript has two
causes — the host's stream is broken, or nobody has typed there — and the observable is identical.
Measured 2026-09-12: the laptop's freshest transcript was **4431 s** behind with 0 of 7 under
300 s, which read as a dead stream and was not one.

Two reads settle it, in this order:

1. **Ask the stream itself.** `reason: accepted` with a byte offset is the STREAM path's own
   verdict — the 5-minute bulk push does not produce one.
   ```bash
   set -a; . ~/.claude/clawgate.env; set +a
   curl -s -H "X-Clawgate-Token: $CLAWGATE_HOOK_TOKEN" \
     "$CLAWGATE_API_URL/api/transcripts/stream/cursors" | jq '.sessions | length'
   curl -s -o /dev/null -w '%{http_code}\n' -H "X-Clawgate-Token: $CLAWGATE_HOOK_TOKEN" \
     "$CLAWGATE_API_URL/api/nosuchroute"    # 404 — so a 401 means "exists, needs auth"
   ```
   The cursor rows carry no host, so join `sessionId` against `clawgatectl tmux windows`
   (`host` + `claude_session_id`). Any `accepted` row for a host means that host's streamer is
   delivering.
2. **Ask whether anything went MISSING**, which is the question staleness cannot answer.
   `ledger.last_activity_ts` in the tmux read model is written ON the host the session runs on,
   so `host_activity - clawgate_updatedAt` is positive only when the host knew of writes clawgate
   never got. Run it for the suspect host **and for a host whose stream is known-live as the
   control** — measured, workbench max `+107 s` and laptop max `-14 s`, i.e. zero missing on
   either, which is what proved the laptop idle rather than disconnected.

🔴 **`X-Clawgate-Token`, not `X-Hook-Token`** — the wrong header returns
`401 {"error":"invalid or missing hook token"}`, which reads as a credential problem rather than
a header-name one (`internal/api/auth.go:583-588` accepts only `Authorization: Bearer` or
`X-Clawgate-Token`).

🔴 **An idle host cannot demonstrate stream LATENCY at all.** If every session there has been
quiet for an hour, there is no recent append to time and no number to report — say that, rather
than quoting the freshest gap. A sample within ~5 min is equally explained by the bulk push and
settles nothing.

## Resolved — do not re-derive
- ~~`.sops.yaml` on-disk is pre-truncated~~ **RESOLVED 2026-06-07**: the full ruleset was restored
  and a `clusters/workbench/apps/clawgate/.*.enc.yaml$` rule added, so the normal
  `SOPS_AGE_KEY_FILE=.secrets/age.key sops -e -i <file>` flow works — no more
  `--config /dev/null --age …` workaround. (`harbor-cred.enc.yaml` still MAC-mismatches under the
  sops 3.13 CLI but Flux applies it fine.)
