import json
import sys
import time

from mcp import ClientSession, StdioServerParameters, stdio_client

from fgo_mcp.path import LOGS
from fgo_mcp.runner.groq import Groq, RequestTooLarge
from fgo_mcp.runner.ollama import Ollama

# example mcp client, a plain agent with tools like any host would be. no game knowledge in here.
# old turns get dropped to fit the model, each dropped tool call is kept as one line and the ones that
# move around or finish things (milestones) are never dropped
SYSTEM = ("You are an agent that plays the mobile game Fate/Grand Order through the tools you have. "
          "Complete the user's task. When it is complete, reply with a short summary and no tool call.")
MILESTONES = ("go", "open", "start", "next", "home", "back", "repeat", "withdraw")
ROUTINE_LINES = 30

def split_history(history: list[dict], max_chars: int) -> tuple[list[dict], list[dict]]:
    """drops the oldest turns (model reply + its tool results) until the rest fits"""
    turns = []
    for m in history:
        if m["role"] == "tool" and turns:
            turns[-1].append(m)
        else:
            turns.append([m])
    dropped = []
    while len(turns) > 1 and len(json.dumps(turns)) > max_chars:
        dropped += turns.pop(0)
    kept = []
    for t in turns:
        kept += t
    return kept, dropped

def remember(dropped: list[dict]) -> list[tuple[bool, str]]:
    calls = {}
    lines = []
    for m in dropped:
        for c in m.get("tool_calls") or []:
            arguments = c["function"].get("arguments") or "{}"
            try:
                action = json.loads(arguments).get("action")
            except (json.JSONDecodeError, AttributeError):
                action = None
            calls[c["id"]] = (action in MILESTONES, f"{c['function']['name']} {arguments}"[:110])
        if m["role"] == "tool":
            milestone, call = calls.get(m["tool_call_id"], (False, "tool"))
            first = ""
            for line in m["content"].splitlines():
                if line.strip():
                    first = line
                    break
            lines.append((milestone, f"{call} -> {first[:90]}"))
    return lines

def earlier_text(earlier: list[tuple[bool, str]]) -> str:
    routine = []
    for i in range(len(earlier)):
        if not earlier[i][0]:
            routine.append(i)
    routine = routine[-ROUTINE_LINES:]
    lines = []
    for i in range(len(earlier)):
        milestone, line = earlier[i]
        if milestone or i in routine:
            lines.append(line)
    return "\n".join(lines)

async def play(task: str, provider: str = "ollama", max_steps: int = 400) -> None:
    if provider == "ollama":
        llm = Ollama()
    else:
        llm = Groq()
    log_dir = LOGS / ("agent-" + time.strftime("%Y%m%d-%H%M%S"))
    log_dir.mkdir(parents=True)
    (log_dir / "task.txt").write_text(f"{task}\n(model: {llm.name} {llm.model})\n")
    log = (log_dir / "steps.jsonl").open("a")
    print(f"logging to {log_dir}", flush=True)

    server = StdioServerParameters(command=sys.executable, args=["-m", "fgo_mcp", "mcp"])
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        instructions = (await session.initialize()).instructions or ""
        tools = []
        for t in (await session.list_tools()).tools:
            tools.append({"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                           "parameters": t.input_schema}})
        system = SYSTEM + "\n\nTool server instructions:\n" + instructions
        history = []
        earlier = []
        max_chars = llm.history_chars

        for step in range(max_steps):
            history, dropped = split_history(history, max_chars)
            earlier += remember(dropped)
            messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
            if earlier:
                messages.append({"role": "user", "content": "Earlier in this session (oldest first):\n" + earlier_text(earlier)})
            for m in history:
                without_reasoning = dict(m)
                without_reasoning.pop("reasoning", None)
                messages.append(without_reasoning)
            started = time.time()
            try:
                reply = llm.chat(messages, tools)
            except RequestTooLarge:
                max_chars = max(max_chars//2, 3000)
                continue
            max_chars = min(max_chars + 2000, llm.history_chars)
            message = reply["message"]
            entry = {"step": step, "t": time.time(), "model_s": round(time.time() - started, 1),
                     "reasoning": message.get("reasoning", ""), "content": message.get("content") or "",
                     "error": reply["error"], "calls": []}
            print(f"\n[{step}] {entry['model_s']} s {entry['content'][:200]!r}", flush=True)

            if reply["error"]:
                # broken output, tell the model like any agent would
                history.append({"role": "assistant", "content": "(invalid tool call)"})
                history.append({"role": "user", "content": f"Your last tool call was invalid: {reply['error']}"})
            elif not message.get("tool_calls") and not entry["content"].strip():
                history.append(message)
                history.append({"role": "user", "content": "Your last reply was empty. Continue the task."})
            elif not message.get("tool_calls"):
                log.write(json.dumps(entry) + "\n")
                print("\ndone:", entry["content"], flush=True)
                return
            else:
                history.append(message)
                for call in message["tool_calls"]:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                        result = await session.call_tool(name, args, read_timeout_seconds=None)
                        text = "\n".join(getattr(part, "text", "") for part in result.content)
                    except json.JSONDecodeError:
                        args = None
                        text = "ERROR: the arguments are not valid JSON"
                    history.append({"role": "tool", "tool_call_id": call["id"], "content": text})
                    entry["calls"].append({"tool": name, "args": args, "result": text})
                    print(f"  -> {name}({json.dumps(args)})\n     " + text.replace("\n", "\n     ")[:900], flush=True)
            log.write(json.dumps(entry) + "\n")
            log.flush()
        print(f"\nstopped after {max_steps} steps", flush=True)