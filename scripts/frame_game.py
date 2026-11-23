import cv2

from fgo_mcp.device.geometry import GameArea, Region

# acts like device.game.Game but serves a saved frame, so readers and regions can be checked without a phone.
# the saved frames are already just the game area. taps are only recorded
class FrameGame:
    def __init__(self, frame):
        if isinstance(frame, str):
            self.image = cv2.imread(frame)
        else:
            self.image = frame
        h, w = self.image.shape[:2]
        self.area = GameArea(w, h)
        self.taps = []

    def frame(self):
        return self.image

    def ensure_area(self):
        return self.area

    def game_image(self, region: Region | None = None, scale: str = "image"):
        k = self.area.rate
        img = self.image
        if region:
            img = img[round(region.y*k):round(region.bottom*k), round(region.x*k):round(region.right*k)]
        if scale == "image":
            if region:
                w, h = region.w, region.h
            else:
                w, h = self.area.script_w, self.area.script_h
            img = cv2.resize(img, (w//2, h//2), interpolation=cv2.INTER_AREA)
        return img

    def tap(self, x, y):
        self.taps.append((x, y))
        return x, y

    def swipe(self, *args, **kwargs):
        pass