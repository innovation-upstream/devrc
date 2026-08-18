# SECRETS.md — env / credential manifest

> **PUBLIC repo.** This file documents **only** file paths, key *names*, host scope,
> and *where to obtain* each secret. It contains **no real secret values** — never
> paste a token, password, API key, or DSN here.

Purpose: make a new-host bootstrap deterministic instead of manual archaeology.
None of these files live in git or the nix store; each is created out-of-band on
the host. Home-manager auto-seeds exactly **one** of them (the activity-collector
env, from its committed `.env.example`); everything else is hand-placed.

Legend for "seeded by HM?":
- **auto** — `home-manager switch` copies a committed `.env.example` into place
  (chmod 600) if missing, then you edit it. See `nix/home.nix`
  `home.activation.activityCollectorEnv`.
- **manual** — you must create/copy the file yourself before the subsystem works.

---

## Per-host env / credential files

| file path | keys (names only) | host(s) | seeded by HM? | source of truth (how to obtain) |
|---|---|---|---|---|
| `~/.config/activity-collector/env` | `CLICKHOUSE_URL`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_TABLE`, `ACTIVITY_HOST`, `ACTIVITY_BATCH_SIZE`, `ACTIVITY_FLUSH_SECONDS`, `ACTIVITY_MAX_BUFFER_BYTES`, `ACTIVITY_MAX_BUFFER_AGE_SECONDS`, `ACTIVITY_HTTP_TIMEOUT`, (`ACTIVITY_SPOOL_DIR`) | both (workbench + laptop) | **auto** (from `scripts/collector/.env.example`, chmod 600) | Only `CLICKHOUSE_PASSWORD` is sensitive: the authed **writer** cred lives in the SOPS secret `homelab-talos/clusters/homelab/apps/activity/secrets.enc.yaml` (decrypt with `SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key`). `.env.example` default is empty (unauthed `default` user). Set `ACTIVITY_HOST` distinctly per host (`workbench`/`laptop` — both machines are hostname `nixos`). **Laptop** must point `CLICKHOUSE_URL` at its nebula CH endpoint. See the `activity` skill. |
| `~/.claude/clawgate.env` | `CLAWGATE_API_URL`, `CLAWGATE_HOOK_TOKEN` | both (workbench primary) | manual | The machine hook token for the clawgate service (`clawgate.zacx.dev` / NodePort `192.168.50.250:30302`). Same value stored in homelab secret `task-drafter-agent-secrets` (ns `devpod-task-drafter`, key `CLAWGATE_HOOK_TOKEN`). Write via stdin so the token isn't in shell history. See the `clawgate` skill. **Rotation coupling:** rotating this token requires updating that k8s secret too, or the daily drafter digest 401s silently. |
| `~/.config/repo-cos/env` | `OPENROUTER_API_KEY` (required); optional: `REPO_COS_SEND`, `REPO_COS_FROM`, `REPO_COS_REPLY_TO`, `REPO_COS_REPLY_SRC`, `REPO_COS_MODEL`, `REPO_COS_SMTP_USER`, `REPO_COS_SMTP_PASSWORD`, `REPO_COS_PROD_KUBECONFIG`, `REPO_COS_RELAY_NS`, `REPO_COS_RELAY_SVC` | workbench only (serverMode weekly timer) | manual (chmod 600) | `OPENROUTER_API_KEY` → OpenRouter dashboard (openrouter.ai). Default send path (`relay`) needs no SMTP creds. Gmail **fallback** (`REPO_COS_SEND=gmail`) uses the Gmail app-password in k8s secret `mailbox-gmail-imap`, key `IMAP_APP_PASSWORD` (or `REPO_COS_SMTP_USER`/`REPO_COS_SMTP_PASSWORD` overrides). See the `repo-cos` skill. |
| `~/.config/bar/media.env` | `PROWLARR_URL`, `PROWLARR_KEY`, `STASH_URL`, `STASH_KEY`, `WHISPARR_URL`, `WHISPARR_KEY`, `QBIT_URL` | workbench (graphical) | manual (0600) | API keys from each self-hosted service's own admin UI (Prowlarr / Stash / Whisparr → Settings → General/API key). **source: UNKNOWN for exact service endpoints — verify** the URLs against the current homelab/media deployment. Consumed by `scripts/media-detail`, `media-menu`, `deep-search`, `bar-status-poll`. |
| `~/.config/bar/airvpn.env` | `AIRVPN_API`, `AIRVPN_COUNTRY`, `AIRVPN_FWD_PORT`, `AIRVPN_WG_PORT`, `AIRVPN_MANIFEST`, `AIRVPN_SIGNAL_ICON`, `AIRVPN_SUDO`, `AIRVPN_SUDO_HELPER` | workbench (graphical) | manual (0600) | `AIRVPN_API` = AirVPN client-area API key (airvpn.org account → Client Area → API). Remaining keys are non-secret tuning. Consumed by `scripts/airvpn-menu`, `bar-status-poll`. |
| `~/.claude/audit-on-push.env` | `AUDIT_ON_PUSH`, `TESTS_ON_PUSH`, `AUDIT_MIN_LINES`, `AUDIT_TIMEOUT`, `AUDIT_LOG_FILE`; optional `CLAWGATE_API_URL`, `CLAWGATE_HOOK_TOKEN` | any host running the git pre-push hooks | manual (copy from `githooks/audit-on-push.env.example`) | Config only — **no standalone secret**. The optional clawgate keys are overrides; by default the hook reuses `~/.claude/clawgate.env`. Installed via `githooks/install.sh` (sets global `core.hooksPath`). |
| `~/.claude/task-spec-drafter.env` | `DRAFTER_MODE`, `DRAFTER_MODEL`, `DRAFTER_MAX_TICKETS`, `DRAFTER_TIMEOUT`, `DRAFTER_OUT_DIR`, `CLICKUP_VIEW_ID`; optional `DRAFTER_STATE_FILE`, `CIVITAI_REPO`, `PROD_KUBECONFIG`, `CLAWGATE_API_URL`, `CLAWGATE_HOOK_TOKEN` | wherever the drafter runs (homelab CronJob primarily) | manual (copy from `scripts/task-spec-drafter/task-spec-drafter.env.example`) | Config only — **no standalone secret**. LLM pass uses ambient Claude Code auth (`claude -p`); clawgate keys reuse `~/.claude/clawgate.env` unless overridden. |

### mail-actions — no local env file (creds read from k8s at runtime)

`scripts/mail-actions/` does not read a local secrets file; it pulls creds from
homelab k8s secrets via `kubectl` (so it needs a working `KUBECONFIG` +
`OPENROUTER_API_KEY` in the environment for the extractor). Documented for
completeness:

| what | key / secret (names only) | source of truth |
|---|---|---|
| Postgres `mail` DSN | k8s secret `mailbox-postgres-auth`, key `pg-dsn`, ns `mailbox` (or env `MAILBOX_PG_DSN`) | homelab cluster (`_db.py`) |
| MinIO invoice archiver | k8s secret `minio-archive-config`, key `config.env` → `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`, ns `minio-archive` (or env `MINIO_ARCHIVE_ENDPOINT`/`_ACCESS_KEY`/`_SECRET_KEY`) | homelab cluster (`_minio.py`) |
| LLM extraction (Stage 2) | `OPENROUTER_API_KEY` (env) | OpenRouter dashboard |

---

## Kubeconfigs (`$KC_*` handles from `nix/programs/zsh/default.nix`)

Each handle is existence-guarded in `.zshenv` — absent on hosts without that
checkout. These are **not** placed by devrc; they come from cloning the relevant
infra repo (or generating via `talosctl kubeconfig`). There is deliberately **no
default `KUBECONFIG`** so a bare `kubectl` can't hit prod.

| handle | path | cluster | source of truth |
|---|---|---|---|
| `$KC_HOMELAB` | `~/workspace/homelab-talos/homelab-kubeconfig` | homelab (`admin@zach-homelab`) | committed/generated in the `homelab-talos` repo |
| `$KC_WORKBENCH` | `~/workspace/homelab-talos/workbench-kubeconfig` | workbench single-node | `homelab-talos` repo |
| `$KC_DPPROD` | `~/workspace/civit/datapacket-talos/prod-kubeconfig` | DataPacket **prod** (client) | `datapacket-talos` repo (civit workspace) |
| `$KC_NEBULA` | `~/.kube/homelab-nebula.yaml` | homelab reached over nebula (laptop remote) | derived from the homelab kubeconfig with the nebula endpoint; place manually |

---

## New-host bootstrap order

Do these **in order** relative to the first `home-manager switch`:

1. **Clone devrc** into `~/workspace/devrc` (see README Installation).
2. **First `home-manager switch`** (`nix run github:nix-community/home-manager -- switch --flake ./devrc --impure`).
   - This **auto-seeds** `~/.config/activity-collector/env` from the committed
     `.env.example` (empty CH password = unauthed default user; the collector
     runs but ships to the default CH user until you add the authed cred).
   - New-host caveat: if a *foreign* `~/.claude/RULES.md` / `~/.claude/skills/*`
     pre-exists, `rm` it once before the switch (HM won't clobber foreign files).
3. **Activity telemetry** (edit the seeded file): set `ACTIVITY_HOST`
   (`workbench`/`laptop`), and on the laptop repoint `CLICKHOUSE_URL` at the
   nebula CH endpoint. Slot in the authed `CLICKHOUSE_PASSWORD` (writer cred from
   the SOPS secret) to ship to the authed store. `systemctl --user restart activity-collector`.
4. **clawgate** (for approval push + repo-cos/drafter card producers): create
   `~/.claude/clawgate.env` with `CLAWGATE_API_URL` + `CLAWGATE_HOOK_TOKEN`
   (via stdin). Needed before the PermissionRequest hook, repo-cos "approve", or
   the drafter digest work.
5. **Kubeconfigs**: clone `homelab-talos` (→ `$KC_HOMELAB`, `$KC_WORKBENCH`) and,
   if this host does client work, the civit `datapacket-talos` repo (→ `$KC_DPPROD`).
   Place `~/.kube/homelab-nebula.yaml` for `$KC_NEBULA` on remote hosts.
   Required before: mail-actions (reads k8s secrets), repo-cos relay/feedback,
   any `KUBECONFIG=$KC_* kubectl` call.
6. **repo-cos** (workbench, serverMode only): create `~/.config/repo-cos/env`
   with `OPENROUTER_API_KEY` (chmod 600). Without it the weekly timer skips.
7. **mail-actions** (workbench/homelab): no local file — ensure `KUBECONFIG`
   reaches homelab and `OPENROUTER_API_KEY` is exported for the extractor.
8. **Graphical bar extras** (workbench, optional): `~/.config/bar/media.env` and
   `~/.config/bar/airvpn.env` (0600) if you use the media/AirVPN bar blocks.
9. **git pre-push hooks** (optional): `githooks/install.sh`, then copy
   `githooks/audit-on-push.env.example` → `~/.claude/audit-on-push.env` if you
   want to tune the audit/test gate.

---

## Dead credentials in reachable history — ALREADY ADJUDICATED, do not re-raise

> **Read this before opening a 🔴 on `cmd/cluster/certs/`.** A credential-shaped
> finding with no live trust costs an hour every time it is rediscovered. It has
> been discovered at least twice. The determination below is the answer.

**What is there.** Three genuine **P-256 (prime256v1) EC private keys**, in SEC1
`EC PRIVATE KEY` PEM form, are present in this repo's **reachable history**:

| path | added by | date |
|---|---|---|
| `cmd/cluster/certs/root.key` | `eb5d197b` | 2021-04-29 |
| `cmd/cluster/certs/issuer.key` | `eb5d197b` (rotated in `d487dadb`) | 2021-04-29 |
| `cmd/cluster/certs/ca-new.key` | `d487dadb` | 2022-03-16 |

Because `issuer.key` was rotated rather than replaced in place, the two commits
carry **four distinct key blobs** between them, all P-256. Both commits are
**ancestors of `origin/main`** (`git merge-base --is-ancestor` — verified). The
files are **absent from HEAD**.

They are a **Linkerd** trust anchor, its identity issuer, and a rotated CA, added
by `add scripts for dev cluster linkerd installation` and `rotate certs`
respectively, for a shared **dev** cluster that no longer exists.

**Why nothing is being done.** The operator has confirmed they do not use
Linkerd, and that was verified independently rather than taken on trust:
**0 Linkerd namespaces across all three reachable clusters** — homelab (50
namespaces), workbench (25) and dpprod (143), 218 namespaces examined, measured
2026-08-17. The non-zero namespace totals are the positive control: a scan
returning 0/0/0 would be indistinguishable from a `kubectl` wired to nothing.

So: **the anchors are dead, no rotation is required, and history is
deliberately NOT being rewritten.** Rewriting it would not unpublish anything
already cloned from a public repo, and would break every existing checkout and
sha reference for no security gain. This is a decision, not an oversight.

**Do not paste key material anywhere** — including into an issue explaining the
finding. Reference by path and sha, as this table does.

**The trade this section itself makes, stated so it is a decision and not an
oversight:** publishing the exact paths and commit shas in a PUBLIC repo takes
the cost of *rediscovering* these blobs to zero — anyone reading this file can
`git show` them without searching. That is accepted because the adjudication
above is that the keys are dead: there is nothing left to protect by obscurity,
and the alternative — a finding that costs an hour every time someone stumbles
on it — has already been paid at least twice. 🔴 **If any of these anchors ever
turns out to be live, this section becomes the disclosure**, and the answer is
rotation first, then editing this file — not the other way round.

### 🔴 The structural gap this exposed — every content gate reads HEAD only

`scripts/tests/test_no_public_ips.py`, `test_no_client_hostnames.py`,
`test_no_captured_text.py` and `test_no_captured_markup.py` all enumerate tracked
files via `git ls-files`. **None of them reads history.** That is exactly why
these keys sat here for four to five years while every gate reported clean, and
it means **a green run is a claim about HEAD and nothing else**.

The limitation is not left as prose:
`test_no_captured_markup.py::test_the_gate_is_blind_to_git_history` DRIVES it —
it commits a finding, deletes it, and asserts the scan goes quiet while the
content is still in the log. If someone later teaches these gates to read
history, that test fails and this section is what must be updated.

**Extending a gate to history is separate work** (different file enumeration,
different allowlist keying, and a per-commit blob walk is not a per-run gate).
It is deliberately not attempted here.

---

## Source-of-truth items to verify (flagged for the owner)

- `~/.config/bar/media.env` — the exact self-hosted service **URLs** (Prowlarr /
  Stash / Whisparr / qBittorrent endpoints) aren't recorded in-repo; confirm
  against the live homelab/media deployment. API keys come from each service's
  own admin UI.
- `$KC_NEBULA` (`~/.kube/homelab-nebula.yaml`) — placed manually; confirm the
  nebula endpoint/CA match the current homelab config.
