import time

import cv2

from fgo_mcp.data import atlas
from fgo_mcp.device.geometry import Region
from fgo_mcp.engine.state import ActionSpec
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.readers import ocr, party_screen, support_list
from fgo_mcp.screens.base import Screen, int_arg, unknown_keys, SeenSoFar
from fgo_mcp.screens.navigation import back_click

REFRESH_THROTTLE_S = 10   # game only lets you refresh the list every 10s

def selected_quest(eng) -> str:
    quest = eng.glob.get("quest")
    if quest:
        return f"Quest being started: {quest} (start begins it; back returns to the quest list)\n"
    return ""

def find_row(rows, number):
    for r in rows:
        if r.row == number:
            return r
    return None

MAX_ENTRY_SCROLLS = 15   # support lists are about 10 screens long at most

class Support(Screen):
    type = "support"
    budget = 20.0
    guide = ("Choose a support servant: pick(row) for a row on screen (R1, R2...), or pick(entry) for an entry of "
             "the numbered seen-so-far list (#1 is the first in the list; the engine scrolls back to it). scroll "
             'shows more ("down", or "up"), top goes back up, refresh updates the list (at most every 10 s), '
             "class_tab filters (all, saber, archer, lancer, rider, caster, assassin, berserker, extra, mix). back "
             "returns to the quest list.")

    def __init__(self):
        self.seen = SeenSoFar()

    def actions(self, eng):
        return [ActionSpec("pick", "row on screen, or entry number of the seen-so-far list", '{"row":1} or {"entry":5}'),
                ActionSpec("scroll", 'direction "down" (default) or "up"', '{"direction":"down"}'),
                ActionSpec("top", "", "{}"), ActionSpec("refresh", "", "{}"),
                ActionSpec("class_tab", "name", '{"name":"caster"}'), ActionSpec("back", "", "{}")]

    def read(self, eng):
        b = eng.ctx.battle

        def list_loaded(snap):
            return support_list.loaded(snap, b.L)

        b.wait_until(list_loaded, 6)
        s = b.snap()
        state = support_list.list_state(s, b.L)
        rows = []
        if not state["empty"]:
            rows = support_list.read(eng.game, s, b.L)
        eng.ctx.session.support_rows = rows
        rows_data = []
        for r in rows:
            row_dict = dict(vars(r))
            row_dict["bounds"] = vars(r.bounds)
            rows_data.append(row_dict)
        emit("support_list", rows=rows_data, state=state, frame=save_frame(eng.game.game_image(scale="native"), "support", "list"))
        if state["empty"]:
            text = "No supports in this list (try refresh or another class_tab)."
        else:
            row_lines = [r.text() for r in rows]
            text = "Supports:\n" + ("\n".join(row_lines) or "none recognised")
            if state["at_top"]:
                position = "top"
            elif state["at_bottom"]:
                position = "bottom"
            else:
                position = "middle"
            tab = support_list.selected_tab(eng.game, b.L) or eng.glob.get("support_tab", "as the game shows")
            text += f"\nList position: {position}; class tab: {tab}"
            # one list = this quest, this class tab, this refresh
            key = (eng.glob.get("quest"), tab, eng.glob.get("support_refreshed_at"))
            items = []
            for r in rows:
                if r.complete and r.name:
                    # one entry per player and servant: the same servant from two players is two entries
                    items.append((r.identity, r.text().split(" ", 1)[1]))
            self.seen.update(eng, key, items, self.type)
            complete = sum(1 for r in rows if r.complete)
            seen_text = self.seen.text("on this support list", complete, at_end=state["at_bottom"], numbered=True)
            if seen_text:
                text += "\n" + seen_text
        text = selected_quest(eng) + text
        data = {"rows": [r.text() for r in rows]}
        data.update(state)
        return text, data, None

    def verify(self, eng, action, args):
        problems = []
        rows = eng.ctx.session.support_rows
        if action == "pick" and "entry" in args:
            unknown_keys(args, {"entry"}, problems)
            int_arg(args, "entry", 1, max(len(self.seen.items), 1), problems)
            if not self.seen.items:
                problems.append("no seen-so-far list yet: pick(row) a row on screen, or scroll first")
        elif action == "pick":
            unknown_keys(args, {"row"}, problems)
            number = int_arg(args, "row", 1, 10, problems)
            if number:
                r = find_row(rows, number)
                if r is None:
                    problems.append(f"row {number} is not listed; rows: {[x.row for x in rows] or 'none'}")
                elif not r.complete:
                    problems.append(f"row {number} is only partly on screen; scroll first")
        elif action == "class_tab":
            unknown_keys(args, {"name"}, problems)
            name = str(args.get("name", "")).strip().lower()
            if name not in support_list.CLASS_TABS:
                problems.append(f"name must be one of {list(support_list.CLASS_TABS)}, got {args.get('name')!r}")
        elif action == "refresh":
            unknown_keys(args, set(), problems)
            wait = REFRESH_THROTTLE_S - (time.time() - eng.glob.get("support_refreshed_at", 0))
            if wait > 0:
                problems.append(f"the list can be refreshed again in {wait:.0f} s (game limit)")
        elif action == "scroll":
            unknown_keys(args, {"direction"}, problems)
            if str(args.get("direction", "down")).lower() not in ("down", "up"):
                problems.append(f'direction must be "down" or "up", got {args.get("direction")!r}')
        else:
            unknown_keys(args, set(), problems)
        return problems

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        L = b.L
        if action == "pick" and "entry" in args:
            return self.pick_entry(eng, int(args["entry"]))
        if action == "pick":
            return self.pick_row(eng, find_row(eng.ctx.session.support_rows, int(args["row"])))
        if action == "scroll":
            direction = str(args.get("direction", "down")).lower()
            self.swipe(eng, up=direction == "up")
            return f"scrolled {direction}"
        if action == "back":
            b.click(back_click(eng.game.ensure_area()), wait=2.0)
            return "went back"
        if action == "top":
            b.click(L.support_list_top_click, wait=1.0)
            return "back to the top of the list"
        if action == "refresh":
            b.click(L.support_update_click, wait=1.0)
            b.click(L.support_update_yes_click, wait=2.5)
            eng.glob["support_refreshed_at"] = time.time()
            return "refreshed the support list"
        name = str(args["name"]).strip().lower()
        b.click(L.support_class_tab(support_list.class_tabs(eng.game, L)[name]), wait=1.5)
        eng.glob["support_tab"] = name
        return f"showing class tab {name}"

    @staticmethod
    def swipe(eng, up: bool = False) -> None:
        start, end = eng.ctx.battle.L.support_swipe
        if up:
            start, end = end, start
        eng.game.swipe(start[0], start[1], end[0], end[1], 700)
        time.sleep(1.2)

    def pick_entry(self, eng, number: int) -> str:
        """finds entry #number of the seen-so-far list again (same player and servant) and picks it"""
        b = eng.ctx.battle
        wanted, shown = self.seen.items[number - 1]

        def find():
            s = b.settle(timeout=2.0)
            for r in support_list.read(eng.game, s, b.L):
                if r.complete and r.name and self.seen.same(r.identity, wanted):
                    return r
            return None

        hit = find()
        if hit is None:
            b.click(b.L.support_list_top_click, wait=1.0)
            for _ in range(MAX_ENTRY_SCROLLS):
                hit = find()
                if hit is not None or support_list.list_state(b.snap(), b.L)["at_bottom"]:
                    break
                self.swipe(eng)
        if hit is None:
            return f"NOT DONE: entry #{number} ({shown}) was not found again; the list may have changed (refresh?)"
        return self.pick_row(eng, hit)

    def pick_row(self, eng, r) -> str:
        b = eng.ctx.battle
        all_skills_read = True
        for v in r.skills:
            if v is None:
                all_skills_read = False
        # who it is, for the battle (its name strip can be unreadable)
        eng.ctx.session.support_from_list = r.atlas_id
        eng.ctx.session.support_levels_from_list = bool(r.atlas_id and all_skills_read)
        if r.atlas_id and all_skills_read:
            # levels from the list are cleaner than reading them again in battle
            svt = atlas.get(r.atlas_id)
            eng.ctx.roster.seen(r.atlas_id, "support", svt["name"], svt["className"])
            eng.ctx.roster.set_skills(r.atlas_id, "support", list(r.skills), np_level=r.np_level)
        eng.ctx.session.support_name = r.name
        eng.glob["support"] = r.name
        emit("support_selected", row=r.text())
        b.click(r.tap, wait=2.0)
        return f"picked support R{r.row}: {r.name or '?'}"

def selected_dot(game, L) -> int | None:
    # the selected party has the one yellow dot out of 15. checking colour because in grayscale
    # (FGA's SelectedParty template) yellow and gray look the same on the compressed stream
    scores = []
    for i in range(15):
        x, y = L.party_dot(i)
        hsv = cv2.cvtColor(game.game_image(Region(x - 8, y - 8, 16, 16)), cv2.COLOR_BGR2HSV)
        yellow = (hsv[..., 0] > 15) & (hsv[..., 0] < 40) & (hsv[..., 1] > 110) & (hsv[..., 2] > 130)
        scores.append(float(yellow.mean()))
    best = 0
    for i in range(15):
        if scores[i] > scores[best]:
            best = i
    if scores[best] > 0.3:
        return best + 1
    return None

class Party(Screen):
    type = "party"
    budget = 30.0
    guide = ("Party screen before the battle. select_party(n) switches to saved party n (members and CEs are "
             "not edited); start begins the quest; back returns to the support list.")

    def actions(self, eng):
        return [ActionSpec("select_party", "party 1-15 (dots above the party name)", '{"party":2}'),
                ActionSpec("start", "", "{}"), ActionSpec("back", "", "{}")]

    @staticmethod
    def current(eng) -> int | None:
        return selected_dot(eng.game, eng.ctx.battle.L)

    def read(self, eng):
        # members slide in and the screen ignores taps until they're done
        eng.ctx.battle.settle(timeout=3.0, threshold=2.0)
        head_region = Region(eng.game.ensure_area().x_from_center(-580), 30, 1200, 90)
        header = " ".join(t for _, _, t in ocr.text_boxes(eng.game, head_region)).upper()
        special = "SPECIAL" in header
        eng.glob["special_party"] = special
        current = None
        if not special:
            current = self.current(eng)
        title, members = party_screen.read_party(eng.game, eng.ctx.roster)
        end = time.monotonic() + 8
        while len(members) < 6 and time.monotonic() < end:
            time.sleep(1.0)
            title, members = party_screen.read_party(eng.game, eng.ctx.roster)
        support_name = eng.ctx.session.support_name
        for m in members:
            if m.support and support_name:
                m.name = support_name
        eng.glob["party"] = current
        members_data = [vars(m) for m in members]
        emit("party_screen", party=current, title=title, members=members_data,
             frame=save_frame(eng.game.game_image(scale="native"), "party", "members"))
        if special:
            head = "Special party (fixed by this quest; cannot be switched)"
        else:
            head = f"Selected party: {current or '?'}"
        text = selected_quest(eng) + head + "\n" + party_screen.party_text(title, members)
        return text, {"party": current, "members": members_data}, None

    def verify(self, eng, action, args):
        problems = []
        if action == "select_party":
            if eng.glob.get("special_party"):
                return ["this quest uses a special party (fixed by the quest); it cannot be switched"]
            unknown_keys(args, {"party"}, problems)
            n = int_arg(args, "party", 1, 15, problems)
            if n and n == eng.glob.get("party"):
                problems.append(f"party {n} is already selected")
        else:
            unknown_keys(args, set(), problems)
        return problems

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        L = b.L
        if action == "back":
            b.click(back_click(eng.game.ensure_area()), wait=2.0)
            return "went back"
        if action == "start":
            b.click(L.menu_start_quest_click, wait=2.0)
            # new battle, nothing should carry over from the last one, except who was just picked as support
            session = eng.ctx.session
            picked = (session.support_from_list, getattr(session, "support_levels_from_list", False))
            eng.ctx.end_battle()
            session.support_from_list, session.support_levels_from_list = picked
            return "started the quest"
        n = int(args["party"])
        if self.current(eng) is None:
            # like FGA: if we dont know the current party switch away first, tapping the same party freezes start
            if n != 1:
                b.click(L.party_dot(0), wait=1.0)
            else:
                b.click(L.party_dot(1), wait=1.0)
        b.click(L.party_dot(n - 1), wait=1.2)
        return f"switched to party {n}"