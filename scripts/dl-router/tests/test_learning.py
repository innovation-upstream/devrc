"""The learning rule: only the DISCRIMINATING signal, and never globally.

The regression this file exists for. On the first forum download, `/learn`
wrote an alias for every captured subject phrase — which on that page meant a
forum section name and two other posters' usernames — plus one at GLOBAL scope,
so anything carrying that username on ANY site would have auto-filed into a
stranger's directory at alias confidence. Four rows had to be deleted by hand,
and nothing in the system had surfaced them.

`App.learn` is HTTP-free by design, so these drive it directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as config_mod  # noqa: E402
import server as S  # noqa: E402
from conftest import SAMPLE_DIRS  # noqa: E402
from fetcher import Fetcher  # noqa: E402
from matcher import (  # noqa: E402
    CHROME_DIR_SPREAD, DISCORD_SITE, KIND_CATEGORY, KIND_PERFORMER,
    MIN_ALIAS_KEY_LEN, MatchContext, discord_alias_key, thread_alias_key,
)

CHANNEL = "119283746551234567"
CDN = f"https://cdn.discordapp.com/attachments/{CHANNEL}/998877665544332211/clip.mp4"
THREAD = "https://someforum.test/threads/aster-vale-collection.481920/"

# "Jane Doe" is a person; "acme-studio" collects unattributed material.
KINDS_TOML = """\
performer = ["Jane Doe", "john-smith", "Mary_Major", "Aster Vale"]
category = ["acme-studio"]
"""


class Spawner:
    def __call__(self, argv, cwd=None):
        class P:
            def poll(self_inner):
                return None

            def terminate(self_inner):
                pass
        return P()


@pytest.fixture
def kinded_app(tmp_path, library, store, dir_index, file_index, clock):
    path = tmp_path / "dirs.toml"
    path.write_text(KINDS_TOML, encoding="utf-8")
    data = config_mod._deep_merge(config_mod.DEFAULTS, {
        "library_root": str(library), "host": "127.0.0.1", "port": 0})
    cfg = config_mod.Config(data, path=tmp_path / "config.toml",
                            state_dir=tmp_path / "state",
                            token_file=tmp_path / "token", dirs_file=path)
    return S.App(cfg, store=store, dir_index=dir_index, file_index=file_index,
                 fetcher=Fetcher(library, runner=Spawner(), clock=clock),
                 clock=clock)


def forum_context(tags):
    """The shape that produced the four bad rows: a forum page whose tag list is
    section names and other posters' usernames."""
    return {
        "url": "https://someforum.test/attachments/opaque-9f2.mp4",
        "filename": "opaque-9f2.mp4",
        "page": {"url": THREAD, "site": "someforum.test",
                 "title": "Aster Vale Collection | Some Forum", "tags": tags},
    }


# --- the regression --------------------------------------------------------- #
def test_a_tag_never_becomes_an_alias_for_a_performer_directory(kinded_app):
    chrome = ["General Discussion", "poster_1988", "uploader42"]
    out = kinded_app.learn({"context": forum_context(chrome),
                            "chosenDir": "Aster Vale", "confirmed": True})
    written = {w["key"] for w in out["written"]}
    for tag in chrome:
        from matcher import norm_key
        assert norm_key(tag) not in written, tag
    assert kinded_app.store.alias("generaldiscussion", "someforum.test") is None
    assert kinded_app.store.alias("poster1988", "") is None


def test_learning_never_writes_a_global_alias(kinded_app):
    """A global alias applies on every site at once — the widest blast radius
    the store has, and the least evidence supports it. `dl-route alias set
    --site '*'` still exists for a deliberate one."""
    for chosen, ctx in (("Aster Vale", forum_context(["Aster Vale"])),
                        ("acme-studio", forum_context(["Acme Studio"])),
                        ("Jane Doe", {"url": CDN, "page": {}})):
        kinded_app.learn({"context": ctx, "chosenDir": chosen,
                          "confirmed": True})
    assert all(site for (_key, site) in kinded_app.store.alias_map())


def test_a_performer_learns_the_thread_slug_and_the_title_subject(kinded_app):
    out = kinded_app.learn({"context": forum_context(["General Discussion"]),
                            "chosenDir": "Aster Vale", "confirmed": True})
    written = {(w["key"], w["site"]) for w in out["written"]}
    assert (thread_alias_key("aster vale collection"), "someforum.test") in written
    # The de-branded title, NOT the raw "... | Some Forum".
    assert ("astervalecollection", "someforum.test") in written


def test_a_discord_download_learns_its_channel_and_nothing_else(kinded_app):
    """Six of nine real downloads looked exactly like this: no page context at
    all. The channel id is the only thing there is, and it is enough."""
    out = kinded_app.learn({
        "context": {"url": CDN, "filename": "clip.mp4",
                    "page": {"tags": [], "title": "", "url": ""}},
        "chosenDir": "Jane Doe", "confirmed": True})
    assert [(w["key"], w["site"]) for w in out["written"]] == [
        (discord_alias_key(CHANNEL), DISCORD_SITE)]


def test_the_learned_channel_then_auto_files_the_next_download(kinded_app):
    kinded_app.learn({"context": {"url": CDN, "page": {}},
                      "chosenDir": "Jane Doe", "confirmed": True})
    res = kinded_app.match({"url": CDN.replace("clip.mp4", "another.mp4"),
                            "filename": "another.mp4", "page": {}})
    assert res["dir"] == "Jane Doe"
    assert res["auto"] is True


# --- the category rule ------------------------------------------------------ #
def test_a_category_directory_does_learn_a_tag_but_only_site_scoped(kinded_app):
    out = kinded_app.learn({"context": forum_context(["Field Recordings"]),
                            "chosenDir": "acme-studio", "confirmed": True})
    assert ("fieldrecordings", "someforum.test") in {
        (w["key"], w["site"]) for w in out["written"]}
    assert kinded_app.store.alias("fieldrecordings", "") is None


def test_a_category_tag_is_not_learned_without_an_explicit_confirmation(
        kinded_app):
    out = kinded_app.learn({"context": forum_context(["Field Recordings"]),
                            "chosenDir": "acme-studio"})
    assert not [w for w in out["written"] if w["source"] == "tag"]
    assert out["skipped"]


def test_a_category_confirmation_is_capped(kinded_app):
    """A tag list can hold 64 entries. The user confirmed one directory, not
    sixty-four synonyms for it."""
    many = [f"Tag Number {n}" for n in range(20)]
    out = kinded_app.learn({"context": forum_context(many),
                            "chosenDir": "acme-studio", "confirmed": True})
    assert len([w for w in out["written"] if w["source"] == "tag"]) \
        <= S.MAX_LEARNED_TAGS


def test_a_suspicious_tag_is_refused_and_reported(kinded_app):
    """The screen catches the failure CLASS, not four specific strings."""
    out = kinded_app.learn({"context": forum_context(["Some Forum", "JD"]),
                            "chosenDir": "acme-studio", "confirmed": True})
    assert not [w for w in out["written"] if w["source"] == "tag"]
    reasons = " ".join(s["why"] for s in out["skipped"])
    assert "site name" in reasons and "shorter than" in reasons


def test_a_tag_seen_on_many_directories_stops_being_learnable(kinded_app):
    """Data-driven, from the labelled examples the router already stores: a
    subject tag appears on ONE directory's pages; a section name appears on
    everything, so it accumulates spread."""
    for chosen in ("Jane Doe", "john-smith"):
        kinded_app.learn({"context": forum_context(["Common Section"]),
                          "chosenDir": chosen, "confirmed": True})
    out = kinded_app.learn({"context": forum_context(["Common Section"]),
                            "chosenDir": "acme-studio", "confirmed": True})
    assert kinded_app.store.alias("commonsection", "someforum.test") is None
    assert any("different directories" in s["why"] for s in out["skipped"])


# --- provenance + kinds ----------------------------------------------------- #
def test_every_learned_alias_records_what_produced_it(kinded_app):
    kinded_app.learn({"context": {"url": CDN, "page": {}},
                      "chosenDir": "Jane Doe", "confirmed": True})
    row = kinded_app.store.alias_rows()[0]
    assert row["source"] == "discord-channel"
    assert CHANNEL in row["evidence"]
    assert row["hits"] == 1


def test_mkdir_records_the_kind_the_picker_asked_for(kinded_app):
    out = kinded_app.mkdir({"name": "Aster Nightingale",
                            "kind": KIND_PERFORMER})
    assert out["kind"] == KIND_PERFORMER
    assert kinded_app.dir_kinds().kind("Aster Nightingale") == KIND_PERFORMER


def test_mkdir_without_a_kind_leaves_the_directory_unclassified(kinded_app):
    """...and an unclassified directory never auto-files, which is why the
    picker asks."""
    out = kinded_app.mkdir({"name": "Aster Nightingale"})
    assert out["kind"] == "unknown"


def test_mkdir_refuses_an_invented_kind(kinded_app):
    from safety import UnsafeName
    with pytest.raises(UnsafeName):
        kinded_app.mkdir({"name": "Aster Nightingale", "kind": "performer!"})


def test_the_snapshot_carries_each_directory_kind(kinded_app):
    """The extension's cached fallback matcher applies the same gate, or a
    sidecar timeout would auto-file into a category the sidecar would have
    asked about."""
    snap = kinded_app.dirs_snapshot()
    kinds = {d["name"]: d["kind"] for d in snap["dirs"]}
    assert kinds["Jane Doe"] == KIND_PERFORMER
    assert kinds["acme-studio"] == KIND_CATEGORY
    assert kinds["other"] == "unknown"
    assert set(kinds) == set(SAMPLE_DIRS)


def test_healthz_reports_how_many_directories_are_unclassified(kinded_app):
    out = kinded_app.healthz()
    assert out["dirKinds"]["unknown"] >= 1
    assert out["dirsFile"]["present"] is True


def test_a_category_match_asks_even_from_a_confirmed_identity_alias(kinded_app):
    """KNOWN CONSEQUENCE, recorded on purpose: a confirmed Discord channel
    pointing at a CATEGORY directory keeps asking forever. That is the rule as
    specified — a category is always confirmed — and it is flagged in the PR
    body rather than quietly excepted here."""
    kinded_app.learn({"context": {"url": CDN, "page": {}},
                      "chosenDir": "acme-studio", "confirmed": True})
    res = kinded_app.match({"url": CDN, "filename": "clip.mp4", "page": {}})
    assert res["dir"] == "acme-studio"
    assert res["confidence"] == pytest.approx(1.0)
    assert res["auto"] is False


def test_the_context_still_lands_in_the_example_log(kinded_app):
    kinded_app.learn({"context": forum_context(["Anything"]),
                      "chosenDir": "Jane Doe", "autoDir": "other"})
    example = kinded_app.store.examples()[0]
    assert example["chosen_dir"] == "Jane Doe"
    assert example["auto_dir"] == "other"


def test_learn_still_refuses_an_unknown_directory(kinded_app):
    from safety import UnsafeName
    with pytest.raises(UnsafeName):
        kinded_app.learn({"context": forum_context([]),
                          "chosenDir": "Not A Real Dir"})


def test_the_host_prior_is_still_recorded(kinded_app):
    kinded_app.learn({"context": forum_context([]), "chosenDir": "Jane Doe"})
    assert kinded_app.store.host_prior("someforum.test") == "Jane Doe"


def test_a_context_with_nothing_provable_learns_nothing(kinded_app):
    """A direct-link download with no page, no thread and no channel. It used
    to produce a GLOBAL alias off the filename-derived phrase."""
    out = kinded_app.learn({
        "context": {"url": "https://filehost.test/d/AbCdEf",
                    "filename": "opaque.mp4", "page": {}},
        "chosenDir": "Jane Doe", "confirmed": True})
    assert out["written"] == []
    assert kinded_app.store.alias_count() == 0


def test_the_kind_gate_reads_a_live_edit_of_the_dirs_file(kinded_app):
    """The classification is a plain file the operator edits; a stale cache
    would mean an edit does not take effect until the sidecar restarts."""
    ctx = MatchContext(tags=("Jane Doe",), site="someforum.test")
    assert kinded_app.matcher().match(ctx).auto is True
    Path(kinded_app.cfg.dirs_file).write_text(
        'performer = []\ncategory = ["Jane Doe"]\n', encoding="utf-8")
    assert kinded_app.matcher().match(ctx).auto is False


# --- cache invalidation ------------------------------------------------------ #
def test_editing_the_kinds_file_changes_the_dirs_etag(kinded_app):
    """The extension caches the WHOLE /dirs payload and revalidates with
    `If-None-Match`. DirIndex's etag only hashes directory NAMES, so a kind
    edited into dirs.toml used to produce a 304 and a permanently stale cache
    -- and the stale copy would keep auto-filing into a directory just
    reclassified as a category, only while the sidecar was unreachable, which
    is exactly when nobody is watching.
    """
    before = kinded_app.dirs_snapshot()["etag"]
    Path(kinded_app.cfg.dirs_file).write_text(
        'performer = []\ncategory = ["Jane Doe"]\n', encoding="utf-8")
    after = kinded_app.dirs_snapshot()["etag"]
    assert before != after


def test_learning_an_alias_changes_the_dirs_etag(kinded_app):
    before = kinded_app.dirs_snapshot()["etag"]
    kinded_app.learn({"context": {"url": CDN, "page": {}},
                      "chosenDir": "Jane Doe", "confirmed": True})
    assert kinded_app.dirs_snapshot()["etag"] != before


def test_the_backfill_plan_applies_the_same_kind_gate(library, store):
    """SKIP is the backfill's safe answer, and an unclassified target is not a
    licence to rename a file inside a live seeding target.

    The `result.auto` branch in backfill.plan is the ONE place a filename-only
    signal can move a file (it needs a lowered threshold to be reachable at
    all, since the filename rule is capped at 0.50). The kind gate applies
    there exactly as it does live.
    """
    import backfill as backfill_mod
    from qbt import PathMap

    (library / "jane_doe.mp4").write_bytes(b"x")
    # A reachable qBittorrent with NO torrents is positive proof that the file
    # is not a payload, which is what lets a row be `fs` at all.
    common = dict(store=store, dir_names=["Jane Doe", "other"],
                  torrents=[], path_map=PathMap("/downloads", str(library)),
                  threshold=0.1, do_seed=False)
    unclassified = backfill_mod.plan(library, dir_kinds={}, **common)
    classified = backfill_mod.plan(library,
                                   dir_kinds={"Jane Doe": KIND_PERFORMER},
                                   **common)

    def row(plan):
        return next(r for r in plan.rows if r.relpath == "jane_doe.mp4")

    assert row(classified).action != backfill_mod.ACTION_SKIP
    assert row(unclassified).action == backfill_mod.ACTION_SKIP


# --- audit follow-ups -------------------------------------------------------- #
def test_the_catch_all_learns_nothing(kinded_app):
    """Sending a download to the catch-all is "not any of these" — the absence
    of a subject, not evidence of one. A 1.00 identity alias pointing at it
    cannot auto-file (it is unclassified), but it would make the catch-all the
    permanent top candidate in the picker for that channel."""
    out = kinded_app.learn({"context": {"url": CDN, "page": {}},
                            "chosenDir": "other", "confirmed": True})
    assert out["written"] == []
    assert kinded_app.store.alias_count() == 0
    assert kinded_app.store.host_prior(DISCORD_SITE) is None
    # ...but the correction is still recorded as an example.
    assert kinded_app.store.examples()[0]["chosen_dir"] == "other"


def test_an_unclassified_directory_learns_identity_only(kinded_app):
    """The docs always said so; the code learned title subjects for it too,
    leaving dormant rows that would all activate at once on classification."""
    (Path(kinded_app.root) / "Unlisted Dir").mkdir()
    kinded_app.dirs.refresh(force=True)
    out = kinded_app.learn({"context": forum_context([]),
                            "chosenDir": "Unlisted Dir", "confirmed": True})
    assert {w["source"] for w in out["written"]} == {"thread-slug"}


def test_a_screened_identity_signal_is_refused_on_a_FIRST_write(kinded_app):
    """Identity signals used to bypass the screen entirely — the one row that
    most needed checking was the only one exempt.

    The screen applies on a first write only: once a row exists, a further
    correction is the operator RE-POINTING it, and an explicit correction
    always wins (see test_a_second_correction_overrules_the_first).
    """
    for chosen in ("Mary_Major", "john-smith"):
        kinded_app.store.add_example(
            {"page": {"tags": ["aster vale collection"],
                      "site": "someforum.test"}}, chosen)
    out = kinded_app.learn({"context": forum_context([]),
                            "chosenDir": "Aster Vale", "confirmed": True})
    assert any(s["source"] == "thread-slug" for s in out["skipped"])
    assert kinded_app.store.alias(thread_alias_key("aster vale collection"),
                                  "someforum.test") is None


def test_junk_tags_do_not_consume_the_category_budget(kinded_app):
    """Screen FIRST, then cap. Capping the input list meant a page whose first
    three tags were junk spent the whole budget and a legitimate fourth was
    never considered."""
    tags = ["JD", "Some Forum", "poster1988", "Field Recordings"]
    out = kinded_app.learn({"context": forum_context(tags),
                            "chosenDir": "acme-studio", "confirmed": True})
    assert "fieldrecordings" in {w["key"] for w in out["written"]}


def test_refusals_are_reported_in_the_response(kinded_app):
    out = kinded_app.learn({"context": forum_context(["JD"]),
                            "chosenDir": "acme-studio", "confirmed": True})
    assert out["skipped"] and out["skipped"][0]["why"]


def test_a_second_correction_overrules_the_first(kinded_app):
    """AN EXPLICIT CORRECTION ALWAYS WINS.

    The chrome-spread measure counts the operator's OWN routing history, so
    correcting the same thread to a second directory looked exactly like a
    phrase "seen on 2 different directories": the fix was REFUSED, the original
    wrong alias survived, and the next download from that thread still
    auto-filed into the wrong place at 1.00. A router that overrules the
    operator on the second correction is worse than one that never learned.
    """
    key = thread_alias_key("aster vale collection")
    kinded_app.learn({"context": forum_context([]), "chosenDir": "other",
                      "confirmed": True})                       # a shrug
    kinded_app.learn({"context": forum_context([]), "chosenDir": "Jane Doe",
                      "confirmed": True})                       # wrong
    assert kinded_app.store.alias(key, "someforum.test") == "Jane Doe"

    out = kinded_app.learn({"context": forum_context([]),
                            "chosenDir": "john-smith", "confirmed": True})
    assert key in {w["key"] for w in out["written"]}, out["skipped"]
    assert kinded_app.store.alias(key, "someforum.test") == "john-smith"

    res = kinded_app.match({"url": THREAD, "filename": "x.mp4",
                            "page": {"url": THREAD, "site": "someforum.test"}})
    assert res["dir"] == "john-smith"


def test_the_catch_all_does_not_count_towards_chrome_spread(kinded_app):
    """It is "not any of these" — the absence of a subject, not evidence of
    one, which is exactly why learn() refuses to write an alias for it.
    Counting it here contradicted that."""
    kinded_app.store.add_example(
        {"page": {"tags": ["Field Recordings"], "site": "someforum.test"}},
        "other")
    kinded_app.store.add_example(
        {"page": {"tags": ["Field Recordings"], "site": "someforum.test"}},
        "Jane Doe")
    spread = kinded_app.store.phrase_dir_spread(other_dir="other")
    assert spread.get("fieldrecordings", 0) == 1


def test_a_numeral_INSIDE_a_word_is_not_a_handle():
    from matcher import suspicious_alias_key
    assert suspicious_alias_key("mi5a", dir_names=["Jane Doe"]) is None


@pytest.mark.parametrize("handle", ["poster1988", "poster1", "uploader7",
                                    "user2", "x1988"])
def test_a_word_with_a_number_on_the_end_still_reads_as_a_handle(handle):
    """ONE trailing digit is the common username shape. An earlier narrowing
    required two, which let most real handles straight through."""
    from matcher import suspicious_alias_key
    assert suspicious_alias_key(handle, dir_names=["Jane Doe"])


@pytest.mark.parametrize("junk", ["gif", "mp4", "www", "com"])
def test_a_stopword_or_format_word_can_never_become_an_alias(junk):
    """These have no content tokens at all, so the shared-vocabulary and handle
    rules below both passed them silently once the length floor was lowered."""
    from matcher import suspicious_alias_key
    assert suspicious_alias_key(junk, dir_names=["Jane Doe"])


def test_an_initialised_name_is_exempt_from_the_length_floor_but_a_word_is_not():
    from matcher import suspicious_alias_key
    assert suspicious_alias_key("M.I.A.", dir_names=["Jane Doe"]) is None
    assert suspicious_alias_key("set", dir_names=["Jane Doe"])


# --- pins on the PERMISSIVE changes ------------------------------------------ #
# Across this PR the defects have been in WIDENED behaviour while every pin sat
# on TIGHTENED behaviour, so the riskiest lines were revertible with a green
# suite. Each test below fails when its permissive change is deleted.
def test_a_re_point_wins_even_once_the_phrase_reads_as_chrome(kinded_app):
    """PINS THE SCREEN BYPASS at server.learn's `screen()`.

    Deleting the bypass leaves this failing. The earlier
    `test_a_second_correction_overrules_the_first` does NOT cover it: with the
    catch-all excluded from spread, its fixture only reaches spread 1, so the
    screen passes anyway and the bypass is never load-bearing. It becomes
    load-bearing on the THIRD correction, which is what this builds directly.
    """
    key = thread_alias_key("aster vale collection")
    # The operator's own history has now put this phrase on two directories,
    # which is exactly what the chrome measure looks for.
    for chosen in ("Mary_Major", "acme-studio"):
        kinded_app.store.add_example(
            {"page": {"tags": ["Aster Vale Collection"],
                      "site": "someforum.test"}}, chosen)
    spread = kinded_app.store.phrase_dir_spread(other_dir="other")
    assert spread.get("astervalecollection", 0) >= CHROME_DIR_SPREAD, \
        "fixture must actually trip the screen, or this pins nothing"
    # ...and the router already learned this key once.
    kinded_app.store.upsert_alias(key, "Jane Doe", "someforum.test",
                                  source="thread-slug",
                                  evidence="aster vale collection")

    out = kinded_app.learn({"context": forum_context([]),
                            "chosenDir": "john-smith", "confirmed": True})
    assert key in {w["key"] for w in out["written"]}, out["skipped"]
    assert kinded_app.store.alias(key, "someforum.test") == "john-smith"


def test_the_same_phrase_is_still_refused_when_there_is_nothing_to_re_point(
        kinded_app):
    """The other half of the pin: identical fixture, no existing row, refused.
    Without this the test above would also pass if the screen were removed."""
    for chosen in ("Mary_Major", "acme-studio"):
        kinded_app.store.add_example(
            {"page": {"tags": ["Aster Vale Collection"],
                      "site": "someforum.test"}}, chosen)
    out = kinded_app.learn({"context": forum_context([]),
                            "chosenDir": "john-smith", "confirmed": True})
    assert thread_alias_key("aster vale collection") not in \
        {w["key"] for w in out["written"]}


@pytest.mark.parametrize("junk", ["Download Video", "Free HD Clip",
                                  "the and of"])
def test_a_stopword_phrase_is_refused_even_well_over_the_length_floor(junk):
    """PINS THE STOPWORD-FIRST RULE.

    The earlier parametrized cases (`gif`, `mp4`, `www`, `com`) are all under
    the 4-character floor, so the floor catches them and deleting the stopword
    rule changes nothing. These fold to long keys — `downloadvideo` is thirteen
    characters — and are caught ONLY by the stopword rule. That rule also has
    to run FIRST: with no content tokens, the shared-vocabulary and handle
    rules below it both pass silently.
    """
    from matcher import norm_key, suspicious_alias_key
    assert len(norm_key(junk)) > MIN_ALIAS_KEY_LEN
    assert suspicious_alias_key(junk, dir_names=["Jane Doe"])
