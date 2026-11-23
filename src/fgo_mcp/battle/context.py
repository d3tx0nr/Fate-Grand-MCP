import cv2

from fgo_mcp.battle import details, party
from fgo_mcp.battle.actions import Battle
from fgo_mcp.battle.session import BattleSession, ally_key
from fgo_mcp.data import atlas
from fgo_mcp.data.store import Roster
from fgo_mcp.data.tracker import SkillTracker
from fgo_mcp.device.adb import device_serial
from fgo_mcp.device.game import Game
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.path import DATA
from fgo_mcp.readers import digits, hud

STATE_PATH = DATA / "engine_battle_state.pkl"
FACES_DIR = DATA / "faces"   # delete a face to capture it again

class BattleContext:
    """everything the engine knows about the battle between screens, saved to disk too"""
    def __init__(self, game: Game) -> None:
        self.game = game
        self.battle = Battle(game)
        self.roster = Roster(device_serial())
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

    def scan_party(self) -> list[str]:
        """find out who is on the field, read skill levels if needed, mystic code skills and faces.
        only reads information, never makes a choice in the game"""
        b = self.battle
        roster = self.roster
        session = self.session
        if hud.read_turn(self.game) == 1:
            self.new_battle()
        # turn 1 and nothing used yet means no skill can be on cooldown
        fresh = hud.read_turn(self.game) == 1 and not self.tracker.used
        known = set()
        for s in roster.all("mine"):
            known.add(s.atlas_id)
        session.party = party.scan(self.game, prefer=known)

        # the support name from the support list is cleaner than the battle ocr
        picked = session.support_name or ""
        for f in session.party:
            if f.support and f.atlas_id is None and picked:
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
            if reason == "support" and (f.atlas_id in self.supports_read or f.atlas_id == session.support_from_list):
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

        self.save_skill_images()
        for f in session.party:
            if ally_key(f) in session.faces:
                continue
            if f.atlas_id:
                session.faces[ally_key(f)] = self.face(f)
            else:
                session.faces[ally_key(f)] = details.capture_face(self.battle, f.slot)
        self.save()
        return notes

    def save_skill_images(self) -> None:
        # like FGA, keep each skill icon while its ready so we can tell later if its ready again
        snap = self.battle.snap()
        turn = self.session.known_turn or 0
        for f in self.session.party:
            if not f.atlas_id:
                continue
            for n in (1, 2, 3):
                key = (ally_key(f), n)
                if key in self.session.skill_images:
                    continue
                unlocked = self.roster.skill(f.atlas_id, f.support, n)[0] is not None
                if unlocked and self.tracker.remaining(f.atlas_id, n, turn) == 0:
                    self.session.skill_images[key] = snap.patch(self.battle.L.skill_image_region(f.slot, n))

    def read_readiness(self) -> None:
        # a skill is ready when its saved icon is back. if a cooldown got shortened by some effect
        # our own count is wrong, so drop it
        snap = self.battle.snap()
        ready = {}
        for f in self.session.party:
            for n in (1, 2, 3):
                image = self.session.skill_images.get((ally_key(f), n))
                if image is None:
                    continue
                is_ready = snap.has_patch(image, self.battle.L.skill_image_region(f.slot, n))
                ready[(f.slot, n)] = is_ready
                if is_ready:
                    self.tracker.used.pop((f.atlas_id, n), None)
        self.session.ready = ready

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

    def read_hand(self, stars: int | None):
        """cards with owners and crit %. crit is marked unsure if it doesnt add up to stars x 10%"""
        session = self.session
        faces = {}
        support_slot = None
        for f in self.party:
            if ally_key(f) in session.faces:
                faces[f.slot] = session.faces[ally_key(f)]
            if f.support and support_slot is None:
                support_slot = f.slot
        read_crit = stars is None or stars > 0
        hand = self.battle.read_cards(faces=faces, support_slot=support_slot, crit=read_crit)
        session.crit_note = ""
        emit("hand", stars=stars, cards=[vars(c) for c in hand], ocr_crit=sorted(self.battle.ocr_crit_cards),
             frame=save_frame(self.game.game_image(scale="native"), "battle_cards", "hand"))
        if stars:
            total = 0
            for c in hand:
                total += c.crit or 0
            expected = min(stars*10, 100*len(hand))
            if total != expected:
                session.crit_note = f"crit chances unreliable this turn: they add up to {total}% but {stars} stars should give {expected}%"
                for c in hand:
                    c.uncertain.append("crit")
            elif self.battle.ocr_crit_cards:
                # the total is right, so the ocr reads can be learned as digit templates
                for c in hand:
                    if c.card in self.battle.ocr_crit_cards:
                        digits.learn(self.battle.crit_crop(c.card), c.crit or 0)
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