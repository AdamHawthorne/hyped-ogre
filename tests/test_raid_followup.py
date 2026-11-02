"""Completion display and rolling guild attendance guards."""

import unittest
from unittest.mock import patch

from cogs import raid_acknowledgements as ack
from cogs import raid_schedule as schedule
from cogs import raid_sheets_v3 as sheets


class RaidFollowupTests(unittest.TestCase):
    def test_rank_window_uses_completed_guild_raids_and_deduplicates_alts(self):
        raids = {
            n: {
                "raid_id": n,
                "raid_at": f"2026-10-{n:02d}T20:00:00+00:00",
                "archived_at": "done" if n < 6 else None,
            }
            for n in range(1, 7)
        }

        def row(raid, member, status):
            return [str(raid), "date", str(member), "", "", "", "", "", "Bench", status]

        records = [row(n, 42, "Attended") for n in (1, 2, 3, 4)]
        records += [
            row(4, 42, "Attended"),
            row(5, 42, "Absent"),
            row(6, 42, "Attended"),
            row(5, 43, "Excused"),
        ]
        window, credit = sheets.rolling_attendance(raids, records)
        self.assertEqual([r["raid_id"] for r in window], [5, 4, 3, 2])
        self.assertEqual(len(credit["42"]), 3)
        self.assertNotIn("43", credit)
        raids[6]["archived_at"] = "done"
        _, credit = sheets.rolling_attendance(raids, records[:-2])
        self.assertEqual(len(credit["42"]), 2)

    def test_custom_acknowledgement_and_safe_outstanding_mentions(self):
        text = ack.render(1, [(42, "Player")], 1)
        self.assertIn("<:check:1566948806535159808>", text)
        self.assertIn("<@42>", text)
        self.assertNotIn("<@42>", ack.render(1, [], 1))

    def test_confirmed_totals_and_names_are_subtext_and_wcl_below_check(self):
        event = {
            "raid_id": 1,
            "raid_at": "2026-10-01T20:00:00+00:00",
            "instance_1": "Hyjal (20m)",
            "roster_published": 1,
            "check_message_url": "https://discord.com/channels/1/2/3",
            "wcl_url": "https://www.warcraftlogs.com/reports/abc",
            "softres_url": "https://softres.it/raid/abc",
        }
        text = schedule.raid_panel_text(
            event,
            [
                ("Tank", "Hyjal", "Warrior", "Tank", "Protection"),
                ("Mage", "Hyjal", "Mage", "DPS", "Frost"),
            ],
        )
        self.assertIn("**Hyjal (2)**", text)
        self.assertIn("-# **Tanks (1)**", text)
        self.assertIn("-# **DPS (1)**", text)
        self.assertTrue(
            all(
                line.startswith("-# ")
                for line in text.splitlines()
                if "<:Warrior:" in line or "<:Mage:" in line
            )
        )
        self.assertLess(text.index("**Check:**"), text.index("**Warcraft Logs:**"))
        self.assertLess(text.index("**Warcraft Logs:**"), text.index("[SoftRes"))

    def test_existing_sheet_format_uses_overflow_and_equal_management_widths(self):
        sid = 92811
        sheets._FORMATTED_SECTION_LAYOUTS.pop(sid, None)
        with patch.object(sheets.raid_sheets, "_json", return_value={}) as api:
            sheets._paint_sections(object(), sid, [[""] * len(sheets.COLUMNS)] * 8)
        requests = api.call_args.kwargs["json"]["requests"]
        widths = {
            r["updateDimensionProperties"]["range"]["startIndex"]: r[
                "updateDimensionProperties"
            ]["properties"]["pixelSize"]
            for r in requests
            if "updateDimensionProperties" in r
            and r["updateDimensionProperties"]["range"]["dimension"] == "COLUMNS"
        }
        self.assertEqual({widths[c] for c in range(2, 11)}, {110})
        overflow = [
            r["repeatCell"]
            for r in requests
            if "repeatCell" in r
            and r["repeatCell"]["cell"]["userEnteredFormat"].get("wrapStrategy")
            == "OVERFLOW_CELL"
        ]
        self.assertEqual(
            overflow[-1]["range"],
            {
                "sheetId": sid,
                "startRowIndex": 1,
                "endRowIndex": 5,
                "startColumnIndex": 7,
                "endColumnIndex": 8,
            },
        )
        sheets._FORMATTED_SECTION_LAYOUTS.pop(sid, None)

    def test_readable_dates_preserve_original_timestamp(self):
        raw = "2026-11-02T20:31:12+00:00"
        self.assertEqual(sheets._uk_timestamp(raw), "02 Nov 2026, 20:31")
        self.assertEqual(raw, "2026-11-02T20:31:12+00:00")
