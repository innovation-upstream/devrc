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
🔴 **This file describes live `0.1.7`, built from app-repo commit `b293455`**
(served bundle `index-BQ7kpc1g.js`). Every source line it cites is cited **at that
commit** — the app's `main` is ahead of what any viewer can load, so a fact read
off `main` describes an app that is not deployed.

**Three measurement points, not two:**

| version | when | what it established |
|---|---|---|
| `0.1.0` | 2026-09-27 | the anchors, the frame rule, the capture recipe (`pubreq_01M3JE62KDFVK17V8FWA95SEQF`) |
| `0.1.3` | 2026-09-28 | the spend retraction below — a framed click DOES spend (6 Buzz debited) |
| `0.1.7` | 2026-09-30 | the history/formats/storage surfaces; the per-image price changed by ~70× |

The 0.1.0 and 0.1.7 runs were instance `work` on the **laptop**, account
`zachlowdenzx` (8753561). The 0.1.3 datum below records only the version and the
debit — its instance and account are not recorded here, so do not read the pair
above onto it.

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

The Generate and Remix forms shoot fine. **Anything past them — the candidates,
which at 0.1.7 appear ONLY inside the history panel (`yt-history-img`,
`History.tsx:403`), the editor (`pm-editor*`, `App.tsx:2043`), the export note —
needs a REAL generation**, and a real generation is blocked twice over:

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
   at least **209 Buzz**, armed from the moment the page finishes loading. The
   `disabled` prop still exists — `disabled={!submittable || remixIncomplete}`
   (`App.tsx:2555`) — but neither input can save you on the Generate tab:
   `submittable` is true because the pre-selected format supplies the prompt
   (above), and `remixIncomplete` (`App.tsx:1977`) is `isRemix && !sourceImage`,
   so it only ever fires on the **Remix** tab with no source image. Do not read a
   disabled Generate on Remix as evidence of a guard on Generate.

   Still true: do not "fix" a dead click by dropping `--frame`. A top-frame
   click arrives `trusted:true`, which is a genuine trust upgrade regardless.

So: **a full-flow capture of this block costs the operator's Buzz and their
consent.** Say the figure up front and get a go-ahead, rather than discovering it
after the debit.

### 🔴 The button's label is NOT always a total — three of its four states are degenerate

`App.tsx:2565-2569` at `b293455` renders **four** labels, and an earlier revision of
this file told you to "read the button — it carries the live total", which is false
in three of them:

| label | what it means | what to do |
|---|---|---|
| `Generate · NNN Buzz` | every selected format priced; `NNN` is the expected total | the only reading you may quote as a cost — but see the grouping trap below |
| `Estimating…` / `Submitting…` / `Generating…` / `Working…` | the `busy` branch (`App.tsx:2566` → `phaseLabel`, `:3516-3521`). A run is IN FLIGHT; this is a healthy, already-paid generation | 🔴 **not a pricing state and not a consent failure.** Earlier revisions listed only three labels, so a driver reading `Generating…` matched the no-figure row and diagnosed gate 1 — on a run that had already succeeded in being submitted. Read the history panel for the realized cost instead |
| `Generate · from NNN Buzz` | `estimatePartial` — only SOME formats priced. `aggregateEstimate` (`generation.ts:627-635`) sums the KNOWN runs and returns `partial: true`, so `NNN` is a **floor, not the bill** | never quote it as the cost: the unpriced formats bill on top of it. Treat the total as unknown |
| bare `Generate`, no figure | NO format priced. The app's own comment at `App.tsx:2562-2564` says `estimate()` **403s until the viewer consents** — so this is the ordinary label for a fresh viewer, i.e. **gate 1 above**, not a defect | the cost is UNKNOWN. There is no figure to get a go-ahead on, so do not click |

The non-price signal is `yt-format-cost-note` (`App.tsx:2390`), present whether or
not `estimate()` answered: it states the **workflow count** — `One generation.
Costs Buzz.` or `N separate generations — each one costs Buzz.`

🔴 **THE FIGURE IS LOCALE-GROUPED, SO NEVER `parseInt` IT.** `formatCost` is
`Math.round(cost).toLocaleString()` (`generation.ts:406-409`), so at four figures the
label reads `Generate · 1,045 Buzz`. A `parseInt` or `/(\d+)/` extraction returns
**1**, and you quote "1 Buzz" for a go-ahead on a 1,045 Buzz debit — an error of
three orders of magnitude, in the direction that gets the click approved. Every
example figure in this file (209 / 418 / 836) is below 1000, which is exactly why no
previous measurement exposed this. **Relay the label verbatim, or strip separators
before parsing** (`replace(/[^\d]/g, '')`). The separator is locale-dependent, so do
not pattern-match on a comma either.

### 🔴 The figures below are EXAMPLES AT QUANTITY 1. 836 is not a ceiling.

Measured 2026-09-30 on 0.1.7 with **ChatGPT Images (OpenAI)** — `DEFAULT_CHECKPOINT`,
`models.ts:114-119` — at 16:9: **1 format → 209, 2 → 418, 4 → 836**, all at
quantity 1. The old SDXL default was ~3/image and FLUX ~33. What bounds one click
is nothing in that list:

- `toggleFormat` (`formats.ts:135-141`) refuses only the deselection of the LAST
  remaining format. There is **no upper bound** on the selected set.
- the selectable catalogue is `allFormats` (`formats.ts:410-415`): 6 built-ins
  **plus** up to `MAX_CUSTOM_FORMATS = 40` (`formats.ts:193`) **plus** every
  published format the viewer pulled in. Tens of chips are selectable, not six.
- `QUANTITY_MAX = 4` (`generation.ts:24`) multiplies **within** one workflow.
- `buzzBudgetPerGen: 900` (`block.manifest.json:28`) is **not** a per-click cap.
  The app's own note at `generation.ts:293-307` says it is enforced **PER
  WORKFLOW**, that *"N formats are N workflows, each with its own budget"*, and
  that the server clamps the manifest value to a platform per-gen cap whose value
  is not recorded here.

So one click costs (formats selected) × (quantity) × (per-image price), and at the
current default 836 Buzz is the per-**format** maximum — the `buzzBudgetPerGen` 900
bounds each workflow, and N selected formats are N workflows.

⚠ **The honest multiplier, arithmetic rather than a flourish.** 5 formats at
quantity 1 is **1,045** Buzz (1.2× the 836 quoted above); the `MAX_CUSTOM_FORMATS`
= 40 ceiling tops out near **8,360** (10×, i.e. ONE order of magnitude), and two
orders would need ~100 formats, which that ceiling does not permit. An earlier
revision of this paragraph said "two orders of magnitude" — that was a flourish, not
a derivation, and it is the same defect class (a bound stated beyond what the
mechanism supports) as the `836`-as-ceiling it was written to replace. **Read the
label for what it gives you, read the chip count for what multiplies it, and never
treat a number in this file as a bound.**

## States

| state | how to reach it | spends Buzz? |
|---|---|---|
| `generate` | default on load | no |
| `remix` | click the Remix tab (below) | no |
| `candidates` | a real generation → the history panel (it auto-opens once there is an entry: `historyOpenEffective = historyOpen ?? entries.length > 0`, `App.tsx:2674`) → `yt-history-row` → `yt-history-images` → `yt-history-img` (`History.tsx:351`, `:392`, `:403`) | **yes** |
| `editor` | click **Add text** = `yt-history-edit` (`History.tsx:423`, wired `onEdit={(url) => setEditing(url)}` at `App.tsx:2696`) → `pm-editor` / `pm-editor-canvas` (`App.tsx:2043`, `:2092`) | yes (needs the above) |

🔴 **There is NO separate candidates picker at 0.1.7, and no `pm-result-img`
anywhere** — the only surface that shows generated images is the history list, and
the editor opens from a **button on a history image**, never from clicking the
image itself. An earlier revision of this table routed both rows through
`pm-result-img`; following it means spending ≥209 Buzz and then waiting forever on
an id that cannot appear, which is indistinguishable from a failed generation and
invites a second debit. Details and the dead-id list: *Testids*, below.

⚠ `yt-history` itself is **absent** when history is ready-and-empty
(`showHistory`, declared `src/history.ts:534-542`; the call is `History.tsx:91`) — a
SIGNED-IN viewer with no history has no panel at all, so
waiting on `yt-history` before any generation also hangs on a healthy app.

## 🔴 The two tabs carry NO testid — tag, then click

⚠ They are **not** a `Tabs` component. `App.tsx:2262-2272` renders an SDK
`SegmentedControl` with `data=[{generate, 'Generate'}, {remix, 'Remix an image'}]`,
and `@civitai/blocks-react@0.49.0` (the pin at `b293455`) renders each segment as
`<button type="button" role="tab" data-civitai-ui-segment aria-selected=…>`
(`node_modules/@civitai/blocks-react/dist/ui/SegmentedControl.js:59`). So
`button[role=tab]` is right, there is no `data-testid`, and the segments are
distinguished only by their text. CSS cannot select on text, so tag first and
click the id — scoping the query to the SDK's own attribute rather than bare
`[role=tab]`, so a future `role=tab` elsewhere in the app cannot be caught:

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
# zsh does NOT word-split an unquoted var — write the flags out, do not stash
# them in "$T" and expand it; the whole string arrives as ONE argument.
$BB --instance work --tab $TAB --frame $F js '(function(){var f=null;
  document.querySelectorAll("button[role=tab][data-civitai-ui-segment]").forEach(
    function(e){if(/remix/i.test(e.textContent||""))f=e;});
  if(!f)return "NOT_FOUND"; f.id="bb-remix-tab"; return "tagged";})()'
$BB --instance work --tab $TAB --frame $F click '#bb-remix-tab'
```

⚠ `data-civitai-ui-segment` is an **SDK** attribute, not this app's — it moves with
`@civitai/blocks-react`, not with the block. If the tagger returns `NOT_FOUND`,
re-grep the installed SDK before concluding the control is gone.

**Prove the switch from `aria-selected`, not from the click's `ok:true`** — it
goes `false` → `true`, and `pm-remix-upload` + `pm-remix-hint` appear. A click
that reports ok and changed nothing is the normal failure here.

## Capturing it for the store listing

The block sits in a **resizable** iframe (`minHeight 600`, `maxHeight 4000`,
`block.manifest.json:31-32`), so its height moves between states — **re-measure
after every state change**, never reuse an offset.

🔴 **"A fixed 640 px card centred in a 1600 px iframe" was TRUE AT 0.1.0 AND IS
FALSE AT 0.1.7.** That constant is exactly what `src/layout.ts` was written to
delete — its own docblock opens *"Every screen used to be `{ width: '100%',
maxWidth: 640 }`"*. At `b293455` the shape is width-dependent
(`layoutForTier`, `layout.ts:271-330`):

| block width | shape | content cap |
|---|---|---|
| `base` / `xs` | one column | `maxWidth: 640` |
| `sm` / `md` (768–1183) | two columns | `maxWidth: 1184` — documented as **never binding** at any width those tiers admit |
| `lg` / `xl` and above (1184+) | **rail + main** (`yt-rail-grid`, `yt-rail`, `yt-main`) | `maxWidth: null` — **fills the block** |

So on the workbench's ~1600 px iframe the block is in the **rail** shape and uses
the whole width: there is no 640 px card, and no dead margin to crop away. Crop
the iframe, or crop to `yt-content`.

⚠ **The old heuristic — "widest div 600–760 px, tallest wins" — now matches
NOTHING at 1600 px** and the snippet it lived in dereferenced a `null` rect. Read
the content box by testid instead; it exists in every shape and carries the shape
in its own attributes (`contentProps`, `App.tsx:3608-3614`):

```bash
# 1. iframe offset + dpr, from the TOP frame
$BB --instance work --tab $TAB js '(function(){var f=document.querySelector("iframe[src*=\"yt-thumbnail\"]");
  var r=f.getBoundingClientRect();return JSON.stringify({dpr:window.devicePixelRatio,
  x:Math.round(r.x),y:Math.round(r.y)});})()'
# 2. content rect + the layout shape, from INSIDE the frame.
#    `rail` present => the rail shape => the content fills the iframe.
$BB --instance work --tab $TAB --frame $F js '(function(){
  var c=document.querySelector("[data-testid=yt-content]");
  if(!c)return "NOT_FOUND";
  var r=c.getBoundingClientRect();
  return JSON.stringify({x:Math.round(r.x),y:Math.round(r.y),
    w:Math.round(r.width),h:Math.round(r.height),
    maxWidth:c.style.maxWidth||null,
    resultColumns:c.getAttribute("data-result-columns"),
    rail:!!document.querySelector("[data-testid=yt-rail]")});})()'
# 3. crop = (iframe.x + content.x - pad, iframe.y + content.y - pad) * dpr
magick full.png -crop ${W}x${H}+${X}+${Y} +repage card.png
```

⚠ `maxWidth` is read off the **inline style**, deliberately: `data-max-width` and
`data-rail` were removed at 0.1.7 as duplicates of the style
(`App.tsx:3596-3606`). Do not reach for those attributes — they are gone.

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

## Testids: grep the four source files — this file does NOT carry an inventory

🔴 **No inventory here is worth trusting, including the one this file used to
carry.** Measured at `b293455`: **113 distinct `data-testid` families** render from
**four** files. The list that stood here named **53** — and **four of those did not
exist in any rendering file**, one of which the States table routed a money spend
through. A 49-of-113 list that no gate checks is a liability, not a reference, so
it is gone. **Grep the source at the deployed commit instead:**

```bash
APP=~/workspace/civit/civitai-app-yt-thumbnail
# `b293455` is 0.1.7. Re-resolve it for whatever is live when you read this.
git -C $APP grep -n data-testid b293455 -- \
  src/App.tsx src/History.tsx src/FormatPicker.tsx src/main.tsx
```

| file | families | what lives there |
|---|---|---|
| `src/App.tsx` | 62 | the form, the editor (`pm-editor*`), money/account (`pm-spent`, `pm-insufficient`, `pm-failed`, `pm-partial`, `pm-account-*`), the `yt-prompt-*` previews, layout (`yt-hero`, `yt-main`, `yt-rail*`, `yt-spend-row`, `yt-content`), `yt-storage-*` |
| `src/History.tsx` | 26 | **every `yt-history-*`** — and **`pm-save-note`**, which its `pm-` prefix hides |
| `src/FormatPicker.tsx` | 19 | **every `yt-format-*`** card/chip/editor id and **every `yt-published-*`** |
| `src/main.tsx` | 6 | `pm-nav-*` + `pm-setup-*` — dev harness only, see below |

`src/Harness.tsx` declares none. A backticked `${…}` id counts as one family. Two
of `App.tsx`'s 62 reach the DOM through a `testId` prop rather than a literal
(`pm-account-icon-${choice}`, `pm-account-trigger-icon`; the sink is
`App.tsx:3113`), so a naive literal grep returns 111 — if your count disagrees with
mine by two, that is why.

🔴 **"From `src/App.tsx`" was the old sourcing line and it was wrong.** File
attribution is load-bearing here by this file's own argument: an agent grepping
`App.tsx` for `yt-history` or `pm-save-note` gets **zero** and concludes the
inventory is stale, when the ids are real and live in `History.tsx`.

⚠ These are **cross-repo claims that nothing in this repo can gate** — no test
here can see the app repo, so the counts and line numbers rot silently when the app
ships. Re-derive with the grep above rather than quoting them.

**Backticked template ids are ungreppable in the built bundle** — only the static
prefix survives minification — so always grep the SOURCE, never the bundle.

🔴 **ONE version's facts at a time — when you re-measure, REPLACE, never append.**
This rule survives the inventory it was written for, because the file is still full
of version-pinned claims (the three-point table at the top, the price ladder, the
layout shapes, the absences below). An earlier revision kept its 0.1.0 inventory
*and* appended the 0.1.7 one, so the file asserted `pm-lora-add` both present and
absent-by-design with nothing saying which applied — the same rot this file exists
to warn about, reproduced inside the commit that warned about it. **And apply it to
the block you are editing**: the commit that imposed this rule edited the testid
block without applying it there.

### The handful a driver actually needs

All verified present at `b293455`, with the file that declares each:

| purpose | testid | declared |
|---|---|---|
| ready anchor | `pm-generate` | `App.tsx:2557` |
| 🔴 the money control — **never click it** | `pm-generate` (same node) | `App.tsx:2557` |
| logged-out tell | `pm-signin` | `App.tsx:2542` |
| model list resolved (exists-check only — never pin its text) | `pm-model-label` | `App.tsx:1983` |
| candidates | `yt-history` → `yt-history-row` → `yt-history-img` | `History.tsx:94`, `:351`, `:403` |
| save a candidate | `yt-history-save` | `History.tsx:415` |
| the save's outcome | `pm-save-note` | `History.tsx:136` |
| open the editor | `yt-history-edit` → `pm-editor-canvas` | `History.tsx:423` → `App.tsx:2092` |
| refill a past form | `yt-history-resume` | `History.tsx:457` |
| images-per-format | `pm-quantity` — a bare `<Select>`, **not** `pm-quantity-${n}` | `App.tsx:2532` |
| format chips | `yt-format-${id}` — read the warning below first | `FormatPicker.tsx:81` |

🔴 **Four ids a previous revision of this file named DO NOT EXIST at `b293455`** —
zero occurrences across every rendering file. Do not wait on them, and if you find
them cited anywhere else, that citation is stale too:

| dead id | reality |
|---|---|
| `pm-result-img` | **never existed in a rendering file.** Its only hit in the app repo is a stale test comment (`App.formats.test.tsx:319`). Candidates are `yt-history-img`. |
| `pm-edit-${i}` | the edit control is `yt-history-edit` (`History.tsx:423`) |
| `pm-preset-${i}` | presets became formats: `yt-format-${id}` (`FormatPicker.tsx:81`) |
| `pm-quantity-${n}` | became the bare `pm-quantity` `<Select>` (`App.tsx:2532`) |

🔴 **`pm-nav-*` and `pm-setup-*` are defined in `src/main.tsx`, the dev-harness
bootstrap — NOT in `App.tsx`.** They do not exist in the host-embedded app at
all, so waiting on one there hangs on a healthy block. Measured absent, and
confirmed by which file declares them; both checks, because absence alone would
not have told you *why*.

**`pm-signin` is NOT one of them** — it is a real `App.tsx` control (`:2542`), and
it is absent in a signed-in session only because the session is signed IN. Treat
its presence as the logged-out tell, exactly as the table at the top says.

### 🔴 NEVER address a format chip with `[data-testid^=yt-format-]`

The prefix is shared by **layout** nodes, not just chips. In the default state — 6
built-ins, one selected — it matches **16** nodes of which only 6 are chips:

| node | count in default state | declared |
|---|---|---|
| `yt-format-grid` | 1 (the container) | `FormatPicker.tsx:63` |
| `yt-format-card` | 6 — one wrapper per format | `FormatPicker.tsx:70` |
| `yt-format-${id}` | 6 — **the chips** | `FormatPicker.tsx:81` |
| `yt-format-check` | 1 — one per SELECTED format | `FormatPicker.tsx:98` |
| `yt-format-new` | 1 | `App.tsx:2374` |
| `yt-format-cost-note` | 1 | `App.tsx:2390` |

🔴 **`querySelectorAll` returns DOCUMENT order, and index 0 is `yt-format-new` — a
LIVE BUTTON, not a container.** The table above is grouped by kind, not by
position; the document order at `b293455` is `yt-format-new` (`App.tsx:2374`) →
`yt-format-cost-note` (`:2390`) → `yt-format-grid` and everything inside
`<FormatPicker>` (`:2396`). So a "first match" or `nth` habit does not click an
inert wrapper: it clicks a `Button` whose `onClick={onNewFormat}` **opens the
custom-format editor**, swapping in the `yt-format-editor`/`-label`/`-suffix`/
`-save`/`-cancel` set. It is enabled in exactly the state you measure in —
`disabled={busy || storageState === 'anon' || customFormatsFull(customFormats)}`,
all three false when signed in and idle.

⚠ **That is worse than a dead click, and it reads like one.** No chip selection
changes, so the op reports `ok: true` with nothing visibly different — while the
app has silently changed state, and your next chip query matches a different node
set. An earlier revision of this section said index 0 was the grid and "changes
nothing"; both halves were wrong. **Address the chips by their own ids.** More
appear in other states — `yt-format-publish-${id}` and
`yt-format-delete-${id}` per custom format (`FormatPicker.tsx:124`, `:135`), and
the whole `yt-format-editor`/`-label`/`-suffix`/`-error`/`-save`/`-cancel` set once
the editor opens (`:173`–`:221`).

🔴 **And the chip id is NOT six-valued.** `yt-format-${fmt.id}` takes the id of
whatever `allFormats` returned (`formats.ts:410-415`):

- the 6 built-ins — `clickbait cinematic bold-simple tech-review tutorial gaming`
  (`BUILTIN_FORMATS`, `formats.ts:69-118`; the default selection is
  `BUILTIN_FORMATS[0]` = `clickbait`, `formats.ts:121`);
- `yt-format-custom:<id>` per custom format, up to `MAX_CUSTOM_FORMATS = 40`
  (`CUSTOM_ID_PREFIX = 'custom:'`, `formats.ts:179`, `:193`);
- `yt-format-shared:<key>` per published format the viewer added
  (`PUBLISHED_ID_PREFIX = 'shared:'`, `formats.ts:316`).

Read the exact id off the page and address that; never enumerate from the six.

> **This warning was DELETED by an earlier revision**, on the grounds that "the
> inventory two lines above already names the six real slugs, which is the remedy."
> **That reasoning was wrong**: the inventory named no layout node, so the remedy it
> pointed at did not exist — and the same commit then wrote `yt-format-*` into a
> present-list, introducing the very glob the warning was about. It is restored
> here, naming the layout nodes, which is what makes it actionable. A rationale
> that answers a different question is not grounds to drop a guard; do not
> re-delete it on a fresh one.

### Absences that are NOT defects

Every id below was measured absent on 2026-09-30 on live 0.1.7, default `generate`
state, signed in, **with three completed generations in history**. The list is the
WHOLE measurement and every row carries its reason, so there is no residue a reader
could take for a defect — an earlier revision stated a **closed count** ("three of
those absences are by design") over a ten-item list, whose arithmetic invited
exactly that. Nine of the ten are absent *by design in that state*; the tenth,
`pm-result-img`, is not an absence at all.

| absent | why, at `b293455` | appears when |
|---|---|---|
| `pm-signin` | rendered only when signed out (`App.tsx:2542`) | signed out |
| `pm-lora-add` (+ `pm-lora-row`, `pm-lora-weight`) | the whole `LoraSelector` is behind `familyHasLoras(checkpoint.baseModel)` (`App.tsx:2734`), and `LORA_FREE_BASE_MODELS = new Set(['OpenAI'])` (`models.ts:153`) excludes the default checkpoint's `baseModel: 'OpenAI'` (`models.ts:114-119`). Absent on first load for **every** viewer — a reaffirmed product decision. `pm-lora-cleared` (`App.tsx:2746`) takes its place when a note is pending | another model family is picked |
| `pm-remix-upload` | Remix tab only (`App.tsx:2304`) | the Remix tab |
| `pm-editor` / `pm-editor-*` / `pm-export-note` | the editor is a full screen swap behind `if (editing)` (`App.tsx:2039-2043`), and `editing` is set **only** by `yt-history-edit` | Add text on a real candidate |
| `pm-spent` | gated on `candidates.length > 0` (`App.tsx:2655`), i.e. **this click's** runs (`runCandidates(runs)`, `App.tsx:613`). History loaded from storage is not a spend, so three past generations do not produce it | a generation completes in-session |
| `pm-insufficient` | `phase === 'insufficient'` (`App.tsx:2586`) | the balance cannot cover the estimate |
| `yt-history-reloading` | only while a storage read is in flight (`History.tsx:274`) | during a refresh |
| `yt-storage-anon` | `storageState === 'anon'` only (`App.tsx:2429`) — so **absent for every signed-in viewer**, by design | signed out / anonymous storage |
| `yt-published-board` | rendered only behind `yt-board-toggle` (`FormatPicker.tsx:262`; the toggle is `App.tsx:2383`) | Browse published is clicked |
| `pm-result-img` | **not an absence — a dead id.** See the dead-id table above | never |

⚠ The converse also holds: `yt-history` and its children are absent for a viewer
with **no** history (`showHistory`, declared `src/history.ts:534-542`), so that measurement's stated
condition — three completed generations — is what made them present.

🔴 **`pm-save-note` is the SAVE RESULT surface and it reports FAILURE as readily
as success — read its text, never just its presence.** It lives in
`History.tsx:136`, outside any row, so one note describes whichever save last
fired. Measured 2026-09-30 on 0.1.7: clicking `yt-history-save` on **one** real
paid candidate rendered **`Couldn't save that image: image url is not allowed`**.
That is a **platform** refusal, not an app bug — the host's allowlist does not
carry the hostname this app's blobs are served from. Mechanism, scope and the
upstream fix: `flows/civit.ai.md`, section
*"`SAVE_IMAGE` is allowlisted by EXACT hostname"* — kept on ONE line so a
line-based grep for it actually matches the heading.

⚠ **Scope that to what was measured:** one click, on one model's blob host, at one
moment. It is evidence that **that** save was refused — not a proof that no save in
this block can ever succeed. Do not spend a generation to re-confirm it; do not
report it as a permanent property of the block either.
