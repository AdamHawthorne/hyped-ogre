"""Exact Hyped destinations from the 2026-11-03 structural server audit.

Never discover destinations by name: another category can contain a channel
with the same name. This register has no guild column, so it must only be
used for the single guild that owns it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Classroom:
    key: str
    channel_id: int
    class_name: str = ""
    role: str = ""


GUILD_ID = 451518969417957376
CATEGORY_ID = 1566963940083376208
LEADERSHIP_ROLE_ID = 1225004372396867685
BOT_ROLE_ID = 1563306351294288047
CHANNELS = (
    Classroom("tanks", 1566964199391760434, role="Tank"),
    Classroom("healers", 1566964285630844999, role="Healer"),
    Classroom("dps", 1566979883068948551, role="DPS"),
    Classroom("druids", 1566964559770689556, class_name="Druid"),
    Classroom("hunters", 1566964591697600652, class_name="Hunter"),
    Classroom("mages", 1566964617207353364, class_name="Mage"),
    Classroom("paladins", 1566964638732779590, class_name="Paladin"),
    Classroom("priests", 1566964680629690460, class_name="Priest"),
    Classroom("rogues", 1566964823546142812, class_name="Rogue"),
    Classroom("shamans", 1566964874959913122, class_name="Shaman"),
    Classroom("warlocks", 1566964405953101914, class_name="Warlock"),
    Classroom("warriors", 1566964524542591066, class_name="Warrior"),
)
MEMBER_PERMISSIONS = ("view_channel", "send_messages", "read_message_history")
