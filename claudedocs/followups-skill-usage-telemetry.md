# Follow-ups from the skill-usage-telemetry work (PR #1000)

Everything here was **found and left undone** during that PR. Each item names a
closing condition, because an item nobody can close is not a work item.

---

## 1. `audit-dispatch.py` briefs auditors onto the WRONG TOOLCHAIN

**What.** The generated brief tells every auditor:

```
nix develop /home/zach/workspace/devrc -c python3 -m pytest <paths>
```

That is the **assembling checkout**, which tracks `main` — not the tree under
audit. So an auditor runs the PR's *source* against `main`'s *toolchain*.

**Measured.** Round 4 of #1000's ladder followed it and reported
`test_opencode_engine.py` failing on `opencode 1.18.21 vs 1.18.18` — a
combination existing in neither tree. It reached the right verdict only because
it independently ran the red-at-base control. The same mechanism produces a
false **PASS** just as easily, and nothing in the brief warns about it.

**Fix.** Emit `nix develop <the extracted head tree>` when the head tree carries
a `flake.nix`; otherwise say plainly that the toolchain is the assembling
checkout's and that version-pinned failures must be controlled before they are
believed.

**Closes when:** a brief generated for a PR whose `flake.lock` differs from
`main` names the head tree's own dev shell, verified by generating one and
reading the TOOLCHAIN section.

---

## 2. ClickHouse reader credential is in the opencode session store

**What.** During the originating investigation a debugging `sops -d … 2>&1`
printed the `activity_reader` password in cleartext into a tool result, which is
persisted in `~/.local/share/opencode/opencode-stable.db`.

🔴 Deliberately no further detail here — devrc is a **PUBLIC** repo and
`SECRETS.md`'s convention is DEAD credentials with their rotation, never a live
exposure and its blast radius.

**Fix.** Rotate; then decide whether the store needs scrubbing.

**Closes when:** the credential is rotated and `SECRETS.md` records it as dead
in the usual form. **Checked by:** Zach.

---

## 3. No skill routes the question "is skill X used?"

**What.** Gap **G4** from `proposal-skill-usage-telemetry.md`, still open.
`adoption-scan` ("is it USED"), `find-session` ("recover a session") and
`activity` ("query the telemetry") each look like the right entry and none says
where the answer lives. The originating session bounced between all three.

`find-session`'s SKILL.md now documents `--skill`, but neither `adoption-scan`
nor `activity` mentions it — `git grep -c -- --skill` over both returns 0.

**Fix.** One disambiguation line in each, pointing at `--skill` for the
interactive answer and (once it exists) at `adoption-scan` for the aggregate.
Description edits only — no new skill, so no tier eviction.

**Closes when:** both SKILL.md bodies name the owning path and
`test_skill_descriptions.py` still passes.

---

## 4. The ClickHouse creds/query path is still a copy-paste bash recipe

**What.** Gap **G5**, still open. `activity`'s SKILL.md hands agents raw
`sops … | curl`. Both operational failures of the originating session came from
an agent editing that recipe under pressure: the credential above, and two pod
OOMs from `ILIKE` full-scans of the keylog table. The prose instruction to read
`queries.md` first was in the loaded skill body and was ignored.

**Fix.** `scripts/collector/ch.sh <sql>` that keeps creds out of argv and
stdout and refuses an unbounded scan of `text`/`payload` without a `ts` bound or
a `LIMIT`. `scripts/validation/chquery.py` already reads the env and is the
natural core.

**Closes when:** `activity`'s SKILL.md status block invokes the helper instead
of the raw recipe, and the helper is shown to refuse an unbounded scan.

---

## 5. Deferred BY DESIGN — these wait on data, and adding them early ships a red gate

Both are specified in `proposal-skill-usage-telemetry.md`.

- **`adoption-scan` `via: "skill"` registry arm.** `skills_used` is
  forward-only; adoption-scan raises a loud `DEAD` flag at zero uses, so adding
  the reader before rows exist reports every skill as dead.
- **A deadman for `attributionSkill`.** It is an undocumented upstream field; a
  rename makes every count silently zero. The invariant belongs in
  `scripts/validation/invariants.py` — but until rows accumulate it is a
  permanently-red gate, which trains everyone to click through.

**Both close when** this returns non-zero, and not before:

```sql
SELECT count() FROM activity.events
WHERE source='claude' AND kind='session-summary'
  AND JSONLength(payload, 'skills_used') > 0
```

which requires PR #1000 merged **and** `scripts/ship.sh` run, since nothing
carries the field until the tailer is deployed and the 5-minute timer ticks.
