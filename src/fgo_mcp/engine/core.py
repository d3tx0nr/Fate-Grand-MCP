import json
import threading
import time
import traceback

from fgo_mcp.battle import details
from fgo_mcp.battle.context import BattleContext
from fgo_mcp.data import atlas, kits, places
from fgo_mcp.device.adb import ADBError
from fgo_mcp.device.game import Game
from fgo_mcp.engine.state import ScreenState
from fgo_mcp.fga import calibrate
from fgo_mcp.fga.screen import classify
from fgo_mcp.log import record
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.path import DATA
from fgo_mcp.screens.battle_checks import find_servant
from fgo_mcp.screens.registry import FGA_TO_TYPE, SCREENS

STABLE_READS = 2          # same screen type this many times in a row before we trust it
TICK_S = 0.5
UNKNOWN_HELP_S = 20
LOADING_HELP_S = 300      # downloads on a new account can take minutes
OUT_OF_BATTLE = {"results", "repeat", "menu", "support", "party"}
TRANSIENT = {"battle_busy"}
AUTO_TAP_S = 1.0
GLOBAL_PATH = DATA / "engine_global.json"

class Engine:
    """watches the screen and builds a ScreenState once per stable screen, then checks and does actions.
    one lock for the device and the state"""
    def __init__(self) -> None:
        self.game = Game()
        self.game.measured_area = calibrate.load
        self.ctx = BattleContext(self.game)
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.state = None
        self.dirty = True
        self.seen_count = (None, 0)
        self.stop_event = threading.Event()
        self.thread = None
        self.last_error = ""
        self.last_auto_tap = 0.0
        self.fine = "unknown"     # FGA's name for the last frame, like "bond" inside "results"
        self.creator_note = None  # set by an action that found the creator is needed, shown on the next screen
        self.last_saved_fine = None
        self.device_connected = None
        self.withdrawn = False
        self.glob = {}
        if GLOBAL_PATH.exists():
            self.glob = json.loads(GLOBAL_PATH.read_text())
        # ids keep going up across restarts
        self.seq = int(self.glob.get("last_screen_id", 0))

    def classify_fine(self) -> str:
        record.set_stage("classify")
        try:
            self.fine = classify(self.ctx.battle.snap(), self.ctx.battle.L)
        finally:
            record.set_stage("read")
        return self.fine

    def classify(self) -> str:
        t = FGA_TO_TYPE.get(self.classify_fine(), "unknown")
        in_battle = self.ctx.in_battle()
        if t == "story" and in_battle:
            # like FGA, no story skip in battle, wave changes look the same
            t = "unknown"
        if t == "unknown" and in_battle:
            return "battle_busy"
        return t

    def between_waves_tap(self) -> None:
        # same as FGA: tap the middle on the black screen between waves and during death animations
        if time.time() - self.last_auto_tap < AUTO_TAP_S:
            return
        b = self.ctx.battle
        s = b.snap()
        black = s.is_black(b.L.np_started_region)
        alive = 0
        for present in b.servants_present(s):
            if present:
                alive += 1
        dying = 1 <= alive <= 2
        if not black and not dying:
            return
        x, y = b.L.battle_safe_middle_click
        self.game.tap(x, y)
        self.last_auto_tap = time.time()
        if black:
            reason = "between waves"
        else:
            reason = "death animation"
        emit("tap", x=x, y=y, source="auto", reason=reason)

    def state_type(self) -> str | None:
        if self.state:
            return self.state.type
        return None

    def tick(self) -> None:
        with self.lock:
            # boxes from checks that dont end in a new screen are not kept
            record.begin()
            t = self.classify()
            record.set_screen(t)
            if self.device_connected is not True:
                self.device_connected = True
                emit("device", status="connected", size=self.game.stream.size)
            if self.fine == "withdraw":
                self.withdrawn = True
            if self.fine != self.last_saved_fine:
                # keep a frame of every change of screen for checking later
                self.last_saved_fine = self.fine
                save_frame(self.game.game_image(scale="native"), self.fine, "seen")
            if t == "battle_busy":
                self.between_waves_tap()
            previous, count = self.seen_count
            if t == previous:
                count += 1
            else:
                count = 1
            self.seen_count = (t, count)
            if count >= STABLE_READS and (self.dirty or self.state is None or self.state.type != t):
                self.build(t)
            elif t in ("unknown", "loading") and self.state and self.state.type == t and not self.state.needs_creator:
                self.check_stuck(t)

    def check_stuck(self, t: str) -> None:
        if t == "unknown":
            limit = UNKNOWN_HELP_S
        else:
            limit = LOADING_HELP_S
        if time.time() - self.state.t <= limit:
            return
        if t == "unknown":
            self.state.needs_creator = f"unrecognised screen for over {limit} s"
        else:
            self.state.needs_creator = f"still loading after {limit} s (connection?)"
        emit("needs_creator", id=self.state.id, screen_kind=t, reason=self.state.needs_creator,
             frame=save_frame(self.game.game_image(scale="native"), "needs_creator", t))
        self.cond.notify_all()

    def build(self, t: str) -> None:
        if t in OUT_OF_BATTLE and self.ctx.in_battle():
            if t == "results":
                self.glob["runs_won"] = self.glob.get("runs_won", 0) + 1
                result = "won"
            elif self.withdrawn:
                result = "withdrawn"
            else:
                result = "unknown"
            # battle is over, drop party, cooldowns and buffs
            self.ctx.end_battle(result)
        if t in OUT_OF_BATTLE:
            self.withdrawn = False
        scr = SCREENS[t]
        t0 = time.time()
        if t not in TRANSIENT and not t.startswith("battle_"):
            # wait for lists to stop scrolling/fading. battle screens never stand still, their readers vote instead
            self.ctx.battle.settle(timeout=2.0)
        text, data, needs = scr.read(self)
        if self.creator_note:
            # an action found the creator is needed: the screen after it says so, whatever screen it is
            needs = needs or self.creator_note
            self.creator_note = None
        self.seq += 1
        self.state = ScreenState(self.seq, t, text, data, scr.actions(self), needs, scr.guide)
        self.dirty = False
        emit("screen", id=self.seq, type=t, fine=self.fine, text=text, needs_creator=needs, build_s=round(time.time() - t0, 2),
             frame=save_frame(self.game.game_image(scale="native"), t, f"s{self.seq}"))
        record.flush(f"s{self.seq}_{t}", self.game)
        self.save_global()
        self.cond.notify_all()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
            except (ADBError, ConnectionError, OSError) as e:
                self.last_error = f"{type(e).__name__}: {e}"
                if self.device_connected is not False:
                    self.device_connected = False
                    emit("device", status="disconnected", size=None)
                time.sleep(2)
            except Exception as e:
                # keep going, the error shows up in the log and in fgo_screen
                self.last_error = f"{type(e).__name__}: {e}"
                emit("error", where="engine", error=self.last_error, trace=traceback.format_exc()[-2000:])
                time.sleep(2)
            time.sleep(TICK_S)

    def start(self) -> None:
        self.thread = threading.Thread(target=self.run, name="fgo-mcp-watch", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def settled(self) -> bool:
        return self.state is not None and not self.dirty and self.state.type not in TRANSIENT

    def arrived(self) -> bool:
        # after an action: settled and not on a loading or unknown screen
        return self.settled() and self.state.type not in ("unknown", "loading")

    def screen(self, detail: str = "brief", wait_s: float = 20) -> str:
        with self.cond:
            self.cond.wait_for(self.settled, timeout=wait_s)
            if self.state is None:
                return "screen_id=0 screen=starting\nThe engine has not read the screen yet; try again."
            text = self.state.render(detail, with_guide=True)
            if self.dirty:
                text += "\n(the screen is changing; this is the last stable state)"
            return text

    def act(self, screen_id, action: str, args: dict | None) -> str:
        with self.lock:
            st = self.state
            if st is None or self.dirty:
                return "NOT DONE: the screen is changing. Read it again with fgo_screen."
            if str(screen_id) != str(st.id):
                return (f"REJECTED, nothing was done: screen_id {screen_id} is not the current screen. "
                        f"Decide on the current one:\n" + st.render(with_guide=False))
            spec = None
            for a in st.actions:
                if a.name == action:
                    spec = a
            if spec is None:
                valid = ", ".join(a.name for a in st.actions) or "none (wait and read the screen again)"
                return f"REJECTED, nothing was done: '{action}' is not an action on screen {st.type}. Valid: {valid}"
            if args is None:
                args = {}
            if not isinstance(args, dict):
                return f"REJECTED, nothing was done: args must be an object, got {args!r}"
            scr = SCREENS[st.type]
            record.begin()
            problems = scr.verify(self, action, args)
            if problems:
                emit("action", screen_id=st.id, screen=st.type, action=action, args=args, status="rejected", detail=problems)
                return "REJECTED, nothing was done:\n- " + "\n- ".join(problems) + f"\nUsage: {spec.text()}"
            status = "done"
            try:
                outcome = scr.perform(self, action, args)
            except Exception as e:
                outcome = f"ERROR while performing {action}: {type(e).__name__}: {e}"
                status = "error"
                emit("error", where="action", error=outcome, trace=traceback.format_exc()[-2000:])
            emit("action", screen_id=st.id, screen=st.type, action=action, args=args, status=status, detail=outcome)
            record.flush(f"{st.type}_{action}", self.game)
            self.dirty = True
            self.seen_count = (None, 0)
            self.save_global()
            # wait (lock is released here) until the watch loop has the next stable screen
            self.cond.wait_for(self.arrived, timeout=scr.budget_for(action))
            if not self.arrived():
                return outcome + "\nIN PROGRESS: the screen is still changing (animations). Call fgo_screen for the result."
            new = self.state
            return outcome + "\n" + new.render(with_guide=new.type != st.type)

    def info(self, topic: str, slot: int | None = None) -> str:
        ctx = self.ctx
        topic = (topic or "").strip().lower()
        if topic == "kits":
            cards = []
            for f in ctx.party:
                if not f.atlas_id:
                    cards.append(f"{f.label()}: not identified")
                    continue
                owner = "mine"
                if f.support:
                    owner = "support"
                s = ctx.roster.get(f.atlas_id, owner)
                levels = None
                names = None
                if s:
                    levels = s.skill_levels
                    names = s.skill_names
                cards.append(kits.kit_card(atlas.get(f.atlas_id), f.label(), levels, names))
            return "\n".join(cards) or "no party identified yet (only known in battle)"
        if topic == "master":
            lines = []
            for m in ctx.session.master_skills:
                lines.append(f"[{m.num}] {m.name}: {m.desc or m.note} (cooldown {m.cooldown})")
            return "\n".join(lines) or "not read yet"
        if topic == "status":
            return self.status_info(slot)
        if topic == "global":
            return self.global_text()
        if topic == "locations":
            note = ""
            if not places.load_progress()["current"]:
                with self.lock:
                    st = self.state
                    if st is not None and st.type == "menu" and not self.dirty:
                        current = SCREENS["menu"].learn_progress(self)
                        self.dirty = True
                        self.seen_count = (None, 0)
                        note = "\n(the engine went to the Terminal home to read the story progress; read the screen again)"
                        if current is None:
                            return "Story progress could not be read from the Terminal home list." + note
            return places.summary() + note
        return "topics: kits, master, status (with slot), global, locations"

    def status_info(self, slot) -> str:
        ctx = self.ctx
        with self.lock:
            if self.state_type() != "battle_command":
                return "status is only readable on the battle command screen"
            f = find_servant(ctx, slot)
            if not f or not f.atlas_id:
                return f"S{slot} is not identified"
            entries = details.read_status(ctx.battle, slot)
            notes = ctx.session.merge_status(f, entries, ctx.session.known_turn or 0)
            # the game is back on the same screen so the screen id stays the same
            ctx.save()
        seen = []
        for e in entries:
            if e.turns:
                seen.append(f"{e.name} ({e.turns}T)")
            elif e.times:
                seen.append(f"{e.name} ({e.times}x)")
            else:
                seen.append(e.name)
        return "\n".join([f"S{slot} status: {'; '.join(seen) or 'nothing recognised'}"] + notes)

    def save_global(self) -> None:
        self.glob["last_screen_id"] = self.seq
        GLOBAL_PATH.write_text(json.dumps(self.glob, default=str))

    def global_text(self) -> str:
        s = self.ctx.session
        g = self.glob
        names = ", ".join(f.label() for f in self.ctx.party) or "none"
        last_results = ", ".join(g.get("last_results") or []) or "-"
        return (f"in battle: {self.ctx.in_battle()}; turn {s.known_turn}; field: {names}\n"
                f"quest: {g.get('quest') or '?'}; runs won: {g.get('runs_won', 0)}; withdrawals: {g.get('withdrawals', 0)}; "
                f"support: {g.get('support') or '?'}; party: {g.get('party') or '?'}; last results: {last_results}")

    def help(self) -> str:
        st = self.state
        if st:
            actions = " | ".join(a.text() for a in st.actions) or "none"
            current = f"Current screen {st.type}: {SCREENS[st.type].guide}\nActions here: {actions}"
        else:
            current = "no screen read yet"
        handled = []
        for name, scr in SCREENS.items():
            if scr.actions(self) or name in ("battle_busy", "unknown", "loading"):
                handled.append(name)
        return (current + "\nAlways act with the current screen_id. Screens the engine handles: " + ", ".join(handled)
                + ".\nNeeds the creator: refilling AP, Saint Quartz, purchases, withdraw, unrecognised screens.")