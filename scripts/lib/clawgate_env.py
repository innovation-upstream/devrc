"""The ONE place a clawgate PRODUCER resolves its hook token.

🔴 WHY THIS MODULE EXISTS (clawgate task #307). Both producers in this repo read
the token from `os.environ` alone and, finding nothing, posted nothing and said
nothing:

    scripts/signal/clawgate.py       token = os.environ.get("CLAWGATE_HOOK_TOKEN")
    scripts/mail-actions/clawgate.py token = os.environ.get("CLAWGATE_HOOK_TOKEN")

The token is NOT in the process environment on this host — it lives in
`~/.claude/clawgate.env`, which is where `clawgatectl` (and `repo-cos`) already
look. Measured: present in the file, absent from a plain shell's env and from a
`nix-shell` one. So a real Signal draft was stored with no card posted and no
line anywhere saying a card had been skipped: the two producers were wrong in
the SAME direction, which is why this is ONE definition and not two fixes.

🔴 WHAT IS DELIBERATELY *NOT* CHANGED. The graceful no-op is the design (D3, in
`scripts/signal/clawgate.py`'s docstring): "a missing token degrades
notification, never the record". A producer that cannot resolve a token must
still return, and its caller must still store the draft / action item. This
module only changes WHERE the token is looked for and makes the skip AUDIBLE. It
never raises on a missing file, a malformed line, or an unreadable path.

PRECEDENCE — read from `clawgatectl`, not invented here. See
`homelab-talos/containers/clawgate/cmd/clawgatectl/config.go` `resolveConfig`
(line 94) and `defaultEnvFile` (line 15): LOWEST to HIGHEST,

    ~/.claude/clawgate.env  ->  process environment  ->  --token

with a later source overriding an earlier one ONLY when it actually supplies a
value, so `CLAWGATE_HOOK_TOKEN=` exported empty does not blank out the file. The
`--token` tier has no analogue here (a producer takes no flags), so the chain
this module implements is the first two, in that order.

🔴 THE TOKEN IS NEVER PRINTED. Not in the warning, not in an exception, not in
argv. `parse_env_file` returns values to its caller and nothing else; the
warning below names the VARIABLE and the PATH it looked in, never a value.
Same rule as `config.go`'s `config` struct comment and
`lib/clawgate_tasks.read_clawgate_env`.

DEPENDENCY-FREE ON PURPOSE: stdlib only, no `requests`, no DB layer, no clock.
`scripts/signal/` and `scripts/mail-actions/` both import a DB module that needs
psycopg2; this one must be testable without it, and it is.

⚠ RELATED BUT DELIBERATELY SEPARATE: `lib/clawgate_tasks.read_clawgate_env`
parses the same file for the CONSUMER surfaces (the bar poller, session-manager)
and has different semantics on purpose — it RAISES `KeyError` on a missing token
because a status pill that cannot authenticate must go stale, not quietly report
zero, and it has no environment tier at all. Folding the two together would give
a producer a raise it must not have and a poller a silent-degrade it must not
have. `repo-cos/clawgate.load_creds` is a third parse with a third policy
(returns `{}`, also reads `CLAWGATE_API_URL`); it is out of scope for #307 and
noted here so the next reader knows it was seen, not missed.
"""
from __future__ import annotations

import os
import sys

#: The one variable name. Spelled once so a producer cannot re-spell it.
TOKEN_VAR = "CLAWGATE_HOOK_TOKEN"

#: The file tier of the precedence chain — `clawgatectl`'s `defaultEnvFile`.
#: Kept `~`-relative and expanded at CALL time, so a test can move `$HOME` and a
#: deployed copy resolves against whatever home it actually runs under.
DEFAULT_ENV_PATH = os.path.join("~", ".claude", "clawgate.env")


def _unquote(value: str) -> str:
    """Strip ONE layer of matching single or double quotes — `config.go` `unquote`."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(path=None) -> dict:
    """`KEY=VALUE` file -> dict. A missing or unreadable file is `{}`, NOT an error.

    Matches `config.go` `parseEnvFile`: blank lines and `#` comments skipped, an
    optional `export ` prefix stripped, the value split on the FIRST `=` (so a
    token containing `=` survives), one layer of quotes removed, a line with no
    `=` or an empty key ignored.

    Never raises. A producer's whole contract is that it degrades to "no card"
    rather than taking the record down with it, so a chmod-000 file or a
    directory in the way must not become a traceback four frames up.
    """
    out: dict = {}
    try:
        with open(os.path.expanduser(path or DEFAULT_ENV_PATH),
                  encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, sep, value = line.partition("=")
                key = key.strip()
                if not sep or not key:
                    continue
                out[key] = _unquote(value.strip())
    except OSError:
        return {}
    return out


def resolve_token(*, env_path=None, environ=None):
    """The token, or None. `clawgatectl`'s precedence, file tier first.

    🔴 ORDER IS LOAD-BEARING AND IS THE OPPOSITE OF THE OBVIOUS ONE. The file is
    read FIRST and the environment overrides it, so an operator who exports a
    different token for one command gets that token, while the ordinary case —
    nothing exported, the host provisioned via `~/.claude/clawgate.env` — still
    resolves. Reading the environment first and only falling back to the file
    would give the same answer in every case EXCEPT the one where both are set
    and disagree, which is exactly the case an override exists for.

    An EMPTY value at either tier is treated as absent (`config.go`'s `set`
    helper only assigns non-empty), so `export CLAWGATE_HOOK_TOKEN=` does not
    blank out a perfectly good file value.
    """
    env = os.environ if environ is None else environ
    token = parse_env_file(env_path).get(TOKEN_VAR) or ""
    from_environ = env.get(TOKEN_VAR) or ""
    if from_environ:
        token = from_environ
    return token or None


def resolve_hook_token(what: str, *, env_path=None, environ=None, stream=None):
    """The token, or None having written ONE line to stderr naming the skip.

    `what` is the thing that will NOT be posted, in the producer's own words
    ("the clawgate card for signal draft #17"). The line names it and names both
    places that were searched, because "nothing happened" is the observable the
    two mechanisms — no token provisioned vs. a producer that cannot see one —
    SHARE, and the silent version left an operator no way to tell them apart.

    🔴 NOT FATAL. Returning None is the contract: the caller has already stored
    the durable record, and a notifier outage must never take that down. And
    🔴 NOT SILENT: silence is the defect being fixed here.

    🔴 NO SECRET IS PRINTED — the variable NAME and the PATH only. If you are
    tempted to add "(found: …)" for debugging, don't: this stream is captured
    into run logs.
    """
    token = resolve_token(env_path=env_path, environ=environ)
    if token:
        return token
    print("clawgate: no %s in %s or the process environment — SKIPPED %s "
          "(the record itself was stored)."
          % (TOKEN_VAR, env_path or DEFAULT_ENV_PATH, what),
          file=sys.stderr if stream is None else stream)
    return None
