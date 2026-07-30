"""Backfill: propose (and, on explicit approval, perform) a home for the loose
files sitting at the library root.

`plan` is READ-ONLY with respect to the tree. It walks the library, seeds the
alias table from the existing directory names and torrent names, scores each
loose root file with the same matcher the live download path uses, and writes a
manifest (TSV for eyeballing, JSON for `apply`).

`apply` refuses to do anything without an explicit manifest the user has
reviewed. It is the ONLY code path in dl-router that touches pre-existing files,
so it is also the only one that has to care about seeding: a torrent-backed file
moves via `torrents/setLocation` (and is re-checked afterwards), a plain file
moves via `os.rename`. Any failure aborts the remaining rows.

Row actions
    SKIP  below threshold or ambiguous — the default, and always safe
    NEW   target directory does not exist yet (created on apply)
    qbt   torrent-backed -> setLocation
    fs    not torrent-backed -> os.rename
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

TSV_HEADER = ["action", "move", "relpath", "size", "proposed_dir",
              "confidence", "torrent_hash", "reason"]


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

    def tsv(self) -> str:
        return "\t".join([
            self.action, self.move, self.relpath, str(self.size),
            self.proposed_dir, f"{self.confidence:.2f}", self.torrent_hash,
            " ".join(str(self.reason).split()),
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
        lines = ["# dl-router backfill manifest — review before `apply`.",
                 "# Edit the `action` column: SKIP disables a row.",
                 "\t".join(TSV_HEADER)]
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
            try:
                if de.is_file(follow_symlinks=False):
                    out.append(de.name)
            except OSError:
                continue
    return sorted(out)


def seed_aliases(store, dir_names, torrents=None, path_map=None,
                 root: Path | None = None) -> int:
    """Seed global aliases from existing directory names and torrent names.

    Directory names give the matcher its identity mapping across the three
    naming conventions; torrent names make a re-download of a known title land
    where its predecessor lives.
    """
    seeded = 0
    existing = set(dir_names)
    for name in dir_names:
        key = norm_key(name)
        if key and store.alias(key, "") is None:
            store.upsert_alias(key, name, "")
            seeded += 1
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
            if key and store.alias(key, "") is None:
                store.upsert_alias(key, parts[0], "")
                seeded += 1
    return seeded


def plan(root, *, store, dir_names, matcher: Matcher | None = None,
         torrents=None, path_map=None, threshold: float = 0.75,
         clock=time.time, do_seed: bool = True) -> Plan:
    """Build a manifest for the loose root files. Never writes into the tree."""
    root = Path(root)
    notes = []
    if do_seed:
        n = seed_aliases(store, dir_names, torrents, path_map, root)
        notes.append(f"seeded {n} alias(es)")

    if matcher is None:
        matcher = Matcher(dir_names, store.alias_map(), threshold=threshold)
    else:
        matcher.aliases = store.alias_map()

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

    torrent_index = qbt_mod.index_by_host_path(torrents or [], path_map) \
        if path_map else {}

    known = set(dir_names)
    rows = []
    for name in loose_root_files(root):
        full = root / name
        try:
            size = full.stat().st_size
        except OSError:
            size = 0
        stem = filename_stem(name)
        # The backfill has NO page context — the filename is all there is. So
        # here (and only here) the stem is fed in as a subject phrase, instead
        # of being capped at the live path's weak filename score. The threshold
        # is unchanged, so an opaque name still lands on SKIP.
        ctx = MatchContext(filename=name, tags=(stem,), size=size)
        result = matcher.match(ctx)

        # A hand-set alias may name a directory that does not exist yet; the
        # manifest is the explicit review step, so propose it as NEW rather
        # than discarding it (directories are still never created silently).
        pending_new = None
        alias_target = store.alias(norm_key(stem), "")
        if (alias_target and alias_target not in known
                and is_safe_dir_name(alias_target)
                and result.confidence < SCORE_ALIAS_GLOBAL):
            pending_new = alias_target

        torrent = torrent_index.get(os.path.normpath(str(full)))
        thash = str(torrent.get("hash", "")) if torrent else ""
        move = MOVE_QBT if torrent else MOVE_FS

        target = pending_new or result.dir
        confidence = SCORE_ALIAS_GLOBAL if pending_new else result.confidence

        if blocked:
            action = ACTION_SKIP
            reason = blocked
        elif pending_new:
            action = ACTION_NEW
            reason = f"alias '{stem}' -> new directory '{pending_new}'"
        elif not result.auto or result.dir == matcher.other_dir:
            action = ACTION_SKIP
            reason = f"below threshold ({result.confidence:.2f}): {result.reason}"
        elif result.dir not in known:
            action = ACTION_NEW
            reason = result.reason
        else:
            action = ACTION_QBT if torrent else ACTION_FS
            reason = result.reason

        rows.append(PlanRow(
            relpath=name, size=size,
            proposed_dir=(target if action != ACTION_SKIP else ""),
            confidence=confidence, reason=reason, action=action,
            move=move, torrent_hash=thash))

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


def load_manifest(path) -> Plan:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = [PlanRow(**r) for r in data.get("rows", [])]
    return Plan(root=data["root"], created_at=data.get("created_at", 0.0),
                rows=rows, path_map=data.get("path_map"),
                notes=data.get("notes", []))


class ApplyError(RuntimeError):
    pass


def apply(manifest, *, client=None, path_map=None, dry_run: bool = False,
          rename=os.rename, makedirs=os.makedirs, exists=os.path.exists,
          verify=True):
    """Execute a reviewed manifest. Aborts the remaining rows on any failure.

    `manifest` is a path or a `Plan`. Passing neither is refused — there is no
    "just do the obvious thing" mode by design.
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

    for idx, row in enumerate(todo):
        try:
            _apply_row(row, root, client=client, path_map=path_map,
                       dry_run=dry_run, rename=rename, makedirs=makedirs,
                       exists=exists, verify=verify, ops=results["ops"])
            results["moved"].append(row.relpath)
        except Exception as exc:  # noqa: BLE001 — reported, then abort
            results["failed"] = {"relpath": row.relpath, "error": str(exc)}
            results["aborted"] = [r.relpath for r in todo[idx + 1:]]
            break
    return results


def _apply_row(row: PlanRow, root: Path, *, client, path_map, dry_run,
               rename, makedirs, exists, verify, ops):
    if not is_safe_dir_name(row.proposed_dir):
        raise ApplyError(f"unsafe target directory: {row.proposed_dir!r}")
    # Confine the source to the library root (rejects `..`, absolutes, escapes).
    try:
        src = safe_rel_path(row.relpath, root=root)
    except UnsafeName as exc:
        raise ApplyError(str(exc)) from exc
    target_dir = root / row.proposed_dir

    if row.action == ACTION_NEW or not exists(str(target_dir)):
        ops.append(f"mkdir {target_dir}")
        if not dry_run:
            makedirs(str(target_dir), exist_ok=True)

    if row.move == MOVE_QBT:
        if client is None or path_map is None:
            raise ApplyError("torrent-backed row needs a qBittorrent client "
                             "and a derived path map")
        if not row.torrent_hash:
            raise ApplyError(f"missing torrent hash for {row.relpath}")
        container = path_map.to_container(str(target_dir))
        if not container:
            raise ApplyError(f"target outside the mapped mount: {target_dir}")
        ops.append(f"setLocation {row.torrent_hash[:12]} -> {container}")
        if dry_run:
            return
        client.set_location(row.torrent_hash, container)
        if verify and not client.verify_seeding(row.torrent_hash):
            raise ApplyError(
                f"torrent {row.torrent_hash[:12]} is not seeding after "
                f"setLocation — aborting before touching anything else")
        return

    dest = target_dir / Path(row.relpath).name
    ops.append(f"rename {src} -> {dest}")
    if dry_run:
        return
    if exists(str(dest)):
        raise ApplyError(f"destination already exists: {dest}")
    rename(str(src), str(dest))
