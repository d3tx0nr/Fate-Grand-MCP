# Coverage

How well each part of the engine reads the game, measured on 13 recorded runs (1,152 frames) from the two devices
it was tested on: a 16:10 tablet with the story done and a 20:9 phone on a fresh account. Measured on 2026-11-27.

Most rows are offline: `full_stats.py` reads every saved frame again with the current code and checks that each
reader got a value. The frames are filed under the screen the engine recognised at the time, nobody labelled them
by hand. Rows marked (live) are counted from the run logs, because they only happen while playing. Each row adds
up the checks inside that feature (successes over checks).

| # | Feature | Coverage | Pooled from |
|---|---|---|---|
| 1 | Screen recognition | **99.2%** | 1143 / 1152 frames |
| 2 | Terminal home & story progress | **92.1%** | entries 20/20, NEXT banner 8/9, progress learned 7/9 runs |
| 3 | Quest lists | **90.2%** | place recognised 37/45, rows with title + AP 55/57 |
| 4 | Chapter maps | **100%** | labels 20/20, NEXT node 10/10 |
| 5 | Navigation `go()` (live) | **86.7%** | 13/15 steps |
| 6 | Battle HUD | **99.4%** | NP 726/726, HP 726/726, turn 242/242, wave 230/242 |
| 7 | Our servants in battle | **95.6%** | identity 658/726 (name strip only), class 712/726, level 712/726 |
| 8 | Skill levels (live dialog) | **97.0%** | 32/33 |
| 9 | Enemies | **86.2%** | name 467/484, class 367/484 |
| 10 | Command cards | **100%** | type 535/535, owner 280/280 |
| 11 | Master skills & mystic code | **97.5%** | dialog name 15/15, level 15/15, mystic code 9/10 battles |
| 12 | Support list | **98.5%** | class tab 155/155, name 282/282, owner 282/282, identity 265/282, NP level 276/282, skill levels 282/282 |
| 13 | Party screen | **100%** | 6 members 33/33, front class + level 99/99 |
| 14 | Battles won (live) | **100%** | 10/10 |

Known misses:
- servants with the same name and class (Cú Chulainn and Cú Chulainn (Prototype), both Lancer) cant be told apart
  from text, and story guest supports show up on every class tab
- the wave counter "2/3" is sometimes read as "213" (the engine keeps the last known wave then)
- enemy class labels are noisy OCR
- "Main Interlude" list titles arent read, "Rank Up Quest" lost its "Up" once

The recorded frames themselves (`data/`) are not in the repo. This table is the output of
`uv run python full_stats.py --skip run-20261127-203334` (that run was thrown away). To run it on your own runs:
play with `fgo-mcp engine --record`, then `uv run python full_stats.py`. `--pool` prints the table again from an
existing `full_stats.json` without reading the frames.