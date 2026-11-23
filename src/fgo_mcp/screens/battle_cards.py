from fgo_mcp.battle.actions import Battle
from fgo_mcp.engine.state import ActionSpec
from fgo_mcp.readers import hud
from fgo_mcp.screens.base import Screen, unknown_keys
from fgo_mcp.screens.battle_checks import (card_count, current_turn, gauges_by_slot, living_enemies, np_not_ready,
                                           parse_cards, parse_nps)

class BattleCards(Screen):
    type = "battle_cards"
    budget = 30.0   # enemy turn is included in this
    guide = ("Card screen. play picks 3 cards in total: READY NPs (by servant slot) plus face cards 1-5, "
             'or cards "auto". back returns to the command screen to use skills first.')

    def actions(self, eng):
        return [ActionSpec("play", 'nps [READY slots], cards "auto" or [face cards 1-5] (3 cards in total)',
                           '{"nps":[1],"cards":[2,4]}'),
                ActionSpec("back", "", "{}")]

    def read(self, eng):
        ctx = eng.ctx
        s = ctx.session
        stars = hud.read_stars(eng.game)
        has_np_values = False
        if s.last_np:
            for v in s.last_np:
                if v is not None:
                    has_np_values = True
        if has_np_values:
            gauges = s.last_np
        else:
            gauges = hud.read_np(eng.game)
        g = ctx.np_gauges_on_cards(gauges)
        s.last_np = [g.get(1), g.get(2), g.get(3)]
        hand = ctx.read_hand(stars)
        ctx.hand = hand

        ready_slots = []
        for slot, value in g.items():
            if value is not None and value >= 100:
                ready_slots.append(slot)
        ready_text = "none"
        if ready_slots:
            ready_text = ", ".join(f"NP{slot}" for slot in ready_slots)
        stars_text = "?"
        if stars is not None:
            stars_text = str(stars)
        text = f"Stars {stars_text}\n" + s.card_table(hand, current_turn(eng), g)
        if s.crit_note:
            text += "\n" + s.crit_note
        text += f"\nREADY NPs: {ready_text}"

        cards = []
        for c in hand:
            cards.append({"card": c.card, "type": c.type, "affinity": c.affinity, "owner": c.owner, "crit": c.crit})
        data = {"stars": stars, "ready_nps": ready_slots, "cards": cards}
        return text, data, None

    def verify(self, eng, action, args):
        problems = []
        if action == "back":
            unknown_keys(args, set(), problems)
            return problems
        unknown_keys(args, {"nps", "cards"}, problems)
        nps = parse_nps(args.get("nps"), problems)
        cards = parse_cards(args.get("cards", "auto"), problems)
        if cards == "choose":
            problems.append('cards "choose" is for the command screen; here give "auto" or a list of face cards')
        card_count(nps, cards, problems)
        problems += np_not_ready(nps, gauges_by_slot(eng.ctx.session.last_np))
        return problems

    def perform(self, eng, action, args):
        if action == "back":
            eng.ctx.battle.back_to_command()
            return "back to the command screen"
        nps = parse_nps(args.get("nps"), [])
        cards = parse_cards(args.get("cards", "auto"), [])
        return self.play_now(eng, nps, cards)

    def play_now(self, eng, nps: list[int], cards) -> str:
        """plays on the open card screen. the turn action on the command screen uses this too"""
        ctx = eng.ctx
        hand = ctx.hand
        if not hand or eng.state_type() != "battle_cards":
            g = ctx.np_gauges_on_cards(ctx.session.last_np or [None, None, None])
            ctx.session.last_np = [g.get(1), g.get(2), g.get(3)]
            hand = ctx.read_hand(hud.read_stars(eng.game))
            ctx.hand = hand
        not_ready = np_not_ready(nps, gauges_by_slot(ctx.session.last_np))
        if not_ready:
            # skills were already used, so stop here instead of guessing
            return "NOT PLAYED: " + "; ".join(not_ready) + ". The card screen is open: use play."
        if cards == "auto":
            faces = Battle.auto_faces(hand, 3 - len(nps))
        else:
            faces = list(cards)
        ctx.battle.play_cards(nps, faces)
        ctx.pending_np = (nps, current_turn(eng), living_enemies(eng))
        ctx.hand = None
        by_number = {}
        for c in hand:
            by_number[c.card] = c
        shown = []
        for n in faces:
            if n not in by_number:
                continue
            c = by_number[n]
            part = f"c{n} {c.type or '?'}"
            if c.affinity != "normal":
                part += " " + c.affinity
            shown.append(part)
        return f"played nps={nps} faces: {', '.join(shown)}"

class BattleBusy(Screen):
    type = "battle_busy"
    guide = "Animations (attacks, NP, enemy turn, wave change). Nothing to do: read the screen again."