# Handoff: laptop-airvpn-tunnel — 2026-09-21

## Run this first — the index, one command
```bash
cairn recall --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading. Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
The laptop runs its OWN host AirVPN WireGuard tunnel (replacing PR #1839's mirror of the
workbench's pill), default-OFF, click-to-toggle from its own bar pill, with a ROAMING
killswitch whose derived LAN is correct on any network — and nebula's transport must be
unaffected while the tunnel is up.
- **closing-condition:** `check` — with the tunnel up: `curl -s https://ipinfo.io/json` shows the
  AirVPN exit; `ssh zach@10.42.0.30 'true'` works (nebula intact); the pill renders `US`
  (verified, not `US?`) after one post-connect writer poll; and
  `journalctl -u 'nebula@mesh' --since <up-time> | rg -c 'Failed to write outgoing packet|Failed to send handshake'`
  counts ONLY `listener is IPv4, but writing to IPv6 remote` lines (see open investigation)
  — no MTU/routing churn.

## State now
🔴 **THIS ARC IS CLOSED — the closing-condition above was MET and VERIFIED 2026-09-30, the
first time it was runnable (it requires the tunnel up, which needs sudo).** Evidence, all
measured with the tunnel up:

| closing-condition leg | result |
|---|---|
| `curl -s https://ipinfo.io/json` shows the AirVPN exit | ✅ `CA / AS11878 tzulo, inc.` — not the home ISP |
| `ssh zach@10.42.0.30 'true'` works (nebula intact) | ✅ `nebula-ok` |
| pill renders verified, not `US?` | ✅ `up=True verdict=verified server=Kornephoros cc=ca` |
| nebula journal: no MTU/routing churn | ✅ **0** `Failed to write outgoing packet|Failed to send handshake` lines |

⚠ **ONE LITERAL DEVIATION, recorded rather than papered over:** the condition says the pill
renders `US`. It renders `CA`, because the laptop is on a Canadian server (`Kornephoros`),
not the `america3` endpoint the condition assumed. The INTENT — a verified country code with
no `?` unverified marker — is met; the literal country string in the condition is stale.

- **Split-tunnel verified while up:** with `P` set at run time to any public address
  (`P=$(getent ahostsv4 one.one.one.one | awk '{print $1; exit}')` — 🔴 `ip route get` needs an
  ADDRESS, a hostname errors), `ip route get "$P" uid 991` (nebula) → `dev wlp170s0`;
  `uid 1000` → `dev airvpn`. `ip rule 500: uidrange 991-991 lookup main` is what exempts nebula,
  and `ip rule 5210: fwmark 0x80000` exempts tailscale. 🔴 **Consequence worth stating: bringing
  this tunnel up does NOT change either overlay's egress path, so it cannot be used to test any
  hypothesis about the laptop's outbound path.** One session proposed exactly that and was wrong.
- Landed on `main` as `37cca5b7` (PR #1937, squash — verified by CONTENT, not ancestry).
- 🔴 The mesh-blackout work moved to `claudedocs/handoff-mesh-blackouts-laptop-workbench.md` on
  2026-09-30. It is a DIFFERENT arc, still open, and it is what pushed this document to its
  65,536 B ceiling.
- Deploy/verify: nothing left changed by the closing-condition run except the tunnel itself,
  which was brought UP deliberately and left up at the operator's request. Designed default is
  OFF: `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo down`.
- 🔴 CARRIED: mosh DOES NOT WORK YET — no host runs `mosh-server`; `programs.mosh.openFirewall`
  DEFAULTS TRUE (must be false or it opens 1001 UDP ports on every interface incl. WAN).

## Open investigations — live diagnosis state
🔴 **The mesh-blackout investigation moved out on 2026-09-30 to**
`claudedocs/handoff-mesh-blackouts-laptop-workbench.md` — that arc is OPEN and is a
DIFFERENT arc from this one. Everything below is tunnel-specific.

### Pill verdict path — `US?` (up, unverified) vs `US` (verified)
- as-of: 2026-09-21
- **Symptom + exact repro:** with the tunnel up pre-#1847, `~/.cache/bar-status/airvpn.json` carried `verdict=unknown server=None cc=None` → pill `US?`. Root cause: the manifest stored no `country_code` (0/255 rows) and the poller reads only the manifest. FIXED (#1847): 254/255 rows now carry `country_code`.
- **Observed (with values):** `server=Chamaeleon cc=None verdict=unknown handshake_age=21` before the fix; post-fix lookup returns Chamaeleon with `country_code: "us"`.
- **Ruled out:** writer env PATH (was a REAL second bug, fixed in #1846 — `sudo -n airvpn-sudo status` died rc 127 `env: bash: not found` under the unit's PATH) — `via: measurement`.
- **Next probe:** on the next tunnel-up writer poll (~60s after Connect): `python3 -c "import json; d=json.load(open('/home/zach/.cache/bar-status/airvpn.json')); print(d['verdict'], d['server'], d['country_code'])"` — expect `verified Chamaeleon us`; pill `US`. **Not yet observed while up** (the 22:18 reconnect predates no manifest issue — it simply wasn't re-read).


### This doc leaked the home public IP to a PUBLIC repo; scrubbed at HEAD, NOT revoked
as-of: 2026-09-23
🔴 **WRITTEN BY A DIFFERENT SESSION than the one that authored this doc**, and deliberately
appended here rather than in a new doc: the leak came out of THIS effort, so the next
person working the tunnel is the one who must not put the value back. Only
append-bucket sections are touched — this arc's `State now`, `Next steps` and
`How to verify` are untouched and still belong to its author.

- **Symptom + exact repro:** `main`'s `tekton/devrc-pytests` leg had been RED since
  `3bd6fc22` (the commit that landed this doc), on
  `test_no_unallowlisted_public_ip_literal_is_committed`. Reproduce with
  `nix develop $DEVRC -c python3 -m pytest scripts/tests/test_no_public_ips.py -q`
  against any tree at or after that commit.
- **Observed (with values):** the repo is `visibility: PUBLIC`, `isPrivate=false`; the
  HEAD copy of this file answered **200** unauthenticated from
  `raw.githubusercontent.com`; **9** commits carry the value per `git log -S … --all`,
  the oldest well before this doc and two of them `untracked files on …` (stash-shaped),
  so it reached history by more than one route. The file was **NOT** in `PENDING_SCRUB`,
  so this was a NEW leak the gate blocked rather than tracked debt. `via: measurement`
- **Ruled out — that it was CI noise.** The measured red rate on this repo is ~42%, and
  that is exactly the trap. A control run on a PRISTINE `origin/main` worktree failed the
  SAME test identically, which is what proved it real and not caused by the PR under
  test. 🔴 On that same PR an EARLIER red was genuinely the PR's fault — two reds,
  opposite verdicts, only the control separated them. `via: measurement`
- **Ruled out — that scrubbing HEAD revokes the disclosure.** It does not. The four
  content gates read `git ls-files` and are blind to git history (devrc `CLAUDE.md` →
  `SECRETS.md`, "Dead credentials in reachable history"). `via: doc`
- **Ruled out — a history rewrite as the remedy.** Considered and DECLINED by the
  operator: it force-pushes a public repo, breaks every clone and fork, and does not
  un-index an address that is already public. `via: change`
- **Leading hypothesis:** rotating the address is the only action that actually revokes
  it. Whether that is worth doing is a judgement about the operator's ISP and threat
  model, not a measurement.
- **Next probe:** 🔴 **REOPENED 2026-09-23 — this block said "it is closed" and that was
  wrong within 24 hours.** #1853 merged as `1c7ad1b9` and the gate went green; the very
  next commit to this file, `a6e98a3d`, put the SAME value back plus two more, and `main`
  went red again on the same test. Hash-compared across all three revisions of this file.
  The ACTION is now `devrc#1861`. 🔴 **The diagnosis is not "someone was careless": it is
  that the prohibition lives in PROSE inside the very document people append to, and prose
  does not gate.** The open question is whether anything cheaper than the test can make the
  rule reachable at APPEND time — the gate catches it only after a push, on CI. Until that
  is answered, treat this block as OPEN, not as history.

<!-- as-of: 2026-09-23 -->

### mosh's stated benefit was WRONG, and the correction is smaller than the claim
- 🔴 **EVICTED 2026-09-30 to `claudedocs/archive/handoff-laptop-airvpn-tunnel-2026-09-30.md`** — CLOSED: the claim was settled and corrected; the corrected wording lives in `nix/pkgs/default.nix`. Read it there before re-deriving anything; the eliminations it records still stand.


## Next steps (ranked)
🔴 **NONE — THIS ARC IS CLOSED.** Its closing-condition was met 2026-09-30; see
`State now`. Outstanding mesh-blackout work is a different arc, linked above.

## Defects (batched)
- Workbench stale `airvpn-updown` copy — operator, ON THE LAN: `sudo install -m0755 ~/workspace/devrc/scripts/airvpn-updown /etc/nixos/i3blocks-scripts/airvpn-updown`, pair with killswitch re-test per `claude/skills/bar/reference/airvpn.md`.
- Nebula gateway v6-remote noise (config in homelab-talos).
- `refresh-airvpn-servers --from-github` fallback rotted; re-run from a qBit-pod host and bake country_code.
- `i3status-airvpn` no-country-code fallback abbreviates full country NAME — one-line fix if ever seen again.
- The flap watcher is still a plain background process, not a unit — v3 is pid 2309522 and a suspend/reboot ends it silently, as v2's predecessor did (3 days of coverage lost).

## Gotchas / decisions / dead-ends
- 🔴 The laptop has NO systemd-resolved: NetworkManager `dns=none` + local dnsmasq (`127.0.0.1` → public resolver). wg-quick ABORTS on the conf's `DNS =` line (`resolvconf` fails, interface torn down in the same invocation — measured on first connect). The apply script now strips that line when resolved is absent. Do NOT re-add DNS to the laptop conf.
- The laptop's wg conf endpoint is a HOSTNAME (`america3.vpn.airdns.org:1637`); AirVPN America servers use distinct entry (.10) vs exit (.16) IPs — that is why the verdict needed baked `country_code` (the workbench's Canada server happens to exit on its entry IP and verified without it).
- `sudo` from the units logs `PWD=/` — a root-privileged `airvpn-sudo down` with `PWD=/` in the journal was the OPERATOR's pill-menu Disconnect (session-3.scope), not a service or test. Don't misread that log line again.
- The pill's `?` on `US?` is the UNVERIFIED marker (exit IP ≠ entry IP and no server cc), NOT the stale marker — two different `?`s in one block's grammar.
- (carried) Laptop has NO systemd-resolved — do NOT re-add `DNS` to the laptop wg conf.
- (carried) The pill's `?` on `US?` is the UNVERIFIED marker, not the stale marker.
- 🔴 **Widening a guard's regex to admit one spelling admitted a WAN hole.** #1865 round 2 widened an idempotence check so `programs.mosh.enable` would match; round 4 found it then accepted a config with `openFirewall` ABSENT — which defaults TRUE and opens 1001 UDP ports on every interface — reported `ALREADY APPLIED`, printed an affirmative "openFirewall is set false" banner, and ran the switch. Fix the CLASS, not the spelling the audit named.
- (carried) Laptop has NO systemd-resolved — never re-add `DNS` to the laptop wg conf.

- 🔴 The laptop's `configuration.nix` splits `imports =` and `[` across two lines — the apply script's awk keys on the `];` CLOSER (POSIX classes), not the opener (#1845), and parse-validates the edit.
- `--block` mirror machinery from #1839 was REMOVED, not left dead; `i3status-airvpn` stays in RELAY_BLOCKS so the `wb` rollup still carries the workbench tunnel's alarms on the laptop.

- ⚠ **A PLACEHOLDER IN THIS DOC IS GATE COMPLIANCE, NOT REVOCATION — and for one of the
  three it is not even a removal from HEAD.** The value written `<hetzner-lighthouse-ip>`
  is still committed at HEAD in `scripts/airvpn-updown` and
  `scripts/claude-hooks/tests/test_guard_core.py`, deliberately, as tracked `PENDING_SCRUB`
  debt — the killswitch one is split into its own PR because moving it is a runtime change.
  The other two are genuinely gone from HEAD. None of the three is revoked: the gate reads
  `git ls-files` and is blind to history.
- ⚠ **The Cloudflare resolver's ALLOWLIST pin for this doc is DELETED** — the literal left
  the file when the mesh-flap rewrite replaced `How to verify` wholesale, and the gate's
  second leg fails a pin that matches nothing rather than leaving a rubber stamp. Note what
  that rewrite cost: **seven command lines and the killswitch escape hatch**, not just one
  probe. `How to verify` below is the pre-rewrite block restored, with the resolver reached
  through `getent` at run time so no literal is needed — 🔴 `ip route get one.one.one.one`
  does NOT work (`Error: any valid prefix is expected`); that argument must be an address,
  which is exactly why the runtime-variable remedy exists.
- 🔴 **THE EXPLANATION OF A LEAK IS ONE OF THE PLACES THE LEAK SPREADS TO.** The first PR
  body for #1853 quoted the address and was REFUSED by the `bash-guard` PreToolUse hook;
  the first commit message had the same defect and had already been PUSHED, and was
  amended + force-pushed (`--force-with-lease`, unmerged branch, no PR yet). The gate's
  own `PENDING_SCRUB` header warns about this for the tracking file — it is equally true
  of a commit message or a PR body on a public repo. The guard was the only thing that
  caught it.
- 🔴 **The ALLOWLIST is keyed on `(relpath, value)`, NEVER the value alone** — the gate's
  header records that a value-only exemption is repo-wide by construction and once let an
  audit mutant through. And never write the offending literal into the gate or its ledger:
  `PENDING_SCRUB` is pinned by COUNT precisely so the tracking file does not become a
  fresh copy of the leak.
- ⚠ **A green run of that gate is a claim about `git ls-files` only** — blind to history
  and to anything gitignored. Not a clean repo.
- ⚠ **The same line also carries a private `192.168.1.1`.** RFC1918, correctly not
  flagged, not a disclosure — noted so nobody "fixes" it and widens the diff.
- ⚠ **A permanently-red gate is the real defect here, not the IP.** It had been red for
  days; the `main-green-check` deadman REPORTS and never fixes, so nothing forced it to be
  cleared, and the standing cost is that every session learns to click through a red.

- 🔴 **`gh pr view --json headRefOid` reporting an old sha is not always API lag — the PR may have MERGED at that sha.** This session diagnosed lag, was wrong, and nearly re-merged a merged PR. Read `state` before theorising about staleness.
- ⚠ **`nixos-rebuild switch` on a host whose `/etc/nixos` has no flake pulls the ROOT CHANNEL.** Measured on the workbench: `dry-build` reported **444 to build / 1420 to fetch** against a system last built 5 days earlier on `nixpkgs-unstable`. A staged script that ends in a bare `switch` is a full system update, not its own delta — which is why #1865's script now stops before switching by default.
- ⚠ The `bash-guard` hook cannot resolve `git -C $VAR`; it judges the caller's cwd instead. Pass an absolute path, or assign the variable in the same command.

- 🔴 **A COMMENT THAT SPELLS AN UNGUARDED FACT IS A CLAIM THAT ROTS, AND THIS REPO ALREADY WROTE THAT DOWN.** `nix/pkgs/tools/default.nix` states it for the opencode entry (only the version is spelled, because only the version is guarded by a test). The mosh comment's first draft spelled four unguarded facts: a **PR number and its lifecycle state**, two line counts (**"~600" was 812; "~1000" was 1473** — understated 35% and 47%), a **per-host nebula group list** measured on one host and quoted where the other host's reader would act on it, and a pointer at a **handoff doc**, which is per-topic and deliberately overwritten. 🔴 Worse: the PREVIOUS version of that same block carried an explicit warning never to cite a PR's state (*"this comment carried two wrong ones"*), and the rewrite **deleted the warning and then did the thing it warned against.** Point durable comments at the subsystem-index entry, not at a handoff doc.
- 🔴 **`isLaptop` (`nix/home.nix:327-330`) is a BACKLIGHT PROBE THAT FAILS OPEN.** Gating a laptop-only package on it risks NOT shipping to the laptop — the wrong failure direction. That is why `mosh` is in the universal list rather than host-gated.
- 🔴 **Five audit rounds are sunk cost, not a reason to merge.** The right question is whether the artifact should exist, and it must be re-asked when the diagnosis changes — round 0 exists for exactly that and should have been re-run at round 2, not round 5. The rounds were not wasted: they caught a bare `nixos-rebuild switch` that would have pulled 444 builds / 1420 fetches onto an unreachable host, a converge path that could never succeed, and a WAN hole introduced by a fix specified in an earlier round.
- (carried) A 20-s poll cannot size an episodic fault; the discriminator (120 pkt @0.5 s concurrent) is the reading.
- ⚠ **The ping output that produced the double match was NOT reproducible** — black-hole, unreachable-network and no-stats shapes all match exactly once. v3 therefore fixes the CLASS (validate the value's shape, fail LOUD) rather than a guessed cause, and says so in its own comment. Do not write up a mechanism for it; none was established.
- **`ssh root@10.42.0.20` (far box, `diffsona`) works from the laptop over the mesh** — needs `-o StrictHostKeyChecking=accept-new` on first use. That is the third vantage; use it rather than re-deriving one.
- 🔴 **`cairn-validate --validate <path>` fails a PRISTINE template with "missing or empty `scope:`"** when the file sits outside the store. Control it against `--template` output before believing a verdict about your own file; the authoritative check is the post-write `hygiene.sh validate --scope <scope>`.
- ⚠ **`handoff_doc.py` reports `leakscan: NO SCANNER FOUND … PASS BY ABSENCE`** in devrc — it looks for `tests/leakscan.py`, which does not exist here. The real gate is `scripts/tests/test_no_public_ips.py`; run it yourself on a doc with a leak history.
- 🔴 **An outbound-port extractor must anchor on the LOCAL port** or it counts inbound replies as remotes: `> [0-9.]+\.\K[0-9]+` matched our own `51711` on reply lines. Use `\.<localport> > [0-9.]+\.\K[0-9]+`, and control it BOTH ways — reject the local port, and confirm a second genuine remote still counts.


## How to verify — the arc closing-condition
🔴 RUN IT ALL WITH THE TUNNEL UP, and substitute HOME_PUB first; with the tunnel down several lines go vacuously green (`ip link show airvpn` fails, split-tunnel probe returns `dev wlp170s0`, pill up=false — those three catch you).
```bash
# tunnel + split-tunnel, with the tunnel UP:
ip link show airvpn && ip rule | grep 500         # pin present: uidrange 991-991 lookup main
HOME_PUB='<home-public-ip>'                       # set at run time; NEVER inline the value
case "$HOME_PUB" in *'<'*) echo "substitute HOME_PUB first" >&2; exit 2;; esac
ip route get "$HOME_PUB" uid 991                  # → via <gw> dev wlp170s0 (NOT airvpn)
ip route get "$(getent ahostsv4 one.one.one.one | awk '{print $1; exit}')" | head -1
                                                  # → dev airvpn table 51820
curl -s https://ipinfo.io/json | jq -r .country   # → US
ssh zach@10.42.0.30 'echo nebula-ok'              # nebula path alive with tunnel up
python3 -c "import json,os; d=json.load(open(os.path.expanduser('~/.cache/bar-status/airvpn.json'))); print(d['up'], d['verdict'], d['server'], d['country_code'])"
systemctl --user list-timers airvpn-status-poll.timer --no-pager | head -3
# v6-remote noise only, no MTU/routing churn:
journalctl -u 'nebula@mesh' --since '<up-time>' | grep -c 'Failed to write outgoing packet|Failed to send handshake'
```
🔴 Killswitch re-test protocol: `claude/skills/bar/reference/airvpn.md` (laptop section) — FAIL-CLOSED on this laptop's ONLY uplink. Instant bail keeping the tunnel: `sudo nft delete table inet airvpn_ks`; full teardown: `sudo /etc/nixos/i3blocks-scripts/airvpn-sudo down`.
