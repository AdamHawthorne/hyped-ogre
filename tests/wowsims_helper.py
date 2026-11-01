"""Test fixture helper: seed a valid WoWSims export before normal signup tests."""
import json

from cogs import raid_signup as raid


def example_export(name, klass="warrior"):
    return json.dumps({
        "name": name, "class": klass.lower(), "version": "v3.2.6",
        "flavor": "era", "gear": {"items": []},
        "talents": "000000-000000-000000"
    })


def seed_export(path, member_id, character_id):
    if raid.has_wowsims_export(member_id, character_id, path):
        return
    with raid.database(path) as con:
        row = con.execute(
            "SELECT name, class_name FROM characters "
            "WHERE character_id=? AND discord_id=?",
            (character_id, member_id)
        ).fetchone()
    if row is None:
        raise ValueError("Test fixture character not found")
    raid.save_wowsims_export(member_id, character_id,
                            example_export(row[0], row[1]), path)


def signup_with_export(raid_id, member_id, character_id, path=None, **kwargs):
    path = path or raid.RAID_DB
    seed_export(path, member_id, character_id)
    return raid.save_signup(raid_id, member_id, character_id, path, **kwargs)
