import json
import os
import time
import urllib.error
import urllib.request

from fgo_mcp.path import ROOT

# groq chat api (openai format) for the example runner, GROQ_API_KEY comes from .env in the repo.
# only waits out rate limits and gives broken output back to the model, never changes what it decided
URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-120b"
HISTORY_CHARS = 14000   # free tier is 8k tokens a minute so keep requests small

class RequestTooLarge(Exception):
    pass

def api_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    key = os.environ.get("GROQ_API_KEY")
    if key:
        return key
    raise SystemExit("GROQ_API_KEY not found: add a line GROQ_API_KEY=... to the .env file in the repo")

class Groq:
    name = "groq"
    model = MODEL
    history_chars = HISTORY_CHARS

    def __init__(self):
        self.key = api_key()

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        reply = self.request(messages, tools, "medium")
        if reply["finish"] == "length" and not reply["message"].get("tool_calls"):
            # it thought until it ran out of tokens, try again with less thinking
            reply = self.request(messages, tools, "low")
        return reply

    def request(self, messages, tools, effort: str) -> dict:
        body = {"model": MODEL, "messages": messages, "tools": tools, "temperature": 0.3,
                "max_completion_tokens": 2048, "reasoning_effort": effort, "parallel_tool_calls": False}
        # cloudflare in front of groq blocks python's default user agent
        headers = {"Content-Type": "application/json", "User-Agent": "fgo-mcp-runner",
                   "Authorization": f"Bearer {self.key}"}
        while True:
            request = urllib.request.Request(URL, json.dumps(body).encode(), headers)
            try:
                with urllib.request.urlopen(request, timeout=600) as response:
                    choice = json.loads(response.read())["choices"][0]
                break
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code == 429:
                    wait = float(e.headers.get("retry-after") or 10)
                    print(f"  (rate limited, waiting {wait:.0f} s)", flush=True)
                    time.sleep(wait)
                    continue
                if e.code == 413:
                    raise RequestTooLarge(text[:200]) from e
                if e.code == 400 and ("tool_use_failed" in text or "output_parse_failed" in text):
                    return {"message": {"role": "assistant", "content": ""}, "finish": "error",
                            "error": json.loads(text)["error"]["message"][:600]}
                raise RuntimeError(f"Groq error {e.code}: {text[:400]}") from e
        message = {}
        for key in ("role", "content", "tool_calls", "reasoning"):
            if key in choice["message"]:
                message[key] = choice["message"][key]
        return {"message": message, "finish": choice.get("finish_reason"), "error": None}