# Hyped Guild Companion sync

The local `hyped-companion` app reads Hyped Guild Tools SavedVariables and sends signed HTTPS requests to Hyped Ogre. It does not receive or use the Discord bot token.

## What the bot stores

- `guild_sync_levels` stores the latest level snapshot for uniquely named registered **main** characters in the existing character roster. Unmatched guild members and duplicate character names are ignored.
- The character board uses the latest synced guild level when available, falling back to its existing WoWSims snapshot when no fresh level is known. Its current one-minute refresh loop updates the board without a manual repost.
- `guild_bank_events` stores idempotent transaction records. Newly accepted deposits and withdrawals are announced in batches in the configured bank-feed channel. Approximate log age is retained because the game API does not provide an exact event timestamp or stable transaction ID.

The sync database is separate from raid attendance and is additive. On Fly.io, set `GUILD_SYNC_DB_PATH=/data/guild_sync.db` to keep it on the existing persistent volume.

## Enable the endpoint

Keep sync disabled until a private channel has been selected and a shared secret is configured.

1. Create a regular text channel for bank announcements. A Discord-linked guild chat channel is not a valid bot destination.
2. Generate a random secret with at least 32 characters, for example `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
3. Set the Fly app variables/secrets:

   ```sh
   fly secrets set HYPED_SYNC_SECRET='...'
   fly secrets set HYPED_SYNC_ENABLED=true
   fly secrets set HYPED_SYNC_BANK_CHANNEL_ID='...'
   ```

   `HYPED_SYNC_BANK_CHANNEL_ID` may be set as an app variable rather than a secret. It must be a regular text channel in the configured Hyped Discord server.
4. Configure the same secret and the deployed endpoint URL in Hyped Companion, then open the guild bank and save the addon file with `/reload` or logout.

The route is `POST /hyped/sync`. It is registered only when `HYPED_SYNC_ENABLED=true`, requires HMAC-SHA256 over `<timestamp>.<raw JSON body>`, and accepts timestamps within five minutes. It validates payload sizes and values, rejects data captured more than 30 days ago, and ignores repeated bank event IDs. The Discord token remains in the bot process.

If Discord cannot accept a bank announcement, the endpoint returns HTTP 503 and retains the event for retry. The companion does not mark those events as sent until it receives a successful response. There is a narrow process-crash window between Discord accepting a message and the bot marking it sent, so a rare duplicate announcement is possible.

## Checks and live validation

Offline tests cover signature/payload validation and database idempotency. Production enablement still needs a client/API test of Forever roster and bank-log capture, SavedVariables flush/reload behavior, and one end-to-end sync to a private test channel.

