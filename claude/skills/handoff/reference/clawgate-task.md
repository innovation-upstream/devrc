# The `clawgate-task:` field — resolution, exit codes, and the front-matter block

Loaded from `/handoff` steps 1 and 2. Every line below is VERBATIM from the core it was demoted from.

## Step 1 — what `clawgate_handoff.sh resolve` reads, and how it ranks

     It reads `GET /api/sessions/{id}/tasks` for the session named by **`OPENCODE_SESSION_ID`**, else **`CLAUDE_CODE_SESSION_ID`**. 🔴 **Those are the exact names; there is no `CLAUDE_SESSION_ID`.** Reading a name that does not exist ships an INERT feature indistinguishable from a working one — an unset variable and a session that touched nothing produce the same empty result. The tool refuses rather than guessing: `NO SESSION ID` (exit 3) is its own outcome and is never folded into "no task". 🔴 Inside opencode (`$OPENCODE` set) with no opencode id it REFUSES too — that id may be an ancestor's.

     Each linked row carries a **`role`**, and the verdict RANKS by it instead of counting links: `worked` (this session commented on the task or flipped its status), `created` (it FILED the task), `read` (it only fetched it). `worked` is the signal because `claude/skills/clawgate/flows/task-pickup.md` mandates the comment/status write-back on every pickup and a Stop hook blocks the turn without it.

     🔴 **CAPTURE the status — a PIPE EATS it**: `… | tail; echo "rc=$?"` prints 0 for a real 5. Use `out=$(… resolve 2>&1); rc=$?`. 📖 `~/.claude/skills/handoff/reference/exit-code.md`.

     ⚠ **Even a `worked` row is a CANDIDATE, not proof this session did the work this doc describes** — it records that the board accepted a write, nothing more. Read the title before recording it, and prefer asking over recording a task you do not recognise. One known blind spot: `created` is TERMINAL upstream and outranks `worked`, so a session that FILED a task and then worked it stays `created` and lands in the no-worked case.

## Step 2 — checking an EXISTING field before adding one

   🔴 **On an UPDATE, check before you add:** `bash ~/workspace/devrc/scripts/lib/clawgate_handoff.sh field <doc>` exits **0** and prints the id when a readable field is already there (leave it alone), **1** when there is none (add it), **2** when the field is there and unreadable — either a value that is not a task id or a front-matter block that is **never closed**; the stderr line says which, and the repair is to *that* block, never a second field. A doc with two `clawgate-task:` fields reconciles against whichever the parser reaches first, which is not a choice anybody made.

   ⚠ **`64` and `66` are about your COMMAND, not the doc**: 64 = no path or unknown verb, 66 = that path could not be read. Neither says anything about a field — fix the invocation. Any other code means the tool did not run.

   🔴 **If step 5's merge reports `This update DROPS the doc's recorded clawgate task`, restore it at LINE 1 — do NOT follow rule (f)'s usual "move it under an APPEND heading" advice for that line.** The field is read only from a closed `---` block at the top of the file; anywhere else it is invisible to every reader, so "moving" it silently disables the thread. The tool prints that remedy itself for this class; the two remedies are opposites and the block header says which one you are looking at.

   🔴 **The closing `---` is load-bearing.** Both readers require it: an unterminated block is not front matter to `handoff_doc.py` either, so it is ordinary preamble and step 5's merge will drop it the next time an update brings its own preamble. That drop is now *reported* rather than silent — but the cheap fix is to close the block.
