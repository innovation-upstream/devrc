"""The one budget every test that spawns the real `browser` CLI must use.

🔴 A TEST'S SAFETY-NET TIMEOUT MUST NOT BE TIGHTER THAN THE BOUND OF THE THING IT
INVOKES. The CLI bounds each of its HTTP calls at `curl -m 60`. Tests spawning the
real CLI used `timeout=30` (test_server.py) or `timeout=60` (three sibling files)
— so the TEST's net fired first, or tied. A stall could then never surface as the
CLI's own attributable error; it surfaced as an opaque subprocess.TimeoutExpired
naming the test instead of the cause, and being wall-clock it flaked under CI
load. Measured 2026-08-25: `test_browser_cli_backs_off_on_429` failed exactly that
way in the devrc-pytests gate.

🔴 SIZED BY THE WORST CASE, WHICH IS FOUR CURLS, NOT ONE — AND NOT THREE. A single
invocation can issue several bounded curls. The maximum is
`emulate --reset --recreate`: `_emulate_reset_recreate` issues **emulate, release,
open, close** — four — and the suite asserts exactly that sequence
(`test_browser_cli_args.py`, `assert ops == ["emulate", "release", "open",
"close"]`) on a test that drives the real CLI subprocess. So the worst case is
4 x 60 = 240s.

⚠️ TWO EARLIER DRAFTS OF THIS RATIONALE WERE WRONG, which is why it now cites a
test instead of a reading: 90 (above one curl, below the real worst case), then
240 with the justification "close does release + open + close" — `close` is a
SINGLE `cmd_op close`; that three-op reading was the `--recreate` path miscounted,
and 240 exactly TIED the true worst case, which the strictness rule says must
lose. 300 clears it with headroom.

⚠️ HONEST COST: on a test that genuinely hangs, the suite waits 300s at that site
instead of 30s. That is only paid when a test is ALREADY failing, and it buys an
attributable error instead of a timeout. If starvation ever becomes systemic
rather than per-test, a run of hangs could approach the CI Task ceiling and turn a
clean red into a pipeline abort — a strictly less informative signal. The fix then
is a per-test `pytest-timeout` budget, NOT lowering this back under the CLI's bound.

🔴 THIS LIVES IN ITS OWN UNIQUELY-NAMED MODULE, NOT conftest.py. `conftest` is not
namespaced: with 7 `conftest.py` files in this repo and no `__init__.py`, an
importer binds to whichever lands in sys.modules first, so
`pytest scripts/browser-bridge/tests scripts/repo-cos/tests` died with
`ImportError: cannot import name 'CLI_TIMEOUT_S' from 'conftest'` — blaming the
wrong file. The shipped gate runs one target per pytest invocation and was safe,
but "safe by how it happens to be invoked" is not a property to rely on.

Pinned by `test_cli_subprocess_timeouts_outrank_the_cli_own_curl_bound`.
"""

CLI_TIMEOUT_S = 300
