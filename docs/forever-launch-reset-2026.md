# Hyped Ogre: 4 November 2026 Forever launch reset

This is a **one-time** wipe of test raids, test character registrations and
LFG/LFM entries, NOT a full Fly volume or bot database reset. The guild rolls
and coins are stored in `/data/bot.db`: do not copy over, delete, restore,
attach, VACUUM or otherwise modify this file, its journal or its WAL.

## Already prepared

- Original Google workbook: [<Hyped> Forever](https://docs.google.com/spreadsheets/d/1If8ssP3d0-BK97w8wnxrbaxzILB-tDsL75ZNCUJKX_Q/edit).
- Prelaunch workbook backup created on 4 November 2026:
  [<Hyped> Forever - Pre-launch test backup](https://docs.google.com/spreadsheets/d/1Kjl731NrcGrcO5Na-iRq5lFa5f-OASS2TljESYdZ7cY/edit).
- GitHub PR #110 adds `python -m hyped_ogre.launch_reset` with a no-write audit,
  count-checked execution, consistent SQLite backups and a repeat-run marker.
- All live DB code operates ONLY on `/data/raid_signup.db` and
  `/data/lfg_board.db`. The reset script never opens `bot.db` or any other
  feature DB. Both reset databases are on the existing `diceman_data` Fly
  volume, which must NOT be deleted or replaced.

## Exact order

1. Save/copy the three current LFG test entries you intend to repost.
2. Merge the PR **only after green Linux and Windows tests**, then confirm the
   automatic Fly deployment succeeded. A normal deploy does NOT run the wipe.
3. In VS Code PowerShell:

   ```powershell
   fly ssh console -a hyped-diceman -C "python -m hyped_ogre.launch_reset --audit"
   ```

   Record the three printed totals: `characters`, `raids` and `lfg`.
   Also note `raid_discord_posts_to_remove` and
   `raid_confirmations_to_remove`. **No live data changes during the audit.**
   If totals look unexpectedly high, investigate before running anything else.
4. Make a manual Fly volume snapshot, separate from the existing SQLite
   point-in-time backups. Identify the correct `diceman_data` volume:

   ```powershell
   fly volumes list -a hyped-diceman
   fly volumes snapshots create VOL_ID -a hyped-diceman
   fly volumes snapshots list VOL_ID -a hyped-diceman
   ```

   Wait for an actually completed snapshot, not merely a requested snapshot.
   A full-volume rollback could also revert roll coins earned since this
   snapshot: **never restore the entire volume to undo just the reset**.
   Instead use the script-created `raid_signup.db`/`lfg_board.db` backups.
5. Execute using the *actual audit counts*, not made-up numbers:

   ```powershell
   fly ssh console -a hyped-diceman -C "python -m hyped_ogre.launch_reset --execute --confirm FOREVER-2026-LAUNCH --expect-characters N --expect-raids N --expect-lfg N"
   ```

   This makes two verified SQLite backups in a new
   `/data/forever-launch-*/` directory and empties all test raid/character/LFG
   data with foreign-key validation. It retains DB schemas, saved Discord
   Characters/LFG board message IDs and bot administration settings.
   LFG boards are switched to **Everyday groups** mode in their saved settings.
   A one-time marker stops an accidental second reset after live signups.
6. Repeat the `--audit` command to verify the three totals are **0, 0, 0**.
   Do this before new characters sign up or anyone creates the first live raid.
7. Remove old test **raid** sign-up/confirmation messages from Discord using
   the IDs recorded in step 3. Do not delete the existing Characters and LFG
   board posts. The SQLite operation alone cannot delete Discord messages.
8. Clean test Google Sheets **after** the SQLite reset and before the first
   real signup or raid, using the exact original workbook and its backup:

   - Delete only the generated raid tabs
     `Raid #1 - Wed 09 Dec 2026` and
     `Raid #2 - Wed 04 Nov 2026` (reconfirm actual titles before deletion).
   - Clear content only, NOT formatting/validation, from
     `Forever Roster!A6:O501`,
     `Forever Attendance!A6:O1001` and
     `Forever Gear!A4:I1500`.
   - Clear old test entries below row 1 in hidden
     `RM - Characters`, `RM - Sign-ups`, `RM - Gear Checks`,
     `RM - Roster Builder`, `RM - Attendance` and
     `RM - Raid History` while retaining their headers and validation.
   - **Never** edit historical non-Forever guild tabs such as `Roster`,
     `Assignments`, `Attendance`, old boss sheets, etc.
   - Verify the blank rows and preserved headers. Keep the backup copy.
9. In Discord, use `/postcharacters` in the character board channel to
   refresh its existing post. Run `/classrooms sync` if automatic access
   reconciliation has not yet completed (it normally runs every 60 seconds).
   In the existing LFG channel, use `/postlfglfm` to refresh the original
   board with live-mode buttons (it should not create a duplicate).
   Players can now register new mains/alts, choose class/off-spec and post
   fresh LFG activities and dungeon plans.
10. Create the first real raid using the normal poster; the empty raid table
    starts again at Raid #1. Verify that a new registration flows into the
    character board and Sheets and that old attendance scores are gone.

## Guarantees and limitations

- The reset script does **not** touch `/data/bot.db`, rolling/gamble coins,
  `countdown.db`, `polls.db` or `streams.db`.
- The two target databases are backed up individually using SQLite's online
  backup API, which includes committed WAL content, and validated before wipe.
- The complete volume snapshot is an additional precaution, not the preferred
  per-database restore mechanism.
- The operation does not remove old Discord posts or Sheet cells for safety;
  those require their own verified steps.
- Dockerfile already copies `hyped_ogre/`, so the command is available
  on the Fly machine after merging PR #110 and its successful deployment.
- The script is **never executed automatically** by a merge or bot restart.
- For any error, do not rerun the reset blindly: inspect the error and the
  `/data/forever-launch-*/` backup directory first.
