# civitai.com — identity, account switching, and the reads that lie

**Load this when:** a result envelope named this file in `site_notes` · you are
about to act on civitai.com **as a particular user** · you need to know WHICH
account a Brave profile holds · a `/apps` read looks empty, stale, or shows
entries that 404 · you are about to conclude civitai "leaked scope" or "is
broken" from a browser read.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
Mechanism files stay authoritative for mechanism: throttling and `wake` →
`reference/spa-wake.md`; hit-testing and stripped `data-testid` →
`reference/css-hit-test.md`; frames/OOPIFs → `reference/frames-cdp.md`;
proving a read is the live authenticated session → `reference/auth-pages.md`.
This file is only what is true of **civitai.com**.

---

## 🔴 Identity: never trust a written-down profile→account mapping

**The Brave-profile→account mapping has NO shelf life.** It was recorded WRONG
three times in five days, in **both** directions. Do not carry one in your head,
in a handoff, or from this file.

**ALWAYS read the session endpoint instead**, and use what it returns:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance work js '(async function(){
  const r = await fetch("/api/auth/session", {credentials:"include"});
  const j = await r.json();
  return JSON.stringify({username:j?.user?.username, id:j?.user?.id,
                         isModerator:j?.user?.isModerator});
})()'
```

Cost of checking: **one request**. Cost of trusting a recorded mapping: **hours**.

`{username, id, isModerator}` is the identity. Nothing else is — not the profile
key, not the profile label, not the tab title, not what you did yesterday.

## 🔴 There is no "switch accounts" flow

Brave profiles are **separate cookie jars, one account each**. So the real
operation is not *switching* — it is **PICKING the profile that already holds the
account you need**. Enumerate; never assume:

```bash
# every connected instance, then the session in each — read the pair, not the key
$BB health          # -> TOP-LEVEL .instances[].key (health is NOT a /cmd op,
                    #    so there is no .result.data wrapper here)
$BB --instance <key> js '(async function(){ …the fetch above… })()'
                    # -> the value lands at .result.data.value
```

### 🔴 `connected` ≠ drivable

An instance can report `connected: true` from `browser health` and still fail
`open` with `No current window`. That instance's identity is **UNKNOWN**, not
absent — do not record it as "profile X holds no account", and do not let it
narrow the candidate set. Re-check it, or hand it to the operator.

### 🔴 Sign-in is Google SSO and CANNOT be automated

Same class as the GitHub sudo-mode gate: you will not get through it. If **no**
profile holds the account you need, **STOP** and hand it to the operator with
exact steps (which profile to open, which account to sign in as, what to confirm).

🔴 **Do NOT route around it** — not via a direct DB write, not by calling the
service layer. Those skip the side effects the UI action exists to produce
(notifications, re-queues, audit events), so the record ends up in a state the
product can never produce, and the thing you were asked to verify was never
exercised.

## Capability is DERIVED, not assumed

`__NEXT_DATA__.props.pageProps.flags` is the **server's own flag resolution for
that session**. Read it rather than guessing what an account can see:

```bash
$BB --instance work js '(function(){
  return JSON.stringify(window.__NEXT_DATA__?.props?.pageProps?.flags ?? null);
})()'
```

**Verify a switch took by re-reading BOTH** the session endpoint **and** the
flags. Either one alone can look right while the other has not moved.

## 🔴 After ANY account switch, purge account-scoped `localStorage`

`recentlyOpenedApps` is per-**BROWSER-PROFILE**, not per-account. Entries written
by the previous account still render in the `/apps` **"Recently opened"** rail and
**404 when clicked**.

This produced a false **"scope leak"** alarm: the UI was showing another account's
apps, and the server was doing nothing wrong.

🔴 **The server returning zero rows is NOT evidence the UI shows zero.** They are
different claims with different storage behind them — check the one you are
actually making.

```bash
$BB --instance work js '(function(){
  localStorage.removeItem("recentlyOpenedApps"); return "cleared";
})()'
```

## 🔴 The account menu is a TOGGLE — a JS `.click()` half-opens it

A JS `.click()` on the account menu leaves `aria-expanded="false"` and finds only
empty `.mantine-Popover-dropdown` shells. That is **indistinguishable from "the
entry is missing"**, and reads as a product bug.

- Use the **trusted `click` op** (top-frame CDP input), not a JS `.click()`.
- Click **ONCE** — it is a toggle, so a second click closes what the first opened.
- **Read `aria-expanded` in the same breath** as the click, so you know which
  state you are in rather than inferring it from what you found.
- If the dropdown links are still not selector-reachable, **`screenshot`** and
  look. Do not selector-hunt.

## Route-level positive control

- **`/apps` returns 404 for an account outside the store cohort.** A 404 there is
  a cohort fact, not a broken session.
- **`/models` returning 200 is the positive control** that the session works at
  all. Run it before diagnosing anything from an `/apps` 404.

## `/apps` needs a long settle (~9s)

Cards arrive via tRPC, and a hidden tab is throttled (mechanism:
`reference/spa-wake.md`). A browser read of `/apps` needs roughly **9 s after
`wake`** before the rail is populated.

Use `document.body.innerText.length` as a **sanity floor** — about **221 chars**
means the page shell rendered and the content did **not**. Assert the floor;
never read an empty rail as "this account has no apps".

## Selectors on civitai

`data-testid` is **stripped from the production build**, so a testid selector
matches nothing and reads as a missing element — mechanism, and what to select
instead, in `reference/css-hit-test.md`. Purpose-built non-`testid` data
attributes DO survive: civitai added **`data-listing-cover-placeholder`** for
exactly this, and it is a reliable hook.

---

## Proposed helper (NOT implemented): `browser sessions`

**Status: a proposal, not a shipped op.** The loop below works today and is what
to paste until someone builds the op.

The gap it closes: reading `/api/auth/session` across **all** connected instances
was hand-rolled **twice in one session**, and **both times contradicted the
written-down mapping**. That is a mechanism-shaped problem, not a discipline one —
the one-call version should print, per instance:

```
key → username → id → isModerator
```

…including an explicit `UNKNOWN` row for an instance that is `connected` but not
drivable (see above), because a silently-omitted instance is how a candidate set
gets narrowed to the wrong profile.

Until then:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
KEYS=$("$BB" health | python3 -c 'import json,sys
print("\n".join(i["key"] for i in json.load(sys.stdin)["instances"]))')
for k in ${=KEYS}; do          # ${=…} — zsh does NOT word-split a bare $VAR
  printf '%s → ' "$k"
  "$BB" --instance "$k" js '(async function(){
     const r = await fetch("/api/auth/session", {credentials:"include"});
     const j = await r.json();
     return (j?.user?.username ?? "ANON") + " → " + (j?.user?.id ?? "-") +
            " → " + (j?.user?.isModerator ?? false);
   })()' 2>/dev/null \
    | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["result"]["data"]["value"])
except Exception: print("UNKNOWN (connected but not drivable?)")'
done
```

⚠ That loop reads whatever page each instance's tab is on — a session fetch is
same-origin, so point each instance at a civitai.com tab first, or it reports the
session of some other site (i.e. nothing).
