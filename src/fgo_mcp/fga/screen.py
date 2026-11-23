from fgo_mcp.device.geometry import Region
from fgo_mcp.fga.locations import Locations
from fgo_mcp.fga.vision import Snapshot

CARD_TYPES = {"buster": "B", "art": "A", "quick": "Q"}

def card_types(s: Snapshot, L: Locations) -> list[str | None]:
    types = []
    for card in range(1, 6):
        r = L.card_type_region(card)
        found = None
        for template_name, letter in CARD_TYPES.items():
            if s.exists(template_name, r):
                found = letter
                break
        types.append(found)
    return types

# same order as FGA AutoBattle loop: (name, template, region attribute, similarity)
CHECKS = [
    ("menu", "menu", "menu_screen_region", 0.8),
    ("bond", "bond", "result_bond_region", 0.8),
    ("result", "result", "result_screen_region", 0.8),
    ("result", "master_lvl_up", "result_master_lvl_up_region", 0.8),
    ("result", "master_exp", "result_master_exp_region", 0.8),
    ("drops", "mat_rewards", "result_mat_rewards_region", 0.8),
    ("quest_reward", "questreward", "result_quest_reward_region", 0.8),
    ("support", "support_screen", "support_check_region", 0.85),
    ("repeat", "repeat", "continue_region", 0.8),
    ("ordeal_out_of_pods", "close", "ordeal_out_of_pods_region", 0.8),
    ("interlude_end", "close", "interlude_end_close_region", 0.8),
    ("withdraw", "withdraw", "withdraw_region", 0.8),
    ("story", "storyskip", "story_skip_region", 0.7),
    ("friend_request", "support_extra", "friend_request_region", 0.8),
    ("bond10_reward", "bond10", "ce_reward_region", 0.75),
    ("ce_details", "ce_details", "ce_reward_details_region", 0.8),
    ("rank_up", "rank_up", "rank_up_region", 0.8),
    ("ap_refill", "stamina", "stamina_region", 0.8),
    ("inventory_full", "inven_full", "inventory_full_region", 0.8),
    ("ordeal_confirm_pod", "start_quest", "ordeal_confirm_pod_use_region", 0.8),
]

def dialog_region(a):
    return Region(a.x_from_center(-580), 900, 1160, 500)

def party_region(a):
    return Region(a.nav_right(-510), 1180, 510, 260)

def title_region(a):
    return Region(a.nav_left(0), 1150, 560, 250)

def overlay_region(a):
    return Region(a.nav_right(-410), 850, 410, 200)

def webview_region(a):
    return Region(a.nav_right(-210), 0, 210, 170)

def notice_region(a):
    return Region(a.x_from_center(-600), 1200, 1200, 240)

# not in FGA, our own templates for navigation screens. dialog has to be before party since it covers it
NAV_CHECKS = [
    ("dialog", "nav_dialog_close", dialog_region),
    ("party", "nav_party_start", party_region),
    ("title", "nav_title", title_region),
    ("menu_overlay", "nav_overlay_close", overlay_region),
    ("webview", "nav_webview_x", webview_region),
    ("notice", "nav_tap_screen", notice_region),
]

def classify(s: Snapshot, L: Locations) -> str:
    # loading banner can be on top of any screen so check it first
    if s.exists("nav_loading", Region(L.a.nav_right(-1100), 1150, 1100, 290)):
        return "loading"
    if s.exists("retry", L.retry_region):
        return "retry"
    if s.exists("battle", L.battle_check_region):
        return "battle"
    card_count = 0
    for t in card_types(s, L):
        if t is not None:
            card_count += 1
    if card_count >= 3:
        return "cards"
    for name, template_name, region_name, similarity in CHECKS:
        if s.exists(template_name, getattr(L, region_name), similarity):
            return name
    for name, template_name, region_func in NAV_CHECKS:
        if s.exists(template_name, region_func(L.a)):
            return name
    return "unknown"