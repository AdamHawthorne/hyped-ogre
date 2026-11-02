# Hyped Ogre: one Google Sheets tab per raid

This replaces the old six-tab Raid Management setup. It does NOT touch the
daily roll, gold coin, leaderboard or other bot SQLite tables.

## Workflow

1. In private `#ogre`, open `/raid poster`. The first **Schedule** page
   defaults to the next Sunday after existing scheduled raids, 20:00-23:00
   UK, Hyjal (20m) + Barrows (10m), the guild raid channel, a target roster
   confirmation one day beforehand, and **WoWSims required if missing**.
   No ping roles are preselected. **Post / Update Raid** shows a private
   confirmation of date, instance, destination and initial pings before
   creating a *new* public raid panel. **Cancel** posts nothing and does
   not save a draft. Updates to an existing posted raid are silent.
   Ogre creates a private Sheet
   tab such as **Raid #1 - Wed 09 Dec 2026**, synced about every 60 seconds.
   The private poster updates after posting to unlock **Preview / Confirm
   Roster**. The roster confirmation target is informational, not an
   automatic publish event.
   
   Use **Advanced settings** for a non-standard end time, third instance,
   destination channel, optional role pings (only when first posting),
   visibility, WoWSims requirement, check link, SoftRes, title and banner.
   **Back to schedule** keeps pending changes. **Post / Update Raid** saves
   the selected settings. The bottom of both poster pages lists leadership
   slash commands. `/raid sync` is owner-only but available in both
   private `#ogre` and raid sign-up channel `1563663151323553802`;
   its response is private.
2. Players use **Attend** and **Characters** to register their main or alt.
   The raid poster sets the requirement. New raids default to **WoWSims:
   Required if missing**, so a character must have a saved export before
   its first signup. The **Advanced settings → WoWSims requirement**
   picker also offers **Require fresh** (export
   submitted after leadership enables that mode) and **Optional / off**
   (Attend without an export). Poster edits preserve existing policy
   settings and fresh-export cutoff. Current sign-ups are never discarded
   when leadership changes modes.
   The **WoWSims** button links to the
   [WoWSims Exporter addon](https://www.curseforge.com/wow/addons/wowsims-exporter).
   In game, `/wse export` opens the exporter and highlights data ready for
   Ctrl+C. Players select **Paste export** in Discord and paste with Ctrl+V.
   The first-time registration flow saves the character, then prompts for
   the export before signing them up. An export is saved per character and
   reused for later raids. Players should update it before the
   roster-confirmation date where possible, but WoWSims collection remains
   open until leadership completes/archives the raid. The public raid panel
   displays the selected requirement and a link to the addon. One character
   per player per raid night.
3. In that raid's Google tab, use the leadership dropdowns to build the
   proposed teams. The public panel can show partial previews but describes
   them with **Confirmation:** and the scheduled Discord relative
   timestamp (e.g. "in a week"), which shows the precise date on hover.
4. In private `#ogre`, open **Preview / Confirm Roster** and check the
   live Sheet selection. Choose **Publish roster only** or **Publish + ping
   selected**. Publishing reveals the teams on the existing Discord panel,
   but **does not close sign-ups or lock the Sheet**.
5. **Publish + ping selected** posts a separate message in the raid channel
   tagging each selected Discord member once. Members react ✅ to acknowledge
   that they have seen the roster. Ogre removes their mention from the list
   of outstanding responses. The selected player remains on the actual roster.
   Ogre does not re-ping on subsequent panel refreshes, and a second
   announcement is blocked for the same raid. PUGs without Discord IDs
   cannot be tagged and are excluded from acknowledgement tracking.
6. Keep using the raid Sheet after publication. Later sign-ups flow into the
   raid tab, and leadership assignment changes update the public roster
   automatically at the next successful sync (~60 seconds). To unpublish,
   choose **Manage Raid → Unpublish Roster → Confirm unpublish** in the private
   raid poster, or use `/raid reopen number:1`. The original public panel
   reverts to live sign-ups; attendance, selections and character data remain
   intact. If a one-time ping announcement was posted, Ogre edits it to
   **Roster withdrawn** without any further mentions. Re-publishing the
   roster does **not** automatically send a second ping. The original
   acknowledgement list stays retired to avoid misleading reactions.
7. After the raid, use
   `/raid archive number:1 wcl_url:https://www.warcraftlogs.com/reports/... notes:...`.
   The date, WCL link and notes are stored on the raid tab. The tab stays visible for review. Leadership can hide it manually later.

## Deleting raids

Use the **Delete Raid** option under the poster's advanced settings, then
confirm. Ogre removes **only** the matching **Raid #N - ...** tab from
Google Sheets, the bot's public Discord raid panel, and the raid's
local sign-ups and attendance records. It never deletes other raid tabs,
the permanent Forever Roster/Attendance/Gear tabs, or characters and
WoWSims exports. The raid's tab is deleted rather than merely hidden.
If Google Sheets deletion fails, Ogre does not delete the Discord panel
or raid database, so deletion can be retried. Automatic Sheets sync is
paused during the coordinated deletion to avoid recreating a tab.

## Single raid tab layout

The top rows contain the raid title, date, status, instances, confirmation
and archive timestamps, Warcraft Logs, review notes and Discord panel link.

### Top-of-sheet controls (rows 1-5)

The existing **title (A1)**, **date (A2/B2)**, **instances (A3/B3)**,
**status (C2/D2)** and **signed/absent count (A5)** remain unchanged.

**B4: Warcraft Logs** is user-editable. Paste a valid HTTPS Warcraft
Logs report URL. Ogre reads it during its next successful Sheets sync,
stores it in the raid record and updates the original Discord raid
panel with the **Warcraft Logs: View report** link. The bot remembers
the last value it wrote to B4, so routine sync will not overwrite a
new manual edit.

**D4: Raid Notes** is also user-editable. A changed D4 is saved to
the raid record on the next successful sync; it is retained after
completion. Raid Notes are leadership-only in the Sheet and saved
record; they are not added to the public sign-up panel. The **Complete
Raid** modal continues requesting Warcraft Logs and raid notes,
prefilled from the last synchronised values where available. Leaving
them unchanged will not clear existing entries.

**G2:G5: Instances and missing-buff counts** updates on each successful
sync, using only **Selected** and **Signed** character assignments, even
if **Show = No**. G2:G5 show Barrows 🔸, Barrows 🔹, Hyjal and Ony
(when configured), with the number of missing class/spec buff checks
in brackets. For example, **Barrows 🔸 (7)** means seven missing
buffs in the current checklist, **not seven players**. H2:H5 list
which buffs are missing. An unassigned team shows **No team selected**
in H and the full number of unchecked buffs in G. The rest of the top
section, including B4 Warcraft Logs and D4 Raid Notes, is unchanged.
This is a roster planning checklist based on WoW: Forever's October
2026 class changes, not proof that buffs are talented, cast or active.
Key Forever changes include raid-wide Warrior Battle Shout, Hunter
Trueshot Aura, Druid Moonkin/Leader of the Pack, and Shaman totems.
[Wowhead Forever changes](https://www.wowhead.com/forever/news/many-buffs-now-raid-wide-class-changes-in-latest-wow-forever-build-383456).
The bracket counts the currently implemented buff checks; it does not yet
represent a comprehensive list of every possible Forever debuff or cooldown.
If nobody is selected for an instance, H displays **No team selected**.

**Warcraft Logs:** Paste the HTTPS report URL into **B4**, next to
**Warcraft Logs**. Ogre reads changes on the next successful ~60-second
sync and updates the **existing public raid panel** with a
**Warcraft Logs: View report** link. Refreshing that message does not ping
anyone. Plain `warcraftlogs.com` and report subdomains such as
`classic.warcraftlogs.com` are accepted; the URL must link to
`/reports/...`. An invalid B4 link stays editable in the Sheet but is
not published. The bot retains the link if you subsequently complete or
archive the raid without entering another URL in the completion form.

Visible completed raid tabs continue syncing WCL and notes. Manually hidden
completed tabs are left alone until unhidden.

Below the fixed headings, registrations appear under five sections:

- **TANKS**, grouped by class
- **HEALERS**, grouped by class
- **MELEE**, grouped by class
- **DPS** (ranged DPS), grouped by class
- **ABSENT**, retained separately for audit, not selectable for public roster

| Column | Value | Owner |
| --- | --- | --- |
| A | Discord name (guild nickname) | Ogre |
| B | Character | Ogre |
| C | Class | Ogre |
| D | Spec | Ogre |
| E | Role | Ogre |
| F | Main / Alt | Ogre |
| G | Selection: Pending / Selected / Bench / Not selected | Leadership |
| H | Hyjal: No / Yes | Leadership |
| I | Barrows: No / 🔸 / 🔹 | Leadership |
| J | Ony: No / Yes | Leadership |
| K | Show: No / Yes | Leadership |
| L | WoWSims average known iLvl and enchant gaps | Ogre |
| M | Past 3 / Next 3 raid status | Ogre |
| N | Sign-up notes and absence reasons | Ogre |
| O | Leadership notes | Leadership |
| P-Q | Internal Discord and character IDs, hidden | Ogre |
| R | Signed-off/absent timestamp | Ogre |
| S | Original signed-at timestamp (UK) | Ogre |
| T | Status: Signed / Absent | Ogre |

Every new player is **Signed** by default. The raid poster's
**Default sign-up visibility** controls whether new players' names
appear on the public panel while sign-ups are being collected. The
per-player **Show on Discord** choice in Sheets overrides that default
after the next successful sync, even **before roster confirmation**.
Changing this choice never changes registration, availability or
leadership's selected teams. A player set to **No** remains recorded
privately but disappears from the public sign-up list.

The raid panel itself remains posted so members can still use Attend,
Characters, WoWSims and Absent. Hiding the raid panel entirely is a
separate action, not the Show on Discord choice.

Selection and instance assignments are used only after leadership
publishes the roster; **Show on Discord** continues to apply then.
Existing players' manual visibility choices in Sheets are preserved
when the poster's default is changed.

For a player to appear in Hyjal, for example: choose **Selected** (G),
**Yes** (K, Show), and **Yes** (H, Hyjal). For Barrows, select the relevant
team symbol in column I. Changes are noticed at the next successful ~60-second sync.
Hyjal remains one roster headed by 🔸🔹 and Barrows has two rosters
headed 🔸 and 🔹. The diamond headers stay visible after players appear.
Names have no bullet prefixes. The bot uses server custom emoji named
`Tank`, `Healer`, `DPS`, `Ranged` and class names such as `Warrior` and
`Mage`, when present; readable Unicode icons are the fallback.
Class/spec/role data travels with the private selected roster, including PUGs.

The private **Preview / Confirm Roster** screen groups names under the same
instance headings: Hyjal with both diamonds, then the orange and blue Barrows
groups. Names use small Discord subtext without repeating each player's
assignment. Large previews continue across private messages so every selected
name is shown. Opening the preview does not confirm the raid or change sign-ups.

The columns above describe the new displayed order, not the internal
Python/SQLite field indices. Ogre reads existing raid tabs by the original
column headings (including the old **Show on Discord**) before rewriting
the new layout, keeping manual selections and leadership notes attached
to the correct Discord ID. The internal IDs remain in P-Q.

The Discord paste field supports a maximum of 4,000 characters. Ogre
validates WoWSims JSON and confirms its character name matches the
registered character. Text-file uploads are no longer supported.
The old `/raid screenshot` and `/raid gear-review` commands have been
removed. Existing saved screenshots remain untouched in SQLite for
backward-compatible data retention, but are no longer collected or displayed.
The 4,000-character WoWSims paste limit remains unchanged.

In `/raid poster`, **Check Message Link** optionally accepts a copied
Discord message URL. When supplied, the raid panel shows a short
**Check: View message** hyperlink; when omitted, the Check line disappears.
There is no Check channel picker. The existing roster-announcement date
picker controls **Confirmation: <Discord relative timestamp>** on the panel
until the roster is actually confirmed. Players can hover to inspect the
full scheduled date and time.

When a player clicks **Absent**, Ogre moves the row to **ABSENT**, sets
selection to Not selected, disables public display, and removes them from
the public roster if necessary. The original sign-up time stays in column S,
and the signed-off timestamp and optional reason are recorded in R and N
alongside the original sign-up note. A player signing up again is reset to
Pending/No, requiring fresh leadership approval.

After publication, leadership's dropdown changes are reflected on the
**live public roster**, without another confirmation or ping. The raid stays
open to new registrations until it is archived. Absences can still remove
a player from the public roster, shown as an **Update:**. Acknowledgements
are recorded separately and never alter attendance or roster selection.
Archived raids accept no further roster edits or absences.

Leadership-entered PUG rows in the old Roster Builder migrate to the new
tab; PUGs can also be added manually, with a character, class/spec/role,
Status=Signed and the leadership dropdowns. Do not overwrite the hidden
internal ID columns for registered guild members.

## Forever Gear: iLvl, enchants and Wowhead

The permanent **Forever Gear** tab shows the most recent WoWSims export
for every registered character: character level, class/spec, professions
and 17 equipment slots. Each equipped item has a human-readable name and
base **iLvl** when found in the **Forever-specific item catalog** and a
working clickable **View item** link to `wowhead.com/forever/item=ID`.
A new **iLvl** column displays each resolved piece. Existing eight-column
gear tabs are automatically extended to nine columns without losing data.

The external item data comes from
[Napalmsteak's WoW Classic Item Caches](https://github.com/Napalmsteak/WoW-Classic-Item-Caches),
specifically the **Forever** data snapshot, not Classic Era. Ogre fetches
it at most once every seven days, keeps a compact cache alongside the
raid database, and continues syncing Sheets if the catalog is unavailable.
Unknown item levels appear blank, never guessed. The source is a beta
snapshot, so individual items may change after updates to Forever.

The raid tab's **WoWSims gear** cell contains a compact summary such as
**iLvl 70.0 · 2 enchant gaps**. If only some equipped items are known,
Ogre labels the average with its coverage, e.g.
**iLvl 65.0 (2/3 known) · 2 enchant gaps**. An average of equipped items
is an *informal gear comparison*, not a simulation, performance score or
Blizzard's canonical character-item-level calculation.

**Enchant gaps** count equipped items without a reported enchant in a
conservative set of commonly enchantable slots: head, shoulders, back,
chest, wrists, hands, legs, feet and main hand. They deliberately exclude
rings, trinkets, necks, off-hand frills and ranged scopes, and they do not
check whether the particular item supports an appropriate enchant.
Some head/shoulder enchants have progression requirements. Use this
as a checklist for review, not an automatic raid readiness judgement.

Player names and raw JSON are not published to any external item-data
service. The exported item IDs alone are looked up against the catalog.
Item links may be opened manually; the bot does not upload players'
WoWSims exports to Wowhead.

## Past 3 | Next 3 on every raid tab

A new **T** column displays a member's registration history for the
three scheduled raids before and three after that tab's raid date,
in chronological order. Example:

`Missing · Missing · Attend | Attend · Absent · Absent`

**Attend** means the member registered as available, **Absent** means
they recorded absence, and **Missing** means no registration or absence
was recorded. If an earlier raid has actual attendance data, that result
(e.g. **Attended**, **Late**, **No-show**) takes precedence. Therefore
**Attend** for a past raid is not proof they physically attended.

A dash (—) means no raid exists in that position; it is different from
**Missing**. Ogre cannot infer when a member joined the guild, so a
Missing value may predate their membership. Entries are joined by the
person's **Discord ID**, across mains/alts, rather than by character ID.
The **current raid** is excluded from the six statuses.

The Sheet and Discord rosters are sorted with the Discord account identified by
`OWNER_ID` first within its Tanks, Healers, Melee or Ranged section,
before the remaining class/character order. No hidden character IDs are
modified by this presentation choice. Discord adds
`<:ogrecrown:1566947968760946948>` after that account's character name. The crown
is based on the saved member ID, not a matching display name, and is never
written into character names or Sheet cells. Existing sign-ups, notes, selections
and attendance records are preserved when this display update rolls out.

This view is bot-maintained. Don't manually edit the tab; use the
WoWSims paste function in Discord to update equipment. Existing raid
sheet leadership selections and notes remain separate and editable.

## Previous RM tabs and data safety

On the first successful per-raid tab sync, Ogre hides the six old
`RM -` tabs. They are **never deleted** by the migration and are no longer
used as live roster data. Existing leadership selections, notes and PUG
rows for that raid are imported once. To inspect historical values, use
Google Sheets' **View > Hidden sheets** menu.

SQLite at `/data/raid_signup.db` remains authoritative for registration,
attendance and gear uploads. Only parsed equipment metadata, never raw WoWSims JSON or gear
screenshots, is sent to the new **Forever Gear** tab. The existing roll/coin database and
all other Sheet tabs are untouched.

For safety, run the Python regression suite before deploying. Do not merge
the development branch directly into another branch without checking the
full ancestry for unrelated commits.

## Google Sheets / Fly.io connection

The ChatGPT Google Drive connection cannot authenticate a Fly.io bot. Ogre
uses a Google service account with the Google Sheets API enabled. Share
the restricted `<Hyped> Forever` workbook with its service account email
as Editor, not a Google Cloud project-wide Editor role. Set these secrets
in Fly.io, keeping the JSON key outside Git and Discord:

- `RAID_SHEETS_ID` = the private Google workbook ID
- `RAID_GOOGLE_SERVICE_ACCOUNT_B64` = Base64 UTF-8 encoding of the JSON key

On Windows PowerShell, after validating the downloaded key:

```powershell
$path = "S:\\WoW\\Discord Bot\\hyped-ogre-47707d794b16.json"
$json = Get-Content -Raw -LiteralPath $path
$key = $json | ConvertFrom-Json
if ($key.type -ne "service_account" -or !$key.private_key) {
    throw "Invalid service account JSON"
}
$secret = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
fly secrets set -a hyped-diceman --stage "RAID_GOOGLE_SERVICE_ACCOUNT_B64=$secret"
Remove-Variable json,key,secret
```

These Fly secrets have already been configured on the live app. No need to
recreate or reapply them when deploying code changes. Run `/raid sync`
in private `#ogre` to diagnose permissions or synchronisation problems.

## Completion and acknowledgement updates

Publish + ping selected sends a separate message in the stored raid channel.
Ogre asks for `<:check:1566948806535159808>` and removes acknowledged members
from the outstanding list, without removing their roster places. Older Unicode
check reactions remain accepted for messages already posted.

Confirmed instance and role headings show actual player counts. Both collecting
and confirmed role/name lists use Discord subtext. WCL appears directly below
Check and continues to sync both ways with the Sheet's report cell.

H2:H5 uses overflow rather than wrapping. Class through Show use equal column
widths. Confirmation and archived timestamps display readable UK dates/times.

Complete Raid opens notes, WCL and loot log/link fields, then asks for confirmation.
The loot field saves a link or short excerpt in raid notes, not a parsed Gargul
loot import. Pending selected/bench attendance becomes Attended; recorded
exceptions are preserved. The raid closes and the Sheet stays visible until
leadership hides it manually. Existing manually hidden tabs are respected.

Forever Roster column I shows recorded attendance in the latest four completed
guild raids, keyed by Discord ID across characters. Three recorded attendances
qualify. Duplicate character records never count twice; pending, absent, excused
and future uncompleted raids earn no credit. This is an eligibility indicator;
it does not automatically change Discord roles or in-game ranks.
