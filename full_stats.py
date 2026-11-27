# every feature of fgo-mcp over every recorded frame, offline: the frames are read again with the current code.
# counts go to full_stats.json (or the path given), run it from the repo root
import sys, glob, json, collections, traceback, re
sys.path.insert(0, "scripts")
from frame_game import FrameGame
from fgo_mcp.battle import party
from fgo_mcp.battle.actions import Battle
from fgo_mcp.battle.details import master_title, MasterSkill, fill_from_atlas, dialog_lines, MASTER_DIALOG
from fgo_mcp.data import places
from fgo_mcp.data.store import Roster
from fgo_mcp.fga.locations import Locations
from fgo_mcp.fga.screen import classify
from fgo_mcp.fga.vision import Snapshot
from fgo_mcp.readers import hud, quests, support_list, party_screen, terminal, ocr
from fgo_mcp.screens.registry import FGA_TO_TYPE

# results go to full_stats.json in the repo root unless another path is given
OUT = sys.argv[1] if len(sys.argv) > 1 else "full_stats.json"
ROSTERS = {"tablet": Roster("HA29M5EM"), "phone": Roster("e03f3346")}
TABLET_RUNS = ("test1-tablet", "run-20261125-183406", "run-20261127-203334", "run-20261127-211504")
# data/archive keeps runs that the keep-newest-6 rule removed from data/logs and data/runs
runs = sorted(glob.glob("data/test*/run-*")) + sorted(glob.glob("data/archive/logs/run-*")) + sorted(glob.glob("data/logs/run-*"))
S = collections.Counter()
per_run = {}
notes = collections.defaultdict(list)

def note(key, text):
    if len(notes[key]) < 40:
        notes[key].append(text)

for run in runs:
    name = run.split("data/")[1]
    device = "tablet" if any(t in run for t in TABLET_RUNS) else "phone"
    roster = ROSTERS[device]
    known = {s.atlas_id for s in roster.all("mine")}
    R = collections.Counter()
    def add(key, n=1):
        S[key] += n; R[key] += n

    # 1. screen recognition against the label the engine filed the frame under
    for f in sorted(glob.glob(run + "/frames/*/*.jpg")):
        label = f.split("/")[-2]
        if label in ("needs_creator",):
            continue
        try:
            g = FrameGame(f); fine = classify(Snapshot(g.game_image()), Locations(g.ensure_area()))
            typ = FGA_TO_TYPE.get(fine, "unknown")
            ok = label in (fine, typ) or (label == "battle_busy" and typ in ("unknown", "battle_busy", "battle_command"))
            add("screen frames"); add("screen agree", ok)
            if not ok: note("screen", f"{name} {f.split('/')[-1]}: filed {label}, now {fine}")
        except Exception as e:
            note("crash", f"{f} classify {e!r}")

    # 2. terminal home: entries, NEXT banner, progress learned from blank
    places.PROGRESS_FILE = places.DATA / "progress-offline-stats.json"
    places.save_progress({"current": None, "seen": {}})
    for f in sorted(glob.glob(run + "/frames/menu/*home*.jpg")):
        try:
            g = FrameGame(f); texts = [e.text for e in terminal.read_entries(g)]
            add("home frames"); add("home frames with entries", bool(texts))
            places.learn_banners("Terminal", texts)
            has_next = any(places.NEXT_MARK.match(t) or places.is_next_marker(t) for t in texts)
            if has_next:
                add("home NEXT shown"); add("home NEXT resolved", bool(places.next_marked(texts)) or places.is_next_marker(texts[-1]))
        except Exception as e:
            note("crash", f"{f} home {e!r}")
    if glob.glob(run + "/frames/menu/*home*.jpg"):
        cur = places.load_progress()["current"]
        add("runs with home frames"); add("runs progress learned", bool(cur))
        R["progress"] = cur

    # 3. quest lists
    for f in sorted(glob.glob(run + "/frames/menu/*list*.jpg")):
        try:
            g = FrameGame(f); title = terminal.read_title(g, wait_s=0); rows = quests.read_quests(g)
            entries = [e.text for e in terminal.read_entries(g)]
            place = places.recognise(title, "list", entries)
            add("list frames"); add("list recognised", bool(place))
            if not place: note("list", f"{name} {f.split('/')[-1]}: title {title!r}")
            for r in rows:
                add("quest rows"); add("quest rows title+AP", bool(r.title) and (r.ap is not None or bool(r.locked)))
        except Exception as e:
            note("crash", f"{f} list {e!r}")

    # 4. maps
    for f in sorted(glob.glob(run + "/frames/menu/*map*.jpg")):
        try:
            g = FrameGame(f); labels = [e.text for e in terminal.read_map_places(g)]
            add("map frames"); add("map frames with labels", bool(labels))
            add("map NEXT found", any(t.endswith("[NEXT]") or t == "NEXT node" for t in labels))
        except Exception as e:
            note("crash", f"{f} map {e!r}")

    # 5. battle command: name strip, HUD, enemies
    for f in sorted(glob.glob(run + "/frames/battle_command/*.jpg")):
        try:
            g = FrameGame(f)
            present = Battle(g).servants_present(Snapshot(g.game_image()))
            for fs in party.scan(g, prefer=known):
                if not present[fs.slot - 1]: continue
                add("strip reads"); add("strip identified", bool(fs.atlas_id)); add("strip class read", bool(fs.cls)); add("strip level read", bool(fs.level))
            h = hud.read(g)
            for v in h.np: add("hud np"); add("hud np read", v is not None)
            for v in h.hp: add("hud hp"); add("hud hp read", v is not None)
            add("hud turn"); add("hud turn read", h.turn is not None)
            add("hud wave"); add("hud wave read", h.wave is not None)
            for e in h.enemies:
                if e.hp:
                    add("enemies"); add("enemy name read", bool(e.name)); add("enemy class read", bool(e.cls))
        except Exception as e:
            note("crash", f"{f} battle {e!r} {traceback.format_exc()[-200:]}")

    # 6. command cards
    for f in sorted(glob.glob(run + "/frames/battle_cards/*.jpg")):
        try:
            g = FrameGame(f); b = Battle(g); hand = b.read_cards(s=b.snap(), frames=1)
            add("card screens")
            for c in hand:
                add("cards"); add("card type read", c.type is not None or c.stunned); add("card owner read", c.owner is not None)
        except Exception as e:
            note("crash", f"{f} cards {e!r}")

    # 7. support list
    for f in sorted(glob.glob(run + "/frames/support/*.jpg")):
        try:
            g = FrameGame(f); L = Locations(g.ensure_area()); s = Snapshot(g.game_image())
            add("support screens"); add("support tab detected", bool(support_list.selected_tab(g, L)))
            for r in support_list.read(g, s, L):
                if not r.complete: continue
                add("support rows"); add("support named", bool(r.name)); add("support owner", bool(r.owner))
                add("support identified", bool(r.atlas_id)); add("support np level", r.np_level is not None)
                add("support skill levels", None not in r.skills)
                if not r.atlas_id: note("support", f"{name}: {r.name!r} ({r.kind})")
        except Exception as e:
            note("crash", f"{f} support {e!r}")

    # 8. party screen
    for f in sorted(glob.glob(run + "/frames/party/*.jpg")):
        try:
            g = FrameGame(f); title, members = party_screen.read_party(g, roster)
            add("party screens"); add("party screens 6 members", len(members) >= 5)
            for m in members[:3]:
                add("party front"); add("party front class+level", bool(m.cls and m.level))
        except Exception as e:
            note("crash", f"{f} party {e!r}")

    # 9. from the logs of the run: skill levels and master skill titles read live
    ev = run + "/events.jsonl"
    try:
        for l in open(ev):
            e = json.loads(l)
            if e["kind"] == "skill_read":
                for r in e["reads"]:
                    add("skill dialog reads"); add("skill level read", r.get("level") is not None)
            if e["kind"] == "battle_start":
                titles = [m["name"] for m in e["master_skills"] if m["name"]]
                got = [master_title([t]) for t in titles]
                sk = [MasterSkill(i + 1, n, level=l) for i, (n, l) in enumerate(got)]
                fill_from_atlas(sk)
                add("battles"); add("battles mystic code identified", any(m.note.startswith(("Mystic", "Souya")) or " Lv" in m.note for m in sk if m.note))
    except FileNotFoundError:
        pass
    per_run[name] = dict(R)
    print("done", name, flush=True)

# 10. master skill dialogs re-read from the frames of the record runs
for run in sorted(glob.glob("data/archive/runs/run-*")) + sorted(glob.glob("data/runs/run-*")):
    frames = []
    for p in sorted(glob.glob(run + "/boxes/*.json")):
        d = json.load(open(p)); items = d["items"] if isinstance(d, dict) and "items" in d else d
        if any(it.get("kind") == "crop" and it.get("region") == [300, 420, 1960, 300] for it in (items if isinstance(items, list) else [])):
            frames.append(p.replace("/boxes/", "/frames/").replace(".json", ".jpg"))
    for f in frames:
        g = FrameGame(f); n, lv = master_title(dialog_lines(ocr.boxes(g.game_image(MASTER_DIALOG, scale="native"))))
        S["master dialogs"] += 1; S["master dialog name"] += bool(n); S["master dialog level"] += lv is not None
places.PROGRESS_FILE.unlink(missing_ok=True)
json.dump({"totals": dict(S), "per_run": per_run, "notes": notes}, open(OUT, "w"), indent=1)
print("written", OUT)