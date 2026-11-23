import time

from fgo_mcp.device.geometry import Region
from fgo_mcp.engine.state import ActionSpec
from fgo_mcp.log.events import emit, save_frame
from fgo_mcp.screens.base import Screen, unknown_keys

# results and dialogs from FGA AutoBattle's screen map, the actions do what FGA does on them
RESULT_KINDS = {"bond", "result", "drops", "quest_reward", "bond10_reward", "ce_details", "rank_up"}
RESULT_LABELS = {"bond": "bond points", "result": "quest result / master EXP", "drops": "drops",
                 "quest_reward": "first-clear reward", "bond10_reward": "bond CE reward", "ce_details": "CE obtained",
                 "rank_up": "rank up"}

def center(r: Region) -> tuple[int, int]:
    return r.x + r.w//2, r.y + r.h//2

class Simple(Screen):
    """a screen where no action takes arguments"""
    acts: dict[str, str] = {}

    def actions(self, eng):
        specs = []
        for name in self.acts:
            specs.append(ActionSpec(name, "", "{}"))
        return specs

    def verify(self, eng, action, args):
        problems = []
        unknown_keys(args, set(), problems)
        return problems

class Results(Simple):
    type = "results"
    budget = 25.0
    guide = "Battle won: result screens (bond, EXP, drops, rewards). next clicks through all of them."
    acts = {"next": "click through the result screens"}

    def read(self, eng):
        return f"Showing: {RESULT_LABELS.get(eng.fine, eng.fine)}", {"kind": eng.fine}, None

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        L = b.L
        pages = []
        seen = []
        end = time.monotonic() + 20
        while time.monotonic() < end:
            s = b.snap()
            if s.exists("repeat", L.continue_region):
                # repeat dialog is on top of the results, stop here
                break
            kind = eng.classify_fine()
            if kind not in RESULT_KINDS:
                if kind == "unknown":
                    # in between two result screens
                    time.sleep(0.5)
                    continue
                break
            if not seen or seen[-1] != kind:
                seen.append(kind)
                pages.append({"kind": kind, "frame": save_frame(eng.game.game_image(scale="native"), kind, "results")})
            if kind == "drops":
                b.click(center(L.result_mat_rewards_region), wait=0.8)
            elif kind == "bond10_reward":
                b.click((L.cx(0), 720), wait=0.8)
            elif kind == "ce_details":
                b.click(L.ce_reward_close_click, wait=0.8)
            elif kind == "rank_up":
                b.click(L.middle_of_screen_click, wait=0.8)
            else:
                b.click(L.result_click, wait=0.6)
        eng.glob["last_results"] = seen
        emit("results", pages=pages)
        labels = []
        for k in seen:
            labels.append(RESULT_LABELS.get(k, k))
        return "clicked through: " + (", ".join(labels) or "nothing")

class Repeat(Simple):
    type = "repeat"
    guide = "Quest finished. repeat plays the same quest again (costs AP); close leaves to the quest list."
    acts = {"repeat": "play again", "close": "leave"}

    def read(self, eng):
        g = eng.glob
        return f"Runs won this session: {g.get('runs_won', 0)}; last quest: {g.get('quest') or '?'}", {}, None

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        L = b.L
        s = b.snap()
        if action == "repeat":
            m = s.find("repeat", L.continue_region)
            if not m:
                return "NOT DONE: the Repeat button was not found"
            b.click(center(m.region), wait=1.5)
            return "pressed Repeat"
        # close is left of repeat, same distance from the center
        m = s.find("close", Region(L.cx(-920), 1000, 800, 300))
        if not m:
            return "NOT DONE: the Close button was not found"
        b.click(center(m.region), wait=1.5)
        return "pressed Close"

class ApRefill(Simple):
    type = "ap_refill"
    guide = "Not enough AP for this quest. Refilling (apples, Saint Quartz) is only done by the creator."
    acts = {"close": "close the AP dialog"}

    def read(self, eng):
        return "", {}, "not enough AP for this quest: refilling AP needs the creator"

    def perform(self, eng, action, args):
        eng.ctx.battle.click(eng.ctx.battle.L.stamina_close_click, wait=1.5)
        return "closed the AP dialog"

class Withdraw(Simple):
    type = "withdraw"
    guide = "The party was defeated. withdraw gives up this run. Continuing costs Saint Quartz: creator only."
    acts = {"withdraw": "give up the run"}

    def read(self, eng):
        return "Party defeated.", {}, "continuing the battle costs Saint Quartz: only the creator can decide that"

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        L = b.L
        m = b.snap().find("withdraw", L.withdraw_region)
        if not m:
            return "NOT DONE: the Withdraw button was not found"
        b.click(center(m.region), wait=0.5)
        b.click(L.withdraw_accept_click, wait=1.0)
        b.click(L.withdraw_close_click, wait=1.5)
        eng.glob["withdrawals"] = eng.glob.get("withdrawals", 0) + 1
        eng.ctx.end_battle("withdrawn")
        return "withdrew from the battle"

class Story(Simple):
    type = "story"
    guide = "Story scene. skip skips it; or read the screen again later to let it play."
    acts = {"skip": "skip the scene"}

    def perform(self, eng, action, args):
        b = eng.ctx.battle
        b.click(b.L.story_skip_click, wait=0.5)
        b.click(b.L.story_skip_yes_click, wait=1.5)
        return "skipped the story"

class Retry(Simple):
    type = "retry"
    guide = "Connection error dialog. retry sends the request again."
    acts = {"retry": "retry the connection"}

    def perform(self, eng, action, args):
        eng.ctx.battle.click(center(eng.ctx.battle.L.retry_region), wait=2.0)
        return "pressed Retry"

class FriendRequest(Simple):
    type = "friend_request"
    guide = "Friend request offer after using a non-friend support. decline skips it."
    acts = {"decline": "do not send a friend request"}

    def perform(self, eng, action, args):
        eng.ctx.battle.click(eng.ctx.battle.L.friend_request_reject_click, wait=1.0)
        return "declined the friend request"

class CloseDialog(Simple):
    # interlude end and ordeal call out of pods, FGA just closes them
    acts = {"close": "close the dialog"}

    def __init__(self, type_: str, guide: str, click: str, needs: str | None = None) -> None:
        self.type = type_
        self.guide = guide
        self.click = click
        self.needs = needs

    def read(self, eng):
        return "", {}, self.needs

    def perform(self, eng, action, args):
        eng.ctx.battle.click(getattr(eng.ctx.battle.L, self.click), wait=1.5)
        return f"closed the {self.type.replace('_', ' ')} dialog"

class OrdealConfirmPod(Simple):
    type = "ordeal_confirm_pod"
    guide = "Ordeal Call: starting this quest uses a pod. confirm starts it."
    acts = {"confirm": "use the pod and start"}

    def perform(self, eng, action, args):
        eng.ctx.battle.click(center(eng.ctx.battle.L.ordeal_confirm_pod_use_region), wait=2.0)
        return "confirmed the pod use"

class InventoryFull(Screen):
    type = "inventory_full"
    guide = "Inventory is full: quests cannot start until the creator makes room."

    def read(self, eng):
        return "", {}, "inventory full: the creator must sell or use items before quests can start"