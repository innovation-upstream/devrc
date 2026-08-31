---
name: hetzner
description: "Hetzner Cloud fleet, spend and pricing across accounts."
---

# Hetzner Cloud fleet + pricing

One script, no dependencies, no CLI to install:
`python3 ~/workspace/devrc/scripts/hetzner-fleet.py <verb>`

```bash
hetzner-fleet.py fleet                                     # servers + cost, every account
hetzner-fleet.py pricing --family ccx --arch x86 --min-cores 4 --locations ash,hil,fsn1
hetzner-fleet.py pricing-meta                              # currency, VAT, traffic overage
hetzner-fleet.py create --account <alias> --name N --type ccx23 --location ash \
                        --confirm 'ccx23@ash EUR102.99'    # refuses without the exact string
hetzner-fleet.py resize                                    # refuses by default, on purpose
```

🔴 **Everything is derived live from `api.hetzner.cloud/v1`. Nothing is hardcoded** — prices,
families, locations and traffic allowances have each moved under us. Do not paste a price from
this file or from memory into a recommendation; run `pricing`.

## Tokens

A Hetzner token is scoped to **one project**, so multiple projects means multiple tokens.
Sources, in precedence order:

1. `~/.config/hetzner/tokens` — `alias=token` per line, `#` comments. **The multi-account file.**
2. `~/.hetznertoken` — one bare token, alias `default`.
3. `$HCLOUD_TOKEN` — alias `env`.

`chmod 600` all of them; the script warns if they are group/world readable (the existing one was
`644` when found, 2026-08-31). **Never put a token in this repo — devrc is PUBLIC.**

Scope test without creating anything: `POST /v1/ssh_keys` with `{}` returns **422** for a
read/write token and **403** for a read-only one.

## 🔴 Gotchas — each of these was measured, and each cost time

- **`servers[].location`, NOT `servers[].datacenter`.** The `datacenter` key exists and is
  `None` on this API version; reading it yields `?` for every server and looks like a bug in
  your code. Hit live 2026-08-31.
- **`/v1/datacenters` returns 410 from 2026-10-01.** Do not build on it.
- **A returned price of `0.00` means NOT OFFERED in that location — not free.** This is how you
  discover that `CAX` (ARM) is unavailable in `ash`.
- 🔴 **`CAX` is ARM64.** It is ~6.6× cheaper per core than `CCX` and cannot run x86 container
  builds without emulation. Nix *does* build `aarch64-linux` natively, so an ARM node is not
  automatically useless — but it is EU-only, which means ~150 ms to a US-based homelab instead
  of the measured 49 ms.
- 🔴 **The API price is the CURRENT list rate, which is not necessarily what you are billed.**
  Hetzner grandfathers existing servers through price adjustments (the 2026-06-15 one) — so for
  a server created before a change, `fleet` **overstates** the real cost. It answers "what would
  a new one cost", never "what am I paying".
- 🔴 **Rescaling a grandfathered server reprices it permanently.** Hetzner's own wording: existing
  servers are unaffected *"as long as no rescaling is performed"*. `resize` refuses for this
  reason; do it in the console where the new price is displayed.
- **Traffic overage is unobtainable from Hetzner's HTML** — the figure is client-side rendered
  and missing from the DOM in both EN and DE. Third-party trackers cite nothing. Read
  `price_per_tb_traffic` from `/v1/pricing`. US locations include far less traffic than EU
  (single-digit TB vs a 20–60 TB ladder), so egress is a real cost in `ash`/`hil`.
- 🔴 **There is NO Hetzner Cloud "fair use" CPU policy. Do not re-derive one.** The page that
  surfaces in search is for **web hosting**, not Cloud. Cloud terms forbid exactly three things:
  crypto mining, network scanning, spoofed source IPs. The widely-quoted *"100% of your vCPU
  100% of the time"* line is not on Hetzner's current site. Sustained 100% CPU on shared vCPU is
  **not prohibited** — the real exposure is silent throttling to baseline. Choose `CCX`
  (dedicated) for *predictable* wall-time if the price justifies it, never out of compliance
  fear. This claim was asserted wrongly in this repo on 2026-08-31 and then retracted.
- **No credible Hetzner MCP server exists.** Do not hand a read/write cloud token to an
  unmaintained third-party MCP. The REST API is the interface.
- **`hcloud` CLI is not installed** and is not needed. If you ever want it: `nix-shell -p hcloud`.
  Its `context` mechanism is the idiomatic multi-project story if this ever outgrows the script.

## 🔴 The honesty rule for this skill

`fleet` reports **only the projects its tokens reach**. A project with no token is invisible and
is **not** reported as missing — there is no API call that enumerates an account's projects. So
never present its total as "the fleet" without saying which accounts were read. The script prints
the account list and an unreachable list for exactly this reason; **relay both.**

Known as of 2026-08-31: the `default` token reaches 3 servers in `ash` (`k0s-01`,
`vetr-k8s-app-1/2`). It does **not** reach the production k0s cluster at `5.161.118.55`
(`diffsona`, `tryonhaulcentral-k8s`) — that is a separate account whose token is not on this box.

## Verify

```bash
python3 ~/workspace/devrc/scripts/hetzner-fleet.py fleet          # expect a per-account subtotal
python3 ~/workspace/devrc/scripts/hetzner-fleet.py create --account default --name x \
  --type ccx23 --location ash --confirm wrong                     # expect 🔴 REFUSED + the right string
```

The refusal path is the one to check after any edit: it is what stands between a typo and a
billed server.
