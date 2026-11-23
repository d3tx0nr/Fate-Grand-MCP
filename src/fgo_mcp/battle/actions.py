import re
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

import cv2
import numpy as np

from fgo_mcp.device.game import Game
from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations, Point
from fgo_mcp.fga.screen import card_types, classify
from fgo_mcp.fga.vision import Snapshot
from fgo_mcp.log.events import emit
from fgo_mcp.readers import digits
from fgo_mcp.readers.ocr import engine as ocr_engine

# waits are from FGA (Caster.kt, Battle.kt, Card.kt). slots, skills and cards start at 1
CLICK_WAIT = 0.3
SKILL_DELAY = 0.5
AFFINITY_RANK = {"weak": 0, "normal": 1, "resist": 2}
STUN_TEMPLATES = ("stun", "immobilized", "stun_buster", "stun_arts", "stun_quick")

class BattleError(Exception):
    pass

@dataclass
class Card:
    card: int
    type: str | None            # B, A, Q or None if unknown/stunned
    affinity: str               # weak, normal, resist
    stunned: bool
    owner: int | None = None    # field slot 1-3
    crit: int | None = None
    uncertain: list[str] = field(default_factory=list)   # fields where the frames didnt agree

    def text(self) -> str:
        if self.stunned:
            t = "stunned"
        else:
            t = self.type or "?"
        s = f"c{self.card}={t}"
        if self.affinity != "normal":
            s += f"({self.affinity})"
        return s

class Battle:
    def __init__(self, game: Game):
        self.g = game
        self.master_button_x = None
        self.ocr_crit_cards = set()   # cards where crit came from ocr, so their digits can be learned

    @property
    def L(self) -> Locations:
        return Locations(self.g.ensure_area())

    def snap(self) -> Snapshot:
        return Snapshot(self.g.game_image())

    def screen(self) -> str:
        return classify(self.snap(), self.L)

    def click(self, pt: Point, wait: float = CLICK_WAIT, source: str = "action") -> None:
        emit("tap", x=pt[0], y=pt[1], source=source)
        self.g.tap(*pt)
        time.sleep(wait)

    def wait_until(self, check: Callable[[Snapshot], bool], timeout: float, interval: float = 0.1) -> bool:
        end = time.monotonic() + timeout
        while True:
            if check(self.snap()):
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(interval)

    def is_idle(self, s: Snapshot) -> bool:
        return s.exists("battle", self.L.battle_check_region)

    def is_busy(self, s: Snapshot) -> bool:
        return not self.is_idle(s)

    def require_idle(self) -> None:
        if not self.wait_until(self.is_idle, 3):
            raise BattleError(f"not on the battle command screen (screen={self.screen()})")

    def wait_animation(self, timeout: float = 15) -> bool:
        # like FGA: attack button goes away and then comes back
        self.wait_until(self.is_busy, 2)
        return self.wait_until(self.is_idle, timeout)

    def confirm_skill_use(self) -> None:
        def confirm_shown(s):
            return s.exists("skill_use", self.L.skill_use_region)
        if self.wait_until(confirm_shown, 0.5):
            self.click(self.L.skill_ok_click)

    def tap_target(self, slot: int | None) -> None:
        if slot is None:
            return
        present = self.servants_present(self.snap())
        time.sleep(SKILL_DELAY)
        self.click(self.L.servant_target(slot, present))

    def use_skill(self, slot: int, skill: int, target: int | None = None) -> None:
        self.require_idle()
        self.click(self.L.skill(slot, skill))
        self.confirm_skill_use()
        self.tap_target(target)
        self.click(self.L.extra_info_close_click)
        self.wait_animation()

    def master_x(self) -> int:
        if self.master_button_x is None:
            m = self.snap().find("battle_menu", self.L.master_offset_search_region)
            if m:
                self.master_button_x = m.region.x + m.region.w//2
            else:
                self.master_button_x = self.L.default_master_offset_x()
        return self.master_button_x

    def use_master_skill(self, skill: int, target: int | None = None) -> None:
        self.require_idle()
        mx = self.master_x()
        self.click(Locations.master_open_click(mx), wait=1.5)
        self.click(Locations.master_skill(mx, skill))
        self.confirm_skill_use()
        self.tap_target(target)
        self.click(self.L.extra_info_close_click)
        self.wait_animation()

    def target_enemy(self, enemy: int) -> None:
        self.require_idle()
        self.click(self.L.enemy(enemy), wait=0.5)
        self.click(self.L.extra_info_close_click)

    def settle(self, timeout: float = 1.5, threshold: float = 4.0) -> Snapshot:
        """waits until two frames in a row barely differ, returns the last one"""
        prev = self.snap()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            time.sleep(0.15)
            cur = self.snap()
            if float(cv2.absdiff(prev.gray, cur.gray).mean()) < threshold:
                return cur
            prev = cur
        return prev

    def read_frame(self, s: Snapshot, faces: dict, support_slot: int | None) -> list[tuple]:
        L = self.L
        types = card_types(s, L)
        result = []
        for c in range(1, 6):
            stunned = False
            for t in STUN_TEMPLATES:
                if s.exists(t, L.card_stun_region(c)):
                    stunned = True
                    break
            r = L.card_affinity_region(c)
            if s.exists("weak", r):
                affinity = "weak"
            elif s.exists("resist", r):
                affinity = "resist"
            else:
                affinity = "normal"
            card_type = types[c - 1]
            if stunned:
                card_type = None
            result.append((card_type, affinity, stunned, self.card_owner(s, c, faces, support_slot)))
        return result

    def read_cards(self, s: Snapshot | None = None, faces: dict | None = None,
                   support_slot: int | None = None, crit: bool = True, frames: int = 3) -> list[Card]:
        """reads the hand from a few frames and takes the majority for each field.
        faces = {slot: gray face crop}, support_slot = field slot of the support"""
        self.ocr_crit_cards = set()
        if faces is None:
            faces = {}
        if s is not None:
            snaps = [s]
        else:
            snaps = [self.settle()]
        while len(snaps) < frames:
            time.sleep(0.2)
            snaps.append(self.snap())
        reads = []
        for x in snaps:
            reads.append(self.read_frame(x, faces, support_slot))

        cards = []
        field_names = ("type", "affinity", "stunned", "owner")
        for i in range(5):
            card = Card(i + 1, None, "normal", False)
            for idx in range(len(field_names)):
                counts = Counter()
                for r in reads:
                    counts[r[i][idx]] += 1
                top, n = counts.most_common(1)[0]
                setattr(card, field_names[idx], top)
                if n*2 <= len(reads):
                    card.uncertain.append(field_names[idx])
            if card.stunned:
                card.type = None
            if crit:
                card.crit = self.card_crit(i + 1)
            else:
                card.crit = 0
            cards.append(card)
        return cards

    def crit_crop(self, c: int):
        x = self.L.card_click(c)[0]
        return self.g.game_image(Region(x - 200, 705, 280, 85), scale="native")

    def card_crit(self, c: int) -> int:
        value = digits.read(self.crit_crop(c))
        if value is not None:
            return value
        # digit templates dont know this one yet
        self.ocr_crit_cards.add(c)
        return self.card_crit_ocr(c)

    def crit_variants(self, x: int) -> list:
        # the shiny italic digits are hard for ocr, so try a few different thresholds
        images = []
        crop = None
        for dx, w, y, h in ((-140, 190, 720, 60), (-150, 210, 710, 80)):
            crop = self.g.game_image(Region(x + dx, y, w, h), scale="native")
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            for sat, val in ((60, 150), (80, 170)):
                m = ((hsv[..., 1] < sat) & (hsv[..., 2] > val)).astype(np.uint8) * 255
                images.append(cv2.resize(255 - m, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        images.append(cv2.resize(255 - otsu, None, fx=3, fy=3))
        return images

    def card_crit_ocr(self, c: int) -> int:
        x = self.L.card_click(c)[0]
        votes = Counter()
        for img in self.crit_variants(x):
            padded = cv2.copyMakeBorder(img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
            r = ocr_engine()(padded, use_det=True, use_cls=False)
            m = re.search(r"(100|[1-9]0)\s*%", " ".join(r.txts or []))
            if m:
                votes[int(m.group(1))] += 1
                if votes.most_common(1)[0][1] >= 2:
                    break
        if not votes:
            return 0
        return votes.most_common(1)[0][0]

    def card_owner(self, s: Snapshot, c: int, faces: dict, support_slot: int | None) -> int | None:
        L = self.L
        if support_slot and s.exists("support", L.card_affinity_region(c).moved(-50, 100)):
            return support_slot
        best = 0.5
        best_slot = None
        hay = s.crop(L.card_servant_region(c))
        # faces has the support too, its tag on the card can be hidden by buff icons
        for slot, face in faces.items():
            if hay.shape[0] < face.shape[0] or hay.shape[1] < face.shape[1]:
                continue
            score = float(cv2.minMaxLoc(cv2.matchTemplate(hay, face, cv2.TM_CCOEFF_NORMED))[1])
            if score > best:
                best = score
                best_slot = slot
        return best_slot

    def back_to_command(self) -> None:
        self.click(self.L.attack_back_click, wait=0.8)
        if not self.wait_until(self.is_idle, 4):
            raise BattleError(f"could not return to the command screen (screen={self.screen()})")

    def cards_shown(self, s: Snapshot) -> bool:
        count = 0
        for t in card_types(s, self.L):
            if t is not None:
                count += 1
        return count >= 3

    def open_cards(self) -> None:
        """press attack and wait for the cards, reading them is done by the caller"""
        self.require_idle()
        self.click(self.L.attack_click, wait=0)
        if not self.wait_until(self.is_busy, 5):
            raise BattleError("attack button did not respond")
        ok = self.wait_until(self.cards_shown, 5, 0.2)
        time.sleep(0.5)
        if not ok:
            raise BattleError(f"command cards not detected (screen={self.screen()})")

    @staticmethod
    def auto_faces(cards: list[Card], n: int, type_order: str = "BAQ") -> list[int]:
        def sort_key(c):
            type_rank = 9
            if c.type and c.type in type_order:
                type_rank = type_order.index(c.type)
            return (AFFINITY_RANK[c.affinity], type_rank, c.card)

        usable = []
        for c in cards:
            if not c.stunned:
                usable.append(c)
        usable.sort(key=sort_key)
        picked = []
        for c in usable[:n]:
            picked.append(c.card)
        for c in cards:
            if len(picked) >= n:
                break
            if c.card not in picked:
                picked.append(c.card)
        return picked

    def play_cards(self, nps: list[int], faces: list[int]) -> None:
        L = self.L
        for slot in nps:
            self.click(L.np_click(slot))
        for c in faces:
            self.click(L.card_click(c))

    def servants_present(self, s: Snapshot) -> list[bool]:
        present = []
        for i in (1, 2, 3):
            present.append(s.exists("servant_exist", self.L.servant_present_region(i), 0.7))
        return present