import cv2
import numpy as np

from fgo_mcp.device.scrcpy import ScrcpyStream
from fgo_mcp.device.geometry import Region, GameArea
from fgo_mcp.log import record

class Game:
    def __init__(self):
        self.stream = ScrcpyStream()
        self.area: GameArea | None = None
        self.measured_area = None   # (w, h) -> Region, after region caliberation is implemented

    def frame(self) -> np.ndarray:
        self.stream.start()
        frame = self.stream.latest()
        h, w = frame.shape[:2]
        if self.area is None or (self.area.screen_w, self.area.screen_h) != (w,h):
            measured = self.measured_area(w, h) if self.measured_area else None
            self.area = GameArea(w, h, measured)
        return frame

    def ensure_area(self) -> GameArea:
        if self.area is None:
            self.frame()
        return self.area

    def game_image(self, region: Region | None = None, scale: str = "image") ->np.ndarray:
        """The game area, or a region of it. scale="image": 720p pixels (for templates); "native": screen pixels."""
        frame = self.frame()
        a = self.area
        if region is not None:
            record.note_crop(region)
        r = a.region_to_screen(region) if region else a.area
        crop = frame[max(r.y, 0):r.bottom, max(r.x, 0): r.right]
        if scale == "image":
            w, h = (region.w, region.h) if region else (a.script_w, a.script_h)
            crop = cv2.resize(crop, (max(w//2, 1), max(h//2, 1)), interpolation=cv2.INTER_AREA)
        return crop

    def tap(self, x: int, y: int) -> tuple[int, int]:
        record.note_tap(self, x, y)
        sx, sy = self.ensure_area().to_screen(x, y)
        self.stream.tap(sx, sy)
        return sx, sy

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        record.note_swipe(self, x1, y1, x2, y2)
        a = self.ensure_area()
        self.stream.swipe(*a.to_screen(x1, y1), *a.to_screen(x2, y2), duration_ms)