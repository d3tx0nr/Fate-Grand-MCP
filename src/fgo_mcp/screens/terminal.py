import difflib
import json
import time

from fgo_mcp.data import places
from fgo_mcp.device.geometry import Region
from fgo_mcp.engine.state import ActionSpec
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.readers import quests, terminal
from fgo_mcp.screens.base import Screen, int_arg, unknown_keys
from fgo_mcp.screens.navigation import (back_click, center_of, list_swipe, map_pan_from, menu_click,
                                        overlay_close_region, terminal_icon_click)

PAN = {"right": (-700, 0), "left": (700, 0), "down": (0, -450), "up": (0, 450)}
KIND_NAMES = {"home": "Terminal home", "list": "quest list", "map": "chapter map"}

def find_row(rows, number):
    for r in rows:
        if r.row == number:
            return r
    return None

class Terminal(Screen):
    type = "menu"
    budget = 20.0
    budgets = {"go": 150.0}
    guide = ("The Terminal: its home list, quest lists and chapter maps (only the home list is the Terminal home; "
             "home() goes there). go(place) travels to a chapter or list in one call, from wherever you are, e.g. "
             'go("Daily Quests") (fgo_info("locations") lists the places this account has unlocked). On a list or map: '
             "start(row) begins a listed quest, open(name) opens a listed entry or map place, scroll moves the list "
             "or pans the map, back goes up one level, home returns to the Terminal top.")

    def __init__(self):
        self.scrolled = False
        self.scroll_down = True
        self.last_seen = ()

    @staticmethod
    def kind(eng) -> str:
        """home (news button), list (close button), map (terminal button) or other"""
        snap = eng.ctx.battle.snap()
        area = eng.game.ensure_area()
        search = Region(0, 0, area.script_w//3, 200)
        for template_name, kind in (("nav_btn_news", "home"), ("nav_btn_terminal", "map"), ("nav_btn_close", "list")):
            if snap.exists(template_name, search):
                return kind
        return "other"

    def actions(self, eng):
        session = eng.ctx.session
        kind = eng.glob.get("place")
        acts = [ActionSpec("go", "place name (a chapter or list)", '{"place":"Daily Quests"}')]
        if session.quest_rows:
            acts.append(ActionSpec("start", "row as listed (AP shown)", '{"row":1}'))
        if session.nav_entries:
            example = json.dumps({"name": session.nav_entries[0].text.removesuffix(" [NEXT]")})
            acts.append(ActionSpec("open", "name of a listed entry", example))
        if kind == "map":
            acts.append(ActionSpec("scroll", 'direction "up", "down", "left" or "right" (pans the map)', '{"direction":"right"}'))
        else:
            acts.append(ActionSpec("scroll", 'direction "down" or "up"', '{"direction":"down"}'))
        if kind == "list" or kind == "map":
            acts.append(ActionSpec("back", "", "{}"))
        acts.append(ActionSpec("home", "", "{}"))
        return acts

    def read(self, eng):
        game = eng.game
        kind = self.kind(eng)
        if kind == "home":
            title = "Terminal"
        else:
            title = terminal.read_title(game)
        ap = terminal.read_ap(game)
        rows = quests.read_quests(game)
        if kind == "map":
            entries = terminal.read_map_places(game)
        else:
            entries = terminal.read_entries(game)
        if rows:
            # the text of a quest row isnt a separate entry
            kept = []
            for e in entries:
                far_from_rows = True
                for r in rows:
                    if abs(e.tap[1] - r.tap[1]) <= 140:
                        far_from_rows = False
                if far_from_rows:
                    kept.append(e)
            entries = kept
        eng.ctx.session.quest_rows = rows
        eng.ctx.session.nav_entries = entries
        entry_texts = [e.text for e in entries]
        place = places.recognise(title, kind, entry_texts)

        # what the game shows tells us how far the story is
        if place:
            places.learn_current(place)
            if kind == "home" or kind == "list":
                places.learn_banners(place, entry_texts)

        eng.glob["place"] = kind
        eng.glob["location"] = place
        eng.glob["list_title"] = title
        if ap:
            eng.glob["ap"] = f"{ap[0]}/{ap[1]}"
        emit("terminal", place=kind, location=place, title=title, ap=ap, entries=[vars(e) for e in entries],
             frame=save_frame(game.game_image(scale="native"), "menu", kind))

        what = KIND_NAMES.get(kind, "Terminal screen")
        first_line = what
        if kind != "home":
            first_line = f"{what}: {title}"
        if ap:
            first_line += f", AP {ap[0]}/{ap[1]}"
        lines = [first_line]
        if place:
            lines.append(f"Location: {places.trail(place)}")
        position = None
        if kind != "map":
            position = terminal.list_position(game)
        if position:
            lines.append(terminal.position_text(position))
        seen = (kind, title, tuple(r.title for r in rows), tuple(entry_texts))
        at_end = False
        if position is not None:
            if self.scroll_down:
                at_end = position[1] >= 0.97
            else:
                at_end = position[0] <= 0.03
        if self.scrolled and (seen == self.last_seen or at_end):
            if kind == "map":
                lines.append("(the scroll changed nothing: edge of the map in that direction)")
            else:
                lines.append("(the scroll changed nothing: end of the list in that direction)")
        self.scrolled = False
        self.last_seen = seen
        if rows:
            lines.append("Quests:\n" + "\n".join(r.text() for r in rows))
        if entries:
            if kind == "map":
                lines.append("Map places (open by name): " + " | ".join(entry_texts))
            else:
                lines.append("Entries: " + " | ".join(entry_texts))

        next_row = None
        for r in rows:
            if "Next" in r.tags:
                next_row = r
                break
        next_node = None
        for e in entries:
            if e.text.endswith("[NEXT]"):
                next_node = e
                break
        if next_row:
            lines.append(f"Next story quest (marked NEXT by the game): start(row {next_row.row}) {next_row.title}. "
                         "A story quest is played several times in a row; each time continues the story, so the "
                         "same quest can be next again.")
        elif next_node:
            lines.append(f'Next story node (marked NEXT by the game): open("{next_node.text[:-7]}")')
        data = {"place": kind, "location": place, "title": title, "ap": ap, "entries": entry_texts,
                "quests": [vars(r) for r in rows]}
        return "\n".join(lines), data, None

    @staticmethod
    def match_entry(entries, name: str):
        """the one entry a name means: same text, else the only entry with all its words, else a clearly closest one"""
        want = " ".join(places.words(name))
        same = []
        holding = []
        for e in entries:
            if " ".join(places.words(e.text)) == want:
                same.append(e)
            if places.contains(e.text, name):
                holding.append(e)
        if len(same) == 1:
            return same[0]
        if len(holding) == 1:
            return holding[0]
        scored = []
        for e in entries:
            ratio = difflib.SequenceMatcher(None, want, " ".join(places.words(e.text))).ratio()
            scored.append((ratio, e))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored or scored[0][0] < 0.75:
            return None
        if len(scored) == 1 or scored[0][0] - scored[1][0] > 0.1:
            return scored[0][1]
        return None

    def verify(self, eng, action, args):
        problems = []
        session = eng.ctx.session
        if action == "go":
            unknown_keys(args, {"place"}, problems)
            target = places.find(str(args.get("place", "")))
            if target is None:
                problems.append(f"unknown place {args.get('place')!r}; fgo_info(\"locations\") lists the places")
            elif not places.is_available(target):
                problems.append(f"{target} is not unlocked on this account yet (story progress); fgo_info(\"locations\") lists the places you can go")
            elif target == eng.glob.get("location"):
                problems.append(f"already at {target}")
        elif action == "start":
            unknown_keys(args, {"row"}, problems)
            number = int_arg(args, "row", 1, 20, problems)
            pick = None
            if number:
                pick = find_row(session.quest_rows, number)
                if pick is None:
                    problems.append(f"row {number} is not listed; rows: {[r.row for r in session.quest_rows] or 'none'}")
            if pick and pick.locked:
                problems.append(f"Q{number} is locked ({pick.locked})")
        elif action == "open":
            unknown_keys(args, {"name"}, problems)
            if self.match_entry(session.nav_entries, str(args.get("name", ""))) is None:
                listed = " | ".join(e.text for e in session.nav_entries) or "none"
                problems.append(f"no single listed entry matches {args.get('name')!r}; listed: {listed}")
        elif action == "scroll":
            unknown_keys(args, {"direction"}, problems)
            if eng.glob.get("place") == "map":
                allowed = ("down", "up", "left", "right")
            else:
                allowed = ("down", "up")
            if str(args.get("direction", "down")).lower() not in allowed:
                problems.append(f"direction must be one of {list(allowed)}, got {args.get('direction')!r}")
        else:
            unknown_keys(args, set(), problems)
        return problems

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        if action == "go":
            return self.go(eng, places.find(str(args["place"])))
        if action == "start":
            pick = find_row(eng.ctx.session.quest_rows, int(args["row"]))
            if pick.row == 1:
                # first row is where FGA taps
                b.click(b.L.menu_select_quest_click, wait=2.5)
            else:
                b.click(pick.tap, wait=2.5)
            eng.glob["quest"] = pick.title
            emit("quest_selected", quest=vars(pick))
            return f"selected {pick.title} (AP {pick.ap})"
        if action == "open":
            entry = self.match_entry(eng.ctx.session.nav_entries, str(args["name"]))
            b.click(entry.tap, wait=2.5)
            return f"opened {entry.text}"
        if action == "scroll":
            direction = str(args.get("direction", "down")).lower()
            self.scrolled = True
            self.scroll_down = direction == "down"
            if eng.glob.get("place") == "map":
                self.pan_map(eng, direction)
                return f"panned the map {direction}"
            self.scroll_list(eng, down=direction == "down")
            return f"scrolled {direction}"
        if action == "back":
            b.click(back_click(terminal.Anchors(eng.game)), wait=2.0)
            return "went back"
        self.go_home(eng)
        return "returned to the Terminal top"

    @staticmethod
    def scroll_list(eng, down: bool) -> None:
        start, end = list_swipe(terminal.Anchors(eng.game))
        if not down:
            start, end = end, start
        eng.game.swipe(start[0], start[1], end[0], end[1], 600)
        time.sleep(1.2)

    @staticmethod
    def pan_map(eng, direction: str) -> None:
        dx, dy = PAN[direction]
        x, y = map_pan_from(eng.game.ensure_area())
        # the map moves the other way from the finger
        eng.game.swipe(x, y, x + dx, y + dy, 600)
        time.sleep(1.2)

    def go_home(self, eng) -> None:
        b = eng.ctx.battle
        area = terminal.Anchors(eng.game)
        if self.kind(eng) == "home":
            # already home, just scroll to the top
            for _ in range(10):
                position = terminal.list_position(eng.game)
                if position is None or position[0] <= 0.03:
                    return
                self.scroll_list(eng, down=False)
            return
        b.click(menu_click(area), wait=1.2)
        b.click(terminal_icon_click(area), wait=2.5)
        close = b.snap().find("nav_overlay_close", overlay_close_region(area))
        if close:
            # the terminal tap got ignored, close the menu again
            b.click(center_of(close.region), wait=1.0)

    def go(self, eng, target: str) -> str:
        """walks the shortest route. if a banner isnt in a list that step is skipped and it routes again from here"""
        here = eng.glob.get("location")
        skip = set()
        done = []
        for _ in range(12):
            if here == target:
                break
            steps = places.route(here, target, skip)
            if not steps:
                missing = sorted(s[2] for s in skip)
                return self.stopped(done, f"no route to {target} (banners not found: {missing})")
            move, place = steps[0]
            if move == "home":
                self.go_home(eng)
            elif move == "back":
                eng.ctx.battle.click(back_click(terminal.Anchors(eng.game)), wait=2.0)
            elif not self.open_banner(eng, place):
                emit("nav", **{"from": here, "move": move, "to": place, "ok": False, "reason": "banner not found"})
                if place == target:
                    return self.stopped(done, f"{target} is not in the list here (this account may not have it yet)")
                skip.add((here, move, place))
                continue
            if not self.wait_for_terminal(eng):
                emit("nav", **{"from": here, "move": move, "to": place, "ok": False, "reason": "terminal did not come back"})
                return self.stopped(done, f"the Terminal did not come back after {move} {place}")
            kind = self.kind(eng)
            if kind == "home":
                title = "Terminal"
            else:
                title = terminal.read_title(eng.game)
            listed = []
            if kind == "list":
                listed = [e.text for e in terminal.read_entries(eng.game)]
            arrived = places.recognise(title, kind, listed)
            if arrived is None:
                emit("nav", **{"from": here, "move": move, "to": place, "ok": False, "reason": f"unknown screen {title!r}"})
                return self.stopped(done, f"expected {place} after {move}, but the screen shows {title!r}")
            emit("nav", **{"from": here, "move": move, "to": arrived, "ok": arrived == place, "reason": ""})
            if move == "open" and here and arrived != here:
                places.learn_parent(arrived, here)
            if move == "back" and arrived != place:
                # back went somewhere else on this account, remember it and route again from there
                places.learn_parent(here, arrived)
            done.append(arrived)
            here = arrived
        if here != target:
            return self.stopped(done, f"could not reach {target}")
        return f"went to {target} (via {' > '.join(done)})"

    @staticmethod
    def stopped(done: list[str], reason: str) -> str:
        if done:
            return f"NOT DONE: go stopped after {' > '.join(done)}: {reason}"
        return f"NOT DONE: go stopped: {reason}"

    def open_banner(self, eng, place: str) -> bool:
        """scroll the list to the banner and tap it. the known position (at) picks which way to scroll,
        at the end of the list it turns around"""
        info = places.all_places()[place]
        position = terminal.list_position(eng.game)
        down = True
        if info.get("at") is not None and position is not None:
            down = info["at"] > (position[0] + position[1])/2
        turned = False
        for _ in range(16):
            # list keeps drifting after a scroll, wait for it to stop
            eng.ctx.battle.settle(timeout=3.0)
            hit = self.match_entry(terminal.read_entries(eng.game), info["banner"])
            if hit:
                return self.tap_and_leave(eng, hit.tap)
            position = terminal.list_position(eng.game)
            at_end = False
            if position:
                if down:
                    at_end = position[1] >= 0.97
                else:
                    at_end = position[0] <= 0.03
            if at_end:
                if turned:
                    return False
                down = not down
                turned = True
            self.scroll_list(eng, down)
        return False

    def tap_and_leave(self, eng, point) -> bool:
        # tap the banner and wait for the screen to change, one retry since taps get lost sometimes
        before = (self.kind(eng), terminal.read_title(eng.game))
        for _ in range(2):
            eng.ctx.battle.click(point, wait=1.0)
            for _ in range(10):
                if eng.classify() != "menu":
                    return True
                if (self.kind(eng), terminal.read_title(eng.game)) != before:
                    return True
                time.sleep(0.5)
        return False

    @staticmethod
    def wait_for_terminal(eng, timeout: float = 20) -> bool:
        # wait through loading until the terminal shows twice in a row
        end = time.time() + timeout
        seen = 0
        while time.time() < end:
            if eng.classify() == "menu":
                seen += 1
            else:
                seen = 0
            if seen >= 2:
                eng.ctx.battle.settle(timeout=2.0)
                return True
            time.sleep(0.5)
        return False