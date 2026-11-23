import asyncio
import json
import queue

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from fgo_mcp.engine.core import Engine
from fgo_mcp.log import events, record

# local api of the engine. the mcp adapter uses it, and later a frontend can watch /events
#   GET  /screen?detail=brief|full
#   POST /act     {"screen_id", "action", "args"}
#   GET  /info?topic=...&slot=N
#   GET  /help
#   GET  /status
#   POST /tap     {"x", "y"}   only for the creator, not an mcp tool
#   GET  /events  server sent events: the events of this run so far, then new ones as they happen
HOST = "127.0.0.1"
PORT = 8765
KEEPALIVE_S = 15

def to_json(obj):
    # ocr coordinates are numpy numbers which json doesnt know
    if hasattr(obj, "item"):
        return obj.item()
    return str(obj)

def reply(data: dict) -> JSONResponse:
    return JSONResponse(json.loads(json.dumps(data, default=to_json)))

def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="fgo-mcp engine")

    # plain def endpoints run in a thread pool, the engine calls block while it waits for the screen
    @app.get("/screen")
    def screen(detail: str = "brief"):
        return reply({"text": engine.screen(detail)})

    @app.post("/act")
    async def act(request: Request):
        body = await request.json()
        text = await asyncio.to_thread(engine.act, body.get("screen_id"), str(body.get("action", "")), body.get("args"))
        return reply({"text": text})

    @app.get("/info")
    def info(topic: str = "", slot: int | None = None):
        return reply({"text": engine.info(topic, slot)})

    @app.get("/help")
    def help_text():
        return reply({"text": engine.help()})

    @app.get("/status")
    def status():
        state = engine.state
        screen_type = None
        screen_id = None
        if state:
            screen_type = state.type
            screen_id = state.id
        record_dir = None
        if record.recorder is not None:
            record_dir = str(record.recorder.dir)
        return reply({"log_dir": str(events.LOG_DIR), "record_dir": record_dir, "screen": screen_type, "screen_id": screen_id, "error": engine.last_error})

    @app.post("/tap")
    async def tap(request: Request):
        body = await request.json()
        x = int(body["x"])
        y = int(body["y"])

        def do_tap():
            with engine.lock:
                engine.game.tap(x, y)
                engine.dirty = True

        await asyncio.to_thread(do_tap)
        events.emit("tap", x=x, y=y, source="creator", reason="")
        return reply({"text": f"tapped ({x}, {y})"})

    @app.get("/events")
    async def event_stream(request: Request):
        # subscribe before reading the file, so nothing gets lost in between
        q = events.subscribe()
        past = events.past_events()

        async def generate():
            try:
                for line in past:
                    yield f"data: {line}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        line = await asyncio.to_thread(q.get, True, KEEPALIVE_S)
                        yield f"data: {line}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                events.unsubscribe(q)

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)

    return app

def serve(engine: Engine) -> None:
    app = create_app(engine)
    engine.start()
    print(f"engine running on http://{HOST}:{PORT}, logging to {events.LOG_DIR}", flush=True)
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    finally:
        engine.stop()
        events.emit("run_end", reason="engine stopped")