from functools import cache

import cv2
import numpy as np

from fgo_mcp.path import ASSETS, DATA

# crit chance on command cards ("40%"), always <d>0% or 100% so only first digit matters
BUNDLED = ASSETS / "custom" / "digits"
SIZE = (20, 30)
MIN_SCORE = 0.75

def learned_dir():
    folder = DATA / "digits"
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def mask(crop: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    silver = (hsv[..., 1] < 40) & (hsv[..., 2] > 120)
    return silver.astype(np.uint8) * 255

def glyphs(crop: np.ndarray) -> list[np.ndarray]:
    m = mask(crop)
    h = m.shape[0]
    n, _, stats, _ = cv2.connectedComponentsWithStats(m)
    boxes = []
    for i in range(1, n):
        x, y, w, gh, area = stats[i]
        # skip the % sign and small specks
        if y > 3 and 0.5*h <= gh <= 0.75*h and 8 <= w <= 0.3*m.shape[1] and area >= 150:
            boxes.append((x, y, w, gh))
    boxes.sort()
    result = []
    for x, y, w, gh in boxes:
        result.append(cv2.resize(m[y:y+gh, x:x+w], SIZE, interpolation=cv2.INTER_AREA))
    return result

@cache
def bundled_templates() -> tuple:
    found = []
    for p in sorted(BUNDLED.glob("*.png")):
        found.append((p.name[0], cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)))
    return tuple(found)

def templates() -> list[tuple[str, np.ndarray]]:
    all_templates = list(bundled_templates())
    for p in sorted(learned_dir().glob("*.png")):
        all_templates.append((p.name[0], cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)))
    return all_templates

def classify(g: np.ndarray) -> tuple[str | None, float]:
    best_score = -1.0
    best_digit = None
    for digit, t in templates():
        score = float(cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)[0][0])
        if score > best_score:
            best_score = score
            best_digit = digit
    if best_score >= MIN_SCORE:
        return best_digit, best_score
    return None, best_score

def read(crop: np.ndarray) -> int | None:
    """0 when nothing is printed, None if not sure"""
    gs = glyphs(crop)
    if len(gs) == 0:
        return 0
    digits = []
    for g in gs:
        digit = classify(g)[0]
        if digit is None:
            return None
        digits.append(digit)
    s = "".join(digits)
    if s == "100":
        return 100
    if len(s) == 2 and s[1] == "0" and s[0] != "0":
        return int(s)
    return None

def learn(crop: np.ndarray, value: int) -> int:
    """save glyphs of a reading confirmed by the star count, returns how many new ones"""
    gs = glyphs(crop)
    text = str(value)
    if value == 0 or len(gs) != len(text):
        return 0
    added = 0
    for i in range(len(text)):
        digit = text[i]
        if classify(gs[i])[0] == digit:
            continue
        count = len(list(learned_dir().glob(f"{digit}_*.png")))
        cv2.imwrite(str(learned_dir() / f"{digit}_{count}.png"), gs[i])
        added += 1
    return added