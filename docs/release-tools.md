# Character hub, raid activity and post-launch LFG

## Character editing and board

`/characters` opens the existing private character editor in any Hyped guild
channel. It reuses the same registered characters as raids and LFG.
Create a guild-visible `#characters` channel and run `/postcharacters` there
as Ogre's owner, server owner or Leadership. Discord's default command
visibility is Manage Server; allow Leadership to use the posting command in
Server Settings > Integrations rather than widening their server permissions.

The board is a persistent, paginated message with an **Update characters**
button. Repeating the command refreshes the saved message; a confirmed missing
message can be recreated. Transient API failures do not discard profiles.
Automatic updates run every minute and never ping displayed members.

It displays character Main/Alt, primary spec/role, off-spec/role, account
attendance across the last four completed guild raids and a saved WoWSims
item-level estimate. Partial item-metadata coverage is labelled. No live
armory data or character level is substituted for item level.

Top parses are explicitly pending a Warcraft Logs connection. Numeric parse
fetching and a verified Forever character/realm/zone identity are not part of
this release. Do not present the board's placeholder as a completed rankings
integration.

## Off-spec and classroom access

Characters > select character > **Off-spec** stores a secondary spec and role
on that character. It does not change which character is Main or Alt and does
not change the primary role used for raid sign-ups. Feral can choose Tank or
DPS. Clearing the off-spec removes only access no other registered character
or spec still qualifies for.

For example, a Retribution/DPS Paladin with Protection/Tank off-spec gets
Paladins, DPS and Tanks. Holy plus Protection gets Healers and Tanks. Class
changes clear the old off-spec; foreign-character and stale-class updates
are rejected. Existing characters default to no off-spec and keep their IDs,
exports and sign-ups. Raid Sheets retain their primary-role layout.

## Leadership alerts

New/changed signup notes, cleared notes, and newly submitted/changed absences
queue an event in the same transaction as the saved update. Repeating an
unchanged form does not queue another alert, including concurrent submissions.
Returning to Attend after an absence sends a correction, so Leadership does
not see only an older absence notification.
No old notes/absences are backfilled on migration.

Alerts go to the existing private Leadership chat `1564086170315395164`, pinging
only Leadership `1225004372396867685`. The bot checks that the target is in
Hyped and is not visible to general-member roles. Ogre needs View Channel,
Send Messages, Read Message History, Embed Links and the ability to mention
Leadership. The note author is displayed without an additional member ping.

An immediate event hook attempts delivery after successful player actions;
a 30-second worker retries pending changes. A saved activity marker and the
recent 100 bot messages recover an uncertain send without routinely repinging
on restart. This is a retryable outbox, not a distributed exactly-once delivery
guarantee. Deleted raids' undelivered updates are retired without a stale ping.

## Attendance and archive

The permanent Google attendance sheet remains editable. Valid known
raid/member/character rows are mirrored into the existing SQLite attendance
table on sync and after bot-written attendance updates. Late arrivals count
as attended; Sheet Absent maps to local No-show; Excused gets no attendance
credit. Unknown/foreign IDs and invalid statuses are ignored. Officer notes
and manual exceptions are retained.

Completion fills Pending/empty selected **and bench** entries with Attended,
preserving existing exceptions. Completed raid tabs are hidden, never deleted,
after permanent attendance synchronization. Permanent tabs remain visible;
the final visible worksheet is never hidden. Archived public raid cards keep
their history and close attendance actions. Hidden archived management tabs
are not recreated on every background pass.

## Everyday LFG mode

The existing board starts in **launch** mode. After launch, leadership can
run `/lfg-mode live` in the board channel. The live Add / Update flow uses a
registered character and free-text activity/zone, availability in UK time,
who is needed, and notes. It supports levelling, dungeon and PvP group plans
without obsolete launch-day/time prompts. The dungeon wishlist picker remains.

`/lfg-mode launch` returns to the preserved launch plans. Live plans have a
separate table and removing one never deletes the original launch record,
dungeon request or another member's plan. No channel is renamed or mode
switched automatically by deployment. `/postlfglfm` restores/refreshes the
anchor; the existing scrollable continuation layout remains in use.

## Additive persistence

- `characters.off_spec` and `characters.off_role`: empty defaults, separate
  from the unchanged primary spec/role fields.
- `raid_activity_events`: durable delivered/pending notification journal.
- `character_panels`: guild/channel/message/page for persistent boards.
- `lfg_settings` and `lfg_live_plans`: mode and current group plans in the
  existing LFG database. Original launch/dungeon records remain intact.

The existing Fly backup/volume guard applies before deployment. Code rollback
does not remove these additive fields/tables or undo saved profile changes.

## Verification

Offline tests use temporary SQLite databases and fake Discord/Sheets
transports. They cover same-character off-spec roles, class changes, legacy
migration, ephemeral commands, persistent board lifecycle, partial item levels,
durable private notification delivery, concurrency/deduplication, archive
properties, attendance exceptions/bench credit, LFG mode preservation and the
real announcement/reaction handlers.

Live checks still require Discord and the connected Google workbook:

1. Open `/characters` outside the signup channel and verify privacy. Add a
   real off-spec and check the resulting role channels.
2. Use a private test raid with consenting selected members to check the
   selected-player ping and custom check reaction. Acknowledgement removes the
   name from that message and does not remove the signup or ping again.
3. Add/change a note and submit absence. Verify the private Leadership ping
   and that an identical repeat causes no additional change alert.
4. Use an isolated workbook/database or a genuine completed raid to verify
   the real completion/archive round-trip. Do not insert fake completed raids
   into live guild attendance/rank eligibility history.

The actual announcement and four raid-night cards are drafts for Adam to post;
this release does not publish them or automatically ping the server.
