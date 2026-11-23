import pickle
import time
from collections import Counter

from fgo_mcp.battle.details import StatusEntry
from fgo_mcp.battle.party import FieldServant
from fgo_mcp.data import atlas
from fgo_mcp.data import kits
from fgo_mcp.data.buffs import BRAVE_EXTRA, BuffBook, apply_functions, classify
from fgo_mcp.data.store import Roster

CARD_NAMES = {"B": "Buster", "A": "Arts", "Q": "Quick"}
CARD_LETTERS = {"Buster": "B", "Arts": "A", "Quick": "Q"}

def ally_key(f: FieldServant) -> tuple:
    # servants we couldnt identify are kept apart by slot
    if f.atlas_id:
        return ("ally", f.atlas_id, f.support)
    return ("ally-slot", f.slot)

def enemy_key(slot: int) -> tuple:
    return ("enemy", slot)

class BattleSession:
    # saved to disk so a restart in the middle of a battle doesnt lose everything
    STATE_KEYS = ("party", "faces", "book", "wave", "target_enemy", "known_turn", "master_skills")

    def __init__(self, roster: Roster) -> None:
        self.roster = roster
        self.party: list[FieldServant] = []
        self.faces = {}          # ally_key -> gray face crop
        self.ready = {}          # (slot, skill) -> ready on this screen
        self.cooldowns = {}      # (slot, skill) -> turns left as shown on the icon (None if unreadable)
        self.book = BuffBook()
        self.wave = None
        self.waves = None
        self.target_enemy = None
        self.last_hud = None
        self.last_np = None
        self.crit_note = ""
        self.known_turn = None
        self.support_rows = []
        self.support_from_list = None
        self.master_skills = []
        self.quest_rows = []
        self.nav_entries = []
        self.support_name = ""

    def new_battle(self) -> None:
        self.known_turn = 1
        self.book.reset()
        self.faces.clear()
        self.ready = {}
        self.cooldowns = {}
        self.wave = None
        self.target_enemy = None

    def save(self, path, extra: dict) -> None:
        data = {}
        for key in self.STATE_KEYS:
            data[key] = getattr(self, key)
        data.update(extra)
        data["saved"] = time.time()
        path.write_bytes(pickle.dumps(data))

    def load(self, path, max_age_s: float = 3*3600) -> dict:
        if not path.exists():
            return {}
        try:
            data = pickle.loads(path.read_bytes())
        except Exception:
            return {}
        if time.time() - data.get("saved", 0) > max_age_s:
            return {}
        for key in self.STATE_KEYS:
            setattr(self, key, data.get(key))
        return data

    def on_wave(self, wave: int | None) -> None:
        if wave is None or wave == self.wave:
            return
        if self.wave is not None:
            # new wave = new enemies
            self.book.reset(enemies_only=True)
            self.target_enemy = None
        self.wave = wave

    def targets(self, target_ally: int | None, alive: list[int]):
        allies = []
        by_slot = {}
        for f in self.party:
            by_slot[f.slot] = f
            if f.atlas_id:
                allies.append(ally_key(f))
        ally_target = None
        if target_ally in by_slot and by_slot[target_ally].atlas_id:
            ally_target = ally_key(by_slot[target_ally])
        enemy_target = None
        if self.target_enemy in alive:
            enemy_target = enemy_key(self.target_enemy)
        enemies = [enemy_key(e) for e in alive]
        return allies, ally_target, enemies, enemy_target

    def record_skill(self, f: FieldServant, num: int, target_ally: int | None, turn: int, alive: list[int]) -> list[str]:
        sk, level = self.roster.skill(f.atlas_id, f.support, num)
        if not sk:
            return []
        allies, ally_target, enemies, enemy_target = self.targets(target_ally, alive)
        return apply_functions(self.book, sk["functions"], level - 1, turn, caster=ally_key(f), allies=allies,
                               target_ally=ally_target, enemies=enemies, target_enemy=enemy_target)

    def record_master(self, m, target_ally: int | None, turn: int, alive: list[int]) -> list[str]:
        if not m.functions or not m.level:
            return [f"master skill {m.num}: effects not tracked (Mystic Code not identified)"]
        allies, ally_target, enemies, enemy_target = self.targets(target_ally, alive)
        return apply_functions(self.book, m.functions, m.level - 1, turn, caster=("master", 0), allies=allies,
                               target_ally=ally_target, enemies=enemies, target_enemy=enemy_target)

    def record_np(self, f: FieldServant, turn: int, alive: list[int]) -> list[str]:
        np_ = kits.pick_np(atlas.get(f.atlas_id))
        if not np_:
            return []
        owner = "mine"
        if f.support:
            owner = "support"
        rec = self.roster.get(f.atlas_id, owner)
        np_level = 1
        if rec and rec.np_level:
            np_level = rec.np_level
        allies, _, enemies, enemy_target = self.targets(None, alive)
        return apply_functions(self.book, np_["functions"], np_level - 1, turn, caster=ally_key(f), allies=allies,
                               target_ally=None, enemies=enemies, target_enemy=enemy_target)

    def merge_status(self, f: FieldServant, entries: list[StatusEntry], turn: int) -> list[str]:
        """adds debuffs from the status tab that we didnt apply ourselfs (value unknown)"""
        key = ally_key(f)
        ours = set()
        for b in self.book.active(key, turn):
            ours.add(b.name.lower())
        notes = []
        for e in entries:
            if e.name.lower() in ours:
                continue
            b = atlas.buff_index().get(e.name.lower())
            if not b:
                continue
            kind, _, condition = classify(b, None)
            turns = e.turns or e.times or 1
            self.book.add(key, kind, None, turns, turn, e.name, source="enemy", conditional=condition)
            notes.append(f"{f.label()}: {e.name} ({turns}T) from enemy or field, value unknown")
        return notes

    def grouped_text(self, key: tuple, turn: int) -> str:
        counts = Counter()
        for b in self.book.active(key, turn):
            counts[b.text(turn)] += 1
        parts = []
        for text, n in counts.items():
            if n == 1:
                parts.append(text)
            else:
                parts.append(f"{text} (x{n})")
        return ", ".join(parts)

    def buffs_list(self, f: FieldServant, turn: int) -> list[str]:
        text = self.grouped_text(ally_key(f), turn)
        if not text:
            return []
        return text.split(", ")

    def enemy_debuffs_text(self, slot: int, turn: int) -> str:
        return self.grouped_text(enemy_key(slot), turn)

    def card_table(self, hand, turn: int, np_ready: dict[int, int | None]) -> str:
        by_slot = {}
        for f in self.party:
            by_slot[f.slot] = f
        enemy_target = None
        if self.target_enemy:
            enemy_target = enemy_key(self.target_enemy)
        lines = []
        cards_by_owner = {}
        for c in hand:
            f = by_slot.get(c.owner)
            if f:
                who = f.label()[3:]
            else:
                who = "unknown owner"
            if c.stunned:
                lines.append(f"c{c.card}: stunned/unusable ({who})")
                continue
            kind = CARD_NAMES.get(c.type or "", "?")
            affinity = ""
            if c.affinity != "normal":
                affinity = " " + c.affinity
            if f and f.atlas_id:
                st = self.book.card_stats(ally_key(f), enemy_target, c.type, c.affinity, turn)
                stats = f"dmg x{st['dmg']}, crit {c.crit}% (crit dmg x{st['crit_x']})"
                if c.owner not in cards_by_owner:
                    cards_by_owner[c.owner] = []
                cards_by_owner[c.owner].append(c)
            else:
                stats = f"crit {c.crit}%"
            unsure = ""
            if c.uncertain:
                unsure = f" [uncertain: {', '.join(c.uncertain)}]"
            lines.append(f"c{c.card} {kind}{affinity} - {who}: {stats}{unsure}")

        for slot, pct in np_ready.items():
            f = by_slot.get(slot)
            if not f or not f.atlas_id or pct is None or pct < 100:
                continue
            kind = kits.np_kind(atlas.get(f.atlas_id))
            if kind.endswith("support"):
                lines.append(f"NP{slot} {f.label()[3:]}: {kind}, {pct}%, no damage")
                continue
            card_letter = CARD_LETTERS.get(kind.split()[0], "")
            m = self.book.np_stats(ally_key(f), enemy_target, card_letter, turn)
            lines.append(f"NP{slot} {f.label()[3:]}: {kind} damage, {pct}%, dmg x{m}")

        for slot, cards in cards_by_owner.items():
            if len(cards) < 3:
                continue
            f = by_slot[slot]
            ids = ",".join(f"c{c.card}" for c in cards)
            lines.append(f"Brave chain possible: 3 of {f.label()[3:]}'s cards ({ids}) -> extra attack (x{BRAVE_EXTRA} card)")
        if self.crit_note:
            lines.append("NOTE: " + self.crit_note)
        return "\n".join(lines)