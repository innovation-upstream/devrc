"""Is a `~/.cache/bar-status/<src>.json` payload a CURRENT measurement?

🔴 THE DEFECT THIS MODULE EXISTS TO CLOSE, measured on the shipped bar. Every
i3status-rust block in this bar renders a small JSON cache that
`scripts/bar-status-poll` rewrites every ~45s. Not one of them read the `ts` the
poller stamps on every payload — so when the poller STOPPED, each block went on
rendering its last reading forever, as a confident, present-tense pill. A count
pill pinned to "clean" by a dead measuring apparatus is not a calm bar; it is a
bar that lies in exactly the situation its numbers are worth reading.

`scripts/i3status-clawgate` grew a private fix for this (PR #490). This module is
that fix, extracted: ONE definition of "too old", ONE no-coercion integer
reading, ONE age computation, imported by every block instead of respelled in
seven. A predicate open-coded at N sites is typically wrong at N-1 of them in the
same direction (RULES.md), and seven hand-copied freshness gates is that shape
with the ink still wet.

🔴 HOW BLOCKS GET AT IT. The block scripts are EXTENSIONLESS (`i3status-mail`,
...), so nothing can `import` them and they cannot be a package. Each loads this
file as a CO-LOCATED SIBLING by explicit path — the same shape `notif-center`
uses for `i3status-notifs` and `bar-status-poll` uses for `scripts/lib/`. That
makes `nix/graphical.nix` responsible for symlinking this file NEXT TO every
block that loads it; `scripts/tests/test_bar_status.py` pins that two-way, since
a missing symlink turns every count pill into `?` on a healthy machine.

🔴 WHAT THIS MODULE IS *NOT*. It is not "the poller's grace period". The poller's
`TELEMETRY_UNKNOWN_GRACE` (1800s) and `MAX_CACHE_AGE_SECS` (600s) look like the
same "too old" number and are not — see the constant's own comment below.
"""

#: The i3status-rust states that mean "look at me". `Idle`/`Good`/`Info` are the
#: calm ones; a pill in any of these is already asking for attention.
LOUD_STATES = ("Warning", "Critical")

#: The bar-wide grammar for "this reading is not a current measurement",
#: appended to whatever the reading still says (`39?`, `!2?`, `tlm 3?`, `LEAK?`).
UNMEASURED_MARK = "?"

MAX_CACHE_AGE_SECS = 600
#: How old a cache may be and still count as a CURRENT measurement.
#:
#: Derived from the systemd unit, not from taste (nix/graphical.nix). THREE
#: terms, and the middle one is the one the derivation originally dropped:
#:   * `OnUnitActiveSec = 45s`  — the timer re-arms this long after each run;
#:   * `AccuracyUSec = 1min`    — systemd's DEFAULT, which the timer does NOT
#:                                override, so an elapse may be deferred a
#:                                further 60s to coalesce with other timers;
#:   * `TimeoutStartSec = 90`   — the hard ceiling on one run of the service.
#: 45 + 60 + 90 = 195s is therefore the widest gap a HEALTHY poller can leave
#: between two writes. 600s is ~3x that — out of reach of scheduler jitter, a
#: slow port-forward or one bounded timeout, and still short enough that a dead
#: poller is announced within the same working session rather than the next day.
#:
#: 🔴 IT IS A DIFFERENT QUANTITY FROM `bar-status-poll.TELEMETRY_UNKNOWN_GRACE`
#: (1800s), and they must not be merged. They answer different questions about
#: different subjects, and the numbers point in opposite directions:
#:
#:   * THIS one asks "is the WRITER alive?" — nobody has rewritten this file, so
#:     no reading at all is being taken. Its subject is the poller. It is
#:     derived from the timer, and any value below ~195s would fire on healthy
#:     jitter.
#:   * The GRACE asks "how long has a LIVING writer been saying 'I cannot
#:     tell'?" — the poller is demonstrably alive and stamping fresh payloads
#:     every 45s; the deadman's own ClickHouse query is what keeps failing. Its
#:     subject is the backend, and its job is to debounce a ClickHouse restart
#:     or a nebula blip so the bar does not flicker. Any value near 600s would
#:     make a routine restart flap the pill.
#:
#: Collapsing them to one number breaks whichever question loses: at 600s a
#: ten-minute ClickHouse restart flickers `tlm ?`; at 1800s a dead poller pins
#: every pill to its last reading for half an hour. Pinned as distinct — and as
#: an ORDERING, not two literals — by
#: `test_the_two_TOO_OLD_constants_measure_DIFFERENT_THINGS`.


def int_or_none(value):
    """A strictly-integral reading, or None.

    🔴 NO COERCION. `int()` would turn `2.5` into `2` and `True` into `1`, both
    of which render as a confident measurement of something nobody measured.
    These caches are JSON written by our own poller, where these fields are
    always ints, so anything else means the writer changed — and the honest
    reading of a writer we no longer recognise is "cannot tell", never a number.

    `bar-status-poll._strict_int` is the same predicate on the other side of an
    extensionless-script boundary it cannot import across;
    `test_the_two_NO_COERCION_predicates_AGREE` pins the pair.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def cache_age_secs(payload, now):
    """Seconds since the poller wrote this payload, or None if it cannot say.

    `ts` is stamped by `bar-status-poll.source()` onto EVERY payload it writes,
    the `stale` markers included, so an absent or non-integral `ts` means the
    file did not come from a poller we recognise — a reason to distrust the
    reading, never a reason to assume it is fresh. Clamped at 0 so clock skew
    reads as "just now" rather than as a negative age (matching
    `clawgate_tasks.agent_idle_secs`).
    """
    ts = int_or_none((payload or {}).get("ts") if isinstance(payload, dict)
                     else None)
    if ts is None:
        return None
    return max(0.0, float(now) - ts)


def is_current(payload, now, max_age: int = MAX_CACHE_AGE_SECS) -> bool:
    """Is this cache a CURRENT measurement?

    An unreadable age is NOT current — "I cannot tell how old this is" must not
    render as "this is fresh", the same substitution `int_or_none` refuses.
    Strictly `<=`, so `max_age` is the last age still considered current rather
    than the first that is not.
    """
    age = cache_age_secs(payload, now)
    return age is not None and age <= max_age


def is_marker(payload) -> bool:
    """Did the POLLER itself say it could not read this source?

    `bar-status-poll.stale()` writes `{"state": "stale", ...}`, and any payload
    carrying `error` came from a fetch that raised. Both mean the poller ran and
    learned nothing — distinct from the file being old, but the same conclusion
    for a renderer.
    """
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("error")) or payload.get("state") == "stale"


def unmeasured(payload, now, max_age: int = MAX_CACHE_AGE_SECS) -> bool:
    """🔴 THE ONE PREDICATE. True when this payload is not a reading of NOW.

    Four shapes, one answer, because a renderer's choice is binary and every one
    of these is "I do not know":
      * not a dict at all      — the file is missing, or `load()` could not parse
                                 it (`load` returns None), or something wrote a
                                 bare JSON scalar/list there;
      * a `stale`/`error` marker — the poller ran and the source did not answer;
      * no or non-integral `ts` — not written by a poller we recognise;
      * `ts` older than max_age  — nobody is rewriting this file.

    A caller that needs to tell them apart (to keep an alarm the payload still
    records, say) reads the payload itself; this answers only "may I present
    this as current?".
    """
    if not isinstance(payload, dict):
        return True
    if is_marker(payload):
        return True
    return not is_current(payload, now, max_age)


def is_loud(pill) -> bool:
    """Is this rendered pill already asking for attention (Warning/Critical)?

    For the STATE-driven blocks (`media`, `airvpn`) this is the whole test of
    "did the cache record an alarm": they have no count, so the pill's colour is
    the reading. The COUNT blocks use a different test — any count above zero is
    a recorded number worth carrying, even one a `--red-above` backlog baseline
    renders neutral — see `carry_forward`.
    """
    return isinstance(pill, dict) and pill.get("state") in LOUD_STATES


def carry_forward(recorded, fallback):
    """🔴 A MEASUREMENT OUTAGE MUST NOT MAKE A KNOWN ALARM QUIETER.

    THE ONE DEFINITION of *how* an unmeasured cache renders a reading it still
    holds, so the seven blocks cannot disagree about the grammar. `recorded` is
    the pill the payload's OWN last reading renders (None when it recorded
    nothing worth carrying); `fallback` is the block's bare "cannot tell" pill.

    A carried pill keeps its text and its colour and gains the trailing
    `UNMEASURED_MARK`: `39` -> `39?`, `LEAK` -> `LEAK?`, `tlm 3` -> `tlm 3?`.
    Alerts do not resolve because the poller died, the `?` marks the number as
    not-currently-measured, and the cost is asymmetric — a false-quiet on a leak
    is far worse than a false-loud. State is floored at `Warning`: an unreadable
    cache is never Idle, so a reading that was neutral-but-visible (a count
    inside its `--red-above` backlog) comes back as Warning rather than as the
    calm colour it had when someone was still measuring it.

    🔴 WHAT is worth carrying is the CALLER'S judgement, not this function's —
    it differs per block by design and the blocks state their own reason:
      * count blocks pass their measured pill whenever the count is > 0;
      * `media`/`airvpn` pass it only when `is_loud` (their neutral readings are
        not alarms, and `airvpn`'s own `CC?` already spends the `?` on a
        different meaning);
      * `i3status-clawgate` does not use this at all: its recorded `count` is
        the EXPECTED steady state rather than an alarm, so it carries only the
        stuck half (`!2?`), by hand, in `_unmeasured`.
    A `stale()`/`error` marker records `count: 0` over the last reading, so on
    that path there is usually nothing to carry and the fallback is honest;
    `bar-status-poll.carry_stuck_forward` is the one writer-side exception.
    """
    if not isinstance(recorded, dict):
        return dict(fallback)
    text = recorded.get("text")
    if not isinstance(text, str) or not text:
        return dict(fallback)          # an invisible reading is not an alarm
    short = recorded.get("short_text")
    if not isinstance(short, str) or not short:
        short = text
    state = recorded.get("state")
    if state not in LOUD_STATES:
        state = "Warning"
    out = dict(recorded)               # keeps `icon`, which media/airvpn need
    out.update({"text": text + UNMEASURED_MARK,
                "short_text": short + UNMEASURED_MARK,
                "state": state})
    return out
