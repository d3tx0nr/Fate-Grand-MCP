import sys
from pathlib import Path

import cv2

from frame_game import FrameGame
from fgo_mcp.battle import party
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations
from fgo_mcp.fga.vision import Snapshot
from fgo_mcp.readers import hud

# draws every region we read on the battle screens onto a saved frame, using the same region functions
# as the engine so the picture always matches the code
#   python scripts/mark_regions.py <frame> <out.jpg> [command|cards]
# green = ocr, blue = template, yellow = colour check, red dots = taps
OCR = (0, 220, 0)
TEMPLATE = (255, 140, 0)
COLOUR = (0, 230, 255)
TAP = (0, 0, 255)

def command_regions(L: Locations, area, snap) -> list:
    regions = [
        (hud.hud_region(snap, L), OCR, "wave / enemies left"),
        (hud.turn_region(snap, L), OCR, "turn"),
        (hud.stars_region(snap, L), OCR, "stars"),
        (L.battle_check_region, TEMPLATE, "battle (classify)"),
        (L.master_offset_search_region, TEMPLATE, "master button"),
    ]
    for i in (1, 2, 3):
        regions.append((hud.enemy_name_region(L, i), OCR, f"E{i} name"))
        regions.append((hud.enemy_hp_region(L, i), OCR, f"E{i} HP"))
        regions.append((hud.enemy_class_region(L, i), OCR, f"E{i} class"))
        regions.append((L.danger_region(i), TEMPLATE, f"E{i} danger"))
        regions.append((Region(L.enemy(i)[0] - 90, 0, 190, 130), COLOUR, f"E{i} targeted"))
        regions.append((party.name_region(L, i), OCR, f"S{i} class Lv name"))
        regions.append((hud.hp_region(L, i), OCR, f"S{i} HP"))
        regions.append((hud.np_region(L, i), OCR, f"S{i} NP"))
        regions.append((L.field_support_region(i), TEMPLATE, f"S{i} support (FGA)"))
        regions.append((L.servant_present_region(i), TEMPLATE, f"S{i} present"))
        for n in (1, 2, 3):
            regions.append((hud.skill_cooldown_region(L, i, n), TEMPLATE, f"{i}.{n} Turn(s)"))
            regions.append((hud.skill_turns_region(L, i, n), OCR, ""))
    return regions

def command_taps(L: Locations) -> list:
    taps = []
    for slot in (1, 2, 3):
        for skill in (1, 2, 3):
            taps.append((L.skill(slot, skill), f"{slot}.{skill}"))
        taps.append((L.enemy(slot), f"E{slot}"))
    taps.append((L.attack_click, "attack"))
    return taps

def cards_regions(L: Locations, area, snap) -> list:
    regions = [(hud.np_strip(area), OCR, "NP card names")]
    for c in range(1, 6):
        regions.append((L.card_type_region(c), TEMPLATE, f"C{c} type"))
        regions.append((L.card_affinity_region(c), TEMPLATE, f"C{c} weak/resist"))
        regions.append((L.card_stun_region(c), TEMPLATE, f"C{c} stun"))
        regions.append((L.card_servant_region(c), TEMPLATE, f"C{c} face"))
        x = L.card_click(c)[0]
        regions.append((Region(x - 200, 705, 280, 85), OCR, f"C{c} crit %"))
    return regions

def cards_taps(L: Locations) -> list:
    taps = []
    for c in range(1, 6):
        taps.append((L.card_click(c), f"C{c}"))
    for slot in (1, 2, 3):
        taps.append((L.np_click(slot), f"NP{slot}"))
    return taps

def draw_on(img, k: float, regions: list, taps: list, font_size: float = 0.38) -> None:
    for r, colour, label in regions:
        x0, y0, x1, y1 = round(r.x*k), round(r.y*k), round(r.right*k), round(r.bottom*k)
        cv2.rectangle(img, (x0, y0), (x1, y1), colour, 2)
        cv2.putText(img, label, (x0 + 3, max(y0 + 12, 12)), cv2.FONT_HERSHEY_SIMPLEX, font_size, colour, 1, cv2.LINE_AA)
    for (x, y), label in taps:
        cv2.circle(img, (round(x*k), round(y*k)), 5, TAP, -1)
        cv2.putText(img, label, (round(x*k) + 7, round(y*k) + 4), cv2.FONT_HERSHEY_SIMPLEX, font_size, TAP, 1, cv2.LINE_AA)

def draw(frame_path: str, out_path: str, screen: str) -> None:
    game = FrameGame(frame_path)
    area = game.area
    L = Locations(area)
    img = game.image.copy()
    snap = Snapshot(game.game_image())
    if screen == "command":
        regions = command_regions(L, area, snap)
        taps = command_taps(L)
    else:
        regions = cards_regions(L, area, snap)
        taps = cards_taps(L)
    draw_on(img, area.rate, regions, taps)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_path, img)

if __name__ == "__main__":
    screen = "command"
    if len(sys.argv) > 3:
        screen = sys.argv[3]
    draw(sys.argv[1], sys.argv[2], screen)