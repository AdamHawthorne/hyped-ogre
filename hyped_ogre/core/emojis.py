"""Server-specific Discord emoji IDs from the 29 October 2026 audit."""

EMOJI_IDS = {
    "Warrior": 1565482101585748109,
    "Paladin": 1565481831904837795,
    "Hunter": 1565481737050394707,
    "Rogue": 1565481939522297936,
    "Priest": 1565481897574801428,
    "Shaman": 1565481998603001897,
    "Mage": 1565481787231182969,
    "Warlock": 1565482059957276712,
    "Druid": 1565481696495931494,
    "Tank": 1565477012322132099,
    "Healer": 1565477112951996456,
    "Melee": 1565477048305197207,
    "Ranged": 1565477080228175973,
    "Caster": 1565477140429021285,
    "Dice": 1565477510651846767,
    "MoveTo": 1565477356326617183,
    "ogrecoin": 1565481626488807434,
    "ogredice": 1565477510651846767,
    "ogreivory": 1566177408556605460,
    "ogredie": 1566175682034270288,
    "ogrepoop": 1566195860746928290,
}

def markup(name):
    value = EMOJI_IDS.get(name)
    return f"<:{name}:{value}>" if value else ""

def option_emoji(name):
    value = EMOJI_IDS.get(name)
    if not value:
        return None
    import discord
    return discord.PartialEmoji(name=name, id=value)

def combat_role(role, klass="", spec=""):
    """Visual role classification; never changes persisted role."""
    if role not in ("DPS", "Dps"):
        return role
    klass = (klass or "").strip().title()
    spec = (spec or "").strip().title()
    if klass == "Hunter":
        return "Melee" if spec == "Survival" else "Ranged"
    if klass in {"Mage", "Warlock"} or (klass, spec) in {
        ("Priest", "Shadow"), ("Druid", "Balance"), ("Shaman", "Elemental")
    }:
        return "Caster"
    return "Melee"


def roll_dice_name(day):
    """Sunday double-coin dice; regular ivory dice on all other UK dates."""
    return "ogredie" if day.weekday() == 6 else "ogreivory"


def roll_dice_markup(day):
    return markup(roll_dice_name(day))


def roll_dice_emoji(day):
    return option_emoji(roll_dice_name(day))
