from fgo_mcp.device.geometry import GameArea, Region

Point = tuple[int, int]


class Locations:
    def __init__(self, area: GameArea):
        self.a = area
        self.wide = area.is_wide

    # helpers
    def cx(self, x: int) -> int:
        return self.a.x_from_center(x)

    def rx(self, x: int) -> int:
        return self.a.x_from_right(x)

    def by(self, y: int) -> int:
        return y + self.a.script_h

    def rc(self, x: int, y: int, w: int, h: int) -> Region:
        return Region(self.cx(x), y, w, h)

    # ---- transitions (Locations.kt, BattleScreenLocations.kt)
    @property
    def np_started_region(self) -> Region:
        return self.rc(-400, 500, 800, 400)

    @property
    def battle_safe_middle_click(self) -> Point:
        return (self.cx(0), 550)

    def danger_region(self, enemy: int) -> Region:
        """FGA BattleScreenLocations.dangerRegion: where TargetDanger / TargetServant show for an enemy."""
        x, w = [(0, 485), (485, 482), (967, 476)][enemy - 1]
        return Region(x + (150 if self.wide else 0), 0, w, 220)

    # ---- battle screen (BattleScreenLocations.kt)
    def skill(self, slot: int, skill: int) -> Point:
        xs = [[148, 324, 500], [784, 960, 1136], [1418, 1594, 1770]]
        x = xs[slot - 1][skill - 1]
        return (x + 108, 1117) if self.wide else (x, 1158)

    def servant_target(self, slot: int, present: list[bool] | None = None) -> Point:
        """Ally target button for field slot `slot`. FGO re-centres the buttons when fewer than
        3 servants are on the field (FGA Caster.selectSkillTarget): 2 -> left/right, 1 -> middle."""
        alive = [i + 1 for i, p in enumerate(present or [True, True, True]) if p]
        if len(alive) == 2 and slot in alive:
            return self.cx(-290 if alive.index(slot) == 0 else 330), 880
        if len(alive) == 1:
            return self.cx(0), 880
        return self.cx([-580, 0, 660][slot - 1]), 880

    def enemy(self, enemy: int) -> Point:
        return [90, 570, 1050][enemy - 1] + (183 if self.wide else 0), 80

    @property
    def battle_check_region(self) -> Region:
        x, y, w, h = (-660, -210, 400, 175) if self.wide else (-455, -181, 336, 116)
        return Region(self.rx(x), self.by(y), w, h)

    def servant_present_region(self, slot: int) -> Region:
        x, y = self.skill(slot, 3)
        return Region(x + 35, y + 67, 120, 120)

    def skill_image_region(self, slot: int, skill: int) -> Region:
        """FGA imageRegion: a patch of the skill icon. Saved while the skill is ready; the skill is ready again
        when the patch matches (on cooldown the icon is greyed and shows a number)."""
        x, y = self.skill(slot, skill)
        return Region(x + 22, y + 28, 30, 30)

    def servant_change_check_region(self, slot: int) -> Region:
        """FGA servantChangeCheckRegion: a patch of the servant that changes when another servant comes in."""
        return Region(self.skill(slot, 2)[0] + 20, 865, 40, 80)

    def field_support_region(self, slot: int) -> Region:
        """FGA servantChangeSupportCheckRegion: where the field's +SUPPORT tag is (servant_check_support)."""
        return Region(self.skill(slot, 2)[0] + 25, 710, 300, 170)

    @property
    def attack_click(self) -> Point:
        x, y = (-460, -230) if self.wide else (-260, -240)
        return self.rx(x), self.by(y)

    @property
    def skill_ok_click(self) -> Point:
        return self.cx(400), 850

    @property
    def extra_info_close_click(self) -> Point:
        return self.rx(-300), 940

    @property
    def skill_use_region(self) -> Region:
        return self.rc(-210, 320, 420, 85)

    # ---- master (MasterLocations.kt); offset found via battle_menu template
    @property
    def master_offset_search_region(self) -> Region:
        return Region(self.rx(-400), 360, 400, 80)

    def default_master_offset_x(self) -> int:
        return self.rx(-298)

    def stage_count_region(self, offset_x: int) -> Region:
        """FGA stageCountRegion: the current wave's digit of "BATTLE 1/3", placed from the battle menu button."""
        return Region(offset_x + (-571 if self.wide else -638), 23, 33, 53)

    @staticmethod
    def master_skill(offset_x: int, skill: int) -> Point:
        return [-740, -560, -400][skill - 1] + 178 + offset_x, 620

    @staticmethod
    def master_open_click(offset_x: int) -> Point:
        return offset_x, 640

    # ---- attack screen (AttackScreenLocations.kt)
    def card_click(self, card: int) -> Point:
        return self.cx([-980, -530, 20, 520, 1070][card - 1]), 1000

    def np_click(self, slot: int) -> Point:
        x, y = [(-280, 220), (20, 400), (460, 400)][slot - 1]
        return self.cx(x), y

    def card_affinity_region(self, card: int) -> Region:
        return self.rc([-985, -470, 41, 554, 1068][card - 1], 590, 250, 260)

    def card_type_region(self, card: int) -> Region:
        return self.rc([-1280, -768, -256, 256, 768][card - 1], 1060, 512, 200)

    def card_stun_region(self, card: int) -> Region:
        t = self.card_type_region(card)
        return Region(t.x, 930, 248, 188)

    def card_servant_region(self, card: int) -> Region:
        return self.rc([-1174, -660, -150, 364, 880][card - 1] - 100, 700, 500, 400)

    @property
    def attack_back_click(self) -> Point:
        x, y = (-325, 1310) if self.wide else (-160, 1370)
        return self.rx(x), y

    # ---- menus and results (Locations.kt)
    @property
    def menu_screen_region(self) -> Region:
        x, w = (-600, 600) if self.wide else (-460, 460)
        return Region(self.rx(x), 1200, w, 240)

    @property
    def continue_region(self) -> Region:
        return self.rc(120, 1000, 800, 300)

    @property
    def result_screen_region(self) -> Region:
        return self.rc(-1180, 300, 700, 200)

    @property
    def result_master_lvl_up_region(self) -> Region:
        return self.rc(710, 160, 250, 270)

    @property
    def result_master_exp_region(self) -> Region:
        return self.rc(0, 350, 400, 110)

    @property
    def result_bond_region(self) -> Region:
        return self.rc(720, 600, 120, 400)

    @property
    def result_mat_rewards_region(self) -> Region:
        return self.rc(800, 1220, 280, 130)

    @property
    def result_quest_reward_region(self) -> Region:
        return self.rc(350, 140, 370, 250)

    @property
    def result_click(self) -> Point:
        return self.cx(320), 1350

    @property
    def withdraw_region(self) -> Region:
        return self.rc(-880, 540, 1800, 333)

    @property
    def stamina_region(self) -> Region:
        return self.rc(-680, 200, 300, 300)

    @property
    def rank_up_region(self) -> Region:
        return self.rc(270, 730, 220, 340)

    # story (FGA Locations.menuStorySkip*)
    @property
    def story_skip_region(self) -> Region:
        return self.rc(960, 20, 300, 120)

    @property
    def story_skip_click(self) -> Point:
        return self.cx(1080), 80

    @property
    def story_skip_yes_click(self) -> Point:
        return self.cx(320), 1100

    # ---- dialogs and other screens of FGA's AutoBattle map (Locations.kt)
    retry_region = property(lambda self: self.rc(20, 1000, 700, 300))
    inventory_full_region = property(lambda self: self.rc(-280, 860, 560, 190))
    ordeal_out_of_pods_region = property(lambda self: self.rc(-112, 1088, 219, 72))
    ordeal_out_of_pods_click = property(lambda self: (self.cx(-2), 1124))
    ordeal_confirm_pod_use_region = property(lambda self: self.rc(190, 1096, 420, 62))
    interlude_end_close_region = property(lambda self: self.rc(-515, 1080, 230, 90))
    interlude_close_click = property(lambda self: (self.cx(-399), 1125))
    stamina_ok_click = property(lambda self: (self.cx(370), 1120))
    stamina_close_click = property(lambda self: (self.cx(0), 1240))
    withdraw_accept_click = property(lambda self: (self.cx(485), 720))
    withdraw_close_click = property(lambda self: (self.cx(-10), 1140))
    friend_request_region = property(lambda self: self.rc(600, 150, 100, 94))
    friend_request_reject_click = property(lambda self: (self.cx(-680), 1200))
    ce_reward_region = property(lambda self: self.rc(-230, 1216, 33, 28))
    ce_reward_details_region = property(lambda self: Region(193 if self.wide else 0, 512, 135, 115))
    ce_reward_close_click = property(lambda self: (265 if self.wide else 80, 60))
    middle_of_screen_click = property(lambda self: (self.cx(0), 720))
    selected_party_region = property(lambda self: self.rc(-370, 62, 740, 72))

    def party_dot(self, index: int) -> Point:
        """FGA partySelectionArray: 15 dots above the party name, party 8 (index 7) in the centre."""
        return self.cx((index - 7) * 50), 100

    @property
    def menu_select_quest_click(self) -> Point:
        """FGA: the uppermost quest of the list."""
        return self.rx(-460 if self.wide else -270), 440

    @property
    def menu_start_quest_click(self) -> Point:
        x, y = (-350, -160) if self.wide else (-160, -90)
        return self.rx(x), self.by(y)

    # ---- support screen (SupportScreenLocations.kt)
    @property
    def support_header_x(self) -> int:
        return 171 if self.wide else 0

    @property
    def support_offset_x(self) -> int:
        if not self.wide:
            return 94
        width, total = 2560 - 94 - 145, self.a.script_w - 305 - 270
        return 305 + round((total - width) / 2)

    @property
    def support_check_region(self) -> Region:
        return Region(self.support_header_x, 0, 200, 400)

    support_extra_region = property(lambda self: Region(1200 + self.support_header_x, 200, 130, 130))
    support_update_click = property(lambda self: (1865 + self.support_header_x, 260))
    support_update_yes_click = property(lambda self: (self.cx(200), 1110))
    support_refresh_region = property(lambda self: Region(1770 + self.support_header_x, 195, 175, 165))
    support_list_top_click = property(lambda self: (self.rx(-218 if self.wide else -80), 360))
    support_not_found_region = property(lambda self: self.rc(-180, 810, 360, 166))
    support_confirm_setup_region = property(lambda self: Region(2006 + self.support_offset_x, 0, 370, 1440))
    support_friend_region = property(lambda self: Region(2140 + self.support_offset_x, 332, 120, 1091))

    def support_bounds(self, y: int) -> Region:
        """FGA SupportBoundsFinder: defaultBounds at the y of a confirm-setup button match minus 70."""
        return Region(-18 + self.support_offset_x, y, 2356, 428)

    def support_class_tab(self, x: int) -> Point:
        return x + self.support_header_x, 256

    @property
    def support_swipe(self) -> tuple[Point, Point]:
        o = self.support_offset_x
        return (-59 + o, 1000), (-89 + o, 300)  # canLongSwipe on scrcpy-injected touches

    @property
    def support_top_scrollbar_region(self) -> Region:
        return Region(self.rx(-255), 325, 140, 55) if self.wide else self.rc(1195, 325, 100, 55)

    @property
    def support_bottom_scrollbar_region(self) -> Region:
        return Region(self.rx(-255), 1390, 140, 55) if self.wide else self.rc(1195, 1390, 100, 55)