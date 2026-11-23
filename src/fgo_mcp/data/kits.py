import difflib

# short text of what each skill/np does at the owned level, for the llm. not a simulator
TARGETS = {
    "self": "self",
    "ptOne": "one ally",
    "ptAll": "party",
    "ptOther": "other allies",
    "ptFull": "party incl. backline",
    "ptOneOther": "other allies",
    "ptselectOneSub": "one backline ally",
    "enemy": "one enemy",
    "enemyAll": "all enemies",
    "enemyFull": "all enemies",
    "ptAnother": "another ally",
}
CARDS = {"buster": "Buster", "arts": "Arts", "quick": "Quick", "1": "Arts", "2": "Buster", "3": "Quick"}
# Value of these buffs is in 1/10 %, regainNp is in 1/100 %, stars and hp are plain numbers
PERMILLE_BUFFS = ("up", "down")
AOE_TARGETS = ("enemyAll", "enemyFull")

def percent(v: int) -> str:
    s = f"{v/10:.1f}".rstrip("0").rstrip(".")
    return s + "%"

def effect_text(f: dict, sv: dict) -> str | None:
    func_type = f["funcType"]
    if f["funcTargetTeam"] == "enemy":
        # only used when an enemy uses the skill
        return None
    target = TARGETS.get(f["funcTargetType"], f["funcTargetType"])
    chance = sv.get("Rate", 1000)
    chance_text = ""
    if 0 < chance < 1000:
        chance_text = f" ({percent(chance)} chance)"
    value = sv.get("Value", 0)

    if func_type == "gainNp":
        return f"NP +{value/100:g}% {target}{chance_text}"
    if func_type == "lossNp":
        who = "NP"
        if target.endswith("enemy") or target.endswith("enemies"):
            who = "enemy NP gauge"
        return f"{who} -{value/100:g}% {target}{chance_text}"
    if func_type == "gainStar":
        return f"+{value} stars"
    if func_type == "gainHp":
        return f"heal {value} HP {target}"
    if func_type == "delayNpturn":
        return f"charge -{value} {target}{chance_text}"
    if func_type == "shortenSkill":
        return f"skill cooldowns -{value} {target}"
    if func_type == "subState":
        what = f.get("funcPopupText") or "effects"
        if not what.lower().startswith("remove"):
            what = "remove " + what
        return f"{what} {target}"
    if func_type == "cardReset":
        return "shuffle cards"
    if func_type in ("addState", "addStateShort") and f["buffs"]:
        b = f["buffs"][0]
        name = b.get("name") or b["type"]
        val = sv.get("Value")
        if "unction" in b["type"]:
            # Value is an internal id here, not an amount
            val = None
        amount = ""
        if val and b["type"].startswith(PERMILLE_BUFFS):
            amount = " " + percent(val)
        elif val and b["type"] in ("upNpturnval", "regainNp"):
            amount = f" {val/100:g}%"
        elif val and b["type"] == "regainStar":
            amount = f" {val} stars"
        elif val:
            amount = f" {val}"
        duration = []
        if sv.get("Turn", -1) > 0:
            duration.append(f"{sv['Turn']}T")
        if sv.get("Count", -1) > 0:
            duration.append(f"{sv['Count']}x")
        duration_text = ""
        if duration:
            duration_text = " " + "/".join(duration)
        return f"{name}{amount} {target}{duration_text}{chance_text}"
    label = f.get("funcPopupText") or func_type
    return f"{label} {target}{chance_text}"

def name_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()

def pick_skill(svt: dict, num: int, name: str | None = None) -> dict | None:
    """skill in slot num. the name seen on screen picks the rank up version, otherwise highest priority"""
    variants = []
    for sk in svt["skills"]:
        if sk["num"] == num:
            variants.append(sk)
    if len(variants) == 0:
        return None
    if name:
        best = variants[0]
        for sk in variants:
            if name_ratio(sk["name"], name) > name_ratio(best["name"], name):
                best = sk
        if name_ratio(best["name"], name) >= 0.8:
            return best
    highest = variants[0]
    for sk in variants:
        if sk["priority"] > highest["priority"]:
            highest = sk
    return highest

def pick_np(svt: dict) -> dict | None:
    """current np, the highest priority one (after rank ups)"""
    best = None
    for np_ in svt["noblePhantasms"]:
        if best is None:
            best = np_
            continue
        if (np_.get("priority", 0), np_["id"]) > (best.get("priority", 0), best["id"]):
            best = np_
    return best

def level_index(level: int) -> int:
    return max(1, min(level, 10)) - 1

def sval_at(f: dict, i: int) -> dict:
    return f["svals"][min(i, len(f["svals"]) - 1)]

def skill_line(skill: dict, level: int) -> str:
    i = level_index(level)
    effects = []
    for f in skill["functions"]:
        if not f["svals"]:
            continue
        e = effect_text(f, sval_at(f, i))
        if e:
            effects.append(e)
    if len(effects) == 0:
        effects = [skill.get("detail", "")]
    return f"[CD {skill['coolDown'][i]}] " + "; ".join(effects)

def np_kind(svt: dict) -> str:
    np_ = pick_np(svt)
    if np_ is None:
        return "none"
    card = CARDS.get(str(np_["card"]), str(np_["card"]))
    damage_func = None
    for f in np_["functions"]:
        if f["funcType"].startswith("damageNp"):
            damage_func = f
            break
    if damage_func is None:
        return f"{card} support"
    if damage_func["funcTargetType"] in AOE_TARGETS:
        return f"{card} AoE"
    return f"{card} single-target"

def np_line(svt: dict) -> str:
    np_ = pick_np(svt)
    if np_ is None:
        return "NP: none"
    return f"NP {np_kind(svt)}: {np_['detail']}"

def kit_card(svt: dict, label: str, skill_levels: list[int] | None = None, skill_names: list[str] | None = None) -> str:
    levels = skill_levels or [10, 10, 10]
    names = skill_names or [None, None, None]
    lines = [f"{label}: {svt['name']} ({svt['className'].title()}, {svt['rarity']}*)", "  " + np_line(svt)]
    for num in (1, 2, 3):
        sk = pick_skill(svt, num, names[num - 1])
        if sk is not None:
            level = levels[num - 1]
            lines.append(f"  S{num} {sk['name']} Lv{level} " + skill_line(sk, level))
    if not skill_levels:
        lines.append("  (skill levels unknown; values shown at Lv10)")
    return "\n".join(lines)

# shorter tags for the per turn brief
ABBREV = [("C. Star Gather Up", "Star Gather+"), ("C. Star Drop Rate Up", "Star Drop+"),
          ("C. Star Drop Rate Down", "Star Drop-"), ("Critical Up", "Crit+"), ("Critical Hit Rate Down", "Crit Rate-"),
          (" Up ", "+"), (" Down ", "-"), ("one ally", "ally"), ("all enemies", "enemies"),
          (" 3T", ""), ("Strength", "Str"), ("NP Gain", "NPgain"), ("Triggers Each Turn", "each turn:"), ("ATK-&", "ATK- &")]

def skill_tags(skill: dict, level: int) -> str:
    i = level_index(level)
    tags = []
    for f in skill["functions"]:
        if not f["svals"]:
            continue
        sv = sval_at(f, i)
        if f["funcType"].startswith("gain") and sv.get("Value") == 0:
            continue
        e = effect_text(f, sv)
        if not e:
            continue
        for long_text, short_text in ABBREV:
            e = e.replace(long_text, short_text)
        e = e.replace("NP +", "NP+").replace("+ ", "+")
        tags.append(" ".join(e.split()))
    if len(tags) == 0:
        return skill.get("detail", "")
    return ", ".join(tags)

def np_role(svt: dict) -> str:
    kind = np_kind(svt)
    if kind == "none" or not kind.endswith("support"):
        return f"NP: {kind} damage"
    np_ = pick_np(svt)
    tags = skill_tags({"functions": np_["functions"], "detail": np_.get("detail", "")}, 1)
    first_tags = tags.split(", ")[:4]
    return f"NP: {kind.split()[0]} support, no damage ({', '.join(first_tags)})"