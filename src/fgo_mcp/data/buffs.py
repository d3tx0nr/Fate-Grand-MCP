import heapq
from dataclasses import dataclass, field

# buffs per target (ally by atlas id, enemy by slot) in a heap ordered by the turn they run out.
# same kind of buff adds up, different kinds multiply. buffs only vs a trait are shown but not counted
CARD_IDS = {4001: "A", 4002: "B", 4003: "Q"}
AFFINITY = {"weak": 2.0, "normal": 1.0, "resist": 0.5}
BRAVE_EXTRA = 2.0
FOREVER = 10_000

KINDS = {
    "upAtk": ("atk", 1), "downAtk": ("atk", -1),
    "upCommandall": ("card", 1), "downCommandall": ("card", -1),
    "upNpdamage": ("np_dmg", 1), "downNpdamage": ("np_dmg", -1),
    "upDamage": ("power", 1), "downDamage": ("power", -1),
    "upCriticaldamage": ("crit", 1), "downCriticaldamage": ("crit", -1),
    "downDefence": ("def", -1), "upDefence": ("def", 1),
    "downDefencecommandall": ("card_res", -1), "upDefencecommandall": ("card_res", 1),
}
KNOWN_KINDS = {"atk", "card", "np_dmg", "power", "crit", "def", "card_res"}

@dataclass(order=True)
class Buff:
    expires: int
    seq: int
    kind: str = field(compare=False)
    value: float | None = field(compare=False)   # 0.2 = 20%, None if we dont know (seen on status tab)
    name: str = field(compare=False, default="")
    source: str = field(compare=False, default="self")
    conditional: str = field(compare=False, default="")
    display: str | None = field(compare=False, default=None)

    def text(self, turn: int) -> str:
        if self.expires >= FOREVER:
            left = "until removed"
        else:
            left = f"{self.expires - turn}T left"
        if self.display is not None:
            val = self.display
        elif self.value is None:
            val = "?"
        else:
            val = f"{self.value*100:+g}%"
        parts = []
        for x in (self.name, val, left):
            if x:
                parts.append(x)
        result = " ".join(parts)
        if self.source == "enemy":
            result += " (from enemy)"
        if self.conditional:
            result += f" [{self.conditional}, not counted]"
        return result

def classify(buff: dict, value: int | None) -> tuple[str, float | None, str]:
    """atlas buff + Value -> (kind, signed fraction, condition)"""
    kind, sign = KINDS.get(buff["type"], ("other", 1))
    self_indv = buff.get("self", [])
    cards = []
    for i in self_indv:
        if i in CARD_IDS:
            cards.append(CARD_IDS[i])
    if cards and kind in ("card", "crit", "power", "card_res"):
        kind = f"{kind}:{cards[0]}"
    elif 4008 in self_indv and kind == "power":
        kind = "power:face"
    fraction = None
    if value is not None:
        fraction = sign*value/1000
    condition = ""
    if buff.get("op"):
        condition = "vs " + "/".join(buff["op"])
    return kind, fraction, condition

class BuffBook:
    def __init__(self) -> None:
        self.q = {}
        self.seq = 0

    def reset(self, enemies_only: bool = False) -> None:
        if not enemies_only:
            self.q.clear()
            return
        for key in list(self.q.keys()):
            if key[0] == "enemy":
                del self.q[key]

    def add(self, key: tuple, kind: str, value: float | None, turns: int, applied_turn: int,
            name: str = "", source: str = "self", conditional: str = "", display: str | None = None) -> None:
        if turns > 0:
            expires = applied_turn + turns
        else:
            expires = FOREVER
        self.seq += 1
        if key not in self.q:
            self.q[key] = []
        heapq.heappush(self.q[key], Buff(expires, self.seq, kind, value, name, source, conditional, display))

    def expire(self, turn: int) -> None:
        for heap in self.q.values():
            while heap and heap[0].expires <= turn:
                heapq.heappop(heap)

    def active(self, key: tuple, turn: int) -> list[Buff]:
        self.expire(turn)
        return sorted(self.q.get(key, []))

    def totals(self, key: tuple, turn: int) -> dict[str, float]:
        sums = {}
        for b in self.active(key, turn):
            if b.value is None or b.conditional:
                continue
            sums[b.kind] = sums.get(b.kind, 0.0) + b.value
        return sums

    def card_stats(self, ally: tuple, enemy: tuple | None, card_type: str | None, affinity: str, turn: int) -> dict:
        a = self.totals(ally, turn)
        e = {}
        if enemy:
            e = self.totals(enemy, turn)
        t = card_type or ""
        power = a.get("power", 0) + a.get("power:face", 0) + a.get(f"power:{t}", 0)
        dmg = (1 + a.get("atk", 0)) * (1 + a.get(f"card:{t}", 0)) * (1 + power)
        dmg = dmg * (1 - e.get("def", 0)) * (1 - e.get(f"card_res:{t}", 0)) * AFFINITY.get(affinity, 1.0)
        crit = 2 * (1 + a.get("crit", 0) + a.get(f"crit:{t}", 0))
        return {"dmg": round(dmg, 2), "crit_x": round(crit, 2)}

    def np_stats(self, ally: tuple, enemy: tuple | None, card_type: str, turn: int) -> float:
        a = self.totals(ally, turn)
        e = {}
        if enemy:
            e = self.totals(enemy, turn)
        m = (1 + a.get("atk", 0)) * (1 + a.get(f"card:{card_type}", 0)) * (1 + a.get("np_dmg", 0))
        m = m * (1 + a.get("power", 0)) * (1 - e.get("def", 0))
        return round(m, 2)

def display_amount(buff_type: str, v) -> str:
    # amount text for buffs that dont go in the damage maths (guts 3000 etc)
    if not v or "unction" in buff_type:
        return ""
    if buff_type == "upChagetd":
        return f"+{v}"
    if buff_type == "regainNp":
        return f"{v/100:g}%"
    if buff_type == "regainStar":
        return f"{v} stars"
    if buff_type.startswith("up") or buff_type.startswith("down"):
        return f"{v/10:g}%"
    return str(v)

def apply_functions(book: BuffBook, functions: list[dict], level_idx: int, turn: int,
                    caster: tuple, allies: list[tuple], target_ally: tuple | None,
                    enemies: list[tuple], target_enemy: tuple | None) -> list[str]:
    """record the buffs a skill or np gives. returns notes for anything we couldnt place"""
    notes = []
    for f in functions:
        if f["funcType"] not in ("addState", "addStateShort"):
            continue
        if not f.get("buffs") or f["funcTargetTeam"] == "enemy":
            continue
        sv = {}
        if f.get("svals"):
            sv = f["svals"][min(level_idx, len(f["svals"]) - 1)]
        target_type = f["funcTargetType"]
        recipients = []
        if target_type == "self":
            recipients = [caster]
        elif target_type in ("ptOne", "ptAnother", "ptselectOneSub"):
            if target_ally:
                recipients = [target_ally]
        elif target_type in ("ptAll", "ptFull"):
            recipients = list(allies)
        elif target_type in ("ptOther", "ptOneOther"):
            for a in allies:
                if a != caster:
                    recipients.append(a)
        elif target_type == "enemy":
            if target_enemy:
                recipients = [target_enemy]
            elif len(enemies) == 1:
                recipients = list(enemies)
            else:
                notes.append(f"{f['buffs'][0]['name']}: single-enemy effect but the targeted enemy is unknown")
        elif target_type in ("enemyAll", "enemyFull"):
            recipients = list(enemies)

        turns = sv.get("Turn", -1)
        count = sv.get("Count", -1)
        if turns <= 0 and count > 0:
            # buffs with uses instead of turns, count each use as a turn
            turns = count
        for b in f["buffs"]:
            kind, fraction, condition = classify(b, sv.get("Value"))
            display = None
            if kind.split(":")[0] not in KNOWN_KINDS:
                display = display_amount(b["type"], sv.get("Value"))
            if count > 0 and display is not None:
                display = (display + f" {count}x").strip()
            for r in recipients:
                book.add(r, kind, fraction, turns, turn, b.get("name") or b["type"], "self", condition, display)
    return notes