"""Conservative WoW: Forever raid class-coverage checker.

This checks *potential* buffs based on class/spec on the selected roster.
It cannot verify a player has cast the buff, learned an optional talent,
placed a totem, brought the correct pet or selected the correct group.

Sources: WoW: Forever Wowhead class changes (Oct 2026), especially
https://www.wowhead.com/forever/news/many-buffs-now-raid-wide-class-changes-in-latest-wow-forever-build-383456
Avoid using outdated Classic Era party-wide assumptions for Oct 2026 Forever.
"""
# All-class caster coverage; the conditional entries require the stated spec.
CLASS_COVERAGE = (
    ("Intellect", "Mage", None),
    ("Fortitude", "Priest", None),
    ("Mark of the Wild", "Druid", None),
    ("Paladin Blessings", "Paladin", None),
    ("Battle Shout", "Warrior", None),
    ("Shaman Totems", "Shaman", None),
)
SPEC_COVERAGE = (
    ("Trueshot Aura", "Hunter", ("Marksmanship",)),
    ("Leader of the Pack", "Druid", ("Feral", "Feral Combat")),
    ("Moonkin Aura", "Druid", ("Balance",)),
)
# Only assigned, selected, signed-up members contribute.
def _selected(body, flag, expected):
    selected = []
    for row in body:
        if len(row) < 14 or row[8] != "Signed" or row[9] != "Selected":
            continue
        if row[flag] != expected:
            continue
        selected.append((row[2].strip().casefold(), row[3].strip().casefold()))
    return selected


def missing_coverage(assigned):
    if not assigned:
        return "No team selected"
    result = []
    for label, klass, _ in CLASS_COVERAGE:
        if not any(k == klass.casefold() for k, _ in assigned):
            result.append(label)
    for label, klass, specs in SPEC_COVERAGE:
        if not any(k == klass.casefold() and spec in
                   tuple(s.casefold() for s in specs)
                   for k, spec in assigned):
            result.append(label)
    return ", ".join(result) if result else "Core class coverage present"


def instance_summaries(body, event):
    """Four G/H rows, independent of public Show visibility setting."""
    names = " ".join(str(event.get(key) or "").lower()
                     for key in ("instance_1", "instance_2", "instance_3"))
    output = [
        ("Barrows 🔸", _selected(body, 12, "🔸")) if "barrows" in names else None,
        ("Barrows 🔹", _selected(body, 12, "🔹")) if "barrows" in names else None,
        ("Hyjal", _selected(body, 11, "Yes")) if "hyjal" in names else None,
        ("Ony", _selected(body, 13, "Yes")) if "ony" in names else None,
    ]
    return [
        (f"{name} ({len(assigned)})", missing_coverage(assigned))
        if entry else ("", "")
        for entry in output
        for name, assigned in ([entry] if entry else [("", [])])
    ]
