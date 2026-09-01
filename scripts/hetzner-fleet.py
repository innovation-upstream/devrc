#!/usr/bin/env python3
"""Hetzner Cloud fleet inventory, spend and pricing across MULTIPLE accounts.

🔴 Everything here is DERIVED LIVE from the API. Nothing about prices, server
families, locations or traffic allowances is hardcoded, because all four have
moved under us before (see the skill body for the dated incidents).

Read verbs are safe. Write verbs refuse unless an exact confirmation string
matching the computed monthly cost is supplied.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.hetzner.cloud/v1"


# --------------------------------------------------------------------------
# token discovery
# --------------------------------------------------------------------------
def load_tokens() -> tuple[list[tuple[str, str]], list[str]]:
    """Return ([(alias, token)], [notes]).

    Sources, in order. A multi-account file wins over the single-token file
    when both name the same alias.
      1. ~/.config/hetzner/tokens  -- lines of `alias=token`, `#` comments
      2. ~/.hetznertoken           -- one bare token, alias "default"
      3. $HCLOUD_TOKEN             -- alias "env"
    """
    out: dict[str, str] = {}
    notes: list[str] = []

    multi = Path.home() / ".config" / "hetzner" / "tokens"
    if multi.is_file():
        if multi.stat().st_mode & 0o077:
            notes.append(f"⚠ {multi} is group/world-readable — chmod 600 it")
        for raw in multi.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            alias, _, tok = line.partition("=")
            alias, tok = alias.strip(), tok.strip()
            if alias and tok:
                out[alias] = tok
    else:
        notes.append(f"no {multi} — only single-token sources were read")

    single = Path.home() / ".hetznertoken"
    if single.is_file():
        if single.stat().st_mode & 0o077:
            notes.append(f"⚠ {single} is group/world-readable — chmod 600 it")
        tok = single.read_text().strip()
        if tok:
            out.setdefault("default", tok)

    if os.environ.get("HCLOUD_TOKEN"):
        out.setdefault("env", os.environ["HCLOUD_TOKEN"].strip())

    return sorted(out.items()), notes


def api(token: str, path: str, method: str = "GET", body: dict | None = None) -> tuple[dict, int]:
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    def _obj(raw: bytes | str) -> dict:
        """Always hand back a dict. A non-object body (or garbage) must not
        become a `.get` crash three call-frames away from the HTTP error."""
        try:
            v = json.loads(raw or "{}")
        except Exception:
            return {"error": {"message": f"non-JSON response: {str(raw)[:120]!r}"}}
        return v if isinstance(v, dict) else {"error": {"message": f"non-object response: {type(v).__name__}"}}

    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return _obj(r.read()), r.status
    except urllib.error.HTTPError as e:
        try:
            return _obj(e.read()), e.code
        except Exception:
            return {}, e.code
    except Exception as e:  # network, DNS, timeout
        return {"error": {"message": str(e)}}, 0


def paged(token: str, path: str, key: str) -> list[dict]:
    items, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        d, st = api(token, f"{path}{sep}page={page}&per_page=50")
        if st != 200:
            raise RuntimeError(f"{path} -> HTTP {st}: {d.get('error',{}).get('message','')}")
        items.extend(d.get(key, []))
        nxt = (d.get("meta", {}).get("pagination", {}) or {}).get("next_page")
        if not nxt:
            return items
        page = nxt


def monthly_gross(server_type: dict, location: str) -> float:
    """Current list price for this type at this location. 0.0 => not offered.

    🔴 This is the CURRENT rate. Hetzner grandfathers existing servers through
    price changes (2026-06-15 adjustment), so for a server created before such a
    change this OVERSTATES what is actually billed. It is the right number for
    'what would a new one cost', never a billing statement.
    """
    for p in server_type.get("prices") or []:
        if p.get("location") == location:
            try:
                return float(p["price_monthly"]["gross"])
            except (KeyError, TypeError, ValueError):
                return 0.0
    return 0.0


# --------------------------------------------------------------------------
# verbs
# --------------------------------------------------------------------------
def cmd_fleet(args) -> int:
    tokens, notes = load_tokens()
    if not tokens:
        print("NO TOKENS FOUND. Expected ~/.config/hetzner/tokens, ~/.hetznertoken, or $HCLOUD_TOKEN.", file=sys.stderr)
        return 2

    grand, unreachable = 0.0, []
    print(f"{'account':10s} {'server':24s} {'type':8s} {'cores':>5s} {'RAM':>5s} {'arch':5s} {'loc':5s} {'status':8s} {'IPv4':16s} {'EUR/mo':>9s}")
    print("-" * 108)
    for alias, tok in tokens:
        try:
            servers = paged(tok, "/servers", "servers")
        except Exception as e:
            unreachable.append(f"{alias}: {e}")
            continue
        sub = 0.0
        for s in servers:
            st = s.get("server_type") or {}
            # 🔴 servers[].location — NOT .datacenter, which is null on this API.
            loc = (s.get("location") or {}).get("name", "?")
            m = monthly_gross(st, loc)
            sub += m
            pn = s.get("public_net") or {}
            ip = ((pn.get("ipv4") or {}) or {}).get("ip") or "-"
            print(f"{alias:10s} {s.get('name','?'):24s} {st.get('name','?'):8s} "
                  f"{st.get('cores','?'):>5} {str(st.get('memory','?'))+'G':>5s} "
                  f"{st.get('architecture','?'):5s} {loc:5s} {s.get('status','?'):8s} {ip:16s} {m:9.2f}")
        if servers:
            print(f"{alias:10s} {'-- subtotal':24s} {'':8s} {'':5s} {'':5s} {'':5s} {'':5s} {'':8s} {'':16s} {sub:9.2f}")
        grand += sub

    print("-" * 108)
    print(f"{'TOTAL (current list rates)':>98s} {grand:9.2f}")
    print(f"\naccounts read: {', '.join(a for a, _ in tokens)}")
    if unreachable:
        print("🔴 UNREACHABLE (counted as ZERO above — the total is a FLOOR, not a fleet):")
        for u in unreachable:
            print(f"   {u}")
    print("🔴 This covers ONLY the projects these tokens reach. A Hetzner token is scoped to ONE")
    print("   project, so absent projects are invisible here and are NOT reported as missing.")
    print("⚠ Prices are CURRENT list rates. Servers created before a price adjustment are")
    print("  grandfathered and may bill LESS than shown. This is not a billing statement.")
    for n in notes:
        print(f"  {n}")
    return 0


def cmd_pricing(args) -> int:
    tokens, _ = load_tokens()
    if not tokens:
        print("NO TOKENS FOUND.", file=sys.stderr)
        return 2
    alias, tok = tokens[0]
    types = paged(tok, "/server_types", "server_types")
    locs = [l["name"] for l in paged(tok, "/locations", "locations")]
    want = [l for l in (args.locations.split(",") if args.locations else locs) if l in locs]

    print(f"pricing via account '{alias}' (list rates are account-independent)")
    print(f"filter: cores>={args.min_cores} arch={args.arch or 'any'} family={args.family or 'any'}\n")
    hdr = f"{'type':9s} {'cores':>5s} {'RAM':>6s} {'disk':>6s} {'arch':5s} " + " ".join(f"{l:>9s}" for l in want)
    print(hdr)
    print("-" * len(hdr))
    for t in sorted(types, key=lambda x: (x.get("cores", 0), x.get("name", ""))):
        if t.get("cores", 0) < args.min_cores:
            continue
        if args.arch and t.get("architecture") != args.arch:
            continue
        if args.family and not t.get("name", "").startswith(args.family):
            continue
        row = f"{t['name']:9s} {t.get('cores',0):5d} {t.get('memory',0):5.0f}G {t.get('disk',0):5d}G {t.get('architecture','?'):5s} "
        row += " ".join(f"{monthly_gross(t,l):9.2f}" for l in want)
        print(row)
    print("\n0.00 means NOT OFFERED in that location (it is not a free tier).")
    print("Traffic overage: read `price_per_tb_traffic` from /v1/pricing — Hetzner does not")
    print("publish it in readable HTML, and third-party figures are uncited.")
    return 0


def cmd_pricing_meta(args) -> int:
    tokens, _ = load_tokens()
    if not tokens:
        print("NO TOKENS FOUND.", file=sys.stderr)
        return 2
    d, st = api(tokens[0][1], "/pricing")
    if st != 200:
        print(f"/pricing -> HTTP {st}", file=sys.stderr)
        return 1
    p = d.get("pricing", {})
    print(f"currency: {p.get('currency')}  vat_rate: {p.get('vat_rate')}")
    ppt = p.get("price_per_tb_traffic")
    print(f"price_per_tb_traffic (legacy global field): {json.dumps(ppt)}")
    for key in ("server_types", "primary_ips", "volume", "floating_ip"):
        if key in p:
            print(f"  {key}: present")
    if args.raw:
        print(json.dumps(p, indent=2)[:4000])
    return 0


def cmd_create(args) -> int:
    """Provision. Refuses without an exact cost confirmation."""
    tokens, _ = load_tokens()
    sel = dict(tokens).get(args.account)
    if not sel:
        print(f"unknown account alias {args.account!r}; known: {[a for a,_ in tokens]}", file=sys.stderr)
        return 2
    types = {t["name"]: t for t in paged(sel, "/server_types", "server_types")}
    t = types.get(args.type)
    if not t:
        print(f"unknown server type {args.type!r}", file=sys.stderr)
        return 2
    m = monthly_gross(t, args.location)
    if m == 0.0:
        print(f"🔴 {args.type} is NOT OFFERED in {args.location}. Refusing.", file=sys.stderr)
        return 3
    expect = f"{args.type}@{args.location} EUR{m:.2f}"
    if args.confirm != expect:
        print("🔴 REFUSED — confirmation string does not match the computed cost.", file=sys.stderr)
        print(f"   this would create: {args.type} in {args.location}, {t.get('cores')}c/"
              f"{t.get('memory')}G/{t.get('disk')}G, arch={t.get('architecture')}", file=sys.stderr)
        print(f"   re-run with exactly:  --confirm '{expect}'", file=sys.stderr)
        return 3
    body = {"name": args.name, "server_type": args.type, "location": args.location,
            "image": args.image, "start_after_create": True}
    if args.ssh_key:
        body["ssh_keys"] = [args.ssh_key]
    d, st = api(sel, "/servers", method="POST", body=body)
    if st not in (200, 201):
        print(f"create failed HTTP {st}: {d.get('error',{}).get('message','')}", file=sys.stderr)
        return 1
    s = d.get("server", {})
    print(f"created id={s.get('id')} name={s.get('name')} "
          f"ip={((s.get('public_net') or {}).get('ipv4') or {}).get('ip')} at EUR{m:.2f}/mo")
    return 0


def cmd_resize(args) -> int:
    """🔴 Rescaling a grandfathered server moves it to CURRENT rates, permanently."""
    if not args.accept_reprice:
        print("🔴 REFUSED. Hetzner's 2026-06-15 price adjustment grandfathers EXISTING servers", file=sys.stderr)
        print("   'as long as no rescaling is performed'. A resize therefore silently and", file=sys.stderr)
        print("   PERMANENTLY moves this server to current rates — which may be much higher.", file=sys.stderr)
        print("   Check what it bills today before proceeding, then pass --accept-reprice.", file=sys.stderr)
        return 3
    print("not implemented — deliberately. Do this in the console where the new price is shown.", file=sys.stderr)
    return 4


def main() -> int:
    ap = argparse.ArgumentParser(description="Hetzner Cloud fleet/pricing across multiple accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("fleet", help="servers + current-rate cost across all tokens")

    p = sub.add_parser("pricing", help="server types and per-location monthly price")
    p.add_argument("--min-cores", type=int, default=1)
    p.add_argument("--arch", choices=["x86", "arm"], default=None)
    p.add_argument("--family", default=None, help="name prefix, e.g. ccx / cpx / cax / cx")
    p.add_argument("--locations", default=None, help="comma list, e.g. ash,hil,fsn1")

    p = sub.add_parser("pricing-meta", help="currency, VAT, traffic overage from /v1/pricing")
    p.add_argument("--raw", action="store_true")

    p = sub.add_parser("create", help="provision a server (requires exact --confirm)")
    p.add_argument("--account", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--image", default="debian-12")
    p.add_argument("--ssh-key", default=None)
    p.add_argument("--confirm", default="", help="must equal 'TYPE@LOC EUR<monthly>'")

    p = sub.add_parser("resize", help="REFUSES by default — resizing reprices grandfathered servers")
    p.add_argument("--accept-reprice", action="store_true")

    args = ap.parse_args()
    return {
        "fleet": cmd_fleet, "pricing": cmd_pricing, "pricing-meta": cmd_pricing_meta,
        "create": cmd_create, "resize": cmd_resize,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
