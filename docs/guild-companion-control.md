# Hyped Companion raid controls

The optional companion control route sends a prefilled raid setup card to the existing private `#ogre` control channel. It uses the bot's existing owner-only `RaidPosterView`; it does not create or publish a public raid panel directly. The normal **Post / Update Raid** and **Confirm and post raid** steps remain in Discord.

Enable it only after reviewing the feature branch and enabling the companion sync endpoint:

```sh
fly secrets set HYPED_CONTROL_SECRET='use-a-different-random-secret-of-at-least-32-characters'
fly secrets set HYPED_CONTROL_ENABLED=true
fly secrets set HYPED_SYNC_ENABLED=true
```

The companion sends signed `POST /hyped/raid-draft` requests using the `HYPED_CONTROL_SECRET`. Timestamps must be within five minutes. Request bodies are capped at 16 KB. The route accepts only a small set of raid dates, times, instances, title and announcement options. It fixes the roster policy and disables role pings; a draft cannot choose a channel, mention roles, or publish without the owner-operated Discord confirmation.

The desktop app stores the endpoint URL with its other local settings. It keeps the control secret in memory or reads `HYPED_CONTROL_SECRET` from the process environment; it does not write the secret to its config file. Keep the endpoint secret separate from the guild sync secret.

The route is registered only when both HYPED_SYNC_ENABLED=true and HYPED_CONTROL_ENABLED=true; it is disabled by default. It has not been deployed or tested against the live bot. Test the private control channel, message permissions, and the full poster confirmation flow before enabling it on Fly.io.
