# ClickUp skill — setup & accounts

Read once per host. Nothing here is needed during normal task/doc work.

## Where state lives

Everything the skill writes — `accounts.json`, `.env`, `watchers.json`, `.cache/`
and the listener's `webhook-url.txt` / `webhooks.jsonl` / `webhook-latest.json` /
`last-seen.txt` — lives in the **state directory**, not the skill directory:

```
$XDG_STATE_HOME/clickup     # when XDG_STATE_HOME is set and non-empty
~/.local/state/clickup      # otherwise
```

The skill directory holds code only. Its source is `~/workspace/devrc/claude/skills/clickup/`
and home-manager deploys it to `~/.claude/skills/clickup/` as read-only `/nix/store`
symlinks, so a write there fails with `EROFS` — and a live `pk_` token has no business
sitting next to source. `lib/paths.mjs` is the single place that resolves these paths;
`test/state-paths.test.mjs` fails devrc's node gate if any module re-derives one from
`__dirname`.

**Editing the skill**: change it in devrc, then `home-manager switch --flake
~/workspace/devrc --impure` (or `scripts/ship.sh` for both hosts). A `git pull`
alone deploys nothing. `node_modules` is not installed by hand either — nix builds
it from `package-lock.json` (`nix/pkgs/clickup-node-modules.nix`).

**Upgrading from the old layout**: on the first run after this change, an
`accounts.json` (or `.env` / `watchers.json` / `.cache/`) still sitting in the
skill directory is **copied** into the state directory at mode 0600, with a
one-line note on stderr. The originals are deliberately left in place — another
host may still be running the old code. Delete them yourself once every host has
moved over.

## Webhook listener (`listen.mjs`)

It binds **127.0.0.1 only** and it **fails closed**. A delivery is acted on only
if it carries an `X-Signature` HMAC that verifies against the secret stored for
its `webhook_id` in `watchers.json` — the secret the skill records when it
creates the webhook. Anything else (no signature, unknown `webhook_id`, wrong
digest) is answered **401** and leaves no trace: nothing appended to
`webhooks.jsonl`, no cursor advance, nothing delivered to the agent. The same
check runs over events replayed from webhook.site on startup, because anyone can
POST to a webhook.site URL.

So if the listener logs `[rejected:unknown-webhook]`, the fix is to re-register
the watcher (`node query.mjs watch …`), not to disable the check. There is no
override flag.

`webhook-url.txt` is a **credential** — `https://webhook.site/<token>` is a
capability, and whoever reads it can read this workspace's event stream and post
forged events into it. It is written 0600, and the unauthenticated `GET /` on
the listener port deliberately does not return it.

## Credentials

Create `accounts.json` in the state directory — `~/.local/state/clickup/accounts.json`
unless `XDG_STATE_HOME` is set:

```json
{
  "defaultAccount": "bot",
  "accounts": {
    "bot": {
      "apiToken": "pk_your_token_here"
    }
  }
}
```

Generate the token at **ClickUp Settings > Apps > API Token**.

Or let the CLI write it — this creates the state directory (0700) and the file
(0600) for you, so it is the preferred route:

```bash
node query.mjs add-account bot --token pk_your_token_here
```

Team ID, User ID and other fields are auto-detected and cached on first use.
Only `apiToken` is required per account.

**Migration from `.env`**: an existing `.env` (in the state directory) is
auto-migrated to `accounts.json` on first run. The `.env` file is preserved —
remove it when ready.

## Default list

Set `defaultListId` to create tasks without naming a list every time:

```json
{
  "defaultAccount": "bot",
  "accounts": {
    "bot": {
      "apiToken": "pk_...",
      "defaultListId": "900000000001"
    }
  }
}
```

## Multiple accounts

Named accounts let one checkout drive both an automation identity and a personal
one (e.g. `bot` for unattended runs, `alex` for comments that should read as a
human).

```json
{
  "defaultAccount": "bot",
  "accounts": {
    "bot": {
      "apiToken": "pk_...",
      "teamId": "9000001",
      "userId": "90000001",
      "defaultListId": "900000000001",
      "email": "bot@example.com",
      "password": "...",
      "jwt": "eyJ...",
      "internalApiUrl": "https://frontdoor-prod-<region>.clickup.com"
    },
    "alex": {
      "apiToken": "pk_...",
      "teamId": "9000001",
      "userId": "90000002"
    }
  }
}
```

The `jwt` / `email` / `password` / `internalApiUrl` fields are only needed for the
**internal-API** commands (the `inbox-*` group and `doc-comments`). Token-only
accounts work for everything else.

`--account <name>` targets a specific account on any command:

```bash
node query.mjs me --account alex
node query.mjs my-tasks --account bot
node lib/jwt.mjs --status --account bot
```

Account management:

```bash
node query.mjs accounts                          # list configured accounts
node query.mjs switch-account alex               # change the default
node query.mjs add-account alex --token pk_...   # add
node query.mjs remove-account old-account        # remove
```
