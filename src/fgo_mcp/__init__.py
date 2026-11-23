import asyncio
import sys

USAGE = """fgo-mcp: lets an LLM play Fate/Grand Order on an android device through MCP

  fgo-mcp engine                     run the engine (keep it running while playing)
  fgo-mcp mcp                        the MCP adapter on stdio (your MCP host starts this)
  fgo-mcp play "<task>"              the example agent with a local ollama model
  fgo-mcp play "<task>" groq         the example agent with groq (GROQ_API_KEY in .env)
  fgo-mcp calibrate                  measure the game area on an unusual screen (game on a terminal screen)
  fgo-mcp progress <chapter>         the story chapter this account is on (which places go() can reach)
"""

def main() -> None:
    command = "engine"
    if len(sys.argv) > 1:
        command = sys.argv[1]
    if command == "engine":
        from fgo_mcp.engine.api import serve
        from fgo_mcp.engine.core import Engine
        from fgo_mcp.log import events

        log_dir = events.start_log()
        engine = Engine()
        events.emit("run_start", log_dir=str(log_dir), screen_size=engine.game.stream.size)
        serve(engine)
    elif command == "mcp":
        from fgo_mcp.mcp import run

        run()
    elif command == "play" and len(sys.argv) > 2:
        from fgo_mcp.runner.agent import play

        provider = "ollama"
        if len(sys.argv) > 3:
            provider = sys.argv[3]
        asyncio.run(play(sys.argv[2], provider))
    elif command == "calibrate":
        from fgo_mcp.device.adb import screenshot
        from fgo_mcp.fga.calibrate import calibrate

        print(calibrate(screenshot()))
    elif command == "progress":
        from fgo_mcp.data import places

        if len(sys.argv) > 2:
            place = places.set_current(" ".join(sys.argv[2:]))
            if place is None:
                print("not a story chapter, try a name from the main story like Orleans or Anastasia")
                return
        print(places.summary())
    else:
        print(USAGE)