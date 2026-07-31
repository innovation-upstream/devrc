# Diagnosing a CSS / layout bug (hit-test, don't theorise)

**Load this when:** an element is in the DOM but invisible, unclickable, or painted
UNDER something else · a popover/dropdown/tooltip renders behind neighbouring content ·
a `z-index` change "does nothing" · a click lands on the wrong element · a
`data-testid` selector matches NOTHING on a production page · you are about
to reason about paint order from CSS source instead of measuring it.

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
so a purpose-built one survives into prod and makes a reliable selector — civitai
added `data-listing-cover-placeholder` for exactly this. If you own the app and
need a stable hook for browser-driving, add a non-`testid` data attribute rather
than fighting the strip.

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

```bash
$BB --instance work js '(function(){
  const el = document.querySelector(".cm-updated-pop");
  const r  = el.getBoundingClientRect();
  const pts = [[r.left+8, r.top+8], [r.left+r.width/2, r.top+r.height/2], [r.right-8, r.bottom-8]];
  return JSON.stringify(pts.map(([x,y]) => {
    const hit = document.elementFromPoint(x, y);
    return {x, y, hit: hit && hit.className, insidePop: !!hit && el.contains(hit)};
  }));
})()'
```

Any `insidePop:false` is the bug, and `hit` is the culprit.

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
