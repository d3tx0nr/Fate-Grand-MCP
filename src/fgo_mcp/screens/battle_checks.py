from fgo_mcp.data.tracker import needs_target
from fgo_mcp.screens.base import as_int

# checks shared by the battle screens. every problem is a sentence saying how to fix it,
# we never change what the model asked for

def current_turn(eng) -> int:
    h = eng.ctx.session.last_hud
    if h and h.turn:
        return h.turn
    return eng.ctx.session.known_turn or 0

def living_enemies(eng) -> list[int]:
    h = eng.ctx.session.last_hud
    if not h:
        return []
    alive = []
    for e in h.enemies:
        if e.hp:
            alive.append(e.slot)
    return alive

def find_servant(ctx, slot):
    for f in ctx.party:
        if f.slot == slot:
            return f
    return None

def find_master_skill(session, num):
    for m in session.master_skills:
        if m.num == num:
            return m
    return None

def check_skill(eng, slot, skill, target, problems: list[str]) -> None:
    ctx = eng.ctx
    f = find_servant(ctx, slot)
    if not f or not f.atlas_id:
        problems.append(f"S{slot} is not identified; no skills can be used for it")
        return
    if ctx.roster.skill(f.atlas_id, f.support, skill)[0] is None:
        problems.append(f"S{slot} has no skill {skill}")
        return
    owner = "mine"
    if f.support:
        owner = "support"
    rec = ctx.roster.get(f.atlas_id, owner)
    name = None
    if rec and rec.skill_names:
        name = rec.skill_names[skill - 1]
    left = ctx.tracker.remaining(f.atlas_id, skill, current_turn(eng))
    # if we have a saved icon, the screen decides. otherwise our own cooldown count
    ready = ctx.session.ready.get((slot, skill), left == 0)
    if not ready:
        msg = f"S{slot} skill {skill} is on cooldown"
        if left:
            msg += f" (about {left} more turn(s))"
        problems.append(msg)
    has_target = needs_target(f.atlas_id, skill, name)
    if has_target and target is None:
        problems.append(f'S{slot} skill {skill} needs an ally target (1-3), given with the skill itself: '
                        f'{{"slot":{slot},"skill":{skill},"target":2}} (in turn, inside its "skills" entry; the '
                        f'top-level "target" of turn is the enemy to attack)')
    if not has_target and target is not None:
        problems.append(f'S{slot} skill {skill} takes no target; remove "target"')

def check_master(eng, skill, target, problems: list[str]) -> None:
    info = find_master_skill(eng.ctx.session, skill)
    if info is None or not info.name:
        if info:
            why = info.note
        else:
            why = "Mystic Code skills not read"
        problems.append(f"master skill {skill} is not available ({why})")
        return
    left = eng.ctx.tracker.remaining(("master", 0), skill, current_turn(eng))
    if left > 0:
        problems.append(f"master skill {skill} is on cooldown for {left} more turn(s)")
    if info.needs_target and target is None:
        problems.append(f'master skill {skill} needs an ally target (1-3), given with the skill itself: '
                        f'{{"skill":{skill},"target":2}} (in turn, inside its "master_skills" entry)')

def parse_cards(cards, problems: list[str]):
    """"auto", "choose" or a list of face cards 1-5. nps given here are rejected"""
    if isinstance(cards, str):
        c = cards.strip().strip('"').lower()
        if c == "auto" or c == "choose":
            return c
        problems.append(f'cards must be "auto", "choose" or a list of face cards 1-5, got {cards!r}')
        return None
    if not isinstance(cards, list):
        problems.append(f'cards must be "auto", "choose" or a list, got {cards!r}')
        return None
    for c in cards:
        if isinstance(c, str) and c.strip().lower().startswith("np"):
            problems.append(f"cards are face cards 1-5 only; put NPs in nps (e.g. nps=[2]), got cards={cards}")
            return None
    result = []
    for c in cards:
        result.append(as_int(c))
    for c in result:
        if not isinstance(c, int) or c < 1 or c > 5:
            problems.append(f"cards must be distinct face cards 1-5, got {cards}")
            return None
    if len(set(result)) != len(result):
        problems.append(f"cards must be distinct face cards 1-5, got {cards}")
        return None
    return result

def parse_nps(nps, problems: list[str]) -> list[int]:
    if nps is None:
        return []
    if not isinstance(nps, list):
        nps = [nps]
    result = []
    for n in nps:
        result.append(as_int(n))
    valid = len(set(result)) == len(result)
    for n in result:
        if not isinstance(n, int) or n < 1 or n > 3:
            valid = False
    if not valid:
        problems.append(f"nps must be distinct servant slots 1-3, got {nps}")
        return []
    return result

def card_count(nps: list[int], cards, problems: list[str]) -> None:
    if not isinstance(cards, list):
        return
    total = len(nps) + len(cards)
    if total != 3:
        problems.append(f"choose 3 cards in total: {len(nps)} NP(s) + {len(cards)} face card(s) = {total}")

def np_not_ready(nps: list[int], gauges: dict[int, int | None]) -> list[str]:
    problems = []
    for n in nps:
        g = gauges.get(n)
        if g is None:
            problems.append(f"NP{n} gauge is unreadable and its NP card is not shown")
        elif g < 100:
            problems.append(f"NP{n} is at {g}%, not ready (needs 100%)")
    return problems

def gauges_by_slot(values) -> dict[int, int | None]:
    if not values:
        values = [None, None, None]
    gauges = {}
    for i in range(len(values)):
        gauges[i + 1] = values[i]
    return gauges