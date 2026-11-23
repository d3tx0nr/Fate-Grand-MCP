import re
from dataclasses import dataclass

from fgo_mcp.device.geometry import Region
from fgo_mcp.readers import ocr

# party screen by ocr (FGA doesnt read it). each column is found by its "Lv.N" box, the class and
# "+SUPPORT" are 646px above it on every layout. names come from the roster since only portraits are shown
LEVEL_RE = re.compile(r"L[Vv]\.?\s*(\d+)")

@dataclass
class PartyMember:
    pos: int     # 1-3 starting members, 4-6 sub members
    cls: str | None
    level: int | None
    support: bool = False
    name: str = ""

def find_levels(game, area) -> list[tuple[float, float, int]]:
    found = []
    # Lv row is at y 1047 on 16:9 and 995 on a 20:9 phone. two passes since the detector misses a box sometimes
    passes = [Region(40, 940, area.script_w - 80, 180), Region(60, 960, area.script_w - 120, 140)]
    for r in passes:
        for x, y, t in ocr.text_boxes(game, r):
            m = LEVEL_RE.fullmatch(t)
            if not m:
                continue
            duplicate = False
            for fx, _, _ in found:
                if abs(x - fx) <= 150:
                    duplicate = True
            if not duplicate:
                found.append((x, y, int(m.group(1))))
    found.sort()
    return found

def guess_name(known, cls: str, level: int) -> str:
    same_class = []
    for s in known:
        if (s.cls or "").lower() == cls.lower():
            same_class.append(s)
    # levels only go up, so a saved servant with a higher level than shown is someone else
    fits = []
    for s in same_class:
        if s.level is None or s.level <= level:
            fits.append(s)
    hits = []
    for s in fits:
        if s.level == level:
            hits.append(s)
    if not hits and len(fits) == 1:
        hits = fits
    names = []
    for h in hits[:3]:
        names.append(h.name)
    return " or ".join(names)

def read_party(game, roster=None) -> tuple[str, list[PartyMember]]:
    from fgo_mcp.battle.party import class_word

    area = game.ensure_area()
    boxes = ocr.text_boxes(game, Region(area.x_from_center(-480), 100, 1000, 120))
    # only the party name, not the "Sub Member" label below it
    top = 0
    if boxes:
        top = min(y for _, y, _ in boxes)
    title_parts = []
    for _, y, t in boxes:
        if y - top < 30:
            title_parts.append(t)
    title = " ".join(title_parts).strip("~ ")

    known = []
    if roster:
        known = roster.all("mine")
    members = []
    levels = find_levels(game, area)[:6]
    for i in range(len(levels)):
        lx, ly, level = levels[i]
        head_boxes = ocr.text_boxes(game, Region(round(lx) - 40, round(ly) - 720, 440, 140))
        head = " ".join(t for _, _, t in head_boxes)
        cls = None
        for word in re.findall(r"[A-Za-z]+", head):
            cls = class_word(word)
            if cls:
                break
        m = PartyMember(i + 1, cls, level, support="SUPPORT" in head.upper())
        if not m.support and cls:
            m.name = guess_name(known, cls, level)
        members.append(m)
    return title, members

def member_text(m: PartyMember) -> str:
    who = m.name or "unknown"
    tag = ""
    if m.support:
        tag = " (support)"
    return f"{m.pos}. {(m.cls or '?').title()} Lv{m.level} {who}{tag}"

def party_text(title: str, members: list[PartyMember]) -> str:
    starting = []
    sub = []
    for m in members:
        if m.pos <= 3:
            starting.append(member_text(m))
        else:
            sub.append(member_text(m))
    return f"{title}: starting {', '.join(starting)}; sub {', '.join(sub) or 'none'}"