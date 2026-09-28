# No write verb has ever executed against real GitHub

Demoted from `claudedocs/handoff-mention-review-tui.md` on 2026-09-21 under that doc's byte
ceiling (playbook step 2 — *demote dated evidence, leave a pointer*). 🔴 **STILL OPEN.** Its
probe is **operator-only**, so it cannot close on an agent's initiative, and the handoff keeps a
pointer to it.

- as-of: 2026-09-16
- **Symptom + exact repro:** n/a — an untested path in shipped code, not a defect. The five
  write verbs are exercised only against in-process fakes and `httptest`.
- **Observed (with values):** the whole Go suite (231 tests) passes inside `nix build`'s
  **network-less sandbox**, which is itself the proof no test reaches a real host. Four
  additional locks: `App.runner` is `nil` in pure tests; the one end-to-end test asserts the
  write ledger is *exactly* `[PostComment … body="ok"]`; `http.DefaultTransport` is replaced in
  both network-reaching packages by a loopback-only transport; `cmd/*` is exempt with a stated
  reason. The transport lock was verified directly — disarming it yields
  `the guard let a request to api.github.com THROUGH`.
- **Ruled out:** that the guard is vacuous — mutated it and watched the negative control fire.
  ⚠ The FIRST mutant did not compile (orphaned `fmt`), which is not a result; a compiling
  variant is what produced the kill. via: measurement
- **Leading hypothesis:** none. `LiveRunner`'s three delegations and `ghapi`'s three endpoints
  are plain code paths that have simply never run live.
- **Next probe:** open a throwaway PR in a scratch repo and drive `c` (comment) then `m`
  (merge) against it. 🔴 **OPERATOR-ONLY — an agent must not press a write key.** The five
  verbs (`c`/`a`/`R`/`v`/`m`) act on real GitHub as the operator.

## Why this matters more now than when it was written

`mention-review` is in daily real use, and the merge verb in particular is reached from the
operator's own review flow. The 2026-09-18 "unprocessable entity" incident (`#1761` →
`1ff1bd6e`, renderer discarded `errors[]`) was on a write path, and its cause was never
established because the response body was unrecoverable. A single throwaway-repo exercise of
`c` then `m` would convert the whole class from "never run" to "run once, observed".
