import json
import threading

import cv2

from fgo_mcp.path import DATA

# "record" mode, for debugging: keeps every frame of a run with the boxes the engine looked at on it (template checks,
# ocr crops and the text read, taps) and the events that belong to that frame, in data/runs/<run>/.
# the readers call the note_* functions below, they do nothing unless start() was called
RUNS = DATA / "runs"
TEMPLATE_HIT = (255, 140, 0)
TEMPLATE_MISS = (150, 150, 150)
OCR = (0, 220, 0)
TAP = (0, 0, 255)
CHECK = (0, 230, 255)

recorder = None

class Recorder:
    def __init__(self, run_dir):
        self.dir = run_dir
        for sub in ("frames", "boxes"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        self.items = []
        self.pending_events = []
        self.count = 0
        self.last_frame = None
        self.screen = "unknown"
        self.stage = "read"     # "classify" while the engine works out which screen it is
        self.flushing = False
        self.lock = threading.Lock()

    def add(self, item: dict) -> None:
        if self.flushing:
            return
        item["stage"] = self.stage
        with self.lock:
            self.items.append(item)

    def classification_event(self, items: list) -> dict | None:
        # which checks it took to find the screen type, kept in events.jsonl instead of on the picture
        misses = {}
        matched = []
        checks = 0
        for it in items:
            if it["stage"] != "classify" or it["kind"] != "template":
                continue
            checks += 1
            if it["passed"]:
                matched.append(it["name"])
            else:
                misses[it["name"]] = misses.get(it["name"], 0) + 1
        if checks == 0:
            return None
        return {"kind": "classification", "screen": self.screen, "checks": checks, "matched": matched, "misses": misses}

    def write_events(self, frame_name) -> None:
        with (self.dir / "events.jsonl").open("a") as f:
            for event in self.pending_events:
                event["record_frame"] = frame_name
                f.write(json.dumps(event, default=str) + "\n")
        self.pending_events = []

    def begin(self) -> None:
        # throw away boxes of reads that didnt lead to a saved frame (the engine checks the screen twice a second)
        with self.lock:
            self.items = []
            if self.pending_events:
                self.write_events(self.last_frame)

    def flush(self, label: str, game) -> str | None:
        with self.lock:
            items = self.items
            self.items = []
        if not items and not self.pending_events:
            return None
        self.flushing = True
        try:
            img = game.game_image(scale="native")
            rate = game.ensure_area().rate
        finally:
            self.flushing = False
        self.count += 1
        name = f"{self.count:04d}_{label}"
        cv2.imwrite(str(self.dir / "frames" / f"{name}.jpg"), draw(img.copy(), rate, items), [cv2.IMWRITE_JPEG_QUALITY, 90])
        (self.dir / "boxes" / f"{name}.json").write_text(json.dumps({"screen": self.screen, "items": items}, default=to_json))
        with self.lock:
            self.last_frame = name
            summary = self.classification_event(items)
            if summary is not None:
                self.pending_events.append(summary)
            self.write_events(name)
        return name

def to_json(obj):
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)

def box(r) -> list:
    return [int(r.x), int(r.y), int(r.w), int(r.h)]

def put_label(img, text: str, x: int, y: int, colour) -> None:
    cv2.putText(img, text, (x, max(y, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, (x, max(y, 12)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, colour, 1, cv2.LINE_AA)

def same_box_key(it: dict) -> tuple:
    # boxes that would be drawn the same way, the score can differ a little between repeated checks
    kind = it["kind"]
    if kind == "template":
        return (kind, it["name"], tuple(it["region"]), it["passed"], tuple(it["match"] or ()))
    if kind == "crop":
        return (kind, tuple(it["region"]), str(it.get("text")))
    if kind == "text":
        return (kind, it["x"], it["y"], it["text"])
    if kind == "check":
        return (kind, it["name"], tuple(it["region"]), str(it["result"]))
    if kind == "tap":
        return (kind, it["x"], it["y"])
    return (kind, it.get("x1"), it.get("y1"), it.get("x2"), it.get("y2"))

def group_boxes(items: list) -> list:
    """[item, count] for every different box, in the order they were first seen"""
    groups = {}
    for it in items:
        key = same_box_key(it)
        if key not in groups:
            groups[key] = [it, 0]
        groups[key][1] += 1
        if it["kind"] == "template" and it["score"] > groups[key][0]["score"]:
            groups[key][0] = it
    return list(groups.values())

def times(count: int) -> str:
    if count > 1:
        return f" x{count}"
    return ""

def draw(img, k: float, items: list):
    def scaled(b):
        return round(b[0]*k), round(b[1]*k), round((b[0] + b[2])*k), round((b[1] + b[3])*k)

    grouped = group_boxes(items)
    # misses first so the things that matched are drawn on top
    for it, count in grouped:
        if it["kind"] == "template" and not it["passed"]:
            x0, y0, x1, y1 = scaled(it["region"])
            cv2.rectangle(img, (x0, y0), (x1, y1), TEMPLATE_MISS, 1)
            if count > 1:
                put_label(img, f"x{count}", x1 - 22, y1 - 3, TEMPLATE_MISS)
    for it, count in grouped:
        kind = it["kind"]
        if kind == "template" and it["passed"]:
            x0, y0, x1, y1 = scaled(it["region"])
            cv2.rectangle(img, (x0, y0), (x1, y1), TEMPLATE_HIT, 1)
            if it["match"]:
                mx0, my0, mx1, my1 = scaled(it["match"])
                cv2.rectangle(img, (mx0, my0), (mx1, my1), TEMPLATE_HIT, 2)
            put_label(img, f"{it['name']} {it['score']:.2f}{times(count)}", x0 + 2, y0 + 12, TEMPLATE_HIT)
        elif kind == "crop":
            x0, y0, x1, y1 = scaled(it["region"])
            cv2.rectangle(img, (x0, y0), (x1, y1), OCR, 1)
            if it.get("text"):
                put_label(img, str(it["text"])[:45] + times(count), x0 + 2, y1 - 3, OCR)
        elif kind == "text":
            x, y = round(it["x"]*k), round(it["y"]*k)
            cv2.circle(img, (x, y), 2, OCR, -1)
            put_label(img, str(it["text"])[:40] + times(count), x + 3, y + 10, OCR)
        elif kind == "check":
            x0, y0, x1, y1 = scaled(it["region"])
            cv2.rectangle(img, (x0, y0), (x1, y1), CHECK, 1)
            put_label(img, f"{it['name']} {it['result']}{times(count)}", x0 + 2, y0 + 12, CHECK)
        elif kind == "tap":
            x, y = round(it["x"]*k), round(it["y"]*k)
            cv2.circle(img, (x, y), 9, TAP, 2)
            put_label(img, "tap", x + 11, y + 4, TAP)
        elif kind == "swipe":
            p1 = (round(it["x1"]*k), round(it["y1"]*k))
            p2 = (round(it["x2"]*k), round(it["y2"]*k))
            cv2.arrowedLine(img, p1, p2, TAP, 2)
    return img

def start(name: str):
    global recorder
    recorder = Recorder(RUNS / name)
    return recorder.dir

def active() -> bool:
    return recorder is not None

def note_template(name: str, r, match, score: float, threshold: float) -> None:
    if recorder is None:
        return
    match_box = None
    if match is not None:
        match_box = box(match)
    recorder.add({"kind": "template", "name": name, "region": box(r), "match": match_box, "score": round(float(score), 3),
                  "threshold": threshold, "passed": score >= threshold})

def note_check(name: str, r, result) -> None:
    # simple checks like "is this area black"
    if recorder is None:
        return
    recorder.add({"kind": "check", "name": name, "region": box(r), "result": result})

def note_crop(r) -> None:
    if recorder is None:
        return
    recorder.add({"kind": "crop", "region": box(r), "text": None})

def note_ocr(text) -> None:
    # the text read belongs to the last crop that has no text yet
    if recorder is None or recorder.flushing:
        return
    with recorder.lock:
        for it in reversed(recorder.items):
            if it["kind"] == "crop" and it["text"] is None:
                it["text"] = text
                return

def note_text(x: float, y: float, text: str) -> None:
    if recorder is None:
        return
    recorder.add({"kind": "text", "x": round(float(x)), "y": round(float(y)), "text": text})

def note_tap(game, x: int, y: int) -> None:
    # a tap changes the screen, so everything read so far belongs to the frame before it
    if recorder is None:
        return
    recorder.add({"kind": "tap", "x": x, "y": y})
    recorder.flush(f"{recorder.screen}_tap", game)

def note_swipe(game, x1: int, y1: int, x2: int, y2: int) -> None:
    if recorder is None:
        return
    recorder.add({"kind": "swipe", "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    recorder.flush(f"{recorder.screen}_swipe", game)

def event(data: dict) -> None:
    if recorder is None:
        return
    with recorder.lock:
        recorder.pending_events.append(dict(data))

def begin() -> None:
    if recorder is not None:
        recorder.begin()

def flush(label: str, game) -> str | None:
    if recorder is None:
        return None
    return recorder.flush(label, game)

def set_stage(stage: str) -> None:
    if recorder is not None:
        recorder.stage = stage

def set_screen(screen_type: str) -> None:
    if recorder is not None:
        recorder.screen = screen_type