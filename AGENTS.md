# Agent instructions

## Setup and execution

Read README.md for prerequisites and complete commands. Use Windows x64 or Linux
x64, Python 3.11+, a separately installed BizHawk 2.11.1, FFmpeg/FFprobe and a local Super
Mario Bros. PAL ROM supplied by the operator. Never download or commit game
assets or emulator binaries.

macOS uses the Linux x64 Docker runtime through start.py; do not claim native
Mac emulator support. Its .env must reference the Linux EmuHawkMono.sh launcher.
Create `.venv`, install `requirements.txt`, and use `.venv/Scripts/python.exe`
on Windows or `.venv/bin/python` on Linux. `start.py` automates this setup.
The local `.env` accepts TYPESAFE_API_KEY, JEV_ROM and JEV_BIZHAWK; it is excluded
from Git and Docker builds. Only the empty `.env.example` is published.
`config.local.json` still accepts only `rom` and `emulator` paths. CLI overrides
JSON, then environment, then .env. Never print keys or include them in source,
examples, logs, build arguments or published settings.

Run these checks without a ROM or API key:

```powershell
.\.venv\Scripts\python.exe jev.py test
.\.venv\Scripts\python.exe tools/check_release.py
```

With local assets configured, run `jev.py doctor --config config.local.json`,
then `jev.py smoke --config config.local.json --out runs/smoke-01`. Smoke uses
scripted inputs and makes no API calls. A real `play` run consumes API usage;
use explicit time, decision, restore and input-token limits as shown in README,
or --until-complete when the operator explicitly authorizes unbounded usage.
Output directories must be new. Do not start a competing controller against an
existing run. Write its `STOP` file and let it finish before resuming it.

Use `--watch` during play or `jev.py watch --run runs/example --port 8768` to
view a run locally. The browser displays confirmed emulator snapshots and
executed controls; T requests keyboard control of the same local emulator.
Only the campaign owns bridge inputs. Do not let HTTP handlers drive the bridge.
Manual moves must retain Human provenance, replay hashes and recording. Release
keys on focus loss and expire input heartbeats; never silently resume Jev. A bounded stop is
normally exit code 2. Inspect `summary.json` rather than treating every nonzero
exit as a crash. Verify both the final victory condition and replay result before
claiming a completed game; partial routes can also replay successfully.

## Source layout

- `jev.py`: CLI, local asset configuration, checks and localhost viewer.
- `start.ps1`: Windows argument forwarding; accepts PowerShell and GNU-style switches.
- `start.py`, `start.sh`: portable native/container launchers.
- `env_config.py`: literal .env loading; no shell evaluation or interpolation.
- `Dockerfile`, `container-entry.sh`: Linux virtual display and runtime; no emulator or game.
- `src/jev_games/run.py`: RAM observations, API transport and backend helpers.
- `campaign_model.py`: SMB actions, outcomes, guide context and terminal rules.
- `campaign.py`: simulations, Jev decisions, failure memory and checkpoints.
- `turbo.py`: direct RAM option selection, without a shadow emulator or predicted outcomes.
  Never label these options as simulated/safe; preserve actual-input replay hashes.
- `gap_jump.py`: RAM-derived run-up and edge-jump skills, simulated before Jev selects them.
- `campaign_media.py`, `campaign_replay.py`: video capture and deterministic replay.
- `live_view.py`, `campaign_watch.html`: telemetry and the browser dashboard.
- `manual_control.py`: validated browser intent and input heartbeat expiration.
- `image_ascii.py`, `pixel_encodings.py`: optional pixel-to-text converters.
- `bridge/`: Python/Lua integration and text configuration, not the emulator.
- `tests/`: synthetic tests requiring no proprietary assets or API calls.

Module names above are under `src/jev_games/` unless a full path is given.
For another game, implement and validate separate RAM mappings, legal controls,
physics assumptions, progress metrics and death/loading/victory predicates.
Do not reuse SMB addresses or infer success from forward movement alone.

## Changes and repository hygiene

Preserve others' uncommitted work. Keep changes scoped and run the relevant
checks. Live telemetry must not advance emulator frames or make extra API calls.
Keep execution, recorded controls and replay state checks consistent.
Maze repeats/missed counters are navigation information, not restore triggers.
Do not reintroduce decision-count or novelty-count resets. Physical stall recovery
uses executed game frames (default 600; 0 disables), and timeout death restores
the level entry. Exhausted predictions use an explicit risky choice fallback,
rather than immediately restoring without actually playing the choice.
Repeated actual deaths must backtrack before a committed airborne trajectory,
even when an area identifier changes mid-jump; do not cross the current level's entry.
Optional guide phases can match area, swimming and maze_pass, and request a
minimum_search_tier (0..2). Log such external assistance and keep local guides ignored.
CI tests Python on Windows, Linux and macOS, and the actual Linux bridge with a
generated original test program. This does not verify gameplay on Apple Silicon
hardware. Preserve OS/core hashes; never bypass replay checks across platforms.
Keep .dockerignore's allowlist excluding local .env and runs. The release checker
must reject non-empty .env.example settings, even when they are not recognizable keys.

The shared documentation is README.md and AGENTS.md. Do not add internal notes,
conversation transcripts, social-media drafts or personal experiment reports.
Do not commit local paths, credentials, ROMs, emulator files, savestates, logs,
screenshots or recordings. Keep generated data under ignored `runs/`; never
force-add ignored data. Run the release checker before publishing changes.
Walkthrough guides are optional runtime input: keep them level-scoped, identify
their external sources and log when they are enabled.
