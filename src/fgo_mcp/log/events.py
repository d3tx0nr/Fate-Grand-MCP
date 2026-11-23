import itertools
import json
import queue
import threading
import time

import cv2

from fgo_mcp.path import LOGS

# every run logs to data/logs/run-<time>/events.jsonl, with a jpg per screen in frames/<label>/
# nothing is written before start_log(), so scripts using the readers dont make logs
# event kinds: run_start, run_end, device, screen, needs_creator, error, action, tap, battle_start, skill_read,
# hud, hand, battle_end, support_list, support_selected, party_screen, results, terminal, quest_selected, nav
LOG_DIR = None
lock = threading.Lock()
frame_numbers = itertools.count(1)
subscribers = []

def start_log():
    global LOG_DIR
    LOG_DIR = LOGS / ("run-" + time.strftime("%Y%m%d-%H%M%S"))
    (LOG_DIR / "frames").mkdir(parents=True)
    return LOG_DIR

def emit(kind: str, **fields) -> None:
    if LOG_DIR is None:
        return
    event = {"t": round(time.time(), 3), "kind": kind}
    event.update(fields)
    line = json.dumps(event, default=str)
    with lock:
        with (LOG_DIR / "events.jsonl").open("a") as f:
            f.write(line + "\n")
        for q in subscribers:
            q.put(line)

def subscribe() -> queue.Queue:
    """a queue that gets every new event line (json string), for the sse stream"""
    q = queue.Queue(maxsize=1000)
    with lock:
        subscribers.append(q)
    return q

def unsubscribe(q: queue.Queue) -> None:
    with lock:
        if q in subscribers:
            subscribers.remove(q)

def past_events() -> list[str]:
    if LOG_DIR is None:
        return []
    path = LOG_DIR / "events.jsonl"
    if not path.exists():
        return []
    with lock:
        return path.read_text().splitlines()

def save_frame(img, label: str, note: str = "") -> str | None:
    """saves the frame under frames/<label>/ and returns "<label>/<name>" """
    if LOG_DIR is None:
        return None
    folder = LOG_DIR / "frames" / label
    folder.mkdir(exist_ok=True)
    name = f"{next(frame_numbers):04d}"
    if note:
        name += "_" + note
    cv2.imwrite(str(folder / f"{name}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return f"{label}/{name}"