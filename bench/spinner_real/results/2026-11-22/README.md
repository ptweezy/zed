# Linux records for zed-industries/zed#67261

The runs behind the PR's CPU table, taken on 2026-11-22, and how they were taken.

## Setup

- Ryzen 7 9800X3D, RTX 4080 (driver 595.99.02), Linux 7.0.0-44, `performance` power profile.
- Headless GNOME Shell 50.1 with one virtual monitor at each refresh rate.
- Bench build: `cargo build --release -p zed` of PR commit 69f68b014d plus the bench hooks committed as 5f45e094fe. That commit also drops a helper the build no longer used.
- Harness: `measure_real.py` as committed in 55a74c12e8.

The PR's later commit changes only how a capped stepped animation hands off to the next animation in a chain. Neither label does that, so these runs were not repeated for it.

## Invocations

All times are EST. Every command ran from `bench/spinner_real` with `BENCH_ZED` set to the bench build, `BENCH_WORK` to a scratch directory, and `BENCH_WINDOW=6`. `as-run/` has the wrapper scripts.

| Time | Command | Records |
|---|---|---|
| 01:45–02:31 | `BENCH_MAX_OTHER=800 BENCH_VARIANTS=before,after,previous BENCH_PREVIOUS=<previous build> python3 measure_real.py 4 120 hang,confirm,subagent,subagents,stream` | 60, cross-check |
| 02:31–03:19 | `BENCH_MAX_OTHER=800 BENCH_VARIANTS=before,after python3 measure_real.py 4 60,120,144,240 hang,confirm,subagent,subagents,stream` | 160, focused |
| 03:19–03:29 | `BENCH_MAX_OTHER=800 BENCH_VARIANTS=before,after python3 measure_real.py 3 120 hang@bg,confirm@bg,subagent@bg,subagents@bg,stream@bg,hidden,done` | 42, unfocused |
| 03:29–03:45 | `python3 fill.py 4 180 fill-focused.log quiet-focused.log` | 57, top-ups |
| 03:47 | `python3 fill.py 4 180 fill-focused.log quiet-focused.filtered.log` | 2, replacements |

The previous build is the bench build of the PR's earlier revision (b495b243ad on this branch), run with `ZED_BENCH_ALIGNED=1`.

## What counts

A run counts if everything else on the machine used at most 180 ms/s of CPU during its window. That is `other` in each record. The table takes the median of each cell's counted runs: 4 per focused cell and 3 per unfocused cell.

- The two main passes ran with a looser gate of 800 ms/s, so the 180 ms/s rule was applied afterwards. 57 of the 160 focused records exceeded it and were dropped. `fill.py` then measured 57 more with 180 ms/s as the gate.
- Two runs that passed the CPU rule were replaced as well: main at 144 Hz while waiting, at 02:55:06 and 02:56:06. GPU utilisation was 11% during them against 7% for the rest of the cell, so another process was rendering.
- Of 261 records, 59 were dropped and 202 count. The highest `other` among the counted runs is 147 ms/s.
- The harness and `fill.py` used for these runs kept the last result after 30 and 40 repeats, even if it was still above the gate. No record was kept that way: the longest streak of repeats before a kept run was 2. The harness now exits instead.

## Results

CPU time of the Zed process in ms/s, main → this PR, from `python3 tables.py counted.jsonl`:

| State | 60 Hz | 120 Hz | 144 Hz | 240 Hz | Unfocused, 120 Hz |
|---|---|---|---|---|---|
| Waiting for the model | 168 → 48 | 272 → 48 | 319 → 48 | 417 → 49 | 92 → 43 |
| Awaiting confirmation | 189 → 87 | 312 → 86 | 337 → 88 | 470 → 88 | 98 → 83 |
| One subagent | 177 → 52 | 304 → 52 | 320 → 52 | 437 → 52 | 93 → 45 |
| Three subagents | 187 → 56 | 327 → 56 | 318 → 55 | 467 → 57 | 97 → 50 |
| Streaming a response | 281 → 266 | 370 → 333 | 379 → 324 | 543 → 335 | 147 → 155 |

Cross-check at 120 Hz, medians of all 4 runs per cell under the 800 ms/s gate:

| State | Main | This PR | Previous revision |
|---|---|---|---|
| Waiting for the model | 248 | 46 | 45 |
| Awaiting confirmation | 294 | 88 | 88 |
| One subagent | 265 | 50 | 52 |
| Three subagents | 301 | 55 | 52 |
| Streaming a response | 366 | 332 | 330 |

## Files

- `counted.jsonl`: the 202 counted runs. `pass` is `main`, `top-up`, or `replacement`.
- `dropped.jsonl`: the 59 dropped runs, with the reason in `dropped`.
- `cross-check-120hz.jsonl`: main, this PR, and the previous revision rotated together.
- `first-pass-ungated.jsonl`: an earlier pass taken with the harness at d3e05cd535, discarded. It has no `other` reading, and other work on the machine moved Zed's CPU time in both directions while it ran.
- `windows-latest.md` and `macos-latest.md`: output of `measure_runner.py` from Actions run 44992496885, built at 5f45e094fe.
- `tables.py`: prints the medians of a set of records.
- `as-run/`: the wrapper scripts and `fill.py` as they ran, with the home directory shortened to `$HOME`.

## Record fields

`zed`, `main`, `shell`, and `other` are CPU ms per second of the window: the Zed process, its main thread, the compositor, and everything else on the machine. `fps` is the frames Zed drew per second, `idle` is the share of all CPU time spent in each idle state, and `at` is the local time the window ended.
