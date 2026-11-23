from fgo_mcp.data import atlas
from fgo_mcp.data import kits
from fgo_mcp.data.store import Roster

# when each skill was used and when its ready again. cooldown reduction effects are ignored so
# ready can be a turn late. keyed by atlas id so swapping order keeps it
class SkillTracker:
    def __init__(self) -> None:
        self.used = {}   # (atlas_id, skill) -> (turn used, cooldown)

    def reset(self) -> None:
        self.used.clear()

    @staticmethod
    def cooldown_for(roster: Roster, atlas_id: int, support: bool, num: int) -> int:
        sk, level = roster.skill(atlas_id, support, num)
        if sk is None:
            return 0
        return sk["coolDown"][max(1, min(level, 10)) - 1]

    def record(self, atlas_id: int, num: int, turn: int, cd: int) -> None:
        self.used[(atlas_id, num)] = (turn, cd)

    def remaining(self, atlas_id: int, num: int, turn_now: int) -> int:
        if (atlas_id, num) not in self.used:
            return 0
        used_turn, cd = self.used[(atlas_id, num)]
        return max(0, used_turn + cd - turn_now)

def needs_target(atlas_id: int, num: int, skill_name: str | None = None) -> bool:
    sk = kits.pick_skill(atlas.get(atlas_id), num, skill_name)
    if not sk:
        return False
    for f in sk["functions"]:
        if f["funcTargetType"] == "ptOne" and f["funcTargetTeam"] != "enemy":
            return True
    return False