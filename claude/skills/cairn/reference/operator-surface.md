# cairn — the operator surface

Durable facts you verify against. **Nothing here restates a number a command can
print.** `cairn doctor` answers reachability, credential, stamp, counts and scope
visibility; this file covers what has no command.

## The pieces

| thing | where | what it does |
|---|---|---|
| the pod | k8s ns `subsystem-store` | the canonical datastore. Serves `GET /api/v1/{recall,search,snapshot}` and accepts the two write routes |
| the client | `scripts/cairn` | syncs a local cache and runs the **unmodified** local reader against it |
| the resolver | `scripts/lib/subsystem_read_store.py` | the one answer to "where does this host read from" |
| the seeder | `scripts/subsystem-store-api/seed.sh` | pushes a local tree into the pod's PVC |
| the acceptance check | `scripts/subsystem-store-api/verify-byte-identity.sh` | compares pod bytes against a local store, scope by scope |
| the image | `scripts/subsystem-store-api/build-push.sh` | `build-push.sh <version>` — the tag is an argument and has **no default** |
| the cutover | `scripts/cairn-cutover.py` | dry-run by default; owns the freeze/unfreeze of the pre-cutover mirror |
| the backup | CronJob `subsystem-store-backup` in the same namespace | daily 03:45 UTC (homelab-infra) |

## 🔴 The token file is read ONCE, at startup

`server.py::load_tokens` runs in `main()` and there is **no SIGHUP, no reload,
no watch**. Editing the Kubernetes secret changes nothing about the running
process — the pod is still authorising against the rows it parsed when it
started.

Two consequences, both measured and recorded in
`claudedocs/handoff-cairn-phase3.md`:

* A malformed row is `EXIT_CONFIG` (**78**, `sysexits.h EX_CONFIG`) and the
  process refuses to start. With `strategy: Recreate` at `replicas: 1` there is
  no second pod, so **the store stays DOWN** — it does not fall back to the old
  rows and it does not fall back to no auth.
* Replace the pod with **`kubectl delete pod`, not `rollout restart`**. Under
  `Recreate` the latter costs two rollouts (see homelab-infra's `CLAUDE.md`),
  i.e. two outages where one was needed.

So: edit the secret, then delete the pod, then re-run `cairn doctor` and read
the `pod` check. A `PROBLEM` there naming HTTP 401/403 is the credential; an
`UNMEASURED` naming a connection failure is the pod not coming back.

⚠ **A 403 from this host's edge is not a token rejection.** Measured against the
live host with the same token: curl's default User-Agent → 200, urllib's default
`Python-urllib/3.12` → **403**, empty UA → 200. The 403 comes from the edge, and
it arrives looking like a bad token *and* like the store being down.
`_apply_standard_headers` in `scripts/cairn` is what keeps every request out of
that trap; a new HTTP caller that skips it will re-learn this the hard way.

## 🔴 `seed.sh` OVERWRITES a shared entry — it does not merge

The push is `rsync -a --delete` SOURCE→STAGE, then `tar` STAGE→pod. The extract
**adds and overwrites but never deletes**, so a bullet that was appended through
the API and exists only in the served copy is replaced by the seeding host's
older copy of that file. That is why the cutover (`cairn-cutover.py`) had to run
*before* any further re-seed: after it, the hosts hold caches of the pod and the
same push has nothing unique left to destroy.

`seed.sh` requires `--store` and refuses to default it. Its push verdict is a
**containment check on NAMES** ("did everything I staged land?"), not an equality
of counts — a second host's entries legitimately sit in the destination.

## 🔴 `verify-byte-identity.sh` has a MEASURED one-directional coverage gap

It enumerates the scopes to compare **from the LOCAL store**, so it can never
see a scope the pod holds and this host does not. Measured 2026-09-02: local 16
scopes / 141 entries, pod 23 / 189 — **7 pod-only scopes holding 48 entries,
25% of the served store, never compared.** The script prints this itself on
every run (`verify: COVERAGE — … This sweep is ONE-DIRECTIONAL …`), so a
`scopes=16 pass=16 fail=0` line is complete coverage *of what it asked for* and
says nothing about the rest.

It also refuses a zero: `0 scopes found under <store>` exits non-zero, because a
comparison over zero scopes passes trivially.

The complementary direction — scopes on this disk the pod's answer did **not**
carry — is `cairn doctor`'s `token-scopes` check. Neither alone is the answer.

## 🔴 A scope invisible to your token is byte-identical to one that never existed

`server.py` closes an enumeration oracle by filtering at the index
(`rc.load_store`), so a refused scope and an absent scope produce the same bytes
on every read route and the same 404 on every write route. **No client can tell
them apart, and none should claim to.** `cairn doctor` reports the set and names
both readings.

A scope is created on the pod by whatever first writes an entry into it. The
token allowlist is static, in the secret, and read once (above) — so a scope can
exist in the store and be invisible to your credential until somebody edits the
secret and replaces the pod.

The store-wide `entry-files=` count in `X-Store-Snapshot` is **not** filtered by
the allowlist — a deliberate, documented residual count leak — which is the only
client-side evidence that such entries exist. `cairn doctor` reads it.

### 🔴 Telling ABSENT from REFUSED — no client can, an operator can, two ways

`doctor`'s `token-scopes` PROBLEM names both readings and stops there, because on
the wire they are the same bytes.

⚠ **This section used to say the two remedies are OPPOSITE — "seed the scope" vs
"widen the allowlist" — and that is RETRACTED.** Under the real mechanism (below)
the allowlist edit is the remedy in BOTH readings: an allowlisted scope with no
directory is created by `cairn create`. Settling absent-vs-refused is still worth
doing — it tells you whether to expect existing entries back — but it no longer
selects between two different fixes.

1. **The count gap, off `doctor` alone.** `entry-files=` (store-wide, unfiltered —
   `snapshot_freshness` walks the root and takes no token) minus `X-Store-Entries`
   (your slice). **Gap 0 ⇒ nothing on the pod is hidden from you ⇒ the scope is
   ABSENT.** ⚠ This line used to end "…and widening the allowlist would change
   nothing" — RETRACTED: widening it is precisely what lets `cairn create` bring
   the scope into existence.
2. **The pod's own disk**, which settles it outright:
   `kubectl -n subsystem-store exec deploy/subsystem-store-api -- ls -1 /data`

MEASURED 2026-09-11: `entry-files=237`, `X-Store-Entries=237`, and `find /data
-mindepth 2 -maxdepth 2 -name '*.md' | wc -l` = **237** — three numbers, one
store, gap zero. The token row's allowlist enumerated exactly the 24 scopes the
PVC held, so it was refusing nothing. `civitai-app-requests` and
`civitai-developer-docs` read as invisible because they had **never been seeded**,
not because access was lost.

🔴 **THE ALLOWLIST EDIT IS THE WHOLE FIX — SEEDING IS NOT REQUIRED.** An earlier
version of this section said "editing the allowlist without seeding changes
nothing at all, because the index is built from what is ON DISK". That is true of
READS and **false for `cairn create`**: `create_entry` does
`path.parent.mkdir(exist_ok=True)`, and
`test_a_scopes_FIRST_entry_creates_the_directory` pins **201** for an allowlisted
scope with no directory. So the sequence is:

1. add the scope to the token row in the secret,
2. replace the pod (the token file is read ONCE — see above),
3. `cairn create --scope <new> --ref <ref> --file <path>` → **201**.

`seed.sh` is for pushing a whole local tree and carries the overwrite hazard
above; reach for it when you have many entries, not to bring one scope into
existence.

## The freeze

`cairn-cutover.py --freeze --apply` chmods the pre-cutover mirror's entry files
`0444` and records every prior mode; `--unfreeze` restores them and refuses
without that ledger. ⚠ The backup precondition lives in the P0 block, which
`--freeze` skips — so `--freeze --apply` chmods the whole tree with no backup
gate in front of it.

`cairn doctor`'s `frozen-mirror` check is what tells you whether the freeze is
still complete. Measured on the workbench 2026-09-03: **6 of 160 entry files
were mode 644**, in a mirror that looked frozen. A write to one of those lives
on one host and the pod never sees it.
