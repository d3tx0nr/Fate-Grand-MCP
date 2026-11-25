import json
import sqlite3
import time
from dataclasses import dataclass

from fgo_mcp.path import DATA

REFRESH_DAYS = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS servants (
    atlas_id      INTEGER NOT NULL,
    owner         TEXT    NOT NULL CHECK (owner IN ('mine', 'support')),
    name          TEXT    NOT NULL,
    class         TEXT    NOT NULL,
    level         INTEGER,
    skill_levels  TEXT,
    skill_names   TEXT,
    np_level      INTEGER,
    first_seen    REAL    NOT NULL,
    last_seen     REAL    NOT NULL,
    skills_checked REAL,
    PRIMARY KEY (atlas_id, owner)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

@dataclass
class Servant:
    atlas_id: int
    owner: str
    name: str
    cls: str
    level: int | None
    skill_levels: list[int] | None
    np_level: int | None
    skills_checked: float | None

class Roster:
    # one roster file per device serial, so two accounts never mix servants
    def __init__(self, serial: str):
        self.path = str(DATA / f"roster-{serial}.db")
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def get(self, atlas_id: int, owner: str = "mine") -> Servant | None:
        row = self.db.execute(
            "SELECT atlas_id, owner, name, class, level, skill_levels, np_level, skills_checked FROM servants WHERE atlas_id=? AND owner=?",
            (atlas_id, owner),
        ).fetchone()
        if row is None:
            return None
        skill_levels = None
        if row[5]:
            skill_levels = json.loads(row[5])
        return Servant(row[0], row[1], row[2], row[3], row[4], skill_levels, row[6], row[7])

    def all(self, owner: str = "mine") -> list[Servant]:
        servants = []
        rows = self.db.execute("SELECT atlas_id FROM servants WHERE owner=? ORDER BY name", (owner,)).fetchall()
        for row in rows:
            s = self.get(row[0], owner)
            if s is not None:
                servants.append(s)
        return servants

    def seen(self, atlas_id: int, owner: str, name: str, cls: str, level: int | None = None) -> None:
        now = time.time()
        self.db.execute(
            "INSERT INTO servants (atlas_id, owner, name, class, level, first_seen, last_seen) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(atlas_id, owner) DO UPDATE SET last_seen=excluded.last_seen",
            (atlas_id, owner, name, cls, level, now, now),
        )
        self.db.commit()

    def needs_check(self, atlas_id: int, owner: str = "mine", level_now: int | None = None) -> str | None:
        """why the skill levels should be read again: new, unchecked, level_changed, stale. None if fine"""
        s = self.get(atlas_id, owner)
        if s is None:
            return "new"
        if s.skills_checked is None or s.skill_levels is None:
            return "unchecked"
        if level_now is not None and s.level is not None and level_now != s.level:
            return "level_changed"
        if owner == "mine" and time.time() - s.skills_checked > REFRESH_DAYS*86400:
            return "stale"
        return None

    def set_skills(self, atlas_id: int, owner: str, levels: list[int], level: int | None = None,
                   np_level: int | None = None) -> None:
        self.db.execute(
            "UPDATE servants SET skill_levels=?, skills_checked=?, level=COALESCE(?, level), np_level=COALESCE(?, np_level) WHERE atlas_id=? AND owner=?",
            (json.dumps(levels), time.time(), level, np_level, atlas_id, owner),
        )
        self.db.commit()

    def skill(self, atlas_id: int, support: bool, num: int) -> tuple[dict | None, int]:
        """(atlas skill, level) for skill num: the skill from atlas, the level read in battle"""
        from fgo_mcp.data import atlas
        from fgo_mcp.data import kits

        owner = "mine"
        if support:
            owner = "support"
        rec = self.get(atlas_id, owner)
        level = 10
        if rec is not None:
            levels = rec.skill_levels or [10, 10, 10]
            level = levels[num - 1]
        if level == 0:
            # empty slot, some npc guests only have 2 skills
            return None, 0
        return kits.pick_skill(atlas.get(atlas_id), num), level