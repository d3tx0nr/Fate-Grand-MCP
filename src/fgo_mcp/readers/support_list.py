import re
import unicodedata
from dataclasses import dataclass

from fgo_mcp.data import atlas, kits
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations
from fgo_mcp.fga.vision import Snapshot
from fgo_mcp.readers import ocr

# support list read like FGA does it: one entry per confirm setup button (SupportBoundsFinder).
# friend/guest/follow and skill 10 by FGA templates, name and np by ocr inside the entry
CONFIRM_SIMILARITY = 0.75
SKILL_TEN_SIMILARITY = 0.68
EMPTY_SKILL_STD = 45      # empty skill slot is flat (std ~25), an icon is 70-90
GUEST_MARK_DY = 282       # friend/guest mark y minus top of the entry, measured on logged frames
CLASS_TABS = {"all": 184, "saber": 320, "archer": 454, "lancer": 568, "rider": 724, "caster": 858,
              "assassin": 994, "berserker": 1130, "extra": 1264, "mix": 1402}
SERVANT_LABEL_RE = re.compile(r"s[eoa]rv[ae]nt")

@dataclass
class SupportEntry:
    row: int
    bounds: Region
    kind: str = ""     # friend, guest, follow or ""
    name: str = ""
    atlas_id: int | None = None
    cls: str | None = None
    np_level: int | None = None
    skills: tuple[int | None, int | None, int | None] = (None, None, None)
    complete: bool = True

    @property
    def tap(self) -> tuple[int, int]:
        b = self.bounds
        return b.x + b.w//3, b.y + b.h//2

    def text(self) -> str:
        cls_text = ""
        if self.cls:
            cls_text = f" ({self.cls.title()})"
        skill_parts = []
        for v in self.skills:
            if v is None:
                skill_parts.append("?")
            elif v == 0:
                skill_parts.append("-")
            else:
                skill_parts.append(str(v))
        if self.atlas_id:
            role = kits.np_role(atlas.get(self.atlas_id))
        else:
            role = "unidentified"
        cut = ""
        if not self.complete:
            cut = " [partly off screen: scroll to pick]"
        return f"R{self.row} {self.name or '?'}{cls_text}, {self.kind or '-'}: NP Lv{self.np_level or '?'}; skills {'/'.join(skill_parts)} | {role}{cut}"

def plain(t: str) -> str:
    result = ""
    for c in unicodedata.normalize("NFKD", t):
        if not unicodedata.combining(c):
            result += c
    return result.lower()

def clip(r: Region, top: int, bottom: int) -> Region:
    y0 = max(r.y, top)
    y1 = min(r.bottom, bottom)
    return Region(r.x, y0, r.w, max(y1 - y0, 0))

def identify(name: str) -> int | None:
    candidates = atlas.match(name)
    if len(candidates) == 1:
        return candidates[0]["id"]
    return None

def skill_digit(t: str) -> int | None:
    t = t.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    numbers = re.findall(r"\d+", t)
    if not numbers:
        return None
    v = int(numbers[0])
    if 1 <= v <= 10:
        return v
    return None

def entries(s: Snapshot, L: Locations) -> list[SupportEntry]:
    matches = s.find_all("support_region_tool", L.support_confirm_setup_region, CONFIRM_SIMILARITY)
    bounds = []
    for m in matches:
        bounds.append(L.support_bounds(m.region.y - 70))
    # guests dont have the confirm setup button, so use the guest mark to find them
    for m in s.find_all("guest", L.support_friend_region):
        y = m.region.y - GUEST_MARK_DY
        already_found = False
        for b in bounds:
            if abs(b.y - y) <= 100:
                already_found = True
        if not already_found:
            bounds.append(L.support_bounds(y))
    bounds.sort(key=lambda r: r.y)
    script_h = L.a.script_h
    result = []
    for i in range(len(bounds)):
        b = bounds[i]
        result.append(SupportEntry(i + 1, b, complete=b.y >= 0 and b.bottom <= script_h))
    return result

def read_skill_levels(game, s: Snapshot, b: Region) -> tuple:
    levels = []
    for i in range(3):
        box = Region(b.x + 1610 + i*90, b.y + 325, 60, 50)
        if s.exists("skill_ten", box, SKILL_TEN_SIMILARITY):
            levels.append(10)
            continue
        crop = game.game_image(Region(box.x - 40, box.y - 10, 110, 70), scale="native")
        if float(crop.std()) < EMPTY_SKILL_STD:
            # no skill in the slot (npc guests with 2 skills)
            levels.append(0)
        else:
            # just the digit without the icon art reads a lot better
            digit = game.game_image(Region(box.x - 40, box.y + 5, 60, 55), scale="native")
            levels.append(ocr.vote(digit, skill_digit, min_agree=2))
    return tuple(levels)

def read(game, s: Snapshot, L: Locations) -> list[SupportEntry]:
    found = entries(s, L)
    for e in found:
        b = e.bounds
        friend_region = clip(L.support_friend_region, b.y, b.bottom)
        e.kind = ""
        if friend_region.h:
            for kind in ("friend", "guest", "follow"):
                if s.exists(kind, friend_region):
                    e.kind = kind
                    break
        if not e.complete:
            continue
        e.skills = read_skill_levels(game, s, b)

        # name and np, only in the text area right of the portrait and CE
        text_area = Region(b.x + 380, b.y, 1600, b.h)
        k = game.ensure_area().rate
        boxes = []
        for x, y, t in ocr.boxes(game.game_image(text_area, scale="native"), pad=0):
            boxes.append((x/k + text_area.x, y/k + text_area.y, t.strip()))
        label = None
        for bx in boxes:
            if SERVANT_LABEL_RE.fullmatch(plain(bx[2])):
                label = bx
                break
        if label:
            lx, ly, _ = label
            same_line = []
            np_line_boxes = []
            for bx in boxes:
                if abs(bx[1] - ly) < 30 and bx[0] > lx + 100:
                    same_line.append(bx)
                if 40 < bx[1] - ly < 120:
                    np_line_boxes.append(bx)
            same_line.sort(key=lambda bx: bx[0])
            np_line_boxes.sort(key=lambda bx: bx[0])
            if same_line:
                e.name = same_line[0][2]
            np_line = " ".join(bx[2] for bx in np_line_boxes)
            m = re.search(r"L[vx]\.?\s*(\d)", np_line)
            if m:
                e.np_level = int(m.group(1))
        if e.name:
            e.atlas_id = identify(e.name)
            if e.atlas_id:
                svt = atlas.get(e.atlas_id)
                e.cls = svt["className"]
                # full name, battle names are the same for different versions
                e.name = svt["name"]
    return found

def loaded(s: Snapshot, L: Locations) -> bool:
    if s.exists("support_not_found", L.support_not_found_region):
        return True
    if s.exists("support_region_tool", L.support_confirm_setup_region, CONFIRM_SIMILARITY):
        return True
    return s.exists("guest", L.support_friend_region)

def list_state(s: Snapshot, L: Locations) -> dict:
    return {
        "dialog_open": not s.exists("support_extra", L.support_extra_region),
        "empty": s.exists("support_not_found", L.support_not_found_region),
        "at_top": s.exists("support_scrollbar_top", L.support_top_scrollbar_region),
        "at_bottom": s.exists("support_scrollbar_bottom", L.support_bottom_scrollbar_region),
        "refresh_ready": s.exists("support_refresh", L.support_refresh_region),
    }