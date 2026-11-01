"""Shared raid signup class/spec role and legacy Sheets references.

Keep original exported dict and tuple objects intact for existing Discord
character selectors, saved profiles and old sheet templates.
"""

SHEET_TABS = {
    "Characters": (
        "Character ID", "Discord ID", "Discord Name", "Character", "Class",
        "Spec", "Role", "Main / Alt", "Profile Updated", "SimC Updated",
    ),
    "Sign-ups": (
        "Raid ID", "Raid Date", "Discord ID", "Character ID", "Character",
        "Availability", "Sign-up Notes", "Sign-up Time", "Last Updated",
        "Signed off at",
    ),
    "Gear Checks": (
        "Character ID", "Discord ID", "Character", "Latest Gear Upload",
        "Export Format", "Item Summary", "Gear Review", "Leadership Notes",
        "Raw Export Reference",
    ),
    "Roster Builder": (
        "Raid ID", "Raid Date", "Discord ID", "Character ID", "Character",
        "Class", "Spec", "Assigned Role", "Selection", "Bench Notes",
        "Show on Discord", "Ony (40m)", "Hyjal (20m)", "Barrows (10m)",
        "Player Source",
    ),
    "Attendance": (
        "Raid ID", "Raid Date", "Discord ID", "Discord Name", "Character ID",
        "Character", "Roster Status", "Attendance", "Late Minutes",
        "AFK Minutes", "Excused", "Leadership Notes", "Updated At",
    ),
    "Raid History": (
        "Raid ID", "Raid Date", "Instance", "Start Time",
        "Roster Published", "Raid Completed", "Leadership Notes",
    ),
}

ATTENDANCE_OPTIONS = ("Pending", "Attended", "Late", "AFK", "No-show", "Excused")


# Forever launches with nine classic classes. Each spec has one or more raid roles.
# The guild shares the PvP ruleset; no realm or ruleset field is needed.
CLASS_SPECS = {
    "Druid": {"Balance": ("DPS",), "Feral": ("Tank", "DPS"),
              "Restoration": ("Healer",)},
    "Hunter": {"Beast Mastery": ("DPS",), "Marksmanship": ("DPS",),
               "Survival": ("DPS",)},
    "Mage": {"Arcane": ("DPS",), "Fire": ("DPS",), "Frost": ("DPS",)},
    "Paladin": {"Holy": ("Healer",), "Protection": ("Tank",),
                "Retribution": ("DPS",)},
    "Priest": {"Discipline": ("Healer",), "Holy": ("Healer",),
               "Shadow": ("DPS",)},
    "Rogue": {"Assassination": ("DPS",), "Combat": ("DPS",),
              "Subtlety": ("DPS",)},
    "Shaman": {"Elemental": ("DPS",), "Enhancement": ("DPS",),
               "Restoration": ("Healer",)},
    "Warlock": {"Affliction": ("DPS",), "Demonology": ("DPS",),
                "Destruction": ("DPS",)},
    "Warrior": {"Arms": ("DPS",), "Fury": ("DPS",),
                "Protection": ("Tank",)},
}


