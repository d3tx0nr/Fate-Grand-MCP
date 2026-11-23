import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request

# a local model with ollama for the example runner. uses any ollama on localhost:11434, otherwise starts
# a docker container called fgo-ollama and pulls the model if its missing
HOST = "http://localhost:11434"
MODEL = "qwen3.5:4b"    # fits a 6GB laptop gpu
CONTEXT = 16384
HISTORY_CHARS = 40000

def api(path: str, body: dict | None = None, timeout: float = 900) -> dict:
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    request = urllib.request.Request(HOST + path, data, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        # /api/pull streams lines, the last one is the result
        lines = response.read().decode().splitlines()
        return json.loads(lines[-1])

def server_up() -> bool:
    try:
        api("/api/tags", timeout=3)
        return True
    except OSError:
        return False

def start_server() -> None:
    if server_up():
        return
    docker = shutil.which("docker") or shutil.which("docker.exe")
    if docker is None:
        raise SystemExit("Ollama is not running and Docker was not found: install Docker (Desktop) and start it")
    exists = subprocess.run([docker, "ps", "-aq", "-f", "name=^fgo-ollama$"], capture_output=True, text=True).stdout
    if exists.strip():
        subprocess.run([docker, "start", "fgo-ollama"], check=True)
    else:
        subprocess.run([docker, "run", "-d", "--gpus=all", "-v", "fgo-ollama-models:/root/.ollama",
                        "-p", "11434:11434", "--name", "fgo-ollama", "ollama/ollama"], check=True)
    for _ in range(60):
        if server_up():
            return
        time.sleep(1)
    raise SystemExit("the Ollama container started but does not answer on localhost:11434")

def to_ollama(messages: list[dict]) -> list[dict]:
    # history is kept in openai format (same as groq). ollama wants tool arguments as objects
    # and tool results named after the tool
    tool_names = {}
    converted = []
    for m in messages:
        if m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                tool_names[c["id"]] = c["function"]["name"]
                arguments = json.loads(c["function"]["arguments"] or "{}")
                calls.append({"function": {"name": c["function"]["name"], "arguments": arguments}})
            converted.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m["role"] == "tool":
            converted.append({"role": "tool", "content": m["content"], "tool_name": tool_names.get(m["tool_call_id"], "")})
        else:
            converted.append({"role": m["role"], "content": m.get("content") or ""})
    return converted

class Ollama:
    name = "ollama"
    model = MODEL
    history_chars = HISTORY_CHARS

    def __init__(self):
        start_server()
        installed = []
        for m in api("/api/tags")["models"]:
            installed.append(m["name"])
        if MODEL not in installed:
            print(f"pulling {MODEL} (once)...", flush=True)
            api("/api/pull", {"model": MODEL})

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """one reply as {"message", "finish", "error"}"""
        body = {"model": MODEL, "messages": to_ollama(messages), "tools": tools, "stream": False, "think": True,
                "options": {"num_ctx": CONTEXT, "temperature": 0.3}}
        try:
            reply = api("/api/chat", body)
        except urllib.error.HTTPError as e:
            # the model wrote a broken tool call
            return {"message": {"role": "assistant", "content": ""}, "finish": "error",
                    "error": e.read().decode(errors="replace")[:600]}
        m = reply["message"]
        message = {"role": "assistant", "content": m.get("content") or ""}
        if m.get("tool_calls"):
            calls = []
            for i in range(len(m["tool_calls"])):
                c = m["tool_calls"][i]
                arguments = json.dumps(c["function"].get("arguments") or {})
                calls.append({"id": f"call_{i}_{time.time_ns()}", "type": "function",
                              "function": {"name": c["function"]["name"], "arguments": arguments}})
            message["tool_calls"] = calls
        if m.get("thinking"):
            message["reasoning"] = m["thinking"]
        return {"message": message, "finish": reply.get("done_reason"), "error": None}