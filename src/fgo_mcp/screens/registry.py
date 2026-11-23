from fgo_mcp.screens.base import Screen
from fgo_mcp.screens.battle_cards import BattleBusy, BattleCards
from fgo_mcp.screens.battle_command import BattleCommand
from fgo_mcp.screens.dialogs import (ApRefill, CloseDialog, FriendRequest, InventoryFull, OrdealConfirmPod, Repeat,
                                     Results, Retry, Story, Withdraw)
from fgo_mcp.screens.navigation import Dialog, MenuOverlay, Notice, Title, WebView
from fgo_mcp.screens.prebattle import Party, Support
from fgo_mcp.screens.terminal import Terminal

class Loading(Screen):
    type = "loading"
    guide = ("The game is loading or downloading data (LOADING banner). Nothing to do: call fgo_screen again in a "
             "while. Downloads on a new account can take minutes.")

class Unknown(Screen):
    type = "unknown"
    guide = "Screen not recognised (transition, dialog or a screen the engine does not know). Read it again shortly."

# names from fga/screen.py classify() -> our screen types
FGA_TO_TYPE = {
    "retry": "retry", "battle": "battle_command", "cards": "battle_cards", "menu": "menu",
    "bond": "results", "result": "results", "drops": "results", "quest_reward": "results",
    "bond10_reward": "results", "ce_details": "results", "rank_up": "results",
    "support": "support", "repeat": "repeat", "ordeal_out_of_pods": "ordeal_out_of_pods",
    "interlude_end": "interlude_end", "withdraw": "withdraw", "story": "story", "friend_request": "friend_request",
    "ap_refill": "ap_refill", "inventory_full": "inventory_full", "ordeal_confirm_pod": "ordeal_confirm_pod",
    "party": "party", "title": "title", "menu_overlay": "menu_overlay", "webview": "webview", "dialog": "dialog",
    "loading": "loading", "notice": "notice",
}

ALL_SCREENS = [
    BattleCommand(), BattleCards(), BattleBusy(),
    Results(), Repeat(), ApRefill(), Withdraw(), Story(), Retry(), FriendRequest(), OrdealConfirmPod(), InventoryFull(),
    CloseDialog("interlude_end", "An interlude finished. close continues.", "interlude_close_click"),
    CloseDialog("ordeal_out_of_pods", "Ordeal Call: no pods left for this quest. close leaves.", "ordeal_out_of_pods_click",
                "no Ordeal Call pods left: the quest cannot be repeated now"),
    Terminal(), Support(), Party(), Title(), MenuOverlay(), WebView(), Dialog(), Notice(), Loading(), Unknown(),
]

SCREENS: dict[str, Screen] = {}
for screen in ALL_SCREENS:
    SCREENS[screen.type] = screen