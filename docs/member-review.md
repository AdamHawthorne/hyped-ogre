# Direct guild member export

/guild-member-export privately returns hyped-members.txt for Hyped Guild Tools 0.5.4.
No companion app, public HTTP endpoint or new database is required.
Command is Hyped-only and restricted to guild owner, bot owner or Leadership.
Discord command permissions also require Manage Guild by default; an administrator
can grant the Leadership role access under Server Settings / Integrations.

It reads all registered full names, mains/alts, and checks current Discord member
presence and the configured HYPED_RANK_DISCORD_ROLE_ID or FOREVER_ROLE_ID role.
Missing configuration, API failure or duplicate normalized names rejects the export.
Anonymous G1/G2 groups associate characters from one Discord account without IDs.
Includes LFG registrations as well as roster registrations. No character records change.

Copy the complete attachment into /hyped import, Preview, Accept, /hyped members.
Choose the actual target rank, optionally include alts, Prepare rank sync.
Enable clicks in Rank Review, Apply rank and Confirm per player. Only adjacent
Social / Alt promotions are permitted. Higher/special ranks retained; no demotions.
Regular+ eligibility is not yet assessed. No attendance eligibility is invented.
Export view lasts 24 hours; actions require export <=15 minutes and roster <=2 minutes.

The new extension is loaded by bot.setup_hook; existing on_ready guild command sync
registers it after deployment. No secrets or Fly configuration changes required if
FOREVER_ROLE_ID is already set. Test the private command and one game promotion.

Tests: python -m unittest discover -s tests -p 'test_guild_member_sync.py'.
