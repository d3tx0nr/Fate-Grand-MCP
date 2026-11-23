import re
import time
from dataclasses import dataclass

import numpy as np

from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.vision import Snapshot
from fgo_mcp.readers import ocr

# terminal screens (map and quest lists). FGA only knows the menu button and taps the top quest,
# so the title, AP and list entries here are our own, read by ocr. measured on recorded NA frames

class Anchors:
    """left and right edge of a terminal screen, found from the top left button (news/close at x 134,
    terminal at x 120 on 16:9) and FGA's MENU button (350px from the right edge)"""
    TOP_LEFT = {"nav_btn_news": 134, "nav_btn_close": 134, "nav_btn_terminal": 120}

    def __init__(self, game):
        area = game.ensure_area()
        snap = Snapshot(game.game_image())
        self.script_w = area.script_w
        self.x_from_center = area.x_from_center
        search = Region(0, 0, area.script_w//3, 200)
        left = None
        for template_name, x in self.TOP_LEFT.items():
            m = snap.find(template_name, search)
            if m:
                left = m.region.x - x
                break
        menu = snap.find("menu", Region(area.script_w//2, 1100, area.script_w//2, 340))
        if left is not None:
            self.left = left
        else:
            self.left = area.nav_left(0)
        if menu:
            self.right = menu.region.x + 350
        else:
            self.right = area.nav_right(0)

    def nav_left(self, x: int) -> int:
        return self.left + x

    def nav_right(self, x: int) -> int:
        return self.right + x

    @property
    def width(self) -> int:
        return self.right - self.left

def ap_region(area) -> Region:
    # "AP 55/138", at y 1318 on 16:9 and 1236 on a 20:9 phone so the box covers both
    return Region(area.nav_left(330), 1150, 700, 290)

def title_region(area) -> Region:
    return Region(area.nav_right(-1260), 0, 1260, 170)

def list_region(area) -> Region:
    return Region(area.nav_right(-1270), 170, 1150, 1080)

def map_region(area) -> Region:
    return Region(area.nav_left(260), 150, area.width - 260, 1060)

HUD_RIGHT = 1150
HUD_TOP = 1100
NEXT_LABEL_DY = 700
NEXT_NODE_DY = 70
NOISE = re.compile(r"^(A[Pp]\s*[\dOo]{1,3}|NEW!?|MENU|-?C[LI]EAR-?|Tap here!?|Ongoing!?|Pickup:?|Drop ?Rate ?UP!?|SSR|\*\d|\d+)$", re.I)
INFO_LINE = re.compile(r"(Day\(s\)|Period|Remaining|Until \d|PST|Requirements|AP Cost|Quest Progress|^Cost)", re.I)

@dataclass
class Entry:
    n: int
    text: str
    tap: tuple[int, int]
    kind: str = "entry"    # entry in a list, or place (a label on a map)

def scroll_track(area) -> Region:
    return Region(area.nav_right(-60), 190, 24, 980)

THUMB_MIN_ROWS = 30   # the scrollbar thumb is a tall white pill, shorter bright bits are list art

def list_position(game) -> tuple[float, float] | None:
    """(start, end) of the part of the list on screen, as fractions of the whole list"""
    img = game.game_image(scroll_track(Anchors(game)))
    if img.ndim == 3:
        gray = img.mean(axis=2)
    else:
        gray = img
    width = gray.shape[1]
    core = gray[:, width//4:width*3//4]
    bright = core.min(axis=1) > 200
    best_start = 0
    best_end = 0
    run = 0
    start = 0
    for i, is_bright in enumerate(np.append(bright, False)):
        if is_bright:
            if run == 0:
                start = i
            run += 1
        else:
            if run > best_end - best_start:
                best_start = start
                best_end = start + run
            run = 0
    if best_end - best_start < THUMB_MIN_ROWS:
        return None
    n = len(bright)
    return round(best_start/n, 2), round(best_end/n, 2)

def position_text(pos: tuple[float, float] | None) -> str:
    if pos is None:
        return ""
    top, bottom = pos
    above = top > 0.03
    below = bottom < 0.97
    if above and below:
        where = "more above and below"
    elif below:
        where = "more below"
    elif above:
        where = "more above"
    else:
        where = "all"
    return f"List position: showing {round(top*100)}-{round(bottom*100)}% of the list ({where})"

# master mission popups slide in over the title. found by the "Mission" tab, one template per colour
TOAST_TABS = ("nav_mission_toast", "nav_mission_toast_gold", "nav_mission_toast_green")
TOAST_SIMILARITY = 0.82
HUD_WORDS = re.compile(r"ltd\.?\s*time|million|weekly|missions?|cleared|^left$|\d+\s*/+\s*\d+", re.I)

def toast_edge(game) -> int | None:
    a = game.ensure_area()
    snap = Snapshot(game.game_image())
    edge = None
    for template_name in TOAST_TABS:
        m = snap.find(template_name, Region(0, 0, a.script_w, 200), TOAST_SIMILARITY)
        if m and (edge is None or m.region.right > edge):
            edge = m.region.right
    return edge

def is_spaced(t: str) -> bool:
    # fancy subtitles come out as spaced letters "C h a l d e a"
    words = t.split()
    if len(words) < 3:
        return False
    single = 0
    for w in words:
        if len(w) == 1:
            single += 1
    return single >= len(words)*0.6

def read_title(game, wait_s: float = 8.0) -> str:
    # wait for a mission popup to go away, if it stays ignore the text left of its tab
    end = time.time() + wait_s
    edge = toast_edge(game)
    while edge is not None and time.time() < end:
        time.sleep(1.0)
        edge = toast_edge(game)
    boxes = []
    for b in ocr.text_boxes(game, title_region(Anchors(game))):
        if b[2].lower() in ("event shop", "rewards") or is_spaced(b[2]):
            continue
        if not re.search(r"[A-Za-z]{3}", b[2]):
            continue
        if edge is not None and b[0] <= edge:
            continue
        boxes.append(b)
    boxes.sort(key=lambda b: (round(b[1]/40), b[0]))
    words = []
    for _, _, t in boxes:
        # subtitle under a title repeats part of it
        repeated = False
        for w in words:
            if t.lower() in w.lower():
                repeated = True
        if not repeated:
            words.append(t)
    return " ".join(words)

def read_ap(game) -> tuple[int, int] | None:
    text = " ".join(t for _, _, t in ocr.text_boxes(game, ap_region(Anchors(game))))
    m = re.search(r"AP\s*(\d{1,3})\s*/\s*(\d{2,3})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None

def read_map_places(game) -> list[Entry]:
    """every label on a map (area buttons and quest nodes), tapped on the label center"""
    k = game.ensure_area().rate
    anchors = Anchors(game)
    r = map_region(anchors)
    places = []
    rects = ocr.rects(game.game_image(r, scale="native"))
    rects.sort(key=lambda b: (b[1], b[0]))
    for x, y, w, h, t in rects:
        sx = x/k + r.x
        sy = y/k + r.y
        sw = w/k
        sh = h/k
        if sx < anchors.nav_left(HUD_RIGHT) and sy > HUD_TOP:
            # master level and AP block
            continue
        t = t.strip()
        if t.upper() == "NEXT" or HUD_WORDS.search(t):
            continue
        letters = re.sub(r"[^A-Za-zÀ-ž]", "", t)
        if len(letters) >= 4 and not NOISE.match(t) and t.upper() != "NOTES":
            places.append(Entry(len(places) + 1, t, (round(sx + sw/2), round(sy + sh/2)), "place"))
    return mark_next(game, places)

def mark_next(game, places: list[Entry]) -> list[Entry]:
    # the NEXT marker bounces over the next story node. the label under it gets [NEXT] and goes first
    snap = Snapshot(game.game_image())
    anchors = Anchors(game)
    for m in snap.find_all("nav_next", Region(anchors.nav_left(0), 0, anchors.width, 1300), 0.8):
        mx = m.region.x + m.region.w/2
        my = m.region.bottom
        below = []
        for e in places:
            if 0 < e.tap[1] - my < NEXT_LABEL_DY and abs(e.tap[0] - mx) < 450 and not e.text.endswith("[NEXT]"):
                below.append(e)
        if below:
            closest = below[0]
            for e in below:
                if abs(e.tap[0] - mx) + (e.tap[1] - my)/2 < abs(closest.tap[0] - mx) + (closest.tap[1] - my)/2:
                    closest = e
            places.remove(closest)
            places.insert(0, Entry(0, f"{closest.text} [NEXT]", closest.tap, "place"))
        else:
            places.insert(0, Entry(0, "NEXT node", (round(mx), round(my + NEXT_NODE_DY)), "place"))
    for i in range(len(places)):
        places[i].n = i + 1
    return places

BANNER_LINE_GAP = 110

def read_entries(game, region: Region | None = None) -> list[Entry]:
    if region is None:
        region = list_region(Anchors(game))
    boxes = []
    for b in ocr.text_boxes(game, region):
        t = b[2]
        if len(re.sub(r"[^A-Za-z]", "", t)) < 3:
            continue
        if NOISE.match(t) or INFO_LINE.search(t) or is_spaced(t):
            continue
        if t[0].islower():
            # bits of labels behind a popup
            continue
        boxes.append(b)
    boxes.sort(key=lambda b: (b[1], b[0]))

    lines = []
    for b in boxes:
        if lines and abs(b[1] - lines[-1][0][1]) < 25:
            lines[-1].append(b)
        else:
            lines.append([b])
    line_entries = []
    for words in lines:
        words.sort(key=lambda b: b[0])
        text = " ".join(w[2] for w in words)
        avg_x = round(sum(w[0] for w in words)/len(words)) + 60
        line_entries.append(Entry(len(line_entries) + 1, text, (avg_x, round(words[0][1]) + 25)))

    # drop subtitles that repeat part of the line above ("Quick Link to Map" / "Quick")
    kept = []
    for e in line_entries:
        is_subtitle = False
        for o in line_entries:
            if e is not o and e.text.lower() in o.text.lower() and 0 < e.tap[1] - o.tap[1] < 120:
                is_subtitle = True
        if not is_subtitle:
            kept.append(e)

    # lines of one banner are ~72px apart, different banners 190+, so join close lines into one entry
    banners = []
    for e in kept:
        if banners and e.tap[1] - banners[-1][-1].tap[1] < BANNER_LINE_GAP:
            banners[-1].append(e)
        else:
            banners.append([e])
    result = []
    for b in banners:
        text = " ".join(e.text for e in b)
        result.append(Entry(len(result) + 1, text, b[len(b)//2].tap))
    return result