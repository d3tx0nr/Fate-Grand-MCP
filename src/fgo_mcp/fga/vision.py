"""Template matching like FGA's DroidCvPattern: 720p grayscale, TM_CCOEFF_NORMED, min similarity 0.8."""

from dataclasses import dataclass
from functools import cache

import cv2
import numpy as np

from fgo_mcp.device.geometry import Region
from fgo_mcp.path import ASSETS

FGA_ASSETS = ASSETS / "fga_en"
CUSTOM_ASSETS = ASSETS / "custom"
MIN_SIMILARITY = 0.8

@cache
def template(name: str) -> np.ndarray:
    path = CUSTOM_ASSETS / f"{name}.png"
    fga_path = FGA_ASSETS / f"{name}.png"
    img = cv2.imread(str(path if path.exists() else fga_path), cv2.IMREAD_UNCHANGED)
    if img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
    elif img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img

@dataclass
class Match:
    region: Region
    score: float

class Snapshot:
    def __init__(self, bgr720: np.ndarray):
        self.bgr = bgr720
        self.gray = cv2.cvtColor(bgr720, cv2.COLOR_BGR2GRAY)

    def crop(self, r: Region) -> np.ndarray:
        h, w = self.gray.shape
        x0, y0 = max(r.x//2, 0), max(r.y//2, 0)
        x1, y1 = min((r.x+r.w)//2, w), min((r.y+r.h)//2, h)
        return self.gray[y0:y1, x0:x1]

    def is_black(self, r: Region) -> bool:
        """FGA DroidCvPattern.isBlack: brightest pixel <= 55."""
        c = self.crop(r)
        return c.size > 0 and int(c.max()) <=55

    def is_white(self, r: Region) -> bool:
        """FGA DroidCvPattern.isWhite: darkest pixel >= 200."""
        c = self.crop(r)
        return c.size > 0 and int(c.min()) >= 200

    def best(self, name: str, r: Region) -> Match | None:
        """Best match of a template inside a script-space region (regardless of threshold)."""
        tpl = template(name)
        hay = self.crop(r)
        if hay.shape[0] < tpl.shape[0] or hay.shape[1] < tpl.shape[1]:
            return None
        res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, (mx, my) = cv2.minMaxLoc(res)
        x0, y0 = max(r.x //2, 0), max(r.y//2, 0)
        tw, th = tpl.shape
        return Match(Region((x0+mx)*2, (y0+my)*2, tw*2, th*2), float(score))

    def find(self, name: str, r: Region, similarity: float = MIN_SIMILARITY) -> Match | None:
        m = self.best(name, r)
        return m if m and m.score >= similarity else None

    def exists(self, name: str, r: Region, similarity: float= MIN_SIMILARITY) -> bool:
        return self.find(name, r, similarity) is not None

    def find_all(self, name: str, r: Region, similarity: float = MIN_SIMILARITY, limit: int = 20) ->list[Match]:
        """FGA findAll: every match above `similarity`, best first, suppressing overlaps."""
        tpl = template(name)
        hay = self.crop(r)
        if hay.shape[0] < tpl.shape[0] or hay.shape[1] < tpl.shape[1]:
            return []
        res = cv2.matchTemplate(hay, tpl, cv2.TM_CCOEFF_NORMED)
        x0, y0 = max(r.x //2, 0), max(r.y//2, 0)
        tw, th = tpl.shape
        out: list[Match] = []
        while len(out) < limit:
            _, score, _, (mx, my) = cv2.minMaxLoc(res) 
            if score < similarity:
                break
            out.append(Match(Region((x0+mx)*2, (y0+my)*2, tw*2, th*2), float(score)))
            res[max(my - th // 2, 0):my + th // 2 + 1, max(mx - tw // 2, 0):mx + tw // 2 + 1] = -1
        return out

    def score(self, name: str, r: Region) -> float:
        m = self.best(name, r)
        return round(m.score, 3) if m else 0.0