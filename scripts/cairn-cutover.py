#!/usr/bin/env python3
"""Cairn phase 1, criterion 9 — the write-through cutover. DRY-RUN BY DEFAULT.

WHAT THIS DOES, IN ORDER, AND WHY THE ORDER IS THAT WAY
-------------------------------------------------------
The hosted store becomes the authority and local disk becomes a read-through
cache. Three things have to be true before that is safe, and they have to become
true in this sequence:

  1. every byte that exists ONLY on a host must be on the pod first, or freezing
     local disk strands it;
  2. every byte that exists ONLY on the pod must survive the upload, or the push
     destroys it — and `seed.sh`'s push is `rsync -a --delete` SOURCE->STAGE then
     `tar` STAGE->pod, which OVERWRITES a shared entry with the source's copy;
  3. no ref may resolve to two entries in the merged union, because the write
     route answers an ambiguous ref with 400 and the entry becomes unwritable.

🔴 THIS SCRIPT NEVER GUESSES A MERGE. Where two copies of one entry disagree and
neither is a derivative of the other, it REFUSES and names the file. A silent
last-write-wins is the failure `claudedocs/plan-cairn-integration.md` phase 1
calls "the risk here, not the transport", and the loss would be invisible.

🔴 IT DOES NOT WRITE A SECOND PUSH PATH. The upload is `seed.sh`, unchanged,
pointed at a *curated delta tree* this script builds. That is the whole trick:
`seed.sh` is destructive because of WHAT IT IS GIVEN, not because of what it
does — its tar adds and overwrites but never deletes, so a source holding only
additive entries is a safe push through the same code that is unsafe with a
whole store behind it. One push path, one set of guards, one verdict format.

🔴 AND IT DOES NOT WRITE A SECOND BYTE-IDENTITY CHECKER. Verification is
`verify-byte-identity.sh`, unchanged.

WHY 9 BEFORE 8 (the ordering argument, stated where it is executed)
-------------------------------------------------------------------
The ranked list in `claudedocs/handoff-cairn-phase3.md` puts the laptop re-seed
(criterion 8) at rank 3 and this cutover (criterion 9) at rank 4. Running them
in rank order is wrong. `seed.sh` from a host replaces every shared entry on the
pod with that host's copy, so running it while API-appended bullets live only in
the served copy DESTROYS them — recoverable from the backup, which is a different
claim from safe. After this cutover the hosts' stores are caches of the pod, so
the same push has nothing unique left to destroy. Landing 9 first turns a
destructive operation into an idempotent one instead of relying on a restore.

PHASES AND THEIR ROLLBACKS
--------------------------
  P0  preconditions            — creates a 0700 run dir holding a full copy of the
                                 store, and sends ONE non-mutating POST (see
                                 `write_route_deployed`). Nothing on either store
                                 is modified. Roll back by deleting the run dir.
  P1  plan the delta           — read-only; writes only into the run directory
  P2  ref-collision check      — read-only
  P3  push the delta           — rollback: `--rollback-push <run-dir>` re-PUTs the
                                 pre-push bytes this script saved for every entry
                                 it was about to overwrite
  P4  acceptance + byte check  — read-only
  P5  freeze local disk        — records every mode, then chmods. Rollback:
                                 `--unfreeze`, which RESTORES the recorded modes
                                 and refuses without the ledger.

⚠ THE BACKUP PRECONDITION GATES THE CUTOVER, NOT EVERY MODE. It lives in the P0
block, which `--freeze`, `--unfreeze`, `--rollback-push` and `--manifest` skip.
Right for the last three; a real gap for `--freeze --apply`, which chmods the
whole store on its own. It is reversible and single-purpose, but do not read the
precondition as covering it.
The skill/protocol half of the cutover (routing `subsystem-index` writes through
`cairn append`) is a DOCUMENT change and is rolled back by reverting its commit;
this script neither applies nor reverts it, and says so rather than implying it
covered the whole criterion.

USAGE
-----
    cairn-cutover.py                       # dry run: plan and report, change nothing
    cairn-cutover.py --apply --push <ns>/<deploy>
    cairn-cutover.py --unfreeze            # roll P5 back
    cairn-cutover.py --rollback-push <run-dir>

Exit codes are disjoint and each names one refusal; see `RC_*` below.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE / "subsystem-store-api" / "seed.sh"
VERIFY = HERE / "subsystem-store-api" / "verify-byte-identity.sh"
CAIRN = HERE / "cairn"

DEFAULT_STORE = Path.home() / ".claude" / "analyze-service-index"
DEFAULT_MERGED = Path.home() / ".local" / "share" / "cairn-cutover" / "merged"
DEFAULT_RUN_ROOT = Path.home() / ".local" / "share" / "cairn-cutover" / "runs"
DEFAULT_BACKUP_NAMESPACE = "subsystem-store"
DEFAULT_BACKUP_CRONJOB = "subsystem-store-backup"
# The CronJob's schedule is daily at 03:45 UTC. 36 h is one full period plus a
# 12 h grace, so a single missed run refuses and an ordinary run never does.
# 🔴 NOT 24: at exactly 24 h a healthy daily job is refused for most of the day
# it succeeded in, which is a permanently-red gate and RULES.md says a gate
# everyone clicks through is worse than no gate.
DEFAULT_BACKUP_MAX_AGE_H = 36.0

# --- exit codes. Disjoint on purpose; each is one refusal with one remedy. ----
RC_OK = 0
RC_USAGE = 2
RC_ROOT = 9                 # running as root makes the EACCES evidence vacuous
RC_BACKUP = 10              # no recent SUCCESSFUL backup, or it could not be measured
RC_UNREACHABLE = 11         # the store could not be read
# 12 — THE 405 ARM AND NOTHING ELSE. Every other falsy result of the probe is
# RC_COULD_NOT_MEASURE. This used to answer all of them, so an unreachable pod
# during P0 was reported as "the running image is read-only" and sent the
# operator to redeploy the store over a network blip.
RC_NO_WRITE_ROUTE = 12      # the RUNNING image has no write path (405 read-only)
RC_NO_STORE = 13            # the local store is missing or holds no scopes
RC_UNRESOLVED_DIVERGENCE = 14   # two copies disagree and no merged file resolves it
RC_REF_COLLISION = 15       # a ref would resolve to two entries in the union
RC_FREEZE_INEFFECTIVE = 16  # the freeze was applied and a write STILL succeeded
# 17 — THE PUSH/VERIFY FAMILY. Deliberately one code with a stated scope rather
# than a code per site: it means "the entries this host holds are not, or cannot
# be shown to be, on the pod, and NOTHING WAS FROZEN". Three sites reach it —
# `seed.sh` exiting non-zero, `comm -23` printing lines, and a failed
# `--rollback-push` restore — and all three leave the operator in the same place
# with the same next step (read the verdict lines above, re-run). The ledger used
# to describe only the `comm -23` site, which is the defect: a code's comment
# must cover every site that returns it or the comment is the narrower claim.
RC_ACCEPTANCE = 17
RC_COULD_NOT_MEASURE = 18   # an instrument did not answer; never folded into a pass

# 🔴 THE DISCRIMINATOR THIS WHOLE MERGE RULE TURNS ON. `server.render_bullet`
# terminates every API-appended bullet with exactly this shape, and nothing else
# in the store produces it: a bullet carrying it was written THROUGH THE POD and
# therefore exists in the served copy and nowhere else. A divergence where the
# pod's extra bullets are all UNattributed is a stale snapshot of a file the host
# has since edited; a divergence where the pod holds an ATTRIBUTED bullet the
# host lacks is content with exactly one copy in the world.
#
# Kept in sync with `server.ATTRIBUTION_RE` by
# `test_cairn_cutover.py::test_the_attribution_pattern_matches_the_servers`,
# which imports the server's own pattern rather than restating it — a second
# spelling of one regex is the shape that drifts silently.
ATTRIBUTION = re.compile(
    r"\[cairn: [a-z0-9][a-z0-9-]{0,31}/[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\]"
)

# 🔴 THE RESOLVER'S OWN `normalize_ref`, IMPORTED — NOT RESTATED. The first
# draft of this file carried `ref.strip().lower().replace("_", "-")`, which is
# the rule as people describe it and NOT the rule as it runs: the real one also
# folds every character outside `[a-z0-9.-]` to `-`, collapses runs and trims.
# A collision check that normalises differently from the resolver models a
# reader that does not exist — it would miss a collision the write route hits,
# and invent one it does not. There is no version of this worth having twice.
sys.path.insert(0, str(HERE / "lib"))
from subsystem_resolver import normalize_ref, split_kind  # noqa: E402


# =============================================================================
# Process plumbing
# =============================================================================


@dataclass
class Ran:
    rc: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def run(cmd: list[str], *, timeout: int = 120, cwd: Path | None = None) -> Ran:
    """Run a command, capturing BOTH streams into their own buffers.

    🔴 NEVER PIPED, AND THAT IS THE POINT. `cmd | tail; echo $?` reports TAIL's
    status; that trap has been paid at least four times in this repo, most
    recently printing `NIXBUILD_RC=0` for a build that had just failed 45 tests.
    `subprocess.run` with `capture_output` gives the child's own status and the
    two streams separately, so a caller can read the CONTENT and the code.
    """
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError as exc:
        return Ran(127, "", f"{cmd[0]}: not found ({exc})")
    except subprocess.TimeoutExpired:
        return Ran(124, "", f"{cmd[0]}: timed out after {timeout}s")
    return Ran(proc.returncode, proc.stdout, proc.stderr)


def say(msg: str) -> None:
    print(f"cutover: {msg}", flush=True)


def refuse(rc: int, msg: str) -> int:
    print(f"🔴 cutover: REFUSED (rc {rc}) — {msg}", file=sys.stderr, flush=True)
    return rc


# =============================================================================
# P0 — preconditions
# =============================================================================


def backup_precondition(
    *, namespace: str, cronjob: str, max_age_h: float, kubeconfig: str | None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Is there a RECENT, SUCCESSFUL backup? `(ok, sentence)`.

    🔴 THIS IS THE ONE PRECONDITION THAT MAY NOT BE ASSUMED. devrc PR #1132
    exists because fifteen separate places asserted this store's backup state and
    were wrong about it — in both directions across its life. So this asks the
    cluster, and it treats every way of NOT getting an answer as a refusal:

      * the CronJob does not exist              -> refuse
      * `kubectl` is absent, or errors, or times out -> refuse
      * `status.lastSuccessfulTime` is absent   -> refuse
      * the timestamp is older than `max_age_h` -> refuse

    🔴 A MISSING FIELD IS NOT A ZERO. `lastSuccessfulTime` is unset on a CronJob
    that has never completed a run, which is indistinguishable in a bare `-o
    jsonpath` from a field the query mis-spelled — both print an empty string.
    So the whole object is fetched and the key looked up in Python, and its
    absence is reported as COULD NOT MEASURE with the reason, never as "0 hours
    ago" and never as a pass.

    ⚠ WHAT IT DOES NOT ESTABLISH, said out loud: that the backup is RESTORABLE.
    `lastSuccessfulTime` is the CronJob controller's record that a Job exited 0.
    Restore-testing is homelab-infra#551's business and is not re-derived here;
    this is a liveness gate, not a restore drill.
    """
    env_note = ""
    cmd = ["kubectl"]
    if kubeconfig:
        cmd += ["--kubeconfig", kubeconfig]
    cmd += ["-n", namespace, "get", "cronjob", cronjob, "-o", "json"]
    got = run(cmd, timeout=45)
    if not got.ok:
        first = (got.err or got.out).strip().splitlines()
        return False, (
            f"COULD NOT MEASURE the backup: `kubectl get cronjob {cronjob}` in "
            f"namespace {namespace} exited {got.rc} — "
            f"{first[0] if first else 'no output'}{env_note}"
        )
    try:
        obj = json.loads(got.out)
    except ValueError as exc:
        return False, f"COULD NOT MEASURE the backup: kubectl's JSON did not parse ({exc})"
    stamp = (obj.get("status") or {}).get("lastSuccessfulTime")
    if not stamp:
        return False, (
            f"COULD NOT MEASURE the backup: CronJob {namespace}/{cronjob} exists but "
            f"carries no `status.lastSuccessfulTime` — it has never completed a run, "
            f"or the controller has forgotten one. That is NOT a recent backup."
        )
    try:
        when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        return False, f"COULD NOT MEASURE the backup: unparseable timestamp {stamp!r} ({exc})"
    now = now or datetime.now(timezone.utc)
    age_h = (now - when).total_seconds() / 3600.0
    if age_h > max_age_h:
        return False, (
            f"the newest SUCCESSFUL backup of {namespace}/{cronjob} is {age_h:.1f} h "
            f"old ({stamp}); the ceiling is {max_age_h:.1f} h. Run one and re-try: "
            f"`kubectl -n {namespace} create job --from=cronjob/{cronjob} "
            f"manual-$(date +%s)` — that form DOES set an ownerReference, so it "
            f"updates lastSuccessfulTime; a hand-rolled Job does not."
        )
    return True, (
        f"backup OK — {namespace}/{cronjob} last succeeded {stamp} "
        f"({age_h:.1f} h ago, ceiling {max_age_h:.1f} h)"
    )


def write_route_deployed(
    *, url: str, token: str, scope: str, timeout: int = 20
) -> tuple[bool, str | None, str]:
    """Does the RUNNING image carry the write path? `(ok, sentence)`.

    🔴 THE PROBE IS A DELIBERATELY MALFORMED BODY, AND THAT IS WHAT MAKES IT
    SAFE. `server._append_bullet` validates the payload BEFORE it resolves the
    ref, so an empty JSON object `{}` is answered `400 bad-request` by a server
    that HAS the route and `405 read-only` by one that does not. It therefore
    discriminates the two without any input that could possibly be written — no
    ref is resolved, no file is opened, no bullet is rendered.

    🔴 A VALID BODY AGAINST A "REF THAT CANNOT EXIST" WAS THE FIRST DESIGN AND IS
    WORSE: it relies on a guess about what the store does NOT contain, and the
    failure mode of a wrong guess is an unrequested production write. Choosing an
    input that fails EARLIER is strictly better than choosing one that is
    expected to fail LATER.

    ⚠ 400 IS THE PASS HERE. That inversion is stated because it reads wrong at a
    glance and a future reader "fixing" it to `== 200` would make the check
    unsatisfiable — the same shape as a guard that can never fire.

    🔴 RETURNS `(ok, rc_on_failure, sentence)` — THREE STATES, NOT TWO. Every
    falsy result used to be answered by the caller with `RC_NO_WRITE_ROUTE`,
    which is documented as "the RUNNING image has no write path (405
    read-only)". So an UNREACHABLE pod during P0 was reported as "the image is
    read-only", sending the operator to redeploy the store over a network blip.
    Only the 405 arm means that; everything else is `RC_COULD_NOT_MEASURE`,
    which exists and was going unused.
    """
    target = f"{url.rstrip('/')}/api/v1/entry/{scope}/__cutover_probe__/bullets"
    req = urllib.request.Request(target, data=b"{}", method="POST")
    # 🔴 THE CLI'S OWN HELPER, NOT A FOURTH COPY. This hand-rolled the two
    # headers, in the same change whose `_apply_standard_headers` docstring
    # argues that a rule at three sites regenerates the same bug at N-1 of them.
    # A drifted copy here would be 403'd by the edge and reported as
    # "the image is read-only" — an operator sent to redeploy over a header.
    _cairn()._apply_standard_headers(req, token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return False, RC_COULD_NOT_MEASURE, (
                f"COULD NOT MEASURE the write route: the probe was ANSWERED "
                f"{resp.status}, and the only expected answers are 400 (route "
                f"present) or 405 (route absent). Not guessing."
            )
    except urllib.error.HTTPError as exc:
        status = exc.headers.get("X-Store-Status", "") if exc.headers else ""
        if exc.code == 400:
            return True, None, (
                f"the write route IS deployed — the malformed-body probe was refused "
                f"400 [{status or 'bad-request'}], which only a server that DISPATCHED "
                f"the POST can answer"
            )
        if exc.code == 405:
            return False, RC_NO_WRITE_ROUTE, (
                f"the RUNNING image is READ-ONLY — POST answered 405 [{status}]. This "
                f"is an operator problem, not a caller problem: deploy an image "
                f"carrying the write path before cutting over, or every append after "
                f"the freeze fails with nowhere to land."
            )
        return False, RC_COULD_NOT_MEASURE, (
            f"COULD NOT MEASURE the write route: the probe answered {exc.code} "
            f"[{status}]. 401 means the credential; 403 means the edge."
        )
    except urllib.error.URLError as exc:
        return False, RC_COULD_NOT_MEASURE, (
            f"COULD NOT MEASURE the write route: {url} unreachable: {exc.reason}")
    except OSError as exc:
        return False, RC_COULD_NOT_MEASURE, (
            f"COULD NOT MEASURE the write route: {url} unreachable: {exc}")


# =============================================================================
# P1 — the plan
# =============================================================================


@dataclass(frozen=True)
class EntryFacts:
    rel: str
    sha256: str
    aliases: tuple[str, ...]
    bullets: tuple[str, ...]


def read_store(root: Path) -> dict[str, EntryFacts]:
    """`<scope>/<entry>.md` -> facts, for every SHIPPABLE entry under `root`.

    🔴 THE SAME POPULATION `seed.sh` SHIPS, deliberately: depth-2 `*.md` regular
    files under a scope directory that is neither a dot-directory nor a symlink.
    A walk with different rules is how that script once printed
    `remote_entries=1 staged_entries=2` one line above `seed: OK`.
    """
    out: dict[str, EntryFacts] = {}
    if not root.is_dir():
        return out
    for scope_dir in sorted(root.iterdir()):
        if not scope_dir.is_dir() or scope_dir.name.startswith(".") or scope_dir.is_symlink():
            continue
        for path in sorted(scope_dir.glob("*.md")):
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            text = data.decode("utf-8", "surrogateescape")
            out[f"{scope_dir.name}/{path.name}"] = EntryFacts(
                rel=f"{scope_dir.name}/{path.name}",
                sha256=hashlib.sha256(data).hexdigest(),
                aliases=tuple(_aliases(text)),
                bullets=tuple(
                    line.strip() for line in text.splitlines()
                    if line.strip().startswith("- ")
                ),
            )
    return out


def _aliases(text: str) -> list[str]:
    """The `aliases:` list, parsed LINE BY LINE exactly as the reader parses it.

    🔴 NOT A YAML LOAD. `subsystem_recall` reads front matter line by line, which
    is why a WRAPPED `aliases: [...]` list makes an entry malformed rather than
    merely oddly formatted. Parsing it more generously here would make this
    script see aliases the resolver cannot, and a collision check that models a
    different reader than the one that runs is worse than none.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for line in lines[1:]:
        if line.strip() == "---":
            return []
        if line.startswith("aliases:"):
            body = line.split(":", 1)[1].strip().strip("[]")
            return [a.strip() for a in body.split(",") if a.strip()]
    return []


ADD = "ADD"            # the pod does not have it: pure addition
SAME = "SAME"          # byte-identical: nothing to do (this is the idempotence)
SUPERSEDES = "SUPERSEDES"   # divergent, host copy is the newer same-lineage file
MERGED = "MERGED"      # divergent, and an operator-authored resolution exists
NEEDS_MERGE = "NEEDS_MERGE"  # divergent, and only a human can resolve it


@dataclass
class Item:
    rel: str
    verdict: str
    reason: str
    source: Path


@dataclass
class Plan:
    items: list[Item] = field(default_factory=list)

    def of(self, verdict: str) -> list[Item]:
        return [i for i in self.items if i.verdict == verdict]

    @property
    def shippable(self) -> list[Item]:
        return [i for i in self.items if i.verdict in (ADD, SUPERSEDES, MERGED)]


def plan_delta(
    local: dict[str, EntryFacts],
    pod: dict[str, EntryFacts],
    *,
    store_root: Path,
    merged_dir: Path | None,
    peer: dict[str, EntryFacts] | None = None,
) -> Plan:
    """Classify every local entry against the served copy. THE MERGE RULE.

    Stated once, here, and generalised past the one file that motivated it:

      1. **Present on one side only** -> ADD. Additive; no decision to make.
      2. **Present on both, byte-identical** -> SAME. This is what makes a
         re-run a no-op rather than a second push.
      3. **Present on both and divergent, pod vs a host** -> the pod's copy is a
         LAGGING DERIVATIVE of that host's file (it got there by being seeded
         from it), so the host copy SUPERSEDES it — *unless* the pod holds a
         bullet the host lacks that carries the API attribution trailer. Such a
         bullet was written through the pod and exists in exactly one place in
         the world, so the entry becomes NEEDS_MERGE.
      4. **Present on both hosts and divergent** -> neither is a derivative of
         the other, so there is no supersession argument available. ALWAYS
         NEEDS_MERGE, never last-write-wins, whatever the mtimes say.
      5. A NEEDS_MERGE is resolved ONLY by a file at the same relative path under
         `--merged`, authored by a human. Nothing else clears it.

    🔴 WHY RULE 3 IS NOT "NEWER MTIME WINS". mtime is a property of a filesystem
    that has been rsynced, tarred and extracted; the pod's copies all carry the
    extract's time. The *lineage* argument is what actually holds: the pod's tree
    was produced FROM a host tree, so for any entry the pod did not itself change
    the host's copy contains everything the pod's does. The attribution trailer is
    precisely the marker of the pod having changed one, which is why detecting it
    is the whole safety of this rule rather than a nicety.

    ⚠ AND ITS LIMIT, STATED: rule 3 is a claim about *how the pod's copy got
    there*. It holds while `seed.sh` is the only writer other than the API. A
    future writer that edits the served copy by some third route would break the
    premise, not merely the implementation — which is why NEEDS_MERGE, not
    SUPERSEDES, is the fail-safe direction and why an unrecognised divergence
    ends in a refusal rather than a push.
    """
    plan = Plan()
    for rel, facts in sorted(local.items()):
        src = store_root / rel
        override = (merged_dir / rel) if merged_dir else None

        # 🔴 THE HAND-AUTHORED RESOLUTION IS CONSULTED FIRST, BEFORE EVERY OTHER
        # CLAUSE. It used to sit third, below the ADD and SAME returns, which
        # made it UNREACHABLE for any entry the pod does not hold — so an
        # operator who had written a merge for a host-only entry watched it be
        # silently ignored in favour of the local copy. A human decision must
        # not be overridden by a classifier; if it exists, it wins.
        if override is not None and override.is_file():
            plan.items.append(
                Item(rel, MERGED, f"resolved by hand at {override}", override)
            )
            continue

        # 🔴 THE PEER CHECK IS SECOND, AND IT USED TO BE FIFTH — BELOW `ADD`.
        # That ordering made merge rule 4 ("host vs host divergent -> ALWAYS a
        # hand merge, never last-write-wins") FALSE for every entry the pod does
        # not yet hold, which is precisely the population this migration is
        # about: the host-exclusive scopes reach the pod for the first time here,
        # so `rel not in pod` is true for all of them and the ADD return fired
        # first. The result was first-host-to-run-wins, silently, with no
        # operator decision — the exact failure the rule is written to forbid,
        # committed by the code that states it.
        if peer is not None and rel in peer and peer[rel].sha256 != facts.sha256:
            plan.items.append(Item(
                rel, NEEDS_MERGE,
                "both hosts hold a different copy — neither is a derivative of the "
                "other, so there is no supersession argument. Author a resolution "
                f"at <merged>/{rel}.",
                src,
            ))
            continue

        if rel not in pod:
            plan.items.append(Item(rel, ADD, "not present in the served copy", src))
            continue
        if pod[rel].sha256 == facts.sha256:
            plan.items.append(Item(rel, SAME, "byte-identical to the served copy", src))
            continue
        pod_only = [b for b in pod[rel].bullets if b not in facts.bullets]
        attributed = [b for b in pod_only if ATTRIBUTION.search(b)]
        if attributed:
            plan.items.append(Item(
                rel, NEEDS_MERGE,
                f"the served copy holds {len(attributed)} API-appended bullet(s) this "
                f"host does not — that content exists nowhere else and a push would "
                f"overwrite it. Author a resolution at <merged>/{rel}.",
                src,
            ))
            continue
        # 🔴 A DIVERGENCE WITH **NO** POD-ONLY BULLETS IS UNRECOGNISED, NOT
        # SUPERSEDED. The bytes differ and the bullet lists do not, so whatever
        # moved is OUTSIDE the region this rule can see — front matter, `## What
        # it is`, `## Pointers`, or a bullet's continuation lines. The attribution
        # scan says nothing about any of them, so classifying it SUPERSEDES
        # printed "the served copy holds 0 bullet line(s) this host lacks and
        # NONE is API-attributed" as the JUSTIFICATION FOR OVERWRITING IT — a
        # vacuous truth offered as evidence from a scan that examined nothing.
        #
        # 🔴 AND THE WRITER THAT PRODUCES EXACTLY THIS SHAPE IS `cairn put`,
        # WHICH THIS SAME CHANGE ADDS. Its stated reasons to exist are updating
        # `## Pointers` and rewriting an `OPEN:` marker — both outside the bullet
        # set, both invisible here. So rule 3's premise ("seed.sh and the API are
        # the only writers, and the API's changes are attributed") is not broken
        # by some hypothetical third route; it is broken by this file's sibling.
        # NEEDS_MERGE is the fail-safe direction this module already declares.
        if not pod_only:
            plan.items.append(Item(
                rel, NEEDS_MERGE,
                "divergent, but the served copy holds NO bullet line this host "
                "lacks — so whatever differs is outside the region the "
                "attribution rule can see (front matter, `## What it is`, "
                "`## Pointers`, or a bullet's continuation lines). A whole-file "
                "`cairn put` produces exactly this shape. Unrecognised, therefore "
                f"refused rather than overwritten. Diff them and resolve at "
                f"<merged>/{rel}.",
                src,
            ))
            continue
        plan.items.append(Item(
            rel, SUPERSEDES,
            f"divergent; the served copy holds {len(pod_only)} bullet line(s) this "
            f"host lacks and NONE is API-attributed, so they are a stale snapshot "
            f"of a file this host has since edited",
            src,
        ))
    return plan


# =============================================================================
# P2 — ref collisions in the union
# =============================================================================


@dataclass(frozen=True)
class Collision:
    tier: str          # "FILENAME" | "ALIAS" | "ALIAS-shadowed"
    scope: str
    ref: str
    claimants: tuple[str, ...]
    shadowed_by: tuple[str, ...] = ()

    @property
    def live(self) -> bool:
        return self.tier in ("FILENAME", "ALIAS")


def ref_collisions(union: dict[str, EntryFacts]) -> list[Collision]:
    """Every ref that does not resolve to exactly one entry, per scope.

    🔴 TWO CLASSES, AND CONFLATING THEM WOULD MAKE THIS A PERMANENTLY-RED GATE.
    `subsystem_resolver.resolve_ref_tiered` consults tier 1 (FILENAME) and only
    reaches tier 2 (ALIAS) **if tier 1 returned zero hits** — measured against
    the live store, not inferred: ref `cairn` in one scope resolves to
    `cairn.md` at `tier=filename` while a second entry in the same scope claims
    `cairn` as an alias. So:

      * a FILENAME collision, or an ALIAS collision on a ref no filename
        answers, is LIVE — the resolver raises `AmbiguousRefError`, the write
        route answers 400, and the entry is unwritable. It BLOCKS.
      * an alias SHADOWED by a filename is LATENT — it changes nothing today and
        becomes live only if that filename is renamed or removed. It is reported
        and does not block, because failing on it would refuse a migration over
        a defect that is already present and already harmless.
    """
    per_scope: dict[str, list[tuple[str, str | None, set[str], str]]] = {}
    for rel, facts in union.items():
        scope, filename = rel.split("/", 1)
        stem = filename[:-3] if filename.endswith(".md") else filename
        # 🔴 THE RESOLVER'S OWN `split_kind`, IMPORTED. The first version was
        # `parts = stem.split("."); slug, kind = (parts[0], parts[1]) if …` — a
        # SECOND SPELLING, inside a function whose comment insists a collision
        # check must never model a different reader than the resolver. It was
        # wrong in both directions: `split_kind` treats a trailing dot-segment as
        # a kind ONLY if it is in `KINDS`, so `foo.notes.md` has slug
        # `foo.notes` here and slug `foo` there (a FALSE collision, the
        # permanently-red-gate direction), while `a.b.c.md` silently discarded
        # `c`. See `test_a_KIND_QUALIFIED_file_collides_with_its_bare_sibling`
        # for the case it MISSED, which is the one that matters.
        slug, kind = split_kind(normalize_ref(stem))
        per_scope.setdefault(scope, []).append(
            (slug, kind, {normalize_ref(a) for a in facts.aliases}, filename)
        )
    found: list[Collision] = []
    for scope, entries in sorted(per_scope.items()):
        # 🔴 WHAT A FILENAME-TIER REF ACTUALLY REACHES — and the bare-slug row is
        # the whole fix. `resolve_ref_tiered` matches a ref with NO kind against
        # `e.slug` alone, **with no kind constraint**, so `svc` hits `svc.md` AND
        # `svc.process.md` and raises `AmbiguousRefError`. Registering only
        # kind-less files (the first version) made that pair invisible: measured,
        # the resolver raised on 2 candidates while this returned `[]` — the
        # exact condition P2 exists to detect, missed. `repo-cos.process` is the
        # resolver docstring's own worked example, so the shape is in live use.
        filename_refs: dict[str, set[str]] = {}
        aliases: dict[str, set[str]] = {}
        for slug, kind, alias_set, filename in entries:
            filename_refs.setdefault(slug, set()).add(filename)
            if kind is not None:
                # A kind-QUALIFIED ref matches only that (slug, kind) pair.
                filename_refs.setdefault(f"{slug}.{kind}", set()).add(filename)
            for alias in alias_set:
                aliases.setdefault(alias, set()).add(filename)
        for ref, files in sorted(filename_refs.items()):
            if len(files) > 1:
                found.append(Collision("FILENAME", scope, ref, tuple(sorted(files))))
        for alias, files in sorted(aliases.items()):
            # Shadowed by ANY filename-tier ref, bare or qualified — the alias
            # tier is reached only when tier 1 returns ZERO hits.
            if alias in filename_refs:
                if files != filename_refs[alias]:
                    found.append(Collision(
                        "ALIAS-shadowed", scope, alias, tuple(sorted(files)),
                        tuple(sorted(filename_refs[alias])),
                    ))
            elif len(files) > 1:
                found.append(Collision("ALIAS", scope, alias, tuple(sorted(files))))
    return found


def parse_alias_owner(specs: list[str]) -> dict[tuple[str, str], str]:
    """`--alias-owner <scope>:<alias>=<filename>` -> {(scope, alias): filename}."""
    owners: dict[tuple[str, str], str] = {}
    for spec in specs:
        if ":" not in spec or "=" not in spec:
            raise ValueError(
                f"--alias-owner wants <scope>:<alias>=<filename>, got {spec!r}"
            )
        head, _, filename = spec.partition("=")
        scope, _, alias = head.partition(":")
        if not (scope and alias and filename):
            raise ValueError(
                f"--alias-owner wants <scope>:<alias>=<filename>, got {spec!r}"
            )
        owners[(scope, normalize_ref(alias))] = filename
    return owners


# =============================================================================
# P5 — the freeze, and the WATCHED EACCES
# =============================================================================


def probe_writable(path: Path) -> str:
    """`"writable"` | `"refused"` | `"error:<errno>"` — by SYSCALL, not by mode.

    🔴 A MODE BIT IS A CLAIM; A REFUSED WRITE IS EVIDENCE. `stat -c %a` says what
    the inode is labelled, not what this process may do — setuid, ACLs, an
    overlay, a bind mount and being root all separate the two, and the last of
    those is common enough that this script refuses to run as root at all.

    The probe opens the REAL entry file for APPEND and writes ZERO bytes. That
    is the exact syscall an `Edit`/`Write` on the entry performs first, so it is
    a faithful discriminator; and because nothing is written, neither the
    contents nor the mtime move. A probe that wrote a byte and truncated it back
    would be a mutation with a crash window, against a curated store.
    """
    try:
        with open(path, "ab"):
            pass
    except PermissionError:
        return "refused"
    except OSError as exc:
        return f"error:{errno.errorcode.get(exc.errno, exc.errno)}"
    return "writable"


def survey(store: Path) -> dict[str, int]:
    """Count entry files by what a write to each ACTUALLY does.

    🔴 `examined` IS PRINTED BESIDE EVERY COUNT, ALWAYS. A bare "0 writable" from
    a walk that visited nothing is indistinguishable from a fully frozen store,
    and it is the reassuring zero the whole repo is arranged to refuse. The same
    rule `drift-check.sh` follows by printing links EXAMINED beside links
    dangling.
    """
    tally = {"examined": 0, "writable": 0, "refused": 0, "other": 0}
    for rel in read_store(store):
        tally["examined"] += 1
        state = probe_writable(store / rel)
        if state in ("writable", "refused"):
            tally[state] += 1
        else:
            tally["other"] += 1
    return tally


MODE_LEDGER = ".cairn-cutover-modes.json"


def save_modes(store: Path, dest: Path) -> int:
    """Record every entry file's CURRENT mode, so a freeze can be undone exactly.

    🔴 A RESTORE NEEDS THE ORIGINALS, AND `chmod 0444` DESTROYS THEM.
    `--unfreeze` used to set every entry to 0644 unconditionally and call itself
    a rollback. For a file that was 0600 — a plausible mode on a
    client-confidential entry, and the mode this script's own staging directory
    uses — that is a permission WIDENING presented as a restore, on exactly the
    content the widening matters for. So the modes are written down before they
    are changed, into the run directory beside the pre-push bytes.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    modes = {rel: (store / rel).stat().st_mode & 0o777 for rel in read_store(store)}
    dest.write_text(json.dumps(modes, sort_keys=True, indent=1), encoding="utf-8")
    dest.chmod(0o600)
    return len(modes)


def restore_modes(store: Path, ledger: Path) -> tuple[int, int]:
    """Put every entry file back to the mode `save_modes` recorded.

    Returns `(restored, unknown)`. An entry with no ledger row is NOT guessed at:
    it is counted and reported, because inventing 0644 for it is the exact defect
    this function replaces.
    """
    recorded: dict[str, int] = json.loads(ledger.read_text(encoding="utf-8"))
    restored = unknown = 0
    for rel in read_store(store):
        want = recorded.get(rel)
        if want is None:
            unknown += 1
            continue
        path = store / rel
        if (path.stat().st_mode & 0o777) != want:
            path.chmod(want)
        restored += 1
    return restored, unknown


def set_entry_mode(store: Path, mode: int) -> int:
    """chmod every ENTRY FILE. Scope directories are deliberately untouched.

    🔴 FILES, NOT DIRECTORIES, AND THE ASYMMETRY IS A DESIGN DECISION WITH A
    KNOWN COST. Freezing the directories too would also stop a genuinely NEW
    entry being created — and the hosted API has NO create route (`POST` and
    `PUT` both resolve an existing ref; a ref that does not resolve is 404
    `ref-unknown`), so the first entry for a new subsystem would have nowhere to
    go at all. Freezing the files closes the hazard this cutover is about — an
    append or an overwrite that lands only locally and dies at the next seed —
    while leaving the one operation the API cannot yet serve. The gap is real and
    is written down rather than papered over; see the design doc's "What
    criterion 9 does NOT close".
    """
    changed = 0
    for rel in read_store(store):
        path = store / rel
        if (path.stat().st_mode & 0o777) != mode:
            path.chmod(mode)
            changed += 1
    return changed


# =============================================================================
# Orchestration
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cairn-cutover.py",
        description="Cairn criterion 9 — the write-through cutover. Dry run unless --apply.",
    )
    p.add_argument("--apply", action="store_true",
                   help="actually push and freeze; without it NOTHING is changed")
    p.add_argument("--store", type=Path, default=DEFAULT_STORE)
    p.add_argument("--merged", type=Path, default=DEFAULT_MERGED,
                   help="operator-authored resolutions, mirroring <scope>/<entry>.md")
    p.add_argument("--peer-manifest", type=Path, default=None,
                   help="a JSON manifest of the OTHER host's store, so a host-vs-host "
                        "divergence is refused rather than silently superseded")
    p.add_argument("--run-dir", type=Path, default=None)
    p.add_argument("--push", default=None, metavar="NS/DEPLOY",
                   help="hand the delta tree to seed.sh --push")
    p.add_argument("--dest", default="/data")
    p.add_argument("--alias-owner", action="append", default=[], metavar="S:A=FILE",
                   help="acknowledge one LIVE ref collision by naming its owner")
    p.add_argument("--backup-namespace", default=DEFAULT_BACKUP_NAMESPACE)
    p.add_argument("--backup-cronjob", default=DEFAULT_BACKUP_CRONJOB)
    p.add_argument("--backup-max-age-hours", type=float, default=DEFAULT_BACKUP_MAX_AGE_H)
    p.add_argument("--kubeconfig", default=os.environ.get("KC_HOMELAB") or None)
    p.add_argument("--freeze", action="store_true",
                   help="run phase 5 alone (still needs --apply to take effect)")
    p.add_argument("--unfreeze", action="store_true",
                   help="ROLLBACK of phase 5: restore each entry file to the mode "
                        "the freeze recorded for it")
    p.add_argument("--mode-ledger", type=Path, default=None, dest="mode_ledger",
                   help=f"the freeze's <run-dir>/{MODE_LEDGER}; the newest one "
                        f"under the default run root is used when omitted")
    p.add_argument("--manifest", action="store_true",
                   help="print this host's store manifest as JSON and exit — the "
                        "input --peer-manifest wants, produced on the other host")
    p.add_argument("--rollback-push", type=Path, default=None, metavar="RUN_DIR",
                   help="ROLLBACK of phase 3: re-PUT the pre-push bytes this script "
                        "saved under <RUN_DIR>/pre-push for every entry it overwrote")
    return p


def rollback_push(run_dir: Path, *, apply: bool, timeout: int = 60) -> int:
    """Re-PUT every pre-push copy, through `cairn put`. One write path, again.

    🔴 THIS UNDOES AN OVERWRITE, NOT AN ADDITION. Entries the push ADDED have no
    pre-image and are not restored, because the API has no delete verb — that is
    a property of the server, not an omission here, and it is why the phase's
    rollback is described as partial wherever it is offered rather than as "the
    rollback".

    🔴 IT DOES NOT SEND `--if-match`, IT LETS `cairn put` DERIVE ONE FROM A LIVE
    SYNC. Restoring against a revision captured before the push would 412 every
    time (the push is exactly what moved it). Deriving live means the restore
    refuses if a THIRD party has changed the entry since — which is the correct
    behaviour: a rollback that silently discards somebody else's later write is
    the lost update this whole precondition exists to prevent.
    """
    src = run_dir / "pre-push"
    if not src.is_dir():
        return refuse(RC_USAGE, (
            f"{src} does not exist. Either that run overwrote nothing (an ADD-only "
            f"push has no pre-image to restore) or the run directory is wrong."
        ))
    files = sorted(p for p in src.glob("*/*.md") if p.is_file())
    say(f"rollback-push: {len(files)} entr(ies) have a saved pre-push copy")
    if not files:
        return refuse(RC_USAGE, (
            f"{src} holds no entry files. A rollback that restores nothing must not "
            f"report success — that is the reassuring zero."
        ))
    if not apply:
        for path in files:
            say(f"DRY RUN — would restore {path.parent.name}/{path.name}")
        say("Nothing was changed. Re-run with --apply.")
        return RC_OK
    failed = []
    for path in files:
        scope, ref = path.parent.name, path.name[:-3]
        got = run([
            sys.executable, str(CAIRN), "put", "--scope", scope, "--ref", ref,
            "--file", str(path),
        ], timeout=timeout)
        sys.stdout.write(got.out)
        sys.stderr.write(got.err)
        if not got.ok:
            failed.append(f"{scope}/{ref} (rc {got.rc})")
    say(f"rollback-push: restored {len(files) - len(failed)} of {len(files)}")
    if failed:
        return refuse(RC_ACCEPTANCE, f"could not restore: {failed}")
    return RC_OK


def emit_manifest(store: Path) -> int:
    """The other host's store, reduced to what the peer check needs.

    🔴 NO BULLET TEXT. The peer manifest is copied between machines and read by a
    third process; it is used for exactly two questions — "does the other host
    hold this entry?" and "is its copy the same bytes?" — and a sha256 answers
    both. Shipping the prose as well would move client-confidential content
    across a channel that does not need it, for no gain. The pod-side
    attribution check reads bullets, but it reads them from files this process
    opened itself, never from a manifest.
    """
    facts = read_store(store)
    print(json.dumps(
        {rel: {"sha256": f.sha256, "aliases": list(f.aliases)}
         for rel, f in facts.items()},
        sort_keys=True,
    ))
    return RC_OK


def load_peer(path: Path | None) -> dict[str, EntryFacts] | None:
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        rel: EntryFacts(rel, v["sha256"], tuple(v.get("aliases", [])),
                        tuple(v.get("bullets", [])))
        for rel, v in raw.items()
    }


def _newest_mode_ledger(root: Path | None = None) -> Path | None:
    """The most recent run's mode ledger, or None. Never a guess at the modes."""
    root = root or DEFAULT_RUN_ROOT
    if not root.is_dir():
        return None
    found = sorted(root.glob(f"*/{MODE_LEDGER}"))
    return found[-1] if found else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.manifest:
        return emit_manifest(args.store)

    if args.rollback_push is not None:
        return rollback_push(args.rollback_push, apply=args.apply)

    # 🔴 REFUSED AS ROOT, BEFORE ANYTHING ELSE. Phase 5's whole verification is a
    # WATCHED EACCES, and root bypasses the permission bits it depends on — so
    # as root the freeze would be applied, the probe would report "writable",
    # and this script would roll the freeze back and report failure on a store
    # that was correctly frozen. Wrong in the safe direction, and still wrong.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return refuse(RC_ROOT, (
            "running as root. The freeze's only evidence is a REFUSED write, and "
            "root is not refused — the check would be vacuous. Re-run as the user "
            "that owns the store."
        ))

    if args.unfreeze:
        before = survey(args.store)
        say(f"unfreeze: before  {before}")
        ledger = args.mode_ledger or _newest_mode_ledger()
        # 🔴 `is_file()`, NOT JUST `is not None`. An explicit `--mode-ledger`
        # naming a path that does not exist reached `restore_modes` and died on
        # an unguarded `FileNotFoundError` at exit 1 — a code outside this
        # script's whole vocabulary, on the recovery path, where the operator is
        # least able to interpret it.
        if ledger is None or not ledger.is_file():
            return refuse(RC_COULD_NOT_MEASURE, (
                f"no readable mode ledger ({ledger or 'none found'}), so there is "
                "nothing to restore TO. The freeze "
                "records every entry file's original mode before changing it; "
                "without that file an 'unfreeze' can only invent one, and inventing "
                "0644 for an entry that was 0600 WIDENS permissions on "
                "client-confidential content while calling itself a rollback. Pass "
                f"--mode-ledger <run-dir>/{MODE_LEDGER} explicitly, or chmod by hand "
                "having decided what the modes should be."
            ))
        say(f"unfreeze: restoring from {ledger}")
        if not args.apply:
            say("DRY RUN — pass --apply to restore the recorded modes. "
                "Nothing was changed.")
            return RC_OK
        restored, unknown = restore_modes(args.store, ledger)
        after = survey(args.store)
        say(f"unfreeze: restored {restored} entry file(s), {unknown} not in the "
            f"ledger and therefore LEFT ALONE; after {after}")
        if unknown:
            return refuse(RC_COULD_NOT_MEASURE, (
                f"{unknown} entry file(s) have no recorded mode — they were created "
                f"after the freeze. They are untouched, not guessed at. Decide their "
                f"modes yourself."
            ))
        return RC_OK

    run_dir = args.run_dir or (DEFAULT_RUN_ROOT / datetime.now(timezone.utc)
                               .strftime("%Y%m%dT%H%M%SZ"))
    delta_dir = run_dir / "delta"
    stage_dir = run_dir / "stage"
    cache_dir = run_dir / "cache"
    prepush_dir = run_dir / "pre-push"
    ledger_path = run_dir / MODE_LEDGER

    # ---- P0 preconditions -------------------------------------------------
    if not args.freeze:
        local = read_store(args.store)
        if not local:
            return refuse(RC_NO_STORE, (
                f"{args.store} holds no shippable entries. A cutover that pushed an "
                f"empty delta and then froze an empty store would report success "
                f"having done nothing."
            ))
        say(f"P0 local store: {len(local)} entries / "
            f"{len({r.split('/')[0] for r in local})} scopes at {args.store}")

        ok, sentence = backup_precondition(
            namespace=args.backup_namespace, cronjob=args.backup_cronjob,
            max_age_h=args.backup_max_age_hours, kubeconfig=args.kubeconfig,
        )
        say(f"P0 {sentence}")
        if not ok:
            return refuse(RC_BACKUP, sentence)

        # 🔴 0700, ON EVERY LEVEL. What lands under here is a FULL PLAINTEXT COPY
        # of a client-confidential store — the sync below writes it, and the
        # runbook prescribes several dry runs before an apply, so these
        # accumulate. `mkdir` at the default umask leaves them 0755, i.e.
        # world-readable, in the same change that is careful to 0600 the merged
        # entry for exactly this reason. `mode=` on `mkdir` is masked by the
        # umask, so the chmod is explicit rather than trusted to the flag.
        DEFAULT_RUN_ROOT.mkdir(parents=True, exist_ok=True)
        DEFAULT_RUN_ROOT.chmod(0o700)
        run_dir.mkdir(parents=True, exist_ok=True)
        run_dir.chmod(0o700)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.chmod(0o700)
        synced = run(
            [sys.executable, str(CAIRN), "--cache", str(cache_dir), "sync"], timeout=120
        )
        say(f"P0 cairn sync -> rc {synced.rc}: {(synced.out or synced.err).strip()[:200]}")
        if not synced.ok:
            return refuse(RC_UNREACHABLE, (
                f"`cairn sync` exited {synced.rc}; the served copy could not be read, "
                f"so there is nothing to compare the local store against and a push "
                f"would be blind. {(synced.err or synced.out).strip()[:300]}"
            ))
        pod = read_store(cache_dir)
        say(f"P0 served copy: {len(pod)} entries / "
            f"{len({r.split('/')[0] for r in pod})} scopes")

        url, token = _config()
        probe_scope = sorted({r.split("/")[0] for r in pod})[0] if pod else None
        if probe_scope is None:
            return refuse(RC_COULD_NOT_MEASURE, (
                "the served copy holds no scope, so the write-route probe has no "
                "scope to address. A zero here is not a pass."
            ))
        ok, probe_rc, sentence = write_route_deployed(
            url=url, token=token, scope=probe_scope)
        say(f"P0 {sentence}")
        if not ok:
            # 🔴 THE PROBE'S OWN CODE, not a blanket RC_NO_WRITE_ROUTE. Only the
            # 405 arm means "the image has no write path"; an unreachable pod
            # answered under that code sent the operator to redeploy the store.
            return refuse(probe_rc or RC_COULD_NOT_MEASURE, sentence)

        # ---- P1 the plan --------------------------------------------------
        peer = load_peer(args.peer_manifest)
        if peer is None:
            say("P1 ⚠ NO --peer-manifest: a host-vs-host divergence cannot be "
                "detected on this run, so rule 4 of the merge rule is UNCHECKED. "
                "Produce one with `--manifest` on the other host.")
        plan = plan_delta(local, pod, store_root=args.store,
                          merged_dir=args.merged, peer=peer)
        counts = {v: len(plan.of(v)) for v in (ADD, SAME, SUPERSEDES, MERGED, NEEDS_MERGE)}
        say(f"P1 plan over {len(plan.items)} local entries: {counts}")
        for item in plan.of(NEEDS_MERGE):
            print(f"  NEEDS_MERGE {item.rel}: {item.reason}", file=sys.stderr)
        if plan.of(NEEDS_MERGE):
            return refuse(RC_UNRESOLVED_DIVERGENCE, (
                f"{len(plan.of(NEEDS_MERGE))} entr(ies) need a hand-authored merge. "
                f"Write each one to {args.merged}/<scope>/<entry>.md and re-run. "
                f"NOTHING was pushed."
            ))

        # ---- P2 collisions ------------------------------------------------
        union = dict(pod)
        for item in plan.shippable:
            data = item.source.read_bytes()
            text = data.decode("utf-8", "surrogateescape")
            union[item.rel] = EntryFacts(
                item.rel, hashlib.sha256(data).hexdigest(), tuple(_aliases(text)),
                tuple(l.strip() for l in text.splitlines() if l.strip().startswith("- ")),
            )
        if peer:
            for rel, facts in peer.items():
                union.setdefault(rel, facts)
        try:
            owners = parse_alias_owner(args.alias_owner)
        except ValueError as exc:
            return refuse(RC_USAGE, str(exc))
        collisions = ref_collisions(union)
        live = [c for c in collisions if c.live]
        latent = [c for c in collisions if not c.live]
        say(f"P2 union {len(union)} entries: LIVE ref collisions={len(live)} "
            f"LATENT (filename-shadowed alias)={len(latent)}")
        for c in latent:
            print(f"  LATENT {c.scope}: alias {c.ref!r} claimed by "
                  f"{list(c.claimants)} but the FILENAME tier answers first with "
                  f"{list(c.shadowed_by)} — unreachable today, live the day that "
                  f"file is renamed.", file=sys.stderr)
        unacknowledged = [c for c in live if (c.scope, c.ref) not in owners]
        for c in unacknowledged:
            print(f"  LIVE {c.scope}: ref {c.ref!r} ({c.tier}) resolves to "
                  f"{list(c.claimants)} — the write route answers such a ref 400 "
                  f"and BOTH entries become unwritable.", file=sys.stderr)
        if unacknowledged:
            return refuse(RC_REF_COLLISION, (
                f"{len(unacknowledged)} ref(s) would resolve to two entries in the "
                f"union. Edit the losing entry's `aliases:` to drop the ref, then "
                f"acknowledge the decision with "
                f"--alias-owner <scope>:<ref>=<winning-filename>. NOTHING was pushed."
            ))
        if owners:
            say(f"P2 {len(owners)} live collision(s) acknowledged by --alias-owner: "
                f"{sorted(f'{s}:{a}' for s, a in owners)}")

        # ---- P3 the push --------------------------------------------------
        if not args.apply:
            say(f"DRY RUN — {len(plan.shippable)} entr(ies) would be pushed "
                f"({counts[ADD]} new, {counts[SUPERSEDES]} superseding, "
                f"{counts[MERGED]} hand-merged); {counts[SAME]} already identical.")
            say(f"DRY RUN — the push would be: {SEED} --store {delta_dir} "
                f"--stage {stage_dir} --push {args.push or '<ns>/<deploy>'} "
                f"--dest {args.dest}")
            say("DRY RUN — the freeze would then chmod 0444 over "
                f"{len(local)} entry file(s) and require EVERY ONE to refuse a write.")
            say("Nothing was changed. Re-run with --apply.")
            return RC_OK

        if not plan.shippable:
            say("P3 the delta is EMPTY — the served copy already holds every entry "
                "this host has, byte for byte. Nothing to push; this is the "
                "idempotent re-run, not a failure.")
        else:
            if not args.push:
                return refuse(RC_USAGE, "--apply needs --push <namespace>/<deployment>")
            _materialise(plan, delta_dir)
            _save_prepush(plan, cache_dir, prepush_dir)
            say(f"P3 pre-push copies of every entry about to be OVERWRITTEN saved "
                f"under {prepush_dir} — that is the rollback for this phase.")
            pushed = run([
                "bash", str(SEED), "--store", str(delta_dir), "--stage", str(stage_dir),
                "--push", args.push, "--dest", args.dest,
            ], timeout=600)
            sys.stdout.write(pushed.out)
            sys.stderr.write(pushed.err)
            if not pushed.ok:
                return refuse(RC_ACCEPTANCE, (
                    f"seed.sh exited {pushed.rc}. Read its own verdict lines above — "
                    f"it distinguishes a staging failure from a push that landed "
                    f"partially. NOTHING was frozen."
                ))

        # ---- P4 acceptance ------------------------------------------------
        rc = _acceptance(args, cache_dir)
        if rc != RC_OK:
            return rc

    # ---- P5 the freeze ----------------------------------------------------
    before = survey(args.store)
    say(f"P5 before the freeze: {before}")
    if before["examined"] == 0:
        return refuse(RC_COULD_NOT_MEASURE, (
            "the freeze walked 0 entry files. A '0 writable' from a walk that "
            "visited nothing is not a frozen store."
        ))
    if before["writable"] == 0:
        say("P5 every entry file already refuses a write — the freeze is already "
            "applied. This is the idempotent re-run.")
        return RC_OK
    if not args.apply:
        say(f"DRY RUN — would chmod 0444 over {before['writable']} writable entry "
            f"file(s) and then require all {before['examined']} to refuse a write.")
        return RC_OK

    # 🔴 RECORD THE MODES BEFORE DESTROYING THEM. `chmod 0444` is not reversible
    # from the result — every entry ends up looking the same, so an "unfreeze"
    # with no ledger can only invent a mode, and inventing 0644 for a file that
    # was 0600 is a permission WIDENING on client-confidential content dressed as
    # a rollback. `--unfreeze` refuses outright without this file.
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    n_recorded = save_modes(args.store, ledger_path)
    say(f"P5 recorded {n_recorded} original mode(s) to {ledger_path} — "
        f"`--unfreeze` REQUIRES this file and refuses without it.")
    changed = set_entry_mode(args.store, 0o444)
    after = survey(args.store)
    say(f"P5 chmod 0444 on {changed} entry file(s); after {after}")
    # 🔴 THE WATCHED EACCES. Not `stat -c %a`, not `test -w`: every entry file is
    # opened for append and every one of them must be REFUSED.
    #
    # ⚠ THE `examined == 0` CLAUSE IS UNREACHABLE, AND IT IS LABELLED RATHER
    # THAN COUNTED. It looks like the empty-walk guard — "a zero from a walk that
    # visited nothing satisfies `refused == examined`" — and that hazard is real,
    # but it is already closed ABOVE, by the `before["examined"] == 0` refusal
    # that returns before any chmod happens. Between those two points the store
    # would have to lose every entry, so nothing can reach this clause with a
    # zero. A mutation sweep scored its removal SURVIVED, which is the correct
    # answer for an unreachable expression; it stays as a cheap statement of
    # intent, and `claude/RULES.md` is explicit that such a clause must not be
    # read as coverage. The guard that actually fires on an empty store is the
    # `before` one, and `test_a_FREEZE_over_an_empty_store_is_COULD_NOT_MEASURE_
    # not_success` is the test that kills its removal.
    if after["refused"] != after["examined"] or after["examined"] == 0:
        restore_modes(args.store, ledger_path)
        return refuse(RC_FREEZE_INEFFECTIVE, (
            f"the freeze did not take: {after['refused']} of {after['examined']} "
            f"entry file(s) refused a write ({after['writable']} still writable, "
            f"{after['other']} failed for another reason). The mode bits were "
            f"RESTORED from {ledger_path} rather than left half-applied. A store "
            f"that looks frozen and is not is worse than one that is plainly not."
        ))
    say(f"P5 WATCHED EACCES: all {after['examined']} entry file(s) refused an "
        f"append. The local store is now a read-through cache.")
    # ⚠ THE DEPLOY STEP NAMES `scripts/ship.sh`, NOT THE UNDERLYING BINARY, and
    # that is deliberate on two counts. It is the more actionable instruction —
    # ship.sh converges BOTH hosts and this store is per-host — and it keeps the
    # launcher vocabulary out of a top-level script that must never launch one.
    # `test_no_real_launchers.py` treats a bare mention as reaching the binary,
    # correctly: it was reopened once by a name that was "only in a string".
    say("REMAINING, and NOT done by this script: land the protocol change that "
        "routes `subsystem-index` appends through `cairn append`, then merge, "
        "pull and run `scripts/ship.sh` so BOTH hosts get it. Until that lands, "
        "the skill still tells a writer to Edit a file that now refuses the "
        "write — a loud failure, not a silent one, but a failure.")
    return RC_OK


_CAIRN_MODULE = None


def _cairn():
    """The `cairn` CLI as a module, loaded once. It owns config and headers.

    🔴 IMPORTED, NEVER REIMPLEMENTED. Both things this script needs from it —
    the config parser and the request headers — are rules with a measured
    incident behind them (a missing value that reads as "the store is down"; a
    User-Agent the edge 403s). A second copy of either would be a fourth site
    for a rule that already argues, in its own docstring, that three is too many.
    """
    global _CAIRN_MODULE
    if _CAIRN_MODULE is None:
        sys.path.insert(0, str(HERE))
        spec = importlib.util.spec_from_loader(
            "cairn_cli", importlib.machinery.SourceFileLoader("cairn_cli", str(CAIRN))
        )
        module = importlib.util.module_from_spec(spec)
        # `sys.modules[name] = mod` BEFORE `exec_module`, or the first @dataclass
        # raises `AttributeError: 'NoneType' has no attribute '__dict__'`.
        sys.modules["cairn_cli"] = module
        spec.loader.exec_module(module)
        _CAIRN_MODULE = module
    return _CAIRN_MODULE


def _config() -> tuple[str, str]:
    """URL + token, read through `cairn`'s own loader — never a second parser."""
    return _cairn().load_config()


def _materialise(plan: Plan, dest: Path) -> None:
    """Copy exactly the shippable entries into a tree `seed.sh` can be pointed at.

    🔴 THE TREE IS BUILT FRESH EVERY RUN, into a run-scoped directory. `seed.sh`
    rsyncs SOURCE->STAGE with `--delete`, so a stale delta tree left from an
    earlier run would push entries a later plan did not choose.
    """
    if dest.exists():
        shutil.rmtree(dest)
    for item in plan.shippable:
        target = dest / item.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item.source, target)


def _save_prepush(plan: Plan, cache: Path, dest: Path) -> None:
    """Keep the SERVED bytes of every entry this push will overwrite.

    🔴 THIS IS THE ROLLBACK FOR P3, AND IT MUST BE TAKEN BEFORE THE PUSH. An ADD
    has no pre-image to save (rolling one back is a deletion, which the API
    cannot do anyway); a SUPERSEDES or a MERGED replaces bytes that, after the
    tar extract, exist only in the daily backup. Saving them here turns "restore
    the whole store from a bundle" into "re-PUT one file".
    """
    dest.mkdir(parents=True, exist_ok=True)
    for item in plan.shippable:
        # 🔴 ONE PREDICATE, AND THE OTHER ONE WAS DELETED ON EVIDENCE. This loop
        # used to open with `if item.verdict == ADD: continue` as well. A
        # mutation sweep scored that line SURVIVED — not because the test was
        # weak but because the two questions are the SAME question: `ADD` means
        # "the served copy has no such entry", which is exactly what
        # `served.is_file()` answers, from the very tree the verdict was computed
        # against. It could never disagree, so it was an unreachable guard being
        # counted as coverage. The remaining check is the direct one: save what
        # the push is about to overwrite, which is whatever bytes are there.
        served = cache / item.rel
        if served.is_file():
            target = dest / item.rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(served, target)


def _acceptance(args: argparse.Namespace, cache_dir: Path) -> int:
    """The prescribed acceptance check, plus the byte-identity verifier.

    🔴 THE CHECK IS THE ONE THE CARD PRESCRIBES — `comm -23` of this host's entry
    names against the served copy's, which must print ZERO lines. It is not
    re-invented here: it is the same containment question `seed.sh`'s own push
    verdict answers, asked again after the fact because a push verdict is a claim
    about the push and this is a claim about the STATE.

    `verify-byte-identity.sh` is then run unmodified over every scope. Its own
    header documents the two controls that make its green meaningful, and its
    PASS lines print `raw-diff-lines` and `store-root-lines` beside the verdict
    so a reader can see the canonicalisation was spent where it claims.
    """
    refreshed = run(
        [sys.executable, str(CAIRN), "--cache", str(cache_dir), "sync"], timeout=120
    )
    if not refreshed.ok:
        return refuse(RC_COULD_NOT_MEASURE, (
            f"could not re-read the served copy after the push (rc {refreshed.rc}), so "
            f"the acceptance check has no second side to compare against. The push "
            f"already happened; re-run to verify."
        ))
    local_names = sorted(read_store(args.store))
    pod_names = set(read_store(cache_dir))
    missing = [n for n in local_names if n not in pod_names]
    say(f"P4 acceptance (comm -23, this host vs the served copy): "
        f"local={len(local_names)} served={len(pod_names)} missing={len(missing)}")
    if missing:
        for name in missing[:40]:
            print(f"  MISSING FROM THE SERVED COPY: {name}", file=sys.stderr)
        return refuse(RC_ACCEPTANCE, (
            f"{len(missing)} entr(ies) this host holds are not on the pod. The store "
            f"was NOT frozen — freezing now would strand them."
        ))
    token_file = os.environ.get("SUBSYSTEM_STORE_TOKEN_FILE")
    if not token_file:
        say("P4 ⚠ verify-byte-identity.sh NOT RUN — it takes --token-file and "
            "$SUBSYSTEM_STORE_TOKEN_FILE is unset. The `comm -23` half above passed; "
            "the byte-identity half is UNMEASURED, which is not the same as clean.")
        return RC_OK
    url, _ = _config()
    verified = run([
        "bash", str(VERIFY), "--store", str(args.store), "--url", url,
        "--token-file", token_file,
    ], timeout=600)
    sys.stdout.write(verified.out)
    sys.stderr.write(verified.err)
    if not verified.ok:
        return refuse(RC_ACCEPTANCE, (
            f"verify-byte-identity.sh exited {verified.rc}. Read its per-scope FAIL "
            f"lines above. The store was NOT frozen."
        ))
    return RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
