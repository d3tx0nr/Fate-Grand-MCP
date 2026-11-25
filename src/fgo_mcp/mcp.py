import json
import urllib.error
import urllib.request

from mcp.server.mcpserver import MCPServer

from fgo_mcp.engine.api import HOST, PORT

# the mcp adapter (stdio). every tool is just a call to the engine api, the engine keeps the game state
# so mcp clients can restart whenever and more than one can look at the same game
ENGINE = f"http://{HOST}:{PORT}"
INSTRUCTIONS = """Plays Fate/Grand Order (NA) on an Android device.
fgo_screen shows the current screen: what is visible there and the actions valid on it.
Act with fgo_act(screen_id, action, args), using that screen_id. Every action is checked first; a rejected one
says what was wrong and how to fix it, and nothing was done in the game. After an action you get the next screen.
On the Terminal, go(place) travels to a chapter or list in one call; only places unlocked by the story so far can
be reached. The support list is not a place on the Terminal: start(row) on a quest opens it (no AP is spent until
start on the party screen after it), and back returns. fgo_info gives details on request (locations, kits, master, status, global); fgo_help explains the
current screen. NEEDS CREATOR in a state means a human must help (e.g. not enough AP): tell your creator instead of retrying."""

mcp = MCPServer("fgo-mcp", instructions=INSTRUCTIONS)

def call(path: str, body: dict | None = None) -> str:
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    request = urllib.request.Request(ENGINE + path, data, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=200) as response:
            reply = json.loads(response.read())
    except (OSError, urllib.error.URLError) as e:
        # engine isnt running, or got restarted during the call
        return f"ENGINE NOT REACHABLE ({e}). Needs creator: start it with `fgo-mcp engine`."
    return reply.get("text") or reply.get("error") or ""

@mcp.tool()
def fgo_screen(detail: str = "brief") -> str:
    """The current game screen: its type, what is visible, and the actions valid on it (with examples).
    detail: "brief" (default) or "full" (adds the raw data)."""
    return call(f"/screen?detail={detail}")

@mcp.tool()
def fgo_act(screen_id: int | str, action: str, args: dict | str | None = None) -> str:
    """Do one action on the current screen. screen_id is from the latest screen state; action and args are as
    listed under "Actions" there, e.g. action="skill", args={"slot":1,"skill":2}. Returns the outcome and the
    next screen, or REJECTED with the reason and how to fix it (then nothing was done)."""
    if isinstance(args, str):
        # some models send the args as a json string
        if args.strip() == "":
            args = {}
        else:
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return f'REJECTED, nothing was done: args must be a JSON object like {{"slot":1,"skill":2}}, got {args!r}'
    return call("/act", {"screen_id": screen_id, "action": action, "args": args or {}})

@mcp.tool()
def fgo_info(topic: str, slot: int | None = None) -> str:
    """Details on request. topic: "locations" (the places go(place) can reach with the story progress so far),
    "kits" (NP and skill text of the party), "master" (Mystic Code skills), "status" (buffs and debuffs of servant
    slot 1-3, battle command screen only), "global" (quest, support, party, runs won)."""
    path = f"/info?topic={topic}"
    if slot:
        path += f"&slot={slot}"
    return call(path)

@mcp.tool()
def fgo_help() -> str:
    """What the current screen is, the actions possible on it, and what needs the creator's help."""
    return call("/help")

@mcp.resource("fgo://screen")
def screen_resource() -> str:
    """The current screen state (same as fgo_screen)."""
    return call("/screen")

@mcp.resource("fgo://global")
def global_resource() -> str:
    """What the engine keeps across screens (quest, support, party, runs won)."""
    return call("/info?topic=global")

def run() -> None:
    mcp.run("stdio")