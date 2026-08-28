# Diagnosing a CSS / layout bug (hit-test, don't theorise)

**Load this when:** an element is in the DOM but invisible, unclickable, or painted
UNDER something else · a popover/dropdown/tooltip renders behind neighbouring content ·
a `z-index` change "does nothing" · a click lands on the wrong element · a
`data-testid` selector matches NOTHING on a production page · you are about
to reason about paint order from CSS source instead of measuring it · **users take the
wrong action on a page that "looks fine"**, or you need to argue about visual
hierarchy with numbers · **a page's text "shrank"** and you must say whether it was
deleted or merely relocated.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## 🔴 `data-testid` is STRIPPED in production builds — it silently finds NOTHING

Before you conclude "the element isn't there", check what you selected ON. At least
the civitai app strips it: `next.config.mjs` sets
`compiler.reactRemoveProperties` under `NODE_ENV=production`, which deletes
`data-testid` from the shipped DOM. **This is a common Next/SWC production
setting generally**, so assume it of any prod Next app.

The failure is silent and reads as a product bug: the selector matches zero nodes,
`click`/`text` report nothing found, and the element is right there on screen.

Select **structurally** instead:

- semantic **roles / visible text** (`button`, heading text, `aria-label`);
- **DOM shape** (parent/child/nth relationships that the build can't erase);
- a **computed style** — e.g. `getComputedStyle(el).aspectRatio` to pick out cards
  of a known ratio.

⚠ Only `data-testid` is targeted. **Non-`testid` data attributes are NOT stripped**,
so a purpose-built one survives into prod and makes a reliable selector. If you own
the app and need a stable hook for browser-driving, add a non-`testid` data
attribute rather than fighting the strip. *Which* attribute a given site already
ships is a SITE fact, not a mechanism one — see `reference/sites/<host>.md`.

The bridge is the only way to see PAINT ORDER. Markup-level tests and `html` reads
can't: an element can be present, correct, and completely covered. The sequence
that found a real one (civitai-manager v0.1.82 — an open popover painted under the
next card, after ~30 UI changes had passed every server-side test):

**1. `open` → `wake` → `screenshot` — and LOOK at the image.** The un-throttle is
not optional; a backgrounded tab is throttled and may never finish painting, so
you'd screenshot a half-built page. Use `wake` (non-intrusive), NOT `activate` —
`screenshot` already works on a background tab via CDP, so nothing here needs the
real foreground. Exit 0 is not a rendered page. `screenshot` prints a `.png` PATH
(not a data URL) — `Read` that file; that is the LOOK step.

**2. Hit-test the suspect element.** Take its `getBoundingClientRect()` and call
`document.elementFromPoint(x, y)` at several points inside it, reporting for each
whether the hit node is `contains()`-inside the element you expected. This NAMES
the covering element instead of guessing — in the real case it returned the *next*
card's NSFW-reveal `<button>`, which reading the popover's own CSS would never
have suggested.

🔴 **`elementFromPoint` returns `null` for a point OUTSIDE the viewport — which is
indistinguishable from "something is covering it".** Read `null` as *unknown*,
never as *covered*: it is how a hit-test invents a blocking overlay that does not
exist. Off-canvas drawers and mobile menus are the common source, and they sit at
NEGATIVE coordinates (measured on one app: a nav control centred at `x = -249`). A
zero-size rect has no meaningful centre either. So the probe **skips** both cases
and labels them, rather than folding them in with the covered points:

```bash
$BB --instance work js '(function(){
  const el = document.querySelector(".cm-updated-pop");
  const r  = el.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return "zero-size rect — nothing to hit-test";
  const inVp = (x,y) => x >= 0 && y >= 0 && x < innerWidth && y < innerHeight;
  const pts = [[r.left+8, r.top+8], [r.left+r.width/2, r.top+r.height/2], [r.right-8, r.bottom-8]];
  return JSON.stringify(pts.map(([x,y]) => {
    if (!inVp(x,y)) return {x, y, skipped: "off-viewport"};
    const hit = document.elementFromPoint(x, y);
    return {x, y, hit: hit && hit.className, insidePop: !!hit && el.contains(hit)};
  }));
})()'
```

Any `insidePop:false` is the bug, and `hit` is the culprit. A `skipped` point is
**not** evidence either way — if every point is skipped you have measured nothing,
so say so instead of reporting "not covered".

**3. Walk the ancestors for the first stacking-context creator.** The offender is
almost never the element you're staring at. Check each ancestor's computed
`transform`, `filter`, `opacity` (<1), `isolation`, `will-change`, `contain`, and
`position`+non-`auto` `z-index` — the first one that creates a context traps every
`z-index` below it.

```bash
$BB --instance work js '(function(){
  const out=[]; let n=document.querySelector(".cm-updated-pop");
  while (n && n !== document.documentElement) {
    const s = getComputedStyle(n);
    out.push({cls:n.className, pos:s.position, z:s.zIndex, tf:s.transform,
              flt:s.filter, op:s.opacity, iso:s.isolation, wc:s.willChange});
    n = n.parentElement;
  }
  return JSON.stringify(out);
})()'
```

**4. Inject a probe `<style>`, re-hit-test, THEN write code.** Proving the fix in
the live page before touching the repo turns a guess into a measurement.

```bash
$BB --instance work js '(function(){
  const s=document.createElement("style"); s.id="probe";
  s.textContent=".cm-lift:has(.cm-updated:hover){z-index:25}";
  document.head.appendChild(s); return "probed";
})()'
# ...re-run the step-2 hit-test; every point should now report insidePop:true...
$BB --instance work js '(function(){ const s=document.getElementById("probe"); if(s) s.remove(); return "clean" })()'
```

⚠ **A probe that clears the problem can still be wrong in the OTHER direction.**
The first value tried in the real case cleared the overlap fine — and would have
painted the card over the sticky nav. Only checking the UPPER bound caught it. So
hit-test both: the thing that was covered, *and* the chrome your fix now
out-ranks. **Always remove the probe** — it's the user's live page.

⚠ Reiterating, because it bites hardest here: **`eval`/`js` takes ONE EXPRESSION.**
All four steps above are multi-statement, so every one of them must be wrapped in
`(function(){ … })()` — otherwise you get `null` with no error and spend the next
ten minutes debugging a bridge that is working fine.

## Is the LOUDEST control the one that works? — measure prominence

A different question from paint order, same instrument. Hit-testing asks *what covers
what*; this asks **whether visual weight matches importance** — and it catches a defect
no markup assertion can express, because every element is present, correct, and visible.

For every visible control read `getBoundingClientRect()` **size and y**, plus computed
**`opacity`** and **`backgroundColor`**:

```bash
$BB --instance work js '(function(){
  return JSON.stringify([].slice.call(document.querySelectorAll("button, a.btn, [role=button]"))
    .filter(function(el){ var r=el.getBoundingClientRect(); return r.width && r.height })
    .map(function(el){
      var r=el.getBoundingClientRect(), s=getComputedStyle(el);
      return {text:el.textContent.trim().slice(0,40), w:Math.round(r.width), h:Math.round(r.height),
              y:Math.round(r.top), opacity:s.opacity, bg:s.backgroundColor,
              disabled:el.disabled===true || el.getAttribute("aria-disabled")==="true"};
    }));
})()'
```

**What it found (civitai-manager, 2026-08-03):** a **disabled** button measured 279×36 at
y=102 while the **working** one was 251×30 at y=336 — *the same fill*, differing only by
`opacity: 0.6`. So the largest, highest, loudest control on the panel did nothing, and the
affordance that actually resolved the user's problem was the smallest element there. A
screenshot showed both and read as fine; **the numbers are what made it arguable.**

Read the output for: a disabled control **larger or higher** than the live one; the primary
action **below** the fold of its own panel; several controls sharing one `backgroundColor`
so the fill no longer signals "primary"; `opacity` as the *only* disabled affordance —
which is a contrast failure as well as a hierarchy one.

## "The page got shorter" — was text DELETED or just RELOCATED?

`innerText` is **rendered** text and omits anything inside a closed `<details>`;
`textContent` returns all of it. Comparing the two separates the cases — and they are not
the same finding, so never report one as the other.

```bash
$BB --instance work js '(function(){
  var n=function(s){ return s.replace(/\s+/g," ").trim().length };
  return JSON.stringify({visible:n(document.body.innerText), total:n(document.body.textContent)});
})()'
```

- **both drop** → text really was removed;
- **`visible` drops while `total` holds** → it moved behind a disclosure.

Verified on a probe page with one closed `<details>`: `innerText` **68** chars vs
`textContent` **120**, and the hidden string appears in `textContent` only; opening the
element brings it into `innerText`. In the real case a **47% visible-text reduction** was
~1,000 of ~1,180 characters *merely moved* behind disclosures — reporting that as deletion
would have been wrong.

⚠ `innerText` depends on layout, so it needs a rendered tab: **`wake` first**, or a
throttled background tab returns a shell and both numbers lie.

## A JS `.click()` cannot open a React/Mantine popover — and the read reports ABSENCE

`SKILL.md` trap 4 carries the instruction; this is the evidence behind it.

`element.click()` inside a `js` payload left `aria-expanded="false"` and found only
**empty `.mantine-Popover-dropdown` shells** — indistinguishable from "the menu entry
isn't there", which is what makes it a silent wrong answer rather than an error.

The trusted **`click`** op (real CDP input on the top frame) does open it. Two things
that still bite after switching:

- It is a **TOGGLE**. Two clicks open then close it, so a stale earlier click makes the
  next read report a confident absence. Click **once**, and read `aria-expanded` in the
  same breath so the read carries its own proof.
- On some builds the dropdown's links are **not reachable** via
  `.mantine-Popover-dropdown a` even once open. **`screenshot` it** rather than
  selector-hunting — a selector that matches nothing looks exactly like missing content.

## 🔴 A menu can have a SECOND VIEW — "not in the open dropdown" ≠ "not in the UI"

Sibling of the trap above, and it survives everything that fixes that one: you used
the trusted `click`, `aria-expanded` reads `true`, you settled, you read the whole
dropdown — and the thing is still not there, because it lives behind a **drill-in**
that swaps the menu's contents in place.

Measured 2026-08-21 on civitai's header menu. The account switcher is a distinct view
of the same popover, reached by clicking the **avatar row** (own username + a `›`
chevron-right). Before that click its accounts are absent from `innerHTML` entirely —
a full-document search for the username returned **false**, which read as "this
profile does not have that account" and was recorded as a blocker. It was one click
away. The generic tells:

- a row whose only affordance is a **chevron-right** — that is a drill-in, not a link;
- a `Back` item appearing after you click something (proof you were in view 2);
- a dropdown that seems oddly short for the surface it belongs to.

**So: before reporting a menu entry absent, click any chevron-row and re-read.** And
prefer a `screenshot` for this judgement — a human glance settles "is there another
view?" instantly, where a text scan cannot distinguish view 1 from the whole menu.

🔴 **Related, same session: a text scan for a control's LABEL can miss it entirely
when the control is an ICON.** Searching for `Logout` returned zero elements while a
logout icon-button sat in the same footer. Match on `aria-label` / `role` / an icon
class, not visible text, before concluding a control does not exist.
