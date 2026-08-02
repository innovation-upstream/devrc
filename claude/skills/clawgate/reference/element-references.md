# clawgate — resolving element references in a task body

Read when: a task body contains element references picked with the browser extension (lines that
start with `- ` and a selector) and you need to find the corresponding element in an app's source
code.

🔑 The one thing to know before you start: **do not search the selector first.** It is the *least*
durable signal — selectors are generated and churn. Work domain/path → adjacent text → selector →
accessible name, as below.

---

## resolving element references

When a task body contains element references from the browser extension (lines starting with `- ` and a selector), the enriched format (v1.4.0+) includes page context and adjacent text that help locate the element in source code.

### Reference format

```
- `#remix-row-cf23 > div.flex > button.grid` — button "Delete remix" (aigeum.com/remixes · prev: "Edit" · next: "Share")
```

### Resolution strategy

Work from the most specific signal inward:

1. **Domain + path** (`aigeum.com/remixes`) — identifies which route/page the element lives on. Search for the route in the app's router config or page components.
2. **Adjacent text** (`prev: "Edit"`, `next: "Share"`) — search for these strings in the source code. They're likely rendered by the same component that owns the picked element. The preceding text often appears just before the element's component in JSX/templates.
3. **Selector** (`#remix-row-`) — search for the element directly in DOM-generating code (look for the ID, testid, or tag+class combination).
4. **Tag + accessible name** (`button "Delete remix"`) — further narrows the search (e.g. search for that label text in templates).

### Practical example

```
Reference: - `#remix-row-cf23 > div.flex > button.grid` — button "Delete remix" (aigeum.com/remixes · prev: "Edit" · next: "Share")

Resolution strategy:
1. domain=aigeum.com, path=/remixes → look for route "/remixes" in router config
2. prev="Edit" → search for "Edit" string in that route's components
3. selector has #remix-row- prefix → search for "remix-row" in component code
4. tag=button, name="Delete remix" → search for "Delete remix" label text
```

### Developer note

The enrichment is built by `buildEnrichment` in `extension/content.js`. It reads `window.location`
for page context and walks `previousSibling` / `nextSibling` (the **node** variants — text nodes
count) for adjacent text, slicing each side at 40 chars; `refText` in `lib.js` then caps at
`MAX_REF_TEXT_CHARS` (80). To modify enrichment behaviour, edit that function — `lib.js` stays pure
and handles only selector building and accessible name computation.

🔴 **Every page-text read in the walk MUST go through `adjacentText` → `pageText`.** That is the
privacy choke point: element siblings get `pageText` (self / ancestor / descendant via the derived
`TYPING_SURFACE_SEL`), text siblings are withheld unless `nearTypingSurface(parentElement)` is
false, and anything unrecognised returns `""` — fail-closed. Reading `.textContent` directly here is
the bug that shipped in the first cut of 1.4.0 (fixed in `fcaca875`, PR #276): it put the operator's
typed text on the wire. **Never add a second "is this a text field" predicate** — derive from the
shared sets so a new role covers self, ancestor and descendant at once. This same root cause
recurred through six audit rounds on the picker and a seventh here.

### 🔴 Testing the privacy guard: most obvious tests CANNOT fail

**A native `<input>` or `<textarea>` never exposes its current value through `textContent`.** Typing
updates `.value`; `.textContent` stays whatever the server rendered (empty, if it was empty). So a
test that types a canary into a form field and picks the neighbouring element **passes under the
leaking build too** and proves nothing. Three consecutive live captures were wasted this way on
2026-08-02 (tasks #147–#149), each read as a pass.

Only these carry user text in `textContent`:
- `contenteditable` elements and `role="textbox"` divs (Gmail / Slack / Notion compose boxes)
- a `<textarea>`'s **server-rendered initial** content — not what was typed into it

A capture is only discriminating if the withheld neighbour's text was **server-rendered**. The
fixture that settled it (task #150): a button flanked by two `contenteditable` divs holding known
canaries, plus a second button beside a plain `<span>` as a positive control — the guard must
withhold the canaries *and* still capture the span, or it is a blanket refusal rather than a fix.

⚠ The keystroke path itself (`chrome.commands`) is **not reachable from Playwright** — every spec
posts to the service worker directly. A live capture by hand is the only way to verify the build
actually loaded in Brave, which is why an out-of-band flat copy (below) is dangerous.
