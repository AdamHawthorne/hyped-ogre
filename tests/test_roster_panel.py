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
from hyped_ogre.features.characters import roster, availability, hub, absence_ui, board_overrides, board_admin
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

    def test_board_display_name_changes_only_public_presentation(self):
        with raid.database(self.path) as con:
            self.assertEqual(board_overrides.rename(con, self.cid, "Snore Boss"),
                             ("Snore", "Snore Boss"))
        registered = raid.member_characters(42, self.path)[0]
        self.assertEqual(registered[1], "Snore")
        record = roster.rows(self.path)[0]
        self.assertEqual(record["name"], "Snore")
        self.assertEqual(record["board_name"], "Snore Boss")
        self.assertIn("Snore Boss", str(roster.render([record])[0].to_dict()))

        with raid.database(self.path) as con:
            self.assertEqual(board_overrides.reset_name(con, self.cid), "Snore")
        self.assertIn("Snore", str(roster.render(roster.rows(self.path))[0].to_dict()))
        self.assertNotIn("Snore Boss", str(roster.render(roster.rows(self.path))[0].to_dict()))

    def test_board_hide_preserves_registration_and_can_be_reversed(self):
        with raid.database(self.path) as con:
            self.assertEqual(board_overrides.set_hidden(con, self.cid, True), "Snore")
            matches = board_overrides.choices(con, "Snore")
            self.assertEqual(matches[0][0], self.cid)
            self.assertEqual(matches[0][-1], 1)
            self.assertEqual(len(board_overrides.changed(con)), 1)
        self.assertEqual(raid.member_characters(42, self.path)[0][1], "Snore")
        self.assertEqual(roster.render(roster.rows(self.path))[0].author.name, "Characters (0)")
        with raid.database(self.path) as con:
            self.assertEqual(board_overrides.set_hidden(con, self.cid, False), "Snore")
            self.assertEqual(board_overrides.changed(con), [])
        self.assertEqual(roster.render(roster.rows(self.path))[0].author.name, "Characters (1)")

    def test_board_overrides_reject_nonmain_and_unregistered_characters(self):
        with raid.database(self.path) as con:
            with self.assertRaises(ValueError):
                board_overrides.rename(con, 999999, "Other")
            with self.assertRaises(ValueError):
                board_overrides.set_hidden(con, 999999, True)
            con.execute("UPDATE characters SET is_main=0 WHERE character_id=?", (self.cid,))
            with self.assertRaises(ValueError):
                board_overrides.rename(con, self.cid, "Other")
            with self.assertRaises(ValueError):
                board_overrides.set_hidden(con, self.cid, True)

    def test_roster_admin_permissions_stay_leadership_only(self):
        from hyped_ogre.features.classrooms.config import LEADERSHIP_ROLE_ID
        guild = SimpleNamespace(owner_id=998)
        guest = SimpleNamespace(guild_id=GUILD_ID, guild=guild,
                                user=SimpleNamespace(id=76, roles=[]))
        leader = SimpleNamespace(guild_id=GUILD_ID, guild=guild,
                                 user=SimpleNamespace(id=77, roles=[
                                     SimpleNamespace(id=LEADERSHIP_ROLE_ID)]))
        self.assertFalse(board_admin.allowed(guest))
        self.assertTrue(board_admin.allowed(leader))
        self.assertFalse(board_admin.allowed(SimpleNamespace(
            guild_id=999, guild=guild, user=leader.user)))

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

    def test_wowsims_only_fills_missing_ilvl_when_complete(self):
        self.assertEqual(roster.rows(self.path)[0]['ilvl'], 'N/A')
        export = {'name':'Snore','class':'warrior','spec':'protection','version':'v3.2.6',
                  'level':60,'gear':{'items':[{'id':5},{'id':6}]}}
        raid.save_wowsims_export(42,self.cid,json.dumps(export),self.path)
        with patch.object(roster.items,'cached_catalog',return_value={'5':{'level':50},'6':{'level':60}}):
            result = roster.rows(self.path)[0]
            self.assertEqual(result['ilvl'], '55')
            self.assertEqual(result['ilvl_source'], 'WoWSims')
            self.assertIsNone(result['level'])
        with patch.object(roster.items,'cached_catalog',return_value={'5':{'level':50}}):
            result = roster.rows(self.path)[0]
            self.assertEqual(result['ilvl'], 'N/A')
            self.assertIsNone(result['ilvl_source'])

    def test_public_board_omits_all_unknown_labels_without_breaking_columns(self):
        record = roster.rows(self.path)[0]
        embed = roster.render([record])[0]
        tank_name, tank_level, tank_prof = embed.fields[:3]
        self.assertEqual(len(tank_name.value.splitlines()), 1)
        self.assertEqual(tank_level.value, roster.EMPTY_ROW_CELL)
        self.assertEqual(tank_prof.value, roster.EMPTY_ROW_CELL)
        self.assertNotIn("(N/A)", str(embed.to_dict()))
        self.assertNotIn("N/A", str(embed.to_dict()))

    def test_wowsims_professions_do_not_fill_public_stats(self):
        self.assertEqual(roster.rows(self.path)[0]['professions'], 'N/A')
        original = {
            'name': 'Snore', 'class': 'warrior', 'spec': 'protection',
            'version': 'v3.2.6', 'level': 20, 'gear': {'items': []},
            'professions': [
                {'name': 'Mining', 'level': 65},
                {'name': 'Blacksmithing', 'level': 120},
            ],
        }
        raid.save_wowsims_export(42, self.cid, json.dumps(original), self.path)
        self.assertEqual(roster.rows(self.path)[0]['professions'], 'N/A')
        self.assertNotIn('N/A', roster.render(roster.rows(self.path))[0].fields[2].value)

        updated = {**original, 'professions': [
            {'name': 'Herbalism', 'level': 150},
            {'name': 'Alchemy', 'level': 99},
        ]}
        raid.save_wowsims_export(42, self.cid, json.dumps(updated), self.path)
        self.assertEqual(roster.rows(self.path)[0]['professions'], 'N/A')

        # Professions are an optional WoWSims field, not a live in-game feed.
        updated['professions'] = []
        raid.save_wowsims_export(42, self.cid, json.dumps(updated), self.path)
        self.assertEqual(roster.rows(self.path)[0]['professions'], 'N/A')

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
                    role=role, is_main=is_main, attendance='75%', ilvl='67.5', level=None,
                    professions='N/A')

    def test_grouping_columns_and_every_main_in_one_guild_sized_embed(self):
        specs = (('Tank', 'Warrior', 'Protection'),
                 ('Healer', 'Priest', 'Holy'),
                 ('DPS', 'Rogue', 'Combat'),
                 ('DPS', 'Mage', 'Frost'))
        data = [self.row(n, *specs[n % 4]) for n in range(40)]
        embeds = roster.render(data)
        self.assertEqual(len(embeds), 1)
        self.assertIsNone(embeds[0].title)
        self.assertIsNone(embeds[0].description)
        self.assertEqual(embeds[0].author.name, 'Characters (40)')
        self.assertEqual(embeds[0].author.icon_url, roster.ROSTER_IMAGE)
        self.assertIsNone(embeds[0].thumbnail.url)
        self.assertIsNone(embeds[0].footer.text)
        self.assertIsNone(embeds[0].image.url)
        self.assertNotIn('image', embeds[0].to_dict())
        self.assertEqual([f.name for f in embeds[0].fields[::3]],
                         ['Tanks (10)', 'Healers (10)', 'Melee (10)', 'Ranged (10)'])
        content = str(embeds[0].to_dict())
        self.assertNotIn('WCL', content)
        for row in data:
            self.assertEqual(content.count(row['name']), 1)
            self.assertIn(
                f"[{row['name']}](https://discord.com/users/{row['discord_id']})",
                content,
            )
            self.assertNotIn(f"<@{row['discord_id']}>", content)
        for i in range(0, len(embeds[0].fields), 3):
            fields = embeds[0].fields[i:i+3]
            self.assertEqual([f.name for f in fields[1:]], ['Level (iLvl)', 'Professions'])
            self.assertEqual(len(set(len(f.value.splitlines()) for f in fields)), 1)
            self.assertTrue(all(v == '(67.5)' for v in fields[1].value.splitlines()))
            self.assertTrue(all(v == roster.EMPTY_ROW_CELL for v in fields[2].value.splitlines()))
        self.assertNotIn('Character', [f.name for f in embeds[0].fields])

    def test_gold_accent_and_clean_roster_without_footer_or_banner(self):
        embed = roster.render([self.row(1, 'Tank', 'Warrior', 'Protection')])[0]
        self.assertEqual(embed.colour.value, 0xA87535)
        self.assertEqual(len(embed.fields), 12)
        self.assertIsNone(embed.footer.text)
        self.assertIsNone(embed.image.url)
        self.assertNotIn('footer', embed.to_dict())
        self.assertNotIn('image', embed.to_dict())
        self.assertNotIn('Hyped Armory', str(embed.to_dict()))
        self.assertEqual(len(embed.fields) % 3, 0)

    def test_long_discord_usernames_cannot_wrap_independent_roster_rows(self):
        # Character name doubles as a clickable Discord profile link.
        # No separate @username can wrap and push the next line down while
        # the Level and Professions columns remain in place.
        data = [
            dict(self.row(1, 'Healer', 'Priest', 'Holy'),
                 name='Doctor Suncles', level=60),
            dict(self.row(2, 'Healer', 'Druid', 'Restoration'),
                 name='Howaboutthat parkour', level=60),
            dict(self.row(3, 'Healer', 'Priest', 'Holy'),
                 name='Based Sigma', level=47),
        ]
        fields = roster.render(data)[0].fields[3:6]
        self.assertEqual(fields[0].name, 'Healers (3)')
        self.assertEqual([len(field.value.splitlines()) for field in fields],
                         [3, 3, 3])
        # Druids are grouped before priests; priest names then sort
        # alphabetically because all three have the same average iLvl.
        expected = [data[1], data[2], data[0]]
        for index, record in enumerate(expected):
            line = fields[0].value.splitlines()[index]
            self.assertIn(record['name'], line)
            self.assertIn(
                f"https://discord.com/users/{record['discord_id']}", line
            )
            self.assertNotIn(' / <@', line)
        self.assertEqual(fields[1].value.splitlines(),
                         ['60 (67.5)', '47 (67.5)', '60 (67.5)'])

    def test_professions_keep_one_line_per_character_and_fit_discord_fields(self):
        players = [self.row(n, 'Tank', 'Warrior', 'Protection') for n in range(1, 11)]
        for row in players:
            row['professions'] = 'Min: 65 / BS: 120'
            row['ilvl'] = '54.5'
            row['level'] = 20
        fields = roster.render(players)[0].fields
        self.assertEqual([f.name for f in fields[:3]], ['Tanks (10)', 'Level (iLvl)', 'Professions'])
        self.assertEqual(len(fields[0].value.splitlines()), 10)
        self.assertEqual(fields[1].value.splitlines(), ['20 (54.5)'] * 10)
        self.assertEqual(fields[2].value.splitlines(), ['Min: 65 / BS: 120'] * 10)
        self.assertTrue(all(len(field.value) <= 1024 for field in fields))

    def test_icon_in_author_does_not_change_three_column_roster_layout(self):
        players = [self.row(n) for n in range(30)]
        embed = roster.render(players)[0]
        self.assertEqual(embed.to_dict()['author']['icon_url'], roster.ROSTER_IMAGE)
        self.assertNotIn('thumbnail', embed.to_dict())
        self.assertNotIn('description', embed.to_dict())
        self.assertNotIn('title', embed.to_dict())
        self.assertEqual(len(embed.fields) % 3, 0)
        self.assertEqual([f.name for f in embed.fields[:3]],
                         ['Tanks (0)', 'Level (iLvl)', 'Professions'])
        self.assertLessEqual(len(embed), 6000)

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
        self.assertEqual(embed.author.name, 'Characters (1)')
        self.assertEqual([f.name for f in embed.fields[::3]],
                         ['Tanks (1)', 'Healers (0)', 'Melee (0)', 'Ranged (0)'])

    def test_big_snore_is_first_tank_regardless_of_class_or_ilvl(self):
        druid = self.row(1, 'Tank', 'Druid', 'Feral')
        druid.update(name='Druid High', ilvl='99', level=60)
        warrior = self.row(2, 'Tank', 'Warrior', 'Protection')
        warrior.update(name='Warrior High', ilvl='90', level=60)
        snore = self.row(3, 'Tank', 'Warrior', 'Protection')
        snore.update(name='Big Snore', ilvl='40', level=60)
        paladin = self.row(4, 'Tank', 'Paladin', 'Protection')
        paladin.update(name='Paladin Middle', ilvl='60', level=60)
        # Sorting should not move Big Snore within its role even when
        # his iLvl is lowest and another class sorts alphabetically first.
        tanks = roster.render([druid, warrior, snore, paladin])[0].fields[:3]
        lines = tanks[0].value.splitlines()
        for index, name in enumerate(
            ('Big Snore', 'Druid High', 'Paladin Middle', 'Warrior High')
        ):
            self.assertIn(f'[{name}](', lines[index])
        self.assertEqual(
            tanks[1].value.splitlines(),
            ['60 (40)', '60 (99)', '60 (60)', '60 (90)'],
        )
        # The name match is case-insensitive, but only its Tank entry
        # is pinned. Other classes still use their standard group sort.
        snore['name'] = 'BIG SNORE'
        self.assertIn('[BIG SNORE](', roster.render(
            [druid, warrior, snore, paladin]
        )[0].fields[0].value.splitlines()[0])

    def test_v1_groups_classes_then_sorts_descending_ilvl_with_unknowns_last(self):
        # Deliberately scramble level, attendance, class and item level.
        # Within Healers, the visible sequence must be Druid, Paladin,
        # Priest; each class is sorted by iLvl, not character level.
        players = [self.row(n, 'Healer', 'Priest', 'Holy') for n in range(1, 10)]
        updates = [
            ('PriestLow', 'Priest', '59', 60, '90%'),
            ('DruidLow', 'Druid', 40, 60, '100%'),
            ('PriestHigh', 'Priest', 70.5, 50, '10%'),
            ('PaladinMissing', 'Paladin', 'N/A', 60, '100%'),
            ('DruidHigh', 'Druid', '75', 40, '0%'),
            ('PriestMissing', 'Priest', None, 60, '100%'),
            ('PaladinHigh', 'Paladin', 80, 30, '0%'),
            ('PriestTieZ', 'Priest', 62.0, 60, '50%'),
            ('PriestTieA', 'Priest', 62, 45, '95%'),
        ]
        for row, (name, klass, ilvl, level, attendance) in zip(players, updates):
            row.update(name=name, class_name=klass, ilvl=ilvl,
                       level=level, attendance=attendance,
                       professions=f'Min: {row["character_id"]}')
        embed = roster.render(players)[0]
        fields = embed.fields[3:6]
        self.assertEqual(fields[0].name, 'Healers (9)')
        expected = [
            'DruidHigh', 'DruidLow', 'PaladinHigh', 'PaladinMissing',
            'PriestHigh', 'PriestTieA', 'PriestTieZ', 'PriestLow',
            'PriestMissing',
        ]
        input_by_name = {r['name']: r for r in players}
        character_lines = fields[0].value.splitlines()
        for index, name in enumerate(expected):
            self.assertIn(f'[{name}](https://discord.com/users/',
                          character_lines[index])
        self.assertEqual(fields[1].value.splitlines(), [
            '40 (75)', '60 (40)', '30 (80)', '60',
            '50 (70.5)', '45 (62)', '60 (62)', '60 (59)', '60',
        ])
        self.assertEqual(fields[2].value.splitlines(), [
            f'Min: {input_by_name[name]["character_id"]}' for name in expected
        ])
        self.assertEqual(len(embed.fields), 12)

    def test_ranged_more_than_ten_are_one_continuous_section(self):
        # The old artificial ten-row limit produced "Ranged · continued".
        # More than ten members fit if repeated Discord profile URLs are
        # omitted from the long column, without losing any class icons.
        players = [
            self.row(n, "DPS", "Mage", "Frost") for n in range(1, 19)
        ]
        for row in players:
            row["name"] = f"MageName{row['character_id']:02}"
            row["professions"] = "Min: 65 / BS: 120"
            row["level"] = 60
        embeds = roster.render(players)
        self.assertEqual(len(embeds), 1)
        fields = embeds[0].fields
        self.assertEqual([field.name for field in fields[::3]], [
            "Tanks (0)", "Healers (0)", "Melee (0)", "Ranged (18)"
        ])
        ranged = fields[-3:]
        self.assertEqual(
            [len(field.value.splitlines()) for field in ranged],
            [18, 18, 18],
        )
        self.assertTrue(all(len(field.value) <= 1024 for field in ranged))
        for row in players:
            self.assertEqual(ranged[0].value.count(row["name"]), 1)
        # Small role groups retain clickable Discord profile links.
        shorter = roster.render(players[:2])[0].fields[-3].value
        self.assertIn("https://discord.com/users/", shorter)

    def test_very_large_role_only_splits_at_discord_field_limit(self):
        players = [self.row(n, "DPS", "Mage", "Frost") for n in range(1, 66)]
        embeds = roster.render(players)
        field_names = [
            field.name for embed in embeds for field in embed.fields
        ]
        self.assertIn("Ranged (65)", field_names)
        self.assertTrue(any("continued" in name.lower() for name in field_names))
        for embed in embeds:
            self.assertLessEqual(len(embed), 6000)
            for field in embed.fields:
                self.assertLessEqual(len(field.value), 1024)
        names = "\n".join(
            field.value for embed in embeds for field in embed.fields
            if field.name.startswith("Ranged")
        )
        for row in players:
            self.assertEqual(names.count(row["name"]), 1)

    def test_level_and_average_ilvl_share_one_column_without_invented_values(self):
        main = self.row(1, 'Tank', 'Warrior', 'Protection')
        fields = roster.render([main])[0].fields
        self.assertEqual(fields[1].value, '(67.5)')
        main['level'] = 60
        main['ilvl'] = '43'
        self.assertEqual(roster.render([main])[0].fields[1].value, '60 (43)')
        main['ilvl'] = 55.37
        self.assertEqual(roster.render([main])[0].fields[1].value, '60 (55.4)')
        for invalid in (999, None, True, 0, -1, '', 'N/A', 'nan', 'infinity'):
            main['ilvl'] = invalid
            self.assertEqual(roster.render([main])[0].fields[1].value, '60 (N/A)')
        main['ilvl'] = 67
        for invalid in (999, None, True, 0, -1, '', '60'):
            main['level'] = invalid
            self.assertEqual(roster.render([main])[0].fields[1].value, 'N/A (67)')

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
            self.assertEqual([f.name for f in fields[1:]], ['Level (iLvl)', 'Professions'])
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
        self.assertEqual(embed.author.name, 'Characters (0)')
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
                          ('WoWSims',discord.ButtonStyle.secondary),('Remove / Absent',discord.ButtonStyle.danger)])
        interaction=self.interaction()
        with patch.object(raid, 'member_characters', return_value=[]):
            await view.children[0].callback(interaction)
        self.assertIsInstance(interaction.response.send_message.call_args.kwargs['view'],hub.RosterAddView)
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
