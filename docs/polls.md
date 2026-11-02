# Anonymous polls

Use `/poll create` in the private Ogre control channel. Poll setup, management
and private results are owner-only. The control channel is Ogre
(`1564594901746716713`), configured with `POLL_ADMIN_CHANNEL_ID`, separately from
the logs channel. It must deny
View Channel to `@everyone`. Voting works for members who can see the posted poll.

## Create and post

1. Run `/poll create`. Enter the question/title and optional details. Options
   start as **Yes**, **No** and **Abstain**; remove Abstain if it is not needed,
   or replace them with 2–10 choices, one per line, up to 80 characters each.
   Every option gets its own grey voting button, including Abstain.
2. Leave the deadline blank to close manually, or enter `30m`, `2h`, `7d` or
   a UK date-time such as `2026-11-04 20:00`. Absolute times use Europe/London
   by default. Clock-change gaps are rejected; a repeated hour needs an offset.
3. A private draft card appears in Ogre. **Configure** lets you choose the
   destination, display mode and single/multiple-choice voting. **Preview**
   shows the exact public layout with voting disabled. **Edit question/options**
   reopens the draft editor.
4. Click **Post poll** when ready. This sends it to the selected channel.

The question is the embed title and the choices appear in its body. The right
thumbnail is always `https://i.vgy.me/dy0EEH.png`. Every poll button, including
Yes/No/Abstain, configuration and confirmation buttons, uses Discord's secondary grey.
Public polls and result summaries have no footer. Poll IDs and recovery details
stay on the private Ogre card. A published result's title links to the original
poll when its message is available.

## Votes and visibility

All voting is anonymous to members and poll managers. There is no voter list
or manager command that displays who chose an option.

| Display mode | What members see |
| --- | --- |
| Hidden totals, the default | Options and deadline; no turnout or option counts until results are published |
| Participation only | Number of people who voted; option counts remain hidden |
| Live results | Option counts, percentages and turnout, refreshed roughly every five seconds |

The owner can change the display mode while voting is open: use
`/poll manage poll_id` in Ogre, click **Display/deadline**, and choose
**Hidden totals**, **Participation only** or **Live results** in the display
menu. Saved votes and the public poll message are preserved. Hiding a previously
visible count cannot make people forget or delete copies of counts already seen.
The question, options, voting type and destination lock when posted, so existing
ballots cannot silently be changed to answer a different question.

Single choice allows one current selection per member. Choosing another option
replaces it. In multiple-choice mode, each option toggles on/off and a person
counts once in turnout. Percentages are of voters, so multiple-choice totals
can exceed 100%. **My vote** privately shows your choices; **Remove vote** clears
them. No public vote confirmation or voter identity is posted.

## Close, review and publish

The deadline is enforced when each vote is saved, even if the scheduled close
worker is delayed or the bot restarts. The worker closes due polls on startup
and normally checks every five seconds. **Close poll** or `/poll close poll_id`
also closes manually after private confirmation.

Closing disables voting and puts the final aggregate result on the private
Ogre management card. In Hidden/Participation mode, full public results remain
hidden. Live mode continues showing the counts that were already visible.

Review the private totals, then click **Publish results** or run
`/poll publish poll_id`. Ogre posts a final summary in the original poll channel
and shows final totals on the closed poll. Repeat clicks do not intentionally
post another summary. Closed polls cannot reopen or change their ballots; create
a new draft for a fresh vote.

`/poll list` lists the latest twenty polls/drafts, `/poll manage poll_id` opens
private controls again, and `/poll results poll_id` shows private aggregate
results, including while a poll is open. An unpublished draft can be cancelled.

## Persistence and privacy

The feature has its own `/data/polls.db` on the existing persistent volume.
It never reads or writes roll, coin, character or attendance tables. Existing
database paths remain protected by the deployment guard. Votes are transactional;
concurrent votes, vote changes and closing cannot split or duplicate a ballot.

Ballots contain no Discord member IDs or names. While voting is open, the bot
uses a different keyed identifier for each poll to enforce one ballot and allow
changes after a restart. Closing atomically saves the final counts/turnout and
removes the individual ballot records and their linking secret. The host and
Discord still process interactions, and operator backups may contain older
records. This is anonymity within the poll feature, not protection against every
possible service operator or timing inference from live counts.

Public controls use versioned dynamic IDs and survive restarts. Pending card
updates retry without changing saved ballots. After a send interrupted by a
restart, Ogre checks the latest 100 messages before reposting. Public polls are
identified by their voting controls and results by their original-poll link;
private cards retain recovery footers. Older public footer markers are still
recognised so existing polls can be updated without reposting. This reduces
duplicates but cannot give a distributed
exactly-once guarantee if old messages are deleted or buried in a busy channel.
Missing permissions can delay card updates or private results; saved totals remain
in the database. Fix permissions and use `/poll manage` or wait for the retry.

On startup, saved poll control cards follow the configured Ogre channel. When
that channel changes, Ogre recreates the cards there, without moving public
polls, changing options or modifying votes. Old cards in the previous channel
are no longer updated. Existing posted polls keep their original options;
to add Abstain to one that is already open, create a new poll.

This release is tested offline and in the Docker runtime. A real Discord poll
should be created by the owner to verify the server's actual channel permissions
and the appearance of the remote thumbnail.
