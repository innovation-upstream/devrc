# Confirming a `type` actually landed — `--expect` / `--verify`

**Load this when:** you typed into a field and need to KNOW the text is there ·
a form submit behaved as if the field were empty · a value you set "disappeared"
on a React/Mantine/SPA page · a `type` reported success and the page kept showing
its old value · you got `bad_target`, `input_not_applied` or `input_not_verified`
· you are choosing between `--expect` and `--verify` on a secret field.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## 🔴 A bare `type` is not a claim about the DOM

`browser type <text> --selector <sel>` returns the same envelope whether the
insert landed or not:

```json
{"typed": 5, "trusted": true, "url": "…"}
```

`typed` is the length of the string the CLI **sent**. Measured live 2026-08-20:
the op replaces the value correctly most of the time and, intermittently, does
not apply **at all** while the page keeps rendering its previous state. Three
confident, wrong conclusions about a live site came out of reading `typed` as
evidence in one session.

## The two flags

| flag | asserts | on failure |
|---|---|---|
| `--expect <value>` | the field's value **equals** `<value>` | rc 1 `input_not_applied`, reports **wanted** and **got** |
| `--verify` | the typed text **arrived** — present, and the field CHANGED | rc 1 `input_not_applied`, reports a **length**, never a value |

Both annotate `result.data`:

```json
{"typed": 5, "applied": true,
 "verify": {"mode": "expect", "state": "ok", "attempts": 2, "len": 5}}
```

`applied` is **tri-state**: `true` / `false` / **`null` = NOT VERIFIED, not
"fine"**. `verify.state` is `ok` · `miss` · `bad_target` · `unverifiable`.

## Exit codes — the distinction that matters

| rc | means | when |
|---|---|---|
| 0 | the assertion holds | |
| 1 | the text is **not** in the target you named | a confirmed `miss`, or `bad_target` |
| 3 | the type applied and only the CHECK failed | `unverifiable` — the read could not be EVALUATED |

`bad_target` is rc **1**, deliberately. The extension's `focusExpression` only
checks that the element EXISTS and calls `.focus()`, so pointing `--selector` at
a wrapper `<div>` — what you get by lifting a selector out of `--annotated`
output — makes the type op report success while the insert goes to whatever is
actually focused. rc 3 says "never nothing happened", which would be false here.
The pre-read normally catches it **before anything is typed at all**.

rc **3** is reserved for reads that could not run: a strict page CSP (GitHub
does this), a `chrome://` tab, a rate-limited (429) follow-up.

With **no** `--selector` an un-editable target is rc 3, not `bad_target`: the
read targets `document.activeElement` but CDP types into whatever the RENDERER
has focused, and focus inside a shadow root reports the shadow HOST. Pass
`--selector` for a definite answer.

## 🔴 Why it costs 3 wire ops, and why that is not negotiable

A verified type is **pre-read → type → settle → read-back**.

| case | ops |
|---|---|
| happy path | **3** |
| 3 attempts, the insert lands | 9 |
| 3 attempts, nothing lands | 7 (the restore is skipped — nothing to undo) |

Two of those three ops are load-bearing:

**The settle is the DETECTOR, not politeness.** CDP `Input.insertText` mutates
the DOM value SYNCHRONOUSLY, so a read-back in the same tick reports success even
in the failing case — the value is there for a moment and a re-render from stale
state overwrites it. `BB_TYPE_SETTLE_S` (default 0.35s, max 30) is what makes the
read observe the state the USER is left looking at. This is why verification
lives in the CLI, not the extension.

**The pre-read is the ground truth.** `Input.insertText` inserts AT THE CARET and
does not select-all, so a retry that re-types without clearing CONCATENATES —
measured live: three attempts left ten copies of the text in a real SPA input,
and a later run left thirteen. The retry therefore restores the field to the
value the pre-read measured. Deriving that value instead — by stripping a
trailing copy of the typed text off the read-back — is only correct when the
insert LANDED, which is exactly the case this feature exists to detect: a field
holding `"my password"`, typed into with `"password"` and not applied, was
amputated to `"my "`.

Consequences you will see:

- `verify.retryStopped: "restore_failed"` — putting the field back did not stick
  (the page owns the value). The loop refuses rather than concatenate.
- `verify.retryStopped: "no_pre_state"` — the field could not be read BEFORE the
  type, so there is nothing to restore to and no retry is made.
- `verify.unchanged: true` — the field is byte-identical to what it held before.
  Under `--verify` that is a **miss** (nothing landed; the text was already
  there). Under `--expect` it is still a pass — the assertion is about the final
  state — but you are told.
- `verify.copiesAdded: N` — under `--verify`, the insert added more than one copy.

## Privacy — `--verify` on anything secret

`--verify` never reports a value, in any branch: a length and, at most, a count.

`--expect` does **not** echo on a pass (no `got`, no `want` — only a length). On
a **miss** it reports `got`, because that is the diagnostic the flag exists for —
and `got` is the PAGE's content by construction, not an echo of your argument.
On a password field, use `--verify`.

## Tuning

| var | default | bounds |
|---|---|---|
| `BB_TYPE_ATTEMPTS` | 3 | 1..10 — each attempt is up to 3 ops against the live browser |
| `BB_TYPE_SETTLE_S` | 0.35 | 0..30, e.g. `0`, `0.35`, `.5` |

Both are validated before any op reaches the wire; a malformed value is rc 1
naming the variable, never a silent fallback. Raise the settle on a heavy SPA, or
if a 429 trips.

## Not covered by the headless suite

`scripts/browser-bridge/tests/test_browser_type_verify.py` pins the control flow
against a stub bridge. It cannot observe a real `Input.insertText` landing in a
real renderer and then being overwritten by a framework re-render — see
`reference/security-ops.md`, which makes live-verify-on-real-Brave the mandatory
gate for any browser-bridge change.
