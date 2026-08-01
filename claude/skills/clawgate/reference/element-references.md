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

The enrichment is built by `buildEnrichment` in `extension/content.js`. It reads `window.location` for page context and walks `previousElementSibling` / `nextElementSibling` for adjacent text, capping each at 120 chars. To modify enrichment behaviour, edit that function — `lib.js` stays pure and handles only selector building and accessible name computation.
