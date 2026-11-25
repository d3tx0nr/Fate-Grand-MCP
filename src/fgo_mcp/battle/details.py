import difflib
import re
from dataclasses import dataclass

from fgo_mcp.battle.party import cancel_click
from fgo_mcp.data import atlas
from fgo_mcp.data import kits
from fgo_mcp.data.store import Roster
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations
from fgo_mcp.readers import ocr

# things read by opening a window and closing it again: servant face (to know whose cards are whose,
# like FGA ServantTracker), the status tab (buffs) and the mystic code skills

def face_region(L: Locations) -> Region:
    return Region(L.cx(-685), 410, 110, 60)

def details_tab_click(L: Locations) -> tuple[int, int]:
    return L.cx(-660), 110

def status_tab_click(L: Locations) -> tuple[int, int]:
    return L.cx(-110), 110

def open_details(battle, slot: int) -> None:
    battle.require_idle()
    L = battle.L
    battle.click((L.skill(slot, 2)[0], 810), wait=1.2)

def close_details(battle) -> None:
    from fgo_mcp.battle.actions import BattleError

    battle.click(battle.L.extra_info_close_click, wait=0.6)
    if not battle.wait_until(battle.is_idle, 3):
        raise BattleError("servant details window did not close")

def capture_face(battle, slot: int):
    open_details(battle, slot)
    battle.click(details_tab_click(battle.L), wait=0.5)
    face = battle.snap().crop(face_region(battle.L)).copy()
    close_details(battle)
    return face

STATUS_PANEL = Region(500, 340, 1660, 1020)
TURNS_RE = re.compile(r"\s*\d+\s*(Turn\(?s?\)?|Left)\s*", re.IGNORECASE)

@dataclass
class StatusEntry:
    name: str
    turns: int | None = None
    times: int | None = None

def parse_status(items: list[tuple[float, float, str]], known: set[str]) -> list[StatusEntry]:
    """items are ocr boxes (x, y, text), buff names get matched against the known buff names"""
    entries = []
    turn_texts = []
    for _, y, t in sorted(items, key=lambda item: item[1]):
        if TURNS_RE.fullmatch(t):
            turn_texts.append((y, t))
            continue
        name = t.strip()
        if difflib.get_close_matches(name.lower(), known, n=1, cutoff=0.85):
            entries.append((y, StatusEntry(name)))
    # "3 Turn(s)" or "1 Left" belongs to the entry closest to it
    for y, t in turn_texts:
        closest = None
        for entry in entries:
            if closest is None or abs(y - entry[0]) < abs(y - closest[0]):
                closest = entry
        if closest is None:
            continue
        n = int(re.search(r"\d+", t).group())
        if "left" in t.lower():
            closest[1].times = n
        else:
            closest[1].turns = n
    return [e for _, e in entries]

def read_status(battle, slot: int) -> list[StatusEntry]:
    open_details(battle, slot)
    battle.click(status_tab_click(battle.L), wait=0.6)
    img = battle.g.game_image(STATUS_PANEL, scale="native")
    close_details(battle)
    return parse_status(ocr.boxes(img), set(atlas.buff_index()))

MASTER_DIALOG = Region(300, 420, 1960, 300)
MASTER_TITLE_RE = re.compile(r"(.+?)\s*L[vy][.,]?\s*(\d+)", re.IGNORECASE)

@dataclass
class MasterSkill:
    num: int
    name: str = ""
    desc: str = ""
    cooldown: int | None = None
    needs_target: bool = False
    note: str = ""
    level: int | None = None
    functions: list | None = None

def fill_from_atlas(skills: list[MasterSkill]) -> None:
    """the names tell which mystic code it is. effects, cooldown and target come from atlas at the level the
    dialog title showed"""
    try:
        mc = atlas.find_mystic_code([m.name for m in skills])
    except Exception:
        mc = None
    if not mc:
        for m in skills:
            if m.name and not m.note:
                m.note = "mystic code not identified from the skill names"
        return
    levels = [m.level for m in skills if m.level]
    if levels:
        level = max(set(levels), key=levels.count)
    else:
        level = 1
    for i in range(len(mc["skills"])):
        sk = mc["skills"][i]
        m = next((x for x in skills if x.num == i + 1), None)
        if m is None or m.note.startswith("no dialog"):
            # read when the battle starts, so no dialog means the skill isnt unlocked at this level
            continue
        m.name = sk["name"]
        m.level = level
        m.desc = kits.skill_tags(sk, level)
        m.cooldown = sk["coolDown"][min(level, len(sk["coolDown"])) - 1]
        m.functions = [atlas.slim_func(f) for f in sk["functions"]]
        m.needs_target = False
        for f in sk["functions"]:
            if f["funcTargetType"] == "ptOne" and f["funcTargetTeam"] != "enemy":
                m.needs_target = True
        m.note = f"{mc['name']} Lv{level}"

def dialog_lines(boxes) -> list[str]:
    # group ocr boxes into lines by y, then each line left to right
    lines = []
    for x, y, t in sorted(boxes, key=lambda b: b[1]):
        if lines and abs(y - lines[-1][0]) < 18:
            lines[-1][1].append((x, t))
        else:
            lines.append((y, [(x, t)]))
    texts = []
    for _, parts in lines:
        parts.sort(key=lambda p: p[0])
        texts.append(" ".join(t for _, t in parts))
    return texts

def read_master_skills(battle, roster: Roster) -> list[MasterSkill]:
    """the menu stays open after cancel, so open it once, read all 3 and close it"""
    from fgo_mcp.battle.actions import BattleError

    L = battle.L
    mx = battle.master_x()
    battle.require_idle()
    battle.click(L.master_open_click(mx), wait=1.5)

    def dialog_shown(s):
        return s.exists("skill_use", L.skill_use_region)

    def close_menu() -> None:
        # leave the battle screen clean even when something went wrong, so nothing else reads into the menu
        battle.click(cancel_click(L), wait=0.6)
        battle.click(L.master_open_click(mx), wait=1.0)
        if not battle.wait_until(battle.is_idle, 3):
            battle.click(L.extra_info_close_click, wait=0.8)

    skills = []
    for n in (1, 2, 3):
        ms = MasterSkill(n)
        battle.click(L.master_skill(mx, n), wait=0)
        if not battle.wait_until(dialog_shown, 1.2):
            ms.note = "no dialog (on cooldown or unavailable)"
            skills.append(ms)
            continue
        # only the title line "First Aid Lv.1": the name finds the mystic code, the rest comes from atlas
        for t in dialog_lines(ocr.boxes(battle.g.game_image(MASTER_DIALOG, scale="native"))):
            if re.search(r"confirm skill|^cancel", t, re.IGNORECASE):
                continue
            m = MASTER_TITLE_RE.search(t)
            if m:
                ms.name = m.group(1).strip()
                ms.level = int(m.group(2)) if 1 <= int(m.group(2)) <= 10 else None
            else:
                ms.name = t.strip()
            break
        battle.click(cancel_click(L), wait=0.8)
        if battle.snap().exists("skill_use", L.skill_use_region):
            close_menu()
            raise BattleError("master skill dialog did not close after Cancel")
        skills.append(ms)
    fill_from_atlas(skills)
    battle.click(L.master_open_click(mx), wait=1.0)
    if not battle.wait_until(battle.is_idle, 3):
        battle.click(L.extra_info_close_click, wait=0.8)
        if not battle.wait_until(battle.is_idle, 3):
            raise BattleError("master menu did not close")
    return skills