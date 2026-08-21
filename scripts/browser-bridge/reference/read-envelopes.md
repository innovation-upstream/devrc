# Read envelopes, `context`, and `text --annotated`

Load this when you need the **exact field names** a read returns, when a
lightweight metadata read would beat a DOM read, or when you are doing
selector/element extraction with `text --annotated`.

Nothing here changes *whether* a read works — that is `reference/spa-wake.md`
(throttled tabs) and `reference/errors.md` (error strings). This file is the
shape of the payload.

All result payloads land under `.result.data`.

---

## `context` — page metadata with NO DOM read

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB --instance <key> --tab <id> context
```

Returns:

```json
{ "url": "...", "domain": "...", "path": "...", "searchParams": {...},
  "title": "...", "tabId": 1234 }
```

- **Tab-scoped** — needs an active or owned tab, same as `text`/`html`.
- **No required fields**: every field is best-effort; do not assume presence.
- It does **not** read the DOM, so it is cheaper than `text` and is not
  affected by how much content the page rendered.

**Prefer `context` over `text`/`html`** whenever the question is "which page am
I on / did the nav land / what's the id in the query string" — i.e. anything
answerable from the URL, domain, path, query params, title, or tabId.

⚠ Because it never touches the DOM, `context` is **not** a way to check whether
a page has rendered. A throttled hidden tab still reports a perfectly good
`url`/`title`. To know whether content exists, do a real `text` read and check
`visibilityState` / `hidden` — see `reference/spa-wake.md`.

## `text` / `html` envelopes

Both reads return the same metadata alongside the content, so a single read
tells you *what you read* as well as *what it said*:

| field | notes |
|---|---|
| `url`, `title` | of the tab that was actually read — check this if you are running concurrent tabs |
| `domain`, `path`, `searchParams` | parsed from `url`; same values `context` returns |
| `tabId` | which tab answered — the authoritative check that `--tab` routed where you meant |
| `text` (on `text`) / `html` (on `html`) | the content |
| `truncated` | `true` when the byte cap clipped the content; a truncation note is also appended to the content itself |
| `visibilityState` | the page's `document.visibilityState` |
| `hidden?` | present when the tab is hidden/backgrounded → suspect throttling |
| `note?` | present when the bridge has something to tell you about the read |

The byte cap (`--max-bytes N`, default 32768, `0` = uncapped) applies to the
content field only, not to the metadata.

## `text --annotated` — structured element extraction

`--annotated` replaces the flat `innerText` string with a **list of elements**,
each carrying its DOM path and the attributes you need to build a selector:

```json
{ "text": "Sign in",
  "path": "html > body > div#root > form > button.btn.primary",
  "tag": "button",
  "attrs": { "id": "...", "class": "...", "href": "...", "src": "...",
             "alt": "...", "title": "...", "name": "...",
             "placeholder": "...", "type": "...", "role": "...",
             "aria-label": "...", "data-testid": "...", "data-cy": "...",
             "data-e2e": "..." },
  "precedingText": "...",
  "followingText": "..." }
```

- `attrs` carries exactly those keys — `id`, `class`, `href`, `src`, `alt`,
  `title`, `name`, `placeholder`, `type`, `role`, `aria-label`, `data-testid`,
  `data-cy`, `data-e2e`. Absent attributes are simply not present.
- `precedingText` / `followingText` give the surrounding copy, which is how you
  disambiguate three identical "Download" buttons.
- Still **byte-capped** (`--max-bytes`), and still far cheaper than `html`.

**Use it when** you need to *act* on the page (`click`/`type` need a selector)
and flat `text` gave you the label but not a way to address the element. For a
pure "what does the page say" read, plain `text` is smaller.

**Works with `--frame`** — structured element extraction runs inside the target
frame. CSS paths are frame-relative (the frame's own DOM), and `url`/`domain` in
the envelope reflect the frame, not the top page.

An element that `--annotated` lists but that will not click is a paint-order /
hit-test problem, not an extraction problem → `reference/css-hit-test.md`.

## `screenshot` output modes — the ONE op whose stdout is not JSON

`screenshot` **always writes a `.png`**: to `<path>` when you give one, else to a
mode-0600 temp file (honouring `TMPDIR`, auto-pruned after 24h). The base64 data URL is
**never** printed — `Read` the `.png`.

| invocation | stdout |
|---|---|
| `screenshot` | compact JSON `{ok,path,bytes,url,via,note}` |
| `screenshot <path>` | ⚠ the **bare path** on line 1 and a `#`-prefixed `Read` hint on line 2 — NOT JSON |
| `screenshot <path> --json` | the same compact envelope the no-path form prints |
| `screenshot --data-url` | the RAW response envelope, data URL and all (100s of KB) — the escape hatch |

Every other subcommand puts a JSON envelope and nothing else on stdout; the advisories
(hidden tab, disconnected instance, routing help) all go to STDERR. `screenshot <path>`
is the back-compat exception, so a caller doing `json.loads(subprocess.run(...).stdout)`
gets `Expecting value: line 1 column 1` — measured 2026-08-20, and the reason three
consumers broke. `--json` is the fix, and it is ADDITIVE: the default output is
byte-identical to before.

Two pairs are **refused**, not silently reconciled: `--data-url` with an explicit path,
and `--data-url` with `--json` (the raw envelope has no `path`/`bytes` to report, so
accepting the pair would hand back a different parseable envelope with none of the keys
the caller was about to read).
