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
   The date, WCL link and notes are stored on the raid tab. The tab is hidden
   to keep the active Sheet tidy, **not deleted**. Use Google Sheets' hidden
   sheets menu to view it later.

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

**Warcraft Logs:** Paste the HTTPS report URL into **B4**, next to
**Warcraft Logs**. Ogre reads changes on the next successful ~60-second
sync and updates the **existing public raid panel** with a
**Warcraft Logs: View report** link. Refreshing that message does not ping
anyone. Plain `warcraftlogs.com` and report subdomains such as
`classic.warcraftlogs.com` are accepted; the URL must link to
`/reports/...`. An invalid B4 link stays editable in the Sheet but is
not published. The bot retains the link if you subsequently complete or
archive the raid without entering another URL in the completion form.

Make B4 edits **before archiving**. Archived raid tabs are hidden and
not monitored for subsequent Sheet edits.

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
| G | Sign-up notes (and absence note) | Ogre |
| H | Original signed-at timestamp | Ogre |
| I | Signed / Absent | Ogre |
| J | Selection: Pending / Selected / Bench / Not selected | Leadership |
| K | Show on Discord: No / Yes | Leadership |
| L | Hyjal: No / Yes | Leadership |
| M | Barrows: No / 🔸 / 🔹 | Leadership |
| N | Ony: No / Yes | Leadership |
| O | Leadership notes | Leadership |
| P | WoWSims character level, equipped-slot and reported-enchant summary | Ogre |
| Q-R | Internal Discord and character IDs, hidden for readability | Ogre |

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

For a player to appear in Hyjal, for example: choose **Selected** (J),
**Yes** (K) and **Yes** (L). For Barrows, select the relevant team symbol in
column M. Changes are noticed at the next successful ~60-second sync.
Hyjal remains one roster headed by 🔹🔸 and Barrows has two rosters
headed 🔸 and 🔹. The diamond headers stay visible after players appear.
Names have no bullet prefixes. The bot uses server custom emoji named
`Tank`, `Healer`, `DPS`, `Ranged` and class names such as `Warrior` and
`Mage`, when present; readable Unicode icons are the fallback.
Class/spec/role data travels with the private selected roster, including PUGs.

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
the public roster if necessary. The original sign-up time stays in column H,
and the signed-off timestamp and optional reason are recorded in column G
alongside any original sign-up note. A player signing up again is reset to
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

## Forever Gear: WoWSims equipment overview

A separate **Forever Gear** tab is created automatically when at least one
character has a valid WoWSims export. It is refreshed during the existing
Google Sheets sync. The raid tab's **WoWSims gear** column shows a compact
summary such as **Lv 15 · 9/17 equipped · 1 enchant reported**.

The permanent gear tab groups equipment by character. Its dark heading
shows character name, class/spec, character **level**, professions if
provided, equipped-slot count, enchant count and time of latest export.
Below it, each of the 17 standard WoWSims gear slots has a separate row
with **Item #ID**, **Enchant #ID** (or **Not reported**) and a Wowhead
Classic item link. Only the latest saved export per character is shown.
No raw JSON is sent to Sheets. Changing WoWSims in Discord refreshes
the details on the next successful sync, normally about once a minute.

**Scope:** This is a visual equipment inventory, not a simulation, item
score or gear audit. The WoWSims export has character level and item/
enchant IDs, but does **not** supply item names, item icons, rarity or
item level. Determining those requires a separate, verified item
database. **Not reported** for an enchant must not be interpreted
automatically as an error: not every item supports or requires enchantment.

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
