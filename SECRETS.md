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
| `~/.config/subsystem-store/env` | `SUBSYSTEM_STORE_URL`, `SUBSYSTEM_STORE_TOKEN` | both (workbench + laptop) | manual (chmod 600) | Bearer token for the hosted `/analyze-service` store API (`store.zacx.dev/api/`), read by `scripts/store`. Source of truth: k8s secret `subsystem-store-token`, key `token`, ns `subsystem-store` on **homelab**. Write via stdin/`printf` so the token isn't in shell history. Environment variables of the same name override the file, which is how the tests point at a throwaway server. **Rotation coupling:** the server accepts a SET of tokens for overlap, so rotate by adding the new one to the k8s secret first, updating **both** host files, then dropping the old — rotating the secret alone silently orphans both hosts and every `store` call reports `store-unreachable` with an HTTP 401 reason. ⚠ **Edge gotcha, measured 2026-08-25:** Cloudflare answers **403** to the default `Python-urllib/*` User-Agent on this host (same token, same path: curl default UA → 200, `Python-urllib/3.12` → 403). That 403 is the edge, not the app — it is neither a bad token nor an outage. `scripts/store` sets its own UA; any other client must too. |
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

### analyze-service index backup — no new secret, REUSES the SOPS age key

`scripts/analyze-service-index/backup.py` (systemd user timer
`analyze-service-index-backup`, daily, **both hosts**) bundles each scope of the
`/analyze-service` index store, encrypts it with `age`, and uploads it to the
`minio-archive` tenant. It introduces **no new credential**:

| what | key / secret (names only) | source of truth |
|---|---|---|
| encryption identity | the **existing** SOPS age key — file `~/workspace/homelab-talos/.secrets/age.key`, handle `SOPS_AGE_KEY_FILE` (override: `ASIB_AGE_IDENTITY`) | the `homelab-talos` repo; the same key already used for every `*.enc.yaml` in the homelab clusters |
| MinIO destination | reuses `_minio.py` entirely — k8s secret `minio-archive-config` (see the row above) | homelab cluster |
| cluster route | `KUBECONFIG`; the unit sets the workbench path and `backup.py` falls back to `~/.kube/homelab-nebula.yaml` on the laptop | see the Kubeconfigs table below |

🔴 **The recipient is DERIVED from that key file at run time** (`age-keygen -y`),
never hardcoded — devrc is public, and more importantly a hardcoded recipient can
drift from the key the operator actually holds, producing archives that encrypt
cleanly and decrypt never. **No new key is minted**: a backup encrypted to a key
nobody keeps alive is a backup nobody can open. If the key file is missing the
backup FAILS loudly rather than falling back to an unencrypted upload.

Objects live at `<host>/<scope>/<UTC stamp>.bundle.age` in bucket
`analyze-service-index-backups`. The host segment carries the machine ID because
**both machines are hostname `nixos`** and their stores are divergent — a shared
prefix would make each host's retention pass evict the other's backups.

**Restoring** (the whole point — rehearse it before you need it). 🔴 **Step 1 is
the retrieval, and it is the one thing you will not already have in the scenario
this exists for.** The bucket is reachable only in-cluster, so bridge to it the
same way the backup does — `_minio.py`'s `kubectl port-forward`:

```sh
cd ~/workspace/devrc
KEY=<host>/<scope>/<UTC stamp>.bundle.age     # from the listing below

# 1a. list what is there (per host, per scope, newest last)
KUBECONFIG=$KC_HOMELAB nix-shell -p 'python3.withPackages(p:[p.minio])' --run '
python3 -c "
import sys; sys.path.insert(0, \"scripts/mail-actions\")
from _minio import MinioArchive
with MinioArchive() as mc:
    for o in sorted(x.object_name for x in mc.client.list_objects(
            \"analyze-service-index-backups\", recursive=True)):
        print(o)
"'

# 1b. fetch ONE object to disk
KUBECONFIG=$KC_HOMELAB KEY="$KEY" nix-shell -p 'python3.withPackages(p:[p.minio])' --run '
python3 -c "
import os, sys; sys.path.insert(0, \"scripts/mail-actions\")
from _minio import MinioArchive
key = os.environ[\"KEY\"]
with MinioArchive() as mc:
    r = mc.client.get_object(\"analyze-service-index-backups\", key)
    open(\"restore.bundle.age\", \"wb\").write(r.read()); r.close(); r.release_conn()
"'

# 2. decrypt
age --decrypt -i ~/workspace/homelab-talos/.secrets/age.key \
    -o restore.bundle  restore.bundle.age

# 3. restore, then IMMEDIATELY drop the remote the clone just added
git clone restore.bundle <scope>
git -C <scope> remote remove origin
git -C <scope> remote          # must print NOTHING
```

🔴 **Step 3's second line is not optional.** `git clone <bundle> <dir>` sets
`origin` to the bundle's path, which breaks the `remote = none` invariant every
scope README, `commit.sh`'s `PrivateNetwork` unit and this feature's own
`test_no_scope_gains_a_remote` rest on. A scope restored and left with a remote
looks healthy and violates the one property the store is supposed to have.

🔴 **`git clone` restores `refs/heads/*` and tags ONLY.** Measured: a bundle
created with `--all` that declares `refs/notes/commits` restores through a plain
(or `--bare`) clone with that ref silently missing. `backup.py`'s own restore
rehearsal is a `--mirror` clone for exactly this reason. If the scope carried
non-branch refs, restore with `git clone --mirror restore.bundle <scope>.git`
instead — or check with `git bundle list-heads restore.bundle` first.

**Rehearsing it, without doing any of the above by hand** —
`scripts/analyze-service-index/restore-verify.py` runs that whole path for every
scope and reports what it proved:

```sh
# 🔴 PASS --host. A BARE RUN CANNOT FIND THIS HOST'S ARTIFACTS — the unit writes
# ASIB_HOST=<name>-%m (systemd expands %m to the machine id) while a hand-run has
# ASIB_HOST unset and the label falls back to the hostname, which is `nixos` on
# BOTH machines. So a bare run searches `nixos/`, finds nothing, and exits 1. It
# says so and names the right prefix — but it is not the command you want.
KUBECONFIG=$KC_HOMELAB nix-shell -p 'python3.withPackages(p:[p.minio])' age --run \
  "python3 scripts/analyze-service-index/restore-verify.py --host $(hostname)-$(cat /etc/machine-id)"

# …or let a bare run tell you the prefix, then paste it back:
… restore-verify.py                             # exits 1, NAMES the right --host
… restore-verify.py --host <other-host-label>   # the laptop's: "no off-machine
                                                #   backups at all" if it has none
… restore-verify.py --from-dir ./objects        # artifacts already fetched, offline
… restore-verify.py --print-plan                # what it would do; writes nothing
```

⚠ `$(hostname)` is the readable half only; the unit's is `workbench`/`laptop` from
a `isLaptop` probe, so if the two disagree take the prefix from the bare run's
message rather than from the line above. Resolving that asymmetry properly is a
tracked follow-up — until then, **`--host` is how you run this**.

It downloads → `age -d` → **`git clone --mirror`** → `git fsck`, then checks the
restored history against the live scope: every restored commit must still exist
there and every restored tip must be an **ancestor of** the live tip (never an
equality — the store advances hourly, and an equality check would be a
permanently-red gate). It reports the lag in commits, fails on an artifact older
than `--max-lag-days`, and prints `NOT CROSS-CHECKED` — never the word used for
a verified scope — whenever it did not compare against live, **with the reason**.
There are three such reasons and they are different findings: there is no live
scope (the disaster case), the artifacts belong to ANOTHER host (routine — the
two stores are divergent content that merely share scope names, so comparing
them would raise a false data-loss alarm), or this machine's id could not be
read, which makes "another host's" an assumption rather than a measurement.

🔴 **It never runs `git bundle verify`, and neither should you.** Measured: a
bundle with one byte flipped mid-packfile passes it at **rc=0** printing *"The
bundle records a complete history."*, while a clone of the same bundle dies at
rc=128 with `error: index-pack died`. `bundle verify` reads the header and the
prerequisites; it does not walk the pack. The only evidence a backup is
restorable is having restored it.

#### The key's own escrow — `escrow-verify.py`

Everything above decrypts with **one file**. It is escrowed into the self-hosted
Vaultwarden as a Secure Note (name: `age.key — SOPS + analyze-service-index
backups`); `scripts/analyze-service-index/escrow-verify.py` is what re-checks
that copy, so the escrow does not quietly rot into a second thing that only
looks intact.

🔴 **RUN IT ONCE BEFORE YOU UNLOCK.** The order below is deliberate and was
learned twice: `--decrypt-check` reaches MinIO through a LAZY `minio` import, so
a shell without that package unlocked the vault and died afterwards — the master
password spent on a run that was never going to finish. A locked first pass costs
a second and proves the shell, the argv and the host label; `escrow-verify.py`
cannot prompt for a password itself (every `bw` call runs with stdin on
`/dev/null`), so the only spend is your own `bw unlock`.

⚠ The `python3.withPackages(...)` argument is **required** for `--decrypt-check`.
Without it `python3` resolves from the ambient profile, which does not carry
`minio`, and the run refuses with exit 34 naming the interpreter.

```sh
# `bw` is deliberately NOT installed on either host.
nix-shell -p bitwarden-cli jq 'python3.withPackages(p:[p.minio])' --run '
  # 1. locked dry pass: exits 12 VAULT-LOCKED, or 34 if THIS shell is wrong.
  #    Either way no password has been spent.
  python3 scripts/analyze-service-index/escrow-verify.py --decrypt-check --host <host label>
  # 2. only now — the ONE step nothing can automate
  export BW_SESSION="$(bw unlock --raw)"
  # 3. the claim that matters
  python3 scripts/analyze-service-index/escrow-verify.py --decrypt-check --host <host label>
'
… escrow-verify.py --print-plan   # no bw, no network, no key — and it names
                                  # which file the comparison will actually use
```

Two levels, and they are **different claims**. The default compares the note to
the on-disk identity **byte for byte** — which proves the two copies agree, and
proves nothing about either one opening anything. `--decrypt-check` writes the
**escrowed** bytes to a 0600 throwaway identity (shredded and unlinked on every
path out, including the failures) and uses *that* to decrypt the newest real
artifact out of the bucket, via `restore-verify.py`'s own pipeline. The verdict
line says which of the two you got; it never lets "verified" stand for both.

🔴 **Every cause has its own exit code** (`--print-plan` lists all of them), so a
timer can act on the number: `12` locked, `13` not logged in, `17` the note is
GONE, `18` two notes share the name and neither can be trusted, `21` it differs
only in trailing newlines — still probably usable, re-escrow it — `22` it differs
materially. It reports **byte counts and a classification only, never the
differing content**, and it reads the server from `bw config server` at run time
rather than carrying an endpoint into a public repo.

🔴 **Under `--decrypt-check` the nine outcomes mean different things, and only
one of them is about the KEY.** Reading these wrong is how a working
disaster-recovery key gets rotated, or a tampered backup gets waved through:

| code | meaning | what to do |
|---|---|---|
| `34` `DECRYPT-DEPS-MISSING` | this interpreter cannot import what the decrypt path needs — raised BEFORE any `bw` call | re-run under the shell above; nothing was tested and the vault was not contacted |
| `27` `STORE-UNREACHABLE` | could not open the bucket at all | cluster/route fault; nothing was tested |
| `28` `NO-ARTIFACT` | zero objects under the prefix | wrong `--host`/prefix, or the backups are gone — `restore-verify.py` diagnoses which |
| `29` `AGE-MISSING` | `age` is not on PATH | environment fault; says nothing about the escrow |
| `30` `ARTIFACT-UNREADABLE` | failed before the key was used | diagnose the object, not the key |
| `25` `DECRYPT-FAILED` | age wrote **nothing**: wrong key **or** damaged header — **not separable** | try a **different** artifact (`--scope <other>`) with the same escrowed copy: if another opens, the key is fine and this object's header is damaged. **Do not rotate first.** |
| `33` `ARTIFACT-CORRUPT` | age authenticated the header (**the key worked**) then failed the payload | 🔴 **the backup is TAMPERED/CORRUPT/TRUNCATED.** Check the other retained objects. Do not rotate. |
| `31` `ARTIFACT-EMPTY` | age exited **zero** on an empty payload (**the key worked**) | the artifact holds nothing; do not rotate |
| `26` `RESTORE-FAILED` | decrypted fine, the git bundle is bad | artifact fault; do not rotate |

⚠ `27` and `28` are the two most likely to fire in practice (a bucket outage, or
the `--host` prefix trap `restore-verify.py` documents), and neither is a verdict
on the escrow.

Measured (age v1.3.1, many offsets and sizes): a wrong key or a damaged header
leaves **no** plaintext file, while payload corruption and truncation leave one —
because age writes output *before* authenticating the payload. That, plus a
machine-readable cause published by `restore-verify.py`, is what separates the
rows; none of it is parsed out of age's stderr.

⚠ It cannot unlock the vault and will not try: every `bw` call runs with stdin on
`/dev/null`, `--nointeraction`, and a timeout, so an unattended run **fails fast
with exit 12** instead of hanging on an invisible password prompt.

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

## Third-party names + ticket ids in reachable history — ALREADY ADJUDICATED, do not re-raise

> **Read this before proposing a history rewrite of `check-clickup-addressed`.**
> The exposure below is real, the current tree is clean, and the decision NOT to
> rewrite was made deliberately on 2026-08-23. Re-deriving it costs a session.

**What is there.** A skill migrated into this PUBLIC repo on 2026-08-22 carried
fixtures and a validation history across from a PRIVATE client repo. In reachable
history (not HEAD) that leaves: **two colleagues' real names**, ~**40 real ClickUp
ticket ids**, several **verbatim third-party comment bodies**, and one client
**alert name + threshold**. It is reachable in `git log -p`, in the migration
commits, and in PR diffs on github.com.

**What was done.** HEAD is clean, verified repo-wide: `#731` replaced the fixtures
with synthetic equivalents and added a **ledger gate**
(`scripts/check-clickup-addressed/tests/test_no_real_identifiers.py`) that fails on
any unregistered id-shaped token or fixture author — going green requires writing
the value down and asserting it is invented. `#733` replaced the one remaining name
in `claudedocs/` with roles.

**Why the history is NOT being rewritten.**

- **A rewrite cannot undisclose.** The content was public and fetchable; `filter-repo`
  + force-push + a GitHub support request to expire cached PR views changes what is
  *convenient* to find, not what is out. Treat the values as disclosed.
- **The blast radius is larger than the exposure.** `main` is protected
  (`enforce_admins: true`, `allow_force_pushes: false`), so a rewrite means
  unprotecting it — and this repo routinely carries **200+ worktrees**, two host
  checkouts, and several concurrent agent sessions. Every one would be anchored to
  commits that no longer exist. Concurrent *reads and writes* in this shared clone
  already caused four false gate attributions in a single day.
- **The class is low severity**: no credential, no personal-life detail — colleagues'
  names, opaque ticket ids, and paraphrasable comments about infrastructure work.
  The identifying **pairing** (a name beside a ticket beside their words) is what
  mattered, and that is gone from HEAD.

🔴 **The revisit condition is a DIFFERENT class, not more of this one.** If a
credential, key, or token is found in reachable history, this determination does not
apply — see the Linkerd section above for how that case is handled, and note the
structural gap below: **every content gate reads HEAD only**, so nobody has actually
scanned history for this repo. That scan is worth doing on its own merits; its result
does not change the decision recorded here.

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
