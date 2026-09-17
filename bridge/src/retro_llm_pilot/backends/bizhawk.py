from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from ..config import LuaBridgeConfig
from ..platforms import check_supported, load_platform_buttons, resolve_button
from .base import LaunchResult

# Accept legacy v2 bridges and SOL-2 v3 (named savestates).
SUPPORTED_PROTOCOL_VERSIONS = frozenset({2, 3})
NAMED_STATE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class LuaBridgeTimeout(TimeoutError):
    pass


class LuaBridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class BizHawkPaths:
    emulator: Path
    bridge_lua: Path
    bridge_dir: Path
    config_ini: Path
    run_dir: Path


class BizHawkBackend:
    def __init__(
        self,
        emulator_path: str | Path,
        project_dir: str | Path,
        run_dir: str | Path,
        bridge_config: LuaBridgeConfig | None = None,
        config_template: str | Path | None = None,
        record: dict[str, Any] | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.bridge_config = bridge_config or LuaBridgeConfig(
            script_path="scripts/bizhawk_bridge.lua",
            directory="lua_bridge",
            boot_frames=120,
        )
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.paths = self._build_paths(Path(emulator_path), config_template)
        self.process: subprocess.Popen[str] | None = None
        self.system_id: str | None = None
        self.platform: str | None = None
        self.last_output: str = ""
        self._stdout_thread: threading.Thread | None = None
        self._next_action_id = 1
        self._poll_interval = self.bridge_config.poll_interval_seconds
        self.record = record or {}
        self.button_map = load_platform_buttons()
        self.states_dir = self.paths.bridge_dir / "states"
        self.states_dir.mkdir(parents=True, exist_ok=True)
        self._clear_stale_files()
        self.write_ram_watch({})

    def launch(self, rom: Path) -> LaunchResult:
        support = check_supported(rom, self.paths.emulator.parent / "Firmware")
        if not support.ok:
            return LaunchResult(status=support.reason or "unsupported_extension", ok=False, error=support.reason)
        self.platform = support.platform
        if not self.paths.emulator.exists():
            return LaunchResult(status="load_failed", ok=False, error=f"EmuHawk not found: {self.paths.emulator}")
        if not rom.exists():
            return LaunchResult(status="load_failed", ok=False, error=f"ROM not found: {rom}")

        env = os.environ.copy()
        env["RETRO_LLM_BRIDGE_DIR"] = str(self.paths.bridge_dir)
        env["RETRO_LLM_BOOT_FRAMES"] = str(self.bridge_config.boot_frames)
        env["RETRO_LLM_POLL_SECONDS"] = str(self.bridge_config.poll_interval_seconds)

        command = [
            str(self.paths.emulator),
            str(rom.resolve()),
            f"--lua={self.paths.bridge_lua}",
        ]
        if self.paths.config_ini.exists():
            command.append(f"--config={self.paths.config_ini}")
        command.extend(self._record_args())
        command_path = self.run_dir / "launch_command.json"
        command_path.write_text(json.dumps({"command": command, "created_at": _now()}, indent=2), encoding="utf-8")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt" and os.environ.get("RETRO_LLM_ECO_MODE") == "1":
            creationflags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
        self.process = subprocess.Popen(
            command,
            cwd=str(self.paths.emulator.parent),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=creationflags,
        )
        self._start_output_drain()
        try:
            state = self.wait_ready(self.bridge_config.ready_timeout_seconds)
        except LuaBridgeTimeout as exc:
            self.close()
            if _looks_like_load_failed(self.last_output):
                return LaunchResult(status="load_failed", ok=False, error=self.last_output.strip()[-1000:] or str(exc))
            return LaunchResult(status="bridge_timeout", ok=False, error=str(exc))
        except RuntimeError as exc:
            self.close()
            return LaunchResult(status="emulator_crashed", ok=False, error=str(exc))

        self.system_id = str(state.get("system") or "")
        if self.platform and self.system_id:
            expected_ids = self.button_map.get(self.platform, {}).get("system_ids", [])
            if expected_ids and self.system_id not in expected_ids:
                self._append_log(f"warning: expected platform {self.platform}, BizHawk reported {self.system_id}")
        return LaunchResult(status="ok", ok=True, state=state)

    def wait_ready(self, timeout: float | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout or self.bridge_config.ready_timeout_seconds)
        while time.monotonic() < deadline:
            self._raise_if_crashed()
            state = self.read_state()
            proto = state.get("protocol_version") if state else None
            if (
                state
                and proto in SUPPORTED_PROTOCOL_VERSIONS
                and state.get("status") in {"ready", "idle"}
            ):
                self.system_id = str(state.get("system") or self.system_id or "")
                return state
            time.sleep(self._poll_interval)
        raw = _safe_read(self.state_path)
        raise LuaBridgeTimeout(f"Timed out waiting for BizHawk bridge at {self.state_path}; raw_state={raw!r}")

    def verify_frames_advance(self, frames: int = 60) -> LaunchResult:
        before = self.read_state() or {}
        before_frame = int(before.get("frame") or 0)
        try:
            self.advance(frames)
        except Exception as exc:
            return LaunchResult(status="bridge_timeout", ok=False, error=str(exc))
        after = self.read_state() or {}
        after_frame = int(after.get("frame") or 0)
        if after_frame <= before_frame:
            return LaunchResult(status="frozen", ok=False, error=f"frame did not advance: {before_frame}->{after_frame}")
        return LaunchResult(status="ok", ok=True, state=after)

    def press(
        self,
        buttons: list[str],
        hold_frames: int,
        advance_frames: int,
        analog: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        resolved = [self.resolve_button(button) for button in buttons]
        return self._send_action(
            {
                "type": "press",
                "buttons": resolved,
                "hold_frames": max(hold_frames, 1),
                "advance_frames": max(advance_frames, 1),
                "analog": analog or {},
            }
        )

    def advance(self, frames: int) -> dict[str, Any]:
        return self._send_action({"type": "advance", "advance_frames": max(frames, 1)})

    def screenshot(self) -> Path | None:
        default_path = self.paths.bridge_dir / "screen.png"
        try:
            previous_mtime_ns = default_path.stat().st_mtime_ns
        except OSError:
            previous_mtime_ns = -1
        state = self._send_action({"type": "screenshot", "advance_frames": 1})
        screenshot_file = state.get("screenshot_file") or "screen.png"
        path = self.paths.bridge_dir / screenshot_file
        # A previous valid screen.png may still exist when the action completes.
        # Wait for this action's newer PNG instead of validating stale bytes that
        # Lua is about to truncate and replace.
        min_mtime_ns = previous_mtime_ns if path == default_path else -1
        if not _wait_image_ready(path, timeout=2.0, min_mtime_ns=min_mtime_ns):
            return None
        # BizHawk can report a lagged frame before the GUI finishes presenting
        # the newly rendered image.  A short real-time settle keeps Qwen from
        # making a decision on the transient black framebuffer while preserving
        # the exact one-emulated-frame input cadence.
        time.sleep(0.05)
        _wait_framebuffer_visible(path)
        return path

    def read_state(self) -> dict[str, Any] | None:
        if not self.state_path.exists():
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def save_state(self, slot: int) -> dict[str, Any]:
        if slot in (8, 9):
            self._append_log(f"warning: autoplay should avoid legacy pipeline slots 8/9; got slot={slot}")
        return self._send_action({"type": "savestate", "slot": slot})

    def load_state(self, slot: int) -> dict[str, Any]:
        if slot in (8, 9):
            self._append_log(f"warning: autoplay should avoid legacy pipeline slots 8/9; got slot={slot}")
        return self._send_action({"type": "loadstate", "slot": slot})

    def save_state_named(self, name: str) -> dict[str, Any]:
        """Persist savestate under bridge_dir/states/<name>.State (protocol v3)."""
        safe = self._validate_state_name(name)
        self.states_dir.mkdir(parents=True, exist_ok=True)
        return self._send_action({"type": "savestate_named", "name": safe})

    def load_state_named(self, name: str, *, neutralize_framebuffer: bool = True) -> dict[str, Any]:
        """Load named savestate; optionally advance 1 neutral frame for stale framebuffer."""
        safe = self._validate_state_name(name)
        state = self._send_action({"type": "loadstate_named", "name": safe})
        if neutralize_framebuffer:
            state = self.advance(1)
            state = dict(state)
            state["framebuffer_offset_frames"] = 1
        return state

    @staticmethod
    def _validate_state_name(name: str) -> str:
        if not NAMED_STATE_RE.fullmatch(name or ""):
            raise ValueError(
                f"Invalid savestate name {name!r}; must match [A-Za-z0-9_-]+ (no paths)"
            )
        return name

    def close(self) -> None:
        try:
            if self.process and self.process.poll() is None:
                try:
                    self._send_action({"type": "quit"}, timeout=3.0)
                except Exception as exc:
                    self._append_log(f"quit action failed: {exc}")
                self._wait_process_exit(3.0)
            if self.process and self.process.poll() is None:
                self.process.terminate()
                self._wait_process_exit(5.0)
            if self.process and self.process.poll() is None:
                self.process.kill()
                self._wait_process_exit(2.0)
        finally:
            self._drain_output()

    def resolve_button(self, button: str) -> str:
        system_or_platform = self.system_id or self.platform or "NES"
        return resolve_button(system_or_platform, button, self.button_map)

    @property
    def state_path(self) -> Path:
        return self.paths.bridge_dir / self.bridge_config.state_file

    @property
    def pending_path(self) -> Path:
        return self.paths.bridge_dir / self.bridge_config.pending_file

    @property
    def done_path(self) -> Path:
        return self.paths.bridge_dir / self.bridge_config.done_file

    @property
    def screenshot_path(self) -> Path:
        return self.paths.bridge_dir / "screen.png"

    @property
    def ram_watch_path(self) -> Path:
        return self.paths.bridge_dir / self.bridge_config.ram_watch_file

    def write_ram_watch(self, watches: dict[str, int | str]) -> None:
        lines = ["# label=address[,domain]. Address can be decimal or 0x-prefixed hex."]
        for label, address in sorted(watches.items()):
            if isinstance(address, int):
                lines.append(f"{label}=0x{address:04X}")
            else:
                lines.append(f"{label}={address}")
        self.ram_watch_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _send_action(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        self._raise_if_crashed()
        action_id = self._next_action_id
        self._next_action_id += 1
        if self.done_path.exists():
            self.done_path.unlink()
        payload = {"id": action_id, "created_at": _now(), **payload}
        _write_json_atomic(self.pending_path, payload)
        done = self._wait_done(action_id, timeout or self.bridge_config.timeout_seconds)
        if done.get("status") == "error":
            raise LuaBridgeError(str(done.get("error") or f"BizHawk bridge action {action_id} failed"))
        state = self.read_state() or {}
        return state

    def _wait_done(self, action_id: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._raise_if_crashed()
            if self.done_path.exists():
                try:
                    done = json.loads(self.done_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    done = {}
                if done.get("id") == action_id:
                    return done
            time.sleep(self._poll_interval)
        raise LuaBridgeTimeout(f"Timed out waiting for BizHawk action {action_id}")

    def _build_paths(self, emulator_path: Path, config_template: str | Path | None) -> BizHawkPaths:
        emulator = emulator_path if emulator_path.is_absolute() else (self.project_dir / emulator_path)
        bridge_lua = self.project_dir / self.bridge_config.script_path
        bridge_dir = self.run_dir / self.bridge_config.directory
        bridge_dir.mkdir(parents=True, exist_ok=True)
        config_ini = self.run_dir / "bizhawk_config.ini"
        template = Path(config_template) if config_template else self.project_dir / "data" / "bizhawk_config_template.ini"
        generated_config = emulator.resolve().parent / "config.ini"
        if template.exists() and template.stat().st_size > 1024:
            shutil.copyfile(template, config_ini)
        elif generated_config.exists():
            shutil.copyfile(generated_config, config_ini)
        else:
            config_ini = self.run_dir / "bizhawk_config_unavailable.ini"
        return BizHawkPaths(
            emulator=emulator.resolve(),
            bridge_lua=bridge_lua.resolve(),
            bridge_dir=bridge_dir.resolve(),
            config_ini=config_ini.resolve(),
            run_dir=self.run_dir,
        )

    def _record_args(self) -> list[str]:
        if not self.record.get("enabled"):
            return []
        writer = self.record.get("writer") or self.record.get("container") or "avi"
        ext = str(self.record.get("container") or writer).lstrip(".")
        output_dir = Path(self.record.get("output_dir") or self.run_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        dump_name = output_dir / f"session.{ext}"
        args = [f"--dump-type={writer}", f"--dump-name={dump_name}"]
        if self.record.get("audiosync", True):
            args.extend(["--audiosync", "true"])
        return args

    def _clear_stale_files(self) -> None:
        for path in (self.pending_path, self.done_path, self.state_path, self.screenshot_path):
            try:
                if path.exists():
                    path.unlink()
            except OSError as exc:
                self._append_log(f"could not remove stale bridge file {path}: {exc}")

    def _raise_if_crashed(self) -> None:
        if self.process and self.process.poll() is not None:
            self._drain_output()
            raise RuntimeError(f"EmuHawk exited with code {self.process.returncode}")

    def _wait_process_exit(self, timeout: float) -> None:
        if not self.process:
            return
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return

    def _drain_output(self) -> None:
        if self._stdout_thread and self._stdout_thread.is_alive():
            self._stdout_thread.join(timeout=1.0)

    def _start_output_drain(self) -> None:
        if not self.process or not self.process.stdout:
            return

        def drain() -> None:
            log_path = self.run_dir / "emuhawk_stdout.log"
            try:
                with log_path.open("a", encoding="utf-8", errors="replace") as log_file:
                    for line in self.process.stdout or []:
                        self.last_output += line
                        log_file.write(line)
                        log_file.flush()
            except OSError:
                return

        self._stdout_thread = threading.Thread(target=drain, name="emuhawk-stdout-drain", daemon=True)
        self._stdout_thread.start()

    def _append_log(self, message: str) -> None:
        with (self.run_dir / "run.log").open("a", encoding="utf-8") as file:
            file.write(f"{_now()} {message}\n")

    def __enter__(self) -> BizHawkBackend:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def _safe_read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _wait_image_ready(path: Path, timeout: float, *, min_mtime_ns: int = -1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            if stat.st_size > 32 and stat.st_mtime_ns > min_mtime_ns:
                with Image.open(path) as image:
                    image.verify()
                return True
        except (OSError, UnidentifiedImageError):
            pass
        time.sleep(0.03)
    return False


def _wait_framebuffer_visible(path: Path, timeout: float = 0.5) -> None:
    """Give lagged BizHawk frames a short chance to present their pixels.

    The bridge can finish an action while the GUI still exposes a transient
    all-black PNG.  Polling the same file does not advance emulation; it only
    lets the renderer catch up.  If the screen is genuinely black after the
    deadline, the caller keeps that valid black frame.
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    while time.monotonic() < deadline:
        try:
            with Image.open(path) as image:
                image.load()
                extrema = image.convert("L").getextrema()
            if extrema[1] > 2:
                return
        except (OSError, UnidentifiedImageError):
            pass
        time.sleep(0.03)


def _looks_like_load_failed(output: str) -> bool:
    lowered = output.lower()
    markers = (
        "could not locate game",
        "not in game database",
        "failed to load",
        "unrecognized rom",
        "exception",
    )
    return any(marker in lowered for marker in markers)
