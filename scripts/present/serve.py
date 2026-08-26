#!/usr/bin/env python3
"""Serve the generated explainer page ON THE WORKBENCH. STATIC, and loud about
its own age.

    scripts/present/serve.py [--host H] [--port N] [--dir D] [--stale-after SEC]

🔴 WHO CAN ACTUALLY READ THIS: the workbench, and nothing else. The socket is
bound to the workbench's own LAN address (192.168.50.250, eth0), but **8900 is
not in `/etc/nixos/configuration.nix`'s `networking.firewall.allowedTCPPorts`**,
so every off-host SYN is dropped. A same-host `curl http://192.168.50.250:8900/`
succeeds — it takes the `lo` path, which the firewall accepts unconditionally —
and that success says NOTHING about a second machine. Measured from the laptop
2026-08-25: 22 OPEN, 443 OPEN, 8899 CLOSED, 8900 CLOSED. 8899 is
`initiatives-viewer`, listening on the identical address with the identical
gap — which is why copying its shape did not warn anyone.

That is the CURRENT decision, not an oversight to be quietly fixed: the reader
who is not on the workbench is served by the SANITIZED PORTABLE EXPORT that the
same regen run produces. Opening 8900 is a system-level change (`/etc/nixos`,
`sudo nixos-rebuild`) *plus* a decision to publish client scope names to anyone
on the LAN. Do it when someone who is not on the workbench actually needs to
read this page, and not before.

⚠ The export is the thing that argument leans on, so check it rather than
assume it: `generate.py --sanitize` prints a `SANITIZE DEGRADED` line per value
it could NOT substitute, and the page's own mode chip carries the same counts.
A 2026-08-26 build left two client-ish names in the sanitized copy (one
hostname "indistinguishable from a word", one scope matched in its exact form
only). That is `scripts/present/sanitize.py`'s business, not this server's, but
it is the reason "the portable export serves the off-workbench reader" is a
claim to verify per build and not a standing fact.

WHAT THIS IS NOT. It runs no subprocess, holds no credential, has no refresh
button and no form. It answers exactly two artefact routes out of an explicit
table and 404s everything else — there is no directory handler here, so there is
no path-traversal surface to reason about. The regeneration lives in a separate
oneshot unit (`present-regen`), which is the whole reason this one can be this
small.

🔴 THE ONE OUTCOME THIS FILE EXISTS TO PREVENT: serving a stale page as if it
were current.

`scripts/present/generate.py` exits 3 and writes NO FILE when every fact came
back UNMEASURED. That is correct — it refuses to publish a broken build. But it
means a failed regeneration leaves the PREVIOUS page in place, and a page that
was true last Tuesday, served today with no marking, is exactly the silent-zero
shape the page itself exists to teach against. A reader cannot tell it from a
fresh one: it is a careful document full of measured numbers, and every one of
them is wrong about now.

THE CHOICE MADE HERE: **serve-last-good, with the age made obvious** — not
serve-an-error.

  * Fresh (age <= --stale-after): the bytes go out verbatim. The page already
    carries its own `built <timestamp>` chip, which is the honest statement for
    a page that IS current.
  * Stale: the same bytes go out with a fixed, full-width, high-contrast banner
    injected at the top of the document, naming the measured age in plain words
    and saying which unit failed to refresh it.
  * Clock-suspect (the artefact was written AFTER `now`): the age cannot be
    measured at all, so it is not reported as one. Same banner chrome, different
    words. See `CLOCK_SUSPECT_AFTER` for why this is a THIRD state and not a
    clamp to zero.
  * Absent: 503 and a self-contained interstitial. There is no last-good to
    serve, and inventing one is not on the menu.

Why not serve-an-error on stale: the content is still TRUE AS OF ITS STAMP and
is often exactly what the reader needs (that is the state the operator is
debugging). What is unacceptable is silence, and the banner removes the silence
without removing the page. The failure is ALSO loud on the operator's side --
`present-regen.service` carries `OnFailure = notify-failure@%n.service`, so a
failed regeneration toasts. The banner is the READER's copy of that signal; the
toast is the operator's. Neither alone covers both audiences.

🔴 AND THE PART THAT MAKES THAT A CONTRACT RATHER THAN AN INTENTION: if the
banner cannot be injected — an artefact whose shape this file does not
recognise, so there is no `<body>` to inject after — the bytes are NOT served.
The response is the interstitial instead. There is deliberately no code path in
this file that emits artefact bytes in a NOT-FRESH state without a banner; that
is the property `scripts/tests/test_present_serve.py` pins, and it is the
property a naive `SimpleHTTPRequestHandler` would not have. Both non-fresh
states go through one helper (`_serve_warned`) precisely so that adding a third
cannot re-open the hole by forgetting the refusal.

EXIT CODES
  0  clean shutdown (SIGINT/SIGTERM)
  2  usage error, or the bind address/port could not be claimed
"""
from __future__ import annotations

import argparse
import html
import os
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: The workbench's OWN LAN address (eth0 — confirmed with `ip -4 -o addr`; an
#: earlier draft of this file, of nix/home.nix and of the unit tests all said
#: eth1, and there is no eth1 on this host).
#:
#: 🔴 NOT 192.168.50.94. That is a homelab node hosting the kube-apiserver and
#: the NodePorts; it is not assignable here and binding it crash-loops the unit.
#: It already bit `initiatives-viewer` once — see the comment block above that
#: unit in nix/home.nix.
#:
#: 🔴 Binding a LAN address is not the same as being REACHABLE on it — the
#: firewall drops 8900 for every non-loopback peer. See the module docstring.
DEFAULT_HOST = "192.168.50.250"

#: 8900. Measured free on the workbench 2026-08-25: the claimed neighbours are
#: 8787 (activity receiver), 8788 (browser bridge), 8791 (dl-router), 8793,
#: 8899 (initiatives viewer) and 8931. `scripts/tests/test_present_units.py`
#: keeps this from colliding with any other port DECLARED in nix/ — a live
#: `ss` reading is a fact about one moment, and the declared set is the thing
#: two units can actually fight over across a reboot.
DEFAULT_PORT = 8900

#: Default staleness threshold, in seconds. 30 hours.
#:
#: The cadence is DAILY (`present-regen.timer`, OnCalendar `*-*-* 05:00:00`,
#: RandomizedDelaySec 600). So two consecutive healthy runs land at most
#: 24h + 600s apart. 30h leaves ~5h of slack for a slow build or a host that was
#: down over the boundary, and still means the banner appears BEFORE a second
#: scheduled run has been missed. A 48h threshold (2x cadence) was the obvious
#: alternative and was rejected: it lets a full extra day pass in silence, which
#: is the failure being guarded against, only slower.
DEFAULT_STALE_AFTER = 30 * 3600

#: 🔴 How far into the FUTURE an artefact's mtime may sit before this server
#: stops calling it an age at all. Seconds.
#:
#: The age used to be `max(0.0, now - mtime)` and nothing else, justified by a
#: `touch -d '3 days 4 hours ago'` mistake made while verifying live (GNU date
#: binds `ago` to the last term only, so that sets a mtime three days in the
#: FUTURE). Clamping made the header report 0 instead of a negative nonsense
#: number, which is right — but it also made the STATE `fresh`, and that is the
#: case this file exists to prevent, arriving by a door nobody was watching:
#:
#:   a host with a bad RTC boots, NTP corrects the clock BACKWARDS, and a page
#:   that is genuinely eight days old now has a mtime after `now`. Age 0,
#:   `X-Present-Stale: 0`, no banner. The single reader-facing staleness signal
#:   disarms itself, silently, in exactly the situation where the machine's own
#:   sense of time is the thing that is broken.
#:
#: So "written after now" is its own state. It is not stale — nothing here knows
#: that — it is UNMEASURABLE, which is strictly worse than a measured age and is
#: reported as such rather than rounded down to the reassuring end.
#:
#: 120s and not 0s: a page written seconds before the request, read across a
#: sub-second NTP slew or a filesystem timestamp rounding, must not raise an
#: alarm about the clock. Anything past two minutes is a real disagreement
#: between the writer's clock and the reader's, and both live on this host.
CLOCK_SUSPECT_AFTER = 120.0

#: The two artefacts, by route. An explicit table, not a directory root: every
#: path that is not a key here is a 404, so `..` is not a case that needs
#: handling — it is simply not in the table.
ROUTES = {
    "/": "present.html",
    "/present.html": "present.html",
    "/sanitized": "present-sanitized.html",
    "/present-sanitized.html": "present-sanitized.html",
}

#: The injection point. `render.build_html` emits `</head><body>` as one literal
#: with no attributes, so this is an exact match rather than a parse. If that
#: ever changes, injection FAILS and the interstitial is served — a loud,
#: correct degradation rather than a silently unbannered stale page.
_BODY_OPEN = "<body>"


def humanise(seconds: float) -> str:
    """`3 days 4 hours` / `4 hours 12 minutes` / `12 minutes`. Never `0`."""
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes and not days:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    return " ".join(parts) or "under a minute"


def _shell(title: str, tone: str, body_html: str) -> str:
    """A self-contained interstitial. Inline CSS only, no external reference —
    the artefact it stands in for is checked for self-containment by
    `generate.self_contained`, and the substitute must clear the same bar."""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<title>{html.escape(title)}</title></head>"
        "<body style=\"margin:0;background:#14100e;color:#ede0d4;"
        "font:16px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace\">"
        "<div style=\"max-width:44rem;margin:0 auto;padding:3rem 1.5rem\">"
        f"<div style=\"border-left:6px solid {tone};padding:0 0 0 1.25rem\">"
        f"<h1 style=\"margin:0 0 1rem;font-size:1.5rem;color:{tone}\">"
        f"{html.escape(title)}</h1>{body_html}</div></div></body></html>"
    )


def _banner(headline_html: str, detail_html: str) -> str:
    """The banner chrome. ONE definition, both non-fresh states.

    Fixed, full-bleed, and at the top of the stacking context on purpose: this
    has to survive the page's own CSS, which lays out a fixed sidebar and a
    scrolling main column. A banner the page can scroll away from, or paint
    over, is a banner that is *present* rather than *obvious* — and present is
    the state that already fails, because the page has carried a build stamp
    from the first commit and nobody reads it.

    It is one function rather than two copies because a second state was added
    to this file after the first shipped, and a copied banner is how the two
    drift until only one of them is actually unmissable.
    """
    return (
        "<div style=\"position:fixed!important;top:0!important;left:0!important;"
        "right:0!important;z-index:2147483647!important;background:#8b1a1a!important;"
        "color:#fff!important;padding:.9rem 1.25rem!important;"
        "font:600 15px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace!important;"
        "box-shadow:0 2px 18px rgba(0,0,0,.6)!important;text-align:left!important\">"
        f"{headline_html}"
        "<div style=\"font-weight:400!important;opacity:.92!important;"
        "margin-top:.35rem!important;font-size:13px!important\">"
        f"{detail_html}"
        "</div></div>"
        # Spacer: the banner is fixed, so without this it covers the masthead —
        # i.e. it would hide the build stamp it is warning about.
        "<div style=\"height:5.5rem\"></div>"
    )


def stale_banner(age_seconds: float, artefact: str) -> str:
    """The banner injected into a stale page — a MEASURED age, in words."""
    age = html.escape(humanise(age_seconds))
    art = html.escape(artefact)
    return _banner(
        f"&#9888; STALE &mdash; this page was last regenerated {age} ago. "
        "The numbers below describe the machine AS IT WAS THEN, not as it is now.",
        f"Artefact <code>{art}</code>. The daily "
        "<code>present-regen.service</code> has not written a new page since "
        "then &mdash; it exits 3 and writes nothing when every fact comes back "
        "UNMEASURED, so the copy you are reading is the last GOOD build, not a "
        "current one. Re-run it with "
        "<code>systemctl --user start present-regen.service</code> and read "
        "<code>journalctl --user -u present-regen.service</code>.")


def clock_suspect_banner(skew_seconds: float, artefact: str) -> str:
    """The banner injected when the artefact was written AFTER `now`.

    🔴 Deliberately does NOT say "fresh" and does not name an age. The whole
    point of this state is that the age is not a number anyone should read: the
    page may have been built ten seconds ago or ten days ago, and the machine's
    own clock is what cannot be trusted to tell you which.
    """
    skew = html.escape(humanise(skew_seconds))
    art = html.escape(artefact)
    return _banner(
        "&#9888; AGE UNKNOWN &mdash; this page's timestamp is in the FUTURE, so "
        "how old it is cannot be measured. Do not read the numbers below as "
        "current.",
        f"Artefact <code>{art}</code> claims to have been written {skew} from "
        "now. The usual cause is a clock correction: a host with a bad RTC boots, "
        "NTP steps the clock BACKWARDS, and every file written before the step "
        "is now dated in the future. A genuinely week-old page looks brand new "
        "under that arithmetic, which is why this server refuses to call it "
        "fresh. Re-run <code>systemctl --user start present-regen.service</code> "
        "&mdash; a successful rebuild re-stamps the artefact against the "
        "corrected clock and this banner goes away. If it does not, the clock is "
        "still wrong: check <code>timedatectl</code>.")


def inject_banner(page: str, banner_html: str) -> str | None:
    """Return `page` with `banner_html` injected, or None if it cannot be.

    None is a REFUSAL, not a fallback. The caller must not serve the page.
    """
    i = page.find(_BODY_OPEN)
    if i < 0:
        return None
    at = i + len(_BODY_OPEN)
    return page[:at] + banner_html + page[at:]


def _missing_page(artefact: str) -> str:
    return _shell(
        "No page has been generated yet", "#e0a458",
        f"<p>There is no <code>{html.escape(artefact)}</code> to serve.</p>"
        "<p>Nothing stale is being shown in its place, and nothing has been "
        "invented. This is what the server looks like before the first "
        "successful <code>present-regen.service</code> run, or after the "
        "artefact was removed.</p>"
        "<p>Generate one: <code>systemctl --user start "
        "present-regen.service</code>, then "
        "<code>journalctl --user -u present-regen.service</code>.</p>")


def _unbannerable_page(artefact: str, why_html: str) -> str:
    """The refusal interstitial, shared by every non-fresh state.

    `why_html` is the one sentence that differs — what was wrong with the page.
    The REST of the argument is identical in every case and is written once, so
    a third state cannot ship with a weaker explanation than the first two.
    """
    return _shell(
        "A page was withheld", "#8b1a1a",
        f"<p>{why_html}</p>"
        "<p>This server could not inject its warning banner &mdash; the document "
        "does not carry the <code>&lt;body&gt;</code> opening tag the injector "
        "matches on.</p>"
        "<p>So it was NOT served. A page that is not current, without its "
        "banner, is indistinguishable from a current one &mdash; which is the "
        "single outcome this server exists to prevent; withholding it is the "
        "honest degradation.</p>"
        "<p>Either regenerate it (<code>systemctl --user start "
        "present-regen.service</code>) or fix the injector in "
        "<code>scripts/present/serve.py</code> if the renderer's output shape "
        "changed.</p>")


def _serve_warned(page: str, hdr: dict, artefact: str, state: str,
                  banner_html: str, why_html: str):
    """Serve `page` with `banner_html`, or withhold it. THE ONLY exit for a
    non-fresh state.

    🔴 One helper and not one branch per state, because the hole this file
    exists to close is a state that emits artefact bytes without a banner, and
    the way that hole re-opens is a third state added later whose author copies
    the happy path and forgets the refusal. Here there is no happy path to copy:
    `inject_banner` returning None is the only way out that carries the page's
    bytes, and it does not.
    """
    bannered = inject_banner(page, banner_html)
    if bannered is None:
        hdr["X-Present-State"] = f"{state}-withheld"
        return (503, "text/html; charset=utf-8", hdr,
                _unbannerable_page(artefact, why_html).encode("utf-8"))
    hdr["X-Present-State"] = f"{state}-bannered"
    return (200, "text/html; charset=utf-8", hdr, bannered.encode("utf-8"))


def build_response(directory: Path, path: str, *, stale_after: float,
                   now: float | None = None):
    """Resolve one request. Pure: no socket, no clock unless you let it.

    Returns `(status, content_type, extra_headers, body_bytes)`.

    Every branch that can emit ARTEFACT bytes is here, and there are exactly
    two of them: fresh-verbatim, and non-fresh-with-banner via `_serve_warned`.
    That is the property worth reading this function for.
    """
    now = time.time() if now is None else now
    name = ROUTES.get(path)
    if name is None:
        return (404, "text/plain; charset=utf-8", {},
                b"404 - this server answers only /, /present.html and /sanitized\n")

    target = directory / name
    try:
        mtime = target.stat().st_mtime
        page = target.read_text(encoding="utf-8")
    except OSError:
        return (503, "text/html; charset=utf-8",
                {"X-Present-Artefact": name, "X-Present-State": "absent"},
                _missing_page(name).encode("utf-8"))

    # SIGNED. `age` below is the clamped reading; `delta` is what actually
    # happened, and the clock-suspect guard is the only thing that can see it.
    delta = now - mtime
    age = max(0.0, delta)

    # 🔴 ONE PREDICATE, ONE PLACE. The header and the body must never disagree
    # about whether this page is stale, and the first cut of this function
    # spelled `age <= stale_after` TWICE — once for the header, once for the
    # branch — with a comment claiming they were one comparison. They were not:
    # a mutation flipping the branch to `<` left the header saying "fresh" while
    # the body carried a staleness banner, and only the sweep caught it. The
    # comment was a claim and the claim was false. Now it is a fact.
    stale = age > stale_after
    clock_suspect = delta < -CLOCK_SUSPECT_AFTER

    hdr = {
        "X-Present-Artefact": name,
        # Never negative. The clamp survives as a HEADER-FORMAT guarantee — an
        # int consumer must not have to parse a minus sign — and no longer as a
        # classification: see `clock_suspect` below.
        "X-Present-Age-Seconds": str(int(age)),
        # Machine-readable twin of the banner. BOTH warned states set it: a
        # clock-suspect page has no measurable age, which is strictly worse than
        # a measured stale one, and reporting `0` there is the silent disarm
        # this whole file exists to prevent.
        "X-Present-Stale": "1" if (stale or clock_suspect) else "0",
    }

    # 🔴 ORDER IS LOAD-BEARING, AND THIS GUARD MUST BE FIRST.
    # `age` is CLAMPED, so a mtime a week in the future arrives at the
    # staleness comparison as 0 and classifies as FRESH. Ask `stale` first and
    # this branch becomes unreachable for every value it exists to catch —
    # which is precisely the state the code was in before this was added.
    if clock_suspect:
        skew = -delta
        hdr["X-Present-Clock-Skew-Seconds"] = str(int(skew))
        return _serve_warned(
            page, hdr, name, "clock-suspect",
            clock_suspect_banner(skew, name),
            f"<code>{html.escape(name)}</code> is dated "
            f"{html.escape(humanise(skew))} in the FUTURE, so its age cannot be "
            "measured and it must not be presented as current.")

    if not stale:
        hdr["X-Present-State"] = "fresh"
        return (200, "text/html; charset=utf-8", hdr, page.encode("utf-8"))

    return _serve_warned(
        page, hdr, name, "stale", stale_banner(age, name),
        f"<code>{html.escape(name)}</code> is "
        f"{html.escape(humanise(age))} old.")


class _Handler(BaseHTTPRequestHandler):
    server_version = "present-serve"
    sys_version = ""
    directory: Path = Path(".")
    stale_after: float = DEFAULT_STALE_AFTER

    def _respond(self, *, with_body: bool):
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        status, ctype, extra, body = build_response(
            self.directory, path, stale_after=self.stale_after)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page is regenerated daily and the whole point is that a reader
        # sees its real age; a cached copy would defeat the banner.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._respond(with_body=True)

    def do_HEAD(self):  # noqa: N802
        self._respond(with_body=False)

    def log_message(self, fmt, *args):
        sys.stderr.write("present-serve: %s - %s\n" % (self.address_string(), fmt % args))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="present-serve",
        description="Serve the generated devrc explainer page (static; bound to "
                    "the workbench's own LAN address, reachable only FROM the "
                    "workbench — 8900 is not in allowedTCPPorts).")
    ap.add_argument("--host", default=os.environ.get("PRESENT_SERVE_HOST", DEFAULT_HOST))
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("PRESENT_SERVE_PORT", DEFAULT_PORT)))
    ap.add_argument("--dir", default=os.environ.get(
        "PRESENT_ARTEFACT_DIR", os.path.expanduser("~/.local/share/present")))
    ap.add_argument("--stale-after", type=float, default=float(
        os.environ.get("PRESENT_STALE_AFTER_SEC", DEFAULT_STALE_AFTER)))
    args = ap.parse_args(argv)

    _Handler.directory = Path(args.dir)
    _Handler.stale_after = args.stale_after

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), _Handler)
    except OSError as exc:
        print(f"present-serve: could not bind {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        print("present-serve:   the host must be an address THIS machine holds "
              "(the workbench's own eth0 LAN IP), never a homelab node.",
              file=sys.stderr)
        return 2

    print(f"present-serve: serving {args.dir} on http://{args.host}:{args.port}/ "
          f"(stale after {humanise(args.stale_after)})", file=sys.stderr)

    def _stop(_sig, _frm):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
