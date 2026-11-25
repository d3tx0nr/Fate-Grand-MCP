import difflib
import re
from typing import TYPE_CHECKING, Any

from fgo_mcp.engine.state import ActionSpec

if TYPE_CHECKING:
    from fgo_mcp.engine.core import Engine

class SeenSoFar:
    """what a scrollable screen showed during one visit, so the agent doesnt have to remember it while
    scrolling. it starts over when the key changes (another list) or the agent was on another screen"""
    SAME = 0.85   # ocr reads the same row a bit differently on each frame

    def __init__(self) -> None:
        self.key = None
        self.items: list[tuple[str, str]] = []   # (identity, text)

    def update(self, eng: Engine, key, items: list[tuple[str, str]], screen_type: str) -> None:
        previous = eng.state.type if eng.state else None
        if key != self.key or previous != screen_type:
            self.key = key
            self.items = []
        for ident, text in items:
            plain = " ".join(re.findall(r"[a-z0-9]+", ident.lower()))
            if not plain:
                continue
            known = False
            for i in range(len(self.items)):
                if difflib.SequenceMatcher(None, plain, self.items[i][0]).ratio() >= self.SAME:
                    known = True
                    if len(text) > len(self.items[i][1]):
                        # keep the fuller read ("Super" then "Super Orion")
                        self.items[i] = (plain, text)
                    break
            if not known:
                self.items.append((plain, text))

    def text(self, label: str, shown_now: int, at_end: bool = False) -> str | None:
        """only once scrolling found more than is on screen now"""
        if len(self.items) <= shown_now:
            return None
        lines = [f"Seen so far {label} ({len(self.items)}, no need to scroll back for these):"]
        for _, t in self.items:
            lines.append(f"- {t}")
        if at_end:
            lines.append("(end of the list reached: this is everything)")
        else:
            lines.append("(the list goes on: scroll to see the rest before saying this is everything)")
        return "\n".join(lines)

class Reject(Exception):
    """the action is not done, the message tells the model how to fix it"""

class Screen:
    type = "unknown"
    guide = ""
    budget = 15.0   # seconds to wait for the next stable screen after an action
    budgets: dict[str, float] = {}

    def budget_for(self, action: str) -> float:
        return self.budgets.get(action, self.budget)

    def actions(self, eng: Engine) -> list[ActionSpec]:
        return []

    def read(self, eng: Engine) -> tuple[str, dict, str | None]:
        """(text, data, needs_creator) for the current frame"""
        return "", {}, None

    def verify(self, eng: Engine, action: str, args: dict) -> list[str]:
        return []

    def perform(self, eng: Engine, action: str, args: dict) -> str:
        raise Reject(f"no actions on screen {self.type}")

# argument helpers. they only fix the input format (like "c3" for 3), never change what was picked
def as_int(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    t = v.strip().lower()
    for prefix in ("np", "c", "s", "e"):
        if t.startswith(prefix):
            t = t[len(prefix):]
    t = t.strip()
    if t.isdigit():
        return int(t)
    return v

def int_arg(args: dict, key: str, lo: int, hi: int, problems: list[str], required: bool = True) -> int | None:
    if key not in args or args[key] is None:
        if required:
            problems.append(f'missing "{key}" ({lo}-{hi})')
        return None
    v = as_int(args[key])
    if isinstance(v, bool) or not isinstance(v, int) or v < lo or v > hi:
        problems.append(f'"{key}" must be a number {lo}-{hi}, got {args[key]!r}')
        return None
    return v

def unknown_keys(args: dict, allowed: set[str], problems: list[str]) -> None:
    extra = []
    for key in args:
        if key not in allowed:
            extra.append(key)
    extra.sort()
    if extra:
        allowed_text = sorted(allowed) or "nothing"
        problems.append(f"unknown argument(s) {extra}; this action takes: {allowed_text}")