# fgo-mcp

Lets an LLM play **Fate/Grand Order (NA)** on an Android phone or tablet, through MCP.

## Why

This started as a dumb question: why do all the "AI plays X" projects only ever play games on a PC? There's a
whole phone right there.

FGO was the obvious pick for me:

1. I play it.(Obviously, why would I spend time making anything that I dont really use or know)
2. [FGA (Fate/Grand Automata)](https://github.com/Fate-Grand-Automata/FGA) already exists and proves the game can
   be read and driven from screenshots.
3. It's turn based. Nothing happens until you act, so a model gets to actually think before every move instead of
   losing to a real-time game while it's still reading the screen.

The plan was simple: port the FGA bits I needed to Python, put an MCP server on top, done in a weekend.

It was not done in a weekend. An LLM can't just be handed taps and templates, it needs to know *what and where something is on the
screen*. So it slowly turned into rebuilding a worse version of the game's UI out of OCR, template matching, and a
lot of hand-measured screen regions that have to be scaled for every screen shape. I only own two devices, a
20:9 phone and a 16:10 tablet, so those are the only ones it's tested on. Anything else might need
`fgo-mcp calibrate`, or might just not work.

What's in this repo is more or less the finalised version. It came after a lot of failed attempts and throwaway
scripts, none of which made it in here, so the history looks a lot tidier than the process was.

Because the scope kept exploding, I cut it down to the parts that make "play the game" work:

- **Terminal navigation**: home list, quest lists, chapter maps, and `go(place)` to travel anywhere in one call.
  It learns how far your story is from the Terminal itself.
- **Story, free and daily quests**: picking a quest, a support, a party, and starting it.
- **Battles**: skills, master skills, targets, cards and noble phantasms. Servants, skills and mystic codes come
  from [Atlas Academy](https://atlasacademy.io) data; only who is on screen and their levels are read.
- **Results, story scenes and the common dialogs.**

No summoning, no enhancement or ascension, no event shops, no gacha. When the game wants something a person should
decide (refilling AP, a story movie, a dialog it doesn't know), the agent is told to stop and ask you: that's the
`NEEDS CREATOR` line in a screen state.

## Running it

You need Python 3.14 with [uv](https://docs.astral.sh/uv/), `adb` on your PATH with the device connected, and FGO
NA on the device with the game's **skill confirmation turned on** (skill levels are read from that dialog).

```sh
uv sync
uv run fgo-mcp engine       # keep this running while playing (--record saves every frame with what was read on it)
```

Then add the MCP server to whatever MCP host you use (Claude Desktop, Claude Code, ...):

```json
{
  "mcpServers": {
    "fgo": { "command": "uv", "args": ["run", "--directory", "/path/to/fgo-mcp", "fgo-mcp", "mcp"] }
  }
}
```

Or use the little example agent in the repo:

```sh
uv run fgo-mcp play "complete the next main story quest"         # local ollama model (qwen3.5:4b)
uv run fgo-mcp play "complete the next main story quest" groq    # groq, needs GROQ_API_KEY in .env
```

Other commands: `fgo-mcp progress [chapter]` shows or sets which story chapter your account is on, and
`fgo-mcp calibrate` measures the game area on a screen it doesn't know (run it with the game on the Terminal).

## MCP tools

| Tool | What it does |
|---|---|
| `fgo_screen(detail="brief")` | The current screen: what it is, what's on it, and the actions you can do there, with examples. `detail="full"` adds the raw data. Every state has a `screen_id`. |
| `fgo_act(screen_id, action, args)` | Does one action on that screen, e.g. `action="skill", args={"slot":1,"skill":2}`. Returns what happened and the next screen, or `REJECTED` with the reason (and nothing was done). |
| `fgo_info(topic, slot=None)` | Details on request: `locations` (where `go` can go), `kits` (the party's NPs and skills), `master` (mystic code skills), `status` (a servant's buffs, in battle), `global` (quest, support, party, runs won). |
| `fgo_help()` | What the current screen is, what you can do on it, and what needs a human. |

There are also two resources, `fgo://screen` and `fgo://global`.

The actions you'll see most:

- **Terminal**: `go(place)`, `open(name)`, `start(row)`, `scroll(direction)`, `back`, `home`
- **Support list**: `pick(row)` or `pick(entry)` (a number from the "seen so far" list), `scroll`, `top`,
  `refresh`, `class_tab(name)`, `back`
- **Party**: `select_party(n)`, `start`, `back`
- **Battle**: `skill`, `master_skill`, `target`, `attack`, `play(nps, cards)`, or `turn(...)` for a whole turn
- **Results, story, dialogs**: `next`, `skip`, and the dialog's own buttons

## Credits

- [FGA](https://github.com/Fate-Grand-Automata/FGA): the idea that this is possible at all, and the screen
  templates and regions this started from (MIT, see `src/fgo_mcp/assets/fga_en/LICENSE-FGA`)
- [Atlas Academy](https://atlasacademy.io): all the game data
- [scrcpy](https://github.com/Genymobile/scrcpy): the screen stream (its server is bundled in `vendor/`, Apache 2.0)
- [RapidOCR](https://github.com/RapidAI/RapidOCR): the OCR

Not affiliated with Lasengle, Aniplex or TYPE-MOON. Use it on your own account at your own risk.

## License

MIT, same as FGA. See `LICENSE`.

Anyone is free to use it, modify it and build on it. If you hook your own AI up to it and do something fun, I'd love
to see it: tag me on X ([@d3tx0nr](https://x.com/d3tx0nr))
