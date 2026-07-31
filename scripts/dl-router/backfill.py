"""Backfill: propose (and, on explicit approval, perform) a home for the loose
files sitting at the library root.

`plan` is READ-ONLY -- with respect to the tree AND to the alias database. It
walks the library, works out which aliases the existing directory and torrent
names WOULD seed (in memory), scores each loose root file, and writes a
manifest. `--seed-aliases` is what actually persists them.

`apply` refuses to do anything without an explicit manifest the user has
reviewed, and RE-DERIVES every torrent decision against live qBittorrent at the
moment it runs -- the manifest's `move`/`torrent_hash` are plan-time values and
a torrent can be added, removed or moved in between (spec section 3, hazard 1).
It is the ONLY code path in dl-router that touches pre-existing files, so it is
also the only one that has to care about seeding: a torrent-backed file moves
via `torrents/setLocation` (and is re-verified afterwards, waiting out the
`moving` state), a file PROVEN not torrent-backed moves via `os.rename`. Any
failure aborts the remaining rows.

Row actions
    SKIP  below threshold, ambiguous, or unproven -- the default, always safe
    NEW   target directory does not exist yet (created on apply)
    qbt   torrent-backed -> setLocation
    fs    PROVEN not torrent-backed -> os.rename

"Proven" is the load-bearing word. `move = MOVE_QBT if torrent else MOVE_FS`
turned an index MISS into a plain rename, and the index only ever knew about
`content_path` and `save_path/name` -- so a multi-file or no-root-folder
torrent whose payload sits at the library root (exactly this tool's target
population) was missed and renamed out from under a live seed. Absence from the
index now only means "not torrent-backed" when the index is known exhaustive.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import qbt as qbt_mod
from matcher import (
    SCORE_ALIAS_GLOBAL, MatchContext, Matcher, filename_stem, norm_key,
)
from safety import UnsafeName, is_safe_dir_name, safe_rel_path

ACTION_SKIP = "SKIP"
ACTION_NEW = "NEW"
ACTION_QBT = "qbt"
ACTION_FS = "fs"

MOVE_QBT = "qbt"
MOVE_FS = "fs"
# Neither proven torrent-backed nor proven not. Never executable.
MOVE_UNKNOWN = "unknown"

# What the proposal actually rests on, so a reviewer can see it in the TSV
# rather than having to infer it from the confidence column.
SIGNAL_ALIAS = "alias"        # a seeded/hand-set alias matched the stem
SIGNAL_FILENAME = "filename"  # filename tokens only -- capped at 0.50
SIGNAL_NONE = "none"

TSV_HEADER = ["action", "move", "relpath", "size", "proposed_dir",
              "confidence", "torrent_hash", "signal", "reason"]


def _tsv_clean(value) -> str:
    """A TSV cell can contain neither a tab nor a newline."""
    return " ".join(str(value).split())


@dataclass
class PlanRow:
    relpath: str
    size: int
    proposed_dir: str
    confidence: float
    reason: str
    action: str
    move: str
    torrent_hash: str = ""
    signal: str = SIGNAL_NONE

    def tsv(self) -> str:
        return "\t".join([
            self.action, self.move, _tsv_clean(self.relpath), str(self.size),
            self.proposed_dir, f"{self.confidence:.2f}", self.torrent_hash,
            self.signal, _tsv_clean(self.reason),
        ])


@dataclass
class Plan:
    root: str
    created_at: float
    rows: list = field(default_factory=list)
    path_map: dict | None = None
    notes: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {"root": self.root, "created_at": self.created_at,
             "path_map": self.path_map, "notes": self.notes,
             "rows": [asdict(r) for r in self.rows]},
            ensure_ascii=False, indent=2)

    def to_tsv(self) -> str:
        """The REVIEWED ARTEFACT. `apply --manifest <this file>` reads it.

        The header used to invite you to edit the `action` column while
        `load_manifest` read JSON only, so every edit was silently discarded --
        on the one operation in dl-router that can break seeding. The TSV is
        now a first-class manifest, and applying the JSON cross-checks it.
        """
        pm = (f"{self.path_map['container']}\t{self.path_map['host']}"
              if self.path_map else "")
        lines = [
            "# dl-router backfill manifest -- review, then:",
            "#     dl-route backfill apply --manifest <this .tsv> --dry-run",
            "# Edit the `action` column: SKIP disables a row. Editing this file",
            "# DOES take effect -- apply reads it. Lines starting with # and",
            "# blank lines are ignored; the column order must not change.",
            f"#!root\t{_tsv_clean(self.root)}",
            f"#!created_at\t{self.created_at}",
            f"#!path_map\t{pm}",
            "\t".join(TSV_HEADER),
        ]
        lines += [r.tsv() for r in self.rows]
        return "\n".join(lines) + "\n"

    def counts(self) -> dict:
        out: dict = {}
        for row in self.rows:
            out[row.action] = out.get(row.action, 0) + 1
        return out


def loose_root_files(root: Path) -> list:
    """Files sitting directly at the library root (the backfill's subjects)."""
    out = []
    try:
        it = os.scandir(root)
    except OSError:
        return out
    with it:
        for de in it:
            if de.name.startswith("."):
                continue
            # A tab or newline in a filename would break the TSV manifest into
            # the wrong number of columns, and `safe_rel_path` would refuse it
            # at apply time anyway. Leave it out of the plan entirely rather
            # than emit a row that cannot be parsed back.
            if any(ch in de.name for ch in "\t\n\r"):
                continue
            try:
                if de.is_file(follow_symlinks=False):
                    out.append(de.name)
            except OSError:
                continue
    return sorted(out)


def alias_seeds(store, dir_names, torrents=None, path_map=None,
                root: Path | None = None) -> dict:
    """Work out which global aliases the tree WOULD seed. Writes nothing.

    Returns `{key: dir}` for keys the store does not already have. Split out of
    `seed_aliases` so `plan` can be genuinely read-only: it advertises itself
    that way, and it was quietly upserting into the same alias table that
    drives LIVE routing, which is not something a read-only command may do.

    Directory names give the matcher its identity mapping across the three
    naming conventions; torrent names make a re-download of a known title land
    where its predecessor lives.
    """
    seeds: dict = {}
    existing = set(dir_names)
    for name in dir_names:
        key = norm_key(name)
        if key and key not in seeds and store.alias(key, "") is None:
            seeds[key] = name
    if torrents and path_map and root is not None:
        for t in torrents:
            container = str(t.get("save_path") or "").strip()
            host = path_map.to_host(container) if container else None
            if not host:
                continue
            try:
                rel = Path(host).resolve().relative_to(Path(root).resolve())
            except (ValueError, OSError):
                continue
            parts = rel.parts
            if len(parts) != 1 or parts[0] not in existing:
                continue
            key = norm_key(str(t.get("name") or ""))
            if key and key not in seeds and store.alias(key, "") is None:
                seeds[key] = parts[0]
    return seeds


def seed_aliases(store, dir_names, torrents=None, path_map=None,
                 root: Path | None = None) -> int:
    """PERSIST the seeds from `alias_seeds`. Returns how many were written."""
    seeds = alias_seeds(store, dir_names, torrents, path_map, root)
    for key, target in seeds.items():
        store.upsert_alias(key, target, "")
    return len(seeds)


def plan(root, *, store, dir_names, matcher: Matcher | None = None,
         torrents=None, path_map=None, threshold: float = 0.75,
         clock=time.time, do_seed: bool = True, persist_seeds: bool = False,
         files_for=None) -> Plan:
    """Build a manifest for the loose root files.

    READ-ONLY by default: it writes nothing into the tree AND nothing into the
    alias database. `persist_seeds=True` (the CLI's `--seed-aliases`) is what
    actually writes; otherwise the would-be seeds are merged into the matcher
    in memory for this run only, so the plan is still useful without the
    command having a side effect its own help text denies.
    """
    root = Path(root)
    notes = []
    seeds: dict = {}
    if do_seed:
        seeds = alias_seeds(store, dir_names, torrents, path_map, root)
        if persist_seeds:
            for key, target in seeds.items():
                store.upsert_alias(key, target, "")
            notes.append(f"seeded {len(seeds)} alias(es)")
        elif seeds:
            notes.append(f"{len(seeds)} alias(es) would be seeded "
                         f"(--seed-aliases to persist); used in memory only")

    aliases = dict(store.alias_map())
    if not persist_seeds:
        for key, target in seeds.items():
            aliases.setdefault((key, ""), target)
    if matcher is None:
        matcher = Matcher(dir_names, aliases, threshold=threshold)
    else:
        matcher.aliases = aliases

    def alias_for(key: str):
        return aliases.get((key, ""))

    # `torrents=None` means the qBittorrent state is UNKNOWN (unreachable, no
    # credentials). That is not the same as "there are no torrents": without
    # that list we cannot prove a file is NOT a live seeding payload, and a
    # plain rename would break seeding. So we refuse to classify anything.
    # The same applies when torrents exist but the path map could not be
    # derived — we then cannot tell which host file each torrent refers to.
    qbt_known = torrents is not None
    blocked = None
    if not qbt_known:
        blocked = ("qBittorrent state unknown — cannot prove a file is not a "
                   "live torrent payload")
    elif torrents and not path_map:
        blocked = ("no host<->container path map derived — cannot tell which "
                   "files are torrent-backed")
    if blocked:
        notes.append(f"{blocked}; every row is SKIP")

    torrent_index = (qbt_mod.index_by_host_path(torrents or [], path_map,
                                                files_for=files_for)
                     if path_map else qbt_mod.TorrentIndex())

    # Can absence from the index be read as "not torrent-backed"?
    #   * qBittorrent unreachable      -> no.
    #   * qBittorrent reachable, NO torrents at all -> yes, positively.
    #   * torrents but no path map     -> no.
    #   * torrents + map + every file list read -> yes.
    #   * any file list failed / not requested   -> NO. This is the case that
    #     used to silently become `fs`.
    # Proof is PER FILE, not per run. A single failed `torrents/files` call
    # used to set one global flag and collapse the whole manifest to SKIP --
    # with ~1000 torrents, one transient HTTP error produced an empty
    # manifest. A torrent's files live under its own save_path, so a failed
    # listing only makes THAT subtree unknown (see TorrentIndex.proves_absent).
    def proves_absent(host_path: str) -> bool:
        if not qbt_known:
            return False
        if not torrents:
            return True          # no torrents at all: positive proof
        if not path_map:
            return False
        return torrent_index.proves_absent(host_path)

    if qbt_known and torrents and path_map and not torrent_index.complete:
        notes.append(
            f"{len(torrent_index.errors)} torrent file listing(s) unavailable — "
            f"files under those torrents' save paths cannot be proven safe to "
            f"rename and stay SKIP; the rest of the tree is unaffected")

    known = set(dir_names)
    rows = []
    for name in loose_root_files(root):
        full = root / name
        try:
            size = full.stat().st_size
        except OSError:
            size = 0
        stem = filename_stem(name)
        # The backfill has NO page context, so the filename is all there is --
        # but spec section 7 caps the filename signal at 0.50 and this used to
        # smuggle the stem in as a page TAG, scoring it 0.85 through the
        # tag-exact rule and auto-filing on a filename alone. The cap is now
        # respected: a filename-only row cannot reach the 0.75 threshold.
        ctx = MatchContext(filename=name, size=size)
        result = matcher.match(ctx)

        # The one signal that IS allowed to carry a row: an explicit alias on
        # the stem (seeded from a directory or torrent name, or hand-set). That
        # is recorded knowledge, not a guess about an opaque filename.
        alias_target = alias_for(norm_key(stem))
        pending_new = None
        alias_hit = None
        if alias_target and is_safe_dir_name(alias_target):
            if alias_target in known:
                alias_hit = alias_target
            else:
                # A hand-set alias may name a directory that does not exist
                # yet; the manifest is the explicit review step, so propose it
                # as NEW (directories are still never created silently).
                pending_new = alias_target

        host_path = os.path.normpath(str(full))
        torrent = torrent_index.get(host_path)
        thash = str(torrent.get("hash", "")) if torrent else ""
        # FAIL CLOSED. `MOVE_QBT if torrent else MOVE_FS` turned an index MISS
        # into a plain rename of what may well be a live seeding payload.
        if torrent:
            move = MOVE_QBT
        elif proves_absent(host_path):
            move = MOVE_FS
        else:
            move = MOVE_UNKNOWN

        if pending_new or alias_hit:
            signal = SIGNAL_ALIAS
            target = pending_new or alias_hit
            confidence = SCORE_ALIAS_GLOBAL
        elif result.candidates:
            signal = SIGNAL_FILENAME
            target = result.dir
            confidence = result.confidence
        else:
            signal = SIGNAL_NONE
            target = result.dir
            confidence = result.confidence

        if blocked:
            action = ACTION_SKIP
            reason = blocked
        elif pending_new:
            action = ACTION_NEW
            reason = f"alias '{stem}' -> new directory '{pending_new}'"
        elif alias_hit:
            action = (ACTION_QBT if torrent
                      else ACTION_FS if move == MOVE_FS else ACTION_SKIP)
            reason = f"alias(global) '{stem}' -> '{alias_hit}'"
            if action == ACTION_SKIP:
                reason = (f"{reason}; NOT PROVEN non-torrent — refusing to "
                          f"rename")
        elif result.auto and result.dir != matcher.other_dir:
            # Only reachable with a lowered threshold; the cap keeps a
            # filename-only score at or below 0.50 with the 0.75 default.
            action = (ACTION_NEW if result.dir not in known
                      else ACTION_QBT if torrent
                      else ACTION_FS if move == MOVE_FS else ACTION_SKIP)
            reason = f"FILENAME ONLY (<=0.50 cap): {result.reason}"
        else:
            action = ACTION_SKIP
            reason = (f"filename-only signal, below threshold "
                      f"({result.confidence:.2f}): {result.reason}"
                      if result.candidates
                      else f"no signal in the filename: {result.reason}")

        if action != ACTION_SKIP and move == MOVE_UNKNOWN:
            action = ACTION_SKIP        # belt and braces: never executable

        rows.append(PlanRow(
            relpath=name, size=size,
            proposed_dir=(target if action != ACTION_SKIP else ""),
            confidence=confidence, reason=reason, action=action,
            move=move, torrent_hash=thash, signal=signal))

    return Plan(root=str(root), created_at=clock(), rows=rows, notes=notes,
                path_map=({"container": path_map.container_prefix,
                           "host": path_map.host_prefix} if path_map else None))


def write_manifest(plan_obj: Plan, out_dir, *, stem: str = "backfill") -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(plan_obj.created_at))
    tsv_path = out_dir / f"{stem}-{stamp}.tsv"
    json_path = out_dir / f"{stem}-{stamp}.json"
    tsv_path.write_text(plan_obj.to_tsv(), encoding="utf-8")
    json_path.write_text(plan_obj.to_json(), encoding="utf-8")
    return {"tsv": str(tsv_path), "json": str(json_path)}


def load_tsv(path) -> Plan:
    """Parse the TSV manifest -- the artefact the user was told to review.

    The TSV header invited an edit to the `action` column while `load_manifest`
    read JSON only, so every edit was silently discarded on the ONE operation
    that can break seeding. This makes the reviewed file the applied file.
    """
    path = Path(path)
    root, created_at, path_map = "", 0.0, None
    rows: list = []
    seen_header = False
    for lineno, raw in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if line.startswith("#!"):
            key, _, rest = line[2:].partition("\t")
            if key == "root":
                root = rest.strip()
            elif key == "created_at":
                try:
                    created_at = float(rest.strip())
                except ValueError:
                    created_at = 0.0
            elif key == "path_map" and rest.strip():
                container, _, host = rest.partition("\t")
                if container.strip() and host.strip():
                    path_map = {"container": container.strip(),
                                "host": host.strip()}
            continue
        if line.startswith("#"):
            continue
        cells = line.split("\t")
        if not seen_header:
            if cells != TSV_HEADER:
                raise ApplyError(
                    f"{path}: unexpected column header on line {lineno}. "
                    f"Expected {TSV_HEADER}, got {cells}. Do not reorder or "
                    f"rename columns -- only edit the `action` values.")
            seen_header = True
            continue
        if len(cells) != len(TSV_HEADER):
            raise ApplyError(
                f"{path}: line {lineno} has {len(cells)} columns, expected "
                f"{len(TSV_HEADER)}. A tab was probably introduced by hand.")
        values = dict(zip(TSV_HEADER, cells))
        action = values["action"].strip()
        if action not in (ACTION_SKIP, ACTION_NEW, ACTION_QBT, ACTION_FS):
            raise ApplyError(
                f"{path}: line {lineno} has action {action!r}. Allowed: "
                f"{ACTION_SKIP}, {ACTION_NEW}, {ACTION_QBT}, {ACTION_FS}.")
        try:
            size = int(values["size"] or 0)
            confidence = float(values["confidence"] or 0.0)
        except ValueError as exc:
            raise ApplyError(f"{path}: line {lineno}: {exc}") from exc
        rows.append(PlanRow(
            relpath=values["relpath"], size=size,
            proposed_dir=values["proposed_dir"], confidence=confidence,
            reason=values["reason"], action=action, move=values["move"],
            torrent_hash=values["torrent_hash"], signal=values["signal"]))
    if not seen_header:
        raise ApplyError(f"{path}: no column header found")
    if not root:
        raise ApplyError(f"{path}: no `#!root` line -- not a dl-router manifest")
    return Plan(root=root, created_at=created_at, rows=rows,
                path_map=path_map, notes=[f"loaded from TSV {path.name}"])


def _tsv_sibling(path: Path) -> Path:
    return path.with_suffix(".tsv")


def load_manifest(path) -> Plan:
    """Load a manifest. `.tsv` and `.json` are both first-class.

    Applying the JSON cross-checks a sibling TSV: if the user edited the TSV
    (which the header tells them to do) and then applied the JSON, the edits
    would be ignored. Refusing is the only safe answer -- silently applying the
    unedited plan is how a SKIP the reviewer added gets executed anyway.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    if path.suffix.lower() == ".tsv":
        return load_tsv(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [PlanRow(**r) for r in data.get("rows", [])]
    plan_obj = Plan(root=data["root"], created_at=data.get("created_at", 0.0),
                    rows=rows, path_map=data.get("path_map"),
                    notes=data.get("notes", []))

    sibling = _tsv_sibling(path)
    if sibling.exists():
        try:
            reviewed = load_tsv(sibling)
        except ApplyError:
            raise
        edits = [(a.relpath, a.action, b.action)
                 for a, b in zip(plan_obj.rows, reviewed.rows)
                 if a.relpath == b.relpath and a.action != b.action]
        same_shape = ([r.relpath for r in plan_obj.rows]
                      == [r.relpath for r in reviewed.rows])
        if not same_shape or edits:
            raise ApplyError(
                f"{sibling.name} has been edited but you pointed apply at the "
                f"JSON, whose rows differ ({len(edits)} action change(s)). The "
                f"TSV is the reviewed artefact -- re-run with "
                f"--manifest {sibling}")
    return plan_obj


class ApplyError(RuntimeError):
    pass


class LiveState:
    """qBittorrent as it is AT APPLY TIME, not as the plan remembered it."""

    __slots__ = ("index", "path_map", "no_torrents")

    def __init__(self, index, path_map, no_torrents):
        self.index = index
        self.path_map = path_map
        self.no_torrents = no_torrents

    def proves_absent(self, host_path: str) -> bool:
        """Per PATH, not per run -- one unreadable torrent must not veto the
        whole apply (see TorrentIndex.proves_absent)."""
        if self.no_torrents:
            return True
        if self.path_map is None:
            return False
        return self.index.proves_absent(host_path)


def derive_live_state(client, root: Path, host_roots=()) -> LiveState:
    """Re-derive the torrent index and the path map from the live instance.

    Spec section 3 hazard 1 requires the host<->container mapping to be derived
    at runtime. `apply` was using the manifest's plan-time `move` and
    `torrent_hash` verbatim, so anything that changed in between -- a torrent
    added, removed, re-checked or moved -- was applied against a stale picture
    of which files are live payloads.
    """
    torrents = client.torrents_info()
    roots = qbt_mod.host_root_candidates(root, host_roots)
    path_map = qbt_mod.derive_path_map(torrents, roots, library_root=root)
    files_for = getattr(client, "torrents_files", None)
    index = qbt_mod.index_by_host_path(torrents, path_map, files_for=files_for)
    return LiveState(index, path_map, no_torrents=not torrents)


def apply(manifest, *, client=None, path_map=None, dry_run: bool = False,
          rename=os.rename, makedirs=os.makedirs, exists=os.path.exists,
          verify=True, revalidate=True, host_roots=()):
    """Execute a reviewed manifest. Aborts the remaining rows on any failure.

    `manifest` is a path or a `Plan`. Passing neither is refused — there is no
    "just do the obvious thing" mode by design.

    With `revalidate=True` (the default, and the only setting anything outside
    the tests should use) every torrent decision is RE-DERIVED against live
    qBittorrent before the first row is touched, and a row whose live
    classification disagrees with the manifest aborts the run.
    """
    if manifest is None:
        raise ApplyError("apply requires an explicit reviewed manifest")
    plan_obj = manifest if isinstance(manifest, Plan) else load_manifest(manifest)
    root = Path(plan_obj.root)
    if not root.is_absolute():
        raise ApplyError(f"manifest root must be absolute: {root}")

    if path_map is None and plan_obj.path_map:
        path_map = qbt_mod.PathMap(plan_obj.path_map["container"],
                                   plan_obj.path_map["host"])

    results = {"moved": [], "skipped": [], "failed": None, "aborted": [],
               "ops": []}
    todo = [r for r in plan_obj.rows if r.action != ACTION_SKIP]
    results["skipped"] = [r.relpath for r in plan_obj.rows
                          if r.action == ACTION_SKIP]

    live = None
    if todo and revalidate:
        if client is None:
            if dry_run:
                # --dry-run moves nothing, and it is the step the docs tell you
                # to run FIRST. Requiring credentials for a preview blocked the
                # review gate itself. Say plainly that the preview is unchecked.
                results["ops"].append(
                    "DRY RUN WITHOUT qBittorrent: this preview shows the "
                    "manifest's plan-time classification, NOT a re-validated "
                    "one. A real apply will re-derive and may differ.")
            else:
                raise ApplyError(
                    "apply must re-validate against live qBittorrent before "
                    "moving anything, and no client was supplied. The "
                    "manifest's `move` and `torrent_hash` are plan-time "
                    "values; a torrent can have been added, removed or moved "
                    "since.")
        else:
            live = derive_live_state(client, root, host_roots)
            if live.path_map is not None:
                path_map = live.path_map
            results["ops"].append(
                f"revalidated against qBittorrent: {len(live.index)} indexed "
                f"path(s), {len(live.index.unknown_prefixes)} unproven "
                f"subtree(s)")

    for idx, row in enumerate(todo):
        try:
            _apply_row(row, root, client=client, path_map=path_map,
                       dry_run=dry_run, rename=rename, makedirs=makedirs,
                       exists=exists, verify=verify, ops=results["ops"],
                       live=live)
            results["moved"].append(row.relpath)
        except Exception as exc:  # noqa: BLE001 — reported, then abort
            results["failed"] = {"relpath": row.relpath, "error": str(exc)}
            results["aborted"] = [r.relpath for r in todo[idx + 1:]]
            break
    return results


def _apply_row(row: PlanRow, root: Path, *, client, path_map, dry_run,
               rename, makedirs, exists, verify, ops, live=None):
    if not is_safe_dir_name(row.proposed_dir):
        raise ApplyError(f"unsafe target directory: {row.proposed_dir!r}")
    # Confine the source to the library root (rejects `..`, absolutes, escapes).
    try:
        src = safe_rel_path(row.relpath, root=root)
    except UnsafeName as exc:
        raise ApplyError(str(exc)) from exc
    target_dir = root / row.proposed_dir

    # --- runtime derivation (spec section 3, hazard 1) --------------------- #
    move, torrent_hash = row.move, row.torrent_hash
    if live is not None:
        torrent = live.index.get(os.path.normpath(str(src)))
        if torrent is not None:
            move = MOVE_QBT
            torrent_hash = str(torrent.get("hash") or "")
        elif live.proves_absent(os.path.normpath(str(src))):
            move = MOVE_FS
            torrent_hash = ""
        else:
            raise ApplyError(
                f"{row.relpath}: live qBittorrent state cannot prove this file "
                f"is not a torrent payload — refusing to move it")
        if move != row.move:
            raise ApplyError(
                f"{row.relpath}: live qBittorrent state says {move!r}, the "
                f"manifest says {row.move!r} — the plan is stale, re-run "
                f"`dl-route backfill plan`")
        if move == MOVE_QBT and torrent_hash.lower() != row.torrent_hash.lower():
            raise ApplyError(
                f"{row.relpath}: the torrent backing this file changed since "
                f"the plan was made — re-run `dl-route backfill plan`")

    if move == MOVE_UNKNOWN:
        raise ApplyError(
            f"{row.relpath}: classified `{MOVE_UNKNOWN}` — it was never proven "
            f"safe to move. This row should have been SKIP.")

    if row.action == ACTION_NEW or not exists(str(target_dir)):
        ops.append(f"mkdir {target_dir}")
        if not dry_run:
            makedirs(str(target_dir), exist_ok=True)

    if move == MOVE_QBT:
        if client is None or path_map is None:
            raise ApplyError("torrent-backed row needs a qBittorrent client "
                             "and a derived path map")
        if not torrent_hash:
            raise ApplyError(f"missing torrent hash for {row.relpath}")
        container = path_map.to_container(str(target_dir))
        if not container:
            raise ApplyError(f"target outside the mapped mount: {target_dir}")
        ops.append(f"setLocation {torrent_hash[:12]} -> {container}")
        if dry_run:
            return
        client.set_location(torrent_hash, container)
        # verify_seeding waits out `moving`: setLocation returns as soon as the
        # request is accepted, not when the payload has arrived.
        if verify and not client.verify_seeding(torrent_hash):
            raise ApplyError(
                f"torrent {torrent_hash[:12]} is not seeding after "
                f"setLocation — aborting before touching anything else")
        return

    dest = target_dir / Path(row.relpath).name
    ops.append(f"rename {src} -> {dest}")
    if dry_run:
        return
    if exists(str(dest)):
        raise ApplyError(f"destination already exists: {dest}")
    rename(str(src), str(dest))
