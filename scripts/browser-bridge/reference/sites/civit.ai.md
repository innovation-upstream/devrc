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

**Corollary, and the reason a "broken" money button is usually not broken:** the
spend path *rejects untrusted events*, so a synthetic in-frame click does nothing
at all on a billing control. That is the platform working as designed, not a
defect — do not report it as one, and do not "fix" it by dropping `--frame`.

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
