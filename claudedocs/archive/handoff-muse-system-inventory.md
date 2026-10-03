## Evicted from `claudedocs/handoff-muse-system-inventory.md` — 2026-10-02

These are SECOND COPIES, not retired knowledge: three append rounds each re-wrote the same six lessons (go-test panic hollows a sweep, spelled guards, unexported-internals-as-a-pair, fixture constants, a payload change voiding a mutation control, naming a payload unit) and the FULLER first copy of every one REMAINS in the handoff. So read these only to recover wording the surviving copy lost — they carry no lesson the doc no longer states, and nothing here should be re-derived into it.

From `Gotchas / decisions / dead-ends`:

- 🔴 **A TEST THAT PANICS ABORTS THE GO TEST BINARY AND SILENTLY HOLLOWS OUT A
  MUTATION SWEEP.** Measured: de-gating a handler made one route test panic, so
  the run printed ONE failure and the two seam guards that should also have
  fired never executed — reading as "those guards missed the mutant".
  `defer recover()` in the loop body and report the panic as a failure. **Any
  sweep over a suite where one test can panic is measuring a PREFIX of itself.**
- **A SPELLED guard is walkable even when you wrote it knowing that.** Two
  successive route guards fell to a different spelling; the second keyed on the
  variable name (`m2 := mux`) four lines under its own warning about exactly
  that. What finally held was enumerating what the system really serves:
  `http.ServeMux`'s `index.segments` (`map[routingIndexKey][]*http.pattern`)
  plus `index.multis`, with `pattern.str` the registered string — reachable by
  plain reflection, `f.String()`, no `unsafe` needed.
- **Reading unexported internals is defensible only as a PAIR:** `t.Fatal` on
  every absent field, so a rename breaks loudly instead of silently ceasing to
  check, AND a positive control per BRANCH that registers an extra item and
  asserts it is seen. One `segments`-shaped control left the `multis` loop
  no-op'able and surviving.
- **A fixture constant can make a whole class invisible:** every route test
  built its server with `k8s: nil` on purpose, so a registration conditional on
  `s.k8s` appeared in neither the table nor the mux, they agreed, and production
  would have served it with no token. Ask which dimension your fixture pins.
- **A payload change can void a mutation control in the same commit with the
  suite green throughout** — mutant KILLED at the base sha, SURVIVED at the
  head. Pin an invariant against a HAND-BUILT input, not one produced by the
  code under test.
- ⚠ **Say which unit a payload figure is.** The posted `payload=40` for round 2
  counted comment lines inside payload files; the executable-line figure is 10.
  One name, one number, per round.
