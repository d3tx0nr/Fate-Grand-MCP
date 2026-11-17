from dataclasses import dataclass

SCRIPT_W, SCRIPT_H = 2560, 1440
ULTRA_W = 3360        # on screens wider than 21:9 FGO draws 3360x1440 with side bars
WIDE_MARGIN = 171     # on 18:9 and wider the game keeps this margin left and right (FGA's support header offset)

@dataclass(frozen=True)
class Region:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def moved(self, dx: int, dy: int) ->"Region":
        return Region(self.x + dx, self.y + dy, self.w, self.h)


class GameArea:
    def __init__(self, screen_w: int, screen_h: int, measured: Region | None = None):
        self.screen_w, self.screen_h = screen_w, screen_h
        ratio = screen_w/screen_h
        if measured:
            self.rate = screen_h/SCRIPT_H
            self.area = measured
        else:
            self.rate = min(screen_w/SCRIPT_W, screen_h/SCRIPT_H)
            if ratio > 21/9:
                self.area = self.centered(ULTRA_W)
            elif ratio > 18/9:
                self.area = Region(0, 0, screen_w, screen_h)
            else:
                self.area = self.centered(SCRIPT_W)
        self.script_w = round(self.area.w/self.rate)
        self.script_h = round(self.area.h/self.rate)
        self.is_wide = self.script_w / self.script_h > 18 / 9
        self.margin = WIDE_MARGIN if self.is_wide else 0

    def centered(self, script_w: int) -> Region:
        w, h = round(script_w*self.rate), round(SCRIPT_H*self.rate)
        return Region((self.script_w-w)//2, (self.screen_h-h)//2, w, h)

    # positions measured on 16:9 (script width 2560), placed on this screen
    def x_from_center(self, x: int) -> int:
        return x + self.script_w//2

    def x_from_right(self, x: int) -> int:
        return x + self.script_w

    def nav_left(self, x: int) -> int:
        """A menu position measured from the left edge on 16:9 (menus keep the wide margin)."""
        return x + self.margin

    def nav_right(self, x:int) -> int:
        """A menu position measured from the right edge (negative) on 16:9."""
        return x + self.script_w - self.margin

    def to_screen(self, x: float, y:float) ->tuple[int, int]:
        return self.area.x + round(x*self.rate), self.area.y + round(y*self.rate)

    def region_to_screen(self, r: Region) ->Region:
        x,y = self.to_screen(r.x, r.y)
        return Region(x, y, round(r.w*self.rate), round(r.h*self.rate))