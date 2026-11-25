import difflib
import re
from dataclasses import dataclass, field

from fgo_mcp.data import atlas
from fgo_mcp.data.store import Roster
from fgo_mcp.device.game import Game
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations
from fgo_mcp.fga.vision import Snapshot
from fgo_mcp.readers import ocr

CLASS_WORDS = {
    "SABER": "saber", "ARCHER": "archer", "LANCER": "lancer", "RIDER": "rider", "CASTER": "caster",
    "ASSASSIN": "assassin", "BERSERKER": "berserker", "SHIELDER": "shielder", "RULER": "ruler",
    "AVENGER": "avenger", "ALTEREGO": "alterEgo", "MOONCANCER": "moonCancer", "FOREIGNER": "foreigner",
    "PRETENDER": "pretender", "BEAST": "beast",
}
NAME_RE = re.compile(r"^(.*?)[LE][Vv][.,]?\s*(\d{1,3})\s*(.+)$")
NAME_RE_SWAPPED = re.compile(r"([A-Za-z]+)\s+(.+?)\s*Lv\.?\s*(\d+)")
# letters ocr confuses in the gold class word: "CA3TLR" is CASTER, "AJJAJJIN" is ASSASSIN
CLASS_OCR_FIX = str.maketrans("3$5J01", "SSSSOI")
CLASS_CUTOFF = 0.6
# ocr reads the level wrong sometimes: "LV.T20" and "LvX20" are Lv.120, "Lv.3O" is Lv.30, "Ev.120" is Lv.120
LEVEL_RE = re.compile(r"([LE][Vv][.,]?\s*)([0-9TIlOoX]{1,3})(?=\D|$)")
LEVEL_DIGIT_FIX = str.maketrans("TIlOoX", "111001")

def fix_level_digits(text: str) -> str:
    def fix(m):
        return m.group(1) + m.group(2).translate(LEVEL_DIGIT_FIX)
    return LEVEL_RE.sub(fix, text)

@dataclass
class FieldServant:
    slot: int
    raw: str
    cls: str | None = None
    level: int | None = None
    name: str | None = None
    atlas_id: int | None = None
    candidates: list[int] = field(default_factory=list)
    support: bool = False

    def label(self) -> str:
        who = self.name or "?"
        if self.support:
            return f"S{self.slot} {who} (support)"
        return f"S{self.slot} {who}"

def name_region(L: Locations, slot: int) -> Region:
    # "CLASS Lv.N Name" under the NP bar. placed from the skill row like the NP and HP regions: on wide
    # phones the whole strip sits ~40px higher than on 16:9 (a fixed y read only the bottom of the letters)
    x0 = max(L.skill(slot, 1)[0] - 150, 0)
    x1 = L.skill(slot, 3)[0] + 160
    return Region(x0, L.skill(slot, 1)[1] + 212, x1 - x0, 70)

def class_word(token: str) -> str | None:
    # close match so ocr typos like "BERSEERKER" still work
    matches = difflib.get_close_matches(token.upper(), CLASS_WORDS.keys(), n=1, cutoff=0.75)
    if matches:
        return CLASS_WORDS[matches[0]]
    return None

def class_in(head: str) -> str | None:
    """the class word in the text before "Lv": any of its words or all of them joined ("CAS,ILR" is CASTER),
    with the letters ocr confuses fixed. stray letters next to it ("LANCER R") are ignored"""
    tokens = [t for t in re.split(r"[^A-Za-z0-9$]+", head) if t]
    tries = tokens + ["".join(tokens)]
    best = None
    best_score = 0.0
    for t in tries:
        t = t.upper().translate(CLASS_OCR_FIX)
        if len(t) < 4:
            continue
        for word, cls in CLASS_WORDS.items():
            score = difflib.SequenceMatcher(None, t, word).ratio()
            if score > best_score:
                best = cls
                best_score = score
    if best_score >= CLASS_CUTOFF:
        return best
    return None

def parse_name_line(text: str) -> tuple[str | None, int | None, str | None]:
    """'CLASS Lv.N Name', also 'CLASS Name Lv.N' when ocr returns it out of order"""
    text = fix_level_digits(text.replace("Lv,", "Lv."))
    m = NAME_RE.search(text)
    if m:
        cls = class_in(m.group(1))
        if cls:
            return cls, int(m.group(2)), m.group(3).strip()
    m = NAME_RE_SWAPPED.search(text)
    if m:
        cls = class_word(m.group(1))
        if cls:
            return cls, int(m.group(3)), m.group(2).strip()
    return None, None, None

def scan(game: Game, prefer: set[int] | None = None) -> list[FieldServant]:
    L = Locations(game.ensure_area())
    party = []
    for slot in (1, 2, 3):
        raw = ocr.line(game.game_image(name_region(L, slot), scale="native"))
        fs = FieldServant(slot, raw)
        fs.cls, fs.level, fs.name = parse_name_line(raw)
        if fs.name:
            candidates = atlas.match(fs.name, fs.cls, prefer)
            fs.candidates = [c["id"] for c in candidates]
            if len(candidates) == 1:
                fs.atlas_id = candidates[0]["id"]
                fs.name = candidates[0]["battleName"] or candidates[0]["name"]
        party.append(fs)
    snap = Snapshot(game.game_image())
    for fs in party:
        fs.support = snap.exists("servant_check_support", L.field_support_region(fs.slot))
    return party

def register(roster: Roster, party: list[FieldServant]) -> list[tuple[FieldServant, str | None]]:
    """saves who was seen, returns (servant, reason) where reason is why skills should be read again"""
    result = []
    for fs in party:
        if fs.atlas_id is None:
            result.append((fs, None))
            continue
        owner = "mine"
        if fs.support:
            owner = "support"
        reason = roster.needs_check(fs.atlas_id, owner, fs.level)
        if owner == "support":
            # supports have different skill levels every run
            reason = "support"
        svt = atlas.get(fs.atlas_id)
        roster.seen(fs.atlas_id, owner, svt["name"], svt["className"], fs.level)
        result.append((fs, reason))
    return result

# skill levels from the confirm skill use dialog: tap skill, read it, then cancel
SKILL_LINE_RE = re.compile(r"(.+?)\s*L[vy][.,]?\s*(\d+)", re.IGNORECASE)   # ocr reads Lv as Ly sometimes

def skill_dialog_region() -> Region:
    return Region(600, 450, 1600, 100)

def cancel_click(L: Locations) -> tuple[int, int]:
    return L.cx(-400), 850

@dataclass
class SkillRead:
    slot: int
    skill: int
    name: str | None = None
    level: int | None = None
    note: str = ""

CONFIRMATION_OFF = ("no skill confirmation dialog appeared: the game's skill confirmation seems to be off. Turn it "
                    "back on in the game's options, then restart the engine")

def read_skill(battle, roster: Roster, fs: FieldServant, num: int) -> SkillRead:
    """never actually uses the skill, so the game's skill confirmation has to stay on"""
    from fgo_mcp.battle.actions import BattleError

    result = SkillRead(fs.slot, num)
    battle.require_idle()
    L = battle.L
    battle.click(L.skill(fs.slot, num), wait=0)

    def dialog_shown(s):
        return s.exists("skill_use", L.skill_use_region)

    if not battle.wait_until(dialog_shown, 1.2):
        # no dialog: skill on cooldown, or confirmation is turned off
        battle.click(L.extra_info_close_click, wait=0.6)
        if not battle.wait_until(battle.is_idle, 2):
            raise BattleError(CONFIRMATION_OFF)
        result.note = "no dialog (skill on cooldown?)"
        return result
    texts = []
    for t, score in ocr.text(battle.g.game_image(skill_dialog_region(), scale="native")):
        if score > 0.5:
            texts.append(t)
    text = " ".join(texts)
    m = SKILL_LINE_RE.search(text)
    if m:
        result.name = m.group(1).strip()
        result.level = int(m.group(2))
    battle.click(cancel_click(L), wait=0.6)
    if not battle.wait_until(battle.is_idle, 3):
        raise BattleError("skill dialog did not close after Cancel")
    if result.level is None:
        result.note = f"could not parse dialog text: {text!r}"
    return result

def refresh_skills(battle, roster: Roster, fs: FieldServant, fresh: bool = False) -> list[SkillRead]:
    """reads all 3 skills. fresh = battle just started so nothing is on cooldown, no dialog then means
    the skill isnt unlocked yet and gets level 0"""
    reads = []
    for n in (1, 2, 3):
        reads.append(read_skill(battle, roster, fs, n))
    if fresh:
        for r in reads:
            if r.level is None and r.note.startswith("no dialog"):
                r.level = 0
                r.note = "not unlocked yet (no dialog at the start of the battle)"
    all_read = True
    for r in reads:
        if r.level is None:
            all_read = False
    if fs.atlas_id and all_read:
        owner = "mine"
        if fs.support:
            owner = "support"
        levels = [r.level for r in reads]
        names = [r.name for r in reads]
        roster.set_skills(fs.atlas_id, owner, levels, names, level=fs.level)
    return reads