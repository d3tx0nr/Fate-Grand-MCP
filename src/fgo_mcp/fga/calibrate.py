"""
Layout calibration: find where the game is drawn on screens that FGA's size rules get wrong.

A notch, a navigation bar or an unusual render size can break that. With the game on a Terminal
screen, two buttons are searched across the whole screenshot at several sizes.

Done using finding the space between the top left button(news, close or terminal) and the 
bottom right button (menu)
"""

import json

import cv2
import numpy as np

from fgo_mcp.fga.vision import template
from fgo_mcp.path import DATA
from fgo_mcp.device.geometry import Region, GameArea, SCRIPT_H

MENU = ("menu", -350, 1244)  # template, x from the right edge, y
TOP_LEFT = [("nav_btn_news", 134, 52), ("nav_btn_close", 134, 52), ("nav_btn_terminal", 120, 52)]
PATH = DATA / "calibration.json"

def best_match(gray: np.ndarray, name: str, scales) -> tuple[float, int, int]:
    """(score, x, y) of a template anywhere on the screen"""
    best = (0.0, 0, 0)
    for k in scales:
        tpl = cv2.resize(template(name), None, fx=k, fy=k)
        _, score, _, (x,y) = cv2.minMaxLoc(cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED))
        best = max(best, (score, x, y))
    return best

def measure(frame: np.ndarray) -> Region | None:
    """The game area in screen pixels, or None when the two buttons are not both on screen."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    full_height = gray.shape[0]/720
    scales = [full_height*f for f in np.arange(0.6, 1.06, 0.02)]
    menu_score, mx, my = best_match(gray, MENU[0], scales)
    top_score, tx, ty, ax, ay = max(best_match(gray, name, scales) + (x, y) for name, x, y in TOP_LEFT)
    if menu_score < 0.8 or top_score < 0.8:
        return None
    rate = (my-ty) / (MENU[2] - ay)     # screen pixels per script pixel
    x0, y0 = tx - ax*rate, ty - ay*rate
    width = (mx - x0)/rate - MENU[1]
    return Region(round(x0), round(y0), round(width*rate), round(SCRIPT_H*rate))

def calibrate(frame: np.ndarray) -> str:
    """Measure the game area and store it, unless FGA's rules already match it."""
    h, w = frame.shape[:2]
    measured = measure(frame)
    if measured is None:
        return "MENU or the top-left button not found: open a Terminal screen and try again"
    rules = GameArea(w,h)
    margin = round(rules.margin * rules.rate)  # wide screens keep a margin inside FGA's area
    expected = Region(rules.area.x + margin, rules.area.y, rules.area.w - 2 * margin, rules.area.h)
    stored = json.loads(PATH.read_text()) if PATH.exists() else {}
    key = f"{w / h:.3f}"
    if all(abs(a - b) <= 3 for a, b in zip(vars(measured).values(), vars(expected).values(), strict=True)):
        stored.pop(key, None)
        result = f"the game area matches FGA's rules ({measured}): nothing stored"
    else:
        stored[key] = {"x": measured.x / w, "y": measured.y / h, "w": measured.w / w, "h": measured.h / h}
        result = f"stored the measured game area {measured} (FGA's rules give {expected}); restart the engine"
    PATH.write_text(json.dumps(stored, indent=1))
    return result

def load(w: int, h: int) -> Region | None:
    """The stored game area for this aspect ratio (within 1%: the stream rounds sizes), scaled to w x h."""
    stored = json.loads(PATH.read_text()) if PATH.exists() else {}
    for key, c in stored.items():
        if abs(float(key) - w/h) <= 0.01*w/h:
            return Region(round(c["x"] * w), round(c["y"] * h), round(c["w"] * w), round(c["h"] * h))
    return None