# `/analyze-service` recon cost — the recorded baseline

Re-run it: `python3 scripts/session-analysis/recon_cost.py`
Compare against this baseline: `… --compare claudedocs/analyze-service-baseline/BASELINE.json`

## What is here

`BASELINE.json` — n=22, produced by `scripts/session-analysis/recon_cost.py`.
The machine baseline `--compare` reads. It is the **only** stored copy of these
numbers; a `BASELINE.txt` alongside it was a third copy of the same figures and
was removed rather than kept in sync (and the repo's `.txt` content gate flagged
it, correctly, as prose in a format that gate reserves for captured text).

## The two measurements, and why they differ

| run | n | instrument | median res_KB | median tool_calls | median asst_turns |
|---|---|---|---|---|---|
| original | 20 | the one-off harness this work replaced | 35.5 | 22.5 | 39.5 |
| recorded | 22 | `recon_cost.py` (this repo) | 36.1 | 23.0 | 42.0 |

Also from the original run: mean 37.8 KB, max 91 KB / 62 tool calls,
p90 ≈ 15.4k tokens, and 359 Bash calls at a mean of 1.5 KB.

🔴 **They differ, and the difference is accounted for rather than reconciled.**
Two more real invocations landed between the two runs (20 → 22), and the
productionized harness counts **every** window in a transcript where the one-off
took only the first. `recon_cost.py` reproducing the original figures to within
that, from a separately written instrument, is what makes the original number
credible — and is why the original is recorded here rather than discarded.

## The finding that decided the fix

Cost was **death by a thousand cuts**, not one fat dump: 412 Bash calls at a mean
of ~1.2 KB, largest single result 22.9 KB. So the lever is **collapsing round
trips**, not truncating output — which is why the fix is one deterministic call
(`scripts/lib/service_recon.py`) rather than an output cap.

The tool mix independently reproduces the other finding: **`Grep` = 0 and
`Glob` = 0** across every measured window, while the skill body instructed
"prefer the Grep/Glob tools". A permanently-ignored line in an always-loaded body
is pure cost; it was removed rather than restated.

## 🔴 No captured text is recorded here

`BASELINE.json` is **numbers, category names and tool names only** — no command
string, message body, path, session id or repo name. `recon_cost.py` is written
so its text AND `--json` surfaces structurally cannot carry any, and
`scripts/session-analysis/tests/test_recon_cost.py::TestNoCapturedTextEscapes`
asserts it against a fixture built from strings that would be unmistakable if
they leaked. The one-off harness this replaced DID print the invoking command
line; that is the one behaviour deliberately not carried over.
