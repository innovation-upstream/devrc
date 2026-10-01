# yt-thumbnail.civit.ai — the YT Thumbnail block: anchors, tabs, and the two gates that stop a capture

**Load this when:** a result envelope named this file in `site_flows` · you are
capturing or driving the **YT Thumbnail** App Block · you are about to report one
of its controls as missing or dead · you want store-listing screenshots of it.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
🔴 **`flows/civit.ai.md` is the prerequisite, not optional background** — the
cross-origin `--frame` rule, the frame id changing on every load, the 2-second
`directLoad` bounce off the bare origin, the app-ready-anchor gate and the
logged-out/suspended 404 ambiguity are all there and all apply here. This file is
only what is true of **this one block**.

App repo: `~/workspace/civit/civitai-app-yt-thumbnail`.
First measured 2026-09-27 against live `0.1.0` (`pubreq_01M3JE62KDFVK17V8FWA95SEQF`);
**re-measured 2026-09-30 against live `0.1.7`** (bundle `index-BQ7kpc1g.js`), where
the history/formats/storage surfaces exist and the spend figure changed by ~70×.
Both runs: instance `work` on the **laptop**, account `zachlowdenzx` (8753561).

---

## Where it lives

| | |
|---|---|
| drive it at | `https://civitai.com/apps/run/yt-thumbnail` |
| frame url | `https://yt-thumbnail.civit.ai/` (cross-origin OOPIF — **every DOM op needs `--frame`**) |
| ready anchor | `[data-testid=pm-generate]` |
| logged-out tell | `[data-testid=pm-signin]` present |

⚠ **This file is named in `site_flows` only by a `--frame` op.** Resolution is on
the URL the op ran against, and `context` is **tab-scoped** — it reports the top
document, so it names `civitai.com.md` even when you pass `--frame`. A framed
`js`/`text`/`html` names this file. Measured 2026-09-27: framed `js` →
`flows/yt-thumbnail.civit.ai.md`, framed `context` → `flows/civitai.com.md`. So
"the envelope never mentioned a per-app flow" is not evidence that none exists.

🔴 **Do NOT `nav` the bare `https://yt-thumbnail.civit.ai/`.** It bounces to the
run page after 2 s (`civit.ai.md`), and two bridge round-trips exceed that — you
end up reading the host page while believing you are in the block.

`pm-generate` is the right anchor because it is present the moment the form
renders and it does not exist in the boot skeleton (`index.html` paints only
`[data-boot-shape]` divs). `pm-model-label` works too and additionally proves the
model list resolved — but ⚠ **do not pin its TEXT as a readiness probe.** It read
`SD XL 1.0 (SDXL 1.0)` at 0.1.0 and reads `ChatGPT Images (OpenAI)` at 0.1.7: the
default model is exactly the kind of thing that moves between versions, and the
0.1.0 spelling sat here stale until an audit caught it. Assert the element EXISTS;
read its text as data, never as an expected value. Do not anchor
on `pm-nav-balance`: it is **absent** in the host-embedded run page (measured
`null`), so waiting on it hangs forever on a perfectly healthy app.

## 🔴 Two gates stop a capture at the picker, and neither is a bug

The Generate and Remix forms shoot fine. **Anything past them — the candidates
picker (`pm-result-img`), the editor (`pm-editor*`), the export note — needs a
REAL generation**, and a real generation is blocked twice over:

1. **The consent gate.** The host renders a banner reading
   *"YT Thumbnail is missing permissions it needs to work fully."* with a
   `Review permissions` **button** (not a link — no href). The block declares
   `ai:write:budgeted`, and a declared scope is **dropped from the token until
   the user consents**, so a submit 403s while the manifest and the runtime both
   look correct. Clicking that button is an **account consent action** — an
   operator decision, never an agent's.
2. 🔴 **The spend gate is LIVE, not a wall — a `--frame` click DOES drive it and
   DOES spend real Buzz.** Measured 2026-09-28 on 0.1.3: a framed `click` on
   `[data-testid=pm-generate]` submitted two workflows, debited **6 Buzz** from
   the Blue account and returned two real images. An earlier draft of this file
   said a synthetic in-frame click does nothing on a billing control; that is
   **retracted**. Never fire one to find out whether it works.

   The trap that produced the false claim: on 0.1.3 `pm-generate` was `disabled`
   until the prompt was non-empty, **and the prompt field's placeholder reads
   exactly like a filled-in value** (`a serene mountain lake at golden hour,
   highly detailed`). Clicking a disabled button reports `ok: true` and changes
   nothing — indistinguishable from a swallowed untrusted event. Never infer a
   trust boundary from a dead click.

   🔴 **BUT THE REMEDY THAT TRAP TAUGHT — "`type` a prompt first, then read
   `.disabled`" — NO LONGER DISCRIMINATES, so do not rely on it.** Measured
   2026-09-30 on live **0.1.7**: on a bare page load, with the prompt box
   **empty**, `pm-generate` reads `disabled: false` and is labelled
   `Generate · 209 Buzz`. That is deliberate, not a defect — a *selected format*
   is itself prompt text (`promptsReadyToSubmit` → `hasSubmittablePrompt` asks
   `composePrompt`), and the pre-selected **Clickbait** format contributes a real
   136-character prompt. So a `disabled: false` reading now proves nothing about
   whether anyone typed anything, and reading it as "the form is filled in" is
   the same mistake in the opposite direction.

   🔴 **The durable rule is the simple one: never click `pm-generate` at all
   unless you intend to spend.** On the current default model one stray click is
   **209 Buzz**, armed from the moment the page finishes loading — there is no
   disabled state to catch you.

   Still true: do not "fix" a dead click by dropping `--frame`. A top-frame
   click arrives `trusted:true`, which is a genuine trust upgrade regardless.

So: **a full-flow capture of this block costs the operator's Buzz and their
consent.** Cost depends entirely on the model, so read the button — it carries
the live total. **ChatGPT Images (OpenAI), the current default, is 209 Buzz per
image per format at 16:9** (measured: 1 format → 209, 2 → 418, 4 → 836, all at
quantity 1); the old SDXL default was ~3 and FLUX ~33. Say the figure up front
and get a go-ahead, rather than discovering it after the debit.

## States

| state | how to reach it | spends Buzz? |
|---|---|---|
| `generate` | default on load | no |
| `remix` | click the Remix tab (below) | no |
| `candidates` | a real generation → `pm-result-img` | **yes** |
| `editor` | click a candidate → `pm-editor-canvas` | yes (needs the above) |

## 🔴 The two tabs carry NO testid — tag, then click

They are `button[role=tab]` distinguished only by text (`Generate`,
`Remix an image`). CSS cannot select on text, so tag first and click the id:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
# zsh does NOT word-split an unquoted var — write the flags out, do not stash
# them in "$T" and expand it; the whole string arrives as ONE argument.
$BB --instance work --tab $TAB --frame $F js '(function(){var f=null;
  document.querySelectorAll("[role=tab]").forEach(function(e){
    if(/remix/i.test(e.textContent||""))f=e;});
  if(!f)return "NOT_FOUND"; f.id="bb-remix-tab"; return "tagged";})()'
$BB --instance work --tab $TAB --frame $F click '#bb-remix-tab'
```

**Prove the switch from `aria-selected`, not from the click's `ok:true`** — it
goes `false` → `true`, and `pm-remix-upload` + `pm-remix-hint` appear. A click
that reports ok and changed nothing is the normal failure here.

## Capturing it for the store listing

The block sits in a **resizable** iframe (`minHeight 600`, `maxHeight 4000`), so
its height moves between states — **re-measure after every state change**, never
reuse an offset. The app card itself is a fixed **640 px** wide column centred in
a 1600 px iframe, so cropping the whole iframe leaves huge dead margins; crop to
the card.

```bash
# 1. iframe offset + dpr, from the TOP frame
$BB --instance work --tab $TAB js '(function(){var f=document.querySelector("iframe[src*=\"yt-thumbnail\"]");
  var r=f.getBoundingClientRect();return JSON.stringify({dpr:window.devicePixelRatio,
  x:Math.round(r.x),y:Math.round(r.y)});})()'
# 2. card rect, from INSIDE the frame (widest div 600–760 px, tallest wins)
$BB --instance work --tab $TAB --frame $F js '(function(){var b=null;
  document.querySelectorAll("div").forEach(function(e){var r=e.getBoundingClientRect();
  if(r.width>600&&r.width<760&&r.height>200){if(!b||r.height>b.height)b=r;}});
  return JSON.stringify({x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.width),h:Math.round(b.height)});})()'
# 3. crop = (iframe.x + card.x - pad, iframe.y + card.y - pad) * dpr
magick full.png -crop ${W}x${H}+${X}+${Y} +repage card.png
```

⚠ **`dpr` is not 1 on the workbench** — it measured `1.140625`, so a crop box
computed in CSS pixels lands in the wrong place by ~14 %. Multiply by the `dpr`
you just read; do not assume it, and do not carry this number forward — it is a
property of the display, not of the app.

`wake --wait` before each capture (tabs open hidden and throttled; a throttled
capture is a blank page) and again after each state change — **a re-`wake` is
per PAGE-STATE here, not once per session**, because the tab re-throttles.

Host chrome — the Civitai header, breadcrumb and the permissions banner — is all
in the **top** frame, so a card crop excludes it automatically. That is a reason
to crop rather than to dismiss the banner.

## Testid inventory

Stable, from `src/App.tsx` (backticked template ids are **ungreppable in the
built bundle** — only their static prefix survives minification):

```
form      pm-generate pm-model-label pm-model-row pm-change-model
          pm-lora-add pm-lora-row pm-lora-weight
          pm-quantity-${n}  pm-preset-${i}
remix     pm-remix-upload pm-remix-preview pm-remix-clear pm-remix-hint
results   pm-result-img  pm-edit-${i}
editor    pm-editor pm-editor-canvas pm-editor-text pm-editor-size pm-editor-x
          pm-editor-y pm-editor-color pm-editor-stroke pm-editor-strokecolor
          pm-editor-download pm-editor-back pm-editor-error pm-export-note
money     pm-spent pm-insufficient pm-account-rejected pm-account-${choice}
          pm-account-trigger pm-save-note
auth      pm-signin
harness   pm-nav-name pm-nav-pill pm-nav-balance pm-nav-remint
          pm-setup-auto pm-setup-done
history   yt-history yt-history-toggle yt-history-reloading
          yt-history-save yt-history-edit yt-history-resume
formats   yt-format-new yt-format-${slug} (clickbait cinematic bold-simple
          tech-review tutorial gaming) yt-prompt-preview-${slug}
storage   yt-storage-anon yt-board-toggle yt-published-board
```

🔴 **The `yt-*` ids are the HISTORY, FORMATS and STORAGE surfaces, all of which
shipped after this file was first written at 0.1.0 — a read that only knows the
`pm-*` set will report most of the app as absent.** The six real format slugs are
named in the block above; address them by name.

🔴 **`pm-nav-*` and `pm-setup-*` are defined in `src/main.tsx`, the dev-harness
bootstrap — NOT in `App.tsx`.** They do not exist in the host-embedded app at
all, so waiting on one there hangs on a healthy block. Measured absent, and
confirmed by which file declares them; both checks, because absence alone would
not have told you *why*.

**`pm-signin` is NOT one of them** — it is a real `App.tsx` control, and it is
absent here only because the session is logged IN. Treat its presence as the
logged-out tell, exactly as the table at the top says.

🔴 **ONE inventory, for the CURRENT version only.** An earlier revision of this
file kept its 0.1.0 inventory *and* appended the 0.1.7 one, so the file asserted
`pm-lora-add` both present and absent-by-design with nothing saying which applied —
the same version-pinned rot this file exists to warn about, reproduced inside the
commit that warned about it. When you re-measure, **replace** the block below;
never append a second.

Measured live 2026-09-30 on **0.1.7** (`index-BQ7kpc1g.js`), default
`generate` state, signed in, **with three completed generations in history**:

```
present  pm-generate pm-model-label pm-model-row pm-change-model
         pm-account-trigger  yt-history yt-history-toggle yt-history-save
         yt-history-edit yt-history-resume  yt-format-new yt-format-*
         yt-board-toggle  yt-prompt-preview-clickbait
absent   pm-signin (logged in)  pm-lora-add  pm-remix-upload (Generate tab)
         pm-result-img pm-editor pm-spent pm-insufficient
         yt-history-reloading  yt-storage-anon  yt-published-board
```

Three of those absences are **by design and must not be read as defects**:
**`pm-lora-add`** is hidden on no-LoRA model families and the default checkpoint
*is* one (`LORA_FREE_BASE_MODELS = {'OpenAI'}`), so it is absent on first load for
every viewer — a reaffirmed product decision; **`yt-history-reloading`** only
exists while a storage read is in flight; **`yt-published-board`** renders only
behind `yt-board-toggle`.

🔴 **`pm-save-note` is the SAVE RESULT surface and it reports FAILURE as readily
as success — read its text, never just its presence.** Measured 2026-09-30 on
0.1.7: clicking `yt-history-save` on a real paid candidate rendered
**`Couldn't save that image: image url is not allowed`**. That is a **platform**
refusal, not an app bug — `isAllowedSaveImageUrl`'s allowlist
(`civitai/src/components/AppBlocks/saveImageDownload.ts:31`) carries
`orchestration-next.civitai.com` while generated blobs are served from
`orchestration-**new**.civitai.com`, matched exactly with no wildcarding. Until
that is fixed, **a successful save is not reachable in this block**, so do not
spend a generation trying to capture one.
