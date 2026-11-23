from collections import Counter
from collections.abc import Callable
from functools import cache

import cv2
import numpy as np

@cache
def engine():
    from rapidocr import RapidOCR

    return RapidOCR()

def _prep(img: np.ndarray) -> np.ndarray:
    """The text detector needs margin, and small HUD crops read better upscaled."""
    if img.shape[0] < 80:
        img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(img, 16, 16, 16, 16, cv2.BORDER_REPLICATE)

def text(img: np.ndarray) -> list[tuple[str, float]]:
    """(text, score) per detected line. RapidOCR keeps call options between calls, so pass them always."""
    r = engine()(_prep(img), use_det=True, use_cls=True)
    return list(zip(r.txts or (), r.scores or (), strict=False))

def boxes(img: np.ndarray, pad: int = 16) -> list[tuple[float, float, str]]:
    """(x, y, text) per detected line, top-left corner in the padded image."""
    r = engine()(cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE), use_det=True, use_cls=True)
    if r.boxes is None or r.txts is None:
        return []
    return [(min(p[0] for p in b), min(p[1] for p in b), t) for b, t in zip(r.boxes, r.txts, strict=False)]

def rects(img: np.ndarray, pad: int = 0) -> list[tuple[float, float, float, float, str]]:
    """(x, y, w, h, text) per detected line, in the (padded) image."""
    r = engine()(cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE), use_det=True, use_cls=True)
    if r.boxes is None or r.txts is None:
        return []
    out = []
    for b, t in zip(r.boxes, r.txts, strict=False):
        xs, ys = [p[0] for p in b], [p[1] for p in b]
        out.append((min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys), t))
    return out

def line(img: np.ndarray) -> str:
    """Text joined left to right (the detector does not return words in reading order)."""
    r = engine()(_prep(img), use_det=True, use_cls=True)
    if r.txts is None:
        return ""
    items = sorted(zip(r.boxes, r.txts, strict=False), key=lambda bt: min(p[0] for p in bt[0]))
    return " ".join(t for _, t in items)

def text_boxes(game, region) -> list[tuple[float, float, str]]:
    """(x, y, text) per detected line inside a script-space region of the game, in script coordinates."""
    k = game.ensure_area().rate
    found = boxes(game.game_image(region, scale="native"), pad=0)
    return [(x / k + region.x, y / k + region.y, t.strip()) for x, y, t in found]

def vote(img: np.ndarray, parse: Callable[[str], object],
         variants=((1, 0), (1.5, 0), (2, 0), (1.5, 16)), det_first: bool = False, min_agree: int | None = 2):
    """Most common valid parse over a few scale/padding variants (or None).

    Small stylised HUD digits defeat any single preprocessing, but the majority is reliable.
    Single-line crops go through recognition only first (~50x faster), detection as the fallback.
    det_first: for lines of several separate numbers (e.g. skill levels "10 10 10"), where recognition-only
    glues them together; min_agree=None runs every variant and takes the full majority.
    """
    votes: Counter = Counter()
    for use_det in ((True, False) if det_first else (False, True)):
        for sc, pad in variants:
            v = img if sc == 1 else cv2.resize(img, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC)
            if pad:
                v = cv2.copyMakeBorder(v, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
            r = engine()(v, use_det=use_det, use_cls=False)
            val = parse(" ".join(r.txts or []))
            if val is not None:
                votes[val] += 1
                if min_agree and votes[val] >= min_agree:
                    return val
        if votes and min_agree:
            break
    return votes.most_common(1)[0][0] if votes else None