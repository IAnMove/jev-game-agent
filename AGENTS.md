# Agent instructions

## Setup and execution

Read README.md for prerequisites and complete commands. Use Windows, Python
3.11+, a separately installed BizHawk 2.11.1, FFmpeg/FFprobe and a local Super
Mario Bros. PAL ROM supplied by the operator. Never download or commit game
assets or emulator binaries.

Create `.venv`, install `requirements.txt`, and use `.venv/Scripts/python.exe`
for commands. `config.local.json` accepts only `rom` and `emulator` paths and is
ignored by Git. Set `TYPESAFE_API_KEY` in the runner's environment; never print
it or place it in source, command examples, logs or configuration files.

Run these checks without a ROM or API key:

```powershell
.\.venv\Scripts\python.exe jev.py test
.\.venv\Scripts\python.exe tools/check_release.py
```

With local assets configured, run `jev.py doctor --config config.local.json`,
then `jev.py smoke --config config.local.json --out runs/smoke-01`. Smoke uses
scripted inputs and makes no API calls. A real `play` run consumes API usage;
use explicit time, decision, restore and input-token limits as shown in README.
Output directories must be new. Do not start a competing controller against an
existing run. Write its `STOP` file and let it finish before resuming it.

Use `--watch` during play or `jev.py watch --run runs/example --port 8768` to
view a run locally. The browser displays confirmed emulator snapshots and
executed controls; it is not an interactive browser emulator. A bounded stop is
normally exit code 2. Inspect `summary.json` rather than treating every nonzero
exit as a crash. Verify both the final victory condition and replay result before
claiming a completed game; partial routes can also replay successfully.

## Source layout

- `jev.py`: CLI, local asset configuration, checks and localhost viewer.
- `start.ps1`: interactive Windows launcher with hidden API-key input.
- `src/jev_games/run.py`: RAM observations, API transport and backend helpers.
- `campaign_model.py`: SMB actions, outcomes, guide context and terminal rules.
- `campaign.py`: simulations, Jev decisions, failure memory and checkpoints.
- `campaign_media.py`, `campaign_replay.py`: video capture and deterministic replay.
- `live_view.py`, `campaign_watch.html`: telemetry and the browser dashboard.
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

The shared documentation is README.md and AGENTS.md. Do not add internal notes,
conversation transcripts, social-media drafts or personal experiment reports.
Do not commit local paths, credentials, ROMs, emulator files, savestates, logs,
screenshots or recordings. Keep generated data under ignored `runs/`; never
force-add ignored data. Run the release checker before publishing changes.
Walkthrough guides are optional runtime input: keep them level-scoped, identify
their external sources and log when they are enabled.
