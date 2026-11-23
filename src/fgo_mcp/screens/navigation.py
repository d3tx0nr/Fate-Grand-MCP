from fgo_mcp.device.geometry import Region
from fgo_mcp.engine.state import ActionSpec
from fgo_mcp.readers import ocr
from fgo_mcp.screens.base import Screen, unknown_keys

# small screens outside battle: title, bottom menu, news page and the standard dialog.
# found with our own templates (assets/custom/nav_*.png), FGA has nothing for these.
# positions measured on 16:9 and moved with the wide margin (nav_left / nav_right)

def top_left(a) -> Region:
    # news / close / terminal button
    return Region(a.nav_left(0), 0, 420, 170)

def back_click(a) -> tuple[int, int]:
    return a.nav_left(190), 85

def terminal_icon_click(a) -> tuple[int, int]:
    return a.nav_left(283), 1207

def menu_click(a) -> tuple[int, int]:
    return a.nav_right(-190), 1304

def webview_x_click(a) -> tuple[int, int]:
    return a.nav_right(-71), 68

def list_swipe(a) -> tuple[tuple[int, int], tuple[int, int]]:
    # about one page of rows
    x = a.nav_right(-710)
    return (x, 1200), (x, 350)

def map_pan_from(a) -> tuple[int, int]:
    return a.x_from_center(120), 650

def overlay_close_region(a) -> Region:
    return Region(a.nav_right(-410), 850, 410, 200)

def dialog_close_search(a) -> Region:
    return Region(a.x_from_center(-580), 900, 1160, 500)

def dialog_text(a) -> Region:
    return Region(a.x_from_center(-900), 160, 1800, 900)

def center_of(r: Region) -> tuple[int, int]:
    return r.x + r.w//2, r.y + r.h//2

def read_lines(game, r: Region) -> list:
    lines = ocr.text_boxes(game, r)
    lines.sort(key=lambda b: (round(b[1]/25), b[0]))
    return lines

class NoArgsScreen(Screen):
    def verify(self, eng, action, args):
        problems = []
        unknown_keys(args, set(), problems)
        return problems

class Title(NoArgsScreen):
    type = "title"
    budget = 30.0
    guide = "Title screen. enter starts the game (Data Transfer and Clear Cache are never touched)."

    def actions(self, eng):
        return [ActionSpec("enter", "", "{}")]

    def perform(self, eng, action, args):
        # tap the middle, well above the two buttons at the bottom
        eng.ctx.battle.click((eng.ctx.battle.L.cx(0), 600), wait=3.0)
        return "entered the game (login popups may follow)"

class MenuOverlay(NoArgsScreen):
    type = "menu_overlay"
    guide = "The bottom menu is open. close hides it; terminal goes to the Terminal (quest map)."

    def actions(self, eng):
        return [ActionSpec("close", "", "{}"), ActionSpec("terminal", "", "{}")]

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        a = eng.game.ensure_area()
        if action == "terminal":
            b.click(terminal_icon_click(a), wait=2.0)
            return "opened the Terminal"
        m = b.snap().find("nav_overlay_close", overlay_close_region(a))
        if m:
            b.click(center_of(m.region), wait=1.0)
        else:
            b.click(menu_click(a), wait=1.0)
        return "closed the menu"

class WebView(NoArgsScreen):
    type = "webview"
    guide = "News / notice page. close returns to the game."

    def actions(self, eng):
        return [ActionSpec("close", "", "{}")]

    def perform(self, eng, action, args):
        a = eng.game.ensure_area()
        found = eng.ctx.battle.snap().find("nav_webview_x", Region(a.script_w - 600, 0, 600, 200))
        if found:
            point = center_of(found.region)
        else:
            point = webview_x_click(a)
        eng.ctx.battle.click(point, wait=1.5)
        return "closed the page"

class Dialog(NoArgsScreen):
    type = "dialog"
    guide = "A dialog (often a quest's Strategy Hint: read it, it describes the fight). close dismisses it."

    def actions(self, eng):
        return [ActionSpec("close", "", "{}")]

    def read(self, eng):
        words = []
        for _, _, t in read_lines(eng.game, dialog_text(eng.game.ensure_area())):
            if t.lower() not in ("close", "confirmation"):
                words.append(t)
        text = " ".join(words)
        eng.glob["last_dialog"] = text
        if text:
            return f"Dialog: {text}", {"text": text}, None
        return "Dialog", {"text": text}, None

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        m = b.snap().find("nav_dialog_close", dialog_close_search(eng.game.ensure_area()))
        if not m:
            return "NOT DONE: the Close button was not found"
        b.click(center_of(m.region), wait=1.2)
        return "closed the dialog"

class Notice(NoArgsScreen):
    # one time announcements like "Please Tap the Screen" when the story unlocks an np or skill
    type = "notice"
    guide = "A notice from the game (e.g. a Noble Phantasm or skill unlocked by the story). next continues."

    def actions(self, eng):
        return [ActionSpec("next", "", "{}")]

    def read(self, eng):
        a = eng.game.ensure_area()
        lines = read_lines(eng.game, Region(a.x_from_center(-900), 150, 1800, 1000))
        text = " ".join(t for _, _, t in lines)
        if text:
            return f"Notice: {text}", {"text": text}, None
        return "Notice", {"text": text}, None

    def perform(self, eng, action, args):
        a = eng.game.ensure_area()
        eng.ctx.battle.click((a.x_from_center(0), 1300), wait=2.0)
        return "continued"