# Forever Classrooms

Ogre uses the existing shared `characters` register in `/data/raid_signup.db`,
the same register used by raids, LFG and the **Forever Roster** Sheets tab.
Both Main and Alt characters count. No class or combat-role Discord roles are
created or assigned. WoWSims, raid attendance, roster selection and the
`Forever` Discord role are not prerequisites for classroom access.

For example, Protection Warrior + Restoration Shaman gives access to
`warriors`, `shamans`, `tanks` and `healers`. Feral DPS gives `druids` without
`tanks`; Feral Tank gives both. A healer class playing DPS does not get
`healers`. Any registered DPS character, melee or ranged, also gives access
to `dps` (including Survival Hunter and Feral DPS). Tank/healer mains with a
DPS alt qualify too. Access remains until no registered character qualifies.
Duplicate qualifying characters only produce one permission entry.

## Audit findings and required permissions

The 2026-11-03 audit already has the category and all eleven text channels
hidden from `@everyone`. Ogre's **Bot** role already has **Manage Roles** and
**Manage Channels** at server level, but neither **Bot** nor **Leadership** has
a View Channel allow in this category. Server-level View Channel does not
override the category/channel `@everyone` denial. Leadership is above Bot;
channel permission updates do not require promoting/demoting Leadership or
moving Bot above Leadership.

The `dps` channel (`1566979883068948551`) was added after the audit. It uses
the same private baseline, bringing the configured text-channel total to twelve.

Before enabling the feature:

1. Open **forever-classrooms > Edit Category > Permissions**. Keep
   **@everyone > View Channel = Deny**. Add **Bot**, allowing **View Channel**,
   **Send Messages**, **Read Message History** and **Manage Permissions**
   (the channel UI name for **Manage Roles**).
2. **Before the first setup only**, sync all twelve classroom text channels
   to that category, or add the same Bot permissions to each channel.
   Most are currently unsynced. Adding Bot only to the category will not fix
   those unsynced channels. Check Tanks, Healers, DPS and all nine class channels.
3. Deploy this change, then run **`/classrooms setup`** as Ogre's configured
   owner or the Discord server owner. This validates every destination, keeps
   `@everyone` hidden, explicitly allows Bot and Leadership to view, send and
   read history on the category and each channel, enables updates and syncs
   existing registered characters immediately.
4. Run **`/classrooms status`** to check expected registered-account totals
   and any remaining permission problems. Check with an ordinary member;
   the owner, administrators and Leadership can see more channels.

Do **not** allow View Channel for Forever, Friend, Nitro or another general
member role on these channels. That would expose every class chat to those
members. Explicit member permissions allow eligible players through the
`@everyone` denial. Administrators always bypass channel restrictions;
Carlbot has Administrator in this audit. No Administrator grant is needed
for Ogre and no new privileged gateway intent is needed for this feature.

After setup, the channels intentionally become **unsynced** because each has
its own members. Do not click **Sync Now** afterwards: it replaces individual
channel permissions, including manual exceptions Ogre does not own. If it has
already happened, Ogre restores still-eligible registered access on the next
automatic pass, or run `/classrooms sync` immediately. Make future baseline
changes per channel or repeat `/classrooms setup`, which preserves existing
member overwrites.

The bot's existing **Manage Channels** permission can remain for other bot
features. This feature changes overwrites on existing channels using
**Manage Roles**; it does not create or delete channels, manage webhooks,
moderate members or need Administrator. Keep Ogre's existing messaging,
reaction and attachment permissions for its other features.

## Commands and updates

| Command | Behaviour |
| --- | --- |
| `/classrooms setup` | Owner only. Install private baselines, enable/resume, sync existing characters. |
| `/classrooms status` | Read-only checks and eligible-account totals per channel, including registered accounts that may have left the server. |
| `/classrooms sync` | Reconcile every currently registered member and previous Ogre grant. |
| `/classrooms pause` | Stop automatic updates; current access stays in place. Setup resumes. |

All replies are private, without pings or new channel announcements. Runtime
checks allow the configured Ogre owner, server owner and audited Leadership
role; setup is restricted to the two owners. Discord command defaults are
**Manage Server**. To let Leadership use status/sync/pause, allow that role
for **classrooms** in **Server Settings > Integrations > Hyped Ogre**.
They do not need server-wide Manage Server or Manage Roles. Setup retains
its owner-only runtime check even if command visibility is widened.

Saving, editing or deleting a character through the shared Characters UI
queues a member refresh after the database commit. A full scan also runs
on startup (once ready and enabled) and every 60 seconds. This catches
missed events, restarts, additions, deletions, role changes and returning
members. Missing cache entries are resolved using individual REST member
lookups; a failed lookup does not cause removal. Departed members are not
granted access. Discord's rate-limit handling applies to all API writes.

Sheets roster character columns mirror the canonical database. Creating a
sheet-only row or editing its displayed class/spec is not a character
registration and will not grant access. Use Ogre's Characters flow to update
profiles. Attendance/roster choices in Sheets do not change classroom access.

## Persistence and manual permissions

Only two additive tables are created: `classroom_settings` and
`classroom_grants`. Existing characters, sign-ups, gear, attendance and
Sheets data are not changed. The code requires the existing character
database and will not silently create a new empty register at a wrong path.
The standard Fly deployment backup guard still applies.

Access is disabled until setup succeeds. The destinations are pinned to the
audited guild/category/channel IDs in
`hyped_ogre/features/classrooms/config.py`, not guessed by names. A channel
rename works; a moved/deleted/recreated destination stops reconciliation
until its configuration is reviewed. This register has no guild field, so
the feature refuses to reuse it on another server.

Ogre grants View Channel, Send Messages and Read Message History, inheriting
ordinary reactions/attachments/embeds from the existing server permissions.
It journals only permission flags it changes from inherit to allow, before
the Discord write. When a member no longer qualifies, it restores those
flags to inherit if they still match Ogre's allow. The overwrite is deleted
only if nothing remains. HTTP failures and restarts retain the journal for
retry, including an uncertain write that may have reached Discord.

Pre-existing manual allows remain manual. Explicit member denies are
respected; existing unrelated permissions and Leadership role access are
preserved. If a moderator edits an existing member overwrite from an active
Ogre-owned allow to deny or inherit, the next sync leaves that decision in
place and reports it as a manual restriction. If the entire member overwrite
has disappeared, Ogre rechecks the current register and restores eligible
access from the empty baseline. This recovers category permission resets;
use an explicit member View Channel deny to block registered access.
A manual member allow can keep access even after character removal. Status
totals describe character eligibility, not every possible administrator/manual
permission exception.

Pause freezes existing permissions; reverting code also leaves permissions
in place. Neither is a bulk access-removal operation. Keep the two journal
tables if you want Ogre to remove its grants correctly after resuming.

API references: [Discord permission overwrites](https://docs.discord.com/developers/topics/permissions#permission-overwrites),
[Edit Channel Permissions](https://docs.discord.com/developers/resources/channel#edit-channel-permissions).
