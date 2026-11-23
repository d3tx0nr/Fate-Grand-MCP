import difflib
import json
import re
import time
import urllib.request
from functools import cache
from pathlib import Path

from fgo_mcp.path import DATA

API = "https://api.atlasacademy.io"
REGION = "NA"
INFO_CHECK_SECONDS = 24*3600
SLIM_FILE = "slim_servant.v3.json"   # change the name when slim_servant() changes

def download(url: str, timeout: float = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fgo-mcp (personal use)"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()

def ensure_export(force: bool = False) -> Path:
    """download nice_servant.json if missing or if atlas has a new data hash for NA"""
    folder = DATA / "atlas"
    folder.mkdir(exist_ok=True)
    path = folder / "nice_servant.json"
    meta_path = folder / "meta.json"
    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    recently_checked = time.time() - meta.get("checked", 0) < INFO_CHECK_SECONDS
    if path.exists() and not force and recently_checked:
        return path
    try:
        remote_hash = json.loads(download(f"{API}/info", 30))[REGION]["hash"]
    except Exception:
        # offline, use what we already have
        if path.exists():
            return path
        raise
    if force or not path.exists() or meta.get("hash") != remote_hash:
        tmp = path.with_suffix(".part")
        tmp.write_bytes(download(f"{API}/export/{REGION}/nice_servant.json"))
        tmp.replace(path)
        load_servants.cache_clear()
    meta_path.write_text(json.dumps({"hash": remote_hash, "checked": time.time()}))
    return path

@cache
def mystic_codes() -> list[dict]:
    path = DATA / "atlas" / "nice_mystic_code.json"
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(download(f"{API}/export/{REGION}/nice_mystic_code.json"))
        tmp.replace(path)
    return json.loads(path.read_text())

def find_mystic_code(skill_names: list[str], cutoff: float = 0.8) -> dict | None:
    """the mystic code whose skills match the names read by ocr"""
    read_names = []
    for i in range(len(skill_names)):
        if skill_names[i]:
            read_names.append((i, normalise(skill_names[i])))
    if len(read_names) == 0:
        return None
    for mc in mystic_codes():
        names = []
        for sk in mc["skills"]:
            names.append(normalise(sk["name"]))
        if len(names) != len(skill_names):
            continue
        all_match = True
        for i, name in read_names:
            if difflib.SequenceMatcher(None, name, names[i]).ratio() < cutoff:
                all_match = False
                break
        if all_match:
            return mc
    return None

def slim_func(f: dict) -> dict:
    """atlas function with only the fields kits and buffs need"""
    slim = {}
    for key in ("funcType", "funcTargetType", "funcTargetTeam", "funcPopupText", "svals"):
        slim[key] = f.get(key)
    buffs = []
    for b in f.get("buffs", []):
        self_indv = []
        for x in b.get("ckSelfIndv") or []:
            self_indv.append(x["id"])
        op_indv = []
        for x in b.get("ckOpIndv") or []:
            op_indv.append(x["name"])
        buffs.append({"name": b.get("name"), "type": b.get("type"), "self": self_indv, "op": op_indv})
    slim["buffs"] = buffs
    return slim

def slim_servant(s: dict) -> dict:
    # the export is 45MB, keeping only what we use makes it load way faster
    battle_names = set()
    overwrite = s.get("ascensionAdd", {}).get("overWriteServantBattleName", {}) or {}
    for value in overwrite.values():
        if isinstance(value, dict):
            for v in value.values():
                if v:
                    battle_names.add(v)
        elif value:
            battle_names.add(value)

    skills = []
    for sk in s["skills"]:
        slim = {}
        for key in ("num", "priority", "name", "coolDown", "detail"):
            slim[key] = sk.get(key)
        slim["functions"] = [slim_func(f) for f in sk["functions"]]
        skills.append(slim)

    nps = []
    for np_ in s["noblePhantasms"]:
        slim = {}
        for key in ("id", "priority", "card", "detail", "name"):
            slim[key] = np_.get(key)
        slim["functions"] = [slim_func(f) for f in np_["functions"]]
        nps.append(slim)

    return {
        "id": s["id"],
        "collectionNo": s["collectionNo"],
        "name": s["name"],
        "battleName": s.get("battleName", ""),
        "originalBattleName": s.get("originalBattleName", ""),
        "battleNames": sorted(battle_names),
        "className": s["className"],
        "rarity": s["rarity"],
        "skills": skills,
        "noblePhantasms": nps,
    }

@cache
def load_servants(path: str) -> dict[int, dict]:
    full = Path(path)
    slim_path = full.with_name(SLIM_FILE)
    if not slim_path.exists() or slim_path.stat().st_mtime < full.stat().st_mtime:
        data = []
        for s in json.loads(full.read_text()):
            if s.get("collectionNo"):
                data.append(slim_servant(s))
        slim_path.write_text(json.dumps(data))
    by_id = {}
    for s in json.loads(slim_path.read_text()):
        by_id[s["id"]] = s
    return by_id

def servants() -> dict[int, dict]:
    return load_servants(str(ensure_export()))

def get(atlas_id: int) -> dict:
    return servants()[atlas_id]

def normalise(s: str) -> str:
    result = ""
    for ch in s.lower():
        if ch.isalnum():
            result += ch
    return result

# letters ocr mixes up in FGO's font, both sides get folded the same way before comparing
OCR_FOLD = [("rn", "m"), ("vv", "w"), ("l", "i"), ("1", "i"), ("|", "i"), ("!", "i"), ("0", "o"), ("5", "s"),
            ("8", "b"), ("é", "e"), ("á", "a")]

def fold(s: str) -> str:
    s = s.lower()
    for a, b in OCR_FOLD:
        s = s.replace(a, b)
    return normalise(s)

def all_names(s: dict) -> set[str]:
    names = set()
    for name in [s["name"], s["battleName"], s["originalBattleName"], *s["battleNames"]]:
        if name:
            names.add(name)
    return names

def similarity(read: str, name: str) -> float:
    a = fold(read)
    b = fold(name)
    if not a or not b:
        return 0.0
    whole = difflib.SequenceMatcher(None, a, b).ratio()
    # the read can be a cut off part of a long name ("Tristan" from "Tam Lin Tristan")
    if len(a) >= 5 and len(b) > len(a):
        best_part = 0.0
        for i in range(len(b) - len(a) + 1):
            best_part = max(best_part, difflib.SequenceMatcher(None, a, b[i:i+len(a)]).ratio())
        return max(whole, best_part*0.95)
    return whole

def match(read: str, class_name: str | None = None, prefer: set[int] | None = None,
          cutoff: float = 0.6, margin: float = 0.08) -> list[dict]:
    """servant(s) an ocr read name means. servants already on the account (prefer) get a small bonus.
    returns [] if nothing is close or two different servants are about as close"""
    read = re.sub(r"^[^A-Za-z]+|[^A-Za-z.)]+$", "", read or "")
    if len(fold(read)) < 3:
        return []
    exact = find(read, class_name)
    if len(exact) == 1:
        return exact
    if prefer is None:
        prefer = set()
    scored = []
    for s in servants().values():
        if class_name and s["className"] != class_name:
            continue
        best = 0.0
        for name in all_names(s):
            best = max(best, similarity(read, name))
        if best >= cutoff:
            if s["id"] in prefer:
                best += 0.1
            scored.append((best, s))
    if len(scored) == 0:
        return []
    scored.sort(key=lambda item: item[0], reverse=True)
    top_score = scored[0][0]
    top = []
    rivals = []
    for score, s in scored:
        if top_score - score < 1e-9:
            top.append(s)
        elif not (all_names(s) & all_names(top[0])):
            rivals.append(score)
    if len(rivals) > 0 and top_score - rivals[0] < margin:
        return []
    return top

def find(name: str, class_name: str | None = None) -> list[dict]:
    wanted = normalise(name)
    found = []
    for s in servants().values():
        if class_name is not None and s["className"] != class_name:
            continue
        for x in all_names(s):
            if normalise(x) == wanted:
                found.append(s)
                break
    return found

@cache
def buff_index() -> dict[str, dict]:
    """buff name (lowercase) -> atlas buff, used to read the status tab"""
    index = {}
    for s in servants().values():
        for sk in s["skills"] + s["noblePhantasms"]:
            for f in sk["functions"]:
                for b in f.get("buffs", []):
                    if b.get("name") and b["name"].lower() not in index:
                        index[b["name"].lower()] = b
    return index