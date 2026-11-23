import sys
from pathlib import Path

import cv2

from frame_game import FrameGame
from mark_regions import draw_on
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.vision import Snapshot

# draws FGA's own detection areas (from its location files: BattleScreenLocations, AttackScreenLocations,
# MasterLocations, CardParser) on a saved frame, to compare with what we read (mark_regions.py)
#   python scripts/mark_fga_regions.py <frame> <out.jpg> [command|cards]
# blue = template, yellow = black screen check, red dots = taps
TEMPLATE = (255, 140, 0)
BLACK = (0, 230, 255)
H = 1440

class FgaAreas:
    def __init__(self, game: FrameGame):
        self.w = game.area.script_w
        self.wide = game.area.is_wide
        # FGA masterOffsetNewUI: center of the battle menu button found in this region
        search = Region(self.w - 400, 360, 400, 80)
        found = Snapshot(game.game_image()).find("battle_menu", search)
        if found:
            self.master_x = found.region.x + found.region.w//2
        else:
            self.master_x = self.w - 298
        self.master_search = search

    def cx(self, x):
        return x + self.w//2

    def rx(self, x):
        return x + self.w

    def skill(self, slot, n):
        xs = [[148, 324, 500], [784, 960, 1136], [1418, 1594, 1770]]
        x = xs[slot - 1][n - 1]
        if self.wide:
            return x + 108, 1117
        return x, 1158

    def enemy(self, e):
        x = [90, 570, 1050][e - 1]
        if self.wide:
            x += 183
        return x, 80

def command(f: FgaAreas) -> list:
    regions = []
    if f.wide:
        x, y, w, h = -660, -210, 400, 175
    else:
        x, y, w, h = -455, -181, 336, 116
    regions.append((Region(f.rx(x), H + y, w, h), TEMPLATE, "screenCheckRegion (battle)"))
    regions.append((Region(f.cx(-400), 500, 800, 400), BLACK, "npStartedRegion (black)"))
    regions.append((Region(f.cx(-210), 320, 420, 85), TEMPLATE, "skillUseRegion (dialog)"))
    regions.append((f.master_search, TEMPLATE, "master offset search (battle menu)"))
    stage_x = -638
    if f.wide:
        stage_x = -571
    regions.append((Region(f.master_x + stage_x, 23, 33, 53), TEMPLATE, "stageCountRegion"))
    if f.wide:
        cs = (-590, 180, 190, 120)
    else:
        cs = (-520, 210, 190, 120)
    regions.append((Region(f.rx(cs[0]), cs[1], cs[2], cs[3]), TEMPLATE, "commandSpellSearchRegion"))
    danger = [(0, 485), (485, 482), (967, 476)]
    for i in range(3):
        dx, dw = danger[i]
        if f.wide:
            dx += 150
        regions.append((Region(dx, 0, dw, 220), TEMPLATE, f"dangerRegion E{i + 1}"))
    for slot in (1, 2, 3):
        s3x, s3y = f.skill(slot, 3)
        regions.append((Region(s3x + 35, s3y + 67, 120, 120), TEMPLATE, f"servantPresentRegion S{slot}"))
        s2x = f.skill(slot, 2)[0]
        regions.append((Region(s2x + 20, 865, 40, 80), TEMPLATE, f"servantChangeCheck S{slot}"))
        regions.append((Region(s2x + 25, 710, 300, 170), TEMPLATE, f"servantChangeSupportCheck S{slot}"))
        for n in (1, 2, 3):
            kx, ky = f.skill(slot, n)
            regions.append((Region(kx + 22, ky + 28, 30, 30), TEMPLATE, f"img {slot}.{n}"))
    return regions

def command_taps(f: FgaAreas) -> list:
    taps = []
    for slot in (1, 2, 3):
        for n in (1, 2, 3):
            taps.append((f.skill(slot, n), f"{slot}.{n}"))
        taps.append((f.enemy(slot), f"E{slot}"))
    if f.wide:
        ax, ay = -460, -230
    else:
        ax, ay = -260, -240
    taps.append(((f.rx(ax), H + ay), "attackClick"))
    taps.append(((f.cx(0), 550), "safe middle"))
    master_offsets = (-740, -560, -400)
    for i in range(3):
        taps.append(((f.master_x + master_offsets[i] + 178, 620), f"M{i + 1}"))
    return taps

FACE = {"affinity": [-985, -470, 41, 554, 1068], "type": [-1280, -768, -256, 256, 768],
        "servant": [-1174, -660, -150, 364, 880], "click": [-980, -530, 20, 520, 1070]}

def cards(f: FgaAreas) -> list:
    regions = []
    for c in range(5):
        ax = f.cx(FACE["affinity"][c])
        tx = f.cx(FACE["type"][c])
        sx = f.cx(FACE["servant"][c])
        regions.append((Region(ax, 590, 250, 260), TEMPLATE, f"C{c + 1} affinityRegion"))
        regions.append((Region(ax - 50, 690, 250, 260), TEMPLATE, f"C{c + 1} supportCheckRegion"))
        regions.append((Region(tx, 1060, 512, 200), TEMPLATE, f"C{c + 1} typeRegion"))
        regions.append((Region(tx, 930, 248, 188), TEMPLATE, f"C{c + 1} stunRegion"))
        regions.append((Region(sx - 100, 700, 500, 400), TEMPLATE, f"C{c + 1} servantMatchRegion"))
    return regions

def cards_taps(f: FgaAreas) -> list:
    taps = []
    for c in range(5):
        taps.append(((f.cx(FACE["click"][c]), 1000), f"C{c + 1}"))
    np_points = [(-280, 220), (20, 400), (460, 400)]
    for i in range(3):
        x, y = np_points[i]
        taps.append(((f.cx(x), y), f"NP{i + 1}"))
    if f.wide:
        bx, by = -325, 1310
    else:
        bx, by = -160, 1370
    taps.append(((f.rx(bx), by), "backClick"))
    return taps

def draw(frame_path: str, out_path: str, screen: str) -> None:
    game = FrameGame(frame_path)
    f = FgaAreas(game)
    img = game.image.copy()
    if screen == "command":
        regions = command(f)
        taps = command_taps(f)
    else:
        regions = cards(f)
        taps = cards_taps(f)
    draw_on(img, game.area.rate, regions, taps, 0.36)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_path, img)

if __name__ == "__main__":
    screen = "command"
    if len(sys.argv) > 3:
        screen = sys.argv[3]
    draw(sys.argv[1], sys.argv[2], screen)