# Guild rank and companion test setup

This build adds an officer-only Discord role export plus opt-in signed companion endpoints. It does not change game or Discord ranks. The addon owns reviewed, click-driven promotions; the companion owns local comparison and optional uploads.

## Rank export with no web hosting

After merging and deploying the bot change, use `/guild-rank-export` in Hyped. The command is guild-scoped, private, and explicitly limited to the bot owner, server owner or Leadership. Its default Discord command permission is Manage Server; if Leadership lacks that permission, grant the command to that role through the server's integration command settings. The runtime permission check still applies.

The configured Discord role is `HYPED_RANK_DISCORD_ROLE_ID` if set, otherwise existing `FOREVER_ROLE_ID`. No role-name guess is used. The current deployment already sets `FOREVER_ROLE_ID`; the command fails if that role is missing.

The JSON includes registered full character names, main/alt status, Discord IDs and current role membership, using the existing character database (including LFG registrations). Discord membership is fetched afresh, not inferred from attendance or a stale role cache. Missing Discord members are flagged. An incomplete or failed lookup produces no partial export. Keep the attachment private and load it into Companion 0.2 within 15 minutes.

The default companion mapping is Forever role → game Forever for mains. The game target may be renamed in both companion/addon settings. Banker, Leadership, Forever Pro and higher ranks are protected, no demotions are generated, and absent/ambiguous links require manual review. Forever Pro's three-of-four attendance policy is not implemented by this change.

## Optional HTTP endpoints

All routes are **disabled by default**. The JSON export above works without them.

| Setting | Purpose |
| --- | --- |
| `HYPED_SYNC_ENABLED=true` | Start the existing port-8080 HTTP listener and register `/hyped/sync` |
| `HYPED_SYNC_SECRET` | Random secret of at least 32 characters, shared only with the operator's companion |
| `HYPED_SYNC_GUILD_NAME=Hyped` | Expected guild name for level/bank intake |
| `GUILD_SYNC_DB_PATH=/data/guild_sync.db` | Separate persistent sync/dedupe store; included in fly.toml |
| `HYPED_SYNC_BANK_CHANNEL_ID` | Ordinary Hyped text channel for bank activity; use a private test channel first |
| `HYPED_RANK_SYNC_ENABLED=true` | Also register `/hyped/rank-roster` for companion role fetching |
| `HYPED_RANK_DISCORD_ROLE_ID` | Optional override for `FOREVER_ROLE_ID` |

The committed Fly configuration has **no HTTP service mapping**. A deployed bot is therefore not automatically a reachable companion API. When explicitly enabling this integration, configure the deployment to forward HTTPS to port 8080. A suitable service block for a single always-running bot is:

```toml
[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 1
```

Set the enable flag and secret before deploying a service that expects that listener. Do not scale to a second bot worker. Preserve the existing `/data` volume and all databases. Use the existing deployment/backup procedure. This PR intentionally does not expose the service, create secrets, choose a bank channel or deploy itself. A normal merge to main triggers the repository's existing deployment workflow.

Point the companion to the deployment's confirmed HTTPS host plus `/hyped/sync`. The same host plus `/hyped/rank-roster` is optional for fetching roles. Check the endpoint rejects unsigned requests with 401 before entering the secret in the companion. Do not use a Discord token as a sync secret. Do not put secrets in URLs or source files.

Requests use `X-Hyped-Timestamp` and `X-Hyped-Signature: sha256=<hex>`, HMAC-SHA256 over `<timestamp>.<exact JSON bytes>`. Timestamps outside five minutes are rejected. Repeating a signed request within that window is possible; event-ID deduplication makes bank intake idempotent. Rank fetching uses a 15-second refresh throttle and no mutation. This is a trusted-operator integration, not a public per-member API.

## Bank/level test

1. Install addon 0.5 and companion 0.2. Back up real saves and sync state.
2. Capture the guild roster, `/reload`, then inspect the account save locally. Level intake matches full registered main names and updates only newer snapshots. Existing WoWSims/profession data and public roster visibility rules stay intact.
3. On the Banker character open the bank and logs, `/hyped scanbank`, wait, `/reload`. Read the local log in the companion with bank uploads off.
4. Exclude existing history in the companion, make one real test deposit yourself, rescan/reload and check one new ID.
5. Set the private test channel and explicitly enable uploads from that one bank account. Run **Sync all now** and check one expected announcement. A second unchanged sync must not announce it again.
6. Only after successful client/deployment tests use automatic watching and the normal bank channel. Watching sees saves on reload/logout, not continuous game changes.

The addon cannot recover expired/unseen history or read tabs the player cannot access. Source timestamps are approximate. Duplicate indistinguishable transactions and capture gaps remain possible. The bot uses persistent event-ID deduplication and serial announcement delivery; a crash after Discord sends but before the database acknowledgement can still duplicate a message. Pending delivery is retried on a later enabled bank sync. Level-only sync does not flush bank messages. Announcements disable mentions.

## Optional private raid drafts

The retained companion Raid draft tab requires `HYPED_SYNC_ENABLED=true`, `HYPED_CONTROL_ENABLED=true` and a **different**, at-least-32-character `HYPED_CONTROL_SECRET`. It registers `POST /hyped/raid-draft`. The validated request only prefills a card in the bot's private control channel. Publishing still uses the existing owner-only Discord confirmation. No endpoint accepts arbitrary messages, public destination channels, role pings or rank mutations. Test this separately from bank/rank review.

## Verification

```sh
python -m unittest discover -s tests -p 'test_guild*.py' -v
python -m unittest discover -s tests -v
python -m compileall -q hyped_ogre
```

Tests mock Discord membership, permission failures, signed requests, duplicate bank IDs, opt-in behaviour, full names and roster persistence. They do not prove a live deployment is reachable or that the Forever client accepts game actions. Follow [the addon client checklist](https://github.com/AdamHawthorne/hyped-addon/blob/feature/forever-rank-sync-testing/docs/TESTING.md).
