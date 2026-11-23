import difflib
import re
from dataclasses import dataclass, field

import cv2
import numpy as np

from fgo_mcp.battle.party import CLASS_WORDS
from fgo_mcp.device.game import Game
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations
from fgo_mcp.fga.vision import Snapshot, template
from fgo_mcp.log import record
from fgo_mcp.readers import glyphs
from fgo_mcp.readers import ocr

COOLDOWN_SIMILARITY = 0.43
COOLDOWN_MASK_CUT = 120   # only the letters of the Turn(s) label are compared, the icon art behind them changes

# FGA doesnt have regions for the hud text, so these are placed relative to things FGA does find:
# battle/enemy/turn text from the wave counter, stars from the attack button and the skill row
def master_x(snap: Snapshot, L: Locations) -> int:
    found = snap.find("battle_menu", L.master_offset_search_region)
    if found:
        return found.region.x + found.region.w//2
    return L.default_master_offset_x()

def hud_region(snap: Snapshot, L: Locations) -> Region:
    return Region(L.stage_count_region(master_x(snap, L)).x - 210, 10, 440, 210)

def turn_region(snap: Snapshot, L: Locations) -> Region:
    return Region(L.stage_count_region(master_x(snap, L)).x - 210, 150, 440, 80)

def stars_region(snap: Snapshot, L: Locations) -> Region:
    # only the "(15)" part of "0 (15)"
    attack = snap.find("battle", L.battle_check_region)
    if attack:
        x = attack.region.x - 108
    else:
        x = L.battle_check_region.x - 100
    return Region(x, L.skill(1, 1)[1] + 182, 160, 60)

def only_digits(s: str) -> int | None:
    d = re.sub(r"\D", "", s)
    if d:
        return int(d)
    return None

def fix_np(v: int | None) -> int | None:
    # np can only be 0-300, ocr sometimes adds an extra digit at the end ("108%" -> 1082)
    while v is not None and v > 300:
        if v >= 1000:
            v = v//10
        else:
            v = None
    return v

def np_region(L: Locations, slot: int) -> Region:
    x, y = L.skill(slot, 1)
    return Region(x + 290, y + 147, 200, 70)

def hp_region(L: Locations, slot: int) -> Region:
    x, y = L.skill(slot, 1)
    return Region(x + 270, y + 74, 210, 64)

def enemy_hp_region(L: Locations, enemy: int) -> Region:
    return Region(L.enemy(enemy)[0] + 120, 60, 330, 80)

def enemy_class_region(L: Locations, enemy: int) -> Region:
    return Region(L.enemy(enemy)[0] - 90, 125, 190, 90)

def enemy_name_region(L: Locations, enemy: int) -> Region:
    return Region(L.enemy(enemy)[0] + 60, 22, 440, 55)

@dataclass
class Enemy:
    slot: int
    hp: int | None
    name: str = ""
    cls: str | None = None
    danger: bool = False

    def text(self) -> str:
        tags = []
        if self.cls:
            tags.append(self.cls.title())
        if self.danger:
            tags.append("DANGER")
        tag = ""
        if tags:
            tag = f" ({', '.join(tags)})"
        return f"E{self.slot} {self.name or 'enemy'}{tag} HP {self.hp}"

@dataclass
class Hud:
    wave: int | None = None
    waves: int | None = None
    turn: int | None = None
    enemies_left: int | None = None
    stars: int | None = None
    np: list[int | None] = field(default_factory=list)
    hp: list[int | None] = field(default_factory=list)
    enemies: list[Enemy] = field(default_factory=list)
    target: int | None = None    # enemy the game is targeting (blue diamond)

def clean_name(s: str) -> str:
    words = []
    for w in s.split():
        has_digit = False
        for ch in w:
            if ch.isdigit():
                has_digit = True
        if has_digit:
            continue
        if len(w) > 2 or w.islower() or (w.isupper() and len(w) == 1):
            words.append(w)
    return " ".join(words)

def parse_turn(t: str) -> int | None:
    m = re.search(r"(\d+)\s*Tur", t)
    if m and int(m.group(1)) > 0:
        return int(m.group(1))
    return None

def read_turn(game: Game) -> int | None:
    L = Locations(game.ensure_area())
    region = turn_region(Snapshot(game.game_image()), L)
    return ocr.vote(game.game_image(region, scale="native"), parse_turn)

def parse_np(t: str) -> int | None:
    m = re.search(r"(\d[\d.]*)\s*%", t)
    if m:
        return fix_np(only_digits(m.group(1)))
    return None

def parse_stars(t: str) -> int | None:
    # crop only has "(N)", but ocr can drop a bracket or read 0 as O
    t = t.replace("O", "0").replace("o", "0")
    m = re.search(r"\(?\s*(\d{1,3})\s*\)?", t)
    if m:
        return int(m.group(1))
    return None

def parse_hp(t: str) -> int | None:
    t = t.replace(".", "")   # ocr adds dots sometimes: "2,1.04"
    numbers = re.findall(r"\d{1,3}(?:,\d{3})+|\d{3,}", t)
    best = None
    for n in numbers:
        v = only_digits(n)
        if best is None or v > best:
            best = v
    return best

def targeted_enemy(game: Game) -> int | None:
    L = Locations(game.ensure_area())
    best = 0.03
    slot = None
    for e in (1, 2, 3):
        img = game.game_image(Region(L.enemy(e)[0] - 90, 0, 190, 130), scale="native")
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        is_blue = (hsv[..., 0] > 90) & (hsv[..., 0] < 115) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)
        blue = float(is_blue.mean())
        if blue > best:
            best = blue
            slot = e
    return slot

def skill_cooldown_region(L: Locations, slot: int, skill: int) -> Region:
    # a skill on cooldown is darker and shows "Turn(s)" at its bottom left
    x, y = L.skill(slot, skill)
    return Region(x - 100, y + 20, 160, 70)

def skill_turns_region(L: Locations, slot: int, skill: int) -> Region:
    # the number of turns left, right of the Turn(s) label
    x, y = L.skill(slot, skill)
    return Region(x + 28, y + 15, 47, 55)

def parse_turns(t: str) -> int | None:
    t = t.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    m = re.search(r"\d{1,2}", t)
    if m and 1 <= int(m.group()) <= 20:
        return int(m.group())
    return None

def cooldown_label_mask():
    tpl = template("skill_cooldown")
    mask = (tpl > COOLDOWN_MASK_CUT).astype(np.uint8) * 255
    return cv2.dilate(mask, np.ones((2, 2), np.uint8))

def cooldown_score(snap: Snapshot, r: Region) -> float:
    hay = snap.crop(r)
    tpl = template("skill_cooldown")
    if hay.shape[0] < tpl.shape[0] or hay.shape[1] < tpl.shape[1]:
        return 0.0
    result = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED, mask=cooldown_label_mask())
    # masked matching gives nan/inf on flat patches
    result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
    score = float(result.max())
    record.note_template("skill_cooldown", r, None, score, COOLDOWN_SIMILARITY)
    return score

def read_skill_cooldowns(game: Game, skills: list[tuple[int, int]]) -> dict[tuple[int, int], int | None]:
    """the skills (slot, skill) that show the cooldown overlay right now, with the turns left
    (None if the number couldnt be read). read every time, so skills that got their cooldown
    shortened by something show up as ready again"""
    L = Locations(game.ensure_area())
    snap = Snapshot(game.game_image())
    cooling = {}
    for slot, skill in skills:
        if cooldown_score(snap, skill_cooldown_region(L, slot, skill)) < COOLDOWN_SIMILARITY:
            continue
        crop = game.game_image(skill_turns_region(L, slot, skill), scale="native")
        cooling[(slot, skill)] = ocr.vote(crop, parse_turns)
    return cooling

def np_strip(a) -> Region:
    # on the card screen ready nps show up as cards with their name in big letters
    return Region(0, 150, a.script_w, 550)

def ready_np_slots(game: Game, party) -> set[int]:
    from fgo_mcp.data import atlas
    from fgo_mcp.data import kits

    words = []
    for t, _ in ocr.text(game.game_image(np_strip(game.ensure_area()), scale="native")):
        for w in t.split():
            words.append(w.lower())
    ready = set()
    for f in party:
        if not f.atlas_id:
            continue
        np_ = kits.pick_np(atlas.get(f.atlas_id))
        np_name = ""
        if np_:
            np_name = np_.get("name") or ""
        name_words = []
        for w in re.findall(r"[A-Za-z]{3,}", np_name):
            name_words.append(w.lower())
        if len(name_words) == 0:
            continue
        hits = 0
        for nw in name_words:
            for w in words:
                if difflib.SequenceMatcher(None, nw, w).ratio() >= 0.8:
                    hits += 1
                    break
        if hits >= max(1, len(name_words)//2):
            ready.add(f.slot)
    return ready

def number(crop, font: str) -> int | None:
    """np % and enemy hp use glyphs when they're sure, otherwise ocr (and ocr teaches the glyphs).
    servant hp is ocr only, glyphs misread it in testing"""
    if font == "np":
        parse = parse_np
    else:
        parse = parse_hp
    if font == "hp":
        return ocr.vote(crop, parse)
    if font == "ehp" and len(glyphs.segments(crop)[0]) < 3:
        # nothing in this slot, enemy hp always has 3+ digits
        return None
    text = glyphs.read(crop, font)
    if text is not None:
        value = parse(text)
        if value is not None:
            return value
    value = ocr.vote(crop, parse)
    if value is not None:
        if font == "np":
            glyphs.learn(crop, font, f"{value}%")
        else:
            glyphs.learn(crop, font, str(value))
    return value

def read_np(game: Game) -> list[int | None]:
    L = Locations(game.ensure_area())
    values = []
    for slot in (1, 2, 3):
        values.append(number(game.game_image(np_region(L, slot), scale="native"), "np"))
    return values

def read_stars(game: Game) -> int | None:
    L = Locations(game.ensure_area())
    region = stars_region(Snapshot(game.game_image()), L)
    return ocr.vote(game.game_image(region, scale="native"), parse_stars)

def enemy_class(text: str) -> str | None:
    text = text.upper().replace(" ", "")
    for word, cls in CLASS_WORDS.items():
        if word in text:
            return cls
    return None

def read(game: Game, enemy_cache: dict | None = None) -> Hud:
    """enemy_cache = {(wave, slot): {...}} keeps enemy names and classes, they only change when a new
    enemy comes into the slot (hp goes up)"""
    L = Locations(game.ensure_area())
    snap = Snapshot(game.game_image())

    def img(r):
        return game.game_image(r, scale="native")

    h = Hud()
    hud_texts = []
    for t, _ in ocr.text(img(hud_region(snap, L))):
        hud_texts.append(t)
    texts = " | ".join(hud_texts)
    m = re.search(r"(\d+)\s*/\s*(\d+)", texts)
    if m:
        h.wave = int(m.group(1))
        h.waves = int(m.group(2))
    m = re.search(r"Remaining\s*:?\s*(\d+)", texts)
    if m:
        h.enemies_left = int(m.group(1))
    h.turn = ocr.vote(img(turn_region(snap, L)), parse_turn)
    h.stars = ocr.vote(img(stars_region(snap, L)), parse_stars)
    for slot in (1, 2, 3):
        h.np.append(number(img(np_region(L, slot)), "np"))
        h.hp.append(number(img(hp_region(L, slot)), "hp"))

    if enemy_cache is None:
        enemy_cache = {}
    for e in (1, 2, 3):
        hp = number(img(enemy_hp_region(L, e)), "ehp")
        enemy = Enemy(e, hp)
        if hp:
            enemy.danger = snap.exists("target_danger", L.danger_region(e))
            known = enemy_cache.get((h.wave, e))
            if known and known["hp"] is not None and hp <= known["hp"] and known["name"]:
                # same enemy as before, just took damage
                enemy.cls = known["cls"]
                enemy.name = known["name"]
            else:
                class_texts = []
                for t, _ in ocr.text(img(enemy_class_region(L, e))):
                    class_texts.append(t)
                enemy.cls = enemy_class(" ".join(class_texts))
                enemy.name = clean_name(ocr.line(img(enemy_name_region(L, e))))
            enemy_cache[(h.wave, e)] = {"hp": hp, "cls": enemy.cls, "name": enemy.name}
        h.enemies.append(enemy)
    h.target = targeted_enemy(game)
    return h