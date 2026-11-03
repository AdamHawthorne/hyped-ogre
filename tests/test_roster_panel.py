"""Roster layout, private self-service and durable member activity checks."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
from cogs import raid_signup as raid, raid_schedule as schedule
from hyped_ogre.features.characters import roster, availability, hub, absence_ui
from hyped_ogre.features.raids import notifications
from hyped_ogre.features.classrooms.config import GUILD_ID, LEADERSHIP_ROLE_ID


class RosterDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'raid.db')
        raid.create_schema(self.path)
        schedule.ensure_raid_fields(self.path)
        raid.save_character(42, 'Snore', 'Warrior', 'Protection', 'Tank', self.path)
        self.cid = raid.member_characters(42, self.path)[0][0]

    def events(self):
        with raid.database(self.path) as con:
            return availability.pending(con)

    def test_percent_uses_last_four_completed_raids_and_account_not_character(self):
        raid.save_character(42, 'Alt', 'Mage', 'Frost', 'DPS', self.path)
        for n in range(1, 7):
            schedule.save_raid(self.path, n, f'2026-12-{n:02} 19:00', 'Hyjal (20m)', wowsims_policy='optional')
        with raid.database(self.path) as con:
            con.execute("UPDATE raids SET archived_at='complete' WHERE raid_id<=5")
            for n in (1, 2, 3, 6):
                con.execute("INSERT INTO attendance (raid_id,discord_id,character_id,status) VALUES (?,?,?,?)",
                            (n,42,self.cid,'Attended'))
        self.assertEqual([r['attendance'] for r in roster.rows(self.path)], ['50%', '50%'])

    def test_complete_item_metadata_is_numeric_and_empty_data_is_na(self):
        self.assertEqual(roster.rows(self.path)[0]['ilvl'], 'N/A')
        export = {'name':'Snore','class':'warrior','spec':'protection','version':'v3.2.6',
                  'level':60,'gear':{'items':[{'id':5},{'id':6}]}}
        raid.save_wowsims_export(42,self.cid,json.dumps(export),self.path)
        with patch.object(roster.items,'cached_catalog',return_value={'5':{'level':50},'6':{'level':60}}):
            self.assertEqual(roster.rows(self.path)[0]['ilvl'], '55.0')
            self.assertEqual(roster.rows(self.path)[0]['level'], 60)

    def test_absence_is_private_idempotent_and_does_not_change_raid_history(self):
        schedule.save_raid(self.path, 1, '2026-12-09 19:00', 'Hyjal (20m)', wowsims_policy='optional')
        raid.save_signup(1,42,self.cid,self.path)
        self.assertTrue(availability.save(self.path,42,' Two weeks ', ' Private reason '))
        self.assertFalse(availability.save(self.path,42,'Two weeks','Private reason'))
        with raid.database(self.path) as con:
            self.assertEqual(con.execute('SELECT availability FROM signups').fetchone()[0], 'Available')
            self.assertEqual(con.execute('SELECT COUNT(*) FROM raid_absences').fetchone()[0], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM attendance').fetchone()[0], 0)
        public = str([e.to_dict() for e in roster.render(roster.rows(self.path))])
        self.assertNotIn('Private reason',public)
        self.assertNotIn('Two weeks',public)
        self.assertEqual(len(self.events()),1)
        self.assertTrue(availability.clear(self.path,42))
        self.assertFalse(availability.clear(self.path,42))
        self.assertEqual([e['kind'] for e in self.events()],['long_absence','returned'])

    def test_repeated_concurrent_absence_forms_only_queue_one_change(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: availability.save(self.path,42,'Away','Note'), range(8)))
        self.assertEqual(len(self.events()),1)
        raid.create_schema(self.path)
        self.assertEqual(len(self.events()),1)

    def test_invalid_absence_does_not_save_or_queue(self):
        for period,note in (('', ''), ('x'*101,''), ('Away','x'*1001)):
            with self.assertRaises(ValueError):
                availability.save(self.path,42,period,note)
        self.assertEqual(self.events(),[])

    def test_removal_queues_name_only_after_owned_character_is_deleted(self):
        with self.assertRaises(ValueError):
            raid.delete_character(99,self.cid,self.path)
        self.assertEqual(self.events(),[])
        self.assertEqual(raid.delete_character(42,self.cid,self.path),'Snore')
        self.assertEqual(raid.member_characters(42,self.path),[])
        self.assertEqual([(e['kind'],e['note']) for e in self.events()],[('character_removed','Snore')])

    def test_failed_removal_does_not_queue_or_erase_signups(self):
        schedule.save_raid(self.path, 1, '2026-12-09 19:00', 'Hyjal (20m)', wowsims_policy='optional')
        raid.save_signup(1,42,self.cid,self.path)
        with self.assertRaises(ValueError):
            raid.delete_character(42,self.cid,self.path)
        self.assertEqual(self.events(),[])
        self.assertEqual(len(raid.member_characters(42,self.path)),1)

    def test_failed_notification_insert_rolls_back_removal(self):
        with patch.object(availability,'enqueue',side_effect=sqlite3.OperationalError('failure')):
            with self.assertRaises(sqlite3.OperationalError):
                raid.delete_character(42,self.cid,self.path)
        self.assertEqual(len(raid.member_characters(42,self.path)),1)


class LayoutTests(unittest.TestCase):
    def row(self, n, role='DPS', klass='Mage', spec='Frost', is_main=True):
        return dict(character_id=n, discord_id=100000000000000000+n,
                    name=f'Character{n:04}', class_name=klass, spec=spec,
                    role=role, is_main=is_main, attendance='75%', ilvl='67.5')

    def test_grouping_columns_and_every_main_in_one_guild_sized_embed(self):
        specs = (('Tank', 'Warrior', 'Protection'),
                 ('Healer', 'Priest', 'Holy'),
                 ('DPS', 'Rogue', 'Combat'),
                 ('DPS', 'Mage', 'Frost'))
        data = [self.row(n, *specs[n % 4]) for n in range(40)]
        embeds = roster.render(data)
        self.assertEqual(len(embeds), 1)
        self.assertEqual(embeds[0].title, '<Hyped> Roster')
        self.assertEqual(embeds[0].description, 'Ogre keep track. You add or update.\n\u200b')
        self.assertEqual(embeds[0].thumbnail.url, roster.ROSTER_IMAGE)
        self.assertIsNone(embeds[0].footer.text)
        self.assertEqual([f.name for f in embeds[0].fields[::3]],
                         ['Tanks (10)', 'Healers (10)', 'Melee (10)', 'Ranged (10)'])
        content = str(embeds[0].to_dict())
        self.assertNotIn('WCL', content)
        for row in data:
            self.assertEqual(content.count(row['name']), 1)
            self.assertIn(f"<@{row['discord_id']}>", content)
        for i in range(0, len(embeds[0].fields), 3):
            fields = embeds[0].fields[i:i+3]
            self.assertEqual([f.name for f in fields[1:]], ['Attendance', 'Lvl'])
            self.assertEqual(len(set(len(f.value.splitlines()) for f in fields)), 1)
            self.assertTrue(all(v == '75%' for v in fields[1].value.splitlines()))
            self.assertTrue(all(v == 'N/A' for v in fields[2].value.splitlines()))
        self.assertNotIn('Character', [f.name for f in embeds[0].fields])

    def test_melee_and_ranged_follow_raid_signup_role_classification(self):
        data = [self.row(1, 'DPS', 'Hunter', 'Survival'),
                self.row(2, 'DPS', 'Hunter', 'Beast Mastery'),
                self.row(3, 'DPS', 'Druid', 'Feral'),
                self.row(4, 'Tank', 'Druid', 'Feral')]
        fields = roster.render(data)[0].fields
        groups = {fields[i].name: fields[i].value for i in range(0, len(fields), 3)}
        self.assertIn('Character0001', groups['Melee (2)'])
        self.assertIn('Character0003', groups['Melee (2)'])
        self.assertIn('Character0002', groups['Ranged (1)'])
        self.assertIn('Character0004', groups['Tanks (1)'])

    def test_alts_are_hidden_even_when_registered(self):
        main = self.row(1, 'Tank', 'Warrior', 'Protection')
        alt = self.row(2, 'DPS', 'Rogue', 'Combat', is_main=False)
        embed = roster.render([alt, main])[0]
        content = str(embed.to_dict())
        self.assertIn('Character0001', content)
        self.assertNotIn('Character0002', content)
        self.assertEqual([f.name for f in embed.fields[::3]],
                         ['Tanks (1)', 'Healers (0)', 'Melee (0)', 'Ranged (0)'])

    def test_sort_by_level_then_attendance_descending_and_name_for_ties(self):
        # All players remain in the same category, including those without data.
        players = [self.row(n, 'Tank', 'Warrior', 'Protection') for n in range(1, 10)]
        updates = [
            ('Level39', 39, '90%'),
            ('Level60Low', 60, '25%'),
            ('Level60High', 60, '100%'),
            ('Level60MissingAtt', 60, 'N/A'),
            ('Level39High', 39, '100%'),
            ('UnknownHighAtt', None, '100%'),
            ('UnknownMissingAtt', None, 'N/A'),
            ('Level60TieZ', 60, '50%'),
            ('Level60TieA', 60, '50%'),
        ]
        for player, (name, level, attendance) in zip(players, updates):
            player.update(name=name, level=level, attendance=attendance)
        embed = roster.render(players)[0]
        ordered = [
            'Level60High', 'Level60TieA', 'Level60TieZ', 'Level60Low',
            'Level60MissingAtt', 'Level39High', 'Level39',
            'UnknownHighAtt', 'UnknownMissingAtt',
        ]
        character_lines = embed.fields[0].value.splitlines()
        for index, name in enumerate(ordered):
            self.assertIn(name + ' /', character_lines[index])
        self.assertEqual(embed.fields[1].value.splitlines(),
                         ['100%', '50%', '50%', '25%', 'N/A', '100%',
                          '90%', '100%', 'N/A'])
        self.assertEqual(embed.fields[2].value.splitlines(),
                         ['60', '60', '60', '60', '60', '39', '39', 'N/A', 'N/A'])
        self.assertEqual(len(embed.fields), 12)
        self.assertIsNone(embed.footer.text)

    def test_level_uses_validated_saved_profile_value_or_na(self):
        main = self.row(1, 'Tank', 'Warrior', 'Protection')
        fields = roster.render([main])[0].fields
        self.assertEqual(fields[2].value, 'N/A')
        main['level'] = 43
        self.assertEqual(roster.render([main])[0].fields[2].value, '43')
        for invalid in (999, None, True, 0, -1):
            main['level'] = invalid
            self.assertEqual(roster.render([main])[0].fields[2].value, 'N/A')

    def test_thirty_distinct_guild_players_fit_one_embed(self):
        specs = (('Tank', 'Warrior', 'Protection'),
                 ('Healer', 'Priest', 'Holy'),
                 ('DPS', 'Rogue', 'Combat'),
                 ('DPS', 'Mage', 'Frost'))
        players = [self.row(n, *specs[n % 4]) for n in range(30)]
        for player in players:
            player['name'] = f"LongCharacterName{player['character_id']:04}"
            player['level'] = (player['character_id'] % 60) + 1
        embeds = roster.render(players)
        self.assertEqual(len(embeds), 1)
        embed = embeds[0]
        self.assertLessEqual(len(embed), 6000)
        self.assertEqual(len(embed.fields), 12)
        for index in range(0, len(embed.fields), 3):
            fields = embed.fields[index:index+3]
            self.assertEqual([f.name for f in fields[1:]], ['Attendance', 'Lvl'])
            self.assertEqual(len({len(f.value.splitlines()) for f in fields}), 1)
        content = str(embed.to_dict())
        for player in players:
            self.assertEqual(content.count(player['name']), 1)

    def test_thirty_players_even_in_one_role_fit_one_embed(self):
        players = [self.row(n) for n in range(30)]
        embeds = roster.render(players)
        self.assertEqual(len(embeds), 1)
        self.assertEqual(len(embeds[0].fields) % 3, 0)
        content = str(embeds[0].to_dict())
        for player in players:
            self.assertEqual(content.count(player['name']), 1)

    def test_large_roster_keeps_all_mains_within_discord_limits(self):
        data = [self.row(n) for n in range(400)]
        embeds = roster.render(data)
        self.assertGreater(len(embeds), 1)
        content = str([e.to_dict() for e in embeds])
        for row in data:
            self.assertEqual(content.count(row['name']), 1)
        for embed in embeds:
            self.assertLessEqual(len(embed), 6000)
            self.assertLessEqual(len(embed.fields), 25)
            for field in embed.fields:
                self.assertLessEqual(len(field.value.encode('utf-16-le'))//2, 1024)

    def test_empty_roster_has_all_role_sections_and_no_fake_numbers(self):
        embed = roster.render([])[0]
        self.assertEqual([f.name for f in embed.fields[::3]],
                         ['Tanks (0)', 'Healers (0)', 'Melee (0)', 'Ranged (0)'])
        self.assertIn('No mains registered', str(embed.to_dict()))
        self.assertNotIn('0%', str(embed.to_dict()))


class PanelActionsTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self,member=42,guild=GUILD_ID):
        return SimpleNamespace(guild_id=guild,user=SimpleNamespace(id=member),client=SimpleNamespace(dispatch=MagicMock()),
            response=SimpleNamespace(send_message=AsyncMock(),send_modal=AsyncMock(),defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))

    async def test_persistent_buttons_have_correct_styles_and_private_actions(self):
        raid_cog=SimpleNamespace(on_gear=AsyncMock())
        cog=SimpleNamespace(open_characters=AsyncMock(),bot=SimpleNamespace(get_cog=lambda _:raid_cog))
        view=hub.HubView(cog,300)
        self.assertTrue(view.is_persistent())
        self.assertEqual([(b.label,b.style) for b in view.children],
                         [('Add',discord.ButtonStyle.success),('Manage',discord.ButtonStyle.secondary),
                          ('WoWSims',discord.ButtonStyle.secondary),('Remove / absent',discord.ButtonStyle.danger)])
        interaction=self.interaction()
        await view.children[0].callback(interaction)
        self.assertIsInstance(interaction.response.send_message.call_args.kwargs['view'],raid.ClassPickerView)
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        await view.children[1].callback(interaction)
        cog.open_characters.assert_awaited_once_with(interaction)
        await view.children[2].callback(interaction)
        raid_cog.on_gear.assert_awaited_once_with(interaction)
        await view.children[3].callback(interaction)
        self.assertIsInstance(interaction.response.send_message.call_args.kwargs['view'],absence_ui.AbsenceActions)
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        self.assertFalse(await view.interaction_check(self.interaction(guild=99)))

    async def test_absence_menu_and_modal_reject_other_member_or_guild(self):
        for interaction in (self.interaction(member=99),self.interaction(guild=99)):
            self.assertFalse(await absence_ui.AbsenceActions(42).interaction_check(interaction))
            with patch.object(availability,'save') as save:
                await absence_ui.AbsenceModal(42).on_submit(interaction)
                save.assert_not_called()

    async def test_modal_saves_then_dispatches_and_responds_privately(self):
        modal=absence_ui.AbsenceModal(42)
        modal.period._value='November'
        modal.note._value='Private note'
        interaction=self.interaction()
        with patch.object(availability,'save',return_value=True) as save:
            await modal.on_submit(interaction)
        save.assert_called_once_with(raid.RAID_DB,42,'November','Private note')
        interaction.client.dispatch.assert_called_once_with('raid_activity_changed',GUILD_ID)
        self.assertTrue(interaction.followup.send.call_args.kwargs['ephemeral'])

    async def test_remove_selection_uses_existing_second_click_confirmation(self):
        with patch.object(raid,'member_characters',return_value=[(7,'Mine','Mage','Frost','DPS',None)]):
            view=absence_ui.RemovalView(42)
            picker=view.children[0]
            picker._values=['7']
            interaction=self.interaction()
            await picker.callback(interaction)
            self.assertIsInstance(interaction.response.send_message.call_args.kwargs['view'],raid.ConfirmCharacterDeleteView)
            self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
            other=self.interaction(member=99)
            await picker.callback(other)
            self.assertNotIn('view',other.response.send_message.call_args.kwargs)

    async def test_overflow_messages_are_edited_and_only_saved_surplus_is_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'raid.db')
            raid.create_schema(path)
            with raid.database(path) as con:
                con.execute('CREATE TABLE character_panel_parts(channel_id,position,message_id,PRIMARY KEY(channel_id,position))')
            channel=MagicMock(spec=discord.TextChannel)
            channel.id=300
            channel.send=AsyncMock(return_value=SimpleNamespace(id=501))
            message=SimpleNamespace(edit=AsyncMock(),delete=AsyncMock())
            channel.get_partial_message.return_value=message
            cog=hub.CharacterHub(SimpleNamespace())
            with patch.object(raid,'RAID_DB',path):
                await cog.sync_parts(channel,[discord.Embed(title='extra')])
                await cog.sync_parts(channel,[discord.Embed(title='updated')])
                channel.send.assert_awaited_once()
                message.edit.assert_awaited_once()
                self.assertFalse(channel.send.call_args.kwargs['allowed_mentions'].users)
                await cog.sync_parts(channel,[])
                message.delete.assert_awaited_once()
            with raid.database(path) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) FROM character_panel_parts').fetchone()[0],0)


class RosterNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_character_alert_is_private_pings_only_leadership_and_retries_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'raid.db')
            raid.create_schema(path)
            availability.save(path, 42, 'Until December', 'Family commitment')
            leader = MagicMock(spec=discord.Role)
            leader.id, leader.mentionable = LEADERSHIP_ROLE_ID, True
            everyone = MagicMock(spec=discord.Role)
            everyone.id = GUILD_ID
            guild = SimpleNamespace(id=GUILD_ID, default_role=everyone,
                                    me=SimpleNamespace(id=200),
                                    get_role=lambda ident: leader)
            channel = MagicMock(spec=discord.TextChannel)
            channel.guild = guild
            channel.overwrites = {
                everyone: discord.PermissionOverwrite(view_channel=False),
                leader: discord.PermissionOverwrite(view_channel=True),
            }

            def permissions(target):
                if target is everyone:
                    return discord.Permissions.none()
                return discord.Permissions(view_channel=True, send_messages=True,
                                           read_message_history=True, embed_links=True,
                                           mention_everyone=True)

            channel.permissions_for.side_effect = permissions
            sent = []

            async def history(**kwargs):
                for message in sent:
                    yield message

            async def send(**kwargs):
                message = SimpleNamespace(id=500, author=SimpleNamespace(id=200),
                                          embeds=[kwargs['embed']])
                sent.append(message)
                return message

            channel.history = history
            channel.send = AsyncMock(side_effect=send)
            bot = SimpleNamespace(user=SimpleNamespace(id=200),
                                  get_channel=lambda ident: channel)
            cog = notifications.RaidNotifications(bot)
            with patch.object(raid, 'RAID_DB', path):
                self.assertEqual(await cog.flush(), 1)
                self.assertEqual(await cog.flush(), 0)
                self.assertEqual(cog.read_pending(), [])
            call = channel.send.call_args.kwargs
            self.assertIn(str(LEADERSHIP_ROLE_ID), call['content'])
            self.assertFalse(call['allowed_mentions'].users)
            self.assertEqual(call['allowed_mentions'].roles, [leader])
            embed = call['embed']
            self.assertEqual(embed.title, '<Hyped> Roster update')
            self.assertIn('Until December', embed.description)
            self.assertIn('Family commitment', embed.description)
            self.assertIn('not been changed', embed.description)
