#!/usr/bin/env python3
"""ONE deterministic call for `/analyze-service` recon: locate, recall, config, log.

WHY THIS EXISTS — THE MEASUREMENT
---------------------------------
`/analyze-service` recon was hand-run: the skill body described six steps and the
agent executed them as loose shell. Measured over n=20 real invocations recovered
from `~/.claude/projects/**/*.jsonl` (the harness is
`scripts/session-analysis/recon_cost.py`, and its recorded baseline is
`claudedocs/analyze-service-baseline/`):

    median  39.5 assistant turns · 22.5 tool calls · 35.5 KB tool output (~9.1k tok)
    p90     ~15.4k tok            max  91 KB / 62 calls

and the cost shape was death by a thousand cuts — 359 Bash calls at a mean of
1.5 KB, largest single result 15 KB. There was no fat dump to trim. The only
lever is COLLAPSING ROUND TRIPS, which is what this module is: the four static
recon steps in one process, emitting one bounded brief.

🔴 IT NEVER WRITES, AND IT NEVER MUTATES THE STORE. The `/analyze-service`
write-back half is confirm-gated, diff-first and lives in the skill; this module
has no write path at all. `TestReconNeverWrites` hashes a store tree either side
of every mode and every failure.

🔴 IT DOES NOT REIMPLEMENT THE INDEX READ. Scope derivation is
`subsystem_touch.scope_for_repo` (the WRITER's own function) and the read is
`subsystem_recall.recall` — the same call `/resume` makes. Before this module,
recon shelled out to `cat ~/.claude/analyze-service-index/<scope>/<slug>.md`; one
such call returned 11 KB of an entry with no ref resolution, no ambiguity check
and no sensitivity fold. A second reader of that store would be a second matcher
free to drift from the resolver, and its failure mode is a miss that reads as
"no index yet".

🔴 THE INDEX IS ASKED AT EVERY SEARCHED ROOT, NOT AT THE ONE THAT "WON". Root
ranking is a path-name heuristic — a lead of a few paths between two repos is a
naming convention, not ownership — and asking only the leader reported a curated
entry in the cwd's own scope as `ref-absent`. Every searched root is asked in
rank order, de-duplicated by SCOPE, first hit wins, and the brief prints WHICH
scope answered and on what BASIS. A miss then names every scope it checked, so
"nothing recorded anywhere" is a claim with a denominator. See `index_scopes`
for why a miss here is a store WRITE and not merely a wasted read.

🔴 LIVE CLUSTER STATE IS OPT-IN AND OFF BY DEFAULT (`--live`). It was 124 of the
359 measured Bash calls, it is the half that can be wrong by the time you read
it, and it is the half a static question never needed. Static recon — locate,
index, config, git log — always runs.

🔴 NEVER A SILENT ZERO. Every section reports what it EXAMINED beside what it
found, and every not-found carries a status distinguishing "looked and there is
nothing" from "did not look":

    roots     `searched` / `absent` / `not-a-directory` / `walk-failed`
    locate    `hits` / `no-match` / `not-searched`
    index     recall's own status (`recalled`/`scope-absent`/`ref-absent`/…), plus
              the BASIS naming which repo the scope was derived from
    config    `extracted` / `no-manifests` / `not-attempted`
    git       `commits` / `no-commits` / `not-attempted` / `git-failed`
    live      `off` / `no-context` / `no-namespace` / `ran` / `failed`

A brief that says `locate: no-match (examined 4,812 files under 2 roots)` is a
finding. A brief that says `locate: not-searched (no repo root was readable)` is
a failure wearing the same clothes, and the exit code separates them too: rc 3
means nothing could be examined, never "the service does not exist".

🔴 STDLIB ONLY. It runs under whatever `python3` the operator's session has, not
under the test env — `pyyaml` is NOT importable there (measured). So the config
step is a KNOB EXTRACTOR, not a YAML parser: it tracks indentation to build a
dotted path per scalar and selects the load-bearing suffixes. It will not
understand anchors, flow mappings or block scalars, and says so in its own
output rather than pretending to be a parser.

🔴 SECRET VALUES NEVER REACH THE BRIEF. The skill's rule is "mounted secrets
(names only — never print secret contents)". Enforced twice over: a value whose
key looks like a credential is redacted wherever it appears, and EVERY value in a
`kind: Secret` document is redacted regardless of its key.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace as _dc_replace
from pathlib import Path
from typing import Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subsystem_recall import (  # noqa: E402
    NUANCE_HEADING,
    POINTERS_HEADING,
    STATUS_PRECEDENCE,
    WHAT_HEADING,
    RecallReport,
    StoreMissingError,
    recall,
)

#: 🔴 TAKEN FROM THE READER, NEVER SPELLED. `recall` returns `"recalled"` on a
#: hit — not `"ok"` — and a literal here that guessed wrong would classify EVERY
#: hit as a miss, rendering `nothing recorded under that ref yet` over a real
#: entry. Deriving it from `STATUS_PRECEDENCE` means a rename over there fails
#: HERE (KeyError at import) instead of turning into a silent zero.
RECALLED_STATUS = STATUS_PRECEDENCE[STATUS_PRECEDENCE.index("recalled")]
from subsystem_resolver import normalize_ref  # noqa: E402
from subsystem_touch import DEFAULT_STORE_ROOT, scope_for_repo  # noqa: E402

__all__ = [
    "RECALLED_STATUS",
    "ENV_ROOT_HANDLES",
    "SKIP_DIRS",
    "MANIFEST_SUFFIXES",
    "MANIFEST_RANK",
    "MANIFEST_RANK_DEFAULT",
    "KNOB_SUFFIXES",
    "SECRETY_SUFFIXES",
    "SECRET_IDENTITY_PATHS",
    "SECRET_DATA_PREFIXES",
    "REDACTED",
    "MOVED_RE",
    "DEFAULT_FILE_LIMIT",
    "DEFAULT_LOG_LIMIT",
    "DEFAULT_KNOBS_PER_FILE",
    "PATHSPEC_SHOWN",
    "UMBRELLA_PATHS",
    "THIN_OWNERSHIP_MARGIN",
    "CWD_ORIGIN",
    "OWNER_BASIS",
    "UNLOCATED_BASIS",
    "MAX_VALUE_CHARS",
    "WALK_FILE_CAP",
    "LIVE_TIMEOUT_SECONDS",
    "EXIT_OK",
    "EXIT_USAGE",
    "EXIT_NOTHING_EXAMINED",
    "RootScan",
    "LocateResult",
    "IndexResult",
    "Knob",
    "ManifestKnobs",
    "ConfigResult",
    "Commit",
    "GitResult",
    "LiveProbe",
    "LiveResult",
    "Brief",
    "search_roots",
    "scan_root",
    "locate",
    "thin_runner_up",
    "index_scopes",
    "read_index",
    "dotted_paths",
    "extract_knobs",
    "config_for",
    "git_log",
    "live_state",
    "recon",
    "render_brief",
    "brief_json",
    "main",
]

# --- What a root is ------------------------------------------------------------

#: 🔴 NO CLIENT PATH IS HARDCODED HERE. devrc is PUBLIC, and the infra repos this
#: command reads are client work. The roots come from the env handles `.zshenv`
#: already exports (CLAUDE.md → "Canonical env handles"), each existence-guarded
#: on the host that has that checkout, plus `--repo` and the cwd's own toplevel.
#: A host where none of them is set searches NOTHING and says so (rc 3) — which
#: is the honest answer, and the reason the alternative (baking in a default
#: path) is worse than useless: it would report `no-match` for a directory that
#: does not exist on this machine.
ENV_ROOT_HANDLES: tuple[str, ...] = ("HOMELAB", "DATAPACKET")

#: Directories never descended. `.git` alone is most of the file count in a
#: gitops repo, and none of these can hold a manifest anyone edits.
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".direnv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".terraform", "vendor", ".venv", "venv", ".cache",
})

#: 🔴 A NESTED CHECKOUT IS PRUNED, AND THIS IS NOT AN OPTIMISATION. Measured on
#: the first real run: two agent worktrees under `<repo>/.claude/worktrees/`
#: turned 30 genuine matches into 85, and the top of the located list was three
#: COPIES of the same file at three different commits — a brief that reads as
#: "this service is deployed in three places" and is simply false.
#:
#: The rule is structural rather than a name ledger (`.claude/worktrees` would
#: have covered that one case and missed submodules, vendored clones and a
#: manually-placed `git worktree add`): a directory holding a `.git` entry — dir
#: OR file, since a worktree's `.git` is a FILE holding `gitdir:` — is a
#: DIFFERENT repository, and its contents are not this repo's. The root itself is
#: exempt, or nothing would ever be walked.
def _is_nested_checkout(d: Path) -> bool:
    return (d / ".git").exists()

#: A located file is a MANIFEST if it carries one of these. Everything else that
#: matched the service token is still reported as a hit (it locates the thing),
#: but only manifests are opened for knobs.
MANIFEST_SUFFIXES: frozenset[str] = frozenset({".yaml", ".yml"})

#: 🔴 A SUFFIX LEDGER, NOT A PATH LEDGER, and deliberately so. The same knob sits
#: at `spec.replicas` on a Deployment, `spec.values.replicaCount` on a
#: HelmRelease and `replicaCount` in a bare values file; pinning full paths would
#: cover the shape the author happened to be looking at and silently miss the
#: other two. Matching the LAST dotted segment covers all three, at the cost of
#: the occasional unrelated `name:` — which is why the brief prints the dotted
#: path beside every value rather than the bare key.
KNOB_SUFFIXES: frozenset[str] = frozenset({
    # identity
    "kind", "namespace",
    # version / provenance — the single most-asked recon question
    "image", "tag", "chart", "version", "targetrevision", "revision", "digest",
    # scale
    "replicas", "replicacount", "minreplicas", "maxreplicas",
    # resources
    "cpu", "memory", "storage", "size", "storageclassname",
    # wiring
    # 🔴 `url`/`uri`/`endpoint` are SELECTED here on purpose, and their exclusion
    # was a live bug: they are not in `SECRETY_SUFFIXES` (a chart repo URL is
    # load-bearing provenance), so if they were not in THIS set either, the pair
    # was never selected and redaction reason 3 — the userinfo-URL check — could
    # never execute on any input. A guard that cannot be reached is not a guard;
    # `test_reason_3_a_userinfo_URL_under_an_ordinary_key` is what found it.
    "port", "targetport", "containerport", "nodeport", "host", "hostname",
    "url", "uri", "endpoint", "server", "address",
    "classname", "ingressclassname", "type", "interval", "timeout",
    # dependencies + secrets (NAMES only — see SECRETY_SUFFIXES)
    "secretname", "secretref", "configmapref", "claimname", "serviceaccountname",
})

#: 🔴 The redaction ledger — REASON 1 of the two in `_redact`. A value under any
#: of these keys is replaced, wherever it appears and whatever the document kind.
#: The skill's rule is "mounted secrets (names only — never print secret
#: contents)", and this repo is PUBLIC: a brief pasted into a PR is the realistic
#: leak, not a hostile one.
#:
#: ⚠ IT IS DELIBERATELY NARROW, because reason 2 (a `kind: Secret` document
#: redacts wholesale) is the workhorse and a wide ledger here costs real recon
#: signal. Measured on the first real run: `key`, `url` and `secret` in this set
#: redacted `secretKeyRef.key` (a key NAME, the pointer you want), a
#: HelmRepository's chart `url` (load-bearing provenance) and `redisSecret.key`
#: — three redactions that hid nothing sensitive and removed the answer.
#: `secretname`/`secretref`/`claimname` are NOT here for the same reason: those
#: ARE names, and naming them is the whole point of the pointer.
SECRETY_SUFFIXES: frozenset[str] = frozenset({
    "password", "passwd", "token", "apikey", "api-key", "credential",
    "credentials", "privatekey", "clientsecret", "sessionsecret",
    "cert", "certificate", "dsn", "connectionstring", "webhook", "webhookurl",
})

#: 🔴 REASON 3: a URL carrying USERINFO. `url:` is not a secret key — a chart
#: repo URL is exactly what recon wants — but `postgres://user:pw@host/db` in a
#: plain `url:` is a credential in the clear, and no key-name ledger can see it
#: because the key is called `url`. So the VALUE is inspected, not just its key.
_USERINFO_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/@\s]*:[^/@\s]+@")

REDACTED = "<redacted>"

#: 🔴 THE IDENTITY OF A SECRET IS NOT ITS CONTENT, and conflating them made the
#: brief useless for the exact case the skill cares about. `/analyze-service`
#: asks for "mounted secrets (names only)" — so under reason 2 these five paths
#: print, and everything else in the document is redacted. Without this, the
#: measured brief rendered `metadata.name = <redacted>`: the pointer the operator
#: wanted, withheld, while the surrounding `sops.*` bookkeeping filled the block.
SECRET_IDENTITY_PATHS: frozenset[str] = frozenset({
    "kind", "apiversion", "metadata.name", "metadata.namespace", "type",
})

#: Inside a `kind: Secret`, only identity + the actual data KEYS are worth a line.
#: A SOPS-encrypted file carries a dozen `sops.*` bookkeeping fields whose values
#: are all redacted anyway — twelve lines of `<redacted>` that answer nothing.
SECRET_DATA_PREFIXES: tuple[str, ...] = ("data.", "stringdata.")

#: A commit worth flagging: a revert or a version move is usually WHY someone is
#: looking. Anchored on the conventional-commit subject shapes this fleet writes.
MOVED_RE = re.compile(
    r"\b(revert|reverts|reverted|rollback|roll back|bump|upgrade|downgrade|pin|unpin)\b",
    re.IGNORECASE,
)

#: 🔴 THESE ARE A CONTEXT BUDGET, NOT TASTE. The whole point of this module is
#: that the measured hand-run recon spent ~35.5 KB of tool output; a brief that
#: prints everything it finds has moved the cost, not removed it. Measured on a
#: real gitops repo at the first draft's limits (12 files × 14 knobs), one brief
#: rendered ~8 KB — a fifth of the cost it replaces, in ONE call, but still more
#: than a reader needs before choosing where to look. At 8 × 10 the same brief is
#: ~3 KB and every cut is COUNTED and reachable with a flag.
DEFAULT_FILE_LIMIT = 8
DEFAULT_LOG_LIMIT = 8
DEFAULT_KNOBS_PER_FILE = 10

#: The `git log` pathspec can be dozens of directories. It is INFORMATION about
#: the query, not the answer, so the brief names a few and counts the rest.
PATHSPEC_SHOWN = 3

#: At or above this many distinct directories, the name is an UMBRELLA and the
#: brief says so. 3 rather than 2: one service plus its secrets directory is the
#: ordinary two-directory shape and is not an umbrella.
UMBRELLA_PATHS = 3

#: 🔴 A LEAD THIS THIN IS A RANKING, NOT A FINDING. `locate` orders the roots by
#: how many paths carry the token and prints the winner as `lives at:` — a
#: sentence every reader takes as ownership. Two roots whose counts differ by a
#: hair have established no such thing; that difference is one directory naming
#: convention, or one repo that happens to keep more handoff docs. ONE number
#: drives two behaviours, deliberately, so there is one rule to reason about:
#:
#:   1. `lives at:` NAMES THE RUNNER-UP when the lead is under it, so the claim
#:      carries its own scope instead of reading as settled.
#:   2. THE CWD'S ROOT IS ASKED FIRST when it is within this of the leader. The
#:      index is asked at every root either way (see `index_scopes`), so this
#:      only decides which scope wins when TWO of them carry an entry — and when
#:      the path evidence cannot separate two repos, the one the human is
#:      standing in is the better guess.
#:
#: A POLICY value, not a measurement: nothing observed is restated here, and
#: `thin_runner_up` computes the margin from the scan at run time.
THIN_OWNERSHIP_MARGIN = 0.20

#: The `origin` `search_roots` stamps on the cwd's own toplevel. Spelled ONCE:
#: the producer and the two consumers below must agree, and a literal that
#: drifted would silently disable the cwd tie-break with no error anywhere.
CWD_ORIGIN = "cwd"

#: The basis string for the located owner. Also spelled once — `render_brief`
#: suppresses the `[scope via …]` marker on exactly this value, so a mismatch
#: would print the fallback marker over every ordinary hit.
OWNER_BASIS = "owning repo"

#: The basis for a root that was searched and matched NOTHING. Pinned by
#: `test_a_fallback_hit_says_SO_rather_than_implying_ownership`.
UNLOCATED_BASIS = "searched root (nothing located)"

#: Values are truncated, and a truncation is MARKED (`…`). An un-truncated brief
#: is one embedded cert away from being the dump this module exists to replace.
MAX_VALUE_CHARS = 96

#: 🔴 A WALK BUDGET, AND IT IS LOUD. A mistyped root (`/`) would otherwise walk
#: the filesystem until the session gave up. On the cap the scan STOPS and the
#: root's status becomes `walk-capped`, which is reported as a NON-answer — never
#: folded into `no-match`, because a capped walk did not finish looking.
WALK_FILE_CAP = 400_000

LIVE_TIMEOUT_SECONDS = 20

EXIT_OK = 0
EXIT_USAGE = 2
#: 🔴 Not "the service was not found". It means NO ROOT COULD BE EXAMINED, so the
#: run produced no evidence in either direction. A caller that branches on rc
#: must be able to tell those apart; a caller that does not still sees the
#: `not-searched` status in the brief.
EXIT_NOTHING_EXAMINED = 3


# --- Results -------------------------------------------------------------------


@dataclass(frozen=True)
class RootScan:
    """One search root, with the denominator its zero needs."""

    path: str
    status: str
    """`searched` | `absent` | `not-a-directory` | `walk-failed` | `walk-capped`."""
    origin: str
    """`--repo` | `env:<NAME>` | `cwd` — where the root came from, so a surprising
    root set is diagnosable without re-deriving it."""
    method: str = "walk"
    """🔴 HOW it looked, printed beside WHAT it found — `git-ls-files` or `walk`.

    The two see different file sets, so a match count means different things
    under each, and a reader who cannot tell them apart cannot read the zero.
    """
    files_examined: int = 0
    matches: tuple[str, ...] = ()
    """Repo-relative paths, sorted. Manifests first is a RENDER choice, not this."""
    detail: str = ""

    @property
    def searched(self) -> bool:
        return self.status == "searched"


@dataclass(frozen=True)
class LocateResult:
    status: str
    """`hits` | `no-match` | `not-searched`."""
    token: str
    roots: tuple[RootScan, ...] = ()
    owner: str | None = None
    """Absolute path of the root holding the most matches, or None."""
    owner_tied_with: tuple[str, ...] = ()
    """🔴 A TIE IS REPORTED, NEVER BROKEN SILENTLY. The skill's own rule: "if
    ambiguous, search both and say which repo owns it"."""

    @property
    def files_examined(self) -> int:
        return sum(r.files_examined for r in self.roots)

    @property
    def total_matches(self) -> int:
        return sum(len(r.matches) for r in self.roots)


@dataclass(frozen=True)
class IndexResult:
    status: str
    """recall's own status, or `not-attempted` / `store-missing`."""
    scope: str | None = None
    ref: str | None = None
    what: str = ""
    """`## What it is` — the entry's own answer to "what IS this thing".

    🔴 FIRST IN THE BLOCK, AND IT IS THE FIELD THE BRIEF WAS MISSING. Until
    2026-08-21 no BRIEFING path printed this section — not `subsystem_recall
    --ref`, not its digest, not this `index:` block. (`subsystem_recall.search`
    always surfaced it, but only for an entry a query happened to match, so it is
    not a path anyone gets briefed on.) A controlled A/B on 2026-08-20 found an
    agent briefed only on an `index:` block could not say what the service was,
    where it lived or what it owned, because `pointers` and `nuance` both assume
    the reader has already identified the thing. The content was on disk in 73 of
    73 entries the whole time.
    """
    pointers: str = ""
    nuance: str = ""
    candidates: tuple[str, ...] = ()
    sensitivity: str | None = None
    basis: str = ""
    """🔴 WHICH REPO THE SCOPE CAME FROM, in words — `owning repo`, `the cwd repo
    (N paths matched)`, `a searched root (N paths matched)` or
    `searched root (nothing located)`. Printed, never implied: a hit found
    anywhere but the owner is answering about a scope the locate step did NOT
    confirm owns the service, and a reader must be able to see that."""
    scopes_checked: tuple[str, ...] = ()
    """🔴 EVERY SCOPE THIS LOOKUP ASKED, in the order asked, de-duplicated.

    A `ref-absent` is a claim of the form "nothing recorded ANYWHERE", and until
    this field exists the reader cannot tell it apart from "nothing recorded in
    the one scope I happened to ask". The render prints the count beside the
    names, so the claim carries its own denominator (`claude/RULES.md` → never a
    silent zero)."""
    detail: str = ""
    report: RecallReport | None = None


@dataclass(frozen=True)
class Knob:
    path: str
    value: str
    redacted: bool = False


@dataclass(frozen=True)
class ManifestKnobs:
    file: str
    knobs: tuple[Knob, ...]
    truncated: int = 0
    detail: str = ""


@dataclass(frozen=True)
class ConfigResult:
    status: str
    """`extracted` | `no-manifests` | `not-attempted`."""
    manifests_seen: int = 0
    manifests_read: int = 0
    files: tuple[ManifestKnobs, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str

    @property
    def moved(self) -> bool:
        return bool(MOVED_RE.search(self.subject))


@dataclass(frozen=True)
class GitResult:
    status: str
    """`commits` | `no-commits` | `not-attempted` | `git-failed`."""
    repo: str | None = None
    pathspec: tuple[str, ...] = ()
    commits: tuple[Commit, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class LiveProbe:
    argv: tuple[str, ...]
    rc: int
    out: str


@dataclass(frozen=True)
class LiveResult:
    status: str
    """`off` | `no-context` | `no-namespace` | `ran` | `failed`."""
    context: str | None = None
    namespace: str | None = None
    probes: tuple[LiveProbe, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class Brief:
    service: str
    token: str
    locate: LocateResult
    index: IndexResult
    config: ConfigResult
    git: GitResult
    live: LiveResult
    store_root: str = ""
    notes: tuple[str, ...] = field(default=())

    @property
    def exit_code(self) -> int:
        return EXIT_NOTHING_EXAMINED if self.locate.status == "not-searched" else EXIT_OK


# --- Roots ---------------------------------------------------------------------


def search_roots(
    explicit: Sequence[str] = (),
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> tuple[tuple[str, str], ...]:
    """`(absolute path, origin)` pairs, de-duplicated, order-stable.

    INJECTED env and cwd, not read from the process — the selection rule is then
    pinnable without setting environment variables in a test, which is how the
    "it worked on my host" class of root bug gets in.

    A root is emitted even when it does not exist: `scan_root` is what classifies
    it, and dropping it here would turn a configured-but-absent checkout into a
    root nobody ever mentions. The whole point is that the brief can say WHICH
    roots it did not look at.
    """
    env = os.environ.copy() if env is None else env
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(raw: str, origin: str) -> None:
        if not raw or not raw.strip():
            return
        p = str(Path(raw).expanduser())
        if p in seen:
            return
        seen.add(p)
        out.append((p, origin))

    for raw in explicit:
        add(raw, "--repo")
    if not explicit:
        for handle in ENV_ROOT_HANDLES:
            add(env.get(handle, ""), f"env:{handle}")
        add(str(cwd) if cwd is not None else "", CWD_ORIGIN)
    return tuple(out)


def _walk(root: Path) -> Iterable[tuple[Path, str]]:
    """(absolute file, repo-relative posix path) under `root`.

    Prunes `SKIP_DIRS` by name and NESTED CHECKOUTS structurally — see
    `_is_nested_checkout` for why the second one is not an optimisation.
    """
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not _is_nested_checkout(here / d)
        )
        for name in sorted(filenames):
            full = here / name
            yield full, full.relative_to(root).as_posix()


def _path_matches(rel: str, token: str) -> bool:
    """Does any path COMPONENT carry the normalized token?

    🔴 COMPONENT-WISE, NEVER OVER THE JOINED PATH — and the hazard is specific,
    not general nervousness about substrings. `normalize_ref` maps `/` to `-`, so
    the one-line version anyone would write (`token in normalize_ref(rel)`) lets
    a HYPHENATED token span directories: `apps/external/dns.yaml` normalizes to
    `apps-external-dns.yaml`, which contains `external-dns` while the path has
    nothing to do with it. Each component is normalized SEPARATELY with the
    STORE's own `normalize_ref`, so `external-dns`, `external_dns` and
    `externalDNS` are one token here exactly as they are one entry there.
    """
    for part in rel.split("/"):
        norm = normalize_ref(part)
        if not norm:
            continue
        if token in norm:
            return True
        stem = normalize_ref(part.rsplit(".", 1)[0]) if "." in part else ""
        if stem and token in stem:
            return True
    return False


def _tracked_files(root: Path, *, runner=None) -> tuple[str, ...] | None:
    """Every file git TRACKS under `root`, or None when `root` is not a repo.

    🔴 THE TRACKED SET IS THE RIGHT UNIVERSE, and the walk is the fallback rather
    than the other way round. Measured on the first real run against a gitops
    repo, the walk's match list was topped by
    `.kube/cache/discovery/<ip>_6443/…/serverresources.json` — a kubectl DISCOVERY
    CACHE. Three things wrong with it at once: it is not config (so it answers
    nothing), it is not tracked (so it exists on one host and not the other, and
    the same recon gives two answers), and it carries a real cluster IP into a
    brief that gets pasted around. `git ls-files` excludes ignored files, cache
    directories and other repos' worktrees by construction — no name ledger of
    junk directories to maintain, and none to fall behind.
    """
    run = runner or _run
    rc, out = run(["git", "-C", str(root), "ls-files", "-z"], timeout=60)
    if rc != 0:
        return None
    return tuple(sorted(p for p in out.split("\0") if p))


def scan_root(
    root: str | Path, token: str, *, file_cap: int = WALK_FILE_CAP, runner=None
) -> RootScan:
    """Classify one root and collect its matches. READ-ONLY: `git ls-files` or `os.walk`."""
    path = Path(root)
    origin = ""  # filled by the caller; kept off this function's contract
    if not path.exists():
        return RootScan(str(path), "absent", origin, detail="no such path on this host")
    if not path.is_dir():
        return RootScan(str(path), "not-a-directory", origin)

    tracked = _tracked_files(path, runner=runner)
    if tracked is not None:
        matches = tuple(r for r in tracked if _path_matches(r, token))
        return RootScan(str(path), "searched", origin, method="git-ls-files",
                        files_examined=len(tracked), matches=matches)

    examined = 0
    hits: list[str] = []
    try:
        for _full, rel in _walk(path):
            examined += 1
            if examined > file_cap:
                return RootScan(
                    str(path), "walk-capped", origin, method="walk",
                    files_examined=examined - 1, matches=tuple(sorted(hits)),
                    detail=f"stopped at the {file_cap}-file cap — the walk did NOT finish",
                )
            if _path_matches(rel, token):
                hits.append(rel)
    except OSError as exc:
        return RootScan(
            str(path), "walk-failed", origin, method="walk", files_examined=examined,
            matches=tuple(sorted(hits)), detail=str(exc),
        )
    return RootScan(str(path), "searched", origin, method="walk",
                    files_examined=examined, matches=tuple(sorted(hits)))


def locate(
    service: str,
    roots: Sequence[tuple[str, str]],
    *,
    file_cap: int = WALK_FILE_CAP,
    runner=None,
) -> LocateResult:
    """Where does `service` live? One walk per root, with per-root accounting."""
    token = normalize_ref(service)
    if not token:
        # 🔴 A ref that normalizes away is NOT a miss — nothing was ever asked.
        # `normalize_ref("---")` is "", and searching for "" matches every path.
        return LocateResult("not-searched", token, ())

    scans: list[RootScan] = []
    for path, origin in roots:
        s = scan_root(path, token, file_cap=file_cap, runner=runner)
        scans.append(RootScan(s.path, s.status, origin, s.method,
                              s.files_examined, s.matches, s.detail))

    if not any(s.searched for s in scans):
        return LocateResult("not-searched", token, tuple(scans))

    ranked = [s for s in _rank(scans) if s.matches]
    if not ranked:
        return LocateResult("no-match", token, tuple(scans))

    top = len(ranked[0].matches)
    tied = tuple(s.path for s in ranked[1:] if len(s.matches) == top)
    return LocateResult("hits", token, tuple(scans), owner=ranked[0].path, owner_tied_with=tied)


# --- Ranking the roots ---------------------------------------------------------


def _n_paths(n: int) -> str:
    """`1 path` / `N paths` — the basis string is read by a human."""
    return f"{n} path" if n == 1 else f"{n} paths"


def _rank(scans: Iterable[RootScan]) -> list[RootScan]:
    """Searched roots, most matches first, ties broken by path.

    🔴 THE KEY IS TOTAL AND CONTENT-DERIVED, so the order does not depend on the
    order the roots were configured in. `locate`, `index_scopes` and
    `thin_runner_up` all rank through here rather than each spelling the key —
    three copies of a sort key is three chances for the brief to name one root as
    the owner and ask a different one for its index entry.
    """
    return sorted((s for s in scans if s.searched), key=lambda s: (-len(s.matches), s.path))


def thin_runner_up(loc: LocateResult) -> tuple[RootScan, int] | None:
    """`(runner-up root, the winner's lead in percent)` when that lead is under
    `THIN_OWNERSHIP_MARGIN`, else None.

    🔴 AN EXACT TIE RETURNS None, and that is not an oversight. A tie already has
    its own `OWNERSHIP IS TIED` note; reporting it here as well would print the
    same finding twice in one brief, and the two wordings would then have to be
    kept in agreement forever. The two cases are mutually exclusive by
    construction: `second >= top` leaves here, `second == top` is the tie.
    """
    ranked = [s for s in _rank(loc.roots) if s.matches]
    if len(ranked) < 2:
        return None
    top, second = len(ranked[0].matches), len(ranked[1].matches)
    if top <= 0 or second >= top:
        return None
    lead = (top - second) / top
    if lead >= THIN_OWNERSHIP_MARGIN:
        return None
    return ranked[1], round(lead * 100)


# --- The index read ------------------------------------------------------------


def index_scopes(loc: LocateResult) -> tuple[tuple[str, str], ...]:
    """`(repo, basis)` pairs to ask the index about, in priority order.

    🔴 THE INDEX MUST NOT BE GATED ON `locate` SUCCEEDING, and the first version
    of this module got that backwards — it derived the scope from `loc.owner` and
    reported `not-attempted` whenever nothing matched. Measured against the real
    store: a service that matched no path component in its repo made the run say
    "no owning repo located to derive a scope from" over a scope whose entries
    were sitting right there. That is precisely inverted — a curated pointer
    sheet is worth MOST when the path heuristic missed, because "where does this
    live?" is the question the index can answer and the matcher just failed.

    🔴 AND THE SECOND VERSION GOT THE OTHER HALF WRONG, which is why this asks
    EVERY searched root rather than just the owner. The fallback above fired only
    when `loc.owner is None` — it covered the case where locate found NOTHING and
    left uncovered the case where locate found the WRONG thing. Reproduced on a
    real run: a four-path margin between two repos handed the lookup to the
    loser's neighbour and rendered `ref-absent` over a curated entry in the cwd's
    own scope. That is worse than a wasted read — `claude/skills/analyze-service`
    tells the agent to OMIT the pointer/nuance block when the index missed, so
    the curated knowledge is silently dropped, and the next `/handoff` in that
    repo sees `ref-absent` and may write a DUPLICATE entry into the wrong scope.
    A miss here is a store WRITE, not just a bad read.

    Reads are local and the whole store answers in tens of milliseconds, so there
    is nothing to buy by asking one scope. Every searched root is asked, ranked
    by match count, and `read_index` takes the FIRST HIT — so a lower-ranked
    scope can only ever answer a question the higher-ranked ones did not.

    The cwd's root is promoted to the front when it is within
    `THIN_OWNERSHIP_MARGIN` of the leader: that only changes which scope wins
    when more than one carries an entry, and the human's own repo is the better
    guess when the path evidence cannot separate them. `--repo` roots carry no
    `cwd` origin, so an explicit root set is never reordered.

    The basis is carried and printed, never implied.
    """
    ranked = _rank(loc.roots)
    if not ranked:
        return ()

    top = len(ranked[0].matches)
    for i, s in enumerate(ranked):
        if s.origin != CWD_ORIGIN:
            continue
        if i:
            lead = (top - len(s.matches)) / top if top else 0.0
            if lead < THIN_OWNERSHIP_MARGIN:
                ranked.insert(0, ranked.pop(i))
        break

    out: list[tuple[str, str]] = []
    for s in ranked:
        if s.path == loc.owner:
            basis = OWNER_BASIS
        elif not s.matches:
            basis = UNLOCATED_BASIS
        elif s.origin == CWD_ORIGIN:
            basis = f"the cwd repo ({_n_paths(len(s.matches))} matched)"
        else:
            basis = f"a searched root ({_n_paths(len(s.matches))} matched)"
        out.append((s.path, basis))
    return tuple(out)


def read_index(
    loc: LocateResult | str | None,
    service: str,
    *,
    store_root: str | Path = DEFAULT_STORE_ROOT,
) -> IndexResult:
    """Front-load the curated recall — through `subsystem_recall`, not a `cat`.

    🔴 THE POINT OF THE INDIRECTION. The measured recon shelled out to
    `cat ~/.claude/analyze-service-index/<scope>/<slug>.md`, which: cannot resolve
    an alias, cannot detect an ambiguous ref (it picks whichever filename was
    typed, or misses), does not fold `sensitivity:` fail-safe, and returned 11 KB
    in one observed call. `recall` answers all four, and it is the SAME call
    `/resume` makes — one reader, not two.

    Accepts a `LocateResult` (the real caller) or a bare repo path (tests and any
    caller that already knows the repo).
    """
    if isinstance(loc, LocateResult):
        candidates = index_scopes(loc)
        if not candidates:
            return IndexResult(
                "not-attempted",
                detail="no root could be examined, so no scope could be derived",
            )
        # Ask each candidate; the FIRST hit wins and the rest are reported only
        # if none hit — so a lower-ranked scope can never silently shadow a real
        # answer, and the winner always carries the basis it was reached by.
        #
        # 🔴 DE-DUPLICATED BY SCOPE, NOT BY PATH. Two roots routinely derive to
        # ONE scope — a worktree and its base clone are different directories and
        # `scope_for_repo` maps both through `--git-common-dir` to the same name.
        # Without this the store is read twice for the same answer and, worse,
        # `checked 3 scope(s)` would name a scope twice and overstate how wide
        # the search actually was.
        misses: list[IndexResult] = []
        seen: set[str] = set()
        checked: list[str] = []
        for repo, basis in candidates:
            scope, failure = _scope_of(repo)
            key = scope if scope is not None else f"\0path:{repo}"
            if key in seen:
                continue
            seen.add(key)
            if failure is not None:
                misses.append(failure)
                continue
            checked.append(scope or "")
            got = _read_index_one(repo, service, store_root=store_root,
                                  basis=basis, scope=scope)
            if got.status in ("hit", "ref-ambiguous"):
                return _dc_replace(got, scopes_checked=tuple(checked))
            misses.append(got)
        if not misses:  # pragma: no cover — `candidates` non-empty implies one
            return IndexResult("not-attempted", detail="no candidate scope was reachable")
        # The primary miss is the first one that actually REACHED a scope. A root
        # git cannot name (a plain directory searched by the walk) yields
        # `not-attempted`, which says nothing about the store; letting it outrank
        # a genuine `ref-absent` would replace a measurement with a shrug.
        primary = next((m for m in misses if m.scope), misses[0])
        return _dc_replace(primary, scopes_checked=tuple(checked))
    if loc is None:
        return IndexResult("not-attempted", detail="no repo given to derive a scope from")
    got = _read_index_one(loc, service, store_root=store_root, basis=OWNER_BASIS)
    return _dc_replace(got, scopes_checked=(got.scope,) if got.scope else ())


def _scope_of(repo: str | Path) -> tuple[str | None, IndexResult | None]:
    """`(scope, failure)` — exactly one is not None.

    Extracted so the candidate loop can de-duplicate BEFORE paying for a recall,
    without re-spelling the failure wording that `_read_index_one` reports.
    """
    try:
        return scope_for_repo(repo), None
    except Exception as exc:  # a non-repo root, or git unavailable
        return None, IndexResult(
            "not-attempted", detail=f"scope not derivable from {repo}: {exc}")


def _read_index_one(
    owner: str,
    service: str,
    *,
    store_root: str | Path = DEFAULT_STORE_ROOT,
    basis: str = OWNER_BASIS,
    scope: str | None = None,
) -> IndexResult:
    """One scope's answer. Split out so the multi-candidate loop above has a
    single per-scope predicate rather than a copy of it per branch.

    `scope` is accepted already-derived: the loop needs it to de-duplicate, and
    deriving it twice would run `git rev-parse` twice per candidate.
    """
    if scope is None:
        scope, failure = _scope_of(owner)
        if failure is not None:
            return failure

    try:
        rep = recall(store_root, scope, ref=service)
    except StoreMissingError as exc:
        return IndexResult("store-missing", scope=scope, ref=normalize_ref(service),
                           basis=basis, detail=str(exc))
    except Exception as exc:
        return IndexResult("store-unreadable", scope=scope, ref=normalize_ref(service),
                           basis=basis, detail=str(exc))

    ref = normalize_ref(service)
    if rep.status == "ref-ambiguous":
        cands = tuple(getattr(rep, "candidates", ()) or ())
        return IndexResult("ref-ambiguous", scope=scope, ref=ref, candidates=cands, report=rep,
                           basis=basis, detail="the ref names more than one entry — pick one, never guess")

    # 🔴 `RECALLED_STATUS` is imported, not spelled. A literal `"ok"` here would
    # have matched nothing recall ever returns and every hit would have rendered
    # as `nothing recorded under that ref yet` — a silent miss with the exact
    # shape the store's own docs warn about.
    if rep.status != RECALLED_STATUS or not rep.entries:
        return IndexResult(rep.status, scope=scope, ref=ref, report=rep, basis=basis,
                           detail="nothing recorded under that ref yet")

    hit = rep.entries[0]
    sections = hit.sections or {}
    return IndexResult(
        "hit",
        scope=scope,
        ref=hit.ref,
        what=(sections.get(WHAT_HEADING) or "").strip(),
        pointers=(sections.get(POINTERS_HEADING) or "").strip(),
        nuance=(sections.get(NUANCE_HEADING) or "").strip(),
        sensitivity=hit.sensitivity,
        basis=basis,
        report=rep,
    )


# --- Config knobs --------------------------------------------------------------

_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s+)?(?P<key>[A-Za-z0-9_.\-/\"']+)\s*:(?P<rest>.*)$")
_DOC_RE = re.compile(r"^---\s*$")


def dotted_paths(text: str) -> list[tuple[int, str, str]]:
    """`(document index, dotted key path, scalar value)` for every `key: value` line.

    🔴 A KNOB EXTRACTOR, NOT A YAML PARSER, and the difference is stated rather
    than discovered. It tracks INDENTATION to build the path; it does not
    understand anchors/aliases, flow mappings (`{a: 1}`), block scalars (`|`/`>`)
    or multi-line strings, and a line inside a block scalar that happens to look
    like `key: value` WILL be emitted. That is acceptable because every value is
    printed WITH its dotted path, so a nonsense path is visible as nonsense —
    and unacceptable to fix by importing a parser, because `pyyaml` is not
    importable from the python this runs under (measured; see the module
    docstring).
    """
    out: list[tuple[int, str, str]] = []
    stack: list[tuple[int, str]] = []
    doc = 0
    for raw in text.splitlines():
        if _DOC_RE.match(raw):
            doc += 1
            stack.clear()
            continue
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _KEY_RE.match(raw)
        if not m:
            continue
        indent = len(m.group("indent"))
        if m.group("dash"):
            # A list item's key sits deeper than the dash column.
            indent += len(m.group("dash"))
        key = m.group("key").strip("\"'")
        value = m.group("rest").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = ".".join([k for _, k in stack] + [key])
        if value and not value.startswith("#"):
            out.append((doc, path, value.strip("\"'")))
        stack.append((indent, key))
    return out


def _doc_kinds(pairs: Sequence[tuple[int, str, str]]) -> dict[int, str]:
    return {d: v for d, p, v in pairs if p == "kind"}


def _redact(path: str, value: str, *, secret_doc: bool) -> tuple[str, bool]:
    """🔴 THREE INDEPENDENT REASONS TO REDACT, and any one suffices.

    1. The KEY looks like a credential (`SECRETY_SUFFIXES`), wherever it sits.
    2. The DOCUMENT is a `kind: Secret` — then every value goes, whatever the key
       is called, because a Secret's data keys are arbitrary operator-chosen
       strings and no key-name ledger can enumerate them.
    3. The VALUE is a URL carrying userinfo (`_USERINFO_URL_RE`), whatever its
       key is called.

    Reason 2 exists because reason 1 alone was walkable by NAMING: a Secret whose
    key is `db-conn-2` matches nothing in the ledger and would print verbatim.
    Reason 3 exists because reasons 1 and 2 together are walkable by PLACEMENT:
    a `url:` on a plain Deployment env var is neither a secret-y key nor inside a
    Secret document, and `postgres://u:pw@h/db` prints in full.
    """
    last = path.rsplit(".", 1)[-1].lower()
    if secret_doc and path.lower() not in SECRET_IDENTITY_PATHS:
        return REDACTED, True
    if last in SECRETY_SUFFIXES:
        return REDACTED, True
    if _USERINFO_URL_RE.match(value):
        return REDACTED, True
    return value, False


def extract_knobs(
    text: str, *, limit: int = DEFAULT_KNOBS_PER_FILE
) -> tuple[tuple[Knob, ...], int]:
    """The load-bearing knobs of one manifest, plus how many were dropped."""
    pairs = dotted_paths(text)
    kinds = _doc_kinds(pairs)
    picked: list[Knob] = []
    for doc, path, value in pairs:
        low = path.lower()
        last = low.rsplit(".", 1)[-1]
        secret_doc = kinds.get(doc, "").strip() == "Secret"
        if secret_doc:
            if low not in SECRET_IDENTITY_PATHS and not low.startswith(SECRET_DATA_PREFIXES):
                continue
        elif last not in KNOB_SUFFIXES and last not in SECRETY_SUFFIXES:
            continue
        shown, red = _redact(path, value, secret_doc=secret_doc)
        if not red and len(shown) > MAX_VALUE_CHARS:
            shown = shown[:MAX_VALUE_CHARS] + "…"
        picked.append(Knob(path, shown, red))
    if len(picked) <= limit:
        return tuple(picked), 0
    return tuple(picked[:limit]), len(picked) - limit


#: 🔴 WHICH MANIFESTS GET READ WHEN THE CAP BITES. Alphabetical order is an
#: accident of naming, and it loses on the case that matters: measured on a real
#: 17-manifest service, `--files 8` alphabetically read `configmap`, `deployment`
#: and six others while `kustomization.yaml` — the file that says what the
#: service IS composed of — fell off the end at position 5-of-17-alphabetical on
#: a longer list. Rank by what answers a recon question, then alphabetically
#: within a rank so the order is still deterministic.
MANIFEST_RANK: tuple[tuple[str, int], ...] = (
    ("kustomization", 0),       # what the service is composed of
    ("helmrelease", 1),
    ("release", 1),
    ("helmrepository", 2),
    ("values", 2),              # the chart's knobs
    ("statefulset", 3),
    ("deployment", 3),
    ("daemonset", 3),
    ("cronjob", 3),
    ("ingress", 4),
    ("service", 4),
    ("configmap", 5),
)

#: Everything unranked sorts after every ranked name, never interleaved with it.
MANIFEST_RANK_DEFAULT = 6


def _manifest_rank(rel: str) -> tuple[int, str]:
    stem = Path(rel).name.lower()
    for token, rank in MANIFEST_RANK:
        if token in stem:
            return rank, rel
    return MANIFEST_RANK_DEFAULT, rel


def config_for(
    loc: LocateResult,
    *,
    file_limit: int = DEFAULT_FILE_LIMIT,
    knob_limit: int = DEFAULT_KNOBS_PER_FILE,
) -> ConfigResult:
    """Open the located MANIFESTS (only) and pull their knobs."""
    if loc.status == "not-searched":
        return ConfigResult("not-attempted", detail="nothing was searched, so nothing was opened")
    if loc.owner is None:
        return ConfigResult("no-manifests", detail="no path matched the service in any root")

    owner = Path(loc.owner)
    scan = next((s for s in loc.roots if s.path == loc.owner), None)
    rels = list(scan.matches) if scan else []
    manifests = sorted(
        (r for r in rels if Path(r).suffix.lower() in MANIFEST_SUFFIXES),
        key=_manifest_rank,
    )
    if not manifests:
        return ConfigResult(
            "no-manifests", manifests_seen=0,
            detail=(f"{len(rels)} path(s) matched under {owner.name} but none carried "
                    f"{'/'.join(sorted(MANIFEST_SUFFIXES))}"),
        )

    files: list[ManifestKnobs] = []
    for rel in manifests[:file_limit]:
        try:
            text = (owner / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            files.append(ManifestKnobs(rel, (), detail=f"unreadable: {exc}"))
            continue
        knobs, dropped = extract_knobs(text, limit=knob_limit)
        files.append(ManifestKnobs(rel, knobs, dropped))
    return ConfigResult("extracted", manifests_seen=len(manifests),
                        manifests_read=len(files), files=tuple(files))


# --- Recent changes ------------------------------------------------------------

_LOG_SEP = "\x1f"


def _run(argv: Sequence[str], *, cwd: str | Path | None = None, timeout: int = 30) -> tuple[int, str]:
    try:
        p = subprocess.run(
            list(argv), cwd=str(cwd) if cwd else None, capture_output=True,
            text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        return 127, f"{argv[0]}: not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 126, str(exc)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def git_log(loc: LocateResult, *, limit: int = DEFAULT_LOG_LIMIT) -> GitResult:
    """`git log` over the located directories — READ-ONLY, one invocation.

    The pathspec is the located files' DIRECTORIES, de-duplicated to the shallowest
    ancestor set, not the file list: a rename inside the service directory is
    exactly the change worth seeing, and a file-scoped pathspec loses it.
    """
    if loc.status == "not-searched":
        return GitResult("not-attempted", detail="nothing was searched, so no repo was chosen")
    if loc.owner is None:
        return GitResult("not-attempted", detail="no owning repo located")

    scan = next((s for s in loc.roots if s.path == loc.owner), None)
    if scan is None or not scan.matches:
        return GitResult("not-attempted", repo=loc.owner, detail="no matching path to scope the log to")

    dirs = sorted({str(Path(r).parent) for r in scan.matches if str(Path(r).parent) != "."})
    pruned: list[str] = []
    for d in dirs:
        if not any(d != o and d.startswith(o + "/") for o in dirs):
            pruned.append(d)
    pathspec = tuple(pruned) or tuple(scan.matches)

    rc, out = _run(
        ["git", "-C", loc.owner, "log", f"-{limit}",
         f"--pretty=format:%h{_LOG_SEP}%ad{_LOG_SEP}%s", "--date=short", "--", *pathspec],
    )
    if rc != 0:
        return GitResult("git-failed", repo=loc.owner, pathspec=pathspec,
                         detail=out.strip()[:400] or f"git exited {rc}")

    commits: list[Commit] = []
    for line in out.splitlines():
        parts = line.split(_LOG_SEP)
        if len(parts) == 3:
            commits.append(Commit(*(p.strip() for p in parts)))
    if not commits:
        return GitResult("no-commits", repo=loc.owner, pathspec=pathspec,
                         detail="the pathspec resolved but no commit touched it")
    return GitResult("commits", repo=loc.owner, pathspec=pathspec, commits=tuple(commits))


# --- Live state (OPT-IN) -------------------------------------------------------


def _namespace_from(cfg: ConfigResult) -> str | None:
    for f in cfg.files:
        for k in f.knobs:
            if k.path.rsplit(".", 1)[-1].lower() == "namespace" and not k.redacted:
                return k.value
    return None


def live_state(
    cfg: ConfigResult,
    *,
    enabled: bool = False,
    context: str | None = None,
    namespace: str | None = None,
    timeout: int = LIVE_TIMEOUT_SECONDS,
    runner=_run,
) -> LiveResult:
    """The 124-of-359 half, behind a flag.

    🔴 THE CONTEXT IS NEVER GUESSED. `--live` without `--context` is `no-context`,
    not "try the current kubeconfig": there is deliberately no default
    `KUBECONFIG` on these hosts (CLAUDE.md) precisely so a bare `kubectl` cannot
    reach prod, and a recon tool that re-invented one would undo that. Every
    probe is read-only (`get`), name-scoped, and carries its own request timeout.
    """
    if not enabled:
        return LiveResult("off", detail="static recon; pass --live --context <ctx> for cluster state")
    if not context:
        return LiveResult("no-context", detail="--live needs --context <ctx>; it is never inferred")
    ns = namespace or _namespace_from(cfg)
    if not ns:
        return LiveResult("no-namespace", context=context,
                          detail="no namespace: in the located manifests; pass --namespace")

    probes: list[LiveProbe] = []
    for argv in (
        ["kubectl", "--context", context, "-n", ns, "get", "pods,deploy,sts,svc",
         "--request-timeout", f"{timeout}s"],
        ["kubectl", "--context", context, "-n", ns, "get", "events",
         "--sort-by=.lastTimestamp", "--request-timeout", f"{timeout}s"],
        ["flux", "--context", context, "-n", ns, "get", "helmrelease"],
    ):
        rc, out = runner(argv, timeout=timeout)
        probes.append(LiveProbe(tuple(argv), rc, out.strip()))

    status = "ran" if any(p.rc == 0 for p in probes) else "failed"
    detail = "" if status == "ran" else "every probe failed — treat live state as UNVERIFIED"
    return LiveResult(status, context=context, namespace=ns, probes=tuple(probes), detail=detail)


# --- The one call --------------------------------------------------------------


def recon(
    service: str,
    *,
    repos: Sequence[str] = (),
    store_root: str | Path = DEFAULT_STORE_ROOT,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    live: bool = False,
    context: str | None = None,
    namespace: str | None = None,
    file_limit: int = DEFAULT_FILE_LIMIT,
    log_limit: int = DEFAULT_LOG_LIMIT,
    knob_limit: int = DEFAULT_KNOBS_PER_FILE,
    file_cap: int = WALK_FILE_CAP,
) -> Brief:
    """Locate → index → config → git log (→ live, opt-in). One process, one brief."""
    roots = search_roots(repos, env=env, cwd=cwd)
    loc = locate(service, roots, file_cap=file_cap)
    idx = read_index(loc, service, store_root=store_root)
    cfg = config_for(loc, file_limit=file_limit, knob_limit=knob_limit)
    log = git_log(loc, limit=log_limit)
    lv = live_state(cfg, enabled=live, context=context, namespace=namespace)

    notes: list[str] = []
    if loc.owner_tied_with:
        notes.append(
            "OWNERSHIP IS TIED — " + ", ".join([Path(loc.owner or "").name]
                                               + [Path(p).name for p in loc.owner_tied_with])
            + " matched equally; the brief below is scoped to the first."
        )
    capped = [r for r in loc.roots if r.status == "walk-capped"]
    if capped:
        notes.append(
            f"{len(capped)} root(s) hit the {file_cap}-file walk cap — those walks did NOT finish."
        )
    # 🔴 An UMBRELLA is a finding, not a defect — the skill's own schema says so
    # ("for an umbrella/multi-instance service enumerate the instances"). It is
    # called out because the git-log block below is then the UNION of N unrelated
    # directories, and a reader who thinks they are looking at one service reads
    # every one of those commits as being about it.
    if len(log.pathspec) >= UMBRELLA_PATHS:
        notes.append(
            f"MULTI-DIRECTORY — '{loc.token}' matched {len(log.pathspec)} distinct directories, "
            f"so `recent changes` below is their UNION rather than one service's history. "
            f"That is an umbrella service, a service split across app/chart/container "
            f"directories, or a name that is too broad — the located list says which."
        )
    return Brief(service=service, token=loc.token, locate=loc, index=idx, config=cfg,
                 git=log, live=lv, store_root=str(store_root), notes=tuple(notes))


# --- Rendering -----------------------------------------------------------------


def _root_line(r: RootScan) -> str:
    name = Path(r.path).name or r.path
    if r.status == "searched":
        return (f"  {name:22} searched via {r.method:<13} "
                f"{r.files_examined:>6} files examined, {len(r.matches):>4} matched  ({r.origin})")
    tail = f" — {r.detail}" if r.detail else ""
    return f"  {name:22} {r.status:<10} ({r.origin}){tail}"


def render_brief(b: Brief, *, file_limit: int = DEFAULT_FILE_LIMIT) -> str:
    """ONE compact brief. Every section names its denominator."""
    L: list[str] = []
    L.append(f"service: {b.service}   token: {b.token or '(empty after normalization)'}")
    for n in b.notes:
        L.append(f"⚠ {n}")

    # --- roots ---------------------------------------------------------------
    L.append("")
    L.append(f"roots ({len(b.locate.roots)}):")
    if not b.locate.roots:
        L.append("  NONE — no --repo given and no root env handle is set on this host")
    for r in b.locate.roots:
        L.append(_root_line(r))

    # --- locate --------------------------------------------------------------
    L.append("")
    if b.locate.status == "not-searched":
        L.append("lives at: NOT SEARCHED — no root could be examined. This is not a "
                 "finding about the service.")
    elif b.locate.status == "no-match":
        L.append(f"lives at: NO MATCH — {b.locate.files_examined} files examined across "
                 f"{sum(1 for r in b.locate.roots if r.searched)} root(s), 0 matched "
                 f"'{b.locate.token}'")
    else:
        owner = Path(b.locate.owner or "")
        scan = next((s for s in b.locate.roots if s.path == b.locate.owner), None)
        shown = list(scan.matches[:file_limit]) if scan else []
        L.append(f"lives at: {owner.name}  ({len(scan.matches) if scan else 0} paths matched, "
                 f"{b.locate.files_examined} files examined)")
        # 🔴 THE CLAIM CARRIES ITS OWN SCOPE. A lead of a few paths is a ranking,
        # not ownership, and it is printed HERE — beside the sentence the reader
        # believes — rather than only in a note at the top of the brief.
        thin = thin_runner_up(b.locate)
        if thin is not None:
            other, pct = thin
            L.append(f"  ⚠ THIN MARGIN — {Path(other.path).name} matched "
                     f"{_n_paths(len(other.matches))}, "
                     f"only {pct}% behind. That is a RANKING, not a finding of ownership; "
                     f"the index was asked in both.")
        for rel in shown:
            L.append(f"  {rel}")
        rest = (len(scan.matches) - len(shown)) if scan else 0
        if rest > 0:
            L.append(f"  … {rest} more (raise --files)")

    # --- index ---------------------------------------------------------------
    L.append("")
    i = b.index
    # 🔴 The BASIS is printed on every branch that has one. A hit reached anywhere
    # but the owner answers about a scope `locate` did NOT confirm owns the
    # service, and the reader has to be able to see WHICH scope answered.
    via = f" [scope via {i.basis}]" if i.basis and i.basis != OWNER_BASIS else ""
    # 🔴 A `ref-absent` is a claim about EVERY scope asked, so it prints the
    # denominator. Without it, "nothing recorded under that ref yet" is
    # indistinguishable from "I asked one scope out of three".
    checked = (f" — checked {len(i.scopes_checked)} scope(s): "
               + ", ".join(i.scopes_checked)) if i.scopes_checked else ""
    if i.status == "hit":
        sens = f" sensitivity={i.sensitivity}" if i.sensitivity else ""
        L.append(f"index: {i.scope}/{i.ref} — HIT (from index){sens}{via}")
        # 🔴 `## What it is` FIRST — it is the orienting sentence, and the two
        # sections after it assume the reader already has it.
        if i.what:
            L.append(f"  {WHAT_HEADING}")
            for ln in i.what.splitlines():
                L.append(f"  {ln}")
        if i.pointers:
            L.append(f"  {POINTERS_HEADING}")
            for ln in i.pointers.splitlines():
                L.append(f"  {ln}")
        if i.nuance:
            L.append(f"  {NUANCE_HEADING}")
            for ln in i.nuance.splitlines():
                L.append(f"  {ln}")
        if not i.what:
            # Said, not left blank — the same rule `subsystem_recall.render_text`
            # applies to its own bodies. Absent and present-but-empty fold
            # together: both render as nothing above, and either way the block
            # does not answer what the thing IS.
            #
            # 🔴 AND IT CLAIMS A PARSE, NEVER A FACT ABOUT THE ENTRY — the same
            # correction, in the same words, as the notice in
            # `subsystem_recall.render_text`. "no `## What it is` content" reads
            # as "the entry has none"; what this branch actually knows is that
            # the extractor found none, and a heading the parser does not match
            # reaches here with the answer sitting on disk.
            # `subsystem_touch.SHAPE_HEADINGS` excludes this heading, so
            # `--validate` will not flag it either — naming the causes here is the
            # only signal the reader gets.
            #
            # 🔴 THE CAUSE LIST IS EXPLICITLY NON-EXHAUSTIVE ("among others"), for
            # the reason spelled out beside the twin notice in `subsystem_recall`:
            # a RENAME (`## What It Is`, `## What it is:`, `### What it is`), an
            # INDENTED heading and one inside a ``` FENCE all land in this branch,
            # and only the first is literally a "rename".
            #
            # 🔴 THE PREFIX THIS SHARES WITH THE `subsystem_recall` notice IS
            # QUOTED IN `claude/skills/analyze-service/SKILL.md` step 2, which
            # tells the agent to relay it AS WRITTEN. That quotation is pinned to
            # this string by `test_service_recon.py::
            # TestTheSkillQuotesTheDegradeNotice` — reword either notice and the
            # pin goes red naming the doc, so the two cannot drift apart.
            L.append(f"  (no parsable `{WHAT_HEADING}` — absent, empty, or not parsed as a "
                     f"heading [renamed, indented, fenced, among others]; "
                     f"re-derive what it is live)")
        if not i.pointers and not i.nuance:
            L.append(f"  (entry exists but carries neither `{POINTERS_HEADING}` "
                     f"nor `{NUANCE_HEADING}`)")
    elif i.status == "ref-ambiguous":
        L.append(f"index: AMBIGUOUS in {i.scope} — {' | '.join(i.candidates) or '(candidates unlisted)'}"
                 f" — pick one, never guess{via}")
    else:
        L.append(f"index: {i.status}"
                 + (f" (scope {i.scope})" if i.scope else "")
                 + via
                 + (f" — {i.detail}" if i.detail else "")
                 + checked)

    # --- config --------------------------------------------------------------
    L.append("")
    c = b.config
    if c.status != "extracted":
        L.append(f"config: {c.status}" + (f" — {c.detail}" if c.detail else ""))
    else:
        L.append(f"config (re-derived live; knob extractor, not a YAML parser): "
                 f"{c.manifests_read} of {c.manifests_seen} manifest(s) read")
        for f in c.files:
            if f.detail:
                L.append(f"  {f.file}: {f.detail}")
                continue
            if not f.knobs:
                L.append(f"  {f.file}: no load-bearing knob matched")
                continue
            L.append(f"  {f.file}")
            for k in f.knobs:
                L.append(f"    {k.path} = {k.value}")
            if f.truncated:
                L.append(f"    … {f.truncated} more knob(s) (raise --knobs)")

    # --- git -----------------------------------------------------------------
    L.append("")
    g = b.git
    if g.status == "commits":
        shown_spec = " ".join(g.pathspec[:PATHSPEC_SHOWN])
        rest_spec = len(g.pathspec) - PATHSPEC_SHOWN
        L.append(f"recent changes (git log -{len(g.commits)} -- {shown_spec}"
                 + (f" +{rest_spec} more path(s)" if rest_spec > 0 else "") + "):")
        for cm in g.commits:
            L.append(f"  {cm.sha}  {cm.date}  {cm.subject}" + ("   ⚠ MOVED" if cm.moved else ""))
    else:
        L.append(f"recent changes: {g.status}" + (f" — {g.detail}" if g.detail else ""))

    # --- live ----------------------------------------------------------------
    L.append("")
    lv = b.live
    if lv.status == "ran":
        L.append(f"live @ {lv.context} ns={lv.namespace}:")
        for p in lv.probes:
            head = " ".join(p.argv[:1] + p.argv[-3:])
            if p.rc == 0:
                L.append(f"  $ {head}")
                for ln in p.out.splitlines():
                    L.append(f"    {ln}")
            else:
                L.append(f"  $ {head} → rc={p.rc}: {p.out.splitlines()[0] if p.out else ''}")
    else:
        L.append(f"live: {lv.status.upper()}" + (f" — {lv.detail}" if lv.detail else "")
                 + ("" if lv.status != "off" else ""))
        if lv.status in ("no-context", "no-namespace", "failed", "off"):
            L.append("  live state is UNVERIFIED — do not report it as observed.")

    L.append("")
    L.append("provenance: pointers/nuance are `from index`; roots/locate/config/log are "
             "`re-derived live`; cluster state is only ever what the `live` block says.")
    return "\n".join(L) + "\n"


def brief_json(b: Brief) -> dict:
    thin = thin_runner_up(b.locate)
    return {
        "service": b.service,
        "token": b.token,
        "store_root": b.store_root,
        "notes": list(b.notes),
        "exit_code": b.exit_code,
        "locate": {
            "status": b.locate.status,
            "owner": b.locate.owner,
            "owner_tied_with": list(b.locate.owner_tied_with),
            "thin_runner_up": (
                None if thin is None
                else {"path": thin[0].path, "matches": len(thin[0].matches),
                      "lead_percent": thin[1]}
            ),
            "files_examined": b.locate.files_examined,
            "total_matches": b.locate.total_matches,
            "roots": [
                {"path": r.path, "status": r.status, "origin": r.origin,
                 "files_examined": r.files_examined, "matches": list(r.matches),
                 "detail": r.detail}
                for r in b.locate.roots
            ],
        },
        "index": {
            "status": b.index.status, "scope": b.index.scope, "ref": b.index.ref,
            "sensitivity": b.index.sensitivity, "basis": b.index.basis,
            "scopes_checked": list(b.index.scopes_checked),
            "candidates": list(b.index.candidates),
            "pointers": b.index.pointers, "nuance": b.index.nuance, "detail": b.index.detail,
        },
        "config": {
            "status": b.config.status, "manifests_seen": b.config.manifests_seen,
            "manifests_read": b.config.manifests_read, "detail": b.config.detail,
            "files": [
                {"file": f.file, "truncated": f.truncated, "detail": f.detail,
                 "knobs": [{"path": k.path, "value": k.value, "redacted": k.redacted}
                           for k in f.knobs]}
                for f in b.config.files
            ],
        },
        "git": {
            "status": b.git.status, "repo": b.git.repo, "pathspec": list(b.git.pathspec),
            "detail": b.git.detail,
            "commits": [{"sha": c.sha, "date": c.date, "subject": c.subject, "moved": c.moved}
                        for c in b.git.commits],
        },
        "live": {
            "status": b.live.status, "context": b.live.context, "namespace": b.live.namespace,
            "detail": b.live.detail,
            "probes": [{"argv": list(p.argv), "rc": p.rc, "out": p.out} for p in b.live.probes],
        },
    }


# --- CLI -----------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="service_recon.py",
        description="Deterministic /analyze-service recon: locate + index + config + git log.",
        epilog="Static by default. --live adds read-only kubectl/flux and needs --context.",
    )
    p.add_argument("service", help="the subsystem to recon (normalized like an index ref)")
    p.add_argument("--repo", action="append", default=[],
                   help="a search root; repeatable. Default: the $HOMELAB/$DATAPACKET handles + cwd.")
    p.add_argument("--store", default=str(DEFAULT_STORE_ROOT), help="index store root")
    p.add_argument("--live", action="store_true", help="also probe the cluster (read-only)")
    p.add_argument("--context", default=None, help="kube context; --live never infers one")
    p.add_argument("--namespace", default=None, help="override the namespace found in the manifests")
    p.add_argument("--files", type=int, default=DEFAULT_FILE_LIMIT, help="max located files shown/read")
    p.add_argument("--knobs", type=int, default=DEFAULT_KNOBS_PER_FILE, help="max knobs per manifest")
    p.add_argument("--log", type=int, default=DEFAULT_LOG_LIMIT, help="git log entries")
    p.add_argument("--json", action="store_true", help="machine-readable brief")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    for name, val in (("--files", args.files), ("--knobs", args.knobs), ("--log", args.log)):
        if val < 1:
            print(f"service_recon: {name} must be >= 1, got {val}", file=sys.stderr)
            return EXIT_USAGE
    if args.context and not args.live:
        print("service_recon: --context without --live probes nothing; add --live", file=sys.stderr)
        return EXIT_USAGE

    b = recon(
        args.service, repos=args.repo, store_root=args.store, cwd=Path.cwd(),
        live=args.live, context=args.context, namespace=args.namespace,
        file_limit=args.files, log_limit=args.log, knob_limit=args.knobs,
    )
    if args.json:
        print(json.dumps(brief_json(b), indent=2))
    else:
        sys.stdout.write(render_brief(b, file_limit=args.files))
    return b.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
