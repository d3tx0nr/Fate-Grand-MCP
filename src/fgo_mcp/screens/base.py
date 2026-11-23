from typing import TYPE_CHECKING, Any

from fgo_mcp.engine.state import ActionSpec

if TYPE_CHECKING:
    from fgo_mcp.engine.core import Engine

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