from fgo_mcp.data import atlas
from fgo_mcp.data import kits

# master skill cooldowns, counted from the cooldown the skill dialog showed. servant skills are not
# counted here, they are read from the screen every turn (readers/hud.py read_skill_cooldowns)
class SkillTracker:
    def __init__(self) -> None:
        self.used = {}   # ("master", 0), skill -> (turn used, cooldown)

    def reset(self) -> None:
        self.used.clear()

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