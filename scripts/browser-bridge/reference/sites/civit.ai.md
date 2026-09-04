# civit.ai — Civitai App Blocks: the iframe, the boot, and the 404 that looks like an app

**Load this when:** a result envelope named this file in `site_notes` · you are
driving anything at `<slug>.civit.ai` · a selector or injected JS on an App Block
returned `null` and you are about to call the bridge broken · you are about to
report an App Block control as missing or a component as defective.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
Mechanism files stay authoritative for mechanism: frames and OOPIFs →
`reference/frames-cdp.md`; throttling and `wake` → `reference/spa-wake.md`;
stripped `data-testid` and hit-testing → `reference/css-hit-test.md`; proving a
read is the live authenticated session → `reference/auth-pages.md`. The parent
site's identity and `/apps` reads → `reference/sites/civitai.com.md`.
This file is only what is true of **an App Block**.

🔴 **Before driving a block by hand, check whether a recipe already exists.** The
`app-capture` skill in `civitai/talos-infra` encodes every fact below as a
*refusal* in `plan.py`, and ships recipes for the first-party blocks. Driving one
deterministically is `capture.sh <recipe>`; only recipe **authoring** needs the
by-hand flow this file describes.

🔴 **Read the recipe even when you ARE driving by hand — it is the only PER-APP
source that exists.** This file is per-HOST, and `civit.ai` matches *every*
`<slug>.civit.ai`: what you are reading is true of all blocks and specific to
none. Three fields of
`<talos-infra>/.claude/skills/app-capture/scripts/recipes/<slug>.json` answer
questions you would otherwise pay for live:

| field | what it saves you |
|---|---|
| `ready.testid` + `ready._comment` | the anchor that means BOOTED — **and the rejected candidates, each with the reason it fails**: a validation message that vanishes once the control unblocks, loading art present in one read and absent in the next, the static `#root` shell that exists before boot and in a deadlocked frame |
| `clickable` | every selector that recipe may activate. 🔴 Read this as a HAZARD list: an ordinary authenticated mutation — post, vote, edit, withdraw, report — really does act on the operator's account, and **no guard will refuse one for you**. 🔴 Do NOT justify a click with "the spend path rejects untrusted events" — that mechanism is RETRACTED (see below). The correct reason these are dangerous is simpler and stronger: they are not spend-path controls at all, they are plain authenticated writes, and nothing anywhere refuses them |
| `states` | the screens known to be reachable, named, in the order that reaches them |

⚠ The pointer is **cross-repo and gated by nothing** — no check in this repo can
see that path, and app-capture's own doc-rot gate cannot see this file. If the
recipes move, this rots silently: confirm the directory before concluding an app
has no recipe.

---

## 🔴 A block renders in a CROSS-ORIGIN IFRAME — every DOM op needs `--frame`

`<slug>.civit.ai` is embedded in `civitai.com/apps/run/<slug>`. Top-frame
selectors find nothing and injected JS returns `null`, which reads exactly like a
broken bridge. It is not: you are addressing the wrong document.

🔴 **This is a SAFETY boundary, not only a correctness one.** A `--frame` op is
dispatched synthetically (`trusted:false`). A **top-frame** `click`/`type`/`key`
takes CDP `Input.dispatch*Event` and arrives `trusted:true`. So dropping `--frame`
does not merely miss the element — it upgrades the event's trust, and that is the
second route to a trusted event, one that spells no `xdotool` anywhere.

**Corollary, and the reason a "broken" money button is usually not broken:** a
synthetic in-frame click does nothing at all on a billing control. Do not report
that as a defect, and do not "fix" it by dropping `--frame`.

🔴 **The OBSERVATION above stands; the EXPLANATION this file used to give —
"the spend path rejects untrusted events" — is RETRACTED. Measured 2026-08-30; do
not re-derive it.** There is no `isTrusted` / `userActivation` / transient-activation
check anywhere on the spend path: both identifiers return **zero** matches across
`<civitai>/src/components/AppBlocks`, `blocks.router.ts` and
`src/server/services/blocks` — and the positive control is that `isTrusted` DOES
match elsewhere in `src`, so that zero is a measurement rather than a broken
search. `blocks.submitWorkflow` is a **`publicProcedure`** taking the block JWT as
an *input*, not a cookie, so a `curl` from outside any browser submits fine.
What actually gates spend is **token scopes, `buzzBudget`, the author capability
and the Buzz caps**.

🔴 **So the CAUSE of the dead in-frame click is NOT established** — only that it is
not an untrusted-event rejection. The operational rule is unchanged and rests on
the reproducible observation: keep using `trustedKey` for a real money-path click.

🔴 **Security corollary, now that the mechanism is known: "it came from a browser"
is NOT a boundary here.** A non-browser client holding a valid scoped token can
drive this path.

The likely source of the false belief: `openBuzzPurchaseGate.ts` and
`requestConsentGate.ts` gate on handshake **readiness**, and the SDK bans the
`allow-top-navigation-by-user-activation` sandbox token — all *about* user
activation, none an `isTrusted` check, none on the spend path.

## 🔴 The frame id changes on EVERY load — re-poll `frames` after any nav

Five distinct ids in one session (819, 821, 828, 830, 832). A frame id captured
before a `nav` is dead after it. Re-poll `frames` and take the new id; never
carry one across a navigation.

## Blocks boot slowly — the frame does not exist right after `open`

Poll for it. A missing frame this early means *not yet*, not *absent*. Never let
a miss fall back to the top frame: that silently gives you the host page's DOM
under a name that says otherwise.

## 🔴 Gate on an APP-READY ANCHOR before the first action

Wait for something only the **booted** app renders — not merely the frame's
existence.

Without that gate the failure is actively misleading: a click fired into a
still-booting app is **discarded**, and it is the *next* wait that times out,
naming a control that is perfectly fine. The resulting defect report is about the
wrong component. This is the single most expensive mistake available here.

## Tabs are created hidden and throttled — `wake` after every view change

A throttled capture is a blank page. `wake --wait 4000` un-throttles *rendering*.

🔴 **But do NOT infer that a block needs a foreground tab to boot.** It does not —
measured 4/4 booting hidden, one with no `activate` at all. An earlier "5/5
`BLOCK_INIT` deadlock in a hidden tab" is **retracted** and unexplained; do not
re-derive a foregrounding requirement from it. The `wake` is for throttling, and
that is all it is for.

## 🔴 Logged out, `/apps/run/<slug>` is a plain 404 — byte-identical to a bad slug

The 404 renders, screenshots, crops and frames just as well as a real app, so
nothing downstream will tell you. **Check auth first**, before any read you intend
to draw a conclusion from — an unauthenticated session makes a working block and a
nonexistent one indistinguishable.

🔴 **A 404 has a SECOND ordinary cause, and it is invisible from the browser: the block's
`app_blocks.status` is `suspended`.** The run-page resolver filters to approved, so a
suspended block 404s for an authenticated moderator exactly as it does for a logged-out
stranger — and it is not rendered in the model slot either. Measured 2026-09-03, **13 of
24** blocks were suspended, including `hello-world`, `dogfood-manual` and `who-am-i`, so
this is the ordinary state of an internal app, not evidence of a takedown. **An
unauthenticated 404 and a suspended 404 are indistinguishable in the browser**, so a
control pair (a known-serving slug in the same session) is what separates them — logging
in is not enough.

## 🔴 A top-level load of `<slug>.civit.ai` WILL NOT HOLD STILL — it bounces after 2 s

Serving 200 with no HTTP redirect, the bare origin *looks* like a way to observe a block
with no host attached — no `BLOCK_INIT`, so it should sit in its pre-ready state
indefinitely. It does not. `@civitai/blocks-react`'s `dist/internal/directLoad.js` bounces
to `civitai.com/apps/run/<slug>` after `DIRECT_LOAD_TIMEOUT_MS = 2000` when no handshake
arrives — deliberate, so a shared link lands somewhere useful.

**Two bridge round-trips exceed 2 s**, so a nav-then-read sequence reads the HOST page and
reports the block's own values as absent. The tell is `location.href` coming back as
`/apps/run/<slug>` when you navigated to the bare origin. Measured 2026-09-03.

## 🔴 `emulate --color-scheme` does NOT reach the block iframe

The block is a cross-origin OOPIF with its own renderer; `emulate` applies the override to
the tab's **top-level** target. Measured 2026-09-03 with a positive control: under a
CDP-session read the TOP frame correctly reported `osDark: False`, while the durable
`data-civitai-boot-theme` the block had written at load still read `dark`.

**So an OS-vs-host theme mismatch cannot be staged from outside.** If you need one, change
the HOST theme — a real change to the operator's account, so ask first — or accept the case
is unobservable and say so rather than reporting the arm you could not run.

Related: **`--wake` and `--frame` are mutually exclusive** (`wake_with_frame_unsupported`)
— un-throttling is tab-level. Wake the tab, then issue the framed read as a separate op.
The emulation above is live only *inside* a CDP session, so a plain framed read sees no
override either way; read the **durable attribute** the boot wrote, not `matchMedia`.

🔴 **The generalisable half, and it applies well beyond theme:** an app's boot state is
transient by definition, so racing it with a screenshot is the reassuring-zero trap — miss
the frame and you report "no flash" from a sample you never took. Prefer a **durable
artifact** the boot wrote and left behind (here `<html data-civitai-boot-theme>`, set once
at parse and never removed). If none exists, say the transient was not observed.
