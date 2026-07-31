Continue the browser-bridge work. Canonical handoff (read first):
  ~/workspace/devrc/claudedocs/handoff-browser-bridge-2026-07-31.md

First action: measure deepseek-flash on ~10 real goals (~$0.06, ~20 min) — it gates the
`browser agent`-first default flip, which is designed and approved but deliberately
unshipped. `browser agent` NOW WORKS (verified 2026-07-31, first successful run ever)
but its capability is unmeasured beyond two trivial tasks. Do not flip the default on
arithmetic alone.

Then: stable extension path + manifest version bump (biggest infra win — cost 3 Brave
restarts and a silently-reverted staged build last session).

Non-negotiables:
- Run `browser whoami` FIRST. Both hosts are hostname `nixos`.
- Live-verify against real Brave is the ONLY gate. Last session FOUR features passed full
  test suites AND clean adversarial audits while broken in reality. Twice the breaking
  change was one an audit had requested — an audit fix RESETS the verification gate.
- Build a deterministic "is the new build loaded?" tell into any extension change (a new
  op name → `unknown_op` on the old build). Without one, reload-vs-restart is unfalsifiable.
- devrc is a SHARED checkout other sessions mutate mid-task. Check which branch you're on
  before any pull/checkout; re-verify files on disk immediately before a live check.
- Never kill Brave to force a reload (`restore_on_startup` is unset — tabs are lost).
  Ask for the ↻ click.
