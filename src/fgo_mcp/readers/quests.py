import re
from dataclasses import dataclass, field

from fgo_mcp.device.geometry import Region
from fgo_mcp.readers import ocr, terminal

# quest rows in a terminal quest list. each row is found by its "AP n" box and the title,
# level and tags are at fixed distances from it
NOISE = re.compile(r"^(REC|Enemy|Class|Cost|Quest Progress|Daily|Quest|D a|u|\d|8|Remain.*|MENU|Help|Lv\.?\d+)$", re.I)
AP_RE = re.compile(r"A[Pp]\s*([\dOo]{1,3})")
TAG_RE = re.compile(r"-?(Previous|NEW!?|CLEAR|NEXT)-?", re.I)
EXTRA_TAG_RE = re.compile(r"Formation Restricted|No Battles|Free\s*Quest", re.I)

@dataclass
class QuestRow:
    row: int
    title: str
    ap: int | None
    rec_level: int | None = None
    tags: list[str] = field(default_factory=list)
    tap: tuple[int, int] = (0, 0)
    locked: str = ""

    def text(self) -> str:
        if self.locked:
            return f"Q{self.row} ??? (LOCKED: {self.locked}; AP {self.ap})"
        if not self.title:
            return f"Q{self.row} (name not readable: partly scrolled off; AP {self.ap})"
        tags = ""
        if self.tags:
            tags = f" [{', '.join(self.tags)}]"
        level = ""
        if self.rec_level:
            level = f", rec. Lv{self.rec_level}"
        return f"Q{self.row} {self.title} (AP {self.ap}{level}){tags}"

def clean_title(title_boxes) -> str:
    name = " ".join(b[2] for b in title_boxes).replace("É", "E")
    name = re.sub(r"[^\x20-\x7EÀ-ſ]", "", name)
    name = re.sub(r"^(NEW!?\s*)", "", name).strip(" ,")
    if len(re.sub(r"[^A-Za-z]", "", name)) < 3:
        # "???" titles and ocr junk
        return ""
    return name

def read_quests(game) -> list[QuestRow]:
    area = game.ensure_area()
    # columns were measured on 16:9, moved by where the MENU button is
    dx = terminal.Anchors(game).nav_right(0) - 2560
    boxes = ocr.text_boxes(game, Region(1150 + dx, 150, area.script_w - 1150 - dx, 1290))
    aps = []
    for x, y, t in boxes:
        m = AP_RE.fullmatch(t)
        if m:
            # "APO" is AP 0
            aps.append((x, y, int(m.group(1).replace("O", "0").replace("o", "0"))))
    aps.sort(key=lambda b: b[1])

    rows = []
    for i in range(len(aps)):
        ay = aps[i][1]
        ap = aps[i][2]
        title = []
        level = None
        tags = []
        extra_tags = set()
        lock = None
        for x, y, t in boxes:
            dy = y - ay
            if 1450 + dx < x < 2200 + dx and -200 < dy < -105 and not NOISE.match(t):
                title.append((x, y, t))
            if level is None and x > 2150 + dx and -170 < dy < -100:
                m = re.fullmatch(r"Lv\.?\s*(\d+)", t)
                if m:
                    level = int(m.group(1))
            if x < 1450 + dx and -260 < dy < 100 and TAG_RE.fullmatch(t):
                tags.append(t.strip("-! ").title())
            if -200 < dy < 120:
                m = EXTRA_TAG_RE.search(t)
                if m:
                    extra_tags.add(m.group(0).title())
            if lock is None and re.search(r"unlock\s*by", t, re.I) and -150 < dy < 20:
                lock = t.strip()
        tags += sorted(extra_tags)
        title.sort(key=lambda b: (round(b[1]/20), b[0]))
        if title:
            title_y = round(sum(b[1] for b in title)/len(title))
        else:
            title_y = ay - 150
        rows.append(QuestRow(i + 1, clean_title(title), ap, level, tags, (1750 + dx, title_y + 15), lock or ""))
    return rows