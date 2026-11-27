from fgo_mcp.data import atlas, kits
from fgo_mcp.data.tracker import needs_target
from fgo_mcp.engine.state import ActionSpec
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.readers import hud
from fgo_mcp.screens.base import Screen, as_int, int_arg, unknown_keys
from fgo_mcp.screens.battle_cards import BattleCards
from fgo_mcp.screens.battle_checks import (card_count, check_master, check_skill, current_turn, find_master_skill,
                                           find_servant, gauges_by_slot, living_enemies, np_not_ready, parse_cards,
                                           parse_nps)

def as_master_entry(m) -> dict:
    # master_skills can be given as just numbers [1, 3]
    if isinstance(m, dict):
        return m
    return {"skill": m}

class BattleCommand(Screen):
    type = "battle_command"
    budget = 15.0
    budgets = {"turn": 30.0}
    guide = ("Your turn. Use skills and master skills one by one (each returns the updated state), optionally "
             "pick an enemy with target, then attack to open the card screen. turn(...) does all of it in one call. "
             "Skills marked (needs target) need an ally slot.")

    def actions(self, eng):
        return [
            ActionSpec("skill", "slot 1-3, skill 1-3, target ally 1-3 only if (needs target)", '{"slot":1,"skill":2}'),
            ActionSpec("master_skill", "skill 1-3, target ally 1-3 only if (needs target)", '{"skill":1,"target":2}'),
            ActionSpec("target", "enemy 1-3", '{"enemy":2}'),
            ActionSpec("attack", "", "{}"),
            ActionSpec("turn", 'skills [{slot,skill,target?}], master_skills [{skill,target?}], target, nps [slots], '
                       'cards "auto"|"choose"|[face cards]', '{"skills":[{"slot":1,"skill":1}],"nps":[1],"cards":"auto"}'),
        ]

    def read(self, eng):
        ctx = eng.ctx
        s = ctx.session
        ctx.apply_pending_np()
        b = ctx.battle
        present = b.servants_present(b.snap())
        h = hud.read(eng.game, ctx.enemy_cache)
        if h.turn == 1 and (s.known_turn or 1) > 1:
            # turn went back to 1 so this is a new battle
            ctx.end_battle("won")
        # keep the last known turn and wave if this frame couldnt be read
        if h.turn is None and s.known_turn is not None:
            h.turn = s.known_turn
        if h.wave is None and s.wave is not None:
            h.wave = s.wave
            h.waves = s.waves
        s.waves = h.waves or s.waves
        s.known_turn = h.turn or s.known_turn
        prev_hp = None
        if s.last_hud is not None:
            prev_hp = s.last_hud.hp
        s.last_hud = h
        s.last_np = h.np
        s.on_wave(h.wave)
        notes = []
        if not ctx.party or present != ctx.scanned_present:
            notes = ctx.scan_party()
            ctx.scanned_present = present
        else:
            notes = ctx.check_slots(ctx.slots_to_check(prev_hp, h.hp, present))
        ctx.read_readiness()
        turn = h.turn or 0
        emit("hud", turn=h.turn, wave=h.wave, waves=h.waves, enemies_left=h.enemies_left, np=h.np, hp=h.hp,
             enemies=[vars(e) for e in h.enemies], target=h.target,
             frame=save_frame(eng.game.game_image(scale="native"), "battle_command", f"turn{turn}"))
        s.target_enemy = h.target or s.target_enemy
        text = "\n".join(notes + [self.brief(eng, h, present, turn)])
        enemies = []
        for e in h.enemies:
            if e.hp:
                enemies.append({"slot": e.slot, "hp": e.hp, "class": e.cls, "danger": e.danger})
        data = {"wave": h.wave, "waves": h.waves, "turn": turn, "np": h.np, "hp": h.hp, "enemies": enemies}
        ctx.save()
        return text, data, None

    def enemy_lines(self, eng, h, turn) -> list[str]:
        s = eng.ctx.session
        lines = []
        for e in h.enemies:
            if not e.hp:
                continue
            line = e.text()
            if s.target_enemy == e.slot:
                line += " <targeted>"
            debuffs = s.enemy_debuffs_text(e.slot, turn)
            if debuffs:
                line += f" [our debuffs: {debuffs}]"
            lines.append(line)
        return lines

    def servant_skill_line(self, eng, f, slot, turn) -> str:
        ctx = eng.ctx
        ready = []
        cooling = []
        for n in (1, 2, 3):
            sk, level = ctx.roster.skill(f.atlas_id, f.support, n)
            if not sk:
                continue
            if not ctx.session.ready.get((slot, n), True):
                left = ctx.session.cooldowns.get((slot, n))
                if left:
                    cooling.append(f"{n} ({left}T)")
                else:
                    cooling.append(f"{n}")
                continue
            target_text = ""
            if needs_target(f.atlas_id, n):
                target_text = " (needs target)"
            ready.append(f"[{n}]{target_text} {kits.skill_tags(sk, level)}")
        line = "   ready: " + (" | ".join(ready) or "none")
        if cooling:
            line += "; cooldown: " + ", ".join(cooling)
        return line

    def brief(self, eng, h, present, turn) -> str:
        ctx = eng.ctx
        s = ctx.session
        left_text = ""
        if h.enemies_left:
            left_text = f" ({h.enemies_left} left in this wave)"
        enemies = self.enemy_lines(eng, h, turn)
        lines = [f"Wave {h.wave}/{h.waves}, turn {turn}",
                 "Enemies" + left_text + ": " + ("; ".join(enemies) or "none visible")]

        # buffs every servant has are shown once as party wide
        buff_lists = []
        for f in ctx.party:
            if f.atlas_id:
                buff_lists.append(s.buffs_list(f, turn))
        shared = []
        if len(buff_lists) > 1:
            for b in buff_lists[0]:
                everyone_has_it = True
                for other in buff_lists[1:]:
                    if b not in other:
                        everyone_has_it = False
                if everyone_has_it:
                    shared.append(b)

        for i in (1, 2, 3):
            f = find_servant(ctx, i)
            if not present[i - 1]:
                lines.append(f"S{i}: empty")
                continue
            if not f or not f.atlas_id:
                lines.append(f"S{i}: not identified")
                continue
            np_value = h.np[i - 1]
            hp = h.hp[i - 1]
            if np_value is None:
                np_text = "?"
            elif np_value >= 100:
                np_text = f"{np_value}% READY"
            else:
                np_text = f"{np_value}%"
            hp_text = "?"
            if hp is not None:
                hp_text = str(hp)
            lines.append(f"S{i} {f.label()[3:]}: HP {hp_text}, NP {np_text} | {kits.np_role(atlas.get(f.atlas_id))}")
            lines.append(self.servant_skill_line(eng, f, i, turn))
            own = []
            for b in s.buffs_list(f, turn):
                if b not in shared:
                    own.append(b)
            if own:
                lines.append("   active: " + ", ".join(own))
        if shared:
            lines.append("Party-wide active: " + ", ".join(shared))

        if s.master_skills:
            parts = []
            for m in s.master_skills:
                if not m.name:
                    parts.append(f"[{m.num}] unavailable ({m.note})")
                    continue
                left = ctx.tracker.remaining(("master", 0), m.num, turn)
                target_text = ""
                if m.needs_target:
                    target_text = " (needs target)"
                status = "READY"
                if left > 0:
                    status = f"{left}T"
                parts.append(f"[{m.num}]{target_text} {m.name}: {m.desc or m.note} ({status})")
            lines.append("Master skills: " + " | ".join(parts))
        return "\n".join(lines)

    def verify(self, eng, action, args):
        problems = []
        if action == "skill":
            unknown_keys(args, {"slot", "skill", "target"}, problems)
            slot = int_arg(args, "slot", 1, 3, problems)
            skill = int_arg(args, "skill", 1, 3, problems)
            target = int_arg(args, "target", 1, 3, problems, required=False)
            if slot and skill:
                check_skill(eng, slot, skill, target, problems)
        elif action == "master_skill":
            unknown_keys(args, {"skill", "target"}, problems)
            skill = int_arg(args, "skill", 1, 3, problems)
            target = int_arg(args, "target", 1, 3, problems, required=False)
            if skill:
                check_master(eng, skill, target, problems)
        elif action == "target":
            unknown_keys(args, {"enemy"}, problems)
            e = int_arg(args, "enemy", 1, 3, problems)
            alive = living_enemies(eng)
            if e and e not in alive:
                problems.append(f"enemy {e} is not a living enemy; living enemies: {alive or 'none visible'}")
        elif action == "attack":
            unknown_keys(args, set(), problems)
        elif action == "turn":
            self.verify_turn(eng, args, problems)
        return problems

    def verify_turn(self, eng, args, problems) -> None:
        unknown_keys(args, {"skills", "master_skills", "target", "nps", "cards"}, problems)
        used = set()
        skills = args.get("skills") or []
        for i in range(len(skills)):
            sk = skills[i]
            if not isinstance(sk, dict):
                problems.append(f'skills[{i}] must be an object like {{"slot":1,"skill":2}}, got {sk!r}')
                continue
            unknown_keys(sk, {"slot", "skill", "target"}, problems)
            slot = int_arg(sk, "slot", 1, 3, problems)
            skill = int_arg(sk, "skill", 1, 3, problems)
            target = int_arg(sk, "target", 1, 3, problems, required=False)
            if not slot or not skill:
                continue
            if (slot, skill) in used:
                problems.append(f"S{slot} skill {skill} is listed twice; a skill can be used once per turn")
                continue
            used.add((slot, skill))
            check_skill(eng, slot, skill, target, problems)

        used_master = set()
        for m in args.get("master_skills") or []:
            m = as_master_entry(m)
            unknown_keys(m, {"skill", "target"}, problems)
            skill = int_arg(m, "skill", 1, 3, problems)
            if not skill:
                continue
            if skill in used_master:
                problems.append(f"master skill {skill} is listed twice")
                continue
            used_master.add(skill)
            check_master(eng, skill, int_arg(m, "target", 1, 3, problems, required=False), problems)

        if args.get("target") is not None:
            e = int_arg(args, "target", 1, 3, problems)
            alive = living_enemies(eng)
            if e and e not in alive:
                problems.append(f"target {e} is not a living enemy; living enemies: {alive or 'none visible'}")
        nps = parse_nps(args.get("nps"), problems)
        cards = parse_cards(args.get("cards", "auto"), problems)
        card_count(nps, cards, problems)
        if not args.get("skills") and not args.get("master_skills"):
            # no skills this turn so the np gauge cant go up before the cards
            for problem in np_not_ready(nps, gauges_by_slot(eng.ctx.session.last_np)):
                if "unreadable" not in problem:
                    problems.append(problem)

    def perform(self, eng, action, args):
        ctx = eng.ctx
        if action == "skill":
            return self.use_skill(eng, as_int(args["slot"]), as_int(args["skill"]), as_int(args.get("target")))
        if action == "master_skill":
            return self.use_master(eng, as_int(args["skill"]), as_int(args.get("target")))
        if action == "target":
            e = as_int(args["enemy"])
            ctx.battle.target_enemy(e)
            ctx.session.target_enemy = e
            return f"targeted enemy {e}"
        if action == "attack":
            ctx.battle.open_cards()
            return "opened the card screen"
        if action == "turn":
            log = []
            if args.get("target") is not None:
                e = as_int(args["target"])
                ctx.battle.target_enemy(e)
                ctx.session.target_enemy = e
                log.append(f"targeted enemy {e}")
            for m in args.get("master_skills") or []:
                m = as_master_entry(m)
                log.append(self.use_master(eng, as_int(m["skill"]), as_int(m.get("target"))))
            for sk in args.get("skills") or []:
                log.append(self.use_skill(eng, as_int(sk["slot"]), as_int(sk["skill"]), as_int(sk.get("target"))))
            if args.get("skills") or args.get("master_skills"):
                # skills can charge the np gauge, so read it again before deciding which nps can fire
                before = ctx.session.last_np or [None, None, None]
                now = hud.read_np(eng.game)
                for i in range(len(now)):
                    if now[i] is None and i < len(before):
                        now[i] = before[i]
                ctx.session.last_np = now
            ctx.battle.open_cards()
            cards = parse_cards(args.get("cards", "auto"), [])
            if cards == "choose":
                log.append("opened the card screen (choose cards with play)")
                return "\n".join(log)
            log.append(BattleCards().play_now(eng, parse_nps(args.get("nps"), []), cards))
            return "\n".join(log)
        raise ValueError(action)

    def use_skill(self, eng, slot, skill, target) -> str:
        ctx = eng.ctx
        f = find_servant(ctx, slot)
        turn = current_turn(eng)
        ctx.battle.use_skill(slot, skill, target)
        notes = ctx.session.record_skill(f, skill, target, turn, living_enemies(eng))
        ctx.save()
        result = f"S{slot} skill {skill}"
        if target:
            result += f" -> S{target}"
        for n in notes:
            result += f"\n  {n}"
        return result

    def use_master(self, eng, skill, target) -> str:
        ctx = eng.ctx
        ctx.battle.use_master_skill(skill, target)
        info = find_master_skill(ctx.session, skill)
        notes = []
        if info and info.cooldown:
            ctx.tracker.record(("master", 0), skill, current_turn(eng), info.cooldown)
        if info:
            notes = ctx.session.record_master(info, target, current_turn(eng), living_enemies(eng))
        ctx.save()
        result = f"master skill {skill}"
        if target:
            result += f" -> S{target}"
        for n in notes:
            result += f"\n  {n}"
        return result