import re

import cv2
import numpy as np

from fgo_mcp.path import ASSETS, DATA

# hud numbers (np, hp, enemy hp) read by glyph templates. if any glyph is unsure we return None and
# the caller falls back to ocr. glyphs confirmed by ocr get learned so the set builds itself while playing
BUNDLED = ASSETS / "custom" / "glyphs"
FILE_NAMES = {"%": "pct"}
FORMATS = {"np": r"\d{1,3}%", "hp": r"\d{1,6}", "ehp": r"\d{1,7}"}
N = 20
MIN_SCORE = 0.8
MIN_MARGIN = 0.05
MAX_PER_CHAR = 6
loaded_templates = {}

def glyph_dir(font: str, learned: bool):
    if not learned:
        return BUNDLED / font
    folder = DATA / "glyphs2" / font
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def mask(img: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 1] < 80) & (hsv[..., 2] > 150)).astype(np.uint8)
    # remove long horizontal lines (np bar), digits are never that wide
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(int(m.shape[0]*0.6), 3), 1))
    lines = cv2.morphologyEx(m, cv2.MORPH_OPEN, kernel)
    return m & ~lines

def components(m: np.ndarray) -> list[list[int]]:
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    comps = []
    for i in range(1, n):
        if stats[i][4] >= 6:
            x, y, w, h = stats[i][:4]
            comps.append([int(x), int(y), int(w), int(h)])
    return comps

def segments(img: np.ndarray) -> tuple[list[list[int]], np.ndarray]:
    """glyph boxes [x, y, w, h] from left to right and the band mask they were cut from"""
    m = mask(img)
    comps = []
    for c in components(m):
        if c[3] >= 8 and c[2] <= 1.3*c[3]:
            comps.append(c)
    fitting = []
    for c in comps:
        if c[3] <= 0.7*m.shape[0]:
            fitting.append(c)
    if len(fitting) == 0:
        fitting = comps
    if len(fitting) == 0:
        return [], m

    tallest = 0
    for c in fitting:
        tallest = max(tallest, c[3])
    tops = []
    bottoms = []
    for c in fitting:
        if c[3] >= 0.75*tallest:
            tops.append(c[1])
            bottoms.append(c[1] + c[3])
    y0 = int(np.median(tops))
    y1 = int(np.median(bottoms))
    band = np.zeros_like(m)
    band[max(y0-1, 0):y1+1] = m[max(y0-1, 0):y1+1]
    band_h = max(y1 - y0, 1)

    pieces = []
    for c in components(band):
        if c[3] >= 0.35*band_h:
            pieces.append(c)
    pieces.sort(key=lambda c: c[0])

    boxes = []
    for c in pieces:
        if len(boxes) > 0:
            # glue broken pieces of the same glyph
            prev = boxes[-1]
            x0 = min(prev[0], c[0])
            x1 = max(prev[0] + prev[2], c[0] + c[2])
            gap = c[0] - (prev[0] + prev[2])
            if gap < 0 or (gap <= 1 and x1 - x0 <= 0.95*band_h):
                ya = min(prev[1], c[1])
                yb = max(prev[1] + prev[3], c[1] + c[3])
                boxes[-1] = [x0, ya, x1 - x0, yb - ya]
                continue
        boxes.append(c)

    result = []
    for b in boxes:
        if b[3] >= 0.7*band_h and b[2] <= band_h:
            result.append(b)
    return result, band

def normalise(glyph: np.ndarray) -> np.ndarray:
    h, w = glyph.shape
    size = max(h, w)
    square = np.zeros((size, size), np.float32)
    top = (size - h)//2
    left = (size - w)//2
    square[top:top+h, left:left+w] = glyph > 0
    v = cv2.resize(square, (N, N), interpolation=cv2.INTER_AREA).ravel()
    return v / (np.linalg.norm(v) + 1e-6)

def char_from_file(stem: str) -> str:
    name = stem.split("_")[0]
    for ch, file_name in FILE_NAMES.items():
        if file_name == name:
            return ch
    return name

def templates(font: str) -> list[tuple[str, np.ndarray, float]]:
    """(char, normalised mask, w/h) of every glyph saved for this font"""
    if font in loaded_templates:
        return loaded_templates[font]
    found = []
    for folder in (glyph_dir(font, False), glyph_dir(font, True)):
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.png")):
            g = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if g is None or g.size == 0:
                continue
            found.append((char_from_file(p.stem), normalise(g), g.shape[1]/g.shape[0]))
    loaded_templates[font] = found
    return found

def trusted(font: str) -> bool:
    have = set()
    for ch, _, _ in templates(font):
        have.add(ch)
    for digit in "0123456789":
        if digit not in have:
            return False
    return True

def classify(v: np.ndarray, aspect: float, tpls) -> tuple[str, float, float]:
    best = {}
    for ch, t, t_aspect in tpls:
        score = float(v @ t) - 0.5*abs(aspect - t_aspect)
        if ch not in best or score > best[ch]:
            best[ch] = score
    ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
    second = -9.0
    if len(ranked) > 1:
        second = ranked[1][1]
    return ranked[0][0], ranked[0][1], ranked[0][1] - second

def read(crop: np.ndarray, font: str) -> str | None:
    tpls = templates(font)
    if not trusted(font):
        return None
    boxes, band = segments(crop)
    chars = []
    for x, y, w, h in boxes:
        ch, score, margin = classify(normalise(band[y:y+h, x:x+w]), w/h, tpls)
        if score < MIN_SCORE or margin < MIN_MARGIN:
            return None
        chars.append(ch)
    text = "".join(chars)
    if re.fullmatch(FORMATS[font], text):
        return text
    return None

def learn(crop: np.ndarray, font: str, text: str) -> int:
    """store glyphs of a value ocr already confirmed (e.g. "70%")"""
    boxes, band = segments(crop)
    if len(boxes) != len(text):
        return 0
    tpls = templates(font)
    sure = trusted(font)
    count_per_char = {}
    for ch, _, _ in tpls:
        count_per_char[ch] = count_per_char.get(ch, 0) + 1
    added = 0
    for i in range(len(text)):
        ch = text[i]
        x, y, w, h = boxes[i]
        if count_per_char.get(ch, 0) >= MAX_PER_CHAR:
            continue
        if sure:
            # templates confidently see another char: ocr was probably wrong, dont learn anything
            seen, score, _ = classify(normalise(band[y:y+h, x:x+w]), w/h, tpls)
            if seen != ch and score >= MIN_SCORE:
                return added
        name = FILE_NAMES.get(ch, ch)
        folder = glyph_dir(font, True)
        n = len(list(folder.glob(f"{name}_*.png")))
        cv2.imwrite(str(folder / f"{name}_{n}.png"), band[y:y+h, x:x+w] * 255)
        count_per_char[ch] = count_per_char.get(ch, 0) + 1
        added += 1
    if added > 0:
        loaded_templates.pop(font, None)
    return added