import json
import re
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from functools import cache

from fgo_mcp.path import DATA

# the terminal's fixed places as a graph (data/navigation.json). which ones are reachable and where they sit
# depends on how far the account is in the story: Part 1 chapters stay on the terminal home until Part 1 is
# cleared, then all of them move into Main Record 1 (a "phase" in the data); later chapters dont exist yet.
# progress is kept per device in data/progress-<serial>.json, set by the user and corrected by what the
# terminal actually shows
PROGRESS_FILE = DATA / "progress.json"

def use_device(serial: str) -> None:
    """one progress file per device, like the roster: one game account per device"""
    global PROGRESS_FILE
    PROGRESS_FILE = DATA / f"progress-{serial}.json"

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

def chapter_at(story: int) -> str | None:
    for name, info in all_places().items():
        if info.get("story") == story:
            return name
    return None

def set_current(name: str) -> str | None:
    """sets the chapter being played now, returns the place name or None if it isnt a story chapter"""
    place = find(name, only_available=False)
    if place is None or all_places()[place].get("story") is None:
        return None
    progress = load_progress()
    progress["current"] = place
    save_progress(progress)
    return place

def phases_passed(idx: int | None) -> list[int]:
    """the phase switches the story is past (8: Part 1 cleared). an observation from another phase is out of date"""
    passed = set()
    if idx is None:
        return []
    for info in all_places().values():
        phase = info.get("phase")
        if phase and idx > phase["after"]:
            passed.add(phase["after"])
    return sorted(passed)

def seen_parent(entry, idx: int | None) -> str | None:
    """the parent the screen showed, if it was seen in the phase the story is in now"""
    if isinstance(entry, str):
        # written before observations were stamped, may be from the wrong phase ("Interlude" learned from "Main Interlude")
        return None
    if entry.get("phases") != phases_passed(idx):
        return None
    return entry.get("parent")

def static_parent(info: dict, idx: int | None) -> str | None:
    phase = info.get("phase")
    if phase and idx is not None and idx > phase["after"]:
        return phase["parent"]
    return info.get("parent")

def available() -> dict[str, dict]:
    """places this account can reach, with the parent they have right now"""
    idx = current_index()
    seen = load_progress()["seen"]
    result = {}
    for name, info in all_places().items():
        place = dict(info)
        place["parent"] = static_parent(info, idx)
        observed = None
        if name in seen:
            observed = seen_parent(seen[name], idx)
        if observed:
            # the screen showed it there, that wins over what we think
            place["parent"] = observed
            result[name] = place
            continue
        if idx is None:
            # progress not set, treat everything as reachable
            result[name] = place
            continue
        story = info.get("story")
        requires = info.get("requires")
        if story is not None and story > idx:
            continue
        if requires is not None and requires > idx:
            continue
        result[name] = place
    return result

def is_available(place: str) -> bool:
    return place in available()

def learn_parent(place: str, parent: str) -> None:
    """the game showed place inside parent (or back from place led to parent)"""
    if place not in all_places() or place == parent:
        return
    reachable = available()
    if place in reachable and reachable[place].get("parent") == parent:
        return
    progress = load_progress()
    story = all_places()[place].get("story")
    idx = current_index()
    if story is not None and (idx is None or story > idx):
        # a chapter the game lists is reached
        progress["current"] = place
        idx = story
    progress["seen"][place] = {"parent": parent, "phases": phases_passed(idx)}
    save_progress(progress)

def learn_current(place: str) -> None:
    # you can only open the chapter you are on or older ones, so a newer one means the story moved on
    story = all_places().get(place, {}).get("story")
    if story is not None:
        reach_story(story)

def reach_story(story: int) -> None:
    """the story is at least at chapter story"""
    idx = current_index()
    name = chapter_at(story)
    if name is None or (idx is not None and story <= idx):
        return
    progress = load_progress()
    progress["current"] = name
    save_progress(progress)

def place_of_entry(text: str, pool) -> str | None:
    """the place an entry of a list is: same words as its banner, else the banner with the most words that
    all appear in it ("Main Interlude ..." is Main Interlude, not Interlude)"""
    best = None
    best_len = 0
    for name in pool:
        banner = all_places()[name].get("banner", name)
        if words(text) == words(banner):
            return name
        if contains(text, banner) and len(words(banner)) > best_len:
            best = name
            best_len = len(words(banner))
    return best

# the game puts a NEXT marker on the banner of the chapter being played, ocr reads it in front of the banner text
# ("NEXT First Singularity ... Orleans", "NEYT ...", "NEUT"). "NEWT" is the NEW badge, not this
NEXT_MARK = re.compile(r"^\W*NI?E[XYUV]T\b")
NEXT_ALONE = re.compile(r"^\W*NI?E[XYUV]T\W*$")

def is_next_marker(text: str) -> bool:
    """a line that is only the NEXT marker, its banner is the one below it"""
    return NEXT_ALONE.match(text) is not None

def next_marked(entries: list[str]) -> str | None:
    """the story chapter whose banner carries the NEXT marker"""
    chapters = [name for name, info in all_places().items() if info.get("story") is not None]
    for e in entries:
        if NEXT_MARK.match(e):
            name = place_of_entry(e, chapters)
            if name:
                return name
    return None

NOT_LEARNED = ("Story progress not learned yet, so which places this account has is not known. The engine learns it "
               "from the Terminal home list: call fgo_info(\"locations\") while on a Terminal screen and wait for the "
               "list of places before choosing where to go.")

def learn_banners(location: str, entries: list[str]) -> list[str]:
    """banners of known places seen in the list at location. returns the places learned"""
    learned = []
    pool = [name for name in all_places() if name != location]
    for e in entries:
        name = place_of_entry(e, pool)
        if name is None or name in learned:
            continue
        info = all_places()[name]
        if info.get("story") is not None:
            # a chapter the game lists is reached
            reach_story(info["story"])
        if info.get("requires_exact"):
            # this place only shows up from that point of the story on (Main Record 1: Part 1 is cleared)
            reach_story(info["requires"])
        reachable = available()
        if name in reachable and reachable[name].get("parent") == location:
            continue
        learn_parent(name, location)
        learned.append(name)
    return learned

def rank(place: str) -> float | None:
    """where a place sits in its list, smaller is higher up"""
    info = all_places().get(place, {})
    if info.get("order") is not None:
        return info["order"]
    if info.get("story") is not None:
        return 100 - info["story"]
    return None

def direction_to(target: str, location: str, entries: list[str]) -> str | None:
    """"up" or "down": where target is from the known banners on screen now, None if none of them is known
    (news and event banners) or target is between them (it should be on screen)"""
    goal = rank(target)
    if goal is None:
        return None
    reachable = available()
    siblings = [n for n, info in reachable.items() if info.get("parent") == location and n != target]
    ranks = []
    for e in entries:
        name = place_of_entry(e, siblings)
        if name is not None and rank(name) is not None:
            ranks.append(rank(name))
    if not ranks:
        return None
    if goal > max(ranks):
        return "down"
    if goal < min(ranks):
        return "up"
    return None

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
        return NOT_LEARNED
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