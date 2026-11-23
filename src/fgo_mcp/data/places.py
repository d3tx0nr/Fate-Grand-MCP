import json
import re
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from functools import cache

from fgo_mcp.path import DATA

# the terminal's fixed places as a graph (data/navigation.json). which ones are reachable depends on how far
# the account is in the story: cleared chapters sit in their list (Main Record 1, ...), the chapter being
# played is on the terminal home, later ones dont exist yet. progress is kept in data/progress.json,
# set by the user and corrected by what the terminal actually shows
PROGRESS_FILE = DATA / "progress.json"

@cache
def all_places() -> dict[str, dict]:
    return json.loads((DATA / "navigation.json").read_text())["places"]

def load_progress() -> dict:
    progress = {"current": None, "seen": {}}
    if PROGRESS_FILE.exists():
        try:
            progress.update(json.loads(PROGRESS_FILE.read_text()))
        except ValueError:
            pass
    return progress

def save_progress(progress: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(progress, indent=1))

def current_index() -> int | None:
    current = load_progress()["current"]
    if current is None or current not in all_places():
        return None
    return all_places()[current].get("story")

def set_current(name: str) -> str | None:
    """sets the chapter being played now, returns the place name or None if it isnt a story chapter"""
    place = find(name, only_available=False)
    if place is None or all_places()[place].get("story") is None:
        return None
    progress = load_progress()
    progress["current"] = place
    save_progress(progress)
    return place

def available() -> dict[str, dict]:
    """places this account can reach, with the parent they have right now"""
    idx = current_index()
    seen = load_progress()["seen"]
    result = {}
    for name, info in all_places().items():
        place = dict(info)
        story = info.get("story")
        requires = info.get("requires")
        # a chapter seen on the terminal home goes back to its list once its cleared
        stale = seen.get(name) == "Terminal" and story is not None and idx is not None and story < idx
        if name in seen and not stale:
            # the screen showed it there, that wins over what we think
            place["parent"] = seen[name]
            result[name] = place
            continue
        if idx is None:
            # progress not set, treat everything as reachable
            result[name] = place
            continue
        if story is not None:
            if story > idx:
                continue
            if story == idx:
                place["parent"] = "Terminal"
        elif requires is not None and requires > idx:
            continue
        result[name] = place

    # a chapter list with nothing in it isnt shown by the game (no Main Record 1 before Fuyuki is cleared)
    for name in list(result.keys()):
        if result[name]["kind"] != "list" or name in seen:
            continue
        holds_chapters = False
        has_child = False
        for child, other in all_places().items():
            if other.get("parent") == name and ("story" in other or "requires" in other):
                holds_chapters = True
                if child in result and result[child].get("parent") == name:
                    has_child = True
        if holds_chapters and not has_child:
            del result[name]
    return result

def is_available(place: str) -> bool:
    return place in available()

def learn_parent(place: str, parent: str) -> None:
    """the game showed place inside parent (or back from place led to parent)"""
    if place not in all_places() or place == parent:
        return
    progress = load_progress()
    reachable = available()
    if place in reachable and reachable[place].get("parent") == parent:
        return
    progress["seen"][place] = parent
    story = all_places()[place].get("story")
    idx = current_index()
    if parent == "Terminal" and story is not None and (idx is None or story > idx):
        # a chapter on the terminal home is the one being played
        progress["current"] = place
    save_progress(progress)

def learn_current(place: str) -> None:
    # you can only open the chapter you are on or older ones, so a newer one means the story moved on
    story = all_places().get(place, {}).get("story")
    idx = current_index()
    if story is None or idx is None or story <= idx:
        return
    progress = load_progress()
    progress["current"] = place
    save_progress(progress)

def learn_banners(location: str, entries: list[str]) -> list[str]:
    """banners of known places seen in the list at location. returns the places learned"""
    learned = []
    reachable = available()
    for name, info in all_places().items():
        if name == location:
            continue
        banner = info.get("banner", name)
        found = False
        for e in entries:
            if words(e) == words(banner) or contains(e, banner):
                found = True
                break
        if not found:
            continue
        if name in reachable and reachable[name].get("parent") == location:
            continue
        learn_parent(name, location)
        learned.append(name)
    return learned

def words(text: str) -> list[str]:
    """lower case words without accents. a dot before a number stays ("No.1", "1.5"), other dots are ocr noise"""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+(?:\.[0-9][a-z0-9]*)*", plain)

def same_word(a: str, b: str) -> bool:
    # short words and numbers have to be exact, "i" is not "iv" and "1" is not "1.5"
    if len(a) < 4 or len(b) < 4:
        return a == b
    return SequenceMatcher(None, a, b).ratio() >= 0.8

def contains(text: str, name: str) -> bool:
    """every word of name is a word in text"""
    have = words(text)
    for w in words(name):
        found = False
        for h in have:
            if same_word(w, h):
                found = True
                break
        if not found:
            return False
    return True

def names_of(place: str, info: dict) -> list[str]:
    return [place, info.get("banner", place)] + info["titles"]

def find(name: str, only_available: bool = False) -> str | None:
    """the place a name means. an exact name wins ("Main Record 1" is not "Main Record 1.5"),
    otherwise the most specific place whose name fits"""
    # a path like fgo_info("locations") writes it: "Main Record 1 > Fuyuki"
    name = name.split(">")[-1]
    if only_available:
        pool = available()
    else:
        pool = all_places()
    exact = []
    for p, info in pool.items():
        for n in names_of(p, info):
            if words(n) == words(name):
                exact.append(p)
                break
    if len(exact) == 1:
        return exact[0]
    best = None
    for p, info in pool.items():
        fits = False
        for n in names_of(p, info):
            if contains(name, n) or contains(n, name):
                fits = True
                break
        if fits and (best is None or len(words(p)) > len(words(best))):
            best = p
    return best

def recognise(title: str, kind: str, entries: list[str] = ()) -> str | None:
    """which place a terminal screen is, by its title. places with the same title (both main records
    say "Main Record") are told apart by which chapter banners are listed"""
    if kind == "home":
        return "Terminal"
    scored = []
    for p, info in all_places().items():
        for t in info["titles"]:
            if contains(title, t):
                scored.append((len(words(t)), p))
    if not scored:
        return None
    longest = max(n for n, _ in scored)
    candidates = []
    for n, p in scored:
        if n == longest and p not in candidates:
            candidates.append(p)
    if len(candidates) == 1:
        return candidates[0]
    listed = " ".join(entries)
    counts = {}
    for p in candidates:
        counts[p] = 0
        for info in all_places().values():
            if info.get("parent") == p and contains(listed, info["banner"]):
                counts[p] += 1
    best = max(counts.values())
    winners = [p for p, n in counts.items() if n == best]
    if best and len(winners) == 1:
        return winners[0]
    return None

def parent_of(place: str) -> str | None:
    reachable = available()
    if place in reachable:
        return reachable[place].get("parent")
    return all_places().get(place, {}).get("parent")

def shown_in(place: str) -> list[str]:
    parent = parent_of(place)
    if parent:
        return [parent]
    return []

def neighbours(place: str | None, skip: set = frozenset()) -> list[tuple[str, str]]:
    """(move, place it goes to). from an unknown place only home works"""
    reachable = available()
    moves = [("home", "Terminal")]
    if place in reachable:
        parent = reachable[place].get("parent")
        if parent:
            moves.append(("back", parent))
        for child, info in reachable.items():
            if info.get("parent") == place:
                moves.append(("open", child))
    result = []
    for move, there in moves:
        if (place, move, there) not in skip:
            result.append((move, there))
    return result

def route(start: str | None, goal: str, skip: set = frozenset()) -> list[tuple[str, str]]:
    """shortest list of (move, place) from start to goal, bfs over the reachable places"""
    came_from = {start: None}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        if here == goal:
            break
        for move, there in neighbours(here, skip):
            if there not in came_from:
                came_from[there] = (here, move)
                queue.append(there)
    if goal not in came_from:
        return []
    steps = []
    while goal != start:
        here, move = came_from[goal]
        steps.append((move, goal))
        goal = here
    steps.reverse()
    return steps

def trail(place: str) -> str:
    """"Terminal > Main Record 1 > Okeanos" """
    names = []
    while place and place not in names:
        names.append(place)
        place = parent_of(place)
    names.reverse()
    return " > ".join(names)

def summary() -> str:
    reachable = available()
    current = load_progress()["current"]
    lines = []
    if current:
        lines.append(f"Story progress: playing {current} (earlier chapters are cleared, later ones not unlocked)")
    else:
        lines.append("Story progress: not set, every place is assumed reachable (creator: fgo-mcp progress <chapter>)")
    lines.append("Places go(place) can reach (Terminal > list > chapter):")
    for p, info in reachable.items():
        line = f"- {trail(p)}"
        if info["kind"] == "map":
            line += " (map)"
        if info.get("holds"):
            line += f": {info['holds']}"
        lines.append(line)
    locked = len(all_places()) - len(reachable)
    if locked:
        lines.append(f"({locked} more places are not unlocked yet on this account)")
    return "\n".join(lines)