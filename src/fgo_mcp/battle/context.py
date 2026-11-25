import time

import cv2

from fgo_mcp.battle import details, party
from fgo_mcp.battle.actions import Battle
from fgo_mcp.battle.session import BattleSession, ally_key
from fgo_mcp.data import atlas, places
from fgo_mcp.data.store import Roster
from fgo_mcp.data.tracker import SkillTracker
from fgo_mcp.device.adb import device_serial
from fgo_mcp.device.game import Game
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.path import DATA
from fgo_mcp.readers import hud

STATE_PATH = DATA / "engine_battle_state.pkl"
RESCAN_WAIT_S = 3.0   # a voice line caption can cover the name strip for a few seconds
RESCANS = 2
FACES_DIR = DATA / "faces"   # delete a face to capture it again

class BattleContext:
    """everything the engine knows about the battle between screens, saved to disk too"""
    def __init__(self, game: Game) -> None:
        self.game = game
        self.battle = Battle(game)
        serial = device_serial()
        self.roster = Roster(serial)
        places.use_device(serial)
        self.tracker = SkillTracker()
        self.session = BattleSession(self.roster)
        self.supports_read = set()
        self.scanned_present = None   # which slots had servants at the last scan
        self.pending_np = None        # (nps, turn, alive), recorded after the turn is done
        self.hand = None
        self.enemy_cache = {}
        self.started = False
        self.restore()

    @property
    def party(self) -> list[party.FieldServant]:
        return self.session.party

    def in_battle(self) -> bool:
        return len(self.party) > 0 or self.session.known_turn is not None

    def new_battle(self) -> None:
        self.tracker.reset()
        self.supports_read.clear()
        self.session.new_battle()
        self.session.master_skills = []
        self.started = False

    def end_battle(self, result: str = "unknown") -> None:
        if self.in_battle():
            emit("battle_end", result=result, turns=self.session.known_turn, waves=self.session.waves or self.session.wave)
        self.new_battle()
        self.session.support_from_list = None
        self.session.support_levels_from_list = False
        self.session.known_turn = None
        self.session.party = []
        self.scanned_present = None
        self.pending_np = None
        self.enemy_cache = {}
        STATE_PATH.unlink(missing_ok=True)

    def apply_pending_np(self) -> list[str]:
        """turn is over, so record buffs/debuffs of the nps that were fired"""
        if not self.pending_np:
            return []
        nps, turn, alive = self.pending_np
        self.pending_np = None
        notes = []
        for f in self.party:
            if f.slot in nps and f.atlas_id:
                notes += self.session.record_np(f, turn, alive)
        return notes

    def save(self) -> None:
        self.session.save(STATE_PATH, {"tracker": self.tracker.used, "supports_read": self.supports_read})

    def restore(self) -> None:
        data = self.session.load(STATE_PATH)
        if data:
            self.tracker.used = data["tracker"]
            self.supports_read.update(data["supports_read"])
            self.started = True

    def rescan_unidentified(self, known: set) -> None:
        """reads the name strip again for servants on the field it couldnt identify: a voice line caption is
        drawn over the strip for a few seconds"""
        present = self.battle.servants_present(self.battle.snap())
        for _ in range(RESCANS):
            missing = []
            for f in self.session.party:
                if f.atlas_id is None and present[f.slot - 1]:
                    missing.append(f.slot)
            if not missing:
                return
            time.sleep(RESCAN_WAIT_S)
            again = party.scan(self.game, prefer=known)
            found = []
            for f in again:
                if f.slot in missing and f.atlas_id:
                    self.session.party[f.slot - 1] = f
                    found.append(f.slot)
            emit("party_rescan", missing=missing, found=found, raws={f.slot: f.raw for f in again if f.slot in missing})

    def scan_party(self) -> list[str]:
        """find out who is on the field, read skill levels if needed, mystic code skills and faces.
        only reads information, never makes a choice in the game"""
        b = self.battle
        roster = self.roster
        session = self.session
        # on turn 1 a skill without a dialog isnt unlocked yet (nothing can be on cooldown from us)
        fresh = hud.read_turn(self.game) == 1
        if fresh:
            self.new_battle()
        known = set()
        for s in roster.all("mine"):
            known.add(s.atlas_id)
        session.party = party.scan(self.game, prefer=known)
        self.rescan_unidentified(known)

        # the support list already knew who the picked support is (by its class tab too), that beats the
        # battle ocr: "Li iShuwen" without a class could be the Assassin or the Lancer
        picked = session.support_name or ""
        for f in session.party:
            if not f.support or f.atlas_id is not None:
                continue
            if session.support_from_list:
                f.atlas_id = session.support_from_list
                f.name = atlas.get(f.atlas_id)["name"]
                f.candidates = [f.atlas_id]
            elif picked:
                candidates = atlas.match(picked, f.cls)
                if len(candidates) == 1:
                    f.atlas_id = candidates[0]["id"]
                    f.name = candidates[0]["name"]
                    f.candidates = [candidates[0]["id"]]

        notes = []
        for f, reason in party.register(roster, session.party):
            if f.atlas_id is None:
                maybe = []
                for c in f.candidates[:4]:
                    maybe.append(atlas.get(c)["name"])
                note = f"S{f.slot}: could not identify ({f.raw!r})"
                if maybe:
                    note += "; could be: " + ", ".join(maybe)
                notes.append(note)
                continue
            from_list = f.atlas_id == session.support_from_list and getattr(session, "support_levels_from_list", False)
            if reason == "support" and (f.atlas_id in self.supports_read or from_list):
                continue
            if reason:
                reads = party.refresh_skills(b, roster, f, fresh=fresh)
                emit("skill_read", slot=f.slot, atlas_id=f.atlas_id, reason=reason, reads=[vars(r) for r in reads])
                if f.support:
                    self.supports_read.add(f.atlas_id)
                levels = [r.level for r in reads]
                notes.append(f"{f.label()}: read skill levels ({reason}) -> {levels}")

        if not session.master_skills:
            session.master_skills = details.read_master_skills(b, roster)
        if not self.started:
            emit("battle_start", party=[vars(f) for f in session.party], master_skills=[vars(m) for m in session.master_skills])
            self.started = True

        for f in session.party:
            if ally_key(f) in session.faces:
                continue
            if f.atlas_id:
                session.faces[ally_key(f)] = self.face(f)
            else:
                session.faces[ally_key(f)] = details.capture_face(self.battle, f.slot)
        self.save()
        return notes

    def read_readiness(self) -> None:
        # read from the screen every time the command screen is read: a skill on cooldown is darker and
        # shows Turn(s) with the turns left. skills that shorten cooldowns are covered this way too
        skills = []
        for f in self.session.party:
            if not f.atlas_id:
                continue
            for n in (1, 2, 3):
                if self.roster.skill(f.atlas_id, f.support, n)[0] is not None:
                    skills.append((f.slot, n))
        cooling = hud.read_skill_cooldowns(self.game, skills)
        ready = {}
        for key in skills:
            ready[key] = key not in cooling
        self.session.ready = ready
        self.session.cooldowns = cooling

    def face(self, f):
        owner = "mine"
        if f.support:
            owner = "support"
        path = FACES_DIR / f"{f.atlas_id}_{owner}.png"
        if path.exists():
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                return img
        face = details.capture_face(self.battle, f.slot)
        FACES_DIR.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(path), face)
        return face

    def read_hand(self):
        """cards with owners"""
        session = self.session
        faces = {}
        support_slot = None
        for f in self.party:
            if ally_key(f) in session.faces:
                faces[f.slot] = session.faces[ally_key(f)]
            if f.support and support_slot is None:
                support_slot = f.slot
        hand = self.battle.read_cards(faces=faces, support_slot=support_slot)
        emit("hand", cards=[vars(c) for c in hand],
             frame=save_frame(self.game.game_image(scale="native"), "battle_cards", "hand"))
        return hand

    def np_gauges_on_cards(self, gauges: list[int | None]) -> dict[int, int | None]:
        """np gauges by slot, the ones we couldnt read are filled from the np cards on the card screen"""
        result = {}
        missing = False
        for i in range(len(gauges)):
            result[i + 1] = gauges[i]
            if gauges[i] is None:
                missing = True
        if missing:
            shown = hud.ready_np_slots(self.game, self.party)
            for slot in result:
                if result[slot] is None and slot in shown:
                    result[slot] = 100
        return result