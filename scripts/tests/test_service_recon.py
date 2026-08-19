"""Tests for `scripts/lib/service_recon.py` — the deterministic /analyze-service recon.

🔴 EVERY FIXTURE IS SYNTHETIC. The real store
(`~/.claude/analyze-service-index/`) and the real infra repos are
client-confidential; devrc is PUBLIC. No test here reads either — every store is
built under `tmp_path`, every repo is a temp `git init`, and every service name
(`roster`, `paging`, `blob-upload`) is invented. The pairwise-distinctness
discipline from `test_subsystem_resolver.py` applies: no service name is also a
scope name, no namespace repeats a service name, and no asserted value equals a
constant the module names — so a wrong-field bug produces nothing, not a
plausible answer.

🔴 NO TEST RUNS A REAL `kubectl` OR `flux`. `live_state` takes a `runner` seam
and every live test injects one. The seam exists for exactly this: the argv
ledger below asserts what WOULD be run, which is a stronger claim than watching a
command that happens to fail because no cluster is reachable.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
sys.path.insert(0, str(ROOT / "scripts"))

import service_recon as sr  # noqa: E402
import subsystem_recall as rc  # noqa: E402
import subsystem_resolver as res  # noqa: E402

SCOPE = "ledger-repo"          # a scope word that is no service's name
SERVICE = "roster"             # the entry that exists
OTHER = "paging"               # a service with no entry
NAMESPACE = "front-desk"       # never equal to a service name


# =============================================================================
# Fixtures
# =============================================================================


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"},
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


DEPLOYMENT = f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {SERVICE}
  namespace: {NAMESPACE}
spec:
  replicas: 4
  template:
    spec:
      containers:
        - name: api
          image: registry.invalid/{SERVICE}:9.3.1
          ports:
            - containerPort: 8123
          resources:
            requests:
              cpu: 250m
              memory: 384Mi
"""

KUSTOMIZATION = f"""\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: {NAMESPACE}
resources:
  - deployment.yaml
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A synthetic gitops repo holding one service, committed."""
    r = tmp_path / "ledger-repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r / "apps" / SERVICE / "deployment.yaml", DEPLOYMENT)
    _write(r / "apps" / SERVICE / "kustomization.yaml", KUSTOMIZATION)
    _write(r / "apps" / OTHER / "deployment.yaml", DEPLOYMENT.replace(SERVICE, OTHER))
    _write(r / "README.md", "synthetic\n")
    _git(r, "add", "apps", "README.md")
    _git(r, "commit", "-qm", f"feat({SERVICE}): bump to 9.3.1")
    return r


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A synthetic index store with ONE entry, in the scope the repo derives to."""
    s = tmp_path / "store"
    (s / SCOPE).mkdir(parents=True)
    _write(s / SCOPE / f"{SERVICE}.md", f"""\
---
service: {SERVICE}
scope: {SCOPE}
sensitivity: public
---

## What it is
A synthetic entry.

## Pointers
- manage-* skill: manage-{SERVICE}

## Nuance / work-history
- 2026-01-02 the readiness probe lies for 40s after a rollout
""")
    return s


def _roots(*paths: Path) -> tuple[tuple[str, str], ...]:
    return tuple((str(p), "--repo") for p in paths)


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        if p.is_file():
            h.update(p.read_bytes())
        h.update(b"\1")
    return h.hexdigest()


# =============================================================================
# 🔴 THE READ-ONLY CONTRACT
# =============================================================================


class TestReconNeverWrites:
    """The store is curated, client-confidential and has no off-machine backup.
    The only writers are the two confirm-gated ones in the skills."""

    def test_the_module_has_no_write_call_at_all(self) -> None:
        """🔴 A STRUCTURAL claim, not a behavioural sample. A behavioural test
        can only prove the paths it exercised did not write; this proves there is
        no write path to exercise."""
        src = (ROOT / "scripts" / "lib" / "service_recon.py").read_text(encoding="utf-8")
        for banned in ("write_text(", "open(", "os.remove", "shutil.", "mkdir(",
                       "unlink(", "rename(", "os.replace"):
            assert banned not in src, f"service_recon.py reaches for {banned!r}"

    @pytest.mark.parametrize("live", [False, True])
    def test_a_full_recon_leaves_the_store_byte_identical(
        self, repo: Path, store: Path, live: bool
    ) -> None:
        before = _tree_hash(store)
        sr.recon(SERVICE, repos=[str(repo)], store_root=store, live=live, context="ctx-a")
        assert _tree_hash(store) == before

    def test_a_FAILING_recon_leaves_the_store_byte_identical(self, store: Path) -> None:
        """The failure paths are where a write would hide — they are the ones
        nobody re-reads."""
        before = _tree_hash(store)
        sr.recon("nothing-here", repos=["/nonexistent/root"], store_root=store)
        sr.recon(SERVICE, repos=[], store_root=store, env={}, cwd="/nonexistent")
        assert _tree_hash(store) == before

    def test_the_repo_is_left_byte_identical_too(self, repo: Path, store: Path) -> None:
        """`git ls-files` and `git log` are reads. A `git` verb that was not
        would show up here."""
        before = _tree_hash(repo)
        sr.recon(SERVICE, repos=[str(repo)], store_root=store)
        assert _tree_hash(repo) == before

    def test_EVERY_subprocess_a_full_recon_makes_is_read_only(
        self, repo: Path, store: Path, monkeypatch
    ) -> None:
        """🔴 AN ASSERTED LEDGER OVER THE SEAM, not over one component.

        The two guards above are behavioural: they prove THESE inputs wrote
        nothing. That is a claim about the paths exercised, and a `git` verb
        reachable only on some other input would pass both — a tree hash cannot
        see a `git fetch`, a `git gc` or a `git config --global`, none of which
        touch the working tree at all.

        So this pins the RELATIONSHIP: capture every argv the recon hands to a
        subprocess and assert the whole set. It fails when the set GROWS (a new
        command appeared) or SHRINKS (a step stopped running), which is the shape
        `claude/RULES.md` asks for at a seam nobody owns.
        """
        seen: list[tuple[str, ...]] = []
        real = sr._run

        def spy(argv, **kw):
            seen.append(tuple(argv))
            return real(argv, **kw)

        monkeypatch.setattr(sr, "_run", spy)
        sr.recon(SERVICE, repos=[str(repo)], store_root=store)

        assert seen, "no subprocess ran at all — the spy is wired to nothing"
        verbs = {a[0] for a in seen}
        assert verbs == {"git"}, f"a non-git binary was invoked: {verbs}"

        # The exact command set, both directions.
        subcommands = sorted({a[3] for a in seen if a[1] == "-C"})
        assert subcommands == ["log", "ls-files"], subcommands

        WRITING = {
            "add", "commit", "push", "fetch", "pull", "merge", "rebase", "reset",
            "checkout", "switch", "restore", "stash", "clean", "gc", "prune",
            "config", "remote", "tag", "branch", "worktree", "apply", "am", "mv",
            "rm", "cherry-pick", "revert", "init", "clone", "update-ref", "notes",
        }
        for argv in seen:
            assert not (WRITING & set(argv)), f"a writing git verb reached argv: {argv}"


# =============================================================================
# 🔴 NEVER A SILENT ZERO — "did not look" vs "looked and found nothing"
# =============================================================================


class TestTheTwoKindsOfZero:
    """An empty result cannot distinguish two mechanisms (claude/RULES.md), so
    the module is required to distinguish them for its reader."""

    def test_a_searched_root_with_no_match_is_no_match(self, repo: Path, store: Path) -> None:
        b = sr.recon("zzz-absent", repos=[str(repo)], store_root=store)
        assert b.locate.status == "no-match"
        assert b.locate.files_examined > 0, "a no-match must carry its denominator"
        assert b.exit_code == sr.EXIT_OK

    def test_a_root_that_could_not_be_examined_is_not_searched(self, store: Path) -> None:
        b = sr.recon("anything", repos=["/nonexistent/root/xyz"], store_root=store)
        assert b.locate.status == "not-searched"
        assert b.locate.files_examined == 0
        assert b.exit_code == sr.EXIT_NOTHING_EXAMINED

    def test_the_two_zeros_have_DIFFERENT_exit_codes(self, repo: Path, store: Path) -> None:
        """🔴 The discriminator a caller can branch on without parsing prose.
        Asserted as a PAIR: two statuses that both mapped to 0 would each pass a
        single-sided test."""
        looked = sr.recon("zzz-absent", repos=[str(repo)], store_root=store)
        blind = sr.recon("zzz-absent", repos=["/nonexistent/root/xyz"], store_root=store)
        assert looked.exit_code != blind.exit_code
        assert (looked.exit_code, blind.exit_code) == (sr.EXIT_OK, sr.EXIT_NOTHING_EXAMINED)

    def test_no_roots_at_all_is_not_searched_not_no_match(self, store: Path) -> None:
        """A host with no `$HOMELAB`/`$DATAPACKET` and a cwd that is not a repo
        searched NOTHING. Reporting that as `no-match` would be a confident
        finding produced by the question."""
        b = sr.recon(SERVICE, repos=[], store_root=store, env={}, cwd=None)
        assert b.locate.status == "not-searched"
        assert b.exit_code == sr.EXIT_NOTHING_EXAMINED

    def test_a_ref_that_normalizes_away_is_not_searched(self, repo: Path, store: Path) -> None:
        """`normalize_ref('---')` is `''`, and searching for `''` matches EVERY
        path. Nothing was asked, so nothing is reported as found."""
        b = sr.recon("---", repos=[str(repo)], store_root=store)
        assert b.token == ""
        assert b.locate.status == "not-searched"
        assert b.locate.total_matches == 0

    def test_every_root_appears_in_the_brief_including_absent_ones(self, repo: Path, store: Path) -> None:
        """A configured-but-absent checkout must be NAMED, not dropped — the
        whole point is that the brief can say which roots it did not look at."""
        b = sr.recon(SERVICE, repos=[str(repo), "/nonexistent/other"], store_root=store)
        statuses = {Path(r.path).name: r.status for r in b.locate.roots}
        assert statuses == {"ledger-repo": "searched", "other": "absent"}
        assert "other" in sr.render_brief(b)

    def test_the_render_names_the_absent_case_without_calling_it_a_finding(
        self, store: Path
    ) -> None:
        text = sr.render_brief(sr.recon("x", repos=["/nonexistent/q"], store_root=store))
        assert "NOT SEARCHED" in text
        assert "This is not a finding about the service." in text


# =============================================================================
# 🔴 REDACTION — three independent reasons, each reachable on its own
# =============================================================================


class TestSecretsNeverReachTheBrief:
    """The skill's rule is "mounted secrets (names only — never print secret
    contents)". Each reason below is exercised by a document that NO OTHER reason
    would catch, so a green here is not one guard firing three times."""

    def test_reason_1_a_credential_KEY_on_an_ordinary_document(self) -> None:
        knobs, _ = sr.extract_knobs("""\
kind: Deployment
spec:
  template:
    spec:
      containers:
        - env:
            - name: A
              password: hunter2-in-the-clear
""")
        vals = {k.path: k.value for k in knobs}
        assert vals["spec.template.spec.containers.env.password"] == sr.REDACTED
        assert "hunter2-in-the-clear" not in json.dumps(vals)

    def test_reason_2_a_Secret_document_whose_key_is_in_NO_ledger(self) -> None:
        """🔴 THE CASE REASON 1 CANNOT SEE. `db-conn-2` is not in
        `SECRETY_SUFFIXES` and never will be — a Secret's data keys are arbitrary
        operator-chosen strings. Without reason 2 this value prints verbatim."""
        assert "db-conn-2" not in sr.SECRETY_SUFFIXES
        knobs, _ = sr.extract_knobs("""\
apiVersion: v1
kind: Secret
metadata:
  name: front-desk-creds
type: Opaque
stringData:
  db-conn-2: postgres-super-secret-value
""")
        vals = {k.path: k.value for k in knobs}
        assert vals["stringData.db-conn-2"] == sr.REDACTED
        assert "postgres-super-secret-value" not in json.dumps(vals)

    def test_reason_3_a_userinfo_URL_under_an_ordinary_key(self) -> None:
        """🔴 THE CASE REASONS 1 AND 2 CANNOT SEE. `url` is deliberately NOT in
        the ledger (a chart repo URL is load-bearing recon signal) and this is a
        Deployment, not a Secret — so only value inspection catches it."""
        assert "url" not in sr.SECRETY_SUFFIXES
        knobs, _ = sr.extract_knobs("""\
kind: Deployment
spec:
  host: db.invalid
  url: postgres://svcuser:s3cr3tpw@db.invalid:5432/app
""")
        vals = {k.path: k.value for k in knobs}
        assert vals["spec.url"] == sr.REDACTED
        assert "s3cr3tpw" not in json.dumps(vals)
        # …and a URL WITHOUT userinfo still prints: the guard must not be a
        # blanket `url:` ban wearing a value check.
        plain, _ = sr.extract_knobs("kind: HelmRepository\nspec:\n  url: https://charts.invalid\n")
        assert {k.path: k.value for k in plain}["spec.url"] == "https://charts.invalid"

    def test_a_Secret_still_reports_its_IDENTITY(self) -> None:
        """🔴 Names are the POINT. Redacting `metadata.name` withheld exactly the
        pointer the skill asks for while the surrounding bookkeeping filled the
        block — measured on the first real run."""
        knobs, _ = sr.extract_knobs("""\
apiVersion: v1
kind: Secret
metadata:
  name: front-desk-creds
  namespace: front-desk
type: Opaque
stringData:
  password: nope
""")
        vals = {k.path: k.value for k in knobs}
        assert vals["metadata.name"] == "front-desk-creds"
        assert vals["metadata.namespace"] == "front-desk"
        assert vals["type"] == "Opaque"
        assert vals["stringData.password"] == sr.REDACTED

    def test_a_Secrets_bookkeeping_fields_are_DROPPED_not_printed_as_redacted(self) -> None:
        """Twelve lines of `<redacted>` sops bookkeeping answer nothing and are
        indistinguishable from twelve withheld answers."""
        knobs, _ = sr.extract_knobs("""\
apiVersion: v1
kind: Secret
metadata:
  name: front-desk-creds
stringData:
  password: nope
sops:
  mac: ENC[abc]
  version: 3.9.0
  lastmodified: "2026-01-01"
""")
        paths = {k.path for k in knobs}
        assert not any(p.startswith("sops") for p in paths), paths

    def test_redaction_survives_the_JSON_surface_too(self, tmp_path: Path) -> None:
        """A guard on the TEXT renderer alone is walkable by asking for --json."""
        r = tmp_path / "r"
        _write(r / "apps" / SERVICE / "secret.yaml",
               "apiVersion: v1\nkind: Secret\nmetadata:\n  name: n\n"
               "stringData:\n  token: THE-LEAKED-TOKEN\n")
        loc = sr.locate(SERVICE, _roots(r))
        b = sr.Brief(SERVICE, SERVICE, loc, sr.IndexResult("not-attempted"),
                     sr.config_for(loc), sr.GitResult("not-attempted"), sr.LiveResult("off"))
        assert "THE-LEAKED-TOKEN" not in json.dumps(sr.brief_json(b))
        assert "THE-LEAKED-TOKEN" not in sr.render_brief(b)


# =============================================================================
# 🔴 LOCATING — the matcher, and what it must NOT match
# =============================================================================


class TestLocate:
    def test_matching_is_COMPONENT_WISE_never_over_the_JOINED_path(
        self, tmp_path: Path
    ) -> None:
        """🔴 THE EXACT HAZARD, measured rather than imagined. The one-line
        implementation anyone would reach for is `token in normalize_ref(rel)` —
        and `normalize_ref` maps `/` to `-`, so a HYPHENATED token spans
        directories: `apps/external/dns.yaml` normalizes to
        `apps-external-dns.yaml`, which contains `external-dns`. The path has
        nothing to do with external-dns.

        (An earlier version of this test used `apps/red/isolation` vs `redis`.
        That fixture was VACUOUS — it matches under neither implementation — and
        the mutation battery is what exposed it: the raw-substring mutant
        SURVIVED. Do not re-derive it.)
        """
        token = "external-dns"
        rel = "apps/external/dns.yaml"
        assert token in res.normalize_ref(rel), "the fixture must exhibit the hazard"

        r = tmp_path / "r"
        _write(r / rel, "kind: X\n")
        scan = sr.scan_root(r, token)
        assert scan.matches == (), scan.matches
        assert scan.files_examined == 1, "the file WAS examined — this is a real zero"

    def test_and_a_genuine_component_hit_still_matches(self, tmp_path: Path) -> None:
        """The other side of the pair: the guard above must not be a matcher that
        never matches. A directory actually named for the service does hit."""
        r = tmp_path / "r"
        _write(r / "apps" / "external-dns" / "hr.yaml", "kind: X\n")
        assert sr.scan_root(r, "external-dns").matches == ("apps/external-dns/hr.yaml",)

    def test_the_token_goes_through_the_STORES_normalizer(self, tmp_path: Path) -> None:
        """One normalizer, not two: `external_dns` and `external-dns` are one
        thing in the index, so they must be one thing here."""
        r = tmp_path / "r"
        _write(r / "apps" / "external-dns" / "hr.yaml", "kind: HelmRelease\n")
        assert res.normalize_ref("external_dns") == "external-dns"
        assert sr.scan_root(r, res.normalize_ref("external_dns")).matches == (
            "apps/external-dns/hr.yaml",
        )

    def test_a_nested_checkout_is_pruned(self, tmp_path: Path) -> None:
        """🔴 Measured: two agent worktrees under `<repo>/.claude/worktrees/`
        turned 30 genuine matches into 85, topped by three COPIES of one file."""
        r = tmp_path / "r"
        _write(r / "apps" / SERVICE / "a.yaml", "kind: X\n")
        nested = r / ".claude" / "worktrees" / "wt"
        _write(nested / "apps" / SERVICE / "a.yaml", "kind: X\n")
        (nested / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        scan = sr.scan_root(r, SERVICE)
        assert scan.matches == (f"apps/{SERVICE}/a.yaml",), scan.matches

    def test_the_prune_sees_a_worktrees_git_FILE_not_only_a_directory(self, tmp_path: Path) -> None:
        """A worktree's `.git` is a FILE holding `gitdir:`. A prune written as
        `(d / '.git').is_dir()` misses every worktree — which is the case that
        was actually measured."""
        r = tmp_path / "r"
        wt = r / "wt"
        _write(wt / "apps" / SERVICE / "a.yaml", "kind: X\n")
        (wt / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
        assert (wt / ".git").is_file() and not (wt / ".git").is_dir()
        assert sr.scan_root(r, SERVICE).matches == ()

    def test_a_git_repo_is_scanned_by_ls_files_and_says_so(self, repo: Path) -> None:
        scan = sr.scan_root(repo, SERVICE)
        assert scan.method == "git-ls-files"
        assert scan.status == "searched"
        assert f"apps/{SERVICE}/deployment.yaml" in scan.matches

    def test_an_UNTRACKED_file_is_not_located(self, repo: Path) -> None:
        """🔴 THE REASON `ls-files` IS THE UNIVERSE. Measured on a real repo, the
        walk's top hit was a kubectl DISCOVERY CACHE: not config, not tracked (so
        the same recon gives two answers on two hosts) and carrying a real
        cluster IP into a brief people paste around."""
        _write(repo / ".kube" / "cache" / SERVICE / "serverresources.json", "{}")
        assert not any(".kube" in m for m in sr.scan_root(repo, SERVICE).matches)

    def test_a_non_repo_root_falls_back_to_the_walk_and_says_so(self, tmp_path: Path) -> None:
        r = tmp_path / "plain"
        _write(r / "apps" / SERVICE / "a.yaml", "kind: X\n")
        scan = sr.scan_root(r, SERVICE)
        assert scan.method == "walk"
        assert scan.status == "searched"
        assert scan.matches == (f"apps/{SERVICE}/a.yaml",)

    def test_the_method_is_reported_in_the_brief(self, repo: Path, store: Path) -> None:
        """Two methods see different file sets, so a match count means different
        things under each. A reader who cannot tell them apart cannot read it."""
        text = sr.render_brief(sr.recon(SERVICE, repos=[str(repo)], store_root=store))
        assert "git-ls-files" in text

    def test_an_ownership_tie_is_reported_never_broken_silently(self, tmp_path: Path) -> None:
        a, b = tmp_path / "alpha", tmp_path / "beta"
        for r in (a, b):
            _write(r / "apps" / SERVICE / "a.yaml", "kind: X\n")
        loc = sr.locate(SERVICE, _roots(a, b))
        assert loc.status == "hits"
        assert loc.owner_tied_with == (str(b),)

    def test_a_clear_winner_reports_NO_tie(self, tmp_path: Path) -> None:
        """The negative half — without it, a `owner_tied_with` hardwired to
        non-empty would pass the test above."""
        a, b = tmp_path / "alpha", tmp_path / "beta"
        _write(a / "apps" / SERVICE / "a.yaml", "kind: X\n")
        _write(a / "apps" / SERVICE / "b.yaml", "kind: X\n")
        _write(b / "apps" / SERVICE / "a.yaml", "kind: X\n")
        loc = sr.locate(SERVICE, _roots(a, b))
        assert loc.owner == str(a)
        assert loc.owner_tied_with == ()


class TestSearchRoots:
    def test_explicit_repos_suppress_the_env_handles(self) -> None:
        got = sr.search_roots(["/x"], env={"HOMELAB": "/h", "DATAPACKET": "/d"}, cwd="/c")
        assert [p for p, _ in got] == ["/x"]

    def test_the_env_handles_and_cwd_are_the_default(self) -> None:
        got = sr.search_roots([], env={"HOMELAB": "/h", "DATAPACKET": "/d"}, cwd="/c")
        assert got == (("/h", "env:HOMELAB"), ("/d", "env:DATAPACKET"), ("/c", "cwd"))

    def test_an_unset_handle_contributes_nothing_and_does_not_crash(self) -> None:
        assert sr.search_roots([], env={}, cwd=None) == ()

    def test_duplicates_collapse_keeping_the_FIRST_origin(self) -> None:
        got = sr.search_roots([], env={"HOMELAB": "/same", "DATAPACKET": "/same"}, cwd="/same")
        assert got == (("/same", "env:HOMELAB"),)

    def test_no_client_path_is_hardcoded_in_the_module(self) -> None:
        """🔴 devrc is PUBLIC. The roots come from env handles; a literal
        workspace path baked in here would both leak and be wrong on a host that
        does not have that checkout."""
        src = (ROOT / "scripts" / "lib" / "service_recon.py").read_text(encoding="utf-8")
        assert "/home/zach" not in src
        assert "workspace/" not in src


# =============================================================================
# 🔴 THE INDEX READ — through the reader, not a `cat`
# =============================================================================


class TestIndexRead:
    def test_a_hit_surfaces_pointers_and_nuance_labelled_from_index(
        self, repo: Path, store: Path
    ) -> None:
        b = sr.recon(SERVICE, repos=[str(repo)], store_root=store)
        assert b.index.status == "hit"
        assert b.index.scope == SCOPE
        assert "manage-roster" in b.index.pointers
        assert "readiness probe lies" in b.index.nuance
        assert "from index" in sr.render_brief(b)

    def test_the_recalled_status_constant_comes_from_the_READER(self) -> None:
        """🔴 THE SILENT-MISS GUARD. `recall` returns `"recalled"`, not `"ok"`. A
        literal here that guessed wrong would classify EVERY hit as a miss and
        print `nothing recorded under that ref yet` over a real entry — a failure
        with no error and no red test anywhere else."""
        assert sr.RECALLED_STATUS == "recalled"
        assert sr.RECALLED_STATUS in rc.STATUS_PRECEDENCE

    def test_a_miss_is_a_status_not_an_error(self, repo: Path, store: Path) -> None:
        b = sr.recon(OTHER, repos=[str(repo)], store_root=store)
        assert b.index.status == "ref-absent"
        assert b.index.scope == SCOPE

    def test_a_missing_store_is_reported_not_raised(self, repo: Path, tmp_path: Path) -> None:
        b = sr.recon(SERVICE, repos=[str(repo)], store_root=tmp_path / "no-store-here")
        assert b.index.status == "store-missing"
        assert b.index.detail

    def test_an_AMBIGUOUS_ref_never_picks(self, repo: Path, tmp_path: Path) -> None:
        """The resolver's rule, carried through: ">1 in a tier → never pick"."""
        s = tmp_path / "amb"
        (s / SCOPE).mkdir(parents=True)
        for kind in ("service", "process"):
            _write(s / SCOPE / f"{SERVICE}.{kind}.md",
                   f"---\nservice: {SERVICE}\nscope: {SCOPE}\nkind: {kind}\n"
                   f"sensitivity: public\n---\n\n## Pointers\n- a\n")
        b = sr.recon(SERVICE, repos=[str(repo)], store_root=s)
        assert b.index.status == "ref-ambiguous"
        assert len(b.index.candidates) == 2
        assert b.index.pointers == "", "an ambiguous ref must surface NO body"
        assert "AMBIGUOUS" in sr.render_brief(b)

    def test_no_owning_repo_means_the_index_was_NOT_ATTEMPTED(self, store: Path) -> None:
        """Distinguished from `ref-absent`: no scope was derivable, so the store
        was never asked. Reporting a miss would be a finding nobody measured."""
        b = sr.recon(SERVICE, repos=["/nonexistent/root"], store_root=store)
        assert b.index.status == "not-attempted"

    def test_the_sensitivity_fold_is_carried_through(self, repo: Path, tmp_path: Path) -> None:
        """🔴 Fail-safe: an entry with NO `sensitivity:` is client-confidential,
        never public. A recon brief that mislabels it invites a paste."""
        s = tmp_path / "s2"
        (s / SCOPE).mkdir(parents=True)
        _write(s / SCOPE / f"{SERVICE}.md",
               f"---\nservice: {SERVICE}\nscope: {SCOPE}\n---\n\n## Pointers\n- a\n")
        b = sr.recon(SERVICE, repos=[str(repo)], store_root=s)
        assert b.index.sensitivity == rc.SENSITIVITY_FAIL_SAFE
        assert b.index.sensitivity != "public"


# =============================================================================
# 🔴 CONFIG — the knob extractor and its stated limits
# =============================================================================


class TestKnobExtraction:
    def test_dotted_paths_track_indentation(self) -> None:
        got = dict((p, v) for _d, p, v in sr.dotted_paths(DEPLOYMENT))
        assert got["metadata.namespace"] == NAMESPACE
        assert got["spec.replicas"] == "4"
        assert got["spec.template.spec.containers.image"] == f"registry.invalid/{SERVICE}:9.3.1"

    def test_documents_are_separated(self) -> None:
        pairs = sr.dotted_paths("kind: A\nx: 1\n---\nkind: B\ny: 2\n")
        docs = {d for d, _p, _v in pairs}
        assert docs == {0, 1}
        assert ("kind" in [p for _d, p, _v in pairs])

    def test_the_same_knob_is_found_at_three_different_PATHS(self) -> None:
        """🔴 Why the ledger matches the LAST segment. `replicas` sits at
        `spec.replicas`, `spec.values.replicaCount` and bare `replicaCount`
        depending on whether it is a Deployment, a HelmRelease or a values file.
        A full-path ledger would cover one and miss two, silently."""
        for text, want in (
            ("kind: Deployment\nspec:\n  replicas: 2\n", "spec.replicas"),
            ("kind: HelmRelease\nspec:\n  values:\n    replicaCount: 2\n",
             "spec.values.replicaCount"),
            ("replicaCount: 2\n", "replicaCount"),
        ):
            paths = {k.path for k in sr.extract_knobs(text)[0]}
            assert want in paths, (text, paths)

    def test_truncation_is_COUNTED_never_silent(self) -> None:
        text = "kind: X\n" + "".join(f"a{i}:\n  replicas: {i}\n" for i in range(12))
        knobs, dropped = sr.extract_knobs(text, limit=4)
        assert len(knobs) == 4
        assert dropped == 9, dropped  # 12 replicas + the kind line, minus 4 shown
        assert "more knob(s)" in sr.render_brief(
            sr.Brief("s", "s", sr.LocateResult("hits", "s", (), owner="/x"),
                     sr.IndexResult("not-attempted"),
                     sr.ConfigResult("extracted", 1, 1, (sr.ManifestKnobs("f.yaml", knobs, dropped),)),
                     sr.GitResult("not-attempted"), sr.LiveResult("off"))
        )

    def test_a_long_value_is_truncated_WITH_A_MARK(self) -> None:
        long = "x" * (sr.MAX_VALUE_CHARS + 40)
        knobs, _ = sr.extract_knobs(f"kind: Deployment\nspec:\n  image: {long}\n")
        v = {k.path: k.value for k in knobs}["spec.image"]
        assert v.endswith("…") and len(v) == sr.MAX_VALUE_CHARS + 1

    def test_comments_and_blank_lines_are_skipped(self) -> None:
        got = dict((p, v) for _d, p, v in sr.dotted_paths(
            "# a note\nkind: Deployment\n\n  \nspec:\n  # inner\n  replicas: 3\n"))
        assert got == {"kind": "Deployment", "spec.replicas": "3"}
        assert not any(k.startswith("#") for k in got)

    def test_a_value_that_is_only_a_comment_is_not_a_value(self) -> None:
        assert sr.dotted_paths("kind: # nothing here\n") == []


class TestConfigSection:
    def test_manifests_are_ranked_so_kustomization_survives_the_cap(self, tmp_path: Path) -> None:
        """🔴 Measured: alphabetically, `configmap`/`deployment` win and
        `kustomization.yaml` — the file saying what the service is COMPOSED of —
        falls off the end of a capped read."""
        r = tmp_path / "r"
        for name in ("zzz.yaml", "aaa.yaml", "kustomization.yaml", "helmrelease.yaml"):
            _write(r / "apps" / SERVICE / name, "kind: X\n")
        cfg = sr.config_for(sr.locate(SERVICE, _roots(r)), file_limit=2)
        assert [f.file for f in cfg.files] == [
            f"apps/{SERVICE}/kustomization.yaml", f"apps/{SERVICE}/helmrelease.yaml",
        ]
        assert cfg.manifests_seen == 4, "the cap must not hide the denominator"

    def test_matched_but_non_manifest_paths_are_reported_as_such(self, tmp_path: Path) -> None:
        """A service that matched only a `.go` file is a real finding — and it is
        NOT the same as matching nothing."""
        r = tmp_path / "r"
        _write(r / "cmd" / SERVICE / "main.go", "package main\n")
        cfg = sr.config_for(sr.locate(SERVICE, _roots(r)))
        assert cfg.status == "no-manifests"
        assert "1 path(s) matched" in cfg.detail

    def test_nothing_searched_means_config_was_NOT_ATTEMPTED(self) -> None:
        cfg = sr.config_for(sr.LocateResult("not-searched", "x", ()))
        assert cfg.status == "not-attempted"
        assert cfg.status != "no-manifests"

    def test_an_unreadable_manifest_is_named_not_dropped(self, tmp_path: Path) -> None:
        r = tmp_path / "r"
        p = _write(r / "apps" / SERVICE / "a.yaml", "kind: X\n")
        loc = sr.locate(SERVICE, _roots(r))
        p.unlink()
        cfg = sr.config_for(loc)
        assert cfg.files[0].detail.startswith("unreadable:")


# =============================================================================
# 🔴 RECENT CHANGES
# =============================================================================


class TestGitLog:
    def test_commits_are_returned_for_the_located_directory(self, repo: Path) -> None:
        g = sr.git_log(sr.locate(SERVICE, _roots(repo)))
        assert g.status == "commits"
        assert g.commits[0].subject == f"feat({SERVICE}): bump to 9.3.1"
        assert g.pathspec == (f"apps/{SERVICE}",)

    def test_a_bump_or_revert_is_FLAGGED(self, repo: Path) -> None:
        """A recent revert/bump is usually WHY someone is looking."""
        g = sr.git_log(sr.locate(SERVICE, _roots(repo)))
        assert g.commits[0].moved is True
        assert "MOVED" in sr.render_brief(
            sr.Brief(SERVICE, SERVICE, sr.locate(SERVICE, _roots(repo)),
                     sr.IndexResult("not-attempted"), sr.ConfigResult("no-manifests"),
                     g, sr.LiveResult("off")))

    def test_an_ordinary_commit_is_NOT_flagged(self) -> None:
        """The negative half: `moved` hardwired to True passes the test above."""
        assert sr.Commit("abc", "2026-01-01", "docs: describe the widget").moved is False

    def test_a_pathspec_is_pruned_to_its_shallowest_ancestors(self, tmp_path: Path) -> None:
        """`apps/x` and `apps/x/sub` are one pathspec entry, not two — otherwise
        the printed query misrepresents itself."""
        r = tmp_path / "r"
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        _write(r / "apps" / SERVICE / "a.yaml", "kind: X\n")
        _write(r / "apps" / SERVICE / "sub" / "b.yaml", "kind: X\n")
        _git(r, "add", "apps")
        _git(r, "commit", "-qm", "add")
        g = sr.git_log(sr.locate(SERVICE, _roots(r)))
        assert g.pathspec == (f"apps/{SERVICE}",)

    def test_nothing_located_means_NOT_ATTEMPTED(self) -> None:
        g = sr.git_log(sr.LocateResult("no-match", "x", ()))
        assert g.status == "not-attempted"
        assert g.status != "no-commits"

    def test_a_non_repo_root_is_git_failed_not_no_commits(self, tmp_path: Path) -> None:
        r = tmp_path / "plain"
        _write(r / "apps" / SERVICE / "a.yaml", "kind: X\n")
        g = sr.git_log(sr.locate(SERVICE, _roots(r)))
        assert g.status == "git-failed"
        assert g.detail


class TestMultiDirectoryNote:
    def test_the_note_fires_at_the_threshold(self, tmp_path: Path, store: Path) -> None:
        r = tmp_path / "r"
        r.mkdir()
        _git(r, "init", "-q", "-b", "main")
        for n in range(sr.UMBRELLA_PATHS):
            _write(r / "apps" / f"app{n}" / f"{SERVICE}.yaml", "kind: X\n")
        _git(r, "add", "apps")
        _git(r, "commit", "-qm", "add")
        b = sr.recon(SERVICE, repos=[str(r)], store_root=store)
        assert any("MULTI-DIRECTORY" in n for n in b.notes), b.notes

    def test_the_note_does_NOT_fire_below_it(self, repo: Path, store: Path) -> None:
        """The negative half. `apps/roster` is one directory."""
        b = sr.recon(SERVICE, repos=[str(repo)], store_root=store)
        assert not any("MULTI-DIRECTORY" in n for n in b.notes), b.notes


# =============================================================================
# 🔴 LIVE STATE — opt-in, never inferred, read-only
# =============================================================================


class TestLiveIsOptIn:
    def test_the_default_runs_NO_probe(self, repo: Path, store: Path) -> None:
        """🔴 The headline decision: live was 124 of the 359 measured Bash calls."""
        b = sr.recon(SERVICE, repos=[str(repo)], store_root=store)
        assert b.live.status == "off"
        assert b.live.probes == ()

    def test_live_without_a_context_probes_NOTHING(self) -> None:
        """🔴 There is deliberately no default KUBECONFIG on these hosts so a bare
        `kubectl` cannot reach prod. Inferring one here would undo that."""
        calls: list[tuple] = []

        def spy(argv, **kw):
            calls.append(tuple(argv))
            return 0, ""

        got = sr.live_state(sr.ConfigResult("no-manifests"), enabled=True, runner=spy)
        assert got.status == "no-context"
        assert calls == [], "a probe ran without a context"

    def test_live_with_no_namespace_probes_NOTHING(self) -> None:
        calls: list[tuple] = []

        def spy(argv, **kw):
            calls.append(tuple(argv))
            return 0, ""

        got = sr.live_state(sr.ConfigResult("no-manifests"), enabled=True,
                            context="ctx-a", runner=spy)
        assert got.status == "no-namespace"
        assert calls == []

    def test_the_namespace_comes_from_the_located_manifests(self) -> None:
        knobs, _ = sr.extract_knobs(DEPLOYMENT)
        cfg = sr.ConfigResult("extracted", 1, 1, (sr.ManifestKnobs("d.yaml", knobs),))
        got = sr.live_state(cfg, enabled=True, context="ctx-a",
                            runner=lambda argv, **kw: (0, "ok"))
        assert got.namespace == NAMESPACE
        assert got.status == "ran"

    def test_every_probe_is_READ_ONLY_and_context_scoped(self) -> None:
        """🔴 AN ASSERTED LEDGER, failing when the set GROWS or SHRINKS. A recon
        tool that acquired a `delete`, `apply`, `patch` or `exec` would be a
        different tool."""
        seen: list[tuple[str, ...]] = []

        def spy(argv, **kw):
            seen.append(tuple(argv))
            return 0, ""

        knobs, _ = sr.extract_knobs(DEPLOYMENT)
        sr.live_state(sr.ConfigResult("extracted", 1, 1, (sr.ManifestKnobs("d.yaml", knobs),)),
                      enabled=True, context="ctx-a", runner=spy)
        assert len(seen) == 3, seen
        assert [a[0] for a in seen] == ["kubectl", "kubectl", "flux"]
        for argv in seen:
            assert "get" in argv, argv
            assert "--context" in argv and "ctx-a" in argv, argv
            assert "-n" in argv and NAMESPACE in argv, argv
            assert not ({"delete", "apply", "patch", "edit", "exec", "create",
                         "replace", "scale", "annotate", "label", "cordon",
                         "drain", "rollout"} & set(argv)), argv

    def test_every_probe_failing_is_FAILED_not_a_clean_ran(self) -> None:
        got = sr.live_state(
            sr.ConfigResult("no-manifests"), enabled=True, context="ctx-a",
            namespace=NAMESPACE, runner=lambda argv, **kw: (1, "connection refused"))
        assert got.status == "failed"
        assert "UNVERIFIED" in got.detail

    @pytest.mark.parametrize("status", ["off", "no-context", "no-namespace", "failed"])
    def test_every_non_ran_status_says_UNVERIFIED_in_the_brief(self, status: str) -> None:
        """The skill's provenance rule: never present a non-observation as one."""
        b = sr.Brief("s", "s", sr.LocateResult("no-match", "s", ()),
                     sr.IndexResult("not-attempted"), sr.ConfigResult("no-manifests"),
                     sr.GitResult("not-attempted"), sr.LiveResult(status))
        assert "live state is UNVERIFIED" in sr.render_brief(b)


# =============================================================================
# 🔴 THE CLI
# =============================================================================


class TestCli:
    def test_a_static_run_exits_ok_and_prints_a_brief(
        self, repo: Path, store: Path, capsys, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo)
        code = sr.main([SERVICE, "--repo", str(repo), "--store", str(store)])
        out = capsys.readouterr().out
        assert code == sr.EXIT_OK
        assert f"service: {SERVICE}" in out
        assert "provenance:" in out

    def test_json_carries_every_section_status(
        self, repo: Path, store: Path, capsys, monkeypatch
    ) -> None:
        monkeypatch.chdir(repo)
        assert sr.main([SERVICE, "--repo", str(repo), "--store", str(store), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) >= {"locate", "index", "config", "git", "live"}
        for section in ("locate", "index", "config", "git", "live"):
            assert payload[section]["status"], f"{section} has no status"

    def test_context_without_live_is_a_usage_error(self, capsys) -> None:
        """A `--context` that silently probed nothing is the shape where an
        operator believes they saw live state and did not."""
        assert sr.main([SERVICE, "--context", "ctx-a"]) == sr.EXIT_USAGE
        assert "add --live" in capsys.readouterr().err

    @pytest.mark.parametrize("flag", ["--files", "--knobs", "--log"])
    def test_a_nonpositive_limit_is_a_usage_error(self, flag: str, capsys) -> None:
        assert sr.main([SERVICE, flag, "0"]) == sr.EXIT_USAGE
        assert "must be >= 1" in capsys.readouterr().err

    def test_the_did_not_look_exit_code_reaches_the_CLI(self, capsys) -> None:
        assert sr.main(["x", "--repo", "/nonexistent/zz"]) == sr.EXIT_NOTHING_EXAMINED
        assert "NOT SEARCHED" in capsys.readouterr().out


# =============================================================================
# 🔴 POSITIVE CONTROL ON THIS FILE'S OWN INSTRUMENT
# =============================================================================


class TestTheHarnessCanObserveAChange:
    """A tree hash that never moves is indistinguishable from one wired to a
    constant; a redaction assertion that can never see a leak is worse than none.
    """

    def test_the_tree_hash_MOVES_for_a_changed_store(self, store: Path) -> None:
        before = _tree_hash(store)
        (store / SCOPE / "new.md").write_text("x\n", encoding="utf-8")
        assert _tree_hash(store) != before

    def test_the_leak_assertion_CAN_fail(self) -> None:
        """Feed the extractor a value that must NOT be redacted and watch it come
        through — otherwise `secret not in output` is satisfied by an extractor
        wired to nothing."""
        knobs, _ = sr.extract_knobs("kind: Deployment\nspec:\n  replicas: 7\n")
        assert {k.path: k.value for k in knobs}["spec.replicas"] == "7"
        assert not any(k.redacted for k in knobs)
